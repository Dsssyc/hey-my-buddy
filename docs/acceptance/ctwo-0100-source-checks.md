# C-Two 0.10.0 source verification

2026-10-30: Worker verification in an isolated checkout on macOS ARM64 with CPython 3.12.10, for hey-my-buddy 0.58.0 and CONTRACT_VERSION 0.58.0. This records source checks; it is not installation or Host acceptance. The daily service and board were not used, and no model was called.

The dependency is pinned to c-two 0.10.0. The frozen lock has CPython 3.12 wheels for macOS x86_64 and ARM64, manylinux x86_64 and aarch64, and Windows x64 and ARM64. Every applicable locked dependency also has a compatible wheel on those targets; fastdb4py is absent. The upgrade-floor fixture retains its historical dependency metadata.

On Unix a contract-changing cutover removes only the private state/ipc root under the startup fence and both owner locks, after proving the board idle in that same owner-lock interval. Forward startup follows the idle snapshot and backup; backed-up rollback restores the board, proves idle and removes the root again; backed-up recovery takes an idle snapshot even when neither service is running. A pre-cutover abort and recovery without a backup retain the old namespace and its executions, as the Host clarified for this task. Patch installs retain the root, and Windows performs no filesystem namespace removal.

Cold-start preflight uses public Runtime configuration, start and bounded shutdown. It preserves a namespace whenever the public example's shutdown criterion fails, including completed receipts with a barrier, route, client or relay error or an undrained route. The real macOS sandbox bind-denial check observed completed=true with runtime_barrier_error containing Operation not permitted; the launcher therefore returns LAUNCH_IO_ERROR with shutdownOutcome and the retained directory, before materializing a runtime or spawning a service. Clean probes remove coordination files through the pinned directory descriptor and preserve an existing default-runtime connection.

Execution startup publishes the credential returned by server_credential after registration, fails immediately when it is absent, and does not inspect or retry. A real private service/execution test holds the namespace gate and observes successful credential publication before releasing it. Forced startup cancellation reaps the exact owned endpoint with that captured credential after proving its process stopped. The endpoint test also binds the descriptor to the public credential and exercises read and cleanup; on Windows the updated test expects the actual credential to reach reap_endpoint and accepts not-applicable as completed cleanup. The Windows product checks have not been rerun here; the developer-provided Windows 11 C-Two probe recorded schema 1 named-pipe credential round trips and not-applicable/no-filesystem-entry reaping, independently of these source checks.

Each invocation used uv run --frozen python -m hey_my_buddy.cli.checks --jobs 1 with explicit affected test files, sanitized inherited runtime and agent credentials, and the runner's default system-temporary private roots. No complete suite was run. The final passing evidence is distributed across these invocations; passing files were not rerun unless a subsequent change affected them.

| Evidence group | Result |
| --- | --- |
| Execution process and transport errors, first focused run | 2 files, 30 tests, no skips; exit 0 |
| Upgrade and private IPC removal boundary | 2 files, 43 tests, no skips; exit 0 |
| RPC configuration, attachment, state paths, named service, contract signatures, execution delivery, scheduling, recovery and interruption | 10 files passed in the initial 12-file batch, 231 tests with 5 skips; the other 2 files failed and were repaired and rerun below |
| Final real cold-start, C-Two integration and transport errors | 3 files, 22 tests, no skips; exit 0 |
| Installation, migration, rollback, recovery, same-contract execution continuity and check-runner isolation/cleanup | 18 files, 248 tests with 4 skips; exit 0 |

The initial failures were the sandbox test's superseded clean-shutdown expectation and a stale inspection variable in the integration fixture. The diagnostic run established the native shutdown result; the final three-file run passed after correcting the expectation and the fixture. All passing runs observed complete teardown and removed their private roots. Documentation and repository hygiene run against the final staged files; their results, complete command lists, original failure logs and wheel inventory are retained in the checkout's ignored tmp/ctwo-0100-upgrade/report.md and sibling files.
