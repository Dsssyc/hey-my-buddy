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
export const PROMPT_VERSION = 8;

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
{"profileId": <string|null>, "reason": <string>, "evidenceIds": <string[]>, "support": {"cardProfileIds": <string[]>, "annotationProfileIds": <string[]>}}

Rules:
1. "profileId" MUST be the "profileId" of exactly one entry of the supplied "profiles" array, or null to abstain. An abstention carries "evidenceIds": [] and empty "support" arrays.
2. Never invent a profile, model, provider, or effort. A profile that is absent, disabled, or unavailable is not a candidate.
3. "evidenceIds" MUST be a subset of the "evidenceIds" values supplied for the chosen profile. Never invent evidence ids.
4. "reason" is a short factual justification (at most 500 characters) grounded in the supplied profiles, cards, preferences, annotations, evidence, task routing preferences, and task.
5. The supplied "preferences" are constraints: a "pin" or "exclude" entry narrows the legal candidate set, and a soft "prefer" only orders legal candidates. Never claim to change a preference.
6. The routing-policy acknowledgment is not part of your answer: the program computes it from the request's "policyFacts" and the profile you select, and it overwrites whatever you send. A "policyCheck" field you add is ignored redundant data, never an opinion, and it can never change the decision.
7. Task routing preferences are POSITIVE: a rule whose match fields equal a legal candidate asks you to prefer that candidate for this request. Such a rule is never an avoid or exclude instruction, applies only to this request, and never overrides hard candidate filters or published pins/excludes.
8. "support" arrays may cite only real supplied card or annotation profileIds, and only for your selected profile or the effective preferred candidates ("taskPreference"."matchingProfileIds" plus "userPreferredProfileIds"); at most 32 unique ids per array. When the program's computed check makes your selection an alternative to a task or user preference, cite at least one actually supplied evidence id for your selection, or a nonempty eligible support array. A matched, fallback, or none selection needs no fabricated references.
9. Model prose never means the task succeeded or was accepted.
10. A missing card or annotation means unknown capability evidence, not inability. Use declared profile capabilities for compatibility; never infer that DSH or another harness lacks ordinary coding tools because cards are absent.
11. A profile's adapter names the harness that will run the Buddy, not the codebase or files it may edit. Working on the source, scripts, or tests of DSH, ZCode, Codex or another harness does not require running on that harness, unless the legal candidates were already narrowed by an explicit hard constraint. Do not invent such a restriction in the reason.
12. Presence in a supplied catalog or table proves only that a configuration is advertised; it does not prove live quota or current provider availability. Among the legal candidates that adequately serve the task, honor the preferences the user actually supplied, and prefer economical, user-supported configurations over premium ones when both suffice, stating the supplied facts behind the choice.
13. Abort with {"profileId": null, "reason": "...", "evidenceIds": [], "support": {"cardProfileIds": [], "annotationProfileIds": []}} when no candidate is clearly better supported than the others or the request is ambiguous. Abstention is a correct answer, not a failure.`;

/** The frozen selection instruction prefix. */
export function instructionsFor(operation) {
  if (operation === 'select') return SELECT_INSTRUCTIONS;
  throw new TypeError(`unknown decision operation: ${String(operation)}`);
}

/**
 * Request-free correction notes for one refused answer, keyed by stable code.
 *
 * Every note is a fixed string: it carries the code's meaning and the schema
 * rule the model violated, and never quotes the request, the refused answer, or
 * any provider text. That keeps one corrective retry bounded and safe to send.
 */
export const CORRECTION_NOTES = Object.freeze({
  'answer-empty': 'Return one bare JSON object and nothing else; an empty answer is refused.',
  'answer-not-json': 'Return one bare JSON object, with no Markdown fences, prose, or comments around it.',
  'answer-invalid-json': 'Return syntactically valid JSON for exactly one object.',
  'answer-shape': 'Match the documented output schema exactly, using bounded non-empty values.',
  'answer-unexpected-field': 'Remove every field that is not in the documented output schema, including any "policyCheck".',
  'answer-profile-not-candidate': 'Choose a "profileId" that is one of the supplied profiles whose enabled and available are both true, or abstain with null.',
  'answer-evidence-not-supplied': 'Cite only "evidenceIds" that were supplied in the request for the chosen profile.',
  'policy-check-shape': 'Send "support" with exactly "cardProfileIds" and "annotationProfileIds"; an abstention carries empty evidence and both support arrays empty.',
  'policy-support-unknown': 'Cite support ids only from the supplied cards or annotations, scoped to your selection or an effective preferred candidate.',
  'policy-alternative-unsupported': 'When you select a candidate that is not an effective preferred match, cite supplied evidence for it or eligible card/annotation support.',
});

/**
 * Render the second, corrective user turn for one refused answer.
 *
 * The payload is one small object with the stable refusal code and a fixed
 * instruction; the frozen request payload and the instruction prefix are never
 * restated or altered, so the retry reuses the same cached prefix and the same
 * bounded input.
 */
export function correctionMessage(code) {
  const stable = String(code).slice(0, 120);
  const instruction = CORRECTION_NOTES[stable] ?? 'Return exactly one JSON object in the documented output schema, using only supplied values.';
  return JSON.stringify({ correction: { code: stable, instruction } });
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
