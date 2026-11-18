"""One execution's durable result and its shared, unbounded delivery path."""
from __future__ import annotations

import time
from pathlib import Path

from ...protocol.execution import (fsync_json, read_execution_receipt as read_receipt,
                                  receipt_reference, RECEIPT, REFUSED)

from ...errors import BoardError


def is_retryable(error: BoardError, *, pre_dispatch_only: bool = False) -> bool:
    """Recognize an unavailable service, withdrawn route or transient IPC failure."""
    if error.code in ("SERVICE_UNAVAILABLE", "UPGRADE_IN_PROGRESS",
                      "C_TWO_RESOURCE_CLOSED", "C_TWO_RESOURCE_REMOVED",
                      "C_TWO_RESOURCE_UNAVAILABLE", "C_TWO_RESOURCE_NOT_FOUND", "C_TWO_ROUTE_STALE"):
        return True
    details = error.details
    phases = ("pre_dispatch",) if pre_dispatch_only else ("pre_dispatch", "dispatch_uncertain")
    return (error.code == "C_TWO_AT_CLIENT_CALLING_RESOURCE"
            and details.get("category") == "transport"
            and details.get("transport_kind") == "ipc"
            and details.get("io_kind") != "other"
            and details.get("transport_phase") in phases)


def receipt_directory(state: Path, private_adapter: str, task_id: str, attempt_id: str) -> Path:
    from ...private_dirs import attempt_root
    return attempt_root(state, private_adapter, task_id, attempt_id)


def write_receipt(directory: Path, receipt: dict) -> None:
    fsync_json(directory / RECEIPT, receipt, immutable=True)


def deliver_receipt(client, receipt: dict, directory: Path, *, nonce: str, log=lambda message: None,
                    flush_hints=None) -> bool:
    """Retry offline delivery; retain unknown outcomes for service recovery."""
    reference = receipt_reference(directory)
    delay = 0.25
    refusal = None
    while True:
        # Hints and results share this delivery wait. Identity rejection from
        # flushing hints is terminal and must reach the caller.
        if flush_hints is None or flush_hints():
            try:
                client.submit_result(receipt["attemptId"], receipt["generation"],
                                     nonce, reference)
            except BoardError as error:
                if error.code in REFUSED:
                    accepted = False
                    refusal = {"code": error.code, "message": error.message}
                    break
                if not is_retryable(error):
                    raise
                log(f"result delivery pending: {error.code}")
            else:
                accepted = True
                break
        time.sleep(delay)
        delay = min(10.0, delay * 2)
    if refusal is not None:
        log(f"result refused: {refusal['code']}: {refusal['message']}")
    return accepted
