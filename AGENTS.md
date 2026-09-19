# Repository maintenance

Read [ADR-001](docs/decisions/001-python-transactional-blackboard.md) before changing
the blackboard's ownership, persistence, worker or recovery contracts.

The Python service owns authoritative SQLite state. Workers own their child process
handles and submit durable receipts over named C-Two operations. Node code belongs
to the dsh adapter. Preserve task/attempt identity, idempotency, transaction/event
atomicity and honest shutdown evidence when changing these boundaries.

Keep README.md, README.zh-CN.md, both SKILL.md files and
deepseek-delegate/references/plugin-service.md consistent with public behavior.
Record acceptance changes in docs/acceptance/ and keep raw local logs in the ignored
.dsh-skill-build/ directory.

Manage Python dependencies and environments with uv. Run
`uv run --project deepseek-delegate --frozen python -m buddy.checks` for a complete
check, or focused affected tests for a bounded repair. Service tests use explicit
private BUDDY_STATE_DIR and BUDDY_RUNTIME_ROOT. BUDDY_DEV_SOURCE=1 is an explicit
source-testing override; production cold starts use stable versioned runtimes.

Verify actual artifacts and task outcomes before recording acknowledgement.
An RPC response, a queued task or a finished agent alone does not establish acceptance.
