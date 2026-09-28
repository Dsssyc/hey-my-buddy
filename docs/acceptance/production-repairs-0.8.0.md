# Production workflow repairs — 0.8.0

This is the source acceptance record for [ADR-010](../decisions/010-production-workflow-repair-plan.md), verified on 2026-09-25. It records the combined regression run, its failed cases and subsequent repairs, focused reruns and real private-board harness exercises. It is not an installed-runtime acceptance: the shared daily service remains contract 0.7.0/schema 9, and activating the 0.8 candidate still requires the user's notice.

## Ownership and delivery

The integration checkout is `socu/buddy-core`. The original dirty `socu/python-blackboard` checkout and the separate CodeBuddy branch were not integration targets. Bounded implementation used independently allocated Buddy worktrees; Host reviewed immutable output commits and made the cross-module corrections before recording acceptance. An original failed scope receipt remains failed even when the Host later adopts its inspected output.

| Implementation goal | Final fixed output | Integration in `socu/buddy-core` |
| --- | --- | --- |
| Recovery `ec00a228-20f7-4dd5-a48f-285ee680fbc6` | `2e3567a84f7aa7f98f9f8b025c85f8c24f36dffa` | `4db88de`, then Host hardening |
| Workspace lifecycle `782ee715-0e00-4393-9805-c356e0e4c0ea` | `aa3f65d9b90b682d5d623ddcc1894cbb570b6ed3` | `7552955`, then cleanup corrections |
| Console `4f489fe9-754c-435f-a224-a6b085c00c7d` | `639165a5133d359e44833af011eadca5cde57467` | `dee2fbb`, then receipt fencing and final build |
| Native adapters `4e8a4138-b12f-4782-9d3b-4e2e0460a04e` | `367c57b6c93f940fc17f7b6a02c4cdca9824c751` | `bd65233`, then Host credential/version/metadata corrections |
| Documentation `e8dfec7a-bd29-4726-967a-9c579a8833d9` | `b9234557b7845c0fb88a36a3c51cf4f3e59880a6` | `c895d56`, then verified contract and acceptance updates |
| Host-adopted RPC output from failed goal `7ac89b7e-ef25-49c8-819d-e979fa44f203` | `2c8f2da36beed09ff8d88c41989afef199166843` | `5af8fc5`; original failure retained |

The daily board's capacity was changed, at the user's request, to six business attempts plus one reserved decision attempt. The switch preserved worker-owned children and their original attempt identities. A later `health` read confirmed `business.limit=6`, `decision.limit=1`, all seven managed supervisors present and clean database integrity. Source defaults remain two plus one. This deployment setting must be retained when the candidate is eventually installed.

## Verified source boundaries

| Area | Verified evidence |
| --- | --- |
| User policy and model assessments | An ordinary Host cannot claim or borrow a human grant. User annotations, preferences, enablement and configuration use authenticated field patches; maintenance publishes only assessment cards. The six user-policy tests passed. |
| Catalog and retained history | Ordered complete/unknown observations preserve user intent, returning identities and cross-project evidence; retired profiles are paged and searched before pagination. The five catalog-observation tests passed, including literal wildcard search. A complete empty Codex catalog is a valid observation, tested without a model turn. |
| Routing | Task-local preferences do not publish global settings. Selection input preserves the assessment's recorded origin. Focused routing/decision checks and the actual Worker selection-input test passed. |
| Recovery and activity | The same live Worker explicitly reconciles its original attempt after restart; another instance or a lost child handle cannot infer ownership from a PID. Durable completion is replayed. Activity is bound, bounded and monotone; a progress message cannot clear uncertainty. Real private-daemon restart and activity/recovery tests passed. |
| Scope recovery | Eighteen real-Git scope tests passed after Host-resolution delivery was added. Only a scope-seal failure with an intact native outcome can produce a resolved delivery; the original failed attempt and receipt remain unchanged. |
| Integration and cleanup | Real-Git lifecycle and four public CLI/HTTP/C-Two tests passed. Root control authorizes lifecycle operations on owned helpers using `targetRunId`; physical allocation survives logical continuation identities. Three Host regression cases cover cleanup recovery after expiry and a missing integration record. The real assistance exercise integrated, accepted and removed both the helper and the continued parent's managed checkouts. |
| Console | The final frontend passed 192 tests in 17 files, serially under heavy machine load, and the TypeScript/Vite production build passed. Checks cover publication conflicts, stale history pages, unavailable configurations, native-session evidence, execution capabilities and final-artifact integration prerequisites. No Computer Use or browser automation was used. |
| Service stop | Three focused governed-stop tests passed after two new cases first reproduced the defect. Internal cancellation now fences active governed work before draining, preserves passive delivered/awaiting-Host goals, and waits for real shutdown evidence. Public Host and Worker authorization is unchanged. |
| Inquiry privacy | Eighteen DSH bridge tests and a real service/Unix-socket observation regression passed after removing tool argument previews. Progress observations retain tool names and timestamps, not raw arguments. |
| RPC and distribution | Ten focused RPC checks and nine first-install checks passed. Tests exercised an 8 MiB message, concurrent calls, chunk fallback and control operations during long waits. The repository supplies its own local/Git marketplace; no public registry registration is claimed. |

## Native Codex execution

A real private-board task used the installed Codex App Server with the native account-plan login, model `gpt-6-luna` and effort `low`. The task changed one file to the exact bytes `native-codex-passed\n`; Host verified the committed blob, integrated its immutable output into the fixture target, recorded integration, accepted the artifact and removed only its managed checkout. The output patch and fixed commit remained readable, and all private supervisors stopped afterward.

The first live probe exposed a genuine completion-interface mismatch: the common context still requested a DSH finish tool, and the native output schema allowed a completed result with a non-null request. The common hint now names the harness-provided completion interface, while Codex uses a root object containing a nested discriminated outcome. A completed result requires `request:null`. Fixture validation and the second real probe passed. No failed first probe was rewritten as successful.

| Binding | Verified identity |
| --- | --- |
| Private state root | `/var/folders/8s/_8wkkrz159qft4v82tbn7dzc0000gn/T/buddy-codex-acceptance-b4n071g9` |
| Goal | `c7ecf54f-83e3-4385-9e84-2e9d9a6d77d1` |
| Native thread | `01a0d532-0fc1-7e63-ab2e-ce1effa673d0` |
| Final artifact | `63099e72-65d0-4994-9f9e-f92762a2a4b9` |
| Output commit | `218ab543f8b7de94175393a4cb98ac48b0e36dc2` |
| Integration record | `int-ba2458f0-8f02-4e3e-bd75-3f52e5288e79` |

The adapter does not claim a correlated inquiry bridge or verified tool-free decision execution. Native App indexing visibility is unknown; the result records the native session identity and storage ownership without inventing an App-open link. The served remote model remains unobserved, distinct from the requested and native-readback configuration.

## Native DSH → ZCode → DSH assistance

The private fixture at `/var/folders/8s/_8wkkrz159qft4v82tbn7dzc0000gn/T/buddy-assistance-08-q1zf6s9_` exercised real DSH `deepseek-flash/max` implementation, a structured assistance request, Host-authorized ZCode `zai-api/GLM-5.3-Flash/max` review of the fixed implementation, and DSH continuation with that exact reviewed artifact. The reviewer found no required code correction; the continued parent incorporated the review and reran validation. This proves the requested assistance/continuation protocol for an explicit review condition, not that a model will spontaneously recognize every task it cannot handle.

During an observed native ZCode Bash tool execution, the verifier restarted the daemon. The same reviewer attempt `9f07fed8-fac5-4c36-ac68-8566d88dde60`, generation `1`, completed afterward; no replacement attempt was dispatched. Native session `sess_836a4c66-9e8d-41e5-93db-85d3f4c2de1e`, bounded tool activity and the durable inquiry state were checked. A question honestly returned `unavailable` without injecting native input. The installed ZCode App 3.14.3 / bundled CLI 0.16.9 input protocol has no safe expected-turn binding: guide input can queue or start another turn after settlement, so this release declares observation only and mounts no inquiry reply tool.

Host inspected the fixed implementation and review, ran eight unit tests plus 1,919 independent integer clamping cases and invalid-size checks, integrated the exact outputs into the fixture target and accepted the named final artifacts. Helper operations used the root's saved control with `targetRunId`; the response names the target while the next operation re-reads the controlling root revision. The cleanup API removed only the reviewer's managed checkout and the parent's original physical allocation across two logical turns. Their immutable output patches and commits remain readable. The private service and supervisors stopped afterward.

| Binding | Verified identity |
| --- | --- |
| Parent goal | `74a4fba5-5836-4886-8d6c-0a8f460919c5` |
| Initial assistance artifact / output | `61613945-d8ce-4eec-970c-75f03ab07a0d` / `1dc020e73ff0cac20fcd7e0a33d52ce9352d8aee` |
| Reviewed helper goal | `619bf689-c980-4c63-b23c-d06c470dac87` |
| Helper artifact / output | `be806126-d9e2-49fc-8607-93d069c38f91` / `06e39600062e58bc0098b3c8386ae35aa4c3d240` |
| Helper integration | `int-a46fb465-f5d9-4f1a-8fda-be3bed3399f1` |
| Final parent artifact / output | `53a57fef-710d-45c4-85cf-bf78ea2f01e0` / `e62313324835deb4644b5936fe1a1f831979f507` |
| Final parent integration | `int-17f24f30-099d-4650-ad4d-cfbc831a25a1` |

The failed probes remain part of the evidence. The first DSH session-isolation attempt moved `DSH_HOME` and failed before inference with `MISSING_CREDENTIAL`; the corrected adapter changes only the supported session-persistence root, leaving the native credential store in place. Nine storage tests and the real DSH runs passed. An earlier reviewer `aba205aa-d4ae-4f34-bb7a-f765026e6671` was cancelled by a verification-driver mistake (`result` was requested before completion and the driver then stopped its private service). Its original cancellation was retained; Host explicitly authorized the replacement reviewer on the same parent and fixed input. A slow optional ZCode version probe was also made nonfatal: unknown version metadata no longer prevents the actual native catalog/execute handshake. None of these earlier outcomes was rewritten as success.

The probe preceded a metadata-label correction from ZCode `buddy-attempt` to `buddy-goal`; the private native store was already goal-owned. Focused metadata tests verify the corrected label. Native execution evidence is not relabelled retrospectively.

## Regression and build record

The combined `BUDDY_NODE=<Node 24 executable> uv run --frozen python -m buddy.checks` run executed 726 Python tests in 3,050.760 seconds and reported four failures plus six errors. Four stale continuation fixtures lacked the now-required reason for an explicit configuration; one concise-help fixture exceeded its line budget; five installed ZCode cases encountered optional version-probe timeouts or a fixture wait shorter than the requested native turn. Those causes were corrected. All 13 affected or added focused cases then passed in 180.207 seconds. Additional focused checks covered the three governed-stop regressions, three cleanup regressions, DSH session storage and corrected native metadata. A new complete 726-test pass is not claimed.

The combined runner stopped before its Node phase, so that phase was invoked separately with `BUDDY_PYTHON` pointing to the repository's uv environment: all 195 DSH tests passed, zero skipped. The final console command `npm test -- --run --maxWorkers=1` passed all 192 tests in 17 files; prior parallel attempts on the heavily loaded machine had timing failures, and one stale integration-command text assertion was corrected. `npm run build` passed typechecking and produced `index-DcOscJGF.js` and `index-DIOOkyyt.css`. The full Python run also passed the staged-worker test that replaces its source installation while the content-addressed Worker runtime continues from stable paths.

Raw local evidence is retained in ignored `tmp/`: `checks-0.8-final.log`, `repairs-0.8-rerun.log`, `node-0.8-final.log`, `console-0.8-serial.log`, `console-0.8-build.log` and the private fixture's `verification.json`. These logs are not shipped with the plugin and contain no claim that failed cases passed before repair.

## Preserved-data rehearsal

The offline copy script reads the daily schema-9 database through SQLite's read-only mode and backs it up before transforming a separate copy. Verification preserved row fingerprints for all 36 pre-existing tables and every old column except the schema marker, populated 31 scope-version records from each root/helper run's hash-verified retained manifest and passed integrity/foreign-key checks. Synthetic helper and old-card cases also passed. Old cards retain their text with `origin:unattributed`; no author or shutdown proof is fabricated.

The rehearsal archive is `/private/tmp/buddy-schema10-daily-validation-20260925-b/board-schema9.sqlite3`; the independently checked candidate is its sibling `board-schema10.sqlite3`. There were four active tasks at snapshot time, so the report explicitly marked the copy ineligible for activation. It is inspection evidence only. Installation needs a fresh idle-state check, fresh complete backup and a new copy checked against the final schema.

## Candidate and installation

The staged plugin is `tmp/release-0.8.0-20260925/hey-my-buddy`. Its isolated cold start at `/var/folders/8s/_8wkkrz159qft4v82tbn7dzc0000gn/T/buddy-candidate-0.8.0-zajc4fph` reported plugin/contract 0.8.0, schema 10, stable runtime `2447dced7df411518fde84f302c98f2f` and the packaged default capacity of two business plus one decision attempt. Both served frontend assets matched the staged bytes. Empty maintenance preparation replayed identically, history remained empty and no model task was created. All private supervisors were stopped after the check. The current documentation check passed 38 Markdown files, 243 local links, 56 JSON examples and 40 shell blocks, and the skill-format validator passed.

Five implementation goals were accepted against their final immutable artifacts after Host integration and the checks recorded above. The R7 goal's original scope-failed attempt remains failed with no acceptance; Host adoption is retained separately as `refs/buddy/host-recovery/adr010-rpc-distribution` (`2c8f2da36beed09ff8d88c41989afef199166843`), integrated in `5af8fc5`. Host closed that already-stopped goal solely to release its obsolete reservation, without rewriting the original execution receipt or starting another attempt.

Eight task-owned implementation checkouts were then removed: the six Buddy allocations above and the two independently integrated Codex-adapter/service-stop checkouts. Before each removal, Host checked immutable output/patch hashes, actual checkout content, Git registration and allocation lock, confirmed attempt shutdown, released reservations, no descendants or live cwd users and the integration commit on `socu/buddy-core`. The procedure used read-only daily SQLite inspection and exact-path Git removal; no schema-10 daemon was pointed at the schema-9 board and no cleanup/integration rows were fabricated. Each checkout was first archived under `tmp/checkout-archive-20260925/` and every archived file was checked against its source hash. Archives retain local source and logs, excluding only rebuildable virtual environments, `node_modules`, Python bytecode and pytest caches. Fixed refs, output patches, manifests and execution receipts remain outside the removed checkouts. Native implementation commits have retained refs under `refs/buddy/host-recovery/`. `tmp/checkout-cleanup-applied.jsonl` records the eight archive hashes and exact removals. The original checkout, CodeBuddy branch, integration checkout and other historical worktrees were preserved.

Daily installation remains pending the user's notice. At installation, recheck all Hosts' live work, preserve the six-plus-one deployment capacity, make a fresh recoverable backup and prepare a verified current-schema copy before switching clients, daemon and idle Workers together. The earlier active-state rehearsal must never be activated.
