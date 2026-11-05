# Using hey-my-buddy

This page follows one agenda from installation to a reviewed result under contract 0.61.0 ([ADR-032](../decisions/032-host-interface-delegation-loop.md)). [The CLI reference](cli.md) explains the Host, operations and role entry points; local `help` supplies their current fields and legal combinations. [Workflow](workflow.md) owns the lifecycle, [operations](operations.md) owns installation and recovery, and [architecture](architecture.md) describes the service.

## The common path

Create a macro task, submit a bounded micro task, await it, answer any boundary with `continue`, and review the fixed output before `accept`. Each micro task keeps one `runId` through its turns. Admission and a Worker's completion message are not acceptance.

### 1. Install once

You need [uv](https://docs.astral.sh/uv/) and a working local DSH, ZCode, Codex or Claude Code CLI with its own provider credentials. The runtime uses Python 3.12; uv can install it. The blackboard uses the user's harness configuration and login.

```sh
/abs/path/to/hey-my-buddy/skills/hey-my-buddy/scripts/hey-my-buddy install
HMB="$HOME/.agents/skills/hey-my-buddy/scripts/hey-my-buddy"
```

The shared skill lives in `~/.agents/skills/hey-my-buddy`, with a Claude Code link at `~/.claude/skills/hey-my-buddy`. Installation prepares a stable runtime separately from the source and skill. Editing a checkout does not change an installed service. Follow [installation](operations.md#installation) for distribution, upgrade and verification.

### 2. Choose the execution workspace

`cwd` is an absolute existing directory and determines the project. A project is a Git repository or an ordinary folder identified by its canonical path. Moving or renaming an ordinary folder creates a new project; initializing Git at its recorded root keeps its project identity. A Git project's marker travels with the repository; [relocation](operations.md#host-operations) records its new location.

Choose `checkout: "worktree"` for an independent Git worktree or `"existing"` for sequential work in the source checkout. Both capture the Host's current working tree, including tracked staged and unstaged content and the untracked paths explicitly named by `inputs`. Supply `inputs: []` when none are needed. Independent writers use separate worktrees; do not create a Git repository just to ask a question.

With `checkout: "none"`, the Worker works directly in `cwd`, whether it is a Git repository or an ordinary folder. Nothing is copied or sealed; each turn fixes only its returned result by location and digest. Directory changes under `write` or `full` remain unsealed and the Host must inspect them itself. Omit `inputs` and `writeScope`. Use a directory you prepared empty when the Worker should have no task files to read. Deleting the macro task does not delete this directory.

`access` is explicit: `read`, `write` or `full`. Git writes require a nonempty checkout-relative `writeScope`; `read` forbids it. Only `full` takes the required single-line `accessNote`. Permissions govern writes and execution through the harness's own mechanisms; they do not bound what it can read, and a checkout is not an OS isolation boundary. Task text and content sent by a Worker reach its model provider. Keep confidential content out of the brief. Native enforcement and measured platform limits belong in [workers](workers.md), [Codex](codex.md) and [Claude Code](claude-code.md).

### 3. Write a bounded packet

Use one macro task per user agenda, created separately with its title and goal. It can remain empty. Each micro task supplies its own intent-focused title and brief: inputs, expected output, permitted changes, non-goals, acceptance checks, plan, references and known pitfalls. The brief must say when the Worker should stop and return assistance or attention, including a cause outside its scope or a disagreement between code and the agreed plan. [Writing the brief](workflow.md#writing-the-brief) explains that boundary.

Use `buddy: "route"` unless the user has named a buddy; a named buddy is written `harness / provider / model / effort`, as the console's Buddy page lists it, and CLI replies write every buddy the same way. Routing uses enabled legal candidates and the user's settings, and returns its reasons on this micro task. The console owns model discovery and user preferences. [Decision](decision.md) owns routing; routing trouble does not authorize changing user settings.

Supply `expectedSeconds` as an integer from 1 to 86400. It is a reminder and Router input, never a deadline and never a reason to stop the Worker. Include `fixes` only when repairing an accepted micro task, and grant `delegationAuthority` only at the user's explicit request ([Lead](#a-lead-for-a-large-macro-task)).

### 4. Submit the micro task once

```sh
"$HMB" create '{"cwd":"/abs/repo","title":"State kernel reliability","goal":"Every state transition is atomic and recoverable after a crash."}'
"$HMB" submit '{"cwd":"/abs/repo","macroTaskId":"<macroTaskId>","title":"Implement the state transition","task":"Implement the agreed transition in src/state.ts, add regression coverage in tests/state.test.ts and run the package checks. Stop with attention if the plan disagrees with the code or a necessary change is outside scope.","buddy":"route","checkout":"worktree","access":"write","writeScope":["src/state.ts","tests/state.test.ts"],"inputs":[],"expectedSeconds":3600}'
```

Save `macroTaskId` and `runId`. Use the literal `"standalone"` as the macro task selector when a micro task has no macro task. Long packets can use `--params-file PATH` or `-` for stdin in place of positional JSON; these input forms are mutually exclusive and read one UTF-8 JSON object. Run `"$HMB" help submit` locally for current fields and combinations; help starts no service or model.

The service derives a creation key from project and title for `create`, and from macro task (or project for standalone work), title and brief for `submit`. Resending returns the original with `alreadyExists`, including its actual authorization and execution choices. A replay does not apply new permissions or select another buddy. Change the title to do the work again. After creation, actions name the exact request, artifact or failed turn they answer; an identical action replays, and a different answer to an already answered id conflicts.

### 5. Await that same logical run

```sh
"$HMB" await '{"runId":"<runId>","waitSeconds":3600}'
```

`await` observes this run and returns a boundary, delivery, reminder or window end. It creates no work and its window never stops an execution. Follow [host.md](host.md) for waiting and acting on the reply's ids and `actions`; the [envelope reference](cli.md#await-envelope) explains the response facts.

### 6. Continue at the boundary

Read `get` when a reply needs a decision or inspection. Answer the returned `requestId` with `continue`: approve or decline a boundary, authorize complete helper packets, or provide continuation input. To send a delivered result back, answer its `artifactId` with input describing the acceptance problem. After a failed turn without a fixed result, use its `turnId`. A new reply's ids and legal actions supersede assumptions remembered from an earlier view.

```sh
"$HMB" continue '{"runId":"<runId>","artifactId":"<artifactId>","input":"The regression case still fails. Fix it in the authorized paths and rerun the focused checks."}'
```

A continuation can also select `buddy: "route"` or a complete buddy name, change access or future write scope, or resolve a recorded workspace site. A buddy change requires a note; a running turn must first stop. With live helpers, explicitly choose `helperPolicy: "keep"` or `"cancel"`. [Workflow](workflow.md#host-boundaries-and-requests) covers helpers, [manual continuation](workflow.md#manual-continuation) covers resume, and [workspace lifecycle](workspace-lifecycle.md) covers site resolution. Provider failures retain their class and partial-work evidence; never create a duplicate to hide a routing or provider boundary.

### 7. Inspect the final artifact and accept it

```sh
"$HMB" get '{"runId":"<runId>","output":"full"}'
"$HMB" accept '{"runId":"<runId>","artifactId":"<finalArtifactId>","note":"Compared the brief, report and fixed output, integrated it and ran the combined regression checks.","target":{"path":"/abs/target/checkout","ref":"main"},"beforeCommit":"<target-commit-before-integration>"}'
```

Compare the frozen brief and later Host inputs, inquiries and answers with the report and fixed output. `finalArtifactId` names the delivered artifact. For Git work, use its `integrationSource`: a verified Worker commit interval (`baseCommit`, `headCommit`) or a retained patch (`path`, `sha256`). `sealedEvidence` identifies the snapshot; it does not tell you to merge an evidence-only commit. Integrate the stated source, then verify the combined target yourself.

Acceptance requires the real target path and ref with its commit before integration, unless a tip merge containing the artifact proves that previous target from its parents. The service uses Git's three-way merge calculation (Git 2.40 or newer). Real Host adjustments require `adjusted: true` and an explanatory note; separate additions outside artifact paths use `hostPaths`. If integration is unnecessary, supply a nonempty `notRequired` reason instead of a target; this is the only acceptance form for a micro task without a checkout. For a pure answer the report and fixed result can be the same content, leaving two things to compare. [Workspace lifecycle](workspace-lifecycle.md) owns the full verification and cleanup boundary.

### 8. End a micro task without accepting it

```sh
"$HMB" conclude '{"runId":"<runId>","artifactId":"<artifactId>","note":"Reviewed the stopped result; this work will not be accepted or continued."}'
```

Use `conclude` for stopped failed, cancelled or delivered work that will not continue, with `runId` and a note. Delivered work requires its current delivered `artifactId`. Failed or cancelled work may omit that field, or name an unanswered fixed result from its current turn; supplying it binds the conclusion to that exact result. Stop evidence, unresolved sites and live descendants still constrain conclusion. Acceptance and conclusion release reservations; the service retries blocked managed-checkout cleanup automatically. An ordinary existing checkout and a directory used without a checkout are never removed by that cleanup.

## Execution records

Hosts delegate through micro tasks. Execution records are internal attempt infrastructure, including model-free check executions and Router calls. They do not provide a second public submission path.

## Progress and questions while it runs

```sh
"$HMB" inquire '{"runId":"<runId>"}'
"$HMB" inquire '{"runId":"<runId>","question":"Which check is currently blocking completion?"}'
"$HMB" inquire '{"runId":"<runId>","inquiryId":"<inquiryId>"}'
```

Observation and inquiry do not continue or cancel a turn. The service derives the question's identity; the same question on the same run returns its first inquiry and answer. Change the wording to ask again. A queued or unavailable native channel is reported as such; a question's delivery is not proof of an answer. Use the reply's inquiry id to retrieve it. The durable wait remains the normal supervision path.

## When the wait ends before the task

`wait-timeout` and `unavailable` end only that wait. Expected-duration and native-silence replies are reminders to inspect and wait again, not failure or shutdown evidence. Execution and service issues are readable facts; this batch adds no return-on-new-issue wakeup. Reconnect to the same `runId` and follow [host.md](host.md).

## Cancellation and stopping

`cancel` uses `runId` and an optional note. It records ordered cancellation across the owned graph, including routing and helpers; its reply never proves every process stopped. `get.shutdown` separates self and descendant confirmation. Unknown is not stopped. Continue only after the required stop evidence holds, or conclude stopped work that will not resume. [Operations](operations.md#cancellation-and-recovery) owns service restart and process recovery.

## Waiting from your Host

Follow [host.md](host.md), which owns the wait loop for every Host and links to the Codex and Claude Code instructions for their native waiting mechanisms.

## Background work that outlives the turn

Work may outlive a Host turn only when the user explicitly requested that background behavior. Name each open micro task, its `runId` and what it waits for in the final reply. An idle or closed Host session is not automatically woken by the blackboard. Any authorized scheduler belongs to the Host harness's own mechanism; the blackboard creates no scheduler and makes no promise of a later Host decision. The wait and recovery rules remain in [host.md](host.md).

## More than one task at a time

Delegate independent outcomes under the same macro task. Give concurrent Git writers separate worktrees and name the integrator of their outputs. Keep each run's identifiers and follow [host.md](host.md) for waiting on concurrent work and finding unfinished work with a project or macro task `get` before ending the turn. A project view includes work from other sessions; coordinate the runs this Host is handling.

## A Lead for a large macro task

Only an explicit user request authorizes a Lead. Submit with `delegationAuthority: true`, a complete named buddy and `access: "full"` with its reason. The Lead uses execution credentials to submit and accept its own micro tasks and returns one result for the Host to review. It never talks to the user. [Workflow](workflow.md#delegation-authority-lead) owns its role, waiting and descendant-stop rules; a Lead's report still requires Host acceptance.

## Operating rules

Keep the user's login, harness settings and preferences under the user's control. Operational repair belongs in [Host operations](operations.md#host-operations). Worker and Lead calls remain credential-scoped. Ordinary Hosts have no per-run control credential: two Hosts can address the same micro task, and only conflicting later actions are rejected. Coordinate ownership of work explicitly and review fixed evidence before acceptance.
