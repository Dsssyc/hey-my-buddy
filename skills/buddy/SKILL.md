---
name: buddy
description: Delegate bounded work with routing, explicit workspaces, Host-directed assistance and verified artifacts, or update shared model evaluation cards from reviewed work. Use when scope and acceptance are clear; skip trivial edits and unresolved requirements.
---

# Hey My Buddy

Delegate to a Worker buddy when it reduces the work or cost of a verified result. As Host you keep acceptance and authorization; Workers cannot create goals.

## Start here

Call the skill's launcher by absolute path (`~/.agents/skills/buddy/scripts/buddy`; `scripts/buddy.cmd` on Windows) with one JSON object: positional, `--params-file PATH` or `-` for UTF-8 stdin (mutually exclusive). `buddy help` lists every method and `buddy help METHOD` prints its validated parameters, defaults and bounds; neither starts a service or model. Results are brief unless `"output":"full"`.

Read `health` first when routing matters (both Router slots, default mode, capacity); report routing failures with their decision code and recorded `routingMode`/`fallback`. The slots, mode and budget are user-owned console settings. Fast routing sends each task description to its provider; review routing also reads a frozen repository copy.

Details: [usage](../../docs/reference/usage.md), [CLI](../../docs/reference/cli.md), [workflow](../../docs/reference/workflow.md), [operations: launcher sandbox](../../docs/reference/operations.md), [harnesses](../../docs/reference/harnesses.md), [Claude](../../docs/reference/claude.md).

## Delegate through acceptance

1. Define one outcome, inputs, permitted files, artifacts and acceptance checks; reference files instead of pasting them; state the user's language (this repository: Chinese). Choose `executionWorkspace` explicitly: `existing` for sequential ownership, independent `worktree` checkouts for parallel writers; it is not an OS sandbox.
2. Omit `adapter`/`provider`/`model`/`effort` for routing; a complete tuple is only for an explicit user choice or recovery at this goal's boundary. One agenda, one objective: the first delegation carries `objective: {title}` and an intent `title` (≤30 characters), later ones its `objectiveId` or `objectiveOf: "<runId>"` when supported. Submit once with a stable `requestId`; keep `runId`/`objectiveId`/`controlFile`; no unsupported fields on continuations or helpers.
3. Wait for the same goal: Codex uses one monitoring-only native subagent per delegation ([codex](../../docs/reference/usage.md#waiting-from-codex)); Claude Code uses one background `await` ([claude](../../docs/reference/usage.md#waiting-from-claude-code)). A timeout never cancels it; unconfirmed shutdown never means stopped.
4. At a boundary `decide` on helpers or `continue` with input; a routing boundary takes a legal configuration with a reason, or `reroute:true`. Repeated failures need an authorized recovery decision.
5. Verify the fixed artifact yourself (changed paths, risky hunks, proportionate checks), then `integration-record` and `acknowledge` the exact `finalArtifactId` with its integration ID, or `notRequired` with a reason. Completion, prose and RPC replies are not acceptance. Reclaim an accepted goal's checkout with `workspace-cleanup-plan`/`workspace-cleanup-apply`.

## Capabilities and authority

Read the capability report; name unverified or not-yet-installed capability as such ([harnesses](../../docs/reference/harnesses.md), [Claude](../../docs/reference/claude.md)), never as working.

Preferences, enablement, capacity and Router configuration are user-owned: change them only in the authenticated console, never by editing this skill to bypass routing. Routing and execution share one attempt ceiling and per-family limits; native permissions need Host attention.

Model-card updates only on user request: `evaluation-prepare` (no model call), then a short maintenance grant ([maintenance](../../docs/reference/evaluation-maintenance.md)). Only an explicit user background request authorizes a Harness scheduler; never create, mute or remove one to save quota.
