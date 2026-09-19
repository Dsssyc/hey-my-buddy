---
name: deepseek-delegate
license: MIT
description: Delegate clear, bounded implementation, investigation, file-transformation, testing, or documentation work to a local DeepSeek Harness (dsh) run early, then verify the real artifacts. Use when a task is well scoped and clearly bigger than a one-line edit; skip trivial one-step tasks and work whose requirements are still moving.
---

# deepseek-delegate

Hand **one bounded task** to a local `dsh` headless run and get back **one compact JSON result**.
Codex keeps framing, route choice, and final acceptance; dsh does the bulk work.

## Codex plugin route

The plugin exposes a **CLI**, not MCP tools. Resolve the launcher from this loaded skill's
actual location: the removed MCP path used to supply a `PLUGIN_ROOT` environment variable,
and nothing supplies it any more. This skill's own directory holds the launcher:

```sh
SKILL_DIR='/abs/path/to/installed/deepseek-delegate'   # directory of this loaded SKILL.md
BUDDY="$SKILL_DIR/scripts/launch-buddy.sh"             # or: node "$SKILL_DIR/scripts/buddy.mjs"
```

The default workflow is **start once, capture the runId, then await that same run in the
current turn**. Use one JSON argument per command and quote `"$BUDDY"` so install paths
with spaces work:

```sh
"$BUDDY" start '{"requestId":"<stable-id>","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
# -> prints the new (or recovered) runId immediately; keep it
"$BUDDY" await '{"runId":"<runId>","waitSeconds":28800}'
```

`buddy await` never starts work, and because the runId is captured before waiting,
`buddy inquire` can observe or ask that same run while the await is still in flight. Keep
the turn waiting — there is no heartbeat, no cron and no polling. Then inspect the real
artifacts, run the relevant checks and record acceptance with
`"$BUDDY" acknowledge '{"runId":"…","note":"…"}'`. Re-running the identical `start` (or the
identical `buddy run`) recovers the same run and never launches dsh twice.

`buddy run` remains an optional one-call convenience: it starts (or recovers) one durable
job by `requestId` and stays connected until the run finishes, with `waitSeconds`
defaulting to `timeoutSeconds + 60 s` shutdown grace capped at the 24 h CLI maximum
(86400 s):

```sh
"$BUDDY" run '{"requestId":"<stable-id>","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
```

Wait windows are now CLI-owned. `buddy run` covers the runner deadline plus the shutdown
grace by default, so one invocation normally covers the whole job; `buddy start` →
`buddy await` splits the same durable run into an immediate runId plus an explicit
`waitSeconds` window (1–86400, default 86400). There is no MCP host timeout to work around
any more. The DSH execution deadline (`timeoutSeconds`) stays a **Buddy execution
timeout**, not a harness model-turn limit: it is the wall clock on the whole spawned dsh
process group, armed from spawn and covering every model and tool step. It defaults to
1800 s (integer 10–86400) and must be set explicitly for longer work (for example
`"timeoutSeconds": 28800` for 8 hours, maximum 86400). `buddy inquire` never extends it.
See
[plugin-service.md](references/plugin-service.md#cli-wait-window-and-upgrade-ordering).

A runner deadline longer than the wait window is allowed: the envelope reports
`waitCoversRunnerDeadline: false` and returns `outcome: "wait-timeout"` while the run stays
active — that is a wait/connection limit, never an execution failure and never a reason to
relaunch. Recover with the identical requestId, or wait on that same durable run:

```sh
"$BUDDY" await '{"runId":"<runId>","waitSeconds":28800}'   # never starts work
"$BUDDY" status '{"runId":"<runId>"}'                      # authoritative state
"$BUDDY" result '{"runId":"<runId>"}'                      # persisted runner result
```

Killing the waiting CLI process (Ctrl-C, closed terminal, `waitSeconds` expiry) cancels
only the wait; while the owner service is alive, the owned job keeps running. `buddy
cancel '{"runId":"…"}'` stops the named owned run, and the runner's own execution deadline
or a service stop (`buddy stop` / owner shutdown) also terminate Buddy-owned work; after
an owner restart, active jobs are `interrupted` with shutdown unconfirmed and nothing is
resumed automatically. If a wait ends with a `wait-timeout` or unavailable envelope, keep
monitoring or report the runId explicitly; do not end the turn with an unmonitored running
job, and never restart work the user stopped.

When a run has been quiet for a long time, `buddy inquire` reads bounded progress and can
ask the run's own live agent one correlated question without cancelling, restarting or
extending it:

```sh
"$BUDDY" inquire '{"runId":"<runId>"}'
"$BUDDY" inquire '{"runId":"<runId>","inquiryId":"q-1","question":"What are you waiting on right now?","waitMs":20000}'
```

States never overstate progress: `queued` (durably pending at the next step boundary),
`claimed` (the inbox message was consumed for a proposed step — a rejected pre-step is
never delivery), `delivered` (durably committed to this run's own session as the
model-visible `user/message` for the correlated message id), `answered` (the correlated
reply tool recorded it), `discarded` (dropped before a boundary), or `unavailable`
(delivery or an answer is unavailable: a late question may be refused, or a previously
delivered question may end without an answer). An inquiry never wakes a finished task.
Repeating an `inquiryId` never injects twice; the same id with
different text is a `CONFLICT` in active and terminal runs, and reading a specific
question back requires both the same text and the same id (an id alone is not a lookup).
Deadline values are estimates from `record.createdAt + timeoutSeconds`, not the exact
spawn, and a no-question inquiry is bounded observation rather than a percentage or ETA.
`waitMs` (max 30000) only bounds that call's wait and never extends or cancels the run.
It is manual on purpose: no automatic timer- or model-triggered inquiry exists, so ask
explicitly when the user asks or when you need the information to report honestly.
Size limits, retention and the full bridge protocol:
[plugin-service.md](references/plugin-service.md#inquiry-bounded-progress-and-correlated-questions).

`buddy start` returns a `runId` immediately and is the first step of the default
start → await flow above; it is also the background route when work must outlive the turn.
For that, follow the plugin's Buddy skill and register an official App heartbeat for the
exact run before ending the turn; the heartbeat's prompt calls this same CLI, then reads
the result, verifies and acknowledges it. Scheduling is periodic; it is not immediate dsh
push, and native post-turn App wakeup is not solved by Buddy. Recover existing runs by ID;
never relaunch because a result or follow-up is delayed.

## Route early

Delegate before doing the work yourself when the task is clear, bounded, and bigger than a
one-line edit: implementation, refactor, bounded investigation, batch file transformation, test
writing, documentation.

Keep it local when the change is a line or two, the answer is already known, requirements still need
discovery, or the call is Codex-only (framing, route choice, accepting the result).

## Write the task packet first

Put the packet in a file and include: goal and why; inputs/outputs; the working directory and
available check commands; the files/areas that may change and explicit non-goals; acceptance
criteria; and key references, known facts, and pitfalls.

Give the context the work needs. Context capacity is configured per model and provider — some models
in a dsh catalog are configured around 1M tokens, which is an example, not a promise across
providers. Do not shrink the packet artificially. Keep large background in files: task content over
32,000 bytes is delivered to dsh as a file reference, and that file must stay in place until the run
finishes.

## Pick the model and effort

- Choose a model for the task's difficulty and needs; never rank by Pro/Flash in the name.
- Defaults are provider `deepseek-official`, model `deepseek-flash`, effort `max`. They are
  configurable defaults, not a capability ranking.
- Effort defaults to `max` and is never silently inherited from a lower value in settings.
- A model ID must be supported by the selected provider. The CLI does not validate, downgrade, or
  retry it; report failures honestly.
- Precedence: `--model` > `DSH_DELEGATE_MODEL` > settings `agent-default-model.model` >
  `deepseek-flash`; `--provider` > `DSH_DELEGATE_PROVIDER` > settings > `deepseek-official`;
  `--effort` > `DSH_DELEGATE_EFFORT` > `max`.

## Run it

Before launching work that may outlive the current turn, choose and establish an
owner-resumption route. Read [handoff.md](references/handoff.md): App/ChatGPT App
uses a verified heartbeat on the original task; CLI uses a completion callback
only after an actual same-thread wakeup test. Use `scripts/handoff.mjs` for these
routes; it wraps the runner below and persists results independently of notification.
If neither route is available, keep waiting in the current turn with `run.mjs`.
Do not end with only “delegated” while an unmonitored run is still active.

```sh
node <skill-dir>/scripts/run.mjs --cwd <dir> --task-file <file> \
  [--model <id>] [--provider <id>] [--effort <name>] [--timeout <seconds>] \
  [--log-dir <parent>] [--dsh-bin <path>] [--settings-file <path>] \
  [--workspace-socket <path>] [--workspace-timeout <seconds>] [--no-workspace] \
  [--inquiry-socket <path> --inquiry-token <token> --inquiry-results <path>]
```

The `--inquiry-*` triple is supplied by the owning Buddy service, never by hand: it
mounts the per-run inquiry bridge (token-authenticated private socket, plus an
append-only answer journal) so `buddy inquire` can observe this run and ask its live
agent a correlated question. Omitting it only means this run has no inquiry channel.

- `<skill-dir>` is wherever this skill is installed (for example
  `$CODEX_HOME/skills/deepseek-delegate`); run `uv sync` there once. `--help` needs no dsh.
- stdout is exactly one JSON object: status, exit code, elapsed time, requested route and effort,
  inputDelivery, log paths, and `finalText` (at most 6000 characters) with a truncation flag.
  It exits 0 only when the task is `ok` and any requested grouping is verified; nonzero, timeout,
  cancellation, spawn failure or unverified grouping exits 1; usage and configuration errors exit 2.
- Retry only with new evidence and a narrower or corrected packet; normally stop after one or two failed attempts. Do not repeat identical requests.
- Long output stays in the private per-run logs. Read the log file when needed; do not paste whole
  logs or reasoning back into the conversation.
- Each run copies the settings document to a private temporary file, overrides only
  `agent-default-model`, and cleans it up. Original settings and credentials are untouched.

## Workspace grouping

Grouping is enabled by default. Install the bundled host plugin once:

```sh
node <skill-dir>/scripts/install-workspace-bridge.mjs
```

This adds a backed-up entry to the existing `web` profile's `cordis.patch.yml`.
Long-lived profiles hot-reload their user patch; otherwise start that profile normally.
The plugin calls the official `ctx.workspaceRegistry.create(cwd)` and
`workspace.attachSession(sessionId)` APIs inside the owning host. The CLI connects
through an owner-private Unix socket at `$DSH_HOME/deepseek-delegate/workspace.sock`
(default home `~/.dsh`). No Web URL, token, cookie, or HTTP endpoint is used.
Use `--workspace-socket` or `DSH_WORKSPACE_SOCKET` only for a custom host endpoint.
The host must mount workspace and persistence and share the headless session storage.
Do not mount a second workspace writer over storage already owned by another host.

The bridge is checked before a model run. A read-only observer captures the exact root
session; after headless fully stops, the host validates the persisted root header and cwd,
attaches it through the official API, and verifies membership. No Agent is activated for
binding. A binding failure preserves task output and makes the CLI exit nonzero.
Use `--attach-session <id> --cwd <dir>` to retry only binding of a completed session.
For intentionally standalone work, explicitly use `--no-workspace`; never silently downgrade.
Socket restarts use the same path without credential updates. An unclean host exit can leave
a stale socket; verify its owner process has stopped before removing that socket only.

Official contracts: [workspace](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/workspace.md),
[storage limits](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/storage/storage-json/README.md).
The socket bridge is this skill's adapter, not an upstream DSH CLI command.

## Scope and isolation

- `--cwd` is a working directory, not a sandbox: state the allowed files and limits in the packet.
- Service-managed runs default to `workspace: true` and need the installed, running host bridge;
  `workspace: false` (or the runner's `--no-workspace`) opts out, and only Buddy-owned runs are
  controlled. Independent user dsh processes are separate.
- CLI commands run with this task's shell permissions — Buddy never grants extra privilege,
  and the local Buddy service is a same-user process. The removed MCP server used to carry
  its own plugin-scoped tool approval; that is gone, so the operating system sandbox of the
  shell that runs `buddy` is the only boundary.
- Delegation inherits the scope the user granted this session. When the user authorized publishing,
  pushing, or sending messages, pass that authorization explicitly in the packet; do not invent
  blanket bans the user never asked for.
- Do not let Codex and dsh edit the same files at the same time; work on something else while the
  run is in flight.

## Verify independently — never skip

- Exit 0 only means the dsh agent finished. It is not proof that the task is correct.
- Inspect the real diff, added/removed files, and command output; run the relevant checks (tests,
  build, lint, targeted reproduction) yourself.
- Check every acceptance criterion and state the evidence. Without evidence, the task is not done.
- On resumption, deduplicate by the persisted run ID and inspect the existing run;
  never start it again because a notification or result is delayed. After handling
  and verification, record acceptance with `handoff.mjs --run-dir <dir> --accept`
  and close the App heartbeat when applicable. Queue acceptance is not task acceptance;
  with the plugin service, `buddy acknowledge '{"runId":"…","note":"…"}'` records review of
  the real outcome (including a reviewed failure) once the result is persisted and shutdown
  is confirmed, and never turns a failed execution into a success.
