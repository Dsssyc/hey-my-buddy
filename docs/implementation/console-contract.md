# Console implementation contract

This contract coordinates the React/Vite frontend and Python backend during implementation of ADR-002/004/005. It is not a claim that these operations are already released. The backend owns validation, authority, identities and all writes. The frontend owns presentation and keeps credentials only in memory or an HttpOnly session cookie, never localStorage.

## HTTP and packaging

Add `buddy console` as a separate writable, private loopback entrypoint. Preserve existing `buddy dashboard` as read-only. The console returns a separate unguessable URL, validates exact loopback Host and same Origin, uses no wildcard CORS, and rejects writes from the read-only dashboard. Browser writes require a same-origin anti-CSRF token issued by bootstrap plus the private console session. Responses are JSON; user text is rendered as text. Do not return service credentials, provider keys or worker claim secrets.

Production assets are built from `deepseek-delegate/console/` into `deepseek-delegate/python/buddy/console_assets/`. Vite uses a relative base so assets work below the private URL prefix. Browser API requests use that prefix. The Python HTTP surface serves only allowlisted static assets, without directory traversal. Source/runtime staging must include built assets; distributable use must not require npm installation.

`GET <console-prefix>/api/console` returns the snapshot below. `GET <console-prefix>/api/tasks/<runId>` returns the existing detailed task/result view. `POST <console-prefix>/api/command` receives `{operation, params}` and dispatches the same validated Python business operations used by C-Two/CLI. Require `X-Buddy-CSRF` on browser writes. Model calls never occur on a GET or ordinary refresh.

Success is `{ok:true,result:...}` for commands. Failure is `{ok:false,error:{code,message,details?}}`, with a matching 4xx/5xx status; no traceback. Bootstrap itself is the snapshot object, not a command envelope. The CSRF value is named `csrfToken` and held in browser memory.

## Bootstrap shape

```ts
type Profile = {
  profileId: string; label: string; adapter: string; provider: string;
  model: string; effort: string; available: boolean; enabled: boolean;
  capabilities: string[]; contextWindow: number | null;
  description: string; source: string; unavailableReason?: string;
};
type Preference = {profileId: string; mode: 'prefer'|'pin'|'exclude'; reason: string};
type Card = {
  profileId: string; revision: number; summary: string;
  strengths: string[]; limitations: string[]; risks: string[];
  evidenceIds: string[]; sampleCount: number; updatedAt: string | null;
};
type Evidence = {
  evidenceId: string; profileId: string; kind: string; summary: string;
  project: string | null; conditions: string[]; source: string;
  runId: string | null; createdAt: string;
};
type Decision = {
  decisionId: string; status: string; task: string; profileId: string | null;
  tableRevision: number; reason: string; evidenceIds: string[];
  createdAt: string; error?: string | null;
};
type Gate = {
  phase: 'open'|'draining'|'writing'; readers: number;
  writer: null | {writerId:string; kind:string; generation:number; expiresAt:string};
  waitingWriters: number;
};
type ConsoleSnapshot = {
  csrfToken: string; tableRevision: number; gate: Gate;
  configuration: {revision:number; decisionProfileId:string|null; autoMaintain:boolean};
  profiles: Profile[]; preferences: Preference[]; cards: Card[];
  evidence: Evidence[]; decisions: Decision[]; pendingEvidence: number;
  tasks: {runs: TaskView[]; total:number};
  capabilities: Record<string,boolean>; // real implemented availability only
};
```

`TaskView` retains the existing task view fields (`runId`, `task`, `status`, `owner`, `cwd`, `revision`, `createdAt`, `acceptedAt`, `acceptanceVerdict`, `spec`, `queueReason`, etc.). Future continuation/workspace fields are additive. Reader counts exclude viewers and tasks whose accepted route is already fixed.

## Commands for the first integrated slice

- `evaluation_write_begin`: `{requestId, expectedRevision, kind:'human'|'maintenance'}`. Queues a fair writer intent. Returns `{writerId,generation,writerToken,phase,expiresAt,tableRevision}`. Only this reply/renew exposes this caller's writer token; no token in shared views. A waiting writer is observable by its ID in the gate; retry by the same identity, never duplicate creation.
- `evaluation_write_renew`: `{writerId,generation,writerToken}`. Returns current grant/phase/expiry. Only an active, non-expired owner may renew. No indefinite lock after UI loss.
- `evaluation_write_publish`: `{commandId,writerId,generation,writerToken,expectedRevision,profiles?,cards?,preferences?,configuration?}`. Each provided collection is the full desired bounded collection for this revision; omitted collections remain unchanged. Configuration is `{decisionProfileId,autoMaintain}`. Atomically validates and publishes one complete new revision and releases the grant. Immutable profile execution identity may not be rewritten under the same ID. Empty arrays are intentional, not omission.
- `evaluation_write_abort`: `{commandId,writerId,generation,writerToken}`. Cancels pending/active ownership. Late publications are rejected. Abort/release does not claim any model process was stopped.
- `model_catalog_refresh`: `{requestId}`. Explicitly discovers DSH metadata without running a model or exposing credentials. Returns catalog metadata; publishing refreshed profiles obeys the gate, rather than bypassing it. Known model/effort legality and actual availability must be distinguished. Do not invent capability/evaluation evidence.
- `evaluation_evidence_record`: `{commandId,profileId,kind,summary,project?,conditions?,source,runId?}`. Bounded source-attributed evidence; receipts can be added during a writer interval. De-duplicate by identity; unverified manual/worker reports must not count as accepted task successes. Existing evidence is not silently rewritten.
- `task_cancel`, `task_retry`, `task_acknowledge`: existing semantics and input shapes; the UI forwards a selected task identity and the backend validates current state. These controls stay usable while the evaluation gate is closed. Retry never bypasses shutdown evidence.

The next slice adds `selection_request` and `evaluation_maintain` using the fixed decision profile, plus Host-directed yield/assistance/continuation and takeover. They must not be falsely advertised before implementation. Keep snapshot fields present as empty collections until real persisted data exists; no simulated progress or invented ratings.

## Implementation ownership

DSH backend work owns Python storage/service/CLI/C-Two/HTTP, metadata discovery helpers and Python tests. Host frontend work owns `deepseek-delegate/console/` and build output. A later integration pass handles packaging, documentation and real-browser acceptance. Coordinate contract changes explicitly before changing a field used by the other side.

## Acceptance

Use private state/runtime roots. Verify stale/two-tab writers, duplicate publication, reader draining, writer fairness, failure/timeout fencing, published snapshot immutability, pending evidence persistence, long business execution overlapping table updates, ordinary GETs making zero model calls, cross-origin/read-only-token write rejection and path traversal rejection. Test schema migration against a private v5 fixture with backup, preserving old task/attempt/event identities; never migrate the user's live state as part of development.
