# C-Two explicit domain repair: microtask 2-A

Baseline: `b2741294296045968f5ff7d92683a8d75802600c`. This is partial verification from the allocated checkout; native socket round trips remain unverified because the execution sandbox refuses bind. No model, account discovery, credential read, installation into the daily runtime, branch/ref operation or commit was performed.

Task root: `/tmp/c2a-8p4Pqv` (canonical alias `/private/tmp/c2a-8p4Pqv`). It is retained for Host collection, including the private uv environment/cache, raw logs, baseline comparison and mutation copies. No manual file or directory cleanup was performed; existing test-framework cleanups ran normally. Every task command session completed; fixture-owned children are waited or observed exited by their existing drivers. No self-created process is known to remain running. Additional process-table inspection was denied by the sandbox (`ps`: Operation not permitted), so the independent process audit is unverified and must be completed by Host before collection. The raw process audit is `process-audit.json` under the task root.

## Changed paths

- `docs/acceptance/c-two-domain-repair.md`
- `src/hey_my_buddy/buddy/harnesses/c_two_live.py`
- `src/hey_my_buddy/protocol/rpc_config.py`
- `tests/python/buddy/harnesses/fixtures/c_two_live_peer.py`
- `tests/python/buddy/harnesses/test_c_two_live.py`
- `tests/python/buddy/harnesses/test_inquiry_owner.py`
- `tests/python/protocol/test_rpc_config.py`

`rpc_config.resolve_state_dir` accepts only the explicit argument or explicitly supplied `BUDDY_STATE_DIR`; otherwise it raises the existing `PRIVATE_STATE_REQUIRED` BoardError before C-Two setup. Endpoint and Channel constructors share that resolver. Endpoint retains the resolved state through registration, independent of later environment changes. Public transport/CLI default resolution is untouched. Windows requires explicit state but passes no custom endpoint root; Windows hardware was not exercised.

RPC path checks retain links, `..` and ordinary-directory guards. Parent ownership/private-mode rules and exact existing-ipc 0700 checks were removed from this module. New state/ipc containers still use mode 0700; existing state 0755 and safe existing ipc modes are left unchanged. `create=False` does not create or chmod. Other business-directory protection was not changed. Native C-Two selects the domain with `set_local_endpoint`; public ValueError/RuntimeError failures there and at actual live registration are chained into existing BoardError. No SDK permission rules or alternate transport were implemented.

Owner-peer now receives `stateDir` in its start command, passes that value into Endpoint, and uses the same self-created state in Channel and client setup. Its child environment clears inherited BUDDY/ANTHROPIC/C2 pins and credentials, supplies private runtime/HOME/TMPDIR, and deliberately carries no BUDDY_STATE_DIR fallback. It checks the SDK root, empty HOME, absent default state tree and empty native TMPDIR after startup and after the answered inquiry. The original admission, native receipts, identity binding, fsync and journal assertions remain. Other live fixtures only pass the new seam or an explicit test-owned construction root.

## Dependencies and isolation

The private uv environment uses CPython 3.13.11 and published C-Two 0.7.4. Every material listed by the supplied provenance was SHA256-checked before installation; installation used `uv pip install --no-index --find-links /tmp/c073-h-x6lhuldm/delegate-materials/wheelhouse --require-hashes -r /tmp/c073-h-x6lhuldm/delegate-materials/locked-dependencies.txt`. C-Two wheel SHA256: `bb625650f186c6066992cac0948c0f44b95a22af46778c8e63bba30d80ee88b8`. `verified-hashes.json`, `setup.py`, and `setup-0.log`/`setup-1.log` retain commands/results; environment creation exited 0 in 0.314 s, locked installation exited 0 in 0.708 s.

The task runner clears inherited `BUDDY_*`, `ANTHROPIC_*`, `C2_*`, `VIRTUAL_ENV`, and `UV_PROJECT_ENVIRONMENT`; it supplies private HOME, runtime, TMPDIR and BUDDY_CHECKS_TMPDIR beneath the task root, and a private C2 fallback only for general probe safety. Owner-peer clears that fallback and observes its private empty TMPDIR. PYTHONDONTWRITEBYTECODE prevents checkout bytecode artifacts. Each module runs in a separate interpreter; no modules with different process-global domains were combined.

## Final source verification

Run commands from `<checkout>` with `python3 /tmp/c2a-8p4Pqv/run.py <label> -m unittest -v <selectors>`. The wrapper invokes `/tmp/c2a-8p4Pqv/env/bin/python`, constructs the isolated environment, and retains `<label>/raw.log` plus `<label>/result.json`. `run-inventory.json` contains every exact argv, source-copy location, exit status and elapsed time. Timings below are wrapper wall time, including interpreter startup.

| Label | Selectors | Items | Exit | Seconds | Evidence boundary |
| --- | --- | ---: | ---: | ---: | --- |
| rpc-final-v2 | `protocol.test_rpc_config` | 24 | 1 | 6.306 | 17 pass; 6 IPC bind failures; 1 private console bind failure |
| live-final-v2 | `buddy.harnesses.test_c_two_live` | 70 | 1 | 14.331 | 52 pass; 18 native bind failures |
| owner-final | `buddy.harnesses.test_inquiry_owner` | 15 | 1 | 13.399 | 13 pass; 2 native bind failures |
| rpc-focused-correct | `protocol.test_rpc_config.ProfileTests protocol.test_rpc_config.DaemonFixtureTests` | 17 | 0 | 1.422 | 17 pass, including real SDK refusal |
| live-focused | `buddy.harnesses.test_c_two_live.WireFrameTests buddy.harnesses.test_c_two_live.ExplicitStateTests buddy.harnesses.test_c_two_live.EndpointAdmissionTests buddy.harnesses.test_c_two_live.EndpointObservationTests buddy.harnesses.test_c_two_live.EndpointLifecycleTests buddy.harnesses.test_c_two_live.CleanupPrimitiveTests buddy.harnesses.test_c_two_live.ReadyMaterialTests buddy.harnesses.test_c_two_live.ChannelUnitTests buddy.harnesses.test_c_two_live.RichReplayIntegrationTests` | 52 | 0 | 1.398 | 52 pass; native lifecycle/deadline classes excluded |
| owner-focused | `buddy.harnesses.test_inquiry_owner.InquiryOwnerTests buddy.harnesses.test_inquiry_owner.InquiryJournalProjectionTests` | 13 | 0 | 1.802 | 13 pass; peer class excluded |

The SDK probe selected 0755 ipc successfully, then registration failed with `Operation not permitted (os error 1)` at bind. Selection alone is not access acceptance. Real registration with ipc 0775/0777 failed with `group and other users must not have write permission`; ipc 0500 failed with `effective user lacks read/write/traverse access`. These SDK refusals run before socket creation and are verified as BoardError with the native RuntimeError cause. `sdk-probe/raw.log` and `sdk-probe-mro/raw.log` retain the actual SDK output. The native error type is a RuntimeError subclass; no vendor implementation was used as static proof.

The seven RPC and eighteen live failures are fully listed in their raw logs. The owner-peer two failures report `PRIVATE_PATH_UNSAFE` with the original `Operation not permitted` cause. The RPC daemon fixture additionally reports `CONSOLE_PORT_IN_USE: Cannot bind 127.0.0.1:0` for its private ephemeral console; this is a sandbox boundary, not an instruction to stop a daily service or change ports. No bind-denial result counts as a successful target mutation.

## Original failures and single-point mutations

The fixed baseline source was copied into `baseline-source/`; regression tests use final test source without changing that baseline implementation. Original owner-peer source was also run before any source edit: `baseline-owner`, 2 items, exit 1, 13.944 s, both blocked at native bind while lacking explicit state. The original private HOME default state tree is retained under that run, never under a daily HOME. Final-test baseline reproduction gives R-01 `BoardError not raised` and R-02 `IPC path is not an owner-private directory`; the separate baseline R-02 real-server attempt preserves its original rejection before native bind.

| Label | Single change / target assertion | Items | Exit | Seconds | Status |
| --- | --- | ---: | ---: | ---: | --- |
| baseline-R01-final | Fixed baseline, missing-root assertion: BoardError not raised (four failures) | 1 | 1 | 1.363 | Target assertion failure; no import/environment error |
| baseline-R02-final | Fixed baseline, 0755 setup assertion fails on old owner-private rejection | 1 | 1 | 1.143 | Target assertion failure; no import/environment error |
| mut-R01-final | Restore default state fallback: BoardError not raised (four failures) | 1 | 1 | 1.312 | Target assertion failure; no import/environment error |
| mut-R02-final | Restore state owner-private refusal: 0755 acceptance assertion fails | 1 | 1 | 1.234 | Target assertion failure; no import/environment error |
| mut-R03-map-final | Remove registration error mapping: native CoreError fails BoardError instance assertion (three modes) | 1 | 1 | 1.429 | Target assertion failure; no import/environment error |
| mut-R03-link-final | Remove linked-component guard: linked-child BoardError assertion fails | 1 | 1 | 1.183 | Target assertion failure; no import/environment error |
| mut-R03-dotdot-final | Remove .. guard: unsafe-component BoardError assertion fails | 1 | 1 | 1.185 | Target assertion failure; no import/environment error |
| mut-R04-root-final | Pass no peer state: started.ok assertion fails with PRIVATE_STATE_REQUIRED before SDK | 1 | 1 | 6.755 | Partial: normal socket green run blocked |

Mutation copies live in `mutations/R01-fallback`, `R02-state-private`, `R03-map`, `R03-link`, `R03-dotdot`, and `R04-peer-root`. Each contains `single-edit.json`, and its raw run directory ends in `-final`. All R-01/R-02 setup/R-03 tests have final green evidence in `rpc-focused-correct`; the R-01 Endpoint/Channel refusal is also green in `live-focused`. R-02 real socket round trip and R-04 complete owner-peer green plus both-side root omission checks still require Host socket access. R-04 here proves SDK-before rejection only, not full red-green acceptance.

Initial R-03 link/.. mutants survived the end-to-end configure assertion because independent directory/SDK guards also refused them. The final retained test directly exercises the existing project structural guard before checking the original end-to-end refusal; both final mutants now fail the intended assertion. The initial R-01 mutation exposed an AttributeError from checking a failed assertRaises context outside its subtest; that test-only mistake was fixed and the final runs contain assertion failures only. `focused-final` also records an accidental nonexistent `TimeoutPropagationTests` selector (18 collected including one loader error, exit 1, 1.384 s); the corrected 17-item run passes. Initial failures/survivors were retained rather than cleaned up.

## Number and assertion preservation

AST test identity comparison is retained in `test-id-diff.json`: rpc 20 → 24, live 69 → 70, owner 15 → 15; removed identity set is empty in every module. Existing V-03/V-04/V-05 labels in the scoped module were retained; R-01…R-04 describe this repair. All owner fsync/receipt/identity assertions remain. The existing `test_bad_state_or_ipc_is_refused_without_repair` name remains: its obsolete 0750-state, exact-0700-ipc and mocked-uid refusal checks are replaced by actual published-SDK rejection of 0775/0777/0500 ipc and structural file/link/.. checks. The 0755 setup and round-trip tests replace the former parent-private boundary with the allowed product behavior. New ipc remains 0700; existing safe modes are not represented as SDK-verified until real I/O succeeds.

## Host integration and remaining verification

Host must pass the owning explicit state into `buddy/roles/run_controller.py::_controller_live`; that public constructor currently supplies neither argument nor a guaranteed explicit environment contract. `buddy/roles/live.py::handle_live_binding` supplies a Unix context-derived state but passes None on Windows; Host must supply owning state there for environments without explicit BUDDY_STATE_DIR. `buddy/runtime/live.py` has injected-client/native-context state selection and raw Worker registration errors that belong to Host review/integration. The blackboard service Channel already supplies `store.directory`. These files and the registry were read only, and no schema, role requirements, tool behavior or stop-evidence change was made.

Host acceptance requires independent interpreter runs of all three target modules with native bind allowed, no model calls, the same private-root isolation, R-02 real 0755-parent/0700-ipc round trip, R-04 owner receipts/identity/fsync assertions and absent private-HOME/default-C-Two artifacts, and root-omission mutations for both registration and connection. Update this same acceptance record with actual Host evidence. Collect the retained task root only after acceptance.

## Complete retained run inventory

Each row maps to `<task-root>/<label>/raw.log` and `result.json`; exact argv is in `run-inventory.json` and the associated result, including all unsuccessful exploratory runs.

| Label | Items / probe | Exit | Seconds | Result |
| --- | ---: | ---: | ---: | --- |
| api-error | probe | 0 | 0.119 | OK |
| api-error-mro | probe | 0 | 0.112 | OK |
| api-inspect | probe | 0 | 0.977 | OK |
| baseline-R01 | 1 | 1 | 1.169 | failures=1, errors=1 |
| baseline-R01-final | 1 | 1 | 1.363 | failures=4 |
| baseline-R02 | 1 | 1 | 1.2 | failures=1 |
| baseline-R02-final | 1 | 1 | 1.143 | failures=1 |
| baseline-owner | 2 | 1 | 13.944 | failures=2 |
| focused-final | 18 | 1 | 1.384 | errors=1 |
| live-final | 70 | 1 | 11.576 | failures=18 |
| live-final-v2 | 70 | 1 | 14.331 | failures=18 |
| live-focused | 52 | 0 | 1.398 | OK |
| mut-R01 | 1 | 1 | 0.937 | failures=1, errors=1 |
| mut-R01-final | 1 | 1 | 1.312 | failures=4 |
| mut-R02 | 1 | 1 | 1.319 | failures=1 |
| mut-R02-final | 1 | 1 | 1.234 | failures=1 |
| mut-R03-dotdot | 1 | 0 | 1.05 | OK |
| mut-R03-dotdot-final | 1 | 1 | 1.185 | failures=1 |
| mut-R03-link | 1 | 0 | 0.959 | OK |
| mut-R03-link-final | 1 | 1 | 1.183 | failures=1 |
| mut-R03-map | 1 | 1 | 0.986 | failures=3 |
| mut-R03-map-final | 1 | 1 | 1.429 | failures=3 |
| mut-R04-root | 1 | 1 | 6.325 | failures=1 |
| mut-R04-root-final | 1 | 1 | 6.755 | failures=1 |
| owner-final | 15 | 1 | 13.399 | failures=2 |
| owner-focused | 13 | 0 | 1.802 | OK |
| profile-green | 9 | 0 | 1.159 | OK |
| rpc-final | 23 | 1 | 4.352 | failures=7 |
| rpc-final-v2 | 24 | 1 | 6.306 | failures=7 |
| rpc-focused-correct | 17 | 0 | 1.422 | OK |
| sdk-probe | probe | 0 | 0.349 | OK |
| sdk-probe-mro | probe | 0 | 0.392 | OK |

`git diff --check` exited 0 after the final source edits. Source changes remain unstaged in the allocated checkout. No self-created process is known to remain running; independent process-table audit is blocked by the sandbox. No retained material was manually removed.
