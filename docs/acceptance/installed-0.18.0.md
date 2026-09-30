# Installed 0.18.0

The user authorized installation on 2026-09-28 after the [0.18.0 source acceptance](buddy-settings-schema13-0.18.0.md). The skill was built from `cc1da88` on `socu/buddy-core` with `packaging/build-skill.py` into a private staging directory and installed with that build's own `scripts/buddy install`. The Claude Code Host ran the install outside its sandbox with node 24's `bin` directory first on `PATH`, so the upgraded service inherits a `PATH` whose `codex` (codex-cli 0.157.0) passes the Codex adapter's admission; this is an interim measure until the harness discovery proposed in [ADR-017](../decisions/017-local-installation-and-harness-discovery.md) exists.

## Result

`install` placed the skill at `~/.agents/skills/buddy` (version 0.18.0, contract 0.18.0) and linked `~/.claude/skills/buddy` to it. Its service step ran the installed skill's `upgrade`, which returned `upgraded: true` with no rollback. The single rolling backup at `~/.local/share/hey-my-buddy/state/backups/current` was created and verified at schema 12 (149,818,481 bytes, 3,027 files, 8.992 seconds). The idle schema-12 board was then migrated in place to schema 13 under the exclusive owner locks, retained data fingerprints were verified with no source leaks, and the service switched from runtime `35fa45f8bc541080d595b2b53146c147` to `4b5fb1640f147e734030f99e44326c26`. Runtime pruning removed nothing.

After installation `health` reports status `ok`, contract 0.18.0, schema 13, a stable runtime, integrity `ok` and no foreign-key violations. The console snapshot carries `familyPreferences`, `preferenceOverrides`, the effective `preferences` and `familyAnnotations`, and no longer carries `annotations`; the migration produced no family default, one per-effort override and six family notes.

`codex plugin remove hey-my-buddy@personal` (Codex CLI 0.157.0) removed the 0.17.0 plugin and its cache, so Codex and Claude Code now read the same skill. The personal marketplace at `/Users/soku/plugins/hey-my-buddy` still lists the plugin as not installed and was left in place.

Two read-only smoke delegations ran on the installed 0.18.0 service from the frozen input `cc1da88`: DeepSeek Flash off (run `8fe07eef`) and Codex GPT-6 Sol high (run `20bd6150`). Both were admitted, delivered with confirmed shutdown and reported `git rev-parse --short HEAD` = `cc1da88` and zero `git status` entries, which the Host checked against the frozen input. Both were recorded as integration not required and accepted; the Host read the reported results after recording the acceptance rather than before, and the results matched. The Codex admission that failed three times earlier in the day passed because of the `PATH` noted above.

## Not established

- No adapter has a verified read-only structured capability, so no Router model is available. The configured Router `dsh:deepseek-flash:off` cannot become one because DSH does not confine reads or networking; the routing health window shows 5 failures in 20 samples. Default-routed delegations stop at the Host boundary; use an explicit enabled configuration. The Router probes in the [0.17.0 source record](router-read-only-routing-0.17.0.md) still need per-run approval.
- Harness executables are still found through the environment the service was started from.
- The governed run `0714b542` (c-two, created 2026-09-26) still awaits its Host and was not touched.
- The managed checkouts of four unaccepted runs remain, as listed in the source acceptance record.

Start a new Codex or Claude Code session to load the 0.18.0 skill.
