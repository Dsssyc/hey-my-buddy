# hey-my-buddy

[中文文档](README.zh-CN.md)

A small Codex skill plus a CLI that hands **clear, bounded** tasks to a local
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`) run, then returns one
compact JSON result. Codex keeps framing, route choice, and final acceptance; dsh does the bulk
work. This is a focused skill and CLI, not a framework or agent platform.

## What it does

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

## Requirements

- macOS or Linux (POSIX). Windows is not implemented or tested; the CLI exits with a clear error.
- Node.js 20 or newer (uses `node:test` and `node:util.parseArgs`).
- A working `dsh` installation with credentials you configured yourself. See
  <https://github.com/deepseek-ai/deepseek-harness> for setup. This project does not install dsh,
  configure provider credentials, or change global dsh/Codex settings.
- Codex with skills support.

## Install

```sh
git clone https://github.com/Dsssyc/hey-my-buddy.git
cd hey-my-buddy
npm --prefix deepseek-delegate ci
```

The skill is self-contained in `deepseek-delegate/`. Copying just that directory somewhere and
running `npm ci` inside it is enough; there is no root package, nothing is published to npm, and the
only runtime dependency is the maintained `js-yaml` parser.

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
`cp -R "$PWD/deepseek-delegate" "$CODEX_SKILLS/deepseek-delegate"` and run `npm ci` inside the copy.
If an older version is already installed, remove or rename it yourself first; nothing here
overwrites it.

## Connect workspace grouping

Grouping is enabled by default. Keep your local `dsh web` service running and save the **full
startup URL**, including `?token=...`, as one line in
`${XDG_CONFIG_HOME:-$HOME/.config}/deepseek-delegate/web-url`. Use a private file (mode `0600`).
This is the **Web launch token**, not your DeepSeek API key (`sk-...`). Do not commit this file or
paste its contents into a task packet. After restarting the service, save its new startup URL.

The CLI exchanges that token at the same loopback origin for a cookie held only in memory. It
checks authentication and registers/reuses the workspace **before** starting the model task.
A missing or rejected credential fails before a headless run is created.

`--cwd` is canonicalized with `realpath`. An observer records the exact matching root session,
then, **after the headless process group has stopped**, the CLI asks the running Web host to attach
that persisted session. The host verifies its cwd and membership. Consequently grouping appears
when the run finishes, not while headless is still writing. The Web host and headless process must
use the same DSH session storage/home. No second process edits workspace storage JSON.

For a standalone run without Web authentication or sidebar grouping, explicitly use
`--no-workspace`. There is no silent ungrouped fallback.

### Recover grouping without repeating a task

Use the `workspace.sessionId` returned by a failed binding (or a known completed ordinary session):

```sh
node "$SKILL_DIR/scripts/run.mjs" --cwd /path/to/project --attach-session SESSION_ID
```

This verifies that the session exists before adoption. It runs no model and does not change the
shared default model. It does not scan or bulk-reassign history. The stored session cwd must equal
the canonical workspace path; mismatched/removed historical directories require separate handling.

## Quick start

This disposable example opts out of sidebar grouping. For real project tasks, complete the Web
setup above and omit `--no-workspace`. Write a task packet, then run the CLI:

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

## Result, exit codes, and logs

stdout carries exactly one JSON object, for example:

```json
{"status":"ok","mode":"run","exitCode":0,"signal":null,"error":null,"elapsedSeconds":42.1,"timeoutSeconds":1800,
 "requested":{"provider":"deepseek-official","model":"deepseek-flash","reasoningEffort":"max"},
 "cwd":"/path/to/project","taskFile":"/path/to/task.md","dshBin":"/path/to/dsh",
 "inputDelivery":"inline",
 "logPaths":{"stdout":"/tmp/deepseek-delegate-logs-XXXX/stdout.log","stderr":"/tmp/deepseek-delegate-logs-XXXX/stderr.log"},
 "workspace":{"enabled":true,"bound":true,"id":"workspace-example","path":"/path/to/project","sessionId":"session-example"},
 "finalText":"...","finalTextTruncated":false,
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
- Logs go to a unique owner-private directory (directory mode `0700`, file mode `0600`): under
  `--log-dir` when given, otherwise under the OS temp directory. Pre-existing files are never
  truncated or reused.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--cwd <dir>` | required | working directory dsh runs in |
| `--task-file <file>` | required for runs | file holding the task text |
| `--model <id>` | see precedence | model id for this run |
| `--provider <id>` | see precedence | provider id for this run |
| `--effort <name>` | `max` | reasoning effort for this run |
| `--timeout <seconds>` | `1800` | headless execution limit, integer 10–86400; Web requests have separate bounds |
| `--log-dir <dir>` | OS temp dir | parent directory for this run's private log directory |
| `--dsh-bin <path>` | see precedence | dsh launcher to execute |
| `--settings-file <path>` | see precedence | settings document to copy and override |
| `--no-workspace` | disabled | explicitly skip Web grouping |
| `--attach-session <id>` | | group an existing completed session; no task file/model run |
| `--dsh-web-url <url>` | see precedence | local Web startup URL; prefer a credential file |
| `--dsh-web-url-file <file>` | see precedence | file containing that URL |
| `--web-timeout <seconds>` | `15` | per-request network bound, integer 1–120 |
| `-h`, `--help` | | print help and exit (needs no dsh) |

## Precedence

| Item | Order (first match wins) |
| --- | --- |
| dsh launcher | `--dsh-bin` → `DSH_BIN` → `dsh` on `PATH` → `~/.local/bin/dsh` |
| settings document | `--settings-file` → `DSH_SETTINGS_FILE` → `$DSH_HOME/settings.yaml` → `~/.dsh/settings.yaml` |
| model | `--model` → `DSH_DELEGATE_MODEL` → settings `agent-default-model.model` → `deepseek-flash` |
| provider | `--provider` → `DSH_DELEGATE_PROVIDER` → settings `agent-default-model.provider` → `deepseek-official` |
| Web URL | `--dsh-web-url` → `--dsh-web-url-file` → `DSH_WEB_URL` → `DSH_WEB_URL_FILE` → default private URL file |
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
- The settings override assumes the booted profile mounts the dsh settings provider under the entry
  id `settings`, as the shipped profiles do. Custom plugins or profiles that read settings another
  way, or keep configuration in profile patch files outside `settings.yaml`, are not covered by the
  copy and stay in effect.
- Model and provider IDs are not validated against your provider's catalog; the wrapper does not
  downgrade, substitute, or retry them. Choose an ID your provider supports.
- On timeout or cancellation the wrapper stops only the dsh process group it started, then reports
  `timeout`/`cancelled` with a nonzero exit — never `ok`, even if the child exits 0 afterwards.
- Nothing here publishes, pushes, or messages anyone; that scope comes from you, and the skill
  inherits whatever you authorized for the session.

## Tests

```sh
npm --prefix deepseek-delegate test
```

The suite drives the real CLI against a mock `dsh` executable in temporary directories. It makes no
model calls, does not read your real settings, does not use your `HOME` or `CODEX_HOME`, and covers
launcher resolution, settings precedence and preservation, literal task delivery, the 32 KB
reference switch, the 6000-character stdout cap, unique private logs, spawn failures, timeouts, and
cancellation cleanup. Additional tests cover root-session capture, authentication, existence checks, verified workspace
binding, and recovery using a local HTTP fixture. No production Web service is contacted.
Nothing in this repository is published to npm.

## License

[MIT](LICENSE). The standalone skill directory includes its own copy of the license.
