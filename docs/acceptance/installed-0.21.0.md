# Installed 0.21.0

The user authorized installing 0.21.0 on 2026-09-30 after the Host accepted ADR-018 Part 2, including the daily board migration from schema 14 to 15. The merge review, the three independent-review fixes and their checks are recorded in [host-workflow-0.21.0.md](host-workflow-0.21.0.md).

## First attempt rolled back

The first installation, from a wheel built at `e87bcf7`, returned `UPGRADE_ROLLED_BACK` with failure `BACKUP_UNSAFE_PATH` before any migration or cutover; the service stayed on 0.20.0 (runtime `7d5a7721d3629f25475a6266f7eb2974`, schema 14) with health and integrity `ok`. The user's DSH fast Router had created `attempts/<runId>/<attemptId>/no-tool-<hex>/dsh-home/` in six attempts, whose `profiles/node_modules` held 2,886 symbolic links, and the Codex fast Router's `no-tool-<hex>/native/codex-home/` was not covered by the 0.20.0 exclusions either. Run `d22c1615` (ZCode GLM-5.3 max, chosen by routing) excluded these no-tool private directories and the no-tool provider snapshots by exact position, made other links or special files inside `attempts/` a skipped-and-recorded entry instead of a failure, and named the state-relative path in every remaining `BACKUP_UNSAFE_PATH`. The Host integrated it with an identical patch as `f742488` and confirmed that the two `test_backup_windows` failures the Worker reported occur only with an unresolved `/var` temporary directory. `buddy.checks` passed on `f742488` (1,596 Python tests, 161 Node tests). Both incidents and the structural solution are recorded in ADR-019 (background and decision 11), which the user asked to implement first.

## Result

The wheel was built with `uv build --wheel` from `86b1b51` and installed outside the Host sandbox with `uvx --from <wheel> hey-my-buddy install`. The skill placement was reported `updated`, `skill.json` records `sourceCommit` `86b1b51ac304aa54605e5995a46e5d9b68de9993`, and `~/.claude/skills/buddy` stays linked. The upgrade took a verified schema-14 backup (154,295,402 bytes, 3,962 files, 9.6 seconds, zero skipped attempt entries), migrated the board to schema 15, verified retained data fingerprints with no source leaks, switched the runtime to `17b0fe97dcd3e9c961c54c167c9fb1ee` with no rollback, and pruned three older runtimes (119,653,457 bytes).

After installation `health` reports status `ok`, contract 0.21.0, schema 15, a stable runtime, integrity `ok` and no foreign-key violations; `runtime` reports source `86b1b51` and no leaks. `adapters` reports `dsh` (0.1.5-rc.1), `zcode` (0.16.9), `codex` (codex-cli 0.157.0) and `claude` (2.1.284) ready. The user's Router settings were retained: fast `dsh:deepseek-official:deepseek-flash:off`, review `codex:openai:gpt-6-luna:high`, default mode fast. `buddy help` lists 75 methods.

A routed read-only smoke delegation submitted through the new `--params-file` input (run `44640d0e`, fast mode, no configuration constraints) was delivered on Codex GPT-6 Luna high with confirmed shutdown and reported `86b1b51` with zero `git status` entries; its attempt carries a complete native token-usage record (61,836 input tokens including 30,464 cached, 211 output). The Host read the result before recording it as integration not required and accepted.

## Not established

- Quota observations, reminders, partial outputs, direct Host completion, recorded conclusions, configuration replacement and cumulative patches have not yet been exercised by real daily work on the installed runtime.
- The installed console was not re-inspected in a browser after installation.
- Windows remains unverified on a real machine.
- ADR-019 is not implemented; the backup exclusions and skip fallback in this installation are interim measures.
