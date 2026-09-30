# Neutral core and installed Buddy 0.6 acceptance

Recorded on 2026-09-24 against contract `0.6.0`, schema `8` and runtime `3ce39af599a01f3299a4f916dcd9409c`. This record describes checks and artifacts actually observed on macOS; operational behavior belongs in the [current references](../README.md#current-references).

## Source and branch ownership

The integration checkout is `/Users/soku/.codex/worktrees/buddy-core/hey-my-buddy`, branch `socu/buddy-core`, based on released 0.5 commit `0b674e91b467e2d688aa8d13196a507b85ca9c9e`. The current-contract/routing implementation was imported from `socu/buddy-current-core` through `0679e3fb189f3e450d21824667142e1e742824ac`; the ZCode implementation was imported from `socu/buddy-zcode` through `31f9d19a43097b4504e877e1218c9541b7ac0f4c`. Only the bounded deltas were assembled, followed by Host integration corrections. No wholesale branch merge replaced the integration tree.

The original checkout on `socu/python-blackboard`, including the user's documentation and `.workbuddy/` changes, was preserved. The user-excluded `socu/buddy-codebuddy` branch and its worktree were not inspected for implementation, modified or merged. This delivery is a local integration branch and plugin installation, not a GitHub publication.

DSH supplied the runtime/package, CLI and documentation slices in isolated worktrees. Runs `82139eb0-5718-4096-9165-be73de9170cd`, `ef4b50bb-6fac-4933-acea-99ac529b3082` and `20392d21-06c3-4527-b232-58f736aaeb95` were reviewed against their sealed outputs and acknowledged; all reported confirmed shutdown. The temporary documentation supervisor was cooperatively stopped and its durable status was checked.

## Automated and browser checks

- `BUDDY_NODE=<Node 24.15.0> uv run --frozen python -m buddy.checks`: 483 Python tests passed in 451.510 seconds, followed by 201 Node tests with zero failures. Raw output is retained locally in `tmp/checks-0.6-delivery.log`.
- After the last removal of the silently ignored `get.taskId` argument, the six current-core tests passed. Fresh schema creation, old-schema refusal without changing archive bytes or permissions, removed public surfaces and opaque current receipts are covered.
- React/Vite: 25 frontend tests, type checking and the production build passed with Node 24.15.0. Built assets are shipped under `src/buddy/console_assets/`; the distributable excludes frontend source, tests and dependency environments.
- Packaging, runtime and protocol tests cover concurrent cold installation, required resources, external symlink refusal, actual stable import paths, source replacement, owner fencing, helper/workspace conflicts, late routing results, cancellation, replay and malformed turn evidence. ZCode fixtures use the installed CLI against local protocol fixtures without external model traffic.
- The built console was exercised in the real in-app browser: empty state, missing-selector attention, discovered configurations, rerouting the same goal, completed/failed helper distinctions and final artifact acceptance. No browser errors or warnings were reported. Widths 320, 768, 1024 and 1440 had no horizontal overflow; temporary viewport overrides were reset.
- Both the source and staged distribution passed Markdown relative-link/anchor checks, JSON and shell-example syntax checks, skill validation, plugin validation and `git diff --check`.

## Real DSH and ZCode collaboration

The private acceptance repository and board are under `/private/tmp/buddy-acceptance-0.6.n24A0g`. The original source commit is `ab21a0ba120a4616f4b13bc990991bdaef08ece8`. The parent goal `e4cc40fd-a881-4b8e-be95-7feec7c6073c` was admitted before a selector was configured and returned a durable routing attention boundary with zero execution turns. After publishing three actually discovered profiles, the real console rerouted the same goal. Decision `dec-ca209dd9-2f5a-4626-a871-67d6f31f87c2` used Flash/off and selected Flash/max under the user preference at table/configuration revision 1.

The DSH parent ended its first turn with an assistance request. The Host approved ZCode helper `bf924767-1d6b-4de7-ad55-8e0c8a0ea44b` on `bigmodel-api / GLM-5.3 / high`, in a separate checkout with write scope limited to `test_math_ops.py`. Native CLI version was `0.16.9`.

The first helper attempt failed because its finish-tool arguments omitted required `request`; the adapter originally aborted before the native model could correct that explicitly failed call. The fix permits correction of a failed call within the same root turn while preserving the unique successful signed receipt, root/turn identity, protocol ordering and shutdown gates. The original failed attempt `d71628d8-5117-4bc2-a2b3-43e6dbe2e384` remains failed and was not rewritten as success.

The parent received the helper failure and explicitly described recovering its unsealed test file; it checked the recorded write and exact bytes, implemented `clamp`, and delivered commit `a59336a57f0ceb91f9a9fc00f7a27e8d4f581917`. The Host inspected both changed files and independently ran all 30 tests successfully. The test file SHA-256 is `730b7951cbe043044e06aae52a8b205510d6d7a4e98174efd4639a0a5edaf59a`.

Explicit continuation of the failed helper exposed a second bug: its released workspace reservation had not been reclaimed, so preparation could not progress. The corrected path reacquires only the same allocated checkout after confirmed stop, rejects competing ownership, supersedes older unconsumed continuation intents and pins scope-limited partial files as new input. It does not fabricate a seal for the failed attempt. Regression tests cover reader/writer conflicts, lost ownership during preparation, a superseding manual input and an unvalidated seal in a failed report.

The helper then completed attempt `165b0c83-01c7-4dd3-8b72-15954c91e67c` with `reconstructed-new-session` and sealed commit `a805a2a96fe87ace41ef40b1a0baf5244650b5a0`. A further read-only continuation completed attempt `d607cc58-94a8-468c-b121-021bbe92c7c1` in `native-session` mode, reusing exact native session `sess_9417ed13-99c6-4573-8c02-c9b879873391` with the same goal, checkout and configuration. Its test bytes matched the parent's integrated file. Native model settings were read back; actual served-model identity remained honestly `observed: null`.

The Host accepted helper artifact `7bd759eb-24d9-4587-8960-974b6f072675` through the real console and parent artifact `c067b02d-9fa8-46d2-8c48-eeec4873ec55` through its saved control file. Both goals reported accepted and self/descendant shutdown confirmed. The private acceptance daemon and its local supervisor were stopped; no model job was left running.

## Installed routed artifact

The plugin was installed through the existing `personal` marketplace and cold-started from the installed cache. Health reported contract `0.6.0`, schema `8`, integrity `ok` and stable runtime `3ce39af599a01f3299a4f916dcd9409c`. The actual interpreter, Python package, DSH runner/catalog/decision helper, YAML bridge and console assets all resolved inside that content-addressed runtime, with no source leaks or missing resources.

The new daily board was observed at revision 0 with zero tasks, profiles, cards, preferences and evidence before bootstrap. Discovery advertised 22 legal native model/effort profiles. The approved bootstrap enabled DSH Flash/off as the fixed selector, DSH Flash/max with the user's soft preference, and ZCode GLM-5.3/high. Revision 1 contained no invented assessment cards or historical samples; automatic maintenance remained off.

Installed goal `f73773da-e6a6-4dd4-bd5e-10ca61e2f93d` omitted the execution model and used an explicit isolated worktree. Decision `dec-b7d83746-a2cc-4fa0-81ed-866ff7c2991e` selected Flash/max. Execution attempt `c433674b-3b15-4655-acc5-cd528448bd6f` ran from the stable runtime, changed only `labels.py`, and sealed commit `b8136c4ff9a9718cb616ea9169f894dfd32a390a`. The Host inspected the exact diff and independently ran all four tests for trimming, counting, Unicode casefold, blank labels, type rejection and unchanged inputs.

The source repository under `/private/tmp/buddy-installed-0.6.SIJMdh` retained clean commit `b585a543070cbe2b36fb18abead8e91378ae3291`. DSH workspace grouping returned `enabled: true` and `bound: true`; its dedicated profile entry now points to the stable runtime's bridge. Final artifact `abb19aff-4353-4347-8e65-d6bf12ef6e7e` was acknowledged accepted, with self and descendant shutdown confirmed.

## Daily-board archive and recoverability

The former schema-7 daily service was stopped only after all 48 tasks were terminal (44 completed, three failed, one cancelled). Stop reported zero queued cancellations, zero new cancellation requests and no unresolved attempts. Its board, attempt files, artifact references, controls and Git worktrees remain in place under `/Users/soku/.local/share/hey-my-buddy`; moving that tree would invalidate absolute references.

An additional private database copy, `board.sqlite3.v7-archive-20260924T085224Z`, passed SQLite integrity checking. Its SHA-256 and the unchanged original database SHA-256 were both `7ca0fd91f5e9a2aa217c6a67a0bd395348a71c79c6ed2e1e095720d7138a39eb`, including after the installed 0.6 task. The fresh board is the separate `/Users/soku/.local/share/hey-my-buddy/state` directory. No old records were imported or converted.

The prior registered plugin source is retained at `/Users/soku/plugins/hey-my-buddy.previous-1790240110365660000`. The removed standalone discovery symlink is recoverable from `/Users/soku/plugins/hey-my-buddy-entrypoints-1EQAqr/deepseek-delegate-link`; its former target is present in that prior source backup. The DSH profile patch was backed up as `cordis.patch.yml.before-buddy-0.6-20260924` before changing only the Buddy workspace-bridge path. The installed plugin is the sole skill distribution.

## Scope of acceptance

This verifies local productivity use with the tested harnesses and configurations, including actual artifacts, explicit workspaces, durable routing, Host assistance and failure recovery. It does not claim Windows support, ZCode OAuth or inquiry support, a ZCode tool-free selector, universal model quality, monetary budget enforcement, automatic community research or native post-turn App wakeup. Community judgments and user preferences remain separate from measured outcomes. Start a new Codex task to load the updated skill after reinstalling the plugin.
