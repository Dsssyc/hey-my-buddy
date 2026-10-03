/**
 * The desensitized native DSH record replayed through the real usage observer.
 *
 * `tests/python/fixtures/native-usage-dsh.json` is a minimal copy of a real
 * private headless session rollout with every identifier, path and prompt
 * replaced. This suite mounts the production plugin over that exact native event
 * sequence, so the observer's projection is pinned to the native vocabulary
 * rather than to a hand-written event shape.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { apply, name } from '../plugins/usage.mjs';

const FIXTURE = JSON.parse(readFileSync(
  new URL('../../../tests/python/buddy/harnesses/dsh/fixtures/native-usage-dsh.json', import.meta.url), 'utf8'));

function sha256(text) {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

function mount(t, { taskId = 'task-one', attemptId = 'attempt-one', generation = 1 } = {}) {
  const directory = mkdtempSync(join(tmpdir(), 'buddy-native-usage-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const usagePath = join(directory, 'native-usage.json');
  const handlers = [];
  const ctx = { on(event, handler) { assert.equal(event, 'session/event'); handlers.push(handler); } };
  apply(ctx, { usagePath, taskId, attemptId, generation, promptSha256: sha256(FIXTURE.prompt), cwd: FIXTURE.session.cwd });
  const session = { id: FIXTURE.session.id, header: { ...FIXTURE.session, type: undefined } };
  return {
    usagePath,
    emit(event) { for (const handler of handlers) handler(session, event); },
    feed() { for (const event of FIXTURE.events) this.emit(event); },
    read() { return JSON.parse(readFileSync(usagePath, 'utf8')); },
  };
}

test('the native observer projects the fixture turn into the canonical counters', (t) => {
  const run = mount(t);
  run.feed();
  const document = run.read();
  assert.equal(document.version, 1);
  assert.equal(document.taskId, 'task-one');
  assert.equal(document.attemptId, 'attempt-one');
  assert.equal(document.generation, 1);
  const expected = FIXTURE.expected;
  const native = document.nativeUsage;
  assert.equal(native.source, 'dsh/session-assistant-usage');
  assert.equal(native.sessionId, FIXTURE.session.id);
  assert.equal(native.tokenUsage.source, 'dsh/session-assistant-usage');
  assert.equal(native.tokenUsage.inputBasis, 'excludes-cached');
  assert.equal(native.tokenUsage.inputTokens, expected.inputTokens);
  assert.equal(native.tokenUsage.outputTokens, expected.outputTokens);
  assert.equal(native.tokenUsage.cachedInputTokens, expected.derivedCachedInputTokens);
  assert.equal(native.tokenUsage.totalTokens, expected.nativeTotalTokens);
  assert.equal(native.tokenUsage.records, expected.records);
  assert.equal(native.tokenUsage.completeness, expected.completeness);
  assert.equal(native.lastAssistantMessage.text, expected.lastAssistantText);
  assert.equal(native.lastAssistantMessage.sourceId, 'assistant-fixture-2');
  assert.equal(native.lastAssistantMessage.sha256, sha256(expected.lastAssistantText));
  assert.equal(native.lastAssistantMessage.truncated, false);
  // The canonical input basis is applied by hey_my_buddy.protocol.usage; the native file keeps
  // the harness's own basis so the two can never be silently mixed up.
  assert.equal(native.tokenUsage.inputTokens, expected.unifiedInputTokens - expected.derivedCachedInputTokens);
  assert.equal(JSON.stringify(document).includes('Fixture task text.\n'), false, 'the prompt is never retained');
  const raw = readFileSync(run.usagePath, 'utf8');
  assert.equal(raw.includes('call-fixture-1'), false, 'tool calls are never retained');
  assert.equal(raw.includes('buddy-native-fixture/checkout/a.txt'), false, 'tool arguments are never retained');
});

test('the native quota failure keeps its structured code and no provider wording', (t) => {
  const run = mount(t);
  run.feed();
  run.emit(FIXTURE.quotaFailure.event);
  const document = run.read();
  assert.deepEqual(document.nativeUsage.failure, { code: FIXTURE.quotaFailure.expectedCode, kind: 'error' });
  assert.equal(document.nativeUsage.tokenUsage.completeness, 'partial');
  assert.equal(document.nativeUsage.lastAssistantMessage.text, FIXTURE.expected.lastAssistantText);
  const raw = readFileSync(run.usagePath, 'utf8');
  assert.equal(raw.includes('provider wording that must never be retained'), false);
});

test('a fixture with a foreign binding is never written', (t) => {
  const directory = mkdtempSync(join(tmpdir(), 'buddy-native-usage-foreign-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const usagePath = join(directory, 'native-usage.json');
  const handlers = [];
  const ctx = { on(event, handler) { handlers.push(handler); } };
  apply(ctx, { usagePath, taskId: 'task-one', attemptId: 'attempt-two', generation: 1,
               promptSha256: sha256('a different prompt'), cwd: FIXTURE.session.cwd });
  const session = { id: FIXTURE.session.id, header: { ...FIXTURE.session } };
  for (const event of FIXTURE.events) for (const handler of handlers) handler(session, event);
  assert.throws(() => readFileSync(usagePath, 'utf8'));
});
