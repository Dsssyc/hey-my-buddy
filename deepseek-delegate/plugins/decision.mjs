/**
 * deepseek-delegate decision plugin.
 *
 * A tiny, self-contained Cordis plugin mounted by `scripts/decision.mjs` into
 * an existing dsh profile through a temporary `--patch` overlay. It performs
 * exactly ONE hand-built call against the native harness `llm` service and
 * writes one validated result file, then exits. It is not an agent: it never
 * creates a session, never asks for a system prompt, never supplies a tool
 * schema, and never retries.
 *
 * Native interfaces used (verified against the installed dsh 0.1.5-rc.1
 * sources; see the adapter report for exact file references):
 * - `ctx.llm.prepareCall(config)` resolves and freezes the exact provider,
 *   model, reasoning effort, and adapter-default controls for one call and
 *   returns a one-shot `stream()` bound to that adapter registration.
 * - `ctx.llm.stream(options)` / the prepared handle speak the canonical chunk
 *   protocol (`block-start`, `text-delta`, `reasoning-delta`,
 *   `tool-call-delta`, `block-end`, `usage`, `finish`).
 * - `ctx.llm.listProviders()` / `listModels()` are the only availability
 *   facts this plugin trusts.
 *
 * Credentials are never read, copied, or logged here: the provider adapter
 * resolves its own credential reference per request through the harness
 * `credentials` service, exactly as an ordinary dsh run does.
 *
 * @module deepseek-delegate/plugins/decision
 */
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { performance } from 'node:perf_hooks';
import { dirname, join } from 'node:path';
import { instructionsFor, renderUserTurn } from '../scripts/decision-prompt.mjs';

/** Stable Cordis plugin name; the run's patch row mirrors it. */
export const name = 'deepseek-delegate-decision';

/** Services this plugin needs before it may run. */
export const inject = ['llm'];

/** Cap on the model's visible output, in UTF-8 bytes. */
export const MAX_ANSWER_BYTES = 32_768;
/** Cap on the model's reasoning text retained for diagnostics, in bytes. */
export const MAX_REASONING_BYTES = 8_192;
/** How long to wait for the decision profile's adapter route to register. */
export const ADAPTER_WAIT_MS = 20_000;
/** Poll interval while waiting for that route. */
const ADAPTER_POLL_MS = 100;

/** Output-token bound for the single bounded call. */
export const MAX_OUTPUT_TOKENS = 4_096;

/** Bounded string/list limits mirrored from the prompt contract. */
export const BOUNDS = Object.freeze({
  reasonChars: 2_000,
  summaryChars: 2_000,
  listItemChars: 500,
  strengths: 20,
  limitations: 20,
  risks: 20,
  evidenceIds: 50,
  cards: 64,
});

/** Non-empty, bounded, single-line-safe identifier. */
function isIdentifier(value) {
  return typeof value === 'string' && value.length > 0 && value.length <= 256 && !/[\u0000-\u001f\u007f]/.test(value);
}

/** Bounded human text; newlines are allowed, control characters are not. */
function isText(value, maxChars) {
  return typeof value === 'string' && value.length > 0 && value.length <= maxChars && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value);
}

/** Read the value for `key` from `object`, or `undefined` when it is not an own property. */
function field(object, key) {
  if (object === null || typeof object !== 'object' || Array.isArray(object)) return undefined;
  return Object.hasOwn(object, key) ? object[key] : undefined;
}

/**
 * Validate one identifier list.
 * @returns `null` when valid, otherwise `'shape'` for a malformed list and
 * `'not-supplied'` when an id is outside the supplied set.
 */
function checkIdentifierList(value, { limit, allowed, allowEmpty = true, label }) {
  if (!Array.isArray(value)) return { kind: 'shape', detail: `${label} must be an array` };
  if (!allowEmpty && value.length === 0) return { kind: 'shape', detail: `${label} must not be empty` };
  if (value.length > limit) return { kind: 'shape', detail: `${label} has ${value.length} entries, above the ${limit} bound` };
  const seen = new Set();
  for (const [index, entry] of value.entries()) {
    if (!isIdentifier(entry)) return { kind: 'shape', detail: `${label}[${index}] is not a bounded identifier` };
    if (allowed !== undefined && !allowed.has(entry)) return { kind: 'not-supplied', detail: `${label}[${index}] references an id that was not supplied` };
    if (seen.has(entry)) return { kind: 'shape', detail: `${label}[${index}] repeats an id` };
    seen.add(entry);
  }
  return null;
}

/** Validate one bounded string list. */
function checkTextList(value, maxItems, maxChars, label) {
  if (!Array.isArray(value)) return `${label} must be an array`;
  if (value.length > maxItems) return `${label} has ${value.length} entries, above the ${maxItems} bound`;
  for (const [index, entry] of value.entries()) {
    if (typeof entry !== 'string') return `${label}[${index}] is not a string`;
    if (entry.length > maxChars) return `${label}[${index}] is ${entry.length} characters, above the ${maxChars} bound`;
  }
  return null;
}

/**
 * Normalize the request's candidate profiles.
 *
 * Only a profile that is explicitly enabled and explicitly available is a
 * legal candidate. `available` must be literally `true`: an omitted or unknown
 * availability is not availability, and never becomes one.
 *
 * @returns `{ candidates, problem }`; `problem` is a machine code, not a message.
 */
export function normalizeCandidates(profiles) {
  if (!Array.isArray(profiles) || profiles.length === 0) return { candidates: null, problem: 'request-profile-invalid' };
  if (profiles.length > 128) return { candidates: null, problem: 'request-profile-invalid' };
  const candidates = new Map();
  for (const entry of profiles) {
    const profileId = field(entry, 'profileId');
    if (!isIdentifier(profileId) || candidates.has(profileId)) return { candidates: null, problem: 'request-profile-invalid' };
    const enabled = field(entry, 'enabled');
    const available = field(entry, 'available');
    const evidenceIds = [];
    const supplied = field(entry, 'evidenceIds');
    if (Array.isArray(supplied)) {
      if (checkIdentifierList(supplied, { limit: 512 }, 'profile.evidenceIds') !== null) {
        return { candidates: null, problem: 'request-profile-invalid' };
      }
      evidenceIds.push(...supplied);
    }
    candidates.set(profileId, {
      profileId,
      enabled: enabled === true,
      available: available === true,
      evidenceIds,
    });
  }
  return { candidates, problem: null };
}

/**
 * Normalize the request's evidence index.
 *
 * @returns `{ evidence, problem }` where `evidence` maps one profile id to the
 * evidence ids supplied for it, in request order.
 */
export function normalizeEvidence(evidence) {
  const index = new Map();
  if (evidence === undefined) return { evidence: index, problem: null };
  if (!Array.isArray(evidence)) return { evidence: null, problem: 'request-evidence-invalid' };
  if (evidence.length > 512) return { evidence: null, problem: 'request-evidence-invalid' };
  for (const entry of evidence) {
    const profileId = field(entry, 'profileId');
    const evidenceId = field(entry, 'evidenceId');
    if (!isIdentifier(profileId) || !isIdentifier(evidenceId)) return { evidence: null, problem: 'request-evidence-invalid' };
    const list = index.get(profileId) ?? [];
    if (list.includes(evidenceId)) return { evidence: null, problem: 'request-evidence-invalid' };
    list.push(evidenceId);
    index.set(profileId, list);
  }
  return { evidence: index, problem: null };
}

/**
 * The legal SELECTION candidate set: only profiles that are explicitly enabled
 * and explicitly available, each with the evidence ids actually supplied for
 * it. A disabled or unavailable profile can never be selected.
 */
export function legalCandidateIds(candidates, evidence) {
  const legal = new Map();
  for (const [profileId, candidate] of candidates) {
    if (!candidate.enabled || !candidate.available) continue;
    legal.set(profileId, suppliedEvidenceIds(candidate, evidence));
  }
  return legal;
}

/**
 * The legal MAINTENANCE set: every profile the request supplied, including
 * disabled and unavailable ones.
 *
 * Selection legality and maintenance legality are different questions. A
 * profile that cannot be selected is exactly the profile whose card most needs
 * to record why — refusing maintenance for it would make an exclusion
 * impossible to document. What maintenance may never do is invent a profile
 * id, so membership of the supplied table is the whole test.
 */
export function legalMaintenanceIds(candidates, evidence) {
  const legal = new Map();
  for (const [profileId, candidate] of candidates) {
    legal.set(profileId, suppliedEvidenceIds(candidate, evidence));
  }
  return legal;
}

/** Evidence ids a request supplied for one profile: request evidence then profile-declared. */
function suppliedEvidenceIds(candidate, evidence) {
  return new Set([...(evidence.get(candidate.profileId) ?? []), ...candidate.evidenceIds]);
}

/** Narrow one parsed model value to a plain object; arrays and null are refused. */
function asRecord(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  return value;
}

/** Reject any own key outside `allowed`. An unknown field is never ignored. */
function unexpectedKey(record, allowed) {
  for (const key of Object.keys(record)) {
    if (!allowed.includes(key)) return key;
  }
  return null;
}

/**
 * Validate one `select` answer against the request's legal candidate set.
 * @returns `{ decision }` or `{ problem }` with a machine code.
 */
export function validateSelectAnswer(answer, legal) {
  const record = asRecord(answer);
  if (record === null) return { problem: 'answer-shape' };
  const extra = unexpectedKey(record, ['profileId', 'reason', 'evidenceIds']);
  if (extra !== null) return { problem: 'answer-unexpected-field' };
  const profileId = field(record, 'profileId');
  const reason = field(record, 'reason');
  const evidenceIds = field(record, 'evidenceIds');
  if (profileId !== null && !isIdentifier(profileId)) return { problem: 'answer-shape' };
  if (profileId !== null && !legal.has(profileId)) return { problem: 'answer-profile-not-candidate' };
  if (!isText(reason, BOUNDS.reasonChars)) return { problem: 'answer-shape' };
  const allowed = profileId === null ? new Set() : legal.get(profileId);
  const evidenceProblem = checkIdentifierList(evidenceIds, { limit: BOUNDS.evidenceIds, allowed, label: 'evidenceIds' });
  if (evidenceProblem !== null) {
    return { problem: evidenceProblem.kind === 'not-supplied' ? 'answer-evidence-not-supplied' : 'answer-shape' };
  }
  return { decision: { profileId, reason, evidenceIds: [...evidenceIds] } };
}

/**
 * Validate one `maintain` answer against the request's legal maintenance set
 * (every supplied profile, including disabled and unavailable ones).
 * @returns `{ proposal }` or `{ problem }` with a machine code.
 */
export function validateMaintainAnswer(answer, legal) {
  const record = asRecord(answer);
  if (record === null) return { problem: 'answer-shape' };
  const extra = unexpectedKey(record, ['cards', 'reason']);
  if (extra !== null) return { problem: 'answer-unexpected-field' };
  const reason = field(record, 'reason');
  const cards = field(record, 'cards');
  if (!isText(reason, BOUNDS.reasonChars)) return { problem: 'answer-shape' };
  if (!Array.isArray(cards)) return { problem: 'answer-shape' };
  if (cards.length > BOUNDS.cards) return { problem: 'answer-too-many-cards' };
  const seen = new Set();
  const accepted = [];
  for (const [index, card] of cards.entries()) {
    const cardRecord = asRecord(card);
    if (cardRecord === null) return { problem: 'answer-shape' };
    const cardExtra = unexpectedKey(cardRecord, ['profileId', 'summary', 'strengths', 'limitations', 'risks', 'evidenceIds']);
    if (cardExtra !== null) return { problem: 'answer-unexpected-field' };
    const profileId = field(cardRecord, 'profileId');
    const summary = field(cardRecord, 'summary');
    const strengths = field(cardRecord, 'strengths');
    const limitations = field(cardRecord, 'limitations');
    const risks = field(cardRecord, 'risks');
    const evidenceIds = field(cardRecord, 'evidenceIds');
    if (!isIdentifier(profileId) || !legal.has(profileId)) return { problem: 'answer-profile-not-candidate' };
    if (seen.has(profileId)) return { problem: 'answer-duplicate-card' };
    seen.add(profileId);
    if (!isText(summary, BOUNDS.summaryChars)) return { problem: 'answer-shape' };
    for (const [label, list, bound] of [
      ['strengths', strengths, BOUNDS.strengths],
      ['limitations', limitations, BOUNDS.limitations],
      ['risks', risks, BOUNDS.risks],
    ]) {
      const listProblem = checkTextList(list, bound, BOUNDS.listItemChars, `${label}[${String(index)}]`);
      if (listProblem !== null) return { problem: 'answer-shape' };
    }
    const evidenceProblem = checkIdentifierList(evidenceIds, { limit: BOUNDS.evidenceIds, allowed: legal.get(profileId), label: 'evidenceIds' });
    if (evidenceProblem !== null) {
      return { problem: evidenceProblem.kind === 'not-supplied' ? 'answer-evidence-not-supplied' : 'answer-shape' };
    }
    accepted.push({
      profileId,
      summary,
      strengths: [...strengths],
      limitations: [...limitations],
      risks: [...risks],
      evidenceIds: [...evidenceIds],
    });
  }
  return { proposal: { cards: accepted, reason } };
}

/**
 * Extract the first top-level JSON value from the model's visible text.
 *
 * The model is instructed to answer with one bare JSON object. A single
 * fenced block is tolerated and stripped; anything else must already be the
 * object itself. Markdown prose around the object is refused rather than
 * scanned for, so a truncated or chatty answer never becomes a decision.
 */
export function extractJsonValue(text) {
  const trimmed = text.trim();
  if (trimmed.length === 0) return { problem: 'answer-empty' };
  let candidate = trimmed;
  const fence = /^```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n?```$/u.exec(trimmed);
  if (fence !== null) candidate = fence[1].trim();
  if (candidate.length === 0) return { problem: 'answer-empty' };
  if (candidate[0] !== '{') return { problem: 'answer-not-json' };
  try {
    return { value: JSON.parse(candidate) };
  } catch {
    return { problem: 'answer-invalid-json' };
  }
}

/** Map one terminal finish reason to a machine code, or `null` when it is clean. */
export function finishProblem(reason) {
  if (reason === undefined || reason === null) return 'answer-missing-finish';
  const kind = field(reason, 'kind');
  if (kind === 'stop') return null;
  if (kind === 'length' || kind === 'max-tokens' || kind === 'max_tokens') return 'answer-truncated';
  if (kind === 'tool-calls' || kind === 'tool_calls') return 'answer-tool-call';
  if (kind === 'aborted') return 'call-aborted';
  return 'call-failed';
}

/**
 * Resolve the model's structured answer to a validated decision or proposal.
 *
 * @returns `{ decision }`, `{ proposal }`, or `{ problem }`.
 */
export function resolveAnswer(operation, text, legal) {
  const extracted = extractJsonValue(text);
  if (extracted.problem !== undefined) return { problem: extracted.problem };
  return operation === 'select'
    ? validateSelectAnswer(extracted.value, legal)
    : validateMaintainAnswer(extracted.value, legal);
}

/**
 * Perform one bounded direct model call.
 *
 * The call carries no tool schema and no harness system prompt: only the
 * frozen instruction prefix and one user message. `prepareCall` first, so an
 * unsupported provider, model, or effort fails before any provider I/O.
 *
 * @param ctx - Cordis context carrying the native `llm` service.
 * @param request - normalized request (`operation`, `profiles`, `evidence`, ...).
 * @param limits - `{ timeoutMs }` for this call.
 * @returns `{ ok: true, ... }` or `{ ok: false, code, detail? }`.
 */
export async function callDecisionModel(ctx, request, { timeoutMs }) {
  const profile = request.profile;
  const started = performance.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error('decision call timed out')), timeoutMs);
  let sawText = false;
  let toolCall = false;
  let finish;
  let usage = null;
  let text = '';
  let reasoningBytes = 0;
  try {
    await waitForRoute(ctx, profile, controller.signal);
    // Two prepare passes: one to read the adapter's materialized defaults, one
    // that additionally requests the bounded short-JSON output. The prepared
    // handle refuses a call whose config differs from the config it resolved,
    // so the bounded config must be resolved rather than imposed afterwards.
    let resolvedCall;
    try {
      const probe = await ctx.llm.prepareCall({ provider: profile.provider, model: profile.model, reasoningEffort: profile.effort }, controller.signal);
      if (probe.config.reasoningEffort !== profile.effort) {
        return { ok: false, code: 'decision-effort-not-supported' };
      }
      const bounded = Math.min(probe.config.maxTokens ?? MAX_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS);
      resolvedCall = bounded === probe.config.maxTokens
        ? probe
        : await ctx.llm.prepareCall({ provider: profile.provider, model: profile.model, reasoningEffort: profile.effort, maxTokens: bounded }, controller.signal);
    } catch (error) {
      const code = field(error, 'code');
      return {
        ok: false,
        code: code === 'UNSUPPORTED_REASONING_EFFORT' ? 'decision-effort-not-supported' : 'decision-profile-unresolved',
        detail: errorDetail(error),
      };
    }
    const prepared = resolvedCall;
    // The call options are the prepared config plus only non-config request
    // fields. Adding or changing one of `provider`, `model`, `reasoningEffort`,
    // `temperature`, `maxTokens` or `stop` here would be refused by the
    // prepared handle, so sampling controls stay exactly as the harness
    // resolved them for this profile.
    const options = {
      ...prepared.config,
      system: instructionsFor(request.operation),
      messages: [{
        id: 'decision-request-1',
        role: 'user',
        content: [{ type: 'text', text: renderUserTurn(request.operation, request) }],
        source: { kind: 'user' },
      }],
      signal: controller.signal,
    };
    for await (const chunk of prepared.stream(options)) {
      if (chunk.type === 'text-delta') {
        sawText = true;
        text += chunk.text;
        if (Buffer.byteLength(text, 'utf8') > MAX_ANSWER_BYTES) {
          controller.abort(new Error('answer exceeded the output bound'));
          return { ok: false, code: 'answer-too-large' };
        }
      } else if (chunk.type === 'reasoning-delta') {
        reasoningBytes = Math.min(MAX_REASONING_BYTES, reasoningBytes + Buffer.byteLength(chunk.text, 'utf8'));
      } else if (chunk.type === 'tool-call-delta') {
        toolCall = true;
      } else if (chunk.type === 'usage') {
        usage = chunk.usage;
      } else if (chunk.type === 'finish') {
        finish = chunk.reason;
      }
    }
    if (controller.signal.aborted) return { ok: false, code: 'call-timeout' };
    if (toolCall) return { ok: false, code: 'answer-tool-call' };
    const finishCode = finishProblem(finish);
    if (finishCode !== null) return { ok: false, code: finishCode, ...(field(finish, 'failure') === undefined ? {} : { detail: errorDetail(field(finish, 'failure')) }) };
    if (!sawText) return { ok: false, code: 'answer-empty' };
    // Selection and maintenance have different legality: see legalMaintenanceIds.
    const legal = request.operation === 'select'
      ? legalCandidateIds(request.candidates, request.evidenceIndex)
      : legalMaintenanceIds(request.candidates, request.evidenceIndex);
    const resolved = resolveAnswer(request.operation, text, legal);
    if (resolved.problem !== undefined) return { ok: false, code: resolved.problem };
    return {
      ok: true,
      operation: request.operation,
      ...(resolved.decision === undefined ? {} : { decision: resolved.decision }),
      ...(resolved.proposal === undefined ? {} : { proposal: resolved.proposal }),
      resolvedConfig: {
        provider: prepared.config.provider,
        model: prepared.config.model,
        reasoningEffort: prepared.config.reasoningEffort ?? null,
      },
      usage: usage === null ? null : {
        inputTokens: usage.inputTokens,
        outputTokens: usage.outputTokens,
        totalTokens: usage.totalTokens ?? null,
        cacheReadTokens: usage.cacheReadTokens ?? null,
        cacheWriteTokens: usage.cacheWriteTokens ?? null,
        reasoningTokens: usage.reasoningTokens ?? null,
      },
      reasoningBytes,
      elapsedSeconds: Math.round(performance.now() - started) / 1000,
    };
  } catch (error) {
    if (controller.signal.aborted) return { ok: false, code: 'call-timeout' };
    return { ok: false, code: 'call-failed', detail: errorDetail(error) };
  } finally {
    clearTimeout(timer);
  }
}

/** The agent runner row this plugin requires to be disabled in the live tree. */
export const AGENT_RUNNER_ENTRY_ID = 'headless-runner';

/**
 * Second-layer safety check, from INSIDE the live tree.
 *
 * `scripts/decision.mjs` already refuses to boot unless `--dump-config` proves
 * this row is disabled. This re-proves it against the tree that actually
 * mounted, so a boot that diverged from the composed dump cannot produce a
 * decision. Failure is a refusal, never a silent downgrade: a decision call
 * must not happen in a process where the ordinary coding agent may be live.
 *
 * @returns `null` when the runner is provably disabled, else a machine code.
 */
export function checkAgentRunnerDisabled(ctx) {
  let entries;
  try {
    const loader = ctx.get('loader');
    if (loader === undefined || typeof loader.entries !== 'function') return 'runner-unverifiable';
    entries = [...loader.entries()];
  } catch {
    return 'runner-unverifiable';
  }
  const runner = entries.find((entry) => entry?.options?.id === AGENT_RUNNER_ENTRY_ID);
  if (runner === undefined) return 'runner-row-missing';
  const disabled = runner.disabled === true || runner.options?.disabled === true;
  return disabled ? null : 'runner-not-disabled';
}

/**
 * Wait, bounded, for the decision profile's provider route to register.
 *
 * A patch-inserted plugin races every other entry during tree activation, so
 * the adapter route may not exist yet when this plugin starts. This waits for
 * service availability only; it never retries a model call.
 */
export async function waitForRoute(ctx, profile, signal) {
  const deadline = performance.now() + ADAPTER_WAIT_MS;
  for (;;) {
    if (signal.aborted) throw new Error('decision call aborted while waiting for the provider route');
    if (ctx.llm.listProviders().some((entry) => entry.id === profile.provider)) return;
    if (performance.now() >= deadline) {
      const error = new Error(`no adapter registered for provider "${profile.provider}"`);
      error.code = 'NO_ADAPTER';
      throw error;
    }
    await new Promise((resolve) => setTimeout(resolve, ADAPTER_POLL_MS));
  }
}

/**
 * Machine-code-only failure detail.
 *
 * Provider and transport messages are deliberately NOT forwarded: they can
 * echo credentials, endpoints, prompts, or request identifiers into a document
 * that the Python board persists and the console renders. The stable `code` is
 * the routable fact; anything more belongs in a private log, not this envelope.
 */
function errorDetail(error) {
  if (error === null || error === undefined) return null;
  const code = typeof error === 'string' ? error : error.code;
  if (typeof code !== 'string' || code === '') return null;
  return { code: code.slice(0, 120) };
}

/** Read and parse the plugin's own input file. */
export function readRequest(inputFile) {
  let text;
  try {
    text = readFileSync(inputFile, 'utf8');
  } catch {
    return { problem: 'request-unreadable' };
  }
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    return { problem: 'request-invalid-json' };
  }
  const operation = field(value, 'operation');
  const profile = field(value, 'profile');
  if (operation !== 'select' && operation !== 'maintain') return { problem: 'request-operation' };
  if (profile === null || typeof profile !== 'object' || Array.isArray(profile)) return { problem: 'request-profile-invalid' };
  const provider = field(profile, 'provider');
  const model = field(profile, 'model');
  const effort = field(profile, 'effort');
  if (!isIdentifier(provider) || !isIdentifier(model) || !isIdentifier(effort)) return { problem: 'request-profile-invalid' };
  const normalized = normalizeCandidates(field(value, 'profiles'));
  if (normalized.problem !== null) return { problem: normalized.problem };
  const evidenceIndex = normalizeEvidence(field(value, 'evidence'));
  if (evidenceIndex.problem !== null) return { problem: evidenceIndex.problem };
  return {
    request: {
      operation,
      requestId: field(value, 'requestId'),
      profile: { provider, model, effort },
      tableRevision: field(value, 'tableRevision'),
      ...(field(value, 'task') === undefined ? {} : { task: field(value, 'task') }),
      profiles: field(value, 'profiles'),
      ...(field(value, 'cards') === undefined ? {} : { cards: field(value, 'cards') }),
      ...(field(value, 'preferences') === undefined ? {} : { preferences: field(value, 'preferences') }),
      ...(field(value, 'evidence') === undefined ? {} : { evidence: field(value, 'evidence') }),
      candidates: normalized.candidates,
      evidenceIndex: evidenceIndex.evidence,
    },
  };
}

/** Write one JSON document atomically, owner-only. */
export function writeJsonFile(file, value) {
  const text = `${JSON.stringify(value, null, 2)}\n`;
  const temporary = join(dirname(file), `.${String(process.pid)}.decision-result.tmp`);
  writeFileSync(temporary, text, { mode: 0o600, flag: 'wx' });
  renameSync(temporary, file);
}

/**
 * Mount the one-shot decision call.
 *
 * The plugin writes exactly one of `result.json` (success) or `error.json`
 * (structured failure) under the run directory given by the patch, then exits
 * the process. It never writes to stdout or stderr, so the parent keeps a
 * clean channel.
 */
export function apply(ctx, config) {
  const inputFile = config?.inputFile;
  const outputDir = config?.outputDir;
  if (typeof inputFile !== 'string' || inputFile === '') return;
  if (typeof outputDir !== 'string' || outputDir === '') return;
  const timeoutMs = Number.isSafeInteger(config?.timeoutMs) && config.timeoutMs > 0 ? config.timeoutMs : 30_000;
  const resultFile = join(outputDir, 'result.json');
  const errorFile = join(outputDir, 'error.json');
  void (async () => {
    let outcome;
    const read = readRequest(inputFile);
    const runnerProblem = read.problem === undefined ? checkAgentRunnerDisabled(ctx) : null;
    if (runnerProblem !== null) {
      outcome = { status: 'error', code: 'decision-profile-unsafe', detail: { code: runnerProblem } };
    } else if (read.problem !== undefined) {
      outcome = { status: 'error', code: read.problem, detail: null };
    } else {
      const called = await callDecisionModel(ctx, read.request, { timeoutMs });
      outcome = called.ok
        ? {
          status: 'ok',
          operation: read.request.operation,
          ...(called.decision === undefined ? {} : { decision: called.decision }),
          ...(called.proposal === undefined ? {} : { proposal: called.proposal }),
          resolvedConfig: called.resolvedConfig,
          usage: called.usage,
          reasoningBytes: called.reasoningBytes,
          elapsedSeconds: called.elapsedSeconds,
        }
        : { status: 'error', code: called.code, detail: called.detail ?? null };
    }
    try {
      mkdirSync(outputDir, { recursive: true, mode: 0o700 });
      writeJsonFile(outcome.status === 'ok' ? resultFile : errorFile, outcome);
    } catch {
      // A write failure leaves both files absent, which the parent reports as
      // an unconfirmed child result rather than as an invented outcome.
    }
    process.exit(0);
  })();
}
