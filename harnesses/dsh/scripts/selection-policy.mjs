/**
 * Program-computed bounded routing policy facts and their typed model check.
 *
 * This module is the Node mirror of ``src/buddy/selection_policy.py``. The
 * Python service computes the ``policyFacts`` for one request from its own
 * authoritative table; this module re-derives the same facts independently from
 * the frozen payload and COMPUTES the one legal ``policyCheck`` for the selected
 * profile. A model-provided ``policyCheck`` is ignored redundant data: it is
 * never interpreted and can never change the adopted check. The plugin computes
 * before adopting a model answer, the adapter computes again over the plugin's
 * result document, and Python computes a third time against its immutable input
 * before publication. No natural-language reason text is ever parsed: the check
 * is typed and structural only.
 *
 * Deliberately pure: no filesystem, process, or harness access.
 *
 * @module harnesses/dsh/scripts/selection-policy
 */

/** Upper bound for each `support` array (mirrors the evidence-reference bound). */
export const MAX_SUPPORT_IDS = 32;
/** Bounded identifier shape for untrusted support references. */
export const MAX_ID_LENGTH = 256;

/**
 * The closed outcome vocabularies. There is deliberately no avoid/exclude value:
 * task preferences are POSITIVE, and an inverted or invented enum is refused as
 * invalid rather than interpreted.
 */
export const TASK_OUTCOMES = Object.freeze(['none', 'matched', 'alternative', 'fallback']);
export const USER_OUTCOMES = Object.freeze(['none', 'matched', 'alternative']);

/**
 * Stable machine codes for policy violations; Python settles each needs-host.
 *
 * Outcomes are program-computed; a malformed redundant policy echo cannot fail
 * an otherwise legal recommendation.
 */
export const POLICY_FACTS_MISMATCH = 'policy-facts-mismatch';
export const POLICY_CHECK_SHAPE = 'policy-check-shape';
export const POLICY_SUPPORT_UNKNOWN = 'policy-support-unknown';
export const POLICY_ALTERNATIVE_UNSUPPORTED = 'policy-alternative-unsupported';

/** Whether a value is a bounded, non-empty, control-character-free identifier. */
export function isBoundedIdentifier(value) {
  return typeof value === 'string' && value.length > 0 && value.length <= MAX_ID_LENGTH &&
    !/[\u0000-\u001f\u007f]/.test(value);
}

/** Own-property read that tolerates non-objects. */
function own(object, key) {
  if (object === null || typeof object !== 'object' || Array.isArray(object)) return undefined;
  return Object.hasOwn(object, key) ? object[key] : undefined;
}

/** Key-order-insensitive deep equality for JSON values. */
export function jsonEqual(first, second) {
  if (first === second) return true;
  if (Array.isArray(first) && Array.isArray(second)) {
    return first.length === second.length && first.every((entry, index) => jsonEqual(entry, second[index]));
  }
  if (first === null || second === null || typeof first !== 'object' || typeof second !== 'object' ||
      Array.isArray(first) || Array.isArray(second)) {
    return false;
  }
  const left = Object.keys(first);
  const right = Object.keys(second);
  return left.length === right.length && left.every((key) => jsonEqual(first[key], second[key]));
}

/**
 * Derive the bounded preference truth from one request's own table data.
 *
 * `hardConstraints` cannot be derived from the table (it is request-level), so
 * the caller supplies the request's own recorded map; the derivation covers
 * `taskPreference` and `userPreferredProfileIds`. Only enabled AND available
 * profiles are legal, so a disabled or unavailable profile can never carry a
 * preference match.
 */
export function derivePolicyFacts({ profiles, routingPreferences = [], preferences = [], hardConstraints = {} }) {
  const legalIds = [];
  const fields = new Map();
  for (const entry of Array.isArray(profiles) ? profiles : []) {
    const profileId = own(entry, 'profileId');
    if (!isBoundedIdentifier(profileId)) continue;
    if (own(entry, 'enabled') !== true || own(entry, 'available') !== true) continue;
    legalIds.push(profileId);
    fields.set(profileId, entry);
  }
  const legal = new Set(legalIds);
  let ruleIndex = null;
  let matchingProfileIds = [];
  for (const [index, preference] of (Array.isArray(routingPreferences) ? routingPreferences : []).entries()) {
    const match = own(preference, 'match');
    if (match === null || typeof match !== 'object' || Array.isArray(match)) continue;
    const pairs = Object.entries(match);
    if (pairs.length === 0) continue;
    const matches = legalIds.filter((profileId) => pairs.every(([key, value]) => own(fields.get(profileId), key) === value));
    if (matches.length > 0) {
      ruleIndex = index;
      matchingProfileIds = matches;
      break;
    }
  }
  const userPreferredProfileIds = (Array.isArray(preferences) ? preferences : [])
    .filter((entry) => own(entry, 'mode') === 'prefer' && legal.has(own(entry, 'profileId')))
    .map((entry) => own(entry, 'profileId'));
  return {
    hardConstraints: hardConstraints === null || typeof hardConstraints !== 'object' || Array.isArray(hardConstraints)
      ? {}
      : { ...hardConstraints },
    taskPreference: { ruleIndex, matchingProfileIds },
    userPreferredProfileIds,
  };
}

/**
 * The one `policyCheck` a selection of `profileId` must carry.
 *
 * The program computes and adopts this value; a model-authored `policyCheck` is
 * ignored redundant data and never changes it. `fallback` exists only when the
 * request supplied routing preferences and none legally matched; `none` only
 * when it supplied none. A selection outside the effective preferred set with a
 * legal preferred candidate is `alternative` — a legitimate, supported choice,
 * never a violation.
 */
export function expectedPolicyCheck(facts, routingPreferences, profileId) {
  const task = facts?.taskPreference ?? {};
  const ruleIndex = task.ruleIndex ?? null;
  const preferences = Array.isArray(routingPreferences) ? routingPreferences : [];
  let taskOutcome;
  if (ruleIndex === null) {
    taskOutcome = preferences.length > 0 ? 'fallback' : 'none';
  } else if ((task.matchingProfileIds ?? []).includes(profileId)) {
    taskOutcome = 'matched';
  } else {
    taskOutcome = 'alternative';
  }
  const userPreferred = facts?.userPreferredProfileIds ?? [];
  const userOutcome = userPreferred.length === 0
    ? 'none'
    : (userPreferred.includes(profileId) ? 'matched' : 'alternative');
  return {
    hardConstraints: { ...(facts?.hardConstraints ?? {}) },
    taskPreference: { ruleIndex, outcome: taskOutcome },
    userPreference: userOutcome,
  };
}

/** Whether the program-computed check makes an alternative that must be supported. */
export function alternativeRequiresSupport(policyCheck) {
  return policyCheck.taskPreference.outcome === 'alternative' || policyCheck.userPreference === 'alternative';
}

/** Validate one support array; returns `{ entries }` or `{ problem }`. */
function checkSupportArray(value, { supplied, scope }) {
  if (!Array.isArray(value)) return { problem: 'support arrays must be lists' };
  if (value.length > MAX_SUPPORT_IDS) return { problem: `support carries ${value.length} entries, above the ${MAX_SUPPORT_IDS} bound` };
  const seen = new Set();
  for (const entry of value) {
    if (!isBoundedIdentifier(entry)) return { problem: 'support entries must be bounded identifiers' };
    if (seen.has(entry)) return { problem: 'support entries must be unique' };
    seen.add(entry);
    // The refused value is deliberately not echoed: an unsupplied reference is
    // model-authored text and never enters a persisted detail message.
    if (!supplied.has(entry)) return { problem: 'a support entry was not supplied in the bounded input' };
    if (!scope.has(entry)) return { problem: 'a support entry is unrelated to the selected or preferred candidates' };
  }
  return { entries: [...value] };
}

/**
 * Validate one recommendation's `support` against the program-derived facts and
 * return the program-computed `policyCheck`.
 *
 * The decision's own `policyCheck` field is read but never interpreted: the
 * model neither states nor withholds the routing acknowledgment, and the one
 * legal check for the selected profile is computed here. Candidate membership
 * and evidence membership are enforced by the caller's base validation; this
 * function enforces the typed support scope and the alternative-support rule.
 *
 * @param decision the model's answer `{profileId, reason, evidenceIds, policyCheck?, support}`.
 * @param facts the derived `policyFacts` for this request.
 * @param routingPreferences the request's task-local routing preferences.
 * @param scope `{cardProfileIds, annotationProfileIds}` sets actually supplied.
 * @returns `{ ok: true, policyCheck, support }` or `{ ok: false, code, detail }`.
 */
export function validateDecision(decision, facts, routingPreferences, scope) {
  const profileId = own(decision, 'profileId');
  const support = own(decision, 'support');
  if (profileId === null) {
    const evidence = own(decision, 'evidenceIds');
    if (!Array.isArray(evidence) || evidence.length > 0) {
      return { ok: false, code: POLICY_CHECK_SHAPE, detail: 'an abstention must not cite evidence' };
    }
    if (!jsonEqual(support, { cardProfileIds: [], annotationProfileIds: [] })) {
      return { ok: false, code: POLICY_CHECK_SHAPE, detail: 'an abstention must carry empty support arrays' };
    }
    return { ok: true, policyCheck: null, support: { cardProfileIds: [], annotationProfileIds: [] } };
  }
  if (!isBoundedIdentifier(profileId)) {
    return { ok: false, code: POLICY_CHECK_SHAPE, detail: 'profileId must be a bounded identifier' };
  }
  if (support === null || typeof support !== 'object' || Array.isArray(support) ||
      !jsonEqual(Object.keys(support).sort(), ['annotationProfileIds', 'cardProfileIds'])) {
    return { ok: false, code: POLICY_CHECK_SHAPE, detail: 'support must carry exactly cardProfileIds and annotationProfileIds' };
  }
  const policyCheck = expectedPolicyCheck(facts, routingPreferences, profileId);
  const effectiveScope = new Set([
    profileId,
    ...(facts?.taskPreference?.matchingProfileIds ?? []),
    ...(facts?.userPreferredProfileIds ?? []),
  ]);
  const checked = {};
  for (const [key, supplied] of [['cardProfileIds', scope.cardProfileIds], ['annotationProfileIds', scope.annotationProfileIds]]) {
    const verdict = checkSupportArray(support[key], { supplied, scope: effectiveScope });
    if (verdict.problem !== undefined) return { ok: false, code: POLICY_SUPPORT_UNKNOWN, detail: verdict.problem };
    checked[key] = verdict.entries;
  }
  if (alternativeRequiresSupport(policyCheck)) {
    const cited = Array.isArray(own(decision, 'evidenceIds')) ? own(decision, 'evidenceIds') : [];
    if (cited.length === 0 && checked.cardProfileIds.length === 0 && checked.annotationProfileIds.length === 0) {
      return {
        ok: false,
        code: POLICY_ALTERNATIVE_UNSUPPORTED,
        detail: 'an alternative selection must cite supplied evidence or eligible card/annotation support',
      };
    }
  }
  return { ok: true, policyCheck, support: checked };
}
