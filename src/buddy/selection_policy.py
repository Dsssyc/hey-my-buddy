"""Derive routing policy outcomes and validate bounded support references.

The model chooses a legal profile and supplies its reason and references. Policy
outcomes are program facts, never model-authored acknowledgments. Node and Python
independently derive them from the frozen request; a redundant policyCheck in an
untrusted answer is ignored and never changes the derived outcome or authority.
This module is pure and does not interpret qualitative reason prose as proof.
"""
from __future__ import annotations

from typing import Any
import json
import re

#: The closed outcome vocabularies. There is deliberately no avoid/exclude/reject
#: value: task preferences are POSITIVE, and the historical inverted enums are
#: refused as invalid rather than interpreted.
TASK_OUTCOMES = ("none", "matched", "alternative", "fallback")
USER_OUTCOMES = ("none", "matched", "alternative")

#: Upper bound for each ``support`` array (mirrors the evidence-reference bound).
MAX_SUPPORT_IDS = 32
#: Bounded identifier shape for untrusted support references.
MAX_ID_LENGTH = 256

SUPPORT_KEYS = frozenset({"cardProfileIds", "annotationProfileIds"})

#: Stable machine codes carried by the helper for policy violations. Python
#: settles exhausted answer-validation failures ``needs-host`` on the same goal.
POLICY_FACTS_MISMATCH = "policy-facts-mismatch"
POLICY_CHECK_SHAPE = "policy-check-shape"
POLICY_SUPPORT_UNKNOWN = "policy-support-unknown"
POLICY_ALTERNATIVE_UNSUPPORTED = "policy-alternative-unsupported"


def sanitize_diagnostics(output: dict, request: dict) -> dict:
    """Recheck advisory diagnostics before they reach receipts or publication.

    Model/provider prose is not authorized by a self-reported redacted flag.
    Keep only the fixed structure, supplied ids and redaction placeholders.
    Invalid diagnostics are omitted without changing a valid recommendation.
    """
    if "diagnostics" not in output:
        return output
    clean = {key: value for key, value in output.items() if key != "diagnostics"}
    value = output["diagnostics"]
    def integer(number, maximum):
        return type(number) is int and 0 <= number <= maximum
    if not isinstance(value, dict) or set(value) != {"calls", "failures"} or not integer(value["calls"], 2):
        return {**clean, "diagnosticsOmitted": True}
    failures = value["failures"]
    if not isinstance(failures, list) or len(failures) > value["calls"]:
        return {**clean, "diagnosticsOmitted": True}
    safe = {"none", "matched", "alternative", "fallback", "<redacted>", "<ignored>"}
    def add(item):
        if isinstance(item, str):
            safe.add(item)
    add(request.get("requestId"))
    for item in (request.get("profile") or {}).values():
        add(item)
    for name in ("profiles", "cards", "preferences", "annotations", "evidence"):
        for item in request.get(name) or []:
            if isinstance(item, dict):
                add(item.get("profileId"))
                add(item.get("evidenceId"))
                for identifier in item.get("evidenceIds") or []:
                    add(identifier)
    keys = {"profileId", "reason", "evidenceIds", "policyCheck", "support", "cardProfileIds", "annotationProfileIds"}
    def safe_shape(item, depth=0):
        if item is None or type(item) is bool:
            return True
        if isinstance(item, str):
            return item in safe
        if depth >= 4:
            return False
        if isinstance(item, list):
            return len(item) <= 16 and all(safe_shape(child, depth+1) for child in item)
        if isinstance(item, dict):
            return len(item) <= 16 and all(
                (key in keys or re.fullmatch(r'<unknown-field>(?:-[0-9]{1,2})?', key))
                and safe_shape(child, depth+1) for key, child in item.items())
        return False
    checked = []
    for failure in failures:
        if not isinstance(failure, dict) or set(failure) != {"code", "answer"}:
            return {**clean, "diagnosticsOmitted": True}
        code, answer = failure["code"], failure["answer"]
        if not isinstance(code, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,119}', code):
            return {**clean, "diagnosticsOmitted": True}
        if not isinstance(answer, dict) or set(answer) != {"text", "sha256", "bytes", "truncated", "redacted"}:
            return {**clean, "diagnosticsOmitted": True}
        text = answer["text"]
        if (not isinstance(text, str) or len(text.encode('utf-8', errors='replace')) > 2048
            or not isinstance(answer["sha256"], str) or not re.fullmatch(r'[0-9a-f]{64}', answer["sha256"])
            or not integer(answer["bytes"], 2**53-1) or type(answer["truncated"]) is not bool or answer["redacted"] is not True):
            return {**clean, "diagnosticsOmitted": True}
        if text not in {"<empty-answer>", "<unparsed-answer>", "<truncated-redacted-answer>"}:
            try:
                parsed = json.loads(text)
            except (ValueError, RecursionError):
                # The child may cut an already redacted JSON projection at its
                # byte bound. Do not forward an unverifiable partial string.
                text = "<truncated-redacted-answer>" if answer["truncated"] else "<unparsed-answer>"
            else:
                if not safe_shape(parsed):
                    return {**clean, "diagnosticsOmitted": True}
        checked.append({"code": code, "answer": {**answer, "text": text}})
    return {**clean, "diagnostics": {"calls": value["calls"], "failures": checked}}


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
    """The program-owned ``policyCheck`` for a selection of ``profile_id``.

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
    """Derive the outcome and validate support for one recommendation.

    Returns ``(derived_policy_check, support, None)`` or a stable failure tuple.
    The qualitative reason and any supplied policyCheck never control facts.
    """
    profile_id = decision.get("profileId")
    support = decision.get("support")
    if profile_id is None:
        evidence = decision.get("evidenceIds")
        if not isinstance(evidence, list) or evidence:
            return None, None, (POLICY_CHECK_SHAPE, "an abstention must not cite evidence")
        if support != {"cardProfileIds": [], "annotationProfileIds": []}:
            return None, None, (POLICY_CHECK_SHAPE, "an abstention must carry empty support arrays")
        return None, {"cardProfileIds": [], "annotationProfileIds": []}, None

    if not isinstance(profile_id, str) or not _bounded_identifier(profile_id):
        return None, None, (POLICY_CHECK_SHAPE, "profileId must be a bounded identifier")
    policy_check = expected_policy_check(facts, routing_preferences, profile_id)
    if not isinstance(support, dict) or set(support) != SUPPORT_KEYS:
        return None, None, (POLICY_CHECK_SHAPE, "support must carry exactly cardProfileIds and annotationProfileIds")

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
    "POLICY_FACTS_MISMATCH",
    "POLICY_SUPPORT_UNKNOWN",
    "TASK_OUTCOMES",
    "USER_OUTCOMES",
    "alternative_requires_support",
    "expected_policy_check",
    "policy_facts",
    "validate_decision",
]
