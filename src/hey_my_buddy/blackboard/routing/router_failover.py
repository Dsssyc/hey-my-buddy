"""Classify a Router outcome from facts already checked by the blackboard.

This policy has no persistence, scheduling or code-to-circumstance inference.
The caller distinguishes a malformed answer from a changed frozen candidate,
and supplies actual stop evidence before a no-answer result can advance.
"""
def classify_outcome(*, stage: str, code: str | None, answer_valid: bool,
                     abstained: bool, cancelled: bool, circumstances_changed: bool,
                     shutdown_confirmed: bool) -> str:
    """Return answered/abstained/no-answer/changed/cancelled/stop-unconfirmed.

    A valid abstention finishes this Router's selection. An invalid answer that
    contains a null buddy is still no-answer. Cancellation and a changed
    request never authorize another Router; unconfirmed stop prevents accepting
    even an otherwise valid answer. Model-before-start preflight failures use
    the same policy with the caller's proven never-started stop fact.
    """
    if cancelled:
        return "cancelled"
    if circumstances_changed:
        return "changed"
    if not shutdown_confirmed:
        return "stop-unconfirmed"
    if answer_valid:
        return "abstained" if abstained else "answered"
    return "no-answer"
