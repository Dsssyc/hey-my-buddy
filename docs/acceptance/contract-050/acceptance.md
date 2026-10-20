# 契约 0.50.0：验收记录

[执行计划](../../design/adr024/session-redesign-plan.md)的“批次 C”。两个微任务依次做，都在宏任务“契约 0.50.0”里；2026-10-20 随 0.50.0 安装（[安装记录](../installs/0.50.0.md)）。

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

## C2：宏任务列表的排序、时间轴上被改派的尝试与“适应宽度”

- **微任务：** “列表排序、改派的尝试与叫法”（run `6e1ea94b`，Codex CLI `gpt-6.1-sol / high`，一个回合），基点 `1eade75a`（C1 封存的输出），合入为 `dc640eab`。
- **做了什么：**
  - `macro-task-list` 加 `sort`：`activity-desc`（默认）、`activity-asc`、`created-desc`、`created-asc`；宏任务按 `macro_tasks.created_at`，没有宏任务的分组按根 run 的创建时刻；同一时刻按分组标识；游标带着排序，换了排序的游标拒绝。控制台列表头上加两个下拉（排序依据、方向），非默认的选择写进地址。
  - 宏任务时间轴把模型开始之前被额度拒绝、随即改派的那次尝试标成 `rejected`，画成已改派；微任务页把回合的 `rejected` 并进片段的前端拼接删掉。
  - “适应宽度 / Fit width”统一；`resolutionDelivery` 删掉恒定的 `state`、`kind` 与 `turnOutcome`。
  - 待办“无宏任务的 Lead，名下的微任务在列表里各成一组”：实现方核对现行代码，Lead 名下的树已经归到 Lead 的分组（待办写的是 2026-10-17 的状况，之后已修），补了断言（分组与删除预览的数一致），没有改代码；待办随之删去。
- **核对：** 差异读过。合并后的树上 typecheck、前端 91 个文件 1,087 项、浏览器检查 598 项（重建的产物与交付一致）、完整检查 290 个文件 4,552 项通过。像素：日常看板副本上的私有服务里，列表的排序控件在搜索框上方，按创建时间升序时最早的宏任务在前，深浅两色都看过；日常看板里没有被改派的尝试，这一处在合成预览上看：那一段不再带失败色的描边，朗读名为“额度被拒，已改派”。
- **观察：** 按创建时间排序时，列表每一项旁边写的仍是最近活动的时间，不是创建时刻。
- **消耗：** 输入 1,206 万（命中缓存 1,186 万），输出 2.9 万。

## 安装演练

脚本 `tmp/schema-audit/reh-0490.sh`（不进仓库），基线改为 0.49.0（`ea7ff3a4`），候选 `0d537cfe`（代码与 `dc640eab` 相同）。空 HOME 下经启动器装 0.49.0，把它新建的看板换成日常看板的只读副本（schema 27，290 个任务、433 次尝试、7,052 条事件、1,097 条命令记录，空闲），在副本上起 0.49.0 的服务，再装候选。这一版没有转换，转换类的故障不再适用。

- **正常路径：** 11 秒装完；备份已校验（schema 27，2 个文件，12.5 MB）；服务 0.50.0、schema 27，54 张表，`meta` 两项，完整性与外键无误；计数与封存内容的五项摘要（产物清单、回合输入、尝试结果、事件、命令回执）与装前相同；安装日志已清，`upgrade-last` 为 `verified`。
- **新服务起不来**（候选的守护进程启动即退出）：10 秒后 `UPGRADE_ROLLED_BACK`，原因 `UPGRADE_START_FAILED`，“运行时与备份已还原”；服务回到 0.49.0，计数与五项摘要不变，安装日志已清。
- 每次演练结束都停掉服务、删掉持有副本的根目录。

