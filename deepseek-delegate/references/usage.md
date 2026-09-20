# Using Buddy

This page is the practical path from install to reviewed result. Read [cli.md](cli.md) for every command and field, [operations.md](operations.md) for lifecycle and recovery, and [architecture.md](architecture.md) for how the service works internally.

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

For an independent copy, replace the `ln -s` line with `cp -R "$PWD/deepseek-delegate" "$BUDDY_SKILLS_DIR/deepseek-delegate"` inside that guard, then run `uv sync --frozen --python 3.12` in the copied directory. For an existing installation, follow [the upgrade guidance](operations.md#runtime-lifecycle-and-upgrade) before updating its source or replacing its link. Open a new Codex task to load the skill.

Resolve the launcher from the installed skill's own location:

```sh
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/deepseek-delegate"   # installed location
BUDDY="$SKILL_DIR/scripts/launch-buddy.sh"                         # or: node "$SKILL_DIR/scripts/buddy.mjs"
```

The launcher forwards every argument to the `buddy` CLI and selects Python 3.12 through uv. A cold start automatically materializes a stable versioned runtime outside the Codex plugin cache, so a later plugin update does not require destroying the service; see [operations.md](operations.md#runtime-lifecycle-and-upgrade).

### 2. Decide how the run joins your workspace

A submitted task defaults to `workspace: true`, which groups the dsh session through the running workspace host bridge. Either install that bridge once into the existing dsh `web` profile:

```sh
node "$SKILL_DIR/scripts/install-workspace-bridge.mjs"
```

or pass `"workspace": false` for an intentionally standalone run. The default is never silently downgraded; a grouped run fails honestly when the bridge is missing. Bridge install, validation, and recovery are in [operations.md](operations.md#workspace-bridge).

### 3. Write a bounded task packet

A good packet lets the worker finish without guessing:

- goal and why; expected inputs and outputs;
- the working directory and the commands that check the work;
- files/areas that may change, plus explicit non-goals;
- acceptance criteria: the observable result and the checks to run;
- key references, known facts and pitfalls;
- as much relevant context as the task needs. Task text is bounded at 1 MiB; content over 32,000 bytes is delivered to dsh as a file reference, and that file must stay in place until the run finishes.

### 4. Start once and keep the runId

```sh
"$BUDDY" start '{"requestId":"fix-flaky-test","task":"<task text>","cwd":"/abs/project","timeoutSeconds":7200}'
```

`start` returns immediately with the task view, including `runId`. The task is admitted as `queued`; it begins when a worker claims it and `queueReason` explains any wait (`awaiting-worker`, `capacity`, `cwd-overlap`, `exclusive-resource`). Queueing is intentional: capacity and resource conflicts wait instead of returning BUSY.

`requestId` is the idempotency key. Repeating the identical `start` recovers the same task and never launches the adapter twice; changed input for the same `requestId` is a `CONFLICT`.

Optional `model`, `provider` and `effort` fields override the route for this task. Omitted model/provider values use the runner environment, then settings, then the `deepseek-flash`/`deepseek-official` fallbacks. Omitted effort uses `DSH_DELEGATE_EFFORT` or `max`, never settings. See [runner.md](runner.md#precedence).

### 5. Await that same run

```sh
"$BUDDY" await '{"runId":"<runId>","waitSeconds":7200}'
```

`await` never starts work. It stays connected to the durable task until the task is terminal or this call's window ends (`waitSeconds` 1–86400, default 86400), and prints the final envelope. Its initial status read attaches to a healthy service or starts one if none is running, but it never creates a task. Keep the current Codex turn waiting instead of starting a second run or beginning work on the same files.

`buddy run` is the one-call alternative that starts (or recovers) and waits in a single invocation; its window defaults to `timeoutSeconds + 60 s` shutdown grace, capped at 86400 s. Prefer the split `start` → `await` when you need the `runId` before the task finishes, for example to observe or question it.

### 6. Inspect the result, then acknowledge

```sh
"$BUDDY" status '{"runId":"<runId>"}'
"$BUDDY" result '{"runId":"<runId>"}'
"$BUDDY" artifacts '{"runId":"<runId>"}'
"$BUDDY" acknowledge '{"runId":"<runId>","note":"inspected the diff and ran the suite","verdict":"accepted"}'
```

Exit 0 only means the call returned. Read `status`, `outcome`, `resultDelivered` and `shutdownConfirmed`, then inspect the real diff, files, hashes and logs yourself. Acknowledgement records that review — including a reviewed failure — and never converts a failed execution into a success. Repeating it with a different note or `verdict` is a `CONFLICT`.

## Progress and questions while it runs

```sh
"$BUDDY" inquire '{"runId":"<runId>"}'
"$BUDDY" inquire '{"runId":"<runId>","inquiryId":"q-1","question":"What are you waiting on?","waitMs":20000}'
```

The no-question form is bounded observation: execution state from the durable record, an explicitly estimated deadline, and recent tool activity. It is not a percentage and not a guaranteed ETA, and fields the bridge could not observe are named in `live.unavailable` rather than reported as zero.

A question goes to the run's own live dsh agent; it never starts a second agent and never extends, shortens, pauses or cancels the task. Answers require the correlated reply tool, so assistant prose is never treated as an answer. Questions and answers are bounded at 4000 UTF-8 bytes each, at most 32 inquiries are retained per task, and an adapter without an inquiry capability reports an honest reason. Repeating the same `inquiryId` with identical text returns the recorded state, while the same id with different text is a `CONFLICT`. The full message contract is in [cli.md](cli.md#messages-and-inquiry).

Ask when the user asks or when you need the answer to report honestly; do not build a polling loop.

## When the wait ends before the task

A deadline longer than the wait window is allowed. The envelope reports `waitCoversRunnerDeadline: false`; a call that runs out of wait returns `outcome: "wait-timeout"` with the run still active, a `limitation` string and a `recovery` block naming real commands and the existing run ID. That is a wait/connection limit, never an execution failure and never a reason to relaunch.

```sh
"$BUDDY" status '{"runId":"<runId>"}'
"$BUDDY" await  '{"runId":"<runId>","waitSeconds":28800}'
"$BUDDY" result '{"runId":"<runId>"}'
```

Killing the waiting CLI process (Ctrl-C, closed terminal, `waitSeconds` expiry) ends only the wait. The durable task keeps running while a worker owns it. An `unavailable` envelope means the service was not reachable while waiting; the record is durable, so re-read it by ID.

## Cancellation and stopping

- `"$BUDDY" cancel '{"runId":"..."}'` cancels queued work immediately and writes durable cancel intent for an active attempt. The worker that owns the child observes the intent and cancels its own process group; unrelated dsh sessions are unaffected.
- `"$BUDDY" stop` asks the service to stop: queued tasks are cancelled, active attempts get a durable cancel request, the service drains for a bounded interval and reports `unresolvedAttempts`.
- The task's own execution deadline also ends Buddy-owned work. Those endings are terminal and are never replayed automatically. A new execution requires an explicit `retry`, which creates a new attempt generation and clears the previous acceptance.

Never describe a wait timeout as an execution failure, never restart work the user stopped, and never relaunch because a result was delayed. Recovery invariants are in [operations.md](operations.md#cancellation-and-recovery).

## Background work that outlives the turn

`start` is not reserved for background work: it is the first half of the default foreground flow. When the user explicitly wants the work to outlive the turn:

1. `start` the task and capture the `runId`.
2. Register an official App heartbeat automation on the original task before ending the turn; its prompt calls this same CLI to read the result, verify the artifacts and acknowledge the run.
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

## Optional modes

**Standalone (ungrouped) runs.** Pass `"workspace": false` for a run that must not touch the workspace bridge, or `--no-workspace` for the raw runner ([runner.md](runner.md)).

**`command` adapter.** One explicit `argv` process, never a shell:

```sh
"$BUDDY" submit '{"requestId":"echo-1","task":"print a greeting","cwd":"/abs/path","adapter":"command","argv":["/bin/echo","hi"]}'
```

**`external` adapter.** No local process: the caller's own agent claims the task through the public client and submits the result itself. A runnable example, worker identity and receipt rules are in [workers.md](workers.md).

**Multiple tasks.** Capacity and cwd/exclusive-resource conflicts queue with a `queueReason` instead of failing. `BUDDY_MAX_CONCURRENT` (1–8, default 1) bounds active attempts and one worker runs one attempt at a time; use separate worktrees for concurrent edits. Independent tasks can be awaited separately by `runId`, and `buddy list` shows the board.
