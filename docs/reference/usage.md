# Using Buddy

This page is the practical path from install to reviewed result. Read [cli.md](cli.md) for every command, field, default, bound and error, the [governed lifecycle](workflow.md) for routing, Host control, request queues and workspace contracts, [operations.md](operations.md) for runtime lifecycle, the DSH workspace bridge, private state and recovery, and [architecture.md](architecture.md) for how the service works internally.

## The common path

For coding work, use the governed lifecycle. Its normal path is one logical `runId`: `submit` → `await` → a `waiting-host` decision or continuation → `acknowledge` against the final artifact. The advanced `execution-*` commands serve the `command` and `external` adapters and internal decision infrastructure; they are not a hidden path to ungoverned coding work.

### 1. Install once

You need macOS or Linux, [uv](https://docs.astral.sh/uv/) with Python 3.12–3.14, Node.js 20+ for the DSH runner, a working local `dsh` and/or ZCode installation with its provider credentials, and Codex plugin support. Buddy uses the harness credentials already configured and leaves global model settings alone.

Install the `hey-my-buddy` plugin from this repository's own marketplace; the committed `.agents/plugins/marketplace.json` serves both a local checkout and a Git clone, so no personal marketplace and no public listing is needed.

```sh
codex plugin marketplace add /abs/path/to/hey-my-buddy   # or Dsssyc/hey-my-buddy --ref main
codex plugin add hey-my-buddy@hey-my-buddy
```

[Installation](operations.md#installation) owns the staged local package, the existing-marketplace path and the post-install verification steps.

Start a new task so the `$buddy` skill loads. The skill resolves the plugin's own `bin/buddy` launcher; there is no separate skill directory to install, no old-layout fallback and no standalone distribution.

From the plugin root, resolve the launcher rather than assuming a working directory:

```sh
BUDDY="<absolute-plugin-root>/bin/buddy"
```

The first command that needs the service installs a content-addressed stable runtime under `~/.local/share/hey-my-buddy/runtime`, so replacing the plugin later does not disturb running work. A fresh board lives under `~/.local/share/hey-my-buddy/state`. An older board directory at the previous default location is retained as an archive: this release does not read, convert or import it, and it refuses any schema other than the current one. See [operations.md](operations.md#runtime-lifecycle).

### 2. Choose grouping and the execution workspace

`workspace` is a boolean and only controls DSH session grouping; it defaults to `true`. Either install the DSH workspace bridge once ([operations.md](operations.md#workspace-bridge)) and keep grouping, or pass `"workspace": false` for an intentionally standalone run. The default is never silently downgraded; a grouped run fails honestly when the bridge is missing.

Independent of grouping, governed coding work names its Git isolation contract in a separate `executionWorkspace` object: `kind` (`existing` or `worktree`), source `cwd`, `access` (`read` or `write`), `base` (`commit` with `ref`, or `working-tree`), `includeUntracked`, `writeScope` and `integrator`. It is an ownership and artifact-verification contract, not session grouping and not an OS sandbox: concurrent writers use independent worktrees, a read workspace must keep its input unchanged, and declared paths are checkout-root-relative. The fixed-input matrix and reservation rules are in [workflow.md#workspace-and-artifact-rules](workflow.md#workspace-and-artifact-rules).

### 3. Write a bounded packet

A good packet lets the worker finish without guessing:

- goal and why; expected inputs and outputs;
- the working directory and the commands that check the work;
- files/areas that may change, plus explicit non-goals and the expected final artifact;
- acceptance criteria: the observable result and the checks to run;
- key references, known facts and pitfalls;
- as much relevant context as the task needs. Task text is bounded at 1 MiB; the DSH runner delivers content over 32,000 bytes as a file reference, and that file must stay in place until the run finishes.

For a governed task, state the workspace intent in the packet too: which checkout or worktree, whether access is read or write, and who integrates helper output.

### 4. Submit the governed goal once

```sh
"$BUDDY" submit '{"requestId":"state-kernel-1","hostId":"codex-state-kernel","task":"Implement the agreed state transition in src/state.ts, add regression tests in tests/state.test.ts and run the package tests. Preserve unrelated files. Report completed only after checks.","cwd":"/abs/repo","timeoutSeconds":28800,"workspace":false,"executionWorkspace":{"kind":"worktree","cwd":"/abs/repo","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["src/state.ts","tests/state.test.ts"],"integrator":"codex-state-kernel"}}'
"$BUDDY" get '{"runId":"<runId>"}'
```

Save `runId` and `controlFile` from the submission. The control file is an owner-private `0600` credential bound to that run and owner generation; later Host commands need this exact file, or the explicit `hostId` + `ownerGeneration` + `controlToken` triple. Never copy control tokens into task text, helper prompts, reports or another Buddy, and never adopt another Host's saved capability. A Host name is attribution and never authority.

Ordinary submit fields stay flat. A complete explicit `adapter`, `provider`, `model` and `effort` quadruple is validated and dispatched directly; a partial quadruple is a hard filter; omitted fields are routed by the configured decision Buddy through the bounded current evaluation table. A goal may also carry task-local `routingPreferences`: at most eight ordered `{match, reason}` soft preferences that apply only to that goal and to helpers explicitly inheriting them, never to global preferences or another task. There is no silent DSH default, and the initial fixed decision profile is chosen by the user ([evaluation.md](evaluation.md#fresh-board-and-the-initial-decision-profile)). A programmatic caller persists its own `submissionToken` before the first RPC so a lost reply still recovers its original control. `workspace` only means session grouping; `executionWorkspace` is the Git contract described above. Replaying the identical submission with the same `requestId` recovers the same logical task, while changed workspace, input or Host under that ID is a `CONFLICT`; a successful RPC is admission evidence only. `get` is compact by default; use `includeAudit:true` only when reviewing exact turn inputs, results, decisions and artifact manifests.

### 5. Await that same logical run

```sh
"$BUDDY" await '{"runId":"<runId>","waitSeconds":28800}'
```

`await` never starts work. It stays connected to the durable goal until a Host decision boundary, a terminal outcome or this call's window end (`waitSeconds` 1–86400, default 86400), and prints one envelope. Its initial status read attaches to a healthy service or starts one if none is running, but it never creates a task. Keep the current Codex turn waiting instead of starting a second run or beginning work on the same files.

The normal boundary is `outcome: "waiting-host"`: a routing boundary may have no agent turn or result yet, and `goalComplete` is `false`, and the envelope names the current `request`, `turn`, pinned artifacts and `nextCommands`; the goal stays unfinished. Waiting on already-authorized helper work can continue inside the same wait. Terminal task outcomes, `wait-timeout` and `unavailable` are covered below and in [cli.md](cli.md#await-envelope).

### 6. Decide, continue or reroute at the boundary

Read `activeRequest`, the routing object, the current turn summary and fixed artifacts with `get`. The Host can approve concrete helper work, decline with a reason, provide new continuation input, supply a complete configuration at a routing boundary, or reroute the same goal after fixing the selector. Use the current revision and saved control file. A helper that depends on the parent's changes should start from the parent's sealed output commit, not an unrelated copy of the original checkout.

The [assistance examples](workflow.md#host-boundaries-and-requests) provide complete helper packets and explain one-use automatic continuation. The [continuation and takeover examples](workflow.md#manual-continuation-and-takeover) cover new Host input, active-helper policy, `targetRunId` for an owned descendant, and transfer of control. Re-read after each decision: another pending request can become active. Nested requests retain their origin and are authorized by the Host.

Each continuation gets a fresh attempt and recorded resume mode: DSH reconstructs a fresh session, while ZCode resumes its exact proven native session or explicitly reconstructs a new one after an unproven prior turn. The named integrator applies exact helper commits/patches and checks the combined result. The private console exposes the same decisions and ownership checks.

### 7. Inspect the final artifact, record integration and acknowledge

```sh
"$BUDDY" get '{"runId":"<runId>"}'
"$BUDDY" integration-record '{"runId":"<runId>","commandId":"integrate-1","expectedRevision":7,"controlFile":"<controlFile>","artifactId":"<finalArtifactId>","strategy":"cherry-pick","target":{"path":"/abs/target/checkout","ref":"main"},"beforeCommit":"<target commit before the integration>","verification":"Applied the sealed artifact commit to the target and ran the combined checks."}'
"$BUDDY" acknowledge '{"runId":"<runId>","commandId":"review-1","controlFile":"<controlFile>","artifactId":"<finalArtifactId>","integrationId":"<returned integrationId>","note":"Inspected the integrated target diff and ran the combined regression suite.","verdict":"accepted"}'
```

Once the goal is delivered (the `get` view reports `state: "delivered"`, exposed as `workflowState` on task and wait envelopes), `finalArtifactId` names the selected fixed output. Inspect the actual output commit/diff, changed paths, hashes and logs yourself, then record what you really checked. An `accepted` verdict requires a verified integration record or an explicit `notRequired` record bound to that artifact; the service resolves the target commit/tree relationship from the repository itself rather than trusting a client hash, and `get` exposes the recorded `integrations`. A task with no repository change records `{"notRequired": true, "reason": "..."}` instead. Acceptance is separate from execution, is tied to that artifact rather than to whichever branch or directory exists later, and never converts a failed execution into a success. A governed wait's `shutdownConfirmed` requires both self and descendant stop evidence, so a delivered or cancelled goal whose descendants are unconfirmed keeps waiting instead of inventing a stopped state.

### 8. Reclaim the managed checkout

After acceptance and integration, plan and apply the removal of exactly this run's registered managed checkout:

```sh
"$BUDDY" workspace-cleanup-plan '{"runId":"<runId>","commandId":"cleanup-plan-1","expectedRevision":7,"controlFile":"<controlFile>"}'
"$BUDDY" workspace-cleanup-apply '{"runId":"<runId>","planId":"<returned plan.planId>","commandId":"cleanup-apply-1","expectedRevision":7,"confirmPath":"<returned plan.path>","controlFile":"<controlFile>"}'
```

The plan names the exact path, its eligibility evidence and retention list and expires after 900 seconds; apply rechecks ownership, acceptance, integration, shutdown, dependencies and Git state and deletes only that checkout, keeping outputs, manifests, fixed refs and receipts. A plan with reasons is blocked, not authorizing; re-read the run and plan again. A repaired workspace conflict is settled first with `workspace-resolve` or `scope-amend` as described in the [workspace lifecycle reference](workspace-lifecycle.md), which owns the complete example and field list.

## Non-coding execution records

The `command` and `external` adapters use the advanced `execution-*` records; they have no governed request queue or Host control file, and use execution-level result review rather than goal artifact acceptance. Coding adapters are refused here with `GOVERNED_REQUIRED`.

**`command` adapter.** One explicit `argv` process, never a shell:

```sh
"$BUDDY" execution-submit '{"requestId":"echo-1","task":"print a greeting","cwd":"/abs/path","adapter":"command","argv":["/bin/echo","hi"]}'
```

**`external` adapter.** No local process: the caller's own agent claims the task through the public client and submits the result itself. A runnable example, worker identity and receipt rules are in [workers.md](workers.md#a-runnable-external-worker).

Inspect and review these records with `status`, `result`, `artifacts`, `wait`, then `execution-acknowledge`. The worker owns the child handle, enforces the deadline and reports confirmed shutdown; `execution-retry` requires that confirmed stop before it can requeue a new generation.

## Progress and questions while it runs

```sh
"$BUDDY" inquire '{"runId":"<runId>"}'
"$BUDDY" inquire '{"runId":"<runId>","inquiryId":"q-1","question":"What are you waiting on?","waitMs":20000}'
```

The no-question form is bounded observation: execution state from the durable record, an explicitly estimated deadline, recent activity and the bounded native-activity projection on the task view (`phase`, last native/tool activity times, event sequence, tool name, waiting reason and honest counters). It is not a percentage and not a guaranteed ETA, and fields the bridge could not observe are named in `live.unavailable` rather than reported as zero. An empty runner log, a static session list, a missing PID or an expired lease never proves the process stopped; only real shutdown evidence does.

A question goes to the run's own live agent only when its adapter declares `inquiry`. DSH answers through its correlated reply tool; plain assistant text is not an answer. ZCode declares only `observe`: read-only activity is available, and questions receive an unavailable result without native input or a new turn. `command`, `external` and `codex` mount no inquiry bridge. Questions and answers are bounded at 4000 UTF-8 bytes and 32 inquiry identities per task. Repeating the same ID and text reads its recorded state; changed text conflicts. See [CLI](cli.md#messages-and-inquiry) for the full contract.

Ask when the user asks or when you need the answer to report honestly; do not build a polling loop. `wait` and `watch` are short bounded waits (at most 30 s) for nearby changes on a service that must already be running; they never cold-start a service and are not a substitute for the durable `await`.

## When the wait ends before the task

A deadline longer than the wait window is allowed: an execution deadline may be up to 24 hours while a single `await` window is capped at 24 hours too, and either can end first. A call that runs out of wait returns `outcome: "wait-timeout"` with the run still active, a `limitation` string and a `recovery` block naming real commands and the existing run ID. That is a wait/connection limit, never an execution failure and never a reason to relaunch.

```sh
"$BUDDY" status '{"runId":"<runId>"}'
"$BUDDY" await  '{"runId":"<runId>","waitSeconds":28800}'
"$BUDDY" result '{"runId":"<runId>"}'
```

Killing the waiting CLI process (Ctrl-C, closed terminal, `waitSeconds` expiry) ends only the wait. The durable task keeps running while a worker owns it. An `unavailable` envelope means the service was not reachable while waiting; the record is durable, so re-read it by ID.

## Cancellation and stopping

- `"$BUDDY" cancel '{"runId":"<governed-run>","commandId":"cancel-1","controlFile":"<controlFile>","reason":"..."}'` fences a governed goal and its complete owned descendant graph, cancels open requests, pending continuations and route work, and records cancellation separately from aggregate stop evidence.
- `"$BUDDY" execution-cancel '{"runId":"..."}'` cancels an ordinary execution record: queued work immediately, durable cancel intent for an active attempt. The worker that owns the child observes the intent and cancels its own process group; unrelated dsh sessions are unaffected.
- `"$BUDDY" stop` asks the service to stop: queued tasks are cancelled, active attempts get a durable cancel request, the service drains for a bounded interval and reports `unresolvedAttempts`. A `stop` or `restart` with no running daemon reports `alreadyStopped` and starts nothing.
- The task's own execution deadline also ends Buddy-owned work. Those endings are terminal and are never replayed automatically. A new execution requires either an explicit `execution-retry` (a new attempt generation, clearing previous acceptance) or, for a governed goal, explicit new input through `continue`.

Never describe a wait timeout as an execution failure, never restart work the user stopped, and never relaunch because a result was delayed. Recovery invariants are in [operations.md](operations.md#cancellation-and-recovery).

## Background work that outlives the turn

`submit` is not reserved for background work: it is the first half of the default foreground flow, which keeps the current turn waiting with `await`. When the user explicitly wants the work to outlive the turn:

1. `submit` the goal (or `execution-submit` a non-coding record) and capture the `runId` (and `controlFile` for a governed goal).
2. Register an official App heartbeat automation on the original task before ending the turn; its prompt calls this same CLI to read the result, verify the artifacts and record the governed or execution acknowledgement.
3. Reuse a matching heartbeat instead of creating duplicates. Keep failures and required intervention visible; after reviewing the terminal outcome (including failure), record the review and remove the heartbeat. Report unresolved shutdown honestly.

Only this explicit user request authorizes a scheduler entry. Never create an automation, and never mute or remove one, merely to save tokens, quota or model calls: suppressing a follow-up that would have surfaced a failure is worse than an extra notification. The service itself has no scheduler; a check-in is a periodic background follow-up, not an immediate completion push, and native post-turn App wakeup is not solved by Buddy.

If the App heartbeat tool is unavailable or registration fails, keep waiting in the current turn. Do not end with an unmonitored job or substitute an unrelated scheduled task.

## More than one task at a time

Capacity and cwd/exclusive-resource conflicts queue with a `queueReason` instead of failing. The daemon starts a worker pool for two simultaneous business attempts and one independently reserved routing decision by default. Configure `BUDDY_MAX_CONCURRENT` (1–8) and `BUDDY_MAX_DECISIONS` (1–4) on daemon startup; `health.capacity` reports actual limits and occupancy. One worker runs one attempt at a time; parallel editors require separate worktrees. Await independent tasks by their own `runId`. Governed helpers share the business lane; routing uses the decision lane, so long coding tasks do not consume routing capacity. Unconfirmed shutdown still occupies its slot.
