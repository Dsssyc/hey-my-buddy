# Documentation index

This page routes readers to the document that owns each subject. It is for readers who want the detail behind the [README](../README.md), and for maintainers deciding where a change belongs. It states no product behavior of its own: behavior lives in the current references, decisions in the ADRs and verification in the acceptance records.

## Human entry points

| Document | Role |
| --- | --- |
| [README.md](../README.md) | English entry point: what the product does, install and first request, current status and limits |
| [README.zh-CN.md](../README.zh-CN.md) | Chinese entry point with the same structure and content (language parity) |
| [terms.md](terms.md), [terms.zh-CN.md](terms.zh-CN.md) | Accounts and terms, in English and Chinese with parity: what the product does with the user's harness accounts, the two layers of terms that still apply, and links to each vendor's official terms |

Both READMEs stay concise and avoid internal terminology; details belong in the current references below.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/hey_my_buddy/` | The package, divided by the two sides of the system: `blackboard/` (the service process, store, micro task lifecycle, routing, catalog and evaluation), `buddy/` (the execution process, the roles and one package per harness under `buddy/harnesses/`) and `protocol/` (C-Two contracts, transport and client) between them; `cli/`, `install/` and `console/`, with the built console assets under `console/assets/` |
| `apps/console/` | React/Vite console source; built output is checked into `src/hey_my_buddy/console/assets/` and shipped to users without npm |
| `tests/python/` | Python and cross-component verification, mirroring the package layout |
| `packaging/` | The explicit runtime resource manifest `runtime-assets.json`, the skill build `build-skill.py` and the upgrade fixture generator |
| `skills/hey-my-buddy/` | The shared agent skill: `SKILL.md` and its `scripts/hey-my-buddy` launcher |
| `tools/` | Development tools that run only from a source checkout, such as the board sample exporter |
| `docs/reference/` | Current operational contracts |
| `docs/decisions/` | The design record: one ADR per decision, with its dated amendments |
| `docs/design/` | Plans and notes, and [backlog.md](design/backlog.md): recorded issues not yet in an implementation batch |
| `docs/acceptance/` | Verification evidence of what was built: records of work that implements an ADR live in `adrNNN/`, records of other work in a folder named after that work, install records in `installs/` |
| `docs/research/` | What was looked at before a decision: dated notes on other projects, papers and products ([index](research/README.md)) |

## Current references

| Document | Role |
| --- | --- |
| [reference/usage.md](reference/usage.md) | Practical path from install to reviewed result: task packets, workspace choice, supervised micro tasks, observation and background follow-up |
| [reference/host-codex.md](reference/host-codex.md) | Codex Host waiting flow: one monitoring-only subagent per micro task, the launcher's sandbox rule and Codex-specific limits |
| [reference/host-claude-code.md](reference/host-claude-code.md) | Claude Code Host waiting flow: one background `await` per running micro task, the launcher's sandbox settings and the session-wakeup limits |
| [reference/workflow.md](reference/workflow.md) | Micro task lifecycle: routing boundaries, Host decisions, helpers, continuation and takeover, workspace and artifact rules, cancellation and provider rejections |
| [reference/workspace-lifecycle.md](reference/workspace-lifecycle.md) | Host-owned scope changes, recorded conflict recovery, verified integration and exact-worktree cleanup |
| [reference/codex.md](reference/codex.md) | Codex Worker execution through the App Server of the Codex CLI: plan discovery, structured results and session bindings |
| [reference/claude-code.md](reference/claude-code.md) | Claude Code Worker execution through the Claude Code CLI: native configuration and login, structured results, native-probe boundaries and provider rejection classification |
| [reference/cli.md](reference/cli.md) | Complete `hey-my-buddy` command reference: parameters, defaults, bounds, result envelopes, event kinds and error codes |
| [reference/operations.md](reference/operations.md) | Installation and upgrade, runtime lifecycle, private state and environment, session privacy, recovery and removal |
| [reference/evaluation.md](reference/evaluation.md) | Shared buddies, preferences, model service notes, the private console HTTP surface and the Buddy and 设置 pages |
| [reference/console.md](reference/console.md) | Console access and CLI lifecycle, persistent sessions, buddy sections, deep links and verification |
| [reference/macro-tasks.md](reference/macro-tasks.md) | Macro task grouping, `macro-task-list`/`macro-task-timeline` read shapes, deletion and timeline presentation |
| [reference/decision.md](reference/decision.md) | The harness-neutral Router protocol in both modes: the tool-free fast request and the read-only review request, frozen candidates, answer bounds, budgets, input verification and the native capability boundary |
| [reference/workers.md](reference/workers.md) | The four harness adapters and the model-free `command` adapter, each harness's launch and turn, receipts, bounded activity and stop evidence |
| [reference/harness-integration.md](reference/harness-integration.md) | Adding a harness: each part to implement, its file and the test that guards it, the conformance check over every harness name and what it cannot verify |
| [reference/architecture.md](reference/architecture.md) | Implemented topology, the C-Two contract, the schema, the shared attempt ceiling and model service concurrency, identity, recovery, catalog observation and packaging |
| [reference/harnesses.md](reference/harnesses.md) | Local harness discovery: health cache, executable selection, environment, capabilities, provider rejections and cooling, and how far each harness is verified |

Detailed commands belong in their owning reference, routed from this page and from [skills/hey-my-buddy/SKILL.md](../skills/hey-my-buddy/SKILL.md).

## Decisions

Each ADR records the decision at its own point in time, with its dated amendments; it is not rewritten as behavior changes, and the current references above say what the source does now.

| ADR | Subject |
| --- | --- |
| [001](decisions/001-python-transactional-blackboard.md) | The Python transactional blackboard over C-Two: the design requirements behind the implementation |
| [002](decisions/002-host-directed-assistance-and-workspaces.md) | Assignment by comparative advantage, turn-end yield and continuation, explicit execution workspaces and integration |
| [003](decisions/003-harness-model-selection.md) | A bounded dynamic evaluation table for model selection (draft, superseded by ADR-004) |
| [004](decisions/004-buddy-decision-support-and-console.md) | Decision support, evaluation maintenance and the local console (proposal; its governance choices were resolved by ADR-005) |
| [005](decisions/005-shared-assessments-and-table-exclusion.md) | Shared assessments, table-level exclusion and the console stack |
| [006](decisions/006-zcode-adapter-integration.md) | ZCode integration: the original feasibility study, with the implemented route at its top |
| [007](decisions/007-neutral-core-and-single-current-contract.md) | A neutral core and a single current contract (its plugin distribution rule is superseded by ADR-015) |
| [008](decisions/008-harness-owned-evaluation-maintenance.md) | Harness-owned evaluation maintenance (superseded by ADR-021) |
| [009](decisions/009-parallel-lanes-and-local-console-drafts.md) | Parallel lanes and local console drafts (its capacity model is superseded by ADR-011) |
| [010](decisions/010-production-workflow-repair-plan.md) | The production workflow repair plan, with its [interface contract](decisions/010-repair-interface-contract.md) |
| [011](decisions/011-runtime-refinement.md) | Shared model concurrency and verified native interaction |
| [012](decisions/012-bounded-control-overhead.md) | Liveness and empty polls kept out of the durable work ledger |
| [012 proposal](decisions/012-claude-code-distribution-and-evidence-routing.md) | Claude Code integration, a global CLI distribution, evidence-based routing and a macro task view |
| [013](decisions/013-buddy-roles-and-blackboard-terminology.md) | Buddy roles and blackboard terminology; [CONTEXT.md](../CONTEXT.md) holds the definitions |
| [014](decisions/014-router-buddy-and-read-only-routing.md) | The Router buddy and read-only routing |
| [015](decisions/015-shared-agent-skill-distribution.md) | One shared `.agents` skill replaces the Codex plugin |
| [016](decisions/016-human-console-design.md) | Human-friendly console design notes |
| [017](decisions/017-local-installation-and-harness-discovery.md) | Local installation, the on-demand service and harness discovery |
| [018](decisions/018-routing-modes-and-host-workflow.md) | Router modes and the Host workflow |
| [019](decisions/019-worker-accounts-and-usage.md) | Worker accounts, usage visibility and private directories (independent Worker accounts are superseded by ADR-030) |
| [020](decisions/020-loopback-console-access.md) | Loopback console access without login by default |
| [021](decisions/021-router-buddy-planes-and-routing-evidence.md) | The Router buddy's planes, routing evidence and provider rejections |
| [022](decisions/022-expected-duration-and-graceful-stop.md) | Expected duration and graceful stop |
| [023](decisions/023-harness-integration-principles.md) | Harness integration principles and the shared permission levels |
| [024](decisions/024-console-session-view.md) | The console's session view |
| [025](decisions/025-harness-run-module.md) | One run module per harness |
| [026](decisions/026-delegation-authority.md) | A micro task that carries delegation authority (a Lead) |
| [027](decisions/027-model-catalog-trust.md) | The model catalog trusts only reliable readings |
| [028](decisions/028-macro-task-deletion.md) | Deleting macro tasks |
| [029](decisions/029-console-frontend-foundation.md) | The console's frontend foundation |
| [030](decisions/030-harness-layer-scope.md) | What the harness layer owns: the user's harness is used as it is |
| [031](decisions/031-one-process-per-execution.md) | One process per execution |

## Acceptance records

[docs/acceptance](acceptance/) holds what was actually run, observed and limited for each piece of work, and [installs](acceptance/installs/) one record per installed version. Acceptance records are versioned evidence, not current contracts: they may name paths and behavior of the release they describe.

## Maintainer rules

| Document | Role |
| --- | --- |
| [AGENTS.md](../AGENTS.md) | Repository development invariants, layout, verification commands and documentation rules |
| [CONTEXT.md](../CONTEXT.md) | Domain language: the blackboard, Host and Worker buddies, execution processes, macro tasks and micro tasks, with the words to avoid. Earlier records say "objective" (工作目标) for a macro task and "delegation" or "governed goal" (委派) for a micro task |

When behavior changes, update the owning reference first, then the READMEs and [skills/hey-my-buddy/SKILL.md](../skills/hey-my-buddy/SKILL.md) if the user-visible entry path changed, and record acceptance evidence under `docs/acceptance/`.
