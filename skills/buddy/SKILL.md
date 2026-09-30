---
name: buddy
description: Delegate clear work with routing and owned workspaces; verify artifacts. Host handles trivial or unclear work.
---

# Hey My Buddy

Delegate when it lowers verification cost. Host owns acceptance and authorization; Workers cannot create goals.

## Start here

Use the absolute launcher (`~/.agents/skills/buddy/scripts/buddy`; Windows `scripts/buddy.cmd`) with one JSON object: positional, `--params-file PATH` or UTF-8 stdin `-`, mutually exclusive. `buddy help [METHOD]` reads validated parameters and starts nothing. Brief; use `"output":"full"`.

Review needs a version certificate. Read `health` for routing slots, mode and capacity; report failures with decision code and `routingMode`/`fallback`. A sole candidate needs no Router; fast sends the task to its provider, review reads a frozen repository copy.

References: [usage](../../docs/reference/usage.md), [CLI](../../docs/reference/cli.md), [workflow](../../docs/reference/workflow.md), [operations](../../docs/reference/operations.md), [harnesses](../../docs/reference/harnesses.md), [Claude](../../docs/reference/claude.md).

## Delegate through acceptance

1. Define inputs, outcome and checks in the user's language; new files need `includeUntracked`. Use `executionWorkspace`: `existing` for sequential ownership, separate `worktree` checkouts for parallel writers; neither is an OS sandbox.
2. Omit `adapter`/`provider`/`model`/`effort` for routing; a complete tuple is only for an explicit user choice or recovery at this goal's boundary. One agenda, one objective: the first delegation carries `objective: {title}` and an intent `title` (≤30 characters), later ones its `objectiveId` or `objectiveOf: "<runId>"`. Submit once with a stable `requestId`; keep `runId`/`objectiveId`/`controlFile`; use supported continuation/helper fields.
3. Wait for the same goal: Codex uses one monitoring-only native subagent per delegation ([codex](../../docs/reference/usage.md#waiting-from-codex)); Claude Code uses one background `await` ([claude](../../docs/reference/usage.md#waiting-from-claude-code)). A timeout never cancels it; unconfirmed shutdown never means stopped.
4. At a boundary `decide` on helpers or `continue` with input; a routing boundary takes a legal configuration with a reason, or `reroute:true`. An enabled configuration may change with a reason unless `configurationLocked`; partial work is unverified. See [recovery](../../docs/reference/workflow.md).
5. Verify changed paths, risky hunks and checks on the fixed artifact, then `integration-record` and `acknowledge` the exact sealed `artifactId` (`finalArtifactId` when delivered) and integration ID, or `notRequired` with a reason. Prose and RPC replies are not acceptance. A waiting Host may finish directly; failed/cancelled goals use `verdict:"recorded"` for a conclusion. Reclaim reviewed checkouts with `workspace-cleanup-plan`/`workspace-cleanup-apply`.

`buddy console` opens loopback, login off; `{"browser":false}` returns its URL ([console](../../docs/reference/console.md)).

Before install, run `backup-preflight '{}'`; resolve refusals ([operations](../../docs/reference/operations.md)).

## Capabilities and authority

Read [capabilities](../../docs/reference/harnesses.md); name unverified paths.

Worker sessions stay private (DSH groups only with `workspace:true`); `worker-sessions` lists them ([details](../../docs/reference/operations.md#worker-session-history)).

Preferences, enablement, capacity and Router settings are user-owned: change them only in the console, never by editing this skill. Routing and execution share capacity and family limits; native permissions need Host attention.

Model-card updates only on user request: `evaluation-prepare`, then a short maintenance grant ([maintenance](../../docs/reference/evaluation-maintenance.md)). Only an explicit user background request authorizes a Harness scheduler; never create, mute or remove one to save quota.

`quota-redetect` opens one retry; reuse its `requestId` after an unknown reply ([harnesses](../../docs/reference/harnesses.md)).
