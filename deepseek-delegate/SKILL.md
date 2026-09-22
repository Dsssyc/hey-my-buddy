---
name: deepseek-delegate
license: MIT
description: Delegate bounded coding, testing, investigation or documentation to local dsh with explicit workspaces, Host-directed assistance and verified artifacts. Use when scope and acceptance are clear; skip trivial edits and unresolved requirements.
---

# deepseek-delegate

Use a local DSH Worker for a bounded part of the user's task. The Host may implement other parts itself and retains route choice, authorization, integration responsibility and final acceptance. Workers keep their own internal tools and subagents; further Buddy work requires a Host decision.

## Choose the work and configuration

Delegate a coherent unit whose execution benefit justifies handoff, verification and possible rework. Include the objective, permitted files, required inputs, expected artifacts and actual acceptance commands. Preserve the user's authorization scope. Keep reusable instructions stable and task-specific facts concise; a higher cache-hit percentage alone does not establish a lower total cost.

Use the user's explicit model/provider/effort or an authorized profile. When comparison is needed, the [evaluation workflow](references/evaluation.md) lets a configured decision Buddy recommend a legal profile through `selection-request` → `selection-get`. Read its compact result, then authorize the separate business task. Do not load the full table/history by default or create a selector recursively. Missing or failed recommendations return the choice to the Host; they do not fail the business goal.

The runner fallbacks are `deepseek-official / deepseek-flash / max`; effort is not inherited from global settings. Check [route precedence](references/runner.md#precedence) when changing the route. Keep requested identity separate from evidence of the model actually served.

## Resolve the CLI

This directory is self-contained; install/copy all of it, including its scripts, Python package, plugins, references and uv lock. No `PLUGIN_ROOT` environment variable is supplied.

```sh
SKILL_DIR='/abs/path/to/installed/deepseek-delegate'
BUDDY="$SKILL_DIR/scripts/launch-buddy.sh"
```

Quote `"$BUDDY"` and pass one JSON argument per command. On installation or recovery, use `health` / `runtime` / `capabilities` to verify the actual service. A schema mismatch needs the [explicit offline upgrade](references/operations.md#database-upgrade), not a fallback to weaker task controls.

## Repository task flow

1. Specify `executionWorkspace`: `worktree` for independent concurrent writers, or `existing` for a sequential sole writer. Pin the input, name an integrator, declare `writeScope`, and include required untracked files explicitly. The Host must not edit a checkout held by a Worker.
2. Submit once with a stable `requestId` and a Host ID. Save the returned `runId` and private `controlFile`. Await that same logical task while doing independent Host work.
3. If `await` returns `waiting-host`, read `workflow-get`. The Worker turn ended, but the goal is unfinished. Inspect the request and fixed artifacts; approve explicit helpers, decline with a reason, or provide continuation input. Approval can authorize one automatic continuation after helper results.
4. The designated integrator applies the exact helper commits/patches and checks the combined result. Continuation reuses the allocated checkout with a fresh attempt and reconstructed DSH session; it is not native same-session resume.
5. Inspect the final artifact's real diff and run the relevant checks. Record `workflow-acknowledge` against the final artifact ID and give the user the artifact location, revision and verified outcome. Helper completion is not final acceptance.

```sh
"$BUDDY" workflow-submit '{"requestId":"<stable-id>","hostId":"<this-host>","task":"<bounded packet>","cwd":"/abs/repo","provider":"deepseek-official","model":"deepseek-flash","effort":"max","workspace":false,"timeoutSeconds":1800,"executionWorkspace":{"kind":"worktree","cwd":"/abs/repo","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["src/state.ts","tests/state.test.ts"],"integrator":"<this-host>"}}'
"$BUDDY" await '{"runId":"<runId>","waitSeconds":3600}'
"$BUDDY" workflow-get '{"runId":"<runId>"}'
# Only after independent verification:
"$BUDDY" workflow-acknowledge '{"runId":"<runId>","commandId":"<stable-review-id>","controlFile":"<returned-path>","artifactId":"<final-artifact-id>","note":"<actual checks and findings>","verdict":"accepted"}'
```

The example deliberately disables DSH session grouping with `workspace:false`. Grouping defaults to true and requires the [running workspace bridge](references/operations.md#workspace-bridge); it is independent of `executionWorkspace`. Follow a requested grouping choice and never silently change it.

Read [workflow.md](references/workflow.md) when approving helpers, continuing, taking over or cancelling a governed goal. It contains the complete packets and workspace contracts. Use `includeAudit:true` only when exact persisted inputs/results are needed.

## Control and recovery

Keep control-file contents out of prompts, helper tasks and reports. Later mutations require the explicit saved control file; a Host label is not authority. Use the current revision where required. After a lost response, replay the exact command ID and payload. A stale owner generation/revision requires re-reading and reconsidering, not adopting another Host's latest credentials.

An identical submission recovers the same task; changed input under the same request ID conflicts. A wait timeout, closed waiting terminal or interrupted connection never authorizes a second submission. Reconnect using the same `runId`. When progress is unclear, `inquire '{"runId":"..."}'` gives bounded observations; read the [inquiry contract](references/cli.md#messages-and-inquiry) before sending a correlated question.

`timeoutSeconds` bounds each spawned execution (10–86400 seconds, default 1800); queue/Host/helper waiting is separate. Each authorized continuation gets a fresh attempt deadline. `await` waits without starting work (1–86400 seconds); short `wait`/`watch` calls are bounded at 30 seconds. A wait ending does not end execution.

Cancel a governed goal with `workflow-cancel` and its control file; cancellation includes owned helpers and must await actual stop evidence. Lease expiry or a missing PID is never proof of shutdown. Restart preserves owned work and receipts; `stop` requests cancellation. Do not use a service-wide stop to resolve one task's wait timeout.

Keep this turn active while delegated work remains running. Only for user-requested work beyond the turn, register an official App heartbeat on this task using the [background flow](references/usage.md#background-work-that-outlives-the-turn). It is periodic follow-up, not immediate native App wakeup. If unavailable, keep waiting; never end with an unmonitored job or restart work the user stopped.

## One-shot work, evaluations and boundaries

Legacy `start` / `run` / `acknowledge` remain useful for ordinary non-Git or standalone one-shot tasks. They do not provide governed assistance or snapshot ownership. Read the [usage guide](references/usage.md) for those packets and monitoring. Legacy mutations cannot bypass governance.

`console` opens the private React/Vite workspace for tasks, profiles, preferences and shared evaluation cards. Viewing and `console-snapshot` make no model calls. Discovery proposes installed configurations; enabling them and choosing the initial decision profile are explicit decisions. An admitted task keeps its route when the evaluation table changes.

Follow [evaluation.md](references/evaluation.md) to record scoped experience and request bounded `evaluation-maintain` work. Automatic card publication is opt-in, not a scheduler; observations are not verified performance samples. Internal decision jobs cannot be acknowledged or retried as business tasks. Recover an uncertain request with its original ID; use a new decision request for a deliberate new attempt.

A cwd/worktree is not an OS sandbox. Existing services keep their launch permissions; tasks run with the local user's access and only within the user's authorized scope. Independent DSH sessions are not managed by Buddy. See [operations](references/operations.md) for lifecycle/recovery, [CLI](references/cli.md) for exact fields and bounds, [workers](references/workers.md) for adapters, and [architecture](references/architecture.md) before changing service contracts.
