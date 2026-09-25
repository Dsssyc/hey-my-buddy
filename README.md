# hey-my-buddy

[中文文档](README.zh-CN.md)

Buddy lets a Host agent delegate bounded work to local coding harnesses while keeping responsibility for the goal. A Host can implement one part itself and hand other parts to a Worker whose harness, model or cost fits the work. The supported coding harnesses are [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`), ZCode (`zcode`) and the experimental Codex App Server (`codex`).

Goals, decisions and results live in a local SQLite blackboard. Coding work gets explicit Git workspaces and fixed input/output snapshots; a private React/Vite console shows tasks, routing, shared model profiles, preferences, per-model concurrency and evaluation cards.

## Working with Buddy

Use it for scoped implementation, testing, reproducible investigations, documentation or file transformations with a checkable result. Keep the task with the Host when the edit is trivial, the answer is already known or the requirements still need clarification.

1. The Host chooses the goal, the model configuration and the execution workspace. A complete adapter/provider/model/effort tuple is validated and dispatched directly; anything less is routed through the configured decision profile and the bounded evaluation table, while up to eight task-local soft routing preferences influence only that goal and never change shared settings. Parallel writers get independent Git worktrees, and one named integrator owns the combined result.
2. The Worker acts with its own tools and internal subagents. When it needs help, it ends the turn with a structured request, and the Host approves explicit helpers, declines with a reason, continues with new input or supplies a complete configuration at a routing boundary. Ownership is fenced by a private control capability; a Host name alone is never authority.
3. The Host inspects the real diff, runs the relevant checks, records the verified integration (or an explicit not-required decision) bound to the fixed final artifact and acknowledges it, then reclaims the managed checkout through a recorded two-step cleanup. Execution, helper completion, integration and acceptance are separate facts.

## Install and try

You need macOS or Linux, [uv](https://docs.astral.sh/uv/) with Python 3.12–3.14, Node.js 20+ for DSH and the runtime required by your installed ZCode CLI, and a working local `dsh` and/or ZCode installation with its own provider credentials. Codex uses the installed App Server with your existing native account-plan login instead of an API key. Buddy uses the credentials already configured for the harness and leaves global model settings alone.

Install the `hey-my-buddy` plugin from this repository's own marketplace; no personal marketplace and no public registry listing is required. From a local checkout:

```sh
codex plugin marketplace add /abs/path/to/hey-my-buddy
codex plugin add hey-my-buddy@hey-my-buddy
```

A Git clone is the same marketplace: `codex plugin marketplace add Dsssyc/hey-my-buddy --ref main` registers the committed catalog, and `packaging/stage-plugin.py` produces a staged tree that is itself a marketplace root. The [installation reference](docs/reference/operations.md#installation) has the staged local path, the existing-marketplace path and the verification steps. Start a new task so the `$buddy` skill loads. The skill resolves the plugin's own `bin/buddy` launcher; there is no separate skill to install and no old-layout fallback. The first command that needs the service installs a stable runtime under `~/.local/share/hey-my-buddy/runtime`, so replacing the plugin later does not disturb running work.

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

The returned loopback URL opens delegation records, model cards and routing configuration. Records are grouped by source project and show original/current Host attribution, execution turns and frozen routing rationale. Model families group effort variants while keeping evaluations independent; enabled variants have a visible check, separate from the inspected variant. The top-right edit-mode switch creates a local draft; Save acquires a short publication grant, and conflicts or uncertain replies preserve recovery state. Edit mode touches only your own annotations, preferences, enablement, model concurrency and the selector: automatic assessments, evidence and catalog facts stay read-only. Each model family also carries a user-owned concurrent-attempt limit edited on its model card; it is published through the same local draft and short grant, takes effect at the next claim, and lowering it never stops already-running attempts. The same surface can show unavailable configurations and page their retained history; a stale pin or selector is a warning that never blocks unrelated saves. `Update history` shows published revisions, and a light/dark switch remembers only your display preference. Viewing, refreshing and editing drafts call no model.

Ask a skill-equipped Harness to “update the Buddy model evaluations,” or schedule that request with the Harness's own scheduler when you explicitly want recurring updates. The [maintenance workflow](docs/reference/evaluation-maintenance.md) incrementally collects reviewed facts across Hosts/projects, preserves qualified failed and retried attempts, and publishes a bounded card-only update without changing preferences or annotations. Task acknowledgement does not trigger a model call. When no new material is available, skip synthesis without claiming a fresh assessment.

## Execution and recovery

Independent work runs in parallel by default under one machine-wide concurrent-attempt ceiling (default 8, configurable 1–32) shared by routing and execution; on top of it, each exact adapter/provider/model family keeps a user-set limit (default 2 per family), and effort variants plus the routing decisions using that model share its counter. The daemon starts the corresponding worker pool automatically; a running installation may be configured with a different ceiling, so read `health.capacity` rather than assuming. Workspace overlap and exclusive resources still serialize conflicting work; parallel editors need separate worktrees. See [capacity settings](docs/reference/operations.md#private-state-and-environment) and inspect `health.capacity` for the total and per-model occupancy.

A wait timeout, a closed terminal or a lost connection never cancels work. Recover the same `runId` with `await`, `get` or `status`; only an explicit `cancel` stops a goal, and shutdown is reported only with real stop evidence. Each continuation gets a new attempt. DSH reconstructs a fresh session; ZCode resumes a proven native session only when its goal, checkout and configuration binding matches, and Codex resumes only its exact bound native thread. Without a proven session or after a configuration change, the harness reconstructs a new root session. After a daemon restart the worker that still holds the child reattaches the uncertain attempt by identity and clears the restart waiting reason, while a different process is refused; an immutable completion receipt is replayed rather than executed again. Results record why they really stopped (completion, user cancel, deadline, harness error or transport failure), so an expiry is never displayed as a user cancel. A bounded activity projection shows phase, last native/tool activity and honest counters; an empty log, a missing PID or a static session list never proves a process stopped. Keep requested, resolved and actually observed model identity distinct.

For user-authorized long tasks, `"timeoutSeconds": 0` explicitly removes the execution deadline; the ordinary default remains 1800 seconds. The Host can still cancel the task, and a bounded `await` call ending does not stop it. Read [execution duration and wait windows](docs/reference/cli.md#defaults-and-bounds) before choosing this option.

The service and its Workers run from that stable private runtime. A fresh board lives under `~/.local/share/hey-my-buddy/state`; an older board directory at the previous default location is retained as an archive and is never read, converted or imported. Directories and Git worktrees are not OS sandboxes.

Current limits: POSIX only; local single-user SQLite state. ZCode supports API-key providers, activity observation and cooperative inquiry: questions wait for the root's next tool checkpoint or finish attempt, and cannot interrupt a running tool or open a new turn. Native permission requests and long Host decisions still use the governed attention/assistance boundary. Codex uses the experimental App Server and does not declare inquiry or tool-free routing. No monetary budgets, automatic community research, built-in periodic maintenance or native App post-turn wakeup are provided. A background follow-up requires an explicit user request.

This source uses contract 0.10.0 and schema 11. Routine attachment uses lightweight `ping`; explicit `health` retains full storage diagnostics. Source verification and installation are recorded separately in [0.10.0 acceptance](docs/acceptance/runtime-efficiency-0.10.0.md); use `health` and `runtime` to identify the running installation.

## Documentation and development

Start with the [documentation index](docs/README.md) for commands, architecture, harness adapters and design history. [AGENTS.md](AGENTS.md) covers repository invariants; end users do not need npm or a frontend build. Report problems through [GitHub issues](https://github.com/Dsssyc/hey-my-buddy/issues).

## License

[MIT](LICENSE).
