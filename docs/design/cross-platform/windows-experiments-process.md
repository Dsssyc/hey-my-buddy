# Windows 阶段 A：进程、Job、会话、身份与真实 CLI 实验

2026-10-21，源码基线 `d5c908aaaed3cfd66c20822222570a1e267ff9f2`，对应 [windows-ownership.md](windows-ownership.md) 第 4 节及第 8 节阶段 A 的启动/Job、身份实验。只运行模型无关探针和 CLI 版本命令，没有修改产品代码，没有发起真实 harness 回合，没有安装或重启日常服务。实验已得到结果。续轮按开发者授权用 uv 卸载了本次私有 Python，全局入口也已消失；但核对期间观察到原先指向另一私有根的注册项消失，第 3 步未通过，已按指令停止，远端工作目录仍保留，详见最后一节。

最需要回到设计处理的结果是：无 kill-on-close 的 Job 在创建者退出、最后句柄关闭后，原成员仍存活，但原名 `OpenJobObjectW` 返回错误码 2；同名 `CreateJobObjectW` 随后成功创建空 Job，原成员不属于新 Job。这否定了第 4 节“所有服务句柄关闭后仍因关联进程存活而可按名接回”的完整假设，也表明在这种情况下不能把原名不存在当作原工作已经结束。

## 环境、方法与原始记录

机器称“开发者的 Windows 11 测试机”。实测为 Windows 11 专业版 x64、版本 `10.0.26200`、固定 NTFS 卷；显式探针外壳为 Windows PowerShell CLI `5.1.26100.9444`，uv CLI `0.10.8`，Node CLI `v22.12.0`。uv 下载的基础解释器为 CPython `3.12.13`、AMD64、MSC v.1944；本次 Python 进程的令牌 `TokenElevation=1`，结果不代表非提权用户。原始记录为 `tmp/windows-process/00-inventory.json`、`18-join-setup.json`、`24-platform.json` 和 `27-core-base.json`。

唯一远端工作根为 `%USERPROFILE%\hmb-work\64ee8084`。输入没有另给名为 runId 的值，因此使用本次 execution attemptId 的前 8 位标识这个私有根，此选择记在 `tmp/windows-process/metadata.json`。源码以 `git bundle` 打包、经 SCP 分块传输、在远端合并后克隆；本机与远端 bundle 的 SHA-256 同为 `d49c120d8f72ac7ce201fb67a8ca9233dce429eb52105db001a0111cdf3d8edb`，克隆 HEAD 与基线相同。依赖使用 `uv sync --frozen --python 3.12`，Python 安装、uv 缓存、venv、TEMP 和 TMP 均指定到工作根内；显式安装 Python 的 bin/registry 默认行为遗漏另见收尾。

每个编号 `.json` 记录实际 SSH 命令、完整 PowerShell 输入、退出码、耗时、stdout 和 stderr；同名 `.stdout.bin`、`.stderr.bin` 保留原始字节。实验使用 `tmp/windows-process/probe.py`、`supplement.py`、`ssh_lifetime.py` 和 `cli_probe.py`；Job 创建/分配/恢复/成员查询沿用产品 `WindowsAPI`，仅在实验中通过 ctypes 设置不同 flags、读取会话号和目录身份。基础解释器实验通过显式 `PYTHONPATH` 读取同一源码与私有 venv 的依赖，不改产品文件。

初次大文件 SCP 中途断开，第一次克隆因 bundle 尚不完整而失败；Python 下载的 SSH 连接也曾断开。后续分块传输成功，完整性校验和最终克隆成功，不能把早期失败当作成功安装的证据。保留 `01-setup.json`、`01b-transfer-inspect.json`、`02-python-install.json`、`04-recovery.json`、`transfer-summary.txt`、`transfer-retry-summary.txt`、各 `bundle.part*.transfer.json` 和最终 `18-join-setup.json`。

原始远端回执与私有服务 stdout/stderr 已回收到 `tmp/windows-process/raw-results.zip`，143 项，SHA-256 为 `7b7e4e541e6efc0f644583019d567c25c4d74a9e34063ad703d2a7c52b7a475d`；展开在 `tmp/windows-process/remote-raw/`。私有看板、控制 token、依赖树没有收入这个压缩包。局部摘录以下均省略实际文件系统路径；完整原始输出保留原样供 Host 检查。

## 先隔离 venv 启动链的影响

先用私有 venv 的 `Scripts\python.exe` 运行，观察到创建者记录的 `Popen.pid=860` 与脚本内 `pid=28840` 不同，脚本查询到当前 Job flags 为 `12288`（`0x3000`），根 Job 成员还包含 conhost。这一轮的“禁止脱离”请求返回成功，而基础解释器的相同实验返回错误码 5。记录为 `tmp/windows-process/22-core.json` 和 `23-outer.json`，退出码均为 0；0 只表示探针完成，不表示假设成立。

随后直接使用 uv 私有安装内的基础 `python.exe` 重做，`Popen.pid` 与脚本 PID 相同，脚本查询到的当前 Job flags 与显式根/native Job 设置一致。后面的系统结论以 `27-core-base.json`、`28-outer-base.json` 和补充复验为依据，前一轮作为启动链差异保留；没有追踪额外 Job 的具体创建者，也没有修改 Python 或 venv 启动器。该差异证实第 8 节要求记录实际启动链和 Job flags 的必要性，无法据前一轮给第 4 节的根 Job 生存期作肯定判断。

## 命名 Job 的最后句柄与同名创建

创建者建立 flags 为 0 的随机 `Local\` 命名 Job，以 suspended 启动成员、分配到 Job 后恢复；成员不继承 Job 句柄，读取自身归属时短暂打开的句柄也在回执发布前关闭。创建者关闭自己的 Job 句柄并退出，观察者在确认创建者退出后再按名打开。摘录：`creatorExit=0; closeHandle=true; child.pid=33020; child.startedAt=134370442136262389; childNow.startedAt=134370442136262389; openSuccess=false`，成员此前确认 `rootMembership=true`、根 Job `active=2`。记录为 `tmp/windows-process/27-core-base.json` 的 `lifetime_after_creator_exit`；成员经私有停止文件退出，随后身份缺席。

针对这一反例，再用全新名字、直接调用 Win32 API 复验。摘录：`creatorExit=0; child.pid=33492; child.startedAt=134370444224205465; childNow.startedAt=134370444224205465; openSuccess=false; openError=2`。紧接着同名创建得到 `createSuccess=true; createError=0; newJob.active=0; originalChildInNewJob.value=false`，同时原成员仍有原创建时间。原始记录为 `tmp/windows-process/30-lifetime-recheck.json`，退出码 0；复验成员也经私有停止文件退出，最终返回身份缺席错误码 87。

结论：本机上最后句柄关闭没有立即结束原成员，但名字已不可用于重新取得原 Job，且可以绑定到另一个新 Job。否定第 4 节关于“无句柄仍可按名接回”的假设；不能由按名打开的错误码 2 或随后新建空 Job 推断原工作结束。这里没有句柄可继续查询原 Job 的内部存续状态，故不把“原成员仍活”扩写成对不可访问内核对象状态的额外断言。

另在原 Job 句柄仍打开时创建相同名字。摘录：`handleNonNull=true; error=183; originalFlags.flags=0`，原始记录为 `tmp/windows-process/27-core-base.json` 的 `duplicate_while_original_handle_open`。结论：Win32 返回有效句柄并以 `ERROR_ALREADY_EXISTS` 告知对象已存在，不会仅因重名自动拒绝。支持第 4 节需要在创建边界明确拒绝已有对象的要求；本次源码 `WindowsAPI.create_job` 未检查 183，实验没有替产品增加这一校验。

## 嵌套、禁止脱离与孙进程

在 flags 为 0 的根 Job 内运行控制器，由控制器创建 `KILL_ON_JOB_CLOSE=0x2000` 的 native Job，再 suspended 启动、分配和恢复成员。摘录：`childInRoot.value=true; childInNative.value=true; root.active=4; native.active=2`；控制器关闭最后 native Job 句柄退出后，native 成员身份缺席，`nativeOpen=false`，根 Job 尚有一个 conhost，继续 `TerminateJobObject` 后才观察到根 `active=0; members=[]`。记录为 `tmp/windows-process/27-core-base.json` 的 `nested_live`、`nested_holder_exit`、`nested_cleanup`。

结论：本机允许这组嵌套，native 成员同时属于根 Job，native Job 的最后句柄关闭会结束该成员；控制器退出时根 Job 不一定立即全空。证实第 4 节的根/native 嵌套方向及保留 native kill-on-close 的行为边界；也支持不能以根进程退出替代根 Job 全空的要求。不能由名字消失单独判断 native Job 已空，本例另外有成员身份缺席及最终根 Job 全空的证据。

在不设 breakaway 标志的根 Job 中，成员请求 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`。摘录：`rootFlags.flags=0; request.success=false; request.winerror=5`。记录为 `tmp/windows-process/27-core-base.json` 的 `breakaway_disallowed`，随后根 Job 强停并观察到 0 个活成员。证实第 4 节“不允许脱离时应明确失败”的本机行为，不能用带额外 Job 的 venv 首轮成功结果否定这个复验。

分别经 `cmd /d /c`、Windows PowerShell CLI、`uv run --offline --no-project`、Node CLI `child_process.spawn` 启动 Python 孙进程；每条链的控制器先被分配到 flags 为 0 的根 Job。四条摘录均为 `leafInRoot.ok=true; leafInRoot.value=true; root.active=5`；每条链结束时分别观察到 `active=0; members=[]`。记录为 `tmp/windows-process/27-core-base.json` 的四组 `grandchild`、`grandchild_cleanup`，具体 argv 和 PID 列表在原始输出中。

结论：这四条实际启动路径的孙进程都在根 Job 内，证实第 4 节所列 shell/uv/Node 继承假设在本次调用形式下成立。Node 路径的叶子查询到另一组当前 Job flags，但明确的根 Job 成员检查仍为 true；不将“当前 Job”查询等同于完整嵌套链枚举，也不将这四例推广到所有 harness 启动方式。

## conhost、控制台与停止

用 `CREATE_NEW_CONSOLE` 启动后再分配根 Job，观察到 `root.active=2`，成员为 Python 和 conhost，conhost 的显式根 Job 成员检查为 true。再从已经属于根 Job 的控制器内部起 new-console 子进程，观察到 `root.active=4`，两个 Python 和两个 conhost 均列在根 Job 中。记录为 `tmp/windows-process/27-core-base.json` 的 `new_console_accounting`、`new_console_inherited_job`，两组收尾均为 0 个活成员；全机同时出现但 `inRoot=false` 的其他 conhost 没有计作本任务成员或被停止。

`CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` 的成员报告 `consoleWindow=0`，根 Job 却仍列出 Python 与 conhost，`active=2`。父进程调用 `GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid)` 返回成功、错误码 0，0.5 秒后成员仍活着。记录为 `tmp/windows-process/27-core-base.json` 的 `no_console_ctrl_break`。结论：本机不能用没有控制台 HWND 或 NO_WINDOW 推断没有 conhost，也不能把控制台事件 API 成功当作软停完成；支持第 4 节对 conhost 计数和无控制台停止的验证要求，否定把计数中的 conhost 忽略掉的做法。

同一个无控制台替身收到私有停止文件后写出 `orderly=true`，父进程等待得到退出码 0，根 Job `active=0; members=[]`。另起一个相同替身，调用 `TerminateJobObject` 后等待根 Job 全空，得到 `childExit=1; orderlyReceiptPresent=false; after.active=0`。记录为 `tmp/windows-process/27-core-base.json` 的 `no_console_orderly_file_protocol` 和 `no_console_TerminateJobObject`。结论：本机的无控制台进程可以通过显式协议有序停止，也可以由 Job 强停；证实第 4 节区分协议停止和 Job 强停的设计方向。此处协议是模型无关文件替身，未验证产品 ExecutionLive 或任何真实 harness 的 interrupt/close。

成员列表与 ActiveProcesses 是先后两次查询，原始输出保留它们在进程退出窗口中的瞬时差异；没有把一次查询中正在退出的进程或数目差异改写成稳定状态。结束判断使用后续的空 Job、精确进程身份或明确不存在，不使用终止 API 的成功布尔值替代这些观察。

## SSH 外层 Job 与模拟 Host harness

首先在 SSH 命令中查询自身：`IsProcessInJob=true`，当前 Job extended limits 的 flags 为 `10240`（`0x2800`，kill-on-close 加显式 breakaway 许可）。原始记录为 `tmp/windows-process/10-session-holder.json` 和 `27-core-base.json`。成功 API 调用后原始 GetLastError 偶有旧值 203，不能解释为调用失败。结论：这台测试机的 SSH 命令确实位于一个 Job 中；这是第 4 节“外层 Job 可能影响服务生存期”的实际例子，只读取了可查询的当前 Job，未枚举全部外层 Job 或修改 SSH 配置。

通过基础解释器从 SSH 启动服务替身及一个孙进程，关闭启动连接后用另一连接按 PID 加原始创建 FILETIME 检查，两次采样相隔 2 秒。仅 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`（`0x208`）时，两者均返回进程不存在错误码 87；加 `CREATE_BREAKAWAY_FROM_JOB`（`0x1000208`）时，两者均保持原创建时间，随后通过停止文件有序退出，记录 `serverOrderly=true; childExit=0`，两者最终身份缺席。原始记录为 `tmp/windows-process/14-ssh-detached-launch.json`、`15-ssh-detached-observe.json`、`16-ssh-breakaway-launch.json`、`17-ssh-breakaway-observe.json`。

结论：本机仅 detached/new-group 不足以活过 SSH 连接，加显式 breakaway 的替身在这次观察中能继续运行，证实第 4 节要求处理外层 Job 的方向。存活者仍报告 `anyJob=true`，不能宣称已脱离所有 Job，更不能以本例替代 PowerShell 桌面、VS Code 或真实 Host harness CLI 的验证。

再建立自己的命名外层 Job，模拟模型无关 Host harness 的 kill-on-close 边界，不修改 SSH 自己的 Job。外层持有者先 suspended 创建“命令进程”、分配到自建外层 Job、恢复；命令进程确认自己属于该 Job 后启动服务替身及孙进程。分别设置 `0x2000`（不允许脱离）和 `0x3000`（另加 SILENT_BREAKAWAY_OK），测试服务是否显式请求 breakaway。第一轮在 SSH 仍打开时关闭自建 Job 持有者，第二轮每种组合另开 SSH 连接、运行后关闭该连接，再由新连接观察。记录分别为 `tmp/windows-process/28-outer-base.json` 和八份 `33-0-launch.json` 至 `33-3-observe.json`。

| 自建外层 flags | 服务创建 flags | 创建结果 | 仅自建 Job 持有者退出后 | SSH 连接也关闭后 |
| --- | --- | --- | --- | --- |
| `0x2000` | `0x208` | 成功 | 服务与孙进程身份缺席 | 服务与孙进程身份缺席 |
| `0x2000` | `0x1000208` | 失败，WinError 5 | 未创建 | 未创建 |
| `0x3000` | `0x208` | 成功 | 服务与孙进程以原创建时间存活 | 服务与孙进程身份缺席 |
| `0x3000` | `0x1000208` | 成功 | 服务与孙进程以原创建时间存活 | 服务与孙进程以原创建时间存活 |

摘录：`deny-explicit.created=false; winerror=5`；两个 silent 组合在自建持有者退出前均有 `membersBeforeClose.active=0`，而服务和孙进程仍活着；第二轮 `silent-detached` 的两者随后都缺席，`silent-explicit` 的两者仍保持原创建 FILETIME。所有存活组合最终均以停止文件退出，后续身份缺席。结论：SILENT_BREAKAWAY_OK 在自建边界允许后代脱离，但不能据此保证活过另一个 SSH 外层边界；证实第 4 节必须核对实际嵌套和启动方退出的要求。没有运行真实 Host harness，不声称已覆盖其 Job 策略。

## Windows 会话与 Local 名字

同时建立两条独立 SSH 连接。第一条以 `ProcessIdToSessionId` 取得会话号并持有一个无成员的随机 `Local\` Job，第二条查询自己的会话号并按该名字打开，之后通知第一条关闭句柄。摘录：`creator.pid=17808; creator.sessionId=0; openerPid=32176; sessionId=0; openSuccess=true; openError=0; closeSuccess=true`。记录为 `tmp/windows-process/10-session-holder.json`、`11-session-opener.json`，两者退出码均为 0。

结论：这两条 SSH 连接属于同一个 Windows 会话 0，名字可见性成立；没有取得不同会话的反例或正例。这里只记录 `ProcessIdToSessionId` 的会话号，不把两条连接数当作不同会话数，也不把它替代令牌的登录标识。证实第 4 节同会话定位在此例可用，无法判断不同会话隔离、登录切换和跨会话接回，仍须按设计保持未验证。

## 目录身份与 BootIdentifier

以目录句柄调用 `GetFileInformationByHandleEx(FileIdInfo)`，句柄使用备份语义和读/写/删除共享；记录同一 NTFS 目录创建后、改名后、复制出的目录、原路径换成副本后的卷序列号和 128 位文件 ID。摘录：卷序列号均为 `d4521c35521c1f34`；原目录和改名后的 ID 均为 `68350200000019000000000000000000`，副本及换入原路径的 ID 均为 `3037020000000d000000000000000000`，移到另一名字的原目录仍保持原 ID。记录为 `tmp/windows-process/21-identities.json`；独立 PowerShell/句柄复验见 `12-directory-identity.json`，关系相同，两者退出码均为 0。

结论：本次 NTFS 观察中改名保持身份，复制和同路径换副本改变身份。证实第 8 节身份实验及第 7 节状态根围栏所依赖的这一文件身份关系；没有验证重建后的长期 ID 重用、其他文件系统、真实副本服务的控制围栏或任何持久编码实现。

直接调用产品 `process_identity.boot_id()` 五次，每次间隔约 0.3 秒。摘录：五个值均为 `windows:ce8dfb43-9a37-11f0-b652-b2a898c82662`，`distinct=1`。记录为 `tmp/windows-process/21-identities.json` 的 `boot_id_repeated`。结论：同一次开机、这个短时间窗口中稳定，证实第 8 节相应的同次开机观察；跨重启、快速启动、睡眠、休眠和注销/登录均未观察，不能据此确定这些事件的标识语义。

## 私有开发态服务与真实 CLI

设置 `HMB_DEV_SOURCE=1`、显式私有 `HMB_STATE_DIR` 和 `HMB_RUNTIME_ROOT`，从克隆源码启动私有 daemon；探针持有一个 kill-on-close Job 作为最终收尾边界。服务 ping 摘录：`status=ok; protocol=2; contractVersion=0.50.0; schemaVersion=27; pid=34772; persistenceError=null`。记录为 `tmp/windows-process/26-cli.json`，服务 stdout/stderr 位于 `remote-raw/cli-v1/daemon.stdout`、`daemon.stderr`。venv 外层 Popen PID 为 4508，与服务实际 PID 不同；没有把外层 PID 单独当作服务身份。

在私有服务运行期间，调用产品发现函数取得 Codex CLI、DSH CLI 候选快照和有界发现结果，记录 subprocess 审计事件；服务健康表及其产品投影另以只读方式读取。没有强制刷新服务的完整健康流程：该流程还会读取账户和模型目录，超出本次纯发现/版本范围。因此以下“发现结果”与“服务中尚未检查的健康状态”分别记录，没有把两者混写成服务已经采用的可用命令。

Codex CLI 候选为 `<Node 当前版本入口>\codex.cmd`，解析后的 JS 为 `<Node 版本目录>\node_modules\@openai\codex\bin\codex.js`。审计记录实际启动了 `[<Node 当前版本入口>\node.exe, --version]` 及 `[<Node 当前版本入口>\node.exe, <上述 codex.js>, --version]`，随后有探测收尾的 taskkill；发现最终返回 `status=unhealthy; available=false; command=[]; executable=null; version=null; reason.code=harness.shutdown-unverified`。记录为 `tmp/windows-process/26-cli.json` 的 `native_launch` 和 Codex `discovery`。结论：本候选 `.cmd` 能被产品转换为 Node 加 JS，但本轮产品未确认探测关闭，不能标为 ready；也未进入登录状态探测或任何回合。

DSH CLI 候选为 `<DSH 安装目录>\resources\runtime\cli\bin\dsh.cmd`。产品发现返回 `status=unhealthy; available=false; command=[]; executable=null; version=null; reason.code=harness.entry-unsupported`。补充只读解析表明该 shim 设置 `ELECTRON_RUN_AS_NODE=1`，经 `DeepSeek Harness.exe --expose-internals` 执行 app.asar 内的 CLI JS，产品 `_script_uses_node` 返回 `entry-unsupported`。记录为 `tmp/windows-process/26-cli.json` 和 `31-cli-versions.json`。结论：本机这一形式的 DSH CLI shim 未被当前产品接受，没有证据表明它已经被转换成可用 Node/JS command。

以下为前述 `31-cli-versions.json` 中 `shimContent` 保存的 `dsh.cmd` 完整文本，另存 `tmp/windows-process/dsh.cmd.observed.txt`，没有补写或省略命令。

```bat
@echo off
setlocal DisableDelayedExpansion
set "ELECTRON_RUN_AS_NODE=1"
"%~dp0..\..\..\..\DeepSeek Harness.exe" --expose-internals "%~dp0..\..\..\app.asar\dsh\node_modules\@deepseek-ai\dsh-desktop-host\lib\cli.js" %*
exit /b %errorlevel%
```

另外用自己的受控 Job 只运行版本命令，以便区分安装是否可执行和产品发现是否可用。Codex 使用解析出的 Node 加 JS，退出 0，stdout 为 `codex-cli 0.163.0`，命令完成后 Job 已空。DSH 经 `cmd /d /c <dsh.cmd> --version`，退出 0，stdout 为 `0.2.1-rc.2`；命令结束后还有一个 conhost，终止该私有 Job 后观察到 `active=0`。记录为 `tmp/windows-process/31-cli-versions.json`，stdout/stderr 另有 Base64 原始字节。它证明 Codex CLI 0.163.0 与桌面应用所带 DSH CLI 0.2.1-rc.2 的纯版本命令能启动，不代表对应 app 版本，也不改变前述产品发现失败的结果。

服务健康表未有这两个 harness 的行，产品健康投影均为 `status=unknown; revision=0; reason.code=harness.not-checked`。服务停止返回 `stopped=true; cancelledQueued=0; cancelRequested=0; unresolvedAttempts=[]`；等待外层进程退出码 0 后，私有 Job 尚有一个 conhost，最后强停该 Job 并再次查询得到 `active=0; members=[]`。记录为 `tmp/windows-process/26-cli.json` 的 `private_service_health_projection`、`private_service_stop`、`private_service_final_cleanup`。该补充 CLI 实验无法单独证实第 4 节的产品服务独立生存期或真实 harness 的工作能力；这里只证明私有开发态服务启动、读取、停止与本次探测的物理收尾。

## 交开发者安排的实验

桌面启动方：在独立私有根中分别从 PowerShell 窗口、VS Code 终端及真实 Host harness CLI 的工具 shell 启动模型无关服务替身，记录 Python/工具版本、实际 PID 与创建 FILETIME、会话号、Job flags 和成员；用独立观察方确认启动成功后关闭启动窗口或工具 shell，再核对服务及孙进程的原身份。真实 Host 工具 shell 必须由开发者另行安排，仍只启动替身，不发模型请求。本次模拟 Job 的结果不能代替它。

窗口可见性：在交互式桌面分别以 `CREATE_NEW_CONSOLE`、`CREATE_NO_WINDOW`、detached/new-group 启动相同替身和孙进程，记录是否出现窗口、HWND、conhost 及 ActiveProcesses，再完成协议停止和 Job 强停。SSH 中的 HWND 或无 HWND 不能证明桌面是否弹窗，此项未观察。

不同 Windows 会话：同一账户在两个确实不同的 Windows 会话中，各先用 `ProcessIdToSessionId` 记录号码；一边持有随机 `Local\` Job，另一边按名字打开并记录返回值和错误码，再回到创建方确认原 Job/成员仍在。不要用两条同为会话 0 的 SSH 连接代替；不要将另一会话按名打不开判作原 Job 已停。记录真实会话来源及提权状态，不操作日常执行。

完整重启与快速启动：先记录 `boot_id()`、会话号和一个私有替身的完整创建身份，再分别由开发者安排“重启”及当前配置下的关机/开机，回来重复取值并检查原身份是否仍存在；记录快速启动的实际启用状态，不能只记录一次电源按钮动作。是否需要改变快速启动设置由开发者单独决定，本次没有修改设置或执行电源操作。

睡眠与休眠：在同一私有替身活着时记录 bootId、创建 FILETIME、Job 和会话，分别进入睡眠、恢复和休眠、恢复，重复读取并比较真实值及身份。注销/重新登录也应独立记录相同信息。标识变化与进程是否存活分别记载，无法读取或检查时保留 unknown，不预设哪一种事件一定改变 BootIdentifier。

## 收尾证据、约束偏差与 Host 所需处理

前一轮的进程收尾有三层证据：各受控 Job 终止后再次查询全空；所有存活服务替身经过停止文件后原身份缺席；最后按原始输出建立了 95 个精确身份及 162 个观察 PID 的登记表，并逐一核对。`tmp/windows-process/36-cleanup-check.json` 返回 `remainingRegistered=[]; privateRootLiveProcesses=[]`，162 个 PID 在当时的核对中全部为不存在，未触发任何追加强杀。环境扫描有 22 个其他进程拒绝读取环境，这不是全机无进程的证明；本次已登记进程没有 unknown。随后独立只读命令行扫描也未找到引用本工作根的活进程，记录为 `38-final-inspection.json` 和 `39-launcher-reference.json`。

前一轮没有删除目录，因为初次显式 `uv python install 3.12` 虽把安装目录放进私有根，却遗漏了 `--no-bin --no-registry`。当时 `%USERPROFILE%\.local\bin\python3.12.exe` 为 46,592 字节的普通启动器，UTF-8 内容引用 `%USERPROFILE%\hmb-work\64ee8084\python\cpython-3.12-windows-x86_64-none\python.exe`，SHA-256 为 `49ed44c28fbbf472279235a18eec2f3e9644d13398baf673b194ba8b2e0b6a12`。历史记录为 `tmp/windows-process/35-python-side-effects.json`、`38-final-inspection.json`、`39-launcher-reference.json`，不是本次卸载后的现状。

续轮开发者明确说明该账户不使用 `python3.12` 命令，授权用 uv 卸载本次私有安装并清理其入口、登记，要求先完整保存现状、对比其他安装与注册项、核对通过后才删工作根；若第 3 步不能成立就停止，禁止手工删除入口或编辑注册表。后续私有实验的显式 Python 安装应使用 `--no-bin --no-registry`，这不替代本次实际核对。

第 1 步记录为 `tmp/windows-process/40-pre-cleanup.json`。启动器仍是上述摘要和私有目标。默认与显式私有安装目录下的 `uv python list --only-installed` 均返回退出码 2，完整 stderr 指出查询全局 `python3.12.exe` 时被应用程序控制策略拒绝，`os error 4551`；这两份完整输出已保存，不能称作成功取得已安装列表。未改变应用控制策略或 PATH，也未改用另一启动路径执行被拒的入口。原始 stdout/stderr 字节分别从记录中的 Base64 解出，位于 `40-uvDefault.*.bin`、`40-uvPrivate.*.bin`。

同一记录只读导出了 HKCU `Software\Python`、`Software\Astral` 的 64 位和 32 位视图，包含全部子键、值名（含默认值）、值类型、未展开的字符串、多字符串及二进制数据；普通 JSON 可读导出为 `tmp/windows-process/40-registry-before.json`。二进制值以 Base64 保留。采集包装函数最初还输出了两个空任务结果对象，原始记录保留原样；`40-pre-state-normalized.json` 仅提取实际 uv 命令结果，没有重跑被拒操作或改写原始输出。

另用只读 `uv python dir` 确定默认 uv 管理根，对该根及注册表指出的其他安装根作文件内容摘要、路径、属性、时间与重解析目标清单；遇到重解析点只记录，不遍历目标。`tmp/windows-process/41-before-inventory.json` 和 `41-before-state.json` 保存结果，清单以 gzip 加 Base64 附在原始记录中，也展开为 `41-managed-tree-*.json` 和 `41-bin-tree.json`。默认管理根内既有 CPython 3.10.17、3.12.13；此根清单有 8,303 项，摘要为 `5b9efa7268eb285baec9ebdd30db7fd3c7a2940d305595489016917a66955863`。两次卸载前的完整注册表导出一致。

第 2 步先重新核对启动器摘要、私有版本目录及其非重解析属性，然后执行 `uv python uninstall --install-dir "%USERPROFILE%\hmb-work\64ee8084\python" 3.12.13`，同时使用相同 `UV_PYTHON_INSTALL_DIR`、私有缓存和临时目录，设置该 uv 进程离线运行。选择的是实际安装的补丁版本 3.12.13，没有使用 `--all`。`tmp/windows-process/42-uninstall.json` 记录 uv 退出码 0，stderr 摘录为 `Uninstalled Python 3.12.13 in 971ms` 和 `- cpython-3.12.13-windows-x86_64-none (python3.12.exe)`；完整原始字节另存 `42-uninstall.stdout.bin`、`42-uninstall.stderr.bin`。

第 3 步记录为 `tmp/windows-process/43-after-uninstall.json`、`43-after-state.json`、`43-registry-after.json`，比较程序及结果为 `compare-cleanup.py`、`44-cleanup-comparison.json`。全局 `python3.12.exe` 已不存在，私有 `cpython-3.12.13-windows-x86_64-none` 目录也不存在；私有别名的 `Test-Path` 仍为 true，未进一步处理。卸载后的默认 `uv python list --only-installed` 成功退出 0，完整列出默认 uv 管理根中的 CPython 3.12.13 和 3.10.17；显式私有目录下的相同命令成功退出 0、输出为空。两份完整输出及原始字节均保留。

对比中，默认 uv 管理根的全部 8,303 项清单及摘要未变，注册表此前提到的另两个安装目录在前后均不存在，`.local\bin` 其他四个文件的内容、属性和时间未变；该目录唯一差异是本次 `python3.12.exe` 被移除。HKCU 导出中没有任何值引用本工作根。这些是已经通过的局部核对，不能代替全部核对。

未通过的是注册表差异：卸载前，HKCU `Software\Python\Astral\CPython3.12.13`、`CPython3.12.3` 及各自 `InstallPath` 指向 `<另一私有根>`，并不指向本工作根；卸载后的两个注册表视图中，这两组版本键及其 InstallPath 都消失了。每个视图原有的 20 个版本值记录均缺失，比较结果列出全部键和值，没有把两个视图的导出记录当成独立物理注册项数量。`Software\Python\Astral` 自身的公司值未变，`Software\Astral` 在前后均不存在。

快照覆盖了本次 uv 卸载及随后 uv 列表核对的时间区间，未跟踪每次注册表写入，无法仅凭这些前后状态判定差异来自哪一个 uv 操作，或是否有同机其他任务同时清理；不能声称这些他根登记由本次安装生成，也不能声称其他状态全部未变。比较结果明确 `step3Passed=false`。因此严格按开发者的停止条件交回，没有手工修复注册表或入口，也没有执行第 4 步目录删除。最后一份远端状态仍为 `rootExists=true`，不存在 `Test-Path=false` 的删除验收，也没有把前一轮进程核对冒充为第 4 步的新核对。

Host 需要核对上述两组注册表差异的来源和是否属于同机其他任务已授权的收尾，明确第 3 步的验收处理；若需要修复，由 Host 另行安排，当前没有手工修改注册表的授权。只有该问题处理后，才能继续删除 `%USERPROFILE%\hmb-work\64ee8084` 并保存 `Test-Path=false` 和新的进程核对输出。完整卸载前状态与差异已保存，可供准确判断，不需要猜测原值。

另有前一轮已知网络约束偏差：内部只读脚本审查误用了 Microsoft Learn 网页工具查询，超出“网络只用于锁定依赖和 Windows SSH”的范围，发现后停止了该审查。没有采用网页内容作为实验结论；本续轮没有网页查询或委派其他代理。初次 Python 安装的默认外部副作用与该网络偏差仍保留记录，不因卸载成功而抹去。

当前检出的写入仅为本文及被忽略的 `tmp/windows-process/`；续轮的远端变更是开发者授权的指定私有 Python 的 uv 卸载，其外部状态差异如实列于上文。本轮没有运行产品检查套件、修改分支/标签、推送、stash 或改日常看板。产品设计与实现、真实 harness 回合、桌面和电源实验均未实施，远端目录删除仍因第 3 步核对失败而未完成。
