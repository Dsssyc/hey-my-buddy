# `hey-my-buddy` CLI reference

`hey-my-buddy` is the supported entrypoint: one command per operation, one JSON object argument — given as the positional argument, as `--params-file PATH`, or on standard input as `-` — and one JSON object on stdout. This page is the complete command surface and its defaults, bounds, envelopes and error codes. Usage flows are in [usage.md](usage.md); the micro task lifecycle is in [workflow.md](workflow.md); runtime lifecycle is in [operations.md](operations.md).

## Invocation

```sh
HMB="$HOME/.agents/skills/hey-my-buddy/scripts/hey-my-buddy"
"$HMB" status '{"runId":"<runId>"}'
```

The shared skill's `scripts/hey-my-buddy` is the single launcher. It resolves its project from the skill's `package/`, or the repository root for `skills/hey-my-buddy/scripts/hey-my-buddy` in a checkout. Installation, upgrade and `HMB_DEV_SOURCE=1` use uv with Python 3.12; daily calls use the pinned runtime interpreter. From the repository root the same CLI is `uv run --frozen hey-my-buddy <command> '<json>'` or `uv run --frozen python -m hey_my_buddy.cli.main <command> '<json>'`. The JSON argument defaults to `{}`; an unknown method name prints the closest candidates (`didYouMean`) and exits 2. Service request schemas reject unknown JSON parameters with `INVALID_ARGUMENT`; the local `worker-start`/`worker-stop` commands validate their own small parameter set.

## Parameter input

Every method takes one JSON object. Give it exactly one way: as the positional argument, as `--params-file PATH`, or on standard input as `-` (`hey-my-buddy status -` and `hey-my-buddy status --params-file -` are the same thing). The sources are mutually exclusive — naming two exits 2 with a structured `INVALID_ARGUMENT` envelope and never calls the service. A file or stream is read as raw bytes, must decode as UTF-8 (an optional byte-order mark is accepted; invalid UTF-8 is `INVALID_ARGUMENT`), and is capped at 8 MiB, the same bound the transport accepts for one request; an oversized document is refused before any socket or daemon exists. The bytes are parsed as JSON directly and are never re-interpreted by a shell, so quoting, `$(...)`, backticks, backslashes, whitespace and non-ASCII text reach the service verbatim. Everything after parsing is identical to the positional form: `output` stays CLI-local, `controlFile` injection resolves the same private `0600` capability, the attempt-scoped credential is injected the same way, and a document that is not one JSON object fails with `INVALID_ARGUMENT`.

## Method help

`hey-my-buddy help` lists every method with a one-sentence description and the three input forms. `hey-my-buddy help METHOD` prints that method's accepted parameters with the type, default and bounds the request validator enforces, marks parameters that only apply under other inputs (`conditional`, for example `buddy` and `reroute` on `continue`, or `expectedConsoleId` on `console close`), and names the validator and source line the facts came from. Fields read out of a nested object are printed under it (`buddy.harness`, `helpers.task`); the retired nested `spec` spelling no longer exists, so the ordinary spec fields print once, where they are given. A condition the reader cannot reduce to one bound (a hash comparison, a duplicate-key check, a cross-field conflict) is printed as the readable condition from the source with its location instead of being dropped. A field the validator accepts but never bounds says exactly that; a field-name set computed at runtime is named as runtime-computed. The facts are read statically from the same request validators the service enforces, on every invocation: there is no second parameter schema to drift, and changing a validator constant changes the next `help` output. Help starts no service, worker or model, opens no socket and reads no credential. A mistyped method name prints the closest candidates and exits 2, and an unknown method name is never forwarded to the service.

Every CLI name maps to one named C-Two operation. The micro task lifecycle keeps the short public names (`submit`, `get`, `decide`, `continue`, `takeover`, `cancel`, `accept`, `conclude`, `reclaim`, `suggest`) for the `workflow_*` operations; `execution-submit`, `execution-cancel`, `execution-retry` and `execution-acknowledge` reach the ordinary `task_*` records used by the `command` adapter and the internal `decision` infrastructure. The workspace lifecycle adds `scope-amend` → `workflow_scope_amend` and `workspace-resolve` → `workflow_workspace_resolve`; [workspace-lifecycle.md](workspace-lifecycle.md) owns their fields and eligibility rules. Do not conflate the two spellings: the short name is CLI syntax, the `workflow_*` or `task_*` name is the stable operation identity.

The CLI injects Host authority locally. For any mutation that requires it, `controlFile` names a private `0600` file bound to one run and owner generation; alternatively the complete `hostId` + `ownerGeneration` + `controlToken` triple may be passed explicitly. There is deliberately no implicit "latest generation" lookup, and an attempt-scoped agent credential may not use a `controlFile`. Newly returned control capabilities are saved privately and only the file path is printed.

## Envelope and exit status

- Success prints one compact JSON object on one line and exits 0. The object is the brief output view described below unless the caller passed `"output":"full"`.
- A failure prints `{"error":{"code":"...","message":"..."}}` and exits 1. The service can attach an `error.details` object (a revision, a resumable cursor, conflicting fingerprints, `legalEfforts`, `routingTaskId`), but the transport wrapper used by the CLI and `BoardClient` rebuilds errors from `code` and `message`; those details remain in the raw C-Two response. Command-line usage errors are argparse errors and exit 2.
- A transport error or invalid response can leave the submission outcome unknown: the task may have committed before the reply was lost. Query the original `requestId`, or replay the identical submission to recover it. A different request ID can duplicate work. A `WAIT_ABANDONED` envelope ends only the wait.
- Exit 0 only means the call returned. It is **not** task success: read `status`, `outcome`, `resultDelivered` and `shutdownConfirmed`, then inspect the artifacts.
- `await` may print a `WAIT_ABANDONED` envelope when the wait is interrupted; the durable task keeps running and the envelope names the real recovery commands.

## Output views

Every printed response stays in the calling Host model's context and is re-read on each later request, so the CLI prints a brief projection of the service response by default. `output` is a CLI-local field: `"output":"brief"` (the default) or `"output":"full"`. It is removed before the RPC, so the service, `BoardClient` and the console always receive and return the complete views, and `"output":"full"` prints the unmodified response. `console` keeps its complete response. Commands without a projection print their complete response in compact JSON. Error envelopes are never projected.

The projection only omits material the caller already supplied (the micro task packet and `executionWorkspace` intent), duplicates (`taskId` beside `runId`, the execution summary under `task`, a cleanup plan echoed as both `plan` and `cleanup`, `tasks` beside `runs`, turn/artifact copies inside an `await` result) and history not needed for the next decision (older turns and requests, input artifacts, fingerprints and timestamps). It keeps every identifier a following command needs, the complete `activeRequest`, workspace conflicts, errors, nonzero truncation with its counts, and the complete `shutdown` report whenever shutdown is not fully confirmed. A value the service did not report is never replaced by a default. Projected objects carry `"view":"brief"` or, for a mutation, `"view":"receipt"`.

| Command | Brief view |
| --- | --- |
| `get` | `runId`, `requestId`, `state`, `status`, `queueReason`, `awaitingHost`, `waitReason`, `revision`, `ownerGeneration`, `continuationCount`, `buddy`, `routing` (`status`, `source`, `reason`, `selectedBuddy`, `decisionId`), `workspace` (`path`, `kind`, `access`, `inputCommit`), `scope` (`scopeVersion`, `writeScope`), `currentTurn` (identity, state, disposition, `summary`, `remaining`), `activeRequest`, `pendingRequests`/`openRequests` when more than one is open, compact `children`, `artifacts` limited to output artifacts (`artifactId`, `kind`, `turnId`, `manifestSha256`, `outputCommit`, `diffPath`, `diffSha256`, `changedPaths`) with an `otherArtifacts` count for the omitted inputs and manifests, `workspaceConflicts`, compact `integrations` and `cleanup`, `finalArtifactId`, `acceptedAt`/`acceptanceVerdict`, `shutdown`, `counts` and nonzero `truncated` entries. `includeAudit` and `routingHistory` are returned as requested |
| `submit` and micro task mutations (`decide`, `continue`, `takeover`, `cancel`, `accept`, `conclude`, `reclaim`, `scope-amend`, `workspace-resolve`, `suggest`) | The `get` brief view without the turn summary, plus the operation's own result: `duplicate`, `controlFile`/`control` (never a token), `verdict`, `integrationId` and compact `integration`, compact `plan` (`planId`, `state`, `eligible`, `reasons`, `path`, `expiresAt`, retained-item counts, `result`), compact `reclaim` (`removed`, `alreadyRemoved`, `reasons`, `path`, `planId`), `removed`, and a different `targetRunId`/`targetRevision`. An unrecognized operation field is kept |
| `await` | The envelope without `note`, `maxWaitSeconds`, `createdAt`, `cwd` and null fields; `logPaths` only when `ok` is false; a micro task result without its runner `finalText` when the turn summary is present |
| `status`, `execution-*` | Task identity (`runId`, `taskId`, `requestId`, `createdAt`) and state, attempt identity/state and `workerId`, `resultAvailable`, `shutdownConfirmed`, acceptance fields, `buddy`, the compact `workflow` summary, `workflowShutdown`, `activity`, `inquiries` and a `title`; no `spec`, task text or runner payload |
| `result` | The `status` view plus `attemptId`, `resultDelivered`, the verified execution `artifacts`, `resultMeta` without its duplicate artifact list, and the micro task result view (or the opaque infrastructure `result`) |

Cancelled micro task `get` and execution `result` views expose `cancellation: {actor, reason}` in both brief and full output when their own durable cancellation event exists. A missing event or field stays unknown. A full `get` also carries the selected attempt's `task.terminationReason`, `task.stopInitiator` and `task.graceDeadline` ([architecture](architecture.md)), and for an interrupted execution `interruptionEvidence: {kind, legacyStartedAt}` in both brief and full output. A completion already recorded before micro task cancellation keeps its completed execution result.
| `list` | `runs` rows with `runId`, `requestId`, `adapter`, `model`, `status`, `workflowState`, `awaitingHost`, `kind`, `parentRunId`, `currentHostId`, `createdAt` and a `title`; `total`, `cursor`, `nextCursor` |

## Service

| Command | Parameters | Behavior |
| --- | --- | --- |
| `ping` | none | Authenticated process liveness: status, protocol/contract/schema versions, service id, PID and the stored persistence error; no integrity or runtime inspection. Internal client attachment uses this operation |
| `health` | none | Protocol/contract/schema versions, service id, state dir, runtime identity and stability, model `capacity` (`totalLimit`, `totalActive`, `models` entries `{harness, provider, model, limit, active}`), `maxConcurrent`, `serviceIssues` (problems the service recorded about itself, such as an admission error or a failed removal of the retired Worker state, until they are resolved), `waitCapacity`, `backupPreflight`, integrity and active work |
| `harnesses` | optional `includeUnavailable`, `refresh`, `harness` | Connector reports under `adapters`, recorded `harnesses` health, `localCapabilities`, named operations, wait admission and the honest `limitations` map |
| `backup-preflight` | none (CLI-local) | Read-only whitelist inventory of copied, skipped and refused entries; no service or runtime startup |
| `runtime` | optional `destination` | Runtime identity, installed-runtime description and source-leak report |
| `restart` | optional `reason`, `drainSeconds` 0–120 (default 10) | Detaches the daemon without cancelling owned work; running executions survive. With no running daemon returns `alreadyStopped` and starts nothing |
| `stop` | optional `reason`, `drainSeconds` 0–120 (default 10) | Cancels queued tasks, writes durable cancel intent for active attempts, drains and returns `unresolvedAttempts`. With no running daemon returns `alreadyStopped` |

## Micro task lifecycle

The [workflow guide](workflow.md) gives complete packets and examples. These operations use the existing task/attempt queue; `runId` remains the logical micro task across turns. A normal structured yield makes `await` return `outcome: "waiting-host"`, while an approved helper or authorized continuation path can continue within the same wait. The workspace-lifecycle controls are owned by [workspace-lifecycle.md](workspace-lifecycle.md), which includes the complete accept/conclude/reclaim example and the eligibility rules behind each operation.

| Command | Parameters | Behavior |
| --- | --- | --- |
| `submit` | ordinary flat submit fields, `hostId`, `executionWorkspace` and `submissionToken` | Prepare a fixed workspace, admit a micro task and route its execution buddy; return the compact view plus owner-private `control`/`controlFile` |
| `get` | `runId`, optional `includeAudit`, `routingHistory` | Compact owner, revision, routing, turn, request, helper, artifact and shutdown view; no control tokens. `includeAudit:true` adds full turns, requests, continuations, reservations, suggestions and the immutable original spec; `routingHistory` requests one bounded page of this run's route records |
| `decide` | `runId`, active `requestId`, `commandId`, `expectedRevision`, control, `decision: approve\|decline`, `reason`, explicit `helpers` (≤8), `autoContinue` (default true) | Record Host authority; create approved helpers or decline without failing the micro task |
| `continue` | `runId`, `targetRunId`, `commandId`, `expectedRevision`, required `input` (string or object, ≤64 KiB), optional `reason`, `helperPolicy: keep\|cancel` while helpers are live, `buddy`, `reroute`, `access` with `accessReason` when it is `full` | Record new input and invalidate all older unconsumed continuations, including manual inputs and automatic triggers; adopt a complete buddy or reroute the same micro task when ownership permits; a given `access` applies from the next turn on, and omitting it keeps the current level |
| `takeover` | `runId`, `commandId`, `expectedOwnerGeneration`, `newHostId`, optional `expectedRevision`, control | Rotate control capability/generation; do not restart work |
| `cancel` | `runId`, optional `commandId`, optional `reason` (default "Host cancelled"), control | Fence the micro task and complete owned descendant graph; record cancellation separately from aggregate stop evidence |
| `accept` | `runId`, `targetRunId`, required `artifactId`, required `note` (≤10000 bytes); either `target {path, ref, repositoryId?, checkoutId?}` or a nonempty `notRequired` reason string; optional `adjusted`, `hostPaths` (≤256, requires `beforeCommit`), `beforeCommit`, `keepCheckout`, control | Accept the exact delivered artifact: the service verifies the integration against the real target itself and reclaims the checkout. Differing paths are refused with their names first; the Host's own adjustments are confirmed with `adjusted: true`. The same payload replays, a changed payload conflicts |
| `scope-amend` | `runId`, `commandId`, `expectedRevision`, `expectedScopeVersion`, `writeScope` (≤256), `reason` (≤4000 bytes), control | Append one authorized scope version for the next stage after every related attempt and descendant is confirmed stopped; never re-authorizes an active attempt or rewrites earlier turns |
| `workspace-resolve` | `runId`, `commandId`, `expectedRevision`, `conflictId`, `action: restore\|adopt\|abandon`, `observedFingerprint` (sha256), optional `paths` (restore), `reason` for adopt/abandon, control | Settle one recorded out-of-scope failure site by compare-and-swap restore, explicit adopt or abandon; mechanical resolution needs no further model turn |
| `conclude` | `runId`, `targetRunId`, required `note` (≤10000 bytes), control | End a failed, cancelled or delivered-unaccepted micro task: seal unsealed managed changes as an independent Host partial output, record the conclusion beside the real execution result and reclaim the checkout; unresolved conflicts, pending continuations or live helpers refuse instead |
| `reclaim` | `runId`, `targetRunId`, control | Reclaim this micro task's own registered managed checkout after a blocked reclaim or a `keepCheckout` acceptance; a blocked removal reports its reasons and changes nothing |
| `suggest` | `runId`, required `body` (≤4000 bytes) | Record one bounded suggestion; an agent-scoped caller may suggest only on its own task |
| `await` | `requestId` or `runId`; optional `waitSeconds` (1–86400, default 86400) | CLI-level read-only wait on an existing task; never starts, resumes, retries or cancels work |

Submit fields are `requestId` (required, ≤128), `hostId` (required, ≤256), `task` (required, ≤1 MiB), `cwd` (required absolute existing directory), optional `harness` (`dsh`, `zcode`, `codex`, `claude`), `model`, `provider`, `effort` (all four or none; `adapter` and `argv` are refused, because command executions are execution records submitted with `execution-submit`); `expectedSeconds` (1–86400, default 1800; an expected duration, not a deadline; the former `timeoutSeconds` is refused), `fixes` (the `runId` of an accepted micro task this one fixes; [workflow](workflow.md#fixes)), `owner`, `requiredCapabilities` (≤32), `exclusiveResources` (≤32), required `executionWorkspace` and optional `submissionToken` (16–256 chars). Spec fields are given flat; the nested `spec` object, `executionWorkspace.cwd`, `executionWorkspace.integrator`, `routingPreferences`, `routingMode` and `allowRoutingFallback` are retired spellings, each rejected with its own message. Display metadata sits outside the spec: a root-level `title` (1–200 characters, whitespace normalized) and one of `macroTask: {title}`, `macroTaskId` (`macro-…`) or `macroTaskOf`; they enter the request fingerprint but never the execution spec, Worker input or Router packet ([macro tasks](macro-tasks.md)). Any other field is rejected.

`executionWorkspace` is a required object, and its `kind` must explicitly be `existing` or `worktree`; omitting the object or its kind is `INVALID_ARGUMENT`. It accepts `access: read|write|full` (default write; `full` needs a one-line `accessReason` of at most 1000 characters, and a buddy whose harness does not provide the chosen level is refused with `UNSUPPORTED_CAPABILITY`; the four built-in harnesses provide all three), `base: {kind: commit|working-tree, ref?}` (default working-tree), `includeUntracked` (≤256, requires a working-tree base), `writeScope` (≤256; write access defaults to `["."]`) and optional attribution-only `targetRef`. Source `cwd` and `integrator` are derived from the top-level `cwd` and the submitting Host; supplying either inside `executionWorkspace` is rejected. Include/write paths are checkout-root-relative; write access needs a nonempty scope. Workspace preparation is idempotent by request ID and records exact input commits; no automatic branch merge or force-clean occurs. Git-ignored runtime data is excluded unless explicitly selected.

DSH session grouping is removed ([ADR-021](../decisions/021-router-buddy-planes-and-routing-evidence.md)): every DSH run uses an execution-private session root, and a `workspace` field on a submission is rejected as unknown. `executionWorkspace` is the Git isolation contract and never meant session grouping.

A complete explicit `harness`/`provider`/`model`/`effort` quadruple is validated against the installed native catalog and delegated directly; a partial quadruple is rejected with the three remaining expressions (complete quadruple, or omit every field and let the Router choose from the task description); an unspecified quadruple auto-routes through the bounded evaluation table. The mode is `configuration.defaultRoutingMode`; the retired `routingMode` and `allowRoutingFallback` inputs are rejected. No legal candidate or no available Router in the selected mode produces a durable routing attention boundary that `decide` refuses and `continue` resolves with `buddy` or `reroute:true`. See [workflow.md#buddy-and-routing](workflow.md#buddy-and-routing).

Continuation reuses the currently allocated actual checkout, prepares a new fixed input there and preserves the original submission specification. The returned `workspace` describes the current effective allocation; `executionWorkspace` retains the original intent. Helper outcomes identify the selected attempt/generation and include only that attempt's sealed output reference, with explicit artifact attempt/turn IDs. Use the output commit and snapshot/diff hashes for integration rather than a moving branch or working directory.

`get` distinguishes the following request and shutdown fields:

| Field | Meaning |
| --- | --- |
| `activeRequest` | Full current open boundary accepted by `decide`, or `null`; includes `summary`, `attempted`, `neededWork`, `expectedArtifacts`, `acceptance`, `suggestedBuddyId`, `proxy`, `origin`, `routing` and any `preparationError`; use the top-level current `revision` for the decision |
| `pendingRequests` | Bounded open-request queue including the current active request; at most 5 compact entries |
| `counts.openRequests` | Complete open-request count, including the active request and entries omitted from the compact queue |
| `truncated.pendingRequests` | Number of open-request entries omitted from `pendingRequests` |
| `routing` | `status`, `decisionId`, `taskId`, `attemptId`, `generation`, `selectedBuddy`, `reason`, `constraints`, `source` (`model-selection` for a Router choice, `single-candidate` for the program's sole-candidate direct selection, `no-candidate` for a zero-candidate Host boundary; each record keeps the source its own frozen basis supports), `routingMode` (actual) |
| `proxy`, `origin` on a request | Immediate source `{runId, requestId}` and original requesting leaf `{runId, requestId}` for nested attention |
| `shutdown` | `selfConfirmed`, `descendantsConfirmed`, full `unconfirmedCount`, up to 32 `unconfirmedRunIds`, and boolean `truncated` |

Do not add `activeRequest` to `counts.openRequests`: it is already included. Compact responses limit recent request summaries to 5, turns to 10, children to 32 and artifacts to 32; `counts` and `truncated` describe the complete collections and omitted entries. After deciding the active request, use the returned or freshly read boundary and revision for the next decision. Another request can become active immediately, and sibling completion does not discard outstanding requests.

For nested attention, submit `decide` against the root run and its active proxy request using that root's control. The service validates the owned chain to `origin` and resumes the requesting leaf; the root token cannot authorize a direct mutation of a different run ID. Intermediate runs wait for their dependencies. `continue` may pass `targetRunId` to deliver input or a buddy to an owned descendant without adopting another owner's capability; an unrelated run is `UNAUTHORIZED`.

At most eight helpers are authorized by one decision, and their specs accept the ordinary flat task fields, `harness`, `executionWorkspace`, optional `integrator` (boolean) and `role` (`helper` or `integrator`). At most one helper per approval may be the named integrator. The retired `inheritRoutingPreferences` flag is rejected: task-local routing preferences no longer exist, so there is nothing to inherit. Continuation input is bounded to 64 KiB. The turn input is bounded to 256 KiB and the structured outcome to 64 KiB, with at most 32 entries per outcome array. DSH and ZCode report `native-session` for a proven previous session bound to the same micro task, checkout and buddy; without a proven previous session or with a changed buddy, they report `reconstructed-new-session`, and so does DSH when the continuation changes the permission level. Codex reports `native-session` only for a proven binding whose last stored native turn still completes, and otherwise reconstructs explicitly. A missing or mismatched binding on a native-session request is rejected. Use the exact same command ID and payload to resolve an uncertain response. Re-read and reconsider after a revision/owner-generation conflict.

### Host workflow fields

`submit` adds top-level `macroTaskOf` (run identifier, up to 128 characters) and `buddyLocked` (boolean, default false). Only one of `macroTask`, `macroTaskId` and `macroTaskOf` may be supplied. The referenced run must have a macro task with the same original Host and project; `NOT_FOUND` and `MACRO_TASK_REQUIRED` distinguish unknown and ungrouped runs. Display metadata stays out of execution and routing input.

`continue` may change an explicit buddy after failure by supplying the complete `buddy` and nonempty `reason`; `buddyLocked:true` fences incompatible changes. It preserves the run and allocated checkout, and continuing a delivered micro task records the negative review with the Host's opinion next to the immutable artifact. `get` reports the lock, partial artifacts and current Host conclusion. `accept` adds `hostPaths` (up to 256 relative paths) with the required `beforeCommit`; verified paths must occur in the target commit interval and be outside the whole artifact's paths.

At an `awaiting-host` boundary, `accept` with the exact current sealed `artifactId` can finish the micro task after verified integration and confirmed lineage shutdown. `conclude` ends a failed, cancelled or delivered-unaccepted micro task with a required note: unsealed managed changes are sealed as an independent Host partial output, the original execution status is retained in `hostConclusion.executionStatus`, and the micro task enters terminal `concluded`. The automatic reclaim keeps the recorded acceptance or conclusion and reports its reasons when blocked; a later `reclaim` retries subject to the same stop, identity, dependency and unsealed-change checks. The [workflow contract](workflow.md) owns these transitions.

Output artifacts may include `cumulativePatch` with its original micro task input commit, output commit, path and hash; `partial-output` is explicitly partial, unverified and non-final. Brief projections retain these distinctions, `hostConclusion`, `buddyLocked` and quota reminders. Execution and turn views carry `tokenUsage`; null or null counters mean unknown. Input tokens include cached input, so cache must not be added again.

## Macro tasks

| Command | Parameters | Behavior |
| --- | --- | --- |
| `macro-task-list` | optional `limit` 1–100 (default 50), `before`, `projectId`, `hostId`, `query` (≤200), `filter` (`all`, `active`, `host`, `review`) | Macro task and standalone micro task summaries ordered by latest recorded activity, with `total`, `nextCursor`, the event `cursor` and `changed` |
| `macro-task-timeline` | `macroTaskId` (`macro-…` or `run:<rootRunId>`), optional `limit` 1–200 (default 100), `query`, `filter` | One group's rows, spans (≤800) and Host markers (≤800) with totals, per-collection truncation flags, `filtered` and `scopeComplete` |

Both are Host/user reads with no model call or publication, refused to attempt-scoped credentials; the [macro tasks reference](macro-tasks.md) owns their shapes. Their responses are bounded and printed without a brief projection.

## Execution records

Advanced execution records back the `command` adapter and the internal decision infrastructure. Coding work must use `submit`: `execution-submit` refuses coding harnesses or an omitted adapter with `MICRO_TASK_REQUIRED`. A micro-task run's execution record requires its Host control for authorization and then still refuses with `MICRO_TASK_REQUIRED`, pointing at `cancel`, `continue` or `accept`.

| Command | Parameters | Behavior |
| --- | --- | --- |
| `execution-submit` | `requestId`, `task`, absolute `cwd`, `adapter: command`, `argv` for `command`, optional `expectedSeconds`, `owner`, `requiredCapabilities`, `exclusiveResources`, `model`/`provider`/`effort` | Admit one ordinary execution record and return its task view immediately |
| `execution-cancel` | `runId`/`taskId`/`requestId`; optional `reason`, `requestedBy` | Queued task → `cancelled`; active attempt gets a durable cancel request (`cancelRequestedAt`) and becomes `cancelling`. Unrelated dsh work is untouched |
| `execution-retry` | selector; optional `reason`, `requestedBy` | Requeue an eligible failed, cancelled or reconciliation-needed task; its next admission creates a new generation. Clears previous acceptance (archived in events). Completed tasks cannot be retried; `SHUTDOWN_UNCONFIRMED` refuses retry while shutdown is unverified; internal decision runs are not retried |
| `execution-acknowledge` | selector plus `note` (required, ≤10000 bytes), `verdict` (default accepted), optional `evidence` (≤32), `acknowledgedBy` | Records review of a persisted result with confirmed shutdown. Never changes execution status; a different repeated note or verdict is `CONFLICT` |

## Execution observation

Observation stays separate from the micro task boundary. `status`, `result`, `list`, `artifacts` and `events` read execution records; `wait` and `watch` use the dedicated bounded wait resource; `inquire` is bounded read-only observation or one correlated question to a live DSH or ZCode root.

| Command | Parameters | Behavior |
| --- | --- | --- |
| `status` | `runId`, `taskId` or `requestId` | Task view: state, `queueReason`, revision, selected attempt, worker, artifacts, inquiry counts; a micro-task run also exposes `workflowState`, `awaitingHost`, `workflowShutdown`, `ownerGeneration`, `continuationCount` and the active request kind/summary |
| `result` | selector | Task view plus the adapter outcome under `result` and commit metadata under `resultMeta` (`status`, `error`, `exitCode`, `signal`, `shutdownConfirmed`, `completedAt`); `NOT_READY` before a persisted result exists |
| `list` | `limit` 1–100 (default 20), `offset`, `state`, `adapter`; optional `before`, `rootsOnly`, `query`, `projectId`, `hostId`, `filter` | Tasks newest first, with filtered `total`, the event `cursor`, and nullable history `nextCursor` |
| `artifacts` | `runId`, `taskId` or `attemptId` | Verified artifact records (kind, location, content hash, size) |
| `events` | `after` cursor, `limit` 1–200 (default 100), `taskId`/`runId` | Committed events with `events`, `cursor`, `head`, `truncated` |
| `watch` | `after` cursor, `timeoutMs` 0–30000 (default 30000), `limit`, `taskId`/`runId` | Bounded event wait on the dedicated wait resource; adds `timedOut` |
| `wait` | `runId`/`taskId`, optional `afterRevision`, `timeoutMs` 0–30000 (default 30000) | Bounded wait for one task to change or finish; returns the task view |
| `wait-capacity` | none | Wait admission counters `capacity`, `admitted`, `rejected`, `available` |

`wait` and `watch` never cold-start a service: with no running daemon they return `SERVICE_UNAVAILABLE`. When every admitted wait slot is busy the service returns a resumable `WAIT_OVERLOAD` whose `details` carry `cursor`, `retryAfterMs` and `capacity`; the message says no task state changed and the caller should retry from the same cursor. (The CLI prints the code and message without `details`.) Event-producing business operations commit the state change and event together. The long call to each execution does not emit an event; the event stream is a replayable outbox.

Event kinds include `task.submitted`, `task.cancel_requested`, `task.cancelled`, `task.completed`, `task.failed`, `task.retried`, `task.review_archived`, `task.accepted`, `task.rejected`, `workflow.review_rejected`, `workflow.host_concluded`, `execution.admitted`, `attempt.progress`, `attempt.activity`, `attempt.released`, `attempt.uncertain`, `execution.deadline`, `execution.shutdown_confirmed`, `execution.materials_cleaned`, `execution.issue`, `execution.issue_resolved`, `execution.connected`, `execution.unknown`, `service.issue`, `service.issue_resolved`, `message.posted`, `message.updated`, `workflow.routing_requested`, `workflow.routing_resolved`, `workflow.routing_needs_host`, `workflow.buddy_validated`, `workflow.boundary_updated`, `workflow.helper_admitted`, `workflow.helper_settled`, `workflow.helper_resumed`, `workflow.auto_continued`, `workflow.continued`, `workflow.request_approved`, `workflow.takeover`, `workflow.cancelled`, `workflow.late_turn`, `workflow.workspace_prepared`, `workflow.workspace_preparation_failed`, `workflow.scope_amended`, `workflow.workspace_resolved`, `workflow.integration_recorded`, `workflow.cleanup_planned`, `workflow.cleanup_applied`, `decision.requested`, `decision.started`, `decision.completed`, `decision.needs_host`, `decision.failed`, `decision.cancelled`, `decision.selection_rerouted` and `evaluation.*` publication events. `attempt.claimed`, `attempt.reconciled`, `attempt.lease_expired` and `worker.registered` are no longer written; they remain only in the history of a board upgraded from schema 22.

A workspace preparation failure opens an attention request and invalidates that continuation while preserving the original micro task and recovery files; `activeRequest.preparationError` carries its code (up to 100 characters) and message (up to 2000). A Git command that does not finish within 60 seconds ends as `WORKSPACE_GIT_UNKNOWN`: nothing is judged broken, the continuation stays queued and its preparation can be retried, and no attention request is opened. A Git failure is `WORKSPACE_GIT_ERROR`, and the service records with it the command line, the Git operation, the exit status and the bounded tail of standard output and standard error, with flags saying whether each was cut. For a failed preparation these details are kept in the attention request and returned by `get` with `includeAudit: true`, under `audit.requests[].payload.preparationError.details`; `preparationError` itself and a command's error output still show only the code and the message. Transient `WORKSPACE_BUSY` lock contention instead leaves the continuation queued and emits no failure boundary. Until a continuation has a prepared manifest, admission skips it with `awaiting-workspace-preparation`; other claimable tasks remain eligible.

For history paging, pass the previous page's opaque `nextCursor` as `before` (at most 512 characters); it is distinct from the integer event `cursor`. Rows are ordered by descending creation time and run ID, and the strict keyset remains stable when newer rows arrive. A nonzero `offset` cannot be combined with `before`. `rootsOnly:true` restricts the list to main micro tasks; the default remains all execution records. `query` (at most 200 characters) searches micro task text, run ID, source project, recorded Hosts and execution adapter/model; `%` and `_` are literal text. `projectId` (at most 4096 characters) matches the recorded project ID, and `hostId` (at most 256) matches original or current Host. `filter` is `all`, `active` (including reconciliation-needed), `host` or `review`. Filtering happens before the limit, `total` counts all matching records independently of the cursor, and `nextCursor:null` marks the last page. Every task read includes the [micro task metadata](evaluation.md#console-http-surface); missing original attribution remains unknown.

### Display summary

Contract 0.12.0 adds nullable `workflow.resultSummary` to micro task reads, including history rows, console snapshots and the bounded `workflow_get.task` view. It contains at most 2000 Unicode characters from this run's latest concluded turn with a persisted outcome, ordered by `turn_index`. A newer prepared/running turn does not erase that result; an empty or whitespace-only latest summary becomes null. Helper, routing and request summaries are not substituted. This projection reads existing rows and changes neither schema 11 nor the original task specification.

The console derives intent titles from the explicit run `title`, then the trimmed first nonempty line of the original task, then `未命名委派`; a Worker result summary is displayed separately. Display whitespace is normalized and headings use a 100-character Unicode-safe excerpt. The original task and recorded execution/acceptance status remain independently visible. Search matches the original task and existing history fields.

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
- The no-question form reports durable state plus a bounded `live` observation (`agentStatus`, inbox depth, `lastEvent` and up to 20 activity entries); fields it cannot observe are named in `live.unavailable` instead of being reported as zero. The view carries the task's `expectedSeconds` (a Router call carries `routerBudgetSeconds`) instead of a computed deadline.
- `live.agentStatus: "running"` means an agent driver is active, not that it is making progress; raw model reasoning is never exposed. A live call that does not get through is reported in `bridge.reason`: `transport-unreachable`, `transport-window-expired` or `reply-invalid` from the channel, a refusal code from an endpoint such as `binding-unavailable`, `queue-full` or `request-window-expired`, or the statement that the owning Worker has not attached this live run. A refusal by the run's own inquiry bridge is `bridge-refused` with its code in `bridge.error`. None of these means the run has stopped, and the question stays pending unless the bridge reported that its turn already ended. A recorded answer carries its provenance (`live-bridge` or `bridge-journal`).
- DSH declares `inquiry`. ZCode declares `observe` and cooperative `inquiry`: `live.deliveryMode: "cooperative-checkpoint"` means the question waits for the root's next checkpoint or finish attempt. Root-native tool evidence and signed receipts bind the answer; settlement makes unanswered questions unavailable, without injected native input. A journal write refusal is also recorded as unavailable (`journal-unavailable`). `command`, `codex` and Claude P1 mount no inquiry bridge; task status and any recorded activity remain readable. `bridge.canObserve` and `bridge.canAsk` distinguish observation from correlated questions.

## Evaluation, routing and console

`console` opens the fixed private loopback URL without login by default. If Settings requires login, it opens a single-use 10-minute entry; sessions have no 30-day expiry and can be revoked in Settings. The CLI launches the browser by default; `browser:false` suppresses launch, while `wait:true` keeps the CLI attached to the exact console instance and requests an identity-fenced console close on Ctrl-C. These booleans are local to open and never reach the service. Status/close and the wait observer never cold-start a daemon. `console-snapshot` reads authoritative data without a browser session or model invocation. See [console.md](console.md) for browser sessions and [evaluation.md](evaluation.md) for policy and evidence semantics.

| Command | Parameters | Behavior |
| --- | --- | --- |
| `console` | `action`: `open` (default), `status`, `close`; CLI-only `browser` (default true) and `wait` (default false) for open; optional `expectedConsoleId` for close | Open returns the fixed `url` and `expiresAt:null` by default, or a one-use URL with its expiry when `requireLogin:true`, plus public `consoleId`, `running`, `alreadyRunning`, asset metadata and CLI `browserOpened`. Status has no entry URL. Fenced close refuses to close a replacement instance |
| `console-snapshot` | none | `eventCursor`, Router `configuration`, bounded available buddies and the ordered Router list, `modelServiceConcurrency`, buddy `preferences`, `modelServiceNotes`, latest 50 decisions and latest 100 tasks; `unavailableBuddyCount` is separate |
| `model-catalog-refresh` | optional `requestId` (≤128) | Explicit native metadata discovery; one monotone `observationId` per request publishes per-harness facts and preserves existing human state. Each harness reports `status: complete\|unknown` with a reason and `accountStatus: confirmed\|unknown\|not-applicable`, plus `appliedHarnesses` and `staleHarnesses`; only a complete reading whose account status is `confirmed` or `not-applicable` is trusted, a model missing from it becomes pending and is confirmed absent only by a further trusted reading at least one hour later, and a late older response is skipped. New buddies are disabled and enabled state is never reset. No model call |
| `buddy-list` | optional `includeUnavailable`, `limit` (1–200, default 100), `after` buddy cursor, `query` (≤200 characters, at most 8 terms), `harness` | Bounded active/unavailable buddy page with `preferences`, `modelServiceNotes`, per-model-service `modelServiceConcurrency` (`active` occupancy is read-only observation) and nextCursor; unavailable entries are hidden unless requested |
| `user-policy-publish` | `commandId`, optional `buddySettings`, `preferenceChanges`, `modelServiceNoteChanges`, `modelServiceConcurrency`, `configuration` | Authenticated console only. Lists contain at most 200 field patches; model service notes at most 4000 characters; `modelServiceConcurrency` patches are `{harness, provider, model, limit}` entries that reject `active` and other derived fields. `preferenceChanges` patch one buddy's preference `{buddyId, mode, reason}`; a null mode deletes that stored row, an empty model service note clears it, and omitted fields remain unchanged. Program model/catalog fields cannot be written |
| `selection-request` | `requestId`, `task` (≤8192 UTF-8 bytes), optional `requiredCapabilities` (the hard budget comes from the routing budget preset in the Router settings) | Durable bounded routing request; returns `decision`, `decisionId`, `status`, `runId` (nullable) and `duplicate` |
| `selection-get` | `decisionId`, optional `includeAudit` (default `false`) | Compact routing summary with frozen `selectedBuddy`, `decisionModel`, `evidence`, `usage`, `budget`, `nativeIdentity`, `stopEvidence`, `inputVerification`, `routingMode`; audit mode includes persisted input/output and hash |
| `selection-list` | optional `limit` (1–100, default 20), positive integer `before` (≤2^63−1) | Read-only compact history, filtered before pagination; returns `decisions`, `nextCursor` and matching `total`; no model invocation |

A settings publication is identified by its `commandId` and carries no revision: of two saves of the same field the later one stands, the same command sent again returns its receipt without rewriting what was saved since, the same ID with different content is refused as a command identity conflict, and `UNAUTHORIZED` reports missing authority. Explicit null and omission are different inputs.

Buddy fields are `buddyId`, `label`, `harness`, `provider`, `model`, `effort`, `available`, `enabled`, `capabilities`, `contextWindow`, `description`, `source`, optional `unavailableReason`, and `catalogStatus` (`available`, `pending` or `unavailable`) with `pendingSince` while pending. `catalogStatus` is the catalog's judgment of the model and is separate from `available`: a pending model stays available, and a buddy excluded by harness health or a retired effort keeps `catalogStatus: available` with its own `unavailableReason`. Identity fields cannot be changed under the same buddy ID, and availability must be backed by recorded discovery. A preference is `{buddyId, mode, reason}` with `mode` `prefer`, `pin` or `exclude`; a buddy without one has no entry. Configuration accepts ordered unique `routerBuddyIds`, integer `routerRetryIntervalSeconds` (1–2147483647, default 600), `defaultRoutingMode` (`fast` or `review`) and optional `routingBudget` (`brief`, `standard` or `deep`; the retired `quick` is refused); the snapshot returns `eventCursor`, the effective budget preset and the read-only `routingBudgetLimits`.

Decision statuses are `queued`, `running`, `completed`, `needs-host`, `failed` and `cancelled`. A nullable `runId` means no model was started. `routingMode` reports the frozen mode; current views have no automatic fallback fields. No-answer advances to the next frozen Router only after confirmed stop; valid abstention, changed circumstances, cancellation and unknown stop do not advance. Exhausting the list stops at the Host boundary with `routerTrials`, frozen facts/candidates, known retry time and executable owner-bound command templates. Timeout/budget failures count toward `routingHealth.failureCount` and its genuine-failure streak while retaining their diagnostic counters. Internal attempts are cancelled by `execution-cancel`, never acknowledged as business work.

## Defaults and bounds

| Setting | Default | Bounds |
| --- | --- | --- |
| Parameter document (positional, `--params-file` or `-`) | `{}` | one JSON object; UTF-8 (BOM accepted); at most 8 MiB; mutually exclusive sources |
| Expected duration `expectedSeconds` | 1800 s | 1–86400 s; not a deadline, nothing is stopped when it passes |
| CLI `await` wait window | 86400 s | 1–86400 s |
| `wait` / `watch` timeout | 30000 ms | 0–30000 ms |
| `message` / `inquire` answer wait (`waitMs`) | 0 ms | 0–30000 ms |
| `inquire` bridge request `timeoutMs` | 1500 ms | 100–5000 ms |
| Event page `limit` | 100 | 1–200 |
| `list` page `limit` | 20 | 1–100 |
| `messages` page `limit` | 50 | 1–200 |
| Task text | — | 1 MiB |
| Question / answer | — | 4000 UTF-8 bytes each |
| Inquiries retained per task | — | 32 |
| Acknowledgment note / evidence | — | 10000 bytes / 32 entries |
| Artifacts per result / location | — | 100 / absolute path ≤4096 characters |
| Micro task continuation input | — | 64 KiB |
| Helpers per Host decision | — | 8 |
| Selection request task text | — | 8192 UTF-8 bytes |
| Routing budget preset | `standard` | `brief` 60 s/8 calls, `standard` 300 s/24 calls, `deep` 600 s/64 calls for review routing (the recorded read-byte limits 128 KiB/512 KiB/2 MiB apply only where an adapter can observe read bytes, which neither current adapter can); fast routing has no tool-call budget and a fixed 60 s deadline; the Router call's `routerBudgetSeconds` is the preset timeout plus a 10 s Worker shutdown margin |
| Compact micro task view | 5 requests / 10 turns / 32 children / 32 artifacts | `counts`/`truncated` describe omissions |
| Cleanup plan expiry | 900 s | fixed; an expired plan authorizes nothing |
| Concurrent attempts `HMB_MAX_CONCURRENT` | 8 | 1–32; one machine-wide ceiling shared by routing and execution |
| Concurrent attempts of a model service | 2 | 1–32 per exact harness/provider/model; user-set on the model card, hot at the next admission |
| Wait capacity `HMB_WAIT_CAPACITY` | 32 | 1–48; leaves native RPC callbacks available for control operations |

## Error codes

| Code | Meaning |
| --- | --- |
| `INVALID_ARGUMENT` | A parameter is missing, unknown or out of bounds; also a malformed, non-object, non-UTF-8 or oversized parameter document |
| `UNKNOWN_METHOD` | The CLI method name is unknown; the envelope's `didYouMean` lists the closest candidates and the process exits 2 |
| `NOT_FOUND` | The selector matches no task, worker, message or artifact |
| `SUBMISSION_REPLAY_UNAVAILABLE` | The `requestId` belongs to a submission recorded before schema 21, whose fingerprint the upgrade cleared; submit with a new `requestId` |
| `NOT_READY` | No persisted result/attempt exists yet (for example `result` before completion) |
| `CONFLICT` | The same identity with changed input: changed submit input, changed inquiry text, a different repeated acknowledgement, a closed boundary or an already-published command |
| `REVISION_CONFLICT` | A stale revision tried to mutate state |
| `STALE_GENERATION` | A replaced attempt or owner generation tried to act |
| `UNAUTHORIZED` | The private token, control capability or worker receipt was rejected, or a continuation target is not an owned descendant |
| `ANCESTOR_TERMINAL` | A cancelled, accepted or concluded ancestor blocks descendant continuation |
| `SHUTDOWN_UNCONFIRMED` | Retry, replacement or a continuation was refused because the previous stop is not verified |
| `ILLEGAL_TRANSITION` | The state machine refuses that task/attempt transition |
| `NOT_REGISTERED` / `ALREADY_RUNNING` | The worker id is unknown / already supervised |
| `ATTEMPT_FINISHED` | Progress or a mutation arrived after the attempt finished |
| `HARNESS_UNAVAILABLE` / `UNSUPPORTED_HARNESS` | The harness cannot run here / is not `dsh`, `zcode`, `codex` or `claude` |
| `MICRO_TASK_REQUIRED` | Coding work or a micro task record was addressed through the advanced execution path; use the micro task command |
| `BUDDY_REQUIRED` | A routing boundary must be resolved with a complete buddy or a reroute |
| `BUDDY_CONFLICT` | A supplied buddy conflicts with the micro task's locked original buddy fields |
| `BUDDY_UNAVAILABLE` / `INVALID_BUDDY` | The catalog does not offer the tuple, after at most one bounded re-read of that harness per 180 seconds; details carry `reason: "catalog-unavailable"`, `remedy` and, when a trusted reading exists, `catalogReadAt`. On `continue`, `reason: "not-enabled"` with `enabled: false` means the catalog admits the tuple and the user has not enabled it / the installed tuple changed and must be chosen again |
| `CATALOG_INVALID` / `CATALOG_UNAVAILABLE` | Native model discovery returned unusable data / was not performed |
| `CATALOG_LIMIT` | The current native buddy set exceeds 200 buddies; nothing was silently dropped |
| `INVALID_WORKSPACE` | A workspace path or scope is absolute, escapes the checkout, names `.git` or does not exist |
| `WORKSPACE_CHANGED` / `WORKSPACE_CONFLICT` | A compare-and-swap found the site changed since it was recorded / the recorded conflict does not match the site |

| `INTEGRATION_UNVERIFIED` | The artifact was not present in the claimed target; no integration record was created |
| `PLAN_EXPIRED` | The cleanup plan expired; request a fresh plan before deleting anything |
| `STATE_INTEGRITY` | A micro-task run lacks its required recorded scope; preserve the state and repair a verified offline copy instead of inventing authority |

| `ARTIFACT_MISSING` / `ARTIFACT_MISMATCH` | A claimed artifact failed validation and no completed result was published |
| `TOO_MANY_INQUIRIES` | The 32-inquiry retention limit for the task was reached |
| `WAIT_OVERLOAD` | Every admitted wait slot is busy; the service's `details.cursor` says where to resume |
| `WAIT_ABANDONED` | The wait was interrupted; the task was not cancelled |
| `SERVICE_UNAVAILABLE` | The service could not be reached, or no service runs for `wait`/`watch` |
| `SERVICE_START_FAILED` / `SERVICE_START_TIMEOUT` | The daemon failed to start or become ready |
| `RESULT_NOT_AVAILABLE` / `RESULT_MISSING` / `RESULT_MISMATCH` / `RECOVERY_MISMATCH` | `await` could not deliver the promised result; the execution status is still reported honestly |
| `RUNTIME_INSTALL_FAILED` / `RUNTIME_INSTALL_TIMEOUT` / `UV_NOT_FOUND` / `RUNTIME_NOT_READY` | Stable-runtime installation or pinning failed; see [operations.md](operations.md#runtime-lifecycle) |
| `INSECURE_CONTROL_FILE` | A `controlFile` is not a private regular file owned by the current user with mode `0600` |
| `FORBIDDEN` | An attempt-scoped credential tried to use Host authority |
| `MESSAGE_TOO_LARGE` / `INVALID_RESPONSE` | The request or the service response exceeded the transport bound / was malformed |
| `INTERNAL_ERROR` | Unexpected service failure; set `HMB_DEBUG` for detail |

| `CONSOLE_SESSION_EXPIRED` / `CONSOLE_ENTRY_EXPIRED` | The browser session is unavailable / the one-use entry is used or expired; reopen through the CLI |
| `CONSOLE_LIMIT` | The bounded collection of pending entries or active browser sessions is full |

## `await` envelope

`await` builds one envelope: `runId`, `requestId`, `status`, `workflowState`, `outcome`, `ok`, `resultAvailable`, `resultDelivered`, `shutdownConfirmed`, `shutdown`, `acceptedAt`, `revision`, `createdAt`, `updatedAt`, `cwd`, `logPaths`, `waitedSeconds`, `waitSeconds`, `maxWaitSeconds`, `timedOut`, `reconnects`, `result`, `recovery`, `error` and a `note`; the default [brief view](#output-views) prints it without the constant and null fields. A wait slices its window into bounded 30-second service waits with at most three reconnects, uses only read operations, and never cold-starts a service for `wait`/`watch`.

`outcome` is the terminal task status (`completed`, `failed`, `cancelled`, `reconciliation-needed`), `waiting-host` for a micro task at a decision boundary, `wait-timeout` when this call's window ended first, `completed-no-result` when a completed run has no deliverable result, or `unavailable` when the service could not be reached while waiting. A task that reaches its own execution deadline ends as a terminal `failed` with the adapter `status: "timeout"`. The `recovery` block names real commands with the existing run ID (`hey-my-buddy status`, `hey-my-buddy await`, `hey-my-buddy result`, `hey-my-buddy get`, `hey-my-buddy cancel`). `ok` is true only when the outcome is `completed` **and** this envelope carries the result payload **and** no error was recorded.

A micro task Host boundary additionally reports `microTaskComplete: false`, the current `request`, compact turn/artifact context, a compact result view and `nextCommands` (approve/decline for assistance or ordinary attention; routing attention offers complete `buddy` or `reroute` continuations); the generated commands use the saved `controlFile` and name the exact revision. Use `get` to inspect all pending requests. Micro task views expose `workflowState` and `workflowShutdown`. The wait envelope carries the aggregate shutdown under `shutdown`, and its `shutdownConfirmed` requires both `selfConfirmed` and `descendantsConfirmed`. `unconfirmedCount` covers the complete owned lineage including the root when applicable; the ID list is bounded and may be truncated. Logical `workflowState: "cancelled"` does not assert that descendants stopped. The wait continues on committed stop evidence, even when the root task is already terminal, and returns `wait-timeout` with unconfirmed shutdown if its own window expires first. A missing PID does not satisfy this evidence requirement.

### 0.15.1 macro task presentation

`submit.macroTask` accepts optional `description`, a nonempty string of at most 300 Unicode characters when present. It is immutable display metadata, excluded from execution/selection and included in submission replay identity. The skill requires a ≤30-character intent title on new micro tasks, while the optional 200-character API limit remains unchanged. Macro task read shapes add `description`, `counts.accepted` (accepted roots), and timeline row `taskSummary`; see [macro tasks](macro-tasks.md). The console-only `macro_task_stop` is not a Host CLI command; ordinary Host `cancel` and its control capability remain unchanged.

## 0.16.0 maintenance commands

`backup-preflight '{}'` is a CLI-local read of the new package's evidence policy. It starts no service and writes no state or runtime data. Its report is `{policy, ok, needsAttention, copied, skipped, rejected}`; each outcome contains `{count, paths, entries:[{path,reason}]}` with at most 20 samples, while counts include every examined item. Unknown directory trees count as one and are not traversed. `ok` means no refused entry, while `needsAttention` also includes skipped entries; `ok:false` names the entries an installation would refuse, and skipped entries are left where they are. `BACKUP_PREFLIGHT_FAILED` stops preparation/cutover and gives paths. A Worker credential is refused.

`backup '{}'` invokes service-owned online backup and returns `{path, verified, schema, bytes, fileCount, skippedAttemptEntries, durationSeconds}`. It has no caller-supplied destination. `install '{}'` is local to a package launcher: it places the shared skill in `~/.agents/skills/hey-my-buddy` unless the same version and source commit is present, links `~/.claude/skills/hey-my-buddy`, runs the installed skill's `upgrade` when a service is running and returns `{skill: {path, version, contract, placement}, claude: {path, status}, service}` plus `legacyPlugins`/`next` when a Codex plugin cache remains; a foreign skill or Claude Code target is `SKILL_TARGET_CONFLICT`, a Claude Code link that cannot be created is `CLAUDE_LINK_FAILED` (both before any upgrade) and a Worker credential is `UNAUTHORIZED`. `paths '{}'` is local and read-only: it starts no service and returns where things live — `skill {dir, installed, version, launcher}`, `claude {path, status}` (`linked`, `missing` or `conflict`), `invokedFrom`, `data {stateDir, board, attempts, workspaces, backup, serviceLog}` and `runtime {root, active}`. `upgrade '{}'` is local to the new package launcher; it accepts no Worker credential or implicit cancellation. A completed cutover returns `{upgraded, backup, archive, verification, previousRuntime, rollback, runtimePruning, legacySkillCleanup}`; failure returns a nonzero CLI status and either verified rollback evidence or a retained recovery journal. Running `upgrade` again resumes an interrupted journal. Ordinary commands cannot mutate while the upgrade fence is active (`UPGRADE_IN_PROGRESS`). Busy work is `UPGRADE_NOT_IDLE`.

`storage plan '{}'` and `storage apply '{"planId":"stg-…","commandId":"…","confirm":true}'` map to named `storage_plan` and `storage_apply` operations. [Operations](operations.md#storage-wire-shapes) owns the exact plan/result shapes, protection rules and 15-minute expiry. Reuse the same commandId after an unknown reply or interrupted deletion; changing its plan conflicts. Neither command stops a process. Browser access adds only these two operations to the existing whitelist.

## Local harness commands (0.19.0)

Every CLI JSON envelope carries `contractVersion`. A reply to a request that names a Host (`hostId`, also supplied by a control file) or one of its micro tasks (`runId`, `taskId` or `requestId`) also carries `waitingForHost` while that Host has micro tasks waiting for it: `{"host": n, "review": m}` with only the nonzero counts, each equal to the `total` of `list` with that `hostId`, that `filter` and otherwise default parameters. Requests made with an attempt-scoped credential, as a Lead makes them, never carry it. `harnesses` returns the connector reports and the recorded `harnesses` health; `harnesses '{"refresh":true,"harness":"codex"}'` performs an explicit bounded rescan (omit `harness` for all). `harness set codex /absolute/path/to/codex` maps to `harness_set`; `harness set codex --auto` removes the override. The named operation accepts `{harness,path}`, with `path:null` restoring automatic detection; the later write wins. Manual paths require Host or authenticated-console authority. [Harnesses](harnesses.md) owns status, retry and diagnostic semantics.

`harness_prepare` is an internal Worker operation authenticated by `workerId`, `attemptId`, `generation`, `nonce` and `commandId`; `retry` or `failedOnly` additionally binds `healthRevision`. It returns the selected health record and records attempt provenance; one proven pre-model retry is permitted. A Worker cannot use this operation to change a manual path.

## Review eligibility

`harnesses` exposes local read-only eligibility and system-sandbox facts without a model call. Eligibility is not a paid certificate; runtime effective-policy, tool evidence, budget, frozen-input and actual stop checks remain mandatory.

## Worker session history

`worker-sessions` maps to the named `worker_sessions` operation. It accepts `harness: codex` (the default) and `action: list` (the default); anything else is rejected. `list` reads only board-recorded native identities and reports `sessions`, `count` and `nativeAccess:false`, labelling Codex historical user-store and private micro task threads; actual archive or deletion requires separate user confirmation and a native Codex action. A `dsh` request is `UNSUPPORTED`: DSH sessions are execution-private and the grouped-session listing and archiving were removed with the grouping feature ([ADR-021](../decisions/021-router-buddy-planes-and-routing-evidence.md)). Worker credentials are denied. [Operations](operations.md#worker-session-history) owns the read boundary and the upgrade boundary.
