# Installed 0.16.0

The user authorized immediate installation after verification on 2026-09-28. The verified package from source commit `7af8b60a7a59c63b3aef320b96f00289bc5a4bca` was staged into the existing personal marketplace at `/Users/soku/plugins/hey-my-buddy`, installed as `hey-my-buddy@personal` 0.16.0, and its new bundled launcher ran `buddy upgrade`. The upgrade returned `upgraded: true`; no manual state archive or database replacement was performed.

## Installed identity

Daily service `1cbc05f0-c6ad-4976-a2c2-9ef149d93f84` runs PID 93275 with contract 0.16.0, schema 12 and runtime `88cfd4423dd42ac7a6f94e70f77007f5`. The former runtime `543fa60da3a0c9d067039a24e0e5195e` remains available for rollback. Maximum concurrency remains 7, wait capacity remains 32 and model-table revision remains 27. Retained database fingerprints and the retained event prefix passed verification, with no source-path leaks.

The final source checks passed 1,185 Python tests and 233 Node 24 tests. Frontend checks passed 465 tests, followed by 58 affected tests for the final inspector correction; TypeScript/build and real browser geometry passed. The exact staged package survived source directory replacement and its private test root was removed. [Source acceptance](maintenance-and-console-0.16.0.md) records the intermediate failures, repairs, Opus implementation, Sonnet review and T1–T4 screenshot links.

## Backup and storage

The real upgrade created and verified the single rolling backup at `/Users/soku/.local/share/hey-my-buddy/state/backups/current`: 143,750,497 bytes, 2,894 payload files, 8.549 seconds. Integrity, foreign keys, hashes and private opening were verified by the shared backup implementation. Exactly one completed backup directory (`current`) remains.

The upgrade removed 19 proven unused old runtimes totaling 1,007,964,885 bytes. Current and previous runtimes are retained; eight older directories with unproven runtime identity remain protected. Post-install read-only inventory reports ZCode homes 3,826,799,234 bytes, managed checkouts 3,551,805,352 bytes, runtime directories 413,976,307 bytes, backup 143,750,497 bytes and durable records 225,359,644 bytes. Three accepted ZCode homes totaling 138,195,356 bytes are eligible after grace; no native-home/workspace storage apply was requested or executed.

All 100 identified orphan test processes were subsequently confirmed exited under the user's stop authorization. Final inventory found zero orphan Buddy processes. This includes three older launcher-test supervisors found in the final snapshot; their start time preceded the final complete test. Historical test directories/records were preserved. The staging helper's temporary previous-plugin source was removed after verification; it had matched the installed 0.15.1 cache before Codex updated that cache, and the verified previous stable runtime remains.

After the user separately replied “删除,” Host reverified the current backup with the shared manifest, hash, SQLite integrity, foreign-key and private-open checks, and confirmed the daily service still ran contract 0.16.0/schema 12 on runtime `88cfd4423dd42ac7a6f94e70f77007f5`. Host then removed only `/Users/soku/.local/share/hey-my-buddy/archive-before-readonly-0.15.1-20260927-195555` (7,482,405,564 logical payload bytes) and confirmed it was absent while `backups/current` remained present. The user's Trash was untouched. Exact evidence is `.dsh-skill-build/016/archive-deletion-report.json`.

## Console

The stable URL is [http://127.0.0.1:49637/](http://127.0.0.1:49637/). A real login verified authenticated write access, HttpOnly/SameSite=Strict cookies and exact installed JS/CSS asset bytes (`index-D9TS73UZ.js`, `index-CuzDh9XE.css`). The normal CLI login flow opened the user's default browser. Persistent sessions, daemon restart and independent-window conflicts were verified in the private package/browser probes. The console follows daemon lifetime; first login uses the CLI ticket, then the root URL can be bookmarked.

Raw evidence is in the ignored `.dsh-skill-build/016/` directory: `daily-upgrade.json`, `installed-verification.json`, `installed-health.json`, `installed-runtime.json`, `installed-capabilities.json`, `daily-storage-final-readonly.json`, `final-checks-release.log`, `final-package-report.json` and the process-shutdown reports. Quota/request accounting is sampled separately in `quota-accounting.json`; logged account quota is not isolated task spend.
