# ADR-025 第三、四步铺开前准备（微任务 3-P1）：三处测试修正

2026-10-06，Worker 由 ZCode 承担。输入基线 `90900d709d4d5eaef806214bfdea05c95529038a`（第二步已由 Claude Code Host 验收并合入 `886836e`，含随后补充检出回收说明的 `4a8dfee`），在受管 worktree 内直接修改，未新建或切换分支。本微任务补齐第二步 Host 验收"带到后面的事项"中的两处测试与一处测试清理：运行模块在原生进程组停止未确认时上报"未知"的见证、实时通道绑定对身份每个组成部分逐项拒绝不一致的覆盖、问询传输测试对自己所建目录的回收。仅改测试与本记录；生产源码零改动，发现的生产缺陷在文末报告。

## 范围与清理

改动只落在 writeScope 四条之内：`tests/python/buddy/harnesses/zcode/test_native_run.py`、`tests/python/buddy/roles/test_role_live_seam.py`、`tests/python/protocol/test_inquiry_transport.py` 与本记录，`git status` 为证；生产源码、角色、公共值与注册表零改动。任务根 `/private/tmp/a253p-a4a03lnd`（Host 创建并登记）：`t/` 承载每次运行的 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR`（含基线泄漏证据目录与 uv 自建的缓存锁文件），`m/` 放一次性材料（两份变异源码副本、各次运行日志、前后测试 ID 清单）；本 Worker 未手动删除任何对象、未使用通配符、未动其他会话材料。修正三新增的正是 fixture 自身生命周期的收尾，属任务说明中的 fixture 例外；普通 fixture 与检查运行器自建对象照常自动收尾。

## 修正一：运行模块停止未确认 → unknown 的真路径见证

新增 `FastSeamTests.test_an_unconfirmed_native_group_stop_reports_unknown_not_gone`：经真实 `native_run.run` 走完整运行（真实伪造 app-server 子进程、真实 `RunRequest`、真实观察规则），只在最窄的停止观察点 `ProcessHandle.shutdown_confirmed` 注入不确认（记录每次被调的 handle 并返回 `False`）。生产停止路径原样执行：`_halt_owned_group` 关 stdin、等待收割 leader、因首个确认被拒而真实 `terminate` 该进程组；断言 `RunResult.stop_evidence.native.group_state == "unknown"`、`observation_basis == "owned-process-group"`、`started` 为真、`exit_code` 与 `leader_exited` 在场（leader 事实已收割，但无确认不得报告 gone——未知不是已停止），且已结算的回合因此被拒（`end.status == "error"`、`reason_code == "native-shutdown-failed"`）、停止层记录真实信号（interrupt `requested`、basis `owned-group-signal`）、本次运行的一个 handle 恰被观察两次。无孤儿证明：补丁作用域结束后，用真实（未打补丁的）`shutdown_confirmed` 复核被捕获的 handle——leader 已 `poll` 到返回码、整组确认消失、`group_alive()` 为假，即实际模拟子进程由正常停止链最终收尾。不测结果构造器，不修改结果喂给外层。

## 修正二：实时绑定逐项拒绝身份每个组成部分的不一致

`stored_run_request` 入口新增 `StoredRequestTests.test_one_request_identity_component_mismatch_refuses_alone`：先写出与已验收角色启动完全一致的存储关系（公共请求、指名 invocation 与 governed turn input 的私有控制、真实的 turn input 文件），再对 task_id、attempt_id、generation、turn_id、invocation_id、input_sha256 逐项以有效类型值只改写请求侧的这一个组成部分——真实 turn input 文件与其正确摘要不动、其余组成部分与控制全部不变——断言该次单独不一致即返回 `None`，不存在另一个失败掩盖比较缺口的可能；harness 亦单列（控制仍指名 zcode、请求改称 codex 时拒绝）。`handle_live_binding` 入口新增 `HandleBindingTests.test_each_held_identity_component_and_harness_must_match_the_request`：持有方收到的身份逐项单独不同（同样有效类型变化、其余成分相等）时绑定拒绝为 `LIVE_UNAVAILABLE`，控制 harness 与存储请求 harness 不一致同样拒绝。两条入口均未 mock 整体、未重复既有测试（既有用例覆盖 invocation 与经改 turn input 文件的 turn，本条覆盖请求侧逐成分）。

## 修正三：问询传输测试回收自己的短 socket 目录

`short_socket_dir` 由裸 `tempfile.mkdtemp`（无收尾，模块单独运行在私有临时根留下 8 个目录）改为 `TransportTests` 上的方法：标准库 `tempfile.TemporaryDirectory` 生命周期，`addCleanup` 注册其 `cleanup`；目录清理在测试随后注册的 socket 与服务线程清理之前登记，unittest 后注册先执行的顺序保证先停线程/关 socket、后删目录。`test_a_non_json_reply_is_invalid_and_a_silent_socket_times_out` 补上缺失的 `listener.close` 与 `silent.close` 注册，使失败路径上顺序同样成立；其余测试本就以内联 join/addCleanup 收尾。模块内无其他 `short_socket_dir` 引用，仓内无别处导入该符号。

## 变异证明（隔离副本）

变异只做在 `<task-root>/m` 下的两份私有源码副本（含本批测试改动的完整工作树快照）；每次运行先经 `PYTHONPATH` 前置验证所导入模块确属副本（打印 `__file__`），再以同一锁定解释器跑聚焦测试。受管 worktree 生产源码零改动（`git status` 为证）。

| 组 | 变异做法 | 变异文件 SHA（sha256） | 红 | 日志 |
| --- | --- | --- | --- | --- |
| 停止未确认 | `native_run.py` 结果组装处 `group_state="gone" if state.shutdown else "unknown"` 强制为 `"gone"` | `92b20354d8443bc20860832ebed00782c7560724711ec1029d5c3b637d165ca5` | 新增测试失败，退出码 1，`AssertionError: 'gone' != 'unknown'`，失败点正是 group_state 断言 | `<task-root>/m/mut1-run.log` |
| 移去 attempt_id 比较 | `roles/live.py` 的 `stored_run_request` 校验链删去 `request.identity.attempt_id != turn_input.get("attemptId")` 一行 | `a990f228c8f83cf634b09cc73dc09f386cf4a139dfdfd55a9bc41e90bdf5ff3d` | 新增测试失败，退出码 1，唯一失败子项 `(component='attempt_id')`：`assertIsNone` 收到完整 `RunRequest`（attempt_id='other-attempt' 被放行） | `<task-root>/m/mut2-run.log` |

worktree 基线源码同两文件 SHA：`ad595cfb…`（native_run.py）、`b78f98e4…`（live.py）。其余逐项覆盖与生产代码逐成分比较链一一静态对应（`stored_run_request` 的 task/generation/turn/digest/invocation/harness 六项、`handle_live_binding` 的整体身份等值加 harness 比较），未再逐项变异。

## 独立模块运行清理证明

以专属 `BUDDY_CHECKS_TMPDIR=<task-root>/t/probe-baseline` 单独运行 `protocol.test_inquiry_transport`（修正前）：8 个测试全过，目录留下恰 8 个 `buddy-transport-*` 目录（ok、missing、refused、internal、foreign、bad、big、clamp 各一），与第二步 Host 验收记录一致。修正后以同法单独运行（`<task-root>/t/probe-fix3` 与最终 `<task-root>/t/probe-final`）：8 个测试全过，目录内条目数为 0——不依赖整套运行器删除私有根。三模块连续同根运行后同样为 0。

## 测试编号

三个测试模块由 `unittest.TestLoader` 实装枚举：改动前 52 个 ID（36+8+8），改动后 55 个（37+10+8）。新增 3、改名 0、删除 0；未列出的 52 个 ID 前后集合相等（清单在 `<task-root>/m/ids-before.txt`、`ids-after.txt`，集合差分见运行输出）。仓内无其他模块导入这三个测试模块，无连带编号影响。

| 处置 | ID |
| --- | --- |
| 新增 | `buddy.harnesses.zcode.test_native_run.FastSeamTests.test_an_unconfirmed_native_group_stop_reports_unknown_not_gone` |
| 新增 | `buddy.roles.test_role_live_seam.StoredRequestTests.test_one_request_identity_component_mismatch_refuses_alone` |
| 新增 | `buddy.roles.test_role_live_seam.HandleBindingTests.test_each_held_identity_component_and_harness_must_match_the_request` |

## 实际验证命令与数量

每次运行镜像检查运行器的子进程环境（`env -i` 重建、清除继承的 `BUDDY_*` 运行时/Worker/凭据变量与 `VIRTUAL_ENV`/`UV_PROJECT_ENVIRONMENT`，`PYTHONPATH` 先 `<worktree>/src` 再 `<worktree>/tests/python`，`BUDDY_DEV_SOURCE=1`，私有 `BUDDY_STATE_DIR`/`BUDDY_RUNTIME_ROOT` 由各测试 fixture 自建），显式 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 指向 `<task-root>/t` 的专属探针目录，命令形如 `.venv/bin/python -m unittest <模块/用例>`；锁定环境为 `uv run --frozen` 自 `uv.lock` 建立的 worktree `.venv`（gitignore 内，不属交付）。

| 命令对象 | 结果 | 日志 |
| --- | --- | --- |
| `buddy.harnesses.zcode.test_native_run`（改动后整模块） | 37 全过 | `<task-root>/m/final-native.log` |
| `buddy.roles.test_role_live_seam`（改动后整模块） | 10 全过 | `<task-root>/m/final-live.log` |
| `protocol.test_inquiry_transport`（改动后整模块，独立运行） | 8 全过，专属临时根零遗留 | `<task-root>/m/final-inquiry.log` |
| `protocol.test_inquiry_transport`（修正前基线，独立运行） | 8 全过，遗留 8 目录（作为前证） | — |
| 变异一副本上新增停止测试 | 1 失败（`'gone' != 'unknown'`） | `<task-root>/m/mut1-run.log` |
| 变异二副本上新增存储请求测试 | 1 失败（子项 `attempt_id` 被放行） | `<task-root>/m/mut2-run.log` |

未跑完整检查、控制台、打包、安装版 harness 与模型调用，符合微任务边界；生产源码未动，故无其他受影响套件需要连带聚焦（三个模块亦无外部导入方）。

## 生产源码核对结果（报 Host）

未发现需要改动生产源码的缺陷：停止未确认时上报"未知"、实时绑定比较完整身份（含 attempt_id 逐成分）这两处生产行为本就正确（与第二步 Host 验收"两处没被抓住的，代码本身是对的"一致），本微任务只补见证与清理；问询传输的目录遗留是测试 fixture 自身的问题，非生产缺陷。无公共接口缺口需要 Host 裁定。
