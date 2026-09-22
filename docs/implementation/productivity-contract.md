# Productivity workflow implementation contract

This implements accepted ADR-002 on top of the existing Python/C-Two blackboard and the decision console. Acceptance requires a real repository task to yield, receive Host-authorized assistance, consume a fixed helper artifact in a later turn and reach Host acceptance through the packaged skill/CLI. Current legacy `start` semantics remain available; the skill's governed workflow uses the operations below. Cost/billing, A2A, peer dispatch and original-session resume are not prerequisites.

## Responsibility and ownership

The Python service owns logical task/turn/request/decision/workspace-claim state. Independent Workers own process handles and durable result receipts. The Node DSH adapter records an authoritative structured turn outcome through a root-agent-scoped terminal tool. Workspace Git/file operations are performed outside database transactions, produce immutable manifests, and are validated before their authoritative activation. No long database transaction spans Git, an LLM call or waiting.

Existing `runId` remains the logical task identity. Each actual execution gets the existing independent attempt generation plus a turn ID. The original goal/spec/fingerprint is immutable; a continuation's effective prompt and input are recorded separately. Retry repeats a failed execution; continuation consumes new input after a concluded turn. The implemented adapter initially reports `reconstructed-new-session`, including previous/current session identities when known, and never calls it native session resume.

Host decisions require a task control capability plus owner generation and expected task/request revision. An owner name is attribution, not authority. Host takeover replaces the capability/generation and fences delayed old decisions. A private console user can explicitly override/take over through an internal authenticated user principal; that override is not a caller-controlled JSON flag on C-Two. Child agents receive scoped credentials that the service enforces: own-task observation and suggestions cannot submit tasks, approve requests, change ownership, write evaluations or control other tasks. Existing same-user full-shell limitations remain explicit; this is capability enforcement in the supported API, not OS isolation against credential theft.

## Public workflow operations

Use named C-Two operations and matching hyphenated CLI commands. Return the normal error envelope. All mutation IDs are stable idempotency identities and conflict on changed input.

| Operation | Input and effect |
| --- | --- |
| `workflow_submit` | `requestId`, `hostId`, ordinary `spec` (task/timeout/route/workspace-grouping fields), and `executionWorkspace` intent. Prepare/validate workspace, admit an ordinary governed task, return task plus private owner control. |
| `workflow_get` | `runId`, optional `includeAudit`. Compact goal/turn/wait reason, current owner generation, active request, children and pinned artifacts by default. Never exposes control/scoped tokens. |
| `workflow_decide` | `runId`, `requestId` of the assistance/attention request, `commandId`, `expectedRevision`, control fields, `decision: approve|decline`, `reason`, `helpers` (explicit ordinary task specs + execution workspace intents), `autoContinue`. Only a Host decision creates helpers. Decline does not fail the parent goal. |
| `workflow_continue` | `runId`, `commandId`, `expectedRevision`, control fields, bounded `input`, and an explicit policy for any still-active helpers (`cancel` or `keep`). Record one continuation and requeue the same logical task when shutdown and workspace ownership permit. Manual continuation invalidates old automatic triggers. |
| `workflow_takeover` | `runId`, `commandId`, expected owner generation, new host ID, current control capability (or authenticated console-user authority). Rotate owner capability; do not rerun or cancel work merely because ownership changed. |
| `workflow_cancel` | governed cancellation with current control or explicit console-user authority; cascade durable cancellation to owned helper descendants and revoke automatic continuation. Honest shutdown rules remain unchanged. |
| `workflow_acknowledge` | final artifact review by current owner/console user; acceptance is separate from execution and bound to the selected final artifact/attempt. Release logical workspace reservation when appropriate. |

Control fields are `hostId`, `ownerGeneration`, `controlToken`. CLI may accept a `controlFile` that injects these fields locally, saves newly returned capabilities to a private file and returns its path instead of printing tokens. It must fail closed in an agent-scoped context. Capture authority for this Host; never silently adopt a different Host's latest generation from a shared cache. `task_cancel/retry/acknowledge` must not bypass governed controls for a governed task. Legacy tasks keep their previous behavior.

`workflow_submit` and approvals use explicit model parameters or a known selected profile; the original `workspace` boolean still means DSH session grouping. An `executionWorkspace` is a separate contract. If route selection is needed, reuse the fixed decision profile and compact selected configuration; do not inject the entire evaluation table into the Host context.

`await`/`run` must understand a governed control boundary: a normal structured yield returns `waiting-host` with its turn result, not a fake goal completion and not an indefinite wait. Waiting for already-authorized helpers can continue within the same durable wait. Helper success, failure, cancellation and attention all produce a visible follow-up path; a parent never waits forever for a success-only event. If a helper yields for more information, surface that decision immediately even when siblings still run. Automatic continuation is a one-use authorization tied to the request/revision and prepared workspace, not permission for another delegation.

## Structured DSH turn protocol

The Python adapter writes `turn-input.json` in the private attempt directory and invokes existing `scripts/run.mjs` with `--turn-input-file <absolute path>` and `--turn-output-file <absolute path>`. Both flags must be present together; legacy runs omit both. They do not depend on the optional DSH workspace grouping bridge.

Input schema (service/Worker-owned metadata, never chosen by the model):

```json
{
  "version": 1,
  "taskId": "logical run ID",
  "attemptId": "attempt ID",
  "generation": 1,
  "turnId": "turn ID",
  "resumeMode": "initial",
  "previousSessionId": null,
  "context": {},
  "executionWorkspace": {}
}
```

`context` contains the original objective, last checkpoint, Host decision, helper outcomes and immutable artifact references, plus next actions. It is bounded and does not contain control or service secrets. New executions report `reconstructed-new-session`; no whole-transcript scan is required.

Register the terminal tool `buddy_finish_turn` only on the correlated root Agent. Its model arguments are:

```json
{
  "disposition": "completed",
  "summary": "what was done",
  "remaining": [],
  "decisions": [],
  "artifacts": [],
  "request": null
}
```

`disposition` is `completed`, `assistance` or `attention`. An assistance/attention `request` contains `summary`, `attempted`, `neededWork`, `expectedArtifacts`, `acceptance`, and optional `suggestedProfileId`; it never creates another task. Model artifacts are bounded references, not trusted existence/hashes. Python seals/verifies actual deliverables after confirmed process shutdown. Empty/malformed/missing required output fails honestly and preserves logs; final prose is not parsed for success.

Follow the installed native `attachStructuredRuntime()` pattern: stage arguments by execution identity, call `exec.concludeTurn()`, capture successful `tools/result` (including accepted enclosing `run_code`), require matching root session tool/result and completed turn/end, and atomically write the record from awaited `session/flush`. Guard later tools after the terminal result. Root-agent identity, prompt hash, continuation-input hash, task/attempt/turn metadata and event provenance come from runtime/configuration, not model arguments. Internal subagents remain available and cannot conclude the root task with their own tool call.

Output file is a version-1 object `{version,taskId,attemptId,generation,turnId,resumeMode,previousSessionId,sessionId,promptSha256,inputSha256,outcome,provenance}`. `outcome` is the validated model argument object; `provenance` records the accepted tool/turn/flush evidence. The existing runner JSON adds `turn` with this object and `turnResultPath`; its ordinary exit and process-group shutdown evidence still apply. A successful file alone does not prove shutdown or final goal acceptance.

## Workspace module boundary

The workspace implementation owns only `python/buddy/workspace.py` and focused tests. It exposes standard-library functions and raises `BoardError`:

```python
inspect(cwd: str) -> dict
prepare(state_dir: Path, request_id: str, intent: dict) -> dict
verify(manifest: dict, *, require_unchanged: bool = True) -> dict
seal(state_dir: Path, manifest: dict, task_id: str, attempt_id: str) -> dict
```

Intent fields: `kind: existing|worktree`, `cwd` (source path), `access: read|write`, `base: {kind: commit|working-tree, ref?}`, `includeUntracked` (explicit relative paths when needed), `writeScope` (relative files/directories), `integrator` and optional `targetRef` (attribution only, not a moving input). Resolve every ref to a commit. `existing` explicitly uses the specified checkout; `worktree` creates an isolated detached worktree under the private state directory from a pinned input. Original checkout sibling directories share one `checkoutId`; linked worktrees have separate IDs even though `repositoryId` is shared.

The manifest contains `version`, `workspaceId`, `kind`, `path`, `checkoutRoot`, `checkoutId`, `repositoryId`, `access`, `baseCommit`, `inputCommit`, `inputTree`, `writeScope`, `integrator`, `targetRef`, `snapshot` and `manifestSha256`. `snapshot` records included/excluded untracked paths and hashes of staged/unstaged input changes. Working-tree capture uses a private temporary index and immutable Git object/ref; never changes the user's HEAD, branch, index, staged split or files. Preserve file modes/binary files/symlinks without following external symlink targets. Unsupported non-Git snapshot cases are explicit errors; no false stable snapshot claim.

Preparing and sealing are idempotent/recoverable by stable identities. Interrupted worktree preparation must inspect/reuse its exact owned target or report an honest conflict; do not delete unknown or changed trees. Verify before execution; read-only input must remain unchanged. After the child stops, seal the actual Git tree/diff, binding `baseCommit`, `inputCommit`, output `commit`, `tree`, changed paths and snapshot hash. New outputs inside declared write scope can be included; outside-scope edits are reported. Keep immutable output refs even if a worktree is later removed. No automatic merge or force-clean operation is part of these functions.

The backend owns durable workspace reservations independently of attempt capacity. Retain write ownership across a yielded logical task until explicit handoff or completion/release. Multiple readers can share the same unchanged snapshot. Conflicting writers, including sibling cwd paths of one checkout, queue or return a concrete preparation conflict. Sequential helper reuse of a parent's checkout requires stopped-parent evidence and explicit Host transfer; independent helper worktrees may run in parallel. A resumed parent receives pinned helper artifacts, and its agent or the Host performs and verifies integration. Git/filesystem operations never run inside a SQLite write transaction.

## Production acceptance

Verify packaged skills/CLI against a private stable runtime, not only mocked stores or a development UI. Use an actual repository task with meaningful file changes and tests. Cover direct delegation, yielded assistance with one Worker slot, multiple independent helper worktrees and integration, decline/failed helper follow-up, explicit Host takeover with old-control rejection, cancellation and late completion, and restart/idempotent replay. Verify dirty staged/unstaged/untracked input transport without changing the source index. Keep the active installed runtime and production database intact during implementation; prepare any final upgrade/migration as an explicit, inspectable operation.
