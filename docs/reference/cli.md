# `hey-my-buddy` CLI reference

The CLI presents the Host delegation loop, user operations and credential-scoped role calls separately. Contract 0.61.0 implements [ADR-032](../decisions/032-host-interface-delegation-loop.md). Request fields and legal combinations come from the same models as local `help`; this page explains how to use the entry points rather than copying their complete schemas.

## Invocation

```sh
HMB="$HOME/.agents/skills/hey-my-buddy/scripts/hey-my-buddy"
"$HMB" help
"$HMB" help submit
"$HMB" help operations
"$HMB" help role
```

Call the installed launcher's absolute path. The [operations guide](operations.md#launcher-sandbox-and-environment) explains sandbox access, runtime selection and private state.

## Parameter input

Pass one JSON object as a positional argument, `--params-file PATH`, or `-` for stdin. These forms are mutually exclusive. File/stdin input is UTF-8, optionally with a BOM, and bounded at 8 MiB; its content is parsed directly and never executed by a shell. Omit inapplicable fields: an explicit null is rejected everywhere except the one field whose request model allows it, `harness-set`'s `path`, where null restores automatic harness detection. Help prints the schema's `anyOf`, so a field that shows `null` there is not permission to send one.

```sh
"$HMB" submit --params-file /abs/packet.json
"$HMB" get '{"runId":"<runId>","output":"full"}'
```

## Method help

`help [METHOD]` describes Host requests, `help operations [METHOD]` describes user operations, and `help role [METHOD]` describes execution-credential calls. Help reads local code and starts no service, native harness or model. Fields, bounds and combination rules are generated from `protocol/requests.py`; unknown fields and invalid combinations are rejected with field paths.

## Envelope and exit status

JSON replies carry `contractVersion`. A successful service reply proves that operation's recorded facts, not completed work or acceptance. Errors have an `error` object with a code, message and applicable structured details. Exit 0 means no error envelope, 1 an operation error, and 2 a local usage error. Unavailable service, refused native permission and unknown stop are operational facts, not necessarily malformed input.

## Output views

Most commands accept the CLI-only `output: "brief"` (default) or `"full"`; it is removed before service validation. Brief action receipts carry ids, current state and `actions`. `get` reads the current run or collection; a full run view retains the brief and inquiries, while `includeAudit: true` requests persisted audit detail. Console lifecycle has its own reply. An action's advertised legality is a fact at that moment; re-read after a conflict rather than guessing another id.

## Host commands

The Host has ten verbs plus `help` and `console`. No per-run Host control credential is required. Worker and Lead execution credentials remain distinct, and their allowed calls are confined to their roles.

### Create and amend

`create` establishes a macro task in the project containing `cwd`, with a title and goal; it can have no micro tasks. `amend` addresses its `macroTaskId` and appends a new goal when it differs from the current one. The same current goal returns `alreadyCurrent`. Goal text is stored as written and does not enter Worker or Router input.

```sh
"$HMB" create '{"cwd":"/abs/project","title":"Storage reliability","goal":"Recover every admitted write after a crash."}'
"$HMB" amend '{"macroTaskId":"<macroTaskId>","goal":"Recover every admitted write and verify migration from the upgrade floor."}'
```

The service derives creation identity from project and title. Repeating creation returns the original with `alreadyExists` and does not replace its goal; change the title for a new macro task.

### Submit

`submit` supplies `cwd`, an existing `macroTaskId` or literal `"standalone"`, title, brief, buddy choice, checkout choice, access and expected duration explicitly. `buddy` is `"route"` or a complete recorded catalog buddy name. A Git checkout is `"worktree"` or `"existing"`, captures the current working tree and requires `inputs`, including an explicit empty array. Git `write` or `full` also requires nonempty `writeScope`; `read` forbids it. `checkout: "none"` works directly in `cwd` and forbids both path arrays. `full` requires a single-line `accessNote`; other levels forbid it. Expected duration only drives reminders and routing input.

```sh
"$HMB" submit '{"cwd":"/abs/repo","macroTaskId":"<macroTaskId>","title":"Repair journal recovery","task":"Repair journal recovery in src/journal.py, run the focused checks and return the fixed output. Stop if a necessary change is outside scope.","buddy":"route","checkout":"worktree","access":"write","writeScope":["src/journal.py"],"inputs":[],"expectedSeconds":1800}'
"$HMB" submit '{"cwd":"/abs/research","macroTaskId":"standalone","title":"Check the argument","task":"Review the supplied argument and return specific counterexamples.","buddy":"route","checkout":"none","access":"read","expectedSeconds":900}'
```

Use `fixes` only for repair of an accepted micro task. Explicitly authorized `delegationAuthority: true` requires full access and a named buddy. The service derives submission identity from macro task (or project for standalone work), title and brief. A repeated key returns the existing run with `alreadyExists` and its actual authorization and execution selection; it does not apply new settings. Change the title to redo it.

### Await and get

`await` observes one `runId` for a bounded window, returning a boundary, delivery, reminder or window end. It starts no task and does not stop work when the window ends. [Host waiting](host.md) owns supervision; the [envelope](#await-envelope) lists the response facts.

`get` selects exactly one of `runId`, `macroTaskId` and project `cwd`. A macro task view includes its goal and members, including an empty collection; a project view includes macro tasks and standalone micro tasks. Collections accept pagination, while `includeAudit` and `routingHistory` apply only to a run. Each collection's cursor continues that collection; a run's routing history has its own numeric cursor.

```sh
"$HMB" await '{"runId":"<runId>","waitSeconds":3600}'
"$HMB" get '{"macroTaskId":"<macroTaskId>","limit":20}'
"$HMB" get '{"cwd":"/abs/project"}'
"$HMB" get '{"runId":"<runId>","routingHistory":{"limit":20}}'
```

### Inquire

`inquire` observes a run when given only `runId`, asks a question with `question`, or reads an existing question with its returned `inquiryId`. Question and inquiry id cannot be combined. The service derives question identity from run and question text, so repeating it returns the original inquiry. Native channel capability, delivery, answer and timeout are distinct facts.

```sh
"$HMB" inquire '{"runId":"<runId>","question":"Which check currently blocks completion?"}'
"$HMB" inquire '{"runId":"<runId>","inquiryId":"<inquiryId>"}'
```

### Continue

`continue` answers exactly one returned `requestId`, `artifactId` or `turnId`. A boundary can take `decision: "approve"` or `"decline"`; approved helpers need complete submission packets and explicit `autoContinue`. A declined boundary forbids helpers and that flag. An artifact or failed turn requires new `input`; use the failed turn id only when it has no fixed result. Identical action replays are recognized; another action on the same answered fact conflicts.

```sh
"$HMB" continue '{"runId":"<runId>","requestId":"<requestId>","decision":"decline","note":"The proposed helper is unnecessary; return the current findings."}'
"$HMB" continue '{"runId":"<runId>","artifactId":"<artifactId>","input":"Address the failed acceptance case and rerun the checks.","buddy":"route","note":"Choose an available buddy for this continuation."}'
```

With continuation input, `buddy` selects routing or a complete named buddy; a change requires a note. Access and write scope changes take effect only on the new turn. Live helpers require an explicit `helperPolicy`, absent when none are live. A workspace-site reply uses `requestId`, `resolution: "restore"`, `"adopt"` or `"abandon"`, and the observed fingerprint; adopt and abandon require a note and forbid path selection. Resolution alone opens no turn; adding input continues only after resolution succeeds. [Workflow](workflow.md#host-boundaries-and-requests) and [workspace lifecycle](workspace-lifecycle.md) own these checks.

### Accept, conclude and cancel

`accept` binds the exact `artifactId`, review note and either `target: {path, ref}` or a reasoned `notRequired`. Supply the target's `beforeCommit` unless its tip merge containing the artifact proves it from the parents. Git 2.40 or newer supplies three-way verification. Real changes by the Host require `adjusted` and a note; `hostPaths` names real changes outside artifact paths within the same before/after interval. A micro task without a checkout accepts only `notRequired`.

```sh
"$HMB" accept '{"runId":"<runId>","artifactId":"<artifactId>","note":"Reviewed the fixed output and checked the integrated target.","target":{"path":"/abs/repo","ref":"main"},"beforeCommit":"<before-commit>"}'
"$HMB" accept '{"runId":"<runId>","artifactId":"<artifactId>","note":"Checked the answer against the brief.","notRequired":"Answer-only work; no repository integration."}'
```

`conclude` ends stopped failed, cancelled or delivered work that will not be accepted, with `runId` and a note. Delivered work requires its current delivered `artifactId`. Failed or cancelled work may omit the artifact id, or supply an unanswered fixed result from its current turn; supplying it binds the conclusion to that exact result. `cancel` locates the micro task only by `runId` with an optional note and records ordered stop intent. `targetRunId` on applicable lifecycle calls addresses an authorized helper through its controlling run; it cannot reach a Lead-owned micro task from an ordinary Host. Neither operation bypasses real stop evidence. Cleanup blocked after acceptance or conclusion is retried by the service.

```sh
"$HMB" conclude '{"runId":"<runId>","artifactId":"<artifactId>","note":"Reviewed the delivered output; no further work will be accepted."}'
"$HMB" conclude '{"runId":"<runId>","note":"End the failed or cancelled micro task without binding a fixed result."}'
"$HMB" cancel '{"runId":"<runId>","note":"The user asked to stop this work."}'
```

### Console

`console` opens the loopback console, reads its lifecycle state or closes the named instance. Browser launch and foreground waiting options apply only to opening; an instance identity applies only to closing. [Console access](console.md) and [operations](operations.md) own session and runtime details.

```sh
"$HMB" console '{"action":"open","browser":false}'
"$HMB" console '{"action":"close","expectedConsoleId":"<console-id>"}'
```

## User operations

These top-level commands have separate `help operations` documentation. [Host operations](operations.md#host-operations) says when an authorized Host uses them and what needs the user's agreement.

| Command | Purpose and combinations | Example |
| --- | --- | --- |
| `health` | Full service, persistence, routing and wait-capacity diagnostics; no parameters. | `"$HMB" health '{}'` |
| `harnesses` | Read cached availability; refresh all or one named harness. | `"$HMB" harnesses '{"refresh":true,"harness":"codex"}'` |
| `harness-set` | Select an absolute executable path; null restores automatic detection. | `"$HMB" harness-set '{"harness":"codex","path":null}'` |
| `quota-redetect` | Clear one harness/provider cooldown with its own operation identity; no model or quota probe. | `"$HMB" quota-redetect '{"harness":"dsh","provider":"deepseek-official","requestId":"clear-1"}'` |
| `runtime` | Inspect pinned runtime identity and source leakage, optionally at a destination. | `"$HMB" runtime '{}'` |
| `install` | Install or upgrade this distribution locally; no parameters. | `"$HMB" install '{}'` |
| `paths` | Read installation locations locally; no parameters. | `"$HMB" paths '{}'` |
| `storage-plan` | Generate a private storage plan; no parameters. | `"$HMB" storage-plan '{}'` |
| `storage-apply` | Apply a named plan with confirmation and its own replay identity. | `"$HMB" storage-apply '{"planId":"<planId>","commandId":"storage-1","confirm":true}'` |
| `worker-sessions` | Read proven native session identities; no parameters. | `"$HMB" worker-sessions '{}'` |
| `restart` | Restart the service with optional drain duration and note; executions can outlive it. | `"$HMB" restart '{"note":"Restart the service after the requested repair."}'` |
| `stop` | Stop the service with optional drain duration and note; report unknown shutdown truthfully. | `"$HMB" stop '{"drainSeconds":30,"note":"The user requested a service stop."}'` |
| `relocate` | Record a known marked Git project's new location using only cwd; no stop or cleanup. | `"$HMB" relocate '{"cwd":"/abs/moved-repo"}'` |

```sh
"$HMB" help operations storage-apply
"$HMB" health '{}'
"$HMB" harness-set '{"harness":"codex","path":null}'
"$HMB" relocate '{"cwd":"/abs/moved-repo"}'
```

The storage operation's `commandId` and the cooldown operation's `requestId` remain their own identities. They do not add fields to Host lifecycle requests. [Operations](operations.md#storage-wire-shapes) owns the storage plan and reply forms.

## Role calls

`help role` lists calls authorized by execution credentials. The launcher reads the credential and issuing board from the role's credential file. Worker calls can read their own run and leave `suggest` with a bounded body; scoped inquiry and observation stay within the credential. Lead calls can submit, page through, read, await, inquire about, continue, accept, conclude and cancel their owned micro tasks. The role-only `list` is that Lead's paginated inventory, with `limit` and returned cursor as `before`. A Lead submission takes a named buddy and inherits macro task and ownership from its credential. It cannot grant delegation authority or select a project collection to widen access.

```sh
"$HMB" help role submit
"$HMB" get '{}'
"$HMB" suggest '{"body":"The remaining failure needs a change outside this task scope."}'
"$HMB" list '{"limit":20}'
"$HMB" submit '{"cwd":"/abs/research","title":"Check the cited premise","task":"Check the premise against the supplied evidence and return counterexamples.","buddy":"<complete-buddy-name>","checkout":"none","access":"read","expectedSeconds":900}'
```

The credential-scoped lifecycle verbs use the same response ids and applicable combinations described above; a role cannot widen the selector by adopting a Host request form. A Worker credential cannot act as Host, an expired execution credential is refused, and a Lead cannot control another Lead's tasks or change user settings. [Workflow](workflow.md#delegation-authority-lead) owns the role lifecycle.

## Execution observation

The Host reads run facts with `get` and waits with `await`. Internal execution, message, artifact and event operations remain for the service, execution processes and console where required; their existence does not make them CLI methods. No public entry admits a model-free execution record.

## Messages and inquiry

Use `inquire` for live observation, questions and old answers. Full `get` retains the brief and inquiry history for acceptance review. Native inquiry capability and actual delivery evidence are described in [workers](workers.md#live-channel).

## Evaluation, routing and console

User policy publication, model discovery, buddy catalog/history and detailed routing reads are console-server operations. They are not CLI methods. `get` carries this run's routing facts and bounded routing history, while `submit` and `await` carry actionable routing reasons. Use the console for global settings; [evaluation](evaluation.md) owns its authenticated HTTP surface.

## Errors and recovery

Input errors name the field and legal combinations. `CONFLICT` can mean an answered or superseded fact: read the current run instead of choosing a new response id yourself. `SHUTDOWN_UNCONFIRMED` preserves process uncertainty and held resources. Integration failures name the comparison problem; they do not create acceptance. An execution issue can require an operation described in [Host operations](operations.md#host-operations). A lost creation reply is recovered by resending the same creation input; lost action replies by resending that exact action.

## `await` envelope

The envelope contains `runId`, task `status`, `workflowState`, `outcome`, `actions`, wait duration/window, `timedOut` and aggregate `shutdownConfirmed`. At a Host boundary it carries `requestId` and `requestKind`; a fixed output carries `artifactId`. Applicable replies also include shutdown detail, routing, queue reason, reminders, input failure, execution/service issues and result evidence. A micro task without a checkout can carry `unsealedChanges`.

`delivered` asks for review of the fixed artifact. `waiting-host` asks for a response to its request. `expected-duration` and `native-silence` are reminders; `wait-timeout` and `unavailable` end only this wait. A missing or unreadable terminal receipt can return `result-unavailable`. The advertised `actions` come from the authoritative run view; the envelope supplies no full command templates. This batch adds no new-issue wakeup condition. Follow [host.md](host.md) for all waiting decisions.
