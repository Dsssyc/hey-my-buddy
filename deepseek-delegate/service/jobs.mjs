import { EventEmitter } from 'node:events';
import { spawn } from 'node:child_process';
import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { mkdirSync, chmodSync, lstatSync, readFileSync, writeFileSync, renameSync, readdirSync, realpathSync, statSync, openSync, closeSync, rmSync, accessSync, constants as fsConstants } from 'node:fs';
import { isAbsolute, join, relative, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

const RUNNER = fileURLToPath(new URL('../scripts/run.mjs', import.meta.url));
const ACTIVE = new Set(['running', 'cancelling', 'completing']);
const MAX_RESULT = 1024 * 1024;
/** Conservative usable length of a Unix socket path in bytes (macOS/BSD 104, Linux 108). */
const UNIX_SOCKET_PATH_BUDGET = process.platform === 'linux' ? 105 : 101;
/** Bounds for one operator inquiry; the same values bound the in-run bridge. */
const MAX_INQUIRY_ID = 128;
const MAX_QUESTION_BYTES = 4000;
const MAX_QUESTION_PREVIEW = 280;
const MAX_ANSWER_BYTES = 4000;
const MAX_INQUIRIES_PER_RUN = 32;
const MAX_JOURNAL_BYTES = 1024 * 1024;
const INQUIRY_STATES = new Set(['queued', 'claimed', 'delivered', 'answered', 'discarded', 'unavailable']);
const TERMINAL_INQUIRY_STATES = new Set(['answered', 'discarded', 'unavailable']);
const INQUIRY_ID_PATTERN = /^[A-Za-z0-9._:-]{1,128}$/;
/** The inquiry bounds, shared verbatim with the in-run bridge. */
export const INQUIRY_LIMITS = Object.freeze({
  maxQuestionBytes: MAX_QUESTION_BYTES,
  maxAnswerBytes: MAX_ANSWER_BYTES,
  maxInquiriesPerRun: MAX_INQUIRIES_PER_RUN,
  maxInquiryId: MAX_INQUIRY_ID,
});
const STATUSES = new Set([...ACTIVE, 'completed', 'cancelled', 'failed', 'interrupted']);
const statSize = path => { try { return statSync(path).size; } catch { return null; } };
const error = (code, message) => Object.assign(new Error(message), { code });
const overlaps = (a, b) => {
  const inside = (x, y) => { const p = relative(x, y); return p === '' || (p !== '..' && !p.startsWith(`..${sep}`) && !isAbsolute(p)); };
  return inside(a, b) || inside(b, a);
};

/**
 * Private endpoint paths and the authentication token for one run's inquiry
 * bridge. The socket normally lives in the run's own 0700 directory. A state
 * directory too deep for the platform's Unix socket limit falls back to a
 * short, owner-private per-run directory under the system temp root, because an
 * over-long `sun_path` can make `listen()` succeed without creating a socket.
 * The literal `/tmp` is tried before `os.tmpdir()` because the macOS per-user
 * temp root is itself long enough to overflow that limit; the fallback is only
 * used for very deep state directories, and the socket there is still 0600
 * inside a 0700 per-run directory and still token-authenticated.
 */
export function inquiryBridgePaths(stateDir, runId) {
  const candidates = [
    join(stateDir, runId),
    join('/tmp', 'hey-my-buddy-inquiry', runId),
    join(tmpdir(), 'hey-my-buddy-inquiry', runId),
  ];
  const directory = candidates.find(candidate => Buffer.byteLength(join(candidate, 'inquiry.sock')) <= UNIX_SOCKET_PATH_BUDGET)
    ?? candidates[candidates.length - 1];
  return {
    socketPath: join(directory, 'inquiry.sock'),
    resultsPath: join(directory, 'inquiry.results.jsonl'),
    errorPath: join(directory, 'inquiry.sock.error.json'),
    // Hex, never base64url: a `-`-leading value would be parsed as an option by
    // the runner's `util.parseArgs` and abort the whole run before it starts.
    token: randomBytes(32).toString('hex'),
  };
}

/** Copy one inquiry record for a client; never hands out a live reference. */
function publicInquiry(inquiry) {
  return structuredClone(inquiry);
}

/**
 * Read one inquiry that is an OWN property of the record's inquiry table.
 * `inquiries` is ordinary JSON, so `record.inquiries[id]` would otherwise
 * resolve inherited members: a valid id such as `constructor` or `toString`
 * would look like an existing inquiry and `__proto__` would hit the prototype
 * setter. Own-key access keeps every valid id a plain data entry.
 */
function ownInquiry(record, inquiryId) {
  const inquiries = record.inquiries;
  if (inquiries === null || typeof inquiries !== 'object') return undefined;
  return Object.hasOwn(inquiries, inquiryId) ? inquiries[inquiryId] : undefined;
}

/** Store one inquiry as an own data property; safe for `__proto__` and friends. */
function setOwnInquiry(record, inquiryId, inquiry) {
  record.inquiries ??= {};
  Object.defineProperty(record.inquiries, inquiryId, {
    value: inquiry,
    enumerable: true,
    writable: true,
    configurable: true,
  });
}

/** Remove one own inquiry entry, leaving inherited members untouched. */
function deleteOwnInquiry(record, inquiryId) {
  if (record.inquiries !== null && typeof record.inquiries === 'object' && Object.hasOwn(record.inquiries, inquiryId)) {
    delete record.inquiries[inquiryId];
  }
}

/** Durable jobs owned by one service. No stored PID is ever used to signal a process. */
export class JobManager extends EventEmitter {
  constructor({ stateDir, runnerPath = RUNNER, maxConcurrent = 1, env = process.env }) {
    super();
    if (!isAbsolute(stateDir ?? '') || !Number.isInteger(maxConcurrent) || maxConcurrent < 1) throw error('INVALID_ARGUMENT', 'An absolute stateDir and positive maxConcurrent are required');
    this.stateDir = stateDir;
    this.runnerPath = runnerPath;
    this.maxConcurrent = maxConcurrent;
    this.env = { ...env };
    this.records = new Map();
    this.children = new Map();
    this.tail = Promise.resolve();
    this.initialized = false;
    this.closing = false;
    this.persistenceFailure = null;
  }

  async init() {
    if (this.initialized) return;
    mkdirSync(this.stateDir, { recursive: true, mode: 0o700 });
    chmodSync(this.stateDir, 0o700);
    for (const entry of readdirSync(this.stateDir, { withFileTypes: true })) {
      if (!entry.isDirectory() || !/^[0-9a-f-]{36}$/.test(entry.name)) continue;
      let record;
      try { record = JSON.parse(readFileSync(join(this.stateDir, entry.name, 'record.json'), 'utf8')); }
      catch { throw error('CORRUPT_STATE', `Cannot read run record ${entry.name}`); }
      if (!record || record.runId !== entry.name || !STATUSES.has(record.status)
        || typeof record.cwd !== 'string' || !isAbsolute(record.cwd)
        || typeof record.requestId !== 'string' || !record.requestId.trim() || record.requestId.length > 128
        || typeof record.inputHash !== 'string' || !/^[0-9a-f]{64}$/.test(record.inputHash)
        || !Number.isInteger(record.revision) || record.revision < 1
        || typeof record.createdAt !== 'string' || !Number.isFinite(Date.parse(record.createdAt))
        || typeof record.updatedAt !== 'string' || !Number.isFinite(Date.parse(record.updatedAt))
        || typeof record.resultAvailable !== 'boolean' || typeof record.shutdownConfirmed !== 'boolean'
        || (record.resultAvailable && (!record.result || typeof record.result !== 'object' || Array.isArray(record.result)))
        || [...this.records.values()].some(r => r.requestId === record.requestId)) {
        throw error('CORRUPT_STATE', `Invalid run record ${entry.name}`);
      }
      this.records.set(record.runId, record);
      if (ACTIVE.has(record.status)) {
        record.status = 'interrupted';
        record.shutdownConfirmed = false;
        record.resultAvailable = false;
        record.error = 'Service stopped before runner shutdown was confirmed; inspect surviving processes before manual recovery';
        this.persist(record);
      }
    }
    this.initialized = true;
  }

  persist(record, directory = join(this.stateDir, record.runId)) {
    record.updatedAt = new Date().toISOString();
    record.revision = (record.revision ?? 0) + 1;
    const dir = directory;
    const temp = join(dir, `.record-${randomUUID()}.tmp`);
    writeFileSync(temp, JSON.stringify(record), { mode: 0o600, flag: 'wx' });
    renameSync(temp, join(dir, 'record.json'));
    this.emit('change', record.runId);
  }

  view(record) {
    const { inputHash, input, result, inquiryBridge, ...publicRecord } = record;
    if (inquiryBridge !== undefined) {
      // The per-run bridge token is never echoed to a client; the paths are
      // owner-private bookkeeping and are safe to report.
      publicRecord.inquiryBridge = {
        enabled: inquiryBridge.enabled === true,
        socketPath: inquiryBridge.socketPath ?? null,
        resultsPath: inquiryBridge.resultsPath ?? null,
        errorPath: inquiryBridge.errorPath ?? null,
      };
    }
    return structuredClone(publicRecord);
  }

  persistEvent(record) {
    try { this.persist(record); } catch (cause) {
      this.persistenceFailure = { runId: record.runId, message: cause.message };
      record.persistenceError = cause.message;
      record.resultAvailable = false;
    }
  }

  get(params) {
    const record = this.records.get(params.runId);
    if (!record) throw error('NOT_FOUND', 'Unknown runId');
    return record;
  }

  async dispatch(method, params = {}) {
    const action = this.tail.then(() => {
      if (!this.initialized) throw error('NOT_INITIALIZED', 'Call init first');
      if (!params || typeof params !== 'object' || Array.isArray(params)) throw error('INVALID_ARGUMENT', 'Expected an object');
      switch (method) {
        case 'start': return this.start(params);
        case 'status': return this.view(this.get(params));
        case 'result': {
          const record = this.get(params);
          if (record.persistenceError) throw error('PERSISTENCE_FAILED', 'Run result could not be persisted');
          if (!record.resultAvailable) throw error('NOT_READY', 'Runner result is not available');
          return { ...this.view(record), result: structuredClone(record.result) };
        }
        case 'list': {
          const { limit = 20, offset = 0 } = params;
          if (!Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(offset) || offset < 0) throw error('INVALID_ARGUMENT', 'Invalid pagination');
          return { runs: [...this.records.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt)).slice(offset, offset + limit).map(r => this.view(r)), total: this.records.size };
        }
        case 'cancel': return this.cancel(this.get(params));
        case 'inquiry-begin': return this.inquiryBegin(params);
        case 'inquiry-update': return this.inquiryUpdate(params);
        case 'acknowledge': {
          const record = this.get(params);
          if (typeof params.note !== 'string' || !params.note.trim() || Buffer.byteLength(params.note) > 10000) throw error('INVALID_ARGUMENT', 'A nonempty note up to 10000 bytes is required');
          if (!record.resultAvailable) throw error('NOT_READY', 'Inspect the completed result before acknowledging');
          if (!record.shutdownConfirmed) throw error('SHUTDOWN_UNCONFIRMED', 'Runner shutdown must be confirmed before acknowledgement');
          if (record.acceptedAt) return this.view(record);
          record.acceptedAt = new Date().toISOString();
          record.acceptanceNote = params.note;
          try { this.persist(record); } catch (cause) {
            record.acceptedAt = null;
            delete record.acceptanceNote;
            throw error('PERSISTENCE_FAILED', 'Acknowledgement could not be persisted');
          }
          return this.view(record);
        }
        default: throw error('METHOD_NOT_FOUND', `Unknown method: ${method}`);
      }
    });
    this.tail = action.catch(() => {});
    return action;
  }

  /**
   * Fail a NEW run loudly when the runner entrypoint cannot be executed.
   *
   * A missing entrypoint makes Node exit 1 with an empty stdout, which the normal
   * finish() path cannot tell apart from a crashed runner: the run is recorded as
   * failed with shutdownConfirmed=false, and that record then blocks every later
   * start through the SHUTDOWN_UNCONFIRMED gate. This check runs before the
   * staging directory, the record and the child process exist, so an unusable
   * install (for example a plugin cache version replaced while the service kept
   * running) fails as RUNNER_UNAVAILABLE and leaves no run history behind.
   */
  assertRunnerUsable() {
    const path = this.runnerPath;
    let info;
    try { info = statSync(path); }
    catch { throw error('RUNNER_UNAVAILABLE', `The runner entrypoint is missing: ${path}. No run was created. Reinstall or repair the plugin, then restart the Buddy service.`); }
    if (!info.isFile()) throw error('RUNNER_UNAVAILABLE', `The runner entrypoint is not a regular file: ${path}. No run was created. Reinstall or repair the plugin, then restart the Buddy service.`);
    if (info.size === 0) throw error('RUNNER_UNAVAILABLE', `The runner entrypoint is empty: ${path}. No run was created. Reinstall or repair the plugin, then restart the Buddy service.`);
    try { accessSync(path, fsConstants.R_OK); }
    catch { throw error('RUNNER_UNAVAILABLE', `The runner entrypoint is not readable: ${path}. No run was created. Reinstall or repair the plugin, then restart the Buddy service.`); }
  }

  start(params) {
    const allowed = new Set(['requestId', 'task', 'cwd', 'model', 'provider', 'effort', 'timeoutSeconds', 'workspace']);
    if (Object.keys(params).some(key => !allowed.has(key))) throw error('INVALID_ARGUMENT', 'Unknown start parameter');
    if (typeof params.requestId !== 'string' || !params.requestId.trim() || params.requestId.length > 128) throw error('INVALID_ARGUMENT', 'requestId must contain 1–128 characters');
    if (typeof params.task !== 'string' || !params.task.trim() || Buffer.byteLength(params.task) > 1024 * 1024) throw error('INVALID_ARGUMENT', 'task must contain 1–1048576 bytes');
    if (typeof params.cwd !== 'string' || !isAbsolute(params.cwd)) throw error('INVALID_ARGUMENT', 'cwd must be absolute');
    let cwd;
    try { cwd = realpathSync(params.cwd); if (!statSync(cwd).isDirectory()) throw new Error(); } catch { throw error('INVALID_ARGUMENT', 'cwd must be an existing directory'); }
    const input = { cwd, task: params.task, timeoutSeconds: params.timeoutSeconds ?? 1800, workspace: params.workspace ?? true };
    if (!Number.isInteger(input.timeoutSeconds) || input.timeoutSeconds < 10 || input.timeoutSeconds > 86400 || typeof input.workspace !== 'boolean') throw error('INVALID_ARGUMENT', 'Invalid timeoutSeconds or workspace');
    for (const name of ['model', 'provider', 'effort']) {
      if (params[name] !== undefined) {
        if (typeof params[name] !== 'string' || !params[name].trim() || params[name].length > 256 || params[name].includes('\0')) throw error('INVALID_ARGUMENT', `Invalid ${name}`);
        input[name] = params[name].trim();
      }
    }
    const inputHash = createHash('sha256').update(JSON.stringify(input)).digest('hex');
    const existing = [...this.records.values()].find(r => r.requestId === params.requestId);
    if (existing) {
      if (existing.inputHash !== inputHash) throw error('CONFLICT', 'requestId already belongs to a different input');
      return this.view(existing);
    }
    // Idempotent recovery of an existing run stays available even on a broken
    // install; only work that would create a NEW run and process needs the runner.
    this.assertRunnerUsable();
    if (this.closing) throw error('SHUTTING_DOWN', 'Service is shutting down');
    if (this.persistenceFailure) throw error('PERSISTENCE_FAILED', 'A run transition could not be persisted; manual recovery is required');
    if ([...this.records.values()].some(r => !ACTIVE.has(r.status) && r.shutdownConfirmed === false)) throw error('SHUTDOWN_UNCONFIRMED', 'A prior run has unconfirmed shutdown; manual recovery is required');
    const running = [...this.records.values()].filter(r => ACTIVE.has(r.status));
    if (running.length >= this.maxConcurrent || running.some(r => overlaps(r.cwd, cwd))) throw error('BUSY', 'Concurrency limit or overlapping working directory is active');
    const runId = randomUUID();
    const dir = join(this.stateDir, runId);
    const stagingDir = join(this.stateDir, `.pending-${runId}`);
    const taskFile = join(dir, 'task.txt');
    const { task, ...savedInput } = input;
    const bridge = inquiryBridgePaths(this.stateDir, runId);
    const record = {
      runId,
      requestId: params.requestId,
      inputHash,
      input: savedInput,
      // Publicly readable copy of the runner deadline: `view` strips `input`.
      timeoutSeconds: input.timeoutSeconds,
      cwd,
      status: 'running',
      revision: 0,
      createdAt: new Date().toISOString(),
      resultAvailable: false,
      acceptedAt: null,
      shutdownConfirmed: false,
      logPaths: { stdout: join(dir, 'runner.stdout.log'), stderr: join(dir, 'runner.stderr.log') },
      inquiryBridge: { enabled: true, socketPath: bridge.socketPath, resultsPath: bridge.resultsPath, errorPath: bridge.errorPath, token: bridge.token },
      inquiries: {},
    };
    // No runner exists until the private task and initial record are published together.
    // A crash before rename leaves only an ignored .pending directory, never a ghost run.
    let stagingCreated = false;
    try {
      mkdirSync(stagingDir, { mode: 0o700 });
      stagingCreated = true;
      writeFileSync(join(stagingDir, 'task.txt'), input.task, { mode: 0o600, flag: 'wx' });
      this.records.set(runId, record);
      this.persist(record, stagingDir);
      renameSync(stagingDir, dir);
    } catch (cause) {
      this.records.delete(runId);
      try { if (stagingCreated) rmSync(stagingDir, { recursive: true, force: true }); } catch { /* No process was spawned; leftover staging directories are ignored. */ }
      throw error('PERSISTENCE_FAILED', 'Could not persist the initial task and run record');
    }
    const args = [this.runnerPath, '--cwd', cwd, '--task-file', taskFile, '--timeout', String(input.timeoutSeconds), '--log-dir', dir];
    // `--flag=value` keeps every inquiry value opaque to argument parsing, so a
    // path or token can never be mistaken for the next option.
    args.push(
      `--inquiry-socket=${bridge.socketPath}`,
      `--inquiry-token=${bridge.token}`,
      `--inquiry-results=${bridge.resultsPath}`,
    );
    if (!input.workspace) args.push('--no-workspace');
    // Same reason as the inquiry values: an operator-supplied route must never be
    // re-read as an option by the runner's argument parser.
    for (const name of ['model', 'provider', 'effort']) if (input[name]) args.push(`--${name}=${input[name]}`);
    let stdout, stderr;
    try {
      stdout = openSync(record.logPaths.stdout, 'wx', 0o600);
      stderr = openSync(record.logPaths.stderr, 'wx', 0o600);
      const child = spawn(process.execPath, args, { cwd, env: this.env, stdio: ['ignore', stdout, stderr] });
      this.children.set(runId, child);
      child.once('error', err => { record.error = err.message; record.spawnFailed = child.pid === undefined; });
      child.once('exit', () => { record.status = 'completing'; this.persistEvent(record); });
      child.once('close', (code, signal) => this.finish(record, code, signal));
    } catch (err) {
      record.error = err.message;
      record.spawnFailed = true;
      this.finish(record, null, null);
    } finally {
      if (stdout !== undefined) closeSync(stdout);
      if (stderr !== undefined) closeSync(stderr);
    }
    return this.view(record);
  }

  finish(record, exitCode, signal) {
    this.children.delete(record.runId);
    let outcome;
    try {
      if (statSync(record.logPaths.stdout).size > MAX_RESULT) throw new Error('Runner result exceeds limit');
      outcome = JSON.parse(readFileSync(record.logPaths.stdout, 'utf8'));
      if (!outcome || typeof outcome !== 'object' || Array.isArray(outcome)) throw new Error('Runner result must be an object');
    } catch (err) { outcome = { status: 'invalid-result', error: err.message }; }
    // run.mjs reserves exit 2 for preflight/usage failures before spawning DSH.
    const preflightFailed = exitCode === 2 && statSize(record.logPaths.stdout) === 0;
    record.shutdownConfirmed = outcome.processState?.shutdownConfirmed === true || record.spawnFailed === true || preflightFailed;
    record.status = !record.cancelRequestedAt && exitCode === 0 && outcome.status === 'ok' && record.shutdownConfirmed ? 'completed' : (record.cancelRequestedAt && record.shutdownConfirmed ? 'cancelled' : 'failed');
    record.exitCode = exitCode;
    record.signal = signal;
    record.result = outcome;
    record.resultAvailable = !record.persistenceError;
    if (record.shutdownConfirmed) this.cleanupInquiryBridge(record);
    this.persistEvent(record);
  }

  /**
   * Remove this run's private bridge endpoint once the owned process group is
   * confirmed stopped. The bridge removes its own socket on graceful disposal;
   * this only cleans up after a SIGKILL, and never touches a path this service
   * did not create for this run.
   */
  cleanupInquiryBridge(record) {
    const bridge = record.inquiryBridge;
    if (bridge === undefined || bridge.enabled !== true) return;
    for (const path of [bridge.socketPath, bridge.errorPath]) {
      try {
        if (typeof path !== 'string' || !path) continue;
        const info = lstatSync(path);
        if (path === bridge.socketPath ? !info.isSocket() : !info.isFile()) continue;
        if (info.uid !== process.getuid()) continue;
        rmSync(path, { force: true });
      } catch { /* best effort: the private run directory keeps the leftover harmless */ }
    }
  }

  /**
   * Validate and durably record one operator inquiry before anything is injected
   * into the run. Idempotent by `inquiryId`: a repeat with the identical
   * question returns the existing record, and different text is a CONFLICT.
   */
  inquiryBegin(params) {
    const record = this.get(params);
    const { inquiryId, question } = params;
    if (typeof inquiryId !== 'string' || !INQUIRY_ID_PATTERN.test(inquiryId)) {
      throw error('INVALID_ARGUMENT', 'inquiryId must match [A-Za-z0-9._:-]{1,128}');
    }
    if (typeof question !== 'string' || question.trim() === '') throw error('INVALID_ARGUMENT', 'question must be a nonempty string');
    const questionBytes = Buffer.byteLength(question, 'utf8');
    if (questionBytes > MAX_QUESTION_BYTES) throw error('INVALID_ARGUMENT', `question must be at most ${MAX_QUESTION_BYTES} bytes`);
    const questionSha256 = createHash('sha256').update(question, 'utf8').digest('hex');
    record.inquiries ??= {};
    const known = ownInquiry(record, inquiryId);
    if (known !== undefined) {
      if (known.questionSha256 !== questionSha256) throw error('CONFLICT', 'inquiryId already belongs to a different question');
      return { inquiry: publicInquiry(known), duplicate: true };
    }
    if (Object.keys(record.inquiries).length >= MAX_INQUIRIES_PER_RUN) {
      throw error('TOO_MANY_INQUIRIES', `At most ${MAX_INQUIRIES_PER_RUN} inquiries are retained per run`);
    }
    const now = new Date().toISOString();
    const inquiry = {
      inquiryId,
      state: 'queued',
      questionSha256,
      questionBytes,
      questionPreview: question.slice(0, MAX_QUESTION_PREVIEW),
      submittedAt: now,
      updatedAt: now,
      attempts: 0,
      reason: null,
      delivery: null,
      answer: null,
    };
    setOwnInquiry(record, inquiryId, inquiry);
    try {
      this.persist(record);
    } catch {
      deleteOwnInquiry(record, inquiryId);
      throw error('PERSISTENCE_FAILED', 'The inquiry could not be persisted; nothing was injected into the run');
    }
    return { inquiry: publicInquiry(inquiry), duplicate: false };
  }

  /**
   * Apply one bounded, validated delivery/answer update produced by the live
   * bridge. The patch is applied to a CLONE and every field is validated before
   * the record is touched, so a malformed or oversized update can never leave a
   * half-mutated inquiry (for example `answered` with a null answer) behind. An
   * existing answer is terminal: it is never cleared, downgraded, or replaced.
   */
  inquiryUpdate(params) {
    const record = this.get(params);
    const inquiry = ownInquiry(record, params.inquiryId);
    if (inquiry === undefined) throw error('NOT_FOUND', 'Unknown inquiryId');
    const patch = params.patch;
    if (patch === null || typeof patch !== 'object' || Array.isArray(patch)) throw error('INVALID_ARGUMENT', 'patch must be an object');
    const candidate = structuredClone(inquiry);
    const answered = candidate.answer !== null && candidate.answer !== undefined;
    if (patch.state !== undefined) {
      if (!INQUIRY_STATES.has(patch.state)) throw error('INVALID_ARGUMENT', 'Invalid inquiry state');
      // `answered` is terminal because an answer is final; `discarded` and
      // `unavailable` are terminal because nothing can revive a question whose
      // delivery window is gone. No later observation may downgrade them.
      if (!answered && !TERMINAL_INQUIRY_STATES.has(candidate.state)) candidate.state = patch.state;
    }
    if (patch.reason !== undefined) {
      candidate.reason = patch.reason === null ? null : String(patch.reason).slice(0, 200);
    }
    if (patch.delivery !== undefined) {
      const delivery = patch.delivery;
      if (delivery === null) candidate.delivery = null;
      else {
        if (typeof delivery !== 'object' || Array.isArray(delivery)) throw error('INVALID_ARGUMENT', 'delivery must be an object');
        // Merge, so a later observation never erases an earlier claim/delivery time.
        const bounded = candidate.delivery !== null && typeof candidate.delivery === 'object' && !Array.isArray(candidate.delivery)
          ? { ...candidate.delivery }
          : {};
        for (const key of ['messageId', 'agentStatusAtInject', 'injectedAt', 'insertedAt', 'claimedAt', 'deliveredAt', 'observedAt', 'observedAgentStatus']) {
          const value = delivery[key];
          if (value === undefined || value === null) continue;
          bounded[key] = String(value).slice(0, 200);
        }
        candidate.delivery = bounded;
      }
    }
    if (patch.answer !== undefined) {
      const answer = patch.answer;
      if (answer === null) {
        // A null never erases an answer that was already recorded.
        if (!answered) candidate.answer = null;
      } else {
        if (typeof answer !== 'object' || Array.isArray(answer) || typeof answer.text !== 'string' || answer.text.trim() === '') {
          throw error('INVALID_ARGUMENT', 'answer must carry nonblank text');
        }
        const bytes = Buffer.byteLength(answer.text, 'utf8');
        if (bytes > MAX_ANSWER_BYTES) throw error('INVALID_ARGUMENT', 'answer exceeds the size limit');
        // First correlated answer wins: a later answer never overwrites one.
        if (!answered) {
          candidate.answer = {
            text: answer.text,
            bytes,
            via: typeof answer.via === 'string' ? answer.via.slice(0, 120) : null,
            toolCallId: typeof answer.toolCallId === 'string' ? answer.toolCallId.slice(0, 200) : null,
            at: typeof answer.at === 'string' ? answer.at.slice(0, 40) : null,
            truncated: answer.truncated === true,
            source: typeof answer.source === 'string' ? answer.source.slice(0, 40) : null,
          };
          candidate.state = 'answered';
        }
      }
    }
    // An `answered` candidate must actually carry a nonblank answer: a
    // state-only patch (or a null/blank answer) must never commit a
    // half-answered record. This is checked on the clone before anything is
    // written, so a rejected patch leaves the durable inquiry untouched.
    if (candidate.state === 'answered' && (typeof candidate.answer?.text !== 'string' || candidate.answer.text.trim() === '')) {
      throw error('INVALID_ARGUMENT', 'answered requires a nonblank answer');
    }
    candidate.attempts = (Number.isInteger(candidate.attempts) ? candidate.attempts : 0) + 1;
    candidate.updatedAt = new Date().toISOString();
    // Commit only after every field validated; the previous entry is restored if
    // persistence fails, exactly like `inquiryBegin`.
    setOwnInquiry(record, params.inquiryId, candidate);
    try {
      this.persist(record);
    } catch {
      setOwnInquiry(record, params.inquiryId, inquiry);
      throw error('PERSISTENCE_FAILED', 'The inquiry update could not be persisted');
    }
    return { inquiry: publicInquiry(candidate) };
  }

  /**
   * In-process only: the private bridge credentials for one owned run. Never
   * reachable through `dispatch`, so the token is never sent to a CLI client.
   */
  bridgeCredentials(runId) {
    const record = this.get({ runId });
    const bridge = record.inquiryBridge;
    if (bridge === undefined || bridge.enabled !== true) return null;
    return { socketPath: bridge.socketPath, resultsPath: bridge.resultsPath, errorPath: bridge.errorPath, token: bridge.token };
  }

  cancel(record) {
    const child = this.children.get(record.runId);
    if (child && record.status === 'running') {
      record.cancelRequestedAt = new Date().toISOString();
      record.status = 'cancelling';
      this.persistEvent(record);
      child.kill('SIGTERM');
    }
    return this.view(record);
  }

  async shutdown() {
    this.closing = true;
    await this.tail;
    const pending = [...this.children].map(([id, child]) => new Promise(resolve => {
      child.once('close', resolve);
      this.cancel(this.records.get(id));
    }));
    await Promise.all(pending);
  }
}
