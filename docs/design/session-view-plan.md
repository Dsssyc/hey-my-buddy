# 会话视图执行计划

2026-10-19，依据输入提交 `1d0f23281d2b44511b1422d4baaa450106fc58c9` 核对。本文是 [ADR-024](../decisions/024-console-session-view.md) 到实现微任务之间的计划，尚未实现或验收；本次只写本文，不修改 ADR、代码、依赖和版本。以下源码位置均相对仓库根目录，行号对应输入提交。遵循 ADR-007、ADR-023、ADR-030、ADR-031：服务拥有数据库，harness 层拥有原生读取和投影，跨进程的事实格式归 `protocol/`，内容不进入数据库、备份、导出、Router 或维护模型的输入。

先完成 Codex 的逻辑，包括原生读取、运行中投影、分页通道、能力和保留边界，再完成控制台展示；DSH、ZCode、Claude Code 分别在后续接入。统一事件固定采用 ACP v1 的 `session/update`，不引入聊天运行时，不增加控制台向 Worker 发话的入口。本文的“拟新增”是实现目标；“待核实”是有明确交付物的前置工作，不能用模拟测试的成功替代原生事实。

## 一、Codex 的现状、读取路径与核实门槛

Codex Worker 的线程位于用户的原生存储，私有区保存绑定，两者不能混称“私有会话记录”。依据：`docs/reference/codex.md:3-5,13`；`src/hey_my_buddy/buddy/harnesses/codex/native_run.py:680-710` 从私有绑定核对 task、thread、cwd、buddy 与末次 turn，然后调用 `thread/read`，参数是 `{threadId, includeTurns:true}`，再判断末回合 ID 与状态；它目前没有把会话内容返回给控制台。参考文档中的实测只针对所记录的 Codex CLI 版本和环境，不能推导为 Codex App 或当前安装版本已经接受会话视图。

已结束回合的目标读取方式是 App Server 的只读 `thread/read`，只取这次执行绑定的原生 turn，不直接打开用户原生目录，也不调用 `thread/resume`、`thread/start`、`turn/start`。在 Codex 的运行模块增加原生会话读取操作，复用已有无模型控制连接的初始化、进程持有、协议解析和停进程路径；现成参照是 `native_run.py:1966-1986` 的 `run_delete_sessions` 及其使用的 `_control_connection`。服务先由 authoritative attempt receipt 或 native checkpoint 得到 task/attempt/native thread/native turn 的绑定，再让 harness 读取；浏览器只能提交 attempt ID，不能提交任意 thread ID、文件名或路径。复用 `blackboard/tasks/worker_sessions.py:42-90` 的身份来源原则，但不能直接用它的线程列表代替“每次执行”的身份解析：该列表在 `:96-108` 合并同一线程的多次 attempt，而且只收已结束并确认停止的记录。失败、取消、结果格式无效的执行也应能查看已证实属于它的原生回合；缺少身份时明确显示未记录，不能猜取线程的最后一回合。

`thread/read` 今天只被用于续接校验，现有 mock 在 `tests/python/buddy/harnesses/codex/fixtures/mock_codex.py:220-229` 返回测试线程状态，并未证明完整正文、所有工具输出、压缩前的历史和子线程内容都可读。第一项微任务须先用仓库已有的脱敏原生协议材料，核对 Codex CLI 0.162.1 对应的生成类型、完整 `thread/read` 回复、与之对应的通知序列及版本元数据；`fixtures/protocol_items.py:1-37` 是该版本的离线协议样例，覆盖计划和多种 item，但不是完整的真实会话夹具。`docs/acceptance/evidence/` 中的验收摘要只能证明其写明的字段，不能当成原始会话。本次未发现足以证明所有内容覆盖的完整读回夹具，也没有打开用户原生目录。若仓库内样本不足，微任务交回 Host，列出所缺脱敏样本；不默认发起付费调用。

无会话长度上限还存在一个实质门槛：当前 `codex/protocol.py:14,56-66` 将单个 JSONL 帧限制为 8 MiB，`thread/read(includeTurns=true)` 是整线程回复，没有现成的分页参数。把整份回复读进内存后再切页，不能据此宣称任意长会话可用。第一项微任务必须核对该版本原生协议有没有按 turn/item 分页的只读方法，并用超过 8 MiB 的离线多回合回复验证；有则选定那一条原生路径，并删除试验实现。若只有整线程回复，需给 Host 一份明确方案：在原生读取边界采用成熟的增量 JSON 解析库、流式投影到私有临时页文件，替换该读取操作的整帧入内存路径，保留输入结构校验与单页资源上限，但不设整线程字节上限；依赖版本与许可证在该微任务中固定。不能只调大常数、偷偷转读用户文件、增加第二种回退读法，或把超限答成会话为空。原生接口若本身已经丢失内容，记录缺口并由 Host 决定能力声明和 ADR 的文字，不能在展示线上补造。

运行中已有逐条接入点：`codex/protocol.py:103-124` 将通知交给 `on_notification`；`codex/native_run.py:1144-1151` 建立共用工具证据投影与事件链；`:1274-1317` 以 `turn/start` 回执绑定 root thread/turn，在 carrier 中观察事件并持续 pump。新会话投影挂在这条事件链上，使用相同的身份绑定规则；不能另起一个 App Server 去跟随活线程。L5 收集器为了审阅证据会在角色过滤前观察事件，但会话视图不能因此把别的执行或未经绑定的线程内容混入当前执行。原生子代理有显式父子关系时保留关系和其实际给出的内容；只有子线程 ID 而无正文时声明缺口，不能任意读取用户的其他线程。

当前已知通知表为 `native_run.py:191-198`：`turn/started`、`item/started`、`item/updated`、`item/completed`、`turn/completed`；消息增量 `item/agentMessage/delta`；推理增量 `item/reasoning/summaryTextDelta`、`item/reasoning/textDelta` 与 `summaryPartAdded`；`rawResponse/completed`、`turn/diff/updated`；线程、用量、限额、警告与错误等。`protocol.py:275-292,314-360` 今天只从其中提取活动、计数、用量和最终消息，不是内容投影。计划专用的 `turn/plan/updated`、命令输出增量等方法不在这个表里；是否在该 CLI 版本实际出现、字段与 item 的对应关系，须从脱敏通知夹具核对后接入，不能把方法名的猜测写成当前能力。`tool_evidence.py:21-77` 认识 typed `plan` 与 raw carrier；`fixtures/protocol_items.py:7,30-37` 的计划仅有文本，不能凭这条文本制造 ACP 结构化计划的步骤和进度。

建议由 Host 修订 ADR-024 第 5 条及背景第 13 行、影响中“四个私有记录读取器”的表述：改为“内容留在 harness 原生存储或执行私有记录；运行中从已有原生事件流投影，经执行进程的分页运行中通道给出；已结束回合经 harness 原生只读接口或读取执行私有记录，按该 harness 的实际存储方式声明；Codex 线程通过 App Server 读取与删除，不遍历用户原生目录”。`docs/reference/harness-integration.md:119` 也需同步去掉所有 harness 都直接读私有文件的假设。ADR 修改属于 Host，不在本次写入范围。

## 二、统一事件、投影和内容边界

固定 ACP 协议主版本为 v1（`protocolVersion:1`），事件名使用 v1 的 chunk 形式，禁止混入调研所述 v2 `agent_message`。依据为 `dsh/protocol.py:44-50` 的既有 v1 事件词汇，以及 `docs/research/2026-10-18-session-transcript-projection.md:12,19-26` 的版本区分。本地目前没有 ACP 会话 schema 的正式锁定快照，不能捏造 npm/Python 包版本或 schema 摘要；第一项微任务交付 v1 官方 schema 的离线快照、来源版本与 SHA-256，作为后端及前端夹具的共同依据。版本选择在本文固定为 v1，具体快照校验完成后再写模型；若无法在授权范围取得已存档的正式 schema，交给 Host 提供材料，不凭记忆自创 ACP 字段。

一条内容事件采用 ACP 通知形状 `{sessionId, update}`；`sessionId` 使用执行会话的稳定不透明 ID，不能让同一原生线程多次 continuation 合并成一个执行。分页外层另带事件 ID、source epoch 和递增序号，原生 thread/turn/item/call ID 的对应关系保留在私有投影器内部或 ACP `_meta` 中的无路径身份字段；外层不改变 ACP update 的含义。统一使用 `user_message_chunk`、`agent_message_chunk`、`agent_thought_chunk`、`tool_call`、`tool_call_update`、`plan`，按正式 v1 schema 校验。原生只给文本就保留 text block；确有图像或其他内容块才声明相应能力，不能把用户文件路径变成浏览器可直接打开的资源 URL。

消息字段映射：原生 `userMessage` 的文本内容块投成 `user_message_chunk.content`，`agentMessage.text` 与 `item/agentMessage/delta.params.delta` 投成 `agent_message_chunk.content={type:"text",text}`；phase、item ID 与 parent 关系在无路径元数据中区分正文、commentary、final answer 和子代理。已结束回合按原生 item 顺序投影一次完整内容；运行中按 item 的文本偏移追加 delta，末次 completed item 只补尚未发送的尾部，不能将累计文本与 delta 再各显示一遍。相同 item 的中途更正若原生允许，应提升 source epoch 后重读该投影，不能假装成新的文字追加。具体 userMessage 内容数组、phase 和 raw response 是否与 typed item 重复由第一项夹具确认。

推理字段映射：原生 reasoning 的 summary 文本和实际给出的 text 分别投为 `agent_thought_chunk`，以无路径元数据说明 summary 或 text；`summaryPartAdded` 只确定段落身份，`summaryTextDelta`、`textDelta` 提供内容，completed reasoning item 用于终态核对。隐藏、加密的 reasoning 不能解密或推测，也不能把摘要称为完整推理；Codex 能力行应写“仅显示 Codex 提供的推理摘要或文本，不含未提供的内部推理 / Shows reasoning summaries or text supplied by Codex; internal reasoning is not provided”。这是目标措辞，实际是否有可读 text、历史读回保留哪一种，仍以 CLI 版本夹具为准。

工具调用字段映射：`item.id` 或原生 `call_id` 成为 `toolCallId`；原生工具名或 command 标题成为 `title`；native status 只映射到 ACP v1 的 `pending/in_progress/completed/failed`，取消和中断的原生事实留在内容或元数据，不能制造 ACP 未定义的状态。参数与返回结果分别放 `rawInput/rawOutput`，命令累计输出、文件差异、原生工具结果作为 ACP `content` 的内容块，开始和后续变化分别发 `tool_call` 与 `tool_call_update`；typed call、raw call、raw output 通过原生调用 ID 关联，在同一处处理重复 carrier。`turn/diff/updated` 只有能与原生 item 关联时才归入那个调用，否则保留为带来源说明的内容，不能猜是哪次 edit。plain `plan.text` 保留文本；只有原生给出步骤、状态与优先级时才发 `plan.entries`，优先级缺失的处理须由 schema 与样本确定，不能让 UI 推断进度。

工具类别 `kind` 必须复用 L5：`src/hey_my_buddy/protocol/tool_evidence.py:22-31,54-79,124-175` 的 ACP 类别表、`NATIVE_TOOL_CATEGORIES` 与 `normalize_tool_event`；Codex 原生 carrier 的调用/输出身份抽取复用 `codex/tool_evidence.py:114-153` 及后面的 raw/output 关联。新内容投影拿分类后的 `category` 作 `kind`，不在前端或会话读取器另写“shell 就是 execute”的表。现有 L5 包只留 128 条工具证据，且明确丢弃参数与正文；会话内容不能读该摘要包，也不能继承其 128 条上限。需要抽出共享的无副作用“识别调用与分类”函数时，在同一微任务移走旧实现和对应测试，不留第二张表或别名。L5 的审阅判定、活动白名单和会话正文仍是不同输出。

“回复中不出现文件路径”有一处需要 Host 同步明确 ADR：ACP 的 `locations[].path`、差异、命令参数及输出本来可能含路径，与“原生给出多少就展示多少”按字面存在冲突。建议将此句限定为“不返回会话源文件、私有目录、凭据或原生存储的定位路径；API 不接受路径；原生会话内容中的路径作为原始内容保留，不作为本机文件读取入口”。按这个建议实现时，`locations` 只来自原生工具事实，绝不填会话记录所在路径；返回的错误、游标和 source metadata 也不带存储路径。若 Host 要求连原生正文里的路径也消除，应先修改“全部内容”的决定并定义脱敏契约，不能在实现里静默删字。本文不假定 Host 已接受这项澄清；展示微任务依赖该文字明确。

未知但合法的原生 item 不吞掉：能作为 ACP 内容块表达的，完整保留原生给出的可读内容并标出原生类型；不能无损表达的结构在无路径的原生内容区展示其 JSON，或在能力中说明确实没有给出的内容。只有外部边界做一次 schema 校验，非法外部数据明确报错，内部信任已经校验的类型。不把未知内容当成工具批准，也不因会话渲染能力改变 Worker 的执行结果、停进程证据或 Host 验收结果。

## 三、只读分页接口和运行中的追加

新增本机控制台 HTTP 读取 `GET /api/tasks/{taskId}/attempts/{attemptId}/session`，经过现有私有 URL 前缀和浏览器认证；不加到 Host CLI、`BuddyControl` 或 `BuddyWait`。控制台 server 与 service 同进程，可直接调专用 session reader，服务只用数据库解析身份、删除状态和能力，不在事务中读文件或等原生 RPC。现有入口与安全边界见 `console/server.py:652-671,738-767,863-878`，定期小快照只取摘要见 `console/reads.py:23-55`；会话内容不并入 `/api/console`、任务结果或宏任务时间轴的轮询回复。

参数固定为 `cursor`（首次省略）、`direction=before|after`（首次默认 before）、`limit`（默认 100，最大 256）。首次返回最近一页，所有页内事件均按从旧到新排序；`beforeCursor` 指向该页最早位置之前，连续 before 到 `hasEarlier:false` 即到开头；`afterCursor` 指向该页末尾之后，连续 after 收取新内容。游标是不透明、经验证的编码，绑定 task、attempt、generation、source epoch、投影版本和位置，不编码路径或让浏览器修改任意原生身份；跨 attempt、过期 epoch、非法参数返回明确错误。内容有数量分页和字节分页，但没有整段会话的总页数、总字数或总时长上限。

拟定回复为 `{attemptId, harness, format:{protocol:"acp",protocolVersion:1,projectionVersion:1}, sourceEpoch, state:"running"|"ended"|"unavailable", capabilities, items:[{id,seq,partIndex,partCount,data}], beforeCursor, afterCursor, hasEarlier, hasLater, observedAt, reason}`；`items` 是下面定义的统一传输分片，重组后每个事件都是 `{sessionId,update}`，不设“大事件走另一套字段”的分支。`reason` 只在不可用或缺失时出现，使用结构化原因而非源路径或原始异常。不可用、harness 不支持、已删除和原生线程不存在分别有明确状态；空数组仅表示这一页确实没有新事件。`hasLater` 表示当前已提交的后续页，运行中的未来事件不算已存在；浏览器据 `state` 决定是否继续轮询。返回 JSON 不含源记录路径；原生内容路径的解释按上一节的 ADR 澄清落实。

64 KiB 是现有 live 帧上限（`buddy/harnesses/live.py:27-33`，`c_two_live.py:31-39`），不能把一个很长工具输出塞进单帧后截尾。新分页器在严格计算 UTF-8 编码后的包络大小内分页；先将 ACP 通知序列化为 JSON 文本，再沿 Unicode 字符边界分为可装入包络的 `data` 字符串，以同一事件 `id/seq` 和从 0 起的 `partIndex/partCount` 标识位置，小事件也使用 `partCount:1` 的同一形状。页面不得切断一个传输分片；巨大 `rawInput/rawOutput` 不被改写为字符串，分片在组装完成、解码 JSON 后仍保留其原生结构，然后才按 ACP schema 校验与交给渲染器。分片属于分页包络，不新增 ACP 事件种类；HTTP 与 C-Two 共用这一套编码器，游标可以落在事件片段之间，且 before/after 跨分片可逆。跨页组装用私有文件或成熟的流式机制，不能把全会话累积在服务或执行进程内存中；UI 按需装载单个大内容块，不能以折叠为由丢掉正文。第一项须用超过单页的单工具 JSON 和多字节文本验收这一条件，不能只测很多短消息。

运行中：Codex 原生事件链投出内容，在 attempt 私有的 `session-view/` 下写只追加临时事件记录及分页索引，服务重启不要求执行进程重放模型；写入对象是当前 attempt/generation，自创建时固定，不能由前端指定文件。记录从原生回合开始即收集，不能等浏览器第一次打开才收集，否则长运行的前半段永久丢失。执行进程在已有 `ExecutionLive` 资源增加专用 `session_read(request_json:str)->str`，携带现有 identity/instanceId/token 的认证包络以及游标参数，转给当前原生通道；service 经 scheduler 已绑定的 client 读取。`buddy/runtime/execution.py:100-163,185-202` 是已有资源、认证和 observe 的接入处；`blackboard/service/execution_scheduler.py:313-334` 与 `live_registry.py:10-43` 是服务持有通道的接入处。原生通道不可达只意味着当前不可读取，不证明执行停止，不自动切成历史读取或把活动清零。

现有 `observe` 是“最新快照”，不是完整事件日志，依据 `c_two_live.py:31-39`。因此新增 `session_read` 而不扩充旧快照的任意 fields，不把内容塞进 activity 或 inquiry journal；仍共用同一个 execution 资源和已认证连接。service 到浏览器沿用页面可见时的 HTTP 轮询节奏，session 面板打开时每 3 秒用 after cursor 读取，`hasLater` 为真时继续排空已有页，隐藏或关闭即停止，并取消未完成请求；现有节奏与 AbortController 可复用 `apps/console/src/task-detail/use-task-detail.ts:29-33,60-71`。不为这一步另加 SSE/WebSocket、常驻订阅或后台全量预取。

执行结束并由服务确认其终态后，历史读取唯一来源改为 Codex 原生只读线程接口；live 临时事件记录不成为第二种历史回退。source epoch 从 live 切成 native-terminal，旧 cursor 明确返回 `SESSION_SOURCE_CHANGED`，浏览器按同一 attempt 重新取历史页并替换当前内容，按原生 item 标识恢复阅读位置，不能把历史完整 item 与已显示 delta 拼接造成重复。临时 live 文件在终态切换完成后作为临时资源删除；正在使用它的读取以明确的 source-changed 结束。执行进程异常死亡而终态尚未确认时，显示通道不可用，待正常恢复流程确定状态，不自行猜测。历史源后来被用户在原生应用中修改或删除时，也要改变 epoch 或返回源缺失，不继续使用旧索引伪装成功。

上述 native-terminal 方案以第一项证实读回能覆盖运行中原生给出的全部应保留内容为前提。如果 native read 没有保存某些工具输出、推理文本或历史，不能一结束就删掉唯一留下这些内容的 live 记录；第一项应在写接口之前把缺口交给 Host，并提出将 Codex 也改为“执行时写私有记录、结束后封存该记录作唯一历史源”的具体改案，由 Host 修订 ADR 与后续微任务。那是替换本计划的历史来源，不是增加缺了就换一种读法的回退；不能一边承诺原生 API 全量读取，一边把缺失的正文留给 UI 处理。

分页、文件读取、原生 RPC 和大内容分片都不占 authoritative 数据库写事务；目录与外部协议校验在各自边界完成。宏任务删除开始标记后拒绝新的读取；服务内的按宏任务读取租约只协调正在进行的 session read 与删除的物理阶段，删除等待读取退出并清掉临时页文件，不向数据库增加会话内容或读取租约。现有删除的标记、物理阶段、末次事务顺序见 `blackboard/tasks/macro_task_deletion.py:558-581`，应复用这个生命周期，不引入另一个独立删除器。

这次实现会改变服务与执行进程的契约，即使 Host 的方法集合不变也要移动中间版本：当前 `protocol/contracts.py:17,208-228` 是 `0.46.0` 和六个 ExecutionLive 方法，拟增加上述 `session_read` 方法并扩展能力事实；`tests/python/protocol/test_contract_version.py:17-44` 将三个资源的方法签名一起固定。以集成时版本为基准一次升到下一个中间版本，更新 `CONTRACT_VERSION`、项目版本及 `contract_signatures.json`，不预留旧方法或支持旧 JSON 形状。安装需按 ADR-031 对变更契约的规则等执行结束并演练从已安装版本升级；不能因为新增的是只读功能就宣称无需契约升级。数据库没有新增会话表、meta 行或正文列，本计划不要求 schema 升级；若实现另改持久绑定格式，则必须在同批扩展已有 24 → 25 升级，不能隐式重解释旧值。

## 四、能力声明、保留和空间检查

在统一 harness 注册报告中拟新增结构化 `sessionContent` 能力：`implemented`、`live`、`historySource`（`native-api|private-record|none`）、可提供的 content 种类、`reasoning`（`summary|text|none` 的实际集合）、原生未提供项的原因码及观测的 CLI 版本依据。声明由各 harness 的实现给出，扩展 `registry.py:167-186` 的 capability report；服务侧的 `blackboard/service/harness_health.py:53-83` 目前只拼接 read-only 等已有能力，需要显式接入会话能力，不能假定 registry 新字段自然出现在控制台。实现声明从注册表生成，实际版本依据来自已有 harness 观察记录，本计划不新增持久能力结构。live 的能力模型在 `live.py:45-65` 中同步声明，不能只在 UI 按名称写死 Codex 支持。会话内容不入数据库，不新建版本付费认证门槛，不把 capability 当模型执行资格要求。

Codex 逻辑验收完成后才声明已实现；另外三家本批写 `implemented:false`、`live:false`、`historySource:none` 与“尚未接入”的原因，不因它们有原生日志就提前点亮入口。区分“harness 没提供”“产品尚未接入”“这个历史执行没保存”“当前读取失败”；前者形成一行能力说明，后两者形成该执行的读取状态。中英文用相同原因码翻译，例如“此 harness 的会话视图尚未接入 / Session view is not implemented for this harness”，不能都显示成“没有消息”。后续逐家接入时替换自己的声明，不为一家的缺项制造四家共同的假限制。

Codex 的原生线程删除已有足够的主路径：`macro_task_deletion.py:325-350` 从已证实库存选线程、检查 harness 可用性并按 harness 删除，`:580-581` 先删 native session，再删 workspace/private root；`codex/native_run.py:1966-1986` 调用 `thread/delete`，只把明确的原生不存在错误视为已删除。新增内容读取无需另删用户原生目录，也不能扩展到未证明属于宏任务的线程。仍要补验证：同线程多次 continuation 只删一次、失败 checkpoint 的线程纳入、引用冲突拒绝、原生删除失败保持删除标记可重试、浏览器打开会话时删除、所有临时投影与页缓存随宏任务清掉。子代理线程是否由 root `thread/delete` 一起删除，仓库现有离线材料没有证明；第一项检查脱敏父子线程删除回复，若不级联就由 Host 决定将已证实的子线程身份纳入现有删除库存，不能猜测 root 删除已经覆盖全部。

检出回收目前不满足会话保留目标：`blackboard/tasks/workflow.py:5801-5809` 清理私有 micro task root 时只 keep 指定 attempt 目录，`private_dirs.py:142-172` 递归删除其余内容，会删掉其中的 native 会话和 Codex 绑定。保留微任务须为已证实的会话内容建立独立的保留子目录或精确 keep 集合，并保留历史读取所必需的无凭据身份；凭据、配置、可删除的原生数据库仍按原流程清除。Codex 正文在用户存储，回收不能调用 `thread/delete`；读取应依赖持久的 attempt 原生身份，不能依赖已删 checkout 仍存在，控制连接 cwd 应用仍存在的私有读取工作目录，不能为读会话恢复检出。DSH/ZCode 混在原生库中的会话内容需在它们各自后续接入前落实分离保留，不能把整个带凭据 native root 永久留下。

存储检查目前在 `blackboard/tasks/storage.py:474-494` 将私有 micro task root 整体计为 `harnesses`；宏任务删除预览在 `macro_task_deletion.py:303-308` 对用户存储 session 只有 count，bytes 为 null。新增私有会话记录分类列出其实际 bytes、所属宏任务和“随宏任务删除”的原因，普通 storage apply 不可单独删除；从原有父项扣除这部分，不能重复统计。live 临时页与历史临时索引也列为临时会话读取占用，结束后应归零；它们不是可独立保留的第二套历史正文。

Codex 用户原生线程只列线程数量和“由 Codex 管理，空间未计入 / Managed by Codex; size not measured”，bytes 保持 null，不能写 0，也不能通过遍历用户原生目录凑空间；目前的 thread receipt 没有原生大小，`worker_sessions.py:1-7` 明确不打开用户会话文件。若以后 App Server 提供无副作用的字节统计，再由独立变更核实接入。建议 Host 同步将 ADR-024 第 5 条“存储检查列出空间”写明只计产品私有记录；外部原生存储明确未测量。设置中的数据流向说明补充“会话仅在本机显示，可能含命令输入输出；不进入备份和导出”，中英文都补。

## 五、控制台入口、组件与验证

当前前端已经用 React 19、Tailwind 4、Radix、Lucide 和 i18next（`apps/console/package.json:16-41`），Vite 已装 Tailwind 插件且输出到包内 assets（`vite.config.ts:1-14`），`src/index.css:2-4` 导入 Tailwind、动画与项目 tokens，`src/components/ui/` 是已有展示基础；因此无需再引入一套 Tailwind/shadcn 基础。但 `package.json` 没有 AI Elements、AI SDK、Streamdown 或 Shiki，本地 `src/` 也没有 AI Elements 组件；不能把 ADR-029 采用的样式基础说成会话渲染组件已经接入。

遵循 ADR-024 第 6 条先试用 AI Elements，仅纳入 conversation、message、reasoning、tool、code block、terminal、plan 及文件差异展示实际需要的展示组件，复用已有 ui 与 tokens，不引入聊天输入、模型连接、附件上传和 agent runtime。源码形式的组件逐个审阅依赖与 MIT 许可，在实施展示微任务时固定来源版本和依赖锁；本计划不联网，尚未核对将采用的组件发布版本、具体所需的 Markdown/代码高亮依赖或差异组件，不能报一个假精确的增量体积。正式实现前先完成离线依赖清单；若 AI Elements 对某类内容没有合适展示组件，再向 Host 给出成熟组件选择及代价，不自行造一套通用渲染库。

体积基线为输入提交已跟踪构建产物 `src/hey_my_buddy/console/assets/assets/index-BkBm67PG.js:1` 的 860,295 字节与 `index-By9rw8hx.css:1` 的 71,209 字节（本次用 `wc -c` 量得，未重建，不代表当前机器新 build 的输出）。展示任务在同一 Node 和锁文件条件下先后构建，记录入口 JS、会话异步 chunk、Markdown/高亮/差异异步 chunk、CSS 和总量的 raw 与 gzip 差值。用 `React.lazy`/dynamic import 将 SessionView 从首屏分出；高亮器和所需语言在展开代码块时再载入，避免全语言包。会话入口之外的页面不拉这些 chunk；构建和浏览器网络证据是验收项，不能凭“按需加载”四个字视为完成。

微任务详情的执行回合 `TurnPane` 添加“查看会话 / View session”，直接使用当前 span 的 attemptId，不依赖已接受的 turn 是否存在；现有回合解析与尚未读取正文的边界见 `apps/console/src/task-detail/TurnPane.tsx:20-23,100-123`。时间轴执行片段使用已有 `onOpen(runId,key)` 导航，`timeline/TaskWork.tsx:33,149`、`task-detail/TaskDetail.tsx:79-95` 已能按片段定位步骤；在这条路径携带 session 面板选择，打开同一 attempt 的会话，不能默认跳到最新 attempt。路由在现有 `task-detail/location.ts:1-7` 的任务/步骤地址上明确扩展会话位置，支持刷新、前进、后退和返回原执行片段，禁止为入口另造一份 timeline 数据。

会话面板头部显示执行回合、harness、状态与一行能力说明，主体按消息顺序显示正文、可读推理、工具输入输出、差异、计划；长内容可折叠，展开不截断，复制可取全量。最新页从尾部进入，向上加载旧页时锚住视口；人在底部时跟随追加，离开底部时只提示“有新内容 / New content”，点击才回到底部。回合切换、epoch 切换与语言切换均不把上一个请求结果写进另一个回合；只保留有界已加载页窗口，翻回已卸载区域可按游标重读，不限制可达历史长度。若需要虚拟列表，应选成熟库并计入体积，不靠自制滚动算法和无限 DOM 实现“无限历史”。

展示遵循 ADR-029：层级、留白和形状承载信息，工具区不用一层层圆角卡片叠套，颜色沿用既有意义；窄屏、键盘展开、返回焦点、减弱动态效果都可用。中英文与产品文案进入 `src/i18n/zh/taskDetail.ts`、`en/taskDetail.ts` 及 settings 对应文件，现有命名空间注册见 `src/i18n/index.ts:3-29`，不翻译原生会话内容。至少覆盖“加载更早内容 / Load earlier content”“已到开头 / Start of session”“当前无法读取 / Session unavailable”“未保存此执行的会话 / Session was not recorded”“线程已被删除 / Native thread was deleted”，并与尚未接入、确实没有新事件区别。

会话 Markdown 和工具输出都是外部内容：组件按现成安全渲染路径处理，禁用任意 HTML 执行、脚本链接和本机文件协议；附件读取只通过受保护的执行内容接口，不开放任意路径文件服务。权限校验复用 console 现有 Host/Origin/登录检查，HTTP 回复 `Cache-Control:no-store`，错误不泄露源路径。浏览器关闭面板立即停止读取；定期 summary、前端日志和遥测不夹带正文。只读视图不显示发送、停止 Worker 或回答提问的操作。

## 六、另外三个 harness 的后续接入

DSH：当前 `buddy/harnesses/dsh/native_run.py:963-966,1060-1078,1118-1122` 把 sessions root 固定在私有区并读回会话事实，`:606-618` 用原生配置给出会话存储根；`dsh/session_records.py:1-8,35-44,49-101` 已用项目的 zstandard 流式读取 `session.v3` JSONL 或 zstd。后续复用该原生记录解析与 ACP `session/update` 接入点，按 attempt 的起止记录位置隔离续接回合，接同一个分页接口；其当前用量/最终文字读取器有 64 MiB、512 steps 等摘要上限，不能直接拿来作无限会话读取器，也不能沿用吞异常后标 partial 的摘要行为。重构共同的逐条解析时保持用量摘要的旧业务上限只在摘要输出层，会话读取层则按页限制。完整推理、图像和工具输入输出是否都持久化，须由有版本的脱敏 JSONL 与通知对照确认。

ZCode：`buddy/harnesses/zcode/native_run.py:723-738` 明确设置私有 `storage/`、`sessions.sqlite` 和每次调用的 `native-logs/`，没有把这些归到用户原生存储。后续先核对该 CLI 版本的数据库/原生日志结构、原生只读接口及接入逐条事件，选一种有原生依据的历史来源，不同时维护数据库与日志两种回退读取器；同一会话续接按原生回合或已证实的边界分页。回收时从需清除的原生数据库分离会话内容，私有保留记录和日志不能混带凭据。工具类别继续共用 L5；完整推理与工具结果的覆盖以夹具为准。

Claude Code：当前 P1 的“无持久原生会话”是启动方式的事实，并非 Claude Code 所有形态都不支持会话。`buddy/harnesses/claude/config.py:112-113,136-148` 使用 CLI stream-json 并加 `--no-session-persistence`；`native_run.py:1322-1327` 的 session facts 为不持久化且不续接；`claude/protocol.py:90-102` 从 stdout 逐条解码入队，没有保存完整流；`claude/native_run.py:330` 将 stdout 接 PIPE、stderr 接文件。因此当前已结束回合没有可供重建完整会话的原生持久记录，stderr、最终结构化 outcome 和活动摘要都不能替代正文。

Claude 的后续实现须在已有 collector 的 stream-json 接入点从回合开始，把原生给出的会话内容持续投影并写入 attempt 私有、无凭据的会话记录，使用与运行中分页相同的记录，终态封存，随宏任务删除；不改用户配置、不取消 `--no-session-persistence`，也不额外打开用户会话库。是否有逐 token partial event、thinking block 及工具结果，核对当前 CLI 版本和已有原生样本；若只有完整 assistant frame，就按实际粒度追加，不为逐 token 显示打开新的 native 开关。旧执行没有记录就声明“未保存”，不回填或发模型调用重建。这样同一读取接口支持 `native-api` 的 Codex 和 `private-record` 的另外三家，差异来自能力声明而非第二套页面。

## 七、微任务顺序、写入范围和检查

下面是后续委派边界，不是本次授权写入范围。所有测试通过仓库 checks runner 启动，测试进程使用其私有根；在 hey-my-buddy 执行内按仓库规则清除继承的运行时与代理凭据变量。不访问日常板、不启动真实 harness、不新增付费调用。新测试目录需带 `__init__.py`。每项交付实际 diff、退出码与失败摘要，Host 审查后才进入下一项；实现声明、离线验证、原生样本验证和安装是不同的验收状态。

微任务 1，Codex 样本与原生内容读取、统一投影。写入范围：`buddy/harnesses/codex/`、必要的 `buddy/roles/run_controller.py` 与 `run_execution.py` 无模型控制操作、`protocol/session_content.py`（新增）、L5 共享抽取的 `protocol/tool_evidence.py`、对应 `tests/python/buddy/harnesses/codex/`、`tests/python/buddy/roles/test_run_controller_operations.py`、新增 `tests/python/protocol/test_session_content.py`、脱敏 ACP schema/会话夹具，以及 `docs/reference/codex.md` 和一份 `docs/acceptance/session-view-codex-read.md`。先交付版本化 schema、通知/读回对照矩阵、长线程与子代理缺口结论，再做唯一原生历史读取路径和内容投影。覆盖消息与推理文本、delta/completed 去重、工具多 carrier 关联、计划文本与步骤差别、失败回合、线程源缺失、超 8 MiB 回复和超页单值；原生材料不足时交给 Host 补脱敏材料，不做真实调用。检查：`uv run --frozen python -m hey_my_buddy.cli.checks tests/python/buddy/harnesses/codex tests/python/buddy/roles/test_run_controller_operations.py tests/python/protocol/test_tool_evidence.py tests/python/protocol/test_session_content.py`。这是逻辑后续各项的前置；“完整内容”和“任意长历史”两项未证实就不能宣布 Codex 接入完成。

微任务 2，分页运行中通道与能力声明。依赖 1 的冻结格式和身份绑定。写入范围：`protocol/contracts.py`、`protocol/session_content.py`、`buddy/harnesses/live.py`、`c_two_live.py`、`registry.py` 与各 adapter 的声明、`codex/native_run.py` 接入点、`buddy/runtime/execution.py`、service 的 live registry/scheduler 客户端接缝与 `harness_health.py`、项目版本与锁文件、`tests/python/protocol/contract_signatures.json` 及相应测试；新增 session-view 临时记录帮助模块必须位于 buddy 或 protocol 的正确所有权侧。实现按页写入/读取、64 KiB 包络与大值分片、运行中从开头可读、服务重连、epoch 切换和能力如实声明，禁止扩大 activity 字段。检查：`uv run --frozen python -m hey_my_buddy.cli.checks tests/python/buddy/harnesses/test_c_two_live.py tests/python/buddy/harnesses/test_live_channel.py tests/python/buddy/runtime/test_execution_process.py tests/python/blackboard/service/test_live_channels.py tests/python/protocol/test_contract_version.py tests/python/protocol/test_session_content.py`；在这些文件补针对新方法的身份拒绝、分片边界、重连与源切换场景。契约升级统一在本项落下，不留可混用的旧形状。

微任务 3，本机只读 HTTP 读取。依赖 1、2。写入范围：新增 `blackboard/tasks/session_content.py`（只解析数据库身份）、新增 `console/session_content.py`（读取编排）、`console/server.py`、必要的 service 显式读取接缝、对应 `tests/python/console/test_console_session_content.py`（新增）与 `tests/python/blackboard/tasks/test_session_content.py`（新增）、`tests/python/console/test_console_access.py`。实现请求/回复、游标边界、逐页读到开头、after 排空、source-changed、仅本机浏览器权限及 no-store；不新增 Host 命令或 BuddyControl 方法。检查：`uv run --frozen python -m hey_my_buddy.cli.checks tests/python/console/test_console_session_content.py tests/python/blackboard/tasks/test_session_content.py tests/python/console/test_console_access.py tests/python/console/test_console_reads_http.py`。验收跨 attempt 游标与任意 thread/path 均不能读取、未登录与跨站请求拒绝、错误不带源路径、单个巨大输出仍完整可读、数据库和备份无正文。

微任务 4，保留、空间与删除闭环。依赖 1 的原生身份结论，接在 3 后以便测试 HTTP 与删除并发。写入范围：`private_dirs.py`、`blackboard/tasks/workflow.py` 的回收段、`storage.py`、`macro_task_deletion.py`、`worker_sessions.py`（只在身份缺口经证实后改）、上述读取编排的删除协调、相关 fixture 和测试，以及 `docs/reference/operations.md`、`codex.md` 与接受记录。实现检出回收后仍可读、删除原生线程及全部临时页、普通空间回收不删会话、Codex bytes 为未测量、私有正文统计不重复。检查：`uv run --frozen python -m hey_my_buddy.cli.checks tests/python/blackboard/tasks/test_workspace_submission_cleanup.py tests/python/blackboard/tasks/test_storage.py tests/python/blackboard/tasks/test_worker_sessions.py tests/python/blackboard/tasks/test_macro_task_deletion.py tests/python/blackboard/tasks/test_macro_task_deletion_files.py tests/python/blackboard/tasks/test_macro_task_deletion_sharing.py tests/python/console/test_console_session_content.py tests/python/buddy/harnesses/codex/test_delete_sessions.py`。若实际持久格式变了，同项扩展 `blackboard/store/upgrade.py` 的 pending upgrade 与其 fixture 对照测试，不推迟到展示项。

微任务 5，控制台会话视图。依赖 1 至 4 的接口、能力、原生样本及 Host 对 ADR 文案的修订；先逻辑后展示到此严格成立。写入范围：`apps/console/src/session-view/`（新增）、所用 AI Elements 展示源码与现有 ui 接缝、`task-detail/`、`timeline/Tracks.tsx`/`TaskWork.tsx` 的执行片段导航、`api.ts` 与 API parser、`i18n/zh/` 和 `en/`、设置空间和数据流向页面、前端 package/lock、`apps/console/browser/session-view.check.ts`（新增）、相关 preview 夹具、构建产物 `console/assets/`；必要的 Python preview 夹具只改模型无关样例。先比较 AI Elements 依赖与构建代价，再接同一接口；默认不为 UI 引入 Python 生产代码变更。检查必须为最终源码执行 `npm --prefix apps/console test` 与 `npm --prefix apps/console run test:browser`，后者包含 build；另执行 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/console/test_console_preview.py tests/python/console/test_console_session_content.py`。浏览器覆盖英文/中文、窄屏/宽屏、时间轴跳到旧执行、返回焦点、刷新与前进后退、向上分页锚定、滚动离底时追加、epoch 切换、超页工具输出和缺项状态；记录异步 chunk 加载和构建前后体积。测试浏览器若未安装，由 Host 按仓库说明准备，本项不擅自运行下载命令。

微任务 6，Codex 集成验收与文档闭合。依赖 5，写入范围只为稳定后发现的阻塞修复、其直接测试、拥有行为的 reference、skill 相应入口、README 中英文必要摘要和 `docs/acceptance/session-view-codex-host-review.md`；由 Host 负责最终审查与接受。对照本计划八项逐条记录实证与未验证项，核对“打开会话 → 运行中追加 → 结束重读 → 回收检出仍能读 → 删除宏任务不可再读”的完整模型无关链路，先在私有模拟板完成；已有脱敏原生样本只证明其版本。因通道、回收和删除影响面广，最终源执行 `uv run --frozen python -m hey_my_buddy.cli.checks`，前端全量与浏览器检查若在 5 之后无变化不重复；有改动则重跑受影响检查。安装演练另由 Host 在私有环境按 installed 版本到新契约执行，不能使用旧服务不接受的字段；本批不自行安装或重启日常服务。Windows、Linux 及没有样本的内容种类如实留为未验证。

后续微任务 7、8、9，依次为 DSH、ZCode、Claude Code 接入，每项只写自己的 `buddy/harnesses/<harness>/`、共享读取器中该 harness 的明确注册、私有保留与空间规则的必要接缝、对应 harness 测试/脱敏夹具、能力文案和 reference，不重做 HTTP 和会话页面。每项依赖 Codex 集成验收；先 DSH 再 ZCode、最后 Claude 的新增持久记录，便于先验证已有原生记录的两种来源。各项检查至少是 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/buddy/harnesses/dsh tests/python/console/test_console_session_content.py tests/python/blackboard/tasks/test_storage.py tests/python/blackboard/tasks/test_macro_task_deletion_files.py`（DSH）、将 harness 目录换成 `tests/python/buddy/harnesses/zcode`（ZCode）或 `tests/python/buddy/harnesses/claude`（Claude），另加各项新建的私有记录保留测试；修改前端文案或解析时仍跑前端全量，改变展示或 preview 时加浏览器检查。Claude 必测旧执行明确未保存、新执行流式记录封存与回收保留；三家都用现成脱敏原生样本验证推理和工具结果覆盖，不通过新增付费调用填空。

## 八、本次交付与检查边界

本次只核对仓库文件并新增本文，没有调用 App Server、真实 harness、模型或网络，没有读取用户原生 harness 目录，没有构建前端或改写既有构建产物。Host 需接续的事项是按第一项收齐正式 ACP v1 schema 与 Codex 完整脱敏读回/通知材料、审定 ADR 的存储及路径措辞，再按依赖委派实现；它们是后续实现的条件，不表示本计划已证明产品能力。

本次尝试运行指定的文档卫生检查：`uv run --frozen python -m hey_my_buddy.cli.checks tests/python/cli/test_repository_hygiene.py`，清除了继承的运行时与代理凭据变量，并设 `UV_OFFLINE=1` 保证不联网。uv 初始化缓存时打开 `~/.cache/uv/sdists-v9/.git` 被权限拒绝，报 `Operation not permitted (os error 1)`，退出码 2，检查尚未启动；没有换路径重试。需 Host 在有缓存访问权限的环境执行该检查，本文不将它记为通过，更不将其记为会话功能已验收。
