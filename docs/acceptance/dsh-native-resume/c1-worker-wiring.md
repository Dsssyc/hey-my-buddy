# DSH 原生续接 C1：Worker 主循环四处接线测试

2026-10-08，微任务C1（经路由执行）。固定基线 `7391081f0a7b59c1583693f65cbb09d71687f97b`；唯一写入为本记录与新增 `tests/python/buddy/runtime/test_worker_live_wiring.py`，生产代码零改动。计划见 [dsh-native-resume](dsh-native-resume.md) 第一部分 C1 行；生产接线本身是 ADR-025 第五步已验收的行为，本微任务只证明真实 Worker 主循环确实接上了它。

## 结论

五项新测试覆盖 V-C1 至 V-C4，在受管检出上全绿（基线共四次全量运行，含交付字节状态的一次）；供体文件 `buddy/runtime/test_worker_invariants.py` 未改动，其 11 项编号两次运行全绿且编号集合相等；四处目标接线在四份全新源码副本中分别单点去掉后，实际测试逐项转红，失败均为业务断言（登记次数、同件重发、socket 文件清除与保留），没有以异常或装载错误代替断言。

## 覆盖与夹具

- V-C1 首次就绪登记：`WorkerLiveWiringTests.test_the_first_ready_tick_registers_the_live_binding_once`。实际续租 tick（`_forward_activity`）首次经 `handle_live_binding` 发现控制器就绪后，`_attempt_activity_binding` 调用 `worker.live.bind`，看板客户端恰好记录一次 `live_attach`；第二个 tick 不再重复登记。
- V-C2 续租后重新登记：`test_a_successful_renew_registers_the_same_binding_again`。合法 nonce 下 `_renew` 成功（无取消、未完成、无待对账）后 `_refresh_live_binding` 再次登记，且是同一个 attachment 对象（`assertIs`）。
- V-C2 对账后重新登记：`test_a_successful_reconcile_registers_the_same_binding_again`。续租响应要求对账、子进程句柄仍存活时，`_recover` 对账成功后再次登记同一 attachment。两种重新登记共用 `_refresh_live_binding`，两条路径各有独立用例与独立断言消息。
- V-C3 退出关闭 Worker 端点：`WorkerEndpointClosureTests.test_worker_exit_closes_its_own_c_two_socket`。真实 `Worker.run(max_iterations=1)` 主循环前后，Worker 自身 C-Two socket 文件从 `is_socket()` 到不存在；同时核对循环确实执行了一次注册与一轮认领。
- V-C4 控制器结束只清其捕获端点：`test_controller_end_through_execute_clears_only_the_captured_socket`。真实 `Worker.execute` 结束路径（finally）在确认 owned 进程组消失（真实子进程被终结并收割，`shutdown_confirmed()` 为真）后，由 `release_live_binding` 只删除该控制器的 captured socket 文件；公共 IPC 目录里另一个真实控制器的 socket 文件保留。此用例同时核对真实续租线程的 tick 完成了一次登记、结束路径恰好一次 `live_detach`。

夹具复用：`WorkerLiveWiringTests` 直接子类化 `test_worker_invariants.LiveActivityForwardTests`（不改该文件），继承其私有根、存储 run request、ready 文件与本地 channel 补丁；供体自身的五个用例以非可调用属性掩蔽，仍只在供体模块内运行。扩展只有三处：嵌套 `RecordingClient` 子类记录 `live_attach`/`live_detach`/`renew`/`reconcile`；`renewal()` 覆写在供体 worker 的 spool 里先种入合法 nonce（`new_nonce()`，64 位十六进制），使续租线程以合法身份登记；`LoopRecordingClient` 补上 `Worker.run` 需要的 `register_worker`/`claim`。V-C4 的控制器是 `tests/python/buddy/runtime/fixtures/live_runtime_peer.py` 的 controller 模式真实子进程（一个进程只有一个 socket 文件，因此 own 与 foreign 各起一个进程）；测试进程与控制器之间的观察调用走真实 C-Two IPC。没有任何用例直接调用 `WorkerLiveRuntime`，也没有放宽任何身份或停止 guard。

## 环境与命令形态

解释器与依赖用 Host 检出的 uv 开发环境，源码固定为受管检出：每次命令先清除继承的 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`、`BUDDY_RUNTIME`、`BUDDY_RUNTIME_IDENTITY`、`BUDDY_WORKER_STATE`、`BUDDY_WORKER_ID`、`BUDDY_AGENT_CREDENTIAL`、`BUDDY_AGENT_CREDENTIAL_FILE`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`、`PYTHONPATH`，再设置本任务私有 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`、`BUDDY_DEV_SOURCE=1`、`TMPDIR` 与 `BUDDY_CHECKS_TMPDIR`（都在任务根 t/ 下每次新名），`PYTHONPATH` 显式指向受管检出的 `src` 与 `tests/python`。命令形态：

`env -u <上述清除项> BUDDY_STATE_DIR=<t运行目录>/state BUDDY_RUNTIME_ROOT=<t运行目录>/runtime-root BUDDY_DEV_SOURCE=1 TMPDIR=<t运行目录> BUDDY_CHECKS_TMPDIR=<t运行目录> PYTHONPATH=<受管检出>/src:<受管检出>/tests/python uv run --project <Host检出> --frozen --offline --no-sync python -m unittest <模块或用例> -v`

原始命令输出逐运行保留在 `<task-root>/m/runNN-*/stdout.log`（变异源码副本、diff、编号清单也在 `<task-root>/m/`）。只运行受影响的两个测试文件；未运行完整检查，未启动用户安装的 harness，未调用模型。

## 变异核对（每处接线单点去掉，全新源码副本）

四份副本各自从受管检出复制 `src/`、`packaging/`、`pyproject.toml`、`uv.lock`（`resolve_runtime` 在源码模式下读取清单），再对 `src/hey_my_buddy/buddy/runtime/worker.py` 施加唯一一处删除；测试目录不变，`PYTHONPATH` 首段换成副本的 `src`。交付状态的证据运行：

| 变异 | 去掉的接线 | 退出码 | 转红用例与业务断言 | diff SHA256 |
| --- | --- | --- | --- | --- |
| `<task-root>/m/mut1c-first-ready-bind` | `_attempt_activity_binding` 中首次就绪后的 `worker.live.bind(...)` 调用 | 1 | V-C1 `0 != 1`（首次登记消失）；V-C2 两用例与 V-C4 因首登记是各自前置同样 `0 != 1`（V-C4 消息即"真实续租线程的 tick 完成登记"） | `d0cb1180…f59dea9` |
| `<task-root>/m/mut2c-refresh-live-binding` | `_refresh_live_binding` 内的 `worker.live.refresh(...)` 调用 | 1 | 仅 V-C2 两用例：续租路径 `1 != 2 : a renewed lease re-registers…`，对账路径 `1 != 2 : a reconciled attempt re-registers…`；V-C1 仍绿 | `db74cf53…0ee259c` |
| `<task-root>/m/mut3c-run-live-stop` | `Worker.run` finally 的 `self.live.stop()` | 1 | 仅 V-C3：`True is not false : the Worker main loop's exit path closes its own C-Two endpoint` | `df2a7350…84553dbc` |
| `<task-root>/m/mut4c-release-owned-endpoint` | `Worker.execute` finally 的 `release_live_binding` 清理块 | 1 | 仅 V-C4：`True is not false : the reaped owned controller's captured socket file was cleared` | `066c0ddf…31c76f47e` |

四份 diff 全文各自保留在副本目录 `worker-wiring-removal.diff`（上表 SHA 对应交付状态复跑 runs 29–32；同一副本更早的 runs 20–23 结果相同）。mut1 的红集包含三个共用"首次登记"前置的用例，属预期联动；其目标用例 V-C1 的失败即该接线的直接业务断言。

## 测试编号与集合相等

以 `unittest.defaultTestLoader` 真正装载的 `TestCase.id()` 为准（`<task-root>/m/run6-id-listing/ids.txt`，交付状态复核 `run26-id-listing-final/ids.txt`，两者逐字节相同，SHA256 `7cc76f50…b7e2f16`）。

- 新增 5 项（模块 `buddy.runtime.test_worker_live_wiring`）：`WorkerEndpointClosureTests.test_controller_end_through_execute_clears_only_the_captured_socket`、`WorkerEndpointClosureTests.test_worker_exit_closes_its_own_c_two_socket`、`WorkerLiveWiringTests.test_a_successful_reconcile_registers_the_same_binding_again`、`WorkerLiveWiringTests.test_a_successful_renew_registers_the_same_binding_again`、`WorkerLiveWiringTests.test_the_first_ready_tick_registers_the_live_binding_once`。
- 基线集合：供体模块 `buddy.runtime.test_worker_invariants` 的 11 项，文件对基线零改动（受管检出 `git status` 仅两个新增交付文件），运行两次（runs 3、24）全绿。
- 未变集合相等：两次编号清单与最终清单中供体 11 项逐项相同；本微任务不删除、不改名任何既有测试。

## 过程发现

两份前置探针（`<task-root>/m/probe1-c2-lifecycle`、`probe2-duplicate-name`）确认：进程内 `cc.register` 后 socket 文件即存在，`cc.shutdown` 后消失且可重新注册（新地址）；同进程两个端点共用一个 socket 文件，故 V-C4 的 own/foreign 控制器必须是独立子进程；重复注册名抛 `ValueError` 且被 `bind` 吞掉。

据此修复了一个自行发现的偶发失效：接线用例最初不在清理时停止各自 worker 的 live 运行时，三个用例的随机人名注册名有小概率（约 1.7%/次全量运行）碰撞，使后一个用例首登记为 0。修复为每个接线用例 cleanup 时 `renewal.worker.live.stop()`（注销名字，端点回收照旧），修复后基线三次全量（runs 17–19）与交付状态（run28）全绿；`<task-root>/m/run25-socket-audit` 以前后快照证明交付测试文件本身在共享 IPC 目录零新增 socket。供体文件自身的同名泄漏行为未改动，不属本微任务写入范围。

## 残留与回收

共享目录 `/tmp/c_two_ipc` 的 `.lock` 累积是 c_two 的机器级既有行为（报告时约 1111 个，早于本任务），未触碰。变异 mut4c 的两次证据运行（runs 23、32）按变异语义各必然遗留 1 个被杀控制器的未清 socket；供体模块运行（run24）按其夹具自身设计遗留 1 个；另 2 个早于本任务或落在修复前运行窗口。共 5 个 stray socket 留待 Host 按计划回收，本微任务未执行任何删除。一次性材料全部在任务根 `<task-root>`（m/ 为源码副本、diff、日志与清单，t/ 为各次运行的私有状态/运行时根），已向 Host 交付确切路径；本记录内位置一律用占位符。

## 边界

无板面守护进程、无 harness 二进制、无模型调用；真实 IPC 仅限本机 C-Two（Worker 自身端点、两个控制器对端子进程及其 socket 文件）。只运行了改动影响的两个测试文件，未运行完整检查套件；未安装、升级或改动任何日常运行时、配置、登录与凭据，未读取凭据内容。

## 续办：Host 驳回后的夹具修复（2026-10-08）

Host 固定上一轮输出（两路径内容、基线与唯一写入范围不变）后驳回一处范围内夹具缺陷，并核出停止边界与记录不符：其一，`test_controller_end_through_execute_clears_only_the_captured_socket` 在 mutation4 下虽红，但原 `retire_peer` 对已被 kill 的 owned 对端只关 stdio，不回收其创建时捕获的 socket，每次变异运行都在共享目录遗留对象；且不能按全目录计数、名字或日期推断归属。其二，原 `EndingControllerHandle` 的 `pgid=None`、`shutdown_confirmed()` 只查 leader 的 `poll()`，并未观察进程组，而记录声称"确认 owned group 消失"。本节为修复与复验记录；上文各节按当时事实保留，不作改写，其中"残留与回收"一节按时间窗推断 5 个 stray socket 归属的做法按 Host 决定作废——未在创建时捕获身份的对象一律不归属、不列为待清、不追查、不触碰（旧运行的 ready 文件随临时 state 根自动清理，其 owned socket 身份已不可按对象追认；run24 的遗留属供体模块自身夹具行为）。

夹具修复（仅测试文件，生产仍零改动）：控制器对端以 `start_new_session=True` 启动、各自领导独立会话与进程组，并由项目已有 `ProcessHandle(own_group=True)` 承载——`pgid` 在 leader 存活时由 `os.getpgid` 捕获，结束走 `terminate()`（对整组 SIGTERM、宽限后 SIGKILL），`shutdown_confirmed()` 是 `group_alive()` 的 killpg 探测观察，不以 leader 退出或缺 PID 代替；`EndingControllerHandle` 与执行器的 collect/cancel 均委托该真实句柄。断言之后、以 finally 收尾（成功/失败/变异都执行，且必在断言之后，不遮盖 V-C4）：owned 端点以本测试捕获的确切 descriptor 身份经生产原语 `cleanup_abandoned_socket` + `ConfirmedProcessGone(pid, exit_code, group_gone)` 清除；foreign 对端经自身正常 stop 注销（不干净时按 owned 同法兜底回收）；对端启动/读取失败由夹具当场终止并关闭，不丢句柄。不扫描、不计数、不手动删除任何共享目录内容。每次运行在保留材料里写 `endpoint-dispositions.jsonl`，逐对象记录 pid/pgid/exitCode/groupGone/socketPath/outcome/socketExistsAfter。

复验（按 Host 指示只跑本模块与原 4 处接线的全新变异副本 mut1d..mut4d；供体模块与完整检查未重跑）：基线 runs 34/35 全绿（5 项），owned 回收结局均为 `already-absent`（生产接线已删，夹具不再碰文件）、`groupGone=true`、foreign 正常 stop（returnCode 0）。mut1d 红集与此前相同（V-C1 目标 `0 != 1` 加三处同前置联动）；mut2d 仅 V-C2 两路径红（消息各指名 renew/reconcile）；mut3d 仅 V-C3 红；mut4d 仅 V-C4 红（`True is not false : the reaped owned controller's captured socket file was cleared`），且其 owned 回收结局为 `deleted`——红断言之后夹具按捕获身份清除了被遗弃 socket，变异运行不再遗留。`<task-root>/m/run40-postfix-evidence/socket-verification.txt` 逐对象核对 runs 34–39 的全部 12 个已记录 socket 路径，存留 0。5 项测试编号集合未变（`run40-postfix-evidence/ids-my-module.txt`，仅装载未执行）。交付测试文件 SHA256 `ff69d5da…1aafcf`；四份 mut*d diff SHA256 依次 `a5c5c843…76e727`、`e1b3339f…81fbe9cc`、`2127e8c7…f26a596b1`、`06a54713…05aaa409`；runs 34–39 原始日志 SHA256 依次 `13f9fd1c…0bfbb355`、`6112729d…f0d8a45`、`b7437897…971b6ac`、`2c6187de…5735e10`、`73978d2b…56d775`、`25e9bb5f…42b1de0`。命令形态与环境清理与上文相同；公共分支新合的 63e6248（时间窗常量）与本 5 项测试无依赖，未纳入本复验。
