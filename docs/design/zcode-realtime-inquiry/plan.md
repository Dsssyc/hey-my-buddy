# ZCode Worker 实时问询执行计划

2026-10-31，基线 `3dac46de`；Host 审定后收窄。依据 [ADR-006 的当日决定](../../decisions/006-zcode-adapter-integration.md)、[本机调研](../../research/2026-10-31-zcode-dsh-in-turn-input.md)、[公开调研](../../research/2026-10-31-in-turn-input-public-sources.md) 与 [ADR-023](../../decisions/023-harness-integration-principles.md)。原生协议依据是 ZCode app 随包 CLI 0.17.0；本轮允许无模型探针及实现、受影响测试，不做真实模型回合，不写验收记录。

## 1. 先核实原生前提

新增 `tests/probes/zcode_realtime_inquiry.py`，只用私有 HOME、配置、存储和工作目录启动已安装 app-server，阻断外网，不连日常黑板。读取快照、订阅及命令校验应答，使用明确排队的输入验证 held 队列和删除；不发会启动模型的输入。固定 sendText、deleteQueueItem、队列读取及事件的实际字段到 `tests/python/buddy/harnesses/zcode/fixtures/`，区分录制与构造应答。

继续实现有两个前提：排队或 guide 回退的条目在没有显式发送时不会自动执行；`baseRevision` 可以不带，或者能从原生快照取得。探针发现不成立就停止交回，写明证据。只在协议要求时带原生 revision，不传 `expectedHeldQueueItemIds`。没有 expected-turn 绑定的事实保留；现有第二次根 `turn.started` 的 `wrong-native-turn` 检查继续承担越回合失败，不新增停止规则。

## 2. 送达和状态

在 `src/hey_my_buddy/buddy/harnesses/zcode/inquiry.py` 新建驱动，参照 Claude `_send_pending_inquiries` 和 Codex `_NativeInquiry`，复用现有 app-server、`NativeConnection` 与 `RootTurnEvidence`。桥先提交 queued，pump 的 `after_pump` 在空读和 RPC 等待期间也轮询；保留 attention 反馈并防止 RPC 等待重入。仅原生 pump 线程发命令。

根 turn 已绑定、尚未结束且 finish 尚未 scheduled 时，向当前根 session 发 `v4/command`，`type: sendText`、`requestedDelivery: guide`，正文包含精确 inquiryId、问题与挂载的回答工具名。commandId 用随机值；驱动在内存记录已尝试的问题，每题只发一次，同 ID 的 Host 重发沿用桥现有去重，同 ID 异文仍冲突。`NativeConnection.call` 没有单次超时，不做原封重放、命令查询、noop/duplicate 恢复或换 revision 重投。RPC 内事件继续处理。

| 原生结果 | 黑板状态 |
| --- | --- |
| accepted，delivery 为 guide | 保持 queued，只有匹配本根回合及本题原生输入的 `turn.steerDrained` 才提交 delivered。 |
| accepted，delivery 为 queue 或 startNow | 尝试撤回该条 held 输入；问题保持 queued，结束时按现有规则 unavailable。 |
| noop、duplicate、stale、rejected | 不恢复、不重发，不凭裁决写 delivered；保持 queued，结束时 unavailable。 |
| JSON-RPC 错误 | 仅在问询驱动边界捕获 `NativeRpcError`，作为没送进处理，保持 queued，不拖垮执行。 |
| 缺必要字段或无法关联 | 不声称送达，保持 queued；可能残留队列项时禁止写续接绑定。未知额外字段仍按现有规则记事实。 |

`turn.steerQueued` 只作佐证，不是送达门槛；`turn.steerDrained` 须匹配原始 inputId 所绑定的根 session、turn 和本题 pendingInputId，不能拿子代理、转发、别的回合或会话的事件凑证据。把必要关联写到现有 nativeCorrelation，不改事实格式。事件可能先于 ack，驱动在本次问询数量上限内保留关联事实，ack 到达后即可对账。

回答沿用根工具成功结果、签名、attempt 与问题摘要验证；精确的根回答本身证明模型收到问题，允许现有 `record_answer` 从 queued 直接变 answered，不暂存回答等 ack。终态后的迟到答案按现有规则忽略。原生 sendText 输入经新增的 `ZcodeSessionProjector.record_input` 进入会话记录。

## 3. 撤回、finish 与结算

只删除本次命令关联的队列条目：deleteQueueItem 一次，随后重读队列一次，不能把 ack 的 accepted/noop 当成删除成功。重读发现还在或无法确认时，不停执行、不清空结果，仅不写该会话的续接绑定；不得清空队列或删除其他输入。对已续接会话也要使旧绑定不能继续被选用。不要调用 sendQueuedNow、setAutoDrain 或新 session/send，也不新增重试机制。

发送前根已结束不发命令；命令在途时结束，继续对账已来的事件和裁决，未送达保持 queued。最终答复段的 queue/startNow 回执按上节撤回。finish scheduled 之后的准入门看 `RootTurnEvidence`，不依赖尚未关闭的 bridge.active。`RootTurnEvidence` 将 finish 后 scheduled 的回答工具记作迟到事实，不抛 duplicate-finish、不覆盖已收结果；其他非法根工具的现有检查保留。正常关闭、停止与服务结算沿用现有顺序，未答问题仍为 inquiry-queued-at-attempt-end 或 inquiry-delivered-at-attempt-end，answered 不降级。

## 4. 删除 ZCode 协作路径，保留 DSH

ZCode 只挂 answer 和 finish，桥配置写实际 toolNames，利用 `session_mcp.py` 已有过滤，不留 checkpoint 别名。ZCode 的 checkpoint 名称、回执追踪、旧注释和专属断言同批删除；挂载按工具名字取值，去掉 tuple 位置假设。ZCode finish 不再被未答问询拦 completed/waiting；参数错误和 attention 拦截仍保留。

共享层只改 `buddy/roles/run_execution.py`、`worker_services.py`、`texts/worker.txt` 与 `buddy/harnesses/live.py` 的送达声明：由实际能力选择工具、提示与 finish 规则，DSH 继续挂 checkpoint、收签名回执并拦未答的 completed/waiting。`inquiry_bridge.py` 只顺带修 snapshot 的 immediateDelivery 常量，使实时通道不再被列为不可用；不动其状态处理。`protocol/inquiry_journal.py`、`session_mcp.py`、`dsh/native_run.py` 不改，除非探针证实有必要并交 Host 收敛范围。无 SQLite、journal、原因码或跨进程事实格式变化，这批按补丁处理，不动契约。

## 5. 文件与当前说明

原生实现落在 `src/hey_my_buddy/buddy/harnesses/zcode/` 的 `inquiry.py`、`native_run.py`、`protocol.py`、`session_content.py`。桥构造明确 realtime 和无 checkpoint；`live.py` 同批声明 zcode 为 realtime，DSH 仍 cooperative-checkpoint。运行模式只来自实际能力，不加用户开关。

先改 `docs/reference/harness-integration.md` 的 Inquiry delivery，再改 `cli.md`、`usage.md`、`workers.md`、`architecture.md` 的 ZCode 问询说明，写明原生 guide、drained 对账、最终答复段可能送不到、残留队列禁止续接及尚无真实模型验收。入口 `docs/README.md` 和共享 skill 按实际引用更新。旧 checkpoint 验收记录作为历史，不用它证明新能力。

## 6. 测试与验证

| 测试根 `tests/python/` 下文件 | 验证 |
| --- | --- |
| 新 `buddy/harnesses/zcode/test_zcode_realtime_inquiry.py`；改同目录 fixtures/mock_zcode.py | 根不调用 checkpoint 仍能收题/回答；五种裁决、三种 delivery、RPC 错误、相同问题不重发、随机命令 ID；queue 回退删除及重读；假成功不丢结果且禁续接。 |
| `buddy/harnesses/zcode/test_zcode_protocol.py`、`test_native_run.py` | drained 独立证明、queued 仅佐证、事件在 ack 前后；错根/turn/input、子代理/转发；发送前结束、在途结束、finish 后不再发送、迟到回答非致命；保留第二根 start 的现有失败检查。 |
| `buddy/harnesses/zcode/test_zcode_inquiry.py`、`test_zcode_tool_refusals.py`、`test_zcode_turn_io.py`、`test_zcode_tool_evidence.py`、`test_zcode_tool_projection_order.py`、`test_session_content.py` | 替换旧 checkpoint 专属用例；保留签名、根回答、attention、结构化结果与工具事实证据；会话记录含 Host 输入。 |
| `buddy/roles/test_worker_services.py`、`test_role_prompts.py`、`test_run_execution.py`；`buddy/harnesses/test_live_channel.py`、`test_harness_conformance.py`、`test_inquiry_owner.py` | 实际工具、角色文字、finish 规则与声明一致；公共 journal 与状态行为不变。 |
| `buddy/harnesses/dsh/test_native_run.py`、`test_dsh_role_wiring.py`；`test_in_turn_inquiry.py`、`codex/test_in_turn_inquiry.py`（位于 buddy/harnesses/） | DSH 检查点仍收题/拦 finish，Codex/Claude 实时通道不变；Router 和无工具调用不装 Worker 驱动。 |
| `blackboard/tasks/test_inquiry.py`、`blackboard/store/test_inquiry_settlement.py`、`protocol/test_inquiry_transport.py` | 现有 queued/delivered 未答结算、服务导入与 answered 保留。 |

通过仓库检查运行器 `uv run --frozen python -m hey_my_buddy.cli.checks --jobs 1 <受影响文件或目录>` 分步验证，清掉继承的 HMB 状态/运行时/凭据和虚拟环境变量，不直接起测试进程，不与 Host 的完整检查重叠。本轮不改应用布局，不另跑浏览器或全套。原始探针输出只放忽略的 tmp，固定夹具脱敏，不写绝对主目录或凭据。

后续真实模型验证收缩为 2 次实验，每次须开发者另行批准，本轮均不做：一次验证工具边界 guide/drained/根回答与相同问题去重；一次验证最终答复段及结束竞争、撤回重读、迟到回答与残留队列禁续接。每次可能包含多次模型 API 请求，未命中目标时机记未覆盖，重跑另批。源码、原生实验与安装验证分别报告。

不另建执行通道，不改用户配置，不新增未使用开关，不恢复协作检查点回退，不新增状态、原因或重放框架。探针前提不成立就按实际证据交回 Host；前提成立才继续实现与提交，提交信息使用英文且无 AI 署名。

## 本轮探针结果与交回边界

已按上述收窄范围运行无模型探针，CLI 实测为 0.17.0。固定应答在 `tests/python/buddy/harnesses/zcode/fixtures/inquiry-app-server-0.17.0.json`；原始输出留在 `tmp/zcode-realtime-inquiry/3e6a7e18107f460fb6b01782ac85b7ad/`。所有配置、工作目录及 SQLite 都是本探针新建的私有内容；外网被阻断，未发 session/send 或 guide，没有原生回合开始，session/close 确认且进程组停止已确认。

实际顺序为 runtime/capabilities、session/create、session/setModel、session/subscribe，再做命令字段校验和显式 queue 投递。快照的 runtime.stateRevision 为 1；不带 baseRevision 的合法 sendText queue 返回 `status: failed`、`reasonCode: fault.command.executionFailed`、`message: FOREIGN KEY constraint failed`、`revisionAtDecision: 0`。去掉模型配置的先前一次探针也返回同样失败。私有原生库中的 session、session_entry、session_input 表均为 0 行；这是已观察到的初始化差异，尚未证明是 v4 失败的完整原因。session/subscribe 的快照未提供可验证的 held 队列内容。这只覆盖尚未发原始 session/send 的私有新会话，不能据此判定正在运行的 Worker 不支持 guide。

因此第 1 节的“排队不会自行执行”尚未得到验证，删除/重读也无法验证；baseRevision 可省略仅确认到封包校验层。探针以退出码 2 明确表示门槛未满足。依 Host 的“有不一致就停”要求，未实施实时驱动、共享代码、声明或产品文档修改，未运行实现测试。需要 Host 提供或授权核实：怎样在不调用模型的情况下，通过原生操作将既有 session/create 根会话初始化到 v4 可排队的状态，并读回/删除队列条目；不能直接向原生 SQLite 填数据冒充初始化。若必须先进入真实模型回合，须由开发者另行逐次批准，本轮不执行。
