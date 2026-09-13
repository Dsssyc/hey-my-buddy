#!/usr/bin/env node
// Durable handoff around run.mjs. No shell interpolation or model-auth changes.
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const usage = `Usage:
  node scripts/handoff.mjs --run-dir <new private directory> --thread <id>
    --mode app --heartbeat-id <verified automation id> [--background] -- <run.mjs arguments>
  node scripts/handoff.mjs --run-dir <new private directory> --thread <id>
    --mode cli --codex-bin <absolute path> --cli-wakeup-verified [--remote <address>]
    [--background] -- <run.mjs arguments>
  node scripts/handoff.mjs --run-dir <directory> --retry-notification
  node scripts/handoff.mjs --run-dir <directory> --accept

App: create and verify a heartbeat for this thread and known run-dir BEFORE launch.
CLI: --cli-wakeup-verified attests a real same-thread wakeup test, not just --help.
Keep this supervisor alive when ending a Codex turn. See references/handoff.md.
Result and notification status are separate; an accepted queue is not task acceptance.`;

function atomic(path, value) {
  const temp = `${path}.${randomUUID()}.tmp`;
  try {
    writeFileSync(temp, `${JSON.stringify(value, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
    renameSync(temp, path);
  } finally { rmSync(temp, { force: true }); }
}
const read = (path) => JSON.parse(readFileSync(path, 'utf8'));
const now = () => new Date().toISOString();

async function notify(dir, record) {
  if (record.mode !== 'cli') throw new Error('App handoffs are consumed by their heartbeat');
  // A separate lock serializes retries, without blocking the receiving agent's acceptance.
  const lock = join(dir, 'notification.lock');
  const fd = openSync(lock, 'wx', 0o600);
  try {
    const statePath = join(dir, 'notification.json');
    const previous = read(statePath);
    if (read(join(dir, 'run.json')).acceptedAt || previous.status === 'queued') return previous;
    const result = read(join(dir, 'result.json')); // Never wake the owner before a complete result exists.
    const state = { status: 'sending', attempts: previous.attempts + 1, updatedAt: now() };
    atomic(statePath, state);
    const args = ['queue', '--thread', record.threadId, '--message',
      `Delegation ${record.runId} runner returned. Execution confirmed stopped: ${result.executionConfirmedStopped}. ` +
      `Read ${join(dir, 'result.json')} and ${join(dir, 'run.json')}. ` +
      'If shutdown is unconfirmed, inspect existing dsh activity before editing or relaunching. ' +
      'Resume the original task and independently verify the artifacts. Do not rerun this delegation. ' +
      'Deduplicate by runId; if already accepted, do nothing.'];
    if (record.remote) args.push('--remote', record.remote);
    const outcome = await new Promise((done) => {
      let child;
      try {
        child = spawn(record.codexBin, args, { stdio: 'ignore' });
      } catch { done({ status: 'pending', reason: 'spawn-failed' }); return; }
      let timedOut = false;
      const timer = setTimeout(() => { timedOut = true; child.kill('SIGKILL'); }, 15000);
      child.once('error', () => { clearTimeout(timer); done({ status: 'pending', reason: 'spawn-failed' }); });
      child.once('close', (code) => {
        clearTimeout(timer);
        done({ status: !timedOut && code === 0 ? 'queued' : 'pending',
          reason: timedOut ? 'timeout-delivery-unknown' : null, exitCode: code });
      });
    });
    const final = { ...state, ...outcome, updatedAt: now() };
    atomic(statePath, final);
    return final;
  } finally { closeSync(fd); rmSync(lock, { force: true }); }
}

async function main() {
  const raw = process.argv.slice(2);
  const separator = raw.indexOf('--');
  const runnerArgs = separator < 0 ? [] : raw.slice(separator + 1);
  const { values: v } = parseArgs({ args: separator < 0 ? raw : raw.slice(0, separator), options: {
    help: { type: 'boolean' }, background: { type: 'boolean' }, 'run-dir': { type: 'string' }, thread: { type: 'string' },
    mode: { type: 'string' }, 'heartbeat-id': { type: 'string' }, 'codex-bin': { type: 'string' },
    remote: { type: 'string' }, 'cli-wakeup-verified': { type: 'boolean' },
    'retry-notification': { type: 'boolean' }, accept: { type: 'boolean' },
  } });
  if (v.help) { process.stdout.write(`${usage}\n`); return; }
  if (!v['run-dir']?.trim()) throw new Error('--run-dir is required');
  const dir = resolve(v['run-dir']);
  const recordPath = join(dir, 'run.json');
  if (v['retry-notification'] || v.accept) {
    if (v['retry-notification'] && v.accept) throw new Error('choose retry or accept');
    if (runnerArgs.length || Object.keys(v).some(k => !['run-dir', 'retry-notification', 'accept'].includes(k))) {
      throw new Error('retry/accept only take --run-dir');
    }
    if (v.accept) {
      read(join(dir, 'result.json'));
      // The result is the publication barrier; never retain a pre-completion manifest.
      const record = read(recordPath);
      record.acceptedAt ??= now();
      atomic(recordPath, record);
      process.stdout.write(`${JSON.stringify({ runId: record.runId, acceptedAt: record.acceptedAt })}\n`);
    } else {
      const record = read(recordPath);
      const state = await notify(dir, record);
      process.stdout.write(`${JSON.stringify(state)}\n`);
      if (state.status !== 'queued' && !read(recordPath).acceptedAt) process.exitCode = 1;
    }
    return;
  }
  if (!v.thread?.trim() || !['app', 'cli'].includes(v.mode) || !runnerArgs.length) {
    throw new Error('--thread, --mode app|cli, and run.mjs arguments after -- are required');
  }
  if (v.mode === 'app' && (!v['heartbeat-id']?.trim() || v['codex-bin'] || v.remote || v['cli-wakeup-verified'])) {
    throw new Error('app requires --heartbeat-id and does not take CLI options');
  }
  if (v.mode === 'cli' && (!v['cli-wakeup-verified'] || !v['codex-bin'] ||
      !isAbsolute(v['codex-bin']) || v['heartbeat-id'])) {
    throw new Error('cli requires --cli-wakeup-verified and absolute --codex-bin; no heartbeat id');
  }
  if (v.background) {
    if (existsSync(dir)) throw new Error('run-dir already exists; inspect it instead of launching again');
    mkdirSync(dirname(dir), { recursive: true, mode: 0o700 });
    const launchLog = `${dir}.launcher.log`;
    const log = openSync(launchLog, 'wx', 0o600);
    const options = raw.slice(0, separator).filter(arg => arg !== '--background');
    const childArgs = [...options, '--', ...runnerArgs];
    let launchError = null;
    const supervisor = spawn(process.execPath, [fileURLToPath(import.meta.url), ...childArgs], {
      detached: true, stdio: ['ignore', log, log],
    });
    closeSync(log);
    supervisor.once('error', error => { launchError = error; });
    supervisor.unref();
    const deadline = Date.now() + 5000;
    while (!existsSync(recordPath) && !launchError && supervisor.exitCode === null && Date.now() < deadline) {
      await new Promise(resolveWait => setTimeout(resolveWait, 25));
    }
    if (!existsSync(recordPath)) {
      throw new Error(`background startup unconfirmed; inspect ${launchLog} and ${dir} before retrying`);
    }
    process.stdout.write(`${JSON.stringify({ status: 'started', runDir: dir, launchLog, ...read(recordPath) })}\n`);
    return;
  }
  // Exclusive directory creation prevents accidental duplicate execution for this handoff.
  mkdirSync(dirname(dir), { recursive: true, mode: 0o700 });
  mkdirSync(dir, { mode: 0o700 });
  const record = { schemaVersion: 1, runId: randomUUID(), threadId: v.thread, mode: v.mode,
    createdAt: now(), supervisorPid: process.pid, status: 'running',
    heartbeatId: v['heartbeat-id'] ?? null, codexBin: v['codex-bin'] ?? null,
    remote: v.remote ?? null, cliWakeupVerified: v['cli-wakeup-verified'] === true,
    resultPath: join(dir, 'result.json'), acceptedAt: null };
  atomic(recordPath, record);
  atomic(join(dir, 'notification.json'), { status: v.mode === 'app' ? 'heartbeat' : 'pending', attempts: 0 });
  const stdoutPath = join(dir, 'runner.stdout.log');
  const stderrPath = join(dir, 'runner.stderr.log');
  const out = openSync(stdoutPath, 'wx', 0o600);
  const err = openSync(stderrPath, 'wx', 0o600);
  let child;
  let receivedSignal = null;
  const forward = signal => { receivedSignal = signal; child?.kill(signal); };
  const onInt = () => forward('SIGINT');
  const onTerm = () => forward('SIGTERM');
  process.on('SIGINT', onInt);
  process.on('SIGTERM', onTerm);
  const runner = fileURLToPath(new URL('./run.mjs', import.meta.url));
  if (!runnerArgs.some(arg => arg === '--log-dir' || arg.startsWith('--log-dir='))) {
    runnerArgs.push('--log-dir', join(dir, 'dsh-logs'));
  }
  const outcome = await new Promise(done => {
    child = spawn(process.execPath, [runner, ...runnerArgs], { stdio: ['ignore', out, err] });
    record.runnerPid = child.pid ?? null;
    atomic(recordPath, record);
    child.once('error', error => done({ exitCode: null, error: error.message }));
    child.once('close', (exitCode, signal) => done({ exitCode, signal }));
  });
  closeSync(out); closeSync(err);
  let payload = null;
  try {
    payload = read(stdoutPath);
    if (!payload || typeof payload !== 'object' || typeof payload.status !== 'string') payload = null;
  } catch { /* Preflight failures have stderr but no runner JSON. */ }
  const result = { schemaVersion: 1, runId: record.runId, threadId: record.threadId, completedAt: now(),
    ...outcome, signal: outcome.signal ?? receivedSignal, status: payload?.status ?? 'runner-error',
    executionConfirmedStopped: payload?.processState?.shutdownConfirmed === true,
    success: !receivedSignal && outcome.exitCode === 0 && payload?.status === 'ok' &&
      payload?.processState?.shutdownConfirmed === true,
    runner: payload, logPaths: { stdout: stdoutPath, stderr: stderrPath } };
  record.status = 'completed';
  record.completedAt = result.completedAt;
  atomic(recordPath, record);
  // Publish result last: receivers cannot acknowledge while the owner record
  // is still being finalized, which would overwrite acceptedAt in a fast callback.
  atomic(record.resultPath, result);
  // The child and workspace finalization are done before any notification is sent.
  let notification = read(join(dir, 'notification.json'));
  if (v.mode === 'cli') {
    for (let attempt = 0; attempt < 3; attempt++) {
      if (attempt) await new Promise(resolveWait => setTimeout(resolveWait, attempt * 1000));
      notification = await notify(dir, record);
      if (notification.status === 'queued' || read(recordPath).acceptedAt) break;
    }
  }
  process.removeListener('SIGINT', onInt);
  process.removeListener('SIGTERM', onTerm);
  process.stdout.write(`${JSON.stringify({ ...result, notification, runDir: dir })}\n`);
  if (!result.success || notification.status === 'pending') process.exitCode = 1;
}

main().catch(error => { process.stderr.write(`${error.message}\n`); process.exitCode = 2; });
