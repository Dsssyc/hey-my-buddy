# Repository maintenance

## Current design

The user has now authorized the [Claude proposal](docs/decisions/012-claude-code-distribution-and-evidence-routing.md)'s section X objective/timeline slice: the UI term is 工作目标, default ordering is latest activity, and idle gaps longer than 30 minutes fold. It is the 0.14.0 source candidate (contract 0.14.0, schema 12) after verified 0.13.0; it does not install either candidate or approve the other pending phases. After the Codex Host ran out of quota, the user asked a Claude Code Host to continue this branch and agenda; the [0.14.0 record](docs/acceptance/objective-timeline-0.14.0.md) separates their work and verification. A schema-11 board becomes schema 12 only through `python -m buddy.board_prepare` on an idle copy, never at startup. Keep objective/title metadata outside Worker and selector input, derive the timeline from recorded facts, and preserve the existing authority and execution lifecycle. Codex owns the backend/schema and final review; the user's Claude design/review division remains applicable.

The 0.13.0 source candidate implements the console-entry slice of the [Claude proposal](docs/decisions/012-claude-code-distribution-and-evidence-routing.md): one-use entry, authenticated browser sessions and the user's single-writer choice. [Console](docs/reference/console.md) owns its contract; schema remains 11. The daily board remains on the [installed 0.12.0 title-fallback baseline](docs/acceptance/title-fallback-0.12.0.md) until a separately authorized coordinated cutover. Use that matching installed launcher for daily work and private state/runtime roots for source tests. Other proposal phases and unresolved section XII choices remain user-owned.

The uninstalled 0.13.0 candidate also carries the user-requested Host token repair: the CLI prints brief compact projections by default, with the CLI-local `"output":"full"` and unchanged RPC views ([CLI output views](docs/reference/cli.md#output-views)), and Claude Code Hosts wait through one background `await` per goal ([usage](docs/reference/usage.md#waiting-from-claude-code)). The user chose to keep the Codex waiting behavior unchanged. The [record](docs/acceptance/cli-brief-output-0.13.0.md) holds the measurements and verification. A projection must keep every identifier the next command needs and never hide errors, open boundaries, truncation or unconfirmed shutdown.

[ADR-012](docs/decisions/012-bounded-control-overhead.md) defines the user-approved efficiency repair: light authenticated liveness, transient empty claims with durable real allocations, and subsequent ZCode/routing/Host-supervision repairs. That repair's source is contract 0.10.0/schema 11. Use the [0.10.0 acceptance record](docs/acceptance/runtime-efficiency-0.10.0.md) to distinguish implemented slices, verified behavior and actual installation.

ADR-007 defines the accepted 0.6 clean-cut target. The source baseline is released 0.5.0 commit `0b674e9`. Only the plugin's `buddy` skill is distributed. Do not restore standalone skill paths, old CLI aliases, legacy Node records/fingerprints/socket guards, or historical schema conversion branches. Historical design documents are evidence, not current compatibility requirements.

[ADR-008](docs/decisions/008-harness-owned-evaluation-maintenance.md) is implemented in the 0.7 source: Harness-owned evaluation maintenance, read-only console evidence and local drafts with short publication grants. Consult the owning references before changing responsibilities. Source acceptance and the installed/running runtime remain separate facts.

[ADR-010](docs/decisions/010-production-workflow-repair-plan.md) defines the 0.8 repair baseline. An explicit task `timeoutSeconds: 0` has no wall-clock execution deadline, while cancellation, Worker ownership and bounded waits remain separate. The 0.9 source adds cooperative ZCode inquiry through verified root-tool checkpoints; it never injects native turn input or treats a child reply as the root's answer. Native permissions still require a governed Host boundary. Keep current behavior in the owning references and actual verification/installation identity in [0.9.0 acceptance](docs/acceptance/runtime-refinement-0.9.0.md); the [0.8.1 record](docs/acceptance/optional-deadline-0.8.1.md) and [0.8.0 installation](docs/acceptance/installed-0.8.0.md) retain their historical evidence.

[ADR-011](docs/decisions/011-runtime-refinement.md) defines the 0.9.0 source candidate: one machine-wide attempt ceiling with user-owned per-model-family concurrency limits, and whole-goal artifact verification over immutable Git objects. The 0.9.0 source uses contract 0.9.0/schema 11 with no startup migrations; source changes never upgrade the installed service, and the delivery record is owned by [0.9.0 acceptance](docs/acceptance/runtime-refinement-0.9.0.md).

The [Claude proposal](docs/decisions/012-claude-code-distribution-and-evidence-routing.md) keeps proposal status outside the implemented Claude Worker P1 slice, the user's 2026-09-26 isolated-settings choice, the bounded title-fallback slice and the authorized console-entry slice described above. The 0.11.0 source (contract 0.11.0, schema 11) carries that implementation; [claude.md](docs/reference/claude.md) owns its execution contract and the [Claude P1 record](docs/acceptance/claude-worker-p1-0.11.0.md) separates native adapter/controller probes, mock service integration and the installed daily runtime. P1 defaults to private settings, empty setting sources and strict MCP configuration; changing source does not update a running installation. The other proposal sections and remaining section XII choices, including distribution, the global CLI, routingBrief, escalation, quota-driven routing, result ledger, macro-task UI, native resume and inquiry, remain unapproved for implementation.

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
