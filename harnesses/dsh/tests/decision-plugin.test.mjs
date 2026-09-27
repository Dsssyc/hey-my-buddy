/**
 * Focused tests for the in-process decision plugin and its prompt assembly.
 *
 * These tests import the production plugin directly and drive it with a fake
 * Cordis context that mimics the native `llm` service contract. No harness is
 * booted, no credential is read, and no model is called: the fake records the
 * exact `prepareCall` config and `stream` options the plugin composes, so the
 * no-tools, non-thinking, and parameter-mapping guarantees are assertions
 * rather than assumptions.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { describe, test } from 'node:test';

import {
  BOUNDS, MAX_ANSWER_BYTES, MAX_DIAGNOSTIC_FAILURES, MAX_DIAGNOSTIC_TEXT_BYTES, MAX_OUTPUT_TOKENS,
  RETRYABLE_ANSWER_CODES,
  boundedAnswerDiagnostic, callDecisionModel, checkAgentRunnerDisabled, extractJsonValue, finishProblem,
  isRetryableAnswerCode, legalCandidateIds,
  normalizeCandidates, normalizeEvidence, readRequest, resolveAnswer,
  validateSelectAnswer, waitForRoute, writeJsonFile,
} from '../plugins/decision.mjs';
import {
  CORRECTION_NOTES, MAX_PAYLOAD_BYTES, PROMPT_VERSION, buildPayload, correctionMessage, instructionsFor, renderUserTurn,
} from '../scripts/decision-prompt.mjs';
import {
  POLICY_ALTERNATIVE_UNSUPPORTED, POLICY_CHECK_SHAPE, POLICY_FACTS_MISMATCH,
  POLICY_SUPPORT_UNKNOWN,
  derivePolicyFacts, expectedPolicyCheck, validateDecision,
} from '../scripts/selection-policy.mjs';

/** A fake `ctx.llm` recording every call it receives. */
function fakeLlm({ chunks, chunkSets, prepareConfig, providers = [{ id: 'deepseek-official', name: 'DeepSeek' }], failPrepare } = {}) {
  const calls = { prepare: [], stream: [], listed: 0 };
  /** The chunks for one stream: a literal list, a per-call list, or a factory. */
  const chunksFor = (index, options) => {
    const set = chunkSets === undefined ? chunks : chunkSets[index];
    return typeof set === 'function' ? set(options) : set;
  };
  const llm = {
    listProviders() {
      calls.listed += 1;
      return providers;
    },
    async prepareCall(config, signal) {
      calls.prepare.push({ config: structuredClone(config), hadSignal: signal !== undefined });
      if (failPrepare !== undefined) throw failPrepare;
      const resolved = prepareConfig === undefined
        ? { provider: config.provider, model: config.model, ...(config.reasoningEffort === undefined ? {} : { reasoningEffort: config.reasoningEffort }), ...(config.maxTokens === undefined ? {} : { maxTokens: config.maxTokens }) }
        : structuredClone(prepareConfig(config));
      return {
        config: resolved,
        stream(options) {
          const index = calls.stream.length;
          calls.stream.push({ options, hadSignal: options.signal !== undefined });
          return (async function* generate() {
            const produced = chunksFor(index, options);
            for await (const chunk of produced ?? []) yield chunk;
          })();
        },
      };
    },
  };
  return { llm, calls };
}

/** An async chunk source that yields nothing until its call is aborted. */
function hangUntilAborted(options) {
  return (async function* generate() {
    await new Promise((resolve) => {
      if (options.signal.aborted) resolve();
      else options.signal.addEventListener('abort', resolve, { once: true });
    });
    yield { type: 'finish', reason: { kind: 'aborted' } };
  })();
}

/** A chunk list carrying one visible answer and a clean finish. */
function answerChunks(text) {
  return [{ type: 'text-delta', index: 0, text }, { type: 'finish', reason: { kind: 'stop' } }];
}

/** The minimal normalized request the plugin works from. */
function pluginRequest(overrides = {}) {
  const profiles = [
    { profileId: 'p1', available: true, enabled: true, evidenceIds: ['e1'] },
    { profileId: 'p2', available: false, enabled: true, evidenceIds: [] },
    { profileId: 'p3', available: true, enabled: false, evidenceIds: [] },
  ];
  const evidence = [{ evidenceId: 'e2', profileId: 'p1' }];
  const candidates = normalizeCandidates(profiles).candidates;
  const evidenceIndex = normalizeEvidence(evidence).evidence;
  const routingPreferences = [];
  const preferences = [];
  const base = {
    operation: 'select',
    requestId: 'req-1',
    profile: { provider: 'deepseek-official', model: 'deepseek-flash', effort: 'off' },
    tableRevision: 4,
    task: 'choose a profile',
    profiles,
    cards: [],
    preferences,
    evidence,
    annotations: [],
    routingPreferences,
    candidates,
    evidenceIndex,
  };
  return {
    ...base,
    policyFacts: derivePolicyFacts({ profiles, preferences, routingPreferences, hardConstraints: {} }),
    ...overrides,
  };
}

const EMPTY_SUPPORT = { cardProfileIds: [], annotationProfileIds: [] };

/** A complete strict-shape answer whose policy check is derived, not invented. */
function statedAnswer(request, profileId, overrides = {}) {
  const check = expectedPolicyCheck(request.policyFacts, request.routingPreferences ?? [], profileId);
  return {
    profileId,
    reason: 'grounded in the supplied table',
    evidenceIds: [],
    policyCheck: check,
    support: EMPTY_SUPPORT,
    ...overrides,
  };
}

const TEXT_ANSWER = JSON.stringify(statedAnswer(pluginRequest(), 'p1', { evidenceIds: ['e1'] }));

describe('prompt assembly', () => {
  test('the instruction prefix is versioned, selection-only and request-free', () => {
    assert.equal(PROMPT_VERSION, 8);
    const select = instructionsFor('select');
    assert.equal(instructionsFor('select'), select, 'the prefix must be stable');
    assert.doesNotMatch(select, /req-1/);
    assert.doesNotMatch(select, /tableRevision/);
    assert.match(select, /Return exactly one JSON object/);
    assert.match(select, /"profileId": <string\|null>, "reason": <string>, "evidenceIds": <string\[\]>, "support":/);
    assert.doesNotMatch(select, /"policyCheck": <object/, 'the model no longer states the policy check');
    assert.doesNotMatch(select, /"policyCheck": null/, 'even the abstention example omits the model check');
    assert.match(select, /The routing-policy acknowledgment is not part of your answer/);
    assert.match(select, /ignored redundant data/);
    assert.match(select, /missing card or annotation means unknown capability evidence/);
    assert.match(select, /adapter names the harness that will run the Buddy, not the codebase or files it may edit/);
    assert.match(select, /Working on the source, scripts, or tests of DSH, ZCode, Codex or another harness does not require running on that harness/);
    assert.match(select, /Task routing preferences are POSITIVE/);
    assert.match(select, /never an avoid or exclude instruction/);
    assert.match(select, /does not prove live quota/);
    assert.match(select, /prefer economical, user-supported configurations over premium ones/);
    assert.doesNotMatch(select, /deepseek-flash/);
    assert.doesNotMatch(select, /deepseek-official/);
    assert.throws(() => instructionsFor('maintain'), /unknown decision operation/);
    assert.throws(() => instructionsFor('delete'), /unknown decision operation/);
  });

  test('the correction note is static, request-free and keyed by the stable code', () => {
    const note = correctionMessage('answer-shape');
    const parsed = JSON.parse(note);
    assert.deepEqual(Object.keys(parsed), ['correction']);
    assert.equal(parsed.correction.code, 'answer-shape');
    assert.equal(parsed.correction.instruction, CORRECTION_NOTES['answer-shape']);
    assert.doesNotMatch(note, /req-1/);
    assert.doesNotMatch(note, /deepseek-flash/);
    for (const code of RETRYABLE_ANSWER_CODES) {
      const message = correctionMessage(code);
      assert.ok(Buffer.byteLength(message, 'utf8') <= 512, `${code} correction is oversized`);
      assert.doesNotMatch(message, /[^\x00-\x7f]/u, `${code} correction must stay printable ASCII`);
    }
    assert.equal(JSON.parse(correctionMessage('answer-unknown-code')).correction.code, 'answer-unknown-code');
  });

  test('the shared table precedes per-request data and requestId comes last', () => {
    const request = { tableRevision: 1, task: 't', operation: 'select', requestId: 'r', profile: {}, profiles: [], cards: [], preferences: [], annotations: [], evidence: [], routingPreferences: [], policyFacts: { hardConstraints: {}, taskPreference: { ruleIndex: null, matchingProfileIds: [] }, userPreferredProfileIds: [] } };
    assert.deepEqual(Object.keys(buildPayload('select', request)), ['operation', 'profile', 'profiles', 'cards', 'preferences', 'annotations', 'evidence', 'tableRevision', 'routingPreferences', 'policyFacts', 'task', 'requestId']);
  });

  test('two requests against one table revision share the whole table prefix', () => {
    const table = {
      profile: { provider: 'deepseek-official', model: 'deepseek-flash', effort: 'off' },
      tableRevision: 5,
      profiles: [{ profileId: 'p1' }, { profileId: 'p2' }],
      cards: [{ profileId: 'p1', summary: 'current' }],
      preferences: [{ profileId: 'p1', mode: 'prefer' }],
      evidence: [{ evidenceId: 'e1', profileId: 'p1' }],
    };
    const first = renderUserTurn('select', { operation: 'select', ...table, task: 'first task', requestId: 'req-1' });
    const second = renderUserTurn('select', { operation: 'select', ...table, task: 'second task', requestId: 'req-2' });
    // The exact bytes every request against this revision shares: everything up
    // to and including the current table, before the per-request tail.
    const revisionPrefix = JSON.stringify(buildPayload('select', { operation: 'select', ...table })).slice(0, -1);
    assert.ok(first.startsWith(revisionPrefix), first);
    assert.ok(second.startsWith(revisionPrefix), second);
    // A new revision with unchanged table contents must not invalidate the
    // expensive table prefix merely because its metadata counter advanced.
    const republished = renderUserTurn('select', { operation: 'select', ...table, tableRevision: 6, task: 'third task', requestId: 'req-3' });
    assert.equal(first.split(',"tableRevision":')[0], republished.split(',"tableRevision":')[0]);
    assert.ok(revisionPrefix.includes('"evidence":[{"evidenceId":"e1","profileId":"p1"}]'), revisionPrefix);
    // Everything that varies per request lives after the shared prefix.
    for (const [text, task, requestId] of [[first, 'first task', 'req-1'], [second, 'second task', 'req-2']]) {
      assert.ok(text.slice(revisionPrefix.length).includes(task), text.slice(revisionPrefix.length));
      assert.equal((text.match(new RegExp(requestId, 'g')) ?? []).length, 1);
      assert.ok(text.indexOf(requestId) > revisionPrefix.length, `${requestId} must not be inside the reusable prefix`);
    }
  });

  test('array order supplied by the owner is preserved, and undefined keys are dropped', () => {
    const payload = buildPayload('select', {
      operation: 'select', requestId: 'r', profile: {}, tableRevision: 1, task: 't',
      profiles: [{ profileId: 'z' }, { profileId: 'a' }], cards: undefined, preferences: [], evidence: [],
    });
    assert.deepEqual(payload.profiles.map((entry) => entry.profileId), ['z', 'a']);
    assert.equal('cards' in payload, false);
  });

  test('the rendered user turn is compact JSON of exactly the payload', () => {
    const request = pluginRequest();
    const text = renderUserTurn('select', request);
    assert.equal(text.includes('\n'), false);
    assert.deepEqual(JSON.parse(text), buildPayload('select', request));
    assert.equal(text, renderUserTurn('select', request), 'rendering must be deterministic');
  });

  test('an oversized payload is refused before any model call', () => {
    const request = pluginRequest({ task: 'x'.repeat(MAX_PAYLOAD_BYTES) });
    assert.throws(() => renderUserTurn('select', request), /above the 262144-byte bound/);
  });
});

describe('native llm call composition', () => {
  test('prepareCall receives exactly the decision profile and the call is tool-free', async () => {
    const { llm, calls } = fakeLlm({ chunks: [{ type: 'text-delta', index: 0, text: TEXT_ANSWER }, { type: 'finish', reason: { kind: 'stop' } }] });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.deepEqual(calls.prepare[0].config, { provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'off' });
    const options = calls.stream[0].options;
    assert.equal('tools' in options, false, 'the decision call must not offer tools');
    assert.equal(options.provider, 'deepseek-official');
    assert.equal(options.model, 'deepseek-flash');
    assert.equal(options.reasoningEffort, 'off', 'non-thinking effort must be preserved');
    // The options must carry the resolved config verbatim plus request fields:
    // changing a config field here would be refused by the prepared handle.
    assert.deepEqual(
      { provider: options.provider, model: options.model, reasoningEffort: options.reasoningEffort },
      { provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'off' },
    );
    assert.equal(options.temperature, calls.prepare[0].config.temperature);
    assert.equal(options.messages.length, 1);
    assert.equal(options.messages[0].role, 'user');
    assert.equal(options.messages[0].content.length, 1);
    assert.equal(options.messages[0].content[0].type, 'text');
    assert.equal(options.system, instructionsFor('select'));
    assert.deepEqual(JSON.parse(options.messages[0].content[0].text), buildPayload('select', pluginRequest()));
    assert.equal(calls.stream[0].hadSignal, true, 'the bounded call must carry an abort signal');
  });

  test('the call config is the resolved config plus only the bounded output request', async () => {
    const { llm, calls } = fakeLlm({
      prepareConfig: (config) => ({ provider: config.provider, model: config.model, reasoningEffort: config.reasoningEffort, temperature: 0, maxTokens: config.maxTokens ?? 999_999 }),
      chunks: [{ type: 'text-delta', index: 0, text: TEXT_ANSWER }, { type: 'finish', reason: { kind: 'stop' } }],
    });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(calls.prepare.length, 2, 'the adapter resolves the default once and the bounded config once');
    assert.equal(calls.prepare[0].config.maxTokens, undefined);
    assert.equal(calls.prepare[1].config.maxTokens, MAX_OUTPUT_TOKENS);
    assert.equal(calls.stream[0].options.maxTokens, MAX_OUTPUT_TOKENS);
  });

  test('an unsupported effort is refused before any provider call', async () => {
    const failPrepare = Object.assign(new Error('does not support reasoning effort "off"'), { code: 'UNSUPPORTED_REASONING_EFFORT' });
    const { llm, calls } = fakeLlm({ failPrepare });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.ok, false);
    assert.equal(result.code, 'decision-effort-not-supported');
    assert.equal(calls.stream.length, 0);
  });

  test('a missing provider route is refused without a model call', async () => {
    const { llm, calls } = fakeLlm({ providers: [] });
    await assert.rejects(
      () => waitForRoute({ llm }, { provider: 'absent', model: 'm', effort: 'off' }, new AbortController().signal),
      /no adapter registered/,
    );
    assert.equal(calls.stream.length, 0);
  });

  test('tool-call output from the model is refused, never executed', async () => {
    const { llm } = fakeLlm({
      chunks: [
        { type: 'tool-call-delta', index: 0, id: 'call-1', name: 'bash', argumentsDelta: '{}' },
        { type: 'text-delta', index: 0, text: TEXT_ANSWER },
        { type: 'finish', reason: { kind: 'stop' } },
      ],
    });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.ok, false);
    assert.equal(result.code, 'answer-tool-call');
  });

  test('truncated and oversized answers are refused', async () => {
    const truncated = fakeLlm({
      chunks: [{ type: 'text-delta', index: 0, text: '{"profileId":"p1"' }, { type: 'finish', reason: { kind: 'length' } }],
    });
    assert.equal((await callDecisionModel({ llm: truncated.llm }, pluginRequest(), { timeoutMs: 5_000 })).code, 'answer-truncated');
    const oversized = fakeLlm({
      chunks: [
        { type: 'text-delta', index: 0, text: 'x'.repeat(MAX_ANSWER_BYTES + 1) },
        { type: 'finish', reason: { kind: 'stop' } },
      ],
    });
    assert.equal((await callDecisionModel({ llm: oversized.llm }, pluginRequest(), { timeoutMs: 5_000 })).code, 'answer-too-large');
  });

  test('a provider failure becomes a structured code, never invented success', async () => {
    const { llm } = fakeLlm({
      chunks: [{ type: 'finish', reason: { kind: 'error', failure: { code: 'RATE_LIMIT', message: 'slow down', status: 429 } } }],
    });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.ok, false);
    assert.equal(result.code, 'call-failed');
    // Only the stable machine code crosses this boundary: provider messages can
    // echo endpoints, prompts or credentials into a persisted envelope.
    assert.deepEqual(result.detail, { code: 'RATE_LIMIT' });
  });

  test('an aborted call reports a timeout code', async () => {
    const { llm } = fakeLlm({ chunks: [{ type: 'finish', reason: { kind: 'aborted' } }] });
    assert.equal((await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 })).code, 'call-aborted');
  });

  test('a select answer is returned with resolved identity, never observed identity', async () => {
    const { llm } = fakeLlm({
      chunks: [
        { type: 'reasoning-delta', index: 0, text: 'thinking' },
        { type: 'text-delta', index: 0, text: TEXT_ANSWER },
        { type: 'usage', usage: { inputTokens: 3, outputTokens: 4, totalTokens: 7, reasoningTokens: 2 } },
        { type: 'finish', reason: { kind: 'stop' } },
      ],
    });
    const request = pluginRequest();
    const result = await callDecisionModel({ llm }, request, { timeoutMs: 5_000 });
    assert.equal(result.ok, true);
    assert.deepEqual(result.decision, {
      profileId: 'p1',
      reason: 'grounded in the supplied table',
      evidenceIds: ['e1'],
      policyCheck: expectedPolicyCheck(request.policyFacts, request.routingPreferences, 'p1'),
      support: EMPTY_SUPPORT,
    });
    assert.deepEqual(result.resolvedConfig, { provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'off' });
    assert.equal('observed' in result, false, 'the plugin must not claim observed identity');
    assert.deepEqual(result.usage, { inputTokens: 3, outputTokens: 4, totalTokens: 7, cacheReadTokens: null, cacheWriteTokens: null, reasoningTokens: 2 });
    assert.equal(result.reasoningBytes, 8);
  });

  test('a request whose policy facts disagree with its table is refused before any model call', async () => {
    const request = pluginRequest({ policyFacts: { hardConstraints: {}, taskPreference: { ruleIndex: null, matchingProfileIds: ['p1'] }, userPreferredProfileIds: ['p1'] } });
    const { llm, calls } = fakeLlm({ chunks: [{ type: 'finish', reason: { kind: 'stop' } }] });
    const result = await callDecisionModel({ llm }, request, { timeoutMs: 5_000 });
    assert.equal(result.ok, false);
    assert.equal(result.code, POLICY_FACTS_MISMATCH);
    assert.equal(calls.prepare.length, 0, 'no provider resolution may happen');
    assert.equal(calls.stream.length, 0, 'no model call may happen');
    assert.equal(calls.listed, 0, 'not even the route wait may start');
  });

  test('a model policyCheck is ignored and replaced by the program-computed check (R1)', async () => {
    // A preference rule that legally matches p1 (the model field is present).
    const flashRule = [{ match: { model: 'deepseek-flash' }, reason: 'Prefer the flash model' }];
    const withFlashModel = (overrides = {}) => {
      const profiles = [
        { profileId: 'p1', available: true, enabled: true, evidenceIds: ['e1'], model: 'deepseek-flash' },
        { profileId: 'p4', available: true, enabled: true, model: 'deepseek-pro' },
      ];
      const request = pluginRequest({
        profiles,
        candidates: normalizeCandidates(profiles).candidates,
        routingPreferences: flashRule,
        ...overrides,
      });
      request.policyFacts = derivePolicyFacts({ profiles: request.profiles, routingPreferences: request.routingPreferences, preferences: [], hardConstraints: {} });
      return request;
    };
    // The real failure shape: the model copies the input taskPreference,
    // including matchingProfileIds, and appends outcome.
    const echoed = withFlashModel();
    assert.deepEqual(echoed.policyFacts.taskPreference, { ruleIndex: 0, matchingProfileIds: ['p1'] });
    const echoedAnswer = JSON.stringify({
      profileId: 'p1',
      reason: 'echoing the supplied facts',
      evidenceIds: [],
      policyCheck: {
        hardConstraints: {},
        taskPreference: { ruleIndex: 0, matchingProfileIds: ['p1'], outcome: 'fallback' },
        userPreference: 'none',
      },
      support: { cardProfileIds: [], annotationProfileIds: [] },
    });
    const echoedResult = await callDecisionModel({ llm: fakeLlm({ chunks: answerChunks(echoedAnswer) }).llm }, echoed, { timeoutMs: 5_000 });
    assert.equal(echoedResult.ok, true, JSON.stringify(echoedResult));
    assert.deepEqual(Object.keys(echoedResult.decision), ['profileId', 'reason', 'evidenceIds', 'policyCheck', 'support']);
    assert.deepEqual(echoedResult.decision.policyCheck, expectedPolicyCheck(echoed.policyFacts, echoed.routingPreferences, 'p1'));
    assert.deepEqual(echoedResult.decision.policyCheck.taskPreference, { ruleIndex: 0, outcome: 'matched' });
    assert.equal(echoedResult.decision.policyCheck.hardConstraints.adapter, undefined);
    assert.equal(echoedResult.diagnostics.calls, 1, 'a tolerated echo is not a refusal');

    // An invented constraint, a wrong index, and a non-integer index are all
    // ignored the same way: the model cannot change the adopted check.
    for (const echo of [
      { hardConstraints: { adapter: 'dsh' }, taskPreference: { ruleIndex: 1, outcome: 'matched' }, userPreference: 'none' },
      { hardConstraints: {}, taskPreference: { ruleIndex: false, outcome: 'matched' }, userPreference: 'none' },
      { hardConstraints: {}, taskPreference: { ruleIndex: 0, outcome: 'alternative' }, userPreference: 'matched' },
    ]) {
      const answer = JSON.stringify({ profileId: 'p1', reason: 'r', evidenceIds: [], policyCheck: echo, support: { cardProfileIds: [], annotationProfileIds: [] } });
      const result = await callDecisionModel({ llm: fakeLlm({ chunks: answerChunks(answer) }).llm }, withFlashModel(), { timeoutMs: 5_000 });
      assert.equal(result.ok, true, JSON.stringify(result));
      assert.deepEqual(result.decision.policyCheck, expectedPolicyCheck(withFlashModel().policyFacts, flashRule, 'p1'));
    }

    // An abstention may carry a policyCheck even though the returned one is null.
    const abstain = pluginRequest();
    const abstainAnswer = JSON.stringify({
      profileId: null,
      reason: 'defer',
      evidenceIds: [],
      policyCheck: { hardConstraints: {}, taskPreference: { ruleIndex: null, outcome: 'none' }, userPreference: 'none' },
      support: { cardProfileIds: [], annotationProfileIds: [] },
    });
    const abstainResult = await callDecisionModel({ llm: fakeLlm({ chunks: answerChunks(abstainAnswer) }).llm }, abstain, { timeoutMs: 5_000 });
    assert.equal(abstainResult.ok, true, JSON.stringify(abstainResult));
    assert.equal(abstainResult.decision.policyCheck, null);
  });

  test('a single candidate reports none, matched or fallback from the program facts', async () => {
    const single = pluginRequest({
      profiles: [{ profileId: 'p1', available: true, enabled: true, evidenceIds: ['e1'] }],
      evidence: [],
    });
    single.candidates = normalizeCandidates(single.profiles).candidates;
    single.evidenceIndex = normalizeEvidence([]).evidence;
    single.policyFacts = derivePolicyFacts({ profiles: single.profiles });
    const answer = JSON.stringify({ profileId: 'p1', reason: 'r', evidenceIds: [], support: { cardProfileIds: [], annotationProfileIds: [] } });
    const withoutPreferences = await callDecisionModel({ llm: fakeLlm({ chunks: answerChunks(answer) }).llm }, single, { timeoutMs: 5_000 });
    assert.equal(withoutPreferences.ok, true, JSON.stringify(withoutPreferences));
    assert.deepEqual(withoutPreferences.decision.policyCheck.taskPreference, { ruleIndex: null, outcome: 'none' });

    // A rule that matches the only candidate.
    const matched = { ...single, profiles: [{ profileId: 'p1', available: true, enabled: true, model: 'deepseek-flash' }], routingPreferences: [{ match: { model: 'deepseek-flash' }, reason: 'prefer flash' }] };
    matched.candidates = normalizeCandidates(matched.profiles).candidates;
    matched.evidenceIndex = normalizeEvidence([]).evidence;
    matched.policyFacts = derivePolicyFacts({ profiles: matched.profiles, routingPreferences: matched.routingPreferences });
    const matchedResult = await callDecisionModel({ llm: fakeLlm({ chunks: answerChunks(answer) }).llm }, matched, { timeoutMs: 5_000 });
    assert.deepEqual(matchedResult.decision.policyCheck.taskPreference, { ruleIndex: 0, outcome: 'matched' });

    // A rule that matches no legal candidate: fallback, not a violation.
    const fallback = { ...single, profiles: [{ profileId: 'p1', available: true, enabled: true, model: 'deepseek-pro' }], routingPreferences: [{ match: { model: 'deepseek-flash' }, reason: 'prefer flash' }] };
    fallback.candidates = normalizeCandidates(fallback.profiles).candidates;
    fallback.evidenceIndex = normalizeEvidence([]).evidence;
    fallback.policyFacts = derivePolicyFacts({ profiles: fallback.profiles, routingPreferences: fallback.routingPreferences });
    const fallbackResult = await callDecisionModel({ llm: fakeLlm({ chunks: answerChunks(answer) }).llm }, fallback, { timeoutMs: 5_000 });
    assert.deepEqual(fallbackResult.decision.policyCheck.taskPreference, { ruleIndex: null, outcome: 'fallback' });
  });

  test('userPreferredProfileIds drives the computed user outcome', async () => {
    const profiles = [
      { profileId: 'p1', available: true, enabled: true, evidenceIds: ['e1'] },
      { profileId: 'p4', available: true, enabled: true },
    ];
    const preferences = [{ profileId: 'p4', mode: 'prefer' }];
    const request = pluginRequest({ profiles, preferences, evidence: [{ evidenceId: 'e1', profileId: 'p1' }] });
    request.candidates = normalizeCandidates(profiles).candidates;
    request.evidenceIndex = normalizeEvidence(request.evidence).evidence;
    request.policyFacts = derivePolicyFacts({ profiles, preferences });
    assert.deepEqual(request.policyFacts.userPreferredProfileIds, ['p4']);
    const answer = JSON.stringify({ profileId: 'p1', reason: 'supported deviation', evidenceIds: ['e1'], support: { cardProfileIds: [], annotationProfileIds: [] } });
    const result = await callDecisionModel({ llm: fakeLlm({ chunks: answerChunks(answer) }).llm }, request, { timeoutMs: 5_000 });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(result.decision.policyCheck.userPreference, 'alternative');
    assert.deepEqual(result.decision.policyCheck.taskPreference, { ruleIndex: null, outcome: 'none' });
  });

  test('candidate, evidence, support and alternative limits still refuse a bad answer', async () => {
    // Support referencing a profile that was never supplied.
    const ghostAnswer = JSON.stringify({ profileId: 'p1', reason: 'r', evidenceIds: [], support: { cardProfileIds: ['ghost'], annotationProfileIds: [] } });
    const ghostResult = await callDecisionModel({ llm: fakeLlm({ chunks: answerChunks(ghostAnswer) }).llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(ghostResult.code, POLICY_SUPPORT_UNKNOWN);

    // An alternative without evidence or eligible support: p4 is legal but not
    // the preferred candidate, so an unsupported deviation is refused.
    const flashRule = [{ match: { model: 'deepseek-flash' }, reason: 'Prefer the flash model' }];
    const profiles = [
      { profileId: 'p1', available: true, enabled: true, evidenceIds: ['e1'], model: 'deepseek-flash' },
      { profileId: 'p4', available: true, enabled: true, model: 'deepseek-pro' },
    ];
    const unsupported = pluginRequest({ profiles, candidates: normalizeCandidates(profiles).candidates, routingPreferences: flashRule, annotations: [] });
    unsupported.policyFacts = derivePolicyFacts({ profiles, routingPreferences: flashRule });
    const unsupportedAnswer = JSON.stringify({ profileId: 'p4', reason: 'r', evidenceIds: [], support: { cardProfileIds: [], annotationProfileIds: [] } });
    const unsupportedResult = await callDecisionModel({ llm: fakeLlm({ chunks: answerChunks(unsupportedAnswer) }).llm }, unsupported, { timeoutMs: 5_000 });
    assert.equal(unsupportedResult.code, POLICY_ALTERNATIVE_UNSUPPORTED);

    // An abstention that cites evidence: the plugin's base candidate/evidence
    // layer refuses first with its established code; the typed module enforces
    // the same empty-evidenceIds rule directly (asserted below).
    const citingAbstainAnswer = JSON.stringify({ profileId: null, reason: 'defer', evidenceIds: ['ev-1'], support: { cardProfileIds: [], annotationProfileIds: [] } });
    const citingAbstainResult = await callDecisionModel({ llm: fakeLlm({ chunks: answerChunks(citingAbstainAnswer) }).llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(citingAbstainResult.code, 'answer-evidence-not-supplied');
    const directAbstention = { profileId: null, reason: 'defer', evidenceIds: ['ev-1'], support: { cardProfileIds: [], annotationProfileIds: [] } };
    assert.equal(validateDecision(directAbstention, pluginRequest().policyFacts, [], { cardProfileIds: new Set(), annotationProfileIds: new Set() }).code, POLICY_CHECK_SHAPE);
  });

  test('a supported alternative is adopted with the cited support', async () => {
    const flashRule = [{ match: { model: 'deepseek-flash' }, reason: 'Prefer the flash model' }];
    const prefer = pluginRequest({
      profiles: [
        { profileId: 'p1', available: true, enabled: true, evidenceIds: ['e1'], model: 'deepseek-pro' },
        { profileId: 'p4', available: true, enabled: true, model: 'deepseek-flash' },
      ],
      routingPreferences: flashRule,
      annotations: [{ profileId: 'p1', text: 'economical and adequate', revision: 1, updatedAt: '2026-09-25T00:00:00Z' }],
    });
    prefer.policyFacts = derivePolicyFacts({ profiles: prefer.profiles, routingPreferences: prefer.routingPreferences, preferences: [], hardConstraints: {} });
    assert.deepEqual(prefer.policyFacts.taskPreference, { ruleIndex: 0, matchingProfileIds: ['p4'] });
    const chunks = [{ type: 'text-delta', index: 0, text: '{"profileId":"p1","reason":"supported by the annotation","evidenceIds":[],"policyCheck":{"hardConstraints":{},"taskPreference":{"ruleIndex":0,"outcome":"alternative"},"userPreference":"none"},"support":{"cardProfileIds":[],"annotationProfileIds":["p1"]}}' }, { type: 'finish', reason: { kind: 'stop' } }];
    const result = await callDecisionModel({ llm: fakeLlm({ chunks }).llm }, prefer, { timeoutMs: 5_000 });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.deepEqual(result.decision.support, { cardProfileIds: [], annotationProfileIds: ['p1'] });
  });
});

describe('bounded corrective retry (R2)', () => {
  test('corrected usage includes both model calls and never invents missing counters', async () => {
    const good = JSON.stringify({ profileId: 'p1', reason: 'r', evidenceIds: [], support: { cardProfileIds: [], annotationProfileIds: [] } });
    const used = text => [
      { type: 'text-delta', text },
      { type: 'usage', usage: { inputTokens: 10, outputTokens: 2, totalTokens: 12 } },
      { type: 'finish', reason: { kind: 'stop' } },
    ];
    const { llm } = fakeLlm({ chunkSets: [used('bad'), used(good)] });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.ok, true);
    assert.equal(result.usage.inputTokens, 20);
    assert.equal(result.usage.outputTokens, 4);
    assert.equal(result.usage.totalTokens, 24);
    assert.equal(result.usage.cacheReadTokens, null);
  });
  const VALID_ANSWER = JSON.stringify({ profileId: 'p1', reason: 'valid', evidenceIds: [], support: { cardProfileIds: [], annotationProfileIds: [] } });

  test('one format refusal is corrected by one second call under the same deadline', async () => {
    const invalid = '{"profileId":"p1","reason":"r","evidenceIds":[],"temperature":1,"support":{"cardProfileIds":[],"annotationProfileIds":[]}}';
    const { llm, calls } = fakeLlm({ chunkSets: [answerChunks(invalid), answerChunks(VALID_ANSWER)] });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(calls.stream.length, 2, 'exactly one correction');
    assert.equal(calls.prepare.length, 4, 'each call legally re-prepares its own one-use handle');
    assert.equal(result.diagnostics.calls, 2);
    assert.equal(result.diagnostics.failures.length, 1);
    assert.equal(result.diagnostics.failures[0].code, 'answer-unexpected-field');
    assert.equal(result.diagnostics.failures[0].answer.redacted, true);
    // The frozen input and prefix are identical; only the correction turn is new.
    const [first, second] = calls.stream.map((entry) => entry.options);
    assert.equal(first.system, second.system);
    assert.equal(first.messages.length, 1);
    assert.equal(second.messages.length, 2);
    assert.equal(first.messages[0].content[0].text, second.messages[0].content[0].text);
    const correction = JSON.parse(second.messages[1].content[0].text);
    assert.equal(correction.correction.code, 'answer-unexpected-field');
    assert.equal(correction.correction.instruction, CORRECTION_NOTES['answer-unexpected-field']);
    assert.equal(first.signal, second.signal, 'both attempts share one overall deadline signal');
  });

  test('two invalid answers fail with the second refusal retained', async () => {
    const { llm, calls } = fakeLlm({ chunkSets: [answerChunks('not json at all'), answerChunks('{"profileId":"p1","reason":"r"}')] });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.ok, false);
    assert.equal(result.code, 'answer-shape');
    assert.equal(calls.stream.length, 2, 'a third call is never made');
    assert.equal(result.diagnostics.calls, 2);
    assert.deepEqual(result.diagnostics.failures.map((failure) => failure.code), ['answer-not-json', 'answer-shape']);
    assert.equal(result.diagnostics.failures[0].answer.text, '<unparsed-answer>');
  });

  test('an abstention is a valid answer and is never retried', async () => {
    const abstain = JSON.stringify({ profileId: null, reason: 'ambiguous', evidenceIds: [], support: { cardProfileIds: [], annotationProfileIds: [] } });
    const { llm, calls } = fakeLlm({ chunks: answerChunks(abstain) });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.ok, true);
    assert.equal(result.decision.profileId, null);
    assert.equal(calls.stream.length, 1);
    assert.deepEqual(result.diagnostics, { calls: 1, failures: [] });
  });

  test('an empty visible answer is corrected once too', async () => {
    const { llm, calls } = fakeLlm({ chunkSets: [[{ type: 'finish', reason: { kind: 'stop' } }], answerChunks(VALID_ANSWER)] });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(calls.stream.length, 2);
    assert.deepEqual(result.diagnostics.failures.map((failure) => failure.code), ['answer-empty']);
    assert.equal(result.diagnostics.failures[0].answer.text, '<empty-answer>');
    assert.equal(result.diagnostics.failures[0].answer.bytes, 0);
  });

  test('quota, provider, transport, tool-call and truncation failures are never retried', async () => {
    const cases = [
      [[{ type: 'finish', reason: { kind: 'error', failure: { code: 'RATE_LIMIT', message: 'slow down', status: 429 } } }], 'call-failed'],
      [[{ type: 'finish', reason: { kind: 'aborted' } }], 'call-aborted'],
      [[{ type: 'tool-call-delta', index: 0, id: 'c', name: 'bash', argumentsDelta: '{}' }, { type: 'text-delta', index: 0, text: '{}' }, { type: 'finish', reason: { kind: 'stop' } }], 'answer-tool-call'],
      [[{ type: 'text-delta', index: 0, text: '{"profileId":"p1"' }, { type: 'finish', reason: { kind: 'length' } }], 'answer-truncated'],
    ];
    for (const [chunks, code] of cases) {
      const { llm, calls } = fakeLlm({ chunks });
      const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
      assert.equal(result.code, code, code);
      assert.equal(calls.stream.length, 1, `${code} must not retry`);
      assert.equal(result.diagnostics.calls, 1, code);
      assert.equal(result.diagnostics.failures.length, 1, code);
    }
  });

  test('a deadline abort is never retried, and the correcting call shares the one deadline', async () => {
    const { llm, calls } = fakeLlm({ chunkSets: [answerChunks('{"profileId":"p1","reason":"r"}'), hangUntilAborted] });
    const started = performance.now();
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 300 });
    const elapsed = performance.now() - started;
    assert.equal(result.code, 'call-timeout');
    assert.equal(calls.stream.length, 2, 'the timeout happened during the correcting second call');
    assert.equal(calls.stream[0].options.signal, calls.stream[1].options.signal);
    assert.ok(elapsed < 3_000, `the shared deadline fired late: ${String(elapsed)}ms`);
  });

  test('an unresolvable profile is reported once and never retried', async () => {
    const failPrepare = Object.assign(new Error('no route'), { code: 'UNSUPPORTED_MODEL' });
    const { llm, calls } = fakeLlm({ failPrepare });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.code, 'decision-profile-unresolved');
    assert.equal(calls.stream.length, 0);
    assert.deepEqual(result.diagnostics, { calls: 0, failures: [] });
  });

  test('reasoning bytes are bounded and summed across the corrected flow', async () => {
    const first = [{ type: 'reasoning-delta', index: 0, text: 'abcd' }, ...answerChunks('not json')];
    const second = [{ type: 'reasoning-delta', index: 0, text: 'efgh' }, ...answerChunks(VALID_ANSWER)];
    const { llm } = fakeLlm({ chunkSets: [first, second] });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(result.reasoningBytes, 8);
    assert.equal(result.diagnostics.calls, 2);
  });

  test('the retryable set is exactly the enumerated answer-validation codes', () => {
    assert.deepEqual([...RETRYABLE_ANSWER_CODES], [
      'answer-empty',
      'answer-not-json',
      'answer-invalid-json',
      'answer-shape',
      'answer-unexpected-field',
      'answer-profile-not-candidate',
      'answer-evidence-not-supplied',
      'policy-check-shape',
      'policy-support-unknown',
      'policy-alternative-unsupported',
    ]);
    for (const code of ['call-failed', 'call-aborted', 'call-timeout', 'answer-truncated', 'answer-tool-call', 'answer-too-large', 'policy-facts-mismatch', 'decision-profile-unresolved']) {
      assert.equal(isRetryableAnswerCode(code), false, code);
    }
  });
});

describe('bounded visible-answer diagnostics (R3)', () => {
  test('free-form and unknown fields redact numeric and nested contents too', () => {
    const raw = JSON.stringify({ profileId: 123456789, reason: { profileId: 'p1' }, secret: 987654321 });
    const diagnostic = boundedAnswerDiagnostic(raw, new Set(['p1']));
    assert.doesNotMatch(diagnostic.text, /123456789|987654321|p1|secret/);
  });
  const SECRET = 'sk-live-SUPERSECRET';
  const URL = 'https://internal.example.invalid/private?token=abcd';
  const VALID_ANSWER = JSON.stringify({ profileId: 'p1', reason: 'valid', evidenceIds: [], support: { cardProfileIds: [], annotationProfileIds: [] } });

  test('free text, unknown keys and unknown strings never enter a diagnostic', async () => {
    const raw = JSON.stringify({
      profileId: 'p1',
      reason: `the user wrote ${SECRET} and ${URL}`,
      evidenceIds: [],
      support: { cardProfileIds: [], annotationProfileIds: [] },
      note: `please remember ${SECRET}`,
    });
    const { llm } = fakeLlm({ chunkSets: [answerChunks(raw), answerChunks(VALID_ANSWER)] });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.ok, true, JSON.stringify(result));
    const rendered = JSON.stringify(result.diagnostics);
    assert.doesNotMatch(rendered, /SUPERSECRET/);
    assert.doesNotMatch(rendered, /internal\.example\.invalid/);
    assert.doesNotMatch(rendered, /the user wrote/);
    assert.doesNotMatch(rendered, /please remember/);
    const failure = result.diagnostics.failures[0];
    assert.equal(failure.code, 'answer-unexpected-field');
    assert.match(failure.answer.text, /"reason":"<redacted>"/);
    assert.match(failure.answer.text, /<unknown-field>/);
    assert.match(failure.answer.text, /"profileId":"p1"/, 'a supplied identifier stays readable');
    assert.equal(failure.answer.sha256, createHash('sha256').update(raw, 'utf8').digest('hex'));
    assert.equal(failure.answer.bytes, Buffer.byteLength(raw, 'utf8'));
    assert.equal(failure.answer.redacted, true);
    assert.equal(failure.answer.truncated, false);
  });

  test('an unparseable answer keeps only its hash, byte count and a placeholder', async () => {
    const raw = `Sorry, I cannot answer. ${SECRET} ${URL}`;
    const { llm } = fakeLlm({ chunkSets: [answerChunks(raw), answerChunks(VALID_ANSWER)] });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.ok, true);
    const failure = result.diagnostics.failures[0];
    assert.equal(failure.code, 'answer-not-json');
    assert.equal(failure.answer.text, '<unparsed-answer>');
    assert.equal(failure.answer.bytes, Buffer.byteLength(raw, 'utf8'));
    assert.equal(failure.answer.sha256, createHash('sha256').update(raw, 'utf8').digest('hex'));
    assert.doesNotMatch(JSON.stringify(result.diagnostics), /SUPERSECRET/);
  });

  test('reasoning text and provider messages never enter a diagnostic', async () => {
    const chunks = [
      { type: 'reasoning-delta', index: 0, text: `hidden chain of thought ${SECRET}` },
      ...answerChunks('{"profileId":"p1","reason":"r","evidenceIds":["e1"],"support":{"cardProfileIds":[],"annotationProfileIds":[]},"ghostField":"x"}'),
    ];
    const { llm } = fakeLlm({ chunkSets: [chunks, answerChunks(VALID_ANSWER)] });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.doesNotMatch(JSON.stringify(result.diagnostics), /hidden chain of thought/);
    assert.doesNotMatch(JSON.stringify(result.diagnostics), /SUPERSECRET/);
    const failure = result.diagnostics.failures[0];
    assert.equal(failure.code, 'answer-unexpected-field');
    assert.match(failure.answer.text, /"e1"/, 'a supplied evidence id stays readable');
    assert.match(failure.answer.text, /<unknown-field>/);
    assert.doesNotMatch(failure.answer.text, /ghostField/);
  });

  test('one redacted diagnostic is structurally bounded and reports truncation', () => {
    const raw = JSON.stringify({ profileId: 'p1', reason: 'x'.repeat(5_000), evidenceIds: Array.from({ length: 50 }, (_, index) => `e${String(index)}`) });
    const diagnostic = boundedAnswerDiagnostic(raw, new Set(['p1']));
    assert.ok(Buffer.byteLength(diagnostic.text, 'utf8') <= MAX_DIAGNOSTIC_TEXT_BYTES);
    assert.equal(diagnostic.truncated, true);
    assert.equal(diagnostic.redacted, true);
    assert.equal(diagnostic.sha256, createHash('sha256').update(raw, 'utf8').digest('hex'));
    assert.doesNotMatch(diagnostic.text, /xxxx/);
  });

  test('the failure list covers the two-attempt flow only', async () => {
    assert.equal(MAX_DIAGNOSTIC_FAILURES, 2);
    const { llm } = fakeLlm({ chunks: answerChunks('nope') });
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.code, 'answer-not-json');
    assert.equal(result.diagnostics.calls, 2);
    assert.equal(result.diagnostics.failures.length, 2);
    assert.deepEqual(result.diagnostics.failures.map((failure) => failure.code), ['answer-not-json', 'answer-not-json']);
  });
});

describe('answer and candidate validation', () => {
  function legal() {
    return legalCandidateIds(normalizeCandidates(pluginRequest().profiles).candidates, normalizeEvidence(pluginRequest().evidence).evidence);
  }

  test('only enabled AND available profiles are legal candidates', () => {
    const candidates = legal();
    assert.deepEqual([...candidates.keys()], ['p1']);
    // Request-supplied evidence comes first, then evidence declared on the profile.
    assert.deepEqual([...candidates.get('p1')].sort(), ['e1', 'e2']);
    assert.equal(candidates.get('p1').has('invented'), false);
  });

  test('an unavailable or disabled profile is never accepted', () => {
    assert.equal(validateSelectAnswer({ profileId: 'p2', reason: 'r', evidenceIds: [] }, legal()).problem, 'answer-profile-not-candidate');
    assert.equal(validateSelectAnswer({ profileId: 'p3', reason: 'r', evidenceIds: [] }, legal()).problem, 'answer-profile-not-candidate');
    assert.equal(validateSelectAnswer({ profileId: 'p9', reason: 'r', evidenceIds: [] }, legal()).problem, 'answer-profile-not-candidate');
  });

  test('select answers enforce shape, bounds and supplied evidence', () => {
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: 'r', evidenceIds: ['e1'] }, legal()).decision.profileId, 'p1');
    assert.equal(validateSelectAnswer({ profileId: null, reason: 'r', evidenceIds: [] }, legal()).decision.profileId, null);
    assert.equal(validateSelectAnswer({ profileId: null, reason: 'r', evidenceIds: ['e1'] }, legal()).problem, 'answer-evidence-not-supplied');
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: 'r', evidenceIds: [] }, legal()).decision.evidenceIds.length, 0, 'an uncited decision is valid');
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: '', evidenceIds: [] }, legal()).problem, 'answer-shape');
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: 'r', evidenceIds: ['e2'] }, legal()).decision.evidenceIds[0], 'e2');
  });

  test('evidence ids are bounded and deduplicated, and unknown ids are distinguished', () => {
    const many = Array.from({ length: BOUNDS.evidenceIds + 1 }, () => 'e1');
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: 'r', evidenceIds: many }, legal()).problem, 'answer-shape');
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: 'r', evidenceIds: ['e1', 'e1'] }, legal()).problem, 'answer-shape');
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: 'r', evidenceIds: [''] }, legal()).problem, 'answer-shape');
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: 'r', evidenceIds: ['invented'] }, legal()).problem, 'answer-evidence-not-supplied');
  });

  test('model output can never add authorization, preference or extra fields', () => {
    for (const extra of ['temperature', 'authorization', 'preference', 'accepted', 'verdict', 'tools', 'cards']) {
      const answer = { profileId: 'p1', reason: 'r', evidenceIds: [], [extra]: true };
      assert.equal(validateSelectAnswer(answer, legal()).problem, 'answer-unexpected-field', extra);
    }
  });

  test('evidence ids are bounded, deduplicated and supplied', () => {
    const many = Array.from({ length: BOUNDS.evidenceIds + 1 }, (_, index) => `e${String(index)}`);
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: 'r', evidenceIds: many }, legal()).problem, 'answer-shape');
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: 'r', evidenceIds: ['e1', 'e1'] }, legal()).problem, 'answer-shape');
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: 'r', evidenceIds: [''] }, legal()).problem, 'answer-shape');
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: 'r', evidenceIds: ['invented'] }, legal()).problem, 'answer-evidence-not-supplied');
  });

  test('extractJsonValue accepts one bare object or one fenced block only', () => {
    assert.deepEqual(extractJsonValue('{"a":1}').value, { a: 1 });
    assert.deepEqual(extractJsonValue('  \n {"a":1} \n ').value, { a: 1 });
    assert.deepEqual(extractJsonValue('```json\n{"a":1}\n```').value, { a: 1 });
    assert.deepEqual(extractJsonValue('```\n{"a":1}\n```').value, { a: 1 });
    assert.equal(extractJsonValue('').problem, 'answer-empty');
    assert.equal(extractJsonValue('Sure! {"a":1}').problem, 'answer-not-json');
    assert.equal(extractJsonValue('{"a":').problem, 'answer-invalid-json');
    assert.equal(extractJsonValue('[1,2]').problem, 'answer-not-json');
    assert.equal(extractJsonValue('null').problem, 'answer-not-json');
    assert.equal(extractJsonValue('```json\n```').problem, 'answer-empty');
  });

  test('finish reasons map to stable codes', () => {
    assert.equal(finishProblem({ kind: 'stop' }), null);
    assert.equal(finishProblem({ kind: 'length' }), 'answer-truncated');
    assert.equal(finishProblem({ kind: 'tool-calls' }), 'answer-tool-call');
    assert.equal(finishProblem({ kind: 'aborted' }), 'call-aborted');
    assert.equal(finishProblem({ kind: 'error' }), 'call-failed');
    assert.equal(finishProblem(undefined), 'answer-missing-finish');
  });

  test('resolveAnswer refuses a chatty or truncated answer instead of scanning for JSON', () => {
    assert.equal(resolveAnswer('select', 'I chose p1.', legal()).problem, 'answer-not-json');
    assert.equal(resolveAnswer('select', '{"profileId":"p1"', legal()).problem, 'answer-invalid-json');
    assert.deepEqual(resolveAnswer('select', TEXT_ANSWER, legal()).decision.profileId, 'p1');
  });
});

describe('in-tree agent-runner guard', () => {
  /** A minimal loader service the way the live tree exposes it. */
  function loaderCtx(entries) {
    return { get: (name) => (name === 'loader' ? { entries: () => entries } : undefined) };
  }

  test('a disabled runner row passes and an active row does not', () => {
    assert.equal(checkAgentRunnerDisabled(loaderCtx([{ options: { id: 'headless-runner', disabled: true }, disabled: true }])), null);
    assert.equal(checkAgentRunnerDisabled(loaderCtx([{ options: { id: 'headless-runner' } }])), 'runner-not-disabled');
  });

  test('a missing runner row or an unreachable loader refuses, never assumes', () => {
    assert.equal(checkAgentRunnerDisabled(loaderCtx([{ options: { id: 'something-else' } }])), 'runner-row-missing');
    assert.equal(checkAgentRunnerDisabled(loaderCtx([])), 'runner-row-missing');
    assert.equal(checkAgentRunnerDisabled({ get: () => undefined }), 'runner-unverifiable');
    assert.equal(checkAgentRunnerDisabled({ get: () => ({}) }), 'runner-unverifiable');
    assert.equal(checkAgentRunnerDisabled({ get: () => { throw new Error('no loader'); } }), 'runner-unverifiable');
  });
});

describe('request reading and result writing', () => {
  test('readRequest normalizes candidates and evidence and rejects malformed documents', async () => {
    const { mkdtempSync, writeFileSync, rmSync } = await import('node:fs');
    const { tmpdir } = await import('node:os');
    const { join } = await import('node:path');
    const dir = mkdtempSync(join(tmpdir(), 'decision-plugin-'));
    try {
      const good = join(dir, 'good.json');
      const profiles = [{ profileId: 'p1', available: true, enabled: true }];
      writeFileSync(good, JSON.stringify({
        operation: 'select', requestId: 'r', profile: { provider: 'p', model: 'm', effort: 'off' }, tableRevision: 1,
        task: 't', profiles, evidence: [], policyFacts: derivePolicyFacts({ profiles }),
      }));
      const read = readRequest(good);
      assert.equal(read.request.operation, 'select');
      assert.equal(read.request.candidates.get('p1').available, true);
      assert.deepEqual(read.request.policyFacts, derivePolicyFacts({ profiles }));
      const bad = join(dir, 'bad.json');
      writeFileSync(bad, '{');
      assert.equal(readRequest(bad).problem, 'request-invalid-json');
      assert.equal(readRequest(join(dir, 'missing.json')).problem, 'request-unreadable');
      writeFileSync(bad, JSON.stringify({ operation: 'select' }));
      assert.equal(readRequest(bad).problem, 'request-profile-invalid');
      writeFileSync(bad, JSON.stringify({ operation: 'select', profile: { provider: 'p', model: 'm', effort: 'off' }, profiles: [] }));
      assert.equal(readRequest(bad).problem, 'request-profile-invalid');
      writeFileSync(bad, JSON.stringify({ operation: 'select', profile: { provider: 'p', model: 'm', effort: 'off' }, profiles: [{ profileId: 'p1' }], evidence: 'no' }));
      assert.equal(readRequest(bad).problem, 'request-evidence-invalid');
      writeFileSync(bad, JSON.stringify({ operation: 'select', profile: { provider: 'p', model: 'm', effort: 'off' }, profiles: [{ profileId: 'p1', available: true, enabled: true }], policyFacts: 'no' }));
      assert.equal(readRequest(bad).problem, 'request-policy-invalid');
      writeFileSync(bad, JSON.stringify({ operation: 'select', profile: { provider: 'p', model: 'm', effort: 'off' }, profiles: [{ profileId: 'p1', available: true, enabled: true }] }));
      assert.equal(readRequest(bad).problem, 'request-policy-invalid', 'the policy facts are part of the current request contract');
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  test('writeJsonFile is atomic, owner-only and leaves no temporary file', async () => {
    const { mkdtempSync, readFileSync, readdirSync, statSync, rmSync } = await import('node:fs');
    const { tmpdir } = await import('node:os');
    const { join } = await import('node:path');
    const dir = mkdtempSync(join(tmpdir(), 'decision-write-'));
    try {
      const file = join(dir, 'result.json');
      writeJsonFile(file, { status: 'ok' });
      assert.deepEqual(JSON.parse(readFileSync(file, 'utf8')), { status: 'ok' });
      assert.equal(statSync(file).mode & 0o777, 0o600);
      assert.deepEqual(readdirSync(dir), ['result.json']);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
