# ADR-031 M1：执行进程

2026-10-15，M1 源码实现说明；没有安装，最终验收由 Host 完成。

## 入口与启动材料

入口是 `python -m hey_my_buddy.buddy.runtime.execution --startup <私有文件>`。`launch_execution` 把认领得到的任务、执行、回合、Router 输入、尝试凭据，以及 `workerId`、nonce、租期和明确的状态根 fsync 写入执行目录的 `execution-start.json`，权限为 0600，目录权限为 0700；命令行只传该文件的路径，不传载荷或凭据，启动环境也移除继承的 Worker 身份与尝试凭据。确认原生运行停止后，共享交付路径删除启动材料；准备失败的已写出凭据也经过同一处清理。

## 一个进程内的结构

Worker 认领之后用现有 `owned_popen` 创建新会话中的执行进程，先保存 `spawn.intent`，拿到句柄后保存 `spawn.marker` 并发布完整进程身份。执行进程负责预检、准备、Router 预算、Host 停止宽限、收集、终止原因、部分产出、凭据清理和回执；harness 通过进程内的 `InlineRunHandle` 运行线程直接调用 `run_harness`，请求、结果与角色判定都是对象，命令仍由 `CommandAdapter` 启动自己的子进程组或 Windows Job。

原生调用线程继承所选 harness 的 contextvars；Codex CLI、Claude Code CLI、ZCode CLI 与 DSH CLI 的启动环境取自这次调用的上下文，不再依赖已删除的控制进程来接收环境。模型启动前的可重试失败仍只重试一次，新起原生进程，执行进程的身份不变；每次原生调用使用独立的私有目录，保留此前的原生证据。

## 在线通道与停止

每次原生运行仍注册 `HarnessRunLive`；总结回合另起端点。执行目录的私有 `execution-live.json` 只交接当前端点身份、描述与令牌，Worker 在核对任务、执行、代次、回合、harness、执行进程号与 C-Two 域后建立通道，端点更换时撤回旧绑定再绑定新端点。服务仍经 `WorkerRuntimeLive` 转发，直接连接执行端点和长调用留给 M2。

Worker 继续每 2 秒读取停止意图，按服务保存的绝对宽限计算同机单调时钟期限，写入绑定当前执行的私有 `execution-stop.json` 后发送 SIGTERM。执行进程只在信号处理器中记录意图，由本地控制线程传给原生调用；Host wrap-up 仍可续接一个总结回合，第二次信号或控制台升级走强停，原期限不延长。Router 预算由执行进程本地执行，服务失联不取消它。活动观察失败不会终止负责停止的控制线程。

`shutdownConfirmed` 的含义覆盖本次执行为运行创建的全部进程。命令依靠实际命令进程组的停止事实，harness 依靠原生 `RunResult` 的 `group_state == "gone"`；前一次原生调用未确认停止，不进行预模型重试或总结续接。执行进程本身在交付回执之后退出，它仍活着不妨碍原生停止证明成立；执行进程退出本身也不能证明原生进程已停。

## 回执与交付

执行进程先将完整回执 fsync 写入执行目录的 `result-receipt.json`，然后用 `worker_result` 交付，传输或未决服务错误按退避持续重试，活着就不放弃。服务明确确认或拒绝后，先 fsync 写入 `result-delivery.json`，再删除待交回执，因此 Worker 能区分正常交付与交付前崩溃；相同 `commandId` 继续保证丢失回复后的幂等重放。确定性的参数、权限、代次或产出校验拒绝会结束交付。

活动和等待原因只在执行进程内存中保留各自最新的一份，交付线程与活动线程合并并发更新；重连后先尝试交这份提示，再交回执，提示失败不阻止回执尝试。原生运行在服务离线期间照常结束。回执本身保留 nonce，所在目录保持私有；没有另建 Worker 回执队列。

Worker 等执行进程退出后读取同一目录：已有交付确认记录就结束，尚有回执就调用同一段无限重试的交付代码，没有回执就持久化并交付 `shutdownConfirmed: false` 的失败。Worker 目录只保留调度与自身恢复所需的启动意图、孤儿记录、停止及退休意图；Worker 池通过这些意图查找执行目录的回执，孤儿意图所指的回执也会阻止过早退休。备份也取消对 Worker 回执目录的读取，单独按完整文件名保存执行回执与交付确认，不把它们当作公开产出证据；启动材料、在线端点令牌和停止控制文件均不进入备份，执行进程的 stdout 与 stderr 日志则作为明确声明的证据保存。

## 原生进程日志与本次范围修订

Host 同意每次执行都记录原生进程日志，恢复统一读取，不再按适配器决定是否读取。执行入口在启动任何原生工作前安装日志，命令也通过同一个 `owned_popen` 与 `ProcessHandle` 记录身份。`blackboard/tasks/interruption.py::_native_journal` 删除适配器排除，`_native_identities` 删除“没有日志描述即没有原生进程”的分支，`worker_recover` 对根身份仍活着或已消失的候选都给出同一份日志描述；Worker 孤儿处理沿用这些候选与日志。

执行进程组消失而命令仍活着，停止证据仍为存活；命令也消失后才可判为停止。日志缺失、存在未决创建意图或身份观察失败仍为未知。没有改变服务调度、认领、`workers` 表、租约列、`attempt_process_identities` 的身份链或 schema 22，也没有开始升级。

## 删除与后续

运行控制入口只剩模型发现；Worker 回合、fast、review 的控制进程启动、控制文件、公共请求帧文件、stdout 结果帧、角色 verdict 文件与总结身份交接文件已由进程内对象替换。Worker 的执行主体迁入执行入口，续租线程不再读取或上交原生活动，120 秒结果重试上限与 Worker 目录的结果回执队列取消；对应测试改到新的边界，真实 harness 探针只修改、未运行。

留给后续的是服务的 Worker 池和 supervisor、调度与认领、租约和续租、Worker 身份及目录、服务经 Worker 的实时转发、服务重启后的接回与孤儿恢复主体，以及旧运行时保留、安装与升级规则。服务端的修改限于上述统一日志读取及 Worker 池、备份的回执读取配套更改；本次未实现 M2 至 M6，也未改 `docs/reference`。

## 验证

Python 检查均通过 Host 指定的解释器与 `python -m hey_my_buddy.cli.checks <文件或目录>` 运行，使用默认进程数；没有向该解释器的环境安装内容，没有运行完整检查、真实 harness 探针或日常服务操作。检查通过结果如下；各批有重复覆盖，不把用例数相加，也不把本说明当作 Host 验收。

直接执行进程测试覆盖命令与离线 DSH CLI 夹具、命令及夹具回合期间杀掉私有服务再启动、离线完成后先交原生活动再交结果、结果被拒后退出、Worker 对已有回执及没有回执的崩溃回退，以及活动通道故障不阻断停止、确定性校验拒绝不重试、准备失败清理凭据。Windows 继续使用现有 `owned_popen` 创建机制，本机未验证 Windows 与 Linux 上的实际进程或 C-Two 行为。

- `tests/python/buddy/roles/`：17 个文件、198 个用例通过；角色与相关路由的组合批次 20 个文件、251 个用例通过。
- `tests/python/buddy/harnesses/`：目录批次中 56 个文件通过，剩余的 ZCode 问询文件修正包装后 47 个用例通过；删除只测已替代 Router 外层停止规则的测试后，控制器文件 38 个用例通过。所有 harness 测试使用离线夹具。
- `tests/python/buddy/runtime/`：13 个文件、133 个用例通过。之后新增的私有停止信号用例及最后的监督失败回退改动，经执行入口、Worker 不变量与 C-Two 集成三个文件复查，43 个用例通过，其中执行入口有 13 个用例。
- `tests/python/blackboard/service/`、`tests/python/blackboard/routing/`、`tests/python/protocol/`、`tests/python/install/`，加上全部 `test_workflow*.py`、并行派发、已中断回合及所改存储测试：整合批次共 96 个文件，其中 93 个通过；另外三个文件的旧进程组或 stdout 结果帧断言已修正，`test_workflow_real.py`、`test_workflow_worker.py` 与 `test_ctwo_integration.py` 均在修正后复查通过。
- 最后一次执行及恢复组合检查覆盖真实 Worker 回合、执行入口、预检等待、Worker 不变量、崩溃恢复与 C-Two 通道，6 个文件、66 个用例通过；真实回合通过服务的最终活动投影检验完成事实，不读取已删除的 stdout 结果帧。
- 命令日志的恢复与 Worker 事件恢复两个文件合计 13 个用例通过；存储主测试 54 个用例通过；并行派发 27 个用例通过；备份、备份预检和 Windows 模拟备份均已取得通过结果。`git diff --check` 通过。

较早的内存看板测试曾错误地启动真实子进程，而该夹具跳过 C-Two，导致等待不存在的服务；这些测试已迁移到显式的进程内执行夹具，真实跨进程测试仍使用私有服务。相应停滞测试已中止并核对身份清理，仅保留检查运行器的临时证据目录；最终通过的检查均报告私有根已清理，没有遗留本轮测试进程。完整检查仍按任务要求由 Host 整合后、安装前运行。
