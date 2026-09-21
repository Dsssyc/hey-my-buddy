/**
 * Prompt assembly for the single-decision adapter.
 *
 * The model prompt is a stable, versioned instruction prefix followed by one
 * deterministic JSON payload. Nothing request-local (request id, table
 * revision, task text, clock) is interpolated into the prefix, so the same
 * decision profile can reuse the same cached prefix across requests while the
 * variable part stays at the end.
 *
 * This module is deliberately pure: no filesystem, process, or harness
 * access. It is imported by the in-process dsh plugin and by the focused
 * tests, so the prompt shape is verified without booting a harness.
 *
 * @module deepseek-delegate/scripts/decision-prompt
 */

/** Bumped whenever the instruction prefix or payload shape changes meaning. */
export const PROMPT_VERSION = 1;

/** Hard cap on the rendered payload JSON, in UTF-8 bytes. */
export const MAX_PAYLOAD_BYTES = 262_144;

/**
 * Instruction prefix. One literal string per operation so the prefix is a pure
 * function of the operation and never of the request.
 *
 * The rules below are the contract the adapter enforces after the call; they
 * are stated to the model so a refusal or a malformed answer is the model's
 * choice, not a surprise. The model has no tools, no repository context, and
 * no authority to change preferences, authorization, or persisted state.
 */
const SELECT_INSTRUCTIONS = `You are a bounded decision selector for a local coding-agent task board.

Return exactly one JSON object and nothing else. Do not use Markdown code fences, prose, or comments.

Output schema:
{"profileId": <string|null>, "reason": <string>, "evidenceIds": <string[]>}

Rules:
1. "profileId" MUST be the "profileId" of exactly one entry of the supplied "profiles" array, or null to abstain.
2. Never invent a profile, model, provider, or effort. A profile that is absent, disabled, or unavailable is not a candidate.
3. "evidenceIds" MUST be a subset of the "evidenceIds" values supplied for the chosen profile. Never invent evidence ids.
4. "reason" is a short factual justification (at most 500 characters) grounded in the supplied profiles, cards, preferences, evidence, and task.
5. The supplied "preferences" are constraints: a "pin" or "exclude" entry narrows the legal candidate set, and a soft "prefer" only orders legal candidates. Never claim to change a preference.
6. You propose only; you never grant execution permission, authorization, or acceptance.
7. Model prose never means the task succeeded or was accepted.
8. Abort with {"profileId": null, "reason": "...", "evidenceIds": []} when no candidate is clearly better supported than the others, when the request is ambiguous, or when the supplied evidence is insufficient. Abstention is a correct answer, not a failure.`;

const MAINTAIN_INSTRUCTIONS = `You are a bounded evaluation maintainer for a local coding-agent task board.

Return exactly one JSON object and nothing else. Do not use Markdown code fences, prose, or comments.

Output schema:
{"cards": [{"profileId": <string>, "summary": <string>, "strengths": <string[]>, "limitations": <string[]>, "risks": <string[]>, "evidenceIds": <string[]>}], "reason": <string>}

Rules:
1. Every "profileId" MUST be the "profileId" of an entry of the supplied "profiles" array. Never invent a profile, model, provider, or effort. A profile that is disabled or unavailable in the current table may still receive a card: record why it is excluded rather than skipping it.
2. A card is either an update of a profile already present in the supplied "cards" array or the first card for a supplied profile that has none yet. Both are legal. Creating a profile id that is not in "profiles" is not.
3. Every id in every "evidenceIds" MUST be one of the supplied evidence ids for that same profile. Never invent evidence.
4. "summary" is at most 1000 characters; each list holds at most 10 short items of at most 300 characters.
5. Keep every unresolved item that the supplied cards still report. Do not delete an unresolved limitation or risk because a later observation succeeded, and do not promote a single observation into a general conclusion.
6. State only what the supplied evidence supports. Unknown stays unknown; do not fill a gap with a guess or a neutral rating.
7. You propose only, and only within the card fields above. Never change preferences, authorization, execution permission, task acceptance, or any other persisted field.
8. Model prose never means the task succeeded or was accepted.
9. Return {"cards": [], "reason": "..."} when no evidence-supported change is justified. An empty patch is a correct answer, not a failure.`;

/** The frozen instruction prefix for one operation. */
export function instructionsFor(operation) {
  if (operation === 'select') return SELECT_INSTRUCTIONS;
  if (operation === 'maintain') return MAINTAIN_INSTRUCTIONS;
  throw new TypeError(`unknown decision operation: ${String(operation)}`);
}

/** Stable key order for the payload so serialization is deterministic. */
/**
 * Stable key order for the payload so serialization is deterministic.
 *
 * The CURRENT TABLE (profiles, cards, preferences, evidence) comes before the
 * per-request remainder (task, requestId). That is what makes the shared table
 * snapshot the reusable prefix: within one table revision every request
 * renders the same leading bytes, and only the small varying tail is new.
 * `requestId` is deliberately last — it is unique per request and must never
 * sit in front of content that can be reused.
 */
const SELECT_KEYS = ['operation', 'profile', 'tableRevision', 'profiles', 'cards', 'preferences', 'evidence', 'task', 'requestId'];
const MAINTAIN_KEYS = ['operation', 'profile', 'tableRevision', 'profiles', 'cards', 'preferences', 'evidence', 'requestId'];

/**
 * Build the deterministic payload object for one request.
 *
 * Key order is fixed by the operation (never by input object order), so two
 * runs over equal requests render byte-identical payloads. Array order is the
 * caller's: the Python owner supplies the current table in its own
 * deterministic order and this adapter never re-sorts it.
 */
export function buildPayload(operation, request) {
  const keys = operation === 'select' ? SELECT_KEYS : MAINTAIN_KEYS;
  const payload = {};
  for (const key of keys) {
    const value = request[key];
    if (value === undefined) continue;
    payload[key] = value;
  }
  return payload;
}

/**
 * Render the whole user turn: the deterministic payload as compact JSON.
 *
 * @throws when the payload exceeds {@link MAX_PAYLOAD_BYTES}; the caller
 * reports that as an input error before any model call.
 */
export function renderUserTurn(operation, request) {
  const payload = buildPayload(operation, request);
  const text = JSON.stringify(payload);
  const bytes = Buffer.byteLength(text, 'utf8');
  if (bytes > MAX_PAYLOAD_BYTES) {
    const error = new Error(`decision payload is ${bytes} bytes, above the ${MAX_PAYLOAD_BYTES}-byte bound`);
    error.code = 'payload-too-large';
    throw error;
  }
  return text;
}
