# Installed 0.25.0

The user authorized installing the combined ADR-019 B phase two sub-batches on 2026-09-30 once review and checks passed. The candidate combines [① Buddy configuration sections](buddy-config-sections-0.25.0.md), [② routing validation and quota recovery](routing-validation-0.25.0.md) and [③ Worker session privacy and storage](worker-session-privacy-0.25.0.md); each sub-batch was implemented in its own Codex session and worktree.

## Integration

The Host fast-forwarded `socu/integration-0.25` to ① (`dbcf939`), then merged ② (`socu/private-dirs`, `2a5bcd6`) as `79a03a6` and ③ (`socu/worker-session-privacy`, `807dbff`) as `8f4ac14`. The one code conflict came from ① moving the Harness status out of `BuddyConfig.tsx` into `HarnessStatus.tsx` while ② added the quota recovery entry in the old place; the Host kept ①'s structure and moved ②'s `QuotaRecovery` into the Harness row of `HarnessStatus.tsx`. The other conflicts were documentation, the backlog (both sides removed their completed entries), a test-fixture socket naming fix that ① and ③ had made independently (①'s version kept) and `SKILL.md`, whose combined text exceeded the 4 KiB budget and was shortened to 4,091 bytes by pointing the new session and quota details to their references. ②'s last full check had failed only on that budget and was fixed before its commit without a rerun. After merging, ②'s eight quota recovery console tests still opened the old combined page; `9f72304` opens the Harness section in them and rebuilds the console assets for the combined tree (624 front-end tests pass). The full check on `9f72304` passed (1,774 Python tests, 165 Node tests). A read-only rehearsal on the daily board with the new source reported `backup-preflight` `ok: true` with 3,481 evidence files, no skipped or rejected entries and an empty relocation plan, and `require_readiness` passed.

## Result

The wheel was built with `uv build --wheel` from `9f72304` and installed outside the Host sandbox with `uvx --from <wheel> hey-my-buddy install` on the first attempt. The skill placement was reported `updated`, `skill.json` records `sourceCommit` `9f72304ae25a20fe27b151aa0f3c6a93e1c3a408`, and `~/.claude/skills/buddy` stays linked. The upgrade took a verified schema-15 backup (67,687,153 bytes, 3,488 files, 16.7 seconds, zero skipped attempt entries), needed no private-file relocation, verified retained data fingerprints with no source leaks and switched the runtime from `faea5cae16db58351f0d071ef601c7ce` to `a5c0464cf440f3e9d02597be42cf0d40` with no rollback. Runtime pruning now completed and reclaimed 420,382,455 bytes of older runtimes; the current and previous runtimes and two malformed early directories (48 KB and 2 MB) remain, the latter reported rather than aborting the cleanup.

After installation `health` reports status `ok`, contract 0.25.0, schema 15, integrity `ok` and no foreign-key violations; `runtime` reports source `9f72304` and no leaks. All four harnesses are ready, `backup-preflight` reports no skipped or rejected entries, and the user's Router settings were retained. Codex 0.159.0 still carries the failed review certificate recorded before ②'s checker correction, so review routing keeps falling back to fast routing until the user approves a new verification.

A routed read-only smoke delegation (run `6a1c733b`, fast mode, no configuration constraints) was delivered on ZCode GLM-5.3-Flash max with confirmed shutdown and reported `9f72304` with zero `git status` entries. The Host read the result before recording it as integration not required and accepted.

## Worker session history

`worker-sessions` lists 13 board-recorded DSH sessions created by Buddy. The DSH plan blocked all 13 with `unsupported-method`, because the workspace bridge installed in the user's DSH web profile predates the archive method; no DSH session was changed. Codex history was not touched.

## Not established

- The corrected Codex review checker has not been certified on a native run; it needs the user's approval for one verification.
- The DSH session archive needs the updated workspace bridge in the user's DSH profile and a reload of that profile.
- The installed console was not re-inspected in a browser after installation.
- Windows remains unverified on a real machine.
- ADR-019 sub-batch ④ (Worker accounts) is not implemented.
