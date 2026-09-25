# Workers and adapters

Built-in tasks run in a Worker object inside an **independent supervisor process**, which owns the adapter child handles. External agents claim work directly over RPC and manage their own execution. This page covers the adapters, the public worker contract, identity and receipts, and capability-specific behavior. Internal state transitions are in [architecture.md](architecture.md); the DSH runner and turn protocol are in [runner.md](runner.md); the tool-free decision helper is in [decision.md](decision.md).

## Adapters

`buddy capabilities` (also `buddy adapters`) reports each adapter with `available`, `reason`, `capabilities` and `executedBy`, plus `localCapabilities`, the named operations, wait admission and an honest `limitations` map.

| Adapter | `executedBy` | Capabilities | Requirements |
| --- | --- | --- | --- |
| `dsh` | `built-in-worker` | `dsh`, `inquiry`, `workspace`, `cancel`, `artifacts`, `deadline`; model discovery and the verified `decision_execution` flag | Node.js and the `dsh.runner` resource; otherwise `ADAPTER_UNAVAILABLE` |
| `zcode` | `built-in-worker` | `zcode`, `observe`, `inquiry`, `workspace`, `cancel`, `artifacts`, `deadline`, `native-session`; model discovery | The installed ZCode CLI and an API-key provider; OAuth account providers are unavailable |
| `codex` | `built-in-worker` | `codex`, `workspace`, `cancel`, `artifacts`, `deadline`, `native-session`; model discovery | The installed Codex App Server and an existing native account-plan login; API-key accounts are refused |
| `decision` | `built-in-worker` | `decision` (tool-free, DSH-only) | Node.js and `dsh.decision`; used only by `selection-request`, with independently reserved capacity |
| `command` | `built-in-worker` | `command`, `cancel`, `artifacts`, `deadline`, `argv` | `argv` with 1–256 entries; `argv[0]` must resolve |
| `external` | `caller-owned-agent` | `external`, `artifacts`, `task-text` | no local process; the caller's agent claims and reports the task itself |

`localCapabilities` lists capabilities advertised by the local built-in workers; `external` is deliberately absent. Coding profiles carry `execution:<adapter>`, so clients do not maintain their own harness allowlist. DSH declares correlated `inquiry` delivered inside the native delegated process; ZCode declares `inquiry` over its cooperative checkpoint channel. Codex exposes bounded activity in task records but does not mount an inquiry socket or declare tool-free decision execution. Capability declarations and native behavior are checked separately in the [source acceptance record](../acceptance/production-repairs-0.8.0.md) and the [ZCode inquiry acceptance](../acceptance/zcode-checkpoint-inquiry.md).

For a task with explicit `timeoutSeconds: 0`, the Worker and DSH/ZCode/Codex runners omit the overall execution deadline. Model selection, the optional native version probe, DSH workspace bridge requests, short socket poll steps, cancellation and shutdown keep their separate bounds; a ZCode/Codex native operation using the overall deadline can now wait until a response or an explicit cancellation. The Worker retains its child handle, renews the attempt lease and records a real receipt when the process actually ends; the Host may cancel it at any time. A `buddy await` wait window ending does not affect the running task.

### DSH

The `dsh` adapter spawns the bundled Node runner `harnesses/dsh/scripts/run.mjs` (declared resource `dsh.runner`, overridable with `BUDDY_RUNNER_PATH`). The runner copies and patches the installed settings for the per-run model/provider/effort without modifying the originals, spawns `dsh --profile headless`, and reports one JSON object. The adapter maps the outcome honestly: only exit 0 with runner `status: "ok"` and confirmed shutdown is `ok`; a cancel without confirmed shutdown is `failed`; unparseable runner output is `invalid-result`. It writes the per-attempt inquiry bridge credentials to `<state>/attempts/<runId>/<attemptId>/inquiry.json` (mode `0600`, never exposed in a public task view) and reports the attempt log paths plus the runner's own `stdout`/`stderr`/`capture` files as artifacts when shutdown is confirmed.

`workspace: true` (the default) means DSH session grouping through the workspace bridge and never Git isolation; `workspace: false` passes `--no-workspace`. A grouping failure fails the run honestly instead of silently continuing ungrouped. The bridge is installed and recovered through [operations.md#workspace-bridge](operations.md#workspace-bridge).

An ungrouped governed run uses an attempt-private session directory through `session-persistence-jsonl.config.root`, while `DSH_HOME`, native settings and the credential vault remain owned by the installed harness. Grouped runs keep the original session root for workspace-bridge membership. `nativeSession` reports this storage choice separately from Git checkout isolation; its session ID must come from a validated native turn. See the [runner options](runner.md#options).

### ZCode

The `zcode` adapter drives the installed ZCode native **app-server** (`zcode app-server --cwd <cwd>`, the native newline-delimited JSON request/event protocol over stdio) through the small stdlib controller `buddy.adapters.zcode_runner`. The adapter spawns the controller in its own session; the controller spawns and owns the native app-server group. A result is `ok` only when the controller receipt, the native session-close evidence and confirmed stop of both process groups agree.

The native session database and storage are private to the logical Buddy goal, shared only with its eligible continuations; provider configuration snapshots are attempt-private. `nativeSession.storageOwner` is `buddy-goal` and `nativeAppVisibility` is `not-listed-in-native-app`, because the normal ZCode App lists its own user store. Activity, tool summaries and fixed artifacts provide the Buddy-side inspection entrypoint.

- Authentication is **API-key providers only** (`api-key` and `zhipu-coding-plan-api-key`). `available()` reports "ZCode has no configured API-key provider; OAuth account providers are unavailable through this adapter", and a requested OAuth/provider is rejected before dispatch. Availability reads provider access types. Execution gives the native loader attempt-private provider-file snapshots (0600); these may contain configured API keys, are excluded from public artifacts and evaluation metadata, and leave the original files unchanged. OAuth credential stores are not copied.
- `provider`, `model` and `effort` are required after routing. The run sets the model with `session/setModel` and the reasoning level with `session/setThoughtLevel` using `persistAsWorkspaceLastUsed: false`, then reads the settings back; a mismatch fails the attempt instead of continuing with unknown settings. The result reports `requested`, `resolved` and `observed: null`, because no served-model identity is available and unknown is never invented.
- Continuations with an unchanged configuration resume the **exact bound native session** (`resumeMode: "native-session"`), verified against a private binding of task, session, checkout and provider/model/effort. After an unproven or failed prior turn, `reconstructed-new-session` explicitly creates a new root session with the durable context and retains the prior session ID only as diagnostic data. If the identity or binding does not match, the adapter fails with `native-resume-unavailable` rather than silently starting a fresh session.
- Model discovery asks the native app-server for its available models without sending a turn and lists each model's native reasoning efforts; models exposing no configurable effort are omitted with a warning. A requested effort outside that list is rejected before inference; catalog errors include the legal efforts.
- Each attempt injects **session-private MCP tools** (`mcp__buddy_<hash>__buddy_finish_turn`, `__buddy_checkpoint` and `__buddy_answer_inquiry`, isolated to the session) that record the structured outcome and the inquiry channel's tentative receipts as signed payloads. Only the correlated root session may conclude the turn or move inquiry state. A failed finish-tool parameter check can be corrected within that same turn; one successful signed receipt, successful native turn completion, session close and real shutdown remain mandatory. Coding tools and internal subagents remain available.
- ZCode declares `observe` and `inquiry`. Its controller hosts a private read-only observation socket and publishes activity through `buddy.activity.ActivitySidecar`. `inquiry` is **cooperative**: the native protocol still has no turn-bound in-turn input (client-visible `v4/command` and `session/send` cannot fence a question to the running turn), so a Host question is only queued by the bridge and reaches the root at its own `buddy_checkpoint` call or through a finish refusal, never through an injected send, command, stop, restart or new turn. The root answers through `buddy_answer_inquiry`; the controller imports `queued → delivered → answered` journal state only after the root session's own `tool.updated` scheduled/result evidence matched the turn, tool call and signed receipt, excluding child/source/agent relays exactly like finish evidence; completed inquiry tool calls keep only a bounded recent identity window so an unlimited turn cannot accumulate retained receipts. A valid answer racing a Host withdrawal or settlement is ignored without failing the turn, while a different answer under an already-answered inquiry still fails it. A pending question refuses a `completed` finish with the question text until it is answered, withdrawn (`discard`) or made explicitly unavailable; settlement marks unanswered entries unavailable without waking a model, and a question whose journal record cannot be appended and fsynced durably is refused `journal-unavailable` instead of ever being reported queued, delivered or answered. Each journal append is one cross-process `flock` transaction (cap check, append, fsync): the session tools' shared-locked journal reads wait behind its exclusive side, so a reader never observes an in-flight append, and a failed append is rolled back to the committed length under the same still-held lock, with only a crash-torn tail isolated by the next append's leading newline. The governed prompt asks the root to checkpoint at natural milestones and before finishing, never on a timer. The [ZCode inquiry acceptance](../acceptance/zcode-checkpoint-inquiry.md) records the real native proof behind this declaration. The signed finish tool still refuses a completed outcome after a denied native interactive request and directs the Worker to report assistance or attention; an invalid or unproven result remains a visible failure.
- Native turn failure attribution comes only from the exported `turn.failed` session event. The controller imports a small whitelist of that event's actual schema — `error.type`, `error.code`, `turnPhase` and the strict `attribution` object (`source`, `reason`, `errorPhase`, `exceptionKind`, `providerId`, `modelId`, `providerKind`, `transport`, `statusCode`, `providerErrorCode`, `retryable`) — into a bounded `nativeFailure` block on the failed result, and its summary is composed exclusively from those whitelisted values. The error's raw `message`, `detail`, `stack`, `underlyingErrorMessage`, `underlyingErrorDetail` and opaque `data` are never imported, because they can carry raw provider text, credentials, URLs or prompt fragments. The `state.updated` `prompt_failed` envelope exports only a reason string and an opaque patch, so a failure observed only there keeps a generic unknown cause instead of a manufactured one, and the runtime never reads the native session store to infer more. A provider-side failure (for example a 429 rate limit) keeps the distinct `harness-error` termination reason; it is never relabelled `deadline`, `user-cancel` or `transport-error`.

### Codex

The `codex` adapter runs the installed Codex App Server over JSONL stdio from a Python controller and uses the user's existing native ChatGPT account-plan login. `BUDDY_CODEX_CLI` can select the executable; `OPENAI_API_KEY` and `CODEX_API_KEY` are removed from the native child environment, an API-key account returned by `account/read` is refused, and Buddy never starts a login or changes global Codex configuration. Discovery opens the native connection, checks the account mode, pages `model/list` and advertises only nonhidden models with native `supportedReasoningEfforts`; it sends no turn.

- Each governed attempt validates the selected model and effort against a fresh native list, sets the model on `thread/start` or `thread/resume`, sets model and effort again on `turn/start`, and imports the turn only from a completed native root turn, a final `agentMessage` validated against a strict `outputSchema`, an exit-zero controller receipt and confirmed shutdown of both owned process groups. The result carries the actual native thread, turn and final item IDs; a free-form final message cannot substitute for a completed turn.
- Cancellation and deadline request native `turn/interrupt` when the thread and turn are known, then close and, if necessary, terminate the owned native group. A completion already recorded before the cancellation keeps its completed result. An interactive native server request is rejected and surfaced as a controller-authored `attention` outcome bound to the correlated denied request: it carries `controllerAttention: true` and is never attributed to the model.
- The controller uses the allocated checkout as `cwd`, selects `workspace-write` on the thread and a `workspaceWrite` turn sandbox rooted at that checkout with network access disabled. Native continuation requires an exact private binding plus `thread/read` proof that the last stored turn is still the completed one; a changed configuration or unproven history reconstructs a new session explicitly, and a missing or mismatched thread fails closed. `observed` stays `null` because the served remote model is not independently attested.
- Codex declares no inquiry capability and no tool-free decision execution; the adapter cannot convert assistant prose into a correlated answer. Internal Codex subagents remain available within the native turn. The App Server is marked experimental by OpenAI, so a changed installed protocol must be reverified before this integration is claimed as verified.

### Command

The `command` adapter runs exactly one explicit `argv` process with `shell=False` and passes the Worker environment plus `BUDDY_TASK_ID`, `BUDDY_ATTEMPT_ID`, `BUDDY_TASK_FILE` and `BUDDY_LOG_DIR`. `argv` is valid only for this adapter, and its result note states that exit 0 means the command exited successfully, not that the task is correct.

### External

The `external` adapter has no local executor. `adapter("external")` reports `UNSUPPORTED_ADAPTER` to a built-in worker, so an external task stays `queued` until a caller-owned agent claims it; a built-in worker that scans it reports `adapter-mismatch` rather than running it with the wrong adapter.

## Governed DSH turns

A governed DSH claim adds service-owned turn metadata to the execution context. The logical task keeps its `runId`, original goal and submission fingerprint; every execution receives an independent attempt/generation and turn ID. The Node adapter implements the [structured turn protocol](runner.md#governed-turn-protocol). Host decisions, helper creation, continuation eligibility, routing and final acceptance belong to the Python blackboard and are described in [workflow.md](workflow.md).

`buddy_finish_turn` is registered in the root agent's scope, identified through the first ordinary user prompt and its prompt hash, and accepts `completed`, `assistance` or `attention`. Internal DSH subagents retain their normal tools and lifecycle; their own calls or final text cannot produce the root turn's result. A valid assistance/attention outcome can finish the current execution successfully while the logical goal waits for Host input. The request itself grants no authority to create another Buddy task.

The adapter and Worker use this order:

1. Before spawning, the adapter verifies the effective execution workspace and stages `task.txt` and `turn-input.json` under `<state>/attempts/<runId>/<attemptId>/`. The attempt directory is private (`0700`), and the input file is `0600`. It passes `--turn-input-file` and `--turn-output-file` together, independently of the `workspace` session-grouping flag. The supported input fields and byte bounds are owned by the runner reference.
2. When the claim supplies an agent credential, the adapter writes `agent-credential.json` privately and passes its path through `BUDDY_AGENT_CREDENTIAL_FILE`, alongside task/attempt attribution. That credential is separate from the model-visible turn input and Host control capability. It identifies the supported scoped API access; it does not establish OS isolation for a process with full same-user shell access.
3. The runner executes headless and records the accepted native tool result, matching session tool-result event, completed turn and awaited flush. `inputSha256` binds the input file's exact bytes; the Python adapter writes compact, sorted-key UTF-8 JSON consistently with its input-hash check. `promptSha256` binds the actual prompt delivered by the runner. Task/attempt/turn identities and these hashes are supplied or observed by runtime code, never selected by the model.
4. Once the owned child has exited, collection requires wrapper exit 0, runner `status: "ok"`, confirmed shutdown and a matching structured turn record before an `ok` report can proceed. Missing or invalid output records a turn-import failure with the logs retained. A successful file cannot override a failed, cancelled or unconfirmed execution.
5. For a successful governed outcome, the Python adapter seals the actual output workspace after shutdown and adds the effective `workspaceManifest` and `workspaceSeal` to its result. A sealing error is recorded as `workspaceSealError` and makes the attempt fail. Model artifact entries remain references; the seal binds the actual output snapshot, and file artifact registration verifies content separately. Preparation, verification and sealing run outside SQLite write transactions.
6. `collect()` returns an `AdapterOutcome`; the Worker adds elapsed time, log paths and runtime identity, then fsyncs the immutable local completion receipt before submitting the result. The service verifies applicable artifact evidence before committing the report, turn/request/task changes and events together. A lost response replays the same receipt and command ID. Receipt replay never executes the attempt again. Host acknowledgement of the fixed final artifact remains a separate operation.

Input writing, native result-file publication, output sealing, local receipt persistence, service commit and Host acceptance are distinct boundaries. A file appearing on disk, a terminal tool response or a queued follow-up does not establish the later boundaries. Workspace reservations also have a different lifetime from process capacity: yielding does not by itself transfer checkout write ownership.

The initial DSH turn reports `resumeMode: "initial"`; subsequent turns report `reconstructed-new-session` and the runner refuses a reconstructed result that reuses the previous session ID. The next execution consumes the service's durable checkpoint, decisions, helper outcomes and pinned artifacts in a fresh headless session. Original-session resume is not implemented by this adapter; ZCode is the adapter that declares `native-session`. Complete runner, workspace, integration and recovery evidence is recorded under `docs/acceptance/` in the full repository.

## The public worker contract

`src/buddy/client.py` is the supported way for a separate agent to participate without importing service internals, touching SQLite or reading the daemon token. `BoardClient` exposes:

- lifecycle and claims: register_worker, claim, reconcile, renew, progress, submit_result, release, workers;
- messages: post_question, update_message, get_message, list_messages, wait_message;
- observation: read_events, wait_events, artifacts;
- task helpers: submit, get, cancel, retry, acknowledge, wait_task, result, list_tasks;
- `new_nonce()` for a claim capability and `new_command_id()` for a replay-safe command ID.

`BoardClient(state_dir, autostart=False)` never cold-starts a service for any operation; it attaches to an existing one read-only or raises `SERVICE_UNAVAILABLE`. The default `autostart=True` attaches when a service is healthy and otherwise starts one.

### A runnable external worker

Submit a small demonstration task in a disposable directory:

```sh
"$BUDDY" execution-submit '{"requestId":"external-example-1","adapter":"external","cwd":"/abs/demo-directory","task":"Write external-report.txt containing handled by the caller-owned agent."}'
```

Save the following as `worker.py`, set `run_id` to the returned run ID, and run it with `uv run --frozen python worker.py` from the repository root. It handles only that task with a short synchronous operation. `fsync_json` is the bundled local-file helper; all board access goes through `BoardClient`.

```python
from pathlib import Path
import hashlib
from uuid import uuid4

from buddy.client import BoardClient, new_nonce
from buddy.worker.worker import fsync_json

state = Path.home() / ".local/share/hey-my-buddy/state"
board = BoardClient(state)
run_id = "<runId returned above>"
worker_id = "example-" + uuid4().hex
board.register_worker(worker_id, adapter="external",
                      capabilities=["external", "artifacts", "task-text"])

nonce = new_nonce()
intent = {"workerId": worker_id, "nonce": nonce, "claimRequestId": "claim-" + uuid4().hex}
fsync_json(state / "workers" / worker_id / "startup.json", intent)

response = board.claim(worker_id, intent["claimRequestId"], nonce, task_id=run_id)
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

Persisting the nonce and `claimRequestId` **before** claiming is what makes a committed claim with a lost reply recoverable: replaying the identical claim returns the same attempt, generation and a re-derived capability instead of minting a new generation. `reconcile` reattaches after daemon downtime, and `release` gives back an attempt that was claimed but never spawned.

For recovery, load the recorded identity and replay its claim; restarting this short example generates a fresh identity. A long-running agent must also renew its lease, observe cancellation, enforce its own execution deadline, and persist/replay completion receipts as described below. The demonstration does not implement that supervisor loop.

## Worker identity and receipts

- `worker-register` accepts `workerId` (required), `identity` (default `worker:<id>`), `adapter` (default `dsh`), `capabilities` (≤32), `host`, `pid`, `state` and a `commandId` receipt. Re-registering refreshes the record.
- `worker-claim` requires `workerId`, `claimRequestId` and a `nonce` (16–256 chars), and may target a `taskId`/`runId`. A worker that is unregistered, `stopping`, or already running an attempt is refused (`NOT_REGISTERED`, `WORKER_STOPPING`, `WORKER_BUSY`). An empty claim returns `claim: null` with a `reason` (`no-queued-work`, `not-queued`, `adapter-mismatch`, `capability-mismatch`, `capacity`, `cwd-overlap`, `exclusive-resource`, `awaiting-workspace-preparation`) and a retry delay.
- An attempt is identified by `(attemptId, generation, nonce)` plus the worker instance. The claim capability is derived from a service secret and the nonce; only a nonce verifier is stored. Superseded generations are rejected with `STALE_GENERATION`, and command receipts are bound to the worker identity, nonce verifier and attempt so another worker cannot replay them.
- `worker-renew` extends the lease and reports `cancelRequested`, which is how a worker learns about durable cancel intent. It never records activity and never clears uncertainty. `worker-progress` appends a bounded progress event; its `data` accepts only `{"activity": ...}` and stores the whitelisted native-activity projection described below. A finished attempt refuses both.
- `worker-reconcile` accepts `workerId`, `attemptId`, `generation`, `nonce`, `claimRequestId`, `pid`, `runtimeIdentity` and `workerInstance`; `taskId` is not a parameter. It reattaches one legitimate worker after downtime by attempt identity and the nonce fsynced before claiming, never by PID. The claiming `workerInstance` is checked when the attempt recorded one: a different process that happens to reuse the worker id has no child handle and is `UNAUTHORIZED`; a superseded generation is `STALE_GENERATION`. The worker replays an immutable completion receipt first, so a reconciled attempt is never returned to executing after it in fact completed. A terminal attempt is reported immutable and unchanged. A successful non-terminal reconcile restores the attempt to `executing`, returns retained resource claims to held, clears the `attempt-uncertain-after-restart` waiting reason when the task still carries it, and records `attempt.reconciled` in one transaction. Lease-expiry uncertainty alone does not set that waiting reason.
- `worker-result` commits the result, artifacts, task/attempt state and the completion event in one transaction. The optional `terminationReason` must be `completed`, `user-cancel`, `deadline`, `harness-error` or `transport-error`; the service never invents another label, and a genuine completion that beat a cancel request stays `completed` with the race recorded. Artifacts are verified outside the write transaction by existence, size and SHA-256; a mismatch is `ARTIFACT_MISSING`/`ARTIFACT_MISMATCH` and publishes no completed result. Replaying the identical result is idempotent; a conflicting replay of a terminal attempt is `CONFLICT`.
- Shutdown evidence is explicit: an `ok` result without confirmed shutdown becomes `reconciliation-needed`; a confirmed cancel becomes `cancelled`, an unconfirmed one stays `reconciliation-needed` with claims retained. `execution-retry` is refused while shutdown is unconfirmed.
- `worker-release` can release a starting attempt without explicit evidence. When evidence is supplied, the accepted proof is `{"spawnIntentWritten": false}` and the claiming worker instance is checked. A started attempt cannot be released merely because its PID disappeared or its lease expired.

### Activity observation

A native controller publishes bounded activity through the shared `buddy.activity` helper: one attempt-private `activity.json` sidecar containing `version`, `taskId`, `attemptId`, `generation`, optional `updatedAt` and one nested `activity` payload. The Worker reads it atomically, validates the attempt binding and forwards it as `worker_progress.data.activity`; the path itself never appears in a public response. `buddy.activity` exposes `normalize_activity`, `validate_sidecar`/`read_sidecar`, `write_json_atomic` and an `ActivitySidecar` that updates atomically and throttles same-phase rewrites to roughly two seconds (a phase change publishes immediately).

The payload is a closed whitelist. `phase` is one of `starting`, `waiting-model`, `streaming-model`, `tool-running`, `waiting-external`, `waiting-host`, `finishing` or `unknown`; optional fields are `observedAt`, `eventSeq`, `nativeSessionId`, `lastNativeActivityAt`, `lastToolActivityAt`, `toolName`, `waitingReason` and `counts` with only `modelTurns` and `toolCalls`. At least one of `observedAt`/`eventSeq` is required. Counts are nonnegative integers, strings have explicit bounds, unknown keys are rejected, and an explicit null means unknown rather than zero. Prompts, tool arguments, output text, credentials and hidden reasoning have no field and are never collected. The projection is stored per attempt and generation, monotone and idempotent: an identical or older receipt is reported `unchanged`, an advancing one appends `attempt.activity`, and a replacement generation never inherits the previous attempt's observation. Ordinary lease renewal and prose progress are not native activity and are never converted into it. Task reads and the governed `get.task` summary expose `activity` (null when none). The DSH event observer and Codex controller emit this envelope; ZCode uses `ActivitySidecar` directly. Observation does not imply support for injecting questions.

## Worker supervisors

The daemon starts a detached supervisor pool sized to business plus decision capacity: `local`, `local-2` and `local-3` by default, with `BUDDY_WORKER_ID` as the configurable prefix. An operator can start an independently named worker or ask a specific worker to stop cooperatively:

```sh
"$BUDDY" worker-start '{"workerId":"local"}'
"$BUDDY" worker-stop  '{"workerId":"local"}'
```

`worker-start` selects or materializes the stable runtime first, runs `python -m buddy.worker.supervisor` in its own session with file-backed logs at `<state>/worker.log`, and returns `workerId`, `supervisorPid`, `logPath` and `stateDir`. The supervisor holds an exclusive lock (`workers/<workerId>/supervisor.lock`), so liveness is decided by lock ownership rather than a stored PID. It restarts the Worker loop after an exception with bounded backoff and observes `workers/<workerId>/stop.request`; the daemon reconciles crashed members of its recorded pool. `worker-stop` writes a durable request only for the specified ID and never signals a process the CLI did not create. Pool scale-down uses a separate `retire.request`, observed between attempts after receipt/startup reconciliation; it does not cancel an active child. Pending results and uncertain attempts remain accounted for even when a supervisor is missing.

Workers keep an immutable local completion receipt under `<state>/workers/<workerId>/receipts/<attemptId>.json` until the service confirms the result transaction, and a `spawn.intent`/`spawn.marker` pair under `<state>/attempts/<runId>/<attemptId>/` makes the crash window explicit. Unsatisfied receipts are replayed by the next supervisor start, and a durable receipt is never executed a second time. A startup intent left by a previous process is preserved as orphaned evidence: the new process holds no child handle, so the attempt stays `uncertain` with its claims retained. `buddy workers` lists registered workers with their state (`starting`, `idle`, `busy`, `stopping`, `lost`), adapter, capabilities and current attempt. A worker polls durable cancel intent every two seconds and renews its lease at `max(5 s, lease_seconds / 3)`.
