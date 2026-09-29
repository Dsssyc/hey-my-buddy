# Installed 0.19.0

The user authorized the final integration and installation on 2026-09-29. The Claude Code Host merged `socu/console-polish` into `socu/local-harness-discovery` as `0a8a05b` (the one conflict region in each of `styles.css`, `console.md` and `evaluation.md` was resolved by run `7ffead25`, DeepSeek Flash), added the superseded-wait timeline fix `753981a` and ADR-018 (proposal) `532ebb2`, and fast-forwarded `socu/buddy-core` to `532ebb2`. `uv run --frozen python -m buddy.checks` passed on `753981a` (1,346 Python tests, 139 Node tests); the later commit changes documentation only.

## Result

The wheel `hey_my_buddy-0.19.0-py3-none-any.whl` was built from `532ebb2` with `uv build` and installed outside the Host sandbox with `uvx --from <wheel> hey-my-buddy install`. This time the Host did not prepend any Node version to `PATH`. The installer printed its `install-plan` (skill, Claude Code link, state and runtime paths), updated `~/.agents/skills/buddy` to 0.19.0, kept `~/.claude/skills/buddy` linked, and upgraded the running service with no rollback. The verified rolling backup was taken at schema 13 (155,805,195 bytes, 3,365 files); the board was migrated to schema 14, retained data fingerprints were verified with no source leaks, and the runtime switched to `eb08d1726c3ce1b3f298f78edf0afb84`.

After installation `health` reports status `ok`, contract 0.19.0, schema 14, a stable runtime, integrity `ok` and no foreign-key violations; `runtime` reports no source leaks. `adapters` reports `dsh` ready (0.1.5-rc.1), `zcode` ready (0.16.9, from the app installation), `codex` ready (codex-cli 0.157.0, found through the nvm default) and `claude` requiring login. The objective whose six Host waits had been closed by continuations now shows all six with an end time.

Two read-only smoke delegations from the frozen input `532ebb2` — DeepSeek Flash off (run `2782d0f4`) and Codex GPT-6 Sol high (run `fac5f053`) — were admitted, delivered with confirmed shutdown and reported `532ebb2` with zero `git status` entries. The Host read both results before recording them as integration not required and accepted; their managed checkouts were removed.

## Not established

- The Router configuration was not changed. The source verifies Codex read-only structured routing only for codex-cli 0.157.0 on macOS ([source record](local-harness-discovery-0.19.0.md)); the user selects the Codex Router profile and budget in Buddy 配置. The configured DSH Router cannot serve verified routing.
- The console polish was checked in the synthetic preview before the merge; the installed console was not re-inspected in a browser after installation.
- Windows remains unverified on a real machine.
- ADR-018 (fast and review routing) is a proposal and is not implemented.

## Same-version reinstall, 2026-09-29

The user authorized a reinstall once the final candidate passed. `socu/buddy-core` was fast-forwarded to `0413318`, which adds on top of `532ebb2`: the harness-probe login-name fix `9fcea9a` (Claude Code's keychain login needs `USER`), Codex's lightweight monitoring subagent guidance and its two Codex worker fixes (report budget and native checkpoint after a result failure, recorded in [codex-monitor-and-continuation-0.19.0.md](codex-monitor-and-continuation-0.19.0.md)), the timeline toolbar fix `ec6fe38` (run `71ffddc8`), and the console display fixes `0413318` (run `cc8fe451`). `uv run --frozen python -m buddy.checks` passed on `0413318` (1,363 Python tests, 139 Node tests); the Host had also independently rerun it on Codex's `a8a4334` with the same counts.

The same `uvx --from <wheel> hey-my-buddy install` path upgraded the service with no rollback: the verified backup was taken at schema 14 (158,760,384 bytes, 3,470 files), retained data fingerprints matched with no source leaks, and the runtime switched to `6c0a69101cbebce8b0c0a3b9edd46310`. The installer reported the skill placement as `already-current` although the installed `SKILL.md` carries the new generic monitoring-model wording, and `skill.json` records `sourceCommit: null` because the wheel was built through an sdist with `uv build`; later candidate wheels should be built with `uv build --wheel` from the checkout so the source commit is retained.

After reinstalling, `health` reports 0.19.0, schema 14 and integrity `ok`, and `adapters` reports all four harnesses ready, including `claude` (2.1.284) now that the probe keeps the login name. Read-only smoke delegations on DeepSeek Flash off (run `9f47019a`) and Codex GPT-6 Luna high (run `210f8648`) reported `0413318` with a clean checkout; the Host read both before recording them as accepted.
