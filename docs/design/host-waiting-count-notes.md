# Host 回复里的等待数量

计数统一放在 `blackboard/service/service.py` 的 `_BaseResource._guard`，操作处理完后、回复编码前附加顶层 `waitingForHost`。只有服务令牌作用域的请求附加；带执行凭据的 Lead 或 Worker 请求不附加。版本号与存储形式没有改动。

优先采用请求的 `hostId`；没有时，通过 `runId`（执行接口的同义参数 `taskId` 也适用）查 `workflow_runs.host_id`，或通过 `requestId` 联结 `tasks` 与 `workflow_runs` 查同一字段。控制文件由 CLI 现有的 `_apply_control` 注入 `hostId`，不增加控制文件读法。未指向微任务、微任务没有 Host、或没有这些参数时不附加数量。

`_waiting_for_host` 在一次只读查询中，使用 `micro_task.TASK_HISTORY_SQL`、`history_where(host_id=...)` 与 `FILTER_SQL` 的 `host`、`review` 条件聚合。它与 `list` 加相同 `hostId`、相应 `filter`、其余参数取默认值的 `total` 使用相同关系和条件；不采用当前命令的分页、搜索、状态或 `rootsOnly` 参数。只输出非零键，两项都为零时整个字段消失，不读取标题或明细。

CLI 的任务解包、公共简略视图入口与 `await` 信封只透传这个字段，不另算数量。`await` 在 Host 边界读取结果后，采用结果回复中的最新数量；结果回复省略字段时也清除先前状态回复中的数量。

最终检查使用 Host 指定的解释器与检查运行器，进程数取默认值：`PYTHONPATH=src <指定解释器> -m hey_my_buddy.cli.checks tests/python/cli/ tests/python/blackboard/service/test_host_waiting_counts.py tests/python/blackboard/service/test_service.py tests/python/blackboard/service/test_reminders.py tests/python/blackboard/tasks/test_lead_owned.py tests/python/blackboard/tasks/test_micro_task_history.py tests/python/blackboard/tasks/test_workflow_stop_surface.py tests/python/blackboard/tasks/test_reminders.py tests/python/protocol/test_ctwo_service.py tests/python/protocol/test_transport_attach.py`。

检查结果为 22 个文件中 21 个通过，通过文件合计 300 项测试、无跳过；运行器退出 1，私有根目录已清理。唯一失败文件为 `cli.test_host_preview`，其中真实前端解析器测试因缺少 `apps/console/node_modules/vitest/vitest.mjs` 而失败，该文件另外两项测试通过。准备控制台开发依赖涉及本微任务写入范围以外的目录，未执行；Host 准备依赖后需用同一检查运行器补跑 `tests/python/cli/test_host_preview.py`。

新增私有服务测试通过真实 CLI 与 C-Two 服务检查简略及完整回复、Host A 的一个 Host 边界与两个已交付微任务、Host B 无等待、提交重放、按请求编号读取、控制文件验收、写结论、取消后省略字段，以及数量与 `list.total` 一致。已交付与 Host 边界由现有合成回合夹具准备，没有运行原生模型；私有 Worker 实际运行 `/bin/echo` 并由 `await` 读取结果。进程内测试另覆盖新提交、继续后的数量、超过默认分页大小、搜索不改变数量，以及 Lead 执行凭据不附加字段。日常服务、Worker、安装、版本号、升级、`docs/reference` 与共享 skill 均未改动，最终验收由 Host 完成。
