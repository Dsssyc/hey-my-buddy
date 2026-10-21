# Windows 阶段 A：私有目录 ACL 与文件发布实验

2026-10-21，在开发者的 Windows 11 测试机上执行，源码基线为 `d5c908aaaed3cfd66c20822222570a1e267ff9f2`（0.50.1）。本文对应 [windows-ownership.md](windows-ownership.md) 第 2、3、6 节与第 8 节阶段 A 的私有存储、文件发布实验；只记录实验，不修改产品代码，不代表产品安装、检查套件或完整备份恢复协议已经验收。

本轮观察到两项直接影响设计的事实：Python 3.12.13 的 `os.mkdir(path, 0o700)` 确实创建 SYSTEM、Administrators、OWNER RIGHTS 三条普通 allow ACE 的受保护 DACL；允许删除共享的读者仍持有目标时，`os.replace` 仍失败，不能据“读者允许删除共享”就认定该发布路径可用。非提权令牌与 Defender 实时扫描开启的场景未观察，安排步骤见末节。

## 环境、方法与原始证据

系统报告 `Microsoft Windows 11 专业版`、版本 `10.0.26200`、构建 `26200`，C 卷为 NTFS；PowerShell 为 `5.1.26100.9444`，uv 为 `0.10.8`。经 uv 获取并实际运行了 CPython `3.12.13` x64 与 `3.12.3` x64，主实验使用 3.12.13，锁定依赖包括 `pywin32==312`、`portalocker==3.2.0`、`c-two==0.7.6`。原始版本、令牌、卷与 Defender 输出在 `tmp/windows-files/setup.stdout.raw`，依赖安装与主解释器版本在 `tmp/windows-files/merge-bootstrap.stdout.raw` 和 `merge-bootstrap.stderr.raw`，旧解释器版本在 `tmp/windows-files/evidence/old-mkdir.stdout.raw`。

所有远端实验文件、源码克隆、解释器、缓存、状态目录和复制的运行时均放在 `%USERPROFILE%\hmb-work\<任务前8位>` 下，文中简称 `<实验根>`。源码通过 git bundle 传输、远端 `git clone`，没有推送；完整历史 bundle 两次传输中断，随后从本地临时浅克隆生成只包含当前基线的 bundle，先本地克隆验证，再分片传输并合并。远端读回提交与上述基线一致；合并 bundle 的 SHA-256 为 `e026aea55cd0870e878131914898699d7cdbf2aa0a5900aebb5dd9e7024101a6`。命令、断线与成功重传记录在 `tmp/windows-files/` 下的 `bootstrap.*`、`python-setup.*`、`merge-bootstrap.*`、`transfer*.py`、`*.transfer*.txt` 与 `shallow-test.json`。

uv 的首轮安装已设置私有解释器与缓存目录，但尚未设置 `UV_PYTHON_BIN_DIR`，原始输出含 `Failed to install executable`、既有 `python3.12.exe` 不由 uv 管理的警告；没有使用 `--force`。后续显式设置私有 `UV_PYTHON_BIN_DIR=<实验根>\python-bin`，没有改 PATH、注册表、服务或 Defender 设置。3.12.3 的下载连接后来中断，未取得那次安装命令的完成退出码；之后 `uv python find 3.12.3 --no-python-downloads` 返回 0，实际执行该解释器输出 `3.12.3 (main, Apr 15 2024, 17:48:16) [MSC v.1929 64 bit (AMD64)]`，因此旧版对照已实际执行。

原始证据目录为 `tmp/windows-files/evidence/`；其中 `*.stdout.raw`、`*.stderr.raw` 保存远端探针进程的原始输出字节，`runtime-install.log` 保存运行时内部安装输出，Python 探针源码也在该目录。外层实际命令、退出码、SSH 标准输出与错误分别保存在 `tmp/windows-files/<步骤>.command.ps1`、`<步骤>.result.json`、`<步骤>.stdout.raw`、`<步骤>.stderr.raw`；关键步骤为 `probes-final`、`acl-rerun`、`acl-rerun3`、`share-run`、`collect`、`cleanup` 和 `cleanup-verify`。外层 PowerShell 退出 0 不等于所有探针成功，应看其中逐项打印的退出码。

远端收集包 `tmp/windows-files/evidence.zip` 的 SHA-256 为 `1b3068fe912db8d113fd797a9de7df82c24735629195c545881a7a3e7a154365`；取回后逐一核对了清单中 33 个文件的长度和 SHA-256，全部相符，结果在 `tmp/windows-files/evidence-verification.json`。`evidence-manifest.json` 是远端生成的逐文件清单。本文摘录将路径与当前用户名称替换为占位符，完整原始输出保留原样。

ACL 初版探针因 `win32con` 未暴露 `SE_DACL_PROTECTED` 退出 1，第二版因未暴露 `FILE_ALL_ACCESS` 退出 1，分别保存在 `evidence/acl_probe.stderr.raw` 与 `evidence/acl-v2.stderr.raw`。改用对应系统位值 `0x1000` 与 `0x1f01ff` 后，在新的子目录重新执行完整探针，`acl-v3` 退出 0、标准错误为空。旧版目录读回的修正版 `old-inspect-v2` 也退出 0。失败版本的脚本保存在 `tmp/windows-files/acl_probe_v1.py` 与 `acl_probe_v2.py`，没有把探针错误计为 ACL 拒绝。

## 新建目录的 owner、DACL 与继承

执行 `os.mkdir(path, 0o700)`、不传 mode 的 `os.mkdir(path)`，并在显式加入可继承 Everyone 读取 ACE 的父目录下分别传 `0o700` 与 `0o777`（默认 mode 的值）创建。每个对象先用 `CreateFile` 打开句柄，再用 `GetSecurityInfo(handle, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION)` 读回，同一快照记录控制位、全部 ACE 与 SID；随后运行 `icacls`，记录该命令的退出码、标准输出与标准错误。完整输出为 `tmp/windows-files/evidence/acl-v3.stdout.raw`；每次 `icacls` 均退出 0。

| 解释器与创建位置 | 创建方式 | owner | DACL 主体与继承 | protected | 规则判定 |
| --- | --- | --- | --- | --- | --- |
| 3.12.13，普通实验父目录 | `mode=0o700` | Administrators | SYSTEM、Administrators、OWNER RIGHTS；三条均非继承 | true | 接受 |
| 3.12.13，同一父目录 | 不传 mode | Administrators | SYSTEM、Administrators、当前用户；三条均继承 | false | 接受 |
| 3.12.13，Everyone 可继承读的父目录 | `mode=0o700` | Administrators | SYSTEM、Administrators、OWNER RIGHTS；三条均非继承 | true | 接受 |
| 3.12.13，同一宽继承父目录 | `mode=0o777` | Administrators | 继承当前用户、SYSTEM、Administrators 与 Everyone ACE | false | 拒绝 |
| 3.12.3，普通实验父目录 | `mode=0o700` | Administrators | SYSTEM、Administrators、当前用户；三条均继承 | false | 接受 |
| 3.12.3，同一父目录 | 不传 mode | Administrators | SYSTEM、Administrators、当前用户；三条均继承 | false | 接受 |

3.12.13 的 0700 目录原始读回摘录为 `owner=S-1-5-32-544, daclPresent=true, nullDacl=false, protected=true, control=36868`；三个 ACE 的 `type=0, flags=3, mask=2032127`，SID 依次是 `S-1-5-18`、`S-1-5-32-544`、`S-1-3-4`。`icacls` 同时显示 `NT AUTHORITY\SYSTEM:(OI)(CI)(F)`、`BUILTIN\Administrators:(OI)(CI)(F)`、`OWNER RIGHTS:(OI)(CI)(F)`，没有 `(I)`。宽继承父目录下的 0700 子目录也是这三条 ACE。

不传 mode 的 3.12.13 目录读回 `protected=false, control=32772`，三条 ACE 的 `flags=19`，`icacls` 对应 `(I)(OI)(CI)(F)`；在宽继承父目录下还出现 Everyone 的继承 ACE，规则据此拒绝。3.12.3 的 0700 与默认创建均读回相同的继承主体与未保护标志；完整旧版输出见 `tmp/windows-files/evidence/old-inspect-v2.stdout.raw`。

结论：本机本令牌下，3.12.13 的新目录行为证实设计第 2 节关于三个主体、受保护 DACL 与隔断宽继承的预期；3.12.3 的对照证实不能把较早版本的 `mode=0o700` 当作设置该 DACL 的证据。没有运行恰好 3.12.4，也没有在 3.12.3 下重做宽继承父目录实验，因此这组数据不能单独证明变更精确始于 3.12.4，或覆盖所有旧补丁版本。默认父目录此次恰好符合白名单，不能据默认创建的“接受”推导任意既有父目录都私有。

## SSH 令牌、提权与对象属主

`whoami /groups` 实际显示 `Mandatory Label\High Mandatory Level`，SID 为 `S-1-16-12288`；Administrators 组为启用状态且标记 `Group owner`。额外调用 `GetTokenInformation`，读回 `TokenElevation=1`、`TokenElevationType=1`、`TokenOwner=S-1-5-32-544`。其中 elevation type 的值为 Default，没有将它写成 Full。完整输出见 `tmp/windows-files/setup.stdout.raw` 与 `tmp/windows-files/evidence/token-probe.stdout.raw`，令牌探针退出 0。

新建的 0700 目录、默认目录、普通文件和 SQLite 文件都实际读回 Administrators 为 owner。查询 `TokenLinkedToken` 失败，原始错误为 `(1312, 'GetTokenInformation', '<错误文本>')`、`winerror=1312`。本轮只取得这个高完整性 SSH 令牌，未从它取得关联的另一令牌，没有启动桌面 UAC 或另用账户。

结论：证实设计第 2 节“提权创建时属主可能为 Administrators”在本次 SSH 会话中实际成立；非提权创建的属主以及提权、非提权交替访问同一根均未观察。关联令牌查询失败是本轮无法从当前令牌继续该对照的直接证据，不能据此断言所有 SSH 登录均无法取得非提权令牌。

## ACL 正反例与 SQLite 子文件

模型无关探针 `tmp/windows-files/evidence/acl_probe.py` 使用上述句柄级快照实现唯一规则：owner 只能是当前用户 SID 或 Administrators；DACL 必须存在且非 NULL；遍历全部 ACE，仅允许普通 allow（type 0），受托者仅限当前用户、SYSTEM、Administrators、OWNER RIGHTS。继承、只作用于子对象、访问掩码与 protected 标志均不另设放行或拒绝规则。此脚本没有导入产品的 ACL 实现，也没有做有效权限计算。完整结果与逐对象 `icacls` 输出见 `evidence/acl-v3.stdout.raw`。

| 构造或对象 | `GetSecurityInfo` 关键读回 | 校验结果 | `icacls` 对照摘录 |
| --- | --- | --- | --- |
| Everyone 读、写各一例 | allow，SID `S-1-1-0` | 两例均拒绝 | `Everyone:(GR)` / `(GW)` |
| Users 读、写各一例 | allow，SID `S-1-5-32-545` | 两例均拒绝 | `BUILTIN\Users:(GR)` / `(GW)` |
| Authenticated Users 读、写各一例 | allow，SID `S-1-5-11` | 两例均拒绝 | `NT AUTHORITY\Authenticated Users:(GR)` / `(GW)` |
| 当前用户的 deny ACE | ACE type 1 | 拒绝 | 当前用户的 `(DENY)(WD)` |
| Everyone 只作用于子对象 | type 0，flags 11，`inheritOnly=true` | 拒绝 | `Everyone:(OI)(CI)(IO)(GR)` |
| 宽继承父目录的默认子目录 | 含 Everyone，`inherited=true` | 拒绝 | Everyone 带 `(I)`，其中一条带 `(IO)` |
| 当前用户、SYSTEM、Administrators 的合法 allow | 全部 type 0，全部白名单 | 接受 | 三个合法主体的 `(F)` |
| 私有祖先下默认新建子目录 | SYSTEM、Administrators、OWNER RIGHTS，flags 19 | 接受 | 三条 `(I)(OI)(CI)(F)` |
| 该子目录中的模拟凭据文件 | 同上三个主体，flags 16 | 接受 | 三条 `(I)(F)` |
| NULL DACL | `daclPresent=true, nullDacl=true` | 拒绝 | `No permissions are set. All users have full control.` |
| 空 DACL | `daclPresent=true, nullDacl=false, aces=[]` | 接受 | 路径后没有 ACE 行 |
| owner 改为当前用户 | owner 为当前用户 SID，ACE 均白名单 | 接受 | 白名单 ACE；owner 依据来自 API |
| owner 改为 Administrators | owner `S-1-5-32-544`，ACE 均白名单 | 接受 | 白名单 ACE；owner 依据来自 API |
| owner 改为 SYSTEM | owner `S-1-5-18`，ACE 均白名单 | 拒绝 | 白名单 ACE；owner 依据来自 API |
| SQLite 数据库、`-wal`、`-shm` | owner Administrators，三条继承 allow，flags 16 | 三者均接受 | SYSTEM、Administrators、OWNER RIGHTS 三条 `(I)(F)` |

改属主实验只对本轮合成文件启用当前探针进程令牌中的 `SeRestorePrivilege` 与 `SeTakeOwnershipPrivilege`，两次读回 `lastError=0`，没有更改系统策略或其他会话。SYSTEM 是 ACE 白名单成员，但不在 owner 允许集合，`owner-system` 的原始拒绝原因正是 `owner outside allowed set`。默认 `icacls <path>` 输出不含属主，本轮没有拿其主体行冒充 owner 证据。

SQLite 实验显式设置 WAL，读回 `['wal']`，建表、插入、提交后保持连接打开，再检查数据库及两个 sidecar；三者均为 owner Administrators、`protected=false`，普通 allow 受托者仅为 SYSTEM、Administrators、OWNER RIGHTS。关闭连接后读回 `walExists=false, shmExists=false`。模拟凭据只含合成字节，没有读取或写入真实凭据。

结论：这些实物样本证实设计第 2 节所列正反例可由单一 owner/DACL/ACE 规则区分，私有祖先继承与 SQLite sidecar 在本轮符合规则；NULL 与空 DACL 确实可区分。空 DACL 的“接受”只是该规则的结果，未验证普通文件读写是否获准。没有构造 DACL 缺失、对象 ACE、条件 ACE、未知 ACE 类型或 junction 竞态，因此不据此宣称所有安全边界情形已覆盖。

## 稳定运行时中的 pywin32 与源目录替换

在 `<实验根>\state` 下设置私有状态与运行时根，`HMB_DEV_SOURCE=1`，经开发态源码调用 `runtime.materialize(repo, destination=<实验根>\state\runtime)`，内部执行锁定的 `uv sync --frozen --no-dev`。返回 `installed=true`、`state=READY`，内容目录标识为 `24347fa68195912491d59bef818c38bd`。没有调用日常安装入口或启动产品服务。原始命令与退出码见 `tmp/windows-files/probes-final.*`；落定结果、内部安装输出分别见 `evidence/runtime_probe.stdout.raw` 与 `evidence/runtime-install.log`，落定探针退出 0。

开发态解释器退出后，改用稳定运行时自己的 `venv\Scripts\python.exe` 启动独立验证器。先启动一个全新进程导入 `win32security`，再把源码目录改名为 `repo-away` 并在原位置建立空目录，再启动一个全新运行时进程导入；最后还原源码目录。两次进程均退出 0、标准错误为空，后一条原始记录为 `phase="source-replaced", sourceReplaced=true, exitCode=0`。完整输出为 `tmp/windows-files/evidence/runtime-verify.stdout.raw`。

实际模块路径为 `<实验根>\state\runtime\<内容标识>\venv\Lib\site-packages\win32\win32security.pyd`。实际 `.pth` 为同一运行时的 `venv\Lib\site-packages\pywin32.pth`，内容包含相对路径 `win32`、`win32\lib`、`pythonwin` 以及 `import pywin32_bootstrap`；输出的 `sys.path` 包含这三个运行时内部目录。另一个 `_editable_impl_hey_my_buddy.pth` 指向该稳定运行时自己的 `src`，不是被替换的原检出。

结论：证实设计第 2 节与第 8 节阶段 A 要求的“稳定运行时落定后，源目录替换不破坏 `import win32security`”在此锁定环境成立。本轮使用解释器正常的 site/.pth 启动，未执行尚待实现的产品 ACL bootstrap 校验；没有验证所有 launcher、active 指针入口或缺依赖时的加载次序。

## 三类读者参与替换与删除

探针分别用本进程 `open(path, 'rb')`、本进程 `CreateFile(GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)`、独立子进程 `CreateFile(GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE)` 持有目标，分别执行 `os.replace(temp, target)` 与 `os.unlink(target)`。外部子进程通过 `READY` 明确表示已打开目标，父进程操作后终止并等待该子进程；自己的句柄显式关闭。每个操作使用独立目录，内容为 `OLD` 与 `NEW`。完整输出在 `tmp/windows-files/evidence/files_probe.stdout.raw`，探针退出 0、标准错误为空。

| 持有目标的读者 | `os.replace` | 删除 | 释放读者后的结果 |
| --- | --- | --- | --- |
| 自己持有的普通 Python 读者 | `PermissionError, errno=13, winerror=5`；目标仍为 OLD，临时文件在 | `PermissionError, errno=13, winerror=32`；目标仍在 | 同一替换或删除成功 |
| 自己持有的允许删除共享读者 | `PermissionError, errno=13, winerror=5`；目标仍为 OLD，临时文件在 | 成功；按路径读回不存在，已有句柄仍读到 OLD | 失败的替换在关句柄后成功 |
| 外部不允许删除共享读者 | `PermissionError, errno=13, winerror=5`；目标仍为 OLD，临时文件在 | `PermissionError, errno=13, winerror=32`；目标仍在 | 停止并等待外部读者后，同一操作成功 |

对允许删除共享却替换失败的结果，另写 ctypes 探针，显式声明 Win32 函数参数和句柄宽度，`CreateFileW` 的 access 固定为 `0x80000000`、share 固定为 `7`。在各自独立目录重复三轮：`os.replace` 三次失败，均 `winerror=5`；`MoveFileExW(..., MOVEFILE_REPLACE_EXISTING)` 三次失败，均 `winerror=5`；`ReplaceFileW(target, temp, NULL, 0, NULL, NULL)` 三次成功，均读回路径内容 NEW 且临时文件消失。完整输出与脚本见 `tmp/windows-files/evidence/share-probe.stdout.raw`、`share_probe.py`，外层命令与退出码见 `tmp/windows-files/share-run.*`，探针退出 0。

结论：证实第 6 节所述普通或外部读者可阻止删除，以及释放已知占用后操作可继续；失败替换保留 OLD 和临时文件的现象也已观察。否定在本轮条件下“读者只要允许删除共享，就足以使第 3 节的 `os.replace` 换代成功”的组合假设；允许删除共享在本轮确实使删除成功，但未使 `os.replace` 成功。替换失败出现的是错误 5，不能把此处所有忙错误都预设为 32。`ReplaceFileW` 的三次成功仅为 API 对照，没有验证其全部失败窗口、元数据或耐久性语义，也没有据此选择或实现产品替代方案。

## SQLite 占用、只读 Git 对象与已加载解释器

在独立数据库上分别保留读写连接与 `mode=ro` 只读连接，执行删除与改名。四次打开连接时的操作全部失败，均为 `PermissionError, errno=13, winerror=32`；显式关闭对应连接后，同一数据库的改名成功。原始摘录为 `sqlite rw open delete/rename: success=false, winerror=32`、`sqlite ro open delete/rename: success=false, winerror=32`、两条 `closed rename: success=true`，见 `tmp/windows-files/evidence/files_probe.stdout.raw`。

结论：证实设计第 6 节关于读写、只读 SQLite 连接均可能阻止删除或恢复改名，以及必须关闭连接的判断。本轮没有执行产品数据库恢复、删除 WAL/SHM 绕过占用或检查所有产品连接是否关闭，不能据这个合成连接实验判断产品清理路径已经正确。

在实验根内另建空 Git 仓库，以 `git hash-object -w --stdin` 写入合成对象；两条 Git 命令均退出 0，实际对象的 `readonlyBefore=true`。首次 `os.unlink` 失败，`errno=13, winerror=5`；只对该对象清除 READONLY 后，`readonlyAfter=false`，同一删除成功。原始摘录为 `git readonly delete: success=false, winerror=5`、`git cleared readonly delete: success=true`，同样见 `evidence/files_probe.stdout.raw`。

结论：证实第 6 节的测试 Git 对象 READONLY 原因与清除后删除这一限定做法在该合成仓库成立。没有更改源码克隆或其他仓库的对象权限，也没有验证产品私有树需要清除 READONLY。

复制本轮 uv 管理的完整 Python 基础目录到实验根的独立副本，启动其中的 `python.exe` 并等待它输出自己的路径后，分别删除该可执行文件、对其目录执行 `shutil.rmtree`。二者均失败，`errno=13, winerror=5`，错误落在副本的 `python.exe`；父进程终止并等待这个解释器子进程（退出 1），再对残留目录 `rmtree` 成功。原始摘录为 `loaded python executable delete: success=false`、`loaded runtime rmtree: success=false`、`stopped runtime rmtree: success=true`，见 `evidence/files_probe.stdout.raw`。

结论：证实第 6 节关于已加载解释器可能阻止运行时目录完整删除的判断。失败的递归删除没有记录此前已经删除哪些其他文件，不能宣称整个目录保持原样；这也不是产品的运行时保留机制验收。没有删除探针正在使用的主解释器或其他执行的运行时。

## 四个发布窗口强杀写者

用模型无关子进程写入 1 MiB 新内容，父进程通过 `READY` 等待它停在指定窗口，随后 `Popen.kill()` 并 `communicate()` 等待结束，再用另一个全新读者进程检查目标、临时文件的存在性、长度与 SHA-256。旧版为 1 MiB 的 `O`，新版为 1 MiB 的 `N`；每个窗口独立重复 3 次，12 个写者退出码均为 1，12 个新读者退出码均为 0。完整源码和记录见 `tmp/windows-files/evidence/files_probe.py` 与 `files_probe.stdout.raw` 的 `crash-window` 行。

| 强杀窗口 | 精确暂停位置 | 3 次独立读者均观察到 |
| --- | --- | --- |
| 临时文件写完 | `write`、`flush` 之后，尚未 `fsync`，写句柄仍开着 | 目标为完整 OLD；临时文件为完整 NEW |
| fsync 之后 | `os.fsync` 返回后，写句柄仍开着 | 目标为完整 OLD；临时文件为完整 NEW |
| 替换之前 | `fsync`、关闭写句柄之后，尚未 `os.replace` | 目标为完整 OLD；临时文件为完整 NEW |
| 替换之后 | `os.replace` 返回后 | 目标为完整 NEW；临时文件不存在 |

原始读回 OLD 的长度为 `1048576`、SHA-256 为 `956f8c406228d40a85d69e3a26ac269d8472b0cef7e171ef67135c845cd17c24`，NEW 的长度为 `1048576`、SHA-256 为 `95140f1fc2c37dc6d586a2c50a701be86dc4e4ae1b0ab95b688d5705482caa6d`。所有读者均读到上表对应完整摘要，未观察到半份目标文件。

结论：这些 12 次结果证实第 3 节关于单文件发布的进程强杀承诺在本探针窗口成立，包括成功替换返回后读到完整新版。没有执行断电、重启、目录刷盘、并发替换读者、多文件绑定、native journal、gzip 尾部、备份 journal 两次改名或恢复器，因此不能将单文件探针结果扩展为这些协议或断电耐久性的验收。

## 连续发布与 Defender 的实际状态

首尾读取 `Get-MpComputerStatus`，均观察到 `RealTimeProtectionEnabled=False`、`AntivirusEnabled=True`；首次还记录了引擎与签名版本，见 `tmp/windows-files/setup.stdout.raw`、`probes-final.stdout.raw`、`collect.stdout.raw`。全程没有调用设置命令，实时扫描开启的前提本轮不成立。

在现状下执行 1000 次串行热路径，每次向仅本探针使用的临时路径写入 4 KiB 加序号的数据、flush/fsync、关闭、`os.replace`、普通读回逐字节比较，再创建并删除另一个叶子；没有设置重试，也没有同时持有阻止替换的目标读者。原始汇总为 `iterations=1000, successes=1000, errors={}, seconds=2.6559999999990396`，见 `tmp/windows-files/evidence/files_probe.stdout.raw` 的 `hot-summary`。

结论：本轮 1000 次在实时扫描关闭的实际环境里未观察到偶发占用错误，记录次数为 0。设计第 6 节与第 8 节要求的“Defender 实时扫描开启时的热路径”未观察，无法判断它是否引入瞬时冲突，不能用这组 0 错误数据支持或否定有界重试，也不能把三个受控持有读者的确定性错误计成杀毒软件偶发错误。

## 清理核验

所有自建读者、写者与加载解释器子进程均由探针显式关闭或终止并等待。证据取回且摘要核对完成后，在远端按可执行文件路径或命令行与实验根的关系枚举，输出 `RELATED PROCESSES BEFORE CLEANUP 0`，再执行 `Remove-Item -LiteralPath <实验根> -Recurse -Force`，返回 `WORK ROOT EXISTS False`，外层退出 0。新开一次独立 SSH 查询，再次输出 `WORK ROOT EXISTS False`、`RELATED PROCESSES 0`、退出 0。完整命令、退出码和输出在 `tmp/windows-files/cleanup.*` 与 `cleanup-verify.*`。

清理结论只覆盖本轮实验根和已登记的实验进程；没有启动或重启日常服务，没有访问日常看板，没有调用模型、真实 harness 或更改登录状态。本文没有运行仓库完整检查套件；交付校验针对原始证据、实验退出码、文档内容和写入范围。

## 需要开发者安排的实验

1. 非提权与提权对照：在开发者的 Windows 11 测试机桌面，使用同一用户分别打开普通 PowerShell 窗口和“以管理员身份运行”的 PowerShell 窗口；不要用本轮的高完整性 SSH 会话代替普通窗口。在新的独立实验根中固定同一个 Python 3.12.13，分别记录 `whoami /groups`、`TokenElevation`、`TokenElevationType` 与 `TokenOwner`，运行相同的 0700、默认、宽父目录探针，用句柄读回 owner、DACL、继承及 protected，再用 `icacls` 对照。先普通窗口建根、提权窗口进入并创建文件及子目录，再反向重复，记录每次实际错误码、owner 与 ACL；不能只凭一次新建成功认定交替访问成立。普通令牌属主是否为当前用户、提权创建的对象能否继续被普通窗口访问，均作为待观察项。最后关闭所有自建进程，取回证据并清理该实验根。

2. Defender 实时扫描开启的热路径：由开发者安排一台原本已开启实时扫描的合适 Windows 11 环境，或自行决定如何安排当前测试机；本任务不改变其设置。运行前后保存 `Get-MpComputerStatus`，只有两次都为 `RealTimeProtectionEnabled=True` 才记为此条件的证据。传入同一基线 bundle，在新的实验根用锁定依赖和相同探针执行三种读者操作、四窗口强杀及至少 1000 次无重试发布，逐次记录原始异常、errno、winerror、操作与迭代序号，并分别统计受控占用和无受控占用热路径的错误。发生错误时保留旧目标与临时文件，释放自己持有的读者后再单独记录一次继续操作的结果；没有错误就记录 0，不归因于未观测到的软件。取回原始证据后关闭本轮进程并清理实验根。

上述两项完成前，第 2 节的非提权/交替使用与第 6 节的实时扫描瞬时占用仍为未观察。本轮不请求改 Python 下限、不选择替换 API、不引入重试；相关设计取舍由 Host 根据这些实测结果处理。
