# DSH harness runner and bridges

`harnesses/dsh/scripts/run.mjs` is the low-level Node runner that the `dsh` adapter spawns for every DSH task. Direct invocation owns one foreground process and prints its result as JSON; optional flags for governed turns also produce a private structured receipt. The runner has no authoritative blackboard records or `requestId` recovery, and its default standalone invocation reports `inquiry.enabled: false`. Use [usage.md](usage.md) and the [governed lifecycle](workflow.md) for durable task ownership, assistance, continuation and acceptance. ZCode uses its own native app-server controller ([workers.md](workers.md#zcode)) and Codex uses the installed App Server controller ([codex.md](codex.md)); neither goes through this runner. Paths on this page are relative to the plugin or repository root, the directory that contains `bin/buddy`.

The same directory also ships the DSH bridge plugins (`harnesses/dsh/plugins/`), the model catalog script (`model-catalog.mjs`), the decision helper (`decision.mjs`, see [decision.md](decision.md)) and the workspace bridge installer (`install-workspace-bridge.mjs`, see [operations.md#workspace-bridge](operations.md#workspace-bridge)). These are DSH-specific harness assets, not public service commands.

## Quick start

The example opts out of sidebar grouping so it works without the workspace bridge. For real project tasks, install the host bridge ([operations.md](operations.md#workspace-bridge)) and omit `--no-workspace`.

```sh
RUNNER="$PWD/harnesses/dsh/scripts/run.mjs"
DELEGATE_DEMO_DIR="$(mktemp -d)"
printf '%s\n' '{"numbers":[3,7,11]}' > "$DELEGATE_DEMO_DIR/input.json"
cat > "$DELEGATE_DEMO_DIR/task.md" <<'EOF'
Read input.json and write only result.json containing the sum and count of numbers.
Do not modify input.json or use the network. Verify result.json by reading it.
Acceptance: result.json equals {"sum":21,"count":3}.
EOF

node "$RUNNER" --no-workspace \
  --cwd "$DELEGATE_DEMO_DIR" \
  --task-file "$DELEGATE_DEMO_DIR/task.md" \
  --timeout 1800
cat "$DELEGATE_DEMO_DIR/result.json"
```

`--cwd` is required, and `--task-file` is required for a run (it is omitted in attach mode). `node "$RUNNER" --help` lists every option and needs neither dsh nor credentials.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--cwd <dir>` | required | working directory dsh runs in |
| `--task-file <file>` | required for runs | file holding the task text |
| `--model <id>` | see precedence | model id for this run |
| `--provider <id>` | see precedence | provider id for this run |
| `--effort <name>` | `max` | reasoning effort for this run |
| `--timeout <seconds>` | `1800` | whole-process-group execution limit, explicit `0` for no deadline or integer 10–86400; active cancellation still stops the owned group |
| `--log-dir <dir>` | OS temp dir | parent directory for this run's private log directory |
| `--dsh-bin <path>` | see precedence | dsh launcher to execute |
| `--settings-file <path>` | see precedence | settings document to copy and override |
| `--session-root <dir>` | owning harness session root | absolute private JSONL session directory for an ungrouped child; requires `--no-workspace` and the shipped `session-persistence-jsonl` profile entry |
| `--no-workspace` | disabled | explicitly skip workspace grouping |
| `--attach-session <id>` | | group an existing completed session; no task file, no model run |
| `--workspace-socket <path>` | see precedence | private socket served by the owning host plugin |
| `--workspace-timeout <seconds>` | `15` | per-request bound, integer 1–120 |
| `--inquiry-socket <path>`, `--inquiry-token <token>`, `--inquiry-results <path>` | | private per-run inquiry channel, supplied together by the owning Worker runtime; never by hand |
| `--turn-input-file <path>`, `--turn-output-file <path>` | omitted for standalone runs | paired private absolute paths for a governed turn; input must exist and output must be absent |
| `-h`, `--help` | | print help and exit (needs no dsh) |

`--no-workspace` conflicts with `--workspace-socket` and `--attach-session`. The `--inquiry-*` triple is never passed by hand: it mounts the per-run bridge that lets `buddy inquire` observe the run or ask its live agent a correlated question.

For governed ungrouped runs, the adapter passes an attempt-private `--session-root`. The native overlay changes only `session-persistence-jsonl.config.root`; it never changes `DSH_HOME`, copies the credential vault or moves settings. Grouped runs retain the owning harness session root because its workspace bridge verifies session membership there. The result records storage separately from Git isolation. A validated structured turn supplies the session ID even when grouping and its capture file are absent.

The two `--turn-*-file` flags must appear together and cannot be used with `--attach-session`. They work with `--no-workspace`; DSH session grouping and ownership of the execution workspace are separate contracts. Invalid turn flags, input schema or paths fail before the runner starts DSH.

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

- An explicitly named settings file (`--settings-file` or `DSH_SETTINGS_FILE`) must exist; a missing default file is treated as an empty mapping.
- Blank model/provider/effort values are rejected instead of silently clearing a default, and effort is never lowered implicitly.
- The run settings copy keeps every other section structurally identical, including `agent-presets` and any other section. Inside `agent-default-model`, only `provider`, `model` and `reasoningEffort` are written. The copy is passed to dsh through a temporary `--patch` overlay, and the original settings and credentials are never modified.
- Context window and output limits belong to your dsh model catalog and presets; this runner never sets them. A 1M-token context window is a model-specific configuration example, not a cross-provider promise.
- Task text over 32,000 bytes is delivered to dsh as a file reference; that file must stay in place until the run finishes. `inputDelivery` reports `inline` or `file-reference`.
- Obsolete `DSH_WEB_URL`/`DSH_WEB_URL_FILE` values are removed from the child environment; the removed web-URL options are rejected.

## Governed turn protocol

The adapter supplies version-1 `turn-input.json` in the private attempt directory. Both paths must be absolute, with existing owner-private parent directories (`0700`). Input must be an owner-private regular file (`0600`); a symlink is refused. Output must not already exist, including as a dangling symlink. The runner canonicalizes parent paths, so `turnResultPath` may expand an OS alias such as macOS `/var` to `/private/var`.

```json
{
  "version": 1,
  "taskId": "logical-task-id",
  "attemptId": "attempt-id",
  "generation": 1,
  "turnId": "turn-id",
  "resumeMode": "initial",
  "previousSessionId": null,
  "context": {},
  "executionWorkspace": {}
}
```

These are the complete top-level input fields; unknown fields are rejected. `context` and `executionWorkspace` are bounded JSON objects supplied by the service/Worker. They carry the original objective, checkpoint, Host decisions, helper outcomes, fixed artifact references, next actions and actual workspace information. Credentials belong in separate private files and must not appear in this model-visible input. The plugin injects a frozen copy of this context into the root agent's prompt.

`inputSha256` is SHA-256 of the input file's exact UTF-8 bytes, including whitespace and any trailing newline. The runner snapshots the file once and passes the parsed values and that hash through its private patch. `promptSha256` hashes the exact ordinary prompt delivered to headless; for a large task this is the file-reference prompt, rather than the contents of the referenced task file. Neither hash is an output-artifact hash. Output artifacts receive their own verified hashes or workspace seals after shutdown.

`plugins/turn-result.mjs` registers `buddy_finish_turn` only in the identified root agent's scope. It matches the first ordinary user inbox message against the delivered prompt hash before assembly of the first request, then requires that same first ordinary message to be committed to the root session. Canonical cwd, root lineage, live registry identity and the exact calling Agent must agree; ambiguous roots are refused. Internal subagents remain available, and their calls cannot conclude the root task.

The tool arguments are exactly `disposition`, `summary`, `remaining`, `decisions`, `artifacts` and `request`. `disposition` is `completed`, `assistance` or `attention`. A completed result requires `request: null`. Assistance or attention requires an object with nonblank string fields `summary`, `attempted`, `neededWork` and `acceptance`, a string array `expectedArtifacts`, and optional nonblank `suggestedProfileId`. It records a request for the Host; it does not create a helper task. `remaining` and `decisions` are string arrays. `artifacts` holds string or nonempty JSON-object references whose existence, content and hashes still require verification.

Input is limited to 256 KiB, the serialized outcome to 64 KiB, and the output record to 96 KiB. Arrays accept at most 32 entries. Summary and request text fields accept at most 8000 UTF-8 bytes each; string-list entries accept at most 4096 bytes, and each serialized artifact reference is limited to 4096 bytes. A supplied `suggestedProfileId` is nonblank text up to 256 UTF-8 bytes or null for no suggested profile; empty strings and other scalar/container types are refused. IDs, JSON nesting and metadata are also bounded. `harnesses/dsh/scripts/lib/turn-contract.mjs` owns the exact validators and schema.

The output record has the following shape; the identifiers and hashes shown here are placeholders:

```json
{
  "version": 1,
  "taskId": "logical-task-id", "attemptId": "attempt-id", "generation": 1,
  "turnId": "turn-id", "resumeMode": "initial", "previousSessionId": null,
  "sessionId": "new-root-session-id",
  "promptSha256": "<delivered-prompt-sha256>",
  "inputSha256": "<input-file-bytes-sha256>",
  "outcome": {
    "disposition": "completed", "summary": "Implemented and checked the result.",
    "remaining": [], "decisions": [], "artifacts": ["result.json"], "request": null
  },
  "provenance": {
    "tool": "buddy_finish_turn", "turnEnd": "completed", "flush": "awaited",
    "rootSessionMatched": true, "toolCallId": "call-1", "rootCallId": "call-1",
    "promptSeq": 3, "toolCallSeq": 5, "toolResultSeq": 6, "ptcDispatchSeq": null,
    "turn": 1, "turnEndSeq": 8, "flushSeq": 9
  }
}
```

Task, attempt, generation and turn identity come from the input; session identity, hashes and `provenance` come from configuration and native runtime events. The tool body stages validated arguments in a WeakMap keyed by the execution object and calls native `exec.concludeTurn()`. Acceptance requires successful final `tools/result`; a call nested in `run_code` also waits for that exact enclosing execution to succeed. Reused call IDs cannot substitute for execution identity. A guard denies later root tool calls once a result is pending or accepted, while already-admitted context may drain before the turn closes.

The plugin then requires the matching session `tool/result`, linked to the original `tool/call`, and a completed `turn/end`. It publishes the private output atomically, without overwriting a target, only from an awaited `session/flush` while the root is idle. A flush or write failure propagates to headless. `toolResultSeq` refers to the root call's committed result, including the outer `run_code` result in PTC mode. Native calls have equal `toolCallId`/`rootCallId` and `ptcDispatchSeq: null`; PTC records also require the successful nested dispatch event between the root call and its result. The enforced order is `promptSeq < toolCallSeq < toolResultSeq < turnEndSeq < flushSeq`; `flushSeq` is the session's next sequence at the flush checkpoint.

The first execution uses `resumeMode: "initial"` and `previousSessionId: null`. Later executions use `reconstructed-new-session` and preserve the previous session ID when known. Every headless invocation creates a fresh session; the runner rejects a reconstructed result that reuses the previous session ID. Continuation consumes the bounded durable context and pinned artifacts. The adapter does not resume the original DSH session or recover an omitted transcript implicitly. ZCode, by contrast, declares native session resume.

The native adapter was exercised with DSH 0.1.5-rc.1 using real initial and reconstructed turns, fixed artifacts, an internal subagent, awaited flush and confirmed shutdown. The [workflow guide](workflow.md) covers the complete task path; versioned runner, scheduler, integration and recovery evidence is kept in `docs/acceptance/` in the full repository.

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

- `status`: `ok`, `nonzero`, `timeout`, `cancelled`, `spawn-error` or governed `turn-result-error`; attach mode reports `ok` or `attach-error`.
- `elapsedSeconds` is the CLI duration measured with a monotonic clock, rounded to 0.1 s.
- Wrapper exit code: `0` only when the task is `ok` and any requested grouping is verified; `1` for a task, termination or grouping failure; `2` for usage and configuration errors, which are reported on stderr without a JSON object; a signal received before the child spawns exits `130`.
- `requested` is the route and effort actually written into the settings copy.
- `finalText` is at most 6000 characters of the head of the dsh stdout log, with `finalTextTruncated` (byte-based) telling you whether more existed. stderr and reasoning are never copied into the JSON.
- `workspace` reports `enabled`, `bound`, `id`, `path`, `sessionId` and any binding `error`. Task `status`/`exitCode` and log paths survive a grouping failure; check the process exit and `workspace.bound`, not task status alone.
- `inquiry` says whether this run's private bridge, requested by the owning Worker runtime, was mounted (`enabled`, its paths and any startup `error`). A bridge that fails to start never fails the run.
- `processState.shutdownConfirmed` is true only when the owned process group was observed stopped; it is required before the adapter reports success and before `acknowledge`.
- Logs go to a unique owner-private directory (directory mode `0700`, file mode `0600`): under `--log-dir` when given, otherwise under the OS temp directory. Pre-existing files are never truncated or reused. When the adapter runs a task, `--log-dir` is the attempt directory, so the runner logs sit beside the adapter's own `runner.stdout.log`/`runner.stderr.log`.

Governed runs additionally return `turn` (the validated record or `null`), `turnResultPath` and `turnResultError` (`null` when no turn-validation error was found). If headless exited zero but the required record is missing, malformed, mismatched or fails private-path checks, or the process group is not confirmed stopped, the runner reports `turn-result-error` and exits 1. The child `exitCode`, process evidence and log paths remain available. A valid file cannot promote nonzero, timeout or cancellation to success, and plain `finalText` cannot replace a missing structured outcome. A returned record alone does not prove shutdown or acceptance of the logical goal; the Python adapter's collection and sealing order is documented in [workers.md#governed-dsh-turns](workers.md#governed-dsh-turns).

## Attach mode

`--attach-session <id>` groups an already completed session instead of running a model. It verifies that the session exists before adoption, runs no model, changes no shared default model, and does not scan or bulk-reassign history. It emits a fixed `mode: "attach"` payload with no logs or final text. The stored session cwd must equal the canonical workspace path; use it to retry only the binding of a completed run.

## Bundled bridge plugins

| Plugin | Role |
| --- | --- |
| `plugins/session-capture.mjs` | During a grouped run, matches the delivered prompt hash to the root session and records `{sessionId, cwd, promptSha256}` privately; an ambiguous match refuses the binding |
| `plugins/inquiry-bridge.mjs` | Serves the per-run private socket for `inquire`; records activity and the correlated `buddy_inquiry_reply` answer over a bounded JSONL journal |
| `plugins/turn-result.mjs` | Registers the root-scoped `buddy_finish_turn` tool and publishes the governed turn record from the awaited session flush |
| `plugins/workspace-bridge.mjs` | Host-side plugin installed into a DSH profile; serves `ping`/`resolve`/`attach` on the private workspace socket |

The workspace bridge checks root lineage and canonical cwd equality before it creates or attaches a session, and it verifies membership afterwards. It never activates an agent. Install, recovery and the stale-socket rule are owned by [operations.md#workspace-bridge](operations.md#workspace-bridge).
