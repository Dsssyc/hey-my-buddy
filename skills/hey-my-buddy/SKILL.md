---
name: hey-my-buddy
description: Delegate bounded work with routing and owned workspaces; verify artifacts. Host handles trivial or unclear work.
---

# Hey My Buddy

The submitting session is the Host: it owns acceptance and authorization; Workers cannot create micro tasks.

## Start here

Use the absolute launcher (`~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy`; Windows `scripts/hey-my-buddy.cmd`) with one JSON object: positional, `--params-file PATH` or UTF-8 stdin `-`. `hey-my-buddy help [METHOD]` reads validated parameters, starts nothing. Brief; use `"output":"full"`.

Read `health` for the Router list, mode and capacity; report failures with decision code and `routingMode`. A sole candidate needs no Router: fast sends the task to its provider, review reads a frozen repository copy.

[usage](../../docs/reference/usage.md), [CLI](../../docs/reference/cli.md), [workflow](../../docs/reference/workflow.md), [harnesses](../../docs/reference/harnesses.md), [Claude](../../docs/reference/claude-code.md).

## Delegate through acceptance

1. One micro task, one independently reviewable outcome: about a module or concern, with its own checks; mechanical apart from diagnosis. Define inputs, outcome and checks in the user's language; new files need `includeUntracked`. Use `executionWorkspace`: `existing` for sequential ownership, separate `worktree` checkouts for parallel writers; neither is an OS sandbox. `access`: `read`, `write` (default) or `full` with `accessReason`.
2. Omit `harness`/`provider`/`model`/`effort` for routing; a complete buddy is only for an explicit user choice or boundary recovery. One agenda, one macro task: its first micro task carries `macroTask: {title}` and an intent `title` (≤30 chars), later ones its `macroTaskId` or `macroTaskOf: "<runId>"`. Submit once with a stable `requestId`; keep `runId`/`macroTaskId`/`controlFile`.
3. Before waiting, submit micro tasks independent of the running ones (no shared files, no needed result); the board queues them. Wait through your Host guide ([codex](../../docs/reference/host-codex.md), [claude](../../docs/reference/host-claude-code.md)); read only yours. One `await` per running micro task; a timeout never cancels it; unknown never means stopped: on `EXECUTION_UNKNOWN` tell the user; `cancel` ends it.
4. At a boundary `decide` on helpers or `continue` with input; a routing one takes `reroute:true` (first choice when `routing-changed`) or a legal `buddy` with a reason. An enabled buddy may change with a reason unless `buddyLocked`; partial work is unverified. [Recovery](../../docs/reference/workflow.md).
5. Verify changed paths, risky hunks and checks on the fixed artifact, then `accept` the exact sealed `artifactId` (`finalArtifactId` when delivered) with a `note` and the integration `target`, or `notRequired` with a reason: it verifies the integration and reclaims the checkout. Prose and RPC replies are not acceptance. A waiting Host may finish directly; `conclude` ends a failed, cancelled or unaccepted micro task with a note, and nothing continues it afterwards; `reclaim` retries a blocked checkout removal.

`hey-my-buddy console` opens loopback, login off; `{"browser":false}` returns its URL ([console](../../docs/reference/console.md)).

Install on the user's word; a new middle version needs idle work and a clean `backup-preflight '{}'` ([operations](../../docs/reference/operations.md)).

## Capabilities and authority

[Harnesses](../../docs/reference/harnesses.md) own capabilities. Shared login is read-only.

`worker-sessions` lists the private Worker sessions ([details](../../docs/reference/operations.md)).

A Lead (`delegationAuthority`) only at the user's explicit request, confirmed first ([usage](../../docs/reference/usage.md#a-lead-for-a-large-macro-task)).

Preferences, enablement, capacity and Router settings are user-owned: change them only in the console. Routing and execution share capacity and model service limits; native permissions need Host attention.

`quota-redetect` opens one retry ([harnesses](../../docs/reference/harnesses.md)).
