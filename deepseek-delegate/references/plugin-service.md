# Buddy C-Two service

The plugin exposes a **CLI** (`buddy`) backed by a **Python transactional blackboard
service** over the PyPI **C-Two** package pinned in `uv.lock` (currently 0.5.1). There is
no MCP server, no `mcp` Python dependency, no Node global job manager and no
Python-to-Node engine relay: one Python daemon owns every authoritative fact in
schema-versioned SQLite, and independent Python workers execute the work. `uv.lock`
fixes the complete Python dependency graph, and this implementation does not import a
sibling C-Two/FastDB checkout. Python is `>=3.12,<3.15`; the bundled launcher selects
3.12 through uv. Node.js remains an external runtime that the `dsh` adapter and dsh
itself need. Install with `uv sync --project deepseek-delegate --python 3.12`, then use
`deepseek-delegate/scripts/launch-buddy.sh health` (or
`node deepseek-delegate/scripts/buddy.mjs health`).

## Ownership

The CLI speaks only the named C-Two contracts: `BuddyControl` on the `buddy-control`
resource (mutations, reads, lifecycle) and `BuddyWait` on the separate `buddy-wait`
resource (bounded waits). There is no public `dispatch(method, JSON)` facade. The
daemon is the only writer of authoritative state; a `BEGIN IMMEDIATE` transaction
commits each state change together with the event that explains it, and no transaction
spans RPC, a subprocess, an LLM call or a wait. `BoardService`/`WaitService` validate
every request schema before it reaches the store.

Workers are **independent processes**, not part of the daemon. A worker owns the child
process handles it created, enforces its own execution deadline, renews its lease,
observes durable cancel intent and keeps an immutable local completion receipt until
the service confirms the result transaction. The daemon never sends an OS signal to a
process it did not create, and a surviving worker is recognized across a daemon restart
by attempt identity plus its unguessable nonce capability, never by PID. Worker recovery
spools are bounded transport receipts, not a second task database.

The Python service never starts, stops or rewrites an unrelated dsh host. Only
Buddy-owned tasks are controlled. Each `dsh` task uses its own settings copy, logs and
dsh process group; `workspace: true` (the default) requires the installed, running dsh
host bridge (see the skill's workspace grouping section) and `workspace: false` opts
out. A `cwd` is a working directory, not a sandbox, and a CLI command runs with the
calling task's shell permissions — Buddy never grants extra privilege, and the service
is a same-user local process. `BUDDY_MAX_CONCURRENT` defaults to 1 (values 1–8);
submitted work whose canonical cwd overlaps a held cwd claim, or whose
`exclusiveResources` are held, is queued rather than rejected. External dsh work still
shares machine/provider resources and may edit the same files, so coordinate those
separately.

## Default blocking delegation

The default workflow is `buddy start` → `buddy await` inside the current turn: `start`
returns a `runId` immediately (or recovers the existing task by `requestId`), and
`await` stays connected on that same durable task until it is terminal or the wait
window ends. Capturing the runId before waiting is what lets `buddy inquire` observe or
question the task while it is in flight. `buddy run` remains an optional one-call
convenience: it starts (or recovers) one durable task by `requestId`, waits inside that
single CLI invocation, and prints the final envelope: `runId`, execution `status`,
`outcome`, `shutdownConfirmed`, the adapter `result`, log paths and, when relevant,
recovery instructions.

`run` and `await` are **CLI-level blocking helpers**, not service operations. They are
ordinary synchronous code built on `task_submit`, `task_wait` and `task_result`, with
bounded wait slices (30 s) re-armed until the task is terminal or the window is spent.
There is no heartbeat, no cron, no busy loop and no model-visible polling. Concurrent
status, list, inquire and cancel calls are separate C-Two calls and are not blocked by a
pending `buddy run` or `buddy await`.

`runId` still selects one task and equals `taskId`. `start`/`submit` return the task
view itself; `status`, `wait`, `cancel`, `retry` and `acknowledge` also return the task
view directly (`cancel`/`acknowledge` add their own flags such as `alreadyTerminal` or
`duplicate`). `result` returns the task view plus the adapter outcome under `result` and
the commit metadata under `resultMeta` (`status`, `error`, `exitCode`, `signal`,
`shutdownConfirmed`, `completedAt`).

`buddy start` returns a task that is admitted as `queued`, not as a rejected request.
It starts when a worker claims it, and `queueReason` says why it is waiting:

| `queueReason` | Meaning |
| --- | --- |
| `awaiting-worker` | no worker has claimed it yet (normal immediately after submit) |
| `capacity` | `BUDDY_MAX_CONCURRENT` attempts are already active |
| `cwd-overlap` | another held task shares or contains this canonical cwd |
| `exclusive-resource` | a declared exclusive resource is held by another task |

Queueing is the intentional admission change from the removed implementation's
BUSY-only behaviour: capacity and resource conflicts no longer reject admission, and
the CLI never answers `BUSY` for admission. `WORKER_BUSY` exists only as a per-worker
rule — one worker runs one attempt at a time.

## Lifetimes: execution deadline, wait window, process lifetime

Four different limits must not be confused:

| Limit | Where | Default | What it bounds |
| --- | --- | --- | --- |
| Execution deadline | `timeoutSeconds` on submit/`run`/`start` | 1800 s (10–86400) | the whole adapter process group, enforced by the worker that owns the child, covering every model and tool step |
| Blocking wait window | `waitSeconds` on CLI `run` | deadline + 60 s shutdown grace, capped at 86400 s (1–86400) | how long one blocking `run` keeps waiting |
| CLI `await` wait | `waitSeconds` on the CLI `await` subcommand | 86400 s (1–86400) | how long that wait stays connected; it never starts work |
| Attempt lease | `BUDDY_LEASE_SECONDS` | 120 s (15–3600) | how long a worker's claim stays valid between renewals; expiry marks the attempt `uncertain`, never `stopped` |

Reaching the wait window returns `outcome: "wait-timeout"` and never changes the
execution deadline: the task keeps running and is recovered by `requestId`/`runId`.
Reaching the execution deadline makes the owning worker stop its own process group and
is reported as a terminal `failed` outcome with the adapter's `status: "timeout"`.
Killing the waiting CLI process (Ctrl-C, closed terminal, user stop) can cut a call
without an envelope; the durable task still survives it, and killing the *wait* never
cancels the task. The CLI process itself is the fourth lifetime: ending it ends only
that call's wait.

The default workflow is `buddy start` then `buddy await` on the returned runId, which
keeps one CLI call short and the wait explicit. `buddy run` is the one-call alternative:
it covers the execution deadline plus the shutdown grace by default, so one invocation
normally covers the whole job. Either way a wait window is bounded, so for a deadline
beyond the 24 h maximum, or to split a wait deliberately, start the task and await the
same runId/requestId:

```sh
BUDDY=<plugin-root>/deepseek-delegate/scripts/launch-buddy.sh
"$BUDDY" start '{"requestId":"x","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
"$BUDDY" await '{"runId":"<runId>","waitSeconds":28800}'
```

The 1800 s default is a **Buddy execution timeout**, not an "agent turn limit" of the
harness and not a limit on how long the model may think. It is a wall-clock bound on
the one adapter process group the worker owns (for `dsh`, `scripts/run.mjs` arms its own
timer after spawn and signals the owned group on expiry). A long task is expected to
pass an explicit value, for example `"timeoutSeconds": 28800` for 8 hours (maximum
86400 = 24 h). `buddy inquire` never extends, shortens, pauses or restarts that timer.

## CLI wait window and upgrade ordering

**Wait window.** There is no host tool timeout on this path and no
`BUDDY_TOOL_WAIT_BUDGET_SECONDS`. `buddy run` picks
`min(86400, timeoutSeconds + 60)` seconds, an explicit `waitSeconds` (1–86400) always
wins, and `buddy await` uses its own explicit `waitSeconds` (default 86400). Reaching
the window is a wait limit, not an execution failure: the envelope carries
`outcome: "wait-timeout"`, `waitCoversRunnerDeadline: false`, a `limitation` string and
a `recovery` object whose `commands` name real CLI invocations with the existing run ID
(`buddy status`, `buddy await`, `buddy result`, `buddy cancel`). A completed task whose
result cannot be delivered is reported as `completed-no-result` with `ok: false`, never
as a successful complete result.

**State directory ownership.** A lifetime `flock` on `control-daemon.lock` and
`board-owner.lock` gives one daemon ownership of a state directory, and a legacy socket
guard binds the old `service.sock` path so a cached Node daemon cannot claim the same
records; a connecting old client receives an explicit `MIGRATED` error. The new service
never dispatches work through the old endpoint and never rewrites what the old
implementation wrote. When a state directory is longer than the platform's AF_UNIX address
capacity, the real `bind` failure proves that no socket can exist there, so the guard omits
that obsolete listener; an entry that does exist at the path (an older service can hold it
through a shorter alias) still refuses the start instead of being bypassed.

**Upgrade ordering (operator).** Finish active work and stop the owned service before
activating a new service version. This is also required when leaving the legacy 0.3
service, which executes directly from the replaceable plugin cache:

```sh
<plugin-root>/deepseek-delegate/scripts/launch-buddy.sh stop   # or the previous version's launcher
# then install/refresh the plugin version
```

`buddy stop` cancels queued tasks, writes a durable cancel request for each active
attempt, drains for a bounded interval (default 10 s) and reports what stayed
unresolved, so it is the supported pre-upgrade action. `buddy restart` detaches the
daemon *without* cancelling owned work: independent workers keep their child handles,
deadlines and receipts, and the next CLI command cold-starts a fresh daemon that
reattaches them by attempt identity and nonce capability. Version 0.4 automatically
materializes a content-addressed runtime outside the plugin cache on the first cold
start. Existing daemons and workers remain pinned to their runtime when a cache
entry is replaced. Stopping an idle service and calling the new bundle activates
its new runtime; verify `buddy health` and `runtimeStable` before delegating work.
See [Packaging](#packaging-migration-and-unrelated-app-tools) for the runtime layout
and the explicit `BUDDY_DEV_SOURCE=1` development override.

## Long jobs

A deadline longer than the wait window is explicitly allowed. The envelope then carries
`waitCoversRunnerDeadline: false` and a `limitation` string, and a call that runs out of
wait returns `outcome: "wait-timeout"` — never `failed`, and the task is never
relaunched. Two supported paths:

1. Use the default start → await flow: `buddy start` returns a runId immediately, then
   `buddy await` waits on that same durable task with its own explicit window:

   ```sh
   BUDDY=<plugin-root>/deepseek-delegate/scripts/launch-buddy.sh
   "$BUDDY" start '{"requestId":"<id>","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
   "$BUDDY" await '{"runId":"<runId>","waitSeconds":28800}'
   ```

   `await` accepts `waitSeconds` 1–86400 (default 86400) and never starts work.
2. Or let one `buddy run` cover it: the default window is `timeoutSeconds + 60 s` (capped
   at 86400 s), so a job whose deadline fits in 24 h is covered by a single invocation.
   Repeating the identical `buddy start` or `buddy run` recovers the same task.

## Cancellation and recovery

Different cancellations are kept separate:

- Killing the waiting CLI process (Ctrl-C, closed terminal, user stop) or reaching
  `waitSeconds` cancels only the *wait*. The durable task keeps running while a worker
  owns it and stays recoverable.
- `buddy cancel '{"runId":"..."}'` is a durable operation. A queued task without a live
  attempt is `cancelled` immediately; an active task becomes `cancelling` and the
  attempt records a durable cancel request (`cancelRequestedAt`). The worker that owns
  the child observes that intent on its next lease renewal and cancels its own process
  group; unrelated dsh sessions are unaffected.
- When the worker commits `cancelled` with confirmed shutdown, the task becomes
  `cancelled` and its resource claims are released. A cancellation whose shutdown is
  unconfirmed leaves the task `reconciliation-needed` and retains its claims. A
  confirmed completion that beat the cancel request is the one legal durable outcome:
  the task is `completed` and the event records the race.
- The task's own execution deadline and `buddy stop` also end Buddy-owned work. Those
  endings are terminal and are never replayed automatically.

Recovery uses `requestId` as an idempotency key. Re-running the identical `buddy start`
or `buddy run` (requestId and normalized input) returns the same existing task and never
launches the adapter twice; changed input is a `CONFLICT`. A `wait-timeout` envelope is
a wait/connection limit, not an execution failure: the task stays active, it carries the
runId and a `recovery` block of real CLI commands, and neither `buddy cancel` nor a
replay is implied. An `unavailable` envelope means the service could not be reached
while waiting; the record is durable, so re-read it by ID.

A daemon restart preserves task and attempt identity. Every in-flight
attempt becomes `uncertain` (ownership `uncertain`, lease cleared) and keeps its
resource claims; workers that were busy are marked `lost`. The legitimate worker keeps
running with its child handle, deadline and receipt, and its `worker_renew` and
`worker_result` calls are authenticated by the same attempt ID, generation and nonce,
so it can finish and commit the same attempt; `worker_reconcile` re-establishes that
identity explicitly and refuses a replaced generation (`STALE_GENERATION`). Nothing is
reattached by PID. Lease expiry likewise marks an attempt `uncertain`, never `stopped`.

Retrying unknown or failed work is always an explicit `buddy retry` that creates a new
attempt generation. An attempt whose shutdown is unconfirmed cannot be retried at all
(`SHUTDOWN_UNCONFIRMED`): a written reason is not evidence of termination, so the only
way it is released is verifiable evidence from the worker that owned the process
handle — that worker reattaches with its own nonce and commits an outcome whose
`shutdownConfirmed` comes from observing the owned process group, or it releases an
attempt that never crossed the durable spawn intent. A retry clears the previous
acceptance, because a review belongs to the attempt that was reviewed; the archived
verdict stays readable in the event stream. A stored PID is never used to signal a
process.

A blocking wait cannot physically prevent the user from stopping the turn or closing
the App. User stop/pause instructions take precedence over continuation; interrupted
work is never restarted automatically.

The removed MCP path also removed the native App completion notification: the plugin's
completion inbox, its binding/`notification_ack` bookkeeping and the native stdio client
are gone. Historical `notifications/*.json` files are left untouched, are never read and
are never replayed by the service; only the CLI result path
(`status`/`wait`/`result`/`await`) delivers results now.

## Independent workers

The daemon starts one detached supervisor automatically at startup (`BUDDY_WORKER_ID`,
default `local`). An operator or agent can start another explicitly:

```sh
"$BUDDY" worker-start '{"workerId":"local"}'   # detached supervisor in its own session
"$BUDDY" worker-stop  '{"workerId":"local"}'   # cooperative durable stop request
```

`worker-start` runs `python -m buddy.worker.supervisor` with
`start_new_session=True`, file-backed logs at `<state>/worker.log`, and returns
`workerId`, `supervisorPid`, `logPath` and `stateDir`. The supervisor holds an exclusive
lock on `workers/<workerId>/supervisor.lock`, so liveness is decided by lock ownership
rather than a stored PID, restarts a crashed worker with bounded backoff, and observes
`workers/<workerId>/stop.request`. `worker-stop` only writes that durable request — it
never sends a signal from a process that did not create the worker. `buddy stop` also
writes it for the supervisor the daemon owns.

Each worker registers with `worker_register`, claims one queued task atomically with
`worker_claim`, renews with `worker_renew`, reports `worker_progress`, and commits with
`worker_result`. The claim response carries the attempt, task, lease and an unguessable
capability; the worker persists its nonce and `claimRequestId` before claiming, so a
committed claim whose reply is lost replays the identical attempt and generation. The
worker enforces its own deadline, keeps a `spawn.intent`/`spawn.marker` pair under
`<state>/attempts/<runId>/<attemptId>/` to make the crash window explicit, and retains an
immutable local receipt under `<state>/workers/<workerId>/receipts/<attemptId>.json`
until the service confirms the result transaction; unsatisfied receipts are replayed by
the next supervisor start, and a durable receipt is never executed a second time. Each
worker process has its own instance identity: a startup intent left by a previous
process is preserved as orphaned evidence and its attempt stays `uncertain` with claims
retained, because the new process holds no child handle. Only an intent this process
owns can be released when it never crossed the durable spawn intent.

`buddy workers` lists registered workers with their state (`starting`, `idle`, `busy`,
`stopping`, `lost`), adapter, capabilities and current attempt.

## Adapters

Three adapters are supported and reported honestly by `buddy capabilities` (also
reachable as `buddy adapters`):

| Adapter | `executedBy` | Behaviour |
| --- | --- | --- |
| `dsh` (default) | `built-in-worker` | spawns the existing Node runner `scripts/run.mjs`, which runs `dsh --profile headless` with a real per-run model/effort override, private logs and the per-run inquiry bridge. Requires Node and a usable runner |
| `command` | `built-in-worker` | runs exactly one explicit `argv` process with `shell=False`; an argv program plus arguments is never an implicit shell. `argv` is valid only for this adapter |
| `external` | `caller-owned-agent` | no local process at all: the caller's own agent claims the task through the public C-Two contract and reports the result itself. `argv` is rejected and no built-in worker advertises the capability |

`capabilities` also lists `localCapabilities` (what a built-in worker host can honestly
advertise; `external` is deliberately absent), the named control/wait operations and the
current wait admission. Its `limitations` object states, in the service's own words,
what is not implemented:

- `steer`: an active dsh attempt cannot be re-scoped;
- `resume`: retry is an explicit new attempt, never an automatic resume;
- `nativeAppWakeup`: notifications are post-commit hints for clients, not App wakeup;
- `postgres`: SQLite is the deliberate local database;
- `remoteTenancy`: same-user local service only.

An unavailable adapter is reported with `available: false` and the reason (for example
missing Node, or a missing runner entrypoint). A `dsh` or `command` task claimed by a
built-in worker whose adapter turns out to be unusable fails that attempt with an honest
`ADAPTER_UNAVAILABLE` receipt (confirmed shutdown when no process was created). A task
whose adapter no built-in worker advertises — for example `external` with no caller
agent — stays `queued`, and a claim attempt reports `adapter-mismatch` rather than
running it with the wrong adapter.

## External workers and the public client

`deepseek-delegate/python/buddy/client.py` is the supported way for a separate agent
process to participate without importing service internals, touching SQLite or reading
the daemon token. `BoardClient` exposes `register_worker`, `claim`, `reconcile`,
`renew`, `progress`, `submit_result`, `release`, `post_question`, `update_message`,
`read_events`, `wait_events` and `artifacts` (plus task helpers such as `submit`, `get`,
`cancel`, `retry`, `acknowledge`, `wait_task`, `result`, `list_tasks`, `workers`,
`get_message`, `list_messages` and `wait_message`). `new_nonce()` generates the claim
capability and `new_command_id()` a replay-safe command ID.

A minimal runnable external worker (run it with
`uv run --frozen --project deepseek-delegate python worker.py`, against a task submitted
with `"adapter":"external"`):

```python
from pathlib import Path
import hashlib

from buddy.client import BoardClient, new_nonce
from buddy.worker.worker import fsync_json

state = Path.home() / ".local/share/hey-my-buddy"
board = BoardClient(state)
board.register_worker("external-agent", adapter="external",
                      capabilities=["external", "artifacts", "task-text"])

nonce = new_nonce()
intent = {"workerId": "external-agent", "nonce": nonce, "claimRequestId": "claim-1"}
fsync_json(state / "workers" / "external-agent" / "startup.json", intent)

claim = board.claim("external-agent", "claim-1", nonce)["claim"]
attempt, task = claim["attempt"], claim["task"]
target = Path(task["cwd"]) / "external-report.txt"
target.write_text("handled by the caller-owned agent\n")
board.progress("external-agent", attempt["attemptId"], attempt["generation"], nonce,
               "agent finished the work", phase="executing")
board.submit_result("external-agent", attempt["attemptId"], attempt["generation"], nonce, {
    "status": "ok",
    "result": {"status": "ok", "mode": "external", "finalText": target.read_text()},
    "shutdownConfirmed": True,
    "artifacts": [{"kind": "result", "location": str(target),
                   "contentHash": hashlib.sha256(target.read_bytes()).hexdigest(),
                   "sizeBytes": target.stat().st_size}],
})
```

The same flow — register, durably persist the nonce, claim, progress, submit a verified
artifact — is exercised end to end by
`python/tests/test_blackboard.py::TestExtensibility::test_an_external_agent_completes_a_real_task_through_the_public_api`.
The persisted nonce before the claim is what makes a committed claim with a lost reply
recoverable; `reconcile` reattaches after daemon downtime, and `release` gives back an
attempt that was claimed but never spawned.

## Events and bounded waits

Every committed state change appends a monotonic event in the same transaction.
`buddy events '{"after":0}'` reads committed events with a resumable cursor and bounded
pages (`after`, optional `taskId`/`runId`, `limit` 1–200, default 100) and returns
`events`, `cursor`, `head` and `truncated`. `buddy watch '{"after":<cursor>,"timeoutMs":30000}'`
waits on that stream, and `buddy wait '{"runId":"...","afterRevision":N,"timeoutMs":30000}'`
waits on one task; both bound their wait at 30 s and never cold-start a service. Waiting
uses the dedicated `buddy-wait` resource, whose admission is separate from the control
resource, so any number of waiters can never consume the capacity that cancel, renew and
result commits need. A wait holds no database transaction and no exclusive resource
lock.

When every admitted wait slot is busy, the service returns a resumable
`WAIT_OVERLOAD` error (`details.cursor`, `details.retryAfterMs`, `details.capacity`;
CLI envelopes print the code and message). No task state changed, and the caller retries
from the same cursor — the event stream is the replayable outbox. `buddy wait-capacity`
reports `capacity`, `admitted`, `rejected` and `available`.

## Inquiry: bounded progress and correlated questions

`buddy inquire` asks one owned task what it is doing, and optionally delivers one
operator question to that task's live dsh agent. It is a CLI/service feature with no
automatic scheduling: an agent with shell access calls it explicitly when the task's
permissions allow it.

```sh
# Read-only progress for one owned runId:
uv run --frozen --project deepseek-delegate buddy inquire '{"runId":"<runId>"}'

# One question for the SAME live agent; stable inquiryId; no second model agent:
uv run --frozen --project deepseek-delegate buddy inquire '{"runId":"<runId>","inquiryId":"q-1","question":"what is blocking you?"}'

# Same, waiting up to 30 s for the correlated answer to arrive:
uv run --frozen --project deepseek-delegate buddy inquire '{"runId":"<runId>","inquiryId":"q-1","question":"what is blocking you?","waitMs":20000}'
```

Accepted parameters: `runId` (required); `inquiryId` + `question` (optional, supplied
together, `inquiryId` matches `[A-Za-z0-9._:-]{1,128}`); `timeoutMs` (100–5000, default
1500) bounds one bridge request; `waitMs` (0–30000, default 0) bounds how long this call
waits for an answer and never extends, shortens, pauses or cancels the task. Unknown
parameters are rejected. Questions and answers are each bounded at 4000 UTF-8 bytes, and
at most 32 inquiries are retained per task. To read a specific question back, repeat
both the same text and the same `inquiryId`; an id alone is not a valid lookup, and the
same id with different text is a `CONFLICT` for active and terminal tasks alike.

### What a no-question inquiry reports

- Authoritative execution state straight from the durable record: `status`, `revision`,
  `createdAt`/`updatedAt`, `resultAvailable`, `shutdownConfirmed`, `cancelRequested`,
  the private log paths, and the selected `attemptId`, `attemptState`, generation and
  `workerId`.
- `deadline`: the recorded `timeoutSeconds`, the derived `startedAt`/`deadlineAt`,
  `elapsedSeconds`, `remainingSeconds` and `expired`, all labelled as an ESTIMATE:
  `estimated: true`, `kind: "estimated-runner-deadline-from-record-createdAt"`,
  `clockOrigin: "task record createdAt (before the adapter spawned anything)"`,
  `deadlineBasis: "createdAt + timeoutSeconds"` and `exactTimingAvailable: false`. The
  worker arms its own timer only after it spawns the adapter, so the real deadline is
  later than `deadlineAt`; inquiry never extends, shortens or restarts it. When an old
  record predates the field it reports `available: false` with the reason instead of
  inventing a deadline.
- `bridge`: whether this attempt has an inquiry bridge (`enabled`, `observed`, `reason`,
  `error`). A task with no `dsh` bridge is reported honestly — for the `command` and
  `external` adapters the reason is `this adapter has no inquiry capability`, and a task
  whose bridge credentials do not exist yet says so instead of faking progress.
- `live`: what the run's own bridge can actually observe — `agentStatus`
  (`idle`/`running`), inbox depth, `lastEvent` and up to 20 bounded activity entries
  for tool calls (tool name, phase, timestamps, duration, error flag, and a
  ≤160-character preview of the tool ARGUMENTS only). Every field the bridge could not
  observe is named in `live.unavailable`; this is bounded observation, not a percentage
  or a guaranteed ETA.

`live.agentStatus: "running"` means an agent driver is active. It is **not** a claim of
productivity: a run can be running while stuck, retrying or waiting on a long tool, and
a long tool call can delay an answer until a later step. An idle or terminal agent
cannot be awakened by an inquiry. Raw model reasoning is never exposed, and neither the
assistant stream nor the assistant text is copied into this payload.

### What a question does

1. The service validates and durably records the inquiry *before* anything is injected.
   Repeating the same `inquiryId` with identical text returns the recorded state (and
   can still read an existing answer) and never injects twice; the same id with
   different text is a `CONFLICT`. At most 32 inquiries are retained per task.
2. For a live `dsh` task it asks the run's own bridge to deliver the question to the
   SAME live agent. Nothing starts a second agent, cancels, restarts, re-scopes or
   extends the task. A question against a terminal task or an adapter without an
   inquiry capability is recorded as `unavailable` with the honest reason rather than
   reported as delivered.
3. Message states are `queued` (recorded, waiting for a delivery boundary — normal
   during a long tool), `claimed` (the agent loop took it for a proposed step, which is
   not delivery), `delivered` (the run's durable commit of the correlated message),
   `answered` (a correlated reply was recorded), `discarded` (the agent dropped the
   injected message before a boundary) and `unavailable` (no bridge, a terminal task or
   a question that can no longer be claimed or answered). A terminal task never keeps a
   misleading permanent `queued` state.
4. **Answers require the correlated reply tool.** An answer is recorded only from the
   run's own reply evidence carrying the exact `inquiryId`; blank answers are refused,
   and a question the loop never committed can never be answered. Assistant prose is
   never treated as an answer. `answered` carries the answer text (≤4000 UTF-8 bytes),
   the tool call id, the timestamp and its provenance (`live-bridge` or
   `bridge-journal`). The first correlated answer wins; later observations never clear
   or replace it. `buddy message-update` records the same evidence programmatically.
5. `waitMs` only bounds this call's wait for an answer (it polls the recorded message
   and the bridge until the deadline). An interrupting wait never cancels, fails or
   re-scopes the task.

Ask explicitly when the user asks or when you need the information to report honestly —
do not build a polling loop.

### Transport and ownership

The bridge socket itself lives in the Node dsh plugin, because upstream's plugin runs
in Node. The Python service is the client of that socket and the importer of its
bounded JSONL journal:

- **Journal**: the bridge appends a bounded journal next to its socket, so an answer
  that arrived before the run ended is still readable afterwards. The Python service
  imports it as **idempotent transport evidence** keyed by `inquiryId`; the durable
  board message rows stay authoritative and the import can never overwrite a recorded
  answer.
- **Credentials**: the `dsh` adapter writes the per-attempt socket path, results path
  and a 256-bit token into the attempt's private `inquiry.json` (mode `0600`). The token
  never appears in any public task view or envelope, so it cannot leak through
  `status`/`result`.
- **Ownership boundary**: the worker owns the runner process group and the adapter
  writes the bridge credentials; the Python service owns every board mutation and the
  journal import. A pending `buddy await` is never blocked by inquiry transport.
- **Failure mode**: a missing, unreachable or failed bridge never fails the task. The
  task result carries the bridge paths, and every later `inquire` reports the same
  honest reason (`bridge-unreachable`, `bridge-timeout`, `bridge-start-failed`, …)
  instead of inventing progress.

Not claimed: inquiry cannot see inside a running tool, cannot force an answer from a
model that ignores the injected message, and cannot recover an answer that was never
recorded by the reply tool or the bridge journal. When any of those happens the envelope
says `delivered`/`queued` with `answer.available: false` and the observed activity, and
it is never reported as answered.

## CLI commands

Every command takes one JSON object argument (or none) and prints one JSON object.

| Command | Parameters / behavior |
| --- | --- |
| `buddy run` | Optional one-call convenience: `requestId`, `task`, `cwd`; optional `model`, `provider`, `effort`, `timeoutSeconds`, `workspace`, `adapter`, `argv`, `requiredCapabilities`, `exclusiveResources`, `waitSeconds` (1–86400). Starts or recovers once, stays connected until the task is terminal or this call's wait window ends, prints the final envelope with `outcome`/`limitation`/`recovery` |
| `buddy await` | `requestId` or `runId`, optional `waitSeconds` (1–86400, default 86400); waits on an existing task and never starts work |
| `buddy start` / `buddy submit` | Same submit parameters without `waitSeconds`; returns the task view immediately, `status: "queued"` with `queueReason`. `start` is the workflow command; `submit` is the explicit board operation used by multi-agent callers |
| `buddy status` | `runId`, `taskId` or `requestId`; the task view (state, `queueReason`, selected attempt, worker, artifacts, inquiry counts) |
| `buddy wait` | `runId`, optional `afterRevision`, `timeoutMs` 0–30000; one bounded task wait, returns the task view |
| `buddy watch` | `after` cursor, optional `timeoutMs` 0–30000, `limit`, `taskId`/`runId`; bounded event wait |
| `buddy events` | `after` cursor, optional `limit` 1–200, `taskId`/`runId`; committed events with a resumable cursor |
| `buddy result` | `runId`; task view plus the adapter outcome under `result` and commit metadata under `resultMeta` |
| `buddy list` | optional `limit` 1–100, `offset`, `state`, `adapter` |
| `buddy cancel` | `runId`; cancels queued work immediately and writes durable cancel intent for an active attempt. Unrelated dsh sessions are unaffected |
| `buddy retry` | `runId`; explicitly creates a new attempt generation for a non-running task and clears the previous acceptance (archived in the event stream). An attempt whose shutdown is unconfirmed cannot be retried (`SHUTDOWN_UNCONFIRMED`); only the worker that owns the process handle can report an observed outcome or release an attempt that never spawned |
| `buddy acknowledge` | `runId`, evidence `note`, `verdict` `accepted`/`rejected`; records review of a real outcome once the result is persisted and shutdown is confirmed. It never changes the execution status; a different repeated verdict/note is a `CONFLICT` |
| `buddy inquire` | `runId`; optional `inquiryId`+`question`, `timeoutMs`, `waitMs`. See the inquiry section above |
| `buddy message` / `messages` / `message-get` / `message-update` | Post, list, read and update correlated inquiries/answers for one task. `message` accepts `waitMs` |
| `buddy artifacts` | `runId` or `attemptId`; verified artifact records (location, hash, size) |
| `buddy workers` | optional `state`, `adapter`, `limit`; registered workers and their current attempt |
| `buddy worker-register` / `worker-claim` / `worker-reconcile` / `worker-renew` / `worker-progress` / `worker-result` / `worker-release` | The public worker contract; normally used by `BoardClient`, not by hand |
| `buddy worker-start` / `worker-stop` | Start one detached worker supervisor, or write a cooperative durable stop request for it |
| `buddy wait-capacity` | Wait-admission counters (`capacity`, `admitted`, `rejected`, `available`) |
| `buddy capabilities` / `buddy adapters` | Adapter report, local capabilities, named operations, wait admission, honest limitations |
| `buddy runtime` | Runtime identity plus installed-runtime description and source-leak report |
| `buddy health` | Service health: protocol/contract/schema versions, service id, state dir, runtime identity, `maxConcurrent`, `waitCapacity`, `persistenceError`, SQLite integrity and active work |
| `buddy dashboard` | `action` `open`/`close`/`status`; private read-only loopback panel |
| `buddy legacy-import` | `sourceDir`, optional `dryRun` (default `true`), `strict` (default `true`), `requestIds`, `snapshot` (default `false`); offline, transactional import of the removed Node records. A live source directory needs `snapshot: true` or an immutable copy |
| `buddy restart` | Detaches the daemon without cancelling work; independent workers survive and reattach |
| `buddy stop` | Cancels queued tasks, writes durable cancel intent for active attempts, drains for `drainSeconds` (default 10), returns `unresolvedAttempts` |

```sh
BUDDY=<plugin-root>/deepseek-delegate/scripts/launch-buddy.sh
# Default: start once, keep the runId, then await that same durable task (never starts work):
"$BUDDY" start '{"requestId":"x","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
"$BUDDY" await '{"runId":"<runId>","waitSeconds":7200}'
# Optional one-call convenience: blocking run whose default window covers timeoutSeconds + 60 s:
"$BUDDY" run '{"requestId":"x","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
# Explicit board submission for another adapter (one argv process, never a shell):
"$BUDDY" submit '{"requestId":"y","task":"...","cwd":"/abs/path","adapter":"command","argv":["/bin/echo","hi"]}'
# Bounded progress, and one correlated question to the same live agent:
"$BUDDY" inquire '{"runId":"<runId>"}'
"$BUDDY" inquire '{"runId":"<runId>","inquiryId":"q-1","question":"what is blocking you?"}'
```

There is no automatic timer- or model-triggered scheduling for inquiry: a model with
shell access calls the CLI explicitly when the task's permissions allow it.

CLI subcommands print one JSON object, and exit 0 only means the call returned.
It is not task success: inspect `status`, `outcome`, `resultDelivered` and
`shutdownConfirmed`, then read `buddy result` and verify the actual artifacts. The
standalone `scripts/run.mjs` runner has a different contract: its own exit codes 0/1/2
and its raw single-line result object (`status`, `exitCode`, `workspace.bound`,
`processState.shutdownConfirmed`).

## Persistence and recovery

`BUDDY_STATE_DIR` defaults to `~/.local/share/hey-my-buddy`. One daemon owns the
directory; its layout is:

- `board.sqlite3` — tasks, attempts, workers, messages, artifacts, events, command
  receipts and resource claims, with SQLite integrity checked by `buddy health`;
- `control.json` — the private C-Two endpoint address and service token (never
  group/world readable);
- `control-daemon.lock` / `board-owner.lock` — the lifetime ownership locks;
- `workers/<workerId>/` — supervisor lock and status, durable stop request, startup
  intent and the receipt spool;
- `attempts/<runId>/<attemptId>/` — task text, spawn intent/marker, adapter logs, and
  the `inquiry.json` bridge credentials;
- `worker.log`, `control.log`, `restart.resume.json`, `stop.request.json`.

An already healthy service is attached read-only: the client reads the private endpoint
and confirms health over C-Two, without creating directories, changing permissions,
taking lock files or writing logs. That matters when the state directory is readable but
not writable, for example under a restricted sandbox. Only a cold start performs the
authorized setup writes (create the directory, apply `0700`, lock, log, spawn), and it
still fails honestly when those writes are not permitted. Endpoints are trusted only
when the directory and `control.json` are owned by the current user with no group/other
access and no symlink in the way; anything else falls through to cold startup instead of
being used. A `wait`/`watch` never cold-starts a service at all.

A request ID is an idempotency key. Retry the same input and ID after an uncertain
start; changed input is rejected. Results survive service restart. Attempts whose
shutdown was never confirmed stay `uncertain`/`reconciliation-needed` with their claims
retained, are never replayed, and refuse a retry until the worker that owns the process
handle reports an observed outcome. No stored PID is used to signal old processes.

`buddy stop` asks the service to stop: queued tasks are cancelled, active attempts get
a durable cancel request, the service drains for a bounded interval (`drainSeconds`,
default 10, range 0–120), asks its own worker supervisor to stop cooperatively, and its
response lists `unresolvedAttempts` whose receipts and resource claims are retained.
Complete results and every durable record remain on disk. `buddy restart` writes a
resume file, detaches without cancelling anything and leaves independent workers
running; the next CLI command starts a fresh daemon. With no running daemon, `stop` and
`restart` report the already-stopped envelope and start nothing.

### Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `BUDDY_STATE_DIR` | `~/.local/share/hey-my-buddy` | state directory (database, endpoint, workers, attempts) |
| `BUDDY_MAX_CONCURRENT` | `1` | simultaneous active attempts, clamped to 1–8 |
| `BUDDY_WAIT_CAPACITY` | `32` | admitted waits on the dedicated `buddy-wait` resource |
| `BUDDY_LEASE_SECONDS` | `120` | attempt lease, clamped to 15–3600 |
| `BUDDY_DEBUG` | unset | when set, internal errors include the exception detail |

Also honoured: `BUDDY_RUNTIME_ROOT` / `BUDDY_RUNTIME` (stable runtime location/pin),
`BUDDY_NODE` / `BUDDY_RUNNER_PATH` (dsh adapter node binary and runner entrypoint),
`BUDDY_WORKER_ID` (supervisor worker id), `UV_BIN` (uv binary for runtime installs).

## Dependencies and validation

All third-party libraries are managed by uv and locked: PyPI `c-two==0.5.1`, PyYAML,
and their pinned transitive dependencies. The `mcp` Python SDK is not a dependency and
must not be importable in the project environment. Node.js remains an external runtime
required by dsh and by the `dsh` adapter. No npm package install is required for Buddy.

```sh
uv sync --project deepseek-delegate --python 3.12
uv run --frozen --project deepseek-delegate python -m buddy.checks
```

The Python suite drives the real CLI and daemon in private state directories and covers
transactional rollback and integrity, duplicate submission and claim replay, queued
capacity admission, overlapping cwd and exclusive-resource reservation, the crash
windows before/after spawn and after commit, a real daemon restart that preserves the
same attempt, worker, deadline and result, stale-generation and wrong-capability
rejection, lease uncertainty, cursor replay with more waiters than capacity while
cancel/renew/result keep committing, the cancellation race, inquiry deduplication,
bounds and answers across a restart, the `dsh`/`command`/`external` adapters including
an external worker over the public client, runtime materialization that never copies
credentials or environments, and the offline legacy import (dry-run, idempotency,
rollback and fingerprint validation). The dependency-free Node runner suite runs
alongside it. Live App/dsh integration is verified separately; unit tests never imply
that a paid model run was exercised.

## Packaging, migration and unrelated App tools

The portable `plugin.json` and the `.codex-plugin/plugin.json` metadata describe a
skills+CLI plugin: there is no `mcp.json` companion, and `scripts/stage-plugin.py`
refuses to stage a tree that still contains one (or any other removed MCP facade path).
The contained launcher (`scripts/launch-buddy.sh`, or `scripts/buddy.mjs` for Node)
resolves the project from its own location, makes uv reachable under a minimal PATH and
forwards every argument to the `buddy` CLI. The launcher selects Python 3.12 through uv
and uses a version-specific environment under `PLUGIN_DATA` when provided; it does not
depend on the calling task's working directory.

**Stable runtime.** Dependency management stays uv-only, and the service can execute
from a content-addressed runtime outside the Codex plugin cache, which may disappear on
upgrade:

```sh
uv run --frozen --project deepseek-delegate python -c "from buddy import runtime; runtime.materialize()"
uv run --frozen --project deepseek-delegate buddy runtime
```

`runtime.materialize()` copies the complete runtime assets (Python package, uv lock,
dsh runner and plugins) into the final content-addressed directory under
`BUDDY_RUNTIME_ROOT` (default `~/.local/share/hey-my-buddy/runtime/<contentId>`), *then*
runs `uv sync --frozen` there, and writes `READY.json` last — the directory is not used
until that marker exists, an install lock makes concurrent installers cooperate, and a
failed install is removed. Credentials, user data, tests, `node_modules`, `.env` files
and any existing virtual environment are never copied. `buddy runtime` reports the
runtime identity, the installed-runtime description and a source-leak report;
`buddy health` reports `runtimeIdentity`, `runtimeStable` and `runtimeContentId`.
A `READY.json` marker alone is not a claim of stability: `stable` is true only when the
process actually imports `buddy` from inside that runtime and no asset or interpreter
path leaks back into a plugin cache or the checkout. When a READY runtime exists, a cold
start launches the daemon — and the daemon launches its worker supervisor — from that
runtime's own interpreter, so the service survives the plugin cache being replaced;
without one it is an explicit development launch from the checkout and `buddy runtime`
honestly reports the in-place source identity (`source:<id>`, `stable: false`).
`BUDDY_RUNTIME` pins an explicit READY runtime.

To stage a distributable copy without environments, logs, or experiment artifacts, use
`uv run --project deepseek-delegate python scripts/stage-plugin.py --destination /path/to/hey-my-buddy`.
The destination is separate from the source checkout and prior staged copies are retained
as backups.

**Legacy import.** The removed Node implementation's durable records can be imported
offline with one transaction:

```sh
"$BUDDY" legacy-import '{"sourceDir":"/old/state","dryRun":true}'
"$BUDDY" legacy-import '{"sourceDir":"/old/state","dryRun":false}'
```

It refuses to run while the old owner is alive (`LEGACY_OWNER_RUNNING`: a live
`control.json` IPC endpoint, a held ownership lock, or a reachable legacy
`service.sock`) or while any legacy run is still active (`LEGACY_ACTIVE_RUNS` for
`running`/`cancelling`/`completing`), and a strict run aborts the whole import on any
malformed, duplicated or conflicting record (`LEGACY_CONFLICT`). Pointing `sourceDir` at
this service's own live state directory is refused as `LEGACY_SOURCE_IS_LIVE_TARGET`;
`"snapshot": true` instead takes a private read-only snapshot of just the legacy
`record.json`/`task.txt` files and imports that copy (reported as `snapshotDir`).
`task.txt` is validated against the recorded legacy input fingerprint, and a mismatch is
a conflict rather than a silent import. Original run IDs, request IDs, fingerprints,
outcomes, inquiry and acceptance evidence and timestamps are preserved; an active legacy
run is never turned into a completed one; the source files are only read and never
modified. A record whose task text is missing is preserved as readable history with an
explicit recovery limit in its `queueReason`, and its request ID can never launch a
duplicate: an identical legacy start request resolves to the imported task, while changed
input is a `CONFLICT`. The dry run reports counts, conflicts and notes without writing
anything.

**User migration (manual; Buddy never edits user config).** Removing the MCP path means
old registrations are dead weight. Delete them yourself when convenient:

- `[mcp_servers.buddy_ctwo]` and any `plugins."hey-my-buddy@…".mcp_servers.*` entries in
  `~/.codex/config.toml`, including their `tool_timeout_sec`, `approval_mode` and
  `BUDDY_TOOL_WAIT_BUDGET_SECONDS` fields;
- any plugin-scoped Buddy tool approval remembered by the App;
- the now-unused `mcp.json` in an older plugin cache copy.

Nothing replaces them: the CLI needs no registration, and its commands run with the
permissions of the shell that invokes them.

Unrelated Codex/App tools are untouched. In particular the official App heartbeat
automation used for explicit background follow-up is a separate tool: its prompt simply
calls the Buddy CLI, and it can still be approved independently.

```toml
[plugins."codex-app-tools@openai-bundled".mcp_servers.codex_app.tools.automation_update]
approval_mode = "approve"
```

These policies persist across restarts and plugin version updates while the identifiers
stay the same. Existing task connections may retain earlier policies until unloaded and
resumed. Verify adoption with a real tool call; parsing the TOML alone is insufficient.
An already pending approval is not retroactively approved by changing the file. The App's
`Approve for me` option instead evaluates eligible calls through its approval reviewer.
