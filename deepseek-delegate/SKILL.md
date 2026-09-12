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

```sh
node <skill-dir>/scripts/run.mjs --cwd <dir> --task-file <file> \
  [--model <id>] [--provider <id>] [--effort <name>] [--timeout <seconds>] \
  [--log-dir <parent>] [--dsh-bin <path>] [--settings-file <path>] \
  [--dsh-web-url-file <file>] [--web-timeout <seconds>] [--no-workspace]
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

Grouping is enabled by default. The local DSH Web service must be running and share the headless
session storage. Its full startup URL (with the **Web token**, not the model API key) belongs in a
private `${XDG_CONFIG_HOME:-~/.config}/deepseek-delegate/web-url` file, or an explicit
`--dsh-web-url-file`. Never put credentials in a task packet or print them.

Authentication is checked before the model run. The canonical cwd determines the workspace. A
read-only observer captures the exact root session; after headless fully stops, the running host
binds it and returns verified membership. Do not write DSH workspace storage directly from another
process. Grouping is visible after completion. Inspect `workspace.bound` as well as the child result:
a grouping failure preserves the task output but makes the CLI exit nonzero.

Use `--attach-session <id> --cwd <dir>` to retry binding an existing completed session without
rerunning the task. This probes existence first. For disposable tests or intentionally standalone
work, explicitly use `--no-workspace`; never silently downgrade a requested grouped run.

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
