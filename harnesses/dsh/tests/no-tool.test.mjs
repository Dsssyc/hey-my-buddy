import assert from 'node:assert/strict';
import { test } from 'node:test';
import { callNoTool } from '../plugins/no-tool-structured.mjs';

const spec = { provider: 'deepseek-official', model: 'deepseek-flash', effort: 'max' };
const request = { spec, prompt: 'choose a profile', callId: 'fake-call' };

function fixture(chunks, { runnerDisabled = true } = {}) {
  const calls = [];
  const ctx = {
    get: () => ({ entries: () => [{ options: { id: 'headless-runner', disabled: runnerDisabled } }] }),
    llm: { listProviders: () => [{ id: spec.provider }], prepareCall: async (config) => ({
      config,
      stream: async function* (options) { calls.push(options); for (const chunk of chunks) yield chunk; },
    }) },
  };
  return { ctx, calls };
}

test('direct native LLM call supplies an empty tool schema and requires terminal finish', async () => {
  const { ctx, calls } = fixture([
    { type: 'text-delta', text: '{"choice":"a"}' },
    { type: 'usage', usage: { inputTokens: 8, outputTokens: 4 } },
    { type: 'finish', reason: { kind: 'stop' } },
  ]);
  const result = await callNoTool(ctx, request, new AbortController().signal);
  assert.equal(result.status, 'ok');
  assert.equal(result.rawAnswer, '{"choice":"a"}');
  assert.equal(result.usage.toolCalls, 0);
  assert.deepEqual(calls[0].tools, []);
  assert.equal(calls[0].messages.length, 1);
  assert.equal(calls[0].messages[0].content[0].text, request.prompt);
  assert.equal(calls[0].system, undefined);
});

test('every native tool chunk shape fails even with no call id', async () => {
  for (const chunk of [
    { type: 'tool-call-delta', name: 'shell' },
    { type: 'block-start', blockType: 'tool-call' },
    { type: 'block-end', block: { type: 'tool-call', name: 'search' } },
    { type: 'block-end', block: { type: 'tool-result', name: 'mcp' } },
    { type: 'finish', reason: { kind: 'tool-calls' } },
  ]) {
    const { ctx } = fixture([chunk]);
    const result = await callNoTool(ctx, request, new AbortController().signal);
    assert.equal(result.code, 'no-tool-violation', JSON.stringify(chunk));
    assert.equal(result.usage.toolCalls, 1);
  }
  const { ctx } = fixture([{ type: 'finish', reason: { kind: 'stop' } },
    { type: 'tool-call-delta', name: 'subagent' }]);
  const late = await callNoTool(ctx, request, new AbortController().signal);
  assert.equal(late.code, 'no-tool-violation');
});

test('missing finish, unknown chunks, and active agent runner cannot verify zero tools', async () => {
  for (const chunks of [[], [{ type: 'text-delta', text: 'answer' }], [{ type: 'future-event' }]]) {
    const { ctx } = fixture(chunks);
    const result = await callNoTool(ctx, request, new AbortController().signal);
    assert.notEqual(result.status, 'ok');
  }
  const { ctx, calls } = fixture([{ type: 'finish', reason: { kind: 'stop' } }], { runnerDisabled: false });
  const result = await callNoTool(ctx, request, new AbortController().signal);
  assert.equal(result.code, 'no-tool-profile-unsafe');
  assert.equal(calls.length, 0);
});
