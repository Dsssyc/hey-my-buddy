# ADR-025 第四步 4E2：黑板问询与 live 接缝测试的 DSH 注册迁移

本记录是 ADR-025 第四步收尾微任务 4E2 的交付材料（基线 `60682b0`，独立受管 worktree，唯一可写为三个测试文件与两份记录）。Host 完整检查（其台账 `step4-full-check1.log`，本轮只读）暴露三个模块仍把 DSH 当作未抽取项：`blackboard.tasks.test_inquiry` 的 `TestInquiry` 仍以旧 Node 桥协议与无绑定凭证夹具直接驱动（9 项失败），`buddy.harnesses.test_live_channel` 仍期待 dsh 宣布 `realtime`（1 项），`buddy.roles.test_role_live_seam` 仍把 dsh 的未登记状态当作 live 通道三态的自然事实（1 项）。本轮把三者迁到当前共同接口：DSH 已通过注册模块与 ExistingLiveChannel 提供合作检查点问询；不恢复旧 Node 通道、不新造角色通道、第五步 C-Two 切换不做。全程零模型调用、零安装版 harness、零公共文件改动。

## 迁移方式

- `TestInquiry` 的 `FakeBridge` 保持无模型传输替身，但改说共享合作桥的线缆协议：一次连接一帧 version 1 JSON、单 token 核对、`observe`/`ask` 方法、id 回显与 16 KiB 帧界，`refusal`/`state`/`observation` 三处可操纵它给出的承诺状态、拒绝码与观察值。不 mock 任何生产 inquiry/LiveChannel 函数。
- 夹具存下真实最小请求/身份与路径绑定（对照同文件已通过的 `ZcodeLiveChannelTests` 与注册模块的 `bind_live_channel`）：public `role-run-request.json` 是真实编码的 `RunRequest`（harness=dsh，完整 `RunIdentity`），private `role-run-control.json` 点名 invocation 与治理 turn 输入，`turn-input.json` 即该输入，`inquiry.json` 为尝试自有桥凭证。黑板真实 `inquiry_observe` 于是走完整生产路径：`stored_run_request` 校验身份关系 → 注册表 `live_binding("dsh")` → `ExistingLiveChannel` → 唯一共享传输客户端 → FakeBridge 套接字。
- journal 记录从旧 Node 形状（无 version、答案为兄弟散字段）改为共享绑定形状（version 1 + taskId/attemptId/generation/turnId + 嵌套答案对象，queued 记录携带 delivery 记录）。旧裸文本形状的通道级投影见证仍由 `test_live_channel` 的 `test_journal_source_fields_and_delivery_survive_the_projection` 承载，黑板终态恢复读取器对两种形状的接受保持不变。
- `test_role_live_seam` 的未抽取态按任务书改为显式注入注册缺席（`mock.patch.dict(RUN_SEAMS, {"dsh": None})`，与同文件既有两处先例一致），不把已登记 DSH 当未抽取。

## 断言处置（编号不变，逐项见表）

- 观察协议见证（`test_observation_uses_the_real_bridge_protocol`）：经注册通道观察 live（sessionId、deliveryMode cooperative-checkpoint、activity 正向发布 toolName），补 version/token 帧断言。
- 原生工具参数不发布（`test_observation_does_not_publish_native_tool_arguments`）：保护不变，形态从旧白名单过滤改为严格 live 模型整体拒绝——夹带 `argumentPreview` 的 activity 项使整次观察被拒（`observation-unavailable`），私有串不出现在任何结果。
- 问询关联与错误 token（`test_a_question_is_correlated_and_a_wrong_token_is_refused`）：ask 的 inquiryId 关联与 `unauthorized` 精确拒绝保留；`delivered`→`queued` 为用户已接受的合作检查点送达差异（送达与答案事实改由 journal 承载），另补"未授权 ask 是可重试拒绝、消息留 queued"。
- journal 形状导入（`test_the_real_bridge_journal_shape_is_imported_with_reply_tool_evidence`）：答案文本、`tool:buddy_checkpoint`/`tool:buddy_answer_inquiry` 证据、`bridge-journal` 来源与幂等重导入断言保留；旧 `messageId` 投递关联随 Node 通道退役——共享 delivery 记录是 journal 传输证据，其键不在消息库有界投递键白名单（与 zcode live 测试的既有口径一致，本轮以运行证据确认）。
- 无文本 answered 降级、journal 幂等（含撕裂行忽略）、`journal-unavailable` 终态拒绝（错误码精确、消息 unavailable、run 保持 running）、外来 journal 拒绝（`journalRejected` 含 another、答案永不导入）：断言意图不变，仅记录改绑定形状。
- `agent-gone` 终态码见证原样保留：共享桥自身以 not-ready 加 journal 终结表达回合结束，共享传输词汇仍保留 agent-gone，此测试钉住黑板对该终态码的处理，由替身恰好如此拒绝。
- `test_inquiry_capability_comes_from_the_adapter_registry` 断言未动，注释更正（DSH 不再"实时注入"，与 ZCode 同为合作检查点）。`test_live_channel` 仅 dsh 能力期待 `realtime`→`cooperative-checkpoint`。`test_role_live_seam` 仅未抽取态夹具改注入。其余 16+43+9 项编号与断言未动。

## 缺口与差异报告

- 未发现实际行为不等价的公共缺口。三处 Host 所有源文件的陈旧表述仅报告不改（公共文件归 Host）：`src/hey_my_buddy/buddy/harnesses/live.py` 的 `EXISTING_CAPABILITIES` 注释仍写"DSH keeps its current Node realtime delivery until step four moves it to ACP"（其字典值已正确为 cooperative-checkpoint）；`src/hey_my_buddy/blackboard/tasks/inquiry.py` 模块 docstring 仍写"the Node dsh plugin hosts one inside the upstream process"；同文件 `_apply_bridge_answer` 内注释仍称"the dsh bridge reports agent-gone/agent-not-running this way"（共享桥不再如此，但该处理对保留的传输词汇仍正确）。
- 行为差异仅一项且属用户已接受范围：问询只在合作检查点送达，ask 即时回报 queued。错误 token、跨 task/外来 journal、身份每成分、队列/字节界、答案来源、重放/撤回（zcode 侧既有见证）、只观察能力与精确拒绝原因的原保护未弱化。

## 验证证据

- 任务根以下记作 `<task-root>`（确切路径只在交付摘要与任务 tmp 台账）；全部命令的 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 指 `<task-root>/t/`，并按检查运行器口径清除继承 runtime/Worker/agent 变量、置 `BUDDY_DEV_SOURCE=1` 与私有 claude/codex CLI 哨兵、`BUDDY_CONSOLE_PORT=0`；一次性材料全部在 `<task-root>/m/4e2-*` 新子目录，未覆盖旧实验、未触碰其他会话对象；普通测试夹具自建的临时目录照常自收尾。
- 基线复现（改动前，`m/4e2-baseline/baseline-run.log`）：三模块 83 项，11 项失败——TestInquiry 9、live_channel 1、role_live_seam 1，与 Host 完整检查台账中的失败同名同断言。
- 迁移后（`m/4e2-baseline/migrated-run2.log`）：同三模块 83 项全绿（约 29 秒），无跳过。
- 编号对账（`m/4e2-ids/`）：以基线 `60682b0` 的三文件原副本与迁移后副本分别经 TestLoader 加载真实 unittest ID，29+44+10=83 对 83 逐项相等，diff 为空。
- 故障注入恰好两项（均在任务根一次性副本上注入并运行，未触碰受管检出与公共源码；应失败→原件通过）：M1 身份绑定——副本在夹具写入后将 stored request 的 `identity.turnId` 改为 `another-turn`（单个有效类型化成分失配）→ 通道不绑定、journal 不读 → `test_the_real_bridge_journal_shape_is_imported_with_reply_tool_evidence` 断言 `'queued' != 'answered'` FAILED（`m/4e2-mutation-identity/m1-run.log`）；M2 答案来源——副本把 answered 记录的 `taskId` 改为 `another-task`（外来答案来源）→ 绑定投影拒绝、`journalRejected` 置位 → 同一断言 FAILED（`m/4e2-mutation-answer/m2-run.log`）；两份原件随全量运行通过。

## 未验证边界

- 第五步 C-Two 切换未开始：本轮全部证据都在 ExistingLiveChannel 现有文件与套接字后端上，C-Two 契约下的同套保护归第五步迁移见证。
- FakeBridge 只替身对端桥；真实 DSH 驱动进程内共享 InquiryBridge 的驱动权限路径（deliver_inquiries/record_answer 的回执核验）不在本轮见证范围，由 DSH 原生测试与 Host 冒烟承载。未跑完整检查（归 Host 两项交付整合后的默认完整检查）。
