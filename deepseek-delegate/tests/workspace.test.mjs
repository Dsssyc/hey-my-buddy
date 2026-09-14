import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:net';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, chmodSync, rmSync, writeFileSync, existsSync, realpathSync } from 'node:fs';
import { join } from 'node:path';
import { CLI_PATH, testEnv, writeMockDsh } from './support/helpers.mjs';
import { resolveWorkspaceTarget, connectWorkspaceHost } from '../scripts/lib/workspace-host.mjs';

async function fixture(t) {
  const dir = realpathSync(mkdtempSync('/tmp/dgw-'));
  const socketPath = join(dir, 'workspace.sock');
  const task = join(dir, 'task.md');
  const settings = join(dir, 'settings.json');
  const artifacts = join(dir, 'artifacts');
  writeFileSync(task, 'Do this bounded task.');
  writeFileSync(settings, '{}');
  mkdirSync(artifacts);
  const mock = writeMockDsh(join(dir, 'bin'));
  const requests = [];
  const state = { failAttach: false, wrongIdentity: false };
  const server = createServer(socket => {
    let buffer = '';
    socket.on('error', () => {});
    socket.on('data', data => {
      buffer += data;
      if (!buffer.includes('\n')) return;
      const request = JSON.parse(buffer.split('\n')[0]);
      requests.push(request);
      let value;
      if (request.method === 'ping') value = { ready: true };
      if (request.method === 'resolve') value = { id: 'workspace-1', path: dir };
      if (request.method === 'attach') {
        // CLI must not attach until the model process has stopped.
        if (request.sessionId === 'session-mock-0001') assert.equal(existsSync(join(dir, 'exit-marker')), true);
        value = { bound: true, id: 'workspace-1', path: dir, sessionId: state.wrongIdentity ? 'wrong' : request.sessionId };
      }
      socket.end(JSON.stringify({ version: 1, id: request.id, ok: !(state.failAttach && request.method === 'attach'), value, error: 'unknown-session' }) + '\n');
    });
  });
  await new Promise((ok, no) => { server.once('error', no); server.listen(socketPath, ok); });
  chmodSync(socketPath, 0o600);
  t.after(async () => { await new Promise(ok => server.close(ok)); rmSync(dir, { recursive: true, force: true }); });
  async function run(extra = [], overrides = {}, attach = false) {
    const args = ['--cwd', dir, '--workspace-socket', socketPath];
    if (!attach) args.push('--task-file', task, '--settings-file', settings, '--dsh-bin', mock);
    args.push(...extra);
    return await new Promise((ok, no) => {
      const child = spawn(process.execPath, [CLI_PATH, ...args], { env: testEnv({ MOCK_ARTIFACT_DIR: artifacts, MOCK_EXIT_MARKER: join(dir, 'exit-marker'), ...overrides }) });
      let stdout = '', stderr = '';
      child.stdout.on('data', data => { stdout += data; });
      child.stderr.on('data', data => { stderr += data; });
      child.on('error', no);
      child.on('close', code => ok({ code, stderr, stdout, payload: stdout ? JSON.parse(stdout) : null }));
    });
  }
  return { dir, socketPath, requests, state, artifacts, run };
}

test('grouped run needs no web URL; verifies exact session after headless exit', async t => {
  const f = await fixture(t);
  const r = await f.run([], { DSH_WEB_URL: 'deliberately-invalid-obsolete-value' });
  assert.equal(r.code, 0, r.stderr);
  assert.equal(r.payload.workspace.bound, true);
  assert.equal(r.payload.workspace.sessionId, 'session-mock-0001');
  assert.deepEqual(f.requests.map(r => r.method), ['ping', 'resolve', 'attach']);
  assert.equal(r.stdout.includes('deliberately-invalid'), false);
});

test('binding failure preserves task status and can be retried without a model', async t => {
  const f = await fixture(t);
  f.state.failAttach = true;
  const r = await f.run();
  assert.equal(r.code, 1);
  assert.equal(r.payload.status, 'ok');
  assert.equal(r.payload.workspace.bound, false);
  assert.match(r.payload.finalText, /mock dsh completed/);
  f.state.failAttach = false;
  rmSync(join(f.artifacts, 'argv.json'));
  const retry = await f.run(['--attach-session', 'session-mock-0001'], {}, true);
  assert.equal(retry.code, 0, retry.stderr);
  assert.equal(retry.payload.mode, 'attach');
  assert.equal(existsSync(join(f.artifacts, 'argv.json')), false);
});

test('missing bridge fails before model launch', async t => {
  const f = await fixture(t);
  const r = await f.run(['--workspace-socket', join(f.dir, 'absent.sock')]);
  assert.equal(r.code, 2);
  assert.equal(existsSync(join(f.artifacts, 'argv.json')), false);
});

test('missing or mismatched capture never sends attach', async t => {
  const f = await fixture(t);
  for (const env of [{ MOCK_CAPTURE: 'off' }, { MOCK_CAPTURE_PROMPT_SHA256: '0'.repeat(64) }]) {
    const r = await f.run([], env);
    assert.equal(r.code, 1);
    assert.equal(r.payload.workspace.bound, false);
  }
  assert.equal(f.requests.some(r => r.method === 'attach'), false);
});

test('incorrect binding identity is never accepted', async t => {
  const f = await fixture(t);
  f.state.wrongIdentity = true;
  const r = await f.run();
  assert.equal(r.code, 1);
  assert.equal(r.payload.workspace.bound, false);
});

test('headless failure is preserved even when binding succeeds', async t => {
  const f = await fixture(t);
  const r = await f.run([], { MOCK_MODE: 'nonzero' });
  assert.equal(r.code, 1);
  assert.notEqual(r.payload.status, 'ok');
  assert.equal(r.payload.workspace.bound, true);
});

test('obsolete URL flags and invalid workspace timeout are rejected', async t => {
  const f = await fixture(t);
  for (const args of [['--dsh-web-url', 'unused'], ['--dsh-web-url-file', 'unused'], ['--workspace-timeout', '0']]) {
    const r = await f.run(args);
    assert.equal(r.code, 2);
  }
  assert.equal(existsSync(join(f.artifacts, 'argv.json')), false);
});

test('private socket permissions and same-path reconnection', async t => {
  const f = await fixture(t);
  await connectWorkspaceHost({ socketPath: f.socketPath });
  await connectWorkspaceHost({ socketPath: f.socketPath });
  chmodSync(f.socketPath, 0o666);
  await assert.rejects(connectWorkspaceHost({ socketPath: f.socketPath }), /private/);
});

test('socket resolution never reads obsolete web credentials', () => {
  assert.equal(resolveWorkspaceTarget({}, { DSH_HOME: '/tmp/home', DSH_WEB_URL: 'bad' }).socketPath, '/tmp/home/deepseek-delegate/workspace.sock');
  assert.equal(resolveWorkspaceTarget({ 'workspace-socket': '/tmp/a' }, { DSH_WORKSPACE_SOCKET: '/tmp/b' }).socketPath, '/tmp/a');
  assert.throws(() => resolveWorkspaceTarget({}, { DSH_WORKSPACE_SOCKET: ' ' }), /blank/);
});

test('timeout remains timeout after a child exits zero on termination', async t => {
  const f = await fixture(t);
  const r = await f.run(['--timeout', '10'], { MOCK_MODE: 'exit0-on-term' });
  assert.equal(r.code, 1);
  assert.equal(r.payload.status, 'timeout');
  assert.equal(r.payload.workspace.bound, true);
});
