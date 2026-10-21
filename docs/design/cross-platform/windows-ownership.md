# Windows 第二批：执行所有权、停止证据与私有存储

2026-10-21，设计候选，核对基线 `ab21a83a8e4c868ab3014ef7439ec2f11505d3e5`。本文只提出设计与实现验收顺序，没有修改产品代码、运行检查套件、连接日常看板、调用真实 harness 或验证 Windows 11。依据为[分批计划](plan.md)、[清单](inventory.md)的 F01、F02、F04、F08、P01、P02、P04、T03、AGENTS.md、[架构](../../reference/architecture.md)、[ADR-007](../../decisions/007-neutral-core-and-single-current-contract.md)、[ADR-010](../../decisions/010-production-workflow-repair-plan.md)、[ADR-031](../../decisions/031-one-process-per-execution.md)。涉及 harness 的建议同时受 ADR-023、ADR-030 约束。本文的“已核实”指本次读到源码或第十轮日志，不能当作目标系统的运行验收。

本轮按 Host 核对独立审查后的决定修订：先用冻结的状态根文件身份挡住看板副本停止原执行；Windows 文件句柄层沿用现有 ctypes，只用 pywin32 校验 ACL；执行根 Job 保留执行，原生 Job 全空后交回执，执行根退出且根 Job 全空后才释放资源。服务控制台及外层 Job、登录会话、提权属主等新增假设均未验证，列入阶段 A。Linux/macOS 脱离进程组的后代另成一批，本批不宣称解决，也不再以它阻塞本批安装。

支持范围沿用计划：Windows 11 x64、本地 NTFS、同一个登录会话、Python 3.12；另建议将下限改为 3.12.4，交开发者确认；作为 Windows 系统服务运行、网络盘、FAT/exFAT 和 Windows ARM 不在首发范围。Linux、macOS 继续使用各自的真实开机会话和内核进程创建值。三系统共用任务身份、单一事实发布协议、结果导入及停止结算规则；ACL、Job、重解析点和文件共享模式在 Windows 的系统边界实现。

## 1. 第十轮日志能证明什么

run `39555977214` 的四份 `overview.md` 记载：w1 为 74 文件、26 通过、48 失败；w2 为 74、22、52；w3 为 74、27、47；w4 为 74、23、51；四片均退出 1，共 296 文件、98 通过、198 失败，没有未完成文件。这是该轮的检查结果，本文没有重跑，也没有把 GitHub 的 `windows-latest` 当作 Windows 11 真机证据。

本次按日志行的字面匹配计数，`PermissionError: [Errno 13]` 共 729 行，`WinError 5` 共 270 行，`WinError 32` 共 133 行，完整的 no-tool cwd 错误共 168 行，`Private test process shutdown unconfirmed` 共 108 行，`Bad file descriptor` 共 36 行，`has no attribute 'killpg'` 共 25 行，`511 != 488` 共 20 行。日志有重复异常与堆栈，计数不是独立用例数，不能与任务输入的粗数直接比较。w2 日志含非 UTF-8 字节，检索时以替代字符解码，不能由此声称产品存在新的编码缺陷。

代表性调用链已对照源码：w1 的目录拒绝访问落在 `protocol/execution.py:fsync_json` 的 `os.open(path.parent, os.O_RDONLY)`；w3、w4 的备份错误落在 `blackboard/store/backup.py:create` 以只读句柄重新 `fsync` 的循环；w1 的 Git 对象拒绝访问发生在测试 `shutil.rmtree`；w2 的 `health/board.sqlite3` 删除失败发生在测试收尾；`killpg` 的代表性堆栈在 `tests/python/buddy/harnesses/test_controller.py:controller_log_close_failure` 和 DSH ACP 的 POSIX 观察测试，不能把 25 次都说成产品在 Windows 调用了 `killpg`。

源码依据以下均相对 `src/hey_my_buddy/`；测试路径相对仓库根。引用函数名用于定位，避免把清单旧基线的行号当作当前行号。

## 2. 私有目录与文件：一次句柄级属主及 ACL 校验

已核实：`private_dirs.py:ensure_private_dir` 在 Windows 上不设置或验证 DACL；`protocol/transport.py:_private_directory` 在 Windows 上只判断真实目录；`protocol/execution.py:_private_json/read_execution_receipt` 仅在 POSIX 检查 uid 和 mode。相反，`console/console_sessions.py` 的 session 读取与 `buddy/roles/run_execution.py:start_fast` 仍用 `st_mode & 0o077` 拒绝 Windows 路径。`private_dirs.py:open_regular_fd` 在 Windows 上先检查路径、普通 `os.open`、再检查路径，没有 POSIX 逐级固定祖先的等价保证；`buddy/roles/turn_io.py:_private_bytes` 也只有 POSIX 分支固定父目录 fd。

本轮补核对两组凭据入口。`cli/main.py:_save_control/_read_control` 保存及读取 Host 控制能力文件，读取在 Windows 上跳过 uid/mode 校验；`_submission_token/_read_submission_token` 保存及读取提交令牌，读端直接用 `path.read_text`，没有真实的 Windows ACL 校验。`install/launcher.py:take_agent_credential_document` 对继承的执行凭据描述符、`read_private` 对运行时选择等私有文件，在 Windows 上也不检查真实属主和 DACL。执行凭据的文件入口 `agent_credential_document` 同样要经统一边界；已经继承的 fd 使用其原生句柄校验，不按路径重开。不能只修服务端回执而留下这些能力文件。

建议将 `pywin32; sys_platform == 'win32'` 加为直接依赖并锁定版本。当前 `uv.lock` 已因 `portalocker 3.2.0` 的 Windows 条件依赖锁入 `pywin32 312`，有 CPython 3.12 x64 wheel，`pyproject.toml` 尚未直接声明。直接依赖表达本项目对安全接口的使用，不依赖 portalocker 永远替本项目带入它。本文没有在 Windows 导入模块；API 签名、句柄适配和稳定运行时中的导入均为未验证。

库与 ctypes 的取舍按实际边界收窄：pywin32 只用于 `GetSecurityInfo`、ACL 遍历与 SID 比较，避免自己维护安全描述符、变长 ACE 与 SID 的解析；现有 `blackboard/tasks/windows_paths.py:WindowsFileAPI` 的 ctypes 句柄层原样搬到根包拟新增 `windows_files.py`，不使用 win32file/win32api 重写打开、固定、fd 转移、关闭与错误映射。`windows_paths.py` 保留工作树政策并调用搬出的能力，私有文件入口复用它；不保留两份实现，不另写 ctypes ACL 回退。`protocol/windows_process.py` 的 Job 层仍沿用 ctypes。本批新增的卷/文件身份读取和必要共享模式在唯一文件边界内扩展，不借搬移重做整个路径模块。

Windows ACL 只留一条接受规则：属主是当前用户 SID 或本机 Administrators SID；DACL 存在且不是 NULL DACL；每一条 ACE 都是普通 allow 类型，受托者都在当前用户、LocalSystem、Administrators、OWNER RIGHTS 的白名单内。必须遍历全部 ACE，包括只作用于子对象的 ACE；其他属主、deny ACE、未知类型、对象或条件 ACE、白名单外主体一律拒绝。空 DACL 与 NULL DACL 区分，规则不额外要求某个访问掩码或“用户完全控制”；实际操作是否获准由系统调用决定。取消 generic mapping、继承范围分析和“状态根 DACL 必须受保护”的要求，不实现通用有效权限分析器。Windows 提权创建时属主是否为 Administrators、提权与非提权交替使用同一根能否正常工作，均需实验。此规则不承诺隔离管理员或同一用户的任意 shell。

新私有目录用 Python 自带的 `mkdir(mode=0o700)` 创建，使用 Python 3.12.4 起在 Windows 上设置的受保护 DACL，不自造安全描述符；目录内新文件靠继承，凭据文件也不另带显式描述符。创建后仍以句柄读回并按上述唯一规则校验；已存在目录同样校验，不自动修改其 ACL。默认状态根、逐级创建的私有父目录与 PowerShell 启动器预先建立的目录都在范围内，不能认为传了 mode 就已证明既有目录安全。Python 创建的实际主体、保护标志及子文件继承仍未验证。建议将 `requires-python = ">=3.12.4,<3.13"` 交开发者确认；本任务不改依赖文件，也不为较早版本保留另一套建目录方法。

文件边界继续使用已有 `FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS` 语义逐级打开并固定祖先，父句柄不允许删除共享，检查类型、重解析属性与最终名称。私有叶子经同一句柄检查普通文件、属主和 DACL，然后交给读写者；ACL 用句柄上的 `GetSecurityInfo`，不用路径查询冒充同一对象。新文件独占创建并继承私有父目录权限；句柄转 CRT fd 只转移一次所有权，使用二进制及不可继承标志。普通源检出复用路径固定而不套私有 ACL 政策，Git 链接仍由工作树政策处理；POSIX 保留 `dir_fd/O_NOFOLLOW` 与 uid/mode，调用点不叠加另一轮权限猜测。

受影响入口包括上述 CLI 与 launcher 凭据、`protocol/transport.py` 的状态根/token/endpoint、`private_dirs.py` 的目录与普通文件、`protocol/execution.py` 的 startup/live/receipt、`buddy/roles/turn_io.py` 的私有输入输出、`console/console_sessions.py` 的 session、`buddy/roles/run_execution.py:start_fast` 的空 cwd，以及备份和安装的私有文件。空 cwd 仍须私有、真实且为空。不要求磁盘根或普通用户目录一律私有；固定祖先的路径政策与私有对象 ACL 政策各有明确作用。

未验证的 Windows 11 实验全部列入阶段 A：普通用户与提权创建、两者交替访问同一根；Python 3.12.4 之前和之后、默认根及 PowerShell 先建目录，读回实际 owner/DACL/继承；普通 allow、只作用于子对象的白名单外 ACE、deny、NULL DACL 与其他属主的正反例；SQLite 和凭据新文件继承；并发把祖先换成 junction、叶子换成 reparse point；在稳定运行时里替换源目录后 `import win32security`。API 失败或导入失败明确报告，不能回退 mode 判断或放行。

拟扩充 `tests/python/test_private_directories.py`、`tests/python/blackboard/tasks/test_windows_workspace.py`、fast Router、console session、CLI 控制能力/提交令牌与 launcher 凭据的原有测试，并新增 Windows 原生 ACL 测试。可注入边界只验证异常收尾；ACL、提权、继承和路径竞态必须有原生证据，POSIX 的 uid/mode/链接拒绝回归保留。

## 3. 落盘：进程崩溃保证与断电边界

三系统共同承诺应是：一次发布返回成功后，即使写入进程被强杀，后继进程仍读到完整的已发布事实；发布过程被杀则只可出现完整旧版、完整新版或明确的未完成状态，不能把半份内容当作成功。备份还要求任一发布阶段中断后能恢复到可验证的完整一代。文件写入成功不等于任务结算成功，SQLite 的状态转换、事件和幂等记录继续在一个事务里提交。当前 `blackboard/store/db.py:Database._configure` 使用 WAL、`synchronous=FULL`；本批不改成弱同步，也不在 SQLite 外再造另一份权威状态。

必须走该承诺的文件是：执行启动前的 `spawn.intent`、startup；放行原生工作前的执行身份和 native journal；原生 spawn 的 pending 与完整身份；用于恢复的 live 描述符及 Router budget；结果回执、交接引用指向的事实、不可变工作区 manifest/patch/物理封存证据；备份 payload、manifest、发布 journal；安装的切换 journal、恢复快照及成功指针。依据为 `protocol/execution.py:launch_execution/fsync_json`、`protocol/process_identity.py:NativeProcessJournal`、`buddy/runtime/execution.py:Execution.run/receipt`、`buddy/runtime/receipts.py`、`buddy/roles/turn_io.py:_private_bytes`、`blackboard/tasks/workspace.py:_write_once`、`blackboard/tasks/windows_paths.py:write_once`、`blackboard/tasks/storage.py`、`blackboard/store/backup.py` 和 `install/upgrade.py:write_journal/restore`。诊断 stdout/stderr、控制台显示用 session 记录、内存活动提示不升格为停止或恢复事实；可以失去末尾，不能拿它们补齐缺失身份。

建议新增公共的私有字节发布实现：同目录独占创建临时文件，写入完整 UTF-8/二进制内容，在仍持有写句柄时 `flush` 和 `os.fsync`，关闭后按原子替换发布；不可变对象使用 compare-and-create，已有同内容可复用、不同内容报冲突。POSIX 发布后刷父目录；Windows 进程崩溃承诺不要求打开目录再 `fsync`，成功替换后的目录项由仍在运行的内核保有，不声称具备 NTFS 目录更新的断电耐久性。所有读写使用第 2 节的句柄边界；不再保留 execution、turn_io、daemon 各一份相似写入器。原子替换在目标支持范围的真实行为仍要经过中断实验，不能仅因 `os.replace` 存在就宣称已验证。

已核实的差异是 `protocol/execution.py:fsync_json` 无条件刷目录，`NativeProcessJournal._publish` 和 turn_io 已跳过 Windows 目录刷盘，`blackboard/store/backup.py:sync_dir` 也明确只对 POSIX 刷目录。修法是统一它们的故障模型和实现，修改函数的“文件及目录已经 durable”注释；不用 Win32 目录句柄刷盘再叠一套未经证明的机制。native journal 的 pending 必须在启动前发布，完整身份发布后才解除 pending；中途崩溃留 pending 就留 unknown，不用“暂未找到进程”消除它。

F08 的根因已经由 w3、w4 的 `backup.py:create` 堆栈确认。`_private_copy` 已在写句柄上刷盘；压缩数据库的 GzipFile 退出后还需在底层 `compressed` 写句柄上 flush/fsync，确保 gzip 尾部也落下；manifest 和 publication journal 同样在各自写者手中刷盘。删除最后遍历全部文件、以 `rb` 重开后刷盘的循环。验证内容仍读只读句柄，不再把验证和补刷盘混合。

备份建议三个系统共用现有 journal 发布协议：先完成并验证 `.incoming`，发布 journal，必要时把 current 改名为 `.previous`，把 incoming 改名为 current，验证 current 后删除 previous，最后删除 journal。已有实现位于 `backup.py:_publication_journal/_recover_publication/create`；POSIX 目前另走 `exchange`，应在替换时删除这套分支及专属测试，不保留自动回退。恢复必须在备份锁下进行；current 在两次改名之间可以暂时缺席，所有正式读者必须先恢复或报告进行中，不能承诺路径一直存在。首次备份中断还没有完整一代时，返回未完成并允许重建，不声称一定有可恢复备份；不得丢掉唯一已验证的一代。

决定 4 的推论只成立一半。已核实 `protocol/process_identity.py:boot_id/inspect_process_identity`：Linux 读 boot UUID，macOS 读 `kern.bootsessionuuid`，Windows 调 `NtQuerySystemInformation(0x5A)`，取前 16 字节作 BootIdentifier；相异时立即返回 `boot-changed`；这个当前实现尚无本轮新增的状态根文件身份与 Windows 会话围栏。`blackboard/tasks/interruption.py:_stop_native` 已把这个结果作为所有旧执行与原生进程停止的证据。真正换了一次内核开机会话，旧内核的进程不可能继续存在，因此这个推理可以证明进程停止；它不能证明断电前的回执、备份或未提交的 SQLite 数据还存在，也不能重建已丢的产物。恢复应记中断、按保留下来的证据处理部分输出，不能填造成功回执。新版恢复先做第 7 节的状态根归属核对；Windows 还须核对登录会话，不得在副本或不同会话中跳过围栏直接结算。

Windows 分支目前只有代码，没有本次可读的系统 API 契约材料或 Windows 11 运行证据；源码注释写的“documented-size prefix”不能证明 `0x5A` 及 32 字节缓冲区在目标系统可用。因此不能现在确认 Windows 的上述前提。重启、关机后开机（含 Fast Startup）、休眠恢复、睡眠恢复、同次开机注销再登录分别记录标识与存活测试进程；要求实际终结旧进程的内核重启改变标识，保留用户进程的恢复不产生错误的改变。Fast Startup 是否换标识必须按观察记录，不从“点了关机”推导；旧执行已经结束但标识没变时，应依完整身份缺席证明。API 失败或结果不可靠时保持 unknown，禁止用墙钟启动时间或生成的 UUID 代替。

应在启动意图中先冻结启动状态根的文件身份、已观测 bootId，以及 Windows 的会话号和预分配 Job 名，再允许创建执行；启动文件、marker、跨进程身份与持久记录绑定同一组启动事实，避免只剩没有归属或开机会话的 intent。当前 `spawn.intent` 只有 taskId/attemptId/generation，完整身份稍后才写入 `spawn.marker`；仅有这份 intent 不能在重启后凭空知道是哪次开机启动的。没有完整身份的既有意图不能用文件 mtime 推定启动会话，也不能把当前状态根身份补填成历史启动属主。启动到身份发布间的中断必须有测试：未放行原生工作不等于执行根已经消失，未证明的根仍占名额。若修改跨进程事实格式，按第 8 节处理契约与升级。

未验证实验与测试：Windows 11 上对每个文件发布阶段、gzip 尾部结束前后、journal 两次改名和清理阶段设置模型无关的暂停点，由父测试进程强杀写者，重新启动读者/恢复器核对摘要、绑定和完整代际；POSIX 做同一组进程中断测试。为 bootId 单独保留重启前后探针记录。断电实验只能在可丢弃虚拟机或专用环境做，目标是确认重启后的停止结算与错误报告，不把它作为“不丢数据”的验收。拟扩充 `tests/python/protocol/test_native_process_journal.py`、`tests/python/buddy/runtime/test_execution_process.py`、`tests/python/install/test_backup.py`、`test_backup_windows.py`、`test_upgrade.py` 和 `tests/python/blackboard/store/test_interruption_recovery.py`。

## 4. 服务启动、执行 Job、会话与停止证据

已核实两层启动路径。服务冷启动在 `protocol/transport.py` 的 `subprocess.Popen` 只给 `start_new_session=True`，没有 Windows `creationflags`；它没有 Windows 脱离启动器控制台及外层 Host Job 的实现。执行路径是 `protocol/execution.py:launch_execution` 预分配 Job 名，`process_controller.py:launch_controller` 调 `owned_popen(close_fds=True)`，`owned_process.py` 以 suspended 启动、加入 Job 再恢复；`windows_process.py:create_job` 每个 Job 都设 kill-on-close。Job 句柄在父进程的 `Popen._buddy_job`，执行从 startup 取名字生成身份，没有接持根 Job 句柄。当前两层实现不能证明执行挺过启动器、Host 或服务退出。

Windows 服务改用 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`，不再依靠 POSIX 的 `start_new_session` 表达 Windows 生命周期；存在外层 Job 且允许脱离时加 `CREATE_BREAKAWAY_FROM_JOB`。若外层 Job 不允许脱离，明确报“执行活不过 Host 的 Job”，不把此环境当作已满足执行独立性的启动成功，不靠补一个内部 Job 抵消外层限制。无外层 Job 时不需要 breakaway。外层权限的识别、`SILENT_BREAKAWAY_OK` 与显式 breakaway 的组合及实际继承结果都未验证，阶段 A 必须给出证据；实现不要通过失败后盲目换一组 flags 来假装成功。

执行与原生子进程的统一 Windows 创建边界加 `CREATE_NO_WINDOW`，仍先 suspended、加入其 Job 再恢复；这不替代服务的 detached/breakaway，也不绕过用户 harness 的设置。阶段 A 从 PowerShell 窗口、VS Code 终端、Host harness CLI 的工具 shell 分别启动私有服务，再关闭启动一方，核对控制台和 Job 链，证明服务及执行是否继续。此处只使用工具 shell 启动模型无关替身，不发模型请求；需要访问真实 harness 的实验由 Host 后续安排。本任务没有操作这些界面或进程。

执行根 Job 不设 kill-on-close，也不允许 breakaway；原生 harness/探测/command 的短命 native Job 继续设 kill-on-close，并且不允许脱离，由执行或探测进程持有。服务关闭最后根 Job 句柄不应结束执行，执行崩溃关闭 native Job 句柄则触发原生工作清理，最终由根 Job 证明执行边界已空。保留这两种已有创建用途，不对全部 Job 一律取消清理属性，也不引入继承服务句柄的移交握手、PID 递归停止、轮询续租或看门狗。

Windows 11 需验证无 kill-on-close 的命名 Job 在所有服务句柄关闭后仍因关联进程存活而可接回，native Job 可以嵌套且后代仍在根 Job 中，Python/uv/shell/Node/new-console/breakaway 请求不能逃出根 Job。尤其验证启动器外层 Job 带 `SILENT_BREAKAWAY_OK` 时服务、执行、孙进程各归哪一层，以及 conhost 是否计入 ActiveProcesses；不能把进程计数看似只多一个的情况忽略掉。若组合不成立，回到设计选择，不用过滤进程名或放宽空 Job 判据补齐。

Job 使用令牌的默认 DACL，不设置显式安全描述符；保留拒绝 `CreateJobObjectW` 打开已有同名对象的创建校验。随机 `Local\HeyMyBuddy-<uuid>` 只负责定位，必须绑定冻结的状态根文件身份、bootId、会话号及 task/attempt/generation，名字不单独授予停止权限。创建执行根时保持 suspended，取得并持久化完整身份及 Job 绑定后再恢复线程；原生工作仍须等待现有 `Execution.run` 的身份确认。创建到登记的崩溃窗口保留 pending，缺失登记不等于没有子进程。

`Local\` 名字空间按 Windows 登录会话隔离。现有 `process_identity.py:validate_process_identity` 的身份只有 bootId、pid、startedAt、processGroupId、jobName，没有会话号。新版在 Windows 身份及启动事实里加入由 `ProcessIdToSessionId` 得到的会话号；POSIX 采用明确的不适用形式，不按字段缺失推断版本。服务打开 Job 或据 Job 缺席结算前，先核对当前会话与启动会话；不同或无法读取就 unknown，不接回、不发信号，也不把按名打不开解释为原 Job 已不存在。重启后的新会话是否仍相同不作假设；bootId 变化的停止事实不能在不同会话中绕过本批规定的归属边界。会话 API、同用户不同会话、会话切换后 Local Job 的可见性列入阶段 A。

接回从 `blackboard/service/execution_scheduler.py:_resume_executions/verify_identity` 的权威 attempt 出发，先做第 7 节状态根文件身份及 Windows 会话核对，再读取固定私有证据，以 `OpenJobObjectW` 保留同一句柄完成查询、必要的停止和结束观察。根仍在时核对 bootId、原始创建 FILETIME、PID 与实际 Job 成员关系；根已退而 Job 还有成员时只能按已绑定的同一 Job 结束遗留工作。当前 `process_identity.py:_SystemSource.job_members/stop_job` 分别开关 Job，应收敛为同一次持有操作，不能查询后关闭再按名字打开另一个对象。这里不新增“已验证目标”类型，业务归属只在入口校验一次。

Windows 有序停止沿现有 ExecutionLive stop 与 harness 原生 interrupt/close 路径，宽限后用 `TerminateJobObject`。`process_handle.py:signal_group` 当前把 SIGTERM 映射为 CTRL_BREAK_EVENT 并吞掉异常，不能把发送控制台事件当作无控制台运行的共同软停保证。command 替身没有原生协议，不承诺等价的 SIGTERM 处理机会。仍区分正常、宽限结束、强停及 service-stop，不伪造 POSIX 负退出码。

同状态根、同会话中的停止证据是：持有的根 Job ActiveProcesses 为 0，且根的完整身份已退出；或按名打开明确返回对象不存在，同时根身份缺席且启动绑定可信；或符合归属及会话前提的真正 boot-changed。拒绝访问、查询失败、不完整成员列表、身份冲突、不同会话都保留 unknown。`TerminateJobObject` 返回成功、creator-reaped、长调用断开、回执里的成功布尔值都不能单独证明全部工作结束。正常回执前 native Job 必须全空，回执接收与根退出后的资源释放次序见第 5 节。

POSIX 继续使用 SIGTERM、必要时 SIGKILL 与原进程组缺席；killpg 成功不是结束证据，根先退而组仍在、PID 重用且组仍在继续 unknown。本批修正三系统共同的归属和结算顺序，不把进程组扩成不可逃逸的子树，POSIX 脱离后代按第 5 节拆出。

拟修改 `protocol/transport.py`、`windows_process.py`、`owned_process.py`、`process_controller.py`、`process_handle.py`、`process_identity.py`、`execution.py`、`buddy/runtime/execution.py`、`buddy/runtime/receipts.py`、`blackboard/store/store.py`、`blackboard/service/execution_scheduler.py` 与 `blackboard/tasks/interruption.py`。拟扩充 `tests/python/buddy/runtime/test_windows_process.py`、`tests/python/protocol/test_process_identity.py`、`tests/python/blackboard/service/test_execution_recovery.py`、`tests/python/blackboard/store/test_interruption_native_tree.py`、transport 冷启动及安装执行围栏测试。Windows 原生实验另记录环境、外层 Job flags、会话、完整身份、成员和实际退出结果，不把 mock 当实测。

## 5. Windows 后代停止与三系统共用的结算次序

已核实待办 `docs/design/backlog.md` 记录 ZCode CLI 某回合经 `uv run` 起的预览服务，在停止确认及检出回收后仍存活，但没有核实它怎样逃过停止。当前 `buddy/roles/run_execution.py:InlineRunHandle.shutdown_confirmed` 只依据 native stop evidence 的 group_state；native journal 登记通过本项目 owned_popen/ProcessHandle 创建的原生运行，不登记 harness 工具内每个后代。不能把待办写成已复现 uv/ZCode 自行脱离，也不补造 CLI 版本。

按 Host 本轮决定，Linux/macOS 脱离进程组的后代另成一批，由开发者决定支持条件；本批照实保留“POSIX 上未解决”。普通同组后代继续按原组处理，任意 setsid、双重 fork 或脱离后代不能由 killpg 证明已经停止。本批不实现 cgroup、递归 PID 追踪或 cwd 扫描，也不在回收边界增加按 cwd 找进程的检查；这些方案与其权限、竞态和强度留到后续一批定。本批安装不再以前述 POSIX 完整子树方案为前置条件，公开说明与验收必须保留这个缺口。

Windows 具体使用 native Job 嵌套于禁止脱离的根 Job。回合结束、取消、超时均先执行原生有序收尾，必要时强停 native Job，观察所有 native Job 全空后再封存原生输出并发回执；执行崩溃则由服务依据完整归属与根 Job 处理遗留成员。执行根退出后根 Job 也必须全空，服务才确认全部停止并释放资源，不通过“只剩执行根”提前释放。不增加“只交付阶段”的事实、进程计数例外或对应服务端验证；conhost 对空 Job 判据的影响先在阶段 A 实验，不能按名字忽略。

三系统共用接收回执与最终结算的次序：服务先可靠接收回执并立即确认收到，attempt 保持 finalizing、保留容量与资源；执行收到确认后退出；服务沿现有长调用失败后的身份观察或自己持有的回收证据确认根和已登记工作边界结束，再一次性导入权威结果、事件并释放资源。Windows 对应根 Job 全空，POSIX 对应既有完整身份及已登记原组的结束证据，不把这一点描述成已覆盖脱离后代。未知就继续保留占用，不因为成功回执已经收到而释放。

已核实 `buddy/runtime/receipts.py:deliver_receipt` 要等 submit_result 返回后才退出，当前还会写 delivery 并删掉 receipt；`blackboard/store/store.py:execution_result_reference/execution_result/_commit_execution_result` 会在收到 shutdownConfirmed 时完成 attempt 并释放 claims。需要调整这两处与调度观察：回执成功响应只确认服务已持久接收，不能等待根退出才回应 RPC；接收阶段复用 finalizing 并保留已接收的原始回执，最终停止确认前不删掉恢复所需内容，也不发布完成事件。最终结算仍是一条路径和一个原子事务，重启后复用已接收回执恢复；不把 native 已停的事实改写成假布尔值，不新增另一种结果或一套交付阶段协议。结果接收、根结束与最终事务之间的断点都须验证幂等与仅结算一次。具体暂存应复用现有 attempt/回执存储，在实现任务中同步调整其状态含义及恢复读端，不保留原来的收到即释放分支。

模型无关实验覆盖同组孙进程、主进程先退出、native Job 内孙进程忽略软停、新控制台、breakaway 请求、uv 启动替身、服务先崩与执行先崩。验收同时观察 native Job 全空后才发回执、RPC 不因等根退出而死锁、根 Job 非空期间不释放容量/claims、不启动冲突工作、不回收检出，根结束后只结算一次。若 harness 通过已有外部进程代起工具工作，Job 不自然覆盖，需如实记录边界而非假报全停；真实 CLI 的版本化复现由 Host 后续安排，本任务没有运行。

拟扩充 `tests/python/protocol/test_native_process_journal.py`、`tests/python/blackboard/store/test_interruption_native_tree.py`、`tests/python/buddy/roles/test_run_execution.py`、`tests/python/buddy/runtime/test_execution_delivery.py`、`tests/python/blackboard/service/test_execution_recovery.py`、store 结果导入及容量/claims 测试。共同结算规则要在正常、失败、取消、服务重启与回执确认前后崩溃中保持一致；POSIX 脱离后代的完整终结和 cwd 回收扫描不在这些验收的完成声明内。

## 6. 占用文件、只读文件与测试收尾

产品中的第一类是事实文件更新：startup/live/budget/receipt、native journal、控制 token、endpoint 与 session 文件。发布者要在改名之前结束自己的写者；允许原子换代的只读事实读者在 Windows 明确使用删除共享，读取和校验仍基于持有的那份对象，不要求所有读者都看到最新一代。固定祖先的句柄继续不允许删除共享，但在删除或改名该目录自身前必须退出固定祖先的操作作用域。目标被外部不允许删除共享的句柄打开时，报告忙并保留已发布旧事实与未完成状态，不无限重试、不删除目标再声称原子替换。阶段 A 在开启 Defender 实时扫描时反复运行发布热路径，核对杀毒软件与索引器是否造成短暂占用；只有确有偶发证据时，才在唯一文件边界对确定的瞬时占用做一次有界重试，并测试重试上限与失败保留。调用点不各加重试，长期占用、ACL 拒绝与证据未知不重试成成功。

第二类是 SQLite、备份和升级恢复：`Database.connect` 缓存每线程/嵌套层连接，退出读事务不会关连接；`Database.close` 才关闭缓存。`daemon.py:cleanup` 已在调度、维护、harness 和 console 清理之后调用 db.close，不能根据测试日志断言产品完全没有关闭数据库。备份在安装协调器已脱离写者并完成 WAL checkpoint 的条件下复制，恢复在替换 board 及 sidecar 前必须让唯一所有者的全部连接关闭并释放操作句柄；与 Windows 无关的存储一致性围栏保留。外部 SQLite 查看器等占用时保留 journal、当前备份和看板，不删除 WAL/SHM 来绕过 sharing violation。恢复的 `install/upgrade.py:restore`、备份 generation 清理使用同一显式忙错误，允许用户解除占用后重试该 journal 的恢复。

第三类是受管检出、私有会话与宏任务 tomb：`workspace.py:remove_checkout` 先核验注册与锁再用 Git 删除，失败时恢复该注册的锁；`workflow.py`、`macro_task_deletion.py`、`macro_task_deletion_workspace.py`、`tasks/storage.py` 调用 `private_dirs.remove_tree` 删除自己拥有的私有树。应保持现有清理计划/墓碑记录，删除不完整就明确可重试，不把目录一部分消失当成整个工作区已回收。Git worktree 元数据仍交 Git；仓库公共 `.git/objects` 不是某次工作树的可删除范围。本批不在产品私有树删除器中增加清除 READONLY 的能力，目前没有已核实的产品使用者；只读文件仍明确报出删除失败并保留清理计划，不扩大 DACL、不递归 chmod 用户源仓库。测试根的只读 Git 对象单独处理，见下文。

第四类是 runtime/skill 安装：`install/runtime.py` 有 staging rename 和若干 ignore_errors 清理，`install/upgrade.py` 有 skill 切换、回滚及旧代删除。Windows 会拒绝删除仍被解释器加载的运行时，已有在做或 unknown 执行使用的 runtime 必须按 ADR-031 第 10 条保留；不能把解释器占用当可忽略的垃圾，也不能停止无关执行来让清理成功。正式安装步骤失败必须报错并保留恢复证据；切换后的可延后旧代清理要明确报保留原因，不能承诺清空。目录联接的创建和名字拒绝属于第三批，本批只统一关闭句柄、忙错误与删除结果，不顺带实现链接安装。

以上 Windows 特有的是共享删除、READONLY 与加载映像占用；三系统共同的是独占所有权、操作作用域内关闭句柄、禁止回收未知执行和保留失败证据。POSIX 允许 unlink 一个仍打开的文件，恰好会掩盖所有者没关闭资源的问题，不能把 POSIX 删除成功拿来证明 Windows 的生命周期已经正确。

测试辅助单独修。`tests/python/support.py:BoardTestCase._cleanup` 只关闭 `_stack` 中由 `self.board()` 注册的 InProcessBoard，而且吞掉 board.close 的异常；`tests/python/blackboard/service/test_harness_health.py:setUp` 自己建了 `BoardStore(self.directory / 'health')`，未在该处登记 db.close，这与 w2 的 health/board.sqlite3 占用相符。它是可直接核对的未关闭所有者，不代表所有 133 次共享冲突都是这个原因。应让所有手建 store、连接、console、RPC 客户端和线程在其测试中有明确的 cleanup，原生资源关闭失败保留报告，不能靠 gc 或最终 rmtree 帮忙关连接。`with sqlite3.connect(...)` 只管理事务，`tests/python/install/test_backup.py` 的这类连接要配 `closing` 或显式 close。不要简单扩大 BoardTestCase 递归搜索并强关未知对象。

测试的 Git 对象通常只读，w1 的拒绝访问堆栈就在测试仓库 `.git/objects`。在确认全部测试工作停止后，专门的私有测试根删除器可以清除私有根内普通只读文件的 READONLY，再重试该叶子一次；遇到 sharing violation、reparse 竞态或其他权限原因即保留根。此处不调用产品 Git 工作树删除的替代路径，也不能用 ignore_errors 抹掉清理失败。

`cli/checks.py:_ps_argv_table/_open_file_pids/_observation_snapshot` 目前依赖 ps、lsof 或 `/proc`，路径词法还按 POSIX 斜杠匹配；Windows 上不能仅调大超时解决“停止未确认”。建议沿已锁定的 psutil 为进程枚举建立平台观察边界，精确创建身份仍使用现有内核值；argv 用参数数组和本机路径关系，不再 split 字符串。Windows 的普通 open_files 枚举能否覆盖所需句柄必须原生验证，不能把 API 返回空当完整证明；优先使用测试启动时已经拥有的 Job/进程句柄与登记表证明自建子树结束，再对私有根访问做可用范围的观察。权限不足、进程枚举失败和无法确认的文件访问保留 evidence，退出 3 的含义不变；不向扫描发现的陌生 PID 发信号。

未验证的 Windows 11 实验：自己持有的正常读者、允许删除共享的读者、外部禁止删除共享的读者分别参与 replace/delete；打开 SQLite 写/只读连接时恢复与删根；gzip 未关闭时改名；只读 Git 对象清理；已加载 Python runtime 的保留；junction 换入删除树。验证忙错误含操作和受限相对路径、原始系统错误，释放占用后能继续同一恢复/清理。拟扩充 `tests/python/install/test_backup_windows.py`、`test_upgrade.py`、`test_execution_install_fence.py`、`tests/python/blackboard/tasks/test_workspace_submission_cleanup.py`、`test_windows_workspace.py`、`tests/python/cli/test_support_cleanup.py`、`test_checks_cleanup.py` 与相应未关闭资源的原用例。POSIX 专项 killpg/FIFO 测试正确标为 POSIX，并补 Job 对应的边界测试；不能只跳过 Windows 失败。

## 7. ADR-031 拟新增第 13 条：状态根文件身份围栏

本轮已读待办“看板副本上起的服务，停止时会强杀原服务的执行”及 Host 的 `tmp/copy-board-experiment.py`，没有运行它。待办记录 2026-10-20 私有副本服务经 `_force_if_required` → `kill_verified_process_group` 停止日常执行；脚本以两个私有服务和 SQLite 副本复现 B 停止后 A 的执行消失。现有启动 marker、startup、身份与看板都可能被复制，因此旧稿“由 store.directory 与 attempt 推导固定路径再核对”不能挡住副本；即使文件、task/attempt/generation、bootId、PID 与 start 全对得上，也只证明是哪一个进程，尚未证明它属于当前状态根。此事故不依赖 Windows，POSIX 围栏列为本批第一个实施阶段。

启动证据新增不可变的 `stateRootIdentity`，在允许创建执行之前由真实状态目录的句柄取得并冻结：POSIX 取该目录的 `st_dev/st_ino`；Windows 取卷序列号和文件 ID，均从句柄取，不把路径字符串或 Windows 模拟 st_ino 当替代。使用有平台标识的明确字段形式，数字/字节的编码由协议 schema 固定；不要在同一字段里按写入时间解释值。复制的 marker/startup/看板继续带原根的文件身份，B 目录有自己的身份，比较必不应通过；副本即使占了 A 原来的路径，也不能因同路径取得停止权限。当前源没有这个字段，Windows 获取及持久化编码为未验证，阶段 A 验证；不以本轮建议描述产品已经有围栏。

接回、`execution_scheduler.py:verify_identity`、`_force_if_required`、崩溃结算及其原生 journal 处理都在进入动作的边界先比较冻结身份与现用状态根的句柄身份，再核对 Windows 会话和完整进程/Job 身份。不能只用服务启动时缓存的路径或旧目录身份代替当前实际根；打开失败、缺少启动根身份或身份不一致就按外来执行记 unknown，不接回、不连接其执行端点、不发信号，也不导入为本根已停止。长调用失败、boot-changed 或本地缺 marker 都不能越过归属围栏。新启动执行也绑定同一根身份，执行自己的身份上报不得把副本的当前根回填覆盖冻结值。

具体落点为 `protocol/execution.py:launch_execution` 的 intent/startup/marker 与执行身份交接，`protocol/process_identity.py` 的事实校验和 journal，`blackboard/tasks/interruption.py:read_identity/record_identity/_marker_identity/_stop_native`，`blackboard/service/execution_scheduler.py` 的恢复、身份确认与强停，以及 `blackboard/store/db.py` 的持久身份表。文件边界负责取得状态根句柄身份，业务入口统一做一次归属核对；保留 `kill_verified_process_group` 及其 dict 参数，不把它重构为“已验证目标”类型，也不在系统调用层再叠同一业务校验。系统层原有对活身份及停止结果的核对仍有必要，不能把归属判断等同于操作结果观察。

当前 ADR-031 只有第 1 至 12 条。拟新增第 13 条仍交开发者确认：“服务只接回和停止由当前状态根启动的执行及其原生工作。启动前将该状态目录的文件身份冻结进启动证据；接回、身份确认、强停和崩溃结算先与现用状态根的文件身份比对，Windows 还须比对启动会话，再核对完整进程与进程组或 Job 身份。副本或同路径替换后的不同状态目录不取得原执行的归属；身份缺失、不符或无法观察时一律视为外来或未知，不接回、不发信号、不按已停释放资源。路径、PID、进程名、复制来的看板与证据文件均不能单独授予这些权限。”

状态根身份与可执行文件、harness 安装位置、Worker cwd 无关；稳定 runtime 和用户自己的 harness 可以在根外，合法工作区也通常在分配的检出。实际目录改名后文件身份可能不变，但这条比较只判归属，不额外承诺状态迁移或重定位的可用性；恢复/复制到一个新根不能继承旧执行的控制权。目录身份不能改成随机 board ID，因为 board ID 会随副本一起复制。

验收先在 POSIX 做 A/B 私有模型无关复现：A 有活执行，B 只复制看板，B 启动和停止之后 A 执行仍在；再完整复制启动文件、spawn.marker 和 native journal，结果相同，B 记 foreign/unknown 且不发信号。另测把新副本放到同一路径、同一真实根服务重启能接回、文件身份获取失败、marker/root identity 不符、PID 重用与 boot 变化，不让缺省分支绕过围栏。Windows 11 用卷/文件 ID 重做副本及同路径替换测试，并加不同会话；所有场景只操作私有根，原始复现脚本仅供改造成受支持检查用例，不能直接运行并删除其范围外现场。

该字段改变跨进程事实与存储形式，必须移动 CONTRACT_VERSION/中间号，扩展显式 schema 升级及 fixture。不能在升级时给旧活记录填当前根身份来“修复”：旧记录可能本就是副本。首次变更契约安装须空闲，升级明确转换或清理被替换的非封存启动存量，无法证明历史归属的记录不再具有新契约的接回/停止资格；sealed 回执与摘要绑定证据不重写。拟扩充 `tests/python/blackboard/service/test_execution_recovery.py`、`tests/python/protocol/test_process_identity.py`、`tests/python/blackboard/store/test_interruption_recovery.py`、native tree、schema fixture 与安装围栏测试；副本停止不能杀 A 是第一阶段的硬验收。

## 8. 文件、契约与可各自验收的实施顺序

公共文件改动集中在根包拟新增 `windows_files.py`：原样搬入现有 ctypes WindowsFileAPI，再在唯一边界接 ACL、状态根文件身份和确有证据才需要的短暂占用重试。`windows_paths.py` 保留工作树政策，`private_dirs.py` 承载私有政策；pywin32 不重写句柄层。公共私有事实发布模块统一 execution/turn_io/daemon/launcher/CLI 写入；测试观察与 READONLY 清理仍只服务私有测试根。

阶段 0：状态根文件身份围栏，本批第一个实施阶段，先完成不依赖 Windows 实验的 POSIX 修复。冻结目录 `st_dev/st_ino`，覆盖接回、verify_identity、force 与崩溃结算，完成契约/存储形式及 fixture 升级；把 Host 的 A/B 副本复现改成正式模型无关用例，要求 B 停止后 A 仍在，完整复制启动证据和同路径换副本也不能绕过。同一真实根重启仍能接回。Windows 的卷/文件 ID 对应落地随后依阶段 A/B 在阶段 D 完成，不因 Windows 工作未完延后 POSIX 事故修复；不新增目标类型重构。

阶段 A：Windows 11 模型无关的系统实验，全部为未验证，不能用本设计文字或 mock 代替结果。记录版本、登录会话、提权状态、目录身份、Job flags、实际成员及退出结果；具体需要 Host shell 或界面时由 Host 后续安排，本任务不执行真实 harness 或模型调用。该阶段可以与阶段 0 的 POSIX 实施并行，但不作为把 POSIX 围栏排后的理由。

阶段 A 的启动/Job 实验：从 PowerShell 窗口、VS Code 终端、Host harness CLI 工具 shell 分别启动服务后关闭启动方；验证 detached/new-group/breakaway、外层不允许时的明确失败、外层 SILENT_BREAKAWAY_OK 下孙进程归属、CREATE_NO_WINDOW 和 conhost 是否计入 ActiveProcesses。验证无 kill-on-close 的根 Job 无服务句柄时存续与按名接回、native Job 嵌套及禁止脱离、无控制台有序/强制停止、创建同名对象拒绝、成员查询失败保留 unknown。

阶段 A 的身份实验：卷序列号与文件 ID 的句柄读取、目录改名/复制/同路径替换；ProcessIdToSessionId、Local Job 在同用户不同会话中的可见性，跨会话一律 unknown；BootIdentifier 在内核重启、Fast Startup、睡眠、休眠及注销/登录中的真实值与存活进程关系。不同会话按名字打不开不能记为 Job 已停；API 失败不能用墙钟替代。

阶段 A 的私有存储实验：提权/非提权及交替用同一状态根；预置的 Python 3.12.4 前后环境里新目录、默认状态根、PowerShell 启动器先建目录的实际 owner/DACL/继承主体，核对白名单含 OWNER RIGHTS；凭据/SQLite 子文件继承、ACL 正反例和 junction 竞态；锁定稳定 runtime 中源目录被替换后 `import win32security`，确认 bootstrap 校验不会在尚无依赖时误载模块，并与 W08 的 .pth 问题衔接；开启 Defender 实时扫描跑文件发布热路径和占用实验。有瞬时冲突证据才设计唯一边界的一次有界重试，持续占用仍报忙。

阶段 A 的文件发布实验：普通/允许删除共享/禁止删除共享的读者参与 replace/delete，SQLite 连接占用与关闭、已加载 runtime 保留、只读测试 Git 对象删除；对私有事实、native journal、gzip 尾部、备份 journal 两次改名及清理逐点强杀写者并恢复，核对摘要、绑定与完整代际。持续占用明确报忙且现场可恢复，发布成功才满足进程崩溃承诺；断电后的启动身份与中断报告单独验证，不承诺找回丢失数据。

阶段 B：私有目录/文件 ACL 边界及直接 pywin32 依赖，依赖 A 的相关结论和开发者对 Python 下限的确认。原样搬 ctypes 文件 API，接统一安全校验和继承创建，补 CLI Host 能力/提交令牌、launcher 凭据 fd/read_private、fast cwd 与 session；删除调用点互相矛盾的 mode 判断。验收为唯一 ACE 规则、提权正反例、祖先固定、fd 单次转移、稳定 runtime 导入和 POSIX 原边界回归。Windows 安全依赖按需加载，保持 launcher bootstrap 的现有加载边界；首次启动、已有 active 指针的启动与源替换后的稳定运行时均纳入实验和回归，不能因依赖加载问题跳过私有校验。加载次序及 .pth 能否成立均未验证，先由阶段 A 暴露问题再落实现有入口。

阶段 C：公共事实发布与唯一备份 journal，依赖 B。验收写者强杀的每个发布窗口、gzip 尾部由写者刷盘、三系统完整代恢复、只读 fsync 循环及 exchange 分支删除、唯一完整代不丢。接入经过实验确认的短暂占用处理；更新 owning reference 的故障模型，不因 PermissionError 消失就宣布 Job 修好。

阶段 D：Windows 服务控制台/外层 Job、执行根 Job、会话与状态根身份接回，依赖阶段 0、A、B、C。落地 detached/new-group/breakaway 与 NO_WINDOW；Windows 身份增加会话及卷/文件 ID，先比归属再碰 Local Job；根 Job 保留执行、native 短命 Job 保留 kill-on-close，拒绝同名对象，Job 不设显式描述符。验收服务/Host 退出后实际存活、同根同会话接回、副本与跨会话不接回/不停止、根先退出仍有后代的处理、查询失败 unknown。新增 ADR-031 第 13 条的最终措辞仍须开发者确认。

阶段 E：Windows 后代停止与三系统共同的回执接收/最终结算，依赖 D。验收 native Job 全空后才发回执，服务先确认可靠接收、执行根退出及根 Job 全空后才释放资源；成功回执到达不提前完成，接收/退出/最终事务任一点崩溃都能恢复且不重复结算，冲突工作与回收在证据未齐时继续受阻。不增加只交付阶段事实。POSIX 只验收既有完整身份/已登记原组和共同次序，脱离后代待办及 cwd 回收扫描另批，不阻塞本批安装。

阶段 F：产品忙文件和测试辅助关闭/观察，依赖 B 的文件边界及 D/E 的生命周期；已核实的手建测试 store 未关闭可先独立修。产品保留删除/恢复计划与未知执行，测试根单独清 READONLY；验收长期占用明确失败、释放后同计划继续、Git 测试对象清理、SQLite 全连接关闭、观察失败保留根且退出 3。Defender 偶发重试只在唯一文件边界验证，不在调用点扩散，不加产品私有树 READONLY 功能。

阶段 G：Host 最终整合与安装前演练，依赖阶段 0、B 至 F 的分别验收及 A 的对应实证。Host 已说明做过设计与独立审查，本轮文档修订仍供 Host 核对，不把审查当 Windows 验证。按最终源码在三系统跑完整 Python 检查，分别保留退出码与 teardown；Windows 11 原生实验单独记录。私有安装上以在跑的模型无关执行演练同契约服务/补丁重启、runtime 留存、回执仅结算一次及根空后的回收；变更契约的首次安装另做空闲围栏、备份与失败回滚，不能把它当同契约保活演练。POSIX 脱离后代未解决的边界要随验收明确记录，日常安装由 Host 后续安排。

本轮已确定会改变事实与存储形式：stateRootIdentity 和 Windows 会话号进入启动证据、身份持久记录及执行/服务交接，必须移动中间号与 CONTRACT_VERSION，并做显式 schema 升级。当前升级基线为 0.49.0、schema 27、没有 pending upgrade；阶段 0 开始同一个待执行升级，后续格式变化在其中完善，fixture generator 提供每种被转换或清理形式的 stock，并比较升级板与新板。若这次 pending upgrade 已在真实板运行，遵 AGENTS.md 消费升级、更新基线与 fixture，后续再开新升级，不能在已运行升级里继续追加。结果暂存/结算语义的调整同时检查表、字段、读取路径与夹具，删除被替代分支及无读者存量；不把“只是 JSON”当作不升级的理由。

首次契约变更安装要求空闲；无法证明历史启动根/会话的旧活记录不补造当前值。被替换的非封存存量显式转换或清理，sealed manifest/回执及摘要绑定文件保持原样，新 seal 明确形式；原始回执暂存与最终结果各自的存储及清理随结束证明一起验收。建议 Python 下限 `>=3.12.4,<3.13` 尚待开发者确认，本文不擅自视为已定。

实现后 owning references 核对 `docs/reference/architecture.md`、`operations.md`、`workers.md` 及 ADR-031；实际证据进 `docs/acceptance/`，Host 在稳定后更新计划/清单与 POSIX 后续批次。本文只改设计，未派发阶段任务，未运行产品测试、系统实验或安装。

## 9. 本轮按 Host 审查决定的改动

归属围栏改为冻结状态根文件身份，补入看板副本事故、脚本阅读边界、完整证据复制与同路径替换验收；POSIX 修复列为第一个实施阶段 0。第 13 条候选措辞及契约/显式升级随之重写，不做“已验证目标”类型重构。

Windows 启动补服务 detached/new-group、允许时 breakaway、不允许时明确失败，以及执行/native 子进程 NO_WINDOW；加入 Local Job 的会话号围栏、提权属主和默认 Job DACL。启动方关闭、SILENT_BREAKAWAY_OK、跨会话和 conhost 均明确未验证并列入阶段 A。

私有边界改为原样搬 ctypes WindowsFileAPI，pywin32 只处理安全查询、ACE 与 SID；新目录用 Python 3.12.4 起的 mode=0700，文件靠继承，ACL 简化为属主集合、DACL 存在和全部 ACE 的类型/主体白名单，加入 OWNER RIGHTS。删除显式凭据/Job 描述符、掩码归一化、完全控制要求、继承范围分析与受保护根的校验要求；Python 下限建议仍交开发者确认。

停止结算删除只交付阶段事实和服务验证，改为 native Job 空后发回执、服务可靠接收后回应、根退出且根 Job 空后最终结算和释放。补源码中收到回执即释放与删除恢复回执的修改点、RPC 死锁避免及幂等验收。

删除产品私有树清 READONLY 的建议，仅测试根保留；补 Defender 实时扫描下热路径实验，确有偶发才在唯一边界作一次有界重试。补 CLI Host 控制能力/提交令牌和 launcher 继承凭据 fd/read_private 入口，以及稳定 runtime 源替换后的 win32security 导入实验。

POSIX 脱离后代与 cwd 回收扫描拆到另批，由开发者定支持条件；本批明确其未解决且不据此阻塞安装。阶段 A 汇集本轮全部未验证项，文档没有把 Host 的修改决定、事故记录或独立审查写成实际系统验证结果。
