# Windows 第二批：执行所有权、停止证据与私有存储

2026-10-21，设计候选，核对基线 `ab21a83a8e4c868ab3014ef7439ec2f11505d3e5`。本文只提出设计与实现验收顺序，没有修改产品代码、运行检查套件、连接日常看板、调用真实 harness 或验证 Windows 11。依据为[分批计划](plan.md)、[清单](inventory.md)的 F01、F02、F04、F08、P01、P02、P04、T03、AGENTS.md、[架构](../../reference/architecture.md)、[ADR-007](../../decisions/007-neutral-core-and-single-current-contract.md)、[ADR-010](../../decisions/010-production-workflow-repair-plan.md)、[ADR-031](../../decisions/031-one-process-per-execution.md)。涉及 harness 的建议同时受 ADR-023、ADR-030 约束。本文的“已核实”指本次读到源码或第十轮日志，不能当作目标系统的运行验收。

建议采用 Windows 专用的 pywin32 依赖处理安全描述符，抽出一处句柄级文件边界；把事实发布与备份的共同承诺定为进程崩溃恢复；执行根 Job 去掉随服务最后句柄关闭而终止的属性，原生运行的短命 Job 继续由执行进程拥有；所有结束判断与回收统一保留 unknown。Windows 11 的 BootIdentifier、无句柄时 Job 的存续和嵌套 Job 必须先经实验，才能接受这套执行设计。POSIX 脱离进程组的后代是本批的实质缺口，不能通过补 Windows 分支就宣称待办已解决。

支持范围沿用计划：Windows 11 x64、本地 NTFS、同一个登录会话、Python 3.12；Windows 服务、网络盘、FAT/exFAT 和 Windows ARM 不在首发范围。Linux、macOS 继续使用各自的真实开机会话和内核进程创建值。三系统共用任务身份、单一事实发布协议、结果导入及停止结算规则；ACL、Job、重解析点和文件共享模式在 Windows 的系统边界实现。

## 1. 第十轮日志能证明什么

run `39555977214` 的四份 `overview.md` 记载：w1 为 74 文件、26 通过、48 失败；w2 为 74、22、52；w3 为 74、27、47；w4 为 74、23、51；四片均退出 1，共 296 文件、98 通过、198 失败，没有未完成文件。这是该轮的检查结果，本文没有重跑，也没有把 GitHub 的 `windows-latest` 当作 Windows 11 真机证据。

本次按日志行的字面匹配计数，`PermissionError: [Errno 13]` 共 729 行，`WinError 5` 共 270 行，`WinError 32` 共 133 行，完整的 no-tool cwd 错误共 168 行，`Private test process shutdown unconfirmed` 共 108 行，`Bad file descriptor` 共 36 行，`has no attribute 'killpg'` 共 25 行，`511 != 488` 共 20 行。日志有重复异常与堆栈，计数不是独立用例数，不能与任务输入的粗数直接比较。w2 日志含非 UTF-8 字节，检索时以替代字符解码，不能由此声称产品存在新的编码缺陷。

代表性调用链已对照源码：w1 的目录拒绝访问落在 `protocol/execution.py:fsync_json` 的 `os.open(path.parent, os.O_RDONLY)`；w3、w4 的备份错误落在 `blackboard/store/backup.py:create` 以只读句柄重新 `fsync` 的循环；w1 的 Git 对象拒绝访问发生在测试 `shutil.rmtree`；w2 的 `health/board.sqlite3` 删除失败发生在测试收尾；`killpg` 的代表性堆栈在 `tests/python/buddy/harnesses/test_controller.py:controller_log_close_failure` 和 DSH ACP 的 POSIX 观察测试，不能把 25 次都说成产品在 Windows 调用了 `killpg`。

源码依据以下均相对 `src/hey_my_buddy/`；测试路径相对仓库根。引用函数名用于定位，避免把清单旧基线的行号当作当前行号。

## 2. 私有目录与文件：一次句柄级属主及 ACL 校验

已核实：`private_dirs.py:ensure_private_dir` 在 Windows 上不设置或验证 DACL；`protocol/transport.py:_private_directory` 在 Windows 上只判断真实目录；`protocol/execution.py:_private_json/read_execution_receipt` 仅在 POSIX 检查 uid 和 mode。相反，`console/console_sessions.py` 的 session 读取与 `buddy/roles/run_execution.py:start_fast` 仍用 `st_mode & 0o077` 拒绝 Windows 路径。`private_dirs.py:open_regular_fd` 的 Windows 分支是检查路径、普通 `os.open`、再检查路径；它没有 POSIX 逐级固定祖先的等价保证。`buddy/roles/turn_io.py:_private_bytes` 也只有 POSIX 分支固定父目录 fd。不能将现有 Windows 分支描述成已经保证私有性。

建议显式增加 `pywin32; sys_platform == 'win32'` 为直接依赖并锁定版本。当前 `uv.lock` 已因 `portalocker 3.2.0` 的 Windows 条件依赖锁入 `pywin32 312`，有 CPython 3.12 x64 wheel，`pyproject.toml` 尚未直接声明它；这次选择不要求另找一个未锁定的软件包。直接依赖表达本项目对安全接口的使用，不能依赖 portalocker 永远替本项目带入它。没有在本机导入 Windows 模块，本文所列 Win32/pywin32 接口是实现目标，具体 Python 签名与返回值须在 Windows 11 核实。

两种做法都可以提供 Win32 语义，但成本不同。pywin32 的 `win32security`、`win32file`、`win32api` 可承载令牌 SID、安全描述符、ACL 和句柄的对象生命周期，避免自己声明 SID、变长 ACE、令牌和安全描述符内存释放。沿用 ctypes 的好处是风格接近 `protocol/windows_process.py:WindowsAPI` 与 `blackboard/tasks/windows_paths.py:WindowsFileAPI`，已有可注入边界；代价是本项目还要维护安全结构、访问掩码、错误映射及句柄释放。已有代码并没有 ACL 实现可直接复用。建议安全与文件句柄边界使用 pywin32，不同时保留 ctypes ACL 回退；Job 的现有 ctypes 边界本批按实际所需修改，不为使用同一库而整体重写精确进程启动值或 Job 实现。

私有规则建议冻结为：对象 owner SID 必须等于当前进程令牌的用户 SID；DACL 必须存在，不能是 NULL DACL，状态根的 DACL 还须受保护以阻断宽父目录继承；可授予访问的主体只允许该用户、LocalSystem 与本机 Administrators，当前用户具备所需读写及管理权。私有根内的子对象可继承已校验私有祖先的 ACE，但不能接受规则外主体，不能把“有继承 ACE”本身当作不安全；SQLite 等原生写者创建的子文件也按同一规则读回。SYSTEM 和 Administrators 是明确的本机特权主体，不把私有性描述成能阻止管理员或同一用户任意 shell 篡改。校验只接受普通 allow ACE 的已知形式，按文件/目录的 generic mapping 归一化访问掩码，要求用户具有该对象的完全控制；deny ACE、未知 ACE、对象 ACE、条件 ACE、规则外主体及不符合继承范围的 ACE 均明确拒绝。这样读回并核对的是内核持有的 owner/DACL，不用“当前用户能打开文件”代替其他用户没有权限的证明，也不实现一套通用 Windows 有效权限推导器。新建目录使用用户完全控制并向文件、子目录继承的安全描述符；新建凭据与事实文件在创建时就带私有描述符，不能先写秘密再收紧权限。已存在的用户自选状态根只校验，宽 DACL 或其他属主给出原因，不自动改其 ACL。需要收紧的操作另有明确授权才做。

在拟新增的公共 Windows 文件边界中，先用不跟随重解析点的目录句柄逐级打开并固定祖先，使用 `FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS` 检查类型、重解析属性与最终名称；父句柄不允许删除共享，从而阻止检查后替换祖先。叶子通过同一个句柄核对普通文件、owner SID 和 DACL，然后将句柄交给读写者，不再由调用点按字符串重开。ACL 使用句柄上的 `GetSecurityInfo`，不用路径上的安全描述符查询冒充同一对象。新建用独占创建和显式安全属性；拒绝叶子和祖先的重解析点，拒绝设备、目录冒充文件及别名路径。转换为 CRT fd 时只转移一次所有权，使用二进制和不可继承标志，避免双重关闭。

文件路径固定与私有权限是同一边界的两个政策参数。现有 `blackboard/tasks/windows_paths.py` 的工作树读写需要复用句柄固定机制，但不能因此要求普通用户源检出也符合私有 DACL；Git 符号链接仍由工作树政策显式处理。建议将低层句柄能力从 tasks 抽至根包拟新增 `windows_files.py`，私有政策留在 `private_dirs.py` 或其专用实现；让备份、凭据和回执不再反向依赖黑板的工作树模块。POSIX 保留逐级 `dir_fd/O_NOFOLLOW` 和 uid/mode 规则，调用方只见一次统一边界校验。

受影响入口包括 `protocol/transport.py` 的状态根、token 与 endpoint 文件，`private_dirs.py` 的目录及普通文件入口，`protocol/execution.py` 的 startup/live/receipt，`buddy/roles/turn_io.py` 的私有输入与输出，`console/console_sessions.py` 的 session，`buddy/roles/run_execution.py:start_fast` 的空 cwd，以及备份和安装产生的私有文件。空 cwd 同时要求目录私有、真实且为空；不能仅删除 mode 检查。祖先允许的系统权限与状态根的私有规则分开：不要求磁盘根和用户目录一律为私有，但固定的状态根及其私有对象必须满足上述规则。

未验证的 Windows 11 实验：以普通用户在宽继承父目录下创建私有根，核对创建瞬间及读回的 owner/DACL；分别加入 Everyone、Users、Authenticated Users 的读或写 ACE、改属主、NULL DACL、规则外继承 ACE，确认拒绝；合法 SYSTEM/Administrators ACE、私有祖先的合法继承与 SQLite 创建的文件确认接受。并发替换每级目录为 junction、替换叶子为 reparse point，验证不能读到根外标记文件；同时验证持有祖先句柄不会令自身的正常发布、关闭和删除死锁。ACL API 调用失败必须是明确的访问错误，不能回到 mode 推断或默认通过。

拟扩充 `tests/python/test_private_directories.py`、`tests/python/blackboard/tasks/test_windows_workspace.py`、fast Router 与 console session 的现有测试，新增 Windows 原生安全边界测试；测试可注入边界覆盖异常清理，但 ACL 与重解析点竞态的结论必须来自 Windows 原生调用。POSIX 的 uid、mode、符号链接拒绝回归保留。

## 3. 落盘：进程崩溃保证与断电边界

三系统共同承诺应是：一次发布返回成功后，即使写入进程被强杀，后继进程仍读到完整的已发布事实；发布过程被杀则只可出现完整旧版、完整新版或明确的未完成状态，不能把半份内容当作成功。备份还要求任一发布阶段中断后能恢复到可验证的完整一代。文件写入成功不等于任务结算成功，SQLite 的状态转换、事件和幂等记录继续在一个事务里提交。当前 `blackboard/store/db.py:Database._configure` 使用 WAL、`synchronous=FULL`；本批不改成弱同步，也不在 SQLite 外再造另一份权威状态。

必须走该承诺的文件是：执行启动前的 `spawn.intent`、startup；放行原生工作前的执行身份和 native journal；原生 spawn 的 pending 与完整身份；用于恢复的 live 描述符及 Router budget；结果回执、交接引用指向的事实、不可变工作区 manifest/patch/物理封存证据；备份 payload、manifest、发布 journal；安装的切换 journal、恢复快照及成功指针。依据为 `protocol/execution.py:launch_execution/fsync_json`、`protocol/process_identity.py:NativeProcessJournal`、`buddy/runtime/execution.py:Execution.run/receipt`、`buddy/runtime/receipts.py`、`buddy/roles/turn_io.py:_private_bytes`、`blackboard/tasks/workspace.py:_write_once`、`blackboard/tasks/windows_paths.py:write_once`、`blackboard/tasks/storage.py`、`blackboard/store/backup.py` 和 `install/upgrade.py:write_journal/restore`。诊断 stdout/stderr、控制台显示用 session 记录、内存活动提示不升格为停止或恢复事实；可以失去末尾，不能拿它们补齐缺失身份。

建议新增公共的私有字节发布实现：同目录独占创建临时文件，写入完整 UTF-8/二进制内容，在仍持有写句柄时 `flush` 和 `os.fsync`，关闭后按原子替换发布；不可变对象使用 compare-and-create，已有同内容可复用、不同内容报冲突。POSIX 发布后刷父目录；Windows 进程崩溃承诺不要求打开目录再 `fsync`，成功替换后的目录项由仍在运行的内核保有，不声称具备 NTFS 目录更新的断电耐久性。所有读写使用第 2 节的句柄边界；不再保留 execution、turn_io、daemon 各一份相似写入器。原子替换在目标支持范围的真实行为仍要经过中断实验，不能仅因 `os.replace` 存在就宣称已验证。

已核实的差异是 `protocol/execution.py:fsync_json` 无条件刷目录，`NativeProcessJournal._publish` 和 turn_io 已跳过 Windows 目录刷盘，`blackboard/store/backup.py:sync_dir` 也明确只对 POSIX 刷目录。修法是统一它们的故障模型和实现，修改函数的“文件及目录已经 durable”注释；不用 Win32 目录句柄刷盘再叠一套未经证明的机制。native journal 的 pending 必须在启动前发布，完整身份发布后才解除 pending；中途崩溃留 pending 就留 unknown，不用“暂未找到进程”消除它。

F08 的根因已经由 w3、w4 的 `backup.py:create` 堆栈确认。`_private_copy` 已在写句柄上刷盘；压缩数据库的 GzipFile 退出后还需在底层 `compressed` 写句柄上 flush/fsync，确保 gzip 尾部也落下；manifest 和 publication journal 同样在各自写者手中刷盘。删除最后遍历全部文件、以 `rb` 重开后刷盘的循环。验证内容仍读只读句柄，不再把验证和补刷盘混合。

备份建议三个系统共用现有 journal 发布协议：先完成并验证 `.incoming`，发布 journal，必要时把 current 改名为 `.previous`，把 incoming 改名为 current，验证 current 后删除 previous，最后删除 journal。已有实现位于 `backup.py:_publication_journal/_recover_publication/create`；POSIX 目前另走 `exchange`，应在替换时删除这套分支及专属测试，不保留自动回退。恢复必须在备份锁下进行；current 在两次改名之间可以暂时缺席，所有正式读者必须先恢复或报告进行中，不能承诺路径一直存在。首次备份中断还没有完整一代时，返回未完成并允许重建，不声称一定有可恢复备份；不得丢掉唯一已验证的一代。

决定 4 的推论只成立一半。已核实 `protocol/process_identity.py:boot_id/inspect_process_identity`：Linux 读 boot UUID，macOS 读 `kern.bootsessionuuid`，Windows 调 `NtQuerySystemInformation(0x5A)`，取前 16 字节作 BootIdentifier；相异时立即返回 `boot-changed`。`blackboard/tasks/interruption.py:_stop_native` 已把这个结果作为所有旧执行与原生进程停止的证据。真正换了一次内核开机会话，旧内核的进程不可能继续存在，因此这个推理可以证明进程停止；它不能证明断电前的回执、备份或未提交的 SQLite 数据还存在，也不能重建已丢的产物。恢复应记中断、按保留下来的证据处理部分输出，不能填造成功回执。

Windows 分支目前只有代码，没有本次可读的系统 API 契约材料或 Windows 11 运行证据；源码注释写的“documented-size prefix”不能证明 `0x5A` 及 32 字节缓冲区在目标系统可用。因此不能现在确认 Windows 的上述前提。重启、关机后开机（含 Fast Startup）、休眠恢复、睡眠恢复、同次开机注销再登录分别记录标识与存活测试进程；要求实际终结旧进程的内核重启改变标识，保留用户进程的恢复不产生错误的改变。Fast Startup 是否换标识必须按观察记录，不从“点了关机”推导；旧执行已经结束但标识没变时，应依完整身份缺席证明。API 失败或结果不可靠时保持 unknown，禁止用墙钟启动时间或生成的 UUID 代替。

应在启动意图中先记录已观测的 bootId 和预分配 Job 名（Windows），再允许创建执行，避免只剩没有开机会话的 intent。当前 `spawn.intent` 只有 taskId/attemptId/generation，完整身份稍后才写入 `spawn.marker`；仅有这份 intent 不能在重启后凭空知道是哪次开机启动的。没有完整身份的既有意图不能用文件 mtime 推定启动会话。启动到身份发布间的中断必须有测试：未放行原生工作不等于执行根已经消失，未证明的根仍占名额。若修改跨进程事实格式，按第 8 节处理契约与升级。

未验证实验与测试：Windows 11 上对每个文件发布阶段、gzip 尾部结束前后、journal 两次改名和清理阶段设置模型无关的暂停点，由父测试进程强杀写者，重新启动读者/恢复器核对摘要、绑定和完整代际；POSIX 做同一组进程中断测试。为 bootId 单独保留重启前后探针记录。断电实验只能在可丢弃虚拟机或专用环境做，目标是确认重启后的停止结算与错误报告，不把它作为“不丢数据”的验收。拟扩充 `tests/python/protocol/test_native_process_journal.py`、`tests/python/buddy/runtime/test_execution_process.py`、`tests/python/install/test_backup.py`、`test_backup_windows.py`、`test_upgrade.py` 和 `tests/python/blackboard/store/test_interruption_recovery.py`。

## 4. 执行 Job、接回与停止证据

已核实的调用路径是 `protocol/execution.py:launch_execution` 预分配 Job 名，`protocol/process_controller.py:launch_controller` 调 `owned_popen(close_fds=True)`，`protocol/owned_process.py:owned_popen` 先创建 Job、以 suspended 启动子进程、加入 Job 再恢复线程，`windows_process.py:WindowsAPI.create_job` 对每个 Job 都设 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`。Job 句柄放在父进程的 `Popen._buddy_job`，执行在 `buddy/runtime/execution.py:main` 仅从 startup 取得名称后生成自己的身份，没有接持根 Job 句柄。`blackboard/service/daemon.py:on_restart/cleanup` 停调度器并清理服务；这条实现不能满足 ADR-031 的服务崩溃与补丁重启保留执行。

建议明确两种现有创建用途的生命周期，不给调用者任意组合开关：执行根 Job 不设 kill-on-close，也不允许 breakaway；原生 harness/探测/command 的短命 Job 继续设 kill-on-close，由创建它的执行或探测进程持有。前者属于一次执行的可接回事实，后者属于一个仍持有原生句柄的运行。服务退出关闭执行根 Job 句柄不结束执行，执行崩溃关闭其原生 Job 句柄会触发原生工作清理，最终仍由根 Job 证明边界已空。这两个用途共用 Job 机制和身份校验，没有另一套 PID 递归停止器。不得取消全部 Job 的清理属性，也不通过给执行继承服务句柄来要求服务与执行做脆弱的移交握手。

Windows 11 必须验证无 kill-on-close 的命名 Job 在服务所有句柄关闭后，只要关联进程还在就仍可按名打开；也必须验证执行根属于根 Job、其后创建的 native Job 可以正常嵌套，孙进程都仍属于根 Job，外层 Job 禁止脱离在 Python、uv、shell、Node 和带 breakaway 标志的创建中成立。当前 ctypes 代码已经有基础操作，但这些组合在本任务内没有运行过。若实验否定该生命周期，先退回设计选择，不加轮询续租、看门狗进程或 taskkill 来掩盖缺口。

创建边界必须拒绝 `CreateJobObjectW` 打开了已有同名对象的情况，并为新 Job 使用明确的用户私有安全描述符；`Local\\HeyMyBuddy-<uuid>` 名称继续使用随机 UUID，bootId、task/attempt/generation 和由状态根推导的启动证据绑定在一起。名称只是定位，不单独授予结束权限。创建执行根时保持 suspended，能取得的完整身份及 Job 归属应在恢复线程前落下；原生工作仍须等现有 `Execution.run` 的身份确认之后才开始。原生 pending 到已登记之间也必须覆盖“创建后尚未持久化就崩溃”的窗口，不能让缺失 journal 被当作无子进程。

接回由 `blackboard/service/execution_scheduler.py:_resume_executions/verify_identity` 使用当前看板所拥有的 attempt 推导固定私有目录、读取对应身份，再调用系统边界 `OpenJobObjectW`，保留这个句柄完成查询、必要的停止及停止观察。一次观察与发出终止之间不能先关 Job 再按名字打开另一对象；当前 `process_identity.py:_SystemSource.job_members/stop_job` 分别开关句柄，应收敛成一次持有的观察/操作。根仍在时需核对 bootId、原始创建 FILETIME、PID 与实际 Job 成员关系；根已退出而 Job 还有成员时，凭私有启动边界预先绑定的同一 Job 结束遗留工作，不用另一个同 PID 的新进程作为凭据。Job 绑定或成员来源不能核实时是 unknown。

Windows 的软停由现有 ExecutionLive stop 进入执行的控制与 harness 原生 interrupt/close 路径，宽限到时以 `TerminateJobObject` 强停。`process_handle.py:signal_group` 目前把 SIGTERM 映射到 CTRL_BREAK_EVENT，异常会被吞掉；控制台事件有控制台关联前提，GUI/无控制台不能把“已请求发送”记作有序收尾成功。建议把控制台事件从共同软停承诺中移出：有序收尾用既有原生通道，系统层负责强停与结果；command 测试执行没有原生协议，不承诺获得 POSIX SIGTERM 等价的处理机会。停止原因仍区分 completed、grace-expired、forced-stop、service-stop 等，不因 Windows 缺少 POSIX 信号号而伪造负退出码。

停止证据必须区分：持有 Job 观察 ActiveProcesses 为 0 且执行根句柄已退出，可以确认该边界为空；按名打开明确报对象不存在，结合根完整身份缺席和可信启动绑定可确认结束；bootId 确实改变可以确认旧开机中的执行结束。拒绝访问、查询失败、返回不完整列表、仍有成员、根身份冲突都不能当作空 Job；`TerminateJobObject` 返回成功本身不是停止证据。当前 `process_identity.py:inspect_process_identity`、`ProcessHandle.shutdown_confirmed`、`blackboard/tasks/interruption.py:_stop_native` 的 unknown 原则保留，具体 Job 操作改为持有同一句柄。根 Job 仍有原生工作时不能因 creator-reaped、长调用断开或回执报告成功就释放 attempt 的资源；正常回执交付时允许的唯一存活执行根及其约束见第 5 节。

POSIX 共同接口仍是发送 SIGTERM、必要时 SIGKILL、观察原组已空；`killpg` 返回成功不是停止证明。已有 `process_identity.py` 会把根已退出但组仍在、根 PID 重用且组仍在记 unknown，这个规则不能为了和 Windows 对齐而放松。Windows Job 的组员继承与 POSIX 进程组的可脱离性不同，第 5 节单独处理，不声称两者有同等强度。

拟修改 `protocol/windows_process.py`、`owned_process.py`、`process_controller.py`、`process_handle.py`、`process_identity.py`、`execution.py`、`buddy/runtime/execution.py`、`buddy/runtime/receipts.py`、`blackboard/store/store.py` 的结果导入、`blackboard/service/execution_scheduler.py`、`blackboard/tasks/interruption.py`。拟扩充 `tests/python/buddy/runtime/test_windows_process.py`、`tests/python/protocol/test_process_identity.py`、`tests/python/blackboard/service/test_execution_recovery.py`、`tests/python/blackboard/store/test_interruption_native_tree.py` 与安装执行围栏测试。原生测试覆盖服务强杀后执行继续、同契约私有重启接回、根先退出而孙进程继续、无控制台停止、Job 权限错误、创建碰撞、成员枚举扩大与嵌套 Job。

## 5. 回合结束后仍活着的进程：三系统的解决范围

已核实待办 `docs/design/backlog.md` 记录的是 ZCode CLI 某回合中经 `uv run` 起的合成预览服务，在停止确认及受管检出回收后仍活了数小时；记录明说没有核实它怎样逃过停止。因此不能写成已经复现 uv 或 ZCode 会脱离进程组，也不能给没有观测过的 CLI 版本补上版本号。当前 `buddy/roles/run_execution.py:InlineRunHandle.shutdown_confirmed` 只判断 native stop evidence 的 `group_state == 'gone'`；native journal 只登记通过本项目 `owned_popen/ProcessHandle` 创建的直接原生运行，不登记 harness 工具内部每个后代。这个覆盖范围不足以证明任意后代都停止。

共同修正是把“原生运行结束”“本次执行可归属的工作边界已空”“可以封存/回收”分清，并在权威结果导入处统一要求后两者的证据。正常完成、取消、超时、执行崩溃都走同一终结规则：先原生有序收尾，再结束遗留工作，观察边界为空，最后发布 shutdownConfirmed、封存和释放资源。执行进程仍要活着交回执，所以 Windows 不能对包含自己的外层 Job 要求 ActiveProcesses 等于 0 后才写回执；正常回执前验证只剩经完整身份核实的执行根且全部 native Job 已空，执行根退出后服务再确认整个根 Job 已空，完成执行端点及启动材料清理；正常结果导入与资源释放所需的是前一步原生工作已停的证据，不把交付回执的已知根进程伪称为已经退出。执行根以外仍有成员或观察失败时返回未确认，不能导入为已停止。强停及崩溃结算则直接要求根 Job 空。

Windows 的具体方案是第 4 节不可脱离的根 Job：工具启动的新控制台或新进程组不应逃离 Job，子 Job 不应取消外层归属。回合结束先关闭原生协议，对其 native Job 强停并观察；仍残留在根 Job 的其他工作要查明归属并终止。不能通过终止整个根 Job 来一边杀执行进程一边要求它写成功回执；必要时由服务按中断路径结算。若目标 harness 实际让系统上已有的外部进程代起工作，Job 不会自然覆盖，必须通过它已有的原生生命周期接口取得相同强度的停止事实，否则不宣布该能力完成。

Linux 的普通同组后代用现有进程组统一结束；主动 `setsid/setpgid` 的后代不能由 `killpg` 证明。候选的强制边界是每执行一个已委派可写的 cgroup v2，执行在允许 native 工作前就进入，后代不能迁出，终止工作后要求组内只剩执行根，根退出后要求 populated 为 0；它需要权限、委派、执行根的排除/最终停止次序、服务重启接回及 cgroup 身份的实际证明。当前仓库没有这条实现，普通用户环境是否提供该能力未验证，不能为了本批而假定有 root 或用户 systemd，也不与 killpg 拼成一条“先试一下”的回退路径。若选它，是统一执行所有权的契约改动，应单独确认支持条件与停用旧的完成判定。

macOS 的普通同组后代同样可用现有进程组结束；本次没有核实到一个无需新权限、覆盖任意双重 fork/setsid、可由重启后的服务接回的 Job 等价机制。进程树枚举、进程标记环境变量、cwd 扫描和重复观察都可能漏掉先重挂父进程或换 cwd 的后代，不能作为“全后代已停”的证明；也不能靠进程名或端口号杀掉陌生进程。需要先用与已接入 CLI 原生执行载体一致的模型无关替身重现，再核对 harness 自身的后台工作生命周期和可用系统约束。不得为解决这个问题替换 harness 的工具、写配置 home 或偷偷开关用户原生能力，ADR-023、ADR-030 仍生效。

因此本批能够具体设计 Windows 的完整边界，以及三系统共同的防止错误结算/回收规则；Linux、macOS 的任意脱离后代终结方案仍有前置实验，不能在本文里宣称三系统待办已可直接实现完。建议把这项作为实施前的独立验收关口：实验要给出可执行的停止机制或明确的能力边界，并提交开发者确认需要的支持条件；拿不到等价证据时保持 unknown 与工作区占用，不把“未观察到残留”改称完整停止证明。这个关口没有通过，整批不能声称已消除待办，不能安装后再补。

回收再补一个用途明确的边界检查：`blackboard/tasks/workflow.py` 的 cleanup plan/apply 与 `blackboard/tasks/workspace.py:remove_checkout` 在最后删除前核对已登记执行的停止事实，并检查是否有可观察的进程 cwd 落入目标检出。Windows 用目标平台可用的进程观察接口，Linux 读 `/proc`，macOS 用系统进程接口或既有受维护库；该扫描只能阻止已发现的忙目录，不能授权发信号，也不能补全树停止证据。查到不属于本次登记执行的进程就报告占用；权限导致无法完成本应观察的范围就报告未知，不能强制回收。内容证明与最终删除的并发写入问题仍按已有回收围栏处理，不把本节扫描包装成全机文件访问隔离。

这道终结边界必须在 `buddy/runtime/execution.py` 调用 `partial_outputs.enrich` 形成封存之前完成，并在 `blackboard/store/store.py:execution_result_reference/execution_result` 导入时核对相同证据。当前 `buddy/runtime/receipts.py:deliver_receipt` 等服务确认结果后才退出，不能让服务等执行根消失后才回应这次 RPC，否则双方死锁。保留现有交付顺序：执行在发回执前关闭原生 spawn 入口，服务核实只剩该执行根且它已进入只交付阶段，才按正常结果规则结算；不新增“先伪报停止再补证据”的第二套回执。如果无法验证只交付阶段或仍有未知工作，现有未确认结果与恢复路径继续保留占用。该阶段的事实格式、执行侧入口封闭及服务验证必须同时设计和测试，不能只相信回执里一个布尔值。

三系统实验应使用模型无关的 Python 替身分别创建同组孙进程、新进程组、setsid、双重 fork（POSIX）、新控制台与 breakaway 请求（Windows），让它继续写目标检出中的标记。覆盖主进程先退、收到 TERM 不退、服务先崩、执行先崩、正常回合结束和回收重试；验收必须同时看到被归属工作实际结束、停止事实不提前成立、检出未在进程仍工作时删除。Windows 再用真正 uv 启动同一替身核对祖孙链；真实 ZCode CLI 的版本化复现另需 Host 后续授权，本文没有运行真实 harness。

拟扩充 `tests/python/protocol/test_native_process_journal.py`、`tests/python/blackboard/store/test_interruption_native_tree.py`、`tests/python/buddy/roles/test_run_execution.py`、`tests/python/blackboard/service/test_execution_recovery.py` 与 workspace reclaim/cleanup 用例；Linux/macOS 具体新增模块和测试由前置实验选定的唯一机制决定，不能现在凭空安排一套跨系统 PID 跟踪器。

## 6. 占用文件、只读文件与测试收尾

产品中的第一类是事实文件更新：startup/live/budget/receipt、native journal、控制 token、endpoint 与 session 文件。发布者要在改名之前结束自己的写者；允许原子换代的只读事实读者在 Windows 明确使用删除共享，读取和校验仍基于持有的那份对象，不要求所有读者都看到最新一代。固定祖先的句柄继续不允许删除共享，但在删除或改名该目录自身前必须退出固定祖先的操作作用域。目标被外部不允许删除共享的句柄打开时，报告忙并保留已发布旧事实与未完成状态，不无限重试、不删除目标再声称原子替换。

第二类是 SQLite、备份和升级恢复：`Database.connect` 缓存每线程/嵌套层连接，退出读事务不会关连接；`Database.close` 才关闭缓存。`daemon.py:cleanup` 已在调度、维护、harness 和 console 清理之后调用 db.close，不能根据测试日志断言产品完全没有关闭数据库。备份在安装协调器已脱离写者并完成 WAL checkpoint 的条件下复制，恢复在替换 board 及 sidecar 前必须让唯一所有者的全部连接关闭并释放操作句柄；与 Windows 无关的存储一致性围栏保留。外部 SQLite 查看器等占用时保留 journal、当前备份和看板，不删除 WAL/SHM 来绕过 sharing violation。恢复的 `install/upgrade.py:restore`、备份 generation 清理使用同一显式忙错误，允许用户解除占用后重试该 journal 的恢复。

第三类是受管检出、私有会话与宏任务 tomb：`workspace.py:remove_checkout` 先核验注册与锁再用 Git 删除，失败时恢复该注册的锁；`workflow.py`、`macro_task_deletion.py`、`macro_task_deletion_workspace.py`、`tasks/storage.py` 调用 `private_dirs.remove_tree` 删除自己拥有的私有树。应保持现有清理计划/墓碑记录，删除不完整就明确可重试，不把目录一部分消失当成整个工作区已回收。Git worktree 元数据仍交 Git；仓库公共 `.git/objects` 不是某次工作树的可删除范围。产品真正拥有的私有树中如有只读普通文件，删除边界可在持有同一叶子对象且已核对归属后，只清除 Windows READONLY 属性再重试该次删除；不扩大 DACL、不递归 chmod 用户源仓库、不跟随链接、不清其他属性。叶子只读处理应落在公共 Windows 文件边界，供私有删除使用；不要给任何 WinError 5 都执行 chmod。

第四类是 runtime/skill 安装：`install/runtime.py` 有 staging rename 和若干 ignore_errors 清理，`install/upgrade.py` 有 skill 切换、回滚及旧代删除。Windows 会拒绝删除仍被解释器加载的运行时，已有在做或 unknown 执行使用的 runtime 必须按 ADR-031 第 10 条保留；不能把解释器占用当可忽略的垃圾，也不能停止无关执行来让清理成功。正式安装步骤失败必须报错并保留恢复证据；切换后的可延后旧代清理要明确报保留原因，不能承诺清空。目录联接的创建和名字拒绝属于第三批，本批只统一关闭句柄、忙错误与删除结果，不顺带实现链接安装。

以上 Windows 特有的是共享删除、READONLY 与加载映像占用；三系统共同的是独占所有权、操作作用域内关闭句柄、禁止回收未知执行和保留失败证据。POSIX 允许 unlink 一个仍打开的文件，恰好会掩盖所有者没关闭资源的问题，不能把 POSIX 删除成功拿来证明 Windows 的生命周期已经正确。

测试辅助单独修。`tests/python/support.py:BoardTestCase._cleanup` 只关闭 `_stack` 中由 `self.board()` 注册的 InProcessBoard，而且吞掉 board.close 的异常；`tests/python/blackboard/service/test_harness_health.py:setUp` 自己建了 `BoardStore(self.directory / 'health')`，未在该处登记 db.close，这与 w2 的 health/board.sqlite3 占用相符。它是可直接核对的未关闭所有者，不代表所有 133 次共享冲突都是这个原因。应让所有手建 store、连接、console、RPC 客户端和线程在其测试中有明确的 cleanup，原生资源关闭失败保留报告，不能靠 gc 或最终 rmtree 帮忙关连接。`with sqlite3.connect(...)` 只管理事务，`tests/python/install/test_backup.py` 的这类连接要配 `closing` 或显式 close。不要简单扩大 BoardTestCase 递归搜索并强关未知对象。

测试的 Git 对象通常只读，w1 的拒绝访问堆栈就在测试仓库 `.git/objects`。在确认全部测试工作停止后，专门的私有测试根删除器可以清除私有根内普通只读文件的 READONLY，再重试该叶子一次；遇到 sharing violation、reparse 竞态或其他权限原因即保留根。此处不调用产品 Git 工作树删除的替代路径，也不能用 ignore_errors 抹掉清理失败。

`cli/checks.py:_ps_argv_table/_open_file_pids/_observation_snapshot` 目前依赖 ps、lsof 或 `/proc`，路径词法还按 POSIX 斜杠匹配；Windows 上不能仅调大超时解决“停止未确认”。建议沿已锁定的 psutil 为进程枚举建立平台观察边界，精确创建身份仍使用现有内核值；argv 用参数数组和本机路径关系，不再 split 字符串。Windows 的普通 open_files 枚举能否覆盖所需句柄必须原生验证，不能把 API 返回空当完整证明；优先使用测试启动时已经拥有的 Job/进程句柄与登记表证明自建子树结束，再对私有根访问做可用范围的观察。权限不足、进程枚举失败和无法确认的文件访问保留 evidence，退出 3 的含义不变；不向扫描发现的陌生 PID 发信号。

未验证的 Windows 11 实验：自己持有的正常读者、允许删除共享的读者、外部禁止删除共享的读者分别参与 replace/delete；打开 SQLite 写/只读连接时恢复与删根；gzip 未关闭时改名；只读 Git 对象清理；已加载 Python runtime 的保留；junction 换入删除树。验证忙错误含操作和受限相对路径、原始系统错误，释放占用后能继续同一恢复/清理。拟扩充 `tests/python/install/test_backup_windows.py`、`test_upgrade.py`、`test_execution_install_fence.py`、`tests/python/blackboard/tasks/test_workspace_submission_cleanup.py`、`test_windows_workspace.py`、`tests/python/cli/test_support_cleanup.py`、`test_checks_cleanup.py` 与相应未关闭资源的原用例。POSIX 专项 killpg/FIFO 测试正确标为 POSIX，并补 Job 对应的边界测试；不能只跳过 Windows 失败。

## 7. ADR-031 拟新增第 13 条：发信号的归属边界

本次读取的 ADR-031 当前只有第 1 至 12 条，没有已接受的第 13 条。以下是提交开发者确认的新增措辞，本文不修改 ADR，也不把措辞当作已定规则：“服务只停止由本状态目录的权威执行记录授权启动、并以该目录下固定私有启动证据绑定的执行及其原生工作。停止前须核对开机会话、内核进程创建身份和所属进程组或 Job；接回原服务启动的同一次执行适用相同规则。路径、命令行、进程号或进程名只能用于定位与诊断，不能单独授予发信号的权限。无法证明归属或停止时保留未知，不释放资源、不回收工作区。”

这里的“从自己状态目录启动”指授权和证据的来源，不要求可执行文件、harness 安装目录或 Worker 的工作目录位于 state 下。已核实执行用稳定 runtime 的解释器，native harness 使用用户自己的安装，工作目录通常是分配的检出；若按 exe/cwd 的路径前缀解释，就会错误拒绝正常执行。实际执行根当前使用 state/attempts 下证据目录为 cwd 只是启动安排，不作为新权限来源。两个独立状态根即使同一 OS 用户、可读到彼此 PID，也不能互相发信号。

代码落点应在服务把权威 attempt 解析为可停止执行的边界：`execution_scheduler.py` 的接回、`verify_identity`、`_force_if_required`；`blackboard/tasks/interruption.py:_directory/_marker_identity/_stop_native` 的崩溃结算。由服务从自己的 `store.directory` 与 attempt 推导固定路径，读取并验证 task/attempt/generation、文件私有性与完整身份一次，然后把已验证的目标交给系统层。`protocol/process_identity.py:kill_verified_process_group/stop_orphan_process` 是机制实现，不能继续成为仅凭任意 dict 就拥有授权意义的入口；调用契约要清楚表达来自哪个已验证执行。新启动的子句柄在创建边界已有归属，不额外再做一次路径扫描。

三系统共用这个授权规则；Windows 额外把 Job 名、私有对象权限与所属执行绑定，POSIX 校验原组和精确 root start。Windows 11 与三系统模型无关测试均需创建私有状态 A、B：A 能接回自己的旧执行，不能结束 B 的执行；伪造 argv/cwd、换启动文件路径、同名 Job、改 generation、PID 重用与读权限错误必须拒绝或 unknown，B 的替身保持存活。只读进程扫描为回收报告占用，不能成为绕过这条规则的停止入口。

## 8. 文件、契约与可各自验收的实施顺序

本批涉及的主要修改面已在各节列出。公共新模块建议为根包 `windows_files.py`（原生文件句柄，不携带业务授权）、私有边界 `private_dirs.py`、公共私有事实发布模块（最终名称由实现任务定）。现有 `windows_paths.py` 保留工作树政策并调用公共文件能力；不留复制的 Win32 文件打开/关闭实现。测试辅助使用产品的身份与 Job 原语，只拥有测试根和自己创建的句柄，不把测试全机观察逻辑并入产品恢复。

阶段 A：先做模型无关的所有权与 boot 实验，产出可审查的 Windows 11 原生证据与 Linux/macOS 脱离后代的最小复现。Windows 验收点是 BootIdentifier、无 kill-on-close 的根 Job 在服务退出后的存续/按名接回、嵌套与禁止 breakaway、无控制台强停；POSIX 验收点是明确现有组判定漏掉哪种后代，以及选定机制能否给出完整结束证明。未找到 macOS 或普通用户 Linux 的可接受机制就交回设计选择，不能开一个承诺修好的实现任务。不得运行真实 harness 或模型来代替这些系统实验。

阶段 B：私有文件/目录边界及直接 pywin32 依赖。依赖设计获审；可在 A 的实验进行时独立实现。验收为 ACL 正反例、同句柄读取、祖先置换、fd 所有权、原有 POSIX 边界、fast cwd 与 console session；删掉调用方互相矛盾的 mode 校验。这个阶段不改变停止结算。

阶段 C：公共事实发布和唯一备份 journal。依赖 B。验收为写者强杀的每个发布窗口、gzip 写者刷盘、三系统恢复一致、只读 fsync 循环与 exchange 分支删除、失败保留唯一完整代。同步更新 owning reference 的故障模型；不因消除 PermissionError 就宣称 Job 修好了。

阶段 D：执行根 Job 生命周期、启动证据、服务接回与第 13 条授权落点。依赖 A 的 Windows 结果、B、C，且新增 ADR 措辞已由开发者确认。验收为服务崩溃和同契约私有重启时原执行继续、按固定状态根接回、持有同一 Job 句柄观察与停止、根先退仍能处理后代、两个状态根互不停止。native 短命 Job 的 kill-on-close 保留且经测试，所有异常仍不假报 stopped。

阶段 E：三系统回合后代与封存/回收终结规则。依赖 A 的 POSIX 设计结论和 D。验收为第 5 节的完整替身矩阵、工作仍在写检出时不得结算/回收、正常回执前只剩执行根的判据、强停后的整个边界空、与旧执行相同身份的接回。该阶段有一个系统未能给出证据，待办就仍未完成；不能通过弱化 shutdownConfirmed 语义结束它。

阶段 F：产品忙文件/只读删除和测试辅助关闭/观察。文件删除原语依赖 B，产品生命周期与测试根最终收尾依赖 D、E；手建测试 store 未关闭等明确缺陷可先独立修。验收为每个占用者持有句柄时的明确失败、释放后的同计划恢复、Git 对象只读清理、SQLite 全连接关闭、Windows 观察失败保留根并退出 3。按日志堆栈修各资源所有者，不写吞异常的通用删根器。

阶段 G：Host 最终整合与安装前演练。依赖 B 至 F 的分别验收。计划要求的 Opus 独立设计审查应在实现前完成，不能拖到阶段 G 才审设计。按最终源码在三系统跑完整 Python 检查，分别保存退出码、文件清单、teardown 结果；Windows 11 原生实验单独成记录，CI 不替代它。再在私有安装和正在运行的模型无关执行上做同契约重启/补丁替换演练，核对旧 runtime 留存、执行继续、结果仅提交一次、回收有实际停止证据；改变契约的升级另做空闲围栏、备份、失败回滚演练。不把第一次安装本批候选当作“同契约补丁保活”的验证。本文没有执行这些动作，日常安装仍由 Host 后续安排。

版本与升级先算清再实施：仅修文件句柄或测试且不改契约时不凭空改中间号；新增启动意图/回执或跨进程停止事实形式属于 ADR-031 第 10 条的契约变化，必须移动中间号与 CONTRACT_VERSION，首次安装要求空闲。当前升级基线是 0.49.0、schema 27、没有 pending upgrade；如改存储形式，在同一待执行升级中转换或清理真正被替换的非封存 stock，并扩充 fixture generator 和新旧板比较。既有 sealed manifest、回执及其摘要绑定文件不能重写，新 seal 自带形式。无法补全的历史身份不推断、不补造 bootId；若不改数据库表，是否需要 schema/文件升级仍按真实被改的存储形式明确登记，不能因为载体是 JSON 就跳过升级要求。

实现后的 owning references 至少核对 `docs/reference/architecture.md`、`operations.md`、`workers.md` 以及 ADR-031 的新增决定；验收证据进入 `docs/acceptance/`，计划和清单的状态由 Host 在证据稳定后更新。本文阶段划分不是已派发任务，设计提交也不代表独立审查、Windows 验证或安装验收通过。
