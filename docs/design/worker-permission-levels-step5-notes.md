# Worker 权限档位第 5 步说明

2026-10-13，在输入提交 `2aded1c5` 的受管检出实现 DSH 的三档原生预设；Host 在续接输入中修订了第 5 项，要求拒绝操作摘要进入原生证据，Worker 自己签发的结果保持原样。本文记录实现与本环境逐文件验证，真实 DSH 的权限效果、完整检查、安装和最终验收由 Host 负责。

## 原生设置与载体

| Worker 权限档位 | 子进程的 DSH_PERMISSION_MODE | 原生工具范围声明 | 强度声明 |
| --- | --- | --- | --- |
| 只读 / read | read-only | read | enforced |
| 写检出 / write | workspace-write | write | enforced |
| 放开 / full | danger-full-access | full | enforced |

`dsh/native_run.py::_launch_policy` 从本次 `RunRequest.worker_permission.level` 取得预设，不从工具范围反推。每次启动 Worker 进程都显式给 `DSH_PERMISSION_MODE`，覆盖继承环境中的同名变量；写检出不再依赖基础 profile 的默认值。原生 patch 仍只为 Worker 指定会话记录与 JSON 投影的保存位置，没有权限配置行，也没有读取或修改用户配置、创建配置主目录或改变用户的原生功能。

`run` 按是否带 Worker permission 选择受管 Worker 载体，三档使用同一条原生会话、结束工具、合作式问询与签名 receipt 路径。带档位却不挂载服务，或挂载 Worker 服务却没有档位，属于调用方参数错误，入口在启动前拒绝；没有 Worker permission 却请求 full 工具范围也属于调用方参数错误。原来的 DSH full 未实现拒绝已删除。载体选择不依据 read、write 或 full 的大小，只读 Worker 因此可以照常通过结束工具结束。

无 Worker permission 的调用继续由 `tool_scope_launch` 表达工具范围。Router 快速调用的 none 配置保持原样：禁用已确认的十五个工具行及 plan-mode，不新增权限环境变量，会话记录与 JSON 投影路径照旧。原有非 Worker 的 read、write 原生测试路径也保持原值；它们没有变成 Router 审阅能力。DSH 的审阅声明和 `local_read_only_check` 的 `readonly-worker-carrier-unimplemented` 原因未改。回归测试逐项检查 none 的 patch 顺序、行名、策略事实、启动权限环境键与假原生进程实际收到的权限变量。

`registry.py::_WORKER_PERMISSIONS` 只改 DSH 一行：read 改为 read/enforced，write 保持 write/enforced，新增 full/enforced。另外三个 harness 的行与表的顺序没有变；能力字符串沿用现有唯一声明生成路径，DSH 新增 permission:full。共用能力与拒绝断言仅更新 DSH 的部分；路由测试的现有手工 buddy 夹具已经使用所属 harness 的声明能力，无须再改夹具。full 候选测试明确检查原有 Codex 和两个 DSH 夹具进入候选。目录测试新增 DSH full 的断言，权限生命周期正常用例删除两处“模拟未来 DSH full”的能力补丁，缺能力的负面用例则显式从唯一声明移除 DSH full；Claude 的测试部分保持原样。既有迁移测试扩展 DSH full 的库存能力断言，迁移源文件、schema 版本和存储形式均未改。

## 策略事实与续接

`_RunState.permission_mode` 保存本次交给启动路径的环境值，`_effective_policy` 将其写入现有 `RunResult.effectivePolicy.tools.requested.permissionMode`，三档都有实际请求的值；`native_evidence` 对有预设的策略同时提供 nativePolicy 投影。无工具的 Router 投影仍保持原来的字段和值。Worker 初始化在打开会话之前失败时，也保留已经交给进程的 requested 预设；缺少续接绑定而未进入启动路径时不生成预设事实。这些记录说明交出去的设置，不宣称原生已经采用或强制它们。

`_goal_binding` 保持任务、会话、工作目录、供应方、模型与推理强度的现有绑定，权限档位没有加入绑定。初次、重建与原生续接使用同一处启动配置，所以续接请求带新档位时，新进程按新档位启动，旧回合保留旧策略事实。没有增加运行中切换预设的操作，也没有预先增加换档新开会话逻辑。

`test_native_resume` 新增 read→write 和 write→full 两项，每项启动两个离线假 DSH 进程，记录两次实际环境，断言第二回合使用 session/resume、没有 session/new、会话身份复用、新回合结束工具绑定有效且两回合策略事实分别保留。新增 `test_worker_permissions` 检查三档覆盖继承的权限变量、原生结果编码后保留策略、只读 Worker 正常结束与问询载体、重建回合使用当前 full 档位，以及三档初始化失败仍留下已请求的预设。

## 被回绝的操作与 Worker 陈述

`dsh/acp/connection.py::permission_operation` 在收到原生 session/request_permission 时提取工具的 `toolCall.title`，以及 `toolCall.rawInput.command` 或 `toolCall.rawInput.path`，优先 command。它不保留其他输入字段；原生没有可用标题或命令、路径时，如实标成 unknown tool 或 input unavailable，不从模型陈述或其他回合补值。生成的 operation 文本在这一处按 UTF-8 截到最多 512 字节，尾部不完整的字符舍弃，后面的消费者不再次截断。

连接将同一个有界 operation 交给权限策略，并保存到该请求的 permissionDecisions。`_AttentionPolicy.decide` 将同一个值加入本回合注意记录；带 inquiry 时交给现有 InquiryBridge，随 attention-report 的 last 项留下，不带 inquiry 时写入现有注意文件。回合的 permission-decisions 证据引用包含同一个摘要与原生请求标识、选项和拒绝决定。拒绝规则仍选择 reject_once、reject_always 或 cancelled，没有因这次改动授予提权或增加新的原生拒绝条件。

Worker 的结束参数、签名 receipt 和返回 outcome 继续保持原样，`worker_services.py` 未改；摘要由 Host 从原生证据读取。这取代了此前说明文件里要求扩展写入范围、在签名前补写 request.attempted 的方案。测试通过带与不带 inquiry 的原生拒绝请求核对两处 operation 相同、包含原生路径、中文长路径按字节有界，以及 Worker 的 attempted 仍是它自己的陈述。连接测试另核对原生命令与路径摘要可读，其他请求参数和帧日志继续只留元数据；旧的“权限决定从不保留工具标题”的断言随新需求替换。

## 逐文件验证

使用 Host 指定的现成只读 Python 解释器，没有使用 uv 或安装依赖；每个文件一个独立进程，按 `python -m unittest <该文件的模块>` 执行。含不适合本环境的用例的文件仅选择该文件里的可运行类或方法，未把不同文件放进同一进程。子进程沿用检查运行器的 child_environment，本检出的 src 与 tests/python 排在前面，清掉继承的运行身份、Worker、凭据与虚拟环境变量，每个文件有自己的 HOME、harness 配置、状态、运行与临时目录。

最终去重结果为 78 个文件、1,294 项测试全部通过，unittest 自身没有跳过项；下面的 31 个测试实例按环境约束未运行。基于完整批次记录和随后对最终改动的局部复跑统计，每个文件只计最后结果；声明核对还补跑了目录、权限生命周期与迁移三个受影响文件。原始逐文件日志与最终去重摘要 `summary-final.json` 在 ignored `tmp/step5-tests/`，它们是本地验证材料，未作为已接受的公共验收记录。`git diff --check` 通过。

| 范围 | 执行文件数 | 通过项数 |
| --- | --- | --- |
| buddy/harnesses/dsh，包括 acp 与新增权限文件 | 12 | 233 |
| buddy/harnesses 顶层 test 文件 | 14 | 338 |
| buddy/roles | 15 | 157 |
| blackboard/routing 全部 test 文件 | 33 | 530 |
| blackboard/tasks/test_workflow_worker 的两个离线类 | 1 | 2 |
| blackboard/catalog/test_catalog、tasks/test_permission_levels、store/test_migrations | 3 | 34 |
| 合计 | 78 | 1,294 |

没有启动真实 harness、真实 hey-my-buddy 服务、启动器或 Worker，没有访问用户配置、凭据、日常看板，没有登录或模型调用。没有运行 install、前端、完整检查或探针。本批次的真实服务与 C-Two 子进程用例留给 Host；以下列明名称，子类继承的测试也属于未运行项。

`buddy.harnesses.test_c_two_live.NativeDeadlineTests` 的六项：`test_first_connection_expires_pre_dispatch_and_recovers`、`test_already_connected_peer_expires_pre_dispatch_and_recovers`、`test_zero_connection_and_call_budgets_are_native_pre_dispatch`、`test_dispatched_call_expires_but_same_connection_and_remote_call_survive`、`test_delayed_owner_never_consumes_an_expired_queue_entry`、`test_connection_time_does_not_renew_the_owner_queue_window`；它们使用真实 C-Two 子进程端点。

`buddy.harnesses.test_c_two_live.SubprocessLifecycleTests` 的十二项：`test_one_run_one_endpoint_with_a_clean_stop`、`test_the_owner_settles_the_real_handoff_with_refusal_then_retry`、`test_the_same_name_on_two_addresses_does_not_cross_wire`、`test_old_tokens_instances_and_identity_components_are_rejected`、`test_wrong_and_oversized_frames_over_real_transport`、`test_a_closed_endpoint_refuses_but_keeps_serving_facts`、`test_the_per_run_inquiry_limit_over_real_transport`、`test_a_killed_endpoint_is_unavailable_and_never_stopped`、`test_a_live_endpoint_is_busy`、`test_a_credential_for_another_endpoint_is_stale_target`、`test_a_committed_request_id_holds_one_real_queue_entry`、`test_a_stalling_endpoint_returns_within_the_native_window`；它们使用真实 C-Two 子进程端点。

`buddy.harnesses.test_inquiry_owner.InquiryOwnerPeerTests` 的两项：`test_real_ctwo_owner_queue_requires_verified_cooperative_native_delivery_and_answer`、`test_tampered_receipt_and_child_native_evidence_cannot_upgrade_rpc_queue_receipt`；它们启动真实 C-Two owner 子进程。

`blackboard.routing.test_readonly_tools_probe.ReadonlyToolsProbeTests.test_only_one_execution_is_possible_for_the_prepared_packet` 需要以 ps 枚举进程检查残留，即使原生执行已被 mock，仍不适合本沙盒，其余七项已经通过。

`blackboard.tasks.test_workflow_worker.RealWorkerTurnTests` 的两项：`test_real_worker_imports_a_turn_and_seals_the_output`、`test_cli_fails_closed_for_an_agent_scoped_caller`，以及 `ConsoleWorkflowRouteTests.test_console_command_route_strips_the_capability_and_refuses_override`，启动真实服务、Worker 或控制台；本文件的 CompactRouteTests 与 SubmissionPreparationRaceTests 已通过。

`blackboard.tasks.test_host_workflow_worker.HostQuotaRecoveryTests` 的三项：`test_quota_failure_seals_then_continues_a_new_configuration_in_the_same_checkout`、`test_quota_failure_cannot_override_a_user_locked_configuration`、`test_failed_goal_conclusion_reclaims_checkout_but_retains_partial_artifacts`；`CrossHarnessQuotaRecoveryTests` 继承这三项，并另有 `test_failed_codex_reconstructs_in_dsh_with_bound_partial_output_and_message`，共七个测试实例。这整个文件启动真实服务与 Worker，未运行。

## Host 在真实 DSH 上核对

强制声明依据本任务给定的 DSH 0.1.5-rc.1 原生预设事实，假 DSH 只能证明参数交付与协议载体，没有模拟或证明原生沙盒。Host 需逐档量文件在检出内、检出外与临时目录的写入，以及网络、本机端口、Unix 套接字、共享内存和进程枚举，核对基础 profile、原生审批策略和三档声明是否符合实际。

Host 需做真实的 read→write 和 write→full 续接，核对同一个会话是否采用新进程的预设、旧会话是否存在切换事件或显式授权而覆盖它；量出旧预设实际保留时再决定下一步。还需核对只读 Worker 的结束工具与问询能正常工作、被回绝的提权请求在注意记录和权限决定证据里留下实际工具及命令或路径摘要，以及 Worker 自己签发的结果没有被控制器改写。Windows 与 Linux 的权限效果、真实回合、故障注入与安装均未在本回合验证。
