# 契约 0.50.0：验收记录

[执行计划](../../design/adr024/session-redesign-plan.md)的“批次 C”。两个微任务依次做，都在宏任务“契约 0.50.0”里；合入整合分支，尚未安装。

## C1：接口剪枝与删除契约

- **微任务：** “接口剪枝与删除契约”（run `51316b7f`，Codex CLI `gpt-6.1-sol / high`，一个回合），基点 `42b4520c`，合入为 `986f4b03`。
- **做了什么：**
  - 回答里删掉恒定、重复或能推出的字段：微任务的 `acceptanceVerdict`，消息的 `correlationId`，helper 视图的 `autoContinue`，Host 结论的 `evidence` 与 `integrationId`，审计视图的 `spec`（连同 `_original_spec`），冲突 `delivery` 里的常量与外层副本，整合的 `notRequired` 与 `verification` 里的常量、推出值与外层副本，清理的 `retention.commandReceipts` 与 `result` 里的副本，选择记录的 `kind` 与 `selection-list` 的 `kind` 输入，harness 视图的 `available`。服务、执行进程、CLI 简报、控制台与合成预览的读者随之改。
  - 删除进行中的错误码改为 `MACRO_TASK_DELETING`（HTTP 409），细节里去掉恒为常量的 `state` 与 `deletedAt`；删完之后的预览与再删在合成预览里都回答 `NOT_FOUND`，控制台把预览或删除收到的 `NOT_FOUND` 当作已删完。
  - 顺带做完待办“剪枝三、四的独立审查留下的几件小事”：`adjusted_paths` 死参数连同分支删掉，`CatalogView` 只从载荷读发现时刻，`decision.py` 的过时说明去掉，`_conflict_view` 与 `_resolution_delivery_result` 对可信数据的防御删掉。
  - 版本 0.50.0：`CONTRACT_VERSION`、签名表、`pyproject.toml`、`uv.lock`；README 由 Host 改。
- **核对后照留的：** 冲突 `delivery` 的 `commandId` 在冲突视图的外层没有副本；整合失败时错误细节里的 `verification` 是诊断，不是成功记录，`verified` 可为 false、路径可不为空。Router 健康、适配器、buddy、模型与原生发现各自的 `available` 是别的事实，不动。
- **核对：** 差异读过。与挪升级基点合并时 `tests/python/blackboard/store/test_upgrade_fixture.py` 冲突，C1 在那里改的都是已删掉的转换测试，取挪基点一边（验收时以 `adjusted:true` 确认）。合并后的树上 typecheck、前端 91 个文件 1,085 项、浏览器检查 590 项（重建的产物与交付一致）、完整检查 290 个文件 4,550 项通过。
- **留给 C2 的：** `workspace-resolve` 与 `get` 回复里的 `resolutionDelivery` 还带着恒定的 `state`、`kind` 与 `turnOutcome`，计划没点到，交 C2 顺带。
- **消耗：** 输入 1,753 万（命中缓存 1,730 万），输出 2.8 万。
