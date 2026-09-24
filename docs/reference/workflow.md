# Governed goal lifecycle

Use this lifecycle when a repository or coding goal needs explicit workspace ownership, model routing, assistance, continuation and final acceptance. The Host can implement part of the goal itself. All executions use the existing Python blackboard, C-Two operations, ordinary Worker capacity and durable completion receipts; a governed run *is* the ordinary logical task (`runId` = `taskId`), never a second job engine.

The CLI names are short (`submit`, `get`, `decide`, `continue`, `takeover`, `cancel`, `acknowledge`, `suggest`, `await`); each maps to one named C-Two operation (`workflow_submit`, `workflow_get`, `workflow_decide`, `workflow_continue`, `workflow_takeover`, `workflow_cancel`, `workflow_acknowledge`, `workflow_suggest`). Keep the operation names distinct when describing the contract: the CLI spelling is a convenience, and the C-Two method name is the stable operation identity. Ordinary execution records for the `command`, `external` and internal `decision` infrastructure use the separate `execution-*` CLI names.

## Submit and keep ownership

The ordinary task specification stays flat. Every submission requires an `executionWorkspace` object with an explicit `kind: existing|worktree`; this is separate from `workspace`, which only controls DSH session grouping. Choose a stable request ID, a Host ID representing this controller and the complete packet before submitting.

```sh
"$BUDDY" submit '{"requestId":"state-kernel-1","hostId":"codex-state-kernel","task":"Implement the agreed state transition in src/state.ts, add regression tests in tests/state.test.ts and run the package tests. Preserve unrelated files. Report completed only after checks.","cwd":"/abs/repo","timeoutSeconds":28800,"workspace":false,"executionWorkspace":{"kind":"worktree","cwd":"/abs/repo","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["src/state.ts","tests/state.test.ts"],"integrator":"codex-state-kernel"}}'
"$BUDDY" await '{"runId":"<returned-runId>","waitSeconds":28800}'
"$BUDDY" get '{"runId":"<returned-runId>"}'
```

Submit fields are `requestId`, `hostId`, the ordinary spec (`task`, `cwd`, `adapter`, `argv`, `model`, `provider`, `effort`, `timeoutSeconds`, `workspace`, `owner`, `requiredCapabilities`, `exclusiveResources`), an optional nested `spec` object carrying the same ordinary fields, `executionWorkspace`, and a private `submissionToken`. Unknown fields are rejected with `INVALID_ARGUMENT`.

Save `runId` and `controlFile` from submission. The control file is an owner-private `0600` file under `<state>/controls/` bound to that run and owner generation; later control commands require this exact file, or the explicit `hostId` + `ownerGeneration` + `controlToken` triple. The CLI must not adopt another Host's latest file implicitly, and an attempt-scoped agent credential can never present a control file. A Host name is attribution and never enough authority. Do not copy control tokens into model prompts, reports or helper tasks.

Replay the exact submission with the same request ID after an uncertain response. Recovery first looks up the recorded run and compares the ordinary spec fingerprint and the request fingerprint (spec plus execution workspace plus Host); identical input returns the same run with `duplicate: true`, while changed workspace, input or Host under that ID is a `CONFLICT`. Control on replay is returned only for owner generation 1 and only to a caller that still presents the same `submissionToken`; otherwise the reply reports `controlAvailable: false`. A successful RPC is admission evidence only. `get` is compact by default; use `includeAudit: true` only when reviewing exact turn inputs, results, decisions and artifact manifests.

## Configuration and routing

Routing is decided once per new execution configuration and recorded separately from the immutable original request. The four configuration fields are `adapter`, `provider`, `model` and `effort`; the coding adapters are `dsh` and `zcode`.

- A **complete explicit quadruple** with a coding adapter is validated against the installed native catalog before any workspace preparation, then dispatched directly: no routing-model call and no decision task is created. Failure is reported to the caller as `INVALID_ARGUMENT` (including `legalEfforts` when the effort is unsupported), `UNSUPPORTED_ADAPTER`, `ADAPTER_UNAVAILABLE` or `CONFIGURATION_UNAVAILABLE`. A later claim re-validates the tuple once outside write transactions; if the installed native route changed in the meantime, the run opens a routing boundary with `INVALID_CONFIGURATION` instead of dispatching a stale route.
- A **partial** quadruple is a hard filter. Every supplied field must equal the published profile's field exactly, in Python rather than by model judgment, and the configured decision profile fills the remaining choice from the bounded current evaluation table. Candidates must be enabled and available, not `exclude`d, satisfy the goal's `requiredCapabilities`, and be complete coding tuples; any `pin` preferences restrict the set. The table slice is bounded (at most 200 profiles, 512 cards/evidence entries, 256 preferences and 128 KiB of input) and over-bound input becomes an explicit `needs-host` reason rather than silent truncation.
- An **unspecified** configuration routes the same way using the whole bounded table and the fixed decision profile. If the complete task exceeds the selector's 8,192-byte input bound, routing records `needs-host` and a `taskReference` (run ID, byte count and hash) without a model call. The original goal is retained; a complete Host configuration resumes the same goal. The fixed selector is the profile published as `configuration.decisionProfileId`; it must be an available, enabled DSH profile with a complete provider/model/effort identity. The service never guesses one, never substitutes a DSH default and never selects the selector recursively.
- `command` and `external` do not use the coding catalog: the constraints you supply pass through unchanged and no selector runs. They are also available through the ordinary `execution-*` records, which is the documented path for non-coding work; coding adapters are refused there.

Selection runs as one durable internal `decision` task on the ordinary Worker queue with `workspace: false` and `requiredCapabilities: ["decision"]`, so it consumes normal capacity and owns its helper process. No selector, no legal candidate or a failed selection produces a **durable routing attention** boundary rather than a failed or completed goal:

- The route row becomes `needs-host`, the run becomes `awaiting-host` with a new `activeRequest`, and the task queue reason becomes `awaiting-host`. The request has `kind: "attention"`, `routing: true` and payload `source: "routing"`, the decision ID, a bounded summary, and the explicit needed work: continue with a complete configuration, or fix the selector/catalog and continue with `reroute:true`.
- Events are `workflow.routing_requested`, `workflow.routing_resolved`, `workflow.routing_needs_host` and `workflow.configuration_validated`; `workflow_get.routing` exposes `status`, `decisionId`, `taskId`, `attemptId`, `generation`, `selectedProfile`, `tableRevision`, `configurationRevision`, `reason` and `constraints`. Statuses are `explicit` (no routing), the live decision status while pending, `completed` for a resolved route, `needs-host` and `fenced`.
- `decide` refuses a routing request with `CONFIGURATION_REQUIRED`: a routing boundary is resolved with `continue` using either a complete `configuration` or `reroute: true`, not with an assistance approval.
- `continue` with a complete `configuration` adopts it on the same run: the execution configuration and its revision advance, `current_routing_id` clears, and the task adapter becomes the chosen one. The configuration must preserve the original goal's hard constraints exactly; a conflicting field is `CONFIGURATION_CONFLICT` and requires a new goal, not a silent change. Passing `configuration` and `reroute` together is `INVALID_ARGUMENT`.
- `continue` with `reroute: true` starts a new routing decision on the same run. Original constraints still apply: the immutable `goal_json` is re-read and its partial constraints are re-derived, so a reroute can fill missing fields but can never relax what the submitter fixed by hand. Old route rows keep their `needs-host` or `fenced` state for audit.
- Cancellation and takeover fence route work. A takeover bumps the owner generation and fences every pending route below the run, opening an attention boundary when selection was interrupted so the new owner must wait for confirmed stop before choosing a configuration or rerouting. Cancellation fences routes, cancels their decision tasks and revokes continuation authority. A replacement continuation requires proven stop of any owned routing attempt (`SHUTDOWN_UNCONFIRMED` with `routingTaskId`) and is refused while a selection is still `queued` or `running` (`CONFLICT`).

The immutable parts stay immutable: `goal_json` and `goal_fingerprint` (the original task, cwd, constraints and Host) and the stored ordinary `spec_json`/`input_fingerprint` never change. The resolved parts are the separate `execution_configuration_json`, `execution_configuration_revision` and `validated_configuration_revision` columns plus the `workflow_routes` link and the decision's own `table_revision` and `configuration_revision`. `get` exposes them as `executionConfiguration`, `executionConfigurationRevision` and the `routing` object.

## Turns and structured results

Each actual execution receives an independent attempt/generation and a turn ID. A governed turn ends only through the harness-specific root terminal tool: DSH registers `buddy_finish_turn` on the correlated root agent and records native tool/turn/awaited-flush evidence, while ZCode injects a session-private signed MCP tool and records session-close evidence. Plain final text, an internal subagent result or a failed enclosing tool call cannot substitute for the recorded disposition.

The tool arguments are `disposition` (`completed`, `assistance` or `attention`), `summary`, `remaining`, `decisions`, `artifacts` and `request`. A completed result requires `request: null`; assistance or attention requires an object with nonblank `summary`, `attempted`, `neededWork` and `acceptance`, plus `expectedArtifacts` and an optional `suggestedProfileId`. Input is bounded to 256 KiB, the serialized outcome to 64 KiB, arrays to 32 entries, and text fields to 8,000 UTF-8 bytes.

The Worker imports the result only with the correct task/attempt/turn identity, a successful runner exit and confirmed shutdown. Files are sealed after shutdown; a reported model path is not by itself a verified artifact. After the initial turn, DSH reports `resumeMode: "reconstructed-new-session"`. ZCode selects `native-session` only after a proven concluded turn with the same frozen execution configuration; the runner then verifies a private binding of the exact `taskId`, `sessionId`, checkout `cwd` and provider/model/effort configuration before resuming. A missing or mismatched native-session binding fails explicitly. When there is no proven previous session or the configuration changed, ZCode reports `reconstructed-new-session` and creates a new root session from the fixed continuation input. Keep requested configuration, native settings readback and actual served-model evidence distinct; `observed` stays null when no served identity was reported.

## Host boundaries and requests

`await` returns `outcome: "waiting-host"` when the goal reaches a decision boundary. `get` exposes:

| Field | Meaning |
| --- | --- |
| `activeRequest` | Full current open boundary accepted by `decide`, or `null`; use the top-level current `revision` for the decision |
| `pendingRequests` | Bounded open-request queue including the current active request; at most 5 compact entries |
| `counts.openRequests` | Complete open-request count, including the active request and entries omitted from the compact queue |
| `truncated.pendingRequests` | Number of open-request entries omitted from `pendingRequests` |
| `requests[].routing` | True when this request is a routing boundary, which `decide` refuses |
| `proxy`, `origin` on a request | Immediate source `{runId, requestId}` and original requesting leaf `{runId, requestId}` for nested attention |
| `shutdown` | `selfConfirmed`, `descendantsConfirmed`, full `unconfirmedCount`, up to 32 `unconfirmedRunIds`, and boolean `truncated` |

Do not add `activeRequest` to `counts.openRequests`: it is already included. Request kinds are `assistance` and `attention` from a turn, `helper-attention` for a yielded helper, and `helper-report` when authorized helpers settled. Request states are `open`, `approved`, `declined`, `superseded` (a continuation bypassed it) and `cancelled`. Compact reads retain at most 5 recent request summaries, 10 turns, 32 children and 32 artifacts; `counts` and `truncated` describe the complete collections and omitted entries. `includeAudit: true` adds full persisted request, turn and continuation records, including the immutable original spec.

Approve concrete helper packets with explicit configurations and workspace intents. Use the revision from the latest `get`, the request ID from `activeRequest`, and one stable command ID.

```sh
"$BUDDY" decide '{"runId":"<parent>","requestId":"<activeRequest.requestId>","commandId":"approve-tests-1","expectedRevision":7,"controlFile":"<parent-controlFile>","decision":"approve","reason":"The parent needs independent concurrency coverage before integration.","autoContinue":true,"helpers":[{"requestId":"state-tests-helper-1","task":"Add concurrency tests for the fixed state implementation. Modify only tests/state.test.ts and run the focused checks. Return the resulting artifact.","cwd":"/abs/repo","adapter":"dsh","provider":"deepseek-official","model":"deepseek-flash","effort":"max","workspace":false,"timeoutSeconds":28800,"executionWorkspace":{"kind":"worktree","cwd":"/abs/repo","access":"write","base":{"kind":"commit","ref":"<parent-output-commit>"},"includeUntracked":[],"writeScope":["tests/state.test.ts"],"integrator":"<parent>"}}]}'
"$BUDDY" await '{"runId":"<parent>","waitSeconds":28800}'
```

`decide` accepts `approve` or `decline`, a bounded `reason`, up to eight explicit `helpers`, and `autoContinue` (default `true`). Declining does not fail the goal. Approval with `autoContinue: true` records one one-use automatic continuation tied to the request and revision; `false` returns the result to Host control before the next parent turn. A helper that needs another decision is surfaced while other helpers may still be running, and an unrelated helper finishing does not clear unanswered requests or start the parent prematurely.

Nested helper attention is forwarded through durable requests on each owned ancestor. A forwarded request's `proxy: {runId, requestId}` names its immediate source; `origin: {runId, requestId}` identifies the original requesting run and request, and `childTaskId` names the direct child at that boundary. Use the root Host's control with the root run ID, its current `activeRequest.requestId` and current revision. The service validates the owned proxy chain and applies that decision to the requesting leaf. The root token cannot authorize a direct mutation of a different run ID, and a stale or superseded boundary cannot be reopened by a delayed answer.

`continue` separates the owning goal from its target. The root owner may pass `targetRunId` to continue an **authorized descendant** without taking it over: the target must be the owner run itself or a direct/recursive child in the owned `workflow_children` graph, or the call is `UNAUTHORIZED`. Additional checks apply the owner's revision, the target's revision, the immutable goal constraints, the open lineage (`ANCESTOR_TERMINAL`) and routing stop evidence. The continuation row and requeue target the descendant, while the response stays the owner's compact view with the resolved `targetRunId` and the command receipt is stored under the owner.

## Manual continuation and takeover

Manual continuation records new input on the same logical goal and invalidates every older unconsumed continuation in `recorded` or `queued` state, including both manual inputs and automatic triggers. With active helpers, `helperPolicy` must be supplied explicitly as `"keep"` or `"cancel"` so a continuation never silently keeps or kills running work; `cancel` must still wait for actual shutdown before conflicting writes resume.

```sh
"$BUDDY" continue '{"runId":"<parent>","commandId":"continue-1","expectedRevision":9,"controlFile":"<controlFile>","input":"Integrate the pinned helper output and rerun the state regression checks.","helperPolicy":"keep"}'
"$BUDDY" takeover '{"runId":"<parent>","commandId":"takeover-1","expectedOwnerGeneration":1,"newHostId":"codex-follow-up","controlFile":"<old-controlFile>"}'
```

Continue and retry have different meanings. Continuation consumes new input after a stopped execution; `execution-retry` repeats a failed attempt record. Control mutations are fenced by owner generation, expected revision and idempotent command receipts: a stale generation is rejected with `STALE_GENERATION`, a stale revision with `REVISION_CONFLICT`, and replaying the same command ID with changed input is a `CONFLICT`.

Takeover rotates the control capability and owner generation without restarting the current execution. Keep the new returned `controlFile`; late operations by the old generation are rejected. An authenticated console user can make an explicit decision or takeover through the private console, whose server attaches its own session authority; browser JSON flags cannot claim user authority.

## Workspace and artifact rules

Every governed workspace names `kind` (`existing` or `worktree`), source `cwd`, `access` (`read` or `write`), `base` (`working-tree` or `commit` with `ref`), `includeUntracked`, `writeScope` and `integrator`. Paths in the latter two lists are relative to the checkout root; `.` explicitly allows the whole checkout. A write workspace needs a nonempty scope. This is an ownership and output-verification contract, not an OS sandbox.

| Arrangement | Fixed input and ownership |
| --- | --- |
| `worktree`, `base.kind: commit` with `ref` | Resolve the ref once and create an isolated detached worktree from that commit |
| `worktree`, `base.kind: working-tree` | Capture tracked staged/unstaged content plus explicitly included untracked files in a private index; preserve the source HEAD, index and files |
| `existing` | Explicitly use the source checkout and reserve its actual checkout identity, including sibling cwd paths |
| `access: read` | Require the input to remain unchanged; multiple readers can share one unchanged snapshot |

The workspace module exposes `inspect`, `prepare`, `verify` and `seal` and raises `BoardError`; all of it runs outside SQLite transactions. A prepared manifest records `version`, `workspaceId`, `kind`, `path`, `checkoutRoot`, `checkoutId`, `repositoryId`, `access`, `baseCommit`, `inputCommit`, `inputTree`, `writeScope`, `integrator`, `targetRef`, `snapshot` and `manifestSha256`. The snapshot records included and excluded untracked paths and hashes of staged/unstaged input changes. Output sealing preserves a fixed commit, tree, diff, changed paths and output snapshot hash. In the raw seal, `manifestSha256` refers to its input manifest while the output snapshot hash binds that output; `get.workspace.manifestSha256` is the run's current effective manifest digest.

Worktree isolation does not isolate shared repository metadata, external services or databases. Declare shared resources and keep one integrator for a common target. Independent writers use separate worktrees. Sequential transfer of an existing checkout requires stopped-parent evidence and an explicit Host allocation, and the service refuses `SHUTDOWN_UNCONFIRMED` when the parent may still run. Attempt slots and logical workspace reservations have different lifetimes; a yielded parent can release execution capacity while retaining its checkout.

A continuation reuses its currently allocated physical checkout, including an already-created worktree. It prepares a new fixed input under the continuation identity while keeping the actual checkout, execution cwd and reservation aligned. Its baseline comes from the most recently published sealed output on that checkout, produced by the run itself or an authorized helper. Selection follows durable artifact publication order and the output's bound execution manifest, so equal timestamps or clock rollback cannot choose an older seal. Separate helper worktrees supply pinned outputs for explicit integration; they do not silently replace the parent's checkout. The original goal, submission specification, workspace intent and previous turn inputs remain immutable.

After a failed turn with confirmed shutdown and no valid pinned output seal, explicit manual continuation can reacquire a released reservation on the same recorded checkout. The service checks the checkout identity, path, access and competing owners in the continuation transaction; a conflicting owner produces `PREPARATION_CONFLICT`, and missing stop evidence produces `SHUTDOWN_UNCONFIRMED`. A checkout transferred to a live helper returns only through that helper's normal settlement. Preparation verifies the allocated checkout, captures its current tracked contents and includes exact nonignored untracked files within the original write scope as a new fixed input. Ignored files and caches remain excluded. The Host-authorized recovery preserves the original goal and fingerprints, retains earlier input/output artifacts, and does not turn the failed attempt's partial files into a successful output artifact.

Preparation and Git checks happen outside database transactions. Before activation, the service rechecks the continuation, current owner/revision, allocated manifest, task activity and reservation. Unprepared continuations are not claimable, and a transferred or occupied checkout is not scanned by preparation. Transient `WORKSPACE_BUSY` lock contention leaves the continuation queued for the competing worker or a later claim. A persistent preparation conflict creates a Host attention request and invalidates that continuation while preserving the original goal and recovery files; its bounded `preparationError` code/message is visible on `activeRequest` without loading the audit. After resolving the conflict, the Host can explicitly continue with new input.

Managed new files outside declared write scope and unsupported non-Git snapshot cases are reported explicitly. Git-ignored dependencies, environments and caches are excluded by the recorded `exclusionPolicy`; they are not read or hashed unless explicitly selected in `includeUntracked`. Stability and scope checks cover that managed set. Sealed Git refs keep artifacts available independently of live worktree paths. Preparation, verification and sealing never merge, delete or force-clean user workspaces.

Each helper handoff freezes the selected execution attempt and generation and carries only that attempt's sealed output reference, with explicit artifact attempt/turn IDs, output commit, snapshot hash and any diff reference. An earlier partial output is not presented as the output of a newer failed attempt. The designated integrator applies the fixed helper commits or patches and verifies the combined result; helper completion and integration acceptance are separate.

## Review, cancel and recover

Review the final attempt's fixed artifact and its checks. Acceptance is tied to that artifact, not whichever branch or directory happens to exist later.

```sh
"$BUDDY" acknowledge '{"runId":"<parent>","commandId":"review-1","controlFile":"<controlFile>","artifactId":"<final-output-artifact>","note":"Inspected the fixed diff and ran the combined regression suite.","verdict":"accepted"}'
"$BUDDY" cancel '{"runId":"<parent>","commandId":"cancel-1","controlFile":"<controlFile>","reason":"User cancelled the goal and its helper work."}'
```

Once the goal is delivered (the `get` view reports `state: "delivered"`, exposed as `workflowState` on task and wait envelopes), `finalArtifactId` names the selected fixed output. `acknowledge` accepts `accepted` or `rejected` plus a required `note`; it never changes execution status and a different repeated note or verdict is a `CONFLICT`.

Cancellation fences the complete owned descendant graph, including nested work behind a helper whose own turn already settled. Open requests and pending continuations are cancelled, attempt-scoped credentials are revoked, route work is fenced, and active executions receive durable cancel intent for their owning Workers. A cancelled goal is not evidence that every process stopped: reservations remain held until the relevant execution has real stop evidence, and a late result may preserve artifacts without reviving the cancelled lineage. Missing PIDs, lease expiry and lost connections never prove shutdown.

`get.shutdown` separates `selfConfirmed` and `descendantsConfirmed`. `unconfirmedCount` is the complete number of runs lacking stop evidence, including the root when applicable; `unconfirmedRunIds` is bounded to 32 and `truncated` marks omitted IDs. Ordinary task views expose the same aggregate as `workflowShutdown`. For governed work, the `await` envelope's `shutdownConfirmed` requires both self and descendants to be confirmed. A cancelled or delivered goal with unresolved descendants keeps waiting for committed stop evidence; expiry of the caller's wait window returns `wait-timeout` without inventing a stopped state.

A stopped wait does not cancel a run; reconnect using the same run ID. After a daemon restart, the existing Worker reattaches by attempt identity and replays its durable receipt. Use the [operations guide](operations.md) for runtime lifecycle and recovery and the [CLI reference](cli.md) for bounds and errors.

The private console shows goal/turn ownership, routing state, decision requests, helper tasks, continuation mode and fixed artifact references. Mutations use the same business operations as the CLI, with revision checks and stable command IDs. Page refresh makes no model call. The current installed/runtime version and acceptance evidence should be checked before assuming a development checkout is active.
