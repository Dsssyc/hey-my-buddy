# C-Two 0.7.4 live deadlines — scope prerequisite

## 2026-10-10 Worker evidence

Baseline: `ebf8dfb6670d11c838c8957fc5c7db346d7c3480`. This turn has not implemented or accepted V-09/V-10. The frozen five-file write scope excludes additional tests that directly replace the old connect/call implementation or only replace one SDK boundary. Removing the waiting mechanism and using native call options requires migrating those fixtures too; keeping their old path as a compatibility fallback would violate the assigned scope's behavior requirements.

All preparation and raw evidence are retained under `<WORKER_TASK_ROOT>`: `materials/`, `env/`, `cache/`, `tmp/`, `deps-0.log`, `deps-1.log`, `deps-2.log` and `sdk-boundary.log`. The structured turn outcome supplies the exact task root. No source/test changes, model calls, real harness CLI calls, complete checks, IPC probes, process signalling, daily runtime operations or git ref changes were performed.

The supplied provenance was read and all 18 recorded SHA256/size pairs verified before copying: 17 wheels plus `uv.lock`; that lock is byte-identical to the baseline checkout. A private CPython 3.13 virtual environment was created, locked dependencies installed with `uv pip install --require-hashes --no-index --find-links`, then the checkout was synced with `uv sync --frozen --offline --no-index --find-links` into that private environment. All three commands exited 0. Inherited `BUDDY_*`, `ANTHROPIC_*`, `C2_*`, `VIRTUAL_ENV` and `UV_PROJECT_ENVIRONMENT` were removed from preparation/probe subprocess environments; temporary files and cache stayed inside the task root.

An in-process public-SDK boundary probe, without registering or connecting an endpoint, called `cc.with_call_options(EndpointConnection(endpoint()), timeout=0.1)` using the existing `test_live_channel.py` fixture. It raised `TypeError: cc.with_call_options() requires a connected CRM instance`. This is evidence that the existing fixture cannot be passed directly to the required native SDK boundary, not a production failure or a passing regression test. `CallDeadlineExceeded(message, details={"transport_phase": phase})` also exposed the supplied `pre_dispatch` and `dispatch_uncertain` facts; no exception text was parsed for phase inference.

## Additional affected fixtures requiring Host ownership or scope amendment

| File | Current dependency | Required migration |
| --- | --- | --- |
| `tests/python/buddy/harnesses/test_live_channel.py` | `LiveSeamCase.setUp` replaces only `cc.connect` with `EndpointConnection`; 29 cases inherit that fixture | Replace both `cc.connect` and `cc.with_call_options`; preserve request/replay/conflict, owner refusals, observations and selections |
| `tests/python/protocol/test_activity.py` | `bounded_channel` replaces only `cc.connect` with the local endpoint | Replace both SDK boundaries; preserve forwarding, identity and activity assertions |
| `tests/python/buddy/harnesses/zcode/test_native_run.py` | `fixture_live_channel` and the connection-loss case patch `_connect_and_call` | Move local dispatch and connection-loss injection to the two SDK boundaries; preserve native bridge and error assertions |
| `tests/python/buddy/runtime/test_worker_invariants.py` | `local_call` patches `CTwoLiveChannel._connect_and_call` | Move the fixture to the two SDK boundaries; retain readiness/identity and forwarding checks |
| `tests/python/blackboard/tasks/test_inquiry.py` | `OwnerChannel` overrides `_connect_and_call` | Replace the SDK boundaries without retaining an alternate production path; retain real owner settlement, payload conflict and idempotency checks |

`tests/python/buddy/harnesses/dsh/test_native_run.py` and `tests/python/buddy/harnesses/dsh/test_native_resume.py` import the ZCode `fixture_live_channel`; they are affected verification consumers. No independent edits to those files are currently identified. All original related cases still exist and no duration or assertion was changed. The assigned in-scope `test_c_two_live.py` and `protocol/test_inquiry_transport.py` migrations, V-09/V-10 private-process tests and mutation comparisons remain pending.

## Proposed implementation boundary, not acceptance

The client will use one `monotonic() + timeoutMs / 1000` deadline, clamp each remaining SDK budget to zero, pass it to every connection and refresh it for `with_call_options`. Native `CallDeadlineExceeded` will retain the current deadline reason codes. The waiting thread, its outcome/event box and the semaphore used only to bound abandoned waiting threads will be removed. The endpoint's owner queue and settlement event serve a separate business handoff and must remain.

The current server starts a new `timeoutMs` window after receiving a request. The private request wire must carry the original local monotonic deadline (or an equivalently enforced deducted budget) so connection time cannot grant an extra owner consumption window; the consume gate must also enforce expiry before delivering to the owner. This proposed internal field is genuinely consumed and does not require a public CLI or board-schema change. Final choice and verification remain pending continuation; no new wire behavior is claimed here.

Host help is needed to amend the write scope for the five additional fixture files or arrange their migration in the integrated result. Resume the original run after that prerequisite is resolved. Focused original/error cases and new V-09/V-10 tests must pass on the final source, with targeted mutation failures demonstrated; any actual IPC/process sandbox restriction must be recorded and handed to Host without repeated attempts. The 60-second idle-connection probe remains Host-owned and is not added to the test suite.

## 2026-10-10 continuation after Host scope amendment

The Host authorized the original five paths plus the five direct fixture dependencies listed above: ten writable files in total. The previous scope-prerequisite record remains historical evidence; its unimplemented V-09/V-10 status is not rewritten as a past success. This continuation implemented the change within those ten paths. No public CLI/schema, registry, role wiring, harness behavior, installed runtime or git references changed; neither DSH consumer file was edited.

`CTwoLiveChannel._call` now runs synchronously through the native SDK: one `monotonic() + timeoutMs / 1000` cutoff precedes encoding/configuration, every connect receives `max(0, deadline - monotonic())`, and `with_call_options` receives a freshly computed remaining budget after connection. There is no client waiting thread, outcome/event box, semaphore, accumulating-worker limit, custom expiry exception or alternate `_connect_and_call` path. Both SDK boundary deadlines are caught as `c_two.error.CallDeadlineExceeded` and retain `transport-window-expired`; capabilities retains `LIVE_UNAVAILABLE` with that reason. No phase or cancellation fields were added to product DTOs, and no exception text is parsed for transport phase.

The internal `LiveWireRequest` adds `deadlineMonotonic`, a finite nonnegative optional float actually read by the endpoint. The channel always supplies its original local cutoff. Same-machine IPC shares the local monotonic clock; the endpoint uses the earlier of that cutoff and its existing `started + timeoutMs / 1000` window. This prevents connecting and transport dispatch from granting the owner a second full window, while the original strict 100–5000 ms `timeoutMs` bound remains unchanged. Pending queue slots retain this deadline, and `consume_request` rechecks it under the endpoint lock, independently of whether an RPC waiter has run its expiry branch. The owner settlement event, payload conflict checks, request/question idempotency indexes and committed facts remain; client expiry does not cancel a consumed request or prove an owner stopped.

All local fixture migrations explicitly replace both `cc.connect` and `cc.with_call_options`. Scripted SDK deadline cases raise real `CallDeadlineExceeded` objects with declared `pre_dispatch`/`dispatch_uncertain` details, without a substitute timer thread or stub timeout engine. Tests that need an owner/caller thread to exercise the existing business handoff retain those threads. The shared ZCode fixture routes coexisting channels by their own fixture addresses so later fixture creation does not replace the first channel's owner.

### Actual focused verification

Each verification subprocess used the task's private CPython 3.13 environment and cleared inherited `BUDDY_*`, `ANTHROPIC_*`, `C2_*`, `VIRTUAL_ENV` and `UV_PROJECT_ENVIRONMENT`, then supplied private state/runtime/home/temporary roots. The repository-focused commands ran through `uv run --frozen --offline --no-index --find-links <WORKER_TASK_ROOT>/materials/wheelhouse python -m unittest -v`. Logs are retained below the same task root; the structured outcome gives its exact location. Only affected cases were run, without a complete check, model call or real harness CLI.

| Final-source verification selection | Result | Retained log |
| --- | --- | --- |
| `test_c_two_live` wire/admission/observation/lifecycle-wrapper/cleanup-wrapper/ready/channel/replay classes plus `protocol.test_inquiry_transport` | 59 passed, exit 0 | `runs/units-final/output.log` |
| `buddy.harnesses.test_live_channel` and `buddy.runtime.test_worker_invariants.LiveActivityForwardTests` | 41 passed, exit 0 | `runs/live-fixtures/output.log` |
| `blackboard.tasks.test_inquiry` and `protocol.test_activity.WorkerActivityForwarding` | 41 passed, exit 0 | `runs/board-fixtures/output.log` |
| `zcode.test_native_run.ProducerContractTests`, `dsh.test_native_run.InquirySeamTests`, `dsh.test_native_resume.ResumeInquiryTests` | 19 passed, exit 0; the harness processes are test fixtures | `runs/native-fixtures/output.log` |
| Delete the consume-time deadline comparison in an isolated source copy | The new consume race assertion fails, exit 1; no library error | `runs/no-consume-deadline/output.log` |
| Delete both remaining-budget zero clamps in an isolated source copy | The negative-budget target assertion fails, exit 1; no library error | `runs/no-zero-clamp/output.log` |

There are 160 passing affected cases in the four final-source selections above. The initial 57-case unit run preceded two additional unit assertions and is retained at `runs/units/output.log`; it is not counted twice. The two mutation failures prove only their in-process assertions, not V-09/V-10 native timing or transport behavior.

The single native attempt, `NativeDeadlineTests.test_first_connection_expires_pre_dispatch_and_recovers`, failed while registering its private simulated peer: `CoreError: server error: config error: server failed to start: IO error: Operation not permitted (os error 1)`. The deadline test CLI was not launched and no pause/resume signal was sent. This is the IPC sandbox boundary, not the target expiry assertion. No further IPC or process-permission probing was attempted. The retained failure is `runs/native-first/output.log`; V-09/V-10 and the original twelve real-peer lifecycle cases remain unverified here and require Host execution.

### Test migration correspondence

The exhaustive original/current method inventory is retained as `<WORKER_TASK_ROOT>/test-migration.json`, compared using a baseline archive and isolated current copy under the task root. No test method was silently deleted. The five replacements below replace obsolete fixture assumptions with native-boundary checks; other method names, business/error assertions and real-peer durations remain. The artificial sleeps used only to drain abandoned client waiting threads disappeared with that mechanism; native elapsed-time assertions now belong to the real-peer tests. The existing 2.0-second stalling peer, 300 ms windows and 2.2-second remote completion allowance were preserved.

| Baseline case | Current case / coverage |
| --- | --- |
| `WireFrameTests.test_the_request_frame_is_the_request_plus_exactly_three_private_fields` | `test_the_request_frame_carries_the_private_deadline_and_envelope`; original envelope/strict codec assertions plus the consumed internal field |
| `ChannelUnitTests.test_the_whole_connect_and_call_is_bounded_by_the_window` | `test_native_connection_and_call_deadlines_keep_the_existing_taxonomy`; all three RPCs at both SDK boundaries, with timing moved to real peers |
| `ChannelUnitTests.test_bounded_call_slots_report_busy_and_drain_back` | `test_repeated_deadlines_need_no_call_workers_or_busy_slots`; repeated expiry, successful next call, no waiting worker or obsolete permits |
| `SubprocessLifecycleTests.test_a_stalling_endpoint_returns_within_the_window_and_stays_bounded` | `test_a_stalling_endpoint_returns_within_the_native_window`; original request/observe/capabilities expiry and duration assertions, explicit finally stop |
| `TransportTests.test_stalled_sdk_calls_expire_without_reporting_a_stopped_owner` | `test_sdk_deadline_facts_expire_without_reporting_a_stopped_owner`; request/observe expiry facts at both phases, without a fake timing mechanism |

Nine methods were added in `test_c_two_live.py`: `WireFrameTests.test_private_deadline_is_finite_and_keeps_the_public_window_bound`, `EndpointAdmissionTests.test_consume_checks_deadline_even_before_rpc_waiter_marks_expiry`, `ChannelUnitTests.test_budgets_refresh_and_clamp_at_both_native_boundaries`, and the six `NativeDeadlineTests` methods below. The file's defined test-method count changed 60 → 69; `protocol/test_inquiry_transport.py` remains 8. The five additional migrated files retain their original method counts: inquiry 36, live channel 36, ZCode native run 42, Worker invariants 11 and activity 15. Only directly affected selections of those files were executed.

### Native evidence prepared for Host, not yet verified

| Requirement | Private real-peer case | Mutation / expected target failure |
| --- | --- | --- |
| V-09 first connection | `test_first_connection_expires_pre_dispatch_and_recovers` | Delete production connect timeout; owned test-CLI watchdog assertion |
| V-09 already connected | `test_already_connected_peer_expires_pre_dispatch_and_recovers` | Same mutation; owned test-CLI watchdog assertion while an existing connection remains held |
| V-09 zero budgets | `test_zero_connection_and_call_budgets_are_native_pre_dispatch` | Both native zero-budget boundaries must report `pre_dispatch`; subsequent call remains usable |
| V-09 dispatched operation | `test_dispatched_call_expires_but_same_connection_and_remote_call_survive` | Actual `dispatch_uncertain`, same-connection immediate observation and later remote completion; deleting production call options is checked by the original stalling case |
| V-10 unconsumed request | `test_delayed_owner_never_consumes_an_expired_queue_entry` | Delete consume-time expiry gate; target owner-delivery assertion |
| V-10 connection budget | `test_connection_time_does_not_renew_the_owner_queue_window` | Omit original cutoff from private wire; target second-owner-window assertion |

The two connection probes and the connection-budget probe use a short outer watchdog for a saved test-client Popen and pause only the saved peer Popen's process group. Startup and test assertions are inside cleanup protection; nested finally blocks resume the peer, stop/reap the client and normally stop the peer even if the other layer's cleanup fails. Both owned groups must be confirmed gone. The business deadline probe uses a finite stalling peer with a separate immediate same-connection operation and a completed-request fact; it never interprets expiry as cancellation. There is no 60-second idle-connection test; that separate probe remains Host-owned.

`<WORKER_TASK_ROOT>/run-host-native.py` is prepared, not executed. It runs the six new real-peer methods and the twelve original real-peer lifecycle methods on the retained `current/` copy, then runs fresh and already-connected mutations plus call-timeout, consume-expiry and owner-budget mutations in separate source copies. It refuses library/permission failures as mutation evidence and stops on the first unexpected result. Its native temporary roots use `<WORKER_TASK_ROOT>/ht` and short random class directories to leave room for Unix socket filenames, while all logs remain under `host-native/`. The baseline, current and six mutation copies, `source-hashes.json`, all logs and scripts remain under the task root; no shared stash, branch, tag, ref, commit or daily configuration was changed. Host help is now limited to executing these private native checks with IPC/process permissions and returning actual positive/targeted-negative evidence before acceptance.
