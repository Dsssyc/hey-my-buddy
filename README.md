<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg"><img src="docs/assets/logo-light.svg" alt="hey-my-buddy logo" width="88"></picture></p>

# hey-my-buddy

[中文文档](README.zh-CN.md)

hey-my-buddy lets a Host buddy, the agent that owns a goal, delegate bounded work to Worker buddies running in local coding harnesses while keeping responsibility for the goal. Both are buddies of equal standing that coordinate only through a shared blackboard; they differ in role. A Host can implement one part itself and hand other parts to a Worker whose harness, model or cost fits the work. The supported coding harnesses are [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`), ZCode (`zcode`) and the experimental Codex App Server (`codex`); a Claude Code adapter (`claude`, [reference](docs/reference/claude.md)) joins them as a P1 adapter with verified native outcome, cancellation and bounded sandbox paths, plus a separately approved read-only lifecycle test through the installed service.

Goals, decisions and results live in a local SQLite blackboard. Coding work gets explicit Git workspaces and fixed input/output snapshots; a private React/Vite console shows tasks, routing, shared model profiles, preferences, per-model concurrency and evaluation cards.

## How buddies collaborate

Use it for scoped implementation, testing, reproducible investigations, documentation or file transformations with a checkable result. Keep the task with the Host when the edit is trivial, the answer is already known or the requirements still need clarification.

1. The Host chooses the goal, the model configuration and the execution workspace. A complete adapter/provider/model/effort tuple is validated and dispatched directly; anything less is routed through the Router of the effective mode (fast or review) and the bounded evaluation table, while up to eight task-local soft routing preferences influence only that goal and never change shared settings. Parallel writers get independent Git worktrees, and one named integrator owns the combined result.
2. The Worker acts with its own tools and internal subagents. When it needs help, it ends the turn with a structured request, and the Host approves explicit helpers, declines with a reason, continues with new input or supplies a complete configuration at a routing boundary. Ownership is fenced by a private control capability; a Host name alone is never authority.
3. The Host inspects the real diff, runs the relevant checks, records the verified integration (or an explicit not-required decision) bound to the fixed final artifact and acknowledges it, then reclaims the managed checkout through a recorded two-step cleanup. Execution, helper completion, integration and acceptance are separate facts.

## Install

hey-my-buddy is one shared Agent Skill, `buddy`, that carries its own CLI, plus one local service per state directory. The verified daily installation is 0.27.0 (contract 0.27.0, schema 15), the same version as the current source: the skill is placed in `~/.agents/skills/buddy`, Claude Code reaches the same directory through the `~/.claude/skills/buddy` link, and the retired Codex plugin is no longer used. Source and daily installation are separate facts. The [native sandbox review acceptance](docs/acceptance/native-sandbox-review-0.27.0.md) records probe-owned verification that no longer reads model output. The [routing validation acceptance](docs/acceptance/routing-validation-0.25.0.md) covers retained review diagnostics and bounded quota recovery. It implements ADR-018 Host workflows: file/stdin packets, method help, objective reuse, direct Host completion, failure conclusions and cleanup, partial output, configuration changes on continuation, cumulative patches and native usage/quota observations. The [Host-workflow acceptance](docs/acceptance/host-workflow-0.21.0.md) records the checks and remaining limits. It is not published; schema 14 → 15 migrates only through an explicit idle upgrade.

Once the user decides the release channel and the exact version, the install entry is one fixed-version package command:

```sh
# After the release channel and version are decided; not available today
uvx hey-my-buddy@0.21.0 install
```

The package name's availability and whether it is published on PyPI are still the user's decisions, and this candidate has not been published there. Until a release exists, build the wheel from the frozen source and install from its absolute path:

```sh
uv build --wheel --out-dir dist
uvx --from /absolute/path/hey_my_buddy-0.21.0-py3-none-any.whl hey-my-buddy install
```

On a machine without `uv`, the release's `install.sh` (macOS, Linux) or `install.ps1` (Windows) prepares a fixed private `uv` and then calls the same entry; they never change `PATH`, shell or Host settings and never install a global `buddy` command. An install prints the paths it will write, materializes the versioned runtime, refuses while work is running (`UPGRADE_NOT_IDLE`, listing the running tasks), then switches the whole generation — skill, launcher, runtime and service — with one verified rolling backup and rollback. Rerunning the same complete version repairs what is missing without restarting the service, and damaged content is repaired by rerunning the same fixed-version command; changed content at the same version reports `updated`. Source archives retain their original commit when later built into wheels. Installing or upgrading the daily service needs the user's separate authorization. The code and scripts are portable to Windows, but no real Windows machine has been validated yet.

### Hand this prompt to the installing agent

```text
Install hey-my-buddy <version> on this machine for me.

1. Use my selected release channel and exact version, or my supplied wheel. Ask only for missing release details; do not assume a PyPI publication.
2. Run the install command outside any agent or Host sandbox, with the Host's supported permission mechanism, without sudo: `uvx hey-my-buddy@<version> install`, or `uvx --from <absolute path to wheel> hey-my-buddy install` for an unpublished build. Without uv, use the matching install.sh or install.ps1 from the release.
3. Before it writes anything, list every path it will create or replace (the skill in ~/.agents/skills/buddy, the Claude Code link ~/.claude/skills/buddy, data and runtimes under ~/.local/share/hey-my-buddy, plus a private uv if needed; on Windows use the reported %LOCALAPPDATA% data path).
4. Report the install result: skill version and placement, Claude Code link status, service action, and whether a backup was taken.
5. Verify with the installed launcher, using the paths and versions it actually reports: `~/.agents/skills/buddy/scripts/buddy health`, `... runtime` and `... adapters`. A new Host session loads the skill; this session can use the absolute launcher path outside the sandbox.
6. If it returns LAUNCH_ACCESS_DENIED, allow that exact launcher command outside the sandbox and retry; do not disable the sandbox globally. If the service is busy it returns UPGRADE_NOT_IDLE and lists the tasks: report them and wait, and never cancel work to force an upgrade.
```

Then start with a bounded goal:

```sh
BUDDY="$HOME/.agents/skills/buddy/scripts/buddy"
"$BUDDY" submit '{"requestId":"doc-links-1","hostId":"codex","task":"Check the relative links in this repository README, add only buddy-doc-review.md, and report broken links with fixes.","cwd":"/abs/repo","timeoutSeconds":3600,"workspace":false,"executionWorkspace":{"kind":"worktree","cwd":"/abs/repo","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["buddy-doc-review.md"],"integrator":"codex"}}'
"$BUDDY" get '{"runId":"<returned-runId>"}'
"$BUDDY" await '{"runId":"<returned-runId>"}'
```

The example leaves the model choice to routing and disables DSH session grouping with `workspace:false`. Grouping is a DSH option independent of Git isolation; `executionWorkspace` is the ownership and artifact-verification contract. `submit` returns a private `controlFile` that later Host commands must present. [Operations](docs/reference/operations.md#installation) owns the exact install steps, error codes, harness discovery commands and recovery.

## Console, routing and models

Open the private local console:

```sh
"$BUDDY" console
```

The console opens the fixed loopback address without login by default. Settings can enable login with a single-use entry valid for 10 minutes and explicitly revocable sessions without a 30-day expiry. Every admitted window may edit settings under revision checks; drafts and conflicts stay per window. Use `console '{"browser":false}'` to open the link yourself, or `console '{"wait":true}'` to keep the CLI attached and close only that console on Ctrl-C. [Console entry](docs/reference/console.md) owns session lifetime, security and the separate installation boundary.

The returned loopback URL opens three pages: 委派记录, Buddy 配置 and 设置. Records are grouped by source project and show original/current Host attribution, execution turns and frozen routing rationale; work objectives open as overview cards with a delegation timeline, and standalone delegations fold under 未归档委派. Buddy 配置 groups model families by harness and keeps thinking efforts as independent evaluation targets; each family row shows its enabled efforts and a Router mark, and enabled variants have a visible check, with the routing status line showing both Router slots and the default mode. Editing is direct, with no global edit switch: a draft starts as soon as a control changes, a bottom save bar offers only discard and save, and saving acquires a short publication grant; conflicts or uncertain replies preserve recovery state. It touches only your own family preferences and notes, per-effort overrides, enablement, model concurrency and the Router slots: automatic assessments, evidence and catalog facts stay read-only. Each model family also carries a user-owned concurrent-attempt limit; it takes effect at the next claim, and lowering it never stops already-running attempts. The same page shows the recorded harness status with an explicit 重新检测 action and an advanced manual path for when automatic detection fails; refreshing runs no model. `更新记录` shows published revisions, and the 设置 page holds the light/dark/follow-system theme plus the local storage check and reclaim panel. Viewing, refreshing and editing drafts call no model.

Ask a skill-equipped Harness to “update the blackboard's model evaluations,” or schedule that request with the Harness's own scheduler when you explicitly want recurring updates. The [maintenance workflow](docs/reference/evaluation-maintenance.md) incrementally collects reviewed facts across Hosts/projects, preserves qualified failed and retried attempts, and publishes a bounded card-only update without changing user preferences or notes. Task acknowledgement does not trigger a model call. When no new material is available, skip synthesis without claiming a fresh assessment.

Python freezes legal routing candidates and validates answer bounds; unhealthy harnesses are excluded. Routing has two modes: fast routing runs one call with every harness tool disabled and sees only the task, preferences and evaluation cards, while review routing is the read-only mode that also reads a frozen repository copy. Fast routing is implemented for DSH, ZCode and Codex: DSH passed an approved provider check, and ZCode/Codex passed native offline zero-tool checks; ZCode provider validation was blocked by a 429 rate limit and Codex fast routing has not been paid-probed. The 0.19.0 source verifies the Codex `openai / gpt-6-sol / high` read-only Router on macOS with CLI 0.157.0 and recommends the `standard` budget. Linux/Windows and the other harnesses remain unverified for review; an assigned Router must also be available and enabled. A submission may pick `routingMode`, and an unavailable review Router falls back to fast routing unless the Host passes `allowRoutingFallback:false`. Note that fast routing sends every task description to the fast Router's model provider, including tasks prepared for a different execution model, and review routing additionally reads a frozen repository copy. After installation, the user selects the profiles in Buddy 配置; this candidate does not change that setting. See the [routing contract](docs/reference/decision.md), [harness discovery](docs/reference/harnesses.md) and [acceptance evidence](docs/acceptance/routing-modes-0.20.0.md).

## Execution and recovery

Codex Hosts use one monitoring-only native subagent per running delegation, explicitly selecting the cheapest model currently available to Codex that can execute commands, at the lowest reasoning effort. The parent continues independent work and owns every decision and acceptance; Claude Code keeps its background Bash wait. See [waiting from Codex](docs/reference/usage.md#waiting-from-codex) for permissions, wait limits and foreground fallback.

Independent work runs in parallel by default under one machine-wide concurrent-attempt ceiling (default 8, configurable 1–32) shared by routing and execution; on top of it, each exact adapter/provider/model family keeps a user-set limit (default 2 per family), and effort variants plus the routing decisions using that model share its counter. The daemon starts the corresponding worker pool automatically; a running installation may be configured with a different ceiling, so read `health.capacity` rather than assuming. Workspace overlap and exclusive resources still serialize conflicting work; parallel editors need separate worktrees. See [capacity settings](docs/reference/operations.md#private-state-and-environment) and inspect `health.capacity` for the total and per-model occupancy.

A wait timeout, a closed terminal or a lost connection never cancels work. Recover the same `runId` with `await`, `get` or `status`; only an explicit `cancel` stops a goal, and shutdown is reported only with real stop evidence. Each continuation gets a new attempt. DSH reconstructs a fresh session; ZCode resumes a proven native session only when its goal, checkout and configuration binding matches, and Codex resumes only its exact bound native thread. Without a proven session or after a configuration change, the harness reconstructs a new root session. After a daemon restart the worker that still holds the child reattaches the uncertain attempt by identity and clears the restart waiting reason, while a different process is refused; an immutable completion receipt is replayed rather than executed again. Results record why they really stopped (completion, user cancel, deadline, harness error or transport failure), so an expiry is never displayed as a user cancel. A bounded activity projection shows phase, last native/tool activity and honest counters; an empty log, a missing PID or a static session list never proves a process stopped. Keep requested, resolved and actually observed model identity distinct.

For user-authorized long tasks, `"timeoutSeconds": 0` explicitly removes the execution deadline; the ordinary default remains 1800 seconds. The Host can still cancel the task, and a bounded `await` call ending does not stop it. Read [execution duration and wait windows](docs/reference/cli.md#defaults-and-bounds) before choosing this option.

The service and its Workers run from that stable private runtime. Daily commands execute the active runtime's own Python directly, without `uv`; `uv` is used only by install and upgrade. A fresh board lives under `~/.local/share/hey-my-buddy/state`; an older board directory at the previous default location is retained as an archive and is never read, converted or imported. Directories and Git worktrees are not OS sandboxes.

## Current status and limits

The daily installation is [0.27.0/contract 0.27.0/schema 15](docs/acceptance/installed-0.27.0.md). It includes ADR-019 B phase two with Worker accounts (a shared local login managed read-only by default, or a Worker-private source with verified Codex private login and stdin keys and a Claude API key in a dedicated macOS Keychain entry) and rests the Codex review check on controller-owned native sandbox probes with structurally checked model tool streams, which still awaits one approved native certification. The [0.27.0 installation record](docs/acceptance/installed-0.27.0.md) separates verified behavior from checks that still need the user's approval.

The daily runtime is supported on macOS and Linux with local single-user SQLite state; Windows code and scripts are portable but unvalidated on a real machine. ZCode supports API-key providers, activity observation and cooperative inquiry: questions wait for the root's next tool checkpoint or finish attempt, and cannot interrupt a running tool or open a new turn. Native permission requests and long Host decisions still use the governed attention/assistance boundary. Codex uses the experimental App Server and does not declare inquiry. The 0.19.0 source verifies its read-only Router on macOS with `openai / gpt-6-sol / high` and recommends the `standard` budget; Linux/Windows and other harnesses remain unverified. Installation does not change the user's Router setting. Claude P1 requires first-party Anthropic authentication, uses isolated settings by default and reconstructs every continuation without inquiry. Its [reference](docs/reference/claude.md) records verified native paths, the installed read-only lifecycle, simulated regression coverage and remaining limits. No monetary budgets, automatic community research, built-in periodic maintenance or native App post-turn wakeup are provided. A background follow-up requires an explicit user request.

## Documentation and development

Start with the [documentation index](docs/README.md) for commands, architecture, harness adapters and design history. The [operations reference](docs/reference/operations.md) owns installation, error codes and recovery; [harnesses](docs/reference/harnesses.md) owns discovery and health; [console](docs/reference/console.md) and [evaluation](docs/reference/evaluation.md) own the console surface and the evaluation table. [AGENTS.md](AGENTS.md) covers repository invariants; end users do not need npm or a frontend build. Report problems through [GitHub issues](https://github.com/Dsssyc/hey-my-buddy/issues).

## License

[MIT](LICENSE).

Repository verification uses uv and a supported Node version (see `apps/console/package.json`). Prepare the console test dependencies with `npm --prefix apps/console ci`, then run `uv run --frozen python -m buddy.checks`; the complete check includes synthetic preview data consumed by the actual frontend parsers.

B-stage source adds native billing labels, exhaustion-aware candidate eligibility and explicit current-version review verification in Buddy configuration or `harness-verify`; new versions fall back to fast routing until verified. Cancellation shows its recorded actor and reason, and console explanations are condensed. Independent Worker accounts and login follow the private-directory prerequisite. [Source and native-check evidence](docs/acceptance/worker-accounts-phase1-0.23.0.md) separates this candidate from installation.

The 0.24.0 source separates attempt evidence from private harness state. Before requesting installation, run the new package’s `buddy backup-preflight '{}'`: it lists copying, skipped and refused paths without starting a service or writing data. The installer checks this inventory before stopping the service and relocates recognized stopped old layouts only after verified backup; credentials and native private directories stay outside backup. Details are in the [operations reference](docs/reference/operations.md#backup-upgrade-and-storage-contract).
