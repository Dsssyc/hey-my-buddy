"""The role-side observation rules of one unified native run.

These are the judgments ADR-025 keeps out of the native drivers: whether an
observed fact ends the run, and whether one format correction is offered. The
rules are harness-neutral and fact-driven — the driver hands over already
normalized, cumulative facts (unknown-event counts, tool-fact and marker
counts, denied interactions, and the settled final answer) and executes only
the returned :class:`~hey_my_buddy.buddy.harnesses.run_contract.RunFeedback`;
they never parse a native protocol frame and never start a native path of
their own.

Two rules exist because the two existing carriers differ in exactly this
one place: the fast structured call refuses an unknown event, a tool fact
and a refused native interaction immediately, while the Worker turn ignores
unknown events and keeps working. The correction rule preserves the fast
call's existing loop: at most one correction, never for an enum violation,
with the same bounded correction text appended to the same base prompt, on
the same native process and total deadline.
"""
from __future__ import annotations

from typing import Mapping

from .structured_call import correction_code
from ..harnesses.run_contract import FEEDBACK_CONTINUE, FEEDBACK_STOP, RunFeedback

#: The correction suffix of the fast structured call, verbatim: the correction
#: code the shared subset validator produced, plus the one instruction line.
CORRECTION_SUFFIX = ". Return exactly the supplied JSON Schema."


def worker_observer(_facts: Mapping) -> RunFeedback:
    """The Worker turn's rule: every retained fact keeps the run running.

    Unknown events stay counted facts the projection judges later, refused
    native interactions become attention in the turn outcome, and a settled
    root turn needs no correction — the finish receipt is the value. This
    observer never stops the run and never corrects.
    """
    return FEEDBACK_CONTINUE


class FastCorrection:
    """The fast structured call's rule: fail closed on facts, correct once.

    The precedence is the one the fast carrier always had: a refused native
    interaction, a tool marker or a projected tool fact ends the call as
    ``no-tool-violation``; an unknown-but-legal event ends it as
    ``invalid-protocol``; only a settled answer may be corrected, once, never
    for an enum violation. The object also carries what the caller needs to
    project the run: the correction count and the reason of its own stop
    decision — the driver only reports that the observer interrupted.
    """

    def __init__(self, output_schema: dict, base_prompt: str):
        self.output_schema = output_schema
        self.base_prompt = base_prompt
        self.correction_count = 0
        self.stop_reason: str | None = None

    def observer(self, facts: Mapping) -> RunFeedback:
        if int(facts.get("deniedInteractions") or 0) > 0:
            return self._stop("no-tool-violation")
        if int(facts.get("toolMarkerFrames") or 0) > 0 or int(facts.get("toolCalls") or 0) > 0:
            return self._stop("no-tool-violation")
        unknown = facts.get("unknownEvents")
        total = int(unknown.get("total") or 0) if isinstance(unknown, Mapping) else 0
        if total > 0:
            return self._stop("invalid-protocol")
        if facts.get("settled") and self.correction_count == 0:
            raw = facts.get("rawAnswer")
            code = correction_code(raw, self.output_schema) if isinstance(raw, str) else "answer-invalid-json"
            if code is not None:
                self.correction_count = 1
                return RunFeedback(
                    action="correct",
                    input_text=f"{self.base_prompt}\n\nFormat correction: {code}{CORRECTION_SUFFIX}")
        return FEEDBACK_CONTINUE

    def _stop(self, reason: str) -> RunFeedback:
        if self.stop_reason is None:
            self.stop_reason = reason
        return FEEDBACK_STOP


__all__ = ["CORRECTION_SUFFIX", "FastCorrection", "worker_observer"]
