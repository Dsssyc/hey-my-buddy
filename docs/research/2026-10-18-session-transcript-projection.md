# 各 harness 的会话记录有没有现成的统一读法

2026-10-18，整合 Host。

## 问题

[ADR-024](../decisions/024-console-session-view.md) 要把各 harness 的会话内容投影成同一套事件，在控制台的微任务详情里显示。开发者问：有没有现成的项目已经做了这一层，可以直接用，不必为每个 harness 自己写。

## 看了什么

- 公开的软件包索引与包的说明页（2026-10-18 检索）：一个 Python 包（内核用 Rust 写）、一个 Rust 库、一个 Go 程序，都声称能读 Claude Code 与 Codex 的会话记录。只读了说明与元数据，没有读任何一家的源码。
- Agent Client Protocol（ACP）的公开文档：概览与 prompt turn 一节的 v1、v2 两版（[文档](https://agentclientprotocol.com/protocol/prompt-turn.md)）。
- 本项目的源码：DSH 的接入（`buddy/harnesses/dsh/`）已经按 ACP 处理会话更新。

## 看到的事实

- 自述：那个 Python 包把 Claude Code 的 JSONL 与 Codex 的会话记录读成同一种带类型的事件，块的种类有文字、推理、工具调用与工具结果；许可是 PolyForm Noncommercial 1.0.0，要求 Python 3.13 以上，发的是平台相关的二进制轮子；说明里只有读写完的文件，没有边写边读。
- 自述：那个 Rust 库给多种编程代理的会话建索引并出统计报表；那个 Go 程序的重点是用量统计，带一个本机的看板。两者都不支持 DSH 与 ZCode。
- 自述：ACP 的 `session/update` 定义了消息片段、推理片段、工具调用、工具调用的更新（带状态）与计划这几种事件。v1 与 v2 的事件名不同（例如 v2 用 `agent_message`，并新增状态更新），实现前要定死一个版本。
- 实测（读源码）：本项目的 DSH 接入已经在逐条处理 `session/update`，认得 `tool_call`、`tool_call_update` 与 `agent_message_chunk`；ZCode 用它自己的协议；Codex 与 Claude Code 由各自的原生协议驱动（app-server 的事件与 stream-json），接入代码在运行中已经逐条读这些事件，只取了阶段与工具名。
- 推断：没有一个现成的库能同时满足许可、四个 harness 与边写边读。

## 取了什么，没取什么

- **取：统一的事件形状用 ACP 的 `session/update`**，不自己再定一套。它是公开的协议，DSH 已经在说它。落在 ADR-024 第 4 条（2026-10-18 的修改）。
- **取：运行中的内容从各 harness 接入已经在处理的原生事件流里投出**，已结束的回合再读私有区里的会话记录。落在 ADR-024 第 5 条的修改。
- **没取：那几个读会话记录的库。** Python 包的许可不允许商用，作为公开产品的依赖不合适，也不支持边写边读；另外两个的语言与我们的服务不合，而且都不支持 DSH 与 ZCode。

## 时效

ACP 发布新的大版本或改动 `session/update` 的事件，或者出现许可宽松、覆盖这四个 harness 并能边写边读的库，这份记录就过时了。
