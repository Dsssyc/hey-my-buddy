# 升级基点挪到 0.40.0 的说明

2026-10-16，微任务。依据 `AGENTS.md` 的 Invariants：2026-10-16 04:52Z 日常安装了 0.40.0（提交 `9baa0524`），schema 22 到 23 的升级（ADR-031）已在那块看板上跑过，算用掉了；升级基点改为写出那块看板的提交 `9baa0524`（0.40.0，schema 23），样板看板从它重新生成，用掉的换算与只为它存在的一次性代码在同一个改动里删除。开发者授权的写入范围是 `packaging/`、`src/hey_my_buddy/`、`tests/fixtures/`、`tests/python/` 与本说明文件。

## 新基点与生成

`packaging/generate-upgrade-fixture.py` 的基点常量改为 `BASE = 9baa0524a884fb335f64446a7bad8f27522082b8`、`BASE_VERSION = "0.40.0"`、`BASE_SCHEMA = 23`。populate 按基点的操作集重写：0.40.0 已退役 Worker 操作，`worker_register`/`worker_claim`/`worker_result` 换成 `BoardService.admit_execution(nonce, task_id=…)` 加命名操作 `execution_result`；micro 任务的 turn 记录仍手工构造，其 codex provenance 逐字段通过基点的真实校验器（`codex/native_run.py::validate_turn_provenance`），没有打补丁绕过；路由决策的冻结循环保留 `while requested.get("runId")` 的守卫，与旧基点下一样因唯一候选直接完成而不进入循环体。生成命令：`python packaging/generate-upgrade-fixture.py --python <prepared-python> --work-dir tmp/upgrade-base-0400 --output-dir tests/fixtures/upgrade-base`，其中 prepared-python 是一个已备好锁定依赖的只读解释器（c-two 0.7.4、pydantic 2.13.5、zstandard 0.25.0、portalocker 3.2.0）。结果：`tests/fixtures/upgrade-base/` 的 board.sql、metadata.json、operations.json、workspace-resources.json 重新生成，catalog.json 不变；生成器自带的 verify 与 `--verify` 复核都返回 `{"integrity": "ok", "foreignKeyViolations": 0, "schema": 23, …, "sourceSchemaMatches": true}`，四个 run 的状态仍是 delivered→concluded、accepted、failed→concluded、cancelled，工作目录全部保留在 `tmp/` 下。

## 删掉的代码

`src/hey_my_buddy/blackboard/store/upgrade.py` 整个模块删除：`upgrade_database`、`_load`、`_interruption_result`、`_view`、`_event` 五个函数与 `BASE_SCHEMA_VERSION`、`_REMOVED_VIEW_KEYS` 两个常量，它们只实现 schema 22 到 23 的这一种换算（重建 attempts 等三张表、投影 command 响应、转换 queueReason/interruptionEvidence/releaseEvidence、删除 worker 命令），没有第二个调用者。`install/upgrade.py` 删除 `retire_workers`、`remove_legacy_worker_state`、`retry_legacy_worker_cleanup` 三个函数：0.39.0 Worker 的退役协议写 `retire.request`、清 `workers/` 与 `worker-pool.json`、在安装与服务启动时重试，都只服务这一次跨 Worker 架构的安装；连带删除 `idle_snapshot` 的 `schema_version` 参数（只剩一种受支持的 schema 后它是死参数，`upgrade`/`recover` 的两处传参一并去掉，`recover` 里改回 `idle_snapshot(state)`）、`upgrade()` 里 `before['schema'] == 22` 触发换算的那一支（换为直接 `expected = idle_snapshot(state, event_head=before['eventHead'])`，仍做换算位置上的活体复读）、`detach()` 与 `recover()` 里对 `retire_workers` 的调用、journal 的 `legacyWorkerCleanup` 键（写它的三处）与结果里 `legacyCleanupError` 的三处展开，以及只为 `retire_workers` 存在的 `ExitStack` 导入。`blackboard/service/daemon.py` 删除 `_cleanup_legacy_workers` 方法及其在 `run()` 与 `_maintain()` 的两处调用。`install/skill_install.py` 删除安装前对 `retry_legacy_worker_cleanup` 的调用与同一结果键的展开。`blackboard/store/backup.py` 删除三处 `workers` 特判（sqlite_sequence 的 `NOT IN ('events','workers')`、指纹表的 `if name != 'workers'`、`exemptFingerprints` 里的 workers 项）与 `_verify` 接受 schema 22 备份的那一处：workers 表只存在于 schema 22 的看板，schema 22 的备份只可能由换算前的旧版本产生，都是这一次升级的回滚窗口专用。

## 删掉的测试

`tests/python/blackboard/store/test_upgrade_fixture.py` 删两条：`test_upgrade_converts_only_service_owned_fields_and_preserves_opaque_results`（逐项断言 `_view`/`_event` 的投影换算）与 `test_upgrade_failure_rolls_back_every_table_replacement`（断言 `upgrade_database` 的回滚），并删只为它们服务的 `assert_projected_delta` 辅助方法。`tests/python/install/test_upgrade_service.py` 删 `test_cleanup_refusal_keeps_private_commands_usable_and_retries_at_startup`（清理被拒时服务议题与启动重试，全在被删的重试路径上）。`tests/python/install/test_upgrade_archive.py` 删 `test_retired_worker_cleanup_failure_keeps_the_verified_runtime_available`（`remove_legacy_worker_state` 失败后的独立重试），setUp 里伪造的 `workers/`、`worker-pool.json` 与"备份不含 workers"的断言同删。`tests/python/install/test_upgrade.py` 删四条：`test_verified_cleanup_failure_releases_fence_and_retries_independently`、`test_pending_legacy_cleanup_does_not_intercept_regular_commands`、`test_cleanup_intent_survives_a_full_disk_without_another_marker`、`test_cleanup_cannot_delete_worker_state_after_rollback_or_before_schema_cutover`，全部只测 `retry_legacy_worker_cleanup` 的语义。`tests/python/blackboard/service/test_daemon.py` 删 `test_legacy_cleanup_issue_does_not_fence_service_and_resolves_after_retry`（`_cleanup_legacy_workers` 的议题去重与恢复）。合计删九条测试方法。

## 判断过而留下的通用部分

备份、预检、日志、恢复、skill 的发布与回滚、运行时的切换与保留、契约版本变化与否的两条路、同一 schema 的看板原样保留，全部未动语义；`upgrade()` 在备份之后仍复读一次 `idle_snapshot` 作为 `verify_started` 的基线，`recover()` 恢复备份后同样复读，删除换算不改变这些校验。`tests/python/install/test_backup.py` 的库存清单测试仍用 `workers/local/startup.json`、`worker-pool.json` 当普通状态文件名，那是"state 下任意文件都进备份"的通用行为，与清理无关，未改。`test_upgrade_archive.py` 的另外两条（两次同 schema 切换、失败回滚）保留，只去掉了 workers 断言。`test_upgrade.py` 的 `test_upgrade_refuses_older_or_unknown_schema` 在拒绝清单里加上 `'22'`，直接钉住新边界。`test_skill_install.py` 的 intact 安装测试去掉清理重试的段落，保留"不重启、不物化"的断言。`test_install_without_upgrade.py` 去掉 `retire_workers` 的拒绝补丁，测试本体不动。`test_current_core.py` 的 meta 写入者清单去掉已删模块的那一项。

## 仍只为 schema 22 存量存在的代码

挪基点之后还有两处读代码只为从 schema 22（或更早）写出的存量存在，按任务要求不删：`blackboard/tasks/workflow.py` 的 `_require_replayable_submission`，对 `request_fingerprint` 为 NULL 的行拒绝重放（这些行早于指纹存在之时，经换算原样保留在日常看板的历史里）；workflow/task 视图与 `cli/cli_views.py` 对 adapter 形态 micro 任务行的读取（`micro_task_json` 无 buddy 四元组、`workflow.execution_selected` 事件、turn 的 `context.buddy` 仅 adapter），由保留的 `test_retained_execution_rows_remain_readable_with_no_buddy` 钉住，当前提交路径已拒绝这种形态。事件历史里的 `worker.registered`、`attempt.claimed` 等旧事件种类没有专用读代码，由通用事件渲染展示，属 schema 23 存量。

## 测试

用检查运行器跑过：`tests/python/install/`、`tests/python/blackboard/store/`、`tests/python/blackboard/service/test_daemon.py` 与本改动涉及但不在前三个范围里的 `tests/python/cli/test_harness_test_safety.py`；前三个目录合并运行 28 个文件 287 条测试全部通过（`exit 0`，私有根自行回收），最后一个单跑 3 条通过。"升级样板看板、与新建的看板比对"改写为 `test_contract_cutover_preserves_stock_and_matches_fresh_structure`：基点 schema 与当前相同，无待做换算，契约切换后每一张表的每一行都原样保留，结构与新建看板逐项一致。生成器另用自身 `--verify` 复核过。未跑完整检查，由 Host 整合后统一跑。

## 与要求不一致的情形

没有。文档（本说明除外）、`skills/`、两份 README、`AGENTS.md`、`CONTEXT.md` 未动；存储形式、版本号与 schema 版本号未动；未用 `git stash`，未动分支与标签，未提交。
