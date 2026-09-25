# Runtime refinement: 0.9.0 candidate

## Status

In progress on 2026-09-25 from source 689e901. The daily installation remains contract 0.8.1/schema 10 while these isolated changes are being verified. Nothing in this record establishes installation of the candidate.

## Verified corrections

The ZCode limitation now distinguishes the app-server session/send rejection during an active prompt from the separate v4 gateway's deferred guide-input behavior. The observation-only contract is unchanged by that wording correction; the affected inquiry suite passed 18 tests.

Live observation exposed a second defect: the controller published activity only when its phase changed, so a long streaming phase left its public timestamp frozen even as native events continued. The real-controller fixture reproduced the failure before the repair. The controller now passes every observed event to the existing throttled, metadata-only sidecar; it does not fabricate activity from a timer. The updated inquiry suite passed 19 tests, including continued same-phase updates, unchanged counters, attempt binding and non-disclosure of native text. git diff --check passed.

The candidate integration verifier was also applied read-only to the three retained 0.7 FastDB artifacts previously rejected on the daily board. Runs 08de5e30-9ff2-447e-b786-6512ec4ccc95, 75ad4372-6777-48fa-9bdf-4e40054010b9 and 2bb91a9d-5d50-4d85-b543-2583fe308dfd all verified against immutable target commit 005e1c796cde90b35320edadb56b827d1476dd41 and original input 6254bd722b2c1a1fd9bdba256d4685fcb85a1bc4, with 7, 3 and 3 matching paths respectively and no unrecorded differences. All three lack changedEntries; the last two have identical final input/output commits, demonstrating why their earlier goal work must remain part of integration verification. The check queried current records with PRAGMA query_only and called the source verifier directly; it changed no daily row, acknowledgement or original receipt. This proves artifact integration at that target commit, not FastDB release readiness or the current contents of a moving target branch.

## Delegated work and integration

| Work | Buddy run | Requested configuration |
| --- | --- | --- |
| Native requestUserInput probe | f95fd9ae-06b8-4dfd-81f8-4fdb7f1514fe | zcode / zai-api / GLM-5.3 / max |
| Private hooks probe | 2d0b9207-ea59-4f1a-8674-ba1cf098ec02 | zcode / zai-api / GLM-5.3-Flash / max |
| Shared model concurrency backend | 2308c8d6-a5e0-4304-84ff-0cf419c37572 | routed to zcode / zai-api / GLM-5.3 / max |
| Shared model concurrency console | 0e076bb9-e3e9-48fd-b963-3a8e706522fb | routed to zcode / zai-api / GLM-5.3-Flash / max |

The experiments use explicit user-authorized configurations. The implementation jobs carry task-local soft preferences and preserve shared user preferences and annotations. Each goal uses an independent managed worktree and timeoutSeconds 0. Native probe subprocesses have their own bounded test limits. Artifact inspection, integrated tests, integration records, acknowledgement and checkout cleanup remain required before accepting these goals.

The P3, concurrency backend, concurrency console and current-documentation jobs have completed Host verification and acknowledgement; their managed checkouts were removed through workspace-cleanup-plan/apply. The retained integration records are int-abb96994-0970-410f-90db-abbfe0ec4b1b, int-97f559cc-f250-45b1-9908-1e91339e7cbf, int-0f5bb993-ec0a-458e-af49-0028e2a45cda and int-0e3bcd8f-4acc-466d-b2a5-900bf48d2c13 respectively. Immutable patches, manifests and fixed refs remain. P3 summaries and the successful native CLI stdout were copied into the integration checkout's ignored .dsh-skill-build/zcode-probes/p3/ directory before removal.

Host verification includes 19 model-concurrency tests, 27 dispatch/pool lifecycle tests, 8 user-policy tests, 11 store tests, 10 scheduling tests, 6 queue tests, 6 current-schema tests and 2 focused console HTTP tests. The Host found and reproduced a page-revision race and an invalid-number input that silently reverted 33 to its intermediate valid prefix 3. Both regressions passed after repair. Unknown model-family policy is refused while retained unavailable families remain editable; incidental model metadata on command/external work does not consume a model slot. The console suite passed 208 tests across 18 files; TypeScript and Vite build passed with Node 24.21.0. Built assets are index-Co7T6Xvf.js and index-CZU-CAXh.css. No Computer Use or browser-visual acceptance is claimed.

The document checker subsequently passed 47 Markdown files, 269 links, 56 JSON examples and 40 shell examples; skill validation and git diff --check passed. The current built JavaScript is index-BZWJG2NU.js, with index-CZU-CAXh.css.

P1 was independently checked with 12 passing probe tests and its retained same-turn native answer report, accepted under integration int-6d74c779-b2bc-40cc-b8ea-32a57f42e26f, then its managed checkout was removed. The report remains in .dsh-skill-build/zcode-probes/p1/.

The checkpoint inquiry implementation passed a real private native app-server probe (GLM-5.3-Flash/low, one admitted turn, verified checkpoint and answer tool calls, normal close); its report and journal are retained in .dsh-skill-build/zcode-probes/checkpoint/. The Host checked 119 ZCode tests before the last journal refinements and 16 board inquiry tests, including delivery-mode reporting and durable journal-unavailable refusal. Further review corrected late answers after withdrawal, bounded completed-call retention, strict journal identity, and cross-process journal publication. The fourth repair turn carried and completed an unsealed edit from a failed third turn; integration used the full Git delta from 0234f9 rather than blindly applying the last incremental patch.

The failed third repair attempt e4109121-a469-4780-9d38-129af3708d39 was a real provider failure: the private native record reports model_rate_limited, HTTP 429, provider code 1308, retryable false and a five-hour usage limit. timeoutSeconds was 0 and its immutable termination reason remains harness-error. The recovered fourth attempt reconstructed a new native session and completed. The runtime now exposes only bounded structured native failure attribution; it never reads the private native SQLite database to diagnose an active run. Host validation additionally rejects URLs, prose and recognizable credential prefixes in identifier fields.

The first integrated full check ran 885 Python tests in 1084.316 seconds with one failure: a stopped old supervisor still holding its lifetime lock retained stop.request during the new daemon's startup, later leaving local-6 unfilled. A deterministic regression reproduced this genuine startup race. The fix withdraws the predecessor stop at explicit startup while preserving lock-based single ownership; the pool can refill the slot after the old owner exits. Six worker-pool tests, the formerly failing real restart test and 27 dispatch/lifecycle tests passed after correction. Final whole-package verification is being rerun after all source changes.
