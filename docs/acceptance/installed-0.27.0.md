# Installed 0.27.0

The user authorized installing 0.27.0 on 2026-10-01 after the checks and the rehearsal had passed. It adds the [native sandbox review proof](native-sandbox-review-0.27.0.md), which rests the Codex review check on controller-fixed native probes and checks model tool streams structurally, and the [parallel check runner](checks-speedup.md) to 0.26.0.

## Integration

The review proof (run `1779470a`) was integrated as `830758a` on `socu/integration-0.26` after the Host adopted its out-of-scope source and documentation paths. The check speedup (run `5b72e792`) was cherry-picked as `afe376f` and corrected by the Host in `fe4f43f` and `9849d9e` after its own review and an independent one (run `67180491`); its record lists the corrections. On `9849d9e` the parallel check passed three times and the serial check once, each with 1,850 Python tests (one skipped) and 165 Node tests; the two later commits change only documentation that is not packaged. A read-only rehearsal on the daily board, run inside the Host sandbox, reported `backup-preflight` `ok: true` with 3,658 evidence files and no skipped or rejected entries, and `require_readiness` passed.

## Result

The wheel was built with `uv build --wheel` from `e4fbc91` and installed outside the Host sandbox with `uvx --from <wheel> hey-my-buddy install` on the first attempt. The skill placement was reported `updated`, `skill.json` records `sourceCommit` `e4fbc91696f354991252ea2abddc73d45f286aa5`, and `~/.claude/skills/buddy` stays linked. The upgrade took a verified schema-15 backup (71,066,368 bytes, 3,664 files, 17.2 seconds, zero skipped attempt entries), needed no private-file relocation, verified retained data fingerprints with no source leaks and switched the runtime from `37648b41bc5adfb216f2e5bf450e948e` to `7aadcb4af814adbde51eaa088e8c7ae1` with no rollback. Runtime pruning completed and removed the runtime before the previous one (42,800,543 bytes). It kept the current and previous runtimes and skipped 48 entries it could not prove to be complete runtimes: 37 install lock files, two incomplete runtime directories from 2026-09-23 and 2026-09-25, and nine build directories of the first (under 3 MB together).

After installation `health` reports status `ok`, contract 0.27.0, schema 15, a stable runtime, integrity `ok` and no foreign-key violations; `runtime` reports source `e4fbc91` and no leaks; `backup-preflight` reports `ok: true` with 3,658 copied entries and an empty relocation plan. All four harnesses are ready (DSH 0.1.5-rc.1, ZCode 0.16.9, Codex 0.159.0, Claude Code 2.1.284), and the user's Router settings were retained (fast `dsh:deepseek-official:deepseek-flash:off`, review `codex:openai:gpt-6-luna:high`, default fast).

A routed read-only smoke delegation (run `688ae94d`, no configuration constraints) was delivered on Codex GPT-6-Luna high with confirmed shutdown and a complete native token-usage record, and reported `e4fbc91` with zero `git status` entries. The Host read the result before recording it as integration not required and accepted.

## Not established

- The Codex review capability still shows the failed certificate taken on 2026-10-01 with the 0.26.0 checker. The probe-based check of 0.27.0 needs one user-approved native verification, which the user starts from the Harness section.
- Pruning does not remove the leftover install locks and incomplete runtime directories; the backlog records them.
- Worker accounts are available but none is configured on the daily board; Claude OAuth, DSH and ZCode account mutation and Windows remain unverified.
- The DSH session archive still needs the updated workspace bridge in the user's DSH profile.
- The installed console was not re-inspected in a browser after installation.
