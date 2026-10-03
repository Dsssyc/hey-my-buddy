/**
 * deepseek-delegate bounded activity observer.
 *
 * A tiny, self-contained Cordis plugin that the delegation CLI mounts into a
 * headless dsh run through a temporary `--patch` overlay. It projects the live
 * root-session event feed into the frozen `hey_my_buddy.protocol.activity` sidecar
 * (`activity.json`) that the owning Buddy Worker forwards through
 * `worker_progress`.
 *
 * Only the frozen, metadata-only fields are written: phase, ISO timestamps, a
 * monotone event sequence, the bound native session id, the last tool name and
 * non-negative counts. Prompts, tool arguments, tool output, credentials and
 * hidden reasoning never reach the file.
 *
 * The plugin binds the root session through root lineage, canonical cwd and
 * the SHA-256 of this run's first user message.
 * Until that binding exists nothing is written; the sidecar is an observation,
 * never a guess or a fabricated heartbeat.
 *
 * @module harnesses/dsh/plugins/activity
 */
import { createHash } from 'node:crypto';
import { closeSync, fsyncSync, openSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

/** Stable Cordis plugin name (the patch entry id mirrors it). */
export const name = 'deepseek-delegate-activity';

/** Must equal `hey_my_buddy.protocol.activity.ACTIVITY_VERSION`. */
const ACTIVITY_VERSION = 1;

/** Bounds copied from the frozen contract; a longer value is truncated, never dropped silently. */
const MAX_SESSION_ID = 256;
const MAX_TOOL_NAME = 64;

/** A same-phase update is coalesced into this window; a phase change always writes. */
const DEFAULT_MIN_INTERVAL_MS = 2000;

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
  const temporary = join(dirname(path), `.activity.${process.pid}.tmp`);
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
 * patch disables capture instead of half-writing a sidecar.
 *
 * @param ctx - Cordis context carrying the session event feed.
 * @param config - `{ activityPath, taskId, attemptId, generation, promptSha256, cwd }`.
 */
export function apply(ctx, config) {
  const activityPath = config?.activityPath;
  const taskId = config?.taskId;
  const attemptId = config?.attemptId;
  const generation = config?.generation;
  const promptSha256 = config?.promptSha256;
  const cwd = config?.cwd;
  if (typeof activityPath !== 'string' || activityPath === '') return;
  if (typeof taskId !== 'string' || taskId === '' || taskId.length > 256) return;
  if (typeof attemptId !== 'string' || attemptId === '' || attemptId.length > 256) return;
  if (!Number.isSafeInteger(generation) || generation < 1) return;
  if (typeof promptSha256 !== 'string' || !/^[0-9a-f]{64}$/.test(promptSha256)) return;
  if (typeof cwd !== 'string' || cwd === '') return;
  const minIntervalMs = Number.isFinite(config?.minIntervalMs) && config.minIntervalMs >= 0
    ? Math.min(config.minIntervalMs, 60000)
    : DEFAULT_MIN_INTERVAL_MS;

  let sessionId;
  let eventSeq = 0;
  let lastPhase;
  let lastWriteAt = 0;
  let lastNativeActivityAt;
  let lastToolActivityAt;
  let toolName;
  let modelTurns = 0;
  let toolCalls = 0;

  const bounded = (value, maximum) => value.slice(0, maximum);
  const now = () => new Date().toISOString();

  const publish = (phase, at) => {
    const changed = phase !== lastPhase;
    if (!changed && at - lastWriteAt < minIntervalMs) return;
    const activity = { phase, observedAt: now(), eventSeq, nativeSessionId: sessionId, lastNativeActivityAt };
    if (lastToolActivityAt !== undefined) activity.lastToolActivityAt = lastToolActivityAt;
    if (toolName !== undefined) activity.toolName = toolName;
    activity.counts = { modelTurns, toolCalls };
    writeSidecar(activityPath, {
      version: ACTIVITY_VERSION,
      taskId,
      attemptId,
      generation,
      updatedAt: now(),
      activity,
    });
    lastPhase = phase;
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
        lastNativeActivityAt = now();
        modelTurns += 1;
        publish('streaming-model', Date.now());
        return;
      }
      if (current !== sessionId) return;
      if (!Number.isSafeInteger(event?.seq) || event.seq < 0) {
        eventSeq += 1;
      } else {
        eventSeq = Math.max(eventSeq + 1, event.seq);
      }
      lastNativeActivityAt = now();
      if (event.type === 'tool/call') {
        const name = event.data && typeof event.data.name === 'string' ? event.data.name : undefined;
        if (name !== undefined && name !== '') toolName = bounded(name, MAX_TOOL_NAME);
        toolCalls += 1;
        lastToolActivityAt = now();
        publish('tool-running', Date.now());
        return;
      }
      if (event.type === 'tool/result') {
        lastToolActivityAt = now();
        publish('streaming-model', Date.now());
        return;
      }
      if (event.type === 'turn/end') {
        // Only the turn itself finishes the projection. `step/end` is an
        // intermediate model step inside the same turn, so it must never be
        // reported as finishing.
        publish('finishing', Date.now());
      }
    } catch {
      // An observer must never affect the delegated run. A failed write simply
      // leaves the last valid sidecar, which the Worker forwards unchanged.
    }
  });
}
