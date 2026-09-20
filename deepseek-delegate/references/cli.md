# Buddy CLI reference

`buddy` is the supported entrypoint: one command per operation, one JSON object argument, one JSON object on stdout. This page is the complete command surface and its defaults, bounds, envelopes and error codes. Usage flows are in [usage.md](usage.md); runtime lifecycle is in [operations.md](operations.md).

## Invocation

```sh
BUDDY='/abs/path/to/deepseek-delegate/scripts/launch-buddy.sh'
"$BUDDY" status '{"runId":"<runId>"}'
```

From the project directory the same CLI is `uv run --frozen --project deepseek-delegate buddy <command> '<json>'`. The launcher resolves the project from its own location, selects Python 3.12 through uv, and forwards arguments verbatim. An unknown command or missing command is an argparse usage error; the JSON argument defaults to `{}`. Service request schemas reject unknown JSON parameters with `INVALID_ARGUMENT`. The local `worker-start`/`worker-stop` commands currently ignore extra fields; use only their documented parameters.

## Envelope and exit status

- Success prints one pretty-printed JSON object and exits 0.
- A failure prints `{"error":{"code":"...","message":"..."}}` and exits 1. The service can attach an `error.details` object (a revision, a resumable cursor, conflicting fingerprints), but the transport wrapper used by the CLI and `BoardClient` rebuilds errors from `code` and `message`. Those details remain in the raw C-Two response. Command-line usage errors are argparse errors and exit 2.
- A transport error or invalid response can leave the submission outcome unknown: the task may have committed before the reply was lost. Query the original `requestId`, or replay the identical start request to recover it. A different request ID can duplicate work. A `WAIT_ABANDONED` envelope ends only the wait.
- Exit 0 only means the call returned. It is **not** task success: read `status`, `outcome`, `resultDelivered` and `shutdownConfirmed`, then inspect the artifacts.
- `run`/`await` may print a `WAIT_ABANDONED` envelope when the wait is interrupted; the durable task keeps running and the envelope names the real recovery commands.

## Commands

### Run and wait

| Command | Parameters | Behavior |
| --- | --- | --- |
| `run` | submit fields except `owner`, plus `waitSeconds` (1–86400) | Starts or recovers one task by `requestId`, then waits inside this call. Default window `min(86400, timeoutSeconds + 60)`; prints the final run envelope |
| `await` | `requestId` or `runId`, optional `waitSeconds` (1–86400, default 86400) | Waits on an existing task and never starts work. Its initial status read attaches to a healthy service or cold-starts one; it never creates a task. A `requestId` with no matching task is `NOT_FOUND`; `runId` and `requestId` from different tasks are a `CONFLICT` |
| `start` | submit fields | Starts or recovers one task and returns the task view immediately; `status: "queued"` with `queueReason` |
| `submit` | submit fields | Same board operation as `start`, for multi-agent callers |

Submit fields: `requestId` (1–128 chars, required), `task` (required, ≤1 MiB), `cwd` (required absolute existing directory), `adapter` (`dsh` default, `command`, `external`), `argv` (only for `command`; 1–256 entries, each ≤32768 bytes), `model`, `provider`, `effort`, `timeoutSeconds` (10–86400, default 1800), `workspace` (default `true`), `owner`, `requiredCapabilities` (≤32), `exclusiveResources` (≤32). Any other field is rejected.

### Tasks

| Command | Parameters | Behavior |
| --- | --- | --- |
| `status` | `runId`, `taskId` or `requestId` | Task view: state, `queueReason`, revision, selected attempt, worker, artifacts, inquiry counts |
| `result` | `runId`, `taskId` or `requestId` | Task view plus the adapter outcome under `result` and commit metadata under `resultMeta` (`status`, `error`, `exitCode`, `signal`, `shutdownConfirmed`, `completedAt`); `NOT_READY` before a persisted result exists |
| `list` | `limit` 1–100 (default 20), `offset`, `state`, `adapter` | Tasks newest first, with `total` and the event `cursor` |
| `cancel` | `runId`, `taskId` or `requestId`; optional `reason`, `requestedBy` | Queued task → `cancelled`; active attempt gets a durable cancel request (`cancelRequestedAt`) and becomes `cancelling`. The response adds `alreadyTerminal`/`duplicate` flags. Unrelated dsh work is untouched |
| `retry` | `runId`, `taskId` or `requestId`; optional `reason`, `requestedBy` | Requeues an eligible failed, cancelled or reconciliation-needed task; its next claim creates a new generation. Clears previous acceptance (archived in events). Completed tasks cannot be retried; `SHUTDOWN_UNCONFIRMED` refuses retry while shutdown is unverified |
| `acknowledge` | selector plus `note` (required, ≤10000 bytes), `verdict` `accepted`/`rejected` (default `accepted`), `evidence` (≤32), `acknowledgedBy` | Records review of a persisted result with confirmed shutdown. Never changes execution status; a different repeated note or verdict is `CONFLICT` |
| `artifacts` | `runId`, `taskId` or `attemptId` | Verified artifact records (kind, location, content hash, size) |

### Events and bounded waits

| Command | Parameters | Behavior |
| --- | --- | --- |
| `events` | `after` cursor, `limit` 1–200 (default 100), `taskId`/`runId` | Committed events with `events`, `cursor`, `head`, `truncated` |
| `watch` | `after` cursor, `timeoutMs` 0–30000 (default 30000), `limit`, `taskId`/`runId` | Bounded event wait on the dedicated wait resource; adds `timedOut` |
| `wait` | `runId`/`taskId`, optional `afterRevision`, `timeoutMs` 0–30000 (default 30000) | Bounded wait for one task to change or finish; returns the task view |
| `wait-capacity` | none | Wait admission counters `capacity`, `admitted`, `rejected`, `available` |

`wait` and `watch` never cold-start a service: with no running daemon they return `SERVICE_UNAVAILABLE`. When every admitted wait slot is busy the service returns a resumable `WAIT_OVERLOAD` whose `details` carry `cursor`, `retryAfterMs` and `capacity`; the message says no task state changed and the caller should retry from the same cursor. (As noted above, the CLI prints the code and message without `details`.) Event-producing business operations commit the state change and event together. Lease renewal and liveness updates do not each emit an event; the event stream is a replayable outbox.

Event kinds include `task.submitted`, `task.cancel_requested`, `task.cancelled`, `task.completed`, `task.failed`, `task.retried`, `task.review_archived`, `task.accepted`, `task.rejected`, `task.imported`, `attempt.claimed`, `attempt.progress`, `attempt.reconciled`, `attempt.released`, `attempt.uncertain`, `attempt.lease_expired`, `worker.registered`, `message.posted` and `message.updated`.

### Messages and inquiry

| Command | Parameters | Behavior |
| --- | --- | --- |
| `inquire` | `runId`/`taskId`; optional `inquiryId`+`question` (together), `timeoutMs` 100–5000 (default 1500), `waitMs` 0–30000 (default 0) | Read-only observation, or one correlated question to the run's own live agent |
| `message` | selector, `inquiryId`, `question` (≤4000 UTF-8 bytes); optional `author`, `recipient`, `correlationId`, `waitMs` | Posts one correlated inquiry; repeating the same id and text returns the recorded message without injecting twice |
| `messages` | optional `runId`/`taskId`, `state`, `limit` 1–200 (default 50) | Lists messages for that task, or all tasks when no task is selected; `requestId` is not accepted |
| `message-get` | `messageId`, or a task selector plus `inquiryId` | Reads one inquiry and its recorded answer without resubmitting the question |
| `message-update` | `messageId`, or `runId`/`taskId` plus `inquiryId`; optional `state`, `reason`, `delivery`, `answer`, `actor` | Records caller-provided delivery/answer evidence; `requestId` is not accepted |

Inquiry rules:

- `inquiryId` matches `[A-Za-z0-9._:-]{1,128}`. Repeating an id with different text is a `CONFLICT` for active and terminal tasks. Re-reading through `inquire` requires both the same text and the same id; `message-get` reads it by identity alone.
- Message states are `queued` (recorded, waiting for a delivery boundary), `claimed` (consumed for a proposed step, not delivery), `delivered` (the run's durable commit), `answered` (a correlated reply was recorded), `discarded` (dropped before a boundary) and `unavailable` (no bridge, a terminal task, or a question that can no longer be claimed or answered).
- The dsh inquiry bridge requires the run's correlated reply tool and does not parse assistant prose as an answer. The public `message-update` operation can separately record caller-provided answer evidence; check its attribution. Questions and answers are each bounded at 4000 UTF-8 bytes and at most 32 inquiries are retained per task (`TOO_MANY_INQUIRIES` beyond that).
- `waitMs`/`timeoutMs` only bound this call's wait. An inquiry never extends, pauses or cancels the execution deadline, and it never wakes a terminal agent.
- A bounded question can return in state `delivered` before any answer exists; check `inquiry.answer.available` (and `message.state`) before reporting an answer. An `answered` journal record without usable text is downgraded to `delivered` with a reason rather than reported as answered.
- The no-question form reports durable state plus a bounded `live` observation (`agentStatus`, inbox depth, `lastEvent` and up to 20 activity entries); fields it cannot observe are named in `live.unavailable` instead of being reported as zero. `deadline` is explicitly an estimate: `estimated: true`, `kind: "estimated-runner-deadline-from-record-createdAt"`, `deadlineBasis: "createdAt + timeoutSeconds"`, plus `clockOrigin`, `startedAt`, `deadlineAt`, `remainingSeconds` and `exactTimingAvailable: false`.
- `live.agentStatus: "running"` means an agent driver is active, not that it is making progress; raw model reasoning is never exposed. Bridge transport failures report `bridge-unreachable`, `bridge-timeout`, `bridge-refused`, `bridge-invalid-response` or `bridge-mismatched-response`, and a recorded answer carries its provenance (`live-bridge` or `bridge-journal`).

### Workers

| Command | Parameters | Behavior |
| --- | --- | --- |
| `workers` | optional `state`, `adapter`, `limit` 1–200 (default 50) | Registered workers with state, adapter, capabilities and current attempt |
| `worker-register` | `workerId`, optional `identity`, `adapter`, `capabilities`, `host`, `pid`, `commandId` | Registers or refreshes one worker; idempotent by `commandId` |
| `worker-claim` | `workerId`, `claimRequestId`, `nonce`; optional `taskId`/`runId`, `pid`, `identity`, `capabilities`, `adapter`, `workerInstance` | Atomically claims one queued task; returns the attempt, lease and unguessable capability. A replay with the same `claimRequestId` and nonce returns the identical attempt and generation |
| `worker-reconcile` | `workerId`, `attemptId`, `generation`, `nonce`; optional `claimRequestId`, `pid`, `runtimeIdentity`, `workerInstance` | Reattaches the legitimate worker after daemon downtime; a replaced generation is `STALE_GENERATION` |
| `worker-renew` | `workerId`, `attemptId`, `generation`, `nonce`; optional `phase`, `pid` | Extends the lease and reports `cancelRequested`; `phase` may move `executing`/`finalizing` |
| `worker-progress` | actor fields plus optional `message`, `phase`, `data` | Appends one bounded progress event |
| `worker-result` | actor fields plus `status` (`ok`/`failed`/`cancelled`); optional `result` (object/null), `shutdownConfirmed` (default false), `error`, `exitCode`, `signal`, `artifacts`, `logPaths`, `runtimeIdentity`, `elapsedSeconds`, `commandId` | Commits result, artifacts, state and completion event in one transaction |
| `worker-release` | actor fields; optional `reason`, `workerInstance`, `evidence: {"spawnIntentWritten": false}` | Releases an attempt that never started; explicit evidence must come from the claiming instance |
| `worker-start` / `worker-stop` | `workerId` (default `local`), optional `stateDir` | Starts one detached supervisor in its own session, or writes a cooperative durable stop request (never a signal) |

Actor fields are `workerId`, `attemptId`, `generation` and `nonce`. Normally these operations are used through `BoardClient`; see [workers.md](workers.md).

### Service, runtime and legacy

| Command | Parameters | Behavior |
| --- | --- | --- |
| `health` | none | Protocol/contract/schema versions, service id, state dir, runtime identity and stability, `maxConcurrent`, `waitCapacity`, `persistenceError`, integrity and active work |
| `capabilities` / `adapters` | `includeUnavailable` | Adapter report (`executedBy`, availability), local capabilities, named operations, wait admission, honest limitations |
| `runtime` | optional `destination` | Runtime identity, installed-runtime description and source-leak report |
| `dashboard` | `action` `open`/`close`/`status` (default `open`) | Private read-only loopback panel |
| `restart` | optional `reason`, `drainSeconds` 0–120 (default 10) | Detaches the daemon without cancelling owned work; independent workers survive |
| `stop` | optional `reason`, `drainSeconds` 0–120 (default 10) | Cancels queued tasks, writes durable cancel intent for active attempts, drains, returns `unresolvedAttempts` |
| `legacy-import` | `sourceDir` plus optional `dryRun` (default `true`), `strict` (default `true`), `requestIds`, `snapshot` (default `false`) | Offline transactional import of removed Node records; see [operations.md](operations.md#legacy-import) |

`stop` and `restart` with no running daemon return an `alreadyStopped` envelope and start nothing.

## Defaults and bounds

| Setting | Default | Bounds |
| --- | --- | --- |
| Task execution deadline `timeoutSeconds` | 1800 s | 10–86400 s |
| CLI `run` wait window | `min(86400, timeoutSeconds + 60)` | explicit `waitSeconds` 1–86400 wins |
| CLI `await` wait window | 86400 s | 1–86400 s |
| `wait` / `watch` timeout | 30000 ms | 0–30000 ms |
| `message` / `inquire` answer wait (`waitMs`) | 0 ms | 0–30000 ms |
| `inquire` bridge request `timeoutMs` | 1500 ms | 100–5000 ms |
| Event page `limit` | 100 | 1–200 |
| `list` page `limit` | 20 | 1–100 |
| `messages` / `workers` page `limit` | 50 | 1–200 |
| Task text | — | 1 MiB |
| Question / answer | — | 4000 UTF-8 bytes each |
| Inquiries retained per task | — | 32 |
| Acknowledgment note / evidence | — | 10000 bytes / 32 entries |
| Artifacts per result / location | — | 100 / absolute path ≤4096 characters |
| Attempt lease `BUDDY_LEASE_SECONDS` | 120 s | 15–3600 s |
| Active attempts `BUDDY_MAX_CONCURRENT` | 1 | 1–8 |
| Wait capacity `BUDDY_WAIT_CAPACITY` | 32 | server-side admission |

## Error codes

| Code | Meaning |
| --- | --- |
| `INVALID_ARGUMENT` | A parameter is missing, unknown or out of bounds |
| `NOT_FOUND` | The selector matches no task, worker or message |
| `NOT_READY` | No persisted result/attempt exists yet (for example `result` before completion) |
| `CONFLICT` | The same identity with changed input: changed submit input, changed inquiry text, or a different repeated acknowledgement |
| `REVISION_CONFLICT` | A stale revision tried to mutate state |
| `STALE_GENERATION` | A replaced attempt generation tried to reconcile or renew |
| `SHUTDOWN_UNCONFIRMED` | Retry refused because the previous stop is not verified |
| `ILLEGAL_TRANSITION` | The state machine refuses that task/attempt transition |
| `WORKER_BUSY` / `WORKER_STOPPING` | The worker already runs an attempt / is draining and accepts no claim |
| `NOT_REGISTERED` / `ALREADY_RUNNING` | The worker id is unknown / already supervised |
| `ATTEMPT_FINISHED` | Progress or a mutation arrived after the attempt finished |
| `ADAPTER_UNAVAILABLE` / `UNSUPPORTED_ADAPTER` | The adapter cannot run here / is not `dsh`, `command` or `external` |
| `ARTIFACT_MISSING` / `ARTIFACT_MISMATCH` | A claimed artifact failed validation and no completed result was published |
| `TOO_MANY_INQUIRIES` | The 32-inquiry retention limit for the task was reached |
| `WAIT_OVERLOAD` | Every admitted wait slot is busy; the service's `details.cursor` says where to resume |
| `WAIT_ABANDONED` | The wait was interrupted; the task was not cancelled |
| `SERVICE_UNAVAILABLE` | The service could not be reached, or no service runs for a `wait`/`watch` |
| `SERVICE_START_FAILED` / `SERVICE_START_TIMEOUT` | The daemon failed to start or become ready |
| `RESULT_NOT_AVAILABLE` / `RESULT_MISSING` / `RESULT_MISMATCH` / `RECOVERY_MISMATCH` | `run`/`await` could not deliver the promised result; the execution status is still reported honestly |
| `RUNTIME_INSTALL_FAILED` / `RUNTIME_INSTALL_TIMEOUT` / `UV_NOT_FOUND` / `RUNTIME_NOT_READY` | Stable-runtime installation or pinning failed; see [operations.md](operations.md#runtime-lifecycle-and-upgrade) |
| `LEGACY_OWNER_RUNNING`, `LEGACY_ACTIVE_RUNS`, `LEGACY_CONFLICT`, `LEGACY_SOURCE_IS_LIVE_TARGET`, `LEGACY_SERVICE_RUNNING`, `STALE_LEGACY_SOCKET`, `MIGRATED` | Legacy import or legacy-owner guard refused the operation; see [operations.md](operations.md#legacy-import) |
| `UNAUTHORIZED` | The private service token was rejected, or a receipt belongs to another worker |
| `INTERNAL_ERROR` | Unexpected service failure; set `BUDDY_DEBUG` for detail |

## `run`/`await` envelope

`run` and `await` print the same envelope shape: `runId`, `requestId`, `status`, `outcome`, `ok`, `resultAvailable`, `resultDelivered`, `shutdownConfirmed`, `acceptedAt`, `revision`, `createdAt`, `updatedAt`, `cwd`, `logPaths`, `waitedSeconds`, `waitSeconds`, `maxWaitSeconds`, `runnerDeadlineSeconds`, `waitCoversRunnerDeadline`, `timedOut`, `reconnects`, `result`, `recovery`, `limitation`, `error` and a `note`.

`outcome` is the terminal task status (`completed`, `failed`, `cancelled`, `reconciliation-needed`), `wait-timeout` when this call's window ended first, or `unavailable` when the service could not be reached while waiting. A task that reaches its own execution deadline ends as a terminal `failed` with the adapter `status: "timeout"`. The `recovery` block names real commands with the existing run ID (`buddy status`, `buddy await`, `buddy result`, `buddy cancel`). `ok` is true only when the outcome is `completed` **and** this envelope carries the result payload **and** no error was recorded; a completed run whose result could not be delivered is reported as `completed-no-result` with `ok: false`, never as success.
