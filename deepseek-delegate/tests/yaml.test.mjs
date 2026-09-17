import test from 'node:test';
import assert from 'node:assert/strict';
import { loadYaml } from '../scripts/lib/yaml.mjs';

test('uv YAML bridge preserves JSON schema settings scalars and aliases', () => {
  assert.deepEqual(loadYaml('yes: yes\non: on\nno: no\nbool: TRUE\ndate: 2026-09-17\nnumber: 0123\nhex: 0x10\nfloat: 1e3\nbase: &base {value: 1}\ncopy: *base\nmerge: {<<: *base}\n'), {
    yes: 'yes', on: 'on', no: 'no', bool: true, date: '2026-09-17', number: 123, hex: 16, float: 1000,
    base: { value: 1 }, copy: { value: 1 }, merge: { '<<': { value: 1 } },
  });
  assert.equal(loadYaml(''), null);
  assert.equal(loadYaml('# comment only\n'), null);
  assert.equal(loadYaml('null'), null);
  assert.equal(loadYaml('.inf'), null);
  assert.equal(loadYaml('1e999'), '1e999');
});

test('YAML bridge refuses executable tags, duplicate keys and non-JSON collections', () => {
  for (const source of ['x: !!python/object/apply:os.system [echo]', 'x: !!js process.cwd()', 'x: !!timestamp 2026-09-17', 'x: 1\nx: 2', 'x: !!set {a: null}', 'a: &a [*a]', 'x: !!bool yes']) {
    assert.throws(() => loadYaml(source), /YAML parsing failed/);
  }
});

test('patch mode accepts only scalar js tags as inert text', () => {
  assert.deepEqual(loadYaml('- config: {root: !!js process.cwd()}', { mode: 'patch' }), [{ config: { root: 'process.cwd()' } }]);
  assert.throws(() => loadYaml('x: !!js [process.cwd()]', { mode: 'patch' }), /YAML parsing failed/);
  assert.throws(() => loadYaml('x: !!python/object/apply:os.system [echo]', { mode: 'patch' }), /YAML parsing failed/);
});
