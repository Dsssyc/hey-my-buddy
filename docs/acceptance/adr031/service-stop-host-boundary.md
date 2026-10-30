# 停服务时保留被动的 Host 边界：源码验证

2026-10-30，Worker 在 macOS 上验证。基于输入提交 `19e61b9e` 的源码候选；这里只记录私有黑板和模拟 harness 的检查结果，等待 Host 验收，尚未安装。没有调用模型，没有访问日常黑板或重启日常服务，没有运行完整检查。

## 改动

`blackboard/store/work.py` 的 `autonomous_tasks(connection)` 是停止服务和安装空闲规则共用的查询。它保留 `queued`、`running`、`cancelling` 和 `reconciliation-needed` 的任务，仅排除处于 `queued`、对应微任务处于 `awaiting-host` 且没有 `recorded` 或 `queued` 续接的任务。未解决的执行仍由各自原有的执行查询处理。

`install/upgrade.py` 删除原有的内联判定，调用共用查询；它已经依赖黑板的存储、备份与升级模块，新增导入也归黑板存储，不引入黑板与 buddy 两侧之间的引用。`BoardStore.autonomous_tasks()` 在读事务里调用查询；服务的 `cancel_queued` 只取消其中仍为 `queued` 且未被工作流处理的任务。`cancel_for_service_stop` 在自己的写事务中使用同一查询选择排队微任务，继续通过原有的工作流取消路径处理续接与拥有的任务。`active_work()` 的完整库存保持原有含义。

`cancel-queued` 的记录与清除逻辑没有新增分支：一次没有取消失败的停止照原有路径清除旧问题。原有守卫和 Host 授权要求继续生效。待办中的旧问题条目删除，运行规则记入 [operations](../../reference/operations.md)。

## 检查

所有命令均从检出根运行，经检查运行器使用 `--jobs 1`。启动运行器时清除了继承的状态、运行时、代理凭据与虚拟环境绑定；私有根使用运行器默认的系统临时目录，各次运行结束均确认移除。原始日志保留在检出中被忽略的 `tmp/stop-host-boundary/`。

先只加强服务停止测试，再在原实现上执行下列命令，5 项中有 3 项失败，均观察到不该发生的底层 `task_cancel` 调用；运行器退出 1，私有根清理完成。日志为 `fail-first.log`。

```sh
uv run --frozen python -m hey_my_buddy.cli.checks --jobs 1 tests/python/blackboard/service/test_micro_task_service_stop.py
```

修改实现及原有取消失败测试后，下列命令通过 3 个文件、27 项检查，退出 0。日志为 `focused-core.log`。

```sh
uv run --frozen python -m hey_my_buddy.cli.checks --jobs 1 \
  tests/python/blackboard/service/test_micro_task_service_stop.py \
  tests/python/blackboard/service/test_daemon.py \
  tests/python/install/test_upgrade_waiting_host.py
```

随后在同一混合库存中补齐普通排队执行、Host 边界上的两种待执行续接和正常执行态的续接，下面 7 个文件、109 项检查通过，退出 0。日志为 `focused-regressions.log`。

```sh
uv run --frozen python -m hey_my_buddy.cli.checks --jobs 1 \
  tests/python/blackboard/service/test_micro_task_service_stop.py \
  tests/python/install/test_upgrade.py \
  tests/python/blackboard/tasks/test_workflow_cancellation.py \
  tests/python/blackboard/tasks/test_workflow_boundaries.py \
  tests/python/blackboard/tasks/test_workflow_stop_surface.py \
  tests/python/blackboard/tasks/test_lead_shutdown.py \
  tests/python/blackboard/tasks/test_wrap_up.py
```

回归证明：仅有 assistance 与 attention 的被动 Host 边界时，安装判空闲，停止不调用底层取消、不产生服务问题，数据库快照不变；放入一条旧 `cancel-queued` 问题后，下一次干净停止通过原有逻辑记录 `service.issue_resolved`。混合库存中，安装报告的自主任务集合恰好对应停止取消的任务集合；待执行续接经工作流取消，只有普通排队执行调用底层取消，未解决子执行继续报告实际停止证据。
