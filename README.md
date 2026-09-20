# hey-my-buddy

[中文文档](README.zh-CN.md)

Buddy lets Codex hand **one clear, bounded task** to a local
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`) run and get
back real work you can inspect. Codex still frames the task, chooses the route and
checks the result; dsh does the bulk execution.

## Why it is useful

- **Work you can inspect.** Give it a working directory and a task with
  acceptance criteria; it edits files, runs commands and leaves the result on disk.
- **Long tasks keep going.** Closing the terminal or a wait ending does not throw the
  work away: Codex can pick the same task up again and report its progress or result.
- **You stay in charge.** The run uses your own credentials and permissions, and Codex
  inspects the actual files and checks before calling anything done.

## What to delegate

Good fits: investigate a failing test or a reproducible bug; implement a scoped feature
or refactor; run a batch file transformation; update documentation for a set of files;
produce a calculation or report with a verifiable output.

Keep it in Codex: one-line edits, answers you already know, work whose requirements are
still moving, or anything that needs your framing and final judgement.

## Requirements

- macOS or Linux (POSIX).
- [uv](https://docs.astral.sh/uv/) with Python 3.12–3.14, and Node.js 20+ for the
  default dsh route.
- A working local `dsh` installation with credentials you configured yourself:
  <https://github.com/deepseek-ai/deepseek-harness>.
- Codex with skills support.

Buddy does not install dsh, configure provider credentials or change global model
settings.

## Get started (0.4.0 branch)

This 0.4.0 code lives on the `socu/python-blackboard` branch and is not merged to `main`
yet, so clone that branch explicitly:

```sh
git clone --branch socu/python-blackboard https://github.com/Dsssyc/hey-my-buddy.git
cd hey-my-buddy
uv sync --frozen --project deepseek-delegate --python 3.12
```

Install the whole `deepseek-delegate/` directory as a standalone Codex skill (copy all of
it, not only `SKILL.md`):

```sh
BUDDY_SKILLS_DIR="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$BUDDY_SKILLS_DIR"
if [ -e "$BUDDY_SKILLS_DIR/deepseek-delegate" ] || [ -L "$BUDDY_SKILLS_DIR/deepseek-delegate" ]; then
  printf '%s\n' 'A skill already exists at this path; see the upgrade guide.'
else
  ln -s "$PWD/deepseek-delegate" "$BUDDY_SKILLS_DIR/deepseek-delegate"
fi
```

For an existing installation, see the [upgrade guide](deepseek-delegate/references/operations.md#runtime-lifecycle-and-upgrade).
The [usage guide](deepseek-delegate/references/usage.md) covers copies, plugin installation
and workspace grouping.

In a new Codex task, ask:

> Use `$deepseek-delegate` to check the relative links in this repository's README and
> docs. Create only `buddy-doc-review.md`, listing any broken links and suggested fixes.
> Disable dsh workspace grouping for this run (`workspace: false`). Verify the report
> when the task finishes and show me the result.

`$deepseek-delegate` is the standalone skill; `$buddy` is the same skill when the
repository is installed as the Codex plugin (details in the usage guide).

## What happens

Codex starts the task once and keeps it running while it waits in the same turn. You can
ask what the run is doing; when it returns you get the outcome plus where the logs and
artifacts are. Codex inspects the actual files, runs the relevant checks and reports
what it verified.

If the wait ends first, the task continues under its execution deadline. Codex can
reconnect using the saved run ID. For work that should outlive the current turn, the
[background workflow](deepseek-delegate/references/usage.md#background-work-that-outlives-the-turn)
uses periodic App follow-up; immediate continuation after the turn ends is unavailable.

## Good to know

- Workspace grouping is on by default and needs a small one-time host bridge; the first
  example uses `workspace: false` so it runs without that setup. The default never
  changes by itself — see [operations](deepseek-delegate/references/operations.md).
- Tasks default to a 30-minute execution deadline. Specify a longer limit in your
  request for long jobs; the maximum is 24 hours.
- State which files may change. A working directory does not restrict file access;
  separate worktrees help keep concurrent edits apart.
- Buddy manages only the runs it started; independent dsh sessions are untouched.
- Task coordination and stored results are local. Model requests use the provider
  configured in dsh. Buddy needs no MCP registration.
- The first run may take longer because it prepares a private runtime for the service.

## Documentation and support

- [docs/README.md](docs/README.md) — every document and the role it plays.
- Detailed topics: [usage](deepseek-delegate/references/usage.md),
  [CLI](deepseek-delegate/references/cli.md),
  [operations](deepseek-delegate/references/operations.md),
  [workers](deepseek-delegate/references/workers.md),
  [runner](deepseek-delegate/references/runner.md) and
  [architecture](deepseek-delegate/references/architecture.md).
- Bugs and support: [GitHub issues](https://github.com/Dsssyc/hey-my-buddy/issues).
- Contributions are welcome; [AGENTS.md](AGENTS.md) lists the checks to run first.
- Design history: [ADR-001](docs/decisions/001-python-transactional-blackboard.md) and
  the [0.4.0 acceptance record](docs/acceptance/python-blackboard-0.4.0.md).

## License

[MIT](LICENSE). The standalone `deepseek-delegate/` directory carries its own copy.
