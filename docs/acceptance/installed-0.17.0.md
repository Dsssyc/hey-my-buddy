# Installed 0.17.0

The user authorized installation on 2026-09-28 after the independent review. The package from source commit `2d63405` on `socu/router-buddy-0.17-review` was staged into the personal marketplace at `/Users/soku/plugins/hey-my-buddy`, installed with the ChatGPT app's bundled Codex CLI (`codex-cli 0.158.0-alpha.2.1`) as `hey-my-buddy@personal` 0.17.0, and its bundled launcher ran `buddy upgrade`. The daily service was idle beforehand (no active attempts). No manual state archive, database replacement, stop or task cancellation was performed.

## Result

`upgrade` returned `upgraded: true` with no rollback. It created and verified the single rolling backup at `/Users/soku/.local/share/hey-my-buddy/state/backups/current` (145,062,301 bytes, 2,916 files, schema 12, 8.047 seconds), verified retained data fingerprints with no source leaks, and switched to runtime `35fa45f8bc541080d595b2b53146c147`. The previous runtime `88cfd4423dd42ac7a6f94e70f77007f5` remains the rollback target; the older unused runtime `543fa60da3a0c9d067039a24e0e5195e` (37,704,313 bytes) was pruned by the upgrade.

After installation `health` reports contract 0.17.0, schema 12, integrity `ok` with no foreign-key violations and no active attempts. `capabilities` reports Codex and Claude read-only structured calls as implemented but unverified, and DSH and ZCode as not implemented. No Router model is therefore eligible: a delegation without a complete configuration opens a Host routing boundary until a separately approved native probe verifies one adapter. The historical routing-health window still contains the 0.16 DSH selections.

The Claude Code side has no hey-my-buddy plugin installation; only the Codex plugin cache was updated. Start a new Codex task to load the 0.17.0 skill and launcher.
