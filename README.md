# hey-my-buddy


[中文文档](README.zh-CN.md)

[Architecture decision](docs/decisions/001-python-transactional-blackboard.md) ·
[0.4.0 acceptance evidence](docs/acceptance/python-blackboard-0.4.0.md)

A Codex plugin, standalone skill and CLI that hand **clear, bounded** tasks to a local
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`) run, then returns one
compact JSON result. Codex keeps framing, route choice, and final acceptance; dsh does the bulk
work. A **Python transactional blackboard service over C-Two** owns every task record; independent
Python workers claim queued tasks and execute them through adapters (`dsh` by default, plus
`command` and caller-owned `external`). The same service and durable tasks are available from the
CLI, and the original standalone Node runner still ships for direct, service-less use — it is also
the runner the `dsh` adapter spawns.

## Codex app plugin

The repository includes a `hey-my-buddy` plugin: a short skill plus the uv-managed Buddy
CLI. Install dependencies with `uv sync --project deepseek-delegate --python 3.12`, then
install the plugin in Codex and use a new task. **There is no MCP server and no MCP
registration**: the plugin is skills + CLI, and every operation goes through the
`buddy` command talking to the transactional Python blackboard over C-Two.

```sh
BUDDY=deepseek-delegate/scripts/launch-buddy.sh     # or: node deepseek-delegate/scripts/buddy.mjs
"$BUDDY" start '{"requestId":"fix-123","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
"$BUDDY" await '{"runId":"<runId>","waitSeconds":28800}'
```

The default workflow starts once, captures the `runId`, then awaits that same run inside
the current turn: `buddy start` returns immediately and `buddy await` stays connected until
the run finishes, printing the final envelope with the runId, execution status, shutdown
confirmation, runner result and log paths. Knowing the runId while waiting is what lets
`buddy inquire` observe or ask that same run. `buddy run` remains an optional one-call
convenience: it starts (or recovers) the durable dsh job and stays connected, with
`waitSeconds` defaulting to `timeoutSeconds + 60 s` shutdown grace capped at the 24 h CLI
maximum. The turn stays active while it waits; there is no heartbeat, cron or model
polling. `buddy result` reads an existing run again, and `buddy dashboard` returns a
private read-only local task panel with no model calls for refresh. The full surface is
`run`, `await`, `start`, `submit`, `status`, `wait`, `watch`, `events`, `result`, `list`,
`cancel`, `retry`, `acknowledge`, `inquire`, `message`, `messages`, `message-get`,
`message-update`, `artifacts`, `workers`, `worker-register`, `worker-claim`,
`worker-reconcile`, `worker-renew`, `worker-progress`, `worker-result`,
`worker-release`, `worker-start`, `worker-stop`, `wait-capacity`, `capabilities`,
`adapters`, `runtime`, `dashboard`, `legacy-import`, `restart`, `health`, `stop`.

A submitted task is admitted as `queued` and starts when a worker claims it;
`queueReason` says why it is waiting (`awaiting-worker`, `capacity`, `cwd-overlap`,
`exclusive-resource`). This is deliberate: capacity and resource conflicts queue the
work instead of returning BUSY, and the task keeps its place until a matching worker is
free. The service defaults to one concurrent attempt (`BUDDY_MAX_CONCURRENT`, 1–8) and
only controls its own worker processes. Existing dsh host/session lifetimes and original
settings stay separately owned. Workspace grouping defaults on (`workspace: true`) and
still uses the existing host bridge described below; `workspace: false` opts out. A
`cwd` is a working directory, not a sandbox, and independent user dsh processes are not
coordinated by Buddy.

The wait window is owned by the CLI: it defaults to `timeoutSeconds + 60 s` shutdown
grace, capped at the 24 h CLI maximum (86400 s), so one command normally covers the whole
job; pass `waitSeconds` explicitly for a shorter, recoverable wait. There is no host tool
timeout and no `BUDDY_TOOL_WAIT_BUDGET_SECONDS` any more. The execution deadline is
separate and unchanged: `timeoutSeconds` is a Buddy **execution** timeout on the whole
spawned dsh process group (default 1800 s, integer 10–86400), armed from spawn and covering
every model and tool step — not a model-turn or session limit. Pass it explicitly for long
work, for example `"timeoutSeconds": 28800` for 8 hours. A runner deadline longer than one
wait window stays allowed: the job keeps running, the envelope reports
`waitCoversRunnerDeadline: false`, and `buddy await` waits on the same durable runId.

Killing the waiting CLI process (Ctrl-C, closed terminal, `waitSeconds` expiry) cancels
only the wait: the durable task keeps running while a worker owns it and is recovered by
requestId/runId. The task can still end on its own execution deadline, and `buddy cancel`
(or `buddy stop`) ends Buddy-owned work through durable cancel intent that the owning
worker observes; those endings are terminal and are never replayed automatically. After a
daemon restart, in-flight attempts are marked `uncertain` with their resource claims
retained: the independent worker keeps its child process and deadline, reattaches by
attempt identity and capability, and `buddy restart` never cancels work. The worker
resubmits a persisted completion receipt until acknowledged without executing the
task again. A new execution requires an explicit `buddy retry` that creates a new attempt.

A finished runner is not acceptance. `buddy acknowledge` records that you reviewed the real
outcome — including a reviewed failure — once the persisted result exists and shutdown is
confirmed; it never turns a failed execution into a success, and repeating it with a
different note or `verdict` is a `CONFLICT`. Exit 0 is not job success
either: read `status`, `outcome`, `resultDelivered` and `shutdownConfirmed`, then inspect
the actual artifacts.

`buddy start` returns a `runId` immediately; it is the first half of the default foreground
workflow and becomes the background route for explicit background work, where the Buddy
skill registers an official App heartbeat whose prompt calls this same CLI to check the
existing run, verify its artifacts and acknowledge it. That heartbeat is a periodic
background follow-up, not an immediate completion push; Buddy does not solve native
post-turn App wakeup. See
[service contracts and recovery](deepseek-delegate/references/plugin-service.md).

### Migrating from the removed MCP path

Nothing needs to be installed for MCP any more. If you previously registered the plugin's
MCP server, remove that leftover entry yourself (Buddy never edits your config):

- `[mcp_servers.buddy_ctwo]` (and any `plugins.*.mcp_servers.*` Buddy entry, including its
  `tool_timeout_sec` / `approval_mode` fields) in `~/.codex/config.toml`,
- any plugin-scoped Buddy tool approval remembered by the App,
- the then-unused `mcp.json` companion in an older plugin cache copy.

Keep every unrelated Codex/App tool and setting, especially the official App heartbeat
automation, which is not part of Buddy.

### Upgrading the plugin safely

Quiesce and **stop the owned service before the plugin cache version is replaced** —
including docs-only cachebuster updates:

```sh
deepseek-delegate/scripts/launch-buddy.sh stop    # or the previous version's launcher
# then install/refresh the plugin version
```

`buddy stop` cancels queued tasks, writes a durable cancel request for each active
attempt, drains for a bounded interval and reports anything unresolved, so it is the
supported pre-upgrade action. `buddy restart` detaches the daemon without cancelling
work and independent workers survive it, but a live daemon still keeps using the code
path it loaded at start: replacing the cache directory under it can leave the service
running from a deleted path (and its next `dsh` task would fail with an unavailable
runner). For an install that survives cache replacement, materialize the
content-addressed stable runtime outside the plugin cache (see
[runtime packaging](deepseek-delegate/references/plugin-service.md#packaging-migration-and-unrelated-app-tools));
a cold start then launches the service from it. Details: [service contracts and recovery](deepseek-delegate/references/plugin-service.md#cli-wait-window-and-upgrade-ordering).

## Service CLI

The same service and the same durable tasks are available from the CLI:

```sh
uv run --frozen --project deepseek-delegate buddy health
# Default: start once, keep the runId, then await that same durable task. await has
# its own bounded window (waitSeconds 1..86400, default 86400) and never starts work.
uv run --frozen --project deepseek-delegate buddy start '{"requestId":"x","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
uv run --frozen --project deepseek-delegate buddy await '{"runId":"<runId>","waitSeconds":28800}'
# One-call convenience: blocking run whose window = timeoutSeconds + 60 s grace, cap 24 h.
uv run --frozen --project deepseek-delegate buddy run '{"requestId":"x","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
uv run --frozen --project deepseek-delegate buddy status '{"runId":"<runId>"}'
uv run --frozen --project deepseek-delegate buddy result '{"runId":"<runId>"}'
uv run --frozen --project deepseek-delegate buddy inquire '{"runId":"<runId>"}'
uv run --frozen --project deepseek-delegate buddy events '{"after":0}'
uv run --frozen --project deepseek-delegate buddy workers
uv run --frozen --project deepseek-delegate buddy cancel '{"runId":"<runId>"}'
uv run --frozen --project deepseek-delegate buddy acknowledge '{"runId":"<runId>","note":"reviewed the diff and ran the tests","verdict":"accepted"}'
uv run --frozen --project deepseek-delegate buddy restart
uv run --frozen --project deepseek-delegate buddy stop
# Explicit board submission for another adapter (one argv process, never a shell):
uv run --frozen --project deepseek-delegate buddy submit '{"requestId":"y","task":"...","cwd":"/abs/path","adapter":"command","argv":["/bin/echo","hi"]}'
# Offline, transactional import of the removed Node records (dry run by default):
uv run --frozen --project deepseek-delegate buddy legacy-import '{"sourceDir":"/old/state","dryRun":true}'
```

Only `run` and `await` have a long wait contract: `run` blocks up to its wait window and
`await` waits up to its own `waitSeconds`. The other subcommands have bounded waits or
none: `wait` and `watch` return after at most 30 s, `inquire` returns immediately unless
`waitMs` asks for a bounded answer window (max 30 s), and `stop` returns after the
service has finished draining. A `wait` or `watch` never cold-starts a service. `await`
waits on an existing `requestId` or `runId` and never starts work.
The CLI prints one JSON object (pretty-printed), and exit 0 only means the call returned:
read `status`/`outcome`/`resultDelivered`/`shutdownConfirmed` and verify the real
artifacts. A low-level failure is printed as an `error` object with exit 1. Every recovery
envelope names real commands (`buddy status|await|result|cancel`) and the existing run ID.
See [service contracts and recovery](deepseek-delegate/references/plugin-service.md) for
the wait, worker, adapter, inquiry and recovery contract.

`buddy inquire` without a question is read-only: it reports the observed execution state,
an explicitly estimated deadline and recent tool activity — not a percentage or a
guaranteed ETA — and names every field it could not observe. To ask the run's own live
agent one correlated question, pass `inquiryId` and `question` together; to read that
same question back, repeat both the same text and the same id (an id alone is not a
valid lookup). The same id with different text is a `CONFLICT`, in both active and
terminal runs, and `waitMs` (max 30000) only bounds this call's wait for an answer — it
never extends or cancels the task. Questions and answers are each bounded at 4000 UTF-8
bytes, at most 32 inquiries are retained per task, and an adapter without an inquiry
capability answers with an explicit reason instead of faking progress. No automatic
timer- or model-triggered inquiry exists; an agent whose task allows shell commands can
still call the CLI explicitly when needed.

## What it does

The plugin service executes tasks through adapters. The `dsh` adapter (default) spawns
the existing Node runner for each task; the runner:

- Runs one bounded task through `dsh --profile headless` with a real per-run model/effort override.
- Copies your settings document to a private temporary JSON file, replaces only the
  `agent-default-model` route/effort, and points dsh at that copy through a temporary `--patch`
  overlay. Your original settings and credentials are never modified.
- Keeps stdout/stderr in a unique owner-private log directory and prints exactly one JSON object on
  stdout.
- Never uses a shell: the launcher and the task text are passed as an argument vector, so paths with
  spaces, leading dashes, newlines, and shell metacharacters stay literal.
- Only starts the one dsh run you asked for: it never retries, downgrades, or substitutes a route
  silently.

The `command` adapter runs exactly one explicit `argv` process with no implicit shell. The
`external` adapter has no local process at all: the caller's own agent claims the task
through the public C-Two contract (`deepseek-delegate/python/buddy/client.py`) and reports
the result itself. `buddy capabilities` reports each adapter with `executedBy` and the
service's honest limitations (no steer/resume, no native App wakeup, no PostgreSQL/HA,
no remote tenancy).

## Requirements

- uv and Python 3.12 or newer below 3.15 (`requires-python = ">=3.12,<3.15"`; the launcher selects Python 3.12 through uv).

- macOS or Linux (POSIX). Windows is not implemented or tested; the CLI exits with a clear error.
- Node.js 20 or newer, required by the `dsh` adapter and the dsh runtime it drives; the
  `command` and `external` adapters need no Node. Node files here use built-in modules only, so
  there is no npm dependency installation.
- A working `dsh` installation with credentials you configured yourself. See
  <https://github.com/deepseek-ai/deepseek-harness> for setup. This project does not install dsh,
  configure provider credentials, or change global model settings. The explicit bridge installer
  adds one plugin entry to the selected dsh profile with a private backup.
- Codex with skills support.

## Install

```sh
git clone https://github.com/Dsssyc/hey-my-buddy.git
cd hey-my-buddy
uv sync --project deepseek-delegate --python 3.12
```

The skill is self-contained in `deepseek-delegate/`. Copying just that directory somewhere and
running `uv sync` inside it is enough; there is no root package, nothing is published to npm, and the
third-party dependencies are uv-managed and locked: PyPI `c-two==0.5.1` and PyYAML. Node files use built-in modules only, and the `mcp` Python SDK is no longer a dependency of anything here. To run independently of the Codex plugin cache, materialize the content-addressed stable runtime outside it:

```sh
uv run --frozen --project deepseek-delegate python -c "from buddy import runtime; runtime.materialize()"
uv run --frozen --project deepseek-delegate buddy runtime
```

`materialize()` copies the complete runtime assets into the final content-addressed directory
first, runs `uv sync --frozen` there, and writes `READY.json` last; credentials, user data,
tests and any existing virtual environment are never copied. Once a READY runtime exists, a
cold start launches the service and its worker supervisor from that runtime's own interpreter,
and `buddy runtime` / `buddy health` report whether the process actually runs from it. See
[runtime packaging](deepseek-delegate/references/plugin-service.md#packaging-migration-and-unrelated-app-tools).

### Add it to Codex without overwriting anything

`CODEX_HOME` is read if you set it; otherwise `~/.codex` is used. The snippet never sets or exports
`CODEX_HOME`, and it leaves an existing skill directory alone.

```sh
CODEX_SKILLS="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$CODEX_SKILLS"
if [ -e "$CODEX_SKILLS/deepseek-delegate" ] || [ -L "$CODEX_SKILLS/deepseek-delegate" ]; then
  echo "already exists, left untouched: $CODEX_SKILLS/deepseek-delegate" >&2
else
  ln -s "$PWD/deepseek-delegate" "$CODEX_SKILLS/deepseek-delegate"
fi
```

For a copy instead of a symlink, replace the `ln -s` line with
`cp -R "$PWD/deepseek-delegate" "$CODEX_SKILLS/deepseek-delegate"` and run `uv sync` inside the copy.
If an older version is already installed, remove or rename it yourself first; nothing here
overwrites it.

## Connect workspace grouping

Grouping uses the official in-process workspace API through a bundled local host plugin.
Install it once into your existing `web` profile:

```sh
node deepseek-delegate/scripts/install-workspace-bridge.mjs
```

The installer backs up and appends to that profile's `cordis.patch.yml`; it preserves existing
settings. Long-lived profiles hot-reload the user patch; otherwise start `dsh web` normally.
The host plugin serves an owner-private Unix socket at
`$DSH_HOME/deepseek-delegate/workspace.sock` (default home `~/.dsh`). There is no Web launch URL,
browser token, cookie exchange, or HTTP dependency. The same socket path works after clean restarts.
An unclean exit may leave a stale socket: verify the owning process has stopped before removing
that socket. The plugin never unlinks an occupied endpoint automatically.

The `dsh` adapter's runner checks the bridge before starting a model task. After the headless process group stops,
it sends the captured root session identity to the host. The plugin validates the persisted
header and canonical cwd, calls `ctx.workspaceRegistry.create(cwd)` and
`workspace.attachSession(sessionId)`, then verifies membership. It never creates or activates an
Agent for grouping. Task and grouping outcomes remain separately visible; attachment can be retried.

This socket transport is our adapter over the [official workspace API](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/workspace.md).
The host must mount workspace/persistence and share the headless session storage. Keeping the
workspace writes inside its owning host avoids the [JSON backend's cross-process limitation](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/storage/storage-json/README.md).
The installer defaults to `--profile web`; another existing long-lived profile with these services
can be selected. Do not start another workspace writer over storage already owned by a host.

Use `--no-workspace` for standalone execution. Grouped runs require the host plugin to be running;
there is no silent fallback. Service runs default to `workspace: true` and need the same bridge;
`workspace: false` is the service equivalent of the runner's `--no-workspace`. Remove the obsolete private `~/.config/deepseek-delegate/web-url`
file when migrating; the runner no longer reads it. Old `--dsh-web-url*` and `--web-timeout` flags
are rejected. Model API credentials remain unchanged.

### Recover grouping without repeating a task

Use the `workspace.sessionId` returned by a failed binding (or a known completed ordinary session):

```sh
node "$SKILL_DIR/scripts/run.mjs" --cwd /path/to/project --attach-session SESSION_ID
```

This verifies that the session exists before adoption. It runs no model and does not change the
shared default model. It does not scan or bulk-reassign history. The stored session cwd must equal
the canonical workspace path; mismatched/removed historical directories require separate handling.

## Standalone runner quick start

`scripts/run.mjs` is the low-level runner the `dsh` adapter spawns for every dsh task. It is
still shipped, tested and usable on its own when you want the raw runner contract with
no durable blackboard records or inquiry channel (a standalone invocation reports
`inquiry.enabled: false`). The disposable example
below opts out of sidebar grouping. For real project tasks, install the host plugin
above and omit `--no-workspace`. Write a task packet, then run the CLI:

```sh
SKILL_DIR="$PWD/deepseek-delegate"
DELEGATE_DEMO_DIR="$(mktemp -d)"
printf '%s\n' '{"numbers":[3,7,11]}' > "$DELEGATE_DEMO_DIR/input.json"
cat > "$DELEGATE_DEMO_DIR/task.md" <<'EOF'
Read input.json and write only result.json containing the sum and count of numbers.
Do not modify input.json or use the network. Verify result.json by reading it.
Acceptance: result.json equals {"sum":21,"count":3}.
EOF

node "$SKILL_DIR/scripts/run.mjs" --no-workspace \
  --cwd "$DELEGATE_DEMO_DIR" \
  --task-file "$DELEGATE_DEMO_DIR/task.md" \
  --timeout 1800
cat "$DELEGATE_DEMO_DIR/result.json"
```

`--cwd` is required; `--task-file` is required for runs and omitted in attach mode. `node "$SKILL_DIR/scripts/run.mjs" --help` lists every
option and works without dsh or credentials.

## Standalone runner result, exit codes, and logs

stdout carries exactly one JSON object, for example:

```json
{"status":"ok","mode":"run","exitCode":0,"signal":null,"error":null,"elapsedSeconds":42.1,"timeoutSeconds":1800,
 "requested":{"provider":"deepseek-official","model":"deepseek-flash","reasoningEffort":"max"},
 "cwd":"/path/to/project","taskFile":"/path/to/task.md","dshBin":"/path/to/dsh",
 "inputDelivery":"inline",
 "logPaths":{"stdout":"/tmp/deepseek-delegate-logs-XXXX/stdout.log","stderr":"/tmp/deepseek-delegate-logs-XXXX/stderr.log","capture":"/tmp/deepseek-delegate-logs-XXXX/capture.json"},
 "inquiry":{"enabled":false,"socketPath":null,"resultsPath":null,"errorPath":null,"error":null},
 "finalText":"...","finalTextTruncated":false,
 "workspace":{"enabled":true,"bound":true,"id":"workspace-example","path":"/path/to/project","sessionId":"session-example"},
 "processState":{"shutdownConfirmed":true},
 "note":"exit 0 only means the dsh agent finished, not that the task is correct: inspect the real diff/artifacts and run the relevant checks yourself."}
```

- `status`: `ok`, `nonzero`, `timeout`, `cancelled`, or `spawn-error`.
- Wrapper exit code: `0` only when the task is `ok` and any requested grouping is verified; `1`
  for a task, termination, or grouping failure; `2` for usage and
  configuration errors (which are reported on stderr without a JSON object).
- `workspace` reports `enabled`, `bound`, `id`, `path`, `sessionId`, and any binding `error`.
  Task `status`/`exitCode` and log paths survive grouping failure; check the process exit and
  `workspace.bound`, not task status alone. `mode` is `run` or `attach` (`attach-error` on failure).
- `requested` is the route and effort actually written into the settings copy.
- `finalText` is at most 6000 characters of the head of the dsh stdout log,
  with `finalTextTruncated` telling you whether more existed. stderr and reasoning are never copied
  into the JSON.
- `inquiry` says whether the owning Buddy service mounted this run's private inquiry
  bridge (`enabled`, its paths and any startup `error`). A bridge that fails to start
  never fails the run; a standalone `run.mjs` invocation without the service reports
  `enabled: false`.
- `processState.shutdownConfirmed` is true only when the owned process group was observed
  stopped; it is required before `buddy acknowledge` and before workspace grouping.
- Logs go to a unique owner-private directory (directory mode `0700`, file mode `0600`): under
  `--log-dir` when given, otherwise under the OS temp directory. Pre-existing files are never
  truncated or reused.

## Standalone runner options

| Option | Default | Meaning |
| --- | --- | --- |
| `--cwd <dir>` | required | working directory dsh runs in |
| `--task-file <file>` | required for runs | file holding the task text |
| `--model <id>` | see precedence | model id for this run |
| `--provider <id>` | see precedence | provider id for this run |
| `--effort <name>` | `max` | reasoning effort for this run |
| `--timeout <seconds>` | `1800` | whole-process-group execution limit, integer 10–86400; workspace socket requests use `--workspace-timeout` |
| `--log-dir <dir>` | OS temp dir | parent directory for this run's private log directory |
| `--dsh-bin <path>` | see precedence | dsh launcher to execute |
| `--settings-file <path>` | see precedence | settings document to copy and override |
| `--no-workspace` | disabled | explicitly skip workspace grouping |
| `--attach-session <id>` | | group an existing completed session; no task file/model run |
| `--workspace-socket <path>` | see precedence | private socket served by the owning host plugin |
| `--workspace-timeout <seconds>` | `15` | per-request bound, integer 1–120 |
| `--inquiry-socket <path>`, `--inquiry-token <token>`, `--inquiry-results <path>` | | private per-run inquiry channel, supplied together by the owning Buddy service; never by hand |
| `-h`, `--help` | | print help and exit (needs no dsh) |

## Precedence

| Item | Order (first match wins) |
| --- | --- |
| dsh launcher | `--dsh-bin` → `DSH_BIN` → `dsh` on `PATH` → `~/.local/bin/dsh` |
| settings document | `--settings-file` → `DSH_SETTINGS_FILE` → `$DSH_HOME/settings.yaml` → `~/.dsh/settings.yaml` |
| model | `--model` → `DSH_DELEGATE_MODEL` → settings `agent-default-model.model` → `deepseek-flash` |
| provider | `--provider` → `DSH_DELEGATE_PROVIDER` → settings `agent-default-model.provider` → `deepseek-official` |
| Workspace socket | `--workspace-socket` → `DSH_WORKSPACE_SOCKET` → `$DSH_HOME/deepseek-delegate/workspace.sock` (home defaults to `~/.dsh`) |
| effort | `--effort` → `DSH_DELEGATE_EFFORT` → `max` (never inherited from settings) |

Notes:

- An explicitly named settings file (`--settings-file` or `DSH_SETTINGS_FILE`) must exist; a missing
  default file is treated as an empty mapping.
- Blank model/provider/effort values are rejected instead of silently clearing a default. Effort is
  never lowered implicitly.
- The run settings copy keeps every other section structurally identical, including `agent-presets`
  and any other section. Inside `agent-default-model`, only `provider`, `model`, and
  `reasoningEffort` are written; other keys there (for example an extension field) are
  carried through.
- Context window and output limits are owned by your dsh model catalog and presets. This wrapper
  never sets them and never shrinks context to a fixed number. A 1M-token context window is a
  model-specific configuration example, not a cross-provider promise.

## Minimal task packet

A good packet lets dsh finish without guessing:

- Goal and why; expected inputs and outputs.
- The working directory and the commands needed to check the work.
- Allowed files/areas, plus explicit non-goals.
- Acceptance criteria: the observable result and the checks to run.
- Key references: file paths, functions, known facts, pitfalls.
- As much relevant context as the task needs. Context capacity is configured per model/provider, so
  do not artificially shrink the packet. Task content over 32,000 bytes is passed to dsh as a file
  reference, and that file must remain in place until the run finishes.

## After the run: review the artifacts yourself

A finished dsh process is **not** proof that the task is correct. Codex must inspect the real diff,
added/removed files, and command output, then run the relevant checks (tests, build, lint, targeted
reproduction) and match them against the acceptance criteria. The `note` field in the JSON says the
same thing on purpose.

## Platform and support bounds

- **POSIX only.** macOS and Linux are the supported platforms; there is no Windows code path.
- `--cwd` is a working directory, not a sandbox. State the allowed scope in the task packet, or give
  dsh its own workspace (for example a git worktree) when you need stronger isolation.
- Service runs default to `workspace: true`, which requires the workspace host bridge to be installed
  and running; there is no silent downgrade to an ungrouped run. Use `workspace: false` (or the
  standalone runner's `--no-workspace`) to opt out.
- Only Buddy-owned runs are controlled. Independent user dsh processes and sessions are separate and
  are not coordinated, grouped or cancelled by Buddy.
- All authoritative state is local SQLite under `BUDDY_STATE_DIR` (default
  `~/.local/share/hey-my-buddy`). PostgreSQL/HA, remote multi-tenant operation and native App
  wakeup are not implemented and are not claimed.
- Capacity and cwd/exclusive-resource conflicts queue work with a `queueReason` instead of
  rejecting it; `BUDDY_MAX_CONCURRENT` (1–8, default 1) bounds active attempts, and one worker
  runs one attempt at a time.
- CLI commands run with the permissions of the Codex task's shell; Buddy never grants extra
  privilege and its service is a same-user local process. The removed MCP server's plugin-scoped
  `approval_mode` is gone with it, so the shell's own sandbox is the only boundary.
- The settings override assumes the booted profile mounts the dsh settings provider under the entry
  id `settings`, as the shipped profiles do. Custom plugins or profiles that read settings another
  way, or keep configuration in profile patch files outside `settings.yaml`, are not covered by the
  copy and stay in effect.
- Model and provider IDs are not validated against your provider's catalog; the wrapper does not
  downgrade, substitute, or retry them. Choose an ID your provider supports.
- On timeout or cancellation only the process group Buddy started is stopped, and the outcome is
  reported as `timeout`/`cancelled`/`failed` — never `ok`, even if a child exits 0 afterwards.
- Nothing here publishes, pushes, or messages anyone; that scope comes from you, and the skill
  inherits whatever you authorized for the session.

## Tests

```sh
uv run --frozen --project deepseek-delegate python -m buddy.checks
```

The suite drives the real CLI against a mock `dsh` executable in temporary directories. It makes no
model calls, does not read your real settings, does not use your `HOME` or `CODEX_HOME`, and covers
launcher resolution, settings precedence and preservation, literal task delivery, the 32 KB
reference switch, the 6000-character stdout cap, unique private logs, spawn failures, timeouts, and
cancellation cleanup. Additional tests drive the real daemon in private state directories and cover
the transactional blackboard (rollback and SQLite integrity, duplicate submission and claim replay,
queued capacity admission, overlapping cwd and exclusive-resource reservation, the crash windows,
a real daemon restart that keeps the same attempt/worker/deadline/result, stale-generation and
capability rejection, lease uncertainty, `WAIT_OVERLOAD` under concurrent waiters, and the
cancellation race), the blocking delegation path (wait-timeout and disconnect recovery), the
inquiry state machine and its bounds, the `dsh`/`command`/`external` adapters including an external
worker over the public client, runtime materialization that never copies credentials or
environments, and the offline legacy import (dry-run, idempotency, rollback and fingerprint
validation). No production host is contacted.
An opt-in no-model check uses your installed official packages with isolated temporary storage:

```sh
node deepseek-delegate/tests/manual/real-workspace.mjs --dsh-lib /path/to/node_modules/@deepseek-ai
```

It verifies binding, rejection of unknown sessions, actual Cordis plugin disposal, and reconnection
after restarting at the same socket path. It does not touch your real sessions or workspaces.

Nothing in this repository is published to npm.

## License

[MIT](LICENSE). The standalone skill directory includes its own copy of the license.
