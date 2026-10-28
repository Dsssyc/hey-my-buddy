# ADR-031 第 15 条的执行计划：停止以会话为界

依据：[ADR-031](../../decisions/031-one-process-per-execution.md) 第 15 条（2026-10-28 开发者定）与[调研记录](../../research/2026-10-28-descendant-process-containment.md)。目标是让 POSIX 上的停止证据覆盖根的整个会话与当时仍挂在父子链上的后代，删受管检出之前查工作目录，并给接入清单加一个探针。预期不改契约与存储形式；实现中发现要改，停下来问 Host。

## 现状（Host 读过的代码）

- `protocol/process_handle.py` 的 `ProcessHandle`：POSIX 上把进程组号定为根的 pid；`group_alive()` 只用 `killpg(pgid, 0)`；`terminate()` 按第 14 条的顺序请停、补 SIGTERM、反复 SIGKILL 直到组空；`shutdown_confirmed()` 要根已回收、组已空。
- 以新会话起根的地方：`buddy/harnesses/codex/native_run.py`、`claude/native_run.py`、`zcode/native_run.py`、`dsh/acp/launch.py`、`buddy/runtime/command.py`、`protocol/process_controller.py`、`codex/account_probe.py`，以及服务起执行进程的地方。每个根各自是一个会话的首进程。
- 恢复：`protocol/process_identity.py` 的 `inspect_process_identity`、`kill_verified_process_group`、`stop_orphan_process`。看板的 `attempt_process_identities` 记执行根的开机会话、根进程号、启动时刻与进程组（POSIX）或 Job 名（Windows）；原生 harness 的停止，POSIX 上由服务读执行写下的原生进程日志来证明（第 7 条）。
- 删受管检出有两条路：回收走 `blackboard/tasks/workflow.py` 的 `_reclaim_checkout`，受阻时报出具体原因、什么都不改；删除宏任务走 `blackboard/tasks/macro_task_deletion.py` 的 `_delete_workspaces`。
- 进程观察已经用 psutil（`process_observation.py`）。

## 微任务

### S1：停止以会话为界与删检出之前的保护

难。动的是停止证据，验收时 Host 在私有服务上做故障注入。

1. **`ProcessHandle`（POSIX）。** 判断“已空”的对象从进程组换成：同一用户、会话号等于根进程号的进程，加上同一次扫描里仍挂在根的父子链上的后代。逐个用进程号加启动时刻识别，发信号之前再核对一次。这一组全没了、根也回收了才算确认；扫描做不完（列进程或读会话号失败）就不算确认。第 14 条的停止顺序不变，只换“已空”的含义。原来按进程组的判断被取代，不留第二套。
2. **恢复。** `process_identity.py` 的三处用同一个判断：在同一次开机里，记录的根进程号就是会话号；根的身份已不在、且会话里没有剩下的进程，才算已停。原生进程日志里的根同样处理。进程号会被重用：会话还有成员活着时，系统会不会把这个号另作新会话的首进程，要找到依据（POSIX 对 `setsid` 的规定、macOS 与 Linux 的文档或头文件），写进说明；找不到依据就停下来问 Host。
3. **删检出之前的保护。** 两条删除的路在删之前都查：同一用户的进程里，有没有以这份检出或其中的目录为工作目录的。按目录身份比较，不按路径字符串的前缀。有就不删，报一个新的受阻原因，带上这些进程的进程号、启动时刻与可执行文件名，不带命令行与环境。沿用现有受阻原因的报告与展示；要为它加表或加列就是改存储形式，停下来问 Host。Windows 上不查，在代码注释里写明理由（读不到别的进程的工作目录，正被用作工作目录的目录本来删不掉）。新原因的界面文字走 i18n，中英文各一份。
4. **测试。** 用 `ProcessHandle` 复现报告里的几种后代：同组、只换进程组、另开会话（仍挂在父子链上）、两次 fork、`nohup`，这五种都要停掉并确认；`setsid` 加两次 fork 的守护进程是边界，照实断言它停不掉，而它的工作目录在检出里时，删除受阻。恢复路径同样覆盖：执行进程被强杀时，另一个进程组里的工具子进程由服务的孤儿停止停掉。故障注入：扫描失败时不确认；删除受阻之后，进程结束了就能删掉。
5. **顺带项：** 没有。这一块风险高，只做上面这些。

### S2：接入探针，工具子进程在哪个会话

中等，经路由。

为已有本机假模型探针的 harness（`tests/probes/` 下 Claude Code、Codex、DSH 的 `*_session_content.py`，ZCode 的 `zcode_*` 探针）各加一步：假模型请求一次 shell 工具调用，那个子进程报出自己的进程号、进程组号与会话号；探针判断它是在 harness 根的会话里、只换了进程组，还是另开了会话，并写明 harness 的形态与版本。不调用真实模型，不改产品代码。Host 据结果在 [harness 接入清单](../../reference/harness-integration.md)里加上这一项。

两个微任务改的文件不重叠，可以并行。

## 验收（Host）

- **S1**：读完差异，跑受影响的测试与共用的下游测试（执行、恢复、回收、删除宏任务、控制台的原因）；在空 HOME、经启动器起的私有服务上做故障注入：一个只换进程组的子进程不理 SIGTERM，停止要停掉它再确认；注入一次扫描失败，停止要记为不知道；一个 `setsid` 加两次 fork、工作目录在检出里的守护进程，回收要受阻并报出它；执行进程被强杀之后，服务的孤儿停止要停掉另一个进程组里的工具子进程。
- **S2**：读探针，亲自跑一遍，核对每个 harness 的结论与版本。
- 安装之前跑一次完整检查（开发者在本机跑计算期间，单进程）。版本号按改动是否触及契约在安装时定。
