# Governed productivity workflow — 0.5.0

Historical acceptance record for the named release. Its setup and upgrade steps are not current instructions; use [current operations](../reference/operations.md). Implementation links lead to the current successor paths; use Git history for the exact historical source.

This record extends the [console and decision-job acceptance](console-decisions-2026-09-22.md) with Host-directed repository work, assistance, continuation, workspace ownership and fixed-artifact acceptance. Checks ran on 2026-09-22 in an isolated implementation checkout. The original development checkout and its uncommitted design documents were preserved. Production installation is a separate operation from this private-board acceptance.

## Implementation and independent checks

DSH supplied the workflow backend in commits `5a82048` and `0537aa4`, through durable run `faf36797-c935-4ff6-b749-f505b2e38687`. The Host implemented the native turn adapter and React controls, integrated workspace preparation, reviewed the actual code and corrected issues exposed by independent checks. The DSH delivery alone was not accepted as the finished feature. Corrections included selected-attempt artifact binding, preparation fencing, checkout reuse, nested request queues, descendant cancellation and aggregate shutdown, as well as compact recovery commands and UI state.

The integrated `uv run --project deepseek-delegate --frozen python -m buddy.checks` exited 0 with **380 Python tests and 213 Node tests**. Frontend verification passed **20 Vitest tests**, TypeScript checking and the Vite production build. Both skill entrypoints and the distributable plugin passed their validators. Raw logs are retained locally in the ignored `.dsh-skill-build/productivity/` directory, including `final-release-checks.log` and `live-final-evidence.json`.

A final bounded repair changed explicit `worker-start` to use the same stable-runtime selection as daemon-started Workers. The Host independently reran its **four additional runtime tests**, all passing in 13.3 seconds (`worker-runtime-checks.log`). The real no-model probe stages the distributable plugin, occupies the original Worker, starts an extra Worker, removes the test's staged directory while its task is running, and confirms the extra Worker still finishes with stable interpreter/import/script/bridge paths. Development-source behavior and cooperative stop remain covered. This focused run follows the full-suite run above; the counts are not presented as one later full-suite invocation.

Coverage includes real Git dirty-input snapshots, unchanged source indexes, sequential checkout transfer, repeated continuation in the same checkout, fixed helper references, post-seal drift, cancellation/takeover during preparation, scoped agent credentials, stale Host control, exact-command replay, queued/nested Host requests, cancelled descendants and late receipts. The tests distinguish unconfirmed shutdown from a stopped process and verify that a parent wait remains pending when its descendants lack stop evidence.

## Real packaged DSH workflow

The test used the staged plugin launcher with private state and runtime roots, two independent Workers and `maxConcurrent:2`. Provider/model/effort were explicitly `deepseek-official / deepseek-flash / max`. The fixture was a small FIFO admission module, with staged and unstaged requirements plus an explicitly included untracked JSON fixture. It was a real coding task with independent tests, not a mocked model response.

The parent logical task was `56423624-4c4b-4ebf-8d4c-b7a92e76487b`:

1. Its initial turn read the captured inputs, changed only the README handoff and concluded `assistance`. The process stopped, the execution slot was released, and the checkout reservation remained.
2. An explicit Host decision authorized implementation and test helpers in two separate worktrees, both based on fixed commit `a5385c338ffd6d8629feb2fea7844c3043a065d0`. Their recorded execution intervals overlapped by **20.844 seconds**. Each modified only its own declared scope and returned a sealed output with confirmed shutdown.
3. The parent consumed the frozen `helperOutcomes[].artifact` references, verified and applied the exact diffs, ran the combined suite and concluded `attention` with `INTEGRATION_VERIFIED`. Its prompt explicitly prohibited finding helper work by scanning state directories or Git refs.
4. The browser authorized one final read-only continuation. A third native session reran the checks, preserved the files and concluded `completed`. All three turns used the same physical checkout, with successive input commits and distinct attempt/session identities.
5. The Host independently ran **27 unittest tests**, inspected the fixed diff and compared file blobs to the sealed final commit. The browser then accepted artifact `8010dad6-fdab-4d3b-9ff5-b7e9c237ab59`, tied to final attempt `8f33decf-0a04-4872-bb74-dc168e8a48ae`. CLI readback reported `accepted` with root and descendant shutdown confirmed.

The helper tasks were `9d63aae4-2549-4058-a953-4ae4ce7f89a8` (implementation, output `526625a9c40450186463e798ce7990fbc5b35775`) and `169cf43c-ac07-43f9-bde6-cf4b91b2cc22` (tests, output `1b04d239cf70c2fbe57845ae7a2c009f0cd6b174`). The final combined commit was `3ece9a90c668d81bf4edf68d2fd44fc61a37625c`; the final read-only turn produced the same tree. The original source HEAD, index SHA-256, staged/unstaged status and input file hashes matched the values captured before submission.

This run exposed a genuine two-Worker race: transient `WORKSPACE_BUSY` during preparation was initially converted into Host attention. The coordinator now leaves such contention queued. A regression holds the actual nonblocking file lock and verifies that no old input can be claimed, no attention boundary is created and the same continuation recovers after release. The private daemon was upgraded without cancelling the goal; the existing helper outputs were retained and the same parent continued. This recovery is recorded explicitly rather than presented as an uninterrupted first-pass success.

The reconstructed sessions were `session-f646be96-5dfb-4c8f-b3ad-3708a1368d17`, `session-77798891-2ff4-4f04-b12e-7d9963bad253` and `session-18e7949d-91a1-478f-af0d-f3a9270d116f`. No native same-session resume is claimed. The final completed session was also attached through the installed DSH workspace bridge; membership was confirmed without another model call.

## Native subagents and browser controls

A separate native smoke test created an actual internal DSH subagent. The official persisted session log contained the successful foreground `subagent` tool call and a child catalog entry with the matching parent session, `origin:subagent` and depth 1. The child independently checked a file artifact; the root then concluded through `buddy_finish_turn`. This verifies that the workflow preserves internal subagent use; no second Buddy dispatch was involved. Local evidence is in `native-turn-evidence.json`.

The real React/Vite console performed explicit Host takeover, manual continuation and final artifact acceptance. A delayed mutation using the superseded control file returned `STALE_GENERATION`. Readback retained the task identity and incremented owner generation without restarting its current execution. UI tests cover lost-response replay, explicit helper configuration/workspace allocation, final-attempt binding, actionable preparation errors and disabled acceptance while a descendant has unconfirmed shutdown.

Responsive checks at 320, 768, 1024 and 1440 pixels found no horizontal document overflow. Keyboard “skip to main” focused the main region. The real console had no captured browser warnings or errors after the final continuation and acknowledgement. Page refresh remained read-only and invoked no model.

## Scope and deployment

The supported coding adapter is DSH; additional harnesses, automatic community research, monetary budgets and automatic background maintenance scheduling are not implemented. The decision Buddy and bounded evaluation table were verified in the linked earlier record. Selection remains advisory, table maintenance remains explicitly requested and automatic publication remains opt-in. A business task retains its admitted configuration when evaluations change.

Governed workspaces require Git. Legacy one-shot commands remain available for non-Git tasks. Capabilities protect the supported API boundaries but do not sandbox a same-user process with full shell access. Worktrees isolate checkout writes, not shared repository metadata or external services. Native App post-turn wakeup remains outside this implementation.

Production replacement requires the `offline schema upgrade` from version 5 or 6 to 7, with its verified backup, followed by plugin/runtime identity checks. The production installation and standalone skill link must refer to the same released code; a successful private test does not establish that the user's installed cache has already changed.

Release candidate `0.5.0+codex.20260922045602` includes the final runtime repair. The private productivity service was stopped with no queued cancellations, no active cancellation requests and no unresolved attempts. Production preflight found schema 5, 40 completed tasks, three failed tasks and zero unconfirmed attempts. At this record's creation, the daily installation remains 0.4.0 pending the requested user's installation confirmation; its original standalone skill link and database are unchanged.

## Approved daily-board upgrade — 2026-09-22

The user subsequently authorized the daily upgrade after refreshing the documentation. Release `0.5.0+codex.20260922052648` updates both READMEs, both skill entrypoints, the document index and the usage/operations references. Independent documentation checks passed for 16 pages, 166 local links, 53 JSON examples and 44 shell blocks, including self-contained standalone links. Runtime content remains `79ddba6b67a36169892fdf571c13767c`; no executable behavior changed in this documentation pass. The DSH documentation run `3cc9445a-12e1-43f9-b79b-917e652f553e` reached its execution deadline during broader checks; its drafts were reviewed and corrected by the Host, and no full-suite pass is attributed to that run.

The old daily service was idle before shutdown and stopped with no queued cancellation, active cancellation or unresolved attempt. The offline migration upgraded schema 5 to 7 and created verified backup `board.sqlite3.v5-backup-20260922T052811Z`. Before starting the new service, a separate read-only comparison hashed every preserved row in all nine original business tables: 44 tasks, 44 attempts, one worker, 48 messages, 41 artifacts, 240 events, 101550 command receipts, 16 resource claims and zero cursors were identical. Integrity was `ok` with no foreign-key violation. The task total includes the documentation run added after the earlier 43-task preflight.

The migrated daily board cold-started with contract `0.5.0`, schema 7 and the expected stable runtime, with no source leaks. Real DSH governed smoke task `2d9848bc-7f09-4bb4-93e2-1f7038f78dd3` checked two local Markdown links in an isolated fixture, produced only `release-check.json`, completed with confirmed shutdown and was independently accepted. The Host parsed the report, checked both targets and inspected sealed commit `16e1b4be2fada678e2d287e7c5574c3d200bcfdc`; acceptance binds artifact `4b3236d7-cf18-4bf5-af1f-d0661412e0a9` to attempt `e9e0894c-089d-4415-8527-cbb0e431033a`. Installation readback and backup locations are retained in the local ignored `.dsh-skill-build/documentation-release/` receipts.
