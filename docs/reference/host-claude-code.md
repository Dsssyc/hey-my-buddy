# Waiting from Claude Code

This page adds Claude Code's own way of waiting to the [Host guide](host.md), which a Claude Code Host reads first: the background `await`, the launcher's sandbox allowance and the session-wakeup boundary. The flow was observed in both the Claude Code CLI and the Claude Code app ([known limits](#known-limits)).

## One background `await` per running micro task

Claude Code runs each wait as its own background command and keeps working:

1. Run `"$HMB" await '{"runId":"<runId>"}'`, where `HMB` holds the absolute launcher path, with the Bash tool's `run_in_background: true`. Start one background wait per running micro task; concurrent micro tasks each have their own.
2. Continue with other Host work: submit the micro tasks that are independent of the running ones ([usage](usage.md#more-than-one-task-at-a-time)) and review micro tasks that already finished. The harness delivers a notification when the command exits: during an active turn it arrives with the next step, and an idle session is woken. The notification carries the brief `await` reply.
3. Act on its `outcome` as the [Host guide](host.md#one-wait-per-running-micro-task) says. Where it says to wait again, after a reminder, a `wait-timeout` or `unavailable`, start another background `await` for the same `runId`.

## Launcher sandbox allowance

The launcher must run outside the Host's sandbox ([Host guide](host.md#the-launcher-outside-the-sandbox)). In Claude Code, the `sandbox.excludedCommands` setting in `~/.claude/settings.json` takes a launcher call out of the sandbox, and a `permissions.allow` rule with the same pattern removes its permission prompt; the two are separate layers ([Claude sandboxing](https://code.claude.com/docs/en/sandboxing), [settings](https://code.claude.com/docs/en/settings)). Both match the text of the call, so the pattern is the launcher path exactly as the Host writes it, followed by ` *`: `~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy *` for the `~` form, or the same path with the home directory spelled out if the Host writes that; a call through another path, with a prefix such as `env`, or with a redirect, `cd` or command substitution stays sandboxed.

## Known limits

- The background `await` wakes the session when it ends: the exit notification arrives with the next step during an active turn and wakes an idle session. Observed on 2026-10-14 both in the Claude Code app (its version was not recorded) and in the Claude Code CLI 2.1.284, where an idle session answered on its own when its background command finished.
- The background command belongs to the Claude Code session: if the session or app closes, the micro task keeps running and is resumed by awaiting the same `runId` later. This is a bounded wait, not a scheduler, and it gives no wakeup to a session that no longer exists. A question the Worker raises while no `await` runs reaches the Host only through the next `await` or `get`.
- The Host launcher clears model endpoint overrides such as `ANTHROPIC_BASE_URL` before starting a service, and native children receive the service's environment allowlist. Native provider configuration remains with the harness ([operations](operations.md#launcher-sandbox-and-environment), [Claude](claude-code.md)).
