"""Program-owned preference facts; model judgments are not acceptance rules."""
from __future__ import annotations

USER_OUTCOMES = ("none", "matched", "alternative")


def policy_facts(
    *,
    buddies: list[dict],
    prefer_buddy_ids: list[str],
    hard_constraints: dict,
) -> dict:
    """The preference truth for one frozen request.

    ``hardConstraints`` is exactly the workflow request's normalized constraint
    map — never inferred from task text or filenames, and empty when
    the request supplied none. ``userPreferredBuddyIds`` are the supplied
    published ``prefer`` buddies that are legal candidates, in supplied order.
    Candidate rows are already eligible when frozen. Task-local routing preferences are retired
    Host input (ADR-021 decision 5); the user's global pin/prefer/exclude
    settings remain exactly as they were.
    """
    candidate_ids = {buddy["buddyId"] for buddy in buddies}
    user_preferred = [buddy_id for buddy_id in prefer_buddy_ids if buddy_id in candidate_ids]
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
