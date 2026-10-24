# Windows 阶段 D 的余下部分（D2）：执行计划

2026-10-24，整合 Host。依据：[Windows 所有权设计](windows-ownership.md)第 3、4 节与第 8 节的阶段 D，ADR-031 第 4、5、7、10 条，D1 之后一次 Windows 调查得出的四项。初稿由一个只读的规划微任务对照源码写出（基线 `ab7b2c12`）；Host 核对源码删改，另有一次独立审查，两者的结论在第 7 节；开发者当天定了两处设计改动（第 3 节）。文中的文件与行号指 `ab7b2c12`。

这份计划与设计正文冲突处，以这份计划为准。句柄实验成立之后，设计正文与 ADR-031 第 4、5 条的相应文字已于 2026-10-24 由 Host 改写。

## 1. 范围与拆批

D2 修 Windows 上两类现存的缺陷：服务的启动与停止报法会把"不知道"说成"成功"或"已停"；服务持有执行根 Job 唯一的句柄，服务每重启一次、每做一次同契约安装，都会因最后句柄关闭杀掉在跑的执行。

两批依次做，各自能独立验收：

- **D2a 服务的启动、停止报法与就绪判断。** 先做，因为 D2b 的保活需要服务本身不被认不出的外层 Job 支配。
- **D2b 执行根的句柄归属、身份、接回与存储形式。** **门槛：** 第 5 节的句柄实验先由 Host 另派 Worker 做，成立之后 D2b 才开工；不成立，回到开发者，重新讨论"更新与安装时让执行挺过去"是否值得。2026-10-24 实验成立，见第 5 节。

不在 D2 里的：阶段 E 的接收表、可靠接收后的 `finalizing`、ACK 之后才最终结算与删除 delivery 文件（ADR-031 第 1 条，及第 7 条里"已接收不是停止证据"那一半）；阶段 F 的忙文件与测试收尾；C-Two 按用户作用域的 Windows 管道与服务端身份核对（由 C-Two 做，hey-my-buddy 之后采用）。D2 的验收不宣称做到了 E 的部分。

版本：0.53.0 与契约 0.53.0 不动；D2 改的交接事实与 SQLite 形式追加进已开着的 schema 28 → 29 升级。

## 2. D2a：服务的启动、停止报法与就绪判断

**停止报法。** `protocol/transport.py:452–470`：`stop`、`restart` 经 `_attach_read_only` 取端点，取不到或 ping 不通（`_ping_endpoint` 把 `SERVICE_UNAVAILABLE` 变成 `None`，:233–241）就回答 `alreadyStopped: true`，不核对所有者。改为：只有服务的两把 lifetime 锁（`control-daemon.lock`、`board-owner.lock`）都证明所有者缺席时才回答已停；锁被持有却连不上，回答 `SERVICE_UNAVAILABLE`，在 Windows 上说明服务可能在同一用户的另一次 Windows 登录里运行。这条路径只观察：在 `control-start.lock` 里打开已有的锁文件，不带 `O_CREAT`，不创建状态目录或文件，不删锁，不删端点。transport 里在端点为 `None` 时报"没有服务在运行"的几处（:209、229、368、378）共用同一个锁观察。

**不另起第二个服务。** 现状已经成立：冷启动在 start 锁里经 `_retire_stale_endpoint`（:323–344）核对 lifetime 锁，被持有就拒绝；新服务的 `acquire_exclusive`（`blackboard/service/daemon.py:84–93`）在锁被持有时以 `ALREADY_RUNNING` 退出。D2a 只把这两处的报法改成上面同一句说明。`control.json` 缺失、损坏、不可读都经 `_read_endpoint` 返回 `None`、走同一条分支，不另加分支；测试只分"锁被持有"与"锁空闲"两种。

**独立启动。** `protocol/service_process.py:22` 的 `independent_service_popen` 用 detached、新进程组与显式 breakaway 创建服务，只把创建失败的错误 5 报成 `SERVICE_START_UNSUPPORTED`。调查发现：经 venv 转发器的启动在禁止脱离的外层 Job 里返回成功，服务却仍在外层里，外层关闭时一起退出；空句柄的 Job 查询只读到最内层。方案：挂起创建，要求 `IsProcessInJob(创建的进程, NULL)` 为假才恢复，否则终止这次创建的进程并报 `SERVICE_START_UNSUPPORTED`。检查的对象是还没有运行任何 venv 代码的创建进程；真正的解释器随后进入转发器自己的 Job，这不影响结论。冷启动与安装的 `start()` 共用这一处。

先在 Windows 上实验再实现，都走产品实际的 venv 入口：自建禁止脱离的外层 Job 里应在服务代码运行前被拒绝、创建的进程已收尾；SSH 原样应通过，关掉 SSH 后服务仍在。桌面 PowerShell 与 VS Code 终端里不能误拒，否则产品在桌面上不能用：这一项要开发者在桌面上做，结果不成立就停下报 Host，不另想机制，不换 flags 重试。

**启动错误的归属与就绪判断。** 冷启动与安装都按 `startup-error.json` 的 `pid` 等于 `child.pid` 认领启动错误（`transport.py:424–427`、`install/upgrade.py:213–219`），安装的 `start()` 还要求 `control.json` 的 `pid` 等于 `child.pid` 才算就绪（`upgrade.py:224`）。服务写的是自己的 `os.getpid()`（`daemon.py:341`），经 venv 转发器时 `child` 是转发器，两者永远不等：Windows 上启动错误都会退化成 `SERVICE_START_FAILED`，每次安装都等满 45 秒再报 `UPGRADE_SHUTDOWN_UNCONFIRMED`（按代码推断，未在 Windows 上复现）。改为：两处启动都已持有 `control-start.lock`（`transport.py:370`、`install/upgrade.py:457、485`），spawn 之前删掉旧的 `startup-error.json`，之后出现的就属于这次启动，pid 比较与文件里的 `pid` 字段一并删掉；安装的就绪改用现有的 `probe(state, target)`（`upgrade.py:176–185`）。两个系统同一条规则；Popen 的 PID 只用来回收启动句柄。

**顺带。** `tests/python/buddy/harnesses/test_harness_discovery.py`：:613 的 `PATH.split(":")` 改用 `os.pathsep`；:425 的别名测试改用一个合法的真实别名（例如 `lts/iron`），仍走同一条别名解析分支；:457 的 `'/.nvm/'` 字符串过滤改为按路径组件判断。直接改现有测试。

## 3. D2b：执行根的句柄归属、身份、接回与存储

### 开发者 2026-10-24 定的两处设计改动

1. **根 Job 的长期句柄交给服务创建的进程。** 原设计让执行自己按名打开并持有根 Job。执行要等解释器起来（零点几秒以上，经转发器更久）才打开；在这之前服务的创建句柄是唯一的句柄，有序重启与同契约安装（安装的 detach 总会先发 restart，`install/upgrade.py:188–190`）一关掉它，kill-on-close 就杀掉这个执行；服务停止时 `_settle_unidentified` 一看到停止就退出（`blackboard/service/execution_scheduler.py:295`）；原设计又把 marker 推到确认之后才写，这个尝试只剩启动意图，恢复永远返回 unknown（`blackboard/tasks/interruption.py:191–197`），名额与 claims 一直占着。改为：服务在恢复它创建的进程之前，把根 Job 的句柄以不可继承的方式复制进这个进程（`DuplicateHandle`），然后才可以关掉自己的。被创建的进程在 Windows 产品链里是运行时 venv 的 `Scripts\python.exe` 转发器：它等真解释器退出才退出，自己被杀时它内层的 Job（`KILL_ON_JOB_CLOSE | SILENT_BREAKAWAY_OK`）带走真解释器；所以根 Job 正好活到执行结束，始终带着 kill-on-close。执行不再打开根 Job。
2. **删去 Windows 会话号（`ProcessIdToSessionId` 的值）。** 停止证据本来就要求根的完整身份缺席；根缺席之后，还能撑着根 Job 的只有服务的临时句柄，而做恢复的服务持有 lifetime 锁、是唯一的服务，也知道自己有没有开着句柄。会话号只多挡住旧服务退出时"先释放锁、后关句柄"的微秒级竞态，代价是一列、一处核对，以及重启后会话号变了时尝试永远 unknown；它也区分不了登录（几次 SSH 登录都是 0）。

### 身份

执行根是服务创建的那个进程：Windows 产品链里是 venv 转发器，没有转发器时就是解释器本身；POSIX 上是进程组的组长，与现在相同。Windows 的身份是 `{pid, startedAt, jobName}`：`startedAt` 是内核创建时间的原始 FILETIME 整数（`protocol/process_start.py:79` 已经这样读）。删去 Windows BootIdentifier 的读取、记录与比较（`protocol/process_identity.py:127–150、242、280、325`，`protocol/execution.py:111–113`）。POSIX 不变：`{bootId, pid, startedAt, processGroupId, jobName: null}`。两种形式由已有的 Job 与进程组互斥关系区分，不加 platform 字段。

### 创建次序

服务持久化 intent 与 startup → 独占创建根 Job（183 拒绝，加入任何进程之前设好 kill-on-close 与禁止脱离，D1 已有）→ 挂起创建启动进程并加入 Job → 取它的身份，写 marker → 恢复 → 把根 Job 句柄以不可继承方式复制进它 → 服务的创建句柄最晚在身份确认后关闭。

- marker 照现在一样在启动时由服务写入，内容是被创建进程的身份；与 POSIX 同一时机。
- 复制放在恢复之后：服务若恰在两者之间崩溃，它的句柄一关，kill-on-close 就收掉这个进程，按身份缺席结算；放在恢复之前，同一时刻崩溃会留下一个永远挂起、却撑着 Job 的进程。
- 在 marker 之后任何时刻崩溃，恢复都有记下的根：活着就接回，接不回就按名打开 Job 终止并观察全空，不在了就按身份缺席结算。在 marker 之前崩溃（进程还挂起着，没运行任何代码），与现在一样只剩启动意图，是 unknown。
- 有序重启与同契约安装不再杀掉执行，不需要在停止时等待未确认的执行。

### 确认与接回

- 执行的解释器（或它本身就是根）经现有 progress 报到。服务在这个边界上核对一次：报到的进程属于这个尝试的根 Job、内核里的创建时间与报的一致；记进 SQLite 的是根的身份（`blackboard/service/service.py:881`、`blackboard/store/store.py:1963、1995–2000`）。不在数据库事务里等子进程或发 RPC。
- 服务重启后，活着的执行向新服务重新报到，走 `verify_identity` 没有句柄的那一支（`execution_scheduler.py:146–147`），用同一条核对规则接纳；保留这一支，补一个单测。
- live 描述符、native journal 的文件名与 SQLite、marker 用同一个根身份；执行怎样得到它，在 D2b 的任务书里定。执行对自己的身份自查（`protocol/execution.py:120–121`）是边界之内的重复检查，删掉。
- 接回沿现有的连接事件观察（`execution_scheduler.py:143、184、324、393`），不加心跳、看门狗或第二个进程等待器；服务只在接回核对与停止时短暂打开原 Job，用完即关。

### 停止证据

现有判定（`process_identity.py:266–273`）保留，只删 boot 比较：根的完整身份（PID 加 FILETIME）缺席、原 Job 按名打开得到错误 2 或打开后没有成员，就是身份缺席；根还在而 Job 看不见是 unknown；Job 里还有成员就是还活着，停止时在同一句柄上终止并观察 `ActiveProcesses=0`。只有 intent 而没有 marker、错误 5、身份或成员查询失败，都是 unknown。停止已登记的根一律按名打开原 Job，不落到按 PID 杀的分支（`protocol/owned_process.py:90`、`process_controller.py:11`、`process_handle.py:21`）。

已登记的 Windows 根，其停止证据覆盖全部 native 成员与 native 创建窗口：`interruption.py` 的 `_stop_native` 在根已证明停止时不再因 native journal 缺失或含 pending 而判未确认。这样 Windows 上 native journal 若再没有读者，Windows 就不写它；POSIX 照旧。

### 存储形式与升级

- **`attempt_process_identities`**（`blackboard/store/db.py:209–218`）：`boot_id` 只有 POSIX 行必填，Windows 行为 NULL，与 `process_group_id`、`job_name` 的互斥写进同一个 CHECK。新建与升级共用同一份 DDL。
- **升级：** 真实看板里没有 Windows 身份的存量：日常看板的 923 行全是 POSIX，Windows 上没有日常看板；首发之前"升级基线写出的看板"只有开发者 macOS 上那一块。待做升级照新 DDL 重建这张表（`blackboard/store/upgrade.py` 的 `_rebuild_table`），POSIX 行原值复制，把旧列登记进 `RETAINED_COLUMN_PROJECTIONS`，安装核验就能证明值没变；旧形式的 Windows 行违反新约束，升级失败并回滚，不做转换与清理。样板看板与生成器不改：现有两行 macOS 身份就是这张表的存量。`test_upgrade_fixture.py` 里行数、rowid 与非数据库文件的严格断言保持原样。
- **文件形式：** POSIX 的 intent、startup、marker 的写入时机与内容、live、native journal 逐字节不变，升级不转换、不清理任何文件。Windows 上只有 marker 与 live 里的身份项去掉 `bootId`；intent 与 `execution-start.json` 不变（`jobName` 已在 startup 里）。
- 封存的内容、回执及其摘要绑定的内容不动；budget 没有进程身份，不变；`result-delivery.json` 的退役归 E；`meta` 只放 schema 与 secret。服务的 `control.json` 不加进程身份字段。

## 4. 测试

受影响测试由各批的微任务按改动列出、只跑那些；完整检查只在最后整合好的源码上跑一次。要点：

- Windows 进程与 Job（`protocol/test_windows_process_api.py`、`buddy/runtime/test_windows_process.py`）：复制句柄的次序与不可继承、恢复之后服务关闭自己的句柄；D1 的 183、加入先于恢复、同一句柄终止与全空的用例保留。
- 身份判定（`protocol/test_process_identity.py`）：根缺席加错误 2、Job 可开但根已退而成员残留、PID 复用、查询失败；FILETIME 用大整数。
- 确认与接回（`buddy/runtime/test_execution_process.py`、`blackboard/service/test_execution_scheduler.py`）：报到进程与根不同而同属根 Job 的成功例，不属于根 Job 或创建时间不符的拒绝例；重启后无句柄那一支的接纳；SQLite 提交失败。
- 恢复（`blackboard/service/test_execution_recovery.py`、`blackboard/store/test_interruption_native_tree.py`、`protocol/test_native_process_journal.py`）：服务重启后接回、只有 intent 时 unknown、Windows 根的停止证据覆盖 pending 或缺失的 journal、POSIX 的 pending 照旧占用。
- 服务启动与停止（`protocol/test_service_process.py`、`test_transport_attach.py`、`test_transport_errors.py`、`install/test_upgrade.py`）：挂起检查的通过、拒绝与查询失败；锁被持有而连不上不回答已停；所有者缺席时回答已停；启动错误的归属与 `probe` 就绪。
- 升级（`blackboard/store/test_upgrade_fixture.py` 等）：升级与新建的结构一致；POSIX 身份值逐行保留。

不为没发生过的情形加测试，不照搬实现写断言（例如"Windows 不再调用 boot_id"），不在产品里加测试钩子，不新造 Windows 上冻结进程的测试机制（`test_install_without_upgrade.py:342–344` 那类用例测的是平台无关的逻辑，在 Windows 上跳过即可）。

## 5. Windows 上的证据

**句柄实验（D2b 的门槛，Host 另派 Worker 先做；2026-10-24 成立）。** 13 组全部符合预期：复制句柄后服务替身正常退出与被强杀，执行在 1、10、60 秒时 7 个成员都在、心跳照常；不复制的对照组随服务退出全部消失；其余各项按名得到错误 2 的时间在 16–115 ms，全部已登记成员身份缺席。原始证据留在 Host 检出被忽略的 `tmp/` 里。原定的做法： 用替身脚本走产品实际的 venv 链，服务替身也像产品服务那样经转发器、以独立方式启动，执行替身经 cmd 等派生几个后代；观察者每次打开 Job 只做一次查询就关掉。各重复三次：

1. 复制句柄之后服务替身退出（正常退出与被强杀两种）：执行与后代按原 PID 与 FILETIME 继续运行，Job 仍能按名打开。对照组不复制句柄：服务退出后全部成员消失、按名得到错误 2，证明保活来自那份副本。
2. 杀掉真解释器，以及让它正常退出而后代还在：转发器随之退出，按名得到错误 2，全部已登记成员身份缺席；后者同时说明副本没有被继承。
3. 只杀转发器：真解释器随之结束，按名得到错误 2，全部成员缺席。
4. 新的服务替身按名打开 Job，核对根是成员，在同一句柄上终止并观察全空。

**Host 验收时另派 Worker 复跑的：**

1. 独立启动矩阵与挂起检查（D2a）。
2. 两次 SSH 登录：一次登录里的私有服务活着，另一次登录发 `stop` 与冷启动：得到连不上与可能在另一次 Windows 登录里的说明，锁与端点都在，没有第二个服务进程。
3. 私有服务上跑一个模型无关的 command 执行：重启服务，执行保持原 PID 与 FILETIME，被接回，只结算一次；再在两个 0.53.x 私有运行时之间做一次同契约安装与一次失败回滚，执行同样挺过去（若被运行时目录的忙文件挡住，记给阶段 F）。服务在执行确认之前重启，执行同样挺过去。
4. 已登记的根被强杀与正常退出：按名得到错误 2、根身份缺席，尝试结算，后续调度继续。
5. ARM64：CI 上跑完整检查，不单列原生实验。

**需要开发者在桌面上做的：** 从普通 PowerShell 与 VS Code 终端启动私有服务，挂起检查不误拒；关掉启动窗口，服务继续存在；执行与它经 cmd、pwsh、git 派生的后代都没有窗口。

## 6. 文档

D2 合入后由 Host 改：设计正文与 ADR-031 第 4、5 条（执行根与句柄归属、删去会话号）；`AGENTS.md` 的进程身份一句与 `docs/reference/architecture.md` 里 Windows 的身份与停止证据；operations 的 Windows 限制里加一句：跨 Windows 登录的执行不在支持范围内，记为 unknown，同一 Windows 会话里可以按 Job 强停。

## 7. Host 的删改与独立审查

初稿里删去或改掉的，及理由：

- 升级不转换、不清理 Windows 的旧身份与旧文件，样板看板不补 Windows 存量，测试的严格断言不放宽：真实看板里没有这种存量；为它设计的清理资格、待清理标记、备份覆盖与历史视图变空的分析一并不需要。
- intent 与 startup 不加会话与 Job 的绑定：只有 intent 时一律 unknown，这份绑定没有读者。
- 服务的 `control.json` 不加 FILETIME 与会话字段：没有读者，lifetime 锁已经是所有者的证据。
- 不为 `control.json` 缺失、损坏或不可读加分支：没发生过，而第二个服务已被 lifetime 锁挡住。
- Windows 实验里在确认次序上设五个可控暂停点：要在产品里加测试钩子；句柄改由被创建的进程持有之后，这些窗口大多不复存在。
- 跨 Windows 会话的 Job 对照、提权与普通终端交替接回、ARM64 的原生实验、Windows 上的升级复跑：前两者随会话号删去或不在支持范围内；ARM64 由 CI 的完整检查代替；升级的 SQL 由检查套件覆盖。

独立审查（另一个模型，双方互不看结论）与 Host 的结论在上面几条上一致；它另外找出了执行在确认之前被有序重启杀掉、永远 unknown 的缺陷（Host 在源码里核对过，由第 3 节的句柄归属解决），以及启动错误归属与就绪判断在 Windows 上永远对不上的具体修法（第 2 节）。
