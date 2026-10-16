"""Client for one execution to report progress and results to the board."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

from . import transport
from ..errors import BoardError

def new_command_id(prefix: str = "cmd") -> str:
    return f"{prefix}-{uuid.uuid4()}"


class BoardClient:
    """One client for the same-user local blackboard."""

    def __init__(
        self,
        state_dir: str | Path | None = None,
        *,
        call: Callable[[str, dict], dict] | None = None,
        autostart: bool = True,
    ):
        self.state_dir = state_dir
        self._call = call
        self.autostart = autostart

    # -- plumbing -----------------------------------------------------------
    def call(self, operation: str, params: dict | None = None, *, resource: str = "control") -> dict:
        if self._call is not None:
            return self._call(operation, params or {})
        directory = transport.get_state_dir(self.state_dir)
        if not self.autostart:
            return transport._call_board_read_only(operation, params, directory, resource=resource)
        return transport._call_board(operation, params or {}, directory, resource=resource)

    # -- service ------------------------------------------------------------
    def ping(self) -> dict:
        return self.call("ping")

    def health(self) -> dict:
        return self.call("health")

    def capabilities(self) -> dict:
        return self.call("capabilities")

    # -- tasks --------------------------------------------------------------
    def submit(self, **params: Any) -> dict:
        return self.call("task_submit", params)

    def get(self, **selector: Any) -> dict:
        return self.call("task_get", selector)["task"]

    def list_tasks(self, **params: Any) -> dict:
        return self.call("task_list", params)

    def result(self, **selector: Any) -> dict:
        return self.call("task_result", selector)

    def cancel(self, **selector: Any) -> dict:
        return self.call("task_cancel", selector)["task"]

    def retry(self, *, reason: str | None = None, **selector: Any) -> dict:
        return self.call("task_retry", {**selector, "reason": reason})["task"]

    def acknowledge(self, note: str, verdict: str = "accepted", **selector: Any) -> dict:
        return self.call("task_acknowledge", {**selector, "note": note, "verdict": verdict})["task"]

    def wait_task(self, timeout_ms: int = 30000, **selector: Any) -> dict:
        return self.call("task_wait", {**selector, "timeoutMs": timeout_ms}, resource="wait")

    # -- executions ---------------------------------------------------------
    def progress(
        self,
        attempt_id: str,
        generation: int,
        nonce: str,
        message: str | None = None,
        *,
        process_identity: dict | None = None,
        phase: str | None = None,
        data: dict | None = None,
    ) -> dict:
        """Report one attempt observation.

        ``data`` is the bounded structured channel; ``data.activity`` carries the
        native-activity projection defined by ``hey_my_buddy.protocol.activity`` (see the reference).
        A plain heartbeat must not put fabricated activity there, and a structured
        observation with no prose ``message`` records no prose progress event.
        """
        params: dict = {
            "attemptId": attempt_id,
            "generation": generation,
            "nonce": nonce,
            "phase": phase,
        }
        if process_identity is not None:
            params["processIdentity"] = process_identity
        if message is not None:
            params["message"] = message
        if data is not None:
            params["data"] = data
        return self.call("execution_progress", params)

    def submit_result(self, attempt_id: str, generation: int, nonce: str, report: dict) -> dict:
        return self.call(
            "execution_result",
            {
                "attemptId": attempt_id,
                "generation": generation,
                "nonce": nonce,
                **report,
            },
        )

    # -- messages -----------------------------------------------------------
    def post_question(self, inquiry_id: str, question: str, *, author: str = "external", **selector: Any) -> dict:
        return self.call(
            "message_post",
            {**selector, "inquiryId": inquiry_id, "question": question, "author": author},
        )

    def update_message(self, inquiry_id: str, *, state: str | None = None, reason: str | None = None, delivery: dict | None = None, answer: dict | None = None, **selector: Any) -> dict:
        return self.call(
            "message_update",
            {
                **selector,
                "inquiryId": inquiry_id,
                "state": state,
                "reason": reason,
                "delivery": delivery,
                "answer": answer,
            },
        )

    def get_message(self, inquiry_id: str, **selector: Any) -> dict:
        return self.call("message_get", {**selector, "inquiryId": inquiry_id})["message"]

    def list_messages(self, **params: Any) -> dict:
        return self.call("message_list", params)

    def wait_message(self, inquiry_id: str, timeout_ms: int = 30000, **selector: Any) -> dict:
        return self.call(
            "message_wait", {**selector, "inquiryId": inquiry_id, "timeoutMs": timeout_ms}, resource="wait"
        )

    # -- events -------------------------------------------------------------
    def read_events(self, after: int = 0, limit: int = 100, **selector: Any) -> dict:
        return self.call("events_read", {**selector, "after": after, "limit": limit})

    def wait_events(self, after: int = 0, timeout_ms: int = 30000, limit: int = 100, **selector: Any) -> dict:
        return self.call(
            "events_wait", {**selector, "after": after, "timeoutMs": timeout_ms, "limit": limit}, resource="wait"
        )

    def artifacts(self, **selector: Any) -> dict:
        return self.call("artifact_list", selector)

    # -- console and routing configuration ----------------------------------
    def console_snapshot(self) -> dict:
        return self.call("console_snapshot")

    def console(self, action: str = "open") -> dict:
        return self.call("console", {"action": action})

    def model_catalog_refresh(self, request_id: str | None = None) -> dict:
        return self.call("model_catalog_refresh", {"requestId": request_id})

    # -- micro-task workflow --------------------------------------------------
    def workflow_submit(self, **params: Any) -> dict:
        return self.call("workflow_submit", params)

    def workflow_get(self, run_id: str, *, include_audit: bool = False) -> dict:
        return self.call("workflow_get", {"runId": run_id, "includeAudit": include_audit})

    def workflow_decide(self, **params: Any) -> dict:
        return self.call("workflow_decide", params)

    def workflow_continue(self, **params: Any) -> dict:
        return self.call("workflow_continue", params)

    def workflow_takeover(self, **params: Any) -> dict:
        return self.call("workflow_takeover", params)

    def workflow_cancel(self, **params: Any) -> dict:
        return self.call("workflow_cancel", params)

    def workflow_accept(self, **params: Any) -> dict:
        return self.call("workflow_accept", params)

    def workflow_conclude(self, **params: Any) -> dict:
        return self.call("workflow_conclude", params)

    def workflow_reclaim(self, **params: Any) -> dict:
        return self.call("workflow_reclaim", params)

    def workflow_suggest(self, run_id: str, body: str) -> dict:
        return self.call("workflow_suggest", {"runId": run_id, "body": body})


__all__ = ["BoardClient", "BoardError", "new_command_id"]
