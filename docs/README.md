# Documentation index

This page names every current document in the repository and what it owns. It is for readers who want the detail behind the [README](../README.md), and for maintainers deciding where a change belongs.

## Human entry points

| Document | Role |
| --- | --- |
| [README.md](../README.md) | English entry point: delegated goals, install and first request, routing and console, recovery and current limits |
| [README.zh-CN.md](../README.zh-CN.md) | Chinese entry point with the same structure and content (language parity) |

Both READMEs stay concise and avoid internal terminology; details belong in the current references below.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/buddy/` | Public CLI, common service, workflow, routing, evaluation and worker machinery; Python harness adapters under `src/buddy/adapters/`; built console assets under `src/buddy/console_assets/` |
| `harnesses/dsh/` | DSH-specific Node runner, bridge and decision scripts under `scripts/`, their plugins under `plugins/`, and the DSH Node tests |
| `apps/console/` | React/Vite console source; built output is checked into `src/buddy/console_assets/` and shipped to users without npm |
| `tests/python/` | Python and cross-component verification; the DSH Node suites live under `harnesses/dsh/tests/` |
| `packaging/runtime-assets.json` | The explicit runtime resource manifest consumed by `packaging/stage-plugin.py` and the stable-runtime materializer |
| `bin/buddy` | The single bundled launcher |
| `skills/buddy/SKILL.md` | The single agent-facing entrypoint |
| `docs/reference/`, `docs/decisions/`, `docs/acceptance/` | Current operational contracts, the design record, and versioned evidence |

## Current references

These pages describe the 0.8 source in this checkout (contract 0.8.0, schema 10). Installed runtime and source acceptance are separate facts; check `buddy health`, `buddy runtime` and the [0.8 acceptance record](acceptance/production-repairs-0.8.0.md). Source capacity defaults to two business attempts plus one decision; a deployed board may use different limits.

| Document | Role |
| --- | --- |
| [reference/usage.md](reference/usage.md) | Practical path from install to reviewed result: task packets, workspace choice, supervised goals, observation and background follow-up |
| [reference/workflow.md](reference/workflow.md) | Governed goal lifecycle: routing boundaries, Host decisions, helpers, continuation and takeover, workspace/artifact rules and cancellation |
| [reference/workspace-lifecycle.md](reference/workspace-lifecycle.md) | Host-owned scope changes, recorded conflict recovery, verified integration and exact-worktree cleanup |
| [reference/codex.md](reference/codex.md) | Native Codex App Server execution, account-plan discovery, structured results and session bindings |
| [reference/cli.md](reference/cli.md) | Complete `buddy` command reference: parameters, defaults, bounds, result envelopes, event kinds and error codes |
| [reference/operations.md](reference/operations.md) | Installation, runtime lifecycle, private state and environment, the DSH workspace bridge, recovery and removal |
| [reference/evaluation.md](reference/evaluation.md) | Shared profiles, cards, preferences, evidence, the reader/writer gate, the private console HTTP surface and the fixed decision profile |
| [reference/evaluation-maintenance.md](reference/evaluation-maintenance.md) | Harness-owned bounded fact preparation, archived reviews, shared card updates, publication and history |
| [reference/decision.md](reference/decision.md) | The bounded tool-free DSH routing helper: selection input/output, cache layout and process ownership |
| [reference/workers.md](reference/workers.md) | `dsh`/`zcode`/`codex`/`command`/`external` adapters, the public `BoardClient` contract, worker identity, reconciliation, bounded activity and receipts, and supervisors |
| [reference/runner.md](reference/runner.md) | DSH-specific `harnesses/dsh/scripts/run.mjs` runner and bridge scripts: options, precedence, governed turn protocol, exit codes and attach mode |
| [reference/architecture.md](reference/architecture.md) | Implemented topology, C-Two contract, schema 10, independent execution lanes, identity, recovery, catalog observation and packaging |

Detailed commands belong in their owning reference, routed from this page and from [skills/buddy/SKILL.md](../skills/buddy/SKILL.md). The previous implementation contracts under `docs/implementation/` were consolidated into these owning references and removed; there is no compatibility copy.

## Accepted design direction

[ADR-009: Parallel lanes and local console drafts](decisions/009-parallel-lanes-and-local-console-drafts.md) records independent business/routing capacity, cooperative worker-pool retirement, and local drafting with short publication grants.

[ADR-010: Production workflow repair plan](decisions/010-production-workflow-repair-plan.md) defines the 0.8 repairs: separated user and maintenance publication, task-local routing preferences, catalog history, recovery and activity, Host-owned workspace lifecycle, native Codex execution and lightweight RPC. The owning references describe the implementation and the [acceptance record](acceptance/production-repairs-0.8.0.md) distinguishes verified behavior from installation. ZCode's checked protocol supports observation but not safe correlated live inquiry.

[ADR-008: Harness-owned evaluation maintenance](decisions/008-harness-owned-evaluation-maintenance.md) was implemented in the 0.7 source and remains current: a skill-equipped Harness performs shared evaluation updates on user request or through its own scheduling facility, only when the user asks for recurring updates. Evidence is shared across source Hosts/projects and read-only in the console. The blackboard retains ordinary business/routing scheduling but does not schedule evaluation maintenance.

[ADR-007: neutral Buddy core and a single current contract](decisions/007-neutral-core-and-single-current-contract.md) is the accepted 0.6 architecture decision. It defines the source layout, the single current contract, durable default routing, the ZCode rebase and the clean-cut release boundary. Current behavior is maintained in the references above; ADR-007 is not rewritten as behavior changes.

[ADR-002: Host-directed assistance and workspace ownership](decisions/002-host-directed-assistance-and-workspaces.md) records the accepted principles for Hosts that both implement and delegate, worker-managed subagents, turn-end yield and continuation, explicit execution workspaces and integration. Its design-time status is retained; supported behavior is maintained in [workflow.md](reference/workflow.md) and [architecture.md](reference/architecture.md).

[ADR-005: shared assessments, table-level exclusion and the console stack](decisions/005-shared-assessments-and-table-exclusion.md) records the policy accepted on 2026-09-22: one shared, bounded current assessment table per user/blackboard with cross-project reuse by default, table-level reader/writer exclusion, single-writer revision/generation fencing, independent control paths, and a React frontend built with Vite. Its original acceptance text records the design-time state; implemented commands and limits are maintained in [evaluation.md](reference/evaluation.md) and [cli.md](reference/cli.md).

## Design proposals

[ADR-004 proposal: decision support, evaluation maintenance and the local console](decisions/004-buddy-decision-support-and-console.md) preserves the consolidated discussion of Host/Worker comparative advantage, blackboard and C-Two boundaries, turn-end yield and continuation, explicit workspaces, bounded assessment cards, evaluation maintenance and a local console. Its section III governance choices were resolved by ADR-005; the remaining recommendations retain their proposal status. Its original interface sketches are historical design text. Supported runtime behavior and callable operations are maintained in the [workflow](reference/workflow.md), [evaluation](reference/evaluation.md), [architecture](reference/architecture.md) and [CLI](reference/cli.md) references.

[ADR-003 draft](decisions/003-harness-model-selection.md) is retained as historical discussion; ADR-004 superseded it as the proposal for model selection, and implemented routing is owned by [workflow.md](reference/workflow.md).

## History and evidence

Current delivery evidence: [0.8 production repairs](acceptance/production-repairs-0.8.0.md), [console/evaluation 0.7.0](acceptance/console-evaluation-0.7.0.md) and [parallel dispatch 0.6.2](acceptance/parallel-dispatch-0.6.2.md). The 0.8 record owns the current verification and deferred-installation status; the 0.7 record retains the earlier installation evidence.

| Document | Role |
| --- | --- |
| [decisions/001-python-transactional-blackboard.md](decisions/001-python-transactional-blackboard.md) | Historical ADR-001: the accepted design requirements that led to the current implementation. Its migration instructions describe a removed path; implemented behavior is in [architecture.md](reference/architecture.md) |
| [decisions/006-zcode-adapter-integration.md](decisions/006-zcode-adapter-integration.md) | ZCode integration record: the original 2026-09-22 feasibility study and measurements, preserved as historical rationale, with the implemented 0.6 route and its correction of the old OAuth assumption recorded at the top |
| [acceptance/python-blackboard-0.4.0.md](acceptance/python-blackboard-0.4.0.md) | Prior acceptance evidence for 0.4.0: what was actually run, observed and limited |
| [acceptance/console-decisions-2026-09-22.md](acceptance/console-decisions-2026-09-22.md) | Independent checks and live private-board acceptance for the React/Vite console, bounded evaluations and durable decision jobs |
| [acceptance/productivity-workflow-0.5.0.md](acceptance/productivity-workflow-0.5.0.md) | Packaged DSH assistance, parallel helpers, three-turn continuation, fixed-artifact acceptance and browser evidence for 0.5.0 |
| [acceptance/neutral-core-0.6.0.md](acceptance/neutral-core-0.6.0.md) | Neutral core and single current contract, DSH/ZCode collaboration and recovery, installed routing, archive cutover and regression evidence for 0.6.0 |
| [acceptance/profile-effort-labels-2026-09-24.md](acceptance/profile-effort-labels-2026-09-24.md) | DSH-assisted correction of duplicated effort labels, unchanged profile values, frontend regressions and real-browser verification |
| [acceptance/desktop-console-2026-09-24.md](acceptance/desktop-console-2026-09-24.md) | Top navigation, grouped models/delegations, provenance and keyset history, draft retention and non-browser verification |
| [acceptance/routing-console-2026-09-24.md](acceptance/routing-console-2026-09-24.md) | Per-delegation routing rationale, settings-only configuration, model-card maintenance and bounded history verification |

Acceptance records are versioned evidence, not current contracts. They may reference paths from the release they describe. The 0.6 record identifies the tested runtime, fixed artifacts and local cutover; check the actual running identity when using another installation.

## Maintainer rules

| Document | Role |
| --- | --- |
| [AGENTS.md](../AGENTS.md) | Repository development invariants, verification commands and topic-based routing: which document to update for which kind of change |

When behavior changes, update the owning reference first, then the READMEs and [skills/buddy/SKILL.md](../skills/buddy/SKILL.md) if the user-visible entry path changed, and record acceptance evidence under `docs/acceptance/`. Keep architecture statements verified against source; historical ADRs describe the decision at their own point in time.
