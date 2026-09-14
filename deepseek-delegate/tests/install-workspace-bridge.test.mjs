import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { installBridge } from '../scripts/install-workspace-bridge.mjs';

test('installer preserves settings/tags, backs up and is idempotent', t => {
  const home = mkdtempSync('/tmp/dgi-');
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const profile = join(home, 'profiles', 'web');
  mkdirSync(profile, { recursive: true });
  writeFileSync(join(profile, 'package.json'), '{}');
  const original = '# user setting\n- id: something\n  config:\n    root: !!js process.cwd()\n';
  const path = join(profile, 'cordis.patch.yml');
  writeFileSync(path, original);
  const result = installBridge({ home });
  assert.equal(result.changed, true);
  assert.equal(readFileSync(result.backup, 'utf8'), original);
  assert.equal(readFileSync(path, 'utf8').startsWith(original), true);
  assert.equal(installBridge({ home }).changed, false);
  assert.throws(() => installBridge({ home, socketPath: '/tmp/other.sock' }), /different/);
});

test('installer accepts empty profile patch and rejects missing profile', t => {
  const home = mkdtempSync('/tmp/dgi-');
  t.after(() => rmSync(home, { recursive: true, force: true }));
  assert.throws(() => installBridge({ home }), /does not exist/);
  const profile = join(home, 'profiles', 'web');
  mkdirSync(profile, { recursive: true });
  writeFileSync(join(profile, 'package.json'), '{}');
  writeFileSync(join(profile, 'cordis.patch.yml'), '[]\n');
  assert.equal(installBridge({ home }).changed, true);
  assert.equal(installBridge({ home }).changed, false);
});
