# Console entry and single-writer sessions: 0.13.0

## Scope and installation boundary

The user requested the next ADR-012 topic after the 0.12.0 installation and selected a new session taking write access while older sessions remain read-only. This candidate implements section V's console entry, not the proposal's macro-task, ledger, routing, global distribution or later Claude phases. Release and named contract advance to 0.13.0; SQLite schema stays 11. The daily installation remains 0.12.0. The [console reference](../reference/console.md) owns the implemented boundaries; installation requires a separate coordinated authorization.

The candidate issues a one-use 60-second link, sets an HttpOnly session cookie on redemption and redirects to a public, credential-free session path. It keeps numeric loopback and cookie-path separation; unverified buddy.localhost resolution is not enabled. A new redeemed session takes write authority, older sessions retain authenticated reads, and mutation dispatch is serialized with handoff. Abandoned publication leases expire normally. An unknown reply stays unknown after permission loss: the client retains its command identity and draft instead of claiming non-commit or automatically replaying it.

The CLI alone launches the browser. browser:false provides the entry for manual opening; wait:true observes the exact console instance without autostart and requests only an identity-fenced console close on Ctrl-C. Session/console inactivity is five minutes; live entry/session collections are bounded. Close and expiry leave ordinary tasks running.

## Verification to date

The Host independently passed 90 focused Python tests covering the CLI, request validation, real daemon console operations, HTTP session/CSRF/origin checks and unchanged schema 11. An additional real-process Ctrl-C test and the complete session test module passed 13 tests: the waiting CLI returned a confirmed console close while the daemon kept its service ID and an existing command task remained active or completed naturally. These use private state/runtime roots, HTTP clients and owned subprocess handles, with no real browser or model probe.

The Host reproduced a frontend stale-handler regression before correcting it: a sibling hook could use its cached writable flag after another component revoked the shared session latch. Dispatch now consults the live latch in the editor, ordinary task and workflow paths. The new regression failed before the correction. The focused frontend run passed 78 tests; the full Vitest suite then passed 244 tests across 22 files, and Node 24 tsc/Vite production build succeeded. npm audit --omit=dev reported zero production dependency vulnerabilities. The bundle hashes are JavaScript index-htL3kjq9.js: 6417ce39b2778c8de97f0245c399e32a067b3f8307502438ee16e38407380154 and CSS index-DPIw79D8.css: 58c86b1bab970f371545e7d3b33f23436c4d99fa1cc3250b4c8283c45cbd8f29.

The required complete buddy.checks run and isolated staged-runtime check are still in progress at this record's initial commit. Their terminal results must be added before claiming complete candidate verification. Raw logs and deterministic probe scripts belong in .dsh-skill-build/console-entry-20260926-S1lA0U/. No Computer Use was performed; HTTP/component verification does not claim Chrome, Firefox or Safari visual acceptance.

## Collaboration and integration

DSH Flash/max implemented two independently scoped goals in parallel worktrees. The Host implemented HTTP/session ownership, serialized handoff, expiry, lifecycle fencing, real-HTTP/process tests, contract coordination and documentation, and reviewed the sealed worker artifacts itself.

CLI goal e7f8158a-53c9-4d44-974f-fed457b9d250 initially returned a8f6db7, which the Host rejected because explicit null could remove a close fence and launch-response validation was too weak. A same-goal correction returned artifact a8b14cc8-003f-4800-9326-3da1cca56363 at 0148910. Its four whole-goal paths exactly match integration target 7427645, verified by int-fa3c448a-0bbd-42bd-8c48-12537ea58d77. Accepted review records the failed first pass and corrections. Cleanup cln-6381588a-1f4d-4e01-b189-d39cc0167476 removed only its registered checkout, retaining both turns' fixed refs, manifests, patches and receipts.

UI goal c62d4982-d8be-4480-bcc8-0b30d941a743 initially returned e17b56d. The Host rejected loss of ambiguous mutation identities on handoff and a queued-renew race; an inquiry also identified missing task-control gating after a failed authenticated poll. The correction returned artifact dac5cc86-c29e-4d3b-ad59-2fbd3aba333f at a3b64ab. Integration int-9c517b87-c8c2-426b-ac41-25300941c769 binds it to 2f92e87 with 21 exact paths and four explicitly declared Host adjustments for the reproduced shared-latch race. Accepted review records these changes and verification. Cleanup cln-da75fd2a-546e-4200-8dd9-ed962333a89e removed only that goal's registered checkout, retaining both logical workspace generations and evidence.

The user's separate uncommitted production-repairs-0.8.0.md is preserved, with SHA-256 0abaca1eb81acecdabdc61075bc5c5480033acae40cd95e101ab9c4e257aa210. User model preferences, annotations, enablement and concurrency limits were not edited. No daily installation, external release or additional Claude model probe was performed for this candidate.
