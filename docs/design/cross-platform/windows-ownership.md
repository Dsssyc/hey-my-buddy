# Windows 第二批：执行所有权、停止证据与私有存储

2026-10-21，设计候选，修订基线 `92031c1deb30e302b9f60e571524c146a367060d`。依据为[分批计划](plan.md)、[清单](inventory.md)、[进程实验](windows-experiments-process.md)、[文件实验](windows-experiments-files.md)、当前源码、AGENTS.md、[架构](../../reference/architecture.md)、[ADR-007](../../decisions/007-neutral-core-and-single-current-contract.md)及 [ADR-031](../../decisions/031-one-process-per-execution.md)。实验结论只覆盖记录中的 Windows 11 x64、NTFS、令牌与启动形式；本轮没有运行新实验或检查套件。实验文档引用的原始输出在本检出中未提供，本文采用其逐项摘录及明确的未验证边界，不声称重新核验过原始包。

支持范围为 Windows 11 x64、本地 NTFS、同一 Windows 会话、Python `>=3.12,<3.13`；阶段 B 一度把下限提到 3.12.4，只为 Windows 上 `mkdir(0o700)` 的受保护 ACL，第 2 节不再用它，下限随精简回到 3.12。Windows 系统服务、网络盘、FAT/exFAT、Windows ARM 不在首发范围。三系统共用任务身份、事实发布、结果接收及最终结算；Windows 的 ACL、Job、会话与文件共享行为在系统边界实现。Linux/macOS 脱离进程组的后代留在另一批，本批不宣称解决。

ADR-031 第 13 条已经定为开发流程约束：开发使用真实数据只经导出工具取得已验收微任务，用完即删。产品不增加状态目录身份，不为看板副本增加接回、停止或结算围栏，也不实现目录文件 ID 的读取或副本验收。下面的 Windows 会话核对服务于 `Local\` Job 的定位，与状态目录无关。

## 1. 已有证据与本批的起点

阶段 A 的两份实验记录均使用基线 `d5c908aaaed3cfd66c20822222570a1e267ff9f2`。进程实验的基础解释器为 CPython 3.12.13；私有 venv 的 `Scripts\python.exe` 转发器另起真正解释器，曾出现 `Popen.pid=860`、脚本 PID 为 28840，脚本查到当前 Job flags 为 `0x3000`，与探针显式设置不同；这一轮本应被禁止的显式 breakaway 请求还返回了成功。额外 Job 的创建者没有追踪，也未核清该成功请求的成员归属。随后直接用基础解释器复验，Popen PID 与脚本 PID 相同，禁止脱离的请求返回错误 5。系统结论以基础解释器复验为准，真实 venv 链的脱离能力仍须定向测量，不能假定已被根 Job 围住。补充实验（[a3 记录](windows-experiments-a3.md)，基线 `f7345410`）已经量过：经 venv 转发器，显式请求返回成功，但创建出的进程仍在根 Job 里，32 次创建没有一个离开根，结论见第 4 节。

无 kill-on-close 的命名 Job 关闭最后句柄后，原成员仍以原创建 FILETIME 存活，`OpenJobObjectW` 返回错误 2；同名再建得到一个空 Job，原成员不在其中。同名对象尚有句柄时，`CreateJobObjectW` 返回有效句柄并报告 183。根/native Job 嵌套、native 最后句柄关闭使成员退出、禁止脱离时显式 breakaway 返回错误 5、四条 shell/uv/Node 孙进程路径的根 Job 成员关系都有本机记录。它们没有验证本设计的新根句柄持有次序。

文件实验中，提权的 Python 3.12.13 用 `mkdir(0o700)` 得到受保护 DACL，主体为 SYSTEM、Administrators、OWNER RIGHTS；3.12.3 的同一参数仍继承父 ACL。这正是第 2 节不在 Windows 上用 0o700 的原因。新目录、普通文件及 SQLite 文件的 owner 为 Administrators。普通用户、交替提权使用尚未验证；实验没有运行恰好 3.12.4，不能用这组数据证明变更精确始于哪个补丁。锁定运行时在源目录被替换后仍可正常导入 `win32security`，但尚未执行拟新增 ACL bootstrap 的全部入口。

私有事实发布与备份阶段 C 已实现、验收并合入 `516c6d5d`；当前入口是 `private_files.py:publish_bytes`，三系统备份共用 journal，exchange 分支及以只读句柄补刷盘的循环已删，gzip 尾部由写者刷盘。`e1e4d1d6` 修复发布测试的 Windows 挂起：握手改为字节协议，避免 CRLF 不匹配后阻塞读 stderr；写者直接用基础解释器，避免只杀掉 venv 转发器。此为测试修复，不能据此声称产品执行启动链已经修好。

私有目录与文件的阶段 B 已实现、验收并合入 `755c476c`（[验收记录](../../acceptance/cross-platform/phase-b.md)）：Python 下限 3.12.4，pywin32 为直接依赖；`windows_files.py` 持有句柄层与唯一 ACL 规则，私有入口逐级以 0700 新建并在各级句柄上校验，句柄的最终路径须是请求路径的长名；服务、执行、启动器与 CLI 在提权令牌下先把默认属主设为当前用户。测试机上的独立复跑量到：提权与普通令牌交替在同一状态目录上起停私有服务，每个私有对象（含 SQLite 的 WAL、SHM）的属主都是当前用户，ACE 只有普通 allow 且受托者在白名单内，WAL、SHM 在每次换令牌后重新建出。它在 Windows 上把安装发布路径也送进了私有边界。同日开发者认为这套私有边界层层设防、与要防的情形不相称，定为精简：第 2 节重写，阶段 B2 删去逐次固定私有路径的祖先、逐次 ACL 校验、私有路径的名称比对、提权进程改默认属主与 Windows 上的 0o700（第 8 节）。

Host 提供的第十六轮 run `39579709581` 状态是 Windows 的 302 个测试文件已过 168 个，不能称为整轮通过。本批剩余失败按入口处理：私有目录/文件的 mode 判断归 B，Job 生存期及身份归 D/E，检查运行器用 `ps` 观察收尾而退出 3、`test_native_process_journal.py` 使用 `/bin/sleep` 归 F。旧第十轮错误行的粗计数不再作为实施依据。

## 2. 私有目录与文件：继承 profile 的 ACL，边界上查一次

私有状态指状态目录里的看板、令牌、凭据、控制文件、会话，以及安装的私有文件（active 指针、启动设置、安装日志、锁与备份）。私有规则防得住的只有同一台机器上的其他普通账户：本账户和它起的进程（包括 Worker）本来就能读，SYSTEM 与管理员也挡不住。Windows 给每个账户的 profile 目录（`C:\Users\<name>`）配的 ACL 只有本账户、SYSTEM 与 Administrators，其他普通账户进不去；状态目录默认在 `%LOCALAPPDATA%` 之下，继承这张 ACL 就满足要求。这一节 2026-10-22 由开发者定，取代阶段 B 的逐次句柄校验（第 9 节）。

Windows 上的私有目录与文件照常创建，继承上层的 ACL，不传 0o700。Python 3.12.4 起，`mkdir(0o700)` 在 Windows 上写一张受保护、不点名用户的 ACL（SYSTEM、Administrators、OWNER RIGHTS），访问随属主走：提权终端建出的对象属主是 Administrators，同一账户的普通终端打不开（[桌面实验记录](windows-experiments-desktop.md)）。继承来的 profile ACL 明确写着本账户，提权与普通终端都能用，不需要改令牌的默认属主。POSIX 照旧逐级以 0700 新建。

状态根在安装与冷启动时查一次：路径上没有链接（符号链接或目录联接）；属主是本账户或 Administrators；DACL 存在且非 NULL，全部 ACE 是普通 allow，受托者只有本账户、SYSTEM、Administrators 与 OWNER RIGHTS。查不过就拒绝并说出原因，例如状态目录被设到了共享位置。这一条对应 POSIX 的“状态目录 0700、属于本人、上层不能被他人替换、路径上没有链接”。状态目录之内的读写不再逐次校验。凭据经继承的文件描述符转交时只核对它是普通文件，POSIX 另照旧核对属主与 mode。ACL 的查询用 pywin32，它仍是直接依赖；DACL 缺失与对象、条件、未知 ACE 等形状用合成列表写单元测试。

安装发布路径不属私有：skill 目录与它的运行时提示、运行时根及其清单与 READY，两个系统同一政策，允许上层目录是链接，不按私有 ACL 校验（`tests/python/install/test_publication_paths.py`）。

工作树不同：Worker 在工作树里有写权限，可能放下联接，把服务骗到工作树之外读写。工作树的文件操作因此仍以 `GENERIC_READ`、不共享删除固定每一级上层目录，拒绝重解析点并核对名称（`blackboard/tasks/windows_paths.py`，句柄层在根包的 `windows_files.py`）；POSIX 用 dir_fd 与 O_NOFOLLOW 做同一件事。补充实验证实，只请求 `FILE_READ_ATTRIBUTES` 的句柄挡不住改名，以 `GENERIC_READ` 固定后改名以错误 32 失败（[a3 记录](windows-experiments-a3.md)）。

事实的发布与读者由第 3 节规定：FileRenameInfoEx 按句柄换代，产品自己的读者允许删除共享。这是让有人读着的文件也能换代的功能要求，不是安全层。

## 3. 事实发布、Windows 换代与开机会话

现有 `publish_bytes` 同目录独占建临时文件，写完整内容并在写句柄上 flush/fsync，关闭后发布；不可变对象 compare-and-create，相同内容可复用、不同内容冲突。POSIX 刷父目录，Windows 不打开目录刷盘。承诺是写者进程中断后读者得到完整旧版、完整新版或明确未完成；不承诺 Windows 目录项的断电耐久性。SQLite 仍是权威状态，WAL、synchronous=FULL 及事务/事件原子性不变。

该入口已经覆盖启动意图、startup、身份 marker、native journal、live/budget、回执、交接事实、封存证据、备份及安装 journal/指针。各调用者仍持有自己的路径政策，阶段 B 统一句柄边界。诊断 stdout/stderr、console session 和内存提示不作为停止证据。备份在锁内完成并验证 incoming、写 journal、将 current 改名 previous、incoming 改名 current，验证后删除 previous 及 journal；正式读者先恢复或报告进行中，首次没有完整一代时可报告未完成，不能删除唯一已验证的一代。这些是阶段 C 的现状，不重新排作待实现机制。

文件实验的四个单文件窗口各重复三次：写完未 fsync、fsync 后、替换前被杀都读到完整 OLD，替换后被杀读到完整 NEW。此证据没有覆盖 native journal、多文件绑定、gzip 尾部、备份恢复器或断电；阶段 C 的验收也不能替拟换代的 Windows API 完成验收。当前 `publish_bytes` 的 Windows 可变发布仍调用 `os.replace`，finally 会清理其临时文件；不能写成当前产品已经保留失败的临时文件或支持带读者换代。

Windows 可变文件发布优先选择 `SetFileInformationByHandle(FileRenameInfoEx, POSIX_SEMANTICS | REPLACE_IF_EXISTS)`，先做实验再落地。它在临时文件句柄上用一条路径覆盖目标缺席的首次发布和已有目标的换代，带过去的是临时文件从目录继承的 ACL；新实现写完后仍保留该句柄，flush/fsync 后按句柄改名，完成后关闭。不可变对象继续 compare-and-create，备份的目录 journal 协议保持现状。不实现 delete-then-rename，不保留多套替换 API 的运行时回退。桌面实验已选定这一机制（[桌面实验记录](windows-experiments-desktop.md)）：普通令牌下连续发布 1000 次、无重试、零错误；首次发布、无读者与允许删除共享的读者都成功；禁止删除共享的读者让发布以错误 32 失败，目标带 READONLY 时为错误 5，所以产品自己的读者一律允许删除共享，产品也不给自己的文件设 READONLY。实验时机器上的实时扫描来自联想预装的杀毒软件（火绒引擎），Defender 处于被动状态，未测。

现有文件实验只证明：允许删除共享的目标读者持有时，os.replace 和 `MoveFileExW(MOVEFILE_REPLACE_EXISTING)` 各三次错误 5，ReplaceFileW 三次成功；POSIX 语义改名尚未试过。Host 本轮审查指出 ReplaceFileW 对目标缺席需要另一条路径，且会把被替换文件的 ACL、属性与对象 ID 合到新文件，因此先试按句柄的 POSIX 语义改名。上述新 API 的完整性、元数据与耐久性均未在测试机验证，不能由选型推导已可用。

采用前在同一私有 NTFS 根验证：临时文件的改名访问权和共享方式；目标缺席及已存在；普通 Python、允许删除共享、外部禁止删除共享的读者；发布后旧句柄读 OLD、新打开读 NEW，目标不暴露半份或无故缺席；失败后的目标/临时文件、原始错误码和摘要；临时文件与目标的 owner/DACL、属性及文件 ID；fsync 后、API 前、调用中竞争窗口及 API 后强杀写者，再用新进程检查摘要；最后跑实际 native/备份/安装 journal 的恢复用例。调用中窗口未命中就标未覆盖，不能冒充逐指令覆盖。正常事实读者允许删除共享；禁止共享的外部读者应明确报忙，释放后可继续。

若 POSIX 语义改名满足上述门槛，只落地该机制；若不满足，保存具体失败事实后才对 ReplaceFileW 做同一矩阵，并额外验证目标缺席的发布路径和目标元数据合并的影响，再选择一个机制。两个候选均未通过就不安装 Windows 换代后续，不在运行时自动换 API。进程中断后的恢复按实际文件和摘要判定，失败时保留仍存在且恢复需要的完整文件，不在 finally 无条件删除唯一候选；不承诺 Windows 目录项的断电耐久性。Defender 条件见第 7 节，不因错误 5 一律重试。

当前 Windows boot_id() 读取 BootIdentifier，实验只证明同次开机五次短间隔稳定；跨重启、快速启动、睡眠、休眠如何变化不再安排实测。相同值不能证明旧进程还在，不同值也不用于推断旧进程已停；“保留进程却改变标识”若被解释成已停，会产生假停止。既然两种比较都不参与 Windows 判断，阶段 D 删除 Windows BootIdentifier 的读取、记录与比较，不存一个不起作用的值。Windows 只用真实执行 PID、内核创建 FILETIME、sessionId 与根 Job，按第 4 节停止规则恢复；Linux 的 boot UUID、macOS 的 bootsessionuuid 保持既有规则。重启后的停止结算和后续调度不依赖电源实验，也不因没有全空采样而永远占用资源。

## 4. 服务启动、执行根与 Job 的句柄生存期

当前服务冷启动 `protocol/transport.py` 和回滚启动 `install/upgrade.py:start` 只用 start_new_session；Windows 缺少独立于控制台/外层 Job 的启动。服务统一采用 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`，创建失败直接报不满足独立启动，不换 flags 重试。补项 2 核实“不在 Job 中时 breakaway 被忽略”的行为，成立就不加预查外层 Job 的分支；若不成立，才在启动边界区分有无外层 Job。不能只根据最近一层 SILENT_BREAKAWAY_OK 猜整个嵌套链允许脱离。

依据是 SSH 当前 Job flags `0x2800`：仅 detached/new-group 的服务替身及孙进程在连接关闭后缺席，加显式 breakaway 则存活；自建禁止脱离的外层请求 breakaway 以错误 5 失败，自建 silent 外层不显式脱离时虽能活过该层关闭，仍活不过 SSH 层。记录中的“仍属于某个 Job”不能解释为已脱离全部外层。桌面 PowerShell、VS Code 及实际 Host 工具 shell 仍须独立验收；外层不允许的环境明确不支持，内部再加 Job 不能抵消它。

当前 `protocol/windows_process.py:create_job` 已对每个 Job 设置 KILL_ON_JOB_CLOSE；`protocol/execution.py` 经 launch_controller、owned_popen 创建执行根 Job，句柄只留在服务的 Popen._buddy_job，执行没有接持自己的根句柄。所以当前 Windows 服务每重启一次，都会因最后句柄关闭杀掉在跑执行；ADR-031 第 10 条要求保活的同契约安装也受影响。这是阶段 D 要修复的现存生命周期缺陷，修法是改变句柄归属，保留 kill-on-close。

执行根定义为实际运行 `hey_my_buddy.buddy.runtime.execution`、承载 ExecutionLive 的 Python 进程。当前启动器 `install/launcher.py:_runtime_python`、稳定运行时 `install/runtime.py:runtime_python` 和升级/回滚都指向 `venv\Scripts\python.exe`；transport 使用选定运行时解释器，执行再用 `sys.executable -m ...execution`。实验环境中这条链经过 venv 转发器，基础解释器才运行 Python。服务与执行的真实 PID 由自身报告并取内核创建 FILETIME；Popen PID 是创建的启动进程，只用于回收该句柄，不能冒充真实服务或执行身份。

启动链保留 venv 入口及其 site/.pth、依赖和固定运行时语义。服务 suspended 创建启动进程，分配到根 Job 再恢复。补充实验（[a3 记录](windows-experiments-a3.md)，CPython 3.12.13）量了真实链：经 venv 转发器，真正解释器及其孙进程带显式 CREATE_BREAKAWAY_FROM_JOB 的创建都返回成功，但创建出的转发器和真正解释器仍在根 Job 里，不带标志的也都在根里；基础解释器直接在根里时，显式请求以错误 5 失败。32 次创建没有一个离开根。所以显式请求返回成功不说明离开了根，后代归属只以 IsProcessInJob(root) 为准，额外 Job 的 flags 不是根成员关系的替代证据。

根 Job 由本项目创建，使用随机 `Local\HeyMyBuddy-<uuid>` 名字。CreateJobObjectW 返回后立即检查 ERROR_ALREADY_EXISTS=183；已存在就关闭返回句柄并失败，绝不先设置 flags 或接管它。在任何进程加入之前设置 KILL_ON_JOB_CLOSE，禁止 breakaway 与 silent breakaway；实际启动链须通过上段实验，确保自然派生的全部成员都不能脱离。Job 使用令牌默认 DACL，不增显式描述符。启动前 intent/startup 绑定 task/attempt/generation、Windows sessionId 和预分配 Job 名，不记录 Windows BootIdentifier，也不增加状态目录身份。

服务只在创建到执行确认已持有自己句柄的窗口保留根 Job 句柄。真正执行进程启动后先打开并持有根 Job，用自身 PID、原始 FILETIME 与 sessionId 走现有身份确认，服务在这一边界核对真实执行属于原 Job，服务将真实身份写入 marker 和 SQLite，执行的 live 描述符使用同一身份；不能要求它等于 Popen PID。确认成功前不启动原生工作，确认后服务立即关闭创建句柄。执行的根句柄不可继承、持有到进程退出，是唯一的长期根句柄；native Job 仍由执行持有、kill-on-close 且禁止脱离。无需句柄复制、继承或新移交协议。窗口中服务先崩溃会关闭其句柄；只有启动意图而没有根身份 marker 的情形仍为 unknown，不把“创建过”当作已完成登记或已停。

服务重启后的接回从权威 attempt 与固定私有证据出发，先核对当前 Windows 会话与记录 sessionId；不同或读不到就 unknown，不连接、不发信号，也不能解释另一个 Local 名字空间中的错误 2。相同会话中按名打开原 Job，核对原始 FILETIME 与真实执行的根成员关系，完成这次接回核对就关闭临时句柄，不在关联期间保留。只按原名打开，恢复不调用 CreateJobObjectW 补空对象；名字用于定位，完整身份和创建不变量共同构成授权。业务校验在入口做一次，服务仍只用原长调用知道执行失联，不加心跳、进程等待或看门狗。

停止时服务按名打开并在同一句柄上完成身份核对、TerminateJobObject、观察 ActiveProcesses=0，最后关闭；不再分别按名字查询和另开对象终止。所有成员均计数，包括转发器、conhost、shell 和嵌套 native 成员。根已退而原 Job 还能打开时，同样终止遗留成员并观察全空。强停过程中服务先崩溃，临时句柄由 OS 关闭，随后仍按下面的名字缺席规则恢复，不要求保住一次采样。

已经记下根身份的执行有两种 Windows 停止证据：同一会话中，根完整身份已缺席且持有的原 Job ActiveProcesses=0；或根完整身份已缺席，OpenJobObjectW 对记录的随机名字明确返回 ERROR_FILE_NOT_FOUND=2。第二种依据是根由本项目独占新建、加入任何进程前已设 kill-on-close、全部后代不能脱离：最后句柄关闭会由内核终止全部成员，嵌套 native 成员也在其中。现有 inspect_process_identity 在名字缺席且根缺席时返回 identity-absent 的判断保留，补上 sessionId 前提并删除 Windows boot 比较。flags 为 0 的实验反例不能推广到本设计的 kill-on-close 根。

执行退出或被强杀时，其最后长期句柄关闭并收掉残留；服务若恰在停止作用域内持有句柄，就负责终止、观察全空后关闭。重启、快速启动关机、服务与执行先后崩溃、执行退出而服务尚未观察全空就重启，均可在同一会话用上述身份缺席规则结算，再继续调度后续工作。只有启动意图而没有根身份 marker、错误 5、会话不符或不可读、身份或成员查询失败仍 unknown；API 成功布尔值、裸 PID、新建同名空 Job 和按进程名过滤都不是停止证据。补项 3 专门验证 kill-on-close 最后句柄关闭后立即按名得到错误 2，已登记的真实 venv 链、孙进程和 conhost 在有界时间内身份缺席。

有序停止沿现有 ExecutionLive stop 进入 harness 原生 interrupt/close，宽限后强停 native Job，必要时强停根 Job。`process_handle.py:signal_group` 的 CTRL_BREAK 不是无控制台的通用软停：实验的 NO_WINDOW/new-group 替身调用成功但 0.5 秒后仍活着，私有停止文件协议则退出 0，TerminateJobObject 则强停后全空。停止文件仅为实验替身，产品不新增文件停止协议。command 替身无原生协议，不承诺 POSIX SIGTERM 的处理机会，不伪造负退出码。

执行与 native 子进程保留 `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`，仍先 suspended、加入 Job 再恢复，不设 breakaway；只有服务使用 detached 加 breakaway。NO_WINDOW 不证明没有 conhost，残留由终止 native Job 并观察全空处理。桌面实验已实测（[桌面实验记录](windows-experiments-desktop.md)）：detached 的服务替身以 flags=0 起的 Python 子进程得到可见窗口，从 PowerShell 与 VS Code 终端启动时都出现；而 NO_WINDOW 的执行经 cmd、pwsh、git 派生的后代，采样时都没有窗口。所以服务自己起的每一个控制台子进程都必须带 CREATE_NO_WINDOW，包括阶段 D 放进 Job 的发现探针。因此不为消除 conhost 改执行/native 的启动方式，补项 2、4 只验证保留方案的窗口及收尾，不安排 detached 对照。

## 5. 后代停止、conhost 与共同结算次序

进程实验的 NO_WINDOW 与 new-console 均观察到 conhost 计入 ActiveProcesses，嵌套 native 成员退出后根中也可能剩 conhost。私有服务及 DSH CLI 0.2.1-rc.2 的版本命令结束后有残留 conhost，终止其受控 Job 后全空。Codex CLI 0.163.0 产品发现返回 harness.shutdown-unverified，但另外直接用 Node 加 JS 的受控版本命令退出 0 且 Job 已空；产品发现那次没有足够成员归因证据。因此 conhost 是 Codex 失败的待核对原因，不能写成同一根因已证实。DSH Electron shim 的解析实现由另一微任务负责，本设计不处理它。

让 Job 全空的路径是：先走 harness 原生协议收尾并等待其主进程退出；若此时 native Job 仍非空，终止整个 native Job 并观察全空，记录实际发生的强停/清理，不把成功发 CTRL_BREAK 或主进程退出当作全空。无须判断剩下的是 conhost 还是业务后代。只有全空才关闭 native Job、封存输出并写回执；强停影响结果分类时沿现有 terminationReason 规则表达。正常回合也做这次非空观察，不能仅在取消路径清理。执行自己的根 Job 包含执行本身，不能由执行终止它来完成正常交付；执行退出时最后根句柄关闭收掉根中残留，服务按第 4 节停止证据结算，主动强停时只临时打开根句柄。

真实启动链实验必须分别复跑 Codex CLI 0.163.0 的产品版本发现与直接 Node 加 JS，记录 Job 全成员及主进程退出时刻，使用保留的 NO_WINDOW/new-group；DSH CLI 版本的既有残留证据只用于停止设计，其 shim 支持单独验收。需要验证嵌套 Job 中 conhost 究竟计入哪层，以及 native Job 全空、执行退出后根成员缺席均可达到。现有 CLI 实验没有运行模型回合，不据此宣布 interrupt/close 或完整 harness 已验证。

三系统共同次序为：native 工作停止已确认后写不可变回执；服务核对绑定、摘要及产物边界并可靠接收，立即响应“已接收”，attempt 保持 finalizing 和全部资源；执行收到响应退出；服务经原有长调用失败/创建句柄回收进入停止观察，Windows 确认真实执行的完整身份缺席，并取得原根 Job 全空或同会话原名错误 2 的证据，POSIX 确认完整身份与已登记原组结束，然后一个事务导入最终结果、事件并释放容量及 claims。成功回执到达不提前完成，不加另一种交付结果，也不等根退出才响应 RPC 造成死锁。

具体收敛为一个接收记录：新增 `attempt_received_receipts`，attempt_id 为外键及主键，只存已接收不可变回执的摘要；固定路径由 attempt 推导，不重复存路径或整份 report。插入该记录与置 finalizing 同事务，重复同摘要幂等，不同摘要冲突；响应采用唯一的已接收语义，删除“收到即提交完成”的旧分支。最终结算读同一份回执并在完成事务删除接收行；文件作为冻结证据随所属微任务删除，不因收到 ACK 就删除。无回执时仍走已有的中断/部分封存结算路径，停止未知时保留占用。新增表的 stock、约束、恢复读取及清理随 E 的升级一起落地，不用 meta 或无结构 JSON 槽存接收状态。

`buddy/runtime/receipts.py:deliver_receipt` 当前在 submit_result 后写 delivery 并删除 receipt；该次序要删掉，ACK 后仅退出，恢复不再靠 delivery 文件。`blackboard/store/store.py:execution_result_reference/execution_result/_commit_execution_result`、调度恢复和 interruption 只保留上述一条结算路径；回执保持封存原文，不改写 shutdownConfirmed 为假值延迟完成。接收记录及原始回执的恢复保证服务可在接收后、执行退出前后或最终事务前崩溃，回来只结算一次。没有足够 Job 结束证据时，即使接收行存在也不提交完成。

Windows 用禁止脱离的根/native Job 覆盖自然派生后代；通过既有外部进程代起的工作不自然属于 Job，照实记录边界。POSIX 的 native journal 只登记本项目创建的原生根，不能证明 setsid、双重 fork 或脱离后代已停；既有 ZCode CLI 待办没有核实其逃逸路径和版本，不扩写为已复现结论。本批不实现 cgroup、PID 递归或 cwd 扫描，也不以该后续批次阻塞本批安装，验收仍须写明缺口。

## 6. 忙文件、运行时保留与测试收尾

事实读者关闭自己不用的句柄并允许删除共享，读取校验针对持有的那一代；换代由第 3 节唯一实现承担。工作树操作固定的祖先，作用域在删除或改名目录自身前退出。普通读者和外部禁止删除共享者阻止删除/替换，实验实际出现错误 5 与 32，不能把 5 一律当 ACL 或把所有忙错误预设为 32；错误保留原始码、操作及受限相对路径。长期占用报忙并保留可恢复现场，不无限重试或删除目标再发布。

`Database.connect` 缓存连接，退出事务不等于 close；daemon cleanup 已调用 db.close，不能说产品没有关闭数据库。恢复替换 board/sidecar 前让唯一所有者全部连接关闭、释放操作句柄，WAL checkpoint 和安装协调围栏保留。实验中只读、读写 SQLite 连接都阻止删除/改名，关闭后成功；外部查看器占用时保留 journal 和备份，不删 WAL/SHM 绕过错误。释放占用后继续同一恢复计划。

检出删除继续交 Git 和既有注册/锁，私有树删除保留现有计划/墓碑；一部分已删不等于整个检出回收。产品删除器不加清除 READONLY、放宽 DACL、递归 chmod 源仓库的能力。测试合成 Git 对象的 READONLY 已实测会以错误 5 阻止删除，清除该对象属性后成功；只允许专用测试根删除器在全部测试工作已停后清该叶子的 READONLY 并重试一次，其他错误保留根。

已加载 Python 实测阻止删除可执行文件及完整 runtime，关闭后可删，但第一次递归删除可能已删掉其他文件。旧 runtime 必须在进入删除前按 ADR-031 第 10 条确认没有在跑或 unknown 执行引用，不能试删后才发现占用。安装切换失败保留恢复证据，切换后的延后清理报告保留原因，不吞失败或停止无关执行。目录联接的安装支持属第三批。

测试辅助显式关闭手建 store、SQLite 连接、console、RPC 与线程；BoardTestCase 不递归搜未知对象。`test_harness_health.py` 的手建 health store 与 `test_backup.py` 只管理事务的 sqlite context 分别在原测试登记 close，不靠 gc/rmtree。检查运行器在 Windows 删除 `ps`/lsof 假设，优先使用它创建并登记的 Job/进程句柄和精确身份观察自建进程；通用枚举需要时复用已锁定 psutil，不实现另一套跨平台扫描器，不把 open_files 空列表当无文件占用的完整证明。不能确认收尾仍保存私有根且退出 3，不向扫描得到的陌生 PID 发信号。

`tests/python/protocol/test_native_process_journal.py` 的 `/bin/sleep` 改为跨平台、可明确握手的 Python 替身；Windows 测试要结束真正写者/成员，不能只 kill 转发器。字节握手和有界 communicate 沿用 `e1e4d1d6` 的修复，不再阻塞读 stderr 等待未退出子进程。POSIX 专用 killpg/FIFO 用例标明平台并补 Windows Job 对应验证，不能只跳过失败。

Defender 实时扫描在阶段 A 关闭，1000 次串行无重试发布全成功只能证明该条件。开启后的占用是否瞬时仍未知，先做第 7 节实验；只有捕获确定会自行释放的短暂占用，才在唯一文件边界设计一次有界重试。持续读者占用、ACL 拒绝和身份未知不重试成成功，调用点不各加重试。

## 7. 阶段 A 的补充实验与桌面协助

阶段 A 已完成上述两份模型无关实验，相关结论逐项并入本文；剩余实验如下，不包含目录身份或电源操作。每项在新的独立实验根运行，记录源码、Python/CLI/工具版本、令牌、会话、完整身份、Job flags/成员、原始输出、错误码及最后收尾。显式安装探针 Python 使用私有根及 uv 的 `--no-bin --no-registry`，不触碰日常服务、看板和环境。只用模型无关替身及版本命令，不调用模型。

1. 桌面普通令牌与交替提权：开发者同一用户打开普通及管理员 PowerShell，分别记录 TokenElevation、TokenOwner 和组，特别记录普通令牌 Administrators 的 deny-only 属性；固定 Python 3.12.13，在宽继承父目录和默认父目录逐级用 0700 建根，读回每级 owner/DACL/继承。先测原样提权创建后普通访问，再测提权进程先把令牌默认属主设为当前用户的政策；普通建根后管理员创建子目录、凭据及 SQLite/WAL/SHM，普通窗口重新访问，再反向重复，另测 WAL/SHM 重建。覆盖默认状态目录及 PowerShell 先建目录的入口。若这一政策使两种令牌均可用就按此落地；若设置被拒，就采用第 2 节在提权启动边界拒绝的分支，不加主体、不自动修既有 ACL。已完成（[桌面实验记录](windows-experiments-desktop.md)）：提权进程把默认属主设为当前用户之后，目录、凭据、SQLite 及其 WAL/SHM 在两种令牌间可交替访问和重建；不设时普通令牌打不开管理员属主的对象。阶段 B 按这一政策落地；2026-10-22 起改为 Windows 上不用 0o700、继承 profile 的 ACL，两种令牌都直接可用，不再改默认属主（第 2 节）。

2. 桌面启动方和窗口：开发者分别从 PowerShell、VS Code 终端及已有 Host CLI 工具 shell 启动私有服务替身，独立观察方登记真实 PID/FILETIME、会话和 Job，再关闭启动窗口/shell。服务使用 detached/new-group/breakaway；在可确认不属于 Job 的启动方核实额外 breakaway 是否被忽略，并测允许/禁止脱离的外层，决定是否可以一律加标志。执行和 native 保留 NO_WINDOW/new-group，观察其窗口、HWND、conhost，以及它们经 cmd/pwsh/git 起控制台孙进程的窗口和成员；不安排执行/native 的 detached 对照。服务及后代独立存活且窗口行为满足要求才验收相应入口，拒绝脱离的外层明确不支持。实际 Host shell 若不能在无模型调用条件下取得，该入口继续未验证。部分完成（[桌面实验记录](windows-experiments-desktop.md)）：两个入口的启动 shell 都已在某个 Job 里，带 breakaway 的服务替身创建成功；外层禁止脱离时创建以错误 5 失败，按上文明确不支持。服务替身的子进程弹出了窗口，结论见第 4 节。启动窗口关掉之后服务是否继续存活，这次没有采到重叠时段，留到阶段 D 用真实服务验收；Host CLI 工具 shell 未观察。

3. 根 Job 与真实 venv 链：已由补充实验完成（[a3 记录](windows-experiments-a3.md)）。后代没有离开根，保留 venv 入口（第 4 节）。执行持根句柄、服务确认后关闭创建句柄的次序成立，另一进程能按原名接回且成员身份不变；执行正常退出或被强杀后，按名打开在 0.5 ms 内返回错误 2，全部登记成员在 9.1 ms 内缺席；服务持临时句柄时 TerminateJobObject 后 ActiveProcesses=0。各暂停点上的句柄次序、服务重启与同契约安装不杀执行、停止观察中崩溃与先后崩溃后的结算、marker 前的意图仍 unknown，以及重名创建失败而原 flags 不变，由阶段 D 在产品上验收，不再单列实验；产品上不成立时 D 不验收，保存反例交 Host 重新定机制。

4. conhost 与版本发现：桌面使用 NO_WINDOW/new-group，必要时以 new-console 作可见窗口参照，运行协议停止/强停替身并记录根/native 两层完整成员；按相同最终启动方式复跑 Codex CLI 0.163.0 的产品发现与直接 Node 加 JS 版本命令，保留失败时成员、创建时间和主进程退出时刻。实际 CLI 版本变化须重新注明，不能沿用旧结论。不做执行/native 的 detached 对照；验证协议先行、native 非空再终止、全空才回执、执行退出后根停止证据成立。若产品失败时残留 conhost 才可归因于同一机制；否则追实际失败原因，不过滤计数。DSH shim 实现及其版本发现由另一任务交证据。经 SSH 的部分已完成（[a3 记录](windows-experiments-a3.md)）：NO_WINDOW 下根与 native 两层都计入 conhost；协议停止后 native 有一次还剩 pwsh，终止 native Job 后全空。Codex CLI 0.163.0 与 DSH CLI 的产品发现已在 shim 的独立复跑中得到 ready。桌面上的可见窗口部分仍待做。桌面部分已完成（[桌面实验记录](windows-experiments-desktop.md)）：NO_WINDOW 下根与 native 的成员采样时都没有窗口，协议停止与直接强停都收敛到全空。

5. Defender 与文件换代：开发者打开实时扫描，首尾保存 Get-MpComputerStatus 均为 RealTimeProtectionEnabled=True，保持普通令牌和锁定环境。在独立根先对 SetFileInformationByHandle 的 FileRenameInfoEx 加 POSIX_SEMANTICS/REPLACE_IF_EXISTS 做第 3 节全矩阵、强杀窗口及实际恢复器；失败才对 ReplaceFileW 做同矩阵，另核对其首次发布分支和元数据合并，最后只选一个机制。选定机制再至少 1000 次无受控读者、无重试连续发布，分开统计人为占用和热路径异常、errno/winerror及迭代号。异常时保留实际文件和摘要，释放已知读者后记录继续结果；无偶发不加重试，有证据才定唯一边界的一次有界重试，未满足完整性/ACL 就不采用。开发者只需安排设置与桌面，探针由后续实施任务执行并保存原始输出。已完成，Defender 除外（[桌面实验记录](windows-experiments-desktop.md)）：选定 FileRenameInfoEx，结论见第 3 节；产品恢复器的接入与 Defender 开启条件留给阶段 C 的后续。

6. 私有边界与会话补项：DACL 缺失、对象/条件/未知 ACE 用合成 ACE 列表及查询结果写单元测试，不在测试机构造；目录联接与叶子重解析竞态已由补充实验完成，结论用于工作树的固定祖先（第 2 节）；新增 ACL bootstrap 的首次、active 指针及源替换入口在实现后原生补测，记录接受/拒绝与句柄关闭。SSH 会话的 sessionId 为 0，桌面会话与它不同，可以实测跨会话。若当前桌面与 SSH 的 ProcessIdToSessionId 确实不同，用一边持有的 Local Job 在另一边尝试打开，再回原会话核对原成员仍活；两边相同就不称跨会话实测，不要求注销或新建会话。不同会话未取得时保留原生证据缺口，代码仍拒绝把另一会话的错误 2 当停止。此项不依赖重启、快速启动、睡眠或休眠实验。

## 8. 实施阶段、文件与契约升级

阶段 A：已有进程/文件实验已完成，补充清单见第 7 节；只产出实验记录，不改契约、存储形式或版本，不开待执行升级。相关补项是 B、C 后续、D、E 各自落地的前提，不重复已取得的证据。

阶段 B：已完成，合入 `755c476c`（[验收记录](../../acceptance/cross-platform/phase-b.md)），留给阶段 C 后续的五件顺带项在验收记录末尾。原定：落地已同意的 Python `>=3.12.4,<3.13`、直接 pywin32 依赖、逐级私有创建及句柄/ACL 边界。依赖 A 已有 ACL/稳定 runtime 证据及补项 1、6；改 pyproject.toml、uv.lock、private_dirs.py、新增 windows_files.py、搬移 windows_paths 文件能力及第 2 节列出的 CLI/launcher/transport/turn_io/session/fast cwd 入口，服务、执行与启动器的进程入口落实提权时的默认属主政策或明确拒绝分支。验收单一 ACL 规则、每级 0700、提权建后普通访问、SQLite sidecar 重建、祖先固定、fd 单次转移、bootstrap 及 POSIX 回归；ACE 形状反例用单元测试，实际 owner/继承及路径竞态用原生证据。不改消息/事实格式或 SQLite 存储形式，单独安装只移动末号，不开待执行升级。

阶段 B2（精简，2026-10-22 开发者定）：照第 2 节删去私有路径逐次固定祖先、逐次 ACL 校验、私有路径的名称比对、提权进程改默认属主（连同 `PRIVATE_OWNER_SETUP_FAILED`）与 Windows 上的 0o700；状态根在安装与冷启动时查一次；Python 下限回到 `>=3.12,<3.13`。句柄层留给工作树与第 3 节的发布。阶段 C 后续的顺带项 1、2（安装发布路径进了私有边界、整条路径比对）随之不再成立。排在阶段 C 后续合入之后；删守卫的差异按惯例另请一次独立审查。不改消息、事实或存储形式。

阶段 C：已完成，合入 `516c6d5d`，测试挂起修复合入 `e1e4d1d6`。共用 `private_files.py:publish_bytes` 及三系统唯一备份 journal 是现状，不再依赖 B 才算完成。其实现没有改跨进程事实或看板存储形式，不因此开升级或移动 CONTRACT_VERSION；测试修复本身不移动版本。这里只留 Windows 带读者换代后续：依赖 B 的句柄边界和补项 5，通过实验门槛后改 `windows_files.py`、`private_files.py` 的可变文件换代及失败清理，复跑 private publication、native journal、`test_backup_publication.py`、upgrade 恢复的相应中断用例。事实形式不变，单独安装移动末号，不因换 API 开升级。

阶段 D：修复 Windows 服务重启及同契约安装会杀执行的现存缺陷，完成独立服务启动、真实身份、根句柄归属及会话接回。依赖 B、C 的既有发布及 Windows 换代后续、补项 2 与补项 6 的跨会话部分（补项 3 已完成）；改 transport、install 的 launcher/runtime/upgrade:start、protocol/windows_process.py、owned_process、process_controller、process_identity、execution、buddy/runtime/execution.py、调度/interruption 和 store/db。保留已存在的 kill-on-close，补 183 拒绝、唯一长期执行句柄、临时停止句柄、真实 PID/sessionId 和同会话名字缺席停止判断；删除 Windows BootIdentifier 读取、记录及比较，不加长期观察句柄。服务在执行进程之外做发现时，`buddy/harnesses/discovery.py:probe` 不走 owned_popen，而是普通 Popen 加 CREATE_NEW_PROCESS_GROUP，在 Windows 上不进任何 Job，后代不受管（2026-10-21 Windows 复跑时观察到，当时无残留）；阶段 D 让这些探针同样在 kill-on-close、禁止脱离的 Job 里运行，结束时终止并观察全空，与执行用同一条停止证据。验收真实链显式/无标志脱离矩阵、服务重启与同契约安装保活、执行强杀、先后崩溃、最后句柄关闭后的错误 2 和全成员缺席、重启后结算继续调度、marker 前及跨会话/查询失败 unknown。扩充 Windows process、process identity、execution recovery、transport 冷启动、安装回滚与执行围栏测试。

D 改变执行/服务交接事实及 SQLite 身份形式，必须同步改 pyproject.toml 的中间号及 `protocol/contracts.py:CONTRACT_VERSION`，开启显式待执行升级；改 db 升级逻辑和 `tests/python/blackboard/store/upgrade_fixture.py`，由 test_upgrade_fixture 比较新建与升级结果。Windows 身份只含真实执行 pid、原始 startedAt FILETIME、sessionId 和 jobName，不含 bootId；POSIX 保持开机会话、PID/创建值与进程组形式。SQLite 身份表按已有 Job/进程组互斥关系区分：Windows 的 boot_id 为 NULL、session_id 必填，POSIX 相反；Windows boot 值不转换成另一种假标识。startup、intent、marker、live、native journal 及 attempt_process_identities 同步采用唯一新形式；Windows intent 只绑定 session/Job 和 task/attempt/generation，尚无 marker 时仍 unknown。根身份已登记后，根 Job 的停止证据覆盖其全部成员及 native 创建窗口，不靠补造缺失 PID 消除 pending。Popen 启动进程仅留创建者回收用的临时句柄，没有状态目录身份字段或副本用例。

阶段 E：共同接收/最终结算与 Windows 全后代停止。依赖 D 和补项 4；改 ProcessHandle、原生运行统一收尾、native journal、receipts、store 结果入口、调度/interruption 与 db 的接收表。验收原生协议先行、native 全空才回执、服务已接收响应不等根退出、取得第 4 节停止证据前不释放、不启动冲突工作、不回收检出；服务先崩、执行先崩、强停残留、ACK 前后和完成事务前后崩溃均只结算一次。覆盖 execution delivery、run execution、native tree、execution recovery、store 结果/容量/claims 测试；POSIX 仅覆盖完整身份及原组，保留脱离后代缺口。

E 的 ACK 语义、保留回执的交接规则和新增接收表均改变契约/存储形式，不能作为末号改动。若 D/E 在未安装的一批内合入，使用同一个新中间号/CONTRACT_VERSION，并在 D 开始的同一待执行升级中完成；若 D 的升级已经在真实板运行，消费升级、刷新基线与 fixture，E 再开下一次升级及中间号。删去旧 delivery 文件及无读者路径，对非封存存量在升级中显式清理；不保留旧“收到即释放”的解释或兼容分支。

阶段 F：忙文件和测试观察/关闭。依赖 B 的文件边界、D/E 的生命周期；已核实的手建 store/SQLite close 与 `/bin/sleep` 替换可先独立修。改产品中第 6 节列出的恢复、清理入口及 `cli/checks.py`、`tests/python/support.py`、原关闭遗漏测试、native journal 和 checks cleanup 用例；失败保留恢复计划，私有测试根单独处理 READONLY，Windows 用已拥有的 Job/身份观察，完整收尾不能观察时保留根并退出 3。验收解除占用后同计划恢复、SQLite 全连接关闭、runtime 先保留后删除、转发器真子进程结束、无 ps 依赖。此阶段不另改事实/存储形式，产品代码单独安装移动末号，纯测试改动不移动版本。

阶段 G：Host 整合、独立审查与安装前演练。依赖 B、C 后续、D、E、F 分别验收和对应原生实验；独立审查检查是否仍有重复机制或代价不相称，不将设计批准或 Worker 完成当作验收。最终源码在三系统按 AGENTS.md 跑完整 Python 检查，分别保存退出码和 teardown；Windows 11 桌面/Defender/真实启动链证据单列。私有安装分别演练同契约服务重启保活和变契约的空闲、备份、失败回滚；旧版本发出的安装请求只用该版本接受的字段。G 不新增契约或存储形式，文档/证据本身不移动版本。日常安装由 Host 另行安排。

升级基线仍为 0.49.0、schema 27，当前无待执行升级；本设计修订不启动升级。D 开始时 fixture generator 必须提供每种待转换/清理的身份、启动、native journal 及 delivery 存量，E 加入它处理的全部存量；升级后的 fixture 与新建板比较。首次契约变更安装要求空闲，不给旧 Windows 记录猜填当前 sessionId 或把启动器 PID 改成当前进程；对不再用于恢复的已结束非封存身份、启动及 journal 存量显式清理，不保留旧形式的运行时读取或猜测分支。sealed snapshot、回执及其摘要绑定内容原样保留，新封存明确形式。升级同步删除触及表内无读者或可推导的重复数据；接收表的固定路径不存第二份，meta 仍只放 schema 和 secret。若升级执行时尚有无法确认停止的旧执行，安装不得越过空闲条件。

需要 Host 修改 ADR-031：第 1 条补“原生工作停止后交回执，收到已接收回应退出，服务取得完整边界的停止证据后最终结算”；第 4 条保留长调用这一种失联通知，注明根 Job 唯一长期句柄由执行持有，服务仅在创建确认窗口和接回/停止操作中短暂持有；第 5 条将 Windows 身份写为真实执行 Python PID、原始 FILETIME、sessionId 与 Job，删除 Windows BootIdentifier；第 7 条明确 ACK 只表示可靠接收，并写入根 Job 独占新建、事先 kill-on-close、禁止脱离的前提下，同会话错误 2 加根完整身份缺席即为停止证据，临时打开的非空 Job 则终止并观察全空。第 10 条契约/升级和执行挺过同契约安装的规则、第 13 条开发流程决定不改。AGENTS.md 及 ADR-010 的“身份缺席即证明结束”原则保持不变；architecture、operations/workers 随实现写清 Windows 新身份、接收结算、busy 和恢复规则。本文不越出范围编辑这些文件。ADR-031 的这几条已于 2026-10-22 照此修改。

## 9. 本轮修订

按 Host 本轮审查，停止判断改为同会话中原名错误 2 加根完整身份缺席，明确仅适用于已登记身份、独占新建且事先设好 kill-on-close/禁止脱离的根 Job；删除 Windows BootIdentifier 的读取、记录、比较及电源实验。服务不再长期持根观察句柄，保留创建确认窗口和停止时的临时句柄；照实记录当前服务持有唯一根句柄导致服务重启、同契约安装杀执行的缺陷。

补入 venv 链中禁止脱离请求却成功的事实，新增真实解释器及孙进程显式/无标志脱离实验，失败就改基础解释器加 venv site 配置的启动链。执行/native 保留 NO_WINDOW/new-group，只有服务用 detached/breakaway；先核实无外层 Job 时 breakaway 被忽略，再省掉启动分支。提权进程启动时把令牌默认属主设为当前用户（Host 合入时改定，代替逐个对象改属主，覆盖 SQLite 自建的文件与提权运行时的重建），设置被拒就在提权启动边界明确拒绝，不改既有 ACL 或白名单；逐级私有目录每级显式建 0700。

Windows 换代先试 FileRenameInfoEx 的 POSIX 语义改名，失败才测 ReplaceFileW，最终只留一个机制；补项 5 和阶段 C 后续随之调整。ACE 形状反例改为单元测试，junction/bootstrap 保留原生补项。修正回滚入口为 upgrade:start，将桌面实验置第 7 节、阶段置第 8 节，顺排节号；ADR 条款与 D 的身份/升级判断同步改写，阶段 E 接收结算及接收表、F 测试收尾和其余阶段版本判断保留。

2026-10-22 按补充实验（[a3 记录](windows-experiments-a3.md)）：补项 3 完成，保留 venv 入口，不改启动链；固定祖先改以 `GENERIC_READ` 打开，因为只请求 `FILE_READ_ATTRIBUTES` 的句柄挡不住改名；补项 6 的目录竞态完成。

2026-10-22 按桌面实验（[桌面实验记录](windows-experiments-desktop.md)）：补项 1 完成，阶段 B 采用提权进程设默认属主的政策；补项 5 选定 FileRenameInfoEx；服务起的每个控制台子进程都带 CREATE_NO_WINDOW；跨会话的错误 2 不是停止证据，已实测；启动窗口关闭后服务的存活留到阶段 D 验收。

2026-10-22 晚，开发者认为私有边界层层设防、与要防的情形不相称：私有规则防得住的只有同机的其他普通账户，而 profile 目录的 ACL 已经挡住他们；提权默认属主那一层原是 Windows 上 0o700 造成的问题。第 2 节重写为继承 profile 的 ACL、状态根在边界上查一次，阶段 B2 照此删去多余的层次，Python 下限回到 3.12。工作树的固定祖先与第 3 节的发布不变。
