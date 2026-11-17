# ZCode 0.17.0 与 DSH 0.2.1-rc.2 能不能在回合进行中送入输入

## 问题

Host 问 ZCode 与 DSH 的 Worker 时，问题现在只在 Worker 自己调用协作检查点或想结束回合时才送到（[harness 集成](../reference/harness-integration.md)的问询送达一节）；2026-10-31 一个 GLM-5.3 Worker 三个半小时只调过一次检查点，Host 的问题等到它结束回合才被看到（待办“问 ZCode 的 Worker”）。要弄清：现在安装的 ZCode 0.17.0 与 DSH 0.2.1-rc.2 的客户端协议里，有没有把新输入送进正在跑的那个回合、让模型在下一次决策时读到的原生办法；能不能绑定期望的回合；送入的输入算新回合还是排队。服务于 ADR-023“用每个 harness 自己的原生机制”之下的决定：改成实时送达，还是在 skill 与使用说明里写明延迟。

## 看了什么

- ZCode app 自带的 `/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs`（`--version` 0.17.0，2026-10-23 装机）。读了源码：混淆 bundle，只读了协议与方法层（方法名表、参数 schema、命令分发、steer 运行时的准入与校验、事件枚举），没有通读，也没有读模型与工具实现。本机已无 0.16.9 的包，0.16.9 的行为以 2026-09-25 的[验收记录](../acceptance/zcode-checkpoint-inquiry.md)为基线。
- 自己的实验（2026-10-31，无模型调用）：在检出 `tmp/` 下用私有 HOME 与私有存储目录起 `zcode.cjs app-server`，实测 `v4/command` 等方法的应答；进程用完停掉并核对已不在。脚本与原始输出留在忽略的 `tmp/` 下。
- DSH：`~/.local/bin/dsh` 指向 `~/.local/share/dsh` 安装的 0.2.1-rc.2（2026-09-11 装机）。读了源码：安装的 npm 包里 ACP 桥与 agent 循环的带注释代码（方法注册、prompt 准入、收件箱），没有读它的上游仓库。自己的实验（无模型调用）：私有 HOME 与私有 `DSH_HOME` 起 `dsh --profile acp`，实测方法面。
- 它自带的 `@agentclientprotocol/sdk` 1.4.0（含实验性 v2 草案 schema）的公开类型与说明，读了个遍。
- 我们这边怎样驱动两者：`src/hey_my_buddy/buddy/harnesses/zcode/`（`session/send` 一路）与 `dsh/acp/client.py`（ACP 八个方法）。

## 看到的事实

ZCode。以下标〔实测〕的是本机实验，标〔读包〕的是读安装包得出，标〔推断〕的是我们的推断：

- 〔读包〕`session/send` 没变：参数 schema 仍没有 delivery、expectedTurn 之类字段；会话有活动 prompt 时处理函数直接拒绝（`-32010 "A prompt is already running for this session"`）。〔实测〕对不存在的会话回 `-32004 "Session is not active"`。
- 〔实测〕同一个 stdio 协议上 `v4/command` 可达：空参数返回 v4 形状的裁决 `{"status":"rejected","reasonCode":"proto.invalidPayload"}` 带逐字段校验详情，而未知方法才回 `-32601 Method not found`；`v4/commands/query` 同样被分派。〔读包〕ZCode 协议方法与 v4 方法由同一个分发器处理，v4 网关在协议服务器构造时无条件创建；`v4/controller/subscribe` 不在这个进程的分发表里（〔实测〕回 -32601）。
- 〔实测〕`v4/command` 的封包是 `{commandId, clientId, sessionId, type, payload, issuedAt, baseRevision?}`；命令 `type` 共 36 个，除了 sendText 还有整组队列操作：`sendQueuedNow`、`editQueueItem`、`reorderQueueItem`、`deleteQueueItem`、`setAutoDrain`。sendText 的 payload 里 `requestedDelivery` 的合法值是 `"startNow"|"queue"|"guide"`（传 `"steer"` 被枚举拒绝并回完整列表）。
- 〔实测〕合法封包、`sessionId: null` 的 sendText 回 `{"status":"rejected","reasonCode":"proto.sessionNotFound","revisionAtDecision":0}`——只走到会话路由，没有任何模型工作；裁决另有 `stale`、`duplicate` 等状态（〔读包〕ack schema），封包可带 `baseRevision` 做版本栅栏，sendText 可带 `expectedHeldQueueItemIds` 绑定排队中的条目。
- 〔实测〕payload 里多带的 `expectedTurnId` 字段被静默丢弃（应答照常走到会话路由）。〔读包〕期望回合绑定只存在于内部包装层：`steerTurn` 的选项有 `expectedTurnId`，不匹配时以 `expected_turn_mismatch` 拒绝（另有 `no_active_turn`、`turn_not_steerable`、空输入、超长四类拒绝），供 TUI、工具结果的 follow-up 与会话邮箱使用；v4 的客户端封包没有这个字段。
- 〔读包〕0.17.0 的运行时有成体系的 steer 机制：输入挂进活动回合的待定列表，在下一次模型请求之前注入（模型下一次决策读到）；常规回合自始可 steer，进入不再有工具边界的最终答复段、模型出错或 prompt 被 hook 拦下时关闭（`steerable` 置否）。`requestedDelivery: "guide"` 的 v4 sendText 在活动回合可 steer 时就是这样注入回合内；不可 steer 时退化为普通排队并记回退原因（如 `guide.noToolBoundary`）；`queue` 明确排队；默认送法在忙时排入队列，ack 里回 `delivery: "queue"`，要求空闲或带模型执行计划的送法在忙时被拒（activePrompt 错误类）。回合结束后送来的输入留在 held 队列，可编辑、重排、删除或立即发送。〔推断〕问题若在模型做完最后一个工具调用、开始写最终答复时送入，会排队到下一回合而不是被拒；未经实测（需真实模型回合）。
- 〔读包〕导出的 `session/event` 通知类型新增 `turn.steerQueued` 与 `turn.steerDrained`，负载带 targetTurnId、pendingInputId 与 delivery——客户端可以事后观察到输入落进了哪个回合。我们适配器的事件白名单没有这两个名字（未知事件归入一个桶）。
- 与 0.16.9 相比：`session/send` 两条结论原样（对照验收记录）；变的是内部 steer 从“客户端不可见”长成了运行时机制加客户端入口——0.16.9 时 v4 sendText 的 guide 只能延迟无绑定的输入或开新回合，0.17.0 的 guide 在活动回合可 steer 时注入回合内，并有导出事件与队列操作命令。这一对比的前半来自旧验收记录，不是重装 0.16.9 实测。

DSH。标注同上：

- 〔实测〕ACP 面没有任何输入注入方法：`session/steer`、`session/queue`、`session/inject`、`session/send` 都回 -32601 Method not found（与 `bogus/method` 同形状）；`initialize` 应答 `deepseek-harness-acp`，会话能力只有 close、list、resume。
- 〔读包〕ACP 面注册的方法共九个：initialize、authenticate、session/new、list、resume、close、set_config_option、prompt，加 session/cancel 通知，与我们 `dsh/acp/client.py` 调的集合一致。`session/prompt` 在已有 prompt 未完成时直接拒绝（invalid params “a prompt is already in flight for this session”）：ACP 客户端既不能回合内送入，也不能排队第二条。〔实测〕对不存在的会话回 -32602 "unknown session"。
- 〔读包〕能力在但不在 ACP 面上：agent 层有 `steer`（下一个步骤边界注入运行中的回合）、`inject`（同样注入但不唤醒）与 `followup`（排队到下一回合；ACP prompt 走的就是它）；DSH 自家网关的会话控制器有带 `mode: "steer"` 的 prompt 和队列操作（编辑、删除、把排到下一回合的条目提升进当前回合，提升要求条目在队且 agent 正在运行，否则 `session/steer-unavailable`）。
- 〔读包〕自带的 ACP SDK 1.4.0 的 v2 实验草案也没有回合内方法，其说明直接要求 prompt 串行：前一个回合报 stop 之后再发下一个，因为会话更新无法跨重叠的 prompt 归属。
- 早先版本没法比：本机只有 0.2.1-rc.2 一个安装（2026-09-11 装），仓库记录里也只出现过这个版本。

## 取了什么，没取什么

结论留给开发者（待办“问 ZCode 的 Worker”与 ADR-023），本记录只立事实：ZCode 0.17.0 已有实时送达的原生机制，但入口在 v4/command 的 sendText（`requestedDelivery: "guide"`），不在我们现用的 `session/send`；没有期望回合绑定（多带的字段被丢弃，防错靠 `baseRevision` 与事后的 `turn.steerQueued`/`turn.steerDrained` 事件），送入时机受“可 steer 窗口”限制，最终答复段会退化为排队。DSH 在我们驱动它的 ACP 面上没有机制：忙时 prompt 直接被拒，其内部的 steer 不外露；要实时送达只能等 DSH 把能力放到 ACP 上，或改走它的私有网关（后者不符合 ADR-023 用 harness 原生机制的边界）。没做的：没有改任何产品代码；没有用真实模型回合验证 guide 的注入时机、最终答复段的回退与 ack 的 `delivery` 三值（需要模型调用）；没有上网，没有读两家上游仓库。

## 时效

ZCode：`zcode.cjs` 版本变化可能改 v4/command 的封包、枚举与 guide 语义——v4 是其桌面客户端的主通道，预期能动；本记录基于 0.17.0（2026-10-23 装机），换了版本应重查并写新的一份。DSH：0.2.1-rc.2 是预发布，ACP 面新增方法（或转正 ACP v2）即过时；其自家 API 的 steer 若暴露到 ACP 也算。ACP SDK 的 v2 是实验草案，定稿可能变。两家若提供公开文档描述这些机制，应以文档对照重查，本记录全程未依赖文档。
