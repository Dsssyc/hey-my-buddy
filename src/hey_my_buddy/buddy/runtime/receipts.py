"""One execution's durable result and its shared, unbounded delivery path."""
from __future__ import annotations

import json
import time
from pathlib import Path

from ...errors import BoardError

STARTUP = "execution-start.json"
RECEIPT = "result-receipt.json"
DELIVERY = "result-delivery.json"
RETRYABLE = frozenset({"SERVICE_UNAVAILABLE", "SERVICE_START_TIMEOUT", "SERVICE_START_FAILED",
                       "INVALID_RESPONSE", "INTERNAL_ERROR"})


def read_receipt(directory: Path) -> dict | None:
    from ..roles.turn_io import guard_private_path
    try:
        return json.loads(guard_private_path(directory / RECEIPT).read_text())
    except FileNotFoundError:
        return None


def write_receipt(directory: Path, receipt: dict) -> None:
    from .worker import fsync_json
    fsync_json(directory / RECEIPT, receipt)


def deliver_receipt(client, receipt: dict, directory: Path, *, log=lambda message: None,
                    flush_hints=None) -> bool:
    """Keep the execution alive until its result is confirmed or explicitly refused."""
    from .worker import fsync_json
    if receipt["report"]["shutdownConfirmed"] is True:
        (directory / STARTUP).unlink(missing_ok=True)
    delay = 0.25
    while True:
        if flush_hints is not None:
            # Hints may fail independently; their delivery never gates the result.
            try:
                flush_hints()
            except Exception as error:
                log(f"hint delivery unavailable: {error!r}")
        try:
            client.submit_result(receipt["workerId"], receipt["attemptId"], receipt["generation"],
                                 receipt["nonce"], {**receipt["report"], "commandId": receipt["commandId"]})
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
        # A crash before it is written replays the same idempotent command.
        fsync_json(directory / DELIVERY, {"attemptId": receipt["attemptId"], "accepted": accepted})
        (directory / RECEIPT).unlink(missing_ok=True)
        return accepted
