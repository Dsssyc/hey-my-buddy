# Console entry and browser sessions

The console-entry contract introduced in 0.13.0 implements section V of the [Claude proposal](../decisions/012-claude-code-distribution-and-evidence-routing.md) and the user's choice that a newly opened session takes write access while older sessions remain readable. Version 0.14.0 adds the work-objective view with schema 12. Its separately authorized [daily installation](../acceptance/installed-0.14.0.md) was verified on 2026-09-27; source verification and installation remain separate records.

## Entry and ownership

The CLI retains the single JSON-argument form. `buddy console` opens the default browser from the CLI process. `buddy console '{"action":"open","browser":false}'` returns an entry link without launching a browser. `browser` and `wait` are CLI-only booleans valid for open; they never reach the daemon. A browser-launch failure returns the short-lived link and an honest `browserOpened:false`. Daemon code never launches a browser.

Every open issues a new single-use launch ticket lasting 60 seconds. Creating a ticket does not revoke an existing writer. Redeeming it atomically consumes the ticket, establishes an HttpOnly, SameSite=Strict cookie, transfers console write authority and redirects to a URL without a credential. Used or expired tickets cannot re-establish a session. Status exposes a public console instance ID and running/asset metadata, never a usable entry ticket or session secret.

This slice keeps numeric loopback and a public path namespace instead of relying on unverified `buddy.localhost` resolution in Safari. The final path is `/console/<consoleId>/<sessionId>/`; neither identifier grants access. Each launch-created session has its own cookie path so a later launch does not overwrite an older session's cookie. Every page, asset and API read requires that session's cookie. Copying a final URL to another browser does not authorize that browser. Duplicating a tab at the same authenticated final URL shares that launch-created session; a new writer requires a fresh CLI entry. These are browser-session identities, not OS window identities.

Cookies do not isolate ports, and Path is not a general security boundary: [RFC 6265](https://www.rfc-editor.org/rfc/rfc6265.html) describes those limitations. Exact loopback Host, Origin and Fetch Metadata checks, no wildcard CORS, no-referrer, content security policy and CSRF checks remain required. This is still a same-user service, not isolation from a full-shell local process.

Only the latest redeemed session may mutate the board. Reads through existing POST command routes remain available to older sessions only for evaluation_history, selection_get, selection_list, model_profiles and workflow_get. Every other allowed command requires current writer authority at dispatch. Handoff and command execution are serialized: a mutation already admitted finishes before handoff, and a stale request cannot commit afterward. Authentication and authority are enforced on the server regardless of UI state. The evaluation publication gate remains separate; an old session cannot renew or publish its grant after handoff, and any abandoned short grant expires under the existing 60/120-second lease rules without rewriting a publication.

## HTTP and UI contract

Authenticated HTTP snapshots add `consoleSession: {id, canWrite, reason}`. `id` is the public session identifier; `canWrite` is boolean; `reason` is null for the writer or `superseded` for a read-only session. The session's CSRF token remains separate. Ordinary C-Two snapshots do not grant browser authority. Frontend HTTP validation requires a valid session descriptor and never treats a missing descriptor as write access.

`CONSOLE_READ_ONLY` refuses a mutation from an older authenticated session. `CONSOLE_SESSION_EXPIRED` means the session no longer exists or its cookie is absent/invalid. `CONSOLE_ENTRY_EXPIRED` means the launch ticket is unavailable. Errors do not return the ticket, cookie, CSRF value, service token or a replacement writer credential.

Contract 0.14.0 adds two authenticated GET reads, `/api/objectives` and `/api/objectives/<objectiveId>/timeline`, forwarded to the named `objective_list` and `objective_timeline` operations. Each accepts only its documented query parameters (unknown or repeated names are `INVALID_ARGUMENT`), is available to superseded read-only sessions, and takes no lease, write authority or model call; [objectives](objectives.md) owns the shapes and presentation.

The UI keeps the existing three pages, selections, local drafts and read-only history/detail/routing reads. A superseded window displays a clear read-only message, stops save/renew/retry mutations and disables edit, discovery and task-control actions; it never discards drafts or reloads to gain authority. Theme and navigation remain usable. Opening a fresh entry is the explicit way to acquire a new writer session.

Within 委派记录, 工作目标 groups governed delegations by project and exposes the bounded timeline; 全部执行记录 retains the previous history, including command/external records. Timeline links reuse existing detail and authority checks. Model-card effort variants use display order `default`, `none`, `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, `ultra`; unfamiliar levels follow without being dropped. This ordering changes presentation only and does not rewrite routing or model settings.

A lost mutation reply remains an unknown outcome after handoff. A later read-only refusal proves only that the later request was denied; it cannot establish whether an earlier request with that command ID committed. Keep its staged identity, draft and uncertainty visible without replaying it from the old session. The user can inspect the published state in the new window; a permission change never implies a rollback or a failed publication.

## Lifetime and bounds

Authenticated polling remains every three seconds. Console and session inactivity expire after five minutes without valid activity; unauthenticated probes and status reads cannot keep them alive. Launch tickets and browser sessions are bounded to 16 and 64 respectively, with explicit refusal rather than silent removal of active sessions. Expiry and close revoke registered console authority. Closing a console never stops, cancels or changes a Buddy task.

The lifecycle response includes `consoleId`; open also returns `url`, `expiresAt`, `alreadyRunning` and asset metadata. Close accepts an optional `expectedConsoleId` and cannot close a replacement instance when the expected ID no longer matches. CLI `wait:true` waits for that exact console instance using non-autostart reads; Ctrl-C requests a fenced console close, never a service stop or task cancellation. Browser opening uses Python's [webbrowser API](https://docs.python.org/3.12/library/webbrowser.html); successful process launch does not prove a browser redeemed the ticket.

## Verification boundary

Private real-HTTP tests must cover single-use and expiry races, final-URL access without a cookie, cookie separation, reads and denied writes after handoff, stale write races, cross-origin/header protections, assets, inactivity and close/reopen identity fencing. CLI tests mock browser launch and interrupts and verify no daemon-side browser launch or automatic service restart. React tests cover read-only task controls and retained dirty drafts. The user's no-Computer-Use instruction remains in force; automated HTTP and component tests do not claim Chrome, Firefox or Safari visual acceptance, and unverified hostname behavior is not enabled.
