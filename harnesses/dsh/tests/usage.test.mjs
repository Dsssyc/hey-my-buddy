/**
 * Unit tests for the deepseek-delegate attempt usage observer plugin.
 *
 * These tests use the event and session shapes verified against the installed
 * dsh sources and real session records (see `observer.test.mjs` and
 * `tests/manual/real-observer.mjs`):
 * - `ctx.on('session/event', (session, event) => ...)`
 * - root session: `session.header` with `cwd` and no parent/origin/depth
 * - user message: `event.type === 'user/message'` with `event.data` the
 *   `UserMessage` itself (`content: [{ type: 'text', text }]`,
 *   `source: { kind: 'user' }`)
 * - assistant message: `event.data.usage` is the provider usage object and
 *   `event.data.message` the assistant message with `source.kind === 'model'`
 * - turn end: `event.data.reason` is `{ kind }`, `{ kind: 'aborted', reason }`
 *   or `{ kind: 'error', error: { code, message } }`
 *
 * The plugin must never throw into the run, never estimate usage and never
 * write prompts, tool arguments, tool output or provider messages.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, before, describe, test } from 'node:test';
import { modeOf, readText } from './support/helpers.mjs';

const PLUGIN_URL = new URL('../plugins/usage.mjs', import.meta.url);
const PLUGIN_PATH = fileURLToPath(PLUGIN_URL);
const { apply, name } = await import(PLUGIN_URL.href);

const PROMPT = 'Implement the delegated task.\n';
const PROMPT_SHA256 = createHash('sha256').update(PROMPT, 'utf8').digest('hex');

let root;
before(() => {
  root = mkdtempSync(join(tmpdir(), 'deepseek-delegate-usage-'));
});
after(() => {
  rmSync(root, { recursive: true, force: true });
});

/** Minimal Cordis-like context: records listeners and lets a test emit. */
function fakeContext() {
  const listeners = new Map();
  return {
    on(event, listener) {
      const list = listeners.get(event) ?? [];
      list.push(listener);
      listeners.set(event, list);
    },
    emit(event, ...args) {
      for (const listener of listeners.get(event) ?? []) listener(...args);
    },
  };
}

/** Root session shape as created by `ctx.sessions.create(id, { meta: { cwd } })`. */
function rootSession(id, cwd, header = {}) {
  return { id, header: { version: 3, id, createdAt: 0, cwd, isSeeded: false, ...header } };
}

/** Real `user/message` event shape: `data` is the UserMessage itself. */
function userMessageEvent(text, seq = 1, source = { kind: 'user' }) {
  return {
    type: 'user/message',
    seq,
    time: 0,
    data: { id: `message-user-${seq}`, role: 'user', content: [{ type: 'text', text }], source },
    surfaceOp: 'append',
  };
}

const textBlock = (text) => ({ type: 'text', text });
const reasoningBlock = (text) => ({ type: 'reasoning', text });
const toolCallBlock = (name, args) => ({ type: 'tool-call', callId: `call-${name}`, name, arguments: args });

/** `assistant/message` shape: `data.usage` is present when the provider reported one. */
function assistantEvent(id, content, usage, seq) {
  const data = {
    message: {
      id,
      role: 'assistant',
      content,
      source: { kind: 'model', provider: 'deepseek-official', model: 'deepseek-flash' },
    },
  };
  if (usage !== undefined) data.usage = usage;
  return { type: 'assistant/message', seq, time: 0, data, surfaceOp: 'append' };
}

function turnEndEvent(reason, seq = 99) {
  return { type: 'turn/end', seq, time: 0, data: { turn: 1, reason } };
}

function toolCallEvent(name, seq, args = '{}') {
  return { type: 'tool/call', seq, time: 0, data: { turn: 1, step: 1, callId: `call-${seq}`, name, arguments: args } };
}

function toolResultEvent(seq, output) {
  return {
    type: 'tool/result',
    seq,
    time: 0,
    data: {
      turn: 1,
      step: 1,
      message: {
        role: 'user',
        source: { kind: 'tool', callId: `call-${seq}` },
        content: [{ type: 'tool-result', toolCallId: `call-${seq}`, isError: false, content: [textBlock(output)] }],
      },
    },
  };
}

const sha256Hex = (text) => createHash('sha256').update(text, 'utf8').digest('hex');

/** One observer instance with its own private sidecar path, bound on demand. */
function harness(config = {}) {
  const dir = mkdtempSync(join(root, 'case-'));
  const usagePath = join(dir, 'usage.json');
  const ctx = fakeContext();
  apply(ctx, {
    usagePath, promptSha256: PROMPT_SHA256, cwd: dir,
    taskId: 'task-1', attemptId: 'attempt-1', generation: 3, minIntervalMs: 0, ...config,
  });
  const session = rootSession('session-root-1', dir);
  return {
    dir,
    usagePath,
    ctx,
    session,
    emit: (event, target = session) => ctx.emit('session/event', target, event),
    bind: () => ctx.emit('session/event', session, userMessageEvent(PROMPT)),
    written: () => existsSync(usagePath),
    read: () => JSON.parse(readFileSync(usagePath, 'utf8')),
  };
}

/** Feed one assistant usage record through a fresh bound observer and return its token projection. */
function tokenUsageFor(usage) {
  const h = harness();
  h.bind();
  h.emit(assistantEvent('message-a', [textBlock('text')], usage, 2));
  h.emit(turnEndEvent({ kind: 'completed' }, 3));
  return h.read().nativeUsage.tokenUsage;
}

/** Feed one oversized assistant text through a fresh bound observer and return its record. */
function retainedFor(text) {
  const h = harness();
  h.bind();
  h.emit(assistantEvent('message-big', [textBlock(text)], { inputTokens: 1, outputTokens: 1, totalTokens: 2 }, 2));
  h.emit(turnEndEvent({ kind: 'completed' }, 3));
  return h.read().nativeUsage.lastAssistantMessage;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

describe('usage observer', () => {
  test('plugin is dependency-free: node builtins only', () => {
    const source = readText(PLUGIN_PATH);
    const specifiers = [...source.matchAll(/^import\s[^\n]*from\s+'([^']+)'/gm)].map((match) => match[1]);
    assert.ok(specifiers.length > 0, 'plugin has imports');
    for (const specifier of specifiers) {
      assert.ok(specifier.startsWith('node:'), `unexpected dependency: ${specifier}`);
    }
    assert.equal(name, 'deepseek-delegate-usage');
  });

  test('writes nothing before the root session is bound and ignores foreign sessions', () => {
    const h = harness();
    const foreign = [
      [rootSession('session-other-cwd', `${h.dir}-elsewhere`), PROMPT],
      [rootSession('session-other-prompt', h.dir), 'A different prompt.\n'],
      [rootSession('session-fork', h.dir, { parentSession: 'session-parent' }), PROMPT],
      [rootSession('session-subagent', h.dir, { origin: 'subagent' }), PROMPT],
      [rootSession('session-depth', h.dir, { delegationDepth: 1 }), PROMPT],
    ];
    for (const [session, text] of foreign) {
      h.ctx.emit('session/event', session, userMessageEvent(text));
      h.ctx.emit('session/event', session, assistantEvent('message-foreign', [textBlock('foreign')], { inputTokens: 900, outputTokens: 900, totalTokens: 1800 }));
      h.ctx.emit('session/event', session, turnEndEvent({ kind: 'completed' }));
    }
    assert.equal(h.written(), false, 'nothing is written before the run root is bound');

    h.bind();
    for (const [session] of foreign) {
      h.ctx.emit('session/event', session, assistantEvent('message-foreign', [textBlock('foreign')], { inputTokens: 900, outputTokens: 900, totalTokens: 1800 }));
      h.ctx.emit('session/event', session, turnEndEvent({ kind: 'completed' }));
    }
    assert.equal(h.written(), false, 'foreign sessions never contribute after binding');

    h.emit(assistantEvent('message-real', [textBlock('real')], { inputTokens: 5, outputTokens: 5, totalTokens: 10 }, 2));
    const usage = h.read().nativeUsage;
    assert.equal(usage.sessionId, 'session-root-1');
    assert.equal(usage.tokenUsage.source, 'dsh/session-assistant-usage');
    assert.equal(usage.tokenUsage.records, 1);
    assert.equal(usage.tokenUsage.inputTokens, 5, 'foreign usage is never summed');
    assert.equal(usage.tokenUsage.outputTokens, 5);
  });

  test('a normal multi-step turn sums native usage and derives cached input', () => {
    const h = harness();
    h.bind();
    h.emit(toolCallEvent('shell', 2));
    h.emit(assistantEvent('message-a', [reasoningBlock('thinking'), textBlock('Step one.')], { inputTokens: 100, outputTokens: 20, totalTokens: 160, cacheReadTokens: 30, cacheWriteTokens: 10 }, 3));
    h.emit(toolResultEvent(4, 'tool output'));
    h.emit(assistantEvent('message-b', [textBlock('Step two.')], { inputTokens: 50, outputTokens: 8, totalTokens: 88, cacheReadTokens: 25, cacheWriteTokens: 5 }, 5));
    h.emit(toolCallEvent('shell', 6));
    h.emit(assistantEvent('message-c', [textBlock('Done.')], { inputTokens: 7, outputTokens: 3, totalTokens: 15 }, 7));
    h.emit(turnEndEvent({ kind: 'completed' }, 8));

    const record = h.read();
    assert.equal(record.version, 1);
    assert.equal(record.taskId, 'task-1');
    assert.equal(record.attemptId, 'attempt-1');
    assert.equal(record.generation, 3);
    assert.equal(Number.isNaN(Date.parse(record.updatedAt)), false);
    const usage = record.nativeUsage;
    assert.equal(usage.source, 'dsh/session-assistant-usage');
    assert.equal(usage.sessionId, 'session-root-1');
    assert.equal('inputTokens' in usage, false, 'the counters live under tokenUsage, not beside it');
    const token = usage.tokenUsage;
    assert.equal(token.source, 'dsh/session-assistant-usage');
    assert.equal(token.inputBasis, 'excludes-cached');
    assert.equal(token.records, 3);
    assert.equal(token.inputTokens, 157);
    assert.equal(token.outputTokens, 31);
    assert.equal(token.totalTokens, 263);
    assert.equal(token.cachedInputTokens, 75, 'total - input - output when the cache fields are incomplete');
    assert.equal(token.completeness, 'complete');
    assert.equal('reasoningOutputTokens' in token, false, 'a field no record carried is omitted, never zero');
    assert.equal(usage.lastAssistantMessage.text, 'Done.');
    assert.equal(usage.lastAssistantMessage.sourceId, 'message-c');
    assert.equal(usage.lastAssistantMessage.truncated, false);
    assert.equal(usage.lastAssistantMessage.sourceBytes, Buffer.byteLength('Done.', 'utf8'));
    assert.equal(usage.lastAssistantMessage.sha256, sha256Hex('Done.'));
    assert.equal(modeOf(h.usagePath), '600', 'the sidecar is owner-private');
    assert.deepEqual(readdirSync(h.dir), ['usage.json'], 'no temporary file survives the write');
  });

  test('cached input falls back to cache fields and is omitted when unprovable', () => {
    const both = tokenUsageFor({ inputTokens: 10, outputTokens: 5, cacheReadTokens: 3, cacheWriteTokens: 2 });
    assert.equal('totalTokens' in both, false);
    assert.equal(both.cachedInputTokens, 5, 'cache read plus cache write when total is unavailable');

    const readOnly = tokenUsageFor({ inputTokens: 10, outputTokens: 5, cacheReadTokens: 3 });
    assert.equal(readOnly.cachedInputTokens, 3, 'the only complete cache field');

    const negative = tokenUsageFor({ inputTokens: 10, outputTokens: 5, totalTokens: 12, cacheReadTokens: 1, cacheWriteTokens: 1 });
    assert.equal(negative.cachedInputTokens, 2, 'a negative derivation falls through to the cache fields');

    const unprovable = tokenUsageFor({ inputTokens: 10, outputTokens: 5 });
    assert.equal('cachedInputTokens' in unprovable, false);
    assert.equal(unprovable.inputTokens, 10);
    assert.equal(unprovable.outputTokens, 5);
    assert.equal('totalTokens' in unprovable, false);
  });

  test('a record without usable usage makes the observation partial and omits incomplete fields', () => {
    const h = harness();
    h.bind();
    h.emit(assistantEvent('message-a', [textBlock('one')], { inputTokens: 100, outputTokens: 10, reasoningTokens: 5 }, 2));
    h.emit(assistantEvent('message-b', [textBlock('two')], { inputTokens: 20, outputTokens: 4 }, 3));
    h.emit(assistantEvent('message-c', [textBlock('three')], undefined, 4));
    h.emit(turnEndEvent({ kind: 'completed' }, 5));

    const usage = h.read().nativeUsage.tokenUsage;
    assert.equal(usage.records, 2, 'only assistant messages with a usable usage object count');
    assert.equal(usage.inputTokens, 120);
    assert.equal(usage.outputTokens, 14);
    assert.equal(usage.completeness, 'partial', 'a completed turn does not hide a missing usage object');
    assert.equal('reasoningOutputTokens' in usage, false, 'one record missed it, so it is never a partial sum');
    assert.notEqual(usage.reasoningOutputTokens, 0);
    assert.equal('totalTokens' in usage, false);
    assert.equal('cachedInputTokens' in usage, false);
  });

  test('unusable usage values are rejected instead of coerced', () => {
    const h = harness();
    h.bind();
    h.emit(assistantEvent('message-a', [textBlock('one')], {
      inputTokens: -5,
      outputTokens: 1.5,
      totalTokens: '10',
      cacheReadTokens: Number.MAX_SAFE_INTEGER + 2,
      cacheWriteTokens: null,
      reasoningTokens: undefined,
    }, 2));
    h.emit(assistantEvent('message-b', [textBlock('two')], [], 3));
    h.emit(turnEndEvent({ kind: 'completed' }, 4));

    const usage = h.read().nativeUsage.tokenUsage;
    assert.equal(usage.records, 0, 'no field was a non-negative safe integer');
    assert.equal(usage.completeness, 'partial');
    for (const field of ['inputTokens', 'outputTokens', 'totalTokens', 'cachedInputTokens', 'reasoningOutputTokens']) {
      assert.equal(field in usage, false, `${field} is omitted, never zero`);
    }
  });

  test('a quota failure keeps only the machine code, never the provider message', () => {
    const h = harness();
    h.bind();
    h.emit(assistantEvent('message-a', [textBlock('I could not finish the work.')], { inputTokens: 5, outputTokens: 1, totalTokens: 6 }, 2));
    h.emit(turnEndEvent({ kind: 'error', error: { code: 'QUOTA', message: 'quota exceeded for key sk-secret-value' } }, 3));

    const raw = readFileSync(h.usagePath, 'utf8');
    const usage = JSON.parse(raw).nativeUsage;
    assert.deepEqual(usage.failure, { code: 'QUOTA', kind: 'error' });
    assert.equal(usage.tokenUsage.completeness, 'partial');
    assert.equal(usage.lastAssistantMessage.text, 'I could not finish the work.');
    assert.equal(usage.tokenUsage.records, 1);
    assert.equal(raw.includes('sk-secret-value'), false, 'the provider message never reaches the sidecar');
    assert.equal(raw.includes('quota exceeded'), false);
  });

  test('failure classification keeps a bounded code and drops everything else', () => {
    for (const [error, expected] of [
      [{ code: 'RATE_LIMIT' }, { code: 'RATE_LIMIT', kind: 'error' }],
      [{ code: 'x'.repeat(100) }, { code: 'x'.repeat(100), kind: 'error' }],
      [{ code: 'x'.repeat(101) }, { kind: 'error' }],
      [{ code: '' }, { kind: 'error' }],
      [{ code: 42, message: 'raw provider text' }, { kind: 'error' }],
      [undefined, { kind: 'error' }],
    ]) {
      const h = harness();
      h.bind();
      h.emit(turnEndEvent({ kind: 'error', error }, 2));
      assert.deepEqual(h.read().nativeUsage.failure, expected, JSON.stringify(error));
    }
  });

  test('lastAssistantMessage keeps the last qualifying text, never tool calls or tool output', () => {
    const h = harness();
    h.bind();
    h.emit(assistantEvent('message-a', [reasoningBlock('hidden reasoning'), textBlock('The first answer.')], { inputTokens: 3, outputTokens: 3, totalTokens: 6 }, 2));
    h.emit(toolCallEvent('shell', 3, '{"command":"echo SECRET_TOOL_ARGUMENT"}'));
    h.emit(toolResultEvent(4, 'SECRET_TOOL_OUTPUT'));
    h.emit(assistantEvent('message-b', [toolCallBlock('shell', '{"command":"echo SECRET_TOOL_ARGUMENT"}')], { inputTokens: 1, outputTokens: 1, totalTokens: 2 }, 5));
    h.emit(turnEndEvent({ kind: 'completed' }, 6));

    const raw = readFileSync(h.usagePath, 'utf8');
    const last = JSON.parse(raw).nativeUsage.lastAssistantMessage;
    assert.equal(last.text, 'The first answer.', 'a trailing tool-call-only message is not text');
    assert.equal(last.sourceId, 'message-a');
    assert.equal(raw.includes('SECRET_TOOL_ARGUMENT'), false);
    assert.equal(raw.includes('SECRET_TOOL_OUTPUT'), false);
    assert.equal(raw.includes('hidden reasoning'), false);
  });

  test('lastAssistantMessage bounds the head to 65536 UTF-8 bytes without splitting a character', () => {
    const exact = 'a'.repeat(65_536);
    const retainedExact = retainedFor(exact);
    assert.equal(retainedExact.text, exact);
    assert.equal(retainedExact.truncated, false);
    assert.equal(retainedExact.sourceBytes, 65_536);
    assert.equal(retainedExact.sha256, sha256Hex(exact));

    const split = `${'a'.repeat(65_535)}😀`;
    const retainedSplit = retainedFor(split);
    assert.equal(retainedSplit.truncated, true);
    assert.equal(retainedSplit.text, 'a'.repeat(65_535), 'the partial four-byte character is held back');
    assert.equal(Buffer.byteLength(retainedSplit.text, 'utf8'), 65_535);
    assert.equal(retainedSplit.sourceBytes, 65_539);
    assert.equal(retainedSplit.sha256, sha256Hex(split));

    const dense = `${'a'.repeat(65_000)}${'😀'.repeat(200)}`;
    const retainedDense = retainedFor(dense);
    assert.equal(retainedDense.truncated, true);
    assert.equal(retainedDense.text, `${'a'.repeat(65_000)}${'😀'.repeat(134)}`);
    assert.equal(Buffer.byteLength(retainedDense.text, 'utf8'), 65_536);
    assert.equal(retainedDense.sourceBytes, 65_800);
    assert.equal(retainedDense.sha256, sha256Hex(dense));
    assert.equal(retainedDense.text.includes('\ufffd'), false, 'no replacement character');
    for (const character of retainedDense.text) {
      const point = character.codePointAt(0);
      assert.ok(point < 0xd800 || point > 0xdfff, 'no lone surrogate is retained');
    }
  });

  test('coalesces an unchanged projection but always writes on turn/end', async () => {
    const h = harness({ minIntervalMs: 60_000 });
    h.bind();
    h.emit(assistantEvent('message-a', [textBlock('one')], undefined, 2));
    const initial = h.read();
    assert.equal(initial.nativeUsage.tokenUsage.records, 0);
    assert.equal(initial.nativeUsage.tokenUsage.completeness, 'partial');

    await sleep(20);
    h.emit(assistantEvent('message-b', [toolCallBlock('shell')], undefined, 3));
    assert.equal(h.read().updatedAt, initial.updatedAt, 'an unchanged projection stays unwritten inside the window');

    h.emit(assistantEvent('message-c', [textBlock('two')], { inputTokens: 1, outputTokens: 1, totalTokens: 2 }, 4));
    assert.equal(h.read().nativeUsage.tokenUsage.records, 1, 'a material change writes immediately');
    assert.notEqual(h.read().updatedAt, initial.updatedAt);

    h.emit(turnEndEvent({ kind: 'completed' }, 5));
    const settled = h.read();
    assert.equal(settled.nativeUsage.tokenUsage.completeness, 'partial', 'earlier records without usage keep the observation partial');
    await sleep(20);
    h.emit(turnEndEvent({ kind: 'completed' }, 6));
    assert.notEqual(h.read().updatedAt, settled.updatedAt, 'turn/end writes even without a material change');
  });

  test('a malformed config disables the observer and writes nothing', () => {
    const invalid = [
      { usagePath: '' },
      { usagePath: 42 },
      { usagePath: undefined },
      { promptSha256: 'not-a-hash' },
      { promptSha256: PROMPT_SHA256.toUpperCase() },
      { promptSha256: undefined },
      { taskId: '' },
      { taskId: 'x'.repeat(257) },
      { attemptId: '' },
      { attemptId: 'x'.repeat(257) },
      { generation: 0 },
      { generation: -1 },
      { generation: 1.5 },
      { generation: 2 ** 53 },
      { cwd: '' },
    ];
    for (const override of invalid) {
      const dir = mkdtempSync(join(root, 'disabled-'));
      const usagePath = join(dir, 'usage.json');
      const ctx = fakeContext();
      assert.doesNotThrow(() => {
        apply(ctx, {
          usagePath, promptSha256: PROMPT_SHA256, cwd: dir,
          taskId: 'task-1', attemptId: 'attempt-1', generation: 1, ...override,
        });
        const session = rootSession('session-root-disabled', dir);
        ctx.emit('session/event', session, userMessageEvent(PROMPT));
        ctx.emit('session/event', session, assistantEvent('message-1', [textBlock('text')], { inputTokens: 1, outputTokens: 1, totalTokens: 2 }));
        ctx.emit('session/event', session, turnEndEvent({ kind: 'completed' }));
      }, JSON.stringify(override));
      assert.equal(existsSync(usagePath), false, `disabled for ${JSON.stringify(override)}`);
    }
    for (const config of [undefined, null, {}]) {
      const ctx = fakeContext();
      assert.doesNotThrow(() => {
        apply(ctx, config);
        ctx.emit('session/event', rootSession('session-root-bare', root), userMessageEvent(PROMPT));
      });
    }
  });

  test('malformed events never throw and never fabricate usage', () => {
    const h = harness();
    h.bind();
    assert.doesNotThrow(() => {
      h.ctx.emit('session/event', undefined, undefined);
      h.ctx.emit('session/event', {}, {});
      h.ctx.emit('session/event', { id: 'x', header: null }, turnEndEvent({ kind: 'completed' }));
      h.emit({ type: 'assistant/message', seq: 2, time: 0, data: { message: null } });
      h.emit({ type: 'assistant/message', seq: 3, time: 0, data: { usage: [], message: null } });
      h.emit({ type: 'assistant/message', seq: 4, time: 0, data: { usage: 'nope', message: null } });
      h.emit({ type: 'assistant/message', seq: 5, time: 0, data: { usage: { inputTokens: 1 }, message: { id: 'message-bad', role: 'assistant', content: 'nope', source: { kind: 'model' } } } });
      h.emit({ type: 'turn/end', seq: 6, time: 0, data: {} });
    });
    const usage = h.read().nativeUsage;
    assert.equal(usage.tokenUsage.records, 1, 'only the object usage with an accepted field counts');
    assert.equal(usage.tokenUsage.inputTokens, 1);
    assert.equal(usage.tokenUsage.completeness, 'partial');
    assert.equal('lastAssistantMessage' in usage, false, 'malformed content is never text');
  });

  test('a write failure never escapes into the run', () => {
    const dir = mkdtempSync(join(root, 'write-failure-'));
    const ctx = fakeContext();
    apply(ctx, {
      usagePath: join(dir, 'missing-subdir', 'usage.json'), promptSha256: PROMPT_SHA256, cwd: dir,
      taskId: 'task-1', attemptId: 'attempt-1', generation: 1,
    });
    const session = rootSession('session-root-1', dir);
    assert.doesNotThrow(() => {
      ctx.emit('session/event', session, userMessageEvent(PROMPT));
      ctx.emit('session/event', session, assistantEvent('message-1', [textBlock('text')], { inputTokens: 1, outputTokens: 1, totalTokens: 2 }));
      ctx.emit('session/event', session, turnEndEvent({ kind: 'completed' }));
    });
    assert.equal(existsSync(join(dir, 'missing-subdir', 'usage.json')), false);
  });
});
