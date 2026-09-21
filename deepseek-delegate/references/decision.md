# Bounded DSH decision helper

`scripts/decision.mjs` makes one tool-free model call through the installed DSH harness's native LLM and credential services. It returns a recommendation or a proposed card patch; it does not authorize work, change the table, launch a coding agent, or recursively choose its own decision profile. The Python service owns durable orchestration and publication.

```sh
node deepseek-delegate/scripts/decision.mjs \
  --input-file /private/path/request.json \
  --output-file /private/path/result.json \
  --timeout 120
```

Optional arguments are `--dsh-bin` and `--settings-file`. The timeout is 5–1,800 seconds, default 120. The installed `headless` profile must already exist. A temporary overlay disables the ordinary headless agent runner, title-generation and telemetry plugins, and mounts a one-shot decision plugin. The helper inspects the composed profile before boot and rechecks the loaded runner state; an unknown or active runner is refused. It does not install a profile or edit global DSH configuration.

## Input and result

The input object has `operation` (`select` or `maintain`), `requestId`, `profile: {provider,model,effort}`, `tableRevision`, `profiles`, and optional `cards`, `preferences`, `evidence`. Selection requires `task`; maintenance requires `cards` and omits `task`. The fixed profile is supplied by the caller. Effort `off` is passed explicitly to the harness; the helper never silently substitutes another effort or model.

Selection returns `decision: {profileId,reason,evidenceIds}`. A null profile is an explicit abstention. Non-null IDs must belong to the supplied enabled, available candidates; evidence must belong to that profile and have been supplied. Python applies hard pin/exclude and capability constraints before supplying candidates.

Maintenance returns `proposal: {cards,reason}`, where each card contains only `profileId`, `summary`, `strengths`, `limitations`, `risks` and `evidenceIds`. A supplied profile may receive its first card, and a disabled profile may still acquire risk evidence. Unknown profiles, fabricated references, authority changes and extra output fields are refused. This is a proposed patch: Python separately checks the current writer generation, revision and adoption policy.

Both successful envelopes include `status: "ok"`, operation, table revision, requested/resolved configuration, `observed: null`, usage when reported, monotonic elapsed time and shutdown evidence. The installed interface does not prove which exact model version the provider served; resolved configuration never becomes a fabricated observed identity. Provider error messages and stderr do not enter public failure envelopes. Failures use stable machine codes and nonzero exit status; no model call is retried automatically.

The helper accepts at most 200 profiles, 512 cards/evidence entries, 256 preferences, a 512 KiB input file, a 256 KiB rendered model payload, 32 KiB visible model output and 4,096 output tokens. The orchestration layer may impose tighter complete-batch bounds. Oversized or malformed data must be reported, not silently converted into a recommendation. Tool calls and truncated model answers are rejected and never executed.

## Cache and process ownership

An operation-specific system instruction stays constant. The caller supplies deterministic table ordering; model profiles, current cards, preferences and referenced evidence precede revision metadata, task text and request ID. This preserves reusable prefixes across requests and leaves unrelated historical logs out of model input. Prefix stability is not a promised cache-hit percentage: provider cache behavior is measured only when usage reports it.

The helper owns the detached DSH process group and forwards cancellation to that group, escalating when necessary. Reaping the immediate child alone does not establish that the group stopped. The Python adapter must require both the helper's shutdown evidence and observed helper termination; a killed helper with no durable result remains uncertain. Process-group control is POSIX-only, consistent with the rest of Buddy.

The native integration was verified against installed DSH 0.1.5-rc.1: `ctx.llm.listProviders()`, `prepareCall(config, signal)` and the prepared handle's `stream(options)`. The call supplies no tools, coding system prompt, repository context or skills. The harness resolves its existing credentials internally. Mock tests cover protocol failures and real child-process cleanup; live smoke tests establish only the specific calls observed, not general routing or curation quality.
