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

## History and evidence

| Document | Role |
| --- | --- |
| [docs/decisions/001-python-transactional-blackboard.md](decisions/001-python-transactional-blackboard.md) | Historical ADR-001: the accepted design requirements that led to the current implementation. Its migration instructions are historical; the implemented behavior is in [architecture.md](../deepseek-delegate/references/architecture.md) |
| [docs/acceptance/python-blackboard-0.4.0.md](acceptance/python-blackboard-0.4.0.md) | Prior acceptance evidence for 0.4.0: what was actually run, observed and limited |

## Maintainer rules

| Document | Role |
| --- | --- |
| [AGENTS.md](../AGENTS.md) | Repository development invariants, verification commands and topic-based routing: which document to update for which kind of change |

When behavior changes, update the owning reference first, then the READMEs and both skills if the user-visible entry path changed, and record acceptance evidence under `docs/acceptance/`. Keep architecture statements verified against source; the historical ADR is not updated to match new behavior.
