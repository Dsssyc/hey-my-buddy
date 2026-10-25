<p align="right"><strong>English</strong> · <a href="README.zh-CN.md">简体中文</a></p>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/banner-dark.svg">
  <img alt="hey-my-buddy" src="docs/assets/banner-light.svg" width="100%">
</picture>

<p align="center"><b>Delegate the work. Keep the responsibility.</b></p>

<p align="center">
  The coding agent you work with hands bounded tasks to other coding agents on your machine,<br>
  and checks every sealed result before it lands.
</p>

<p align="center">
  <a href="#how-it-works">How it works</a> ·
  <a href="#install">Install</a> ·
  <a href="#first-steps">First steps</a> ·
  <a href="#status-and-limits">Status</a> ·
  <a href="#documentation">Docs</a>
</p>

---

## Why hey-my-buddy

- **The agent you trust stays in charge.** The coding agent you work with, such as Codex or Claude Code, is your Host buddy: it splits the work, delegates the bounded parts and accepts every result itself.
- **The right buddy for each part.** Workers run in DeepSeek Harness, ZCode, Codex or Claude Code, with the models and efforts you enabled. Routing picks one, or the Host names it.
- **Nothing lands unchecked.** Each micro task works in its own checkout with a fixed input and a sealed output; the Host reads the diff and runs the checks before it accepts.
- **Work outlives your session.** A closed terminal, a lost connection or a service restart never cancels work; pick it up again by its id.
- **See who is doing what.** A private local console shows each macro task as a timeline, every buddy's load and your settings.
- **Your accounts, your plans.** Workers sign in with your own harness accounts; hey-my-buddy never keeps or forwards your credentials. See [accounts and terms](docs/terms.md).

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/architecture-en-dark.svg">
  <img alt="The Host buddy delegates a micro task to the blackboard; unless the Host names a buddy, a Router buddy picks a Worker buddy, which runs a turn in its own checkout; the Host can ask that running turn a question through the blackboard; the blackboard seals the checkout when the turn ends; the Host checks the Worker's report against the seal and either integrates and accepts it into your repository or sends it back to the same Worker without routing. A Lead buddy runs the same loop as Host for the micro tasks it delegates." src="docs/assets/architecture-en-light.svg" width="100%">
</picture>

1. **Delegate.** The Host writes a micro task: what to do, which files it may change, and how to check it. Unless the Host names a buddy, a Router buddy picks one.
2. **Work.** The Worker runs in its own harness and checkout. The Host can ask the running turn a question through the blackboard without stopping it; when the Worker needs a decision or help, it stops and asks, and the Host answers or continues it.
3. **Accept.** The Host reviews the sealed result. It either integrates and accepts it, which verifies the integration and reclaims the checkout, or sends it back to the same micro task for another turn; work it gives up on is concluded.

Micro tasks, decisions and results live on a local blackboard, a SQLite database owned by one private service. More in [workflow](docs/reference/workflow.md) and [architecture](docs/reference/architecture.md).

## Install

hey-my-buddy is one shared agent skill at `~/.agents/skills/hey-my-buddy` that carries its own launcher, plus a private service and runtime under `~/.local/share/hey-my-buddy`. Claude Code finds the skill through a link at `~/.claude/skills/hey-my-buddy`. The install never changes your `PATH`, shell or agent settings. It needs [uv](https://docs.astral.sh/uv/).

It is not published yet. Build the wheel from this repository and install it:

```sh
uv build --wheel --out-dir dist
uvx --from "$PWD/dist/hey_my_buddy-0.53.1-py3-none-any.whl" hey-my-buddy install
```

Once a release is published, the same install is one command:

```sh
uvx hey-my-buddy@0.53.1 install
```

<details>
<summary>Or let your agent install it</summary>

```text
Install hey-my-buddy on this machine for me.

1. Use the release version or the wheel I give you; do not assume it is on PyPI.
2. Run the install outside your sandbox through your own permission mechanism, without sudo.
3. Before it writes anything, list every path it will create or replace.
4. Report the skill version and place, the Claude Code link, the service action and whether a backup was taken.
5. Verify with ~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy health.
6. On LAUNCH_ACCESS_DENIED, allow that exact launcher command and retry. On UPGRADE_NOT_IDLE, report the listed work and wait; never cancel work to force an upgrade.
```

</details>

Upgrading runs the same command with the newer version. An upgrade that changes the contract refuses while work is running and takes a verified backup before it switches. [Operations](docs/reference/operations.md#installation) has the details, error codes and recovery.

## First steps

Ask your Host buddy in plain words, for example:

> Use hey-my-buddy to have a buddy check the links in this README and report the broken ones.

The skill tells the Host the rest. To look around yourself:

```sh
~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy health    # routing and capacity
~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy console   # open the private console
```

In the console, enable the buddies you want to work with and set how many may run at once. [Usage](docs/reference/usage.md) walks from install to a reviewed result; the [CLI reference](docs/reference/cli.md) covers every command.

## Status and limits

- **Pre-release.** Nothing is published yet, and while the version is 0.x the contract can change between middle versions.
- **Platforms.** Used every day on macOS; the full test suite passes on Linux in CI. Windows 11 support (x64 and ARM64) is being finished for the first release.
- **Harnesses.** Codex runs through its experimental App Server. What each harness supports and how far it is verified is in [harnesses](docs/reference/harnesses.md).
- **Routing sees your task.** When routing chooses the buddy, the task text goes to the Router's model provider, even if another model does the work.
- **Not a sandbox.** Checkouts and private directories keep work apart; they are not an operating-system sandbox. A Worker acts with the permissions its harness grants.
- **No spending limits.** Usage counts against your own plans and keys; hey-my-buddy does not track money.

## Documentation

- [Documentation index](docs/README.md)
- [Operations](docs/reference/operations.md): install, upgrade, storage, error codes and recovery
- [Usage](docs/reference/usage.md) and [CLI](docs/reference/cli.md)
- [Console](docs/reference/console.md) and [harnesses](docs/reference/harnesses.md)
- [Architecture](docs/reference/architecture.md) and [decisions](docs/decisions/)

## Development

```sh
npm --prefix apps/console ci
uv run --frozen python -m hey_my_buddy.cli.checks
npm --prefix apps/console test
```

[AGENTS.md](AGENTS.md) covers the repository's invariants, layout and checks. Report problems in [GitHub issues](https://github.com/Dsssyc/hey-my-buddy/issues).

## License

[MIT](LICENSE)
