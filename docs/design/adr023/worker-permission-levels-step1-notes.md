# Worker 权限档位第 1 步说明

## 实现清单

当前选择在 `workflow_runs.execution_workspace_json.access/accessReason`，当前准备好的检出在 `workflow_runs.workspace_manifest_json`，继续的准备结果在 `workflow_continuations.workspace_manifest_json`，固定输入在 `workflow_turns.input_json.executionWorkspace`。每个回合新增 `workflow_turns.access` 与 `access_reason` 两个标量列，前者记录 Host 选择，后者仅记录 full 的理由；不另建一对一表，不写入 meta。`workspace_reservations.access` 仍表示物理检出的读写占用，full 使用 write 占用。

升级基准为已安装的 0.30.0、schema 17、提交 `9131a1bda057f5ec3790393ec3e31ca5c96ba06c`；生成器将此提交用 git archive 读进本检出的 ignored tmp，使用给定只读解释器运行基准源码的命名服务操作，生成新的 `tests/fixtures/upgrade-base`。删除已用掉的 16 → 17 账户退休升级及专用辅助断言，只保留 17 → 18 升级；已有回合按所属微任务当时固定的 access 回填，理由为 NULL，其余列、输入哈希、封存证据与表保持原值；同时从当前 harness 声明补齐既有 profile 的 permission 能力字符串，保留启用、可用状态与其他字段，避免升级后路由候选因旧记录缺少新字符串而消失。升级后完整 schema 与新建看板比对，旧 schema 16 不再作为启动或升级输入。

数据路径为 `normalize_execution_workspace` → `WorkflowCoordinator.submit` → `attach_governed_task` → `begin_turn/_turn_workspace` → `run_execution.worker_request` → harness 的逐档声明 → `RunRequest`。权限档位和原生工具范围是两个不同事实：Codex、DSH、ZCode 的 read 保留当前的原生 write 范围，由黑板的读占用和不允许改动的封存判定承载；Claude 的 read 使用原生 read；四家的 write 保持当前范围；full 均不声明。每个 harness 每档的声明包含 native tool scope 与 enforcement，资格仍通过已有 permission 能力字符串；RunRequest 增加独立的 Worker permission 事实，把声明的原生范围交给已有运行模块，不改原生参数映射。

逐档声明的 enforcement 只记录在回合的原生证据中，get、CLI 简要视图、能力字符串与控制台均不显示；Host 的选择仍按回合列读取。删去上一轮为 DSH 占用测试临时扩展只读能力的夹具，还原 Codex、DSH、ZCode 原来已可提交、路由的 read 行为。角色说明继续要求如实报告被拒绝的操作，不绕过去。

接口保持 read/write/full，省略为 write；full 必须给单行非空 accessReason，最多 1000 字符；别的档位带理由拒绝。continue 不给档位沿用，明确换档从下个回合生效，选择和换档事件原子提交；已经固定的回合、manifest 和输出不改。未封存的首次继续保留原来显式选择的未跟踪输入。

## 已实现的取舍与删除

两个新列是回合的一部分，具有固定、可独立读取的含义，与回合一同写入同一事务；另建一对一表会多一次连接却不增加独立生命周期，因此沿用回合表。SQL 约束确保 full 与非 NULL 理由同时存在，接口边界负责单行、非空和长度校验。继续操作改变当前选择与后续准备好的检出，并记录 `workflow.access_changed` 事件；先前回合从自己的列读取，不依赖微任务当前值。full 在物理占用层仍是 write，不增加另一套占用规则。

每档声明以 `WorkerPermission` 保存 level、toolScope、enforcement，enforcement 仅取 `enforced` 与 `convention`，Worker 请求携带这一事实与实际 tool_scope；两者范围不一致属于自身调用参数错误。声明由角色层选择一次，原生运行模块沿用原有参数映射。Codex 和 DSH 的 write 声明对应已有原生写范围，ZCode 的 write 对应其现有运行路径；Claude 的 read/write 对应其已有原生工具限制。四个原生入口同时拒绝 full 工具范围和声明为 full 的请求，避免错误调用把 full 降为 write。Codex 的网络设置及四个 harness 的原生权限参数均未改。

控制器把本次声明写入私有 `worker-permission.json`，附带回合、执行与会话绑定及内容大小和哈希，成功和失败都记录；角色层校验引用和绑定后放入 `nativeEvidence.workerPermission`。这是本次调用选用的声明，不代表对原生运行结果的额外认证。非 Worker 请求不增加该证据，get 与 CLI 不新增 enforcement 字段。

已删除 `migrate_16_to_17`、账户退休升级专用常量和 SQL 辅助代码、旧样板中的退休操作及只为它存在的测试断言；通用私有迁移、备份和工作区身份迁移仍有现行调用方，保留。样板读取器继续验证固定内容及重绑定私有路径所需的哈希，没有兼容旧样板的两套读法。既有 profile 的 permission 能力是代码能力，不是账户可用性；17 → 18 仅补齐这项声明，保留账户、启用、可用、时间及其他能力字段。删去 DSH 测试临时声明未来只读能力的绕路，四家直接使用现行声明。

## 实际验证与边界

生成器已成功从 `9131a1bda057f5ec3790393ec3e31ca5c96ba06c` 的 git archive 读取源码，使用给定只读解释器和私有 HOME、harness 配置目录执行基准的进程内命名操作。新样板为 schema 17、58 张表、51 条事件、3 个既有回合，完整性为 ok、外键检查为空；源码 schema 与导出一致，metadata 中生成器哈希与最终生成器一致。基准的原生发现与目录读取使用夹具，Git 工作区与封存对象由真实代码生成；没有安装依赖、启动服务或原生 Worker、读取日常看板或调用模型。

上一轮最终两组受影响测试分别通过 206 项和 220 项：第一组覆盖 `protocol.test_workspace_access`、`buddy.harnesses.test_run_contract`、`buddy.roles.test_schema_worker`、`buddy.roles.test_run_execution`、`buddy.roles.test_registered_run_wiring`、`buddy.roles.test_permission_evidence`、`buddy.roles.test_role_prompts`、`blackboard.catalog.test_catalog`、`blackboard.evaluation.test_evaluation`、`blackboard.store.test_migrations`、`blackboard.store.test_upgrade_fixture`、`blackboard.store.test_current_core`、`install.test_upgrade_migration`、`cli.test_cli_views`、`cli.test_cli.HelpTests`、`cli.test_host_cli.GovernedParameterDocumentTests`、`blackboard.tasks.test_permission_levels` 和 `blackboard.routing.test_permission_route`；第二组覆盖 `blackboard.tasks.test_workflow`、`blackboard.tasks.test_workflow_routing`、`blackboard.tasks.test_workflow_preparation`、`blackboard.tasks.test_continue_catalog_trust`、`blackboard.tasks.test_workspace_batch`、`blackboard.tasks.test_objective_summary_cache`、`blackboard.service.test_live_registry` 和 `blackboard.tasks.test_inquiry`。另外 `install.test_backup`、`install.test_backup_windows`、`install.test_backup_preflight`、`install.test_upgrade` 共 51 项通过。测试子进程清掉任务继承的运行身份与凭据变量，使用本检出源码、私有 HOME 和 harness 配置目录；没有往给定环境写依赖。

`install.test_workspace_identity_migration` 的 46 项也通过，合计上一轮最终分组验证 523 项；备份和工作区身份测试输出了既有未关闭数据库的 ResourceWarning，退出码均为 0。迁移测试升级真实 schema-17 样板并与新建 schema-18 看板逐项比对 schema，同时核对既有回合的 access、空理由、其余历史字段和所有未计划变更的表；profile 启用及可用状态保持，并可继续进入 read 候选。连续换档测试验证各回合记录独立、事件原子写入、继续沿用与 Git 输入选择不丢失；read 提交及路由覆盖四家，角色请求覆盖各家原生工具范围，full 无声明时拒绝，证据测试覆盖成功、失败、篡改与错绑。`git diff --check` 通过。

本环境不执行需要套接字、共享内存或真实启动器的测试：`install.test_upgrade_service.UpgradeServiceTests.test_real_private_service_reads_the_fixed_upgrade_and_uses_public_host_conclusions` 交给 Host；`install.test_local_launcher`、`install.test_runtime`、`install.test_first_install` 的真实启动器验证以及完整检查、看板副本升级、真实 harness 回合与故障注入也由 Host 执行。RPC 测试的 schema 断言已改为 18，旧账户退休断言一并删除，但这里不声称该测试通过。前端未改，未运行前端检查。

## 留给 Host 的公共说明修订

`docs/reference/architecture.md` 的 “schema version 17” 和 “Startup accepts schema 17 only; a schema-16 board ... installed 0.29.0” 应改为当前 schema 18、基准为已安装 0.30.0/schema 17。`docs/reference/operations.md` 的 “current source ... schema 17” 和 “one pending upgrade converts 16 to 17” 应改为唯一 17 → 18，并注明固定生成来源提交。`docs/reference/evaluation.md` 中 model_facts “part of the shared pending schema 16” 已过时；`docs/reference/objectives.md` 的 “Startup accepts schema 16 only”、schema-15 基准及 “15 → 16 transaction” 也应按当前升级重写。

`docs/reference/cli.md` 的 “access: read|write” 缺少 full 及其 accessReason，continue 字段说明缺少 access/accessReason，“executionWorkspace retains the original intent” 应改为当前选择；简要 get 现提供当前 access，完整 get 从回合列提供 access/accessReason。`docs/reference/workflow.md` 的 “access (read or write)” 及写占用说明应包含 full、理由和换档规则，并补充每个回合保留自己的选择。`skills/buddy/SKILL.md` 的第 4 条 continue 指引缺少权限换档及 full 理由，应由 Host 与拥有这些接口说明的公共页面一起更新。README、skill、docs/reference 和控制台均未修改；本步仍没有任何 harness 声明 full。

## 第二次打回的修正与验证计划

本轮不改升级基准、17 → 18 换算或 read 的原生运行方式；Host 已提供看板副本升级及中断、未知档位、重复升级的验收结果，这些属于 Host 的证据。路由失败来自手工建立的 DSH profile 没有新增 permission 能力字符串；共有的 RouterDispatchTestCase.profile 与 GovernedBoundaryTests 的建立处改为从当前 harness 声明取值，failover 的继承夹具随之修复，两个写死 requiredCapabilities 的断言补上派生的 permission:write。资格检查与刻意缺少能力的负面测试均未放松。

`test_run_contract` 的两个原生拒绝测试显式导入各家的 native_run 子模块，消除同进程其他测试的导入顺序影响。声明、请求契约、证据期望与角色测试的 enforcement 已统一为 convention，没有保留旧取值或别名；原来的 Worker 角色文本中普通英文 agreed 与这一字段无关，未改。

权限校验使用现有 optional_string 与明确的枚举判断；帮助抽取器增加通用的字符串默认值、按另一字段取值必填，以及 splitlines 单行规则的读取，不硬编码权限字段的帮助正文。submit 显示省略默认为 write，continue 省略保留当前选择；accessReason 显示 full 时必填、单行且最多 1000 字符。单行判定检查校验过类型的原始字符串，避免末尾换行被整理掉；协议测试保留对应拒绝案例。

按检查运行器的 child_environment 为每个测试文件新建私有 HOME、harness 配置、状态和临时目录，清除继承的运行身份与凭据；每个文件独立 Python 进程，最多四个并行。CLI 收据的体积测试先将本次临时状态根替换为固定占位符，再检查原来的 4000 字符门限，消除测试运行器临时路径长度造成的波动；标识与实际路径仍由原有断言核对。第一次清单试跑误包含真实冷启动测试，它在 AF_UNIX 路径过长的 IPC 前置检查处失败，未启动服务；该类随后纳入延期清单，没有修改 transport 或绕过沙盒。

## 本轮逐文件检查的延期清单

指定四个目录共 196 个测试文件；其中 189 个文件的可运行部分各用独立进程通过，共 3296 项，另外 7 个文件没有本环境可运行的测试。普通 unittest 模块发现与所选测试 ID 已比对，除明确延期的项目外没有漏掉导入的测试类或继承的测试。以下 32 个文件含环境或任务规则不允许执行的 147 项测试；整类选择器表示该类全部测试，部分延期的类列出具体方法。其他范围的真实升级服务与安装启动器测试仍按上文交给 Host。

静态清单的第一版漏列了几项调用真实 live 通道、冷启动 socket 前置检查、进程观察及前端依赖的测试；试跑后已按实际调用和错误补全。进程枚举报 ps process enumeration failed，清理如实保留私有根；没有放松停进程证据或把未知当作停止。临时运行器后来按检查运行器统一规范化私有根，纯模拟的 live_lifecycle_integration 在规范化后通过；需要实际 live RPC 的测试交给 Host，未修改其路径断言或实际通道。缺少 Vitest 的前端解析测试未安装依赖。每个混合文件的其余测试又分别在新进程通过；最终没有未解释的代码失败。

| 模块 | 延期选择器 | 原因 |
| --- | --- | --- |
| `blackboard.routing.test_readonly_tools_probe` | `ReadonlyToolsProbeTests.test_only_one_execution_is_possible_for_the_prepared_packet` | sandbox denies ps process enumeration required by clean teardown |
| `blackboard.service.test_daemon` | `DaemonHealthTests`、`DaemonPoolLifecycleTests.test_failed_start_drains_a_real_supervisor_through_its_private_instance_request` | real daemon/service, Worker supervisor, or C-Two IPC；starts real Worker supervisor |
| `blackboard.service.test_live_holder_integration` | `ServiceHolderIntegrationTests` | real C-Two IPC/socket or shared-memory peer |
| `blackboard.service.test_service_environment` | `ColdStartEnvironmentTests` | real Unix socket bind in cold-start preflight |
| `blackboard.store.test_blackboard` | `TestCrashWindows.test_a_killed_worker_keeps_the_surviving_child_uncertain_and_unretryable`、`TestDelivery.test_correlated_answer_survives_a_daemon_restart`、`TestExtensibility.test_an_external_agent_completes_a_real_task_through_the_public_api`、`TestLifecycle.test_work_survives_a_real_daemon_restart_and_reconnects_to_the_same_result`、`TestPackaging.test_the_default_cold_start_installs_and_reports_the_stable_runtime` | real daemon/service, Worker supervisor, or C-Two IPC |
| `blackboard.tasks.test_host_workflow_worker` | `CrossHarnessQuotaRecoveryTests`、`HostQuotaRecoveryTests` | real daemon/service, Worker supervisor, or C-Two IPC |
| `blackboard.tasks.test_parallel_dispatch` | `DaemonPoolTests` | real daemon/service, Worker supervisor, or C-Two IPC; one test also starts console HTTP |
| `blackboard.tasks.test_workflow_worker` | `ConsoleWorkflowRouteTests`、`RealWorkerTurnTests` | real console HTTP listener/socket；real daemon/service, Worker supervisor, or C-Two IPC |
| `blackboard.tasks.test_workspace_api` | `WorkspaceApiTests.test_console_refuses_scope_and_resolution_while_host_cli_remains_authorized`、`WorkspaceApiTests.test_five_named_methods_reach_the_real_ctwo_resource` | real console HTTP listener/socket；real daemon/service, Worker supervisor, or C-Two IPC |
| `buddy.harnesses.claude.test_claude_worker` | `ClaudeCatalogRuleTests`、`ClaudeWorkerTests` | real daemon/service, Worker supervisor, or C-Two IPC |
| `buddy.harnesses.dsh.test_dsh_role_wiring` | `WorkerRegisteredRunTests.test_an_answered_host_question_flows_through_the_governed_turn` | real C-Two live-controller inquiry; first attempt failed before inquiry on noncanonical temp root |
| `buddy.harnesses.test_c_two_live` | `NativeDeadlineTests`、`SubprocessLifecycleTests` | real C-Two IPC/socket or shared-memory peer |
| `buddy.harnesses.test_inquiry_owner` | `InquiryOwnerPeerTests` | real C-Two IPC/socket or shared-memory peer |
| `buddy.harnesses.zcode.test_zcode_checkpoint` | `ZcodeCheckpointFlowTests.test_a_child_relay_cannot_deliver_or_answer`、`ZcodeCheckpointFlowTests.test_a_completed_finish_is_refused_with_the_question_then_retries_after_answering`、`ZcodeCheckpointFlowTests.test_a_forged_answer_receipt_fails_the_whole_turn`、`ZcodeCheckpointFlowTests.test_a_pending_question_blocks_completed_but_not_assistance`、`ZcodeCheckpointFlowTests.test_a_question_after_the_final_receipt_becomes_honest_unavailable`、`ZcodeCheckpointFlowTests.test_an_unanswered_delivered_question_settles_unavailable`、`ZcodeCheckpointFlowTests.test_cancellation_with_a_queued_question_ends_honestly`、`ZcodeCheckpointFlowTests.test_one_send_delivers_and_answers_a_question_and_finishes_completed` | real C-Two live-controller inquiry and checkpoint |
| `buddy.harnesses.zcode.test_zcode_hooks_probe` | `FreeProbePipelineTests` | installed native harness/configuration or localhost model server |
| `buddy.harnesses.zcode.test_zcode_inquiry` | `LiveActivityTests.test_running_same_phase_native_events_refresh_the_published_observation`、`LiveActivityTests.test_the_runner_publishes_real_bound_metadata_only_live_activity` | real C-Two live-controller activity observation |
| `buddy.harnesses.zcode.test_zcode_native` | `DiscoveryReceiptTests`、`InstalledZcodeTests` | installed native harness/configuration or localhost model server |
| `buddy.harnesses.zcode.test_zcode_tool_refusals` | `ZcodeToolRefusalFlowTests.test_an_answer_refusal_recovers_in_the_same_turn_through_the_run`、`ZcodeToolRefusalFlowTests.test_pending_inquiry_refusal_then_checkpoint_answer_and_finish` | real C-Two live-controller inquiry refusal |
| `buddy.runtime.test_live` | `WorkerLiveRealPeerTests` | real C-Two IPC/socket or shared-memory peer |
| `buddy.runtime.test_repair_recovery` | `CompletionDuringOutage`、`RealDaemonRestart`、`RenewalAndRefusal`、`TerminationTruth.test_a_user_cancel_is_recorded_as_user_cancel`、`TerminationTruth.test_an_execution_deadline_is_recorded_as_deadline`、`TerminationTruth.test_explicit_zero_deadline_runs_to_completion_and_can_still_be_cancelled` | real daemon/service, Worker supervisor, or C-Two IPC；starts real Worker command child |
| `buddy.runtime.test_run` | `TestExecutionRecords` | real daemon/service, Worker supervisor, or C-Two IPC |
| `buddy.runtime.test_worker_live_wiring` | `WorkerEndpointClosureTests` | real C-Two IPC/socket or shared-memory peer |
| `buddy.runtime.test_worker_runtime` | `StagedWorkerRuntimeTests` | real staged launcher/install and Worker supervisors |
| `cli.test_checks_cleanup` | `PrivateRootTeardownTests.test_teardown_drains_a_cooperative_supervisor`、`PrivateRootTeardownTests.test_teardown_removes_a_clean_private_root`、`PrivateRootTeardownTests.test_teardown_reports_a_surviving_process_with_unlinked_lock`、`PrivateRootTeardownTests.test_teardown_waits_out_a_live_process_with_unlinked_lock` | sandbox denies ps process enumeration required by teardown |
| `cli.test_host_preview` | `PreviewFixtureTest.test_frontend_parsers_consume_the_emitted_fixtures` | frontend Vitest dependencies unavailable; installation forbidden |
| `cli.test_support_cleanup` | `FixtureCleanupTests.test_cleanup_stops_cli_started_replacement_after_restart`、`FixtureCleanupTests.test_private_state_context_stops_its_cli_started_service` | real daemon/service, Worker supervisor, or C-Two IPC |
| `protocol.test_ctwo_integration` | `CTwoIntegrationTests` | real daemon/service, Worker supervisor, or C-Two IPC |
| `protocol.test_ctwo_service` | `TestNamedContract` | real daemon/service, Worker supervisor, or C-Two IPC |
| `protocol.test_path_state` | `ReadonlyPathStateTests`、`RealPathStateTests` | real C-Two IPC/socket or shared-memory peer；real daemon/service, Worker supervisor, or C-Two IPC; cold-start AF_UNIX path can exceed length under deep root |
| `protocol.test_public_state` | `PublicStateTests.test_ps02_default_board_client_autostart`、`PublicStateTests.test_ps02_default_board_client_readonly`、`PublicStateTests.test_ps02_default_call_board`、`PublicStateTests.test_ps02_default_call_service`、`PublicStateTests.test_ps02_default_cli`、`PublicStateTests.test_ps02_source_launcher`、`PublicStateTests.test_ps03_default_restart_owned_service`、`PublicStateTests.test_ps03_default_stop_owned_service`、`PublicStateTests.test_ps03_no_service_stop_and_restart_do_not_start`、`PublicStateTests.test_ps04_environment_precedes_default`、`PublicStateTests.test_ps04_environment_selected_after_construction`、`PublicStateTests.test_ps04_explicit_precedes_environment`、`PublicStateTests.test_ps05_missing_readonly_client_never_creates_or_starts`、`PublicStateTests.test_ps05_readonly_attach_preserves_permissions`、`PublicStateTests.test_ps06_cold_helper_imports_with_source_only_environment`、`PublicStateTests.test_ps06_cold_start_probe_and_request_share_default`、`PublicStateTests.test_ps06_supplied_endpoint_keeps_default_directory`、`PublicStateTests.test_ps07_public_resolution_preserves_path_guards`、`PublicStateTests.test_ps08_call_board_directory_parameter`、`PublicStateTests.test_ps08_call_service_directory_parameter`、`PublicStateTests.test_ps08_readonly_client_directory_parameter` | real CLI/launcher/daemon startup, except two pure boundary tests |
| `protocol.test_rpc_config` | `IsolatedTransportTests`、`ProfileTests.test_active_root_is_fenced_and_complete_shutdown_allows_new_root`、`ProfileTests.test_bad_state_or_ipc_is_refused_without_repair`、`RealDaemonControlTests` | real C-Two IPC/socket or shared-memory peer；real daemon/service, Worker supervisor, or C-Two IPC |
| `protocol.test_transport_attach` | `RpcReadOnlySetupTests.test_group_readable_ipc_reaches_native_rpc_without_chmod`、`RpcReadOnlySetupTests.test_unsafe_ipc_is_not_chmodded_or_connected` | real C-Two IPC/socket or shared-memory peer |
