# Runtime efficiency and reliability: 0.10.0

## Status

Implementation in progress after the user approved the ordered repairs on 2026-09-25. Source uses contract 0.10.0/schema 11; the daily installation remains the separately verified [0.9.0 installation](runtime-refinement-0.9.0.md). Source changes do not update that running service. No production CPU reduction is claimed here until an actual coordinated installation is recorded.

## Liveness slice

The new private-store regression first failed because three ordinary client observations each triggered full storage integrity scans; the new ping cases also failed because the operation did not exist. After adding the named authenticated ping and moving internal endpoint attachment to it, all five liveness tests passed. The tests require the ordinary service-token ping to perform no storage transaction or runtime inspection, retain authentication/argument validation, surface the stored persistence error honestly, and leave explicit health performing its current integrity check.

The 22 transport-attachment tests and four real cross-process C-Two service tests passed. A direct focused test invocation exposed that the old cold-start fixture tried to install a runtime outside its private fixture when no matching READY build existed; its runtime boundary is now an explicit test double and its temporary directories are explicitly cleaned up. The transport tests still exercise the actual endpoint trust, private-mode and read-only-attachment rules. No real model or daily-board operation was used by these tests.

## Remaining verification

Empty-claim ledger growth, aged-board idle measurements and process cleanup, ZCode tool-error recovery, routing-policy evidence, Host guidance and bounded card maintenance remain under implementation or verification. The final artifact and installation identities will be recorded here after those checks, without overwriting earlier failure evidence or user-owned policy.
