# Work objectives and delegation timelines: 0.14.0 candidate

## Scope and boundary

The user authorized section X of the [Claude proposal](../decisions/012-claude-code-distribution-and-evidence-routing.md) with the UI term 工作目标, latest-activity ordering and 30-minute idle folding. The Codex Host thread `01a0c252-f073-7e13-9a5f-033260938176` started the slice and ran out of Codex quota at 17:07 on 2026-09-26 with the backend partly written and uncommitted. At the user's request a Claude Code Host session continued the same branch and agenda. Release and named contract advance to 0.14.0 and the schema to 12. The [objectives reference](../reference/objectives.md) owns the contract. The daily board remains the installed 0.12.0 runtime; installation needs a separately authorized, coordinated cutover with an explicitly prepared schema-12 copy.

## Backend

Codex had implemented the schema-12 tables and columns, the admission-time objective attachment in the run's own transaction, strict display metadata outside the execution spec and request fingerprint changes, helper inheritance, Worker-claim isolation and the event-sequence activity projection, with five admission tests and a frozen schema-11 DDL fixture. The Claude Host reviewed that work and completed:

- `objective_list` and `objective_timeline` as bounded read-only derivations in `src/buddy/objectives.py`, registered on the named C-Two contract, the service operation table, the CLI (`objective-list`, `objective-timeline`) and the authenticated console GET routes `/api/objectives` and `/api/objectives/<objectiveId>/timeline` with exact parameter whitelists;
- response shapes aligned with the frontend contract Codex had fixed in `apps/console/src/objective-types.ts`, which now also lists the additional fields;
- `python -m buddy.board_prepare`, the explicit offline preparation of a schema-12 copy from an idle schema-11 board, with no startup conversion;
- brief CLI views that carry `objectiveId` and `title`, and the version and documentation updates.

Two defects in the unfinished work were corrected: the admission tests reused one checkout for several writers or read snapshots and failed before any objective logic ran, and several existing tests still asserted schema 11.

## Verification

Focused tests pass: 11 objective tests (admission, grouping and replay conflicts, foreign-Host and unknown-group refusal, strict metadata, reads that never advance activity, list ordering by recorded activity, member filters and matching counts, filter-bound cursors and the `changed` signal, timeline rows/spans/markers, an unconfirmed stop that stays uncertain and open-ended, truncation and filter scope, identity errors, and refusal of attempt-scoped credentials); 3 offline-preparation tests (a schema-11 copy rebuilt from a real board opens under the schema-12 runtime with every row and the capability secret preserved, derives exactly the activity sequences the runtime maintained, never writes the source, and refuses an existing destination, a non-schema-11 source, held owner locks and open work); and the console HTTP route test (authentication, whitelists, percent-encoded `run:` identifiers and equality with the named operation).

The Claude Host also accepted Codex's two delegated inputs after taking over their ownership as `claude-code-objective-timeline`: the Claude Opus design goal `bfaf548e` (spec and static mockup, not visually verified in a browser) integrated as `883b51c` by `int-b264494c`, and the DSH layout goal `a2514cd9` (Node 24 `tsc --noEmit` clean, 34/34 Vitest tests) integrated as `4279655` by `int-eed574b0`. Both managed checkouts were then removed through their cleanup plans.

A focused rerun of the 91 tests in the edited modules passed. A complete `buddy.checks` run started before the last edits reported only the since-fixed schema-11 assertions and CLI help length; a complete run on the committed candidate has not yet been recorded.

Not done: the frontend view that consumes these reads (the user stopped that work on 2026-09-26), a complete check on the committed candidate, the packaged-runtime check and any installation. The candidate is therefore not ready to install.
