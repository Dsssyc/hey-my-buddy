# buddy 一族改名记录

本微任务基于 `e0e857d3`，同步单个 buddy 的存储、后端、命令行、原生回执、测试、控制台源码与构建资产，只改名；版本 0.34.1 和 schema 19 没有改动。根 `tests/fixtures/`、基点生成器、其它命名族和 Host 保留的文档均未改动。

新板使用 `buddies`、`router_buddies`、`buddy_id`、`decision_buddy_id`、`buddy_json`、`buddy_revision`、`validated_buddy_revision` 与 `buddy_locked`。设置升级有独立的 `UPGRADE_BASE_TABLES` 和 `UPGRADE_BASE_COLUMNS` 对照，固定读 schema 18 的旧名，固定写当前名；Router 的旧 meta 列表键也只在这个读取边界出现。升级测试继续逐表对照全新板及设置，并校验归档字节。

`suggestedProfileId` 只改为 `suggestedBuddyId`，原有字符串与完整四元组两种形态保持不变。`evaluation_profiles_adapter_idx` 按清单改为 `buddies_harness_idx`；其 adapter 列和其余 adapter 一族仍留给后续微任务。

顺带补上 `test_scheduler_lanes.py` 中 `w-free` 认领的任务 `requestId` 就是 `runnable-b` 的断言，删除 console `command` 方法中已经不适用的设置版本号并发检查注释。

计数以 HEAD 与交付树的仓库源码文本比较，排除生成的 console assets 和依赖目录，按完整名字记录旧名净减少的处数；包含引用、测试名、注释。基点映射中需要保留或新增的旧字段名列在保留清单，因此净减少不等同于 patch 的新增/删除行数。下面的内部名字是清单 internal 行没有逐项列名的同一族；不同名字的计数可重叠，不相加当作代码行数。

## 改名和处数

| 旧名 | 新名 | 旧名净减少 | 来源 |
| --- | --- | ---: | --- |
| `CONFIGURATION` | `BUDDY` | 141 | 清单外逐项名字 |
| `CONFIGURATION_CONFLICT` | `BUDDY_CONFLICT` | 6 | 清单 |
| `CONFIGURATION_FIELDS` | `BUDDY_FIELDS` | 32 | 清单外逐项名字 |
| `CONFIGURATION_REQUIRED` | `BUDDY_REQUIRED` | 7 | 清单 |
| `CONFIGURATION_UNAVAILABLE` | `BUDDY_UNAVAILABLE` | 29 | 清单 |
| `CheckedConfiguration` | `CheckedBuddy` | 22 | 清单外逐项名字 |
| `ConfigurationStyle` | `BuddyStyle` | 7 | 清单外逐项名字 |
| `ConfigurationTests` | `BuddyTests` | 1 | 清单外逐项名字 |
| `DECISION_PROFILE` | `DECISION_BUDDY` | 3 | 清单外逐项名字 |
| `DISABLED_DSH_PROFILE` | `DISABLED_DSH_BUDDY` | 3 | 清单外逐项名字 |
| `DSH_HIGH_PROFILE` | `DSH_HIGH_BUDDY` | 3 | 清单外逐项名字 |
| `DSH_PROFILE` | `DSH_BUDDY` | 6 | 清单外逐项名字 |
| `ExcludedProfile` | `ExcludedBuddy` | 4 | 清单外逐项名字 |
| `ExecutionConfiguration` | `BuddyIdentity` | 12 | 清单外逐项名字 |
| `FIXTURE_CONFIGURATION` | `FIXTURE_BUDDY` | 2 | 清单外逐项名字 |
| `FLASH_LOW_PROFILE_ID` | `FLASH_LOW_BUDDY_ID` | 5 | 清单外逐项名字 |
| `FLASH_PROFILE_ID` | `FLASH_BUDDY_ID` | 25 | 清单外逐项名字 |
| `FriendlyProfile` | `FriendlyBuddy` | 15 | 清单外逐项名字 |
| `INVALID_CONFIGURATION` | `INVALID_BUDDY` | 1 | 清单 |
| `MAX_DECISION_PROFILES` | `MAX_DECISION_BUDDIES` | 7 | 清单外逐项名字 |
| `MAX_HISTORY_PROFILES` | `MAX_HISTORY_BUDDIES` | 7 | 清单外逐项名字 |
| `ModelProfilesCliTests` | `ModelBuddiesCliTests` | 1 | 清单外逐项名字 |
| `NEEDS_HOST_NO_PROFILE` | `NEEDS_HOST_NO_BUDDY` | 2 | 清单外逐项名字 |
| `NamedProfile` | `NamedBuddy` | 4 | 清单外逐项名字 |
| `PARTIAL_CONFIGURATION_HINT` | `PARTIAL_BUDDY_HINT` | 5 | 清单外逐项名字 |
| `PROFILE` | `BUDDY` | 53 | 清单外逐项名字 |
| `PROFILE_ID` | `BUDDY_ID` | 159 | 清单外逐项名字 |
| `PROFILE_IDS` | `BUDDY_IDS` | 11 | 清单外逐项名字 |
| `PROFILE_MODEL_SERVICE` | `BUDDY_MODEL_SERVICE` | 5 | 清单外逐项名字 |
| `PROFILE_PAGE_SIZE` | `BUDDY_PAGE_SIZE` | 4 | 清单外逐项名字 |
| `Profile` | `Buddy` | 118 | 清单外逐项名字 |
| `ProfileHistoryFilters` | `BuddyHistoryFilters` | 2 | 清单外逐项名字 |
| `ProfilePage` | `BuddyPage` | 19 | 清单外逐项名字 |
| `ProfileSettingPatch` | `BuddySettingPatch` | 3 | 清单外逐项名字 |
| `ResultConfiguration` | `ResultBuddy` | 20 | 清单外逐项名字 |
| `RunConfiguration` | `RunBuddy` | 62 | 清单外逐项名字 |
| `SECOND_PROFILE` | `SECOND_BUDDY` | 26 | 清单外逐项名字 |
| `SECOND_PROFILE_ID` | `SECOND_BUDDY_ID` | 82 | 清单外逐项名字 |
| `THIRD_PROFILE` | `THIRD_BUDDY` | 15 | 清单外逐项名字 |
| `THIRD_PROFILE_ID` | `THIRD_BUDDY_ID` | 18 | 清单外逐项名字 |
| `TimelineConfiguration` | `TimelineBuddy` | 15 | 清单外逐项名字 |
| `UNAVAILABLE_ZCODE_PROFILE` | `UNAVAILABLE_ZCODE_BUDDY` | 3 | 清单外逐项名字 |
| `ZCODE_PROFILE` | `ZCODE_BUDDY` | 4 | 清单外逐项名字 |
| `_PROFILE_EXPRESSION` | `_BUDDY_EXPRESSION` | 4 | 清单外逐项名字 |
| `_checked_configuration` | `_checked_buddy` | 4 | 清单外逐项名字 |
| `_complete_configuration` | `_complete_buddy` | 2 | 清单外逐项名字 |
| `_configuration` | `_buddy` | 12 | 清单外逐项名字 |
| `_configuration_matches_goal` | `_buddy_matches_goal` | 3 | 清单外逐项名字 |
| `_decision_profile` | `_decision_buddy` | 1 | 清单外逐项名字 |
| `_excluded_profiles` | `_excluded_buddies` | 2 | 清单外逐项名字 |
| `_full_configuration` | `_full_buddy` | 3 | 清单外逐项名字 |
| `_profile_array` | `_buddy_array` | 2 | 清单外逐项名字 |
| `_profile_content` | `_buddy_content` | 3 | 清单外逐项名字 |
| `_profile_entries` | `_buddy_entries` | 3 | 清单外逐项名字 |
| `_profile_id` | `_buddy_id` | 1 | 清单外逐项名字 |
| `_profile_input` | `_buddy_input` | 3 | 清单外逐项名字 |
| `_profile_view` | `_buddy_view` | 3 | 清单外逐项名字 |
| `_real_validate_configuration` | `_real_validate_buddy` | 2 | 清单外逐项名字 |
| `_turn_configuration` | `_turn_buddy` | 2 | 清单外逐项名字 |
| `_validated_configuration` | `_validated_buddy` | 12 | 清单外逐项名字 |
| `add_profile` | `add_buddy` | 77 | 清单外逐项名字 |
| `awaiting-configuration-validation` | `awaiting-buddy-validation` | 4 | 清单 |
| `boundProfileQuery` | `boundBuddyQuery` | 7 | 清单外逐项名字 |
| `byProfileId` | `byBuddyId` | 5 | 清单外逐项名字 |
| `config` | `buddy` | 34 | 清单外逐项名字 |
| `configuration` | `buddy` | 754 | 清单 |
| `configuration-catalog-unrecognized` | `buddy-catalog-unrecognized` | 3 | 清单 |
| `configuration-mismatch` | `buddy-mismatch` | 19 | 清单 |
| `configuration-readback-unconfirmed` | `buddy-readback-unconfirmed` | 3 | 清单 |
| `configuration-selection-unrecognized` | `buddy-selection-unrecognized` | 3 | 清单 |
| `configuration-unavailable` | `buddy-unavailable` | 9 | 清单 |
| `configurationError` | `buddyError` | 1 | 清单外逐项名字 |
| `configurationKey` | `buddyKey` | 4 | 清单外逐项名字 |
| `configurationLabel` | `buddyLabel` | 17 | 清单外逐项名字 |
| `configurationLockText` | `buddyLockText` | 8 | 清单外逐项名字 |
| `configurationLocked` | `buddyLocked` | 32 | 清单 |
| `configurationNamer` | `buddyNamer` | 9 | 清单外逐项名字 |
| `configurationPalette` | `buddyPalette` | 3 | 清单外逐项名字 |
| `configurationRawLabel` | `buddyRawLabel` | 4 | 清单外逐项名字 |
| `configurationRevision` | `buddyRevision` | 5 | 清单外逐项名字 |
| `configurationStatus` | `buddyStatus` | 2 | 清单 |
| `configurationText` | `buddyText` | 9 | 清单外逐项名字 |
| `configuration_constraints` | `buddy_constraints` | 14 | 清单外逐项名字 |
| `configuration_locked` | `buddy_locked` | 16 | 清单 |
| `configuration_overridden` | `buddy_overridden` | 5 | 清单外逐项名字 |
| `configuration_retry` | `buddy_retry` | 8 | 清单外逐项名字 |
| `configuration_selected` | `buddy_selected` | 3 | 清单外逐项名字 |
| `configuration_spec` | `buddy_spec` | 11 | 清单外逐项名字 |
| `configuration_validated` | `buddy_validated` | 1 | 清单外逐项名字 |
| `configurations` | `buddies` | 11 | 清单外逐项名字 |
| `confirmed_profiles` | `confirmed_buddies` | 2 | 清单外逐项名字 |
| `currentRouterProfileId` | `currentRouterBuddyId` | 56 | 清单 |
| `decisionProfileId` | `decisionBuddyId` | 9 | 清单外逐项名字 |
| `decision_profile` | `decision_buddy` | 13 | 清单外逐项名字 |
| `decision_profile_id` | `decision_buddy_id` | 3 | 清单 |
| `enable_fixture_configuration` | `enable_fixture_buddy` | 13 | 清单外逐项名字 |
| `enable_profile` | `enable_buddy` | 5 | 清单外逐项名字 |
| `evaluation_profiles` | `buddies` | 144 | 清单 |
| `evaluation_profiles_adapter_idx` | `buddies_harness_idx` | 1 | 清单 |
| `evaluation_profiles_current_idx` | `buddies_current_idx` | 1 | 清单 |
| `excludedProfileText` | `excludedBuddyText` | 2 | 清单外逐项名字 |
| `excludedProfiles` | `excludedBuddies` | 11 | 清单 |
| `executionConfiguration` | `buddy` | 89 | 清单 |
| `executionConfigurationRevision` | `buddyRevision` | 26 | 清单 |
| `execution_configuration_json` | `buddy_json` | 17 | 清单 |
| `execution_configuration_revision` | `buddy_revision` | 18 | 清单 |
| `fastRouterProfileId` | `fastRouterBuddyId` | 10 | 清单外逐项名字 |
| `freshProfiles` | `freshBuddies` | 3 | 清单外逐项名字 |
| `invalid-configuration` | `invalid-buddy` | 15 | 清单 |
| `listed_profile` | `listed_buddy` | 5 | 清单外逐项名字 |
| `model-profiles` | `buddy-list` | 9 | 清单 |
| `model.profiles` | `buddy.list` | 2 | 清单 |
| `model_profiles` | `buddy_list` | 62 | 清单 |
| `normalize_configuration` | `normalize_buddy` | 3 | 清单外逐项名字 |
| `observedProfile` | `observedBuddy` | 2 | 清单 |
| `onSelectProfile` | `onSelectBuddy` | 4 | 清单外逐项名字 |
| `original_configuration` | `original_buddy` | 2 | 清单外逐项名字 |
| `parseProfilePage` | `parseBuddyPage` | 2 | 清单外逐项名字 |
| `pendingProfiles` | `pendingBuddies` | 3 | 清单外逐项名字 |
| `prefer_profile_ids` | `prefer_buddy_ids` | 8 | 清单外逐项名字 |
| `profile` | `buddy` | 690 | 清单 |
| `profile-display` | `buddy-name` | 8 | 清单外逐项名字 |
| `profileDisplayName` | `buddyDisplayName` | 2 | 清单外逐项名字 |
| `profileId` | `buddyId` | 958 | 清单 |
| `profileIds` | `buddyIds` | 5 | 清单 |
| `profileName` | `buddyName` | 12 | 清单外逐项名字 |
| `profileSettings` | `buddySettings` | 76 | 清单 |
| `profileTitle` | `buddyTitle` | 16 | 清单外逐项名字 |
| `profileTitleOr` | `buddyTitleOr` | 4 | 清单外逐项名字 |
| `profile_enabled` | `buddy_enabled` | 5 | 清单外逐项名字 |
| `profile_id` | `buddy_id` | 501 | 清单 |
| `profile_ids` | `buddy_ids` | 27 | 清单外逐项名字 |
| `profile_problem` | `buddy_problem` | 10 | 清单外逐项名字 |
| `profile_row` | `buddy_row` | 9 | 清单外逐项名字 |
| `profile_timeline` | `buddy_timeline` | 6 | 清单外逐项名字 |
| `profile_views` | `buddy_views` | 4 | 清单外逐项名字 |
| `profiles` | `buddies` | 651 | 清单 |
| `proposed_profiles` | `proposed_buddies` | 13 | 清单外逐项名字 |
| `quotaRetryProfileIds` | `quotaRetryBuddyIds` | 3 | 清单 |
| `requestedProfile` | `requestedBuddy` | 6 | 清单 |
| `require_suggested_profile` | `require_suggested_buddy` | 3 | 清单外逐项名字 |
| `resolvedProfile` | `resolvedBuddy` | 2 | 清单 |
| `reviewRouterProfileId` | `reviewRouterBuddyId` | 9 | 清单外逐项名字 |
| `routerProfile` | `routerBuddy` | 21 | 清单 |
| `routerProfileId` | `routerBuddyId` | 51 | 清单 |
| `routerProfileIds` | `routerBuddyIds` | 245 | 清单 |
| `router_outcomes_profile_idx` | `router_outcomes_buddy_idx` | 1 | 清单 |
| `router_profile_ids` | `router_buddy_ids` | 17 | 清单 |
| `router_profiles` | `router_buddies` | 6 | 清单外逐项名字 |
| `routingProfile` | `routingBuddy` | 1 | 清单外逐项名字 |
| `run_configuration_json` | `run_buddy_json` | 2 | 清单外逐项名字 |
| `seed_decision_profile` | `seed_decision_buddy` | 2 | 清单外逐项名字 |
| `seed_profiles` | `seed_buddies` | 5 | 清单外逐项名字 |
| `selectedProfile` | `selectedBuddy` | 58 | 清单 |
| `selectedProfileId` | `selectedBuddyId` | 5 | 清单外逐项名字 |
| `suggestedProfileId` | `suggestedBuddyId` | 41 | 清单 |
| `suggested_profile` | `suggested_buddy` | 4 | 清单外逐项名字 |
| `supplied_configuration` | `supplied_buddy` | 9 | 清单外逐项名字 |
| `test_a_binding_for_another_goal_or_configuration_is_refused` | `test_a_binding_for_another_goal_or_buddy_is_refused` | 1 | 清单外逐项名字 |
| `test_a_configuration_change_reconstructs_without_a_resume_reason` | `test_a_buddy_change_reconstructs_without_a_resume_reason` | 1 | 清单外逐项名字 |
| `test_an_explicit_null_suggested_profile_is_the_accepted_no_suggestion` | `test_an_explicit_null_suggested_buddy_is_the_accepted_no_suggestion` | 1 | 清单外逐项名字 |
| `test_applied_reading_keeps_current_profiles_and_pending_absence` | `test_applied_reading_keeps_current_buddies_and_pending_absence` | 1 | 清单外逐项名字 |
| `test_blank_configuration_and_previous_session_are_refused_at_the_boundary` | `test_blank_buddy_and_previous_session_are_refused_at_the_boundary` | 1 | 清单外逐项名字 |
| `test_blank_configuration_fields_are_refused_at_the_request_boundary` | `test_blank_buddy_fields_are_refused_at_the_request_boundary` | 1 | 清单外逐项名字 |
| `test_buddy_preferences_are_saved_per_profile_and_can_be_cleared` | `test_buddy_preferences_are_saved_per_buddy_and_can_be_cleared` | 1 | 清单外逐项名字 |
| `test_changed_input_or_configuration_cannot_reuse_approval` | `test_changed_input_or_buddy_cannot_reuse_approval` | 1 | 清单外逐项名字 |
| `test_codex_profile_suggestion_is_required_and_nullable` | `test_codex_buddy_suggestion_is_required_and_nullable` | 1 | 清单外逐项名字 |
| `test_codex_schema_accepts_a_nullable_profile_suggestion` | `test_codex_schema_accepts_a_nullable_buddy_suggestion` | 1 | 清单外逐项名字 |
| `test_configuration_carries_router_profile_and_budget_without_maintenance` | `test_configuration_carries_router_buddy_and_budget_without_maintenance` | 1 | 清单外逐项名字 |
| `test_configuration_required_error_explains_same_boundary_commands` | `test_buddy_required_error_explains_same_boundary_commands` | 1 | 清单外逐项名字 |
| `test_configuration_validation_is_once_per_revision_even_while_capacity_is_full` | `test_buddy_validation_is_once_per_revision_even_while_capacity_is_full` | 1 | 清单外逐项名字 |
| `test_continue_reports_every_required_configuration_string` | `test_continue_reports_every_required_buddy_string` | 1 | 清单外逐项名字 |
| `test_effort_retirement_changes_profile_once` | `test_effort_retirement_changes_buddy_once` | 1 | 清单外逐项名字 |
| `test_explicit_host_configuration_is_reminded_and_never_consumes_a_window` | `test_explicit_host_buddy_is_reminded_and_never_consumes_a_window` | 1 | 清单外逐项名字 |
| `test_failed_explicit_configuration_can_change_with_a_reason` | `test_failed_explicit_buddy_can_change_with_a_reason` | 1 | 清单外逐项名字 |
| `test_hard_bounds_and_unavailable_profiles_do_not_create_preference_matches` | `test_hard_bounds_and_unavailable_buddies_do_not_create_preference_matches` | 1 | 清单外逐项名字 |
| `test_host_configuration_cannot_break_locked_constraints` | `test_host_buddy_cannot_break_locked_constraints` | 1 | 清单外逐项名字 |
| `test_initialize_existing_profiles_preserves_catalog_reasons` | `test_initialize_existing_buddies_preserves_catalog_reasons` | 1 | 清单外逐项名字 |
| `test_invalid_argument_refusal_recovers_and_null_profile_means_no_suggestion` | `test_invalid_argument_refusal_recovers_and_null_buddy_means_no_suggestion` | 1 | 清单外逐项名字 |
| `test_invalid_null_profile_does_not_count_as_abstention` | `test_invalid_null_buddy_does_not_count_as_abstention` | 1 | 清单外逐项名字 |
| `test_later_profiles_and_preferences_cannot_replace_frozen_facts` | `test_later_buddies_and_preferences_cannot_replace_frozen_facts` | 1 | 清单外逐项名字 |
| `test_locked_configuration_is_part_of_identity_and_survives_failure` | `test_locked_buddy_is_part_of_identity_and_survives_failure` | 1 | 清单外逐项名字 |
| `test_metadata_fields_update_profile_facts` | `test_metadata_fields_update_buddy_facts` | 1 | 清单外逐项名字 |
| `test_missing_decision_profile_is_needs_host_without_a_model_call` | `test_missing_decision_buddy_is_needs_host_without_a_model_call` | 1 | 清单外逐项名字 |
| `test_model_profiles_pages_carry_the_same_model_service_rows` | `test_model_buddies_pages_carry_the_same_model_service_rows` | 1 | 清单外逐项名字 |
| `test_model_profiles_uses_the_named_schema_and_cursor` | `test_model_buddies_uses_the_named_schema_and_cursor` | 1 | 清单外逐项名字 |
| `test_native_ids_with_context_suffixes_keep_legal_distinct_profile_ids` | `test_native_ids_with_context_suffixes_keep_legal_distinct_buddy_ids` | 1 | 清单外逐项名字 |
| `test_native_resume_requires_same_configuration_and_exact_previous_session` | `test_native_resume_requires_same_buddy_and_exact_previous_session` | 1 | 清单外逐项名字 |
| `test_native_resume_requires_same_configuration_and_reconstruction_uses_a_new_root` | `test_native_resume_requires_same_buddy_and_reconstruction_uses_a_new_root` | 1 | 清单外逐项名字 |
| `test_omitted_configuration_has_one_durable_decision_and_immutable_request` | `test_omitted_buddy_has_one_durable_decision_and_immutable_request` | 1 | 清单外逐项名字 |
| `test_override_cannot_enable_a_disabled_configuration` | `test_override_cannot_enable_a_disabled_buddy` | 1 | 清单外逐项名字 |
| `test_override_file_route_without_a_profile_row_refuses_not_enabled` | `test_override_file_route_without_a_buddy_row_refuses_not_enabled` | 1 | 清单外逐项名字 |
| `test_oversized_goal_stops_before_selection_and_recovers_with_explicit_configuration` | `test_oversized_goal_stops_before_selection_and_recovers_with_explicit_buddy` | 1 | 清单外逐项名字 |
| `test_partial_configurations_and_model_less_work_have_no_model_service` | `test_partial_buddies_and_model_less_work_have_no_model_service` | 1 | 清单外逐项名字 |
| `test_per_profile_reads_use_the_partial_outcome_index` | `test_per_buddy_reads_use_the_partial_outcome_index` | 1 | 清单外逐项名字 |
| `test_pin_requires_an_available_enabled_profile` | `test_pin_requires_an_available_enabled_buddy` | 1 | 清单外逐项名字 |
| `test_profile_execution_identity_is_program_owned` | `test_buddy_execution_identity_is_program_owned` | 1 | 清单外逐项名字 |
| `test_profile_page_is_a_consistent_snapshot_during_a_concurrent_save` | `test_buddy_page_is_a_consistent_snapshot_during_a_concurrent_save` | 1 | 清单外逐项名字 |
| `test_profile_permissions_follow_worker_adapter_declarations` | `test_buddy_permissions_follow_worker_adapter_declarations` | 1 | 清单外逐项名字 |
| `test_profile_problem_keeps_each_cached_unavailability_reason` | `test_buddy_problem_keeps_each_cached_unavailability_reason` | 1 | 清单外逐项名字 |
| `test_profile_problem_orders_identity_before_availability` | `test_buddy_problem_orders_identity_before_availability` | 1 | 清单外逐项名字 |
| `test_profile_removal_incomplete_disabled_and_unavailable_are_specific_boundaries` | `test_buddy_removal_incomplete_disabled_and_unavailable_are_specific_boundaries` | 1 | 清单外逐项名字 |
| `test_provenance_and_discovery_identity_alone_do_not_change_profile_facts` | `test_provenance_and_discovery_identity_alone_do_not_change_buddy_facts` | 1 | 清单外逐项名字 |
| `test_query_matches_task_project_hosts_and_configuration` | `test_query_matches_task_project_hosts_and_buddy` | 1 | 清单外逐项名字 |
| `test_quota_failure_cannot_override_a_user_locked_configuration` | `test_quota_failure_cannot_override_a_user_locked_buddy` | 1 | 清单外逐项名字 |
| `test_quota_failure_seals_then_continues_a_new_configuration_in_the_same_checkout` | `test_quota_failure_seals_then_continues_a_new_buddy_in_the_same_checkout` | 1 | 清单外逐项名字 |
| `test_rejected_null_profile_cannot_hide_a_failed_answer_in_the_overview` | `test_rejected_null_buddy_cannot_hide_a_failed_answer_in_the_overview` | 1 | 清单外逐项名字 |
| `test_reroute_after_configuration_fix_is_durable_and_idempotent` | `test_reroute_after_buddy_fix_is_durable_and_idempotent` | 1 | 清单外逐项名字 |
| `test_resolved_reroute_history_keeps_its_frozen_selection_after_profiles_change` | `test_resolved_reroute_history_keeps_its_frozen_selection_after_buddies_change` | 1 | 清单外逐项名字 |
| `test_retired_configuration_history_is_paginated_and_new_profiles_disabled` | `test_retired_buddy_history_is_paginated_and_new_buddies_disabled` | 1 | 清单外逐项名字 |
| `test_run_configuration_boundary_refuses_missing_or_blank_fields` | `test_run_buddy_boundary_refuses_missing_or_blank_fields` | 1 | 清单外逐项名字 |
| `test_snapshot_and_model_profiles_expose_catalog_status_fields` | `test_snapshot_and_model_buddies_expose_catalog_status_fields` | 1 | 清单外逐项名字 |
| `test_stat_change_reprobes_without_changing_profile_facts` | `test_stat_change_reprobes_without_changing_buddy_facts` | 1 | 清单外逐项名字 |
| `test_unknown_reading_keeps_profiles_unless_it_restores_retained_availability` | `test_unknown_reading_keeps_buddies_unless_it_restores_retained_availability` | 1 | 清单外逐项名字 |
| `test_unread_catalog_continues_the_turn_without_checked_configuration` | `test_unread_catalog_continues_the_turn_without_checked_buddy` | 1 | 清单外逐项名字 |
| `test_unrecognized_thought_level_is_unknown_and_configuration_continues` | `test_unrecognized_thought_level_is_unknown_and_buddy_continues` | 1 | 清单外逐项名字 |
| `test_user_pin_on_a_pending_profile_still_resolves` | `test_user_pin_on_a_pending_buddy_still_resolves` | 1 | 清单外逐项名字 |
| `unavailableProfileCount` | `unavailableBuddyCount` | 19 | 清单 |
| `use-profile-history` | `use-buddy-history` | 4 | 清单外逐项名字 |
| `useProfileHistory` | `useBuddyHistory` | 11 | 清单外逐项名字 |
| `userPreferredProfileIds` | `userPreferredBuddyIds` | 8 | 清单 |
| `validRouterProfileIds` | `validRouterBuddyIds` | 2 | 清单外逐项名字 |
| `validate_configuration` | `validate_buddy` | 17 | 清单外逐项名字 |
| `validated_configuration_revision` | `validated_buddy_revision` | 11 | 清单 |
| `workflow.configuration_overridden` | `workflow.buddy_overridden` | 5 | 清单 |
| `workflow.configuration_selected` | `workflow.buddy_selected` | 3 | 清单 |
| `workflow.configuration_validated` | `workflow.buddy_validated` | 1 | 清单 |
| `PROFILE` | `CATALOG_BUDDY` | 39 | 清单外逐项名字 |

清单外逐项改名共 176 个名字，全部列在上表标为“清单外逐项名字”的行；包含原生 `RunConfiguration`、`CheckedConfiguration`、`ResultConfiguration`，内部 buddy 身份 helper、测试方法名和标识符，以及前端类型与局部变量。清单中已经删除、当前树不再存在的评价卡片/证据表索引、事件等不重建。

改名文件：`apps/console/src/profile-display.ts` → `buddy-name.ts`；`profile-display.test.tsx` → `buddy-name.test.tsx`；`use-profile-history.ts` → `use-buddy-history.ts`；`use-profile-history.test.ts` → `use-buddy-history.test.ts`；`unavailable-configs.test.tsx` → `unavailable-buddies.test.tsx`。既有 `buddy-display.ts` 承担目录/模型服务分组显示，所以姓名格式化文件使用 `buddy-name`。两个 Router configuration 测试文件的名字仍指 Router 设置，保留。

## 验证

Python 完整检查首轮：234 文件、默认 4 个 Worker 进程，223 文件通过、11 文件失败，退出码 1，私有根完整移除。改名和测试修正后的最终完整检查：234 文件全部通过，3869 项、0 skip，退出码 0，私有根完整移除。Python 检查只用 Host 指定解释器执行 `PYTHONPATH=src <指定解释器> -m hey_my_buddy.cli.checks`，使用默认进程数和检查器自建的系统临时私有根，没有向解释器环境安装东西；最终检查启动后没有再修改 Python 源码。
首轮 `blackboard/routing/test_routing_history.py`、`test_routing_preferences.py`、`test_single_router_requests.py` 与 `blackboard/tasks/test_workflow_routing.py` 因两个原有夹具 `PROFILE`、`CONFIGURATION` 同变成 `BUDDY`，导入时遮盖目录行，出现 `KeyError: buddyId`。从目录发布的那一行命名为 `CATALOG_BUDDY`，四元组仍为 `BUDDY`，所有使用者同步、没有别名。四个失败文件各单独重跑一次均退出 0，私有根移除。其它导入此目录夹具的文件也同步，并在最终全量复验。
首轮 `buddy/harnesses/codex/test_batch4_facts.py`（9 项断言）、`test_native_run.py`（4 项断言）、`test_no_tool_codex.py`（1 项断言）仍期待模型/provider 不符的旧码 `configuration-mismatch`；改为 `buddy-mismatch`，权限的 `readonly-configuration-mismatch` 保留。三个文件各单独重跑一次均退出 0，私有根移除。
首轮 `buddy/roles/test_schema_worker.py` 的 Codex、Claude schema 固定哈希因 `suggestedBuddyId` 改名而失配；完整 prompt 中也有同一字段名。静态重建基点 schema/prompt，先验证基点固定哈希，再验证当前结果仅把 `suggestedProfileId` 替为 `suggestedBuddyId`，据此更新固定哈希。该文件单独重跑一次退出 0，私有根移除。
首轮 `cli/test_checks_uv_cache.py` 与 `test_harness_test_safety.py` 因标准变量 `USERPROFILE` 被中间机械改名为 `USERBUDDY` 而失败；已将测试支持层和相关断言一并恢复 `USERPROFILE`，全树没有 USERBUDDY，产品代码里的标准名未改。两个失败文件各单独重跑一次退出 0，私有根移除。
首轮 `blackboard/tasks/test_workflow_real.py` 的 `test_collect_imports_a_turn_only_with_confirmed_shutdown` 报 role controller 没有完整当前格式结果、shutdown 未确认（48.2 秒）；按要求单独重跑同一源码仍失败（41.7 秒），两个日志均保留。进一步静态核对发现基点已有的 `REPOSITORY_ROOT = parents[3]` 在该文件搬到 `blackboard/tasks` 后指向 tests，令子进程 PYTHONPATH 不含本检出源码；修为 parents[4]，再次运行该文件 11 项通过、退出 0（41.9 秒），私有根移除。这条一行测试路径修正是保证本次改名检查实际使用本检出的阻塞修正，未修改产品的运行结束判断或行为。
npm ci 报告 1 项 high severity 依赖审计项和可选 fsevents 安装脚本提示；本微任务只改名，未自动修改依赖、锁文件或安装脚本策略。

前端：使用 Node 24.21.0，`npm --prefix apps/console ci`、typecheck、最终 `npm --prefix apps/console test` 均通过，最后一轮 78 个文件、1088 项；最终 `npm --prefix apps/console run test:browser` 的构建和 96 项 Playwright 均通过。首次 Vitest 有 3 项因分页测试 fixture 的中间操作名 `model_buddies` 漏改而失败，修为 `buddy_list` 后聚焦 5 文件 100 项及最终全量通过；随后审查修正废弃 Router 槽拒绝键与局部 config 名，最终全量和浏览器再次通过。npm 缓存与 Chromium/FFmpeg 下载均在 checkout 的 `tmp/` 内。

显示文字：前端生产 TS/TSX 的含中文 AST 字符串、模板片段和 JSX 文本，基点与交付树各 1641 个、逐字相同；i18n 资源文本未改。前端已废弃槽的拒绝规则也统一为新的 BuddyId 拼写，只有一套规则。生成 JS 为 `index-C157hAkK.js`，`index.html` 已指向它，旧 JS 被它取代，CSS 与图标散列不变。

原始检查日志和子代理说明在忽略的 `tmp/`，包括 `python-full-check.log`、`python-final-check.log`、`python-retry-*.log`、`python-fixed-blackboard.tasks.test_workflow_real.log`、`frontend-npm-ci.log`、`frontend-test.log`、`frontend-browser.log`、`frontend-notes.md`、`native-notes.md` 与 `tests-notes.md`。未提交、未安装产品、未启动日常服务、未作模型调用；本记录是 Worker 的检查证据，等待 Host 验收。

## 保留项逐处清单

以 `profile|configuration` 不分大小写扫描 `src`、`tests/python`、`tests/probes`、`apps/console`、`packaging` 的仓库文件，保留 908 处词干匹配，分布在 715 行。忽略第三方 node_modules、npm/Playwright 缓存、临时日志和 Python 字节码，遵循仓库 ignore 规则；生成的 console assets 包括在内。每行列出所有匹配列号和词干次数，理由覆盖这行的每一处。

| 文件与行 | 匹配 | 列号 | 保留理由 |
| --- | --- | --- | --- |
| `apps/console/design/work-macro-task-timeline.html:22` | configuration × 1 | 36 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/playwright.config.ts:10` | configuration × 1 | 37 | i18next 官方文档地址中的 configuration 是上游名字。 |
| `apps/console/src/App.test.tsx:15` | configuration × 1 | 3 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:2` | Configuration × 1 | 75 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:5` | configuration × 1 | 34 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:9` | Configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:10` | Configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:12` | Configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:13` | Configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:17` | Configuration × 1 | 26 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:19` | Configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:20` | Configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:21` | Configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:22` | Configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:23` | Configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:25` | Configuration × 1 | 26 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:27` | Configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:28` | Configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:33` | configuration × 1 | 9 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:34` | configuration × 2 | 122, 137 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:46` | configuration × 2 | 40, 61 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:47` | configuration × 2 | 33, 54 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:49` | configuration × 2 | 42, 63 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:52` | configuration × 1 | 40 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:68` | configuration × 2 | 40, 60 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:70` | configuration × 2 | 40, 60 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:257` | configuration × 1 | 122 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.test.ts:258` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.ts:1` | Configuration × 1 | 66 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.ts:86` | Configuration × 2 | 29, 69 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.ts:87` | Configuration × 1 | 35 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.ts:97` | Configuration × 1 | 15 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.ts:98` | configuration × 1 | 22 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.ts:99` | Configuration × 1, configuration × 2 | 12, 55, 74 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/api.ts:609` | Configuration × 1 | 15 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:12` | Configuration × 1 | 15 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:17` | Configuration × 1 | 87 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:56` | configuration × 1 | 29 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:58` | configuration × 2 | 9, 57 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:59` | configuration × 1 | 29 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:60` | configuration × 1 | 19 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:63` | configuration × 1 | 53 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:65` | configuration × 1 | 64 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:66` | configuration × 1 | 54 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:67` | configuration × 1 | 51 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:68` | configuration × 1 | 66 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:74` | configuration × 1 | 20 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:75` | configuration × 1 | 29 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:87` | Configuration × 1 | 34 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:88` | Configuration × 1 | 45 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:93` | configuration × 1 | 9 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:94` | configuration × 2 | 19, 98 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:97` | configuration × 1 | 64 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:98` | configuration × 1 | 17 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:106` | configuration × 1 | 62 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:116` | configuration × 1 | 66 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/RolesView.tsx:123` | configuration × 1 | 40 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/attention.test.ts:19` | configuration × 1 | 101 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/attention.test.ts:20` | configuration × 2 | 9, 33 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/attention.test.ts:30` | configuration × 1 | 22 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/attention.test.ts:31` | configuration × 1 | 36 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/attention.test.ts:45` | configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/attention.test.ts:59` | configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/attention.ts:14` | configuration × 3 | 9, 34, 71 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/attention.ts:16` | configuration × 1 | 8 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/attention.ts:20` | configuration × 1 | 7 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/attention.ts:21` | configuration × 1 | 15 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/attention.ts:24` | configuration × 1 | 9 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/catalogue-view.test.tsx:34` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/harness.fixtures.ts:56` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/models.test.ts:38` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/navigation-page.test.tsx:89` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:191` | configuration × 1 | 55 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:216` | configuration × 1 | 47 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:227` | configuration × 1 | 47 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:239` | configuration × 1 | 47 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:382` | configuration × 1 | 47 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:393` | configuration × 1 | 47 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:417` | configuration × 1 | 47 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:434` | configuration × 1 | 47 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:468` | configuration × 1 | 34 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:499` | configuration × 1 | 52 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:513` | configuration × 1 | 34 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:523` | configuration × 1 | 48 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:599` | configuration × 1 | 47 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:603` | configuration × 1 | 34 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:619` | configuration × 1 | 47 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:643` | configuration × 1 | 47 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:666` | configuration × 1 | 47 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles-view.test.tsx:681` | configuration × 1 | 47 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.test.ts:18` | configuration × 1 | 40 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.test.ts:167` | configuration × 2 | 17, 51 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.test.ts:173` | configuration × 1 | 44 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.test.ts:182` | configuration × 1 | 49 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.test.ts:183` | configuration × 2 | 14, 35 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.testing.ts:7` | Configuration × 1 | 15 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.testing.ts:64` | Configuration × 1, configuration × 1 | 3, 27 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.testing.ts:73` | configuration × 1 | 42 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.testing.ts:77` | configuration × 2 | 5, 20 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.testing.ts:78` | configuration × 1 | 129 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.testing.ts:79` | configuration × 2 | 5, 25 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.testing.ts:106` | configuration × 4 | 9, 30, 58, 84 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.ts:2` | Configuration × 1 | 15 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.ts:89` | Configuration × 2 | 21, 65 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.ts:90` | configuration × 3 | 16, 44, 70 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.ts:94` | Configuration × 1, configuration × 2 | 16, 36, 88 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.ts:126` | configuration × 1 | 49 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.ts:127` | configuration × 1 | 23 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddies/roles.ts:132` | configuration × 1 | 98 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddy-name.test.tsx:49` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddy-settings.test.tsx:61` | configuration × 1 | 18 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddy-settings.test.tsx:87` | configuration × 2 | 5, 28 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddy-settings.test.tsx:89` | configuration × 2 | 5, 33 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/buddy-settings.test.tsx:114` | configuration × 4 | 5, 26, 53, 79 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/catalog-status.test.tsx:66` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/console-session.test.tsx:75` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/console-session.test.tsx:179` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/desktop-console.test.tsx:53` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:5` | configuration × 1 | 3 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:54` | configuration × 1 | 3 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:131` | configuration × 1 | 7 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:135` | configuration × 1 | 67 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:174` | configuration × 1 | 12 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:177` | configuration × 2 | 7, 36 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:179` | configuration × 1 | 12 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:422` | configuration × 2 | 34, 63 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:423` | configuration × 1 | 12 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:425` | configuration × 1 | 50 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:429` | configuration × 2 | 34, 63 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:430` | configuration × 1 | 12 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:431` | configuration × 1 | 53 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:435` | configuration × 2 | 34, 63 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:438` | configuration × 1 | 49 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:444` | configuration × 1 | 60 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:445` | configuration × 2 | 39, 68 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:447` | configuration × 2 | 36, 67 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:449` | configuration × 1 | 12 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:450` | configuration × 1 | 51 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:456` | configuration × 2 | 34, 63 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:457` | configuration × 2 | 34, 63 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:460` | configuration × 1 | 26 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:461` | configuration × 1 | 65 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:466` | configuration × 1 | 38 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:467` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:472` | configuration × 1 | 70 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:475` | configuration × 1 | 25 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:480` | configuration × 2 | 34, 63 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:482` | configuration × 1 | 55 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:487` | configuration × 2 | 34, 63 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:493` | configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:494` | configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.test.ts:495` | configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:10` | Configuration × 1 | 3 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:31` | configuration × 2 | 5, 29 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:180` | Configuration × 1, configuration × 1 | 3, 32 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:188` | configuration × 1 | 54 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:246` | configuration × 2 | 17, 41 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:247` | configuration × 1 | 27 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:248` | configuration × 1 | 23 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:253` | configuration × 4 | 21, 46, 72, 110 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:256` | configuration × 1 | 17 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:281` | configuration × 1 | 9 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:282` | configuration × 1 | 11 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:284` | configuration × 1 | 39 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:286` | configuration × 1 | 51 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:288` | configuration × 1 | 43 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:289` | configuration × 1 | 78 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:311` | configuration × 2 | 5, 26 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:312` | configuration × 1 | 29 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:313` | configuration × 1 | 41 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:314` | configuration × 1 | 33 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:315` | configuration × 1 | 28 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:355` | configuration × 1 | 59 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:371` | configuration × 1 | 3 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:381` | configuration × 1 | 18 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:474` | configuration × 1 | 36 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:475` | configuration × 3 | 16, 39, 69 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:476` | configuration × 1 | 39 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:479` | configuration × 2 | 37, 73 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:480` | configuration × 2 | 20, 51 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:482` | configuration × 1 | 28 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:485` | configuration × 1 | 14 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:486` | configuration × 1 | 22 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/draft.ts:529` | configuration × 1 | 7 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/edit-mode.test.tsx:38` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/history-navigation.test.tsx:15` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/i18n/index.ts:37` | configuration × 1 | 37 | i18next 官方文档地址中的 configuration 是上游名字。 |
| `apps/console/src/macro-task-detail.test.tsx:16` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/macro-task-display.ts:679` | configuration × 1 | 58 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/macro-task-tab.test.tsx:14` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/model-service-concurrency.test.tsx:45` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.test.ts:60` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.test.ts:85` | configuration × 2 | 52, 81 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.test.ts:86` | configuration × 2 | 52, 81 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.test.ts:87` | configuration × 2 | 52, 81 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.test.ts:230` | configuration × 2 | 7, 36 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.test.ts:238` | configuration × 2 | 7, 36 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.test.ts:245` | configuration × 1 | 60 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.test.ts:248` | configuration × 2 | 36, 65 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.test.ts:250` | configuration × 1 | 51 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.test.ts:257` | configuration × 1 | 7 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.test.ts:266` | configuration × 1 | 39 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.test.ts:292` | configuration × 1 | 7 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.test.ts:305` | configuration × 2 | 9, 38 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.test.ts:313` | configuration × 2 | 7, 36 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.test.ts:316` | configuration × 1 | 48 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.ts:167` | configuration × 2 | 13, 57 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.ts:173` | configuration × 1 | 13 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.ts:176` | configuration × 1 | 32 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.ts:189` | configuration × 2 | 54, 97 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.ts:190` | configuration × 1 | 21 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.ts:198` | configuration × 1 | 56 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/policy.ts:208` | configuration × 1 | 24 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/preview-contract.test.ts:124` | configuration × 2 | 9, 33 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/preview-contract.test.ts:258` | configuration × 1 | 51 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/preview-contract.test.ts:259` | configuration × 1 | 26 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/preview-contract.test.ts:264` | configuration × 1 | 47 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/preview-contract.test.ts:265` | configuration × 1 | 45 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/routing-console.test.tsx:23` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/routing-console.test.tsx:133` | profile × 1 | 85 | 服务 reason 的英文显示值；依微任务要求保持显示文字逐字相同。 |
| `apps/console/src/routing-console.test.tsx:155` | profile × 1 | 59 | 服务 reason 的英文显示值；依微任务要求保持显示文字逐字相同。 |
| `apps/console/src/theme.test.tsx:15` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/timeline-idle-readout.test.tsx:1` | Profile × 1 | 10 | React Profiler 标准 API。 |
| `apps/console/src/timeline-idle-readout.test.tsx:134` | Profile × 2 | 13, 90 | React Profiler 标准 API。 |
| `apps/console/src/types.ts:302` | Configuration × 1 | 13 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/types.ts:398` | Configuration × 1, configuration × 1 | 3, 18 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/types.ts:399` | configuration × 1 | 3 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/types.ts:432` | configuration × 1 | 54 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/unavailable-buddies.test.tsx:69` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/unavailable-buddies.test.tsx:375` | configuration × 1 | 27 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/use-buddy-history.test.ts:25` | configuration × 1 | 122 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/use-buddy-history.test.ts:80` | configuration × 1 | 121 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/use-buddy-history.test.ts:95` | configuration × 1 | 36 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/use-buddy-history.ts:102` | configuration × 2 | 5, 29 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/use-editor.ts:8` | configuration × 2 | 32, 56 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/use-editor.ts:72` | configuration × 2 | 9, 69 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/use-editor.ts:81` | configuration × 2 | 51, 72 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `apps/console/src/use-editor.ts:289` | configuration × 1 | 5 | Router 设置、其类型、其内部变量或它的测试；包含列表、模式、预算、重试间隔，不表示单个 buddy。 |
| `packaging/generate-upgrade-fixture.py:36` | PROFILE × 1 | 1 | 生成固定 0.34.1 升级基点；Host 明确要求不改该生成器，旧表、旧列、旧字段与 native 设置均保留。 |
| `packaging/generate-upgrade-fixture.py:191` | PROFILE × 1, profile × 4 | 26, 45, 57, 87, 98 | 生成固定 0.34.1 升级基点；Host 明确要求不改该生成器，旧表、旧列、旧字段与 native 设置均保留。 |
| `packaging/generate-upgrade-fixture.py:192` | PROFILE × 1, profile × 1 | 47, 59 | 生成固定 0.34.1 升级基点；Host 明确要求不改该生成器，旧表、旧列、旧字段与 native 设置均保留。 |
| `packaging/generate-upgrade-fixture.py:196` | PROFILE × 1, Profile × 1, configuration × 1 | 26, 48, 61 | 生成固定 0.34.1 升级基点；Host 明确要求不改该生成器，旧表、旧列、旧字段与 native 设置均保留。 |
| `packaging/generate-upgrade-fixture.py:201` | PROFILE × 1, profile × 1 | 41, 53 | 生成固定 0.34.1 升级基点；Host 明确要求不改该生成器，旧表、旧列、旧字段与 native 设置均保留。 |
| `packaging/generate-upgrade-fixture.py:280` | PROFILE × 1, profile × 2 | 26, 45, 57 | 生成固定 0.34.1 升级基点；Host 明确要求不改该生成器，旧表、旧列、旧字段与 native 设置均保留。 |
| `packaging/generate-upgrade-fixture.py:319` | PROFILE × 1, Profile × 1 | 20, 33 | 生成固定 0.34.1 升级基点；Host 明确要求不改该生成器，旧表、旧列、旧字段与 native 设置均保留。 |
| `packaging/generate-upgrade-fixture.py:414` | configuration × 1 | 29 | 生成固定 0.34.1 升级基点；Host 明确要求不改该生成器，旧表、旧列、旧字段与 native 设置均保留。 |
| `packaging/generate-upgrade-fixture.py:415` | PROFILE × 1, profile × 1 | 40, 58 | 生成固定 0.34.1 升级基点；Host 明确要求不改该生成器，旧表、旧列、旧字段与 native 设置均保留。 |
| `src/hey_my_buddy/blackboard/evaluation/evaluation.py:27` | configuration × 1 | 19 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/evaluation/evaluation.py:33` | configuration × 1 | 50 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/evaluation/evaluation.py:37` | configuration × 1 | 13 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/evaluation/evaluation.py:39` | configuration × 1 | 35 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/evaluation/evaluation.py:43` | configuration × 1 | 17 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/evaluation/evaluation.py:91` | configuration × 1 | 14 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/evaluation/evaluation.py:95` | configuration × 2 | 14, 35 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/evaluation/model_facts.py:10` | profile × 2 | 21, 46 | 术语表中的模型画像 / model profile，属于模型而非单个 buddy。 |
| `src/hey_my_buddy/blackboard/evaluation/model_facts.py:319` | profile × 1 | 50 | 术语表中的模型画像 / model profile，属于模型而非单个 buddy。 |
| `src/hey_my_buddy/blackboard/evaluation/model_facts.py:320` | profile × 1 | 40 | 术语表中的模型画像 / model profile，属于模型而非单个 buddy。 |
| `src/hey_my_buddy/blackboard/evaluation/user_policy.py:8` | configuration × 1 | 28 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/evaluation/user_policy.py:208` | configuration × 1 | 9 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/evaluation/user_policy.py:209` | configuration × 3 | 9, 46, 68 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/evaluation/user_policy.py:211` | configuration × 1 | 26 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/evaluation/user_policy.py:212` | configuration × 1 | 32 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/evaluation/user_policy.py:213` | configuration × 1 | 32 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/evaluation/user_policy.py:228` | configuration × 1 | 44 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/evaluation/user_policy.py:229` | configuration × 1 | 31 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/evaluation/user_policy.py:231` | configuration × 1 | 63 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/routing/decision.py:478` | configuration × 1 | 29 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/routing/router.py:202` | configuration × 1 | 5 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/routing/router.py:213` | configuration × 1 | 16 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/routing/router.py:345` | configuration × 1 | 24 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/routing/router_settings.py:89` | configuration × 1 | 37 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/routing/router_settings.py:91` | configuration × 1 | 47 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/service/daemon.py:601` | profile × 1 | 27 | C-Two 端点的 RPC 资源/容量 profile，与 buddy 身份无关。 |
| `src/hey_my_buddy/blackboard/service/daemon.py:610` | profile × 1 | 37 | C-Two 端点的 RPC 资源/容量 profile，与 buddy 身份无关。 |
| `src/hey_my_buddy/blackboard/service/service.py:331` | Profile × 1, profile × 1 | 21, 53 | C-Two 端点的 RPC 资源/容量 profile，与 buddy 身份无关。 |
| `src/hey_my_buddy/blackboard/store/db.py:1105` | configuration × 1 | 61 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/store/db.py:1106` | configuration × 1 | 32 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:7` | profile × 1 | 24 | 仅从固定 schema 18 基点读取的表、列、meta 键；当前板只写 buddies、buddy_id 和 router_buddies，不接受旧名作为当前格式。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:12` | profile × 1 | 28 | 仅从固定 schema 18 基点读取的表、列、meta 键；当前板只写 buddies、buddy_id 和 router_buddies，不接受旧名作为当前格式。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:29` | profile × 1 | 18 | 仅从固定 schema 18 基点读取的表、列、meta 键；当前板只写 buddies、buddy_id 和 router_buddies，不接受旧名作为当前格式。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:32` | profile × 1 | 33 | 仅从固定 schema 18 基点读取的表、列、meta 键；当前板只写 buddies、buddy_id 和 router_buddies，不接受旧名作为当前格式。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:49` | profile × 2 | 17, 76 | 仅从固定 schema 18 基点读取的表、列、meta 键；当前板只写 buddies、buddy_id 和 router_buddies，不接受旧名作为当前格式。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:52` | profile × 1 | 57 | 仅从固定 schema 18 基点读取的表、列、meta 键；当前板只写 buddies、buddy_id 和 router_buddies，不接受旧名作为当前格式。 |
| `src/hey_my_buddy/buddy/harnesses/c_two_live.py:472` | profile × 1 | 29 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/claude/config.py:138` | configuration × 1 | 47 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/claude/config.py:182` | configuration × 1 | 63 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/claude/native_run.py:1080` | configuration × 1 | 22 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/config.py:45` | configuration × 1 | 23 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:6` | configuration × 1 | 1 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:15` | configuration × 1 | 22 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:511` | configuration × 1 | 49 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:590` | configuration × 1 | 15 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:698` | profile × 1 | 54 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:700` | profile × 1 | 15 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:701` | profile × 1 | 56 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:707` | configuration × 1 | 15 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:723` | profile × 1 | 5 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:725` | profile × 1 | 74 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:729` | Profile × 1, profile × 1 | 13, 53 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:731` | Profile × 1, profile × 1 | 38, 56 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:739` | profile × 1 | 54 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:740` | profile × 1 | 47 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:750` | configuration × 1 | 44 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:753` | configuration × 1 | 44 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:755` | profile × 3 | 5, 26, 66 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:757` | profile × 2 | 50, 71 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:762` | configuration × 1 | 44 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:778` | configuration × 1 | 13 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:1857` | configuration × 1 | 24 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/codex/tool_evidence.py:64` | configuration × 1 | 6 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/discovery.py:289` | PROFILE × 1 | 60 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/discovery.py:614` | Profile × 1 | 28 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/acp/client.py:11` | configuration × 1 | 1 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/acp/client.py:95` | configuration × 1 | 65 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/acp/client.py:148` | configuration × 1 | 28 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/acp/launch.py:1` | configuration × 1 | 43 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/acp/launch.py:5` | configuration × 1 | 58 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/acp/launch.py:121` | configuration × 1 | 23 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/acp/launch.py:209` | configuration × 1 | 21 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:1` | configuration × 1 | 43 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:3` | profile × 2 | 36, 73 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:6` | configuration × 1 | 60 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:7` | configuration × 1 | 38 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:166` | configuration × 1 | 62 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:173` | configuration × 1 | 26 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:179` | configuration × 1 | 37 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:191` | configuration × 1 | 66 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:254` | configuration × 1 | 43 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:519` | configuration × 1 | 19 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:614` | configuration × 1 | 35 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:618` | configuration × 1 | 61 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:621` | profile × 1 | 26 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:678` | configuration × 1 | 10 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:733` | configuration × 1 | 5 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:736` | configuration × 1 | 5 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:739` | configuration × 1 | 36 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:745` | configuration × 1 | 47 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:1022` | configuration × 1 | 15 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:1039` | configuration × 1 | 65 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:1366` | configuration × 1 | 28 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:1686` | configuration × 1 | 31 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:1796` | configuration × 1 | 49 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:1917` | configuration × 1 | 29 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:1926` | configuration × 1 | 47 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:1941` | configuration × 1 | 14 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/protocol.py:4` | profile × 1 | 43 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/protocol.py:93` | configuration × 1 | 40 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/protocol.py:97` | configuration × 1 | 53 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/protocol.py:102` | configuration × 1 | 38 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/protocol.py:110` | configuration × 1 | 58 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/protocol.py:116` | configuration × 1 | 41 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/protocol.py:119` | configuration × 1 | 61 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/session_receipts.py:193` | configuration × 1 | 33 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/session_receipts.py:214` | configuration × 1 | 103 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/session_receipts.py:216` | configuration × 2 | 62, 117 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/session_receipts.py:262` | configuration × 1 | 38 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/session_receipts.py:290` | configuration × 1 | 104 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/session_receipts.py:293` | configuration × 1 | 44 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/session_receipts.py:294` | configuration × 1 | 47 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/session_receipts.py:331` | configuration × 1 | 41 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/session_receipts.py:356` | configuration × 1 | 103 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/session_receipts.py:358` | configuration × 1 | 89 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/config.py:44` | configuration × 1 | 87 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/native_run.py:4` | configuration × 1 | 31 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/native_run.py:13` | configuration × 1 | 31 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/native_run.py:150` | configuration × 1 | 28 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/native_run.py:256` | configuration × 1 | 43 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/native_run.py:313` | configuration × 1 | 5 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/native_run.py:666` | configuration × 1 | 37 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/native_run.py:736` | configuration × 1 | 41 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/native_run.py:906` | configuration × 1 | 27 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/native_run.py:1551` | configuration × 1 | 11 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/native_run.py:1728` | configuration × 1 | 15 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py:221` | configuration × 1 | 33 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py:224` | configuration × 1 | 53 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py:229` | configuration × 1 | 38 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py:237` | configuration × 1 | 58 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py:243` | configuration × 1 | 41 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py:246` | configuration × 1 | 61 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py:262` | configuration × 1 | 32 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/buddy/roles/live.py:69` | configuration × 1 | 30 | 服务/状态的运行设置，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/session_mcp.py:32` | configuration × 1 | 28 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/session_mcp.py:44` | configuration × 1 | 97 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/session_mcp.py:56` | configuration × 1 | 5 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/session_mcp.py:62` | configuration × 1 | 41 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/structured_call.py:106` | configuration × 1 | 40 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:17` | configuration × 1 | 44 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:139` | configuration × 1 | 24 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:141` | configuration × 1 | 18 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:155` | configuration × 1 | 26 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:166` | configuration × 1 | 47 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:170` | configuration × 1 | 15 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:171` | configuration × 1 | 16 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:198` | configuration × 1 | 23 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:200` | configuration × 1 | 36 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:259` | configuration × 1 | 28 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:261` | configuration × 1 | 50 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:265` | configuration × 1 | 14 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:289` | configuration × 1 | 68 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:290` | configuration × 1 | 33 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:308` | configuration × 1 | 60 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:309` | configuration × 1 | 25 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:310` | configuration × 1 | 27 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:334` | configuration × 1 | 38 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:337` | configuration × 1 | 42 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:338` | configuration × 1 | 31 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:340` | configuration × 1 | 50 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:353` | configuration × 1 | 56 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:363` | configuration × 1 | 37 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:365` | configuration × 1 | 29 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:367` | configuration × 1 | 84 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:368` | configuration × 1 | 70 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:371` | configuration × 1 | 43 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:375` | configuration × 1 | 29 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:376` | configuration × 1 | 75 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:380` | configuration × 1 | 29 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:382` | configuration × 1 | 57 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:386` | configuration × 1 | 33 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:387` | configuration × 1 | 47 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:388` | configuration × 1 | 43 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:392` | configuration × 1 | 40 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:402` | configuration × 1 | 25 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:412` | configuration × 1 | 36 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:424` | configuration × 1 | 76 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:426` | configuration × 1 | 43 | 回合桥接/控制器设置，包含身份、签名、路径或挂载信息，不是 buddy 四元组。 |
| `src/hey_my_buddy/cli/checks.py:84` | configuration × 1 | 61 | 私有测试环境设置；USERPROFILE 是 Windows 标准变量。 |
| `src/hey_my_buddy/cli/checks.py:122` | PROFILE × 1 | 33 | 私有测试环境设置；USERPROFILE 是 Windows 标准变量。 |
| `src/hey_my_buddy/cli/cli_help.py:100` | configuration × 1 | 55 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/cli/cli_help.py:101` | configuration × 1 | 82 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/console/assets/assets/index-C157hAkK.js:1` | Profile × 2, profile × 2 | 1412, 6227, 19713, 20498 | 构建产物：Router 设置字段和其解析代码，以及上游 React Profiler；源码里的保留项已逐行列出，资产按构建命令生成。 |
| `src/hey_my_buddy/console/assets/assets/index-C157hAkK.js:8` | Profile × 1, profile × 2 | 11118, 11729, 11745 | 构建产物：Router 设置字段和其解析代码，以及上游 React Profiler；源码里的保留项已逐行列出，资产按构建命令生成。 |
| `src/hey_my_buddy/console/assets/assets/index-C157hAkK.js:51` | configuration × 58 | 84354, 84382, 84551, 99907, 99923, 102950, 102968, 102999, 103020, 103107, 103126, 103143, 103175, 103508, 103569, 103676, 103775, 103840, 104249, 104265, 104297, 104359, 104421, 104470, 105059, 105275, 106487, 106506, 106523, 106540, 106563, 106647, 106678, 106710, 106731, 106766, 106836, 107585, 109193, 109213, 109339, 109394, 109639, 109682, 111347, 111367, 111387, 111436, 111475, 111769, 111974, 112451, 112479, 136746, 136762, 139695, 139711, 142855 | 构建产物：Router 设置字段和其解析代码，以及上游 React Profiler；源码里的保留项已逐行列出，资产按构建命令生成。 |
| `src/hey_my_buddy/console/assets/assets/index-C157hAkK.js:54` | configuration × 4 | 130189, 130221, 131510, 131577 | 构建产物：Router 设置字段和其解析代码，以及上游 React Profiler；源码里的保留项已逐行列出，资产按构建命令生成。 |
| `src/hey_my_buddy/console/reads.py:8` | configuration × 1 | 60 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/install/launcher.py:29` | configuration × 1 | 56 | 用户原生 harness 设置；USERPROFILE 是 Windows 主目录变量。 |
| `src/hey_my_buddy/install/launcher.py:32` | PROFILE × 1 | 57 | 用户原生 harness 设置；USERPROFILE 是 Windows 主目录变量。 |
| `src/hey_my_buddy/install/launcher.py:47` | configuration × 1 | 30 | 用户原生 harness 设置；USERPROFILE 是 Windows 主目录变量。 |
| `src/hey_my_buddy/install/runtime.py:454` | configuration × 1 | 9 | Python 虚拟环境 pyvenv.cfg 文件。 |
| `src/hey_my_buddy/install/runtime.py:455` | configuration × 1 | 16 | Python 虚拟环境 pyvenv.cfg 文件。 |
| `src/hey_my_buddy/install/runtime.py:457` | configuration × 1 | 16 | Python 虚拟环境 pyvenv.cfg 文件。 |
| `src/hey_my_buddy/install/runtime.py:459` | configuration × 1 | 13 | Python 虚拟环境 pyvenv.cfg 文件。 |
| `src/hey_my_buddy/protocol/client.py:305` | configuration × 1 | 30 | Router 设置及其初始化、验证、读取或说明，包含 Router 列表、模式、预算和重试间隔。 |
| `src/hey_my_buddy/protocol/rpc_config.py:1` | profile × 1 | 41 | C-Two 端点的 RPC 资源/容量 profile，与 buddy 身份无关。 |
| `src/hey_my_buddy/protocol/rpc_config.py:6` | configuration × 1 | 1 | C-Two 端点的 RPC 资源/容量 profile，与 buddy 身份无关。 |
| `src/hey_my_buddy/protocol/rpc_config.py:20` | profile × 1 | 30 | C-Two 端点的 RPC 资源/容量 profile，与 buddy 身份无关。 |
| `src/hey_my_buddy/protocol/rpc_config.py:22` | PROFILE × 1 | 1 | C-Two 端点的 RPC 资源/容量 profile，与 buddy 身份无关。 |
| `src/hey_my_buddy/protocol/rpc_config.py:79` | PROFILE × 1, profile × 1 | 10, 20 | C-Two 端点的 RPC 资源/容量 profile，与 buddy 身份无关。 |
| `src/hey_my_buddy/protocol/rpc_config.py:92` | profile × 1 | 20 | C-Two 端点的 RPC 资源/容量 profile，与 buddy 身份无关。 |
| `src/hey_my_buddy/protocol/rpc_config.py:164` | profile × 1 | 66 | C-Two 端点的 RPC 资源/容量 profile，与 buddy 身份无关。 |
| `src/hey_my_buddy/protocol/rpc_config.py:177` | profile × 1 | 25 | C-Two 端点的 RPC 资源/容量 profile，与 buddy 身份无关。 |
| `src/hey_my_buddy/protocol/rpc_config.py:182` | profile × 1 | 25 | C-Two 端点的 RPC 资源/容量 profile，与 buddy 身份无关。 |
| `src/hey_my_buddy/protocol/transport.py:216` | profile × 1 | 47 | C-Two 端点的 RPC 资源/容量 profile，与 buddy 身份无关。 |
| `tests/probes/codex_permission_levels.py:16` | configuration × 1 | 15 | native harness configuration, permission mode, or native profile |
| `tests/probes/codex_permission_levels.py:17` | configuration × 2 | 1, 48 | native harness configuration, permission mode, or native profile |
| `tests/probes/dsh_native_resume_preflight.py:3` | profile × 1 | 35 | native harness configuration, permission mode, or native profile |
| `tests/probes/dsh_native_resume_preflight.py:10` | configuration × 1 | 57 | native harness configuration, permission mode, or native profile |
| `tests/probes/dsh_native_resume_preflight.py:19` | profile × 1 | 67 | native harness configuration, permission mode, or native profile |
| `tests/probes/dsh_native_resume_preflight.py:30` | configuration × 1 | 60 | native harness configuration, permission mode, or native profile |
| `tests/probes/dsh_native_resume_preflight.py:31` | profile × 1 | 17 | native harness configuration, permission mode, or native profile |
| `tests/probes/dsh_native_resume_preflight.py:341` | profile × 1 | 30 | native harness configuration, permission mode, or native profile |
| `tests/probes/dsh_native_resume_preflight.py:639` | configuration × 1 | 28 | native harness configuration, permission mode, or native profile |
| `tests/probes/macro_task_console_preview.py:1405` | configuration × 1 | 10 | Router settings and their snapshot field |
| `tests/probes/macro_task_console_preview.py:1904` | configuration × 2 | 5, 55 | Router settings and their snapshot field |
| `tests/probes/macro_task_console_preview.py:1906` | configuration × 2 | 43, 82 | Router settings and their snapshot field |
| `tests/probes/macro_task_console_preview.py:1907` | configuration × 1 | 71 | Router settings and their snapshot field |
| `tests/probes/macro_task_console_preview.py:2290` | configuration × 1 | 17 | Router settings and their snapshot field |
| `tests/probes/macro_task_console_preview.py:2291` | configuration × 1 | 13 | Router settings and their snapshot field |
| `tests/probes/macro_task_console_preview.py:2294` | configuration × 1 | 19 | Router settings and their snapshot field |
| `tests/probes/zcode_hooks_native.py:10` | configuration × 1 | 10 | native harness configuration, permission mode, or native profile |
| `tests/probes/zcode_hooks_native.py:42` | PROFILE × 1 | 48 | Windows home or PowerShell native parameter |
| `tests/probes/zcode_native.py:11` | configuration × 1 | 61 | native harness configuration, permission mode, or native profile |
| `tests/probes/zcode_native.py:96` | configuration × 1 | 61 | native harness configuration, permission mode, or native profile |
| `tests/probes/zcode_native.py:99` | profile × 1 | 9 | native harness configuration, permission mode, or native profile |
| `tests/probes/zcode_native.py:102` | profile × 1 | 56 | native harness configuration, permission mode, or native profile |
| `tests/probes/zcode_plan_mode.py:9` | configuration × 1 | 49 | native harness configuration, permission mode, or native profile |
| `tests/probes/zcode_plan_mode.py:10` | configuration × 1 | 57 | native harness configuration, permission mode, or native profile |
| `tests/probes/zcode_request_input.py:31` | configuration × 1 | 58 | native harness configuration, permission mode, or native profile |
| `tests/python/blackboard/evaluation/test_evaluation.py:105` | configuration × 1 | 36 | Router settings and their snapshot field |
| `tests/python/blackboard/evaluation/test_evaluation.py:106` | configuration × 1 | 36 | Router settings and their snapshot field |
| `tests/python/blackboard/evaluation/test_evaluation.py:114` | configuration × 1 | 36 | Router settings and their snapshot field |
| `tests/python/blackboard/evaluation/test_evaluation.py:115` | configuration × 1 | 36 | Router settings and their snapshot field |
| `tests/python/blackboard/evaluation/test_evaluation.py:171` | configuration × 1 | 22 | Router settings and their snapshot field |
| `tests/python/blackboard/evaluation/test_evaluation.py:177` | configuration × 1 | 36 | Router settings and their snapshot field |
| `tests/python/blackboard/evaluation/test_evaluation.py:180` | configuration × 1 | 14 | Router settings and their snapshot field |
| `tests/python/blackboard/evaluation/test_evaluation.py:186` | configuration × 1 | 22 | Router settings and their snapshot field |
| `tests/python/blackboard/evaluation/test_evaluation.py:187` | configuration × 1 | 48 | Router settings and their snapshot field |
| `tests/python/blackboard/evaluation/test_evaluation.py:188` | configuration × 1 | 48 | Router settings and their snapshot field |
| `tests/python/blackboard/evaluation/test_evaluation.py:190` | configuration × 1 | 30 | Router settings and their snapshot field |
| `tests/python/blackboard/evaluation/test_evaluation.py:192` | configuration × 1 | 30 | Router settings and their snapshot field |
| `tests/python/blackboard/evaluation/test_evaluation.py:193` | configuration × 1 | 48 | Router settings and their snapshot field |
| `tests/python/blackboard/evaluation/test_evaluation.py:345` | configuration × 2 | 22, 39 | Router settings and their snapshot field |
| `tests/python/blackboard/evaluation/test_user_policy.py:50` | configuration × 1 | 36 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_current_router.py:49` | configuration × 1 | 32 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_current_router.py:133` | configuration × 1 | 27 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_current_router.py:309` | configuration × 1 | 35 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_current_router.py:313` | configuration × 1 | 70 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_current_router.py:335` | configuration × 1 | 27 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_decision.py:89` | configuration × 1 | 18 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_nonretryable_quota.py:34` | configuration × 1 | 14 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router.py:168` | configuration × 1 | 14 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router.py:169` | configuration × 1 | 58 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_boundary_integration.py:441` | configuration × 1 | 50 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_boundary_integration.py:619` | configuration × 1 | 50 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_boundary_integration.py:646` | configuration × 1 | 50 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_dispatch.py:52` | configuration × 1 | 32 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:22` | Configuration × 1 | 17 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:48` | configuration × 1 | 23 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:52` | configuration × 1 | 12 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:53` | configuration × 2 | 22, 40 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:59` | configuration × 1 | 36 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:65` | configuration × 1 | 37 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:76` | configuration × 1 | 36 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:77` | configuration × 1 | 36 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:78` | configuration × 1 | 36 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:79` | configuration × 1 | 36 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:80` | configuration × 1 | 48 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:96` | configuration × 1 | 36 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:97` | configuration × 1 | 36 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:100` | configuration × 1 | 68 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:110` | configuration × 1 | 68 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:115` | configuration × 1 | 68 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:121` | configuration × 1 | 37 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:124` | configuration × 1 | 68 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:158` | configuration × 1 | 68 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:170` | configuration × 2 | 9, 67 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:171` | configuration × 1 | 26 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:172` | configuration × 1 | 26 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:174` | configuration × 1 | 14 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_configuration.py:179` | configuration × 1 | 24 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_health.py:45` | configuration × 1 | 32 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_health.py:397` | configuration × 1 | 31 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_list_health.py:401` | configuration × 1 | 37 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_router_tool_evidence.py:106` | configuration × 1 | 59 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_routing_history.py:236` | configuration × 1 | 13 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_routing_history.py:239` | configuration × 1 | 36 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_routing_modes.py:25` | configuration × 1 | 54 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_routing_modes.py:29` | configuration × 1 | 53 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_routing_modes.py:35` | configuration × 1 | 56 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_single_router_claims.py:76` | configuration × 1 | 90 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_single_router_claims.py:131` | configuration × 1 | 78 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_single_router_configuration.py:10` | Configuration × 1 | 19 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_single_router_configuration.py:15` | configuration × 1 | 66 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_single_router_configuration.py:19` | configuration × 1 | 60 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_single_router_configuration.py:25` | configuration × 1 | 58 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_single_router_configuration.py:29` | configuration × 1 | 30 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_single_router_configuration.py:35` | configuration × 1 | 37 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_single_router_requests.py:36` | configuration × 1 | 40 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_single_router_requests.py:329` | configuration × 1 | 33 | Router settings and their snapshot field |
| `tests/python/blackboard/routing/test_single_router_requests.py:353` | configuration × 1 | 96 | Router settings and their snapshot field |
| `tests/python/blackboard/store/test_upgrade_fixture.py:66` | profile × 1 | 96 | 0.34.1 schema-18 stock SQL |
| `tests/python/blackboard/store/test_upgrade_fixture.py:78` | profile × 1 | 35 | 0.34.1 schema-18 stock SQL |
| `tests/python/blackboard/store/test_upgrade_fixture.py:115` | profile × 1 | 40 | 0.34.1 schema-18 stock SQL |
| `tests/python/blackboard/store/test_upgrade_fixture.py:117` | profile × 2 | 61, 96 | 0.34.1 schema-18 stock SQL |
| `tests/python/blackboard/store/test_upgrade_fixture.py:159` | configuration × 1 | 37 | Router settings API |
| `tests/python/blackboard/store/test_upgrade_fixture.py:161` | configuration × 2 | 37, 69 | Router settings API |
| `tests/python/blackboard/store/test_upgrade_fixture.py:183` | profile × 2 | 60, 78 | 0.34.1 schema-18 stock SQL |
| `tests/python/blackboard/store/test_upgrade_fixture.py:188` | profile × 2 | 41, 50 | 0.34.1 schema-18 stock SQL |
| `tests/python/blackboard/store/test_upgrade_fixture.py:193` | profile × 3 | 21, 48, 109 | 0.34.1 schema-18 stock SQL |
| `tests/python/blackboard/store/test_upgrade_fixture.py:199` | profile × 1 | 57 | 0.34.1 schema-18 stock SQL |
| `tests/python/blackboard/store/test_upgrade_fixture.py:201` | profile × 1 | 57 | 0.34.1 schema-18 stock SQL |
| `tests/python/blackboard/store/test_upgrade_fixture.py:203` | profile × 2 | 40, 81 | 0.34.1 schema-18 stock SQL |
| `tests/python/blackboard/store/test_upgrade_fixture.py:205` | profile × 1 | 26 | 0.34.1 schema-18 stock SQL |
| `tests/python/blackboard/store/test_upgrade_fixture.py:206` | profile × 1 | 21 | 0.34.1 schema-18 stock SQL |
| `tests/python/blackboard/store/test_upgrade_fixture.py:207` | profile × 3 | 58, 85, 103 | 0.34.1 schema-18 stock SQL |
| `tests/python/blackboard/tasks/test_model_service_concurrency.py:45` | configuration × 1 | 18 | Router settings and their snapshot field |
| `tests/python/blackboard/tasks/test_parallel_dispatch.py:136` | configuration × 1 | 14 | Router settings and their snapshot field |
| `tests/python/blackboard/tasks/test_parallel_dispatch.py:983` | configuration × 1 | 18 | Router settings and their snapshot field |
| `tests/python/blackboard/tasks/test_workflow_routing.py:98` | configuration × 1 | 14 | Router settings and their snapshot field |
| `tests/python/buddy/harnesses/claude/test_claude_config.py:1` | configuration × 1 | 11 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/claude/test_claude_config.py:125` | configuration × 1 | 55 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/fixtures/mock_codex.py:221` | Profile × 1 | 41 | Codex native permission profile |
| `tests/python/buddy/harnesses/codex/fixtures/protocol_items.py:19` | configuration × 1 | 7 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/test_batch4_facts.py:37` | Profile × 1 | 69 | Codex native permission profile |
| `tests/python/buddy/harnesses/codex/test_batch4_facts.py:123` | configuration × 1 | 49 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/test_batch4_facts.py:151` | Profile × 2 | 42, 75 | Codex native permission profile |
| `tests/python/buddy/harnesses/codex/test_batch4_facts.py:160` | Profile × 1 | 42 | Codex native permission profile |
| `tests/python/buddy/harnesses/codex/test_batch4_facts.py:198` | configuration × 1 | 49 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/test_batch4_facts.py:232` | configuration × 1 | 89 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/test_batch4_facts.py:233` | configuration × 1 | 67 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/test_batch4_facts.py:245` | Profile × 1 | 53 | Codex native permission profile |
| `tests/python/buddy/harnesses/codex/test_batch4_facts.py:264` | Profile × 1 | 64 | Codex native permission profile |
| `tests/python/buddy/harnesses/codex/test_codex.py:193` | configuration × 1 | 61 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/test_codex.py:448` | configuration × 1 | 59 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/test_native_run.py:8` | configuration × 1 | 8 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/test_native_run.py:579` | configuration × 1 | 79 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/test_native_run.py:653` | profile × 1 | 9 | Codex native permission profile |
| `tests/python/buddy/harnesses/codex/test_native_run.py:655` | profile × 1 | 26 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/test_native_run.py:656` | profile × 1 | 26 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/test_native_run.py:715` | configuration × 1 | 32 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/test_native_run.py:783` | configuration × 1 | 60 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/test_no_tool_codex.py:1` | configuration × 1 | 45 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/test_no_tool_codex.py:67` | configuration × 1 | 39 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/codex/test_no_tool_codex.py:147` | configuration × 1 | 29 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:15` | configuration × 1 | 62 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:57` | profile × 2 | 35, 51 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:231` | configuration × 1 | 67 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:571` | configuration × 1 | 37 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:600` | configuration × 1 | 9 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:630` | configuration × 1 | 58 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:634` | configuration × 1 | 87 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:639` | configuration × 1 | 77 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:661` | configuration × 1 | 25 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:676` | configuration × 1 | 103 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:703` | configuration × 1 | 88 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:997` | profile × 1 | 48 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:998` | profile × 1 | 28 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:1098` | profile × 2 | 45, 60 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/dsh/acp/test_acp_launch.py:1` | configuration × 1 | 39 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/acp/test_acp_session.py:4` | configuration × 1 | 8 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/acp/test_acp_session.py:61` | Configuration × 1 | 7 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/fixtures/workflow_agent.py:30` | profile × 1 | 6 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/fixtures/workflow_agent.py:223` | profile × 2 | 56, 71 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_dsh_session_storage.py:3` | configuration × 1 | 8 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_dsh_session_storage.py:4` | configuration × 1 | 54 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_native_resume.py:217` | configuration × 1 | 38 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_native_run.py:8` | configuration × 1 | 32 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_native_run.py:206` | configuration × 1 | 66 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_native_run.py:1448` | Configuration × 1 | 13 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_native_run.py:1451` | configuration × 1 | 50 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_native_run.py:1454` | profile × 2 | 9, 29 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_native_run.py:1455` | profile × 1 | 9 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_native_run.py:1458` | profile × 2 | 22, 53 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_native_run.py:1459` | profile × 2 | 22, 65 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_native_run.py:1493` | profile × 2 | 48, 65 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_native_run.py:1504` | profile × 1 | 53 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_no_tool_dsh.py:32` | configuration × 1 | 34 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_refusal_facts.py:4` | configuration × 1 | 9 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_refusal_facts.py:124` | configuration × 1 | 11 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_refusal_facts.py:188` | configuration × 1 | 21 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_refusal_facts.py:215` | configuration × 1 | 51 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_refusal_facts.py:269` | configuration × 1 | 30 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/dsh/test_refusal_facts.py:325` | configuration × 1 | 21 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/test_c_two_live.py:991` | profile × 1 | 19 | C-Two RPC resource profile or configuration |
| `tests/python/buddy/harnesses/test_harness_discovery.py:463` | profile × 1 | 44 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/test_harness_discovery.py:466` | Profile × 1 | 55 | Windows home or PowerShell native parameter |
| `tests/python/buddy/harnesses/test_pending_ready_receipt_guards.py:192` | configuration × 1 | 9 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/test_pending_ready_receipt_guards.py:195` | configuration × 1 | 33 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/test_session_receipts.py:150` | configuration × 1 | 77 | session tool bridge or receipt configuration |
| `tests/python/buddy/harnesses/zcode/fixtures/checkpoint_domain.py:114` | PROFILE × 1 | 44 | Windows home or PowerShell native parameter |
| `tests/python/buddy/harnesses/zcode/test_native_run.py:671` | configuration × 1 | 51 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/zcode/test_native_run.py:837` | configuration × 1 | 55 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:41` | PROFILE × 1 | 60 | Windows home or PowerShell native parameter |
| `tests/python/buddy/harnesses/zcode/test_zcode_hooks_probe.py:93` | PROFILE × 1 | 54 | Windows home or PowerShell native parameter |
| `tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py:957` | configuration × 1 | 29 | native harness configuration, permission mode, or native profile |
| `tests/python/buddy/harnesses/zcode/test_zcode_request_input_probe.py:378` | PROFILE × 1 | 58 | Windows home or PowerShell native parameter |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_evidence.py:520` | PROFILE × 1 | 60 | Windows home or PowerShell native parameter |
| `tests/python/buddy/roles/test_outcome_truncation.py:191` | configuration × 1 | 9 | session tool bridge or receipt configuration |
| `tests/python/buddy/roles/test_outcome_truncation.py:197` | configuration × 1 | 93 | session tool bridge or receipt configuration |
| `tests/python/buddy/roles/test_outcome_truncation.py:202` | configuration × 1 | 98 | session tool bridge or receipt configuration |
| `tests/python/buddy/roles/test_outcome_truncation.py:203` | configuration × 1 | 78 | session tool bridge or receipt configuration |
| `tests/python/buddy/roles/test_worker_services.py:40` | configuration × 1 | 14 | session tool bridge or receipt configuration |
| `tests/python/buddy/roles/test_worker_services.py:52` | configuration × 1 | 74 | session tool bridge or receipt configuration |
| `tests/python/buddy/roles/test_worker_services.py:62` | configuration × 1 | 65 | session tool bridge or receipt configuration |
| `tests/python/buddy/roles/test_worker_services.py:69` | configuration × 1 | 64 | session tool bridge or receipt configuration |
| `tests/python/buddy/roles/test_worker_services.py:260` | configuration × 2 | 9, 60 | session tool bridge or receipt configuration |
| `tests/python/buddy/roles/test_worker_services.py:261` | configuration × 1 | 61 | session tool bridge or receipt configuration |
| `tests/python/buddy/roles/test_worker_services.py:264` | configuration × 1 | 77 | session tool bridge or receipt configuration |
| `tests/python/buddy/roles/test_worker_services.py:269` | configuration × 1 | 66 | session tool bridge or receipt configuration |
| `tests/python/buddy/roles/test_worker_services.py:272` | configuration × 1 | 66 | session tool bridge or receipt configuration |
| `tests/python/buddy/roles/test_worker_services.py:274` | configuration × 1 | 66 | session tool bridge or receipt configuration |
| `tests/python/buddy/roles/test_worker_services.py:277` | configuration × 1 | 69 | session tool bridge or receipt configuration |
| `tests/python/buddy/runtime/test_live.py:169` | profile × 1 | 81 | C-Two RPC resource profile or configuration |
| `tests/python/buddy/runtime/test_live.py:171` | profile × 1 | 81 | C-Two RPC resource profile or configuration |
| `tests/python/buddy/runtime/test_live.py:218` | profile × 1 | 20 | C-Two RPC resource profile or configuration |
| `tests/python/buddy/runtime/test_live.py:225` | profile × 2 | 35, 53 | C-Two RPC resource profile or configuration |
| `tests/python/buddy/runtime/test_live.py:238` | profile × 2 | 54, 87 | C-Two RPC resource profile or configuration |
| `tests/python/buddy/runtime/test_live.py:519` | profile × 2 | 35, 53 | C-Two RPC resource profile or configuration |
| `tests/python/cli/test_checks_cleanup.py:44` | configuration × 1 | 18 | private harness home or process configuration |
| `tests/python/cli/test_checks_uv_cache.py:40` | PROFILE × 1 | 26 | Windows 标准主目录变量；必须保留 USERPROFILE，用私有测试主目录赋值。 |
| `tests/python/cli/test_cli.py:147` | configuration × 1 | 21 | Router settings and their snapshot field |
| `tests/python/cli/test_cli.py:149` | configuration × 1 | 11 | Router settings and their snapshot field |
| `tests/python/cli/test_harness_test_safety.py:31` | PROFILE × 1 | 30 | Windows 标准主目录变量；必须保留 USERPROFILE，用私有测试主目录赋值。 |
| `tests/python/cli/test_host_preview.py:156` | configuration × 2 | 9, 33 | Router settings and their snapshot field |
| `tests/python/cli/test_host_preview.py:157` | configuration × 1 | 45 | Router settings and their snapshot field |
| `tests/python/cli/test_host_preview.py:159` | configuration × 1 | 32 | Router settings and their snapshot field |
| `tests/python/cli/test_support_cleanup.py:150` | PROFILE × 1 | 79 | Windows 标准主目录变量；必须保留 USERPROFILE，用私有测试主目录赋值。 |
| `tests/python/cli/test_support_cleanup.py:185` | PROFILE × 1 | 49 | Windows 标准主目录变量；必须保留 USERPROFILE，用私有测试主目录赋值。 |
| `tests/python/console/test_console.py:159` | configuration × 2 | 129, 146 | Router settings and their snapshot field |
| `tests/python/console/test_console.py:160` | configuration × 1 | 37 | Router settings and their snapshot field |
| `tests/python/console/test_console.py:215` | configuration × 2 | 62, 89 | Router settings and their snapshot field |
| `tests/python/console/test_console.py:799` | configuration × 1 | 53 | Router settings and their snapshot field |
| `tests/python/console/test_console_reads_http.py:349` | configuration × 1 | 52 | Router settings and their snapshot field |
| `tests/python/console/test_console_reads_http.py:364` | configuration × 1 | 44 | Router settings and their snapshot field |
| `tests/python/fixtures/console_macro_task_board.py:280` | configuration × 1 | 14 | Router settings and their snapshot field |
| `tests/python/fixtures/console_macro_task_board.py:580` | profile × 1 | 360 | Router settings and their snapshot field |
| `tests/python/install/test_backup.py:118` | profile × 1 | 46 | native DSH profile directory |
| `tests/python/install/test_upgrade_host_cutover.py:75` | configuration × 1 | 9 | Python virtual environment configuration |
| `tests/python/install/test_upgrade_host_cutover.py:76` | configuration × 2 | 9, 61 | Python virtual environment configuration |
| `tests/python/install/test_upgrade_migration.py:24` | profile × 1 | 28 | 0.34.1 schema-18 stock names |
| `tests/python/install/test_upgrade_migration.py:29` | profile × 1 | 18 | 0.34.1 schema-18 stock names |
| `tests/python/install/test_upgrade_migration.py:30` | profile × 1 | 33 | 0.34.1 schema-18 stock names |
| `tests/python/install/test_upgrade_migration.py:59` | profile × 1 | 66 | 0.34.1 schema-18 stock names |
| `tests/python/install/test_upgrade_migration.py:80` | configuration × 1 | 37 | Router settings API |
| `tests/python/install/test_upgrade_service.py:30` | Profile × 1 | 62 | 0.34.1 fixture metadata and Router settings |
| `tests/python/install/test_upgrade_service.py:41` | Profile × 1, configuration × 1 | 40, 90 | 0.34.1 fixture metadata and Router settings |
| `tests/python/protocol/test_path_state.py:190` | configuration × 1 | 51 | C-Two RPC resource profile or configuration |
| `tests/python/protocol/test_path_state.py:202` | configuration × 1 | 58 | C-Two RPC resource profile or configuration |
| `tests/python/protocol/test_rpc_config.py:86` | profile × 1 | 45 | C-Two RPC resource profile or configuration |
| `tests/python/protocol/test_rpc_config.py:90` | profile × 1 | 15 | C-Two RPC resource profile or configuration |
| `tests/python/protocol/test_rpc_config.py:124` | profile × 1 | 57 | C-Two RPC resource profile or configuration |
| `tests/python/protocol/test_rpc_config.py:126` | profile × 1 | 52 | C-Two RPC resource profile or configuration |
| `tests/python/protocol/test_rpc_config.py:167` | profile × 1 | 74 | C-Two RPC resource profile or configuration |
| `tests/python/protocol/test_rpc_config.py:212` | Profile × 1 | 7 | C-Two RPC resource profile or configuration |
| `tests/python/protocol/test_rpc_config.py:222` | profile × 1 | 14 | C-Two RPC resource profile or configuration |
| `tests/python/protocol/test_rpc_config.py:385` | configuration × 1 | 49 | C-Two RPC resource profile or configuration |
| `tests/python/protocol/test_rpc_config.py:647` | Profile × 1 | 98 | C-Two RPC resource profile or configuration |
| `tests/python/protocol/test_rpc_config.py:814` | profile × 1 | 96 | C-Two RPC resource profile or configuration |
| `tests/python/protocol/test_state_boundaries.py:97` | configuration × 1 | 14 | private harness home or process configuration |
| `tests/python/protocol/test_state_boundaries.py:115` | configuration × 1 | 35 | private harness home or process configuration |
| `tests/python/protocol/test_state_boundaries.py:147` | PROFILE × 1 | 48 | Windows home or PowerShell native parameter |
| `tests/python/protocol/test_state_boundaries.py:166` | PROFILE × 1 | 43 | Windows home or PowerShell native parameter |
| `tests/python/protocol/test_transport_attach.py:469` | profile × 1 | 55 | C-Two RPC resource profile or configuration |
| `tests/python/support.py:206` | configuration × 1 | 47 | private harness home or process configuration |
| `tests/python/support.py:211` | PROFILE × 1 | 13 | Windows 标准主目录变量；必须保留 USERPROFILE，用私有测试主目录赋值。 |
| `tests/python/support.py:242` | PROFILE × 1 | 30 | Windows 标准主目录变量；必须保留 USERPROFILE，用私有测试主目录赋值。 |
