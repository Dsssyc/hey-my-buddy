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
export const PROMPT_VERSION = 7;

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
{"profileId": <string|null>, "reason": <string>, "evidenceIds": <string[]>, "policyCheck": <object|null>, "support": {"cardProfileIds": <string[]>, "annotationProfileIds": <string[]>}}

Rules:
1. "profileId" MUST be the "profileId" of exactly one entry of the supplied "profiles" array, or null to abstain. An abstention carries "policyCheck": null, "evidenceIds": [] and empty "support" arrays.
2. Never invent a profile, model, provider, or effort. A profile that is absent, disabled, or unavailable is not a candidate.
3. "evidenceIds" MUST be a subset of the "evidenceIds" values supplied for the chosen profile. Never invent evidence ids.
4. "reason" is a short factual justification (at most 500 characters) grounded in the supplied profiles, cards, preferences, annotations, evidence, task routing preferences, and task.
5. The supplied "preferences" are constraints: a "pin" or "exclude" entry narrows the legal candidate set, and a soft "prefer" only orders legal candidates. Never claim to change a preference.
6. "policyCheck" is a typed acknowledgment of the request's program-computed "policyFacts", not an opinion. Copy "hardConstraints" and "taskPreference"."ruleIndex" exactly as supplied. Set "taskPreference"."outcome" to "none" when the request carries no routingPreferences, "fallback" when routingPreferences exist but none legally matches a candidate, "matched" when your selection is one of "taskPreference"."matchingProfileIds", or "alternative" when a legal match exists and you selected another candidate. Set "userPreference" to "none" when "userPreferredProfileIds" is empty, "matched" when your selection is in it, or "alternative" when it is non-empty and you selected outside it. Only these values exist: there is no avoid, exclude, or reject outcome, and the program verifies your acknowledgment against its own facts.
7. Task routing preferences are POSITIVE: a rule whose match fields equal a legal candidate asks you to prefer that candidate for this request. Such a rule is never an avoid or exclude instruction, applies only to this request, and never overrides hard candidate filters or published pins/excludes.
8. "support" arrays may cite only real supplied card or annotation profileIds, and only for your selected profile or the effective preferred candidates ("taskPreference"."matchingProfileIds" plus "userPreferredProfileIds"); at most 32 unique ids per array. When you report an "alternative" outcome for the task preference or the user preference, cite at least one actually supplied evidence id for your selection, or a nonempty eligible support array. A "matched", "fallback", or "none" selection needs no fabricated references.
9. Model prose never means the task succeeded or was accepted.
10. A missing card or annotation means unknown capability evidence, not inability. Use declared profile capabilities for compatibility; never infer that DSH or another harness lacks ordinary coding tools because cards are absent.
11. A profile's adapter names the harness that will run the Buddy, not the codebase or files it may edit. Working on the source, scripts, or tests of DSH, ZCode, Codex or another harness does not require running on that harness, unless the legal candidates were already narrowed by an explicit hard constraint. Do not invent such a restriction in the reason.
12. Presence in a supplied catalog or table proves only that a configuration is advertised; it does not prove live quota or current provider availability. Among the legal candidates that adequately serve the task, honor the preferences the user actually supplied, and prefer economical, user-supported configurations over premium ones when both suffice, stating the supplied facts behind the choice.
13. Abort with {"profileId": null, "reason": "...", "evidenceIds": [], "policyCheck": null, "support": {"cardProfileIds": [], "annotationProfileIds": []}} when no candidate is clearly better supported than the others or the request is ambiguous. Abstention is a correct answer, not a failure.`;

/** The frozen selection instruction prefix. */
export function instructionsFor(operation) {
  if (operation === 'select') return SELECT_INSTRUCTIONS;
  throw new TypeError(`unknown decision operation: ${String(operation)}`);
}

/**
 * Stable key order for the payload so serialization is deterministic.
 *
 * The CURRENT TABLE (profiles, cards, preferences, evidence) comes before the
 * per-request remainder (routingPreferences, policyFacts, task, requestId).
 * That is what makes the shared table snapshot the reusable prefix: within one
 * table revision every request renders the same leading bytes, and only the
 * small varying tail is new. The request-local policy facts sit in that tail,
 * after the table and routing preferences. `requestId` is deliberately last —
 * it is unique per request and must never sit in front of content that can be
 * reused. Revision metadata follows the table too, so a no-content publication
 * keeps the reusable bytes intact and a card edit can still reuse preceding
 * profiles.
 */
const SELECT_KEYS = ['operation', 'profile', 'profiles', 'cards', 'preferences', 'annotations', 'evidence', 'tableRevision', 'routingPreferences', 'policyFacts', 'task', 'requestId'];

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
