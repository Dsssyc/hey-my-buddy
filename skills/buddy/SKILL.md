---
name: buddy
description: Delegate bounded work with routing and owned workspaces; verify artifacts. Host handles trivial or unclear work.
---

# Hey My Buddy

Host owns acceptance and authorization; Workers cannot create goals.

## Start here

Use the absolute launcher (`~/.agents/skills/buddy/scripts/buddy`; Windows `scripts/buddy.cmd`) with one JSON object: positional, `--params-file PATH` or UTF-8 stdin `-`. `buddy help [METHOD]` reads validated parameters, starts nothing. Brief; use `"output":"full"`.

Read `health` for the Router list, mode and capacity; report failures with decision code and `routingMode`. A sole candidate needs no Router: fast sends the task to its provider, review reads a frozen repository copy.

[usage](../../docs/reference/usage.md), [CLI](../../docs/reference/cli.md), [workflow](../../docs/reference/workflow.md), [harnesses](../../docs/reference/harnesses.md), [Claude](../../docs/reference/claude.md).

## Delegate through acceptance

1. One delegation, one independently reviewable outcome: about a module or concern, with its own checks; mechanical apart from diagnosis. Define inputs, outcome and checks in the user's language; new files need `includeUntracked`. Use `executionWorkspace`: `existing` for sequential ownership, separate `worktree` checkouts for parallel writers; neither is an OS sandbox. `access`: `read`, `write` (default) or `full` with `accessReason`.
2. Omit `adapter`/`provider`/`model`/`effort` for routing; a complete tuple is only for an explicit user choice or goal-boundary recovery. One agenda, one objective: the first delegation carries `objective: {title}` and an intent `title` (≤30 chars), later ones its `objectiveId` or `objectiveOf: "<runId>"`. Submit once with a stable `requestId`; keep `runId`/`objectiveId`/`controlFile`.
3. Before waiting, submit delegations independent of the running ones (no shared files, no needed result); the board queues them. Wait through your Host guide ([codex](../../docs/reference/host-codex.md), [claude](../../docs/reference/host-claude-code.md)); read only yours. One `await` per running goal; a timeout never cancels it; unknown never means stopped.
4. At a boundary `decide` on helpers or `continue` with input; a routing one takes `reroute:true` (first choice when `routing-changed`) or a legal configuration with a reason. An enabled configuration may change with a reason unless `configurationLocked`; partial work is unverified. [Recovery](../../docs/reference/workflow.md).
5. Verify changed paths, risky hunks and checks on the fixed artifact, then `accept` the exact sealed `artifactId` (`finalArtifactId` when delivered) with a `note` and the integration `target`, or `notRequired` with a reason: it verifies the integration and reclaims the checkout. Prose and RPC replies are not acceptance. A waiting Host may finish directly; `conclude` ends a failed, cancelled or unaccepted goal with a note, and `reclaim` retries a blocked checkout removal.

`buddy console` opens loopback, login off; `{"browser":false}` returns its URL ([console](../../docs/reference/console.md)).

Before install, run `backup-preflight '{}'`; resolve refusals ([operations](../../docs/reference/operations.md)).

## Capabilities and authority

[Harnesses](../../docs/reference/harnesses.md) own capabilities. Shared login is read-only.

Worker sessions stay private; `worker-sessions` lists them ([details](../../docs/reference/operations.md)).

Preferences, enablement, capacity and Router settings are user-owned: change them only in the console, never in this skill. Routing and execution share capacity and family limits; native permissions need Host attention.

Model-card updates only on user request: `evaluation-prepare`, then a short maintenance grant ([maintenance](../../docs/reference/evaluation-maintenance.md)). Only an explicit user background request authorizes a Harness scheduler; never create, mute or remove one to save quota.

`quota-redetect` opens one retry; reuse its `requestId` after an unknown reply ([harnesses](../../docs/reference/harnesses.md)).
