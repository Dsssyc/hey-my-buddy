# ZCode Worker 实时问询执行计划

2026-10-31，基线 `3dac46de`；本版遵从 Host 的收窄决定与开发者选定的 A。[ADR-006](../../decisions/006-zcode-adapter-integration.md) 的实时问询走同一个 app-server 的 v4 sendText/guide；开发者同意以原生 setAutoDrain(false) 约束黑板创建的私有会话，理由只限回合契约。Host 负责把这条决定记入 ADR-006 与 ADR-030。本任务不改用户自己的会话、配置和默认值，不改跨进程事实格式或契约版本。

依据为[本机协议调研](../../research/2026-10-31-zcode-dsh-in-turn-input.md)、[公开资料](../../research/2026-10-31-in-turn-input-public-sources.md)、[父行与队列续查](../../research/2026-10-31-zcode-queue-initialization.md)及 [ADR-023](../../decisions/023-harness-integration-principles.md)。原生版本为 ZCode app 随包 CLI 0.17.0。前轮记录不回写；本轮实验另写 docs/research 续查，不在 docs/acceptance 写记录。

## 1. 本轮先验证一次真实回合

开发者只批准一次 GLM-5.3-Flash / low 的原始根回合，不批准失败重跑。`tests/probes/zcode_realtime_inquiry_live.py` 先作不发模型输入的预检，再用一次性记录限制获批的 session/send；模型提供方由原生目录选择该型号，按产品适配器的 native_environment、提供方文件路径和运行时偏好交给原生进程，不读取、复制、打印或保存提供方密钥。会话、MCP、日志和协议证据位于检出私有 tmp；协议记录只保留已选择的非秘密字段。

先创建会话、读取 v4 快照，以快照 revision/logEpoch 作 baseRevision/baseLogEpoch，执行 setAutoDrain(false) 并读回 false，再发唯一原始任务。模型先调用门控工具；工具等待时向根回合送 guide A，并排入普通 queue C。释放门控后，模型须用挂载的 hey_my_buddy_answer_inquiry 回答 A 并结束。核对 A 的 steerQueued/steerDrained 的 targetTurnId、pendingInputId(s) 与本根回合一致；回合结束后 C 仍在 held 队列且未启动第二根，用 CAS 删除 C，再读确认消失。预计 3 次主模型请求（门控、回答工具、最终答复），原生辅助调用遵从用户配置；审批只覆盖这一次实验，失败后不再发原始输入。

三项全部成立才继续实现：autoDrain=false；guide A 在当前根回合 drain 且模型通过回答工具答复；C 在根结束后不自动执行且 CAS 删除/重读成立。任一不成立就保存脱敏原样证据、确认停止、写续查并交回；不重跑。`tests/probes/zcode_realtime_inquiry.py` 保留无模型的原生参数验证用途。

## 2. 一条原生连接上的驱动

新增 `src/hey_my_buddy/buddy/harnesses/zcode/inquiry.py`，参照 Claude `_send_pending_inquiries` 与 Codex `_NativeInquiry`，复用已有 NativeConnection、RootTurnEvidence 和 InquiryBridge；不另建执行通道。桥仍先持久提交 queued，原生 pump 线程在 after_pump 轮询，包含空读和 RPC 等待期间；保留 attention 反馈并防重入。驱动在内存记已尝试的 inquiryId，每题只发送一次，commandId 用随机值；Host 重发沿用桥现有去重，同 ID 异文仍冲突。没有命令查询、原封重放、确定性 ID、noop/duplicate 恢复或换 revision 重投。

在原始 inputId 对应的根 turn.started 已绑定、根未结束且 finish 未 scheduled 时，发 v4/command 的 sendText，requestedDelivery 固定 guide，正文包含精确 inquiryId、问题和本次挂载的回答工具名。准入门看 RootTurnEvidence，不依赖晚关闭的 bridge.active。sendText 不带不需要的 CAS 或 expectedHeldQueueItemIds；不要添加会被丢弃的 expectedTurnId。

私有会话创建和原生续接均在发原始任务前用 v4 快照 CAS 设 autoDrain=false 并确认。只作用于此运行模块已经拥有的私有 session；不改变用户设置。setAutoDrain 被拒、v4 快照无效或该 RPC 被拒时，把原生错误码记在现有 inquiry.error，本次不再送题；问题保持 queued，随原有回合结束结算为 unavailable，Worker 工作继续。取消、总时限和根回合协议错误仍按原规则处理。读取用 v4/conversation/subscribe 的 conversation/<sessionId>；订阅 ack 与 v4/conversation/frame 对账，revision/logEpoch 从同一快照取。订阅及必要的 wire 解码仍在原生连接上，不把 v4/commands/query 当队列读取。

## 3. 送达裁决、撤回与结算

| 原生事实 | 黑板处理 |
| --- | --- |
| accepted 的 guide、queue、startNow 回执 | 都不单独证明送达；成功 guide 也可能回 queue。按下述原生事件或精确根回答对账，不凭 queue 回执撤回。 |
| noop、duplicate、stale、rejected、failed | 不恢复或重发，不凭裁决写 delivered；没有送达/回答证据时保持 queued，结束时沿用现有 unavailable 结算。 |
| v4/command 的 JSON-RPC 错误 | 在问询驱动边界捕获 NativeRpcError，按没送进处理，不拖垮执行；问题保持 queued。 |
| turn.steerQueued | 只作佐证，记录本题原生 inputId、pendingInputId 与根 targetTurnId 的关联，不写 delivered。 |
| turn.steerDrained | 根 session/turn/targetTurnId 及本题 pendingInputId(s) 对得上才调用现有 deliver_native；不拿子代理、转发或别的会话/回合补证据。 |
| 精确根回答工具成功且签名验证通过 | 沿用现有 record_answer；答案本身证明模型收题，允许 queued 直接到 answered，不暂存回执等待 ack。 |

事件可能早于 ack，sent/command 关联在写命令前建立，事件照常处理；不等待 ack 的 delivery 才承认 drained。nativeCorrelation 只放当前格式已有的有界身份事实，不新增 journal 状态、原因码或跨进程格式。原始输入仍只用原来的 session/send，问询输入经新增 ZcodeSessionProjector.record_input 进入会话记录。

根结束时，读取 held 队列，按 sourceCommandId 只找本驱动发过且仍在队列里的条目；CAS 删除一次，再读一次确认消失。删除要求 baseRevision 和 baseLogEpoch；accepted/noop 不等于已删。仍在或读回无法确认时，不停止执行、不丢结果，只禁用此 session 的续接绑定；原先已有的绑定也不能继续被选用。不删别人的输入，不自动重试，不发送 sendQueuedNow，不重新打开回合。

发送前结束则不发；在途结束则继续处理已经来的原生事实，未答 queued/delivered 由现有关闭/服务结算记 unavailable，answered 不降级。第二根 turn.started 沿用现有 wrong-native-turn 失败，不另造停止规则。第一次 finish scheduled 后不再发新问题，即使参数被拒需要重试也不重新开放送题；RootTurnEvidence 将其后到来的回答工具调用记作迟到事实，保留 finish 结果、不抛 duplicate-finish；其他工具、签名及真实失败检查仍按原有边界执行。

## 4. 清理旧路径与共享层

ZCode 只挂 answer 和 finish，桥配置写实际 toolNames，利用 session_mcp.py 的现有过滤；按名字绑定回答工具，删 tuple 位置假设、checkpoint 挂载/回执追踪、旧注释和专属断言，不保留别名或协作回退。ZCode 未答问询不再拦 completed/waiting，参数错误与 attention 拦截保留。DSH 仍挂 checkpoint、签名收题并拦未答 completed/waiting。

共享层只改 buddy/roles/run_execution.py、worker_services.py、texts/worker.txt 和 buddy/harnesses/live.py 的实际送达声明；模式从现有能力取得，工具文字跟实际挂载一致。inquiry_bridge.py 只修 snapshot 的 immediateDelivery 常量；其状态处理、protocol/inquiry_journal.py、dsh/native_run.py 不改。无模型 tools/list 夹具已证实 MCP 会按默认模式重建 finish 说明，因此 session_mcp.py 只从现有 toolNames 取得 checkpoint 模式并传给工具生成器，让 DSH 保留未答拒绝说明而 ZCode 不带；不新增配置字段或事实格式。桥构造明确 realtime、checkpoint_tool=None，live.py 中只有 zcode 改 realtime。

原生实现其余落点为 zcode/native_run.py、protocol.py、session_content.py。被取代的 checkpoint 测试删去或换成实时流程；有效的公共 journal、签名、身份和 DSH 用例保留到其现有公共/角色测试位置。

## 5. 文档与验证

先改 docs/reference/harness-integration.md 的 Inquiry delivery，再改 cli.md、usage.md、workers.md、architecture.md 的 ZCode 问询说明；写明私有会话关 autoDrain、drained 对账、最终答复段可送不到、残留队列禁续接及实际验证边界。入口 docs/README.md 与共享 skill 按实际引用更新。原 checkpoint 验收只作历史，不能证明新路径。

检查覆盖：新增 zcode/test_zcode_realtime_inquiry.py 与修改 fixtures/mock_zcode.py，测试真实录制或明确构造的应答；五种原裁决及 failed、三种 delivery、RPC 错误、准备通道失败不送题且完成工作、重复题不重发、事件早于 ack、仅 drained 证明、错根/turn/input/子代理、finish 前后竞争、迟到回答、取消、队列 CAS 假成功、删除失败保结果并禁续接。同步调整 zcode/test_zcode_protocol.py、test_native_run.py、test_zcode_inquiry.py、test_zcode_tool_refusals.py、test_zcode_turn_io.py、test_zcode_tool_evidence.py、test_zcode_tool_projection_order.py、test_session_content.py。

共享回归为 buddy/roles/test_worker_services.py、test_role_prompts.py、test_run_execution.py，buddy/harnesses/test_live_channel.py、test_harness_conformance.py、test_inquiry_owner.py，DSH 的 test_native_run.py、test_dsh_role_wiring.py，Codex/Claude 的实时问询测试，以及 blackboard/tasks/test_inquiry.py、blackboard/store/test_inquiry_settlement.py、protocol/test_inquiry_transport.py。代码和测试路径分别以 src/hey_my_buddy/ 与 tests/python/ 为根。

分步通过仓库检查运行器 `uv run --frozen python -m hey_my_buddy.cli.checks --jobs 1 <受影响路径>` 验证，清掉继承的 HMB 状态/运行时/凭据与虚拟环境变量；不手工启动测试进程，不与 Host 全套重叠。无前端布局改动，不额外跑浏览器。不改版本或安装日常服务，不碰日常黑板与状态，不改分支、标签或 stash，不推送；提交信息英文，无 AI 署名。
