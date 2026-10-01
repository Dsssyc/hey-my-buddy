# Installed 0.26.0

The user authorized installing the combined candidate on 2026-10-01 once checks and the rehearsal passed. It adds [ADR-019 sub-batch ④, Worker accounts](worker-accounts-batch4-0.26.0.md) and the [native sandbox review verification](native-sandbox-review-0.26.0.md) to 0.25.0; each was implemented and verified in its own Codex session, including the per-run approved native checks recorded there.

## Integration

The Host merged `socu/sandbox-review-results` (`536be84`) into `socu/integration-0.26` without conflicts as `81cf95d`, then `socu/worker-accounts-batch4` (`8632117`) as `7dd7003`. The sub-batch ④ branch carries the local merge `b9eb842` of `24b67af` into the earlier `main` (content identical to `24b67af`); the Host later returned local `main` to `origin/main` and protected `main` (pull requests only on GitHub, and a shared `.githooks/reference-transaction` hook that keeps local `main` equal to `origin/main`). The five conflicts were documentation in which both sides had added a paragraph at the same place; both were kept, with the review verifier paragraph placed before the new account sections. The console sources and assets equal sub-batch ④'s verified build, and `SKILL.md` stays at 4,092 bytes. The full check on `7dd7003` passed (1,828 Python tests with one skipped, 165 Node tests). A read-only rehearsal on the daily board reported `backup-preflight` `ok: true` with 3,547 evidence files and no skipped or rejected entries, and `require_readiness` passed.

## Result

The wheel was built with `uv build --wheel` from `7dd7003` and installed outside the Host sandbox with `uvx --from <wheel> hey-my-buddy install` on the first attempt. The skill placement was reported `updated` and `skill.json` records `sourceCommit` `7dd7003c7acf0c283d325ff4ecf6695ff87dbc10`. The upgrade took a verified schema-15 backup (69,394,602 bytes, 3,554 files, 16.9 seconds, zero skipped attempt entries), needed no private-file relocation, verified retained data fingerprints with no source leaks and switched the runtime from `a5c0464cf440f3e9d02597be42cf0d40` to `37648b41bc5adfb216f2e5bf450e948e` with no rollback; runtime pruning completed and reclaimed 42,681,005 bytes.

After installation `health` reports status `ok`, contract 0.26.0, schema 15, integrity `ok` and no foreign-key violations; `runtime` reports source `7dd7003` and no leaks. All four harnesses are ready with the default `native` account source (the shared local login, managed read-only), and the user's Router settings were retained. A routed read-only smoke delegation (run `d58ab2f8`) was delivered on Codex GPT-6-Luna high with confirmed shutdown and reported `7dd7003` with zero `git status` entries; it ran in a newly created goal-private `native/codex-home` under the Codex private area, the first daily run of sub-batch ③'s private Codex home. The Host read the result before recording it as integration not required and accepted.

## Not established

- Codex review routing still carries the earlier failed certificate; the corrected native-sandbox checker needs one user-approved verification.
- Worker accounts are available but none is configured on the daily board; Claude OAuth, DSH and ZCode account mutation and Windows remain unverified.
- The DSH session archive still needs the updated workspace bridge in the user's DSH profile.
- The installed console was not re-inspected in a browser after installation.
