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
 * @module harnesses/dsh/scripts/decision-prompt
 */

/** Bumped whenever the instruction prefix or payload shape changes meaning. */
export const PROMPT_VERSION = 6;

/** Hard cap on the rendered payload JSON, in UTF-8 bytes. */
export const MAX_PAYLOAD_BYTES = 262_144;

/**
 * Instruction prefix for the one bounded selection prompt. It is a literal
 * string, never a function of the request.
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
4. "reason" is a short factual justification (at most 500 characters) grounded in the supplied profiles, cards, preferences, annotations, evidence, task routing preferences, and task.
5. The supplied "preferences" are constraints: a "pin" or "exclude" entry narrows the legal candidate set, and a soft "prefer" only orders legal candidates. Never claim to change a preference.
6. Apply "routingPreferences" only to this request. Each is a soft preference over its "match" fields; consider entries in order. If no legal candidate matches, choose another legal candidate when justified and explain the fallback. They never override hard candidate filters or published pins/excludes.
7. Model prose never means the task succeeded or was accepted.
8. A missing card or annotation means unknown capability evidence, not inability. Use declared profile capabilities for compatibility; never infer that DSH or another harness lacks ordinary coding tools because cards are absent.
9. A profile's adapter names the harness that will run the Buddy, not the codebase or files it may edit. A task about DSH, ZCode, Codex or another harness does not require that harness unless the legal candidates were already filtered by an explicit hard constraint. Do not invent such a restriction in the reason.
10. When a legal preferred profile fits the task's recorded conditions, honor that preference unless concrete supplied capability or evidence favors another candidate. Explain any deviation using the supplied facts.
11. Abort with {"profileId": null, "reason": "...", "evidenceIds": []} when no candidate is clearly better supported than the others or the request is ambiguous. Abstention is a correct answer, not a failure.`;

/** The frozen selection instruction prefix. */
export function instructionsFor(operation) {
  if (operation === 'select') return SELECT_INSTRUCTIONS;
  throw new TypeError(`unknown decision operation: ${String(operation)}`);
}

/**
 * Stable key order for the payload so serialization is deterministic.
 *
 * The CURRENT TABLE (profiles, cards, preferences, evidence) comes before the
 * per-request remainder (task, requestId). That is what makes the shared table
 * snapshot the reusable prefix: within one table revision every request
 * renders the same leading bytes, and only the small varying tail is new.
 * `requestId` is deliberately last — it is unique per request and must never
 * sit in front of content that can be reused.
 * Revision metadata follows the table too, so a no-content publication keeps
 * the reusable bytes intact and a card edit can still reuse preceding profiles.
 */
const SELECT_KEYS = ['operation', 'profile', 'profiles', 'cards', 'preferences', 'annotations', 'evidence', 'tableRevision', 'routingPreferences', 'task', 'requestId'];

/**
 * Build the deterministic payload object for one selection request.
 *
 * Key order is fixed by the selection contract (never by input object order),
 * so two runs over equal requests render byte-identical payloads. Array order
 * is the caller's: the Python owner supplies the current table in its own
 * deterministic order and this adapter never re-sorts it.
 */
export function buildPayload(operation, request) {
  if (operation !== 'select') throw new TypeError(`unknown decision operation: ${String(operation)}`);
  const payload = {};
  for (const key of SELECT_KEYS) {
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
