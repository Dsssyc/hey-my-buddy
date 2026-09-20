# Architecture

This page describes the **implemented 0.4.0 architecture** and is verified against the
source under `python/buddy/`, `scripts/run.mjs` and `plugins/`. The repository's
`docs/decisions/001-python-transactional-blackboard.md` (ADR-001) is the historical
design record from which this implementation was built; where its wording describes a
requirement rather than the shipped behavior, this page states what the code actually
does today.

## Process topology

```text
Codex skill / CLI / dashboard / external worker
                  |  named C-Two RPC (private token)
        Python blackboard daemon  <-- only writer of authoritative state
          state machine + transactions + events
                  |  SQLite WAL (board.sqlite3)
        tasks / attempts / workers / messages / artifacts
        events / command receipts / resource claims

Independent supervisor process -- C-Two --> daemon
  Worker.run() executes inside this supervisor process
    | owns the child process handle, deadline and local receipt
    +-- dsh adapter -----> scripts/run.mjs -> dsh (Node)
    +-- command adapter -> one explicit argv process

Caller-owned external agent -- BoardClient / C-Two --> daemon
  claims external tasks and reports its own results
```

The daemon and each supervisor are separate OS processes. A supervisor runs the Worker
object in its own process and restarts that execution loop after an exception. The
Worker owns the adapter child handles and enforces the execution deadline while the
daemon is unavailable. External agents participate directly through RPC. Stored PIDs
are diagnostic values, never sufficient authority to signal a process.

## C-Two surface

Service operations use named C-Two methods: `BuddyControl` on the `buddy-control`
resource (mutations, reads, lifecycle) and
`BuddyWait` on the separate `buddy-wait` resource (bounded waits). `BuddyControl` exposes
service operations (`health`, `capabilities`, `runtime_info`, `service_control`,
`dashboard`, `legacy_import`), task operations (`task_submit`, `task_get`, `task_list`,
`task_result`, `task_cancel`, `task_retry`, `task_acknowledge`, `task_wait`), the
`worker_*` and `message_*` groups, `inquiry_observe`, `artifact_list` and `events_read`;
`BuddyWait` exposes `events_wait`, `task_wait`, `message_wait` and `wait_capacity`. Every
request is a validated JSON string carrying the private service token; the schema
validators reject unknown fields before anything reaches the store.

The local `worker-start` and `worker-stop` CLI commands start a supervisor or write its
cooperative stop request directly. They do not use this RPC path.

The transport DTO in the released C-Two 0.5.1 uses the Python pickle protocol for
string arguments, so this is a **same-user Python API only**. It is not claimed to be
cross-language portable, no FastDB DTO is used, and a non-Python client must define its
own contract instead of relying on these payloads.

## Data model

One schema-versioned SQLite database (`board.sqlite3`, schema version 5) owns every
authoritative fact:

| Table | Contents |
| --- | --- |
| `tasks` | durable `task_id` (= `runId`), `request_id` unique across this database, owner attribution, canonical specification and input fingerprint, adapter, cwd, required capabilities, exclusive resources, timeout, state, `queue_reason`, revision and acceptance fields |
| `attempts` | one row per attempt generation: worker identity/instance, nonce verifier, claim request id, lease, execution state, runtime identity, log paths, result, cancel request, exit code/signal, shutdown evidence |
| `workers` | identity, adapter, capabilities, host, PID (diagnostic), state, current attempt, last seen |
| `messages` | bounded question/answer or observation, author/recipient, correlation id, payload hash, delivery/answer evidence, state, unique per `(task_id, inquiry_id)` |
| `artifacts` | immutable reference: kind, location, content hash, size, verification flag, unique per attempt/location/hash |
| `events` | monotonic `seq`, task/attempt/revision, kind and bounded payload; event-producing business operations commit their state changes and events in one transaction |
| `commands` | idempotent command receipts: kind, request hash, response, subject (worker identity + nonce verifier) |
| `resource_claims` | held cwd/exclusive resources per task, with release timestamps |
| `cursors` | optional persisted consumer cursors |

Connections are opened per operation with `foreign_keys=ON`, `journal_mode=WAL`,
`synchronous=FULL`, `busy_timeout=10000` and `trusted_schema=OFF`; writes use
`BEGIN IMMEDIATE`. Transactions are short, and no transaction spans RPC, a subprocess,
an LLM call or an event wait. Startup refuses a database whose schema version differs or
whose integrity/foreign-key checks fail, rather than rewriting records.

Routine lease renewal and liveness timestamp updates do not each emit an event. Atomic
event delivery applies to the business operations that record events.

Task states: `queued`, `running`, `cancelling`, `completed`, `failed`, `cancelled`,
`reconciliation-needed`. Attempt states: `starting`, `executing`, `finalizing`,
`uncertain`, `finished`. Worker states: `starting`, `idle`, `busy`, `stopping`, `lost`.
Message states: `queued`, `claimed`, `delivered`, `answered`, `discarded`,
`unavailable`. Transitions are checked in one table in `store.py`; a stale
revision/generation is rejected with `REVISION_CONFLICT` or `STALE_GENERATION`, and a
partial unique index guarantees at most one effective active attempt per task — an
uncertain attempt keeps its slot so a replacement cannot be created while a survivor
may still run.

## Identity and idempotency

- `requestId` is the idempotency key. The request is normalized to a canonical
  specification (canonical realpath cwd, validated fields, defaults) and fingerprinted
  (version 2). The same `requestId` with an identical fingerprint recovers the existing
  task; changed input is a `CONFLICT`, including after completion or restart.
- `runId` equals `taskId` and selects exactly one task. Read and wait operations never
  launch a replacement.
- An attempt is identified by `(attempt_id, generation, worker, nonce)`. The claim
  capability is `HMAC(secret, attempt_id:generation:nonce)`; only a verifier for the
  nonce is persisted, never the capability in plaintext. A worker persists its nonce
  and `claimRequestId` **before** claiming, so a committed claim whose reply is lost
  replays the identical attempt, generation and capability instead of minting a second
  one.
- Command receipts are bound to the worker identity and nonce; replaying a
  `commandId` with a different request or from another worker is an error, not a state
  change. Worker results are replayed idempotently; a conflicting replay fails.
- Legacy records keep the Node `JSON.stringify` fingerprint (version 1) so identical
  legacy `start` requests resolve to the imported task.

## Admission and resources

A submitted task is admitted as `queued` and starts when a matching worker claims it.
`queueReason` reports why it is waiting: `awaiting-worker`, `capacity`, `cwd-overlap`
(canonical ancestor/descendant overlap) or `exclusive-resource`; after a daemon restart
it can also be `attempt-uncertain-after-restart`. Capacity and resource conflicts queue
work instead of rejecting admission; `BUDDY_MAX_CONCURRENT` (1–8, default 1) bounds
active attempts, and one worker runs one attempt at a time (`WORKER_BUSY`). Reserving a
resource creates a claim row. Claims are released when shutdown is confirmed or the
owning worker proves the attempt never spawned. Cancellation intent alone does not
release an active attempt's resources; uncertainty keeps them reserved.

## Wait separation

Bounded waits use the dedicated `buddy-wait` resource with its own admission
(`BUDDY_WAIT_CAPACITY`, default 32). Waits are check/subscribe/recheck sequences on the
committed event stream, hold no database transaction and no exclusive lock, and never
cold-start a service. When every admitted slot is busy the caller gets a resumable
`WAIT_OVERLOAD` error carrying the cursor and retry delay; no task state changes. This
keeps any number of waiting clients from consuming the capacity that cancel, renew and
result commits need.

## Inquiry

The inquiry bridge lives in the Node dsh plugin because the upstream plugin runs in
Node; the Python service is its client and the importer of its bounded JSONL journal.
The bridge appends transport evidence next to its socket, and the service imports it
idempotently keyed by `inquiryId` while the board message rows stay authoritative. The
journal can never overwrite a recorded answer.

The service has no automatic inquiry scheduler; clients request it as needed. The no-question
form is bounded observation from the durable record plus the bridge's live view; it is
not a percentage or a guaranteed ETA, and unobservable fields are named. A question is
durably recorded before injection, delivered to the run's own live agent, and answered
only through that run's correlated reply tool, so assistant prose is never an answer.
`waitMs` bounds only the call's wait: an inquiry never extends, pauses or cancels the
execution deadline, and an idle or terminal agent cannot be woken.

## Uncertainty and recovery

- Ending a CLI wait (Ctrl-C, closed terminal, `waitSeconds` expiry) cancels only the
  wait. The durable task keeps running while a worker owns it and is recovered by
  `requestId`/`runId`.
- `cancel` is durable: a queued task is cancelled immediately; an active task becomes
  `cancelling` and the owning worker observes the cancel intent through its periodic
  cancellation check (currently every 2 seconds, separate from lease renewal) and stops
  its own process group. A confirmed completion that beat the cancel request is
  the one legal outcome, recorded in the event stream.
- A daemon restart preserves task and attempt identity. Every in-flight attempt becomes
  `uncertain` with its resource claims retained, and busy workers are marked `lost`.
  The legitimate worker keeps its child handle, deadline and receipt, and reattaches
  through `worker_renew`/`worker_reconcile` using the same attempt id, generation and
  nonce. Nothing is reattached by PID.
- Lease expiry marks an attempt `uncertain`, never `stopped`. A surviving process is
  never inferred dead from a missing PID or an expired lease, and uncertainty is never
  converted into a retry: `retry` requires confirmed shutdown (`SHUTDOWN_UNCONFIRMED`)
  or the worker that owns the process handle reporting an observed outcome.
- Workers keep an immutable local completion receipt and replay it until the service
  confirms the result transaction; a durable receipt is never executed twice. Spawn
  intent/marker files make the crash window explicit, and an intent owned by a previous
  process is preserved as orphaned evidence.

## Runtime packaging

Dependency management is uv-only with a frozen lock. On a cold start with no READY
runtime, `launch_target()` materializes a content-addressed runtime under
`BUDDY_RUNTIME_ROOT` (default `~/.local/share/hey-my-buddy/runtime/<contentId>`): it
copies the runtime assets into the final directory, runs `uv sync --frozen --no-dev`
there, rewrites environment paths, and writes `READY.json` last. Credentials, user
data, tests, `node_modules` and any existing virtual environment are never copied. The
daemon and its workers then run from that runtime's own interpreter, so replacing the
Codex plugin cache does not disturb a running service. `BUDDY_DEV_SOURCE=1` suppresses
automatic materialization; source execution requires that no matching or explicitly
pinned READY runtime is selected. Use a private runtime root for source tests. Inspect
the actual runtime identity: `stable` is true only when the process imports `buddy`
from inside the runtime with no source leaks.

## Current limits

- POSIX only (macOS/Linux); Windows is not implemented.
- One daemon owns a state directory (lifetime locks plus the legacy-socket guard); a
  schema or contract mismatch is refused instead of migrated silently.
- SQLite is the deliberate local database. Remote untrusted tenancy, PostgreSQL/HA and
  exactly-once external side effects are not implemented and are not claimed.
- No `steer` and no automatic `resume`: retry is an explicit new attempt, and
  notifications are post-commit hints for clients. `capabilities` reports these limits
  under `steer`, `resume`, `nativeAppWakeup`, `postgres` and `remoteTenancy`.
- Native Codex App wakeup is not provided by the service; an explicit App heartbeat
  that calls the CLI is the background route.
- The ADR asked for a separate documented transition table; the effective tables live
  in `store.py` (`TASK_TRANSITIONS`, `ATTEMPT_TRANSITIONS`) and are summarized here.
