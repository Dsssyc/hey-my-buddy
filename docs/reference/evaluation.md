# Shared buddies, settings and local console

The Python blackboard owns published buddies, each buddy’s preference, model service notes, model facts and routing records. Settings patches publish atomically in commit order and retain command receipts. Native token usage and provider cooling remain separate from user policy; installation facts live in the [acceptance records](../README.md#history-and-evidence).

## Open the console

```sh
~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy console '{"action":"open"}'
```

`console` accepts `open` (default), `status` and `close`, and serves the built React/Vite bundle over private loopback HTTP. The CLI opens the browser; pass `browser:false` to obtain the link without launching it. Open returns the fixed root URL without login by default. With “需要登录” enabled in Settings it returns a 10-minute single-use launch URL; redemption establishes an HttpOnly session cookie and redirects to the root. Sessions have no 30-day expiry and can be logged out or revoked. Settings writes apply in commit order in either access mode. The [console entry contract](console.md) owns tickets, session lifetime, authority, client-side wait and identity-fenced close; closing the console never stops a task.

`console-snapshot '{}'` reads the same authoritative data without a browser session. Console status reports the public instance ID, running flag and asset metadata, without an entry URL or credential. Neither makes a model call.

## Console HTTP surface

After the one-use launch route, pages and APIs live at the console root `/`; the session is authenticated by cookie when login is enabled. Unknown API paths return 404. The data routes are:

| Route | Purpose |
| --- | --- |
| `GET /api/console` | Bootstrap snapshot; the CSRF value is named `csrfToken` and kept in browser memory, never localStorage |
| `GET /api/macro-tasks` | Bounded macro task list with filters, totals and a keyset `nextCursor` |
| `GET /api/macro-tasks/<macroTaskId>/timeline` | The selected macro task's rows, spans, events and explicit scope limitations |
| `GET /api/tasks/<runId>` | Existing detailed task/result view for one run |
| `GET /api/runtime-version` | Recorded runtime and installation facts, read when the version section is opened |
| `POST /api/command` | `{operation, params}` dispatched through the same validated Python business operations the C-Two surface uses |

A command success is `{ok:true,result:...}`; a failure is `{ok:false,error:{code,message,details?}}` with a matching 4xx/5xx status and no traceback. Reads and commands require the session cookie when login is enabled, the exact loopback `Host`, the exact console `Origin` when present, and `Sec-Fetch-Site` of `same-origin` or `none`; there is no wildcard CORS. POST commands additionally require the per-session `X-Hey-My-Buddy-CSRF` header and exact Origin. Any admitted local client or authenticated session may mutate settings. Cookies are HttpOnly and SameSite=Strict, with `Path=/`; the launch URL contains a one-use ticket and redirects to `/`. The request body is bounded to 1 MiB. User text is rendered as text, and service credentials, provider keys and attempt nonces are never returned.

HTTP snapshots additionally carry `consoleSession: {id}`; the descriptor never grants authority to a C-Two snapshot reader. Settings drafts remain per browser window, and read-only browsing and theme controls stay available.

The browser command allowlist is defined in `CONSOLE_OPERATIONS` in `src/hey_my_buddy/console/server.py`. It permits authenticated `user_policy_publish`, bounded buddy and selection-history reads, catalog refresh, one manual harness path, bounded harness re-check, clearing a provider's cooldown, macro task stop, and console-only macro task deletion preview and deletion. Session authority is attached by the console server, not trusted from browser JSON: a caller-supplied `consoleAuthority`, `userOverride`, `consoleUser` or `adminOverride` is refused, and a fabricated console authority over C-Two is `UNAUTHORIZED`. Ordinary service-token Hosts and Workers cannot publish user settings or replay console publication receipts.

The bootstrap snapshot carries `csrfToken` (nonempty only on the HTTP surface), `eventCursor`, `configuration` (`{routerBuddyIds,routerRetryIntervalSeconds,defaultRoutingMode,routingBudget,routingBudgetLimits}`), `buddies`, `modelServiceConcurrency` entries `{harness,provider,model,limit,active}` with read-only `active`, `preferences` (`{buddyId,mode,note}`), `modelServiceNotes` (`{harness,provider,model,text,updatedAt}`), `decisions`, `tasks`, `capabilities` and `harnesses`. Each preference keeps its own nullable authored `note`; model service notes are stored once per model service. Reads are bounded: the latest 50 decision summaries and at most 202 buddies in the default snapshot. On the browser route `tasks` is only `{pendingCount}`; the task page reads macro task lists and timelines separately, and details use the fixed run route. There is no `GET /api/tasks` history route. The `console-snapshot` CLI read carries the latest 100 tasks, infrastructure tasks included, with a total and `nextCursor`; the [CLI list operation](cli.md#execution-observation) supplies bounded execution history.

The UI has three top navigation tabs: `任务` (`#tasks`), `Buddy` (`#buddies`) and `设置` (`#settings`); [console](console.md) owns their description. The desktop task page has a folding macro task list and a shared timeline scroll area; opening a micro task replaces the lanes with its detail and a micro task list. Its own timeline strip stays above independently scrolling step content. Narrow windows use layered navigation. Main-tab switches preserve filters, selections, scroll positions and in-memory drafts. Snapshot polling every three seconds remains read-only: it invokes no model, and closing the page does not cancel a business task.

The Buddy roles section reads the ordered Router list, common mode and budget, retry interval and per-Router health. The list supports moving and removing buddies, and a null current Router stays unavailable. Routing details read frozen facts from the actual request and dispatch, including Router trials and Host boundary commands. Historical route and turn bindings remain record-owned. Fast sends the task packet to the selected Router provider; review also reads the frozen repository copy.

The task page groups macro tasks by recorded source project, with standalone micro tasks in an initially folded group. It offers text search and all, running, waiting-for-Host and waiting-for-acceptance filters; filtering precedes the server page limit. Timeline rows include helpers and Lead ownership, while Router executions appear on the micro task they routed. New activity reorders the list only when the reader is at its top and the pointer is outside; otherwise the heading offers to apply the order. The detail follows recorded steps: submission, routing, turns and inquiries, Host decisions and continuations, acceptance or conclusion, and stops or changes of buddy or Host. Missing facts remain unknown, and final acceptance remains a Host action.

The Buddy catalogue groups models beneath harness sections; selecting a model expands its detail below the row. Each reasoning effort is a distinct buddy, ordered by its `effortRank` from that model's current native effort list, with its own enablement and preference (`不设`, `优先`, `固定`, `排除`). The model service shares one concurrent-attempt limit and one user note. A newly discovered model starts disabled and shows `新`; a model missing from a confirmed catalogue reading shows `待确认` ([ADR-027](../decisions/027-model-catalog-trust.md)). Search, `只看已启用` and `显示不可用` filter the catalogue without changing policy. The Router list and its `添加` control are in the roles section. Drafts are memory-only and do not survive a browser reload.

The `lineage` projection contains `kind`, `sourceHostId`, `currentHostId`, `parentRunId`, `rootRunId`, `project` (`id`, `path`, `label`) and nullable selected `buddy`. Relationships come from persisted workflow links; source projects use saved repository identity and original cwd, not managed execution worktrees. Standalone routing computations belong to `内部决策` with no source project path. Admission events preserve the original Host, and takeover events preserve the prior Host and generation. Older records without sufficient source evidence remain null. Host IDs are recorded attribution, not authenticated human identities or control capabilities, and selected buddies do not claim a provider-observed served-model identity.

Buddy names display effort once; every native effort keeps its recorded name, `off` included. An enabled effort and the currently inspected effort have separate marks, and draft enablement changes stay marked until saved. Selection and enablement are separate. The `设置` page is a column of aligned sections for theme, language, reminders, access, version and storage. Its theme choice is 浅色 / 深色 / 跟随系统; the browser remembers theme, language and display habits, while credentials and drafts remain in memory.

This boundary does not claim isolation from another process running as the same OS user. Do not publish or share the private URL.

## Fresh board and the initial Router buddy

A fresh board starts with a fresh capability secret, an empty ordered Router list and the default routing mode `fast`. Buddies, preferences, model service notes and harness health start empty. The running installed version is a separate fact; inspect `health`, `runtime` and `harnesses` before using a new-contract client.

The authenticated user publishes ordered `routerBuddyIds`, `routerRetryIntervalSeconds`, common default mode and review budget. List publication validates complete catalog identities; admission checks current health, cooling and mode eligibility. Fresh boards start with `[]`/600/fast/standard.

```sh
~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy model-catalog-refresh '{"requestId":"first-discovery"}'
~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy console '{"action":"open"}'
```

Discovery publishes program-owned native facts and creates new buddies disabled. It does not create user preferences, call a paid model or modify harness settings.

## Discover and enable buddies

Use the **发现模型** button in the Buddy catalogue, or `model-catalog-refresh` explicitly. Discovery records native efforts as proposed disabled buddies without a paid model call, credential disclosure or global harness changes. A failure, or a reading taken while the harness could not confirm its account, leaves both published data and prior catalog intact; a model that a trusted reading no longer lists is kept as pending and retired only after a later trusted reading, at least one hour on, still lacks it.

A buddy has a stable `(harness, provider, model, effort)` identity. Native discovery owns availability, capabilities and model metadata; user patches cannot replace these facts. Returning buddies recover their existing user settings. Enabling an unavailable buddy or adding a pin to one is refused with `BUDDY_UNAVAILABLE`; a new pin requires that buddy to be available and enabled. The Router list may retain or add a published buddy with a complete identity even while it cannot currently serve; the resolver reports that condition, so unrelated settings remain publishable. A native directory identity collision under an existing `buddyId` is `CATALOG_INVALID`. Catalog declaration is not proof of remote quota or served-model identity, and provider credentials stay with the harness.

A preference belongs to one buddy: a `preferences` entry is `{buddyId,mode,note}` with `mode` `prefer`, `pin` or `exclude`, and a buddy without a preference has no entry. Its explanation is the user's `备注` (`note`), not a program reason: it is nullable, an empty string is stored as null, and its limit is the shared `MAX_NOTE_BYTES` of 10,000 UTF-8 bytes. Publication, `evaluation_preferences.note`, the console, frozen Router input and frozen `routingBasis.excludedBuddies` use this name. Hard constraints and `pin`/`exclude` are applied in Python before any candidate reaches the Router; a soft `prefer` enters the bounded Router input and `policyFacts` without changing the candidate set or its catalogue order. A new pin requires an available, enabled buddy; existing intent remains stored when health changes. Preferences never rewrite observed outcomes. The separate model service note is shared by that model's buddies and retains its `text` field and 4,000-character limit.

## Availability and harness health

The current source stores one service-owned health row per harness (DSH, ZCode, Codex and Claude Code); [harnesses.md](harnesses.md) owns the discovery, environment and cache contract. Only `ready` counts as available: it means a bounded public version probe or native initialize handshake (and, where supported, the native login-status read) passed without any model request. `missing`, `login-required`, `unhealthy` and `unknown` are unavailable: they immediately exclude that harness's buddies from effective availability, enabling and routing, while the user's enablement, preferences and model service notes stay intact. A healthy change refreshes that harness's model metadata automatically, and a newly discovered buddy starts disabled with the console's `新` marker. Submissions read the recorded cache rather than running a slow probe; Host buddy queries, submissions and console reads may each borrow one rate-limited rescan, and an explicitly named unavailable harness is rescanned before it is refused. Discovery diagnostics store no environment values, login output or provider credentials. Quota observations may carry an explicitly reported bounded native account/limit identifier; credentials remain with the harness.

Availability alone never makes a Router eligible: health, enablement and the capability of its mode must all hold. The [routing-mode acceptance](../acceptance/adr018/routing-modes-0.20.0.md) and [harness discovery record](../acceptance/local-harness-discovery-0.19.0.md) own prior native verification; this Host-workflow candidate performs no new paid native probe. Native usage records declare their coverage (for example the root session/thread); separately owned native subagent sessions and billing totals are not inferred.

The execution details display native `tokenUsage` per attempt: input tokens (including cached input), cached input and output tokens. Missing counters stay unknown; native cumulative session records are not charged to every continuation. These are native observations, not billing estimates or served-model attestations.

A quota refusal leaves provider cooling, not a reading ([harnesses](harnesses.md#quota-refusals-and-cooling)): the cooling provider's buddies drop out of automatic candidates until the cooldown ends or the user clears it, and user enablement and preferences never change. A buddy the Host names explicitly stays as chosen and shows `providerCooling`.

## Edit and publish

Buddy settings edit directly: a control change starts an in-memory draft, and drafts survive tab changes. A bottom save bar appears while the page holds a draft and offers `放弃` and `保存`. Later saves of a field take effect; a running Router call keeps its frozen input, and its selected buddy is checked for current legality before publication. Running business micro tasks retain their selected buddies.

Save publishes one event and receipt atomically. User publication is a field patch through `user_policy_publish`: `buddySettings:[{buddyId,enabled}]`, `preferenceChanges:[{buddyId,mode,note}]` (`mode` is `prefer`, `pin` or `exclude`, or `null` to delete it; nullable `note` allows at most 10,000 UTF-8 bytes), `modelServiceNoteChanges:[{harness,provider,model,text}]` (empty text clears; at most 4,000 characters), optional `configuration:{routerBuddyIds,routerRetryIntervalSeconds,defaultRoutingMode,routingBudget}`, and optional `modelServiceConcurrency:[{harness,provider,model,limit}]`. The retired preference key `reason` is rejected. At least one patch field must be present, each list is bounded to 200 changes, and duplicate identities, duplicate model services and program-owned fields are rejected. Omitted patch fields stay unchanged. Publication requires `commandId`, and only an admitted console session can publish or replay its receipt. An unknown reply retains the same command identity for recovery; changed input under that ID conflicts. No database transaction spans human typing or model synthesis.

Every model service keeps one user-owned concurrent-attempt limit (integer 1–32, default 2) shared by all its effort variants and by routing decisions using that model. A raise affects subsequent admissions immediately; a decrease never terminates running attempts and only holds new admissions until occupancy is below the limit, and the setting survives discovery marking the model unavailable. `active` occupancy is derived, read-only observation: a patch carrying it is rejected, a draft never publishes occupancy, and only the authenticated console can write capacity policy — Hosts and Workers cannot.

Multiple tabs can hold local drafts. Settings patches commit in order; an older tab can save its own changed field without a table-version conflict. An identical `commandId` replays its original receipt without overwriting later work. Unavailable actions retain an accessible reason; micro task controls remain independent of the settings draft.

The active native set is limited to 200 buddies. The default snapshot includes available buddies and assigned Router buddies, with their preferences and model service notes. Unavailable buddies remain stored and are read through a separately paginated `buddy-list` query with `includeUnavailable`, `limit`, `after`, `query` and `harness`; it returns associated user policy, capacity and a `nextCursor`. Their presence cannot enlarge a routing packet. Model service notes are bounded to 4,000 characters. Buddy `unavailableReason` and `catalogReason`, harness/account `reason` and catalog reading `reason` are nullable `{code, params}` reason objects; native `unavailableContent` and discovery `content` remain content. The code table is `src/hey_my_buddy/protocol/reason-codes.json`. `effortRank` is a zero-based native position or null when unknown, and does not compare harnesses. See the [CLI reference](cli.md#evaluation-routing-and-console) for command fields and bounds, and [architecture](architecture.md) for persistence ownership.

## Router attempts

With multiple legal candidates, routing freezes the Router list and identities, mode, retry interval, budget and candidate set for each dispatch. Each reached Router has an immutable internal task and attempt. A no-answer advances within the same request only after confirmed stop; an adopted answer or abstention, cancellation and unknown stop do not advance. When a chosen buddy becomes illegal before publication, the service may reroute once with current candidates and settings, provided the original execution is stopped and ownership remains valid. Capacity constraints queue. Fast requires complete zero-tool evidence and a 60-second deadline; review uses the frozen copy and common brief (60 s/8 calls), standard (300 s/24 calls) or deep (600 s/64 calls) limits. Unknown read bytes stay null. The Router has no workflow turn, credential, completion tool or artifact authority.

```sh
~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy selection-request '{"requestId":"route-1","task":"Implement bounded React concurrency tests without changing the public API"}'
~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy await '{"runId":"<returned runId>","waitSeconds":600}'
~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy selection-get '{"decisionId":"<returned decisionId>"}'
```

These are durable records on the existing Worker, attempt, receipt and capacity mechanism. A queued routing attempt takes no execution slot before admission; a review attempt materializes a private read-only copy of the frozen `manifest.inputTree`, while a fast attempt reads no repository content. Cancellation, deadlines, restart fencing and lost-reply replay preserve process ownership. An internal routing attempt cannot be retried with `execution-retry` or acknowledged as a business deliverable. An explicit new request gets a new request ID; an uncertain submission is recovered with the original ID.

`selection-get` returns a summary with nullable reason object `reason`, the Router's own nullable `note`, `selectedBuddy`, `decisionModel`, `evidence`, `usage`, `budget`, `nativeIdentity`, `stopEvidence`, `inputVerification` and `routingMode`; `includeAudit:true` adds persisted input/output and hash. Publication requires matching attempt and generation, selected-buddy legality at adoption, confirmed native and controller stop, valid cumulative time and tool counts and, for review, the unchanged frozen manifest and mirror. A recommendation is recorded history, not an execution permit. Failure stops at the Host boundary on the same micro task.

`selection-list` is a read-only history operation. It accepts `limit` (1–100, default 20) and positive integer `before` cursor (at most 2^63−1). It returns `decisions`, `nextCursor` and the complete matching `total`. Filtering precedes the page limit; ordering follows the descending durable request sequence, so newer inserts do not shift an older page. Summaries exclude the saved input/output, which remain available by fixed ID through `selection-get`. This read does not invoke the decision adapter or write state.

`routingHealth` in `health` and `console-snapshot` reports per-Router availability, skip/retry facts, answer/no-answer counts and timeout failures through the shared current-Router entry; it also retains the total settled-request window and separate budget, bounds and input-change diagnostics. Its top-level `reason` is a nullable `{code,params}` reason object, rendered in the console's current language; it is separate from user preference and Router notes. Timeouts/budget exhaustion count as failures. Valid adopted abstention counts as an answer for that Router; cancelled/changed outcomes do not become no-answer facts. Reads have no state side effects.

Host evaluation maintenance, cards, their evidence ledger and samples have been removed. The Router maintenance plane remains future work. Source verification is recorded in [the deletion notes](../design/adr021/l14-delete-maintenance-notes.md); it does not establish installation or Host acceptance.

The native routing call has a separate [Router contract](decision.md). In fast mode it receives no tools, no repository content and no historical scan; in review mode it receives a frozen read-only mirror and no coding tools or full historical scan. Its reported usage is telemetry, not a billing estimate or monetary budget. Schema and protected-text checks do not prove semantic quality; calibration and actual Host-reviewed outcomes remain necessary.

## Frontend build

Source lives in `apps/console/` (React 19, Vite 8, TypeScript); a relative-base build produces `src/hey_my_buddy/console/assets/`. These checked-in assets are included in the shared skill package and stable runtime. End users do not need npm. The frontend source and `apps/console/README.md` are available only in a full repository checkout and are excluded from the shared skill package. Contributors use a supported Node 24 LTS release, read that source README, and run `npm ci`, `npm run typecheck`, `npm test` and `npm run build` from `apps/console/`. Test the built console through a private, source-backed state directory; Vite alone does not supply the authoritative API.

## Native eligibility facts (0.23.0)

Buddy reads include bounded native `billing` and `providerCooling`; neither is user-editable. Billing never changes enablement, preference or routing eligibility. Cooling temporarily filters candidates without changing user policy. Review capability is projected from the local eligibility check without certificates. See [harnesses](harnesses.md) for the separate native verification boundary.

## Public model facts snapshots (ADR-021 L8)

Until 0.49.0 a refresh stored a dated snapshot of each newly enabled model service's public facts from models.dev (`model_facts`). Nothing read the snapshots, so the schema pruning of 2026-10-20 removed the table and the refresh; enabling a model makes no network request. The routing plane brings price facts back when it reads them ([ADR-021](../decisions/021-router-buddy-planes-and-routing-evidence.md) L8).
