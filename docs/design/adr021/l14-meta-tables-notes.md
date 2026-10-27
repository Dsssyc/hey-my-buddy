# L14：meta 的结构化记录归表

本微任务从 `e451a474` 的 schema 19 新看板形式出发；升级基点仍是 `4b7f494670efe7a3fe053062f16cce9312a5e042`、0.34.1、schema 18。没有改名、前端、打包资源或提交中的基点样板；没有历史数据换算和兼容读法。以下是源码与私有测试的记录，Host 的验收及日常安装另行进行。

## 记录的归属

| 原来的键 | 现在的位置 | 写者与读者 |
| --- | --- | --- |
| `router_profile_ids` | `router_profiles(position, profile_id)`，一项一行 | `router._write_settings` 保存完整有序列表，`router.configuration` 按位置读；列表允许保留不再发布的 ID，由现有解析器解释不可用原因 |
| `router_default_mode` | `router_settings.default_mode`，固定 `id=1` | 设置发布与升级写入，配置读取、Router 解析读取 |
| `router_budget_preset` | `router_settings.budget_preset` | 设置发布与升级写入，配置读取与 `configured_budget` 读取 |
| `router_retry_interval_seconds` | `router_settings.retry_interval_seconds` | 设置发布与升级写入，配置读取与 Router 重试解析读取 |
| `catalog-read-at:<adapter>` | `catalog_current.read_at` | 可信目录发布的 `note_confirmed_read` 写，目录保鲜与健康扫描读取；未知观察只更新观察时间，不改可信读取时间 |
| `catalog-account-status:<adapter>` | `catalog_current.account_status` | 同一可信发布写，目录和账户投影读；未知观察保留上一次可信账户事实，没有可信事实时为 `unknown` |
| `catalog-scan-after:<adapter>` | `harness_health.scan_after` | `coordinate_catalog_reread` 在原事务里认领窗口；必要的直接目录读取结束后 `_note_catalog_scan` 写；显式验证和目录保鲜读同一窗口与单次飞行 |
| `catalog-harnesses:<observation_id>` | `catalog_observations.harnesses_json` | `catalog_store.begin` 首次插入观察时写，再次使用同一请求返回首份快照，后续记录与目录调用从观察行读 |
| `attempt-tool-policy:<attempt_id>` | `router_claims.tool_policy_json` | Router 认领从真实 `read_health` 冻结工具规则，与认领绑定和事件一起提交；发布时 `_attempt_tool_policy` 从该行读，认领重放不能更换规则 |
| `workspace-identity:<old_id>` | 删除 | 新看板不承接旧身份，也不再读取旧身份别名 |
| `workspace-identity-migration` | 删除 | 新看板不承接旧迁移报告，也不再读取报告的原因 |
| `schema_version`、`created_at`、`capability_secret` | 保留 `meta` | 建库、版本检查与看板密钥使用；删除通用 `Database.meta`、`Database.set_meta` |

`router_profiles` 的位置非负且唯一、ID 唯一；公共设置入口仍校验列表和标量参数，响应字段与默认值不变。工具规则列非空；未认领的执行没有认领行，读取仍返回 `None`。被删的旧身份不进入新表。源码生产写入 `meta` 的 SQL 只剩三处，看板打开时只读当前 schema，不临时转换数据。

目录窗口只表示目录补读的认领或已经执行的读取。普通健康探测不推进它，否则健康探测会阻止缺模型时本应进行的目录补读。非 ready 健康结果用现有 `expires_at` 限制重复探测；ready 且未过期的结果仍在原生探测之前检查位置指纹，没有目录窗口时可以重新检查本地位置快照，保持健康结果和目录观察的含义分开。实际目录补读仍共用唯一的 180 秒窗口，手动强制发现继续忽略该窗口。没有增加第二个持久化扫描时钟。

## 从基点带设置

`migrations.read_settings` 仅从 schema 18 的四个旧键读 Router 设置：JSON 列表保持原顺序，间隔转换为整数，模式与预算档保持值。`write_settings` 写当前的设置行与列表行，不写旧键。其余设置表全部用明确的目标列名插入，列顺序变化不再影响写入。

带走 `harness_health` 的手工路径、修订、健康记录、检查时间和过期时间；不带 `scan_after`。旧健康扫描时间不能证明新看板读过目录，新行的目录窗口从 `NULL` 开始。目录可信时间、账户观察、观察快照、工具规则和工作历史均不带走。新密钥、创建时间和默认状态由建库产生。

带走的 buddy 行若原因是 `ACCOUNT_STATUS_UNKNOWN`，清除该已归档目录的诊断。首次可信读取后，保留设置但从未进入新看板保留目录的模型若缺失，直接使用现有 `not present in the latest confirmed native discovery` 原因。已经有保留目录事实的模型仍遵循原来的失踪确认窗口与 effort 退休规则。基点样板本身没有该过时原因，回归在加载后的私有副本上设置这一状态，再走真实升级及目录发布；提交中的样板未改。

逐表比较仍将“基点升级后的看板”与“新建看板加同一批设置”对照；Router 四值还单独与基点读取的值对照，非默认列表的顺序、模式、预算和间隔均保留。比较排除各新看板独立生成的密钥和时间。

## 删除的读者

`workspace_identity.py` 只保留当前稳定目录身份计算，删除别名映射、锚点证明加载、上下文、SQL 规范化与迁移原因读取。`workspace.py` 的验证、封存、集成、分配和回收直接比较当前身份。`workflow.py` 删除别名包装，直接调用工作区操作，预约、继续和转交查询用单个 `checkout_id`；相同路径上身份不同的预约仍拒绝重复取得所有权。

`delegation.py` 直接把记录的项目身份投影为 `project_id`，原有 `project_identity_reason` 列保留为 `NULL`；项目筛选直接等值。`objectives.py` 的归属、附加与摘要直接用当前项目身份，冲突的公开原因字段为 `None`，可选原因字段继续按原有规则省略。`objective_summary_cache.py` 删除别名及迁移报告依赖，事件描述、元数据、成员关系及真实项目记录仍参与原来的缓存判定。

`router_boundary_data.py` 的继续边界、`storage.py` 的分配引用检查直接用当前身份，仍保留实际路径、请求、摘要和历史引用的保护。没有发现新看板上的读者需要旧别名或报告才能工作。旧看板只能按归档对应的旧版本查看，这里没有为归档提供新版本读法。

## 顺带项

保留 `_complete` 原有的实际确认停止条件，新增外层停止未确认时不重新路由的回归；该回归检查只存在一个派发、没有重新路由事件、执行停止字段仍为零，公开结果保留原来的 `changed` / `needs-host`。选中 buddy 失效与重新路由的指定原因改为英文，原因测试同步更新。

上一代运行时提交号在准备新运行时、写升级日志和停止旧服务之前检查；缺少时以 `UPGRADE_ARCHIVE_IDENTITY_UNAVAILABLE` 拒绝。归档函数仍在破坏性发布边界检查归档清单的提交身份。回归证明旧端点、数据库和服务控制流程没有被切换，未调用停止或启动，未写升级日志。

删除本次文件中的未用导入和变量，包括旧别名的上下文、通用 meta 方法、未消费的设置及工具规则变量、Router 测试的过时导入和 workflow 的未消费返回值。`db.canonical_json` 是仍被调用者使用的导出，保留该明确的导出。

## 测试调整

下面按原测试名称记录改写、删除和原来的保护点；改名的测试保留非旧身份的断言，独立的旧身份及报告测试删除。没有因新存储形式删掉仍有意义的生命周期、所有权、缓存或证据断言。

| 测试文件与原名称 | 处理 | 原保护点与本次调整 |
| --- | --- | --- |
| `blackboard/store/test_blackboard.py::test_the_default_cold_start_installs_and_reports_the_stable_runtime` | 改写 | 真实冷启动必须安装并报告稳定运行时；失败断言附上安装日志末尾，防止私有根清场后丢掉 uv 的失败原因，成功断言不变。 |
| `blackboard/catalog/test_catalog_observations.py::test_identical_read_records_observation_and_confirmed_time_without_event` | 改写 | 相同目录读取推进观察与可信时间，不制造业务事件；改断言为当前目录列。 |
| `blackboard/catalog/test_catalog_observations.py::test_unknown_reading_keeps_profiles_unless_it_restores_retained_availability` | 改写 | 未知读取保留 profile 与上次可信事实；从当前目录行核对可信时间和账户状态。 |
| `blackboard/routing/test_router.py::test_budget_publication_preserves_model_and_schema` | 改写 | 真实预算发布保留 Router 选择和 schema；schema 用显式只读查询。 |
| `blackboard/routing/test_router.py::test_budget_update_does_not_change_selection_request_replay` | 改写 | 预算改变不影响同一请求重放；注入改为设置表。 |
| `blackboard/routing/test_router_dispatch.py::test_claim_freezes_the_actual_family_policy_and_binding` | 改写 | 认领冻结实际工具规则和执行绑定；改读认领行。 |
| `blackboard/routing/test_router_failover_integration.py::test_two_no_answers_keep_request_and_dispatch_receipts_without_loop` | 改写 | 两次无答案保留冻结请求、派发和收据，不循环；核对第二次认领的规则列。 |
| `blackboard/routing/test_router_list_configuration.py::test_fresh_private_board_initializes_router_defaults` | 改写 | 新看板的 Router 默认值及持久形式；改核对设置列、空列表表与三个单值键。 |
| `blackboard/routing/test_router_list_configuration.py::test_publish_writes_list_settings_and_projection` | 改写 | 设置发布实际写入列表和标量，投影及预算事件一致；核对列表位置与设置列。 |
| `blackboard/routing/test_router_list_configuration.py::test_configuration_reads_are_side_effect_free` | 改写 | 配置及 Router 读取不写设置；比较包含新设置表的状态。 |
| `blackboard/routing/test_router_sequence.py::test_claim_binds_the_attempt_and_appends_one_atomic_event` | 改写 | 认领绑定与事件原子且幂等；增加冻结规则的持久断言。 |
| `blackboard/routing/test_router_sequence.py::test_claim_rolls_back_with_its_caller_transaction` | 改写 | 调用方回滚同时撤销认领与事件；样板认领提供规则。 |
| `blackboard/routing/test_router_sequence.py::test_deferred_claim_accepts_only_the_exact_attempt_task_and_generation_at_commit` | 改写 | 延迟外键只接受准确执行、任务与代；样板认领提供规则。 |
| `blackboard/routing/test_router_sequence.py::test_claim_task_generation_is_unique_before_deferred_attempt_binding` | 改写 | 执行尚未插入时，认领的任务与代也唯一；原始 SQL 增加必填规则。 |
| `blackboard/routing/test_router_tool_evidence.py::test_publication_rejects_usage_mismatch_and_unbound_old_attempts` | 改名并改写 | 拒绝工具计数不符与缺失冻结规则；改名为 test_publication_rejects_usage_mismatch_and_missing_frozen_policy，篡改认领规则为 JSON null，保留拒绝证据断言。 |
| `blackboard/routing/test_routing_modes.py::test_fresh_board_defaults_fast_and_patch_preserves_omitted_settings` | 改写 | 新看板 fast 默认值及部分设置补丁保留遗漏字段；schema 用显式查询。 |
| `blackboard/routing/test_selection_publication.py::test_disabled_selected_buddy_reroutes_once` | 改写 | 选中 buddy 停用后只重新路由一次；断言英文原因。 |
| `blackboard/routing/test_selection_publication.py::test_unavailable_selected_buddy_reroutes_once` | 改写 | 选中 buddy 不可用后只重新路由一次；断言英文原因。 |
| `blackboard/routing/test_selection_publication.py::test_excluded_selected_buddy_reroutes_once` | 改写 | 选中 buddy 被排除后只重新路由一次；断言英文原因。 |
| `blackboard/routing/test_selection_publication.py::test_capability_loss_reroutes_once` | 改写 | 选中 buddy 丢失任务所需能力后只重新路由一次；断言英文原因。 |
| `blackboard/routing/test_selection_publication.py::test_quota_loss_reroutes_without_adopting_exhausted_buddy` | 改写 | 额度边界阻止采纳已耗尽 buddy，重新路由；断言英文原因。 |
| `blackboard/routing/test_selection_publication.py::test_second_invalid_selection_reaches_host_with_reason` | 改写 | 第二次仍非法时到 Host 边界并给准确候选和原因；断言英文原因。 |
| `blackboard/service/test_harness_health.py::test_throttled_scan_still_rereads_a_stale_catalog_once` | 改写 | 旧目录时钟到期时，健康扫描仍补读一次且受限流；改过期唯一目录窗口，原生健康缓存仍只探测一次。 |
| `blackboard/service/test_harness_health.py::test_unparseable_read_time_counts_as_expired` | 改写 | 不可解析的可信读取时间算过期；改注入目录列。 |
| `blackboard/service/test_harness_health.py::test_stale_reread_failure_returns_the_post_callback_record` | 改写 | 补读中发生健康失败，调用者得到回调后事实；改注入目录列与唯一窗口。 |
| `blackboard/service/test_harness_health.py::test_native_failure_inside_the_service_callback_invalidates_for_the_caller` | 改写 | 服务目录回调内的真实原生失败使健康事实失效；改过期唯一窗口。 |
| `blackboard/service/test_harness_service.py::test_manual_refresh_ignores_the_bounded_reread_window` | 改写 | 人工 refresh 可越过目录补读限流；改注入和检查健康行窗口。 |
| `blackboard/store/test_upgrade_fixture.py::test_upgrade_matches_a_fresh_board_with_only_the_retained_settings` | 改写 | 基点升级逐表等于新看板加设置，密钥独立且没有历史；新增 Router 与旧键四值逐项对照及新目录窗口为空。 |
| `blackboard/tasks/test_catalog_trust.py::test_c05_a_window_noted_by_the_health_scan_bounds_validation_too` | 改写 | 健康目录读取与显式验证共用补读窗口；注入和检查改唯一列。 |
| `blackboard/tasks/test_catalog_trust.py::test_c05_a_window_hit_rejudges_facts_published_after_the_first_lookup` | 改写 | 命中补读窗口时也重新判断初次查找后发布的事实；窗口改唯一列。 |
| `blackboard/tasks/test_objective_summary_cache.py::test_project_alias_and_raw_source_event_correction_affect_query_membership` | 改名并改写 | 项目别名与原始来源事件更正改变成员；改名为 test_project_id_and_raw_source_event_correction_affect_query_membership，改直接项目 ID，保留事件更正。 |
| `blackboard/tasks/test_objective_summary_cache.py::test_objective_metadata_description_and_identity_reason_raw_corrections` | 改名并改写 | 宏任务元数据、描述与迁移原因更正失效缓存；改名为 test_objective_metadata_and_description_raw_corrections，保留元数据与描述，删除迁移原因。 |
| `blackboard/tasks/test_objective_summary_cache.py::test_duplicate_kept_top_level_keys_use_existing_python_identity_reason` | 删除 | 迁移报告 kept 顶层重复键的原因与缓存一致；只测被删报告，删除。 |
| `blackboard/tasks/test_objective_summary_cache.py::test_duplicate_project_keys_in_kept_only_disable_affected_project_reuse` | 删除 | 报告 kept 的重复项目键只阻止受影响项目复用；只测被删报告，删除。 |
| `blackboard/tasks/test_objective_summary_cache.py::test_unconsumed_duplicate_json_keys_and_unrelated_report_changes_keep_reuse` | 改名并改写 | 无关报告与未消费重复事件键不使缓存失效；改名为 test_unconsumed_duplicate_json_keys_and_unrelated_meta_changes_keep_reuse，保留事件和无关写入。 |
| `blackboard/tasks/test_objective_summary_cache.py::test_identity_reason_type_precision_nul_and_size_boundaries_preserve_summary` | 删除 | 迁移原因的 JSON 类型、精度、NUL 与大小边界；只测被删报告，删除。 |
| `blackboard/tasks/test_objective_summary_cache.py::test_kept_container_type_changes_do_not_freeze_previous_identity_reason` | 删除 | 迁移报告 kept 类型改变不能冻住旧原因；只测被删报告，删除。 |
| `blackboard/tasks/test_objective_summary_cache.py::test_huge_unrelated_report_and_event_fields_are_not_copied_into_marker` | 改名并改写 | 巨大无关报告与事件字段不复制进缓存标记；改名为 test_huge_unrelated_event_fields_are_not_copied_into_marker，保留大事件断言。 |
| `blackboard/tasks/test_storage_orphans.py::test_all_integration_states_and_path_identity_alias_forms_are_retained` | 改名并改写 | 所有集成状态的路径、身份与别名都保留分配；改名为 test_all_integration_states_and_path_identity_forms_are_retained，删除别名样本，保留状态、路径和当前身份。 |
| `blackboard/tasks/test_storage_orphans.py::test_cleanup_plan_states_and_path_identity_alias_forms_cannot_use_orphan_bypass` | 改名并改写 | 各回收计划状态的路径、身份、别名不能走孤儿绕过；改名为 test_cleanup_plan_states_and_path_identity_forms_cannot_use_orphan_bypass，保留非别名保护。 |
| `blackboard/tasks/test_storage_orphans.py::test_raw_request_reference_survives_an_unrelated_verified_identity_alias` | 删除 | 请求 token 与无关已证明别名分别保护引用；专属别名测试删除，真实请求保护由另两项继续覆盖。 |
| `blackboard/tasks/test_storage_orphans.py::test_raw_request_alias_reference_protects_only_its_orphan_during_plan_and_apply` | 改名并改写 | 请求别名引用只保护对应孤儿，在计划和执行阶段重查；改名为 test_raw_request_reference_protects_only_its_orphan_during_plan_and_apply，以真实请求 token 保留两阶段保护。 |
| `blackboard/tasks/test_storage_orphans.py::test_late_raw_request_alias_reference_is_rechecked_inside_writer_fence` | 改名并改写 | 晚到的请求别名引用在写者闸门内再查；改名为 test_late_raw_request_reference_is_rechecked_inside_writer_fence，以当前请求 token 保留并发再查。 |
| `blackboard/tasks/test_workflow_routing.py::test_explicit_validated_and_no_selection_or_coding_bypass` | 改写 | 完整配置真实验证，无选择或执行绕过；schema 用显式只读查询。 |
| `blackboard/tasks/test_workspace_identity.py::test_old_manifest_verifies_without_a_coordinator_thread_context` | 删除 | 旧 manifest 借看板别名验证而不改固定文件；只测旧身份，删除。 |
| `blackboard/tasks/test_workspace_identity.py::test_registered_alias_cannot_verify_a_copied_git_directory` | 删除 | 已登记别名也不能验证复制的 Git 目录；专属别名测试删除，当前 inode 变化仍由当前身份测试覆盖。 |
| `blackboard/tasks/test_workspace_identity.py::test_valid_alias_cannot_bypass_fixed_manifest_checksum` | 删除 | 有效别名不绕过固定 manifest 摘要；专属别名测试删除，真实工作区摘要验证测试保留。 |
| `blackboard/tasks/test_workspace_identity.py::test_python_alias_loader_rejects_independently_drifted_anchor_hashes` | 删除 | Python 别名加载拒绝旧值、新值及锚点摘要分别漂移；只测已删除加载器，删除。 |
| `console/test_console_objective_cache.py::test_duplicate_json_raw_corrections_change_response_validator` | 改名并改写 | 重复描述与重复迁移报告键的原始更正改变 HTTP validator；改名为 test_duplicate_description_raw_corrections_change_response_validator，保留描述更正。 |
| `console/test_console_reads_http.py::test_every_injected_change_source_returns_new_content` | 改写 | 实际 HTTP 缓存对每种变化重新计算及返回正确内容；Router 变化注入新设置表。 |
| `console/test_console_reads_http.py::test_a_database_replacement_invalidates_every_cached_route` | 改写 | 数据库替换使各缓存路由失效；替换库的 Router 值注入新设置表。 |
| `install/test_upgrade_migration.py::test_verified_schema_18_stock_becomes_new_schema_with_only_settings` | 改写 | schema 18 真实样板经过备份与升级后只保留设置；当前配置与旧键读出的四项设置逐项相等。 |

`test_user_policy.py.setUp` 的退役 Router 样板由旧 meta JSON 改为列表表行，其六项测试的权限、退役固定偏好、原因编辑、原子拒绝及并发限制断言保留。`test_router_dispatch.py.tool_policy` 的共享读取辅助函数改认领表，`test_harness_health.py.set_read_at` 与窗口辅助函数改当前目录列和唯一窗口；调用方的冷启动、账户状态、并发飞行、恢复与缓存边界断言保留。

新增回归：`test_reorder_add_remove_and_scalar_settings_survive_real_save_and_read` 走真实设置发布，验证新增、重排、删除及标量保存；`test_begin_freezes_harnesses_in_the_observation_on_first_request` 验证观察行的首份快照；`test_frozen_tool_policy_cannot_be_replaced_by_a_replayed_claim` 验证认领冻结；`test_meta_writers_are_only_the_three_board_singletons` 扫描源码生产写入且核对新看板的三个键；`test_first_trusted_read_grounds_a_retained_setting_missing_from_catalog` 与 `test_upgraded_buddies_absent_from_first_confirmed_catalog_have_catalog_reason` 验证保留设置在首次可信目录下的缺失原因；`test_invalid_selection_with_unconfirmed_outer_stop_does_not_reroute` 验证停止门槛；`test_missing_previous_commit_refuses_before_old_service_stops` 验证最早拒绝；`test_health_only_scan_does_not_consume_a_catalog_reread_window` 验证健康检查不抢占目录补读，实际补读后再次请求才命中窗口。

## 验证过程与边界

前端依赖使用检出内临时准备的 Node 24.21.0，执行 `npm --prefix apps/console ci`，退出 0，安装 202 个包。npm 缓存与人工保存的日志都在检出的 `tmp/meta-tables/`，交付前删除。没有改前端源码，也没有执行单独的前端或浏览器布局检查。

Python 检查只使用 Host 指定的现成解释器，以 `PYTHONPATH=src <指定现成 Python> -m hey_my_buddy.cli.checks --jobs 8` 运行；外层清掉指定的运行时、Worker、代理凭据与虚拟环境变量，Node 24 放在 PATH 前面。检查器自行创建和清理私有状态、运行时及 home；macOS 的完整检查使用检查器默认的短私有根，避免 104 字节的原生 UNIX socket 路径上限，没有碰日常看板或真实 harness 配置。

定向检查包括设置和认领的真实路径、基点升级、目录可信事实、观察冻结、扫描窗口、身份删除后的工作区与缓存、停止门槛及升级最早拒绝。窗口审查发现健康扫描不能冒充目录读取，修复后 5 个定向文件、97 项通过。归档预检最初扩大到同 schema 升级路径，完整检查及该文件单独复跑均暴露了这一问题；限定为 schema 18 的归档前提后，升级与归档的 2 个文件、25 项通过。同 schema 升级继续在原来的备份阶段读取提交元数据。

首轮完整检查有 `blackboard.store.test_blackboard` 与 `install.test_upgrade` 两个文件失败。冷启动在首轮报告 `runtimeStable=false`，同文件独立重跑 57 项全部通过，未单独证明首轮不稳定身份的原因；源码整理后重新执行完整检查。最终轮冷启动转为运行时依赖下载失败，补上安装日志诊断后再次单独复跑，该文件 57 项中的 56 项通过、1 项因同一下载超时失败，检查器退出 1，私有根已清理。

冷启动安装失败输出（私有路径替换为占位符）：

```text
RUNTIME_INSTALL_FAILED: uv sync failed while installing the stable runtime
Building hey-my-buddy @ file://<private-runtime>
× Failed to download `typing-extensions==4.16.0`
├─▶ Request failed after 3 retries in 44.8s
├─▶ Failed to fetch:
│   https://files.pythonhosted.org/packages/49/d3/b8441a820a491ddfc024b0b0cf0393375b75ea13866d9c66727e54c2fc80/typing_extensions-4.16.0-py3-none-any.whl
├─▶ error sending request for url
├─▶ client error (Connect)
╰─▶ operation timed out
help: hey-my-buddy v0.34.1 → pydantic v2.13.5 → typing-extensions v4.16.0
```

这是 Host 在简报中允许记录交付的 PyPI 依赖下载超时，没有修改安装依赖或现成解释器环境来规避它。冷启动安装的网络条件下成功仍需 Host 重跑；本次已有一次独立文件的成功证据，不能替代失败的完整检查结果。

最终完整检查共 233 个文件，231 个文件通过，成功文件内 3790 项、0 跳过；两个失败文件是 `blackboard.store.test_blackboard` 与 `install.test_first_install`，检查器退出 1，私有根已清理。前者的单独复跑已捕获上述 PyPI 下载超时。后者完整检查中 12 项有 2 个失败、5 个错误，全部在运行时 `uv sync` 失败处先于各测试预期的安装行为终止；原始输出是 `RUNTIME_INSTALL_FAILED: uv sync failed while installing the stable runtime; see the install log`。该安装路径不传日志文件，所以没有进一步捕获这一文件的具体依赖错误，不把它断言为已独立证明的某个下载超时；随后原样单独重跑同文件，12 项、0 跳过、67.4 秒、退出 0，私有根已清理。首轮完整检查中这一文件也曾通过（97.1 秒）。

未守住的验证边界是完整检查的冷启动依赖安装成功；Host 需要在自己的网络环境复跑这两个安装文件。没有未解释的持续性源码测试失败，没有新增绕过安装或降弱断言的路径。源码停在输入提交 `e451a474` 的本次未提交差异；所有跟踪文件的改动都在冻结写入范围，`git diff --check` 通过。没有提交、分支或标签操作，没有日常安装、日常服务或 Worker 操作、真实账户读取、付费模型调用；真实 harness、Windows 本机升级与 Host 最终验收没有验证。本次说明取代上一份升级说明中 Router 仍写 meta、保留别名读法与完整复制旧扫描窗口的描述，冻结范围外的历史说明没有改写。
