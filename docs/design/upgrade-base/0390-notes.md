# 升级基点移至 0.39.0

新基点是 `b9632dd9421b02fab170752175edabf683a8aeb1`（0.39.0，schema 22）。开发者提供的安装事实是：2026-10-15 13:58Z 日常安装已执行 schema 21 → 22 升级，因此这次换算已消费；本任务未检查日常安装。`packaging/generate-upgrade-fixture.py` 的 `BASE`、`BASE_VERSION`、`BASE_SCHEMA` 已改为新基点，产品版本与 `SCHEMA_VERSION` 均未改动；当前没有待执行的 schema 换算。

在检出根目录执行 `<prepared-python> packaging/generate-upgrade-fixture.py --python <prepared-python> --work-dir tmp/upgrade-base-0390 --output-dir tests/fixtures/upgrade-base`；`<prepared-python>` 表示 Host 指定的现成只读依赖解释器。生成器退出码为 0，返回 `integrity=ok`、`foreignKeyViolations=0`、`schema=22`、`tables=55`、`events=42`、`sourceSchemaMatches=true`；四个稳定任务的状态分别为 `concluded`、`accepted`、`concluded`、`cancelled`。源码从指定提交归档到检出内的私有工作目录，命名服务操作在进程内运行，原生发现与执行结果为合成输入，没有启动服务或调用模型。

重新生成的文件是 `tests/fixtures/upgrade-base/board.sql`、`metadata.json`、`operations.json`、`workspace-resources.json`；`catalog.json` 内容与此前相同。元数据记录基点提交、版本、schema、源码树、源码与生成器摘要、解释器依赖版本、生成命令以及各样板文件的摘要；加载器仍先校验摘要，再把路径、文件身份和相关摘要绑定到测试的私有目录，返回之后才采集保留基线。这些绑定属于样板准备，不是存储形式换算。

删除了 `blackboard/store/upgrade.py` 整个模块及其中 6 个函数：`original_micro_task` 只解析换算时的 schema 21 原始适配器选择；`obsolete_receipt_ids` 只寻找应由本次换算清除的旧命令与外部微任务视图回执；`migrated_buddy` 只把旧适配器选择换成空 buddy；`migrated_selection_event` 只换算旧选择事件；`migrated_stop_response` 只把旧控制台停止回执换成当前形状；`migrate` 只负责这次事务中的提醒与修正关系建表、停止回执换算、旧选择换算、回执清除及 schema 标记更新。模块中的 `BASE_SCHEMA_VERSION=21`、`_NATIVE_ADAPTERS`、`_BUDDY_FIELDS`、`_VIEW_RECEIPT_KINDS` 均只为这些函数服务，一并删除。基点的提交、版本和 schema 由生成器和样板元数据明确记录，不留下空迁移模块。

删除安装端的 `verify_migrated_snapshot`，它逐项核对的新增表、空表、schema 标记和预期指纹只对应 schema 21 → 22。删除安装流程中的 `migrating`、`migrated` 阶段、换算调用及换算后核对；备份核验之后直接以原始快照作为切换后核对的预期值。删除 `idle_snapshot` 为本次换算计算的 `migratedCommandsFingerprint`、`migratedCommandsCount`、`migratedWorkflowRunsFingerprint`、`migratedEventsFingerprint`、`migratedMetaFingerprint`。`idle_snapshot` 和备份清单的 schema 接受范围均收窄为当前的 22，备份恢复试读仍执行 `Database.initialize()` 的当前结构校验，没有增加基点与当前版本相同的开关。

`backup.database_snapshot` 的 `changes`、`table_names` 参数、更新与插入及删除的投影分支、投影排序合并，以及内部函数 `projected_count`，源码中的生产调用者全部是上述换算预期指纹计算，因此一并删除。保留了它原本的全表行数、排序 JSON 指纹、事件前缀、生命周期豁免与 `exact` 捕获能力；宽表分块编码只移除服务于投影的偏移与主键参数，输出格式仍由原有测试核对。合计删除 8 个生产函数，其中 1 个是内部函数；测试辅助函数保留并改为直接准备当前存量形状。

删除 `test_upgrade_fixture.py` 中 12 个仅验证这次换算的测试：`test_upgrade_preserves_historical_buddy_after_a_harness_override` 和 `test_native_selection_event_uses_original_spec_after_harness_override` 测选择换算的原始身份依据；`test_upgrade_refuses_existing_fix_storage_without_changing_the_board` 测换算建表前的拒绝条件；`test_migrated_snapshot_rejects_a_fix_row_inserted_after_upgrade`、`test_migration_verification_refuses_changes_beyond_the_exact_stop_conversion`、`test_exact_migration_snapshot_detects_workers_sequence_and_metadata_changes`、`test_native_selection_snapshot_detects_run_and_event_tampering` 测已删除的换算后核对；`test_native_view_receipts_are_deleted_by_original_run_identity` 测本次回执清除；`test_model_selection_event_must_match_the_original_explicit_buddy` 测选择换算的内容约束；`test_macro_stop_receipt_converts_to_the_exact_current_form`、`test_invalid_macro_stop_receipt_rolls_back_all_upgrade_changes` 测停止回执换算及回滚；`test_invalid_native_selection_rolls_back_all_upgrade_changes` 测旧选择换算失败后的回滚。

删除 `test_backup.py` 中的 `test_snapshot_projects_sparse_update_insert_and_delete`，它只验证换算预期快照的稀疏投影，合计删除 13 个测试。其余测试按新基点调整：`test_schema_upgrade_preserves_stock_and_matches_fresh_structure` 仍走安装升级入口、真实备份与核验，比较切换前后的完整精确快照、工作区文件及新建看板结构；回执篡改测试直接写入 schema 22 的停止回执，继续验证 `verify_started` 拒绝改写；旧命令与外部微任务读取测试直接准备 schema 22 存量；同 schema 切换和私有服务测试直接加载新样板；`test_current_core.py` 的 `meta` 写入清单移除已删除的迁移写入者。原有 schema 拒绝测试补上 21，备份拒绝测试也使用已消费的 21。

逐项判断后保留的通用部分包括：`idle_snapshot` 的空闲任务判定、未完成执行与实际停止确认、完整性及外键检查；`backup_readiness` 在准备阶段、入口围栏建立后、停服与持锁之后的预检；完整备份、路径约束、摘要校验、离线 WAL 拒绝、发布日志与恢复；`verify_started` 的运行时身份、schema、容量设置、表清单、原有数据指纹及源码泄漏核对；skill 的暂存、代际核对、发布和恢复；活动运行时指针切换、失败回滚和中断日志恢复。同 schema 的看板及历史工作区文件仍由原有切换测试检查。`db.py` 的 `REMINDER_SCHEMA` 和 `FIX_SCHEMA` 是新建 schema 22 看板的当前结构，保留原样。

挪基点后，生产代码中明确为 schema 21 传下来的旧命令与外部微任务保留的专用读法是 `tasks/workflow.py:WorkflowCoordinator._turn_view` 中对 `command`、`external` 与冻结输入 `context.buddy={"adapter": ...}` 的匹配：读取结果的 buddy 为 `None`，冻结输入本身不改写。当前回合、历史回合和审计都会使用这条读法；保留的读取测试覆盖它们及 CLI 摘要，并核对冻结输入没有变化。

与这批存量有关、同时服务当前状态的通用读取也保留：`WorkflowCoordinator._buddy` 接受空 `buddy_json`，`_view` 与审计读取不可变的 `micro_task_json`，`effective_spec` 读取执行记录的适配器；`tasks/micro_task.py` 的 `_complete_buddy` 和任务来源投影，以及 `cli/cli_views.py` 的微任务、执行与列表摘要，均允许缺少完整 buddy。它们还服务尚未完成路由的当前微任务或普通执行，不能整体归为旧存量专用代码。`workflow.execution_selected` 作为已经换算好的历史事件由通用事件读取保留，当前源码没有专门再换算它的分支。`_require_replayable_submission` 对空请求指纹返回 `SUBMISSION_REPLAY_UNAVAILABLE`，覆盖的是早于 schema 21、经 schema 21 和 22 继续保留的更早存量，相关拒绝重放与新提交幂等测试也保留。`protocol/schemas.py` 中命令与外部适配器仍属于当前普通执行契约。

先执行 `PYTHONPATH=src <prepared-python> -m hey_my_buddy.cli.checks tests/python/blackboard/store/test_upgrade_fixture.py tests/python/install/test_backup.py`，2 个文件、20 项测试全部通过，退出码 0，私有根目录已移除。随后执行 `PYTHONPATH=src <prepared-python> -m hey_my_buddy.cli.checks tests/python/install/ tests/python/blackboard/store/`，默认 4 个并行 Worker 跑完 25 个文件、262 项测试，全部通过、无跳过，退出码 0，私有根目录已移除。改过的测试文件全部在这两个目录内，未运行完整检查。`git diff --check` 通过；检查了所有改动文件和本说明文件，没有本机主目录路径。测试覆盖包括新基点与当前结构比对、同 schema 原样切换、私有服务读取、私有真实服务切换及 Host 恢复、备份与预检、启动失败回滚、skill 发布和恢复。

与任务要求不一致的情形：没有。未改产品版本、schema 版本或 schema 22 存储形式；除了本说明文件，没有修改文档、skill、README、`AGENTS.md` 或 `CONTEXT.md`；没有安装产品或操作日常服务，未提交代码。
