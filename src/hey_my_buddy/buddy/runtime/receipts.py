"""One execution's durable result and its shared, unbounded delivery path."""
from __future__ import annotations

import time
from pathlib import Path

from ...protocol.execution import (fsync_json, read_execution_receipt as read_receipt,
                                  receipt_reference, RECEIPT, REFUSED)

from ...errors import BoardError

# SERVICE_UNAVAILABLE and UPGRADE_IN_PROGRESS cover an offline and a fenced
# service. The C-Two codes are the transient face of one board-side fact: the
# old daemon of a same-contract hot swap closing its IPC listener while this
# execution's idempotent call was in flight, so the replay reaches the new
# daemon. Contract, identity, protocol and internal defects stay terminal.
RETRYABLE = frozenset({"SERVICE_UNAVAILABLE", "UPGRADE_IN_PROGRESS",
                       "C_TWO_AT_CLIENT_CALLING_RESOURCE", "C_TWO_TRANSPORT", "C_TWO_RESOURCE_CLOSED",
                       "C_TWO_RESOURCE_UNAVAILABLE", "C_TWO_RESOURCE_NOT_FOUND", "C_TWO_ROUTE_STALE"})


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
