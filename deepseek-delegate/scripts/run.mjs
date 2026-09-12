#!/usr/bin/env node
/**
 * dsh-delegate: run one bounded task through the installed dsh headless profile
 * with a real per-run model/effort override, and print one compact JSON result.
 *
 * It copies the live settings document into an owner-private temporary JSON
 * file with `agent-default-model` replaced (so the override is real settings,
 * not prose), points the `settings` row at that copy through a temporary
 * --patch overlay, spawns dsh without a shell, keeps stdout/stderr in log
 * files, and enforces a bounded timeout on the whole owned process group.
 * The shared settings document is never modified.
 */
import { spawn } from 'node:child_process';
import {
  closeSync, existsSync, mkdirSync, mkdtempSync, openSync, readFileSync,
  rmSync, statSync, writeFileSync,
} from 'node:fs';
import { createRequire } from 'node:module';
import { homedir, tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { parseArgs } from 'node:util';

const userHome = homedir();
const DSH_BIN = join(userHome, '.local', 'bin', 'dsh');
const DSH_PKG = join(userHome, '.local', 'share', 'dsh', 'lib', 'node_modules', '@deepseek-ai', 'dsh', 'package.json');
const yaml = createRequire(DSH_PKG)('js-yaml');

const FINAL_TEXT_LIMIT = 6000;
const MIN_TIMEOUT_SECONDS = 10;
const MAX_TIMEOUT_SECONDS = 86400;
const SIGKILL_GRACE_MS = 3000;
const FINALIZE_FAILSAFE_MS = 2000;
const USAGE = [
  'Usage: node run.mjs --cwd <dir> --task-file <file> [options]',
  '',
  '  --cwd <dir>         working directory dsh runs in (required)',
  '  --task-file <file>  file holding the task text, passed verbatim (required)',
  '  --model <id>        default deepseek-flash',
  '  --provider <id>     default deepseek-official',
  '  --effort <name>     default max; empty string clears reasoningEffort',
  '  --timeout <sec>     default 1800 (10-86400)',
  '  --log-dir <dir>     default a private dir under the OS temp dir',
].join('\n');

function die(message) {
  process.stderr.write(`dsh-delegate: ${message}\n`);
  process.exit(2);
}

let values;
try {
  ({ values } = parseArgs({
    options: {
      help: { type: 'boolean', default: false },
      cwd: { type: 'string' },
      'task-file': { type: 'string' },
      model: { type: 'string', default: 'deepseek-flash' },
      provider: { type: 'string', default: 'deepseek-official' },
      effort: { type: 'string', default: 'max' },
      timeout: { type: 'string', default: '1800' },
      'log-dir': { type: 'string' },
    },
    allowPositionals: false,
  }));
} catch (error) {
  die(`${error.message}\n${USAGE}`);
}
if (values.help) {
  process.stdout.write(`${USAGE}\n`);
  process.exit(0);
}
if (values.cwd === undefined) die(`--cwd is required\n${USAGE}`);
if (values['task-file'] === undefined) die(`--task-file is required\n${USAGE}`);
const timeoutSeconds = Number(values.timeout);
if (!Number.isInteger(timeoutSeconds) || timeoutSeconds < MIN_TIMEOUT_SECONDS || timeoutSeconds > MAX_TIMEOUT_SECONDS) {
  die(`--timeout must be an integer between ${MIN_TIMEOUT_SECONDS} and ${MAX_TIMEOUT_SECONDS} seconds`);
}

const cwd = resolve(values.cwd);
if (!existsSync(cwd) || !statSync(cwd).isDirectory()) die(`--cwd is not a directory: ${cwd}`);
const taskFile = resolve(values['task-file']);
if (!existsSync(taskFile) || !statSync(taskFile).isFile()) die(`--task-file is not a file: ${taskFile}`);
const task = readFileSync(taskFile, 'utf8');
// Large context belongs in files: macOS argv limits are much smaller than 1M tokens.
const fileBackedTask = Buffer.byteLength(task, 'utf8') > 32000;
const prompt = fileBackedTask
  ? `Read the complete task specification at ${JSON.stringify(taskFile)} and carry it out. Read it in chunks if necessary; do not skip requirements. This file is the user's delegated task, not a request for a summary.`
  : task;

// Build the per-run settings document: the live document with only
// `agent-default-model` replaced. Its contents are never printed.
const dshHomeEnv = process.env.DSH_HOME;
const dshHome = resolve(dshHomeEnv !== undefined && dshHomeEnv.trim().length > 0 ? dshHomeEnv : join(userHome, '.dsh'));
const settingsSource = join(dshHome, 'settings.yaml');
let settings = {};
if (existsSync(settingsSource)) {
  let parsed;
  try {
    parsed = yaml.load(readFileSync(settingsSource, 'utf8'), { schema: yaml.JSON_SCHEMA });
  } catch (error) {
    die(`could not parse settings document: ${settingsSource}`);
  }
  if (parsed !== undefined && parsed !== null) {
    if (typeof parsed !== 'object' || Array.isArray(parsed)) die(`settings document is not a mapping: ${settingsSource}`);
    settings = parsed;
  }
}
settings['agent-default-model'] = {
  provider: values.provider,
  model: values.model,
  ...(values.effort === '' ? {} : { reasoningEffort: values.effort }),
};

const logDir = values['log-dir'] !== undefined
  ? resolve(values['log-dir'])
  : mkdtempSync(join(tmpdir(), 'dsh-delegate-logs-'));
if (values['log-dir'] !== undefined) mkdirSync(logDir, { recursive: true, mode: 0o700 });
const stdoutLog = join(logDir, 'run.stdout.log');
const stderrLog = join(logDir, 'run.stderr.log');

const tempDir = mkdtempSync(join(tmpdir(), 'dsh-delegate-settings-'));
const tempSettings = join(tempDir, 'run.settings.json');
const tempPatch = join(tempDir, 'run.patch.json');
writeFileSync(tempSettings, JSON.stringify(settings), { mode: 0o600 });
writeFileSync(tempPatch, JSON.stringify([{ id: 'settings', config: { path: tempSettings, watch: false } }]), { mode: 0o600 });

// Launcher flags first, then `--`: the outer launcher consumes the first `--`,
// the headless app consumes the second, so the task text reaches it verbatim
// even when it starts with a dash or contains newlines.
const argv = ['--profile', 'headless', '--patch', tempPatch, '--', '--', prompt];
const startedAt = Date.now();
let settled = false;
let timedOut = false;
let timeoutTimer;
let killTimer;
let child;

function killTree(signal) {
  if (child?.pid === undefined) return;
  if (process.platform === 'win32') {
    try { child.kill(signal); } catch { /* already gone */ }
    return;
  }
  try { process.kill(-child.pid, signal); } catch { try { child.kill(signal); } catch { /* already gone */ } }
}

function result(status, exitCode, signal, error) {
  let finalText = '';
  let finalTextTruncated = false;
  try {
    finalText = readFileSync(stdoutLog, 'utf8').trim();
  } catch { /* no stdout log: leave the final text empty */ }
  if (finalText.length > FINAL_TEXT_LIMIT) {
    finalText = finalText.slice(0, FINAL_TEXT_LIMIT);
    finalTextTruncated = true;
  }
  return {
    status,
    exitCode,
    signal: signal ?? null,
    error: error ?? null,
    elapsedSeconds: Math.round((Date.now() - startedAt) / 100) / 10,
    timeoutSeconds,
    requested: { provider: values.provider, model: values.model, reasoningEffort: values.effort === '' ? null : values.effort },
    cwd,
    taskFile,
    inputDelivery: fileBackedTask ? 'file-reference' : 'inline',
    logPaths: { stdout: stdoutLog, stderr: stderrLog },
    finalText,
    finalTextTruncated,
    note: 'exit 0 only means the dsh agent finished, not that the task is correct: review the real diff/artifacts and run the relevant checks.',
  };
}

function finish(payload) {
  if (settled) return;
  settled = true;
  clearTimeout(timeoutTimer);
  rmSync(tempDir, { recursive: true, force: true });
  process.exitCode = payload.status === 'ok' ? 0 : 1;
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

let outFd;
let errFd;
try {
  outFd = openSync(stdoutLog, 'w', 0o600);
  errFd = openSync(stderrLog, 'w', 0o600);
  child = spawn(DSH_BIN, argv, {
    cwd,
    env: process.env,
    detached: process.platform !== 'win32',
    stdio: ['ignore', outFd, errFd],
  });
} catch (error) {
  finish(result('spawn-error', null, null, error.message));
} finally {
  if (outFd !== undefined) closeSync(outFd);
  if (errFd !== undefined) closeSync(errFd);
}

if (child !== undefined) {
  // The child owns its process group, so a terminal signal reaches only this
  // wrapper: forward it and let the child's close event finalize and clean up.
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => killTree(signal));
  child.on('error', (error) => finish(result('spawn-error', null, null, error.message)));
  child.on('close', (code, signal) => {
    finish(result(timedOut ? 'timeout' : (code === 0 ? 'ok' : 'nonzero'), code, signal, null));
  });
  timeoutTimer = setTimeout(() => {
    timedOut = true;
    killTree('SIGTERM');
    killTimer = setTimeout(() => {
      killTree('SIGKILL');
      if (!settled) killTimer = setTimeout(() => finish(result('timeout', null, null, 'timeout: process tree did not exit after SIGKILL')), FINALIZE_FAILSAFE_MS);
    }, SIGKILL_GRACE_MS);
  }, timeoutSeconds * 1000);
}
