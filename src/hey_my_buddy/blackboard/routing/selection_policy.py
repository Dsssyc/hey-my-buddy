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
    buddies: list[dict],
    prefer_buddy_ids: list[str],
    hard_constraints: dict,
) -> dict:
    """The complete bounded preference truth for one request.

    ``hardConstraints`` is exactly the workflow request's normalized constraint
    map — never inferred from task text or filenames, and empty when
    the request supplied none. ``userPreferredBuddyIds`` are the supplied
    published ``prefer`` buddies that are legal candidates, in supplied order.
    Legal means enabled and available: a disabled or unavailable buddy can
    never carry a preference match. Task-local routing preferences are retired
    Host input (ADR-021 decision 5); the user's global pin/prefer/exclude
    settings remain exactly as they were.
    """
    legal_ids: list[str] = []
    for buddy in buddies:
        if not isinstance(buddy, dict):
            continue
        buddy_id = buddy.get("buddyId")
        if not _bounded_identifier(buddy_id):
            continue
        if buddy.get("enabled") is not True or buddy.get("available") is not True:
            continue
        legal_ids.append(buddy_id)
    legal_set = set(legal_ids)

    user_preferred = [
        buddy_id for buddy_id in (prefer_buddy_ids or []) if buddy_id in legal_set
    ]
    return {
        "hardConstraints": dict(hard_constraints or {}),
        "userPreferredBuddyIds": user_preferred,
    }


def expected_policy_check(facts: dict, buddy_id: str) -> dict:
    """The program-owned ``policyCheck`` for a selection of ``buddy_id``.

    A selection outside the effective preferred set with a legal preferred
    candidate is ``alternative`` — a legitimate, supported choice, never a
    violation.
    """
    user_preferred = facts.get("userPreferredBuddyIds") or []
    if not user_preferred:
        user_outcome = "none"
    elif buddy_id in user_preferred:
        user_outcome = "matched"
    else:
        user_outcome = "alternative"
    return {
        "hardConstraints": dict(facts.get("hardConstraints") or {}),
        "userPreference": user_outcome,
    }
