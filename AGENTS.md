# Repository maintenance

## Current design

ADR-007 defines the accepted 0.6 clean-cut target. The source baseline is released 0.5.0 commit `0b674e9`. Only the plugin's `buddy` skill is distributed. Do not restore standalone skill paths, old CLI aliases, legacy Node records/fingerprints/socket guards, or historical schema conversion branches. Historical design documents are evidence, not current compatibility requirements.

[ADR-008](docs/decisions/008-harness-owned-evaluation-maintenance.md) is implemented in the 0.7 source: Harness-owned evaluation maintenance, read-only console evidence and local drafts with short publication grants. Consult the owning references before changing responsibilities. Source acceptance and the installed/running runtime remain separate facts.

[ADR-010](docs/decisions/010-production-workflow-repair-plan.md) defines the 0.8 repair baseline, which the installed daily service still runs as contract 0.8.1/schema 10: an explicit task `timeoutSeconds: 0` has no wall-clock execution deadline, while cancellation, Worker ownership and bounded waits remain separate. ZCode still declares read-only observation; its checked native protocol has no safe Host-initiated in-turn inquiry input. Keep implementation details in the owning references and the actual installed identity in [0.8.1 acceptance](docs/acceptance/optional-deadline-0.8.1.md). The [0.8.0 installation record](docs/acceptance/installed-0.8.0.md) retains the unresolved old-artifact integration boundary.

[ADR-011](docs/decisions/011-runtime-refinement.md) defines the 0.9.0 source candidate: one machine-wide attempt ceiling with user-owned per-model-family concurrency limits, and whole-goal artifact verification over immutable Git objects. The 0.9.0 source uses contract 0.9.0/schema 11 with no startup migrations; source changes never upgrade the installed service, and the delivery record is owned by [0.9.0 acceptance](docs/acceptance/runtime-refinement-0.9.0.md).

Read [architecture](docs/reference/architecture.md) and [ADR-007](docs/decisions/007-neutral-core-and-single-current-contract.md) before changing ownership, persistence, worker, routing or recovery contracts. Current source and ADR-007 take precedence over older implementation documents.

The Python service owns authoritative SQLite state. Workers own child handles and durable receipts over named C-Two operations. Preserve task/attempt identity, idempotency, transaction/event atomicity, owner fencing, fixed artifact bindings and actual shutdown evidence. Unknown never means stopped. Host and Worker edits require explicit workspace ownership; independent writers use isolated worktrees.

## Layout

- `src/buddy/`: public CLI, common service, workflow, evaluation and worker machinery; Python harness adapters under `adapters/`.
- `harnesses/dsh/`: DSH-specific Node scripts, plugins and tests.
- `apps/console/`: React/Vite frontend; built assets are packaged for users without npm.
- `tests/python/`: Python and cross-component verification.
- `docs/reference/`: current operational contracts; `docs/decisions/` and `docs/acceptance/`: decisions and evidence.
- `packaging/`: explicit runtime resource and plugin assembly; `bin/buddy`: the single bundled launcher.
- `skills/buddy/SKILL.md`: the single agent entrypoint.

## Verification

Use uv from the repository root. The normal complete check is `uv run --frozen python -m buddy.checks`. Run focused affected tests for a bounded change; do not run paid models or broad suites for wording-only edits.

Tests always use private state/runtime roots. When invoked inside a Buddy process, clear inherited runtime, Worker and agent credentials for each test subprocess: `BUDDY_STATE_DIR`, `BUDDY_RUNTIME_ROOT`, `BUDDY_RUNTIME`, `BUDDY_RUNTIME_IDENTITY`, `BUDDY_WORKER_STATE`, `BUDDY_WORKER_ID`, `BUDDY_AGENT_CREDENTIAL`, `BUDDY_AGENT_CREDENTIAL_FILE`, `VIRTUAL_ENV`, `UV_PROJECT_ENVIRONMENT`. `BUDDY_DEV_SOURCE=1` alone cannot override a pinned runtime. Use the test harness's private directories; never the daily board.

Verify real diffs, artifacts and relevant checks before acceptance. Admission, process completion and Host acceptance are separate facts. A private cold-start and staged-plugin test must verify stable interpreter/package/resource paths, including after the source directory is replaced.

Stage with `uv run --frozen python packaging/stage-plugin.py --destination <separate hey-my-buddy directory>`. No compatibility facades are shipped. Keep raw local logs and experiment scripts in ignored `.dsh-skill-build/`.

## Documentation

Each Markdown prose paragraph occupies one source line; preserve structural newlines. Keep setup instructions independent of temporary branches. README and its Chinese counterpart describe purpose and use with language parity. Detailed commands belong in their owning reference, routed from `docs/README.md` and the skill. Update the owning page first and entrypoint summaries when behavior changes. Record actual verification in `docs/acceptance/`; never present a draft or a worker's success message as acceptance. State the verification boundary in public docs: a capability that is unverified, partially wired or waiting on installation must be named as such rather than described as working. Only an explicit user background request authorizes a scheduler or automation, and no agent creates or mutes one merely to save tokens or quota.
