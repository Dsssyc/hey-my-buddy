"""Program-owned preference facts; model judgments are not acceptance rules."""
from __future__ import annotations

from typing import Any

MAX_ID_LENGTH = 256
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
    prefer_profile_ids: list[str],
    hard_constraints: dict,
) -> dict:
    """The complete bounded preference truth for one request.

    ``hardConstraints`` is exactly the workflow request's normalized constraint
    map — never inferred from task text, filenames or card prose, and empty when
    the request supplied none. ``userPreferredProfileIds`` are the supplied
    published ``prefer`` profiles that are legal candidates, in supplied order.
    Legal means enabled and available: a disabled or unavailable profile can
    never carry a preference match. Task-local routing preferences are retired
    Host input (ADR-021 decision 5); the user's global pin/prefer/exclude
    settings remain exactly as they were.
    """
    legal_ids: list[str] = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        profile_id = profile.get("profileId")
        if not _bounded_identifier(profile_id):
            continue
        if profile.get("enabled") is not True or profile.get("available") is not True:
            continue
        legal_ids.append(profile_id)
    legal_set = set(legal_ids)

    user_preferred = [
        profile_id for profile_id in (prefer_profile_ids or []) if profile_id in legal_set
    ]
    return {
        "hardConstraints": dict(hard_constraints or {}),
        "userPreferredProfileIds": user_preferred,
    }


def expected_policy_check(facts: dict, profile_id: str) -> dict:
    """The program-owned ``policyCheck`` for a selection of ``profile_id``.

    A selection outside the effective preferred set with a legal preferred
    candidate is ``alternative`` — a legitimate, supported choice, never a
    violation.
    """
    user_preferred = facts.get("userPreferredProfileIds") or []
    if not user_preferred:
        user_outcome = "none"
    elif profile_id in user_preferred:
        user_outcome = "matched"
    else:
        user_outcome = "alternative"
    return {
        "hardConstraints": dict(facts.get("hardConstraints") or {}),
        "userPreference": user_outcome,
    }
