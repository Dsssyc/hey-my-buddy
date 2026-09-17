# Buddy C-Two service


The plugin exposes Python MCP tools backed by PyPI **C-Two 0.5.1**. `uv.lock` fixes the complete Python dependency graph. The latest stable version was checked against PyPI JSON on 2026-09-17; this implementation does not import a sibling C-Two/FastDB checkout. Install with `uv sync --project deepseek-delegate --python 3.12`, then use `uv run --frozen --project deepseek-delegate buddy health`.

## Ownership

The MCP client calls a private `BuddyControl` resource over C-Two direct IPC. One Python daemon owns that resource and one Node execution engine. Node uses built-in modules only and retains the tested dsh runner/process-group behavior. YAML parsing uses uv-managed PyYAML. The engine's parent/child stdio is an internal process boundary; service requests and completion events travel over C-Two.

The daemon never starts, stops or rewrites an unrelated dsh host. Each run uses its own settings copy, logs and dsh process group. Workspace grouping still goes through the existing dsh host bridge, which owns its workspace storage. `BUDDY_MAX_CONCURRENT` defaults to 1, with values 1–8; overlapping canonical working directories are rejected even at higher concurrency. External dsh work still shares machine/provider resources and may edit the same files, so coordinate those separately.

## Completion delivery

`notify` defaults to false. For work within the current turn, use `buddy_wait`.
For background work, the Buddy skill registers an official App heartbeat against
the same task before ending its turn. The heartbeat contains the existing run ID
and request ID, checks the saved run through C-Two, stays quiet while it is running,
then verifies artifacts, acknowledges the result and deletes itself. A matching
heartbeat must be reused rather than duplicated. App scheduling supplies the wakeup;
C-Two supplies status and result communication. This checks periodically, usually
about once a minute; it is not an immediate completion push. App availability,
scheduling and model processing affect latency.

If heartbeat creation fails, keep the current turn waiting or report the blocker.
The scheduler never receives authority to relaunch work. Completed results remain
recoverable by ID, and acknowledged results do not need repeated acceptance.

The separate `notify:true` receiver is experimental and is not used by the supported
workflow. Its native App entry checks the code-signing identity of the connecting
process, parent and grandparent. The uv Python ancestor lacks the required identity,
so the App rejects it. Tool approval cannot grant that trust. The receiver's
`registered`, `received` and `submitted` states are not proof that a task resumed.
Unknown native delivery is never automatically repeated.

## Tools

| Tool | Parameters / behavior |
| --- | --- |
| `buddy_start` | `requestId`, `task`, `cwd`; optional `model`, `provider`, `effort`, `timeoutSeconds`, `workspace`, `notify` |
| `buddy_status` | `runId`; compact execution and notification state |
| `buddy_wait` | `runId`, optional `afterRevision`, `timeoutMs` 0–30000 |
| `buddy_result` | `runId`; actual runner JSON and log paths |
| `buddy_list` | optional `limit` 1–100 and `offset` |
| `buddy_cancel` | `runId`; stops only the owned run |
| `buddy_acknowledge` | `runId`, evidence `note`; records independent acceptance |
| `buddy_dashboard` | private read-only local panel URL |
| `buddy_health` | service health and native channel setup |

Experimental native preflight failure returns an error before launching. With `notify:false`, use active-turn waiting or register the official heartbeat before ending the turn. The CLI accepts JSON parameters, for example `uv run --frozen --project deepseek-delegate buddy list`.

## Persistence and recovery

`BUDDY_STATE_DIR` defaults to `~/.local/share/hey-my-buddy`. Records, endpoint tokens and notification bindings are private. A lifetime file lock gives one daemon ownership of the state directory; a legacy socket guard prevents an old cached Node daemon from starting over the same records. Upgrade after existing runs settle, then stop the old Buddy service normally.

A request ID is an idempotency key. Retry the same task and ID after an uncertain start; changed input is rejected. Results survive service restart. Runs interrupted before shutdown was confirmed are not replayed and block new execution until inspected. No stored PID is used to signal old processes.

`buddy stop` is an administrative CLI action: it gracefully stops Buddy-owned work. It does not stop an unrelated dsh process. An absent service is not launched just to stop it. Complete results and notification records remain on disk.

## Dependencies and validation

All third-party libraries are managed by uv: C-Two, MCP, PyYAML and their locked dependencies. Node.js remains an external runtime required by dsh; native App dispatch uses the runtime bundled with the App. No npm package install is required for Buddy.

```sh
uv sync --project deepseek-delegate --python 3.12
uv run --frozen --project deepseek-delegate python -m buddy.checks
```

Checks include real cross-process PyPI C-Two IPC, notification deduplication and acknowledgement races, MCP reconnect, and the existing mock-dsh process/grouping suite. Live App/dsh integration is verified separately; unit tests never imply that native App delivery was exercised.

## App registration and approval

The portable `plugin.json` and `mcp.json` package uses a contained executable launcher and `${PLUGIN_ROOT}` working directory. The Codex compatibility manifest supplies install metadata. The launcher resolves uv from the normal user/system paths, selects Python 3.12 and uses a version-specific environment under `PLUGIN_DATA` when provided. It does not depend on the calling task's working directory.

The installed plugin provides `buddy_ctwo` directly; no duplicate standalone MCP registration is needed. Tool trust is a user setting, separate from plugin code. For persistent approval of the existing Buddy job lifecycle, put these entries in `~/.codex/config.toml` after the user has authorized them:

```toml
[plugins."hey-my-buddy@personal".mcp_servers.buddy_ctwo.tools.buddy_start]
approval_mode = "approve"

[plugins."hey-my-buddy@personal".mcp_servers.buddy_ctwo.tools.buddy_acknowledge]
approval_mode = "approve"

[plugins."hey-my-buddy@personal".mcp_servers.buddy_ctwo.tools.buddy_cancel]
approval_mode = "approve"
```

For fully unattended heartbeat setup and cleanup, the user can also persist approval
for the official App automation tool. This tool-level policy covers App automation
operations generally; the Buddy skill must still act only on its matching run:

```toml
[plugins."codex-app-tools@openai-bundled".mcp_servers.codex_app.tools.automation_update]
approval_mode = "approve"
```

These policies persist across restarts and plugin version updates while the identifiers stay the same. Existing task connections may retain earlier policies until unloaded and resumed. Verify adoption with a real tool call; parsing the TOML alone is insufficient. An already pending approval is not retroactively approved by changing the file. The App's `Approve for me` option instead evaluates eligible calls through its approval reviewer. Tool approval does not grant the independent process-signature trust needed by the experimental native completion receiver.

To stage a distributable copy without environments, logs, or experiment artifacts, use `uv run --project deepseek-delegate python scripts/stage-plugin.py --destination /path/to/hey-my-buddy`. The destination is separate from the source checkout and prior staged copies are retained as backups.
