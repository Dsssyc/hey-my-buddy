import assert from 'node:assert/strict';
import { test } from 'node:test';
import { callNoTool } from '../plugins/no-tool-structured.mjs';

const spec = { provider: 'deepseek-official', model: 'deepseek-flash', effort: 'max' };
const request = { spec, prompt: 'choose a profile', callId: 'fake-call' };
const identity = { callId: request.callId };

function fixture(chunks, { runnerDisabled = true, throwAt = -1 } = {}) {
  const calls = [];
  const ctx = {
    get: () => ({ entries: () => [{ options: { id: 'headless-runner', disabled: runnerDisabled } }] }),
    llm: { listProviders: () => [{ id: spec.provider }], prepareCall: async (config) => ({
      config,
      stream: async function* (options) {
        calls.push(options);
        for (const [index, chunk] of chunks.entries()) {
          if (index === throwAt) throw new Error('connection reset');
          yield chunk;
        }
      },
    }) },
  };
  return { ctx, calls };
}

function delta(id, name) {
  return { type: 'tool-call-delta', index: 1, id, ...(name === undefined ? {} : { name }), argumentsDelta: '{}' };
}

test('direct native LLM call supplies an empty tool schema and requires terminal finish', async () => {
  const { ctx, calls } = fixture([
    { type: 'text-delta', index: 0, text: '{"choice":"a"}' },
    { type: 'usage', usage: { inputTokens: 8, outputTokens: 4 } },
    { type: 'finish', reason: { kind: 'stop' } },
  ]);
  const result = await callNoTool(ctx, request, new AbortController().signal);
  assert.equal(result.status, 'ok');
  assert.equal(result.rawAnswer, '{"choice":"a"}');
  assert.equal(result.usage.toolCalls, 0);
  assert.deepEqual(result.nativeToolEvents, []);
  assert.equal(result.nativeToolEventsTruncated, false);
  assert.equal(result.streamComplete, true);
  assert.deepEqual(calls[0].tools, []);
  assert.equal(calls[0].messages.length, 1);
  assert.equal(calls[0].messages[0].content[0].text, request.prompt);
  assert.equal(calls[0].system, undefined);
});

test('every native tool chunk shape is kept as a fact without inventing a call count', async () => {
  const cases = [
    [delta('call-9', 'shell'), [{ nativeIdentity: identity, callId: 'call-9', toolName: 'shell', phase: 'start' }]],
    [delta('call-7'), [{ nativeIdentity: identity, callId: 'call-7', type: 'tool-call', phase: 'start' }]],
    [{ type: 'block-start', index: 1, blockType: 'tool-call' },
     [{ nativeIdentity: identity, type: 'tool-call', phase: 'start' }]],
    [{ type: 'block-end', index: 2, block: { type: 'tool-call', id: 'call-2', name: 'search', arguments: '{}' } },
     [{ nativeIdentity: identity, callId: 'call-2', toolName: 'search', phase: 'start' }]],
    [{ type: 'block-end', index: 2, block: { type: 'tool-result', toolCallId: 'call-2', content: [] } },
     [{ nativeIdentity: identity, callId: 'call-2', type: 'tool-result', phase: 'end' }]],
    [delta('call-3', 'mcp-server__lookup'),
     [{ nativeIdentity: identity, callId: 'call-3', toolName: 'mcp-server__lookup', phase: 'start' }]],
  ];
  for (const [chunk, events] of cases) {
    const { ctx } = fixture([chunk]);
    const result = await callNoTool(ctx, request, new AbortController().signal);
    assert.equal(result.status, 'error', JSON.stringify(chunk));
    assert.equal(result.code, 'no-tool-violation', JSON.stringify(chunk));
    assert.equal(result.usage, undefined, JSON.stringify(chunk));
    assert.deepEqual(result.nativeToolEvents, events, JSON.stringify(chunk));
    assert.equal(result.nativeToolEventsTruncated, false);
  }
  const finished = await callNoTool(
    fixture([{ type: 'finish', reason: { kind: 'tool-calls' } }]).ctx, request, new AbortController().signal);
  assert.equal(finished.code, 'no-tool-violation');
  assert.deepEqual(finished.nativeToolEvents, []);
  assert.equal(finished.nativeFailure.finishKind, 'tool-calls');
  assert.equal(finished.streamComplete, true);
  const late = await callNoTool(fixture([{ type: 'finish', reason: { kind: 'stop' } }, delta('call-4', 'shell')])
    .ctx, request, new AbortController().signal);
  assert.equal(late.code, 'no-tool-violation');
  assert.equal(late.nativeToolEvents.length, 1);
});

test('tool facts past the shared bound are truncated and clearly reported', async () => {
  const chunks = Array.from({ length: 130 }, (_, index) => delta(`call-${index}`, 'shell'));
  const { ctx } = fixture(chunks);
  const result = await callNoTool(ctx, request, new AbortController().signal);
  assert.equal(result.code, 'no-tool-violation');
  assert.equal(result.nativeToolEvents.length, 128);
  assert.equal(result.nativeToolEvents.at(-1).callId, 'call-127');
  assert.equal(result.nativeToolEventsTruncated, true);
});

test('a broken stream reports the real failure with the facts observed so far', async () => {
  const broken = fixture([delta('call-5', 'shell'), { type: 'text-delta', index: 0, text: 'partial' }], { throwAt: 1 });
  const result = await callNoTool(broken.ctx, request, new AbortController().signal);
  assert.equal(result.code, 'invalid-native-result');
  assert.equal(result.nativeToolEvents.length, 1);
  assert.equal(result.nativeToolEvents[0].callId, 'call-5');
  assert.equal(result.streamComplete, false);
  const clean = fixture([{ type: 'text-delta', index: 0, text: 'answer' }], { throwAt: 0 });
  const failed = await callNoTool(clean.ctx, request, new AbortController().signal);
  assert.equal(failed.code, 'invalid-native-result');
  assert.deepEqual(failed.nativeToolEvents, []);
  assert.equal(failed.nativeChunkCount, 0);
});

test('non-stop finishes stay real native failures with bounded facts', async () => {
  for (const [chunks, finishKind] of [
    [[{ type: 'text-delta', index: 0, text: 'answer' }], null],
    [[{ type: 'finish', reason: { kind: 'max-tokens' } }], 'max-tokens'],
    [[{ type: 'finish', reason: { kind: 'error', failure: { code: 'provider_down', message: 'secret endpoint' } } }],
     'error'],
  ]) {
    const { ctx } = fixture(chunks);
    const result = await callNoTool(ctx, request, new AbortController().signal);
    assert.equal(result.code, 'native-turn-failed', finishKind);
    assert.equal(result.nativeFailure.finishKind, finishKind);
    assert.equal(result.streamComplete, chunks[0].type === 'finish');
    if (finishKind === 'error') {
      assert.equal(result.nativeFailure.code, 'provider_down');
      assert.equal(result.nativeFailure.message, undefined);
    }
  }
});

test('unknown protocol shapes fail without being read as tools', async () => {
  for (const chunk of [{ type: 'future-event' }, { type: 'block-start', index: 1, blockType: 'image' },
                       { type: 'usage' }, { type: 'text-delta', index: 0 }]) {
    const { ctx } = fixture([chunk]);
    const result = await callNoTool(ctx, request, new AbortController().signal);
    assert.equal(result.code, 'invalid-native-result', JSON.stringify(chunk));
  }
  const { ctx } = fixture([{ type: 'finish', reason: { kind: 'stop' } }, { type: 'usage', usage: {} }]);
  const result = await callNoTool(ctx, request, new AbortController().signal);
  assert.equal(result.code, 'invalid-native-result');
  assert.equal(result.streamComplete, false);
});

test('missing finish and active agent runner cannot verify zero tools', async () => {
  for (const chunks of [[], [{ type: 'text-delta', index: 0, text: 'answer' }]]) {
    const { ctx } = fixture(chunks);
    const result = await callNoTool(ctx, request, new AbortController().signal);
    assert.notEqual(result.status, 'ok');
  }
  const { ctx, calls } = fixture([{ type: 'finish', reason: { kind: 'stop' } }], { runnerDisabled: false });
  const result = await callNoTool(ctx, request, new AbortController().signal);
  assert.equal(result.code, 'no-tool-profile-unsafe');
  assert.deepEqual(result.nativeToolEvents, []);
  assert.equal(calls.length, 0);
});
