# Worker 可见性

本次微任务把 supervisor 已发布的事实接到 `health` 与 `workers`，并修正同一 Worker 标识重新注册时沿用前任状态的问题；不改控制台，不改 SQLite schema 或 supervisor 文件形式，不安装产品，最终验收由 Host 完成。

## 查询字段

`health.managedWorkerIds` 保持现有含义，只列当前期望的槽位；多余成员继续由 `surplusWorkerIds`、`surplusDraining`、`surplusRetained` 分别报告。新增 `workerSupervisors` 数组，每项为 `{workerId, supervisor}`，覆盖期望槽位及池清单中仍保留的所有多余成员，即使 Worker 从未成功注册、因而没有 `workers` 表行，也会列出。标识数量不表示运行数量；`health.status` 仍表示服务能响应查询，supervisor 的报告逐项查看 `workerSupervisors`。

`workers` 保留原来的 `workers` 数组与看板字段，在每行增加 `supervisor`，另增加与 `health` 同义的 `workerSupervisors` 数组。原来的 `state`、`adapter`、`limit` 参数只过滤看板行，不过滤 `workerSupervisors`，因此注册前持续崩溃的槽位不会被过滤或行数限制遮住；显式启动且已注册的非受管 Worker 也会读取自己目录里的 supervisor 文件，但不会因此被算作受管成员。

Host 在整合树的下游测试中发现，初稿把 `managedWorkerIds` 扩成期望槽位与多余成员的并集，破坏了缩容读者对该字段的既有判断；本轮恢复其原义，并删除守护进程报告中误加的同名键。池报告通过独立的内部 `supervisedWorkerIds` 提供 `managed_ids()` 的完整成员集合，服务每次查询为这些标识读取状态文件，公开查询统一命名为 `workerSupervisors`；这一集合不会从“运行中的多余成员”推断，因此持续起不来的多余成员也可见。既有多余成员字段、注册和中断结算修正保持原行为。

两个查询共用一处文件读取边界，每次查询读取 `<state>/workers/<workerId>/supervisor.json`，不创建目录、不写回文件、不缓存正常状态。`supervisor.state` 保留发布者原生值：`starting`（启动中）、`running`（运行）、`restarting`（重启中）、`retiring`（退役中）、`retired`（退役完成）、`stopped`（已停止）。文件缺失、不可读、JSON 或相关字段不合法、标识不符、时间缺失或无时区、重启状态缺少错误时，返回 `state: unknown`，其余事实字段为 `null`。路径使用现有 Worker 目录标识规则校验，不能借注册标识遍历其他目录。

`supervisor.lastError` 取文件中的 `lastError`，存在错误时 `lastErrorAt` 取该次发布的 `updatedAt`；`startedAt` 是该 supervisor 本次启动时间，`updatedAt` 是最近一次状态发布时间。现有 supervisor 每次发布都会替换整份文件，恢复运行后不再携带上一条错误，此时 `lastError` 与 `lastErrorAt` 均为 `null`；这两个字段不声称提供跨状态的历史错误记录。读取器不改变这一存储契约。

看板行的 `state` 与 `currentAttemptId` 是注册、认领、重连、续租、结果和恢复事务维护的事实；`supervisor` 是独立进程最近写下的报告。两者保持各自原义，不把 `restarting` 写进看板，不把看板的 `idle` 翻译成 supervisor 正常。文件中的 `running` 也不是额外的进程存活证明；该文件没有周期心跳，`updatedAt` 让调用方看到报告的时刻。

## `lost` 的读者与修正

对生产 Python 源码及控制台源码的检索表明，`lost` 由 `BoardStore.reconcile_startup` 在启动恢复时写入，也由 `interruption._settle` 对仍绑定旧执行的行写入。`BoardStore.worker_list` 接受 `state: lost` 过滤，`_worker_view` 向 `worker_list`、注册响应等通用展示暴露原值，CLI 的 `workers` 与 `BoardClient.workers` 透传这一查询。`db.WORKER_STATES` 仍把它列作合法看板值。控制台没有专门消费 Worker `lost` 状态的逻辑。

调度的 `worker_claim` 只特别拒绝 `stopping`，没有把 `lost` 当作进程存活或停止证明；续租与重连通过执行身份更新为 `busy`。中断恢复按 `attempts`、进程身份、关闭证据和 `attempt_interruptions` 工作，`worker_recover` 对 Worker 行只要求已注册，不依赖 `lost` 过滤候选。备份把 `workers` 作为运行时行处理，没有基于 `lost` 的决策。

现在已有标识重新注册会原子更新本次身份、能力、主机、PID、注册与最近观察时间，将 `state` 置为 `idle`、`currentAttemptId` 清空，并增加 revision、发布既有注册事件。注册命令回执的重放仍返回原结果。它不更改前任执行，不释放前任的资源声明，也不绕过执行实例的重连与所有权校验。

前任执行被确认中断时，原有 `attempt_interruptions` 行、执行结果中的 `terminationReason: interrupted`、关闭证据和 `task.failed` 事件记录该执行的结算。原有 Worker 更新按 `worker_id` 与 `current_attempt_id` 同时匹配：重新注册已解除旧指针，结算不会命中新进程。如果没有新的注册且该行仍指向已中断的前任执行，结算仍把这条前任记录置为 `lost` 并清空指针。`worker_claim` 还会按执行记录拒绝同标识尚有活动执行时的新认领（`WORKER_BUSY`），清空看板指针不会绕过此约束；新进程需先恢复或结算前任执行。注册、结算和新的认领都通过既有 SQLite 写事务串行化，不需要新增存储形式或升级。

## 验证

使用微任务指定的解释器及 `PYTHONPATH=src <指定解释器> -m hey_my_buddy.cli.checks <测试文件…>`，默认进程数；所有服务、Worker、故障注入和命令子进程均由检查运行器的私有根目录承载，没有模型调用。

第一轮运行 `blackboard/service/test_worker_visibility.py`、`blackboard/service/test_worker_pool.py`、`blackboard/store/test_interruption_recovery.py`：3 个文件、19 个测试通过，退出码 0，私有根目录已移除。私有真实服务与 CLI 覆盖注册前启动异常、`health` 与 `workers` 显示重启错误、恢复后显示运行与空闲；文件边界覆盖缺失和畸形数据、受管 surplus 成员、只读行为；恢复测试覆盖新注册解除旧执行指针。

扩展运行 `blackboard/service/test_daemon.py`、`blackboard/service/test_service.py`、`blackboard/service/test_worker_event_recovery.py`、`buddy/runtime/test_repair_recovery.py`、`buddy/runtime/test_worker_invariants.py`、`buddy/runtime/test_worker_idle.py`、`buddy/runtime/test_run.py`、`blackboard/store/test_worker_orphan_settlement.py`、`blackboard/store/test_interruption_native_tree.py`、`cli/test_cli.py`：10 个文件、116 个测试通过，退出码 0，私有根目录已移除。

补充测试最初尝试让新进程在前任结算前认领另一条微任务，检查因既有 `WORKER_BUSY` 约束而失败；确认执行记录的约束后，替换为注册先于启动恢复的有效顺序测试。另在真实 supervisor 被杀、子进程存活或一并消失的两种恢复测试中增加 `workers` 断言：替换进程仍存活、PID 对应替换进程、看板为 `idle`、旧执行指针为空、supervisor 报告 `running`。重新运行 `blackboard/store/test_interruption_recovery.py` 与 `buddy/runtime/test_repair_recovery.py`：2 个文件、23 个测试通过，退出码 0，私有根目录已移除。三轮成功运行覆盖当时源码的 13 个不同测试文件、136 个不同测试，均无跳过。

本轮回修补强了多余 Worker 重启失败与状态缺失的查询断言，以及真实缩容时 `managedWorkerIds` 与 `workerSupervisors` 各自的成员断言；重新运行上述 13 个文件和 `blackboard/tasks/test_parallel_dispatch.py`，包含 Host 指定的 `blackboard/service/test_daemon.py`：14 个文件、163 个测试全部通过，无跳过，退出码 0，私有根目录已移除。`test_parallel_dispatch.py` 耗时 75.8 秒，`test_repair_recovery.py` 耗时 160.3 秒；此后只补录本段验证结果。

本记录是该检出的测试证据，不表示日常安装或 Host 已验收；没有运行完整检查。
