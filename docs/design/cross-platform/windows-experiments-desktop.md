# Windows 阶段 A：桌面令牌、窗口、文件换代与跨会话 Job

2026-10-22，源码基线 `bfd61bb6b1761b5aa4b516427d38449cf21eb111`，对应 [windows-ownership.md](windows-ownership.md) 第 7 节补项 1、补项 2、补项 4 的桌面部分、补项 5 及补项 6 的跨会话部分。第一轮准备探针并在 SSH 中预检，第二轮由开发者在桌面运行，Worker 取回输出、核对并收尾；没有修改产品代码或测试，没有启动产品日常服务、调用模型或查询 harness 登录状态。本文记录替身和系统 API 的观察，不是阶段 B/C/D/E 的产品验收。

本次同账户普通与提权桌面令牌之间，提权探针先将本进程 TokenOwner 设为当前用户后，新目录、凭据、SQLite 及其 WAL/SHM 可双向访问和重建；未设置时，普通令牌打不开管理员属主的目录和重建 sidecar。文件换代实验最终选择 `SetFileInformationByHandle(FileRenameInfoEx, POSIX_SEMANTICS | REPLACE_IF_EXISTS)`，普通令牌下连续发布 1000 次、无受控读者、无重试，错误为 0。Defender 实时保护没有开启条件，本次 Defender 实时扫描未测；开发者报告实际开启的实时扫描由联想预装杀毒软件提供。

## 环境、方法与证据边界

机器称“开发者的 Windows 11 测试机”。实测 Windows 11 专业版 x64、版本 `10.0.26200`、本地 NTFS；Windows PowerShell CLI `5.1.26100.9444`、PowerShell CLI `7.6.7`、uv CLI `0.10.8`、Node CLI `v22.12.0`、Git CLI `2.52.0.windows.1`，VS Code app 文件版本 `1.134.0`。私有基础解释器为 CPython `3.12.13`，SQLite `3.50.4`。解释器、锁定依赖、源码、缓存、临时文件和探针输出均在唯一私有实验根内；Python 安装明确使用 `--no-bin --no-registry`。见 `tmp/windows-desktop/01-setup.*`、`02-inventory.*`、`05-install-retry.*`、`25-publication-summary.*`、`34-tool-versions.*` 及各 ACL 日志。

输入没有另给 runId，第一轮沿前几份实验的方法采用当次 attemptId 的前 8 位标识私有根，见 `tmp/windows-desktop/metadata.json`。源码由本检出创建 git bundle，经 SCP 传入并克隆；两端 bundle SHA-256 为 `6136344ae76048cb42340e251ff6e0a5068e90cdbb085dbea0aa2253cd47855c`，远端 HEAD 与基线相同。首次传输、拼接及早期探针的失败输出均保留；下文的桌面结论只采用最终探针的桌面标签，不将旧失败版本的计数并入它。

SSH 是同账户的提权令牌、Windows sessionId 为 0；准备及第二轮 `quser` 均显示该账户的活动 console 会话为 1，桌面探针自行报告的 sessionId 也为 1。这里的 console 是 `quser` 返回的会话名称；开发者通过远程桌面软件操作，不把该名称改写成额外 RDP 会话。普通和管理员动作的用户 SID 一致，以下以 U 表示日志中的当前用户 SID；Administrators 为 `S-1-5-32-544`，SYSTEM 为 `S-1-5-18`，OWNER RIGHTS 为 `S-1-3-4`。

开发者没有逐项另开普通和常驻管理员 PowerShell：前一组步骤由 Host 提供的包装脚本在同一个普通 PowerShell 顺序调用，提示符为 `C:\Windows\system32`；两次管理员动作由该窗口以 `Start-Process -Verb RunAs` 启动提权进程，各确认一次 UAC。Host 转交的报告还包括：第一次包装在 admin1 后等待，开发者按 Ctrl+C，再从 normal2 继续；后续管理员等待改用 `WaitForExit`，admin2 窗口曾处在标题带“选择”的 QuickEdit 状态，按 Esc 后继续。包装使用 `Start-Process -Wait` 等待后代和 QuickEdit 是 Host 提供的候选解释，本轮没有独立确认停顿原因。原始探针的时序与持有者状态另在下一节核对，不能由包装停顿推断探针未完成。

每个编号 `.json` 保存实际 SSH 命令、完整 PowerShell 输入、退出码和解码视图，同名 `.stdout.raw`、`.stderr.raw` 保存原始字节；桌面 `logs/` 保存命令、退出码、stdout/stderr，`acl-control/`、`evidence/` 和 publication 事件流保存逐项事实。Python 输出为 UTF-8，SSH 的 GBK/CLIXML 原样保留。第一轮原始包为 `tmp/windows-desktop/phase1-raw.zip`，691 项，SHA-256 为 `647bb81f4f26db5ce521eae5b1ab6e7d19b78e41643aa6ef03c167019930a795`；第二轮为 `tmp/windows-desktop/phase2/desktop-raw.zip`，1179 项，SHA-256 为 `46abb63790fa2127ab932add274e46e1ebefea2121e6008f9ef2cb84459e2633`，CRC 校验通过，展开在其同目录 `raw/`。第二轮追加的安全状态、身份核对及清理记录是编号 43–47 的独立原始输出，不在较早生成的桌面包内。

开发者的包装方式、QuickEdit、第三方杀毒界面和 MSIX 内置 CLI 输出由 Host 转交，另存 `tmp/windows-desktop/phase2/host-observations.md`；这些报告与探针自行测得的事实分别注明。`derived-summary.json`、`sqlite-owners.json` 是原始数据的索引，不替代原始输出。

## 补项 1：桌面令牌、默认属主及交替使用

三组样本分别采用默认继承父目录、额外添加可继承 Everyone allow ACE 的宽父目录、PowerShell 预建父目录；每一级被测私有目录均另用 `mkdir(mode=0o700)` 创建并读回句柄上的 owner/DACL。默认及宽父目录下的私有子目录均观察到 protected DACL，三个显式 allow ACE 的主体为 SYSTEM、Administrators、OWNER RIGHTS，掩码均为 `2032127`，继承标志为 3；宽父目录自己的 Everyone ACE 没有进入该私有子目录。PowerShell 预建父目录保留其原继承 ACL，随后创建的私有子目录仍得到上述 protected DACL。文件和 SQLite sidecar 则记录为继承 ACE。这里没有改既有对象的 owner/ACL，也没有将 Everyone 加到私有子对象。

| 桌面令牌 | TokenElevation / Type | 初始 TokenOwner | Administrators 组属性 |
| --- | --- | --- | --- |
| 普通动作 normal1/normal2/normal3 | 0 / 3 | U | 16，deny-only=true |
| 提权动作 admin1/admin2 | 1 / 2 | Administrators | 15，deny-only=false |
| 提权动作设置默认属主后 | 1 / 2 | U | 15，deny-only=false |

admin1 和 admin2 的 `SetTokenInformation(TokenOwner)` 均成功，读取前值为 Administrators，请求和读取后值均为 U；这里只改变本次探针进程令牌的默认属主。admin1 在设置前创建的 baseline 私有目录、凭据和数据库属主为 Administrators；它还在普通创建的数据库上关闭原连接、重建 WAL/SHM，数据库保留 U 属主，新 sidecar 属主变为 Administrators。

normal2 对上述 baseline 目录/凭据/数据库的访问被拒，句柄 ACL 查询出现 WinError 5，SQLite 返回 `unable to open database file`；对普通数据库上管理员重建的 WAL/SHM，普通令牌的 ACL 查询同样返回 5，SQLite 访问失败。三组各有 15 条此类负例记录；该阶段总退出码仍为 0，表示探针完成，不能把它读成全部访问成功。

设置默认属主后的 admin-policy、admin-return 私有目录、凭据、数据库和 sidecar 读回 owner 均为 U，DACL 主体仍为 SYSTEM、Administrators、OWNER RIGHTS。normal2 成功读写管理员 policy 组的凭据和 SQLite，关闭持有者后由普通令牌重建 sidecar；admin2 成功访问这批普通重建的 sidecar，再以 U 默认属主重建普通创建的 ordinary-return 数据库 sidecar；normal3 成功访问这批提权重建的 sidecar 及 admin-return 对象，再次正常重建。所有成功的 SQLite 操作在连接仍打开时都确认 WAL 和 SHM 存在并记录它们的 ACL；没有用关闭后已消失的 sidecar 代替跨令牌访问。

| 三组中的阶段 | 默认组时间（当地时间 UTC+8） | 三组最晚结束 | 持有者证据 |
| --- | --- | --- | --- |
| normal1 | 16:49:37–16:49:38 | 16:49:39 | 原普通数据库持有者由 admin1 请求停止 |
| admin1 | 16:49:45–16:49:46 | 16:49:48 | baseline、普通库重建 baseline、policy 持有者留到 normal2 |
| normal2 | 16:57:07–16:57:13 | 16:57:25 | 上述持有者在访问后收到 stop-marker；再创建普通重建及 ordinary-return 持有者 |
| admin2 | 16:57:29–16:57:30 | 16:57:32 | normal2 持有者在访问后停止；再创建 policy 重建及 admin-return 持有者 |
| normal3 | 17:01:09–17:01:10 | 17:01:12 | 最后两类持有者在访问后停止 |

三组共有 24 个桌面 SQLite 持有者，全部 done 记录均为 `reason=stop-marker`、`error=null`，没有 timeout；各阶段的 stop 记录与源代码顺序表明先执行访问，再发送 stop，再读回 done，并观察到对应进程 signaled 或已缺席。因此 admin1 的三组探针在 normal2 的 16:57:07 之前完整结束，所需持有者没有因包装等待而先超时；normal2/admin2/normal3 的跨令牌 sidecar 访问有仍持连接的对应证据。依据为 `phase2/raw/acl-control/acl-desktop/{default,wide,precreated}/{normal1,admin1,normal2,admin2,normal3}.json` 和各 holder 的 ready/stop/done 原始记录。

结论：本次 CPython 3.12.13、同账户桌面普通/提权进程及三个父目录入口中，将提权进程默认属主设为当前用户，使被测目录、凭据、SQLite 和自行创建/重建的 WAL/SHM 可由两种令牌交替使用；原样管理员默认属主的负例实际失败。没有测试独立常驻管理员窗口的手工流程、实际产品 ACL bootstrap、真实默认状态目录入口或阶段 B 的实现，不把这组替身事实写成产品入口已验收。

## 补项 2：服务启动方、窗口与保活边界

普通 PowerShell 和 VS Code 的 PowerShell 终端均运行了服务替身，创建 flags 为 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`（`0x1000208`），命令退出 0。两者启动侧 Python 都报告 `anyJob=true`，本轮没有得到一个已确认不在 Job 中的启动方，因此“无外层 Job 时额外 breakaway 被忽略”未观察。受控外层另有桌面记录：禁止脱离时创建返回 WinError 5；允许 `BREAKAWAY_OK` 时创建成功、版本替身退出 0，但该成功子进程仍报告 `anyJob=true`，不能据此声称脱离了所有其他外层。

独立观察者在启动后约 2 秒、再过 15 秒采样，两入口的服务和 Python 孙进程都以原 PID/FILETIME 存活，指定 Job 里均为三个成员：服务、Python 子进程和 conhost。两次采样里登记的启动 shell 也都仍存活：PowerShell 入口为 PID 32568，VS Code 入口为 PID 32900。开发者报告 READY 后点 X/终端垃圾桶关闭，但日志没有覆盖“启动 shell 已退出而服务继续存活”的重叠时段；观察者随后主动停止替身。因此这次桌面入口的关闭后保活条件未验收，不能将之前 SSH 的保活结果移用到桌面。

开发者报告两个入口最后阶段都出现一个会自行关闭的额外窗口。探针将可见 HWND 归给下面的 Python 子进程；服务及观察者的窗口列表为空。对应 conhost 在 Job 中，但 EnumWindows 的窗口所属 PID 记录是 Python 子进程，不能把弹窗归给 conhost 本身。

| 启动入口 | 可见窗口所属进程 | 创建 FILETIME | HWND（原始十进制） | 同 Job 的 conhost |
| --- | --- | --- | --- | --- |
| 普通 PowerShell | Python timedleaf，PID 31468 | 134371335799657648 | 2690142，visible=true | PID 4140 |
| VS Code PowerShell 终端 | Python timedleaf，PID 34252 | 134371336429643641 | 722542，visible=true | PID 33884 |

服务替身在 detached 创建后，再用 flags=0 启动这个 timedleaf；上述可见 HWND 是该子进程的实际记录。约 17 秒时它们仍可见，观察者随后写停止文件，服务等待子进程退出，再退出并关闭根句柄；最终两入口均取得原名错误 2 加登记服务/子进程原身份缺席。额外窗口的观察不满足本次期望的无弹窗条件，不能据此验收服务及后代的窗口行为。原始依据为 `phase2/raw/evidence/{powershell,vscode}/` 的 launcher、launched、observer、exit、各子进程 stdout/stderr，以及 `logs/launch-service-*.{command,result}.json`。

已有 Host CLI 工具 shell 没有在零模型调用且不触碰登录状态的条件下取得，记“未观察”。没有用受控外层 Job 冒充真实 Host shell，没有额外创建 harness 会话。

## 补项 4：执行/native、conhost、CLI 版本与停止

桌面两组均以 `CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` 创建 execution/native，分配对应 kill-on-close、禁止脱离 Job 后再恢复；没有执行/native 的 detached 对照。native 分别经 cmd、pwsh、Git shell alias 启动 Python 叶子。Git 路径实际登记到 Git 启动器、真正 git、sh 和 Python 叶子；全部成员继续计入根/native，不过滤 conhost。

两组起齐时根 Job 均为 12 个成员，native 均为 10 个成员；根含两个 conhost，native 含其中一个。cmd/pwsh/git 及叶子的 PID、FILETIME、两层成员关系保存在 handoff 快照中，两个快照里所有成员的 EnumWindows 列表均为空。NEW_CONSOLE 参照的 Python PID 32328、FILETIME 134371332794800797、HWND 1639664 则为 visible=true，约 4 秒后自行停止。开发者尚未回报本步骤前段是否有瞬时弹窗以及参照窗口是否亲眼出现；空列表只覆盖采样时刻，不替代完整时段的肉眼观察。

| 桌面组 | native 收尾 | native 最终计数 | execution 退出后根名首次错误 2 | 全部登记身份缺席的最后采样 |
| --- | --- | --- | --- | --- |
| 协议停止 | 停止文件协议后主进程退出 0；无需终止 Job | active=0，members=[] | 0.0814 ms | 1.5381 ms |
| 直接强停替身 | execution 直接调用 TerminateJobObject；native 主进程退出 1 | active=0，members=[] | 0.0593 ms | 4.0492 ms |

第二组主动测强停，没有先走停止文件协议，不能把它写成“协议超时后再强停”的实际 harness 验收。第一组在 native 全空后才让 execution 退出，两组根均登记 12 个完整身份并最终 allAbsent=true。时差零点是观察到 execution 进程句柄 signaled 的时刻，不是内核关闭最后 Job 句柄的精确时刻，也不是普遍终止时限。见 `logs/desktop-native-desktop-native-20261022-170114.stdout.raw` 与 `evidence/desktop-native/`。

在上述 NO_WINDOW/native 环境内，复用产品的候选解析和纯 `--version` 探测路径，另直接运行解析得到的 Node 加 JS；两条路径均输出 Codex CLI `0.163.0`，版本探测关闭证据为 shutdown=true、exit_code=0，直接命令退出 0。完整产品 `discover('codex')` 还会查询 login status，超出冻结范围，因此完整发现记“未观察”，不标为产品 ready。DSH 发现属于另一任务，没有重做。

Host 另转交开发者的一次普通令牌观察：直接运行 Codex app（MSIX 包 `OpenAI.Codex`）内置资源的 `codex.exe --version`，输出 `codex-cli 0.163.0-alpha.11`，exit=0。该数值来自 app 内置 CLI 的版本命令；此次原始字节没有包含在探针原始包中，也没有同样的根/native 快照，故只作为 Host 转交的独立版本观察，不扩大上面的 Node CLI 成员/停止结论。

## 补项 5：文件换代、实时扫描条件与连续发布

桌面 publication 命令在普通令牌下于 17:05:45 左右开始，退出 0。首尾探针自身及 PowerShell 包装都调用 `Get-MpComputerStatus`，均为 `RealTimeProtectionEnabled=false`、`AntivirusEnabled=false`、`AMEngineVersion=0.0.0.0`。开发者报告安全中心显示 Lenovo Anti-Virus powered by Huorong Security 已打开，Microsoft Defender 在界面上处于被动状态，仅有关闭的“定期扫描”开关，没有可开启的 Defender 实时保护；在 Host 的提示处直接回车，没有改设置。第二轮只读 `SecurityCenter2/AntiVirusProduct` 确认注册名称包括该联想杀毒和 Windows Defender，另一次 `Get-MpComputerStatus` 回读 AMRunningMode 为 `Not running`、AMServiceEnabled=false。实时扫描由联想预装杀毒（火绒引擎）提供是开发者的界面报告；本轮没有跟踪该引擎对每次文件操作的实际扫描，也没有测 Defender 实时保护开启条件。见编号 43 及 `phase2/host-observations.md`。

最终探针先试 FileRenameInfoEx，一共九例：目标缺席的首次发布，以及已有目标在无读者、普通 Python 读者、允许删除共享的 Win32 读者、禁止删除共享的独立读者四种条件下的普通/READONLY 目标。临时文件用 WRITE、DELETE、READ_CONTROL 权限并允许 READ/WRITE/DELETE 共享打开，写完整内容并 fsync 后保留该句柄发布；取证读者也允许删除共享，避免取证本身因写者 DELETE 访问权而制造错误。

首次发布、已有普通目标无读者、允许删除共享读者三例直接成功；旧句柄读到完整 OLD，新打开读到完整 NEW，目标的新文件 ID、owner/DACL 和属性来自临时文件，没有合入刻意区分的旧目标 protected DACL。普通 Python 和禁止删除共享读者阻止发布，原始 WinError 为 32；READONLY 无读者/允许删除共享读者返回 5，READONLY 加普通/禁止删除共享读者返回 32。六个失败样本都保留原目标和候选的完整内容及元数据；释放受控读者、在对应一次性样本中清除 READONLY 后，显式再次调用同一 API 成功。清除 READONLY 只是实验恢复步骤，没有据此给产品增加清属性、自动修 ACL 或重试行为。

九例均满足上述各自预期后，运行五个强杀时点，每点三次：写完未 fsync、fsync 后、API 前被杀时，新进程读到完整 OLD 且候选仍在；API 后被杀时读到完整 NEW、候选路径缺席。竞争组的三次也读到完整 OLD，`api_returned=false`，所有 `race_window_proven=false`；本次没有证据证明杀到了 API 内部，不能把这三例写成 API 调用中逐指令覆盖。全部 15 例完整性检查通过，不代表断电耐久性。

最终实验选择为 **FileRenameInfoEx，POSIX_SEMANTICS | REPLACE_IF_EXISTS**。其后连续 1000 次发布，迭代号 0–999，没有受控读者，没有重试，errors=[]，耗时约 1.4773 秒；每次关闭发布写句柄后核对完整内容。最终候选已通过，桌面最终探针没有再运行 ReplaceFileW。第一轮失败版本中保留的 ReplaceFileW 输出属于诊断原始记录，不是最终桌面选型的回退结果。

依据为 `phase2/raw/publication-desktop/events.jsonl` 的 matrix、matrix_summary、crash、crash_summary、hot_iteration、hot_summary 和 summary，以及逐个写者/读者的原始 stdout/stderr、命令和退出码。结果支持在本次系统条件下选用该单一换代 API；Defender 开启条件未测，实际 native/备份/安装 journal 恢复器需要产品代码接入，本轮明确未测，summary 的 full_design_gate_passed=false。没有据此新增热路径重试，也不将替身完整性检查当作阶段 C 后续已验收。

## 补项 6：不同 Windows 会话及重复启动的证据污染

开发者两次运行 session-start，均显示 READY。探针使用同一 label 和同名回执路径，第二次覆盖持有者命令、Job 回执和 stdout，并可能在真实新子进程写入之前读到第一次 child 回执；READY 还只检查回执路径存在。这个探针没有防重复运行，不能将两次 READY 当作两个互不干扰的完整实验。

第二轮开始立即在 SSH 会话 0 对已记录的原名 `Local\HeyMyBuddy-A3-81bea2f1f5cd4908821521e993e675a2` 调用 OpenJobObjectW，返回错误 2，没有创建同名对象。它的桌面持有者为 PID 21688、FILETIME 134371337754519887、sessionId=1。该 Job 的初始快照实际登记 Python PID 31912、FILETIME 134371337755975392，conhost PID 7464、FILETIME 134371337757105115；桌面收到 peer 后在仍持有的原句柄上重查，两个成员的原身份及 inRoot=true 均保持，active=2。随后这个持有者终止自己的 Job，记录 active=0、members=[]。因此确实观察到：会话 0 原名错误 2 时，会话 1 的原 Job/原成员仍活，跨会话错误 2 不能作为停止证据。

第二次回执中的 originalChild 却是第一次的 Python PID 35540、FILETIME 134371336905539650；将它与第二个 Job 比较得到 inRoot=false，是重复运行读入旧回执的结果，不能解释为第二个 Job 的后代逃逸。第二个 Job 的有效比较使用上段初始/重查快照中一致的 PID 31912 和 7464。第一次 Job 的原名、持有者完整身份和独立重查输出被覆盖，未取得，故第一次跨会话过程不验收；没有为补齐它重建同名 Job 或假造身份。

原始依据为 `40-cross-session.*`、`41-live-after-peer.*` 以及 `phase2/raw/evidence/cross-session/`、cross-session-holder 原始文件。Worker 取证后的实时命令行扫描未发现仍运行的 session-holder/timedleaf；后续完整身份核对覆盖两个已知 child（35540、31912）和第二个 holder，均已缺席。两次重复启动造成的缺口保留，收尾另按整个私有根引用及全部可恢复的完整身份核对，不能凭被覆盖的第一份回执声称第一次协议成功。

## 尚未取得的证据

本次没有完成桌面启动 shell 已退出后服务继续存活的重叠采样；两个服务入口实际出现额外子进程窗口，窗口条件不满足。没有取得已确认无 Job 的启动方、已有 Host CLI 工具 shell、完整 Codex 登录发现、DSH 重跑、实际产品默认状态/ACL bootstrap、产品恢复器或 Defender 实时保护开启条件。desktop-native 的采样窗口列表和 NEW_CONSOLE 可见记录已取得，开发者对整段可见性的口头反馈尚未收到。第一次重复启动的跨会话持有者证据被覆盖，不能补写为完成。

这些缺口不以 SSH 预检、命令退出 0、开发者 READY 或 Worker 完成消息替代；后续实施及 Host 验收应使用本文逐项边界。没有安排重启、注销、睡眠、休眠、系统设置或关闭杀毒软件的动作。

## 收尾与最终核对

第二轮完整原始包下载后，按可恢复的 PID、原始创建 FILETIME 和会话登记了 168 条非交互启动 shell 的进程身份，逐一查询均为 absent，remaining=[]；另将八条启动 shell 身份单列为观察对象，没有把人工作业窗口当作可终止的探针后代。见 `tmp/windows-desktop/44-phase2-owned-verification.*`、`phase2/owned-identities.json`、`phase2/observed-launch-shells.json` 和 `phase2/owned-verification.json`。这个计数包括两轮保留的完整探针身份，不能用它补回被覆盖的第一次 holder 身份。

删除前另一次 SSH 扫描返回 privateRootProcessCount=0。随后仅在本轮私有根内清理：先移除一个 Python 版本别名目录重解析链接，不遍历其目标；清除三个本轮 Git pack 叶子的 READONLY 属性，再删除整个实验根，没有放宽 DACL 或改系统设置。清理命令退出 0，rootExistsAfterDelete=false，原始操作清单保存在 `46-cleanup.*`。

最后另开 SSH，在当地时间 2026-10-22 17:24:19 读回 rootExists=false、privateRootProcessCount=0、sshSessionId=0，命令退出 0，见 `47-independent-cleanup-verification.*`。测试机工作目录已不在；相关进程的零计数限定为完整身份登记及私有根映像/命令行引用扫描，不声称枚举了全机所有工作。记录和原始输出已留在本检出的允许范围内，后续需要补桌面证据时须重新准备私有实验根。
