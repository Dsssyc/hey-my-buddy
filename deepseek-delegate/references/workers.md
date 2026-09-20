# Workers and adapters

Built-in tasks run in a Worker object inside an **independent supervisor process**,
which owns the adapter child handles. External agents claim work directly over RPC and
manage their own execution. This page covers the adapters, public worker contract, identity
and receipts, and capability-specific behavior. Internal state transitions are in
[architecture.md](architecture.md).

## Adapters

`buddy capabilities` (also `buddy adapters`) reports each adapter with `executedBy`,
availability and reason, the service's local capabilities, the named operations, wait
admission and its honest limitations.

| Adapter | `executedBy` | Capabilities | Requirements |
| --- | --- | --- | --- |
| `dsh` (default) | `built-in-worker` | `dsh`, `inquiry`, `workspace`, `cancel`, `artifacts`, `deadline` | Node binary and the runner entrypoint; otherwise `ADAPTER_UNAVAILABLE` |
| `command` | `built-in-worker` | `command`, `cancel`, `artifacts`, `deadline`, `argv` | `argv` with 1–256 entries; `argv[0]` must resolve |
| `external` | `caller-owned-agent` | `external`, `artifacts`, `task-text` | no local process; the caller's agent claims and reports the task itself |

- The `dsh` adapter spawns the existing Node runner `scripts/run.mjs`, which runs
  `dsh --profile headless` with a real per-run model/effort override and writes the
  per-attempt bridge credentials to `<state>/attempts/<runId>/<attemptId>/inquiry.json`
  (mode `0600`, never exposed in a public task view). It maps the runner outcome
  honestly: only exit 0 with runner status `ok` and confirmed shutdown is `ok`; a cancel
  without confirmed shutdown is `failed`; unparseable runner output is `invalid-result`.
- The `command` adapter runs exactly one explicit `argv` process with `shell=False`.
  `argv` is valid only for this adapter, and its result note states that exit 0 means the
  command exited successfully, not that the task is correct.
- The `external` adapter has no local executor. `adapter("external")` reports
  `UNSUPPORTED_ADAPTER` to a built-in worker, so an external task stays `queued` until a
  caller-owned agent claims it; a built-in worker that scans it reports `adapter-mismatch`
  rather than running it with the wrong adapter.
- `localCapabilities` lists only what a built-in worker host can honestly advertise;
  `external` is deliberately absent. Only `dsh` has an inquiry bridge: `inquire` on
  `command`/`external` reports `this adapter has no inquiry capability` instead of
  inventing progress.
- A `dsh` or `command` task whose adapter turns out to be unusable fails that attempt
  with `ADAPTER_UNAVAILABLE` and confirmed shutdown when no process was created.

## The public worker contract

`deepseek-delegate/python/buddy/client.py` is the supported way for a separate agent to
participate without importing service internals, touching SQLite or reading the daemon
token. `BoardClient` exposes:

- `register_worker`, `claim`, `reconcile`, `renew`, `progress`, `submit_result`,
  `release`, `workers`;
- `post_question`, `update_message`, `get_message`, `list_messages`, `wait_message`;
- `read_events`, `wait_events`, `artifacts`;
- task helpers `submit`, `get`, `cancel`, `retry`, `acknowledge`, `wait_task`, `result`,
  `list_tasks`;
- `new_nonce()` for a claim capability and `new_command_id()` for a replay-safe command
  ID.

`BoardClient(state_dir, autostart=False)` never cold-starts a service for any operation;
it attaches to an existing one read-only or raises `SERVICE_UNAVAILABLE`. The default
`autostart=True` attaches when a service is healthy and otherwise starts one.

### A runnable external worker

Submit a small demonstration task in a disposable directory:

```sh
"$BUDDY" submit '{"requestId":"external-example-1","adapter":"external","cwd":"/abs/demo-directory","task":"Write external-report.txt containing handled by the caller-owned agent."}'
```

Save the following as `worker.py`, set `task_id` to the returned run ID, and run
`uv run --frozen --project deepseek-delegate python worker.py`. It handles only that
task with a short synchronous operation. `fsync_json` is the bundled local-file helper;
all board access goes through `BoardClient`.

```python
from pathlib import Path
import hashlib
from uuid import uuid4

from buddy.client import BoardClient, new_nonce
from buddy.worker.worker import fsync_json

state = Path.home() / ".local/share/hey-my-buddy"
board = BoardClient(state)
task_id = "<runId returned above>"
worker_id = "example-" + uuid4().hex
board.register_worker(worker_id, adapter="external",
                      capabilities=["external", "artifacts", "task-text"])

nonce = new_nonce()
intent = {"workerId": worker_id, "nonce": nonce, "claimRequestId": "claim-" + uuid4().hex}
fsync_json(state / "workers" / worker_id / "startup.json", intent)

response = board.claim(worker_id, intent["claimRequestId"], nonce, task_id=task_id)
claim = response["claim"]
if claim is None:
    raise SystemExit(response.get("reason", "task is not claimable"))
attempt, task = claim["attempt"], claim["task"]
target = Path(task["cwd"]) / "external-report.txt"
target.write_text("handled by the caller-owned agent\n")
board.progress(worker_id, attempt["attemptId"], attempt["generation"], nonce,
               "agent finished the work", phase="executing")
board.submit_result(worker_id, attempt["attemptId"], attempt["generation"], nonce, {
    "status": "ok",
    "result": {"status": "ok", "mode": "external", "finalText": target.read_text()},
    "shutdownConfirmed": True,
    "artifacts": [{"kind": "result", "location": str(target),
                   "contentHash": hashlib.sha256(target.read_bytes()).hexdigest(),
                   "sizeBytes": target.stat().st_size}],
})
```

Persisting the nonce and `claimRequestId` **before** claiming is what makes a committed
claim with a lost reply recoverable: replaying the identical claim returns the same
attempt, generation and a re-derived capability instead of minting a new generation.
`reconcile` reattaches after daemon downtime, and `release` gives back an attempt that
was claimed but never spawned. The same flow is exercised end to end by
`python/tests/test_blackboard.py::TestExtensibility::test_an_external_agent_completes_a_real_task_through_the_public_api`.

For recovery, load the recorded identity and replay its claim; restarting this short
example generates a fresh identity. A long-running agent must also renew its lease,
observe cancellation, enforce its own execution deadline, and persist/replay completion
receipts as described below. The demonstration does not implement that supervisor loop.

## Worker identity and receipts

- `worker_register` accepts `workerId` (required), `identity` (default `worker:<id>`),
  `adapter` (default `dsh`), `capabilities` (≤32), `host`, `pid` and a `commandId`
  receipt. Re-registering refreshes the record; `duplicate` is always false today.
- `worker_claim` requires `workerId`, `claimRequestId` and a `nonce` (16–256 chars), and
  may target a `taskId`/`runId`. A worker that is unregistered, `stopping`, or already
  running an attempt is refused (`NOT_REGISTERED`, `WORKER_STOPPING`, `WORKER_BUSY`). An
  empty claim returns `claim: null` with a `reason` (`no-queued-work`, `not-queued`,
  `adapter-mismatch`, `capability-mismatch`, `capacity`, `cwd-overlap`,
  `exclusive-resource`) and a retry delay.
- An attempt is identified by `(attemptId, generation, nonce)` plus the worker instance.
  The claim capability is derived from a service secret and the nonce; only a nonce
  verifier is stored. Superseded generations are rejected with `STALE_GENERATION`, and
  command receipts are bound to `workerId:nonceVerifier:attempt` so another worker
  cannot replay them.
- `worker_renew` extends the lease and reports `cancelRequested`, which is how a worker
  learns about durable cancel intent. `worker_progress` appends a bounded progress event.
  A finished attempt refuses both.
- `worker_result` commits the result, artifacts, task/attempt state and the completion
  event in one transaction. Artifacts are verified outside the write transaction by
  existence, size and SHA-256; a mismatch is `ARTIFACT_MISSING`/`ARTIFACT_MISMATCH` and
  publishes no completed result. Replaying the identical result is idempotent; a
  conflicting replay of a terminal attempt is `CONFLICT`.
- Shutdown evidence is explicit: an `ok` result without confirmed shutdown becomes
  `reconciliation-needed`; a confirmed cancel becomes `cancelled`, an unconfirmed one
  stays `reconciliation-needed` with claims retained. `retry` is refused while shutdown
  is unconfirmed.
- `worker_release` can release a starting attempt without explicit evidence. When
  evidence is supplied, the accepted proof is `{"spawnIntentWritten": false}` and the
  claiming worker instance is checked. A started attempt cannot be released merely
  because its PID disappeared or its lease expired.

## Worker supervisors

The daemon starts one detached supervisor for `BUDDY_WORKER_ID` (default `local`) at
startup. An operator can start another, or ask one to stop cooperatively:

```sh
"$BUDDY" worker-start '{"workerId":"local"}'
"$BUDDY" worker-stop  '{"workerId":"local"}'
```

`worker-start` runs `python -m buddy.worker.supervisor` in its own session with
file-backed logs at `<state>/worker.log`, and returns `workerId`, `supervisorPid`,
`logPath` and `stateDir`. The supervisor holds an exclusive lock
(`workers/<workerId>/supervisor.lock`), so liveness is decided by lock ownership rather
than a stored PID. It restarts the Worker loop after an exception with bounded backoff
and observes `workers/<workerId>/stop.request`; it cannot restart itself after its
entire OS process exits. `worker-stop` only writes that durable request; it
never signals a process the CLI did not create.

Workers keep an immutable local completion receipt under
`<state>/workers/<workerId>/receipts/<attemptId>.json` until the service confirms the
result transaction, and a `spawn.intent`/`spawn.marker` pair under
`<state>/attempts/<runId>/<attemptId>/` makes the crash window explicit. Unsatisfied
receipts are replayed by the next supervisor start, and a durable receipt is never
executed a second time. A startup intent left by a previous process is preserved as
orphaned evidence: the new process holds no child handle, so the attempt stays
`uncertain` with its claims retained. `buddy workers` lists registered workers with their
state (`starting`, `idle`, `busy`, `stopping`, `lost`), adapter, capabilities and current
attempt.
