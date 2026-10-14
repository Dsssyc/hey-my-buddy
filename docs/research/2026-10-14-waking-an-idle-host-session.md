# 回合结束之后，Host 的会话能不能被叫醒

## 问题

Host 委派出去的微任务在跑时，Host 的回合能不能先结束，等微任务交回、需要决定或该提醒时再被叫醒；还是必须把回合一直开着等。这决定 Host 指南里怎样等待，也决定 Lead 的建模：Lead 等名下微任务时是结束回合、由黑板叫醒，还是开着回合干等，以及等待中的 Lead 占不占 Worker 的并发名额（[ADR-026](../decisions/026-delegation-authority.md) 第 9 条）。开发者于 2026-10-14 要求先弄清 Codex 的情形再定，并要求说到 harness 时写明是 CLI 还是 app。

## 看了什么

- Claude Code CLI 2.1.284：本机安装的软件，自己的实验（一次付费调用，Haiku 4.5，开发者批准）。
- Claude Code app：整合 Host 自己的会话一直在 app 里跑，后台命令结束时的通知会叫醒它；这是长期的观察，不是专门的实验。
- Codex CLI 0.162.1：本机安装的软件。读了 `codex --help`、`codex queue --help`、`codex agents --help`、`codex remote-control --help` 的输出；自己的实验，三个短回合（gpt-6-luna / low，开发者批准）。没有读源码，没有读 Codex 的公开文档。
- Codex app（桌面版）0.162.0-alpha.17.2：只有 [Codex 的 Host 指南](../reference/host-codex.md) 里早先记下的观察，这次没有在 app 上做实验。

## 看到的事实

- 实测，Claude Code CLI 2.1.284：会话用后台命令跑 `sleep 25` 后结束回合；命令结束时，空闲的会话自己开始新回合并回答。命令行上给的开场提示词在启动后没有自动提交，由开发者在终端里粘贴。
- Codex CLI 0.162.1 的帮助自述：`codex queue --thread <会话的 UUID 或名字> --message <文字>` 给一个已有的会话排进一条消息；`codex agents` 浏览共享的本地 app-server 守护进程上的全部会话；`codex remote-control` 管理开了远程控制的守护进程（标为实验性）。启动时不能给会话起名，会话的 UUID 从会话文件的名字读出。
- 实测，Codex CLI 0.162.1：会话在终端里开着、回合已结束时，从另一个进程 `codex queue` 排进的消息立刻作为新回合跑完（2 秒），界面上显示为一条新的用户输入和回答。关掉会话之后再排一条，48 秒里会话文件没有变化，没有在后台被跑掉；随后 `codex resume <UUID>` 重新打开，这条消息在打开时被送进去并跑完。命令行上给的开场提示词在启动后自动提交了。
- 早先的观察，Codex app 0.162.0-alpha.17.2：回合结束后没有东西能叫醒 Host 的会话，子代理发给根代理的后续任务也不能（见 Host 指南的已知限制）。
- 推断，未验证：排队的消息经会话所在的 app-server 送达。本机同时有几个 app-server 进程：ChatGPT app 里内置的一个、VS Code 扩展的一个、Codex CLI 的一个；`codex queue` 能不能送进一个开在 Codex app 或 IDE 扩展里的会话，没有试过。

## 取了什么，没取什么

- 取：Lead 的会话是黑板开的，黑板在它的原生会话上开新回合就能叫醒它，不需要 `codex queue`；这条路黑板每次续接 Codex 微任务都在走（有序收尾那一步的四个回合都在同一个 Codex 线程上）。所以等待中的 Lead 可以结束回合、不占名额，这一点不取决于 Codex 当 Host 时的情形。
- 取：Codex CLI 当 Host 时，回合结束后有它自己的办法把事件送进来：会话开着就立刻被叫醒，关着就等下次打开时送达，消息不丢。Host 指南里“Codex Host 的回合不结束”这条规矩，对开着的 Codex CLI 会话不再是唯一的办法。
- 没取：黑板不主动去续接 Host 的会话。Host 面对用户，Lead 面对 Host（开发者 2026-10-14）；把事件送进 Host 会话，用的是 harness 自己的机制，由 Host 一侧发起。具体怎样接（由谁调用 `codex queue`、Host 怎样知道自己会话的 UUID）随 Lead 的唤醒一起设计，现在的 Host 工作流还没有用它。
- 待查：Codex app 与 IDE 扩展里开着的会话能不能被 `codex queue` 叫醒。

## 时效

Codex 的 `queue`、共享守护进程与远程控制都是新近的功能，其中远程控制标为实验性，换版本可能改变行为；Codex app 与 Codex CLI 各自的版本号要分开看。Claude Code CLI 开场提示词不自动提交，可能是这个版本或这个终端的情形。任何一方的版本变了，这份记录就要重查。
