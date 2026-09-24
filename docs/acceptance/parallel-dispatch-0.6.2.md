# Parallel dispatch acceptance

Date: 2026-09-25. Integration branch: `socu/buddy-core`. Buddy implementation was inspected from fixed output commit `c7d7ebc67c4fa59ba6c9c67c0f9eaeea9d173815` against input `22f05acc7b81ddd7c7965e21de95a016e8ee828d`. Host separately integrated the staged-runtime test fixture and made the private daemon fixture wait for managed worker startup before cleanup. The release keeps contract 0.6.1 and schema 8; no named operation or persisted schema changed in this slice.

## Verified behavior

- Business capacity defaults to 2 and decision capacity defaults to 1. Both count uncertain and unconfirmed attempts. Bounded candidate scans filter by lane before their limit; more than 100 queued entries in either lane cannot conceal runnable work in the other.
- Real private daemons start the expected generic worker pool, execute two business attempts concurrently, and finish routing while business capacity is occupied. Workspace and exclusive-resource conflicts remain serialized.
- Restart preserves the same attempt and worker ownership. Stop reaches recorded pool members. Scale-down drains between attempts, replays receipts at startup and after exceptions, and retains unknown attempts without speculative process restarts. Custom worker IDs are not adopted by name. Explicit worker-stop remains effective until an explicit start or deliberate daemon startup.
- Managed worker paths reject malformed IDs, including `.` and `..`. Exact pool ownership is recorded before spawning. No stored PID is used to adopt or signal a process.
- The staged launcher starts an explicit worker from a stable runtime; replacing the disposable stage does not change that worker's interpreter, imports, bridge or assets.

## Checks

Checks run with `uv run --frozen`, source and test imports supplied by `buddy.checks.test_environment`, and private state/runtime roots. The `test_parallel_dispatch`, `test_ctwo_service` and `test_worker_runtime` modules supplied 36 passing cases. The first command also named three nonexistent modules (`test_store`, `test_worker`, `test_supervisor`), resulting in loader errors; the correct existing modules were then run as `test_blackboard`, `test_workflow_worker` and `test_decision`: 97 tests passed, exit 0. No implementation assertion failed in these runs. The first three modules were not rerun after passing.

The documentation checker validated 29 documents, 201 links, 48 JSON snippets and 36 shell snippets with no errors. `git diff --check` passed. No browser automation or paid test-model requests were used. Earlier Buddy test runs left 13 private test supervisors; Host issued cooperative stop requests only to their verified private test paths and confirmed all lifetime locks were released. The amended private daemon fixture prevents teardown from racing pool startup; the Host test runs produced no new survivors.

The temporary shared-board capacity used during development is not the default policy. Installed runtime identity, actual lane occupancy and the separate full evaluation/console release must be verified operationally; this source-level acceptance does not imply the later schema-9 contract has been installed.
