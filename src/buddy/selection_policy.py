"""Program-owned preference facts; model judgments are not acceptance rules."""
from __future__ import annotations

from typing import Any

MAX_ID_LENGTH = 256
TASK_OUTCOMES = ("none", "matched", "alternative", "fallback")
USER_OUTCOMES = ("none", "matched", "alternative")


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
