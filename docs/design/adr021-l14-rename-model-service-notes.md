# 模型服务一族改名的微任务说明

2026-10-14，基于 `c4c6ac56` 的独立检出。这里只记录本微任务的源码改名与检查结果，等待 Host 审查和验收；版本仍为 0.34.1，schema 仍为 19。

Python 的指定目录检查退出 1：137 个文件中 116 个通过、21 个失败，失败的 65 个测试用例在未改的基点源码上全部复现，两个检查运行的私有根都已清理。前端最终全量通过 78 个文件、1,088 项测试，浏览器检查通过 96 项，构建成功并已重建打包资产。Python 验证仍需要 Host 提供检出外的短路径私有测试根。

清单没有逐项列出的具体旧名字共改了 115 个，包含清单第 366 行内部词干规则展开后的函数、常量、变量、测试名字和两个测试文件名；下表逐项列出。规定的 `src`、`tests/python`、`apps/console`、`packaging` 检索范围剩 42 处匹配，另有预览探针中的显示文字 1 处，全部理由见后表。

处数按基点源码中的完整标识符或完整字段逐词计数，包括字符串里的内部引用、注释里的同名引用，以及随 `modelFamily` 断言一起删除的引用，测试文件名各计 1 处；它表示处理的旧名命中数，不代表新名现在的引用数，生成资产不重复计入。`family` 的内部变量、回应键与普通文字分别说明，字体属性和历史基点读法不算改名。

## 清单明确列出的名字

| 旧名 | 新名或处理 | 改动处数 |
| --- | --- | --- |
| `family_annotations` | `model_service_notes` | 13 |
| `model_concurrency` | `model_service_concurrency` | 18 |
| `attempts_model_family_idx` | `attempts_model_service_idx` | 1 |
| `familyAnnotations` | `modelServiceNotes` | 106 |
| `familyAnnotationChanges` | `modelServiceNoteChanges` | 46 |
| `modelConcurrency` | `modelServiceConcurrency` | 149 |
| 回应键 `family` | `modelService` | 2（源码与对应测试各 1） |
| `modelFamily` | 删除回应字段、合成预览字段及 9 组只读它的测试断言 | 11（源码 1、预览 1、测试引用 9） |

`family`、`families` 的 Python 内部名字分别改为 `model_service`（122 处）和 `model_services`（28 处）；前端的同词按语境成为 `modelService`、`modelServices`，有一个测试的分组局部变量成为 `groupedServices`，相应完整词的基点命中数分别为 141 和 15（包括注释与测试描述）。注释和模型服务错误消息同步改用 model service/model services，错误码和事件名未改。

清单中的 `family_preferences`、`familyPreferences`、`familyPreferenceChanges` 与取值 `family` 所属的模型服务偏好层已经从当前运行路径删除，本微任务不重新引入；旧基点与拒绝旧输入的测试仍使用其原名字。前端 i18n 中已经没有这一族的旧键，因此没有要改的 i18n 键；全部 13 个 i18n 源文件逐字相同。

## 清单未逐项列出的具体名字

| 旧名 | 新名 | 改动处数 |
| --- | --- | --- |
| `FAMILY_A` | `MODEL_SERVICE_A` | 66 |
| `FAMILY_B` | `MODEL_SERVICE_B` | 41 |
| `FAMILY_FIELDS` | `MODEL_SERVICE_FIELDS` | 5 |
| `FLASH_FAMILY` | `FLASH_MODEL_SERVICE` | 30 |
| `FamilyAnnotation` | `ModelServiceNote` | 14 |
| `FamilyAnnotationChangePatch` | `ModelServiceNoteChangePatch` | 5 |
| `FamilyQuotaTests` | `ModelServiceQuotaTests` | 1 |
| `FrozenFamilyTests` | `FrozenModelServiceTests` | 1 |
| `FullFamilyBacklogTests` | `FullModelServiceBacklogTests` | 1 |
| `LIVE_FAMILY` | `LIVE_MODEL_SERVICE` | 5 |
| `MAX_FAMILIES` | `MAX_MODEL_SERVICES` | 4 |
| `MODEL_CONCURRENCY_DEFAULT` | `MODEL_SERVICE_CONCURRENCY_DEFAULT` | 9 |
| `MODEL_CONCURRENCY_FIELDS` | `MODEL_SERVICE_CONCURRENCY_FIELDS` | 2 |
| `MODEL_CONCURRENCY_MAX` | `MODEL_SERVICE_CONCURRENCY_MAX` | 6 |
| `MODEL_CONCURRENCY_MIN` | `MODEL_SERVICE_CONCURRENCY_MIN` | 6 |
| `ModelConcurrencyEntry` | `ModelServiceConcurrencyEntry` | 8 |
| `ModelConcurrencySetting` | `ModelServiceConcurrencySetting` | 10 |
| `ModelConcurrencyTestCase` | `ModelServiceConcurrencyTestCase` | 5 |
| `ModelFamily` | `ModelService` | 21 |
| `ModelFamilyFairnessTests` | `ModelServiceFairnessTests` | 1 |
| `ModelFamilyGroup` | `ModelServiceGroup` | 11 |
| `ModelFamilyTests` | `ModelServiceTests` | 1 |
| `PROFILE_FAMILY` | `PROFILE_MODEL_SERVICE` | 5 |
| `_QUEUED_FAMILY_MODEL` | `_QUEUED_MODEL_SERVICE_MODEL` | 4 |
| `_QUEUED_FAMILY_PROVIDER` | `_QUEUED_MODEL_SERVICE_PROVIDER` | 4 |
| `_families` | `_model_services` | 3 |
| `_family` | `_model_service` | 4 |
| `_family_admission_blocker` | `_model_service_admission_blocker` | 2 |
| `_family_changes` | `_model_service_changes` | 2 |
| `_family_key` | `_model_service_key` | 8 |
| `_full_families` | `_full_model_services` | 2 |
| `_model_concurrency_changes` | `_model_service_concurrency_changes` | 2 |
| `_queued_families` | `_queued_model_services` | 2 |
| `addMissingFamilies` | `addMissingModelServices` | 7 |
| `byFamily` | `byModelService` | 3 |
| `chosen_family` | `chosen_model_service` | 4 |
| `emptyFamilyAnnotation` | `emptyModelServiceNote` | 3 |
| `enabledFamilies` | `enabledModelServices` | 4 |
| `enabled_families` | `enabled_model_services` | 3 |
| `families_from_payload` | `model_services_from_payload` | 1 |
| `familyAnnotation` | `modelServiceNote` | 4 |
| `familyAnnotationText` | `modelServiceNoteText` | 13 |
| `familyKey` | `modelServiceKey` | 49 |
| `familyOf` | `modelServiceOf` | 3 |
| `familyPending` | `modelServicePending` | 3 |
| `familySearchText` | `modelServiceSearchText` | 3 |
| `familyText` | `modelServiceText` | 4 |
| `family_clauses` | `model_service_clauses` | 4 |
| `family_facts` | `model_service_facts` | 7 |
| `family_key` | `model_service_key` | 15 |
| `family_reason` | `model_service_reason` | 4 |
| `family_row` | `model_service_row` | 12 |
| `family_rows` | `model_service_rows` | 4 |
| `family_values` | `model_service_values` | 3 |
| `flashFamily` | `flashModelService` | 18 |
| `freshFamilies` | `freshModelServices` | 2 |
| `full_families` | `full_model_services` | 3 |
| `model-concurrency.test.tsx` | `model-service-concurrency.test.tsx` | 1 |
| `modelConcurrencyChanges` | `modelServiceConcurrencyChanges` | 7 |
| `modelFamilies` | `modelServices` | 14 |
| `model_family` | `model_service` | 9 |
| `normalize_family` | `normalize_model_service` | 5 |
| `pending_families` | `pending_model_services` | 9 |
| `plan_family` | `plan_model_service` | 4 |
| `refresh_families` | `refresh_model_services` | 17 |
| `retiredFamily` | `retiredModelService` | 5 |
| `selector_family` | `selector_model_service` | 3 |
| `setFamilyAnnotation` | `setModelServiceNote` | 14 |
| `test_a_claim_freezes_the_resolved_family_onto_the_attempt` | `test_a_claim_freezes_the_resolved_model_service_onto_the_attempt` | 1 |
| `test_a_command_attempt_has_no_family_and_counts_only_against_the_total` | `test_a_command_attempt_has_no_model_service_and_counts_only_against_the_total` | 1 |
| `test_a_confirmed_completion_releases_the_lowered_family_slot` | `test_a_confirmed_completion_releases_the_lowered_model_service_slot` | 1 |
| `test_a_family_at_its_default_limit_blocks_only_that_family` | `test_a_model_service_at_its_default_limit_blocks_only_that_model_service` | 1 |
| `test_a_freed_family_slot_lets_the_routing_task_claim_and_freeze_the_selector` | `test_a_freed_model_service_slot_lets_the_routing_task_claim_and_freeze_the_selector` | 1 |
| `test_a_full_family_backlog_cannot_hide_model_less_command_work` | `test_a_full_model_service_backlog_cannot_hide_model_less_command_work` | 1 |
| `test_a_full_family_backlog_over_one_hundred_cannot_hide_other_families` | `test_a_full_model_service_backlog_over_one_hundred_cannot_hide_other_model_services` | 1 |
| `test_active_counts_unresolved_attempts_of_the_family` | `test_active_counts_unresolved_attempts_of_the_model_service` | 1 |
| `test_an_empty_claim_names_the_full_family_not_an_idle_board` | `test_an_empty_claim_names_the_full_model_service_not_an_idle_board` | 1 |
| `test_an_historical_attempt_never_changes_its_frozen_family` | `test_an_historical_attempt_never_changes_its_frozen_model_service` | 1 |
| `test_an_unconfirmed_result_keeps_its_family_slot` | `test_an_unconfirmed_result_keeps_its_model_service_slot` | 1 |
| `test_blocked_backlog_tasks_report_the_family_queue_reason` | `test_blocked_backlog_tasks_report_the_model_service_queue_reason` | 1 |
| `test_capacity_report_counts_total_and_per_family_occupancy` | `test_capacity_report_counts_total_and_per_model_service_occupancy` | 1 |
| `test_capacity_report_is_the_total_ceiling_plus_family_rows` | `test_capacity_report_is_the_total_ceiling_plus_model_service_rows` | 1 |
| `test_claim_freezes_the_actual_family_policy_and_binding` | `test_claim_freezes_the_actual_model_service_policy_and_binding` | 1 |
| `test_claim_rechecks_review_without_charging_another_family` | `test_claim_rechecks_review_without_charging_another_model_service` | 1 |
| `test_confirmed_stop_releases_same_router_family_for_next_claim` | `test_confirmed_stop_releases_same_router_model_service_for_next_claim` | 1 |
| `test_disabled_family_policy_publish_does_not_fetch` | `test_disabled_model_service_policy_publish_does_not_fetch` | 1 |
| `test_effort_never_widens_or_narrows_the_family` | `test_effort_never_widens_or_narrows_the_model_service` | 1 |
| `test_effort_variants_share_one_family_limit` | `test_effort_variants_share_one_model_service_limit` | 1 |
| `test_enablement_refreshes_the_enabled_family` | `test_enablement_refreshes_the_enabled_model_service` | 1 |
| `test_family_annotations_write_update_and_clear` | `test_model_service_notes_write_update_and_clear` | 1 |
| `test_family_capacity_is_frozen_and_unknown_stop_keeps_its_slot` | `test_model_service_capacity_is_frozen_and_unknown_stop_keeps_its_slot` | 1 |
| `test_family_capacity_only_queues_without_a_bypass_task` | `test_model_service_capacity_only_queues_without_a_bypass_task` | 1 |
| `test_family_facts_without_a_row_is_unknown` | `test_model_service_facts_without_a_row_is_unknown` | 1 |
| `test_family_is_the_exact_adapter_provider_model_tuple` | `test_model_service_is_the_exact_adapter_provider_model_tuple` | 1 |
| `test_fresh_board_is_the_current_schema_with_the_model_concurrency_table` | `test_fresh_board_is_the_current_schema_with_the_model_service_concurrency_table` | 1 |
| `test_health_capacity_reports_the_total_ceiling_and_family_rows` | `test_health_capacity_reports_the_total_ceiling_and_model_service_rows` | 1 |
| `test_model_concurrency.py` | `test_model_service_concurrency.py` | 1 |
| `test_model_concurrency_is_a_console_only_human_patch` | `test_model_service_concurrency_is_a_console_only_human_patch` | 1 |
| `test_model_concurrency_rejects_derived_fields_and_bad_limits` | `test_model_service_concurrency_rejects_derived_fields_and_bad_limits` | 1 |
| `test_model_free_command_progresses_while_decision_family_is_full` | `test_model_free_command_progresses_while_decision_model_service_is_full` | 1 |
| `test_model_profiles_pages_carry_the_same_family_rows` | `test_model_profiles_pages_carry_the_same_model_service_rows` | 1 |
| `test_more_than_hundred_full_family_rows_cannot_hide_other_work` | `test_more_than_hundred_full_model_service_rows_cannot_hide_other_work` | 1 |
| `test_non_string_family_fields_are_not_a_family` | `test_non_string_model_service_fields_are_not_a_model_service` | 1 |
| `test_only_a_family_enablement_transition_triggers` | `test_only_a_model_service_enablement_transition_triggers` | 1 |
| `test_partial_configurations_and_model_less_work_have_no_family` | `test_partial_configurations_and_model_less_work_have_no_model_service` | 1 |
| `test_refresh_rejects_bad_families` | `test_refresh_rejects_bad_model_services` | 1 |
| `test_refresh_writes_one_dated_snapshot_per_family` | `test_refresh_writes_one_dated_snapshot_per_model_service` | 1 |
| `test_repeated_families_in_one_patch_are_refused` | `test_repeated_model_services_in_one_patch_are_refused` | 1 |
| `test_selector_family_reads_the_current_dispatch` | `test_selector_model_service_reads_the_current_dispatch` | 1 |
| `test_snapshot_reports_one_row_per_represented_family_with_defaults` | `test_snapshot_reports_one_row_per_represented_model_service_with_defaults` | 1 |
| `test_the_machine_ceiling_holds_across_families` | `test_the_machine_ceiling_holds_across_model_services` | 1 |
| `test_the_selector_family_quota_precedes_the_claim` | `test_the_selector_model_service_quota_precedes_the_claim` | 1 |
| `test_uncertain_attempts_keep_their_total_and_family_slots` | `test_uncertain_attempts_keep_their_total_and_model_service_slots` | 1 |
| `test_unknown_family_is_rejected_but_retained_unavailable_family_is_editable` | `test_unknown_model_service_is_rejected_but_retained_unavailable_model_service_is_editable` | 1 |
| `touched_families` | `touched_model_services` | 3 |

两个文件分别从 `tests/python/blackboard/tasks/test_model_concurrency.py` 和 `apps/console/src/model-concurrency.test.tsx` 改名到 `test_model_service_concurrency.py` 和 `model-service-concurrency.test.tsx`；测试内容随名称同步，保留了冻结模型服务身份、未确认停止保留占用、调度公平性及并发限额的实质检查。`workflow.py` 只改了与模型服务有关的一行说明，`workspace.py` 和 `storage.py` 没有改。

## 升级

`store/migrations.py` 的 `UPGRADE_BASE_TABLES` 固定将当前表 `model_service_notes` 指向旧基点的 `family_annotations`，将 `model_service_concurrency` 指向旧基点的 `model_concurrency`；读取时只查这两个旧表，得到的设置以当前名字为键，写入时只使用当前表名。映射也显式列出其余没有改名的设置表，没有探测、别名或同时认新旧名字的运行路径。备注、并发上限及其时间字段按原列顺序携带，工作历史的处理未改。

`tests/fixtures/` 与旧样板生成器均未改。`test_upgrade_migration.py` 从旧表取得独立的原始值，再用新表逐项核对；`test_upgrade_fixture.py` 继续比较升级结果和带相同设置的全新看板的全部表及数据。两个文件在本次指定目录检查中均通过，归档、恢复等已有检查也随所属目录运行。新看板直接创建新表和新索引，schema 数字未改。

## 留下的每一处匹配

下表对应规定的四个检索根里所有不分大小写的 `famil`、`model_concurrency` 与 `modelConcurrency` 命中；生成资产按行和列标出，长行不复制全文。所有保留项都有明确的其他词义、历史基点或否定断言用途，没有待定的模型服务名字。

| 位置 | 留下的名字 | 原因 |
| --- | --- | --- |
| `packaging/generate-upgrade-fixture.py:193:17` | `familyPreferenceChanges` | 生成和校验 0.34.1/schema 18 的固定样板，调用旧基点源码的旧参数、旧表及固定备注文字。 |
| `packaging/generate-upgrade-fixture.py:194:81` | `family` | 生成和校验 0.34.1/schema 18 的固定样板，调用旧基点源码的旧参数、旧表及固定备注文字。 |
| `packaging/generate-upgrade-fixture.py:195:17` | `modelConcurrency` | 生成和校验 0.34.1/schema 18 的固定样板，调用旧基点源码的旧参数、旧表及固定备注文字。 |
| `packaging/generate-upgrade-fixture.py:199:17` | `familyAnnotationChanges` | 生成和校验 0.34.1/schema 18 的固定样板，调用旧基点源码的旧参数、旧表及固定备注文字。 |
| `packaging/generate-upgrade-fixture.py:281:17` | `familyPreferenceChanges` | 生成和校验 0.34.1/schema 18 的固定样板，调用旧基点源码的旧参数、旧表及固定备注文字。 |
| `packaging/generate-upgrade-fixture.py:282:84` | `family` | 生成和校验 0.34.1/schema 18 的固定样板，调用旧基点源码的旧参数、旧表及固定备注文字。 |
| `packaging/generate-upgrade-fixture.py:417:44` | `family_annotations` | 生成和校验 0.34.1/schema 18 的固定样板，调用旧基点源码的旧参数、旧表及固定备注文字。 |
| `tests/python/blackboard/store/test_upgrade_fixture.py:68:77` | `family_preferences` | 读取或修改旧 schema 18 样板，或断言旧偏好表不被带到新看板。 |
| `tests/python/blackboard/store/test_upgrade_fixture.py:78:73` | `family_preferences` | 读取或修改旧 schema 18 样板，或断言旧偏好表不被带到新看板。 |
| `tests/python/blackboard/store/test_upgrade_fixture.py:79:24` | `family_annotations` | 读取或修改旧 schema 18 样板，或断言旧偏好表不被带到新看板。 |
| `tests/python/blackboard/store/test_upgrade_fixture.py:79:46` | `model_concurrency` | 读取或修改旧 schema 18 样板，或断言旧偏好表不被带到新看板。 |
| `tests/python/blackboard/store/test_upgrade_fixture.py:196:34` | `family_preferences` | 读取或修改旧 schema 18 样板，或断言旧偏好表不被带到新看板。 |
| `tests/python/blackboard/store/test_upgrade_fixture.py:197:34` | `family_preferences` | 读取或修改旧 schema 18 样板，或断言旧偏好表不被带到新看板。 |
| `tests/python/blackboard/store/test_upgrade_fixture.py:215:27` | `family_preferences` | 读取或修改旧 schema 18 样板，或断言旧偏好表不被带到新看板。 |
| `tests/python/blackboard/store/test_upgrade_fixture.py:226:65` | `family_preferences` | 读取或修改旧 schema 18 样板，或断言旧偏好表不被带到新看板。 |
| `src/hey_my_buddy/console/assets/assets/index-BI7ePOsQ.js:8:11998` | `fontFamily` | 依赖生成的 React 字体属性映射或 CSS 解析器字体类型名。 |
| `src/hey_my_buddy/console/assets/assets/index-BI7ePOsQ.js:8:12012` | `font-family` | 依赖生成的 React 字体属性映射或 CSS 解析器字体类型名。 |
| `src/hey_my_buddy/console/assets/assets/index-BI7ePOsQ.js:9:66120` | `family-name` | 依赖生成的 React 字体属性映射或 CSS 解析器字体类型名。 |
| `src/hey_my_buddy/console/assets/assets/index-BI7ePOsQ.js:9:66196` | `family-name` | 依赖生成的 React 字体属性映射或 CSS 解析器字体类型名。 |
| `src/hey_my_buddy/console/assets/assets/index-Qm-l-qr6.css:2:2792` | `--default-font-family` | 标准字体 CSS 属性或字体变量，显示样式保持原值。 |
| `src/hey_my_buddy/console/assets/assets/index-Qm-l-qr6.css:2:2899` | `--default-mono-font-family` | 标准字体 CSS 属性或字体变量，显示样式保持原值。 |
| `src/hey_my_buddy/console/assets/assets/index-Qm-l-qr6.css:2:3172` | `font-family` | 标准字体 CSS 属性或字体变量，显示样式保持原值。 |
| `src/hey_my_buddy/console/assets/assets/index-Qm-l-qr6.css:2:3198` | `--default-font-family` | 标准字体 CSS 属性或字体变量，显示样式保持原值。 |
| `src/hey_my_buddy/console/assets/assets/index-Qm-l-qr6.css:2:3980` | `font-family` | 标准字体 CSS 属性或字体变量，显示样式保持原值。 |
| `src/hey_my_buddy/console/assets/assets/index-Qm-l-qr6.css:2:4011` | `--default-mono-font-family` | 标准字体 CSS 属性或字体变量，显示样式保持原值。 |
| `src/hey_my_buddy/console/assets/assets/index-Qm-l-qr6.css:2:6818` | `font-family` | 标准字体 CSS 属性或字体变量，显示样式保持原值。 |
| `src/hey_my_buddy/console/assets/assets/index-Qm-l-qr6.css:2:20990` | `font-family` | 标准字体 CSS 属性或字体变量，显示样式保持原值。 |
| `src/hey_my_buddy/console/assets/assets/index-Qm-l-qr6.css:2:75717` | `font-family` | 标准字体 CSS 属性或字体变量，显示样式保持原值。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:14:29` | `family_annotations` | 固定升级基点的旧表读取映射；当前写入只认新表。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:15:35` | `model_concurrency` | 固定升级基点的旧表读取映射；当前写入只认新表。 |
| `tests/python/blackboard/evaluation/test_evaluation.py:275:30` | `familyPreferenceChanges` | 拒绝旧偏好层输入，或断言发布结果不携带已删除的旧字段。 |
| `apps/console/src/buddies/catalogue-view.test.tsx:201:48` | `familyPreferenceChanges` | 拒绝旧偏好层输入，或断言发布结果不携带已删除的旧字段。 |
| `apps/console/src/buddy-settings.test.tsx:336:52` | `familyPreferenceChanges` | 拒绝旧偏好层输入，或断言发布结果不携带已删除的旧字段。 |
| `apps/console/src/styles.css:155:14` | `font-family` | 标准字体 CSS 属性或字体变量，显示样式保持原值。 |
| `apps/console/design/work-objective-timeline.html:129:14` | `font-family` | 标准字体 CSS 属性或字体变量，显示样式保持原值。 |
| `tests/python/install/test_upgrade_migration.py:25:29` | `family_annotations` | 从旧基点表读取独立的原始设置值供升级后比较。 |
| `tests/python/install/test_upgrade_migration.py:26:35` | `model_concurrency` | 从旧基点表读取独立的原始设置值供升级后比较。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/native_run.py:481:11` | `families` | 原生 stream 事件的类别。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py:838:57` | `unfamiliar` | unfamiliar 表示未知的原生协议内容，不是模型服务。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:794:48` | `family` | 原生 marker-frame 的类别。 |
| `tests/python/buddy/harnesses/codex/test_native_run.py:466:16` | `test_unfamiliar_startup_items_continue_and_reach_result_facts` | unfamiliar 表示未知的原生协议内容，不是模型服务。 |
| `tests/python/buddy/harnesses/codex/test_native_run.py:496:16` | `test_unfamiliar_drain_frame_continues_and_retains_unknown_shape` | unfamiliar 表示未知的原生协议内容，不是模型服务。 |

额外检查 `tests/probes/objective_console_preview.py:1271`：备注文字 `synthetic preview family note` 保留 1 处，因为它直接显示在界面中，开发者要求显示文字一字不动；它的字段已成为 `modelServiceNotes`，内部函数已成为 `model_service`，`modelFamily` 已删除。

## 检查、构建与后续

Python 使用 Host 指定的只读解释器与 `PYTHONPATH=src`，仅经 `-m hey_my_buddy.cli.checks` 运行，未给进程数参数、未设 `BUDDY_CHECKS_JOBS`；运行的目录为 `tests/python/blackboard/catalog`、`evaluation`、`routing`、`service`、`store`、`tasks`、`tests/python/cli`、`tests/python/console`、`tests/python/install`，并明确列入 `tests/python/cli/test_repository_hygiene.py`。私有检查根、主目录、状态、运行时、临时目录和 uv 缓存均放在本检出的 `tmp/model-service-rename/` 内，继承的运行时、Worker 与凭据变量已清除。

指定目录的检查退出 1，137 个文件中 116 个通过（runner 报告这些文件运行 1,675 项测试），21 个文件失败。`test_repository_hygiene.py` 通过，私有检查根已移除。把未改的 `c4c6ac56` 源码放入检出内的临时基线副本，仅复跑这 21 个失败文件：同样退出 1、同样清理私有根；归一化改过的文件、类和测试名字之后，失败文件与 65 个失败用例的身份逐项一致。

失败分为两个检出内临时根造成的问题：macOS 的 AF_UNIX 预检地址过长，直接报 `AF_UNIX path too long`，随后表现为 `LAUNCH_ACCESS_DENIED` 或盖过测试原先期待的其他错误码；测试目录位于一个已有 Git 检出内部，无独立 Git 标记的工作目录被上溯识别为本检出，造成 `WORKSPACE_BUSY`、读取快照互斥、项目路径与名称断言失败，以及 shared stash 假对象没有匹配到其预期目录。相关根因不属于模型服务改名；工作区实现又由另一微任务修改，未在这里修补。

| 失败文件 | 失败用例数 | 原因 |
| --- | --- | --- |
| `tests/python/blackboard/evaluation/test_native_observations.py` | 2 | 检出内测试目录被识别为同一 Git 检出，导致工作区、项目归属或 shared stash 断言失败。 |
| `tests/python/blackboard/routing/test_routing_history.py` | 1 | 检出内测试目录被识别为同一 Git 检出，导致工作区、项目归属或 shared stash 断言失败。 |
| `tests/python/blackboard/service/test_governed_service_stop.py` | 1 | 检出内测试目录被识别为同一 Git 检出，导致工作区、项目归属或 shared stash 断言失败。 |
| `tests/python/blackboard/service/test_service_environment.py` | 2 | AF_UNIX 预检地址过长及其 LAUNCH_ACCESS_DENIED 结果。 |
| `tests/python/blackboard/store/test_blackboard.py` | 1 | AF_UNIX 预检地址过长及其 LAUNCH_ACCESS_DENIED 结果。 |
| `tests/python/blackboard/store/test_store.py` | 5 | 检出内测试目录被识别为同一 Git 检出，导致工作区、项目归属或 shared stash 断言失败。 |
| `tests/python/blackboard/tasks/test_delegation_history.py` | 3 | 检出内测试目录被识别为同一 Git 检出，导致工作区、项目归属或 shared stash 断言失败。 |
| `tests/python/blackboard/tasks/test_model_service_concurrency.py` | 7 | 检出内测试目录被识别为同一 Git 检出，导致工作区、项目归属或 shared stash 断言失败。 |
| `tests/python/blackboard/tasks/test_objectives.py` | 6 | 检出内测试目录被识别为同一 Git 检出，导致工作区、项目归属或 shared stash 断言失败。 |
| `tests/python/blackboard/tasks/test_permission_levels.py` | 3 | 检出内测试目录被识别为同一 Git 检出，导致工作区、项目归属或 shared stash 断言失败。 |
| `tests/python/blackboard/tasks/test_scheduler_lanes.py` | 6 | 检出内测试目录被识别为同一 Git 检出，导致工作区、项目归属或 shared stash 断言失败。 |
| `tests/python/blackboard/tasks/test_worker_claim_stash.py` | 7 | 检出内测试目录被识别为同一 Git 检出，导致工作区、项目归属或 shared stash 断言失败。 |
| `tests/python/blackboard/tasks/test_workflow.py` | 3 | 检出内测试目录被识别为同一 Git 检出，导致工作区、项目归属或 shared stash 断言失败。 |
| `tests/python/blackboard/tasks/test_workflow_stash.py` | 8 | 检出内测试目录被识别为同一 Git 检出，导致工作区、项目归属或 shared stash 断言失败。 |
| `tests/python/blackboard/tasks/test_workspace_stash.py` | 1 | 检出内测试目录被识别为同一 Git 检出，导致工作区、项目归属或 shared stash 断言失败。 |
| `tests/python/cli/test_support_cleanup.py` | 2 | AF_UNIX 预检地址过长及其 LAUNCH_ACCESS_DENIED 结果。 |
| `tests/python/console/test_console_reads_http.py` | 2 | 检出内测试目录被识别为同一 Git 检出，导致工作区、项目归属或 shared stash 断言失败。 |
| `tests/python/console/test_console_sessions.py` | 1 | AF_UNIX 预检地址过长及其 LAUNCH_ACCESS_DENIED 结果。 |
| `tests/python/install/test_first_install.py` | 1 | AF_UNIX 预检地址过长及其 LAUNCH_ACCESS_DENIED 结果。 |
| `tests/python/install/test_local_launcher.py` | 1 | AF_UNIX 预检地址过长及其 LAUNCH_ACCESS_DENIED 结果。 |
| `tests/python/install/test_packaging.py` | 2 | AF_UNIX 预检地址过长及其 LAUNCH_ACCESS_DENIED 结果。 |

`npm --prefix apps/console ci` 成功；使用支持的 Node 24.15.0，运行时、npm 缓存、浏览器下载和临时目录都在检出内。最终源码的 `npm --prefix apps/console test` 通过 78 个文件、1,088 项测试；`npm --prefix apps/console run test:browser` 通过 96 项，其中现有 `build` 命令完成 TypeScript 检查与 Vite 构建并重建 `src/hey_my_buddy/console/assets/`。首次前端测试发现的测试局部变量与函数同名问题已在该测试内改名修复，并在最终全量测试中通过。

CSS 去掉注释后与基点逐字相同，13 个 i18n 源文件逐字相同；前端显示的中文文字与合成预览备注均保留原值。最终浏览器检查通过；生成资产中旧契约字段已消失，剩下的 13 处 family 词干均是字体属性或依赖的 CSS 解析类型。最终 `git diff --check` 通过，改过的 38 个 Python 文件可解析，受保护的文档、skill、README、样板、版本与不属本微任务的工作区文件均未改。

说明文件写完后单独复跑 `test_repository_hygiene.py`，5 项测试通过，退出 0 且私有根已清理；另核对说明文件没有本机个人路径，所有 83 个交付路径都在授权范围内。

原始检查日志与清单在忽略的 `tmp/model-service-rename/` 内：`python-checks.log`、`python-baseline-checks.log`、`python-hygiene-final.log`、`baseline-comparison.json`、`console/npm-ci.log`、`console/npm-test-final-restored.log`、`console/test-browser.log` 和改名、残留清单。Python 验证需要 Host 提供本检出外、地址足够短且不属于其他 Git 检出的私有测试根，再按同一指定命令复跑上述目录；本微任务没有自行越过临时文件范围。完整检查与最终验收留给 Host 整合之后完成，没有安装到日常环境。
