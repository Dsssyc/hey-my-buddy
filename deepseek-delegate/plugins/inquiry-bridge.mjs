/**
 * deepseek-delegate inquiry bridge.
 *
 * A private, per-run Cordis plugin that gives the owning Buddy service exactly
 * one capability inside a live delegated headless run: ask the run's own live
 * agent an out-of-band progress question through the public agent input API,
 * and read back a correlated answer.
 *
 * What it deliberately does NOT do:
 * - it never calls a model, never starts, cancels, restarts, or re-scopes the
 *   delegated task, and never touches the runner's timeout;
 * - it never reads or writes dsh storage files;
 * - it never exposes raw model reasoning: the progress view carries event
 *   types, sequence numbers, timestamps, tool names, and a bounded preview of
 *   tool ARGUMENTS only;
 * - it never writes to stdout/stderr, so the run's single JSON result and its
 *   final text are unaffected.
 *
 * Identity contract (same rule as `session-capture.mjs`, verified against the
 * installed dsh sources):
 * a root session (no fork parent, no `subagent` origin, no positive delegation
 * depth) whose `header.cwd` equals this run's canonical cwd and whose FIRST
 * ordinary user message hashes to this run's delivered prompt is this run's own
 * agent. Nothing is guessed from timestamps, newest files, or "the first
 * session seen"; an ambiguous match disables the bridge instead of picking one.
 *
 * Question lifecycle: `queued` (durably pending in the inbox) -> `claimed`
 * (the loop proposed it for a step) -> `delivered` (dsh durably committed it to
 * this session's model-visible surface) -> `answered`. A rejected pre-step
 * drops a claimed message without committing it, so a claim is never reported
 * as delivery; `discarded` and `unavailable` are terminal and never answered.
 *
 * Answer channel: a scoped reply tool (`buddy_inquiry_reply`) registered on the
 * identified agent's own context. The answer is correlated by `inquiryId`, so a
 * value is only ever reported as an answer when it arrives through that tool
 * call carrying the exact inquiry id. Natural-language assistant output is
 * never treated as an answer.
 *
 * Transport: one bounded newline-terminated JSON frame per Unix-socket
 * connection, authenticated by a per-run random token that the owning service
 * generated and passed through the run's private patch overlay. The socket and
 * its parent directory are owner-private; the listener only ever unlinks the
 * inode it created.
 *
 * @module deepseek-delegate/plugins/inquiry-bridge
 */
import { randomBytes, randomUUID, createHash } from 'node:crypto';
import { appendFileSync, chmodSync, linkSync, lstatSync, mkdirSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { dirname, isAbsolute, join } from 'node:path';

/** Stable Cordis plugin name; the run's patch row mirrors it. */
export const name = 'deepseek-delegate-inquiry-bridge';

/**
 * Services this bridge needs from the host tree.
 *
 * `agents` MUST be declared: a patch-inserted plugin only sees a sibling
 * service through Cordis injection, and without it `ctx.agents` is undefined —
 * which the first real-dsh E2E caught as `agentStatus: null` on a bound,
 * actively running agent. `tools` carries the scoped answer tool; a missing
 * tool runtime only disables the answer channel, so it is not load-bearing.
 */
export const inject = ['agents', 'tools'];

/** Wire protocol version understood by this bridge. */
export const PROTOCOL_VERSION = 1;

/** Largest accepted request frame in bytes, excluding the trailing newline. */
export const MAX_FRAME_BYTES = 16 * 1024;

/** Largest response this bridge will write, in bytes. */
export const MAX_RESPONSE_BYTES = 32 * 1024;

/** How long a connection may take to deliver one complete request frame. */
export const CONNECTION_TIMEOUT_MS = 10_000;

/** Upper bound on waiting for in-flight work while disposing. */
const SHUTDOWN_GRACE_MS = 2_000;

/** Bound on the retained activity ring. */
export const MAX_ACTIVITY_ENTRIES = 20;

/** Bound on one tool-argument preview inside the activity ring. */
export const MAX_ARGUMENT_PREVIEW_CHARS = 160;

/**
 * Bound on an operator question and on a recorded answer, in UTF-8 BYTES. The
 * owning service enforces the same byte budget, so a multibyte answer that fits
 * here can never be rejected there after this bridge already mutated state.
 */
export const MAX_QUESTION_BYTES = 4000;
export const MAX_ANSWER_BYTES = 4000;

/** Bound on outstanding inquiries retained per run. */
export const MAX_INQUIRIES = 32;

/**
 * Bound on the append-only answer journal. The journal is this run's own
 * private file (never a dsh storage file); it exists so a correlated answer
 * stays readable after the headless process has exited.
 */
export const MAX_JOURNAL_BYTES = 1024 * 1024;

/** The inquiry bounds this plugin enforces, published for its tests and clients. */
export const INQUIRY_LIMITS = Object.freeze({
  maxQuestionBytes: MAX_QUESTION_BYTES,
  maxAnswerBytes: MAX_ANSWER_BYTES,
  maxInquiriesPerRun: MAX_INQUIRIES,
  maxInquiryId: 128,
});

/** Conservative usable length of a Unix socket path in bytes. */
export const UNIX_SOCKET_PATH_BUDGET = process.platform === 'linux' ? 105 : 101;

/** The model-facing reply tool registered for the identified agent. */
export const REPLY_TOOL_NAME = 'buddy_inquiry_reply';

/** Fixed failure vocabulary; a response `error` is always one of these codes. */
export const ERROR_CODES = Object.freeze({
  BAD_REQUEST: 'bad-request',
  UNAUTHORIZED: 'unauthorized',
  FRAME_TOO_LARGE: 'frame-too-large',
  TIMEOUT: 'timeout',
  UNSUPPORTED_METHOD: 'unsupported-method',
  NOT_READY: 'not-ready',
  AGENT_GONE: 'agent-gone',
  AGENT_NOT_RUNNING: 'agent-not-running',
  CONFLICT: 'conflict',
  TOO_MANY: 'too-many',
  INTERNAL: 'internal',
});

const ERROR_CODE_SET = new Set(Object.values(ERROR_CODES));
const HARDLINK_FALLBACK_CODES = new Set(['EPERM', 'ENOTSUP', 'EOPNOTSUPP', 'EXDEV']);

/** Plain JSON object, excluding arrays and null. */
function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** Non-empty string. */
function isUsableString(value) {
  return typeof value === 'string' && value !== '';
}

/** Lowercase hex SHA-256 of one UTF-8 string. */
function sha256(text) {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

/** Length of one string in UTF-8 bytes. */
function utf8Bytes(text) {
  return Buffer.byteLength(text, 'utf8');
}

/**
 * Cut one string to at most `maxBytes` UTF-8 bytes without splitting a code
 * point, so the result is always valid Unicode text rather than a lone
 * replacement character. Returns the byte length actually kept.
 */
function truncateUtf8(text, maxBytes) {
  const buffer = Buffer.from(text, 'utf8');
  if (buffer.length <= maxBytes) return { text, bytes: buffer.length, truncated: false };
  let end = maxBytes;
  // A continuation byte at the cut means the previous code point is split.
  while (end > 0 && (buffer[end] & 0xc0) === 0x80) end -= 1;
  return { text: buffer.subarray(0, end).toString('utf8'), bytes: end, truncated: true };
}

/** Join the text blocks of a message the way the CLI hashes the delivered prompt. */
function messageText(message) {
  if (!isObject(message)) return undefined;
  const content = message.content;
  if (!Array.isArray(content)) return undefined;
  let text = '';
  for (const block of content) {
    if (!isObject(block) || block.type !== 'text') continue;
    if (typeof block.text !== 'string') return undefined;
    text += block.text;
  }
  return text;
}

/** Root lineage: no fork parent, no subagent origin, no positive delegation depth. */
function isRootSession(session) {
  const header = session?.header;
  if (!isObject(header)) return false;
  if (header.parentSession !== undefined && header.parentSession !== null) return false;
  if (header.origin === 'subagent') return false;
  if (typeof header.delegationDepth === 'number' && header.delegationDepth > 0) return false;
  return true;
}

/** One-line, length-bounded preview of a tool call's raw argument text. */
function argumentPreview(raw) {
  if (typeof raw !== 'string' || raw === '') return undefined;
  const flat = raw.replace(/\s+/g, ' ').trim();
  if (flat === '') return undefined;
  return flat.length > MAX_ARGUMENT_PREVIEW_CHARS ? `${flat.slice(0, MAX_ARGUMENT_PREVIEW_CHARS)}…` : flat;
}

/**
 * The model-facing instruction delivered through `agent.steer`. It is written
 * as an out-of-band operator inquiry so the agent answers it and then continues
 * the original task unchanged.
 */
function inquiryText(inquiryId, question) {
  return [
    `[buddy inquiry ${inquiryId}] The operator of this delegated run asked a progress question while you were working:`,
    '',
    question,
    '',
    'This is an out-of-band status inquiry, not a new task and not a scope change.',
    'Do not abandon, restart, or re-scope the original task, and do not treat this message as its replacement.',
    `Answer it with the \`${REPLY_TOOL_NAME}\` tool: call it once with {"inquiryId":"${inquiryId}","answer":"<your answer>"}.`,
    'If that tool is not available in the current step, continue working; answer at the next step where it is offered.',
    'Then continue and finish the original task exactly as before.',
  ].join('\n');
}

/** The reply tool definition, built without importing dsh packages (see module docs). */
function replyToolDefinition(record, isOwnedAgent) {
  return {
    name: REPLY_TOOL_NAME,
    description:
      'Answer one pending out-of-band progress inquiry from the operator of this delegated run. '
      + 'Call it only when a message carrying a `buddy inquiry <id>` marker asks you to. Pass that exact '
      + 'inquiryId and your answer. It records the answer for the operator and never changes, restarts, or '
      + 'ends your task.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['inquiryId', 'answer'],
      properties: {
        inquiryId: { type: 'string', description: 'The exact inquiry id from the `buddy inquiry <id>` message.' },
        answer: { type: 'string', description: 'The answer for the operator, as plain text.' },
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        required: ['recorded', 'inquiryId'],
        properties: {
          recorded: { type: 'boolean' },
          inquiryId: { type: 'string' },
        },
      },
      render: (_args, value) => [{
        type: 'text',
        text: isObject(value) && value.recorded === true
          ? `recorded the answer for inquiry ${value.inquiryId}`
          : 'the answer was not recorded',
      }],
    },
    async execute(args, exec) {
      if (!isObject(args) || typeof args.inquiryId !== 'string' || typeof args.answer !== 'string') {
        throw new Error('buddy_inquiry_reply requires string inquiryId and answer');
      }
      if (!isOwnedAgent(exec?.agent)) {
        throw new Error('buddy_inquiry_reply is only available to the delegated run that owns this bridge');
      }
      if (args.answer.trim() === '') throw new Error('buddy_inquiry_reply requires a nonblank answer');
      const answered = record(args.inquiryId, args.answer, {
        toolCallId: isUsableString(exec?.callId) ? String(exec.callId) : null,
        at: Date.now(),
      });
      if (answered === undefined) throw new Error('unknown inquiry id: no inquiry with that id was ever asked in this run');
      if (answered === 'refused') {
        throw new Error('buddy_inquiry_reply: that inquiry has no committed question in this session yet, so it cannot be answered');
      }
      return { recorded: answered === 'recorded', inquiryId: args.inquiryId };
    },
  };
}

/**
 * Start the bridge for tests and for `apply`.
 *
 * @param ctx - Cordis context carrying the live agent registry and tool runtime.
 * @param config - `{ socketPath, token, promptSha256, cwd, resultsPath?, errorPath? }`.
 * @returns `{ close, state }` where `close()` is idempotent.
 */
export async function startInquiryBridge(ctx, config) {
  const socketPath = config?.socketPath;
  const token = config?.token;
  const promptSha256 = config?.promptSha256;
  const cwd = config?.cwd;
  const errorPath = config?.errorPath;
  const resultsPath = config?.resultsPath;
  if (!isUsableString(socketPath) || !isAbsolute(socketPath)) {
    throw new Error('inquiry bridge requires an absolute config.socketPath');
  }
  if (!isUsableString(token)) throw new Error('inquiry bridge requires a non-empty config.token');
  if (typeof promptSha256 !== 'string' || !/^[0-9a-f]{64}$/.test(promptSha256)) {
    throw new Error('inquiry bridge requires config.promptSha256 as a lowercase hex SHA-256');
  }
  if (!isUsableString(cwd)) throw new Error('inquiry bridge requires config.cwd');
  if (Buffer.byteLength(socketPath) > UNIX_SOCKET_PATH_BUDGET) {
    throw new Error(`inquiry bridge socket path exceeds this platform's Unix socket limit (${UNIX_SOCKET_PATH_BUDGET} bytes)`);
  }
  if (typeof process.getuid !== 'function') throw new Error('inquiry bridge requires a POSIX host');

  const state = {
    closing: false,
    identity: null,
    bindPath: null,
    connections: new Set(),
    idle: new Set(),
    inFlight: new Set(),
    closePromise: undefined,
    // Identity
    sessionId: undefined,
    matchedSessionIds: new Set(),
    boundAt: null,
    ambiguous: false,
    // Bounded live observation
    lastEvent: null,
    activity: [],
    toolCalls: new Map(),
    // Inquiry state
    inquiries: new Map(),
    replyToolDisposer: null,
    globalReplyToolDisposer: null,
    replyToolScope: null,
    ensureScopedReplyTool: null,
    // Bookkeeping
    startedAt: new Date().toISOString(),
    droppedActivity: 0,
    refused: 0,
    journal: { path: typeof resultsPath === 'string' && resultsPath !== '' ? resultsPath : null, bytes: 0, truncated: false },
  };

  const privateParent = () => {
    const parent = dirname(socketPath);
    mkdirSync(parent, { recursive: true, mode: 0o700 });
    const info = lstatSync(parent);
    if (info.isSymbolicLink() || !info.isDirectory()) throw new Error('inquiry bridge socket parent must be a real directory');
    if (info.uid !== process.getuid()) throw new Error('inquiry bridge socket parent must be owned by the current user');
    if ((info.mode & 0o077) !== 0) throw new Error('inquiry bridge socket parent must be owner-private (0700)');
  };
  privateParent();

  assertSocketPathAvailable(socketPath);

  const server = createServer((socket) => acceptConnection(socket, state, { token, handleRequest: buildHandlers(state, ctx) }));
  const privateBindPath = join(dirname(socketPath), `.iq${randomBytes(5).toString('hex')}`);
  const privateBind = Buffer.byteLength(privateBindPath) <= UNIX_SOCKET_PATH_BUDGET;
  const bindPath = privateBind ? privateBindPath : socketPath;
  state.bindPath = privateBind ? bindPath : null;
  await listen(server, bindPath);
  server.on('error', () => { /* post-listen transport errors are cleanup's concern */ });
  try {
    chmodSync(bindPath, 0o600);
    state.identity = readSocketIdentity(bindPath);
    if (privateBind) publishSocket(bindPath, socketPath);
  } catch (error) {
    await shutdown(server, socketPath, state);
    throw new Error(`inquiry bridge could not secure its socket (${error?.code ?? 'unknown error'})`, { cause: error });
  }

  // Identity observation and the answer channel are installed only after the
  // socket is live: a bridge that cannot be reached must not change the run.
  installIdentityObserver(ctx, state, { promptSha256, cwd });
  installInboxObserver(ctx, state);
  installReplyTool(ctx, state);

  return {
    state,
    close() {
      state.closePromise ??= (async () => {
        // No pending inquiry can be claimed or answered after this point; record
        // that honestly in the journal so the service never reports a permanent
        // `queued`/`claimed` question for a run that has ended.
        finalizeInquiries(state);
        disposeReplyTool(state);
        await shutdown(server, socketPath, state);
      })();
      return state.closePromise;
    },
  };
}

/** Observe the session feed and bind this run's exact root session. */
function installIdentityObserver(ctx, state, { promptSha256, cwd }) {
  const remember = (event) => {
    if (state.closing) return;
    if (!isObject(event) || typeof event.type !== 'string') return;
    const entry = {
      seq: typeof event.seq === 'number' ? event.seq : null,
      type: event.type,
      at: typeof event.time === 'number' ? event.time : Date.now(),
    };
    state.lastEvent = entry;
    if (event.type === 'tool/call') {
      const id = isObject(event.data) ? String(event.data.callId) : undefined;
      const name = isObject(event.data) && isUsableString(event.data.name) ? event.data.name : 'unknown';
      const preview = isObject(event.data) ? argumentPreview(event.data.arguments) : undefined;
      if (id !== undefined) state.toolCalls.set(id, { name, at: entry.at });
      pushActivity(state, { phase: 'started', tool: name, callId: id ?? null, at: entry.at, seq: entry.seq, ...(preview === undefined ? {} : { argumentPreview: preview }) });
      return;
    }
    if (event.type === 'tool/result') {
      const id = isObject(event.data?.message) && isUsableString(event.data.message.source?.callId)
        ? String(event.data.message.source.callId)
        : undefined;
      const known = id === undefined ? undefined : state.toolCalls.get(id);
      if (id !== undefined) state.toolCalls.delete(id);
      pushActivity(state, {
        phase: 'finished',
        tool: known?.name ?? 'unknown',
        callId: id ?? null,
        at: entry.at,
        seq: entry.seq,
        durationMs: known === undefined ? null : Math.max(0, entry.at - known.at),
        isError: isObject(event.data) && event.data.message?.content?.[0]?.isError === true,
      });
      return;
    }
    if (event.type === 'turn/end' || event.type === 'step/end') {
      pushActivity(state, { phase: event.type, at: entry.at, seq: entry.seq });
    }
  };
  try {
    ctx.on('session/event', (session, event) => {
      try {
        if (state.ambiguous) return;
        if (!isRootSession(session)) return;
        if (session.header.cwd !== cwd) return;
        const sessionId = String(session.id);
        if (event?.type === 'user/message' && event.data?.source?.kind === 'user') {
          const text = messageText(event.data);
          if (text !== undefined && sha256(text) === promptSha256 && !state.matchedSessionIds.has(sessionId)) {
            state.matchedSessionIds.add(sessionId);
            if (state.matchedSessionIds.size > 1) {
              // Two different root sessions carry this run's exact first prompt:
              // refuse an ambiguous binding instead of guessing one.
              state.ambiguous = true;
              state.sessionId = undefined;
              return;
            }
            state.sessionId = sessionId;
            state.boundAt = new Date().toISOString();
          }
        }
        if (state.sessionId === undefined || sessionId !== state.sessionId) return;
        if (event.type === 'user/message') deliveryFromDurableEvent(state, event);
        remember(event);
      } catch { /* an observer must never affect the delegated run */ }
    });
  } catch { /* no session feed: the bridge stays reachable but reports not-ready */ }
}

/**
 * Follow the injected message through the agent's own inbox, so the service can
 * distinguish "accepted and waiting" from "claimed by the loop at a step
 * boundary" (`agent/inbox/claimed`) and "dropped before it ran"
 * (`agent/inbox/discarded`). Both live notifications are emitted by the loop
 * implementation, verified against the installed dsh sources.
 */
/**
 * Follow the injected message through the agent's own inbox. `inserted` means
 * the message is durably pending; `claimed` means the loop removed it for a
 * proposed step. The installed loop claims BEFORE the `agent/pre-step`
 * waterfall runs, so a rejected pre-step drops a claimed batch without ever
 * committing it to the model-visible surface. Delivery is therefore recorded
 * only from the exact owned session's durable `user/message` append (see
 * `deliveryFromDurableEvent`), never from the claim itself.
 */
function installInboxObserver(ctx, state) {
  const apply = (kind) => (payload) => {
    try {
      const id = payload?.message?.id;
      if (!isUsableString(id)) return;
      const inquiry = inquiryByMessageId(state, String(id));
      if (inquiry === undefined) return;
      const now = new Date().toISOString();
      if (kind === 'inserted') {
        inquiry.insertedAt = now;
        return;
      }
      if (inquiry.state === 'answered' || inquiry.state === 'discarded' || inquiry.state === 'unavailable') return;
      if (kind === 'claimed') {
        // A claim is a proposal, not a commit: keep it a distinct truthful
        // state, and never downgrade a question dsh already committed.
        if (inquiry.state !== 'queued') return;
        inquiry.state = 'claimed';
        inquiry.claimedAt = now;
        inquiry.claimedTurn = typeof payload?.turn === 'number' ? payload.turn : null;
        journal(state, {
          inquiryId: inquiry.inquiryId,
          state: 'claimed',
          claimedAt: now,
          turn: inquiry.claimedTurn,
          messageId: inquiry.messageId ?? null,
        });
        return;
      }
      inquiry.state = 'discarded';
      inquiry.discardedAt = now;
      journal(state, { inquiryId: inquiry.inquiryId, state: 'discarded', discardedAt: now, messageId: inquiry.messageId ?? null });
    } catch { /* delivery bookkeeping must never affect the run */ }
  };
  for (const [kind, event] of [['inserted', 'agent/inbox/inserted'], ['claimed', 'agent/inbox/claimed'], ['discarded', 'agent/inbox/discarded']]) {
    try {
      ctx.on(event, apply(kind));
    } catch { /* no inbox feed: the bridge reports `queued` and never claims delivery */ }
  }
}

/** Find the inquiry that owns one injected message id. */
function inquiryByMessageId(state, messageId) {
  for (const inquiry of state.inquiries.values()) {
    if (inquiry.messageId === messageId) return inquiry;
  }
  return undefined;
}

/**
 * Mark delivery from the owned session's durable `user/message` event. dsh
 * appends that event in `step()` for exactly the admitted batch, after the
 * pre-step decision and before the model call, so its presence is proof that
 * the question became model-visible input for this run's own session.
 */
function deliveryFromDurableEvent(state, event) {
  try {
    const data = event?.data;
    if (!isObject(data)) return;
    const id = isUsableString(data.id) ? String(data.id) : undefined;
    if (id === undefined) return;
    const inquiry = inquiryByMessageId(state, id);
    if (inquiry === undefined) return;
    const source = isObject(data.source) ? data.source : undefined;
    if (source !== undefined && source.inquiryId !== undefined && source.inquiryId !== inquiry.inquiryId) return;
    if (inquiry.state === 'delivered' || inquiry.state === 'answered' || inquiry.state === 'discarded' || inquiry.state === 'unavailable') return;
    const at = typeof event.time === 'number' ? new Date(event.time).toISOString() : new Date().toISOString();
    inquiry.state = 'delivered';
    inquiry.deliveredAt = at;
    journal(state, {
      inquiryId: inquiry.inquiryId,
      state: 'delivered',
      deliveredAt: at,
      claimedAt: inquiry.claimedAt ?? null,
      messageId: inquiry.messageId ?? null,
    });
  } catch { /* an observer must never affect the delegated run */ }
}

/**
 * Terminalize every inquiry that can no longer be answered. Called only while
 * the bridge is closing, so the answer channel is already gone.
 */
function finalizeInquiries(state) {
  const at = new Date().toISOString();
  for (const inquiry of state.inquiries.values()) {
    if (inquiry.state === 'answered' || inquiry.state === 'discarded' || inquiry.state === 'unavailable') continue;
    inquiry.state = 'unavailable';
    inquiry.reason = 'the run ended before a correlated answer was recorded';
    inquiry.finalizedAt = at;
    journal(state, {
      inquiryId: inquiry.inquiryId,
      state: 'unavailable',
      reason: inquiry.reason,
      finalizedAt: at,
      messageId: inquiry.messageId ?? null,
    });
  }
}

/** Insert one bounded entry into the activity ring. */
function pushActivity(state, entry) {
  state.activity.push(entry);
  if (state.activity.length > MAX_ACTIVITY_ENTRIES) {
    state.activity.splice(0, state.activity.length - MAX_ACTIVITY_ENTRIES);
    state.droppedActivity += 1;
  }
}

/**
 * Register the correlated answer channel. The tool is registered in the
 * identified agent's OWN scope when that is supported, so no other agent in the
 * process can see or call it; a global registration keeps a runtime guard that
 * rejects any caller that is not this run's agent.
 */
function installReplyTool(ctx, state) {
  const isOwnedAgent = (agent) => {
    if (agent === undefined || agent === null) return false;
    if (state.sessionId === undefined) return false;
    const session = agent.session;
    if (session === undefined || session === null) return false;
    return String(session.id) === state.sessionId;
  };
  const record = (inquiryId, answer, meta) => recordAnswer(state, inquiryId, answer, meta);
  const definition = replyToolDefinition(record, isOwnedAgent);

  const tryRegister = (target, scope) => {
    const tools = target?.tools;
    if (tools === undefined || tools === null || typeof tools.register !== 'function') return false;
    try {
      const disposer = tools.register(definition);
      const release = typeof disposer === 'function' ? disposer : null;
      if (scope === 'global') {
        state.globalReplyToolDisposer = release;
        if (state.replyToolScope !== 'agent') state.replyToolDisposer = release;
      } else {
        state.replyToolDisposer = release;
        state.replyToolScope = 'agent';
      }
      return true;
    } catch {
      return false;
    }
  };

  // Global registration first: it is always available and the runtime guard
  // above keeps it inert for every other agent. A scoped registration is
  // preferred and replaces it as soon as this run's exact agent is identified.
  tryRegister(ctx, 'global');
  state.ensureScopedReplyTool = (agent) => {
    if (state.replyToolScope === 'agent') return;
    if (!isOwnedAgent(agent)) return;
    if (!tryRegister(agent.ctx, 'agent')) return;
    try { state.globalReplyToolDisposer?.(); } catch { /* best effort */ }
    state.globalReplyToolDisposer = null;
  };
  try {
    ctx.on('agent/created', ({ agent }) => {
      try { state.ensureScopedReplyTool?.(agent); } catch { /* best effort */ }
    });
  } catch { /* no agent event feed; the global registration remains */ }
}

/** Undo the reply-tool registration, scoped or global. */
function disposeReplyTool(state) {
  try { state.replyToolDisposer?.(); } catch { /* best effort */ }
  try { state.globalReplyToolDisposer?.(); } catch { /* best effort */ }
  state.replyToolDisposer = null;
  state.globalReplyToolDisposer = null;
}

/**
 * Append one bounded lifecycle line to this run's private inquiry journal, so a
 * correlated answer stays readable after the headless process exits. The owning
 * service only ever reads this file; it is never a dsh storage file.
 */
function journal(state, entry) {
  const path = state.journal.path;
  if (path === null || state.journal.truncated) return;
  try {
    const line = `${JSON.stringify({ version: 1, ...entry })}\n`;
    const bytes = Buffer.byteLength(line);
    if (state.journal.bytes + bytes > MAX_JOURNAL_BYTES) {
      state.journal.truncated = true;
      return;
    }
    appendFileSync(path, line, { mode: 0o600 });
    state.journal.bytes += bytes;
  } catch { /* the journal is best effort; the live answer is unaffected */ }
}

/**
 * Record one correlated answer.
 *
 * Only a question dsh already committed to this run's own session can be
 * answered, and only with nonblank text; the answer is truncated to the shared
 * UTF-8 byte budget on a code-point boundary.
 *
 * @returns `'recorded'`; `'closed'` for a known inquiry that already has an
 *   answer or was discarded; `'refused'` when the inquiry has no committed
 *   question yet or the answer is blank; `undefined` for an unknown id.
 */
function recordAnswer(state, inquiryId, answer, meta) {
  const inquiry = state.inquiries.get(inquiryId);
  if (inquiry === undefined) return undefined;
  if (typeof answer !== 'string' || answer.trim() === '') return 'refused';
  if (inquiry.state === 'discarded') return 'closed';
  if (inquiry.answer !== undefined) return 'closed'; // first correlated answer wins
  if (inquiry.state !== 'delivered') return 'refused';
  const cut = truncateUtf8(answer, MAX_ANSWER_BYTES);
  inquiry.answer = cut.text;
  inquiry.answerBytes = cut.bytes;
  inquiry.answerTruncated = cut.truncated;
  inquiry.answeredAt = new Date(meta.at).toISOString();
  inquiry.answerToolCallId = meta.toolCallId;
  inquiry.answerVia = `tool:${REPLY_TOOL_NAME}`;
  inquiry.state = 'answered';
  journal(state, {
    inquiryId,
    state: 'answered',
    answeredAt: inquiry.answeredAt,
    via: inquiry.answerVia,
    toolCallId: inquiry.answerToolCallId,
    messageId: inquiry.messageId ?? null,
    answer: cut.text,
    answerBytes: cut.bytes,
    truncated: cut.truncated,
  });
  return 'recorded';
}

/**
 * Resolve the host's agent registry. `ctx.get(name)` is the documented service
 * lookup and works regardless of how the plugin was mounted; `ctx.agents` is
 * the injected property and is used as a fallback for hosts that only expose
 * the property. Both are probed defensively: a missing registry must degrade to
 * an honest `unavailable` observation, never throw into the run.
 */
function agentRegistry(ctx) {
  try {
    const viaGet = typeof ctx?.get === 'function' ? ctx.get('agents') : undefined;
    if (viaGet !== undefined && viaGet !== null) return viaGet;
  } catch { /* fall through to the injected property */ }
  try {
    return ctx?.agents ?? undefined;
  } catch {
    return undefined;
  }
}

/** Build the request handlers bound to this bridge's state and host context. */
function buildHandlers(state, ctx) {
  const liveAgent = () => {
    if (state.sessionId === undefined) return undefined;
    try {
      const registry = agentRegistry(ctx);
      if (registry === undefined || typeof registry.get !== 'function') return undefined;
      return registry.get(state.sessionId) ?? undefined;
    } catch {
      return undefined;
    }
  };
  const observation = () => boundObservation(state, liveAgent());
  const handlers = {
    ping: () => success({ ready: true, version: PROTOCOL_VERSION, bound: state.sessionId !== undefined }),
    observe: () => success({ ready: state.sessionId !== undefined, ...observation() }),
    ask: (request) => {
      const inquiryId = request.inquiryId;
      const question = request.question;
      if (!isUsableString(inquiryId) || !isUsableString(question)) return failure(ERROR_CODES.BAD_REQUEST);
      if (question.trim() === '') return failure(ERROR_CODES.BAD_REQUEST);
      if (utf8Bytes(question) > MAX_QUESTION_BYTES) return failure(ERROR_CODES.BAD_REQUEST);
      const known = state.inquiries.get(inquiryId);
      if (known !== undefined) {
        // A repeat never injects again: an already recorded inquiry stays
        // readable (and answerable) even when the agent is no longer running.
        if (known.questionSha256 !== sha256(question)) return failure(ERROR_CODES.CONFLICT);
        return success({ ...receipt(state, known), duplicate: true, observation: observation() });
      }
      if (state.inquiries.size >= MAX_INQUIRIES) return failure(ERROR_CODES.TOO_MANY);
      if (state.sessionId === undefined) {
        return success({ accepted: false, state: 'unavailable', reason: ERROR_CODES.NOT_READY, observation: observation() });
      }
      const agent = liveAgent();
      if (agent === undefined) {
        return success({ accepted: false, state: 'unavailable', reason: ERROR_CODES.AGENT_GONE, observation: observation() });
      }
      // `agent.steer` is public input: on an idle agent it WAKES a new turn. A
      // late question arriving while headless is finishing (`whenIdle`, result
      // flush, exit) must never reopen a run that is already over, so only a
      // step that is actually in flight may receive one.
      if (agent.status !== 'running') {
        return success({
          accepted: false,
          state: 'unavailable',
          reason: ERROR_CODES.AGENT_NOT_RUNNING,
          agentStatus: typeof agent.status === 'string' ? agent.status : null,
          observation: observation(),
        });
      }
      // Make the correlated answer channel visible to this exact agent before
      // the boundary that will claim the question.
      try { state.ensureScopedReplyTool?.(agent); } catch { /* the global fallback remains */ }
      const inquiry = {
        inquiryId,
        questionSha256: sha256(question),
        questionBytes: utf8Bytes(question),
        submittedAt: new Date().toISOString(),
        state: 'queued',
        messageId: randomUUID(),
      };
      // Publish the inquiry BEFORE steering: `steer` commits the inbox splice
      // synchronously and the loop emits `agent/inbox/inserted` inside it.
      state.inquiries.set(inquiryId, inquiry);
      try {
        agent.steer({
          id: inquiry.messageId,
          role: 'user',
          content: [{ type: 'text', text: inquiryText(inquiryId, question) }],
          source: {
            kind: 'plugin',
            plugin: 'deepseek-delegate-inquiry-bridge',
            form: 'notice',
            summary: `buddy inquiry ${inquiryId}`.slice(0, 120),
            // Producer-declared correlation for readers of the durable log.
            inquiryId,
          },
        });
      } catch {
        state.inquiries.delete(inquiryId);
        return success({ accepted: false, state: 'unavailable', reason: ERROR_CODES.AGENT_GONE, observation: observation() });
      }
      inquiry.injectedAt = new Date().toISOString();
      inquiry.agentStatusAtInject = typeof agent.status === 'string' ? agent.status : null;
      journal(state, {
        inquiryId,
        state: 'queued',
        submittedAt: inquiry.submittedAt,
        injectedAt: inquiry.injectedAt,
        agentStatusAtInject: inquiry.agentStatusAtInject,
        messageId: inquiry.messageId,
        questionSha256: inquiry.questionSha256,
        questionBytes: inquiry.questionBytes,
      });
      return success({ ...receipt(state, inquiry), duplicate: false, observation: observation() });
    },
    answer: (request) => {
      const inquiry = state.inquiries.get(request.inquiryId);
      if (inquiry === undefined) return failure(ERROR_CODES.NOT_READY);
      return success({ ...receipt(state, inquiry), observation: observation() });
    },
  };
  return handlers;
}

/** One bounded progress view, with unavailable fields named explicitly. */
function boundObservation(state, agent) {
  const unavailable = [];
  let agentStatus = null;
  if (agent !== undefined && typeof agent.status === 'string') agentStatus = agent.status;
  else unavailable.push('agentStatus');
  let inbox = null;
  if (agent !== undefined && isObject(agent.inbox)) {
    try {
      inbox = {
        nextTurn: Array.isArray(agent.inbox.nextTurn) ? agent.inbox.nextTurn.length : null,
        nextStep: Array.isArray(agent.inbox.nextStep) ? agent.inbox.nextStep.length : null,
      };
    } catch { inbox = null; }
  }
  if (inbox === null) unavailable.push('inbox');
  if (state.activity.length === 0) unavailable.push('activity');
  if (state.lastEvent === null) unavailable.push('lastEvent');
  return {
    sessionId: state.sessionId ?? null,
    ambiguous: state.ambiguous === true,
    boundAt: state.boundAt ?? null,
    bridgeStartedAt: state.startedAt,
    observedAt: new Date().toISOString(),
    agentStatus,
    inbox,
    lastEvent: state.lastEvent,
    activity: state.activity.slice(-MAX_ACTIVITY_ENTRIES),
    activityDropped: state.droppedActivity,
    replyTool: { name: REPLY_TOOL_NAME, scope: state.replyToolScope },
    journal: { enabled: state.journal.path !== null, truncated: state.journal.truncated },
    unavailable,
    limits: {
      maxActivityEntries: MAX_ACTIVITY_ENTRIES,
      maxArgumentPreviewChars: MAX_ARGUMENT_PREVIEW_CHARS,
      maxQuestionBytes: MAX_QUESTION_BYTES,
      maxAnswerBytes: MAX_ANSWER_BYTES,
      exposesModelReasoning: false,
    },
  };
}

/** Bounded receipt for one inquiry, correlated by inquiryId. */
function receipt(state, inquiry) {
  return {
    accepted: true,
    inquiryId: inquiry.inquiryId,
    state: inquiry.state,
    submittedAt: inquiry.submittedAt ?? null,
    injectedAt: inquiry.injectedAt ?? null,
    insertedAt: inquiry.insertedAt ?? null,
    claimedAt: inquiry.claimedAt ?? null,
    claimedTurn: inquiry.claimedTurn ?? null,
    deliveredAt: inquiry.deliveredAt ?? null,
    discardedAt: inquiry.discardedAt ?? null,
    answeredAt: inquiry.answeredAt ?? null,
    finalizedAt: inquiry.finalizedAt ?? null,
    reason: inquiry.reason ?? null,
    messageId: inquiry.messageId ?? null,
    agentStatusAtInject: inquiry.agentStatusAtInject ?? null,
    answer: inquiry.answer === undefined ? null : {
      text: inquiry.answer,
      bytes: inquiry.answerBytes ?? utf8Bytes(inquiry.answer),
      truncated: inquiry.answerTruncated === true,
      via: inquiry.answerVia,
      toolCallId: inquiry.answerToolCallId ?? null,
      at: inquiry.answeredAt,
    },
    correlation: 'inquiryId',
    bridgeBound: state.sessionId !== undefined,
  };
}

// ---------------------------------------------------------------------------
// Socket lifecycle: private parent, atomic publish, own-inode-only cleanup.
// ---------------------------------------------------------------------------

function assertSocketPathAvailable(socketPath) {
  let info;
  try {
    info = lstatSync(socketPath);
  } catch (error) {
    if (error?.code === 'ENOENT') return;
    throw new Error(`inquiry bridge cannot inspect its socket path (${error?.code ?? 'unknown error'})`);
  }
  throw occupiedSocketError(socketPath, info);
}

function occupiedSocketError(socketPath, info) {
  const kind = info?.isSocket() === true ? 'socket' : 'file';
  return new Error(`inquiry bridge refuses to replace the existing ${kind} at '${socketPath}'`);
}

function publishSocket(bindPath, socketPath) {
  try {
    linkSync(bindPath, socketPath);
  } catch (error) {
    if (error?.code === 'EEXIST') throw occupiedSocketError(socketPath);
    if (!HARDLINK_FALLBACK_CODES.has(error?.code)) {
      throw new Error(`inquiry bridge could not publish its socket (${error?.code ?? 'unknown error'})`, { cause: error });
    }
    assertSocketPathAvailable(socketPath);
    renameSync(bindPath, socketPath);
    return;
  }
  unlinkSync(bindPath);
}

function listen(server, socketPath) {
  return new Promise((resolve, reject) => {
    const onError = (error) => {
      server.off('listening', onListening);
      reject(Object.assign(new Error(`inquiry bridge could not listen (${error?.code ?? 'unknown error'})`, { cause: error }), { code: error?.code }));
    };
    const onListening = () => {
      server.off('error', onError);
      resolve();
    };
    server.once('error', onError);
    server.once('listening', onListening);
    server.listen({ path: socketPath, exclusive: true });
  });
}

function readSocketIdentity(socketPath) {
  const info = lstatSync(socketPath);
  if (!info.isSocket()) throw new Error('inquiry bridge expected a socket');
  return { dev: info.dev, ino: info.ino };
}

function removeOwnSocket(socketPath, identity) {
  if (identity === null) return false;
  let info;
  try {
    info = lstatSync(socketPath);
  } catch {
    return false;
  }
  if (!info.isSocket() || info.dev !== identity.dev || info.ino !== identity.ino) return false;
  try {
    unlinkSync(socketPath);
    return true;
  } catch {
    return false;
  }
}

function settleWithin(promise, timeoutMs) {
  let timer;
  return Promise.race([
    promise,
    new Promise((resolve) => { timer = setTimeout(resolve, timeoutMs); }),
  ]).finally(() => clearTimeout(timer));
}

/** Accept one connection: one bounded frame, one response, then end. */
function acceptConnection(socket, state, services) {
  if (state.closing) {
    socket.destroy();
    return;
  }
  state.connections.add(socket);
  state.idle.add(socket);
  let buffer = Buffer.alloc(0);
  let dispatched = false;

  socket.setTimeout(CONNECTION_TIMEOUT_MS);
  socket.on('close', () => {
    state.connections.delete(socket);
    state.idle.delete(socket);
  });
  socket.on('error', () => socket.destroy());
  socket.on('timeout', () => {
    if (dispatched) {
      socket.destroy();
      return;
    }
    dispatched = true;
    state.idle.delete(socket);
    void sendError(socket, '', ERROR_CODES.TIMEOUT);
  });
  socket.on('data', (chunk) => {
    if (dispatched || state.closing) return;
    buffer = buffer.length === 0 ? chunk : Buffer.concat([buffer, chunk]);
    const newline = buffer.indexOf(0x0a);
    if (newline < 0) {
      if (buffer.length > MAX_FRAME_BYTES) {
        dispatched = true;
        state.idle.delete(socket);
        void sendError(socket, '', ERROR_CODES.FRAME_TOO_LARGE);
      }
      return;
    }
    dispatched = true;
    state.idle.delete(socket);
    socket.setTimeout(0);
    if (newline > MAX_FRAME_BYTES) {
      void sendError(socket, '', ERROR_CODES.FRAME_TOO_LARGE);
      return;
    }
    const task = dispatchFrame(socket, buffer.subarray(0, newline), services);
    state.inFlight.add(task);
    const settled = () => state.inFlight.delete(task);
    task.then(settled, settled);
  });
}

/** Parse and serve exactly one frame; never rejects and never echoes exceptions. */
async function dispatchFrame(socket, frame, services) {
  let id = '';
  try {
    let request;
    try {
      request = JSON.parse(frame.toString('utf8'));
    } catch {
      await sendError(socket, '', ERROR_CODES.BAD_REQUEST);
      return;
    }
    if (isObject(request) && typeof request.id === 'string') id = request.id;
    let outcome;
    try {
      outcome = await handleRequest(request, services);
    } catch {
      outcome = failure(ERROR_CODES.INTERNAL);
    }
    if (outcome.ok) await send(socket, { version: PROTOCOL_VERSION, id, ok: true, value: outcome.value });
    else await sendError(socket, id, outcome.error);
  } catch {
    await sendError(socket, id, ERROR_CODES.INTERNAL);
  }
}

/** Validate the envelope, then dispatch one method. */
async function handleRequest(request, services) {
  if (!isObject(request)) return failure(ERROR_CODES.BAD_REQUEST);
  if (request.version !== PROTOCOL_VERSION) return failure(ERROR_CODES.BAD_REQUEST);
  if (typeof request.id !== 'string') return failure(ERROR_CODES.BAD_REQUEST);
  if (typeof request.token !== 'string' || request.token !== services.token) return failure(ERROR_CODES.UNAUTHORIZED);
  if (typeof request.method !== 'string') return failure(ERROR_CODES.BAD_REQUEST);
  const handler = services.handleRequest[request.method];
  if (handler === undefined) return failure(ERROR_CODES.UNSUPPORTED_METHOD);
  return handler(request);
}

function success(value) {
  return { ok: true, value };
}

function failure(error) {
  return { ok: false, error };
}

/** Write one newline-terminated response, bounded; never rejects. */
function send(socket, message) {
  return new Promise((resolve) => {
    let settled = false;
    const settle = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
    try {
      const line = `${JSON.stringify(message)}\n`;
      if (Buffer.byteLength(line) > MAX_RESPONSE_BYTES) {
        socket.destroy();
        settle();
        return;
      }
      if (socket.destroyed || socket.writableEnded) {
        settle();
        return;
      }
      socket.once('close', settle);
      socket.end(line, settle);
    } catch {
      socket.destroy();
      settle();
    }
  });
}

function sendError(socket, id, error) {
  const code = ERROR_CODE_SET.has(error) ? error : ERROR_CODES.INTERNAL;
  return send(socket, { version: PROTOCOL_VERSION, id, ok: false, error: code });
}

/** Stop accepting, drain briefly, destroy the rest, remove only our own inode. */
async function shutdown(server, socketPath, state) {
  state.closing = true;
  try {
    const accepted = new Promise((resolve) => {
      try {
        server.close(() => resolve());
      } catch {
        resolve();
      }
    });
    for (const socket of [...state.idle]) socket.destroy();
    await settleWithin(Promise.allSettled([...state.inFlight]), SHUTDOWN_GRACE_MS);
    for (const socket of [...state.connections]) socket.destroy();
    await settleWithin(accepted, SHUTDOWN_GRACE_MS);
  } catch {
    // Disposal is best-effort and must never throw into the host lifecycle.
  }
  removeOwnSocket(socketPath, state.identity);
  if (state.bindPath !== null) removeOwnSocket(state.bindPath, state.identity);
}

/**
 * Mount the bridge and tear it down with the plugin's fiber. `apply` never
 * throws into the host: a bridge that cannot start records the failure next to
 * its socket path so the owning service can report the exact reason, and the
 * delegated run continues without inquiry.
 *
 * @param ctx - Cordis context.
 * @param config - `{ socketPath, token, promptSha256, cwd, errorPath? }`.
 */
export async function apply(ctx, config) {
  let started;
  try {
    started = await startInquiryBridge(ctx, config);
  } catch (error) {
    try {
      if (isUsableString(config?.errorPath)) {
        writeFileSync(config.errorPath, `${JSON.stringify({
          version: 1,
          ok: false,
          error: typeof error?.code === 'string' ? error.code : 'start-failed',
          message: String(error?.message ?? 'inquiry bridge failed to start').slice(0, 500),
          at: new Date().toISOString(),
        })}\n`, { mode: 0o600, flag: 'w' });
      }
    } catch { /* diagnostics are best effort */ }
    return;
  }
  let hooked = false;
  try {
    ctx.on('dispose', async () => { await started.close(); });
    hooked = true;
  } catch { /* the fiber was disposed during startup */ }
  if (typeof ctx.effect === 'function') {
    try {
      ctx.effect(() => () => started.close(), 'deepseek-delegate.inquiry-bridge');
      hooked = true;
    } catch { /* the fiber was disposed during startup */ }
  }
  if (!hooked) await started.close();
}
