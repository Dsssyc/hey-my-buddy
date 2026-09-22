# Using Buddy

This page is the practical path from install to reviewed result. Read [cli.md](cli.md) for every command, field, default, bound and error, the [governed workflow guide](workflow.md) for Host control, request queues and workspace contracts, [operations.md](operations.md) for lifecycle, upgrade and recovery, and [architecture.md](architecture.md) for how the service works internally.

For repository work in a Git checkout, use the governed workflow. Its normal path is one logical `runId`: `workflow-submit` → `await` → a `waiting-host` decision or continuation → `workflow-acknowledge` against the final artifact. The legacy one-shot commands (`start`, `run`, `submit`, `acknowledge`) remain available for non-Git or legacy tasks and keep their original behavior, but they provide no governed workspace ownership, assistance or Host control; do not mix them into a governed goal.

## The common path

### 1. Install once

The skill is self-contained in `deepseek-delegate/`: the installable unit is the whole directory (its `SKILL.md`, `references/`, `scripts/`, `plugins/`, package and lock file), so copy the directory rather than only the `SKILL.md`. From a checkout:

```sh
uv sync --frozen --project deepseek-delegate --python 3.12
```

From the checkout root, add a link without replacing an existing installation:

```sh
BUDDY_SKILLS_DIR="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$BUDDY_SKILLS_DIR"
if [ -e "$BUDDY_SKILLS_DIR/deepseek-delegate" ] || [ -L "$BUDDY_SKILLS_DIR/deepseek-delegate" ]; then
  printf '%s\n' 'A skill already exists at this path; see the upgrade guide.'
else
  ln -s "$PWD/deepseek-delegate" "$BUDDY_SKILLS_DIR/deepseek-delegate"
fi
```

For an independent copy, replace the `ln -s` line with `cp -R "$PWD/deepseek-delegate" "$BUDDY_SKILLS_DIR/deepseek-delegate"` inside that guard, then run `uv sync --frozen --python 3.12` in the copied directory. For an existing installation, follow [the offline upgrade sequence](operations.md#database-upgrade) before replacing its source or link, and open a new Codex task afterwards to load the skill.

Resolve the launcher from the installed skill's own location, never from the caller's working directory:

```sh
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/deepseek-delegate"   # installed location
BUDDY="$SKILL_DIR/scripts/launch-buddy.sh"                         # or: node "$SKILL_DIR/scripts/buddy.mjs"
```

The launcher forwards every argument to the one `buddy` CLI and selects Python 3.12 through uv. The plugin ships the same self-contained `deepseek-delegate/` directory, so the `$buddy` skill resolves `<plugin-root>/deepseek-delegate/scripts/launch-buddy.sh`; both entrypoints run identical commands against the same state directory. A cold start automatically materializes a stable versioned runtime outside the Codex plugin cache, so a later plugin update does not require destroying the service; see [operations.md](operations.md#runtime-lifecycle-and-upgrade).

### 2. Decide how the run joins your workspace

`workspace` (default `true`) only controls DSH session grouping: it groups the session through the running workspace host bridge. Either install that bridge once into the existing dsh `web` profile:

```sh
node "$SKILL_DIR/scripts/install-workspace-bridge.mjs"
```

or pass `"workspace": false` for an intentionally standalone run. The default is never silently downgraded; a grouped run fails honestly when the bridge is missing. Bridge install, validation, and recovery are in [operations.md](operations.md#workspace-bridge).

In addition to that grouping choice, governed repository work names its execution workspace in a separate `executionWorkspace` object: `kind` (`existing` or `worktree`), source `cwd`, `access` (`read` or `write`), `base` (`commit` with `ref`, or `working-tree`), `includeUntracked`, `writeScope` and `integrator`. That object is an ownership and artifact-verification contract, not session grouping and not an OS sandbox: concurrent writers use independent worktrees, a read workspace must keep its input unchanged, and declared paths are checkout-root-relative. The fixed-input matrix and reservation rules are in [workflow.md](workflow.md#workspace-and-artifact-rules).

### 3. Write a bounded packet

A good packet lets the worker finish without guessing:

- goal and why; expected inputs and outputs;
- the working directory and the commands that check the work;
- files/areas that may change, plus explicit non-goals and the expected final artifact;
- acceptance criteria: the observable result and the checks to run;
- key references, known facts and pitfalls;
- as much relevant context as the task needs. Task text is bounded at 1 MiB; content over 32,000 bytes is delivered to dsh as a file reference, and that file must stay in place until the run finishes.

For a governed task, state the workspace intent in the packet too: which checkout or worktree, whether access is read or write, and who integrates helper output.

### 4. Submit the governed task once

```sh
"$BUDDY" workflow-submit '{"requestId":"state-kernel-1","hostId":"codex-state-kernel","task":"Implement the agreed state transition in src/state.ts, add regression tests in tests/state.test.ts and run the package tests. Preserve unrelated files. Report completed only after checks; request assistance or attention through buddy_finish_turn if a bounded dependency is missing.","cwd":"/abs/repo","provider":"deepseek-official","model":"deepseek-flash","effort":"max","timeoutSeconds":28800,"workspace":false,"executionWorkspace":{"kind":"worktree","cwd":"/abs/repo","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["src/state.ts","tests/state.test.ts"],"integrator":"codex-state-kernel"}}'
"$BUDDY" workflow-get '{"runId":"<runId>"}'
```

Save `runId` and `controlFile` from the submission. The control file is an owner-private `0600` credential bound to that run and owner generation; later Host commands need this exact file, or the explicit `hostId` + `ownerGeneration` + `controlToken` triple. Never copy control tokens into task text, helper prompts, reports or another Buddy, and never adopt another Host's saved capability. A Host name is attribution and never authority.

All ordinary submit fields stay flat, and optional `model`, `provider` and `effort` fields override the route with the precedence documented in [runner.md](runner.md#precedence). A programmatic caller persists its own `submissionToken` before the first RPC so a lost reply still recovers its original control. `workspace` only means session grouping; `executionWorkspace` is the governed contract described above. Replaying the identical submission with the same `requestId` recovers the same logical task, while changed workspace, input or route under that ID is a `CONFLICT`; a successful RPC is admission evidence only. `workflow-get` is compact by default; use `includeAudit:true` only when reviewing exact turn inputs, results, decisions and artifact manifests.

### 5. Await that same logical run

```sh
"$BUDDY" await '{"runId":"<runId>","waitSeconds":28800}'
```

`await` never starts work. It stays connected to the durable goal until a Host decision boundary, a terminal outcome or this call's window end (`waitSeconds` 1–86400, default 86400), and prints one envelope. Its initial status read attaches to a healthy service or starts one if none is running, but it never creates a task. Keep the current Codex turn waiting instead of starting a second run or beginning work on the same files.

The normal repository boundary is `outcome: "waiting-host"`: the previous agent turn concluded normally, `goalComplete` is `false`, the envelope names the current `request`, `turn` and pinned artifacts, and the goal stays unfinished. Waiting on already-authorized helper work can continue inside the same wait. Terminal task outcomes, `wait-timeout` and `unavailable` are covered below and in [cli.md](cli.md#runawait-envelope).

### 6. Decide or continue at the Host boundary

Read `activeRequest`, the current turn summary and fixed artifacts with `workflow-get`. The Host can approve concrete helper work, decline with a reason, or provide new continuation input. Use the current revision and saved control file. A helper that depends on the parent's changes should start from the parent's sealed output commit, not an unrelated copy of the original checkout.

The [assistance examples](workflow.md#approve-or-decline-assistance) provide complete helper packets and explain one-use automatic continuation. The [continuation and takeover examples](workflow.md#manual-continuation-and-takeover) cover new Host input, active-helper policy and transfer of control. Re-read after each decision: another pending request can become active. Nested requests retain their origin and are authorized by the Host.

Each continuation gets a fresh attempt and reconstructed DSH session while retaining the allocated physical checkout. The named integrator applies exact helper commits/patches and checks the combined result. The private console exposes the same decisions and ownership checks.

### 7. Inspect the final artifact and acknowledge

```sh
"$BUDDY" workflow-get '{"runId":"<runId>"}'
"$BUDDY" workflow-acknowledge '{"runId":"<runId>","commandId":"review-1","controlFile":"<controlFile>","artifactId":"<finalArtifactId>","note":"Inspected the fixed diff and ran the combined regression suite.","verdict":"accepted"}'
```

Once the goal is delivered (the `workflow-get` view reports `state: "delivered"`, exposed as `workflowState` on task and wait envelopes), `finalArtifactId` names the selected fixed output. Inspect the actual output commit/diff, changed paths, hashes and logs yourself, then record `verdict` `accepted` or `rejected` and a `note` describing what you really checked. Acceptance is separate from execution, is tied to that artifact rather than to whichever branch or directory exists later, and never converts a failed execution into a success. A governed wait's `shutdownConfirmed` requires both self and descendant stop evidence, so a delivered or cancelled goal whose descendants are unconfirmed keeps waiting instead of inventing a stopped state.

## Legacy one-shot and non-Git tasks

These commands keep their original behavior for non-Git tasks and for tasks that do not need a Host decision boundary, fixed workspace ownership or helper artifacts. They have no `controlFile` and no governed request queue; use `workflow-cancel` with the saved control file for a governed goal, and never use `cancel`, `retry` or `acknowledge` to bypass governance.

### Start once and keep the runId

```sh
"$BUDDY" start '{"requestId":"fix-flaky-test","task":"<task text>","cwd":"/abs/project","timeoutSeconds":7200}'
```

`start` returns immediately with the task view, including `runId`. The task is admitted as `queued`; it begins when a worker claims it and `queueReason` explains any wait (`awaiting-worker`, `capacity`, `cwd-overlap`, `exclusive-resource`). Queueing is intentional: capacity and resource conflicts wait instead of returning BUSY.

`requestId` is the idempotency key. Repeating the identical `start` recovers the same task and never launches the adapter twice; changed input for the same `requestId` is a `CONFLICT`. Optional `model`, `provider` and `effort` fields override the route for this task; omitted values follow the precedence in [runner.md](runner.md#precedence), and effort is never inherited from settings.

### Await that same run

```sh
"$BUDDY" await '{"runId":"<runId>","waitSeconds":7200}'
```

`await` is the same durable wait described above; for a legacy task it ends at the terminal status instead of a Host boundary. `buddy run` is the one-call alternative that starts (or recovers) and waits in a single invocation; its window defaults to `timeoutSeconds + 60 s` shutdown grace, capped at 86400 s. Prefer the split `start` → `await` when you need the `runId` before the task finishes, for example to observe or question it.

### Inspect the result, then acknowledge

```sh
"$BUDDY" status '{"runId":"<runId>"}'
"$BUDDY" result '{"runId":"<runId>"}'
"$BUDDY" artifacts '{"runId":"<runId>"}'
"$BUDDY" acknowledge '{"runId":"<runId>","note":"inspected the diff and ran the suite","verdict":"accepted"}'
```

Exit 0 only means the call returned. Read `status`, `outcome`, `resultDelivered` and `shutdownConfirmed`, then inspect the real diff, files, hashes and logs yourself. Acknowledgement records that review of a persisted result with confirmed shutdown — including a reviewed failure — and never converts a failed execution into a success. Repeating it with a different note or `verdict` is a `CONFLICT`.

### Other one-shot adapters

**`command` adapter.** One explicit `argv` process, never a shell:

```sh
"$BUDDY" submit '{"requestId":"echo-1","task":"print a greeting","cwd":"/abs/path","adapter":"command","argv":["/bin/echo","hi"]}'
```

**`external` adapter.** No local process: the caller's own agent claims the task through the public client and submits the result itself. A runnable example, worker identity and receipt rules are in [workers.md](workers.md). `submit` is the same board operation as `start`, intended for multi-agent callers.

## Progress and questions while it runs

```sh
"$BUDDY" inquire '{"runId":"<runId>"}'
"$BUDDY" inquire '{"runId":"<runId>","inquiryId":"q-1","question":"What are you waiting on?","waitMs":20000}'
```

The no-question form is bounded observation: execution state from the durable record, an explicitly estimated deadline, and recent tool activity. It is not a percentage and not a guaranteed ETA, and fields the bridge could not observe are named in `live.unavailable` rather than reported as zero.

A question goes to the run's own live dsh agent; it never starts a second agent and never extends, shortens, pauses or cancels the task. Answers require the correlated reply tool, so assistant prose is never treated as an answer. Questions and answers are bounded at 4000 UTF-8 bytes each, at most 32 inquiries are retained per task, and an adapter without an inquiry capability reports an honest reason. Repeating the same `inquiryId` with identical text returns the recorded state, while the same id with different text is a `CONFLICT`. The full message contract is in [cli.md](cli.md#messages-and-inquiry).

Ask when the user asks or when you need the answer to report honestly; do not build a polling loop. `wait` and `watch` are short bounded waits (at most 30 s) for nearby changes on a service that must already be running; they never cold-start a service and are not a substitute for the durable `await`.

## When the wait ends before the task

A deadline longer than the wait window is allowed. The envelope reports `waitCoversRunnerDeadline: false`; a call that runs out of wait returns `outcome: "wait-timeout"` with the run still active, a `limitation` string and a `recovery` block naming real commands and the existing run ID. That is a wait/connection limit, never an execution failure and never a reason to relaunch.

```sh
"$BUDDY" status '{"runId":"<runId>"}'
"$BUDDY" await  '{"runId":"<runId>","waitSeconds":28800}'
"$BUDDY" result '{"runId":"<runId>"}'
```

Killing the waiting CLI process (Ctrl-C, closed terminal, `waitSeconds` expiry) ends only the wait. The durable task keeps running while a worker owns it. An `unavailable` envelope means the service was not reachable while waiting; the record is durable, so re-read it by ID.

## Cancellation and stopping

- `"$BUDDY" workflow-cancel '{"runId":"<governed-run>","commandId":"cancel-1","controlFile":"<controlFile>","reason":"..."}'` fences a governed goal and its complete owned descendant graph, cancels open requests and pending continuations, and records cancellation separately from aggregate stop evidence.
- `"$BUDDY" cancel '{"runId":"..."}'` is the legacy operation: it cancels queued work immediately and writes durable cancel intent for an active attempt. The worker that owns the child observes the intent and cancels its own process group; unrelated dsh sessions are unaffected.
- `"$BUDDY" stop` asks the service to stop: queued tasks are cancelled, active attempts get a durable cancel request, the service drains for a bounded interval and reports `unresolvedAttempts`. A `stop` or `restart` with no running daemon reports `alreadyStopped` and starts nothing.
- The task's own execution deadline also ends Buddy-owned work. Those endings are terminal and are never replayed automatically. A new execution requires an explicit `retry`, which creates a new attempt generation and clears the previous acceptance; a governed goal instead receives explicit new input through `workflow-continue`.

Never describe a wait timeout as an execution failure, never restart work the user stopped, and never relaunch because a result was delayed. Recovery invariants are in [operations.md](operations.md#cancellation-and-recovery).

## Background work that outlives the turn

`workflow-submit` and legacy `start` are not reserved for background work: they are the first half of the default foreground flow, which keeps the current turn waiting with `await` (or `run`). When the user explicitly wants the work to outlive the turn:

1. `workflow-submit` or `start` the task and capture the `runId` (and `controlFile` for a governed goal).
2. Register an official App heartbeat automation on the original task before ending the turn; its prompt calls this same CLI to read the result, verify the artifacts and record the governed or legacy acknowledgement.
3. Reuse a matching heartbeat instead of creating duplicates. Keep it quiet while nothing actionable changes; after reviewing the terminal outcome (including failure), record the review and remove the heartbeat. Report unresolved shutdown honestly.

If the App heartbeat tool is unavailable or registration fails, keep waiting in the current turn. Do not end with an unmonitored job or substitute an unrelated scheduled task.

The heartbeat is a periodic background follow-up, not an immediate completion push. Native post-turn App wakeup is not solved by Buddy, and the service never claims it.

## Plugin installation

The repository also ships the `hey-my-buddy` Codex plugin, whose entrypoint is `$buddy`. This route requires a marketplace that contains a `hey-my-buddy` entry; the repository itself is a plugin bundle, not a public marketplace listing.

For an existing configured marketplace, replace `your-marketplace` with its actual name:

```sh
codex plugin add hey-my-buddy@your-marketplace
```

If you maintain a local marketplace, stage this checkout into its plugin directory:

```sh
uv run --frozen --project deepseek-delegate python scripts/stage-plugin.py \
  --destination /path/to/marketplace/plugins/hey-my-buddy
```

Its marketplace manifest must include a local `hey-my-buddy` entry pointing to that directory. Register an explicitly configured marketplace with `codex plugin marketplace add /path/to/marketplace`, then use the install command above with the manifest's marketplace name. The default personal marketplace at `~/.agents/plugins/marketplace.json` is discovered automatically. Reopen the task after installation to load `$buddy`. Keep the complete bundle layout when distributing it; the standalone skill installation above needs no marketplace.

## More than one task at a time

Capacity and cwd/exclusive-resource conflicts queue with a `queueReason` instead of failing. `BUDDY_MAX_CONCURRENT` (1–8, default 1) bounds active attempts and one worker runs one attempt at a time; use separate worktrees for concurrent editors. Independent tasks can be awaited separately by `runId`, and `buddy list` shows the board. Governed helpers reuse the same queue and capacity, so an approved helper waits its turn like any other task.
