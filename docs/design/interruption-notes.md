# 中断后的接续实现说明

执行的完整身份保存在 SQLite 的 `attempt_process_identities`：开机 UUID、根进程号、启动时刻、POSIX 进程组或 Windows 命名 Job，以及原 Worker 的开机 UUID、进程号和启动时刻；原生进程组另存 `attempt_native_process_identities`。同一执行在确认前一组已经停止后进行预模型重试时，按序追加根身份并保留前一组的停止事实，不覆盖历史身份；这些表与 `attempt_legacy_starts`、`attempt_interruptions` 一起并入现有 schema 19 到 20 的升级，没有增加另一个版本的升级或隐式启动迁移。

`protocol/process_identity.py` 使用 BSD 许可的 psutil 7.2.2 读取进程创建时刻、进程状态、进程枚举和存量判断所需的开机时间。稳定的开机身份分别读取 macOS 的 `kern.bootsessionuuid`、Linux 的内核 boot UUID 和 Windows 的 BootIdentifier；不以会受校时影响的 `psutil.boot_time()` 数值充当开机身份。Win32 的中立操作在 `protocol/windows_process.py`，供黑板与 Worker 共用；Windows 的 Job 使用唯一名称，保留原来的挂起创建、先加入 Job 再运行及最后关闭句柄时终止整组的行为。

Worker 在拉起外层子进程后立即把完整身份写入持久 `spawn.marker`，再通过 `worker_progress` 写入黑板；服务暂时不可达时用同一身份重发，新 Worker 也能用原启动意图的 capability 补发已经落盘的身份，但不能接管原生句柄。控制进程在任何原生进程启动前在固定的私有 attempt 根目录建立绑定该执行的 `native-process-journal-<pid>-<start>.json`，每次启动先写意图，取得完整身份后再消掉该意图；版本探测等内部子进程也进入日志，防止只看外层控制进程就误判整个执行停止。

三条证据规则的 OS 核验在 `protocol/process_identity.py`，黑板的判定与结算在 `blackboard/tasks/interruption.py`。开机身份改变证明旧进程全部结束；同一次开机内必须核对进程号与启动时刻，并确认其组没有成员；身份仍在时仅当原 Worker 的完整身份已经不存在，新 Worker 才能当场重新核对并停止整组，再确认不存在。POSIX 上先确认外层控制进程消失，再检查已稳定的原生进程日志及其中的所有组；Windows 的外层 Job 还包含嵌套 Job 的进程。进程号被复用不会授权发信号，观测失败保持未知；Windows Job 为空但原根进程身份仍存在时也保持未知。

服务启动时在 `reconcile_startup` 标记未知后判一次，Worker 启动及空闲等待的超时后通过 `worker_recover` 判一次，服务已有的租约清扫也判一次。租约到期本身只写未知；原 Worker 的完整身份仍存在时保留其收集结果、重放回执与 reconcile 的权利，包括子进程在 Worker 暂停期间已经完成的情况。

新的终止原因是 `interrupted`，证据值为 `boot-changed`、`identity-absent` 或 `worker-stopped`，保存在结果的 `interruptionEvidence` 中，并随 `get`、执行记录与 `await` 的简报显示。中断回合的状态是 `interrupted`；微任务停到现有的失败边界，停止证据、回合结算、资源释放和事件在同一事务提交；中断回执也必须有确认停止和有效证据，不能仅凭租约标为中断，Host 决定是否继续。Lead 名下的微任务使用已有的 `workflow.turn_failed` 唤醒机制，不会自行续接或重试这个微任务。

证明停止后先在 `attempt_interruptions` 记录封存占用，阻止并发的旧回执改变已证停止的执行，再在 SQLite 写事务外封存检出现状。Git 封存失败会保存错误并由后续清扫重试，不会丢弃部分产物或提前把未封存的回合结算。成功后部分产物固定为 `partial-output`，标为 `verified=false`、`final=false`；已有的 continuation 准备从它的固定提交派生下次输入，因此未封存改动不会挡住正常接续。

会话方式在下一回合启动前按声明的能力及该执行已经持久保存的会话记录决定。Codex CLI 的精确 checkpoint、原生回合终态和绑定均成立，或 DSH CLI 的持久会话记录、终态和绑定均成立时，可以接回原生会话；原生回合没有明确结束或无法绑定时重建会话。ZCode CLI 与 Claude Code CLI 的中断回合重建会话。重建输入写明上一回合被中断、停止证据及未经验证的部分产物位置，没有发生原生续接报错后的回退。

schema 19 的未知执行在升级时显式写入 `attempt_legacy_starts`，它是无完整身份的存量事实，不是完整身份的另一种格式。开始时刻早于本次开机的存量按 `boot-changed` 封存和结算；其余保留未知。升级的空闲门槛只为有这条开机证据的存量放行，不为其他在跑或未知工作放行。新执行缺少身份时不会套用存量规则。

真实进程故障注入通过：杀 Worker 留下子进程时，由新 Worker 核验后整组停止并记录 `worker-stopped`；Worker 与子进程一起结束时记录 `identity-absent`；暂停 Worker 超过实际租约并等到周期清扫标记未知，再恢复后同一执行重新接上且只运行一次；服务断线期间完成的持久回执只重放、不重跑。离线 DSH CLI 首回合写部分文件后报告网络错误，失败回合封存部分产物，显式 continue 从固定提交接上并完成；这些均在私有服务与测试进程中执行。

指定的已有测试解释器未安装新增 psutil，且任务禁止往该解释器环境写入。验证时把锁定的 wheel 解压到忽略的 `tmp/test-dependencies`，通过临时的测试目录 `sitecustomize.py` 给检查运行器的私有子进程提供该依赖；未安装产品或改动指定解释器，临时加载文件在交付前移除，供复核的副本及原始检查日志保留在忽略的 `tmp/`。两项专门验证 source 环境的文件复跑时没有启用临时的子进程路径补充，因此仍检查原来的严格环境契约。

Linux 和 Windows 的实际 OS 调用没有在本机运行，分支判断、命名 Job 的重新打开及查询和停止使用可控来源测试；macOS 的真实进程调用已在私有进程测试中执行。没有运行真实 harness 的原生探测或任何付费模型调用；完整检查的既有前端预览测试会运行 `tests/probes/` 下的合成预览夹具，未直接调用其中的真实 harness 探针。

可核验性有两个明确边界：POSIX 根进程已消失而原进程组仍有成员时，光凭组号不能证明它没有被复用；控制进程的原生身份日志缺失、未绑定或仍有启动意图时，也不能证明原生组全部结束。这些情况保留未知，不发出无身份证明的信号；换次开机的证据仍可结算。无法把某条原生会话记录明确绑定到该执行时重建会话。独立检查还发现服务先结算孤儿、候选列表不再包含它时，本地孤儿记录可能阻止退役；已补充权威终态观察并以真实黑板的替代 Worker 流程验证清理，只在确认执行已经结束后清除本地记录。要求差异仅剩下述前端预览依赖阻塞；Linux 与 Windows 的真机验证按任务要求留给对应平台。

验证全部使用任务指定解释器和检查运行器：完整检查仅运行一次，不带文件，251 个文件中 241 个通过；其余九个代码测试文件修复后单文件复跑通过，另一个文件因前端开发依赖缺失阻塞。没有第二次运行完整检查，所有私有检查根均由运行器确认清理，未提交或安装产品。

最终通过的定向文件包括 `tests/python/protocol/test_boot_identity_platforms.py`、`test_process_identity.py`、`test_native_process_journal.py`，`tests/python/buddy/runtime/test_windows_process.py`、`test_worker_invariants.py`、`test_worker_live_wiring.py`、`test_repair_recovery.py`，`tests/python/blackboard/store/test_blackboard.py`、`test_interruption_recovery.py`、`test_interruption_native_tree.py`、`test_worker_orphan_settlement.py`、`test_upgrade_fixture.py`，`tests/python/blackboard/tasks/test_interrupted_continuation.py`、`test_network_continuation.py`、`test_lead_lifecycle.py`、`test_parallel_dispatch.py`、`test_wrap_up.py`，`tests/python/buddy/harnesses/claude/test_claude.py`、`tests/python/buddy/harnesses/test_private_adapter_invariants.py`、`tests/python/buddy/roles/test_run_controller_signals.py`、`tests/python/buddy/harnesses/test_harness_discovery.py`，以及 `tests/python/buddy/runtime/test_worker_runtime.py`、`tests/python/install/test_runtime.py`、`tests/python/install/test_upgrade_waiting_host.py`、`tests/python/cli/test_blocking.py`、`tests/python/cli/test_cli_views.py`；会话选择相关的 DSH CLI、Codex CLI 和权限续接文件也在定向检查与完整检查中通过。

唯一未通过的检查是 `tests/python/cli/test_host_preview.py`：其合成夹具自检及确定性检查通过，但真实前端解析器测试因 `apps/console/node_modules/vitest/vitest.mjs` 不存在而失败。`apps/console/node_modules` 不在冻结写入范围内，因此未执行会向该目录写入的依赖安装，也未绕过或跳过检查；需要 Host 准备控制台开发依赖，然后用指定检查运行器单独执行此文件并取得退出码 0。
