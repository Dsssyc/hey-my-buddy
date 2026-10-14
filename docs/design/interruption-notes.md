# 中断后的接续实现说明

执行的完整身份保存在 SQLite 的 `attempt_process_identities`：开机 UUID、根进程号、启动时刻、POSIX 进程组或 Windows 命名 Job，以及原 Worker 的开机 UUID、进程号和启动时刻；原生子进程身份保存在绑定该执行的持久启动日志中。同一执行在确认前一组已经停止后进行预模型重试时，按序追加根身份，不覆盖历史身份；允许追加本身要求原 Worker 明确报告前一组已经停止；这些表与 `attempt_legacy_starts`、`attempt_interruptions` 一起并入现有 schema 19 到 20 的升级，没有增加另一个版本的升级或隐式启动迁移。

`protocol/process_start.py` 读取稳定的内核原始创建值：Linux 读取 `/proc/<pid>/stat` 第 22 项，即自开机以来的节拍数；macOS 通过公开的 libproc `proc_pidinfo(PROC_PIDTBSDINFO)` 读取原始 `pbi_start_tvsec` 与 `pbi_start_tvusec`，合成整数微秒；Windows 通过 `GetProcessTimes` 读取创建 FILETIME 的整数 100 纳秒节拍。身份的 `startedAt` 与 SQLite 两列均为整数，不做墙钟或开机时间换算，不经过浮点，不调用 psutil 的私有接口或 `Process.create_time()`；开机身份的平台前缀明确了单位。BSD 许可的 psutil 7.2.2 仅用于旧记录判断所需的开机墙钟。稳定的开机身份分别读取 macOS 的 `kern.bootsessionuuid`、Linux 的内核 boot UUID 和 Windows 的 BootIdentifier。Win32 的中立操作在 `protocol/windows_process.py`，Windows Job 使用唯一名称，并保留挂起创建、先加入 Job 再运行、最后关闭句柄时终止整组的行为。

Worker 在拉起外层子进程后立即把完整身份写入持久 `spawn.marker`，再通过 `worker_progress` 写入黑板；服务暂时不可达时用同一身份重发，新 Worker 也能用原启动意图的 capability 补发已经落盘的身份，但不能接管原生句柄。控制进程在任何原生进程启动前在固定的私有 attempt 根目录建立绑定该执行的 `native-process-journal-<pid>-<start>.json`，每次启动先写意图，取得完整身份后再消掉该意图；版本探测等内部子进程也进入日志，防止只看外层控制进程就误判整个执行停止。

三条证据规则的 OS 核验在 `protocol/process_identity.py`，黑板的判定与结算在 `blackboard/tasks/interruption.py`。开机身份改变证明旧进程全部结束；同一次开机内必须核对进程号与原始启动值，并用原子的 `os.killpg(pgid, 0)` 确认 POSIX 组不存在，只有 ESRCH 算没有成员，不扫描进程表；身份仍在时仅当原 Worker 的完整身份已经不存在，新 Worker 才能当场重新核对并停止整组，再确认不存在；POSIX 先发 SIGTERM，使用与 `ProcessHandle` 相同的 `COMMAND_STOP_GRACE_SECONDS`（3 秒），仍能核对完整身份时再发 SIGKILL，并从发出 KILL 后另行观察 2 秒。POSIX 上先确认外层控制进程消失，再检查已稳定的原生进程日志及其中的所有组；Windows 的外层 Job 还包含嵌套 Job 的进程。进程号被复用不会授权发信号，观测失败保持未知；Windows Job 为空但原根进程身份仍存在时也保持未知。

结算仅由服务启动的 `reconcile_startup` 与已有的租约清扫调用 `recover`；Worker 启动与未知事件触发的 `worker_recover` 只列出可核验的孤儿、停止并上报证据，不触发完整恢复扫描或 Git 封存。服务独立确认整个执行无存活进程后记录证据，围住迟到的旧回执，由下次服务清扫封存和结算；已经确认的证据保持有效，不重复观测操作系统。候选列表排除已记录停止证据的执行，定向上报只查询相应执行。租约到期本身只写未知；原 Worker 的完整身份仍存在时保留其收集结果、重放回执与 reconcile 的权利，包括子进程在 Worker 暂停期间已经完成的情况。

新的终止原因是 `interrupted`，证据值为 `boot-changed`、`identity-absent` 或 `worker-stopped`，保存在结果的 `interruptionEvidence` 中，并随 `get`、执行记录与 `await` 的简报显示。结算清掉原 Worker 的当前执行并保留 `lost`，不会将已不存在的 Worker 写回 `idle`。中断回合的状态是 `interrupted`；微任务停到现有的失败边界，停止证据、回合结算、资源释放和事件在同一事务提交；只有服务恢复路径能生成中断结果，`worker_result` 无条件拒绝 Worker 报告 `interrupted`；不能仅凭租约标为中断，Host 决定是否继续。Lead 名下的微任务使用已有的 `workflow.turn_failed` 唤醒机制，不会自行续接或重试这个微任务。

证明停止后先在 `attempt_interruptions` 记录封存占用，阻止并发的旧回执改变已证停止的执行，再在 SQLite 写事务外封存检出现状。临时 Git 或文件系统错误保存原因并由服务后续清扫重试；确定性的 `WORKSPACE_SCOPE_VIOLATION` 与 `WORKSPACE_CHANGED` 则填入正常结束回合已有的 `workspaceSealError` 与外层错误，照常确认停止、结算和释放名额。范围越界使用已有的 Host 冲突恢复出口，只读检出变更使用普通失败出口；没有另造封存错误状态。成功后部分产物固定为 `partial-output`，标为 `verified=false`、`final=false`；已有的 continuation 准备从它的固定提交派生下次输入，因此未封存改动不会挡住正常接续。

会话方式在下一回合启动前按声明的能力及该执行已经持久保存的会话记录决定。Codex CLI 的精确 checkpoint、原生回合终态和绑定均成立，或 DSH CLI 的持久会话记录、终态和绑定均成立时，可以接回原生会话；原生回合没有明确结束或无法绑定时重建会话。ZCode CLI 与 Claude Code CLI 的中断回合重建会话。重建输入写明上一回合被中断、停止证据及未经验证的部分产物位置，没有发生原生续接报错后的回退。

schema 19 的未知执行在升级时显式写入 `attempt_legacy_starts`，它是无完整身份的存量事实，不是完整身份的另一种格式。开始时刻严格早于本次开机至少一小时的存量按 `boot-changed` 封存和结算；其余保留未知。升级的空闲门槛只为有这条开机证据的存量放行，不为其他在跑或未知工作放行。新执行缺少身份时不会套用存量规则。一小时余量保留开机后早期向前校时造成的近期开机歧义，恰好在余量边界的也保持未知；开发者描述的重启前约一个半小时的两例仍满足规则。旧记录只有墙钟，没有原始开机身份，不能还原任意幅度的历史校时；该余量是开发者要求的保守存量门槛，不是新执行的停止证据。

真实进程故障注入通过：杀 Worker 留下子进程时，由新 Worker 核验后整组停止并记录 `worker-stopped`；Worker 与子进程一起结束时记录 `identity-absent`；暂停 Worker 超过实际租约并等到周期清扫标记未知，再恢复后同一执行重新接上且只运行一次；服务断线期间完成的持久回执只重放、不重跑。离线 DSH CLI 首回合写部分文件后报告网络错误，失败回合封存部分产物，显式 continue 从固定提交接上并完成；这些均在私有服务与测试进程中执行。

指定的已有测试解释器未安装新增 psutil，且任务禁止往该解释器环境写入。验证时把锁定的 wheel 解压到忽略的 `tmp/test-dependencies`，通过临时的测试目录 `sitecustomize.py` 给检查运行器的私有子进程提供该依赖；未安装产品或改动指定解释器，临时加载文件在交付前移除，供复核的副本及原始检查日志保留在忽略的 `tmp/`。两项专门验证 source 环境的文件复跑时没有启用临时的子进程路径补充，因此仍检查原来的严格环境契约。

Linux 和 Windows 的实际 OS 调用没有在本机运行，分支判断、命名 Job 的重新打开及查询和停止使用可控来源测试；macOS 的真实进程调用已在私有进程测试中执行。没有运行真实 harness 的原生探测或任何付费模型调用；完整检查的既有前端预览测试会运行 `tests/probes/` 下的合成预览夹具，未直接调用其中的真实 harness 探针。

可核验性有两个明确边界：POSIX 根进程已消失而原进程组仍有成员时，光凭组号不能证明它没有被复用，包括 TERM 宽限期内根进程先退出而其余成员未退出的情况；控制进程的原生身份日志缺失、未绑定或仍有启动意图时，也不能证明原生组全部结束。这些情况保留未知，不发出无身份证明的信号；换次开机的证据仍可结算。无法把某条原生会话记录明确绑定到该执行时重建会话。独立检查还发现服务先结算孤儿、候选列表不再包含它时，本地孤儿记录可能阻止退役；已补充权威终态观察并以真实黑板的替代 Worker 流程验证清理，只在确认执行已经结束后清除本地记录。Linux 与 Windows 的真机验证按任务要求留给对应平台。

第一回合使用指定解释器与检查运行器运行过一次完整检查：251 个文件中 241 个通过，九个代码测试文件修复后单文件通过，另一个前端预览文件因缺开发依赖未完成。Host 的本次接续明确授权准备该依赖；第二回合使用 Node.js 24.15.0 完成 `npm --prefix apps/console ci`，没有改锁文件或安装产品。

## 第二回合验收修订

第二回合修订覆盖九条验收意见：身份换成各平台原始整数；进程组存在性改为原子探测；孤儿停止使用 TERM、宽限与核验后的 KILL；去掉初始重复核验和自建身份的重复边界校验；停止证据只由服务结算；确定性封存失败进入已有出口；旧 Worker 保持 lost；旧记录使用一小时余量；删除没有读者的原生身份数据库镜像及与执行终态重复的根身份停止标志，存储变更仍并入 19 到 20 的同一个升级。

第二回合的定向检查已经通过：三个协议文件共 28 项，四个黑板恢复及升级文件共 34 项，新增 `tests/python/blackboard/tasks/test_interrupted_seal.py` 的三项真实 Git 工作区测试。新增时钟回归在活进程存在时同时前后移动模拟墙钟与开机时间，要求进程身份与 Worker 身份仍匹配；Linux 的原始节拍和 Windows 的原始 FILETIME 使用可控平台 API 覆盖，macOS 的 libproc 在本机真实读取。`test_worker_invariants.py`、`test_blackboard.py` 与补充时钟回归后的 `test_process_identity.py` 也通过。`test_repair_recovery.py` 初次因旧 40 秒预算未覆盖“租约过期、Worker 上报、下一次服务清扫”的新结算时序而失败；仅将该故障注入等待窗口改为 70 秒后单文件复跑，12 项全部通过，私有根已清理。两个 Worker 崩溃情形、暂停超过租约后恢复及完成回执重放均在该文件通过；控制面的生产时限没有因此改动。

第二回合只运行一次不带文件的完整检查，使用指定 Python 检查运行器和默认的 4 个进程，254 个文件中的 4,134 项测试全部通过，跳过 0 项，退出码为 0；运行器确认全部私有进程退出并删除私有根。`tests/python/cli/test_host_preview.py` 在前端依赖准备后通过，`test_network_continuation.py` 的网络错误后显式 continue、各 CLI harness 的会话方式和 Lead 唤醒相关检查也在完整检查中通过。最终 `git diff --check` 通过；临时的 `tests/python/sitecustomize.py` 已移除，忽略目录里的检查日志和依赖加载副本保留供 Host 复核。

未在真实 Linux 与 Windows 上运行平台调用，未运行真实 harness CLI 或 app 的探针和付费调用。除上述明确的存量墙钟信息边界与核验不了的进程组保持未知外，没有偏离本次验收要求的情形。

## 第三回合：事件触发恢复与空闲占用

Worker 在启动时找一次孤儿；空闲期间只有等待响应确认出现过 `attempt.uncertain` 或 `attempt.lease_expired` 时才再找，每次事件唤醒至多发一次发现请求。空的等待窗口只续等，普通事件不触发发现。等待响应中的 `recoveryNeeded` 覆盖请求游标到返回 head 的整个事件区间，因此 limit 为 1 时第一页后面的未知事件也不会漏掉；空响应不额外查事件。只有处理过的等待响应推进 Worker 游标，空认领不会跳过两次等待之间发生的未知事件。发现或上报 RPC 失败时保留这个触发并使用既有的恢复退避，成功后才消费事件。

替代 Worker 遇到前一进程遗留的 `WORKER_BUSY` 时进入同一个事件等待，不做认领重试轮询；真实故障注入已收紧为停掉无关空闲测试 Worker，只让这个替代 Worker 自己收未知事件并停止孤儿。Worker 池形态与认领事务不变；退役仍使用已有的持久记录对账。停止宽限的常量移至中立的进程协议模块，由 `ProcessHandle` 与孤儿停止共用；KILL 之后另行观察 2 秒，信号前的完整身份核验不变。服务拒绝 Worker 报告中断，候选原生日志位置只计算一次。

空闲占用用指定检查运行器中的临时测量文件执行：私有空主目录、相同离线目录夹具、6 个空闲 Worker，预热 10 秒后读取服务及其所有后代的 user 加 system CPU 时间，等待 60 秒再读一次；核对进程号与原始创建值均未改变，进程集合也未改变。两次均测量 7 个产品进程，不计测量进程本身；占用为 CPU 秒差除以实际经过时间再乘 100，表示一个核的百分比。改动前为 60.005 秒中 2.444881 CPU 秒，即 4.07446%；改动后为 60.006 秒中 0.215163 CPU 秒，即 0.35857%，比改前下降约 91.2%；与 Host 提供的整合分支 0.37% 同一水平。原始指标与测量脚本保存在忽略的 `tmp/`，临时测量文件不作为日常检查的一部分。

本回合使用指定解释器与检查运行器，默认进程数，不跑完整检查。`tests/python/buddy/runtime/test_worker_invariants.py`、`test_repair_recovery.py`、`tests/python/blackboard/store/test_interruption_recovery.py`、`test_worker_orphan_settlement.py`、`tests/python/protocol/test_process_identity.py` 及新增 `tests/python/blackboard/service/test_worker_event_recovery.py` 首轮共 86 项通过；补充单个替代 Worker 故障注入和事件失败重试后，仅复核两份发生变化的 Worker 文件，44 项全部通过。前后两次临时测量文件也通过，所有私有根均已由运行器确认清理，最终 `git diff --check` 通过。临时测量文件和依赖加载文件已移除；本回合没有失败后重跑或完整检查。与本回合要求不一致的情形：没有。

## 第四回合：整合检查回归修复

七个中断续接用例的共用夹具改为先认证上报完整进程身份、建立绑定该执行的空原生启动日志，再用可控的进程观测经服务启动对账与恢复结算；不再通过 `worker_result` 伪造中断结果。Codex CLI 与 DSH CLI 的原生会话事实由恢复过程读取其私有 checkpoint、会话日志及绑定，并断言这些事实已经保存在恢复结果中；Claude Code CLI 与 ZCode app 没有恢复出的原生会话事实时明确验证重建输入，不再注入虚构会话。

事件恢复测试改为导入辅助模块，避免将带测试方法的 TestCase 类直接放入另一个模块，消除 11 项重复收集。空闲等待测试先处理启动注册事件，再观察连续三个空窗口，要求认领次数、启动意图写入次数与游标均保持不变；认领与等待之间入队的用例仍验证工作不会丢失。空认领回复删除无读者的 `cursor`，同时删除对应断言与测试替身里的旧字段。

本回合按要求只运行九个指定或修改到的文件：`tests/python/blackboard/tasks/test_interrupted_continuation.py`、`tests/python/cli/test_checks_parallel.py`、`tests/python/buddy/runtime/test_worker_idle.py`、`tests/python/blackboard/tasks/test_empty_claims.py`、`tests/python/buddy/runtime/test_worker_invariants.py`、`tests/python/buddy/runtime/test_worker_runtime.py`、`tests/python/blackboard/tasks/test_parallel_dispatch.py`、`tests/python/blackboard/store/test_interruption_recovery.py` 与 `tests/python/blackboard/service/test_worker_event_recovery.py`。指定解释器与检查运行器使用默认 4 个进程，111 项全部通过，跳过 0 项，退出码为 0，运行器确认私有根已清理；没有运行完整检查或失败后重跑。临时依赖加载文件已移除，最终 `git diff --check` 通过；原始检查日志保留在忽略的 `tmp/`。与本回合要求不一致的情形：没有。
