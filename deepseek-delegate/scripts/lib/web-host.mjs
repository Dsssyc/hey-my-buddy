/**
 * Local dsh web-host client used by deepseek-delegate for workspace grouping.
 *
 * The only supported way to attach a persisted headless session to a workspace
 * is the already-running, authenticated web host's own RPC surface. This module
 * speaks that surface with normal HTTP:
 *
 * - `GET /?token=...` performs the ordinary DSH root-token exchange and yields
 *   the signed browser cookie; the token never travels in a query to an RPC
 *   endpoint, an Authorization header, or any log line.
 * - `POST /api/<namespace>/<method>` carries one
 *   `{ type: 'client-request', rpcId, method, payload }` envelope and expects
 *   `{ type: 'server-response', rpcId, result }` back.
 * - `workspace/create` registers or idempotently resolves the canonical `--cwd`
 *   and returns the workspace view (including current `sessionIds`), so it also
 *   serves as the supported membership read-back.
 * - `session/page` with `{ address: { kind: 'session', sessionId }, throughSeq: -1,
 *   maxMessages: 1 }` is the read-only existence probe: the installed host opens
 *   the persisted session source without activating an Agent and rejects an
 *   unknown or non-ordinary identity. It always runs before any mutation, so an
 *   unknown `--attach-session` id cannot be silently created by the host.
 * - `session/create` with `{ sessionId, workspaceId }` adopts the persisted
 *   ordinary session into the live host and calls `workspace.attachSession`.
 *   It never submits a prompt, never selects a model, and never forks.
 *
 * `dsh-storage-json` explicitly has no cross-process locking and its registry,
 * cache, and feed files are host-local, so this module must never write storage
 * files directly. Every mutation goes through this authenticated host RPC.
 *
 * Only node builtins are used (global fetch plus AbortSignal.timeout), and every
 * error message is built from fixed local text plus allowlisted dsh error codes:
 * no token, cookie, launch-URL path, remote message, or credential-bearing URL.
 *
 * @module deepseek-delegate/scripts/lib/web-host
 */
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';

/** Default per-request network bound in seconds. */
export const DEFAULT_WEB_TIMEOUT_SECONDS = 15;
export const MIN_WEB_TIMEOUT_SECONDS = 1;
export const MAX_WEB_TIMEOUT_SECONDS = 120;

/** Configuration/setup failure: reported on stderr with exit code 2, no run. */
export class WebSetupError extends Error {
  constructor(message) {
    super(message);
    this.name = 'WebSetupError';
  }
}

/** Authentication or transport failure against a configured host. */
export class WebRpcError extends Error {
  constructor(message, code = 'web-error') {
    super(message);
    this.name = 'WebRpcError';
    this.code = code;
  }
}

/**
 * Remote error codes this client is willing to echo. The list is the installed
 * dsh `RemoteError` vocabulary observed in the workspace/session controllers;
 * anything else is reported as `rpc-error`, so an unexpected or hostile host
 * cannot smuggle text through `error.code` into output.
 */
const KNOWN_RPC_ERROR_CODES = new Set([
  'agent-preset/conflict',
  'agent-preset/invalid',
  'agent-preset/locked',
  'agent-preset/not-found',
  'agent-preset/read-only',
  'credential/rejected',
  'directory-picker/unavailable',
  'gateway/bad-request',
  'gateway/cancelled',
  'gateway/internal',
  'session/agent-busy',
  'session/attachment-invalid',
  'session/conflict',
  'session/fork-unavailable',
  'session/invalid-time-zone',
  'session/model-unavailable',
  'session/not-found',
  'session/queue-item-not-found',
  'session/steer-unavailable',
  'session/title-invalid',
  'session/workspace-attach-failed',
  'settings/conflict',
  'settings/rejected',
  'subagent/attachment-invalid',
  'subagent/catalog-diagnostic',
  'subagent/delivery-unavailable',
  'subagent/invalid-time-zone',
  'subagent/not-found',
  'subagent/not-resumable',
  'subagent/parent-unavailable',
  'subagent/projections-unavailable',
  'subagent/unauthorized',
  'workspace-file/not-directory',
  'workspace-file/not-found',
  'workspace-file/not-regular-file',
  'workspace-file/not-text',
  'workspace-file/outside-workspace',
  'workspace-file/too-large',
  'workspace-file/unknown-workspace',
  'workspace-file/unsupported-address',
  'workspace/invalid-path',
  'workspace/move-invalid',
  'workspace/name-conflict',
  'workspace/not-found',
]);

/** Only well-formed `namespace/method` labels may appear in local diagnostics. */
const SAFE_METHOD_LABEL = /^[a-z][a-zA-Z0-9-]*\/[a-zA-Z][a-zA-Z0-9-]*$/;

/** Render a caller-supplied method for a diagnostic without echoing arbitrary text. */
function methodLabel(method) {
  return typeof method === 'string' && SAFE_METHOD_LABEL.test(method) ? method : 'the requested method';
}

/** Collapse a remote error code to an allowlisted code or the generic `rpc-error`. */
function remoteErrorCode(error) {
  const code = error?.code;
  return typeof code === 'string' && KNOWN_RPC_ERROR_CODES.has(code) ? code : 'rpc-error';
}

/** Loopback hostnames accepted for this local-headless integration. */
function isLoopbackHostname(hostname) {
  const bare = hostname.startsWith('[') && hostname.endsWith(']') ? hostname.slice(1, -1) : hostname;
  return bare === 'localhost' || bare === '127.0.0.1' || bare === '::1';
}

/** Read one non-comment URL line from a credential file without echoing it. */
function readUrlFile(file, source) {
  const path = resolve(file);
  let text;
  try {
    text = readFileSync(path, 'utf8');
  } catch {
    throw new WebSetupError(`${source} could not be read: ${path}`);
  }
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter((line) => line !== '' && !line.startsWith('#'));
  if (lines.length === 0) throw new WebSetupError(`${source} is empty: ${path}`);
  if (lines.length > 1) throw new WebSetupError(`${source} must contain exactly one URL line: ${path}`);
  return lines[0];
}

/** Validate and normalize one saved launch URL; never returns a printable token. */
function parseLaunchUrl(raw, source) {
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new WebSetupError(`${source} is not a valid absolute URL`);
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new WebSetupError(`${source} must be an http(s) URL`);
  }
  if (url.username !== '' || url.password !== '') {
    throw new WebSetupError(`${source} must not embed a username or password`);
  }
  if (!isLoopbackHostname(url.hostname)) {
    throw new WebSetupError(`${source} must point at localhost, 127.0.0.1, or [::1] for this local-headless integration`);
  }
  if (url.pathname !== '/') {
    throw new WebSetupError(`${source} must point at the origin root (/), not a path`);
  }
  const tokens = url.searchParams.getAll('token');
  if (tokens.length > 1) throw new WebSetupError(`${source} carries more than one token parameter`);
  return {
    origin: url.origin,
    label: url.origin,
    source,
    token: tokens.length === 1 ? tokens[0] : null,
  };
}

/** The default credential file: `${XDG_CONFIG_HOME:-~/.config}/deepseek-delegate/web-url`. */
export function defaultWebUrlFile(env) {
  const xdg = env.XDG_CONFIG_HOME !== undefined && env.XDG_CONFIG_HOME.trim() !== ''
    ? resolve(env.XDG_CONFIG_HOME)
    : join(homedir(), '.config');
  return join(xdg, 'deepseek-delegate', 'web-url');
}

/**
 * Resolve the web target with one documented precedence:
 * `--dsh-web-url` > `--dsh-web-url-file` > `DSH_WEB_URL` > `DSH_WEB_URL_FILE`
 * > the default credential file.
 *
 * @param values - parsed CLI values (`dsh-web-url`, `dsh-web-url-file`).
 * @param env - environment object (`DSH_WEB_URL`, `DSH_WEB_URL_FILE`, `XDG_CONFIG_HOME`).
 * @returns `{ origin, label, source, token }` or throws {@link WebSetupError}.
 */
export function resolveWebTarget(values, env) {
  if (values['dsh-web-url'] !== undefined) {
    if (values['dsh-web-url'].trim() === '') throw new WebSetupError('--dsh-web-url must not be blank');
    return parseLaunchUrl(values['dsh-web-url'].trim(), '--dsh-web-url');
  }
  if (values['dsh-web-url-file'] !== undefined) {
    if (values['dsh-web-url-file'].trim() === '') throw new WebSetupError('--dsh-web-url-file must not be blank');
    const source = `--dsh-web-url-file ${resolve(values['dsh-web-url-file'])}`;
    return parseLaunchUrl(readUrlFile(values['dsh-web-url-file'], source), source);
  }
  if (env.DSH_WEB_URL !== undefined) {
    if (env.DSH_WEB_URL.trim() === '') throw new WebSetupError('DSH_WEB_URL must not be blank');
    return parseLaunchUrl(env.DSH_WEB_URL.trim(), 'DSH_WEB_URL');
  }
  if (env.DSH_WEB_URL_FILE !== undefined) {
    if (env.DSH_WEB_URL_FILE.trim() === '') throw new WebSetupError('DSH_WEB_URL_FILE must not be blank');
    const source = `DSH_WEB_URL_FILE ${resolve(env.DSH_WEB_URL_FILE)}`;
    return parseLaunchUrl(readUrlFile(env.DSH_WEB_URL_FILE, source), source);
  }
  const fallback = defaultWebUrlFile(env);
  let raw;
  try {
    raw = readUrlFile(fallback, 'the default dsh web URL file');
  } catch (error) {
    if (error instanceof WebSetupError) {
      throw new WebSetupError(`${error.message}; save the URL printed by 'dsh web' (with ?token=...) there, pass --dsh-web-url/--dsh-web-url-file or DSH_WEB_URL/DSH_WEB_URL_FILE, or use --no-workspace for an ungrouped run`);
    }
    throw error;
  }
  return parseLaunchUrl(raw, `the default dsh web URL file ${fallback}`);
}

/** Origin-only fetch wrapper with a bounded timeout and no token in errors. */
async function fetchBounded(url, options, timeoutMs, label) {
  try {
    return await fetch(url, { ...options, signal: AbortSignal.timeout(timeoutMs) });
  } catch (error) {
    if (error?.name === 'TimeoutError' || error?.name === 'AbortError') {
      throw new WebRpcError(`request to the dsh web host at ${label} timed out after ${Math.round(timeoutMs / 1000)}s`, 'timeout');
    }
    const code = error?.cause?.code ?? error?.code;
    throw new WebRpcError(`could not reach the dsh web host at ${label}${code === undefined ? '' : ` (${code})`}`, 'network');
  }
}

function isRedirect(status) {
  return status === 301 || status === 302 || status === 303 || status === 307 || status === 308;
}

/** The signed cookie name/value pair from a token-exchange response. */
function sessionCookie(response) {
  const raw = typeof response.headers.getSetCookie === 'function'
    ? response.headers.getSetCookie()
    : [response.headers.get('set-cookie')].filter((value) => value !== null);
  for (const entry of raw) {
    const pair = entry.split(';', 1)[0].trim();
    const at = pair.indexOf('=');
    if (at > 0) return pair;
  }
  return null;
}

/**
 * Authenticate against a running host with the ordinary root-token exchange.
 *
 * Only a same-origin redirect is respected; any other destination is refused
 * before it is followed. The cookie stays in memory and is never written or
 * logged.
 *
 * @param target - value from {@link resolveWebTarget}.
 * @param options - `{ timeoutMs }`.
 * @returns `{ origin, label, cookie }` for {@link webRpc}.
 */
export async function connectWebHost(target, { timeoutMs = DEFAULT_WEB_TIMEOUT_SECONDS * 1000 } = {}) {
  if (target.token === null || target.token === '') {
    throw new WebSetupError(`${target.source} has no launch token; save the full URL printed by 'dsh web' (it carries ?token=...) instead of a bare origin`);
  }
  const authUrl = `${target.origin}/?token=${encodeURIComponent(target.token)}`;
  const response = await fetchBounded(authUrl, { method: 'GET', redirect: 'manual' }, timeoutMs, target.label);
  if (isRedirect(response.status)) {
    const location = response.headers.get('location');
    if (location === null || location === '') {
      throw new WebSetupError(`the dsh web host at ${target.label} redirected authentication without a Location`);
    }
    let destination;
    try {
      destination = new URL(location, target.origin);
    } catch {
      throw new WebSetupError(`the dsh web host at ${target.label} redirected authentication to an invalid Location`);
    }
    if (destination.origin !== target.origin) {
      throw new WebSetupError(`the dsh web host at ${target.label} redirected authentication off-origin; refusing to follow it`);
    }
    const cookie = sessionCookie(response);
    if (cookie === null) {
      throw new WebSetupError(`the dsh web host at ${target.label} did not issue a session cookie; the saved token may belong to a previous process`);
    }
    await response.body?.cancel().catch(() => {});
    return { origin: target.origin, label: target.label, cookie };
  }
  await response.body?.cancel().catch(() => {});
  if (response.status === 401 || response.status === 403) {
    throw new WebSetupError(`the dsh web host at ${target.label} rejected the saved launch token (HTTP ${response.status}); it may have expired or belong to a previous process, so save a fresh URL from 'dsh web'`);
  }
  throw new WebSetupError(`the dsh web host at ${target.label} answered HTTP ${response.status} during authentication instead of the expected token exchange`);
}

/**
 * Invoke one host Remote method and unwrap its `RemoteResult`.
 *
 * @param host - value from {@link connectWebHost}.
 * @param method - canonical `<namespace>/<method>` endpoint, e.g. `workspace/create`.
 * @param payload - exact named arguments object.
 * @param options - `{ timeoutMs }`.
 * @returns the unwrapped `result.value`.
 */
export async function webRpc(host, method, payload, { timeoutMs = DEFAULT_WEB_TIMEOUT_SECONDS * 1000 } = {}) {
  const rpcId = randomUUID();
  const body = JSON.stringify({ type: 'client-request', rpcId, method, payload });
  const response = await fetchBounded(`${host.origin}/api/${method}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', cookie: host.cookie },
    body,
    redirect: 'error',
  }, timeoutMs, host.label);
  if (response.status === 401) {
    await response.body?.cancel().catch(() => {});
    throw new WebSetupError(`the dsh web host at ${host.label} rejected the browser session for ${methodLabel(method)} (HTTP 401); save a fresh URL from 'dsh web' and retry`);
  }
  if (response.status !== 200) {
    await response.body?.cancel().catch(() => {});
    throw new WebRpcError(`the dsh web host at ${host.label} answered HTTP ${response.status} for ${methodLabel(method)}`, `http-${response.status}`);
  }
  let message;
  try {
    message = await response.json();
  } catch {
    throw new WebRpcError(`the dsh web host at ${host.label} returned a non-JSON response for ${methodLabel(method)}`, 'bad-response');
  }
  if (message?.type !== 'server-response' || message.rpcId !== rpcId || message.result === null || typeof message.result !== 'object') {
    throw new WebRpcError(`the dsh web host at ${host.label} returned a malformed response for ${methodLabel(method)}`, 'bad-response');
  }
  const result = message.result;
  if (result.ok !== true) {
    const code = remoteErrorCode(result.error);
    throw new WebRpcError(`${methodLabel(method)} failed on the dsh web host at ${host.label} (${code})`, code);
  }
  return result.value;
}

/**
 * Register or idempotently resolve the workspace for one canonical directory.
 *
 * @param host - authenticated host.
 * @param cwd - canonical (`realpath`) absolute directory.
 * @param options - `{ timeoutMs }`.
 * @returns `{ id, path, created }`.
 */
export async function resolveWorkspace(host, cwd, options = {}) {
  const value = await webRpc(host, 'workspace/create', { path: cwd }, options);
  const workspace = value?.workspace;
  if (workspace === null || typeof workspace !== 'object' || typeof workspace.workspaceId !== 'string' || workspace.workspaceId === '') {
    throw new WebRpcError('workspace/create returned no workspace identity', 'bad-response');
  }
  if (typeof workspace.path !== 'string' || workspace.path === '') {
    throw new WebRpcError('workspace/create returned no canonical workspace path', 'bad-response');
  }
  if (workspace.path !== cwd) {
    throw new WebRpcError('workspace/create resolved a different canonical path than --cwd; refusing to group into it', 'path-mismatch');
  }
  return { id: workspace.workspaceId, path: workspace.path, created: value.created === true };
}

/**
 * Prove that one persisted ordinary session exists before any mutation.
 *
 * The installed host implements `session/page` as a cold-safe read that opens
 * the session source without activating an Agent and rejects an unknown or
 * non-ordinary identity. Because the real `session/create` would happily create
 * a brand-new session for an unknown id, this probe is what keeps
 * `--attach-session` honest across different `DSH_HOME` values.
 *
 * @param host - authenticated host.
 * @param sessionId - captured plain session id.
 * @param options - `{ timeoutMs }`.
 */
async function assertSessionExists(host, sessionId, options) {
  const page = await webRpc(host, 'session/page', {
    address: { kind: 'session', sessionId },
    throughSeq: -1,
    maxMessages: 1,
  }, options);
  if (page === null || typeof page !== 'object' || !Array.isArray(page.records) || typeof page.hasMore !== 'boolean') {
    throw new WebRpcError('session/page did not return the expected read-only page shape', 'bad-response');
  }
}

/**
 * Adopt one persisted ordinary session into a workspace through the host, then
 * verify membership through the host's own read-back.
 *
 * An unknown id is refused by the read-only `session/page` existence probe
 * before `session/create` runs, so adoption can never silently invent a session
 * or mask a different `DSH_HOME`. `session/create` is then idempotent adoption:
 * it resumes the host Agent on the persisted session and attaches it, but
 * submits no prompt and never selects a model. Membership is only reported as
 * bound after the returned session id matches, the read-back reports the same
 * workspace and the requested canonical `--cwd`, and `workspace/create` lists
 * that id.
 *
 * @param host - authenticated host.
 * @param request - `{ sessionId, workspaceId, cwd }`.
 * @param options - `{ timeoutMs }`.
 * @returns `{ id, path, sessionId, bound: true }`.
 */
export async function adoptSession(host, { sessionId, workspaceId, cwd }, options = {}) {
  await assertSessionExists(host, sessionId, options);
  const created = await webRpc(host, 'session/create', { sessionId, workspaceId }, options);
  if (created?.sessionId !== sessionId) {
    throw new WebRpcError('session/create adopted a different session id than the captured one', 'session-mismatch');
  }
  const readBack = await webRpc(host, 'workspace/create', { path: cwd }, options);
  const workspace = readBack?.workspace;
  if (workspace === null || typeof workspace !== 'object' || workspace.workspaceId !== workspaceId) {
    throw new WebRpcError('could not re-read the same workspace after adoption', 'verify-failed');
  }
  if (typeof workspace.path !== 'string' || workspace.path === '' || workspace.path !== cwd) {
    throw new WebRpcError('the workspace read-back did not report the requested canonical --cwd; refusing to claim the binding', 'path-mismatch');
  }
  const sessionIds = Array.isArray(workspace.sessionIds) ? workspace.sessionIds : [];
  if (!sessionIds.includes(sessionId)) {
    throw new WebRpcError('the adopted session is not listed in workspace membership', 'verify-failed');
  }
  return { id: workspaceId, path: workspace.path, sessionId, bound: true };
}
