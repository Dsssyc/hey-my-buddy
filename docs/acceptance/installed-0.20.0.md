# Installed 0.20.0

The user authorized installing 0.20.0 on 2026-09-29 after the Host accepted ADR-018 Part 1. The Host merged `socu/routing-modes` into `socu/buddy-core`, which already carried the timeline requeue fix `049171c`, as `f97dea8` on `socu/integration-0.20`; `uv run --frozen python -m buddy.checks` passed on it (1,405 Python tests, 142 Node tests). The live ZCode fast-route check was then added to the [routing-modes record](routing-modes-0.20.0.md) (`e5d10af`), followed by the ADR-019 proposal.

## First attempt rolled back

The first installation, from a wheel built at `d8bb5de`, returned `UPGRADE_ROLLED_BACK` with failure `BACKUP_UNSAFE_PATH` before cutover. The service stayed on 0.19.0 (runtime `6c0a69101cbebce8b0c0a3b9edd46310`) with health `ok`, integrity `ok` and no work affected. Codex Router attempts had left `attempts/<runId>/<attemptId>/native/codex-home/auth.json` as symlinks to the user's `~/.codex/auth.json` (four on the daily board), and the backup refuses links in the trees it copies. The same backup was also copying ZCode attempt-private provider snapshots, which can contain API keys, from 108 attempt directories.

Run `d0ae4c57` (Codex GPT-6 Sol high) excluded exactly `native/codex-home/` and the attempt-level `builtin-provider.json` and `personal-provider.json` from backups, kept every other link or nonregular file fail-closed, and made the Codex controller remove its private `auth.json` link after confirmed stop on the review and fast Router paths. The Host read the change and integrated the sealed commit unchanged as `0ca7bba`; the daily links were not deleted by hand. `buddy.checks` passed on `541a45f` (1,410 Python tests, 142 Node tests). The structural follow-up (attempt-private native content outside the backed-up tree, an invariant test across harnesses, a path-naming `BACKUP_UNSAFE_PATH` and a read-only pre-install backup check) is ADR-019 decision 11.

## Result

The wheel was built with `uv build --wheel` from `edfee37` and installed outside the Host sandbox with `uvx --from <wheel> hey-my-buddy install`. The skill placement was reported `updated`, `skill.json` records `sourceCommit` `edfee377df6fac14a758ef473b49cb6e4791fd1b`, and `~/.claude/skills/buddy` stays linked. The upgrade took a verified schema-14 backup (148,809,550 bytes, 3,692 files, 9.2 seconds) without the excluded attempt-private content, verified retained data fingerprints with no source leaks, switched the runtime to `7d5a7721d3629f25475a6266f7eb2974` with no rollback, and pruned two older runtimes (78,174,953 bytes).

After installation `health` reports status `ok`, contract 0.20.0, schema 14, a stable runtime, integrity `ok` and no foreign-key violations; `runtime` reports source `edfee37` and no leaks. `adapters` reports `dsh` (0.1.5-rc.1), `zcode` (0.16.9), `codex` (codex-cli 0.157.0) and `claude` (2.1.284) ready. The upgrade placed the previous Router in the review slot (`codex:openai:gpt-6-luna:high`), left the fast slot empty and kept the default mode `review`; the user chooses a fast Router and the default mode in Buddy 配置. The timeline fix that starts a requeue after the closed Host wait, previously listed as fixed and awaiting installation in the backlog, is part of this installation.

Two read-only smoke delegations from the frozen input `edfee37` were delivered with confirmed shutdown and reported that commit with zero `git status` entries: DeepSeek Flash off chosen explicitly (run `a0fdd2e5`), and a routed goal in the default review mode (run `a3b04869`), where the Luna Router selected Codex GPT-6 Luna high, citing a matching smoke-check sample on its card, over cheaper candidates. The Host read both results before recording them as integration not required and accepted.

## Not established

- No fast Router is configured, so fast routing and review-to-fast fallback have not run on the installed runtime; the live DSH and ZCode fast-route checks in the routing-modes record ran from source with private roots.
- The installed console was not re-inspected in a browser after installation.
- Windows remains unverified on a real machine.
- ADR-018 Part 2 and ADR-019 are not implemented.
