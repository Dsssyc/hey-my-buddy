# Windows 阶段 B：私有目录边界：验收记录

2026-10-22，整合 Host。依据是 [Windows 所有权设计](../../design/cross-platform/windows-ownership.md)第 2 节与第 8 节的“阶段 B”。宏任务“跨系统适配”。验收通过，合入为 `755c476c`；与问询那一批的 0.51.0 一起，没有安装。

## 实现

- **微任务：** “Windows 阶段 B：私有目录边界”（run `d483fe57`，Codex CLI `gpt-6.1-sol / high`，三个回合），基点 `66901ce8`，最终输出 `1ea462ed`。
- **做了什么：**
  - `pyproject.toml` 的 Python 下限改为 `>=3.12.4,<3.13`，pywin32 在 Windows 上成为直接依赖，锁文件随之更新。
  - 根包的 `windows_files.py`：句柄层从 `windows_paths.py` 原样搬来，工作树的政策留在原处。它承载唯一的 ACL 接受规则：属主是当前用户或 Administrators，DACL 存在且非 NULL，ACE 全是普通 allow，受托者只有当前用户、SYSTEM、Administrators 与 OWNER RIGHTS。`parent()` 以 `GENERIC_READ`、不共享删除固定每一级祖先，缺的私有目录逐级以 0700 新建，并在该级的句柄上校验。句柄不能是重解析点，必须是磁盘上的对象，它的最终路径必须就是请求路径的长名（不分大小写）。文件句柄只向 CRT 转移一次。
  - 提权时的默认属主：服务、执行进程、启动器与 CLI 在进程开始时，若令牌是提权的，就把默认属主设为当前用户，只做一次；系统拒绝时以 `PRIVATE_OWNER_SETUP_FAILED` 失败。
  - 设计列出的私有入口都经这一个边界：CLI 的控制文件与提交令牌，启动器的凭据转交与 `read_private`，传输的 endpoint，执行的私有 JSON，控制台会话，fast 的空 cwd，备份与安装的私有文件。调用点上重复的 mode 判断删掉。POSIX 上 `ensure_private_dir` 改为逐级以 0700 新建，容许别的进程并发建好其中一级。
  - Windows 上启动器用 `os.spawnv` 代替 `execve`：CPython 3.12.13 在测试机上以继承的环境调 `execve` 时崩溃过。
- **返修：** Host 读完第一回合的差异，打回两处。一是 POSIX 逐级新建时，若某一级恰好被别的进程建好，会以 `FileExistsError` 崩掉。二是 Windows 比较最终名称时，大小写不同或带 8.3 短名的合法路径会被拒；GitHub 的 Windows runner 上 TEMP 就是短名。另有一次清理：测试机系统临时目录里一个检查运行器留下的根，由 Host 授权，按“没有进程在用、运行器的进程身份已缺席”两个条件删除。
- **消耗：** 输入 2,818 万（命中缓存 2,773 万），输出 13.7 万。

## Host 的核对

- 两次的差异都读过。
- 完整检查跑在与问询那一批合并的候选上：307 个文件、4,687 项，两处失败都在环境与文档，见[问询的验收记录](../adr031/inquiry-settlement.md)。
- CI（run 39768410976，`755c476c`）：Ubuntu、macOS、前端与浏览器全过。Windows 四片照旧失败，通过的文件由上一轮（`1fc19667`）的 173 个增到 183 个（共 307 个），新通过 11 个，其中有 `test_windows_files` 与 `test_launcher_selection`；新失败 1 个，`install.test_publication_paths`，见“留给下一批的”。
- 安装演练，脚本 `tmp/inquiry-fix/reh-051.sh`（不进仓库），候选 `755c476c`，基线 0.50.0，导出样本有 169 个任务、202 次尝试、2,462 条事件、3 条问询：正常路径升到 schema 28，备份已校验，计数与五项摘要不变；新服务起不来时回滚到 0.50.0、schema 27，摘要不变。阶段 B 让安装代码经 `open_regular_fd` 读 control.json、安装日志与锁文件，这两遍都走到了。
- 首次安装的边角，脚本 `tmp/cross-platform/reh-b-edges.sh`（不进仓库），空 HOME，经启动器，候选对基线 0.50.0，三种情况两边结果相同：没有 `~/.local` 的 HOME 正常装上并冷启动；经符号链接到达的 HOME 两边都以 `PRIVATE_PATH_UNSAFE` 拒绝；预先建成 0755 的状态目录两边都被修成 0700。
- 日常环境的状态目录核对过，只看元数据：各级都不是链接，文件都是 0600；权限偏宽的子目录（`harnesses/`、`attempts/` 等）不经私有目录校验。
- Windows 独立复跑（run `483297c2`，经路由到 Codex CLI `gpt-6.1-sol / max`；Windows 11 专业版 x64 10.0.26200，CPython 3.12.13，pywin32 312）。Host 读了它的原始输出：
  - 测试：候选上 8 个文件、145 项，88 通过、17 断言失败、29 错误、11 跳过。阶段 B 自己的三个测试文件全过，8.3 短名那一条在测试机上真的取到了短名。有失败的四个文件都在合并前的基线 `b2fae605` 上逐个复跑过：46 处失败里 45 处原来就有，新的一处是 `test_transport_attach` 里只在 POSIX 成立的断言，见下。
  - 私有服务：经源码启动器在私有状态目录上冷启动、读 `health`（0.51.0，schema 28）、停止，共三轮：SSH 的提权令牌；开发者在远程桌面用普通令牌；再一次 SSH 提权。独立的观察脚本不调用产品的校验函数，三轮共记下 60 次对象观察：属主都是当前用户的 SID，ACE 都是普通 allow，受托者只有 SYSTEM、Administrators 与 OWNER RIGHTS。WAL 与 SHM 在两次换令牌之后都重新建出，每次停止之后都不在。
  - 测试机上的工作目录已删除，另开一次核对它不在。
  - 消耗：输入 1,068 万（命中缓存 1,044 万），输出 8.2 万。

## 没有验证的

- Windows 还没有用户。阶段 C 后续、D、E、F、G 做完之前，产品在 Windows 上整体还不可用；检查运行器在 Windows 上观察不到清场而以 3 退出，归阶段 F。
- 测试机上只用开发态源码跑了私有服务。稳定运行时的物化、启动器的首次引导与 active 指针在 Windows 上只由测试覆盖，独立复跑没有在测试机上走一遍真实安装；`test_runtime.py` 在 Windows 上有 18 处原有的失败。
- 设计不自动修改已有对象的属主或 ACL。这一版之前在提权令牌下建出、属主为 Administrators 的对象，普通令牌仍打不开。

## 留给下一批的

阶段 C 后续改 Windows 上 `publish_bytes` 的换代方式，下面几件同在这一块，随它做：

1. **安装发布路径在 Windows 上进了私有边界。** `install.test_publication_paths` 钉着阶段 C 定下的政策：skill 目录与运行时提示、运行时根及其清单与 READY 是安装发布路径，允许上层目录是链接，也不按私有对象校验。阶段 B 在 Windows 上让 `publish_bytes`、`runtime.is_ready` 与运行时根的创建都走私有句柄边界：有一级是链接就拒绝，还拿私有 ACL 规则校验 skill 目录与运行时根。后果是 Windows 用户的 `~/.agents` 若是链接，或 profile 目录继承了白名单以外的 ACE，安装会被拒。POSIX 上只有一角受影响：启动器用系统 Python 引导、读不到运行时模块时，`_runtime_ready` 的回退分支对链接下的运行时根抛出 `PRIVATE_PATH_UNSAFE`，原来是允许的。设计第 2 节已写明这条界线；测试里 active 指针那一半属于私有状态，不属于这条政策，随之删去。
2. **私有路径拿整条路径比对。** `windows_files._check` 比的是整条最终路径：subst 或网络映射的盘会被拒，每一级又把上面各级重新比一遍。各级的祖先已经单独固定和校验过，只比这一级名字的长名（不分大小写）就够了。
3. **工作树上层目录的名字按大小写逐字比对。** `windows_paths._checked` 对仓库所在位置以上的各级目录，也拿调用方的写法逐字比对；仓库路径的大小写与磁盘不同或带 8.3 短名时会被误拒，与第 2 件同一个根因。仓库之内照旧逐字比对 Git 路径。
4. **一处只在 POSIX 成立的测试断言。** `test_transport_attach.py` 的 `assert_falls_back_to_cold_start` 断言状态目录的权限位是 0700，并断言调用过一次 `chmod`；Windows 上目录的 `st_mode` 恒为 0777，这条断言在 Windows 上失败，在基线上原本通过。
5. **一处死条件。** `private_dirs.ensure_private_dir` 在 Windows 分支早早返回之后，又写了 `if os.name != 'nt'`。
