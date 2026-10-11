# 调研：聚合多个 harness 的平台怎样在各家之间保持一致

日期：2026-10-02。整理自 [ADR-023](../decisions/023-harness-integration-principles.md) 的调研小节，2026-10-11 移入本目录。事实一节照原文移入，只去掉了指向别的项目仓库的链接；当时读了每个来源的哪些部分、读到哪里为止，原文没有逐项记录。

## 问题

一项行为要在几个 harness 上都成立，例如只读查看代码、受限联网，而各家的原生能力并不相同。开源的多 harness 聚合平台是怎样处理的：换上同一套工具，还是别的办法。

## 看了什么

五个项目或协议当日的公开文档，以及一份第三方的汇总。没有实测，没有读源码的记录。

## 看到的事实

以下依据各项目当日的公开文档，未做实测。

| 项目 | 做法 |
| --- | --- |
| [Vibe Kanban](https://vibekanban.com/docs/configuration-customisation/agent-configurations) | 每个代理只用自己的原生参数：Claude Code 是计划模式与跳过权限，Codex 是三档沙盒与审批策略，Gemini 是免确认开关。没有跨代理通用的只读模式 |
| Omnigent | 策略分服务器、代理、会话三级。统一强制靠在代理进程外套 bwrap 或 seatbelt，再加出口代理。维护一张能力矩阵，并用测试台核对矩阵与实际行为。自述 Windows 上只约束进程树，不隔离文件系统与网络 |
| Rivet sandbox-agent | 统一的是 HTTP 接口与事件格式；隔离交给外层沙盒，自己不做限制 |
| [Agent Client Protocol](https://agentclientprotocol.com/protocol/v1/tool-calls) | 为工具调用规定统一的类别：read、edit、delete、move、search、execute、think、fetch、switch_mode、other。类别由代理上报，用于展示与权限提示，协议不强制 |
| Anthropic sandbox-runtime | 可以包裹任意进程的系统沙盒：写入与网络默认拒绝，按白名单放行。支持 macOS 与 Linux，Windows 为 alpha |

各 harness 自带的沙盒也不齐全。按[一份 2026 年 5 月的汇总](https://gist.github.com/wincent/2752d8d97727577050c043e4ff9e386e)，Codex 在 macOS、Linux 与 Windows 上都有，Claude Code 只有 macOS 与 Linux，Cursor 没有。

结论：没有一家靠"给每个 harness 换上同一套工具"来保证一致。它们统一的是三样东西：通用设置到各 harness 原生开关的映射，差异记入能力矩阵；进程之外的系统级隔离；事件的词汇。

## 取了什么，没取什么

取：通用设置映射到各 harness 的原生开关，差异记入能力矩阵；一致性放在归一之后的证据上；用探针核对能力矩阵与实际行为。

没取：为各 harness 重做同一套工具；在进程之外自建系统级隔离，这件事留到确有需要的档位再议。

## 时效

各项目的文档随版本变化；各 harness 原生沙盒的平台覆盖以其自身文档为准。2026-10-09 的补查没有改变这里的结论。
