# Read-only delegation records and objective browsing: 0.15.1

## Scope and installation

The user authorized U1–U4 and normal routed delegations under objective `obj-11f3e1a9-6b3a-4374-944d-61fde180672f`. Source/release contract is 0.15.1; schema remains 12. Daily installation remains verified 0.15.0, stable runtime `091d997d8164557d9877366d48095971`, service `a1a93886-9e11-42ed-9418-3cc956e5264e`. Installation is not authorized for this candidate. No native acceptance probe was run. The protected uncommitted production-repairs-0.8.0.md is excluded. A2/A3 remain unimplemented proposals.

## Implemented contracts and backend

console.md, objectives.md, SKILL.md and usage.md were committed before implementation. Optional objective description is bounded to 300 characters, stored in the first run's creation event atomically with admission, and excluded from Worker and selector inputs. No schema/column/index change or historical backfill occurs. Read projections add description, taskSummary and complete-group accepted-root count. The skill requires ≤30-character intent titles and leaves low-level API compatibility intact. Implementation work defaults to routing; Claude design and screenshot-review roles remain explicit.

Browser write operations before: evaluation_write_begin, evaluation_write_renew, user_policy_publish, evaluation_write_abort, model_catalog_refresh, task_cancel, task_retry, task_acknowledge, workflow_submit, workflow_decide, workflow_continue, workflow_takeover, workflow_cancel, workflow_acknowledge, workflow_scope_amend, workflow_workspace_resolve, workflow_integration_record, workspace_cleanup_plan, workspace_cleanup_apply, workflow_suggest.

Browser write operations after: evaluation_write_begin, evaluation_write_renew, user_policy_publish, evaluation_write_abort, model_catalog_refresh, objective_stop. Existing evaluation_history, selection_get, selection_list, model_profiles and workflow_get reads remain. Host CLI authorization is unchanged. Removed commands return METHOD_NOT_FOUND for writers too. Browser submission/control-file persistence helpers are removed.

objective_stop resolves the complete selected group, cancels current unaccepted roots and owned helpers/routes in one transaction, retains accepted roots, and seals replay scope with a command receipt. It requires current console authority. It does not introduce a durable closed-group lifecycle; future authorized Host submissions remain possible. Cancellation intent never proves process termination.

## Delegation and real routing evidence

Design b88aa718-abca-47b1-b98d-4d64dc46be52 used explicit Claude Opus 5.5/high under the user-established division. Host integrated its fixed document and corrected optional-description storage, accepted-root progress, uniform three-line cards and whole-group cancellation. Accepted integration int-c63054ef-d3fd-4de4-994c-886700ccca5a; managed checkout cleanup cln-4a30e796-ae63-4965-a69f-dfcdac283c46.

Frontend d37a8c79-ce98-4e72-b003-a72704c99a5f omitted the configuration tuple and used a task-local GLM-5.3 preference. Decision dec-8edbcdcf-52cd-47e0-bf3b-6f26c4840ea0 succeeded and selected zcode/zai-api/GLM-5.3/max with matched task preference; no Host recovery. Its durable diagnostics record one selector call with an empty failure list (17,597 input and 224 output tokens); no correction occurred. Implementation acceptance remains pending.

Baseline routingHealth: window 20, samples 20, failures 5, consecutive failures 4, last success 2026-09-25T11:17:44.243Z. Final comparison is pending. Explicit Claude configurations are not smart-route successes. policy-alternative-unsupported has not been observed in this agenda. Contrary to the supplied observation, installed 0.15.0 source lists that code among one-correction retryable answers; this round changes no routing retry code and records actual diagnostics when relevant.

## Verification status

Focused backend validation: objective metadata/read and stop tests 18 passed; console/CLI/session tests 67 passed; description-to-selector isolation 1 passed; current-core tests 6 passed; routing and offline schema-preparation tests 26 passed. Tests use private state/runtime roots. A new test initially omitted an acceptance note and was corrected; a focused command initially named the wrong test class and was rerun correctly. No full buddy.checks has run in this task yet.

Frontend, browser width evidence, final full checks, package validation and task usage accounting remain pending. Raw receipts/logs are in ignored .dsh-skill-build/readonly-0151/. Screenshots will use output/playwright/ and synthetic fixtures, not the daily board.
