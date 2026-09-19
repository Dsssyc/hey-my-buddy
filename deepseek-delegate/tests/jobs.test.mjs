import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, statSync, rmSync, readdirSync, chmodSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { JobManager } from '../service/jobs.mjs';

async function fixture(t, options = {}) {
  const root = mkdtempSync(join(tmpdir(), 'buddy-jobs-'));
  const cwd = join(root, 'project');
  mkdirSync(cwd);
  const runnerPath = join(root, 'runner.mjs');
  writeFileSync(runnerPath, `
    import { readFileSync, writeFileSync } from 'node:fs';
    const file = process.argv[process.argv.indexOf('--task-file') + 1];
    const task = readFileSync(file, 'utf8');
    const finish = (status, confirmed = true) => {
      process.stdout.write(JSON.stringify({status, processState:{shutdownConfirmed:confirmed}}), () => process.exit(status === 'ok' ? 0 : 1));
    };
    process.on('SIGTERM', () => setTimeout(() => finish(task === 'cancel-ok' ? 'ok' : 'cancelled'), task === 'slow-cancel' ? 150 : 0));
    writeFileSync(file + '.ready', 'ready');
    if (['wait', 'cancel-ok', 'slow-cancel'].includes(task)) setInterval(() => {}, 1000);
    else if (task === 'preflight') process.exit(2);
    else if (task === 'unknown') finish('ok', false);
    else setTimeout(() => finish('ok'), 30);
  `);
  const config = { stateDir: join(root, 'state'), runnerPath, ...options };
  const manager = new JobManager(config);
  await manager.init();
  t.after(async () => { await manager.shutdown(); rmSync(root, { recursive: true, force: true }); });
  return { root, cwd, config, manager };
}

async function until(check) {
  const end = Date.now() + 6000;
  while (Date.now() < end) {
    const value = await check();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 15));
  }
  throw new Error('Timed out');
}

async function completed(manager, runId) {
  return until(async () => {
    const status = await manager.dispatch('status', { runId });
    return status.resultAvailable && status;
  });
}

test('idempotent start, durable completion and explicit artifact acceptance', async t => {
  const { manager, cwd, config } = await fixture(t);
  const input = { requestId: 'one', cwd, task: 'ok' };
  const [a, b] = await Promise.all([manager.dispatch('start', input), manager.dispatch('start', input)]);
  assert.equal(a.runId, b.runId);
  await assert.rejects(manager.dispatch('start', { ...input, task: 'changed' }), { code: 'CONFLICT' });
  await assert.rejects(manager.dispatch('result', { runId: a.runId }), { code: 'NOT_READY' });
  const done = await completed(manager, a.runId);
  assert.equal(done.status, 'completed');
  assert.equal(done.acceptedAt, null);
  assert.equal(done.shutdownConfirmed, true);
  assert.equal(statSync(join(config.stateDir, a.runId, 'task.txt')).mode & 0o777, 0o600);
  assert.equal(statSync(join(config.stateDir, a.runId)).mode & 0o777, 0o700);
  const fresh = new JobManager(config);
  await fresh.init();
  assert.equal((await fresh.dispatch('result', { runId: a.runId })).result.status, 'ok');
  const accepted = await fresh.dispatch('acknowledge', { runId: a.runId, note: 'Inspected artifacts' });
  assert.ok(accepted.acceptedAt);
  assert.deepEqual(await fresh.dispatch('acknowledge', { runId: a.runId, note: 'Changed note' }), accepted);
  assert.equal((await fresh.dispatch('list')).total, 1);
});

test('cancellation affects only the owned runner; ancestor directories and capacity are guarded', async t => {
  const { manager, cwd, root, config } = await fixture(t, { maxConcurrent: 2 });
  const unrelated = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
  t.after(() => unrelated.kill('SIGTERM'));
  const run = await manager.dispatch('start', { requestId: 'waiting', cwd, task: 'wait' });
  await until(() => { try { return readFileSync(join(config.stateDir, run.runId, 'task.txt.ready'), 'utf8'); } catch { return false; } });
  await assert.rejects(manager.dispatch('start', { requestId: 'ancestor', cwd: root, task: 'ok' }), { code: 'BUSY' });
  mkdirSync(join(cwd, 'nested'));
  await assert.rejects(manager.dispatch('start', { requestId: 'child', cwd: join(cwd, 'nested'), task: 'ok' }), { code: 'BUSY' });
  await manager.dispatch('cancel', { runId: run.runId });
  assert.equal((await completed(manager, run.runId)).status, 'cancelled');
  assert.equal(unrelated.exitCode, null);
  assert.equal(unrelated.kill(0), true);
  const next = await manager.dispatch('start', { requestId: 'next', cwd, task: 'ok' });
  assert.equal((await completed(manager, next.runId)).status, 'completed');
});

test('unconfirmed runner shutdown blocks starts even with an exit code of zero', async t => {
  const { manager, cwd } = await fixture(t);
  const run = await manager.dispatch('start', { requestId: 'unknown', cwd, task: 'unknown' });
  assert.equal((await completed(manager, run.runId)).status, 'failed');
  await assert.rejects(manager.dispatch('acknowledge', { runId: run.runId, note: 'reviewed' }), { code: 'SHUTDOWN_UNCONFIRMED' });
  await assert.rejects(manager.dispatch('start', { requestId: 'next', cwd, task: 'ok' }), { code: 'SHUTDOWN_UNCONFIRMED' });
});

test('preflight and spawn failures release the execution lock without claiming success', async t => {
  const { manager, cwd } = await fixture(t);
  const run = await manager.dispatch('start', { requestId: 'preflight', cwd, task: 'preflight' });
  const failed = await completed(manager, run.runId);
  assert.equal(failed.status, 'failed');
  assert.equal(failed.shutdownConfirmed, true);
  manager.env.BAD = '\0';
  const spawnFailure = await manager.dispatch('start', { requestId: 'spawn-error', cwd, task: 'ok' });
  assert.equal(spawnFailure.status, 'failed');
  assert.equal(spawnFailure.shutdownConfirmed, true);
  delete manager.env.BAD;
  const next = await manager.dispatch('start', { requestId: 'next', cwd, task: 'ok' });
  assert.equal((await completed(manager, next.runId)).status, 'completed');
});

test('cancellation wins over runner success and shutdown awaits actual process close', async t => {
  const { manager, cwd, config } = await fixture(t);
  for (const task of ['cancel-ok', 'slow-cancel']) {
    const run = await manager.dispatch('start', { requestId: task, cwd, task });
    await until(() => { try { return readFileSync(join(config.stateDir, run.runId, 'task.txt.ready'), 'utf8'); } catch { return false; } });
    if (task === 'cancel-ok') await manager.dispatch('cancel', { runId: run.runId });
    else {
      const start = Date.now();
      await manager.shutdown();
      assert.ok(Date.now() - start >= 100);
      assert.equal(manager.children.size, 0);
    }
    assert.equal((await completed(manager, run.runId)).status, 'cancelled');
  }
});

test('async persistence failure keeps service alive and prevents unpersisted result delivery', async t => {
  const { manager, cwd, config } = await fixture(t);
  const run = await manager.dispatch('start', { requestId: 'disk-error', cwd, task: 'wait' });
  await until(() => { try { return readFileSync(join(config.stateDir, run.runId, 'task.txt.ready'), 'utf8'); } catch { return false; } });
  // Make atomic replacement fail deterministically, including when tests run as root.
  const recordPath = join(config.stateDir, run.runId, 'record.json');
  rmSync(recordPath); mkdirSync(recordPath);
  await manager.dispatch('cancel', { runId: run.runId });
  await until(() => manager.children.size === 0);
  await assert.rejects(manager.dispatch('result', { runId: run.runId }), { code: 'PERSISTENCE_FAILED' });
  await assert.rejects(manager.dispatch('start', { requestId: 'another', cwd, task: 'ok' }), { code: 'PERSISTENCE_FAILED' });
});

test('initial persistence failures leave no ghost or partial run and may be retried safely', async t => {
  const { manager, cwd, config } = await fixture(t);
  const originalPersist = manager.persist;
  manager.persist = () => { throw new Error('Injected disk write failure'); };
  const input = { requestId: 'retry-initial', cwd, task: 'ok' };
  await assert.rejects(manager.dispatch('start', input), { code: 'PERSISTENCE_FAILED' });
  assert.equal(manager.records.size, 0);
  assert.equal(manager.children.size, 0);
  assert.deepEqual(readdirSync(config.stateDir), []);
  const fresh = new JobManager(config);
  await fresh.init();
  assert.equal((await fresh.dispatch('list')).total, 0);
  manager.persist = originalPersist;
  // A filesystem failure before even creating the staging directory is equally safe.
  rmSync(config.stateDir, { recursive: true });
  writeFileSync(config.stateDir, 'not a directory');
  await assert.rejects(manager.dispatch('start', input), { code: 'PERSISTENCE_FAILED' });
  assert.equal(manager.records.size, 0);
  assert.equal(manager.children.size, 0);
  rmSync(config.stateDir); mkdirSync(config.stateDir, { mode: 0o700 });
  const run = await manager.dispatch('start', input);
  assert.equal((await completed(manager, run.runId)).status, 'completed');
});

test('restart rejects structurally corrupt state with a stable error', async t => {
  const { manager, cwd, config } = await fixture(t);
  const run = await manager.dispatch('start', { requestId: 'corrupt', cwd, task: 'ok' });
  await completed(manager, run.runId);
  const path = join(config.stateDir, run.runId, 'record.json');
  const record = JSON.parse(readFileSync(path, 'utf8'));
  record.status = 'invented-state';
  writeFileSync(path, JSON.stringify(record));
  await assert.rejects(new JobManager(config).init(), { code: 'CORRUPT_STATE' });
});

test('restart recovers unfinished records without signaling or replaying a stored PID', async t => {
  const { manager, cwd, config } = await fixture(t);
  const run = await manager.dispatch('start', { requestId: 'recover', cwd, task: 'wait' });
  await until(() => { try { return readFileSync(join(config.stateDir, run.runId, 'task.txt.ready'), 'utf8'); } catch { return false; } });
  const recovered = new JobManager(config);
  await recovered.init();
  assert.equal((await recovered.dispatch('status', { runId: run.runId })).status, 'interrupted');
  await recovered.dispatch('cancel', { runId: run.runId });
  assert.equal(manager.children.get(run.runId).kill(0), true);
  await assert.rejects(recovered.dispatch('start', { requestId: 'new', cwd, task: 'ok' }), { code: 'SHUTDOWN_UNCONFIRMED' });
  await manager.shutdown();
  assert.equal(manager.children.size, 0);
});

test('distinct directories may run concurrently and service shutdown cancels both', async t => {
  const { manager, cwd, root, config } = await fixture(t, { maxConcurrent: 2 });
  const second = join(root, 'other');
  mkdirSync(second);
  const runs = await Promise.all([cwd, second].map((dir, i) => manager.dispatch('start', { requestId: String(i), cwd: dir, task: 'wait' })));
  for (const run of runs) await until(() => { try { return readFileSync(join(config.stateDir, run.runId, 'task.txt.ready'), 'utf8'); } catch { return false; } });
  const third = join(root, 'third'); mkdirSync(third);
  await assert.rejects(manager.dispatch('start', { requestId: 'third', cwd: third, task: 'ok' }), { code: 'BUSY' });
  await manager.shutdown();
  for (const run of runs) assert.equal((await manager.dispatch('status', { runId: run.runId })).status, 'cancelled');
});

test('missing runner entrypoint fails before any run record or process and cannot poison the gate', async t => {
  const { manager, cwd, root, config } = await fixture(t);
  const run = await manager.dispatch('start', { requestId: 'recoverable', cwd, task: 'ok' });
  assert.equal((await completed(manager, run.runId)).status, 'completed');

  // The installed entrypoint disappears (for example a plugin cache version is
  // replaced while the service keeps running).
  const gone = join(root, 'vanished', 'run.mjs');
  manager.runnerPath = gone;
  const before = readdirSync(config.stateDir).sort();

  // Recovery of an existing durable run still answers without a new process.
  const recovered = await manager.dispatch('start', { requestId: 'recoverable', cwd, task: 'ok' });
  assert.equal(recovered.runId, run.runId);

  // A NEW run must fail before the staging directory, the record and the child exist.
  await assert.rejects(manager.dispatch('start', { requestId: 'after-break', cwd, task: 'ok' }), { code: 'RUNNER_UNAVAILABLE' });
  assert.deepEqual(readdirSync(config.stateDir).sort(), before, 'no run directory or staging directory may be created');
  assert.equal(manager.children.size, 0);
  assert.equal(manager.persistenceFailure, null);
  const listed = await manager.dispatch('list');
  assert.equal(listed.total, 1, 'the failed start must not appear as a run');
  assert.equal(listed.runs[0].runId, run.runId);

  // Because no failed record was written, the concurrency gate stays usable once
  // the entrypoint is repaired - this is exactly what a poisoned SHUTDOWN_UNCONFIRMED
  // record used to prevent.
  manager.runnerPath = config.runnerPath;
  const repaired = await manager.dispatch('start', { requestId: 'after-repair', cwd, task: 'ok' });
  assert.equal((await completed(manager, repaired.runId)).status, 'completed');
});

test('unusable runner entrypoint variants are rejected without side effects', async t => {
  const { manager, cwd, root, config } = await fixture(t);
  const directory = join(root, 'runner-dir');
  mkdirSync(directory);
  const empty = join(root, 'empty-runner.mjs');
  writeFileSync(empty, '');
  const unreadable = join(root, 'unreadable-runner.mjs');
  writeFileSync(unreadable, 'process.exit(0);');
  chmodSync(unreadable, 0o000);

  const variants = [['directory', directory], ['empty', empty], ['unreadable', unreadable]];
  for (const [name, runnerPath] of variants) {
    await t.test(name, async () => {
      if (process.getuid?.() === 0 && name === 'unreadable') return; // root bypasses mode bits
      manager.runnerPath = runnerPath;
      const before = readdirSync(config.stateDir).sort();
      await assert.rejects(manager.dispatch('start', { requestId: `bad-${name}`, cwd, task: 'ok' }), { code: 'RUNNER_UNAVAILABLE' });
      assert.deepEqual(readdirSync(config.stateDir).sort(), before);
      assert.equal(manager.children.size, 0);
    });
  }
  manager.runnerPath = config.runnerPath;
  const run = await manager.dispatch('start', { requestId: 'healthy-again', cwd, task: 'ok' });
  assert.equal((await completed(manager, run.runId)).status, 'completed');
});
