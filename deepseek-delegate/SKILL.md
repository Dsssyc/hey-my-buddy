---
name: deepseek-delegate
license: MIT
description: Delegate clear, bounded implementation, investigation, file-transformation, testing, or documentation work to a local DeepSeek Harness (dsh) run early, then verify the real artifacts. Use when a task is well scoped and clearly bigger than a one-line edit; skip trivial one-step tasks and work whose requirements are still moving.
---

# deepseek-delegate

Hand **one bounded task** to a local `dsh` headless run and get back **one compact JSON result**.
Codex keeps framing, route choice, and final acceptance; dsh does the bulk work.

## Route early

Delegate before doing the work yourself when the task is clear, bounded, and bigger than a
one-line edit: implementation, refactor, bounded investigation, batch file transformation, test
writing, documentation.

Keep it local when the change is a line or two, the answer is already known, requirements still need
discovery, or the call is Codex-only (framing, route choice, accepting the result).

## Write the task packet first

Put the packet in a file and include: goal and why; inputs/outputs; the working directory and
available check commands; the files/areas that may change and explicit non-goals; acceptance
criteria; and key references, known facts, and pitfalls.

Give the context the work needs. Context capacity is configured per model and provider — some models
in a dsh catalog are configured around 1M tokens, which is an example, not a promise across
providers. Do not shrink the packet artificially. Keep large background in files: task content over
32,000 bytes is delivered to dsh as a file reference, and that file must stay in place until the run
finishes.

## Pick the model and effort

- Choose a model for the task's difficulty and needs; never rank by Pro/Flash in the name.
- Defaults are provider `deepseek-official`, model `deepseek-flash`, effort `max`. They are
  configurable defaults, not a capability ranking.
- Effort defaults to `max` and is never silently inherited from a lower value in settings.
- A model ID must be supported by the selected provider. The CLI does not validate, downgrade, or
  retry it; report failures honestly.
- Precedence: `--model` > `DSH_DELEGATE_MODEL` > settings `agent-default-model.model` >
  `deepseek-flash`; `--provider` > `DSH_DELEGATE_PROVIDER` > settings > `deepseek-official`;
  `--effort` > `DSH_DELEGATE_EFFORT` > `max`.

## Run it

Before launching work that may outlive the current turn, choose and establish an
owner-resumption route. Read [handoff.md](references/handoff.md): App/ChatGPT App
uses a verified heartbeat on the original task; CLI uses a completion callback
only after an actual same-thread wakeup test. Use `scripts/handoff.mjs` for these
routes; it wraps the runner below and persists results independently of notification.
If neither route is available, keep waiting in the current turn with `run.mjs`.
Do not end with only “delegated” while an unmonitored run is still active.

```sh
node <skill-dir>/scripts/run.mjs --cwd <dir> --task-file <file> \
  [--model <id>] [--provider <id>] [--effort <name>] [--timeout <seconds>] \
  [--log-dir <parent>] [--dsh-bin <path>] [--settings-file <path>] \
  [--workspace-socket <path>] [--workspace-timeout <seconds>] [--no-workspace]
```

- `<skill-dir>` is wherever this skill is installed (for example
  `$CODEX_HOME/skills/deepseek-delegate`); run `npm ci` there once. `--help` needs no dsh.
- stdout is exactly one JSON object: status, exit code, elapsed time, requested route and effort,
  inputDelivery, log paths, and `finalText` (at most 6000 characters) with a truncation flag.
  Nonzero/timeout/cancellation/spawn failure exits 1; usage and configuration errors exit 2.
- Retry only with new evidence and a narrower or corrected packet; normally stop after one or two failed attempts. Do not repeat identical requests.
- Long output stays in the private per-run logs. Read the log file when needed; do not paste whole
  logs or reasoning back into the conversation.
- Each run copies the settings document to a private temporary file, overrides only
  `agent-default-model`, and cleans it up. Original settings and credentials are untouched.

## Workspace grouping

Grouping is enabled by default. Install the bundled host plugin once:

```sh
node <skill-dir>/scripts/install-workspace-bridge.mjs
```

This adds a backed-up entry to the existing `web` profile's `cordis.patch.yml`.
Long-lived profiles hot-reload their user patch; otherwise start that profile normally.
The plugin calls the official `ctx.workspaceRegistry.create(cwd)` and
`workspace.attachSession(sessionId)` APIs inside the owning host. The CLI connects
through an owner-private Unix socket at `$DSH_HOME/deepseek-delegate/workspace.sock`
(default home `~/.dsh`). No Web URL, token, cookie, or HTTP endpoint is used.
Use `--workspace-socket` or `DSH_WORKSPACE_SOCKET` only for a custom host endpoint.
The host must mount workspace and persistence and share the headless session storage.
Do not mount a second workspace writer over storage already owned by another host.

The bridge is checked before a model run. A read-only observer captures the exact root
session; after headless fully stops, the host validates the persisted root header and cwd,
attaches it through the official API, and verifies membership. No Agent is activated for
binding. A binding failure preserves task output and makes the CLI exit nonzero.
Use `--attach-session <id> --cwd <dir>` to retry only binding of a completed session.
For intentionally standalone work, explicitly use `--no-workspace`; never silently downgrade.
Socket restarts use the same path without credential updates. An unclean host exit can leave
a stale socket; verify its owner process has stopped before removing that socket only.

Official contracts: [workspace](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/workspace.md),
[storage limits](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/storage/storage-json/README.md).
The socket bridge is this skill's adapter, not an upstream DSH CLI command.

## Scope and isolation

- `--cwd` is a working directory, not a sandbox: state the allowed files and limits in the packet.
- Delegation inherits the scope the user granted this session. When the user authorized publishing,
  pushing, or sending messages, pass that authorization explicitly in the packet; do not invent
  blanket bans the user never asked for.
- Do not let Codex and dsh edit the same files at the same time; work on something else while the
  run is in flight.

## Verify independently — never skip

- Exit 0 only means the dsh agent finished. It is not proof that the task is correct.
- Inspect the real diff, added/removed files, and command output; run the relevant checks (tests,
  build, lint, targeted reproduction) yourself.
- Check every acceptance criterion and state the evidence. Without evidence, the task is not done.
- On resumption, deduplicate by the persisted run ID and inspect the existing run;
  never start it again because a notification or result is delayed. After handling
  and verification, record acceptance with `handoff.mjs --run-dir <dir> --accept`
  and close the App heartbeat when applicable. Queue acceptance is not task acceptance.
