---
name: buddy
description: Delegate bounded work to local Buddy harnesses with model routing, explicit workspaces, Host-directed assistance and verified artifacts, or update shared model evaluation cards from reviewed work. Use when scope and acceptance are clear; skip trivial edits and unresolved requirements.
---

# Hey My Buddy

Use a Buddy when its comparative advantage reduces the work or cost needed to reach a verified result. The Host retains acceptance and authorization, and may implement other parts itself. Native subagents still serve context isolation and parallel work. Workers may request help but cannot create peer Buddy goals themselves.

## Choose the relevant path

- Delegation: follow the workflow below. [Usage](../../docs/reference/usage.md) has packet examples; [CLI](../../docs/reference/cli.md) owns exact fields and output views.
- Assistance, configuration or workspace boundaries: the relevant section of [workflow](../../docs/reference/workflow.md) or [workspace lifecycle](../../docs/reference/workspace-lifecycle.md).
- A user-requested model-card update: [evaluation maintenance](../../docs/reference/evaluation-maintenance.md). Never scan task history for an ordinary delegation.
- Installation or recovery: [operations](../../docs/reference/operations.md); read [architecture](../../docs/reference/architecture.md) before changing service ownership, storage, worker or recovery contracts.
- Console: `console` opens the default browser; `console '{"browser":false}'` hands its single-use entry (valid 60 seconds) to an app browser panel. See [console](../../docs/reference/console.md); closing it does not stop tasks.
- Work objectives: the console groups governed delegations into a read-only timeline; ordinary command/external history stays under 全部执行记录. Records are read-only except the confirmed whole-objective stop; settings retain single-writer editing. Display groups do not grant Host control or prove acceptance; use CLI goal controls and fixed artifacts.
- Before delegating to Claude P1, read [Claude](../../docs/reference/claude.md): verified boundaries, isolated settings, the user's design/review division and the single-Claude-task limit. Every further native model probe needs per-run approval.

Resolve the plugin root from this file and use its `bin/buddy` with one JSON argument. This source uses contract 0.15.1/schema 12; source and installed runtime are separate facts. On setup or recovery check `health`, `runtime` and `capabilities` and use a matching launcher; routine attachment uses `ping`, and `health` is never a progress poll. Retained-data upgrades use a verified backup and a coordinated idle client/daemon/worker cutover.

Commands print a brief compact projection by default: identifiers for the next command, open boundaries, conflicts, errors, unconfirmed shutdown and truncation are kept; your own packet, workspace intent and older history are not echoed. Add `"output":"full"` only when you need a field the brief view omits, and `get` with `includeAudit:true` for specific evidence.

## Delegate through acceptance

1. Define one coherent outcome, inputs, permitted files, required artifacts and acceptance checks. Separate mechanical collection from open-ended diagnosis when they need different capabilities. Reference files, symbols and commands instead of pasting their contents; the Worker reads the checkout itself. Put task-specific facts last. A long prompt or tiny subtasks do not make delegation economical.
2. Choose `executionWorkspace` explicitly: sequential ownership may use `existing`; parallel writers need independent `worktree` checkouts, a fixed base/input and an integrator. Include the write scope. A worktree is not an OS sandbox.
3. Default to omitting `adapter`/`provider`/`model`/`effort` so the configured decision Buddy routes the task. Supply a complete tuple only for an explicit user choice, a user-established division for this repository (such as its Claude design/review roles), or recovery at this goal's routing boundary. Express a soft preference with `routingPreferences`; partial fields are hard constraints and need a task requirement. Source filenames alone never require their own harness. Inspect actual legal profiles and capabilities; never guess a default or select a selector recursively.
4. For a user agenda, the first delegation must carry `objective: {title}`; subsequent new delegations in that agenda carry its returned `objectiveId`, plus a required intent-focused delegation `title` of at most 30 characters on every new submission (describe the intended outcome, not the work process). When supported by the installed version, the first objective also carries optional `description`: one or two sentences in the user's words, at most 300 characters, display-only and excluded from Worker/selector input. Keep `objectiveId`, `runId` and `controlFile` in the handoff/context; recover a missing group id from that run's brief `get` before submitting more work. Continuations and helpers inherit the existing objective automatically: do not add unsupported fields to those commands. A different submitting Host cannot enroll in the group under the current contract; report that boundary instead of changing Host labels or inventing authority. Submit once with a stable `requestId`, recovering uncertain replies with the same identity and payload. See [objectives](../../docs/reference/objectives.md); metadata never reaches the Worker, and the control file remains private authority.
5. Wait for the same goal as described in the next section. A wait timeout or disconnected caller does not cancel execution or authorize a duplicate. Read `get` at a real decision or completion boundary; observe or `inquire` only when the answer changes the next action. Missing logs/activity or a PID alone never establishes shutdown.
6. At a boundary the owning Host can `decide` on concrete helpers or `continue` with new input. A routing boundary accepts a legal complete configuration with an explicit reason, or `reroute:true`; original hard constraints remain. Repeated infrastructure failures call for a changed, authorized recovery decision, not blind resubmission. Helpers and continuations stay in the governed graph. A turn ending for assistance releases capacity after confirmed stop but does not authorize another writer in the reserved checkout.
7. Verify the fixed artifact yourself: start from its changed paths and patch statistics, read the risky hunks, and run checks proportionate to the risk with quiet output. Record a specific rejected review when it fails, then request a bounded correction. For acceptance, record the whole goal's integration with `integration-record`, then `acknowledge` the exact `finalArtifactId` and integration ID (`notRequired` with a reason for no change). Capture material Host corrections in the review note. Process completion, model prose and an RPC reply are not acceptance.
8. Reclaim an accepted goal's managed checkout with `workspace-cleanup-plan` and `workspace-cleanup-apply`, confirming the exact returned path; patches, manifests, refs and receipts remain. Never prune unrelated worktrees.

An omitted execution duration defaults to 1800 seconds. For user-authorized long coding work set `timeoutSeconds: 0`; give helpers their own explicit duration. Cancel only owned work through its saved control; a daemon restart preserves workers, whereas service stop asks work to cancel.

## Waiting for delegated work

Report routing failures to the user with the decision code. An explicit recovery configuration applies only to this goal; it is not permission to preselect all later tasks, modify shared policy, or edit this skill to bypass routing. Check the recorded cause instead of silently normalizing degraded routing.

- **Codex**: keep the Host turn active with `await` on the same goal while its work runs.
- **Claude Code**: start `await '{"runId":"<runId>"}'` with the Bash tool's `run_in_background: true`, one per running goal, and keep working on other parts or on reviewing finished goals. The harness notifies you when the command exits, during your turn or by waking the idle session; then read `get` and verify. Do not also poll `get`, `status` or `wait` in a loop. If the session is closed, the goal keeps running; await it again by `runId`.

Only an explicit user background request authorizes a Harness/product scheduler. Never create, mute or remove an automation merely to save quota or calls, and do not restart work the user stopped. The blackboard provides no native post-turn App wakeup.

## Capabilities and authority

Read the actual capability report rather than treating harnesses as interchangeable. DSH offers correlated inquiry. ZCode answers cooperatively at a root tool checkpoint or finish attempt; a delayed answer never authorizes restarting. Native permissions require Host attention. Codex exposes activity and bound continuation, without inquiry. DSH reconstructs a fresh session on continuation; ZCode and Codex resume only proven native bindings. Claude P1 requires first-party Anthropic authentication, uses the user-approved isolated settings, reconstructs every continuation and reports quota exhaustion as a bounded failure without retry; requested/resolved settings and attested served-model or effort values remain different facts.

Routing and execution share a machine-wide attempt ceiling (default 8) and each exact adapter/provider/model family's user-set limit (default 2); effort variants share it and unresolved shutdown keeps its slot. These count Buddy attempts, not provider API requests. Capacity waiting is not a failure or a reason to submit again. User preferences, annotations, enablement, capacity and the selector configuration are user-owned: neither Host nor Worker may change them silently or edit this skill to bypass routing; user changes go through the authenticated console.

## Maintain shared model cards

Only when the user requests an update, a Host prepares facts with `evaluation-prepare` (no model call, no lease), synthesizes changed cards and publishes them with a short `evaluation-write-begin(kind: maintenance)` grant and `assessment-publish`. A Worker must not strip or borrow credentials to impersonate that role, though a credential-free synthesis proposal may be delegated. Never turn CI failures, provider limits or cancelled work into invented model-performance samples, and preserve user policy, annotations and unrelated cards. Page views and acknowledgements never trigger a model call; recurring maintenance needs an explicitly requested Harness schedule. The [maintenance reference](../../docs/reference/evaluation-maintenance.md) owns fields, bounds, conflict recovery and verification.

## 0.16.0 operating rules

State the user's language explicitly in every Worker task packet and require the result summary in that language; this repository uses Chinese. Every delegation in an agenda retains its objectiveId and a short intent title. The browser never translates results or invokes a model for wording. Existing Codex await behavior is unchanged.

The 0.16.0 target uses service-owned `buddy backup` and new-package `buddy upgrade` with one verified rolling `backups/current/`, automatic failure rollback and idle-only cutover. Never make ad-hoc whole-state/source archives. Installation requires separate user authorization. Storage plan is read-only; apply requires confirmation of its exact guarded plan. The console uses a stable bookmarkable loopback URL and renewable persistent login; all authenticated windows can edit settings subject to revision conflict checks. Source verification and actual installation remain separate facts.
