---
name: hey-my-buddy
description: Delegate bounded pieces of your current work — implementing, fixing tests, investigating, reviewing — to other coding agents, each routed to a suitable model in its own checkout, then verify and accept the result. Use it whenever such a piece can run while you continue or suits another model better; do trivial or still-unclear steps yourself.
---

# Hey My Buddy

The submitting session is the Host: it owns acceptance and authorization; Workers cannot create micro tasks.

## Start here

Use the absolute launcher (`~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy`; Windows `scripts/hey-my-buddy.cmd`) with one JSON object: positional, `--params-file PATH` or UTF-8 stdin `-`. `hey-my-buddy help [METHOD]` reads validated parameters, starts nothing.

Read `health` for the Router list, mode and capacity; report failures with decision code and `routingMode`.

[usage](../../docs/reference/usage.md), [workflow](../../docs/reference/workflow.md).

## Delegate through acceptance

1. One micro task, one independently reviewable outcome: about a module or concern, with its own checks; mechanical apart from diagnosis. Define inputs, outcome and checks in the user's language; untracked inputs need `includeUntracked`. Use `executionWorkspace`: `existing` for sequential ownership, `worktree` checkouts for parallel writers; neither is an OS sandbox. `access`: `read`, `write` (default) or `full` with `accessNote`. Name a narrow `writeScope` and the plan to follow; the Worker stops and asks instead of deciding alone or working around ([brief](../../docs/reference/workflow.md#writing-the-brief)).
2. Omit `harness`/`provider`/`model`/`effort` for routing; a complete buddy is only for an explicit user choice or boundary recovery. One agenda, one macro task: its first micro task carries `macroTask: {title, goal}` and an intent `title` (≤30 chars), later ones its `macroTaskId` or `macroTaskOf: "<runId>"`. Submit once with a stable `requestId`; keep `runId`/`macroTaskId`/`controlFile`.
3. Before waiting, submit independent micro tasks; the board queues them. Wait through your Host guide ([codex](../../docs/reference/host-codex.md), [claude](../../docs/reference/host-claude-code.md)); read only yours. One `await` per running micro task; a timeout never cancels it; unknown never means stopped: on `EXECUTION_UNKNOWN` tell the user.
4. At a boundary `decide` on helpers or `continue` with input; a routing one takes `reroute:true` (first choice when `routing-changed`) or a legal `buddy` with a `note`. To hand off, `continue` with an enabled `buddy` and a `note`, after `cancel` if a turn runs; partial work is unverified.
5. Reread the brief and later inputs (`get` with `"output":"full"`); check the report and the fixed artifact against it: changed paths, risky hunks, checks. `accept` the exact sealed `artifactId` (`finalArtifactId` when delivered) with a `note` and the integration `target`, or `notRequired` with its reason: it verifies the integration and reclaims the checkout. Prose and RPC replies are not acceptance. A waiting Host may finish directly. Failed, cancelled or unaccepted work keeps its checkout; `conclude` ends it for good, with a note; `reclaim` retries a blocked removal.

`hey-my-buddy console` opens loopback, login off; `{"browser":false}` returns its URL ([console](../../docs/reference/console.md)).

Install on the user's word; a new middle version needs idle work ([operations](../../docs/reference/operations.md)).

## Capabilities and authority

[Harnesses](../../docs/reference/harnesses.md) own capabilities. Sign-in stays the user's.

A Lead (`delegationAuthority`) only at the user's explicit request, confirmed first ([usage](../../docs/reference/usage.md#a-lead-for-a-large-macro-task)).

Preferences, enablement, capacity and Router settings are user-owned: change them only in the console. Routing skips `exclude`d buddies, and unpinned ones while any is pinned; see `buddy-list`. Routing and execution share capacity and model service limits; native permissions need Host attention.

