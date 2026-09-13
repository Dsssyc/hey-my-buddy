import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { makeWorkspace, makeDir, writeMockDsh, testEnv, waitFor } from './support/helpers.mjs';

const script = fileURLToPath(new URL('../scripts/handoff.mjs', import.meta.url));
const read = path => JSON.parse(readFileSync(path, 'utf8'));
function fixture(t) {
  const dir = makeWorkspace('handoff-test-');
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const runDir = join(dir, 'run');
  const task = join(dir, 'task.md');
  const settings = join(dir, 'settings.yaml');
  writeFileSync(task, 'Test handoff'); writeFileSync(settings, '{}');
  const mock = writeMockDsh(makeDir(dir, 'bin'));
  const artifacts = makeDir(dir, 'artifacts');
  const env = testEnv({ MOCK_ARTIFACT_DIR: artifacts });
  const runner = ['--', '--cwd', dir, '--task-file', task, '--settings-file', settings, '--dsh-bin', mock, '--no-workspace'];
  const base = ['--run-dir', runDir, '--thread', 'owner-thread'];
  const app = [...base, '--mode', 'app', '--heartbeat-id', 'heartbeat-test'];
  const exec = (args, extra = {}) => spawnSync(process.execPath, [script, ...args], {
    env: { ...env, ...extra }, encoding: 'utf8', timeout: 25000,
  });
  const codex = join(dir, 'codex');
  const calls = join(dir, 'calls.jsonl');
  writeFileSync(codex, `#!${process.execPath}\nimport {readFileSync,appendFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
const result=JSON.parse(readFileSync(${JSON.stringify(join(runDir, 'result.json'))},'utf8'));
appendFileSync(${JSON.stringify(calls)}, JSON.stringify({args:process.argv.slice(2),runId:result.runId})+'\\n');
if(process.env.NOTIFY_ACCEPT) {
  const accepted=spawnSync(process.execPath,[${JSON.stringify(script)},'--run-dir',${JSON.stringify(runDir)},'--accept']);
  if(accepted.status!==0) process.exit(9);
}
process.exit(Number(process.env.NOTIFY_EXIT||0));\n`, { mode: 0o700 });
  const cli = [...base, '--mode', 'cli', '--codex-bin', codex, '--cli-wakeup-verified'];
  return { dir, runDir, env, runner, base, app, cli, exec, calls };
}

test('App run persists private terminal result, refuses duplicate launch, accepts once', t => {
  const s = fixture(t);
  const r = s.exec([...s.app, ...s.runner]);
  assert.equal(r.status, 0, r.stderr);
  const result = read(join(s.runDir, 'result.json'));
  assert.equal(result.success, true);
  assert.equal(result.threadId, 'owner-thread');
  assert.equal(read(join(s.runDir, 'notification.json')).status, 'heartbeat');
  assert.equal(statSync(s.runDir).mode & 0o777, 0o700);
  assert.equal(statSync(join(s.runDir, 'result.json')).mode & 0o777, 0o600);
  assert.notEqual(s.exec([...s.app, ...s.runner]).status, 0);
  assert.equal(read(join(s.runDir, 'result.json')).runId, result.runId);
  const args = ['--run-dir', s.runDir, '--accept'];
  assert.equal(s.exec(args).status, 0);
  const accepted = read(join(s.runDir, 'run.json')).acceptedAt;
  assert.equal(s.exec(args).status, 0);
  assert.equal(read(join(s.runDir, 'run.json')).acceptedAt, accepted);
});

test('missing resumption prerequisites fail before any dsh run', t => {
  const s = fixture(t);
  for (const mode of ['app', 'cli']) {
    const r = s.exec([...s.base, '--mode', mode, ...s.runner]);
    assert.equal(r.status, 2);
    assert.equal(existsSync(s.runDir), false);
  }
});

test('CLI queues only after result exists, preserves explicit remote, and does not redeliver', t => {
  const s = fixture(t);
  const r = s.exec([...s.cli, '--remote', 'unix:///verified-test.sock', ...s.runner]);
  assert.equal(r.status, 0, r.stderr);
  const call = readFileSync(s.calls, 'utf8').trim().split('\n').map(JSON.parse);
  assert.equal(call.length, 1);
  assert.deepEqual(call[0].args.slice(0, 3), ['queue', '--thread', 'owner-thread']);
  assert.deepEqual(call[0].args.slice(-2), ['--remote', 'unix:///verified-test.sock']);
  assert.equal(s.exec(['--run-dir', s.runDir, '--retry-notification']).status, 0);
  assert.equal(readFileSync(s.calls, 'utf8').trim().split('\n').length, 1);
});

test('failed callback retains task success; retry only delivers notification', t => {
  const s = fixture(t);
  assert.equal(s.exec([...s.cli, ...s.runner], { NOTIFY_EXIT: '1' }).status, 1);
  const original = readFileSync(join(s.runDir, 'result.json'), 'utf8');
  assert.equal(JSON.parse(original).success, true);
  assert.equal(read(join(s.runDir, 'notification.json')).status, 'pending');
  assert.equal(s.exec(['--run-dir', s.runDir, '--retry-notification']).status, 0);
  assert.equal(read(join(s.runDir, 'notification.json')).attempts, 4);
  assert.equal(readFileSync(join(s.runDir, 'result.json'), 'utf8'), original);
});

test('runner preflight failures are persisted and notify owner', t => {
  const s = fixture(t);
  assert.equal(s.exec([...s.cli, '--', '--cwd', '/does-not-exist']).status, 1);
  const result = read(join(s.runDir, 'result.json'));
  assert.equal(result.success, false);
  assert.equal(result.status, 'runner-error');
  assert.equal(result.exitCode, 2);
  assert.equal(read(join(s.runDir, 'notification.json')).status, 'queued');
});

test('dsh nonzero exit is reported independently of successful notification', t => {
  const s = fixture(t);
  const r = s.exec([...s.cli, ...s.runner], { MOCK_MODE: 'nonzero', MOCK_EXIT_CODE: '7' });
  assert.equal(r.status, 1, r.stderr);
  const result = read(join(s.runDir, 'result.json'));
  assert.equal(result.success, false);
  assert.equal(result.runner.exitCode, 7);
  assert.equal(read(join(s.runDir, 'notification.json')).status, 'queued');
});

test('supervisor cancellation is forwarded and leaves a terminal result', async t => {
  const s = fixture(t);
  const child = spawn(process.execPath, [script, ...s.app, ...s.runner], {
    env: { ...s.env, MOCK_MODE: 'hang' }, stdio: 'ignore',
  });
  const done = new Promise(resolve => child.on('close', resolve));
  t.after(() => child.kill('SIGKILL'));
  await waitFor(() => existsSync(join(s.runDir, 'run.json')), 'manifest');
  await waitFor(() => existsSync(join(s.dir, 'artifacts', 'argv.json')), 'dsh start');
  child.kill('SIGTERM');
  assert.equal(await done, 1);
  assert.equal(read(join(s.runDir, 'result.json')).success, false);
});

test('background supervisor survives launcher exit and finalizes after cancellation', async t => {
  const s = fixture(t);
  const r = s.exec([...s.app, '--background', ...s.runner], { MOCK_MODE: 'hang' });
  assert.equal(r.status, 0, r.stderr);
  const pid = read(join(s.runDir, 'run.json')).supervisorPid;
  t.after(() => { try { process.kill(pid, 'SIGTERM'); } catch {} });
  await waitFor(() => existsSync(join(s.dir, 'artifacts', 'pids.json')), 'background dsh');
  process.kill(pid, 0);
  process.kill(pid, 'SIGTERM');
  await waitFor(() => existsSync(join(s.runDir, 'result.json')), 'background completion');
  assert.equal(read(join(s.runDir, 'result.json')).runner.status, 'cancelled');
});

test('timeout persists failure and is still delivered', t => {
  const s = fixture(t);
  const r = s.exec([...s.cli, ...s.runner, '--timeout', '10'], { MOCK_MODE: 'hang' });
  assert.equal(r.status, 1, r.stderr);
  assert.equal(read(join(s.runDir, 'result.json')).runner.status, 'timeout');
  assert.equal(read(join(s.runDir, 'notification.json')).status, 'queued');
});

test('accepted failed-delivery run suppresses further callbacks', t => {
  const s = fixture(t);
  s.exec([...s.cli, ...s.runner], { NOTIFY_EXIT: '1' });
  assert.equal(s.exec(['--run-dir', s.runDir, '--accept']).status, 0);
  const calls = readFileSync(s.calls, 'utf8');
  assert.equal(s.exec(['--run-dir', s.runDir, '--retry-notification']).status, 0);
  assert.equal(readFileSync(s.calls, 'utf8'), calls);
});

test('immediate acceptance inside callback survives supervisor completion', t => {
  const s = fixture(t);
  assert.equal(s.exec([...s.cli, ...s.runner], { NOTIFY_ACCEPT: '1' }).status, 0);
  const record = read(join(s.runDir, 'run.json'));
  assert.equal(record.status, 'completed');
  assert.ok(record.acceptedAt);
});

test('killed runner does not claim detached dsh stopped', async t => {
  const s = fixture(t);
  const child = spawn(process.execPath, [script, ...s.cli, ...s.runner], {
    env: { ...s.env, MOCK_MODE: 'hang' }, stdio: 'ignore',
  });
  const done = new Promise(resolve => child.on('close', resolve));
  let dshPid;
  t.after(() => {
    if (dshPid) { try { process.kill(-dshPid, 'SIGKILL'); } catch {} }
    child.kill('SIGKILL');
  });
  await waitFor(() => existsSync(join(s.dir, 'artifacts', 'pids.json')), 'dsh process');
  dshPid = read(join(s.dir, 'artifacts', 'pids.json')).self;
  process.kill(read(join(s.runDir, 'run.json')).runnerPid, 'SIGKILL');
  assert.equal(await done, 1);
  process.kill(dshPid, 0);
  const result = read(join(s.runDir, 'result.json'));
  assert.equal(result.executionConfirmedStopped, false);
  assert.equal(result.success, false);
  assert.match(readFileSync(s.calls, 'utf8'), /Execution confirmed stopped: false/);
});
