"""Program-computed bounded routing policy facts and their typed model check.

The selector's historical failure mode was semantic: a positive task preference
(``match.adapter=dsh``) was inverted into an "avoid DSH" explanation, and a legal
candidate was reported as absent. Natural-language reason text cannot be policed
without becoming a regex judge over prose, so the repair is structural instead:
the program derives the complete bounded preference truth for one request, the
model must acknowledge it with a typed ``policyCheck`` bound to its selection,
and that acknowledgment is verified independently — once in the Node helper
against the same frozen payload and again here against this service's immutable
input — before any adoption. The free-form ``reason`` stays qualitative model
judgment; no code claims its prose is true.

This module is pure: no database, no filesystem, no clock. Both the claim-time
derivation and the publication-time re-derivation call the same functions, so a
recommendation can only be adopted when the model's answer agrees with facts the
service itself computed twice.
"""
from __future__ import annotations

from typing import Any

#: The closed outcome vocabularies. There is deliberately no avoid/exclude/reject
#: value: task preferences are POSITIVE, and the historical inverted enums are
#: refused as invalid rather than interpreted.
TASK_OUTCOMES = ("none", "matched", "alternative", "fallback")
USER_OUTCOMES = ("none", "matched", "alternative")

#: Upper bound for each ``support`` array (mirrors the evidence-reference bound).
MAX_SUPPORT_IDS = 32
#: Bounded identifier shape for untrusted support references.
MAX_ID_LENGTH = 256

POLICY_CHECK_KEYS = frozenset({"hardConstraints", "taskPreference", "userPreference"})
TASK_PREFERENCE_KEYS = frozenset({"ruleIndex", "outcome"})
SUPPORT_KEYS = frozenset({"cardProfileIds", "annotationProfileIds"})

#: Stable machine codes carried by the helper for policy violations. Python
#: settles every one of them ``needs-host`` on the same goal; none is retried.
POLICY_FACTS_MISMATCH = "policy-facts-mismatch"
POLICY_CHECK_SHAPE = "policy-check-shape"
POLICY_CONSTRAINT_MISMATCH = "policy-constraint-mismatch"
POLICY_INDEX_MISMATCH = "policy-index-mismatch"
POLICY_OUTCOME_INVALID = "policy-outcome-invalid"
POLICY_OUTCOME_FALSE = "policy-outcome-false"
POLICY_SUPPORT_UNKNOWN = "policy-support-unknown"
POLICY_ALTERNATIVE_UNSUPPORTED = "policy-alternative-unsupported"


def _bounded_identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_ID_LENGTH
        and not any(ord(character) < 32 or character == "\x7f" for character in value)
    )


def policy_facts(
    *,
    profiles: list[dict],
    routing_preferences: list[dict],
    prefer_profile_ids: list[str],
    hard_constraints: dict,
) -> dict:
    """The complete bounded preference truth for one request.

    ``hardConstraints`` is exactly the workflow request's normalized constraint
    map — never inferred from task text, filenames or card prose, and empty when
    the request supplied none. ``taskPreference`` names the first ordered
    routing-preference rule that at least one legal candidate matches, with the
    matching legal candidate ids; ``ruleIndex`` stays null when the request
    carries no rule with a legal match. ``userPreferredProfileIds`` are the
    supplied published ``prefer`` profiles that are legal candidates, in
    supplied order. Legal means enabled and available: a disabled or unavailable
    profile can never carry a preference match.
    """
    legal_ids: list[str] = []
    fields: dict[str, dict] = {}
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        profile_id = profile.get("profileId")
        if not _bounded_identifier(profile_id):
            continue
        if profile.get("enabled") is not True or profile.get("available") is not True:
            continue
        legal_ids.append(profile_id)
        fields[profile_id] = profile
    legal_set = set(legal_ids)

    rule_index: int | None = None
    matching_profile_ids: list[str] = []
    for index, preference in enumerate(routing_preferences or []):
        match = preference.get("match") if isinstance(preference, dict) else None
        if not isinstance(match, dict) or not match:
            continue
        matches = [
            profile_id
            for profile_id in legal_ids
            if all(fields[profile_id].get(key) == value for key, value in match.items())
        ]
        if matches:
            rule_index = index
            matching_profile_ids = matches
            break

    user_preferred = [
        profile_id for profile_id in (prefer_profile_ids or []) if profile_id in legal_set
    ]
    return {
        "hardConstraints": dict(hard_constraints or {}),
        "taskPreference": {"ruleIndex": rule_index, "matchingProfileIds": matching_profile_ids},
        "userPreferredProfileIds": user_preferred,
    }


def expected_policy_check(facts: dict, routing_preferences: list[dict], profile_id: str) -> dict:
    """The one ``policyCheck`` a selection of ``profile_id`` may carry.

    ``ruleIndex`` is always the input fact's index; ``fallback`` exists only when
    the request supplied routing preferences and none legally matched, and
    ``none`` only when the request supplied none. A selection outside the
    effective preferred set with a legal preferred candidate is ``alternative`` —
    a legitimate, supported choice, never a violation.
    """
    task = facts.get("taskPreference") or {}
    rule_index = task.get("ruleIndex")
    if rule_index is None:
        task_outcome = "fallback" if routing_preferences else "none"
    elif profile_id in (task.get("matchingProfileIds") or []):
        task_outcome = "matched"
    else:
        task_outcome = "alternative"
    user_preferred = facts.get("userPreferredProfileIds") or []
    if not user_preferred:
        user_outcome = "none"
    elif profile_id in user_preferred:
        user_outcome = "matched"
    else:
        user_outcome = "alternative"
    return {
        "hardConstraints": dict(facts.get("hardConstraints") or {}),
        "taskPreference": {"ruleIndex": rule_index, "outcome": task_outcome},
        "userPreference": user_outcome,
    }


def alternative_requires_support(policy_check: dict) -> bool:
    return policy_check["taskPreference"]["outcome"] == "alternative" or policy_check["userPreference"] == "alternative"


def _check_support_array(value: Any, *, supplied: set[str], scope: set[str]) -> tuple[list[str] | None, str | None]:
    if not isinstance(value, list):
        return None, "support arrays must be lists"
    if len(value) > MAX_SUPPORT_IDS:
        return None, f"support carries {len(value)} entries, above the {MAX_SUPPORT_IDS} bound"
    seen: set[str] = set()
    for entry in value:
        if not _bounded_identifier(entry):
            return None, "support entries must be bounded identifiers"
        if entry in seen:
            return None, "support entries must be unique"
        seen.add(entry)
        if entry not in supplied:
            return None, f"{entry!r} was not supplied in the bounded input"
        if entry not in scope:
            return None, f"{entry!r} is unrelated to the selected or preferred candidates"
    return list(value), None


def validate_decision(
    decision: dict,
    facts: dict,
    routing_preferences: list[dict],
    card_profile_ids: set[str],
    annotation_profile_ids: set[str],
) -> tuple[dict | None, dict | None, tuple[str, str] | None]:
    """Validate one recommendation's ``policyCheck`` and ``support``.

    Returns ``(policy_check, support, None)`` when the answer carries the exact
    typed acknowledgment the program derived, or ``(None, None, (code, detail))``
    with a stable machine code. The qualitative ``reason`` text is never parsed.
    """
    profile_id = decision.get("profileId")
    support = decision.get("support")
    if profile_id is None:
        if decision.get("policyCheck") is not None:
            return None, None, (POLICY_CHECK_SHAPE, "an abstention must carry policyCheck null")
        evidence = decision.get("evidenceIds")
        if not isinstance(evidence, list) or evidence:
            return None, None, (POLICY_CHECK_SHAPE, "an abstention must not cite evidence")
        if support != {"cardProfileIds": [], "annotationProfileIds": []}:
            return None, None, (POLICY_CHECK_SHAPE, "an abstention must carry empty support arrays")
        return None, {"cardProfileIds": [], "annotationProfileIds": []}, None

    if not isinstance(profile_id, str) or not _bounded_identifier(profile_id):
        return None, None, (POLICY_CHECK_SHAPE, "profileId must be a bounded identifier")
    policy_check = decision.get("policyCheck")
    if not isinstance(policy_check, dict) or set(policy_check) != POLICY_CHECK_KEYS:
        return None, None, (POLICY_CHECK_SHAPE, "policyCheck must carry exactly hardConstraints, taskPreference and userPreference")
    if not isinstance(support, dict) or set(support) != SUPPORT_KEYS:
        return None, None, (POLICY_CHECK_SHAPE, "support must carry exactly cardProfileIds and annotationProfileIds")

    if policy_check["hardConstraints"] != facts.get("hardConstraints"):
        return None, None, (POLICY_CONSTRAINT_MISMATCH, "hardConstraints must be exactly the input policy facts map")
    task = policy_check["taskPreference"]
    if not isinstance(task, dict) or set(task) != TASK_PREFERENCE_KEYS:
        return None, None, (POLICY_CHECK_SHAPE, "taskPreference must carry exactly ruleIndex and outcome")
    expected = expected_policy_check(facts, routing_preferences, profile_id)
    # The JSON contract allows only null or an integer index. Python's bool is an
    # int subclass and json parses 0.0 as float, so both would otherwise compare
    # equal to a derived index of 0 and later reach list indexing: the type is
    # therefore refused explicitly, before any comparison or indexing.
    stated_index = task["ruleIndex"]
    if stated_index is not None and (isinstance(stated_index, bool) or not isinstance(stated_index, int)):
        return None, None, (POLICY_INDEX_MISMATCH, "taskPreference.ruleIndex must be null or an integer index")
    if stated_index != expected["taskPreference"]["ruleIndex"]:
        return None, None, (POLICY_INDEX_MISMATCH, "taskPreference.ruleIndex must be the input fact's rule index")
    if task["outcome"] not in TASK_OUTCOMES:
        return None, None, (POLICY_OUTCOME_INVALID, f"unknown task preference outcome {task['outcome']!r}")
    if task["outcome"] != expected["taskPreference"]["outcome"]:
        return None, None, (
            POLICY_OUTCOME_FALSE,
            f"the program-derived task outcome for {profile_id} is {expected['taskPreference']['outcome']!r}",
        )
    user_outcome = policy_check["userPreference"]
    if user_outcome not in USER_OUTCOMES:
        return None, None, (POLICY_OUTCOME_INVALID, f"unknown user preference outcome {user_outcome!r}")
    if user_outcome != expected["userPreference"]:
        return None, None, (
            POLICY_OUTCOME_FALSE,
            f"the program-derived user outcome for {profile_id} is {expected['userPreference']!r}",
        )

    scope = {profile_id, *(facts.get("taskPreference", {}).get("matchingProfileIds") or []), *(facts.get("userPreferredProfileIds") or [])}
    checked_support: dict[str, list[str]] = {}
    for key, supplied in (("cardProfileIds", card_profile_ids), ("annotationProfileIds", annotation_profile_ids)):
        entries, problem = _check_support_array(support[key], supplied=supplied, scope=scope)
        if problem is not None:
            return None, None, (POLICY_SUPPORT_UNKNOWN, problem)
        checked_support[key] = entries

    if alternative_requires_support(policy_check):
        cited = decision.get("evidenceIds") or []
        if not cited and not any(checked_support.values()):
            return None, None, (
                POLICY_ALTERNATIVE_UNSUPPORTED,
                "an alternative selection must cite supplied evidence or eligible card/annotation support",
            )
    return policy_check, checked_support, None


__all__ = [
    "MAX_SUPPORT_IDS",
    "POLICY_ALTERNATIVE_UNSUPPORTED",
    "POLICY_CHECK_SHAPE",
    "POLICY_CONSTRAINT_MISMATCH",
    "POLICY_FACTS_MISMATCH",
    "POLICY_INDEX_MISMATCH",
    "POLICY_OUTCOME_FALSE",
    "POLICY_OUTCOME_INVALID",
    "POLICY_SUPPORT_UNKNOWN",
    "TASK_OUTCOMES",
    "USER_OUTCOMES",
    "alternative_requires_support",
    "expected_policy_check",
    "policy_facts",
    "validate_decision",
]
