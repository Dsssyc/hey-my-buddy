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
 * - Workspace grouping (on by default) never writes dsh storage directly:
 *   the CLI canonicalizes --cwd, mounts a temporary observer plugin that
 *   captures this run's exact root session id, and after the owned process has
 *   fully stopped, groups that session through the running web host's
 *   authenticated RPC (`workspace/create` + idempotent `session/create`), then
 *   verifies membership through the host's own read-back. `--no-workspace`
 *   keeps the fully offline standalone behavior.
 */
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  accessSync, closeSync, constants, existsSync, mkdirSync, mkdtempSync,
  openSync, readFileSync, readSync, realpathSync, rmSync, statSync, writeFileSync,
} from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { delimiter, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import yaml from 'js-yaml';
import {
  DEFAULT_WEB_TIMEOUT_SECONDS, MAX_WEB_TIMEOUT_SECONDS, MIN_WEB_TIMEOUT_SECONDS,
  WebRpcError, WebSetupError, adoptSession, connectWebHost, resolveWebTarget, resolveWorkspace,
} from './lib/web-host.mjs';

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
/** Private capture file written by the observer plugin inside this run's log dir. */
const CAPTURE_FILENAME = 'capture.json';
/** The observer plugin shipped beside this script; mounted only for grouped runs. */
const CAPTURE_PLUGIN_PATH = fileURLToPath(new URL('../plugins/session-capture.mjs', import.meta.url));

const USAGE = [
  'Usage: node scripts/run.mjs --cwd <dir> --task-file <file> [options]',
  '       node scripts/run.mjs --cwd <dir> --attach-session <id> [options]',
  '',
  'Run one bounded task through the installed dsh headless profile and print',
  'one compact JSON result on stdout.',
  '',
  'Required:',
  '  --cwd <dir>             working directory dsh runs in (canonicalized with',
  '                          realpath before the run)',
  '  --task-file <file>      file holding the task text; omit it only with',
  '                          --attach-session',
  '',
  'Options:',
  '  --model <id>            model id (default: DSH_DELEGATE_MODEL, then the',
  '                          settings document, then deepseek-flash)',
  '  --provider <id>         provider id (default: DSH_DELEGATE_PROVIDER, then',
  '                          the settings document, then deepseek-official)',
  '  --effort <name>         reasoning effort (default: DSH_DELEGATE_EFFORT,',
  '                          then max)',
  '  --timeout <seconds>     headless time limit, 10-86400 (default 1800)',
  '  --log-dir <dir>         parent directory for this run\'s private log dir',
  '  --dsh-bin <path>        dsh launcher (default: DSH_BIN, then PATH lookup,',
  '                          then ~/.local/bin/dsh)',
  '  --settings-file <path>  settings document (default: DSH_SETTINGS_FILE,',
  '                          then $DSH_HOME/settings.yaml, then',
  '                          ~/.dsh/settings.yaml)',
  '  --no-workspace          do not group this run in a dsh web workspace;',
  '                          keeps standalone/offline headless behavior',
  '  --dsh-web-url <url>     launch URL printed by `dsh web` (contains ?token=);',
  '                          prefer --dsh-web-url-file to keep it out of argv',
  '  --dsh-web-url-file <f>  file holding that launch URL (default:',
  '                          DSH_WEB_URL_FILE, then',
  '                          ${XDG_CONFIG_HOME:-~/.config}/deepseek-delegate/web-url)',
  '  --web-timeout <seconds> bound for each web-host request, 1-120 (default 15)',
  '  --attach-session <id>   group an EXISTING completed ordinary session via',
  '                          the running web host; runs no model task',
  '  -h, --help              print this help and exit',
  '',
  'Web URL precedence (grouped runs): --dsh-web-url > --dsh-web-url-file >',
  'DSH_WEB_URL > DSH_WEB_URL_FILE > the default credential file. Only',
  'loopback http(s) origins are accepted; non-root paths, embedded',
  'credentials, cross-origin redirects, and non-loopback hosts are rejected.',
  '',
  'A run prints exactly one JSON object on stdout. Configuration and usage',
  'errors go to stderr with exit code 2; a run that is not ok, or a grouped',
  'run whose session could not be verified as a workspace member, exits 1.',
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

function sha256Hex(text) {
  return createHash('sha256').update(text, 'utf8').digest('hex');
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
      'no-workspace': { type: 'boolean', default: false },
      'dsh-web-url': { type: 'string' },
      'dsh-web-url-file': { type: 'string' },
      'web-timeout': { type: 'string', default: String(DEFAULT_WEB_TIMEOUT_SECONDS) },
      'attach-session': { type: 'string' },
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

const workspaceEnabled = values['no-workspace'] !== true;
const attachSession = values['attach-session'] === undefined ? undefined : values['attach-session'].trim();

if (values.cwd === undefined) fail(`--cwd is required\n\n${USAGE}`);
if (values.cwd.trim() === '') fail('--cwd must not be blank');
if (!workspaceEnabled && (values['dsh-web-url'] !== undefined || values['dsh-web-url-file'] !== undefined)) {
  fail('--no-workspace cannot be combined with --dsh-web-url or --dsh-web-url-file');
}
if (values['attach-session'] !== undefined) {
  if (attachSession === '') fail('--attach-session must not be blank');
  if (!/^[A-Za-z0-9._:-]+$/.test(attachSession)) {
    fail('--attach-session must be a plain session id (letters, digits, dot, underscore, colon, dash)');
  }
  if (values['task-file'] !== undefined) {
    fail('--attach-session does not take --task-file: it attaches an existing session without a model run');
  }
  if (!workspaceEnabled) fail('--attach-session requires workspace grouping; remove --no-workspace');
} else if (values['task-file'] === undefined) {
  fail(`--task-file is required\n\n${USAGE}`);
}
if (values['task-file'] !== undefined && values['task-file'].trim() === '') {
  fail('--task-file must not be blank');
}
if (!/^\d+$/.test(values.timeout)) {
  fail(`--timeout must be an integer between ${MIN_TIMEOUT_SECONDS} and ${MAX_TIMEOUT_SECONDS} seconds`);
}
const timeoutSeconds = Number(values.timeout);
if (timeoutSeconds < MIN_TIMEOUT_SECONDS || timeoutSeconds > MAX_TIMEOUT_SECONDS) {
  fail(`--timeout must be an integer between ${MIN_TIMEOUT_SECONDS} and ${MAX_TIMEOUT_SECONDS} seconds`);
}
if (!/^\d+$/.test(values['web-timeout'])) {
  fail(`--web-timeout must be an integer between ${MIN_WEB_TIMEOUT_SECONDS} and ${MAX_WEB_TIMEOUT_SECONDS} seconds`);
}
const webTimeoutSeconds = Number(values['web-timeout']);
if (webTimeoutSeconds < MIN_WEB_TIMEOUT_SECONDS || webTimeoutSeconds > MAX_WEB_TIMEOUT_SECONDS) {
  fail(`--web-timeout must be an integer between ${MIN_WEB_TIMEOUT_SECONDS} and ${MAX_WEB_TIMEOUT_SECONDS} seconds`);
}
const webTimeoutMs = webTimeoutSeconds * 1000;

const startedAt = Date.now();

// All paths are resolved against the invoking cwd, before dsh runs in --cwd.
const cwdInput = resolve(values.cwd);
if (!isDirectory(cwdInput)) fail(`--cwd is not a directory: ${cwdInput}`);
let cwd;
try {
  cwd = realpathSync(cwdInput);
} catch {
  fail(`--cwd could not be canonicalized: ${cwdInput}`);
}

// ---------------------------------------------------------------------------
// --attach-session: group one existing completed session, no model run.
// ---------------------------------------------------------------------------
if (attachSession !== undefined) {
  let host;
  let workspace;
  try {
    const target = resolveWebTarget(values, process.env);
    host = await connectWebHost(target, { timeoutMs: webTimeoutMs });
    const resolvedWorkspace = await resolveWorkspace(host, cwd, { timeoutMs: webTimeoutMs });
    const adopted = await adoptSession(host, {
      sessionId: attachSession,
      workspaceId: resolvedWorkspace.id,
      cwd,
    }, { timeoutMs: webTimeoutMs });
    workspace = { enabled: true, bound: true, id: adopted.id, path: adopted.path, sessionId: adopted.sessionId };
  } catch (error) {
    const message = error instanceof WebSetupError || error instanceof WebRpcError
      ? error.message
      : 'unexpected attach failure';
    if (host === undefined) fail(message);
    workspace = { enabled: true, bound: false, id: null, path: cwd, sessionId: attachSession, error: message };
  }
  emitAttach(workspace, workspace.bound ? null : workspace.error);
  // Never fall through into run setup: the pending stdout write keeps the loop
  // alive until emitPayload's flush callback (or the fallback) exits with the
  // matching code, and an empty loop exits with that same process.exitCode.
  await new Promise(() => {});
}

/** Print the single attach-mode JSON result and exit. */
function emitAttach(workspace, error) {
  const payload = {
    status: error === null ? 'ok' : 'attach-error',
    mode: 'attach',
    exitCode: error === null ? 0 : null,
    signal: null,
    error,
    elapsedSeconds: Math.round((Date.now() - startedAt) / 100) / 10,
    timeoutSeconds: null,
    requested: null,
    cwd,
    taskFile: null,
    dshBin: null,
    inputDelivery: null,
    logPaths: null,
    finalText: '',
    finalTextTruncated: false,
    workspace,
    note: 'attach mode only groups an existing completed session into its workspace through the running dsh web host; it runs no model task.',
  };
  // Attach mode never initializes run-only child/timer/temporary-file state.
  const code = error === null ? 0 : EXIT_RUN_FAILED;
  process.exitCode = code;
  process.stdout.write(`${JSON.stringify(payload)}\n`, () => process.exit(code));
  setTimeout(() => process.exit(code), 1000).unref();
}

// ---------------------------------------------------------------------------
// Normal run setup.
// ---------------------------------------------------------------------------
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
const promptSha256 = sha256Hex(prompt);

// ---------------------------------------------------------------------------
// Workspace preflight: resolve/authenticate the host and register the
// canonical cwd BEFORE the paid headless run starts. Missing or expired auth
// fails here, so no orphan run is ever created. Adoption itself happens only
// after the owned process has fully stopped.
// ---------------------------------------------------------------------------
let webHost;
let workspaceInfo;
if (workspaceEnabled) {
  if (!isFile(CAPTURE_PLUGIN_PATH)) fail(`session capture plugin is missing: ${CAPTURE_PLUGIN_PATH}`);
  let target;
  try {
    target = resolveWebTarget(values, process.env);
  } catch (error) {
    fail(error instanceof WebSetupError ? error.message : 'could not resolve the dsh web URL');
  }
  try {
    webHost = await connectWebHost(target, { timeoutMs: webTimeoutMs });
  } catch (error) {
    fail(error instanceof WebSetupError || error instanceof WebRpcError ? error.message : 'could not authenticate to the dsh web host');
  }
  try {
    workspaceInfo = await resolveWorkspace(webHost, cwd, { timeoutMs: webTimeoutMs });
  } catch (error) {
    fail(error instanceof WebRpcError ? error.message : 'could not register the workspace on the dsh web host');
  }
}

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
const capturePath = workspaceEnabled ? join(logDir, CAPTURE_FILENAME) : null;

let tempDir;
let settingsCopy;
let patchFile;
try {
  tempDir = mkdtempSync(join(tmpdir(), 'deepseek-delegate-settings-'));
  settingsCopy = join(tempDir, 'settings.json');
  patchFile = join(tempDir, 'patch.json');
  writeFileSync(settingsCopy, JSON.stringify(runSettings), { mode: 0o600 });
  const patchRows = [{ id: 'settings', config: { path: settingsCopy, watch: false } }];
  if (workspaceEnabled) {
    // A temporary, self-contained observer plugin captures this run's exact root
    // session id from the live session event feed; nothing else changes.
    patchRows.push({
      insert: [{
        id: 'deepseek-delegate-session-capture',
        name: CAPTURE_PLUGIN_PATH,
        config: { capturePath, promptSha256, cwd },
      }],
    });
  }
  writeFileSync(patchFile, JSON.stringify(patchRows), { mode: 0o600 });
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
let settled = false;
let settling = false;
let childExited = false;
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
  if (settled || settling || childExited || termination !== null) return;
  termination = reason;
  terminationError = reason === 'timeout' ? `timeout after ${timeoutSeconds}s` : `cancelled by ${signal}`;
  signalProcessGroup(signal);
  killTimer = setTimeout(() => {
    signalProcessGroup('SIGKILL');
    failsafeTimer = setTimeout(() => {
      void settle(termination, null, null, `${terminationError}; process group still alive after SIGKILL`);
    }, FAILSAFE_MS);
  }, SIGKILL_GRACE_MS);
}

/**
 * Read and validate the observer's private capture metadata. The prompt hash
 * and canonical cwd must match this run exactly; a missing or mismatching
 * capture is a hard grouping failure, never a silently ungrouped run.
 */
function readCapture() {
  if (capturePath === null || !existsSync(capturePath)) {
    throw new Error('the dsh observer did not write capture metadata, so this run\'s session id is unknown');
  }
  let record;
  try {
    record = JSON.parse(readFileSync(capturePath, 'utf8'));
  } catch {
    throw new Error('the dsh observer capture metadata is not valid JSON');
  }
  if (record === null || typeof record !== 'object') throw new Error('the dsh observer capture metadata is malformed');
  if (record.ambiguous === true) throw new Error('multiple root sessions matched this run; refusing an ambiguous binding');
  if (typeof record.sessionId !== 'string' || record.sessionId === '') {
    throw new Error('the dsh observer capture metadata carries no session id');
  }
  if (record.promptSha256 !== promptSha256) {
    throw new Error('the dsh observer capture metadata does not match this run\'s prompt');
  }
  if (record.cwd !== cwd) {
    throw new Error('the dsh observer capture metadata cwd does not match the canonical --cwd');
  }
  return record.sessionId;
}

function baseWorkspace(bound = false) {
  return {
    enabled: workspaceEnabled,
    bound,
    id: workspaceInfo === undefined ? null : workspaceInfo.id,
    path: cwd,
    sessionId: null,
  };
}

/**
 * Post-run grouping. Runs only after the owned child process has fully exited
 * and persisted; adoption before that would be unsafe. On any failure the
 * workspace metadata stays visible with `bound: false` and an error, while the
 * original child status, exit code, and log paths are preserved.
 */
async function finalizeGrouping(status, shutdownConfirmed) {
  const workspace = baseWorkspace();
  if (status === 'spawn-error') {
    workspace.error = 'the run never started, so there was no session to group';
    return workspace;
  }
  let sessionId;
  try {
    sessionId = readCapture();
  } catch (error) {
    workspace.error = `could not capture this run's session id: ${error.message}`;
    return workspace;
  }
  workspace.sessionId = sessionId;
  if (!shutdownConfirmed) {
    workspace.error = 'the owned headless process group is not confirmed stopped; grouping is deferred';
    return workspace;
  }
  try {
    const adopted = await adoptSession(webHost, {
      sessionId,
      workspaceId: workspaceInfo.id,
      cwd,
    }, { timeoutMs: webTimeoutMs });
    return { enabled: true, bound: true, id: adopted.id, path: adopted.path, sessionId: adopted.sessionId };
  } catch (error) {
    workspace.error = `grouping failed: ${error instanceof WebSetupError || error instanceof WebRpcError ? error.message : 'unexpected grouping failure'}`;
    return workspace;
  }
}

function buildResult(status, exitCode, signal, error, workspace, shutdownConfirmed) {
  const { text, truncated } = readTextPrefix(stdoutLog, FINAL_TEXT_LIMIT);
  return {
    status,
    mode: 'run',
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
    logPaths: { stdout: stdoutLog, stderr: stderrLog, capture: capturePath },
    finalText: text.trim().slice(0, FINAL_TEXT_LIMIT),
    finalTextTruncated: truncated,
    workspace,
    processState: { pid: child?.pid ?? null, shutdownConfirmed },
    note: 'exit 0 only means the dsh agent finished, not that the task is correct: inspect the real diff/artifacts and run the relevant checks yourself.',
  };
}

/** Print the single terminal JSON object, clean up, and exit with a matching code. */
function emitPayload(payload) {
  if (settled) return;
  settled = true;
  clearTimeout(timeoutTimer);
  clearTimeout(killTimer);
  clearTimeout(failsafeTimer);
  try {
    if (tempDir !== undefined) rmSync(tempDir, { recursive: true, force: true });
  } catch { /* best effort: never skip the result because cleanup failed */ }
  // A grouped run is only a success when the host verified the membership.
  const grouped = payload.workspace === undefined || payload.workspace.enabled !== true || payload.workspace.bound === true;
  const code = payload.status === 'ok' && grouped ? 0 : EXIT_RUN_FAILED;
  process.exitCode = code;
  process.stdout.write(`${JSON.stringify(payload)}\n`, () => process.exit(code));
  // If stdout never flushes (e.g. a broken pipe), do not hang.
  setTimeout(() => process.exit(code), 1000).unref();
}

/**
 * Settle one run exactly once. Cleanup and grouping are awaited before the
 * synchronous JSON emit, so the async grouping path can never race the
 * process exit.
 */
async function settle(status, exitCode, signal, error) {
  if (settled || settling) return;
  settling = true;
  // A task deadline must never signal an old PID during network finalization.
  clearTimeout(timeoutTimer);
  clearTimeout(killTimer);
  clearTimeout(failsafeTimer);
  let shutdownConfirmed = childExited;
  if (shutdownConfirmed) {
    const deadline = Date.now() + FAILSAFE_MS;
    while (processGroupAlive() && Date.now() < deadline) {
      await new Promise((resolveWait) => setTimeout(resolveWait, 25));
    }
    shutdownConfirmed = !processGroupAlive();
  }
  try {
    if (tempDir !== undefined) rmSync(tempDir, { recursive: true, force: true });
  } catch { /* best effort */ }
  const workspace = workspaceEnabled ? await finalizeGrouping(status, shutdownConfirmed) : baseWorkspace();
  settling = false;
  emitPayload(buildResult(status, exitCode, signal, error, workspace, shutdownConfirmed));
}

// Installed before spawning: a signal during setup exits immediately; later
// signals are forwarded to the owned process group and reported as cancelled.
// After the child has exited, finalization is bounded and is never raced.
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    if (child === undefined) process.exit(130);
    if (childExited) return;
    beginTermination('cancelled', signal);
  });
}

try {
  const childEnv = { ...process.env };
  // Preserve the same home after changing the child's working directory.
  if (childEnv.DSH_HOME?.trim()) childEnv.DSH_HOME = resolve(childEnv.DSH_HOME);
  // The launch token never enters the headless child or its logs.
  delete childEnv.DSH_WEB_URL;
  delete childEnv.DSH_WEB_URL_FILE;
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
  childExited = true;
  void settle('spawn-error', null, null, error.message);
});
child.on('close', (code, signal) => {
  childExited = true;
  if (termination !== null) {
    // The direct child is gone; SIGKILL any survivors in the owned group so no
    // grandchild outlives the run, then report the termination (never ok).
    if (processGroupAlive()) signalProcessGroup('SIGKILL');
    void settle(termination, code, signal, terminationError);
    return;
  }
  void settle(code === 0 ? 'ok' : 'nonzero', code, signal, null);
});
timeoutTimer = setTimeout(() => beginTermination('timeout', 'SIGTERM'), timeoutSeconds * 1000);
