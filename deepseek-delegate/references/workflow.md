# Governed productivity workflow

Use this workflow when a repository task needs explicit workspace ownership, assistance, continuation and final acceptance. The Host can implement part of the goal itself. A Buddy chooses its own internal tools and subagents; only a Host decision creates another Buddy task. All executions use the existing Python blackboard, C-Two operations, ordinary Worker capacity and durable completion receipts.

## Submit and keep ownership

The CLI fields for the ordinary task specification remain flat. `executionWorkspace` is a separate object from `workspace`, which only controls DSH session grouping. Choose a stable request ID, a Host ID representing this controller and the complete packet before submitting.

```sh
"$BUDDY" workflow-submit '{"requestId":"state-kernel-1","hostId":"codex-state-kernel","task":"Implement the agreed state transition in src/state.ts, add regression tests in tests/state.test.ts and run the package tests. Preserve unrelated files. Report completed only after checks; request assistance or attention through buddy_finish_turn if a bounded dependency is missing.","cwd":"/abs/repo","provider":"deepseek-official","model":"deepseek-flash","effort":"max","workspace":false,"timeoutSeconds":28800,"executionWorkspace":{"kind":"worktree","cwd":"/abs/repo","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["src/state.ts","tests/state.test.ts"],"integrator":"codex-state-kernel"}}'
"$BUDDY" await '{"runId":"<returned-runId>","waitSeconds":28800}'
"$BUDDY" workflow-get '{"runId":"<returned-runId>"}'
```

Save `runId` and `controlFile` from submission. The latter is an owner-private credential file; later control commands require this exact file or the explicit control triple. A Host name is attribution and never enough authority. Do not copy control tokens into model prompts, reports or helper tasks. The CLI keeps submission-recovery credentials privately so a lost initial response can recover the same task without granting a different Host's current capability.

Replay the exact submission with the same request ID after an uncertain response. A changed workspace, input or route is a new request, not an idempotent retry. A successful RPC is admission evidence only. `workflow-get` is compact by default; use `includeAudit:true` only when reviewing exact turn inputs, results, decisions and artifact manifests.

## End of a turn

The DSH root agent receives `buddy_finish_turn`. A successful call concludes its ordinary agent turn and records one of `completed`, `assistance` or `attention`. It does not block the process awaiting a helper. Plain final text, an internal subagent result or a failed enclosing tool call cannot substitute for this result.

The adapter correlates the result to the root's first ordinary user prompt, tool execution and completed native turn, then writes at an awaited session-flush boundary. The Worker imports it only with the correct task/attempt/turn identity, successful runner exit and confirmed shutdown. Files are sealed after shutdown; a reported model path is not by itself a verified artifact.

`await` returns `outcome: "waiting-host"` when the goal reaches a decision boundary. Inspect `activeRequest`, the current turn summary, remaining work and immutable artifacts. The goal remains unfinished. Waiting for already-authorized helper work does not require starting a new parent task.

## Approve or decline assistance

Approve concrete helper packets with explicit configurations and workspace intents. Use the revision from the latest `workflow-get`, the request ID from `activeRequest`, and one stable command ID. Below, the parent remains the named integrator and helpers work in separate snapshots.

```sh
"$BUDDY" workflow-decide '{"runId":"<parent>","requestId":"<activeRequest.requestId>","commandId":"approve-tests-1","expectedRevision":7,"controlFile":"<parent-controlFile>","decision":"approve","reason":"The parent needs independent concurrency coverage before integration.","autoContinue":true,"helpers":[{"requestId":"state-tests-helper-1","task":"Add concurrency tests for the fixed state implementation. Modify only tests/state.test.ts and run the focused checks. Return the resulting artifact.","cwd":"<parent-workspace-path>","provider":"deepseek-official","model":"deepseek-flash","effort":"max","workspace":false,"timeoutSeconds":28800,"executionWorkspace":{"kind":"worktree","cwd":"<parent-workspace-path>","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["tests/state.test.ts"],"integrator":"<parent>"}}]}'
"$BUDDY" await '{"runId":"<parent>","waitSeconds":28800}'
```

Set `autoContinue:false` to return the result to Host control before the next parent turn. `true` authorizes one continuation for this decision; it does not authorize recursive delegation. Success, failure, cancellation and helper attention all have follow-up paths. A helper that needs another decision is surfaced while other helpers may still be running.

Decline with a reason when the Host has supplied the missing work or chooses a different approach. Declining does not fail the goal:

```sh
"$BUDDY" workflow-decide '{"runId":"<parent>","requestId":"<active-request>","commandId":"decline-1","expectedRevision":7,"controlFile":"<controlFile>","decision":"decline","reason":"The Host has supplied the verified fixed commit; use it in the next turn.","helpers":[],"autoContinue":true}'
```

The next execution has a fresh attempt and DSH session. Its `resumeMode` is `reconstructed-new-session`; context carries the original goal, previous checkpoint, decision, helper outcomes and pinned artifacts. No full session-history scan or original-session-resume claim is involved. The designated integrator applies the fixed helper commits or patches and verifies the combined result. Helper completion and integration acceptance are separate.

## Manual continuation and takeover

Manual continuation records new input on the same logical goal and invalidates old automatic triggers. With active helpers, explicitly choose `helperPolicy: "keep"` or `"cancel"`; cancellation must still wait for actual shutdown before conflicting writes resume.

```sh
"$BUDDY" workflow-continue '{"runId":"<parent>","commandId":"continue-1","expectedRevision":9,"controlFile":"<controlFile>","input":"Integrate the pinned helper output and rerun the state regression checks.","helperPolicy":"keep"}'
"$BUDDY" workflow-takeover '{"runId":"<parent>","commandId":"takeover-1","expectedOwnerGeneration":1,"newHostId":"codex-follow-up","controlFile":"<old-controlFile>"}'
```

Takeover rotates the control capability and owner generation without restarting the current execution. Keep the new returned `controlFile`; late operations by the old generation are rejected. An authenticated user can make an explicit decision or takeover in the private console. Browser JSON flags cannot claim user authority.

Continue and retry have different meanings. Continuation consumes new input after a stopped execution; retry repeats a failed attempt. Legacy cancel/retry/acknowledge commands cannot bypass governance.

## Workspace and artifact rules

Every governed workspace names `kind`, source `cwd`, `access`, `base`, `includeUntracked`, `writeScope` and `integrator`. Paths in the latter two lists are relative to the checkout root; `.` explicitly allows the whole checkout. A write workspace needs a nonempty scope. This is an ownership and output-verification contract, not an OS sandbox.

| Arrangement | Fixed input and ownership |
| --- | --- |
| `worktree`, `base.kind: commit` with `ref` | Resolve the ref once and create an isolated detached worktree from that commit |
| `worktree`, `base.kind: working-tree` | Capture tracked staged/unstaged content plus explicitly included untracked files in a private index; preserve the source HEAD, index and files |
| `existing` | Explicitly use the source checkout and reserve its actual checkout identity, including sibling cwd paths |
| `access: read` | Require the input to remain unchanged; multiple readers can share one unchanged snapshot |

Worktree isolation does not isolate shared repository metadata, external services or databases. Declare shared resources and keep one integrator for a common target. Independent writers use separate worktrees. Sequential transfer of an existing checkout requires a stopped parent and an explicit Host allocation. Attempt slots and logical workspace reservations have different lifetimes; a yielded parent can release execution capacity while retaining its checkout.

The input manifest records exact input commit/tree, staged/unstaged hashes, included and excluded untracked paths and canonical checkout/repository identities. Output sealing preserves a fixed commit, tree, diff, changed paths and output snapshot hash. `manifestSha256` in the raw seal refers to its input manifest; `snapshotSha256` binds that output. New continuation preparation uses a fresh preparation identity and the prior sealed commit; it never rewrites the original submission snapshot. Post-seal drift is a conflict requiring explicit handling.

Managed new files outside declared write scope and unsupported non-Git snapshot cases are reported explicitly. Git-ignored dependencies, environments and caches are excluded by the recorded `exclusionPolicy`; they are not read or hashed unless explicitly selected in `includeUntracked`. Stability and scope checks cover that managed set. Sealed Git refs keep artifacts available independently of live worktree paths. Preparation, verification and sealing do not merge, delete or force-clean user workspaces.

## Review, cancel and recover

Review the final attempt's fixed artifact and its checks. Acceptance is tied to that artifact, not whichever branch or directory happens to exist later.

```sh
"$BUDDY" workflow-acknowledge '{"runId":"<parent>","commandId":"review-1","controlFile":"<controlFile>","artifactId":"<final-output-artifact>","note":"Inspected the fixed diff and ran the combined regression suite.","verdict":"accepted"}'
"$BUDDY" workflow-cancel '{"runId":"<parent>","commandId":"cancel-1","controlFile":"<controlFile>","reason":"User cancelled the goal and its helper work."}'
```

Cancellation records durable intent for the parent and its helpers and revokes automatic continuation. Missing PIDs, lease expiry and lost connections never prove shutdown. A stopped wait does not cancel a run; reconnect using the same run ID. After a daemon restart, the existing Worker reattaches by attempt identity and replays its durable receipt. Use the [operations guide](operations.md) for runtime/schema upgrades and [CLI reference](cli.md) for bounds and errors.

The private console shows goal/turn ownership, decision requests, helper tasks, continuation mode and fixed artifact references. Mutations use the same business operations as the CLI, with revision checks and stable command IDs. Page refresh makes no model call. The current installed/runtime version and acceptance evidence should be checked before assuming a development checkout is active.
