"""One execution's durable result and its shared, unbounded delivery path."""
from __future__ import annotations

import time
from pathlib import Path

from ...protocol.execution import fsync_json, read_execution_receipt as read_receipt, RECEIPT

from ...errors import BoardError

DELIVERY = "result-delivery.json"
RETRYABLE = frozenset({"SERVICE_UNAVAILABLE", "SERVICE_START_TIMEOUT", "SERVICE_START_FAILED",
                       "INVALID_RESPONSE", "INTERNAL_ERROR", "UPGRADE_IN_PROGRESS"})


def receipt_directory(state: Path, private_adapter: str, task_id: str, attempt_id: str) -> Path:
    from ...private_dirs import attempt_root
    return attempt_root(state, private_adapter, task_id, attempt_id)


def write_receipt(directory: Path, receipt: dict) -> None:
    fsync_json(directory / RECEIPT, receipt)


def deliver_receipt(client, receipt: dict, directory: Path, *, nonce: str, log=lambda message: None,
                    flush_hints=None) -> bool:
    """Keep the execution alive until its result is confirmed or explicitly refused."""
    delay = 0.25
    while True:
        if flush_hints is not None:
            # A disconnected service or an installation fence may delay hints.
            # Identity rejection is terminal and must reach the caller.
            flush_hints()
        try:
            client.submit_result(receipt["attemptId"], receipt["generation"],
                                 nonce, receipt["report"])
        except BoardError as error:
            if error.code not in RETRYABLE:
                accepted = False
                log(f"result refused: {error.code}")
            else:
                log(f"result delivery pending: {error.code}")
                time.sleep(delay)
                delay = min(10.0, delay * 2)
                continue
        except Exception as error:
            log(f"result delivery unavailable: {error!r}")
            time.sleep(delay)
            delay = min(10.0, delay * 2)
            continue
        else:
            accepted = True
        # The acknowledgement is durable before removing the pending receipt.
        # A crash before it is written replays the same attempt result.
        fsync_json(directory / DELIVERY, {"attemptId": receipt["attemptId"], "accepted": accepted})
        (directory / RECEIPT).unlink(missing_ok=True)
        return accepted
