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

Integration records bind the final artifact to a real target checkout and commit/tree relationship, or record an explicit `notRequired` decision. For a verified integration, `target` names the checkout `path` and `ref` (plus optional `repositoryId` and `checkoutId`), `strategy` is `patch`, `cherry-pick` or `merge`, and `beforeCommit` is required so the service can resolve the after-commit and both trees from the repository itself rather than trusting a client hash. `verification` (a bounded note) and `adjustedPaths` record Host inspection when the Host adapted the artifact; `reason` is required when `adjustedPaths` is non-empty, and each adjusted path must be one the artifact actually changed. A `notRequired` record takes an explicit `reason`, carries no strategy, target or adjusted paths, and performs no Git work. Calling it again with the identical binding returns the recorded `integrationId` with `duplicate: true`; reusing the same binding for another artifact is a `CONFLICT`. An accepted `acknowledge` may name the selected `integrationId`; acceptance of the final artifact requires a verified or not-required record and otherwise fails with `INTEGRATION_REQUIRED`, and a record verified against a different artifact commit is a `CONFLICT`. `acknowledge` itself takes no `expectedRevision`; it binds the final artifact and the selected integration record to the current delivered goal.

A restore, adopt or abandon resolution performs the Git and filesystem work outside SQLite and records the new `resolved-output` (or `abandoned-site`) artifact without rewriting the failed attempt, its turn or its command receipts. When the only failure was the refused scope seal and that attempt's own native completed outcome still validates, a full restore or adopt also delivers the resolved artifact so the goal can be acknowledged without another paid model turn; a genuine harness failure, a missing turn or unconfirmed shutdown can never be converted into a delivery this way.

Cleanup is a two-step Host decision. The plan names one registered managed checkout, its eligibility evidence, the exact path, its `state` (`planned` or `blocked`), the reason list, and the artifacts, manifests, patches and refs to retain; it expires 900 seconds after creation. Apply requires that plan's `planId` and an exact `confirmPath` equal to the planned `path`, then rechecks live ownership, revision, acceptance, integration, shutdown, open requests, active children, pending continuations, workspace reservations and Git state before removing exactly that checkout. A blocked plan never authorized a deletion (`NOT_READY`), an expired one fails with `PLAN_EXPIRED`, and a repeated apply returns the applied plan with `duplicate: true`. The source checkout, outputs directory, manifests, fixed refs and acceptance evidence are outside the deletion target.

```sh
# Read the run immediately before every mutation and use that current revision.
"$BUDDY" integration-record '{"runId":"<runId>","commandId":"integrate-1","expectedRevision":<get.revision>,"controlFile":"<controlFile>","artifactId":"<finalArtifactId>","strategy":"cherry-pick","target":{"path":"/abs/target/checkout","ref":"main"},"beforeCommit":"<target commit at integration time>","verification":"Applied the sealed artifact commit to the target and ran the combined package checks."}'
# -> { "integrationId": "int-<uuid>", "integration": { "state": "verified", "strategy": "cherry-pick", ... }, "duplicate": false, ... }

"$BUDDY" acknowledge '{"runId":"<runId>","commandId":"review-1","controlFile":"<controlFile>","artifactId":"<finalArtifactId>","integrationId":"<returned integrationId>","note":"Inspected the integrated target diff and ran the relevant checks myself.","verdict":"accepted"}'
# acknowledge takes no expectedRevision; it binds the artifact and the selected integration record.

"$BUDDY" workspace-cleanup-plan '{"runId":"<runId>","commandId":"cleanup-plan-1","expectedRevision":<get.revision>,"controlFile":"<controlFile>"}'
# -> { "plan": { "planId": "cln-<uuid>", "path": "/abs/managed/worktree", "state": "planned", "eligible": true, "reasons": [], "retention": { "artifactIds": [...], "fixedRefs": [...], ... }, "expiresAt": "<ISO-8601>" }, ... }

"$BUDDY" workspace-cleanup-apply '{"runId":"<runId>","planId":"<returned plan.planId>","commandId":"cleanup-apply-1","expectedRevision":<get.revision>,"confirmPath":"<returned plan.path>","controlFile":"<controlFile>"}'
# -> { "removed": true, "retention": {...}, "plan": { "state": "applied", ... }, ... }
```

A task that needed no repository integration records `{"notRequired": true, "reason": "documentation-only artifact with no target change"}` against its final artifact instead of a verified target. The Host still owns the actual integration; a returned patch, a Worker's completion message or a one-line success note is not integration evidence.
