/** One native DSH LLM call, with no Agent, Session, or tool registry. */
import { readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

export const name = 'buddy-no-tool-structured';
// Provider registration alone can precede credentials-local.loadInitial().
// Cordis must activate this caller only after both services are ready.
export const inject = ['llm', 'credentials'];
const MAX_ANSWER_BYTES = 65_536;
const MAX_OUTPUT_TOKENS = 4_096;
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

export async function callNoTool(ctx, request, signal) {
  if (!agentRunnerDisabled(ctx)) return { status: 'error', code: 'no-tool-profile-unsafe', modelStarted: false };
  const spec = request.spec;
  if (!spec || !['provider', 'model', 'effort'].every((key) => typeof spec[key] === 'string' && spec[key])) {
    return { status: 'error', code: 'invalid-configuration', modelStarted: false };
  }
  let prepared;
  try {
    const routeDeadline = Date.now() + 20_000;
    while (!ctx.llm.listProviders().some((entry) => entry.id === spec.provider)) {
      if (signal.aborted) return { status: 'error', code: 'deadline', modelStarted: false };
      if (Date.now() >= routeDeadline) return { status: 'error', code: 'configuration-unavailable', modelStarted: false };
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    const base = { provider: spec.provider, model: spec.model, reasoningEffort: spec.effort };
    const probe = await ctx.llm.prepareCall(base, signal);
    const bounded = Math.min(probe.config.maxTokens ?? MAX_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS);
    prepared = bounded === probe.config.maxTokens
      ? probe : await ctx.llm.prepareCall({ ...base, maxTokens: bounded }, signal);
    if (prepared.config.provider !== spec.provider || prepared.config.model !== spec.model ||
        prepared.config.reasoningEffort !== spec.effort) {
      return { status: 'error', code: 'configuration-mismatch', modelStarted: false };
    }
  } catch {
    return { status: 'error', code: 'configuration-unavailable', modelStarted: false };
  }
  const options = { ...prepared.config, tools: [],
    messages: [{ id: request.callId, role: 'user', content: [{ type: 'text', text: request.prompt }],
      source: { kind: 'user' } }], signal };
  let answer = '';
  let usage = null;
  let finish = null;
  let failure = null;
  let chunks = 0;
  try {
    for await (const chunk of prepared.stream(options)) {
      chunks += 1;
      if (!chunk || typeof chunk.type !== 'string') {
        return { status: 'error', code: 'invalid-native-result', modelStarted: true };
      }
      if (/(tool|mcp|agent|search|shell)/i.test(chunk.type) ||
          chunk.type === 'block-start' && !['text', 'reasoning'].includes(chunk.blockType) ||
          chunk.type === 'block-end' && !['text', 'reasoning'].includes(chunk.block?.type) ||
          chunk.type === 'finish' && chunk.reason?.kind === 'tool-calls') {
        return { status: 'error', code: 'no-tool-violation', modelStarted: true, usage: { toolCalls: 1 } };
      }
      if (finish !== null) return { status: 'error', code: 'invalid-native-result', modelStarted: true };
      if (chunk.type === 'text-delta') {
        if (typeof chunk.text !== 'string') return { status: 'error', code: 'invalid-native-result', modelStarted: true };
        answer += chunk.text;
        if (Buffer.byteLength(answer) > MAX_ANSWER_BYTES) {
          return { status: 'error', code: 'answer-too-large', modelStarted: true };
        }
      } else if (chunk.type === 'reasoning-delta') {
        if (typeof chunk.text !== 'string') return { status: 'error', code: 'invalid-native-result', modelStarted: true };
      } else if (chunk.type === 'block-start' || chunk.type === 'block-end') {
        // The native terminal finish is still required after completed blocks.
      } else if (chunk.type === 'usage') {
        if (!chunk.usage || typeof chunk.usage !== 'object') return { status: 'error', code: 'invalid-native-result', modelStarted: true };
        usage = chunk.usage;
      } else if (chunk.type === 'finish') {
        finish = chunk.reason?.kind;
        const native = chunk.reason?.failure;
        // Retain only machine classifications; provider messages may contain credentials.
        failure = {};
        for (const key of ['code', 'name', 'category', 'phase']) {
          if (typeof native?.[key] === 'string' && /^[A-Za-z0-9_.:-]{1,100}$/.test(native[key])) failure[key] = native[key];
        }
        for (const key of ['status', 'statusCode', 'httpStatus']) {
          if (Number.isInteger(native?.[key])) failure[key] = native[key];
        }
      } else {
        return { status: 'error', code: 'invalid-native-result', modelStarted: true };
      }
    }
  } catch {
    return { status: 'error', code: signal.aborted ? 'deadline' : 'invalid-native-result', modelStarted: true };
  }
  if (signal.aborted) return { status: 'error', code: 'deadline', modelStarted: true };
  if (finish !== 'stop' || !chunks || !answer.trim()) {
    return { status: 'error', code: 'native-turn-failed', modelStarted: true,
      nativeFailure: { finishKind: finish, ...failure }, nativeChunkCount: chunks, usage: safeUsage(usage) };
  }
  return { status: 'ok', rawAnswer: answer, resolved: { provider: spec.provider, model: spec.model, effort: spec.effort },
    observed: null, modelStarted: true, nativeIdentity: { callId: request.callId },
    usage: safeUsage(usage), streamComplete: true, nativeToolsDisabled: true,
    nativeChunkCount: chunks, nativeToolSchemaCount: options.tools.length };
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
      result = { status: 'error', code: 'invalid-native-result', modelStarted: false };
    }
    try { writeResult(config.outputFile, result); }
    catch { /* Missing file is an unconfirmed call, never a success. */ }
    process.exit(result.status === 'ok' ? 0 : 1);
  })();
}
