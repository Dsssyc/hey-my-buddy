# Repository maintenance

This file holds repository facts for anyone changing the code: architecture invariants, layout, verification commands and documentation rules. It deliberately contains no delegation, model, harness, quota or authorization guidance. Product behavior belongs to the shared skill and the references it routes to, and task-specific instructions come from whoever assigns the task.

## Invariants

Read [architecture](docs/reference/architecture.md) and [ADR-007](docs/decisions/007-neutral-core-and-single-current-contract.md) before changing ownership, persistence, worker, routing or recovery contracts. Current source and ADR-007 take precedence over older implementation documents; historical design documents are evidence, not current compatibility requirements.

The Python service owns authoritative SQLite state. Workers own child handles and durable receipts over named C-Two operations. Preserve task/attempt identity, idempotency, transaction/event atomicity, owner fencing, fixed artifact bindings and actual shutdown evidence. Unknown never means stopped. Host and Worker edits require explicit workspace ownership; independent writers use isolated worktrees.

Read [ADR-023](docs/decisions/023-harness-integration-principles.md) before adding a harness, making several harnesses share a behavior, or setting what a role requires of a harness. It requires using each harness's native mechanisms instead of rebuilding its tools, recording differences as declared capabilities, and keeping consistency in normalized evidence that the service judges.

The shared `buddy` skill is the only distribution ([ADR-015](docs/decisions/015-shared-agent-skill-distribution.md)). The source carries no compatibility facades for retired plugin paths, CLI aliases, legacy Node records, fingerprints or socket guards, or historical schema conversions.

## Layout

- `src/buddy/`: public CLI, common service, workflow, evaluation and worker machinery; Python harness adapters under `adapters/`.
- `harnesses/dsh/`: DSH-specific Node scripts, plugins and tests.
- `apps/console/`: React/Vite frontend; built assets are packaged for users without npm.
- `tests/python/`: Python and cross-component verification.
- `docs/reference/`: current operational contracts; `docs/decisions/` and `docs/acceptance/`: decisions and evidence.
- `packaging/`: explicit runtime resources and the shared skill build.
- `skills/buddy/`: the shared agent skill (`SKILL.md` and its `scripts/buddy` launcher).

## Verification

Use uv from the repository root. Prepare the console development dependencies with `npm --prefix apps/console ci` using the Node version supported in `apps/console/package.json`; the preview regression runs the real frontend parsers. The complete check is `uv run --frozen python -m buddy.checks`; focused tests cover a bounded change. The check suite makes no model calls. The Python suite runs one private subprocess per test file with a conservative CPU-based worker count; `--jobs N` or `BUDDY_CHECKS_JOBS` sets it, and `1` restores the original serial single-process run.

Tests use private state and runtime roots created by the test harness, never the daily board. When tests run inside a hey-my-buddy process, clear the inherited runtime, Worker and agent credentials for each test subprocess: `BUDDY_STATE_DIR`, `BUDDY_RUNTIME_ROOT`, `BUDDY_RUNTIME`, `BUDDY_RUNTIME_IDENTITY`, `BUDDY_WORKER_STATE`, `BUDDY_WORKER_ID`, `BUDDY_AGENT_CREDENTIAL`, `BUDDY_AGENT_CREDENTIAL_FILE`, `VIRTUAL_ENV`, `UV_PROJECT_ENVIRONMENT`. `BUDDY_DEV_SOURCE=1` alone cannot override a pinned runtime. A private cold-start test verifies stable interpreter, package and resource paths, including after the source directory is replaced. An installed launcher and service run from their own pinned runtime copy, never from this checkout: editing the source does not change a running installed service, and only an install does.

`main` changes only through pull requests and is protected on GitHub. Enable the shared hooks once per clone with `git config core.hooksPath .githooks`; `.githooks/reference-transaction` keeps local `main` equal to the fetched `origin/main`.

Build the skill with `uv run --frozen python packaging/build-skill.py --destination <separate directory>/buddy`. Installation and upgrade are described in [operations](docs/reference/operations.md). Keep raw local logs and experiment scripts in the ignored `tmp/`.

## Documentation

Each Markdown prose paragraph occupies one source line; preserve structural newlines. Use the domain language in `CONTEXT.md` ([ADR-013](docs/decisions/013-buddy-roles-and-blackboard-terminology.md)): Host and Worker buddies act only through the blackboard, and "Buddy" never names the product, the blackboard or a runtime. Keep setup instructions independent of temporary branches. README and its Chinese counterpart describe purpose and use with language parity. Detailed commands belong in their owning reference, routed from `docs/README.md` and the skill; update the owning page first and the entrypoint summaries when behavior changes.

Record actual verification in `docs/acceptance/`; never present a draft or a worker's success message as acceptance. State the verification boundary in public docs: a capability that is unverified, partially wired or waiting on installation is named as such rather than described as working.

Records, fixtures and saved evidence name locations with `~` or a placeholder, never a machine's absolute home directory or the layout of its project directories; the check suite fails when a tracked file carries those of the machine running it.

Public docs and the shared skill describe product behavior for any user. They do not carry one user's model, harness, quota or workflow choices; those belong in that user's Buddy 配置 or in the instructions of a specific task.
