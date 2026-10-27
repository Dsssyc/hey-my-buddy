# Waiting from Claude Code

This page owns the Claude Code Host's way of waiting on a micro task: the background-`await` flow, the launcher's sandbox allowance and the session-wakeup boundary. The common submit → await → decision → accept path is in [usage.md](usage.md); every other Host reads only its own guide.

## One background `await` per running micro task

Claude Code runs each wait as its own background command and keeps working:

1. `submit` the micro task and keep `runId` and `controlFile`.
2. Run `"$HMB" await '{"runId":"<runId>"}'`, where `HMB` holds the absolute launcher path, with the Bash tool's `run_in_background: true`. Start one background wait per running micro task; concurrent micro tasks each have their own.
3. Continue with other Host work: submit the micro tasks that are independent of the running ones ([usage](usage.md#more-than-one-task-at-a-time)) and review micro tasks that already finished. The harness delivers a notification when the command exits: during an active turn it arrives with the next step, and an idle session is woken. The notification carries the brief `await` envelope.
4. At the notification act on the envelope's `outcome`: for a delivered result read `get`, verify the artifact and `accept` it, or send it back with `continue`; for `waiting-host` answer with `decide` or `continue`; a reminder (`microTaskComplete: false`, with a `reminder`) means look at `get` and start another background `await`. An `outcome` of `wait-timeout` or `unavailable` ends only that wait; start another background `await` for the same `runId`.

Do not add a polling loop of `get`, `status` or `wait` beside the background wait.

## Launcher sandbox allowance

A cold start must write the private state directory and open local IPC; a Host sandbox that refuses either gets `LAUNCH_ACCESS_DENIED` from the launcher ([operations.md](operations.md)). In Claude Code, the command-specific `sandbox.excludedCommands` setting can include `"/absolute/path/to/.claude/skills/hey-my-buddy/scripts/hey-my-buddy *"` for the exact absolute launcher command, while `permissions.allow` with a matching `Bash(...)` rule is a separate permission layer ([Claude sandboxing](https://code.claude.com/docs/en/sandboxing), [settings](https://code.claude.com/docs/en/settings)). Have the user or Host owner apply these settings; do not disable the sandbox globally and do not change Host settings for the user. A service that is already running keeps its own permissions; attaching to it does not re-sandbox it.

## Known limits

- The background `await` wakes the session when it ends: the exit notification arrives with the next step during an active turn and wakes an idle session. Observed on 2026-10-14 both in the Claude Code app (its version was not recorded) and in the Claude Code CLI 2.1.284, where an idle session answered on its own when its background command finished.
- The background command belongs to the Claude Code session: if the session or app closes, the micro task keeps running and is resumed by awaiting the same `runId` later. This is a bounded wait, not a scheduler, and it gives no wakeup to a session that no longer exists.
- The Host launcher clears model endpoint overrides such as `ANTHROPIC_BASE_URL` before starting a service, and native children receive the service's environment allowlist. Native provider configuration remains with the harness ([operations](operations.md#launcher-sandbox-and-environment), [Claude](claude-code.md)).
