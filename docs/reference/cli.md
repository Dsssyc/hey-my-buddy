# Buddy CLI reference

`buddy` is the supported entrypoint: one command per operation, one JSON object argument, one JSON object on stdout. This page is the complete command surface and its defaults, bounds, envelopes and error codes. Usage flows are in [usage.md](usage.md); the governed goal lifecycle is in [workflow.md](workflow.md); runtime lifecycle is in [operations.md](operations.md).

## Invocation

```sh
BUDDY='/absolute/plugin-root/bin/buddy'
"$BUDDY" status '{"runId":"<runId>"}'
```

`bin/buddy` is the single bundled launcher. It resolves the uv project from its own location rather than the caller's working directory, selects Python 3.12 through uv, and forwards every argument verbatim. From the repository root the same CLI is `uv run --frozen buddy <command> '<json>'` or `uv run --frozen python -m buddy.cli <command> '<json>'`. An unknown or missing command is an argparse usage error; the JSON argument defaults to `{}`. Service request schemas reject unknown JSON parameters with `INVALID_ARGUMENT`; the local `worker-start`/`worker-stop` commands validate their own small parameter set.

Every CLI name maps to one named C-Two operation. The governed lifecycle keeps the short public names (`submit`, `get`, `decide`, `continue`, `takeover`, `cancel`, `acknowledge`, `suggest`) for the `workflow_*` operations; `execution-submit`, `execution-cancel`, `execution-retry` and `execution-acknowledge` reach the ordinary `task_*` records used by the `command`, `external` and internal `decision` infrastructure. The workspace lifecycle adds `scope-amend` → `workflow_scope_amend`, `workspace-resolve` → `workflow_workspace_resolve`, `integration-record` → `workflow_integration_record`, and `workspace-cleanup-plan`/`workspace-cleanup-apply` → the `workspace_cleanup_*` operations; [workspace-lifecycle.md](workspace-lifecycle.md) owns their fields and eligibility rules. Do not conflate the two spellings: the short name is CLI syntax, the `workflow_*` or `task_*` name is the stable operation identity.

The CLI injects Host authority locally. For any mutation that requires it, `controlFile` names a private `0600` file bound to one run and owner generation; alternatively the complete `hostId` + `ownerGeneration` + `controlToken` triple may be passed explicitly. There is deliberately no implicit "latest generation" lookup, and an attempt-scoped agent credential may not use a `controlFile`. Newly returned control capabilities are saved privately and only the file path is printed.

## Envelope and exit status

- Success prints one compact JSON object on one line and exits 0. The object is the brief output view described below unless the caller passed `"output":"full"`.
- A failure prints `{"error":{"code":"...","message":"..."}}` and exits 1. The service can attach an `error.details` object (a revision, a resumable cursor, conflicting fingerprints, `legalEfforts`, `routingTaskId`), but the transport wrapper used by the CLI and `BoardClient` rebuilds errors from `code` and `message`; those details remain in the raw C-Two response. Command-line usage errors are argparse errors and exit 2.
- A transport error or invalid response can leave the submission outcome unknown: the task may have committed before the reply was lost. Query the original `requestId`, or replay the identical submission to recover it. A different request ID can duplicate work. A `WAIT_ABANDONED` envelope ends only the wait.
- Exit 0 only means the call returned. It is **not** task success: read `status`, `outcome`, `resultDelivered` and `shutdownConfirmed`, then inspect the artifacts.
- `await` may print a `WAIT_ABANDONED` envelope when the wait is interrupted; the durable task keeps running and the envelope names the real recovery commands.

## Output views

Every printed response stays in the calling Host model's context and is re-read on each later request, so the CLI prints a brief projection of the service response by default. `output` is a CLI-local field: `"output":"brief"` (the default) or `"output":"full"`. It is removed before the RPC, so the service, `BoardClient` and the console always receive and return the complete views, and `"output":"full"` prints the unmodified response. `console` keeps its complete response. Commands without a projection print their complete response in compact JSON. Error envelopes are never projected.

The projection only omits material the caller already supplied (the goal packet and `executionWorkspace` intent), duplicates (`taskId` beside `runId`, the execution summary under `task`, a cleanup plan echoed as both `plan` and `cleanup`, `tasks` beside `runs`, turn/artifact copies inside an `await` result) and history not needed for the next decision (older turns and requests, input artifacts, fingerprints and timestamps). It keeps every identifier a following command needs, the complete `activeRequest`, workspace conflicts, errors, nonzero truncation with its counts, and the complete `shutdown` report whenever shutdown is not fully confirmed. A value the service did not report is never replaced by a default. Projected objects carry `"view":"brief"` or, for a mutation, `"view":"receipt"`.

| Command | Brief view |
| --- | --- |
| `get` | `runId`, `requestId`, `state`, `status`, `queueReason`, `awaitingHost`, `waitReason`, `revision`, `ownerGeneration`, `continuationCount`, `executionConfiguration`, `routing` (`status`, `source`, `reason`, `selectedProfile`, `preferenceOutcome`, `decisionId`), `workspace` (`path`, `kind`, `access`, `inputCommit`), `scope` (`scopeVersion`, `writeScope`), `currentTurn` (identity, state, disposition, `summary`, `remaining`), `activeRequest`, `pendingRequests`/`openRequests` when more than one is open, compact `children`, `artifacts` limited to output artifacts (`artifactId`, `kind`, `turnId`, `manifestSha256`, `outputCommit`, `diffPath`, `diffSha256`, `changedPaths`) with an `otherArtifacts` count for the omitted inputs and manifests, `workspaceConflicts`, compact `integrations` and `cleanup`, `finalArtifactId`, `acceptedAt`/`acceptanceVerdict`, `shutdown`, `counts` and nonzero `truncated` entries. `includeAudit` and `routingHistory` are returned as requested |
| `submit` and governed mutations (`decide`, `continue`, `takeover`, `cancel`, `acknowledge`, `scope-amend`, `workspace-resolve`, `integration-record`, `workspace-cleanup-plan`, `workspace-cleanup-apply`, `suggest`) | The `get` brief view without the turn summary, plus the operation's own result: `duplicate`, `controlFile`/`control` (never a token), `verdict`, `integrationId` and compact `integration`, compact `plan` (`planId`, `state`, `eligible`, `reasons`, `path`, `expiresAt`, retained-item counts, `result`), `removed`, and a different `targetRunId`/`targetRevision`. An unrecognized operation field is kept |
| `await` | The envelope without `note`, `maxWaitSeconds`, `createdAt`, `cwd` and null fields; `logPaths` only when `ok` is false; a governed result without its runner `finalText` when the turn summary is present |
| `status`, `execution-*` | Task identity (`runId`, `taskId`, `requestId`, `createdAt`) and state, attempt identity/state and `workerId`, `resultAvailable`, `shutdownConfirmed`, acceptance fields, `configuration`, the compact `workflow` summary, `workflowShutdown`, `activity`, `inquiries` and a `title`; no `spec`, task text or runner payload |
| `result` | The `status` view plus `attemptId`, `resultDelivered`, the verified execution `artifacts`, `resultMeta` without its duplicate artifact list, and the governed result view (or the opaque infrastructure `result`) |
| `list` | `runs` rows with `runId`, `requestId`, `adapter`, `model`, `status`, `workflowState`, `awaitingHost`, `kind`, `parentRunId`, `currentHostId`, `createdAt` and a `title`; `total`, `cursor`, `nextCursor` |

## Service

| Command | Parameters | Behavior |
| --- | --- | --- |
| `ping` | none | Authenticated process liveness: status, protocol/contract/schema versions, service id, PID and the stored persistence error; no integrity or runtime inspection. Internal client attachment uses this operation |
| `health` | none | Protocol/contract/schema versions, service id, state dir, runtime identity and stability, model `capacity` (`totalLimit`, `totalActive`, `models` entries `{adapter, provider, model, limit, active}`), `managedWorkerIds`, `unstartedWorkerIds`, `stoppedWorkerIds`, `surplusWorkerIds`, `surplusDraining`, `surplusRetained`, `waitCapacity`, integrity and active work |
| `capabilities` / `adapters` | `includeUnavailable` | Adapter report (`adapter`, `available`, `reason`, `capabilities`, `executedBy`), `localCapabilities`, named operations, wait admission and the honest `limitations` map |
| `runtime` | optional `destination` | Runtime identity, installed-runtime description and source-leak report |
| `restart` | optional `reason`, `drainSeconds` 0–120 (default 10) | Detaches the daemon without cancelling owned work; independent workers survive. With no running daemon returns `alreadyStopped` and starts nothing |
| `stop` | optional `reason`, `drainSeconds` 0–120 (default 10) | Cancels queued tasks, writes durable cancel intent for active attempts, drains and returns `unresolvedAttempts`. With no running daemon returns `alreadyStopped` |

## Governed goal lifecycle

The [workflow guide](workflow.md) gives complete packets and examples. These operations use the existing task/attempt queue; `runId` remains the logical goal across turns. A normal structured yield makes `await` return `outcome: "waiting-host"`, while an approved helper or authorized continuation path can continue within the same wait. The five workspace-lifecycle controls are owned by [workspace-lifecycle.md](workspace-lifecycle.md), which includes the complete integration-record → acknowledge → cleanup-plan/apply example and the eligibility rules behind each operation.

| Command | Parameters | Behavior |
| --- | --- | --- |
| `submit` | ordinary flat submit fields, `hostId`, `executionWorkspace`, optional nested `spec` and `submissionToken` | Prepare a fixed workspace, admit a governed task and route its execution configuration; return the compact view plus owner-private `control`/`controlFile` |
| `get` | `runId`, optional `includeAudit`, `routingHistory` | Compact owner, revision, routing, turn, request, helper, artifact and shutdown view; no control tokens. `includeAudit:true` adds full turns, requests, continuations, reservations, suggestions and the immutable original spec; `routingHistory` requests one bounded page of this run's route records |
| `decide` | `runId`, active `requestId`, `commandId`, `expectedRevision`, control, `decision: approve\|decline`, `reason`, explicit `helpers` (≤8), `autoContinue` (default true) | Record Host authority; create approved helpers or decline without failing the goal |
| `continue` | `runId`, `targetRunId`, `commandId`, `expectedRevision`, required `input` (string or object, ≤64 KiB), optional `reason`, `helperPolicy: keep\|cancel` while helpers are live, `configuration`, `reroute` | Record new input and invalidate all older unconsumed continuations, including manual inputs and automatic triggers; adopt a complete configuration or reroute the same goal when ownership permits |
| `takeover` | `runId`, `commandId`, `expectedOwnerGeneration`, `newHostId`, optional `expectedRevision`, control | Rotate control capability/generation; do not restart work |
| `cancel` | `runId`, optional `commandId`, optional `reason` (default "Host cancelled"), control | Fence the goal and complete owned descendant graph; record cancellation separately from aggregate stop evidence |
| `acknowledge` | `runId`, optional `commandId`, optional `artifactId`, optional `integrationId`, required `note` (≤10000 bytes), `verdict: accepted\|rejected` (default accepted), optional `evidence` (≤32), control | Record review of the fixed final artifact separately from execution. An accepted verdict requires a verified or `notRequired` integration record for that artifact (`INTEGRATION_REQUIRED`) |
| `scope-amend` | `runId`, `commandId`, `expectedRevision`, `expectedScopeVersion`, `writeScope` (≤256), `reason` (≤4000 bytes), control | Append one authorized scope version for the next stage after every related attempt and descendant is confirmed stopped; never re-authorizes an active attempt or rewrites earlier turns |
| `workspace-resolve` | `runId`, `commandId`, `expectedRevision`, `conflictId`, `action: restore\|adopt\|abandon`, `observedFingerprint` (sha256), optional `paths` (restore), `reason` for adopt/abandon, control | Settle one recorded out-of-scope failure site by compare-and-swap restore, explicit adopt or abandon; mechanical resolution needs no further model turn |
| `integration-record` | `runId`, `commandId`, `expectedRevision`, `artifactId`; either `notRequired: true` with `reason`, or `strategy: patch\|cherry-pick\|merge`, `target {path,ref}`, `beforeCommit`; optional `verification`, `adjustedPaths`, control | Bind the sealed artifact to a target checkout/commit relationship resolved from the repository itself, or record an explicit not-required decision; returns `integrationId` |
| `workspace-cleanup-plan` | `runId`, `commandId`, `expectedRevision`, control | Plan removal of this run's own registered managed checkout with eligibility evidence, retention list and a 900-second expiry |
| `workspace-cleanup-apply` | `runId`, `planId`, `commandId`, `expectedRevision`, exact `confirmPath`, control | Recheck ownership, acceptance, integration, shutdown, dependencies and Git state, then remove exactly the planned checkout while keeping outputs, manifests, refs and receipts |
| `suggest` | `runId`, required `body` (≤4000 bytes) | Record one bounded suggestion; an agent-scoped caller may suggest only on its own task |
| `await` | `requestId` or `runId`; optional `waitSeconds` (1–86400, default 86400) | CLI-level read-only wait on an existing task; never starts, resumes, retries or cancels work |

Submit fields are `requestId` (required, ≤128), `hostId` (required, ≤256), `task` (required, ≤1 MiB), `cwd` (required absolute existing directory), optional `adapter` (`dsh`, `zcode`, `codex`, `claude`, `command`, `external`; omission leaves the harness choice to routing), `argv` (only for `command`; 1–256 entries, each ≤32768 bytes), `model`, `provider`, `effort`, optional task-local `routingPreferences` (at most 8 ordered `{match,reason}` entries, each match naming at least one of `adapter`/`provider`/`model`/`effort`), `timeoutSeconds` (explicit `0` removes the execution deadline; otherwise 10–86400, default 1800), `workspace` (default `true`), `owner`, `requiredCapabilities` (≤32), `exclusiveResources` (≤32), required `executionWorkspace` and optional `submissionToken` (16–256 chars). Spec fields may be given flat or inside one nested `spec` object, and a value given in both places must be identical. Contract 0.14.0 adds optional display metadata outside the spec: a root-level `title` (1–200 characters, whitespace normalized) and either `objective: {title}` to create a work objective or `objectiveId` (`obj-…`) to join an existing one of the same source project and submitting Host; they enter the request fingerprint but never the execution spec, Worker input or selector packet ([objectives](objectives.md)). Any other field is rejected.

`executionWorkspace` is a required object separate from the `workspace` boolean, and its `kind` must explicitly be `existing` or `worktree`; omitting the object or its kind is `INVALID_ARGUMENT`. It also has absolute source `cwd` (defaults to the ordinary spec's `cwd`), `access: read|write` (default write), `base: {kind: commit|working-tree, ref?}` (default working-tree), `includeUntracked` (≤256, requires a working-tree base), `writeScope` (≤256; write access defaults to `["."]`), `integrator` (default the Host) and optional attribution-only `targetRef`. Include/write paths are checkout-root-relative; write access needs a nonempty scope. Workspace preparation is idempotent by request ID and records exact input commits; no automatic branch merge or force-clean occurs. Git-ignored runtime data is excluded unless explicitly selected.

`workspace` only means DSH session grouping: `true` (the default) groups the run through the workspace bridge and fails honestly when the bridge is missing, while `false` passes `--no-workspace` and runs ungrouped. It never means Git isolation. The [operations guide](operations.md#workspace-bridge) owns bridge installation and recovery.

A complete explicit `adapter`/`provider`/`model`/`effort` quadruple is validated against the installed native catalog and dispatched directly; a partial quadruple is a hard filter resolved through the fixed decision profile; an unspecified quadruple auto-routes through the bounded evaluation table. No selector or no legal candidate produces a durable routing attention boundary that `decide` refuses and `continue` resolves with `configuration` or `reroute:true`. See [workflow.md#configuration-and-routing](workflow.md#configuration-and-routing).

Continuation reuses the currently allocated actual checkout, prepares a new fixed input there and preserves the original submission specification. The returned `workspace` describes the current effective allocation; `executionWorkspace` retains the original intent. Helper outcomes identify the selected attempt/generation and include only that attempt's sealed output reference, with explicit artifact attempt/turn IDs. Use the output commit and snapshot/diff hashes for integration rather than a moving branch or working directory.

`get` distinguishes the following request and shutdown fields:

| Field | Meaning |
| --- | --- |
| `activeRequest` | Full current open boundary accepted by `decide`, or `null`; includes `summary`, `attempted`, `neededWork`, `expectedArtifacts`, `acceptance`, `suggestedProfileId`, `proxy`, `origin`, `routing` and any `preparationError`; use the top-level current `revision` for the decision |
| `pendingRequests` | Bounded open-request queue including the current active request; at most 5 compact entries |
| `counts.openRequests` | Complete open-request count, including the active request and entries omitted from the compact queue |
| `truncated.pendingRequests` | Number of open-request entries omitted from `pendingRequests` |
| `routing` | `status`, `decisionId`, `taskId`, `attemptId`, `generation`, `selectedProfile`, `tableRevision`, `configurationRevision`, `reason`, `constraints`, `routingPreferences`, `source`, `preferenceOutcome` |
| `proxy`, `origin` on a request | Immediate source `{runId, requestId}` and original requesting leaf `{runId, requestId}` for nested attention |
| `shutdown` | `selfConfirmed`, `descendantsConfirmed`, full `unconfirmedCount`, up to 32 `unconfirmedRunIds`, and boolean `truncated` |

Do not add `activeRequest` to `counts.openRequests`: it is already included. Compact responses limit recent request summaries to 5, turns to 10, children to 32 and artifacts to 32; `counts` and `truncated` describe the complete collections and omitted entries. After deciding the active request, use the returned or freshly read boundary and revision for the next decision. Another request can become active immediately, and sibling completion does not discard outstanding requests.

For nested attention, submit `decide` against the root run and its active proxy request using that root's control. The service validates the owned chain to `origin` and resumes the requesting leaf; the root token cannot authorize a direct mutation of a different run ID. Intermediate runs wait for their dependencies. `continue` may pass `targetRunId` to deliver input or a configuration to an owned descendant without adopting another owner's capability; an unrelated run is `UNAUTHORIZED`.

At most eight helpers are authorized by one decision, and their specs use the same fields as `submit` plus `role` (`helper` or `integrator`). At most one helper per approval may be the named integrator. A helper inherits the parent's task-local `routingPreferences` only with an explicit `inheritRoutingPreferences: true`, and may not set both. Continuation input is bounded to 64 KiB. The turn input is bounded to 256 KiB and the structured outcome to 64 KiB, with at most 32 entries per outcome array. DSH continuations report `reconstructed-new-session`. ZCode reports `native-session` for a proven previous session bound to the same goal, checkout and configuration; without a proven previous session or with a changed configuration, it reports `reconstructed-new-session`. Codex reports `native-session` only for a proven binding whose last stored native turn still completes, and otherwise reconstructs explicitly. A missing or mismatched binding on a native-session request is rejected. Use the exact same command ID and payload to resolve an uncertain response. Re-read and reconsider after a revision/owner-generation conflict.

## Work objectives

| Command | Parameters | Behavior |
| --- | --- | --- |
| `objective-list` | optional `limit` 1–100 (default 50), `before`, `projectId`, `hostId`, `query` (≤200), `filter` (`all`, `active`, `host`, `review`) | Work-objective and standalone-delegation summaries ordered by latest recorded activity, with `total`, `nextCursor`, the event `cursor` and `changed` |
| `objective-timeline` | `objectiveId` (`obj-…` or `run:<rootRunId>`), optional `limit` 1–200 (default 100), `query`, `filter` | One group's rows, spans (≤800) and Host markers (≤800) with totals, per-collection truncation flags, `filtered` and `scopeComplete` |

Both are Host/user reads with no model call, reader lease or publication, refused to attempt-scoped credentials; the [objectives reference](objectives.md) owns their shapes. Their responses are bounded and printed without a brief projection.

## Execution records

Advanced execution records back the `command` and `external` adapters and the internal decision infrastructure. Coding work must use `submit`: `execution-submit` refuses `dsh`, `zcode` or an omitted adapter with `GOVERNED_REQUIRED`. A governed run's execution record requires its Host control for authorization and then still refuses with `GOVERNED_REQUIRED`, pointing at `cancel`, `continue` or `acknowledge`.

| Command | Parameters | Behavior |
| --- | --- | --- |
| `execution-submit` | `requestId`, `task`, absolute `cwd`, `adapter: command\|external`, `argv` for `command`, optional `timeoutSeconds`, `workspace`, `owner`, `requiredCapabilities`, `exclusiveResources`, `model`/`provider`/`effort` | Admit one ordinary execution record and return its task view immediately |
| `execution-cancel` | `runId`/`taskId`/`requestId`; optional `reason`, `requestedBy` | Queued task → `cancelled`; active attempt gets a durable cancel request (`cancelRequestedAt`) and becomes `cancelling`. Unrelated dsh work is untouched |
| `execution-retry` | selector; optional `reason`, `requestedBy` | Requeue an eligible failed, cancelled or reconciliation-needed task; its next claim creates a new generation. Clears previous acceptance (archived in events). Completed tasks cannot be retried; `SHUTDOWN_UNCONFIRMED` refuses retry while shutdown is unverified; internal decision runs are not retried |
| `execution-acknowledge` | selector plus `note` (required, ≤10000 bytes), `verdict` (default accepted), optional `evidence` (≤32), `acknowledgedBy` | Records review of a persisted result with confirmed shutdown. Never changes execution status; a different repeated note or verdict is `CONFLICT` |

## Execution observation

Observation stays separate from the governed boundary. `status`, `result`, `list`, `artifacts` and `events` read execution records; `wait` and `watch` use the dedicated bounded wait resource; `inquire` is bounded read-only observation or one correlated question to a live DSH or ZCode root.

| Command | Parameters | Behavior |
| --- | --- | --- |
| `status` | `runId`, `taskId` or `requestId` | Task view: state, `queueReason`, revision, selected attempt, worker, artifacts, inquiry counts; a governed run also exposes `workflowState`, `awaitingHost`, `workflowShutdown`, `ownerGeneration`, `continuationCount` and the active request kind/summary |
| `result` | selector | Task view plus the adapter outcome under `result` and commit metadata under `resultMeta` (`status`, `error`, `exitCode`, `signal`, `shutdownConfirmed`, `completedAt`); `NOT_READY` before a persisted result exists |
| `list` | `limit` 1–100 (default 20), `offset`, `state`, `adapter`; optional `before`, `rootsOnly`, `query`, `projectId`, `hostId`, `filter` | Tasks newest first, with filtered `total`, the event `cursor`, and nullable history `nextCursor` |
| `artifacts` | `runId`, `taskId` or `attemptId` | Verified artifact records (kind, location, content hash, size) |
| `events` | `after` cursor, `limit` 1–200 (default 100), `taskId`/`runId` | Committed events with `events`, `cursor`, `head`, `truncated` |
| `watch` | `after` cursor, `timeoutMs` 0–30000 (default 30000), `limit`, `taskId`/`runId` | Bounded event wait on the dedicated wait resource; adds `timedOut` |
| `wait` | `runId`/`taskId`, optional `afterRevision`, `timeoutMs` 0–30000 (default 30000) | Bounded wait for one task to change or finish; returns the task view |
| `wait-capacity` | none | Wait admission counters `capacity`, `admitted`, `rejected`, `available` |

`wait` and `watch` never cold-start a service: with no running daemon they return `SERVICE_UNAVAILABLE`. When every admitted wait slot is busy the service returns a resumable `WAIT_OVERLOAD` whose `details` carry `cursor`, `retryAfterMs` and `capacity`; the message says no task state changed and the caller should retry from the same cursor. (The CLI prints the code and message without `details`.) Event-producing business operations commit the state change and event together. Lease renewal and liveness updates do not each emit an event; the event stream is a replayable outbox.

Event kinds include `task.submitted`, `task.cancel_requested`, `task.cancelled`, `task.completed`, `task.failed`, `task.retried`, `task.review_archived`, `task.accepted`, `task.rejected`, `attempt.claimed`, `attempt.progress`, `attempt.activity`, `attempt.reconciled`, `attempt.released`, `attempt.uncertain`, `attempt.lease_expired`, `worker.registered`, `message.posted`, `message.updated`, `workflow.routing_requested`, `workflow.routing_resolved`, `workflow.routing_needs_host`, `workflow.configuration_validated`, `workflow.boundary_updated`, `workflow.helper_admitted`, `workflow.helper_settled`, `workflow.helper_resumed`, `workflow.auto_continued`, `workflow.continued`, `workflow.request_approved`, `workflow.takeover`, `workflow.cancelled`, `workflow.late_turn`, `workflow.workspace_prepared`, `workflow.workspace_preparation_failed`, `workflow.scope_amended`, `workflow.workspace_resolved`, `workflow.integration_recorded`, `workflow.cleanup_planned`, `workflow.cleanup_applied`, `decision.requested`, `decision.started`, `decision.completed`, `decision.needs_host`, `decision.failed`, `decision.cancelled`, `decision.stale` and `evaluation.*` gate/publication events.

A workspace preparation failure opens an attention request and invalidates that continuation while preserving the original goal and recovery files; `activeRequest.preparationError` carries its code (up to 100 characters) and message (up to 2000). Transient `WORKSPACE_BUSY` lock contention instead leaves the continuation queued and emits no failure boundary. Until a continuation has a prepared manifest, `worker-claim` skips it with `awaiting-workspace-preparation`; other claimable tasks remain eligible.

For history paging, pass the previous page's opaque `nextCursor` as `before` (at most 512 characters); it is distinct from the integer event `cursor`. Rows are ordered by descending creation time and run ID, and the strict keyset remains stable when newer rows arrive. A nonzero `offset` cannot be combined with `before`. `rootsOnly:true` restricts the list to main governed goals; the default remains all execution records. `query` (at most 200 characters) searches goal text, run ID, source project, recorded Hosts and execution adapter/model; `%` and `_` are literal text. `projectId` (at most 4096 characters) matches the recorded project ID, and `hostId` (at most 256) matches original or current Host. `filter` is `all`, `active` (including reconciliation-needed), `host` or `review`. Filtering happens before the limit, `total` counts all matching records independently of the cursor, and `nextCursor:null` marks the last page. Every task read includes the [delegation metadata](evaluation.md#console-http-surface); missing original attribution remains unknown.

### Display summary

Contract 0.12.0 adds nullable `workflow.resultSummary` to governed task reads, including history rows, console snapshots and the bounded `workflow_get.task` view. It contains at most 2000 Unicode characters from this run's latest concluded turn with a persisted outcome, ordered by `turn_index`. A newer prepared/running turn does not erase that result; an empty or whitespace-only latest summary becomes null. Helper, routing and request summaries are not substituted. This projection reads existing rows and changes neither schema 11 nor the original task specification.

The console derives list and detail titles from that nonempty summary, then the trimmed first line of the original task, then `未命名委派`. Display whitespace is normalized and headings use a 100-character Unicode-safe excerpt. The original task and recorded execution/acceptance status remain independently visible. Search still matches the original task and existing history fields; no submitted `title` or macro-task field is introduced by this slice.

## Messages and inquiry

| Command | Parameters | Behavior |
| --- | --- | --- |
| `inquire` | `runId`/`taskId`; optional `inquiryId`+`question` (together), `timeoutMs` 100–5000 (default 1500), `waitMs` 0–30000 (default 0) | Read-only observation, or one correlated question to the run's own live agent when its adapter declares the `inquiry` capability |
| `message` | selector, `inquiryId`, `question` (≤4000 UTF-8 bytes); optional `author`, `recipient`, `correlationId`, `waitMs` | Posts one correlated inquiry; repeating the same id and text returns the recorded message without injecting twice |
| `messages` | optional `runId`/`taskId`, `state`, `limit` 1–200 (default 50) | Lists messages for that task, or all tasks when no task is selected; `requestId` is not accepted |
| `message-get` | `messageId`, or a task selector plus `inquiryId` | Reads one inquiry and its recorded answer without resubmitting the question |
| `message-update` | `messageId`, or `runId`/`taskId` plus `inquiryId`; optional `state`, `reason`, `delivery`, `answer`, `actor` | Records caller-provided delivery/answer evidence; `requestId` is not accepted |

Inquiry rules:

- `inquiryId` matches `[A-Za-z0-9._:-]{1,128}`. Repeating an id with different text is a `CONFLICT` for active and terminal tasks. Re-reading through `inquire` requires both the same text and the same id; `message-get` reads it by identity alone.
- Message states are `queued` (recorded, waiting for a delivery boundary), `claimed` (consumed for a proposed step, not delivery), `delivered` (the run's durable commit), `answered` (a correlated reply was recorded), `discarded` (dropped before a boundary) and `unavailable` (no bridge, a terminal task, or a question that can no longer be claimed or answered).
- The DSH inquiry bridge requires the run's correlated reply tool and does not parse assistant prose as an answer. The public `message-update` operation can separately record caller-provided answer evidence; check its attribution. Questions and answers are each bounded at 4000 UTF-8 bytes and at most 32 inquiries are retained per task (`TOO_MANY_INQUIRIES` beyond that).
- `waitMs`/`timeoutMs` only bound this call's wait. An inquiry never extends, pauses or cancels the execution deadline, and it never wakes a terminal agent.
- A bounded question can return in state `delivered` before any answer exists; check `inquiry.answer.available` (and `message.state`) before reporting an answer. An `answered` journal record without usable text is downgraded to `delivered` with a reason rather than reported as answered.
- The no-question form reports durable state plus a bounded `live` observation (`agentStatus`, inbox depth, `lastEvent` and up to 20 activity entries); fields it cannot observe are named in `live.unavailable` instead of being reported as zero. For a positive duration, `deadline` is explicitly an estimate: `estimated: true`, `kind: "estimated-runner-deadline-from-record-createdAt"`, `deadlineBasis: "createdAt + timeoutSeconds"`, plus clock origin, start/deadline timestamps, remaining seconds and `exactTimingAvailable: false`. A task with `timeoutSeconds: 0` reports `deadline: {"available":false,"unlimited":true,"reason":"this execution has no deadline"}` and never invents an expiry.
- `live.agentStatus: "running"` means an agent driver is active, not that it is making progress; raw model reasoning is never exposed. Bridge transport failures report `bridge-unreachable`, `bridge-timeout`, `bridge-refused`, `bridge-invalid-response` or `bridge-mismatched-response`, and a recorded answer carries its provenance (`live-bridge` or `bridge-journal`).
- DSH declares `inquiry`. ZCode declares `observe` and cooperative `inquiry`: `live.deliveryMode: "cooperative-checkpoint"` means the question waits for the root's next checkpoint or finish attempt. Root-native tool evidence and signed receipts bind the answer; settlement makes unanswered questions unavailable, without injected native input. A journal write refusal is also recorded as unavailable (`journal-unavailable`). `command`, `external`, `codex` and Claude P1 mount no inquiry bridge; task status and any recorded activity remain readable. `bridge.canObserve` and `bridge.canAsk` distinguish observation from correlated questions.

## Workers

| Command | Parameters | Behavior |
| --- | --- | --- |
| `workers` | optional `state`, `adapter`, `limit` 1–200 (default 50) | Registered workers with state, adapter, capabilities and current attempt |
| `worker-register` | `workerId`, optional `identity`, `adapter`, `capabilities`, `host`, `pid`, `state`, `commandId` | Registers or refreshes one worker; idempotent by `commandId` |
| `worker-claim` | `workerId`, `claimRequestId`, `nonce` (≥16 chars); optional `taskId`/`runId`, `pid`, `identity`, `capabilities`, `adapter`, `workerInstance` | Atomically claims one queued task; returns the attempt, lease and unguessable capability. A replay with the same `claimRequestId` and nonce returns the identical attempt and generation |
| `worker-reconcile` | `workerId`, `attemptId`, `generation`, `nonce`; optional `claimRequestId`, `pid`, `runtimeIdentity`, `workerInstance` | Reattaches the legitimate worker after daemon downtime; a replaced generation is `STALE_GENERATION` |
| `worker-renew` | `workerId`, `attemptId`, `generation`, `nonce`; optional `phase`, `pid` | Extends the lease and reports `cancelRequested`; `phase` may move `executing`/`finalizing` |
| `worker-progress` | actor fields plus optional `message`, `phase`, `data` | Appends one bounded progress event; `data` accepts only `{"activity": ...}` and records the whitelisted native-activity projection, so a heartbeat never becomes a fabricated activity |
| `worker-result` | actor fields plus `status` (`ok`/`failed`/`cancelled`); optional `result` (object/null), `shutdownConfirmed` (default false), `error`, `exitCode`, `signal`, `artifacts` (≤100), `logPaths`, `runtimeIdentity`, `elapsedSeconds`, `terminationReason`, `commandId` | Commits result, artifacts, state and completion event in one transaction; `terminationReason` is `completed`, `user-cancel`, `deadline`, `harness-error` or `transport-error` |
| `worker-release` | actor fields; optional `reason`, `workerInstance`, `evidence: {"spawnIntentWritten": false}` | Releases an attempt that never started; explicit evidence must come from the claiming instance |
| `worker-start` | `workerId` (default `local`), optional `stateDir` | Starts one detached supervisor in its own session from the stable runtime, with file-backed logs |
| `worker-stop` | `workerId` (default `local`), optional `stateDir` | Writes a cooperative durable stop request; never signals a process this CLI did not create |

Actor fields are `workerId`, `attemptId`, `generation` and `nonce`. Normally these operations are used through `BoardClient`; see [workers.md](workers.md).

## Evaluation, routing and console

`console` opens the private loopback surface with a 60-second single-use entry link. The CLI launches the browser by default; `browser:false` suppresses launch, while `wait:true` keeps the CLI attached to the exact console instance and requests an identity-fenced console close on Ctrl-C. These booleans are local to open and never reach the service. Status/close and the wait observer never cold-start a daemon. `console-snapshot` reads authoritative data without a browser session or model invocation. See [console.md](console.md) for browser sessions and [evaluation.md](evaluation.md) for policy and evidence semantics.

| Command | Parameters | Behavior |
| --- | --- | --- |
| `console` | `action`: `open` (default), `status`, `close`; CLI-only `browser` (default true) and `wait` (default false) for open; optional `expectedConsoleId` for close | Open returns a single-use `url`, `expiresAt`, public `consoleId`, `running`, `alreadyRunning`, asset metadata and CLI `browserOpened`. Status has no entry URL. Fenced close refuses to close a replacement instance |
| `console-snapshot` | none | Current revision, gate, configuration, bounded active profiles and configured selector with their `modelConcurrency`, cards/preferences/annotations and per-profile counters, latest 200 evidence records, latest 50 decisions and latest 100 tasks; unavailable profile count is separate |
| `model-catalog-refresh` | optional `requestId` (≤128) | Explicit native metadata discovery; one monotone `observationId` per request publishes per-harness facts and preserves existing human state. Each harness reports `status: complete\|unknown` with a reason, plus `appliedAdapters` and `staleAdapters`; only a complete result may confirm absence, and a late older response is skipped. New profiles are disabled and enabled state is never reset. No model call |
| `model-profiles` | optional `includeUnavailable`, `limit` (1–200, default 100), `after` profile cursor, `query` (≤200 characters, at most 8 terms), `adapter` | Bounded active/unavailable profile page with associated cards/annotations/preferences/sampleCounts, per-family `modelConcurrency` (`active` occupancy is read-only observation), tableRevision and nextCursor; unavailable entries are hidden unless requested |
| `evaluation-write-begin` | `requestId`, `expectedRevision`, optional `kind`: `maintenance` (default) or `human` | Human is restricted to the authenticated console; maintenance to the Host. Fair writer intent returns writerId/generation/writerToken/state/expiry/revision/position |
| `evaluation-write-renew` | writer fields | Renews an owned intent or active lease, reports state; caller retains the original token |
| `user-policy-publish` | writer fields, `commandId`, `expectedRevision`, optional `profileSettings`, `preferenceChanges`, `annotationChanges`, `modelConcurrency`, `configuration` | Authenticated console only. Lists contain at most 200 field patches; annotations at most 4000 characters; `modelConcurrency` patches are `{adapter, provider, model, limit}` entries that reject `active` and other derived fields. Null preference mode removes, empty annotation clears, omitted fields remain unchanged. Program model/catalog fields and automatic cards cannot be written |
| `assessment-publish` | writer fields, `commandId`, `expectedRevision`, `cards` | Maintenance Host only. Merge changed automatic cards without modifying human annotations/preferences, enablement or catalog fields; preserve unrelated cards. Both publication operations commit revision/event/receipt and release the writer atomically |
| `evaluation-write-abort` | writer fields, `commandId` | Fences and releases this writer, leaving the last complete publication unchanged |
| `evaluation-reader-begin` | optional `kind` (`selection` is the only accepted reader kind) and optional `revision` | Admits one bounded selection reader if no writer intent is present; returns `readerId` and identified revision |
| `evaluation-reader-release` | `readerId` | Idempotently releases that reader and promotes a waiting writer when possible |
| `evaluation-evidence-record` | `profileId`, `kind`, `summary`, `source`; optional `commandId`, `project`, `conditions`, `runId` | Appends attributed, deduplicated evidence, including during an exclusive edit |
| `selection-request` | `requestId`, `task` (≤8192 UTF-8 bytes), optional `requiredCapabilities`, `timeoutSeconds` (5–1800, default 300) | Durable bounded recommendation request; returns `decision`, `decisionId`, `status`, `runId` (nullable) and `duplicate` |
| `selection-get` | `decisionId`, optional `includeAudit` (default `false`) | Compact decision summary with frozen `selectedProfile` and separate `decisionModel`; audit mode includes persisted input/output/proposal |
| `selection-list` | optional `kind`: `select` or `maintain`, `limit` (1–100, default 20), positive integer `before` (≤2^63−1) | Read-only compact history, filtered before pagination; returns `decisions`, `nextCursor` and matching `total`; no model invocation or evaluation lease |
| `evaluation-prepare` | `requestId`, optional `limit` (1–64, default 32), published `profileId`, coding `adapter` | Host-only bounded factual preparation, with durable per-profile progress and stable request replay; no model call or publication lease |
| `evaluation-history` | optional `limit` (1–100, default 20), positive revision `before` | Read-only publication metadata: `revisions`, `nextCursor`, `total`; also available in the console |

Writer fields are `writerId`, integer `generation` and secret `writerToken`. Default leases are 60 seconds for active writers, 120 seconds for waiting intents and 300 seconds for selection readers. An expired or aborted writer cannot publish. `TABLE_BUSY`, `WRITER_NOT_ACTIVE`, `REVISION_CONFLICT`, `STALE_GENERATION` and `UNAUTHORIZED` distinguish admission, liveness, revision and authority failures. Request IDs identify writer intents; command IDs identify publication/abort receipts. Explicit null and omission are different inputs.

Profile fields are `profileId`, `label`, `adapter`, `provider`, `model`, `effort`, `available`, `enabled`, `capabilities`, `contextWindow`, `description`, `source` and optional `unavailableReason`. Identity fields cannot be changed under the same profile ID, and availability must be backed by recorded discovery. Card input is only `profileId`, `summary`, `strengths`, `limitations`, `risks` and `evidenceIds`; derived counters, revision and timestamps are rejected. Preferences contain `profileId`, `mode` (`prefer`, `pin`, `exclude`) and `reason`. Configuration accepts only `decisionProfileId`; the snapshot also returns its code-owned `revision`.

Evidence kinds are `task-success`, `task-failure`, `task-cancelled`, `observation`, `incident`, `correction`, `manual`. Task kinds require `runId`. A summary is at most 2,000 characters, source at most 256, project at most 200, and conditions at most 8 entries. Evidence references in a card must belong to that profile. Discovery failure leaves both the publication and prior catalog intact.

Decision statuses are `queued`, `running`, `completed`, `needs-host`, `failed`, `cancelled` and `stale`. A nullable `runId` means admission resolved without starting a model, for example a missing fixed profile or empty candidate set. Read the decision status even when the internal execution task completed. Cancel an admitted selection by `runId` with `execution-cancel`; submit a new explicit selection request instead of retrying or acknowledging the internal task. Historical `kind: maintain` decisions and proposals remain read-only. Current evaluation updates follow [Harness-owned maintenance](evaluation-maintenance.md), which never creates an internal maintenance task.

## Defaults and bounds

| Setting | Default | Bounds |
| --- | --- | --- |
| Task execution deadline `timeoutSeconds` | 1800 s | explicit `0` for no deadline; positive 10–86400 s |
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
| Governed continuation input | — | 64 KiB |
| Helpers per Host decision | — | 8 |
| Selection request task text | — | 8192 UTF-8 bytes |
| Decision timeout | 300 s | 5–1800 s, plus a 10 s Worker shutdown margin |
| Compact governed view | 5 requests / 10 turns / 32 children / 32 artifacts | `counts`/`truncated` describe omissions |
| Attempt lease `BUDDY_LEASE_SECONDS` | 120 s | 15–3600 s |
| Cleanup plan expiry | 900 s | fixed; an expired plan authorizes nothing |
| Concurrent attempts `BUDDY_MAX_CONCURRENT` | 8 | 1–32; one machine-wide ceiling shared by routing and execution |
| Model-family concurrent attempts | 2 | 1–32 per exact adapter/provider/model; user-set on the model card, hot at the next claim |
| Wait capacity `BUDDY_WAIT_CAPACITY` | 32 | 1–48; leaves native RPC callbacks available for control operations |

## Error codes

| Code | Meaning |
| --- | --- |
| `INVALID_ARGUMENT` | A parameter is missing, unknown or out of bounds |
| `NOT_FOUND` | The selector matches no task, worker, message or artifact |
| `NOT_READY` | No persisted result/attempt exists yet (for example `result` before completion) |
| `CONFLICT` | The same identity with changed input: changed submit input, changed inquiry text, a different repeated acknowledgement, a closed boundary or an already-published command |
| `REVISION_CONFLICT` | A stale revision tried to mutate state |
| `STALE_GENERATION` | A replaced attempt or owner generation tried to act |
| `UNAUTHORIZED` | The private token, control capability or worker receipt was rejected, or a continuation target is not an owned descendant |
| `ANCESTOR_TERMINAL` | A cancelled or accepted ancestor blocks descendant continuation |
| `SHUTDOWN_UNCONFIRMED` | Retry, replacement or a continuation was refused because the previous stop is not verified |
| `ILLEGAL_TRANSITION` | The state machine refuses that task/attempt transition |
| `WORKER_BUSY` / `WORKER_STOPPING` | The worker already runs an attempt / is draining and accepts no claim |
| `NOT_REGISTERED` / `ALREADY_RUNNING` | The worker id is unknown / already supervised |
| `ATTEMPT_FINISHED` | Progress or a mutation arrived after the attempt finished |
| `ADAPTER_UNAVAILABLE` / `UNSUPPORTED_ADAPTER` | The adapter cannot run here / is not `dsh`, `zcode`, `codex`, `command` or `external` |
| `GOVERNED_REQUIRED` | Coding work or a governed record was addressed through the advanced execution path; use the governed command |
| `CONFIGURATION_REQUIRED` | A routing boundary must be resolved with a complete configuration or a reroute |
| `CONFIGURATION_CONFLICT` | A supplied configuration does not preserve the goal's original hard constraints |
| `CONFIGURATION_UNAVAILABLE` / `INVALID_CONFIGURATION` | The native catalog does not offer the tuple / the installed tuple changed and must be chosen again |
| `CATALOG_INVALID` / `CATALOG_UNAVAILABLE` | Native model discovery returned unusable data / was not performed |
| `CATALOG_LIMIT` | The current native profile set exceeds 200 configurations; nothing was silently dropped |
| `INVALID_WORKSPACE` | A workspace path or scope is absolute, escapes the checkout, names `.git` or does not exist |
| `WORKSPACE_CHANGED` / `WORKSPACE_CONFLICT` | A compare-and-swap found the site changed since it was recorded / the recorded conflict does not match the site |
| `INTEGRATION_REQUIRED` | An accepted verdict has no verified or `notRequired` integration record for the final artifact |
| `INTEGRATION_UNVERIFIED` | The artifact was not present in the claimed target; no integration record was created |
| `PLAN_EXPIRED` | The cleanup plan expired; request a fresh plan before deleting anything |
| `STATE_INTEGRITY` | A governed run lacks its required recorded scope; preserve the state and repair a verified offline copy instead of inventing authority |
| `PACKET_TOO_LARGE` | A bounded preparation packet exceeded its input bound; nothing was recorded |
| `ARTIFACT_MISSING` / `ARTIFACT_MISMATCH` | A claimed artifact failed validation and no completed result was published |
| `TOO_MANY_INQUIRIES` | The 32-inquiry retention limit for the task was reached |
| `TABLE_BUSY` / `WRITER_NOT_ACTIVE` | The evaluation writer gate is closed / the writer grant is no longer active |
| `WAIT_OVERLOAD` | Every admitted wait slot is busy; the service's `details.cursor` says where to resume |
| `WAIT_ABANDONED` | The wait was interrupted; the task was not cancelled |
| `SERVICE_UNAVAILABLE` | The service could not be reached, or no service runs for `wait`/`watch` |
| `SERVICE_START_FAILED` / `SERVICE_START_TIMEOUT` | The daemon failed to start or become ready |
| `RESULT_NOT_AVAILABLE` / `RESULT_MISSING` / `RESULT_MISMATCH` / `RECOVERY_MISMATCH` | `await` could not deliver the promised result; the execution status is still reported honestly |
| `RUNTIME_INSTALL_FAILED` / `RUNTIME_INSTALL_TIMEOUT` / `UV_NOT_FOUND` / `RUNTIME_NOT_READY` | Stable-runtime installation or pinning failed; see [operations.md](operations.md#runtime-lifecycle) |
| `INSECURE_CONTROL_FILE` | A `controlFile` is not a private regular file owned by the current user with mode `0600` |
| `FORBIDDEN` | An attempt-scoped credential tried to use Host authority |
| `MESSAGE_TOO_LARGE` / `INVALID_RESPONSE` | The request or the service response exceeded the transport bound / was malformed |
| `INTERNAL_ERROR` | Unexpected service failure; set `BUDDY_DEBUG` for detail |
| `CONSOLE_READ_ONLY` | A newer console session owns write authority; this session may still browse |
| `CONSOLE_SESSION_EXPIRED` / `CONSOLE_ENTRY_EXPIRED` | The browser session is unavailable / the one-use entry is used or expired; reopen through the CLI |
| `CONSOLE_LIMIT` | The bounded collection of pending entries or active browser sessions is full |

## `await` envelope

`await` builds one envelope: `runId`, `requestId`, `status`, `workflowState`, `outcome`, `ok`, `resultAvailable`, `resultDelivered`, `shutdownConfirmed`, `shutdown`, `acceptedAt`, `revision`, `createdAt`, `updatedAt`, `cwd`, `logPaths`, `waitedSeconds`, `waitSeconds`, `maxWaitSeconds`, `timedOut`, `reconnects`, `result`, `recovery`, `error` and a `note`; the default [brief view](#output-views) prints it without the constant and null fields. A wait slices its window into bounded 30-second service waits with at most three reconnects, uses only read operations, and never cold-starts a service for `wait`/`watch`.

`outcome` is the terminal task status (`completed`, `failed`, `cancelled`, `reconciliation-needed`), governed `waiting-host` at a decision boundary, `wait-timeout` when this call's window ended first, `completed-no-result` when a completed run has no deliverable result, or `unavailable` when the service could not be reached while waiting. A task that reaches its own execution deadline ends as a terminal `failed` with the adapter `status: "timeout"`. The `recovery` block names real commands with the existing run ID (`buddy status`, `buddy await`, `buddy result`, `buddy get`, `buddy cancel`). `ok` is true only when the outcome is `completed` **and** this envelope carries the result payload **and** no error was recorded.

A governed Host boundary additionally reports `goalComplete: false`, the current `request`, compact turn/artifact context, a compact result view and `nextCommands` (approve/decline for assistance or ordinary attention; routing attention offers complete `configuration` or `reroute` continuations); the generated commands use the saved `controlFile` and name the exact revision. Use `get` to inspect all pending requests. Governed task views expose `workflowState` and `workflowShutdown`. The wait envelope carries the aggregate shutdown under `shutdown`, and its `shutdownConfirmed` requires both `selfConfirmed` and `descendantsConfirmed`. `unconfirmedCount` covers the complete owned lineage including the root when applicable; the ID list is bounded and may be truncated. Logical `workflowState: "cancelled"` does not assert that descendants stopped. The wait continues on committed stop evidence, even when the root task is already terminal, and returns `wait-timeout` with unconfirmed shutdown if its own window expires first. Missing PIDs and expired leases do not satisfy this evidence requirement.

### 0.15.1 objective presentation

`submit.objective` accepts optional `description`, a nonempty string of at most 300 Unicode characters when present. It is immutable display metadata, excluded from execution/selection and included in submission replay identity. The skill requires a ≤30-character intent title on new delegations, while the optional 200-character API limit remains unchanged. Objective read shapes add `description`, `counts.accepted` (accepted roots), and timeline row `taskSummary`; see [objectives](objectives.md). The console-only `objective_stop` is not a Host CLI command; ordinary Host `cancel` and its control capability remain unchanged.
