import { EventEmitter } from 'node:events';
import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { mkdirSync, chmodSync, readFileSync, writeFileSync, renameSync, readdirSync, realpathSync, statSync, openSync, closeSync, rmSync } from 'node:fs';
import { isAbsolute, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const RUNNER = fileURLToPath(new URL('../scripts/run.mjs', import.meta.url));
const ACTIVE = new Set(['running', 'cancelling', 'completing']);
const MAX_RESULT = 1024 * 1024;
const STATUSES = new Set([...ACTIVE, 'completed', 'cancelled', 'failed', 'interrupted']);
const statSize = path => { try { return statSync(path).size; } catch { return null; } };
const error = (code, message) => Object.assign(new Error(message), { code });
const overlaps = (a, b) => {
  const inside = (x, y) => { const p = relative(x, y); return p === '' || (p !== '..' && !p.startsWith(`..${sep}`) && !isAbsolute(p)); };
  return inside(a, b) || inside(b, a);
};

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
    const { inputHash, input, result, ...publicRecord } = record;
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
    const record = { runId, requestId: params.requestId, inputHash, input: savedInput, cwd, status: 'running', revision: 0, createdAt: new Date().toISOString(), resultAvailable: false, acceptedAt: null, shutdownConfirmed: false, logPaths: { stdout: join(dir, 'runner.stdout.log'), stderr: join(dir, 'runner.stderr.log') } };
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
    if (!input.workspace) args.push('--no-workspace');
    for (const name of ['model', 'provider', 'effort']) if (input[name]) args.push(`--${name}`, input[name]);
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
    this.persistEvent(record);
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
