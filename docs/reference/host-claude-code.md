# Waiting from Claude Code

This page owns the Claude Code Host's way of waiting on a governed goal: the background-`await` flow, the launcher's sandbox allowance and the session-wakeup boundary. The common submit → await → decision → acknowledge path is in [usage.md](usage.md); every other Host reads only its own guide.

## One background `await` per running goal

The user chose this Claude Code Host flow on 2026-09-26. Claude Code runs each wait as its own background command and keeps working:

1. `submit` the goal and keep `runId` and `controlFile`.
2. Run `"$BUDDY" await '{"runId":"<runId>"}'` with the Bash tool's `run_in_background: true`. Start one background wait per running goal; concurrent goals each have their own.
3. Continue with other Host work, including reviewing goals that already finished. The harness delivers a notification when the command exits: during an active turn it arrives with the next step, and an idle session is woken. The notification carries the brief `await` envelope.
4. At the notification read `get`, verify the artifact, then `integration-record` and `acknowledge` as usual. An `outcome` of `wait-timeout` or `unavailable` ends only that wait; start another background `await` for the same `runId`.

Do not add a polling loop of `get`, `status` or `wait` beside the background wait.

## Launcher sandbox allowance

A cold start must write the private state directory and open local IPC; a Host sandbox that refuses either gets `LAUNCH_ACCESS_DENIED` from the launcher ([operations.md](operations.md)). In Claude Code, the command-specific `sandbox.excludedCommands` setting can include `"/absolute/path/to/.claude/skills/buddy/scripts/buddy *"` for the exact absolute launcher command, while `permissions.allow` with a matching `Bash(...)` rule is a separate permission layer ([Claude sandboxing](https://code.claude.com/docs/en/sandboxing), [settings](https://code.claude.com/docs/en/settings)). Have the user or Host owner apply these settings; do not disable the sandbox globally and do not change Host settings for the user. A service that is already running keeps its own permissions; attaching to it does not re-sandbox it.

## Known limits

- The background `await` wakes the session when it ends: the exit notification arrives with the next step during an active turn and wakes an idle session.
- The background command belongs to the Claude Code session: if the session or app closes, the goal keeps running and is resumed by awaiting the same `runId` later. This is a bounded wait, not a scheduler, and it gives no wakeup to a session that no longer exists.
- Claude Code exports `ANTHROPIC_BASE_URL` to its commands, so a service cold-started from a Claude Code session cannot run Claude Workers ([Claude](claude.md)); attaching to an already running service is unaffected.
