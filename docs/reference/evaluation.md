# Shared profiles, settings and local console

The Python blackboard owns published profiles, family preferences, per-effort overrides, model notes, model facts and routing records. The table and configuration revisions remain authoritative publication boundaries. Host evaluation maintenance, cards, their evidence ledger and samples have been removed; the Router maintenance plane is not implemented by this change.

## Open the console

```sh
"$BUDDY" console '{"action":"open"}'
```

`console` accepts `open` (default), `status` and `close`, and serves the built React/Vite bundle over private loopback HTTP. Pass `browser:false` to obtain the link without launching a browser. The [console entry contract](console.md) owns tickets, sessions, authority and identity-fenced close; closing the console never stops a micro task.

`console-snapshot '{}'` reads authoritative data without a browser session. Console status reports the public instance ID, running flag and asset metadata, without an entry URL or credential. Neither read calls a model or admits a selection reader.

## Console HTTP surface

Authenticated APIs use the public prefix `/console/<consoleId>/<sessionId>/`; the path identifies a session and does not authorize access. Unknown paths return 404.

| Route | Purpose |
| --- | --- |
| `GET <prefix>/api/console` | Bootstrap snapshot with the in-memory CSRF value `csrfToken` |
| `GET <prefix>/api/tasks` | Bounded execution history with typed filters, opaque `before` cursor and `nextCursor` |
| `GET <prefix>/api/tasks/<runId>` | Detailed micro task/result view |
| `POST <prefix>/api/command` | `{operation, params}` dispatched through validated Python operations |

Command success is `{ok:true,result:...}`; failure is `{ok:false,error:{code,message,details?}}` with a matching HTTP status and no traceback. Reads require the session cookie, exact loopback Host, exact console Origin when present, and `Sec-Fetch-Site` of `same-origin` or `none`. Commands also require the per-session `X-Buddy-CSRF` value and exact Origin. The request body is bounded to 1 MiB. User text is rendered as text, and service credentials, provider keys and Worker secrets are never returned. HTTP snapshots carry `consoleSession: {id, canWrite, reason}`; this descriptor never grants C-Two readers authority.

`console.CONSOLE_OPERATIONS` allows authenticated settings publication, bounded profile reads, catalog refresh, fenced harness path changes and re-checks, and micro task controls. Console authority comes from the authenticated server session. Ordinary service-token Hosts and Workers cannot publish user settings or replay console publication receipts.

The bootstrap includes `tableRevision`, `configurationError`, `configuration` (`revision`, `routerProfileIds`, `routerRetryIntervalSeconds`, `defaultRoutingMode`, `routingBudget`, `routingBudgetLimits`), `profiles`, `modelConcurrency`, `unavailableProfileCount`, `familyPreferences`, `preferenceOverrides`, effective `preferences`, `familyAnnotations`, selection `decisions`, `tasks`, `capabilities` and recorded harness health. The browser snapshot carries only the pending micro task count; paginated history supplies execution records. The CLI snapshot carries the latest 100 executions, a total and `nextCursor`. Selection summaries are bounded to 50 records. Cards, evidence, samples, pending evidence and maintenance state are absent.

## Profiles and user policy

The Buddy page groups models by harness and family. Each effort retains its own exact `profileId`, availability, enablement and effective preference. Family notes and concurrency limits are shared by `(adapter, provider, model)`. Search, enabled-only filtering and paginated unavailable profiles do not change routing constraints. `model-profiles` accepts `includeUnavailable`, `limit`, `after`, `query` and `adapter`; it returns profiles, their user policy, capacity, `tableRevision` and `nextCursor`.

Family preferences use `{adapter, provider, model, mode, reason}`, with `mode` `prefer`, `pin` or `exclude`; per-effort overrides use `{profileId, mode, reason}` and additionally allow `none`. The `effective_preferences` view resolves overrides first, uses `none` to clear the family default, and otherwise applies the family default. Candidate filtering, Router input and the console read this same view. Fixed and excluded preferences bound candidates; preferred entries affect their ordering. A new pin requires an available, enabled profile. Existing unavailable pins do not prevent unrelated edits. Model notes are user-owned family text.

The Router list is ordered and has one common mode, budget and retry interval. The first available eligible buddy serves as Router. Mode eligibility, recorded health and enablement remain separate requirements. Routing details retain the facts frozen onto the request and dispatch, Router trials and Host boundary commands.

## Edit and publish

Controls start an in-memory draft. Drafts survive tab changes and keep their original table revision. Saving sends one `user_policy_publish` with `commandId`, `expectedRevision` and at least one patch field. The server checks authenticated console authority, the request identity and the current revision, then records the settings, revision, event and receipt in one transaction. A duplicate command with the same input replays its result; changed input under the same command ID conflicts. An unknown reply retains the command identity for retry. A newer table revision leaves the draft available for explicit reload or rebase.

The patch fields are `profileSettings: [{profileId,enabled}]`, `preferenceChanges: [{profileId,mode,reason}]`, `familyPreferenceChanges: [{adapter,provider,model,mode,reason}]`, `familyAnnotationChanges: [{adapter,provider,model,text}]`, `configuration: {routerProfileIds,routerRetryIntervalSeconds,defaultRoutingMode,routingBudget}` and `modelConcurrency: [{adapter,provider,model,limit}]`. Omitted fields remain unchanged; each list is bounded to 200 changes, duplicate identities and program-owned fields are rejected. A null preference removes that stored preference; an empty note clears it, and note text is bounded to 4,000 characters. Publication returns `published`, `revision`, `tableRevision`, `configurationRevision`, `counts` and `duplicate`.

A model-family concurrency limit is an integer from 1 to 32, default 2, shared across efforts and Router attempts using that model. A raise affects subsequent claims; a decrease holds new claims without terminating running attempts. The setting survives temporary unavailability. `active` is a read-only observation and cannot be published.

## Availability and routing

[harnesses.md](harnesses.md) owns discovery, health, quota and native usage observations. Recorded availability does not change user enablement, preferences or notes. Profiles newly found in the model catalog start disabled. Public model facts are program-owned dated snapshots; model notes never replace them.

With multiple candidates, routing freezes the task, legal candidates, program facts, user preferences, model notes, Router list, mode, budget and revisions. A selection has a bounded internal reader lease. Changed table or configuration revisions still stop publication with `stale`; already-started business executions retain their accepted configurations. The Router's structured answer can cite surviving `preference`, `annotation` (model-note) and review-only `file` references, but cannot cite cards or evaluation evidence. Native tool and stop evidence remains part of the runtime's publication checks.

```sh
"$BUDDY" selection-request '{"requestId":"route-1","task":"Implement bounded React concurrency checks without changing the public API"}'
"$BUDDY" await '{"runId":"<returned runId>","waitSeconds":600}'
"$BUDDY" selection-get '{"decisionId":"<returned decisionId>"}'
```

See [architecture](architecture.md) for persistence ownership and [console](console.md) for the local console boundary. Source verification for this deletion is recorded in [the deletion notes](../design/adr021-l14-delete-maintenance-notes.md); it does not establish installation or Host acceptance.
