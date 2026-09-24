# hey-my-buddy

[中文文档](README.zh-CN.md)

Buddy lets a Host agent delegate bounded work to local coding harnesses while keeping responsibility for the goal. A Host can implement one part itself and hand other parts to a Worker whose harness, model or cost fits the work. The supported coding harnesses are [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`) and ZCode (`zcode`).

Goals, decisions and results live in a local SQLite blackboard. Coding work gets explicit Git workspaces and fixed input/output snapshots; a private React/Vite console shows tasks, routing, shared model profiles, preferences and evaluation cards.

## Working with Buddy

Use it for scoped implementation, testing, reproducible investigations, documentation or file transformations with a checkable result. Keep the task with the Host when the edit is trivial, the answer is already known or the requirements still need clarification.

1. The Host chooses the goal, the model configuration and the execution workspace. A complete adapter/provider/model/effort tuple is validated and dispatched directly; anything less is routed through the configured decision profile and the bounded evaluation table. Parallel writers get independent Git worktrees, and one named integrator owns the combined result.
2. The Worker acts with its own tools and internal subagents. When it needs help, it ends the turn with a structured request, and the Host approves explicit helpers, declines with a reason, continues with new input or supplies a complete configuration at a routing boundary. Ownership is fenced by a private control capability; a Host name alone is never authority.
3. The Host inspects the real diff, runs the relevant checks and acknowledges the fixed final artifact. Execution, helper completion, integration and acceptance are separate facts.

## Install and try

You need macOS or Linux, [uv](https://docs.astral.sh/uv/) with Python 3.12–3.14, Node.js 20+ for DSH and the runtime required by your installed ZCode CLI, and a working local `dsh` and/or ZCode installation with its own provider credentials. Buddy uses the credentials already configured for the harness and leaves global model settings alone.

Install the `hey-my-buddy` plugin from a marketplace that carries it. For a configured marketplace whose entry points at your staged directory:

```sh
uv run --frozen python packaging/stage-plugin.py --destination /path/to/marketplace/plugins/hey-my-buddy
codex plugin add hey-my-buddy@your-marketplace
```

For a first local installation, follow the [marketplace setup](docs/reference/operations.md#installation). Start a new task so the `$buddy` skill loads. The skill resolves the plugin's own `bin/buddy` launcher; there is no separate skill to install and no old-layout fallback. The first command that needs the service installs a stable runtime under `~/.local/share/hey-my-buddy/runtime`, so replacing the plugin later does not disturb running work.

Start with a bounded goal:

```sh
BUDDY="<absolute-plugin-root>/bin/buddy"
"$BUDDY" submit '{"requestId":"doc-links-1","hostId":"codex","task":"Check the relative links in this repository README, add only buddy-doc-review.md, and report broken links with fixes.","cwd":"/abs/repo","timeoutSeconds":3600,"workspace":false,"executionWorkspace":{"kind":"worktree","cwd":"/abs/repo","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["buddy-doc-review.md"],"integrator":"codex"}}'
"$BUDDY" get '{"runId":"<returned-runId>"}'
"$BUDDY" await '{"runId":"<returned-runId>"}'
```

The example leaves the model choice to routing and disables DSH session grouping with `workspace:false`. Grouping is a DSH option independent of Git isolation; `executionWorkspace` is the ownership and artifact-verification contract. `submit` returns a private `controlFile` that later Host commands must present.

## Console, routing and models

Open the private local console:

```sh
"$BUDDY" console
```

The returned loopback URL opens delegation records, model cards and routing configuration. Records are grouped by source project and show original/current Host attribution, execution turns and frozen routing rationale. Model families group effort variants while keeping evaluations independent; enabled variants have a visible check, separate from the inspected variant. The top-right edit-mode switch creates a local draft; Save acquires a short publication grant, and conflicts or uncertain replies preserve recovery state. Evidence stays read-only with guidance to ask a skill-equipped Harness for an update; `Update history` shows published revisions. A light/dark switch remembers only your display preference. Viewing, refreshing and editing drafts call no model.

Ask a skill-equipped Harness to “update the Buddy model evaluations,” or schedule that request with the Harness's own scheduler. The [maintenance workflow](docs/reference/evaluation-maintenance.md) incrementally collects reviewed facts across Hosts/projects, preserves qualified failed and retried attempts, and publishes a bounded card-only update without changing preferences. Task acknowledgement does not trigger a model call. When no new material is available, skip synthesis without claiming a fresh assessment.

## Execution and recovery

Independent work runs in parallel by default: two business attempts plus one reserved routing decision. The daemon starts the corresponding worker pool automatically. Workspace overlap and exclusive resources still serialize conflicting work; parallel editors need separate worktrees. See [capacity settings](docs/reference/operations.md#private-state-and-environment) and inspect `health.capacity` for lane limits and occupancy.

A wait timeout, a closed terminal or a lost connection never cancels work. Recover the same `runId` with `await`, `get` or `status`; only an explicit `cancel` stops a goal, and shutdown is reported only with real stop evidence. Each continuation gets a new attempt. DSH reconstructs a fresh session; ZCode resumes a proven native session only when its goal, checkout and configuration binding matches. Without a proven previous session or after a configuration change, ZCode reconstructs a new root session. Keep requested, resolved and actually observed model identity distinct.

The service and its Workers run from that stable private runtime. A fresh board lives under `~/.local/share/hey-my-buddy/state`; an older board directory at the previous default location is retained as an archive and is never read, converted or imported. Directories and Git worktrees are not OS sandboxes.

Current limits: POSIX only; local single-user SQLite state; ZCode supports API-key providers only and has no inquiry bridge; no monetary budgets, automatic community research or native App post-turn wakeup. The [0.6 acceptance record](docs/acceptance/neutral-core-0.6.0.md) documents regression checks, real DSH/ZCode cooperation and the installed runtime's routed artifact task. Check the running installation with `health` and `runtime` after each upgrade.

## Documentation and development

Start with the [documentation index](docs/README.md) for commands, architecture, harness adapters and design history. [AGENTS.md](AGENTS.md) covers repository invariants; end users do not need npm or a frontend build. Report problems through [GitHub issues](https://github.com/Dsssyc/hey-my-buddy/issues).

## License

[MIT](LICENSE).
