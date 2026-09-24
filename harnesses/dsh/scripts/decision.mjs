#!/usr/bin/env node
/**
 * deepseek-delegate lightweight decision adapter.
 *
 * One reusable single-decision entrypoint over the already installed dsh
 * model/auth/provider capability. It runs one bounded, tool-free model call
 * through the native harness `llm` service and returns one validated bounded
 * selection decision. It is deliberately NOT an agent run:
 *
 * - no session, no agent loop, no tool schemas, no repository context, no
 *   skills: the model sees one frozen instruction prefix and one deterministic
 *   JSON payload whose shared table snapshot comes first;
 * - no credentials are read, copied, or printed; the provider adapter resolves
 *   its own harness credential reference per request exactly as an ordinary
 *   dsh run does, and no provider message, endpoint, or settings text crosses
 *   into the returned envelope;
 * - no automatic model retry: a failure is reported once, honestly;
 * - no writes outside this run's private directory, and no board transaction
 *   is open across the model call (this process cannot open one at all);
 * - no global configuration is written, and a harness home whose profile is
 *   not already installed is refused rather than initialized.
 *
 * Safety gate: before the real boot, `dsh --dump-config` must prove that the
 * composed profile DISABLES the ordinary headless agent runner and mounts this
 * adapter's plugin. Without that proof the adapter refuses to run, because the
 * agent runner would otherwise execute this run's sentinel argument as a real
 * coding task with real tools. The plugin re-checks the same condition against
 * the tree that actually mounted.
 *
 * Process ownership: the launcher is spawned as its own process-group leader,
 * every signal goes to the whole group, and `shutdownConfirmed` is reported
 * only after the group is observed gone. A live group member keeps the claim
 * false; unknown is never reported as stopped.
 *
 * The Python blackboard remains the only authority: this adapter returns a
 * recommendation, and the caller decides whether to adopt it.
 *
 * Usage:
 *   node scripts/decision.mjs --input-file request.json --output-file result.json
 *     [--dsh-bin <path>] [--settings-file <path>] [--timeout <seconds>]
 *
 * @module harnesses/dsh/scripts/decision
 */
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { performance } from 'node:perf_hooks';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

/** Absolute path of the in-process plugin this adapter mounts. */
export const PLUGIN_PATH = fileURLToPath(new URL('../plugins/decision.mjs', import.meta.url));

/** dsh profile the decision plugin is mounted into. */
export const DEFAULT_PROFILE = 'headless';

/**
 * Positional argument the headless app's own command layer requires before it
 * publishes its startup service. The runner that would consume it is disabled
 * in the patch below, so this string is never sent anywhere.
 */
const HEADLESS_STARTUP_SENTINEL = 'deepseek-delegate-decision-adapter';

/** Largest accepted request document. */
export const MAX_INPUT_BYTES = 512 * 1024;
/** Largest accepted model answer, mirrored from the plugin's own bound. */
export const MAX_ANSWER_BYTES = 32 * 1024;
/**
 * Bounds for the current table a caller may submit in one request.
 *
 * The Python table owner may hold more rows than one bounded request carries.
 * When it does, it must submit an explicitly selected batch (the profiles and
 * cards relevant to this decision plus their evidence) rather than truncating
 * silently here: this adapter validates the bound and refuses an oversized
 * document instead of deciding which rows to drop.
 */
export const MAX_REQUEST_PROFILES = 200;
export const MAX_REQUEST_CARDS = 512;
export const MAX_REQUEST_PREFERENCES = 256;
export const MAX_REQUEST_EVIDENCE = 512;
export const MAX_REQUEST_ANNOTATIONS = 200;
export const MAX_REQUEST_ROUTING_PREFERENCES = 8;
/** Bounded stdout/stderr diagnostics kept from the child. */
const MAX_CHILD_STDOUT_BYTES = 16 * 1024;
const MAX_CHILD_STDERR_BYTES = 8 * 1024;

export const DEFAULT_TIMEOUT_SECONDS = 120;
export const MIN_TIMEOUT_SECONDS = 5;
export const MAX_TIMEOUT_SECONDS = 1_800;
/** Grace between SIGTERM and SIGKILL for the owned child. */
export const KILL_GRACE_MS = 5_000;
/** Hard bound on waiting for the child to be reaped after SIGKILL. */
export const REAP_TIMEOUT_MS = 5_000;

/** Exit codes: 0 ok, 1 failure, 2 usage, 3 timeout, 4 spawn failure. */
export const EXIT = Object.freeze({ ok: 0, failure: 1, usage: 2, timeout: 3, spawn: 4 });

/**
 * Diagnose the dsh launcher the same way the existing runner does, without
 * reading or writing any configuration.
 */
export function resolveDshBin(supplied, env = process.env) {
  if (supplied !== undefined) {
    if (supplied.trim() === '') throw new DecisionError('dsh-bin-empty', '--dsh-bin must not be blank');
    return supplied;
  }
  if (typeof env.DSH_BIN === 'string' && env.DSH_BIN.trim() !== '') return env.DSH_BIN;
  return 'dsh';
}

/** Structured adapter failure carrying a stable machine code. */
export class DecisionError extends Error {
  constructor(code, message, { exitCode = EXIT.failure, details } = {}) {
    super(message);
    this.name = 'DecisionError';
    this.code = code;
    this.exitCode = exitCode;
    if (details !== undefined) this.details = details;
  }
}

/** Parse `--flag value` arguments; every flag requires a value. */
export function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === '-h' || token === '--help') return { help: true };
    if (!token.startsWith('--')) {
      throw new DecisionError('usage', `unexpected argument: ${token}`, { exitCode: EXIT.usage });
    }
    const name = token.slice(2);
    if (!['input-file', 'output-file', 'dsh-bin', 'settings-file', 'timeout'].includes(name)) {
      throw new DecisionError('usage', `unknown option: ${token}`, { exitCode: EXIT.usage });
    }
    const value = argv[index + 1];
    if (value === undefined || value.startsWith('--')) {
      throw new DecisionError('usage', `${token} requires a value`, { exitCode: EXIT.usage });
    }
    if (values[name] !== undefined) {
      throw new DecisionError('usage', `${token} was given more than once`, { exitCode: EXIT.usage });
    }
    values[name] = value;
    index += 1;
  }
  const inputFile = values['input-file'];
  const outputFile = values['output-file'];
  if (inputFile === undefined) throw new DecisionError('usage', '--input-file is required', { exitCode: EXIT.usage });
  if (outputFile === undefined) throw new DecisionError('usage', '--output-file is required', { exitCode: EXIT.usage });
  if (inputFile.trim() === '') throw new DecisionError('usage', '--input-file must not be blank', { exitCode: EXIT.usage });
  if (outputFile.trim() === '') throw new DecisionError('usage', '--output-file must not be blank', { exitCode: EXIT.usage });
  const rawTimeout = values.timeout ?? String(DEFAULT_TIMEOUT_SECONDS);
  if (!/^\d+$/.test(rawTimeout)) {
    throw new DecisionError('usage', '--timeout must be an integer number of seconds', { exitCode: EXIT.usage });
  }
  const timeoutSeconds = Number(rawTimeout);
  if (timeoutSeconds < MIN_TIMEOUT_SECONDS || timeoutSeconds > MAX_TIMEOUT_SECONDS) {
    throw new DecisionError('usage', `--timeout must be an integer from ${MIN_TIMEOUT_SECONDS} through ${MAX_TIMEOUT_SECONDS}`, { exitCode: EXIT.usage });
  }
  return {
    inputFile,
    outputFile,
    dshBin: values['dsh-bin'],
    settingsFile: values['settings-file'],
    timeoutSeconds,
  };
}

/** Render the CLI help text; needs neither dsh nor credentials. */
export function helpText() {
  return `Usage: node scripts/decision.mjs --input-file <path> --output-file <path> [options]

Runs one bounded, tool-free decision call through the installed dsh model
service and writes one JSON envelope to --output-file.

Options:
  --input-file <path>    request document (required)
  --output-file <path>   envelope destination (required)
  --dsh-bin <path>       dsh launcher (default: $DSH_BIN, then dsh on PATH)
  --settings-file <path> dsh settings document the child boots with
  --timeout <seconds>    whole-child bound, ${MIN_TIMEOUT_SECONDS}-${MAX_TIMEOUT_SECONDS} (default ${DEFAULT_TIMEOUT_SECONDS})
  -h, --help             print this text and exit

Exit codes: 0 ok, 1 failure, 2 usage, 3 timeout, 4 dsh could not start.`;
}

/** Whether a value is a bounded, non-empty identifier. */
function isIdentifier(value, max = 256) {
  return typeof value === 'string' && value.length > 0 && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value);
}

/** Whether a value is a bounded, non-empty text. */
function isText(value, max = 4_000) {
  return typeof value === 'string' && value.length > 0 && value.length <= max && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value);
}

/** Own-property read that tolerates non-objects. */
function own(object, key) {
  if (object === null || typeof object !== 'object' || Array.isArray(object)) return undefined;
  return Object.hasOwn(object, key) ? object[key] : undefined;
}

/** Validate one bounded collection of JSON objects. */
function checkObjects(value, { name, min, max }) {
  if (!Array.isArray(value)) throw new DecisionError('request-invalid', `"${name}" must be an array`);
  if (value.length < min) throw new DecisionError('request-invalid', `"${name}" must hold at least ${min} entry`);
  if (value.length > max) throw new DecisionError('request-invalid', `"${name}" holds ${value.length} entries, above the ${max} bound`);
  for (const [index, entry] of value.entries()) {
    if (entry === null || typeof entry !== 'object' || Array.isArray(entry)) {
      throw new DecisionError('request-invalid', `"${name}"[${index}] must be an object`);
    }
    const rendered = JSON.stringify(entry);
    if (rendered === undefined) throw new DecisionError('request-invalid', `"${name}"[${index}] is not JSON-serializable`);
    if (Buffer.byteLength(rendered, 'utf8') > 32 * 1024) {
      throw new DecisionError('request-invalid', `"${name}"[${index}] exceeds the 32 KiB per-entry bound`);
    }
  }
}

/**
 * Validate the whole request document before any process starts.
 *
 * Every failure here is a caller error: the adapter never invents a decision
 * to cover a malformed request, and it never reaches the model with an input
 * whose candidates or evidence it could not bound.
 */
export function validateRequest(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new DecisionError('request-invalid', 'the request document must be a JSON object');
  }
  const allowedTopLevel = ['operation', 'requestId', 'profile', 'tableRevision', 'task', 'profiles', 'cards', 'preferences', 'evidence', 'annotations', 'routingPreferences'];
  for (const key of Object.keys(value)) {
    if (!allowedTopLevel.includes(key)) {
      throw new DecisionError('request-invalid', `unexpected request field "${key}"`);
    }
  }
  const operation = own(value, 'operation');
  if (operation !== 'select') {
    throw new DecisionError('request-invalid', '"operation" must be "select"');
  }
  const requestId = own(value, 'requestId');
  if (!isIdentifier(requestId, 200)) throw new DecisionError('request-invalid', '"requestId" must be a bounded non-empty string');
  const profile = own(value, 'profile');
  if (profile === null || typeof profile !== 'object' || Array.isArray(profile)) {
    throw new DecisionError('request-invalid', '"profile" must be an object');
  }
  const provider = own(profile, 'provider');
  const model = own(profile, 'model');
  const effort = own(profile, 'effort');
  const adapter = own(profile, 'adapter');
  if (!isIdentifier(provider) || !isIdentifier(model) || !isIdentifier(effort)) {
    throw new DecisionError('request-invalid', '"profile" must carry bounded non-empty provider, model and effort strings');
  }
  if (adapter !== undefined && !isIdentifier(adapter, 32)) throw new DecisionError('request-invalid', '"profile.adapter" must be a bounded identifier');
  const tableRevision = own(value, 'tableRevision');
  if (!Number.isSafeInteger(tableRevision) || tableRevision < 0) {
    throw new DecisionError('request-invalid', '"tableRevision" must be a non-negative integer');
  }
  checkObjects(own(value, 'profiles'), { name: 'profiles', min: 1, max: MAX_REQUEST_PROFILES });
  const seenProfiles = new Set();
  for (const [index, entry] of own(value, 'profiles').entries()) {
    const profileId = own(entry, 'profileId');
    if (!isIdentifier(profileId)) throw new DecisionError('request-invalid', `"profiles"[${index}].profileId must be a bounded non-empty string`);
    if (seenProfiles.has(profileId)) throw new DecisionError('request-invalid', `"profiles"[${index}].profileId repeats ${profileId}`);
    seenProfiles.add(profileId);
  }
  const task = own(value, 'task');
  if (!isText(task, 20_000)) throw new DecisionError('request-invalid', '"task" must be bounded non-empty text for a select request');
  const cards = own(value, 'cards');
  if (cards !== undefined) checkObjects(cards, { name: 'cards', min: 0, max: MAX_REQUEST_CARDS });
  const preferences = own(value, 'preferences');
  if (preferences !== undefined) checkObjects(preferences, { name: 'preferences', min: 0, max: MAX_REQUEST_PREFERENCES });
  const evidence = own(value, 'evidence');
  if (evidence !== undefined) checkObjects(evidence, { name: 'evidence', min: 0, max: MAX_REQUEST_EVIDENCE });
  const annotations = own(value, 'annotations');
  if (annotations !== undefined) {
    checkObjects(annotations, { name: 'annotations', min: 0, max: MAX_REQUEST_ANNOTATIONS });
    for (const entry of annotations) {
      if (!isIdentifier(own(entry, 'profileId')) || !isText(own(entry, 'text'), 4_000) ||
          !Number.isSafeInteger(own(entry, 'revision')) || own(entry, 'revision') < 0 ||
          !isIdentifier(own(entry, 'updatedAt'), 80)) {
        throw new DecisionError('request-invalid', 'annotations require bounded profileId, text, revision and updatedAt');
      }
    }
  }
  const routingPreferences = own(value, 'routingPreferences');
  if (routingPreferences !== undefined) {
    checkObjects(routingPreferences, { name: 'routingPreferences', min: 0, max: MAX_REQUEST_ROUTING_PREFERENCES });
    for (const entry of routingPreferences) {
      const match = own(entry, 'match');
      if (match === null || typeof match !== 'object' || Array.isArray(match) || Object.keys(match).length === 0 ||
          Object.keys(match).some((key) => !['adapter', 'provider', 'model', 'effort'].includes(key) || !isIdentifier(match[key])) ||
          !isText(own(entry, 'reason'), 512)) {
        throw new DecisionError('request-invalid', 'routingPreferences require a nonempty bounded match and reason');
      }
    }
  }
  return value;
}

/** Read and validate the request file. */
export function readRequestFile(file) {
  let info;
  try {
    info = statSync(file);
  } catch {
    throw new DecisionError('input-unreadable', `cannot read --input-file ${file}`);
  }
  if (!info.isFile()) throw new DecisionError('input-unreadable', `--input-file ${file} is not a regular file`);
  if (info.size > MAX_INPUT_BYTES) {
    throw new DecisionError('input-too-large', `--input-file is ${info.size} bytes, above the ${MAX_INPUT_BYTES}-byte bound`);
  }
  let text;
  try {
    text = readFileSync(file, 'utf8');
  } catch {
    throw new DecisionError('input-unreadable', `cannot read --input-file ${file}`);
  }
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    throw new DecisionError('input-invalid-json', '--input-file is not valid JSON');
  }
  return validateRequest(value);
}

/** Write one JSON document, replacing any previous file atomically. */
export function writeOutputFile(file, value) {
  mkdirSync(dirname(resolve(file)), { recursive: true });
  const temporary = uniqueSibling(file);
  writeFileSync(temporary, `${JSON.stringify(value)}\n`, { mode: 0o600, flag: 'wx' });
  renameSync(temporary, file);
}

/** Monotonic-enough unique sibling name for a private temporary file. */
let temporaryCounter = 0;
function uniqueSibling(file) {
  temporaryCounter += 1;
  return join(dirname(resolve(file)), `.decision-${String(process.pid)}-${String(temporaryCounter)}.tmp`);
}

/**
 * Build the temporary loader patch that composes the decision run.
 *
 * Two rows are disabled and one inserted:
 * - `headless-runner` would otherwise start an ordinary agent turn; this
 *   adapter must never run the coding agent, its tools, or its prompt;
 * - `session-telemetry-otel` batches exports on a timer, which would keep the
 *   child alive after the call;
 * - `session-title-llm` would make a second, unrelated model call for a
 *   session title if a session were created;
 * - the inserted row mounts {@link PLUGIN_PATH} with this run's private paths.
 *
 * A patch that targets a row an installation does not have is skipped with a
 * warning by the loader, so this overlay stays portable across dsh versions.
 */
export function composePatch({ runDir, timeoutMs, settingsFile }) {
  const rows = [
    { id: 'headless-runner', disabled: true },
    { id: 'session-telemetry-otel', disabled: true },
    { id: 'session-title-llm', disabled: true },
  ];
  if (settingsFile !== undefined) rows.push({ id: 'settings', config: { path: settingsFile } });
  rows.push({
    insert: [{
      id: 'deepseek-delegate-decision',
      name: PLUGIN_PATH,
      config: { inputFile: join(runDir, 'request.json'), outputDir: runDir, timeoutMs },
    }],
  });
  return rows;
}

/** Choose the dsh profile directory the child boots. */
export function resolveProfileRoot(env = process.env) {
  const home = typeof env.DSH_HOME === 'string' && env.DSH_HOME.trim() !== ''
    ? resolve(env.DSH_HOME)
    : join(homedir(), '.dsh');
  return join(home, 'profiles', DEFAULT_PROFILE);
}

/** Strip one layer of YAML quoting. */
function unquote(value) {
  const trimmed = value.trim();
  if (trimmed.length >= 2 && ((trimmed.startsWith("'") && trimmed.endsWith("'")) || (trimmed.startsWith('"') && trimmed.endsWith('"')))) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

/**
 * Read the rows of a `dsh --dump-config` document.
 *
 * A deliberately small scanner over the documented dump shape: a row starts at
 * `- id: <id>` in column zero, and that row's own keys are the 2-space indented
 * `key: value` lines before the next row. It is not a YAML parser, and it is
 * used only where a miss is safe: {@link analyzeComposedTree} treats "row not
 * found" as "cannot prove", and this adapter then refuses to run.
 *
 * @returns a `Map` of row id to that row's top-level keys.
 */
export function parseComposedRows(text) {
  const rows = new Map();
  const lines = text.split('\n');
  let current = null;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const idMatch = /^- id: (.+)$/.exec(line);
    if (idMatch !== null) {
      current = new Map();
      const id = unquote(idMatch[1]);
      if (!rows.has(id)) rows.set(id, current);
      continue;
    }
    if (current === null) continue;
    const keyMatch = /^ {2}([A-Za-z0-9_-]+): ?(.*)$/.exec(line);
    if (keyMatch === null) continue;
    const key = keyMatch[1];
    const raw = keyMatch[2].trim();
    // The dump renders long values as YAML block scalars (`>-`, `|`, ...); the
    // value is then the following more-indented lines, not the indicator.
    if (/^[>|][-+]?$/.test(raw)) {
      const folded = raw.startsWith('>');
      const parts = [];
      while (index + 1 < lines.length && /^ {4,}\S/.test(lines[index + 1])) {
        index += 1;
        parts.push(lines[index].trim());
      }
      if (!current.has(key)) current.set(key, parts.join(folded ? ' ' : '\n'));
      continue;
    }
    if (!current.has(key)) current.set(key, raw);
  }
  return rows;
}

/**
 * Decide whether one composed profile tree is safe to boot for a decision run.
 *
 * The load-bearing requirement is that the headless agent runner is DISABLED.
 * If that row is missing, renamed, or active, the ordinary coding agent would
 * run this adapter's sentinel argument as a real task with real tools — exactly
 * what a decision run must never do. Anything this function cannot prove is a
 * refusal, so an unknown dsh version fails closed instead of silently running
 * an agent.
 *
 * @returns `{ ok: true }` or `{ ok: false, problem, detail }`.
 */
export function analyzeComposedTree(text, { runnerId = 'headless-runner', pluginId, pluginName } = {}) {
  const rows = parseComposedRows(text);
  const runner = rows.get(runnerId);
  if (runner === undefined) {
    return { ok: false, problem: 'runner-row-missing', detail: `the composed profile has no "${runnerId}" row` };
  }
  if (unquote(runner.get('disabled') ?? '') !== 'true') {
    return { ok: false, problem: 'runner-not-disabled', detail: `the composed profile does not disable "${runnerId}"` };
  }
  if (pluginId !== undefined) {
    const plugin = rows.get(pluginId);
    if (plugin === undefined) {
      return { ok: false, problem: 'plugin-row-missing', detail: `the composed profile has no "${pluginId}" row` };
    }
    if (unquote(plugin.get('disabled') ?? '') === 'true') {
      return { ok: false, problem: 'plugin-row-disabled', detail: `the composed profile disables "${pluginId}"` };
    }
    if (pluginName !== undefined) {
      const declared = unquote(plugin.get('name') ?? '');
      const accepted = new Set([pluginName, pathToFileURL(pluginName).href]);
      try {
        accepted.add(realpathSync(pluginName));
        accepted.add(pathToFileURL(realpathSync(pluginName)).href);
      } catch { /* the plugin path is already fixed at build time */ }
      if (!accepted.has(declared)) {
        return { ok: false, problem: 'plugin-row-mismatch', detail: 'the composed profile mounts a different decision plugin' };
      }
    }
  }
  return { ok: true };
}

/**
 * Prove, before any real boot, that the installed profile composes into a
 * decision run.
 *
 * Two refusals happen here, both fail-closed and both before a model or an
 * agent can start:
 * - the profile directory must already exist, because booting a missing
 *   profile makes dsh CREATE it under the user's harness home, and this
 *   adapter never writes global configuration;
 * - `dsh --dump-config` must show the agent runner disabled and this plugin
 *   mounted.
 */
export function verifyComposedTree({ dshBin, env, cwd, patchFile, profile = DEFAULT_PROFILE }) {
  const profileRoot = resolveProfileRoot(env);
  if (!existsSync(join(profileRoot, 'package.json'))) {
    throw new DecisionError('decision-profile-missing', `the dsh profile "${profile}" is not installed at ${profileRoot}`, { exitCode: EXIT.failure });
  }
  const result = spawnSync(dshBin, ['--profile', profile, '--patch', patchFile, '--dump-config'], {
    cwd,
    env,
    encoding: 'utf8',
    timeout: 60_000,
    maxBuffer: 8 * 1024 * 1024,
    windowsHide: true,
  });
  if (result.error !== undefined && result.error !== null) {
    const code = result.error.code;
    if (code === 'ENOENT' || code === 'EACCES' || code === 'EPERM') {
      throw new DecisionError('dsh-unavailable', 'the dsh launcher could not be started', { exitCode: EXIT.spawn });
    }
    throw new DecisionError('decision-profile-unverified', 'the dsh profile could not be inspected before the decision run', { exitCode: EXIT.failure });
  }
  if (result.status !== 0) {
    throw new DecisionError('decision-profile-unverified', 'dsh refused to compose the decision profile', { exitCode: EXIT.failure, details: { exitCode: result.status } });
  }
  const verdict = analyzeComposedTree(result.stdout ?? '', { pluginId: 'deepseek-delegate-decision', pluginName: PLUGIN_PATH });
  if (!verdict.ok) {
    throw new DecisionError('decision-profile-unsafe', `refusing to run: ${verdict.detail}`, { exitCode: EXIT.failure, details: { reason: verdict.problem } });
  }
  return verdict;
}

/** Collect a bounded string from a stream. */
function collector(limit) {
  const chunks = [];
  let size = 0;
  return {
    push(chunk) {
      if (size >= limit) return;
      const text = chunk.toString('utf8');
      const remaining = limit - size;
      chunks.push(text.length > remaining ? text.slice(0, remaining) : text);
      size += Math.min(text.length, remaining);
    },
    text() {
      return chunks.join('');
    },
  };
}

/**
 * Run the child dsh process group to completion under an explicit timeout.
 *
 * The launcher is spawned as the leader of its OWN process group (`detached`),
 * and every signal is sent to the whole group with a negative pid. A launcher
 * that itself spawned something must not leave that work running behind a
 * "stopped" claim: reaping the direct child proves only that the direct child
 * exited, so shutdown is confirmed by polling the GROUP until it disappears.
 * When the group cannot be proved gone, `shutdownConfirmed` stays false.
 *
 * @returns `{ status, signal, stdout, stderr, elapsedSeconds, shutdownConfirmed, termination }`
 * where `status` is `ok`, `nonzero`, `timeout`, `cancelled` or `spawn-error`.
 */
export async function runChild({ dshBin, argv, env, cwd, timeoutMs, signal }) {
  const started = performance.now();
  return new Promise((resolvePromise) => {
    let child;
    try {
      child = spawn(dshBin, argv, {
        cwd,
        env,
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
        // New process group: the whole tree this launcher creates is ours to signal.
        detached: process.platform !== 'win32',
      });
    } catch (error) {
      resolvePromise({
        status: 'spawn-error', signal: null, stdout: '', stderr: errorMessage(error), elapsedSeconds: 0,
        shutdownConfirmed: true, termination: null,
      });
      return;
    }
    const stdout = collector(MAX_CHILD_STDOUT_BYTES);
    const stderr = collector(MAX_CHILD_STDERR_BYTES);
    child.stdout.on('data', (chunk) => stdout.push(chunk));
    child.stderr.on('data', (chunk) => stderr.push(chunk));
    const groupPid = child.pid ?? null;
    let settled = false;
    let termination = null;
    let killTimer = null;
    let groupReapTimer = null;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(idleTimer);
      if (killTimer !== null) clearTimeout(killTimer);
      if (groupReapTimer !== null) clearTimeout(groupReapTimer);
      resolvePromise({
        ...result,
        stdout: stdout.text(),
        stderr: stderr.text(),
        elapsedSeconds: Math.round(performance.now() - started) / 1000,
      });
    };
    /** Signal the owned process group, falling back to the direct child. */
    const signalOwned = (name) => {
      if (groupPid !== null) {
        try {
          process.kill(-groupPid, name);
          return;
        } catch { /* the group is gone; fall through to the direct child */ }
      }
      try {
        child.kill(name);
      } catch { /* already gone */ }
    };
    /** Whether the owned process group still has at least one member. */
    const groupAlive = () => {
      if (groupPid === null) return false;
      try {
        process.kill(-groupPid, 0);
        return true;
      } catch (error) {
        return error.code === 'EPERM';
      }
    };
    /** Wait for the group to disappear, bounded, then settle honestly either way. */
    const awaitGroupGone = (result) => {
      const deadline = performance.now() + REAP_TIMEOUT_MS;
      const poll = () => {
        if (settled) return;
        if (!groupAlive()) {
          finish({ ...result, shutdownConfirmed: true });
          return;
        }
        if (performance.now() >= deadline) {
          finish({
            ...result,
            shutdownConfirmed: false,
            termination: `${result.termination ?? 'terminated'}; an owned process is still present after SIGKILL`,
          });
          return;
        }
        groupReapTimer = setTimeout(poll, 50);
      };
      poll();
    };
    const beginTermination = (reason) => {
      if (termination !== null || settled) return;
      termination = reason;
      signalOwned('SIGTERM');
      killTimer = setTimeout(() => {
        signalOwned('SIGKILL');
      }, KILL_GRACE_MS);
    };
    const idleTimer = setTimeout(() => beginTermination('timeout'), timeoutMs);
    if (signal !== undefined) {
      if (signal.aborted) beginTermination('cancelled');
      else signal.addEventListener('abort', () => beginTermination('cancelled'), { once: true });
    }
    child.on('error', (error) => {
      finish({
        status: termination ?? 'spawn-error',
        signal: null,
        // The launcher failed, so this adapter cannot prove whether a process
        // of its own ever existed; unknown is never reported as stopped.
        shutdownConfirmed: false,
        termination: termination === null ? errorMessage(error) : `${termination} (launcher failed before it could be reaped)`,
      });
    });
    child.on('close', (code, closeSignal) => {
      const status = termination ?? (code === 0 ? 'ok' : 'nonzero');
      const base = {
        status,
        exitCode: code,
        signal: closeSignal,
        termination: termination === null ? null : `${termination} (launcher reaped)`,
      };
      // The direct child is reaped. Confirm the whole owned group is gone
      // before claiming shutdown, then settle.
      awaitGroupGone(base);
    });
  });
}

/** Trim one error to a short, credential-free message. */
function errorMessage(error) {
  if (error === null || error === undefined) return null;
  if (typeof error === 'string') return error.slice(0, 300);
  return typeof error.message === 'string' ? error.message.slice(0, 300) : String(error).slice(0, 300);
}

/** Read one JSON document, or `undefined` when it is absent or malformed. */
function readJsonIfPresent(file) {
  try {
    return JSON.parse(readFileSync(file, 'utf8'));
  } catch {
    return undefined;
  }
}

/**
 * The public envelope for one bounded selection decision, in its documented
 * key order.
 */
export function successEnvelope(request, child, result) {
  const base = {
    status: 'ok',
    operation: request.operation,
    tableRevision: request.tableRevision,
    requested: { provider: request.profile.provider, model: request.profile.model, reasoningEffort: request.profile.effort },
    resolved: result.resolvedConfig,
    // The installed provider adapters do not report the identity the provider
    // actually served, so this adapter will not claim one: unknown stays unknown.
    observed: null,
    usage: result.usage ?? null,
    elapsedSeconds: roundSeconds(child.elapsedSeconds),
    shutdownConfirmed: child.shutdownConfirmed === true,
  };
  return { ...base, decision: result.decision };
}

/** Round one already-measured duration to 0.1 s. */
function roundSeconds(value) {
  return Math.round(Number(value) * 10) / 10;
}

/** The public failure envelope. */
export function failureEnvelope(request, { code, message, details, elapsedSeconds, shutdownConfirmed }) {
  return {
    status: 'error',
    ...(request === undefined ? {} : { operation: request.operation, tableRevision: request.tableRevision }),
    code,
    message,
    ...(details === undefined ? {} : { details }),
    elapsedSeconds: roundSeconds(elapsedSeconds),
    shutdownConfirmed: shutdownConfirmed === true,
  };
}

/** Validate the child's success payload and map it onto the public envelope. */
export function interpretSuccess(request, child, payload) {
  if (own(payload, 'operation') !== request.operation) {
    return { error: new DecisionError('child-protocol-error', 'the decision child reported a different operation') };
  }
  if (request.operation === 'select') {
    const decision = own(payload, 'decision');
    if (decision === null || typeof decision !== 'object' || Array.isArray(decision)) {
      return { error: new DecisionError('child-protocol-error', 'the decision child returned no select decision') };
    }
    const allowed = ['profileId', 'reason', 'evidenceIds'];
    if (Object.keys(decision).some((key) => !allowed.includes(key))) {
      return { error: new DecisionError('child-protocol-error', 'the select decision carries an unexpected field') };
    }
    const profileId = own(decision, 'profileId');
    const reason = own(decision, 'reason');
    const evidenceIds = own(decision, 'evidenceIds');
    if (profileId !== null && !isIdentifier(profileId)) {
      return { error: new DecisionError('child-protocol-error', 'the select decision carries no usable profileId') };
    }
    if (!isText(reason)) return { error: new DecisionError('child-protocol-error', 'the select decision carries no usable reason') };
    if (!Array.isArray(evidenceIds) || evidenceIds.some((entry) => !isIdentifier(entry))) {
      return { error: new DecisionError('child-protocol-error', 'the select decision carries no usable evidenceIds') };
    }
    const candidates = new Map(own(request, 'profiles').map((entry) => [own(entry, 'profileId'), entry]));
    if (profileId !== null) {
      const candidate = candidates.get(profileId);
      if (candidate === undefined || own(candidate, 'enabled') !== true || own(candidate, 'available') !== true) {
        return { error: new DecisionError('decision-not-candidate', `the model selected ${profileId}, which is not an enabled available candidate`) };
      }
      const suppliedEvidence = new Set([...evidenceIdsOf(candidate), ...evidenceIdsOfRequest(request, profileId)]);
      if (evidenceIds.some((entry) => !suppliedEvidence.has(entry))) {
        return { error: new DecisionError('decision-evidence-unknown', 'the model cited evidence that was not supplied') };
      }
    } else if (evidenceIds.length > 0) {
      return { error: new DecisionError('decision-evidence-unknown', 'an abstention must not cite evidence') };
    }
    return { envelope: { ...successEnvelope(request, child, payload), decision: { profileId, reason, evidenceIds: [...evidenceIds] } } };
  }
}

/** The candidate record for one profile id, when present. */
function evidenceIdsOf(candidate) {
  const list = own(candidate, 'evidenceIds');
  return Array.isArray(list) ? list.filter((entry) => typeof entry === 'string') : [];
}

/** Evidence ids supplied in the request evidence collection for one profile. */function evidenceIdsOfRequest(request, profileId) {
  const evidence = own(request, 'evidence');
  if (!Array.isArray(evidence)) return [];
  return evidence
    .filter((entry) => own(entry, 'profileId') === profileId && typeof own(entry, 'evidenceId') === 'string')
    .map((entry) => own(entry, 'evidenceId'));
}

/** Map a child failure code to the adapter's exit code. */
function exitCodeForFailure(code) {
  if (code === 'call-timeout') return EXIT.timeout;
  return EXIT.failure;
}

/**
 * Classify a child that failed before producing a usable result.
 *
 * Only a fixed machine code and the numeric exit facts cross into the public
 * envelope. The child's stderr is deliberately NOT copied: dsh diagnostics can
 * quote settings paths, endpoints, or credential references, and this envelope
 * is persisted by the Python board and rendered by the console. The bounded
 * stderr stays in this process's memory for the caller's own logging.
 */
function classifyChildFailure(child) {
  const stderr = child.stderr;
  if (child.status === 'ok' && child.exitCode === 0) {
    // The child exited cleanly but wrote neither a result nor an error: that is
    // a failure of the child protocol, not of the dsh installation.
    return new DecisionError('child-no-result', 'the decision child exited without a result document', {
      details: { exitCode: 0, status: 'ok' },
    });
  }
  // A nonzero exit with a dsh diagnostic means the launcher or the profile
  // rejected this invocation.
  if (/unknown option|takes none of|a task is required|error: /.test(stderr)) {
    return new DecisionError('dsh-invocation-failed', 'dsh rejected the decision adapter invocation', {
      details: { exitCode: child.exitCode ?? null },
    });
  }
  return new DecisionError('dsh-boot-failed', 'the decision child exited without a result document', {
    details: { exitCode: child.exitCode ?? null, status: child.status },
  });
}

/**
 * One complete adapter run.
 *
 * @returns `{ envelope, exitCode }`. Nothing is written to stdout: the caller
 * owns the output file, and a failure is reported there as well.
 */
export async function runDecision({ inputFile, outputFile, dshBin, settingsFile, timeoutSeconds }, { env = process.env, signal } = {}) {
  const started = performance.now();
  const request = readRequestFile(inputFile);
  if (settingsFile !== undefined && !existsSync(settingsFile)) {
    throw new DecisionError('settings-file-missing', `--settings-file ${settingsFile} does not exist`, { exitCode: EXIT.usage });
  }
  const runDir = mkdtempSync(join(tmpdir(), 'deepseek-delegate-decision-'));
  let child = null;
  try {
    const patchFile = join(runDir, 'patch.json');
    writeFileSync(join(runDir, 'request.json'), JSON.stringify(request), { mode: 0o600 });
    writeFileSync(patchFile, JSON.stringify(composePatch({ runDir, timeoutMs: timeoutSeconds * 1_000, settingsFile })), { mode: 0o600 });
    const argv = ['--profile', DEFAULT_PROFILE, '--patch', patchFile, '--', '--', HEADLESS_STARTUP_SENTINEL];
    const resolvedDshBin = resolveDshBin(dshBin, env);
    // Fail-closed safety gate, BEFORE the real boot: the composed profile must
    // prove that the headless agent runner is disabled and this plugin is
    // mounted. Otherwise the ordinary coding agent could run the sentinel
    // argument as a real task with real tools.
    verifyComposedTree({ dshBin: resolvedDshBin, env, cwd: runDir, patchFile });
    child = await runChild({
      dshBin: resolvedDshBin,
      argv,
      env,
      cwd: runDir,
      timeoutMs: timeoutSeconds * 1_000,
      signal,
    });
    if (child.status === 'spawn-error') {
      throw new DecisionError('dsh-unavailable', 'the dsh launcher could not be started', { exitCode: EXIT.spawn });
    }
    if (child.status === 'timeout') {
      return {
        envelope: failureEnvelope(request, {
          code: 'timeout',
          message: `the decision child exceeded ${timeoutSeconds}s and was terminated`,
          elapsedSeconds: (performance.now() - started) / 1000,
          shutdownConfirmed: child.shutdownConfirmed,
        }),
        exitCode: EXIT.timeout,
      };
    }
    if (child.status === 'cancelled') {
      return {
        envelope: failureEnvelope(request, {
          code: 'cancelled',
          message: 'the decision run was cancelled by its owner',
          elapsedSeconds: (performance.now() - started) / 1000,
          shutdownConfirmed: child.shutdownConfirmed,
        }),
        exitCode: EXIT.failure,
      };
    }
    const success = readJsonIfPresent(join(runDir, 'result.json'));
    const failure = readJsonIfPresent(join(runDir, 'error.json'));
    if (success !== undefined) {
      // A result document is not by itself a success: the child must also have
      // exited zero. A launcher or harness that wrote a file and then failed
      // (crash, OOM kill, signal) is reported as the failure it was.
      if (child.exitCode !== 0 || child.signal !== null) {
        return {
          envelope: failureEnvelope(request, {
            code: 'child-exit-nonzero',
            message: 'the decision child did not exit cleanly, so its result document is not accepted',
            details: { exitCode: child.exitCode ?? null, signal: child.signal ?? null },
            elapsedSeconds: (performance.now() - started) / 1000,
            shutdownConfirmed: child.shutdownConfirmed,
          }),
          exitCode: EXIT.failure,
        };
      }
      const interpreted = interpretSuccess(request, child, success);
      if (interpreted.envelope !== undefined) {
        return { envelope: interpreted.envelope, exitCode: EXIT.ok };
      }
      const code = interpreted.error.code;
      return {
        envelope: failureEnvelope(request, {
          code,
          message: interpreted.error.message,
          details: interpreted.error.details,
          elapsedSeconds: (performance.now() - started) / 1000,
          shutdownConfirmed: child.shutdownConfirmed,
        }),
        exitCode: exitCodeForFailure(code),
      };
    }
    if (failure !== undefined && typeof own(failure, 'code') === 'string') {
      const code = own(failure, 'code');
      return {
        envelope: failureEnvelope(request, {
          code,
          message: `the decision call failed: ${code}`,
          details: own(failure, 'detail') ?? undefined,
          elapsedSeconds: (performance.now() - started) / 1000,
          shutdownConfirmed: child.shutdownConfirmed,
        }),
        exitCode: exitCodeForFailure(code),
      };
    }
    const classified = classifyChildFailure(child);
    return {
      envelope: failureEnvelope(request, {
        code: classified.code,
        message: classified.message,
        details: classified.details,
        elapsedSeconds: (performance.now() - started) / 1000,
        shutdownConfirmed: child.shutdownConfirmed,
      }),
      exitCode: classified.exitCode,
    };
  } finally {
    // The run directory is this process's own private scratch space: the
    // request, the patch, and the child's result all live and die here.
    rmSync(runDir, { recursive: true, force: true });
  }
}

/** CLI entrypoint; returns the process exit code. */
export async function main(argv, { env = process.env, signal } = {}) {
  let options;
  try {
    options = parseArgs(argv);
    if (options.help === true) {
      process.stdout.write(`${helpText()}\n`);
      return EXIT.ok;
    }
  } catch (error) {
    process.stderr.write(`${error.code ?? 'usage'}: ${error.message}\n`);
    return error.exitCode ?? EXIT.usage;
  }
  const controller = new AbortController();
  const owner = signal ?? controller.signal;
  const onSignal = (name) => {
    controller.abort(new Error(`cancelled by ${name}`));
  };
  const handlers = [['SIGINT', () => onSignal('SIGINT')], ['SIGTERM', () => onSignal('SIGTERM')]];
  for (const [name, handler] of handlers) process.on(name, handler);
  let outcome;
  try {
    outcome = await runDecision(options, { env, signal: owner });
  } catch (error) {
    const code = error instanceof DecisionError ? error.code : 'adapter-failed';
    const exitCode = error instanceof DecisionError ? error.exitCode : EXIT.failure;
    const envelope = failureEnvelope(undefined, {
      code,
      message: error instanceof Error ? error.message : String(error),
      details: error instanceof DecisionError ? error.details : undefined,
      elapsedSeconds: 0,
      // Nothing was started, so nothing of ours can still be running.
      shutdownConfirmed: exitCode !== EXIT.spawn,
    });
    try {
      writeOutputFile(options.outputFile, envelope);
    } catch {
      process.stderr.write(`output-unwritable: cannot write ${options.outputFile}\n`);
      return EXIT.usage;
    }
    process.stderr.write(`${code}: ${envelope.message}\n`);
    return exitCode;
  } finally {
    for (const [name, handler] of handlers) process.removeListener(name, handler);
  }
  try {
    writeOutputFile(options.outputFile, outcome.envelope);
  } catch {
    process.stderr.write(`output-unwritable: cannot write ${options.outputFile}\n`);
    return EXIT.usage;
  }
  return outcome.exitCode;
}

/** Whether this module is the process entrypoint. */
export function isEntrypoint(metaUrl, argv1) {
  if (argv1 === undefined) return false;
  try {
    return metaUrl === pathToFileURL(resolve(argv1)).href;
  } catch {
    return false;
  }
}

if (isEntrypoint(import.meta.url, process.argv[1])) {
  process.exitCode = await main(process.argv.slice(2));
}
