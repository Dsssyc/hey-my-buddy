# Windows 第二批：执行所有权、停止证据与私有存储

2026-10-21，设计候选，修订基线 `92031c1deb30e302b9f60e571524c146a367060d`。依据为[分批计划](plan.md)、[清单](inventory.md)、[进程实验](windows-experiments-process.md)、[文件实验](windows-experiments-files.md)、当前源码、AGENTS.md、[架构](../../reference/architecture.md)、[ADR-007](../../decisions/007-neutral-core-and-single-current-contract.md)及 [ADR-031](../../decisions/031-one-process-per-execution.md)。实验结论只覆盖记录中的 Windows 11 x64、NTFS、令牌与启动形式；本轮没有运行新实验或检查套件。实验文档引用的原始输出在本检出中未提供，本文采用其逐项摘录及明确的未验证边界，不声称重新核验过原始包。

支持范围为 Windows 11 x64、本地 NTFS、同一 Windows 会话、Python `>=3.12.4,<3.13`；Python 下限已由开发者同意，阶段 B 落地。Windows 系统服务、网络盘、FAT/exFAT、Windows ARM 不在首发范围。三系统共用任务身份、事实发布、结果接收及最终结算；Windows 的 ACL、Job、会话与文件共享行为在系统边界实现。Linux/macOS 脱离进程组的后代留在另一批，本批不宣称解决。

ADR-031 第 13 条已经定为开发流程约束：开发使用真实数据只经导出工具取得已验收微任务，用完即删。产品不增加状态目录身份，不为看板副本增加接回、停止或结算围栏，也不实现目录文件 ID 的读取或副本验收。下面的 Windows 会话核对服务于 `Local\` Job 的定位，与状态目录无关。

## 1. 已有证据与本批的起点

阶段 A 的两份实验记录均使用基线 `d5c908aaaed3cfd66c20822222570a1e267ff9f2`。进程实验的基础解释器为 CPython 3.12.13；私有 venv 的 `Scripts\python.exe` 转发器另起真正解释器，曾出现 `Popen.pid=860`、脚本 PID 为 28840，脚本查到当前 Job flags 为 `0x3000`，与探针显式设置不同。额外 Job 的创建者没有追踪。随后直接用基础解释器复验，Popen PID 与脚本 PID 相同；系统结论以该复验为准，不能把转发器的行为推广成基础解释器行为。

无 kill-on-close 的命名 Job 关闭最后句柄后，原成员仍以原创建 FILETIME 存活，`OpenJobObjectW` 返回错误 2；同名再建得到一个空 Job，原成员不在其中。同名对象尚有句柄时，`CreateJobObjectW` 返回有效句柄并报告 183。根/native Job 嵌套、native 最后句柄关闭使成员退出、禁止脱离时显式 breakaway 返回错误 5、四条 shell/uv/Node 孙进程路径的根 Job 成员关系都有本机记录。它们没有验证本设计的新根句柄持有次序。

文件实验中，提权的 Python 3.12.13 用 `mkdir(0o700)` 得到受保护 DACL，主体为 SYSTEM、Administrators、OWNER RIGHTS；3.12.3 的同一参数仍继承父 ACL。新目录、普通文件及 SQLite 文件的 owner 为 Administrators。普通用户、交替提权使用尚未验证；实验没有运行恰好 3.12.4，不能用这组数据证明变更精确始于哪个补丁。锁定运行时在源目录被替换后仍可正常导入 `win32security`，但尚未执行拟新增 ACL bootstrap 的全部入口。

私有事实发布与备份阶段 C 已实现、验收并合入 `516c6d5d`；当前入口是 `private_files.py:publish_bytes`，三系统备份共用 journal，exchange 分支及以只读句柄补刷盘的循环已删，gzip 尾部由写者刷盘。`e1e4d1d6` 修复发布测试的 Windows 挂起：握手改为字节协议，避免 CRLF 不匹配后阻塞读 stderr；写者直接用基础解释器，避免只杀掉 venv 转发器。此为测试修复，不能据此声称产品执行启动链已经修好。

Host 提供的第十六轮 run `39579709581` 状态是 Windows 的 302 个测试文件已过 168 个，不能称为整轮通过。本批剩余失败按入口处理：私有目录/文件的 mode 判断归 B，Job 生存期及身份归 D/E，检查运行器用 `ps` 观察收尾而退出 3、`test_native_process_journal.py` 使用 `/bin/sleep` 归 F。旧第十轮错误行的粗计数不再作为实施依据。

## 2. 私有目录与文件：一次句柄级属主及 ACL 校验

源码中的边界仍不统一：`private_dirs.py:ensure_private_dir` 与 `protocol/transport.py:_private_directory` 在 Windows 上未验证 DACL；execution 私有 JSON 跳过 uid/mode，console session 与 fast Router 空 cwd 却仍以 `st_mode & 0o077` 拒绝路径。`private_dirs.py:open_regular_fd` 的 Windows 分支先检查路径、普通打开、再检查路径，turn_io 也没有 POSIX 固定祖先 fd 的等价保证。修复在公共边界完成，调用点删除重复或相矛盾的 mode 判断。

将 `pywin32; sys_platform == 'win32'` 声明为直接依赖并锁定版本；当前锁文件因 portalocker 已含 pywin32 312。pywin32 只用于句柄上的 `GetSecurityInfo`、ACE 遍历和 SID 比较，不自写安全描述符解析。现有 `blackboard/tasks/windows_paths.py:WindowsFileAPI` 的 ctypes 打开、固定、fd 转移、关闭和错误映射原样搬到根包 `windows_files.py`，工作树政策留在原处；私有入口复用唯一句柄层，不另留回退实现。Job 的 ctypes 层不借此重写。

唯一 ACL 接受规则为：owner 是当前用户 SID 或本机 Administrators；DACL 存在且非 NULL；全部 ACE 均为普通 allow，受托者仅为当前用户、SYSTEM、Administrators、OWNER RIGHTS。只作用于子对象或继承的 ACE 同样遍历；其他 owner、deny、未知、对象、条件 ACE 与白名单外主体拒绝。空 DACL 和 NULL DACL 区分，不另算有效权限、不要求特定掩码或 protected 标志，实际访问交系统决定。实验已覆盖部分正反例及 SQLite sidecar 继承；DACL 缺失、对象/条件/未知 ACE、junction 竞态仍未实测。

新私有目录使用 Python 的 `mkdir(mode=0o700)`，文件继承父目录；不自造描述符，不自动修复既有 ACL。创建及打开后在同一句柄上按唯一规则校验，默认状态目录、逐级创建的私有父目录、PowerShell 启动器先建的目录同样适用。3.12.13 的提权实测支持当前选择；普通令牌 owner 与交替访问结果由第 8 节补齐。此规则不承诺隔离管理员或同一用户的任意 shell。

固定祖先沿用 `FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS`，父句柄不允许删除共享，核对真实类型、重解析属性和最终名称；叶子使用同一句柄校验普通文件及 ACL。新文件独占创建，CRT fd 使用二进制和不可继承标志，句柄所有权只转移一次。事实读者允许删除共享，能否换代另受第 3 节 API 选择约束。普通源检出复用路径政策，不套私有 ACL；POSIX 继续使用 dir_fd、O_NOFOLLOW、uid/mode。

接入范围包括 `cli/main.py` 的 Host 控制能力及提交令牌、`install/launcher.py` 的 agent credential 文件与继承 fd、read_private、transport 的 token/endpoint、execution 的 startup/live/receipt、turn_io 私有输入输出、console session、fast Router 空 cwd、备份及安装私有文件。继承 fd 按原生句柄校验，不按路径重开。空 cwd 仍须真实、私有且为空，不要求磁盘根和普通用户目录全都私有。Windows 安全依赖按需加载，launcher 首次 bootstrap、active 指针及源替换后的稳定运行时入口都要验证；导入失败直接报告，不能跳过安全校验。

## 3. 事实发布、Windows 换代与开机会话

现有 `publish_bytes` 同目录独占建临时文件，写完整内容并在写句柄上 flush/fsync，关闭后发布；不可变对象 compare-and-create，相同内容可复用、不同内容冲突。POSIX 刷父目录，Windows 不打开目录刷盘。承诺是写者进程中断后读者得到完整旧版、完整新版或明确未完成；不承诺 Windows 目录项的断电耐久性。SQLite 仍是权威状态，WAL、synchronous=FULL 及事务/事件原子性不变。

该入口已经覆盖启动意图、startup、身份 marker、native journal、live/budget、回执、交接事实、封存证据、备份及安装 journal/指针。各调用者仍持有自己的路径政策，阶段 B 统一句柄边界。诊断 stdout/stderr、console session 和内存提示不作为停止证据。备份在锁内完成并验证 incoming、写 journal、将 current 改名 previous、incoming 改名 current，验证后删除 previous 及 journal；正式读者先恢复或报告进行中，首次没有完整一代时可报告未完成，不能删除唯一已验证的一代。这些是阶段 C 的现状，不重新排作待实现机制。

文件实验的四个单文件窗口各重复三次：写完未 fsync、fsync 后、替换前被杀都读到完整 OLD，替换后被杀读到完整 NEW。此证据没有覆盖 native journal、多文件绑定、gzip 尾部、备份恢复器或断电；阶段 C 的验收也不能替拟换代的 Windows API 完成验收。当前 `publish_bytes` 的 Windows 可变发布仍调用 `os.replace`，finally 会清理其临时文件；不能写成当前产品已经保留失败的临时文件或支持带读者换代。

Windows 换代选择为：既有可变普通文件使用 `ReplaceFileW`，首次发布使用要求目标不存在的同卷改名，不可变发布继续 compare-and-create。此为阶段 C 的后续候选，须先通过下面的实验门槛才能安装；不保留 os.replace/MoveFileEx 的失败回退，不实现 delete-then-rename，也不自造另一套每文件 journal。现有备份的目录改名不改用只适用于文件的 ReplaceFileW。所有正式事实读者经第 2 节允许删除共享；外部禁止共享的读者导致明确忙错误。

选择的依据限于文件实验 `share-probe`：允许删除共享的目标读者持有时，os.replace 和 `MoveFileExW(MOVEFILE_REPLACE_EXISTING)` 各三次错误 5，ReplaceFileW 三次成功。后者的失败窗口、元数据及耐久性尚未验证，POSIX 语义改名没有试过；本文不把它列为并行实现。选择 ReplaceFileW 前，在同一私有 NTFS 根逐项验证：目标缺席的首次发布和已有目标换代；普通 Python、允许删除共享及外部禁止删除共享读者；换代后旧句柄读 OLD、新打开读 NEW、目标从不暴露半份或无故缺席；返回失败后的目标/临时文件及原始错误码；目标和替换物的 owner/DACL、属性、文件 ID 及重新打开校验；fsync 后、调用前、调用中及调用后强杀写者后用新进程读取摘要；再跑实际 native/备份/安装 journal 的恢复用例。调用中窗口可重复竞争，未命中就标未覆盖，不能冒充逐指令覆盖。

若 ReplaceFileW 的失败行为不能保持完整旧/新代或明确可恢复的未完成状态，或其元数据行为不能满足唯一 ACL 规则，就不落地该候选，阶段 C 后续保持未完成；下一步只补 POSIX 语义改名的定向实验再选一个机制，不能同时维护两套。进程中断后的恢复按实际路径及摘要判定，不仅看 API 布尔值。失败时保留仍存在且恢复需要的完整文件，不在 finally 无条件删除唯一候选；不保证 Windows 未验证的断电恢复。Defender 条件见第 8 节，不因错误 5 一律重试。

当前 Windows `boot_id()` 调 `NtQuerySystemInformation(0x5A)` 读取 BootIdentifier；实验只证明同次开机五次短间隔稳定。它跨完整重启、快速启动、睡眠、休眠及注销的行为没有证据，本批不再安排这些电源实验，也不由电源动作名称推导标识值。Linux 的 boot UUID、macOS 的 bootsessionuuid 仍按既有系统契约处理。

Windows 的安全规则改为不以 BootIdentifier 不同单独推出旧进程或旧 Job 已停。同值只允许继续核对，既不证明还活着，也不证明期间没有重启；旧进程已结束但标识没变，会多做一次精确身份/Job 观察，不产生假停止。异值只能记录“开机会话观测不一致”，不能直接结束 attempt；如果睡眠/休眠保留了进程却改变该标识，“异值必已停”的推断就会把活进程当成已停，这是必须删除的危险方向。即使通常重启会终结进程，本批也不把未验证的 Windows API 标识当该事实的替身。

正常核对使用 PID 加原始创建 FILETIME、Windows 会话及持有的原 Job。BootIdentifier 相异而旧完整进程仍在时保留 unknown，不连接或强停身份冲突的目标；根身份缺席仍须第 4 节的 Job 结束证据。读不到标识、身份、会话或 Job 均保持 unknown，不用墙钟、mtime、随机 UUID 或 PID 缺席补齐。代价是某些跨开机的旧记录可能继续占用，尤其根 Job 名已消失时不能自动证明全空；保留资源和运行时，交给后续取得真实停止证据的恢复，不能用“等够时间”释放。这一可用性边界由 Host 写入 ADR 与验收，不安排电源实验来解除它。

## 4. 服务启动、执行根与 Job 的句柄生存期

当前服务冷启动 `protocol/transport.py` 和升级回滚的 `install/upgrade.py:_restart` 只用 start_new_session；Windows 缺少独立于控制台/外层 Job 的启动。服务应使用 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`；若启动方处于 Job，显式请求 `CREATE_BREAKAWAY_FROM_JOB`，创建失败直接报不满足独立启动，不换 flags 重试。没有外层 Job 时不加 breakaway。不能只根据最近一层 SILENT_BREAKAWAY_OK 猜整个嵌套链允许脱离。

依据是 SSH 当前 Job flags `0x2800`：仅 detached/new-group 的服务替身及孙进程在连接关闭后缺席，加显式 breakaway 则存活；自建禁止脱离的外层请求 breakaway 以错误 5 失败，自建 silent 外层不显式脱离时虽能活过该层关闭，仍活不过 SSH 层。记录中的“仍属于某个 Job”不能解释为已脱离全部外层。桌面 PowerShell、VS Code 及实际 Host 工具 shell 仍须独立验收；外层不允许的环境明确不支持，内部再加 Job 不能抵消它。

执行根定义为实际运行 `hey_my_buddy.buddy.runtime.execution`、承载 ExecutionLive 的 Python 进程。当前启动器 `install/launcher.py:_runtime_python`、稳定运行时 `install/runtime.py:runtime_python` 和升级/回滚都指向 `venv\Scripts\python.exe`；transport 使用选定运行时解释器，执行再用 `sys.executable -m ...execution`。在实验环境中这条链会经过 venv 转发器，基础解释器才运行 Python。服务与执行的真实 PID 必须由自身报告并取内核创建 FILETIME；Popen PID 是创建的启动进程，只可用于回收该句柄，不能冒充真实服务或执行身份。

本批保留 venv 解释器入口以保留 site/.pth、依赖及固定运行时语义，不把测试用基础解释器加 PYTHONPATH 的探针办法移植到产品。启动器的短命客户端不取得执行停止权；服务描述符使用服务自身 PID。执行仍由服务 suspended 创建启动进程，先分配到预建的根 Job 再恢复，转发器和真正解释器都必须在同一根 Job；真正解释器用 `os.getpid()` 与原始创建 FILETIME 报告身份，服务在已有身份确认边界核对该进程确属持有的 Job，并将其写入 marker、SQLite 和 live 描述符。不能要求它等于 Popen PID。转发器是否另建嵌套 Job 不影响外层根 Job 的结束判据，但仍须实测真实链的根成员关系，不能拿“当前 Job flags”替代明确的 IsProcessInJob。

根 Job 采用 kill-on-close、禁止脱离。服务在启动前分配随机 `Local\HeyMyBuddy-<uuid>` 名字，写入绑定 task/attempt/generation、观测 bootId 和 Windows sessionId 的 intent/startup；不增加状态目录身份。`CreateJobObjectW` 成功后立即检查 ERROR_ALREADY_EXISTS=183，若已存在，关闭返回句柄并失败，绝不先设置 flags 或接管已有对象。清楚区分成功调用后的指定状态码与其他残留 last-error。Job 使用令牌默认 DACL，不增显式描述符。

真正执行进程启动后，先打开并持有根 Job、核对自身成员关系，再走现有身份确认；身份确认成功之前不启动任何原生工作。它的句柄不可继承，持有至执行退出；native Job 句柄仍由执行持有，kill-on-close 且禁止脱离。服务初次创建的根 Job 句柄覆盖转发器启动到执行持有句柄的窗口，执行未能确认就由服务强停并观察该 Job。无需句柄继承/复制或新的移交协议，复用已有身份确认。服务在这个窗口先崩溃，最后句柄关闭可能终结尚未持有句柄的执行，不把“已创建”宣称成已经独立运行。

Host 提议的“只有执行持根句柄、执行一退剩余进程一起结束”可以保证清理的触发条件，却不足以让服务观察结束：最后句柄关闭后名字消失，不能再查询 ActiveProcesses；关闭句柄或触发内核终止也不是已观察到全空。采用的最小调整是服务在初次创建及同会话接回后，持有一个查询/终止句柄到这次关联结束，执行自己的句柄保证服务重启时 Job 仍可按名打开。服务不增加等待进程或心跳，仍只用原有长调用触发生命周期核对；保留的句柄只用于同一个 Job 的停止与结束观察。

服务正常脱离或自身重启时关闭所持句柄，执行仍持有，不能误杀执行。长调用失败但实际执行仍活着时保留 unknown，不能直接强停；根的精确身份已退出，或已获授权强停时，服务对持有的同一个 Job 调 TerminateJobObject 并观察 ActiveProcesses=0，之后才关闭句柄和结算。若服务恰好持有句柄，执行被强杀不会自动触发最后句柄关闭，因此这次显式终止不可省。服务的异常退出由 OS 关闭其句柄，执行仍存活则维持；两者均退出、最后句柄关闭则触发 kill-on-close，但下一次恢复仍要区分清理触发与清理已完成。

接回从权威 attempt 与固定私有证据出发，先核对 Windows sessionId 与当前会话，再按名打开 Job；根仍在时核对原始 FILETIME 和明确的根成员关系，沿同一句柄查询、停止和观察。不同/读不到会话就 unknown，不接回、不发信号，不能由另一个 Local 名字空间中的错误 2 推断已停。名字只定位，不独立授权。Job 创建边界拒绝重名，恢复绝不调用 CreateJobObjectW 补一个空对象。旧的 job_members 与 stop_job 各自开关句柄路径应合并，业务身份在入口校验一次，操作结果照实观察。

停止证据为根的精确身份已退出且持有的原根 Job ActiveProcesses=0；所有成员都计数，包括转发器、conhost 和 shell。根退出但 Job 非空时停止同一 Job 的遗留成员并继续观察。服务重启后若执行仍持句柄，按名接回即可；若执行也已崩溃且名字已经消失，根身份缺席、kill-on-close、错误 2 的组合仍不冒充全空观察，保留 unknown。不得因重建同名空 Job、按进程名过滤或只核对已登记 native 根 PID 就释放整个子树。此为同时失去观察句柄时的明确恢复限制，不增看门狗或 PID 递归扫描弥补；阶段 D 必须验收不会误结算。

有序停止沿现有 ExecutionLive stop 进入 harness 原生 interrupt/close，宽限后强停 native Job，最终强停根 Job。`process_handle.py:signal_group` 的 CTRL_BREAK 不能作为 Windows 无控制台的通用软停：实验的 NO_WINDOW/new-group 替身调用成功但 0.5 秒后仍活着，私有停止文件协议则退出 0，TerminateJobObject 则强停后全空。停止文件仅为实验替身，产品不新增文件停止协议。command 替身无原生协议，不承诺 POSIX SIGTERM 的处理机会，不伪造负退出码。

执行及 native 子进程的控制台方式候选改为 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`，仍先 suspended、加入其 Job 再恢复，不设 breakaway；NO_WINDOW 不再被当作消除 conhost 的证明。detached 是否产生 conhost、是否影响原生 stdio 协议仍未验证。即使改 flags 后仍有成员残留，收尾走同一 Job 的终止和空观察，不加按进程名例外；若影响原生协议则阶段 D 停在未验收，不能盲目回退 flags 宣称成功。

## 5. 后代停止、conhost 与共同结算次序

进程实验的 NO_WINDOW 与 new-console 均观察到 conhost 计入 ActiveProcesses，嵌套 native 成员退出后根中也可能剩 conhost。私有服务及 DSH CLI 0.2.1-rc.2 的版本命令结束后有残留 conhost，终止其受控 Job 后全空。Codex CLI 0.163.0 产品发现返回 harness.shutdown-unverified，但另外直接用 Node 加 JS 的受控版本命令退出 0 且 Job 已空；产品发现那次没有足够成员归因证据。因此 conhost 是 Codex 失败的待核对原因，不能写成同一根因已证实。DSH Electron shim 的解析实现由另一微任务负责，本设计不处理它。

让 Job 全空的路径是：先走 harness 原生协议收尾并等待其主进程退出；若此时 native Job 仍非空，终止整个 native Job 并观察全空，记录实际发生的强停/清理，不把成功发 CTRL_BREAK 或主进程退出当作全空。无须判断剩下的是 conhost 还是业务后代。只有全空才关闭 native Job、封存输出并写回执；强停影响结果分类时沿现有 terminationReason 规则表达。正常回合也做这次非空观察，不能仅在取消路径清理。执行自己的根 Job 包含执行本身，不能由执行终止它来完成正常交付；根退出后的残留由服务持有的根 Job 结束并观察。

真实启动链实验必须分别复跑 Codex CLI 0.163.0 的产品版本发现与直接 Node 加 JS，记录 Job 全成员及主进程退出时刻，对照 NO_WINDOW 和 detached；DSH CLI 版本的既有残留证据只用于停止设计，其 shim 支持单独验收。需要验证嵌套 Job 中的 conhost 究竟计入哪层，以及 native Job 全空、执行退出后根 Job 全空均可达到。现有 CLI 实验没有运行模型回合，不据此宣布 interrupt/close 或完整 harness 已验证。

三系统共同次序为：native 工作停止已确认后写不可变回执；服务核对绑定、摘要及产物边界并可靠接收，立即响应“已接收”，attempt 保持 finalizing 和全部资源；执行收到响应退出；服务经原有长调用失败/创建句柄回收进入停止观察，Windows 确认实际执行退出且持有的根 Job 全空，POSIX 确认完整身份与已登记原组结束，然后一个事务导入最终结果、事件并释放容量及 claims。成功回执到达不提前完成，不加另一种交付结果，也不等根退出才响应 RPC 造成死锁。

具体收敛为一个接收记录：新增 `attempt_received_receipts`，attempt_id 为外键及主键，只存已接收不可变回执的摘要；固定路径由 attempt 推导，不重复存路径或整份 report。插入该记录与置 finalizing 同事务，重复同摘要幂等，不同摘要冲突；响应采用唯一的已接收语义，删除“收到即提交完成”的旧分支。最终结算读同一份回执并在完成事务删除接收行；文件作为冻结证据随所属微任务删除，不因收到 ACK 就删除。无回执时仍走已有的中断/部分封存结算路径，停止未知时保留占用。新增表的 stock、约束、恢复读取及清理随 E 的升级一起落地，不用 meta 或无结构 JSON 槽存接收状态。

`buddy/runtime/receipts.py:deliver_receipt` 当前在 submit_result 后写 delivery 并删除 receipt；该次序要删掉，ACK 后仅退出，恢复不再靠 delivery 文件。`blackboard/store/store.py:execution_result_reference/execution_result/_commit_execution_result`、调度恢复和 interruption 只保留上述一条结算路径；回执保持封存原文，不改写 shutdownConfirmed 为假值延迟完成。接收记录及原始回执的恢复保证服务可在接收后、执行退出前后或最终事务前崩溃，回来只结算一次。没有足够 Job 结束证据时，即使接收行存在也不提交完成。

Windows 用禁止脱离的根/native Job 覆盖自然派生后代；通过既有外部进程代起的工作不自然属于 Job，照实记录边界。POSIX 的 native journal 只登记本项目创建的原生根，不能证明 setsid、双重 fork 或脱离后代已停；既有 ZCode CLI 待办没有核实其逃逸路径和版本，不扩写为已复现结论。本批不实现 cgroup、PID 递归或 cwd 扫描，也不以该后续批次阻塞本批安装，验收仍须写明缺口。

## 6. 忙文件、运行时保留与测试收尾

事实读者关闭自己不用的句柄并允许删除共享，读取校验针对持有的那一代；换代由第 3 节唯一实现承担。固定祖先的作用域在删除或改名目录自身前退出。普通读者和外部禁止删除共享者阻止删除/替换，实验实际出现错误 5 与 32，不能把 5 一律当 ACL 或把所有忙错误预设为 32；错误保留原始码、操作及受限相对路径。长期占用报忙并保留可恢复现场，不无限重试或删除目标再发布。

`Database.connect` 缓存连接，退出事务不等于 close；daemon cleanup 已调用 db.close，不能说产品没有关闭数据库。恢复替换 board/sidecar 前让唯一所有者全部连接关闭、释放操作句柄，WAL checkpoint 和安装协调围栏保留。实验中只读、读写 SQLite 连接都阻止删除/改名，关闭后成功；外部查看器占用时保留 journal 和备份，不删 WAL/SHM 绕过错误。释放占用后继续同一恢复计划。

检出删除继续交 Git 和既有注册/锁，私有树删除保留现有计划/墓碑；一部分已删不等于整个检出回收。产品删除器不加清除 READONLY、放宽 DACL、递归 chmod 源仓库的能力。测试合成 Git 对象的 READONLY 已实测会以错误 5 阻止删除，清除该对象属性后成功；只允许专用测试根删除器在全部测试工作已停后清该叶子的 READONLY 并重试一次，其他错误保留根。

已加载 Python 实测阻止删除可执行文件及完整 runtime，关闭后可删，但第一次递归删除可能已删掉其他文件。旧 runtime 必须在进入删除前按 ADR-031 第 10 条确认没有在跑或 unknown 执行引用，不能试删后才发现占用。安装切换失败保留恢复证据，切换后的延后清理报告保留原因，不吞失败或停止无关执行。目录联接的安装支持属第三批。

测试辅助显式关闭手建 store、SQLite 连接、console、RPC 与线程；BoardTestCase 不递归搜未知对象。`test_harness_health.py` 的手建 health store 与 `test_backup.py` 只管理事务的 sqlite context 分别在原测试登记 close，不靠 gc/rmtree。检查运行器在 Windows 删除 `ps`/lsof 假设，优先使用它创建并登记的 Job/进程句柄和精确身份观察自建进程；通用枚举需要时复用已锁定 psutil，不实现另一套跨平台扫描器，不把 open_files 空列表当无文件占用的完整证明。不能确认收尾仍保存私有根且退出 3，不向扫描得到的陌生 PID 发信号。

`tests/python/protocol/test_native_process_journal.py` 的 `/bin/sleep` 改为跨平台、可明确握手的 Python 替身；Windows 测试要结束真正写者/成员，不能只 kill 转发器。字节握手和有界 communicate 沿用 `e1e4d1d6` 的修复，不再阻塞读 stderr 等待未退出子进程。POSIX 专用 killpg/FIFO 用例标明平台并补 Windows Job 对应验证，不能只跳过失败。

Defender 实时扫描在阶段 A 关闭，1000 次串行无重试发布全成功只能证明该条件。开启后的占用是否瞬时仍未知，先做第 8 节实验；只有捕获确定会自行释放的短暂占用，才在唯一文件边界设计一次有界重试。持续读者占用、ACL 拒绝和身份未知不重试成成功，调用点不各加重试。

## 8. 实施阶段、桌面实验与契约升级

阶段 A 已完成上述两份模型无关实验，相关结论逐项并入本文；剩余实验按下面清单进行，不再包含目录身份或电源操作。A 只产出实验记录，不改契约、存储形式或版本，不开待执行升级。每项在新的独立实验根运行，记录源码、Python/CLI/工具版本、令牌、会话、完整身份、Job flags/成员、原始输出、错误码及最后收尾。显式安装探针 Python 使用私有根及 uv 的 `--no-bin --no-registry`，不触碰日常服务、看板和环境。只用模型无关替身及版本命令，不调用模型。

1. 桌面普通令牌与交替提权：开发者同一用户打开普通及管理员 PowerShell，分别记录 TokenElevation、TokenOwner 和组；固定 Python 3.12.13，在宽继承父目录及默认父目录用 0700 建根，再读回句柄 owner/DACL/ACE/继承，以 icacls 对照。普通建根后管理员建子文件，再由普通访问；反向重复，检查凭据和 SQLite sidecar。补跑默认状态目录及 PowerShell 先建目录的产品路径。若普通 owner 为当前 SID 且两向可用，保留 B 规则；若不满足，先定位实际 ACL/访问失败，B 保持未验收，不增加主体白名单或自动改 ACL。

2. 桌面启动方和窗口：开发者分别从 PowerShell、VS Code 终端及已有 Host CLI 工具 shell 启动私有服务替身，独立观察方登记原始 PID/FILETIME、会话和 Job，再关闭启动窗口/shell。对比 new-console、NO_WINDOW 与 detached/new-group，并对外层 Job 明确测试 breakaway 允许及拒绝，观察窗口、HWND、conhost、孙进程归属和存活。能独立存活且不弹窗才验收相应入口；拒绝脱离就明确不支持，若 detached 破坏 stdio 或仍留 conhost，按第 4/5 节收尾，不忽略成员。实际 Host shell 若不能在无模型调用条件下取得，该入口继续未验证。

3. 根 Job 与真实 venv 链：用最终启动 flags 从服务替身经稳定 venv 启动执行替身，分别记录 Popen PID、脚本自身 PID、服务/执行描述符身份和显式根 Job 成员关系；逐点暂停在根 Job 创建、启动进程入 Job、执行打开句柄、身份确认与原生工作启动前后。验证服务先退出后执行保有名字可被新服务打开，执行先强杀时服务所持句柄下显式终止并观察全空，两者都退出且失去句柄时恢复不误判。新服务正常脱离时关闭句柄不能杀执行；重名创建必须失败且不改变原 flags。若转发器使后代不在根 Job 或现有身份确认无法成立，D 不验收，回到真实解释器启动设计，不用错误 Popen PID 补值。

4. conhost 与版本发现：在桌面重复 NO_WINDOW、detached 和 new-console 的协议停止/强停，记录嵌套根/native 两层的成员；以最终 venv/Node 路径复跑 Codex CLI 0.163.0 的产品发现与直接版本命令，保留失败时的完整成员和创建时间。实际 CLI 版本变化须重新注明，不能沿用旧版本结论。无原生请求时用停止文件替身验证协议先行、native 非空再终止、根随后全空。若产品失败时确实残留 conhost，才可归因于同一机制；若没有，另追失败原因，E 不用过滤计数解决。DSH shim 实现及其版本发现由另一任务交证据。

5. Defender 与文件换代：开发者打开实时扫描，实验首尾记录 `Get-MpComputerStatus` 均为 RealTimeProtectionEnabled=True，保持普通令牌和锁定环境。在独立根先做第 3 节 ReplaceFileW 全矩阵、强杀窗口和真实恢复器，再至少 1000 次无受控读者、无重试连续发布，分别统计人为占用与热路径异常、errno/winerror及迭代号。异常时保留实际旧/新/临时文件与摘要，释放自己已知读者后独立记录继续结果。没有偶发则不加重试；有证据才决定唯一边界的有界重试；不满足完整性或 ACL 就不采用候选 API。开发者只需安排设置与桌面，其余探针由后续实施任务执行并保存原始输出。

6. 私有边界与会话补项：在模型无关测试中覆盖 DACL 缺失、对象/条件/未知 ACE、junction/叶子重解析竞态，以及新增 ACL bootstrap 的首次、active 指针和源替换入口；记录唯一规则的接受/拒绝及句柄关闭。若开发者当前桌面与 SSH 的 ProcessIdToSessionId 实际不同，用一边持有的 Local Job 在另一边尝试打开，返回后原会话核对仍活；两边相同就不称跨会话实验，不要求注销或新建会话。不同会话无法取得时保留原生验证缺口，代码仍拒绝跨会话。此项不依赖重启、快速启动、睡眠或休眠实测。

阶段 B：落地已同意的 Python `>=3.12.4,<3.13`、直接 pywin32 依赖和私有句柄/ACL 边界。依赖 A 已有 ACL/稳定 runtime 证据及补项 1、6 的相关结果；改 `pyproject.toml`、uv.lock、`private_dirs.py`、新增 `windows_files.py`、搬移 windows_paths 文件能力及第 2 节列出的 CLI/launcher/transport/turn_io/session/fast cwd 入口。验收单一 ACL 规则、祖先固定、fd 单次转移、bootstrap 加载及 POSIX 原边界回归；对应 private directories、Windows workspace、CLI 凭据、session、fast Router 和安装原测试补原生情形。不改消息/事实格式或 SQLite 存储形式，单独安装只移动末号，不开待执行升级。

阶段 C：已完成，合入 `516c6d5d`，测试挂起修复合入 `e1e4d1d6`。共用 `private_files.py:publish_bytes` 及三系统唯一备份 journal 是现状，不再依赖 B 才算完成。其实现没有改跨进程事实或看板存储形式，不因此开升级或移动 CONTRACT_VERSION；测试修复本身不移动版本。这里只留 Windows 带读者换代后续：依赖 B 的句柄边界和补项 5，通过实验门槛后改 `windows_files.py`、`private_files.py` 的可变文件换代及失败清理，复跑 private publication、native journal、`test_backup_publication.py`、upgrade 恢复的相应中断用例。事实形式不变，单独安装移动末号，不因换 API 开升级。

阶段 D：Windows 独立服务启动、真实执行身份、根 Job 与会话接回。依赖 B、C 的既有发布实现、Windows 换代后续及补项 2、3、6；改 transport、install 的 launcher/runtime/upgrade 启动入口、`protocol/windows_process.py`、owned_process、process_controller、process_identity、execution、`buddy/runtime/execution.py`、调度器、interruption 和 store/db。实现本文的根/服务句柄次序、183 拒绝、真实 PID 与 sessionId、同句柄停止观察及 Windows boot-changed 不独立结算；删除旧分散开关 Job 和将 Popen PID 当真实执行的路径。验收服务重启保活、执行强杀后的全空、转发器/孙进程归属、跨会话 unknown、名字消失及 boot 变化不误结算、查询失败不释放。新增/扩充 Windows process、process identity、execution recovery、transport 冷启动、安装回滚与执行围栏测试。

D 改变执行/服务交接事实及 SQLite 身份形式，必须同步改 pyproject.toml 的中间号及 `protocol/contracts.py:CONTRACT_VERSION`，开启显式待执行升级；改 db 的升级逻辑和 `tests/python/blackboard/store/upgrade_fixture.py`，由 test_upgrade_fixture 比较新建与升级结果。新的统一身份形状增加 sessionId：Windows 为 ProcessIdToSessionId 的非负整数，POSIX 明确为 null；startup、intent、marker、live、native journal 及 `attempt_process_identities` 同步升级到唯一新形式。执行根 PID 的含义明确为真正执行 Python，Popen 启动进程仅留创建者持有的临时句柄，不再作为第二份持久身份。启动前 intent 的 boot/session/Job 绑定只说明预定启动，不能补出尚未取得的执行 PID；中途崩溃或 pending 仍未知。没有状态目录身份字段，也没有相应升级 stock 或副本用例。

阶段 E：共同接收/最终结算与 Windows 全后代停止。依赖 D 和补项 4；改 ProcessHandle、原生运行统一收尾、native journal、receipts、store 结果入口、调度/interruption 与 db 的接收表。验收原生协议先行、native 全空才回执、服务已接收响应不等根退出、根空前不释放、不启动冲突工作、不回收检出；服务先崩、执行先崩、强停残留、ACK 前后和完成事务前后崩溃均只结算一次。覆盖 execution delivery、run execution、native tree、execution recovery、store 结果/容量/claims 测试；POSIX 仅覆盖完整身份及原组，保留脱离后代缺口。

E 的 ACK 语义、保留回执的交接规则和新增接收表均改变契约/存储形式，不能作为末号改动。若 D/E 在未安装的一批内合入，使用同一个新中间号/CONTRACT_VERSION，并在 D 开始的同一待执行升级中完成；若 D 的升级已经在真实板运行，消费升级、刷新基线与 fixture，E 再开下一次升级及中间号。删去旧 delivery 文件及无读者路径，对非封存存量在升级中显式清理；不保留旧“收到即释放”的解释或兼容分支。

阶段 F：忙文件和测试观察/关闭。依赖 B 的文件边界、D/E 的生命周期；已核实的手建 store/SQLite close 与 `/bin/sleep` 替换可先独立修。改产品中第 6 节列出的恢复、清理入口及 `cli/checks.py`、`tests/python/support.py`、原关闭遗漏测试、native journal 和 checks cleanup 用例；失败保留恢复计划，私有测试根单独处理 READONLY，Windows 用已拥有的 Job/身份观察，完整收尾不能观察时保留根并退出 3。验收解除占用后同计划恢复、SQLite 全连接关闭、runtime 先保留后删除、转发器真子进程结束、无 ps 依赖。此阶段不另改事实/存储形式，产品代码单独安装移动末号，纯测试改动不移动版本。

阶段 G：Host 整合、独立审查与安装前演练。依赖 B、C 后续、D、E、F 分别验收和对应原生实验；独立审查检查是否仍有重复机制或代价不相称，不将设计批准或 Worker 完成当作验收。最终源码在三系统按 AGENTS.md 跑完整 Python 检查，分别保存退出码和 teardown；Windows 11 桌面/Defender/真实启动链证据单列。私有安装分别演练同契约服务重启保活和变契约的空闲、备份、失败回滚；旧版本发出的安装请求只用该版本接受的字段。G 不新增契约或存储形式，文档/证据本身不移动版本。日常安装由 Host 另行安排。

升级基线仍为 0.49.0、schema 27，当前无待执行升级；本设计修订不启动升级。D 开始时 fixture generator 必须提供每种待转换/清理的身份、启动、native journal 及 delivery 存量，E 加入它处理的全部存量；升级后的 fixture 与新建板比较。首次契约变更安装要求空闲，不给旧 Windows 记录猜填当前 sessionId 或把启动器 PID 改成当前进程；对不再用于恢复的已结束非封存身份、启动及 journal 存量显式清理，不保留旧形式的运行时读取或猜测分支。sealed snapshot、回执及其摘要绑定内容原样保留，新封存明确形式。升级同步删除触及表内无读者或可推导的重复数据；接收表的固定路径不存第二份，meta 仍只放 schema 和 secret。若升级执行时尚有无法确认停止的旧执行，安装不得越过空闲条件。

需要 Host 修改 ADR-031：第 1 条补“执行在原生工作停止后交回执，收到已接收回应退出，服务确认完整边界结束后最终结算”；第 4 条保留长调用这一种失联通知，注明 Windows 句柄仅保持原 Job 对象供停止/结束观察，Windows BootIdentifier 变化不能独立证明已停，丢失观察证据保持 unknown；第 5 条把 Windows 身份写成真实执行 PID、原始 FILETIME、sessionId 与 Job；第 7 条明确 ACK 只表示可靠接收，孤儿按持有的原 Job 停止且观察全空。第 10 条的契约与升级规则不改，第 13 条开发流程决定原样保留。ADR-010 2026-10-14 及 architecture 的“不同开机会话即可证明已停”措辞由 Host 同步限定到有依据的平台，Windows 按本文保守规则；operations/workers 的接收、busy 和恢复边界随实现更新。本文不越出范围编辑这些文件。

## 9. 本轮修订

删去状态目录文件身份围栏、原第 7 节和阶段 0，移除启动/接回/升级/副本验收对它的全部依赖；Python 下限改为已同意。阶段 C 改为已完成，记录 `516c6d5d` 和 `e1e4d1d6`，只保留读者持有时 Windows 换代的后续。

并入阶段 A 对 Job 最后句柄、同名创建、venv 转发器、conhost/CTRL_BREAK、SSH 外层 Job、ACL、读者占用、SQLite/READONLY/runtime 的证据及未验证边界。根 Job 改为 kill-on-close，执行持有跨服务重启的句柄，服务关联期间持有观察句柄，根退出时显式终止并观察全空；失去对象不能借名字消失结算。执行身份指向实际 Python，保留 venv 正常启动语义；不将 Codex 发现失败直接归因于 conhost。

Windows 文件换代选择 ReplaceFileW 候选并列清采用门槛，保留现有备份协议，Defender 没开启的结果不支持添加重试。删除电源实测前置条件，Windows BootIdentifier 不同不再独立推出已停，明确由此保留 unknown 的边界。重排 B 至 G 的依赖、文件与验收，逐项重判中间号、CONTRACT_VERSION 及升级，仅 D/E 改契约/存储形式；列出桌面协助步骤和 Host 需改的 ADR 条款。
