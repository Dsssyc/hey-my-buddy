# ZCode 0.17.0 的入队父行、队列读取与自动执行

## 问题

[前两份调研](2026-10-31-zcode-dsh-in-turn-input.md)之后，实时问询的无模型探针在空 session/create 会话上遇到入队外键失败。Host 要求再查一次：父行由什么创建，现有 Worker 回合开始后是否已有父行，怎样读取 held 队列，能否不调用模型验证实时问询的前提。服务于 [ADR-006](../decisions/006-zcode-adapter-integration.md) 的 2026-10-31 决定与[执行计划](../design/zcode-realtime-inquiry/plan.md)。

## 看了什么

2026-10-31 按 Host 明确要求读取本机 ZCode app 随包 CLI 0.17.0 的混淆安装包 `zcode.cjs`，SHA-256 为 `97490d0f06a7a225d54d47817acf4b4859cdb268b59f2490caaeda982bef063a`。只追了会话/输入表、父行持久化、原生 session/send、v4 命令路由、队列投影和自动排空，没通读模型、工具、鉴权实现；没有读上游仓库，没有上网。下文偏移是 UTF-8 解码后的字符位置，符号名仅用于在这一份安装包里复核，不是稳定 API。

运行自己的私有探针 `tests/probes/zcode_realtime_inquiry.py`，所有 HOME、假提供方、SQLite、日志和工作目录均新建于检出的 tmp，禁止出网，不连接日常黑板。用原生操作创建父行，未直接写数据库；SQLite 只读用于核对行数。固定应答在 `tests/python/buddy/harnesses/zcode/fixtures/inquiry-queue-initialization-0.17.0.json`。没有调用真实模型或付费服务；一次显式 queue 请求意外产生了一个原生 turn.started，已如实保留，随后 session/close 和进程组停止都已确认。发现这个行为后，默认探针删除了有效 sendText，只保留无回合的初始化、读队列和删除不存在条目。

## 看到的事实

- 〔读包〕外键是 session_input.session_id 指向 session.id（字符 1570750 附近）。原生运行时的 ensureSessionPersisted 经会话存储 createSession 写父行（13475125 附近）；v4 的 admitInputCommand 只有在会话记录标为 deferred 时才主动调用这道持久化（15032912 附近）。普通空 session/create 本身不等于数据库已有父行。
- 〔读包〕v4 命令 createSession 的原生宿主会先建 deferred 会话；payload 带 permissionScope: session 时，立即调用 ensureSessionPersistedForExternalActivity，再写会话权限与执行状态（15041633 附近）。不需要 firstInput，也不需要调用模型。〔实测〕空私有会话这样建立后，session 表为 1 行，session_input 为 0 行，原生回合启动数为 0；原先的入队外键前置条件已补齐。
- 〔读包〕现有 Worker 的 session/send 进入共享运行时执行路径；执行原始输入时，ensureSessionPersisted 在规范的 TurnStarted 事件写入之前完成（executeTurnCommand，13361750 附近）。因此，RootTurnEvidence 已收到原始 inputId 对应的根 turn.started 时，正常持久化路径上已经有父行；此前“空会话入队失败”的实验不能外推到正在运行的 Worker。这个现有 Worker 路径的结论来自读包，尚未以真实模型 Worker 回合重验。
- 〔读包、实测〕队列读取入口是同一 app-server 的 v4/conversation/subscribe。参数为 topic: conversation/<sessionId>、connectionId 和 clientMode: web-remote-replayable。应答给 ack.subscriptionId，之后 v4/conversation/frame 通知按订阅关联；本次小快照使用 wireVersion 3、kind: complete，队列位于 params.frame.payload.snapshot.queue，revision 与 logEpoch 是该 snapshot 自己的字段。不是 session/subscribe 的旧快照，也不是 v4/commands/query。只验证了空会话的小快照，未验证大快照分片。
- 〔实测〕sendText 可以省略 baseRevision，合法请求已执行到 accepted。deleteQueueItem 缺少 CAS 字段则 rejected / proto.invalidPayload，详情要求 baseRevision 和 baseLogEpoch 同时存在；从 v4 快照取两者后，删除已知不存在的条目得到 noop / queue.itemMissing。这里没有成功删除非空队列条目，也没有检验旧版本号的假成功。
- 〔实测〕已持久化但空闲的会话收到 sendText、requestedDelivery: queue，结果仍为 accepted，result.delivery: startNow，并发出 turn.started。事件里的 intent.requestedDelivery 与 intent.admittedDelivery 都写 queue，不能覆盖外层真实 startNow 回执和开始事件。假提供方与全程禁止出网使它没有联系真实模型。原始输出保留在 `tmp/zcode-realtime-inquiry/a9e3c1712a5b441ba834ca989855bb0d/`；默认探针不再重复这一输入。
- 〔读包〕活动回合的 guide 成功并不一定有 guide 回执：steerTurn 返回 kind: queued（13102397 附近）；上层 startPromptTurnAfterPreflight 保留这一准入类型（14502646 附近）；sendText 将 queued 统一映射为 result.delivery: queue，另一个分支是 startNow（14756472 附近）；handleCommand 原样把该返回值放进 accepted.result（14735818 附近）。所以“回执 queue 就一定未进当前回合”不能作裁决规则。事件里的 delivery、targetTurnId、pendingInputId 仍需对账；这一点尚未在活动真实模型回合实测。
- 〔读包、实测〕v4 快照默认 queue.autoDrain 为 true。〔读包〕shouldAutoDrainV4QueueHead 在该值为真、队首 dispatch 为 queued、会话不忙、目标为空或完成时允许排空（15014354 附近）；autoDrainV4QueueIfReady 会自己执行 sendQueuedNow，忙时用 100 毫秒定时器重看（15029050 附近），afterLegacyStateMutation 也调用它（15041201 附近）。这是一条宿主自动执行路径，不需要我们的客户端显式发 sendQueuedNow。它在正常模型回合结束后实际触发的时机尚未动态验证；现有证据已经不足以承诺“回退队列不会自动执行”。

## 取了什么，没取什么

取父行初始化入口、规范根开始前的持久化顺序、v4 会话快照路径和删除 CAS 要求，写入执行计划；这些解开了前一轮的空会话外键问题。没有把读包结果当成实时能力验收，也没有把小快照的 complete 帧扩写为已支持分片。

未直接实施已审定的“queue 回执就撤回”规则：本轮读到的实际调用链会把成功 guide 同样回为 queue，照旧实施会撤回有效插话。也未加入 setAutoDrain 或其他新约束：默认自动排空与原先实施前提有冲突，属于 Host 应先收敛的边界。没有改用户配置，没有另起执行通道，没有为了通过探针改原生数据库。是否按这些事实修订 ADR-006/执行边界，以及是否批准一次真实回合实验，交 Host 与开发者；后续模型实验的具体范围在执行计划中列出。

## 时效

安装包的版本或上述 SHA-256 变化、sendText 回执映射变化、会话创建/持久化流程变化、自动排空条件变化、或 v4 通知的 wire 版本变化，都使相应结论需要重查。后续真实模型实验若观测到不同表现，应另写续查记录，不回写本份观测。
