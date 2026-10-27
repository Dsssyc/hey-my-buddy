# 会话视图执行计划

第 3 版（2026-10-19，开发者定）：每次执行一个记录文件，放在所属分组的目录里；记录写失败不再判回合失败；不属于任何分组的 Router 调用不记录。第二至四节里关于记录位置、回收保留与写失败的部分，以第九节为准；格式行与字节偏移的分页不变。

2026-10-19，按 Host 对第一版的审查意见改写。本计划以检出提交 `1d0f2328` 的源码行号为依据；Host 已说明其后合入的 `3cdbba03` 只改存储与安装，升级基点现为 0.46.0，没有待执行的数据库升级。本文只安排实现，不表示功能已实现或验收；本次只修改本文，ADR 的修订由 Host 完成。

执行进程从回合开始，在各 harness 已有的原生事件接入点投影 ACP 事件，追加写入本次 attempt 私有目录固定名字的 `session.jsonl`。这份记录是运行中和结束后的唯一读取来源。控制台就在服务进程里，直接经只读 HTTP 读取记录，不通过执行进程的运行中通道取正文。先完成 Codex 的 A、B 两项逻辑，再完成 C 项展示；随后 DSH、ZCode、Claude Code 各接自己的投影器和夹具。

## 一、现状与唯一来源

Codex Worker 的原生线程在用户自己的存储里，微任务私有区只保存线程绑定；这与 ADR-024 第 5 条“已结束的回合读私有区里的会话记录”的写法不同。依据为 `docs/reference/codex.md:3-5,13` 和 `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:680-710`：现有 `thread/read({threadId,includeTurns:true})` 只用于续接前核对末回合身份与状态。会话视图不调用它读历史，也不打开用户原生目录；由执行时已经收到的事件生成私有展示记录，结束后继续读取同一份。旧执行没有这份记录就显示“未记录”，不回填、不另找原生记录补齐。

Codex 已有接入点是 `buddy/harnesses/codex/protocol.py:103-124` 的通知分派、`native_run.py:1144-1151` 的事件链，以及 `:1274-1317` 的 Worker 回合：先从原生回执取得 thread/turn 身份，再绑定 carrier、逐条 pump。内容投影器挂在该事件链里，使用现有 root thread/turn 关联，不另起原生进程，也不改变活动、结算、停进程或 outcome 的判定。只有属于本次执行、由原生身份明确关联的内容才进入记录，不能把别的用户线程或别的 attempt 混进来。

当前已知通知表见 `native_run.py:191-198`：`item/started`、`item/updated`、`item/completed`，消息增量 `item/agentMessage/delta`，推理的 `item/reasoning/summaryTextDelta`、`item/reasoning/textDelta`、`item/reasoning/summaryPartAdded`，以及 turn、diff、raw response、线程、用量、限额等通知。`protocol.py:314-359` 目前只取活动、用量和最终消息；`codex/tool_evidence.py:26-45` 识别 userMessage、agentMessage、reasoning、plan、命令、文件改动、MCP、动态工具和子代理活动等 typed item，尚无会话正文记录器。

`rawResponse` 通知不写会话记录，账户、限额、用量等通知也不写。内容只从 typed item 及其增量投影，避免同一内容经 raw 和 typed 两个 carrier 重复。未知 item 写一个带原生类型名的标记，有可读文本就附文本，不转储整条原始通知；它仍用下面六种事件中的消息片段承载，并以小型 `_meta` 标出是原生标记，不能伪装成模型说的话。原生只给子代理活动标记时就显示标记，不能为此读取另一个原生线程。

已有离线材料可用于第一批：`tests/python/buddy/harnesses/codex/fixtures/mock_codex.py:220-229` 是续接读回的 mock，`:281-286` 及其后是回合事件；`fixtures/protocol_items.py:1-37` 标明 Codex CLI 0.162.1 的协议样例，含计划文本、工具输出及未知类型。它们可以扩展为会话投影夹具，但不能证明真实环境的全部正文覆盖。原生计划步骤、命令输出增量、子代理正文和推理文本的具体字段若现有样本没有，就在实现记录中明确未核实，优先补已有脱敏记录做夹具，不发新付费调用，也不把缺样本设成整项开工的门槛。

## 二、记录格式与 Codex 投影

文件位置由 `private_dirs.attempt_root(state,harness,taskId,attemptId)` 唯一确定，固定为其下的 `session.jsonl`，不由浏览器或模型提供路径；私有根现有计算见 `src/hey_my_buddy/private_dirs.py:107-120`。第一行固定为 `{"protocol":"acp","protocolVersion":1,"projectionVersion":1}`，后面每行都是一条 `{sessionId,update}`，以换行结束；`sessionId` 使用本次 attempt 的稳定标识，原生 item/call 的关联由投影器维护。每次 attempt 独立建文件，不因原生线程续接而混写前一执行的记录。

统一事件固定为 ACP v1，只允许 `user_message_chunk`、`agent_message_chunk`、`agent_thought_chunk`、`tool_call`、`tool_call_update`、`plan` 六种 `update.sessionUpdate`；词汇与现有 `buddy/harnesses/dsh/protocol.py:44-50` 一致，不混入 v2 的 `agent_message`。在 `protocol/session_content.py` 新增 pydantic 模型，定义格式行、通知包络及按 sessionUpdate 区分的六种 update；复用项目已有 pydantic 基础与严格 JSON 解码方式（`protocol/internal_models.py:1-14`），不自行造一个完整 ACP SDK，不增加官方 schema 快照这道前置门槛。模型覆盖实际使用的 content、工具和计划字段，不给正文附加长度上限。

消息：原生 `userMessage` 的内容投成 `user_message_chunk.content`，实际发送的用户输入从现有发送接入点记录一次，原生再报同一 item 时去重；`agentMessage.text` 和消息 delta 投成 `agent_message_chunk.content`。文本使用 `{type:"text",text}`，原生提供的其他可表达内容块按实际字段保留；不为了显示图片去开放任意本机文件读取。原生 item ID 与消息 phase 可放在有明确字段的小型 `_meta` 中，供展示区分消息与标记，不带会话源路径。

推理：reasoning 的 summary 文本和原生实际给出的 text 投成 `agent_thought_chunk`，以元数据区分摘要和文本。`summaryPartAdded` 确定段落，summary/text delta 提供内容；不解密、不推测隐藏推理。Codex CLI 的具体覆盖以已有版本样本为据，界面说明“仅显示 Codex 提供的推理摘要或文本，不含未提供的内部推理 / Shows reasoning summaries or text supplied by Codex; internal reasoning is not provided”。不能把摘要称为完整推理。

工具：typed item 的原生 ID 成为 `toolCallId`，工具名或原生命令标题成为 `title`，开始发 `tool_call`，状态、输入输出或文件改动发生变化时发 `tool_call_update`。参数和结果保留在 `rawInput/rawOutput`，命令输出与差异放入 ACP `content`；`locations` 只表达原生内容涉及的位置。状态只使用 ACP v1 的 `pending/in_progress/completed/failed`，原生取消或中断的事实放在可读说明中，不新增 ACP 状态；某种原生状态映射尚无样本时明确记录缺项，不猜已完成。

工具 `kind` 共用 ADR-021 L5 的 `src/hey_my_buddy/protocol/tool_evidence.py:54-79,124-175`：投影器用原生身份、调用 ID、工具名或类型及 start/end 构成事实，调用 `normalize_tool_event`，把返回的 category 用作 kind；不在会话读取器或前端再写类别表。原生工具识别复用 `codex/tool_evidence.py:26-45,114-153` 的 typed item 词汇与身份规则，必要时抽出小的纯函数并替换原调用。L5 摘要有 128 条上限且丢弃正文（`protocol/tool_evidence.py:28-31,140-148`），不能把那份证据包当会话来源，会话记录也不继承其上限。

计划：只有原生确实给出结构化步骤时才投为 `plan.entries`，内容、状态、优先级来自原生字段；不从散文推断进度。已有 `fixtures/protocol_items.py:7` 的 typed plan 只带 text，所以把这份计划文本保留为消息内容，不能制造步骤。`turn/diff/updated` 能明确关联工具 item 时附到该调用，只有独立可读 diff 时以带原生类型说明的消息内容保留；不能猜测属于哪次 edit。当前已知通知表不含专用计划步骤通知，具体补充接入以离线样本为据。

同一 item 连续的文本片段先合并，再按一行写入，减少每个 token 各占一行；合并限于一次事件队列排空中连续属于同一 item、同一内容段的片段，排空或切换 item 时就 flush，不能等 item 全部完成才让运行中的文字可见。已写出的行不改写；之后的 delta 继续追加新行。投影器按 item/内容段维护已写位置，completed item 只补还没写出的尾部；只有 completed、没有 delta 的 item 仍写全量一次。工具输出也用同一去重原则，不能把累计输出与其增量各显示一遍。回合正常结束或控制器结束时 flush 已收片段，异常终止留下的完整行照样可读。

记录从回合开始即写，先完成格式行，再写内容行；及时 flush 到文件，供 HTTP 读取，不做 fsync。这是展示记录，不是结算证据，不承担掉电后完整恢复承诺；不能拿它代替原生完成、durable receipt 或实际停止证据。写入使用已有私有目录和安全文件打开边界，I/O 错误按现有错误传播处理，不吞异常、不切到第二种存储。JSONL 不设置总长度、总行数、总时长上限，不保存另一份原始通知日志作为会话回退。

## 三、服务直接读取与分页

控制台与 service 同进程，依据 `src/hey_my_buddy/blackboard/service/daemon.py:21,137-138`；HTTP handler 可以直接读上述文件。新增只读 `GET /api/tasks/{taskId}/attempts/{attemptId}/session`，经过现有私有 URL、Host/Origin 和登录校验，沿用 `console/server.py:652-671,738-767` 的读取入口与认证边界，响应为 `Cache-Control:no-store`。服务用数据库核对 task、attempt、harness 和所属宏任务，再用固定规则定位文件，不接受任意 thread ID 或路径，不在数据库事务中等文件读取。

参数是互斥的 `before=<字节偏移>` 或 `after=<字节偏移>`，以及 `limit`（默认 100 行，最大 256 行）。都省略时返回最新一页；before 返回该偏移之前最近的一页，after 返回该偏移之后的一页。游标使用十进制字节偏移，不能按字符偏移计算；在当前文件读取边界验证一次，要求不超过文件长度，且前一个字节是换行，事件区游标不得落入格式行内部。第一页的有效起点是格式行结束后的字节偏移；同一个合法偏移用于 before 时不含其后的行，用于 after 时从该偏移处的下一条记录开始读取。

每次读取固定本次可见的文件长度，只读到这个范围内最后一个换行为止；未完成的尾行留给下一次 after 读取，不把它当成坏记录。向前或向后按完整行取页，页内始终从旧到新排列，不为分页另建索引、临时页文件或数据库记录。返回的 before 是本页首事件的起始偏移，after 是末事件结束换行后的偏移，hasEarlier 表示 before 之前是否还有事件；空页沿用请求的有效位置，只有格式行时 before 与 after 都为格式行结束位置。反复 before 必须能到第一条事件；反复 after 直到 events 为空即排空本次已有内容。

成功回复只有 `{state:"running"|"ended",omitted:[…],events:[{sessionId,update},…],before,after,hasEarlier}`；状态从权威 attempt 状态得出，文件是否增长不能证明回合结束，端点断连也不证明停止。每条完整行在读取边界经 `protocol/` 的 pydantic 模型校验一次，格式行校验版本；非法 JSON、版本或字段形状明确失败，不跳过坏行，不重试另一种读法。HTTP 读取不受 C-Two live 的 64 KiB 帧限制，单页按完整事件数控制；一行很长时完整返回该行，不截断、不分片，没有整段会话的总量上限。

未接入投影器的 harness 返回“尚未接入”，已接入但文件不存在的旧执行返回“未记录”，沿用现有 HTTP 错误包络及明确原因码，不再扩充成功回复的 state。读取开始先检查宏任务删除标记，已经开始删除就回答“已删除”；若读取途中文件消失，也回答“已删除”，不加读取租约或另一套删除锁。文件、游标和异常处理都不返回会话源、私有根或凭据路径；原生消息、命令、工具参数、diff 与 locations 里的路径作为内容照原样保留，不把它们变成文件读取入口。

运行中与结束后都读取同一文件和同一偏移，不切换来源、不设 epoch，也不增加 C-Two 方法。面板打开且页面可见时每 3 秒用 after 轮询，取到一满页就继续排空已有页，取到不足一页或空页后回到正常节奏；关闭面板、切换 attempt 或隐藏页面时取消请求并停止轮询。复用 `apps/console/src/task-detail/use-task-detail.ts:29-33,60-71` 的可见性与 AbortController 方式。内容不并入 `console/reads.py:23-55` 的轮询摘要、任务结果、Router 输入或模型维护输入。

## 四、能力、回收、删除与版本

每个 harness 包里只放一张静态 omitted 原因码表，随成功读取回复返回；“是否已接入”由是否注册投影器推出，不另存 live/history/source 等多字段能力，不改 `buddy/harnesses/live.py` 或 `blackboard/service/harness_health.py`。注册表只提供按 harness 取得投影器与 omitted 的入口；缺投影器就明确尚未接入，不将“产品还没接入”“旧执行没记录”“harness 没给这类内容”混成一句没有消息。omitted 的每个原因都需对应明确的原生缺项依据或实现观察，不能把未经核实的全量能力写成已提供。

控制台把 omitted 原因码译成一行中英文说明，例如 Codex 的未提供内部推理、ZCode 核实后确实没有的推理内容；没有缺项时不补一句猜测。未知 item 已留类型标记，原生没有给出其结构化展示内容时可用统一的未展开类型说明；不复制原始通知去补齐它。CLI 的原生行为结论注明已有样本对应的形式和版本，Codex 的协议基线为现有夹具注明的 CLI 0.162.1，不据此宣称 Codex App 或其他版本已实测。

检出回收当前只保留失败 attempt：`blackboard/tasks/workflow.py:5641-5650` 选出非成功完成的执行，`:5802-5809` 只 keep 这些 attempt 目录；`private_dirs.py:142-174` 会递归删掉其余文件。因此成功执行的 session.jsonl 也会被删除。A 项须把每个 attempt 的固定 `session.jsonl` 加入回收 keep 集合，保留现有失败 attempt 的证据规则，成功 attempt 仅为这一个会话文件保留必要父目录，不留下凭据、配置或整个 native root。keep 的文件来自已知 attempt 身份与固定位置，不接受调用方随意指定文件。

宏任务删除继续沿现有私有根删除，无需新删文件协议：`blackboard/tasks/macro_task_deletion.py:367-395` 删除各 harness 的 micro task root，会包含其下所有 session.jsonl；`:558-581` 先标删除，再执行 native session、workspace、private root 等物理阶段。Codex 的原生线程仍在用户存储里，现有 `_delete_sessions`（`:344-350`）及 `buddy/harnesses/codex/native_run.py:1966-1986` 继续经 App Server 的 `thread/delete` 删除已证实线程。本次展示记录不从原生线程取内容，所以无需增加子线程读回或原生目录扫描；原生删除路径和私有根删除一起覆盖本次新增存储，回收检出不调用 thread/delete。

存储检查新增 `session-records` 一类，按固定 session.jsonl 文件列出真实 bytes、所属任务和“随宏任务删除”的原因；从原有 harnesses 父项扣除已计入的会话 bytes，避免重复。当前父项整体统计见 `blackboard/tasks/storage.py:430-444,474-494`。普通 storage apply 不单独删除这些记录；回收检出后仍能列出并读取，宏任务删除后条目消失。Codex 用户原生线程不在这项空间统计内：不遍历用户目录，现有预览也只有数量而没有 bytes（`macro_task_deletion.py:303-308`），界面说明“Codex 原生存储空间未计入 / Codex native storage size is not included”，不能写成 0 bytes。

内容不写数据库、不进备份或导出。新增 session.jsonl 是执行进程写、服务读的跨进程事实格式，按 ADR-031 第 10 条（`docs/decisions/031-one-process-per-execution.md:50-53`）在引入批次移动一次中间号，`CONTRACT_VERSION` 同步到该中间号、末位为 0；C-Two 方法签名完全不变，Host CLI 不增加方法。当前源码为 `protocol/contracts.py:17` 的 0.46.0，实施时以集成版本为基准升一次，更新项目版本、锁文件与 `tests/python/protocol/contract_signatures.json` 的版本，签名摘要应保持不变。数据库不变，升级基点已由 Host 移到 0.46.0，本计划不安排数据库升级，也不重解释历史记录。

建议 Host 修改 ADR-024 第 5 条：去掉“经执行进程的运行中通道给出”，改为“执行进程把投影后的事件追加写进这次执行私有区的会话记录，运行中与结束后都读这一份”；背景第 13 行改正“各 harness 都把原生记录写在私有区”的概括，写明 Codex 线程在用户存储、Claude Code 当前 CLI 调用不持久化原生会话。把“响应中不出现文件路径”限定为会话源与私有存储定位路径，原生内容里的路径照原样保留；影响中四个历史读取器改为四个原生事件投影器。`docs/reference/harness-integration.md:119` 也应同步这条唯一来源；上述 ADR 修改由 Host 做，本次不越范围改写。

## 五、控制台展示

入口在每个执行回合的“查看会话 / View session”和时间轴的执行片段。（2026-10-19：C 实现成弹窗，开发者试用后改为详情下方的“概述 / 会话”标签页，见 ADR-024 第 7 条的补记；下面关于入口与导航的写法按标签页理解。）`apps/console/src/task-detail/TurnPane.tsx:20-23,100-123` 已按 span.attemptId 取回合，尚不读会话；应直接携带该 attemptId，失败或未接受的执行也能查看，不能默认跳最新执行。时间轴已有 `timeline/TaskWork.tsx:33,149` 的 `onOpen(runId,key)`，详情在 `task-detail/TaskDetail.tsx:79-95` 按片段选择步骤，沿用这条导航，增加打开会话的选择状态，不另建时间轴数据。刷新、前进后退和从会话返回原执行片段要保留选择与焦点。

当前控制台已用 React 19、Tailwind 4、Radix 和 Lucide（`apps/console/package.json:16-41`），Vite 已接 Tailwind、构建到包内 assets（`vite.config.ts:1-14`），`src/index.css:2-4` 导入 Tailwind、动画与项目 tokens。AI Elements 组件尚未在 src 中，也不在依赖表里；已有 ui 基础不等于会话组件已经接入。按 ADR-024 第 6 条先试用 AI Elements 的 message、reasoning、tool、code block、terminal、plan 等实际需要的展示组件，复用已有 tokens/ui，实施 C 项时固定源码来源、许可证和依赖锁，不加聊天输入或模型运行时。

会话视图用 React.lazy/dynamic import 按需加载，代码高亮在展开代码块时按需加载语言与高亮器。体积基线为本次检出的已跟踪产物 `src/hey_my_buddy/console/assets/assets/index-BkBm67PG.js:1` 860,295 字节、`index-By9rw8hx.css:1` 71,209 字节；这是既有产物的 wc 字节数，不是本次新构建。C 项在相同 Node 与锁文件条件下记录构建前后入口 JS、会话异步 chunk、高亮 chunk、CSS 和总量的 raw/gzip 差值，浏览器验证未打开会话时不会加载它们。现在不能报出尚未引入组件的精确增量体积。

主体只读，显示原生正文、实际给出的推理、工具输入输出、文件改动、命令输出和计划，不设会话长度上限。长块可以折叠，展开与复制仍保留全文；Markdown 和工具输出用成熟展示组件，遵循其安全渲染方式，不执行原始 HTML、脚本链接或本机文件协议。标题显示执行、状态和一行 omitted 说明，颜色、层级、间距沿用 ADR-029，避免叠套圆角容器。先直接渲染，用一个超长会话预览夹具量一次滚动与追加；只有实测确实需要时再引入成熟虚拟列表库，记录其收益和构建代价，不先造滚动框架。

首次进入显示最近一页，向上加载时锚住视口，反复加载能到开头；人在底部时随新事件滚动，离开底部后提示“有新内容 / New content”，点击才回到底部。切换 attempt 要取消旧请求，旧回复不能写进新面板。中英文使用现有 `src/i18n/zh/taskDetail.ts`、`en/taskDetail.ts` 和设置命名空间，注册依据 `src/i18n/index.ts:3-29`；原生会话内容不翻译。至少覆盖“加载更早内容 / Load earlier content”“已到开头 / Start of session”“未记录此执行的会话 / Session was not recorded”“此 harness 尚未接入会话视图 / Session view is not implemented for this harness”“已删除 / Deleted”，设置数据流向补充会话可能含命令输入输出、仅在本机显示、不进备份导出。

## 六、其余三个 harness

DSH 已经在 ACP 接入里逐条消费消息、推理、工具和计划，见 `buddy/harnesses/dsh/protocol.py:269-294`；原生会话在私有区，存储根与记录事实接入见 `dsh/native_run.py:963-966,1060-1078,1118-1122`，既有 `dsh/session_records.py:35-44,49-101` 读 JSONL/zstd 供摘要。后续只在已有 ACP 事件接入点挂投影器、写同一 attempt session.jsonl，不拿摘要读取器或原生 JSONL 建第二条会话读法；原生摘要的大小与步骤上限不会变成展示记录上限。工具 kind 继续来自 normalize_tool_event，原生完整推理是否出现在事件流里用已有版本化脱敏样本核对。

ZCode 当前把 `storage/`、`sessions.sqlite` 和调用日志放在私有区，见 `buddy/harnesses/zcode/native_run.py:723-738`。后续从既有原生事件接入点投影到同一记录，核对事件流实际给出的推理、工具输入输出和计划；若没有全部推理，写进它包内的 omitted 原因码表。不读私有数据库或日志去补事件流缺项，不增加第二种读取器；原生存储的续接与清理仍由它现有逻辑负责，session.jsonl 随统一 keep 和宏任务删除规则处理。

Claude Code 的无持久原生会话是当前 CLI 调用方式的事实，不代表其他形态的 Claude 没有历史功能。`buddy/harnesses/claude/config.py:112-113,136-148` 使用 stream-json 与 `--no-session-persistence`，`claude/native_run.py:1322-1327` 声明不持久化也不原生续接；`claude/protocol.py:90-102` 从 stdout 解码入队，当前没有完整留存 stdout。后续在这条已有接入点投影并写 attempt session.jsonl，已结束回合就读这份记录；保留 --no-session-persistence，不改用户配置，不读用户原生目录。已有历史执行未保存就显示未记录，不能用 stderr、最终 outcome 或活动摘要补正文；token 片段或完整消息按原生实际粒度记录，不为显示效果开启额外 native 开关。

## 七、微任务、范围与检查

下述范围用于后续委派，本次仍只写本文。实现检查都经仓库 checks runner，使用私有状态根，清除继承的 `HMB_STATE_DIR`、`HMB_RUNTIME_ROOT`、`HMB_RUNTIME`、`HMB_RUNTIME_IDENTITY`、`HMB_AGENT_CREDENTIAL_FILE`、`HMB_AGENT_CREDENTIAL_FD`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`；不触及日常环境，不调用真实 harness 或模型。夹具优先复用已有 mock_codex、protocol_items 和脱敏记录，不增加付费样本或 ACP schema 前置任务。新增测试文件放在已有包目录；每项交付 diff、检查终态与明确的未核实项，Host 最终审查。

A：Codex 投影器、会话记录、事件模型与回收 keep。写入范围为 `buddy/harnesses/codex/` 的投影器及接入、最小共享记录写入帮助模块、`protocol/session_content.py`（新增）、必要的 L5 纯函数抽取、`private_dirs.py`、`blackboard/tasks/workflow.py` 的 keep 集合，以及相应测试和夹具。只做单份 session.jsonl，无 fsync；校验六种事件、连续片段合并、delta/completed 去重、只收到 completed、超长单行、多字节正文、尾行未完成、未知 item 标记、排除 rawResponse/账户/用量、成功执行与失败执行在回收后都留记录。已有 `tests/python/buddy/harnesses/codex/fixtures/mock_codex.py`、`protocol_items.py` 扩展为模型无关场景；新增 `tests/python/buddy/harnesses/codex/test_session_content.py` 与 `tests/python/protocol/test_session_content.py`。检查：`uv run --frozen python -m hey_my_buddy.cli.checks tests/python/buddy/harnesses/codex/test_session_content.py tests/python/buddy/harnesses/codex/test_native_run.py tests/python/buddy/harnesses/codex/test_codex_tool_evidence.py tests/python/protocol/test_session_content.py tests/python/protocol/test_tool_evidence.py tests/python/blackboard/tasks/test_workspace_submission_cleanup.py`。

B：只读 HTTP、空间项、omitted 表、版本和 reference。依赖 A。写入范围为新增 `console/session_content.py` 及 `console/server.py` 的 HTTP 接入、必要的 task/attempt 身份读取接缝、`blackboard/tasks/storage.py`、各 harness 包内静态 omitted 表及最小投影器注册入口、项目版本/锁文件、`protocol/contracts.py` 的版本常量、`tests/python/protocol/contract_signatures.json` 的版本、拥有这些行为的 `docs/reference/codex.md`、`operations.md`、`harness-integration.md`。不动 live.py、harness_health.py 或 C-Two 方法。新增 `tests/python/console/test_console_session_content.py`；覆盖前后翻页到开头、偏移非行边界/超过文件长度拒绝、只读完整行、运行到结束继续用原偏移、旧执行未记录/未接入、认证与 no-store、删除标记/读取中文件消失、源路径不泄露、内容路径保留、session bytes 从父项扣除。检查：`uv run --frozen python -m hey_my_buddy.cli.checks tests/python/console/test_console_session_content.py tests/python/console/test_console_access.py tests/python/console/test_console_reads_http.py tests/python/blackboard/tasks/test_storage.py tests/python/blackboard/tasks/test_macro_task_deletion_files.py tests/python/buddy/harnesses/codex/test_delete_sessions.py tests/python/protocol/test_contract_version.py`。A、B 作为一批集成，在 B 同步移动中间号，未全部接受前不安装，中间不运行新执行去写版本未更新的日常记录。

C：控制台会话视图。依赖 A、B，逻辑验收后才开始展示。写入范围为 `apps/console/src/session-view/`（新增）、AI Elements 展示源码和必要 ui 接缝、`task-detail/`、时间轴执行片段导航、前端 API/parser、中英文文案、设置空间/数据流向、package/lock、超长 preview 夹具、浏览器检查和最终构建产物 `src/hey_my_buddy/console/assets/`；需要时只调整模型无关 Python preview 夹具。新增 `apps/console/browser/session-view.check.ts`，覆盖 TurnPane 与旧执行片段入口、返回焦点/刷新前后退、英文/中文、窄屏/宽屏、向上分页锚定、离底追加、关闭取消、长块全量内容和 omitted/未记录/已删除。先直接渲染并记录一次超长夹具测量，确需虚拟列表才引库。最终源码必须运行 `npm --prefix apps/console test` 与 `npm --prefix apps/console run test:browser`，后者含 build；另运行 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/console/test_console_preview.py tests/python/console/test_console_session_content.py`。记录构建前后 raw/gzip 体积和会话/高亮按需加载证据；不以静态截图代替真实入口和分页交互检查。

D：DSH 投影器与夹具。依赖 C 的 Codex 整批接受。写入范围只为 `buddy/harnesses/dsh/` 的投影器、omitted 表、现有注册接缝，以及对应 DSH 测试和脱敏夹具；使用同一记录写入器、HTTP 和页面，不加原生历史读取。检查：`uv run --frozen python -m hey_my_buddy.cli.checks tests/python/buddy/harnesses/dsh tests/python/console/test_console_session_content.py`，核对其原生 ACP 事件到六种记录事件、工具类别、推理实际覆盖和 completed 去重。

E：ZCode 投影器与夹具。接在 D 后，写入范围只为 `buddy/harnesses/zcode/` 的投影器、omitted 表、现有注册接缝及对应测试/脱敏夹具。核对事件流有没有全部推理，没有的直接写 omitted，不引入数据库/日志读取。检查：`uv run --frozen python -m hey_my_buddy.cli.checks tests/python/buddy/harnesses/zcode tests/python/console/test_console_session_content.py`，用流式消息、工具调用/结果和推理缺项夹具验收同一接口。

F：Claude Code 投影器与夹具。接在 E 后，写入范围只为 `buddy/harnesses/claude/` 的 stream-json 投影器、omitted 表、现有注册接缝与对应测试/脱敏夹具；使用统一 session.jsonl，不改启动持久化策略。检查：`uv run --frozen python -m hey_my_buddy.cli.checks tests/python/buddy/harnesses/claude tests/python/console/test_console_session_content.py`，覆盖新执行可在结束后读同一份记录、旧执行未记录、完整 assistant frame 与 delta 的实际粒度、工具结果与未提供推理。D、E、F 不重写共享协议或页面；若发现共享缺陷需 Host 调整范围后修因，不在单家投影器加回退。

Codex 整批由 Host 核对“打开运行中会话、追加、结束继续读、回收检出仍可读、删除宏任务后已删除”的模型无关完整链路，并审查对应 reference 和 ADR 建议。A/B 涉及回收、删除和跨进程格式，集成最终源运行 `uv run --frozen python -m hey_my_buddy.cli.checks`；C 的前端全量与浏览器检查在最终前端无后续改动时不重复。安装、原生实测和平台接受独立记录，不能将离线夹具通过描述为真实 CLI 或 Windows 已接受；本计划不授权安装、日常服务重启或新增模型调用。

## 八、本次验证边界

本次只改本文，未联网、未调用模型或真实 harness、未打开用户原生目录，也未构建前端。检查为 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/cli/test_repository_hygiene.py`，运行时清除继承的运行时与代理凭据变量，设置 `UV_OFFLINE=1`；实际结果见本次 outcome。此检查只验证仓库卫生，不表示会话功能已实现或验收。

## 九、第 3 版：每次执行一个记录文件，放在分组目录里（2026-10-19）

**为什么改。** 开发者问能否把记录改成宏任务目录里的 SQLite 库，以便比逐个文件更好管理。Host 起初提的是每个宏任务一个库，开发者指出同一宏任务里并行的几次执行共用一把写锁，等待就得设上限，满负载（比如几十个小时的水动力模拟）下这个上限会让写入失败；按原来的规则，写入失败会判整个回合失败，停掉这次执行的进程组，连 Worker 起的模拟一起。于是改成每次执行一个库、写失败只记成事实。微任务 G 实现时发现，标准库的 `sqlite3` 只能按文件名打开、会跟随链接：先用受保护的打开核对、再让 SQLite 按名字重开，中间换成链接就会写到、读到分组之外（G 用连接钩子复现，Host 另行复现）。SQLite 自己的“不跟随链接”选项能拒绝已有的链接（Host 用系统的 SQLite 3.54.0 核对过文件名、父目录与 `-wal`、`-shm` 四种情形），但标准库不提供它，第三方封装 apsw 提供，可它是 SQLite 沿路径的检查，抵不抵得住检查与打开之间的调包没有核实，还会在运行时里多一套 SQLite 与一个要编译的依赖。开发者听过 Host 的意见后定：先用 JSONL，实践中发现问题再议。

1. **每次执行一个文件，放在所属分组的目录里。** 分组就是控制台读取时已经用的那个：宏任务，或者不属于宏任务的独立微任务。目录是状态根下的 `sessions/macro-tasks/<宏任务>/` 与 `sessions/micro-tasks/<微任务>/`，文件是其中的 `<attempt>.jsonl`，格式照第二节（首行格式行，此后每行一条 ACP 通知，追加与 `_meta.replace` 的替换不变），读写都经现有的受保护打开（逐级固定父目录、不跟随链接）。Router 的路由任务、协助任务与 Lead 名下的微任务归它们最外层所有者的分组；控制台、记录位置与删除用同一个分组定义（G 第三回合按根因统一）。服务在认领执行时算出文件的位置，随这次执行的控制信息交给执行进程。只有这次执行的执行进程写它，执行之间没有共用的东西。
2. **不属于任何分组的执行不记录。** Host 单独请 Router 选一次 buddy（`selection_request`）时没有分组：控制台没有它的入口，也没有能带走它的删除，所以不写会话记录，服务不给位置。
3. **记录写失败只记成事实。** 第一次写入失败时，这次执行停止记录，原因（`session-record-write-failed`）随运行结果交回，回合照常进行，结果、停止证据与结算都不受影响。读取的回复多一项 `stopped`：有这项事实时是有结构的原因，否则为 null。这是 ADR-030 第 7 条“处理要相称”的应用。
4. **崩溃留下的半行。** 读取照旧只读到最后一个换行。同一个 attempt 再次运行（启动重试、有序收尾回合）打开文件时，末尾没有换行的那一段是上一次写到一半的残行，先截回到最后一个换行再追加；文件为空时先写格式行。
5. **分页。** 游标照旧是字节偏移，控制台把它当作不透明的值，不用改。
6. **回收、存储与删除。** 记录不在微任务的私有目录里了：回收不再为它保留文件；存储检查的 `session-records` 一类按分组目录列出占用，只能随宏任务删除；原因 `session-records-retained` 删除（连同控制台的文字）。删除宏任务时，连同这个分组的会话目录一起删，“已经没有了”算作这一项已完成。删除路径改动，按规矩另请一次独立审查。

**微任务 G（会话视图 G：每次执行一个库，第二回合起按本节改为 JSONL）。** 位置、控制信息、写入方、四个 harness 接线处的失败处理、读取、回收、存储、删除、原因码；控制台只删去 `session-records-retained` 的文字并重建资源。检查：受影响的 Python 测试（四个 harness、控制台、回收、存储、删除、原因码、服务恢复），`npm --prefix apps/console run typecheck`、`npm --prefix apps/console test` 与重建资源。版本仍是 0.47.0：它没有安装，日常看板里没有会话记录，不需要升级。
