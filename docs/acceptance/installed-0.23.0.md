# Installed 0.23.0

The user authorized installing the accepted candidate on 2026-09-30. It combines batch A ([console and routing fixes](console-routing-fixes-0.22.0.md), including ADR-020's optional loopback login), the zero-candidate routing source fix and ADR-019 B phase one ([worker account visibility and review verification](worker-accounts-phase1-0.23.0.md)). The full check on `9cf4bf7` passed (1,659 Python tests, 161 Node tests); the later commit changes documentation only.

## Result

The wheel was built with `uv build --wheel` from `0c5592b` and installed outside the Host sandbox with `uvx --from <wheel> hey-my-buddy install` on the first attempt. The skill placement was reported `updated`, `skill.json` records `sourceCommit` `0c5592b28500efeaedb8175cd98f91da4993e928`, and `~/.claude/skills/buddy` stays linked. The upgrade took a verified schema-15 backup (156,908,731 bytes, 4,129 files, 10.1 seconds, zero skipped attempt entries), kept schema 15, verified retained data fingerprints with no source leaks, switched the runtime from `17b0fe97dcd3e9c961c54c167c9fb1ee` to `64641d79d1bae2ba6b40b48f7be106c4` with no rollback, and pruned one older runtime (41,787,004 bytes).

After installation `health` reports status `ok`, contract 0.23.0, schema 15, a stable runtime, integrity `ok` and no foreign-key violations; `runtime` reports source `0c5592b` and no leaks. `adapters` reports all four harnesses ready (DSH 0.1.5-rc.1, ZCode 0.16.9, Codex 0.159.0, Claude Code 2.1.284); the Codex review capability reports `new-version`, so review routing falls back to fast routing until the user runs the review verification for 0.159.0. `buddy console '{"browser":false}'` returns the fixed `http://127.0.0.1:49637/` with no expiry. The user's Router settings were retained (fast `dsh:deepseek-official:deepseek-flash:off`, review `codex:openai:gpt-6-luna:high`, default fast). Billing labels show DSH as `metered` from its DeepSeek API key and the Codex configurations as `unknown` until an account read runs; no enabled configuration is marked exhausted.

A routed read-only smoke delegation (run `4edafdb5`, fast mode, no configuration constraints) was delivered on Codex GPT-6 Luna high with confirmed shutdown and reported `0c5592b` with zero `git status` entries; its attempt carries a complete native token-usage record (62,738 input tokens including 30,464 cached, 323 output). The Host read the result before recording it as integration not required and accepted.

## Not established

- The review verification for Codex 0.159.0, the Codex account and quota reads, and every ADR-019 native check remain to be run with the user's per-run approval.
- The installed console was not re-inspected in a browser after installation.
- Windows remains unverified on a real machine.
- ADR-019 decision 11 and B phase two are not implemented.
