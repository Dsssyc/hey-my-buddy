# Bounded DSH decision helper

`harnesses/dsh/scripts/decision.mjs` makes one tool-free model call through the installed DSH harness's native `llm` and credential services. It returns a selection recommendation; it does not authorize work, change the table, launch a coding agent, propose card patches or recursively choose its own decision profile. The Python service owns durable orchestration and publication. Decision dispatch now requires a registered adapter's verified `decision_execution` capability; DSH declares it, while ZCode and Codex do not. The fixed selector is never selected recursively.

```sh
node harnesses/dsh/scripts/decision.mjs \
  --input-file /private/path/request.json \
  --output-file /private/path/result.json \
  --timeout 120
```

Optional arguments are `--dsh-bin` and `--settings-file`. The helper's own timeout is 5–1,800 seconds, default 120; the service's durable decision jobs use `timeoutSeconds` 5–1,800 with a default of 300 and pass it through. Exit codes are 0 ok, 1 failure, 2 usage, 3 timeout and 4 spawn failure. The installed `headless` profile must already exist. A temporary overlay disables the ordinary headless agent runner, title generation and telemetry plugins, and mounts a one-shot decision plugin; before the real boot, `dsh --dump-config` must prove the composed tree has the agent runner disabled and the plugin mounted, and the plugin re-checks the same condition from inside the live tree. It does not install a profile or edit global DSH configuration.

## Input and result

The input object has `operation` (exactly `select`), `requestId`, `profile: {adapter,provider,model,effort}`, `tableRevision`, `profiles`, `task`, and bounded `cards`, `preferences`, `annotations`, `evidence` and `routingPreferences`. Annotations carry the user's text with profile, revision and update time, separate from model cards. Task routing preferences are up to eight ordered `{match,reason}` entries and remain soft; they cannot expand the hard candidate set. A `maintain` input is refused as an invalid request: the blackboard no longer executes maintenance model calls, so the helper has no maintenance prompt, no card-patch validation and no `proposal` output. Historical `kind: "maintain"` decision records stay readable through `selection-get`/`selection-list` as archived history (see [evaluation maintenance](evaluation-maintenance.md)). The fixed profile is supplied by the caller. Effort `off` is passed explicitly to the harness; the helper never silently substitutes another effort or model.

Selection returns `decision: {profileId,reason,evidenceIds}`. A null profile is an explicit abstention. Non-null IDs must belong to the supplied enabled, available candidates; evidence must belong to that profile and have been supplied. Python applies hard `pin`/`exclude` and capability constraints before supplying candidates.

There is no maintenance answer shape any more. Card changes are synthesized by an external Harness from a bounded `evaluation-prepare` packet and published through the ordinary evaluation writer gate; the helper never produces or adopts such a patch, and the service never reads a maintenance `proposal` from a model envelope.

A successful envelope includes `status: "ok"`, the operation, table revision, `requested`, `resolved`, `observed: null`, usage when reported, monotonic elapsed time and shutdown evidence. `requested` is the configuration the helper was asked to use and `resolved` is what the native settings readback confirms; the installed interface does not prove which exact model version the provider served, so `observed` stays null and is never fabricated. Provider error messages and stderr do not enter public failure envelopes. Failures use stable machine codes and nonzero exit status; no model call is retried automatically.

The helper accepts at most 200 profiles, 512 cards/evidence entries, 256 published preferences, 200 annotations, eight task routing preferences, a 512 KiB input file, a 256 KiB rendered model payload, 32 KiB visible model output and 4,096 output tokens. The orchestration layer may impose tighter complete-batch bounds. Oversized or malformed data must be reported, not silently converted into a recommendation. Tool calls and truncated model answers are rejected and never executed; the plugin makes exactly one `prepareCall`/`stream` call with no session, no system prompt from the harness and no tool schema.

## Cache and process ownership

An operation-specific system instruction stays constant. The caller supplies deterministic table ordering; model profiles, current cards, published preferences, user annotations and referenced evidence precede revision metadata, task-local routing preferences, task text and request ID. A missing card or annotation leaves capability evidence unknown; it does not establish that an adapter lacks ordinary coding tools. This preserves reusable prefixes across requests and leaves unrelated historical logs out of model input. Prefix stability is not a promised cache-hit percentage: provider cache behavior is measured only when usage reports it.

The helper owns its detached DSH process group and forwards cancellation to that group, escalating when necessary. Reaping the immediate child alone does not establish that the group stopped. The Python `decision` adapter requires both the helper's shutdown evidence and observed helper termination, with a 12-second grace because the helper escalates its own child after roughly 5 seconds and then reaps; a killed helper with no durable result remains uncertain. Process-group control is POSIX-only, consistent with the rest of Buddy.

## Relation to routing and maintenance

A decision task is created only by `selection-request`; it cannot be created through public `execution-submit`. The selector is always the configured fixed decision profile: a published, enabled, available profile whose registered adapter declares verified tool-free decision execution and whose provider/model/effort identity is complete. DSH currently supplies that native implementation. Missing or empty candidates settle as `needs-host` rather than guessing, and the service never selects a selector recursively. Workflow routing passes its hard constraints and task-local soft preferences to the same helper and adopts the returned complete tuple only when every original hard constraint still matches ([workflow.md#configuration-and-routing](workflow.md#configuration-and-routing)).

The selection job runs as an ordinary Worker task with `workspace: false` and `requiredCapabilities: ["decision"]`, sharing the machine-wide attempt ceiling and occupying its decision model's family counter, and owning its helper handle. It admits one bounded selection reader when claimed. Evaluation maintenance is performed by an external Harness: `evaluation-prepare` collects facts without a model, synthesis happens outside the blackboard, and a short card-only patch uses a maintenance writer grant ([evaluation maintenance](evaluation-maintenance.md)).

The native integration was verified against installed DSH 0.1.5-rc.1: `ctx.llm.listProviders()`, `prepareCall(config, signal)` and the prepared handle's `stream(options)`. The call supplies no tools, coding system prompt, repository context or skills. The harness resolves its existing credentials internally. Mock tests cover protocol failures and real child-process cleanup; live smoke tests establish only the specific calls observed, not general routing or curation quality. No live ZCode decision inquiry exists.
