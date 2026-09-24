/** Native-event contract tests: no installed harness, model, or service is started. */
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { apply } from '../plugins/turn-result.mjs';
import { sha256 } from '../scripts/lib/turn-contract.mjs';

const prompt = 'Implement the delegated task.\n';
const outcome = { disposition: 'assistance', summary: 'Prepared the interface.', remaining: ['Integrate the helper.'], decisions: [], artifacts: [{ location: 'src/api.ts', kind: 'file' }], request: { summary: 'Need a fixture.', attempted: 'Checked the existing tests.', neededWork: 'Create the fixture in the assigned workspace.', expectedArtifacts: ['fixture.json'], acceptance: 'It must satisfy the input schema.' } };

function harness(t, overrides = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'buddy-turn-plugin-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const input = { version: 1, taskId: 'task-1', attemptId: 'attempt-1', generation: 1, turnId: 'turn-1', resumeMode: 'initial', previousSessionId: null, context: { nextActions: ['Use pinned fixture.'] }, executionWorkspace: { path: dir }, ...overrides };
  const config = { cwd: dir, input, inputSha256: sha256(JSON.stringify(input)), promptSha256: sha256(prompt), outputFile: join(dir, 'result.json') };
  const agents = new Map();
  const listeners = new Map();
  const on = (name, fn) => { listeners.set(name, [...listeners.get(name) ?? [], fn]); return () => {}; };
  const emit = (name, ...args) => { for (const fn of listeners.get(name) ?? []) fn(...args); };
  const flush = async (session) => { await Promise.all((listeners.get('session/flush') ?? []).map((fn) => Promise.resolve().then(() => fn(session)))); };
  const ctx = {
    agents: { get: (id) => agents.get(id), roots: () => [...agents.values()].filter((a) => !a.parent) },
    tools: { register() { throw new Error('Global registration is forbidden'); } }, on,
  };
  apply(ctx, config);
  const agent = (id, { header = {}, parent = null } = {}) => {
    const tools = new Map();
    const guards = [];
    const sections = [];
    const session = { id, header: { id, cwd: dir, ...header }, seq: 0 };
    const value = { id, session, status: 'idle', parent, tools, guards, sections,
      ctx: {
        tools: { register(definition) { tools.set(definition.name, definition); return () => tools.delete(definition.name); }, guard(fn) { guards.push(fn); return () => {}; } },
        systemPrompt: { section(section) { sections.push(section); }, getSectionOrder() { return 1000; } },
        on(name, fn) { return on(name, (...args) => { if (name === 'tools/result' && args[0]?.agent !== value) return; return fn(...args); }); },
      },
    };
    agents.set(id, value);
    return value;
  };
  const append = (a, type, data, extra = {}) => {
    const event = { type, data, seq: a.session.seq++, time: 1, ...extra };
    emit('session/event', a.session, event);
    return event;
  };
  const user = (text = prompt, source = { kind: 'user' }) => ({ role: 'user', id: `message-${Math.random()}`, content: [{ type: 'text', text }], source });
  const insert = (a, message = user()) => { emit('agent/inbox/inserted', { agent: a, message }); return message; };
  const start = (a, message = insert(a)) => { a.status = 'running'; append(a, 'turn/start', { turn: 1 }); append(a, 'step/start', { turn: 1, step: 1 }); append(a, 'user/message', message); };
  const execution = (a, options = {}) => ({ agent: a, name: 'buddy_finish_turn', callId: 'call-1', rootCallId: 'call-1', token: Symbol(), signal: new AbortController().signal, concluded: false, concludeTurn() { this.concluded = true; }, ...options });
  const call = (a, { id = 'call-1', name = 'buddy_finish_turn' } = {}) => append(a, 'tool/call', { turn: 1, step: 1, callId: id, name, arguments: '{}' });
  const accepted = (exec, result = { isError: false, value: { recorded: true }, concludesTurn: true }) => emit('tools/result', exec, result);
  const committed = (a, event, { isError = false, id = event.data.callId, turn = 1, sourceSeq = event.seq } = {}) => append(a, 'tool/result', { turn, step: 1, message: { role: 'user', source: { kind: 'tool', callId: id }, content: [{ type: 'tool-result', toolCallId: id, isError, content: [] }] } }, { sourceEventSeqs: [sourceSeq] });
  const finish = (a, reason = 'completed') => { append(a, 'step/end', { turn: 1, step: 1 }); append(a, 'turn/end', { turn: 1, reason: { kind: reason } }); a.status = 'idle'; };
  const read = () => JSON.parse(readFileSync(config.outputFile, 'utf8'));
  return { dir, config, ctx, agent, append, emit, flush, user, insert, start, execution, call, accepted, committed, finish, read };
}

test('root tool exists before first assembly, then writes only after accepted commit, completed end and awaited flush', async (t) => {
  const h = harness(t);
  const a = h.agent('root');
  const message = h.insert(a);
  const definition = a.tools.get('buddy_finish_turn');
  assert.ok(definition, 'inbox insertion registers before the driver starts');
  assert.match(a.sections[0].text, /Use pinned fixture/);
  await assert.rejects(() => definition.execute(outcome, h.execution(a)), /committed first root/);
  h.start(a, message);
  const call = h.call(a);
  const exec = h.execution(a);
  assert.deepEqual(await definition.execute(outcome, exec), { recorded: true });
  assert.equal(exec.concluded, true);
  assert.equal(existsSync(h.config.outputFile), false, 'the tool body is not acceptance');
  h.accepted(exec);
  assert.match(a.guards[0](h.execution(a, { name: 'bash' })), /later tools/);
  await h.flush(a.session);
  assert.equal(existsSync(h.config.outputFile), false, 'running per-request flush does not publish');
  h.committed(a, call);
  h.finish(a);
  assert.equal(existsSync(h.config.outputFile), false, 'turn/end alone is not persistence');
  await h.flush(a.session);
  const record = h.read();
  assert.deepEqual(record.outcome, outcome);
  assert.equal(record.taskId, h.config.input.taskId);
  assert.equal(record.sessionId, 'root');
  assert.equal(record.provenance.toolResultSeq, 4);
  assert.equal(record.provenance.flushSeq, 7);
  await h.flush(a.session);
  assert.deepEqual(h.read(), record, 'identical repeated flush is harmless');
  assert.deepEqual(readdirSync(h.dir), ['result.json']);
});

test('children, unrelated roots, mismatching first prompts and plugin input cannot gain the terminal tool', async (t) => {
  const h = harness(t);
  const a = h.agent('root');
  h.start(a);
  const definition = a.tools.get('buddy_finish_turn');
  const candidates = [
    h.agent('fork', { header: { parentSession: 'root' } }),
    h.agent('subagent', { header: { origin: 'subagent' } }),
    h.agent('deep', { header: { delegationDepth: 1 } }),
    h.agent('owned', { parent: a }),
    h.agent('other-cwd', { header: { cwd: `${h.dir}/other` } }),
  ];
  for (const child of candidates) {
    h.start(child);
    assert.equal(child.tools.size, 0);
    await assert.rejects(() => definition.execute(outcome, h.execution(child)), /committed first root/);
    assert.equal(a.guards[0](h.execution(child, { name: 'bash' })), undefined, 'internal child work remains allowed');
  }
  const wrong = h.agent('wrong');
  h.insert(wrong, h.user('wrong initial prompt'));
  h.insert(wrong);
  assert.equal(wrong.tools.size, 0);
  const plugin = h.agent('plugin');
  h.insert(plugin, h.user(prompt, { kind: 'plugin', plugin: 'other' }));
  assert.equal(plugin.tools.size, 0);
  assert.equal(existsSync(h.config.outputFile), false);
});

test('later matching ordinary message cannot replace a mismatched first committed prompt', async (t) => {
  const h = harness(t);
  const a = h.agent('root');
  h.insert(a);
  h.start(a, h.user('different committed first prompt'));
  h.append(a, 'user/message', h.user());
  await assert.rejects(() => a.tools.get('buddy_finish_turn').execute(outcome, h.execution(a)), /committed first root/);
});

test('a non-text first ordinary message still prevents later prompt matching', (t) => {
  const h = harness(t);
  const a = h.agent('root');
  h.insert(a, { role: 'user', source: { kind: 'user' }, content: [{ type: 'image', source: 'reference' }] });
  h.insert(a);
  assert.equal(a.tools.size, 0);
});

test('cancellation and a missing concludeTurn API cannot produce an accepted result', async (t) => {
  const h = harness(t);
  const a = h.agent('root');
  h.start(a);
  h.call(a);
  const controller = new AbortController();
  controller.abort(new Error('cancelled'));
  await assert.rejects(() => a.tools.get('buddy_finish_turn').execute(outcome, h.execution(a, { signal: controller.signal })), /cancelled/);
  await assert.rejects(() => a.tools.get('buddy_finish_turn').execute(outcome, h.execution(a, { concludeTurn: undefined })), /concludeTurn API/);
  h.finish(a);
  await assert.rejects(() => h.flush(a.session), /without an accepted/);
});

test('a second matching root invalidates binding instead of selecting one', async (t) => {
  const h = harness(t);
  const a = h.agent('root');
  h.start(a);
  h.start(h.agent('ambiguous'));
  h.finish(a);
  await assert.rejects(() => h.flush(a.session), /Ambiguous/);
  assert.equal(existsSync(h.config.outputFile), false);
});

test('execution identity prevents stale or fabricated result events from accepting a staged call', async (t) => {
  const h = harness(t);
  const a = h.agent('root');
  h.start(a);
  const call = h.call(a);
  const exec = h.execution(a);
  await a.tools.get('buddy_finish_turn').execute(outcome, exec);
  h.accepted(h.execution(a)); // identical call ID, distinct pipeline execution
  h.committed(a, call);
  h.finish(a);
  await assert.rejects(() => h.flush(a.session), /without an accepted/);
  assert.equal(existsSync(h.config.outputFile), false);
});

for (const result of [{ isError: true }, { isError: false, value: { recorded: true } }, { isError: false, value: { recorded: false }, concludesTurn: true }]) {
  test(`pipeline refusal cannot conclude Buddy: ${JSON.stringify(result)}`, async (t) => {
    const h = harness(t);
    const a = h.agent('root');
    h.start(a);
    const call = h.call(a);
    const exec = h.execution(a);
    await a.tools.get('buddy_finish_turn').execute(outcome, exec);
    h.accepted(exec, result);
    h.committed(a, call);
    h.finish(a);
    await assert.rejects(() => h.flush(a.session), /without an accepted/);
  });
}

for (const failOuter of [false, true]) {
  test(`PTC capture waits for the exact enclosing result (${failOuter ? 'rejected' : 'accepted'})`, async (t) => {
    const h = harness(t, { resumeMode: 'reconstructed-new-session', previousSessionId: 'previous-root' });
    const a = h.agent('new-root');
    h.start(a);
    const call = h.call(a, { name: 'run_code' });
    const outer = h.execution(a, { name: 'run_code' });
    const nested = h.execution(a, { callId: 'call-1:ptc:1', parent: outer.token });
    await a.tools.get('buddy_finish_turn').execute(outcome, nested);
    h.accepted(nested);
    assert.match(a.guards[0](h.execution(a, { name: 'bash' })), /already accepted/);
    h.append(a, 'tool/ptc-dispatch', { rootCallId: 'call-1', subCallId: nested.callId, parentCallId: 'call-1', name: 'buddy_finish_turn', isError: false });
    h.accepted(outer, { isError: failOuter, value: {}, concludesTurn: !failOuter });
    h.committed(a, call, { isError: failOuter });
    h.finish(a);
    if (failOuter) {
      await assert.rejects(() => h.flush(a.session), /without an accepted/);
      assert.equal(a.guards[0](h.execution(a, { name: 'bash' })), undefined, 'a refused outer result releases the capture guard');
    } else {
      await h.flush(a.session);
      assert.equal(h.read().provenance.ptcDispatchSeq, 4);
      assert.equal(h.read().resumeMode, 'reconstructed-new-session');
      assert.equal(h.read().previousSessionId, 'previous-root');
    }
  });
}

for (const failure of ['missing-commit', 'wrong-call-seq', 'aborted', 'write-failure']) {
  test(`accepted tool does not hide ${failure}`, async (t) => {
    const h = harness(t);
    const a = h.agent('root');
    h.start(a);
    const call = h.call(a);
    const exec = h.execution(a);
    await a.tools.get('buddy_finish_turn').execute(outcome, exec);
    h.accepted(exec);
    if (failure !== 'missing-commit') h.committed(a, call, failure === 'wrong-call-seq' ? { sourceSeq: 999 } : {});
    h.finish(a, failure === 'aborted' ? 'aborted' : 'completed');
    if (failure === 'write-failure') writeFileSync(h.config.outputFile, 'existing receipt', { mode: 0o600 });
    await assert.rejects(() => h.flush(a.session));
    if (failure === 'write-failure') assert.equal(readFileSync(h.config.outputFile, 'utf8'), 'existing receipt');
    else assert.equal(existsSync(h.config.outputFile), false);
  });
}
