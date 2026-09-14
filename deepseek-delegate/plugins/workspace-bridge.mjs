/**
 * deepseek-delegate workspace bridge.
 *
 * A self-contained Cordis plugin that exposes the official in-process
 * `workspaceRegistry` API to the delegation CLI over a private Unix domain
 * socket. It performs no HTTP, holds no authentication token, never calls a
 * model, and never opens DSH storage directly: the CLI canonicalizes its
 * `--cwd`, asks the owning host to resolve or create the matching workspace,
 * proves that one completed ordinary root session really belongs to that
 * directory, and asks the registry to record the membership.
 *
 * Security posture:
 * - The socket's parent directory must be owner-private: 0700, owned by the
 *   current user, and not a symlink. It is created recursively with 0700 when
 *   missing.
 * - The listener binds under a private random name in that directory, when the
 *   platform's Unix socket path limit allows one, and is published to the
 *   requested path atomically (hard link, or rename on filesystems without
 *   hard links). The path this process asks libuv to unlink on close therefore
 *   never names a file the bridge did not create: an occupied path fails with
 *   a clear stale-socket error and is left untouched. Paths too long for a
 *   private bind name are bound directly and still identity-checked.
 * - The socket itself is chmod 0600 after bind, and only the bridge's own
 *   socket inode is ever unlinked.
 * - One bounded newline-terminated JSON frame per connection (16 KiB maximum,
 *   15 s request-read window). Failures answer with a fixed machine code and
 *   never echo a remote exception, path, or stack.
 * - On disposal the server stops accepting, in-flight requests are awaited
 *   where possible, connections are destroyed, and only the bridge's own
 *   socket inode is removed.
 *
 * Wire protocol (version 1):
 *   request  { version: 1, id: string, method: 'ping'|'resolve'|'attach',
 *              cwd?: string, sessionId?: string, workspaceId?: string }
 *   response { version: 1, id, ok: true, value }
 *          | { version: 1, id, ok: false, error: <fixed code> }
 *
 * @module deepseek-delegate/plugins/workspace-bridge
 */
import { randomBytes } from 'node:crypto';
import { chmodSync, linkSync, lstatSync, mkdirSync, renameSync, unlinkSync } from 'node:fs';
import { realpath, stat } from 'node:fs/promises';
import { createServer } from 'node:net';
import { dirname, isAbsolute, join } from 'node:path';

/** Stable Cordis plugin name; the profile patch row mirrors it. */
export const name = 'deepseek-delegate-workspace-bridge';

/** The only host services this bridge speaks to; nothing is read from storage. */
export const inject = ['workspaceRegistry', 'sessionPersistence'];

/** Wire protocol version understood by this bridge. */
export const PROTOCOL_VERSION = 1;

/** Largest accepted request frame in bytes, excluding the trailing newline. */
export const MAX_FRAME_BYTES = 16 * 1024;

/** How long a connection may take to deliver one complete request frame. */
export const CONNECTION_TIMEOUT_MS = 15_000;

/** Upper bound on waiting for in-flight requests while disposing. */
const SHUTDOWN_GRACE_MS = 2_000;

/**
 * Conservative usable length of a Unix socket path in bytes. `sun_path` is 104
 * bytes on macOS/BSD and 108 on Linux, and an over-long path can make
 * `listen()` report success without creating the socket file, so the bridge
 * checks the limit itself.
 */
export const UNIX_SOCKET_PATH_BUDGET = process.platform === 'linux' ? 105 : 101;

/** Hard-link failures that justify the atomic-rename publish fallback. */
const HARDLINK_FALLBACK_CODES = new Set(['EPERM', 'ENOTSUP', 'EOPNOTSUPP', 'EXDEV']);

/**
 * Fixed failure vocabulary. A response `error` field is always exactly one of
 * these codes: stable, machine-readable, and free of remote text.
 */
export const ERROR_CODES = Object.freeze({
  BAD_REQUEST: 'bad-request',
  FRAME_TOO_LARGE: 'frame-too-large',
  TIMEOUT: 'timeout',
  UNSUPPORTED_METHOD: 'unsupported-method',
  INVALID_CWD: 'invalid-cwd',
  RESOLVE_FAILED: 'resolve-failed',
  UNKNOWN_SESSION: 'unknown-session',
  NOT_ROOT_SESSION: 'not-root-session',
  CWD_MISMATCH: 'cwd-mismatch',
  WORKSPACE_MISMATCH: 'workspace-mismatch',
  ATTACH_FAILED: 'attach-failed',
  INTERNAL: 'internal',
});

const ERROR_CODE_SET = new Set(Object.values(ERROR_CODES));

/** Plain JSON object, excluding arrays and null. */
function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** Non-empty string. */
function isUsableString(value) {
  return typeof value === 'string' && value !== '';
}

/** Absolute, non-empty directory spelling (realpath handles aliases later). */
function isAbsoluteDirectoryPath(value) {
  return isUsableString(value) && isAbsolute(value);
}

/**
 * Ensure the socket's parent directory exists and is owner-private, then
 * refuse anything the CLI would also refuse: a symlinked parent, a parent
 * owned by another user, or a parent with any group/world permission bit.
 *
 * @param socketPath - Absolute path of the socket that will be bound.
 */
function assertPrivateParentDirectory(socketPath) {
  const parent = dirname(socketPath);
  try {
    mkdirSync(parent, { recursive: true, mode: 0o700 });
  } catch (error) {
    throw new Error(`workspace bridge cannot create its private socket directory '${parent}'`, { cause: error });
  }
  let info;
  try {
    info = lstatSync(parent);
  } catch (error) {
    throw new Error(`workspace bridge cannot inspect its socket directory '${parent}'`, { cause: error });
  }
  if (info.isSymbolicLink()) {
    throw new Error(`workspace bridge refuses a symlinked socket directory: '${parent}'`);
  }
  if (!info.isDirectory()) {
    throw new Error(`workspace bridge socket directory is not a directory: '${parent}'`);
  }
  if (info.uid !== process.getuid()) {
    throw new Error(`workspace bridge socket directory is not owned by the current user: '${parent}'`);
  }
  if ((info.mode & 0o077) !== 0) {
    throw new Error(`workspace bridge socket directory must be owner-private (0700): '${parent}'`);
  }
  if ((info.mode & 0o200) === 0) {
    throw new Error(`workspace bridge socket directory is not writable by its owner: '${parent}'`);
  }
}

/**
 * Refuse to start when anything already occupies the socket path. The bridge
 * never unlinks a file or socket it did not create; a stale socket is removed
 * by the user, explicitly.
 *
 * @param socketPath - Absolute path of the socket that will be bound.
 */
function assertSocketPathAvailable(socketPath) {
  let info;
  try {
    info = lstatSync(socketPath);
  } catch (error) {
    if (error?.code === 'ENOENT') return;
    throw new Error(`workspace bridge cannot inspect its socket path '${socketPath}'`, { cause: error });
  }
  throw occupiedSocketError(socketPath, info);
}

/** Clear, path-naming failure for a path that is already taken. */
function occupiedSocketError(socketPath, info) {
  const kind = info?.isSocket() === true ? 'socket' : 'file';
  return new Error(`workspace bridge refuses to replace the existing ${kind} at '${socketPath}'; remove the stale socket explicitly before starting the bridge`);
}

/**
 * Publish the bound listener at the requested path without ever replacing an
 * existing entry: hard-link the private bind name into place and drop the bind
 * name, or fall back to rename where hard links are unavailable. Because the
 * listener's own recorded name is the private bind path, `server.close()` can
 * only ever unlink an entry this bridge created.
 *
 * @param bindPath - Private random name the listener is bound to.
 * @param socketPath - Requested public path.
 */
function publishSocket(bindPath, socketPath) {
  try {
    linkSync(bindPath, socketPath);
  } catch (error) {
    if (error?.code === 'EEXIST') throw occupiedSocketError(socketPath);
    if (!HARDLINK_FALLBACK_CODES.has(error?.code)) {
      throw new Error(`workspace bridge could not publish its socket at '${socketPath}' (${error?.code ?? 'unknown error'})`, { cause: error });
    }
    // The destination was already checked, and rename is atomic; re-check
    // immediately so the fallback still refuses a path that appeared meanwhile.
    assertSocketPathAvailable(socketPath);
    renameSync(bindPath, socketPath);
    return;
  }
  unlinkSync(bindPath);
}

/** Bind the server, failing with an explicit stale-socket message on EADDRINUSE. */
async function listen(server, socketPath) {
  await new Promise((resolve, reject) => {
    const onError = (error) => {
      server.off('listening', onListening);
      reject(listenFailure(error, socketPath));
    };
    const onListening = () => {
      server.off('error', onError);
      resolve();
    };
    server.once('error', onError);
    server.once('listening', onListening);
    server.listen({ path: socketPath, exclusive: true });
  });
}

/** Turn a bind failure into a clear, local error that never hides the path. */
function listenFailure(error, socketPath) {
  if (error?.code === 'EADDRINUSE' || error?.code === 'EEXIST') {
    return new Error(`workspace bridge EADDRINUSE: '${socketPath}' is already bound; remove the stale socket explicitly before starting the bridge`, { cause: error });
  }
  return new Error(`workspace bridge could not listen on '${socketPath}' (${error?.code ?? 'unknown error'})`, { cause: error });
}

/** Device/inode identity of the socket this bridge just created. */
function readSocketIdentity(socketPath) {
  const info = lstatSync(socketPath);
  if (!info.isSocket()) throw new Error(`workspace bridge expected a socket at '${socketPath}'`);
  return { dev: info.dev, ino: info.ino };
}

/** Remove the socket only when the path still names this bridge's own inode. */
function removeOwnSocket(socketPath, identity) {
  if (identity === null) return false;
  let info;
  try {
    info = lstatSync(socketPath);
  } catch {
    return false;
  }
  if (!info.isSocket() || info.dev !== identity.dev || info.ino !== identity.ino) return false;
  try {
    unlinkSync(socketPath);
    return true;
  } catch {
    return false;
  }
}

/** `Promise.race` with a cancellable timer so a fast settle never leaves one pending. */
function settleWithin(promise, timeoutMs) {
  let timer;
  return Promise.race([
    promise,
    new Promise((resolve) => {
      timer = setTimeout(resolve, timeoutMs);
    }),
  ]).finally(() => clearTimeout(timer));
}

/**
 * Accept one connection: read one bounded newline frame, answer once, and end.
 * The request-read window is 15 s; it is cleared once a frame is dispatched so
 * a slow registry operation is bounded by the client, not by the socket.
 *
 * @param socket - Accepted connection.
 * @param state - Bridge lifecycle state (connections, idle, in-flight, closing).
 * @param services - Injected `{ registry, persistence }` services.
 */
function acceptConnection(socket, state, services) {
  if (state.closing) {
    socket.destroy();
    return;
  }
  state.connections.add(socket);
  state.idle.add(socket);

  let buffer = Buffer.alloc(0);
  let dispatched = false;

  socket.setTimeout(CONNECTION_TIMEOUT_MS);
  socket.on('close', () => {
    state.connections.delete(socket);
    state.idle.delete(socket);
  });
  socket.on('error', () => socket.destroy());
  socket.on('timeout', () => {
    if (dispatched) {
      socket.destroy();
      return;
    }
    dispatched = true;
    state.idle.delete(socket);
    sendError(socket, '', ERROR_CODES.TIMEOUT);
  });
  socket.on('data', (chunk) => {
    if (dispatched || state.closing) return;
    buffer = buffer.length === 0 ? chunk : Buffer.concat([buffer, chunk]);
    const newline = buffer.indexOf(0x0a);
    if (newline < 0) {
      if (buffer.length > MAX_FRAME_BYTES) {
        dispatched = true;
        state.idle.delete(socket);
        sendError(socket, '', ERROR_CODES.FRAME_TOO_LARGE);
      }
      return;
    }
    dispatched = true;
    state.idle.delete(socket);
    socket.setTimeout(0);
    if (newline > MAX_FRAME_BYTES) {
      sendError(socket, '', ERROR_CODES.FRAME_TOO_LARGE);
      return;
    }
    const task = dispatchFrame(socket, buffer.subarray(0, newline), services);
    state.inFlight.add(task);
    const settled = () => state.inFlight.delete(task);
    task.then(settled, settled);
  });
}

/**
 * Parse and serve exactly one frame. This function never rejects: every
 * failure path answers with a fixed code, and the response echoes the request
 * id when it was a string.
 */
async function dispatchFrame(socket, frame, services) {
  let id = '';
  try {
    let request;
    try {
      request = JSON.parse(frame.toString('utf8'));
    } catch {
      sendError(socket, '', ERROR_CODES.BAD_REQUEST);
      return;
    }
    if (isObject(request) && typeof request.id === 'string') id = request.id;
    let outcome;
    try {
      outcome = await handleRequest(request, services);
    } catch {
      outcome = failure(ERROR_CODES.INTERNAL);
    }
    await (outcome.ok ? sendResult(socket, id, outcome.value) : sendError(socket, id, outcome.error));
  } catch {
    await sendError(socket, id, ERROR_CODES.INTERNAL);
  }
}

/** Dispatch one validated-enough request to its method handler. */
async function handleRequest(request, services) {
  if (!isObject(request)) return failure(ERROR_CODES.BAD_REQUEST);
  if (request.version !== PROTOCOL_VERSION) return failure(ERROR_CODES.BAD_REQUEST);
  if (typeof request.id !== 'string') return failure(ERROR_CODES.BAD_REQUEST);
  if (typeof request.method !== 'string') return failure(ERROR_CODES.BAD_REQUEST);
  if (request.method === 'ping') return success({ ready: true });
  if (request.method === 'resolve') return await resolveWorkspace(request, services.registry);
  if (request.method === 'attach') return await attachSession(request, services);
  return failure(ERROR_CODES.UNSUPPORTED_METHOD);
}

/**
 * Canonicalize an existing directory and create (or reuse) its workspace.
 * Returns the resolved identity only; `created` stays false because the
 * registry interface does not report whether the record already existed.
 */
async function resolveWorkspace(request, registry) {
  if (!isAbsoluteDirectoryPath(request.cwd)) return failure(ERROR_CODES.BAD_REQUEST);
  let canonical;
  try {
    canonical = await realpath(request.cwd);
    if (!(await stat(canonical)).isDirectory()) return failure(ERROR_CODES.INVALID_CWD);
  } catch {
    return failure(ERROR_CODES.INVALID_CWD);
  }
  let workspace;
  try {
    workspace = await registry.create(canonical);
  } catch {
    return failure(ERROR_CODES.RESOLVE_FAILED);
  }
  if (!isObject(workspace) || !isUsableString(workspace.id) || !isUsableString(workspace.path)) {
    return failure(ERROR_CODES.RESOLVE_FAILED);
  }
  const path = String(workspace.path);
  if (path !== canonical) return failure(ERROR_CODES.RESOLVE_FAILED);
  return success({ id: String(workspace.id), path, created: false });
}

/**
 * Prove a completed ordinary root session belongs to `cwd` and attach it.
 *
 * Membership is validated before any registry mutation: the session must exist
 * in `sessionPersistence.list()`, carry no fork parent / subagent origin /
 * positive delegation depth, and its recorded cwd must canonicalize to the
 * requested cwd. Only then is the workspace created and the session attached;
 * the result is read back from `sessionIds` before success is claimed.
 */
async function attachSession(request, { registry, persistence }) {
  if (!isAbsoluteDirectoryPath(request.cwd)) return failure(ERROR_CODES.BAD_REQUEST);
  if (!isUsableString(request.sessionId)) return failure(ERROR_CODES.BAD_REQUEST);
  const sessionId = request.sessionId;
  let requestedWorkspaceId;
  if (request.workspaceId !== undefined) {
    if (!isUsableString(request.workspaceId)) return failure(ERROR_CODES.BAD_REQUEST);
    requestedWorkspaceId = request.workspaceId;
  }

  let canonicalCwd;
  try {
    canonicalCwd = await realpath(request.cwd);
  } catch {
    return failure(ERROR_CODES.INVALID_CWD);
  }

  let snapshots;
  try {
    snapshots = await persistence.list();
  } catch {
    return failure(ERROR_CODES.INTERNAL);
  }
  if (!Array.isArray(snapshots)) return failure(ERROR_CODES.INTERNAL);

  const snapshot = snapshots.find((entry) => isObject(entry)
    && isObject(entry.header)
    && String(entry.header.id) === sessionId);
  if (snapshot === undefined) return failure(ERROR_CODES.UNKNOWN_SESSION);

  const header = snapshot.header;
  if (header.parentSession !== undefined && header.parentSession !== null) return failure(ERROR_CODES.NOT_ROOT_SESSION);
  if (header.origin === 'subagent') return failure(ERROR_CODES.NOT_ROOT_SESSION);
  if (typeof header.delegationDepth === 'number' && header.delegationDepth > 0) return failure(ERROR_CODES.NOT_ROOT_SESSION);
  if (!isUsableString(header.cwd)) return failure(ERROR_CODES.CWD_MISMATCH);
  let sessionCwd;
  try {
    sessionCwd = await realpath(header.cwd);
  } catch {
    return failure(ERROR_CODES.CWD_MISMATCH);
  }
  if (sessionCwd !== canonicalCwd) return failure(ERROR_CODES.CWD_MISMATCH);

  let workspace;
  try {
    workspace = await registry.create(canonicalCwd);
  } catch {
    return failure(ERROR_CODES.ATTACH_FAILED);
  }
  if (!isObject(workspace) || !isUsableString(workspace.id)) return failure(ERROR_CODES.ATTACH_FAILED);
  if (requestedWorkspaceId !== undefined && String(workspace.id) !== requestedWorkspaceId) {
    return failure(ERROR_CODES.WORKSPACE_MISMATCH);
  }

  try {
    await workspace.attachSession(sessionId);
  } catch {
    return failure(ERROR_CODES.ATTACH_FAILED);
  }

  const sessionIds = workspace.sessionIds;
  const path = workspace.path;
  if (!Array.isArray(sessionIds) || !sessionIds.some((value) => String(value) === sessionId)) {
    return failure(ERROR_CODES.ATTACH_FAILED);
  }
  if (!isUsableString(path) || String(path) !== canonicalCwd) return failure(ERROR_CODES.ATTACH_FAILED);
  return success({ id: String(workspace.id), path: String(path), sessionId, bound: true });
}

function success(value) {
  return { ok: true, value };
}

function failure(error) {
  return { ok: false, error };
}

/**
 * Write one response line and resolve once the write is handed to the OS, so
 * shutdown never truncates a response that was already computed.
 */
function send(socket, message) {
  return new Promise((resolve) => {
    let settled = false;
    const settle = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
    try {
      if (socket.destroyed || socket.writableEnded) {
        settle();
        return;
      }
      socket.once('close', settle);
      socket.end(`${JSON.stringify(message)}\n`, settle);
    } catch {
      socket.destroy();
      settle();
    }
  });
}

function sendResult(socket, id, value) {
  return send(socket, { version: PROTOCOL_VERSION, id, ok: true, value });
}

function sendError(socket, id, error) {
  const code = ERROR_CODE_SET.has(error) ? error : ERROR_CODES.INTERNAL;
  return send(socket, { version: PROTOCOL_VERSION, id, ok: false, error: code });
}

/**
 * Stop accepting, destroy connections that have not started a request, await
 * in-flight requests within a bounded grace, destroy what remains, and remove
 * only this bridge's own socket inode. Never throws.
 */
async function shutdown(server, socketPath, state) {
  state.closing = true;
  try {
    const accepted = new Promise((resolve) => {
      try {
        server.close(() => resolve());
      } catch {
        resolve();
      }
    });
    for (const socket of [...state.idle]) socket.destroy();
    await settleWithin(Promise.allSettled([...state.inFlight]), SHUTDOWN_GRACE_MS);
    for (const socket of [...state.connections]) socket.destroy();
    await settleWithin(accepted, SHUTDOWN_GRACE_MS);
  } catch {
    // Disposal is best-effort and must never throw into the host lifecycle.
  }
  removeOwnSocket(socketPath, state.identity);
  if (state.bindPath !== null) removeOwnSocket(state.bindPath, state.identity);
}

/**
 * Start the bridge for tests and for `apply`.
 *
 * @param ctx - Cordis context carrying `workspaceRegistry` and
 *   `sessionPersistence`.
 * @param config - `{ socketPath }`; `socketPath` must be an absolute path.
 * @returns an idempotent `async close()` that performs the same disposal as
 *   the plugin lifecycle hook.
 */
export async function startWorkspaceBridge(ctx, config) {
  const socketPath = config?.socketPath;
  if (!isUsableString(socketPath)) {
    throw new Error('workspace bridge requires config.socketPath as a non-empty string');
  }
  if (!isAbsolute(socketPath)) {
    throw new Error(`workspace bridge requires an absolute config.socketPath, received '${socketPath}'`);
  }
  if (Buffer.byteLength(socketPath) > UNIX_SOCKET_PATH_BUDGET) {
    throw new Error(`workspace bridge socket path exceeds this platform's Unix socket limit (${UNIX_SOCKET_PATH_BUDGET} bytes): '${socketPath}'`);
  }
  if (typeof process.getuid !== 'function') {
    throw new Error('workspace bridge requires a POSIX host with Unix domain socket support');
  }
  const registry = ctx?.workspaceRegistry;
  const persistence = ctx?.sessionPersistence;
  if (registry === undefined || registry === null || typeof registry.create !== 'function') {
    throw new Error('workspace bridge requires the workspaceRegistry service');
  }
  if (persistence === undefined || persistence === null || typeof persistence.list !== 'function') {
    throw new Error('workspace bridge requires the sessionPersistence service');
  }

  assertPrivateParentDirectory(socketPath);
  assertSocketPathAvailable(socketPath);

  const state = {
    closing: false,
    identity: null,
    bindPath: null,
    connections: new Set(),
    idle: new Set(),
    inFlight: new Set(),
    closePromise: undefined,
  };

  const server = createServer((socket) => {
    acceptConnection(socket, state, { registry, persistence });
  });

  // Bind under a private random name when the platform limit allows it, then
  // publish at the requested path. The name libuv records for close() is then
  // never a path a foreign file could occupy, so a replaced socket path
  // survives shutdown untouched. A path so long that no private bind name fits
  // is bound directly and only enjoys the inode check below.
  const privateBindPath = join(dirname(socketPath), `.w${randomBytes(5).toString('hex')}`);
  const privateBind = Buffer.byteLength(privateBindPath) <= UNIX_SOCKET_PATH_BUDGET;
  const bindPath = privateBind ? privateBindPath : socketPath;
  state.bindPath = privateBind ? bindPath : null;
  await listen(server, bindPath);
  server.on('error', () => {
    // Post-listen transport errors are cleanup's concern; the bridge must
    // never crash the host with an unhandled server error.
  });
  try {
    chmodSync(bindPath, 0o600);
    state.identity = readSocketIdentity(bindPath);
    if (privateBind) publishSocket(bindPath, socketPath);
  } catch (error) {
    await shutdown(server, socketPath, state);
    throw new Error(`workspace bridge could not secure its socket at '${socketPath}' (${error?.code ?? 'unknown error'})`, { cause: error });
  }

  return function close() {
    state.closePromise ??= shutdown(server, socketPath, state);
    return state.closePromise;
  };
}

/**
 * Mount the bridge and tear it down with the plugin's fiber. The `dispose`
 * listener is the documented lifecycle hook; the Cordis effect is what
 * actually runs when the owning fiber unloads on Cordis 4. If the fiber is
 * disposed while the socket is still starting, no hook can be registered, so
 * the bridge closes itself instead of leaking a listening server.
 *
 * @param ctx - Cordis context carrying both injected services.
 * @param config - `{ socketPath }` absolute socket path.
 */
export async function apply(ctx, config) {
  const close = await startWorkspaceBridge(ctx, config);
  let hooked = false;
  try {
    ctx.on('dispose', async () => {
      await close();
    });
    hooked = true;
  } catch {
    // The fiber was disposed during startup.
  }
  if (typeof ctx.effect === 'function') {
    try {
      ctx.effect(() => () => close(), 'deepseek-delegate.workspace-bridge');
      hooked = true;
    } catch {
      // The fiber was disposed during startup.
    }
  }
  if (!hooked) await close();
}
