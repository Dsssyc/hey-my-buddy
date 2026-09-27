# Operations

How to install, run, recover and retire a Buddy installation. Command syntax and fields are in [cli.md](cli.md); internals are in [architecture.md](architecture.md); the practical path is in [usage.md](usage.md). The supported distribution is the Codex plugin: the `.agents` marketplace catalog, one `bin/buddy` launcher, `src/buddy` source, the DSH harness assets, the built console and the `skills/buddy` entrypoint. There is no standalone skill directory and no old-layout launcher.

## Installation

The supported distribution is the Codex plugin `hey-my-buddy`, and this repository is its own marketplace: the committed `.agents/plugins/marketplace.json` points at the plugin root, so a first installation depends on no personal marketplace entry and no public registry listing. A public listing is optional follow-up work and is not claimed here.

From Git, register the repository as a marketplace and install the entry it publishes:

```sh
codex plugin marketplace add Dsssyc/hey-my-buddy --ref main
codex plugin add hey-my-buddy@hey-my-buddy
```

From a local checkout, register that checkout's absolute path instead; the catalog resolves `"path": "./"` against the marketplace root:

```sh
codex plugin marketplace add /abs/path/to/hey-my-buddy
codex plugin add hey-my-buddy@hey-my-buddy
```

A local (non-Git) marketplace resolves in place, so installing copies the working tree — including untracked and ignored content such as `.venv`. Prefer a fresh clone or the staged path below when the source is a development checkout.

`packaging/stage-plugin.py` assembles the supported tree and carries the same catalog, which makes a staged plugin directory a marketplace root of its own:

```sh
uv run --frozen python packaging/stage-plugin.py --destination /path/to/plugins/hey-my-buddy
codex plugin marketplace add /path/to/plugins/hey-my-buddy
codex plugin add hey-my-buddy@hey-my-buddy
```

An already configured marketplace whose entry points at a staged directory works too: stage into that marketplace (for example `/path/to/marketplace/plugins/hey-my-buddy`) and run `codex plugin add hey-my-buddy@your-marketplace` with its catalog name. `packaging/stage-plugin.py` copies the metadata files including `.agents/plugins/marketplace.json`, both READMEs, `AGENTS.md`, `docs`, `skills/buddy` and the declared runtime assets, refuses tests, virtual environments, the React source and scratch content, and replaces the destination atomically after verifying the inventory.

Keep the following facts in mind for every install path:

- The manifest `version` is the install cachebuster; `codex plugin add` is both the install and the update path, so publish a new version to publish new code. The repository root `plugin.json` and `.codex-plugin/plugin.json` must keep the same `name` and `version`; staging refreshes the portable copy from the Codex manifest.
- Do not pass `--sparse` with this repository's catalog: the plugin lives at the marketplace root (`"path": "./"`), and a sparse checkout that omits the cited path leaves an entry Codex silently skips. A Git marketplace snapshot is refreshed with `codex plugin marketplace upgrade <name>`; pin it with `--ref`.
- The implicit personal marketplace (`~/.agents/plugins/marketplace.json`, listed by Codex with root `$HOME`) is optional and unrelated. Nothing in this installation depends on it, and no public listing is required.

For a retained-data upgrade, obtain the user's installation authorization, install/stage the new package using the chosen marketplace path, then run **that new package's** `bin/buddy upgrade`. This one command owns idle checks, rolling backup, daemon and idle-supervisor cutover, verification and rollback. Do not run `stop`, cancel tasks, archive the entire state tree or manually replace the database. An interrupted upgrade resumes recovery by running the same command again. A legacy 0.15.1 service has no backup RPC: the launcher fences startup, detaches it without cancellation, rechecks idle ownership and runs the same backup implementation under the exclusive maintenance owner before opening the new runtime. A work admission racing that detach aborts the upgrade and returns to the previous service with work intact.

After installation or upgrade, start a new Codex task to load that plugin version's `$buddy` skill and resolve its bundled launcher, then check the installed identity and the resolved marketplace:

```sh
codex plugin list --json
"$BUDDY" health
"$BUDDY" runtime
"$BUDDY" capabilities
```

Requirements are macOS or Linux, uv with Python 3.12–3.14, Node.js 20+ for the DSH runner and decision helper, and a working local `dsh` and/or ZCode installation with its own provider credentials (or the Codex App Server with an existing account-plan login, which requires no API key). The `claude` 0.11.0 candidate additionally needs the installed Claude Code CLI with a first-party Anthropic account and stays unavailable until that CLI is authenticated. Buddy never writes global harness model settings.

## 0.16.0 backup, upgrade and storage contract

This section is the authorized target; acceptance records distinguish implemented code, private validation and separately authorized installation. Schema stays 12. Backup and retention metadata live outside SQLite. A schema change requires stopping and explaining the need to the user first.

`buddy backup` is a service operation using SQLite online backup. Compress the verified database and retain durable attempt receipts, controls and submissions, together with a manifest containing schema, contract, runtime identity, plugin commit and SHA-256 for every payload file. Exclude workspaces, native harness homes, plugin source, caches and process locks. Validate SQLite integrity and foreign keys, all hashes and a private trial open before publication. All manual and upgrade backups share `backups/current/` inside the state root. Build in `backups/.incoming/`, atomically exchange the verified generation into current, then remove the old generation. Validation or publication failure preserves current. A recovery marker resolves a crash during exchange without leaving an ambiguous current backup. Only one completed backup is retained.

Upgrade an existing installation with the new package's `bin/buddy upgrade`; the Host must not compose an ad-hoc copy/restart sequence. The launcher coordinates idle preflight, the same verified backup, client/daemon/idle-supervisor cutover, stable-runtime cold start and health/runtime/data validation. Refuse queued/running/cancelling work and any unconfirmed stop; never cancel work to upgrade. Fence new admissions across the final preflight and cutover. A failure after cutover restores the single backup with the previous runtime and verifies recovery. Preserve current and previous runtime; remove any older runtime only after proving no process uses it. Unknown process usage blocks deletion. The command reports backup bytes/time, cutover identity, validation and rollback evidence. Installing or switching the daily service requires the user's separate authorization.

`buddy storage plan` reports bytes and reclaimable bytes for native homes, managed workspaces, runtimes, backup and durable state, plus explicit protected reasons and orphan daemon/supervisor observations. A plan is private, expiring and binds candidates to their identities/fingerprints. `buddy storage apply` requires the exact confirmed plan and rechecks every condition before deleting; it never follows symlinks or accepts arbitrary caller paths. Concurrent or changed data invalidates the affected candidate. Durable board, receipts, controls, submissions and the current backup remain protected.

ZCode native continuation depends on the proven private binding and native session database/storage under its per-goal home. Never prune individual guessed cache files or retain only the binding. A whole home is eligible only after its owning goal is accepted or cancelled, all owned execution is proven stopped, no live descendant/recovery/native continuation can reference it, continuation is permanently refused for that state, and a three-day grace period has elapsed. Otherwise preserve the complete home. Accepted managed workspaces use the existing workspace cleanup eligibility, immutable artifact/integration, dependency and Git-state checks; Host-owned and unrelated worktrees are never candidates. Reclamation may automatically apply only already-authorized accepted-workspace lifecycle cleanup under those same rules.

List Buddy daemons/supervisors not associated with the current daily control.json using observed identity and state/runtime roots; never stop them as part of storage apply. Obtain explicit user confirmation to stop a specific orphan. Never inspect or empty the user's Trash. `buddy.checks` owns private test roots and asserts that no child daemon or supervisor remains when it exits, retaining failure evidence if teardown is incomplete.

## Runtime lifecycle

The [installed 0.15.1 runtime](../acceptance/installed-0.15.1.md) uses contract 0.15.1 and schema 12. It was switched from the idle 0.15.0 service with a verified state/plugin archive and an unchanged database; the recorded runtime identity and preserved configuration are installation facts, distinct from [source acceptance](../acceptance/readonly-objective-browser-0.15.1.md). New Codex tasks load the updated skill; existing Hosts must resolve the new bundled launcher before dispatching work against the upgraded service.

Current source contract is 0.16.0, schema 12 and C-Two 0.6.0. The daily 0.15.1 installation above remains unchanged until separately authorized. `ping` is the lightweight attachment check; `health` performs explicit current diagnostics. Source changes do not switch an installed client, daemon or worker.

Buddy installs Python dependencies with uv from a frozen lock (PyPI `c-two==0.6.0` and PyYAML; Python `>=3.12,<3.15`). The service and its workers execute from a **content-addressed stable runtime** outside the plugin cache, so replacing the plugin does not disturb a running service.

- **A cold start installs the runtime automatically.** With no READY runtime, the first command that needs the service copies the runtime assets declared by `packaging/runtime-assets.json` into `BUDDY_RUNTIME_ROOT/<contentId>`, runs `uv sync --frozen --no-dev --python 3.12` there, rewrites environment paths and writes `READY.json` last. Credentials, user data, tests, `node_modules`, the React source and any existing virtual environment are never copied. The first start can therefore take noticeably longer.
- **Manual materialization is preinstallation**, useful before shipping a bundle or on a machine that should not install at first use:

  ```sh
  uv run --frozen python -c "from buddy import runtime; runtime.materialize()"
  uv run --frozen buddy runtime
  ```

- **`READY` only means installed.** `health` reports `runtimeIdentity`, `runtimeContentId` and `runtimeStable`; `stable` is true only when the running process actually imports `buddy` from that runtime with no path leaking back into a plugin cache or the checkout. `runtime` prints the same identity plus a source-leak report.
- **An explicit `BUDDY_RUNTIME` pin always wins**, including over the runtime matching the current assets, so a pin left over from an older release keeps running that older code. Check `runtimeContentId` after every upgrade; leave the pin unset for automatic selection.
- **`BUDDY_DEV_SOURCE=1` suppresses automatic materialization for source tests.** It uses the checkout when no READY runtime has been selected; an explicit `BUDDY_RUNTIME` pin still takes precedence, so use a private `BUDDY_RUNTIME_ROOT` and check the reported identity when testing source.
- **Explicit `worker-start` uses the same runtime selection.** Starting an additional supervisor from a staged plugin launcher selects or materializes a READY runtime, uses its Python and package imports, and aligns its Python bridge and environment paths with that runtime. Existing supervisors keep the runtime they already loaded. An attempt's `runtimeIdentity` records the runtime actually executing its Worker.
- **Changing any hashed asset changes the runtime.** `packaging/runtime-assets.json`, `pyproject.toml`, `uv.lock`, `bin/buddy`, `src/buddy` (including the console build) and `harnesses/dsh/scripts` and `harnesses/dsh/plugins` are hashed, so a code or dependency change produces a new content-addressed directory on the next cold start. A daemon already running keeps its own runtime and code until it is restarted or stopped.
- **The daemon and every client process apply Buddy's private C-Two profile before their first `register`/`connect`** (`buddy.rpc_config.configure_server()`/`configure_client()`). The shared-memory pool is bounded to two 16 MiB segments instead of the default four 256 MiB segments, reassembly to two 16 MiB segments with a 16 MiB reassembled-payload ceiling, and the server callback capacity to 64 — the released C-Two maximum — because the C-Two default of 10 is below Buddy's 32 admitted waits and delayed an ordinary control call by 7.4 s in a measured run. The profile goes in through C-Two's public Python overrides inside the Buddy process: it is never written to the environment, never inherited by a coding child (including work on C-Two itself) and never applied to another C-Two project. The existing Buddy process-local profile is retained for 0.6.0 and checked through Buddy regressions; this release makes no new claim about C-Two internals. `buddy.rpc_config.report()` publishes only the whitelisted overrides and the bounds they imply; a configured capacity is a ceiling, not RSS, and mapped shared memory and resident memory are separate measurements.
- **A named C-Two surface change advances `CONTRACT_VERSION`.** Use the new package's `upgrade` for a coordinated idle cutover. This release keeps schema 12 and has no startup conversion. The old stable runtime remains the rollback target; the launcher talks to each version through its own interpreter and named interface.
- **`restart` is the detach without cancellation.** It writes a resume file, returns, and preserves every independent worker; the next autostart-capable CLI call starts a fresh daemon (which reconciles existing attempts). Use it when the host should move to new code but owned work must survive. It is not a schema upgrade.
- **`stop` is the explicit "cancel owned work and stop" operation**, not a required cache-refresh step: it cancels queued tasks, writes durable cancel intent for active attempts, drains for a bounded interval and reports `unresolvedAttempts`. Its internal workflow path fences governed roots with in-flight execution or routing and their owned descendants, recording `service-stop` without borrowing a Host capability or relaxing public authorization. Passive delivered and awaiting-Host roots with no in-flight work remain unchanged. Cancellation intent commits before draining; only actual Worker receipts establish shutdown. It cooperatively stops every supervisor recorded in the daemon-managed pool; an independently named supervisor started with `worker-start` needs its own exact-ID `worker-stop`.
- **Verify after any upgrade:** start a new Codex task so the upgraded plugin skill is loaded, then use its bundled launcher to check that `health` and `runtime` report the expected identity and stability before new work is delegated.
- **A mismatched database is refused, never migrated at startup.** The runtime accepts schema 12 only; another version, an unreadable board or failed integrity/foreign-key checks refuse startup and preserve the existing archive. An explicitly planned retained-data upgrade must prepare and verify a separate offline copy before activation; never relabel an unverified database or discard the original.

For a source installation (`runtimeStable: false`), keep its checkout available while work is active. Complete that work before replacing the source, or deliberately cancel it and inspect the shutdown result.

## Fresh board and the previous archive

A fresh installation uses `~/.local/share/hey-my-buddy/state` as its board. An older board directory at the previous default location (`~/.local/share/hey-my-buddy`) is retained as an archive: this release does not read, convert, import or rewrite it, and it contains no importer, legacy-record path or migration command. Switch to the current layout only after owned work has concluded with real shutdown evidence; keep the archive until its records are no longer needed.

Tests and previews always use separate private state and runtime roots. Never point a development run at the daily board, and never overwrite a live board with an archived copy.

## Workspace bridge

`workspace: true` (the default) groups the DSH session through a bundled host plugin; it controls session grouping only, while a governed task's `executionWorkspace` is a separate checkout/worktree ownership contract. Grouped runs require the bridge to be installed and its dsh profile running; there is no silent fallback to an ungrouped run. Run the installer from the plugin or repository root, the directory that contains `bin/buddy`.

```sh
node harnesses/dsh/scripts/install-workspace-bridge.mjs
```

- The installer defaults to `--profile web` and appends one entry to that profile's `cordis.patch.yml`, keeping existing text and writing a private backup first. Use `--home`, `--profile` or `--workspace-socket` for another existing long-lived profile.
- The bridge serves an owner-private Unix socket, by default `$DSH_HOME/deepseek-delegate/workspace.sock` (`~/.dsh` when `DSH_HOME` is unset). There is no Web URL, browser token or HTTP dependency.
- Long-lived profiles hot-reload the user patch; otherwise start `dsh web` normally.
- Before a model run, the runner checks that the bridge answers and resolves the canonical cwd. If it is missing, unusable or misconfigured, the run fails with an explicit error (exit 2) instead of running ungrouped. Use `workspace: false` to opt out deliberately.
- A stale socket left by an unclean exit is **refused, never replaced**. Verify the old host process has stopped, then remove that socket yourself and restart the profile.
- The bridge never activates an Agent; it validates the run's persisted root session and cwd, calls the official workspace API inside the owning host, and verifies membership. Task outcome and grouping outcome stay separately visible.

Recover binding without rerunning a task, using a `workspace.sessionId` from a failed binding or a known completed session:

```sh
node harnesses/dsh/scripts/run.mjs --cwd /path/to/project --attach-session SESSION_ID
```

This verifies the session exists before adopting it, runs no model, and does not scan or bulk-reassign history. The stored session cwd must equal the canonical workspace path.

## Private state and environment

All authoritative state is local SQLite under `BUDDY_STATE_DIR` (default `~/.local/share/hey-my-buddy/state`, mode `0700`):

- `board.sqlite3` — tasks, attempts, workers, messages, artifacts, events, command receipts, resource claims and the evaluation/workflow/gate tables; `health` runs an integrity check;
- `control.json` — the private endpoint address and service token (never group/world readable);
- `control-daemon.lock`, `board-owner.lock` — lifetime ownership locks: one daemon per state directory;
- `controls/<runId>.g<generation>.json`, `submissions/` — CLI-private Host control files and submission-recovery tokens (`0600`);
- `workers/<workerId>/` — supervisor lock and status, cooperative stop/retirement requests, startup intent and the receipt spool;
- `worker-pool.json` — exact daemon-managed worker IDs; similarly named custom workers are not inferred as members;
- `attempts/<runId>/<attemptId>/` — task text, turn input/output, spawn intent/marker, adapter logs, the bounded native-activity sidecar, per-attempt inquiry credentials and, for ZCode/Codex, the provider snapshot or native controller files and finish bridge; the Claude candidate adds its native controller files and `claude-private/settings.json` here, with an empty MCP configuration passed inline;
- `harnesses/zcode/<taskHash>/` — private native ZCode session database, storage and session bindings;
- `decisions/<decisionId>/` — private decision workspace;
- `worker.log`, `control.log`, `runtime-install.log`, `restart.resume.json`, `stop.request.json`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BUDDY_CONSOLE_PORT` | `49637` | stable loopback console port; private tests explicitly use `0` |
| `BUDDY_STATE_DIR` | `~/.local/share/hey-my-buddy/state` | state directory |
| `BUDDY_RUNTIME_ROOT` | `~/.local/share/hey-my-buddy/runtime` | parent of content-addressed runtimes |
| `BUDDY_RUNTIME` | unset | pin one READY runtime directory; an explicit pin always wins |
| `BUDDY_DEV_SOURCE` | unset | suppress automatic materialization; an already selected READY runtime still wins |
| `BUDDY_MAX_CONCURRENT` | `8` | machine-wide concurrent-attempt ceiling shared by routing and execution, clamped 1–32 |
| `BUDDY_WAIT_CAPACITY` | `32` | admitted waits on the dedicated wait resource |
| `BUDDY_LEASE_SECONDS` | `120` | attempt lease, clamped 15–3600 |
| `BUDDY_WORKER_ID` | `local` | pool prefix: this ID, then `<prefix>-2`, `<prefix>-3`, up to the machine-wide ceiling |
| `BUDDY_NODE` / `BUDDY_RUNNER_PATH` | unset | DSH adapter node binary / runner entrypoint overrides |
| `BUDDY_DECISION_HELPER` | unset | executable override for the bounded decision helper |
| `BUDDY_ZCODE_CLI` | unset | ZCode CLI path when it is not on `PATH` (the macOS bundle default is used otherwise) |
| `BUDDY_CLAUDE_SETTINGS_POLICY` | `isolated` | User-approved Claude P1 default: private settings, empty setting sources and strict MCP. Explicit empty or unsupported values are refused before execution; see [claude.md](claude.md) and its installation boundary |
| `BUDDY_CLAUDE_CLI` | `claude` on `PATH` | Claude Code executable override; discovery is initialize-only and never sends a user message |
| `BUDDY_AGENT_CREDENTIAL` / `BUDDY_AGENT_CREDENTIAL_FILE` | set by the adapter | attempt-scoped CLI credential: restricted to its own run; Host operations are denied and it never falls back to the administrator token |
| `BUDDY_TASK_ID`, `BUDDY_ATTEMPT_ID`, `BUDDY_TASK_FILE`, `BUDDY_LOG_DIR` | set for child processes | attribution and paths passed to `command` children |
| `BUDDY_PYTHON` | unset | interpreter the DSH YAML bridge uses to reach `buddy.yaml_bridge` |
| `BUDDY_DEBUG` | unset | include exception detail in `INTERNAL_ERROR` |
| `UV_BIN` | `uv` on `PATH` | uv binary used for runtime installs |

The launcher also honours `PLUGIN_DATA` when the Codex plugin host provides it, placing the uv environment under that directory (`UV_PROJECT_ENVIRONMENT`) so the plugin cache stays disposable.

A request ID is an idempotency key: retry the same input and ID after an uncertain start; changed input is rejected. Results survive service restart. Attempts whose shutdown was never confirmed stay `uncertain`/`reconciliation-needed` with their claims retained, are never replayed, and refuse a retry until the worker that owns the process handle reports an observed outcome. No stored PID is used to signal old processes.

**Permissions.** A CLI invocation runs with the invoking shell's permissions, and the launcher grants no extra privilege; an already-running daemon or worker keeps the permissions it was started with, so attaching from another task does not re-sandbox it. Attaching to an already healthy service does not change state-directory permissions or create daemon locks/logs. The launcher may still prepare its uv environment; cold startup also writes under the state and runtime roots.

## Cancellation and recovery

- Ending a CLI wait (Ctrl-C, closed terminal, `waitSeconds` expiry) cancels only the wait. The durable task keeps running while a worker owns it and is recovered by the same `runId` or `requestId`; recovery replays the identical request and never launches a second adapter.
- `cancel` fences a governed goal and its complete owned descendant graph, cancels open requests, pending continuations and route work, and records cancellation separately from aggregate stop evidence. `execution-cancel` cancels an ordinary execution record: queued work immediately, a durable cancel request for an active attempt. The worker that owns the child observes that intent and stops its own process group. Unrelated dsh sessions are unaffected.
- A daemon restart marks every in-flight attempt `uncertain` with its resource claims retained, and busy workers `lost`. The legitimate worker keeps its child process, optional positive deadline and receipt, and reattaches by attempt identity, generation, nonce and its own worker process instance; nothing is reattached by PID. A durable completion receipt is replayed before any reconciliation, so a finished attempt is never returned to executing, and a different worker process that merely reuses the worker id is refused. A confirmed reattach restores the retained resource claims and clears the restart waiting reason when the task still carries it.
- **Unknown is not stopped.** An expired lease or a missing PID never proves a process ended. `execution-retry` is refused while the previous attempt's shutdown is unconfirmed (`SHUTDOWN_UNCONFIRMED`), and only the worker holding the process handle can report an observed outcome or release an attempt that never spawned. A governed goal is continued with explicit new input or a complete routing configuration rather than a legacy retry.
- A worker replays its immutable completion receipt until the service confirms the result transaction; replaying that receipt does not execute the task again. Explicit retry requeues an eligible task; its next claim creates a new attempt generation. Previous acceptance is cleared from the task and remains readable in the event stream.
- If a wait returns `wait-timeout` or `unavailable`, keep monitoring the same run or report its `runId` explicitly; never relaunch and never describe the wait limit as an execution failure.

## Removed paths and cleanup

This release ships one skill (`skills/buddy/SKILL.md`), one launcher (`bin/buddy`) and no read-only dashboard, Node-record importer, migration command, MCP registration, handoff helper or compatibility facade. It never edits user harness configuration, and it leaves historical notification files untouched and unread. Only CLI and console reads deliver results now. Removed entrypoints are not recreated as forwarding stubs; do not run an old launcher against the current state directory.

### Storage wire shapes (0.16.0)

`storage_plan` accepts `{}` and returns `{planId, createdAt, expiresAt, categories, candidates, orphanProcesses}`. Each category is `{id, label, bytes, reclaimableBytes, count, eligibleCount, reasons}`; ids are `zcode`, `workspaces`, `runtimes`, `backup`, `durable`. Candidates are `{id, category, path, bytes, eligible, reasons}`. Reasons are stable strings; unknown reasons remain visible. Orphan process observations contain `{pid, kind, stateDir, runtimeDir}`; missing ownership data is null, never guessed. `storage_apply` accepts `{planId, commandId, confirm:true}` and returns `{planId, removedBytes, removed, skipped}`. Results are durably replayable by the same command identity; changed parameters conflict. Expiry is 15 minutes. A changed candidate is skipped with explicit reasons; browser reply loss preserves the command identity for safe replay. `buddy storage plan` and `buddy storage apply '<JSON>'` map to these named operations.

Legacy stable runtimes created before build provenance existed may have no source commit metadata. Their first upgrade backup records `pluginCommit: null` with `pluginCommitStatus: unavailable-in-source-metadata`, while retaining the exact runtime content identity and manifest hash; it never guesses a commit. Newly staged 0.16.0 packages embed `src/buddy/build-info.json`, and newly materialized runtimes carry the source commit in `READY.json`. Normal 0.16.0 service backups include that recorded commit.

Backup payloads also retain worker startup/orphan receipts and the exact worker-pool identity manifest. Online manual backup preserves the database and durable sidecars, but excludes native session homes and checkout contents; it is the upgrade rollback set, not a claim to recreate native sessions or Git repositories after total disk loss. Upgrade rollback leaves those excluded directories in place. A cancelled goal remains protected while the existing continuation contract can reopen it. Accepted managed checkout cleanup is queued after daemon-served Host acknowledgement and reuses the existing verified cleanup plan/apply protections; a crash or guard refusal leaves it available to storage plan.
