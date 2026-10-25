# Windows 阶段 F3：Host 验收

2026-10-25 起。依据 [F3 执行计划](../../design/cross-platform/windows-f3-plan.md)。各批验收之后追加在这里；没有安装。

## 长路径文件边界（微任务 `460cd8a8`）

合入整合分支 `7a3de125`，输出 `c6ed13fc`，37 个文件。

- **做法。** 本机文件调用处才把已核对过的绝对路径转成 `\\?\` 扩展写法，转换只在 `paths.windows_path` 一处；记下的路径、比较、摘要输入、对外 JSON、进程 cwd 与 Git 参数照旧用普通写法。删除树时保留项与遍历出的子项用同一种写法比较。Router 镜像在 Windows 上不再设只读属性，POSIX 照旧 chmod；镜像摘要的算法不变，两个平台都纳入模式位，摘要的含义不随写入时间而变。
- **三个回合。** 第一回合交付后，Host 的审查与一次 Opus 独立审查（互不看对方的结论；478,594 tokens，113 次工具调用，约 42 分钟）的发现经 Host 在源码里核对，合并成一次打回：
  - 恢复摘要算法。
  - 删掉逐段查保留设备名的检查。它多余、昂贵（一条 19 段的路径一次转换约 81 µs，去掉后约 3 µs），还会让存储盘点碰到一个字面的 `nul` 文件就整体失败。
  - 链接类型的判断经转换。
  - 补长路径联接的删除测试，补服务端漏转的产物核对。
  - Windows 上的相对路径按 POSIX 的方式处理。
  - 删除死代码。

  第三回合只交代残余失败的归属。
- **macOS。** 与清场判定、测试根删除合在候选分支上的两次完整检查都通过：`c657db8e` 上 323 个文件 4,978 项，前端 95 个文件 1,123 项。合入整合分支后，受影响的 16 个文件 300 项通过。
- **Windows。** 另派的 Worker（`00460bf2`）在未开系统长路径设置的 Windows 11 上，用 A、T 与测试根删除合起来的候选复跑：
  - 九个长路径主用例全部通过：工具证据、发布与执行事实、服务端产物核对、Router 长镜像、存储深层盘点、长目录符号链接、长联接不被跟随、长路径 resolve、长树的保留删除。
  - 根内 317 个字符处的只读叶子连同一个指向根外的联接一起删除，根外的哨兵文件属性、文件 ID 与内容都不变。
- **记下、不改的。** 以下几条或可以接受，或没有发生过，不为它们加机制：
  - 本机调用失败时，`OSError.filename` 带扩展写法，并进入 issues 的自由文本；
  - UNC 与网络路径被明确拒绝；
  - `open_read` 对非普通文件抛 `BoardError`，而 DSH、ZCode 的几处读者只接 OSError；
  - 用户自设的状态目录带尾部点或空格时，两种写法会分叉。
- **验证边界。** 长路径用例只在 `LongPathsEnabled=0` 的机器上有区分力；测试机是 0，用例本身不记这个前提。Windows 上剩下的失败已经归到后面的批：
  - 存储测试的链接时间与路径写法，归“进程、信号、端点与权限的测试”；
  - 运行时测试的 uv 替身，归“harness 测试替身”；
  - 回执测试清场时 `board.sqlite3` 被占用，归“残余”。

## 其余六批（2026-10-26）

六批一起合入整合分支 `60ccfef1`（经候选分支 `socu/f3-candidate`，三个测试文件的合并冲突由 Host 解决）。Windows 上另一台机器的复核用持续集成：运行 `40217571572`（`e2785c62`，windows-latest）324 个 Python 文件过 252 个，F3 之前是 113 个；同一运行里 macOS 四片覆盖全部 Python 文件，都通过。下面各批“通过”的文件都指这次运行。长路径的区分只在 `LongPathsEnabled=0` 的机器上成立，留到最后在测试机上的复跑。

- **清场判定（`13afbecb`，三个回合，产物 `8f61f3e0`）。** 只认持有的句柄与 Job；私有服务以生命周期锁加上确认过的停止回复算停，确认按状态目录记下；清场证据带 Job 成员的身份；Windows 上拒绝 `--sweep`。第二回合之后的独立复跑查出测试自己停掉服务时被误报“停止回复未确认”，第三回合修好：`test_ctwo_service`、`test_support_cleanup`、`test_checks_parallel`、`test_windows_process_api` 通过。`test_checks_cleanup` 只因 CI 机器 `D:` 盘临时目录的 ACL 与用户目录不同、被状态根的规则拒绝，归“残余”。顺带项：停止确认按目录而不是按服务的这一代记。一次 Opus 独立审查（前两个回合）。
- **测试根删除（`47c4f241`，产品 `3a0a1775`）。** 删除测试根与锁的扫描用扩展路径；它的 Opus 审查留下的三条顺带项由“进程、信号、端点与权限的测试”做掉。
- **样板字节、默认路径与前端的 Python 入口（`f9e5a445`，`ee430d63`）。** `.gitattributes` 固定换行，默认路径按平台构造，前端预览测试改用 `uv run --no-project python`（CI 的前端任务因此要装 uv，随[发版流程](../release/release-workflow.md)补上）。`test_first_install` 通过；`test_upgrade_fixture` 剩下的失败是样板生成器自己的 `git update-ref` 碰上长引用，归“残余”。
- **Git 长文件（`2093e2ca`，`61aabce0`）。** 产品的 Git 命令都经同一个构造处，逐次带 `core.longpaths=true`；Router 的 Git 失败带有界的诊断。`test_router_input`、`test_windows_workspace` 通过；`test_workspace` 剩下的是文件模式、Windows 上不合法的文件名与不可移植的 Git 路径，归“残余”。
- **harness 测试替身（`3fbeb749`，两个回合，`9d6bd825`）。** 替身改成显式的 Python argv；DSH 替身的私有 home 由测试给出的根判断；匿名管道改用读线程；第二回合查清 Windows 上 `test_workflow_real` 挂住的原因（替身往很深的会话目录写文件失败、出错的请求不答复），替身现在对失败的请求回错误并记日志。`test_launcher`、`test_runtime`、`test_packaging`、`test_first_install`、ZCode hooks 自检、`test_workflow_real`、`test_acp_launch` 通过。
- **进程、信号、端点与权限的测试（`b0c59361`，`66d6540d`，44 个文件）。** 只在 POSIX 上有的调用标明平台，Windows 上按 Job、FILETIME 与 ACL 验证同一个目标；产品改两处：Windows 上忽略继承的 `C2_IPC_ROOT`（`rpc_config`，测试机上先复现了 C-Two 的 ValueError），检查运行器在 Windows 上把 SIGBREAK 当作中断、根是链接时锁的扫描失败。`test_rpc_config`、`test_process_identity`、`test_execution_process`、`test_path_state`、`test_transport_attach`、`test_storage`、`test_checks_parallel` 等通过。它查出两处产品缺陷（软停送不到无窗口的子进程；Windows 上发现探针丢掉总超时的分类），记在 [F3 计划](../../design/cross-platform/windows-f3-plan.md)；它没有覆盖到的只在 POSIX 上有的调用（探针与 ZCode 测试里的 `O_NOFOLLOW`、控制台会话的 SIGINT、Codex native run 的负退出码）归“残余”。
- **核对。** 每批的差异 Host 都读过；合并冲突解决后两批都改过的六个文件在 macOS 上 86 项通过。证据副本在 Host 检出被忽略的 `tmp/storage/f3-*-evidence/`。

## 残余（一）：测试与夹具（2026-10-26）

微任务“残余一：Windows 测试与夹具”（run `a86becbc`，Codex CLI 的 `gpt-6.1-sol / max`），基点 `04162c28`，产物 `62e221a0`（92 个测试、探针与工作流文件，没有产品源码），合入 `3369a9fe`（一处与“样板看板的 Git 调用”的冲突由 Host 保留产品的 `_git_command`）。

- **最大的一类（删除时文件被占用，WinError 32）的根因在测试支持：** `InProcessBoard.close` 关掉自己的资源后就删数据库，而同一测试里另一个看板还连着它；删除改由测试的私有根统一清场，实现方先在 Windows 基线上复现了这个错误。各测试自己建的数据库也显式关闭。
- **其余：** 临时树经检查运行器已有的私有根删除；长路径经 `native_path`；只在 POSIX 上有的调用（`O_NOFOLLOW`、SIGINT、负退出码）给出 Windows 的对应验证；只设 HOME 的夹具同时设 USERPROFILE；文件模式与 Windows 上不合法的名字按平台；CI 的 Windows 任务把 TEMP 放到 `%LOCALAPPDATA%\Temp`（运行器的 `D:\a\_temp` 不在用户目录之下，状态根的规则拒绝它）；启动器测试的上限与同类的 240 秒一致。
- **核对：** 合并后完整检查 324 个文件 5,036 项、前端 1,123 项通过，打包的控制台资源与构建一致。持续集成运行 `40229228436`：Windows x64 与 ARM64 各 324 个文件过 279 个（上一轮 256 个），新出现一个失败（`test_host_workflow_worker` 里测试自己调用的 `git apply` 碰上更长的 TEMP），交给残余（二）；Ubuntu 的失败从 39 个降到 30 个，余下的是“读不到的同账户进程”那一处（待开发者定）。测试机上（`LongPathsEnabled=0`）实现方最后一次复跑 325 个文件过 242 个。
- **它查到、留给残余（二）的产品缺陷：** `catalog.py` 两处 `Database(...)` 用完不关；同级 cwd 映射用反斜杠后被 `validate_git_path` 拒绝；scope 冲突的创建路径没有转换（WinError 206）。还有它准备好未应用的 7 个文件的测试补丁。
