# ZCode 运行路径清单：抽取通用运行模块前的事实调研

## 范围与方法

本文只记录事实：ZCode 适配器的三条启动入口——Worker 执行回合的 `start`、快速路由的 `start_no_tool_structured`、审阅路由的 `start_read_only_structured`——现在各自实际做什么，为把"让一个 harness 运行一次并拿到事件、结果与停止证据"抽成通用运行模块做准备。调研对象是输入提交 26f9dd0 时的源码，范围为 `src/buddy/adapters/zcode.py`、`zcode_runner.py`、`zcode_protocol.py`、`zcode_mcp.py`、`zcode_config.py` 与共用的 `src/buddy/adapters/base.py`、`read_only.py`、`turn_io.py`、`decision.py`；每条事实带仓库相对路径与行号。这些文件调用的 `harness_runtime.controller_environment`、`harness_discovery.native_environment`、`harness_runtime.command_for` 与 `usage` 模块不在范围内，凡依赖其内部行为处标"未确认"。用词按 `CONTEXT.md`：执行回合是 Worker buddy 对微任务的一次执行。ZCode 没有实现的入口照实写"没有"。

## 1. 启动

入口与能力声明：`ZcodeAdapter` 声明 `capabilities` 含 `zcode/observe/inquiry/workspace/cancel/artifacts/deadline/native-session`，且 `native_resume`、`model_discovery`、`no_tool_structured` 均为 True（`src/buddy/adapters/zcode.py:36-39`）。`start_read_only_structured` ZCode 没有：`ZcodeAdapter` 未覆盖该方法（zcode.py:41-43 只定义了 `start_no_tool_structured`），落到基类直接抛 `UNSUPPORTED_ADAPTER`（`src/buddy/adapters/base.py:156-158`），`read_only_structured` 标志保持基类的 False（base.py:146），决策适配器因此在审阅路由拒绝它（`src/buddy/adapters/decision.py:53-54`）。

Worker 回合的 `prepare`（zcode.py:59-95）依次：校验 spec 的 provider/model/effort 为非空字符串（zcode.py:61-62）；用 `cli_command` 与 `provider_access_types` 核对所请提供方的 access 类型属于 `SUPPORTED_ACCESS`（zcode.py:63-69；`src/buddy/adapters/zcode_config.py:11,50-67`，OAuth 提供方不可用）；要求存在治理回合输入与 turn_id（zcode.py:70-71）；要求 resumeMode 是 initial/native-session/reconstructed-new-session 之一（zcode.py:72-73）；要求 `BUDDY_STATE_DIR`（zcode.py:74-76）；回合结果文件已存在则拒绝重复执行（zcode.py:77-78）。

Worker 回合接着写共享回合文件与私有目录：`turn_io.prepare_turn` 建目录、写 `task.txt` 与 `turn-input.json`、写代理凭据文件并校验工作区清单（`src/buddy/adapters/turn_io.py:134-155,199-207`）；native root 为 `native_root(state_dir, "zcode", task_id)`（zcode.py:80）；控制文件为 `context_root(...)/zcode-control.json`，内容含 directory、privateRoot、nativeRoot、cwd、timeoutSeconds、inputFile、outputFile、taskFile、inquiry 四元组（socketPath/resultsPath/errorPath/token）与 spec 三项（zcode.py:87-95）。

Worker 回合的 `start`（zcode.py:97-115）：打开 runner 日志（zcode.py:100-101；日志路径 `base.py:85-89`）；以 `owned_popen` 启动 `python -m buddy.adapters.zcode_runner --control <控制文件>`，cwd 为工作区（`turn_io.workspace_cwd`，turn_io.py:158-160），env 为 `controller_environment(context.directory, context.environment)`（内部未确认），stdin DEVNULL、新会话组（zcode.py:103-107）；handle.deadline 在 timeoutSeconds 为 0 时取无穷，否则 now+timeout（zcode.py:112-114）。

快速路由的 `start_no_tool_structured` 委托给 `read_only.start_no_tool`（zcode.py:41-43）。后者（`src/buddy/adapters/read_only.py:157-209`）：取冻结账号选择 `_account_environment(purpose='router')`（read_only.py:159,105-119）；拒绝任何工作流回合或代理凭据，要求 prompt、schema 有界且 timeout 是 0<t≤60 的整数（read_only.py:161-165；`NoToolStructuredRequest` 缺省 60 秒，base.py:128-136）；要求 cwd 是已存在的空、属主私有目录（read_only.py:167-173）；在 `context_root/no-tool-<uuid>` 建调用目录与证据目录（read_only.py:174-177）；控制文件写到 `context.directory/no-tool-control.json`，内容含 directory（=invocation）、nativeRoot（=invocation/native）、evidenceRoot、account、cwd、timeoutSeconds、spec 与 noToolRequest{prompt,outputSchema,captureEvidence}（read_only.py:178-189）；env 为 `controller_environment(..., read_only=True)`（read_only.py:191，内部未确认）；启动同一 runner 模块，cwd 为该空目录（read_only.py:197-203）；handle.deadline 为 now+timeout（read_only.py:205），并在 handle 上打 `no_tool`/`no_tool_control`/`no_tool_evidence` 标记（read_only.py:206-208）。决策适配器固定传 60 秒并先建空 cwd `no-tool-cwd`（decision.py:41-45）。

审阅路由：没有。ZCode 未实现该入口（见本节第一段）。

三种模式（外加模型发现）共用同一个控制器 `zcode_runner.run`（`src/buddy/adapters/zcode_runner.py:886-1184`）与同一段启动骨架（zcode_runner.py:886-930）：deadline 由 `execution_deadline` 给出，timeoutSeconds 为 0 时无穷（zcode_runner.py:688-695）；directory 与 nativeRoot 建为 0700（zcode_runner.py:890-892），私有根取 control.privateRoot 或 directory（zcode_runner.py:897）；命令经 `cli_command(os.environ)` 解析（zcode_runner.py:894-895；`zcode_config.py:15-35`，仅当 `BUDDY_DEV_SOURCE=1` 且设置了 `BUDDY_ZCODE_CLI` 时走开发覆盖，否则用 `harness_runtime.command_for`）；环境先经 `native_environment(incoming, command)` 清洗（zcode_runner.py:896，内部未确认），再由 `snapshot_provider_files` 把内建与个人提供方配置快照进私有根、只回带两个快照路径变量（zcode_runner.py:898-901；`zcode_config.py:70-89`，快照防止原生写回源偏好文件）；开发测试用例变量 `BUDDY_ZCODE_TEST_CASE` 透传（zcode_runner.py:902-903）。

启动骨架的环境变量与进程（三模式相同）：设置 `ZCODE_STORAGE_DIR=nativeRoot/storage`、`ZCODE_SESSION_DB_PATH=nativeRoot/sessions.sqlite`、`ZCODE_LOG_DIR=private/native-logs`、`ZCODE_LOG_CONSOLE=0`、`ZCODE_MODEL_TELEMETRY_ENABLED=0`（zcode_runner.py:909-911）；`--version` 探测 5 秒超时、失败记 unknown（zcode_runner.py:912-918）；原生 stderr 落 `directory/native.stderr.log`（zcode_runner.py:919-920）；以 `owned_popen` 启动 `[命令..., "app-server", "--cwd", control.cwd]`，stdin/stdout 管道、新会话组（zcode_runner.py:922-924）；启动失败立即返回 error 结果且 shutdownConfirmed=True（zcode_runner.py:925-927）。

启动阶段的相同与不同：三种模式的差别只在控制文件的键——`discover`（模型发现，非三条入口之一，zcode_runner.py:944-947）、`noToolRequest`（快速，zcode_runner.py:948-950）、或 Worker 的 inputFile/taskFile/outputFile/inquiry（zcode_runner.py:951-956）；控制器进程的 cwd 与 env 在适配器侧不同（Worker 用工作区与完整环境，快速用空目录与 read_only 环境），模型发现则用临时目录（zcode.py:210-242，`.zcode/config.json` 关闭 plugins/mcp/memory/skill/subagent，216-218）。

## 2. 会话与输入

Worker 回合的会话建立按 resumeMode 分发（zcode_runner.py:974-993）：previousSessionId 必须为 null 或非空白字符串（zcode_runner.py:977-978）；native-session 要求 previous 非空，读 nativeRoot 下 `<sha256(previous)>.json` 绑定文件并要求精确等于 `{taskId, sessionId, cwd, configuration}`（zcode_runner.py:980-988），然后调 `session/resume`（zcode_runner.py:989）；reconstructed-new-session 或 initial 且 previous 为 null 时调 `session/create`，带原生 mode "yolo"、titleGenerationEnabled=False 与私有 MCP 服务器（zcode_runner.py:990-991）；其余情形（含 initial 带 previous）抛 invalid-resume-mode（zcode_runner.py:992-993）。会话核对（zcode_runner.py:994-1004）：必须是根会话（无 parentSessionId，sessionKind 为 None 或 "interactive"）；native-session 恢复的 sessionId 必须等于 previous；reconstructed-new 必须不同于 previous；会话 workspacePath 必须与控制文件 cwd 解析后一致。

模型、提供方与推理强度的选择和核对由 `configure_session` 完成（zcode_runner.py:629-651），Worker 与快速两路共用：spec 完整性（zcode_runner.py:631-632）；提供方 access 属于 `SUPPORTED_ACCESS`，OAuth 拒绝（zcode_runner.py:634-635）；provider/model 必须在原生 available 目录里（zcode_runner.py:636-639）；effort 必须在该模型的 reasoning levels 里（zcode_runner.py:640-643）；依次调 `session/setModel`（带 `options.reasoningLevel`）与 `session/setThoughtLevel`，均 `persistAsWorkspaceLastUsed=False`（zcode_runner.py:644-646）；回读 `selected(snapshot)`（zcode_runner.py:621-626）并要求实际值与请求一致，否则 configuration-mismatch（zcode_runner.py:647-650）。

Worker 回合的输入组装：prompt 由 `governed_prompt` 拼出（zcode_runner.py:1062-1064），依次为治理前言、finish 契约、问询段（仅当挂了 checkpoint/answer 工具）、`ASSISTANCE_HINTS`（`turn_io.py:28-41`）、任务书原文（control.taskFile）、canonical_json(turn_input)（zcode_runner.py:601-617）。输入身份为 `input_id = "buddy-" + sha256(canonical_json({taskId, attemptId, generation, turnId}))`（zcode_runner.py:955,1010）；发送顺序为 `session/subscribe`（web-remote-replayable、无快照，zcode_runner.py:1059）、用量基线读（zcode_runner.py:1067）、`session/send`（zcode_runner.py:1069），admission 校验 accepted=True 且 sessionId 匹配（zcode_runner.py:1070-1071）。

快速路由的输入走 `_no_tool_call`（zcode_runner.py:838-883）：prompt 为 `no_tool_prompt`，即原 prompt 加 "\n\nReturn only one JSON value matching this schema: <schema>"（zcode_runner.py:842；`read_only.py:100-102`）；`session/create` 带 toolAllowlist=[]、mcpServers=[]、offPeakToolEnabled=False、dynamicWorkflowEnabled=False（zcode_runner.py:846-848）；根会话与 workspace 核对同 Worker 形状（zcode_runner.py:850-854）；模型核对共用 configure_session（zcode_runner.py:855）；input_id 为 `"buddy-no-tool-" + 随机 hex`（zcode_runner.py:857）；subscribe、send 与 admission 校验同形（zcode_runner.py:860-867）；pump 直到 settled（zcode_runner.py:868-871）；`session/close` 并要求 ack（zcode_runner.py:872-874）。最终答案是 turn.completed 事件携带的 response 文本，必须是不超过 65536 字节的字符串（zcode_runner.py:781-786），按 output_schema 校验（`read_only.py:81-97`，支持的 JSON Schema 子集见 read_only.py:35-74）；形状错误且属首次时重试一次——新开一个会话重发，prompt 追加 "Format correction: <code>. Return exactly the supplied JSON Schema."（zcode_runner.py:844,879-882；每次尝试各自建会话、各自 close，zcode_runner.py:1089-1090 注释）；enum 违规永不重试（read_only.py:94-97）。

审阅路由：没有。附带说明模型发现（非三条入口之一）：control 带 `discover: True` 时 `session/create`（toolAllowlist=[]，zcode_runner.py:945），用同一会话快照编目录——`catalog` 只收 SUPPORTED_ACCESS 提供方、必须暴露 reasoning levels，检测到 OAuth 时出警告（zcode_runner.py:654-685,947）。

## 3. 工具与权限

Worker 回合：会话以原生 mode "yolo" 创建，不传工具白名单，原生工具集默认全开（zcode_runner.py:991）；权限边界由治理输入的工作区校验承担——`verify_workspace` 要求清单未被改动或带路由决定（`turn_io.py:199-207`）。会话上挂一个会话级私有 MCP 服务器：名为 `"buddy_" + sha256(attemptId)[:16]`，命令 `python -m buddy.adapters.zcode_mcp --config <桥配置>`，env 只透传 PYTHONPATH，isolation "session"、protocolVersion "legacy"（zcode_runner.py:966-973），暴露 buddy_checkpoint、buddy_answer_inquiry、buddy_finish_turn 三个工具（`src/buddy/adapters/zcode_mcp.py:313-318`）。

Worker 回合的反向请求处置（`NativeConnection.pump`，`src/buddy/adapters/zcode_protocol.py:888-925`）：`session/requestRuntimePreferences` 回答 nativeSearchEnhancementsEnabled=True（no_tools=False）、memoryEnabled=False、askUserQuestionAutoResolutionEnabled=False、modelContextBudgetStrategy preflight-v1（zcode_protocol.py:889-894）；`interaction/requestProviderRuntimeHeaders` 记 attention 并以 -32601 拒绝、抛 unsupported-provider（OAuth 需要原生认证宿主，zcode_protocol.py:898-903）；`interaction/requestPermission` 记 attention 后回答 decision deny（zcode_protocol.py:907-913）；`interaction/requestUserInput` 记 attention 后回答 decline（zcode_protocol.py:914-920）；其余方法记 attention 并以 -32601 拒绝（zcode_protocol.py:922-924）。原生权限永不被批准，问询只走合作通道（zcode.py:27-35 注释；`COOPERATIVE_INQUIRY_NOTE`，zcode_protocol.py:119-124）。

快速路由：原生层禁工具——toolAllowlist=[]、mcpServers=[]、offPeakToolEnabled=False、dynamicWorkflowEnabled=False（zcode_runner.py:846-848）；cwd 是宿主新建的空目录（read_only.py:167-173；decision.py:41-43）；requestRuntimePreferences 得 nativeSearchEnhancementsEnabled=False（zcode_protocol.py:889-891，`not self.no_tools`）；任何其他反向请求一律 -32601 拒绝并抛 no-tool-violation（zcode_protocol.py:895-897）。事件层双保险：递归拒绝任何带 tool/agent./permission./userInput./subagent./workflow. 类型前缀、toolCallId、role=tool 或正 toolCalls 计数的载荷（`_reject_no_tool_events`，zcode_runner.py:801-816）；tool./agent. 类 session 事件直接 no-tool-violation（zcode_runner.py:753-755）；出现 no-tool-violation 时结果 usage 记 toolCalls=1（zcode_runner.py:1099-1100）。

审阅路由：没有。

## 4. 事件

传输层三模式共用：`NativeConnection`（zcode_protocol.py:822-962）以 NDJSON over stdio 通信，单帧上限 8 MiB（zcode_protocol.py:23,837-849）；`decode_json` 拒绝重复成员与非有限数（zcode_protocol.py:80-90）；读线程把帧放进容量 128 的队列并以 None 作 EOF（zcode_protocol.py:828,845-849）；`pump` 统一处理响应（zcode_protocol.py:926-929）、反向请求（zcode_protocol.py:888-925）与通知——通知递给 `connection.observe(message, ordinal)`，ordinal 单调递增（zcode_protocol.py:887,930-931）。

Worker 回合的观察链为 RootTurnEvidence（结束证据）、ZcodeAttemptUsage（用量）、ActivityProjection（活动）、InquiryBridge.note_event（问询桥的有限视图），每次事件后发布活动 sidecar（zcode_runner.py:1044-1057；sidecar 的校验与合并属 `buddy.activity`，发布失败只记原因、不失败回合，zcode_runner.py:1023-1035）。

Worker 回合的事件校验（`RootTurnEvidence.observe`，zcode_protocol.py:996-1093）：只认本会话（zcode_protocol.py:998-999）；state.updated 的 prompt_failed 抛 native-turn-failed（zcode_protocol.py:1001-1002）；prompt_completed 必须在 turn.completed 之后并记 settled_ordinal（zcode_protocol.py:1003-1006）；session/event 的 seq 必须严格递增（zcode_protocol.py:1010-1013）；turn.started 必须匹配 inputId 并记 turn_id 与 start_seq（zcode_protocol.py:1017-1021）；其它回合的事件忽略（zcode_protocol.py:1022-1023）；turn.failed 解码白名单归因后抛错（zcode_protocol.py:1024-1029）；turn.completed 必须 resultType=success、inputId 匹配且已有 finish 回执（zcode_protocol.py:1030-1037）；tool.updated 排除 child/relay/agent/background 来源（zcode_protocol.py:1039），finish 调用记 call_id/call_seq（zcode_protocol.py:1044-1050），checkpoint/answer 调用分别跟踪（zcode_protocol.py:1051-1058）；finish 结果为 error 或 success=False 时允许同回合重试（`_retry_failed_finish`，zcode_protocol.py:1155-1164），重复或截断为 finish-tool-failed（zcode_protocol.py:1066-1067），refusal 形状的内容先验签再重试（zcode_protocol.py:1069-1078,300-339），成功内容验签为回执（`verify_receipt`，zcode_protocol.py:237-262）。

Worker 回合遇到未知事件是忽略：未匹配的事件类型、其它 state.updated 原因与非 session/event 的方法都直接返回（zcode_protocol.py:1000-1009,1022-1023,1039）；连接层对全部通知一视同仁地交给观察链。

Worker 回合记成活动的部分（`ActivityProjection`，zcode_protocol.py:158-230）：只保留阶段、时间戳、事件序号、最后工具名与非负计数（zcode_protocol.py:163-166,219-230）；turn.started 计 modelTurns、tool.updated 的 scheduled 计 toolCalls（zcode_protocol.py:197-209）；permission.requested/userInput.requested 置 waiting-host（zcode_protocol.py:213-215）；prompt_completed/failed 与 turn.completed/failed 置 finishing（zcode_protocol.py:189-201）。

Worker 回合记成用量的部分（`ZcodeAttemptUsage`，zcode_protocol.py:586-762）：首选消息边界读——模型前 `session/messages` 基线（限 32 条，zcode_protocol.py:27,691-700）与结算后再读（限 64 条，zcode_protocol.py:29-30,702-746），只计本会话、有非空 parentMessageId 的根 assistant 消息（zcode_protocol.py:721-724），token 计数器缺一即整条不可用（zcode_protocol.py:540-560），游标在第二页重现则整页作废（zcode_protocol.py:716-719），页满记截断（zcode_protocol.py:720,746）；回退是本回合已开始后的 `v4/telemetry/event` usage.delta，按 eventId/eventSeq 去重、上限 4096 条（zcode_protocol.py:32,639-674），同一身份两种载荷使总量记为未知（zcode_protocol.py:666-671）；子代理活动使根-only 总量记为 partial（zcode_protocol.py:629-638）；`raw_usage` 取消息和，否则 delta（zcode_protocol.py:760-762）。两类读都经 `optional_native_call`，自带 2 秒预算且永不改变业务结果（zcode_protocol.py:34-35,497-515）；runner 在 finally 发布 tokenUsage 与 lastAssistantMessage（zcode_runner.py:1126-1131），适配器再用 `usage` 模块归一（zcode.py:132-136，模块内部未确认）。

Worker 回合的额度：唯一来源是 turn.failed 的白名单归因——`decode_native_failure` 只导入导出 schema 的枚举与有界字段、拒绝 URL 与凭据前缀文本（zcode_protocol.py:388-393,401-476），`quota_native_code` 经 `classify_quota_code` 判定后发布为 quotaFailure（zcode_protocol.py:479-494；zcode_runner.py:1113-1116）；额度窗口接口不存在，`quota` 恒为 null（zcode.py:128-134）。

快速路由的事件校验（`NoToolEvidence`，zcode_runner.py:698-798；准入前预检 `_no_tool_preflight`，zcode_runner.py:818-835）：只允许白名单事件族——模型文本流事件（zcode_runner.py:703-704）、会话生命周期事件（zcode_runner.py:705）、turn.started/completed/failed（zcode_runner.py:756）、两条生命周期元数据流及其 kind（zcode_runner.py:706-707,720-738）与少数遥测/存储通知（zcode_runner.py:745-746）；seq 严格递增（zcode_runner.py:760-763）；turn 事件绑定 input_id 与元数据 turnId（zcode_runner.py:770-777）；turn.failed、prompt_failed、未 success 的完成都抛 native-turn-failed（zcode_runner.py:737-738,778-779,781-782,790-791）；prompt_completed 必须跟在 completed 之后（zcode_runner.py:792-795）。快速路由遇到未知事件是失败：未知 session 事件（zcode_runner.py:756-757）、未知通知（zcode_runner.py:796-798）与准入前未知事件（zcode_runner.py:833-835）都抛 invalid-protocol；工具痕迹一律 no-tool-violation（zcode_runner.py:753-755,801-816）。结算后还要排干事件流：EOF 之前出现的请求或响应分别是 no-tool-violation 与 invalid-protocol（zcode_runner.py:1147-1167）。快速路由的用量只有 `usage={"toolCalls": 0}`（违规时 1）与事件计数（zcode_runner.py:875-878），没有 token 统计。

模型发现：不设观察者，`NativeConnection.observe` 缺省为空函数（zcode_protocol.py:832），事件全部忽略。

## 5. 结束约定

Worker 回合的结果由会话私有 MCP 工具 buddy_finish_turn 产生（zcode_mcp.py:313-318,334-352）：先 `validate_outcome`（`turn_io.py:210-251`，恰好六个字段、有界文本、completed 必须 request:null、assistance/attention 必须带完整 request）；参数错误返回签名拒绝信封 invalid-arguments（zcode_mcp.py:335-337）；completed 且有未解决的本地注意请求返回 attention-outstanding 拒绝（zcode_mcp.py:338-342）；completed 且有未答问询返回 inquiry-pending 拒绝，列出至多 4 条（`MAX_PENDING_IN_FINISH_REFUSAL=4`，zcode_mcp.py:46,343-348,207-227）；否则签发回执 `{version 1, identity, inputSha256, outcome, receiptId, HMAC 签名}`（zcode_mcp.py:230-233,360-367）。

Worker 回合的控制器侧验收：`verify_receipt` 要求不超过 70000 字节、字段集精确、HMAC 通过、version/identity/inputSha256 绑定、receiptId 32 位、outcome 再过 validate_outcome（zcode_protocol.py:237-262）；turn.completed 必须携带 success 且已有回执（zcode_protocol.py:1030-1037）；`provenance()` 要求 start_seq<call_seq<result_seq<end_seq 且 0<completed_ordinal<settled_ordinal<close_ordinal（zcode_protocol.py:1166-1169）。回合记录由 runner 独占写出：`{version 1, 身份四元组, inputSha256, promptSha256, sessionId, previousSessionId, resumeMode, outcome, provenance}` 以 exclusive 方式写 control.outputFile（zcode_runner.py:1085-1087,1094-1096,1181-1183）。

Worker 回合的适配器侧 collect（zcode.py:117-172）：读 stdout 上的控制器结果 JSON（上限 512 KiB，zcode.py:288-297）；shutdown 为结果 processState.shutdownConfirmed 与 `handle.shutdown_confirmed()` 的合取——原生 app-server 自成一个进程组，两个组都确认消失才算（zcode.py:123-125）；`turn_io.read_turn` 再验一遍：exit 0、shutdown 确认、记录不超过 96 KiB（`MAX_RECORD_BYTES`，turn_io.py:20,254-267）、version 1、身份五元组匹配（turn_io.py:273-278）、inputSha256 匹配（turn_io.py:279-280）、validate_outcome、sessionId 存在（turn_io.py:281-285）、以及适配器的 `validate_turn_provenance`（zcode.py:177-208：固定事实集 zcode.py:180-182、nativeSessionId 与 sessionId 一致 zcode.py:185-186、四个 id 非空 zcode.py:187-189、inputId 等于 "buddy-"+sha256(identity) zcode.py:190-193、seq 与 ordinal 有序 zcode.py:194-198、续接身份规则 zcode.py:199-208）；被拒的原生交互请求使 completed 不成立（zcode.py:146-152）；ok 时封存工作区（zcode.py:162-169；`turn_io.py:293-307`）。

快速路由的结果与校验：runner 发布 rawAnswer/answerValid/usage.toolCalls=0/nativeEventCount（zcode_runner.py:875-878），status ok 且 shutdown 时置 zeroToolVerified=True（zcode_runner.py:950）；控制器把同一 JSON 结果写到 stdout（zcode_runner.py:1200），由 `read_only.collect` 读取（`read_only.py:248-287`；上限 256 KiB，read_only.py:212-221）；status ok 但 zeroToolVerified 非 True 或 toolCalls 不为 0 时改判 invalid-native-result（read_only.py:256-261）；status ok 需要原生 ok、exit 0、processState.shutdownConfirmed 与 handle.shutdown_confirmed 双确认（read_only.py:262-264）；证据保留把启动时冻结的绑定中 call-1/call-2 的 request/result 对拷进证据根，一次调用都没观察到则用冻结请求加最终载荷合成 call-1（read_only.py:186-187,224-245），保留失败降级为 evidence-retention-failed（read_only.py:267-285）。决策适配器的 collect 再包一层（decision.py:68-134）：清理空 cwd（decision.py:73-77）、核对镜像输入（decision.py:78-84）、组装 usage 与 stopEvidence（decision.py:85-106）、错误码映射（decision.py:108-116）、快速路由再次核对零工具（decision.py:117-119）、最后以 router.validate_answer 校验候选（decision.py:122-127，router 模块不在范围内）。另见 `ZcodeAdapter.collect` 自身也把 no_tool 句柄分流给 read_only.collect（zcode.py:118-120）。

审阅路由：没有。

## 6. 停止与证据

取消：Worker 用 `ZcodeAdapter.cancel`，grace 8 秒（zcode.py:174-175）；快速路由经 `DecisionAdapter.cancel`，grace 12 秒，注释说明控制器把 SIGTERM 转成原生中断再收自己的组（decision.py:136-138）；二者都落到 `ProcessHandle.terminate`——SIGTERM 发给拥有的进程组，宽限轮询后 SIGKILL，再等 2 秒（base.py:262-282）；基类缺省 grace 3 秒（base.py:183-184）。

取消与超时在控制器内表现为 NativeError：send 与 pump 在写或等之前检查 cancelled 事件与 deadline，分别抛 "cancelled" 与 "timeout"（zcode_protocol.py:857-861,874-878）；信号处理在 main 只 set 事件（zcode_runner.py:1187-1194）；NativeError code 为 "cancelled" 时 status 记 cancelled，其余记 error（zcode_runner.py:1097-1098）；run 末尾 cancelled 事件强制 status=cancelled 并丢弃回合记录（zcode_runner.py:1173-1175）。超时上限由 `execution_deadline` 给出，timeoutSeconds 为 0 时仅此一处无穷（zcode_runner.py:688-695）；版本探测、可选元数据读、取消与关闭等待各保有更短的有限上限（zcode_runner.py:913；zcode_protocol.py:34-35；zcode_runner.py:1142-1145）。

finally 收尾（三模式共用，zcode_runner.py:1125-1146）：关 stdin；handle.wait 至多 3 秒且受剩余 deadline 约束；`shutdown_confirmed(0.2s)` 不成立则 terminate(1.0s)；再以 0.5 秒结算确认；发布 `processState={shutdownConfirmed, nativeExitCode}`（zcode_runner.py:1146）。

停止证据的产生（共用）：status ok 要求 shutdown 且原生退出码为 0，否则 native-shutdown-failed 并丢弃记录（zcode_runner.py:1176-1178）；控制器把最终 JSON 结果写到 stdout（zcode_runner.py:1200）。适配器侧 Worker 另要求外层控制器进程组确认消失——控制器与原生 app-server 各成一组，缺一不可（zcode.py:123-125）；Worker 的证据链还要求会话 close 被 ack（zcode_runner.py:1090-1093）且 close 时间序进入 provenance（zcode_runner.py:1094-1096；zcode_protocol.py:1166-1178）。Worker 工件只在 shutdown 时收集：task.txt、turn-output.json、attention.json 与 runner stdout/stderr 日志，各带大小与 sha256（zcode.py:172,310-319）。快速路由的停止证据为 processState.shutdownConfirmed 加 handle.shutdown_confirmed 双确认（read_only.py:262-263），另加结算后 EOF 排干无违规（zcode_runner.py:1147-1167）；decision 侧发布 stopEvidence 汇总（decision.py:103-106）。

## 7. 续接

Worker 回合的续接依赖两样状态：治理回合输入里的 resumeMode 与 previousSessionId（zcode_runner.py:974-978），以及 nativeRoot 下的会话绑定文件 `nativeRoot/<sha256(sessionId)>.json`，内容恰好为 `{taskId, sessionId, cwd, configuration}`（zcode_runner.py:987,1006-1009）。native-session 模式读绑定文件并要求与当前目标、检出与配置完全一致，否则 native-resume-unavailable，然后 `session/resume`，恢复的会话必须是原会话（zcode_runner.py:980-989,998-999）；initial 与 reconstructed-new-session 新建会话后以 exclusive 写绑定文件（zcode_runner.py:990-991,1006-1009），reconstructed-new 要求新会话 id 不同于 previous（zcode_runner.py:1000-1001）。provenance 校验的续接规则与之一致：native-session 要求 sessionId 等于非空 previous；initial 要求 previous 为 null；reconstructed-new 要求 previous 为 null，或非空且 sessionId 不同（zcode.py:199-208）。

绑定文件的另一读者在适配器：`_native_session` 报告 bindingPresent 与 resumable——绑定存在、shutdown 确认且 provenance settlement 为 session-closed 三者同时成立（zcode.py:245-285，判定在 zcode.py:262-279）。会话存储位于该目标私有的 nativeRoot（ZCODE_STORAGE_DIR/ZCODE_SESSION_DB_PATH，zcode_runner.py:909-911），宿主 ZCode 应用的会话列表看不到（zcode.py:280-284）。快速路由没有续接：每次调用新建会话，不写绑定，也不读 previous（zcode_runner.py:846-848）。审阅路由：没有。

## 8. 只属于 Worker 回合的东西

治理回合输入与身份：turn claim 写成私有文件（turn_io.py:141-146）；inputId 由身份四元组导出并双侧校验（zcode_runner.py:955,1010；turn_io.py:279-280；zcode.py:190-193）。

代理凭据：attempt 级凭据写入私有文件并按路径导出 `BUDDY_AGENT_CREDENTIAL_FILE`、`BUDDY_TASK_ID`、`BUDDY_ATTEMPT_ID`（turn_io.py:147-155）；控制器进程以 `controller_environment(context.directory, context.environment)` 启动（zcode.py:105），这些变量是否最终到达原生 app-server 取决于范围外的 `native_environment`（未确认）；MCP 子进程 env 只透传 PYTHONPATH（zcode_runner.py:972-973）。

问询桥（`InquiryBridge`，zcode_runner.py:50-598）：控制文件携带 socketPath/resultsPath/errorPath/token 四元组（zcode.py:93；路径生成在 turn_io.py:169-196，socket 路径超预算时退到短临时目录，turn_io.py:166,177-185）；桥在回合内启动（zcode_runner.py:1037-1042）、send 被 admit 后激活（zcode_runner.py:1072-1073）、结算即关闭（zcode_runner.py:1079-1082,1132-1137）；Host 问题只排队、从不注入（`_ask`，zcode_runner.py:532-567；`COOPERATIVE_INQUIRY_NOTE`，zcode_protocol.py:119-124）；投递与作答只认本回合的 tool 证据加签收回执（`deliver_inquiries` zcode_runner.py:298-321、`record_answer` zcode_runner.py:323-358；接线 zcode_runner.py:1011-1017；回执验签 `verify_inquiry_receipt`，zcode_protocol.py:765-819）；日志为 attempt 私有、独占 flock 追加（zcode_runner.py:215-271），MCP 侧以共享锁读（zcode_protocol.py:346-373；zcode_mcp.py:127-167）。errorPath 生成并写进控制文件（turn_io.py:190；zcode.py:93），但 ZCode 控制器自身不消费它（对照 `harnesses/dsh` 侧的使用，src/buddy/adapters/dsh.py:118,186,227）。

会话工具 MCP 服务器（zcode_mcp.py 全文件）：桥配置文件含 identity、inputSha256、key（随机 hex32）、attentionPath、inquiryJournalPath（zcode_runner.py:956-965）；三个工具的每种预期拒绝都以签名 refusal 信封返回（`_refusal`，zcode_mcp.py:236-281，detail 按含包装前缀的线格式预算做二分适配，zcode_mcp.py:259-277）；finish 在有未解决注意请求（zcode_mcp.py:338-342）或未答问询（zcode_mcp.py:343-348）时拒绝 completed；checkpoint 批量投递并携带 morePending（zcode_mcp.py:284-302,323-331）。

本地注意记录：`NativeConnection` 把被拒的原生交互请求记成 attention（`attention_request`，zcode_protocol.py:933-946），桥落盘 attention.json（`note_attention`，zcode_runner.py:442-460；路径 zcode_runner.py:957），MCP 的 finish 读取计数并拒绝 completed（`attention_requests`，zcode_mcp.py:111-124），适配器 collect 二次核对（zcode.py:146-152）。

协助提示词：`ASSISTANCE_HINTS`（turn_io.py:28-41）拼进治理提示（zcode_runner.py:615）。

工作区：prepare 时 `verify_workspace` 校验清单（turn_io.py:199-207），collect 时 `seal_workspace` 封存（turn_io.py:293-307；调用 zcode.py:164-169），清单随结果发布（zcode.py:156-157）。

回合记录文件 turn-output.json：runner 独占写出（zcode_runner.py:1181-1183），适配器读回并全量校验（turn_io.py:254-290；zcode.py:140,177-208），并作为 turn-result 工件上报（zcode.py:154,310-319）。

活动与用量收集：ActivitySidecar 发布（zcode_runner.py:1023-1035）、ZcodeAttemptUsage 的基线与终读（zcode_runner.py:1067,1078,1119-1120,1126-1131）以及 `_native_session` 会话事实报告（zcode.py:245-285）都只存在于 Worker 回合路径。

## 9. 重复

会话建立与核对写了两遍：Worker 分支（zcode_runner.py:991-1004）与 `_no_tool_call`（zcode_runner.py:846-854）各自实现"create 会话、根会话校验、workspacePath 与 control.cwd 一致"。

subscribe、send 与 admission 校验写了两遍：Worker（zcode_runner.py:1059,1069-1071）与快速（zcode_runner.py:860-861,865-867）形状相同。

session/close 加 ack 校验写了两遍：Worker（zcode_runner.py:1090-1093）与快速（zcode_runner.py:872-874）。

事件状态机写了两遍：`RootTurnEvidence`（zcode_protocol.py:996-1093）与 `NoToolEvidence`（zcode_runner.py:698-798）平行实现 seq 单调、turn 绑定、turn.completed/failed 与 settlement 的先后关系；state.updated 的 prompt_completed/prompt_failed 解释还出现在 `ActivityProjection`（zcode_protocol.py:188-193）与 `_no_tool_preflight`（zcode_runner.py:827-828）。

失败回合的识别写了多处：turn.failed/prompt_failed 抛 native-turn-failed 在 `NoToolEvidence`（zcode_runner.py:737-738,778-779,790-791）与 `RootTurnEvidence`（zcode_protocol.py:1001-1002,1024-1029）各一处，元数据层 `observe_metadata` 再一处（zcode_runner.py:737-738）。

child/relay/agent/background 的排除写了三遍：`RootTurnEvidence`（zcode_protocol.py:1039）、`ZcodeAttemptUsage`（zcode_protocol.py:630-638）、`_reject_no_tool_events`（zcode_runner.py:801-816）各一种写法。

spec 与 access 校验写了两遍：适配器 prepare（zcode.py:61-69）与 `configure_session`（zcode_runner.py:631-635）都查 provider/model/effort 完整与 SUPPORTED_ACCESS。

停止证据的双确认组合写了两遍：`read_only.collect`（read_only.py:262-264）与 `zcode.collect`（zcode.py:123-125,137-139）各自组合 processState.shutdownConfirmed、handle.shutdown_confirmed 与退出码。

控制文件编写与 spawn 骨架有三份近似副本：`read_only.start`（审阅用，read_only.py:122-154，ZCode 不走）、`read_only.start_no_tool`（read_only.py:157-209）与 `zcode.start`（zcode.py:97-115）。

输入组装有两套：快速路由的"prompt 拼 schema 加一次格式纠正"（zcode_runner.py:842-882；read_only.py:100-102）与 Worker 的 `governed_prompt`（zcode_runner.py:601-617）。

取消入口有三个包装：`base.Adapter.cancel`（base.py:183-184，3 秒）、`ZcodeAdapter.cancel`（zcode.py:174-175，8 秒）、`DecisionAdapter.cancel`（decision.py:136-138，12 秒）。

## 10. 对照表

行是第 1 到 7 项的步骤，列是三种模式；"相同"指三模式（或两模式，审阅列为"没有"时指两模式）走同一实现。

| 步骤 | Worker `start` | 快速 `start_no_tool_structured` | 审阅 `start_read_only_structured` |
| --- | --- | --- | --- |
| 1a 控制文件 | 不同：zcode-control.json，含回合文件路径与问询四元组（zcode.py:87-95） | 不同：no-tool-control.json，含 noToolRequest 与证据根（read_only.py:178-189） | 没有 |
| 1b 私有目录 | 不同：attempt 目录加 native_root(state)（zcode.py:80） | 不同：no-tool-<uuid> 调用目录加证据目录（read_only.py:174-177） | 没有 |
| 1c 控制器进程 | 不同：cwd 为工作区，env 为 controller_environment(context.environment)（zcode.py:103-107） | 不同：cwd 为空目录，env 为 read_only=True（read_only.py:191,197-203） | 没有 |
| 1d runner 启动段（env 清洗、提供方快照、ZCODE_*、app-server spawn） | 相同（共用 run()，zcode_runner.py:886-930） | 相同 | 没有 |
| 1e handle 截止时间 | 不同：可为无穷（timeoutSeconds=0，zcode.py:112-114） | 不同：有界 1..60 秒（read_only.py:162,205） | 没有 |
| 2a 会话建立 | 不同：session/create(yolo) 或 session/resume（zcode_runner.py:989-991） | 不同：session/create 空白名单（zcode_runner.py:846-848） | 没有 |
| 2b 会话与 workspace 核对 | 相同（同一组检查：根会话加 cwd 一致；实现两份，zcode_runner.py:994-1004 与 850-854） | 相同 | 没有 |
| 2c 模型/提供方/强度选择与核对 | 相同（共用 configure_session，zcode_runner.py:629-651） | 相同 | 没有 |
| 2d 输入组装 | 不同：governed_prompt 加任务书加 turn_input（zcode_runner.py:601-617,1062-1064） | 不同：prompt 加 JSON Schema 附录，至多一次格式纠正（zcode_runner.py:842,879-882） | 没有 |
| 2e 输入身份 | 不同："buddy-"+sha256(identity)（zcode_runner.py:1010） | 不同："buddy-no-tool-"+随机 hex（zcode_runner.py:857） | 没有 |
| 3 工具与权限 | 不同：yolo 加私有 MCP 三工具，原生交互记 attention（zcode_runner.py:991,966-973,898-924） | 不同：原生禁工具，任何交互即违规（zcode_runner.py:846-848,895-897） | 没有 |
| 4a 事件传输 | 相同（同一 NativeConnection，zcode_protocol.py:822-962） | 相同 | 没有 |
| 4b 事件观察者 | 不同：RootTurnEvidence 加用量加活动加桥（zcode_runner.py:1044-1057） | 不同：NoToolEvidence 全拒绝式（zcode_runner.py:698-798） | 没有 |
| 4c 未知事件 | 不同：忽略（zcode_protocol.py:1008-1023,1039） | 不同：失败（zcode_runner.py:756-757,796-798） | 没有 |
| 4d 用量与额度 | 不同：消息边界读加 delta 回退加 quotaFailure（zcode_protocol.py:586-762；zcode_runner.py:1113-1116） | 不同：只有 toolCalls 计数（zcode_runner.py:875-878） | 没有 |
| 5a 结束约定 | 不同：finish 工具签收回执加回合记录文件（zcode_runner.py:1085-1096,1181-1183） | 不同：turn.completed 文本按 schema 校验加 zeroToolVerified（zcode_runner.py:781-786,950） | 没有 |
| 5b 结果校验 | 不同：read_turn 加 validate_turn_provenance（turn_io.py:254-290；zcode.py:177-208） | 不同：valid_answer 加证据保留（read_only.py:81-97,224-245,248-287） | 没有 |
| 6a 停止证据 | 不同：processState 双确认相同；Worker 另需 close ack 与有序 settlement 证据（zcode_runner.py:1090-1096） | 不同：双确认相同；另加结算后 EOF 排干（zcode_runner.py:1147-1167；read_only.py:262-263） | 没有 |
| 6b 取消 | 不同：grace 8 秒（zcode.py:174-175） | 不同：经 DecisionAdapter grace 12 秒（decision.py:136-138） | 没有 |
| 6c 超时 | 不同：可无穷（zcode_runner.py:688-695；zcode.py:112-114） | 不同：不超过 60 秒（read_only.py:162） | 没有 |
| 7 续接 | 不同：三模式加绑定文件（zcode_runner.py:974-1009） | 不同：没有续接，每次新会话（zcode_runner.py:846-848） | 没有 |

## 11. 抽取时的观察

一、`run()` 已经是三种模式共用的"运行一次"函数（zcode_runner.py:886-1184）：环境清洗、提供方快照、ZCODE_* 变量、app-server spawn、版本探测与 finally 收尾（zcode_runner.py:893-930,1125-1146）对各模式逐字相同，可直接成为通用运行模块的主体（第 1、6 节）。

二、`configure_session`（zcode_runner.py:629-651）与 `NativeConnection` 的传输层（zcode_protocol.py:822-962）不含角色逻辑，可整体上移（第 2、4 节）；唯一的模式分支是 requestRuntimePreferences 的 nativeSearchEnhancementsEnabled 取反（zcode_protocol.py:889-891），可用一个布尔参数替代。

三、反向交互请求的处置是角色差异：Worker 记 attention 后按方法 deny/decline/拒绝（zcode_protocol.py:898-924），快速一律 -32601 加 no-tool-violation（zcode_protocol.py:895-897）；公共模块应把处置策略作为注入回调，而不是让 no_tools 布尔扩散进泵循环（第 3 节）。

四、会话建立与核对在两条分支各写一遍（zcode_runner.py:846-854 与 991-1004），可合并为一个"建立根会话并核对 workspace"的助手，create/resume 与会话选项作参数（第 9 节）。

五、两个事件证据类结构平行（zcode_protocol.py:996-1093；zcode_runner.py:698-798）：seq 单调、turn 绑定、turn.completed/failed、settlement 先后是共同骨架；"未知事件忽略还是失败"与"工具痕迹如何定性"是模式策略，必须留在角色一侧（第 4 节）。

六、结束约定是最大障碍：Worker 的结果是"finish 工具签名回执、回合记录文件、适配器全量复验加 provenance 有序证据"（zcode_mcp.py:334-352；zcode_runner.py:1085-1096；zcode.py:177-208），快速路由是"turn.completed 文本、schema 校验、zeroToolVerified、证据保留"（zcode_runner.py:781-786,950；read_only.py:248-287）；两条证据格式没有公共形状，运行模块只能统一事件收集与停止证据，结果校验必须留在各入口（第 5 节）。

七、停止证据可以统一：两种模式最终都落到 processState.shutdownConfirmed、原生退出码与 handle.shutdown_confirmed 双组确认（zcode_runner.py:1125-1146,1176-1178；zcode.py:123-125；read_only.py:262-263），仅快速的 EOF 排干（zcode_runner.py:1147-1167）是模式附加项，可作可选钩子（第 6 节）。

八、续接天然属于 Worker：绑定文件与 resumeMode 规则（zcode_runner.py:974-1009；zcode.py:199-208）依赖治理输入，快速路由明确没有续接（zcode_runner.py:846-848）；运行模块应把会话建立参数（含 resume 决策）留给角色侧传入（第 7 节）。

九、问询桥、会话工具 MCP、attention 文件、活动与用量收集都挂在 Worker 的 observe 链与控制文件键上（zcode_runner.py:956-973,1037-1061），是运行模块之上的可选能力层；它们已通过回调解耦（on_delivery/on_answer，zcode_runner.py:1011-1017），沿这条缝抽取即可（第 8 节）。

十、适配器层的 collect 是第二个障碍：`zcode.collect`（zcode.py:117-172）导入回合记录、封存工作区、归一用量，`read_only.collect`（read_only.py:248-287）做零工具判定与证据保留；二者只共享"有界 stdout JSON 加双确认"的读取形状，统一时要么抽一个共同的"读控制器结果"前缀，要么接受两个收集函数（第 5、9 节）。
