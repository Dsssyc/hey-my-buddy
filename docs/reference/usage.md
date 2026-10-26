# Using hey-my-buddy

This page is the practical path from install to reviewed result. Read [cli.md](cli.md) for every command, field, default, bound and error, the [micro task lifecycle](workflow.md) for routing, Host control, request queues and workspace contracts, [operations.md](operations.md) for runtime lifecycle, the DSH workspace bridge, private state and recovery, and [architecture.md](architecture.md) for how the service works internally.

## The common path

For coding work, use the micro task lifecycle. Its normal path is one logical `runId`: `submit` → `await` → a `waiting-host` decision or continuation → `accept` of the final artifact. The advanced `execution-*` commands serve the `command` adapter and the internal decision infrastructure; they are not a hidden path to uncoding micro tasks.

### 1. Install once

You need [uv](https://docs.astral.sh/uv/) (hey-my-buddy runs on Python 3.12, which uv installs itself when the machine has none) and a working local DSH, ZCode, Codex or Claude Code installation with its provider credentials. hey-my-buddy uses the harness credentials already configured and leaves global model settings alone.

hey-my-buddy is one shared Agent Skill, `hey-my-buddy`, carrying its own CLI. Install it once:

```sh
/abs/path/to/hey-my-buddy/skills/hey-my-buddy/scripts/hey-my-buddy install
```

The skill lands in `~/.agents/skills/hey-my-buddy` (Codex) and `~/.claude/skills/hey-my-buddy` links to it (Claude Code); a second Host with the same version only repairs the link. [Installation](operations.md#installation) owns building a distributable skill, upgrades, plugin retirement and the post-install verification steps.

Start a new Host task so the `hey-my-buddy` skill loads, and call its launcher by absolute path rather than relying on PATH or a working directory:

```sh
HMB="$HOME/.agents/skills/hey-my-buddy/scripts/hey-my-buddy"
```

Installation prepares a content-addressed stable runtime under `~/.local/share/hey-my-buddy/runtime`, so replacing the skill later does not disturb running work. A fresh board lives under `~/.local/share/hey-my-buddy/state`. An older board directory at the previous default location is retained as an archive: this release does not read, convert or import it, and it refuses any schema other than the current one. See [operations.md](operations.md#runtime-lifecycle).

### 2. Choose the execution workspace

DSH session grouping was removed ([ADR-021](../decisions/021-router-buddy-planes-and-routing-evidence.md)): each DSH micro task keeps its native sessions in its private micro task root, and a `workspace` field on a submission is rejected as unknown.

Coding micro tasks name their Git isolation contract in a separate `executionWorkspace` object: `kind` (`existing` or `worktree`), `access` (`read`, `write` or `full`, the Worker permission level), `base` (`commit` with `ref`, or `working-tree`), `includeUntracked` and `writeScope`; source `cwd` and `integrator` are derived from the top-level `cwd` and the submitting Host, and are rejected inside the object. It is an ownership and artifact-verification contract, not session grouping and not an OS sandbox: concurrent writers use independent worktrees, a read workspace must keep its input unchanged, and declared paths are checkout-root-relative. The fixed-input matrix and reservation rules are in [workflow.md#workspace-and-artifact-rules](workflow.md#workspace-and-artifact-rules).

The level says where a Worker may modify files: nowhere under `read`, inside the checkout and the temporary directory under `write`, anywhere under `full`, which needs a one-line `accessNote`. A later turn may run at another level: give `access` on `continue`. On DSH that turn starts a new native session, which receives the blackboard's record of the micro task but not the earlier conversation. Choose `write` unless the task only reads, and `full` only when the task needs something that `write` refuses on the buddy's harness:

| Under `write` | Codex | Claude Code | DSH | ZCode |
| --- | --- | --- | --- | --- |
| A write outside the checkout | refused, except by a command the user's own Codex rules allow | refused | refused | not stopped |
| A Unix socket | works | refused | works | works |
| Shared memory | refused | works | works | works |
| Listing processes | refused | refused | refused | works |
| The network | open | HTTP and HTTPS through its sandbox proxy | open | open |

Writes in the checkout and the temporary directory and a loopback port work on all four. ZCode carries `write` out by convention: its Worker is told to stay inside the checkout, and nothing stops a command that does not. Under `read`, Codex and DSH still run commands as long as they write nothing, ZCode runs read-only commands, and Claude Code offers no command tool. What a level refuses is reported back with the operation that was refused; continue at another level when the task needs it. These are measurements on macOS from 2026-10-12 and 2026-10-13 with Codex 0.162.1, Claude Code 2.1.284, DSH 0.1.5-rc.1 and ZCode 0.16.9; the pages of [Codex](codex.md), [Claude Code](claude-code.md) and the [Workers](workers.md) have the details, and other platforms are not measured.

### 3. Write a bounded packet

Use one macro task per user agenda: the first `submit` carries `macroTask: {title}` and a required intent-focused micro task `title` of at most 30 characters; later new micro tasks use the returned `macroTaskId`. Keep that identifier with the run/control identifiers in the session handoff. If context is lost, the brief `get` of a known run recovers it. Continuations and helpers preserve their existing association without an extra argument. Cross-Host enrollment remains refused; do not invent a matching Host label or silently start another group to hide that boundary.

Omit the execution `harness`, `provider`, `model` and `effort` by default and let routing choose them with the Router of the effective mode; the mode itself comes from the Router setting (`configuration.defaultRoutingMode`), not from the submission. A complete tuple is appropriate only for a user-specified buddy, a user-established repository work division, or recovery at this micro task's routing boundary, and a partial tuple is rejected: write the task's needs into the task description and let the Router choose, or give the complete quadruple. Hard capability needs go in `requiredCapabilities`; soft task-local preferences no longer exist, and the user's global pin/prefer/exclude settings keep their existing meaning. Report a routing failure and its decision code to the user; recovering one micro task explicitly never turns into a policy of preselecting subsequent micro tasks.

A good packet lets the worker finish without guessing:

Delegate a coherent work unit with a verifiable output. A deterministic file listing, status query or already-known calculation usually belongs in a script; open-ended diagnosis and implementation may benefit from a coding Worker buddy. A read-only cross-platform artifact audit can still be complex. Give the worker the owning reference and relevant evidence, rather than automatically loading every design document or the complete project history. Choose native subagents and Worker buddies independently: context isolation and comparative cost/capability solve different problems.

Delegate one independently reviewable outcome per micro task: about one module or one concern, carrying its own checks. Keep mechanical work — collection, deterministic queries, artifact gathering — separate from open-ended diagnosis, and split a long agenda into several micro tasks under the same macro task instead of writing one oversized packet. Example: one user agenda, "harden the state store", becomes one macro task with three micro tasks:

- replace the on-disk journal format and add its migration test;
- diagnose and fix the read regression that test exposes;
- collect the before/after timings and write the report.

`expectedSeconds` is the expected duration of the work, 1–86400 seconds, default 1800: the moment the Host should look again, not a deadline. Nothing is stopped when it passes, and there is no form without an expectation; the former `timeoutSeconds` is refused with a hint to rename it. `await` remains a bounded, resumable caller wait; its window does not end an active execution. Observe long work and use the Host's ordinary `cancel` command if it must stop.

- micro task and why; expected inputs and outputs;
- the working directory and the commands that check the work;
- files/areas that may change, plus explicit non-goals and the expected final artifact;
- acceptance criteria: the observable result and the checks to run;
- key references, known facts and pitfalls;
- as much relevant context as the task needs. Task text is bounded at 1 MiB; the DSH runner delivers content over 32,000 bytes as a file reference, and that file must stay in place until the run finishes.

For a micro task, state the workspace intent in the packet too: which checkout or worktree, whether access is read or write, and who integrates helper output.

New macro tasks may carry an optional `description` of at most 300 characters: one or two sentences in the user’s words, written at creation and excluded from Worker and Router inputs. This field requires installed 0.15.1; omit it when using installed 0.15.0.

### 4. Submit the micro task once

```sh
"$HMB" submit '{"requestId":"state-kernel-1","hostId":"codex-state-kernel","macroTask":{"title":"State kernel reliability"},"title":"Implement the agreed state transition","task":"Implement the agreed state transition in src/state.ts, add regression tests in tests/state.test.ts and run the package tests. Preserve unrelated files. Report completed only after checks.","cwd":"/abs/repo","expectedSeconds":28800,"executionWorkspace":{"kind":"worktree","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["src/state.ts","tests/state.test.ts"]}}'
"$HMB" get '{"runId":"<runId>"}'
```

Save `macroTaskId`, `runId` and `controlFile` from the submission. The control file is an owner-private `0600` credential bound to that run and owner generation; later Host commands need this exact file, or the explicit `hostId` + `ownerGeneration` + `controlToken` triple. Never copy control tokens into task text, helper prompts, reports or another buddy, and never adopt another Host's saved capability. A Host name is attribution and never authority.

Long packets are easier from a file than from shell quoting: every method also accepts `--params-file PATH` or `-` for standard input in place of the positional JSON object. The three sources are mutually exclusive, the document must be one UTF-8 JSON object of at most 8 MiB, and its bytes are parsed directly — never re-interpreted by a shell. Everything after parsing is unchanged, including `output`, `controlFile` injection and the attempt-scoped credential. `hey-my-buddy help` lists every method, and `hey-my-buddy help submit` prints the parameters, defaults and bounds the service actually validates; both read only local code and start no service or model.

Ordinary submit fields stay flat, and the nested `spec` spelling is retired. A complete explicit `harness`, `provider`, `model` and `effort` quadruple is validated and delegated directly; a partial quadruple is rejected with the three remaining expressions; omitted fields are chosen by the Router of the configured default mode using the bounded current evaluation table. Task-local `routingPreferences` no longer exist, and neither the submission's `routingMode` nor `allowRoutingFallback` is accepted input. There is no silent DSH default, and the ordered Router list, retry interval and common default mode are chosen by the user ([evaluation.md](evaluation.md#fresh-board-and-the-initial-router-buddy)). A programmatic caller persists its own `submissionToken` before the first RPC so a lost reply still recovers its original control. The removed `workspace` field is rejected; `executionWorkspace` is the Git contract described above, and its `cwd`/`integrator` are derived from the top-level `cwd` and the submitting Host rather than accepted. Replaying the identical submission with the same `requestId` recovers the same logical task, while changed workspace, input or Host under that ID is a `CONFLICT`; a successful RPC is admission evidence only. `get` is compact by default; use `includeAudit:true` only when reviewing exact turn inputs, results, decisions and artifact manifests.

### 5. Await that same logical run

```sh
"$HMB" await '{"runId":"<runId>","waitSeconds":28800}'
```

`await` never starts work. It stays connected to the durable micro task until a Host decision boundary, a terminal outcome or this call's window end (`waitSeconds` 1–86400, default 86400), and prints one envelope. Its initial status read attaches to a healthy service or starts one if none is running, but it never creates a task. Your Host's guide owns the exact wait — [Codex](host-codex.md) or [Claude Code](host-claude-code.md); read only the one for your Host. Keep one wait per running micro task and preserve its workspace ownership.

The normal boundary is `outcome: "waiting-host"`: a routing boundary may have no agent turn or result yet, and `microTaskComplete` is `false`, and the envelope names the current `request`, `turn`, pinned artifacts and `nextCommands`; the micro task stays unfinished. Waiting on already-authorized helper work can continue inside the same wait. Terminal task outcomes, `wait-timeout` and `unavailable` are covered below and in [cli.md](cli.md#await-envelope).

### 6. Decide, continue or reroute at the boundary

Keep supervision bounded. Use the durable await path for unchanged external state, and read one compact boundary/result before inspecting the relevant fixed files. A worker can run deterministic CI waiting or artifact collection inside its authorized scope and report a new failure as attention; there is no need for a new high-level model decision on every unchanged poll. Distinguish a failed Router call, provider rate limit, native tool error, scope refusal and an actual failed acceptance check. A Router failure can be resolved on the same micro task with an authorized complete buddy and a `note`, or with `reroute` after fixing the Router; never hide the failure by creating a duplicate or rewriting shared preferences. Read the recorded `routingMode` rather than assuming which mode answered. A configured effort must be legal and user-enabled for normal routing; do not enable lower efforts or revise preferences just to make a cost claim.

Read `activeRequest`, the routing object, the current turn summary and fixed artifacts with `get`. The Host can approve concrete helper work, decline with a `note`, provide new continuation input, supply a complete buddy at a routing boundary, or reroute the same micro task after fixing the Router. Use the current revision and saved control file. To hand a micro task off to another buddy, `continue` it with the complete `buddy` and a `note`: a queued micro task changes buddy at once, a running turn is cancelled first and the micro task continues once that stop is confirmed; the run, its checkout and its history stay, and the earlier buddy's partial work is unverified. A helper that depends on the parent's changes should start from the parent's sealed output commit, not an unrelated copy of the original checkout.

The [assistance examples](workflow.md#host-boundaries-and-requests) provide complete helper packets and explain one-use automatic continuation. The [continuation and takeover examples](workflow.md#manual-continuation-and-takeover) cover new Host input, active-helper policy, `targetRunId` for an owned descendant, and transfer of control. Re-read after each decision: another pending request can become active. Nested requests retain their origin and are authorized by the Host.

Each continuation gets a fresh attempt and recorded resume mode: DSH and ZCode resume their exact proven native session, or explicitly reconstruct a new one after an unproven prior turn. The named integrator applies exact helper commits/patches and checks the combined result. The private console displays recorded decisions and provides authenticated controls for the operations it exposes; the Host uses the CLI.

### 7. Inspect the final artifact and accept it

```sh
"$HMB" get '{"runId":"<runId>"}'
"$HMB" accept '{"runId":"<runId>","controlFile":"<controlFile>","artifactId":"<finalArtifactId>","note":"Inspected the integrated target diff and ran the combined regression suite.","target":{"path":"/abs/target/checkout","ref":"main"}}'
```

Once the micro task is delivered (the `get` view reports `state: "delivered"`, exposed as `workflowState` on task and wait envelopes), `finalArtifactId` names the selected fixed output. Inspect the actual output commit/diff, changed paths, hashes and logs yourself, integrate the artifact into the target, then `accept` it with a `note` that says what you really checked. One `accept` verifies the integration against the real `target` itself, records the acceptance for that exact artifact and reclaims the managed checkout; when nothing needs integrating, give a nonempty `notRequired` reason in place of `target`. Paths that differ from the artifact are refused by name; the Host's own adjustments are confirmed with `adjusted: true` (and `hostPaths` with `beforeCommit` for changes outside the artifact's paths). The same payload replays its recorded answer and a changed one conflicts. The [CLI reference](cli.md) lists every field and the [workspace lifecycle](workspace-lifecycle.md) has a complete example.

If a deliverable fails acceptance, send the concrete findings back through `continue`; the same micro task repairs them. A defect found after acceptance goes into a new micro task submitted with `fixes: "<runId>"` naming the accepted one, which stays accepted ([workflow](workflow.md#fixes)). A later acceptance note should name any material Host correction or extra verification it required; avoid implying first-pass success from the final status alone. Acceptance does not invoke an evaluation model. Choose checks that exercise the changed boundary; expand or repeat them when code changes or a failure warrants it, not merely because another inspection occurred.

### 8. End a micro task without accepting it, or retry a reclaim

```sh
"$HMB" conclude '{"runId":"<runId>","controlFile":"<controlFile>","note":"Failed after the schema change; the partial work is kept for the next attempt."}'
"$HMB" reclaim '{"runId":"<runId>","controlFile":"<controlFile>"}'
```

`conclude` ends a failed, cancelled or delivered-but-unaccepted micro task with a required `note`: it retains the original execution status in `hostConclusion.executionStatus`, puts the micro task in terminal `concluded`, seals unsealed managed changes as a partial output and reclaims the checkout. A conclusion never counts as a success, and a concluded micro task cannot continue. `reclaim` retries the removal of this micro task's own managed checkout after a blocked or kept removal; a blocked removal reports its reasons and changes nothing.

A workspace conflict is settled first with `workspace-resolve` or `scope-amend`, as described in the [workspace lifecycle reference](workspace-lifecycle.md), which owns the complete example and field list.

## Execution records

Every micro task turn and every routing calculation runs as an execution record. A Host reads them with `status`, `result` and `list` and acts on the micro task, never on its records. No method admits an execution record: the `command` adapter is the check suite's own model-free test execution ([ADR-007](../decisions/007-neutral-core-and-single-current-contract.md)).

## Progress and questions while it runs

```sh
"$HMB" inquire '{"runId":"<runId>"}'
"$HMB" inquire '{"runId":"<runId>","inquiryId":"q-1","question":"What are you waiting on?","waitMs":20000}'
```

The no-question form is bounded observation: execution state from the durable record, `expectedSeconds` (or `routerBudgetSeconds` for a routing calculation), recent activity and the bounded native-activity projection on the task view (`phase`, last native/tool activity times, event sequence, tool name, a nullable `{code, params}` waiting reason and honest counters). It is not a percentage and not a guaranteed ETA, and fields the bridge could not observe are named in `live.unavailable` rather than reported as zero. An empty runner log, a static session list, a missing PID never proves the process stopped; only real shutdown evidence does.

A question goes to the run's own live agent only when its harness declares `inquiry`. DSH answers through its correlated reply tool; plain assistant text is not an answer. Codex and Claude Code take it into the running turn, where the model reads it at its next decision and answers through its inquiry tool. ZCode supports cooperative inquiry: the question waits for the root's next tool checkpoint or finish attempt, and only a signed answer matched to the native root turn counts. `live.deliveryMode` reports `cooperative-checkpoint`; an unanswered question becomes unavailable when the turn ends, without native input or a new turn. `command` mounts no inquiry bridge. Inquiry and message `reason` fields are nullable `{code, params}` objects; `src/hey_my_buddy/protocol/reason-codes.json` registers their codes and parameters. Questions and answers are bounded at 4000 UTF-8 bytes and 32 inquiry identities per task. Repeating the same ID and text reads its recorded state; changed text conflicts. See [CLI](cli.md#messages-and-inquiry) for the full contract.

Ask when the user asks or when you need the answer to report honestly; do not build a polling loop. `wait` and `watch` are short bounded waits (at most 30 s) for nearby changes on a service that must already be running; they never cold-start a service and are not a substitute for the durable `await`.

## When the wait ends before the task

A micro task has no execution deadline, while a single `await` window is capped at 24 hours. A call that runs out of wait returns `outcome: "wait-timeout"` with the run still active and a `recovery` block naming real commands and the existing run ID. That is a wait/connection limit, never an execution failure and never a reason to relaunch.

```sh
"$HMB" status '{"runId":"<runId>"}'
"$HMB" await  '{"runId":"<runId>","waitSeconds":28800}'
"$HMB" result '{"runId":"<runId>"}'
```

Killing the waiting CLI process (Ctrl-C, closed terminal, `waitSeconds` expiry) ends only the wait. The durable task keeps running while a worker owns it. An `unavailable` envelope means the service was not reachable while waiting; the record is durable, so re-read it by ID.

## Cancellation and stopping

- `"$HMB" cancel '{"runId":"<runId>","commandId":"cancel-1","controlFile":"<controlFile>","note":"..."}'` fences a micro task and its complete owned descendant graph, cancels open requests, pending continuations and route work, and records cancellation separately from aggregate stop evidence. A running turn wraps up first when its harness can continue an interrupted turn in the same native session (Codex and DSH): the Worker interrupts the turn, continues the session for one short summary turn and delivers it as the turn's result within a grace of five minutes; when the grace runs out, or when the harness cannot wrap up (Claude Code and ZCode), the run is stopped at once. Either way the partial output is sealed, the summary is read with `await` and `get` like any turn, and a repeated `cancel` keeps the first grace deadline. Stopping from the console stops at once, without a wrap-up.
- `"$HMB" execution-cancel '{"runId":"..."}'` cancels an ordinary execution record: queued work immediately, durable cancel intent for an active attempt. The worker that owns the child observes the intent and cancels its own process group; unrelated dsh sessions are unaffected.
- `"$HMB" stop` asks the service to stop: queued tasks are cancelled, active attempts get a durable cancel request, the service drains for a bounded interval and reports `unresolvedAttempts`. A `stop` or `restart` with no running daemon reports `alreadyStopped` and starts nothing.
- An exhausted Router budget also ends that Router call. Those endings are terminal and are never replayed automatically. A new execution requires either an explicit `execution-retry` (a new attempt generation, clearing previous acceptance) or, for a micro task, explicit new input through `continue`.

Never describe a wait timeout as an execution failure, never restart work the user stopped, and never relaunch because a result was delayed. Recovery invariants are in [operations.md](operations.md#cancellation-and-recovery).

## Waiting from your Host

Each Host waits in its own way, and each way is owned by one per-Host guide: [Codex](host-codex.md) or [Claude Code](host-claude-code.md). Read only the guide for your Host; both own that Host's waiting flow, launcher sandbox allowance and known limits.

## Background work that outlives the turn

`submit` is not reserved for background work: it starts the default flow, whose current Host turn retains a [Codex monitor](host-codex.md) or a Claude Code [background `await`](host-claude-code.md). When the user explicitly wants the work to outlive the turn:

1. `submit` the micro task and capture its `runId` and `controlFile`.
2. Register an official App heartbeat automation on the original task before ending the turn; its prompt calls this same CLI to read the result, verify the artifacts and record the micro task acceptance or execution acknowledgement.
3. Reuse a matching heartbeat instead of creating duplicates. Keep failures and required intervention visible; after reviewing the terminal outcome (including failure), record the review and remove the heartbeat. Report unresolved shutdown honestly.

Only this explicit user request authorizes a scheduler entry. Never create an automation, and never mute or remove one, merely to save tokens, quota or model calls: suppressing a follow-up that would have surfaced a failure is worse than an extra notification. The service itself has no scheduler; a check-in is a periodic background follow-up, not an immediate completion push, and native post-turn App wakeup is not solved by hey-my-buddy.

If the App heartbeat tool is unavailable or registration fails, keep waiting in the current turn. Do not end with an unmonitored job or substitute an unrelated scheduled task.

## More than one task at a time

Capacity and cwd/exclusive-resource conflicts queue with a `queueReason` instead of failing. The service admits work within one machine-wide concurrent-attempt ceiling — `HMB_MAX_CONCURRENT`, default 8, clamped 1–32 — shared by routing and execution; the reserved decision lane and its separate limit are removed. On top of the ceiling, each model service (an exact harness/provider/model) has a user-set concurrency limit (default 2; effort variants and the routing decisions using that model share the counter), edited on its model card in the console. `health.capacity` reports the total limit, total active and per-model occupancy. Each attempt runs in its own execution process; parallel editors require separate worktrees. Await independent tasks by their own `runId`. Unconfirmed shutdown still occupies its slot.

Before waiting on a running micro task, submit the micro tasks that are independent of it; the queue holds whatever exceeds capacity, so nothing is lost by submitting early. Two micro tasks are independent when neither needs the other's result and they do not change the same files or the same stored form. A name, a format or another convention that two of them must share is written into both task texts first: the board makes submission safe, not the compatibility of what comes back. Each micro task is still verified and accepted on its own. When several finish together, integrate them and run the project's checks on the integrated result before accepting any of them, because a change that passes alone can break what another one relies on; an accepted micro task cannot be continued, and its repair is a new micro task.

## A Lead for a large macro task

Use a Lead only when the user asks for one. Before granting delegation authority, confirm with the user and say what it costs: more spend and more time for more precision, worth it for a macro task on the scale of an architecture decision and not for ordinary work. The user names the buddy that leads; write it completely and do not route it. The Lead runs at the full level without an OS sandbox, accepts the micro tasks it delegates, and delivers one integrated result that you accept; it never talks to the user, so anything that needs the user comes to you as a request. Talk to it with `continue` at its boundaries or while it waits. The rules are in [workflow](workflow.md#delegation-authority-lead); its first real run stopped on a missing credential, which 0.37.0, installed on 2026-10-15, corrects; the second run that day delivered through four owned micro tasks.

## Operating rules

State the user's language explicitly in every Worker task packet and require the result summary in that language. Every micro task in an agenda retains its macroTaskId and a short intent title. The browser never translates results or invokes a model for wording. Each Host keeps its own waiting flow and reads only its own guide ([Codex](host-codex.md), [Claude Code](host-claude-code.md)).

Use the new package's `install` (or `upgrade`) for cutover with automatic failure rollback; only an install that moves the middle number takes a backup, for that rollback. An installation that moves the middle number of the version requires idle work and confirmed stops; one that moves only the last number keeps running work ([operations](operations.md)). Never make ad-hoc whole-state/source archives. Installation requires separate user authorization. Storage plan is read-only; apply requires confirmation of its exact guarded plan. The console defaults to login-free access at the stable bookmarkable `http://127.0.0.1:<port>/`. `hey-my-buddy console` opens it; `console {"browser":false}` returns it. Settings can enable login with a single-use 10-minute entry, sessions without a 30-day expiry and explicit logout/revocation. All admitted windows can edit settings; for shared Router and buddy settings, later saves of the same field take effect and command identities fence replays. [Console](console.md) owns the HTTP boundary and session contract. Source verification and actual installation remain separate facts.
