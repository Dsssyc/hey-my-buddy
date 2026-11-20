"""The role-side observation rules of one unified native run.

These are the judgments ADR-025 keeps out of the native drivers: whether an
observed fact ends the run, and whether one format correction is offered. The
rules are harness-neutral and fact-driven — the driver hands over already
normalized, cumulative facts (unknown-event counts, tool-fact and marker
counts, denied interactions, the count of calls to the mounted snapshot
service, and the settled final answer) and executes only the returned
:class:`~hey_my_buddy.buddy.harnesses.run_contract.RunFeedback`; they never
parse a native protocol frame and never start a native path of their own.

The fast structured call stops on a refused native interaction, an
unexplainable tool marker or a tool call outside the mounted snapshot service,
while the Worker turn keeps working and retains refused interactions for Host
attention. Both keep unknown events as facts. Calls to the mounted snapshot
service do not stop the call: the final ``judge_tool_evidence`` alone decides
whether their signed receipts verified, so a forged or unpaired mount call is
refused at publication instead of hidden by an early stop (ADR-035 decision
3). The correction rule offers at most one correction, never for a hard bound
violation, and assembles the input for the driver's own session policy: a
driver that keeps the native context receives only the precise error
locations, and a driver that opens a fresh native session receives the
reassembled prompt with the bounded previous answer (ADR-035 decision 8).
"""
from __future__ import annotations

from functools import partial
from typing import Mapping

from ...errors import BoardError
from ...protocol.router_selection import FORMAT_ERROR_CODES, validate_answer
from .structured_call import correction_code
from .turn_io import canonical_json
from ..harnesses.run_contract import FEEDBACK_CONTINUE, FEEDBACK_STOP, RunFeedback

#: The correction suffix of the fast structured call, verbatim: the correction
#: code the shared subset validator produced, plus the one instruction line.
CORRECTION_SUFFIX = ". Return exactly the supplied JSON Schema."
#: One fresh-session correction carries a bounded slice of the rejected answer
#: and at most a few precise error locations; the rest stays in the evidence.
MAX_CORRECTION_ANSWER_CHARACTERS = 4000
MAX_CORRECTION_ERRORS = 8


def worker_observer(_facts: Mapping) -> RunFeedback:
    """The Worker turn's rule: every retained fact keeps the run running.

    Unknown events stay counted facts the projection judges later, refused
    native interactions become attention in the turn outcome, and a settled
    root turn needs no correction — the finish receipt is the value. This
    observer never stops the run and never corrects.
    """
    return FEEDBACK_CONTINUE


def selection_validator(document: dict):
    """The one protocol-20 selection check the observer and the publisher share.

    The returned callable applies :func:`validate_answer` to one raw Router
    answer against the frozen document, so the in-run correction decision and
    the blackboard's publication judgment use the same rules.
    """
    return partial(validate_answer, document=document)


def _error_locations(error: BoardError) -> list[dict]:
    errors = error.details.get("errors") if isinstance(error.details, dict) else None
    locations = []
    for item in errors if isinstance(errors, list) else ():
        if isinstance(item, dict):
            locations.append({key: item[key] for key in ("field", "line", "column", "reason", "message")
                              if item.get(key) is not None})
    return locations[:MAX_CORRECTION_ERRORS] or [{"message": (error.message or "")[:256]}]


class FastCorrection:
    """The fast structured call's rule: stop on foreign tools, correct once.

    The precedence is the one the fast carrier always had: a refused native
    interaction, a tool marker or a tool call outside the mounted snapshot
    service ends the call as ``no-tool-violation``; unknown events remain
    facts; only a settled answer may be corrected, once, never for a hard
    bound violation. A hard bound — a legal-shaped answer naming an illegal
    candidate, domain or exploration move — terminates the run at once with
    the bound's own code instead of being retried. The object also carries
    what the caller needs to project the run: the correction count and the
    reason of its own stop decision — the driver only reports that the
    observer interrupted.
    """

    def __init__(self, output_schema: dict, base_prompt: str, *, suffix: str = CORRECTION_SUFFIX,
                 validator=None, session_preserved: bool = False):
        self.output_schema = output_schema
        self.base_prompt = base_prompt
        self.suffix = suffix
        self.validator = validator
        self.session_preserved = session_preserved
        self.correction_count = 0
        self.stop_reason: str | None = None

    def observer(self, facts: Mapping) -> RunFeedback:
        if int(facts.get("deniedInteractions") or 0) > 0:
            return self._stop("no-tool-violation")
        session_calls = int(facts.get("sessionToolCalls") or 0)
        if int(facts.get("toolMarkerFrames") or 0) > 0 or int(facts.get("toolCalls") or 0) > session_calls:
            return self._stop("no-tool-violation")
        if facts.get("settled") and isinstance(facts.get("rawAnswer"), str):
            problem = self._problem(facts.get("rawAnswer"))
            if problem is not None:
                kind, detail = problem
                if kind == "hard":
                    return FEEDBACK_CONTINUE
                if self.correction_count:
                    return FEEDBACK_CONTINUE
                self.correction_count = 1
                return RunFeedback(
                    action="correct",
                    input_text=self._correction_input(facts.get("rawAnswer"), detail))
        return FEEDBACK_CONTINUE

    def _problem(self, raw):
        """Classify one settled answer as a hard bound, a format error or none."""
        if self.validator is None:
            code = correction_code(raw, self.output_schema) if isinstance(raw, str) else "answer-invalid-json"
            return ("format", code) if code is not None else None
        if not isinstance(raw, str) or not raw.strip():
            return ("format", "answer-empty")
        try:
            self.validator(raw)
        except BoardError as error:
            return ("format", error) if error.code in FORMAT_ERROR_CODES else ("hard", error)
        return None

    def _correction_input(self, raw, detail) -> str:
        """Assemble the correction for this driver's own native session policy."""
        description = (canonical_json({"code": detail.code, "errors": _error_locations(detail)})
                       if isinstance(detail, BoardError) else str(detail))
        if self.session_preserved:
            # The native session still holds the prompt, the schema and the
            # mounted query entry; only the precise locations are new input.
            return f"Format correction: {description}{self.suffix}"
        return self._fresh_input(raw, f"Format correction: {description}{self.suffix}")

    def _fresh_input(self, raw, instruction: str) -> str:
        previous = raw if isinstance(raw, str) else ""
        bounded = previous[:MAX_CORRECTION_ANSWER_CHARACTERS]
        truncation = "\n(the previous answer was truncated)" if len(previous) > len(bounded) else ""
        return (f"{self.base_prompt}\n\nYour previous answer was rejected and is kept below as data. "
                f"Previous answer:{truncation}\n{bounded}\n"
                f"{instruction}")

    def _stop(self, reason: str) -> RunFeedback:
        if self.stop_reason is None:
            self.stop_reason = reason
        return FEEDBACK_STOP


class ReviewObserver:
    """Optionally correct a settled review answer once."""

    def __init__(self, output_schema: dict, prompt: str, *, can_correct: bool,
                 validator=None, session_preserved: bool = False):
        self._validate_tools = validator is not None
        self._tool_stop = False
        self.correction = FastCorrection(
            output_schema, prompt,
            suffix=". Return exactly the supplied JSON Schema; do not repeat exploration.",
            validator=validator, session_preserved=session_preserved) if can_correct else None

    @property
    def stop_reason(self):
        if self._tool_stop:
            return "router-tools-forbidden"
        return self.correction.stop_reason if self.correction else None

    def observer(self, facts: Mapping) -> RunFeedback:
        if self._validate_tools and any(facts.get(key) for key in (
                "deniedInteractions", "toolMarkerFrames", "readToolViolations")):
            self._tool_stop = True
            return FEEDBACK_STOP
        if self.correction is not None and facts.get("settled"):
            # Only reuse the format-correction rule, not the fast role's
            # prohibition on tools. It sees the settled answer alone.
            return self.correction.observer({"settled": True, "rawAnswer": facts.get("rawAnswer")})
        return FEEDBACK_CONTINUE


class MaintenanceCorrection(FastCorrection):
    """Stage valid entries at each settled answer, correcting rejected ones twice.

    The publisher sends a private file reference to the service for validation
    and staging. The correction input follows the driver’s session policy.
    """

    def __init__(self, output_schema, base_prompt, publisher, *, session_preserved: bool = False):
        super().__init__(output_schema, base_prompt, session_preserved=session_preserved)
        self.publisher = publisher

    def observer(self, facts):
        feedback = super().observer({**facts, "settled": False})
        if feedback.action == "stop" or not facts.get("settled"):
            return feedback
        checked = self.publisher(facts.get("rawAnswer"), self.correction_count, {
            key: int(facts.get(key) or 0) for key in ("toolCalls", "toolMarkerFrames", "deniedInteractions")})
        if checked["errors"] and self.correction_count < 2:
            self.correction_count += 1
            body = ("Maintenance correction: valid entries are already staged. "
                    "Correct the rejected entries described below; preserve accepted classifications and summaries.\n"
                    + canonical_json(checked))
            # The same session-policy rule as the fast correction: a context
            # preserving driver needs only the rejected entries, a fresh
            # session needs the bounded request head and tool instructions again.
            return RunFeedback(action="correct",
                               input_text=body if self.session_preserved
                               else self._fresh_input(facts.get("rawAnswer"), body))
        return FEEDBACK_CONTINUE


__all__ = [
    "CORRECTION_SUFFIX", "FastCorrection", "MaintenanceCorrection", "ReviewObserver",
    "selection_validator", "worker_observer",
]
