# Host 回复里的等待数量

计数统一放在 `blackboard/service/service.py` 的 `_BaseResource._guard`，操作处理完后、回复编码前附加顶层 `waitingForHost`。只有服务令牌作用域的请求附加；带执行凭据的 Lead 或 Worker 请求不附加。版本号与存储形式没有改动。

优先采用请求的 `hostId`；没有时，通过 `runId`（执行接口的同义参数 `taskId` 也适用）查 `workflow_runs.host_id`，或通过 `requestId` 联结 `tasks` 与 `workflow_runs` 查同一字段。控制文件由 CLI 现有的 `_apply_control` 注入 `hostId`，不增加控制文件读法。未指向微任务、微任务没有 Host、或没有这些参数时不附加数量。

`_waiting_for_host` 先从请求读取 Host 与微任务标识；没有 `hostId`、`runId`／`taskId`、`requestId` 时直接返回，不开启读事务，因此 `ping` 与不带这些标识的 Worker 进度等请求不会为计数访问存储。需要计数时，在一次只读查询中使用 `micro_task.TASK_HISTORY_SQL`、`history_where(host_id=...)` 与 `FILTER_SQL` 的 `host`、`review` 条件聚合。它与 `list` 加相同 `hostId`、相应 `filter`、其余参数取默认值的 `total` 使用相同关系和条件；不采用当前命令的分页、搜索、状态或 `rootsOnly` 参数。只输出非零键，两项都为零时整个字段消失，不读取标题或明细。

CLI 的任务解包、公共简略视图入口与 `await` 信封只透传这个字段，不另算数量。`await` 在 Host 边界读取结果后，采用结果回复中的最新数量；结果回复省略字段时也清除先前状态回复中的数量。

本轮先用 Host 指定的解释器与检查运行器单跑 `tests/python/blackboard/service/test_liveness.py`，复现 `test_ping_uses_no_storage_or_runtime_inspection` 失败，再将参数检查移到读事务之前。没有另加测试，现有回归测试直接约束 `ping` 不访问存储或运行时检查。

最终检查使用同一指定解释器，进程数取默认值：`PYTHONPATH=src <指定解释器> -m hey_my_buddy.cli.checks tests/python/blackboard/service/ tests/python/protocol/ tests/python/cli/test_blocking.py tests/python/cli/test_checks_parallel.py tests/python/cli/test_checks_focused.py tests/python/cli/test_cli.py tests/python/cli/test_cli_views.py tests/python/cli/test_checks_cleanup.py tests/python/cli/test_support_cleanup.py tests/python/cli/test_harness_test_safety.py tests/python/cli/test_checks_uv_cache.py tests/python/cli/test_skill_workflow.py tests/python/cli/test_repository_hygiene.py tests/python/cli/test_host_cli.py`。44 个文件、590 项测试全部通过，无跳过；运行器退出 0，私有根目录已清理，包含 `test_liveness.py` 与 `test_host_waiting_counts.py`。

上一轮检查的唯一失败是预览测试缺少 Vitest 开发依赖；Host 本轮告知，已在具备 Node 24 与前端依赖的合并树上运行 `tests/python/cli/test_host_preview.py` 并通过。依照 Host 指令，本轮从 CLI 范围中仅排除这个文件，未重复运行或自行准备依赖；该文件的通过证据来自 Host。

新增私有服务测试通过真实 CLI 与 C-Two 服务检查简略及完整回复、Host A 的一个 Host 边界与两个已交付微任务、Host B 无等待、提交重放、按请求编号读取、控制文件验收、写结论、取消后省略字段，以及数量与 `list.total` 一致。已交付与 Host 边界由现有合成回合夹具准备，没有运行原生模型；私有 Worker 实际运行 `/bin/echo` 并由 `await` 读取结果。进程内测试另覆盖新提交、继续后的数量、超过默认分页大小、搜索不改变数量，以及 Lead 执行凭据不附加字段。日常服务、Worker、安装、版本号、升级、`docs/reference` 与共享 skill 均未改动，最终验收由 Host 完成。
