/**
 * Root-scoped Buddy terminal tool for DSH 0.1.5-rc.1.
 *
 * Verified against installed @deepseek-ai packages: dsh-subagent-in-process-
 * driver/lib/index.js attachStructuredRuntime (execution WeakMap, enclosing
 * PTC acceptance, concludeTurn and terminal guard); dsh-agent-loop/lib/index.js
 * send/executeToolCalls (inbox insertion precedes first assembly, tools/result
 * precedes session append); dsh-session/lib/index.js flush (awaited listeners);
 * dsh-headless/lib/index.js run (whenIdle -> flush -> completed exit).
 *
 * This plugin owns only a private attempt receipt. Python owns task state.
 * No cancellation, global tool registration, DSH storage writes or prose parsing.
 */
import {
  TURN_OUTCOME_SCHEMA, TURN_TOOL, sha256, turnOutputPath, validateTurnInput,
  validateTurnOutcome, validateTurnRecord, writeTurnRecord,
} from '../scripts/lib/turn-contract.mjs';

export const name = 'deepseek-delegate-turn-result';
export const inject = ['agents', 'tools', 'systemPrompt'];

function ordinaryText(message) {
  if (message?.role !== 'user' || message.source?.kind !== 'user' || !Array.isArray(message.content)) return undefined;
  let text = '';
  for (const block of message.content) {
    if (block?.type !== 'text' || typeof block.text !== 'string') return undefined;
    text += block.text;
  }
  return text;
}

function rootSession(session, cwd) {
  const header = session?.header;
  return header !== null && typeof header === 'object' && header.cwd === cwd
    && (header.parentSession === undefined || header.parentSession === null)
    && header.origin !== 'subagent' && !(typeof header.delegationDepth === 'number' && header.delegationDepth > 0);
}

/** Install only into the first-prompt-correlated root's exact agent scope. */
export function apply(ctx, config) {
  const input = validateTurnInput(JSON.parse(JSON.stringify(config?.input)));
  const expected = { input, inputSha256: config.inputSha256, promptSha256: config.promptSha256 };
  if (![expected.inputSha256, expected.promptSha256].every((value) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value))) throw new Error('Buddy turn configuration requires input/prompt hashes');
  const outputFile = turnOutputPath(config.outputFile);
  if (typeof config.cwd !== 'string' || !config.cwd.startsWith('/')) throw new Error('Buddy turn configuration requires canonical cwd');
  const firstInbox = new WeakSet();
  const firstCommitted = new WeakSet();
  const staged = new WeakMap();
  let root;
  let promptSeq;
  let activeTurn;
  let lastCall;
  let pending;
  let captured;
  let invalid;
  let writtenSeq;

  const liveRoot = (agent) => rootSession(agent?.session, config.cwd)
    && agent.id === agent.session.id && ctx.agents.get(agent.id) === agent && ctx.agents.roots().includes(agent);
  const owns = (agent) => agent === root && liveRoot(agent) && invalid === undefined;

  function register(agent) {
    agent.ctx.tools.register({
      name: TURN_TOOL,
      description: 'Conclude this Buddy turn with the complete structured result. Use completed for final delivery, assistance for bounded help, or attention for a Host decision. This does not dispatch another task. Call once after your other work and tool calls have settled.',
      parameters: TURN_OUTCOME_SCHEMA,
      output: {
        schema: { type: 'object', additionalProperties: false, required: ['recorded'], properties: { recorded: { type: 'boolean', const: true } } },
        render: () => [{ type: 'text', text: 'Buddy turn result accepted for durable recording.' }],
      },
      async execute(args, exec) {
        if (!owns(exec?.agent) || promptSeq === undefined) throw new Error('buddy_finish_turn requires this run\'s committed first root user prompt');
        if (typeof exec.concludeTurn !== 'function') throw new Error('DSH lacks the required concludeTurn API');
        exec.signal.throwIfAborted();
        if (pending !== undefined || captured !== undefined) throw new Error('Buddy turn result is already accepted');
        const outcome = validateTurnOutcome(JSON.parse(JSON.stringify(args)));
        const rootCallId = String(exec.rootCallId);
        const toolCallId = String(exec.callId);
        if (lastCall?.callId !== rootCallId || lastCall.turn !== activeTurn || lastCall.seq <= promptSeq) throw new Error('Buddy terminal call has no matching root session tool/call');
        if (exec.parent === undefined ? lastCall.name !== TURN_TOOL || rootCallId !== toolCallId : lastCall.name !== 'run_code' || rootCallId === toolCallId) throw new Error('unsupported Buddy terminal tool transport');
        staged.set(exec, { outcome, rootCallId, toolCallId, toolCallSeq: lastCall.seq, turn: activeTurn, ptcDispatchSeq: null });
        exec.concludeTurn();
        return { recorded: true };
      },
    });
    agent.ctx.systemPrompt.section({
      name: `tool:${TURN_TOOL}`,
      order: agent.ctx.systemPrompt.getSectionOrder('STRUCTURED_OUTPUT'),
      text: [
        `This is a governed Buddy turn. You MUST finish by calling ${TURN_TOOL} with the full result. Plain final text does not record a result.`,
        'Call it after your own work and internal subagents have settled. Internal subagents remain available; only you conclude the Buddy turn.',
        'Use request: null for completed. Assistance/attention requires a nonblank summary, attempted, neededWork and acceptance, plus expectedArtifacts as an array. Keep arrays to 32 items and the whole outcome to 64 KiB.',
        'The following JSON is the Host-provided frozen input for this execution. Consume its decisions, pinned artifacts and next actions. Reconstructed-new-session means a new session, not native session resume.',
        JSON.stringify({ resumeMode: input.resumeMode, previousSessionId: input.previousSessionId, context: input.context, executionWorkspace: input.executionWorkspace }),
      ].join('\n\n'),
    });
    agent.ctx.tools.guard((exec) => {
      if (exec.agent !== root) return exec.name === TURN_TOOL ? 'Only the owning root agent may conclude a Buddy turn' : undefined;
      if (invalid !== undefined) return invalid;
      return pending !== undefined || captured !== undefined ? 'Buddy turn result already accepted; later tools are not executed' : undefined;
    });
    agent.ctx.on('tools/result', (exec, result) => {
      if (!owns(exec.agent)) return;
      if (exec.name === TURN_TOOL) {
        const entry = staged.get(exec);
        staged.delete(exec);
        if (entry === undefined || result.isError || result.concludesTurn !== true || result.value?.recorded !== true || exec.signal.aborted) return;
        if (exec.parent === undefined) captured = entry;
        else pending = { ...entry, parent: exec.parent };
        return;
      }
      if (pending?.parent !== exec.token) return;
      const entry = pending;
      pending = undefined;
      if (exec.parent !== undefined || exec.callId !== entry.rootCallId || result.isError || result.concludesTurn !== true || exec.signal.aborted) return;
      captured = entry;
    });
  }

  // AgentLoop.send() emits inserted synchronously BEFORE wakeDriver/assembly.
  // Registration here is root-local and the first model request can see it.
  // Delivery remains unconfirmed until the ordinary session user/message.
  ctx.on('agent/inbox/inserted', ({ agent, message }) => {
    if (message?.role !== 'user' || message.source?.kind !== 'user' || !liveRoot(agent) || firstInbox.has(agent)) return;
    firstInbox.add(agent);
    const text = ordinaryText(message);
    if (text === undefined) return;
    if (sha256(text) !== expected.promptSha256) return;
    if (root !== undefined && root !== agent) { invalid = 'Ambiguous root sessions matched this Buddy prompt'; return; }
    root = agent;
    try { register(agent); } catch (error) { invalid = `Buddy terminal tool registration failed: ${error.message}`; }
  });

  ctx.on('session/event', (session, event) => {
    if (!rootSession(session, config.cwd)) return;
    if (event?.type === 'user/message' && event.data?.role === 'user' && event.data.source?.kind === 'user' && !firstCommitted.has(session)) {
      firstCommitted.add(session);
      const text = ordinaryText(event.data);
      const matches = text !== undefined && sha256(text) === expected.promptSha256;
      if (matches && root !== undefined && session !== root.session) invalid = 'Ambiguous root sessions matched this Buddy prompt';
      if (session === root?.session) {
        if (matches) promptSeq = event.seq;
        else invalid = 'The first committed ordinary root prompt did not match';
      }
    }
    if (session !== root?.session || invalid !== undefined) return;
    if (event.type === 'turn/start') {
      if (captured !== undefined) invalid = 'The root opened another turn after its structured result';
      activeTurn = event.data.turn;
    }
    if (event.type === 'tool/call') lastCall = { ...event.data, seq: event.seq };
    const candidate = captured ?? pending;
    if (candidate === undefined) return;
    if (event.type === 'tool/ptc-dispatch' && event.data.subCallId === candidate.toolCallId && event.data.rootCallId === candidate.rootCallId && event.data.name === TURN_TOOL && event.data.isError === false) {
      candidate.ptcDispatchSeq = event.seq;
    }
    if (event.type === 'tool/result' && captured !== undefined) {
      const message = event.data.message;
      const block = message?.content?.[0];
      if (message?.source?.kind === 'tool' && message.source.callId === captured.rootCallId && block?.type === 'tool-result' && block.toolCallId === captured.rootCallId && block.isError === false && event.data.turn === captured.turn && event.sourceEventSeqs?.length === 1 && event.sourceEventSeqs[0] === captured.toolCallSeq) {
        captured.toolResultSeq = event.seq;
      }
    }
    if (event.type === 'turn/end' && captured !== undefined && event.data.turn === captured.turn) {
      if (event.data.reason?.kind !== 'completed') invalid = 'The root turn did not complete normally';
      else captured.turnEndSeq = event.seq;
    }
  });

  // Unlike emit observers, session/flush is awaited and propagates write errors.
  // Earlier per-request flushes occur while running and cannot publish a result.
  ctx.on('session/flush', (session) => {
    if (session !== root?.session || root.status !== 'idle') return;
    if (invalid !== undefined) throw new Error(invalid);
    if (captured === undefined || promptSeq === undefined) throw new Error('Buddy turn ended without an accepted buddy_finish_turn result');
    if (writtenSeq !== undefined) {
      if (session.seq !== writtenSeq) throw new Error('Buddy session changed after its result was flushed');
      return;
    }
    const { version, taskId, attemptId, generation, turnId, resumeMode, previousSessionId } = input;
    const record = {
      version, taskId, attemptId, generation, turnId, resumeMode, previousSessionId,
      sessionId: String(root.id), promptSha256: expected.promptSha256, inputSha256: expected.inputSha256,
      outcome: captured.outcome,
      provenance: {
        tool: TURN_TOOL, turnEnd: 'completed', flush: 'awaited', rootSessionMatched: true,
        toolCallId: captured.toolCallId, rootCallId: captured.rootCallId, promptSeq,
        toolCallSeq: captured.toolCallSeq, toolResultSeq: captured.toolResultSeq,
        ptcDispatchSeq: captured.ptcDispatchSeq, turn: captured.turn,
        turnEndSeq: captured.turnEndSeq, flushSeq: session.seq,
      },
    };
    validateTurnRecord(record, expected);
    writeTurnRecord(outputFile, record);
    writtenSeq = session.seq;
  });
}
