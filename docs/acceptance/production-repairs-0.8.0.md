# Production workflow repairs — 0.8.0

This is the source acceptance record for [ADR-010](../decisions/010-production-workflow-repair-plan.md). Combined verification is in progress. It is not an installed-runtime acceptance: the shared daily service remains contract 0.7.0/schema 9, and activating the 0.8 candidate still requires the user's notice.

## Ownership and delivery

The integration checkout is `socu/buddy-core`. The original dirty `socu/python-blackboard` checkout and the separate CodeBuddy branch were not integration targets. Bounded implementation used independently allocated Buddy worktrees; Host reviewed immutable output commits and made the cross-module corrections before recording acceptance. An original failed scope receipt remains failed even when the Host later adopts its inspected output.

The daily board's capacity was changed, at the user's request, to six business attempts plus one reserved decision attempt. The switch preserved worker-owned children and their original attempt identities. A later `health` read confirmed `business.limit=6`, `decision.limit=1`, all seven managed supervisors present and clean database integrity. Source defaults remain two plus one. This deployment setting must be retained when the candidate is eventually installed.

## Verified source boundaries

| Area | Evidence so far |
| --- | --- |
| User policy and model assessments | An ordinary Host cannot claim or borrow a human grant. User annotations, preferences, enablement and configuration use authenticated field patches; maintenance publishes only assessment cards. The six user-policy tests passed. |
| Catalog and retained history | Ordered complete/unknown observations preserve user intent, returning identities and cross-project evidence; retired profiles are paged and searched before pagination. The five catalog-observation tests passed, including literal wildcard search. A complete empty Codex catalog is a valid observation, tested without a model turn. |
| Routing | Task-local preferences do not publish global settings. Selection input preserves the assessment's recorded origin. Focused routing/decision checks and the actual Worker selection-input test passed. |
| Recovery and activity | The same live Worker explicitly reconciles its original attempt after restart; another instance or a lost child handle cannot infer ownership from a PID. Durable completion is replayed. Activity is bound, bounded and monotone; a progress message cannot clear uncertainty. Real private-daemon restart and activity/recovery tests passed. |
| Scope recovery | Eighteen real-Git scope tests passed after Host-resolution delivery was added. Only a scope-seal failure with an intact native outcome can produce a resolved delivery; the original failed attempt and receipt remain unchanged. |
| Integration and cleanup | Nine initial lifecycle tests and four public CLI/HTTP/C-Two tests passed. Multi-turn physical allocation and root-authorized helper lifecycle corrections are undergoing final verification. |
| Console | The current frontend passed 190 tests in 17 files. They cover publication conflicts, stale history pages, unavailable configurations, native-session evidence, execution capabilities and final-artifact integration prerequisites. No Computer Use or browser automation was used. |
| Inquiry privacy | Eighteen DSH bridge tests and a real service/Unix-socket observation regression passed after removing tool argument previews. Progress observations retain tool names and timestamps, not raw arguments. |
| RPC and distribution | Ten focused RPC checks and nine first-install checks passed. Tests exercised an 8 MiB message, concurrent calls, chunk fallback and control operations during long waits. The repository supplies its own local/Git marketplace; no public registry registration is claimed. |

## Native Codex execution

A real private-board task used the installed Codex App Server with the native account-plan login, model `gpt-6-luna` and effort `low`. The task changed one file to the exact bytes `native-codex-passed\n`; Host verified the committed blob, integrated its immutable output into the fixture target, recorded integration, accepted the artifact and removed only its managed checkout. The output patch and fixed commit remained readable, and all private supervisors stopped afterward.

The first live probe exposed a genuine completion-interface mismatch: the common context still requested a DSH finish tool, and the native output schema allowed a completed result with a non-null request. The common hint now names the harness-provided completion interface, while Codex uses a root object containing a nested discriminated outcome. A completed result requires `request:null`. Fixture validation and the second real probe passed. No failed first probe was rewritten as successful.

| Binding | Verified identity |
| --- | --- |
| Private state root | `/var/folders/8s/_8wkkrz159qft4v82tbn7dzc0000gn/T/buddy-codex-acceptance-b4n071g9` |
| Goal | `c7ecf54f-83e3-4385-9e84-2e9d9a6d77d1` |
| Native thread | `01a0d532-0fc1-7e63-ab2e-ce1effa673d0` |
| Final artifact | `63099e72-65d0-4994-9f9e-f92762a2a4b9` |
| Output commit | `218ab543f8b7de94175393a4cb98ac48b0e36dc2` |
| Integration record | `int-ba2458f0-8f02-4e3e-bd75-3f52e5288e79` |

The adapter does not claim a correlated inquiry bridge or verified tool-free decision execution. Native App indexing visibility is unknown; the result records the native session identity and storage ownership without inventing an App-open link. The served remote model remains unobserved, distinct from the requested and native-readback configuration.

## Preserved-data rehearsal

The offline copy script reads the daily schema-9 database through SQLite's read-only mode and backs it up before transforming a separate copy. Verification preserved row fingerprints for all 36 pre-existing tables and every old column except the schema marker, populated 31 scope-version records from each root/helper run's hash-verified retained manifest and passed integrity/foreign-key checks. Synthetic helper and old-card cases also passed. Old cards retain their text with `origin:unattributed`; no author or shutdown proof is fabricated.

The rehearsal archive is `/private/tmp/buddy-schema10-daily-validation-20260925-b/board-schema9.sqlite3`; the independently checked candidate is its sibling `board-schema10.sqlite3`. There were four active tasks at snapshot time, so the report explicitly marked the copy ineligible for activation. It is inspection evidence only. Installation needs a fresh idle-state check, fresh complete backup and a new copy checked against the final schema.

## Remaining acceptance

The final combined source suite, packaged cold start, rebuilt HTTP assets and the real DSH → ZCode → DSH assistance/inquiry/restart/integration/cleanup exercise are still pending. The ZCode inquiry first artifact must not be treated as accepted: Host identified a repeated-question identity loss, a missing activity-helper binding and an unsafe native guide-at-settlement race and requested corrections. The source and documentation will be reconciled with the verified final behavior before a candidate is handed off.
