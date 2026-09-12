/**
 * Local authenticated dsh web-host fixture for the deepseek-delegate tests.
 *
 * It implements exactly the surface the CLI is allowed to use, verified against
 * the installed dsh sources:
 * - `GET /?token=...` performs the ordinary root-token exchange and answers a
 *   same-origin 303 with a signed-cookie stand-in.
 * - `POST /api/<namespace>/<method>` accepts the
 *   `{ type: 'client-request', rpcId, method, payload }` envelope and answers
 *   `{ type: 'server-response', rpcId, result }`.
 * - `workspace/create` registers or resolves one canonical directory and returns
 *   the workspace view (with `sessionIds`), which is also the membership read.
 * - `session/page` is the read-only existence probe: a proper session address
 *   with the `throughSeq: -1, maxMessages: 1` sentinel answers an empty page for
 *   a persisted session and `session/not-found` for an unknown one, without any
 *   mutation or Agent activation.
 * - `session/create` adopts one persisted session id, attaches it to a
 *   workspace, and — like the real host — actually creates the session when the
 *   requested id is unknown, so a client that skips the probe is detectable.
 *
 * No real service, model, or credential is involved; the launch token is a test
 * constant and is never logged by the fixture.
 */
import { createServer } from 'node:http';
import { existsSync, readFileSync } from 'node:fs';

/** Default launch token used by the fixture. */
export const TEST_TOKEN = 'test-launch-token-not-a-secret';

function hasChildExited(pidFile) {
  if (!pidFile || !existsSync(pidFile)) return false;
  const pid = Number(readFileSync(pidFile, 'utf8'));
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try { process.kill(pid, 0); return false; }
  catch (error) { return error.code === 'ESRCH'; }
}

/** Session ids the fixture reports as persisted unless overridden. */
export const DEFAULT_PERSISTED_SESSIONS = ['session-mock-0001', 'session-existing-42'];

/** Synthetic credential markers the hostile-error mode tries to leak. */
export const HOSTILE_ERROR_MARKERS = Object.freeze({
  code: 'session/leak-TOKEN_MARKER_7788',
  message: 'open http://127.0.0.1:9/?token=URL_LEAK_MARKER_9911 (cookie=COOKIE_LEAK_MARKER_2233)',
});

function json(res, status, body) {
  const text = JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(text) });
  res.end(text);
}

function okResponse(res, rpcId, value) {
  json(res, 200, { type: 'server-response', rpcId, result: { ok: true, value } });
}

function errorResponse(res, rpcId, code, message) {
  json(res, 200, { type: 'server-response', rpcId, result: { ok: false, error: { code, message, details: {} } } });
}

function workspaceView(state, workspace) {
  return {
    workspaceId: workspace.workspaceId,
    path: state.workspacePathOverride ?? workspace.path,
    title: workspace.title,
    sessionIds: [...workspace.sessionIds],
    createdAt: workspace.createdAt,
    updatedAt: workspace.updatedAt,
  };
}

/**
 * Start the fixture on an ephemeral loopback port.
 *
 * Options:
 * - `token` — accepted launch token (default {@link TEST_TOKEN}).
 * - `authMode` — `ok` | `reject` | `hang` | `cross-origin` | `no-cookie`.
 * - `crossOriginLocation` — Location answered by `authMode: 'cross-origin'`.
 * - `sessionCreateMode` — `ok` | `error` | `wrong-id` | `http500` | `hang` |
 *   `malformed` | `hostile-error`.
 * - `sessionPageMode` — `ok` | `malformed`.
 * - `persistedSessions` — exact persisted session ids (default
 *   {@link DEFAULT_PERSISTED_SESSIONS}).
 * - `attachMembership` — when false, `session/create` succeeds but the workspace
 *   read-back never lists the session.
 * - `workspacePathOverride` — test-only path returned in every workspace view,
 *   so a canonical read-back mismatch can be exercised.
 * - `childExitMarker` — path recorded into every `session/create` request log so
 *   a test can prove adoption happened after the child process exited.
 * - `workspaces` — pre-registered `{ workspaceId, path, title?, sessionIds? }`.
 * - `preflightDelayMs`, `responseDelayMs` — per-phase delays.
 *
 * @returns the fixture handle: `url`, `launchUrl`, `requests`, `state`, `close()`.
 */
export async function startMockWeb(options = {}) {
  const state = {
    token: options.token ?? TEST_TOKEN,
    authMode: options.authMode ?? 'ok',
    crossOriginLocation: options.crossOriginLocation ?? 'http://off-origin.invalid/',
    sessionCreateMode: options.sessionCreateMode ?? 'ok',
    sessionPageMode: options.sessionPageMode ?? 'ok',
    persistedSessions: new Set(options.persistedSessions ?? DEFAULT_PERSISTED_SESSIONS),
    attachMembership: options.attachMembership ?? true,
    workspacePathOverride: options.workspacePathOverride ?? null,
    preflightDelayMs: options.preflightDelayMs ?? 0,
    responseDelayMs: options.responseDelayMs ?? 0,
    childExitMarker: options.childExitMarker ?? null,
    cookieName: 'dsh-auth-mockfixture',
    cookieValue: 'mock-signed-cookie-value',
    workspaces: new Map(),
    requests: [],
    nextWorkspace: 1,
    sessionPageCalls: 0,
    sessionCreateCalls: 0,
    sessionCreates: [],
  };
  for (const entry of options.workspaces ?? []) {
    state.workspaces.set(entry.path, {
      workspaceId: entry.workspaceId,
      path: entry.path,
      title: entry.title ?? entry.path,
      sessionIds: [...(entry.sessionIds ?? [])],
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    });
  }
  const cookieHeader = `${state.cookieName}=${state.cookieValue}`;
  const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

  const server = createServer(async (req, res) => {
    let url;
    try {
      url = new URL(req.url, 'http://127.0.0.1');
    } catch {
      res.writeHead(400);
      res.end('bad url');
      return;
    }

    if (req.method === 'GET' && url.pathname === '/') {
      state.requests.push({ kind: 'auth', hasToken: url.searchParams.has('token') });
      if (state.authMode === 'hang') return;
      if (state.preflightDelayMs > 0) await sleep(state.preflightDelayMs);
      if (state.authMode === 'cross-origin') {
        res.writeHead(303, {
          location: state.crossOriginLocation,
          'set-cookie': `${cookieHeader}; Path=/; HttpOnly; SameSite=Strict`,
        });
        res.end();
        return;
      }
      if (url.searchParams.get('token') === state.token) {
        const headers = { location: '/' };
        if (state.authMode !== 'no-cookie') {
          headers['set-cookie'] = `${cookieHeader}; Path=/; HttpOnly; SameSite=Strict`;
        }
        res.writeHead(303, headers);
        res.end();
        return;
      }
      res.writeHead(401, { 'content-type': 'text/plain; charset=utf-8' });
      res.end('dsh web authentication required; reopen the URL printed by dsh web.\n');
      return;
    }

    if (req.method === 'POST' && url.pathname.startsWith('/api/')) {
      const method = url.pathname.slice('/api/'.length);
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      let body;
      try {
        body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      } catch {
        res.writeHead(400);
        res.end('bad json');
        return;
      }
      const authenticated = req.headers.cookie === cookieHeader;
      const record = {
        kind: 'rpc',
        method,
        authenticated,
        rpcId: body?.rpcId ?? null,
        payload: body?.payload ?? null,
        childExited: hasChildExited(options.childPidFile),
        rpcMethodMatches: body?.method === method,
        envelope: body?.type === 'client-request',
      };
      state.requests.push(record);
      if (!authenticated) {
        res.writeHead(401, { 'content-type': 'text/plain; charset=utf-8' });
        res.end('unauthorized');
        return;
      }
      if (state.responseDelayMs > 0) await sleep(state.responseDelayMs);

      if (method === 'workspace/create') {
        const path = body?.payload?.path;
        let workspace = state.workspaces.get(path);
        let created = false;
        if (workspace === undefined) {
          workspace = {
            workspaceId: `workspace-${state.nextWorkspace++}`,
            path,
            title: path,
            sessionIds: [],
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          };
          state.workspaces.set(path, workspace);
          created = true;
        }
        okResponse(res, body.rpcId, { workspace: workspaceView(state, workspace), created });
        return;
      }

      if (method === 'session/page') {
        state.sessionPageCalls += 1;
        const payload = body?.payload ?? {};
        const address = payload.address;
        const requestedId = address !== null && typeof address === 'object' ? address.sessionId : undefined;
        const pageRequestValid = address !== null && typeof address === 'object' && address.kind === 'session'
          && typeof requestedId === 'string' && requestedId !== ''
          && Number.isSafeInteger(payload.throughSeq) && payload.throughSeq >= -1
          && Number.isSafeInteger(payload.maxMessages) && payload.maxMessages > 0;
        record.sessionId = typeof requestedId === 'string' ? requestedId : null;
        record.addressKind = address !== null && typeof address === 'object' ? address.kind ?? null : null;
        record.sentinel = payload.throughSeq === -1 && payload.maxMessages === 1;
        if (!pageRequestValid) {
          record.persisted = null;
          errorResponse(res, body.rpcId, 'gateway/bad-request', 'not a valid session page request');
          return;
        }
        record.persisted = state.persistedSessions.has(requestedId);
        if (!record.persisted) {
          errorResponse(res, body.rpcId, 'session/not-found', `session "${requestedId}" not found`);
          return;
        }
        if (state.sessionPageMode === 'malformed') {
          okResponse(res, body.rpcId, { records: 'not-an-array', hasMore: 'not-a-boolean' });
          return;
        }
        okResponse(res, body.rpcId, { records: [], hasMore: false });
        return;
      }

      if (method === 'session/create') {
        state.sessionCreateCalls += 1;
        const requestedId = body?.payload?.sessionId;
        record.sessionId = typeof requestedId === 'string' ? requestedId : null;
        record.persistedBefore = state.persistedSessions.has(requestedId);
        record.created = false;
        if (state.sessionCreateMode === 'hang') return;
        if (state.sessionCreateMode === 'http500') {
          res.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' });
          res.end('handler failure: fixture');
          return;
        }
        if (state.sessionCreateMode === 'error') {
          errorResponse(res, body.rpcId, 'session/not-found', 'the persisted session was not found');
          return;
        }
        if (state.sessionCreateMode === 'hostile-error') {
          errorResponse(res, body.rpcId, HOSTILE_ERROR_MARKERS.code, HOSTILE_ERROR_MARKERS.message);
          return;
        }
        if (state.sessionCreateMode === 'malformed') {
          json(res, 200, { type: 'server-response', rpcId: 'some-other-rpc-id', result: { ok: true, value: {} } });
          return;
        }
        // The real host creates a session for an unknown id; keeping that here is
        // what makes a missing existence probe visible to the tests.
        const sessionId = state.sessionCreateMode === 'wrong-id' ? 'session-somebody-else' : requestedId;
        state.persistedSessions.add(requestedId);
        record.created = !record.persistedBefore;
        state.sessionCreates.push({ sessionId, requestedSessionId: requestedId, created: record.created });
        const workspace = [...state.workspaces.values()].find((entry) => entry.workspaceId === body?.payload?.workspaceId);
        if (workspace !== undefined && state.attachMembership && !workspace.sessionIds.includes(sessionId)) {
          workspace.sessionIds.unshift(sessionId);
        }
        okResponse(res, body.rpcId, { sessionId });
        return;
      }

      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
      res.end('not found');
      return;
    }

    res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
    res.end('not found');
  });

  await new Promise((resolveListen) => server.listen(0, '127.0.0.1', resolveListen));
  const port = server.address().port;
  const url = `http://127.0.0.1:${port}/`;

  return {
    url,
    port,
    state,
    requests: state.requests,
    launchUrl: `${url}?token=${encodeURIComponent(state.token)}`,
    rpcRequests(method) {
      return state.requests.filter((entry) => entry.kind === 'rpc' && entry.method === method);
    },
    async close() {
      if (typeof server.closeAllConnections === 'function') server.closeAllConnections();
      await new Promise((resolveClose) => server.close(resolveClose));
    },
  };
}
