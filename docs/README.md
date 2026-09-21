# Documentation index

This page names every document in the repository and what it owns. It is for readers who want the detail behind the [README](../README.md), and for maintainers deciding where a change belongs.

## Human entry points

| Document | Role |
| --- | --- |
| [README.md](../README.md) | English entry point: why Buddy is useful, what to delegate, requirements, the one install path, a first natural-language request, boundaries and support links |
| [README.zh-CN.md](../README.zh-CN.md) | Chinese entry point with the same structure and content (language parity) |

Both READMEs stay concise and avoid internal terminology; details belong in the references below.

## Operating the skill

| Document | Role |
| --- | --- |
| [deepseek-delegate/references/usage.md](../deepseek-delegate/references/usage.md) | The practical path: install, workspace grouping, task packet, start → await → verify, progress questions, wait timeouts, cancellation, background work, optional adapters |
| [deepseek-delegate/references/cli.md](../deepseek-delegate/references/cli.md) | Complete `buddy` command reference: parameters, defaults, bounds, error codes, result/run envelopes, events and inquiry contract |
| [deepseek-delegate/references/operations.md](../deepseek-delegate/references/operations.md) | Runtime lifecycle and upgrade, workspace-bridge install and recovery, private state and environment variables, cancellation/recovery, legacy import, old-MCP cleanup |
| [deepseek-delegate/references/workers.md](../deepseek-delegate/references/workers.md) | `dsh`/`command`/`external` adapters, the public `BoardClient` contract, a runnable external worker, worker identity and receipts, supervisors |
| [deepseek-delegate/references/runner.md](../deepseek-delegate/references/runner.md) | Advanced standalone `scripts/run.mjs` runner: options, precedence, result, exit codes, logs, attach mode; how it differs from durable Buddy use |
| [deepseek-delegate/references/handoff.md](../deepseek-delegate/references/handoff.md) | Standalone owner-resumption helper (`scripts/handoff.mjs`) for work that outlives the turn; App heartbeat and verified CLI callback routes |
| [deepseek-delegate/references/plugin-service.md](../deepseek-delegate/references/plugin-service.md) | Compatibility index: keeps the old service-manual headings as forwarding sections and links the focused pages |

The whole `deepseek-delegate/` directory can be copied and installed on its own, so every link inside it is relative to that directory. The two skill entrypoints are [deepseek-delegate/SKILL.md](../deepseek-delegate/SKILL.md) (standalone `$deepseek-delegate`) and [skills/buddy/SKILL.md](../skills/buddy/SKILL.md) (plugin `$buddy`); each is a compact operational entrypoint that links to the packaged references above.

## Current architecture

[architecture.md](../deepseek-delegate/references/architecture.md) describes the implemented process topology, state ownership, transactions, identities, recovery and limits. Start here when modifying the service or integrating another agent.

## Accepted design direction

[ADR-002: Host-directed assistance and workspace ownership](decisions/002-host-directed-assistance-and-workspaces.md) records the accepted principles for Hosts that both implement and delegate, worker-managed subagents, turn-end yield and continuation, explicit execution workspaces and integration. Cost observation and budgets are deferred optional additions. Runtime implementation is pending; this design does not add supported commands or replace the current architecture reference.

[ADR-005: shared assessments, table-level exclusion and the console stack](decisions/005-shared-assessments-and-table-exclusion.md) records the policy the user accepted on 2026-09-22: one shared, bounded current assessment table per user/blackboard with cross-project reuse by default, table-level reader/writer exclusion, single-writer revision/generation fencing, independent control paths, and a React frontend developed and built with Vite. It resolves ADR-004's section III choices and supersedes the project-opt-in default for cross-project reuse. The design is accepted; the shared table, maintenance, selection/editor gate and new frontend are not implemented, so it adds no supported commands.

## Design proposals

[ADR-004 proposal: decision support, evaluation maintenance and the local console](decisions/004-buddy-decision-support-and-console.md) is the current consolidated proposal. It restates the discussed direction — Host/Worker comparative advantage, the blackboard and C-Two boundaries, turn-end yield and continuation, explicit workspaces, bounded current assessment cards, the preferred non-thinking decision model, evaluation maintenance and a local console — and then separates the new boundary recommendations that still need Host and user review. Its section III governance choices are resolved by accepted ADR-005; the rest of the document keeps its proposal status. It is a discussion consolidation: the runtime is not implemented, and the filenames, role names and interface sketches it names are proposed contracts, not callable operations.

[ADR-003 draft](decisions/003-harness-model-selection.md) is retained as historical discussion; ADR-004 supersedes it as the current proposal for model selection and evaluation maintenance.

## History and evidence

| Document | Role |
| --- | --- |
| [docs/decisions/001-python-transactional-blackboard.md](decisions/001-python-transactional-blackboard.md) | Historical ADR-001: the accepted design requirements that led to the current implementation. Its migration instructions are historical; the implemented behavior is in [architecture.md](../deepseek-delegate/references/architecture.md) |
| [docs/decisions/003-harness-model-selection.md](decisions/003-harness-model-selection.md) | Historical ADR-003 discussion draft on bounded assessment cards, typed model selection and the Jev backend. Its findings and acceptance table remain research input; ADR-004 is the current consolidated proposal |
| [docs/acceptance/python-blackboard-0.4.0.md](acceptance/python-blackboard-0.4.0.md) | Prior acceptance evidence for 0.4.0: what was actually run, observed and limited |

## Maintainer rules

| Document | Role |
| --- | --- |
| [AGENTS.md](../AGENTS.md) | Repository development invariants, verification commands and topic-based routing: which document to update for which kind of change |

When behavior changes, update the owning reference first, then the READMEs and both skills if the user-visible entry path changed, and record acceptance evidence under `docs/acceptance/`. Keep architecture statements verified against source; the historical ADR is not updated to match new behavior.
