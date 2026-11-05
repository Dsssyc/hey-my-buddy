---
name: hey-my-buddy
description: Delegate bounded pieces of your current work — implementing, fixing tests, investigating, reviewing — to other coding agents, each routed to a suitable model, then verify and accept the result. Use it whenever such a piece can run while you continue or suits another model better; do trivial or still-unclear steps yourself.
---

# Hey My Buddy

The submitting session is the Host: it owns acceptance and authorization; Workers cannot create micro tasks. The project's files and history use its own terms, not the board's (roles, mechanisms, records).

## Start here

Use the absolute launcher (`~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy`; Windows `scripts/hey-my-buddy.cmd`) with one JSON object: positional, `--params-file PATH` or UTF-8 stdin `-`. `hey-my-buddy help [COMMAND]` gives the commands, fields and legal combinations; it starts nothing. Replies name the next ids and legal commands; `"output":"full"` shows all.

Installing, health, storage and relocation are the user's operations ([operations](../../docs/reference/operations.md#host-operations)).

## Delegate through acceptance

1. One agenda, one macro task: `create` it with `cwd`, a short `title` and the `goal`. `amend` revises the goal. A piece outside any agenda is `"macroTaskId":"standalone"`.
2. One micro task, one independently reviewable outcome: one concern with its own checks; mechanical apart from diagnosis. Write the brief in the user's language: inputs, outcome, checks and the plan to follow; the Worker stops and asks instead of deciding alone or working around ([brief](../../docs/reference/workflow.md#writing-the-brief)). `submit` names every choice: `buddy` is `route` unless the user named one; `checkout` is `worktree` for parallel writers or `existing` for sequential work in your Git checkout, or `none` to work in `cwd` itself, sealing nothing; `access` is `read`, `write` or `full` with an `accessNote`. A Git checkout also takes `inputs`, the untracked files it carries (`[]` if none), and with `write` or `full` a narrow `writeScope`. Resending `create` or `submit` returns the original; to do it again, change the `title`.
3. Submit independent micro tasks before waiting; the board queues them. Wait as the [Host guide](../../docs/reference/host.md) says; Codex and Claude Code add theirs ([codex](../../docs/reference/host-codex.md), [claude](../../docs/reference/host-claude-code.md)). One `await` per running micro task; unknown never means stopped: on `EXECUTION_UNKNOWN` tell the user. `inquire` asks a running turn.
4. At a boundary `continue` its `requestId` with input or a helper decision. To hand off or reroute running work, `cancel` it, then `continue` the stopped turn's `artifactId` or `turnId` with input, a `buddy` and a `note`.
5. Reread the brief (full `get`); check the report and the fixed output against it: changed paths, risky hunks, checks. Integrate the Worker's commits or the fixed patch, then `accept` the `artifactId` the reply gives, with a `note`, the `target` and, unless its tip is your merge, its `beforeCommit`, plus `adjusted` if you changed the result; or with `notRequired` and its reason, always for `checkout: none`. To send work back, `continue` its `artifactId` with what must change. Prose and RPC replies are not acceptance. `conclude` ends failed, cancelled or unaccepted work for good.

`hey-my-buddy console` opens the [console](../../docs/reference/console.md) on loopback.

## Capabilities and authority

[Harnesses](../../docs/reference/harnesses.md) own capabilities. Sign-in stays the user's. `access` limits writing and execution, not reading: a Worker reads what its harness allows, a checkout is no OS sandbox, and the brief and what the Worker sends its model reach the provider; keep secrets out of the brief.

A Lead (`delegationAuthority`) only at the user's explicit request, confirmed first ([usage](../../docs/reference/usage.md)).

Preferences, enablement, capacity and Router settings are user-owned: change them only in the console.
