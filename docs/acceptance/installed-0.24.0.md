# Installed 0.24.0

The user authorized installing the accepted ADR-019 decision 11 candidate on 2026-09-30, including the tidying of the daily board, once review, checks and a rehearsal had passed. The implementation and its reviews are recorded in [private-directories-0.24.0.md](private-directories-0.24.0.md).

## Pre-installation rehearsal

The Host merged `socu/private-dirs` (`77aa147`) into `socu/buddy-core` as `99036e1` without conflicts; the full check passed there (1,721 Python tests, 161 Node tests). Before asking to install, the Host ran the new source against the daily board read-only. `backup-preflight` ran inside the Host sandbox, which cannot write the daily state directory, and reported `ok: true` with 3,358 evidence files copied, 1,129 entries skipped and none rejected. The upgrade's own readiness rule (`private_migration.preview` and `require_readiness`, opened with a read-only SQLite URI) then refused the board with `BACKUP_PREFLIGHT_FAILED`: 113 legacy ZCode `native-logs` directories were skipped by the backup but not covered by the relocation plan, which the task prompt had not listed. Run `57441d33` (ZCode GLM-5.3 max, chosen by routing) added the relocation of legacy `native-logs` into the ZCode private area (moved, not deleted; unconfirmed stops stay blocked) and made `backup-preflight` report the relocation plan's readiness so that its `ok` matches the installer. The Host integrated it unchanged as `3049ca5`; a second read-only rehearsal on the daily board reported the plan ready with 1,253 covered items and no uncovered or blocked entries, and `require_readiness` passed. The full check on `3049ca5` passed (1,724 Python tests, 161 Node tests).

## Result

The wheel was built with `uv build --wheel` from `3049ca5` and installed outside the Host sandbox with `uvx --from <wheel> hey-my-buddy install` on the first attempt. The skill placement was reported `updated`, `skill.json` records `sourceCommit` `3049ca5ffe1c59a143853b2b1c9f980292daaa31`, and `~/.claude/skills/buddy` stays linked. The upgrade took a verified schema-15 backup under the new evidence allowlist (66,343,527 bytes, 3,385 files, 16.0 seconds; the 1,140 skipped attempt entries were the legacy private content not yet relocated), verified retained data fingerprints with no source leaks, relocated the legacy private content (1,385 items: 340 moved into the private area, 913 deleted credentials and provider snapshots, 132 attempt credential cleanups) and switched the runtime from `64641d79d1bae2ba6b40b48f7be106c4` to `faea5cae16db58351f0d071ef601c7ce` with no rollback. Runtime pruning reported `complete: false` with `NotADirectoryError` and the note that cleanup can be retried independently; eleven older runtime directories (about 470 MB, the oldest from 2026-09-19) remain besides the current and previous runtimes.

After installation `health` reports status `ok`, contract 0.24.0, schema 15, a stable runtime, integrity `ok` and no foreign-key violations; `runtime` reports source `3049ca5` and no leaks. All four harnesses are ready. `backup-preflight` now reports `ok: true`, `needsAttention: false`, 3,378 copied, 0 skipped and 0 rejected entries with an empty relocation plan, and the attempt tree contains no symbolic links, attempt credential files or provider snapshots.

A routed read-only smoke delegation (run `6e0da74d`, fast mode, no configuration constraints) was delivered on ZCode GLM-5.3-Flash max with confirmed shutdown and reported `3049ca5` with zero `git status` entries. Its attempt directory holds only evidence files (task, turn input and output, activity, harness selection, spawn records and runner and native stderr logs); no control file with a token, provider snapshot, bridge credential or native log directory was written there. The Host read the result before recording it as integration not required and accepted.

## Not established

- Runtime pruning did not finish; the cause of `NotADirectoryError` is recorded in the backlog.
- The installed console was not re-inspected in a browser after installation.
- Windows remains unverified on a real machine.
- ADR-019 B phase two is not implemented.
