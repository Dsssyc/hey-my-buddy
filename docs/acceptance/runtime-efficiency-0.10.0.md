# Runtime efficiency and reliability: 0.10.0

## Status

Implementation in progress after the user approved the ordered repairs on 2026-09-25. Source uses contract 0.10.0/schema 11; the daily installation remains the separately verified [0.9.0 installation](runtime-refinement-0.9.0.md). Source changes do not update that running service. No production CPU reduction is claimed here until an actual coordinated installation is recorded.

## Liveness slice

The new private-store regression first failed because three ordinary client observations each triggered full storage integrity scans; the new ping cases also failed because the operation did not exist. After adding the named authenticated ping and moving internal endpoint attachment to it, all five liveness tests passed. The tests require the ordinary service-token ping to perform no storage transaction or runtime inspection, retain authentication/argument validation, surface the stored persistence error honestly, and leave explicit health performing its current integrity check.

The 22 transport-attachment tests and four real cross-process C-Two service tests passed. A direct focused test invocation exposed that the old cold-start fixture tried to install a runtime outside its private fixture when no matching READY build existed; its runtime boundary is now an explicit test double and its temporary directories are explicitly cleaned up. The transport tests still exercise the actual endpoint trust, private-mode and read-only-attachment rules. No real model or daily-board operation was used by these tests.

## Empty claims and aged-board measurement

GLM-5.3-Flash implemented the bounded store slice in goal 295489ba-118e-4125-b959-a0d0ce112741. Host reviewed its sealed two-file patch, integrated it as 4e8507d, and independently passed all six empty-claim regressions: no ledger growth on repeated empty claims, newly eligible work after an empty response, exact positive-claim replay after reopening the store, identity fencing, capacity and workspace exclusion. The delegated focused neighbors also passed: 49 blackboard, 11 store, 4 worker-runtime and 27 parallel-dispatch tests. The output artifact is de80db2f-8323-48c1-b821-afd253d4d86f and the verified integration is int-fc39366f-133d-42f4-86ec-efbcf39c8288; Host acceptance was recorded after inspecting the source and actual measurements.

The same private synthetic benchmark seeded 120,000 empty claim receipts, no task records and seven idle workers. Over an 8-second steady-state interval, the 0.9.0 stable runtime consumed 7.01 daemon CPU seconds and 7.16 total CPU seconds including supervisors, and added 22 empty claim rows. The 0.10.0 source consumed 0.19 daemon CPU seconds and 0.35 total CPU seconds, with zero new command/claim rows and unchanged 36,585,472-byte database size. All seven workers were observed ready; cooperative shutdown completed with zero unresolved attempts and every daemon/worker lifetime lock released. These are one local paired measurement, not a universal throughput promise or a production deployment result. The ignored benchmark script and both reports are retained under .dsh-skill-build/overhead-repair-0.10.0-20260925/.

Test fixture cleanup was repaired separately and integrated as f8a2f33 and 1a928b8. It stops the service in the exact private directory through its authenticated endpoint, including a replacement daemon that a CLI started after restart, waits for real lifetime-lock release, and preserves state on an unconfirmed shutdown before removing any files. Inherited runtime/worker credentials are removed while explicit private test overrides remain usable. The new five-test cleanup suite passed in the integrated 0.10.0 source. The originating isolated regression was also observed to fail with the old cleanup. Existing unrelated processes were not signalled or adopted.

## Remaining verification

ZCode tool-error recovery, routing-policy evidence, Host guidance, bounded card maintenance and final package verification remain under implementation or verification. The final artifact and installation identities will be recorded here after those checks, without overwriting earlier failure evidence or user-owned policy.
