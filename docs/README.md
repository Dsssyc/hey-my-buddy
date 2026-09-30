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
| `packaging/runtime-assets.json` | The explicit runtime resource manifest consumed by `packaging/build-skill.py` and the stable-runtime materializer |
| `skills/buddy/` | The shared agent skill: `SKILL.md` and its `scripts/buddy` launcher |
| `docs/reference/`, `docs/decisions/`, `docs/acceptance/` | Current operational contracts, the design record, and versioned evidence |

## Current references

[Local harness discovery](reference/harnesses.md) owns the ADR-017 source candidate's health cache, executable selection, environment and diagnostic contracts; it does not change the installed-version evidence below.

Daily [installation is 0.25.0/schema 15](acceptance/installed-0.25.0.md): the shared `buddy` skill in `~/.agents/skills` (linked from `~/.claude/skills`) implements both parts of ADR-018, ADR-020's optional loopback login, ADR-019 B phase one, ADR-019 decision 11 and the ADR-019 B phase two sub-batches ① to ③ (configuration sections, routing validation and quota recovery, Worker session privacy and storage cleanup). Each [acceptance record](acceptance/) separates automated tests, native checks and remaining limits; sub-batch ④ (Worker accounts) is pending. [Cross-Host membership and human reclassification](design/objective-membership-options.md) remain proposals.

| Document | Role |
| --- | --- |
| [reference/usage.md](reference/usage.md) | Practical path from install to reviewed result: task packets, workspace choice, supervised goals, observation and background follow-up |
| [reference/workflow.md](reference/workflow.md) | Governed goal lifecycle: routing boundaries, Host decisions, helpers, continuation and takeover, workspace/artifact rules and cancellation |
| [reference/workspace-lifecycle.md](reference/workspace-lifecycle.md) | Host-owned scope changes, recorded conflict recovery, verified integration and exact-worktree cleanup |
| [reference/codex.md](reference/codex.md) | Native Codex App Server execution, account-plan discovery, structured results and session bindings |
| [reference/claude.md](reference/claude.md) | Claude Code Worker P1: verified metadata and native-probe boundaries, isolated execution, quota classification and the user-directed bootstrap division |
| [reference/cli.md](reference/cli.md) | Complete `buddy` command reference: parameters, defaults, bounds, result envelopes, event kinds and error codes |
| [reference/operations.md](reference/operations.md) | Installation, runtime lifecycle, private state and environment, the DSH workspace bridge, recovery and removal |
| [reference/evaluation.md](reference/evaluation.md) | Shared profiles, cards, family preferences and notes, evidence, the reader/writer gate, the private console HTTP surface, the Buddy 配置/设置 pages and the two configured Router profiles |
| [reference/console.md](reference/console.md) | Console access and CLI lifecycle, persistent sessions, Buddy configuration sections, deep links and verification |
| [reference/objectives.md](reference/objectives.md) | Work-objective grouping, `objective-list`/`objective-timeline` read shapes, timeline presentation and the schema-13 offline preparation tool |
| [reference/evaluation-maintenance.md](reference/evaluation-maintenance.md) | Harness-owned bounded fact preparation, archived reviews, shared card updates, publication and history |
| [reference/decision.md](reference/decision.md) | The harness-neutral Router protocol in both modes: the tool-free fast request and the read-only review request, frozen candidates, answer bounds, provisional budgets, input verification and the native capability boundary |
| [reference/workers.md](reference/workers.md) | `dsh`/`zcode`/`codex`/`command`/`external` adapters, the public `BoardClient` contract, worker identity, reconciliation, bounded activity and receipts, and supervisors |
| [reference/runner.md](reference/runner.md) | DSH-specific `harnesses/dsh/scripts/run.mjs` runner and bridge scripts: options, precedence, governed turn protocol, exit codes and attach mode |
| [reference/architecture.md](reference/architecture.md) | Implemented topology, C-Two contract, schema 15, the shared attempt ceiling and model-family concurrency, identity, recovery, catalog observation and packaging |

Detailed commands belong in their owning reference, routed from this page and from [skills/buddy/SKILL.md](../skills/buddy/SKILL.md). The previous implementation contracts under `docs/implementation/` were consolidated into these owning references and removed; there is no compatibility copy.

## Accepted design direction

[ADR-020: default loopback access with optional login](decisions/020-loopback-console-access.md) is accepted from the user’s 2026-09-29 decision. The [0.22.0 console and routing acceptance](acceptance/console-routing-fixes-0.22.0.md) covers page scrolling, quiet objective ordering, optional persistent login, and direct selection of a sole legal candidate with frozen routing evidence. Schema stays 15; source verification and any later installation remain separate.

[ADR-018: routing modes and Host workflow](decisions/018-routing-modes-and-host-workflow.md) (accepted; both parts implemented in the 0.21.0 source candidate) splits the Router into a tool-free fast mode that any harness able to disable tools can serve and the existing read-only review mode that needs a verified harness sandbox (review budgets renamed 简要/标准/深入 with `brief`/`standard`/`deep` identifiers, two Router slots persisted in `meta` with a default mode, per-submission mode and automatic fallback to fast routing), and its second part addresses the Claude Code Host trial with a slimmer SKILL.md, file/stdin parameters, per-method help, objective reuse, direct Host closure of integrated work, Host notes and cleanup for failed or cancelled goals, sealed partial artifacts after interruptions, replaceable Host-chosen configurations, Host follow-up paths in integration records and cumulative patches. The Host-workflow part adds native token/quota observations and the four installation, packaging and fixture repairs, with the separately approved schema-15 design. The [Host-workflow acceptance](acceptance/host-workflow-0.21.0.md) maps every trial observation to tests and remaining limits; merge and installation remain separately authorized.

## Accepted design direction, implemented in source

[ADR-017: local installation, on-demand service and harness discovery](decisions/017-local-installation-and-harness-discovery.md) (accepted, implemented in the 0.19.0 source) keeps every Worker on the local machine, starts the single service on demand from any Host but never from inside a Host sandbox, installs and updates through one versioned command that refuses while work is running, and makes the service find and handshake harness executables itself without running shell configuration files or storing environment values.

## Accepted design direction

[ADR-015: shared `.agents` skill distribution](decisions/015-shared-agent-skill-distribution.md) replaces the Codex plugin with one `buddy` skill in `~/.agents/skills` that carries its CLI, linked into `~/.claude/skills` for Claude Code, and supersedes ADR-007's plugin-only distribution rule.

[ADR-013: buddy roles and blackboard terminology](decisions/013-buddy-roles-and-blackboard-terminology.md) names the participants: Host buddies and Worker buddies are peers in standing that interact only through the blackboard and differ in role and authority, while hey-my-buddy names the product. It changes wording only; [CONTEXT.md](../CONTEXT.md) holds the definitions.

[ADR-014: Router buddy and read-only routing](decisions/014-router-buddy-and-read-only-routing.md) accepts the direction of a separate Router role that examines a goal read-only within a budget and whose choice is checked against the routing bounds only, not re-judged. The 0.17.0 source candidate implements the Python half — protocol version 9, frozen candidates, boundary validation, program policyCheck, budgets and input verification — and removes the old DSH decision path; every native read-only structured capability remains unverified, so routing opens the Host boundary and nothing is installed. The [source acceptance record](acceptance/router-read-only-routing-0.17.0.md) owns the current evidence and the Host-filled gaps.

[ADR-012: Bounded control overhead](decisions/012-bounded-control-overhead.md) records light liveness, transient empty polls versus durable allocations, and the user-approved order of subsequent reliability and routing repairs. The [0.10.0 acceptance record](acceptance/runtime-efficiency-0.10.0.md) owns progress and installation evidence.

[ADR-011: Shared model concurrency and verified native interaction](decisions/011-runtime-refinement.md) defines the 0.9.0 source candidate: one machine-wide attempt ceiling with user-owned per-model-family concurrency limits replacing the separate business and decision lanes, and whole-goal artifact verification over immutable Git objects. Its delivery status is owned by the [0.9.0 acceptance record](acceptance/runtime-refinement-0.9.0.md).

[ADR-009: Parallel lanes and local console drafts](decisions/009-parallel-lanes-and-local-console-drafts.md) records independent business/routing capacity — superseded for capacity by [ADR-011](decisions/011-runtime-refinement.md)'s shared ceiling and model-family limits — cooperative worker-pool retirement, and local drafting with short publication grants.

[ADR-010: Production workflow repair plan](decisions/010-production-workflow-repair-plan.md) defines the 0.8 repairs: separated user and maintenance publication, task-local routing preferences, catalog history, recovery and activity, Host-owned workspace lifecycle, native Codex execution and lightweight RPC. The [0.8 acceptance record](acceptance/production-repairs-0.8.0.md) retains that release's observation-only ZCode boundary. The 0.9 source adds a [verified cooperative checkpoint inquiry channel](acceptance/zcode-checkpoint-inquiry.md), without native turn-input injection; [native question](acceptance/zcode-request-input-probe.md) and [private hook](acceptance/zcode-hooks-probe.md) experiments record the alternatives and their limitations.

[ADR-008: Harness-owned evaluation maintenance](decisions/008-harness-owned-evaluation-maintenance.md) was implemented in the 0.7 source and remains current: a skill-equipped Harness performs shared evaluation updates on user request or through its own scheduling facility, only when the user asks for recurring updates. Evidence is shared across source Hosts/projects and read-only in the console. The blackboard retains ordinary business/routing scheduling but does not schedule evaluation maintenance.

[ADR-007: neutral Buddy core and a single current contract](decisions/007-neutral-core-and-single-current-contract.md) is the accepted 0.6 architecture decision. It defines the source layout, the single current contract, durable default routing, the ZCode rebase and the clean-cut release boundary. Current behavior is maintained in the references above; ADR-007 is not rewritten as behavior changes.

[ADR-002: Host-directed assistance and workspace ownership](decisions/002-host-directed-assistance-and-workspaces.md) records the accepted principles for Hosts that both implement and delegate, worker-managed subagents, turn-end yield and continuation, explicit execution workspaces and integration. Its design-time status is retained; supported behavior is maintained in [workflow.md](reference/workflow.md) and [architecture.md](reference/architecture.md).

[ADR-005: shared assessments, table-level exclusion and the console stack](decisions/005-shared-assessments-and-table-exclusion.md) records the policy accepted on 2026-09-22: one shared, bounded current assessment table per user/blackboard with cross-project reuse by default, table-level reader/writer exclusion, single-writer revision/generation fencing, independent control paths, and a React frontend built with Vite. Its original acceptance text records the design-time state; implemented commands and limits are maintained in [evaluation.md](reference/evaluation.md) and [cli.md](reference/cli.md).

## Design proposals

[ADR-019 proposal: Worker accounts, usage visibility and private directories](decisions/019-worker-accounts-and-usage.md) is the user-approved direction for a later goal after ADR-018 Part 2: each harness keeps the shared local login read-only by default or uses a Worker-private account whose credentials the harness itself stores in a service-created private directory (BYOK and OAuth through the harness's own mechanisms, with a bounded OS-credential-store exception), metered or unknown-billing configurations stay out of automatic routing unless the user enables them, and the 0.21.0 usage and quota records become account-aware for display and reminders but are never used for routing. After two installations were stopped by harness links in backed-up attempt directories, its decision 11 separates evidence from harness-private content and attempt credentials, replaces exclusions with an evidence allowlist, cleans up after confirmed stop, tidies existing boards during upgrade and adds a read-only backup preflight; the user approved implementing decision 11 first. It is not implemented; its native facts come from static local checks only.

[Backlog](design/backlog.md) lists user-reported issues that are recorded but not yet in an implementation batch, with the cause when known and a suggested fix.

[ADR-012 proposal: Claude Code integration, a global CLI distribution, evidence-based routing and a macro-task view](decisions/012-claude-code-distribution-and-evidence-routing.md) records the 2026-09-25 discussion: Claude Code as Host and Worker, one machine-wide `buddy` CLI and one skill replacing per-Host plugins with an agent-run staged upgrade, a one-time-token console entry, routing that stays in the blackboard with a Host `routingBrief`, public price tiers and a passively derived delegation outcome ledger, and archived macro tasks shown to people as a derived delegation timeline. The user authorized section XI step 1 (Claude Code Worker P1) on 2026-09-26 and subsequently selected its tested isolated-settings policy; 0.12.0 then implemented and installed the no-schema title-fallback slice of section XI.3. The user subsequently authorized section V's console entry and selected read-only older sessions with write access transferred to a new entry; its 0.13.0 source candidate is described in [console.md](reference/console.md). The remaining design stays a proposal and the other section XII choices remain user-owned. The P1 slice's owning reference is [reference/claude.md](reference/claude.md) and its status is owned by the [Claude P1 0.11.0 record](acceptance/claude-worker-p1-0.11.0.md). This proposal retains its supplied filename and number, distinct from the accepted [ADR-012 performance repair](decisions/012-bounded-control-overhead.md).

[ADR-004 proposal: decision support, evaluation maintenance and the local console](decisions/004-buddy-decision-support-and-console.md) preserves the consolidated discussion of Host/Worker comparative advantage, blackboard and C-Two boundaries, turn-end yield and continuation, explicit workspaces, bounded assessment cards, evaluation maintenance and a local console. Its section III governance choices were resolved by ADR-005; the remaining recommendations retain their proposal status. Its original interface sketches are historical design text. Supported runtime behavior and callable operations are maintained in the [workflow](reference/workflow.md), [evaluation](reference/evaluation.md), [architecture](reference/architecture.md) and [CLI](reference/cli.md) references.

[ADR-003 draft](decisions/003-harness-model-selection.md) is retained as historical discussion; ADR-004 superseded it as the proposal for model selection, and implemented routing is owned by [workflow.md](reference/workflow.md).

## History and evidence

The [0.15.1 installation record](acceptance/installed-0.15.1.md) identifies the daily runtime installed from `f8a0fe8` on 2026-09-27 and its unchanged schema-12 data, user configuration and model limits. The [0.15.0 installation record](acceptance/installed-0.15.0.md) retains the preceding installation evidence. The [0.14.0 installation](acceptance/installed-0.14.0.md) records the earlier verified schema-12 preparation and new logo; the [0.14.0 source acceptance](acceptance/objective-timeline-0.14.0.md), [0.13.0 installation](acceptance/installed-0.13.0.md), [console-entry record](acceptance/console-entry-0.13.0.md) and [brief-output record](acceptance/cli-brief-output-0.13.0.md) retain their separate verification history.

Current delivery evidence: [0.9.0 runtime refinement](acceptance/runtime-refinement-0.9.0.md) (candidate delivery record), [0.8.1 optional execution duration](acceptance/optional-deadline-0.8.1.md), [0.8 daily installation](acceptance/installed-0.8.0.md), [0.8 source repairs](acceptance/production-repairs-0.8.0.md), [console/evaluation 0.7.0](acceptance/console-evaluation-0.7.0.md) and [parallel dispatch 0.6.2](acceptance/parallel-dispatch-0.6.2.md). The 0.9.0 record owns the candidate's source and installation checks; earlier records keep their own verified scope, and the 0.8 installation record identifies the preserved data and unresolved old-artifact integration boundary.

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
| [CONTEXT.md](../CONTEXT.md) | Domain language: the blackboard, Host and Worker buddies, Worker runtimes and delegated-work terms, with the words to avoid |

When behavior changes, update the owning reference first, then the READMEs and [skills/buddy/SKILL.md](../skills/buddy/SKILL.md) if the user-visible entry path changed, and record acceptance evidence under `docs/acceptance/`. Keep architecture statements verified against source; historical ADRs describe the decision at their own point in time.

The [0.15.1 design](design/objective-browser-0.15.1.md) and [acceptance record](acceptance/readonly-objective-browser-0.15.1.md) cover read-only delegation records, objective descriptions, short titles, viewport-based detail browsing and the narrowed console write surface. The record distinguishes implementation, focused/full checks, synthetic browser evidence, normal routed work and installation status.

The [0.16.0 acceptance record](acceptance/maintenance-and-console-0.16.0.md) covers rolling backup, idle upgrade and rollback, guarded storage reclamation, persistent multi-window access, console usability and C-Two 0.6.0. It separates source and test evidence from the separately authorized daily installation.

The [0.16.0 installation record](acceptance/installed-0.16.0.md) identifies the actual daily runtime, verified rolling backup, retained rollback runtime, storage reclamation and stable console URL.

The preceding source delivery is [ADR-017 local installation and harness discovery, 0.19.0](acceptance/local-harness-discovery-0.19.0.md): schema 14, active-runtime launcher, installable wheel, private uv bootstrap and verified macOS Codex Router evidence. The actual daily installation is [0.19.0/schema 14](acceptance/installed-0.19.0.md). Follow [harnesses](reference/harnesses.md), [operations](reference/operations.md) and [Codex](reference/codex.md) for current contracts.

The [Codex monitoring and continuation repair](acceptance/codex-monitor-and-continuation-0.19.0.md) records lightweight monitor configuration, approved native wait/token probes, UTF-8 structured-report validation and recovery of failed native turns. These source changes and the login-name environment fix still require a separately authorized daily installation.

The [ADR-019 first-stage record](acceptance/worker-accounts-phase1-0.23.0.md) covers console copy, cancellation attribution, billing labels, exhaustion eligibility and data-backed review checks; its [native-check plan](design/adr019-native-checks.md) keeps per-check authorization and remaining platform limits explicit.

[ADR-019 decision 11 private-directory source design](design/private-directories.md) inventories each adapter’s evidence, credentials and native content; [0.24.0 acceptance](acceptance/private-directories-0.24.0.md) records private fixtures, invariant coverage and remaining Windows/daily-board boundaries. This prerequisite adds no independent account or login behavior; B phase two stays paused until integration.

[Buddy 配置分区导航 0.25.0](acceptance/buddy-config-sections-0.25.0.md) records the model, Router and Harness sections, deep links, aligned controls and synthetic browser matrix; verification and merge boundaries are recorded there.
[Routing validation and quota recovery](design/routing-validation.md) describes ADR-019 second-stage subbatch ②; its [0.25.0 acceptance](acceptance/routing-validation-0.25.0.md) distinguishes the approved native probe, offline replay and remaining verification from installed behavior.

[Native sandbox review proof](design/native-sandbox-review-proof.md) defines command-independent review validation, fixed native challenges and conservative replay of the Sol/Luna historical diagnostics; [acceptance](acceptance/native-sandbox-review-0.26.0.md) records verification.
