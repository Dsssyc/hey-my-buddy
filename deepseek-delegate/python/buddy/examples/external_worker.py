"""One caller-owned external agent that really participates through the public API.

Run it against a running Buddy service; it registers itself as an ``external``
worker, claims a real submitted task (no argv and no dsh pretence), executes the
task text with its own logic, publishes an artifact and reports the result.

    BUDDY_STATE_DIR=/path/to/state python -m buddy.examples.external_worker --worker-id my-agent

Submit a task for it first:

    buddy submit '{"requestId":"ext-1","task":"summarize the repository","cwd":"/abs/path","adapter":"external"}'
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from .. import transport
from ..client import BoardClient, new_nonce
from ..errors import BoardError
from ..worker.worker import fsync_json

CAPABILITIES = ["external", "artifacts", "task-text"]


def execute_task_text(task_text: str) -> str:
    """The caller's own agent logic. Replace this with a real model/tool call."""
    return f"external agent handled {len(task_text)} characters of task text"


def run_once(board: BoardClient, worker_id: str, state_dir: Path) -> bool:
    nonce = new_nonce()
    claim_request_id = f"claim-{worker_id}-{int(time.time() * 1000)}"
    # Persist the startup intent before claiming, so a lost reply is recoverable.
    fsync_json(
        state_dir / "workers" / worker_id / "startup.json",
        {"workerId": worker_id, "instanceId": f"{worker_id}-{nonce[:8]}", "nonce": nonce,
         "claimRequestId": claim_request_id, "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
    )
    response = board.claim(worker_id, claim_request_id, nonce, worker_instance=f"{worker_id}-{nonce[:8]}")
    claim = response.get("claim")
    if not claim:
        return False
    attempt = claim["attempt"]
    task = claim["task"]
    output = state_dir / "attempts" / task["taskId"] / attempt["attemptId"] / "external-report.txt"
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    body = execute_task_text(task["task"])
    output.write_text(body)
    board.progress(worker_id, attempt["attemptId"], attempt["generation"], nonce, "agent finished", phase="executing")
    board.submit_result(
        worker_id,
        attempt["attemptId"],
        attempt["generation"],
        nonce,
        {
            "status": "ok",
            "result": {"status": "ok", "mode": "external", "finalText": body},
            "shutdownConfirmed": True,
            "artifacts": [
                {
                    "kind": "result",
                    "location": str(output.resolve()),
                    "contentHash": hashlib.sha256(output.read_bytes()).hexdigest(),
                    "sizeBytes": output.stat().st_size,
                }
            ],
        },
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-id", default="external-agent")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--once", action="store_true", help="Claim and finish at most one task, then exit")
    arguments = parser.parse_args(argv)
    state_dir = transport.get_state_dir(arguments.state_dir)
    board = BoardClient(state_dir)
    board.register_worker(arguments.worker_id, adapter="external", capabilities=CAPABILITIES)
    print(json.dumps({"registered": arguments.worker_id, "stateDir": str(state_dir), "capabilities": CAPABILITIES}))
    while True:
        try:
            worked = run_once(board, arguments.worker_id, state_dir)
        except BoardError as error:
            if error.code in ("SERVICE_UNAVAILABLE", "WORKER_BUSY"):
                time.sleep(2)
                continue
            raise
        if arguments.once:
            return 0 if worked else 1
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
