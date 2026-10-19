# 缩短 buddy.checks 完整检查（按文件并行并去掉最慢测试的真实等待）

状态：Host 已于 2026-10-01 验收并合并。Worker 的实现经 Host 审查和一次独立审查后做了修正；「Host 验收」一节记录 Host 实际核对的内容，其余各节是 Worker 的报告，其中被修正的说法已按最终代码改写。

2026-10-01，以 `socu/buddy-core` 的 `7dd7003c7acf0c283d325ff4ecf6695ff87dbc10` 为基线，在独立 worktree 实现 `buddy.checks` 的按测试文件并行与最慢测试去等待。验证机为 Apple Silicon（10 逻辑核：8 性能 + 2 能效），解释器 Python 3.13.3，Node `v24.21.0`。修改范围：`src/buddy/checks.py`、`tests/python/test_blackboard.py`、`tests/python/test_parallel_dispatch.py`、`AGENTS.md` 与本记录；未改任何产品默认值、未引入第三方依赖、未调用模型、未联网。

## 基线测量

按现有方式完整跑一次 `time uv run --frozen python -m buddy.checks`：总墙钟 22:19.94，其中 Python 套件单进程 `unittest discover` 报告 `Ran 1828 tests in 1335.266s`（133 个测试文件，1 项跳过）；因 Python 套件失败，该轮未跑到 Node 部分。首轮两个失败均已归因，都不是产品缺陷：`test_host_preview` 的前端解析器回归失败是 console 开发依赖未安装（随后按规程执行 `npm --prefix apps/console ci` 修复）；`test_packaging` 的 cwd 独立测试断言两次启动的 `source:` 运行时身份不同，是基线运行期间源码被并行编辑（`src/buddy/checks.py` 属于运行时资产清单，其内容哈希进入身份），两次启动横跨了编辑时刻。此后所有运行再未出现这两项失败。

用等效方式采集逐测试耗时：`python -m unittest discover -s tests/python -v --durations 3000`，与 checks 子环境相同的净化环境与私有根，墙钟 22:28.76，1828 项全部通过、私有根清理干净，逐测试时长合计 1347.2s。原始日志在忽略的 `tmp/baseline-full.log`、`tmp/durations-full.log` 与 `tmp/durations-parsed.json`。

最慢 40 个测试（秒，unittest `--durations` 口径）：

| # | 秒 | 测试 |
| --- | --- | --- |
| 1 | 29.319 | test_blackboard.TestCrashWindows.test_a_killed_worker_keeps_the_surviving_child_uncertain_and_unretryable |
| 2 | 22.748 | test_repair_recovery.RealDaemonRestart.test_a_real_supervisor_keeps_its_child_and_reattaches_after_a_restart |
| 3 | 17.937 | test_parallel_dispatch.DaemonPoolTests.test_an_explicitly_stopped_slot_stays_down_until_explicitly_restarted |
| 4 | 17.757 | test_parallel_dispatch.DaemonPoolTests.test_a_desired_slot_that_disappears_is_restarted_by_the_pool |
| 5 | 16.886 | test_rpc_config.RealDaemonControlTests.test_waits_and_control_operations_share_execution_capacity_without_starvation |
| 6 | 15.463 | test_decision.DecisionFailureTests.test_worker_deadline_is_bounded_without_inventing_native_stop_evidence |
| 7 | 14.711 | test_blackboard.TestLifecycle.test_work_survives_a_real_daemon_restart_and_reconnects_to_the_same_result |
| 8 | 13.366 | test_parallel_dispatch.DaemonPoolTests.test_lowering_limits_retains_a_busy_surplus_worker_and_drains_it_later |
| 9 | 12.374 | test_worker_runtime.StagedWorkerRuntimeTests.test_staged_launcher_adds_runtime_worker_that_survives_stage_replacement |
| 10 | 12.278 | test_host_workflow_worker.HostQuotaRecoveryTests.test_quota_failure_seals_then_continues_a_new_configuration_in_the_same_checkout |
| 11 | 12.039 | test_host_workflow_worker.CrossHarnessQuotaRecoveryTests.test_failed_codex_reconstructs_in_dsh_with_bound_partial_output_and_message |
| 12 | 11.354 | test_claude.ClaudeAdapterTests.test_unverified_auth_status_fails_closed_with_bounded_reasons |
| 13 | 10.883 | test_workspace_lifecycle.LifecycleTestCase.test_an_accepted_helper_allocation_cleans_after_the_parent_is_accepted |
| 14 | 10.572 | test_workflow_real.ContinuationCheckoutReuseTests.test_sequential_helper_and_parent_edit_keep_one_checkout_and_latest_baseline |
| 15 | 10.329 | test_repair_recovery.TerminationTruth.test_an_execution_deadline_is_recorded_as_deadline |
| 16 | 10.107 | test_workspace_lifecycle.LifecycleTestCase.test_a_borrowed_run_never_authorizes_its_own_cleanup |
| 17 | 9.851 | test_first_install.SkillInstallTests.test_a_different_version_replaces_the_skill_without_leftovers |
| 18 | 9.662 | test_parallel_dispatch.DaemonPoolTests.test_scale_up_withdraws_the_retire_intent_and_restores_desired_slots |
| 19 | 9.326 | test_run.TestExecutionRecords.test_await_never_launches_work_and_honours_its_wait_window |
| 20 | 9.301 | test_host_workflow_worker.HostQuotaRecoveryTests.test_failed_goal_conclusion_reclaims_checkout_but_retains_partial_artifacts |
| 21 | 9.171 | test_workflow_real.ContinuationCheckoutReuseTests.test_parent_continuation_bases_on_the_helpers_sealed_commit |
| 22 | 8.556 | test_workspace.WorkspaceTests.test_ignored_caches_do_not_interfere_with_managed_input_or_output |
| 23 | 8.371 | test_workflow_real.PinnedHelperHandoffTests.test_helper_refs_are_frozen_into_the_parent_continuation |
| 24 | 8.208 | test_workspace_lifecycle.LifecycleTestCase.test_continuation_cleanup_removes_the_original_physical_allocation |
| 25 | 8.070 | test_workspace_lifecycle.LifecycleTestCase.test_continuation_cleanup_blocks_a_change_after_the_latest_seal |
| 26 | 7.829 | test_workflow_real.WorktreeContinuationReuseTests.test_parent_continuation_bases_on_the_helpers_sealed_commit |
| 27 | 7.793 | test_workflow_preparation.PreparationTests.test_partial_recovery_is_fenced_by_a_new_manual_input |
| 28 | 7.774 | test_workflow_real.TransferredCheckoutContinuationTests.test_parent_continuation_bases_on_the_helpers_sealed_commit |
| 29 | 7.750 | test_workspace_lifecycle.LifecycleTestCase.test_root_control_targets_an_independently_allocated_helper |
| 30 | 7.735 | test_workspace_lifecycle.LifecycleTestCase.test_storage_native_removal_resumes_after_interrupted_delete |
| 31 | 7.569 | test_packaging.CwdIndependentCliTests.test_the_launcher_is_cwd_independent |
| 32 | 7.488 | test_blackboard.TestPackaging.test_the_default_cold_start_installs_and_reports_the_stable_runtime |
| 33 | 7.381 | test_blackboard.TestDelivery.test_correlated_answer_survives_a_daemon_restart |
| 34 | 7.368 | test_workflow_worker.RealWorkerTurnTests.test_real_worker_imports_a_turn_and_seals_the_output |
| 35 | 7.309 | test_claude_worker.ClaudeWorkerTests.test_governed_worker_seals_only_a_proven_claude_result |
| 36 | 7.295 | test_host_workflow_worker.HostQuotaRecoveryTests.test_quota_failure_cannot_override_a_user_locked_configuration |
| 37 | 7.286 | test_parallel_dispatch.DaemonPoolTests.test_decision_completes_on_the_pool_with_a_free_total_slot |
| 38 | 7.235 | test_run.TestExecutionRecords.test_execution_cancel_then_acknowledge_records_a_reviewed_outcome |
| 39 | 7.221 | test_packaging.CwdIndependentCliTests.test_the_launcher_reaches_the_private_cli_from_an_unrelated_cwd |
| 40 | 7.156 | test_workspace_lifecycle.LifecycleTestCase.test_storage_apply_reuses_workspace_protection_and_current_revision |

最慢 15 个测试文件（该文件内逐测试时长合计）：

| # | 秒 | 文件 | 测试数 |
| --- | --- | --- | --- |
| 1 | 187.5 | test_workspace_lifecycle | 34 |
| 2 | 106.4 | test_workflow_preparation | 23 |
| 3 | 96.7 | test_parallel_dispatch | 27 |
| 4 | 75.1 | test_blackboard | 57 |
| 5 | 65.2 | test_workflow_real | 11 |
| 6 | 65.0 | test_workspace | 25 |
| 7 | 64.3 | test_scope_recovery | 18 |
| 8 | 42.3 | test_run | 7 |
| 9 | 40.9 | test_host_workflow_worker | 4 |
| 10 | 40.9 | test_repair_recovery | 10 |
| 11 | 31.2 | test_decision | 39 |
| 12 | 27.5 | test_zcode | 25 |
| 13 | 23.8 | test_claude | 42 |
| 14 | 22.9 | test_codex | 41 |
| 15 | 22.5 | test_console | 19 |

## 按测试文件并行

`src/buddy/checks.py` 新增：`python_test_modules` 按与 `unittest discover` 相同的规则枚举测试模块（顶层 `test*.py`，以及带 `__init__.py` 的嵌套包；Worker 提交时为 133 个模块，全部在顶层）；并行模式下每个文件一个子进程 `python -m unittest -v <module>`（工作目录、净化环境与原来完全一致），并各自获得私有子根 `<checks根>/pNNN`——`TMPDIR`、`BUDDY_CHECKS_TMPDIR`、`BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT` 全部指向该子根，状态、运行时与临时目录互不共享。子根仍在 `/tmp` 短根之下（macOS `sun_path` 104 字节预算），inquiry 套接字本就有按路径长度的回退，深度增加 4 字节不改变行为。

并发数 `default_jobs()` 取 `max(1, min(4, CPU−1))`（本机 10 核取 4），可用 `--jobs N` 参数或 `BUDDY_CHECKS_JOBS` 环境变量调整（参数优先）；非法值立即报错。`--jobs 1` 走提取出的原串行路径：单进程 `unittest discover -v` 直流输出、Node 套件随后，与原实现逐字节等价。基线最慢的 20 个文件（`SLOWEST_FILES_FIRST`，指向本记录）先调度，其余按字母序补齐；该表只是调度提示，不改变跑哪些测试。Node 套件与 Python 并行，占独立 `node` 子根。失败报告保留并增强：失败子进程的完整 stdout/stderr 原样打印（unittest `-v` 列出 module.Class.test），并按文件名汇总失败列表。整根 teardown 完全不变：全部子进程结束后仍走协作停止、生命周期锁与只读进程观察的残留断言，覆盖所有子根。仅用标准库（`threading`、`concurrent.futures`、`subprocess`），无新依赖；被中断时不再启动排队中的文件，终止在飞子进程（超过 10 秒宽限则杀掉）后进入 teardown（中断处理由 Host 修正，见「Host 验收」）。

## 去掉最慢测试中的真实等待

四处盲等改为事件等待或测试专用短节奏。`test_blackboard` 崩溃窗口测试的 `sleep(3)`：等待替身 supervisor 持有生命周期锁，并写出两次晚于其启动时刻的不同心跳（心跳在启动对账之后和每一轮认领之后写出，两次心跳证明启动对账之后至少完整跑过一轮认领）。同文件打断等待客户端的 `sleep(1)`：轮询 wait 资源 `wait_capacity` 直到 `admitted ≥ 1`，证明客户端确实被接纳在等。`test_parallel_dispatch` 歧义启动意图测试的 `sleep(2.5)`：记录每一轮退休对账的返回值，等到第二轮出现且前两轮都返回未解决的启动意图，再断言线程存活、意图仍在。同文件退休竞态测试的 `sleep(2.5)`：测试内 `patch` `_Renewal.CANCEL_POLL_SECONDS=0.25` 加 0.75 秒窗口，窗口内取消观察轮次约从 1 次增至 3 次，产品默认值不动。Worker 最初提交的第一处只等到替身启动，第三处的等待条件在夹具写入时就已成立，两处都弱于原测试，已由 Host 改为上述写法。

以下最慢测试的真实等待在本次范围内无法去除，原因如下：`test_parallel_dispatch`「降低上限保留繁忙盈余」的 2.5 秒观察对象是真实 supervisor 子进程，其节奏常量（`CLAIM_IDLE_SECONDS`、`CANCEL_POLL_SECONDS`）在产品源码中且无构造接缝，本次 writeScope 不含产品文件，进程内 patch 也够不到子进程；`test_rpc_config` 容量测试的 `WAIT_MILLISECONDS=3500` 被断言要求等待真实在飞满窗口（`elapsed ≥ 窗口`），且窗口为 2.0 秒控制预算留余量，缩短即放松断言；`test_decision` 截止测试的 `timeoutSeconds=5` 已是测试专用短时限，再缩短会贴近真实启动延迟、在并行负载下引入偶发；最慢 40 中其余时间由真实进程生命周期构成（守护进程与 supervisor 的启动/重启/重连、真实安装与打包、真实前端解析器），不存在可注入的时钟。

## 稳定性验证

Worker 在其提交 `957ea21` 上的结果：并行模式（默认 4 worker）连续 3 次完整通过：第 1 次 6:10.82（134/134 子任务全 ok，最慢文件 test_workspace_lifecycle 183.4s，接近其串行 187.5s，说明争用很低）；第 2 次 6:10.22；第 3 次 6:23.55。串行模式 `--jobs 1` 完整 1 次通过：22:30.26 墙钟，Python 报告 `Ran 1828 tests in 1320.289s`、`OK (skipped=1)`，Node 套件随后通过。运行期间未出现任何偶发失败，无需重试。原始日志在忽略的 `tmp/parallel-*.log` 与 `tmp/serial-1.log`。整体从基线 22:19.94 缩至约 6 分10 秒（约 3.6 倍）；由于基线那轮 Python 失败未跑 Node 部分，同口径比较应以含 Node 的串行 22:30.26 为准。

## 文档

`AGENTS.md` Verification 一节新增一句说明并行参数：Python 套件按测试文件各起一个私有子进程，并发数按 CPU 取保守值，`--jobs N` 或 `BUDDY_CHECKS_JOBS` 可调，`1` 恢复原始串行单进程运行。

## Host 验收

Claude Code Host 审查了 run `5b72e792`（ZCode GLM-5.3 max，由路由选择）的交付，并委派了一次独立只读审查（run `67180491`，Codex GPT-6.1-Sol high，由路由选择）。Worker 的提交 `957ea21` 以 `afe376f` 拣选到 `socu/integration-0.26`，Host 的修正是 `fe4f43f` 与 `9849d9e`。

覆盖等价：Host 比较了 `unittest discover` 与按文件加载得到的测试 ID，两者完全相同（当时 1,835 个、133 个模块，无重复、无加载失败）。这一比较现在是常驻测试 `test_checks_parallel.ScheduledModulesTests`，嵌套包按 discover 的规则调度。并行汇总行给出实际运行的测试总数；最终并行与串行都是 1,850 个 Python 测试（跳过 1 个）和 165 个 Node 测试。

失败传递：Host 向运行器注入断言失败、导入错误、没有测试的文件（退出码 5）、被 SIGKILL 的子进程和失败的 Node 套件，每一种都使整次运行失败并列出所属文件；只含通过文件时退出码为 0。没有报告测试数的文件、没有产生结果的已调度文件、报告 0 个测试的 Node 套件和空的 Node 测试目录也都判为失败。这些情形由 `test_checks_parallel` 的 15 个测试覆盖。

审查发现与修正。第一，被中断时排队的文件仍会启动，子进程在启动与登记之间可能被漏掉，被测试遗留的进程占住输出管道时运行器会一直等待；现在中断检查、启动与登记在同一把锁内，排队任务被取消，输出由读取线程收集，子进程退出后最多再收 2 秒。第二，歧义启动意图测试的等待条件在夹具写入时已成立；Host 用变异验证确认：把退休循环改成有未解决证据也退休后，Worker 的版本在 0.12 秒内通过，修正后的版本失败。第三，汇总解析在 Python 3.14 的彩色输出下漏计跳过数；解析前先去掉终端样式。Host 复查其余三处等待时还发现崩溃窗口测试只证明了替身启动，改为等待两次心跳。退休竞态测试的 0.75 秒窗口保留：其后“任务最终完成”的断言仍会抓住被错误取消的尝试。去掉中断检查或恢复等待管道的变异都使新增的中断测试失败。

中断实测：对真实全量运行只向运行器发送 SIGINT（已完成 16 个文件时），运行器 31.5 秒后退出，私有根下原有的 14 个进程全部消失，teardown 判定干净并删除私有根。运行器收到 SIGTERM 时不做清理，与串行模式相同，未处理。

最终验证在 `9849d9e` 上进行：并行 3 次全部通过，379、376、376 秒，每次 1,850 个 Python 测试（跳过 1 个）、134/134 个文件、Node 165 个；串行 `--jobs 1` 1 次通过，1,343 秒，`Ran 1850 tests`、`OK (skipped=1)`、Node 165 个通过。全程没有偶发失败。原始日志在忽略的 `tmp/checks-fix-*.log`。
