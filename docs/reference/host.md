# Waiting as a Host

This page owns how any Host waits on its micro tasks and finds open work again. Codex and Claude Code each add a page for the native mechanism they use to wait ([Codex](host-codex.md), [Claude Code](host-claude-code.md)); every other Host follows this page alone. The common create → submit → await → continue → accept path is in [usage.md](usage.md).

## One wait per running micro task

`await` with a `runId` returns when that micro task reaches a boundary, delivers, sends a reminder or the wait window ends; it never starts work and never cancels it. Run one `await` per running micro task. If your harness can run a command in the background and wake you when it exits, run each `await` that way and keep working; otherwise run it in the foreground, where it holds your turn. Give a foreground `await` a `waitSeconds` shorter than your command tool's own time limit, so that the wait ends before the tool gives up on it.

Act on the reply's `outcome`, using the ids it carries:

- `delivered`: read the full `get`, check the Worker's report and the fixed output against the brief, then `accept` the `artifactId` or send it back with `continue`.
- `waiting-host`: answer its `requestId` with `continue`.
- `expected-duration` or `native-silence` is a reminder: look at `get`, then wait again.
- `wait-timeout` or `unavailable` ends only that wait: wait again for the same `runId`.
- Any other outcome is a stopped micro task: read the full `get` and act on its `actions`.

Answer exactly the ids a reply gives, never ones remembered from an earlier reply. If a reply is lost, send the same request again, whatever the command: it returns the original instead of acting twice. Do not poll `get` beside a wait.

## Before the turn ends

Unless your harness wakes an idle session when a background command ends, nothing wakes a Host after its turn has ended, so the turn does not end while a micro task this Host submitted is still running or waiting on the Host. Before the final reply, ask the blackboard instead of trusting memory, which a context compaction may have dropped: `get` with the macro task's `macroTaskId` lists its micro tasks with their states, and `get` with the project's `cwd` lists the project's macro tasks with their `state` and `counts` and its standalone micro tasks with their `state` and `actions`. The board does not record which Host submitted a micro task, so the project's list includes the work of other sessions. Wait on the micro tasks that are still running; continue, accept or conclude the ones that wait. A wait that ends without an outcome to act on is not a reason to end the turn. When the turn has to end anyway — the user asked for a stop, the next step needs the user, or the user asked for the work to [outlive the turn](usage.md#background-work-that-outlives-the-turn) — the final reply names each micro task left open with its `runId` and what it waits for.

## The launcher outside the sandbox

A cold start writes the private state directory and opens local IPC, and every later call reaches the service over local IPC; a Host sandbox that refuses either gets `LAUNCH_ACCESS_DENIED` from the launcher ([operations](operations.md#launcher-sandbox-and-environment)). Allow the exact absolute launcher command through your harness's own setting for running a command outside its sandbox. Have the user or the Host's owner apply that setting; do not disable the sandbox globally and do not change Host settings for the user. A service that is already running keeps its own permissions; attaching to it does not re-sandbox it.
