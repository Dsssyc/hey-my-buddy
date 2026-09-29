---
name: buddy
description: Delegate bounded work with routing, owned workspaces and verified artifacts, or update shared model cards. Use for clear outcomes; skip trivial edits and unresolved scope.
---

# Hey My Buddy

Delegate when a Worker reduces verification effort or cost. Host keeps acceptance and authorization; Workers cannot create goals.

## Start here

Use the absolute launcher (`~/.agents/skills/buddy/scripts/buddy`; `scripts/buddy.cmd` on Windows) with one JSON object: positional, `--params-file PATH` or `-` for UTF-8 stdin (mutually exclusive). `buddy help` lists methods; `buddy help METHOD` gives validated parameters, defaults and bounds. Neither starts service or model. Brief unless `"output":"full"`.

Review needs a version certificate. Read `health` first for routing slots, mode and capacity; report failures with decision code and `routingMode`/`fallback`. A sole legal candidate needs no Router; otherwise fast sends the task to its provider, and review also reads a frozen repository copy.

Details: [usage](../../docs/reference/usage.md), [CLI](../../docs/reference/cli.md), [workflow](../../docs/reference/workflow.md), [operations](../../docs/reference/operations.md), [harnesses](../../docs/reference/harnesses.md), [Claude](../../docs/reference/claude.md).

## Delegate through acceptance

1. Define outcome, inputs, permitted files, artifacts and checks; reference files and state the user's language. Set `executionWorkspace`: `existing` for sequential ownership, separate `worktree` checkouts for parallel writers; neither is an OS sandbox.
2. Omit `adapter`/`provider`/`model`/`effort` for routing; a complete tuple is only for an explicit user choice or recovery at this goal's boundary. One agenda, one objective: the first delegation carries `objective: {title}` and an intent `title` (≤30 characters), later ones its `objectiveId` or `objectiveOf: "<runId>"`. Submit once with a stable `requestId`; keep `runId`/`objectiveId`/`controlFile`; no unsupported fields on continuations or helpers.
3. Wait for the same goal: Codex uses one monitoring-only native subagent per delegation ([codex](../../docs/reference/usage.md#waiting-from-codex)); Claude Code uses one background `await` ([claude](../../docs/reference/usage.md#waiting-from-claude-code)). A timeout never cancels it; unconfirmed shutdown never means stopped.
4. At a boundary `decide` on helpers or `continue` with input; a routing boundary takes a legal configuration with a reason, or `reroute:true`. An enabled configuration may change with a reason unless `configurationLocked`; partial work is unverified. See [recovery](../../docs/reference/workflow.md).
5. Verify the fixed artifact yourself (changed paths, risky hunks, proportionate checks), then `integration-record` and `acknowledge` the exact sealed `artifactId` (`finalArtifactId` when delivered) and integration ID, or `notRequired` with a reason. Completion, prose and RPC replies are not acceptance. A waiting Host may finish directly; failed/cancelled goals use `verdict:"recorded"` for a conclusion. Reclaim reviewed checkouts with `workspace-cleanup-plan`/`workspace-cleanup-apply`.

`buddy console` opens the fixed loopback URL, no login by default (`{"browser":false}` returns it). Optional login uses 10-minute entries; sessions have no age expiry and can be revoked in Settings ([console](../../docs/reference/console.md)).

## Capabilities and authority

Use recorded [capabilities](../../docs/reference/harnesses.md); name unverified paths.

Preferences, enablement, capacity and Router configuration are user-owned: change them only in the local console, never by editing this skill to bypass routing. Routing and execution share one attempt ceiling and per-family limits; native permissions need Host attention.

Model-card updates only on user request: `evaluation-prepare` (no model call), then a short maintenance grant ([maintenance](../../docs/reference/evaluation-maintenance.md)). Only an explicit user background request authorizes a Harness scheduler; never create, mute or remove one to save quota.
