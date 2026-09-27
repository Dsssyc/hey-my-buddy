# Routing and objective-browser repair: 0.15.0 source

## Scope and authority

The user supplied the 0.14.0 review/handoff and authorized R1–R6, A1, C1–C6 and A4/A5. Release and named contract become 0.15.0 because the selection output responsibility, health/snapshot and objective presentation shapes change. Schema stays 12; one partial event index supports read-only routing health and adds no business state. A2 cross-Host enrollment and A3 human reclassification remain proposals in [membership options](../design/objective-membership-options.md). No daily installation or shared model-card/policy/capacity change is authorized by this work. The installed baseline is [0.14.0](installed-0.14.0.md).

## Routing evidence and changes

The Host read the supplied frozen input and helper result for decision task `642ebc0d-1a6d-4581-a181-bdabadaf146f`, attempt `231eb7ac-8dbe-451b-9f4b-5b5912fa8c87`, without changing the daily board. The recorded code is `policy-check-shape`, detail `taskPreference must carry exactly ruleIndex and outcome`. One correction to the handoff: there were two enabled/available candidates, GLM-5.3/max and GLM-5.3-Flash/max; only the former matched the task preference. The original model text was not retained, so copying the input object is a reproduced mechanism, not an established transcript fact.

A failing-before regression reconstructed `{ruleIndex, matchingProfileIds, outcome}` from the input shape. All three pure-policy cases (one candidate without preferences, task preference, user preference) failed with that exact old code. After removing model-owned checks, they pass; a zero-model replay using the recorded two-candidate input selected the preferred GLM-5.3 profile and derived `matched` with the original hard adapter constraint. This proves the validation path, not live routing quality or which words the original model used.

The model now supplies only profile ID, reason, evidence IDs and support. Node and Python derive and record policyCheck independently. Hard candidate restrictions, supplied evidence/support scopes and support for an alternative remain enforced. Redundant check fields cannot override facts or independently reject a valid selection. DSH's implementation is in goal `94ca1a59-1e5c-4863-b6b3-e3b94f41c6ff`; Host corrections additionally aggregate corrective-call usage, recheck the deadline after preparing a handle, redact numeric/unknown/free-form diagnostic content and validate diagnostics again before Python receipt/publication.

At most one format/answer-validation correction shares the request's original deadline, profile and attempt. Valid abstention, quota/infrastructure/input/configuration errors, cancellation, timeout and unsafe/truncated output are not retried. A second invalid answer produces a Host boundary while preserving one task/attempt. Up to two bounded redacted visible-answer diagnostics retain original SHA-256/byte length without reasoning or arbitrary provider text. The [decision reference](../reference/decision.md) owns the exact shape and retry codes.

Read-only routingHealth uses settled selection events, showing the recent 20-event window, failure streak, separate abstention/cancellation/stale counts, at most five failure codes and immutable last-success event time. Explicit Host configurations do not masquerade as successful smart routes. Reads write no events, take no evaluation lease and call no model. The partial kind/sequence index avoids loading task history or raw model messages.

## Host behavior and grouping

The skill now defaults to routing and permits a complete tuple only for an explicit user choice, an established repository division, or same-goal routing recovery. Failure codes must be reported; recovery cannot silently become the default for later tasks. The first delegation for a user agenda creates an objective; subsequent new delegations reuse its ID. Continuations/helpers already inherit it and are not sent unsupported extra fields. Brief submit/get recovery of the identifier is tested. Missing identity is recovered from a known run, not guessed from titles. Existing cross-Host authority restrictions remain.

This agenda itself uses `obj-8c8f2fa7-36fd-433c-8097-eeb2ecf9de74`, with explicit profiles only under the user's repository work division. Ordinary implementation/design delegation is separate from the native routing smoke test, which requires its own approval. The user's uncommitted `production-repairs-0.8.0.md` is excluded from all commits and packages.

## UI design and implementation status

Opus 5.5/high delivered the [interaction design](../design/objective-browser-0.15.md) in goal `5eb630cc-1ac9-4967-ac1a-527f488db83b`, accepted with integration `int-26d37033-b9c1-402e-b39f-d81b41b5ebe3` after Host amendments. The managed checkout was reclaimed. Those amendments keep desktop macro context via side/lower docking, prohibit invented timeline pagination and unreliable duration lower bounds, require non-null same-run event correlation and reuse canonical title/summary DTOs. UI implementation is being performed by GLM-5.3/max in `fff886f3-a980-4119-ac95-e26d746aa1a1`; its delivery, browser evidence and Sonnet review are not yet acceptance claims.

Intent titles now precede result summaries. Standalone groups and timeline rows carry their bounded own summary separately; explicit objectives do not invent an aggregate Worker summary. A regression shows the old `did the work` heading replaced with the original task intent while preserving the result as secondary data.

## Verification so far

Recorded focused passes: 38 Python decision integration tests, 16 pure policy/diagnostic tests, 13 objective tests, 3 offline preparation tests, 9 CLI view tests and 6 current-core checks. Node decision/plugin tests passed 93 tests after Host corrections. The typed read-shape changes passed local TypeScript and 45 affected console tests. Private-state tests verify reader release, unchanged task/attempt counts, no read-side events, supplied support checks, bounded diagnostics and objective identity recovery. A full `buddy.checks` run is in progress at runtime source `321b321`; totals and subsequent frontend/build/staged verification will be recorded only after completion.

No native DSH routing smoke has been run in this repair so far; per-run permission was requested. Mock validation is not presented as live provider acceptance. Installation remains separately gated. Raw packets, logs and replay output are in `.dsh-skill-build/routing-objectives-015/`.
