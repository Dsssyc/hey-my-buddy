#!/usr/bin/env node
/**
 * Mock run.mjs for the inquiry service tests.
 *
 * It stands in for `scripts/run.mjs` inside a real `JobManager`: it mounts the
 * REAL inquiry bridge plugin over a synthetic Cordis context, emulates one
 * headless agent (session identity, inbox, scoped reply tool), and then writes
 * the single JSON run result that `JobManager.finish` expects. No dsh and no
 * model are involved.
 *
 * Behavior is driven by MOCK_INQUIRY_* environment variables:
 *   MOCK_INQUIRY_PROMPT       exact task text (defaults to --task-file content)
 *   MOCK_INQUIRY_BIND=0       never emit the identity user message
 *   MOCK_INQUIRY_HOLD_MS      how long to stay alive (default 1200)
 *   MOCK_INQUIRY_ANSWER_MS    after N ms, claim the question and answer it
 *   MOCK_INQUIRY_ANSWER       answer text (default "mock answer")
 *   MOCK_INQUIRY_EXIT_CODE    process exit code (default 0)
 *   MOCK_INQUIRY_STATUS       result status field (default ok)
 */
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';

const { startInquiryBridge } = await import(new URL('../../plugins/inquiry-bridge.mjs', import.meta.url).href);

const argv = process.argv.slice(2);
/** Accept both `--flag value` and `--flag=value`, like the real runner does. */
const argOf = (name) => {
  const inline = argv.find((entry) => entry.startsWith(`${name}=`));
  if (inline !== undefined) return inline.slice(name.length + 1);
  const index = argv.indexOf(name);
  return index === -1 ? undefined : argv[index + 1];
};
const cwd = argOf('--cwd') ?? process.cwd();
const taskFile = argOf('--task-file');
const socketPath = argOf('--inquiry-socket');
const token = argOf('--inquiry-token');
const resultsPath = argOf('--inquiry-results');
const prompt = process.env.MOCK_INQUIRY_PROMPT ?? readFileSync(taskFile, 'utf8');

/** Minimal Cordis-like context. */
function makeCtx() {
  const listeners = new Map();
  const registrations = [];
  return {
    tools: {
      register(definition) {
        const record = { definition, disposed: false, scope: 'global' };
        registrations.push(record);
        return () => { record.disposed = true; };
      },
      registrations,
    },
    agents: { get: (id) => live.get(String(id)) },
    on(event, handler) {
      if (!listeners.has(event)) listeners.set(event, []);
      listeners.get(event).push(handler);
    },
    emit(event, ...args) {
      for (const handler of listeners.get(event) ?? []) handler(...args);
    },
  };
}

const listeners = new Map();
const ctx = makeCtx();
const session = { id: `session-${randomUUID()}`, header: { version: 3, id: 'session-mock', createdAt: Date.now(), cwd, isSeeded: false } };
session.header.id = session.id;
const agent = {
  id: session.id,
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
const live = new Map([[session.id, agent]]);
void listeners;

const started = await startInquiryBridge(ctx, { socketPath, token, promptSha256: createHash('sha256').update(prompt, 'utf8').digest('hex'), cwd, resultsPath });
if (process.env.MOCK_INQUIRY_BIND !== '0') {
  ctx.emit('session/event', session, { type: 'user/message', seq: 1, time: Date.now(), data: { role: 'user', content: [{ type: 'text', text: prompt }], source: { kind: 'user' } } });
  agent.status = 'running';
  ctx.emit('session/event', session, { type: 'tool/call', seq: 2, time: Date.now(), data: { turn: 1, step: 1, callId: 'call-1', name: 'bash', arguments: '{"command":"sleep 600"}' } });
}

const answerMs = Number(process.env.MOCK_INQUIRY_ANSWER_MS ?? 0);
if (answerMs > 0) {
  setTimeout(() => {
    const message = agent.steered.at(-1);
    if (message === undefined) return;
    ctx.emit('agent/inbox/claimed', { agent, message, turn: 2 });
    if (process.env.MOCK_INQUIRY_DELIVER !== '0') {
      // Real dsh commits the admitted batch to the model-visible surface in
      // `step()`; a claim alone is never delivery.
      ctx.emit('session/event', session, { type: 'user/message', seq: 3, time: Date.now(), data: message });
    }
    const inquiryId = message.source?.inquiryId;
    const registration = ctx.tools.registrations.at(-1);
    registration?.definition.execute(
      { inquiryId, answer: process.env.MOCK_INQUIRY_ANSWER ?? 'mock answer' },
      { agent, callId: 'call-answer' },
    ).catch(() => {});
  }, answerMs);
}

const holdMs = Number(process.env.MOCK_INQUIRY_HOLD_MS ?? 1200);
setTimeout(async () => {
  await started.close();
  const payload = {
    status: process.env.MOCK_INQUIRY_STATUS ?? 'ok',
    mode: 'run',
    exitCode: 0,
    signal: null,
    error: null,
    elapsedSeconds: Math.round(holdMs / 100) / 10,
    timeoutSeconds: 1800,
    requested: { provider: 'mock', model: 'mock', reasoningEffort: 'max' },
    cwd,
    taskFile,
    dshBin: null,
    inputDelivery: 'inline',
    logPaths: null,
    inquiry: { enabled: true, socketPath, resultsPath, errorPath: null, error: null, note: 'mock' },
    finalText: 'mock run finished',
    finalTextTruncated: false,
    workspace: { enabled: false, bound: false, id: null, path: cwd, sessionId: session.id },
    processState: { pid: process.pid, shutdownConfirmed: true },
    note: 'mock',
  };
  process.stdout.write(`${JSON.stringify(payload)}\n`, () => {
    writeFileSync(`${socketPath}.runner-exit`, 'done');
    process.exit(Number(process.env.MOCK_INQUIRY_EXIT_CODE ?? 0));
  });
}, holdMs);
