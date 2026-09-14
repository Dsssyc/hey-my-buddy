/** Private local transport to a plugin using the official workspaceRegistry API. */
import { connect } from 'node:net';
import { lstatSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';

export const DEFAULT_WORKSPACE_TIMEOUT_SECONDS = 15;
export const MIN_WORKSPACE_TIMEOUT_SECONDS = 1;
export const MAX_WORKSPACE_TIMEOUT_SECONDS = 120;
export class WorkspaceError extends Error {}

export function resolveWorkspaceTarget(values, env) {
  const supplied = values['workspace-socket'] ?? env.DSH_WORKSPACE_SOCKET;
  if (supplied !== undefined && supplied.trim() === '') throw new WorkspaceError('workspace socket path must not be blank');
  const socketPath = supplied === undefined
    ? join(resolve(env.DSH_HOME || join(homedir(), '.dsh')), 'deepseek-delegate', 'workspace.sock')
    : resolve(supplied);
  return { socketPath };
}

export function workspaceRpc(host, method, payload = {}, { timeoutMs = 15000 } = {}) {
  try {
    for (const [path, socket] of [[dirname(host.socketPath), false], [host.socketPath, true]]) {
      const info = lstatSync(path);
      if (info.isSymbolicLink() || (socket ? !info.isSocket() : !info.isDirectory())
        || (info.mode & 0o077) !== 0 || info.uid !== process.getuid()) {
        throw new WorkspaceError('workspace socket and its parent must be private and owned by the current user');
      }
    }
  } catch (error) {
    if (error instanceof WorkspaceError) return Promise.reject(error);
    return Promise.reject(new WorkspaceError('workspace bridge is unavailable; install the host plugin and start that dsh profile, or use --no-workspace'));
  }
  return new Promise((accept, reject) => {
    const id = randomUUID();
    const socket = connect(host.socketPath);
    let buffer = '';
    let done = false;
    const finish = (error, value) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      socket.destroy();
      if (error) reject(error); else accept(value);
    };
    const timer = setTimeout(() => finish(new WorkspaceError('workspace bridge request timed out; binding may have completed, retry attachment without rerunning the task')), timeoutMs);
    socket.setEncoding('utf8');
    socket.on('connect', () => socket.write(`${JSON.stringify({ version: 1, id, method, ...payload })}\n`));
    socket.on('error', () => finish(new WorkspaceError('workspace bridge connection failed; start the configured host profile')));
    socket.on('end', () => finish(new WorkspaceError('workspace bridge closed without a complete response')));
    socket.on('data', chunk => {
      buffer += chunk;
      if (Buffer.byteLength(buffer) > 16384) return finish(new WorkspaceError('workspace bridge response exceeds limit'));
      const end = buffer.indexOf('\n');
      if (end < 0) return;
      let message;
      try { message = JSON.parse(buffer.slice(0, end)); } catch { return finish(new WorkspaceError('invalid workspace bridge response')); }
      if (message?.version !== 1 || message.id !== id || typeof message.ok !== 'boolean') return finish(new WorkspaceError('mismatched workspace bridge response'));
      if (!message.ok) {
        // Only echo short machine codes, never arbitrary server exceptions.
        const code = typeof message.error === 'string' && /^[a-z-]{1,60}$/.test(message.error) ? message.error : 'request-failed';
        return finish(new WorkspaceError(`workspace bridge: ${code}`));
      }
      finish(null, message.value);
    });
  });
}

export async function connectWorkspaceHost(target, options) {
  const value = await workspaceRpc(target, 'ping', {}, options);
  if (value?.ready !== true) throw new WorkspaceError('workspace bridge is not ready');
  return target;
}

export async function resolveWorkspace(host, cwd, options) {
  const value = await workspaceRpc(host, 'resolve', { cwd }, options);
  if (!value || typeof value.id !== 'string' || !value.id || value.path !== cwd) throw new WorkspaceError('workspace bridge returned a different workspace path or invalid identity');
  return value;
}

export async function adoptSession(host, { sessionId, workspaceId, cwd }, options) {
  const value = await workspaceRpc(host, 'attach', { sessionId, workspaceId, cwd }, options);
  if (value?.bound !== true || value.sessionId !== sessionId || value.path !== cwd || value.id !== workspaceId) throw new WorkspaceError('workspace bridge did not verify the requested session membership');
  return value;
}
