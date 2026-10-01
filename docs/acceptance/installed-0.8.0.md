# Daily installation — 0.8.0

The user authorized installation on 2026-09-25. The daily service now runs contract 0.8.0, schema 10 and stable runtime `2447dced7df411518fde84f302c98f2f`. The installed plugin is `~/.codex/plugins/cache/personal/hey-my-buddy/0.8.0`; its launcher is `bin/buddy`. The current Codex installer selected the portable manifest version `0.8.0` for the cache directory; the compatibility manifest also records `0.8.0+codex.20260925021920`. Existing tasks must resolve the current installed launcher instead of retaining a removed 0.7 cache path. The [source acceptance record](production-repairs-0.8.0.md) remains the earlier candidate verification record.

## Cutover and retained state

Installation confirmed zero active attempts, queued tasks or unfinished governed goals before detaching the 0.7 daemon. Startup exclusion and daemon/worker lifetime locks covered the switch. Seven idle managed supervisors stopped cooperatively; no other Host's active work was cancelled. The old content-addressed runtime remains available. The previous deployment limits remain six business attempts plus one decision attempt; the proposed model-specific shared concurrency settings are not implemented by this installation.

The complete durable-state backup and offline-copy report are under `~/.local/share/hey-my-buddy/archive-before-0.8.0-20260925-102805`. The copy retained all 36 previous tables' old-column fingerprints, 53 task records, existing settings, preferences and card text, and added 38 scope-version bindings from retained manifests. Integrity and foreign-key checks passed. The original physical database, an independent schema-9 backup, the verified schema-10 copy and a full state-directory backup remain available. Ephemeral sockets/FIFOs and checkpointed root WAL/SHM coordination files are excluded from the directory copy; the durable database is independently backed up and checked.

An initial backup attempt encountered a WAL file disappearing after SQLite closed it. That attempt did not activate the new database and restored the old daemon. The corrected retry excluded those checkpointed coordination files and completed. The earlier `archive-before-0.8.0-20260925-102629` directory is a retained incomplete backup attempt, not the installation's recovery archive.

Installed verification confirmed stable package/resource paths, no source leaks, the six-plus-one limits, retained task identities and unchanged durable user preferences/enablement. A Host attempt to acquire a human editing grant returned `FORBIDDEN`. Both served JavaScript/CSS assets matched the installed package. Native metadata discovery completed for DSH, ZCode and Codex without a model call. Newly discovered profiles remain disabled; unavailable profile history and its user preferences remain retained even though the default console snapshot omits them.

## Actual unrestricted routed task

The installed service processed one small text-file task with no adapter/provider/model/effort constraint and no task-local routing preference. The decision read shared table revision 7 and selected `zcode:zai-api:GLM-5.3-Flash:max`, explicitly citing the user's preference for Flash on simple, bounded work. The actual ZCode task completed; the Host verified the sole changed file's exact committed bytes `buddy-0.8-installed\n`, integrated the immutable output into the disposable fixture target, recorded integration, accepted the final artifact and removed only its managed checkout. Output patches and fixed commits remain readable. This verifies one current decision and native execution, not an assurance that all future tasks will choose the same model or that the remote served identity was independently attested.

| Binding | Identity |
| --- | --- |
| Goal | `731f8aa0-ad76-476e-b305-837351f09897` |
| Decision | `dec-186cf3e3-57f2-4e82-ae92-c59c8aea93ff` |
| Final artifact | `0a3db274-8a14-4455-98ea-36178c116c46` |
| Output commit | `55064f7aa139aa3ada76d58b9b8c9834869276e1` |
| Integration | `int-d9efb778-bff2-4b09-a0e7-75bb185345e8` |

## Outstanding retained-artifact boundary

Three already-delivered C-Two/FastDB goals created on 0.7 remain readable but cannot yet register their real target integration through 0.8. Their immutable output snapshots lack the per-path `changedEntries` binding required by `workspace.integration_verify`, which returns `WORKSPACE_UNSUPPORTED: The artifact carries no bounded per-path output binding to verify`. Preserving database rows did not make those older artifacts acceptable to the new verifier. This is an installation/migration gap, distinct from execution timeout and from the successfully verified new 0.8 task.

Their original receipts, fixed artifacts and worktrees remain intact. No false `notRequired` integration, forged acknowledgement, old-client bypass or manual database repair was performed. Remedying this boundary needs an explicit retained-artifact conversion/verification design that preserves original evidence and the current-contract-only runtime policy. It must not trigger a paid model turn merely to manufacture a new seal or silently weaken integration verification.

Raw installation and check outputs remain in ignored `tmp/`: `install-0.8.0-retry.log`, `installed-0.8-verification.json`, `installed08-smoke-submit.json`, `installed08-smoke-accepted.json` and `install08-catalog.json`. No Computer Use was used. An external ZCode review appended concurrently to the source acceptance page was left unmodified and excluded from this installation record.
