# Codex CLI 与 Claude Code CLI 的回合内问询：验收记录

2026-10-16，整合 Host。依据是[研究笔记](../../research/2026-10-16-codex-claude-in-turn-questions.md)与 [ADR-031 执行计划](../../design/adr031/execution-plan.md)里的同名待办。微任务“问询：Codex 与 Claude Code”（Codex CLI，`gpt-6.1-sol / high`，三个回合：实现约 20 分钟，返修一回合被 Host 停下，换了做法之后约 15 分钟），基点 `c605b127`，产物 `f6cb9560`，合并为 `7d23c296`。版本 0.42.1，契约仍是 0.42.0，同日装上（[安装记录](../installs/0.42.1.md)）。

## 做成了什么

- Codex CLI 与 Claude Code CLI 都声明 `inquiry`，送达方式是 `realtime`：问题进入正在跑的根回合，模型在下一次决策时读到；问题不开启也不延长回合，回合结束之后才到的问题被拒绝并记为不可用。DSH 与 ZCode 仍是合作式的检查点。
- Codex CLI：问题经绑定当前回合的 `turn/steer` 送达；答复走 app-server 的客户端工具（`thread/start` 的 `dynamicTools`，`initialize` 声明 `experimentalApi`），调用经驱动自己的连接回来，驱动只为当前根回合、已送达且未答的问题记录答复，其余调用回失败、回合继续。不挂 MCP 服务，没有签名回执，也不受审批策略影响。
- Claude Code CLI：问题写进流式输入；答复走会话 MCP 工具（`--mcp-config` 与 `--allowedTools`，另加 `ToolSearch`），签名回执由驱动核对。普通的工具失败不结束回合，失败原文有界地留作证据。送过问题的回合，驱动在根结果到来时停掉原生进程组，收尾时才到的问题不会开出第二轮，记为不可用。
- 服务一侧：问题的原因写具体的 `reason`，不再被通用说明盖住、也不在第 200 个字符处截断；执行结束之后读它的问询日志；只按 `runId` 观察也会把各个问题的最新状态导进来。

## Host 的核对

- 第一轮产物在私有服务上的付费端到端核对两边都失败了（开发者逐次批准）。Codex：答复工具的调用失败，驱动把普通失败判成致命，整个微任务以 `harness-error` 结束。一次诊断回合临时记下原始帧，查到根因是 Codex 自己拒绝了挂上的 MCP 调用（`MCP tool call requires approval, but approval policy is never`），参数是对的。Claude Code：问题在根查询收尾时送到，驱动随后见到第二个根 `system/init`，判为协议错误。
- 开发者定下 Codex CLI 改用客户端工具、Claude Code CLI 保留会话 MCP；Host 停下正在跑的返修回合，续接时只换这一项。
- 读完返修的差异；受影响的测试 43 个文件、1,005 项通过。
- 在“有界保存加这一批”的临时合并上起私有服务（新的状态目录），付费复核：Codex CLI `gpt-6-luna / low` 在新回合里问，约 4 秒后经客户端工具答 "42"，回合正常完成；续接回合（`native-session`，同一线程号）里再问，同样答复，证实恢复线程之后客户端工具仍可用。Claude Code CLI haiku 在写权限下跑一个约 25 秒的计算时问，约 25 秒后经会话 MCP 答复，回合正常完成；驱动的观察记下在根结果处停了进程组，没有第二轮。之前两个 Claude Code 回合没测到问询，是 Host 的任务设计：只读权限禁用 Bash，写权限下 Claude Code 拦下单独的 `sleep`；两次模型都如实以 attention 交回，晚到的问题被干净地拒绝。
- 付费回合的消耗（输入，其中命中缓存，输出）：第一轮 Codex 10.0 万、6.6 万、143，Claude Code 5.0 万、5.0 万、584；两次诊断 5.8 万、2.9 万、134 与 16.8 万、13.4 万、424；复核 Codex 13.4 万、10.0 万、221 与 15.1 万、14.6 万、200，Claude Code 5.0 万、5.0 万、731，6.3 万、6.3 万、1,389，12.8 万、12.8 万、664。

## 没有验证的

- Claude Code CLI 收尾窗口里送到的问题（根结果之后紧跟第二个 init）只在模拟的输出流里覆盖。
- Codex 子线程或旧回合的答复调用只在模拟的 app-server 里覆盖。
- 只在 `gpt-6-luna` 与 haiku 上跑过；别的模型是否同样调用答复工具没有验证。客户端工具是 Codex app-server 的实验接口，Codex CLI 升级之后要重新核对。
- Windows 未验证。
