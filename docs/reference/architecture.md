# Architecture

This page describes the implemented 0.6 architecture in this checkout and is verified against the source under `src/buddy/`, the DSH scripts and plugins under `harnesses/dsh/`, and the React sources in `apps/console/`. The release identity is 0.6.2; `CONTRACT_VERSION` is `0.6.1`, the transport protocol version is 2, and the database accepts schema 8 only. The repository's `docs/decisions/` directory holds the design record; design proposals do not by themselves establish runtime behavior, and installed-runtime identity is verified separately from this checkout.

## Process topology

```text
Codex skill / CLI / private console / external worker
                  |  named C-Two RPC (private token)
        Python blackboard daemon  <-- only writer of authoritative state
          state machine + transactions + events
                  |  SQLite WAL (board.sqlite3, schema 8)
        tasks / attempts / workers / messages / artifacts
        events / command receipts / resource claims
        evaluation table / governed goal, turn, request and route records

Independent supervisor process -- C-Two --> daemon
  Worker.run() executes inside this supervisor process
    | owns the child process handle, deadline and local receipt
    +-- dsh adapter ------> harnesses/dsh/scripts/run.mjs -> dsh (Node)
    +-- zcode adapter ----> buddy.adapters.zcode_runner -> zcode app-server
    +-- decision adapter -> harnesses/dsh/scripts/decision.mjs (tool-free call)
    +-- command adapter --> one explicit argv process

Caller-owned external agent -- BoardClient / C-Two --> daemon
  claims external tasks and reports its own results
```

The daemon and each supervisor are separate OS processes. A supervisor runs the Worker object in its own process and restarts that execution loop after an exception. The Worker owns the adapter child handles and enforces the execution deadline while the daemon is unavailable. External agents participate directly through RPC. Stored PIDs are diagnostic values, never sufficient authority to signal a process.

## C-Two surface

Service operations use named C-Two methods: `BuddyControl` on the `buddy-control` resource (mutations, reads, lifecycle) and `BuddyWait` on the separate `buddy-wait` resource (bounded waits). `BuddyControl` exposes service operations (`health`, `capabilities`, `service_control`, `console`, `console_snapshot`, `runtime_info`), ordinary task operations (`task_submit`, `task_get`, `task_list`, `task_result`, `task_cancel`, `task_retry`, `task_acknowledge`), the governed `workflow_*` operations, the `worker_*` and `message_*` groups, `inquiry_observe`, `artifact_list`, `events_read`, the `evaluation_*` gate and `selection_*`/`model_catalog_refresh` operations; `BuddyWait` exposes `events_wait`, `task_wait`, `message_wait` and `wait_capacity`. Every request is a validated JSON string carrying the private service token; the schema validators reject unknown fields before anything reaches the store.

The CLI uses short names that map one-to-one onto those operations: `submit` → `workflow_submit`, `get` → `workflow_get`, `decide` → `workflow_decide`, `continue` → `workflow_continue`, `takeover` → `workflow_takeover`, `cancel` → `workflow_cancel`, `acknowledge` → `workflow_acknowledge`, `suggest` → `workflow_suggest`; `execution-submit` → `task_submit`, `execution-cancel` → `task_cancel`, `execution-retry` → `task_retry`, `execution-acknowledge` → `task_acknowledge`. There is no public `dispatch(method, JSON)` facade and no compatibility alias for the removed `start`, `run`, `workflow-*` or dashboard commands.

The local `worker-start` and `worker-stop` CLI commands start a supervisor or write its cooperative stop request directly. They do not use this RPC path.

The transport DTO in the released C-Two 0.5.1 uses the Python pickle protocol for string arguments, so this is a **same-user Python API only**. It is not claimed to be cross-language portable, no FastDB DTO is used, and a non-Python client must define its own contract instead of relying on these payloads. Node-side bridges never connect to C-Two directly: the Python service is the client of the DSH bridges over private files and sockets.

## Data model

One schema-versioned SQLite database (`board.sqlite3`, schema version 8) owns every authoritative fact in 35 tables.

| Group | Tables |
| --- | --- |
| Core | `meta`, `tasks`, `attempts`, `workers`, `messages`, `artifacts`, `events`, `commands`, `resource_claims`, `cursors` |
| Evaluation | `evaluation_state`, `evaluation_revisions`, `evaluation_profiles`, `evaluation_cards`, `evaluation_preferences`, `evaluation_evidence`, `evaluation_decisions`, `evaluation_catalog`, `evaluation_readers`, `evaluation_writers`, `evaluation_evidence_pending`, `evaluation_samples`, `evaluation_card_history`, `evaluation_aggregates`, `decision_requests` |
| Governed work | `workflow_runs`, `workflow_routes`, `workflow_turns`, `workflow_requests`, `workflow_children`, `workflow_continuations`, `workspace_reservations`, `agent_credentials`, `workflow_artifacts`, `workflow_suggestions` |

Notable responsibilities: `tasks` holds the durable `task_id` (= `runId`), unique `request_id`, owner attribution, canonical specification and input fingerprint, adapter, cwd, required capabilities, exclusive resources, timeout, state, `queue_reason`, revision and acceptance fields; `attempts` holds one row per attempt generation with worker identity/instance, nonce verifier, claim request id, lease, execution state, runtime identity, log paths, result, cancel request, exit code/signal and shutdown evidence; `commands` is the idempotency receipt ledger; `evaluation_readers`/`evaluation_writers` implement table-level admission with fair writer intent, generations and expiring grants; `workflow_routes` binds one routing decision to one run and owner generation; `workflow_turns` stores one structured turn per execution with its frozen input, outcome and native provenance; `workflow_artifacts` pins immutable input/output manifests; `workspace_reservations` records logical checkout ownership independently of attempt capacity.

Connections are opened per operation with `foreign_keys=ON`, `journal_mode=WAL`, `synchronous=FULL`, `busy_timeout=10000` and `trusted_schema=OFF`; writes use `BEGIN IMMEDIATE`. Startup refuses a database whose schema marker is not `8`, whose integrity check fails or whose foreign-key check is nonempty, and tells the operator to use a clean state directory and retain the existing directory as an archive. There is no importer, conversion branch or compatibility path for older schemas or the removed Node records.

Routine lease renewal and liveness timestamp updates do not each emit an event. Atomic event delivery applies to the business operations that record events.

Task states: `queued`, `running`, `cancelling`, `completed`, `failed`, `cancelled`, `reconciliation-needed`. Attempt states: `starting`, `executing`, `finalizing`, `uncertain`, `finished`. Worker states: `starting`, `idle`, `busy`, `stopping`, `lost`. Message states: `queued`, `claimed`, `delivered`, `answered`, `discarded`, `unavailable`. Governed run states: `executing`, `awaiting-host`, `waiting-helpers`, `delivered`, `accepted`, `cancelled`, `failed`. Turn resume modes: `initial`, `reconstructed-new-session`, `native-session`. Transitions are checked in one table in `store.py`; a stale revision/generation is rejected with `REVISION_CONFLICT` or `STALE_GENERATION`, and a partial unique index guarantees at most one effective active attempt per task — an uncertain attempt keeps its slot so a replacement cannot be created while a survivor may still run.

## Identity and idempotency

- `requestId` is the idempotency key. The request is normalized to a canonical specification (canonical realpath cwd, validated fields, defaults) and fingerprinted (version 2). The same `requestId` with an identical fingerprint recovers the existing task; changed input is a `CONFLICT`, including after completion or restart. A governed run additionally compares a request fingerprint covering the execution workspace and Host.
- `runId` equals `taskId` and selects exactly one task. Read and wait operations never launch a replacement.
- An attempt is identified by `(attempt_id, generation, worker, nonce)`. The claim capability is derived from a service secret; only a verifier for the nonce is persisted, never the capability in plaintext. A worker persists its nonce and `claimRequestId` **before** claiming, so a committed claim whose reply is lost replays the identical attempt, generation and capability instead of minting a second one.
- Command receipts are bound to the worker identity and nonce; replaying a `commandId` with a different request or from another worker is an error, not a state change. Worker results are replayed idempotently; a conflicting replay fails.
- Governed mutations use stable `commandId` receipts plus owner generation and expected revision; a replay returns the recorded response and a changed payload under the same command ID is a `CONFLICT`.

## Admission, routing and resources

A submitted task is admitted as `queued` and starts when a matching worker claims it. `queueReason` reports why it is waiting: `awaiting-worker`, `capacity` (business lane), `decision-capacity`, `cwd-overlap` (canonical ancestor/descendant overlap), `exclusive-resource`, `awaiting-model-selection`, `awaiting-configuration-validation`, `awaiting-routing-shutdown`, `awaiting-workspace-preparation`, `awaiting-host` or `helper-attention`. `BUDDY_MAX_CONCURRENT` bounds business attempts (1–8, default 2); `BUDDY_MAX_DECISIONS` separately bounds the internal `decision` adapter (1–4, default 1). Each worker runs one attempt at a time (`WORKER_BUSY`). Claims filter full lanes before the bounded candidate scan, so a business backlog cannot hide available routing work. Workspace and exclusive-resource checks apply in both lanes. Claims are released when shutdown is confirmed or the owning worker proves the attempt never spawned; uncertain attempts keep their lane capacity and resource claims.

The daemon manages a generic supervisor pool sized to the sum of both limits: `local`, `local-2`, `local-3` by default. Workers can execute either lane; admission enforces the independent limits. Exact managed IDs are durably recorded in `worker-pool.json`; similarly named custom workers are not adopted. Reducing capacity requests cooperative retirement between attempts and after receipt recovery, preserving active children and unresolved evidence. `health.capacity` reports each lane's limit and active count, while `maxConcurrent` is their combined limit.

Model routing is durable and decision-bound: admission is committed before any selector work, and the immutable original request stays separate from the resolved execution configuration. A complete explicit `adapter`/`provider`/`model`/`effort` quadruple is validated against the installed native catalog and dispatched without a routing-model call; partial fields are hard filters applied in Python; an unspecified configuration uses the bounded current evaluation table and the fixed decision profile. No selector, no legal candidate or a failed selection opens a durable Host routing boundary on the same run, which the Host resolves with a complete `configuration` or a `reroute:true` continuation under the original constraints. Selection and execution revisions are recorded separately, and cancellation or takeover fences owned selection work. The [workflow guide](workflow.md#configuration-and-routing) owns the full contract.

## Wait separation

Bounded waits use the dedicated `buddy-wait` resource with its own admission (`BUDDY_WAIT_CAPACITY`, default 32). Waits are check/subscribe/recheck sequences on the committed event stream, hold no database transaction and no exclusive lock, and never cold-start a service. When every admitted slot is busy the caller gets a resumable `WAIT_OVERLOAD` error carrying the cursor and retry delay; no task state changes. This keeps any number of waiting clients from consuming the capacity that cancel, renew and result commits need.

## Inquiry

The inquiry bridge lives in a DSH Node plugin because the upstream plugin runs in Node; the Python service is its client and the importer of its bounded JSONL journal. The bridge appends transport evidence next to its socket, and the service imports it idempotently keyed by `inquiryId` while the board message rows stay authoritative. The journal can never overwrite a recorded answer. Only the `dsh` adapter declares the inquiry capability; `command`, `external` and `zcode` report honestly that they have no inquiry capability, and ZCode has no inquiry bridge yet.

The service has no automatic inquiry scheduler; clients request it as needed. The no-question form is bounded observation from the durable record plus the bridge's live view; it is not a percentage or a guaranteed ETA, and unobservable fields are named. A question is durably recorded before injection, delivered to the run's own live agent, and answered only through that run's correlated reply tool, so assistant prose is never an answer. `waitMs` bounds only the call's wait: an inquiry never extends, pauses or cancels the execution deadline, and an idle or terminal agent cannot be woken.

## Uncertainty and recovery

- Ending a CLI wait (Ctrl-C, closed terminal, `waitSeconds` expiry) cancels only the wait. The durable task keeps running while a worker owns it and is recovered by `requestId`/`runId`.
- `cancel` is durable: a queued task is cancelled immediately; an active task becomes `cancelling` and the owning worker observes the cancel intent through its periodic cancellation check (currently every 2 seconds, separate from lease renewal) and stops its own process group. A confirmed completion that beat the cancel request is the one legal outcome, recorded in the event stream.
- A daemon restart preserves task and attempt identity. Every in-flight attempt becomes `uncertain` with its resource claims retained, and busy workers are marked `lost`. The legitimate worker keeps its child handle, deadline and receipt, and reattaches through `worker_renew`/`worker_reconcile` using the same attempt id, generation and nonce. Nothing is reattached by PID.
- Lease expiry marks an attempt `uncertain`, never `stopped`. A surviving process is never inferred dead from a missing PID or an expired lease, and uncertainty is never converted into a retry: `execution-retry` requires confirmed shutdown (`SHUTDOWN_UNCONFIRMED`) or the worker that owns the process handle reporting an observed outcome.
- Workers keep an immutable local completion receipt and replay it until the service confirms the result transaction; a durable receipt is never executed twice. Spawn intent/marker files make the crash window explicit, and an intent owned by a previous process is preserved as orphaned evidence.

## Evaluation and console

The private writable console serves a built React/Vite bundle over authenticated loopback HTTP. Its session, exact-origin and CSRF checks are separate from the ordinary CLI token. HTTP mutations and agent-side C-Two operations reach the same Python business operations. The table-level reader/writer gate excludes selection readers from edits, not existing business execution; ordinary snapshot reads never take a lease or invoke models. The [evaluation reference](evaluation.md) owns the console HTTP surface, the gate, evidence, cards, decisions and maintenance.

`selection_request`, `selection_get`, `selection_list`, `evaluation_maintain` and `model_catalog_refresh` are named C-Two operations. Decisions use the internal `decision` adapter on the existing Worker queue, not a daemon-owned process runner. Input is frozen in the claim, and decision completion, reader/writer release and any publication share the Worker result transaction. Proposal validation uses a savepoint so an invalid patch can settle without poisoning the completion receipt. The [native helper](decision.md) is DSH-only and has no coding tools. Read-only decision history and per-run routing history use bounded keyset pages over existing durable request/route sequences; they do not enter model admission. New turn inputs retain their exact routing/configuration-revision binding, with absent historical bindings reported as unknown.

## Host-directed work

The [governed workflow](workflow.md) preserves the logical task and original specification while each continued execution receives a new attempt, turn identity, effective workspace and frozen context. The DSH root agent records completed, assistance or attention through native tool/turn/flush evidence; the ZCode root session records the same dispositions through its session-private signed MCP tool and session-close evidence. Worker shutdown and Git sealing precede authoritative result import. A yield returns control to the Host and releases execution capacity only after real stop evidence; the logical checkout reservation can remain held.

The Host authorizes explicit helper tasks and one-use automatic continuation. Helpers reuse the normal queue, workers and receipt mechanism. Multiple open requests coexist: `activeRequest` remains the selected decision boundary, while `pendingRequests` includes it in a bounded open-request queue. `counts.openRequests` counts the complete open set, and `truncated.pendingRequests` counts omitted entries. Answering one request promotes another open boundary before the parent can resume. A helper completing elsewhere in the graph cannot erase that queue.

Nested attention creates correlated proxy requests along the owned ancestor chain. `proxy` identifies the immediate source request and `origin` the requesting leaf. A Host decision on the root boundary follows the validated ownership chain, resolves those exact requests, and resumes the leaf when dependencies permit. Intermediate runs wait for their helpers; other pending requests remain visible. Boundary changes and their wakeup events commit together, and replay cannot schedule the same continuation twice. There is no direct peer dispatch. Internal agent subagents remain available in both coding harnesses.

`continue` accepts `targetRunId` so the owning root Host can deliver input or a configuration to an authorized descendant without adopting another owner's capability; the service validates the recursive ownership chain and applies the checks above. Cancellation and takeover fence the whole owned graph, including route work and pending continuations.

Owner capabilities, owner generations, revisions and idempotent command receipts govern mutations. Private submission credentials recover original admission without turning an owner label into authority. The console's authenticated user authority is attached internally. DSH and ZCode children receive attempt-scoped credentials; supported API calls cannot create helpers, impersonate a Host or control other runs. Full-shell processes of the same OS user are not sandboxed by this capability boundary.

`workspace.py` resolves actual checkout/repository identities, captures dirty input with a private Git index, creates detached worktrees and seals fixed output commits/diffs. A continuation retains its currently allocated physical checkout and records a fresh input manifest there; the original submission and earlier turn inputs remain unchanged. The latest applicable self/helper seal is selected by the append-only artifact publication order and its immutable execution-manifest binding, independently of wall-clock timestamps. Helper outcome references are frozen to the named attempt and carry their actual artifact attempt/turn IDs. Independent helper outputs are integrated explicitly by the named Host/agent.

Git operations run outside SQLite transactions. Preparation snapshots owner/revision, current allocation, task/continuation state and reservations, then rechecks them before activating a new manifest. Unprepared continuations cannot fall back to the original manifest at claim time. Preparation failures become durable attention boundaries without failing the original goal or blocking unrelated queued work. Reservations use actual checkout identity independently of Worker capacity; sibling cwd paths cannot evade write exclusion, and preparation does not scan a checkout whose write ownership was transferred. Ignored environments/caches are outside the manifest's managed set unless explicitly selected. The source HEAD, index and files are preserved.

## Harness integrations

| Adapter | Executed by | Declared capabilities | Continuation |
| --- | --- | --- | --- |
| `dsh` | built-in Worker | `dsh`, `inquiry`, `workspace`, `cancel`, `artifacts`, `deadline`; model discovery | `reconstructed-new-session` |
| `zcode` | built-in Worker | `zcode`, `workspace`, `cancel`, `artifacts`, `deadline`, `native-session`; model discovery | `native-session` for a proven session with matching goal/checkout/configuration; `reconstructed-new-session` without a proven session or after a configuration change |
| `decision` | built-in Worker | `decision` (tool-free, DSH-only) | n/a |
| `command` | built-in Worker | `command`, `cancel`, `artifacts`, `deadline`, `argv` | n/a |
| `external` | caller-owned agent | `external`, `artifacts`, `task-text` | n/a |

DSH runs through `harnesses/dsh/scripts/run.mjs`, which patches a private copy of the installed settings for the per-run model/provider/effort and mounts the session-capture, inquiry and turn-result plugins. ZCode runs through the native `app-server` protocol with a private native session store per goal, provider configuration snapshots per attempt and a session-private signed MCP finish tool. Native continuation requires a proven prior turn, unchanged configuration and a private binding matching `taskId`, `sessionId`, checkout `cwd` and provider/model/effort; missing or mismatched bindings are rejected. A continuation without a proven previous session or with a changed configuration creates a new root session from fixed input. ZCode supports API-key providers only and reports `observed: null` because no served-model identity is available. Neither integration claims the other's evidence format. See [workers.md](workers.md) and [runner.md](runner.md).

## Runtime packaging

Dependency management is uv-only with a frozen lock. `packaging/runtime-assets.json` declares exactly what is shipped: `packaging/runtime-assets.json`, `pyproject.toml`, `uv.lock`, `bin/buddy`, `src/buddy`, `harnesses/dsh/scripts` and `harnesses/dsh/plugins`, with named resources `dsh.runner`, `dsh.catalog`, `dsh.decision`, `yaml.bridge` and `console.assets`. On a cold start with no READY runtime, `launch_target()` materializes a content-addressed runtime under `BUDDY_RUNTIME_ROOT` (default `~/.local/share/hey-my-buddy/runtime/<contentId>`): it copies the runtime assets into the final directory, runs `uv sync --frozen --no-dev` there, rewrites environment paths, and writes `READY.json` last. Credentials, user data, tests, `node_modules`, the React source and any existing virtual environment are never copied. The daemon and its workers then run from that runtime's own interpreter, so replacing the plugin cache does not disturb a running service. `BUDDY_DEV_SOURCE=1` suppresses automatic materialization; source execution requires that no matching or explicitly pinned READY runtime is selected. Inspect the actual runtime identity: `stable` is true only when the process imports `buddy` from inside the runtime with no source leaks.

`packaging/stage-plugin.py` assembles the plugin from the metadata files, both READMEs, `docs`, `skills/buddy` and the declared runtime assets; it refuses tests, `node_modules`, virtual environments, the React source and scratch content, and publishes atomically. `bin/buddy` is the single bundled launcher.

## Current limits

- POSIX only (macOS/Linux); Windows is not implemented.
- One daemon owns a state directory (lifetime locks); a schema or contract mismatch is refused instead of migrated silently. The default fresh board is `~/.local/share/hey-my-buddy/state`.
- SQLite is the deliberate local database. Remote untrusted tenancy, PostgreSQL/HA and exactly-once external side effects are not implemented and are not claimed.
- DSH continuations reconstruct a fresh session; ZCode resumes a proven session only with a matching goal, checkout and configuration binding. Without a proven session or after a configuration change, ZCode reconstructs a new root session. Neither adapter recovers an omitted transcript implicitly.
- No monetary budgets, automatic community research, periodic evaluation maintenance or native post-turn App wakeup. Notifications are post-commit hints; inspect `capabilities` for the installed version's supported operations and limits.
- ZCode has no inquiry bridge, supports API-key providers only and reports no served-model identity.
- The [0.6 acceptance record](../acceptance/neutral-core-0.6.0.md) identifies the verified installed runtime and real artifacts; a checkout alone does not establish the identity of another running installation.
- The effective state-transition tables live in `store.py` (`TASK_TRANSITIONS`, `ATTEMPT_TRANSITIONS`) and are summarized here.
