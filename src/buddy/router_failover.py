"""Classify a Router outcome from facts already checked by the blackboard.

This policy has no persistence, scheduling or code-to-circumstance inference.
The caller distinguishes a malformed answer from a changed frozen candidate,
and supplies actual stop evidence before a no-answer result can advance.
"""
from .errors import BoardError


def classify_outcome(*, stage: str, code: str | None, answer_valid: bool,
                     abstained: bool, cancelled: bool, circumstances_changed: bool,
                     shutdown_confirmed: bool) -> str:
    """Return answered/abstained/no-answer/changed/cancelled/stop-unconfirmed.

    A valid abstention finishes this Router's selection. An invalid answer that
    contains a null profile is still no-answer. Cancellation and a changed
    request never authorize another Router; unconfirmed stop prevents accepting
    even an otherwise valid answer. Model-before-start preflight failures use
    the same policy with the caller's proven never-started stop fact.
    """
    if not isinstance(stage, str) or stage not in ("preflight", "runtime", "publication"):
        raise BoardError("INVALID_ARGUMENT", "Router outcome stage must be preflight, runtime or publication")
    if code is not None and not isinstance(code, str):
        raise BoardError("INVALID_ARGUMENT", "Router outcome code must be a string or null")
    flags = {"answer_valid": answer_valid, "abstained": abstained, "cancelled": cancelled,
             "circumstances_changed": circumstances_changed, "shutdown_confirmed": shutdown_confirmed}
    for name, value in flags.items():
        if type(value) is not bool:
            raise BoardError("INVALID_ARGUMENT", f"Router outcome {name} must be boolean")
    if cancelled:
        return "cancelled"
    if circumstances_changed:
        return "changed"
    if not shutdown_confirmed:
        return "stop-unconfirmed"
    if answer_valid:
        return "abstained" if abstained else "answered"
    return "no-answer"
