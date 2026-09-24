/**
 * Workspace-bridge plugin tests.
 *
 * Every test drives the shipped plugin through a REAL Unix domain socket in a
 * private temporary directory, with fake `workspaceRegistry` and
 * `sessionPersistence` services that mirror the installed interfaces. No
 * model, no dsh storage, no HTTP, and no real host process is involved.
 */
import assert from 'node:assert/strict';
import { chmodSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs';
import { connect, createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, describe, test } from 'node:test';
import { apply, inject, name, startWorkspaceBridge, UNIX_SOCKET_PATH_BUDGET } from '../plugins/workspace-bridge.mjs';

const FRAME_LIMIT = 16 * 1024;
const SOCKET_NAME = 'workspace.sock';

let root;
let caseCount = 0;

before(() => {
  // Short base: Unix socket paths are length-limited, and tests exercise the
  // platform boundary deliberately.
  const base = process.platform === 'win32' || !existsSync('/tmp') ? tmpdir() : '/tmp';
  root = realpathSync(mkdtempSync(join(base, 'wsb-')));
});

after(() => {
  rmSync(root, { recursive: true, force: true });
});

/** Fresh private case directory with a path short enough for sun_path. */
function caseDir(label) {
  caseCount += 1;
  const dir = join(root, `c${caseCount}-${label}`);
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  return realpathSync(dir);
}

function makeDir(parent, child) {
  const dir = join(parent, child);
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  return realpathSync(dir);
}

function modeOf(path) {
  return (lstatSync(path).mode & 0o777).toString(8);
}

async function waitUntil(predicate, description, timeoutMs = 3000) {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${description}`);
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

// ---------------------------------------------------------------------------
// Test transport: one newline frame over a real socket, like the CLI client.
// ---------------------------------------------------------------------------

function sendFrame(socketPath, frame) {
  return new Promise((resolve, reject) => {
    const socket = connect(socketPath);
    let buffer = '';
    let settled = false;
    const timer = setTimeout(() => {
      finish(new Error('test client timed out waiting for a response'));
    }, 5000);
    function finish(error, value) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket.destroy();
      if (error) reject(error); else resolve(value);
    }
    socket.setEncoding('utf8');
    socket.on('connect', () => socket.write(frame));
    socket.on('data', (chunk) => {
      buffer += chunk;
      const end = buffer.indexOf('\n');
      if (end < 0) return;
      try {
        finish(null, JSON.parse(buffer.slice(0, end)));
      } catch (error) {
        finish(error);
      }
    });
    socket.on('error', (error) => finish(error));
    socket.on('end', () => finish(new Error('connection ended without a complete response')));
  });
}

function rpc(socketPath, request) {
  return sendFrame(socketPath, `${JSON.stringify(request)}\n`);
}

// ---------------------------------------------------------------------------
// Fake host services mirroring the installed workspaceRegistry interface.
// ---------------------------------------------------------------------------

function workspaceHandle(record, calls, attachSession) {
  return {
    id: record.id,
    get path() { return record.path; },
    get sessionIds() { return [...record.sessionIds]; },
    async attachSession(sessionId) {
      calls.attachSession.push({ workspaceId: record.id, sessionId });
      if (typeof attachSession === 'function') {
        await attachSession(record, sessionId);
        return;
      }
      if (!record.sessionIds.includes(sessionId)) record.sessionIds.unshift(sessionId);
    },
  };
}

/** `create` canonicalizes exactly like the real registry, so aliases collide. */
function makeRegistry(options = {}) {
  const records = new Map();
  const calls = { create: [], attachSession: [] };
  let nextId = 0;
  return {
    calls,
    records,
    async create(cwd) {
      const canonical = realpathSync(cwd);
      calls.create.push(canonical);
      if (typeof options.create === 'function') {
        return await options.create({ canonical, calls, records });
      }
      let record = records.get(canonical);
      if (record === undefined) {
        record = { id: `workspace-${++nextId}`, path: canonical, sessionIds: [] };
        records.set(canonical, record);
      }
      return workspaceHandle(record, calls, options.attachSession);
    },
  };
}

function makePersistence(sessions, options = {}) {
  const calls = { list: 0 };
  return {
    calls,
    async list() {
      calls.list += 1;
      if (options.fail === true) throw new Error(options.message ?? 'persistence failure');
      const value = typeof sessions === 'function' ? sessions() : sessions;
      return value.map((header) => ({ header }));
    },
  };
}

function makeContext({ registry, persistence }) {
  const listeners = [];
  const disposers = [];
  return {
    workspaceRegistry: registry,
    sessionPersistence: persistence,
    on(event, listener) {
      listeners.push({ event, listener });
      return () => {};
    },
    effect(execute) {
      const dispose = execute();
      if (typeof dispose === 'function') disposers.push(dispose);
      return dispose;
    },
    async dispose() {
      for (const entry of listeners) if (entry.event === 'dispose') await entry.listener();
      for (const dispose of disposers.reverse()) await dispose();
    },
  };
}

function rootHeader(cwd, id = 'session-root-1') {
  return {
    id,
    version: 1,
    cwd,
    parentSession: null,
    origin: 'user',
    delegationDepth: 0,
    createdAt: new Date(0).toISOString(),
  };
}

/** Start a bridge on a fresh case directory and register test cleanup. */
async function start(t, options = {}) {
  const dir = options.dir ?? caseDir('bridge');
  const socketPath = options.socketPath ?? join(dir, SOCKET_NAME);
  const registry = options.registry ?? makeRegistry();
  const persistence = options.persistence ?? makePersistence([]);
  const context = options.context ?? makeContext({ registry, persistence });
  const close = await startWorkspaceBridge(context, { socketPath });
  t.after(async () => { await close(); });
  return { dir, socketPath, registry, persistence, context, close };
}

describe('plugin surface', () => {
  test('declares its Cordis identity and injected services', () => {
    assert.equal(name, 'deepseek-delegate-workspace-bridge');
    assert.deepEqual(inject, ['workspaceRegistry', 'sessionPersistence']);
  });

  test('rejects a missing or relative socket path and missing services', async () => {
    const dir = caseDir('config');
    const context = makeContext({ registry: makeRegistry(), persistence: makePersistence([]) });
    await assert.rejects(startWorkspaceBridge(context, {}), /config\.socketPath/);
    await assert.rejects(startWorkspaceBridge(context, { socketPath: 'relative.sock' }), /absolute config\.socketPath/);
    await assert.rejects(startWorkspaceBridge({}, { socketPath: join(dir, SOCKET_NAME) }), /workspaceRegistry/);
    await assert.rejects(
      startWorkspaceBridge({ workspaceRegistry: makeRegistry() }, { socketPath: join(dir, SOCKET_NAME) }),
      /sessionPersistence/,
    );
    assert.equal(existsSync(join(dir, SOCKET_NAME)), false, 'no socket is created for a rejected config');
  });
});

describe('resolve', () => {
  test('ping answers ready', async (t) => {
    const bridge = await start(t);
    const response = await rpc(bridge.socketPath, { version: 1, id: 'ping-1', method: 'ping' });
    assert.deepEqual(response, { version: 1, id: 'ping-1', ok: true, value: { ready: true } });
  });

  test('canonicalizes a symlink alias and reuses one workspace record', async (t) => {
    const dir = caseDir('resolve');
    const real = makeDir(dir, 'real');
    const alias = join(dir, 'alias');
    symlinkSync(real, alias);
    const registry = makeRegistry();
    const bridge = await start(t, { dir, registry });

    const first = await rpc(bridge.socketPath, { version: 1, id: 'r1', method: 'resolve', cwd: alias });
    assert.deepEqual(first, {
      version: 1,
      id: 'r1',
      ok: true,
      value: { id: 'workspace-1', path: real, created: false },
    });
    const second = await rpc(bridge.socketPath, { version: 1, id: 'r2', method: 'resolve', cwd: real });
    assert.equal(second.ok, true);
    assert.deepEqual(second.value, { id: 'workspace-1', path: real, created: false });
    assert.deepEqual(registry.calls.create, [real, real], 'the alias was canonicalized before create');
    assert.equal(registry.records.size, 1);
    assert.equal(bridge.persistence.calls.list, 0, 'resolve never reads session persistence');
  });

  test('rejects a missing, file, or relative cwd without touching the registry', async (t) => {
    const dir = caseDir('resolve-bad');
    const file = join(dir, 'a-file');
    writeFileSync(file, 'not a directory', { mode: 0o600 });
    const registry = makeRegistry();
    const bridge = await start(t, { dir, registry });

    const cases = [
      [{ cwd: join(dir, 'missing') }, 'invalid-cwd'],
      [{ cwd: file }, 'invalid-cwd'],
      [{ cwd: 'relative-dir' }, 'bad-request'],
      [{}, 'bad-request'],
      [{ cwd: 42 }, 'bad-request'],
    ];
    for (const [fields, code] of cases) {
      const response = await rpc(bridge.socketPath, { version: 1, id: 'bad', method: 'resolve', ...fields });
      assert.deepEqual(response, { version: 1, id: 'bad', ok: false, error: code }, JSON.stringify(fields));
    }
    assert.equal(registry.calls.create.length, 0);
  });

  test('maps a registry failure to resolve-failed without echoing its text', async (t) => {
    const dir = caseDir('resolve-fail');
    const secret = 'SECRET-registry-text-9137';
    const registry = makeRegistry({ create: async () => { throw new Error(secret); } });
    const bridge = await start(t, { dir, registry });

    const response = await rpc(bridge.socketPath, { version: 1, id: 'rf', method: 'resolve', cwd: dir });
    assert.deepEqual(response, { version: 1, id: 'rf', ok: false, error: 'resolve-failed' });
    assert.ok(!JSON.stringify(response).includes(secret));
  });
});

describe('attach', () => {
  test('binds a completed ordinary root session and verifies membership', async (t) => {
    const dir = caseDir('bind');
    const cwd = makeDir(dir, 'cwd');
    const registry = makeRegistry();
    const persistence = makePersistence([rootHeader(cwd, 'session-root-1')]);
    const bridge = await start(t, { dir, registry, persistence });

    const response = await rpc(bridge.socketPath, {
      version: 1,
      id: 'attach-1',
      method: 'attach',
      cwd,
      sessionId: 'session-root-1',
    });
    assert.deepEqual(response, {
      version: 1,
      id: 'attach-1',
      ok: true,
      value: { id: 'workspace-1', path: cwd, sessionId: 'session-root-1', bound: true },
    });
    assert.equal(persistence.calls.list, 1);
    assert.deepEqual(registry.calls.create, [cwd]);
    assert.deepEqual(registry.calls.attachSession, [{ workspaceId: 'workspace-1', sessionId: 'session-root-1' }]);
    assert.deepEqual(registry.records.get(cwd).sessionIds, ['session-root-1']);
  });

  test('accepts a header cwd that canonicalizes through a symlink alias', async (t) => {
    const dir = caseDir('bind-alias');
    const real = makeDir(dir, 'real');
    const alias = join(dir, 'alias');
    symlinkSync(real, alias);
    const registry = makeRegistry();
    const persistence = makePersistence([rootHeader(alias, 'session-alias')]);
    const bridge = await start(t, { dir, registry, persistence });

    const response = await rpc(bridge.socketPath, {
      version: 1,
      id: 'alias',
      method: 'attach',
      cwd: real,
      sessionId: 'session-alias',
    });
    assert.equal(response.ok, true, JSON.stringify(response));
    assert.equal(response.value.path, real);
    assert.equal(response.value.bound, true);
  });

  test('rejects a cwd mismatch before creating or mutating any workspace', async (t) => {
    const dir = caseDir('mismatch');
    const cwd = makeDir(dir, 'cwd');
    const elsewhere = makeDir(dir, 'elsewhere');
    const registry = makeRegistry();
    const persistence = makePersistence([rootHeader(elsewhere, 'session-elsewhere')]);
    const bridge = await start(t, { dir, registry, persistence });

    const response = await rpc(bridge.socketPath, {
      version: 1,
      id: 'mm',
      method: 'attach',
      cwd,
      sessionId: 'session-elsewhere',
    });
    assert.deepEqual(response, { version: 1, id: 'mm', ok: false, error: 'cwd-mismatch' });
    assert.equal(persistence.calls.list, 1);
    assert.equal(registry.calls.create.length, 0, 'create must not run before cwd validation');
    assert.equal(registry.calls.attachSession.length, 0);
  });

  test('rejects an unknown session without mutation', async (t) => {
    const dir = caseDir('unknown');
    const cwd = makeDir(dir, 'cwd');
    const registry = makeRegistry();
    const persistence = makePersistence([rootHeader(cwd, 'session-other')]);
    const bridge = await start(t, { dir, registry, persistence });

    const response = await rpc(bridge.socketPath, {
      version: 1,
      id: 'u1',
      method: 'attach',
      cwd,
      sessionId: 'session-missing',
    });
    assert.deepEqual(response, { version: 1, id: 'u1', ok: false, error: 'unknown-session' });
    assert.equal(registry.calls.create.length, 0);
    assert.equal(registry.calls.attachSession.length, 0);
  });

  const childCases = [
    ['fork parent', { parentSession: 'session-parent' }],
    ['subagent origin', { origin: 'subagent' }],
    ['positive delegation depth', { delegationDepth: 1 }],
  ];
  for (const [label, extra] of childCases) {
    test(`rejects a ${label} session without mutation`, async (t) => {
      const dir = caseDir('child');
      const cwd = makeDir(dir, 'cwd');
      const registry = makeRegistry();
      const persistence = makePersistence([{ ...rootHeader(cwd, 'session-child'), ...extra }]);
      const bridge = await start(t, { dir, registry, persistence });

      const response = await rpc(bridge.socketPath, {
        version: 1,
        id: 'c1',
        method: 'attach',
        cwd,
        sessionId: 'session-child',
      });
      assert.deepEqual(response, { version: 1, id: 'c1', ok: false, error: 'not-root-session' });
      assert.equal(registry.calls.create.length, 0);
      assert.equal(registry.calls.attachSession.length, 0);
    });
  }

  test('attaches idempotently on retry', async (t) => {
    const dir = caseDir('retry');
    const cwd = makeDir(dir, 'cwd');
    const registry = makeRegistry();
    const persistence = makePersistence([rootHeader(cwd, 'session-retry')]);
    const bridge = await start(t, { dir, registry, persistence });

    const request = { version: 1, id: 'a', method: 'attach', cwd, sessionId: 'session-retry' };
    const first = await rpc(bridge.socketPath, request);
    const second = await rpc(bridge.socketPath, { ...request, id: 'b' });
    assert.equal(first.ok, true, JSON.stringify(first));
    assert.equal(second.ok, true, JSON.stringify(second));
    assert.equal(first.value.id, second.value.id);
    assert.deepEqual(registry.records.get(cwd).sessionIds, ['session-retry'], 'membership is recorded once');
    assert.equal(registry.calls.create.length, 2, 'each request resolves through create');
    assert.equal(registry.calls.attachSession.length, 2);
  });

  test('verifies the workspaceId when one is supplied', async (t) => {
    const dir = caseDir('workspace-id');
    const cwd = makeDir(dir, 'cwd');
    const registry = makeRegistry();
    const persistence = makePersistence([rootHeader(cwd, 'session-wid')]);
    const bridge = await start(t, { dir, registry, persistence });

    const mismatch = await rpc(bridge.socketPath, {
      version: 1,
      id: 'w1',
      method: 'attach',
      cwd,
      sessionId: 'session-wid',
      workspaceId: 'workspace-else',
    });
    assert.deepEqual(mismatch, { version: 1, id: 'w1', ok: false, error: 'workspace-mismatch' });
    assert.equal(registry.calls.attachSession.length, 0, 'a mismatched identity never attaches');

    const match = await rpc(bridge.socketPath, {
      version: 1,
      id: 'w2',
      method: 'attach',
      cwd,
      sessionId: 'session-wid',
      workspaceId: 'workspace-1',
    });
    assert.equal(match.ok, true, JSON.stringify(match));
    assert.equal(match.value.id, 'workspace-1');
  });

  test('fails closed when the registry does not actually record membership', async (t) => {
    const dir = caseDir('no-membership');
    const cwd = makeDir(dir, 'cwd');
    const registry = makeRegistry({ attachSession: async () => {} });
    const persistence = makePersistence([rootHeader(cwd, 'session-nomem')]);
    const bridge = await start(t, { dir, registry, persistence });

    const response = await rpc(bridge.socketPath, {
      version: 1,
      id: 'nm',
      method: 'attach',
      cwd,
      sessionId: 'session-nomem',
    });
    assert.deepEqual(response, { version: 1, id: 'nm', ok: false, error: 'attach-failed' });
    assert.deepEqual(registry.records.get(cwd).sessionIds, []);
  });

  test('fails closed when the returned workspace path is wrong', async (t) => {
    const dir = caseDir('wrong-path');
    const cwd = makeDir(dir, 'cwd');
    const registry = makeRegistry({
      create: async ({ calls }) => workspaceHandle({ id: 'workspace-x', path: '/somewhere-else', sessionIds: [] }, calls),
    });
    const persistence = makePersistence([rootHeader(cwd, 'session-path')]);
    const bridge = await start(t, { dir, registry, persistence });

    const response = await rpc(bridge.socketPath, {
      version: 1,
      id: 'wp',
      method: 'attach',
      cwd,
      sessionId: 'session-path',
    });
    assert.deepEqual(response, { version: 1, id: 'wp', ok: false, error: 'attach-failed' });
  });

  test('maps a registry failure to attach-failed without leaking its text', async (t) => {
    const dir = caseDir('service-fail');
    const cwd = makeDir(dir, 'cwd');
    const secret = 'SECRET-service-text-5521';
    const registry = makeRegistry({ create: async () => { throw new Error(secret); } });
    const persistence = makePersistence([rootHeader(cwd, 'session-fail')]);
    const bridge = await start(t, { dir, registry, persistence });

    const response = await rpc(bridge.socketPath, {
      version: 1,
      id: 'f1',
      method: 'attach',
      cwd,
      sessionId: 'session-fail',
    });
    assert.deepEqual(response, { version: 1, id: 'f1', ok: false, error: 'attach-failed' });
    assert.ok(!JSON.stringify(response).includes(secret));
    assert.equal(registry.calls.attachSession.length, 0);
  });

  test('maps a persistence listing failure to internal', async (t) => {
    const dir = caseDir('list-fail');
    const cwd = makeDir(dir, 'cwd');
    const secret = 'SECRET-persistence-text-3319';
    const registry = makeRegistry();
    const persistence = makePersistence([], { fail: true, message: secret });
    const bridge = await start(t, { dir, registry, persistence });

    const response = await rpc(bridge.socketPath, {
      version: 1,
      id: 'lf',
      method: 'attach',
      cwd,
      sessionId: 'session-any',
    });
    assert.deepEqual(response, { version: 1, id: 'lf', ok: false, error: 'internal' });
    assert.ok(!JSON.stringify(response).includes(secret));
    assert.equal(registry.calls.create.length, 0);
  });

  test('rejects attach requests with missing or invalid fields as bad-request', async (t) => {
    const dir = caseDir('fields');
    const cwd = makeDir(dir, 'cwd');
    const registry = makeRegistry();
    const persistence = makePersistence([rootHeader(cwd, 'session-fields')]);
    const bridge = await start(t, { dir, registry, persistence });

    const cases = [
      { method: 'attach', sessionId: 'session-fields' },
      { method: 'attach', cwd },
      { method: 'attach', cwd: 'relative', sessionId: 'session-fields' },
      { method: 'attach', cwd, sessionId: '' },
      { method: 'attach', cwd, sessionId: 'session-fields', workspaceId: '' },
      { method: 'attach', cwd, sessionId: 'session-fields', workspaceId: 7 },
    ];
    for (const fields of cases) {
      const response = await rpc(bridge.socketPath, { version: 1, id: 'bad', ...fields });
      assert.deepEqual(response, { version: 1, id: 'bad', ok: false, error: 'bad-request' }, JSON.stringify(fields));
    }
    assert.equal(persistence.calls.list, 0);
    assert.equal(registry.calls.create.length, 0);
  });
});

describe('socket hygiene', () => {
  test('creates a 0700 parent chain and a 0600 socket', async (t) => {
    const dir = caseDir('perm');
    const nested = join(dir, 'nested', 'private');
    const socketPath = join(nested, SOCKET_NAME);
    await start(t, { socketPath });

    assert.equal(modeOf(nested), '700');
    assert.equal(modeOf(join(dir, 'nested')), '700');
    assert.equal(modeOf(socketPath), '600');
    assert.equal(lstatSync(socketPath).isSocket(), true);
  });

  test('rejects an existing group or world accessible parent', async () => {
    const dir = caseDir('perm-open');
    chmodSync(dir, 0o755);
    const socketPath = join(dir, SOCKET_NAME);
    await assert.rejects(
      startWorkspaceBridge(makeContext({ registry: makeRegistry(), persistence: makePersistence([]) }), { socketPath }),
      /owner-private \(0700\)/,
    );
    assert.equal(existsSync(socketPath), false);
  });

  test('rejects a symlinked parent directory', async () => {
    const dir = caseDir('perm-link');
    const real = makeDir(dir, 'real');
    const alias = join(dir, 'alias');
    symlinkSync(real, alias);
    const socketPath = join(alias, SOCKET_NAME);
    await assert.rejects(
      startWorkspaceBridge(makeContext({ registry: makeRegistry(), persistence: makePersistence([]) }), { socketPath }),
      /symlinked socket directory/,
    );
    assert.equal(existsSync(join(real, SOCKET_NAME)), false);
  });

  test('never unlinks an existing file at the socket path', async () => {
    const dir = caseDir('occupied-file');
    const socketPath = join(dir, SOCKET_NAME);
    writeFileSync(socketPath, 'keep me', { mode: 0o600 });
    const before = lstatSync(socketPath);
    await assert.rejects(
      startWorkspaceBridge(makeContext({ registry: makeRegistry(), persistence: makePersistence([]) }), { socketPath }),
      /remove the stale socket explicitly/,
    );
    const after = lstatSync(socketPath);
    assert.equal(readFileSync(socketPath, 'utf8'), 'keep me');
    assert.equal(after.ino, before.ino);
  });

  test('never unlinks a live socket owned by another server', async (t) => {
    const dir = caseDir('occupied-live');
    const socketPath = join(dir, SOCKET_NAME);
    const live = createServer();
    await new Promise((resolve, reject) => {
      live.once('error', reject);
      live.listen(socketPath, resolve);
    });
    t.after(() => new Promise((resolve) => live.close(resolve)));
    const before = lstatSync(socketPath);

    await assert.rejects(
      startWorkspaceBridge(makeContext({ registry: makeRegistry(), persistence: makePersistence([]) }), { socketPath }),
      /remove the stale socket explicitly/,
    );
    const after = lstatSync(socketPath);
    assert.equal(after.ino, before.ino, 'the other server still owns the same inode');
    assert.equal(after.isSocket(), true);
  });

  test('serves a socket path too long for a private bind name', async (t) => {
    const dir = caseDir('long');
    const shortName = 's.sock';
    const pad = Math.max(1, UNIX_SOCKET_PATH_BUDGET - 3 - Buffer.byteLength(join(dir, shortName)));
    const longDir = join(dir, 'd'.repeat(pad));
    mkdirSync(longDir, { mode: 0o700 });
    const socketPath = join(longDir, shortName);
    assert.ok(Buffer.byteLength(socketPath) <= UNIX_SOCKET_PATH_BUDGET, 'the public path itself must fit');
    assert.ok(
      Buffer.byteLength(join(longDir, '.w0000000000')) > UNIX_SOCKET_PATH_BUDGET,
      'no private bind name fits, so the bridge must bind the public path directly',
    );

    const registry = makeRegistry();
    const bridge = await start(t, { dir, registry, socketPath });
    const response = await rpc(socketPath, { version: 1, id: 'long', method: 'ping' });
    assert.deepEqual(response, { version: 1, id: 'long', ok: true, value: { ready: true } });
    await bridge.close();
    assert.equal(existsSync(socketPath), false);
  });

  test('rejects a socket path beyond the platform limit', async () => {
    const dir = caseDir('too-long');
    const socketPath = join(dir, 'x'.repeat(UNIX_SOCKET_PATH_BUDGET + 5));
    await assert.rejects(
      startWorkspaceBridge(makeContext({ registry: makeRegistry(), persistence: makePersistence([]) }), { socketPath }),
      /exceeds this platform's Unix socket limit/,
    );
    assert.equal(existsSync(socketPath), false);
  });
});

describe('protocol limits', () => {
  test('rejects malformed frames with fixed codes only', async (t) => {
    const bridge = await start(t);
    const cases = [
      ['{not json}\n', 'bad-request', ''],
      ['[1,2,3]\n', 'bad-request', ''],
      [`${JSON.stringify({ version: 2, id: 'v2', method: 'ping' })}\n`, 'bad-request', 'v2'],
      [`${JSON.stringify({ version: 1, method: 'ping' })}\n`, 'bad-request', ''],
      [`${JSON.stringify({ version: 1, id: 'm1', method: 5 })}\n`, 'bad-request', 'm1'],
      [`${JSON.stringify({ version: 1, id: 'm2', method: 'frobnicate' })}\n`, 'unsupported-method', 'm2'],
    ];
    for (const [frame, code, id] of cases) {
      const response = await sendFrame(bridge.socketPath, frame);
      assert.deepEqual(response, { version: 1, id, ok: false, error: code }, frame);
      assert.match(response.error, /^[a-z-]{1,60}$/, 'errors are short machine codes');
      assert.deepEqual(Object.keys(response).sort(), ['error', 'id', 'ok', 'version']);
    }
  });

  test('rejects a frame larger than 16 KiB and answers one frame per connection', async (t) => {
    const bridge = await start(t);

    const oversize = await sendFrame(bridge.socketPath, `${'x'.repeat(FRAME_LIMIT + 1)}\n`);
    assert.deepEqual(oversize, { version: 1, id: '', ok: false, error: 'frame-too-large' });

    const atLimit = await sendFrame(bridge.socketPath, `${'x'.repeat(FRAME_LIMIT)}\n`);
    assert.equal(atLimit.error, 'bad-request', 'exactly 16 KiB is still one bounded frame');

    const first = { version: 1, id: 'first', method: 'ping' };
    const second = { version: 1, id: 'second', method: 'ping' };
    const only = await sendFrame(bridge.socketPath, `${JSON.stringify(first)}\n${JSON.stringify(second)}\n`);
    assert.equal(only.id, 'first', 'only the first frame on a connection is answered');
  });
});

describe('shutdown', () => {
  test('close removes only its own socket and is idempotent', async (t) => {
    const bridge = await start(t);
    assert.equal(existsSync(bridge.socketPath), true);
    await bridge.close();
    await bridge.close();
    assert.equal(existsSync(bridge.socketPath), false);
    assert.equal(existsSync(bridge.dir), true, 'the private parent directory is kept');
  });

  test('the apply dispose hook removes the socket', async (t) => {
    const dir = caseDir('dispose');
    const socketPath = join(dir, SOCKET_NAME);
    const context = makeContext({ registry: makeRegistry(), persistence: makePersistence([]) });
    await apply(context, { socketPath });
    assert.equal(existsSync(socketPath), true);
    await context.dispose();
    assert.equal(existsSync(socketPath), false);
  });

  test('apply closes the bridge when no lifecycle hook can be registered', async () => {
    const dir = caseDir('apply-race');
    const socketPath = join(dir, SOCKET_NAME);
    const context = {
      workspaceRegistry: makeRegistry(),
      sessionPersistence: makePersistence([]),
      on() { throw new Error('fiber is disposing'); },
      effect() { throw new Error('fiber is disposing'); },
    };
    await apply(context, { socketPath });
    assert.equal(existsSync(socketPath), false, 'a disposed fiber must not leak a listening server');
  });

  test('close leaves a path that is no longer its own socket', async (t) => {
    const bridge = await start(t);
    unlinkSync(bridge.socketPath);
    writeFileSync(bridge.socketPath, 'replacement', { mode: 0o600 });
    await bridge.close();
    assert.equal(readFileSync(bridge.socketPath, 'utf8'), 'replacement');
  });

  test('close waits for an in-flight request before disposal', async (t) => {
    const dir = caseDir('inflight');
    const cwd = makeDir(dir, 'cwd');
    const socketPath = join(dir, SOCKET_NAME);
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const registry = makeRegistry({
      create: async ({ canonical, calls }) => {
        await gate;
        return workspaceHandle({ id: 'workspace-late', path: canonical, sessionIds: [] }, calls);
      },
    });
    const persistence = makePersistence([rootHeader(cwd, 'session-late')]);
    const context = makeContext({ registry, persistence });
    const close = await startWorkspaceBridge(context, { socketPath });
    t.after(async () => { await close(); });

    const pending = rpc(socketPath, { version: 1, id: 'late', method: 'attach', cwd, sessionId: 'session-late' });
    await waitUntil(() => registry.calls.create.length === 1, 'the in-flight request to reach create');
    const closing = close();
    release();

    const response = await pending;
    assert.equal(response.ok, true, JSON.stringify(response));
    assert.deepEqual(response.value, { id: 'workspace-late', path: cwd, sessionId: 'session-late', bound: true });
    await closing;
    assert.equal(existsSync(socketPath), false);
  });
});
