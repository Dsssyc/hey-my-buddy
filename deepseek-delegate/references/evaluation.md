# Shared evaluation table and local console

The Python blackboard owns one current evaluation table per state directory. Its profiles, cards and preferences are shared across projects; evidence retains its project of origin, conditions and source. A project name identifies provenance, while the recorded conditions determine whether experience applies elsewhere. Credentials, raw repository contents and unfiltered logs are not evaluation cards.

## Open the console

```sh
"$BUDDY" console '{"action":"open"}'
```

Open the returned private loopback URL. The React/Vite interface shows durable tasks, model configurations, current cards, preferences and evidence. `console` also accepts `status` and `close`. The existing `dashboard` remains read-only and has a separate token; its URL cannot write through the console.

Page refresh reads the last complete publication. It neither invokes a model nor acquires a selection reader lease. Closing the page does not cancel a business task. Task cancellation and eligible retry remain available while somebody edits the evaluation table. An unconfirmed process outcome cannot be retried or accepted; a task cancelled before any attempt was claimed can be requeued without pretending a process was stopped.

The console binds to loopback and validates the exact Host. Writes require its private session cookie, exact Origin and a per-session CSRF token; the cookie is HttpOnly and SameSite=Strict. Browser requests and named C-Two operations use the same Python validators. This boundary does not claim isolation from another process running as the same OS user. Do not publish or share the private URL.

## Discover and enable profiles

Use **Edit evaluation table → Discover models**. Discovery reads the installed DSH provider's exported model/configuration metadata, records its source, and returns proposed profiles. It does not call a paid model or change global DSH settings. New profiles remain disabled until explicitly enabled and published; discovery does not invent a capability assessment. Catalog availability means the installed harness advertises the combination, not that a live provider call has succeeded.

A profile has an immutable execution identity (`adapter`, `provider`, `model`, `effort`). Publish a different profile for a different identity. Provider credentials stay with the installed harness. A soft `prefer` ranks legal candidates; `pin` and `exclude` constrain the candidate set. An unavailable or disabled profile cannot be pinned. Preferences express user intent and never rewrite observed outcomes.

## Edit and publish

An editor first acquires a durable writer intent against the expected table revision. Writer intent closes admission for new selection readers, then drains readers already admitted. One human or maintenance writer holds the active generation and lease. Business tasks already executing are not evaluation readers and keep their accepted configuration.

The browser renews its own lease while editing. Drafts stay separate from read refreshes. Publish validates the full requested collections and commits one new revision, its event and idempotency receipt atomically. Omitted collections remain unchanged; an empty array explicitly replaces a collection with nothing. Changing the payload under the same command ID is a conflict, including changing an omitted field to explicit null. A stale revision or writer generation cannot overwrite the current table. No SQLite transaction stays open while a user edits or a model runs.

Two tabs cannot both acquire the active writer. A waiting or expired grant is visible; an expired draft must reacquire authority and reconcile with the latest revision before saving. Abort releases only that writer's intent. Lease expiry fences publication and never constitutes evidence that a process stopped.

## Evidence and current cards

Evidence is append-only and attributed. Record a new correction instead of rewriting the original observation. `project` and `conditions` carry applicability; source URLs and actual run identities make a claim inspectable. Evidence can arrive during an exclusive edit and remains pending until incorporated into a published card. A preferences-only publication does not consume pending evidence. Automatic maintenance can compact current references while retaining the old card and its sources in immutable publication history; retired references are not requeued. Explicit human removal retains reconsider/requeue semantics.

Card authors provide summaries, strengths, limitations, risks and evidence references. The service derives revision, timestamps and sample counts; callers cannot submit fabricated counters. Samples require the appropriate Host verdict for the exact attempt and a matching known request configuration; cancelled, unreviewed and recognized infrastructure outcomes do not count. One attempt counts once even if described by several observations. Requested, resolved and observed identity remain distinct, so a requested alias is never presented as proof of the exact served model version. Pending and sample counters are maintained transactionally; ordinary refreshes and publications do not recount the historical ledgers.

The current publication is bounded: at most 200 profiles, 500 cards/preferences, 16 items per card list, and 64 evidence references per card. Summaries are at most 4,000 characters and individual points at most 500. Historical evidence and revisions remain outside the current card collection. See [the CLI reference](cli.md#evaluation-and-console) for command fields and bounds, and [architecture](architecture.md) for persistence ownership.

## Decision and maintenance jobs

Select a fixed enabled DSH decision profile in the console and publish it first. For example, `deepseek-official / deepseek-flash / off` uses the non-thinking route. This bootstrap choice is explicit; the system never selects a selector recursively. `autoMaintain` grants permission to adopt safe maintenance proposals when a maintenance request is explicitly made. It is not a periodic scheduler and does not trigger a model call from page refresh.

```sh
"$BUDDY" selection-request '{"requestId":"route-1","task":"Implement bounded React concurrency tests without changing the public API"}'
"$BUDDY" await '{"runId":"<returned runId>","waitSeconds":600}'
"$BUDDY" selection-get '{"decisionId":"<returned decisionId>"}'
"$BUDDY" evaluation-maintain '{"requestId":"maintenance-1"}'
```

These are durable jobs on the existing independent Worker, attempt, receipt and capacity mechanism. A queued selection takes no reader or execution slot while waiting behind a writer; it reads a complete current publication when claimed. Maintenance bypasses selector admission, uses the fixed profile and consumes the same finite Worker capacity. Cancellation, deadlines, restart fencing and lost-reply replay preserve ordinary process ownership. An internal decision job cannot be created with public `task_submit`, retried with `task_retry` or acknowledged as a business deliverable. An explicit new decision request gets a new request ID; an uncertain submission is recovered with the original ID.

`selection-get` returns a small summary by default. `selectedProfile` contains the frozen worker configuration to pass to a separately authorized business submission; `decisionModel` describes the model that computed the suggestion. `includeAudit:true` adds the exact persisted input, output, proposal and input hash. A recommendation does not submit a business task or grant execution permission. `needs-host`, an unavailable helper or an oversized input returns control to the Host; an already authorized fixed route or Host implementation can still satisfy the business goal.

Maintenance reads at most 64 pending records per call, includes their current cards and referenced sources, and reports the remainder. No candidate or referenced source is silently cut off to make input fit. The complete request is bounded to 128 KiB; an oversized request returns `needs-host`. Automatic adoption changes cards only, requires the active writer generation and expected revision, preserves existing risk/limitation strings exactly, and rejects fabricated references. Evidence conditions stay in the source records and history; the model is instructed to preserve their meaning in its summary, which is not a property schema validation can prove. Maintenance consumes only cited pending records; evidence arriving during the call remains pending. A valid no-op completes without publishing a revision. With `autoMaintain:false`, the proposal is retained for Host inspection without changing the table.

The native model call has a separate [helper contract](decision.md). It receives no coding tools, ordinary agent prompt or full historical scan. Its reported token usage is telemetry, not a billing estimate or monetary budget. Schema and protected-text checks do not prove semantic quality; calibration and actual Host-reviewed outcomes remain necessary.

## Frontend build

Source lives in `deepseek-delegate/console/`; a Vite relative-base build produces `python/buddy/console_assets/`. These checked-in assets are included in plugin staging and the content-addressed runtime. End users do not need npm. Contributors use a supported Node 24 LTS release and run `npm ci`, `npm run typecheck`, `npm test`, and `npm run build` from the console directory. Test the built console through a private source-backed Buddy state directory; Vite alone does not supply the authoritative API.
