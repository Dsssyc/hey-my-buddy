# ZCode Worker 实时问询执行计划

2026-10-31，基线 `3dac46de`；Host 审定后收窄。依据 [ADR-006 的当日决定](../../decisions/006-zcode-adapter-integration.md)、[本机调研](../../research/2026-10-31-zcode-dsh-in-turn-input.md)、[公开调研](../../research/2026-10-31-in-turn-input-public-sources.md) 与 [ADR-023](../../decisions/023-harness-integration-principles.md)。原生协议依据是 ZCode app 随包 CLI 0.17.0；Host 允许无模型探针，前提成立才实现。续查发现与已审定回执和自动排空前提冲突的包内行为，当前停在 Host 审定边界；没有调用真实模型或付费服务，不写验收记录。

## 1. 先核实原生前提

`tests/probes/zcode_realtime_inquiry.py` 使用私有 HOME、配置、存储和工作目录，阻断外网，不连日常黑板。默认探针现只用 v4 createSession（permissionScope: session，无 firstInput）初始化父行，读 v4 会话快照、校验字段并删除已知不存在的条目。空闲 queue 试投已从默认探针删除，因为一次实测仍得到 startNow 并启动原生回合；假提供方和禁止出网使它没有联系真实模型，且已确认停止。固定应答在 `tests/python/buddy/harnesses/zcode/fixtures/`，区分录制与构造应答。

续查依据见[父行、队列与自动执行](../../research/2026-10-31-zcode-queue-initialization.md)。父行可不调用模型建立；正常 Worker 的 session/send 在规范 turn.started 之前已持久化。队列读取用 v4/conversation/subscribe 的 conversation/<sessionId> 小快照；sendText 不要求 CAS，deleteQueueItem 则要求快照的 revision 与 logEpoch，分别作为 baseRevision、baseLogEpoch。另一前提“没有显式发送就不会自动执行”不能继续假定：默认 queue.autoDrain 为 true，宿主有自动 sendQueuedNow 路径。因此暂停实现，先由 Host 收敛该边界；不擅自加 setAutoDrain 或其他开关。没有 expected-turn 绑定的事实及现有第二根 turn.started 检查仍保留。

## 2. 送达和状态

在 `src/hey_my_buddy/buddy/harnesses/zcode/inquiry.py` 新建驱动，参照 Claude `_send_pending_inquiries` 和 Codex `_NativeInquiry`，复用现有 app-server、`NativeConnection` 与 `RootTurnEvidence`。桥先提交 queued，pump 的 `after_pump` 在空读和 RPC 等待期间也轮询；保留 attention 反馈并防止 RPC 等待重入。仅原生 pump 线程发命令。

根 turn 已绑定、尚未结束且 finish 尚未 scheduled 时，向当前根 session 发 `v4/command`，`type: sendText`、`requestedDelivery: guide`，正文包含精确 inquiryId、问题与挂载的回答工具名。commandId 用随机值；驱动在内存记录已尝试的问题，每题只发一次，同 ID 的 Host 重发沿用桥现有去重，同 ID 异文仍冲突。`NativeConnection.call` 没有单次超时，不做原封重放、命令查询、noop/duplicate 恢复或换 revision 重投。RPC 内事件继续处理。

| 原生结果 | 黑板状态 |
| --- | --- |
| accepted，result.delivery 为 guide | 类型枚举允许该值，但本机尚未观测；仍须匹配本根回合及本题原生输入的 drained，不能凭回执直接写 delivered。 |
| accepted，result.delivery 为 queue | 包内成功 steer 也回 queue，不能据此直接撤回。待 Host 根据原生事件及实际 held 条目审定新规则，当前不实施旧裁决。 |
| accepted，result.delivery 为 startNow | 实测空闲 queue 也走到这一值；按失败边界处理，不能宣称没有开回合，不能靠事后删队列撤销已开始的执行。 |
| noop、duplicate、stale、rejected、failed | 不恢复、不重发，不凭裁决写 delivered；保持 queued，结束时 unavailable。 |
| JSON-RPC 错误 | 仅在问询驱动边界捕获 `NativeRpcError`，作为没送进处理，保持 queued，不拖垮执行。 |
| 缺必要字段或无法关联 | 不声称送达，保持 queued；可能残留队列项时禁止写续接绑定。未知额外字段仍按现有规则记事实。 |

`turn.steerQueued` 只作佐证，不是送达门槛；`turn.steerDrained` 须匹配原始 inputId 所绑定的根 session、turn 和本题 pendingInputId，不能拿子代理、转发、别的回合或会话的事件凑证据。把必要关联写到现有 nativeCorrelation，不改事实格式。事件可能先于 ack，驱动在本次问询数量上限内保留关联事实，ack 到达后即可对账。

回答沿用根工具成功结果、签名、attempt 与问题摘要验证；精确的根回答本身证明模型收到问题，允许现有 `record_answer` 从 queued 直接变 answered，不暂存回答等 ack。终态后的迟到答案按现有规则忽略。原生 sendText 输入经新增的 `ZcodeSessionProjector.record_input` 进入会话记录。

## 3. 撤回、finish 与结算

只删除本次命令关联的实际 held 队列条目：取 v4 快照 revision 和 logEpoch，分别带作 baseRevision 和 baseLogEpoch，deleteQueueItem 一次，随后重读队列一次，不能把 ack 的 accepted/noop 当成删除成功。重读发现还在或无法确认时，不停执行、不清空结果，仅不写该会话的续接绑定；不得清空队列或删除其他输入。对已续接会话也要使旧绑定不能继续被选用。不要调用 sendQueuedNow、setAutoDrain 或新 session/send，也不新增重试机制。

发送前根已结束不发命令；命令在途时结束，继续对账已来的事件和裁决，未送达保持 queued。最终答复段也不能仅凭 queue 回执判回退；实际 held 条目的认定及默认自动排空须先经 Host 收敛。finish scheduled 之后的准入门看 `RootTurnEvidence`，不依赖尚未关闭的 bridge.active。`RootTurnEvidence` 将 finish 后 scheduled 的回答工具记作迟到事实，不抛 duplicate-finish、不覆盖已收结果；其他非法根工具的现有检查保留。正常关闭、停止与服务结算沿用现有顺序，未答问题仍为 inquiry-queued-at-attempt-end 或 inquiry-delivered-at-attempt-end，answered 不降级。

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

## 首轮探针结果

原先的空 session/create 会话经 setModel 后，sendText queue 返回 failed / FOREIGN KEY constraint failed；父行与输入行均为 0。原始固定应答保留在 `tests/python/buddy/harnesses/zcode/fixtures/inquiry-app-server-0.17.0.json`，作为这次初始化差异的记录。该问题已由本轮找到的 v4 持久化入口解开，不能继续用它解释活动 Worker 的能力；当前阻塞见下节。

## 续查结果与当前交回边界

本轮已补齐父行初始化和队列读取，固定应答为 `tests/python/buddy/harnesses/zcode/fixtures/inquiry-queue-initialization-0.17.0.json`。当前安全探针的原始输出在 `tmp/zcode-realtime-inquiry/ed37a6c6cfe240a2b0d167aeb09a97a5/`：父行 1、输入行 0、原生回合数 0，关闭与进程组停止确认，退出码 2 表示活动回合/自动排空门槛尚未通过。空闲 queue 的反例单独记录了 1 个原生回合；没有联系真实模型。

需要 Host 审定的已不再是空会话外键：包内成功 guide 的回执同样是 queue，旧的“queue 就撤回”会误判；默认开启的自动 sendQueuedNow 路径又不满足“不显式发送就不执行”的前提。尚未动态验证成功模型回合结束后的自动排空时机，也未在真实活动模型回合验证 guide 回执/事件。故未改产品实现、能力声明或现行产品文档，未运行实现测试。不能用新的停止机制或擅自设置 autoDrain 来绕过这两项事实。

## 待开发者批准的一次真实实验

用 ZCode app 随包 CLI 0.17.0，模型固定 GLM-5.3-Flash、reasoning low；提供方由 Host 从开发者已有配置中锁定并先核对原生目录，不临时配置凭据或替换模型。保持用户 harness 配置原样，沿现有 session/create + session/send 起一个私有原始回合。本轮不执行。

1. 第一次主模型请求只让模型调用一个无副作用的 Bash 门控命令，等待外部标记，最长 15 秒。在对应根 turn.started 后只读核对父行，订阅 v4 快照；在工具等待期间送入 guide A，记录实际 ack、steerQueued、steerDrained 与根回合归属。
2. 同一等待窗口送入 queue B，读回真实条目并短暂观察；带最新 revision/logEpoch 删除一次、重读确认。另留一条只要求输出固定标记的 queue C，用来观察原回合结束后是否会自行启动。所有条目用各自的 commandId/sourceCommandId 精确关联。
3. 释放门控命令；第二次主模型请求读取 guide A 的标记并作简短最终答复。记录原回合结束、C 是否仍 held 或出现新的根 turn.started；若出现第二根，立即结束这次探针并收集停机证据，不继续其他验证。这里只验证协议前提，结构化回答工具与实现验收仍在原计划的后续实验中。

预计原回合 2 次主模型请求；若 C 自动执行，可能额外 1 次，即为批准一次实验时需要包含的风险，总计预计 2–3 次主请求。标题等原生辅助调用按开发者自己的配置另计，批准前由 Host 核对；不为减少费用关闭原生功能。若模型没有按门控流程行动、没有命中目标时机或删除未确认，按实际未覆盖交回，重跑须另批。这次实验要确定：标准 Worker 路径的父行、成功 guide 是否回 queue、drained 的根身份、非空队列读取/删除的 CAS、以及原回合结束后的自动执行行为；不把源码推导或空队列读回当成这些边界已通过。
