# Shared evaluation table and local console

The Python blackboard owns one current evaluation table per state directory (schema 8). Its profiles, cards and preferences are shared across projects; evidence retains its project of origin, conditions and source. A project name identifies provenance, while the recorded conditions determine whether experience applies elsewhere. Credentials, raw repository contents and unfiltered logs are never evaluation cards.

## Open the console

```sh
"$BUDDY" console '{"action":"open"}'
```

`console` accepts `open` (default), `status` and `close`, and serves the built React/Vite bundle over a private loopback HTTP surface. Open returns a fresh unguessable URL of the form `http://127.0.0.1:<port>/<token>/`, a `readOnly: false` flag, whether the built assets exist and an `alreadyRunning` flag; closing the console rotates its token, session and CSRF values and never stops a task. The older read-only `dashboard` command no longer exists, and no separate read-only surface is shipped.

`console-snapshot '{}'` reads the same authoritative data without a browser session, and `console` status reports the current URL, running flag and asset directory. Neither makes a model call or admits a selection reader.

## Console HTTP surface

Everything lives under the token-scoped prefix `/<token>/`; unknown paths return 404. The console uses four routes:

| Route | Purpose |
| --- | --- |
| `GET <prefix>/api/console` | Bootstrap snapshot; the CSRF value is named `csrfToken` and kept in browser memory, never localStorage |
| `GET <prefix>/api/tasks` | Bounded history over the same named `task_list` operation; typed filters and an opaque `before` cursor, with `nextCursor` in the response |
| `GET <prefix>/api/tasks/<runId>` | Existing detailed task/result view for one run |
| `POST <prefix>/api/command` | `{operation, params}` dispatched through the same validated Python business operations the C-Two surface uses |

A command success is `{ok:true,result:...}`; a failure is `{ok:false,error:{code,message,details?}}` with a matching 4xx/5xx status and no traceback. Requests require the exact loopback `Host`, the exact console `Origin` when present, and `Sec-Fetch-Site` of `same-origin` or `none`; there is no wildcard CORS. Writes additionally require the per-session `X-Buddy-CSRF` header and the HttpOnly, `SameSite=Strict` session cookie scoped to the token prefix. The request body is bounded to 1 MiB. User text is rendered as text, and service credentials, provider keys and worker claim secrets are never returned.

The browser command allowlist is exactly these 22 operations: `evaluation_write_begin`, `evaluation_write_renew`, `evaluation_write_publish`, `evaluation_write_abort`, `evaluation_reader_begin`, `evaluation_reader_release`, `evaluation_evidence_record`, `evaluation_maintain`, `selection_request`, `selection_get`, `model_catalog_refresh`, `task_cancel`, `task_retry`, `task_acknowledge`, `workflow_submit`, `workflow_get`, `workflow_decide`, `workflow_continue`, `workflow_takeover`, `workflow_cancel`, `workflow_acknowledge` and `workflow_suggest`. Anything else is `METHOD_NOT_FOUND`. Browser JSON cannot supply authority fields such as `consoleAuthority`, `userOverride`, `consoleUser` or `adminOverride`; the server attaches the authenticated session itself, and a returned control capability is saved privately while only its `controlFile` path is exposed.

The bootstrap snapshot carries `csrfToken` (nonempty only on the HTTP surface), `tableRevision`, `gate`, `configuration`, `profiles`, `preferences`, `cards`, `evidence`, `decisions`, `pendingEvidence`, `tasks` and `capabilities`. Reads are bounded: the latest 200 evidence records, the latest 50 decision summaries and the latest 100 tasks with a total and `nextCursor`; the published profile/card/preference collections are bounded at publish time. The snapshot still includes infrastructure tasks. The separate history route accepts the [CLI list filters](cli.md#execution-observation); unknown or repeated query parameters are rejected. It shares the existing private-prefix, Host and Origin read checks; it does not add an operation to the write allowlist.

The UI renders Chinese with three top navigation tabs: `委派记录` (`#tasks`), `模型卡片` (`#models`) and `路由配置` (`#settings`). The desktop layout uses a compact header, a resizable list column and a detail pane with its own content tabs; narrow windows show the list or selected detail with a back button. List and detail content scroll within the available viewport. Main-tab switches preserve filters, selections, scroll positions and in-memory drafts. Snapshot polling every three seconds remains read-only: it neither invokes a model nor acquires a selection reader lease, and closing the page does not cancel a business task.

Delegation records default to main governed goals, grouped by recorded source project. An explicit switch includes helpers and internal execution records; helpers are also reachable through their owning goal. History is fetched in pages of 50, automatically near the scroll boundary after user scrolling or with `加载更早记录`. Filters are applied before the server page limit. A new-record notice lets the reader return to the newest page without inserting records into their current reading position. Project/Host dropdown choices come from loaded records; the text search queries the complete matching history. Every row identifies the recorded source Host and resolved execution configuration, with missing facts shown as unknown. The detail separates overview, assistance, artifacts/acceptance and execution records; pending Host decisions remain visible above the tabs. Publication and final acceptance controls stay outside the long content area.

Model cards group the list by `(adapter, provider, model)` and keep thinking efforts as variants inside the selected family. Filtering does not change profiles or routing constraints. Selecting an effort still selects its exact `profileId`; assessments, preferences, enablement and evidence are never merged across variants or harnesses. The detail tabs separate overview, assessment, preferences/enablement and evidence. Read-only assessments are text and lists; editing exposes form fields. Observation drafts are scoped to their profile, while task decision/continuation/review drafts are scoped to their run. An uncertain task mutation must be resolved before switching to another record so its idempotency identity is retained. Drafts are memory-only and do not survive a browser reload.

The `delegation` projection contains `kind`, `sourceHostId`, `currentHostId`, `parentRunId`, `rootRunId`, `project` (`id`, `path`, `label`) and nullable complete `configuration`. Relationships come from persisted workflow links; source projects use saved repository identity and original cwd, not managed execution worktrees. Standalone decision computations belong to `内部决策` with no source project path. Admission events preserve the original Host, and takeover events preserve the prior Host and generation; these facts allow later reads to distinguish origin from the current root controller. Older records without sufficient source evidence remain null. Host IDs are recorded attribution, not authenticated human identities or control capabilities, and resolved configurations do not claim a provider-observed served-model identity.

Profile names display their effort once, including when the discovered label already ends with the same ` · <effort>` suffix. The display word for `off` is `非思考`; other native efforts retain their names. Custom labels are preserved, and the stored profile identity, effort value and command payload are unchanged.

This boundary does not claim isolation from another process running as the same OS user. Do not publish or share the private URL.

## Fresh board and the initial decision profile

A fresh board starts with all 35 schema-8 tables, schema marker `8`, a fresh capability secret and exactly one `evaluation_state` row with `table_revision: 0`, `configuration_revision: 0`, `decision_profile_id: null` and `auto_maintain: false`. No profiles, preferences, cards, evidence or decisions exist yet, and the gate is open.

The user explicitly chooses the initial fixed decision profile and publishes it as `configuration.decisionProfileId`. The service never guesses one and never selects a selector recursively; until a valid profile is configured, routing and decision work settle as `needs-host` instead of inventing a fallback. Selecting a fixed enabled DSH profile first is the documented bootstrap step:

```sh
"$BUDDY" model-catalog-refresh '{"requestId":"first-discovery"}'
"$BUDDY" console '{"action":"open"}'
```

Discovery only proposes profiles; nothing is enabled or published automatically, and discovery never invents an ability review, an evaluation card, a sample count or a monetary budget. Those concepts do not exist in the current table.

## Discover and enable profiles

Use **Edit evaluation table → Discover models**, or `model-catalog-refresh` explicitly. Discovery inspects the installed harness through its declared catalog support, records its source, and returns proposed profiles with `enabled: false`. It does not call a paid model, change global harness settings or expose credentials. A failed discovery writes nothing and leaves the prior catalog intact.

A profile has an immutable execution identity (`adapter`, `provider`, `model`, `effort`). Publishing a different identity under the same `profileId` is a `CONFLICT`; publish a new profile ID instead. Availability must be backed by the recorded discovery: a publisher cannot claim an available route the installed harness catalog does not advertise, and an unavailable profile cannot be enabled. Provider credentials stay with the installed harness.

Preferences contain `profileId`, `mode` (`prefer`, `pin`, `exclude`) and a `reason`. User preferences take priority over model judgment: hard constraints and `pin`/`exclude` are applied in Python before any candidate reaches the selector, and a soft `prefer` only orders the remaining legal candidates inside the bounded model input. A pin must reference an available, enabled profile, because a preference never makes an unverified model or effort legal. Preferences express user intent and never rewrite observed outcomes.

## Edit and publish

An editor first acquires a durable writer intent against the expected table revision. Writer intent closes admission for new selection readers, then drains readers already admitted. One human or maintenance writer holds the active generation, lease and token; the default lease is 60 seconds for an active writer and 120 seconds for a waiting intent, and readers hold 300-second leases. A published revision releases the grant and promotes the oldest waiting writer in FIFO order. Business tasks already executing are not evaluation readers and keep their accepted configuration.

The browser renews its own lease while editing. Drafts stay separate from read refreshes. Publish validates the full requested collections and commits one new revision, its event and idempotency receipt atomically. Omitted collections remain unchanged; an empty array explicitly replaces a collection with nothing. Changing the payload under the same command ID is a conflict, including changing an omitted field to explicit null. A stale revision or writer generation cannot overwrite the current table. No SQLite transaction stays open while a user edits or a model runs, and lease expiry is never evidence that a process stopped; `TABLE_BUSY`, `WRITER_NOT_ACTIVE`, `REVISION_CONFLICT`, `STALE_GENERATION` and `UNAUTHORIZED` name the distinct failures.

Two tabs cannot both acquire the active writer. A waiting or expired grant is visible; an expired draft must reacquire authority and reconcile with the latest revision before saving. Abort releases only that writer's intent.

## Evidence and current cards

Evidence is append-only and attributed. Record a new correction instead of rewriting the original observation. `project` and `conditions` carry applicability; source URLs and actual run identities make a claim inspectable. Evidence can arrive during an exclusive edit and remains pending until incorporated into a published card. A preferences-only publication does not consume pending evidence. Automatic maintenance can compact current references while retaining the old card and its sources in immutable publication history; retired references are not requeued. Explicit human removal retains reconsider/requeue semantics.

Card authors provide `summary`, `strengths`, `limitations`, `risks` and `evidenceIds`. The service derives revision, timestamps and sample counts; callers cannot submit fabricated counters, and a supplied counter is rejected as an unknown field. Samples require the appropriate Host verdict for the exact attempt and a matching known request configuration; cancelled, unreviewed and recognized infrastructure outcomes do not count. One attempt counts once even if described by several observations. Requested, resolved and observed identity remain distinct: `observed` stays null when the provider did not report a served identity, and unknown is never filled in. Pending and sample counters are maintained transactionally; ordinary refreshes and publications do not recount the historical ledgers.

Evidence kinds are `task-success`, `task-failure`, `task-cancelled`, `observation`, `incident`, `correction` and `manual`; the three task kinds require the `runId` they are about. Duplicate reports dedupe by identity and recorded reports are never rewritten. The current publication is bounded: at most 200 profiles, 500 cards/preferences, 16 items per card list, and 64 evidence references per card. Summaries are at most 4,000 characters and individual points at most 500. Historical evidence and revisions remain outside the current card collection. See the [CLI reference](cli.md#evaluation-routing-and-console) for command fields and bounds, and [architecture](architecture.md) for persistence ownership.

## Decision and maintenance jobs

Selection uses the fixed decision profile configured above. The default decision execution timeout is 300 seconds, bounded 5–1,800 seconds, with a separate 10-second Worker shutdown margin. Decision states are `queued`, `running`, `completed`, `needs-host`, `failed`, `cancelled` and `stale`; a nullable `runId` means admission resolved without starting a model, for example a missing fixed profile or an empty legal candidate set. A valid no-op maintenance call completes with `publishedRevision: null` and `noOp: true`.

```sh
"$BUDDY" selection-request '{"requestId":"route-1","task":"Implement bounded React concurrency tests without changing the public API"}'
"$BUDDY" await '{"runId":"<returned runId>","waitSeconds":600}'
"$BUDDY" selection-get '{"decisionId":"<returned decisionId>"}'
"$BUDDY" evaluation-maintain '{"requestId":"maintenance-1"}'
```

These are durable jobs on the existing independent Worker, attempt, receipt and capacity mechanism. A queued selection takes no reader or execution slot while waiting behind a writer; it reads a complete current publication when claimed. Maintenance bypasses selector admission, uses the fixed profile and consumes the same finite Worker capacity. Cancellation, deadlines, restart fencing and lost-reply replay preserve ordinary process ownership. An internal decision job cannot be created with public `execution-submit`, retried with `execution-retry` or acknowledged as a business deliverable. An explicit new decision request gets a new request ID; an uncertain submission is recovered with the original ID.

`selection-get` returns a small summary by default. `selectedProfile` contains the frozen worker configuration to pass to a separately authorized business submission; `decisionModel` describes `requested`, `resolved` and `observed` for the model that computed the suggestion. `includeAudit:true` adds the exact persisted input, output, proposal and input hash. A recommendation is recorded history, not an execution permit. `needs-host`, an unavailable helper or an oversized input returns control to the Host; an already authorized fixed route or Host implementation can still satisfy the business goal. Because the served model identity is unknown, it is never invented.

Maintenance reads at most 64 pending records per call, includes their current cards and referenced sources, and reports the remainder. No candidate or referenced source is silently cut off to make input fit. Automatic adoption changes cards only, requires `configuration.autoMaintain: true`, the active writer generation and the expected revision, preserves existing risk/limitation strings exactly, and rejects fabricated references; profiles, preferences, configuration, authority and code-owned counters are never changed by a model. Evidence conditions stay in the source records and history. Maintenance consumes only cited pending records; evidence arriving during the call remains pending. With `autoMaintain: false`, the validated proposal is retained as `needs-host` for explicit Host inspection and nothing is published. `autoMaintain` is not a periodic scheduler and never triggers a model call from page refresh; no periodic maintenance process exists.

The native model call has a separate [helper contract](decision.md). It receives no coding tools, ordinary agent prompt or full historical scan. Its reported token usage is telemetry, not a billing estimate or monetary budget. Schema and protected-text checks do not prove semantic quality; calibration and actual Host-reviewed outcomes remain necessary.

## Frontend build

Source lives in `apps/console/` (React 19, Vite 8, TypeScript); a relative-base build produces `src/buddy/console_assets/`. These checked-in assets are included in plugin staging and the stable runtime. End users do not need npm. The frontend source and `apps/console/README.md` are available only in a full repository checkout and are excluded from the plugin. Contributors use a supported Node 24 LTS release, read that source README, and run `npm ci`, `npm run typecheck`, `npm test` and `npm run build` from `apps/console/`. Test the built console through a private source-backed Buddy state directory; Vite alone does not supply the authoritative API.
