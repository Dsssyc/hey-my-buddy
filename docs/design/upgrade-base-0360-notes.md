# 升级基点移到 0.36.0

2026-10-15，本次修改基于 `202f4df2`。新的升级基点是 `6b385e53bf24e462b7c1534efcd77698b2c4729a`，版本 0.36.0，schema 20；依据是开发者的日常安装已经用掉 schema 19 → 20 的升级，见[安装记录](../acceptance/install-0.36.0.md)。当前产品版本、协议版本、schema 版本及 schema 20 的 DDL 均未改动。

生成命令为 `<prepared-python> packaging/generate-upgrade-fixture.py --python <prepared-python> --work-dir tmp/upgrade-base-0360 --output-dir tests/fixtures/upgrade-base`；`<prepared-python>` 代表 Host 指定的既有解释器，生成时也用它提供依赖，没有安装依赖或改写其环境。生成器通过 `git archive` 读取基点，在私有目录里从基点源码运行进程内的命名操作；没有启动服务、harness 或模型，没有读取账户。原始生成材料保留在被忽略的 `tmp/upgrade-base-0360/`。

生成命令退出码为 0：SQLite 完整性为 `ok`，外键违规为 0，schema 为 20，53 张产品表，42 条事件，源码 schema 指纹匹配；四个微任务的状态为 `concluded`、`accepted`、`concluded`、`cancelled`。生成器重新写出了 `tests/fixtures/upgrade-base/` 的五个文件，其中 `catalog.json` 内容未变。`metadata.json` 记录基点、依赖版本、生成器与源码文件的指纹、生成命令和私有路径绑定方式；新 `board.sql` 的 SHA-256 是 `deb8d19b694f5473cec3180ad161193c944bc75c507820df61c7dbea0557938a`，schema 指纹是 `7eb1c6428288c2835b5cdcbfc0c8e4cca8419718cb21c37f4ddfa709d3d85b54`。

删除了 `src/hey_my_buddy/blackboard/store/upgrade.py`：共 14 个生产函数与 2 个模块常量。`migrate` 是唯一的 schema 19 → 20 离线事务入口；`_fresh_ddl`、`_rebuild` 只供它按 schema 20 重建旧表；`_spec`、`_task`、`_turn`、`_run` 只转换旧的时长字段、指纹和委派字段；`_continuation` 只转换旧续做授权字段；`_stop_origin`、`_legacy_stops`、`_attempt`、`_termination`、`_receipt_object` 只换算旧停止记录、服务回执与事件；`_health_record` 只补齐旧健康观测的存储形状。`BASE_SCHEMA_VERSION = 19` 与该模块的 `DEFAULT_EXPECTED_SECONDS = 1800` 都只服务于这次换算，一并删除；`protocol/schemas.py` 中当前输入契约仍在使用的同名默认时长常量未动。

`install/upgrade.py` 删除了对旧基点 schema 的接纳、只为 schema 19 的不确定执行提供的开机前存量放行，以及调用换算的 `migrating`、`migrated` 分支。安装现在只接受 schema 20，同 schema 切换直接以已校验备份的原始快照作为验证基线。`blackboard/store/backup.py` 删除了对旧基点常量的导入和 schema 19 备份的接纳；备份格式、清单、哈希、数据库指纹、完整性、外键与 schema 一致性检查保留，当前 schema 的 `Database.initialize()` 检查改为无条件执行。

删除了 `BoardStore.recover_interrupted_attempts`、`interruption.recover`、`interruption._snapshot` 的 `legacy_only` 参数与相应筛选分支：唯一启用它的调用是已经删除的离线换算入口。三个函数本身保留，普通恢复仍扫描所有不确定执行，并按原有停止证据结算。

`tests/python/blackboard/store/test_upgrade_fixture.py` 删除了 15 项专用测试与 2 个测试辅助函数。以下逐项说明删除依据；测试辅助函数 `native_bindings` 只供已删除的换算成功和失败测试使用，`assert_legacy_stop` 只供旧取消记录的归因换算测试使用。

`test_upgrade_makes_health_observation_status_explicit_in_stock_and_replays`、`test_upgrade_marks_actual_account_errors_failed_and_unchecked_health_has_no_probe` 只测试旧健康记录、缓存回执和事件的形状换算。

`test_conversion_recovers_only_proven_preboot_unknown_stock` 只测试换算期间为旧不确定执行补写存量行和离线空闲放行；schema 20 正常恢复对存量行的处理另有测试，继续保留。

`test_upgrade_leaves_native_bindings_byte_for_byte_unchanged`、`test_failed_upgrade_leaves_native_bindings_byte_for_byte_unchanged` 直接调用已经删除的换算函数，验证换算成功或失败不改写 native 绑定文件。

`test_upgrade_removes_stored_scopes_and_preserves_credentials` 只测试重建旧凭据表以移除 `scopes_json`；`test_upgrade_converts_continuation_authority_to_kind` 只测试旧续做记录换算后具有新字段与约束。

`test_migration_converts_timeout_stock_and_preserves_turn_input` 只测试旧任务时长、回执指纹的换算及原始输入保留；`test_migration_freezes_router_budget_without_changing_router_policy` 只测试旧 Router 时限换算。

`test_migration_rolls_back_invalid_source_without_consuming_base` 只测试 schema 19 → 20 换算事务的回滚；`test_old_worker_deadlines_are_cleared_without_rewriting_native_evidence` 只测试旧 Worker deadline 回执的换算。

`test_migration_uses_matching_legacy_cancellation_actor`、`test_migration_attributes_unfinished_cancel_request_without_terminal_event`、`test_migration_does_not_claim_an_unconfirmed_legacy_stop` 只测试旧 `user-cancel` 记录的归因换算。

`test_installed_upgrade_restores_schema19_backup_when_conversion_fails` 只测试被删除的换算调用失败时恢复 schema 19；当前 schema 切换失败后的恢复测试仍保留在 `tests/python/install/`。

保留并调整了样板的源码、schema、历史哈希与私有路径绑定测试，以及 `test_schema_upgrade_preserves_stock_and_matches_fresh_structure`：基点与当前 schema 同为 20，不再期待数据被换算，改为核对数据指纹和数据库字节完全相同，继续检查备份、历史文件、设置、偏好、任务与新建看板结构。`test_current_core.py` 只移除了已经不存在的换算模块在 `meta` 写入者白名单中的条目；`test_upgrade_archive.py` 与 `test_upgrade_service.py` 保留全部测试，只删除准备阶段的换算导入和调用；`test_upgrade.py` 的不支持 schema 测试新增了对已用掉的 schema 19 的拒绝核对。

判断后保留的通用部分包括：备份的预检、白名单与发布恢复；安装的锁、重复空闲检查和实际停止证据；升级日志、失败原因和恢复流程；skill 的暂存、发布、回退和一致性校验；运行时身份校验、切换、恢复与回收；同 schema 看板和历史文件的保留；`verify_started` 对 schema、表清单、数据指纹、运行参数及源码泄漏的验证。升级日志的 `expected` 快照仍供通用的启动验证与中断恢复使用，没有随换算分支删除。

移走基点后，仍只为来自 schema 19 的存量保留的内容有：`blackboard/store/db.py` 的 `attempt_legacy_starts` 表及说明；`blackboard/tasks/interruption.py` 的 `LEGACY_BOOT_MARGIN_SECONDS`、`legacy_boot_evidence`，以及 `_snapshot` 对该表的读取、`_evidence` 在没有完整身份时使用显式存量时刻的分支、恢复过程对 `legacy_start` 的传递和 `_settle` 写入的 `interruptionEvidence.legacyStartedAt`；`protocol/process_identity.py` 的 `boot_started_at` 当前生产调用点只有上述存量规则。这些内容没有删除或改变含义：只有显式标记的执行开始于当前开机一小时以前时，规则才给出 `boot-changed`；否则仍然未知。下一次改变存储形式时须同时处理表中存量和已存回执的形状。

`tests/python/blackboard/store/test_interruption_recovery.py` 中的 `test_explicit_legacy_start_only_settles_before_this_boot`、`test_legacy_clock_corrections_within_margin_remain_unknown` 继续保留，用于验证 schema 20 仍有的存量规则及一小时余量。完整进程身份、native 子进程证据、恢复保留与停止确认属于通用能力，本次未删。

测试命令为 `PYTHONPATH=src <prepared-python> -m hey_my_buddy.cli.checks tests/python/install/ tests/python/blackboard/store/ tests/python/cli/test_harness_test_safety.py`，解释器为 Host 指定的既有解释器，未指定进程数，运行器采用默认的 4 个进程。结果为 26 个测试文件、258 项测试全部通过，跳过 0 项，退出码 0；运行器确认私有根清理完成。额外的 `test_harness_test_safety.py` 核对样板恢复仍能在隔离的空目录中执行。`git diff --check` 通过。未跑完整检查，完整检查由 Host 整合后运行。

与任务要求不一致的情形：没有。所有受版本控制的改动均在授权范围内；除本说明外未改 `docs/`，未改 `skills/`、README、`AGENTS.md` 或 `CONTEXT.md`，未改分支、标签或共享 stash，未提交，也未安装产品或操作日常服务与 Worker。
