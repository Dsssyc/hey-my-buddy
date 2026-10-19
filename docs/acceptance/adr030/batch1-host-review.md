# ADR-030 批次 1：接入角色说明的验收

2026-10-12，整合 Host。[ADR-030](../../decisions/030-harness-layer-scope.md) 第 8、9 条与[执行计划](../../design/adr030/execution-plan.md)的批次 1。一个微任务，两个回合，Codex 的 Worker（gpt-6.1-sol / high），在已安装的 0.29.0 上运行；封存的交付是 `030d3b59` 与 `04b195bb`，合入整合分支的提交是 `f31e914b` 与 `af307fc8`。实现方自己的说明在 [batch1-notes.md](../../design/adr030/batch1-notes.md)。

## 做了什么

三段角色说明成为随运行时发布的资源（`buddy/roles/texts/`，登记在 `packaging/runtime-assets.json`）。提示词的拼装合成 `buddy/roles/prompts.py` 一处：角色说明在最前，其后是原生的交付说明、任务的文字与回合的输入；四个 harness 的 Worker 回合与 Router 的快速、审阅两种调用都经它，没有参数能改动或省掉角色说明。Lead 的一段只随包，没有接线。`turn_io.ASSISTANCE_HINTS` 并进 Worker 的一段之后删除。

## 核对

- **差异。** 18 个文件读完。Router 与 Lead 两段与[定稿](../../design/adr030/role-texts.md)逐字相同；Worker 一段是定稿加两条，内容来自原来的求助提示：什么时候以 `assistance` 或 `attention` 结束，结果里各个字段放什么，不许声称审查者或协助者已经运行过。
- **测试。** 实现方在沙盒里跑了 112 项；它跑不了的（冷启动、启动器、打包、受管运行的接线）由 Host 跑，连同它的定向测试共 134 项全部通过。
- **完整检查。** 整合之后跑了两次。第一次 3,740 项，224 个文件里 1 个不过：路由的一条测试断言“请求的提示词等于渲染出来的正文”，是这一批该改而没有改的，打回之后只改了这一条测试。第二次 3,716 项，仍有 1 个文件不过，是 Claude Code 适配器的一条时序测试（期望 `native-turn-started-early`，得到 `native-init-missing`），与这一批无关：这一批合入之前它就在并行的完整检查里失败过，单独重跑六次都通过。它已记进待办。
- **真实回合。** 开发者批准了五次付费调用。提示词由产品自己的拼装函数生成（Codex 与 Claude Code 用登记的 Worker 格式，DSH 与 ZCode 用受管回合的提示词，Router 用路由的准备函数），经各 harness 的运行模块执行。任务的文字里故意写着“先问用户要中文还是英文，并等他回答”。

| 调用 | 配置 | 结果 |
| --- | --- | --- |
| DSH 的 Worker 回合 | deepseek-official / deepseek-flash / off | 正常结束；直接回答，并说明没有去问人，因为这个会话里没有人会回答 |
| ZCode 的 Worker 回合 | bigmodel-standard-api / GLM-5.3-Flash / low | 正常结束；同上 |
| Codex 的 Worker 回合 | openai / gpt-6.1-sol / high | 正常结束；返回结构化的 `completed` 结果 |
| Claude Code 的 Worker 回合 | anthropic / claude-sonnet-5-5 / high | 正常结束；以 `attention` 结束，按结果的字段写明需要 Host 定语言 |
| Router 的快速调用 | dsh / deepseek-official / deepseek-flash / off | 探针通过；请求的正文从第一个字符起就是 Router 的那一段 |

四个 Worker 回合的提示词都以 Worker 的一段开头，任务的文字排在它之后；没有一个去问人或等人。五次调用结束时原生进程组都已确认消失。

## 验证的边界

- 真实回合没有经过服务与 Worker 运行时，是直接经运行模块跑的；工具范围是无工具（Claude Code 是只读），不是带写权限的完整 Worker 回合。
- Codex 的第一次尝试在准备阶段被拒，没有到模型：Host 的脚本给的时限超过了快速模式的 60 秒。
- 用户级指令与角色说明的冲突要到取消隔离之后才真正出现，由批次 3 的真实回合核对。
- Lead 的一段没有接线，也没有测过。

## 留给后面的

- Codex 的原生结果格式里没有 `suggestedProfileId`，而 Worker 的文字提到了它。
- Router 的文字讲三个功能面，运行时现在只执行路由一面；维护与画像两面还没有受管的调用。
- 无工具的路径在 Codex 与 ZCode 上不报用量。
- [role-texts.md](../../design/adr030/role-texts.md) 里 Worker 的一段还是并入之前的版本，测试允许它与资源相差这两条；Host 把文档更新为并入后的全文之后，测试可以收紧为逐字相同。
- 共用的 skill 改成只写 Host 的事，由 Host 做。
