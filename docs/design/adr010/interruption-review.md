# 中断后的接续：独立审查

审查对象是微任务“中断后的接续”在基点 `33f8127e` 上交回的提交 `523e3495`，读的是 `git diff 33f8127e 523e3495 -- src tests`、AGENTS.md 的 Invariants、ADR-010 第三节文末的 2026-10-14 补充和 ADR-006 里 `ProcessHandle` 的约定；没有读 `docs/acceptance/`，实现方的说明在自己的清点完成之后才读。没有运行检查套件，没有启动服务、Worker 或 harness，没有调用模型。唯一的实验是在检出之外的临时目录里，用 uv 缓存中锁定的 psutil 7.2.2 wheel 在一个 Python 进程里替换它的开机时间读数，观察 `Process.create_time()` 的变化（见 A1）；其余结论来自读代码和读 psutil 7.2.2 的源码，凡是没有复现的都写明了。

## 结论

会把活着的进程当成已停的路径有三条，另有一条只在 Windows 上理论存在；没有找到会停错进程的路径。结算与续接有两处问题和一处设计上的提醒。过度防御或多余的机制有五处。最严重的是 A1：进程启动时刻取自 psutil 的 `create_time()`，它在 Linux 与 macOS 上随墙钟校正而变，代码却做精确相等比较。休眠唤醒后时钟一跳，还活着的原 Worker 就会被判成“已不在”；它的子进程如果已经结束，服务会把这次执行结算成 `interrupted`，并挡掉原 Worker 正要交回的真实结果。ADR 恰恰要求这种场景由原 Worker 接上。

## A. 会不会把活进程当成已停，或者停错进程

**A1（高）进程启动时刻随墙钟变化，精确比较会把活着的原 Worker 判成已不在。** `protocol/process_identity.py:192-210` 的 `_creation_time` 与 `_started_at` 调用 `psutil.Process(pid).create_time()`，`worker_identity_alive`（同文件 343 行）与 `inspect_process_identity`（361、368、380 行）把它和记下的 `startedAt` 做浮点相等比较。psutil 7.2.2 自己的文档字符串写明这个值“基于系统时钟，会受手动调整或 NTP 影响”。Linux 上它等于 `/proc/<pid>/stat` 的开机后节拍数加上 `/proc/stat` 的 `btime`，而 `btime` 是整秒数，墙钟一跳就可能变；macOS 上 `_psosx.adjust_proc_create_time` 会按“本进程导入 psutil 时读到的开机时刻”和“现在的 `kern.boottime`”之差修正，差值达到 1 秒才修正。也就是说，两个进程在时钟一跳前后各自导入 psutil，读同一个进程会得到不同的值。我在本机复现了这个机制：同一进程里把 psutil 读到的开机时间加 2 秒，`create_time()` 随之多出 2.0 秒，而 psutil 内部的 `create_time(monotonic=True)` 不变。psutil 自己判断进程号复用的 `_get_ident` 在 Linux 和 macOS 上用的正是这个单调值。另外，在本审查的沙箱里 `kern.boottime` 的 sysctl 被拒绝，psutil 便把导入时的开机时刻记为 0 并跳过修正。这是两个进程读出不同值的又一条途径。后果：租约在休眠中到期，`mark_expired_leases` 把执行标成未知，然后调用 `recover`（`blackboard/tasks/interruption.py:135-145`）；`worker_identity_alive` 因时刻不等返回 `False`，于是继续往下判。若子进程树这时还在，根进程的时刻同样对不上，又有成员，判为 `unknown`，不会发信号，这一层守住了。若子进程树已经结束，原 Worker 正在收集或准备交回结果，`_evidence` 得到 `identity-absent`，`_settle` 便结算为 `interrupted`，`_verify_attempt_actor` 用 `ATTEMPT_FINISHED` 挡掉原 Worker 的回执，真实结果就丢了。同一原因还会让控制进程自己算出的日志文件名 `native-process-journal-<pid>-<startedAt.hex()>`（同文件 28-38 行）与 Worker 捕获、服务读取时用的名字不一致；读不到日志，执行就一直停在未知。证伪做法：在真实子进程测试里，让原 Worker 进程活着、子进程已退出，在检查方的进程中把 `psutil._pslinux.boot_time` 或 `psutil._psosx.boot_time` 连同 `INIT_BOOT_TIME` 平移 2 秒，断言 `recover()` 跳过这次执行；按现在的代码它会结算。真机做法：在 Linux 虚拟机里捕获身份后执行 `date -s '+2 seconds'`，再比较 `create_time()`。更小的修法是改记开机后的单调启动时刻（Linux 的 `starttime` 节拍、macOS 的原始 `p_starttime`、Windows 的 `FILETIME`），它与开机标识合在一起是没有歧义的身份，不随墙钟改变。

**A2（中低）进程组“没有成员”的判断建立在一次非原子的全表扫描上。** `_group_members`（`protocol/process_identity.py:213-223`）先由 `psutil.process_iter()` 取进程号快照，再逐个调用 `os.getpgid`。根进程已经不在时，如果组里剩下的成员在扫描途中先派生一个子进程再退出（双重 fork 的守护化、或一串前后接力的短命进程），这个新子进程不在快照里，而已退出的成员会被跳过，于是 `members` 为空，`inspect_process_identity` 在 366-367 行返回 `identity-absent`，可组里其实还有活进程。服务端的 `recover` 不发信号，会直接结算并在 Git 里封存一个仍在被写的检出。证伪做法：在私有测试里让根进程退出，留一个“派生下一代后自己退出”的接力进程链，反复调用 `inspect_process_identity`，同时用 `os.killpg(pgid, 0)` 确认组一直存在，统计返回 `identity-absent` 的次数。修法见 C2。

**A3（中低）升级时的存量规则拿服务记下的墙钟和现在推算的开机时刻比较，时钟向前跳过之后会误判。** `interruption.py:52-58` 的 `legacy_boot_evidence` 用 `psutil.boot_time()` 和 `started_at`（旧版服务写入时的墙钟）比较。三个平台上的 `boot_time()` 都等于“现在的墙钟减去开机时长”。设这次执行在开机后第 t 秒开始，此后时钟向前校正了 Δ 秒，那么只要 t < Δ，比较结果就是“开始早于本次开机”。典型情形是没有 RTC 或 RTC 偏差很大的机器：开机后服务自启并认领了排队的工作，几十秒后 NTP 才把时钟往前拨。`install/upgrade.py:77-87` 的空闲门槛据此放行，`store/upgrade.py:298` 随后把它结算成 `boot-changed`，名额释放、检出封存，而旧版的进程可能还活着。还要注意，没被证明的存量行会留在 `attempt_legacy_starts` 里，每次租约清扫都再判一次，所以以后的任何一次大的前跳都可能触发。证伪做法：单元测试里把 `boot_started_at` 设为 S+Δ（S 是存量的开始时刻），再用一个真实活着的旧进程作为存量，观察它被结算。若要保留这条规则，至少把比较放宽一个明确的余量，或者要求时钟同步状态可读；更干净的做法是让开机标识本身进入存量事实，但 schema 19 没有记它，这一点需要开发者决定。

**A4（低，理论路径）Windows 的命名 Job 在 `Local\` 命名空间里，跨登录会话不可见。** `_SystemSource.job_members`（`protocol/process_identity.py:236-246`）打不开 Job 时返回 `None`；如果根进程也已退出，就判为 `identity-absent`。服务与 Worker 若不在同一个登录会话（例如一个经 SSH 启动，一个随桌面自启），服务看不见 Job。不过原 Worker 一旦死亡，它持有的唯一句柄随之关闭，`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 会终止整个 Job，所以“原 Worker 已不在而 Job 里还有活进程”只在有第三方持有句柄时才会出现。我认为实际上走不到，但没有真机核实。

**站得住的部分。** 开机标识：Linux 读 `/proc/sys/kernel/random/boot_id`，macOS 读 `kern.bootsessionuuid`，Windows 用 `NtQuerySystemInformation(0x5A)` 取 `BootIdentifier`；读失败时抛 `OSError`，在 `inspect_process_identity` 中成为 `unknown`，在 `worker_identity_alive` 中成为 `None`，都不会被当作已停。Windows 的结构大小与真实行为只有模拟测试覆盖。判断与 `killpg` 之间的空档：根进程被确认仍是原来那个、仍是组长时，组号不可能被重新分配；即使根进程在空档里退出，只要组里还有成员，Linux 与 macOS 都不会把这个号分给新进程；组恰好在空档里变空，又恰好在几微秒内被重新分配为新组，需要进程号整整绕一圈，不现实。Linux 节拍 10 毫秒的精度不足以造成同号同时刻的复用。根进程时刻不符而组里还有成员时判 `unknown`，原 Worker 活着时不发信号，这两处都守住了“不知道不等于已停”。另有一个沿用 ADR-006 组级约定的边界，不算新缺陷：后代如果自己 `setsid` 离开了进程组，又没经过 `owned_popen` 写进日志，组级证据看不到它，这与正常结束时 `shutdown_confirmed()` 的边界相同。

## B. 结算、部分产物与续接

**B1（高）封存在确定性的错误上失败时，已经证明停止的执行会永远停在未知。** `_settle`（`blackboard/tasks/interruption.py:224-238`）先写入 `sealing` 占位，再调用 `seal_interrupted_turn`；封存抛错时只记下 `seal_error` 并返回 0，执行保持 `uncertain`，名额保留，回执被挡，之后每次清扫都会重试。暂时的 Git 故障可以这样重试，但 `workspace._seal_output` 遇到写范围之外的改动会抛 `WORKSPACE_SCOPE_VIOLATION`，只读检出被改过时会抛 `WORKSPACE_CHANGED`，这两种情况重试一万次结果也一样。正常的结算路径会把范围冲突以 `workspaceSealError` 交给 Host 处理（`workflow.py` 约 4943 行起），中断路径没有接这条出口；我也没找到 Host 能对一个带 `sealing` 占位的 `uncertain` 执行做什么。这恰好重现了这次补充要解决的“产品里没有出路”。证伪做法：写范围检出里让 Worker 改一个范围外的文件，再连同子进程一起杀掉，调用 `recover_interrupted_attempts()` 两次，观察执行仍是 `uncertain` 且 `attempt_interruptions.state='sealing'`。修法应当是让中断结算复用正常路径对封存失败的处理：如实结算为已停，把封存错误连同范围证据交给 Host，而不是把结算押在封存成功上。

**B2（低）结算把一个已经证明不在的 Worker 写回 `idle`。** `interruption.py:262-264` 对原 Worker 执行 `state='idle', current_attempt_id=NULL`。`reconcile_startup` 刚把它标成 `lost`（`store.py:3104`），而开机标识变化或身份消失正说明它不在了。这个值在存储里的意思变成了“空闲可用”，与事实相反。只清掉 `current_attempt_id`、保留 `lost` 就够了。

**B3（提醒）孤儿组直接收到 `SIGKILL`。** `_SystemSource.stop_group`（`process_identity.py:263`）不经过 `SIGTERM` 宽限，与 `ProcessHandle.terminate()` 的约定不同。这样做是安全的，代价是原生 harness 没有机会写完会话记录，于是 `worker-stopped` 之后的续接几乎总是走重建。这个取舍可以接受，但应当是有意为之。

**站得住的部分。** 最后一个事务一起完成这些事：执行改为 `finished`，`shutdown_confirmed=1`，任务改为 `failed`，释放资源占用，写事件，结束 decision，`settle_turn` 把回合标成 `interrupted` 并撤销凭据，`child_settled` 叫醒 Lead（`workflow.turn_failed`，`test_lead_lifecycle` 断言只叫醒一次）。检出没有回收，部分产物是 `partial-output`，带 `verified=false`、`final=false`。下一回合只有 Host 显式 continue 才会开始，并以它的固定提交为基点，不会自动续接。原生接回还是重建只看两样东西：harness 声明的 `interrupted_resume`（Codex CLI 与 DSH CLI 为真，Claude Code CLI 与 ZCode app 为假），以及该执行自己留下的会话事实（Codex CLI 的 checkpoint、回合终态与绑定，DSH CLI 的会话记录 `turnEndSeen` 与绑定），没有猜测。`sealing` 占位在 Git 之前挡住原 Worker 的迟到回执，两次事务都重新核对身份，`_recovery_lock` 防止清扫与 RPC 并发封存，这几处都对。

## C. 过度防御或多余的机制

**站得住的。** 引入 psutil 替换手写的进程枚举是对的。三个平台的开机标识用 ctypes 自己读，因为 psutil 不提供开机标识。控制进程的原生进程日志是必需的：按 ADR-006，原生 harness 进程各占一个进程组，只停外层控制进程组证明不了整棵树都停了。日志在边界上读，所以做了链接、大小与结构的校验，这些合理。`spawn.marker` 加上新 Worker 补发身份，填的是“已落盘、未写入黑板”这一段真实存在的空档。服务独立复核 Worker 报来的停止，这是进程边界上的校验。`attempt_interruptions` 的 `sealing` 状态是为了跨过 Git 边界去挡回执。`attempt_legacy_starts` 用一张单独的表明确标出“没有完整身份的存量”，符合“存储不随写入时间改变含义”。把 Win32 操作移到 `protocol/windows_process.py`，是因为黑板与 Worker 两侧都要用。

**C1 同一处检查重复。** `stop_orphan_process` 连续两次调用 `inspect_process_identity`（`process_identity.py:392-398`），而 `inspect_process_identity` 末尾（380 行）已经重读过一次根进程时刻；第二次检查并没有缩短到 `killpg` 之间的空档，只是多做一次全表扫描。`Worker.recover_orphans`（`worker.py:486`）在调用 `stop_orphan_process` 之前先调一次 `worker_identity_alive`，`stop_orphan_process` 自己（390 行）又调一次。`capture_process_identity`（331 行）对自己刚拼出的记录调用 `validate_process_identity`，而黑板在 RPC 边界上还会再校验一次。这些都是边界之内的重复。

**C2 手写全表扫描，有现成的原子原语可用。** `_group_members` 用 `process_iter` 加 `getpgid` 判断“组里还有没有人”，而 `os.killpg(pgid, 0)` 由内核一次给出答案：返回 `ESRCH` 就是组里一个进程也没有，结果原子、更简单，也顺带消除 A2。代价只有一点：还没被回收的僵尸进程也算在组里，这只会让判断更保守。

**C3 只写不读的存储。** `attempt_process_identities.shutdown_confirmed` 在三处被置 1（`interruption.py:44`、`interruption.py:252`、`store.py:2542`），没有任何地方读它，含义也和 `attempts.shutdown_confirmed` 重复。`attempt_native_process_identities` 只被拿来和它自己比较是否“不可变”，没有其他读者。它如果是停止证据的存档，就该由回执或控制台读出来；否则可以不建。

**C4 同一套清扫有三处触发。** 服务的启动对账与租约清扫已经会调用 `recover`；`worker_recover` 不带 `attemptId` 时又跑一次完整的 `recover(board)`（`interruption.py:167-168`），而每个空闲 Worker 每次空等结束都会调用 `recover_orphans`（`worker.py:590`）。于是 N 个空闲 Worker 带来 N 次额外的全表扫描，外加 Git 封存的重试。Worker 其实只需要取候选并停掉孤儿，结算交给服务的清扫就够了。

**C5 Windows 上按名字重开 Job 去停止，对应的情形几乎不存在。** 原 Worker 是 Job 句柄唯一的持有者，又设了 `KILL_ON_JOB_CLOSE`，它一死整个 Job 就被终止并销毁。所以 `OpenJobObjectW`、`process_ids` 和 `stop_job`（`protocol/windows_process.py`、`process_identity.py:236-259`）只在第三方持有句柄时才用得上。真正需要的只是“Job 已经打不开、根进程身份也不在了”这一个观察。这一处代码不大，列出来是为了说明它可以更简单，不是阻塞。

## 对照实现方的说明

说明与代码有三处不符或遗漏。第一，说明第 5 段强调不用会受校时影响的 `psutil.boot_time()` 充当开机身份，却没有提到同样受校时影响的 `create_time()` 被当成了进程身份的一部分，A1 正出在这里。第二，第 15 段说“Git 封存失败会保存错误并由后续清扫重试”，把重试当作完整答案，没有区分暂时失败与范围冲突这类确定性失败，后者在代码里没有出口（B1）。第三，第 21 段的“休眠”故障注入是暂停 Worker 进程，墙钟相对开机时刻没有移动，覆盖不到唤醒后校时的情形；所谓“断网”验证的是原生 harness 遇到网络错误后的续接，不是 Worker 与服务之间断开（Worker 与服务之间的断开由“服务断线期间完成的回执只重放”那一项覆盖）。其余描述，包括三条证据的分支、根进程已消失而组仍有成员时保持未知、日志在有挂起意图时保持未知、存量规则只对带标记的行生效、Lead 用 `workflow.turn_failed` 叫醒、续接方式的选择，都与代码一致。说明第 25 段写明 Linux 与 Windows 的真实系统调用没有在真机上跑过，这与我看到的测试相符：Windows 的开机标识和命名 Job 只有模拟测试。
