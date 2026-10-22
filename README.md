<p align="right"><strong>English</strong> · <a href="README.zh-CN.md">简体中文</a></p>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/banner-dark.svg">
  <img alt="hey-my-buddy" src="docs/assets/banner-light.svg" width="100%">
</picture>

<p align="center"><b>Delegate the work. Keep the responsibility.</b></p>

<p align="center">
  Let the coding agent you work with, the Host, delegate bounded tasks to other coding agents on your machine, and check each sealed result before accepting it.<br>
  Micro tasks, decisions and results live on a local blackboard; a private console shows who is doing what.
</p>

<p align="center">
  <a href="#how-buddies-collaborate">How buddies collaborate</a> ·
  <a href="#install">Install</a> ·
  <a href="#console-routing-and-models">Console</a> ·
  <a href="#current-status-and-limits">Status and limits</a> ·
  <a href="#documentation-and-development">Docs</a>
</p>

---

The Host buddy owns a micro task and its acceptance; Worker buddies execute bounded parts of it in local coding harnesses. Both are buddies of equal standing that coordinate only through a shared blackboard; they differ in role. A Host can implement one part itself and hand other parts to a Worker whose harness, model or cost fits the work. The supported coding harnesses are DeepSeek Harness (`dsh`), ZCode (`zcode`) and the experimental Codex App Server (`codex`); a Claude Code harness (`claude`, [reference](docs/reference/claude-code.md)) joins them as a P1 harness with verified native outcome, cancellation and bounded sandbox paths, plus a separately approved read-only lifecycle test through the installed service.

Workers use your own harness sign-ins, and their usage counts against your own plans and keys. [Accounts and terms](docs/terms.md) says what hey-my-buddy does with your accounts and which terms still apply to you.

Coding work gets explicit Git workspaces and fixed input/output snapshots. The blackboard is a local SQLite database, and the private React/Vite console shows tasks, routing, shared buddies, preferences and per-model concurrency.

## How buddies collaborate

Use it for scoped implementation, testing, reproducible investigations, documentation or file transformations with a checkable result. Keep the task with the Host when the edit is trivial, the answer is already known or the requirements still need clarification.

1. The Host chooses the micro task, the buddy and the execution workspace. A complete harness/provider/model/effort tuple is validated and dispatched directly; omit all four fields to let the Router choose from bounded candidates using the shared routing mode and user preferences. Parallel writers get independent Git worktrees, and one named integrator owns the combined result.
2. The Worker acts with its own tools and internal subagents. When it needs help, it ends the turn with a structured request, and the Host approves explicit helpers, declines with a reason, continues with new input or supplies a complete buddy at a routing boundary. Ownership is fenced by a private control capability; a Host name alone is never authority.
3. The Host inspects the real diff, runs the relevant checks and accepts the fixed final artifact, naming the integration target (or an explicit not-required decision): one `accept` verifies the integration, records the acceptance and reclaims the managed checkout. A micro task the Host will not accept is ended with `conclude`, after which nothing continues it. Execution, helper completion, integration and acceptance are separate facts.

## Install

hey-my-buddy is one shared Agent Skill, `hey-my-buddy`, that carries its own CLI, plus one local service per state directory. The verified daily installation is 0.50.0 (contract 0.50.0, schema 27), installed on 2026-10-20 ([record](docs/acceptance/installs/0.50.0.md)): replies no longer carry fields that were constant, repeated or derivable, a submission to a macro task being deleted gets `MACRO_TASK_DELETING`, and the console sorts the macro task list by activity or by creation ([acceptance](docs/acceptance/contract-050/acceptance.md)). With 0.49.0, deleting a macro task leaves nothing of it behind, and the board drops what nothing reads or what repeats another stored value ([acceptance](docs/acceptance/schema-audit/pruning.md)); the `Session` tab reads like a session in the Claude Code app, one row per call with its details on opening ([acceptance](docs/acceptance/adr024/session-view.md)), not yet looked at in daily use. 0.48.0 gives every role one set of permission levels, so that DSH and ZCode can also review as a Router ([acceptance](docs/acceptance/adr023/shared-levels.md)); a review-mode routing has not yet run on a real board. 0.47.0 records each execution's session and shows it in a `Session` tab of the micro task detail ([acceptance](docs/acceptance/adr024/session-view.md)); the records were checked against real turns of all four harnesses after the install. It also makes the console easier to read: one click opens a step, buddy colours stay clear of the colours that carry meaning, and the timeline zooms by gestures on its ruler ([acceptance](docs/acceptance/adr029/console-frontend.md)). 0.46.0 replaced quota readings with cooling: a provider that refuses work for quota sits out routing for a while, and a micro task refused before its model started goes to another buddy ([acceptance](docs/acceptance/adr021/quota-cooling.md)). Hosts delegate only through micro tasks: the `execution-submit` method is removed. It takes Host questions inside running Codex and Claude Code turns, also after a Host has waited on a micro task, and uses C-Two 0.7.6, which loads neither NumPy nor FastDB ([acceptance](docs/acceptance/live-channel-0.43.0.md)). Every console page is rebuilt, the macro task timeline included, and the console can be switched between Chinese and English ([acceptance](docs/acceptance/adr029/console-frontend.md)). On Linux it trusts a group-writable parent directory of the state directory when the group is the user's own private group ([acceptance](docs/acceptance/linux-portability/linux-portability.md)). The upgrade of 0.42.0 from schema 23 to 24 keeps for each command only what identifies the committed operation and for periodic state such as activity heartbeats only the latest record. The skill is placed in `~/.agents/skills/hey-my-buddy`, Claude Code reaches the same directory through the `~/.claude/skills/hey-my-buddy` link, and the retired Codex plugin is no longer used. Source and daily installation are separate facts. The [native sandbox review acceptance](docs/acceptance/native-sandbox-review/0.27.0.md) records probe-owned verification that no longer reads model output. The [routing validation acceptance](docs/acceptance/adr019/routing-validation-0.25.0.md) covers retained review diagnostics and bounded quota recovery. It implements ADR-018 Host workflows: file/stdin packets, method help, macro task reuse, direct Host completion, failure conclusions and cleanup, partial output, buddy changes on continuation, cumulative patches and native usage observations. The [Host-workflow acceptance](docs/acceptance/adr018/host-workflow-0.21.0.md) records the checks and remaining limits. It is not published. An install that moves the middle number takes a verified backup before it switches ([operations](docs/reference/operations.md#installation)).

Once the developer decides the release channel and the exact version, the install entry is one fixed-version package command:

```sh
# After the release channel and version are decided; not available today
uvx hey-my-buddy@0.51.0 install
```

The package name's availability and whether it is published on PyPI are still the developer's decisions, and this candidate has not been published there. Until a release exists, build the wheel from the frozen source and install from its absolute path:

```sh
uv build --wheel --out-dir dist
uvx --from /absolute/path/hey_my_buddy-0.51.0-py3-none-any.whl hey-my-buddy install
```

On a machine without `uv`, the release's `install.sh` (macOS, Linux) or `install.ps1` (Windows) prepares a fixed private `uv` and then calls the same entry; they never change `PATH`, shell or Host settings and never install a global `hey-my-buddy` command. An install prints the paths it will write and materializes the versioned runtime. A version that moves only the last number keeps running work: the service restarts on the new runtime and takes the running executions over. A version that moves the middle number refuses while work is queued or running or a stop is unconfirmed (`UPGRADE_NOT_IDLE`, listing the blockers) and switches with a verified backup. Either way the whole generation — skill, launcher, runtime and service — switches together, and a failure rolls back. Rerunning the same complete version repairs what is missing without restarting the service, and damaged content is repaired by rerunning the same fixed-version command; changed content at the same version reports `updated`. Source archives retain their original commit when later built into wheels. Installing or upgrading the daily service needs the user's separate authorization. The code and scripts are portable to Windows, but no real Windows machine has been validated yet.

### Hand this prompt to the installing agent

```text
Install hey-my-buddy <version> on this machine for me.

1. Use my selected release channel and exact version, or my supplied wheel. Ask only for missing release details; do not assume a PyPI publication.
2. Run the install command outside any agent or Host sandbox, with the Host's supported permission mechanism, without sudo: `uvx hey-my-buddy@<version> install`, or `uvx --from <absolute path to wheel> hey-my-buddy install` for an unpublished build. Without uv, use the matching install.sh or install.ps1 from the release.
3. Before it writes anything, list every path it will create or replace (the skill in ~/.agents/skills/hey-my-buddy, the Claude Code link ~/.claude/skills/hey-my-buddy, data and runtimes under ~/.local/share/hey-my-buddy, plus a private uv if needed; on Windows use the reported %LOCALAPPDATA% data path).
4. Report the install result: skill version and placement, Claude Code link status, service action, and whether a backup was taken.
5. Verify with the installed launcher, using the paths and versions it actually reports: `~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy health`, `... runtime` and `... harnesses`. A new Host session loads the skill; this session can use the absolute launcher path outside the sandbox.
6. If it returns LAUNCH_ACCESS_DENIED, allow that exact launcher command outside the sandbox and retry; do not disable the sandbox globally. If the service is busy it returns UPGRADE_NOT_IDLE and lists the tasks: report them and wait, and never cancel work to force an upgrade.
```

Then start with a bounded micro task:

```sh
HMB_CLI="$HOME/.agents/skills/hey-my-buddy/scripts/hey-my-buddy"
"$HMB_CLI" submit '{"requestId":"doc-links-1","hostId":"codex","task":"Check the relative links in this repository README, add only buddy-doc-review.md, and report broken links with fixes.","cwd":"/abs/repo","expectedSeconds":3600,"executionWorkspace":{"kind":"worktree","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["buddy-doc-review.md"]}}'
"$HMB_CLI" get '{"runId":"<returned-runId>"}'
"$HMB_CLI" await '{"runId":"<returned-runId>"}'
```

The example leaves the buddy choice to routing; `executionWorkspace` is the ownership and artifact-verification contract. Its source directory and integrator are derived from the top-level working directory and submitting Host. `submit` returns a private `controlFile` that later Host commands must present. [Operations](docs/reference/operations.md#installation) owns the exact install steps, error codes, harness discovery commands and recovery.

## Console, routing and models

Open the private local console:

```sh
"$HMB_CLI" console
```

The console opens the fixed loopback address without login by default. Settings can enable login with a single-use entry valid for 10 minutes and explicitly revocable sessions without a 30-day expiry. Every admitted window may edit settings; later saves take effect, and drafts stay per window. Use `console '{"browser":false}'` to open the link yourself, or `console '{"wait":true}'` to keep the CLI attached and close only that console on Ctrl-C. [Console entry](docs/reference/console.md) owns session lifetime, security and the separate installation boundary.

The returned loopback URL opens three pages: Tasks, Buddies and Settings. Tasks lists macro tasks by project, with standalone micro tasks in a last group. A macro task opens as a timeline of its micro tasks, where long idle stretches fold into fixed breaks; a click selects a micro task and a double click opens it in place, organized by turns, with its steps under its name. Buddies groups model services by harness; each reasoning effort identifies a distinct buddy. Each model service row shows its enabled efforts and concurrent-attempt occupancy; enablement is edited separately for each buddy. The roles view shows the ordered Router list and the shared mode. Editing is direct, with no global edit switch: a draft starts as soon as a control changes, and a bottom save bar offers only discard and save; an uncertain reply preserves recovery state. It touches only your own buddy preferences and enablement, model service notes and concurrency, and the Router list: catalog facts stay read-only. Each model service also carries a user-owned concurrent-attempt limit; it takes effect at the next admission, and lowering it never stops already-running attempts. The same page shows the recorded harness status with an explicit Check action and an advanced manual path for when automatic detection fails; refreshing runs no model. The Settings page holds the theme, the language (Chinese or English), reminders, access, the version and the local storage check and reclaim panel. Viewing, refreshing and editing drafts call no model.

Python freezes legal routing candidates and validates answer bounds; unhealthy harnesses are excluded. Routing has two modes: fast routing runs one call with every harness tool disabled and sees only the task, the candidates' facts, preferences and notes, while review routing is the read-only mode that also reads a frozen repository copy. Fast routing is implemented for DSH, ZCode and Codex: DSH passed an approved provider check, and ZCode/Codex passed native offline zero-tool checks; ZCode provider validation was blocked by a 429 rate limit and Codex fast routing has not been paid-probed. The 0.19.0 source verifies the Codex `openai / gpt-6-sol / high` read-only Router on macOS with CLI 0.157.0 and recommends the `standard` budget. Linux/Windows and the other harnesses remain unverified for review; an assigned Router must also be available and enabled. The shared mode applies to an ordered Router buddy list; routing uses its first available enabled buddy. If none is available, the micro task returns to the Host at a routing boundary. Note that a fast Router call sends the task description to that Router's model provider, including tasks prepared for a different execution model, and review routing additionally reads a frozen repository copy. After installation, the user selects the buddies in Buddies; this candidate does not change that setting. See the [routing contract](docs/reference/decision.md), [harness discovery](docs/reference/harnesses.md) and [acceptance evidence](docs/acceptance/adr018/routing-modes-0.20.0.md).

## Execution and recovery

Codex Hosts use one monitoring-only native subagent per running micro task, explicitly selecting the cheapest model currently available to Codex that can execute commands, at the lowest reasoning effort. The parent continues independent work and owns every decision and acceptance; Claude Code keeps its background Bash wait. See [waiting from Codex](docs/reference/host-codex.md) for permissions, wait limits and foreground fallback.

Independent work runs in parallel by default under one machine-wide concurrent-attempt ceiling (default 8, configurable 1–32) shared by routing and execution; on top of it, each model service (an exact harness/provider/model) keeps a user-set limit (default 2), and effort variants plus the routing decisions using that model share its counter. The service admits work within these limits and starts one execution process for each attempt; a running installation may be configured with a different ceiling, so read `health.capacity` rather than assuming. Workspace overlap and exclusive resources still serialize conflicting work; parallel editors need separate worktrees. See [capacity settings](docs/reference/operations.md#private-state-and-environment) and inspect `health.capacity` for the total and per-model occupancy.

A wait timeout, a closed terminal or a lost connection never cancels work. Recover the same `runId` with `await`, `get` or `status`. Use an explicit `cancel` to stop a micro task; shutdown is reported only with real stop evidence. Each continuation gets a new attempt. DSH and ZCode resume a proven native session only when its micro task, checkout and buddy binding matches, and Codex resumes only its exact bound native thread. Without a proven session or after a buddy change, the harness reconstructs a new root session. The execution process of an attempt outlives a restart of the service: it finishes its turn and delivers its result to the new service, and an immutable completion receipt is replayed rather than executed again. Results record why they really stopped (completion, a wrap-up or a stop and who asked for it, an exhausted Router budget, a harness or transport failure, or an interruption), so one is never displayed as another. After a power loss, a reboot or a crashed execution process, the identity recorded for each execution (the boot session, the process and its start time, its process group or Windows Job) settles it as interrupted without any action: either the processes are proven gone or the service stops what is left, by its verified identity. The micro task then waits for the Host, who may continue it from its unverified partial output. A bounded activity projection shows phase, last native/tool activity and honest counters; an empty log, a missing PID or a static session list never proves a process stopped. Keep requested, resolved and actually observed model identity distinct.

`expectedSeconds` (default 1800) is the moment the Host should look again, not a deadline: nothing is stopped when it passes, and there is no form without an expectation. Only a Router call keeps a hard budget, taken from the Router settings. The Host can still cancel the task, and a bounded `await` call ending does not stop it. Read [execution duration and wait windows](docs/reference/cli.md#defaults-and-bounds) before choosing this option.

The service and its execution processes run from that stable private runtime. Daily commands execute the active runtime's own Python directly, without `uv`; `uv` is used only by install and upgrade. The board lives under `~/.local/share/hey-my-buddy/state`; an upgrade converts it in place after a verified backup. Directories and Git worktrees are not OS sandboxes.

## Current status and limits

The daily installation is [0.50.0/contract 0.50.0/schema 27](docs/acceptance/installs/0.50.0.md). It changed the contract but not the stored form, so it was installed on an idle board after a verified backup and converted nothing.

The daily runtime is supported on macOS and Linux with local single-user SQLite state; Windows code and scripts are portable but unvalidated on a real machine. ZCode supports activity observation and cooperative inquiry: questions wait for the root's next tool checkpoint or finish attempt, and cannot interrupt a running tool or open a new turn. Native permission requests and long Host decisions still use the attention/assistance boundary. Codex and Claude Code take Host questions into the running turn: the model reads one at its next decision and answers through its inquiry tool, a question never opens or extends a turn, and one that arrives after the turn has ended is refused. Codex uses the experimental App Server. The 0.19.0 source verifies its read-only Router on macOS with `openai / gpt-6-sol / high` and recommends the `standard` budget; Linux/Windows and other harnesses remain unverified. Installation does not change the user's Router setting. Claude P1 reads the user's native provider, account and settings, and reconstructs every continuation. Its [reference](docs/reference/claude-code.md) records verified native paths, the installed read-only lifecycle, simulated regression coverage and remaining limits. No monetary budgets, automatic community research or native App post-turn wakeup are provided. A background follow-up requires an explicit user request.

## Documentation and development

Start with the [documentation index](docs/README.md) for commands, architecture, harnesses and design history. The [operations reference](docs/reference/operations.md) owns installation, error codes and recovery; [harnesses](docs/reference/harnesses.md) owns discovery and health; [console](docs/reference/console.md) and [evaluation](docs/reference/evaluation.md) own the console surface and the evaluation table. [AGENTS.md](AGENTS.md) covers repository invariants; end users do not need npm or a frontend build. Report problems through [GitHub issues](https://github.com/Dsssyc/hey-my-buddy/issues).

## License

[MIT](LICENSE).

Repository verification uses uv and a supported Node version (see `apps/console/package.json`). Prepare the console test dependencies with `npm --prefix apps/console ci`, then run `uv run --frozen python -m hey_my_buddy.cli.checks`; the complete Python check includes synthetic preview data consumed by the actual frontend parsers. Frontend tests and browser checks run separately; see [AGENTS.md](AGENTS.md).

B-stage source adds native billing labels and exhaustion-aware candidate eligibility. Cancellation shows its recorded actor and reason, and console explanations are condensed. [Source and native-check evidence](docs/acceptance/worker-accounts-phase1-0.23.0.md) separates this candidate from installation.

An install that moves the middle number backs up the board, only for its own rollback; evidence, managed checkouts, credentials and native private directories stay outside that backup. Details are in the [operations reference](docs/reference/operations.md#backup-upgrade-and-storage-contract).
