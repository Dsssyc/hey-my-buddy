# Standalone Node runner

`scripts/run.mjs` is the low-level runner that the `dsh` adapter spawns for every dsh
task. It is still shipped and tested for direct use when you want the raw runner
contract, but it has **no durable blackboard records, no `requestId` recovery and no
inquiry channel** (a standalone invocation reports `inquiry.enabled: false`). Use
[usage.md](usage.md) and the `buddy` CLI for durable delegated work; use this runner for
a single foreground process whose whole result is the JSON it prints.

For resuming an owner after a delegation that outlives the turn, read
[handoff.md](handoff.md); it documents the standalone `scripts/handoff.mjs` supervisor,
which persists its own run directory and notification files independently of the
blackboard.

## Quick start

The example opts out of sidebar grouping so it works without the workspace bridge. For
real project tasks, install the host bridge ([operations.md](operations.md#workspace-bridge))
and omit `--no-workspace`.

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

`--cwd` is required, and `--task-file` is required for a run (it is omitted in attach
mode). `node "$SKILL_DIR/scripts/run.mjs" --help` lists every option and needs neither
dsh nor credentials.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--cwd <dir>` | required | working directory dsh runs in |
| `--task-file <file>` | required for runs | file holding the task text |
| `--model <id>` | see precedence | model id for this run |
| `--provider <id>` | see precedence | provider id for this run |
| `--effort <name>` | `max` | reasoning effort for this run |
| `--timeout <seconds>` | `1800` | whole-process-group execution limit, integer 10–86400 |
| `--log-dir <dir>` | OS temp dir | parent directory for this run's private log directory |
| `--dsh-bin <path>` | see precedence | dsh launcher to execute |
| `--settings-file <path>` | see precedence | settings document to copy and override |
| `--no-workspace` | disabled | explicitly skip workspace grouping |
| `--attach-session <id>` | | group an existing completed session; no task file, no model run |
| `--workspace-socket <path>` | see precedence | private socket served by the owning host plugin |
| `--workspace-timeout <seconds>` | `15` | per-request bound, integer 1–120 |
| `--inquiry-socket <path>`, `--inquiry-token <token>`, `--inquiry-results <path>` | | private per-run inquiry channel, supplied together by the owning Buddy service; never by hand |
| `-h`, `--help` | | print help and exit (needs no dsh) |

`--no-workspace` conflicts with `--workspace-socket` and `--attach-session`. The
`--inquiry-*` triple is never passed by hand: it mounts the per-run bridge that lets
`buddy inquire` observe the run or ask its live agent a correlated question.

## Precedence

| Item | Order (first match wins) |
| --- | --- |
| dsh launcher | `--dsh-bin` → `DSH_BIN` → `dsh` on `PATH` → `~/.local/bin/dsh` |
| settings document | `--settings-file` → `DSH_SETTINGS_FILE` → `$DSH_HOME/settings.yaml` → `~/.dsh/settings.yaml` |
| model | `--model` → `DSH_DELEGATE_MODEL` → settings `agent-default-model.model` → `deepseek-flash` |
| provider | `--provider` → `DSH_DELEGATE_PROVIDER` → settings `agent-default-model.provider` → `deepseek-official` |
| effort | `--effort` → `DSH_DELEGATE_EFFORT` → `max` (never inherited from settings) |
| Workspace socket | `--workspace-socket` → `DSH_WORKSPACE_SOCKET` → `$DSH_HOME/deepseek-delegate/workspace.sock` (home defaults to `~/.dsh`) |

Notes:

- An explicitly named settings file (`--settings-file` or `DSH_SETTINGS_FILE`) must
  exist; a missing default file is treated as an empty mapping.
- Blank model/provider/effort values are rejected instead of silently clearing a
  default, and effort is never lowered implicitly.
- The run settings copy keeps every other section structurally identical, including
  `agent-presets` and any other section. Inside `agent-default-model`, only `provider`,
  `model` and `reasoningEffort` are written. The copy is passed to dsh through a
  temporary `--patch` overlay, and the original settings and credentials are never
  modified.
- Context window and output limits belong to your dsh model catalog and presets; this
  runner never sets them. A 1M-token context window is a model-specific configuration
  example, not a cross-provider promise.
- Task text over 32,000 bytes is delivered to dsh as a file reference; that file must
  stay in place until the run finishes. `inputDelivery` reports `inline` or
  `file-reference`.

## Result, exit codes and logs

stdout carries exactly one JSON object:

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

- `status`: `ok`, `nonzero`, `timeout`, `cancelled` or `spawn-error`; attach mode reports
  `ok` or `attach-error`.
- Wrapper exit code: `0` only when the task is `ok` and any requested grouping is
  verified; `1` for a task, termination or grouping failure; `2` for usage and
  configuration errors, which are reported on stderr without a JSON object; a signal
  received before the child spawns exits `130`.
- `requested` is the route and effort actually written into the settings copy.
- `finalText` is at most 6000 characters of the head of the dsh stdout log, with
  `finalTextTruncated` (byte-based) telling you whether more existed. stderr and
  reasoning are never copied into the JSON.
- `workspace` reports `enabled`, `bound`, `id`, `path`, `sessionId` and any binding
  `error`. Task `status`/`exitCode` and log paths survive a grouping failure; check the
  process exit and `workspace.bound`, not task status alone.
- `inquiry` says whether the owning Buddy service mounted this run's private bridge
  (`enabled`, its paths and any startup `error`). A bridge that fails to start never
  fails the run.
- `processState.shutdownConfirmed` is true only when the owned process group was
  observed stopped; it is required before workspace grouping and before
  `buddy acknowledge`.
- Logs go to a unique owner-private directory (directory mode `0700`, file mode `0600`):
  under `--log-dir` when given, otherwise under the OS temp directory. Pre-existing files
  are never truncated or reused.

## Attach mode

`--attach-session <id>` groups an already completed session instead of running a model.
It verifies that the session exists before adoption, runs no model, changes no shared
default model, and does not scan or bulk-reassign history. It emits a fixed `mode:
"attach"` payload with no logs or final text. The stored session cwd must equal the
canonical workspace path; use it to retry only the binding of a completed run.
