# Buddy C-Two service


The plugin exposes a **CLI** (`buddy`) backed by the PyPI **C-Two** package pinned in `uv.lock` (currently 0.5.1). There is no MCP server, no `mcp` Python dependency and no SDK facade. `uv.lock` fixes the complete Python dependency graph, and this implementation does not import a sibling C-Two/FastDB checkout. Python is `>=3.12,<3.15`; the bundled launcher selects 3.12 through uv, and Node.js is an external runtime that dsh itself needs. Install with `uv sync --project deepseek-delegate --python 3.12`, then use `deepseek-delegate/scripts/launch-buddy.sh health` (or `node deepseek-delegate/scripts/buddy.mjs health`).

## Ownership

The CLI calls a private `BuddyControl` resource over C-Two direct IPC. One Python daemon owns that resource and one Node execution engine. Node uses built-in modules only and retains the tested dsh runner/process-group behavior. YAML parsing uses uv-managed PyYAML. Service requests and results travel over C-Two; the daemon-to-engine hop is line-delimited JSON over stdio and the engine spawns `scripts/run.mjs` as a child, so not every internal hop is C-Two. The engine's `run_completed` notification event was removed together with its only consumer (the MCP completion delivery); results are read through `wait`/`status`/`result` on the same endpoint.

The daemon never starts, stops or rewrites an unrelated dsh host. Each run uses its own settings copy, logs and dsh process group. Service runs default to `workspace: true`, which requires the installed, running dsh host bridge (see the skill's workspace grouping section); `workspace: false` opts out, and only Buddy-owned runs are controlled. A `cwd` is a working directory, not a sandbox, and a CLI command runs with the calling task's shell permissions — Buddy never grants extra privilege, and the service is a same-user local process. Workspace grouping still goes through the existing dsh host bridge, which owns its workspace storage. `BUDDY_MAX_CONCURRENT` defaults to 1, with values 1–8; overlapping canonical working directories are rejected even at higher concurrency. External dsh work still shares machine/provider resources and may edit the same files, so coordinate those separately.

## Default blocking delegation

The default workflow is `buddy start` → `buddy await` inside the current turn: `start`
returns a `runId` immediately (or recovers the existing run by `requestId`), and `await`
stays connected on that same durable run until it is terminal or the wait window ends.
Capturing the runId before waiting is what lets `buddy inquire` observe or question the
run while it is in flight. `buddy run` remains an optional one-call convenience: it starts
(or recovers) one durable job by `requestId`, waits inside that single CLI invocation, and
prints the final envelope:
`runId`, execution `status`, `outcome`, `shutdownConfirmed`, the runner `result`, log
paths and, when relevant, recovery instructions. Waiting is ordinary synchronous code in
the CLI facade: it re-arms the engine's existing event wait (`wait` semantics, 30 s
slices) until the run is terminal or the wait window is spent. There is no heartbeat,
no cron, no busy loop and no model-visible polling. Concurrent status, list, inquire and
cancel calls are handled in separate processes (and separate engine dispatches) and are
not blocked by a pending `buddy run` or `buddy await`.

`buddy start` returns a run ID immediately; it is the first half of the default
start → await flow and becomes the background route when work must outlive the turn. In
that case the Buddy skill registers an official App heartbeat against the same task before
ending its turn. The heartbeat's prompt calls this same CLI
with the existing run ID and request ID, checks the saved run through C-Two, stays quiet
while it is running, then verifies artifacts, acknowledges the result and deletes itself.
A matching heartbeat must be reused rather than duplicated. App scheduling supplies the
wakeup; C-Two supplies status and result communication. This checks periodically, usually
about once a minute; it is not an immediate completion push. App availability, scheduling
and model processing affect latency. Buddy does not claim native post-turn App wakeup.

If heartbeat creation fails, keep the current turn waiting or report the blocker.
The scheduler never receives authority to relaunch work. Completed results remain
recoverable by ID, and acknowledged results do not need repeated acceptance.

## Lifetimes: execution deadline, wait window, process lifetime

Three different limits must not be confused:

| Limit | Where | Default | What it bounds |
| --- | --- | --- | --- |
| DSH execution deadline | `timeoutSeconds` on CLI `run`/`start` | 1800 s (10–86400) | the whole spawned dsh process group (wall clock from spawn), covering every model and tool step — not a model-turn limit |
| Blocking wait window | `waitSeconds` on CLI `run` | runner deadline + 60 s shutdown grace, capped at 86400 s (1–86400) | how long one blocking `run` keeps waiting |
| CLI `await` wait | `waitSeconds` on the CLI `await` subcommand | 86400 s (1–86400) | how long that wait stays connected; it never starts work |

Reaching the wait window returns `outcome: "wait-timeout"` and never changes the
execution deadline: the run keeps its concurrency slot, keeps running and is recovered
by `requestId`/`runId`. Reaching the execution deadline makes the runner stop the owned
process group and is reported as a terminal `failed` outcome with the runner's
`status: "timeout"`. Killing the waiting CLI process (Ctrl-C, closed terminal, user stop)
can cut a call without an envelope; the durable run still survives it, and killing the
*wait* never cancels the job.

The default workflow is `buddy start` then `buddy await` on the returned runId, which keeps
one CLI call short and the wait explicit. `buddy run` is the one-call alternative: it covers
the runner deadline plus the shutdown grace by default, so one invocation normally covers
the whole job. Either way a wait window is bounded, so for a deadline beyond the 24 h
maximum, or to split a wait deliberately, start the run and await the same runId/requestId:

```sh
BUDDY=<plugin-root>/deepseek-delegate/scripts/launch-buddy.sh
"$BUDDY" start '{"requestId":"x","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
"$BUDDY" await '{"runId":"<runId>","waitSeconds":28800}'
```

The 1800 s default is a **Buddy execution timeout**, not an "agent turn limit" of the
harness and not a limit on how long the model may think. It is a wall-clock bound on
the one spawned dsh process group (`scripts/run.mjs` arms it after spawn and signals
the owned group on expiry). A long task is expected to pass an explicit value, for
example `"timeoutSeconds": 28800` for 8 hours (maximum 86400 = 24 h). `buddy inquire`
never extends, shortens, pauses or restarts that timer.

## CLI wait window and upgrade ordering

**Wait window.** There is no host tool timeout on this path and no
`BUDDY_TOOL_WAIT_BUDGET_SECONDS`. `buddy run` picks
`min(86400, timeoutSeconds + 60)` seconds, an explicit `waitSeconds` (1–86400) always
wins, and `buddy await` uses its own explicit `waitSeconds` (default 86400). A multi-hour
`timeoutSeconds` therefore gets a multi-hour window; nothing here
imposes an implicit ~55 minute cap. Reaching the window is a wait limit, not an execution
failure: the envelope carries `outcome: "wait-timeout"`, `waitCoversRunnerDeadline: false`,
a `limitation` string and a `recovery` object whose `commands` name real CLI invocations
with the existing run ID (`buddy status`, `buddy await`, `buddy result`, `buddy cancel`).

**A run is never created without a usable runner.** `JobManager.start()` validates the
runner entrypoint (exists, regular file, non-empty, readable) *before* it creates the run
directory, the durable record or the child process. A missing entrypoint fails with
`RUNNER_UNAVAILABLE` and leaves no run record behind. This is deliberate: Node reports a
missing entrypoint by exiting 1 with an empty stdout, which the normal finish path cannot
distinguish from a crashed runner — the old behaviour recorded a `failed` run with
`shutdownConfirmed: false`, and that record then blocked every later start through the
`SHUTDOWN_UNCONFIRMED` gate until an operator reconciled it. Idempotent recovery of an
already existing run still answers from its durable record even on a broken install.

**Upgrade ordering (operator).** Quiesce and **stop the owned service before replacing the
plugin cache version**, including docs-only cachebuster updates:

```sh
<plugin-root>/deepseek-delegate/scripts/launch-buddy.sh stop   # or the previous version's launcher
# then install/refresh the plugin version
```

A live daemon keeps using the code path it loaded at start, and `scripts/run.mjs` is
resolved from the engine's own directory at load time. Replacing the cache directory under
a running service can therefore leave the daemon (and its engine) executing from a deleted
path. With the preflight guard the next start fails loudly as `RUNNER_UNAVAILABLE` instead
of poisoning state, but stopping first is the supported order and avoids the stale process
entirely. After an upgrade, start the service from the new version and confirm
`buddy health` answers from the new bundle before delegating work.

## Long jobs

A runner deadline longer than the wait window is explicitly allowed. The envelope then
carries `waitCoversRunnerDeadline: false` and a `limitation` string, and a call that runs
out of wait returns `outcome: "wait-timeout"` — never `failed`, and the job is never
relaunched. Two supported paths:

1. Use the default start → await flow: `buddy start` returns a runId immediately, then
   `buddy await` waits on that same durable run with its own explicit window:

   ```sh
   BUDDY=<plugin-root>/deepseek-delegate/scripts/launch-buddy.sh
   "$BUDDY" start '{"requestId":"<id>","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
   "$BUDDY" await '{"runId":"<runId>","waitSeconds":28800}'
   ```

   `await` accepts `waitSeconds` 1–86400 (default 86400) and never starts work.
2. Or let one `buddy run` cover it: the default window is `timeoutSeconds + 60 s` (capped
   at 86400 s), so a job whose deadline fits in 24 h is covered by a single invocation.
   Repeating the identical `buddy start` or `buddy run` recovers the same run.

## Cancellation and recovery

Different cancellations are kept separate:

- Killing the waiting CLI process (Ctrl-C, closed terminal, user stop) or reaching
  `waitSeconds` cancels only the *wait*. While the owner service is alive, the owned dsh
  job keeps running, keeps its concurrency slot and stays recoverable.
- `buddy cancel '{"runId":"..."}'` stops the named Buddy-owned run; unrelated dsh sessions
  are unaffected.
- The run's own runner execution deadline and a service stop (`buddy stop`, or the owner
  service shutting down) also terminate Buddy-owned work by signaling the owned process
  group. Those endings are terminal, are reported as `timeout`/`cancelled` or
  `interrupted`, and are never replayed automatically.

Recovery uses `requestId` as an idempotency key. Re-running the identical `buddy start` or
`buddy run` (requestId, task, cwd and options) returns the same existing run and never
launches dsh twice; the returned `runId` is the same. A `wait-timeout` envelope is a wait/connection
limit, not an execution failure: the run's status stays active, it carries the runId and a
`recovery` block of real CLI commands, and neither `buddy cancel` nor a replay is implied.
An `unavailable` envelope means the service could not be reached while waiting; the run
record is durable, so re-run or read it by ID. Terminal `interrupted` means the service
stopped before runner shutdown was confirmed: inspect surviving processes before any
manual recovery and never replay the run.

Restarting the owner service marks every previously active job `interrupted` with
`shutdownConfirmed: false` and `resultAvailable: false`. Nothing is taken over, resumed
or relaunched automatically, and durable records/results plus same-requestId recovery
are not crash-proof recovery of a process that may still be running: a stored record
proves state, not that execution survived.

A blocking wait cannot physically prevent the user from stopping the turn or closing
the App. User stop/pause instructions take precedence over continuation; interrupted
work is never restarted automatically.

The removed MCP path also removed the native App completion notification: the plugin's
completion inbox, its binding/`notification_ack` bookkeeping, the daemon delivery thread
and the native stdio client are gone. Historical `notifications/*.json` files are left
untouched, are never read and are never replayed by the service; only the CLI result path
(`status`/`wait`/`result`/`await`) delivers results now. This is unrelated to the
experimental App-notification receiver that was never part of the supported workflow.

## Inquiry: bounded progress and correlated questions

`buddy inquire` asks one owned run what it is doing, and optionally delivers one
operator question to that run's live agent. It is a CLI/service feature with no automatic
scheduling: an agent with shell access calls it explicitly when the task's permissions
allow it.

```sh
# Read-only progress for one owned runId:
uv run --frozen --project deepseek-delegate buddy inquire '{"runId":"<runId>"}'

# One question for the SAME live agent; stable inquiryId; no second model agent:
uv run --frozen --project deepseek-delegate buddy inquire '{"runId":"<runId>","inquiryId":"q-1","question":"what is blocking you?"}'

# Same, waiting up to 30 s for the correlated answer to arrive at the next boundary:
uv run --frozen --project deepseek-delegate buddy inquire '{"runId":"<runId>","inquiryId":"q-1","question":"what is blocking you?","waitMs":20000}'
```

Accepted parameters: `runId` (required); `inquiryId` + `question` (optional, supplied
together, `inquiryId` matches `[A-Za-z0-9._:-]{1,128}`, question ≤ 4000 UTF-8 bytes);
`timeoutMs` (100–5000, default 1500) bounds one bridge request; `waitMs` (0–30000,
default 0) bounds how long this call waits for an answer and never extends, shortens,
pauses or cancels the run. Unknown parameters are rejected. To read a specific question
back, repeat both the same text and the same `inquiryId`; an id alone is not a valid
lookup, and the same id with different text is a `CONFLICT` for active and terminal
runs alike.

### What a no-question inquiry reports

- Authoritative execution state straight from the durable run record: `status`,
  `revision`, `createdAt`/`updatedAt`, `resultAvailable`, `shutdownConfirmed`,
  `exitCode`, `cancelRequestedAt` and the private log paths.
- `deadline`: the recorded `timeoutSeconds`, the derived `startedAt`/`deadlineAt`,
  `elapsedSeconds`, `remainingSeconds` and `expired`, all labelled as an ESTIMATE:
  `estimated: true`, `kind: "estimated-runner-deadline-from-record-createdAt"`,
  `clockOrigin: "run record createdAt (before runner preflight and before dsh spawn)"`,
  `deadlineBasis: "createdAt + timeoutSeconds"` and `exactTimingAvailable: false`. The
  runner starts its own timeout timer only after preflight and after dsh is spawned, so
  the real deadline is later than `deadlineAt`; inquiry never extends, shortens or
  restarts it. For a terminal run `measuredTo` is that run's last durable `updatedAt`
  and elapsed time is frozen there instead of accumulating while someone keeps asking.
  When an old record predates the field it reports `available: false` with the reason
  instead of inventing a deadline.
- `live`: what the run's own bridge can actually observe — `agentStatus`
  (`idle`/`running`), inbox depth, `lastEvent` (type/seq/time) and up to 20 bounded
  activity entries for tool calls (tool name, phase, timestamps, duration, error flag,
  and a ≤160-character preview of the tool ARGUMENTS only). This is bounded observation,
  not a percentage or a guaranteed ETA: activity can be empty, stale or incomplete, and
  anything the bridge did not observe is named rather than reported as zero.
- Every field the bridge could not observe is named in `live.unavailable`, and
  `live.available: false` carries a `reason` (`run-completed`, `bridge-unreachable`,
  `bridge-start-failed`, `bridge-timeout`, …).

`live.agentStatus: "running"` means an agent driver is active. It is **not** a claim of
productivity: a run can be running while stuck, retrying or waiting on a long tool, and a
long tool call can delay an answer until a later step. An idle or terminal agent cannot
be awakened by an inquiry. Raw model reasoning is never exposed, and neither the
assistant stream nor the assistant text is copied into this payload.

### What a question does

1. The service validates and durably records the inquiry in the run's private record
   *before* anything is injected. Repeating the same `inquiryId` with identical text
   returns the recorded state (and can still read an existing answer) and never injects
   twice; the same id with different text is a `CONFLICT`. Every valid id is stored as an
   own property, so ids such as `constructor`, `toString` or `__proto__` behave like any
   other. At most 32 inquiries are retained per run.
2. It asks the run's own bridge to deliver the question through the public
   `agent.steer(UserMessage)` API of the SAME live agent. Nothing starts a second
   agent, cancels, restarts, re-scopes or extends the run. The request is refused
   (`state: "unavailable"`, `recorded: false`) when the run is already terminal, and also
   when the owned agent is not actually `running`: public `steer` would WAKE an idle
   agent, so a late question during headless's `whenIdle`/flush/exit boundary can never
   reopen a finished task.
3. `steer` queues into the agent's next-step inbox. While the agent is inside a long
   tool call the answer is `state: "queued"` with the observed activity. `claimed` means
   the loop took the message for a proposed step (`claimedAt` recorded); dsh claims
   BEFORE its `agent/pre-step` waterfall, so a rejected pre-step drops the batch and a
   claim is never reported as delivery. `delivered` (with `deliveredAt`) is recorded only
   from this run's own session's durable `user/message` event carrying the correlated
   message id — the commit dsh performs before the model call.
4. The answer channel is a scoped reply tool (`buddy_inquiry_reply`) registered for
   that one agent. An answer is reported only when the tool call carries the exact
   `inquiryId` AND the inquiry was already delivered; blank answers are refused, and a
   question the loop never committed can never be answered. Assistant text is never
   treated as an answer. `state: "answered"` carries the answer text (≤4000 UTF-8 bytes,
   truncated on a code-point boundary with `bytes`/`truncated` metadata), the tool call
   id, the timestamp and its provenance (`live-bridge` or `bridge-journal`). The first
   correlated answer wins; later observations never clear or replace it.
5. The bridge appends a bounded lifecycle journal next to its socket, so an answer that
   arrived before the run ended is still readable afterwards. `discarded` means the
   agent dropped the injected message before a boundary; it is reported as discarded,
   never as an answer. Once the bridge closes (the run has ended), any question that can
   no longer be claimed or answered is reported as `unavailable`, including a question
   that was submitted and then ended without a correlated answer; a terminal run never
   keeps a misleading permanent `queued` state.

No automatic timer- or model-triggered inquiry is implemented: there is no scheduled
heartbeat and no automatic prompting, and the supervising agent should not build a
polling loop. An operator, or the supervising agent on an explicit user request (or when
it needs the information to report honestly), decides when to call the CLI. An explicit
`buddy inquire` call is the only supported way to ask, subject to the calling task's
shell permissions.

### Transport and ownership

Each owned run mounts one private Cordis plugin (`plugins/inquiry-bridge.mjs`) through
the same temporary `--patch` overlay that already carries the settings copy and the
session-capture observer. Nothing is installed globally, no installed dsh file is
patched, and the plugin uses only Node builtins. Decisions and their reasons:

- **Transport**: a Unix domain socket in the run's own `0700` directory
  (`<BUDDY_STATE_DIR>/<runId>/inquiry.sock`; a state directory too deep for the
  platform's `sun_path` limit falls back to a short owner-private per-run directory
  under `/tmp`). One bounded newline-terminated JSON frame per connection, 16 KiB
  request / 32 KiB response, 10 s frame window, no streaming and no multiplexing.
- **Authentication**: every frame must carry the per-run 256-bit token the service
  generated and passed to this run's wrapper as an argument; the wrapper writes it only
  into that run's private patch file, and public views and envelopes redact it. The
  socket is `0600`, its parent `0700`, and the listener removes only the inode it
  created; a run whose socket path is already occupied reports `bridge-start-failed`
  instead of replacing it.
- **Correlation and identity**: the bridge binds exactly one root session whose
  `header.cwd` equals this run's canonical cwd and whose first ordinary user message
  hashes to the exact prompt this run delivered. Two matching root sessions make the
  binding ambiguous and the bridge refuses to guess. The reply tool is registered in
  that agent's own scope when the runtime supports it, and always re-checks the caller.
- **Ownership boundary**: the CLI reaches the service through C-Two,
  and the service (Node engine) owns the record mutation and the socket I/O; the runner
  wrapper receives the inquiry arguments when the service spawns it and mounts the
  bridge. The bridge token never appears in any client-visible view or envelope, and
  `inquire` runs outside the manager's serialized dispatch tail, so a pending
  `buddy await` is never blocked by inquiry transport.
- **Lifecycle**: the bridge closes and removes its own socket on plugin disposal; once
  the owned process group is confirmed stopped the service removes any socket or
  failure file left behind by a SIGKILL. The bounded journal stays in the private run
  directory as durable evidence.
- **Failure mode**: a bridge that cannot start never fails the run. The run result
  carries `inquiry.enabled: false` with the exact error, and every later `inquire`
  reports the same reason instead of faking progress.

Not claimed: inquiry cannot see inside a running tool, cannot force an answer from a
model that ignores the injected message, and cannot recover an answer that was never
recorded by the reply tool or the bridge journal. When any of those happens the
envelope says `delivered`/`queued` with `answer.available: false` and the observed
activity, and it is never reported as answered.

## CLI commands

| Command | Parameters / behavior |
| --- | --- |
| `buddy run` | Optional one-call convenience: `requestId`, `task`, `cwd`; optional `model`, `provider`, `effort`, `timeoutSeconds`, `workspace`, `waitSeconds` (1–86400). Starts or recovers once, stays connected until the run is terminal or this call's wait window ends, prints the final envelope with `outcome`/`limitation`/`recovery`; concurrent status/inquire/cancel still work. A runner deadline longer than the wait is allowed and is never reported as execution failure |
| `buddy start` | Same start parameters without `waitSeconds`; returns a runId immediately. First step of the default start → await flow, and the route when work must outlive the turn |
| `buddy await` | `requestId` or `runId`, optional `waitSeconds` (1–86400, default 86400); waits on an existing run and never starts work |
| `buddy status` | `runId`; compact execution state |
| `buddy wait` | `runId`, optional `afterRevision`, `timeoutMs` 0–30000; one bounded event wait |
| `buddy result` | `runId`; actual runner JSON and log paths |
| `buddy list` | optional `limit` 1–100 and `offset` |
| `buddy cancel` | `runId`; stops the named owned run (unrelated dsh sessions are unaffected). The runner's own execution deadline and a service stop also end owned work |
| `buddy acknowledge` | `runId`, evidence `note`; records review of a real outcome — including a reviewed failure — once the result is persisted and shutdown is confirmed. It never changes the execution status or turns a failure into success |
| `buddy inquire` | `runId`; optional `inquiryId`+`question`, `timeoutMs`, `waitMs`. See the inquiry section above |
| `buddy dashboard` | private read-only local panel URL |
| `buddy health` | service health and the engine's state directory |
| `buddy stop` | stops only this owned service (and its owned processes); it never signals unrelated dsh sessions |

```sh
BUDDY=<plugin-root>/deepseek-delegate/scripts/launch-buddy.sh
# Default: start once, keep the runId, then await that same durable run (never starts work):
"$BUDDY" start '{"requestId":"x","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
"$BUDDY" await '{"runId":"<runId>","waitSeconds":7200}'
# Optional one-call convenience: blocking run whose default window covers timeoutSeconds + 60 s:
"$BUDDY" run '{"requestId":"x","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
# Bounded progress, and one correlated question to the same live agent:
"$BUDDY" inquire '{"runId":"<runId>"}'
"$BUDDY" inquire '{"runId":"<runId>","inquiryId":"q-1","question":"what is blocking you?"}'
```

There is no automatic timer- or model-triggered scheduling for inquiry: a model with
shell access calls the CLI explicitly when the task's permissions allow it.

CLI service subcommands print one JSON object, and exit 0 only means the call returned.
It is not job success: inspect `status`, `outcome`, `resultDelivered` and
`shutdownConfirmed`, then read `buddy result` and verify the actual artifacts. The
standalone `scripts/run.mjs` runner has a different contract: its own exit codes 0/1/2 and
its raw single-line result object (`status`, `exitCode`, `workspace.bound`,
`processState.shutdownConfirmed`).

## Persistence and recovery

`BUDDY_STATE_DIR` defaults to `~/.local/share/hey-my-buddy`. Records and endpoint tokens are private. A lifetime file lock gives one daemon ownership of the state directory; a legacy socket guard prevents an old cached Node daemon from starting over the same records. Historical `notifications/` files from the removed MCP path stay on disk untouched and are never read, mutated or replayed. Upgrade ordering (quiesce and stop the owned service before replacing a plugin cache version, including docs-only cachebuster updates) is specified in [CLI wait window and upgrade ordering](#cli-wait-window-and-upgrade-ordering).

An already healthy service is attached read-only: the client reads the private endpoint and confirms health over C-Two, without creating directories, changing permissions, taking lock files or writing logs. That matters when the state directory is readable but not writable, for example under a restricted sandbox. Only a cold start performs the authorized setup writes (create the directory, apply `0700`, lock, log, spawn), and it still fails honestly when those writes are not permitted. Endpoints are trusted only when the directory and `control.json` are owned by the current user with no group/other access and no symlink in the way; anything else falls through to cold startup instead of being used.

A request ID is an idempotency key. Retry the same task and ID after an uncertain start; changed input is rejected. Results survive service restart. Runs interrupted before shutdown was confirmed are not replayed and block new execution until inspected. No stored PID is used to signal old processes.

`buddy stop` is an administrative CLI action: it gracefully stops the service and the
Buddy-owned work the engine owns (owned children are cancelled before the engine
exits). It does not stop an unrelated dsh process. An absent service is not launched
just to stop it. Complete results and every durable run record remain on disk.

## Dependencies and validation

All third-party libraries are managed by uv and locked: PyPI `c-two==0.5.1`, PyYAML, and their pinned transitive dependencies. The `mcp` Python SDK is not a dependency and must not be importable in the project environment. Node.js remains an external runtime required by dsh. No npm package install is required for Buddy.

```sh
uv sync --project deepseek-delegate --python 3.12
uv run --frozen --project deepseek-delegate python -m buddy.checks
```

Checks include real cross-process PyPI C-Two IPC across separate CLI processes (concurrent clients, one daemon, unauthorized dispatch rejected), start idempotency without a duplicate dsh launch, durable results and terminal truth, a killed waiting CLI that never cancels the owned job, explicit short-wait recovery on the same run, deadline-versus-wait-window behavior (a multi-hour deadline still gets a covering window), concurrent health/status/list/inquire/cancel responsiveness during a pending run, the CLI wait-window math, the per-run inquiry bridge and service inquiry state machine, the retained legacy guard/cold-start/trust-boundary behavior, the runner-entrypoint preflight guard, launcher portability from an unrelated cwd with a minimal PATH, and the existing mock-dsh process/grouping suite. Live App/dsh integration is verified separately; unit tests never imply that a paid model run was exercised.

## Packaging, migration and unrelated App tools

The portable `plugin.json` and the `.codex-plugin/plugin.json` metadata describe a
skills+CLI plugin: there is no `mcp.json` companion, and `scripts/stage-plugin.py` refuses
to stage a tree that still contains one (or any other removed MCP facade path). The
contained launcher (`scripts/launch-buddy.sh`, or `scripts/buddy.mjs` for Node) resolves
the project from its own location, makes uv reachable under a minimal PATH and forwards
every argument to the `buddy` CLI. The launcher selects Python 3.12 through uv and uses a
version-specific environment under `PLUGIN_DATA` when provided; it does not depend on the
calling task's working directory.

To stage a distributable copy without environments, logs, or experiment artifacts, use
`uv run --project deepseek-delegate python scripts/stage-plugin.py --destination /path/to/hey-my-buddy`.
The destination is separate from the source checkout and prior staged copies are retained
as backups.

**User migration (manual; Buddy never edits user config).** Removing the MCP path means
old registrations are dead weight. Delete them yourself when convenient:

- `[mcp_servers.buddy_ctwo]` and any `plugins."hey-my-buddy@…".mcp_servers.*` entries in
  `~/.codex/config.toml`, including their `tool_timeout_sec`, `approval_mode` and
  `BUDDY_TOOL_WAIT_BUDGET_SECONDS` fields;
- any plugin-scoped Buddy tool approval remembered by the App;
- the now-unused `mcp.json` in an older plugin cache copy.

Nothing replaces them: the CLI needs no registration, and its commands run with the
permissions of the shell that invokes them.

Unrelated Codex/App tools are untouched. In particular the official App heartbeat
automation used for explicit background follow-up is a separate tool: its prompt simply
calls the Buddy CLI, and it can still be approved independently.

```toml
[plugins."codex-app-tools@openai-bundled".mcp_servers.codex_app.tools.automation_update]
approval_mode = "approve"
```

These policies persist across restarts and plugin version updates while the identifiers
stay the same. Existing task connections may retain earlier policies until unloaded and
resumed. Verify adoption with a real tool call; parsing the TOML alone is insufficient.
An already pending approval is not retroactively approved by changing the file. The App's
`Approve for me` option instead evaluates eligible calls through its approval reviewer.
