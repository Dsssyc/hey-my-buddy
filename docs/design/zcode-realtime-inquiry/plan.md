# ZCode Worker 实时问询执行计划

2026-10-31，供 Host 审定；基线 `3dac46de`。本轮只写计划、提交一次，不改实现、不调用模型、不写验收记录；Host 定下计划后，在同一微任务续做。实施顺序服从 [ADR-006 的 2026-10-31 决定](../../decisions/006-zcode-adapter-integration.md)：排在 Windows 测试欠账第二批之后。

依据是两份调研：[本机安装包与无模型实验](../../research/2026-10-31-zcode-dsh-in-turn-input.md)、[公开资料](../../research/2026-10-31-in-turn-input-public-sources.md)，以及 [ADR-023](../../decisions/023-harness-integration-principles.md)、[ADR-030](../../decisions/030-harness-layer-scope.md)。原生行为依据为 ZCode app 随包 CLI 0.17.0；尚未用真实模型回合验证。下面均为拟实施行为，不是验收结论。

## 1. 先补齐协议事实，再实现送达

两份记录没有留下可直接用于编码的完整快照、ack、命令查询与队列条目 schema。先在后续实现轮补一个不调用模型的协议探针 `tests/probes/zcode_realtime_inquiry.py`，只创建私有空会话、订阅/读取状态、查询命令及校验无副作用的错误应答；不得对空闲会话发有效 sendText。把确认过的字段路径和脱敏应答固化到 `tests/python/buddy/harnesses/zcode/fixtures/`，构造的状态组合另作标注。不能把 `v4/commands/query` 擅自当成队列读取方法，也不能用不可达的 `v4/controller/subscribe`。

实施前必须明确三件事：如何在现有订阅/快照面读到根会话的当前 revision、活动回合及 held 队列；sendText 的文本字段、ack/命令查询结果怎样把 `commandId` 关联到原生输入与队列条目；`expectedHeldQueueItemIds` 检查的对象和失败裁决。只用有依据的字段，不给 sendText 加会被静默丢弃的 `expectedTurnId`，不靠问题文本相同或事件相邻来猜条目归属。

“绝不开新回合”另有一项必须核实的前提：根回合结束是否推进 sendText 所比较的 revision，以及 `guide` 回退的条目是否保持 held、不会在撤回前自动执行。资料只证明可以带 `baseRevision`，没有证明它对这两处竞争有原子约束。收到 `delivery: startNow` 再删队列，不能撤销已经开始的回合。先用已记录的协议证据核对，再由第 6 节的原生验证检查；若不能满足，停在 Host 的决定边界，不能把事后对账写成事前保证，也不能自行用 `setAutoDrain` 改用户设置来补洞。

## 2. 一条连接上的送达与状态

在 `src/hey_my_buddy/buddy/harnesses/zcode/inquiry.py` 新建本 harness 的问询驱动，复用 `native_run.py` 已持有的 `NativeConnection`、根会话和 `RootTurnEvidence`。`InquiryBridge` 仍先持久提交问题为 `queued`；原生 RPC 只由现有 pump 所在线程发送，接到 Host 请求的线程不碰原生连接。通过现有 `after_pump` 接入轮询，保留 attention 反馈；空读也轮询，不等模型自己调用工具。RPC 内部也 pump，故驱动只允许一次轮询在途，防的是等待 ack 时重入并重复发送。

仅当初始 `session/send` 已接受，且原有 `inputId` 对应的根 `turn.started` 已确定 native turn ID，才开放问询；`session/send` 仍只承载原始任务。每次发送前检查这一个根回合的完成/finish 证据，取根会话最新的权威 revision，调用同一连接的 `v4/command`：`{commandId, clientId, sessionId, type: "sendText", payload, issuedAt, baseRevision}`，payload 的送法固定为 `requestedDelivery: "guide"`，正文携带原问题、精确 `inquiryId` 和本次挂载的回答工具名。`baseRevision` 不用黑板 revision、事件 `seq` 或猜出的数字；拿不到原生 revision 就报告送不到。held 队列预期值取同一快照的实际条目集合，只有第 1 节确认其原生语义后才按该语义传 `expectedHeldQueueItemIds`，它不能冒充期望回合绑定。

`clientId` 固定到本 attempt；`commandId` 由 attempt、问题 ID、问题摘要、操作及操作序号确定。一次发送的正文、`issuedAt`、revision 与 ID 一起冻结。不确定应答只按原 ID 查 `v4/commands/query`，或原封重放；不换 ID 再投一份问题。Host 对相同问题的重发沿用桥已有的去重与冲突规则，已经发送、送达、回答或送不到的条目不再次发送；不新增持久命令账本。同 ID 改正文仍在原有入口拒绝。

裁决先决定命令有没有效果，再看原生送法与事件；`accepted`、`noop`、`duplicate` 都不是黑板的 `delivered`：

| `status` | 黑板处理 |
| --- | --- |
| `accepted` | 保存命令与输入的确切关联，按下表处理 `delivery`。 |
| `noop` | 有已确认的同一输入时恢复它的状态；查询确认没有产生输入/队列条目时记 `unavailable`，原因 `zcode-guide-no-effect`。若无法确定是否有效，按下述证据不足处理，不编造成功。 |
| `duplicate` | 查回原命令的有效裁决与关联，恢复原状态；不再新发，不新增日志状态。缺原裁决时不得把 duplicate 当送达。 |
| `stale` | 本次不能证明送入，记 `unavailable`，原因 `zcode-guide-stale`；不自动换 revision 重发 sendText，防止一次过时请求跨过根回合结束。 |
| `rejected` | 记 `unavailable`，保存有界 `reasonCode`，如会话不存在、不能 steer 或队列预期冲突；不改送法重试。 |

| 有效裁决的 `delivery` | 黑板处理 |
| --- | --- |
| `guide` | 仍为 `queued`，等待匹配的原生事件；确认 drain 到本根回合后才为 `delivered`。 |
| `queue` | 不是本回合送达；按第 3 节撤回这一条，记 `unavailable`，原因 `zcode-guide-queued`，并保留原生回退原因（如 `guide.noToolBoundary`）。 |
| `startNow` | 记 `unavailable`，原因 `zcode-guide-start-now`；撤回仍 held 的本条输入，绝不发送启动/排空命令。若已出现另一 `turn.started`，属于违背约定的真实失败，停止本次原生执行、保留证据交 Host，不能宣称“没有开新回合”。 |
| 缺失或未知值 | 查原命令及队列，能恢复上述事实才继续；否则记 `unavailable`，原因 `zcode-guide-unconfirmed`。可能留下未归属的输入时结束该原生执行并报告清理未确认，不猜 ID 删除。 |

只有这三种已知 `delivery`；缺失不是第四种送法。未知 status 也按证据不足处理。以上失败都不把整份 ack/原生错误文字抄进公开结果。原生形状只在边界解析一次，内部使用已解析的事实；桥现有 fsync/大小限制继续生效，日志写失败不能发布送达。

在 `zcode/protocol.py` 把两个事件加入现有白名单，在现有根事件顺序校验之后交给问询驱动：`turn.steerQueued` 只证明进入待注入列表，`turn.steerDrained` 才证明被当前回合消费。两者必须关联同一命令对应的 `pendingInputId`、相同 `targetTurnId`，且 session、事件回合都属于从原始 `inputId` 得到的根回合；不能用子代理、别的会话、别的回合或带转发来源的事件补齐。`queued → delivered` 只发生一次，写现有 `nativeCorrelation`，保留 command、session、目标 turn、输入 ID 与两个事件的序号。

事件可能在 ack 之前到达，故驱动按原生输入 ID 暂存本次问题的候选事件，再用裁决对账，容量沿用本次问询数量上限；不能等待 ack 而堵住 pump。回答仍须原生根 `hey_my_buddy_answer_inquiry` 的成功、未截断结果和签名回执，复用现有身份与问题摘要校验。根回答早于 ack 时只暂存已验证回执，等送达对账完成才按 `queued → delivered → answered` 提交；没有匹配 drain 的回答不能跳过送达证明。终态后的迟到事件/答案不复活条目。

## 3. 只撤回本条输入，重读确认删除

问询驱动只保存本次 sendText 的原生输入/队列 ID 关联。`queue`、`startNow`、根结束但尚未 drain 等情况，先停止该条的发送资格，再从当前队列快照定位该 ID，用最新 revision 发 `deleteQueueItem`；绝不清空队列、删别人的输入，或调用 `sendQueuedNow`、`setAutoDrain`、新的 `session/send`。

删除后必读根会话当前队列，结合删除裁决和输入事件确认确切 ID 已撤回。`deleteQueueItem` 即使回 accepted/noop，条目仍在也算未删；这是公开调研记载的过期 `baseRevision` 假成功。读新 revision 后，以新删除操作 ID 重试同一条目（不重发问题）；同一次删除的不确定应答仍用原 ID。重读证明条目已不存在则无需再删；若它已 drain，靠匹配事件确认去了哪个回合，不能把“不在队列”解释成“已撤回”。

队列持续变化时不加无限紧循环：一次重读、删除、再读仍未消失，就结束本次撤回尝试，保留 `cleanup-unconfirmed` 事实，停止这次拥有的原生执行并向 Host 报清理失败；不发布这个 session 可续接的成功凭据。取消/断连导致不能读回时也如此，使用现有停止与真实停机证据，不为清理重新连接或开回合。这里的再读防已知假成功，停止防未撤回输入在以后执行，不添加没有实际场景的多层重试。

为 `InquiryBridge` 增加逐条提交 `unavailable` 的操作，沿用现有 journal 字段，将有界失败代码、原生 status/delivery/reasonCode 和撤回结果放进现有 `nativeCorrelation`，遵守现有 delivery 的 1024 字节上限。其消费循环也要识别本条 unavailable 的重放并返回已记录原因，不能走现在的 `unknown-bridge-state` 分支。`protocol/inquiry_journal.py` 当前会把任意 unavailable 归为“回合结束”；需在这里保留本条原生失败的原因，投影为现有 `bridge-refused {errorCode}`，详细原生事实留在 delivery 中。普通关闭仍使用原有 queued/delivered-at-attempt-end 原因；不靠 CLI 或 console 改写原因。预计复用现有日志版本、状态、JSON 关联字段与 SQLite 列，不改封存日志或已运行的升级步；若最终需要改变事实格式或存储形式，由 Host 在实现前确定契约版本与升级范围。

## 4. 竞争的处理与被取代的路径

发送前已结束：不开命令，问题 `unavailable`。发送到 ack 之间结束：继续处理已经在途的裁决及原生事件，能证明此前 drain 就保留 delivered 事实，再将未答状态结算为 unavailable；没 drain 则撤回 held 项。最终答复段：以原生 `queue`/回退原因裁决并撤回，不用“还没有 turn.completed”推断还能 steer。原生根 finish 工具已 scheduled 时暂停注入；有效 finish 回执关闭问询窗口，拒绝后续提问；如果 finish 被原有参数/attention 规则拒绝，恢复尚在运行的窗口。这防的是成功结项后再要求根调用回答工具，与现有“成功 finish 后不再调用工具”相冲突。

停止、取消、失败与正常结算都先关问询准入，再处理已有命令/撤回及已验证答案，最后调用桥的关闭。现有 `blackboard/store/store.py::_settle_attempt_inquiries` 已在同一事务先导入 journal 再结算，继续沿用；answered 不降级，未答 queued/delivered 按现有规矩 unavailable，并保留曾经送达的证据。不得把 native turn ID 与黑板 turn ID 混用。上述每一种竞争都进入第 6 节的夹具，不能只测空闲 RPC。

删除 ZCode 的 checkpoint 挂载、根 checkpoint 回执追踪与“无原生注入”旧注释/断言；`RootTurnEvidence` 保留根 start、回答、finish、完成与关闭证明。`native_run.py::prepare_session_service` 对问询工具按名字绑定，去掉当前把 tuple 第一项当 checkpoint、第二项当 answer 的假设，避免仅剩回答工具时错绑。

`buddy/roles/run_execution.py` 从既有送达能力声明取得模式，交给 `worker_services.py` 的工具与提示文字组装；该实际使用的模式随 DSH/ZCode 挂载进入私有 MCP 配置。`session_mcp.py` 沿用已有 `toolNames` 过滤并使 tools/list 与 tools/call 一致。ZCode 只挂 answer 和 finish，不保留隐形 checkpoint 别名；其 finish 不再以未答问询拒绝 completed/waiting，晚到问题在关闭时送不到。原有参数错误与 attention 拦截照常执行。

DSH 保留 checkpoint、签名收题回执、未答问询的 completed/waiting 拦截及相应提示。`dsh/native_run.py` 只传递同一已有能力对应的配置，不改 ACP 行为。`worker_services.py` 的 checkpoint 实现与公共回执原语仍有 DSH 使用者；`in_turn_inquiry.py` 的 Claude 工具不在本次删除范围。共享 `roles/texts/worker.txt` 去掉对所有 harness 都要求 checkpoint 的笼统句子，通道说明由本轮实际能力和挂载工具补入；ZCode 写实时送入及精确 ID 回答，DSH 写自然节点调用检查点，不加定时器。旧 ZCode checkpoint 专属测试由实时用例取代，有效的公共 journal、签名、身份与 DSH 检查点用例移到已有公共/角色测试，不因文件旧名而整组丢弃。

## 5. 文件与文档落点

- 原生送达：新增 `src/hey_my_buddy/buddy/harnesses/zcode/inquiry.py`；修改同目录 `native_run.py`、`protocol.py`；问询输入同步进入 `session_content.py` 的现有会话记录投影，不能因改通道丢掉 Host 输入。
- 共享状态与工具：修改 `buddy/harnesses/inquiry_bridge.py`、`protocol/inquiry_journal.py`、`buddy/roles/run_execution.py`、`worker_services.py`、`session_mcp.py`、`texts/worker.txt`，以及 DSH 挂载处的必要传参。代码根均为 `src/hey_my_buddy/`。
- 声明：`buddy/harnesses/live.py::EXISTING_CAPABILITIES["zcode"]` 改为 realtime，构造桥时明确 `delivery_mode="realtime", checkpoint_tool=None`；声明、实际挂载、live 快照与结果报告同批改变。DSH 仍为 cooperative-checkpoint。
- 当前说明：先改 `docs/reference/harness-integration.md` 的 Inquiry delivery，再改 `cli.md`、`usage.md`、`workers.md`、`architecture.md` 中的 ZCode 问询说明；写明 guide、两个事件、最终答复段送不到及没有 expected-turn 绑定的边界，命名 CLI 0.17.0 与验证状态。旧 checkpoint 验收记录只能作历史依据，不能继续作为实时路径证明。`docs/README.md` 与 `skills/hey-my-buddy/` 的入口按实际引用更新；`harnesses.md`、`workflow.md` 若只讲提供方或结果工具，不为本次重复写一套问询说明。

## 6. 测试、执行次序与验证边界

先核对第 1 节并固定夹具，再实现原生驱动和共享状态；随后移除 ZCode checkpoint 路径、改声明及文档，最后验证。最小夹具必须先能复现“根不调用 checkpoint，Host 的问题始终 queued”，改后同一场景经 guide/drain/根回答成为 answered；不能仅断言发出过命令。

| 文件（测试根为 `tests/python/`） | 必须覆盖的行为 |
| --- | --- |
| 新 `buddy/harnesses/zcode/test_zcode_realtime_inquiry.py`；改 `zcode/fixtures/mock_zcode.py` | 替换 `test_zcode_checkpoint.py` 的 ZCode 专属流程。五种 status 各自覆盖；accepted/noop/duplicate 的有效结果分别覆盖 guide/queue/startNow；缺字段、未知值、查询原裁决、同 ID 重发、同 ID 异文、两个并发问题不串线；ack 前后事件及根回答的排序。mock 断言除原始任务外没有 session/send、sendQueuedNow 或 setAutoDrain。 |
| 同上；`buddy/harnesses/zcode/test_zcode_protocol.py` | 同一 pendingInput 的 queued/drained；只 queued、只 drained、错 ID、错 target turn、子代理/转发、其他 session、事件顺序错误；finish scheduled/拒绝/成功窗口；发送前结束、发送后 ack 前结束、最终答复段、取消与断连。队列回退后删本条、旧 revision 假成功、重读重试、新删除 ID、条目已 drain、另一条队列项不受影响、撤回仍未确认。 |
| `buddy/harnesses/zcode/test_zcode_inquiry.py`、`test_zcode_tool_refusals.py`、`test_zcode_turn_io.py`、`test_zcode_tool_evidence.py`、`test_zcode_tool_projection_order.py`、`test_session_content.py`、`test_native_run.py` | 调整实际工具集合与旧断言；保留签名伪造、子代理回答、原生错误包装、attention、结构化 finish 与任务工具事实验证；Host 问题仍出现在会话记录中。 |
| `buddy/roles/test_worker_services.py`、`test_role_prompts.py`、`test_run_execution.py`；`buddy/harnesses/dsh/test_native_run.py`、`test_dsh_role_wiring.py` | ZCode 无 checkpoint、未答不再阻塞 finish、answer 名字正确；DSH 检查点仍收题，未答仍拦 completed/waiting，assistance/attention 可交回；通用角色文字与工具描述跟模式一致。 |
| `buddy/harnesses/test_live_channel.py`、`test_harness_conformance.py`、`test_inquiry_owner.py`；`blackboard/tasks/test_inquiry.py`、`blackboard/store/test_inquiry_settlement.py`、`protocol/test_inquiry_transport.py` | 声明/报告同为 realtime；逐条 unavailable 原因经 live、journal、服务导入与结算仍可见；回答先于服务观察不丢失；answered 不降级；queued/delivered 未答结算原因及 journal 写失败照旧。 |
| `buddy/harnesses/test_in_turn_inquiry.py`、`buddy/harnesses/codex/test_in_turn_inquiry.py`、ZCode/DSH 的 `test_no_tool_*.py` | 共享改动不改变 Codex/Claude 的实时通道、不把 Worker 的问询驱动装到 Router 或无工具调用。 |

实现轮用仓库检查入口运行受影响路径：`uv run --frozen python -m hey_my_buddy.cli.checks <上述测试文件或目录>`，不直接起测试进程；清掉继承的 `HMB_STATE_DIR`、`HMB_RUNTIME_ROOT`、`HMB_RUNTIME`、`HMB_RUNTIME_IDENTITY`、`HMB_AGENT_CREDENTIAL_FILE`、`HMB_AGENT_CREDENTIAL_FD`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`。共享角色与 journal 改动合入后由 Host 安排一次完整 `uv run --frozen python -m hey_my_buddy.cli.checks`，只读退出码和失败摘要，避免与整合分支的全套重叠。此轮仅检查计划 diff、引用与写入范围，不跑实现测试、不改版本、不构建或安装。

真实原生验证计划 **3 次实验，每次另经开发者批准**，用上述 probe 的显式付费模式和私有会话，保留脱敏协议记录与实际停机证据；次数指根回合实验，不是模型 API 请求数，一次回合可能多次调用模型。本轮一次也不做，失败后的重跑也须另获批准：

1. 正在工具边界运行的根回合：不调用 checkpoint，送一个问题并重放相同 Host ID，实测 guide 回执、queued/drained 的身份关联、只收到一份问题、根工具回答及正常结束，核对只有原始任务的一个根回合。
2. 模型已进入最终答复段：送题，捕获实际回退值/原因、队列条目归属与删除后的重读，确认送不到、没有第二回合；回放其中旧 revision 的删除应答用于假成功回归。若时机没命中，记录未覆盖，不把推断写成通过。
3. 根结束与请求在途竞争：在终结前后安排送达并延迟客户端读取 ack，核对 revision、原生命令裁决、两个事件和结算；再对已结束的窗口提问应没有新命令，整段没有第二个 root start。若发现 startNow 已执行或自动 drain，判失败并交 Host，不能用合成夹具代替真实边界结论。

## 7. Host 审定点与范围

建议 Host 明确三项后再续做：接受“stale 不自动重发 sendText”的失败语义；确认实时 ZCode 与 Codex 对齐，移除未答问询对 completed/waiting 的 finish 拦截；确定第 1 节的无新回合前提怎样取证，若原生机制做不到，由 Host 收敛约定后再实施。原生快照、ack、输入关联的精确字段仍需上述探针补齐；公开社区的旧版说明不能替代 CLI 0.17.0 的实际形状。

不另建 ZCode 执行通道，不改用户配置，不为未使用的模式加开关，不恢复协作检查点作兼容回退，不加后台自动重问或时间上限。声明与新实现只在同一批交付；源码检查、真实原生验证、安装验证分别报告，不能用计划或夹具通过代替已安装可用。本轮交付仅此计划文件与一次英文提交。
