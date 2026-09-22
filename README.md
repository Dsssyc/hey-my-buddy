# hey-my-buddy

[中文文档](README.zh-CN.md)

Buddy lets Codex delegate bounded work to a local coding agent while keeping responsibility for the overall task. The Host can implement one part itself and hand other parts to a Worker whose capabilities or cost fit the work. The current coding integration is [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`).

Work, decisions and results live in a local blackboard. Repository tasks have explicit workspaces and fixed input/output snapshots; a React/Vite console shows their progress alongside shared model profiles, preferences and evaluation cards.

## Working with Buddy

Use it for scoped implementation, testing, reproducible investigations, documentation or file transformations with a checkable result. Keep the task with the Host when the edit is trivial, the answer is already known or the requirements still need clarification. Delegation is useful when the execution savings justify the handoff, verification and possible rework.

1. The Host chooses the task, model configuration and workspace. Parallel writers get independent Git worktrees; a sequential sole writer can use an existing checkout. One named integrator owns the combined result.
2. The Worker executes with its own tools and internal subagents. When it needs assistance, it ends its current turn and records a request. The Host can authorize helpers and one automatic continuation; the same logical goal then continues from fixed artifacts in a new session.
3. The Host inspects the actual diff and runs relevant checks before accepting the final artifact. Helper completion, integration and final acceptance are recorded separately.

The [workflow guide](deepseek-delegate/references/workflow.md) explains assistance, continuation and ownership. Ordinary one-shot commands remain available for non-Git work.

## Install and try

You need macOS or Linux, [uv](https://docs.astral.sh/uv/) with Python 3.12–3.14, Node.js 20+ for Buddy's runner, a working local dsh installation with your provider credentials, and Codex skill support. Buddy uses the configured harness credentials and leaves global model settings alone.

From a checkout, install the complete `deepseek-delegate/` directory as a standalone skill:

```sh
git clone https://github.com/Dsssyc/hey-my-buddy.git
cd hey-my-buddy
uv sync --frozen --project deepseek-delegate --python 3.12

BUDDY_SKILLS_DIR="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$BUDDY_SKILLS_DIR"
if [ -e "$BUDDY_SKILLS_DIR/deepseek-delegate" ] || [ -L "$BUDDY_SKILLS_DIR/deepseek-delegate" ]; then
  printf '%s\n' 'A skill already exists here; follow the upgrade guide.'
else
  ln -s "$PWD/deepseek-delegate" "$BUDDY_SKILLS_DIR/deepseek-delegate"
fi
```

Keep the checkout available while using that link. For a copied installation, copy the whole directory, including its scripts, Python package, plugins, references and lock file. The [usage guide](deepseek-delegate/references/usage.md) also covers the Codex plugin, where the entrypoint is `$buddy`; the standalone entrypoint is `$deepseek-delegate`.

Existing installations should follow the [upgrade guide](deepseek-delegate/references/operations.md#database-upgrade) before using 0.5.0. Schema 5/6 requires an explicit offline migration with a verified backup. If both skill entrypoints are installed, keep them on the same release.

Start a new Codex task and ask:

> Use $deepseek-delegate to check relative links in this repository's README and docs. Work in an isolated worktree, add only buddy-doc-review.md, and list broken links with suggested fixes. Set workspace: false to disable DSH session grouping for this run. Verify the report and give me the fixed artifact path.

Session grouping is separate from execution-workspace isolation. It is on by default and needs the [DSH workspace bridge](deepseek-delegate/references/operations.md#workspace-bridge); this example explicitly opts out of grouping.

## Console and model selection

From the checkout, open the private local console:

```sh
BUDDY="$PWD/deepseek-delegate/scripts/launch-buddy.sh"
"$BUDDY" console
```

Open the returned URL to inspect tasks, handle assistance requests and review fixed artifacts. The same console lets you discover installed model configurations, enable profiles, set preferences and maintain evaluation cards shared across projects. Viewing or refreshing the page makes no model calls.

You explicitly choose the initial decision profile. When comparison is useful, a decision Buddy can recommend a legal model/effort configuration from the bounded current table. The Host authorizes the actual task. Evaluation maintenance is explicitly requested; automatic adoption of valid card updates is opt-in. See [evaluations and configuration](deepseek-delegate/references/evaluation.md) for setup, evidence and editing rules.

## Execution and recovery

A wait ending or its terminal closing does not cancel the task. Reconnect using its saved run ID; execution remains bounded by its own deadline (30 minutes by default, up to 24 hours per attempt). A continuation receives a new attempt and fresh session. Work that outlives the Host turn needs the [background follow-up flow](deepseek-delegate/references/usage.md#background-work-that-outlives-the-turn); native immediate App wakeup is not provided.

The service and Workers run from a stable private runtime so plugin-cache replacement does not interrupt them. The first start prepares that runtime. A working directory and a Git worktree are not OS sandboxes: tasks retain the local user's access, and shared services or repository metadata still need coordination.

Only DSH is integrated as a coding Buddy today. Additional harnesses, automatic community research, monetary budgets and periodic evaluation maintenance are not implemented. Task coordination stays local; model requests go to the configured provider.

## Documentation and development

Start with the [documentation index](docs/README.md) for commands, architecture, adapters and design history. The [0.5.0 acceptance record](docs/acceptance/productivity-workflow-0.5.0.md) separates real DSH/browser evidence from implementation claims. Contributors should follow [AGENTS.md](AGENTS.md); end users do not need npm or a frontend build. Report problems through [GitHub issues](https://github.com/Dsssyc/hey-my-buddy/issues).

## License

[MIT](LICENSE). The standalone `deepseek-delegate/` directory includes its own copy.
