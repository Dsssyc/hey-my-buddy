# ADR-030 批次 4：共用模块两类拒绝的最终处理

本微任务基于 `5fbebe15`（0.30.0），按 ADR-030 第 7 条和拒绝清单中“Host 对待定 45 处的处置”拆开混着不同原因的拒绝。以下是 Worker 的源码修改与局部验证记录，尚待 Host 审查和验收。

## 实时端点注册失败

`buddy/harnesses/c_two_live.py:CTwoLiveEndpoint.start` 与 `buddy/runtime/live.py:WorkerLiveRuntime.start` 将 `cc.register` 的 `ValueError`、`RuntimeError` 统一包装为 `LIVE_ENDPOINT_START_FAILED`。这个名字只断言实时端点启动失败，避免把套接字创建失败等原因误报为路径不安全；消息仍为 `str(error)`，原异常仍通过 `raise ... from error` 保留，两处都继续拒绝。没有新增别名、回退或第二套读法。

全仓库搜索 `PRIVATE_PATH_UNSAFE` 后，未发现生产代码对这两处的旧码做专门的分支、重试限制或不可重试判定。唯一直接断言这里注册失败旧码的测试是 `protocol.test_rpc_config.ProfileTests.test_bad_state_or_ipc_is_refused_without_repair` 的原生注册部分：现拆为 `test_endpoint_registration_refuses_unsafe_ipc_without_repair`，断言替换成新码并补充消息与原异常消息相等；原名继续覆盖结构性路径检查。因此无需修改范围外的消费方。

`WorkerLiveRuntime.start` 失败时的原有 shutdown 与条件 unregister 保留，`bind` 的原有失败处理仍关闭未保留的 channel 并返回 False；它们不按旧码分支。调用点 `roles/run_controller.py:_controller_live` 的 finally 清理和 `runtime/worker.py` 的启动调用不按旧码分支，未修改。`protocol/rpc_config.py`、`private_dirs.py`、`roles/turn_io.py` 等真正的路径检查与其 `PRIVATE_PATH_UNSAFE` 断言全部保留；历史清单和验收文档里提到旧码的文字是当时事实，未改写。

对应测试为 `buddy.harnesses.test_c_two_live.EndpointLifecycleTests.test_registration_failure_preserves_the_start_failure_code_and_cause` 和 `buddy.runtime.test_live.WorkerLiveUnitTests.test_registration_failure_preserves_the_start_failure_code_and_cause`：分别注入 ValueError 与 RuntimeError，底层消息为 `server failed to start: IO error: Operation not permitted`，断言新码、完整消息、原异常对象、未发布 descriptor 和未读取地址；Worker 测试还断言只 shutdown、不 unregister、不产生 attachment。既有 `test_b2_23_failed_register_shuts_own_session_and_does_not_keep_channel` 保留，继续覆盖 bind 的 channel 清理。

路径安全的对应测试为 `protocol.test_rpc_config.ProfileTests.test_bad_state_or_ipc_is_refused_without_repair` 和 `test_linked_state_root_is_refused_with_root_link_diagnostic_before_changes`：普通文件冒充目录、私有树链接和状态根链接仍报 `PRIVATE_PATH_UNSAFE`，原有路径诊断与无副作用断言保留。原生 SDK 对 IPC 权限的注册拒绝测试仍保留全部权限模式和原因断言，等待 Host 运行。

## Router 冻结输入

`roles/router_input.py:prepare` 把原条件拆成两个依次判断的拒绝：绝对路径、`..` 或 `.git` 组件报 `A frozen tree path is unsafe: absolute paths, parent traversal and Git metadata are forbidden`；非 blob 条目报 `Frozen Router input with submodules is not supported`。`git ls-tree -r` 中子模块的类型为 commit。这两类都继续报 `router-input-unavailable`，原有 discard 和 reader 清理逻辑保留；删除了原来的混合条件和 `Unsupported frozen tree entry` 消息，没有新增拒绝范围。

新增 `buddy.roles.test_router_input.FrozenTreeEntryTests` 的 `test_absolute_and_parent_paths_are_refused_as_unsafe_and_cleaned_up`、`test_git_metadata_is_refused_as_unsafe_and_cleaned_up`、`test_submodule_is_refused_as_unsupported_and_cleaned_up`。测试使用私有真实 Git blob 与 cat-file 读取，只替换 manifest 验证和 ls-tree 返回的边界条目，以覆盖 Git 通常不会生成的不安全路径；每次先实际物化 `first.txt`，再触发拒绝，并在真实 discard 前核对内容，最后断言整个冻结目录消失、没有写出越界文件。没有被这次 Router 修改取代的旧消息断言。

## Worker 局部验证

使用 Host 提供的只读解释器，清掉 AGENTS.md 列出的十个继承变量，以本检出的 `src:tests/python` 为 PYTHONPATH；通过现有 `cli.checks.child_environment` 给每个文件建立独立的私有 HOME、harness 配置路径、state/runtime 和临时目录。每个测试文件单独一个 unittest 子进程，未使用 uv、安装依赖、真实 harness、日常看板或模型调用。

| 文件模块 | 本次执行范围 | 结果 |
| --- | --- | --- |
| `buddy.harnesses.test_c_two_live` | WireFrameTests、EndpointAdmissionTests、EndpointObservationTests、EndpointLifecycleTests、CleanupPrimitiveTests、ReadyMaterialTests、ChannelUnitTests、RichReplayIntegrationTests | 55 项通过，退出 0 |
| `buddy.runtime.test_live` | WorkerLiveUnitTests | 30 项通过，退出 0 |
| `buddy.roles.test_router_input` | 全文件 | 3 项通过，退出 0 |
| `protocol.test_rpc_config` | ProfileTests 除下列两项以外的全部测试，以及 DaemonFixtureTests | 15 项通过，退出 0 |

合计 103 项通过。以下 33 项需要原生端点、共享内存或真实服务启动器，按任务环境限制没有尝试运行；没有把这些未运行的测试计作通过。Host 可按文件独立进程补跑完整模块；完整检查、真实回合与故障注入也由 Host 进行。

### `buddy.harnesses.test_c_two_live`

- `NativeDeadlineTests.test_first_connection_expires_pre_dispatch_and_recovers`
- `NativeDeadlineTests.test_already_connected_peer_expires_pre_dispatch_and_recovers`
- `NativeDeadlineTests.test_zero_connection_and_call_budgets_are_native_pre_dispatch`
- `NativeDeadlineTests.test_dispatched_call_expires_but_same_connection_and_remote_call_survive`
- `NativeDeadlineTests.test_delayed_owner_never_consumes_an_expired_queue_entry`
- `NativeDeadlineTests.test_connection_time_does_not_renew_the_owner_queue_window`
- `SubprocessLifecycleTests.test_one_run_one_endpoint_with_a_clean_stop`
- `SubprocessLifecycleTests.test_the_owner_settles_the_real_handoff_with_refusal_then_retry`
- `SubprocessLifecycleTests.test_the_same_name_on_two_addresses_does_not_cross_wire`
- `SubprocessLifecycleTests.test_old_tokens_instances_and_identity_components_are_rejected`
- `SubprocessLifecycleTests.test_wrong_and_oversized_frames_over_real_transport`
- `SubprocessLifecycleTests.test_a_closed_endpoint_refuses_but_keeps_serving_facts`
- `SubprocessLifecycleTests.test_the_per_run_inquiry_limit_over_real_transport`
- `SubprocessLifecycleTests.test_a_killed_endpoint_is_unavailable_and_never_stopped`
- `SubprocessLifecycleTests.test_a_live_endpoint_is_busy`
- `SubprocessLifecycleTests.test_a_credential_for_another_endpoint_is_stale_target`
- `SubprocessLifecycleTests.test_a_committed_request_id_holds_one_real_queue_entry`
- `SubprocessLifecycleTests.test_a_stalling_endpoint_returns_within_the_native_window`

### `buddy.runtime.test_live`

- `WorkerLiveRealPeerTests.test_b2_15_real_owner_journal_commit_precedes_queued_and_observe_carries_facts`
- `WorkerLiveRealPeerTests.test_b2_16_real_controller_owns_replay_and_conflict_not_worker`
- `WorkerLiveRealPeerTests.test_b2_17_real_same_name_different_addresses_and_normal_endpoint_disappearance`
- `WorkerLiveRealPeerTests.test_b2_18_real_full_identity_token_instance_refresh_and_unbind`
- `WorkerLiveRealPeerTests.test_b2_19_real_stalled_source_bound_and_other_holder_remains_usable`
- `WorkerLiveRealPeerTests.test_b2_20_real_unreachable_controller_is_unavailable_not_shutdown_evidence`

### `protocol.test_rpc_config`

- `ProfileTests.test_endpoint_registration_refuses_unsafe_ipc_without_repair`
- `ProfileTests.test_active_root_is_fenced_and_complete_shutdown_allows_new_root`
- `IsolatedTransportTests.test_0755_state_creates_0700_ipc_and_round_trips`
- `IsolatedTransportTests.test_maximum_legal_payload_round_trips_and_over_limit_fails_explicitly`
- `IsolatedTransportTests.test_concurrent_large_transfers_complete_without_pool`
- `IsolatedTransportTests.test_disabled_pool_releases_outgoing_shm_after_real_rpc`
- `IsolatedTransportTests.test_private_root_overrides_inherited_namespace`
- `IsolatedTransportTests.test_different_private_root_cannot_connect`
- `RealDaemonControlTests.test_waits_and_control_operations_share_execution_capacity_without_starvation`
