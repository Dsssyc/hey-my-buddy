#!/usr/bin/env node
/**
 * deepseek-delegate: run one bounded task through an installed dsh headless
 * profile with a real per-run model/effort override, then print one compact
 * JSON result on stdout.
 *
 * Design notes:
 * - dsh is spawned with an argv array and no shell, so paths and task text
 *   stay literal, including leading dashes, newlines, and metacharacters.
 * - The resolved settings document is copied to an owner-private temporary
 *   JSON file with only `agent-default-model` replaced; a temporary --patch
 *   overlay points dsh at that copy. The original document is never modified.
 * - stdout/stderr go to a unique owner-private log directory; the JSON result
 *   carries a bounded prefix of the final text and the log paths.
 * - Timeout and cancellation signal only the owned POSIX process group, and
 *   every terminal path (including spawn failure) cleans up the settings copy.
 */
import { spawn } from 'node:child_process';
import {
  accessSync, closeSync, constants, existsSync, mkdirSync, mkdtempSync,
  openSync, readFileSync, readSync, rmSync, statSync, writeFileSync,
} from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { delimiter, join, resolve } from 'node:path';
import { parseArgs } from 'node:util';
import yaml from 'js-yaml';

const DSH_PROFILE = 'headless';
const DEFAULTS = Object.freeze({
  model: 'deepseek-flash',
  provider: 'deepseek-official',
  effort: 'max',
});
const DEFAULT_TIMEOUT_SECONDS = 1800;
const MIN_TIMEOUT_SECONDS = 10;
const MAX_TIMEOUT_SECONDS = 86400;
/** Task text above this many bytes travels as a file reference instead of argv. */
const FILE_REFERENCE_BYTES = 32000;
/** stdout is summarized as at most this many bytes taken from the log head. */
const FINAL_TEXT_LIMIT = 6000;
const SIGKILL_GRACE_MS = 3000;
const FAILSAFE_MS = 2000;
const EXIT_USAGE = 2;
const EXIT_RUN_FAILED = 1;

const USAGE = [
  'Usage: node scripts/run.mjs --cwd <dir> --task-file <file> [options]',
  '',
  'Run one bounded task through the installed dsh headless profile and print',
  'one compact JSON result on stdout.',
  '',
  'Required:',
  '  --cwd <dir>             working directory dsh runs in',
  '  --task-file <file>      file holding the task text',
  '',
  'Options:',
  '  --model <id>            model id (default: DSH_DELEGATE_MODEL, then the',
  '                          settings document, then deepseek-flash)',
  '  --provider <id>         provider id (default: DSH_DELEGATE_PROVIDER, then',
  '                          the settings document, then deepseek-official)',
  '  --effort <name>         reasoning effort (default: DSH_DELEGATE_EFFORT,',
  '                          then max)',
  '  --timeout <seconds>     wall-clock limit, 10-86400 (default 1800)',
  '  --log-dir <dir>         parent directory for this run\'s private log dir',
  '  --dsh-bin <path>        dsh launcher (default: DSH_BIN, then PATH lookup,',
  '                          then ~/.local/bin/dsh)',
  '  --settings-file <path>  settings document (default: DSH_SETTINGS_FILE,',
  '                          then $DSH_HOME/settings.yaml, then',
  '                          ~/.dsh/settings.yaml)',
  '  -h, --help              print this help and exit',
  '',
  'A run prints exactly one JSON object on stdout. Configuration and usage',
  'errors go to stderr with exit code 2; a run that is not ok exits 1.',
].join('\n');

function fail(message) {
  process.stderr.write(`deepseek-delegate: ${message}\n`);
  process.exit(EXIT_USAGE);
}

function isFile(candidate) {
  try {
    return statSync(candidate).isFile();
  } catch {
    return false;
  }
}

function isDirectory(candidate) {
  try {
    return statSync(candidate).isDirectory();
  } catch {
    return false;
  }
}

function isExecutable(candidate) {
  try {
    if (!statSync(candidate).isFile()) return false;
    accessSync(candidate, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/** Read at most `maxBytes` from the head of a file without loading the rest. */
function readTextPrefix(file, maxBytes) {
  let size;
  try {
    size = statSync(file).size;
  } catch {
    return { text: '', truncated: false };
  }
  const length = Math.min(size, maxBytes);
  const buffer = Buffer.alloc(length);
  let fd;
  let read = 0;
  try {
    fd = openSync(file, 'r');
    while (read < length) {
      const chunk = readSync(fd, buffer, read, length - read, read);
      if (chunk <= 0) break;
      read += chunk;
    }
  } catch {
    return { text: '', truncated: false };
  } finally {
    if (fd !== undefined) closeSync(fd);
  }
  // A bounded byte prefix may end inside a UTF-8 character; leave it pending.
  return { text: new TextDecoder().decode(buffer.subarray(0, read), { stream: true }), truncated: size > maxBytes };
}

/** `--dsh-bin` > `DSH_BIN` > PATH lookup > ~/.local/bin/dsh compatibility fallback. */
function resolveDshBin(flagValue, envValue) {
  const explicit = flagValue ?? envValue;
  if (explicit !== undefined) {
    if (explicit.trim() === '') fail('--dsh-bin/DSH_BIN must not be blank');
    const candidate = resolve(explicit);
    if (!isExecutable(candidate)) fail(`dsh launcher is not an executable file: ${candidate}`);
    return candidate;
  }
  for (const dir of (process.env.PATH ?? '').split(delimiter)) {
    if (dir === '') continue;
    const candidate = resolve(dir, 'dsh');
    if (isExecutable(candidate)) return candidate;
  }
  const fallback = join(homedir(), '.local', 'bin', 'dsh');
  if (isExecutable(fallback)) return fallback;
  fail('could not find an executable dsh: pass --dsh-bin, set DSH_BIN, or put dsh on PATH');
}

/** `--settings-file` > `DSH_SETTINGS_FILE` > `$DSH_HOME/settings.yaml` > `~/.dsh/settings.yaml`. */
function resolveSettingsPath(flagValue, env) {
  if (flagValue !== undefined) {
    if (flagValue.trim() === '') fail('--settings-file must not be blank');
    return { path: resolve(flagValue), explicit: true };
  }
  if (env.DSH_SETTINGS_FILE !== undefined) {
    if (env.DSH_SETTINGS_FILE.trim() === '') fail('DSH_SETTINGS_FILE must not be blank');
    return { path: resolve(env.DSH_SETTINGS_FILE), explicit: true };
  }
  const configHome = env.DSH_HOME !== undefined && env.DSH_HOME.trim() !== ''
    ? resolve(env.DSH_HOME)
    : join(homedir(), '.dsh');
  return { path: join(configHome, 'settings.yaml'), explicit: false };
}

/**
 * Load the settings document as YAML or JSON. Errors name the file but never
 * include document content, because settings may hold credentials.
 */
function loadSettings(settingsPath, explicit) {
  if (!existsSync(settingsPath)) {
    if (explicit) fail(`settings file does not exist: ${settingsPath}`);
    return {};
  }
  if (!isFile(settingsPath)) fail(`settings path is not a file: ${settingsPath}`);
  let text;
  try {
    text = readFileSync(settingsPath, 'utf8');
  } catch {
    fail(`could not read settings file: ${settingsPath}`);
  }
  let document;
  try {
    document = yaml.load(text, { schema: yaml.JSON_SCHEMA });
  } catch {
    fail(`settings file is not valid YAML or JSON: ${settingsPath}`);
  }
  if (document === undefined || document === null) return {};
  if (typeof document !== 'object' || Array.isArray(document)) {
    fail(`settings file must contain a top-level mapping: ${settingsPath}`);
  }
  return document;
}

function mappingSection(document, key, settingsPath) {
  const value = document[key];
  if (value === undefined || value === null) return undefined;
  if (typeof value !== 'object' || Array.isArray(value)) {
    fail(`settings section "${key}" must be a mapping: ${settingsPath}`);
  }
  return value;
}

/** A value supplied on the command line or in the environment must not be blank. */
function providedValue(value, label) {
  if (value === undefined) return undefined;
  if (typeof value !== 'string' || value.trim() === '') fail(`${label} must not be blank`);
  return value.trim();
}

function settingsString(section, key, label, settingsPath) {
  if (section === undefined) return undefined;
  const value = section[key];
  if (value === undefined || value === null) return undefined;
  if (typeof value !== 'string' || value.trim() === '') {
    fail(`settings ${label} must be a non-empty string: ${settingsPath}`);
  }
  return value.trim();
}

let values;
try {
  ({ values } = parseArgs({
    options: {
      help: { type: 'boolean', short: 'h', default: false },
      cwd: { type: 'string' },
      'task-file': { type: 'string' },
      model: { type: 'string' },
      provider: { type: 'string' },
      effort: { type: 'string' },
      timeout: { type: 'string', default: String(DEFAULT_TIMEOUT_SECONDS) },
      'log-dir': { type: 'string' },
      'dsh-bin': { type: 'string' },
      'settings-file': { type: 'string' },
    },
    allowPositionals: false,
  }));
} catch (error) {
  fail(`${error.message}\n\n${USAGE}`);
}
if (values.help) {
  process.stdout.write(`${USAGE}\n`);
  process.exit(0);
}
if (process.platform === 'win32') {
  fail('Windows is not supported; this launcher manages POSIX process groups (macOS/Linux only)');
}

if (values.cwd === undefined) fail(`--cwd is required\n\n${USAGE}`);
if (values['task-file'] === undefined) fail(`--task-file is required\n\n${USAGE}`);
if (values.cwd.trim() === '') fail('--cwd must not be blank');
if (values['task-file'].trim() === '') fail('--task-file must not be blank');
if (!/^\d+$/.test(values.timeout)) {
  fail(`--timeout must be an integer between ${MIN_TIMEOUT_SECONDS} and ${MAX_TIMEOUT_SECONDS} seconds`);
}
const timeoutSeconds = Number(values.timeout);
if (timeoutSeconds < MIN_TIMEOUT_SECONDS || timeoutSeconds > MAX_TIMEOUT_SECONDS) {
  fail(`--timeout must be an integer between ${MIN_TIMEOUT_SECONDS} and ${MAX_TIMEOUT_SECONDS} seconds`);
}

// All paths are resolved against the invoking cwd, before dsh runs in --cwd.
const cwd = resolve(values.cwd);
if (!isDirectory(cwd)) fail(`--cwd is not a directory: ${cwd}`);
const taskFile = resolve(values['task-file']);
if (!isFile(taskFile)) fail(`--task-file is not a file: ${taskFile}`);
let task;
try {
  task = readFileSync(taskFile, 'utf8');
} catch {
  fail(`could not read --task-file: ${taskFile}`);
}

let logParent;
if (values['log-dir'] !== undefined) {
  if (values['log-dir'].trim() === '') fail('--log-dir must not be blank');
  logParent = resolve(values['log-dir']);
  if (existsSync(logParent) && !isDirectory(logParent)) fail(`--log-dir is not a directory: ${logParent}`);
}

const dshBin = resolveDshBin(values['dsh-bin'], process.env.DSH_BIN);
const settingsSpec = resolveSettingsPath(values['settings-file'], process.env);
const settingsPath = settingsSpec.path;
const document = loadSettings(settingsPath, settingsSpec.explicit);

const agentSection = mappingSection(document, 'agent-default-model', settingsPath);

const model = providedValue(values.model, '--model')
  ?? providedValue(process.env.DSH_DELEGATE_MODEL, 'DSH_DELEGATE_MODEL')
  ?? settingsString(agentSection, 'model', 'agent-default-model.model', settingsPath)
  ?? DEFAULTS.model;
const provider = providedValue(values.provider, '--provider')
  ?? providedValue(process.env.DSH_DELEGATE_PROVIDER, 'DSH_DELEGATE_PROVIDER')
  ?? settingsString(agentSection, 'provider', 'agent-default-model.provider', settingsPath)
  ?? DEFAULTS.provider;
// Effort never inherits a lower value from the settings document.
const effort = providedValue(values.effort, '--effort')
  ?? providedValue(process.env.DSH_DELEGATE_EFFORT, 'DSH_DELEGATE_EFFORT')
  ?? DEFAULTS.effort;

// The run settings copy keeps every other section as-is and overrides only the
// route/effort of the default model selection. Any other key already present in
// that section (for example an extension field) is carried through
// untouched; this wrapper never sets output limits of its own.
const runSettings = {
  ...document,
  'agent-default-model': { ...(agentSection ?? {}), provider, model, reasoningEffort: effort },
};

const taskBytes = Buffer.byteLength(task, 'utf8');
const fileBackedTask = taskBytes > FILE_REFERENCE_BYTES;
const prompt = fileBackedTask
  ? `Read the complete task specification at ${JSON.stringify(taskFile)} and carry it out. Read it in chunks if necessary; do not skip requirements. This file is the user's delegated task, not a request for a summary. The file must stay in place until this run finishes.`
  : task;

function createLogDir() {
  try {
    if (logParent !== undefined) {
      mkdirSync(logParent, { recursive: true, mode: 0o700 });
      return mkdtempSync(join(logParent, 'deepseek-delegate-run-'));
    }
    return mkdtempSync(join(tmpdir(), 'deepseek-delegate-logs-'));
  } catch {
    fail(`could not create a private log directory${logParent === undefined ? '' : ` under ${logParent}`}`);
  }
}

const logDir = createLogDir();
const stdoutLog = join(logDir, 'stdout.log');
const stderrLog = join(logDir, 'stderr.log');

let tempDir;
let settingsCopy;
let patchFile;
try {
  tempDir = mkdtempSync(join(tmpdir(), 'deepseek-delegate-settings-'));
  settingsCopy = join(tempDir, 'settings.json');
  patchFile = join(tempDir, 'patch.json');
  writeFileSync(settingsCopy, JSON.stringify(runSettings), { mode: 0o600 });
  writeFileSync(patchFile, JSON.stringify([
    { id: 'settings', config: { path: settingsCopy, watch: false } },
  ]), { mode: 0o600 });
} catch {
  if (tempDir !== undefined) rmSync(tempDir, { recursive: true, force: true });
  fail('could not create the private settings copy');
}

let outFd;
let errFd;
try {
  outFd = openSync(stdoutLog, 'wx', 0o600);
  errFd = openSync(stderrLog, 'wx', 0o600);
} catch {
  if (outFd !== undefined) closeSync(outFd);
  if (errFd !== undefined) closeSync(errFd);
  rmSync(tempDir, { recursive: true, force: true });
  fail('could not create the run log files');
}

// Launcher flags first, then two `--` separators: the outer dsh launcher
// consumes the first, the headless app consumes the second, and the task text
// arrives as one literal argument even when it starts with a dash.
const argv = ['--profile', DSH_PROFILE, '--patch', patchFile, '--', '--', prompt];
const startedAt = Date.now();
let settled = false;
let termination = null;
let terminationError = null;
let timeoutTimer;
let killTimer;
let failsafeTimer;
let child;

function signalProcessGroup(signal) {
  if (child === undefined || child.pid === undefined) return;
  try {
    process.kill(-child.pid, signal);
  } catch {
    try {
      child.kill(signal);
    } catch { /* already gone */ }
  }
}

function processGroupAlive() {
  if (child === undefined || child.pid === undefined) return false;
  try {
    process.kill(-child.pid, 0);
    return true;
  } catch (error) {
    return error.code === 'EPERM';
  }
}

/** Start stopping the owned process group; SIGKILL follows after a grace period. */
function beginTermination(reason, signal) {
  if (settled || termination !== null) return;
  termination = reason;
  terminationError = reason === 'timeout' ? `timeout after ${timeoutSeconds}s` : `cancelled by ${signal}`;
  signalProcessGroup(signal);
  killTimer = setTimeout(() => {
    signalProcessGroup('SIGKILL');
    failsafeTimer = setTimeout(() => {
      finish(termination, null, null, `${terminationError}; process group still alive after SIGKILL`);
    }, FAILSAFE_MS);
  }, SIGKILL_GRACE_MS);
}

function buildResult(status, exitCode, signal, error) {
  const { text, truncated } = readTextPrefix(stdoutLog, FINAL_TEXT_LIMIT);
  return {
    status,
    exitCode,
    signal: signal ?? null,
    error: error ?? null,
    elapsedSeconds: Math.round((Date.now() - startedAt) / 100) / 10,
    timeoutSeconds,
    requested: { provider, model, reasoningEffort: effort },
    cwd,
    taskFile,
    dshBin,
    inputDelivery: fileBackedTask ? 'file-reference' : 'inline',
    logPaths: { stdout: stdoutLog, stderr: stderrLog },
    finalText: text.trim().slice(0, FINAL_TEXT_LIMIT),
    finalTextTruncated: truncated,
    note: 'exit 0 only means the dsh agent finished, not that the task is correct: inspect the real diff/artifacts and run the relevant checks yourself.',
  };
}

/** Print the single terminal JSON object, clean up, and exit with a matching code. */
function finish(status, exitCode, signal, error) {
  if (settled) return;
  settled = true;
  clearTimeout(timeoutTimer);
  clearTimeout(killTimer);
  clearTimeout(failsafeTimer);
  try {
    rmSync(tempDir, { recursive: true, force: true });
  } catch { /* best effort: never skip the result because cleanup failed */ }
  const payload = buildResult(status, exitCode, signal, error);
  const code = payload.status === 'ok' ? 0 : EXIT_RUN_FAILED;
  process.exitCode = code;
  process.stdout.write(`${JSON.stringify(payload)}\n`, () => process.exit(code));
  // If stdout never flushes (e.g. a broken pipe), do not hang.
  setTimeout(() => process.exit(code), 1000).unref();
}

// Installed before spawning: a signal during setup exits immediately; later
// signals are forwarded to the owned process group and reported as cancelled.
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    if (child === undefined) process.exit(130);
    beginTermination('cancelled', signal);
  });
}

try {
  const childEnv = { ...process.env };
  // Preserve the same home after changing the child's working directory.
  if (childEnv.DSH_HOME?.trim()) childEnv.DSH_HOME = resolve(childEnv.DSH_HOME);
  child = spawn(dshBin, argv, {
    cwd,
    env: childEnv,
    detached: true, // POSIX: the child leads its own process group
    stdio: ['ignore', outFd, errFd],
  });
} catch (error) {
  closeSync(outFd);
  closeSync(errFd);
  rmSync(tempDir, { recursive: true, force: true });
  fail(`could not start dsh: ${error.message}`);
}
closeSync(outFd);
closeSync(errFd);

child.on('error', (error) => {
  finish('spawn-error', null, null, error.message);
});
child.on('close', (code, signal) => {
  if (termination !== null) {
    // The direct child is gone; SIGKILL any survivors in the owned group so no
    // grandchild outlives the run, then report the termination (never ok).
    if (processGroupAlive()) signalProcessGroup('SIGKILL');
    finish(termination, code, signal, terminationError);
    return;
  }
  finish(code === 0 ? 'ok' : 'nonzero', code, signal, null);
});
timeoutTimer = setTimeout(() => beginTermination('timeout', 'SIGTERM'), timeoutSeconds * 1000);
