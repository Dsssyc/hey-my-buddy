# ADR-017 implementation plan

This plan is prepared against `dcd482a` for the 0.19.0 source candidate. Implementation is authorized; the schema 14 change below was separately approved by the user on 2026-09-28. No daily installation, board migration, configuration change or package publication is authorized.

## Proposed schema 14

Add one service-owned `harness_health` row per supported harness (`dsh`, `zcode`, `codex`, `claude`). The row holds `adapter` (primary key), `manual_path` (nullable user setting), `revision` (compare-and-swap generation), `status` (`unknown`, `ready`, `missing`, `login-required`, `unhealthy`), `record_json` (bounded diagnostic facts), `checked_at`, `expires_at` and `scan_after`. Diagnostic facts contain selected executable/interpreter paths, version, source, file fingerprints (real path, size, mtime), version-manager record fingerprints, bounded attempted locations, stable failure code and repair advice. No environment values, authentication output, account identities or raw subprocess output are persisted.

The blackboard is the sole health writer. Refresh reserves a generation before performing bounded filesystem/handshake work outside the SQLite transaction; publication compares that generation so a late discovery cannot overwrite a newer manual selection or result. Manual path changes require Host or authenticated-console authority and invalidate the health generation. Workers use named operations and report pre-start failure under attempt authority; they cannot change manual settings.

Migration is additive from schema 13 to 14 and starts with an empty health table: no discovery, provider call or modification of model settings takes place inside migration. Startup continues to refuse an old schema. Only the new package's `upgrade` migrates an idle board after a verified rolling backup under the existing start, daemon and board-owner locks. All old table fingerprints and non-schema meta values must remain identical. Failure restores the verified backup and previous runtime; backup verification tests migration on a private copy. Implementation and migration tests use private state/runtime roots only.

## Integration boundaries

Submission and routing use the recorded health plus native model metadata. Ordinary submission does not wait for a handshake. A call-triggered, rate-limited discovery job checks known locations and manager records, with no periodic background scanner. Explicitly naming an unavailable harness or requesting refresh performs one bounded current discovery. A claim carries the selected health generation and executable identity; before starting, the Worker asks the service to revalidate changed fingerprints. Only a proven pre-model startup failure permits one rediscovery/retry, recorded in the attempt. No retry is inferred from a process failure after model work may have started.

Healthy version changes trigger the existing monotone per-harness catalog publication outside the health transaction. Unhealthy records immediately exclude that harness from effective availability and routing without deleting catalog identities, cards, user enablement, family preferences or notes. Newly discovered configurations retain the existing disabled default and new marker.

## Delivery

Stage 1 implements discovery, health, effective availability and diagnostics, with focused tests and its own commit. Stage 2 switches the daily launcher to the active runtime, enforces sandbox preflight and coordinated idle cutover, and documents verified Host permission configuration. Stage 3 builds an installable distribution and private-uv bootstrap scripts without publishing. Codex GPT-6 Sol high Router probes start only after Stage 1 and a separate user approval for each fresh output directory; capability remains unverified until the recorded native evidence meets every acceptance criterion. The cleanup and SQLite warning repairs are independent commits. Final acceptance includes the complete checks and an explicit list of remaining native/Windows/publication boundaries.
