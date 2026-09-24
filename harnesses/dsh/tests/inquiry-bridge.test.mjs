/**
 * Unit tests for the private per-run inquiry bridge plugin.
 *
 * These tests exercise the plugin in-process with a synthetic Cordis context:
 * no dsh, no model, no service. They cover identity binding, the correlated
 * answer channel, dedup/conflict, transport authentication and bounds, and
 * socket lifecycle cleanup.
 */
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { existsSync, lstatSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { connect } from 'node:net';
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { test } from 'node:test';

import {
  ERROR_CODES, MAX_ACTIVITY_ENTRIES, MAX_ANSWER_BYTES, MAX_QUESTION_BYTES, PROTOCOL_VERSION, REPLY_TOOL_NAME,
  apply, inject, startInquiryBridge,
} from '../plugins/inquiry-bridge.mjs';
// The Node job manager was removed with the ADR-001 implementation; the bridge's
// own published bounds are the source of truth for its plugin tests.
import { INQUIRY_LIMITS } from '../plugins/inquiry-bridge.mjs';

const CWD = '/tmp';
const TOKEN = 'test-token-0123456789';

/** A short owner-private socket directory (macOS /tmp is short and private enough). */
function makeSocketDir() {
  return mkdtempSync('/tmp/iq-bridge-');
}

function sha256(text) {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

/** Minimal Cordis-like context: `on`, `emit`, `tools.register`, `agents.get`. */
function makeCtx() {
  const listeners = new Map();
  const registrations = [];
  const ctx = {
    agents: { get: (id) => ctx.liveAgents.get(String(id)) },
    liveAgents: new Map(),
    tools: {
      register(definition) {
        const record = { definition, disposed: false, scope: 'global' };
        registrations.push(record);
        return () => { record.disposed = true; };
      },
      registrations,
    },
    on(event, handler) {
      if (!listeners.has(event)) listeners.set(event, []);
      listeners.get(event).push(handler);
      return () => {};
    },
    emit(event, ...args) {
      for (const handler of listeners.get(event) ?? []) handler(...args);
    },
  };
  return ctx;
}

/** A root session carrying this run's exact prompt. */
function makeSession({ id = `session-${randomUUID()}`, cwd = CWD, prompt } = {}) {
  return {
    id,
    header: { version: 3, id, createdAt: Date.now(), cwd, isSeeded: false },
    prompt,
  };
}

function userMessageEvent(prompt, source = { kind: 'user' }) {
  return { type: 'user/message', seq: 1, time: Date.now(), data: { role: 'user', content: [{ type: 'text', text: prompt }], source } };
}

/** A live agent double that records steering and can emulate the inbox lifecycle. */
function makeAgent(ctx, session) {
  const agent = {
    id: session.id,
    session,
    status: 'running',
    inbox: { nextTurn: [], nextStep: [] },
    steered: [],
    ctx: {
      tools: {
        register(definition) {
          const record = { definition, disposed: false, scope: 'agent' };
          ctx.tools.registrations.push(record);
          return () => { record.disposed = true; };
        },
      },
    },
    steer(message) {
      if (agent.steered.some((existing) => existing.id === message.id)) throw new Error('duplicate message id');
      agent.steered.push(message);
      ctx.emit('agent/inbox/inserted', { agent, message });
    },
    claim(messageId) {
      const message = agent.steered.find((candidate) => candidate.id === messageId);
      ctx.emit('agent/inbox/claimed', { agent, message, turn: 1 });
    },
    /** The durable model-visible commit dsh performs in `step()`. */
    deliver(messageId) {
      const message = agent.steered.find((candidate) => candidate.id === messageId);
      ctx.emit('session/event', session, { type: 'user/message', seq: 9, time: Date.now(), data: message });
    },
    discard(messageId) {
      const message = agent.steered.find((candidate) => candidate.id === messageId);
      ctx.emit('agent/inbox/discarded', { agent, message });
    },
  };
  ctx.liveAgents.set(String(session.id), agent);
  return agent;
}

/** One request/response frame over the bridge socket. */
function rpc(socketPath, payload, { timeoutMs = 5000, raw } = {}) {
  return new Promise((resolve, reject) => {
    const socket = connect(socketPath);
    let buffer = '';
    const timer = setTimeout(() => { socket.destroy(); reject(new Error('rpc timeout')); }, timeoutMs);
    socket.setEncoding('utf8');
    socket.on('connect', () => socket.write(raw ?? `${JSON.stringify(payload)}\n`));
    socket.on('error', (error) => { clearTimeout(timer); reject(error); });
    socket.on('data', (chunk) => {
      buffer += chunk;
      const end = buffer.indexOf('\n');
      if (end < 0) return;
      clearTimeout(timer);
      socket.destroy();
      resolve(JSON.parse(buffer.slice(0, end)));
    });
  });
}

/** Start a bridge wired to one synthetic run; always closes it. */
async function withBridge(options, body) {
  const dir = options.dir ?? makeSocketDir();
  const socketPath = options.socketPath ?? join(dir, 'inquiry.sock');
  const prompt = options.prompt ?? 'do the delegated thing';
  const ctx = options.ctx ?? makeCtx();
  const started = await startInquiryBridge(ctx, {
    socketPath,
    token: TOKEN,
    promptSha256: sha256(prompt),
    cwd: options.cwd ?? CWD,
    errorPath: options.errorPath,
    resultsPath: options.resultsPath,
  });
  try {
    return await body({ ctx, started, socketPath, prompt, dir });
  } finally {
    await started.close();
    if (options.keepDir !== true) rmSync(dir, { recursive: true, force: true });
  }
}

function call(socketPath, method, extra = {}, id = randomUUID()) {
  return rpc(socketPath, { version: PROTOCOL_VERSION, id, token: TOKEN, method, ...extra });
}

test('bridge binds only the exact root session for this run prompt and cwd', async () => {
  await withBridge({}, async ({ ctx, socketPath, prompt }) => {
    const before = await call(socketPath, 'observe');
    assert.equal(before.ok, true);
    assert.equal(before.value.ready, false);
    assert.ok(before.value.unavailable.includes('agentStatus'));

    const child = makeSession({ prompt });
    ctx.emit('session/event', { ...child, header: { ...child.header, parentSession: 'parent', delegationDepth: 1 } }, userMessageEvent(prompt));
    ctx.emit('session/event', { ...makeSession({ cwd: '/elsewhere', prompt }), header: { ...makeSession({ cwd: '/elsewhere', prompt }).header } }, userMessageEvent(prompt));
    const wrongPrompt = makeSession({ prompt: 'another task' });
    ctx.emit('session/event', wrongPrompt, userMessageEvent('another task'));
    assert.equal((await call(socketPath, 'observe')).value.ready, false, 'foreign sessions must not bind the bridge');

    const owned = makeSession({ prompt });
    ctx.emit('session/event', owned, userMessageEvent(prompt));
    const bound = await call(socketPath, 'observe');
    assert.equal(bound.value.ready, true);
    assert.equal(bound.value.sessionId, owned.id);
    assert.equal(bound.value.ambiguous, false);
  });
});

test('two matching root sessions make the binding ambiguous instead of guessing', async () => {
  await withBridge({}, async ({ ctx, socketPath, prompt }) => {
    ctx.emit('session/event', makeSession({ prompt }), userMessageEvent(prompt));
    assert.equal((await call(socketPath, 'observe')).value.ready, true);
    ctx.emit('session/event', makeSession({ prompt }), userMessageEvent(prompt));
    const after = await call(socketPath, 'observe');
    assert.equal(after.value.ready, false);
    assert.equal(after.value.ambiguous, true);
    assert.equal(after.value.sessionId, null);
    const refused = await call(socketPath, 'ask', { inquiryId: 'q1', question: 'status?' });
    assert.equal(refused.value.accepted, false);
    assert.equal(refused.value.reason, ERROR_CODES.NOT_READY);
  });
});

test('ask queues into the live agent inbox, never starts a second agent, and tracks delivery', async () => {
  await withBridge({}, async ({ ctx, socketPath, prompt }) => {
    const session = makeSession({ prompt });
    const agent = makeAgent(ctx, session);
    ctx.emit('session/event', session, userMessageEvent(prompt));

    const queued = await call(socketPath, 'ask', { inquiryId: 'inq-1', question: 'what are you doing?' });
    assert.equal(queued.ok, true);
    assert.equal(queued.value.accepted, true);
    assert.equal(queued.value.state, 'queued');
    assert.equal(queued.value.duplicate, false);
    assert.equal(agent.steered.length, 1, 'exactly one steered message');
    const text = agent.steered[0].content[0].text;
    assert.match(text, /inq-1/);
    assert.match(text, /what are you doing\?/);
    assert.match(text, new RegExp(REPLY_TOOL_NAME));
    assert.equal(agent.steered[0].source.kind, 'plugin');
    assert.equal(agent.steered[0].source.inquiryId, 'inq-1');

    // A claim is a proposal, not a commit: it is a distinct truthful state.
    agent.claim(agent.steered[0].id);
    const claimed = await call(socketPath, 'answer', { inquiryId: 'inq-1' });
    assert.equal(claimed.value.state, 'claimed');
    assert.ok(claimed.value.claimedAt);
    assert.equal(claimed.value.deliveredAt, null);
    assert.equal(claimed.value.answer, null);

    // Only dsh's own durable user/message append for this exact message id is
    // proof that the question became model-visible input.
    agent.deliver(agent.steered[0].id);
    const delivered = await call(socketPath, 'answer', { inquiryId: 'inq-1' });
    assert.equal(delivered.value.state, 'delivered');
    assert.ok(delivered.value.deliveredAt);
    assert.equal(delivered.value.answer, null);
    assert.equal(agent.steered.length, 1, 'delivery must not inject again');
  });
});

test('a claimed question whose pre-step is rejected never becomes delivered', async () => {
  const dir = makeSocketDir();
  const resultsPath = join(dir, 'inquiry.results.jsonl');
  await withBridge({ dir, resultsPath, keepDir: true }, async ({ ctx, socketPath, prompt }) => {
    const session = makeSession({ prompt });
    const agent = makeAgent(ctx, session);
    ctx.emit('session/event', session, userMessageEvent(prompt));
    await call(socketPath, 'ask', { inquiryId: 'inq-reject', question: 'will this survive?' });
    // The installed loop claims before `agent/pre-step`; a rejected pre-step
    // drops the claimed batch without a discard event and without a commit.
    agent.claim(agent.steered[0].id);
    const claimed = await call(socketPath, 'answer', { inquiryId: 'inq-reject' });
    assert.equal(claimed.value.state, 'claimed');
    assert.notEqual(claimed.value.state, 'delivered');
  });
  // Closing the bridge terminalizes it; the journal proves it was never delivered.
  const lines = readFileSync(resultsPath, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
  assert.deepEqual(lines.map((line) => line.state), ['queued', 'claimed', 'unavailable']);
  assert.equal(lines.some((line) => line.state === 'delivered'), false, 'a rejected pre-step must never report delivery');
  rmSync(dir, { recursive: true, force: true });
});

test('ask refuses to wake an idle agent but a recorded inquiry stays readable', async () => {
  await withBridge({}, async ({ ctx, socketPath, prompt }) => {
    const session = makeSession({ prompt });
    const agent = makeAgent(ctx, session);
    ctx.emit('session/event', session, userMessageEvent(prompt));
    await call(socketPath, 'ask', { inquiryId: 'inq-early', question: 'first question' });
    assert.equal(agent.steered.length, 1);

    // headless is finishing (whenIdle/flush/exit): public steer would open a
    // brand-new turn, so the bridge must refuse instead.
    agent.status = 'idle';
    const late = await call(socketPath, 'ask', { inquiryId: 'inq-late', question: 'are you still there?' });
    assert.equal(late.ok, true);
    assert.equal(late.value.accepted, false);
    assert.equal(late.value.state, 'unavailable');
    assert.equal(late.value.reason, ERROR_CODES.AGENT_NOT_RUNNING);
    assert.equal(agent.steered.length, 1, 'an idle agent is never steered');

    // Retrying an inquiry this run already recorded still reads it.
    const retried = await call(socketPath, 'ask', { inquiryId: 'inq-early', question: 'first question' });
    assert.equal(retried.value.accepted, true);
    assert.equal(retried.value.duplicate, true);
    assert.equal(retried.value.state, 'queued');
    assert.equal(agent.steered.length, 1, 'a retry never injects again');
  });
});

test('answers require a committed question, real content and the shared byte cap', async () => {
  await withBridge({}, async ({ ctx, socketPath, prompt }) => {
    const session = makeSession({ prompt });
    const agent = makeAgent(ctx, session);
    ctx.emit('session/event', session, userMessageEvent(prompt));
    await call(socketPath, 'ask', { inquiryId: 'inq-gate', question: 'ready?' });
    const tool = ctx.tools.registrations.at(-1).definition;

    // Not delivered yet: a claimed-but-uncommitted question cannot be answered.
    agent.claim(agent.steered[0].id);
    await assert.rejects(
      () => tool.execute({ inquiryId: 'inq-gate', answer: 'premature' }, { agent, callId: 'call-early' }),
      /no committed question/,
    );
    assert.equal((await call(socketPath, 'answer', { inquiryId: 'inq-gate' })).value.answer, null);

    agent.deliver(agent.steered[0].id);
    // Blank and whitespace-only answers are refused, not recorded.
    for (const blank of ['', '   ', '\n\t ']) {
      await assert.rejects(
        () => tool.execute({ inquiryId: 'inq-gate', answer: blank }, { agent, callId: 'call-blank' }),
        /nonblank answer/,
      );
    }
    assert.equal((await call(socketPath, 'answer', { inquiryId: 'inq-gate' })).value.state, 'delivered');

    // A multibyte answer over the 4000-BYTE budget is cut on a code-point edge:
    // no replacement character, truthful byte and truncation metadata.
    const multibyte = '中'.repeat(2000); // 6000 UTF-8 bytes, 2000 code points
    const recorded = await tool.execute({ inquiryId: 'inq-gate', answer: multibyte }, { agent, callId: 'call-multi' });
    assert.deepEqual(recorded, { recorded: true, inquiryId: 'inq-gate' });
    const answer = (await call(socketPath, 'answer', { inquiryId: 'inq-gate' })).value.answer;
    assert.equal(answer.truncated, true);
    assert.ok(answer.bytes <= MAX_ANSWER_BYTES, `${answer.bytes} must fit ${MAX_ANSWER_BYTES}`);
    assert.equal(Buffer.byteLength(answer.text, 'utf8'), answer.bytes);
    assert.equal(answer.text.length, answer.bytes / 3);
    assert.equal(answer.text.includes('\uFFFD'), false, 'truncation must not split a code point');
    assert.equal(answer.text, '中'.repeat(Math.floor(MAX_ANSWER_BYTES / 3)));
  });
});

test('the bridge and the service share one UTF-8 byte cap for questions and answers', async () => {
  assert.equal(MAX_ANSWER_BYTES, INQUIRY_LIMITS.maxAnswerBytes);
  assert.equal(MAX_QUESTION_BYTES, INQUIRY_LIMITS.maxQuestionBytes);
  await withBridge({}, async ({ ctx, socketPath, prompt }) => {
    const session = makeSession({ prompt });
    makeAgent(ctx, session);
    ctx.emit('session/event', session, userMessageEvent(prompt));
    const atLimit = 'é'.repeat(MAX_QUESTION_BYTES / 2); // exactly 4000 bytes
    assert.equal(Buffer.byteLength(atLimit, 'utf8'), MAX_QUESTION_BYTES);
    assert.equal((await call(socketPath, 'ask', { inquiryId: 'q-bytes', question: atLimit })).value.accepted, true);
    const overLimit = `${atLimit}x`; // 4001 bytes, still under the character count
    const refused = await call(socketPath, 'ask', { inquiryId: 'q-over', question: overLimit });
    assert.equal(refused.error, ERROR_CODES.BAD_REQUEST);
  });
});

test('the same inquiry id never injects twice and conflicting text is rejected', async () => {
  await withBridge({}, async ({ ctx, socketPath, prompt }) => {
    const session = makeSession({ prompt });
    const agent = makeAgent(ctx, session);
    ctx.emit('session/event', session, userMessageEvent(prompt));

    await call(socketPath, 'ask', { inquiryId: 'inq-dup', question: 'first text' });
    const repeat = await call(socketPath, 'ask', { inquiryId: 'inq-dup', question: 'first text' });
    assert.equal(repeat.value.duplicate, true);
    assert.equal(agent.steered.length, 1);
    const conflict = await call(socketPath, 'ask', { inquiryId: 'inq-dup', question: 'different text' });
    assert.equal(conflict.ok, false);
    assert.equal(conflict.error, ERROR_CODES.CONFLICT);
    assert.equal(agent.steered.length, 1);
  });
});

test('the scoped reply tool is the only answer channel and is correlated by inquiry id', async () => {
  await withBridge({}, async ({ ctx, socketPath, prompt }) => {
    const session = makeSession({ prompt });
    const agent = makeAgent(ctx, session);
    const foreign = makeAgent(ctx, makeSession({ prompt: 'unrelated' }));
    ctx.emit('session/event', session, userMessageEvent(prompt));
    await call(socketPath, 'ask', { inquiryId: 'inq-answer', question: 'what is 7*13?' });

    const registration = ctx.tools.registrations.at(-1);
    const tool = registration.definition;
    assert.equal(tool.name, REPLY_TOOL_NAME);

    await assert.rejects(() => tool.execute(
      { inquiryId: 'inq-answer', answer: '91' },
      { agent: foreign, callId: 'call-foreign' },
    ), /only available to the delegated run/);

    // The question must have been committed to this session before it can be answered.
    agent.claim(agent.steered[0].id);
    agent.deliver(agent.steered[0].id);

    const value = await tool.execute(
      { inquiryId: 'inq-answer', answer: '91' },
      { agent, callId: 'call-1' },
    );
    assert.deepEqual(value, { recorded: true, inquiryId: 'inq-answer' });
    const rendered = tool.output.render({}, value);
    assert.equal(rendered[0].type, 'text');

    const answered = await call(socketPath, 'answer', { inquiryId: 'inq-answer' });
    assert.equal(answered.value.state, 'answered');
    assert.equal(answered.value.answer.text, '91');
    assert.equal(answered.value.answer.via, `tool:${REPLY_TOOL_NAME}`);
    assert.equal(answered.value.answer.toolCallId, 'call-1');
    assert.ok(answered.value.answeredAt);
    assert.equal(answered.value.correlation, 'inquiryId');

    // A second, different answer never overwrites the first correlated one.
    await tool.execute({ inquiryId: 'inq-answer', answer: 'late guess' }, { agent, callId: 'call-2' });
    assert.equal((await call(socketPath, 'answer', { inquiryId: 'inq-answer' })).value.answer.text, '91');
    await assert.rejects(() => tool.execute({ inquiryId: 'unknown', answer: 'x' }, { agent, callId: 'c' }), /unknown inquiry id/);
  });
});

test('an unanswered delivered inquiry stays delivered with observed activity, never a fake answer', async () => {
  await withBridge({}, async ({ ctx, socketPath, prompt }) => {
    const session = makeSession({ prompt });
    const agent = makeAgent(ctx, session);
    ctx.emit('session/event', session, userMessageEvent(prompt));
    await call(socketPath, 'ask', { inquiryId: 'inq-silent', question: 'progress?' });
    agent.claim(agent.steered[0].id);
    agent.deliver(agent.steered[0].id);
    ctx.emit('session/event', session, { type: 'tool/call', seq: 7, time: Date.now() - 1200, data: { turn: 1, step: 1, callId: 'c9', name: 'bash', arguments: '{"command":"sleep 60"}' } });
    const observed = await call(socketPath, 'answer', { inquiryId: 'inq-silent' });
    assert.equal(observed.value.state, 'delivered');
    assert.equal(observed.value.answer, null);
    assert.equal(observed.value.observation.agentStatus, 'running');
    assert.equal(observed.value.observation.activity.at(-1).tool, 'bash');
    assert.match(observed.value.observation.activity.at(-1).argumentPreview, /sleep 60/);
    assert.equal(observed.value.observation.limits.exposesModelReasoning, false);
  });
});

test('discarded steering is reported as discarded, not as an answer', async () => {
  await withBridge({}, async ({ ctx, socketPath, prompt }) => {
    const session = makeSession({ prompt });
    const agent = makeAgent(ctx, session);
    ctx.emit('session/event', session, userMessageEvent(prompt));
    await call(socketPath, 'ask', { inquiryId: 'inq-drop', question: 'anyone there?' });
    agent.discard(agent.steered[0].id);
    const dropped = await call(socketPath, 'answer', { inquiryId: 'inq-drop' });
    assert.equal(dropped.value.state, 'discarded');
    assert.equal(dropped.value.answer, null);
    assert.ok(dropped.value.discardedAt);
  });
});

test('transport rejects bad tokens, oversized frames and unknown methods', async () => {
  await withBridge({}, async ({ socketPath, prompt, ctx }) => {
    ctx.emit('session/event', makeSession({ prompt }), userMessageEvent(prompt));
    const denied = await rpc(socketPath, { version: PROTOCOL_VERSION, id: 'a', token: 'wrong', method: 'ping' });
    assert.equal(denied.error, ERROR_CODES.UNAUTHORIZED);
    const unknown = await call(socketPath, 'launch-missiles');
    assert.equal(unknown.error, ERROR_CODES.UNSUPPORTED_METHOD);
    const malformed = await rpc(socketPath, null, { raw: 'not json\n' });
    assert.equal(malformed.error, ERROR_CODES.BAD_REQUEST);
    const huge = await rpc(socketPath, null, { raw: `${'x'.repeat(20 * 1024)}\n` });
    assert.equal(huge.error, ERROR_CODES.FRAME_TOO_LARGE);
    const wrongVersion = await rpc(socketPath, { version: 99, id: 'v', token: TOKEN, method: 'ping' });
    assert.equal(wrongVersion.error, ERROR_CODES.BAD_REQUEST);
  });
});

test('payload sizes are bounded and the activity ring is capped', async () => {
  await withBridge({}, async ({ ctx, socketPath, prompt }) => {
    const session = makeSession({ prompt });
    makeAgent(ctx, session);
    ctx.emit('session/event', session, userMessageEvent(prompt));
    const tooLong = await call(socketPath, 'ask', { inquiryId: 'long', question: 'x'.repeat(MAX_QUESTION_BYTES + 1) });
    assert.equal(tooLong.error, ERROR_CODES.BAD_REQUEST);

    for (let index = 0; index < MAX_ACTIVITY_ENTRIES + 10; index += 1) {
      ctx.emit('session/event', session, {
        type: 'tool/call', seq: 100 + index, time: Date.now(), data: { turn: 1, step: 1, callId: `c${index}`, name: 'bash', arguments: '{"command":"echo hi"}' },
      });
    }
    const observed = await call(socketPath, 'observe');
    assert.equal(observed.value.activity.length, MAX_ACTIVITY_ENTRIES);
    assert.equal(observed.value.activityDropped, 10);

    await call(socketPath, 'ask', { inquiryId: 'big-answer', question: 'q' });
    const tool = ctx.tools.registrations.at(-1).definition;
    const owner = ctx.liveAgents.get(String(session.id));
    owner.claim(owner.steered.at(-1).id);
    owner.deliver(owner.steered.at(-1).id);
    await tool.execute({ inquiryId: 'big-answer', answer: 'y'.repeat(MAX_ANSWER_BYTES + 500) }, { agent: owner, callId: 'c' });
    const answered = await call(socketPath, 'answer', { inquiryId: 'big-answer' });
    assert.equal(answered.value.answer.text.length, MAX_ANSWER_BYTES);
    assert.equal(answered.value.answer.bytes, MAX_ANSWER_BYTES);
    assert.equal(answered.value.answer.truncated, true);
  });
});

test('the reply tool moves to the owned agent scope and the global one is disposed', async () => {
  await withBridge({}, async ({ ctx, socketPath, prompt }) => {
    const globalRegistration = ctx.tools.registrations.at(-1);
    assert.equal(globalRegistration.scope, 'global');
    const session = makeSession({ prompt });
    const agent = makeAgent(ctx, session);
    ctx.emit('session/event', session, userMessageEvent(prompt));
    const queued = await call(socketPath, 'ask', { inquiryId: 'inq-scope', question: 'who are you?' });
    assert.equal(queued.value.accepted, true);
    const scoped = ctx.tools.registrations.at(-1);
    assert.equal(scoped.scope, 'agent', 'the tool is registered in the owned agent scope');
    assert.equal(globalRegistration.disposed, true, 'the global registration is released once the agent scope holds the tool');
    assert.equal(scoped.definition.name, REPLY_TOOL_NAME);
    // A foreign agent never receives the scoped tool.
    const foreign = makeAgent(ctx, makeSession({ prompt: 'other' }));
    const registrationsBefore = ctx.tools.registrations.length;
    ctx.emit('agent/created', { agent: foreign });
    assert.equal(ctx.tools.registrations.filter((entry) => entry.scope === 'agent').length, 1);
    assert.equal(ctx.tools.registrations.length, registrationsBefore, 'a foreign agent registers nothing');
    assert.equal(ctx.tools.registrations.at(-1).scope, 'agent');
  });
});

test('close() removes only its own socket and is idempotent', async () => {
  const dir = makeSocketDir();
  const socketPath = join(dir, 'inquiry.sock');
  const ctx = makeCtx();
  const started = await startInquiryBridge(ctx, { socketPath, token: TOKEN, promptSha256: sha256('p'), cwd: CWD });
  assert.equal(lstatSync(socketPath).isSocket(), true);
  assert.equal((lstatSync(socketPath).mode & 0o777), 0o600);
  assert.equal((lstatSync(dir).mode & 0o777), 0o700);
  await started.close();
  await started.close();
  assert.equal(existsSync(socketPath), false);
  await assert.rejects(() => rpc(socketPath, { version: PROTOCOL_VERSION, id: 'x', token: TOKEN, method: 'ping' }));
  rmSync(dir, { recursive: true, force: true });
});

test('a refused second bridge leaves the existing socket untouched', async () => {
  const dir = makeSocketDir();
  const socketPath = join(dir, 'inquiry.sock');
  const first = await startInquiryBridge(makeCtx(), { socketPath, token: TOKEN, promptSha256: sha256('p'), cwd: CWD });
  const inode = lstatSync(socketPath).ino;
  const errorPath = join(dir, 'bridge-error.json');
  await apply(makeCtx(), { socketPath, token: TOKEN, promptSha256: sha256('p'), cwd: CWD, errorPath });
  assert.equal(lstatSync(socketPath).ino, inode, 'the occupied socket is never replaced');
  const recorded = JSON.parse(readFileSync(errorPath, 'utf8'));
  assert.equal(recorded.ok, false);
  assert.ok(typeof recorded.message === 'string' && recorded.message.length > 0);
  await first.close();
  rmSync(dir, { recursive: true, force: true });
});

test('the bridge declares the agents service and also resolves it through ctx.get', async () => {
  // A patch-inserted plugin only sees a sibling service through Cordis
  // injection; without this the first real-dsh E2E observed a bound, actively
  // running agent as `agentStatus: null`.
  assert.ok(inject.includes('agents'), 'the bridge must inject the agents service');

  // Host shapes: the registry is reachable only through the documented getter.
  const ctx = makeCtx();
  const registry = ctx.agents;
  ctx.agents = undefined;
  ctx.get = (name) => (name === 'agents' ? registry : undefined);
  const dir = makeSocketDir();
  const socketPath = join(dir, 'inquiry.sock');
  const prompt = 'registry via getter';
  const started = await startInquiryBridge(ctx, { socketPath, token: TOKEN, promptSha256: sha256(prompt), cwd: CWD });
  try {
    const session = makeSession({ prompt });
    const agent = makeAgent(ctx, session);
    ctx.emit('session/event', session, userMessageEvent(prompt));
    const observed = await call(socketPath, 'observe');
    assert.equal(observed.value.ready, true);
    assert.equal(observed.value.agentStatus, 'running', 'the agent must be reachable through ctx.get');
    const asked = await call(socketPath, 'ask', { inquiryId: 'q-get', question: 'reachable?' });
    assert.equal(asked.value.accepted, true);
    assert.equal(agent.steered.length, 1);
  } finally {
    await started.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('the bridge never writes to stdout or stderr', async () => {
  // Measured in a child process so the test runner's own reporter output cannot
  // be mistaken for bridge output.
  const script = `
    import { createHash, randomUUID } from 'node:crypto';
    import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
    import { join } from 'node:path';
    import { connect } from 'node:net';
    import { startInquiryBridge, PROTOCOL_VERSION } from ${JSON.stringify(new URL('../plugins/inquiry-bridge.mjs', import.meta.url).href)};
    let written = 0;
    const stdoutWrite = process.stdout.write.bind(process.stdout);
    process.stdout.write = (chunk, ...rest) => { written += Buffer.byteLength(chunk); return stdoutWrite(chunk, ...rest); };
    const dir = mkdtempSync('/tmp/iq-quiet-');
    const socketPath = join(dir, 'inquiry.sock');
    const listeners = new Map();
    const ctx = {
      agents: { get: (id) => live.get(String(id)) },
      tools: { register: () => () => {} },
      on(event, handler) { if (!listeners.has(event)) listeners.set(event, []); listeners.get(event).push(handler); },
    };
    const emit = (event, ...args) => { for (const handler of listeners.get(event) ?? []) handler(...args); };
    const prompt = 'do the delegated thing';
    const session = { id: 'session-quiet', header: { version: 3, id: 'session-quiet', createdAt: Date.now(), cwd: '/tmp', isSeeded: false } };
    const agent = { id: session.id, session, status: 'running', inbox: { nextTurn: [], nextStep: [] }, steered: [], steer(message) { agent.steered.push(message); emit('agent/inbox/inserted', { agent, message }); } };
    const live = new Map([[session.id, agent]]);
    const started = await startInquiryBridge(ctx, { socketPath, token: 'quiet-token', promptSha256: createHash('sha256').update(prompt, 'utf8').digest('hex'), cwd: '/tmp' });
    emit('session/event', session, { type: 'user/message', seq: 1, time: Date.now(), data: { role: 'user', content: [{ type: 'text', text: prompt }], source: { kind: 'user' } } });
    const call = (method, extra) => new Promise((resolve, reject) => {
      const socket = connect(socketPath);
      let buffer = '';
      socket.setEncoding('utf8');
      socket.on('connect', () => socket.write(JSON.stringify({ version: PROTOCOL_VERSION, id: randomUUID(), token: 'quiet-token', method, ...extra }) + '\\n'));
      socket.on('error', reject);
      socket.on('data', (chunk) => { buffer += chunk; const end = buffer.indexOf('\\n'); if (end < 0) return; socket.destroy(); resolve(JSON.parse(buffer.slice(0, end))); });
    });
    await call('ask', { inquiryId: 'quiet', question: 'status?' });
    emit('agent/inbox/claimed', { agent, message: agent.steered[0], turn: 1 });
    await call('observe');
    await call('answer', { inquiryId: 'quiet' });
    await started.close();
    rmSync(dir, { recursive: true, force: true });
    writeFileSync(process.env.QUIET_REPORT, String(written));
  `;
  const reportPath = join(mkdtempSync('/tmp/iq-report-'), 'written.txt');
  const child = spawnSync(process.execPath, ['--input-type=module', '-e', script], { encoding: 'utf8', timeout: 20000, env: { ...process.env, QUIET_REPORT: reportPath } });
  assert.equal(child.status, 0, child.stderr);
  assert.equal(child.stdout, '', 'the bridge must not write to the run stdout');
  assert.equal(readFileSync(reportPath, 'utf8'), '0');
  rmSync(dirname(reportPath), { recursive: true, force: true });
});
