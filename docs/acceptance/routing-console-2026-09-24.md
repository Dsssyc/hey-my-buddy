# Routing configuration and per-delegation rationale

## Scope

This change follows the accepted discussion: routing configuration contains settings only, each delegation provides its own recorded selection rationale, and evaluation maintenance belongs to model cards. The removed recommendation trial and global recent-decision feed are no longer console entrypoints. The UI keeps earlier routing and maintenance records accessible through bounded read-only pages rather than discarding history.

Work is isolated to `socu/buddy-core`, starting at `95536fb6f29f6b2dde73d96f1e36bdb664761c00`. The original `socu/python-blackboard` checkout and the separate `socu/buddy-codebuddy` worktree are not implementation targets. DSH's bounded Python slice uses its own detached worktree from that same fixed input, with Host integration and independent verification required before acceptance.

## Frontend evidence

On 2026-09-24, Node 24.15.0 ran `npm test` from `apps/console`: all 65 tests in 9 files passed. This includes 12 new behavioral cases for settings without a history feed or model invocation, independent maintenance pagination, a retained unpublished proposal, uncertain-request identity across tab changes, frozen historical preferences, native `reasoningEffort`, explicit Host choices, missing/failed decisions, old-turn unknown bindings, late-response isolation, long-reason disclosures and keyboard focus after choosing a historical decision. Subsequent focused corrections separate a successful recommendation from native execution-configuration rejection and collapse preferences for other candidates; all 14 tests in `routing-console.test.tsx` passed after those corrections. Existing workflow, model-effort, draft, task-history and desktop-console checks remain in the suite.

`npm run build` passed TypeScript and Vite 8.3.0 compilation after the final frontend correction. The built bundle is `index-DwGxZq5e.js` with stylesheet `index-Csd_qLIs.css`. `npm audit --omit=dev --audit-level=high` reported zero vulnerabilities. Documentation link/JSON/shell checks and the skill validator passed after backend integration.

The settings pane now uses ordinary content flow inside one viewport scroll area. Maintenance history and the selected maintenance detail occupy separate reading areas, and previous routing choices collapse after selection with focus moved to the chosen rationale. React asynchronous reads ignore responses after their selection or active pane changes, following the [React effect cleanup guidance](https://react.dev/reference/react/useEffect#fetching-data-with-effects).

No Computer Use, browser automation, screenshots or visual acceptance was performed for this change, following the user's instruction. Component tests and CSS inspection do not establish pixel-level rendering correctness.

## Python and protocol evidence

The DSH run `5caa7166-73f5-473f-8093-5b01f97a364f` delivered artifact `dd16c857-2e76-4a22-9c96-192bce157d2c`, sealed at commit `58162493dba55db7ad8b219b6171202a45a99600`; the patch SHA-256 is `e75ed54192b5f95376cb87cbd9fb106c4b65514189f4c405de58207e2fa53eb3`. The Host checked all eight changed paths, added the intentionally Host-owned `BuddyControl.selection_list` declaration and transport alias, and independently verified the integrated result before recording acceptance. Self and descendant shutdown were confirmed. Its ordinary wait window expired once while queued/executing; the same run was awaited again without restarting work.

Host review added SQLite int64 cursor bounds and rejected a partial turn binding without a recorded `decisionId`. Three assertions first reproduced the two defects; they then passed in a 117-test focused run covering `test_routing_history`, `test_cli`, `test_current_core`, `test_workflow_routing`, `test_decision` and `test_console`. Both reads are bounded, filter before limiting, keep fixed historical selections and leave the authoritative state/event head unchanged. New turn inputs freeze their route and configuration revision; earlier inputs remain unchanged and missing bindings remain unknown.

The DSH report noted a full Python run with one expected failure because its write scope excluded Host-owned transport wiring. That binding is present and the CLI surface check passed in Host verification. It also reported Node runner fixtures failing to find the YAML bridge under the restricted fixture PATH. Host ran the affected `turn-contract.test.mjs` and `turn-runner.test.mjs` with the uv-managed source interpreter explicitly provided as `BUDDY_PYTHON`; all 14 tests passed. No Node implementation change or real-model call was needed for this check. Raw Host logs are retained under ignored `.dsh-skill-build/routing-python-checks.log` and `routing-turn-checks.log`.

## Real private service acceptance

The ignored `.dsh-skill-build/routing-http-acceptance.py` ran against a new private source-backed state/runtime directory. It created 71 mixed fixture records with no configured selector and an additional model-helper fuse. Named C-Two and authenticated console HTTP reads paged all 23 maintenance records and 25 routing decisions; a deliberately marked unpublished fixture proposal older than the global snapshot remained accessible. Six requests missing the expected session/CSRF or using a foreign Origin were rejected. The built JavaScript and CSS were both served successfully. Event head, task count and evaluation leases were unchanged by reads; there were zero model attempts. The private service stopped with zero unresolved attempts.

## Packaged runtime

The validated plugin version is `0.6.1+codex.20260924125851`. Installation checks exposed that adding a C-Two method changes interface identity, so retaining contract version 0.6.0 was not valid for the new surface. The package and `CONTRACT_VERSION` advance to `0.6.1`; schema remains `8`, preserving board data without schema conversion or compatibility facades. After this correction, 27 current-core, CLI and C-Two service tests passed, and the real 71-record private HTTP/C-Two acceptance was repeated successfully against the final contract.

A separate staged plugin cold-started against private state and runtime roots, reported stable content ID `fdebe48e2c75c65dcf7edcee739465c0`, imported its package from inside that runtime and reported no source leaks. The plugin source directory was replaced while this private service remained running: PID `24415` and the runtime identity stayed unchanged, and `selection-list` still returned the expected empty bounded page. The private packaged service then stopped with no cancellations or unresolved attempts. The prior daily daemon's matching retained stable runtime is used for the detach before the final new client starts; an incompatible new client is not used to control the old C-Two surface.

During implementation, the user approved a temporary capacity increase to two while another project occupied the daily board. That work released its slot before a second worker was needed; the implementation ran with the observed capacity of one. No additional worker was started, and the final capacity is to remain one. Daily profiles, preferences and configuration are not reset by this update.
