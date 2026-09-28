# ADR-007: Neutral Buddy core and a single current contract

## Status

Accepted by the user on 2026-09-22. Implementation proceeds from released source `0b674e9`, not the older `6442456` checkout used for ADR-006. This decision supersedes the standalone-distribution and backward-compatibility requirements in earlier implementation documents. Historical decisions remain evidence, not active compatibility promises.

## Source and distribution

The root owns one uv-managed Python project. Public service, workflow, routing, evaluation and CLI modules live in `src/buddy/`; Python harness bindings live in `src/buddy/adapters/`. DSH-specific Node scripts, plugins and tests live in `harnesses/dsh/`. The React/Vite source lives in `apps/console/`, Python and cross-system tests in `tests/`, references in `docs/reference/`, and distributable assembly in `packaging/`.

The only agent-facing skill is the plugin's `skills/buddy/SKILL.md`; the only bundled shell launcher is `bin/buddy`. The standalone `deepseek-delegate` skill, its independent distribution, Node CLI shim, old handoff helper and duplicate read-only dashboard are retired. A plugin build assembles the supported current layout explicitly; installed launchers and runtime assets never locate the project through a removed vendor directory or old-layout fallback.

## Current contract only

The new release is 0.6.0. `CONTRACT_VERSION` remains a single current C-Two version and changes only for actual contract changes. The database accepts one current schema. Remove the Node-record importer, legacy socket guard, old fingerprint algorithm/columns, historical schema migrations and their compatibility-only tests. Keep current process ownership, identity, idempotency, transaction/event atomicity, owner-generation fencing and confirmed shutdown semantics.

Execution records and worker primitives remain current infrastructure for internal decisions and process ownership. The public coding path has one controlled goal lifecycle; old `start`/`run` and standalone aliases do not remain as compatibility redirects. Current public goal commands are `submit`, `get`, `decide`, `continue`, `takeover`, `cancel`, `acknowledge` and `await`. Process/result observation remains distinct where it conveys different information. Harness execution cannot bypass the goal's authorization and workspace rules through an old one-shot public path.

The user chose to retain the old daily board intact as an archive and start the new release from clean state. Do not reinterpret old records at runtime or delete the archive. Switch the daily installation only after all owned implementation/acceptance work has concluded with real shutdown evidence.

## Routing

Host authorization fixes the goal, writable scope, workspace and permitted configuration constraints. A complete explicit configuration is validated and dispatched without a routing-model call. Partial configuration is a hard filter and the configured decision Buddy fills the remaining choice. An unspecified configuration uses the bounded current evaluation table and the fixed decision profile. No silent DSH default is part of public admission.

Admission is durable and idempotent before routing. Replaying one request must not create duplicate selection or execution work. Preserve the original request independently of its resolved execution configuration and record the decision/profile/table revision used. Selection uses the existing worker capacity and table reader/writer gate. Cancellation and owner changes fence late selection results. A missing selector, empty legal candidate set or failed selection produces an actionable Host boundary, not a falsely completed or abandoned goal. The fixed selector is never selected recursively.

Each harness declares execution availability, legal model/provider/effort combinations, model discovery and its supported turn/progress/session features. Unsupported explicit parameters are rejected; requested and observed identities remain separate. Core catalog/routing code must not manufacture `dsh` identities for another harness. Decision-model execution is an explicit capability; coding execution alone does not imply a cheap tool-free decision interface.

## ZCode

Rebase ADR-006 on this contract after the structural and current-DSH checks pass. Preserve its original measurements as historical evidence. Verify per-session model/effort control, native authentication, structured root-turn results and normal shutdown against the installed ZCode version; CLI help flags alone do not establish resume behavior. Do not edit shared model preferences, reimplement OAuth, or turn an `idle` label or free-form prose into a completed goal. A strict structured result must carry the real native session/turn provenance and bind to the authorized attempt; adapter-specific provenance must be reported honestly rather than imitating DSH's tool/flush evidence.

Use a small native bridge only where the harness requires it. A session-private MCP/tool bridge inside a harness is an adapter implementation detail, not a revived blackboard MCP facade. ZCode internal subagents remain available subject to the authorized task scope and the same owned-process termination boundary.

## Execution and acceptance

1. Establish the 0.5.0 source baseline, move ownership boundaries, and verify packaging, CLI startup, imports and console build.
2. Remove historical compatibility paths and duplicate entrypoints; verify a fresh board plus rejection of unsupported old formats.
3. Implement durable default routing, explicit/partial configuration semantics and harness-neutral discovery; verify bounded reads, authority and cancellation races.
4. Verify the existing DSH assistance, parallel-worktree, continuation and acceptance path against the new public interface.
5. Implement and verify the ZCode adapter with native evidence, protocol fixtures, timeout/cancellation cases and a real artifact task; update ADR-006 and current references.
6. Publish only the plugin, remove the independent skill discovery entry recoverably, retain the old board archive, initialize the approved current profiles, and verify the installed runtime and real routed work.

All test subprocesses use explicit private state/runtime roots. Clear inherited `BUDDY_*` runtime/worker/credential variables when starting a test harness; setting `BUDDY_DEV_SOURCE=1` alone does not override a pinned runtime. Do not repeat broad model or regression runs for documentation-only changes. Keep raw local evidence in ignored `tmp/`.
