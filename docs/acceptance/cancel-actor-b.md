# Cancellation actor projection, batch B

The source branch projects the first durable `workflow.cancelled` or `workflow.helper_cancelled` event of each run as `cancellation: {actor, reason}`. Host and console labels are derived only from validated event actor values. An absent or malformed actor remains unknown, and no historical event is rewritten. Explicit helper cancellation during Host continuation records that Host as actor. The projection is bounded and appears in governed `get`, task history/detail, and execution `result`; brief CLI views retain it.

Verification from this isolated source worktree: Python workflow cancellation tests (20 passed), CLI view tests (11 passed), console activity tests (14 passed), TypeScript type check and `git diff --check` passed. Test processes used private state and runtime roots and cleared inherited runtime, Worker, agent and venv settings; no native harness or paid model ran. These are source checks, not an installed-runtime claim.
