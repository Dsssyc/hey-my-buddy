/** Native DSH read-only Agent bridge: one restricted root Agent turn whose
 * tool facts are reported as observed. The native registry provides the tools
 * and the LLM loop; the blackboard judges the facts (ADR-021 §4, ADR-023). */
import { readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, isAbsolute, join } from 'node:path';

export const name = 'buddy-read-only-structured';
// The agent registry, session store and tool runtime must be live before the
// bridge composes its Agent; the llm service additionally needs
// credentials-local.loadInitial(), so Cordis activates us only after both.
export const inject = ['agents', 'sessions', 'tools', 'llm', 'credentials'];

const TOOL_NAMES = ['glob', 'grep', 'read'];
const ROOT_PREFIX = 'buddy-read-only-';
const MAX_OUTPUT_TOKENS = 4_096;
const MAX_TOOL_EVENTS = 128;
const USAGE_FIELDS = ['inputTokens', 'outputTokens', 'totalTokens', 'cacheReadTokens',
  'cacheWriteTokens', 'reasoningTokens'];
// Session log vocabulary this bridge understands; anything else on an observed
// session is retained as a fact and leaves the stream incomplete.
const KNOWN_EVENT_TYPES = new Set(['turn/start', 'turn/end', 'step/start', 'step/end',
  'user/message', 'system/message', 'assistant/message', 'assistant/attempt',
  'tool/call', 'tool/result', 'request/header', 'request/context', 'session/end-seed']);

export function agentRunnerDisabled(ctx) {
  try {
    const entries = [...ctx.get('loader').entries()];
    const runner = entries.find((entry) => entry?.options?.id === 'headless-runner');
    return runner !== undefined && (runner.disabled === true || runner.options?.disabled === true);
  } catch {
    return false;
  }
}

function validString(value, bound = 512) {
  return typeof value === 'string' && value.length > 0 && value.length <= bound ? value : undefined;
}

function sessionIdFor(callId) {
  return ROOT_PREFIX + callId.replace(/[^A-Za-z0-9._-]+/g, '-').slice(0, 160);
}

function schemaConstrainedPrompt(prompt, outputSchema) {
  return prompt + '\n\nReturn only one JSON value matching this schema: ' + JSON.stringify(outputSchema);
}

function requestProblem(request) {
  if (!request || typeof request !== 'object') return true;
  const { callId, cwd, spec, prompt, outputSchema, budget } = request;
  if (!validString(callId, 200) || !validString(prompt, 1_000_000)) return true;
  if (typeof cwd !== 'string' || !cwd || !isAbsolute(cwd)) return true;
  if (!spec || !['provider', 'model', 'effort'].every((key) => validString(spec[key]))) return true;
  if (!outputSchema || typeof outputSchema !== 'object' || Array.isArray(outputSchema)) return true;
  if (!budget || typeof budget !== 'object') return true;
  if (!Number.isSafeInteger(budget.timeoutSeconds) || budget.timeoutSeconds < 1) return true;
  if (!Number.isSafeInteger(budget.toolCalls) || budget.toolCalls < 0) return true;
  return false;
}

/** The exact three-tool surface as one sorted name list, or undefined. */
function surfaceNames(schemas) {
  if (!Array.isArray(schemas) || schemas.length !== TOOL_NAMES.length) return undefined;
  const names = schemas.map((schema) => validString(schema?.name)).filter(Boolean);
  if (names.length !== TOOL_NAMES.length) return undefined;
  const sorted = [...names].sort();
  return sorted.every((value, index) => value === TOOL_NAMES[index]) ? sorted : undefined;
}

function createRecorder(state, initiateStop) {
  const retain = (fact) => {
    if (state.events.length >= MAX_TOOL_EVENTS) state.truncated = true;
    else state.events.push(fact);
  };
  const incomplete = () => {
    state.incomplete = true;
  };
  const checkHeader = (data) => {
    const config = data.header?.config;
    const configOk = config && config.provider === state.spec.provider &&
      config.model === state.spec.model && config.reasoningEffort === state.spec.effort;
    if (configOk && surfaceNames(data.header?.tools)) return;
    state.configMismatch = true;
    // The header is appended after prepareCall and before the stream: refuse
    // the mismatched request synchronously rather than letting it continue.
    initiateStop({ kind: 'hook', reason: 'request header does not match the read-only request' });
  };
  const accumulateUsage = (usage) => {
    if (!usage || typeof usage !== 'object') return;
    for (const key of USAGE_FIELDS) {
      const value = usage[key];
      if (Number.isSafeInteger(value) && value >= 0) {
        state.usage[key] = (Number.isSafeInteger(state.usage[key]) ? state.usage[key] : 0) + value;
      }
    }
  };
  const handleToolCall = (sid, data) => {
    if (state.closed) incomplete('late-tool-event');
    const callId = validString(data.callId);
    const toolName = validString(data.toolName ?? data.name);
    if (data.turn !== state.rootTurn || !state.openSteps.has(`${data.turn}:${data.step}`)) {
      incomplete('turn-association');
    } else if (callId) {
      if (!state.startedCallIds.has(callId)) {
        state.startedCallIds.add(callId);
        state.startedCount += 1;
        // The budget counts real starts by callId; the N+1st new call cancels
        // the turn synchronously inside this append notification.
        if (state.startedCount > state.budget.toolCalls) {
          state.budgetExhausted = true;
          initiateStop({ kind: 'hook', reason: 'tool call budget exhausted' });
        }
      }
    }
    if (callId) state.callNames.set(callId, toolName);
    else incomplete('missing-call-id');
    if (!toolName) incomplete('missing-tool-name');
    retain({ nativeIdentity: { sessionId: sid }, ...(callId && { callId }),
      ...(toolName ? { toolName } : { type: 'tool/call' }), phase: 'start' });
  };
  const handleToolResult = (sid, data) => {
    if (state.closed) incomplete('late-tool-event');
    const callId = validString(data.message?.source?.callId) ??
      validString(data.message?.content?.[0]?.toolCallId);
    const toolName = callId ? state.callNames.get(callId) : undefined;
    if (data.turn !== state.rootTurn || !state.seenSteps.has(`${data.turn}:${data.step}`)) {
      incomplete('turn-association');
    } else if (callId) {
      if (state.startedCallIds.has(callId)) state.settledCallIds.add(callId);
      else incomplete('end-before-start');
    }
    if (!callId) incomplete('missing-call-id');
    retain({ nativeIdentity: { sessionId: sid }, ...(callId && { callId }),
      ...(toolName ? { toolName } : { type: 'tool/result' }), phase: 'end' });
  };
  return (session, event) => {
    try {
      const sid = session?.id;
      const type = event?.type;
      const data = event?.data;
      if (typeof sid !== 'string' || typeof type !== 'string' || !data || typeof data !== 'object') {
        incomplete('malformed-native-event');
        return;
      }
      if (sid !== state.sessionId) {
        // Foreign and sub-agent sessions are facts, never filtered away.
        if (type === 'tool/call' || type === 'tool/result') {
          incomplete('foreign-session');
          if (type === 'tool/call') {
            retain({ nativeIdentity: { sessionId: sid },
              ...(validString(data.callId) && { callId: data.callId }),
              ...(validString(data.name) && { toolName: data.name }), phase: 'start' });
          } else {
            const callId = validString(data.message?.source?.callId);
            retain({ nativeIdentity: { sessionId: sid }, ...(callId && { callId }),
              type: 'tool/result', phase: 'end' });
          }
        } else if (!KNOWN_EVENT_TYPES.has(type)) {
          incomplete('unknown-event');
          retain({ nativeIdentity: { sessionId: sid }, type });
        }
        return;
      }
      switch (type) {
        case 'turn/start':
          if (state.rootTurn === null) {
            state.rootTurn = data.turn;
          } else {
            incomplete('unexpected-turn');
            retain({ nativeIdentity: { sessionId: sid }, type });
          }
          break;
        case 'turn/end':
          if (data.turn === state.rootTurn) {
            state.turnEndReason = typeof data.reason?.kind === 'string' ? data.reason.kind : undefined;
            state.closed = true;
          } else {
            incomplete('unexpected-turn');
            retain({ nativeIdentity: { sessionId: sid }, type });
          }
          break;
        case 'step/start':
          if (data.turn === state.rootTurn) {
            state.openSteps.add(`${data.turn}:${data.step}`);
            state.seenSteps.add(`${data.turn}:${data.step}`);
          } else {
            incomplete('turn-association');
          }
          break;
        case 'step/end':
          state.openSteps.delete(`${data.turn}:${data.step}`);
          break;
        case 'request/header':
          state.modelStarted = true;
          checkHeader(data);
          break;
        case 'assistant/message':
          state.modelStarted = true;
          if (data.turn === state.rootTurn && data.message && Array.isArray(data.message.content)) {
            state.assistantMessages.push(data.message);
            accumulateUsage(data.usage);
          }
          break;
        case 'assistant/attempt':
          state.modelStarted = true;
          break;
        case 'tool/call':
          handleToolCall(sid, data);
          break;
        case 'tool/result':
          handleToolResult(sid, data);
          break;
        case 'user/message':
          if (!state.closed && data.source?.kind === 'user' && data.id !== state.messageId) {
            // Only our submitted prompt may enter the root turn as user input;
            // native plugin injections carry their own source kind.
            incomplete('unexpected-input');
          }
          break;
        case 'system/message': case 'request/context': case 'session/end-seed':
          break;
        default:
          incomplete('unknown-event');
          retain({ nativeIdentity: { sessionId: sid }, type });
      }
    } catch {
      incomplete('observer-failure');
    }
  };
}

function rawAnswerFrom(messages) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const texts = (messages[index].content ?? [])
      .filter((block) => block?.type === 'text' && typeof block.text === 'string');
    if (texts.length) return texts[texts.length - 1].text;
  }
  return undefined;
}

function safeUsage(aggregate, toolCalls) {
  const result = { toolCalls };
  for (const key of USAGE_FIELDS) {
    if (Number.isSafeInteger(aggregate[key]) && aggregate[key] >= 0) result[key] = aggregate[key];
  }
  return result;
}

async function drive(handle, state) {
  const agent = handle.agent;
  state.agent = agent;
  if (agent?.id !== state.sessionId || agent?.session?.id !== state.sessionId) return 'invalid-native-result';
  const options = agent.options ?? {};
  if (options.provider !== state.spec.provider || options.model !== state.spec.model ||
      options.reasoningEffort !== state.spec.effort || options.maxTokens !== MAX_OUTPUT_TOKENS) {
    return 'configuration-mismatch';
  }
  agent.followup({ id: state.messageId, role: 'user',
    content: [{ type: 'text', text: state.promptText }], source: { kind: 'user' } });
  await agent.whenIdle();
  state.closed = true;
  if (state.configMismatch) return 'configuration-mismatch';
  if (state.surfaceExpanded) return 'tool-surface-expanded';
  if (state.budgetExhausted) return 'tool-budget-exhausted';
  if ((state.externalAbort || state.deadlineHit) && state.turnEndReason !== 'completed') return 'deadline';
  if (state.rootTurn === null || state.turnEndReason === undefined) return 'stream-incomplete';
  if (state.turnEndReason !== 'completed') return 'native-turn-failed';
  if (state.incomplete || state.truncated) return 'stream-incomplete';
  if ([...state.startedCallIds].some((callId) => !state.settledCallIds.has(callId))) {
    return 'stream-incomplete';
  }
  const rawAnswer = rawAnswerFrom(state.assistantMessages);
  if (typeof rawAnswer !== 'string' || !rawAnswer.trim()) return 'native-turn-failed';
  state.rawAnswer = rawAnswer;
  try {
    if (await state.ctx.sessions.flush(agent.session) !== true) return 'flush-failed';
  } catch {
    return 'flush-failed';
  }
  return null;
}

function assemble(state, code) {
  // A clean run whose handle disposal failed still has an unconfirmed stop.
  const status = code === null && state.stopUnknown ? 'stop-unknown' : code;
  const result = { status: status ? 'error' : 'ok', ...(status && { code: status }),
    modelStarted: state.modelStarted, usage: safeUsage(state.usage, state.startedCount),
    nativeToolEvents: state.events, nativeToolEventsTruncated: state.truncated,
    streamComplete: status === null };
  if (state.published) result.nativeIdentity = { sessionId: state.sessionId };
  if (status === null) {
    result.rawAnswer = state.rawAnswer;
    result.resolved = { provider: state.spec.provider, model: state.spec.model, effort: state.spec.effort };
    result.observed = null;
  } else if (status === 'native-turn-failed' && state.turnEndReason) {
    result.nativeTurnEnd = state.turnEndReason;
  }
  return result;
}

export async function callReadOnly(ctx, request, signal) {
  const refusal = (code) => ({ status: 'error', code, modelStarted: false,
    usage: { toolCalls: 0 }, nativeToolEvents: [], nativeToolEventsTruncated: false, streamComplete: false });
  if (!agentRunnerDisabled(ctx)) return refusal('read-only-profile-unsafe');
  if (requestProblem(request)) return refusal('invalid-configuration');
  try {
    if (!ctx.llm.listProviders().some((entry) => entry?.id === request.spec.provider)) {
      return refusal('configuration-unavailable');
    }
  } catch {
    return refusal('configuration-unavailable');
  }
  const sessionId = sessionIdFor(request.callId);
  const state = {
    ctx, spec: request.spec, budget: request.budget, sessionId, published: false,
    messageId: `${sessionId}-prompt`, promptText: schemaConstrainedPrompt(request.prompt, request.outputSchema),
    agent: null, events: [], truncated: false, usage: {}, deniedFacts: [],
    startedCallIds: new Set(), settledCallIds: new Set(), callNames: new Map(), startedCount: 0,
    openSteps: new Set(), seenSteps: new Set(), assistantMessages: [],
    rootTurn: null, turnEndReason: undefined, closed: false, modelStarted: false,
    incomplete: false, configMismatch: false, surfaceExpanded: false,
    budgetExhausted: false, externalAbort: false, deadlineHit: false, stopping: false,
    restrictFailed: false, commitFailed: false, stopUnknown: false, rawAnswer: undefined,
  };
  const initiateStop = (cause) => {
    if (state.stopping || !state.agent) return;
    state.stopping = true;
    try { state.agent.cancel(cause); } catch { /* Dispose still owns the stop. */ }
  };
  const recorder = createRecorder(state, initiateStop);
  const onAbort = () => { state.externalAbort = true; initiateStop({ kind: 'user' }); };
  const onDeadline = () => { state.deadlineHit = true; initiateStop({ kind: 'user' }); };
  let handle = null;
  let detach = null;
  let timer = null;
  let code;
  try {
    detach = ctx.on('session/event', recorder);
    signal?.addEventListener('abort', onAbort, { once: true });
    timer = setTimeout(onDeadline, request.budget.timeoutSeconds * 1_000);
    handle = await ctx.agents.create({
      sessionId,
      meta: { cwd: request.cwd },
      agentOptions: { provider: request.spec.provider, model: request.spec.model,
        reasoningEffort: request.spec.effort, maxTokens: MAX_OUTPUT_TOKENS },
      signal,
      setup(agentCtx, agent) {
        try {
          agentCtx.tools.restrict({ allow: TOOL_NAMES });
        } catch (error) {
          state.restrictFailed = true;
          throw error;
        }
        agentCtx.tools.presentAs('native');
        agentCtx.tools.guard((execution) => {
          const toolName = validString(execution?.name);
          if (!toolName || !TOOL_NAMES.includes(toolName)) {
            state.deniedFacts.push({ callId: validString(execution?.callId) ?? null, toolName: toolName ?? null });
            return `read-only bridge denies tool ${toolName ?? '<unnamed>'}`;
          }
          return undefined;
        });
        agentCtx.on('agent/pre-step', async (_payload, next) => {
          if (!surfaceNames(agentCtx.tools.schemas())) {
            state.surfaceExpanded = true;
            initiateStop({ kind: 'hook', reason: 'read-only tool surface expanded before step' });
            return { kind: 'reject' };
          }
          return next();
        });
        return {
          commit() {
            if (!surfaceNames(agentCtx.tools.schemas())) {
              state.surfaceExpanded = true;
              state.commitFailed = true;
              throw new Error('read-only tool surface expanded at publication');
            }
          },
        };
      },
    });
    state.published = true;
    code = await drive(handle, state);
  } catch {
    // Setup, commit and creation failures roll the unpublished Agent back.
    code = state.restrictFailed ? 'read-only-tools-unavailable'
      : state.commitFailed ? 'tool-surface-expanded'
        : state.published ? 'invalid-native-result'
          : signal?.aborted ? 'deadline' : 'configuration-unavailable';
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  }
  // Guard-denied executions whose call never reached the session log stay
  // recorded as facts; the unpaired start also leaves the stream incomplete.
  for (const denied of state.deniedFacts) {
    if (!denied.callId || !state.startedCallIds.has(denied.callId)) {
      state.incomplete = true;
      if (state.events.length >= MAX_TOOL_EVENTS) state.truncated = true;
      else {
        state.events.push({ nativeIdentity: { sessionId }, ...(denied.callId && { callId: denied.callId }),
          ...(denied.toolName ? { toolName: denied.toolName } : { type: 'tool/call' }), phase: 'start' });
      }
    }
  }
  if (handle) {
    try {
      await handle.dispose();
    } catch {
      state.stopUnknown = true;
    }
  }
  // The listener stays attached through dispose so closing tool events are
  // still observed as late facts.
  detach?.();
  if (code === null && (state.incomplete || state.truncated)) code = 'stream-incomplete';
  return assemble(state, code);
}

function writeResult(file, value) {
  const target = join(dirname(file), `.${process.pid}.read-only.tmp`);
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
      try { result = await callReadOnly(ctx, request, controller.signal); }
      finally { clearTimeout(timer); }
    } catch {
      result = { status: 'error', code: 'invalid-native-result', modelStarted: false };
    }
    try { writeResult(config.outputFile, result); }
    catch { /* Missing file is an unconfirmed call, never a success. */ }
    process.exit(result.status === 'ok' ? 0 : 1);
  })();
}
