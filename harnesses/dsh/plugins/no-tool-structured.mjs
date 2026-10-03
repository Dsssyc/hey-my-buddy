/** One native DSH LLM call, with no Agent, Session, or tool registry. */
import { readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

export const name = 'buddy-no-tool-structured';
// Provider registration alone can precede credentials-local.loadInitial().
// Cordis must activate this caller only after both services are ready.
export const inject = ['llm', 'credentials'];
const MAX_ANSWER_BYTES = 65_536;
const MAX_OUTPUT_TOKENS = 4_096;
// The shared tool-evidence retention bound; facts past it set the truncated flag.
const MAX_TOOL_EVENTS = 128;
const USAGE_FIELDS = ['inputTokens', 'outputTokens', 'totalTokens', 'cacheReadTokens',
  'cacheWriteTokens', 'reasoningTokens'];

function safeUsage(native) {
  const result = { toolCalls: 0 };
  for (const key of USAGE_FIELDS) {
    if (Number.isSafeInteger(native?.[key]) && native[key] >= 0) result[key] = native[key];
  }
  return result;
}

export function agentRunnerDisabled(ctx) {
  try {
    const entries = [...ctx.get('loader').entries()];
    const runner = entries.find((entry) => entry?.options?.id === 'headless-runner');
    return runner !== undefined && (runner.disabled === true || runner.options?.disabled === true);
  } catch {
    return false;
  }
}

function boundedFact(value) {
  return typeof value === 'string' && value.length > 0 && value.length <= 512 ? value : null;
}

// Chunk shapes are the installed public StreamChunk vocabulary. Only a
// tool-call-delta and tool-call/tool-result blocks carry tool facts; the frame
// type is reported as `type` only where no call name exists, because a frame
// envelope is not the native operation. A completed request block means the
// request was sent — its phase is start, never the execution's end.
function toolFact(chunk) {
  if (chunk.type === 'tool-call-delta') {
    return { callId: chunk.id, name: chunk.name, type: 'tool-call', phase: 'start' };
  }
  if (chunk.type === 'block-start') {
    if (chunk.blockType === 'tool-call') return { callId: null, name: null, type: 'tool-call', phase: 'start' };
    return chunk.blockType === 'text' || chunk.blockType === 'reasoning' ? null : 'malformed';
  }
  if (chunk.type === 'block-end') {
    const block = chunk.block;
    if (!block || typeof block.type !== 'string') return 'malformed';
    if (block.type === 'tool-call') return { callId: block.id, name: block.name, type: 'tool-call', phase: 'start' };
    if (block.type === 'tool-result') return { callId: block.toolCallId, name: null, type: 'tool-result', phase: 'end' };
    return block.type === 'text' || block.type === 'reasoning' ? null : 'malformed';
  }
  if (chunk.type === 'text-delta' || chunk.type === 'reasoning-delta' ||
      chunk.type === 'usage' || chunk.type === 'finish') return null;
  return 'malformed';
}

export async function callNoTool(ctx, request, signal) {
  const idle = { nativeToolEvents: [], nativeToolEventsTruncated: false, streamComplete: false };
  if (!agentRunnerDisabled(ctx)) {
    return { status: 'error', code: 'no-tool-profile-unsafe', modelStarted: false, ...idle };
  }
  const spec = request.spec;
  if (!spec || !['provider', 'model', 'effort'].every((key) => typeof spec[key] === 'string' && spec[key])) {
    return { status: 'error', code: 'invalid-configuration', modelStarted: false, ...idle };
  }
  let prepared;
  try {
    const routeDeadline = Date.now() + 20_000;
    while (!ctx.llm.listProviders().some((entry) => entry.id === spec.provider)) {
      if (signal.aborted) return { status: 'error', code: 'deadline', modelStarted: false, ...idle };
      if (Date.now() >= routeDeadline) {
        return { status: 'error', code: 'configuration-unavailable', modelStarted: false, ...idle };
      }
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    const base = { provider: spec.provider, model: spec.model, reasoningEffort: spec.effort };
    const probe = await ctx.llm.prepareCall(base, signal);
    const bounded = Math.min(probe.config.maxTokens ?? MAX_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS);
    prepared = bounded === probe.config.maxTokens
      ? probe : await ctx.llm.prepareCall({ ...base, maxTokens: bounded }, signal);
    if (prepared.config.provider !== spec.provider || prepared.config.model !== spec.model ||
        prepared.config.reasoningEffort !== spec.effort) {
      return { status: 'error', code: 'configuration-mismatch', modelStarted: false, ...idle };
    }
  } catch {
    return { status: 'error', code: 'configuration-unavailable', modelStarted: false, ...idle };
  }
  const options = { ...prepared.config, tools: [],
    messages: [{ id: request.callId, role: 'user', content: [{ type: 'text', text: request.prompt }],
      source: { kind: 'user' } }], signal };
  // Facts only: the tool events this stream really produced, bounded like the
  // shared evidence package. Nothing here judges them or counts a call that no
  // chunk carried.
  const events = [];
  let truncated = false;
  const recordFact = (fact) => {
    if (events.length >= MAX_TOOL_EVENTS) {
      truncated = true;
      return;
    }
    const event = { nativeIdentity: { callId: request.callId }, phase: fact.phase };
    const callId = boundedFact(fact.callId);
    if (callId) event.callId = callId;
    const name = boundedFact(fact.name);
    if (name) event.toolName = name;
    else {
      const type = boundedFact(fact.type);
      if (type) event.type = type;
    }
    events.push(event);
  };
  let answer = '';
  let usage = null;
  let finishKind = null;
  let failure = null;
  let chunks = 0;
  let malformed = false;
  let finished = false;
  let answerTooLarge = false;
  try {
    for await (const chunk of prepared.stream(options)) {
      chunks += 1;
      if (!chunk || typeof chunk.type !== 'string') {
        malformed = true;
        break;
      }
      const fact = toolFact(chunk);
      if (fact === 'malformed') {
        malformed = true;
        break;
      }
      if (fact) recordFact(fact);
      if (finished) {
        // Any chunk after the one terminal finish leaves the protocol unknown.
        malformed = true;
        break;
      }
      if (chunk.type === 'text-delta') {
        if (typeof chunk.text !== 'string') {
          malformed = true;
          break;
        }
        if (!answerTooLarge) {
          answer += chunk.text;
          if (Buffer.byteLength(answer) > MAX_ANSWER_BYTES) answerTooLarge = true;
        }
      } else if (chunk.type === 'reasoning-delta') {
        if (typeof chunk.text !== 'string') {
          malformed = true;
          break;
        }
      } else if (chunk.type === 'block-start' || chunk.type === 'block-end') {
        // The native terminal finish is still required after completed blocks.
      } else if (chunk.type === 'usage') {
        if (!chunk.usage || typeof chunk.usage !== 'object') {
          malformed = true;
          break;
        }
        usage = chunk.usage;
      } else if (chunk.type === 'finish') {
        finished = true;
        finishKind = boundedFact(chunk.reason?.kind);
        const native = chunk.reason?.failure;
        // Retain only machine classifications; provider messages may contain credentials.
        failure = {};
        for (const key of ['code', 'name', 'category', 'phase']) {
          if (typeof native?.[key] === 'string' && /^[A-Za-z0-9_.:-]{1,100}$/.test(native[key])) failure[key] = native[key];
        }
        for (const key of ['status', 'statusCode', 'httpStatus']) {
          if (Number.isInteger(native?.[key])) failure[key] = native[key];
        }
      }
    }
  } catch {
    return { status: 'error', code: signal.aborted ? 'deadline' : 'invalid-native-result', modelStarted: true,
      nativeToolEvents: events, nativeToolEventsTruncated: truncated, streamComplete: false,
      nativeChunkCount: chunks };
  }
  const observed = { nativeToolEvents: events, nativeToolEventsTruncated: truncated };
  const streamComplete = finished && !malformed;
  if (signal.aborted) {
    return { status: 'error', code: 'deadline', modelStarted: true, ...observed,
      streamComplete: false, nativeChunkCount: chunks };
  }
  if (events.length > 0 || finishKind === 'tool-calls') {
    return { status: 'error', code: 'no-tool-violation', modelStarted: true, ...observed, streamComplete,
      nativeFailure: { finishKind, ...failure }, nativeChunkCount: chunks };
  }
  if (malformed) {
    return { status: 'error', code: 'invalid-native-result', modelStarted: true, ...observed,
      streamComplete: false, nativeChunkCount: chunks };
  }
  if (answerTooLarge) {
    return { status: 'error', code: 'answer-too-large', modelStarted: true, ...observed, streamComplete,
      nativeChunkCount: chunks };
  }
  if (finishKind !== 'stop' || !chunks || !answer.trim()) {
    return { status: 'error', code: 'native-turn-failed', modelStarted: true, ...observed, streamComplete,
      nativeFailure: { finishKind, ...failure }, nativeChunkCount: chunks, usage: safeUsage(usage) };
  }
  return { status: 'ok', rawAnswer: answer, resolved: { provider: spec.provider, model: spec.model, effort: spec.effort },
    observed: null, modelStarted: true, nativeIdentity: { callId: request.callId },
    usage: safeUsage(usage), streamComplete: true, nativeToolsDisabled: true,
    nativeChunkCount: chunks, nativeToolSchemaCount: options.tools.length, ...observed };
}

function writeResult(file, value) {
  const target = join(dirname(file), `.${process.pid}.no-tool.tmp`);
  writeFileSync(target, JSON.stringify(value), { mode: 0o600, flag: 'wx' });
  renameSync(target, file);
}

export function apply(ctx, config) {
  if (typeof config?.requestFile !== 'string' || typeof config?.outputFile !== 'string') return;
  void (async () => {
    let result;
    try {
      const request = JSON.parse(readFileSync(config.requestFile, 'utf8'));
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), Math.max(1, Number(config.timeoutMs) || 1));
      try { result = await callNoTool(ctx, request, controller.signal); }
      finally { clearTimeout(timer); }
    } catch {
      result = { status: 'error', code: 'invalid-native-result', modelStarted: false,
        nativeToolEvents: [], nativeToolEventsTruncated: false, streamComplete: false };
    }
    try { writeResult(config.outputFile, result); }
    catch { /* Missing file is an unconfirmed call, never a success. */ }
    process.exit(result.status === 'ok' ? 0 : 1);
  })();
}
