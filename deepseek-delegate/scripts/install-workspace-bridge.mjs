#!/usr/bin/env node
/** Add the official Cordis plugin composition to one existing host profile. */
import { existsSync, readFileSync, writeFileSync, renameSync, rmSync } from 'node:fs';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { loadYaml } from './lib/yaml.mjs';

const pluginPath = fileURLToPath(new URL('../plugins/workspace-bridge.mjs', import.meta.url));

export function installBridge({ home = process.env.DSH_HOME || join(homedir(), '.dsh'), profile = 'web', socketPath } = {}) {
  if (!/^[a-zA-Z0-9_-]+$/.test(profile)) throw new Error('profile must be a simple profile name');
  home = resolve(home);
  const dir = join(home, 'profiles', profile);
  if (!existsSync(join(dir, 'package.json'))) throw new Error('target dsh profile does not exist');
  if (!existsSync(pluginPath)) throw new Error('workspace bridge plugin is missing');
  const path = join(dir, 'cordis.patch.yml');
  const original = existsSync(path) ? readFileSync(path, 'utf8') : '';
  const rows = loadYaml(original, { mode: 'patch' }) ?? [];
  if (!Array.isArray(rows)) throw new Error('profile patch must be a YAML list');
  const endpoint = resolve(socketPath || join(home, 'deepseek-delegate', 'workspace.sock'));
  const entry = { id: 'deepseek-delegate-workspace-bridge', name: pluginPath, config: { socketPath: endpoint } };
  const existing = rows.flatMap(row => row?.insert ?? []).find(row => row?.id === entry.id);
  if (existing) {
    if (existing.name !== entry.name || existing.config?.socketPath !== endpoint) throw new Error('profile already contains a different workspace bridge; review its existing entry');
    return { changed: false, profile, path, socketPath: endpoint };
  }
  // Preserve every existing tag, comment and setting verbatim. Empty flow lists
  // must become block lists before appending a row.
  if (rows.length && original.trimStart().startsWith('[')) throw new Error('nonempty flow-style patch: convert it to a YAML block list before installation');
  if (/^\.\.\.\s*$/m.test(original)) throw new Error('remove the YAML document end marker before installation');
  const prefix = rows.length ? original.trimEnd() + '\n' : '';
  const next = prefix + '- ' + JSON.stringify({ insert: [entry] }) + '\n';
  loadYaml(next, { mode: 'patch' });
  let backup = null;
  if (existsSync(path)) {
    backup = `${path}.before-workspace-bridge-${Date.now()}`;
    writeFileSync(backup, original, { mode: 0o600, flag: 'wx' });
  }
  const temporary = `${path}.delegate-${process.pid}`;
  try {
    writeFileSync(temporary, next, { mode: 0o600, flag: 'wx' });
    renameSync(temporary, path);
  } finally { rmSync(temporary, { force: true }); }
  return { changed: true, profile, path, socketPath: endpoint, backup };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const { values } = parseArgs({ options: {
      home: { type: 'string' }, profile: { type: 'string', default: 'web' },
      'workspace-socket': { type: 'string' }, help: { type: 'boolean' },
    } });
    if (values.help) console.log('Usage: node scripts/install-workspace-bridge.mjs [--home <DSH_HOME>] [--profile web] [--workspace-socket <path>]\nAdds a local workspace plugin to an existing host profile; saves a private backup. No URL or token.');
    else console.log(JSON.stringify(installBridge({ home: values.home, profile: values.profile, socketPath: values['workspace-socket'] })));
  } catch (error) { console.error(`workspace bridge install: ${error.message}`); process.exitCode = 1; }
}
