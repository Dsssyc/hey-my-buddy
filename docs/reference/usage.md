# Using hey-my-buddy

This page is the practical path from install to reviewed result. Read [cli.md](cli.md) for every command, field, default, bound and error, the [governed lifecycle](workflow.md) for routing, Host control, request queues and workspace contracts, [operations.md](operations.md) for runtime lifecycle, the DSH workspace bridge, private state and recovery, and [architecture.md](architecture.md) for how the service works internally.

## The common path

For coding work, use the governed lifecycle. Its normal path is one logical `runId`: `submit` → `await` → a `waiting-host` decision or continuation → `acknowledge` against the final artifact. The advanced `execution-*` commands serve the `command` and `external` adapters and internal decision infrastructure; they are not a hidden path to ungoverned coding work.

### 1. Install once

You need macOS or Linux, [uv](https://docs.astral.sh/uv/) with Python 3.12–3.14, Node.js 20+ for the DSH runner and a working local `dsh` and/or ZCode installation with its provider credentials. hey-my-buddy uses the harness credentials already configured and leaves global model settings alone.

hey-my-buddy is one shared Agent Skill, `buddy`, carrying its own CLI. Install it once:

```sh
/abs/path/to/hey-my-buddy/skills/buddy/scripts/buddy install
```

The skill lands in `~/.agents/skills/buddy` (Codex) and `~/.claude/skills/buddy` links to it (Claude Code); a second Host with the same version only repairs the link. [Installation](operations.md#installation) owns building a distributable skill, upgrades, plugin retirement and the post-install verification steps.

Start a new Host task so the `buddy` skill loads, and call its launcher by absolute path rather than relying on PATH or a working directory:

```sh
BUDDY="$HOME/.agents/skills/buddy/scripts/buddy"
```

The first command that needs the service installs a content-addressed stable runtime under `~/.local/share/hey-my-buddy/runtime`, so replacing the skill later does not disturb running work. A fresh board lives under `~/.local/share/hey-my-buddy/state`. An older board directory at the previous default location is retained as an archive: this release does not read, convert or import it, and it refuses any schema other than the current one. See [operations.md](operations.md#runtime-lifecycle).

### 2. Choose grouping and the execution workspace

`workspace` is a boolean and only controls DSH session grouping; it defaults to `true`. Either install the DSH workspace bridge once ([operations.md](operations.md#workspace-bridge)) and keep grouping, or pass `"workspace": false` for an intentionally standalone run. The default is never silently downgraded; a grouped run fails honestly when the bridge is missing.

Independent of grouping, governed coding work names its Git isolation contract in a separate `executionWorkspace` object: `kind` (`existing` or `worktree`), source `cwd`, `access` (`read` or `write`), `base` (`commit` with `ref`, or `working-tree`), `includeUntracked`, `writeScope` and `integrator`. It is an ownership and artifact-verification contract, not session grouping and not an OS sandbox: concurrent writers use independent worktrees, a read workspace must keep its input unchanged, and declared paths are checkout-root-relative. The fixed-input matrix and reservation rules are in [workflow.md#workspace-and-artifact-rules](workflow.md#workspace-and-artifact-rules).

### 3. Write a bounded packet

Use one work objective per user agenda: the first `submit` carries `objective: {title}` and a required intent-focused delegation `title` of at most 30 characters; later new delegations use the returned `objectiveId`. Keep that identifier with the run/control identifiers in the session handoff. If context is lost, the brief `get` of a known run recovers it. Continuations and helpers preserve their existing association without an extra argument. Cross-Host enrollment remains refused; do not invent a matching Host label or silently start another group to hide that boundary.

Omit the execution `adapter`, `provider`, `model` and `effort` by default and let routing choose them with the Router of the effective mode. `routingMode: "fast"` routes without tools from the task text alone and sends that text to the fast Router's model provider; `routingMode: "review"` also reads a frozen repository copy and is only usable where a verified read-only Router exists. Omitted `routingMode` uses the configured default mode, and `allowRoutingFallback:false` stops at the Host boundary instead of falling back from an unavailable review Router to fast routing. A complete tuple is appropriate only for a user-specified configuration, a user-established repository work division, or recovery at this goal's routing boundary. Use `routingPreferences` for soft preferences and partial fields only for actual hard requirements. Report a routing failure and its decision code to the user; recovering one goal explicitly never turns into a policy of preselecting subsequent goals.

A good packet lets the worker finish without guessing:

Delegate a coherent work unit with a verifiable output. A deterministic file listing, status query or already-known calculation usually belongs in a script; open-ended diagnosis and implementation may benefit from a coding Worker buddy. A read-only cross-platform artifact audit can still be complex. Give the worker the owning reference and relevant evidence, rather than automatically loading every design document or the complete project history. Choose native subagents and Worker buddies independently: context isolation and comparative cost/capability solve different problems.

Delegate one independently reviewable outcome per delegation: about one module or one concern, carrying its own checks. Keep mechanical work — collection, deterministic queries, artifact gathering — separate from open-ended diagnosis, and split a long agenda into several delegations under the same work objective instead of writing one oversized packet. Example: one user agenda, "harden the state store", becomes one objective with three delegations:

- replace the on-disk journal format and add its migration test;
- diagnose and fix the read regression that test exposes;
- collect the before/after timings and write the report.

Long work may explicitly set `"timeoutSeconds": 0` to run without an overall execution deadline. An omitted value still defaults to 1800 seconds; a positive value must be 10–86400 seconds. The no-deadline choice belongs to that task and any helper explicitly configured the same way. `await` remains a bounded, resumable caller wait; its window does not end an active execution. Observe long work and use the Host's ordinary `cancel` command if it must stop.

- goal and why; expected inputs and outputs;
- the working directory and the commands that check the work;
- files/areas that may change, plus explicit non-goals and the expected final artifact;
- acceptance criteria: the observable result and the checks to run;
- key references, known facts and pitfalls;
- as much relevant context as the task needs. Task text is bounded at 1 MiB; the DSH runner delivers content over 32,000 bytes as a file reference, and that file must stay in place until the run finishes.

For a governed task, state the workspace intent in the packet too: which checkout or worktree, whether access is read or write, and who integrates helper output.

New objectives may carry an optional `description` of at most 300 characters: one or two sentences in the user’s words, written at creation and excluded from Worker and Router inputs. This field requires installed 0.15.1; omit it when using installed 0.15.0.

### 4. Submit the governed goal once

```sh
"$BUDDY" submit '{"requestId":"state-kernel-1","hostId":"codex-state-kernel","objective":{"title":"State kernel reliability"},"title":"Implement the agreed state transition","task":"Implement the agreed state transition in src/state.ts, add regression tests in tests/state.test.ts and run the package tests. Preserve unrelated files. Report completed only after checks.","cwd":"/abs/repo","timeoutSeconds":28800,"workspace":false,"executionWorkspace":{"kind":"worktree","cwd":"/abs/repo","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["src/state.ts","tests/state.test.ts"],"integrator":"codex-state-kernel"}}'
"$BUDDY" get '{"runId":"<runId>"}'
```

Save `objectiveId`, `runId` and `controlFile` from the submission. The control file is an owner-private `0600` credential bound to that run and owner generation; later Host commands need this exact file, or the explicit `hostId` + `ownerGeneration` + `controlToken` triple. Never copy control tokens into task text, helper prompts, reports or another buddy, and never adopt another Host's saved capability. A Host name is attribution and never authority.

Long packets are easier from a file than from shell quoting: every method also accepts `--params-file PATH` or `-` for standard input in place of the positional JSON object. The three sources are mutually exclusive, the document must be one UTF-8 JSON object of at most 8 MiB, and its bytes are parsed directly — never re-interpreted by a shell. Everything after parsing is unchanged, including `output`, `controlFile` injection and the attempt-scoped credential. `buddy help` lists every method, and `buddy help submit` prints the parameters, defaults and bounds the service actually validates; both read only local code and start no service or model.

Ordinary submit fields stay flat. A complete explicit `adapter`, `provider`, `model` and `effort` quadruple is validated and dispatched directly; a partial quadruple is a hard filter; omitted fields are chosen by the Router of the effective mode (`routingMode` on the submission, otherwise the configured default) using the bounded current evaluation table. A goal may also carry task-local `routingPreferences`: at most eight ordered `{match, reason}` soft preferences that apply only to that goal and to helpers explicitly inheriting them, never to global preferences or another task. There is no silent DSH default, and both Router profiles and the default mode are chosen by the user ([evaluation.md](evaluation.md#fresh-board-and-the-initial-router-profile)). A programmatic caller persists its own `submissionToken` before the first RPC so a lost reply still recovers its original control. `workspace` only means session grouping; `executionWorkspace` is the Git contract described above. Replaying the identical submission with the same `requestId` recovers the same logical task, while changed workspace, input or Host under that ID is a `CONFLICT`; a successful RPC is admission evidence only. `get` is compact by default; use `includeAudit:true` only when reviewing exact turn inputs, results, decisions and artifact manifests.

### 5. Await that same logical run

```sh
"$BUDDY" await '{"runId":"<runId>","waitSeconds":28800}'
```

`await` never starts work. It stays connected to the durable goal until a Host decision boundary, a terminal outcome or this call's window end (`waitSeconds` 1–86400, default 86400), and prints one envelope. Its initial status read attaches to a healthy service or starts one if none is running, but it never creates a task. Your Host's guide owns the exact wait — [Codex](host-codex.md) or [Claude Code](host-claude-code.md); read only the one for your Host. Keep one wait per running goal and preserve its workspace ownership.

The normal boundary is `outcome: "waiting-host"`: a routing boundary may have no agent turn or result yet, and `goalComplete` is `false`, and the envelope names the current `request`, `turn`, pinned artifacts and `nextCommands`; the goal stays unfinished. Waiting on already-authorized helper work can continue inside the same wait. Terminal task outcomes, `wait-timeout` and `unavailable` are covered below and in [cli.md](cli.md#await-envelope).

### 6. Decide, continue or reroute at the boundary

Keep supervision bounded. Use the durable await path for unchanged external state, and read one compact boundary/result before inspecting the relevant fixed files. A worker can run deterministic CI waiting or artifact collection inside its authorized scope and report a new failure as attention; there is no need for a new high-level model decision on every unchanged poll. Distinguish a failed Router call, provider rate limit, native tool error, scope refusal and an actual failed acceptance check. A Router failure can be resolved on the same goal with an authorized complete configuration and a reason, or with `reroute` after fixing the Router; never hide the failure by creating a duplicate or rewriting shared preferences. Read the recorded `routingMode` and `fallback` rather than assuming which mode answered. A configured effort must be legal and user-enabled for normal routing; do not enable lower efforts or revise preferences just to make a cost claim.

Read `activeRequest`, the routing object, the current turn summary and fixed artifacts with `get`. The Host can approve concrete helper work, decline with a reason, provide new continuation input, supply a complete configuration at a routing boundary, or reroute the same goal after fixing the Router. Use the current revision and saved control file. A helper that depends on the parent's changes should start from the parent's sealed output commit, not an unrelated copy of the original checkout.

The [assistance examples](workflow.md#host-boundaries-and-requests) provide complete helper packets and explain one-use automatic continuation. The [continuation and takeover examples](workflow.md#manual-continuation-and-takeover) cover new Host input, active-helper policy, `targetRunId` for an owned descendant, and transfer of control. Re-read after each decision: another pending request can become active. Nested requests retain their origin and are authorized by the Host.

Each continuation gets a fresh attempt and recorded resume mode: DSH reconstructs a fresh session, while ZCode resumes its exact proven native session or explicitly reconstructs a new one after an unproven prior turn. The named integrator applies exact helper commits/patches and checks the combined result. The private console displays the recorded decisions read-only; the Host performs these operations through the CLI.

### 7. Inspect the final artifact, record integration and acknowledge

```sh
"$BUDDY" get '{"runId":"<runId>"}'
"$BUDDY" integration-record '{"runId":"<runId>","commandId":"integrate-1","expectedRevision":7,"controlFile":"<controlFile>","artifactId":"<finalArtifactId>","strategy":"cherry-pick","target":{"path":"/abs/target/checkout","ref":"main"},"beforeCommit":"<target commit before the integration>","verification":"Applied the sealed artifact commit to the target and ran the combined checks."}'
"$BUDDY" acknowledge '{"runId":"<runId>","commandId":"review-1","controlFile":"<controlFile>","artifactId":"<finalArtifactId>","integrationId":"<returned integrationId>","note":"Inspected the integrated target diff and ran the combined regression suite.","verdict":"accepted"}'
```

Once the goal is delivered (the `get` view reports `state: "delivered"`, exposed as `workflowState` on task and wait envelopes), `finalArtifactId` names the selected fixed output. Inspect the actual output commit/diff, changed paths, hashes and logs yourself, then record what you really checked. Artifact verification covers the whole governed goal — the service checks immutable Git objects for the delta from the original goal input tree to the final output tree, including earlier continuation changes, and missing or mismatched objects fail instead of being skipped. An `accepted` verdict requires a verified integration record or an explicit `notRequired` record bound to that artifact; the service resolves the target commit/tree relationship from the repository itself rather than trusting a client hash, and `get` exposes the recorded `integrations`. A task with no repository change records `{"notRequired": true, "reason": "..."}` instead. Acceptance is separate from execution, is tied to that artifact rather than to whichever branch or directory exists later, and never converts a failed execution into a success. A governed wait's `shutdownConfirmed` requires both self and descendant stop evidence, so a delivered or cancelled goal whose descendants are unconfirmed keeps waiting instead of inventing a stopped state.

If a deliverable fails acceptance, record `acknowledge` with `verdict: "rejected"`, the artifact ID and concrete findings before requesting a correction through `continue`. A later accepted review should name any material Host correction or extra verification it required; avoid implying first-pass success from the final status alone. These review records do not invoke an evaluation model. A user-requested batch maintenance can later use them without regenerating cards after every task. Choose checks that exercise the changed boundary; expand or repeat them when code changes or a failure warrants it, not merely because another inspection occurred.

### 8. Reclaim the managed checkout

After acceptance and integration, plan and apply the removal of exactly this run's registered managed checkout:

```sh
"$BUDDY" workspace-cleanup-plan '{"runId":"<runId>","commandId":"cleanup-plan-1","expectedRevision":7,"controlFile":"<controlFile>"}'
"$BUDDY" workspace-cleanup-apply '{"runId":"<runId>","planId":"<returned plan.planId>","commandId":"cleanup-apply-1","expectedRevision":7,"confirmPath":"<returned plan.path>","controlFile":"<controlFile>"}'
```

The plan names the exact path, its eligibility evidence and retention list and expires after 900 seconds; apply rechecks ownership, acceptance, integration, shutdown, dependencies and Git state — including the final tree and fingerprint against the original allocation — and deletes only that checkout, keeping outputs, manifests, fixed refs and receipts. A plan with reasons is blocked, not authorizing; re-read the run and plan again. A repaired workspace conflict is settled first with `workspace-resolve` or `scope-amend` as described in the [workspace lifecycle reference](workspace-lifecycle.md), which owns the complete example and field list.

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

A question goes to the run's own live agent only when its adapter declares `inquiry`. DSH answers through its correlated reply tool; plain assistant text is not an answer. ZCode supports cooperative inquiry: the question waits for the root's next tool checkpoint or finish attempt, and only a signed answer matched to the native root turn counts. `live.deliveryMode` reports `cooperative-checkpoint`; an unanswered question becomes unavailable when the turn ends, without native input or a new turn. `command`, `external` and `codex` mount no inquiry bridge. Questions and answers are bounded at 4000 UTF-8 bytes and 32 inquiry identities per task. Repeating the same ID and text reads its recorded state; changed text conflicts. See [CLI](cli.md#messages-and-inquiry) for the full contract.

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
- The task's own execution deadline also ends its active attempt. Those endings are terminal and are never replayed automatically. A new execution requires either an explicit `execution-retry` (a new attempt generation, clearing previous acceptance) or, for a governed goal, explicit new input through `continue`.

Never describe a wait timeout as an execution failure, never restart work the user stopped, and never relaunch because a result was delayed. Recovery invariants are in [operations.md](operations.md#cancellation-and-recovery).

## Waiting from your Host

Each Host waits in its own way, and each way is owned by one per-Host guide: [Codex](host-codex.md) or [Claude Code](host-claude-code.md). Read only the guide for your Host; both own that Host's waiting flow, launcher sandbox allowance and known limits.

## Background work that outlives the turn

`submit` is not reserved for background work: it starts the default flow, whose current Host turn retains a [Codex monitor](host-codex.md) or a Claude Code [background `await`](host-claude-code.md). When the user explicitly wants the work to outlive the turn:

1. `submit` the goal (or `execution-submit` a non-coding record) and capture the `runId` (and `controlFile` for a governed goal).
2. Register an official App heartbeat automation on the original task before ending the turn; its prompt calls this same CLI to read the result, verify the artifacts and record the governed or execution acknowledgement.
3. Reuse a matching heartbeat instead of creating duplicates. Keep failures and required intervention visible; after reviewing the terminal outcome (including failure), record the review and remove the heartbeat. Report unresolved shutdown honestly.

Only this explicit user request authorizes a scheduler entry. Never create an automation, and never mute or remove one, merely to save tokens, quota or model calls: suppressing a follow-up that would have surfaced a failure is worse than an extra notification. The service itself has no scheduler; a check-in is a periodic background follow-up, not an immediate completion push, and native post-turn App wakeup is not solved by hey-my-buddy.

If the App heartbeat tool is unavailable or registration fails, keep waiting in the current turn. Do not end with an unmonitored job or substitute an unrelated scheduled task.

## More than one task at a time

Capacity and cwd/exclusive-resource conflicts queue with a `queueReason` instead of failing. The daemon starts a worker pool for one machine-wide concurrent-attempt ceiling — `BUDDY_MAX_CONCURRENT`, default 8, clamped 1–32 — shared by routing and execution; the reserved decision lane and its separate limit are removed. On top of the ceiling, each exact adapter/provider/model family has a user-set concurrency limit (default 2 per family; effort variants and the routing decisions using that model share the counter), edited on its model card in the console. `health.capacity` reports the total limit, total active and per-model occupancy. One worker runs one attempt at a time; parallel editors require separate worktrees. Await independent tasks by their own `runId`. Unconfirmed shutdown still occupies its slot.

## Operating rules

State the user's language explicitly in every Worker task packet and require the result summary in that language. Every delegation in an agenda retains its objectiveId and a short intent title. The browser never translates results or invokes a model for wording. Each Host keeps its own waiting flow and reads only its own guide ([Codex](host-codex.md), [Claude Code](host-claude-code.md)).

Backup and upgrade use service-owned `buddy backup` and new-package `buddy upgrade` with one verified rolling `backups/current/`, automatic failure rollback and idle-only cutover. Never make ad-hoc whole-state/source archives. Installation requires separate user authorization. Storage plan is read-only; apply requires confirmation of its exact guarded plan. The console defaults to login-free access at the stable bookmarkable `http://127.0.0.1:<port>/`. `buddy console` opens it; `console {"browser":false}` returns it. Settings can enable login with a single-use 10-minute entry, sessions without a 30-day expiry and explicit logout/revocation. All admitted windows can edit settings subject to revision conflict checks. [Console](console.md) owns the HTTP boundary and session contract. Source verification and actual installation remain separate facts.
