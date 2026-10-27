"""One execution's durable result and its shared, unbounded delivery path."""
from __future__ import annotations

import time
from pathlib import Path

from ...protocol.execution import (fsync_json, read_execution_receipt as read_receipt,
                                  receipt_reference, RECEIPT, REFUSED)

from ...errors import BoardError

RETRYABLE = frozenset({"SERVICE_UNAVAILABLE", "UPGRADE_IN_PROGRESS"})


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
                if error.code not in RETRYABLE:
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
