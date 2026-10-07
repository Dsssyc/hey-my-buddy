# Operations

How to install, run, recover and retire a hey-my-buddy installation. Command syntax and fields are in [cli.md](cli.md); internals are in [architecture.md](architecture.md); the practical path is in [usage.md](usage.md); harness discovery and recorded health are in [harnesses.md](harnesses.md). The supported distribution is one shared Agent Skill, `buddy`, that carries its own CLI (`scripts/buddy`), its references and the package the stable runtime is built from ([ADR-015](../decisions/015-shared-agent-skill-distribution.md)). Its installation entry, launcher, whole-generation switch and harness discovery follow [ADR-017](../decisions/017-local-installation-and-harness-discovery.md). There is no Codex plugin, no plugin marketplace and no project-level skill.

## Installed state

The recorded daily installation is [0.24.0/contract 0.24.0/schema 15](../acceptance/installed-0.24.0.md). The current source candidate is 0.25.0/contract 0.25.0/schema 15; source preparation does not install it, migrate the daily board, publish a release or change user settings. The shared skill lives in `~/.agents/skills/buddy`, with Claude Code reading the same directory through its link. Runtime, state and backup roots remain owned by `hey_my_buddy.home`.

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

`buddy install` first prints its `install-plan` and contract on stderr, with the same paths in the final result. It runs the read-only evidence preflight before preparation and before stopping the service; unsafe or unrecognized paths are `BACKUP_PREFLIGHT_FAILED`. It checks for queued/running/cancelling work and unresolved shutdown before replacing the skill, launcher, runtime or service; `UPGRADE_NOT_IDLE` lists that work and cancels nothing. Interrupted journals are recovered before beginning a new installation.

After target/link validation, the package materializes a content-addressed runtime and prepares the skill. On upgrade it fences admission, rechecks idle state, cooperatively detaches the old owner, takes and verifies `backups/current/` under exclusive locks, and migrates schema 14 to 15 while checking retained columns and unrelated table fingerprints. The new stable service must verify before the skill, launcher and active pointer are published. A failure restores the previous generation; an unprovable rollback keeps the journal with `UPGRADE_RECOVERY_REQUIRED`. On first install, the next service command performs the cold start.

The canonical skill is `~/.agents/skills/buddy`; Claude Code uses a symbolic link at `~/.claude/skills/buddy`, with no copy fallback. Foreign targets or conflicting links return `SKILL_TARGET_CONFLICT`. A denied link (for example Windows without Developer Mode) returns `CLAUDE_LINK_FAILED` or a coordinated rollback error; correct link permissions and rerun the fixed package. Installation reports `skill`, `claude`, `service`, `writePaths`, diagnostic guidance and any detected legacy plugin cache.

A complete, current installation can be rerun safely: `install` repairs what is missing (for example the Claude Code link) and returns `service: {"action": "none", "reason": "same complete generation"}` without restarting the service. Damaged or incomplete installed content is repaired by rerunning the same fixed-version package install command, which places a complete copy and re-verifies the generation; when the running installed copy is itself incomplete the launcher refuses with `SKILL_INVALID` and points at that command. A skill and runtime that came from different source generations are refused with `INSTALL_GENERATION_MISMATCH`. A retained-data upgrade needs a running service to hand over: with an existing board but no provable previous runtime to hand over from, `install` reports `UPGRADE_NO_ROLLBACK_RUNTIME` instead of replacing a live installation blindly. An interrupted upgrade journal is recovered by rerunning the same command under both launcher fences. While an installation is in progress, other launcher commands are fenced with `UPGRADE_IN_PROGRESS`. A Worker credential cannot install or upgrade; that is `UNAUTHORIZED`.

After installation or upgrade, start a new Host task so it loads the installed skill, then check the installed identity through that skill's own launcher:

```sh
~/.agents/skills/buddy/scripts/buddy health
~/.agents/skills/buddy/scripts/buddy runtime
~/.agents/skills/buddy/scripts/buddy adapters
```

`health` reports contract, schema, runtime identity and integrity; `runtime` reports the same identity plus a source-leak report; `adapters` reports one recorded health row per harness with the selected path, version and source, or the reason and repair advice. The current Host session can call the launcher's absolute path outside its sandbox; a new session loads the skill itself.

Requirements are macOS or Linux, [uv](https://docs.astral.sh/uv/) with Python 3.12–3.14 (the bootstrap scripts can place a fixed private uv when the machine has none), Node.js 20+ for the DSH runner, and a working local `dsh` and/or ZCode installation with its own provider credentials (or the Codex App Server with an existing account-plan login, which requires no API key). The `claude` P1 adapter additionally needs the installed Claude Code CLI with a first-party Anthropic account and stays unavailable until that CLI is authenticated. hey-my-buddy never writes global harness model settings.

Windows is not validated on a real machine. The code and scripts are portable — `install.ps1`, `buddy.cmd` and `buddy.ps1` exist, PATH is read from the registry — but symbolic-link creation, forced process termination and registry-based discovery still need native verification.

### Bootstrap scripts

`install.sh` (macOS, Linux) and `install.ps1` (Windows) do one job: prepare a fixed private `uv`, then hand installation to the package entry.

```sh
sh install.sh --version 0.19.0 [--wheel-url https://.../hey_my_buddy-0.25.0-py3-none-any.whl]
./install.ps1 -Version 0.19.0 [-WheelUrl https://.../hey_my_buddy-0.25.0-py3-none-any.whl]
```

They use an existing `uv` on `PATH`; otherwise they download the pinned uv 0.12.19 release into a private directory under the hey-my-buddy data home, verify it against the pinned SHA-256 list, and run `uv tool run --from <fixed package or wheel> hey-my-buddy install`. They print their private uv destination before downloading, then print the package source and delegate the remaining write-path plan to the installer. They never change `PATH`, shell configuration or Host settings, never need sudo, and never install a global `buddy` command. Stable error codes are `BOOTSTRAP_ARGS` (invalid version or wheel URL), `BOOTSTRAP_PLATFORM`, `BOOTSTRAP_FETCH`, `BOOTSTRAP_VERIFY`, `BOOTSTRAP_WRITE`, `BOOTSTRAP_EXTRACT` and `PACKAGE_INSTALL_FAILED`; each prints one line with a repair action.

### Source checkout

For development from a checkout, obtain the user's authorization and run the checkout's launcher, which uses uv:

```sh
/abs/path/to/hey-my-buddy/skills/buddy/scripts/buddy install
```

To hand out a built skill instead, build it and run that copy's own launcher:

```sh
uv run --frozen python packaging/build-skill.py --destination /path/to/dist/buddy
/path/to/dist/buddy/scripts/buddy install
```

`packaging/build-skill.py` writes `SKILL.md` (links rewritten to `references/`), `skill.json` (name, version, contract and source commit), `scripts/buddy`, the current references and `package/` with the declared runtime assets. It refuses tests, virtual environments, the React source and scratch content and replaces the destination by rename after verifying the inventory.

### Installation error codes

| Code | Meaning and repair |
| --- | --- |
| `BACKUP_PREFLIGHT_FAILED` | Evidence inventory contains unsafe, unrecognized or unconfirmed paths. Inspect the reported paths with `backup-preflight`; the service was not stopped for this refusal. |
| `UPGRADE_NOT_IDLE` | Queued/running/cancelling work or an unconfirmed shutdown blocks the switch; the reply lists the tasks. Wait for them to finish or cancel them yourself, then rerun install. Nothing was cancelled. |
| `UPGRADE_RECOVERY_REQUIRED` | An interrupted journal could not be recovered automatically; the journal and backup are retained. Rerun the same fixed-version install command, or inspect the recorded phase first. |
| `UPGRADE_NO_ROLLBACK_RUNTIME` | An existing board has no provable previous runtime to hand over from; recover that previous package before installing. |
| `UPGRADE_IN_PROGRESS` | Another install/upgrade holds the launcher fence; wait for it or rerun the same command to recover its journal. |
| `SKILL_TARGET_CONFLICT` | The skill directory or the Claude Code link is occupied by something else; move it away and rerun install. |
| `CLAUDE_LINK_FAILED` | The link could not be created (for example Windows without Developer Mode); fix permissions and rerun. A failed coordinated cutover restores the previous generation. |
| `SKILL_INVALID` | The installed copy itself is incomplete; rerun the fixed-version package install command. |
| `INSTALL_GENERATION_MISMATCH` | Skill and materialized runtime came from different source generations; rerun install from a single package. |
| `PACKAGE_INSTALL_USAGE` | The package only accepts `install`; rerun as `hey-my-buddy install`. |
| `UNAUTHORIZED` | A Worker credential tried to install or upgrade the owning service. |
| `ACTIVE_RUNTIME_MISSING` / `ACTIVE_RUNTIME_INVALID` / `ACTIVE_RUNTIME_UNAVAILABLE` | The active-runtime pointer is missing, unreadable or not READY. Rerun the fixed-version package install command; do not hand-edit the pointer. |
| `LAUNCH_ACCESS_DENIED` | A cold start needs state-directory writes and local IPC and the Host sandbox refuses them. See the launcher section below. |

## Launcher, sandbox and environment

`scripts/buddy` is the only CLI entry. `install` and `upgrade` run the package's own bootstrap (the skill launcher runs it with uv, while the published package runs it with its own interpreter); every ordinary daily command executes the Python of the runtime recorded in `active-runtime.json` directly, using the `.runtime-python` bootstrap hint, so daily calls do not go through uv. uv is also used explicitly for source development with `BUDDY_DEV_SOURCE=1`. A missing pointer is `ACTIVE_RUNTIME_MISSING`, an inconsistent one is `ACTIVE_RUNTIME_INVALID`, and a not-READY runtime is `ACTIVE_RUNTIME_UNAVAILABLE`; all three tell the user to rerun the fixed-version package install command.

A cold start must write the private state directory and open local IPC. If the Host sandbox refuses either, the launcher returns `LAUNCH_ACCESS_DENIED` instead of starting a service that inherits the sandbox limits: allow that exact absolute launcher command outside the sandbox once and retry, following your Host's own guide — [Codex](host-codex.md) or [Claude Code](host-claude-code.md) — and have the user or Host owner apply its setting; [skills/buddy/SKILL.md](../../skills/buddy/SKILL.md) only points here. Do not disable the sandbox globally, and do not change Host settings for the user. A service that is already running keeps its own permissions; attaching to it does not re-sandbox it.

On a Host entry the launcher clears leftover service-internal identity variables (`BUDDY_RUNTIME`, `BUDDY_RUNTIME_IDENTITY`, `BUDDY_WORKER_STATE`, `BUDDY_WORKER_ID`, `BUDDY_SUPERVISOR_START_ID`, `BUDDY_TASK_ID`, `BUDDY_ATTEMPT_ID`, `BUDDY_HARNESS_RECORD_FILE`) and third-party model endpoint overrides (Anthropic, OpenAI, Azure OpenAI, Gemini/Google GenAI, ZAI, ZCode, DeepSeek and OpenRouter base URLs or endpoint keys), so a cold-started service does not inherit the Host session's private endpoints. An attempt credential keeps Worker scope and its identity variables, because dropping them would turn a Worker into a service-token Host. User development variables such as `BUDDY_STATE_DIR`, `BUDDY_RUNTIME_ROOT` and `BUDDY_DEV_SOURCE` still apply.

Daemon cold starts, upgrade starts and worker supervisors inherit an explicit environment allowlist in `launcher.service_environment`. It retains system/home/path/temp/locale/proxy values, native account directories and named runtime settings; only explicit development mode adds named fixture/CLI overrides. Host session identities, `CLAUDE_CODE_*` tokens, provider endpoint/key overrides and attempt credentials are absent from service processes. Worker credentials remain attached to CLI calls so a Worker cannot become a Host; a scoped Worker cannot start or stop supervisors. Upgrade removes development/worker identity and selects its own stable interpreter.

Candidate builds use `uv build --wheel`. An sdist also embeds `src/hey_my_buddy/build-info.json`; its original `sourceCommit` takes precedence even when extracted inside another Git checkout. A same-version installation compares actual skill content and reports `updated` when bytes changed, rather than claiming `already-current` from version markers alone.

## Harness discovery and health

The service owns one recorded health row per supported harness — DSH, ZCode, Codex and Claude Code — instead of finding executables only through the environment the service was started from. [harnesses.md](harnesses.md) owns the full discovery, environment and cache contract; the steps below are the operational path.

```sh
buddy adapters '{}'                                  # read the recorded rows
buddy adapters '{"refresh":true}'                    # detect all harnesses now
buddy adapters '{"refresh":true,"adapter":"codex"}'  # detect one harness now
buddy harness set codex /absolute/path/to/codex      # advanced manual path
buddy harness set codex --auto                       # restore automatic detection
buddy harness-set '{"adapter":"codex","path":"/absolute/path/to/codex","expectedRevision":3}'
```

Discovery reads manual paths, version-manager default records (nvm, fnm, Volta, asdf, mise), known installation locations and app bundles, the Windows registry PATH and, with the lowest priority, the calling process's PATH. It never executes a shell configuration file and never reads or stores environment values, login output or account identities. Each candidate gets a bounded `--version` handshake and, where supported, a native login-status read; no model request is sent. The service builds every native child environment from an explicit allowlist, so provider API keys, model endpoint overrides and inherited Host authority are not passed, while each harness keeps its own native authentication.

An ordinary submission reads the recorded cache and does not run a slow probe; Host configuration queries, submissions and console reads may each borrow one rate-limited rescan, and no periodic scanner runs without a call. `ready` means the handshake passed and is the only available state; `missing`, `login-required`, `unhealthy` and `unknown` are unavailable and immediately exclude that harness's configurations from effective availability, enabling and routing. User enablement, preferences, family notes and cards stay intact, and a healthy change refreshes that harness's model metadata automatically. An explicitly named harness that the cache reports unavailable is rescanned once before the request is refused.

Before claiming queued native work and again before starting a harness, the service checks the executable fingerprint (real target, size, modification time and version-manager record). A proven failure before any model input permits one rediscovery and retry; a started model, cancellation or deadline does not. A running execution keeps its selected command, and a recorded harness-version change reconstructs a subsequent turn instead of trusting an old native session binding.

Stable operational codes are `HARNESS_NOT_CHECKED` (no check yet; run `buddy adapters '{"refresh":true}'`), `HARNESS_HANDSHAKE_FAILED` (the candidate CLI could not be probed; repair the native installation), `HARNESS_INVALID_RESULT` (discovery returned an unusable result) and `REVISION_CONFLICT` (a manual path or check changed since the caller read it; reread and retry). Each row also carries a bounded candidate reason code and one repair line, for example `not-found`, `login-required`, `node-missing`, `interpreter-missing`, `timeout`, `output-limit`, `auth-unverified`, `version-unknown`, `scan-timeout`, `shutdown-unverified` or `launch-failed`. A handshake that passes with unrecognized version text reports `unknown` plus a warning: it is never turned into a compatibility range. The manual path is an advanced setting for when automatic detection fails; the JSON form fences the write with `expectedRevision`, and a `null` path clears it.

The Router list and common mode/budget/retry interval are user-owned configuration. Each dispatch checks the selected harness's local mechanism eligibility without a paid certificate, then verifies its actual native policy, tool evidence, cumulative limits, frozen input and stop receipts. Codex/Claude retain their existing native sandbox implementations. DSH and ZCode have no system sandbox; their restricted native-tool paths and paid-probe evidence are separately recorded in the ADR-021 module acceptance, with unapproved native checks explicitly marked unverified. No installer or ordinary read changes Router settings; explicit upgrade conversion is owned by L15.

## Backup, upgrade and storage contract

The 0.24.0 source implements ADR-019 decision 11 without changing schema 15. Backup, upgrade and reclamation journals remain outside SQLite; the previously authorized schema 14 → 15 migration still runs only during an idle upgrade after verified backup.

`buddy backup '{}'` uses SQLite online backup and retains one verified generation at `backups/current/`. Its manifest records the actual board schema, contract, runtime identity, source commit, database fingerprints and SHA-256 for every payload. It retains controls, submissions, selected durable Worker receipts and state records. Native homes, accounts, workspaces, caches and process locks are outside this payload. Integrity, foreign keys, hashes and a private trial open precede publication; a schema-14 backup also proves the existing migration on its trial copy. Publication builds `.incoming`, preserves current on failure, and uses atomic exchange on POSIX or the recoverable publication journal on Windows.

Attempt payloads use the exact `attempt-evidence-v1` whitelist in `src/hey_my_buddy/protocol/attempt_evidence.py`, maintained alongside the writing adapters and invariant tests. It admits task and turn input/output, spawn and harness-selection records, structured results, routing input/output, bounded activity and usage, inquiry journals, declared nonsecret controls and logs. DSH run logs/capture and tool-free call request/result records have explicit container and filename rules. It never admits arbitrary JSON, controller signing keys, agent/inquiry credentials, provider snapshots, native homes, sessions or temporary drafts. [The design inventory](../design/private-directories.md) classifies every current writer and retained old layout.

A non-whitelisted file, link or special entry is skipped. A non-whitelisted directory counts as one skipped tree, including empty trees, and is never traversed. `skippedAttemptEntries` in the manifest records the total and at most 20 state-relative paths; `attemptEvidencePolicy` records the applied whitelist. A declared evidence path or container that is a link, reparse point, special file or wrong file/directory type refuses with `BACKUP_UNSAFE_PATH`, including its relative path and reason. This replaces the 0.21.0 position exclusions and fallback skipping of unsafe evidence.

```sh
buddy backup-preflight '{}'
buddy help backup-preflight
```

`backup-preflight` reads local source inventory without service attachment, autostart, database initialization, backup verification, locks or writes. It accepts only `{}` apart from CLI-local output options. It returns `policy`, `ok` (no refused entry, and a relocation plan that covers every skipped entry with nothing blocked), `needsAttention` (any skipped or refused entry), `legacyPlan`, and `copied`, `skipped`, `rejected`, each `{count, paths, entries:[{path,reason}]}` with at most 20 samples. `legacyPlan` mirrors the installer's admission: `ready`, the plan's action `planCount`, `uncovered` (skipped entries no exact stopped plan binds, reason `not-in-relocation-plan`) and `blocked` (plan refusals such as `shutdown-unconfirmed`), both with full counts and at most 20 `{path,reason}` samples. Building it reads `board.sqlite3` read-only and never creates the SQLite sidecar files. `copied` includes the database path that online backup will snapshot, not its live WAL files. Accounts and harness private trees are outside inventory and are not listed in the manifest. A missing fresh state root returns empty inventory and is not created. A Worker credential cannot invoke this Host-local read. `health.backupPreflight` and the console snapshot carry the same current report, and the console displays skipped/refused paths and reasons; no model call or repair is performed by reading it.

Run the new package's preflight before requesting installation authorization. The installer runs the same policy before preparing or detaching the service, and upgrade repeats the read after its admission fence immediately before detach. `BACKUP_PREFLIGHT_FAILED` names paths and stops before cutover for unsafe evidence, unrecognized skipped content, changed identities or unconfirmed native stop; the report's `ok` is false in exactly these situations, so a `backup-preflight` with `ok:false` names the entries an installation would refuse. Precisely recognized old private entries may pass only when a read-only relocation plan binds them to existing task/attempt identities and proves every relevant native process stopped; they remain visible as planned legacy cleanup in the upgrade result. Every skipped entry is checked, including those beyond the 20 displayed samples. No process is cancelled to make an upgrade possible.

After the backup is verified and while exclusive board/daemon locks are held, upgrade moves stopped old private homes, sessions and Router roots from attempts into the corresponding `harnesses/<adapter>/goals/<sha256(taskId)>/attempts/<attemptId>/`, deletes stopped credentials and provider snapshots, and moves old Codex/ZCode goal homes into `goals/<hash>/native/`. It retains ordinary evidence at its original paths. Unknown-stop attempts stay untouched and block installation; storage plan lists their protected private state. `privateMigration` reports moved, deleted and credential-cleanup counts plus at most 20 path samples. An interrupted journal binds every move to fixed old/new identities; rollback reverses session moves for the previous runtime, while already-stopped credentials stay deleted. Credential links are unlinked without reading their targets. The runtime switches only after backup and relocation succeed; data fingerprints and the new stable runtime are then verified under the existing rollback contract.

`harnesses/<adapter>/goals/<hash>/native/` owns the goal's resumable native state and bindings. Its `attempts/<attemptId>/` owns private settings, provider snapshots, credentials, Router homes, frozen review mirrors and tool-free private roots. `harnesses/<adapter>/accounts/worker/` is reserved for B batch phase two and remains protected; this batch adds no account, secret-input or login operation. Attempt credentials, credential links and provider snapshots are deleted only after actual native and controller stop evidence, including failed/cancelled outcomes. Unknown stop retains them. Short Unix inquiry sockets may use an owner-private temporary socket directory when the state path cannot fit the native socket bound; the credentials remain in the harness private area, the evidence journal remains in attempts, and a confirmed stop removes the exactly bound temporary socket directory.

`buddy storage plan '{}'` inventories all harness private roots, managed workspaces, runtimes, backup and durable state. It protects unknown ownership/stop, available continuation/recovery, pending descendants/requests and independent accounts. A plan is private, expiring and fingerprint-bound. `storage apply` requires the exact confirmed plan and rechecks every condition; changed candidates are skipped. Accepted managed-workspace cleanup reuses its ownership, immutable output/integration, dependency and Git checks, then reclaims that allocation's owned goal private roots and retained sessions. A removed checkout with interrupted private cleanup can replay its existing cleanup plan; recovery never guesses a different worktree. Durable records and the current backup remain protected.

Whole native goal homes can also be reclaimed after accepted ownership, confirmed self/descendant shutdown, permanently refused continuation, settled requests and the existing three-day grace period. A cancelled goal still supports continuation under the current contract and remains protected. The service never prunes guessed individual ZCode cache files or retains only a binding while deleting its native session store. Host-owned and unrelated checkouts are not candidates. Storage observations of orphan processes never authorize stopping them.

Backup, restore, relocation, storage and cleanup neither traverse links/reparse points nor write through them. Windows junctions and every other `FILE_ATTRIBUTE_REPARSE_POINT` are treated as links; their guards have private fixtures, while Windows hardware behavior is unverified. Process-interruption backup publication is tested on POSIX; Windows power-loss directory durability is unverified. Source acceptance, actual daily-board relocation and authorized installation are separate facts.

## Runtime lifecycle

The [installed 0.18.0 runtime](../acceptance/installed-0.18.0.md) uses contract 0.18.0 and schema 13. It was switched from the idle 0.17.0 service with one verified rolling backup and an in-place schema-12 → 13 migration under the exclusive owner locks; retained table fingerprints were verified, and the recorded runtime identity and preserved configuration are installation facts, distinct from source acceptance. New Codex tasks load the updated skill; existing Hosts must resolve the new bundled launcher before dispatching work against the upgraded service.

Current source contract is 0.20.0, schema 14 and C-Two 0.6.0. The 0.19.0 candidate implements [ADR-017](../decisions/017-local-installation-and-harness-discovery.md): the fixed-version package install entry, a launcher that executes the active runtime's Python directly, the sandbox refusal and inherited-environment cleaning, schema-14 harness health, and the idle whole-generation switch with one verified backup. It is not installed: the daily 0.18.0 installation above stays unchanged until the user separately authorizes the upgrade, and the daily Router is still unverified. The source verifies Codex Router on macOS; see [Codex](codex.md) for the tested profile and boundary. `ping` is the lightweight attachment check; `health` performs explicit current diagnostics. Source changes do not switch an installed client, daemon or worker.

hey-my-buddy installs Python dependencies with uv from a frozen lock (PyPI `c-two==0.6.0` and PyYAML; Python `>=3.12,<3.15`). The service and its workers execute from a **content-addressed stable runtime** outside the skill directory, so replacing the skill does not disturb a running service.

- **Installation materializes the runtime.** The installer copies declared assets into `BUDDY_RUNTIME_ROOT/<contentId>`, runs `uv sync --frozen --no-dev --python 3.12`, and writes `READY.json` last. Daily installed cold starts follow `active-runtime.json` without uv; a missing or damaged runtime requires rerunning the fixed-version installer. Credentials, data, tests, node_modules and pre-existing virtual environments are excluded.
- **Manual materialization is preinstallation**, useful before shipping a bundle or on a machine that should not install at first use:

  ```sh
  uv run --frozen python -c "from buddy import runtime; runtime.materialize()"
  BUDDY_DEV_SOURCE=1 uv run --frozen python -m hey_my_buddy.cli.main runtime
  ```

- **`READY` only means installed.** `health` reports `runtimeIdentity`, `runtimeContentId` and `runtimeStable`; `stable` is true only when the running process actually imports `buddy` from that runtime with no path leaking back into the skill package or the checkout. `runtime` prints the same identity plus a source-leak report.
- **Host and Worker selection differ.** The Host launcher removes residual internal runtime/Worker pins and provider endpoints before following the active pointer. A genuine attempt credential keeps Worker authority. Source tests use explicit `BUDDY_DEV_SOURCE=1` and private state/runtime roots; internal runtime clients keep their deliberate pin. The launcher preserves development paths and never drops a Worker credential to gain Host authority.

- **Explicit `worker-start` uses the same runtime selection.** Starting an additional supervisor from a skill launcher selects the active READY runtime, uses its Python and package imports, and aligns its Python bridge and environment paths with that runtime. Existing supervisors keep the runtime they already loaded. An attempt's `runtimeIdentity` records the runtime actually executing its Worker.
- **Changing any hashed asset changes the runtime.** `packaging/runtime-assets.json`, `packaging/hatch_build.py`, `pyproject.toml`, `uv.lock` and `src/hey_my_buddy` (including the console build) are hashed, so a code or dependency change produces a new content-addressed directory on the next authorized installation. A running daemon keeps its generation until a coordinated cutover.
- **The daemon and every client process apply hey-my-buddy's private C-Two profile before their first `register`/`connect`** (`hey_my_buddy.protocol.rpc_config.configure_server()`/`configure_client()`). The shared-memory pool is bounded to two 16 MiB segments instead of the default four 256 MiB segments, reassembly to two 16 MiB segments with a 16 MiB reassembled-payload ceiling, and the server callback capacity to 64 — the released C-Two maximum — because the C-Two default of 10 is below the blackboard's 32 admitted waits and delayed an ordinary control call by 7.4 s in a measured run. The profile goes in through C-Two's public Python overrides inside each hey-my-buddy process: it is never written to the environment, never inherited by a coding child (including work on C-Two itself) and never applied to another C-Two project. The existing hey-my-buddy process-local profile is retained for 0.6.0 and checked through hey-my-buddy regressions; this release makes no new claim about C-Two internals. `hey_my_buddy.protocol.rpc_config.report()` publishes only the whitelisted overrides and the bounds they imply; a configured capacity is a ceiling, not RSS, and mapped shared memory and resident memory are separate measurements.
- **A named C-Two surface change advances `CONTRACT_VERSION`.** Use the new package's `install` (or its `upgrade`) for a coordinated idle cutover. This release keeps schema 14 and has no startup conversion; the upgrade itself migrates an idle schema-13 board after its verified backup. The old stable runtime remains the rollback target; the launcher talks to each version through its own interpreter and named interface. The 0.29.0 source adds two internal Worker operations and two live contracts, so a service, its Workers and their controllers must all run 0.29.0; a peer declaring 0.28.0 is refused with a contract mismatch, checked in both directions between isolated C-Two peers.
- **`restart` is the detach without cancellation.** It writes a resume file, returns, and preserves every independent worker; the next autostart-capable CLI call starts a fresh daemon (which reconciles existing attempts). Use it when the host should move to new code but owned work must survive. It is not a schema upgrade.
- **`stop` is the explicit "cancel owned work and stop" operation**, not a required cache-refresh step: it cancels queued tasks, writes durable cancel intent for active attempts, drains for a bounded interval and reports `unresolvedAttempts`. Its internal workflow path fences governed roots with in-flight execution or routing and their owned descendants, recording `service-stop` without borrowing a Host capability or relaxing public authorization. Passive delivered and awaiting-Host roots with no in-flight work remain unchanged. Cancellation intent commits before draining; only actual Worker receipts establish shutdown. It cooperatively stops every supervisor recorded in the daemon-managed pool; an independently named supervisor started with `worker-start` needs its own exact-ID `worker-stop`.
- **Verify after any upgrade:** start a new Host task so the installed skill is loaded, then use its `scripts/buddy` to check that `health`, `runtime` and `adapters` report the expected identity, stability and harness health before new work is delegated.
- **A mismatched database is refused, never migrated at startup.** The runtime accepts schema 14 only; another version, an unreadable board or failed integrity/foreign-key checks refuse startup and preserve the existing archive. The one in-place migration is an idle schema-13 board inside `upgrade`, after that upgrade's verified backup and under the exclusive locks; it is never performed by startup. Startup also neither migrates nor populates harness health records. Backup verification exercises the migration on a private trial copy before activation; never relabel an unverified database or discard the original.

For a source installation (`runtimeStable: false`), keep its checkout available while work is active. Complete that work before replacing the source, or deliberately cancel it and inspect the shutdown result.

## Fresh board and the previous archive

A fresh installation uses `~/.local/share/hey-my-buddy/state` as its board. An older board directory at the previous default location (`~/.local/share/hey-my-buddy`) is retained as an archive: this release does not read, convert, import or rewrite it, and it contains no importer, legacy-record path or migration command. Switch to the current layout only after owned work has concluded with real shutdown evidence; keep the archive until its records are no longer needed.

Tests and previews always use separate private state and runtime roots. Never point a development run at the daily board, and never overwrite a live board with an archived copy.

## DSH session privacy

Every DSH execution writes its session rollout under an execution-private root through the per-run launch patch; the credentials store and settings document stay with the owning DSH home and are only named by path. The product session-grouping feature — the submission `workspace` switch, the workspace bridge plugin and installer and `worker-sessions` DSH listing/archiving — is removed by [ADR-021](../decisions/021-router-buddy-planes-and-routing-evidence.md). A `workspace` field on a submission is rejected as unknown. Sessions grouped before the removal stay wherever the native harness keeps them: archive them once in DSH itself if desired; this product no longer reads or writes them.

## Worker session history

`buddy worker-sessions '{"adapter":"codex"}'` reads only board-proven native identities; it does not open user harness stores or call a native harness. It labels user-store and goal-private Worker threads whose creation is proven by the governed native receipt; actual archive or deletion requires the user's separate confirmation and Codex's native interface. A `dsh` adapter request is `UNSUPPORTED`: DSH sessions are execution-private and their grouped listing and archiving were removed with the grouping feature; only `action: "list"` exists.

This upgrade leaves historical Codex threads in place. Historical cleanup is a separate explicit post-installation Host action. Ordinary old-layout private files still relocate only after verified backup under decision 11's existing journal, validation and rollback. Any future upgrade that reorganizes native history must run after `backup.verify`, retain exact reversible identities and validate rollback before claiming completion; merely changing a default cannot authorize native history changes.

Ordinary Codex execution and eligible native continuation share a goal-private `CODEX_HOME`; the selected file-based login is linked only in that private area and removed after confirmed native and controller stop. Unknown stop retains it. The Worker-account consumption seam is described in [codex.md](codex.md); this slice does not create accounts or change login configuration.

## Private state and environment

All authoritative state is local SQLite under `BUDDY_STATE_DIR` (default `~/.local/share/hey-my-buddy/state`, mode `0700`):

- `board.sqlite3` — tasks, attempts, workers, messages, artifacts, events, command receipts, resource claims and the evaluation/workflow/gate tables, plus the schema-14 `harness_health` rows; `health` runs an integrity check;
- `control.json` — the private endpoint address and service token (never group/world readable);
- `control-daemon.lock`, `board-owner.lock` — lifetime ownership locks: one daemon per state directory;
- `upgrade.json`, `upgrade-last.json`, `runtime-retention.json` — the install/upgrade journal, its last completed record and runtime-retention state;
- `controls/<runId>.g<generation>.json`, `submissions/` — CLI-private Host control files and submission-recovery tokens (`0600`);
- `workers/<workerId>/` — supervisor lock and status, cooperative stop/retirement requests, startup intent and the receipt spool;
- `worker-pool.json` — exact daemon-managed worker IDs; similarly named custom workers are not inferred as members;
- `attempts/<runId>/<attemptId>/` — declared ordinary evidence only: task/turn input/output, spawn and nonsecret controls, logs, activity, usage and inquiry journals;
- `harnesses/<adapter>/goals/<taskHash>/native/` — goal native state and continuation bindings; `attempts/<attemptId>/` beneath the same goal holds private homes, credentials, snapshots and Router roots; `harnesses/<adapter>/accounts/worker/` is reserved for later independent accounts;
- `decisions/<decisionId>/` — private routing workspace; the Router materializes its read-only frozen input mirror here;
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
| `BUDDY_NODE` | unset | Node.js binary used to start a ZCode CLI that is installed as a JavaScript entry file |
| `BUDDY_ZCODE_CLI` | unset | ZCode CLI path when it is not on `PATH` (the macOS bundle default is used otherwise) |
| `BUDDY_CLAUDE_SETTINGS_POLICY` | `isolated` | User-approved Claude P1 default: private settings, empty setting sources and strict MCP. Explicit empty or unsupported values are refused before execution; see [claude.md](claude.md) and its installation boundary |
| `BUDDY_CLAUDE_CLI` | `claude` on `PATH` | Claude Code executable override; discovery is initialize-only and never sends a user message |
| `BUDDY_AGENT_CREDENTIAL` / `BUDDY_AGENT_CREDENTIAL_FILE` | set by the adapter | attempt-scoped CLI credential: restricted to its own run; Host operations are denied and it never falls back to the administrator token |
| `BUDDY_TASK_ID`, `BUDDY_ATTEMPT_ID`, `BUDDY_TASK_FILE`, `BUDDY_LOG_DIR` | set for child processes | attribution and paths passed to `command` children |
| `BUDDY_DEBUG` | unset | include exception detail in `INTERNAL_ERROR` |
| `UV_BIN` | `uv` on `PATH` | uv binary used for runtime installs and by the bootstrap scripts |

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

This release ships one skill (`skills/buddy`, built by `packaging/build-skill.py`), one launcher (its `scripts/buddy`) and no read-only dashboard, Node-record importer, migration command, MCP registration, handoff helper or compatibility facade. It never edits user harness configuration, and it leaves historical notification files untouched and unread. Only CLI and console reads deliver results now. Removed entrypoints are not recreated as forwarding stubs; do not run an old launcher against the current state directory.

### Storage wire shapes

`storage_plan` accepts `{}` and returns `{planId, createdAt, expiresAt, categories, candidates, orphanProcesses}`. Each category is `{id, label, bytes, reclaimableBytes, count, eligibleCount, reasons}`; ids are `harnesses`, `zcode` (retained old homes), `workspaces`, `runtimes`, `backup`, `durable`. Candidates are `{id, category, path, bytes, eligible, reasons}`. Reasons are stable strings; unknown reasons remain visible. Orphan process observations contain `{pid, kind, stateDir, runtimeDir}`; missing ownership data is null, never guessed. `storage_apply` accepts `{planId, commandId, confirm:true}` and returns `{planId, removedBytes, removed, skipped, complete}`. `removed` is an array of `{id, path, bytes}` records, and `skipped` is an array of `{id, path, reasons: string[]}` records; neither field is a count. Runtime entries with unsupported format, missing or unsafe READY markers are individually listed in `skipped` and do not abort the inventory or cleanup. A recognized early-format READY runtime with exact content/root/interpreter identity can be reclaimed after successful process and mapped-file inspection; current/previous generations and unknown usage remain protected. A completed result has `complete: true`, including a completed scan that reports protected or malformed entries as skipped. `STORAGE_INCOMPLETE` means deletion already started and must be resumed with the same planId/commandId; its durable receipt remains replayable after plan expiry. A fresh plan must not discard that unresolved operation. Results are durably replayable by the same command identity; changed parameters conflict. Expiry is 15 minutes. A changed candidate is skipped with explicit reasons; browser reply loss preserves the command identity for safe replay. `buddy storage plan` and `buddy storage apply '<JSON>'` map to these named operations.

Legacy stable runtimes created before build provenance existed may have no source commit metadata. Their first upgrade backup records `pluginCommit: null` with `pluginCommitStatus: unavailable-in-source-metadata`, while retaining the exact runtime content identity and manifest hash; it never guesses a commit. Packages staged from a current source tree embed `src/hey_my_buddy/build-info.json`, and newly materialized runtimes carry the source commit in `READY.json`. Normal service backups include that recorded commit.

Backup payloads also retain worker startup/orphan receipts and the exact worker-pool identity manifest. Online manual backup preserves the database and durable sidecars, but excludes native session homes and checkout contents; it is the upgrade rollback set, not a claim to recreate native sessions or Git repositories after total disk loss. Upgrade rollback leaves those excluded directories in place. A cancelled goal remains protected while the existing continuation contract can reopen it. Accepted managed checkout cleanup is queued after daemon-served Host acknowledgement and reuses the existing verified cleanup plan/apply protections; a crash or guard refusal leaves it available to storage plan.

The bundled launcher bootstraps before loading C-Two contracts. `upgrade` always executes the new package's code; ordinary commands follow the verified `active-runtime.json` pointer (or the first legacy control identity), using that runtime's own interpreter/client. A verified rollback updates the pointer to the previous version, so the newly installed launcher remains usable with the restored daemon. `health` reports the active runtime; package metadata and active contract may differ after a rollback. Worker credentials survive this dispatch and never become Host authority.

Upgrade captures the live machine concurrency and wait capacity rather than inheriting unrelated defaults from the upgrading terminal. These values are verified after cutover/rollback and saved in private `launch-settings.json` as defaults for later cold starts; an explicit operator environment value takes precedence on a normal cold start. A successfully bound configured console port is saved in private `console-settings.json`, so restarting from another terminal retains its URL. Port collision reaches the CLI as `CONSOLE_PORT_IN_USE` with an actionable configuration message. These files and the active runtime pointer are backup sidecars, outside schema 14.

On Windows, backup publication uses a durable journal and fixed `.incoming`, `.previous` and `current` siblings. Recovery verifies a whole generation before discarding another copy. Process-interruption states have POSIX filesystem tests; native Windows behavior and directory-rename durability through power loss are not established.

Legacy plugin retirement, when still needed and separately authorized, uses `codex plugin remove hey-my-buddy@<marketplace>` after identifying the installed marketplace. The package installer only reports legacy locations; it never removes another Host installation automatically.

A non-default state directory cannot reclaim runtimes from the default state's runtime root; such candidates retain `runtime-owned-by-default-state`. Private installations/tests must set both state and runtime roots. Each state's retention record cannot authorize removal of another state's rollback runtime.

Windows process ownership uses a suspended child assigned to a private Job Object before resuming its primary thread; shutdown is observed through that held Job rather than a stored PID. The implementation uses documented Win32 Job/Thread APIs and Python 3.12+ nonblocking pipe support ([Python os.set_blocking](https://docs.python.org/3.13/library/os.html#os.set_blocking)). Native Windows tests remain a separate acceptance step; unsupported process/mapped-file inspection retains storage instead of reclaiming it.

In PowerShell, invoke `buddy.ps1` directly for JSON commands. It preserves argument boundaries using `ProcessStartInfo.ArgumentList` where available and explicit native quoting on legacy Windows PowerShell; `buddy.cmd` is the Command Prompt bridge. The quoting paths have a PowerShell-on-macOS round-trip smoke, with Windows shell behavior still awaiting native acceptance.

## Routing configuration upgrade

ADR-018 adds only `meta` values: `router_fast_profile_id`, `router_review_profile_id`, `router_default_mode` and `router_configuration_version`; `router_budget_preset` remains the review budget key. After backup and under the upgrade locks, the old `evaluation_state.decision_profile_id` is mapped to its eligible slot (verified review first, otherwise fast), the other slot stays empty and a saved `quick` becomes `brief`. This operation is idempotent, checks unrelated meta values and retained table fingerprints, and participates in the existing rollback. Fresh boards start with two empty slots and default `fast`. Startup does not migrate an existing board.

## Routing diagnostics and quota recovery (0.25.0)

Paid review-check evidence is retired with its executor. Ordinary Router results retain their native policy/tool stream, usage, frozen-input verification and actual stop evidence under the existing private attempt lifecycle; no review certificate is read or issued. Old certificate data is left for explicit upgrade cleanup.

For native exhaustion with no reset time, use `buddy quota-redetect '{"requestId":"quota-check-1","adapter":"dsh","provider":"deepseek-official"}'` to allow one later selected call to retry now. Reuse the requestId after a lost reply; a fresh request intentionally opens a new chance after consumption. The command performs no native query or model call and does not assert that the balance recovered. Otherwise one chance opens after an hour; only its selected configuration consumes it. Known reset times, explicit Host choices and newer available/unknown observation rules retain their existing behavior.

The native review verifier judges the model tool stream structurally, without parsing or interpreting model output, and proves the boundary categories solely through five controller-owned `command/exec` challenges under the same private `buddy-router` profile, including the live network positive control. Fixed challenge binding, denied native results, fixture snapshots, structurally complete allowed tool streams, budget and confirmed stop are required. `review-evidence.json` retains the sanitized v3 proof; legacy v1 replay establishes observations without inventing missing boundary categories, and a passing replay never certifies. [Native sandbox proof](../design/native-sandbox-review-proof.md) owns details and the remaining live-verification boundary.

## Worker account permissions and recovery (0.26.0)

Independent account content lives only at `harnesses/<adapter>/accounts/worker/`, with private ancestors/directories (0700) and ordinary credential files (0600). Symlinks, reparse points and shared hard-linked credential files are refused. Backup inventory and storage reclamation exclude/protect these roots; macOS system-store entries never become backup items. A restored state needs fresh independent credentials. Native shared login is managed by the person in the native tool.

A credential mutation starts with a durable nonsecret reservation, refuses active/uncertain credential users and performs native I/O outside the SQLite transaction. Confirmed stop completes the credential generation and invalidates old account observations; uncertainty retains the reservation and content. `account-status` never supplies an authorization link. If the original owner is gone, do not infer stop from age or PID, do not retry a mutation or delete its contents, and retain that reservation for verified owner recovery. Upgrade refuses active/uncertain account reservations before cutover. Shared native source remains selectable without modifying the retained independent credentials.

The approved macOS system-store exception uses direct Security.framework calls with authentication UI forbidden. An unavailable store fails as `ACCOUNT_SECRET_STORE_UNAVAILABLE`, with no file/environment fallback. Only the actual native Claude child receives the selected key. Host launchers and test subprocesses clear inherited `BUDDY_ACCOUNT_SELECTION`; an attempt controller carries only the trusted nonsecret marker. Secret input, login links and native raw account errors are never operation audit payloads.
