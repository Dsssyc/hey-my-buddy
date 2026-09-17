import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { waitForChange } from '../service/wait.mjs';

function fixture() {
  const manager = new EventEmitter();
  let state = { runId: 'x', revision: 1, resultAvailable: false };
  manager.dispatch = async () => ({ ...state });
  return { manager, update: () => { state = { ...state, revision: 2, resultAvailable: true }; manager.emit('change', 'x'); } };
}
test('wait returns on completion and releases listeners', async () => {
  const { manager, update } = fixture();
  const pending = waitForChange(manager, { runId: 'x', afterRevision: 1, timeoutMs: 1000 });
  setImmediate(update);
  assert.equal((await pending).resultAvailable, true);
  assert.equal(manager.listenerCount('change'), 0);
});
test('wait timeout is a nonterminal snapshot, cancellation cleans up', async () => {
  const { manager } = fixture();
  assert.equal((await waitForChange(manager, { runId: 'x', timeoutMs: 5 })).resultAvailable, false);
  const controller = new AbortController();
  const pending = waitForChange(manager, { runId: 'x', timeoutMs: 1000 }, controller.signal);
  setImmediate(() => controller.abort());
  await assert.rejects(pending, { code: 'ABORTED' });
  assert.equal(manager.listenerCount('change'), 0);
});
test('a newer revision returns immediately and invalid waits are rejected', async () => {
  const { manager } = fixture();
  assert.equal((await waitForChange(manager, { runId: 'x', afterRevision: 0, timeoutMs: 30000 })).revision, 1);
  await assert.rejects(waitForChange(manager, { runId: 'x', timeoutMs: 30001 }), { code: 'INVALID_INPUT' });
});
