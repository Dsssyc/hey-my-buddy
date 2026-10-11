# Installed 0.29.0

The user authorized this one installation on 2026-10-11, to follow the acceptance of ADR-021 L18. It replaces 0.27.0 on the daily board and carries what was accepted into `socu/integration-0.26` since then: ADR-021 logic stages [one](adr021-logic-stage1.md) and [two](adr021-logic-stage2-host-review.md), the harness run module of ADR-025 with [DSH native resume](dsh-native-resume-host-review.md), the catalog rules of [ADR-027](adr027-host-review.md), the [console reads and managed checkouts](console-reads-and-checkouts-host-review.md) batch, the [console interface and board reliability](console-ui-and-board-fixes-host-review.md) batch, [C-Two 0.7.4](c-two-074-host-review.md), [two runtime fixes](routing-and-claude-fixes-host-review.md) and the [schema 15 to 16 upgrade](adr021-l18-host-review.md). The new console frontend of ADR-029 is on its own branch and is not part of it.

## Integration

The package was built from `b6b5489e` on `socu/integration-0.26`, which is the L18 merge `02589e06` plus records. On the merged tree the complete check passed once with 3,752 Python tests (one skipped) in 224 files, and an empty-HOME rehearsal installed 0.27.0, started its service and upgraded it to schema 16. The rehearsal on a copy of the daily board had passed on the previous L18 candidate; the changes made after it do not touch the upgrade. The frontend suite was not run, because nothing under `apps/console` changed.

## Result

The skill was built with `packaging/build-skill.py` and installed outside the Host sandbox with that copy's own launcher, on the first attempt, in about four and a half minutes. The skill placement was reported `updated`, `skill.json` records `sourceCommit` `b6b5489e2ac506974ea49741d796a9202502152b`, and `~/.claude/skills/buddy` stays linked. The upgrade took a verified schema-15 backup (143,291,148 bytes, 12,321 files, 60.7 seconds, zero skipped attempt entries), converted the board to schema 16, verified the retained data and switched the runtime from `7aadcb4af814adbde51eaa088e8c7ae1` to `b2831d269b0beca091ceb01b348bd610` without a rollback. The conversion added the tables `catalog_pending`, `model_facts`, `router_claims`, `router_dispatches` and `router_outcomes`, and turned the two Router positions into the list `dsh:deepseek-official:deepseek-flash:off`, `codex:openai:gpt-6-luna:high`.

After installation `health` reports status `ok`, contract 0.29.0, schema 16, a stable runtime, integrity `ok`, no foreign-key violations, seven Workers and no active work; `adapters` reports the four harnesses available. Read from the board file without changing it: 55 tables, 933 tasks, 1,284 attempts, 461 runs and 55 macro tasks; the 48 uncertain account operations are kept as they were. The new service process held 44 file descriptors after start; the 0.27.0 process it replaced held 210 after about 36 hours.

## Not established

- No micro task had run on the new runtime when this was written; the first ones were submitted right after.
- Whether C-Two 0.7.4 ends the descriptor growth of the daily service takes a day or two of running to show.
- The model catalog still lists the provider entry ZCode offered earlier. The user's ZCode now offers two other entries, which a catalog read brings in; enabling their buddies is the user's setting.
- The 48 uncertain account operations still have no way to be concluded; the backlog records what becomes of them.
- The items the [0.27.0 record](installed-0.27.0.md) left open were not rechecked, the installed console was not inspected in a browser, and Windows remains unverified.
