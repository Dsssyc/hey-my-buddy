/**
 * deepseek-delegate decision plugin.
 *
 * A tiny, self-contained Cordis plugin mounted by `scripts/decision.mjs` into
 * an existing dsh profile through a temporary `--patch` overlay. It performs
 * one bounded tool-free decision call, and at most one corrective retry when
 * the visible answer fails format/answer validation, then writes one validated
 * result file and exits. It is not an agent: it never creates a session, never
 * asks for a system prompt, never supplies a tool schema, and never retries
 * anything except that one bounded correction.
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
 * @module harnesses/dsh/plugins/decision
 */
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { performance } from 'node:perf_hooks';
import { dirname, join } from 'node:path';
import { correctionMessage, instructionsFor, renderUserTurn } from '../scripts/decision-prompt.mjs';
import { POLICY_FACTS_MISMATCH, derivePolicyFacts, jsonEqual, validateDecision } from '../scripts/selection-policy.mjs';

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

/**
 * Bounded corrective retry: one answer-format refusal may be corrected once
 * inside the same tool-free flow, so the helper makes at most two model calls
 * under one deadline, one frozen request, and one cancel ownership.
 */
export const MAX_CORRECTIONS = 1;

/**
 * The only refusal codes that may consume the one corrective retry: a visible
 * answer that was present but did not satisfy the documented format or answer
 * validation. Quota, provider, transport, request, configuration, deadline,
 * cancellation and truncation/tool-call failures are never retried, and a legal
 * null abstention is a success rather than a failure.
 */
export const RETRYABLE_ANSWER_CODES = Object.freeze([
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

/** Whether one refusal may be corrected by the bounded second call. */
export function isRetryableAnswerCode(code) {
  return RETRYABLE_ANSWER_CODES.includes(code);
}

/** Cap on one redacted visible-answer diagnostic, in UTF-8 bytes. */
export const MAX_DIAGNOSTIC_TEXT_BYTES = 2_048;
/** Cap on retained failed-attempt diagnostics (the flow makes at most two attempts). */
export const MAX_DIAGNOSTIC_FAILURES = 2;

/** Bounded string/list limits mirrored from the selection prompt contract. */
export const BOUNDS = Object.freeze({
  reasonChars: 2_000,
  evidenceIds: 50,
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
 * Validate one `select` answer's base shape against the request's legal
 * candidate set. The typed `policyCheck`/`support` acknowledgment is validated
 * separately by {@link validateDecision} in `scripts/selection-policy.mjs`.
 * @returns `{ decision }` or `{ problem }` with a machine code.
 */
export function validateSelectAnswer(answer, legal) {
  const record = asRecord(answer);
  if (record === null) return { problem: 'answer-shape' };
  const extra = unexpectedKey(record, ['profileId', 'reason', 'evidenceIds', 'policyCheck', 'support']);
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

/** The only field names a redacted answer diagnostic may keep verbatim. */
const SAFE_ANSWER_KEYS = new Set(['profileId', 'reason', 'evidenceIds', 'policyCheck', 'support', 'cardProfileIds', 'annotationProfileIds']);
/** Values that are already typed outcomes rather than free text. */
const SAFE_ANSWER_ENUMS = new Set(['none', 'matched', 'alternative', 'fallback']);
/** Stand-in for one value or field name that must not leave this process. */
const REDACTED = '<redacted>';
const UNKNOWN_FIELD = '<unknown-field>';
const MAX_ANSWER_KEYS = 16;
const MAX_ANSWER_ENTRIES = 16;
const MAX_ANSWER_DEPTH = 3;

/** Every identifier the frozen request actually supplied; the only strings kept verbatim. */
function collectSuppliedIds(request) {
  const ids = new Set();
  const add = (value) => {
    if (typeof value === 'string' && value.length > 0) ids.add(value);
  };
  add(field(request, 'requestId'));
  for (const key of ['provider', 'model', 'effort', 'adapter']) add(field(request.profile, key));
  const collections = ['profiles', 'cards', 'preferences', 'annotations', 'evidence'];
  for (const name of collections) {
    const entries = field(request, name);
    if (!Array.isArray(entries)) continue;
    for (const entry of entries) {
      add(field(entry, 'profileId'));
      add(field(entry, 'evidenceId'));
      const declared = field(entry, 'evidenceIds');
      if (Array.isArray(declared)) for (const value of declared) add(value);
    }
  }
  return ids;
}

/**
 * Project one parsed answer value into a redacted, structurally bounded shape.
 *
 * Field names that are not part of the documented answer schema are replaced by
 * plain placeholders, free text is replaced by {@link REDACTED}, and only
 * enums, booleans, numbers, null, and identifiers the request actually supplied
 * survive. `policyCheck` is represented as ignored data because the program
 * never reads it.
 */
function projectAnswerValue(value, supplied, depth, state) {
  if (typeof value === 'string') {
    return supplied.has(value) || SAFE_ANSWER_ENUMS.has(value) ? value : REDACTED;
  }
  if (value === null || typeof value === 'number' || typeof value === 'boolean') return value;
  if (depth >= MAX_ANSWER_DEPTH) {
    state.truncated = true;
    return REDACTED;
  }
  if (Array.isArray(value)) {
    if (value.length > MAX_ANSWER_ENTRIES) state.truncated = true;
    return value.slice(0, MAX_ANSWER_ENTRIES).map((entry) => projectAnswerValue(entry, supplied, depth + 1, state));
  }
  if (typeof value !== 'object') return REDACTED;
  const projected = {};
  const keys = Object.keys(value);
  if (keys.length > MAX_ANSWER_KEYS) state.truncated = true;
  let unknown = 0;
  for (const key of keys.slice(0, MAX_ANSWER_KEYS)) {
    let name;
    if (SAFE_ANSWER_KEYS.has(key)) {
      name = key;
    } else {
      unknown += 1;
      name = unknown === 1 ? UNKNOWN_FIELD : `${UNKNOWN_FIELD}-${String(unknown)}`;
    }
    projected[name] = name === 'policyCheck'
      ? '<ignored>'
      : projectAnswerValue(value[key], supplied, depth + 1, state);
  }
  return projected;
}

/** Cut one string at a UTF-8 character boundary so it never exceeds `maxBytes`. */
function truncateUtf8(text, maxBytes) {
  const buffer = Buffer.from(text, 'utf8');
  if (buffer.length <= maxBytes) return text;
  let end = maxBytes;
  while (end > 0 && (buffer[end] & 0xc0) === 0x80) end -= 1;
  return buffer.subarray(0, end).toString('utf8');
}

/**
 * One bounded, redacted snapshot of a visible model answer.
 *
 * `text` is a structural projection capped at {@link MAX_DIAGNOSTIC_TEXT_BYTES};
 * `sha256` and `bytes` describe the exact visible answer; a raw answer that
 * cannot be parsed is represented only by that hash/byte pair and a placeholder,
 * so no arbitrary text can cross into a persisted envelope. Reasoning text,
 * provider messages and credentials never enter this record.
 */
export function boundedAnswerDiagnostic(text, supplied = new Set()) {
  const raw = typeof text === 'string' ? text : '';
  const bytes = Buffer.byteLength(raw, 'utf8');
  const sha256 = createHash('sha256').update(raw, 'utf8').digest('hex');
  const state = { truncated: false };
  let rendered;
  if (raw.trim().length === 0) {
    rendered = '<empty-answer>';
  } else {
    const extracted = extractJsonValue(raw);
    rendered = extracted.problem === undefined
      ? JSON.stringify(projectAnswerValue(extracted.value, supplied, 0, state))
      : '<unparsed-answer>';
  }
  if (Buffer.byteLength(rendered, 'utf8') > MAX_DIAGNOSTIC_TEXT_BYTES) {
    rendered = truncateUtf8(rendered, MAX_DIAGNOSTIC_TEXT_BYTES);
    state.truncated = true;
  }
  return { text: rendered, sha256, bytes, truncated: state.truncated, redacted: true };
}

/** Append one failed attempt to the bounded diagnostics record. */
function recordAttemptFailure(diagnostics, code, text, supplied) {
  if (diagnostics.failures.length >= MAX_DIAGNOSTIC_FAILURES) return;
  diagnostics.failures.push({
    code: String(code).slice(0, 120),
    answer: boundedAnswerDiagnostic(text, supplied),
  });
}

/**
 * Resolve the model's structured answer to a validated selection decision.
 *
 * When a `policy` context is supplied, the answer's typed `policyCheck` and
 * `support` are additionally validated against the program-derived facts; a
 * violation carries a stable `policy-*` machine code that Python settles
 * needs-host. The qualitative reason text is never parsed.
 *
 * @returns `{ decision }` or `{ problem, detail? }`.
 */
export function resolveAnswer(operation, text, legal, policy) {
  const extracted = extractJsonValue(text);
  if (extracted.problem !== undefined) return { problem: extracted.problem };
  const base = validateSelectAnswer(extracted.value, legal);
  if (base.problem !== undefined) return base;
  if (policy === undefined) return base;
  // The typed check reads the answer as the model wrote it (policyCheck and
  // support included); the returned decision keeps the validated base fields.
  const verdict = validateDecision(extracted.value, policy.facts, policy.routingPreferences, policy.scope);
  if (!verdict.ok) return { problem: verdict.code, detail: verdict.detail };
  return { decision: { ...base.decision, policyCheck: verdict.policyCheck, support: verdict.support } };
}

/**
 * Resolve one prepared, bounded call handle for the decision profile.
 *
 * `prepareCall` may return a one-use handle, so every attempt — the first and
 * the bounded corrective second — resolves its own handle from the same frozen
 * profile. Two prepare passes: one to read the adapter's materialized defaults,
 * one that additionally requests the bounded short-JSON output. The prepared
 * handle refuses a call whose config differs from the config it resolved, so
 * the bounded config must be resolved rather than imposed afterwards.
 *
 * @returns `{ ok: true, prepared }` or `{ ok: false, code, detail? }`.
 */
async function prepareDecisionCall(ctx, profile, signal) {
  try {
    const probe = await ctx.llm.prepareCall({ provider: profile.provider, model: profile.model, reasoningEffort: profile.effort }, signal);
    if (probe.config.reasoningEffort !== profile.effort) {
      return { ok: false, code: 'decision-effort-not-supported' };
    }
    const bounded = Math.min(probe.config.maxTokens ?? MAX_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS);
    const prepared = bounded === probe.config.maxTokens
      ? probe
      : await ctx.llm.prepareCall({ provider: profile.provider, model: profile.model, reasoningEffort: profile.effort, maxTokens: bounded }, signal);
    return { ok: true, prepared };
  } catch (error) {
    const code = field(error, 'code');
    return {
      ok: false,
      code: code === 'UNSUPPORTED_REASONING_EFFORT' ? 'decision-effort-not-supported' : 'decision-profile-unresolved',
      detail: errorDetail(error),
    };
  }
}

/**
 * Stream one bounded attempt and collect its visible text, usage and finish facts.
 *
 * The call options are the prepared config plus only non-config request fields.
 * A corrective second attempt appends one deterministic user turn carrying the
 * stable refusal code and a fixed correction note; the frozen system prefix and
 * the frozen request payload stay byte-identical across both attempts.
 *
 * The returned `text` is the attempt's visible answer even when it was refused,
 * so the caller can record a bounded redacted diagnostic for it.
 *
 * @returns `{ ok: true, text, usage, reasoningBytes }` or `{ ok: false, code, text, detail? }`.
 */
async function streamDecisionAnswer(request, prepared, controller, correction) {
  const signal = controller.signal;
  let sawText = false;
  let toolCall = false;
  let finish;
  let usage = null;
  let text = '';
  let reasoningBytes = 0;
  const messages = [{
    id: 'decision-request-1',
    role: 'user',
    content: [{ type: 'text', text: renderUserTurn(request.operation, request) }],
    source: { kind: 'user' },
  }];
  if (correction !== null) {
    messages.push({
      id: 'decision-correction-2',
      role: 'user',
      content: [{ type: 'text', text: correctionMessage(correction) }],
      source: { kind: 'user' },
    });
  }
  // The call options are the prepared config plus only non-config request
  // fields. Adding or changing one of `provider`, `model`, `reasoningEffort`,
  // `temperature`, `maxTokens` or `stop` here would be refused by the prepared
  // handle, so sampling controls stay exactly as the harness resolved them.
  const options = {
    ...prepared.config,
    system: instructionsFor(request.operation),
    messages,
    signal,
  };
  for await (const chunk of prepared.stream(options)) {
    if (chunk.type === 'text-delta') {
      sawText = true;
      text += chunk.text;
      if (Buffer.byteLength(text, 'utf8') > MAX_ANSWER_BYTES) {
        controller.abort(new Error('answer exceeded the output bound'));
        return { ok: false, code: 'answer-too-large', text, reasoningBytes };
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
  if (signal.aborted) return { ok: false, code: 'call-timeout', text, reasoningBytes };
  if (toolCall) return { ok: false, code: 'answer-tool-call', text, reasoningBytes };
  const finishCode = finishProblem(finish);
  if (finishCode !== null) {
    return {
      ok: false,
      code: finishCode,
      text,
      reasoningBytes,
      ...(field(finish, 'failure') === undefined ? {} : { detail: errorDetail(field(finish, 'failure')) }),
    };
  }
  if (!sawText) return { ok: false, code: 'answer-empty', text, reasoningBytes };
  return { ok: true, text, usage, reasoningBytes };
}

/**
 * Perform the bounded direct model call flow for one decision.
 *
 * The flow carries no tool schema and no harness system prompt: only the frozen
 * instruction prefix, one frozen request payload, and — for the one permitted
 * correction — one small static note naming the stable refusal code. The whole
 * flow shares one deadline, one abort signal, one frozen profile and one frozen
 * request; only an answer-format/validation refusal may consume the single
 * corrective retry, and every call re-prepares its own one-use handle.
 *
 * @param ctx - Cordis context carrying the native `llm` service.
 * @param request - normalized request (`operation`, `profiles`, `evidence`, ...).
 * @param limits - `{ timeoutMs }` for the whole call flow.
 * @returns `{ ok: true, ..., diagnostics }` or `{ ok: false, code, detail?, diagnostics }`.
 */
export async function callDecisionModel(ctx, request, { timeoutMs }) {
  const profile = request.profile;
  const started = performance.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error('decision call timed out')), timeoutMs);
  const diagnostics = { calls: 0, failures: [] };
  const supplied = collectSuppliedIds(request);
  let reasoningBytes = 0;
  try {
    // Independent re-derivation of the request's policy facts from its own table
    // data, BEFORE any provider I/O: a payload whose facts disagree with the
    // table they claim to describe is a caller error, never a model call.
    const facts = derivePolicyFacts({
      profiles: request.profiles,
      routingPreferences: request.routingPreferences ?? [],
      preferences: request.preferences ?? [],
      hardConstraints: field(request.policyFacts, 'hardConstraints') ?? {},
    });
    if (!jsonEqual(facts, request.policyFacts)) {
      return {
        ok: false,
        code: POLICY_FACTS_MISMATCH,
        detail: { code: POLICY_FACTS_MISMATCH, description: 'the request policy facts disagree with the table they were derived from' },
        diagnostics,
      };
    }
    // Card/annotation support is scoped to actually supplied references.
    const suppliedIds = (entries) => new Set((Array.isArray(entries) ? entries : [])
      .map((entry) => field(entry, 'profileId'))
      .filter((value) => typeof value === 'string' && value.length > 0));
    const policy = {
      facts,
      routingPreferences: Array.isArray(request.routingPreferences) ? request.routingPreferences : [],
      scope: { cardProfileIds: suppliedIds(request.cards), annotationProfileIds: suppliedIds(request.annotations) },
    };
    await waitForRoute(ctx, profile, controller.signal);
    let correction = null;
    for (let attempt = 0; attempt <= MAX_CORRECTIONS; attempt += 1) {
      // The overall deadline and cancel ownership are fixed before the first
      // attempt; a correction shares them and never restarts either.
      if (controller.signal.aborted) return { ok: false, code: 'call-timeout', diagnostics };
      const resolvedCall = await prepareDecisionCall(ctx, profile, controller.signal);
      if (!resolvedCall.ok) return { ...resolvedCall, diagnostics };
      diagnostics.calls += 1;
      const streamed = await streamDecisionAnswer(request, resolvedCall.prepared, controller, correction);
      reasoningBytes = Math.min(MAX_REASONING_BYTES * (attempt + 1), reasoningBytes + (streamed.reasoningBytes ?? 0));
      if (!streamed.ok) {
        recordAttemptFailure(diagnostics, streamed.code, streamed.text, supplied);
        if (attempt < MAX_CORRECTIONS && !controller.signal.aborted && isRetryableAnswerCode(streamed.code)) {
          correction = streamed.code;
          continue;
        }
        return {
          ok: false,
          code: streamed.code,
          ...(streamed.detail === undefined ? {} : { detail: streamed.detail }),
          diagnostics,
        };
      }
      // Only explicitly enabled and available profiles are legal candidates.
      const legal = legalCandidateIds(request.candidates, request.evidenceIndex);
      const resolved = resolveAnswer(request.operation, streamed.text, legal, policy);
      if (resolved.problem !== undefined) {
        recordAttemptFailure(diagnostics, resolved.problem, streamed.text, supplied);
        if (attempt < MAX_CORRECTIONS && isRetryableAnswerCode(resolved.problem)) {
          correction = resolved.problem;
          continue;
        }
        return {
          ok: false,
          code: resolved.problem,
          ...(resolved.detail === undefined ? {} : { detail: { code: resolved.problem, description: String(resolved.detail).slice(0, 500) } }),
          diagnostics,
        };
      }
      return {
        ok: true,
        operation: request.operation,
        decision: resolved.decision,
        resolvedConfig: {
          provider: resolvedCall.prepared.config.provider,
          model: resolvedCall.prepared.config.model,
          reasoningEffort: resolvedCall.prepared.config.reasoningEffort ?? null,
        },
        usage: streamed.usage === null ? null : {
          inputTokens: streamed.usage.inputTokens,
          outputTokens: streamed.usage.outputTokens,
          totalTokens: streamed.usage.totalTokens ?? null,
          cacheReadTokens: streamed.usage.cacheReadTokens ?? null,
          cacheWriteTokens: streamed.usage.cacheWriteTokens ?? null,
          reasoningTokens: streamed.usage.reasoningTokens ?? null,
        },
        reasoningBytes,
        elapsedSeconds: Math.round(performance.now() - started) / 1000,
        diagnostics,
      };
    }
    // Unreachable: the loop always returns on its last iteration.
    return { ok: false, code: 'call-failed', diagnostics };
  } catch (error) {
    if (controller.signal.aborted) return { ok: false, code: 'call-timeout', diagnostics };
    return { ok: false, code: 'call-failed', detail: errorDetail(error), diagnostics };
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
  if (operation !== 'select') return { problem: 'request-operation' };
  if (profile === null || typeof profile !== 'object' || Array.isArray(profile)) return { problem: 'request-profile-invalid' };
  const provider = field(profile, 'provider');
  const model = field(profile, 'model');
  const effort = field(profile, 'effort');
  const adapter = field(profile, 'adapter');
  if (!isIdentifier(provider) || !isIdentifier(model) || !isIdentifier(effort)) return { problem: 'request-profile-invalid' };
  const normalized = normalizeCandidates(field(value, 'profiles'));
  if (normalized.problem !== null) return { problem: normalized.problem };
  const evidenceIndex = normalizeEvidence(field(value, 'evidence'));
  if (evidenceIndex.problem !== null) return { problem: evidenceIndex.problem };
  const policyFacts = field(value, 'policyFacts');
  if (policyFacts === null || typeof policyFacts !== 'object' || Array.isArray(policyFacts)) {
    return { problem: 'request-policy-invalid' };
  }
  return {
    request: {
      operation,
      requestId: field(value, 'requestId'),
      profile: { ...(adapter === undefined ? {} : { adapter }), provider, model, effort },
      tableRevision: field(value, 'tableRevision'),
      ...(field(value, 'task') === undefined ? {} : { task: field(value, 'task') }),
      profiles: field(value, 'profiles'),
      ...(field(value, 'cards') === undefined ? {} : { cards: field(value, 'cards') }),
      ...(field(value, 'preferences') === undefined ? {} : { preferences: field(value, 'preferences') }),
      ...(field(value, 'evidence') === undefined ? {} : { evidence: field(value, 'evidence') }),
      ...(field(value, 'annotations') === undefined ? {} : { annotations: field(value, 'annotations') }),
      ...(field(value, 'routingPreferences') === undefined ? {} : { routingPreferences: field(value, 'routingPreferences') }),
      policyFacts,
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
 * Mount the one-shot decision call flow.
 *
 * The plugin writes exactly one of `result.json` (success) or `error.json`
 * (structured failure) under the run directory given by the patch, then exits
 * the process. Both documents carry the bounded, redacted `diagnostics` record
 * described by {@link callDecisionModel}. It never writes to stdout or stderr,
 * so the parent keeps a clean channel.
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
    const diagnostics = { calls: 0, failures: [] };
    const read = readRequest(inputFile);
    const runnerProblem = read.problem === undefined ? checkAgentRunnerDisabled(ctx) : null;
    if (runnerProblem !== null) {
      outcome = { status: 'error', code: 'decision-profile-unsafe', detail: { code: runnerProblem }, diagnostics };
    } else if (read.problem !== undefined) {
      outcome = { status: 'error', code: read.problem, detail: null, diagnostics };
    } else {
      const called = await callDecisionModel(ctx, read.request, { timeoutMs });
      outcome = called.ok
        ? {
          status: 'ok',
          operation: read.request.operation,
          decision: called.decision,
          resolvedConfig: called.resolvedConfig,
          usage: called.usage,
          reasoningBytes: called.reasoningBytes,
          elapsedSeconds: called.elapsedSeconds,
          diagnostics: called.diagnostics,
        }
        : { status: 'error', code: called.code, detail: called.detail ?? null, diagnostics: called.diagnostics };
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
