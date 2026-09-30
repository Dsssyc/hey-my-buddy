#!/usr/bin/env node
/** No model calls: real Cordis, workspace API and persistence in a private temp home. */
import assert from 'node:assert/strict';
import { mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import * as bridgePlugin from '../../plugins/workspace-bridge.mjs';
import { connectWorkspaceHost, resolveWorkspace, adoptSession } from '../../scripts/lib/workspace-host.mjs';

const { values } = parseArgs({ options: { 'dsh-lib': { type: 'string' } } });
if (!values['dsh-lib']) throw new Error('--dsh-lib must name the installed @deepseek-ai package directory');
const lib = resolve(values['dsh-lib']);
const pkg = async name => await import(pathToFileURL(join(lib, name, 'lib', 'index.js')).href);
const { Context } = await pkg('cordis');
const sessionPkg = await pkg('dsh-session');
const dir = realpathSync(mkdtempSync('/tmp/dgr-'));
const ctx = new Context();
let close;
try {
  await ctx.plugin(sessionPkg.default);
  await ctx.plugin((await pkg('dsh-session-persistence-jsonl')).default, { root: join(dir, 'sessions'), compression: 'none' });
  await ctx.plugin((await pkg('dsh-storage')).default);
  await ctx.plugin(await pkg('dsh-storage-json'), { root: join(dir, 'storages') });
  await ctx.plugin(await pkg('dsh-storage-domain'), { backend: 'json' });
  const workspaceFork = ctx.plugin((await pkg('dsh-workspace')).default);
  await workspaceFork;
  // Plugin startup may wait for async storage initialization.
  for (let i = 0; !ctx.get('workspaceRegistry') && i < 100; i++) await new Promise(r => setTimeout(r, 20));
  assert.ok(ctx.get('workspaceRegistry'));
  const id = sessionPkg.SessionId('session-real-workspace-root');
  const session = ctx.get('sessions').create(id, { meta: { cwd: dir } });
  session.append('user/message', { id: 'm1', role: 'user', content: [{ type: 'text', text: 'No model is invoked.' }], source: { kind: 'user' } }, { surfaceOp: 'append' });
  const writer = await ctx.get('sessionPersistence').create(session.header);
  await writer.flush();
  await writer.close();
  const socketPath = join(dir, 'bridge', 'workspace.sock');
  let fork = ctx.plugin(bridgePlugin, { socketPath });
  await fork;
  close = () => fork.dispose();
  const host = await connectWorkspaceHost({ socketPath });
  const workspace = await resolveWorkspace(host, dir);
  const bound = await adoptSession(host, { cwd: dir, workspaceId: workspace.id, sessionId: String(id) });
  assert.equal(bound.bound, true);
  assert.ok(ctx.get('workspaceRegistry').get(workspace.id).sessionIds.includes(id));
  await assert.rejects(adoptSession(host, { cwd: dir, workspaceId: workspace.id, sessionId: 'session-missing' }), /unknown-session/);
  await close();
  fork = ctx.plugin(bridgePlugin, { socketPath });
  await fork;
  close = () => fork.dispose();
  const reconnected = await connectWorkspaceHost({ socketPath });
  assert.equal((await adoptSession(reconnected, { cwd: dir, workspaceId: workspace.id, sessionId: String(id) })).bound, true);
  console.log('real-workspace: ok (official API bound root; unknown session rejected; same-path restart and idempotent retry passed; no model)');
} finally {
  if (close) await close();
  rmSync(dir, { recursive: true, force: true });
}
