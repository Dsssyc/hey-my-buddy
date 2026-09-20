# Repository maintenance

## Invariants

Read the current [architecture](deepseek-delegate/references/architecture.md) before changing the blackboard's ownership, persistence, worker or recovery contracts. The [historical ADR-001](docs/decisions/001-python-transactional-blackboard.md) records the design requirements this implementation was accepted against; it is not a description of current behavior.

The Python service owns authoritative SQLite state. Workers own their child process handles and submit durable receipts over named C-Two operations. Node code belongs to the dsh adapter and its upstream plugins. Preserve task/attempt identity, idempotency, transaction/event atomicity and honest shutdown evidence when changing these boundaries. Unknown is never reported as stopped; lease expiry and missing PIDs are not termination evidence.

## Verification

Manage Python dependencies and environments with uv. Run checks from a full repository checkout (the distributable plugin excludes test suites):

```sh
uv run --project deepseek-delegate --frozen python -m buddy.checks
```

Run focused affected tests for a bounded repair. Service tests use explicit private `BUDDY_STATE_DIR` and `BUDDY_RUNTIME_ROOT`. `BUDDY_DEV_SOURCE=1` suppresses automatic runtime installation; use an empty private runtime root to exercise source. Production cold starts automatically materialize and use a stable versioned runtime. Verify actual artifacts and task outcomes before recording acknowledgement: an RPC response, a queued task or a finished agent alone does not establish acceptance.

Stage a distributable plugin with `uv run --project deepseek-delegate python scripts/stage-plugin.py --destination <separate hey-my-buddy directory>`. Staging refuses a tree that still contains the removed MCP facade or the retired Node engine/job-manager paths, and the destination must be a separate `hey-my-buddy` directory.

## Documentation ownership

Write each Markdown prose paragraph on one source line and let readers wrap it to the window width. Preserve structural newlines for headings, separate list items, tables, code blocks and diagrams. Keep installation instructions independent of temporary development-branch names and merge-status notes.

Use the [documentation index](docs/README.md) to find each page. Keep detailed contracts in their owning document and update it first; entrypoints retain only necessary summaries.

| Topic | Owning document |
| --- | --- |
| Human entry points (install, first request, boundaries) | `README.md`, `README.zh-CN.md` (keep language parity) |
| Document index and routing | `docs/README.md` |
| Install/use paths, task examples, foreground/background flows | `deepseek-delegate/references/usage.md` |
| Implemented architecture, data model, identity, wait separation, recovery, limits | `deepseek-delegate/references/architecture.md` |
| Complete CLI surface, defaults/bounds, envelopes, errors, inquiry contract | `deepseek-delegate/references/cli.md` |
| Runtime lifecycle/upgrade, bridge, state and env vars, legacy import, MCP cleanup | `deepseek-delegate/references/operations.md` |
| Adapters, public client, worker identity/receipts, supervisors | `deepseek-delegate/references/workers.md` |
| Standalone Node runner and its contract | `deepseek-delegate/references/runner.md` |
| Standalone owner-resumption helper | `deepseek-delegate/references/handoff.md` |
| Old service-manual headings and compatibility links | `deepseek-delegate/references/plugin-service.md` |
| Skill entrypoints | `skills/buddy/SKILL.md`, `deepseek-delegate/SKILL.md` |
| Development invariants and this routing table | `AGENTS.md` |
| Historical design rationale | `docs/decisions/001-python-transactional-blackboard.md` |
| Acceptance evidence | `docs/acceptance/` |

Record acceptance changes in `docs/acceptance/` and keep raw local logs in the ignored `.dsh-skill-build/` directory. Update the READMEs, both `SKILL.md` files and the owning reference together when public behavior changes; `plugin-service.md` stays an index, not a second manual.
