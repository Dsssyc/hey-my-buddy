# ZCode hooks probe (P3): per-attempt private hooks and PostToolUse nonce injection

Status: bounded probe-level experiment, recorded 2026-09-25 by the governed root attempt for task `2d0b9207-ea59-4f1a-8674-ba1cf098ec02` (turn `b9b2f0f2-bbbb-4164-af5d-a251a36d4652`, attempt `c7dd790c-7199-4d4d-8c85-a3f7b92b6674`). This record proves behavior of the installed native CLI in private subprocesses only; it is **not** acceptance of a production capability, the Buddy ZCode adapter and its declared capabilities were not modified, and ZCode continues to declare read-only observation.

## Question and answers

1. Can hooks be installed per attempt/private `ZCODE_STORAGE_DIR` without changing user `~/.zcode` state? **Yes, but only through a workspace-scoped inline plugin; the config-file hooks path is blocked.** `ZCODE_STORAGE_DIR` verifiably redirects all native state (session DB, plugin cache, exec, logs) into a private directory, yet the user-level config file stays hard-coded at `$HOME/.zcode/cli/config.json`: a hooks block written into `<storage>/cli/config.json` never fires (paid run 1). A `<cwd>/.zcode/config.json` `plugins.dirs` entry pointing at a private plugin directory with `hooks/hooks.json` does fire (paid run 2), and everything involved lives inside the probe's private work directory.
2. Can `PostToolUse` `additionalContext` carry a fixed harmless nonce into the same running model turn? **Yes.** The hook returned `hookSpecificOutput` `{hookEventName: "PostToolUse", additionalContext: "PROBE_NONCE=<nonce>"}` and the model's continuation in the same turn quoted the exact nonce; the hook's stdin `session_id` matched the CLI session id exactly.

## Environment identities (actual)

| Item | Value |
| --- | --- |
| App | 3.14.3 (`CFBundleShortVersionString`, `/Applications/ZCode.app/Contents/Info.plist`) |
| CLI | 0.16.9 (`node zcode.cjs --version`) |
| Bundle | `/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs`, sha256 `b1df2ef3e5bd76c4af3ecb296bc003a10d3f13191a26610bd0ba940feadad529` |
| Node | v25.8.1 (`/opt/homebrew/opt/node/bin/node`) |
| Platform | macOS darwin/arm64 |
| Docs consulted | `https://zcode.z.ai/en/docs/hooks` (project-level `hooks` blocks documented ignored; config-file hooks need `hooks.enabled: true`) |

## Isolation mechanics (all runs)

Each native subprocess received a constructed minimal environment — never the shell's inherited one: `ZCODE_STORAGE_DIR=<work>/storage`, `ZCODE_LOG_DIR=<work>/log`, `ZCODE_SESSION_DB_PATH=<work>/sessions.sqlite`, `ZCODE_LOG_CONSOLE=0`, plus passthrough of `HOME`/`PATH`/proxy variables and the harness-provided `ZCODE_BUILTIN_PROVIDER_CONFIG_FILE`/`ZCODE_PERSONAL_PROVIDER_CONFIG_FILE` (used read-only; contents never printed or copied). `BUDDY_STATE_DIR`, `BUDDY_RUNTIME_ROOT`, `BUDDY_RUNTIME`, `BUDDY_RUNTIME_IDENTITY`, `BUDDY_WORKER_STATE`, `BUDDY_WORKER_ID`, `BUDDY_AGENT_CREDENTIAL`, `BUDDY_AGENT_CREDENTIAL_FILE`, `VIRTUAL_ENV`, `UV_PROJECT_ENVIRONMENT` were scrubbed per AGENTS.md. Before and after every probe run the entire `~/.zcode` tree is hashed (sha256 per file, names only); the diff is part of every summary.

## Paid run 1 — config-file hooks block under private storage: negative

Command (as run by `tests/probes/zcode_hooks.py --paid`, `--hook-via config` default at that time): `node /Applications/ZCode.app/Contents/Resources/glm/zcode.cjs --prompt "<probe prompt asking for one Bash call: echo p3-hook-probe, then to quote any additional context note>" --cwd <work>/cwd --json` with the private environment above and `<storage>/cli/config.json` containing `{"hooks": {"enabled": true, "events": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "process", "command": "/usr/bin/python3", "args": ["<hook.py>", "<log>", "P3HOOK-abc5228b927e7248", "PostToolUse", "<run token>"], "timeoutMs": 15000}]}]}}}`.

Result: exit 0 in 10.3 s, session `sess_a5092a82-7dcd-43e3-a350-c6b9f63e40e8`, `modelRequestCount: 2` (the model did call Bash and saw `p3-hook-probe`), reply `"...No additional context note was received after the tool result.\n\nNO_NOTE"`, usage 44 909 input / 151 output tokens. The hook log contained only the probe's own self-test record; the private CLI log contains no hook registration or run events; `~/.zcode` stayed byte-identical. Conclusion: the hooks block in the storage-redirected config is not loaded as a hook source — config-file hooks are global-only, matching the bundle (the only `config.json` resolution joins `homedir()` with `.zcode/cli`, and no `ZCODE_CONFIG_DIR`-style variable exists; `ZCODE_STORAGE_DIR` redirects state roots, not the user config path).

## Paid run 2 — workspace-scoped inline plugin under private storage: positive

Setup written by the probe into the private work directory only: `<work>/plugin/.zcode-plugin/plugin.json` (`name: p3-probe-hooks`, `version: 0.1.0`, `hooks: "hooks/hooks.json"`), `<work>/plugin/hooks/hooks.json` (`{"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "process", ...same hook argv with nonce P3HOOK-bee50ad064c1a24f...}]}]}}`), and `<work>/cwd/.zcode/config.json` `{"plugins": {"dirs": ["<work>/plugin"]}}`. Free pre-check in the same run: `zcode --cwd <work>/cwd plugins list` exited 0 and listed the plugin (the bundle's `resolveCandidates` gives `config.dirs` candidates `defaultEnabled: true`, source `inline`).

Command: same prompt form as run 1, `--cwd <work>/cwd --json`. Result: exit 0 in 11.4 s.

- Session/turn: `sess_31c69777-273f-4a1e-879f-21f7e7ec0754`, `turn_fa46d0c4-c62c-4d29-a7d2-043087c779cc`, `modelRequestCount: 2`, usage 44 957 input / 385 output tokens.
- One PostToolUse hook record with stdin bindings: `session_id` `sess_31c69777-273f-4a1e-879f-21f7e7ec0754` (equal to the CLI session id), `hook_event_name` `PostToolUse`, `tool_name` `Bash`, `tool_use_id` `call_305a4ba791ec49469d08d21e`, `permission_mode` `yolo`, `cwd` the private cwd, `transcript_path` a per-hook temp path, plus sha256 of `tool_input` and `tool_response` presence (contents never logged).
- Model reply (same turn, after the tool call): `"...An additional context note was received from the hook.\n\n#1\nPROBE_NONCE=P3HOOK-bee50ad064c1a24f"` — the nonce existed only inside the hook's `additionalContext` payload.

## Global `~/.zcode` unchanged; external appends attributed

Paid run 1: global-root snapshot diff empty. Paid run 2: snapshot diff showed two appended files, `cli/log/zcode-2026-09-25.jsonl` and `v2/logs/2026-09-25.log`; inspection shows the appended lines are periodic `zcode_protocol.process.memory_sample` heartbeats from the separately running ZCode desktop app (its `v2/` log names its own pids 8626/8749), and **no line in the global CLI log references either probe session id**, while the probe subprocess's own CLI logged 94 lines referencing its session inside the private `ZCODE_LOG_DIR`. Both paid runs therefore wrote nothing into `~/.zcode` themselves; the appends are concurrent user-app traffic that any hash comparison during desktop-app activity will see.

## Technical blocker (config-file path)

Installing hooks for a single attempt by writing `hooks.events.*` into `<storage>/cli/config.json` does not work: the native CLI reads configuration-file hooks only from the hard-coded `$HOME/.zcode/cli/config.json` (global) and ignores workspace `hooks` blocks by documented design. Modifying the global file is outside this task's authorization, so that path stops here by rule, with the run-1 negative result as evidence. The only observed per-attempt mechanism is the workspace-scoped inline plugin, whose reach is one working directory (`<cwd>/.zcode/config.json`), which is exactly what the probe used inside its private work directory.

## Reproduce

- Free (no model call, no state writes outside the work dir): `uv run --frozen python tests/probes/zcode_hooks.py` — assembles a private workspace under ignored `.dsh-skill-build/`, self-tests the hook, runs `--version` and `plugins list` privately, hashes `~/.zcode` before/after, prints a redacted JSON summary.
- Paid (small model run; this task's budget of two was fully spent): `uv run --frozen python tests/probes/zcode_hooks.py --paid [--hook-via config|plugin]` — adds one headless `--prompt` run and records the hook log, session/turn bindings and nonce observation in `summary.json`.
- Focused tests: `uv run --frozen python -m unittest discover -s tests/python -p 'test_zcode_hooks_probe.py' -v` — 13 tests, all passing; they cover credential scrubbing, snapshot/diff, hook self-test, plugin/config assembly, and the free probe pipeline including private plugin discovery.

## Boundary

Both paid native probes are spent; any further paid runs need new authorization. The probe does not hot-reload running sessions, does not modify the Buddy ZCode adapter, workers or routing contracts, and its plugin-registration recipe is a workspace (`cwd`)-scoped effect, not a per-attempt daemon-side switch — wiring it into an adapter would be new work outside this record.
