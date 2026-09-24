# Workspace lifecycle operations

The 0.8 control contract exposes five named Host operations for scope changes, recorded workspace conflicts, integration evidence and cleanup. Each request is a JSON object sent through a named C-Two operation or its CLI name; a browser may use the same operations through the authenticated private console. A Worker credential does not authorize these controls.

| CLI | C-Two operation | Required operation fields |
| --- | --- | --- |
| `scope-amend` | `workflow_scope_amend` | `runId`, `commandId`, `expectedRevision`, `expectedScopeVersion`, `writeScope`, `reason` |
| `workspace-resolve` | `workflow_workspace_resolve` | `runId`, `commandId`, `expectedRevision`, `conflictId`, `action`, `observedFingerprint`; `paths` for selected restore paths, `reason` for adopt or abandon |
| `integration-record` | `workflow_integration_record` | `runId`, `commandId`, `expectedRevision`, `artifactId`; either `notRequired: true` with `reason`, or `strategy`, `target`, and `beforeCommit` for a verified target |
| `workspace-cleanup-plan` | `workspace_cleanup_plan` | `runId`, `commandId`, `expectedRevision` |
| `workspace-cleanup-apply` | `workspace_cleanup_apply` | `runId`, `planId`, `commandId`, `expectedRevision`, `confirmPath` |

Every mutation also requires the current Host control triple: `hostId`, `ownerGeneration`, and `controlToken`. The CLI accepts an exact `controlFile` and injects that triple locally; it prints a path to the private file, not the token. The private console attaches its registered session server-side. Caller-supplied `consoleAuthority` is rejected by HTTP.

Read `get` immediately before each mutation and use its current `revision`. The scope amendment checks the previous `scopeVersion` and records the new `writeScope` only after related attempts and descendants have confirmed shutdown. Conflict resolution binds a recorded `conflictId`, the observed workspace fingerprint and the chosen restore, adopt or abandon action; restore compares the selected paths against the recorded site before changing them. A failed or stale compare leaves the site available for review.

Integration records bind the final artifact to a real target checkout and commit/tree relationship, or record an explicit `notRequired` decision. For a verified integration, `target` names the checkout path and ref, `strategy` names how the artifact was integrated, and `beforeCommit` identifies the prior target commit. `verification` and `adjustedPaths` record Host inspection when needed. An accepted `acknowledge` may name the selected `integrationId`; acceptance requires a verified or not-required record for the final artifact.

Cleanup is a two-step Host decision. The plan names one registered managed checkout, its eligibility evidence, the exact path and the artifacts, manifests and refs to retain. Apply requires that plan's `planId` and an exact `confirmPath`, then rechecks live ownership, revision, shutdown, integration, reservations and Git state before removing the checkout. If the plan is blocked or expired, re-read the run and request a fresh plan. The source checkout and retained evidence are outside the deletion target.
