# Console, bounded evaluations and decision jobs — 2026-09-22

This is the earlier console/evaluation delivery record. The subsequent [0.5.0 productivity acceptance](productivity-workflow-0.5.0.md) adds assistance, continuation, workspace ownership and Host takeover; the original scope and check counts below remain historical evidence.

This record covers the first implementation of the React/Vite console, shared bounded evaluation table, native DSH decision call and durable selection/maintenance workflow. Implementation is isolated on `socu/buddy-console`. The original checkout and installed production runtime were not replaced, and the production v5 database was not migrated. ADR-002 assistance, continuation, workspace transfer and Host takeover remain outside this delivery.

## Delegation and independent acceptance

DSH implemented the Python foundation, native decision helper and durable orchestration in separate worktrees. The Host implemented the React UI, reviewed the contracts and artifacts, integrated the branches and independently reran checks. The DSH delivery commits were `08e63b5`/`c315fc4`/`3b7d8ff` (foundation), `ae740cc` (native helper) and `356ae1f` (decision jobs). These were implementation inputs, not acceptance by themselves.

Review corrections included pending-evidence accounting, provided-field idempotency, no fabricated empty model cards, task controls derived from real attempt evidence, process-group shutdown proof, strict boolean stop evidence, a no-op receipt that does not claim publication, complete bounded candidate input, fixed-size counters, subset card publication, compact Host responses and reference compaction with retained provenance. Current protected risk/limitation strings remain unchanged by automatic maintenance; semantic quality is separately evaluated and is not proved by those checks.

## Reproducible checks

Run from the complete checkout with uv, clearing inherited Worker/runtime variables when necessary:

```sh
env -u BUDDY_STATE_DIR -u BUDDY_RUNTIME -u BUDDY_RUNTIME_IDENTITY -u BUDDY_WORKER_ID -u VIRTUAL_ENV \
  uv run --project deepseek-delegate --frozen python -m buddy.checks
```

The final integrated run exited 0: 237 Python tests and 183 Node tests. Focused regressions additionally checked that a string `"false"` cannot establish shutdown and that a completed maintenance no-op has `publishedRevision:null` in both its public view and persisted Worker command receipt. An earlier integration run caught a merge-marker import error and a pre-helper availability expectation; both were corrected before this passing full run.

Frontend checks under Node 24.21.0 passed: TypeScript typecheck, 14 Vitest tests and the Vite production build. The UI tests cover read-only viewing, retained edit drafts, evidence references, task cancellation/retry boundaries, durable result rendering, ambiguous command receipts and reuse of request identity after a lost response. Both skill entrypoints passed `quick_validate.py`.

Backend coverage includes fair two-tab writers, reader drain, one-slot maintenance, a queued selector reading the new revision, independent cancellation, deadline and restart fencing, uncertain shutdown, receipt replay without another model call, pin/exclude filtering, absence of a configured selector, immutable attributed evidence, reviewed-attempt sample deduplication, constant-size counters and more than 64 sequential observations with bounded current references and archived sources.

## Real browser and Worker observations

The in-app browser first exercised a private real-store fixture without any Worker or paid model. Installed-harness discovery produced 16 proposed configurations; publishing did not create invented assessments. Cancellation worked during an exclusive edit. A task cancelled before being claimed was successfully requeued under the same task identity. Attributed observations preserved project and conditions, and linking one to a published card consumed its pending entry without creating a performance sample.

Responsive checks at 320, 768, 1024 and 1440 pixels found no horizontal document overflow. The final page had no captured browser warnings or errors. Keyboard “skip to main” focused `main` without changing the active settings route. Model lists have bounded scrolling and the published audit detail rendered its real input hash and publication revision.

A separate disposable source-backed daemon and detached Worker, with `maxConcurrent:1`, then exercised the actual C-Two → Worker → native DSH call → result/publication path. The browser submitted the requests through authenticated loopback HTTP; CLI reads saw the same records:

- A fixed `deepseek-flash / off` decision profile recommended the pinned `deepseek-flash / max` execution profile. The two identities remained separate, and no business task was submitted. The default result was a small summary without the table or full input.
- With `autoMaintain:false`, maintenance retained a proposal for the Host and left V1 unchanged.
- With preauthorization enabled, a valid no-op completed without a new publication and retained the unsupported observation as pending.
- After a conditional synthetic React observation was added, a subsequent maintenance job published V3. Both observations were incorporated with explicit uncertainty; the unrelated existing card and user pin survived. Pending count became zero and both cards still had zero verified performance samples.
- All four internal computation tasks completed with confirmed shutdown; the evaluation gate returned to open with no reader or writer left behind.

The live decision was `dec-640f0da5-daea-46d9-a857-5c92bcec2d42`; the live publication was `dec-15b59e62-44ac-49ad-b891-9a07ed323326`. These refer only to the disposable acceptance board, not production work. The final publication input hash was `2b76d6c0a6f358f3f45e76161c6c805778985a9537881b241cf34edbe50463fe`.

## Native model smoke tests and bounds of the evidence

Tool-free `deepseek-official / deepseek-flash / off` smoke calls returned the requested/resolved non-thinking configuration, `observed:null` and confirmed shutdown. Initial helper-reported child execution durations were 2.1 seconds for selection and 1.9 seconds for a scoped maintenance example; these are not end-to-end queue/browser latency measurements.

A 70-record synthetic compaction case exposed two useful failures: the model could paraphrase protected text and, when old evidence was listed first, omit new records. The prompt was clarified to preserve protected strings verbatim and prioritize new sources, and the fixture was aligned with the service's deterministic pending-first ordering. That call kept 50 current references, retained the unresolved incident and all six new references, and kept the old risk/limitation strings unchanged. It reported 3.6 seconds and 896 cache-read tokens. No cache-hit percentage or universal routing-quality claim follows from this small sample; the native usage fields are recorded as reported, not converted into money or a billing budget.

Raw local inputs/results and setup scripts are in the ignored `tmp/` directory. Model calls used synthetic observations and installed public model metadata; no repository source or credential value was included in the decision payloads. None of these fixtures is evidence of real model coding quality.

## Remaining scope

Only the installed DSH native LLM integration is implemented. Automatic community-research ingestion, additional Buddy harness adapters, periodic maintenance scheduling, monetary budgets and ADR-002 task continuation/workspace/takeover are not implemented. The console lists bounded recent records, while older source and publication records remain in the database. Automatic prose curation remains opt-in and probabilistic: structural validation and representative smoke cases do not establish general semantic correctness.
