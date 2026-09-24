---
name: buddy
description: Delegate bounded coding, testing, investigation or documentation to local Buddy harnesses with model routing, explicit workspaces, Host-directed assistance and verified artifacts. Use when scope and acceptance are clear; skip trivial edits and unresolved requirements.
---

# Hey My Buddy

Use Buddy to assign coherent work to an appropriate local harness/model while the Host retains technical ownership and final acceptance. The Host may implement other parts itself. Worker-internal subagents remain available; a Worker may request Host-authorized help but may not create peer Buddy tasks on its own.

## Start a bounded goal

Read [usage](../../docs/reference/usage.md) for installation and task packets. The plugin root contains `bin/buddy`; resolve that path from this skill's location and invoke the launcher with one JSON argument. Check `health`, `runtime` and `capabilities` on setup or recovery. This release accepts only its current contract and schema. A board with an unsupported schema must remain archived; use a clean state directory rather than a conversion or fallback. A contract-only update with the same schema retains board data; finish owned work and switch clients, daemon and idle workers together as described in [runtime lifecycle](../../docs/reference/operations.md#runtime-lifecycle).

Include the objective, permitted files, required inputs, expected artifacts and acceptance commands. Keep reusable instructions stable and task facts concise; a higher cache-hit percentage alone does not establish lower total cost.

Choose `executionWorkspace` explicitly. Sequential ownership may use `existing`; parallel writers need separate `worktree` checkouts, an exact base/input snapshot, declared write scope and an integrator. Preserve the same boundary for Host edits. A checkout is not an OS sandbox.

The daemon starts a worker pool automatically: two business attempts and one independently reserved decision attempt by default. Await each delegated goal by its own `runId`; a capacity wait does not authorize duplicate submission. `health.capacity` identifies the full lane. Workspace conflicts and unconfirmed shutdown still retain capacity; use [runtime settings](../../docs/reference/operations.md#private-state-and-environment) to set startup limits.

Honor explicit adapter/provider/model/effort constraints. A complete tuple is validated and dispatched without a selector call. Partial fields are hard filters; omitted fields use the configured decision Buddy and the bounded current table. `submit` records the goal before routing and returns its durable identity. Never substitute a DSH default, scan the entire history, or select the selector recursively. With no selector/legal candidate, the goal waits for Host input. The user chooses the initial fixed decision profile; see [evaluation](../../docs/reference/evaluation.md).

```sh
BUDDY="<absolute-plugin-root>/bin/buddy"
"$BUDDY" submit '{"requestId":"<stable-id>","hostId":"<this-host>","task":"<bounded packet>","cwd":"/abs/repo","workspace":false,"timeoutSeconds":1800,"executionWorkspace":{"kind":"worktree","cwd":"/abs/repo","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["src/state.ts","tests/state.test.ts"],"integrator":"<this-host>"}}'
"$BUDDY" await '{"runId":"<returned-run-id>","waitSeconds":1860}'
"$BUDDY" get '{"runId":"<returned-run-id>"}'
```

The example leaves the model choice to routing and disables DSH session grouping with `workspace:false`. Grouping is a DSH option independent of execution-workspace isolation; an explicitly requested grouping choice must be preserved. For direct dispatch, add all four validated fields: `adapter`, `provider`, `model`, `effort`. Check native availability and legal efforts rather than assuming every harness exposes the same options.

## Supervise through acceptance

Save the returned `runId` and `controlFile`. The CLI stores Host authority in a private file; never copy its token into task prompts, command arguments or public artifacts. Mutations require the exact saved control file or explicit current owner credentials. A replay cannot adopt another Host's newer generation.

Use `await` to stay with the task. A wait timeout, a closed terminal or a lost connection does not cancel execution or authorize a second task. Recover the same request/run; changing input under the same request ID conflicts. `inquire` provides bounded observation. Correlated live questions are adapter capabilities, not universal model support.

When the turn ends with an assistance request or attention boundary, read the compact `get` view and fixed artifacts. Use `decide` to approve explicit helpers or decline with a reason; approval can authorize one automatic continuation after their results. Use `continue` for Host input. A routing boundary accepts a complete `configuration` or `reroute:true` on the same goal; original hard constraints still apply. Consult [workflow](../../docs/reference/workflow.md) for nested requests, `targetRunId`, worktree integration, takeover and cancellation.

Each continuation is a new owned attempt. DSH reconstructs a fresh session. ZCode resumes a proven native session only when its goal, checkout and configuration binding matches; without a proven previous session or after a configuration change, it reconstructs a new root session. A missing or mismatched binding on a native-session request fails explicitly. Keep requested configuration, native settings readback and actual served-model evidence distinct.

Inspect the final artifact's real diff and run the relevant checks before `acknowledge`. Bind acceptance to the final artifact ID; a completed helper, RPC reply or process alone is not acceptance.

```sh
"$BUDDY" acknowledge '{"runId":"<run-id>","commandId":"<stable-review-id>","controlFile":"<returned-path>","artifactId":"<final-artifact-id>","note":"<actual checks and findings>","verdict":"accepted"}'
```

Cancel the goal with `cancel` and its control file, then wait for actual shutdown evidence across its owned work. Lease expiry or a missing PID never proves shutdown. A service restart preserves owned work; a service stop requests cancellation. Do not stop the service to resolve one waiter timeout.

Keep the Host turn active while delegated work is running. For user-requested work beyond the turn, register the product's official recurring follow-up on this task as described in [background work](../../docs/reference/usage.md#background-work-that-outlives-the-turn). It is periodic follow-up, not immediate native App wakeup. If unavailable, keep waiting. Never end with an unmonitored job or restart work the user stopped.

## Configuration and evidence

The private React/Vite console has top tabs for delegation records, model cards and routing configuration. Records are grouped by source project with original/current Host attribution and paginated history; click the executor for that delegation's frozen routing rationale and prior decisions. Model families expose effort variants without merging evaluations; their page also contains evaluation maintenance and retained suggestions. Routing configuration contains settings only. Use the bounded [evaluation workflow](../../docs/reference/evaluation.md) to record scoped observations and request maintenance. Automatic card publication is opt-in; model-generated maintenance cannot change profiles, user preferences or authority. Cost accounting and economic budgets are not required for execution.

Read [CLI](../../docs/reference/cli.md) for exact fields and bounds, [workers](../../docs/reference/workers.md) for harness/authentication capabilities, [operations](../../docs/reference/operations.md) for lifecycle and recovery, and [architecture](../../docs/reference/architecture.md) before changing service contracts. The advanced execution/worker primitives support command, external and internal decision processes; coding goals always use `submit`.
