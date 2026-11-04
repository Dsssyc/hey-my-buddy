# hey-my-buddy 在现有多代理编码系统中的位置：开发者三项主张的核对

2026-11-04，整合 Host 在 Claude Code 里派出的一个调研子代理，应开发者的要求。只读公开的文档、公告与 README；没有安装或运行任何被比较的工具，没有读任何项目的源码，没有做需要模型调用的实验。

## 问题

与 2026 年 11 月初公开可见的多代理编码系统相比，hey-my-buddy 不同在哪里；开发者列的三项亮点（产品形态、设计理念、垂直领域的积累）对照一手资料站不站得住，哪里要收窄，要靠什么测量来支撑。服务于对外介绍本项目时的措辞（README 等），没有对应的 ADR，取舍待开发者定。

第二项里的交叉核对，开发者在调研中途补充了含义：微任务交回时，Host 把三样东西互相对照，不单信其中任何一样：微任务的任务书连同问询的内容、Worker 或 Lead 自己的报告、封存。理由是任务书可能因 Host 漏写而偏，报告可能自夸，封存可能偏离任务书。所以评判别家时，看编排的一方是否同时持有这三样，而不是只有一个审查代理或一次测试。

## 看了什么

都是 2026-11-04 读的。多数页面经网页摘录工具读取：工具按提问用一个小模型作摘要，少数页面原样返回全文；摘要可能漏掉或改写细节，逐字的措辞以原页为准。下面每项写名称、种类、版本或日期，以及读到哪里。

- **DeepSeek。** 研究原型仓库（在 DeepSeek 官方组织名下）的 README 全文，与其中编号 007 的架构说明全文，都没有版本与日期；编号 000–006、008 的其余说明与安装说明没有读。设计文章《Super-agent 设计探索：迈向更自主的 Agent 系统》的公众号原文没有读到：按标题搜索、限定公众号域名搜索与直接抓取都没有拿到原文，只读了 IT之家（2026-10-31）、爱范儿与虎嗅（2026-11-01 转载的一篇评论）三篇报道，三篇都没有给原文链接。DeepSeek Harness（下称 DSH）的官网介绍页、开发者文档的导航、参考文档首页与“子代理”子系统页（标为技术预览，无版本号），发布页上 0.2.1-alpha.3 至 0.2.2-alpha.3 的说明（只标月日），npm 上 `@deepseek-ai/dsh-subagent-claude-code` 的元数据（README 没有随元数据返回）。Codex 子代理插件包的 npm 网页拒绝访问，没有读到；“智能体团队”的单独说明没有读。
- **Anthropic。** Claude Code 文档的《Create custom subagents》（前十万字经摘要，其余读了原文；页内提到的最新版本为 2.1.314）、《Orchestrate teams of Claude Code sessions》（全文，页内最新 2.1.309）、agent view 一页（前十万字的摘要）、《Use Claude Code in the cloud》（全文）、《Let Claude coordinate ongoing work with Projects》（全文存下后按关键词检索），以及 Agent SDK 的《Subagents in the SDK》（全文）。都没有发布日期。文档不分形态的地方，下文也不分；只适用于某一形态的写明。
- **OpenAI。** Codex 文档的 Subagents 页（两次摘要，第二次要求逐字引述）、Cloud 页与 Codex environments 页；Worktrees 页只看了搜索摘要。OpenAI Agents SDK（Python）文档的 Agent orchestration 与 Models 两页；Handoffs 页只看了搜索摘要。都没有版本号。
- **IDE 与编辑器里的编排。** Roo Code 文档的 Boomerang Tasks 页（标注 2026-05-15 更新）；Kilo Code 文档的 Agent Manager 页；Cline 文档的 Subagents 与 Agent Teams 两页，CLI 参考与 Kanban 只看了搜索摘要；Cursor 文档的 Subagents 与 Cloud Agents 两页；GitHub 文档《About third-party coding agents》，Copilot 云端代理的运行环境与时限只看了搜索摘要，GitHub 博客与更新日志（2026-02-04）只看了搜索结果里的标题与摘要。
- **多代理框架。** LangChain 文档的多代理一页与 LangGraph 的持久化一页；CrewAI 文档的 Hierarchical Process 与 Flows 两页；Google ADK 文档的工作流总览页（原来的多代理一页已改为跳到这里）；Microsoft Agent Framework 的 Learn 与 devblogs 页面三次连接中断，没有读到，只有搜索结果对这些官方页面的摘要；AG2 只有搜索结果对其 Classic 文档的摘要。
- **并排跑多个代理的工具。** AionUi 官方仓库的 README、Claude Squad 仓库的 README、Conductor 官方文档首页，三者都没有版本与日期。
- **会学习的路由器。** LMSYS 的 RouteLLM 博客（2024-07-01），论文本身没有读；Not Diamond 文档《Training a custom router》（2026-05-21 更新）与 Not Diamond Code 的入门页；OpenRouter 文档的 Auto Router 页；Arch-Router 论文（arXiv 2506.16655，2025-06-19）的摘要页。Not Diamond 的“个性化路由”说明没有找到。
- **没有读的线索。** 搜索里出现的第三方项目都没有读，例如让 Claude Code 或 Codex 经 MCP 把事交给 DeepSeek 模型的服务、照 Super-agent 思路做的第三方实现、对原型的非官方评测。
- **本项目。** 当前检出（文档对应 0.60.1）的 README、CONTEXT.md、architecture.md 里的进程拓扑、路由、Host 主导的工作与当前限制几节、skill 正文、ADR-021 的状态一段、usage 与 operations 里各一段，以及本目录 2026-10-01 与 2026-10-02 的两份记录。

## 看到的事实

每条括号里写出处的性质：自述是对方自己的文档、公告或 README，二手是媒体或第三方的转述，推断是本记录的推断。没有另写日期的，都是 2026-11-04 读到的状态。

### DeepSeek：Super-agent 是一篇设计文章加一个研究原型

- 原型的 README 说它只供参考、不是 DeepSeek 的正式产品，以 MIT 许可公开；它是一个面向单个 GitHub 仓库的主动式编码代理，打包成 DSH 的插件包；它听 GitHub，可选再听飞书；有权限的人提出要做的事时，它开一个工作项，并在原来的对话里回复，例如给出一个 PR；每个工作项在自己的代理会话与目录里运行；网页面板列出需要人处理的事；文章里的一部分思路只是以后的方向，没有实现。README 引了设计文章的英文标题，没有给链接。（自述，README，无版本）
- 原型的架构说明：与人对话的主会话用路由与决定的工具承担分派；一个内部引擎是触发、线程、决定、工单与指派的唯一写入者；执行器为每个工单开一个 DSH 会话；执行的会话不能直接问人，也不能给别的会话发消息；主动接活有一个观察模式，只记一笔“本会接下”而不真接，自动模式下要有匹配的规则开着才真的接；状态在一个加了目录锁的 SQLite 文件里，每条命令一个事务，副作用排队并带退避重试，启动时报告并续上被中断的指派，重复的触发按重复回复；部署形态是 DSH 里的插件、本地状态目录与只监听回环的 HTTP 接口；执行只用 DSH 的会话；没有描述测试、评审或批准之类的落地关口，CI 的快照可读，但不是关口；工单随所属线程结束而结束，例如 PR 被合并或关闭。（自述，架构说明，无日期）
- 媒体转述的文章内容：DSH 团队 2026-10-31 在官方公众号发表这篇文章；出发点是一个人大约只能同时跟 3 到 5 个活跃会话；双层结构里常驻的 Router 接收人与各渠道的消息、维护全局上下文并分派，Worker 在相互隔离的会话里并行做事、向 Router 汇报；外部事件先过程序规则再由模型判断，已分派的线程的后续消息直通对应的 Worker；可以在后台先试着复现一个 bug，确认并找到简单修法才出声；另有一个独立的 Watchdog 会话，监控基础设施错误，并判断 Worker 是否真有进展、是否偏离目标；计算与会话状态放在云端，仓库与工具链可以留在用户的机器上，经一个轻量的守护进程接入；合成负载的实验（每轮约 300 条消息、约 20 个真实请求，每种方案 5 轮）里，Super-agent 的完成时间中位数 12.4 分钟、95 分位 15.7 分钟，单会话加子代理、中性提示时 95 分位 29.5 分钟；中性提示的五轮里模型一次也没有调用子代理（只有爱范儿提到）；去掉事件直通后完成时间相近，Router 的决定多约三成，缓存读取的 token 从约 2323 万增到约 3177 万；实验只测调度与协作，不测编码质量；文章列出的未解决问题包括 Router 随任务量扩展、长期记忆、代理之间的通信，以及提示注入、凭据泄漏与跨设备权限等安全问题；DeepSeek 内部有一个基于 DSH 的实例在用，做问题的复现与修复、回应代码评审。（二手，三篇报道，未能对照原文）
- 推断：DeepSeek 没有发布叫“多智能体框架”的产品。官方发布的是一篇设计文章，加一个自己声明不是正式产品的研究原型。

### DSH 自己的子代理：主代理可以把一件事交给本机别家的 harness

- 本地子代理经面向模型的 spawn 与 fork 两种工具建立，有自己的持久会话，工作目录默认沿用父代理的，空闲后再收到消息时从持久记录里冷恢复；外部后端（Codex、Claude Code、ACP 与 DSH SDK）各只提供一次执行，不支持多轮，运行中收不到后续消息，在子代理目录里是只读的叶子；结果自动通知父代理，带最终文字、结构化结果、诊断与状态；这一页没有写 worktree。（自述，“子代理”子系统页，技术预览）
- 发布说明（只标月日，按版本序列推断为 2026 年）：0.2.1-rc.1（10-17）为 headless、SDK 与 ACP 三种 profile 提供可选的 Claude Code 与 Codex 插件包，并加了实验性的 Git worktree 插件，让代理建立并进入单独的检出；0.2.2-alpha.2（10-28）加了实验性的 `subagent_oar` 插件，把独立的任务交给本机的 Codex 或 Claude CLI，并让实验性的变更请求在单独的 Git worktree 里跑会话；0.2.2-alpha.3（11-03）把评审与 worktree 并入这项实验功能。（自述，发布页）
- npm 上的 Claude Code 子代理插件包自称是经官方 Agent SDK 实现的一次性 Claude Code 子代理提供者，元数据显示首个版本约在 2026-08-10 发布。（自述，npm 元数据；日期由元数据推出，是近似值）
- 实验性的智能体团队要显式开启，在可恢复的子代理之上加持久的名册、任务状态与直达的收件箱。（自述，参考文档首页）
- 推断：在所读系统里，DSH 的外部后端最接近本项目的 Worker 来源：主代理自己决定把事交给本机别家的 harness。差别是外部执行只有一次、不能多轮，结果直接回到父代理的对话里，文档里没有固定的产物，也没有验收；Host 一侧只能是 DSH。

### Claude Code 与 Claude Agent SDK

- 子代理：Claude 依各子代理的描述决定何时委派给它，用户也可以点名；`model` 只接受 Claude 的别名与完整模型 ID；可以设 `isolation: worktree` 让子代理在临时的 git worktree 里跑，没有改动时 worktree 自动清掉，有改动时保留什么、交回什么，页面没写；父代理收到的只有子代理的最终报告，交给父代理之前做一次扫描，只中和冒充控制标记的文字，不判断内容、不删不改；默认最多嵌套三层、同时 20 个；fork 模式从 2.1.302 起默认开着，Claude 可以自己开 fork；子代理的对话记录随会话保存，恢复同一个会话就能续，默认 30 天后清理。（自述，《Create custom subagents》）
- Agent SDK：子代理由开发者的代码或文件定义，运行时由 Claude 自己决定何时开、开几个，也可以点名强制；深度、并发与花费都能设上限；文档说 Opus 5 比早先的模型更容易委派，所以用 claude_code 预设的系统提示词、模型为 Opus 5 时，Claude Code 在提示词里加一句，叫它除非被要求，否则不调用 Agent 工具。（自述，《Subagents in the SDK》）推断：Claude Code 的 CLI 用的是同一套预设提示词，Opus 5 上很可能也是被要求才委派；没有核对。
- agent teams：实验功能，默认关；开启后 Claude 可能自己给子代理起名，被起名的就以 teammate 身份启动，用户没要求也可能组成团队；teammate 的个数由 Claude 按任务定，lead 自动把工作拆成任务分派；每个 teammate 是一个独立的 Claude Code 会话，只用 Claude 模型；大家在同一个工作目录里，文档提醒两个 teammate 改同一个文件会互相覆盖；质量关口靠 TeammateIdle、TaskCreated、TaskCompleted 三个 hook；teammate 的计划默认由 Claude Code 直接批准，lead 不审；lead 可能自己动手不等 teammate，也可能提前收工，文档让用户提醒它；只在交互式会话里组队；恢复会话不会带回进程内的 teammate，一个会话一个团队，lead 固定。（自述，《Orchestrate teams of Claude Code sessions》）
- 后台会话（CLI 的 agent view，研究预览）：由用户派出，由单独的监督进程在本机运行，关掉终端仍在跑，关机就停；编辑之前通常进入自己的 worktree；按需要输入、待审、进行中、已完成分组，由人看过再处理。（自述，agent view 页的摘要）
- 云端会话：跑在 Anthropic 管理的隔离 VM 里，或组织自托管的环境里；关上电脑仍在跑；用户从网页、手机、桌面应用或终端开；结果推到分支，在差异视图里由人审，再建 PR；auto-fix 可以盯着 PR 的 CI 与评审意见自动修；环境闲置过期后对话恢复，但当时还在跑的后台工作不恢复。（自述，《Use Claude Code in the cloud》）
- Projects（公开测试，限 Pro、Max 与 Ultra）：一个长期的协调对话，由 Claude 决定用户发来的哪些事成为 thread，并跟踪它开的每个 thread；thread 多是云端会话，各在自己的分支上做事，需要时开 PR，PR 开了之后自动盯 CI 与评审意见；协调对话只看 thread 汇报的内容，不看每一步；合并由用户决定，可以点按钮让 thread 去合并；thread 默认用 Opus，可以另选 Claude 的模型；用户说的“一次开几个”之类的要求被记进项目记忆，但不是硬上限。（自述，《Let Claude coordinate ongoing work with Projects》）

### Codex 与 OpenAI Agents SDK

- 子代理：本地的 Codex 只在用户直接要求，或适用的 AGENTS.md、skill 里的说明要求时才开子代理；ChatGPT 里选 Ultra 这一档时可以主动委派，别的档位要明确要求；拆分、分派、等待与关闭由编排者做；子代理继承父回合当前的沙盒与审批设置，同时改代码可能冲突；编排者等齐结果后给一个汇总的回复，页面没有单独的核对步骤；页面只列 OpenAI 的模型；桌面应用、CLI 与 IDE 扩展里都能看到子代理的活动。（自述，Subagents 页，无版本）
- 运行位置：桌面应用里每开一个对话由用户选 Local、Worktree 或 Cloud，前两种在本机；每个 Cloud 任务有自己的工作区，网页与手机可以开，CLI 也可以从终端开；用户看改动的文件与测试结果，再决定提交或开 PR；云端任务在电脑休眠时继续。（自述，Codex environments 与 Cloud 两页）
- OpenAI Agents SDK：编排有两种，由模型规划（handoff 以工具的形式交给模型选），或由代码定流程；代理与 handoff 由开发者在代码里定义；文档建议做 evals，让代理自我批评，或由评审代理循环把关；独立的代理可以并行；非 OpenAI 的模型经 Any-LLM 与 LiteLLM 两个 beta 适配器接入；隔离与持久化，这两页没写。（自述，Agent orchestration 与 Models 两页）

### IDE 与编辑器里的编排

- Roo Code：Orchestrator 模式用 `new_task` 把工作拆成子任务，每个子任务有自己的对话历史，只有 `attempt_completion` 的摘要回到父任务；默认每个子任务的建立与完成都要用户批准；页面没有核对步骤。（自述，2026-05-15 更新的文档）
- Kilo Code：Agent Manager 是一个控制面板，由用户开多个代理会话，worktree 模式下各有自己的 worktree 与分支，Local 模式共用工作区；对话也能经 `agent_manager` 工具开会话，要用户批准；有完整工具的代理可以把事交给非交互的 task 子代理，orchestrator 代理已弃用；多版本模式在各自的 worktree 里把同一个提示并行跑最多 4 份，可以换模型；用户看差异，把改动应用到本地或在 PR 面板合并；模型用侧边栏同样的提供方与自带的 key。（自述，Agent Manager 页，无日期）
- Cline：子代理只在 Cline CLI 与 Desktop 里有，由 Cline 自己判断一件事值不值得拆，用户也可以在提示里要求；批准 `spawn_agent` 就等于批准子代理的一切；主代理等齐子代理的回复再继续；agent teams 以 `/team` 开始，或在 Cline 认为需要时自己组队，协调者有加队友、派任务、收结果的工具；团队的状态存在本机的数据库里，可以按会话 id 恢复。（自述，两页文档，无日期）CLI 的 `--worktree` 把任务放进单独的 worktree，Kanban 为每个任务建一个临时 worktree。（自述，但只读了搜索摘要）
- Cursor：任务复杂时代理自动开子代理，也可以点名；子代理可以用别家的模型，按第三方用量计费；本地子代理默认共用父代理的检出，要隔离时各用一个 git worktree；`/in-cloud` 让下一个任务作为云端子代理在自己的 VM 与分支上跑；Cloud Agents（原名 Background Agents）由用户从网页、桌面、手机、Slack、GitHub 评论、Linear 或 API 开，每个在自己的 VM 里、在单独的分支上做，交出带截图、录像与日志的 PR，不需要本机在线。（自述，两页文档，提到嵌套子代理从 2.5 起）
- GitHub：第三方编码代理处于公开预览，Claude 的代理基于 Claude Agent SDK，Codex 的代理基于 Codex SDK，可以用已有的 Copilot 订阅驱动，消耗 Actions 分钟与 AI 额度；用户从仓库的 Agents 页、把 issue 指派给代理、在 PR 评论里提到它、手机或 VS Code 开始；做完开 PR 并请用户审，定稿前用 CodeQL、密钥扫描与依赖库查生成的代码，并尝试修掉问题。（自述，《About third-party coding agents》）Copilot 云端代理的环境是每次会话临时建立、用完销毁的 Actions 环境，单次会话最长 59 分钟；2026-02-04 的公告说 Claude 与 Codex 进入公开预览，可以把一个 issue 同时指派给几个代理比较结果。（自述，但只读了搜索摘要）

### 多代理框架

- LangChain 与 LangGraph：模式有子代理（主代理把子代理当工具，自己决定何时、怎样调用）、handoff、router 与自定义工作流；checkpointer 保存线程的状态快照，支持人在环中与出错后续跑，生产环境要用持久的后端。（自述，两页文档，无版本）
- CrewAI：层级流程里管理者代理按角色分配任务，并评估结果是否达标；代理与任务由开发者在代码里定义，默认不让代理委派；Flows 的 `@persist` 保存状态、重启后可续，`@human_feedback` 暂停等人反馈（1.8.0 起）；例子经 LiteLLM 用带提供方前缀的模型名。（自述，两页文档）
- Google ADK：开发者在代码里把代理与确定性节点组合成图式、动态或协作式（一个代理当协调者）的工作流，另有固定的顺序、循环、并行模板与实验性的代理路由（回退、A/B 与自动路由）；导航里列出 Gemini、Claude、OpenAI、Ollama、vLLM 与 LiteLLM。（自述，工作流总览页；页面的版本标注前后不一，没有核对）
- Microsoft Agent Framework：Semantic Kernel 与 AutoGen 的后继；顺序、并发、handoff、群聊与 Magentic 五种编排在 1.0 稳定，都支持检查点、人在环中的批准与长任务的暂停续跑；1.0 的日期各处说法不一，在 2026 年 4 月前后。（自述，但官方页面打不开，只读了搜索摘要）
- AG2：它自己的 Classic 文档说 autogen 包进入维护模式，只收关键与安全更新；Classic 的群聊默认由模型选下一个发言者。（自述，但只读了搜索摘要）

### 并排跑多个代理的工具

- AionUi：团队模式里，Leader 接收用户的指令、拆成子任务，经内置的 Team MCP 服务交给 Teammate；Leader 可以是 Claude Code、Codex、Hermes Agent、Gemini、Snow CLI 或 Aion CLI，Teammate 可以是 Claude Code、Codex、Gemini 等，以及任何支持 stdio 的 ACP 后端；外部代理经 ACP 接入，本机装的 CLI 会被自动发现；所有代理共用同一个文件夹，各有自己的权限对话框；Leader 分派、跟踪并汇总结果，README 没写另外的核对；是桌面应用，数据存在本机的 SQLite 里，WebUI 模式可以部署在服务器上；README 没写是否沿用用户已有的登录，也没写关掉应用后团队是否继续。（自述，README，无版本）
- Claude Squad：终端应用，管理多个 Claude Code、Codex、Gemini、Aider 等本地代理；由用户新建会话；tmux 隔开终端，git worktree 让每个会话在自己的分支上；用户看过差异再提交、推送；有实验性的自动接受。（自述，README，无版本）
- Conductor：在 Mac 上并行跑 Claude Code、Codex、Cursor 与 OpenCode；由用户为每件事建一个隔离的工作区，各有分支、文件、终端、差异与审阅入口；用户看差异、开 PR、合并、归档；没有负责分派的主代理。（自述，文档首页，无版本）

### 会学习的路由器

- RouteLLM：在一强一弱两个模型之间路由以省钱；用 Chatbot Arena 的人类偏好数据离线训练，部分加了 LLM 评判与 MMLU 的数据增强；只用 Arena 数据训练的路由器在 MMLU 上接近随机，作者归因于题目在分布之外，加入该领域的数据后变好。（自述，2024-07-01 的博客）
- Not Diamond：用户用自己的评测数据（输入、各候选的回答与分数）训练路由器，按请求在候选模型之间选；追加数据后手动重训，页面不讲从生产反馈连续学习。（自述，2026-05-21 更新的文档）Not Diamond Code 在本机放一个代理挡在 Claude Code 前面，每个用户回合与子代理回合之前向它的优化服务要模型与推理强度的推荐；服务怎样训练、是否随这个用户的结果调整，页面没写。（自述，入门页，无日期）
- OpenRouter Auto Router：先把提示分到约 30 种细的任务类型，再按全站用户过去 7 天的匿名花费排名选模型；可以限定允许或排除的模型与花费档；不讲从单个用户的结果学习。（自述，文档页，无日期）
- Arch-Router：一个 1.5B 的模型，把查询对到用户自己写的“领域—动作”偏好上再选模型，加新模型不必重训；摘要没写从结果学习。（自述，论文摘要，2025-06-19）

### 本项目（作对照的基线）

- 入口是一个 skill（正文 4095 字节，检查把它卡在约 4 KB 以内）与它自带的启动器；安装还会在本机常驻一个私有服务（黑板），另有可选的私有本地控制台；用户正在用的会话就是 Host；README 说任何能运行这个命令的 agent 都能当 Host，Host 指南有 Codex 与 Claude Code 两份。（本项目文档）
- Worker 跑在 DSH、ZCode、Codex 与 Claude Code 的 CLI 里，用用户自己的登录与套餐；skill 让 Host 在一块工作能并行或更适合别的模型时自己委派，琐碎或还不清楚的自己做；Lead 只在用户明确要求时才有。（本项目文档）
- 每个微任务有固定的输入、自己的检出（顺序的所有用 `existing`，并行写用 `worktree`，两者都不是系统沙盒）、写入范围与权限档位；回合结束时黑板封存检出；Host 重读任务书与之后的输入，把报告与封存对照着查，`accept` 指名确切的封存、校验集成后收回检出，或者打回给同一个 Worker。（本项目文档）
- 关掉终端、断线或服务重启都不会取消工作；断电、重启机器或执行进程被杀之后，执行记为中断，微任务停在失败的边界，带着未验证的部分产物，由 Host 决定是否继续。（本项目文档）
- Router buddy 在合法候选之间按用户的偏好与模型备注选；只有一个候选或 Host 指名时不跑路由计算；按领域树与任务类型积累评价的维护面、从公开资料建模型画像的画像面还没有实现（ADR-021 的状态，2026-10-27 按源码与验收记录重核）。（本项目文档）

### 同一个词的不同意思

- DeepSeek 设计里的 Router 是与人对话、维护全局上下文并分派工作的一层，位置接近本项目的 Host buddy，不是本项目只负责挑 Worker 的 Router buddy；它的 Worker 只跑 DSH 的会话。
- Claude Code agent teams 的 team lead 是主会话本身，位置接近本项目的 Host，不是本项目的 Lead buddy（Lead 只在用户要求时由 Host 授予委派权）。Claude Code 的 Projects 是一个协调对话，不是本项目所说的项目（代码仓库）。
- OpenAI Agents SDK 的 handoff 是把对话交给另一个代理，Codex 的 handoff 是把一个对话在 Local 与 Worktree 之间挪动；两者都不是本项目的换手。

## 对照

格子里只写读到的；“未写”是读到的页面没写，不等于没有。

| 系统 | 谁决定委派、拆多细 | 执行者 | 并行时怎样隔离 | 落地前怎样核对 | 会话结束、进程重启之后 | 本地或托管 | 强加的方法或结构 | 选执行者是否从结果学习 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hey-my-buddy（本检出） | Host 在 skill 的说明下自己决定，用户也可以要求；拆多细由 Host 定 | DSH、ZCode、Codex、Claude Code 的 CLI，用户自己的登录 | 每个微任务自己的检出，加写入范围与权限档位；不是系统沙盒 | Host 对照任务书与问询、报告、黑板做的封存；验收指名封存并校验集成 | 会话结束与服务重启不取消；重启机器记为中断，由 Host 决定 | 本地 | 微任务任务书的格式，与封存、审查、验收或打回的循环 | 还没有；按偏好与备注选 |
| Claude Code 子代理 | Claude 按描述决定，也可点名；SDK 文档说 Opus 5 的预设提示叫它被要求才用（CLI 未核对） | 只有 Claude 模型 | 默认共用工作区；可选临时 worktree | 只回最终报告；只做防注入扫描 | 对话记录随会话保存，恢复会话可续 | 本地，也可在云端会话里 | 子代理定义可有可无 | 无 |
| Claude Code agent teams（实验） | 用户要求，或开启后 Claude 自己组队；lead 拆任务 | Claude Code 会话，Claude 模型 | 共用工作目录 | lead 汇总；hooks 当关口 | 恢复会话不带回进程内的 teammate | 本地 | 开实验开关 | 无 |
| Claude Code 云端会话与 Projects | 云端会话由用户开；Projects 由协调对话决定开哪些 thread | Claude 模型 | 各自的云端 VM 与分支 | 推分支或开 PR，auto-fix 盯 CI，由用户审与合并 | 关电脑继续；环境过期后在跑的后台工作丢失 | 托管 | 项目说明与项目记忆 | 无 |
| Claude Agent SDK | 开发者定义候选，Claude 运行时决定开不开、开几个 | Claude 模型 | 同 Claude Code | 最终消息；可设深度、并发、花费上限 | 按会话恢复，由开发者的程序负责 | 随部署 | 开发者写代码 | 无 |
| Codex 子代理 | 本地版只在用户直接要求，或 AGENTS.md、skill 的说明要求时；ChatGPT 的 Ultra 档可主动 | OpenAI 模型 | 继承沙盒，共用工作区 | 汇总回复，无单独核对 | 未写 | 本地（ChatGPT 里也有托管的） | AGENTS.md 或 skill 可以要求 | 无 |
| Codex 桌面应用与 Cloud | 用户每个对话选 Local、Worktree 或 Cloud | OpenAI 模型 | worktree，或云端各自的工作区 | 用户看改动与测试结果，决定提交或开 PR | 云端任务在电脑休眠时继续 | 本地或托管 | 无 | 无 |
| OpenAI Agents SDK | 开发者定义代理与 handoff，模型运行时选；或由代码决定 | 多家模型（经适配器调 API） | 进程内；未写 | 建议 evals 与评审代理循环 | 未写 | 随部署 | 开发者写代码 | 无 |
| DSH 子代理（技术预览） | 主代理经面向模型的 spawn、fork 工具 | 本地子代理，或一次性的外部后端：Codex、Claude Code、ACP | 默认沿用父目录；worktree 是实验插件 | 结果直接回父代理 | 本地子代理可冷恢复；外部运行是只读叶子 | 本地 | 无 | 无 |
| DeepSeek Super-agent 原型 | 有权限的人或渠道事件开工作项，主会话分派；主动接活有只记录不接的观察模式 | 只有 DSH 会话 | 每个工单自己的会话与目录 | 未写落地关口；以 PR 交付，PR 合并或关闭时工单结束 | SQLite，重启后续上被中断的指派 | 本地插件（文章说云端加本机守护进程，二手） | 一个仓库一个部署，渠道与授权规则 | 无 |
| Roo Code Orchestrator | 选了 Orchestrator 模式后由它拆；默认每个子任务的建立与完成都要用户批准 | 未写 | 各自的对话；工作区未写 | 只回摘要，由用户批准完成 | 可在任务之间切换；存储未写 | 本地 IDE | 要选 Orchestrator 模式 | 无 |
| Kilo Code Agent Manager | 用户开会话；对话经工具开会话要批准 | 侧边栏同样的提供方与自带 key | worktree 模式各自的 worktree 与分支 | 用户看差异，应用到本地或合并 PR | 状态文件与 worktree 留在磁盘 | 本地 IDE | 面板 | 无（多版本模式让用户挑） |
| Cline 子代理与 agent teams | Cline 自己判断要不要拆，也可要求；`/team` 或 Cline 自己组队 | 未写 | 子代理各自的上下文；`--worktree` 与 Kanban 用 worktree | 主代理收回复；未写核对 | 团队存在本机数据库，可按会话 id 恢复 | 本地 CLI 与 Desktop | 无 | 无 |
| Cursor 子代理与 Cloud Agents | 任务复杂时代理自动开子代理；Cloud Agents 由用户开 | 可用别家模型（经 Cursor） | 本地默认共用检出，可选 worktree；云端各自的 VM 与分支 | 云端交出带证据的 PR，由用户审 | 云端不需要本机在线 | 本地与托管 | 无 | 无 |
| GitHub Copilot 与第三方代理（公开预览） | 用户指派 issue，或在 Agents 页、PR 评论里叫 | Copilot、Claude（经 Agent SDK）、Codex（经 Codex SDK），走 Copilot 的订阅 | 每次会话一个临时环境（搜索摘要） | 开 PR 请用户审；生成的代码过安全扫描 | 单次会话最长 59 分钟（搜索摘要） | 托管 | issue 到 PR | 无 |
| LangGraph 与 LangChain | 开发者定义，主代理运行时决定调用哪个子代理 | 多家模型的 API | 进程内 | 人在环中的中断 | checkpointer 持久化、可续 | 随部署 | 开发者写图 | 无 |
| CrewAI | 开发者定义；层级流程由管理者分配 | 多家模型的 API | 进程内 | 管理者评估结果；可等人反馈 | Flows 可持久化续跑 | 随部署 | 开发者写代码 | 无 |
| Google ADK | 开发者组合；协调代理在运行时决定 | Gemini 与别家 | 未写 | 未写 | 未写 | 随部署 | 开发者写代码 | 实验性的代理路由，细节未读 |
| Microsoft Agent Framework（搜索摘要） | 开发者选编排；Magentic 的管理者运行时调整 | 多家 | 进程内 | 人在环中的批准 | 检查点、暂停续跑 | 随部署 | 开发者写代码 | 未读到 |
| AionUi 团队模式 | 用户给 Leader 指令，Leader 拆分分派 | Claude Code、Codex、Gemini 等本机 CLI，经 ACP | 共用同一个文件夹 | Leader 汇总；各代理的权限对话框 | 数据在本机 SQLite；团队是否续跑未写 | 本地桌面应用，WebUI 可上服务器 | 无 | 无 |
| Claude Squad | 用户开会话 | Claude Code、Codex、Gemini、Aider 等本地 CLI | tmux 加 worktree | 用户看差异再提交、推送 | 未写 | 本地终端界面 | 无 | 无 |
| Conductor | 用户建工作区 | Claude Code、Codex、Cursor、OpenCode | 每件事自己的工作区与分支 | 用户看差异、开 PR、合并 | 未写 | 本地 Mac 应用 | 无 | 无 |
| RouteLLM、Not Diamond、OpenRouter Auto Router、Arch-Router | 不委派，只为每个请求选模型 | API 模型；Not Diamond Code 在 Claude Code 里选模型与推理强度 | 不适用 | 不适用 | 不适用 | RouteLLM 是开源库，Not Diamond 与 OpenRouter 是托管服务，Arch-Router 是模型 | 不适用 | RouteLLM 离线用偏好数据；Not Diamond 用用户的评测数据批量重训；OpenRouter 按全站花费与任务类型；Arch-Router 按用户写的领域—动作策略；都没有读到按用户的领域、从任务结果在线学习 |

## 三项主张的核对

判断只到所读文档为止，分成立、部分成立、不成立三档。怎样收窄、要量什么都是建议，用不用待开发者定。

### 主张一：产品形态。中立、轻量：入口是一个 skill，没有独立界面，任何 agent 都能当 Host

判断：部分成立。

- 站得住的部分（推断）：所读系统里，能让别家原生 harness 里的 agent 做事的，都另有一个主界面或宿主：AionUi 与 Conductor 是桌面应用，Claude Squad 是终端界面，GitHub 的第三方代理在 GitHub 网页与它的 Actions 环境里，DSH 的外部后端只能由 DSH 的主代理调用。厂商自带的子代理与团队不需要新界面，但执行者只有自家模型；框架要写代码。本项目让用户留在原来的 agent 里，入口是一个约 4 KB 的 skill 加启动器，Host 可以是 Codex 或 Claude Code，Worker 来自四家 harness。这个组合在所读系统里没有找到第二个。
- 要收窄的地方：一，没有独立界面不等于轻量：安装会在本机常驻一个服务（黑板、SQLite、每次执行一个进程），另有可选的控制台；说成“不另设主界面，后台有一个本地服务”更准确。二，任何 agent 都能当 Host 是 README 的说法，但 Host 指南只有 Codex 与 Claude Code 两份，别的 agent 做 Host 走完一轮的记录，这次在所读文档里没有找到（验收记录没有专门翻）。三，中立在 Worker 一侧只覆盖已接入的四家；AionUi 经 ACP 接任何支持 ACP 的 CLI，这一点上覆盖更宽。
- 要量什么：在 Codex 与 Claude Code 之外至少一个 agent 上，从安装到第一次验收完整走一遍；后台服务空闲时的资源占用；新用户从安装到第一个被验收的微任务要几步、多长时间。

### 主张二：设计理念。在有限的约束下，由 Host 自己决定委派，并且稳定

判断：部分成立。分四点看。

**（一）“少有系统让 agent 自己决定委派，多数让用户来发起。”判断：不成立。**

- 证据：由 agent 自己决定委派的，至少有 Claude Code 的子代理（按描述决定，fork 也可以自开）、开启后的 agent teams（可能自己组队）、Projects 的协调对话（决定什么成为 thread）、Claude Agent SDK（自己决定开不开、开几个）、Cursor 的子代理、Cline 的子代理与团队、DSH 的 spawn 与 fork（工具面向模型）、ChatGPT 的 Ultra 档，以及各框架里由开发者定义候选、模型在运行时挑的 handoff 与子代理。由用户发起的是本地 Codex（直接要求，或 AGENTS.md 与 skill 里的说明要求）、Roo Code（默认每个子任务都要批准）、Kilo Code 的 Agent Manager、云端的 Codex、Cursor Cloud Agents、GitHub 的代理，以及并排工具 Claude Squad 与 Conductor。DeepSeek 的设计走得更远，让渠道里的事件不经人开口就开工（二手；原型里有只记录、不真接的观察模式）。
- 也看到两家厂商在收：Anthropic 说 Opus 5 更容易委派，于是在 claude_code 预设的系统提示词里叫它被要求才用（Agent SDK 文档；CLI 是否一样没有核对）；本地 Codex 只在被要求或被说明要求时才开子代理。
- 收窄：少见的不是“agent 自己决定委派”，而是“agent 自己决定把有界的一块工作交给别家原生 harness 里的 agent，用用户自己的登录，结果留在一份不随会话消失的记录里”。所读系统里只有 DSH 的外部后端接近（一次性，结果回到父代理的对话，没有固定的产物）；AionUi 的 Leader 在用户给了指令之后也会分派给别家的 CLI（共用一个文件夹）。另外，本项目的“自己决定”靠的是 skill 正文里的常驻说明，这正是 Codex 文档说的那条途径（skill 的说明要求委派）；对外说成“Host 在 skill 的说明下自己判断何时委派”，比“自发”更准确。
- 要量什么：Host 未经用户要求就委派的比例，以及这类微任务与用户要求的微任务相比，验收率与打回次数是否相当。黑板现在是否记下是谁提出的，这次没有核对。

**（二）“Host 比用户更会决定委派时拆多细。”判断：不成立。没有证据，是待检验的假设，不宜当事实说。**

- 证据：所读材料里没有一处比较过 agent 与用户谁拆得更好。厂商文档反倒写明 agent 的拆分要人盯：Claude Code 的团队文档让用户在 lead 拆得不够细时叫它拆细，提醒 lead 可能自己动手不等队友，也可能提前收工；Projects 让用户告诉 Claude 一次开几个 thread；Codex 说用户的提示可以规定怎样分。两条线索指向两头：Anthropic 说 Opus 5 更容易委派，所以要收（自述）；据爱范儿转述，DeepSeek 的实验里中性提示的单会话五轮都没有用子代理（二手，未见原文）。拆分随模型与提示而变，偏多偏少都有。
- 收窄：说“Host 能自己定拆多细，用户不必逐件去拆”，是能力的陈述，有依据；说“比用户更好”，是比较，没有依据。
- 要量什么：同类工作分两组，一组由 Host 自己拆，一组由用户指定怎样拆，比较每个微任务的打回次数、验收之后的修正微任务、Host 审查所花的时间、从开始到验收的总时长、用量，以及用户插手的次数。

**（三）“规则少、控制灵活，加上交叉核对，交付质量就稳定。”判断：部分成立。机制在，而且在谁同时持有三样东西这一点上与所读系统不同；稳定没有量过。**

- 按开发者补充的标准看（任务书连同问询、报告、封存，由编排的一方互相对照）：
  - 编排者是 agent 的系统里，编排者收到的是执行者的报告：Claude Code 子代理与 Agent SDK 的最终消息、Codex 的汇总回复、Roo Code 的摘要、Cline 的最终回复、DSH 回给父代理的文字与结构化结果、AionUi 的 Leader 汇总、各框架的最终消息。任务书只在编排者的上下文里；改动在共用的文件夹里，或在仍然可写的 worktree 里，报告之后还会变。所读文档里没有一家写到编排者在接受之前，把任务书、报告与一份不会再变的产物对照。它们提供的检查是别的形状：Claude Code 团队的 hook 关口、Agents SDK 建议的评审代理循环、CrewAI 管理者的结果评估、DeepSeek 文章里判断进展与偏离的 Watchdog（二手）、Claude Code 对报告的防注入扫描、GitHub 的安全扫描。
  - 以 PR 交付的系统里（GitHub 的代理、Codex Cloud、Cursor Cloud Agents、Claude Code 的 Projects，以及 Conductor、Claude Squad、Kilo Code 的差异审阅），三样东西其实都在：任务文字、代理写的 PR 说明、按提交固定的改动；但对照它们的是人，外加 CI 与自动修复。Projects 的协调对话只看 thread 的汇报，PR 由用户审、由用户决定合并。
  - 本项目让 Host 这个 agent 同时持有三样：任务书与问询记在黑板上；封存由黑板做，而不是由 Worker 做，报告之后封存不会再变；验收指名确切的封存并校验集成。在所读文档的范围里，没有别的系统让编排的 agent 拿到这一组合。
- 收窄：一，规则少说的是 Host 面对的一面（skill 约 4 KB，由检查卡着上限）；黑板内部有大量机械规则（准入、隔离、封存、停止证据），两面要分开说。二，可以说“Host 在验收前同时持有任务书、报告与不可再改的封存，三者互相对照；所读系统里这一步由人来做，或者不做”；不宜说“因此质量稳定”。
- 要量什么：按 buddy 与时间统计第一次封存就被验收的比例、每个微任务的打回次数、每个被验收的微任务之后出现的修正微任务（CONTEXT.md 里修正算作有缺陷的交付）、报告所说与封存不符而被 Host 抓到的次数；再在可比的工作上，与不封存的流程（例如原生子代理）对比。

**（四）“规则少，所以贴合用户自己的做法（ADR 驱动、先讨论再细化、计划—执行—测试—验收）。”判断：部分成立。**

- 证据：本项目不规定项目的工作方法：没有要求计划模式、规格格式或 ADR。对照之下，框架都要开发者写代码，Roo Code 要选模式并默认逐个批准，DeepSeek 的原型一个仓库一个部署、要配渠道与授权规则，GitHub 的代理走 issue 到 PR 的路。但规则少不是本项目独有：Claude Code 的子代理与团队、Projects、Conductor 也几乎不规定方法。另一方面，本项目对交出去的那一块规定得很具体：任务书要写输入、结果、检查、写入范围与权限档位，一个微任务只管一件事，宏任务要有标题与目标说明，还有封存、审查、验收或打回的循环。贴合的证据目前只有开发者自己用它开发这个项目的经验，没有别的用户的。
- 收窄：说成“不规定项目的工作方法，只规定交出去的那一块怎样交接”。
- 要量什么：请工作方法不同的几位用户各用一段时间，看 Host 是照用户的方法走（例如先写 ADR），还是被 skill 的循环带走，记下摩擦的地方。

### 主张三：垂直领域的积累。Router 从评价里学到哪个 buddy 在用户的领域里最好

判断：作为现有能力，不成立（还没有实现，开发者自己也这样说）；作为方向，所读系统里没有现成的对应物，相邻的做法与已知的风险都有。

- 证据：所读的路由器都不按用户自己的领域、从代理任务被验收的结果在线学习：RouteLLM 用公开的偏好数据离线训练，只在强弱两个模型间选；Not Diamond 用用户自己的评测数据训练，按请求在模型间选，追加数据后手动重训；Not Diamond Code 在 Claude Code 里逐回合选模型与推理强度，服务怎样训练没写；OpenRouter 按全站花费与约 30 种任务类型选，不看单个用户的结果；Arch-Router 按用户写的领域—动作策略对号，摘要没写从结果学习。编码工具里，执行者的模型由用户或配置指定；Claude Code 的 Projects 把用户的要求记进项目记忆，那是记住吩咐，不是学习。
- 支持这条路的线索：RouteLLM 的博客说，只用通用偏好数据训练的路由器在分布外的题目上接近随机，加入该领域的数据后变好；本地、本领域的数据有用（自述）。
- 风险的线索：本目录 2026-10-01 的记录引过的统一评测发现，多数路由方法不能稳定胜过一直用单一最好的模型。
- 收窄：对外只能说计划中；可以写目标是“按用户自己的领域与任务类型，从验收的结果积累每种 buddy 的评价，再用于路由”，并写明维护面与画像面还没有实现。
- 要量什么：在冻结的历史输入上回放，比较用评价来选、现在的选法（偏好加备注）与一直用最好的一种 buddy 三者的验收结果；每个领域节点、每种 buddy、每种任务类型要多少样本估计才稳定；探索要花多少；harness 或模型更新之后评价漂移多快。

## 取了什么，没取什么

- 取：上面的事实与判断，作为收窄三项主张措辞的依据；对照表可以用在 README 一类对外介绍里，具体措辞待开发者定。
- 取：术语撞车的提醒。本项目继续只用 CONTEXT.md 的词；对外比较时，提到 DeepSeek 的 Router、Claude Code 的 team lead 与 Projects、OpenAI 与 Codex 的 handoff，注明它们各指什么。
- 没取：别家的机制都没有作为设计输入，包括渠道事件触发的主动开工与观察模式、Watchdog 式的进度判断、PR 上的自动修复、按全站花费的路由、批量训练的路由器。要不要为其中某项另立调研，待开发者定。
- 没取：没有对哪一项为本项目独有下最终结论；对照只到所读文档为止，没有实测任何一家。

## 时效

- DeepSeek 的原文能读到，或 Super-agent 变成正式产品；DSH 的外部后端离开实验状态、开始支持多轮，或加上固定的产物与验收。
- Claude Code 的 agent teams 离开实验状态，Projects 对 Team 与 Enterprise 开放，Anthropic 改动 Opus 5 上关于何时委派的预设说明，或允许别家模型当子代理；Codex 在 Ultra 档之外也能主动委派。
- GitHub 的第三方代理改用用户自己的订阅或在本机执行；AionUi 加上隔离或验收；Conductor 或 Claude Squad 加上负责分派的主代理。
- 任何路由器开始按用户的领域、从代理任务的结果学习。
- 本项目实现了评价的维护面，或为 Codex 与 Claude Code 之外的 agent 写出 Host 指南，或做了上面任一项测量。
- 所读文档大多不标日期；版本明显前进时（例如 Claude Code 过了 2.1.31x、DSH 过了 0.2.2）要重读。
