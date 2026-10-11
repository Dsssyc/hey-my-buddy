# 两处运行时修复的整合 Host 验收

2026-10-11，Claude Code Host。两处缺陷都是在日常使用里发现的，修复排在下一次安装之前，各是一个微任务，同属一个宏任务，基点是整合分支的 `b9958dce`。第一处已验收并合入；第二处（供应方标明不可重试的限流不留下额度事实，见[待办](../design/backlog.md)）的微任务还在进行，验收之后补在本文里。两处都只在源码里，没有安装：日常运行的 0.27.0 在下一次安装之前仍有这两个缺陷。

## Claude Code 的回合把已经结束的前台命令算作未了结

**缺陷。** Claude Code 2.1.284 为一条运行了几秒的前台命令发出 `system/task_started`（`is_backgrounded` 为 `false`），结束时发的是 `system/task_notification`，没有 `task_updated`。运行模块把每个 `task_started` 都记为后台任务，又只认 `task_updated` 为结束，于是回合结束时这条命令仍算未了结，回合被记为失败，原因是 `background-work-unsettled`。真正干活的回合因此几乎都以失败收场。成因此前用两次经用户批准的付费回合复现过，原始事件流留在整合工作树被忽略的 `tmp/claude-runtime/`。

**改动。** `TurnEvidence.observe` 里 `task_notification` 与 `task_updated` 走同一段代码；终止状态增加 `stopped`。合入为 `a6d6751e`，与封存的产出是同一个补丁。

**核对。**

- 差异全部读过：产品代码 2 处，测试 6 条，假的 CLI 多一种情形，运行清单文档改一句。
- 测试编号：三个受影响的模块 114 → 120，新增 6，消失 0，与实现方的清单一致。
- 三个模块 120 项通过，是在 Host 的沙盒之外跑的。在沙盒里同样的命令有 35 项不通过，原因没有逐条查。
- 变异 5 个，各被指名的测试抓到：不处理 `task_notification`；`stopped` 不算终止；`failed` 不算终止；任何 `task_notification` 都了结任务；不处理 `task_updated`。
- 用留存的真实事件流重放：基点上回合结束时任务 `b67sc680t` 未了结，候选上没有未了结的任务。

**没有验证的。** `task_notification` 的 `failed` 与 `stopped` 两个状态来自 Claude Agent SDK 的类型定义，实测只见过 `completed`。没有在候选上再跑付费的真实回合，重放用的是修复之前留存的帧。完整检查与这一批的其他改动合在一起跑。

**黑板上的记录。** 微任务 `abfb2edc-c2d0-4118-8e84-28ed73f25ce4`，整合记录 `int-4c35fa96-c4c4-440e-ad48-aeeed0f5df5a`，结论为接受，检出已回收。Worker 在自己的检出里无法提交（Git 目录在它的沙盒之外），为此向 Host 求助；黑板封存的产出已经带着全部六个文件，Host 直接按封存的产出整合。
