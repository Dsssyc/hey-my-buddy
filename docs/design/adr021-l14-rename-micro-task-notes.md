# 微任务一族改名说明

本候选以 `0ec3952c` 为基点，只改微任务一族名称；版本 `0.34.1`、schema `19`、任务状态、`runId` 等标识字段及事件种类保持原值。微任务视图去掉 `governed` 布尔字段，使用 `task.lineage.kind` 的 `micro-task` / `helper` 判断；提交事件的原对象改为 `microTask`。升级仍归档旧历史，只带设置，没有新旧名称双读或兼容别名。

统计口径：下表按源码、测试、前端和 packaging 中完整标识符或字符串/注释里的完整同名词形计数；同一个旧名有多种去处时合并处数。排除文档、升级基点样板、其生成器和构建资产中的重复。共有 190 个旧词形、1735 处替换；文件路径改名另列。

| 旧名 | 新名 | 处数 |
| --- | --- | ---: |
| `CompactGovernedEnvelopeTests` | CompactMicroTaskEnvelopeTests | 1 |
| `DELEGATION_FIELDS` | MICRO_TASK_FIELDS | 2 |
| `DELEGATION_KINDS` | MICRO_TASK_KINDS | 1 |
| `Delegation` | Lineage（对象类型）、Micro task（文字） | 5 |
| `DelegationCard` | MicroTaskCard | 2 |
| `DelegationDetailRows` | MicroTaskDetailRows | 3 |
| `DelegationStrip` | MicroTaskStrip | 11 |
| `DelegationTestCase` | MicroTaskTestCase | 5 |
| `GOVERNED_RECEIPTS` | MICRO_TASK_RECEIPTS | 2 |
| `GOVERNED_REQUIRED` | MICRO_TASK_REQUIRED | 6 |
| `Goal` | Micro task（文字） | 25 |
| `GoalProvenanceTests` | MicroTaskProvenanceTests | 1 |
| `GoalStopWaitTests` | MicroTaskStopWaitTests | 1 |
| `Governed` | Micro-task / MicroTask（文字及内部） | 13 |
| `GovernedBoundaryTests` | MicroTaskBoundaryTests | 2 |
| `GovernedCliTests` | MicroTaskCliTests | 1 |
| `GovernedParameterDocumentTests` | MicroTaskParameterDocumentTests | 1 |
| `GovernedPromptTests` | MicroTaskPromptTests | 1 |
| `GovernedServiceStopTests` | MicroTaskServiceStopTests | 1 |
| `GovernedWorkerTestCase` | MicroTaskWorkerTestCase | 9 |
| `SingleDelegationCard` | SingleMicroTaskCard | 2 |
| `UNTITLED_DELEGATION` | UNTITLED_MICRO_TASK | 3 |
| `UngovernedTests` | ExecutionTests | 1 |
| `_KNOWN_GOVERNED_FIELDS` | _KNOWN_MICRO_TASK_FIELDS | 2 |
| `_TASK_GOAL_DIGEST` | _TASK_MICRO_TASK_DIGEST | 2 |
| `_buddy_matches_goal` | _buddy_matches_micro_task | 3 |
| `_goal_binding` | _micro_task_binding | 3 |
| `_goal_shared_native` | _micro_task_shared_native | 2 |
| `_goal_view` | _micro_task_view | 2 |
| `_governed_get_command` | _micro_task_get_command | 2 |
| `_governed_record` | _micro_task_record | 3 |
| `_governed_result_view` | _micro_task_result_view | 5 |
| `_governed_round` | _micro_task_round | 2 |
| `_governed_turn` | _micro_task_turn | 2 |
| `attach_governed_task` | attach_micro_task | 3 |
| `buddy-goal` | buddy-micro-task | 16 |
| `cancel_goal` | cancel_micro_task | 3 |
| `cancelled_goal` | cancelled_micro_task | 2 |
| `claim_governed` | claim_micro_task | 35 |
| `claim_governed_claim` | claim_micro_task_claim | 3 |
| `delegation` | lineage（关系对象）、micro_task / microTask / MicroTask（内部）、micro task（文字） | 195 |
| `delegation-band` | micro-task-band | 12 |
| `delegation-band-head` | micro-task-band-head | 6 |
| `delegation-band-toggle` | micro-task-band-toggle | 5 |
| `delegation-card` | micro-task-card | 23 |
| `delegation-card-meta` | micro-task-card-meta | 3 |
| `delegation-card-result` | micro-task-card-result | 3 |
| `delegation-card-result-text` | micro-task-card-result-text | 3 |
| `delegation-card-state` | micro-task-card-state | 5 |
| `delegation-card-title` | micro-task-card-title | 4 |
| `delegation-card-top` | micro-task-card-top | 3 |
| `delegation-current` | micro-task-current | 4 |
| `delegation-hidden-current` | micro-task-hidden-current | 2 |
| `delegation-list-collapsed` | micro-task-list-collapsed | 2 |
| `delegation-show-all` | micro-task-show-all | 3 |
| `delegation-strip` | micro-task-strip | 15 |
| `delegation-strip-region` | micro-task-strip-region | 1 |
| `delegation-tree` | micro-task-tree | 1 |
| `delegationField` | microTaskField | 8 |
| `delegationTitle` | microTaskTitle | 3 |
| `delegation_of` | micro_task_of | 24 |
| `delegation_result` | lineage_result | 4 |
| `delegations` | micro_tasks / microTasks（内部）、micro tasks（文字） | 12 |
| `foreign-goal-session` | foreign-micro-task-session | 3 |
| `foreign_goal` | foreign_micro_task | 3 |
| `gated-governed` | gated-micro-task | 2 |
| `goal` | microTask（对象）、micro-task（种类）、micro_task（内部）、micro task（文字） | 320 |
| `goal-1` | micro-task-1 | 43 |
| `goal-2` | micro-task-2 | 1 |
| `goal-binding` | micro task-binding | 1 |
| `goal-digest` | micro-task-digest | 1 |
| `goal-owned` | micro task-owned、micro-task-owned | 4 |
| `goal-shaped` | micro-task-shaped | 1 |
| `goal-state` | micro-task-state | 1 |
| `goalComplete` | microTaskComplete | 3 |
| `goalFingerprint` | microTaskFingerprint | 4 |
| `goalInputCommit` | microTaskInputCommit | 2 |
| `goalState` | microTaskState | 1 |
| `goal_dir` | micro_task_dir | 9 |
| `goal_fingerprint` | micro_task_fingerprint | 9 |
| `goal_id` | micro_task_id | 26 |
| `goal_json` | micro_task_json | 46 |
| `goal_owned_storage` | micro_task_owned_storage | 6 |
| `goal_root` | micro_task_root | 6 |
| `goal_row` | micro_task_row | 5 |
| `goal_state` | micro_task_state | 3 |
| `goals` | micro-tasks（私有路径）、micro_tasks（内部）、micro tasks（文字） | 25 |
| `governed` | 删除视图布尔字段并读 task.lineage.kind；microTask（事件对象）；micro_task / MicroTask / micro-task（内部） | 417 |
| `governed-1` | micro-task-1 | 3 |
| `governed-native` | micro-task-native | 1 |
| `governed-permission` | micro-task-permission | 3 |
| `governed-permission-answer` | micro-task-permission-answer | 1 |
| `governed-role` | micro-task-role | 1 |
| `governed-run` | micro task run、micro-task-run | 2 |
| `governed-task` | micro-task-awaiting | 2 |
| `governed-turn` | micro task-turn、micro-task-turn | 4 |
| `governedError` | microTaskError | 4 |
| `governedQuotaFailure` | microTaskQuotaFailure | 1 |
| `governedRuns` | microTaskRuns | 2 |
| `governedSuccess` | microTaskSuccess | 1 |
| `governed_agent_args` | micro_task_agent_args | 27 |
| `governed_brief` | micro_task_brief | 10 |
| `governed_fold` | micro_task_fold | 2 |
| `governed_permission` | micro_task_permission | 3 |
| `governed_prompt` | micro_task_prompt | 12 |
| `governed_receipt` | micro_task_receipt | 4 |
| `governed_record` | micro_task_record | 12 |
| `governed_run_id` | micro_task_run_id | 12 |
| `governed_submit` | micro_task_submit | 10 |
| `governed_task_ids` | micro_task_task_ids | 3 |
| `governed_view` | micro_task_view | 26 |
| `governed_yield` | micro_task_yield | 2 |
| `long-goal-coding` | long-micro-task-coding | 1 |
| `non-governed` | (deleted)、raw task | 2 |
| `per-delegation` | per-micro-task | 2 |
| `private_goal` | private_micro_task | 3 |
| `private_goals` | private_micro_tasks | 5 |
| `protected-goals` | protected-micro-tasks | 1 |
| `reclaim-own-goal` | reclaim-own-micro-task | 1 |
| `route_goal` | route_micro_task | 4 |
| `run-governed` | run-micro-task | 1 |
| `run_governed_prompt` | run_micro_task_prompt | 4 |
| `standalone-delegation` | standalone-micro-task | 1 |
| `submit_governed` | submit_micro_task | 13 |
| `task-goal` | task-micro-task | 7 |
| `test_a_binding_for_another_goal_or_buddy_is_refused` | test_a_binding_for_another_micro_task_or_buddy_is_refused | 1 |
| `test_a_completed_goal_root_turn_reports_goal_owned_storage` | test_a_completed_micro_task_root_turn_reports_micro_task_owned_storage | 1 |
| `test_a_governed_mutation_from_a_file_uses_the_same_private_control_file` | test_a_micro_task_mutation_from_a_file_uses_the_same_private_control_file | 1 |
| `test_a_governed_turn_cannot_be_hidden_by_a_both_null_identity` | test_a_micro_task_turn_cannot_be_hidden_by_a_both_null_identity | 1 |
| `test_a_governed_turn_carries_its_unknown_notifications_in_the_result` | test_a_micro_task_turn_carries_its_unknown_notifications_in_the_result | 1 |
| `test_a_governed_turn_settles_with_a_verified_completion_tool_value` | test_a_micro_task_turn_settles_with_a_verified_completion_tool_value | 2 |
| `test_a_real_ungoverned_claim_keeps_the_both_null_rule` | test_a_real_execution_claim_keeps_the_both_null_rule | 1 |
| `test_all_adapters_private_goals_are_visible` | test_all_adapters_private_micro_tasks_are_visible | 1 |
| `test_an_answer_with_no_session_service_takes_no_governed_path` | test_an_answer_with_no_session_service_takes_no_micro_task_path | 1 |
| `test_an_answered_host_question_flows_through_the_governed_turn` | test_an_answered_host_question_flows_through_the_micro_task_turn | 1 |
| `test_an_incomplete_governed_turn_saves_no_binding` | test_an_incomplete_micro_task_turn_saves_no_binding | 1 |
| `test_an_ungoverned_context_cannot_publish_worker_usage` | test_an_unmicro_task_context_cannot_publish_worker_usage | 1 |
| `test_awaiting_and_grace_goals_are_kept_and_reclaim_takes_only_the_qualified_goal` | test_awaiting_and_grace_micro_tasks_are_kept_and_reclaim_takes_only_the_qualified_micro_task | 1 |
| `test_cancel_routes_owned_attempt_and_late_selection_cannot_start_goal` | test_cancel_routes_owned_attempt_and_late_selection_cannot_start_micro_task | 1 |
| `test_cancelled_goal_can_cleanup_only_after_a_recorded_conclusion` | test_cancelled_micro_task_can_cleanup_only_after_a_recorded_conclusion | 1 |
| `test_cancelled_goal_never_becomes_success_from_a_late_completed_execution` | test_cancelled_micro_task_never_becomes_success_from_a_late_completed_execution | 1 |
| `test_candidate_native_resume_prepares_one_goal_native_root_for_both_attempts` | test_candidate_native_resume_prepares_one_micro_task_native_root_for_both_attempts | 1 |
| `test_codex_user_sessions_are_not_inventory_or_goal_removal_targets` | test_codex_user_sessions_are_not_inventory_or_micro_task_removal_targets | 1 |
| `test_concluded_delivered_goal_reports_the_conclusion_instead_of_acknowledgement` | test_concluded_delivered_micro_task_reports_the_conclusion_instead_of_acknowledgement | 1 |
| `test_control_methods_are_the_current_governed_mutations` | test_control_methods_are_the_current_micro_task_mutations | 1 |
| `test_delivered_goal_without_a_conclusion_still_awaits_final_acknowledgement` | test_delivered_micro_task_without_a_conclusion_still_awaits_final_acknowledgement | 1 |
| `test_every_task_view_carries_the_delegation_contract` | test_every_task_view_carries_the_micro_task_contract | 1 |
| `test_execution_results_unwrap_the_task_view_and_goal_results_do_not` | test_execution_results_unwrap_the_task_view_and_micro_task_results_do_not | 1 |
| `test_execution_task_without_a_source_goal_reports_unknown_hosts` | test_execution_task_without_a_source_micro_task_reports_unknown_hosts | 1 |
| `test_explicit_long_goal_does_not_require_selector` | test_explicit_long_micro_task_does_not_require_selector | 1 |
| `test_explicit_reroute_after_known_retry_time_continues_same_goal_without_model` | test_explicit_reroute_after_known_retry_time_continues_same_micro_task_without_model | 1 |
| `test_failed_goal_conclusion_reclaims_checkout_but_retains_partial_artifacts` | test_failed_micro_task_conclusion_reclaims_checkout_but_retains_partial_artifacts | 1 |
| `test_final_noop_continuation_still_requires_earlier_goal_change` | test_final_noop_continuation_still_requires_earlier_micro_task_change | 1 |
| `test_goal_at_exact_routing_byte_limit_is_passed_whole` | test_micro_task_at_exact_routing_byte_limit_is_passed_whole | 1 |
| `test_goal_native_state_stays_out_of_the_verified_backup` | test_micro_task_native_state_stays_out_of_the_verified_backup | 1 |
| `test_goal_reports_source_and_current_host_with_the_source_project` | test_micro_task_reports_source_and_current_host_with_the_source_project | 1 |
| `test_goal_wait_timeout_expires_only_the_wait_and_names_current_commands` | test_micro_task_wait_timeout_expires_only_the_wait_and_names_current_commands | 1 |
| `test_governed_boundary_has_post_attention_revision_and_real_continue_tuple` | test_micro_task_boundary_has_post_attention_revision_and_real_continue_tuple | 1 |
| `test_governed_final_envelope_is_compact_and_points_at_the_full_result` | test_micro_task_final_envelope_is_compact_and_points_at_the_full_result | 1 |
| `test_governed_get_and_execution_status_stay_distinct` | test_micro_task_get_and_execution_status_stay_distinct | 1 |
| `test_governed_goal_can_explicitly_omit_an_execution_deadline` | test_micro_task_can_explicitly_omit_an_execution_deadline | 1 |
| `test_governed_mock_codex_zcode_claude_and_native_continuation` | test_micro_task_mock_codex_zcode_claude_and_native_continuation | 1 |
| `test_governed_mutation_without_the_control_file_fails_closed` | test_micro_task_mutation_without_the_control_file_fails_closed | 1 |
| `test_governed_task_ops_require_host_control` | test_micro_task_task_ops_require_host_control | 1 |
| `test_governed_turn_leaves_unknown_login_to_the_native_cli` | test_micro_task_turn_leaves_unknown_login_to_the_native_cli | 1 |
| `test_governed_valid_abstention_payload_has_program_nature_before_answered_notification` | test_micro_task_valid_abstention_payload_has_program_nature_before_answered_notification | 1 |
| `test_governed_worker_seals_only_a_proven_claude_result` | test_micro_task_worker_seals_only_a_proven_claude_result | 1 |
| `test_helper_takes_lineage_project_and_hosts_from_its_root_goal` | test_helper_takes_lineage_project_and_hosts_from_its_root_micro_task | 1 |
| `test_history_on_an_ungoverned_task_is_honestly_empty` | test_history_on_an_execution_task_is_honestly_empty | 1 |
| `test_internal_routing_decision_belongs_to_its_owning_goal` | test_internal_routing_decision_belongs_to_its_owning_micro_task | 1 |
| `test_late_completed_artifact_is_retained_without_reviving_cancelled_goal` | test_late_completed_artifact_is_retained_without_reviving_cancelled_micro_task | 1 |
| `test_late_result_never_reopens_a_cancelled_goal` | test_late_result_never_reopens_a_cancelled_micro_task | 1 |
| `test_newest_concluded_own_result_reaches_every_governed_read` | test_newest_concluded_own_result_reaches_every_micro_task_read | 1 |
| `test_no_turns_and_ungoverned_tasks_project_no_summary` | test_no_turns_and_execution_tasks_project_no_summary | 1 |
| `test_oversized_goal_stops_before_selection_and_recovers_with_explicit_buddy` | test_oversized_micro_task_stops_before_selection_and_recovers_with_explicit_buddy | 1 |
| `test_proxy_delegation_records_the_actual_authorizing_host_after_root_takeover` | test_proxy_micro_task_records_the_actual_authorizing_host_after_root_takeover | 1 |
| `test_public_task_cancel_cannot_bypass_governed_control` | test_public_task_cancel_cannot_bypass_micro_task_control | 1 |
| `test_queued_goal_is_fenced_but_passive_host_request_is_preserved` | test_queued_micro_task_is_fenced_but_passive_host_request_is_preserved | 1 |
| `test_rejected_then_failed_goal_can_be_concluded` | test_rejected_then_failed_micro_task_can_be_concluded | 1 |
| `test_router_unavailable_opens_same_goal_attention_and_complete_host_buddy_recovers` | test_router_unavailable_opens_same_micro_task_attention_and_complete_host_buddy_recovers | 1 |
| `test_selection_decision_outside_a_goal_is_classified_and_has_no_lineage` | test_selection_decision_outside_a_micro_task_is_classified_and_has_no_lineage | 1 |
| `test_templates_keep_the_same_goal_and_complete_identity_with_supported_parameters` | test_templates_keep_the_same_micro_task_and_complete_identity_with_supported_parameters | 1 |
| `test_the_governed_activity_callback_publishes_bounded_phases` | test_the_micro_task_activity_callback_publishes_bounded_phases | 1 |
| `test_unknown_stop_blocks_governed_commands_and_does_not_claim_prior_output` | test_unknown_stop_blocks_micro_task_commands_and_does_not_claim_prior_output | 1 |
| `test_worker_levels_keep_the_governed_carrier_and_record_the_sent_mode` | test_worker_levels_keep_the_micro_task_carrier_and_record_the_sent_mode | 1 |
| `totalDelegations` | totalMicroTasks | 1 |
| `ungoverned` | execution、ordinary | 2 |
| `unknown-goal` | unknown-micro-task | 1 |
| `unrelated-goal` | unrelated-micro-task | 1 |
| `whole-goal` | whole-micro-task | 4 |

清单未逐个列出的内部名称、样例标识和带同族词干的复合词形共 173 个；清单的 internal 行已按词干概括其工作量，本说明把它们逐名列出：`CompactGovernedEnvelopeTests`、`DELEGATION_FIELDS`、`DELEGATION_KINDS`、`DelegationCard`、`DelegationDetailRows`、`DelegationStrip`、`DelegationTestCase`、`GOVERNED_RECEIPTS`、`GoalProvenanceTests`、`GoalStopWaitTests`、`GovernedBoundaryTests`、`GovernedCliTests`、`GovernedParameterDocumentTests`、`GovernedPromptTests`、`GovernedServiceStopTests`、`GovernedWorkerTestCase`、`SingleDelegationCard`、`UNTITLED_DELEGATION`、`UngovernedTests`、`_KNOWN_GOVERNED_FIELDS`、`_TASK_GOAL_DIGEST`、`_buddy_matches_goal`、`_goal_binding`、`_goal_shared_native`、`_goal_view`、`_governed_get_command`、`_governed_record`、`_governed_result_view`、`_governed_round`、`_governed_turn`、`attach_governed_task`、`buddy-goal`、`cancel_goal`、`cancelled_goal`、`claim_governed`、`claim_governed_claim`、`delegation-band`、`delegation-band-head`、`delegation-band-toggle`、`delegation-card`、`delegation-card-meta`、`delegation-card-result`、`delegation-card-result-text`、`delegation-card-state`、`delegation-card-title`、`delegation-card-top`、`delegation-current`、`delegation-hidden-current`、`delegation-list-collapsed`、`delegation-show-all`、`delegation-strip`、`delegation-strip-region`、`delegation-tree`、`delegationField`、`delegationTitle`、`delegation_of`、`delegation_result`、`foreign-goal-session`、`foreign_goal`、`gated-governed`、`goal-1`、`goal-2`、`goal-binding`、`goal-digest`、`goal-owned`、`goal-shaped`、`goal-state`、`goalState`、`goal_dir`、`goal_id`、`goal_owned_storage`、`goal_root`、`goal_row`、`goal_state`、`governed-1`、`governed-native`、`governed-permission`、`governed-permission-answer`、`governed-run`、`governed-task`、`governed-turn`、`governedQuotaFailure`、`governedRuns`、`governedSuccess`、`governed_agent_args`、`governed_brief`、`governed_fold`、`governed_permission`、`governed_prompt`、`governed_receipt`、`governed_record`、`governed_run_id`、`governed_submit`、`governed_task_ids`、`governed_view`、`governed_yield`、`long-goal-coding`、`non-governed`、`per-delegation`、`private_goal`、`private_goals`、`protected-goals`、`reclaim-own-goal`、`route_goal`、`run-governed`、`run_governed_prompt`、`standalone-delegation`、`submit_governed`、`task-goal`、`test_a_binding_for_another_goal_or_buddy_is_refused`、`test_a_completed_goal_root_turn_reports_goal_owned_storage`、`test_a_governed_mutation_from_a_file_uses_the_same_private_control_file`、`test_a_governed_turn_cannot_be_hidden_by_a_both_null_identity`、`test_a_governed_turn_carries_its_unknown_notifications_in_the_result`、`test_a_governed_turn_settles_with_a_verified_completion_tool_value`、`test_a_real_ungoverned_claim_keeps_the_both_null_rule`、`test_all_adapters_private_goals_are_visible`、`test_an_answer_with_no_session_service_takes_no_governed_path`、`test_an_answered_host_question_flows_through_the_governed_turn`、`test_an_incomplete_governed_turn_saves_no_binding`、`test_an_ungoverned_context_cannot_publish_worker_usage`、`test_awaiting_and_grace_goals_are_kept_and_reclaim_takes_only_the_qualified_goal`、`test_cancel_routes_owned_attempt_and_late_selection_cannot_start_goal`、`test_cancelled_goal_can_cleanup_only_after_a_recorded_conclusion`、`test_cancelled_goal_never_becomes_success_from_a_late_completed_execution`、`test_candidate_native_resume_prepares_one_goal_native_root_for_both_attempts`、`test_codex_user_sessions_are_not_inventory_or_goal_removal_targets`、`test_concluded_delivered_goal_reports_the_conclusion_instead_of_acknowledgement`、`test_control_methods_are_the_current_governed_mutations`、`test_delivered_goal_without_a_conclusion_still_awaits_final_acknowledgement`、`test_every_task_view_carries_the_delegation_contract`、`test_execution_results_unwrap_the_task_view_and_goal_results_do_not`、`test_execution_task_without_a_source_goal_reports_unknown_hosts`、`test_explicit_long_goal_does_not_require_selector`、`test_explicit_reroute_after_known_retry_time_continues_same_goal_without_model`、`test_failed_goal_conclusion_reclaims_checkout_but_retains_partial_artifacts`、`test_final_noop_continuation_still_requires_earlier_goal_change`、`test_goal_at_exact_routing_byte_limit_is_passed_whole`、`test_goal_native_state_stays_out_of_the_verified_backup`、`test_goal_reports_source_and_current_host_with_the_source_project`、`test_goal_wait_timeout_expires_only_the_wait_and_names_current_commands`、`test_governed_boundary_has_post_attention_revision_and_real_continue_tuple`、`test_governed_final_envelope_is_compact_and_points_at_the_full_result`、`test_governed_get_and_execution_status_stay_distinct`、`test_governed_goal_can_explicitly_omit_an_execution_deadline`、`test_governed_mock_codex_zcode_claude_and_native_continuation`、`test_governed_mutation_without_the_control_file_fails_closed`、`test_governed_task_ops_require_host_control`、`test_governed_turn_leaves_unknown_login_to_the_native_cli`、`test_governed_valid_abstention_payload_has_program_nature_before_answered_notification`、`test_governed_worker_seals_only_a_proven_claude_result`、`test_helper_takes_lineage_project_and_hosts_from_its_root_goal`、`test_history_on_an_ungoverned_task_is_honestly_empty`、`test_internal_routing_decision_belongs_to_its_owning_goal`、`test_late_completed_artifact_is_retained_without_reviving_cancelled_goal`、`test_late_result_never_reopens_a_cancelled_goal`、`test_newest_concluded_own_result_reaches_every_governed_read`、`test_no_turns_and_ungoverned_tasks_project_no_summary`、`test_oversized_goal_stops_before_selection_and_recovers_with_explicit_buddy`、`test_proxy_delegation_records_the_actual_authorizing_host_after_root_takeover`、`test_public_task_cancel_cannot_bypass_governed_control`、`test_queued_goal_is_fenced_but_passive_host_request_is_preserved`、`test_rejected_then_failed_goal_can_be_concluded`、`test_router_unavailable_opens_same_goal_attention_and_complete_host_buddy_recovers`、`test_selection_decision_outside_a_goal_is_classified_and_has_no_lineage`、`test_templates_keep_the_same_goal_and_complete_identity_with_supported_parameters`、`test_the_governed_activity_callback_publishes_bounded_phases`、`test_unknown_stop_blocks_governed_commands_and_does_not_claim_prior_output`、`test_worker_levels_keep_the_governed_carrier_and_record_the_sent_mode`、`totalDelegations`、`unknown-goal`、`unrelated-goal`、`whole-goal`。其中包括 `buddy-goal` → `buddy-micro-task`（只改微任务部分），关系结果 `delegation_result` → `lineage_result`，模块 `delegation.py` → `micro_task.py`，Worker 提示词及其两项哈希快照，前端类型、CSS 选择器和列表折叠偏好键。

清单中的 `legacy-goal-not-directory` 对应的旧私有迁移代码已在基点前删除，本次跳过。较早清单中的旧前端文件位置和升级实现位置以当前源码为准，没有恢复已删除的模块。`BUDDY_`、指产品的 `buddy`、动词 delegate、DSH 上游词、版本号和中性工程名称按范围保留。

自查在 `src`、`tests/python`、`tests/probes`、`apps/console` 和 `packaging` 上不分大小写搜索 `goal|delegation|governed`，遵循仓库忽略规则排除依赖与临时缓存，并包含新构建的打包资产。残余共 16 处，逐处如下；生产前端与打包资产无旧词。

| 位置 | 留下的内容 | 原因 |
| --- | --- | --- |
| `apps/console/src/routing-console.test.tsx:51` | `完成 goal 状态内核` | 原样显示的任务文字及两处匹配断言，按显示文字不变的要求保留。 |
| `apps/console/src/routing-console.test.tsx:317` | `完成 goal 状态内核` | 原样显示的任务文字及两处匹配断言，按显示文字不变的要求保留。 |
| `apps/console/src/routing-console.test.tsx:442` | `完成 goal 状态内核` | 原样显示的任务文字及两处匹配断言，按显示文字不变的要求保留。 |
| `apps/console/src/task-activity.test.tsx:140` | `Stop goal` | 外部提供的取消理由，是测试显示内容，原样保留。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:125` | `tool-goal` | DSH 上游原生工具功能名，清单明确不改。 |
| `src/hey_my_buddy/buddy/roles/texts/lead.txt:2` | `delegation authority` | Lead 的委派权术语，指授权而非一个微任务；对应文字和验证保留。 |
| `src/hey_my_buddy/buddy/roles/texts/lead.txt:5` | `delegation authority` | Lead 的委派权术语，指授权而非一个微任务；对应文字和验证保留。 |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:760` | `delegationDepth` | DSH 上游原生会话字段，清单明确不改。 |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py:868` | `delegationDepth` | DSH 上游原生会话字段，清单明确不改。 |
| `tests/python/buddy/harnesses/dsh/fixtures/native-usage-dsh.json:12` | `delegationDepth` | DSH 上游原生会话字段，清单明确不改。 |
| `tests/python/buddy/harnesses/dsh/fixtures/workflow_agent.py:161` | `delegationDepth` | DSH 上游原生会话字段，清单明确不改。 |
| `tests/python/buddy/harnesses/dsh/test_native_run.py:63` | `tool-goal` | DSH 上游原生工具功能名，清单明确不改。 |
| `tests/python/buddy/harnesses/dsh/test_session_records.py:48` | `delegationDepth` | DSH 上游原生会话字段，清单明确不改。 |
| `tests/python/buddy/harnesses/dsh/test_session_records.py:80` | `delegationDepth` | DSH 上游原生会话字段，清单明确不改。 |
| `tests/python/buddy/roles/test_role_prompts.py:50` | `delegation authority` | Lead 的委派权术语，指授权而非一个微任务；对应文字和验证保留。 |
| `tests/python/buddy/roles/test_role_prompts.py:55` | `delegation authority` | Lead 的委派权术语，指授权而非一个微任务；对应文字和验证保留。 |

改名和同步引用涉及的文件如下（相同路径表示文件内容改名）；升级样板、`packaging/generate-upgrade-fixture.py`、其他 docs、skills、README、AGENTS.md 和 CONTEXT.md 均未改。

| 原路径 | 当前路径 |
| --- | --- |
| `apps/console/design/work-macro-task-timeline.html` | `apps/console/design/work-macro-task-timeline.html` |
| `apps/console/src/MacroTaskOverview.tsx` | `apps/console/src/MacroTaskOverview.tsx` |
| `apps/console/src/MacroTaskTimeline.tsx` | `apps/console/src/MacroTaskTimeline.tsx` |
| `apps/console/src/MacroTasks.tsx` | `apps/console/src/MacroTasks.tsx` |
| `apps/console/src/RunDetailPane.tsx` | `apps/console/src/RunDetailPane.tsx` |
| `apps/console/src/TaskDetails.tsx` | `apps/console/src/TaskDetails.tsx` |
| `apps/console/src/Tasks.tsx` | `apps/console/src/Tasks.tsx` |
| `apps/console/src/WorkflowPanel.test.tsx` | `apps/console/src/WorkflowPanel.test.tsx` |
| `apps/console/src/WorkflowPanel.tsx` | `apps/console/src/WorkflowPanel.tsx` |
| `apps/console/src/api.test.ts` | `apps/console/src/api.test.ts` |
| `apps/console/src/buddy-name.test.tsx` | `apps/console/src/buddy-name.test.tsx` |
| `apps/console/src/console-data.test.ts` | `apps/console/src/console-data.test.ts` |
| `apps/console/src/console-data.ts` | `apps/console/src/console-data.ts` |
| `apps/console/src/console-session.test.tsx` | `apps/console/src/console-session.test.tsx` |
| `apps/console/src/delegation-strip-collapse.test.tsx` | `apps/console/src/micro-task-strip-collapse.test.tsx` |
| `apps/console/src/desktop-console.test.tsx` | `apps/console/src/desktop-console.test.tsx` |
| `apps/console/src/history-navigation.test.tsx` | `apps/console/src/history-navigation.test.tsx` |
| `apps/console/src/host-workflow-panel.test.tsx` | `apps/console/src/host-workflow-panel.test.tsx` |
| `apps/console/src/host-workflow.test.ts` | `apps/console/src/host-workflow.test.ts` |
| `apps/console/src/host-workflow.ts` | `apps/console/src/host-workflow.ts` |
| `apps/console/src/inspector-card.ts` | `apps/console/src/inspector-card.ts` |
| `apps/console/src/integration.test.ts` | `apps/console/src/integration.test.ts` |
| `apps/console/src/layout-016.test.tsx` | `apps/console/src/layout-016.test.tsx` |
| `apps/console/src/local-refresh.test.tsx` | `apps/console/src/local-refresh.test.tsx` |
| `apps/console/src/macro-task-detail.test.tsx` | `apps/console/src/macro-task-detail.test.tsx` |
| `apps/console/src/macro-task-display-016.test.ts` | `apps/console/src/macro-task-display-016.test.ts` |
| `apps/console/src/macro-task-display.ts` | `apps/console/src/macro-task-display.ts` |
| `apps/console/src/macro-task-fixtures.ts` | `apps/console/src/macro-task-fixtures.ts` |
| `apps/console/src/macro-task-metrics.test.ts` | `apps/console/src/macro-task-metrics.test.ts` |
| `apps/console/src/macro-task-metrics.ts` | `apps/console/src/macro-task-metrics.ts` |
| `apps/console/src/macro-task-tab.test.tsx` | `apps/console/src/macro-task-tab.test.tsx` |
| `apps/console/src/macro-task-timeline-016.test.tsx` | `apps/console/src/macro-task-timeline-016.test.tsx` |
| `apps/console/src/macro-task-timeline.test.tsx` | `apps/console/src/macro-task-timeline.test.tsx` |
| `apps/console/src/macro-task-types.ts` | `apps/console/src/macro-task-types.ts` |
| `apps/console/src/native-session.tsx` | `apps/console/src/native-session.tsx` |
| `apps/console/src/preview-contract.test.ts` | `apps/console/src/preview-contract.test.ts` |
| `apps/console/src/routing-console.test.tsx` | `apps/console/src/routing-console.test.tsx` |
| `apps/console/src/routing-timeline.test.tsx` | `apps/console/src/routing-timeline.test.tsx` |
| `apps/console/src/styles.css` | `apps/console/src/styles.css` |
| `apps/console/src/task-activity.tsx` | `apps/console/src/task-activity.tsx` |
| `apps/console/src/task-state.test.ts` | `apps/console/src/task-state.test.ts` |
| `apps/console/src/task-state.ts` | `apps/console/src/task-state.ts` |
| `apps/console/src/task-title.test.tsx` | `apps/console/src/task-title.test.tsx` |
| `apps/console/src/timeline-layers.test.tsx` | `apps/console/src/timeline-layers.test.tsx` |
| `apps/console/src/types.ts` | `apps/console/src/types.ts` |
| `apps/console/src/ui-box-model.test.tsx` | `apps/console/src/ui-box-model.test.tsx` |
| `apps/console/src/use-macro-task-timeline.test.tsx` | `apps/console/src/use-macro-task-timeline.test.tsx` |
| `apps/console/src/use-macro-task-timeline.ts` | `apps/console/src/use-macro-task-timeline.ts` |
| `apps/console/src/use-task-history.test.tsx` | `apps/console/src/use-task-history.test.tsx` |
| `apps/console/src/use-workflow.test.tsx` | `apps/console/src/use-workflow.test.tsx` |
| `apps/console/src/use-workflow.ts` | `apps/console/src/use-workflow.ts` |
| `apps/console/src/workflow-types.ts` | `apps/console/src/workflow-types.ts` |
| `src/hey_my_buddy/blackboard/routing/decision.py` | `src/hey_my_buddy/blackboard/routing/decision.py` |
| `src/hey_my_buddy/blackboard/routing/router_boundary.py` | `src/hey_my_buddy/blackboard/routing/router_boundary.py` |
| `src/hey_my_buddy/blackboard/routing/router_boundary_data.py` | `src/hey_my_buddy/blackboard/routing/router_boundary_data.py` |
| `src/hey_my_buddy/blackboard/service/daemon.py` | `src/hey_my_buddy/blackboard/service/daemon.py` |
| `src/hey_my_buddy/blackboard/service/live_registry.py` | `src/hey_my_buddy/blackboard/service/live_registry.py` |
| `src/hey_my_buddy/blackboard/service/service.py` | `src/hey_my_buddy/blackboard/service/service.py` |
| `src/hey_my_buddy/blackboard/store/backup.py` | `src/hey_my_buddy/blackboard/store/backup.py` |
| `src/hey_my_buddy/blackboard/store/db.py` | `src/hey_my_buddy/blackboard/store/db.py` |
| `src/hey_my_buddy/blackboard/store/store.py` | `src/hey_my_buddy/blackboard/store/store.py` |
| `src/hey_my_buddy/blackboard/tasks/delegation.py` | `src/hey_my_buddy/blackboard/tasks/micro_task.py` |
| `src/hey_my_buddy/blackboard/tasks/macro_task_summary_cache.py` | `src/hey_my_buddy/blackboard/tasks/macro_task_summary_cache.py` |
| `src/hey_my_buddy/blackboard/tasks/macro_tasks.py` | `src/hey_my_buddy/blackboard/tasks/macro_tasks.py` |
| `src/hey_my_buddy/blackboard/tasks/scheduling.py` | `src/hey_my_buddy/blackboard/tasks/scheduling.py` |
| `src/hey_my_buddy/blackboard/tasks/storage.py` | `src/hey_my_buddy/blackboard/tasks/storage.py` |
| `src/hey_my_buddy/blackboard/tasks/worker_sessions.py` | `src/hey_my_buddy/blackboard/tasks/worker_sessions.py` |
| `src/hey_my_buddy/blackboard/tasks/workflow.py` | `src/hey_my_buddy/blackboard/tasks/workflow.py` |
| `src/hey_my_buddy/blackboard/tasks/workspace.py` | `src/hey_my_buddy/blackboard/tasks/workspace.py` |
| `src/hey_my_buddy/buddy/harnesses/base.py` | `src/hey_my_buddy/buddy/harnesses/base.py` |
| `src/hey_my_buddy/buddy/harnesses/claude/adapter.py` | `src/hey_my_buddy/buddy/harnesses/claude/adapter.py` |
| `src/hey_my_buddy/buddy/harnesses/claude/config.py` | `src/hey_my_buddy/buddy/harnesses/claude/config.py` |
| `src/hey_my_buddy/buddy/harnesses/claude/native_run.py` | `src/hey_my_buddy/buddy/harnesses/claude/native_run.py` |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py` | `src/hey_my_buddy/buddy/harnesses/codex/native_run.py` |
| `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py` | `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py` |
| `src/hey_my_buddy/buddy/harnesses/dsh/protocol.py` | `src/hey_my_buddy/buddy/harnesses/dsh/protocol.py` |
| `src/hey_my_buddy/buddy/harnesses/inquiry_bridge.py` | `src/hey_my_buddy/buddy/harnesses/inquiry_bridge.py` |
| `src/hey_my_buddy/buddy/harnesses/registry.py` | `src/hey_my_buddy/buddy/harnesses/registry.py` |
| `src/hey_my_buddy/buddy/harnesses/run_contract.py` | `src/hey_my_buddy/buddy/harnesses/run_contract.py` |
| `src/hey_my_buddy/buddy/harnesses/session_receipts.py` | `src/hey_my_buddy/buddy/harnesses/session_receipts.py` |
| `src/hey_my_buddy/buddy/harnesses/zcode/native_run.py` | `src/hey_my_buddy/buddy/harnesses/zcode/native_run.py` |
| `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py` | `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py` |
| `src/hey_my_buddy/buddy/roles/run_execution.py` | `src/hey_my_buddy/buddy/roles/run_execution.py` |
| `src/hey_my_buddy/buddy/roles/turn_io.py` | `src/hey_my_buddy/buddy/roles/turn_io.py` |
| `src/hey_my_buddy/buddy/roles/worker_services.py` | `src/hey_my_buddy/buddy/roles/worker_services.py` |
| `src/hey_my_buddy/buddy/runtime/partial_outputs.py` | `src/hey_my_buddy/buddy/runtime/partial_outputs.py` |
| `src/hey_my_buddy/cli/blocking.py` | `src/hey_my_buddy/cli/blocking.py` |
| `src/hey_my_buddy/cli/cli_help.py` | `src/hey_my_buddy/cli/cli_help.py` |
| `src/hey_my_buddy/cli/cli_views.py` | `src/hey_my_buddy/cli/cli_views.py` |
| `src/hey_my_buddy/cli/main.py` | `src/hey_my_buddy/cli/main.py` |
| `src/hey_my_buddy/console/reads.py` | `src/hey_my_buddy/console/reads.py` |
| `src/hey_my_buddy/install/upgrade.py` | `src/hey_my_buddy/install/upgrade.py` |
| `src/hey_my_buddy/private_dirs.py` | `src/hey_my_buddy/private_dirs.py` |
| `src/hey_my_buddy/protocol/client.py` | `src/hey_my_buddy/protocol/client.py` |
| `src/hey_my_buddy/protocol/contracts.py` | `src/hey_my_buddy/protocol/contracts.py` |
| `src/hey_my_buddy/protocol/schemas.py` | `src/hey_my_buddy/protocol/schemas.py` |
| `src/hey_my_buddy/protocol/transport.py` | `src/hey_my_buddy/protocol/transport.py` |
| `src/hey_my_buddy/protocol/usage.py` | `src/hey_my_buddy/protocol/usage.py` |
| `tests/probes/dsh_native_resume_preflight.py` | `tests/probes/dsh_native_resume_preflight.py` |
| `tests/probes/dsh_native_resume_smoke.py` | `tests/probes/dsh_native_resume_smoke.py` |
| `tests/probes/macro_task_console_preview.py` | `tests/probes/macro_task_console_preview.py` |
| `tests/python/blackboard/evaluation/test_other_native_observations.py` | `tests/python/blackboard/evaluation/test_other_native_observations.py` |
| `tests/python/blackboard/routing/test_decision.py` | `tests/python/blackboard/routing/test_decision.py` |
| `tests/python/blackboard/routing/test_router_boundary.py` | `tests/python/blackboard/routing/test_router_boundary.py` |
| `tests/python/blackboard/routing/test_router_boundary_integration.py` | `tests/python/blackboard/routing/test_router_boundary_integration.py` |
| `tests/python/blackboard/routing/test_router_dispatch.py` | `tests/python/blackboard/routing/test_router_dispatch.py` |
| `tests/python/blackboard/routing/test_router_failover_integration.py` | `tests/python/blackboard/routing/test_router_failover_integration.py` |
| `tests/python/blackboard/routing/test_routing_history.py` | `tests/python/blackboard/routing/test_routing_history.py` |
| `tests/python/blackboard/routing/test_routing_preferences.py` | `tests/python/blackboard/routing/test_routing_preferences.py` |
| `tests/python/blackboard/routing/test_selection_publication.py` | `tests/python/blackboard/routing/test_selection_publication.py` |
| `tests/python/blackboard/routing/test_single_router_requests.py` | `tests/python/blackboard/routing/test_single_router_requests.py` |
| `tests/python/blackboard/routing/test_sole_candidate_selection.py` | `tests/python/blackboard/routing/test_sole_candidate_selection.py` |
| `tests/python/blackboard/service/test_governed_service_stop.py` | `tests/python/blackboard/service/test_micro_task_service_stop.py` |
| `tests/python/blackboard/service/test_live_holder_integration.py` | `tests/python/blackboard/service/test_live_holder_integration.py` |
| `tests/python/blackboard/service/test_live_registry.py` | `tests/python/blackboard/service/test_live_registry.py` |
| `tests/python/blackboard/store/test_blackboard.py` | `tests/python/blackboard/store/test_blackboard.py` |
| `tests/python/blackboard/store/test_store.py` | `tests/python/blackboard/store/test_store.py` |
| `tests/python/blackboard/tasks/test_console_macro_task_fixture.py` | `tests/python/blackboard/tasks/test_console_macro_task_fixture.py` |
| `tests/python/blackboard/tasks/test_delegation_history.py` | `tests/python/blackboard/tasks/test_micro_task_history.py` |
| `tests/python/blackboard/tasks/test_dsh_resume_selection.py` | `tests/python/blackboard/tasks/test_dsh_resume_selection.py` |
| `tests/python/blackboard/tasks/test_host_workflow.py` | `tests/python/blackboard/tasks/test_host_workflow.py` |
| `tests/python/blackboard/tasks/test_host_workflow_worker.py` | `tests/python/blackboard/tasks/test_host_workflow_worker.py` |
| `tests/python/blackboard/tasks/test_inquiry.py` | `tests/python/blackboard/tasks/test_inquiry.py` |
| `tests/python/blackboard/tasks/test_macro_task_summary_cache.py` | `tests/python/blackboard/tasks/test_macro_task_summary_cache.py` |
| `tests/python/blackboard/tasks/test_macro_tasks.py` | `tests/python/blackboard/tasks/test_macro_tasks.py` |
| `tests/python/blackboard/tasks/test_model_service_concurrency.py` | `tests/python/blackboard/tasks/test_model_service_concurrency.py` |
| `tests/python/blackboard/tasks/test_parallel_dispatch.py` | `tests/python/blackboard/tasks/test_parallel_dispatch.py` |
| `tests/python/blackboard/tasks/test_permission_levels.py` | `tests/python/blackboard/tasks/test_permission_levels.py` |
| `tests/python/blackboard/tasks/test_permission_resume.py` | `tests/python/blackboard/tasks/test_permission_resume.py` |
| `tests/python/blackboard/tasks/test_scheduler_lanes.py` | `tests/python/blackboard/tasks/test_scheduler_lanes.py` |
| `tests/python/blackboard/tasks/test_scope_recovery.py` | `tests/python/blackboard/tasks/test_scope_recovery.py` |
| `tests/python/blackboard/tasks/test_storage.py` | `tests/python/blackboard/tasks/test_storage.py` |
| `tests/python/blackboard/tasks/test_title_fallback.py` | `tests/python/blackboard/tasks/test_title_fallback.py` |
| `tests/python/blackboard/tasks/test_worker_claim_stash.py` | `tests/python/blackboard/tasks/test_worker_claim_stash.py` |
| `tests/python/blackboard/tasks/test_workflow.py` | `tests/python/blackboard/tasks/test_workflow.py` |
| `tests/python/blackboard/tasks/test_workflow_cancellation.py` | `tests/python/blackboard/tasks/test_workflow_cancellation.py` |
| `tests/python/blackboard/tasks/test_workflow_preparation.py` | `tests/python/blackboard/tasks/test_workflow_preparation.py` |
| `tests/python/blackboard/tasks/test_workflow_real.py` | `tests/python/blackboard/tasks/test_workflow_real.py` |
| `tests/python/blackboard/tasks/test_workflow_routing.py` | `tests/python/blackboard/tasks/test_workflow_routing.py` |
| `tests/python/blackboard/tasks/test_workflow_stash.py` | `tests/python/blackboard/tasks/test_workflow_stash.py` |
| `tests/python/blackboard/tasks/test_workflow_stop_surface.py` | `tests/python/blackboard/tasks/test_workflow_stop_surface.py` |
| `tests/python/blackboard/tasks/test_workflow_worker.py` | `tests/python/blackboard/tasks/test_workflow_worker.py` |
| `tests/python/blackboard/tasks/test_workspace_api.py` | `tests/python/blackboard/tasks/test_workspace_api.py` |
| `tests/python/blackboard/tasks/test_workspace_lifecycle.py` | `tests/python/blackboard/tasks/test_workspace_lifecycle.py` |
| `tests/python/blackboard/tasks/test_workspace_stash.py` | `tests/python/blackboard/tasks/test_workspace_stash.py` |
| `tests/python/blackboard/tasks/test_workspace_submission_cleanup.py` | `tests/python/blackboard/tasks/test_workspace_submission_cleanup.py` |
| `tests/python/buddy/harnesses/claude/test_claude.py` | `tests/python/buddy/harnesses/claude/test_claude.py` |
| `tests/python/buddy/harnesses/claude/test_claude_tool_evidence.py` | `tests/python/buddy/harnesses/claude/test_claude_tool_evidence.py` |
| `tests/python/buddy/harnesses/claude/test_claude_worker.py` | `tests/python/buddy/harnesses/claude/test_claude_worker.py` |
| `tests/python/buddy/harnesses/claude/test_native_run.py` | `tests/python/buddy/harnesses/claude/test_native_run.py` |
| `tests/python/buddy/harnesses/codex/test_codex.py` | `tests/python/buddy/harnesses/codex/test_codex.py` |
| `tests/python/buddy/harnesses/codex/test_codex_continuation.py` | `tests/python/buddy/harnesses/codex/test_codex_continuation.py` |
| `tests/python/buddy/harnesses/codex/test_codex_protocol.py` | `tests/python/buddy/harnesses/codex/test_codex_protocol.py` |
| `tests/python/buddy/harnesses/codex/test_native_run.py` | `tests/python/buddy/harnesses/codex/test_native_run.py` |
| `tests/python/buddy/harnesses/dsh/acp/fake_agent.py` | `tests/python/buddy/harnesses/dsh/acp/fake_agent.py` |
| `tests/python/buddy/harnesses/dsh/fixtures/workflow_agent.py` | `tests/python/buddy/harnesses/dsh/fixtures/workflow_agent.py` |
| `tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py` | `tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py` |
| `tests/python/buddy/harnesses/dsh/test_dsh_session_storage.py` | `tests/python/buddy/harnesses/dsh/test_dsh_session_storage.py` |
| `tests/python/buddy/harnesses/dsh/test_dsh_tool_evidence.py` | `tests/python/buddy/harnesses/dsh/test_dsh_tool_evidence.py` |
| `tests/python/buddy/harnesses/dsh/test_native_resume.py` | `tests/python/buddy/harnesses/dsh/test_native_resume.py` |
| `tests/python/buddy/harnesses/dsh/test_native_run.py` | `tests/python/buddy/harnesses/dsh/test_native_run.py` |
| `tests/python/buddy/harnesses/dsh/test_session_records.py` | `tests/python/buddy/harnesses/dsh/test_session_records.py` |
| `tests/python/buddy/harnesses/dsh/test_worker_permissions.py` | `tests/python/buddy/harnesses/dsh/test_worker_permissions.py` |
| `tests/python/buddy/harnesses/fixtures/run_contract/codex-payloads.json` | `tests/python/buddy/harnesses/fixtures/run_contract/codex-payloads.json` |
| `tests/python/buddy/harnesses/test_adapter_usage.py` | `tests/python/buddy/harnesses/test_adapter_usage.py` |
| `tests/python/buddy/harnesses/test_live_channel.py` | `tests/python/buddy/harnesses/test_live_channel.py` |
| `tests/python/buddy/harnesses/test_pending_ready_receipt_guards.py` | `tests/python/buddy/harnesses/test_pending_ready_receipt_guards.py` |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py` | `tests/python/buddy/harnesses/test_private_adapter_invariants.py` |
| `tests/python/buddy/harnesses/test_session_receipts.py` | `tests/python/buddy/harnesses/test_session_receipts.py` |
| `tests/python/buddy/harnesses/zcode/fixtures/mock_zcode.py` | `tests/python/buddy/harnesses/zcode/fixtures/mock_zcode.py` |
| `tests/python/buddy/harnesses/zcode/fixtures/mock_zcode_usage.py` | `tests/python/buddy/harnesses/zcode/fixtures/mock_zcode_usage.py` |
| `tests/python/buddy/harnesses/zcode/test_native_run.py` | `tests/python/buddy/harnesses/zcode/test_native_run.py` |
| `tests/python/buddy/harnesses/zcode/test_zcode.py` | `tests/python/buddy/harnesses/zcode/test_zcode.py` |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py` | `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py` |
| `tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py` | `tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py` |
| `tests/python/buddy/harnesses/zcode/test_zcode_protocol.py` | `tests/python/buddy/harnesses/zcode/test_zcode_protocol.py` |
| `tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py` | `tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py` |
| `tests/python/buddy/roles/test_dsh_resume_storage.py` | `tests/python/buddy/roles/test_dsh_resume_storage.py` |
| `tests/python/buddy/roles/test_role_live_seam.py` | `tests/python/buddy/roles/test_role_live_seam.py` |
| `tests/python/buddy/roles/test_run_controller_signals.py` | `tests/python/buddy/roles/test_run_controller_signals.py` |
| `tests/python/buddy/roles/test_run_execution.py` | `tests/python/buddy/roles/test_run_execution.py` |
| `tests/python/buddy/roles/test_schema_worker.py` | `tests/python/buddy/roles/test_schema_worker.py` |
| `tests/python/buddy/roles/test_worker_services.py` | `tests/python/buddy/roles/test_worker_services.py` |
| `tests/python/buddy/runtime/test_run.py` | `tests/python/buddy/runtime/test_run.py` |
| `tests/python/buddy/runtime/test_worker_invariants.py` | `tests/python/buddy/runtime/test_worker_invariants.py` |
| `tests/python/buddy/runtime/test_worker_live_wiring.py` | `tests/python/buddy/runtime/test_worker_live_wiring.py` |
| `tests/python/buddy/runtime/test_worker_sessions.py` | `tests/python/buddy/runtime/test_worker_sessions.py` |
| `tests/python/cli/test_blocking.py` | `tests/python/cli/test_blocking.py` |
| `tests/python/cli/test_cli.py` | `tests/python/cli/test_cli.py` |
| `tests/python/cli/test_cli_views.py` | `tests/python/cli/test_cli_views.py` |
| `tests/python/cli/test_host_cli.py` | `tests/python/cli/test_host_cli.py` |
| `tests/python/cli/test_host_preview.py` | `tests/python/cli/test_host_preview.py` |
| `tests/python/cli/test_skill_workflow.py` | `tests/python/cli/test_skill_workflow.py` |
| `tests/python/console/test_console.py` | `tests/python/console/test_console.py` |
| `tests/python/console/test_console_macro_task_cache.py` | `tests/python/console/test_console_macro_task_cache.py` |
| `tests/python/fixtures/console_macro_task_board.py` | `tests/python/fixtures/console_macro_task_board.py` |
| `tests/python/install/test_upgrade_host_cutover.py` | `tests/python/install/test_upgrade_host_cutover.py` |
| `tests/python/mock_workspace.py` | `tests/python/mock_workspace.py` |
| `tests/python/protocol/fixtures/native_turn.py` | `tests/python/protocol/fixtures/native_turn.py` |
| `tests/python/protocol/test_ctwo_integration.py` | `tests/python/protocol/test_ctwo_integration.py` |
| `tests/python/test_private_directories.py` | `tests/python/test_private_directories.py` |
| `src/hey_my_buddy/console/assets/index.html` | `src/hey_my_buddy/console/assets/index.html` |
| `src/hey_my_buddy/console/assets/assets/index-BdWZhNZo.js` | `src/hey_my_buddy/console/assets/assets/index-BlojrtyL.js` |
| `src/hey_my_buddy/console/assets/assets/index-BWfWCvCN.css` | `src/hey_my_buddy/console/assets/assets/index-CIsJ0mzd.css` |

所有 Python 检查使用 `PYTHONPATH=src <Host 指定的解释器> -m hey_my_buddy.cli.checks [测试文件]`，未给进程数参数、未设 `BUDDY_CHECKS_JOBS`，未向指定解释器环境写入内容。检查器负责每个测试文件的私有主目录、状态目录、运行时目录和系统临时根；本候选没有测试时调用日常服务。

Python 首轮完整检查退出 1：234 个文件中 10 个失败，检查器报告通过文件内 3621 项测试，私有根已删除。首轮日志为 `tmp/python-full-first.log`。这轮在交叉引用修正期间运行，因此只作为查漏证据，不作为最终源码通过证据。

| 首轮失败文件 | 原因 | 单独重跑结果 |
| --- | --- | --- |
| `tests/python/blackboard/tasks/test_workflow_preparation.py` | 提交钩子的 microTaskFingerprint 关键字与声明不一致，返回 INTERNAL_ERROR；已修正。 | 退出 0；`tmp/python-retry-blackboard.tasks.test_workflow_preparation.log` |
| `tests/python/blackboard/tasks/test_workflow_real.py` | 首次为同一提交关键字遗漏；第一次单独重跑又发现 fake ACP 的 --prompt-mode 值误写成 microTask，已改为 micro-task。 | 退出 1；修正 --prompt-mode 后再次单独重跑退出 0（tmp/python-real-corrected.log）；`tmp/python-retry-blackboard.tasks.test_workflow_real.log` |
| `tests/python/blackboard/tasks/test_workspace_lifecycle.py` | 同一提交关键字遗漏导致工作区创建失败；已修正。 | 退出 0；`tmp/python-retry-blackboard.tasks.test_workspace_lifecycle.log` |
| `tests/python/console/test_console.py` | 测试 URL 的 query 误带空格，已统一为 micro-task 标识。 | 退出 0；`tmp/python-retry-console.test_console.log` |
| `tests/python/blackboard/evaluation/test_other_native_observations.py` | 测试任务标识误带空格，已改为 micro-task-1。 | 退出 0；`tmp/python-retry-blackboard.evaluation.test_other_native_observations.log` |
| `tests/python/blackboard/tasks/test_workflow.py` | 错误文字断言中的 micro task 与实际 micro-task 不一致，已修正。 | 退出 0；`tmp/python-retry-blackboard.tasks.test_workflow.log` |
| `tests/python/blackboard/tasks/test_workflow_cancellation.py` | CLI result 的内部导入误改为 _lineage_result_view，已恢复正确的新名 _micro_task_result_view。 | 退出 0；`tmp/python-retry-blackboard.tasks.test_workflow_cancellation.log` |
| `tests/python/buddy/harnesses/test_discovery_stop_timing.py` | fixture registration missing，时序用例在并行首轮失败；未改该测试或其实现。 | 退出 0；`tmp/python-retry-buddy.harnesses.test_discovery_stop_timing.log` |
| `tests/python/buddy/roles/test_schema_worker.py` | 提示词改名后两项固定哈希未更新；核对仅词语改名后更新，schema 哈希不变。 | 退出 0；`tmp/python-retry-buddy.roles.test_schema_worker.log` |
| `tests/python/cli/test_cli_views.py` | 同一 CLI result 导入遗漏；已修正。 | 退出 0；`tmp/python-retry-cli.test_cli_views.log` |

保留相邻 Buddy 一词之前的完整复验已通过：234 个文件、3879 项，私有根已删除，日志 tmp/python-full-before-buddy-preservation.log。随后把提示词中的 Buddy 留给后续词族，仅改微任务部分；更新两项提示词哈希前的定向检查出现预期的两项哈希快照失败（tmp/python-prompt-hash.log），schema 哈希未变。最终完整 Python 检查：退出 0，234 个文件、3879 项全部通过，跳过 0，私有根已删除。日志 `tmp/python-full-final.log`。完整检查与每个通过的单独重跑均观察到私有根清理完成。

前端依赖：`npm --prefix apps/console ci` 退出 0；npm 缓存在 `tmp/npm-cache/`。Playwright Chromium headless shell 下载退出 0，下载目录 `tmp/playwright-browsers/`，日志 `tmp/playwright-install.log`。

最终源码上与浏览器检查并行的前端全量首轮退出 1：`apps/console/src/console-session.test.tsx` 的 keeps a dirty draft local… 与 pauses discovery and saving once the login lapses (401)…，以及 `apps/console/src/desktop-console.test.tsx` 的 keeps one note draft per model across rows and main pages without a lease 三项各在 5000 ms 超时；76 个文件、1088 项通过，2 个文件、3 项失败，日志 `tmp/frontend-test-final.log`。浏览器任务结束后单独重跑相同全量命令 `npm --prefix apps/console test` 退出 0：78 个文件、1091 项全部通过，日志 `tmp/frontend-test-isolated.log`。未为这三项改实现或增加等待。

最终源码的 `npm --prefix apps/console run test:browser` 退出 0：96 项通过，日志 `tmp/frontend-browser-final.log`；此仓库命令先运行既有 build，成功重建上述 JS/CSS 和 index.html。此前单独 build 同样成功。与基点比对，1094 个含中文的生产源码引号字符串原样，CSS 只改选择器名；i18n 键跟随名称、显示文字保留。

静态校验：Python 文件语法解析、内部独立只读审查、`git diff HEAD --check` 已执行；独立审查指出的三处测试替身关键字已改为 micro_task。语法解析和差异检查通过；独立审查是辅助证据，最终验收仍由 Host 对固定候选判断。

范围之外的观察：Vite 仍提示某个压缩 chunk 超过 500 kB，本次没有做拆包或其他清理。角色/操作消息中既有中文名词和控制台显示文字未顺手改写。真实 harness 探针仅改名，未执行；未安装产品、未验证安装后的日常环境，没有账户登录、凭据修改或付费模型调用。临时日志、脚本和缓存留在忽略的 tmp/ 下供复核；开始时曾误将一份检索输出放到 /tmp/no-use，已立即删除，此后临时材料均在检出中。
