# Console and Harness-owned evaluation acceptance

Date: 2026-09-25. Source branch: `socu/buddy-core`. Release candidate: `0.7.0+codex.20260924162940`, named contract 0.7.0, schema 9. The user requested verification first and explicitly deferred installation until further notice. No 0.7 plugin was installed and the daily database was not converted.

## Fixed inputs and integration

Parallel dispatch was integrated in `e6c13fd` from Buddy output `c7d7ebc67c4fa59ba6c9c67c0f9eaeea9d173815`. Console source came from `035232cc06f9ffeca8decde58a34d18410d15559`. The evaluation implementation began at `5c38040b3b081fd8b9e5a22208f8ad6af7a84c2b`; review found insufficient archived-review and artifact proof, so that base was rejected and its superseded goal closed. Follow-on output `559a295d107dcae6d7c68a01f559551b31b19c81` supplies corrected proof, indexed collection, public wiring and removal of internal maintenance execution. Host integrated these artifacts, removed obsolete daemon renewal, reconciled tests/docs, advanced the contract, and rebuilt checked-in console assets.

## Verified behavior

- Edit mode is a shared local draft across model/settings pages; entering it and typing do not acquire a writer or call a model. Save takes a short grant. Dirty exit, keyboard focus containment/restoration, version conflict, queued cancellation and ambiguous begin/publish/abort replies have component coverage.
- Enabled effort, inspected effort and unsaved enablement each have a distinct indication. Evidence remains read-only, with the agreed empty-state guidance. Published revision history is a bounded read. Light/dark choice persists without storing credentials or drafts. Tested semantic text/background contrast pairs have a minimum ratio of 4.78 in light mode and 7.07 in dark mode. No browser automation or visual-browser acceptance was performed, as requested.
- External Harness maintenance prepares bounded, deduplicated facts across source Hosts/projects. Immutable event cursors survive clock rollback and backfill newly published profiles. A qualified rejected attempt and its later accepted continuation remain distinct. Artifact proof binds run, attempt, output kind, hash and turn. Query-plan tests verify the partial review index rather than a scan through unrelated events.
- Maintenance publication merges only changed cards and preserves unrelated cards, profiles, preferences and configuration. Conflicting revisions and expired grants cannot overwrite current data. Snapshot sample counters exist independently of card prose. Browser commands cannot prepare evidence, edit evidence or start internal maintenance; historical maintenance decisions remain readable.

## Verification evidence

`uv run --frozen python -m buddy.checks` passed: 569 Python tests and 195 Node tests, exit 0. The focused evaluation/console run initially found one stale snapshot-field assertion, which was updated for `sampleCounts` and passed its focused rerun before the complete suite. Vitest passed all 103 tests in 11 files; `npm run build` passed TypeScript and Vite and produced `index-BtvdRc7d.css` and `index-gB02U3a6.js`. The rebuilt assets match the sealed frontend output.

The staged plugin and skill validators passed. A candidate launched from a separate private state/runtime root reported stable runtime, contract 0.7.0, schema 9 and default capacity 2 business + 1 decision. Its real named prepare/history/snapshot operations passed, replay returned the identical preparation packet, and no model tasks were created. Loopback HTTP served both JS/CSS assets byte-identical to the candidate files. The private workers stopped cooperatively and released their lifetime locks.

An offline retained-data rehearsal copied the daily schema-8 database without modifying it, prepared schema 9 separately, compared every retained column of all 35 existing tables, and passed integrity and foreign-key checks. The original copy remains recoverable. This rehearsal does not authorize or constitute live activation; a future installation must take a fresh backup and verify that all old-contract work has settled.

## Running installation and pending activation

The daily service already runs stable parallel runtime `63dbb934d7f4e430b04a9450ce05ed4f`, contract 0.6.1/schema 8. Its temporary operational setting is 3 business + 1 decision; source defaults are 2 + 1. The C-Two run retained attempt `ac56cf4c-73c8-40a2-83d5-ee5df572384f` through the transition. All Buddy implementation work for this delivery has finished and its fixed artifacts have been reviewed; unrelated C-Two work remains under its own Host.

The installed plugin cache remains `0.6.1+codex.20260924125851`. The new console and evaluation operations become available only after the user authorizes the full switch. At that point, inspect active work again, prepare a fresh verified database copy and backup, refresh idle workers with the new named contract, install the staged plugin, restore default 2 + 1 capacity and verify health plus the console. Do not cancel another Host's work to obtain that window or point a new client at the old interface.
