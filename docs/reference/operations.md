# Operations

How to install, run, recover and retire a hey-my-buddy installation. Command syntax and fields are in [cli.md](cli.md); internals are in [architecture.md](architecture.md); the practical path is in [usage.md](usage.md); harness discovery and recorded health are in [harnesses.md](harnesses.md). The supported distribution is one shared Agent Skill, `hey-my-buddy`, that carries its own CLI (`scripts/hey-my-buddy`), its references and the package the stable runtime is built from ([ADR-015](../decisions/015-shared-agent-skill-distribution.md)). Its installation entry, launcher, whole-generation switch and harness discovery follow [ADR-017](../decisions/017-local-installation-and-harness-discovery.md). There is no Codex plugin, no plugin marketplace and no project-level skill.

## Installed state

The recorded daily installation is [0.36.0/contract 0.36.0/schema 20](../acceptance/install-0.36.0.md); the current source is 0.37.0 (contract 0.37.0, schema 21) and is not installed yet; source preparation does not install anything, upgrade the daily board, publish a release or change user settings. The shared skill lives in `~/.agents/skills/hey-my-buddy`, with Claude Code reading the same directory through its link. Runtime, state and backup roots remain owned by `hey_my_buddy.home`.

## Installation

The source candidate installs through one fixed-version package command. The command below assumes that the package name is available and that the version has been published; neither has been decided yet, so treat it as a post-publication command and do not claim that it works today or that anything can be installed from PyPI now. Until the release channel is decided, build the wheel from the frozen source and install from its absolute path:

```sh
# After the user decides the release channel and the version is published
uvx hey-my-buddy@0.25.0 install

# Unpublished candidate: build a wheel and install from its absolute path
uv build --wheel --out-dir dist
uvx --from /absolute/path/hey_my_buddy-0.25.0-py3-none-any.whl hey-my-buddy install
```

The package exposes exactly one command, `hey-my-buddy install`; any other argument returns `PACKAGE_INSTALL_USAGE`. Run the command outside the Host or agent sandbox, in a real user session; it needs no sudo and writes only inside the user's home. Installing or switching the daily service still requires the user's separate authorization.

`hey-my-buddy install` first prints its `install-plan` and contract on stderr, with the same paths in the final result. It runs the read-only evidence preflight before preparation and before stopping the service; unsafe declared evidence paths are `BACKUP_PREFLIGHT_FAILED`. It checks for queued/running/cancelling work and unresolved shutdown before replacing the skill, launcher, runtime or service; an archive upgrade also requires open micro tasks to be accepted or concluded and managed checkouts reclaimed; `UPGRADE_NOT_IDLE` lists that work and cancels nothing. Interrupted journals are recovered before beginning a new installation.

After target/link validation, the package materializes a content-addressed runtime and prepares the skill. On upgrade it fences admission, rechecks the preconditions, cooperatively detaches the old owner, and takes and verifies a backup of the stopped board under exclusive locks. A board that already has the current schema is kept as it is; a board with the upgrade base's schema 20 is converted to the current schema by the explicit upgrade after the verified backup; no other schema is accepted. The new stable service must verify before the skill, launcher and active pointer are published. A failure restores the previous generation; an unprovable rollback keeps the journal with `UPGRADE_RECOVERY_REQUIRED`. On first install, the next service command performs the cold start.

The canonical skill is `~/.agents/skills/hey-my-buddy`; Claude Code uses a symbolic link at `~/.claude/skills/hey-my-buddy`, with no copy fallback. Foreign targets or conflicting links return `SKILL_TARGET_CONFLICT`. A denied link (for example Windows without Developer Mode) returns `CLAUDE_LINK_FAILED` or a coordinated rollback error; correct link permissions and rerun the fixed package. Installation reports `skill`, `claude`, `service`, `writePaths`, diagnostic guidance and any detected legacy plugin cache.

A complete, current installation can be rerun safely: `install` repairs what is missing (for example the Claude Code link) and returns `service: {"action": "none", "reason": "same complete generation"}` without restarting the service. Damaged or incomplete installed content is repaired by rerunning the same fixed-version package install command, which places a complete copy and re-verifies the generation; when the running installed copy is itself incomplete the launcher refuses with `SKILL_INVALID` and points at that command. A skill and runtime that came from different source generations are refused with `INSTALL_GENERATION_MISMATCH`. A retained-data upgrade needs a running service to hand over: with an existing board but no provable previous runtime to hand over from, `install` reports `UPGRADE_NO_ROLLBACK_RUNTIME` instead of replacing a live installation blindly. An interrupted upgrade journal is recovered by rerunning the same command under both launcher fences. While an installation is in progress, other launcher commands are fenced with `UPGRADE_IN_PROGRESS`. A Worker credential cannot install or upgrade; that is `UNAUTHORIZED`.

After installation or upgrade, start a new Host task so it loads the installed skill, then check the installed identity through that skill's own launcher:

```sh
~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy health
~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy runtime
~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy harnesses
```

`health` reports contract, schema, runtime identity and integrity; `runtime` reports the same identity plus a source-leak report; `harnesses` reports one recorded health row per harness with the selected path, version and source, or the reason and repair advice. The current Host session can call the launcher's absolute path outside its sandbox; a new session loads the skill itself.

Requirements are macOS or Linux, [uv](https://docs.astral.sh/uv/) with Python 3.12–3.14 (the bootstrap scripts can place a fixed private uv when the machine has none), and a working local DSH, ZCode, Codex or Claude Code CLI with its own native user configuration and login. Node.js is needed when the selected ZCode CLI is a JavaScript entry file. hey-my-buddy never writes global harness model settings.

Windows is not validated on a real machine. The code and scripts are portable — `install.ps1`, `hey-my-buddy.cmd` and `hey-my-buddy.ps1` exist, PATH is read from the registry — but symbolic-link creation, forced process termination and registry-based discovery still need native verification.

### Bootstrap scripts

`install.sh` (macOS, Linux) and `install.ps1` (Windows) do one job: prepare a fixed private `uv`, then hand installation to the package entry.

```sh
sh install.sh --version 0.19.0 [--wheel-url https://.../hey_my_buddy-0.25.0-py3-none-any.whl]
./install.ps1 -Version 0.19.0 [-WheelUrl https://.../hey_my_buddy-0.25.0-py3-none-any.whl]
```

They use an existing `uv` on `PATH`; otherwise they download the pinned uv 0.12.19 release into a private directory under the hey-my-buddy data home, verify it against the pinned SHA-256 list, and run `uv tool run --from <fixed package or wheel> hey-my-buddy install`. They print their private uv destination before downloading, then print the package source and delegate the remaining write-path plan to the installer. They never change `PATH`, shell configuration or Host settings, never need sudo, and never install a global `hey-my-buddy` command. Stable error codes are `BOOTSTRAP_ARGS` (invalid version or wheel URL), `BOOTSTRAP_PLATFORM`, `BOOTSTRAP_FETCH`, `BOOTSTRAP_VERIFY`, `BOOTSTRAP_WRITE`, `BOOTSTRAP_EXTRACT` and `PACKAGE_INSTALL_FAILED`; each prints one line with a repair action.

### Source checkout

For development from a checkout, obtain the user's authorization and run the checkout's launcher, which uses uv:

```sh
/abs/path/to/hey-my-buddy/skills/hey-my-buddy/scripts/hey-my-buddy install
```

To hand out a built skill instead, build it and run that copy's own launcher:

```sh
uv run --frozen python packaging/build-skill.py --destination /path/to/dist/hey-my-buddy
/path/to/dist/hey-my-buddy/scripts/hey-my-buddy install
```

`packaging/build-skill.py` writes `SKILL.md` (links rewritten to `references/`), `skill.json` (name, version, contract and source commit), `scripts/hey-my-buddy`, the current references and `package/` with the declared runtime assets. It refuses tests, virtual environments, the React source and scratch content and replaces the destination by rename after verifying the inventory.

### Installation error codes

| Code | Meaning and repair |
| --- | --- |
| `BACKUP_PREFLIGHT_FAILED` | Evidence inventory contains refused paths, such as unsafe declared evidence. Non-whitelisted entries are skipped. Inspect the reported paths with `backup-preflight`; the service was not stopped for this refusal. |
| `UPGRADE_NOT_IDLE` | With a current-schema board, work that can still move on its own blocks the switch: running, cancelling or reconciliation-needed work, queued work a Worker can claim (a queued continuation and a Router call included), and an attempt that has not finished or whose stop is not confirmed. A micro task stopped at a Host boundary with no continuation pending does not block a current-schema switch; the archive upgrade also requires open micro tasks to be accepted or concluded and managed checkouts reclaimed. The reply lists the tasks and unresolved attempts. Wait for actual completion or owner recovery, then rerun install. Nothing was cancelled or declared stopped. |
| `UPGRADE_RECOVERY_REQUIRED` | An interrupted journal could not be recovered automatically; the journal and backup are retained. Rerun the same fixed-version install command, or inspect the recorded phase first. |
| `UPGRADE_NO_ROLLBACK_RUNTIME` | An existing board has no provable previous runtime to hand over from; recover that previous package before installing. |
| `UPGRADE_IN_PROGRESS` | An install or upgrade holds the fence. A Host command refused with this code recorded nothing: send the same command again with the same identity after the upgrade finishes. An `await` that spans the upgrade ends with this code and names the same run to wait on again. For the installer itself, wait for the other install or rerun the same command to recover its journal. |
| `SKILL_TARGET_CONFLICT` | The skill directory or the Claude Code link is occupied by something else; move it away and rerun install. |
| `CLAUDE_LINK_FAILED` | The link could not be created (for example Windows without Developer Mode); fix permissions and rerun. A failed coordinated cutover restores the previous generation. |
| `SKILL_INVALID` | The installed copy itself is incomplete; rerun the fixed-version package install command. |
| `INSTALL_GENERATION_MISMATCH` | Skill and materialized runtime came from different source generations; rerun install from a single package. |
| `PACKAGE_INSTALL_USAGE` | The package only accepts `install`; rerun as `hey-my-buddy install`. |
| `UNAUTHORIZED` | A Worker credential tried to install or upgrade the owning service. |
| `ACTIVE_RUNTIME_MISSING` / `ACTIVE_RUNTIME_INVALID` / `ACTIVE_RUNTIME_UNAVAILABLE` | The active-runtime pointer is missing, unreadable or not READY. Rerun the fixed-version package install command; do not hand-edit the pointer. |
| `LAUNCH_ACCESS_DENIED` | A cold start needs state-directory writes and local IPC and the Host sandbox refuses them. See the launcher section below. |

## Archive upgrade

The upgrade from schema 18 (written by 0.34.1) kept the old board as an archive, created a new board and carried only the settings over: the Router list and its settings, every buddy with its enabled state, preferences, model service notes and concurrency limits, and the harness health rows. It ran on the daily board when 0.35.0 was installed on 2026-10-14 ([record](../acceptance/install-0.35.0.md)), and the source no longer contains it: the upgrade base is now 0.35.0 (commit `4ca5344b`, schema 19).

The archive lives under `archives/<UTC time>-schema-18/` in the state directory: `board.sqlite3.gz` with the exact bytes of the stopped old board, the evidence, control and submission files under `state/`, and a `manifest.json` whose `archivedFrom` names the commit, version and schema that wrote it. No upgrade overwrites an archive, storage reclamation neither lists nor removes it, and deleting it is the user's decision. To look at it, copy it to another private directory, decompress the board, copy the archived `state/` beside it and open that directory with the runtime of `archivedFrom.sourceCommit`; the current version does not open it.

The old board also left Git references in every repository it worked in, under `refs/buddy/`: they pin the sealed outputs that the archived records name, so they belong to the archive. The upgrade never touches a user's repositories; the current version keeps its own references under `refs/hey-my-buddy/`, does not list the old ones, and storage reclamation does not remove them. When an archive is no longer wanted, delete them in each such repository with `git for-each-ref --format='delete %(refname)' refs/buddy/ | git update-ref --stdin`.

## Launcher, sandbox and environment

`scripts/hey-my-buddy` is the only CLI entry. `install` and `upgrade` run the package's own bootstrap (the skill launcher runs it with uv, while the published package runs it with its own interpreter); every ordinary daily command executes the Python of the runtime recorded in `active-runtime.json` directly, using the `.runtime-python` bootstrap hint, so daily calls do not go through uv. uv is also used explicitly for source development with `HMB_DEV_SOURCE=1`. A missing pointer is `ACTIVE_RUNTIME_MISSING`, an inconsistent one is `ACTIVE_RUNTIME_INVALID`, and a not-READY runtime is `ACTIVE_RUNTIME_UNAVAILABLE`; all three tell the user to rerun the fixed-version package install command.

A cold start must write the private state directory and open local IPC. If the Host sandbox refuses either, the launcher returns `LAUNCH_ACCESS_DENIED` instead of starting a service that inherits the sandbox limits: allow that exact absolute launcher command outside the sandbox once and retry, following your Host's own guide — [Codex](host-codex.md) or [Claude Code](host-claude-code.md) — and have the user or Host owner apply its setting; [skills/hey-my-buddy/SKILL.md](../../skills/hey-my-buddy/SKILL.md) only points here. Do not disable the sandbox globally, and do not change Host settings for the user. A service that is already running keeps its own permissions; attaching to it does not re-sandbox it.

On a Host entry the launcher clears leftover service-internal identity variables (`HMB_RUNTIME`, `HMB_RUNTIME_IDENTITY`, `HMB_WORKER_STATE`, `HMB_WORKER_ID`, `HMB_SUPERVISOR_START_ID`, `HMB_TASK_ID`, `HMB_ATTEMPT_ID`, `HMB_HARNESS_RECORD_FILE`) and third-party model endpoint overrides (Anthropic, OpenAI, Azure OpenAI, Gemini/Google GenAI, ZAI, ZCode, DeepSeek and OpenRouter base URLs or endpoint keys), so a cold-started service does not inherit the Host session's private endpoints. An attempt credential keeps Worker scope and its identity variables, because dropping them would turn a Worker into a service-token Host. User development variables such as `HMB_STATE_DIR`, `HMB_RUNTIME_ROOT` and `HMB_DEV_SOURCE` still apply.

Daemon cold starts, upgrade starts and worker supervisors inherit an explicit environment allowlist in `launcher.service_environment`. Today the service and every harness process it starts keep the environment of the command that started the service, so a changed variable takes effect once the service has been started again; proxies reach a harness through that environment or the harness's own configuration. The `PATH` it passes on is the caller's without the virtual environment of the process that builds it: an install runs inside the built skill's own virtual environment, and the service it starts does not keep that directory at the front of its `PATH`. It retains system/home/path/temp/locale/proxy values, native account directories and named runtime settings; only explicit development mode adds named fixture/CLI overrides. Host session identities, `CLAUDE_CODE_*` tokens, provider endpoint/key overrides and attempt credentials are absent from service processes. Worker credentials remain attached to CLI calls so a Worker cannot become a Host; a scoped Worker cannot start or stop supervisors. Upgrade removes development/worker identity and selects its own stable interpreter.

Candidate builds use `uv build --wheel`. An sdist also embeds `src/hey_my_buddy/build-info.json`; its original `sourceCommit` takes precedence even when extracted inside another Git checkout. A same-version installation compares actual skill content and reports `updated` when bytes changed, rather than claiming `already-current` from version markers alone.

## Harness discovery and health

The service owns one recorded health row per supported harness — DSH, ZCode, Codex and Claude Code — instead of finding executables only through the environment the service was started from. [harnesses.md](harnesses.md) owns the full discovery, environment and cache contract; the steps below are the operational path.

```sh
hey-my-buddy harnesses '{}'                                  # read the recorded rows
hey-my-buddy harnesses '{"refresh":true}'                    # detect all harnesses now
hey-my-buddy harnesses '{"refresh":true,"harness":"codex"}'  # detect one harness now
hey-my-buddy harness set codex /absolute/path/to/codex      # advanced manual path
hey-my-buddy harness set codex --auto                       # restore automatic detection
hey-my-buddy harness-set '{"harness":"codex","path":"/absolute/path/to/codex"}'
```

Discovery reads manual paths, version-manager default records (nvm, fnm, Volta, asdf, mise), known installation locations and app bundles, the Windows registry PATH and, with the lowest priority, the calling process's PATH. It never executes a shell configuration file and never reads or stores environment values, login output or account identities. Each candidate gets a bounded public version probe or native initialize handshake and, where supported, a native login-status read; no model request is sent. The service builds every native child environment from an explicit allowlist, so provider API keys, model endpoint overrides and inherited Host authority are not passed, while each harness keeps its own native authentication.

An ordinary submission reads the recorded cache and does not run a slow probe; Host buddy queries, submissions and console reads may each borrow one rate-limited rescan, and no periodic scanner runs without a call. `ready` means the handshake passed and is the only available state; `missing`, `login-required`, `unhealthy` and `unknown` are unavailable and immediately exclude that harness's buddies from availability, enabling and routing. User enablement, preferences and model service notes stay intact, and a healthy change refreshes that harness's model metadata automatically. An explicitly named harness that the cache reports unavailable is rescanned once before the request is refused.

Before claiming queued native work and again before starting a harness, the service checks the executable fingerprint (real target, size, modification time and version-manager record). A proven failure before any model input permits one rediscovery and retry; a started model, cancellation or deadline does not. A running execution keeps its selected command, and a recorded harness-version change reconstructs a subsequent turn instead of trusting an old native session binding.

Stable operational codes are `HARNESS_NOT_CHECKED` (no check yet; run `hey-my-buddy harnesses '{"refresh":true}'`), `HARNESS_HANDSHAKE_FAILED` (the candidate CLI could not be probed; repair the native installation) and `HARNESS_INVALID_RESULT` (discovery returned an unusable result). Each row also carries a bounded candidate reason code and one repair line, for example `not-found`, `login-required`, `node-missing`, `interpreter-missing`, `timeout`, `output-limit`, `auth-unverified`, `version-unknown`, `scan-timeout`, `shutdown-unverified` or `launch-failed`. A handshake that passes with unrecognized version text reports `unknown` plus a warning: it is never turned into a compatibility range. The manual path is an advanced setting for when automatic detection fails; in the JSON form a `null` path clears it.

The Router list and common mode/budget/retry interval are user-owned configuration. Each dispatch checks the selected harness's declared local mechanism eligibility, then verifies its actual native policy, tool evidence, cumulative limits, frozen input and stop receipts. Codex/Claude retain their native sandbox implementations. DSH and ZCode have no system sandbox; their restricted native-tool paths are recorded separately, with unverified native checks named in the acceptance records. No installer or ordinary read changes Router settings; the archive upgrade carries them over.

## Backup, upgrade and storage contract

Backup and reclamation journals remain outside SQLite. The pending schema-18 upgrade archives history and creates a new board with only settings after the verified backup.

`hey-my-buddy backup '{}'` uses SQLite online backup and retains one verified generation at `backups/current/`. Its manifest records the actual board schema, contract, runtime identity, source commit, database fingerprints and SHA-256 for every payload. It retains controls, submissions, selected durable Worker receipts and state records. Native homes, accounts, workspaces, caches and process locks are outside this payload. Integrity, foreign keys, hashes and a private trial open precede publication; a current-schema trial is also opened through the current database validator. Publication builds `.incoming`, preserves current on failure, and uses atomic exchange on POSIX or the recoverable publication journal on Windows.

Attempt payloads use the exact `attempt-evidence-v1` whitelist in `src/hey_my_buddy/protocol/attempt_evidence.py`, maintained alongside the writing adapters and invariant tests. It admits task and turn input/output, spawn and harness-selection records, structured results, routing input/output, bounded activity and usage, inquiry journals, declared nonsecret controls and logs. DSH run logs/capture and tool-free call request/result records have explicit container and filename rules. It never admits arbitrary JSON, controller signing keys, agent/inquiry credentials, provider snapshots, native homes, sessions or temporary drafts. [The design inventory](../design/private-directories.md) classifies every current writer and retained old layout.

A non-whitelisted file, link or special entry is skipped. A non-whitelisted directory counts as one skipped tree, including empty trees, and is never traversed. `skippedAttemptEntries` in the manifest records the total and at most 20 state-relative paths; `attemptEvidencePolicy` records the applied whitelist. A declared evidence path or container that is a link, reparse point, special file or wrong file/directory type refuses with `BACKUP_UNSAFE_PATH`, including its relative path and reason. This replaces the 0.21.0 position exclusions and fallback skipping of unsafe evidence.

```sh
hey-my-buddy backup-preflight '{}'
hey-my-buddy help backup-preflight
```

`backup-preflight` reads local source inventory without service attachment, autostart, database initialization, backup verification, locks or writes. It accepts only `{}` apart from CLI-local output options. It returns `policy`, `ok` (no refused entry), `needsAttention` (any skipped or refused entry), and `copied`, `skipped`, `rejected`, each `{count, paths, entries:[{path,reason}]}` with at most 20 samples. `copied` includes the database path that online backup will snapshot, not its live WAL files. Accounts and harness private trees are outside inventory and are not listed in the manifest. A missing fresh state root returns empty inventory and is not created. A Worker credential cannot invoke this Host-local read. `health.backupPreflight` and the console snapshot carry the same current report, and the console displays skipped/refused paths and reasons; no model call or repair is performed by reading it.

Run the new package's preflight before requesting installation authorization. The installer runs the same policy before preparing or detaching the service, and upgrade repeats the read after its admission fence immediately before detach. `BACKUP_PREFLIGHT_FAILED` names paths and stops before cutover for refused entries such as unsafe evidence paths; the report's `ok` is false in exactly this situation, so a `backup-preflight` with `ok:false` names the entries an installation would refuse. Skipped entries are left where they are. No process is cancelled to make an upgrade possible.

After the backup is verified and while exclusive board/daemon locks are held, the archive upgrade archives the old board and its declared history files, creates the new board with only settings and removes the archived history from the live state directory. It performs no private-directory relocation. Unknown-stop attempts block installation. A failed switch restores the board and its archived files before starting the previous runtime.

`harnesses/<adapter>/micro-tasks/<hash>/native/` owns a micro task's resumable state and bindings where its harness provides them. Its `attempts/<attemptId>/` owns run controls and logs, frozen Router review mirrors and tool-free working directories. Harness configuration and login stay in the user's native directories. Attempt credentials are deleted only after actual native and controller stop evidence, including failed/cancelled outcomes. Unknown stop retains them.

`hey-my-buddy storage plan '{}'` inventories all harness private roots, managed workspaces, runtimes, backup and durable state. It protects unknown ownership/stop, available continuation/recovery, pending descendants/requests. A plan is private, expiring and fingerprint-bound. `storage apply` requires the exact confirmed plan and rechecks every condition; changed candidates are skipped. Accepted managed-workspace cleanup reuses its ownership, immutable output/integration, dependency and Git checks, then reclaims that allocation's owned micro task private roots and retained sessions. A removed checkout with interrupted private cleanup can replay its existing cleanup plan; recovery never guesses a different worktree. Durable records and the current backup remain protected.

Whole native micro task homes can also be reclaimed after accepted ownership, confirmed self/descendant shutdown, permanently refused continuation, settled requests and the existing three-day grace period. The runtime directory is cleaned the same way: a successful install removes its own lock file, and an unfinished runtime directory, a build directory or an orphaned lock file older than the grace period becomes a reclamation candidate only when its install lock can actually be taken; the runtime in use and the one retained for rollback are never candidates. A cancelled micro task still supports continuation under the current contract and remains protected. The service never prunes guessed individual ZCode cache files or retains only a binding while deleting its native session store. Host-owned and unrelated checkouts are not candidates. Storage observations of orphan processes never authorize stopping them.

Backup, restore, relocation, storage and cleanup neither traverse links/reparse points nor write through them. Windows junctions and every other `FILE_ATTRIBUTE_REPARSE_POINT` are treated as links; their guards have private fixtures, while Windows hardware behavior is unverified. Process-interruption backup publication is tested on POSIX; Windows power-loss directory durability is unverified. Source acceptance, actual daily-board archive upgrade and authorized installation are separate facts.

## Runtime lifecycle

The [installed runtime](../acceptance/install-0.34.1.md) is 0.34.1 with contract 0.34.1 and schema 18. It was installed on 2026-10-13 over an idle 0.34.0 service with one verified backup and no schema change; the schema 17 to 18 upgrade ran with [0.31.0](../acceptance/install-0.31.0.md) on 2026-10-12. The recorded runtime identity and the preserved configuration are installation facts, distinct from source acceptance.

The current source is contract 0.37.0, schema 21 and C-Two 0.7.4; the daily installation is 0.36.0 with schema 20. The upgrade base is 0.36.0 (commit `6b385e53`, schema 20); the source carries one pending upgrade from 20 to 21, which records who acted as a role and an identifier, derives the owner of a Lead's micro tasks from their ownership record, clears the request fingerprints written before it (replaying such a request is refused with `SUBMISSION_REPLAY_UNAVAILABLE`) and drops the schema-19 legacy starts; it accepts no older board. `ping` is the lightweight attachment check; `health` performs explicit current diagnostics. Source changes do not switch an installed client, daemon or worker.

hey-my-buddy installs Python dependencies with uv from a frozen lock (PyPI `c-two==0.7.4` and PyYAML; Python `>=3.12,<3.15`). The service and its workers execute from a **content-addressed stable runtime** outside the skill directory, so replacing the skill does not disturb a running service.

- **Installation materializes the runtime.** The installer copies declared assets into `HMB_RUNTIME_ROOT/<contentId>`, runs `uv sync --frozen --no-dev --python 3.12`, and writes `READY.json` last. Daily installed cold starts follow `active-runtime.json` without uv; a missing or damaged runtime requires rerunning the fixed-version installer. Credentials, data, tests, node_modules and pre-existing virtual environments are excluded.
- **Manual materialization is preinstallation**, useful before shipping a bundle or on a machine that should not install at first use:

  ```sh
  uv run --frozen python -c "from hey_my_buddy.install import runtime; runtime.materialize()"
  HMB_DEV_SOURCE=1 uv run --frozen python -m hey_my_buddy.cli.main runtime
  ```

- **`READY` only means installed.** `health` reports `runtimeIdentity`, `runtimeContentId` and `runtimeStable`; `stable` is true only when the running process actually imports `hey_my_buddy` from that runtime with no path leaking back into the skill package or the checkout. `runtime` prints the same identity plus a source-leak report.
- **Host and Worker selection differ.** The Host launcher removes residual internal runtime/Worker pins and provider endpoints before following the active pointer. A genuine attempt credential keeps Worker authority. Source tests use explicit `HMB_DEV_SOURCE=1` and private state/runtime roots; internal runtime clients keep their deliberate pin. The launcher preserves development paths and never drops a Worker credential to gain Host authority.

- **Explicit `worker-start` uses the same runtime selection.** Starting an additional supervisor from a skill launcher selects the active READY runtime, uses its Python and package imports, and aligns its Python bridge and environment paths with that runtime. Existing supervisors keep the runtime they already loaded. An attempt's `runtimeIdentity` records the runtime actually executing its Worker.
- **Changing any hashed asset changes the runtime.** `packaging/runtime-assets.json`, `packaging/hatch_build.py`, `pyproject.toml`, `uv.lock` and `src/hey_my_buddy` (including the console build) are hashed, so a code or dependency change produces a new content-addressed directory on the next authorized installation. A running daemon keeps its generation until a coordinated cutover.
- **The daemon and every client process apply hey-my-buddy's private C-Two profile before their first `register`/`connect`** (`hey_my_buddy.protocol.rpc_config.configure_server()`/`configure_client()`). The profile also selects where local endpoints live: on Unix the state directory's own `ipc` directory, mode 0700, created by the service and never by a client, and independent of an inherited `C2_IPC_ROOT`; no endpoint is placed in the machine-wide temporary directory any more. The C-Two shared-memory pool is disabled, so no segment stays mapped between calls and a large message uses a temporary transfer that is returned afterwards. Reassembly is bounded to two 16 MiB segments with a 16 MiB reassembled-payload ceiling. The server accepts 64 execution workers — the released C-Two maximum, because the default of 10 is below the blackboard's 32 admitted waits and delayed an ordinary control call by 7.4 s in a measured run — 256 pending requests, payloads up to 32 MiB and frames up to 16 MiB. The profile goes in through C-Two's public Python overrides inside each hey-my-buddy process: it is never written to the environment, never inherited by a coding child (including work on C-Two itself) and never applied to another C-Two project. The profile is checked through hey-my-buddy's own regressions; no claim is made about C-Two internals. `hey_my_buddy.protocol.rpc_config.report()` publishes only the whitelisted overrides and the bounds they imply; a configured capacity is a ceiling, not RSS, and mapped shared memory and resident memory are separate measurements.
- **A named C-Two surface change advances `CONTRACT_VERSION`.** Use the new package's `install` (or its `upgrade`) for a coordinated idle cutover. The pending archive upgrade runs only after its verified backup, never at startup. The old stable runtime remains the rollback target; the launcher talks to each version through its own interpreter and named interface. A service, its Workers and their controllers must use the same contract version; a mismatched peer is refused.
- **`restart` is the detach without cancellation.** It writes a resume file, returns, and preserves every independent worker; the next autostart-capable CLI call starts a fresh daemon (which reconciles existing attempts). Use it to restart the daemon while owned work survives; it keeps the installed runtime generation. It is not a schema upgrade.
- **`stop` is the explicit "cancel owned work and stop" operation**, not a required cache-refresh step: it cancels queued tasks, writes durable cancel intent for active attempts, drains for a bounded interval and reports `unresolvedAttempts`. Its internal workflow path fences micro task roots with in-flight execution or routing and their owned descendants, recording `service-stop` without borrowing a Host capability or relaxing public authorization. Passive delivered and awaiting-Host roots with no in-flight work remain unchanged. Cancellation intent commits before draining; only actual Worker receipts establish shutdown. It cooperatively stops every supervisor recorded in the daemon-managed pool; an independently named supervisor started with `worker-start` needs its own exact-ID `worker-stop`.
- **Verify after any upgrade:** start a new Host task so the installed skill is loaded, then use its `scripts/hey-my-buddy` to check that `health`, `runtime` and `harnesses` report the expected identity, stability and harness health before new work is delegated.
- **A mismatched database is refused, never migrated at startup.** The runtime accepts only the current schema; another version, an unreadable board or failed integrity/foreign-key checks refuse startup. Only the idle archive upgrade opens the upgrade-base board, after its verified backup and under the exclusive locks. Startup neither converts old data nor populates harness health records. Never relabel an unverified database or discard the original.

For a source installation (`runtimeStable: false`), keep its checkout available while work is active. Complete that work before replacing the source, or deliberately cancel it and inspect the shutdown result.

## Fresh board and the previous archive

A fresh installation uses `~/.local/share/hey-my-buddy/state` as its board. An older board directory at the previous default location (`~/.local/share/hey-my-buddy`) is retained as an archive: this release does not read, convert, import or rewrite it, and it contains no importer, legacy-record path or migration command. Switch to the current layout only after owned work has concluded with real shutdown evidence; keep the archive until its records are no longer needed.

Tests and previews always use separate private state and runtime roots. Never point a development run at the daily board, and never overwrite a live board with an archived copy.

## DSH session privacy

DSH Worker executions write session rollouts under the micro task's shared native root through the per-run launch patch; Router runs use their own attempt-private root; the credentials store and settings document stay with the owning DSH home and are only named by path. The product session-grouping feature — the submission `workspace` switch, the workspace bridge plugin and installer and `worker-sessions` DSH listing/archiving — is removed by [ADR-021](../decisions/021-router-buddy-planes-and-routing-evidence.md). A `workspace` field on a submission is rejected as unknown. Sessions grouped before the removal stay wherever the native harness keeps them: archive them once in DSH itself if desired; this product no longer reads or writes them.

## Worker session history

`hey-my-buddy worker-sessions '{"harness":"codex"}'` reads only board-proven native identities; it does not open user harness stores or call a native harness. It labels user-store and micro-task-private Worker threads whose creation is proven by the native receipt; actual archive or deletion requires the user's separate confirmation and Codex's native interface. A `dsh` harness request is `UNSUPPORTED`: DSH sessions are execution-private and their grouped listing and archiving were removed with the grouping feature; only `action: "list"` exists.

This upgrade leaves historical Codex threads in the user's harness store in place. Historical cleanup is a separate explicit post-installation Host action. It archives declared board history without relocating native session stores. Any future upgrade that reorganizes native history must run after `backup.verify`, retain exact reversible identities and validate rollback before claiming completion; merely changing a default cannot authorize native history changes.

After ADR-030 batch 3b, Codex Worker execution and eligible native continuation use Codex's own user directory, without credential links or a private configuration home. Old private-session receipts reconstruct new sessions; old private homes are retained pending a separate cleanup decision. Installed native verification remains pending.

## Private state and environment

All authoritative state is local SQLite under `HMB_STATE_DIR` (default `~/.local/share/hey-my-buddy/state`, mode `0700`):

- `board.sqlite3` — tasks, attempts, workers, messages, artifacts, events, command receipts, resource claims and the evaluation, workflow, Router and catalog tables, plus the `harness_health` rows; `health` runs an integrity check;
- `control.json` — the private endpoint address and service token (never group/world readable);
- `control-daemon.lock`, `board-owner.lock` — lifetime ownership locks: one daemon per state directory;
- `upgrade.json`, `upgrade-last.json`, `runtime-retention.json` — the install/upgrade journal, its last completed record and runtime-retention state;
- `controls/<runId>.g<generation>.json`, `submissions/` — CLI-private Host control files and submission-recovery tokens (`0600`);
- `workers/<workerId>/` — supervisor lock and status, cooperative stop/retirement requests, startup intent and the receipt spool;
- `worker-pool.json` — exact daemon-managed worker IDs; similarly named custom workers are not inferred as members;
- `attempts/<runId>/<attemptId>/` — declared ordinary evidence only: task/turn input/output, spawn and nonsecret controls, logs, activity, usage and inquiry journals;
- `harnesses/<adapter>/micro-tasks/<taskHash>/native/` — micro task native state and continuation bindings; `attempts/<attemptId>/` beneath the same micro task holds run controls, logs, credentials and Router working directories;
- Router review mirrors live inside the selected harness's attempt-private directory and are removed after verified unchanged input and confirmed stop;
- `worker.log`, `control.log`, `runtime-install.log`, `restart.resume.json`, `stop.request.json`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `HMB_CONSOLE_PORT` | `49637` | stable loopback console port; private tests explicitly use `0` |
| `HMB_STATE_DIR` | `~/.local/share/hey-my-buddy/state` | state directory |
| `HMB_RUNTIME_ROOT` | `~/.local/share/hey-my-buddy/runtime` | parent of content-addressed runtimes |
| `HMB_RUNTIME` | unset | pin one READY runtime directory; an explicit pin always wins |
| `HMB_DEV_SOURCE` | unset | suppress automatic materialization; an already selected READY runtime still wins |
| `HMB_MAX_CONCURRENT` | `8` | machine-wide concurrent-attempt ceiling shared by routing and execution, clamped 1–32 |
| `HMB_WAIT_CAPACITY` | `32` | admitted waits on the dedicated wait resource |
| `HMB_LEASE_SECONDS` | `120` | attempt lease, clamped 15–3600 |
| `HMB_WORKER_ID` | `local` | pool prefix: this ID, then `<prefix>-2`, `<prefix>-3`, up to the machine-wide ceiling |
| `HMB_NODE` | unset | development-only Node.js override for a ZCode JavaScript entry file; passed through only with `HMB_DEV_SOURCE=1` |
| `HMB_ZCODE_CLI` | unset | development-only ZCode CLI override, passed through only with `HMB_DEV_SOURCE=1`; ordinary selection uses recorded harness discovery |
| `HMB_CLAUDE_CLI` | unset | development-only Claude Code executable override, passed through only with `HMB_DEV_SOURCE=1`; ordinary discovery probes version and login without sending a user message |
| `HMB_AGENT_CREDENTIAL_FILE` | unset | path of an execution's private credential file, which also names its board; a Lead sets it on each launcher command to the path its role text names. The credential is restricted to its own run, a Lead's also to its owned micro tasks; Host operations are denied and it never falls back to the administrator token |
| `HMB_TASK_ID`, `HMB_ATTEMPT_ID`, `HMB_TASK_FILE`, `HMB_LOG_DIR` | set for child processes | attribution and paths passed to `command` children |
| `HMB_DEBUG` | unset | include exception detail in `INTERNAL_ERROR` |
| `UV_BIN` | `uv` on `PATH` | uv binary used for runtime installs and by the bootstrap scripts |

A request ID is an idempotency key: retry the same input and ID after an uncertain start; changed input is rejected. Results survive service restart. Attempts whose shutdown was never confirmed stay `uncertain`/`reconciliation-needed` with their claims retained, are never replayed, and refuse a retry until the worker that owns the process handle reports an observed outcome or the service settles them from stop evidence. A stored PID alone never signals an old process; only the full recorded identity does ([architecture](architecture.md)).

**Permissions.** A CLI invocation runs with the invoking shell's permissions, and the launcher grants no extra privilege; an already-running daemon or worker keeps the permissions it was started with, so attaching from another task does not re-sandbox it. Attaching to an already healthy service does not change state-directory permissions or create daemon locks/logs. Daily installed calls execute the selected runtime's interpreter directly; cold startup writes under the state root. Source development and installation use uv and may prepare a runtime.

## Cancellation and recovery

- Ending a CLI wait (Ctrl-C, closed terminal, `waitSeconds` expiry) cancels only the wait. The durable task keeps running while a worker owns it and is recovered by the same `runId` or `requestId`; recovery replays the identical request and never launches a second adapter.
- `cancel` fences a micro task and its complete owned descendant graph, cancels open requests, pending continuations and route work, and records cancellation separately from aggregate stop evidence. `execution-cancel` cancels an ordinary execution record: queued work immediately, a durable cancel request for an active attempt. The worker that owns the child observes that intent and stops its own process group. Unrelated dsh sessions are unaffected.
- A daemon restart marks every in-flight attempt `uncertain` with its resource claims retained, and busy workers `lost`. The legitimate worker keeps its child process, optional positive deadline and receipt, and reattaches by attempt identity, generation, nonce and its own worker process instance; nothing is reattached by PID. A durable completion receipt is replayed before any reconciliation, so a finished attempt is never returned to executing, and a different worker process that merely reuses the worker id is refused. A confirmed reattach restores the retained resource claims and clears the restart waiting reason when the task still carries it.
- **Unknown is not stopped.** An expired lease or a missing PID never proves a process ended. `execution-retry` is refused while the previous attempt's shutdown is unconfirmed (`SHUTDOWN_UNCONFIRMED`), and only the worker holding the process handle can report an observed outcome or release an attempt that never spawned. A micro task is continued with explicit new input or a complete buddy rather than a legacy retry.
- **Interruption.** After a power loss, a reboot, a crash or a killed Worker, the execution's recorded identity decides: a new boot session or an absent identity settles it as `interrupted`, and an orphan that is still running is stopped by a new Worker first. This needs no action and no restart: the next service start or lease sweep (the lease is 120 seconds by default) does it. The micro task then waits at its failure boundary with the unverified partial output, and the Host decides whether to `continue`; Codex CLI and DSH CLI resume the interrupted native session when its record proves where the turn stopped, and the others rebuild the session from the fixed continuation input. A sleeping machine or a lost network is not an interruption: the original Worker is still alive and reconciles as after a service restart.
- A worker replays its immutable completion receipt until the service confirms the result transaction; replaying that receipt does not execute the task again. Explicit retry requeues an eligible task; its next claim creates a new attempt generation. Previous acceptance is cleared from the task and remains readable in the event stream.
- If a wait returns `wait-timeout` or `unavailable`, keep monitoring the same run or report its `runId` explicitly; never relaunch and never describe the wait limit as an execution failure.

## Removed paths and cleanup

This release ships one skill (`skills/hey-my-buddy`, built by `packaging/build-skill.py`), one launcher (its `scripts/hey-my-buddy`) and no read-only dashboard, Node-record importer, migration command, MCP registration, handoff helper or compatibility facade. It never edits user harness configuration, and it leaves historical notification files untouched and unread. Only CLI and console reads deliver results now. Removed entrypoints are not recreated as forwarding stubs; do not run an old launcher against the current state directory.

### Storage wire shapes

`storage_plan` accepts `{}` and returns `{planId, createdAt, expiresAt, categories, candidates, orphanProcesses}`. Each category is `{id, label, bytes, reclaimableBytes, count, eligibleCount, reasons}`; ids are `harnesses`, `zcode` (retained old homes), `workspaces`, `runtimes`, `backup`, `durable`. Candidates are `{id, category, path, bytes, eligible, reasons}`. A managed checkout that no record on the board refers to, such as one left by a submission the board refused before this release, appears as a `workspaces` candidate with `orphan: true`; it is eligible only when its own allocation records, fixed Git references and lock prove it and no retained fact of any state names it, and applying removes the checkout and keeps those records. A directory whose checkout is already gone is not a candidate. Reasons are stable strings; unknown reasons remain visible. Orphan process observations contain `{pid, kind, stateDir, runtimeDir}`; missing ownership data is null, never guessed. `storage_apply` accepts `{planId, commandId, confirm:true}` and returns `{planId, removedBytes, removed, skipped, complete}`. `removed` is an array of `{id, path, bytes}` records, and `skipped` is an array of `{id, path, reasons: string[]}` records; neither field is a count. Runtime entries with unsupported format, missing or unsafe READY markers are individually listed in `skipped` and do not abort the inventory or cleanup. A recognized early-format READY runtime with exact content/root/interpreter identity can be reclaimed after successful process and mapped-file inspection; current/previous generations and unknown usage remain protected. A completed result has `complete: true`, including a completed scan that reports protected or malformed entries as skipped. `STORAGE_INCOMPLETE` means deletion already started and must be resumed with the same planId/commandId; its durable receipt remains replayable after plan expiry. A fresh plan must not discard that unresolved operation. Results are durably replayable by the same command identity; changed parameters conflict. Expiry is 15 minutes. A changed candidate is skipped with explicit reasons; browser reply loss preserves the command identity for safe replay. `hey-my-buddy storage plan` and `hey-my-buddy storage apply '<JSON>'` map to these named operations.

Legacy stable runtimes created before build provenance existed may have no source commit metadata. Their first upgrade backup records `pluginCommit: null` with `pluginCommitStatus: unavailable-in-source-metadata`, while retaining the exact runtime content identity and manifest hash; it never guesses a commit. Packages staged from a current source tree embed `src/hey_my_buddy/build-info.json`, and newly materialized runtimes carry the source commit in `READY.json`. Normal service backups include that recorded commit.

Backup payloads also retain worker startup/orphan receipts and the exact worker-pool identity manifest. Online manual backup preserves the database and durable sidecars, but excludes native session homes and checkout contents; it is the upgrade rollback set, not a claim to recreate native sessions or Git repositories after total disk loss. Upgrade rollback leaves those excluded directories in place. A cancelled micro task remains protected while the existing continuation contract can reopen it. Accepted managed checkout cleanup is queued after daemon-served Host acceptance and reuses the existing verified cleanup plan/apply protections; a crash or guard refusal leaves it available to storage plan.

The bundled launcher bootstraps before loading C-Two contracts. `upgrade` always executes the new package's code; ordinary commands follow the verified `active-runtime.json` pointer (or the first legacy control identity), using that runtime's own interpreter/client. A verified rollback updates the pointer to the previous version, so the newly installed launcher remains usable with the restored daemon. `health` reports the active runtime; package metadata and active contract may differ after a rollback. Worker credentials survive this dispatch and never become Host authority.

Upgrade captures the live machine concurrency and wait capacity rather than inheriting unrelated defaults from the upgrading terminal. These values are verified after cutover/rollback and saved in private `launch-settings.json` as defaults for later cold starts; an explicit operator environment value takes precedence on a normal cold start. A successfully bound configured console port is saved in private `console-settings.json`, so restarting from another terminal retains its URL. Port collision reaches the CLI as `CONSOLE_PORT_IN_USE` with an actionable configuration message. These files and the active runtime pointer are backup sidecars, outside SQLite.

On Windows, backup publication uses a durable journal and fixed `.incoming`, `.previous` and `current` siblings. Recovery verifies a whole generation before discarding another copy. Process-interruption states have POSIX filesystem tests; native Windows behavior and directory-rename durability through power loss are not established.

Legacy plugin retirement, when still needed and separately authorized, uses `codex plugin remove hey-my-buddy@<marketplace>` after identifying the installed marketplace. The package installer only reports legacy locations; it never removes another Host installation automatically.

A non-default state directory cannot reclaim runtimes from the default state's runtime root; such candidates retain `runtime-owned-by-default-state`. Private installations/tests must set both state and runtime roots. Each state's retention record cannot authorize removal of another state's rollback runtime.

Windows process ownership uses a suspended child assigned to a private Job Object before resuming its primary thread; shutdown is observed through that held Job rather than a stored PID. The implementation uses documented Win32 Job/Thread APIs and Python 3.12+ nonblocking pipe support ([Python os.set_blocking](https://docs.python.org/3.13/library/os.html#os.set_blocking)). Native Windows tests remain a separate acceptance step; unsupported process/mapped-file inspection retains storage instead of reclaiming it.

In PowerShell, invoke `hey-my-buddy.ps1` directly for JSON commands. It preserves argument boundaries using `ProcessStartInfo.ArgumentList` where available and explicit native quoting on legacy Windows PowerShell; `hey-my-buddy.cmd` is the Command Prompt bridge. The quoting paths have a PowerShell-on-macOS round-trip smoke, with Windows shell behavior still awaiting native acceptance.

## Routing diagnostics and quota recovery (0.25.0)

Paid review-check evidence is retired with its executor. Ordinary Router results retain their native policy/tool stream, usage, frozen-input verification and actual stop evidence under the existing private attempt lifecycle; no review certificate is read or issued. Historical evidence is part of the archive upgrade's declared payload.

A provider rate limit explicitly marked non-retryable also counts as quota exhaustion without a recovery time. For native exhaustion with no reset time, use `hey-my-buddy quota-redetect '{"requestId":"quota-check-1","harness":"dsh","provider":"deepseek-official"}'` to allow one later selected call to retry now. Reuse the requestId after a lost reply; a fresh request intentionally opens a new chance after consumption. The command performs no native query or model call and does not assert that the balance recovered. Otherwise one chance opens after an hour; only its selected buddy consumes it. Known reset times, explicit Host choices and newer available/unknown observation rules retain their existing behavior.





Discovery failures carry the native stop fact through the role controller and its caller. Codex includes private-directory preparation, command and environment selection, the public version probe and app-server creation in that boundary: a known failure before child creation confirms stop, while creation failures consume the creating side's actual receipt, including failures before handle registration. Runtime versions are diagnostic metadata: Claude Code, Codex and ZCode share one bounded public-probe implementation, while DSH reads its existing initialize handshake. Missing versions, nonzero version-command exits, timeouts and read errors with confirmed shutdown leave the optional runtime version unknown and permit the native run or catalog read to continue. Only unconfirmed version-process shutdown refuses subsequent native startup with `native-shutdown-failed`; its false stop receipt remains part of run collection and discovery errors even when the main process was never created. Optional runtime facts use `None` for an unknown version; display and catalog boundaries render `unknown`. An initialize protocol failure remains a native failure. The outer controller publishes a separate creation and cleanup receipt for itself: log-opening and preparation failures before creation confirm that no native child could have started, while a created controller requires both an explicit native stop receipt and confirmed outer shutdown. Missing, malformed or false native receipts stay unknown even after the controller group has been terminated; a native child may own a separate process group. Post-creation log-close failures terminate and observe the held controller group, and discovery combines that recorded fact with the bounded native receipt through the same public stop rule as ordinary collection. The shared log opener closes an already opened stdout FD if opening stderr fails, preserving the original opening error; command and controller launches use that one opener. Role directory creation and cleanup errors carry the same observed stop fact; cleanup failure cannot change a confirmed stop to unknown, and an unknown stop retains the directory. Health probes preserve `shutdown-unverified` over login or interpreter diagnostics, and unexpected probe errors carry their observed stop fact. Unknown shutdown never becomes stopped through an error code, lease, stored PID or missing evidence. Account-query keeps its existing bounded-reader stop contract. Private commands, real private FDs and model-free discovery controllers cover the new failure boundaries; Windows ownership failures use simulated API/process handles, so Windows hardware, live-endpoint sockets and daily-service behavior remain unverified here.
