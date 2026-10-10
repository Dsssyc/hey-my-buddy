# 3-C 状态边界整理：范围内交付与待 Host 补核

本记录保留首份未签收候选 `65bd6f1f8d859c7fb0690a4ecefff989d22d7011` / artifact `9acc5797-953d-48ef-a051-f7decae923e5` 的证据，并登记同 run R2 返修、机械范围失败的保全及正式 scope v3 下的 R3 收尾；原补丁 SHA-256 `b04e10f9e6a768cee8718e2d9a5f7602c22a83c99ec46483457f147d1b4604d1`。本记录绑定基线 `5f7e3b2add11936421d70f76951dc1a71aaeddc3`，3-A/3-B 已整合、3-B 生产 `8183fd05`，core 保持 `12eb4fcd`。当前范围内代码与受影响离线聚焦已交付，正式 scope v3 的最后一个 await 调用方已接通；停等 Host 固定产物补核与签收，尚无最终真实 C-Two/Worker 补核结果；本记录不是整批验收，不启动队列后续，不提交或移动 refs。

路由沿用 Host 给出的同 run 恢复：`dec-50783657-382b-46b8-8977-c900b7b3753e` 原 ZCode/zai-api/GLM-5.3-Flash/max，实际 provider 429/1310、rate_limited、retryable=false，Host 输入登记旧回合两层停止、无产物；恢复配置 Codex/openai/gpt-6.1-sol/high，configuration revision 2。本回合未重试 ZCode、未改偏好、未创建 Buddy goal/task/worker；仅有内部 Codex 只读调用审计，未把它称作独立验收。

已读 AGENTS、最新用户补充 3-C、architecture、ADR-007 单一当前契约与 ADR-025 决策 12/13。Python 服务仍拥有数据库权威状态；Worker/controller 仍拥有原进程句柄与停止证据；本批只整理传值与入口选择，不做跨侧清理、schema/CLI 契约变更、harness 行为或 3-B 目录 owner 规则重做。

## 架构边界与仍可选的入口

唯一可选目录解析 helper 为 transport.get_state_dir：显式参数 > BUDDY_STATE_DIR > 默认 HOME，expanduser().resolve()。公开 BoardClient 保留构造时存原参数、call 时选择与注入 call 先短路；自动启动/只读分支共享一次选择后的 Path。call_board/call_service/ensure_service/request_stop 在自己的边界解析，然后复用必填 _call_board/_call_service/_ensure_service；CLI 各分支选择一次并显式传下去；await_run 和 console_cli.run 现为必填 Path，默认服务直接用 _call_service，console 观察客户通过原 BoardClient 注入 call，以标准库 partial 绑定所选 Path；与 BoardClient.call(autostart=False) 共用 transport._call_board_read_only，后者只做一次 attach/缺服务拒绝/_request，不复制只读流程或重新解析。只读/冷启动策略不变，无新公开解析器或兼容包装。

| 可选目录入口 | 实际选择/调用点（仓库内生产来源） |
| --- | --- |
| transport.get_state_dir(state_dir=None) | transport 各公开入口、client.BoardClient.call、cli.main；install/upgrade、install/skill_install、examples/external_worker 的既有外部调用亦保留，未改这些范围外操作。 |
| BoardClient.__init__(state_dir=None)，call 时解析 | buddy/runtime/worker.py、supervisor.py 显式 Path；cli/console_cli.py 显式 Path + 原注入 call；examples/external_worker.py 的既有公开客户调用。注入 call 不选择目录。 |
| transport.call_service(..., state_dir=None) | 对外公开入口；blocking/console_cli 默认服务及普通 cli.main 均已使用必填 _call_service，范围内无生产直接调用。 |
| transport.call_board(..., state_dir=None) | 公开 Python 调用入口；BoardClient 内部已用 _call_board，仓库内无其他生产调用。 |
| transport.ensure_service(state_dir=None) | install/skill_install.py 的既有调用；transport 内部已用 _ensure_service。 |
| transport.request_stop(state_dir=None) | 对外合作 stop/restart API；仓库生产代码无直接调用，daemon/pool 与 Supervisor 的同名方法不是此函数。 |
| cli.main(argv=None) | 可选状态来自已有 Worker 命令 stateDir 或环境/默认；普通 RPC、Worker 启动、控制文件与 submission token 都用所选 Path；await、console 均已传 Path；所有范围内下游消费同一结果。 |

Host 已在 f22d8450 的最新计划明确 blocking/console 为 CLI 内部 helper、catalog.discover 为黑板内部生产接缝。三者现为必填 Path；catalog 删除 state 环境 fallback 和必填后不可达的目录缺失规则，不改账户/模型语义，生产唯一 service 调用传 store.directory。最终改动源码 AST 中可选 state_dir/directory 的函数仅为上述 BoardClient 和 transport 五个入口/helper，CLI 仍通过既有 JSON 参数/环境接受可选状态；最新完整扫描为 `<TASK_ROOT>/optional-state-functions-r3.json`，仍是相同六个可选参数函数；未变公开调用点沿用 `public-call-sites-r2.json`，其中同名 pool 方法不能误当 transport API。

内部 _request/_healthy/_attach_read_only/_cold_start_preflight、rpc_config configure 三入口/_apply、CTwoLiveEndpoint/Channel、roles/live、WorkerLiveRuntime 和 controller execute/_controller_live 都是必填 Path。删除 rpc_config.resolve_state_dir、环境/client.state_dir fallback、PRIVATE_STATE_REQUIRED；context_root 直接读取 context.environment 的必有项，其唯一生产 ExecutionContext 来自 Worker 显式 environment，不是另一外部解析边界。保留私有 IPC/link guard；不加内部 resolve/absolute 或 path 类型运行时证明。Worker 本体直接存 Path。

Daemon、Supervisor、controller 只在进程入口读取父进程给出的 BUDDY_STATE_DIR/既有 --state-dir；缺失立即退出 2，不选默认。controller execute/_controller_live 显式接收该 Path。已接线范围内 fixture 与 Python 直接 execute 调用；原生请求、schema、harness preparation/结果语义不变。

LiveWireRequest.deadline_monotonic 是无默认的必填有限非负 float；唯一 wire sender 在创建 frame 前得到原截止时刻，_call 使用这个值给 cc.connect(timeout)/with_call_options 剩余预算。Worker relay 经内部 CTwoLiveChannel._request 转发同一截止时刻，过期/缺字段在 Worker server 就拒绝；controller endpoint 用原截止时刻与公开窗口的较小值，不再容忍缺字段。不解析错误文字，不造 call 等待线程；内部 relay 相信已验证 frame。

_healthy 仅将 SERVICE_UNAVAILABLE 当作没应答，其余异常原样传播。读不到可信 endpoint 就返回 None；可信 endpoint 的 POSIX ipc 缺失用直接条件，Windows Named Pipe 不要求 ipc 目录，dangling ipc link 仍到现有结构拒绝边界。preflight 仅 OSError 写入/IPC拒绝映射 LAUNCH_ACCESS_DENIED，3-B code/message/path 保真与不读 BUDDY_CHECKS_TMPDIR 保留。

## 最新 changedPaths（56，正式 scope v3 的 67 路径内）

首份 42 路径候选与 R2 的 55 路径候选保留。R2 相对首份有 20 路径变化、含 13 个新增许可路径，但当时机械 scope 仍为 v1，封存失败；Host 核对失败现场后 adopt 保全，之后才正式记录 v2/v3，本轮使用实际 v3/67。原 submission intent 53 不变，没有自行修改 manifest/SQL。R3 相对 adopt 的 f862 固定候选实际变化 6 路径（5 个 Python 文件与本记录），累计 56 路径。完整列表为 `<TASK_ROOT>/changed-paths-r3.json`，差集为 `turn-delta-r3.json`。

- `docs/acceptance/c-two-state-boundaries.md`
- `src/hey_my_buddy/blackboard/catalog/catalog.py`
- `src/hey_my_buddy/blackboard/service/daemon.py`
- `src/hey_my_buddy/buddy/harnesses/c_two_live.py`
- `src/hey_my_buddy/buddy/roles/live.py`
- `src/hey_my_buddy/buddy/roles/run_controller.py`
- `src/hey_my_buddy/buddy/roles/run_execution.py`
- `src/hey_my_buddy/buddy/roles/structured_call.py`
- `src/hey_my_buddy/buddy/runtime/live.py`
- `src/hey_my_buddy/buddy/runtime/supervisor.py`
- `src/hey_my_buddy/buddy/runtime/worker.py`
- `src/hey_my_buddy/cli/blocking.py`
- `src/hey_my_buddy/cli/console_cli.py`
- `src/hey_my_buddy/cli/main.py`
- `src/hey_my_buddy/private_dirs.py`
- `src/hey_my_buddy/protocol/client.py`
- `src/hey_my_buddy/protocol/rpc_config.py`
- `src/hey_my_buddy/protocol/transport.py`
- `tests/python/blackboard/catalog/test_account_operations.py`
- `tests/python/blackboard/catalog/test_catalog.py`
- `tests/python/blackboard/evaluation/test_evaluation.py`
- `tests/python/blackboard/routing/test_stage2_review_scope.py`
- `tests/python/blackboard/service/test_service_environment.py`
- `tests/python/blackboard/tasks/test_inquiry.py`
- `tests/python/blackboard/tasks/test_workflow_stop_surface.py`
- `tests/python/blackboard/tasks/test_workflow_worker.py`
- `tests/python/blackboard/tasks/test_workspace_api.py`
- `tests/python/buddy/harnesses/claude/test_native_run.py`
- `tests/python/buddy/harnesses/codex/test_native_run.py`
- `tests/python/buddy/harnesses/dsh/test_native_run.py`
- `tests/python/buddy/harnesses/fixtures/c_two_live_peer.py`
- `tests/python/buddy/harnesses/test_c_two_live.py`
- `tests/python/buddy/harnesses/test_live_channel.py`
- `tests/python/buddy/harnesses/zcode/test_native_run.py`
- `tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py`
- `tests/python/buddy/roles/test_registered_review.py`
- `tests/python/buddy/roles/test_registered_run_wiring.py`
- `tests/python/buddy/roles/test_role_live_seam.py`
- `tests/python/buddy/roles/test_schema_worker.py`
- `tests/python/buddy/runtime/fixtures/live_runtime_peer.py`
- `tests/python/buddy/runtime/test_live.py`
- `tests/python/buddy/runtime/test_worker_invariants.py`
- `tests/python/buddy/runtime/test_worker_runtime.py`
- `tests/python/cli/test_blocking.py`
- `tests/python/cli/test_cli.py`
- `tests/python/cli/test_cli_views.py`
- `tests/python/cli/test_host_cli.py`
- `tests/python/console/test_console_cli.py`
- `tests/python/protocol/test_activity.py`
- `tests/python/protocol/test_inquiry_transport.py`
- `tests/python/protocol/test_path_state.py`
- `tests/python/protocol/test_public_state.py`
- `tests/python/protocol/test_rpc_config.py`
- `tests/python/protocol/test_state_boundaries.py`
- `tests/python/protocol/test_transport_attach.py`
- `tests/python/test_private_directories.py`

## 首份固定候选的聚焦命令与实际结果（历史）

每个测试文件独立解释器，uv 离线私有环境；标准命令为 `python3 <TASK_ROOT>/run.py <CHECKOUT> <label> -m unittest -v <下表选择>`。run.py 实际执行 `<TASK_ROOT>/env/bin/python`，清除继承 BUDDY_/ANTHROPIC_/C2_/虚拟环境，私有 HOME/state/runtime/TMPDIR、固定模型目录和原生 sentinel；不运行完整检查、前端、账户发现或付费 smoke。下表保留首份实际命令参数，秒数含解释器启动。pure unit/模拟 harness 不被当作真实 RPC 通过。

| label | unittest 选择/语法参数 | 项数 | exit | seconds |
| --- | --- | ---: | ---: | ---: |
| `review-scope-fixed` | `blackboard.routing.test_stage2_review_scope` | 3 | 0 | 0.543 |
| `inquiry` | `blackboard.tasks.test_inquiry` | 36 | 0 | 40.858 |
| `native-claude` | `buddy.harnesses.claude.test_native_run` | 52 | 0 | 8.390 |
| `native-codex` | `buddy.harnesses.codex.test_native_run` | 51 | 0 | 17.757 |
| `native-dsh` | `buddy.harnesses.dsh.test_native_run` | 65 | 0 | 15.903 |
| `native-zcode` | `buddy.harnesses.zcode.test_native_run` | 42 | 0 | 17.214 |
| `ctwo-final` | `buddy.harnesses.test_c_two_live.WireFrameTests buddy.harnesses.test_c_two_live.EndpointAdmissionTests buddy.harnesses.test_c_two_live.EndpointObservationTests buddy.harnesses.test_c_two_live.EndpointLifecycleTests buddy.harnesses.test_c_two_live.CleanupPrimitiveTests buddy.harnesses.test_c_two_live.ReadyMaterialTests buddy.harnesses.test_c_two_live.ChannelUnitTests` | 51 | 0 | 0.903 |
| `live-channel-final` | `buddy.harnesses.test_live_channel` | 36 | 0 | 0.692 |
| `registered-review` | `buddy.roles.test_registered_review` | 4 | 0 | 1.181 |
| `registered-wiring-unit` | `buddy.roles.test_registered_run_wiring.RegisteredDescriptionTests buddy.roles.test_registered_run_wiring.NativeEvidenceProjectionTests buddy.roles.test_registered_run_wiring.FastRegisteredRunTests.test_preparation_retains_the_run_and_refuses_a_changed_registration buddy.roles.test_registered_run_wiring.FastRegisteredRunTests.test_discovery_has_no_prompt_run_or_service_binding` | 8 | 0 | 1.205 |
| `role-live` | `buddy.roles.test_role_live_seam` | 17 | 0 | 0.846 |
| `schema-worker` | `buddy.roles.test_schema_worker` | 7 | 0 | 1.201 |
| `runtime-live-final` | `buddy.runtime.test_live.WorkerLiveUnitTests` | 28 | 0 | 0.434 |
| `worker-invariants` | `buddy.runtime.test_worker_invariants` | 11 | 0 | 1.899 |
| `cli-unit` | `cli.test_cli` | 19 | 0 | 1.259 |
| `host-cli-unit` | `cli.test_host_cli` | 31 | 0 | 5.270 |
| `activity` | `protocol.test_activity` | 15 | 0 | 1.692 |
| `inquiry-transport` | `protocol.test_inquiry_transport` | 8 | 0 | 0.572 |
| `path-unit` | `protocol.test_path_state.PathStateTests` | 8 | 0 | 1.774 |
| `public-unit` | `protocol.test_public_state.FixtureRepairTests protocol.test_public_state.PublicStateTests.test_ps03_no_service_stop_and_restart_do_not_start protocol.test_public_state.PublicStateTests.test_ps05_missing_readonly_client_never_creates_or_starts protocol.test_public_state.PublicStateTests.test_ps07_internal_request_still_requires_private_root protocol.test_public_state.PublicStateTests.test_ps04_injected_call_does_not_resolve_state protocol.test_public_state.PublicStateTests.test_ps08_call_service_directory_parameter protocol.test_public_state.PublicStateTests.test_ps08_call_board_directory_parameter protocol.test_public_state.PublicStateTests.test_ps08_readonly_client_directory_parameter protocol.test_public_state.PublicStateTests.test_ps06_supplied_endpoint_keeps_default_directory protocol.test_public_state.PublicStateTests.test_ps07_public_resolution_preserves_path_guards protocol.test_public_state.PublicStateTests.test_ps06_cold_helper_imports_with_source_only_environment` | 13 | 0 | 7.083 |
| `rpc-profile` | `protocol.test_rpc_config.ProfileTests` | 8 | 0 | 0.977 |
| `rpc-daemon-unit` | `protocol.test_rpc_config.DaemonFixtureTests` | 7 | 0 | 0.901 |
| `attach-final` | `protocol.test_transport_attach.ReadOnlyAttachTests protocol.test_transport_attach.TrustBoundaryTests protocol.test_transport_attach.ColdStartTests protocol.test_transport_attach.RpcReadOnlySetupTests.test_missing_ipc_does_not_create_on_transport_or_board_client_attach protocol.test_transport_attach.RpcReadOnlySetupTests.test_read_only_state_with_private_ipc_reaches_rpc_without_mutation protocol.test_transport_attach.RpcReadOnlySetupTests.test_missing_state_does_not_create_on_board_client_attach` | 25 | 0 | 1.273 |
| `boundaries-fixed` | `protocol.test_state_boundaries` | 8 | 0 | 1.558 |
| `private-directories` | `test_private_directories` | 14 | 0 | 1.342 |
| `ctwo-peer-syntax` | `独立 AST parse fixture（语法，非运行）` | 0 | 0 | 0.058 |
| `runtime-peer-syntax` | `独立 AST parse fixture（语法，非运行）` | 0 | 0 | 0.035 |

首份上述聚焦共 567 项（含分文件 loader 实例，不作为全仓库唯一 ID 数），两项 fixture 只有语法验证。原生测试指仓库 mock CLI/协议模拟进程，不是安装原生账户或模型冒烟；其真实 Popen/停止语义由原 fixture 检查。测试改动不通过重导出或重复导入维持数量。

历史失败完整保留：rpc-once 首次选择了不存在的 unittest 方法，1 项 exit 1、1.018s，AttributeError、没有 RPC；ctwo-unit 51 项 exit 1、1.190s，6 项 string→Path fixture 未接线；attach-unit 25 项 exit 1、1.554s，3 errors/1 failure（ipc 检查位置、INVALID_RESPONSE 旧断言、模拟健康 endpoint 的缺 ipc）；live-channel 36 项 exit 1、0.778s，公开 timeout 校验晚于算 deadline；review-scope 3 项 exit 1、0.700s，原空环境断言；review-scope-final 3 项 exit 1、0.398s，移除旧缺根 guard 后显露同断言还缺 provider。上述缺陷按本批根因修正并有对应最终聚焦，不用保留冗余运行时规则维持旧测试。

中间 green 亦保留：state-boundaries 6/0/3.053s，state-boundaries-final 8/0/3.092s，ctwo-unit-final 51/0/1.104s，runtime-live-unit 27/0/0.614s；它们不是后续改动的最终结果，上表绑定首份固定源码，当前更新见 R2 节。

## 真实 RPC 与停止边界

首次真实 PublicStateTests.test_ps02_default_board_client_readonly：1 项 exit 1、2.795s，空 HOME 无 BUDDY_STATE_DIR、自有固定 catalog daemon，在 console 127.0.0.1:0 bind 被 sandbox 拒绝（CONSOLE_PORT_IN_USE），未到 C-Two register/RPC。原 evidence 的 daemon PID 87435、waitExit 2，serviceAndWorkerLocksReleased=true、shutdownCompleted=true、cleanupFailures=[]；成功 RPC 0、真实 RPC stop 回执 0，正常 finally 仍恢复权限、逐一收集直属 wait 与 shutdown。

违规需如实保留：后来误将 DaemonHealthTests 放进 daemon-unit 纯配置列表，产生第二次绑定尝试；同样 CONSOLE_PORT_IN_USE，5 项 exit 1、2.111s，其中 4 项 ceiling 通过。该命令违反仅尝试一次约束；不是重试成功，不抹掉或改为 skipped。原 support.daemon 的 poll 已看到 child 退出，BoardTestCase cleanup 运行并无 secondary cleanup error，suite PID 94516 waitExit 1；原 fixture 没有持久化 daemon PID/数字 wait receipt，不能编造两层账。此后不再启动真实绑定。

全部 run.py 所有 suite Popen 均已 wait，JSON pid/waitExit 保留；真实/模拟 native 进程由既有直属 fixture 收集，框架自己的正常清理允许进行。最终只读 argv 观察 ps 尝试一次被 sandbox EPERM 拒绝（`residual-observation.json`）；没有扫描/操作日常服务，没有发信号或收养进程。没有已知未 wait 的本回合 suite 句柄，但不能把无法观察的其他残留说成已停止；Host 要在允许观察的环境补核 task root，尤其 accidental daemon sample 的 receipt 边界。

首份 Worker 未验证（后续 Host R1 的两份 green 见 R2 节）：全部真实默认 CLI/源码 launcher/BoardClient（两 autostart 模式）、stop/restart、真实链接 root、0755 cold/Daemon、0500 tiny real peer 的无变更与真实 code/message/path、真实 native live channel、controller/Worker 真实注册与两跳 deadline。原 3-A/3-B 回归都保留（除被明确取代规则断言），本回合没有真实 green，不用 mock RPC 代替。Host-only 固定助手 `<TASK_ROOT>/host-replay.py` 在允许 bind 的环境按文件独立解释器执行上述全文件；默认 fixed-source，使用同一原 fixture/catalog/sentinel，遇失败停下，不能把本轮 unit 数当成补核结果。

## loader ID 与规则取代

基线实际 loader 3512、唯一 3512、exit 0、4.859s；最终 loader 3516、唯一 3516、exit 0、1.521s。只 collect，没有跑整套检查。3500→3512 属于已签收 3-B 新增 12 项 test_path_state 的历史对照，本回合基线用完整 3512；没有改写该记录。此次 removed 8、added 12，其中 6 删除、2 改名替换、10 真新增，未变化集合 3504 严格相等、重复 0、loader errors 0。完整集合和差集在 `<TASK_ROOT>/baseline-ids.json`、`final-ids.json`、`id-delta.json`。

| 删除/替代的完整 loader ID | 规则/新断言 |
| --- | --- |
| `blackboard.routing.test_stage2_review_scope.DeferredReviewTests.test_capabilities_and_all_entrypoints_refuse_without_native_start` | 改名为 capabilities_and_router_refuse_without_native_start；原 start_review 的断言仅因空 context.environment 命中被删除的 PRIVATE_STATE_REQUIRED，与 review carrier 不支持无关，删除此断言，保留 registry/router/直接 controller 的拒绝断言；未新增 harness capability 规则。 |
| `buddy.harnesses.test_c_two_live.ExplicitStateTests.test_internal_endpoint_and_channel_refuse_missing_state_before_sdk` | 删除内部 endpoint/channel 的 PRIVATE_STATE_REQUIRED 运行时规则；必填签名由 StateBoundaryTests 检查，实际调用方显式接线。 |
| `buddy.roles.test_role_live_seam.LiveBindingRegistryTests.test_a_ready_handle_cannot_borrow_the_ambient_sdk_state_when_its_owner_is_missing` | 删除 roles/live 的环境状态解析与 PRIVATE_STATE_REQUIRED 规则；不保留改名壳。 |
| `buddy.runtime.test_live.WorkerLiveUnitTests.test_explicit_client_root_wins_over_environment` | 删除 WorkerLiveRuntime 从 client.state_dir 自动选根的规则；保留 worker_supplied_root_wins_over_an_injected_client_root 的显式传递测试。 |
| `buddy.runtime.test_live.WorkerLiveUnitTests.test_missing_owner_root_refuses_before_sdk_even_with_an_ambient_domain` | 删除 WorkerLiveRuntime 的内部 PRIVATE_STATE_REQUIRED 规则，必填参数自然失败。 |
| `protocol.test_rpc_config.ProfileTests.test_explicit_state_and_environment_are_the_only_sources` | 删除 rpc_config 环境 fallback 的旧规则，新增实际 supplied root/无环境读取测试。 |
| `protocol.test_rpc_config.ProfileTests.test_missing_state_refuses_before_sdk_or_default_paths` | 删除 rpc_config.resolve_state_dir 与 PRIVATE_STATE_REQUIRED 专属规则；不留运行时兜底。 |
| `protocol.test_transport_attach.TrustBoundaryTests.test_unresolvable_health_reply_is_not_attached` | 改名为 invalid_health_reply_fails_without_cold_start；旧规则吞 INVALID_RESPONSE 后可冷启动，新规则同错误直接传播、无 spawn/无文件变更。 |

新增/改名后的完整 ID：

- `blackboard.routing.test_stage2_review_scope.DeferredReviewTests.test_capabilities_and_router_refuse_without_native_start`
- `buddy.harnesses.test_c_two_live.WireFrameTests.test_missing_deadline_is_refused_before_queue_or_native_delivery`
- `buddy.runtime.test_live.WorkerLiveUnitTests.test_worker_forwarding_preserves_original_deadline_and_refuses_missing_or_expired`
- `protocol.test_state_boundaries.StateBoundaryTests.test_configuration_uses_supplied_root_without_environment_or_client_fallback`
- `protocol.test_state_boundaries.StateBoundaryTests.test_dangling_ipc_link_still_reaches_the_structural_guard`
- `protocol.test_state_boundaries.StateBoundaryTests.test_internal_parameters_are_required_path_without_another_resolver`
- `protocol.test_state_boundaries.StateBoundaryTests.test_missing_ipc_is_a_direct_read_only_condition`
- `protocol.test_state_boundaries.StateBoundaryTests.test_process_entries_fail_without_parent_state_and_do_not_select_default`
- `protocol.test_state_boundaries.StateBoundaryTests.test_public_calls_select_exactly_once_and_share_the_selected_object`
- `protocol.test_state_boundaries.StateBoundaryTests.test_unavailable_ping_is_unhealthy_but_other_errors_escape_unchanged`
- `protocol.test_state_boundaries.StateBoundaryTests.test_windows_attach_does_not_require_a_posix_ipc_directory`
- `protocol.test_transport_attach.TrustBoundaryTests.test_invalid_health_reply_fails_without_cold_start`

保留 test_ps07_internal_request_still_requires_private_root 并将原 PRIVATE_STATE_REQUIRED 改为自然 TypeError/state_dir 必填断言；public_state parameter/default 实际传递断言仍在，新增 request spy 在进入 RPC 前断言目标 Path，以便缺根变异触发目标 assertion。test_private_directories 的混合测试只删除 PRIVATE_STATE_REQUIRED 部分，保留实际 context→attempt_root 与 reparse guard；PathStateTests 的 public precedence 保留，删除旧 rpc_config 环境解析断言。没有找到独立命名的“旧 sender 缺 deadline 兼容”测试；所有有效模拟 sender 已显式供字段，保留合法/过期测试并新增真实 server handler 的缺字段拒绝、不排队/不送 owner 断言。

## 固定副本、故障注入与 AST 绑定

`<TASK_ROOT>/fixed-source` 是最终生产/测试固定副本（记录写入前，记录不参与自身 hash），fixed-manifest.json 逐文件 SHA-256；ast-source-binding.json 包含 41 个受影响 Python 文件的源码 hash/AST 节点，写记录前逐项与固定副本相等。4 个独立 copy 只按 AST 确认的类/函数节点做相称变异，mutation-bindings.json 保存原/变异 hash、符号、行号和目标。未变动受管 checkout 的源码做红灯，也未用 import/sandbox/任意外层 timeout 代替目标失败。

| 变异 | 固定源 green (exit/seconds) | 变异 (exit/seconds) | 目标 assertion |
| --- | --- | --- | --- |
| `remove-public-resolution` (`src/hey_my_buddy/protocol/transport.py:448`, `call_service`) | 0/1.360 | 1/1.353 | public default request must carry the selected real state Path |
| `allow-missing-deadline` (`src/hey_my_buddy/buddy/harnesses/c_two_live.py:199`, `LiveWireRequest`) | 0/0.324 | 1/1.847 | request-window-expired != frame-invalid: missing deadline must be refused by the server |
| `renew-worker-deadline` (`src/hey_my_buddy/buddy/runtime/live.py:317`, `request`) | 0/0.328 | 1/0.403 | channel.deadline != 原 deadline |
| `swallow-invalid-health` (`src/hey_my_buddy/protocol/transport.py:263`, `_healthy`) | 0/0.268 | 1/0.293 | ServiceError not raised |

缺 deadline 变异同时恢复 nullable DTO 与旧 server tolerance；1.503s 是旧 server 的合法 business window 等待后返回 request-window-expired，目标断言要求 frame-invalid 而失败，不是 watchdog。正常 handler 立即拒绝且 consume_request(0)=None、pending/requests 空。所有变异与 green 只用固定离线测试，不发真实模型/RPC。

## 范围外使用方与 Host 请求

以下六个文件有确定的新增必填 CLI helper 或移到内部 RPC helper 后的调用/patch 缺口；首份未改它们；R2 按 input 许可接线，但机械 scope 未修订而封存失败，随后由 Host 正式 adopt 保全；R3 当前正式 scope 已覆盖这些路径，没有通过兼容 symbol/default 参数绕过。以下保留首份发现位置，其迁移没有删除原断言或编号：

- `tests/python/blackboard/service/test_service_environment.py:89,253`：_worker_command 需真实 Path。
- `tests/python/buddy/runtime/test_worker_runtime.py:34,56,73`：_worker_command 需真实 Path。
- `tests/python/blackboard/tasks/test_workspace_api.py:55,146`：_ensure_service patch 与 _save_control Path。
- `tests/python/blackboard/tasks/test_workflow_worker.py:377,383`：_submission_token Path。
- `tests/python/cli/test_cli_views.py:47,58,62`：移到 _call_service/_ensure_service，Path 明确。
- `tests/python/blackboard/catalog/test_account_operations.py:176,180`：原 cli.call_service patch 已不再存在。

首份生产缺口已按本轮 Host 决定修复：blocking.await_run、console_cli.run 和 catalog.discover 均必填 Path；沿 CLI.main 传递，catalog 生产只消费 store.directory。console 原权限、browser/wait 与 cold/read-only 策略保留，账户/模型语义未变。install/external example 的既有外部公开调用不属于本批改动，未擅改。R2 又发现的 workflow_stop_surface 调用方在正式 scope v3 下已接线；本批已审计使用方没有剩余未授权缺口，不外推为全仓其他接缝已整理。

无闲置生产 resolve_state_dir/PRIVATE_STATE_REQUIRED 使用方；范围内 obsolete helper/规则专属测试已经删除，没有新兼容包装。历史验收、ADR/CONTEXT/AGENTS/README/reference/SKILL/Host 指南未编辑。scope 外一次格式遍历误插入 test_dsh_tool_evidence 空行，随后按原内容恢复，最终 changedPaths/scope 审计无该路径。

## 私有材料与交付收尾

短系统临时任务根创建后立即报告，结构化 outcome 提供确切 root，本记录以 `<TASK_ROOT>` 引用。TMPDIR/BUDDY_CHECKS_TMPDIR、uv 环境/cache、固定副本、变异和一次性助手都在 root；未手工删除文件或目录，只有既有框架/产品自身正常清理。首份内部只读审计助手曾将 rg 输出重定向到 task root 外的 `<OUTSIDE_AUDIT_FILE>`；本轮从该助手原始命令记录确认是普通 `>`，不是独占创建，未检查原文件是否存在。若存在会截断，但实际是否覆盖、写入前归属均未知，不能按名称/内容/日期推断是本任务新建。未访问、覆盖或删除此文件；确切路径与原命令在结构化 outcome / outside-audit-command-r2.json，Host 先保留而非按“owned”回收。没有 git stash/分支切换/新 refs/提交或日常安装操作。

先核 Host 提供公开 delegate-materials 的 18 项 provenance SHA-256/size 与 uv.lock byte equality（锁 SHA-256 5ae675829eb4c9aca5e2c933b86799abd3ab9a979e80f8d00c074adf5e27cdf8），再 uv 离线私有 venv/pip 安装锁定公开 wheel（10 个运行依赖，c-two 0.7.4、pydantic 2.13.5、portalocker 3.2.0、zstandard 0.25.0）；materials-verified.json/dependency-setup.log 保留。未安装/升级 daily runtime、未调用原生账户发现/模型/付费 smoke/读取凭据内容。Host 验收后按结构化确切 root 回收，先补未验证进程与真实 RPC，不把本次 assistance 当作内部签收或整批验收。

## 同 run R2：权限、Host 输入与实际返修

本轮先读取 `git show f22d8450:docs/acceptance/c-two-public-state-plan.md` 的“3-C 首份固定交付与范围修订”，当时将 continuation 的文本许可误记为有效范围已从 53 扩至 66；实际执行冻结 scopeVersion 1，这个错误解释与 R2 的 WORKSPACE_SCOPE_VIOLATION 保留，不能追溯改写为机械授权成功；未编辑保护文档、黑板 manifest/SQL 或 refs。沿用原短任务根，未新建 root、未重新安装依赖，仍使用首轮核过 18 项 provenance/hash 与锁的私有环境。首份产物未签收，Host 的只读接缝审查不是测试绿灯或最终验收。

Host 明确输入的 R1 固定候选补核：path_state 12 / exit 0 / 6.801s，public_state 26 / exit 0 / 27.858s；真实默认/link/0755/0500 和结构错误通过。原两份测试与 transport/client/rpc_config/daemon 生产 SHA 均未变，不用签名检查替换这些真实回归，也不因记录或 console 专支接线重跑它们。本轮 CLI.main 只比首份多传 console state_dir；不能把 R1 外推为 R2 所有真实接缝已通过。

R1 失败如实保留：C-Two 全 70 项 / exit 1 / 5.837s / 18 failures，c_two_live_peer 使用 Path 未导入；Worker live 全 34 项 / exit 1 / 4.416s / 6 errors，nested controller endpoint 漏 state_dir，而旧 DEVNULL 只留下 pipe closed。原 `state-boundaries-host-{path,public,ctwo,live}-r1.{json,log}` 是 Host 输入指向的证据，本 Worker 没有声称自己执行这些补核或拷贝其未提供原始日志。

已修两处 fixture：补 Path import、controller endpoint 显式传父进程 state Path；nested stderr 改为继承当前 fixture stderr，使实际错误进入原上层 peer 保存的日志，不增加管道等待/线程。增加两项执行真实 fixture 函数和真正 endpoint 构造、在 start/原生注册前截住的单元回归，不能称作真实 RPC 成功；它们在重新删除 import/参数的固定副本上都以目标 assertion 红灯。

六个新增调用方只迁移必填 Path/内部 patch；CLI view 删除已被实际 main 传根取代的 partial 注入，不删原测试断言和 ID。catalog/evaluation 直接调用传自己私有根；catalog 的 mock discovery 只跑离线替身，未运行 InstalledHarnessDiscoveryTests（该真实安装/发现边界仍未验证），更未做原生账户发现/模型调用。

### R2 分文件聚焦

命令仍为 `python3 <TASK_ROOT>/run.py <CHECKOUT> <label> -m unittest -v <选择>`，每行独立解释器，实际日志与 suite PID/waitExit 均为同名 JSON/log；14 条完整 green、232 项通过。另 r2-service-env 12 项 exit 1，其中 10 个离线目标通过、2 个 preflight sandbox error，不能将整个文件计作通过。

| label | 实际 unittest 选择 | 项数 | exit | seconds |
| --- | --- | ---: | ---: | ---: |
| `r2-console` | `console.test_console_cli` | 36 | 0 | 0.601 |
| `r2-blocking` | `cli.test_blocking` | 26 | 0 | 0.642 |
| `r2-catalog` | `blackboard.catalog.test_catalog` | 10 | 0 | 0.751 |
| `r2-service-env` | `blackboard.service.test_service_environment` | 12 | 1 | 0.815 |
| `r2-worker-env` | `buddy.runtime.test_worker_runtime.WorkerLaunchEnvironmentTests` | 3 | 0 | 0.930 |
| `r2-boundaries-fixed` | `protocol.test_state_boundaries` | 9 | 0 | 1.608 |
| `r2-ctwo-fixed` | `buddy.harnesses.test_c_two_live.WireFrameTests buddy.harnesses.test_c_two_live.EndpointAdmissionTests buddy.harnesses.test_c_two_live.EndpointObservationTests buddy.harnesses.test_c_two_live.EndpointLifecycleTests buddy.harnesses.test_c_two_live.CleanupPrimitiveTests buddy.harnesses.test_c_two_live.ReadyMaterialTests buddy.harnesses.test_c_two_live.ChannelUnitTests` | 52 | 0 | 1.114 |
| `r2-live-fixed` | `buddy.runtime.test_live.WorkerLiveUnitTests` | 29 | 0 | 0.649 |
| `r2-cli-views` | `cli.test_cli_views` | 11 | 0 | 15.284 |
| `r2-workflow-race` | `blackboard.tasks.test_workflow_worker.SubmissionPreparationRaceTests` | 1 | 0 | 0.869 |
| `r2-account-cli` | `blackboard.catalog.test_account_operations.AccountOperationTests.test_cli_refuses_key_in_arguments_and_delivers_only_stdin_key_to_rpc` | 1 | 0 | 0.804 |
| `r2-workspace-unit` | `blackboard.tasks.test_workspace_api.WorkspaceApiTests.test_five_named_surfaces_and_worker_authority blackboard.tasks.test_workspace_api.WorkspaceApiTests.test_cli_integration_acceptance_and_cleanup_preserve_private_control` | 2 | 0 | 7.806 |
| `r2-cli` | `cli.test_cli` | 19 | 0 | 1.249 |
| `r2-host-cli` | `cli.test_host_cli` | 31 | 0 | 5.391 |
| `r2-evaluation` | `blackboard.evaluation.test_evaluation.EvaluationEvidenceTests.test_catalog_refresh_is_explicit_attributed_and_failure_safe blackboard.evaluation.test_evaluation.EvaluationGateTests.test_ordinary_reads_never_discover_models` | 2 | 0 | 1.015 |

新增测试初期失败保留：r2-boundaries 9 / exit 1 / 1.713s，模拟 task_get 缺 task envelope，修 fixture 后 r2-boundaries-fixed 通过；r2-ctwo 52 / exit 1 / 1.006s，guard 错以为 BoardError.__str__ 含 code，修为其真实 message 后通过。r2-live 29 / exit 0 / 0.512s 是中间 green；为缺 state 的迁移变异给出目标 assertion 后以 r2-live-fixed 重新绑定。迁移脚本初次 IndexError（空参数 discover call，0 测试）及两次变异助手 FileNotFoundError（原副本位置选择错误、0 目标测试）不是故障注入证据，原脚本与 proof-setup-failures-r2.json 均保留，改正后才执行下表目标。

本轮违规另行披露：r2-service-env 虽模拟 Popen，但 ColdStartEnvironmentTests 两项仍调用真实 _cold_start_preflight.listener.bind，各被 EPERM 拒绝后报 LAUNCH_ACCESS_DENIED。这违反本轮“不要再尝试任何受限真实 bind”的指令，不能因 Popen 是 mock 抹去；没有 daemon Popen 或 native register，没有重试该文件/这两项，没有为沙箱给产品加 skip。原 12 项失败日志保留，两个目标交 Host；余下选测逐项限定在无 bind/注册路径。

本轮 35 个首份 Python 文件 SHA 相等，其已绑定聚焦沿用；6 个首份 Python 文件改变（main、两个 peer、两个对应测试、state_boundaries）及 13 个新增范围 Python 文件分别由上述对应聚焦/guard 验证。peer guard 是执行构造的离线验证，不能替代真实 peers。完整复用/重核边界见 verification-reuse-r2.json。没跑全检查、前端或付费 smoke。

### 最终固定源、编号与变异

最终生产/测试固定副本为 `<TASK_ROOT>/fixed-source-r2`，54 个改动 Python 文件的源码 SHA/AST node count 与受管 checkout 相等（ast-source-binding-r2.json），完整归档 manifest 为 fixed-manifest-r2.json。本轮 loader 仅 collect：3519 项、唯一 3519、exit 0、2.137s；相对首份 3516 只新增下列 3 项、删除 0。相对原 3512：删除 8、增加 15、未变 3504 集合严格相等、重复 0/装载错误 0；首份删除规则表原样适用，不为数字留旧规则/重导出/重复导入。

- `buddy.harnesses.test_c_two_live.ReadyMaterialTests.test_private_peer_reaches_start_with_explicit_state`
- `buddy.runtime.test_live.WorkerLiveUnitTests.test_private_controller_reaches_start_with_parent_state`
- `protocol.test_state_boundaries.StateBoundaryTests.test_internal_cli_defaults_share_selected_path_without_public_resolution`

从本轮最终固定副本重建六个独立变异，每个 green/red 独立解释器只跑该目标；原四个生产节点 SHA 未变，新增两个迁移 guard 与相称故障同时验证。所有 red 均为一项 failure/AssertionError，没有 import framework、sandbox 或任意 watchdog 红灯。mutation-bindings-r2.json 保存原/变异文件 SHA、源码 AST SHA、具体符号/行号、目标及两级 driver/suite waitExit。

| 变异 | 完整目标 | green exit/seconds | red exit/seconds |
| --- | --- | --- | --- |
| `remove-public-resolution` | `protocol.test_public_state.PublicStateTests.test_ps08_call_service_directory_parameter` | 0/1.438 | 1/1.382 |
| `allow-missing-deadline` | `buddy.harnesses.test_c_two_live.WireFrameTests.test_missing_deadline_is_refused_before_queue_or_native_delivery` | 0/0.331 | 1/1.853 |
| `renew-worker-deadline` | `buddy.runtime.test_live.WorkerLiveUnitTests.test_worker_forwarding_preserves_original_deadline_and_refuses_missing_or_expired` | 0/0.357 | 1/0.366 |
| `swallow-invalid-health` | `protocol.test_transport_attach.TrustBoundaryTests.test_invalid_health_reply_fails_without_cold_start` | 0/0.251 | 1/0.262 |
| `peer-path-import` | `buddy.harnesses.test_c_two_live.ReadyMaterialTests.test_private_peer_reaches_start_with_explicit_state` | 0/0.315 | 1/0.317 |
| `controller-state-argument` | `buddy.runtime.test_live.WorkerLiveUnitTests.test_private_controller_reaches_start_with_parent_state` | 0/0.335 | 1/0.323 |

原四项目标原因仍分别为选中真实 Path、missing deadline 必须 frame-invalid、Worker 维持原 deadline、INVALID_RESPONSE 不得被吞。缺 deadline 变异仍只等待旧合法业务窗口后返回 request-window-expired，原拒绝断言失败。peer import 变异以 start 未被调用的 assertion 失败；controller 参数变异先捕获真实 TypeError，再以“private controller must reach start with the parent state Path”的 assertion 失败。二者不是模块装载失败，也没到注册。

### R2 发现的 scope 缺口与当时补核请求（R3 已正式授权接线）

`tests/python/blackboard/tasks/test_workflow_stop_surface.py` 不在最新 66 路径内；GoalStopWaitTests 四处直接 await_run（当前行 81/110/133/153）没有 state_dir，必填签名会自然失败。R2 未修改/运行这个文件，也不通过 default 参数、alias 或 compatibility wrapper 绕过。当时请求追加此一个精确路径；Host 随后正式 scope-amend v3，R3 已把四个测试传自有 Path 并保留断言/ID；这是本轮全调用方审计唯一确认的新缺口。所有已授权的六个测试和三生产接缝已实接。

待 Host 在允许私有 bind 的环境补核 R2 全 C-Two 71、Worker live 35，以及 service_environment 两个 preflight 目标、workspace_api 的真实 C-Two 目标；本 Worker 不再执行受限 bind/observe。固定助手 host-replay-r2.py 先核 54 个源码 hash，再按独立解释器运行受影响真实目标，遇失败停止，不执行模型/原生账户发现；不重跑无变化的全批检查，未执行的助手不能作为验收。其他原生真实 live/lifecycle 与 staged runtime 边界仍继承首份未验证口径，未因本轮 unit 声称 green。

### 原始写入证据与停止收尾

只读 audit helper 本轮仅从其原始工具调用记录回报首次命令：`rg ... > <OUTSIDE_AUDIT_FILE>`，普通重定向、非独占创建、无存在检查。实际是否截断了旧内容及原归属未知；不是“确认本任务新建”，本轮未访问/更改/删除。原完整命令与未知字段在 outside-audit-command-r2.json；scope 外 test_dsh_tool_evidence 的空行误写/原样恢复仍保留首份披露，最终 diff 不含该路径。

Host 本轮输入的只读当下观察：与精确 root 绑定的当前进程 0、观测问题 0、held daemon/supervisor locks 0；这仅补该时刻观察，不能追补旧 accidental daemon 未持久化的数字 wait/两层回执。原二次误 bind 违规与 receipt 缺口保留。本轮 run.py suite Popen 和变异 driver 都有直属 waitExit，构造 guard 没创建服务/peer；没有已知未 wait 的本轮 suite 句柄，不把禁止新观察后的其他状态猜为 stopped。

沿用 root，所有新副本/日志/材料/助手仍在 root；没有手工删除任何材料、git stash/refs/commit 或日常运行时操作。R2 的 scope 追加请求现由正式 v3 解决；Host 完成真实补核与验收后按确切 root 回收，根外未知归属文件原样保留。R2 原生输出不是成功封存或签收；机械失败、adopt 与本轮正式授权见下节。

## 同 run R3：正式机械 scope、最后接线与只读流程复用

本轮先读 `git show c521843c:docs/acceptance/c-two-public-state-plan.md` 的正式修订；执行输入明确为 scopeVersion 3、67 路径，基线是 adopt 保全的 `f86215878e746cbb3b5e9c50d0d9374a961467dc`。原 submission intent 53、原 R2 scopeVersion 1 和失败现场均保留，不把后续授权追溯到 R2。旧 final-record-binding-r2.json 的 effectiveScope=66 是当时把 input 当机械授权的错误口径，原文件不回写；最新机械事实与纠正字段在 mechanical-scope-r3.json。

Host 给出的 R2 机械结果为 WORKSPACE_SCOPE_VIOLATION，conflict `wsc-b68ab678-4458-4225-90a6-8e923b18340d`，observedFingerprint `eeb0a6cff67f83d36af2c765736942c3f246a79247cf29765f81a2bed56c6cff`，两层停止确认；这不是源码测试失败。Host 起初错把 input 权限当机械 scope，核对 13 个 blockingPaths 与失败现场哈希后用 workspace-resolve/adopt 固定 artifact `217e3f07-7f4f-4d65-8f80-82b67adb3573` / `f86215878e746cbb3b5e9c50d0d9374a961467dc`，累积补丁 SHA-256 `fa2e641dbbba4ef176d839934bdd89c0ac7f41bcd6ea6d99bc1cef04e7acea70`，55 路径。adopt 只是保全，不是验收。确认相关回合停止、没有待执行 continuation 后，Host 正式 scope-amend v2=66、再 v3=67/revision 15 追加唯一 stop-surface 文件，本轮才消费新 scope。Worker 没改黑板 manifest/SQL，也没创建新 run。

GoalStopWaitTests 的四个 await_run 现在从 runner 已有私有 BUDDY_STATE_DIR 获取 Path，显式沿 service 参数传入，模拟服务断言收到同一对象；原取消、晚到 completed、后代停止事件、未知停止超时及 recovery 命令断言和四个 ID 全部保留。移除 console 注入 closure 和 BoardClient 内重复的 attach/缺服务拒绝/_request 代码，放入 transport._call_board_read_only 必填 directory: Path；两处复用同一实现，console 使用 partial 绑定已选根。BoardClient 的 call 注入时机、resource 传递与 autostart=False 策略不变，未新增公开解析入口、环境/default fallback 或兼容包装。现有内部参数测试增加这一 helper，未增加测试编号。

### 本轮受影响离线聚焦与直接 wait

仍用 `python3 <TASK_ROOT>/run.py <CHECKOUT> <label> -m unittest -v <选择>`，每行独立解释器；清除继承 BUDDY_/ANTHROPIC_/C2_/虚拟环境，使用原私有环境、HOME/state/runtime/tmp、固定 catalog/sentinel。下表 55 项全部 exit 0，五个 suite 的直属 pid/waitExit 在同名 JSON 与 focus-results-r3.json。三个进程入口缺状态用既有测试子进程自然退出 2 并 communicate 收集；没有原生 harness、真实 bind/注册、模型、账户发现或新进程观察。没有为记录重跑测试。

| label | 完整 unittest 选择 | 项数 | exit | seconds | suite pid / waitExit |
| --- | --- | ---: | ---: | ---: | --- |
| `r3-workflow-stop` | `blackboard.tasks.test_workflow_stop_surface.GoalStopWaitTests` | 4 | 0 | 0.659 | 78594 / 0 |
| `r3-console` | `console.test_console_cli` | 36 | 0 | 0.562 | 78595 / 0 |
| `r3-boundaries` | `protocol.test_state_boundaries` | 9 | 0 | 1.514 | 78593 / 0 |
| `r3-readonly` | `protocol.test_transport_attach.RpcReadOnlySetupTests.test_missing_ipc_does_not_create_on_transport_or_board_client_attach protocol.test_transport_attach.RpcReadOnlySetupTests.test_read_only_state_with_private_ipc_reaches_rpc_without_mutation protocol.test_transport_attach.RpcReadOnlySetupTests.test_missing_state_does_not_create_on_board_client_attach` | 3 | 0 | 0.674 | 78596 / 0 |
| `r3-public-readonly` | `protocol.test_public_state.PublicStateTests.test_ps05_missing_readonly_client_never_creates_or_starts protocol.test_public_state.PublicStateTests.test_ps04_injected_call_does_not_resolve_state protocol.test_public_state.PublicStateTests.test_ps08_readonly_client_directory_parameter` | 3 | 0 | 1.491 | 78973 / 0 |

readonly setup 只替换 cc.connect，仍走真实目录 guard/configure_client；0500 断言通过不代表 native peer 成功。public 参数目标真实断言所选 Path，缺服务/注入目标不启动服务。whole public_state、C-Two、runtimeLive 和含真实 preflight 的 service_environment 不在本轮执行集合，避免同文件带来禁用的 bind；它们的原失败和 Host 待补核边界不被本轮 green 覆盖。

loader 只 collect：`python3 <TASK_ROOT>/run.py <CHECKOUT> r3-loader <TASK_ROOT>/loader.py <TASK_ROOT>/final-ids-r3.json`，3519 项/唯一 3519、exit 0、1.428s，直属 pid 78972 / waitExit 0。R2→R3 增删 0，集合相等；原 3512→当前删除 8、增加 15、未变 3504 集合严格相等、重复/装载错误 0。上述首份八条删除/替代规则、十二个新增/改名 ID 和 R2 三个新增 ID 均原样适用；本轮四个 await 原 ID 保留，无动态重导出、重复导入或留空改名壳。完整差集为 id-delta-r3.json。

### 最终源码固定与既有证据复用

最新固定源 `<TASK_ROOT>/fixed-source-r3` 共 944 文件，manifest 排除本记录以免自引用，fixed-manifest-r3.json SHA-256 为 `9cf8c3bbeb898231d327ba6272e08681a11c83799d0bb96202212d4b9e1d1d8b`；Python 全集 SHA-256 `4baee78aad8fcc84e6b87a609c3344d85998fd00a8157f98b620087e0d8da5fe`。累计 56 路径中的 55 个 Python 文件与固定源逐一 byte/hash 相等、AST 可解析，保护文档无改变；本记录最终 hash 与源码清单在 final-record-binding-r3.json，实际完整 changedPaths 和本轮六路径差集分别在 changed-paths-r3.json/turn-delta-r3.json。记录写完后只重核绑定与 diff，不重跑测试。

R2 的 54 个改动 Python 文件中 50 个 hash 未变；四个改变是 console_cli、client、transport、state_boundaries，另新增 stop-surface 改动文件，都由本轮受影响聚焦覆盖。未变文件沿用已绑定聚焦，不把 R1 的真实 public 成功外推为本轮只读流程原生成功。完整复用列表 verification-reuse-r3.json 保留测试边界。

六个 R2 故障注入的生产/fixture AST 节点及目标测试文件逐一与当前固定源相等，mutation-reuse-r3.json 绑定最新文件 hash、节点 hash/行号、目标 hash 与原 R2 green/red receipts；其中 transport 文件整体因新增只读 helper 改变，call_service 和 _healthy 节点未变。missing deadline DTO/server、Worker 原 deadline 转发、两项 fixture 与所有六个目标文件整体不变。沿用 R2 六项目标 AssertionError 证据，没有重跑这些未变目标或把 AST 等价说成新红绿执行。合法/过期、服务端拒绝缺字段不排队/不送达、Worker 两跳原时窗仍由对应原测试守住。

### 尚待 Host 的固定补核与收尾

Host 输入只说明已开始在 f862 固定副本做 C-Two 71 和 runtimeLive 35 的真实 SDK 补核，尚未提供结果，本记录不声称它们通过。它们的生产/fixture/test 文件与最新源完全未变，Host 可按 hash 复用其结果；若有新缺陷，原 run 在正式 scope 内继续，不由 Host 代写代码。最新只读公共流程的真实默认 BoardClient/晚选环境/0500、原受限 service_environment 两个 preflight 及 workspace_api 的真实目标仍需 Host 核定。host-replay-r3.py 核对 55 个源码 hash 后只执行这些补核选择，每个 driver 与 suite 都保留直接 wait；此助手由 Worker 静态交付，未运行。其选测不重复正在由 Host 核定的未变 C-Two/runtimeLive 文件。R1 path_state 12/public_state 26 实际通过与 C-Two 70/runtimeLive 34 失败、首份本地/沙箱失败、R2 两处误 bind 全部保持原历史。

沿用结构化报告的同一精确任务根，未新建 root、未新装依赖、未派新 helper/Worker、未手工删除任何材料、未用 stash/refs/commit。没有已知未 wait 的本轮 suite 句柄；旧 accidental daemon 缺数字 wait/两层证据仍未知，Host 的当前 0 观察不能追补历史。根外 `<OUTSIDE_AUDIT_FILE>` 首次普通 > 可能截断旧文件，实际覆盖与写前归属未知，原样保留且本轮没有再访问/修改/删除，不纳入 owned 清理。scope 外空行误写/恢复披露保留。当前停止在用户约定的 Host 审查条件，范围内无已知待接线使用方或新增共享缺口；真实边界与最终签收仍待 Host，不是整批验收。
