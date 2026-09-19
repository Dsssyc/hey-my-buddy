/**
 * `inquire`: bounded, read-only progress observation plus correlated questions
 * for one owned run.
 *
 * Design boundaries (see references/plugin-service.md):
 * - This module owns no dsh process, no model call, and no runner deadline. It
 *   reads the durable run record through `JobManager.dispatch` (serialized,
 *   in-memory, no I/O) and talks to one run's PRIVATE bridge socket outside that
 *   serialization, so a pending `await` is never blocked by inquiry transport.
 * - A question is durably recorded before anything is injected, is idempotent by
 *   `inquiryId`, and is only ever delivered through the bridge's public
 *   `agent.steer` path into the SAME live agent. Nothing here can cancel,
 *   restart, re-scope, or extend the run.
 * - An answer is reported only when the bridge's scoped reply tool recorded it
 *   against the exact `inquiryId`; natural-language output is never treated as
 *   an answer.
 */
import { connect } from 'node:net';
import { readFileSync, statSync } from 'node:fs';
import { randomUUID } from 'node:crypto';

/** Bounds of the bounded socket request; never touches the run's own deadline. */
export const DEFAULT_TRANSPORT_TIMEOUT_MS = 1500;
export const MIN_TRANSPORT_TIMEOUT_MS = 100;
export const MAX_TRANSPORT_TIMEOUT_MS = 5000;
/** Optional bounded wait for an answer to arrive; transport wait only. */
export const DEFAULT_WAIT_MS = 0;
export const MAX_WAIT_MS = 30000;
const POLL_INTERVAL_MS = 300;
/** Largest accepted bridge response. */
const MAX_RESPONSE_BYTES = 64 * 1024;
/** Largest inquiry journal this service will read. */
const MAX_JOURNAL_BYTES = 1024 * 1024;
const ACTIVE = new Set(['running', 'cancelling', 'completing']);
const PROTOCOL_VERSION = 1;
const MAX_ACTIVITY_ENTRIES = 20;
/** Shared with the in-run bridge: one UTF-8 byte budget for a recorded answer. */
const MAX_ANSWER_BYTES = 4000;

const error = (code, message) => Object.assign(new Error(message), { code });

/** Plain JSON object, excluding arrays and null. */
function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Read one OWN entry of a JSON table. `view.inquiries` is ordinary JSON, so a
 * plain lookup would resolve inherited members for valid ids such as
 * `constructor` or `toString`, and the prototype accessor for `__proto__`.
 */
function ownEntry(table, key) {
  if (table === null || typeof table !== 'object') return undefined;
  return Object.hasOwn(table, key) ? table[key] : undefined;
}

/** Byte-bounded copy of one journal answer, always cut on a code-point edge. */
function truncateUtf8(text, maxBytes) {
  const buffer = Buffer.from(text, 'utf8');
  if (buffer.length <= maxBytes) return { text, bytes: buffer.length, truncated: false };
  let end = maxBytes;
  while (end > 0 && (buffer[end] & 0xc0) === 0x80) end -= 1;
  return { text: buffer.subarray(0, end).toString('utf8'), bytes: end, truncated: true };
}

/** Validate one `inquire` request before any service state is touched. */
export function validateInquireParams(params) {
  if (!isObject(params)) throw error('INVALID_ARGUMENT', 'inquire requires an object of parameters');
  const allowed = new Set(['runId', 'inquiryId', 'question', 'timeoutMs', 'waitMs']);
  const unknown = Object.keys(params).filter((key) => !allowed.has(key));
  if (unknown.length > 0) throw error('INVALID_ARGUMENT', `Unknown inquire parameter: ${unknown.sort()[0]}`);
  if (typeof params.runId !== 'string' || params.runId.trim() === '') throw error('INVALID_ARGUMENT', 'runId must be a nonempty string');
  const hasQuestion = params.question !== undefined;
  const hasInquiryId = params.inquiryId !== undefined;
  if (hasQuestion !== hasInquiryId) throw error('INVALID_ARGUMENT', 'question and inquiryId must be supplied together');
  if (hasQuestion && (typeof params.question !== 'string' || params.question.trim() === '')) {
    throw error('INVALID_ARGUMENT', 'question must be a nonempty string');
  }
  if (hasInquiryId && (typeof params.inquiryId !== 'string' || !/^[A-Za-z0-9._:-]{1,128}$/.test(params.inquiryId))) {
    throw error('INVALID_ARGUMENT', 'inquiryId must match [A-Za-z0-9._:-]{1,128}');
  }
  const timeoutMs = params.timeoutMs ?? DEFAULT_TRANSPORT_TIMEOUT_MS;
  if (!Number.isInteger(timeoutMs) || timeoutMs < MIN_TRANSPORT_TIMEOUT_MS || timeoutMs > MAX_TRANSPORT_TIMEOUT_MS) {
    throw error('INVALID_ARGUMENT', `timeoutMs must be an integer between ${MIN_TRANSPORT_TIMEOUT_MS} and ${MAX_TRANSPORT_TIMEOUT_MS}`);
  }
  const waitMs = params.waitMs ?? DEFAULT_WAIT_MS;
  if (!Number.isInteger(waitMs) || waitMs < 0 || waitMs > MAX_WAIT_MS) {
    throw error('INVALID_ARGUMENT', `waitMs must be an integer between 0 and ${MAX_WAIT_MS}`);
  }
  return { runId: params.runId, inquiryId: params.inquiryId, question: params.question, timeoutMs, waitMs };
}

/**
 * The runner deadline as far as the durable record can prove it. The runner
 * starts its own timeout timer only after preflight and after dsh is spawned,
 * which is strictly later than the record's `createdAt`, so this value is an
 * ESTIMATE with an explicit clock origin, never an exact spawned-process
 * deadline. A terminal run's elapsed time is frozen at its last durable record
 * update instead of accumulating while someone keeps asking.
 */
export function deadlineOf(view, now = Date.now()) {
  const createdMs = typeof view.createdAt === 'string' ? Date.parse(view.createdAt) : Number.NaN;
  const timeoutSeconds = Number.isInteger(view.timeoutSeconds) ? view.timeoutSeconds : null;
  const terminal = !ACTIVE.has(view.status);
  const updatedMs = terminal && typeof view.updatedAt === 'string' ? Date.parse(view.updatedAt) : Number.NaN;
  const measuredMs = terminal && Number.isFinite(updatedMs) ? updatedMs : now;
  const base = {
    estimated: true,
    exact: false,
    kind: 'estimated-runner-deadline-from-record-createdAt',
    clockOrigin: 'run record createdAt (before runner preflight and before dsh spawn)',
    deadlineBasis: 'createdAt + timeoutSeconds',
    exactTimingAvailable: false,
    timeoutSeconds,
    terminal,
  };
  if (!Number.isFinite(createdMs) || timeoutSeconds === null) {
    return {
      ...base,
      available: false,
      reason: 'this run record carries no recorded runner deadline (created before the field existed)',
      startedAt: null,
      deadlineAt: null,
      elapsedSeconds: null,
      remainingSeconds: null,
      expired: null,
      measuredTo: null,
      note: 'No deadline can be derived from this record; inquiry never extends, shortens, or restarts a run.',
    };
  }
  const deadlineMs = createdMs + timeoutSeconds * 1000;
  return {
    ...base,
    available: true,
    startedAt: new Date(createdMs).toISOString(),
    deadlineAt: new Date(deadlineMs).toISOString(),
    elapsedSeconds: Math.round(((measuredMs - createdMs) / 1000) * 10) / 10,
    remainingSeconds: Math.round(((deadlineMs - measuredMs) / 1000) * 10) / 10,
    expired: measuredMs >= deadlineMs,
    measuredTo: new Date(measuredMs).toISOString(),
    note: terminal
      ? 'Estimated deadline (record.createdAt + timeoutSeconds); the runner starts its own timer only after preflight and dsh spawn, so the real deadline is later. This run is terminal, so elapsed time is frozen at its last durable record update rather than accumulating to now.'
      : 'Estimated deadline (record.createdAt + timeoutSeconds); the runner starts its own timer only after preflight and dsh spawn, so the real deadline is later than deadlineAt (exact spawn timing is not published). Inquiry never extends, shortens, or restarts it.',
  };
}

/**
 * Read one run's private inquiry journal (appended by the bridge inside the
 * run). Returns the newest entry per inquiryId; a missing or oversized journal
 * is reported as absent rather than guessed at.
 */
export function readJournal(resultsPath) {
  if (typeof resultsPath !== 'string' || resultsPath === '') return { available: false, reason: 'no-journal-path', entries: new Map() };
  let size;
  try {
    size = statSync(resultsPath).size;
  } catch {
    return { available: false, reason: 'journal-not-written', entries: new Map() };
  }
  if (size > MAX_JOURNAL_BYTES) return { available: false, reason: 'journal-exceeds-limit', entries: new Map() };
  let text;
  try {
    text = readFileSync(resultsPath, 'utf8');
  } catch {
    return { available: false, reason: 'journal-unreadable', entries: new Map() };
  }
  const entries = new Map();
  for (const line of text.split('\n')) {
    if (line.trim() === '') continue;
    let record;
    try {
      record = JSON.parse(line);
    } catch {
      continue; // a torn trailing line is ignored, never fatal
    }
    if (!isObject(record) || typeof record.inquiryId !== 'string') continue;
    entries.set(record.inquiryId, record);
  }
  return { available: true, reason: null, entries };
}

/** One request/response frame against a run's private bridge socket. */
export function bridgeRequest(credentials, method, payload = {}, { timeoutMs = DEFAULT_TRANSPORT_TIMEOUT_MS } = {}) {
  return new Promise((resolve) => {
    const id = randomUUID();
    let socket;
    try {
      socket = connect(credentials.socketPath);
    } catch {
      resolve({ ok: false, reason: 'bridge-unreachable' });
      return;
    }
    let buffer = '';
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try { socket.destroy(); } catch { /* already closed */ }
      resolve(value);
    };
    const timer = setTimeout(() => finish({ ok: false, reason: 'bridge-timeout' }), timeoutMs);
    socket.setEncoding('utf8');
    socket.on('connect', () => {
      try {
        socket.write(`${JSON.stringify({ version: PROTOCOL_VERSION, id, token: credentials.token, method, ...payload })}\n`);
      } catch {
        finish({ ok: false, reason: 'bridge-write-failed' });
      }
    });
    socket.on('error', (cause) => finish({ ok: false, reason: cause?.code === 'ENOENT' || cause?.code === 'ECONNREFUSED' ? 'bridge-unreachable' : 'bridge-error' }));
    socket.on('end', () => finish({ ok: false, reason: 'bridge-closed' }));
    socket.on('data', (chunk) => {
      buffer += chunk;
      if (Buffer.byteLength(buffer) > MAX_RESPONSE_BYTES) return finish({ ok: false, reason: 'bridge-response-too-large' });
      const end = buffer.indexOf('\n');
      if (end < 0) return;
      let message;
      try {
        message = JSON.parse(buffer.slice(0, end));
      } catch {
        return finish({ ok: false, reason: 'bridge-invalid-response' });
      }
      if (!isObject(message) || message.version !== PROTOCOL_VERSION || message.id !== id || typeof message.ok !== 'boolean') {
        return finish({ ok: false, reason: 'bridge-mismatched-response' });
      }
      if (!message.ok) return finish({ ok: false, reason: 'bridge-refused', code: typeof message.error === 'string' ? message.error : 'internal' });
      finish({ ok: true, value: message.value });
    });
  });
}

/** The recorded bridge startup failure, when the bridge could not mount itself. */
function readBridgeFailure(errorPath) {
  try {
    const record = JSON.parse(readFileSync(errorPath, 'utf8'));
    if (!isObject(record)) return null;
    return { code: typeof record.error === 'string' ? record.error.slice(0, 80) : 'start-failed', message: typeof record.message === 'string' ? record.message.slice(0, 300) : null };
  } catch {
    return null;
  }
}

/** Bounded observation block derived from one bridge response. */
function observationOf(value) {
  if (!isObject(value)) return null;
  const activity = Array.isArray(value.activity) ? value.activity.slice(-MAX_ACTIVITY_ENTRIES) : [];
  return {
    // `ready` is the bridge's own statement that it bound this run's exact root
    // session; an answering bridge with no binding still reports no live view.
    available: value.ready === true,
    observedAt: typeof value.observedAt === 'string' ? value.observedAt : null,
    sessionId: typeof value.sessionId === 'string' ? value.sessionId : null,
    ambiguous: value.ambiguous === true,
    boundAt: typeof value.boundAt === 'string' ? value.boundAt : null,
    bridgeStartedAt: typeof value.bridgeStartedAt === 'string' ? value.bridgeStartedAt : null,
    agentStatus: typeof value.agentStatus === 'string' ? value.agentStatus : null,
    inbox: isObject(value.inbox) ? { nextTurn: value.inbox.nextTurn ?? null, nextStep: value.inbox.nextStep ?? null } : null,
    lastEvent: isObject(value.lastEvent)
      ? { seq: value.lastEvent.seq ?? null, type: String(value.lastEvent.type ?? ''), at: value.lastEvent.at ?? null }
      : null,
    activity: activity.map((entry) => (isObject(entry) ? {
      phase: String(entry.phase ?? ''),
      tool: entry.tool === undefined ? undefined : String(entry.tool),
      callId: entry.callId ?? null,
      at: entry.at ?? null,
      seq: entry.seq ?? null,
      durationMs: entry.durationMs ?? undefined,
      isError: entry.isError === undefined ? undefined : entry.isError === true,
      argumentPreview: entry.argumentPreview === undefined ? undefined : String(entry.argumentPreview).slice(0, 200),
    } : null)).filter(Boolean),
    activityDropped: Number.isInteger(value.activityDropped) ? value.activityDropped : 0,
    replyTool: isObject(value.replyTool) ? { name: String(value.replyTool.name ?? ''), scope: value.replyTool.scope ?? null } : null,
    journal: isObject(value.journal) ? { enabled: value.journal.enabled === true, truncated: value.journal.truncated === true } : null,
    limits: isObject(value.limits) ? {
      maxActivityEntries: value.limits.maxActivityEntries ?? null,
      maxArgumentPreviewChars: value.limits.maxArgumentPreviewChars ?? null,
      maxQuestionBytes: value.limits.maxQuestionBytes ?? null,
      maxAnswerBytes: value.limits.maxAnswerBytes ?? null,
      exposesModelReasoning: value.limits.exposesModelReasoning === true,
    } : null,
    unavailable: Array.isArray(value.unavailable) ? value.unavailable.map(String).slice(0, 12) : [],
  };
}

/** The progress view with every field that could not be observed named explicitly. */
function unavailableObservation(reason, extra = {}) {
  return {
    available: false,
    reason,
    observedAt: new Date().toISOString(),
    sessionId: null,
    agentStatus: null,
    inbox: null,
    lastEvent: null,
    activity: [],
    activityDropped: 0,
    replyTool: null,
    unavailable: ['sessionId', 'agentStatus', 'inbox', 'lastEvent', 'activity', 'replyTool'],
    ...extra,
  };
}

/**
 * One `inquire` call. Never throws for a run/bridge problem that can be reported
 * honestly in the envelope; it throws only for malformed input or an unknown run.
 */
export async function inquireRun(manager, params, options = {}) {
  const request = validateInquireParams(params);
  const now = options.now ?? Date.now();
  const clock = options.clock ?? (() => Date.now());
  const sleep = options.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const view = await manager.dispatch('status', { runId: request.runId });
  const active = ACTIVE.has(view.status);
  const credentials = active ? manager.bridgeCredentials(request.runId) : null;
  const deadline = deadlineOf(view, now);

  let inquiry = null;
  let duplicate = false;
  let recorded = false;
  if (request.question !== undefined) {
    if (active) {
      const begun = await manager.dispatch('inquiry-begin', {
        runId: request.runId,
        inquiryId: request.inquiryId,
        question: request.question,
      });
      inquiry = begun.inquiry;
      duplicate = begun.duplicate;
      recorded = true;
    } else {
      // A terminal run cannot receive a new question. An inquiry this run
      // already carries is reported from its durable record (and journal)
      // instead of being re-created or silently forgotten. The idempotency
      // check is the same one an active run uses, against the exact recorded
      // UTF-8 hash: identical text reads the recorded inquiry (including a
      // recovered answer) back, while different text is the same CONFLICT. It
      // is only consulted for an id this run already owns, so a terminal run
      // can never create a new question.
      const known = ownEntry(view.inquiries, request.inquiryId);
      if (known !== undefined) {
        const begun = await manager.dispatch('inquiry-begin', {
          runId: request.runId,
          inquiryId: request.inquiryId,
          question: request.question,
        });
        inquiry = begun.inquiry;
        recorded = true;
        duplicate = begun.duplicate;
      } else {
        inquiry = {
          inquiryId: request.inquiryId,
          state: 'unavailable',
          reason: `the run is ${view.status}: no live agent exists to receive a question`,
          submittedAt: null,
          questionBytes: Buffer.byteLength(request.question, 'utf8'),
          questionPreview: request.question.slice(0, 280),
          answer: null,
          delivery: null,
        };
      }
    }
  }

  let bridge = {
    enabled: credentials !== null,
    observed: false,
    reason: active ? 'bridge-unavailable' : `run-${view.status}`,
    error: null,
  };
  let observation = unavailableObservation(bridge.reason);
  let journal = { available: false, reason: 'not-read', entries: new Map() };

  if (credentials !== null && request.question !== undefined && inquiry.state !== 'answered') {
    // Deliver (or re-check) the question through the bridge. The service never
    // re-injects: the bridge dedups by inquiryId and returns the existing receipt.
    const response = await bridgeRequest(credentials, 'ask', { inquiryId: request.inquiryId, question: request.question }, { timeoutMs: request.timeoutMs });
    if (response.ok) {
      bridge = { enabled: true, observed: true, reason: null, error: null };
      observation = observationOf(response.value.observation) ?? observation;
      inquiry = await applyBridgeReceipt(manager, request.runId, inquiry, response.value);
    } else {
      bridge.error = response.code ?? null;
      bridge.reason = response.reason;
      const failure = readBridgeFailure(credentials.errorPath);
      if (failure !== null) {
        bridge.reason = 'bridge-start-failed';
        bridge.error = failure.code;
        bridge.errorMessage = failure.message;
      }
    }
    journal = readJournal(credentials.resultsPath);
    inquiry = await mergeJournal(manager, request.runId, inquiry, journal);
  } else if (inquiry !== null) {
    // Terminal or bridgeless run: recover whatever this run already recorded,
    // including an answer the bridge journaled before the run ended.
    journal = readJournal(view.inquiryBridge?.resultsPath);
    inquiry = await mergeJournal(manager, request.runId, inquiry, journal);
    bridge.reason = inquiry.answer === null || inquiry.answer === undefined ? bridge.reason : 'run-terminal-answer-from-journal';
  } else if (credentials !== null) {    const response = await bridgeRequest(credentials, 'observe', {}, { timeoutMs: request.timeoutMs });
    if (response.ok) {
      bridge = { enabled: true, observed: true, reason: null, error: null };
      observation = observationOf(response.value) ?? observation;
    } else {
      bridge.reason = response.reason;
      bridge.error = response.code ?? null;
      const failure = readBridgeFailure(credentials.errorPath);
      if (failure !== null) {
        bridge.reason = 'bridge-start-failed';
        bridge.error = failure.code;
        bridge.errorMessage = failure.message;
      }
    }
    if (request.inquiryId !== undefined) {
      journal = readJournal(credentials.resultsPath);
      const known = ownEntry(view.inquiries, request.inquiryId);
      if (known !== undefined) inquiry = await mergeJournal(manager, request.runId, known, journal);
    }
  }

  // Optional bounded wait for an answer that is produced at the next boundary.
  if (request.waitMs > 0 && inquiry !== null && recorded && inquiry.state !== 'answered' && credentials !== null) {
    const until = clock() + request.waitMs;
    while (clock() < until) {
      await sleep(POLL_INTERVAL_MS);
      const response = await bridgeRequest(credentials, 'answer', { inquiryId: request.inquiryId }, { timeoutMs: request.timeoutMs });
      if (!response.ok) {
        bridge.reason = response.reason;
        bridge.observed = false;
        break;
      }
      bridge = { enabled: true, observed: true, reason: null, error: null };
      observation = observationOf(response.value.observation) ?? observation;
      inquiry = await applyBridgeReceipt(manager, request.runId, inquiry, response.value);
      if (inquiry.state === 'answered') break;
    }
    journal = readJournal(credentials.resultsPath);
    inquiry = await mergeJournal(manager, request.runId, inquiry, journal);
  }

  // A question that no longer has a live agent can never be claimed, delivered,
  // or answered. Persist that honestly instead of leaving a permanent `queued`.
  if (!active && inquiry !== null && recorded) {
    const settled = await settleUnanswered(manager, request.runId, inquiry, view.status);
    if (settled !== null) inquiry = settled;
  }

  // Re-read the durable record so `status`/`revision` reflect any concurrent change.
  const latest = await manager.dispatch('status', { runId: request.runId });
  return composeEnvelope({ view: latest, deadline: deadlineOf(latest, now), bridge, observation, inquiry, journal, duplicate, recorded });
}

/**
 * Terminalize one still-unanswered inquiry of a run that has ended, best effort.
 * The envelope also reports the effective state, so a persistence failure can
 * never turn into a misleading `queued` answer status.
 */
async function settleUnanswered(manager, runId, inquiry, runStatus) {
  if (inquiry.answer !== null && inquiry.answer !== undefined) return inquiry;
  if (!['queued', 'claimed', 'delivered'].includes(inquiry.state)) return inquiry;
  const patch = {
    state: 'unavailable',
    reason: `the run is ${runStatus}: the question ended without a correlated answer`,
  };
  try {
    const updated = await manager.dispatch('inquiry-update', { runId, inquiryId: inquiry.inquiryId, patch });
    return updated.inquiry;
  } catch {
    return { ...inquiry, ...patch };
  }
}

/** Persist one bridge receipt (delivery/answer) into the durable record. */
async function applyBridgeReceipt(manager, runId, inquiry, value) {
  if (!isObject(value)) return inquiry;
  const patch = {};
  const state = typeof value.state === 'string' ? value.state : undefined;
  if (state !== undefined && ['queued', 'claimed', 'delivered', 'answered', 'discarded', 'unavailable'].includes(state)) patch.state = state;
  if (value.accepted === false) {
    patch.state = 'unavailable';
    patch.reason = typeof value.reason === 'string' ? `bridge refused delivery: ${value.reason}` : 'bridge refused delivery';
  }
  patch.delivery = {
    messageId: typeof value.messageId === 'string' ? value.messageId : undefined,
    agentStatusAtInject: typeof value.agentStatusAtInject === 'string' ? value.agentStatusAtInject : undefined,
    injectedAt: typeof value.injectedAt === 'string' ? value.injectedAt : undefined,
    insertedAt: typeof value.insertedAt === 'string' ? value.insertedAt : undefined,
    claimedAt: typeof value.claimedAt === 'string' ? value.claimedAt : undefined,
    deliveredAt: typeof value.deliveredAt === 'string' ? value.deliveredAt : undefined,
    observedAt: new Date().toISOString(),
    observedAgentStatus: typeof value.observation?.agentStatus === 'string' ? value.observation.agentStatus : undefined,
  };
  if (isObject(value.answer) && typeof value.answer.text === 'string') {
    patch.answer = {
      text: value.answer.text,
      via: value.answer.via ?? null,
      toolCallId: value.answer.toolCallId ?? null,
      at: value.answer.at ?? null,
      truncated: value.answer.truncated === true,
      bytes: Number.isInteger(value.answer.bytes) ? value.answer.bytes : undefined,
      source: 'live-bridge',
    };
    patch.state = 'answered';
  }
  const updated = await manager.dispatch('inquiry-update', { runId, inquiryId: inquiry.inquiryId, patch });
  return updated.inquiry;
}

/** Merge the run's private journal, so an answer stays readable after the run ended. */
async function mergeJournal(manager, runId, inquiry, journal) {
  if (inquiry === null || inquiry === undefined || !journal.available) return inquiry;
  const entry = journal.entries.get(inquiry.inquiryId);
  if (entry === undefined) return inquiry;
  const patch = {};
  if (inquiry.answer === null || inquiry.answer === undefined) {
    if (entry.state === 'answered' && typeof entry.answer === 'string') {
      // The journal is this run's own file, but it is still bounded here: an
      // oversized line must never be rejected by the record (or worse, half
      // applied) after the bridge already reported the answer.
      const cut = truncateUtf8(entry.answer, MAX_ANSWER_BYTES);
      patch.answer = {
        text: cut.text,
        bytes: cut.bytes,
        via: entry.via ?? null,
        toolCallId: entry.toolCallId ?? null,
        at: entry.answeredAt ?? null,
        truncated: entry.truncated === true || cut.truncated,
        source: 'bridge-journal',
      };
      patch.state = 'answered';
    } else if (entry.state === 'discarded') {
      patch.state = 'discarded';
      patch.reason = 'the live agent discarded the injected question before its next step boundary';
    } else if (entry.state === 'delivered' && (inquiry.state === 'queued' || inquiry.state === 'claimed')) {
      // Journaled only from the durable `user/message` commit of this exact
      // message id, never from the inbox claim.
      patch.state = 'delivered';
      patch.delivery = { messageId: entry.messageId ?? undefined, claimedAt: entry.claimedAt ?? undefined, deliveredAt: entry.deliveredAt ?? undefined };
    } else if (entry.state === 'claimed' && inquiry.state === 'queued') {
      patch.state = 'claimed';
      patch.delivery = { messageId: entry.messageId ?? undefined, claimedAt: entry.claimedAt ?? undefined };
    } else if (entry.state === 'unavailable' && ['queued', 'claimed', 'delivered'].includes(inquiry.state)) {
      patch.state = 'unavailable';
      patch.reason = typeof entry.reason === 'string' ? entry.reason.slice(0, 200) : 'the run ended before a correlated answer was recorded';
    }
  }
  if (Object.keys(patch).length === 0) return inquiry;
  const updated = await manager.dispatch('inquiry-update', { runId, inquiryId: inquiry.inquiryId, patch });
  return updated.inquiry;
}

/** Assemble the single bounded `inquire` envelope. */
function composeEnvelope({ view, deadline, bridge, observation, inquiry, journal, duplicate, recorded }) {
  const terminal = !ACTIVE.has(view.status);
  const pending = Object.values(view.inquiries ?? {}).map((entry) => ({
    inquiryId: entry.inquiryId,
    state: entry.state,
    submittedAt: entry.submittedAt ?? null,
    updatedAt: entry.updatedAt ?? null,
  }));
  const unanswered = inquiry !== null && (inquiry.answer === null || inquiry.answer === undefined);
  // A terminal run cannot claim, deliver, or answer anything any more, so its
  // still-pending inquiry is reported as unavailable even if a stale record or
  // an unwritable state directory still says `queued`/`claimed`/`delivered`.
  const effectiveState = inquiry === null
    ? null
    : (terminal && unanswered && ['queued', 'claimed', 'delivered'].includes(inquiry.state) ? 'unavailable' : inquiry.state);
  const effectiveReason = inquiry === null || effectiveState === inquiry.state
    ? inquiry?.reason ?? null
    : `the run is ${view.status}: the question ended without a correlated answer`;
  let answer = null;
  if (inquiry !== null && inquiry.answer !== null && inquiry.answer !== undefined) {
    answer = {
      available: true,
      text: inquiry.answer.text,
      bytes: inquiry.answer.bytes ?? Buffer.byteLength(inquiry.answer.text, 'utf8'),
      via: inquiry.answer.via ?? null,
      toolCallId: inquiry.answer.toolCallId ?? null,
      at: inquiry.answer.at ?? null,
      truncated: inquiry.answer.truncated === true,
      source: inquiry.answer.source ?? null,
    };
  } else if (inquiry !== null) {
    answer = {
      available: false,
      reason: effectiveState === 'answered'
        ? 'the answer was recorded without text'
        : effectiveState === 'delivered'
          ? 'the question was committed to the agent’s model-visible input; no correlated answer has been recorded yet'
          : effectiveState === 'claimed'
            ? 'the loop claimed the question for a proposed step, but dsh has not committed it to the model-visible input yet (a rejected pre-step drops it without delivery)'
            : effectiveState === 'queued'
              ? 'the question is not yet claimed at a step boundary'
              : effectiveState === 'discarded'
                ? 'the question was discarded before it reached a step boundary'
                : effectiveReason ?? 'the question was not delivered to a live agent',
    };
  }
  return {
    runId: view.runId,
    requestId: view.requestId,
    status: view.status,
    phase: terminal ? 'terminal' : 'active',
    execution: {
      status: view.status,
      revision: view.revision ?? null,
      createdAt: view.createdAt ?? null,
      updatedAt: view.updatedAt ?? null,
      resultAvailable: view.resultAvailable === true,
      shutdownConfirmed: view.shutdownConfirmed === true,
      exitCode: view.exitCode ?? null,
      cancelRequestedAt: view.cancelRequestedAt ?? null,
      logPaths: view.logPaths ?? null,
    },
    deadline,
    bridge,
    live: observation,
    inquiry: inquiry === null ? null : {
      inquiryId: inquiry.inquiryId,
      state: effectiveState,
      recordedState: inquiry.state,
      reason: effectiveReason,
      recorded,
      duplicate,
      questionBytes: inquiry.questionBytes ?? null,
      questionPreview: inquiry.questionPreview ?? null,
      submittedAt: inquiry.submittedAt ?? null,
      injectedAt: inquiry.delivery?.injectedAt ?? null,
      claimedAt: inquiry.delivery?.claimedAt ?? null,
      deliveredAt: inquiry.delivery?.deliveredAt ?? null,
      updatedAt: inquiry.updatedAt ?? null,
      answer,
      correlation: 'inquiryId',
    },
    pendingInquiries: pending,
    journal: { available: journal.available, reason: journal.reason, entries: journal.entries.size },
    limits: {
      maxQuestionBytes: 4000,
      maxAnswerBytes: MAX_ANSWER_BYTES,
      maxInquiriesPerRun: 32,
      maxWaitMs: MAX_WAIT_MS,
      maxTransportTimeoutMs: MAX_TRANSPORT_TIMEOUT_MS,
      maxActivityEntries: MAX_ACTIVITY_ENTRIES,
      exposesModelReasoning: false,
    },
    note: 'Read-only observation of the owned run plus, when a question is supplied, one correlated delivery into that same live agent. Inquiry never cancels, restarts, re-scopes, or extends the run, and a runner that exited 0 is still not acceptance: inspect the real artifacts and checks yourself.',
  };
}
