# ADR-030 批次 4：共用结果约定与角色层

本片以 `3a4a9637` 为输入，只改共用运行约定、角色投影、受影响测试及本文；运行模块、黑板、模型目录聚合、`validate_outcome` 与 `session_receipts.py` 不在写入范围。这里记录的是 Worker 检查结果，Host 尚未验收，未安装。

## 1. 未知的结束

`run_contract.EndStatus` 增加 `unknown`，编码与解码保留运行模块给出的原因码。角色层现有的成功条件全部是明确的 `ok`：`run_controller.execute` 的退出码、`schema_worker.NativeSchemaWorker.receipt_fields` 的成功结果检查、`run_execution._base_result` 的错误原因投影、`_fast_result` 与 `_review_result` 的答案投影、`_worker_result` 的交付、`WorkerRunExecutor.collect` 的回合采用、`structured_call.collect` 的成功状态以及 `router.DecisionAdapter.collect` 的答案采用。没有一处以“不是 error”作为成功条件，因此这些分支无需再加一套 unknown 特判。

Worker 的未知结束沿现有失败路径返回，保留原因码、用量、最后消息、已读会话事实和部分工作区产出，不发布完成交付或工作区封存。Router 的未知结束不生成 `rawAnswer`，不验证或采用决定；即使模拟控制器给出零退出码，Worker 也不认成功。结束未知与进程停止证据是两个独立事实：只有原生组和外层控制器均确认停止才算停止，未松开既有规则。

测试：`NativeEvidenceContractTests.test_unknown_end_and_bounded_native_evidence_roundtrip_losslessly`、`NativeEvidenceRoleTests.test_unknown_worker_end_keeps_facts_and_partial_artifacts_without_delivery`、`NativeEvidenceRoleTests.test_unknown_router_end_never_adopts_an_answer_and_keeps_native_facts`；`WorkerPermissionEvidenceTests.test_success_failure_and_unknown_end_retain_the_declaration_and_stop` 还让模拟的 unknown 结果经过真正的角色控制器编码，确认退出码为 1 且权限声明保留。

## 2. 请求边界拒绝空白

`RunConfiguration.provider/model/effort` 与 `RunContinuation.previous_session_id` 在原有字符串类型、长度与 NUL 检查之后统一检查 `strip()` 是否为空；有效字符串不被修剪或改写，重建续接仍可用 `None` 表示没有上一会话。本片不删各运行模块的重复检查，各 harness 接入时自行删除。

测试：`NativeEvidenceContractTests.test_blank_configuration_and_previous_session_are_refused_at_the_boundary` 经请求解码拒绝空格、制表符与换行、全角空格，并覆盖原生续接和重建续接；同时确认有效值保留原样及重建允许 `None`。

## 3. 共用原生事实出口

唯一的新出口是 `RunResult.native_evidence: NativeEvidence | None`，内部 JSON 的字段名为 `nativeEvidence`。`NativeEvidence.unknown_events` 是可选的 `UnknownEvents`，其 JSON 为 `{"countsByType": {"future-event": 2}, "total": 2}`；`NativeEvidence.unconfirmed` 是不可变名字元组，JSON 为名字数组。省略结果字段默认 `None`；省略两项分别默认 `None`、空元组，不从旧证据文件猜测或回填。

`UnknownEvents.counts_by_type` 在 Python 中保留为不可变键值对元组，构造和 JSON 解码只接收对象，JSON 序列化仍为对象。类型数最多 `MAX_UNKNOWN_EVENT_TYPES = 64`，类型名遵循 `Text(128)`：非空、无 NUL、最多 128 个 UTF-8 字节；每个计数与总数遵循既有 `Count`：严格的非负整数，最大 `2**53 - 1`。`unconfirmed` 最多 `MAX_UNCONFIRMED_ITEMS = 16` 项，每项 1 至 `MAX_UNCONFIRMED_NAME_CHARS = 64` 个字符，字符集合为小写 ASCII 字母、数字、连字符。约定只校验形状与上限，不判断名字的业务意义，也不判断某项限制是否生效。

`run_execution._base_result` 使用一个共用投影把有内容的两项放到 `nativeEvidence.unknownEvents`、`nativeEvidence.unconfirmed`；未知事件总数为零与空名字列表不投影。快速、审阅和 Worker 回合共用这个位置，失败和未知结束也保留事实。快速调用的既有原生投影改为合并，Worker 的 `workerPermission` 同样合并；Codex 快速与审阅既有投影仍放在其原有层级，新两项始终位于 `nativeEvidence` 下面，与 `captureEvidence` 开关无关。

四个 harness 后续把各自安全的事件分类计数与未证实项直接放进 `RunResult.native_evidence`，例如 `NativeEvidence(unknown_events={"countsByType": {"future-event": 2}, "total": 2}, unconfirmed=["configuration-readback", "thread-receipt", "tool-scope", "model-catalog"])`；示例名字不构成强制词汇。计数必须来自原生观察，不用请求值冒充回读，不把未经证实的权限写成已生效；类型过多时沿用已有有界分类方式，不丢总数；类型名和短名字不得携带原生载荷、路径或凭据。无法读懂结束原因时使用 `RunEnd(status="unknown", reason_code=<运行模块自己的原因码>)`。本片未改任何运行模块，所以真实运行尚未向这个出口供给两项事实。

Router 的 `DecisionAdapter.collect` 已原样带出 `nativeEvidence`，模拟成功与未知结束的测试均证明它到达 Router 结果。只读核对黑板链路：`blackboard/routing/decision.py` 的 `_complete` 保留结果对象，`_finish` 将完整输出写入 `decision_requests.output_json`，`_view(include_audit=True)` 原样返回 `output`，因此 Host 可以从 `decision_get` 的 `includeAudit: true` 结果读取 `decision.output.nativeEvidence`。常规决定摘要和 `routerTrials` 没有单列这两项；审计读取链路不受阻，摘要是否新增展示由 Host 决定。本片没有运行数据库持久化测试，也没有改黑板代码。

测试：`NativeEvidenceContractTests.test_native_evidence_refuses_bad_shapes_counts_and_unconfirmed_names` 覆盖对象形状、严格计数、类型数与短名字边界；`test_unknown_end_and_bounded_native_evidence_roundtrip_losslessly` 覆盖无损往返和不可变表示。`NativeEvidenceRoleTests.test_shared_native_facts_have_one_location_in_all_three_results` 用四个 harness 的模拟结果覆盖三种结果；`test_absent_and_empty_facts_are_omitted_from_every_result`、`test_fast_native_projection_merges_with_the_shared_facts`、`test_worker_permission_merges_with_the_shared_facts` 覆盖省略和合并。Router 的采用与拒绝测试还验证结果携带这些事实。所有新投影测试都使用假的运行结果，没有修改运行模块。

## 4. 快速调用的未知事件

删除 `run_observers.FastCorrection.observer` 对 `unknownEvents.total > 0` 的 `invalid-protocol` 中止，删除相应旧说明，并从 `read_fast_result` 的角色 verdict 合法原因集合删除这个已不存在的观察器原因。原生运行本身如实返回的 `invalid-protocol` 错误仍按运行失败转报，来源、停止、路径、参数、上限、能力和真实失败的拒绝没有放松。工具调用、工具标记、被拒绝交互仍立即以 `no-tool-violation` 中止；未知事件下的有效答案、一次格式纠正和枚举不纠正沿用普通规则。

已替换 Codex、DSH、ZCode 因快速观察器遇未知事件而中止的旧断言，Codex 测试里的 `ReviewCorrection` 副本也删除同一旧中止分支。ZCode 共用假服务器的 `unknown` 分支原来只发未知事件，然后等角色中止；新规则继续后会超时，因此仅为该分支补上完成事件和结算通知，其他夹具分支及断言未动。Claude 当前测试没有因共用快速观察器的未知事件中止而失效的断言，因此没有修改其目录。

测试：`NativeEvidenceRoleTests.test_unknown_fast_events_continue_to_an_adopted_router_answer`、`test_unknown_events_use_the_ordinary_format_correction_rule`、`test_tool_facts_markers_and_denied_interactions_still_stop_immediately`；Codex 的 `FastSeamTests.test_unknown_events_continue_through_the_observer_to_a_result` 与 `NoToolCodexTests.test_unknown_event_keeps_a_valid_answer`；DSH 的 `FastSeamTests.test_an_unknown_event_is_retained_and_continues_to_a_result`；ZCode 的 `NoToolPolicyTests.test_unknown_events_are_counts_the_fast_rule_continues_on` 与 `NoToolFakeProtocolTests.test_unknown_event_keeps_a_valid_answer`。旧的失败断言在同一改动中取代。

重新搜索角色层，未知原生事件导致失败的规则只有已删除的 `FastCorrection` 分支和其旧 verdict 读法；Worker 观察器与审阅观察器原本继续。`roles/structured_call.schema_errors` 对未知 JSON Schema 关键字的拒绝属于本项目调用参数不支持，保留。证据引用不匹配、角色 verdict 形状错误、停止不确认、缺能力、工具事件与既有真实失败也保留。`ZCode NoToolProtocol.observe_metadata` 仍有固定元数据种类、归属与序号混在一起的原生拒绝，现有 `NoToolPolicyTests.test_unknown_or_reordered_metadata_fails_closed` 仍覆盖它；那条断言没有经过共用快速观察器，属于 ZCode 运行模块自己的批次，未动。DSH/Claude 因缺失结束原因暂时报失败的断言同样由各运行模块接入 unknown 后取代，本片不替它们改运行模块或相关断言。

## 检查与交接

使用任务提供的只读依赖解释器，从本检出的 `src:tests/python` 导入源码。测试子进程使用 `cli.checks.child_environment`：先清除继承的 runtime、Worker、agent 凭据及环境钉住的路径，再给每个测试进程单独的私有 HOME、四个 harness 的配置根、状态根、运行时根与临时根；只启动离线夹具，没有启动已安装的 harness，没有读取用户主目录、日常看板或账户，没有模型调用。临时执行脚本和日志在系统临时目录，不属于交付文件。

最终受影响文件及相关角色回归共 13 个模块、311 条测试通过：`buddy.harnesses.test_run_contract` 37，`buddy.roles.test_run_native_evidence` 9，`buddy.harnesses.codex.test_native_run` 82，`buddy.harnesses.codex.test_no_tool_codex` 13，`buddy.harnesses.dsh.test_native_run` 60，`buddy.harnesses.zcode.test_no_tool_zcode` 10，`buddy.harnesses.zcode.test_zcode_tool_evidence` 27，`buddy.roles.test_permission_evidence` 3，`buddy.roles.test_registered_review` 4，`buddy.roles.test_schema_worker` 12，`buddy.roles.test_run_execution` 18，`buddy.roles.test_registered_run_wiring` 21，`buddy.roles.test_role_controller` 15。全部模块在上述隔离环境里完整运行，没有跳过测试；`git diff --check` 通过。首次新版 ZCode 测试因夹具不发完成事件而超时，修正上述单一分支后已通过；不是通过放宽结束或停止条件解决。

本片没有因套接字、共享内存或启动器限制而无法完成的受影响测试。完整检查、四个实际 harness 回合、安装后行为、归属/停止/私有路径/资源上限的实际故障注入、Windows 与 Linux 留给 Host；持久化及审计可见性这里只读核对源码，尚需 Host 集成验证。Host 还需协调各 harness 把事实供给新出口并删除它们的重复空白校验；若要在常规决定摘要或 `routerTrials` 显示原生事实，需另定黑板写入范围。任务指定排除的目录状态聚合、摘要数组超限、`router_input.py`、`discover_models` 和 C-Two 注册错误码未改。
