---
name: buddy
description: Delegate bounded work to local dsh through the uv-managed buddy CLI over C-Two and its transactional Python blackboard, keeping this turn waiting on one durable run, then independently verify artifacts.
---

# Buddy

Hand **one bounded task** to a local `dsh` run through the `buddy` CLI, keep this turn waiting on that durable run, then verify the real artifacts yourself. Codex keeps framing, route choice and final acceptance.

The Host may implement work itself and delegate another part for comparative advantage. Choose the execution workspace explicitly: a sequential sole writer can use the original checkout; concurrent writers use separate worktrees with a fixed input revision, named integrator and combined acceptance checks. Workers manage their own internal subagents.

## Resolve the launcher

This file is `<plugin-root>/skills/buddy/SKILL.md`, so the plugin root is two directories up. Nothing supplies a `PLUGIN_ROOT` environment variable any more.

```sh
SKILL_DIR='/abs/path/to/<plugin-root>/skills/buddy'   # directory of this loaded SKILL.md
PLUGIN_ROOT=$(cd "$SKILL_DIR/../.." && pwd)
BUDDY="$PLUGIN_ROOT/deepseek-delegate/scripts/launch-buddy.sh"
```

Quote `"$BUDDY"`, and pass one JSON object per command.

## Default workflow

1. Write a bounded packet: objective, inputs and outputs, permitted files, commands, acceptance conditions, references and known pitfalls. Explain why this Buddy configuration fits. Keep instructions stable and put changing task facts after reusable guidance. Do not add irrelevant context to chase cache hits.
2. Choose `executionWorkspace` explicitly. Concurrent writers use independent `worktree` snapshots; a sole sequential writer can use `existing`. Name an integrator, declare `writeScope`, and list any untracked inputs. `workspace` only controls DSH session grouping.
3. Submit once with a stable `requestId` and a Host identity. Save the returned `runId` and private `controlFile`. Await that same logical task while doing independent Host work. Never edit its held checkout.
4. If `await` returns `waiting-host`, read `workflow-get`. The previous agent turn ended normally, but the goal is unfinished. Inspect the request and pinned artifacts; approve explicit helpers with workspace/model choices, decline with a reason, or supply continuation input. A helper can suggest further work; only the Host authorizes new Buddy tasks.
5. After execution, inspect the actual output commit/diff and run the relevant checks. Record `workflow-acknowledge` against the final artifact. Helper success, parent completion and final acceptance are separate facts.

```sh
"$BUDDY" workflow-submit '{"requestId":"<stable-id>","hostId":"<this-host>","task":"<packet>","cwd":"/abs/repo","provider":"deepseek-official","model":"deepseek-flash","effort":"max","timeoutSeconds":28800,"executionWorkspace":{"kind":"worktree","cwd":"/abs/repo","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["src/state.ts","tests/state.test.ts"],"integrator":"<this-host>"}}'
"$BUDDY" await '{"runId":"<runId>","waitSeconds":28800}'
"$BUDDY" workflow-get '{"runId":"<runId>"}'
# After independent verification of the final artifact:
"$BUDDY" workflow-acknowledge '{"runId":"<runId>","commandId":"<stable-review-id>","controlFile":"<returned-path>","artifactId":"<final-artifact-id>","note":"<actual checks and findings>","verdict":"accepted"}'
```

Use [workflow.md](../../deepseek-delegate/references/workflow.md) for complete approval, continuation, takeover and workspace examples. The CLI persists private submission credentials before sending and uses explicit `controlFile` for later control. Never read or paste its token into task text, another Buddy or a public result; never adopt another Host's latest credentials. A stale generation/revision means re-read and reconsider, not retry with invented authority.

An identical submission recovers the same logical task. Changed input under the same ID is a conflict. Reuse the same `commandId` and exact payload after an uncertain mutation response. A new continuation gets an independent attempt in a reconstructed new DSH session; it does not claim native session resume.

Legacy `start` / `run` and `acknowledge` remain for ordinary one-shot/non-Git tasks. They do not provide governed assistance or snapshot ownership. `run` starts or recovers once and waits; its default wait window is execution timeout plus 60 seconds, capped at 86400 seconds. Do not use legacy mutations to bypass a governed task's control.

## Lifetimes and cancellation

- `timeoutSeconds` is the execution deadline: integer 10–86400, default 1800. It bounds the whole spawned dsh process group from spawn and covers every model and tool step — not a model-turn limit and not the time a task spends queued. Pass it explicitly for long work (for example `28800` for 8 hours). Nothing extends it.
- `run`/`await` carry the long waits; `await` accepts `waitSeconds` 1–86400 (default 86400) and never starts work. Ending a wait — Ctrl-C, a closed terminal, `waitSeconds` expiry — cancels only the wait: the durable task keeps running and is recovered by the same `runId`/`requestId`.
- A wait that ends first returns `outcome: "wait-timeout"` with the run still active. That is a wait/connection limit, never an execution failure: do not relaunch it.
- Use `workflow-cancel` with the saved control file for a governed goal and its helpers; use `cancel` for a legacy task. Active cancellation writes durable intent for the owning worker's process group. The execution deadline and `buddy stop` also end owned work; unknown shutdown remains unknown and cancelled goals never resume automatically.
- Each authorized continuation has a fresh attempt with the configured execution timeout. Waiting for Host decisions or helpers does not consume a running process's deadline; it does not silently create a monetary or whole-goal budget.
- After a daemon restart, in-flight attempts become `uncertain` with resource claims retained; the independent worker keeps its child and deadline and reattaches by attempt identity and nonce. Unknown never means stopped, and a new execution requires an explicit `retry`.
- `wait` and `watch` are bounded at 30 s and never cold-start a service. If a call returns `wait-timeout` or an unavailable envelope, keep monitoring the same run or report its `runId` explicitly; never end the turn with an unmonitored running job and never restart work the user stopped.

## Shared evaluations and console

`buddy console` opens the private React/Vite workspace for tasks, model profiles, evidence and preferences. Ordinary page refresh and `console-snapshot` are read-only and invoke no model. Explicit catalog discovery proposes installed configurations; it does not prove their real-world quality or enable them automatically. Use [evaluation.md](../../deepseek-delegate/references/evaluation.md) for table editing and evidence, and inspect reported capabilities before invoking decision operations. A running business task keeps its accepted configuration when the current evaluation table changes.

When a route is already explicit, use it. When model comparison is needed and a decision profile is configured, submit `selection-request` once with a stable request ID, await its returned run ID if present, then read `selection-get` by decision ID. Its default small summary carries `selectedProfile` execution parameters; do not load the complete evaluation table into the Host context. `includeAudit:true` is for investigating a decision. This result proposes a route; the Host still authorizes the separate business task. A `needs-host` or failed decision does not fail the business goal: use an already authorized fixed route or do the work as Host when appropriate.

Internal decision runs are not business deliverables and cannot be acknowledged or retried with task operations. For a new deliberate attempt, submit a new decision request; for an uncertain submission, recover the same request ID. `evaluation-maintain` explicitly requests a bounded batch; `autoMaintain` controls whether safe results can publish without another review, and never schedules calls from page refresh. Record scoped experience against a known profile and actual run after Host verification; ordinary observations and model-generated recommendations are not verified performance samples.

## Progress and questions

When a run has been quiet and you need to report honestly:

```sh
"$BUDDY" inquire '{"runId":"<runId>"}'
"$BUDDY" inquire '{"runId":"<runId>","inquiryId":"q-1","question":"What are you waiting on?","waitMs":20000}'
```

The no-question form is bounded observation, not a percentage or guaranteed ETA, and names unobservable fields in `live.unavailable`. A question goes to the run's own live agent; it never starts a second agent and never extends or cancels the task. Answers require the correlated reply tool, so assistant prose is never an answer; a bounded question can return `delivered` before an answer exists — check `answer.available`. Re-read a question through `inquire` with the same id and question text. Changed text for the same id is a `CONFLICT`; ask explicitly instead of building a polling loop.

## Background route

`workflow-submit` (or legacy `start`) returns the durable identity before waiting. Only when the user explicitly wants work to outlive the turn, register an official App heartbeat on the original task before ending the turn; its prompt calls this same CLI to read the result, verify it and acknowledge. The heartbeat is a periodic follow-up, not an immediate completion push, and native post-turn App wakeup is not solved by Buddy.

Read the [background workflow](../../deepseek-delegate/references/usage.md#background-work-that-outlives-the-turn) for monitor reuse and cleanup. If that route is unavailable, keep this turn waiting.

## Boundaries

- `workspace: true` (the default) requires the installed, running workspace host bridge; `workspace: false` opts out and is never applied silently.
- `cwd` is a working directory, not a sandbox; state the allowed scope in the packet.
- Only Buddy-owned runs are controlled; independent dsh sessions are not coordinated.
- CLI commands run with this task's shell permissions, but an already-running daemon or worker keeps the permissions it was started with. Treat delegated work as running with your own access, and never let Codex and dsh edit the same files at once.

## References

| Need | Read |
| --- | --- |
| Install/use paths, examples, foreground/background flows | [usage.md](../../deepseek-delegate/references/usage.md) |
| Every command, field, default, bound, error and envelope | [cli.md](../../deepseek-delegate/references/cli.md) |
| Runtime install/upgrade, bridge, state, env vars, legacy import | [operations.md](../../deepseek-delegate/references/operations.md) |
| Adapters, external worker example, worker receipts | [workers.md](../../deepseek-delegate/references/workers.md) |
| Standalone `run.mjs` runner and its contract | [runner.md](../../deepseek-delegate/references/runner.md) |
| Owner resumption without the service | [handoff.md](../../deepseek-delegate/references/handoff.md) |
| Implemented architecture and limits | [architecture.md](../../deepseek-delegate/references/architecture.md) |
| Shared evaluations, preferences, evidence and local console | [evaluation.md](../../deepseek-delegate/references/evaluation.md) |
