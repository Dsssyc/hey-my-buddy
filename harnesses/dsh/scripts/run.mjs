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
 * - Every run writes its session rollout to an execution-private session root
 *   through the same per-run patch overlay; the owning harness session store,
 *   credentials and settings are never touched.
 */
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  accessSync, closeSync, constants, existsSync, lstatSync, mkdirSync, mkdtempSync,
  openSync, readFileSync, readSync, realpathSync, rmSync, statSync, writeFileSync,
} from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { delimiter, dirname, isAbsolute, join, resolve } from 'node:path';
import { performance } from 'node:perf_hooks';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { loadYaml } from './lib/yaml.mjs';
import { readTurnInput, readTurnRecord, turnOutputPath } from './lib/turn-contract.mjs';

const DSH_PROFILE = 'headless';
const DEFAULTS = Object.freeze({
  model: 'deepseek-flash',
  provider: 'deepseek-official',
  effort: 'max',
});
const DEFAULT_TIMEOUT_SECONDS = 1800;
const MIN_TIMEOUT_SECONDS = 10;
const MAX_TIMEOUT_SECONDS = 86400;
/**
 * Explicit no-deadline sentinel. The normalized Buddy spec uses
 * ``timeoutSeconds=0`` for a task the Host deliberately left unbounded, so this
 * runner must accept it and install no termination timer at all. Omission still
 * defaults to 1800 and the positive 10-86400 range is unchanged.
 */
const NO_TIMEOUT_SECONDS = 0;
/** Task text above this many bytes travels as a file reference instead of argv. */
const FILE_REFERENCE_BYTES = 32000;
/** stdout is summarized as at most this many bytes taken from the log head. */
const FINAL_TEXT_LIMIT = 6000;
const SIGKILL_GRACE_MS = 3000;
const FAILSAFE_MS = 2000;
const EXIT_USAGE = 2;
const EXIT_RUN_FAILED = 1;
/** Private per-run inquiry bridge; mounted only when the service supplies a socket. */
const INQUIRY_PLUGIN_PATH = fileURLToPath(new URL('../plugins/inquiry-bridge.mjs', import.meta.url));
/** Required only for governed turns; independent of the private session root. */
const TURN_PLUGIN_PATH = fileURLToPath(new URL('../plugins/turn-result.mjs', import.meta.url));
/** Bounded activity observer; mounted for governed turns when a sidecar path is supplied. */
const ACTIVITY_PLUGIN_PATH = fileURLToPath(new URL('../plugins/activity.mjs', import.meta.url));
/** Native usage/quota observer; mounted for governed turns when a sidecar path is supplied. */
const USAGE_PLUGIN_PATH = fileURLToPath(new URL('../plugins/usage.mjs', import.meta.url));
/** Prefix of the private bridge failure report written next to the socket. */
const INQUIRY_ERROR_SUFFIX = '.error.json';
/**
 * Conservative usable length of a Unix socket path in bytes. `sun_path` is 104
 * bytes on macOS/BSD and 108 on Linux; an over-long path can make `listen()`
 * report success without creating the socket file, so it is rejected up front.
 */
const UNIX_SOCKET_PATH_BUDGET = process.platform === 'linux' ? 105 : 101;

const USAGE = [
  'Usage: node scripts/run.mjs --cwd <dir> --task-file <file> [options]',
  '',
  'Run one bounded task through the installed dsh headless profile and print',
  'one compact JSON result on stdout. The session rollout always lands in an',
  'execution-private session root; it never joins the user session store.',
  '',
  'Required:',
  '  --cwd <dir>             working directory dsh runs in (canonicalized with',
  '                          realpath before the run)',
  '  --task-file <file>      file holding the task text',
  '',
  'Options:',
  '  --model <id>            model id (default: DSH_DELEGATE_MODEL, then the',
  '                          settings document, then deepseek-flash)',
  '  --provider <id>         provider id (default: DSH_DELEGATE_PROVIDER, then',
  '                          the settings document, then deepseek-official)',
  '  --effort <name>         reasoning effort (default: DSH_DELEGATE_EFFORT,',
  '                          then max)',
  '  --timeout <seconds>     headless time limit: 0 disables the deadline, or',
  '                          10-86400 (default 1800)',
  '  --log-dir <dir>         parent directory for this run\'s private log dir',
  '  --dsh-bin <path>        dsh launcher (default: DSH_BIN, then PATH lookup,',
  '                          then ~/.local/bin/dsh)',
  '  --settings-file <path>  settings document (default: DSH_SETTINGS_FILE,',
  '                          then $DSH_HOME/settings.yaml, then',
  '                          ~/.dsh/settings.yaml)',
  '  --session-root <dir>    private session root for THIS child only: a',
  '                          per-run patch overlay moves just the JSONL session',
  '                          backend under this attempt directory, so its',
  '                          rollout never joins the user session store.',
  '                          DSH_HOME, the credentials store and the settings',
  '                          document are never moved, so native auth keeps',
  '                          resolving from the owning harness. Defaults to a',
  '                          private "sessions" directory beside this run\'s logs',
  '  --inquiry-socket <path> private socket for this run\'s inquiry bridge, an',
  '                          absolute owner-private path supplied by the owning',
  '                          service; combined with --inquiry-token it mounts a',
  '                          per-run status/question channel for this run only',
  '  --inquiry-token <token>  per-run shared secret every bridge frame must carry',
  '  --inquiry-results <path> private append-only journal the bridge writes so a',
  '                          correlated answer stays readable after this run ends',
  '  --turn-input-file <path> private absolute JSON input for a governed turn;',
  '                          must be paired with --turn-output-file',
  '  --turn-output-file <path> absent private absolute path for the structured',
  '                          root-agent result; ordinary prose is not a result',
  '  --activity-file <path>  private absolute activity.json sidecar path: the',
  '                          bounded metadata-only projection of this turn\'s',
  '                          native events, written throttled and atomically for',
  '                          the owning Worker to forward; requires a governed turn',
  '  --usage-file <path>     private absolute native-usage.json sidecar path: the',
  '                          bounded native token-usage and quota observation of',
  '                          this governed turn plus the last native root',
  '                          assistant text, written atomically for the owning',
  '                          adapter to normalize; requires a governed turn',
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

/** `--dsh-bin` > `DSH_BIN` > PATH lookup > the native default ~/.local/bin/dsh. */
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
    document = loadYaml(text);
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
      'flat-log-dir': { type: 'boolean', default: false },
      'private-dir': { type: 'string' },
      'dsh-bin': { type: 'string' },
      'settings-file': { type: 'string' },
      'session-root': { type: 'string' },
      'inquiry-socket': { type: 'string' },
      'inquiry-token': { type: 'string' },
      'inquiry-results': { type: 'string' },
      'inquiry-error': { type: 'string' },
      'turn-input-file': { type: 'string' },
      'turn-output-file': { type: 'string' },
      'activity-file': { type: 'string' },
      'usage-file': { type: 'string' },
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
if (values.cwd.trim() === '') fail('--cwd must not be blank');
if (values['task-file'] === undefined) {
  fail(`--task-file is required\n\n${USAGE}`);
}
if (values['task-file'] !== undefined && values['task-file'].trim() === '') {
  fail('--task-file must not be blank');
}
// The inquiry bridge is optional and private to one run: both halves must be
// supplied together, and a malformed pair is a usage error before any spawn.
const inquiryRawSocket = values['inquiry-socket'] === undefined ? undefined : values['inquiry-socket'].trim();
const inquiryToken = values['inquiry-token'] === undefined ? undefined : values['inquiry-token'].trim();
const inquiryRawResults = values['inquiry-results'] === undefined ? undefined : values['inquiry-results'].trim();
const inquiryRawError = values['inquiry-error'] === undefined ? undefined : values['inquiry-error'].trim();
const inquiryParts = [inquiryRawSocket, inquiryToken, inquiryRawResults].filter(part => part !== undefined).length;
if (inquiryParts !== 0 && inquiryParts !== 3) {
  fail('--inquiry-socket, --inquiry-token and --inquiry-results must be supplied together');
}
if (inquiryRawSocket !== undefined && inquiryRawSocket === '') fail('--inquiry-socket must not be blank');
if (inquiryToken !== undefined && inquiryToken === '') fail('--inquiry-token must not be blank');
if (inquiryRawResults !== undefined && inquiryRawResults === '') fail('--inquiry-results must not be blank');
if (inquiryRawError !== undefined && (!isAbsolute(inquiryRawError) || inquiryRawError.includes('\0'))) fail('--inquiry-error must be an absolute path');
if (inquiryToken !== undefined && (inquiryToken.length > 256 || inquiryToken.includes('\0'))) {
  fail('--inquiry-token must be at most 256 characters and contain no NUL');
}
// The private session root moves ONLY the JSONL session backend's root for this
// child, through the same per-run patch overlay this runner already uses for
// its settings copy. The child's DSH home, credentials store and settings
// document are never relocated, so native model auth keeps resolving from the
// owning harness instead of a broken empty home. It requires an absolute
// owner-private path.
const rawSessionRoot = values['session-root'] === undefined ? undefined : values['session-root'].trim();
if (rawSessionRoot !== undefined) {
  if (rawSessionRoot === '') fail('--session-root must not be blank');
  if (!isAbsolute(rawSessionRoot) || rawSessionRoot.includes('\0')) {
    fail('--session-root must be an absolute path without NUL');
  }
}
let sessionRootDir = rawSessionRoot === undefined ? undefined : resolve(rawSessionRoot);
const attemptSessionRoot = sessionRootDir !== undefined;
// Validate the complete governed protocol before settings or a paid spawn. The
// input is snapshotted once; the private patch carries these exact values and
// the SHA-256 of the original input file's bytes into the plugin.
let turnConfig;
const turnInputFlag = values['turn-input-file'];
const turnOutputFlag = values['turn-output-file'];
if ((turnInputFlag === undefined) !== (turnOutputFlag === undefined)) fail('--turn-input-file and --turn-output-file must be supplied together');
if (turnInputFlag !== undefined) {
  try {
    const loaded = readTurnInput(turnInputFlag);
    turnConfig = { ...loaded, outputFile: turnOutputPath(turnOutputFlag) };
    if (!isFile(TURN_PLUGIN_PATH)) throw new Error('Buddy turn-result plugin is missing');
  } catch (error) { fail(`invalid turn protocol: ${error.message}`); }
}
// The bounded activity sidecar carries the governed attempt identity, so it is
// only meaningful with a governed turn; an invalid path fails before any spawn.
const activityRawFile = values['activity-file'] === undefined ? undefined : values['activity-file'].trim();
if (activityRawFile !== undefined) {
  if (activityRawFile === '') fail('--activity-file must not be blank');
  if (!isAbsolute(activityRawFile) || activityRawFile.includes('\0')) {
    fail('--activity-file must be an absolute path without NUL');
  }
  if (turnConfig === undefined) fail('--activity-file requires a governed turn (--turn-input-file)');
  if (!isFile(ACTIVITY_PLUGIN_PATH)) fail(`activity observer plugin is missing: ${ACTIVITY_PLUGIN_PATH}`);
}
const activityFile = activityRawFile === undefined ? undefined : resolve(activityRawFile);
// The native-usage sidecar is attempt-bound too: it carries this turn's frozen
// usage/quota observation and the retained root assistant text, so it is only
// meaningful with a governed turn and an absolute private path.
const usageRawFile = values['usage-file'] === undefined ? undefined : values['usage-file'].trim();
if (usageRawFile !== undefined) {
  if (usageRawFile === '') fail('--usage-file must not be blank');
  if (!isAbsolute(usageRawFile) || usageRawFile.includes('\0')) {
    fail('--usage-file must be an absolute path without NUL');
  }
  if (turnConfig === undefined) fail('--usage-file requires a governed turn (--turn-input-file)');
  if (!isFile(USAGE_PLUGIN_PATH)) fail(`native usage observer plugin is missing: ${USAGE_PLUGIN_PATH}`);
}
const usageFile = usageRawFile === undefined ? undefined : resolve(usageRawFile);
if (!/^\d+$/.test(values.timeout)) {
  fail(`--timeout must be an integer: 0 disables the deadline, or ${MIN_TIMEOUT_SECONDS}-${MAX_TIMEOUT_SECONDS} seconds`);
}
const timeoutSeconds = Number(values.timeout);
// 0 is the explicit no-deadline sentinel and never enters the positive range,
// which keeps rejecting 1-9, out-of-range and malformed values unchanged.
if (timeoutSeconds !== NO_TIMEOUT_SECONDS && (timeoutSeconds < MIN_TIMEOUT_SECONDS || timeoutSeconds > MAX_TIMEOUT_SECONDS)) {
  fail(`--timeout must be an integer: 0 disables the deadline, or ${MIN_TIMEOUT_SECONDS}-${MAX_TIMEOUT_SECONDS} seconds`);
}

const startedAt = performance.now();

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
// Private per-run log directory and execution-private session root.
// ---------------------------------------------------------------------------
function createLogDir() {
  try {
    if (logParent !== undefined) {
      mkdirSync(logParent, { recursive: true, mode: 0o700 });
      if (values['flat-log-dir']) {
        if (lstatSync(logParent).isSymbolicLink() || !isDirectory(logParent)) throw new Error('unsafe log directory');
        return logParent;
      }
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
// Standalone runs without an attempt root still keep sessions in this run's
// private log directory. The Buddy adapter supplies its unified attempt root.
if (sessionRootDir === undefined) sessionRootDir = join(logDir, 'sessions');

// The per-run session root must be usable before the child starts; an
// unusable path is a usage error, never a paid run whose session cannot
// persist. A symlinked root is refused so the private root stays a real
// directory this runner created or was explicitly given.
try {
  mkdirSync(sessionRootDir, { recursive: true, mode: 0o700 });
  if (lstatSync(sessionRootDir).isSymbolicLink() || !isDirectory(sessionRootDir)) {
    throw new Error('the session root is not a real directory');
  }
} catch {
  fail(`could not create the private session root: ${sessionRootDir}`);
}

// ---------------------------------------------------------------------------
// Private per-run inquiry bridge. A missing or unusable socket degrades this
// run to "no inquiry" and is reported in the result; it never fails the run and
// never changes its deadline.
// ---------------------------------------------------------------------------
let inquirySocketPath = null;
let inquiryResultsPath = null;
let inquiryErrorPath = null;
let inquiryMountError = null;
if (inquiryRawSocket !== undefined) {
  if (!isAbsolute(inquiryRawSocket)) fail('--inquiry-socket must be an absolute path');
  const resolvedSocket = resolve(inquiryRawSocket);
  if (Buffer.byteLength(resolvedSocket) > UNIX_SOCKET_PATH_BUDGET) {
    inquiryMountError = `inquiry socket path exceeds this platform's Unix socket limit (${UNIX_SOCKET_PATH_BUDGET} bytes)`;
  } else if (!isFile(INQUIRY_PLUGIN_PATH)) {
    inquiryMountError = `inquiry bridge plugin is missing: ${INQUIRY_PLUGIN_PATH}`;
  } else {
    try {
      mkdirSync(dirname(resolvedSocket), { recursive: true, mode: 0o700 });
      const parent = lstatSync(dirname(resolvedSocket));
      if (parent.isSymbolicLink() || !parent.isDirectory()) throw new Error('its parent is not a real directory');
      if (parent.uid !== process.getuid()) throw new Error('its parent is not owned by the current user');
      if ((parent.mode & 0o077) !== 0) throw new Error('its parent is not owner-private (0700)');
      inquirySocketPath = resolvedSocket;
      inquiryResultsPath = resolve(inquiryRawResults);
      inquiryErrorPath = inquiryRawError === undefined ? `${resolvedSocket}${INQUIRY_ERROR_SUFFIX}` : resolve(inquiryRawError);
    } catch (error) {
      inquiryMountError = `inquiry socket directory is unusable: ${error.message}`;
    }
  }
}

let tempDir;
let settingsCopy;
let patchFile;
try {
  const privateParent = values['private-dir'] === undefined ? tmpdir() : resolve(values['private-dir']);
  if (values['private-dir'] !== undefined && (!isAbsolute(values['private-dir']) || !isDirectory(privateParent) || lstatSync(privateParent).isSymbolicLink())) throw new Error('unsafe private directory');
  tempDir = mkdtempSync(join(privateParent, 'deepseek-delegate-settings-'));
  settingsCopy = join(tempDir, 'settings.json');
  patchFile = join(tempDir, 'patch.json');
  writeFileSync(settingsCopy, JSON.stringify(runSettings), { mode: 0o600 });
  const patchRows = [{ id: 'settings', config: { path: settingsCopy, watch: false } }];
  // The shipped profiles compose the durable session store under this entry
  // id; the overlay replaces only its `root`, so this child's session rollout
  // lands in the execution-private directory while the DSH home, credentials
  // store and every other harness path keep the owning harness values. A
  // profile that does not compose the entry only warns, never fails.
  patchRows.push({ id: 'session-persistence-jsonl', config: { root: sessionRootDir } });
  if (inquirySocketPath !== null) {
    // The per-run inquiry bridge is reachable only through a token-authenticated
    // owner-private Unix socket created for this exact run.
    patchRows.push({
      insert: [{
        id: 'deepseek-delegate-inquiry-bridge',
        name: INQUIRY_PLUGIN_PATH,
        config: {
          socketPath: inquirySocketPath,
          token: inquiryToken,
          promptSha256,
          cwd,
          errorPath: inquiryErrorPath,
          resultsPath: inquiryResultsPath,
        },
      }],
    });
  }
  if (turnConfig !== undefined) {
    patchRows.push({
      insert: [{
        id: 'deepseek-delegate-turn-result', name: TURN_PLUGIN_PATH,
        config: { input: turnConfig.input, inputSha256: turnConfig.inputSha256, outputFile: turnConfig.outputFile, promptSha256, cwd },
      }],
    });
  }
  if (activityFile !== undefined && turnConfig !== undefined) {
    // The observer writes only the frozen bounded projection for this exact
    // attempt; the Worker validates the binding before forwarding it.
    patchRows.push({
      insert: [{
        id: 'deepseek-delegate-activity', name: ACTIVITY_PLUGIN_PATH,
        config: {
          activityPath: activityFile, taskId: turnConfig.input.taskId, attemptId: turnConfig.input.attemptId,
          generation: turnConfig.input.generation, promptSha256, cwd,
        },
      }],
    });
  }
  if (usageFile !== undefined && turnConfig !== undefined) {
    // One bounded native observation for this exact attempt: token usage, the
    // harness quota when the provider exposes one, and the retained root
    // assistant text. The owning adapter validates the attempt binding.
    patchRows.push({
      insert: [{
        id: 'deepseek-delegate-usage', name: USAGE_PLUGIN_PATH,
        config: {
          usagePath: usageFile, taskId: turnConfig.input.taskId, attemptId: turnConfig.input.attemptId,
          generation: turnConfig.input.generation, promptSha256, cwd,
        },
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

function buildResult(status, exitCode, signal, error, shutdownConfirmed) {
  const { text, truncated } = readTextPrefix(stdoutLog, FINAL_TEXT_LIMIT);
  let turn;
  let turnResultError;
  if (turnConfig !== undefined) {
    try {
      turn = readTurnRecord(turnConfig.outputFile, { ...turnConfig, promptSha256 });
    } catch (failure) {
      turn = null;
      turnResultError = `required Buddy turn result is unavailable or invalid: ${failure.message}`;
    }
    if (!shutdownConfirmed) turnResultError = 'Buddy turn process group is not confirmed stopped';
    // Child status and exit code remain diagnostic facts. A well-formed file
    // cannot turn cancellation/nonzero into success; a missing or unconfirmed
    // governed result cannot inherit success from ordinary headless prose.
    if (status === 'ok' && turnResultError !== undefined) {
      status = 'turn-result-error';
      error = turnResultError;
    }
  }
  return {
    status,
    mode: 'run',
    exitCode,
    signal: signal ?? null,
    error: error ?? null,
    elapsedSeconds: Math.round((performance.now() - startedAt) / 100) / 10,
    timeoutSeconds,
    requested: { provider, model, reasoningEffort: effort },
    cwd,
    taskFile,
    dshBin,
    inputDelivery: fileBackedTask ? 'file-reference' : 'inline',
    logPaths: { stdout: stdoutLog, stderr: stderrLog },
    inquiry: {
      enabled: inquirySocketPath !== null,
      socketPath: inquirySocketPath,
      resultsPath: inquiryResultsPath,
      errorPath: inquiryErrorPath,
      error: inquiryMountError,
      note: 'a private per-run bridge that answers bounded progress queries and delivers correlated operator questions to this run only; it never extends or shortens timeoutSeconds.',
    },
    finalText: text.trim().slice(0, FINAL_TEXT_LIMIT),
    finalTextTruncated: truncated,
    nativeActivity: activityFile === undefined
      ? { enabled: false, reason: 'no --activity-file was supplied for this run' }
      : { enabled: true, sidecar: 'activity.json',
          note: 'the bounded metadata-only projection this run wrote for its owning Worker; the sidecar path stays private and only the Worker reads it' },
    nativeUsage: usageFile === undefined
      ? { enabled: false, reason: 'no --usage-file was supplied for this run' }
      : { enabled: true, sidecar: 'native-usage.json',
          source: 'dsh/session-assistant-usage',
          note: 'the bounded native token-usage observation, the provider quota when the harness exposes one, and the retained root assistant text; the adapter validates the attempt binding and normalizes the counters' },
    nativeStorage: {
      // Truthful session-storage facts. Git isolation and native storage are
      // separate dimensions. Every run moves ONLY the JSONL session backend's
      // root through the per-run patch overlay into an execution-private
      // directory; the DSH home, credentials store and settings document keep
      // their owning-harness values, so native auth is never moved or simulated.
      scope: attemptSessionRoot ? 'task-private-sessions' : 'run-private-sessions',
      sessionRootPrivate: true,
      sessionRootSource: 'a per-run patch overlay on the shipped session-persistence-jsonl root; a profile without that entry warns and keeps its own store',
      credentialsStore: 'harness-user-store',
      nativeAppVisibility: 'not-listed-in-native-app',
      resumeMode: 'reconstructed-new-session',
      note: 'this child wrote its session rollout under the execution-private root through a supported per-run patch overlay; the DSH home, credentials store and other harness state stayed with the owning harness, and Buddy continuations reconstruct a new session instead of resuming it',
    },
    processState: { pid: child?.pid ?? null, shutdownConfirmed },
    ...(turnConfig === undefined ? {} : { turn, turnResultPath: turnConfig.outputFile, turnResultError: turnResultError ?? null }),
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
    if (tempDir !== undefined && payload.processState?.shutdownConfirmed === true) rmSync(tempDir, { recursive: true, force: true });
  } catch { /* best effort: never skip the result because cleanup failed */ }
  const code = payload.status === 'ok' ? 0 : EXIT_RUN_FAILED;
  process.exitCode = code;
  process.stdout.write(`${JSON.stringify(payload)}\n`, () => process.exit(code));
  // If stdout never flushes (e.g. a broken pipe), do not hang.
  setTimeout(() => process.exit(code), 1000).unref();
}

/**
 * Settle one run exactly once. Cleanup is awaited before the synchronous JSON
 * emit, so no asynchronous path can race the process exit.
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
    const deadline = performance.now() + FAILSAFE_MS;
    while (processGroupAlive() && performance.now() < deadline) {
      await new Promise((resolveWait) => setTimeout(resolveWait, 25));
    }
    shutdownConfirmed = !processGroupAlive();
  }
  try {
    if (tempDir !== undefined && shutdownConfirmed) rmSync(tempDir, { recursive: true, force: true });
  } catch { /* best effort */ }
  settling = false;
  emitPayload(buildResult(status, exitCode, signal, error, shutdownConfirmed));
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
  // Preserve the same home after changing the child's working directory. The
  // child keeps the owning DSH home: the private session root travels through
  // the per-run patch overlay only, so native credentials and settings keep
  // resolving exactly where the owning harness put them.
  if (childEnv.DSH_HOME?.trim()) childEnv.DSH_HOME = resolve(childEnv.DSH_HOME);
  // Discard obsolete Web credentials if inherited from an older installation.
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
// `--timeout 0` installs no timer at all: an unbounded run ends only when its
// owned child exits, or when the owning service cancels it through the same
// owned-process-group path every other run uses. Positive deadlines keep their
// unchanged timer.
if (timeoutSeconds !== NO_TIMEOUT_SECONDS) {
  timeoutTimer = setTimeout(() => beginTermination('timeout', 'SIGTERM'), timeoutSeconds * 1000);
}
