# 公开资料与社区里，ZCode、DSH 与 ACP 怎样在回合进行中送入输入

## 问题

[上一份](2026-10-31-zcode-dsh-in-turn-input.md)只读了本机安装的软件并做了不调用模型的实验，全程没有用公开资料。本份续查公开的一面：ZCode 官方有没有把 app-server 的 v4 协议与 sendText 的几种送法（startNow、queue、guide）写成公开的接口，是打算稳定的接口还是桌面客户端的内部通道，有没有绑定期望回合的办法或宣布的计划；DSH 有没有把 steer、inject 或排队放上 ACP 的计划，除 ACP 外有没有别的公开、受支持的客户端接口能在回合中送入输入；ACP 的规范、提案（RFD）与社区讨论里，回合进行中送入输入（steering、排队的第二条消息）到了哪一步，主要的 ACP 客户端在用户回合进行中再发一条消息时怎样处理。仍服务于待办“问 ZCode 的 Worker”与 ADR-023 的“用每个 harness 自己的原生机制”，结论留给开发者。

## 看了什么

全部 2026-10-31 经网页读取，只读：不登录任何账户，不发帖、不提 issue，不下载或运行可执行文件，不读任何项目的源码（只读文档、发行说明与讨论的页面）。

- ZCode 官方文档站（[文档首页](https://zcode.z.ai/en/docs)、[远程控制](https://zcode.z.ai/en/docs/remote-control)）与 [changelog](https://zcode.z.ai/en/changelog)，厂商公开文档，读到当天的版本；changelog 覆盖桌面版 3.14.1 至 3.16.3（2026-10-23）。
- ZCode 公开源码仓库（zai-org 组织，Apache-2.0）的 README 页，版本列表更新到 v3.17.1（2026-10-30）；只读了 README，没有进仓库读别的文件。
- 社区两份自写的协议文档：ZC-GUI（一个 JetBrains 插件）的 app-server 协议参考（2026-10-11 更新，基于 CLI 0.16.5），zcode-executor 的 SPEC（2026-09-07，补记至 2026-09-21）。只读文档页，没读两者的代码。
- DSH 官方仓库的发行说明页（0.2.1 的 rc.1 至 rc.5 与 0.2.2-alpha.2）、官方 ACP 包的中文 README、官方 SDK 客户端（`@deepseek-ai/dsh-sdk-client`）的 README、社区插件讨论 dsh-steer（2026-10-11）。均只读文档与讨论页。
- ACP 规范站 agentclientprotocol.com：[v1 的 prompt 回合一节](https://agentclientprotocol.com/protocol/v1/prompt-turn)、[v2 迁移说明](https://agentclientprotocol.com/protocol/v2/migration)、[v2 prompt 生命周期 RFD](https://agentclientprotocol.com/rfds/v2/prompt)（修订至 2026-10-29）、[v2 tracking 总览](https://agentclientprotocol.com/rfds/v2/overview)；协议建议讨论 #1220（2026-05-17 起，读至当时的全部回复）。
- ACP 客户端与代理侧的厂商文档：[Zed 的 agent panel](https://zed.dev/docs/ai/agent-panel)、[JetBrains 的 ACP 页](https://www.jetbrains.com/help/ai-assistant/acp.html)、[Docker 代理的 ACP 页](https://docs.docker.com/ai/docker-agent/features/acp)。

没有做的：社区工具 dshctl 与一个第三方移动客户端的协议整理只在搜索摘要里出现、原文打不开（404），不采信为事实；下面每一条都出自读到了原文的页面。

## 看到的事实

ZCode。〔自述〕＝厂商公开材料自述，〔社区〕＝社区文档或讨论的说法，〔推断〕＝我们的推断：

- 〔自述〕文档站没有 app-server 或协议的条目；远程控制页写明可以"keep sending instructions to the Agent"、在手机输入框里继续打字把任务推进下去，即回合进行中送输入是它宣传的用户能力，但不写机制，queue、guide、steer 这些名字不出现；changelog 到 3.16.3 为止，没有 v4、sendText 或 steer 的条目。
- 〔自述〕源码公开，README 把产品写成桌面应用、浏览器界面与终端 Agent 三种形态，链接了 CLI 的使用指南，没有协议文档一节，也没有 steer、queue、guide 的字样。〔推断〕产品版本线是 3.x，zcode.cjs 的 0.x 是随包分发的 CLI 自己的版本线（本机 0.17.0 的装机日与桌面版 3.16.3 的发行日同为 2026-10-23）；v4 是“事实公开、没有契约”的接口——源码可见，官方把它当自家客户端的通道，不对第三方承诺稳定。
- 〔社区〕ZC-GUI 的协议参考自述非官方、由静态分析与协议实测逆向而来，警告协议随 CLI 版本演进可能随时变化、以官方发布为准；它记录了 v4/command 的封包、sendText 的三种 requestedDelivery（startNow、queue、guide，ZC-GUI 自己只用 guide 插队）、队列命令组（`sendQueuedNow`、`editQueueItem`、`reorderQueueItem`、`deleteQueueItem`）是官方客户端即时排队体验的地基；`baseRevision` 作 compare-and-swap 用，过期时 `deleteQueueItem` 会“假成功”，客户端要重读重试；没有记录任何期望回合绑定。基于 CLI 0.16.5。
- 〔社区〕zcode-executor 的 SPEC 写 guide 在下一个工具边界生效，无边界时排到回合结束后执行；应答的 accepted、noop、duplicate 都算成功，应答里 delivery 回 startNow（意味着回合已结束）记为失败并示警。
- 没有查到：官方对 v4 或 sendText 的文档与稳定性承诺；期望回合绑定的公开办法；关于绑定或 steer 的宣布计划。

DSH。标注同上：

- 〔自述〕官方 ACP 包的 README 列出的方法与本机读包一致，写明“每个会话只允许一个正在处理的提示词”，全文没有 steer、inject、queue；它引用的设计笔记其一题为“ACP 仅自动化协议”（2026-07-23）。
- 〔自述〕发行说明页（页上标注 rc.1 与 rc.2 为 2026-10-17，rc.3 为 10-23，rc.4 为 10-28，rc.5 为 10-30，0.2.2-alpha.2 为 10-28）里 ACP 只有 rc.4 修了“ACP 模式下诊断信息漏进 stdout”；steer 相关的改动（运行状态放在待处理插话之前、后台任务等待时用户插话立即解除等待）都在它自家界面层；页面没有路线图内容，各版说明里没有把 steer、inject 或排队放上 ACP 的字样，仓库讨论里也没有查到这样的计划。
- 〔自述〕官方 SDK 客户端的 README 写明让 TypeScript 程序经 stdio JSON-RPC 驱动完整的运行时；它没有专门的 steer 方法，但写明并发调用各自独立返回、输入走 prompt 队列，一次 run 的结果区间（receipt-to-idle）里可能有“steering、注入的上下文与其他排队的工作”一起贡献，模型活动期间原生命令回 busy 结果。
- 〔社区〕dsh-steer 插件（2026-10-11 发在插件展示分区，无回复）用自定义消息类型加 `agent.steer()` 让插话在“最近的小步骤末尾”（一个推理块或一次工具调用）读入，跟踪每条的送达状态，可对未读的重发；作者刻意不做排队与送达模式。全文不提 ACP。
- 它自家网关（Host HTTP）有 steer 是上一份记录从安装包读到的事实；公开资料里没有查到网关 API 的官方对外文档，第三方工具与协议整理的佐证原文打不开，不采信。

ACP。ACP 自己的规范与 RFD 按自述计，讨论按社区计：

- 〔自述〕v1 规范的 prompt 回合一节：一次一个回合，回合进行中客户端可做的只有 `session/cancel`，“回合完成后客户端可再发 session/prompt”；读过的这一节没有写忙时第二条 prompt 会怎样，没有定义 busy 一类的错误。〔推断〕DSH 忙时拒绝与下面 Docker 的 busy 错误都是各家实现自己的选择。
- 〔自述〕Docker 代理的 ACP 文档：有工作在途时 resume 与 delete 回 busy 错误且不取消在途工作，指引是“等它结束后重试”；配置修改要求会话空闲；声明只覆盖稳定的 v1，不承诺 draft 与 v2。
- 〔自述〕v2 草案（迁移页）：整体仍标 draft，经 `initialize` 里 `protocolVersion: 2` 协商开启；prompt 生命周期是最大的语义变化——`session/prompt` 的应答不再结束回合、只确认接受，回合完成走状态通知（含新增的 error 停止原因）；忙时的 prompt “必须不仅因为工作在进行就拒绝：立即插入，或持有并按收到的顺序插入”，应答带 messageId（只作相关、不作幂等），cancel 对未插入（含持有的）prompt 回 -32800；“显式的排队与 steering 留给以后的一份 RFD”（prompt 生命周期 RFD 修订至 2026-10-29）。
- 〔社区〕协议建议讨论 #1220《Proposal: session/inject for mid-turn queue + steer》（2026-05-17）：一个方法两个模式，steer 在下一个安全边界送达（工具调用进行中先让它跑完），queue 先进先出排到空闲；接受即应答，代理用 `session.inject.modes` 声明能力；维护者 benbrandt 当日认可方向、愿意收进 v2；作者 2026-05-24 起草了 RFD（PR #1261）；Kandev 客户端的作者 2026-07-10 附议按能力声明，并指出 Codex 合 steer 模式、Claude Agent SDK 目前只合 queue 模式（回合中输入排队到回合后，只有硬中断）。截至读到：这份 RFD 没有并入 v2 的 tracking 总览（总览 2026-07-20 的修订注写明 steering 与排队是分开的关切），prompt 生命周期 RFD 的最新修订（2026-10-29）仍把它们留给以后的 RFD。
- 〔自述〕Zed 的 agent panel 文档：生成状态下发送的消息默认排队，可编辑、可移除，可用停止按钮或双回车立即发送；“Steer”开关让排队的消息在下一步（通常是工具调用与回复之间）打断代理；并写明“Steering 只对 Zed Agent 可用，因为 Zed 检测不到外部代理的回合边界”。〔推断〕Zed 对 ACP 外部代理的实际做法就是客户端侧排队、等回合结束后再发。
- 〔自述〕JetBrains 的 ACP 公开页只写安装、配置与排障，没有写回合进行中再发消息的行为。没有查到。

## 取了什么，没取什么

与上一份相比多了公开资料一面，上一份的实测与读包结论无一被推翻、也无一需要修改；上一份“时效”一节设想“两家若提供公开文档描述这些机制，应以文档对照重查”——查了：ZCode 没有，DSH 的 ACP 有官方 README，与本机读包一致。取到的事实与理由，落到待办“问 ZCode 的 Worker”与 ADR-023：

- ZCode：v4 sendText 是事实公开但无文档契约的接口，社区已有两家第三方（ZC-GUI、zcode-executor）在依赖它并各自整理了语义；zcode-executor 把“应答里 delivery 回 startNow”记为失败，正是待办里“退化成排队时怎样不开新回合”的一个社区先例。若按 ADR-023 走原生机制，它仍是最贴近的入口；代价是没有稳定性承诺（升级须按版本重验）与没有期望回合绑定（社区文档同样只有 baseRevision 栅栏，且过期时会假成功）。把一个未文档化的接口当能力前提，处在 ADR-023 第 1 条“不把厂商标为实验性的接口当作能力成立的前提”的边界上，取不取留给开发者。
- DSH：官方把 ACP 定位成仅自动化、单提示词，rc 的节奏里没有把 steer 外露到 ACP 的迹象；ACP v2 草案把忙时 prompt 从“可以拒绝”改成“必须插入或按序持有”，若 DSH 跟进 v2，ACP 面至少会有排队，steer 仍不在范围。今天最贴近原生的做法仍是客户端侧排队（像 Zed 那样等回合结束再发）或在 skill 与使用说明里写明延迟，正是待办给出的两条；它自家的 SDK 面公开自述可驱动完整运行时、并提到 steering，但那是一条新的执行通道，是否改走它属于 ADR-023 第 7 条（复用已有执行载体）的权衡，留给开发者。
- ACP：标准层面今天没有可依赖的回合中送入——v1 没有写，v2 草案只到“持有不拒绝”，steer 与排队的标准化停留在提案与“以后的一份 RFD”；主要的编辑器客户端（Zed）对外部代理就是排队，其理由“检测不到回合边界”与上一份记录发现的可观察性缺口（steer 落在哪个回合只有 ZCode 私有面的事件能对账）相合。由黑板自己做排队与送达对账，与通行做法一致。

没做的：没有重做本机实验，没有读任何项目的源码，没有采信打不开原文的社区说法；两家厂商的动机与计划只以公开材料为准，没有查到的都照实写在上节。

## 时效

ZCode：官方一旦发布 app-server 协议文档或稳定性承诺，“无契约”的定性过时；zcode.cjs 版本变化的时效照上一份；ZC-GUI 的文档基于 0.16.5，0.17.0 起的新命令它未必覆盖。DSH：rc 节奏以天计，ACP 面新增方法、SDK 出现文档化的 steer 方法或网关 HTTP 出官方文档，任一发生即过时；它声明跟进 ACP v2 也算。ACP：v2 草案仍在修订（prompt 生命周期 RFD 到 2026-10-29 还在改），session/inject 并入 v2 或另立 RFD 定稿即过时；Zed 若把 steering 扩到外部代理（前提是回合边界变得可观察）也算。
