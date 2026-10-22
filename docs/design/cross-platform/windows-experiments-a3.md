# Windows 阶段 A 补充：根 Job、真实 venv 链、停止与目录联接竞态

2026-10-21，源码基线 `f7345410410271b58bd2ff503c28a8eeac83b945`，对应 [windows-ownership.md](windows-ownership.md) 第 7 节补项 3、补项 4 经 SSH 可做的替身实验，以及补项 6 的目录联接竞态。只运行模型无关替身，没有修改产品代码或测试，没有启动产品服务、调用模型、探测 harness 登录或重跑 Codex 产品发现。本文是本机实验事实，不是阶段 D/E 的实现或验收。

本轮最直接的启动链结论为：经过稳定 venv 转发器，真实解释器与其孙进程的显式 `CREATE_BREAKAWAY_FROM_JOB` 创建请求均成功，但创建的启动进程及其真实解释器仍属于指定根 Job；不带标志的后代也均在根中。未观察到后代离开根 Job。基础解释器对照的显式请求返回错误 5。因此不能把 venv 链的“创建成功”解释为“已离开根”，也不能声称 venv 链会拒绝显式请求。本文未改启动链，是否保留 venv 由 Host 结合这组实际成员关系决定。

目录竞态另外观察到：当前目录句柄边界使用的 `FILE_READ_ATTRIBUTES` 加不允许删除共享，未阻止目录被改名并换入 junction；改用 `GENERIC_READ` 的对照才返回错误 32。原组合中下一次打开可以已经到私有根外，随后由最终名称校验拒绝。未观察到被校验流程接受的根外叶子读写，不能将这件事写成“固定句柄已阻止路径替换”。

## 环境、方法与原始记录

机器称“开发者的 Windows 11 测试机”。实测 Windows 11 专业版 x64、`10.0.26200`、本地 NTFS；Windows PowerShell CLI `5.1.26100.9444`、PowerShell CLI `7.6.7`、uv CLI `0.10.8`，私有 CPython `3.12.13`、AMD64、MSC v.1944。SSH 及全部登记进程的 Windows sessionId 均为 0。令牌查询为 `TokenElevation=1`、`TokenElevationType=1`、`TokenOwner=S-1-5-32-544`，组清单为 High Mandatory Level；不代表普通桌面令牌。见 `tmp/windows-a3/01-setup.*`、`02-python.*`、`09-inventory.*`、`08-process.stdout.raw`。

输入未另给 runId，沿前轮方法以 attemptId 的前 8 位标识唯一私有实验根，选择记录在 `tmp/windows-a3/metadata.json`。源码由本检出创建完整 git bundle，经分块 SCP 传入后克隆，远端 HEAD 与基线一致；两端 bundle SHA-256 均为 `e41d78b8ae474ac364e64ce04df931e9e90d6a8f527558009faacb2db0f4e8e2`。第一次顺序传输在第一块成功后中断，改为最多四条并行 SCP，其余块均有成功退出记录，最终合并摘要一致。没有推送或修改本地分支、标签、stash。传输及克隆原始记录见 `transfer-*.json`、`parallel-*.json`、`06-install.*`。

Python 显式安装为 `uv python install 3.12 --no-bin --no-registry`，安装目录、uv 缓存、TEMP、TMP 全部指定在实验根内。先对源码运行 `uv sync --frozen --no-dev`，再调用 `runtime.materialize` 落定稳定运行时；其内部同样使用锁定依赖，返回 `installed=true`、`state=READY`、内容标识 `d8ddb1a096626e3a3354012da4cd8603`。实验 venv 入口为该运行时的 `venv\Scripts\python.exe`，基础解释器为同一次私有安装的 `python.exe`。未执行产品安装或启动日常环境。命令、退出码及完整安装输出见 `06-install.*` 与 `remote-raw/runtime-install.log`。

每个编号 `.json` 保存实际 SSH 命令、完整 PowerShell 输入、退出码、耗时及解码视图；同名 `.stdout.raw`、`.stderr.raw` 保留 stdout/stderr 原始字节。SSH 外壳仍有 GBK 输出及 PowerShell CLIXML progress，解码视图中的替代字符不替代原始字节。Python 输出设置 UTF-8。进程首轮和补轮分别为 `08-process.*`、`10-process-supplement.*`，均退出 0；目录最终矩阵为 `05-junction-v2.*`、`07-junction-v3.*`，均退出 0。`03-junction.*` 是初版探针在路径消失时退出 1 的原始记录，保留且不当作完成矩阵；`04-race-inspect.*` 核对了该轮改名及 junction 实际成功。

模型无关探针在 `tmp/windows-a3/probe.py`、`probe_v1.py`、`junction_probe.py`、`junction_probe_v1.py`。Job 创建额外直接检查 183 并读回 flags；分配、恢复、成员与计数沿用源码 `WindowsAPI`，身份、会话、句柄类型及最终名称使用显式 Win32 ctypes 签名。每个成功创建的后代自行报告真实 PID，再由观察者对指定根句柄调用 `IsProcessInJob`，没有以“当前某个 Job”的 flags 代替根成员关系。私有停止文件仅是本轮替身协议。

远端原始子进程输出、逐项回执、探针及运行时安装日志已取回 `tmp/windows-a3/raw-results.zip`，376 项，SHA-256 为 `d7b5ce115e0e1537c9cdfd0acb9335a1fd1271877cee43244e45e2f11bc9d98a`；展开在 `tmp/windows-a3/remote-raw/`，CRC 检查无坏条目。包中没有源码克隆、venv、缓存或看板。收集、下载记录为 `11-collect.*`、`12-download.json`；`derived-summary.json` 是从原始输出提取并核对的索引，不替代原始记录。

## 全新根 Job 与真实 venv 后代矩阵

每个根使用全新随机 `Local\HeyMyBuddy-A3-<uuid>` 名。`CreateJobObjectW` 前清除 last error，返回后立即读取；12 个根的创建错误均为 0，没有 183。加入任何进程前设置并读回 `LimitFlags=8192`（仅 `KILL_ON_JOB_CLOSE`），`ActiveProcesses=0`，不带 `BREAKAWAY_OK` 或 `SILENT_BREAKAWAY_OK`。四个 native Job 同样先创建、设置后分配，记录在相应 execution stdout。没有对已有对象修改 flags。

执行替身用 `CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`（`0x08000204`）创建，分配到根 Job 后才恢复。真实解释器按名打开不可继承的根句柄并写 holding 回执；此时服务替身核对原 FILETIME、sessionId 与根成员关系，再允许它启动后代。主轮实例的完整身份摘录如下，原始映像位置见 `08-process.stdout.raw` 的 `spawn`、`execution_holds`：

| 入口 | Popen PID / 创建 FILETIME | 真正执行 PID / 创建 FILETIME | sessionId | 两者在根中 |
| --- | --- | --- | --- | --- |
| 稳定 venv，正常退出组 | 34784 / 134370574438924425 | 38008 / 134370574440451785 | 0 | 均 true |
| 基础解释器，正常退出组 | 4724 / 134370574490488940 | 4724 / 134370574490488940 | 0 | true |

真实执行各创建一个普通 actor 孙进程；真实执行和 actor 各自尝试一次显式 breakaway、一次不带标志创建。成功的 venv 叶子再经过转发器，因此分别记录其 Popen PID 和自行报告的真实 PID。两种入口各在正常退出、强杀、服务继续持有句柄、服务重新打开临时句柄四组中运行同一矩阵，合计 32 次创建尝试：

| 创建方与入口 | 显式 breakaway | 不带标志 |
| --- | --- | --- |
| venv 真实执行 | 4 次成功；Popen 与真实解释器根成员均 true | 4 次成功；两者根成员均 true |
| venv 的 actor 孙进程 | 4 次成功；Popen 与真实解释器根成员均 true | 4 次成功；两者根成员均 true |
| 基础解释器执行 | 4 次失败，均 WinError 5 | 4 次成功；根成员 true |
| 基础解释器的 actor 孙进程 | 4 次失败，均 WinError 5 | 4 次成功；根成员 true |

原始依据为两份进程输出的 `handoff_observed.result.attempts`、`actor.attempts`，以及 `remote-raw/evidence/` 中对应 `*-explicit.json`、`*-normal.json` 与子进程 stdout/stderr。首轮 venv 根共登记 13 个成员，基础解释器根为 5 个，包含 conhost。全部成功创建且登记的后代均在根内；没有清理根外成员的分支被触发。

结论：本次 CPython 3.12.13 稳定 venv 链未观察到显式或普通派生能脱离指定根 Job；前轮“显式请求成功”的现象再次出现，但本轮逐个成员检查补上了其仍在根内的事实。未追踪 venv 内部额外 Job 的创建者或完整嵌套链，未把本机结果扩展到其他 Python 版本、其他启动入口或经既有外部进程代起的工作。设计文字若要求“显式请求必被拒绝”，该句对本轮 venv 事实不成立；若判断的是“后代不能离开根”，本轮矩阵未出现反例。

## 创建句柄交接、最后句柄与停止证据

正常退出、强杀和嵌套正常退出六组中，服务替身收到真实执行持有根句柄的回执后关闭创建句柄；之后才放行后代。另起基础解释器观察者按名打开原 Job，核对登记成员均仍活着、FILETIME 与 sessionId 未变、根成员为 true，查询后关闭并退出。观察者完全退出后才触发执行退出或强杀；停止测量期间不再保留原 Job 句柄。全部 12 组的独立观察均通过逐身份核对。

真正执行正常退出前写出 `beforeExitNs` 后 `os._exit(0)`；强杀组对真实执行句柄调用 `TerminateProcess(...,77)`，没有仅杀 venv Popen 转发器。观察者对预先打开的真实执行进程句柄等到 signaled，再按名打开根并轮询各已登记身份。身份缺席为原句柄 signaled，或 `OpenProcess` 返回 87；112 条最终缺席观察中前者 28 条、后者 84 条，没有把错误 5 当缺席。FILETIME 在身份存在时继续比较，未以 PID 或映像名独立证明停止。

内核关闭最后 Job 句柄的精确时间未观察。下表的零点是观察到真实执行句柄 signaled 的时刻；它位于退出请求之后，不能把采样差写成内核终止时限。`exitRequestNs` 到 `executionExitObservedNs` 的间隔也原样记录，故可另给从请求开始的上界。正常退出请求前戳至观察退出为 9.7518、12.1317、11.7450、11.9896 ms，强杀请求至观察退出为 5.5709、4.6886 ms；不将这些调度/文件握手时间归为 Job 开销。

| 组 | 登记成员数 | 观察真实执行退出后首次原名错误 2 / ms | 最后登记身份确认缺席 / ms |
| --- | --- | --- | --- |
| venv 正常退出 | 13 | 0.0477 | 4.2554 |
| venv 强杀 | 13 | 0.4792 | 4.1780 |
| 基础解释器正常退出 | 5 | 0.2156 | 3.6330 |
| 基础解释器强杀 | 5 | 0.0648 | 3.9581 |
| venv 嵌套正常退出 | 12 | 0.3484 | 1.4955 |
| 基础解释器嵌套正常退出 | 8 | 0.0842 | 0.2097 |

每个登记成员单独的 PID、原 FILETIME、sessionId、映像、缺席时差及最后查询结果，均在 `stop_evidence.timing.registered` 与 `identityAbsence`；转发器、真正解释器、actor、叶子、shell 和 conhost 都计入。嵌套场景的 native 成员在根退出之前已清理，其缺席时差是最后复核时刻，不能当作该成员实际退出耗时。六组首次按名打开都已返回错误 2，全部登记身份在 15 秒探针边界内缺席；未观察到名字缺席而原登记成员仍活的反例。

初轮 `venv-temporary`、`base-temporary` 刻意保留创建句柄，真实执行退出后根分别还剩 12、4 个成员；显式终止后均为 0。为核实设计里的临时句柄次序，补轮 `*-temporary-v2` 与 `*-nested-temporary` 均先关闭创建句柄，完成独立接回观察，随后才按名新开服务临时句柄，验证后触发执行退出。四组执行退出后的根计数依次为 venv 12、venv 嵌套 2、基础解释器 4、基础解释器嵌套 1；在同一个临时句柄上 `TerminateJobObject` 后全部采到 `ActiveProcesses=0; members=[]`，再关闭。关闭至首次原名错误 2 分别为 0.0850、0.0760、0.0474、0.0585 ms，全部登记身份最终缺席，原始依据为 `10-process-supplement.stdout.raw`。

结论：本轮支持执行自行长期持有根句柄、服务在确认后关闭创建句柄的次序；另一个进程随后能按原名接回且成员原身份不变。正常退出和强杀后，取得了原名错误 2 加全部登记身份缺席的组合证据；服务持临时句柄时另取得了显式终止后的全空计数。没有模拟实际服务重启、同契约安装、真实 RPC、SQLite marker 写入或全部崩溃顺序，不将替身握手称为产品恢复验收，也不把一次本机测量当作普遍时限。

## NO_WINDOW、cmd/pwsh 与根/native 两层收尾

执行和 native 替身均以 `NO_WINDOW | NEW_PROCESS_GROUP` 创建，先 suspended、分配对应 Job 后恢复；native Job 由执行持有，kill-on-close 且禁止脱离。native 再分别经 `cmd /d /s /c` 与 `pwsh -NoProfile -NonInteractive -Command` 起控制台 Python 叶子，不为 shell 派生额外添加 NO_WINDOW。根和 native 的完整成员来自两次独立 Job 查询；成员计数和列表不是原子快照，保留原始采样差异。

| 主轮入口 | 起齐后的根 / native 成员数 | conhost 的两层归属 | 协议后 native | 收尾后根 |
| --- | --- | --- | --- | --- |
| venv | 12 / 9 | 根含两个，native 含其中一个 | 主 Popen 退出 0；仍有 1 个登记成员，终止后 0 | native 空后仍为 3，执行退出后原名错误 2、12 个原身份缺席 |
| 基础解释器 | 8 / 6 | 根含两个，native 含其中一个 | 主进程退出 0；已为 0，无须终止 | native 空后仍为 2，执行退出后原名错误 2、8 个原身份缺席 |

venv 主轮的残留身份为 PID 38188、FILETIME 134370574531894472，先前成员快照确认为 pwsh；协议后其映像查询返回空，但同 PID/FILETIME 与根/native 成员关系仍可核对。不能将该次残留归因为 conhost。native 主进程收到停止文件后通知两条叶子停止，叶子退出后写协议回执；随后由执行观察 native，仍非空才终止，并确认空后关闭 native 句柄。原始回执见 `remote-raw/evidence/venv-nested.native.protocol`、`base-nested.native.protocol`，两层快照及强停记录见 `08-process.stdout.raw` 的 `handoff_observed`、`native_stopped`。

补轮两种入口的 native 都在协议停止后已空，没有调用 native 终止；根此时分别为 3、2。随后真实执行退出，服务持临时根句柄观察到 2、1，再终止根并采到全空。这组明确补上“native 已空，再看执行退出后的根是否全空”的计数证据，见 `10-process-supplement.stdout.raw`。四次 native 实验均确认 `active=0; members=[]` 后才关其句柄。

结论：本机 SSH 启动形式中，NO_WINDOW 不等于 Job 中没有 conhost；native 和根的计数均包含相应 conhost。协议停止后既观察到已空，也观察到 pwsh 尚未退出，后者可通过终止整个 native Job 收敛到全空。未观察桌面可见窗口、HWND 或桌面启动方退出，不从 SSH 会话推断窗口体验。没有运行 Codex CLI 0.163.0 产品发现或其版本命令；任务给出的独立修复复跑 ready 结果属于另一微任务，本记录没有复核或归因。

## 目录联接与叶子重解析竞态

在实验根下给每个样本建立独立的 `private` 与兄弟 `outside`，outside 仍在本轮可删除的实验根内，包含哨兵和与内部同名的完整子树。独立基础解释器在固定时点把祖先或叶子改名，再用 `cmd /c mklink /J` 换成目录联接；普通文件叶子另用 `os.symlink`。每个打开使用 `FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS`（`0x02200000`），共享仅 READ/WRITE（3），不含 DELETE。逐句柄读回真实属性和最终名称，发现重解析属性或最终名越出指定 private 即拒绝，不继续读取叶子内容。

初版在固定后的祖先被改名并换入 junction 时，outside 未提供下一层，故下一次 `CreateFileW` 返回 WinError 2、探针退出 1；后续检查确认改名和 `mklink /J` 均成功，见 `03-junction.*`、`04-race-inspect.*`。第二版补全 outside 子树，分别把各级句柄 access 设为 `FILE_READ_ATTRIBUTES=128` 与 `GENERIC_READ=0x80000000`，12 例完整跑完。第三版 6 例采用当前目录/普通文件边界的组合：祖先用 attributes、文件叶子用 GENERIC_READ。第二版的 attributes 文件叶子只是访问权限对照，不冒充实际读文件入口。均未运行尚未实现的 ACL bootstrap。

| 第三版时点 | 替换操作观察 | 下一步与错误码 | 是否跨出指定 private |
| --- | --- | --- | --- |
| 祖先尚未固定，父已固定 | 改名成功，junction 创建退出 0 | 打开该祖先成功，属性 1040，重解析校验拒绝；无 Win32 失败码 | 打开的是 junction 本身，未继续穿过它 |
| 祖先 attributes 句柄已固定 | 改名成功，junction 创建退出 0 | 下一层打开成功、属性 16，但最终名为 outside 下的目录；最终名称校验拒绝 | 已打开根外目录句柄；未接受后续叶子 |
| 目录叶子尚未固定，父已固定 | 改名成功，junction 创建退出 0 | 打开目录叶子成功，属性 1040，重解析校验拒绝 | 未继续穿过该 junction |
| 目录叶子 attributes 句柄已固定 | 改名成功，junction 创建退出 0 | 普通文件打开成功、属性 32，但最终名为 outside 下的文件；最终名称校验拒绝 | 已打开根外文件句柄；未读取其内容 |
| 普通文件叶子打开前 | 改名成功，文件 symlink 创建成功 | 打开叶子成功，重解析属性校验拒绝 | 未读取 symlink 目标内容 |
| 普通文件叶子 GENERIC_READ 句柄已固定 | 改名失败，WinError 32；未创建 symlink | 原句柄仍对应内部普通文件 | 未观察跨根 |

第二版 GENERIC_READ 对照中，已固定的祖先、目录叶子与普通文件叶子三次改名全部失败，均为 WinError 32；固定之前的三次替换成功，但随后均在重解析校验拒绝。attributes 对照的六次改名均成功，含已经打开的普通文件叶子；该最后一例保留的是原文件句柄，后续没有再次打开、读写路径，不能把其 `accepted=true` 当作换入 symlink 后的路径读取安全证明。全部样本的 outside 哨兵仍为 `OUTSIDE_UNCHANGED`、`OUTSIDE_FILE_UNCHANGED`；校验流程未读取叶子内容，未观察到接受后的根外数据读写。

完整结果分别为 `05-junction-v2.stdout.raw`、`07-junction-v3.stdout.raw`，每个 actor 的实际 rename 结果、mklink 命令/退出码、原始 stdout/stderr 及文件 symlink 错误码在 `remote-raw/evidence/race-v2-*`、`race-v3-*`。重解析或最终名称拒绝由探针在 API 成功后作出，`winerror=null`，没有虚构为 Win32 错误 5。所有打开句柄在样本结束的 finally 内按逆序关闭。

结论：本机固定时点的竞态证实 flags 加“不允许删除共享”还须连同 desired access 一起描述；当前 attributes 祖先句柄不足以阻止改名。最终名称校验在本次完整目标树反例中拒绝了已经跨根的打开，不能只靠叶子的 OPEN_REPARSE_POINT 断言不会穿过已替换祖先。GENERIC_READ 对照实际阻止了固定后的替换，是否采用及所需访问权限、其他拒绝情形由 Host 后续设计决定；本任务没有修改实现。未测任意频率并发攻击、挂载点原地改写、所有重解析标签、ACL 组合或完整产品读写入口。

## 清理与未观察边界

证据取回并核对摘要、CRC 后，远端按本轮可执行文件路径或命令行与实验根的关系枚举，输出 `RELATED PROCESSES BEFORE CLEANUP 0`；随后只对本轮工作根执行递归删除，返回 `WORK ROOT EXISTS False`，退出 0。新开独立 SSH 再核对工作根不存在且相关进程为 0。命令、退出码及 stdout/stderr 在 `13-cleanup.*`、`14-cleanup-verify.*`。没有启动或重启日常服务，没有接触日常看板、harness 配置和登录状态，没有更改系统设置、PATH、注册表、计划任务、Defender 或防火墙。

桌面普通/管理员 PowerShell 交替、从 PowerShell/VS Code/Host shell 启动与关窗口、可见窗口、跨 Windows 会话、Defender 开启条件及新的 ACL bootstrap 均未观察，也不在本轮范围。后续由开发者在普通与管理员桌面分别运行同一探针，登记令牌与 sessionId，观察窗口与启动方退出后原身份/Job；若桌面和 SSH sessionId 不同，再相互按原 Local 名打开并回原会话核对成员。Defender 应在本来满足实时扫描条件的环境首尾读回状态，再执行另行安排的热路径，不用本轮结果代替。上述操作应各用新私有根、取回原始输出并收尾；本轮没有通过重启、注销或改变安全设置取得这些条件。

本次未运行仓库完整检查套件；变更仅为本实验记录和忽略的 tmp 证据。交付核对覆盖两端摘要、压缩包 CRC、12 组进程停止证据、32 次派生矩阵、18 例完整目录矩阵、远端独立清理确认及 Git 写入范围，不将它们称为产品测试、安装或 Host 验收。
