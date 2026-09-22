# Operations

How to install, upgrade, recover and clean up a Buddy installation. Command syntax and fields are in [cli.md](cli.md); internals are in [architecture.md](architecture.md); the practical path is in [usage.md](usage.md). The `deepseek-delegate/` directory is the complete distributable unit for both entrypoints: the standalone `$deepseek-delegate` skill and the `$buddy` plugin ship the same directory layout, and every launcher resolves its project from its own location rather than from the caller's working directory.

## Runtime lifecycle and upgrade

Buddy installs Python dependencies with uv from a frozen lock (PyPI `c-two==0.5.1` and PyYAML; Python `>=3.12,<3.15`). The service and its workers execute from a **content-addressed stable runtime** outside the Codex plugin cache, so replacing the plugin does not disturb a running service.

- **A production cold start installs the runtime automatically.** With no READY runtime, the first CLI command that needs the service copies the runtime assets, runs `uv sync --frozen --no-dev --python 3.12` inside the final `BUDDY_RUNTIME_ROOT/<contentId>` directory, and writes `READY.json` last. Credentials, user data, tests, `.env` files, `node_modules` and any existing virtual environment are never copied. This is why the first run can take noticeably longer.
- **Manual materialization is preinstallation**, useful before shipping a bundle or on a machine that should not install at first use:

  ```sh
  uv run --frozen --project deepseek-delegate python -c "from buddy import runtime; runtime.materialize()"
  uv run --frozen --project deepseek-delegate buddy runtime
  ```

- **`READY` only means installed.** `buddy health` reports `runtimeIdentity`, `runtimeContentId` and `runtimeStable`; `stable` is true only when the running process actually imports `buddy` from that runtime with no path leaking back into a plugin cache or the checkout. `buddy runtime` prints the same identity plus a source-leak report.
- **An explicit `BUDDY_RUNTIME` pin always wins**, including over the runtime matching the current assets, so a pin left over from an older release keeps running that older code. Check `runtimeContentId` after every upgrade; leave the pin unset for the automatic selection.
- **`BUDDY_DEV_SOURCE=1` suppresses automatic materialization for source tests.** It uses the checkout when no READY runtime has been selected; an existing matching runtime or one pinned by `BUDDY_RUNTIME` still takes precedence, so use a private `BUDDY_RUNTIME_ROOT` and check the reported identity when testing source.
- **Explicit `worker-start` uses the same runtime selection.** Starting an additional supervisor from a staged/plugin launcher selects or materializes a READY runtime, uses its Python and package imports, and aligns its Python bridge/environment paths with that runtime. Existing supervisors keep the runtime they already loaded. An attempt's `runtimeIdentity` records the runtime actually executing its Worker.
- **Docs-only refreshes need no destructive step.** Documentation is not part of the hashed runtime content, so replacing the plugin cache with a docs-only change keeps the same content id and reuses the existing READY runtime (manual `materialize()` simply reports `installed: false`). An already-running daemon keeps serving from the runtime it loaded; the next cold start launches from that same runtime outside the cache.
- **Changing any hashed asset changes the runtime.** `python/buddy` (including the console build), `scripts/lib`, `plugins`, `scripts/buddy.mjs`, `scripts/run.mjs`, `scripts/handoff.mjs`, `scripts/model-catalog.mjs`, `scripts/decision.mjs`, `scripts/decision-prompt.mjs`, `pyproject.toml`, `uv.lock` and `package.json` are hashed, so a code or dependency change produces a new content-addressed directory on the next cold start. A daemon already running keeps its own runtime and code until it is restarted or stopped.
- **`restart` is the detach without cancellation.** It writes a resume file, returns, and preserves every independent worker; the next autostart-capable CLI call starts a fresh daemon (which reconciles existing attempts). Use it when the host should move to new code but owned work must survive. It is **not** a schema upgrade: a database version change requires a stopped service and the explicit offline migration below.
- **`stop` is the explicit "cancel owned work and stop" operation**, not a required cache-refresh step: it cancels queued tasks, writes durable cancel intent for active attempts, drains for a bounded interval and reports `unresolvedAttempts`. It stops the daemon-owned supervisor; a supervisor started explicitly with `worker-start` is a separate process and needs its own cooperative `worker-stop`.
- **Verify after any upgrade:** `buddy health` and `buddy runtime` must report the expected identity and stability before new work is delegated.

For a source installation (`runtimeStable: false`), keep its checkout available while work is active. Complete that work before replacing the source, or deliberately cancel it and inspect the shutdown result. Review version-specific migration requirements before switching code that changes the database or RPC contract.

## Database upgrade

Schema 7 adds the shared evaluation and governed workflow tables. Startup refuses schema 5/6; `migrate` is the explicit offline path and never stops services or Workers itself. Prepare the new release before stopping the old one, and keep its launcher available until verification is complete.

Choose the actual old and new launchers. Both must use the same private state directory; set `BUDDY_STATE_DIR` explicitly if using a non-default board.

```sh
OLD_BUDDY=/abs/old/deepseek-delegate/scripts/launch-buddy.sh
NEW_BUDDY=/abs/new/deepseek-delegate/scripts/launch-buddy.sh
"$OLD_BUDDY" health
"$OLD_BUDDY" workers
```

Complete owned work, or explicitly cancel work whose cancellation is authorized. Require `activeWork:0` and confirmed outcomes before switching; an expired lease or missing PID does not establish shutdown. If health reports outstanding work, inspect the affected tasks with `status` or bounded, state-filtered `list` calls. For each additional local supervisor you started explicitly, request `worker-stop '{"workerId":"<actual-extra-worker>"}'` and verify its cooperative exit as described in [workers.md](workers.md#worker-supervisors).

With work settled, stop the old service. Require `stopped:true` and an empty `unresolvedAttempts`; otherwise resolve the outstanding old-runtime ownership before proceeding.

```sh
"$OLD_BUDDY" stop '{"drainSeconds":120}'
"$NEW_BUDDY" migrate '{"dryRun":true}'
"$NEW_BUDDY" migrate '{"confirm":true}'
```

The dry run takes the daemon/board ownership locks and validates version, integrity and foreign keys, then reports the plan and preserved table counts without creating a backup or changing the schema. Execution writes and verifies a `board.sqlite3.v<source>-backup-<timestamp>` sibling, adds the missing tables and version change in one transaction, and checks preserved counts and integrity before committing. It retains existing task, attempt, event, receipt, claim and evaluation identities. A failed transaction rolls back; its backup remains available. An already-current database is a no-op, and unsupported versions are refused. The command accepts an explicit `stateDir` too.

Update the installed standalone link/copy and [plugin installation](usage.md#plugin-installation) to the same release, then use the new launcher:

```sh
"$NEW_BUDDY" health
"$NEW_BUDDY" runtime
```

Verify `schemaVersion:7`, `runtimeStable:true`, the release's expected `runtimeContentId` and no runtime leaks. An explicit `BUDDY_RUNTIME` pin wins over automatic selection; remove an obsolete pin from the launch environment before starting the new service. `BUDDY_DEV_SOURCE` is a development opt-out, not a production upgrade step. Installed copies may occupy different directories, but must contain the same released code. Start a new Codex task to load the updated skill instructions.

Do not run an old autostart-capable launcher against the migrated board. `restart` preserves Workers and is not a substitute for this offline schema upgrade. Keep the previous package/runtime and verified database backup for recovery; never overwrite a live board with its backup. Tests and previews use separate private state/runtime roots.

## Workspace bridge

`workspace: true` (the default) groups the dsh session through a bundled host plugin; it controls session grouping only, while a governed task's `executionWorkspace` is a separate checkout/worktree ownership contract. Grouped runs require the bridge to be installed and its dsh profile running; there is no silent fallback to an ungrouped run.

```sh
node deepseek-delegate/scripts/install-workspace-bridge.mjs
```

- The installer defaults to `--profile web` and appends one entry to that profile's `cordis.patch.yml`, keeping existing text and writing a private backup first. Use `--home`, `--profile` or `--workspace-socket` for another existing long-lived profile.
- The bridge serves an owner-private Unix socket, by default `$DSH_HOME/deepseek-delegate/workspace.sock` (`~/.dsh` when `DSH_HOME` is unset). There is no Web URL, browser token or HTTP dependency.
- Long-lived profiles hot-reload the user patch; otherwise start `dsh web` normally.
- Before a model run, the runner checks that the bridge answers and resolves the canonical cwd. If it is missing, unusable or misconfigured, the run fails with an explicit error (exit 2) instead of running ungrouped. Use `workspace: false` (service) or `--no-workspace` (runner) to opt out deliberately.
- A stale socket left by an unclean exit is **refused, never replaced**. Verify the old host process has stopped, then remove that socket yourself and restart the profile.
- Removed legacy options are rejected by the runner (`--dsh-web-url*`, `--web-timeout`), and the old `~/.config/deepseek-delegate/web-url` file is no longer read; delete it when convenient. Model API credentials and unrelated settings are untouched.
- The bridge never activates an Agent; it validates the run's persisted root session and cwd, calls the official workspace API inside the owning host (`ctx.workspaceRegistry.create(cwd)`, then `workspace.attachSession(sessionId)`), and verifies membership. Task outcome and grouping outcome stay separately visible.

Recover binding without rerunning a task, using a `workspace.sessionId` from a failed binding or a known completed session:

```sh
node "$SKILL_DIR/scripts/run.mjs" --cwd /path/to/project --attach-session SESSION_ID
```

This verifies the session exists before adopting it, runs no model, and does not scan or bulk-reassign history. The stored session cwd must equal the canonical workspace path.

## Private state and environment

All authoritative state is local SQLite under `BUDDY_STATE_DIR` (default `~/.local/share/hey-my-buddy`, mode `0700`):

- `board.sqlite3` — tasks, attempts, workers, messages, artifacts, events, command receipts and resource claims; `buddy health` runs an integrity check;
- `control.json` — the private endpoint address and service token (never group/world readable);
- `control-daemon.lock`, `board-owner.lock` — lifetime ownership locks: one daemon per state directory;
- `workers/<workerId>/` — supervisor lock and status, cooperative stop request, startup intent and the receipt spool;
- `attempts/<runId>/<attemptId>/` — task text, spawn intent/marker, adapter logs and the per-attempt inquiry credentials;
- `worker.log`, `control.log`, `runtime-install.log`, `restart.resume.json`, `stop.request.json`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BUDDY_STATE_DIR` | `~/.local/share/hey-my-buddy` | state directory |
| `BUDDY_RUNTIME_ROOT` | `~/.local/share/hey-my-buddy/runtime` | parent of content-addressed runtimes |
| `BUDDY_RUNTIME` | unset | pin one READY runtime directory; an explicit pin always wins |
| `BUDDY_DEV_SOURCE` | unset | suppress automatic materialization; an already selected READY runtime still wins |
| `BUDDY_MAX_CONCURRENT` | `1` | simultaneous active attempts, clamped 1–8 |
| `BUDDY_WAIT_CAPACITY` | `32` | admitted waits on the dedicated wait resource |
| `BUDDY_LEASE_SECONDS` | `120` | attempt lease, clamped 15–3600 |
| `BUDDY_WORKER_ID` | `local` | worker supervisor id started with the daemon |
| `BUDDY_NODE` / `BUDDY_RUNNER_PATH` | unset | dsh adapter node binary / runner entrypoint overrides |
| `BUDDY_AGENT_CREDENTIAL` / `BUDDY_AGENT_CREDENTIAL_FILE` | set by the dsh adapter | attempt-scoped CLI credential: restricted to its own run; Host operations are denied and it never falls back to the administrator token |
| `BUDDY_DEBUG` | unset | include exception detail in `INTERNAL_ERROR` |
| `UV_BIN` | `uv` on `PATH` | uv binary used for runtime installs |

The launcher also honours `PLUGIN_DATA` when the Codex plugin host provides it, placing the uv environment under that directory (`UV_PROJECT_ENVIRONMENT`) so the plugin cache stays disposable.

A request ID is an idempotency key: retry the same input and ID after an uncertain start; changed input is rejected. Results survive service restart. Attempts whose shutdown was never confirmed stay `uncertain`/`reconciliation-needed` with their claims retained, are never replayed, and refuse a retry until the worker that owns the process handle reports an observed outcome. No stored PID is used to signal old processes.

The daemon also binds a guard on the removed implementation's `service.sock` path so a cached legacy daemon cannot claim the same records; a connecting legacy client receives an explicit `MIGRATED` error. The new service never dispatches work through the old endpoint and never rewrites what the old implementation wrote.

The removed MCP path also removed the native App completion notification: the plugin's completion inbox, its binding/`notification_ack` bookkeeping and the native stdio client are gone. Historical `notifications/*.json` files are left untouched, never read and never replayed; only the CLI result path (`status`/`wait`/`result`/`await`) delivers results now.

**Permissions.** A CLI invocation runs with the invoking shell's permissions, and the launcher grants no extra privilege; an already-running daemon or worker keeps the permissions it was started with, so attaching from another task does not re-sandbox it. Attaching to an already healthy service does not change state-directory permissions or create daemon locks/logs. The launcher may still prepare its uv environment; cold startup also writes under the state and runtime roots.

## Cancellation and recovery

- Ending a CLI wait (Ctrl-C, closed terminal, `waitSeconds` expiry) cancels only the wait. The durable task keeps running while a worker owns it and is recovered by the same `runId` or `requestId`; recovery replays the identical start request and never launches a second adapter.
- `cancel` cancels queued work immediately and writes a durable cancel request for an active attempt. The worker that owns the child observes that intent and stops its own process group. `workflow-cancel` fences a governed goal and its complete owned descendant graph with the same honest stop-evidence rule. Unrelated dsh sessions are unaffected.
- A daemon restart marks every in-flight attempt `uncertain` with its resource claims retained, and busy workers `lost`. The legitimate worker keeps its child process, deadline and receipt, and reattaches by attempt identity, generation and nonce. Nothing is reattached by PID.
- **Unknown is not stopped.** An expired lease or a missing PID never proves a process ended. `retry` is refused while the previous attempt's shutdown is unconfirmed (`SHUTDOWN_UNCONFIRMED`), and only the worker holding the process handle can report an observed outcome or release an attempt that never spawned. A governed goal is continued with explicit new input rather than a legacy retry.
- A worker replays its immutable completion receipt until the service confirms the result transaction; replaying that receipt does not execute the task again. Explicit retry requeues an eligible task; its next claim creates a new attempt generation. Previous acceptance is cleared from the task and remains readable in the event stream.
- If a wait returns `wait-timeout` or `unavailable`, keep monitoring the same run or report its `runId` explicitly; never relaunch and never describe the wait limit as an execution failure.

## Legacy import

The removed Node implementation's durable records can be imported offline in one transaction:

```sh
"$BUDDY" legacy-import '{"sourceDir":"/old/state","dryRun":true}'
"$BUDDY" legacy-import '{"sourceDir":"/old/state","dryRun":false}'
```

- The importer refuses to run while the old owner is alive (`LEGACY_OWNER_RUNNING`) or while any legacy run is still active (`LEGACY_ACTIVE_RUNS`), and a strict run aborts the whole import on a malformed, duplicated or conflicting record (`LEGACY_CONFLICT`).
- Pointing `sourceDir` at this service's own state directory is refused as `LEGACY_SOURCE_IS_LIVE_TARGET`. With `"snapshot": true`, the importer can copy the legacy `record.json`/`task.txt` files still stored there into a private snapshot and import that copy (`snapshotDir` in the result). The old owner must already be stopped; snapshot mode never bypasses the old-owner or active-run checks.
- Original run IDs, request IDs, fingerprints, outcomes, inquiry and acceptance evidence and timestamps are preserved; the source files are only read. A record whose task text is missing is kept as readable history with an explicit recovery limit, and its request ID can never launch a duplicate.
- The dry run reports counts, conflicts and notes without importing database records; snapshot mode can still create the private copy. Imported results and acceptance remain readable afterwards; verification is the same as for any other result.

## Removing the old MCP path

Nothing needs to be installed or registered for MCP any more, and Buddy never edits user configuration. If the plugin's old MCP server was registered, delete the leftovers yourself when convenient:

- `[mcp_servers.buddy_ctwo]` and any `plugins.*.mcp_servers.*` Buddy entry in `~/.codex/config.toml`, including `tool_timeout_sec`, `approval_mode` and `BUDDY_TOOL_WAIT_BUDGET_SECONDS` fields;
- any plugin-scoped Buddy tool approval remembered by the App;
- the now-unused `mcp.json` in an older plugin cache copy.

Keep every unrelated Codex/App tool and setting, especially the official App heartbeat automation used for explicit background follow-up — it is a separate tool that calls the Buddy CLI and can be approved independently.
