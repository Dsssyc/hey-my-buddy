/**
 * Synthetic headless-agent harness for the inquiry tests.
 *
 * It provides the minimum Cordis surface the REAL inquiry bridge plugin uses —
 * a session event feed, a live agent registry, and a tool registry — so tests
 * can mount the production bridge without dsh and without a model. Shared by the
 * Node service tests and the Python end-to-end tests (through a mock dsh).
 */
import { createHash, randomUUID } from 'node:crypto';

const { startInquiryBridge } = await import(new URL('../../plugins/inquiry-bridge.mjs', import.meta.url).href);

/** Minimal Cordis-like context: `on`, `emit`, `tools.register`, `agents.get`. */
function makeContext() {
  const listeners = new Map();
  const registrations = [];
  const ctx = {
    tools: {
      register(definition) {
        const record = { definition, disposed: false, scope: 'global' };
        registrations.push(record);
        return () => { record.disposed = true; };
      },
      registrations,
    },
    agents: { get: (id) => ctx.live.get(String(id)) },
    live: new Map(),
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

/**
 * Mount the real bridge over one synthetic agent.
 *
 * @param options - `{ prompt, cwd, socketPath, token, resultsPath, bind }`.
 * @returns handles for driving the fake agent from a test or mock dsh.
 */
export async function createFakeHeadlessRun({ prompt, cwd, socketPath, token, resultsPath, bind = true }) {
  const ctx = makeContext();
  const sessionId = `session-${randomUUID()}`;
  const session = { id: sessionId, header: { version: 3, id: sessionId, createdAt: Date.now(), cwd, isSeeded: false } };
  const agent = {
    id: sessionId,
    session,
    status: 'idle',
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
      agent.steered.push(message);
      ctx.emit('agent/inbox/inserted', { agent, message });
    },
  };
  ctx.live.set(sessionId, agent);

  const started = await startInquiryBridge(ctx, {
    socketPath,
    token,
    promptSha256: createHash('sha256').update(prompt, 'utf8').digest('hex'),
    cwd,
    resultsPath,
  });

  const emitIdentity = () => {
    ctx.emit('session/event', session, {
      type: 'user/message',
      seq: 1,
      time: Date.now(),
      data: { role: 'user', content: [{ type: 'text', text: prompt }], source: { kind: 'user' } },
    });
    agent.status = 'running';
  };
  const emitTool = (name, argsText = '{"command":"sleep 600"}', callId = 'call-1') => {
    ctx.emit('session/event', session, {
      type: 'tool/call', seq: 2, time: Date.now(), data: { turn: 1, step: 1, callId, name, arguments: argsText },
    });
  };
  const claim = (message, turn = 2) => ctx.emit('agent/inbox/claimed', { agent, message, turn });
  /**
   * dsh appends the admitted batch to the session's model-visible surface in
   * `step()`, after the pre-step decision. This is the ONLY delivery signal.
   */
  const deliver = (message, turn = 2) => ctx.emit('session/event', session, {
    type: 'user/message', seq: 3 + turn, time: Date.now(), data: message,
  });
  /** Claim the newest steered question, deliver it, and answer it through the scoped tool. */
  const answerLatest = async (text = 'mock answer') => {
    const message = agent.steered.at(-1);
    if (message === undefined) return false;
    claim(message);
    if (process.env.MOCK_INQUIRY_DELIVER !== '0') deliver(message);
    const registration = ctx.tools.registrations.at(-1);
    try {
      await registration?.definition.execute(
        { inquiryId: message.source?.inquiryId, answer: text },
        { agent, callId: 'call-answer' },
      );
    } catch {
      // The bridge refuses an answer to a question dsh never committed.
      return false;
    }
    return true;
  };

  if (bind) {
    emitIdentity();
    emitTool('bash');
  }

  return { ctx, agent, session, started, emitIdentity, emitTool, claim, deliver, answerLatest };
}
