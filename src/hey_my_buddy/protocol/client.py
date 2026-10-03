"""Public board client: the supported way for an external worker to participate.

A third-party agent imports this module (or speaks the same C-Two contract) and can
register, claim, renew, report progress, submit a result and ask/answer questions
without importing service internals, touching SQLite or reading the daemon's token.

Example::

    from hey_my_buddy.protocol.client import BoardClient

    board = BoardClient()
    board.register_worker("my-agent", adapter="command", capabilities=["command", "argv"])
    claim = board.claim("my-agent", claim_request_id, nonce)
    ...
    board.submit_result("my-agent", attempt["attemptId"], generation, nonce, report)
"""
from __future__ import annotations

import os
import socket
import secrets
import uuid
from pathlib import Path
from typing import Any, Callable

from . import transport
from ..errors import BoardError

DEFAULT_LEASE_SECONDS = 120


def new_nonce() -> str:
    return secrets.token_hex(32)


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
        if not self.autostart:
            # ``autostart=False`` means exactly that, for every resource: observe an
            # existing service read-only and never cold-start one as a side effect.
            endpoint = transport._attach_read_only(transport.get_state_dir(self.state_dir))
            if endpoint is None:
                raise transport.ServiceError(
                    "SERVICE_UNAVAILABLE", "No board service is running in this state directory"
                )
            return transport._request(endpoint, operation, params or {}, resource=resource)
        return transport.call_board(operation, params or {}, self.state_dir, resource=resource)

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

    # -- workers ------------------------------------------------------------
    def register_worker(
        self,
        worker_id: str,
        *,
        adapter: str = "dsh",
        capabilities: list[str] | None = None,
        identity: str | None = None,
        pid: int | None = None,
        command_id: str | None = None,
    ) -> dict:
        return self.call(
            "worker_register",
            {
                "workerId": worker_id,
                "identity": identity or f"{adapter}:{worker_id}",
                "adapter": adapter,
                "capabilities": capabilities if capabilities is not None else [adapter],
                "pid": pid if pid is not None else os.getpid(),
                "host": socket.gethostname(),
                "commandId": command_id,
            },
        )

    def claim(
        self,
        worker_id: str,
        claim_request_id: str,
        nonce: str,
        *,
        task_id: str | None = None,
        worker_instance: str | None = None,
    ) -> dict:
        return self.call(
            "worker_claim",
            {
                "workerId": worker_id,
                "claimRequestId": claim_request_id,
                "nonce": nonce,
                "taskId": task_id,
                "workerInstance": worker_instance,
            },
        )

    def reconcile(
        self,
        worker_id: str,
        attempt_id: str,
        generation: int,
        nonce: str,
        *,
        claim_request_id: str | None = None,
        worker_instance: str | None = None,
    ) -> dict:
        return self.call(
            "worker_reconcile",
            {
                "workerId": worker_id,
                "attemptId": attempt_id,
                "generation": generation,
                "nonce": nonce,
                "claimRequestId": claim_request_id,
                "workerInstance": worker_instance,
            },
        )

    def renew(self, worker_id: str, attempt_id: str, generation: int, nonce: str, *, phase: str | None = None) -> dict:
        return self.call(
            "worker_renew",
            {
                "workerId": worker_id,
                "attemptId": attempt_id,
                "generation": generation,
                "nonce": nonce,
                "phase": phase,
            },
        )

    def progress(
        self,
        worker_id: str,
        attempt_id: str,
        generation: int,
        nonce: str,
        message: str | None = None,
        *,
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
            "workerId": worker_id,
            "attemptId": attempt_id,
            "generation": generation,
            "nonce": nonce,
            "phase": phase,
        }
        if message is not None:
            params["message"] = message
        if data is not None:
            params["data"] = data
        return self.call("worker_progress", params)

    def submit_result(self, worker_id: str, attempt_id: str, generation: int, nonce: str, report: dict) -> dict:
        return self.call(
            "worker_result",
            {
                "workerId": worker_id,
                "attemptId": attempt_id,
                "generation": generation,
                "nonce": nonce,
                **report,
            },
        )

    def release(
        self,
        worker_id: str,
        attempt_id: str,
        generation: int,
        nonce: str,
        reason: str = "",
        *,
        evidence: dict | None = None,
        worker_instance: str | None = None,
    ) -> dict:
        return self.call(
            "worker_release",
            {
                "workerId": worker_id,
                "attemptId": attempt_id,
                "generation": generation,
                "nonce": nonce,
                "reason": reason or None,
                "evidence": evidence,
                "workerInstance": worker_instance,
            },
        )

    def workers(self, **params: Any) -> dict:
        return self.call("worker_list", params)

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

    # -- console and the evaluation table -----------------------------------
    def console_snapshot(self) -> dict:
        return self.call("console_snapshot")

    def console(self, action: str = "open") -> dict:
        return self.call("console", {"action": action})

    def evaluation_write_begin(self, request_id: str, expected_revision: int, *, kind: str = "human") -> dict:
        return self.call(
            "evaluation_write_begin",
            {"requestId": request_id, "expectedRevision": expected_revision, "kind": kind},
        )

    def evaluation_write_renew(self, writer_id: str, generation: int, writer_token: str) -> dict:
        return self.call(
            "evaluation_write_renew",
            {"writerId": writer_id, "generation": generation, "writerToken": writer_token},
        )

    def evaluation_write_publish(
        self,
        command_id: str,
        writer_id: str,
        generation: int,
        writer_token: str,
        expected_revision: int,
        **collections: Any,
    ) -> dict:
        return self.call(
            "evaluation_write_publish",
            {
                "commandId": command_id,
                "writerId": writer_id,
                "generation": generation,
                "writerToken": writer_token,
                "expectedRevision": expected_revision,
                **collections,
            },
        )

    def evaluation_write_abort(self, command_id: str, writer_id: str, generation: int, writer_token: str) -> dict:
        return self.call(
            "evaluation_write_abort",
            {
                "commandId": command_id,
                "writerId": writer_id,
                "generation": generation,
                "writerToken": writer_token,
            },
        )

    def evaluation_reader_begin(self, *, kind: str = "selection", revision: int | None = None) -> dict:
        return self.call(
            "evaluation_reader_begin", {"kind": kind, **({"revision": revision} if revision is not None else {})}
        )

    def evaluation_reader_release(self, reader_id: str) -> dict:
        return self.call("evaluation_reader_release", {"readerId": reader_id})

    def evaluation_evidence_record(
        self,
        profile_id: str,
        kind: str,
        summary: str,
        source: str,
        *,
        project: str | None = None,
        conditions: list[str] | None = None,
        run_id: str | None = None,
        command_id: str | None = None,
    ) -> dict:
        return self.call(
            "evaluation_evidence_record",
            {
                "commandId": command_id,
                "profileId": profile_id,
                "kind": kind,
                "summary": summary,
                "project": project,
                "conditions": conditions or [],
                "source": source,
                "runId": run_id,
            },
        )

    def model_catalog_refresh(self, request_id: str | None = None) -> dict:
        return self.call("model_catalog_refresh", {"requestId": request_id})

    # -- governed workflow --------------------------------------------------
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


__all__ = ["BoardClient", "BoardError", "new_command_id", "new_nonce", "DEFAULT_LEASE_SECONDS"]
