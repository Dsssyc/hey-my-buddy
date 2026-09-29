/**
 * deepseek-delegate attempt usage observer.
 *
 * A tiny, self-contained Cordis plugin that the delegation CLI mounts into a
 * headless dsh run through a temporary `--patch` overlay. It projects the live
 * root-session event feed into an attempt-private sidecar that records the
 * native usage this attempt actually reported, the last root assistant text and
 * a machine failure classification.
 *
 * Only native observations are written. A token field is summed only when every
 * contributing usage record carried it, `cachedInputTokens` is derived only
 * when the records make it provable, and a missing field is never treated as
 * zero. Prompts, tool arguments, tool output, credentials and hidden reasoning
 * never reach the file; usage is an observation, never an estimate.
 *
 * The plugin binds the root session exactly like the session-capture and
 * activity observers: root lineage, canonical cwd and the SHA-256 of this run's
 * first user message. Until that binding exists nothing is written.
 *
 * @module harnesses/dsh/plugins/usage
 */
import { createHash } from 'node:crypto';
import { closeSync, fsyncSync, openSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { TextDecoder } from 'node:util';

/** Stable Cordis plugin name (the patch entry id mirrors it). */
export const name = 'deepseek-delegate-usage';

/** Sidecar document version written to disk. */
const USAGE_VERSION = 1;

/** Bounds copied from the frozen contract; a longer value is truncated, never dropped silently. */
const MAX_SESSION_ID = 256;
const MAX_FAILURE_CODE = 100;

/** The retained root assistant text is capped at this many UTF-8 bytes from the head. */
const MAX_ASSISTANT_TEXT_BYTES = 65536;

/** An unchanged projection is coalesced into this window; a material change always writes. */
const DEFAULT_MIN_INTERVAL_MS = 2000;

/** The only native usage fields this plugin understands; anything else is ignored. */
const USAGE_FIELDS = ['inputTokens', 'outputTokens', 'totalTokens', 'cacheReadTokens', 'cacheWriteTokens', 'reasoningTokens'];

/** Lowercase hex SHA-256 of one UTF-8 string. */
function sha256(text) {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

/** Join the text blocks of a message the way the CLI hashes the delivered prompt. */
function messageText(message) {
  if (message === null || typeof message !== 'object') return undefined;
  const content = message.content;
  if (!Array.isArray(content)) return undefined;
  let text = '';
  for (const block of content) {
    if (block === null || typeof block !== 'object' || block.type !== 'text') continue;
    if (typeof block.text !== 'string') return undefined;
    text += block.text;
  }
  return text;
}

/**
 * Join an assistant message's own text blocks in order. Reasoning and tool-call
 * blocks are never text, and tool output never appears in an assistant message;
 * a malformed block is skipped rather than guessed at.
 */
function assistantText(message) {
  if (message === null || typeof message !== 'object') return undefined;
  const content = message.content;
  if (!Array.isArray(content)) return undefined;
  let text = '';
  for (const block of content) {
    if (block === null || typeof block !== 'object' || block.type !== 'text') continue;
    if (typeof block.text !== 'string') continue;
    text += block.text;
  }
  return text;
}

/**
 * Keep at most MAX_ASSISTANT_TEXT_BYTES complete UTF-8 characters from the
 * head. The streaming decoder holds back a trailing partial sequence, so the
 * retained text can never end in a lone surrogate or a replacement character.
 */
function boundText(text) {
  const bytes = Buffer.from(text, 'utf8');
  if (bytes.length <= MAX_ASSISTANT_TEXT_BYTES) return { text, truncated: false };
  const head = bytes.subarray(0, MAX_ASSISTANT_TEXT_BYTES);
  return { text: new TextDecoder('utf-8').decode(head, { stream: true }), truncated: true };
}

/** Root lineage: no fork parent, no subagent origin, no positive delegation depth. */
function isRootSession(session) {
  const header = session?.header;
  if (header === null || typeof header !== 'object') return false;
  if (header.parentSession !== undefined && header.parentSession !== null) return false;
  if (header.origin === 'subagent') return false;
  if (typeof header.delegationDepth === 'number' && header.delegationDepth > 0) return false;
  return true;
}

/** Atomically replace the sidecar: temp file, fsync, rename, fsync parent. */
function writeSidecar(path, document) {
  const temporary = join(dirname(path), `.usage.${process.pid}.tmp`);
  let descriptor;
  try {
    descriptor = openSync(temporary, 'wx', 0o600);
    writeFileSync(descriptor, `${JSON.stringify(document, null, 1)}\n`);
    fsyncSync(descriptor);
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
  }
  try {
    renameSync(temporary, path);
    const parent = openSync(dirname(path), 'r');
    try { fsyncSync(parent); } finally { closeSync(parent); }
  } catch (error) {
    try { unlinkSync(temporary); } catch { /* best effort */ }
    throw error;
  }
}

/**
 * Mount the observer. Every config field is validated up front: a malformed
 * patch disables observation instead of half-writing a sidecar.
 *
 * @param ctx - Cordis context carrying the session event feed.
 * @param config - `{ usagePath, promptSha256, cwd, taskId, attemptId, generation, minIntervalMs? }`.
 */
export function apply(ctx, config) {
  const usagePath = config?.usagePath;
  const promptSha256 = config?.promptSha256;
  const cwd = config?.cwd;
  const taskId = config?.taskId;
  const attemptId = config?.attemptId;
  const generation = config?.generation;
  if (typeof usagePath !== 'string' || usagePath === '') return;
  if (typeof taskId !== 'string' || taskId === '' || taskId.length > 256) return;
  if (typeof attemptId !== 'string' || attemptId === '' || attemptId.length > 256) return;
  if (!Number.isSafeInteger(generation) || generation < 1) return;
  if (typeof promptSha256 !== 'string' || !/^[0-9a-f]{64}$/.test(promptSha256)) return;
  if (typeof cwd !== 'string' || cwd === '') return;
  const minIntervalMs = Number.isFinite(config?.minIntervalMs) && config.minIntervalMs >= 0
    ? Math.min(config.minIntervalMs, 60000)
    : DEFAULT_MIN_INTERVAL_MS;

  let sessionId;
  const sums = {
    inputTokens: 0, outputTokens: 0, totalTokens: 0,
    cacheReadTokens: 0, cacheWriteTokens: 0, reasoningTokens: 0,
  };
  const missed = new Set();
  let records = 0;
  let missingUsage = false;
  let turnEndSeen = false;
  let turnEndCompleted = false;
  let failure;
  let lastAssistant;
  let lastContent;
  let lastWriteAt = 0;

  const bounded = (value, maximum) => value.slice(0, maximum);
  const now = () => new Date().toISOString();
  const completeField = (field) => records > 0 && !missed.has(field);

  /**
   * Accept a usage field only as a non-negative safe integer. A contributing
   * record that did not carry a field marks that field incomplete for the whole
   * attempt, so it is omitted instead of being read as zero.
   */
  const accumulate = (usage) => {
    let usable = false;
    for (const field of USAGE_FIELDS) {
      const value = usage[field];
      if (Number.isSafeInteger(value) && value >= 0) {
        sums[field] += value;
        usable = true;
      } else {
        missed.add(field);
      }
    }
    return usable;
  };

  /**
   * Cached input is emitted only when the native records prove it: first
   * `totalTokens - inputTokens - outputTokens` when non-negative, then the
   * cache read/write fields together or alone. An unprovable value is omitted.
   */
  const cachedInputTokens = () => {
    if (completeField('totalTokens') && completeField('inputTokens') && completeField('outputTokens')) {
      const derived = sums.totalTokens - sums.inputTokens - sums.outputTokens;
      if (derived >= 0) return derived;
    }
    if (completeField('cacheReadTokens') && completeField('cacheWriteTokens')) {
      return sums.cacheReadTokens + sums.cacheWriteTokens;
    }
    if (completeField('cacheReadTokens')) return sums.cacheReadTokens;
    if (completeField('cacheWriteTokens')) return sums.cacheWriteTokens;
    return undefined;
  };

  const projection = () => {
    const tokenUsage = {
      source: 'dsh/session-assistant-usage',
      inputBasis: 'excludes-cached',
    };
    if (completeField('inputTokens')) tokenUsage.inputTokens = sums.inputTokens;
    if (completeField('outputTokens')) tokenUsage.outputTokens = sums.outputTokens;
    const cached = cachedInputTokens();
    if (cached !== undefined) tokenUsage.cachedInputTokens = cached;
    if (completeField('reasoningTokens')) tokenUsage.reasoningOutputTokens = sums.reasoningTokens;
    if (completeField('totalTokens')) tokenUsage.totalTokens = sums.totalTokens;
    tokenUsage.records = records;
    tokenUsage.completeness = !missingUsage && turnEndSeen && turnEndCompleted ? 'complete' : 'partial';
    const nativeUsage = {
      source: 'dsh/session-assistant-usage',
      sessionId,
      tokenUsage,
    };
    if (lastAssistant !== undefined) nativeUsage.lastAssistantMessage = lastAssistant;
    if (failure !== undefined) nativeUsage.failure = failure;
    return nativeUsage;
  };

  const publish = (at, force) => {
    const nativeUsage = projection();
    const content = JSON.stringify(nativeUsage);
    if (!force && content === lastContent && at - lastWriteAt < minIntervalMs) return;
    writeSidecar(usagePath, {
      version: USAGE_VERSION,
      taskId,
      attemptId,
      generation,
      updatedAt: now(),
      nativeUsage,
    });
    lastContent = content;
    lastWriteAt = at;
  };

  ctx.on('session/event', (session, event) => {
    try {
      if (!isRootSession(session)) return;
      if (session.header.cwd !== cwd) return;
      const current = String(session.id);
      if (sessionId === undefined) {
        if (event?.type !== 'user/message') return;
        if (event.data?.source?.kind !== 'user') return;
        const text = messageText(event.data);
        if (text === undefined || sha256(text) !== promptSha256) return;
        sessionId = bounded(current, MAX_SESSION_ID);
        return;
      }
      if (current !== sessionId) return;
      if (event?.type === 'assistant/message') {
        const usage = event.data?.usage;
        let usable = false;
        if (usage !== null && typeof usage === 'object' && !Array.isArray(usage)) usable = accumulate(usage);
        if (usable) records += 1;
        else missingUsage = true;
        const message = event.data?.message;
        if (message?.source?.kind === 'model') {
          const text = assistantText(message);
          if (text !== undefined && text.trim() !== '') {
            const retained = boundText(text);
            const entry = { text: retained.text };
            if (typeof message.id === 'string' && message.id !== '') entry.sourceId = message.id;
            entry.sourceBytes = Buffer.byteLength(text, 'utf8');
            entry.sha256 = sha256(text);
            entry.truncated = retained.truncated;
            lastAssistant = entry;
          }
        }
        publish(Date.now(), false);
        return;
      }
      if (event.type === 'turn/end') {
        // The last turn/end is this attempt's terminal reason; only a completed
        // one makes the observation complete.
        const reason = event.data?.reason;
        const kind = typeof reason?.kind === 'string' ? reason.kind : undefined;
        turnEndSeen = true;
        turnEndCompleted = kind === 'completed';
        failure = undefined;
        if (kind === 'error') {
          // Keep only the machine classification; a provider message can carry
          // credentials and is never copied into the sidecar.
          failure = {};
          const code = reason?.error?.code;
          if (typeof code === 'string' && code !== '' && code.length <= MAX_FAILURE_CODE) failure.code = code;
          failure.kind = 'error';
        }
        publish(Date.now(), true);
      }
    } catch {
      // An observer must never affect the delegated run. A failed write simply
      // leaves the last valid sidecar, which the Worker forwards unchanged.
    }
  });
}
