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
import { describe, test } from 'node:test';

import {
  BOUNDS, MAX_ANSWER_BYTES, MAX_OUTPUT_TOKENS,
  callDecisionModel, checkAgentRunnerDisabled, extractJsonValue, finishProblem,
  legalCandidateIds, legalMaintenanceIds,
  normalizeCandidates, normalizeEvidence, readRequest, resolveAnswer,
  validateMaintainAnswer, validateSelectAnswer, waitForRoute, writeJsonFile,
} from '../plugins/decision.mjs';
import { MAX_PAYLOAD_BYTES, PROMPT_VERSION, buildPayload, instructionsFor, renderUserTurn } from '../scripts/decision-prompt.mjs';

/** A fake `ctx.llm` recording every call it receives. */
function fakeLlm({ chunks, prepareConfig, providers = [{ id: 'deepseek-official', name: 'DeepSeek' }], failPrepare } = {}) {
  const calls = { prepare: [], stream: [], listed: 0 };
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
          calls.stream.push({ options, hadSignal: options.signal !== undefined });
          return (async function* generate() {
            for (const chunk of chunks ?? []) yield chunk;
          })();
        },
      };
    },
  };
  return { llm, calls };
}

/** The minimal normalized request the plugin works from. */
function pluginRequest(overrides = {}) {
  const profiles = [
    { profileId: 'p1', available: true, enabled: true, evidenceIds: ['e1'] },
    { profileId: 'p2', available: false, enabled: true, evidenceIds: [] },
    { profileId: 'p3', available: true, enabled: false, evidenceIds: [] },
  ];
  const candidates = normalizeCandidates(profiles).candidates;
  const evidence = normalizeEvidence([{ evidenceId: 'e2', profileId: 'p1' }]).evidence;
  return {
    operation: 'select',
    requestId: 'req-1',
    profile: { provider: 'deepseek-official', model: 'deepseek-flash', effort: 'off' },
    tableRevision: 4,
    task: 'choose a profile',
    profiles,
    cards: [],
    preferences: [],
    evidence: [{ evidenceId: 'e2', profileId: 'p1' }],
    candidates,
    evidenceIndex: evidence,
    ...overrides,
  };
}

const TEXT_ANSWER = '{"profileId":"p1","reason":"grounded","evidenceIds":["e1"]}';

describe('prompt assembly', () => {
  test('the instruction prefix is versioned, operation-specific and request-free', () => {
    assert.equal(PROMPT_VERSION, 3);
    const select = instructionsFor('select');
    const maintain = instructionsFor('maintain');
    assert.notEqual(select, maintain);
    assert.equal(instructionsFor('select'), select, 'the prefix must be stable');
    for (const text of [select, maintain]) {
      assert.doesNotMatch(text, /req-1/);
      assert.doesNotMatch(text, /tableRevision/);
      assert.match(text, /Return exactly one JSON object/);
    }
    assert.throws(() => instructionsFor('delete'), /unknown decision operation/);
  });

  test('the shared table precedes per-request data and requestId comes last', () => {
    const request = { tableRevision: 1, task: 't', operation: 'select', requestId: 'r', profile: {}, profiles: [], cards: [], preferences: [], evidence: [] };
    assert.deepEqual(Object.keys(buildPayload('select', request)), ['operation', 'profile', 'profiles', 'cards', 'preferences', 'evidence', 'tableRevision', 'task', 'requestId']);
    // Maintain carries the same table too: its instructions refer to profiles.
    assert.deepEqual(Object.keys(buildPayload('maintain', { ...request, operation: 'maintain' })), ['operation', 'profile', 'profiles', 'cards', 'preferences', 'evidence', 'tableRevision', 'requestId']);
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
    const result = await callDecisionModel({ llm }, pluginRequest(), { timeoutMs: 5_000 });
    assert.equal(result.ok, true);
    assert.deepEqual(result.decision, { profileId: 'p1', reason: 'grounded', evidenceIds: ['e1'] });
    assert.deepEqual(result.resolvedConfig, { provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'off' });
    assert.equal('observed' in result, false, 'the plugin must not claim observed identity');
    assert.deepEqual(result.usage, { inputTokens: 3, outputTokens: 4, totalTokens: 7, cacheReadTokens: null, cacheWriteTokens: null, reasoningTokens: 2 });
    assert.equal(result.reasoningBytes, 8);
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
    for (const extra of ['temperature', 'authorization', 'preference', 'accepted', 'verdict', 'tools']) {
      const answer = { profileId: 'p1', reason: 'r', evidenceIds: [], [extra]: true };
      assert.equal(validateSelectAnswer(answer, legal()).problem, 'answer-unexpected-field', extra);
    }
    const maintain = { cards: [], reason: 'r', preferences: [{ profileId: 'p1', mode: 'pin' }] };
    assert.equal(validateMaintainAnswer(maintain, legal()).problem, 'answer-unexpected-field');
  });

  test('evidence ids are bounded, deduplicated and supplied', () => {
    const many = Array.from({ length: BOUNDS.evidenceIds + 1 }, (_, index) => `e${String(index)}`);
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: 'r', evidenceIds: many }, legal()).problem, 'answer-shape');
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: 'r', evidenceIds: ['e1', 'e1'] }, legal()).problem, 'answer-shape');
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: 'r', evidenceIds: [''] }, legal()).problem, 'answer-shape');
    assert.equal(validateSelectAnswer({ profileId: 'p1', reason: 'r', evidenceIds: ['invented'] }, legal()).problem, 'answer-evidence-not-supplied');
  });

  test('maintenance accepts a disabled or unavailable profile so it can record why', () => {
    const candidates = normalizeCandidates(pluginRequest().profiles).candidates;
    const index = normalizeEvidence(pluginRequest().evidence).evidence;
    const maintenance = legalMaintenanceIds(candidates, index);
    assert.deepEqual([...maintenance.keys()].sort(), ['p1', 'p2', 'p3']);
    assert.equal(legalCandidateIds(candidates, index).has('p2'), false, 'selection still refuses unavailable profiles');
    const disabled = { profileId: 'p3', summary: 'disabled after the incident', strengths: [], limitations: ['unavailable'], risks: [], evidenceIds: [] };
    assert.deepEqual(validateMaintainAnswer({ cards: [disabled], reason: 'r' }, maintenance).proposal.cards[0], disabled);
    // A profile id that was never supplied is still refused.
    assert.equal(validateMaintainAnswer({ cards: [{ ...disabled, profileId: 'ghost' }], reason: 'r' }, maintenance).problem, 'answer-profile-not-candidate');
  });

  test('maintain cards are validated field by field', () => {
    const card = { profileId: 'p1', summary: 's', strengths: ['a'], limitations: [], risks: [], evidenceIds: ['e1'] };
    const accepted = validateMaintainAnswer({ cards: [card], reason: 'r' }, legal());
    assert.equal(accepted.proposal.cards.length, 1);
    assert.deepEqual(accepted.proposal.cards[0], card);
    assert.equal(validateMaintainAnswer({ cards: [card, card], reason: 'r' }, legal()).problem, 'answer-duplicate-card');
    assert.equal(validateMaintainAnswer({ cards: [{ ...card, summary: '' }], reason: 'r' }, legal()).problem, 'answer-shape');
    assert.equal(validateMaintainAnswer({ cards: [{ ...card, strengths: ['a', 'b', 'c'] }], reason: 'r' }, legal()).proposal.cards[0].strengths.length, 3);
    assert.equal(validateMaintainAnswer({ cards: [{ ...card, risks: 'no' }], reason: 'r' }, legal()).problem, 'answer-shape');
    assert.equal(validateMaintainAnswer({ cards: [{ ...card, profileId: 'p2' }], reason: 'r' }, legal()).problem, 'answer-profile-not-candidate');
  });

  test('an empty maintenance proposal is a valid, explicit answer', () => {
    const accepted = validateMaintainAnswer({ cards: [], reason: 'nothing to change' }, legal());
    assert.deepEqual(accepted.proposal, { cards: [], reason: 'nothing to change' });
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
    assert.equal(resolveAnswer('maintain', '{"cards":[]', legal()).problem, 'answer-invalid-json');
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
      writeFileSync(good, JSON.stringify({
        operation: 'select', requestId: 'r', profile: { provider: 'p', model: 'm', effort: 'off' }, tableRevision: 1,
        task: 't', profiles: [{ profileId: 'p1', available: true, enabled: true }], evidence: [],
      }));
      const read = readRequest(good);
      assert.equal(read.request.operation, 'select');
      assert.equal(read.request.candidates.get('p1').available, true);
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
