---
name: buddy
description: Delegate bounded work to local dsh through the uv-managed buddy CLI over C-Two and its transactional Python blackboard, keeping this turn waiting on one durable run, then independently verify artifacts.
---

# Buddy

Hand **one bounded task** to a local `dsh` run through the `buddy` CLI, keep this turn waiting on that durable run, then verify the real artifacts yourself. Codex keeps framing, route choice and final acceptance.

## Resolve the launcher

This file is `<plugin-root>/skills/buddy/SKILL.md`, so the plugin root is two directories up. Nothing supplies a `PLUGIN_ROOT` environment variable any more.

```sh
SKILL_DIR='/abs/path/to/<plugin-root>/skills/buddy'   # directory of this loaded SKILL.md
PLUGIN_ROOT=$(cd "$SKILL_DIR/../.." && pwd)
BUDDY="$PLUGIN_ROOT/deepseek-delegate/scripts/launch-buddy.sh"
```

Quote `"$BUDDY"`, and pass one JSON object per command.

## Default workflow

1. Write the packet before launching: goal and why; inputs and outputs; the working directory and check commands; allowed files/areas and non-goals; acceptance criteria; references, known facts and pitfalls. Task text over 32,000 bytes is delivered to dsh as a file reference, and that file must stay in place until the run finishes.
2. Start once with a stable `requestId` and keep the returned `runId`.
3. Await that same run in the current turn. Do not start a second run and do not work on the same files meanwhile.
4. When it returns, inspect the real diff, files, hashes and logs and run the relevant checks yourself. Exit 0 only means the call returned; it is not acceptance.
5. Once the result is persisted and shutdown is confirmed, record the review with `acknowledge` and the evidence you actually gathered.

```sh
"$BUDDY" start '{"requestId":"<stable-id>","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
"$BUDDY" await '{"runId":"<runId>","waitSeconds":28800}'
# After independent verification:
"$BUDDY" acknowledge '{"runId":"<runId>","note":"<actual checks and findings>","verdict":"accepted"}'
```

`requestId` is the idempotency key: an identical request recovers the same task and never launches dsh twice, while changed input for the same id is a `CONFLICT`. A task is admitted as `queued` and starts when a worker claims it; `queueReason` explains any wait (`awaiting-worker`, `capacity`, `cwd-overlap`, `exclusive-resource`). Capacity and resource conflicts queue instead of failing, so there is no BUSY error to retry around.

`buddy run` is the optional one-call alternative: it starts (or recovers) and stays connected, with `waitSeconds` defaulting to `timeoutSeconds + 60 s` shutdown grace capped at 86400 s.

## Lifetimes and cancellation

- `timeoutSeconds` is the execution deadline: integer 10–86400, default 1800. It bounds the whole spawned dsh process group from spawn and covers every model and tool step — not a model-turn limit and not the time a task spends queued. Pass it explicitly for long work (for example `28800` for 8 hours). Nothing extends it.
- `run`/`await` carry the long waits; `await` accepts `waitSeconds` 1–86400 (default 86400) and never starts work. Ending a wait — Ctrl-C, a closed terminal, `waitSeconds` expiry — cancels only the wait: the durable task keeps running and is recovered by the same `runId`/`requestId`.
- A wait that ends first returns `outcome: "wait-timeout"` with the run still active. That is a wait/connection limit, never an execution failure: do not relaunch it.
- `cancel` cancels queued work immediately and writes durable cancel intent for an active attempt, which the owning worker applies to its own process group. The task's own deadline and `buddy stop` also end owned work; those endings are terminal and are never replayed automatically.
- After a daemon restart, in-flight attempts become `uncertain` with resource claims retained; the independent worker keeps its child and deadline and reattaches by attempt identity and nonce. Unknown never means stopped, and a new execution requires an explicit `retry`.
- `wait` and `watch` are bounded at 30 s and never cold-start a service. If a call returns `wait-timeout` or an unavailable envelope, keep monitoring the same run or report its `runId` explicitly; never end the turn with an unmonitored running job and never restart work the user stopped.

## Progress and questions

When a run has been quiet and you need to report honestly:

```sh
"$BUDDY" inquire '{"runId":"<runId>"}'
"$BUDDY" inquire '{"runId":"<runId>","inquiryId":"q-1","question":"What are you waiting on?","waitMs":20000}'
```

The no-question form is bounded observation, not a percentage or guaranteed ETA, and names unobservable fields in `live.unavailable`. A question goes to the run's own live agent; it never starts a second agent and never extends or cancels the task. Answers require the correlated reply tool, so assistant prose is never an answer; a bounded question can return `delivered` before an answer exists — check `answer.available`. Re-read a question through `inquire` with the same id and question text. Changed text for the same id is a `CONFLICT`; ask explicitly instead of building a polling loop.

## Background route

`start` is the first half of the default foreground flow. Only when the user explicitly wants work to outlive the turn, register an official App heartbeat on the original task before ending the turn; its prompt calls this same CLI to read the result, verify it and acknowledge. The heartbeat is a periodic follow-up, not an immediate completion push, and native post-turn App wakeup is not solved by Buddy.

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
