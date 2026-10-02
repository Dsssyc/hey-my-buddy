/** Fake-native fixtures for the read-only DSH bridge: a Cordis-shaped context,
 * an AgentRegistry that composes setup before publication, a scoped ToolRuntime
 * and an event-sourced session log, all scripted per test. No model runs. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { apply, callReadOnly } from '../plugins/read-only-structured.mjs';

const spec = { provider: 'deepseek-official', model: 'deepseek-flash', effort: 'max' };
const OUTPUT_SCHEMA = { type: 'object', properties: { choice: { type: 'string' } }, required: ['choice'] };
const CWD = '/tmp/buddy-frozen-copy';
const ROOT_ID = 'buddy-read-only-fake-call';

function readOnlyRequest(overrides = {}) {
  return { callId: 'fake-call', cwd: CWD, spec, prompt: 'choose a profile',
    outputSchema: OUTPUT_SCHEMA, budget: { timeoutSeconds: 60, toolCalls: 8 }, ...overrides };
}

function expectedPrompt(request) {
  return request.prompt + '\n\nReturn only one JSON value matching this schema: '
    + JSON.stringify(request.outputSchema);
}

function fakeToolkit({ globals = ['read', 'glob', 'grep', 'bash', 'edit', 'write'] } = {}) {
  const definitions = new Map(globals.map((toolName) => [toolName, { name: toolName }]));
  const scopes = new Map();
  const globalGuards = [];
  let current = null;
  const scopeOf = (key) => {
    if (!scopes.has(key)) scopes.set(key, { restrictions: [], registrations: new Map(), mode: null, guards: [] });
    return scopes.get(key);
  };
  const visibleNames = (key) => {
    let names = [...definitions.keys()];
    const scope = scopes.get(key);
    if (scope) {
      for (const filter of scope.restrictions) {
        if (filter.allow) names = names.filter((name) => filter.allow.includes(name));
        if (filter.deny) names = names.filter((name) => !filter.deny.includes(name));
      }
      names = [...names, ...scope.registrations.keys()];
      if (scope.mode === 'ptc') return ['run_code'];
    }
    return names.sort();
  };
  return {
    pluginRegistrations: [],
    executed: [],
    definitionOf: (toolName) => definitions.get(toolName),
    enter(scope) { current = scope; },
    exit() { current = null; },
    register(definition) {
      this.pluginRegistrations.push(definition.name);
      definitions.set(definition.name, definition);
      return () => definitions.delete(definition.name);
    },
    registerScoped(key, definition) { scopeOf(key).registrations.set(definition.name, definition); },
    presentAsFor(key, mode) { scopeOf(key).mode = mode; },
    restrict(filter) {
      if (filter?.allow?.some((name) => !definitions.has(name))) {
        throw new Error(`unknown tool in restriction: ${filter.allow.join(',')}`);
      }
      scopeOf(current).restrictions.push(filter);
      return () => {};
    },
    presentAs(mode) { scopeOf(current).mode = mode; return () => {}; },
    guard(callback) {
      (current ? scopeOf(current).guards : globalGuards).push(callback);
      return () => {};
    },
    schemas(key = current) { return visibleNames(key).map((toolName) => ({ name: toolName })); },
    names(key = current) { return visibleNames(key); },
    get(toolName, key = current) {
      return visibleNames(key).includes(toolName) ? definitions.get(toolName) : undefined;
    },
    guardReason(execution, key = current) {
      for (const callback of [...globalGuards, ...(scopes.get(key)?.guards ?? [])]) {
        const reason = callback(execution);
        if (reason) return reason;
      }
      return undefined;
    },
  };
}

let eventSeq = 0;

function fixture(script, {
  runnerDisabled = true, providers = [spec.provider], mangleOptions, disposeThrows = false,
  noTurnEnd = false, flushResult = true, flushThrows = false, globals, beforeCommit, breakIdentity = false,
} = {}) {
  const order = [];
  const listeners = new Map();
  const emit = (type, ...args) => { for (const callback of listeners.get(type) ?? []) callback(...args); };
  const toolkit = fakeToolkit({ globals });
  const sessions = {
    behavior: { flushResult, flushThrows },
    flushCalls: 0,
    store: new Map(),
    create(id, options = {}) {
      const session = { id, header: { cwd: options.meta?.cwd } };
      this.store.set(id, session);
      return session;
    },
    get(id) { return this.store.get(id); },
    async flush(session) {
      order.push('flush');
      this.flushCalls += 1;
      if (this.behavior.flushThrows) throw new Error('flush failed');
      return this.behavior.flushResult;
    },
  };
  const append = (session, type, data) => {
    order.push(type + (typeof data?.callId === 'string' ? `:${data.callId}` : ''));
    emit('session/event', session, { type, seq: eventSeq++, time: Date.now(), data });
  };
  const world = { toolkit, append, script, noTurnEnd, spec };
  const makeAgent = (session, agentOptions) => {
    const agent = {
      id: session.id, options: agentOptions, session, status: 'idle', disposed: false,
      followups: [], listeners: new Map(), cancelled: false, cancelCause: null, done: null,
      cancel(cause) { agent.cancelled = true; agent.cancelCause = cause; order.push('cancel'); },
      followup(message) {
        agent.followups.push(message);
        agent.status = 'running';
        agent.done = (async () => {
          await Promise.resolve();
          await runScript(world, agent, message);
          agent.status = 'idle';
        })();
      },
      async whenIdle() { if (agent.done) await agent.done; },
      on(type, callback) {
        if (!agent.listeners.has(type)) agent.listeners.set(type, []);
        agent.listeners.get(type).push(callback);
        return () => {};
      },
    };
    return agent;
  };
  const registry = {
    createCalls: [], agents: [], disposeCount: 0,
    async create(options) {
      this.createCalls.push(options);
      if (options.signal?.aborted) throw new Error('creation aborted');
      const session = sessions.create(options.sessionId, { meta: options.meta });
      const agentOptions = mangleOptions ? mangleOptions(options.agentOptions) : options.agentOptions;
      const agent = makeAgent(breakIdentity ? sessions.create(options.sessionId + '-other') : session, agentOptions);
      if (breakIdentity) agent.session = session;
      toolkit.enter(agent);
      try {
        order.push('setup');
        const commit = await options.setup?.({ tools: toolkit, on: agent.on.bind(agent) }, agent);
        order.push('setup-done');
        if (beforeCommit) beforeCommit(agent);
        commit?.commit?.();
      } finally { toolkit.exit(); }
      order.push('published');
      this.agents.push(agent);
      return {
        agent,
        dispose: async () => {
          order.push('dispose');
          registry.disposeCount += 1;
          if (disposeThrows) throw new Error('dispose failed');
          agent.disposed = true;
        },
      };
    },
  };
  const ctx = {
    on(type, callback) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(callback);
      return () => listeners.get(type)?.delete(callback);
    },
    get: (serviceName) => serviceName === 'loader'
      ? { entries: () => [{ options: { id: 'headless-runner', disabled: runnerDisabled } }] }
      : undefined,
    llm: { listProviders: () => providers.map((id) => ({ id })) },
    agents: registry,
    sessions,
    tools: toolkit,
  };
  return { ctx, order, toolkit, sessions, registry, script: world.script };
}

async function runScript(world, agent, message) {
  const { toolkit, append } = world;
  append(agent.session, 'turn/start', { turn: 1 });
  let step = 0;
  let turnEnded = false;
  const endTurn = (reason) => { turnEnded = true; append(agent.session, 'turn/end', { turn: 1, reason }); };
  for (const action of world.script) {
    if (agent.cancelled) break;
    if (action.wait) { await action.wait; continue; }
    if (action.run) { action.run(world, agent); continue; }
    if (action.header) {
      step += 1;
      toolkit.enter(agent);
      let decision = { kind: 'enter', messages: [message] };
      try {
        for (const listener of agent.listeners.get('agent/pre-step') ?? []) {
          decision = await listener({ agent, turn: 1, step, messages: [message] },
            () => Promise.resolve(decision));
        }
      } finally { toolkit.exit(); }
      if (decision.kind === 'reject') { endTurn({ kind: 'blocked' }); return; }
      append(agent.session, 'step/start', { turn: 1, step });
      append(agent.session, 'user/message', { id: message.id, role: 'user',
        content: message.content, source: message.source });
      append(agent.session, 'request/header', { header: {
        config: { provider: spec.provider, model: spec.model, reasoningEffort: spec.effort,
          maxTokens: 4096, ...action.header.config },
        tools: 'tools' in action.header ? action.header.tools : toolkit.schemas(agent),
      }, reason: 'initial' });
      continue;
    }
    if (action.stepEnd) { append(agent.session, 'step/end', { turn: 1, step: action.stepEnd }); continue; }
    if (action.call) {
      const { callId, name, turn = 1, step: callStep = Math.max(step, 1), noResult = false } = action.call;
      append(agent.session, 'tool/call', { turn, step: callStep, callId, name, arguments: '{}' });
      if (noResult) continue;
      const signal = new AbortController().signal;
      const reason = toolkit.guardReason({ callId, name, arguments: '{}', agent, rootCallId: callId, signal }, agent);
      const isError = reason !== undefined;
      if (!isError) {
        const definition = toolkit.get(name, agent);
        if (definition) toolkit.executed.push(definition);
      }
      append(agent.session, 'tool/result', { turn, step: callStep,
        message: { id: `result-${callId}`, role: 'user',
          content: [{ type: 'tool-result', toolCallId: callId,
            content: [{ type: 'text', text: isError ? reason : 'ok' }], isError }],
          source: { kind: 'tool', callId } },
        ...(isError && { error: { name, code: 'DENIED' } }) });
      continue;
    }
    if (action.assistant) {
      const content = action.assistant.blocks ?? [{ type: 'text', text: action.assistant.text ?? 'answer' }];
      append(agent.session, 'assistant/message', { turn: 1, step: Math.max(step, 1),
        message: { id: `assistant-${eventSeq}`, role: 'assistant', content,
          source: { kind: 'model', provider: spec.provider, model: spec.model } },
        stream: [], ...(action.assistant.usage && { usage: action.assistant.usage }) });
      continue;
    }
    if (action.turnEnd) { endTurn(action.turnEnd); continue; }
    if (action.event) {
      append(action.event.session ?? agent.session, action.event.type, action.event.data ?? {});
      continue;
    }
  }
  if (agent.cancelled) endTurn({ kind: 'aborted', reason: agent.cancelCause });
  else if (!turnEnded && !world.noTurnEnd) endTurn({ kind: 'completed' });
}

test('invalid requests, unsafe profiles and missing providers never create an Agent', async () => {
  for (const bad of [
    readOnlyRequest({ callId: '' }), readOnlyRequest({ cwd: 'relative/path' }),
    readOnlyRequest({ spec: { provider: '', model: 'm', effort: 'e' } }),
    readOnlyRequest({ prompt: '' }), readOnlyRequest({ outputSchema: null }),
    readOnlyRequest({ budget: { timeoutSeconds: 0, toolCalls: 8 } }),
    readOnlyRequest({ budget: { timeoutSeconds: 60 } }),
    { ...readOnlyRequest(), budget: 'big' },
  ]) {
    const { ctx, registry } = fixture([]);
    const result = await callReadOnly(ctx, bad, new AbortController().signal);
    assert.equal(result.status, 'error');
    assert.equal(result.code, 'invalid-configuration');
    assert.deepEqual(registry.createCalls, []);
    assert.equal(result.streamComplete, false);
  }
  const unsafe = fixture([], { runnerDisabled: false });
  assert.equal((await callReadOnly(unsafe.ctx, readOnlyRequest(), new AbortController().signal)).code,
    'read-only-profile-unsafe');
  const unavailable = fixture([], { providers: ['other-provider'] });
  const missing = await callReadOnly(unavailable.ctx, readOnlyRequest(), new AbortController().signal);
  assert.equal(missing.code, 'configuration-unavailable');
  assert.deepEqual(unavailable.registry.createCalls, []);
});

test('a clean turn reuses the native tools and reports the full fact package', async () => {
  const script = [
    { header: {} },
    { call: { callId: 'call-1', name: 'read' } },
    { call: { callId: 'call-2', name: 'grep' } },
    { assistant: { text: 'partial', usage: { inputTokens: 10, outputTokens: 5 } } },
    { stepEnd: 1 },
    { header: {} },
    { assistant: { blocks: [{ type: 'reasoning', text: 'hmm' }, { type: 'text', text: '{"choice":"b"}' }],
      usage: { inputTokens: 7, outputTokens: 3 } } },
    { turnEnd: { kind: 'completed' } },
  ];
  const { ctx, order, toolkit, registry } = fixture(script);
  const request = readOnlyRequest();
  const result = await callReadOnly(ctx, request, new AbortController().signal);
  assert.equal(result.status, 'ok');
  assert.equal(result.code, undefined);
  assert.equal(result.streamComplete, true);
  assert.equal(result.modelStarted, true);
  assert.equal(result.rawAnswer, '{"choice":"b"}');
  assert.deepEqual(result.resolved, { provider: spec.provider, model: spec.model, effort: spec.effort });
  assert.equal(result.observed, null);
  assert.deepEqual(result.nativeIdentity, { sessionId: ROOT_ID });
  assert.deepEqual(result.usage, { toolCalls: 2, inputTokens: 17, outputTokens: 8 });
  assert.equal(result.nativeToolEventsTruncated, false);
  assert.deepEqual(result.nativeToolEvents.map((event) => [event.callId, event.toolName, event.phase]), [
    ['call-1', 'read', 'start'], ['call-1', 'read', 'end'],
    ['call-2', 'grep', 'start'], ['call-2', 'grep', 'end'],
  ]);
  assert.ok(result.nativeToolEvents.every((event) => Object.keys(event.nativeIdentity).length === 1
    && event.nativeIdentity.sessionId === ROOT_ID && !('arguments' in event)));
  // Composition happens before publication and before the first model step.
  assert.ok(order.indexOf('setup') < order.indexOf('published'));
  assert.ok(order.indexOf('published') < order.indexOf('turn/start'));
  assert.ok(order.indexOf('flush') < order.indexOf('dispose'));
  assert.equal(registry.disposeCount, 1);
  // The bridge registers nothing and drives the official plugin tools.
  assert.deepEqual(toolkit.pluginRegistrations, []);
  assert.deepEqual(toolkit.executed, [toolkit.definitionOf('read'), toolkit.definitionOf('grep')]);
  assert.deepEqual(toolkit.names(registry.agents[0]), ['glob', 'grep', 'read']);
  const created = registry.createCalls[0];
  assert.equal(created.sessionId, ROOT_ID);
  assert.deepEqual(created.meta, { cwd: CWD });
  assert.deepEqual(created.agentOptions,
    { provider: spec.provider, model: spec.model, reasoningEffort: spec.effort, maxTokens: 4096 });
  const sent = registry.agents[0].followups[0];
  assert.equal(sent.role, 'user');
  assert.equal(sent.source.kind, 'user');
  assert.equal(sent.content[0].type, 'text');
  assert.equal(sent.content[0].text, expectedPrompt(request));
});

test('a hallucinated tool is denied by the guard and stays a recorded fact', async () => {
  const script = [
    { header: {} },
    { call: { callId: 'call-1', name: 'bash' } },
    { assistant: { text: '{"choice":"a"}' } },
    { turnEnd: { kind: 'completed' } },
  ];
  const { ctx, toolkit } = fixture(script);
  const result = await callReadOnly(ctx, readOnlyRequest(), new AbortController().signal);
  // The bridge reports facts only; the blackboard judges the forbidden name.
  assert.equal(result.status, 'ok');
  assert.equal(result.streamComplete, true);
  assert.equal(result.usage.toolCalls, 1);
  assert.deepEqual(result.nativeToolEvents.map((event) => [event.callId, event.toolName, event.phase]),
    [['call-1', 'bash', 'start'], ['call-1', 'bash', 'end']]);
  assert.deepEqual(toolkit.executed, []);
});

test('scoped registrations and PTC presentation cannot expand the surface', async () => {
  const scoped = fixture([
    { run: (world, agent) => world.toolkit.registerScoped(agent, { name: 'bash' }) },
    { header: {} },
    { assistant: { text: '{"choice":"a"}' } },
  ]);
  const scopedResult = await callReadOnly(scoped.ctx, readOnlyRequest(), new AbortController().signal);
  assert.equal(scopedResult.code, 'tool-surface-expanded');
  assert.equal(scopedResult.streamComplete, false);
  assert.equal(scopedResult.modelStarted, false);
  assert.ok(scoped.order.includes('cancel'));
  assert.equal(scoped.registry.disposeCount, 1);

  const ptc = fixture([
    { run: (world, agent) => world.toolkit.presentAsFor(agent, 'ptc') },
    { header: {} },
  ]);
  assert.equal((await callReadOnly(ptc.ctx, readOnlyRequest(), new AbortController().signal)).code,
    'tool-surface-expanded');

  // The same check runs at the publication commit boundary.
  const holder = {};
  const committed = fixture([], { beforeCommit: (agent) => holder.toolkit.registerScoped(agent, { name: 'bash' }) });
  holder.toolkit = committed.toolkit;
  const commitResult = await callReadOnly(committed.ctx, readOnlyRequest(), new AbortController().signal);
  assert.equal(commitResult.code, 'tool-surface-expanded');
  assert.deepEqual(committed.registry.agents, []);

  const missing = fixture([], { globals: ['read', 'glob', 'bash'] });
  assert.equal((await callReadOnly(missing.ctx, readOnlyRequest(), new AbortController().signal)).code,
    'read-only-tools-unavailable');
});

test('request headers must carry the requested config and exactly three tools', async () => {
  for (const header of [
    { config: { model: 'other-model' } },
    { config: { provider: 'other-provider' } },
    { config: { reasoningEffort: 'low' } },
    { config: { reasoningEffort: undefined } },
    { tools: [{ name: 'read' }] },
    { tools: [{ name: 'glob' }, { name: 'grep' }, { name: 'read' }, { name: 'run_code' }] },
    { tools: [] },
  ]) {
    const { ctx, order } = fixture([{ header }, { assistant: { text: 'x' } }]);
    const result = await callReadOnly(ctx, readOnlyRequest(), new AbortController().signal);
    assert.equal(result.code, 'configuration-mismatch', JSON.stringify(header));
    assert.equal(result.modelStarted, true);
    assert.equal(result.streamComplete, false);
    // The refusal is synchronous: cancel lands inside the header append.
    assert.equal(order[order.indexOf('request/header') + 1], 'cancel');
  }
  const mangled = fixture([], { mangleOptions: (options) => ({ ...options, model: 'other' }) });
  const optionsResult = await callReadOnly(mangled.ctx, readOnlyRequest(), new AbortController().signal);
  assert.equal(optionsResult.code, 'configuration-mismatch');
  assert.equal(optionsResult.modelStarted, false);
  assert.deepEqual(mangled.registry.agents[0].followups, []);
  assert.equal(mangled.registry.disposeCount, 1);

  const identity = fixture([], { breakIdentity: true });
  assert.equal((await callReadOnly(identity.ctx, readOnlyRequest(), new AbortController().signal)).code,
    'invalid-native-result');
});

test('missing identities, unpaired results and unsettled calls leave the stream incomplete', async () => {
  const noCallId = fixture([
    { header: {} },
    { event: { type: 'tool/call', data: { turn: 1, step: 1, name: 'read' } } },
    { assistant: { text: '{"choice":"a"}' } },
    { turnEnd: { kind: 'completed' } },
  ]);
  const missing = await callReadOnly(noCallId.ctx, readOnlyRequest(), new AbortController().signal);
  assert.equal(missing.code, 'stream-incomplete');
  assert.equal(missing.status, 'error');
  assert.equal(missing.nativeToolEvents.length, 1);
  assert.equal(missing.nativeToolEvents[0].toolName, 'read');
  assert.equal(missing.nativeToolEvents[0].callId, undefined);
  assert.equal(missing.nativeToolEvents[0].phase, 'start');

  const ghost = fixture([
    { header: {} },
    { event: { type: 'tool/result', data: { turn: 1, step: 1,
      message: { id: 'r', role: 'user', content: [{ type: 'tool-result', toolCallId: 'ghost' }],
        source: { kind: 'tool', callId: 'ghost' } } } } },
    { assistant: { text: '{"choice":"a"}' } },
    { turnEnd: { kind: 'completed' } },
  ]);
  const ghosted = await callReadOnly(ghost.ctx, readOnlyRequest(), new AbortController().signal);
  assert.equal(ghosted.code, 'stream-incomplete');
  assert.equal(ghosted.nativeToolEvents[0].type, 'tool/result');

  const unsettled = fixture([
    { header: {} },
    { call: { callId: 'call-1', name: 'read', noResult: true } },
    { assistant: { text: '{"choice":"a"}' } },
    { turnEnd: { kind: 'completed' } },
  ]);
  const open = await callReadOnly(unsettled.ctx, readOnlyRequest(), new AbortController().signal);
  assert.equal(open.code, 'stream-incomplete');
  assert.deepEqual(open.nativeToolEvents.map((event) => event.phase), ['start']);
});

test('broken streams are never reported as complete', async () => {
  const broken = fixture([{ header: {} }, { assistant: { text: '{"choice":"a"}' } }], { noTurnEnd: true });
  const idle = await callReadOnly(broken.ctx, readOnlyRequest(), new AbortController().signal);
  assert.equal(idle.code, 'stream-incomplete');
  assert.equal(idle.nativeTurnEnd, undefined);

  const failed = fixture([{ header: {} }, { assistant: { text: 'x' } },
    { turnEnd: { kind: 'error', error: { message: 'boom', code: 'UNKNOWN' } } }]);
  const errored = await callReadOnly(failed.ctx, readOnlyRequest(), new AbortController().signal);
  assert.equal(errored.code, 'native-turn-failed');
  assert.equal(errored.nativeTurnEnd, 'error');
  assert.equal(errored.streamComplete, false);

  const silent = fixture([{ header: {} }, { turnEnd: { kind: 'completed' } }]);
  assert.equal((await callReadOnly(silent.ctx, readOnlyRequest(), new AbortController().signal)).code,
    'native-turn-failed');
});

test('foreign, sub-agent, old-turn, unknown and late facts are retained', async () => {
  const foreign = fixture([
    { header: {} },
    { event: { session: null, type: 'tool/call', data: { turn: 1, step: 1, callId: 'sub-1', name: 'bash' } } },
    { assistant: { text: '{"choice":"a"}' } },
    { turnEnd: { kind: 'completed' } },
  ]);
  foreign.sessions.create('sub-agent-session');
  foreign.script[1].event.session = foreign.sessions.get('sub-agent-session');
  const foreignResult = await callReadOnly(foreign.ctx, readOnlyRequest(), new AbortController().signal);
  assert.equal(foreignResult.code, 'stream-incomplete');
  assert.deepEqual(foreignResult.nativeToolEvents[0],
    { nativeIdentity: { sessionId: 'sub-agent-session' }, callId: 'sub-1', toolName: 'bash', phase: 'start' });

  const old = fixture([
    { header: {} },
    { event: { type: 'tool/call', data: { turn: 99, step: 1, callId: 'old-1', name: 'read' } } },
    { assistant: { text: '{"choice":"a"}' } },
    { turnEnd: { kind: 'completed' } },
  ]);
  const oldResult = await callReadOnly(old.ctx, readOnlyRequest(), new AbortController().signal);
  assert.equal(oldResult.code, 'stream-incomplete');
  assert.equal(oldResult.nativeToolEvents[0].nativeIdentity.sessionId, ROOT_ID);

  const unknown = fixture([
    { header: {} },
    { event: { type: 'future/telemetry', data: { odd: true } } },
    { assistant: { text: '{"choice":"a"}' } },
    { turnEnd: { kind: 'completed' } },
  ]);
  const unknownResult = await callReadOnly(unknown.ctx, readOnlyRequest(), new AbortController().signal);
  assert.equal(unknownResult.code, 'stream-incomplete');
  assert.deepEqual(unknownResult.nativeToolEvents[0],
    { nativeIdentity: { sessionId: ROOT_ID }, type: 'future/telemetry' });

  const late = fixture([
    { header: {} },
    { assistant: { text: '{"choice":"a"}' } },
    { turnEnd: { kind: 'completed' } },
    { call: { callId: 'late-1', name: 'read' } },
  ]);
  const lateResult = await callReadOnly(late.ctx, readOnlyRequest(), new AbortController().signal);
  assert.equal(lateResult.code, 'stream-incomplete');
  assert.ok(lateResult.nativeToolEvents.some((event) => event.callId === 'late-1'));

  const intruder = fixture([
    { header: {} },
    { event: { type: 'user/message', data: { id: 'intruder', role: 'user', content: [],
      source: { kind: 'user' } } } },
    { assistant: { text: '{"choice":"a"}' } },
    { turnEnd: { kind: 'completed' } },
  ]);
  assert.equal((await callReadOnly(intruder.ctx, readOnlyRequest(), new AbortController().signal)).code,
    'stream-incomplete');

  const extraTurn = fixture([
    { header: {} },
    { event: { type: 'turn/start', data: { turn: 2 } } },
    { assistant: { text: '{"choice":"a"}' } },
    { turnEnd: { kind: 'completed' } },
  ]);
  assert.equal((await callReadOnly(extraTurn.ctx, readOnlyRequest(), new AbortController().signal)).code,
    'stream-incomplete');
});

test('the N+1st new tool call cancels the turn synchronously', async () => {
  const script = [{ header: {} },
    { call: { callId: 'call-1', name: 'read' } },
    { call: { callId: 'call-2', name: 'read' } },
    { call: { callId: 'call-3', name: 'read' } }];
  const { ctx, order, registry } = fixture(script);
  const result = await callReadOnly(ctx, readOnlyRequest({ budget: { timeoutSeconds: 60, toolCalls: 2 } }),
    new AbortController().signal);
  assert.equal(result.code, 'tool-budget-exhausted');
  assert.equal(result.modelStarted, true);
  assert.equal(result.streamComplete, false);
  assert.equal(result.usage.toolCalls, 3);
  assert.ok(result.nativeToolEvents.some((event) => event.callId === 'call-3'));
  assert.equal(order[order.indexOf('tool/call:call-3') + 1], 'cancel');
  assert.ok(order.includes('turn/end'));
  assert.equal(registry.disposeCount, 1);
});

test('external aborts and the internal deadline cancel and dispose for real', async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const aborted = fixture([{ header: {} }, { wait: gate }, { assistant: { text: 'x' } }]);
  const controller = new AbortController();
  const running = callReadOnly(aborted.ctx, readOnlyRequest(), controller.signal);
  await new Promise((resolve) => setTimeout(resolve, 10));
  controller.abort();
  release();
  const result = await running;
  assert.equal(result.code, 'deadline');
  assert.equal(result.streamComplete, false);
  assert.ok(aborted.order.includes('cancel'));
  assert.equal(aborted.registry.disposeCount, 1);
  assert.ok(aborted.registry.agents[0].disposed);

  const sleeping = fixture([{ header: {} }, { wait: new Promise((resolve) => setTimeout(resolve, 1_400)) }]);
  const deadline = await callReadOnly(sleeping.ctx,
    readOnlyRequest({ budget: { timeoutSeconds: 1, toolCalls: 8 } }), new AbortController().signal);
  assert.equal(deadline.code, 'deadline');
  assert.equal(sleeping.registry.disposeCount, 1);
});

test('flush failures and unconfirmed disposal are reported, never passed off as stopped', async () => {
  for (const behavior of [{ flushThrows: true }, { flushResult: false }]) {
    const { ctx, order, registry } = fixture([
      { header: {} }, { assistant: { text: '{"choice":"a"}' } }, { turnEnd: { kind: 'completed' } },
    ], behavior);
    const result = await callReadOnly(ctx, readOnlyRequest(), new AbortController().signal);
    assert.equal(result.code, 'flush-failed', JSON.stringify(behavior));
    assert.equal(result.streamComplete, false);
    assert.ok(order.indexOf('flush') < order.indexOf('dispose'));
    assert.equal(registry.disposeCount, 1);
  }
  const undisposed = fixture([
    { header: {} }, { assistant: { text: '{"choice":"a"}' } }, { turnEnd: { kind: 'completed' } },
  ], { disposeThrows: true });
  const unknown = await callReadOnly(undisposed.ctx, readOnlyRequest(), new AbortController().signal);
  assert.equal(unknown.code, 'stop-unknown');
  assert.equal(unknown.status, 'error');
  assert.equal(unknown.streamComplete, false);
  assert.equal(unknown.rawAnswer, undefined);
});

test('event retention stops at 128 start/end facts and marks the package truncated', async () => {
  const script = [{ header: {} }];
  for (let index = 0; index < 130; index += 1) script.push({ call: { callId: `call-${index}`, name: 'read' } });
  script.push({ assistant: { text: '{"choice":"a"}' } }, { turnEnd: { kind: 'completed' } });
  const { ctx } = fixture(script);
  const result = await callReadOnly(ctx, readOnlyRequest({ budget: { timeoutSeconds: 60, toolCalls: 200 } }),
    new AbortController().signal);
  assert.equal(result.code, 'stream-incomplete');
  assert.equal(result.nativeToolEvents.length, 128);
  assert.equal(result.nativeToolEventsTruncated, true);
  assert.equal(result.usage.toolCalls, 130);
  assert.equal(result.streamComplete, false);
});

test('the request-file driver only runs with an explicit configuration', async () => {
  assert.equal(apply({}, {}), undefined);
  assert.equal(apply({}, null), undefined);
  assert.equal(apply(null, undefined), undefined);
  assert.equal(apply({}, { requestFile: '/nonexistent' }), undefined);
});

test('format correction can spend zero remaining tools without resetting the budget', async () => {
  const { ctx } = fixture([{ header: {} }, { assistant: { text: '{"choice":"a"}' } },
                          { turnEnd: { kind: 'completed' } }]);
  const result = await callReadOnly(ctx, readOnlyRequest({ budget: { timeoutSeconds: 60, toolCalls: 0 } }),
                                  new AbortController().signal);
  assert.equal(result.status, 'ok');
  assert.equal(result.usage.toolCalls, 0);
  assert.equal(result.streamComplete, true);
});
