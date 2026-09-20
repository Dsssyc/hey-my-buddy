# ADR-001: Python transactional agent blackboard over C-Two

Status: Accepted for implementation; acceptance evidence is required before release. Date: 2026-09-19

> **Historical record.** This ADR preserves the design requirements that led to the 0.4.0 implementation. It is not a description of current behavior, and its implementation and migration instructions are historical — do not repeat the migration. Implemented behavior is documented in [architecture.md](../../deepseek-delegate/references/architecture.md); the acceptance evidence is [python-blackboard-0.4.0.md](../acceptance/python-blackboard-0.4.0.md).

## Purpose

Buddy becomes a durable collaboration resource shared by clients and agent workers. A Python service owns task facts in SQLite. C-Two exposes explicit domain operations. Independent workers execute through adapters; dsh is one adapter, not the data model. The implementation replaces the Node global job manager and Python-to-Node engine relay. Node remains necessary only inside the dsh adapter and its upstream plugins.

This design was authorized by the architecture discussion. Implementation preserved the existing CLI-only implementation as a baseline. The then-installed 0.3.0 Buddy skill was used to delegate implementation, and acceptance subsequently dispatched real work through the new blackboard itself.

## Ownership and process topology

```text
Codex / CLI / dashboard / external worker SDK
                    | C-Two domain RPC
          Python blackboard service
       state machine + transactions + events
                    | SQLite WAL
       tasks / attempts / messages / artifacts
           events / receipts / resource claims

Independent Python Worker -- C-Two --> blackboard
       | owns process lifetime, deadline and local receipts
       +-- dsh adapter --> existing run.mjs --> dsh
       +-- command adapter --> explicit argv process
       +-- external adapter protocol --> caller-owned agent
```

The service is the only writer of authoritative database state. Worker recovery spools are transport receipts, never a second task database. They contain bounded, correlated, replayable facts and can be deleted only after durable acknowledgement. The supervisor owns only processes it created. Stored PIDs are diagnostic values, never sufficient authority for a restarted service to send signals.

Scheduling and agent-specific protocols are separate from the transactional store. Scheduling can be an in-process service module. Adapters do not receive database handles. No agent can set arbitrary task state or change another attempt's result.

## Persistent model

Use schema-versioned SQLite with foreign keys, WAL, busy timeout and `synchronous=FULL`. Transactions are short, with a fresh transaction/connection boundary per operation; no database transaction spans RPC, a subprocess, an LLM call or an event wait. Use `BEGIN IMMEDIATE` for competing claim/mutation decisions. On-disk state, endpoints, receipts and authentication material retain private modes.

The model must expose these concepts with actual relational constraints:

* Task: durable task/run ID, scoped request ID, canonical specification and input fingerprint, owner, adapter/required capabilities, canonical cwd, declared exclusive resources, timeout, state, revision, timestamps and selected attempt. Store the full task text; the CLI's existing runId remains a task identifier.
* Attempt: unique ID and monotonically increasing generation per task, worker identity, unguessable claim capability (persist a verifier, do not expose it in public records), lease, execution state, runtime identity, logs, result and shutdown evidence. A terminal attempt is immutable apart from separate review.
* Worker: identity, adapter/capabilities and liveness. A worker may execute only its current claimed attempt; external Python workers use the same public RPC.
* Message: full bounded question/answer or observation, author/recipient, task and attempt references, correlation ID, payload hash, delivery evidence and state. Observations are attributed reports, not automatically verified facts.
* Artifact: immutable result reference, location, content hash, size and attempt; large data and full logs remain outside the database. Artifact registration must validate the claimed file before publishing a completed result.
* Event: monotonic sequence, task/attempt/revision, event kind and bounded payload. State change and event insertion occur in the same transaction. Retain events for cursor replay; no pruning without an explicit retention contract.
* Review/receipt: idempotent command receipts, consumer cursors where persisted, and acceptance evidence distinct from execution success.

An equivalent normalized schema is acceptable; duplicating authoritative state in JSON files is not. JSON columns for validated specifications or adapter metadata are appropriate alongside explicit indexed state/identity columns.

## Operations and compatibility

C-Two contracts expose named operations, not one public `dispatch(method, JSON)`. Define and validate request/response schemas and consistent structured errors for:

* task submit/start, get/status, list, result, cancel request, acknowledge;
* worker registration, atomic claim, lease renewal, progress/result submission;
* message/inquiry posting, delivery/answer receipts and lookup;
* event read/wait with cursor, bounded page size and bounded wait;
* health, adapter capabilities, dashboard and service lifecycle.

The transport DTO choice must work with the released PyPI C-Two version (currently 0.5.1). Explicit named CRM methods plus validated Python request types are sufficient for this same-user Python API. If portable FastDB DTOs are implemented, verify their actual encoding/export; do not claim cross-language portability for pickle payloads. Private JSON serialization is permissible at an adapter edge, but not an extra Python-to-Node global dispatch service.

Keep existing user-facing `start/run/await/status/wait/result/list/cancel/inquire/ acknowledge/health/dashboard/stop`, stable JSON envelopes and actionable errors. `runId` selects one task. `requestId` + identical normalized input returns the same task; changed input conflicts, including after completion/restart. Read and wait operations never launch a replacement task. `run` retains deadline + 60 s default wait; `await` only observes an existing task. CLI wait timeout/disconnect never cancels execution or extends execution time. Maintain explicit execution deadlines.

Add public board/worker commands or a Python client for multi-agent operations and document their concrete invocation. Implement two real built-in adapters: `dsh` (default) and `command` (explicit argv, no implicit shell). Demonstrate a separate external worker claiming and completing work through RPC without importing service internals. Optional capabilities such as steer/resume must be advertised honestly.

Task admission may now queue work when capacity or cwd resources are occupied. Document this intentional change from the old BUSY-only admission and expose a queue reason. Claiming must enforce global capacity and canonical ancestor/descendant cwd overlap, including unknown attempts which still reserve resources. Each task has at most one effective active attempt. Retrying unknown or failed work is an explicit operation with a new attempt, never an automatic reaction to lost RPC.

## State transitions, leases and recovery

Tasks distinguish queued, running, cancelling, completed, failed, cancelled and reconciliation-needed states. Attempt state additionally records starting/executing/ finalizing and uncertain ownership. Use one documented transition table and reject illegal transitions with revision/attempt checks.

Claim atomically checks eligibility/resources, creates the attempt, assigns its generation and capability, and appends the event. Worker startup is authorized by that attempt. A durable start intent, exclusive per-attempt worker ownership and an idempotent startup handshake prevent two workers from spawning the same attempt. The worker persists its startup nonce/secret and claim request ID before claiming, or uses an equivalently replayable authenticated handshake. A committed claim with a lost reply must recover the same attempt and capability; it cannot mint another generation or leave the only capability stranded in that lost reply. If an ambiguous crash leaves possible external work, record uncertainty and retain its resource claims. Never infer stopped from an expired lease or nonexistent PID.

Workers run in independent sessions with file-backed logs and no daemon stdio lifetime dependency. They retain their child handles and enforce deadlines even while the daemon is unavailable. A worker renews its lease and observes durable cancel intent via C-Two. It retains an immutable local completion receipt until the service confirms the result transaction; replay uses the same attempt and command ID.

A daemon restart reconciles workers by attempt identity and unguessable capability, not PID matching. It must permit the same legitimate worker to reattach after daemon downtime without allowing a replaced generation to mutate state. If a worker RPC control endpoint is used, authenticate a nonce/challenge and identity. Alternatively use worker-initiated renew/reconcile RPC and durable commands. In either form only the worker holding the process handle sends OS cancellation.

Lease expiry marks an execution uncertain and blocks unsafe takeover. Generation checks fence stale database writes; they do not stop an old process writing files. Workspace isolation or confirmed termination is required before a replacement may touch the same resources. A dead supervisor with surviving children remains explicitly shutdown-unconfirmed; no portable exactly-once side-effect claim.

Keep `stop` as an explicit request to cancel all Buddy-owned active work and stop the service, confirming what stopped. Add `restart`/detach lifecycle for a service restart that preserves workers. Unexpected daemon exit also preserves independent workers. Do not clear worker receipts or resource ownership merely to let an upgrade proceed. Unrelated dsh sessions/processes must remain untouched. Stop rejects new claims, cancels queued tasks, persists cancellation of active attempts and drains for a bounded interval. Its response lists any unresolved attempts before daemon exit; their receipts and resource claims remain. External caller-owned workers receive a cooperative cancel request, not arbitrary OS signals.

Commit result, artifact references, task/attempt state and completion event in one transaction. Failed commits publish no success event. Result replay is idempotent; conflicting replay fails. Cancellation/completion races have one legal durable outcome. Acceptance records review of the actual result, including reviewed failure, and requires the relevant shutdown evidence; it cannot convert failure to success.

## Event delivery and inquiry

Event RPC returns committed data only, a monotonic resume cursor, and bounded pages. Use event-driven bounded waits with a check/subscribe/recheck sequence so completion between a read and subscription cannot be lost. No open database transaction or exclusive C-Two resource lock while waiting. Isolate wait capacity from mutations; many await clients must not prevent cancel/renew/result commits. The current C-Two parallel scheduling configuration may be retained if DB transitions own concurrency. Use a dedicated wait resource/route with its own bounded capacity, or explicit wait admission that returns a resumable overload error before consuming control capacity. PARALLEL alone is insufficient. Test more wait clients than the admitted capacity while renew, cancel and completion are executing.

Notifications are post-commit hints; the persisted event stream is the replayable outbox. Subscribers deduplicate and resume from cursors. A lost reply or notification does not recreate a task. App post-turn wakeup remains a client integration capability; do not claim that blackboard event delivery itself starts a Codex App turn.

dsh inquiry keeps correlation IDs, full question persistence, hash conflict checks, and queued/claimed/delivered/answered/discarded/unavailable distinctions. Delivery requires the agent's durable input evidence; answers require the correlated reply tool. Import the dsh bridge's journal as idempotent transport evidence. The existing Unix socket is allowed inside the dsh adapter because upstream's plugin runs in Node; there is no second global task manager. Unsupported adapters return an honest capability error. Preserve bounds (4,000 UTF-8 bytes for question/answer, 32/run).

## Runtime packaging and legacy records

Dependency management stays uv-only; use PyPI packages and frozen locks. The default service and its workers must execute from a versioned, content-identified stable runtime outside the Codex plugin cache, including Python environment and required dsh scripts/plugins. The cache may disappear on upgrade. Materialize only runtime assets; never copy credentials, user data, tests or an existing virtual environment. Put complete assets in the final content-addressed runtime directory before running uv frozen installation there, then mark that runtime READY. Do not move a venv or leave editable-import, interpreter, yaml bridge or dsh-plugin paths pointing into the disposable plugin source. Verify all these paths in the packaging acceptance. Retain a referenced runtime while any live/uncertain attempt needs it. New installs cannot silently replace the runtime of an already executing attempt.

Protocol health reports the runtime identity and database version. Old/new services must use an exclusion check so the new service cannot start alongside the legacy owner on the same state directory. A contract/version mismatch is actionable and must not fall back to an old implementation or destructively rewrite records.

Provide an offline, transactional, idempotent legacy import command with dry-run: require the old owner stopped and no active/uncertain execution; import original task IDs, request IDs, inputs where available, outcomes, inquiry/acceptance evidence and timestamps. Preserve old files unchanged and report counts/conflicts. Failure rolls back the entire import. Never auto-import an active legacy run as completed. Acceptance first uses a separate state directory; default installation activation happens only after the old implementation task is complete and acknowledged.

Version input fingerprints. Legacy hashes used Node JSON.stringify with a particular key order and implicit dsh defaults. Verify imported task.txt against its legacy specification, retain the original fingerprint, and support identical legacy start requests without pretending new adapter/resource fields existed. If task text is missing, preserve the record as readable history with an explicit recovery limit; never invent a specification or allow that request ID to launch a duplicate.

Port the private read-only dashboard to Python or a static client of the Python service; preserve private token, loopback origin checks and text-safe rendering. Remove the old Node engine/job manager from the active distribution and replace its tests with behavior tests of the new implementation. Keep standalone dsh runner, workspace bridge and legitimate adapter tests. Update READMEs, both skills, API reference, packaging and maintenance instructions to describe actual behavior.

## Required acceptance evidence

1. Atomicity: injected failure between state/event/artifact writes rolls everything back; reconnect sees only committed state; integrity/foreign-key checks pass.
2. Concurrency: duplicate submission/claim/completion, changed idempotency input, revision conflict, capacity and overlapping resources produce one valid result.
3. Crash windows: before spawn, after spawn before acknowledgement, after commit before RPC response; no duplicate execution or silently lost committed result.
4. Lifecycle: interrupt waiting client and crash/restart daemon during work; same attempt/worker/child and deadline persist; reconnect obtains the same result.
5. Identity: wrong capability, stale generation and diagnostic PID of a sentinel cannot mutate another attempt or kill the sentinel. Uncertain workers stay honest.
6. Delivery: cursor replay, concurrent wait/mutation responsiveness, cancellation race, question deduplication and correlated answer after daemon restart.
7. Extensibility: dsh + command adapters and a separate external worker through the public C-Two contract, with explicit unsupported inquiry behavior.
8. Packaging: fresh uv environment, staged plugin and stable runtime independent of removed plugin-source paths; no old Node engine or MCP dependency is loaded.
9. Legacy migration: dry-run, idempotent import, malformed/conflicting/active input rollback; original files unchanged; imported results and acceptance readable.
10. Self-hosted acceptance: the new skill/CLI starts a real dsh task producing a verifiable artifact; observe inquiry, restart the new daemon during its run, await the same run, independently verify artifact bytes/hash and acknowledge. A second run through the command/external adapter validates generic participation.

Run the relevant full checks once after implementation, then targeted checks for subsequent repairs. Keep machine-local logs under `.dsh-skill-build/`; commit a portable acceptance summary with commands, outcomes and explicit limitations.

## Sources and design basis

* [C-Two resources and CRM contracts](https://github.com/world-in-progress/c-two#core-concepts).
* [SQLite isolation](https://sqlite.org/isolation.html) and [WAL durability](https://sqlite.org/wal.html).
* [Transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html).

SQLite is the local deployment's deliberate database choice. PostgreSQL/HA, remote untrusted tenancy, model reasoning-state migration and native Codex App wakeup are separate capabilities and are not claimed by this implementation.
