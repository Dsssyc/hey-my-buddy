# Worker 权限档位：续接换档与整合说明

2026-10-13，在输入提交 `4cf89414` 的受管检出完成本次修改。本文记录源码、离线验证及需要 Host 补跑的边界；Host 提供的真实 harness 事实是声明的依据，本回合没有重新运行真实 harness、模型调用、服务、Worker、安装或故障注入。

## 声明与服务规则

新增布尔声明 `native_resume_permission_change`，放在 `native_resume` 旁边；`base.Adapter` 默认值为 `False`，四个原生 adapter 都显式声明自己的取值。它只说明已有原生会话能否采用下一回合的新权限档位，不改变各档交给原生的设置，也不增加持久化字段或能力别名。

| harness | native_resume | native_resume_permission_change | 依据 |
| --- | --- | --- | --- |
| Codex | True | True | Host 在 Codex 0.162.1 的第 2 步真实回合量到策略每回合重新给一次，换档生效。 |
| Claude Code | False | False | 当前源码每回合分配新会话，没有原生续接。 |
| DSH | True | False | Host 在 DSH 0.1.5-rc.1 量到续接保留创建时的预设；原生配置选项只有模型和推理强度，没有切换预设的开关。 |
| ZCode | True | True | 当前源码每个 Worker 续接回合使用 session/setMode 重设模式，Host 的 ZCode 0.16.9 假模型探针量过两个换档方向。 |

`blackboard/tasks/workflow.py::begin_turn` 提前取得本回合已准备好的 `execution_workspace`，用上一回合自己的 `workflow_turns.access` 与它的 `access` 比较。沿用原有的正常结束、原生会话、相同配置、版本与原生存储判断，只有档位相同或 adapter 声明可以换档时，才选 `native-session`；其余续接保留已有 `reconstructed-new-session`。新增条件没有 harness 名称，不从任务当前档位反推上一回合，不改变存储形式、schema 18 或版本 0.31.0。

已有重建原因保存在输入的 `context.resumeReason`；本次沿用该出口，因权限变化重建时写 `permission-level-changed`。版本变化的 `harness-version-changed` 和原生存储变化的既有原因仍先于权限原因；配置变化照旧重建且不新增原因，Claude Code 因没有原生续接照旧重建且不制造换档原因。

服务测试新增 `blackboard/tasks/test_permission_resume.py`，直接取四个产品 adapter 的声明，用既有离线工作流夹具经过提交、完成、继续和 claim 验证多回合行为。覆盖 read→write、write→read、三档各自不变，以及涉及 full 的升降档；验证旧回合记录仍保留旧档位、DSH 换档重建、Codex/ZCode 原生续接、Claude 每回合重建。`test_dsh_resume_selection.py` 的版本、主目录与配置变化用例同时换档，验证原有原因与优先次序。

DSH 原生测试删除 `assert_changed_permission`、read→write 和 write→full 复用会话的两条旧断言，改成 `assert_same_permission` 与三档参数化的同档续接；仍验证同一会话、session/resume、当前回合服务绑定及两回合相同的 requested 预设。已有重建回合启动当前档位的测试保留。假 DSH 的启动环境事实不能证明原生会话会采用换档后的预设，因此换档行为只在服务层断言。

## 五项整合修正

1. `blackboard/routing/test_permission_route.py` 保留四家声明 full 后的候选正例，再临时移除 ZCode 的 full 声明，将由此生成的能力写入测试画像，断言它不再进入 full 候选；`buddy/harnesses/test_run_contract.py` 临时移除 Claude 的 full 声明，验证 `require_permission` 与 `worker_permission` 拒绝；`buddy/roles/test_schema_worker.py` 临时移除 ZCode 的 full 声明，验证 Worker 请求在构造阶段拒绝。临时修改都作用于已有唯一声明 `_WORKER_PERMISSIONS`，没有另造不支持 full 的产品 adapter。
2. `blackboard/catalog/test_catalog.py` 的画像能力与显式权限入口正例改为四家都支持 read/write/full；临时移除 Claude 的 full 声明，验证画像不再声明它且显式入口拒绝。`blackboard/tasks/test_permission_levels.py` 删除已过时的“模拟未来 Claude full”能力补丁与注释。服务测试的 executor stand-in 在 `test_workflow.py`、`test_permission_levels.py`、`test_dsh_resume_selection.py` 及 `blackboard/evaluation/test_native_observations.py` 补齐新布尔声明，没有在产品代码中为这些夹具加回退读取。
3. `zcode/native_run.py::run` 对 `tool_scope=full` 且没有 `worker_permission` 的调用方错误，以 INVALID_ARGUMENT 在原生启动前拒绝；有效 full Worker 仍沿用当前原生设置。`test_run_contract.py::test_full_scope_crosses_the_run_contract_and_is_refused_before_native_launch` 恢复四家覆盖，另在 ZCode 原生测试中核对其 `_run` 没被调用。
4. `registry.py` 的 `_WORKER_PERMISSIONS` 注释改为说明每家声明该档的原生工具范围与强度，删除“两条只读 Worker 路径仍走可写执行路径”的过时描述；声明表本身不变。
5. `claude/native_run.py::_build_result` 在 Worker 已收到会话 init、请求了权限模式但该模式不可读时，把 `settings-readback` 加入共用 `NativeEvidence.unconfirmed`，保留已有 `model-catalog` 项及原生观察。可读模式不符的既有失败判断不变，模式不可读仍允许回合继续；Claude 的权限测试覆盖缺字段、不可识别值、错误类型及正常模式没有新增未证实项。

## 逐文件验证

使用 Host 指定的冻结只读解释器，检出里的 src 与 tests/python 排在前面，没有 uv 或依赖安装。每个文件单独一个 unittest 子进程，含禁跑项的文件只选该文件内可运行的类或方法；每个进程通过 checks.child_environment 清除继承的运行、Worker、凭据与虚拟环境变量，取得检出外临时目录中的私有 HOME、harness 配置、状态、运行与临时根。原生运行测试只启动离线假 CLI 与私有完成工具，不启动真实 harness、服务、Worker 或付费模型。

最终按文件去重为 139 个文件、2,459 项测试通过，unittest 自身没有跳过项；预先排除的项目见下节。ZCode inquiry 的一个独立进程日志屏障用例在同文件其他安全项之后单独追加，统计只加这一项；首轮聚焦测试与内部辅助代理的重复验证不重复计数。另补跑本次增加 executor 声明的 blackboard/evaluation/test_native_observations.py，12 项通过。git diff --check 通过，全部 22 个修改或新增文件均在授权范围内。

| 范围 | 已执行文件数 | 已通过项数 |
| --- | --- | --- |
| blackboard/tasks | 40 | 709 |
| blackboard/routing | 33 | 503 |
| blackboard/catalog | 4 | 58 |
| buddy/harnesses 顶层 | 14 | 338 |
| buddy/harnesses/dsh，含 acp | 12 | 224 |
| buddy/harnesses/zcode | 14 | 284 |
| buddy/harnesses/claude | 7 | 191 |
| buddy/roles | 14 | 140 |
| blackboard/evaluation/test_native_observations.py | 1 | 12 |
| 合计 | 139 | 2,459 |

首次运行脚本以四个独立文件进程并行验证，设定的每文件 180 秒上限使 test_host_workflow、test_workflow_preparation、test_workspace_lifecycle 没有得到完整结果，并令汇总驱动以 TimeoutExpired 结束；这不是已确认的产品断言失败。其余文件保留各自完整的 unittest OK 日志。上述三文件随后各自单独、依次以 900 秒上限重跑，61、32、49 项分别在约 316、155、314 秒通过，进程退出码均为 0，没有为时延修改产品或测试语义。最终统计采用完整日志与重跑结果；原始逐文件日志、final-summary.json 和 final-selection.json 保留在 /tmp/buddy-resume-results-jkdx5s87/，它们是本轮本地材料，本文不把这些结果表述为 Host 已验收。

## 未运行的测试

以下选择器在运行前排除；类或模块选择器包含其中全部测试及继承的测试，方法选择器只排除该方法。它们会执行真实服务、Worker、Supervisor、带 C-Two 端点的角色控制器或真实 C-Two peer，或在收尾枚举进程；完整检查与这些用例由 Host 在允许相应本机通信和进程操作的环境补跑。共享内存传输相关的两个 C-Two 类被排除，其余八个纯类的 55 项已通过；只构造本机端点 context、mock 凭据读取而不注册或连接端点的 role_live_seam 17 项也已通过。私有 session_mcp 子进程的日志屏障测试不启动服务或 Worker，已单独追加验证。

```text
blackboard.routing.test_decision.DecisionFailureTests.test_abstention_may_cite_checkout_evidence
blackboard.routing.test_decision.DecisionFailureTests.test_alternative_without_cards_or_annotations_completes_and_is_audited
blackboard.routing.test_decision.DecisionFailureTests.test_answer_shape_json_and_path_boundaries_keep_one_attempt_each
blackboard.routing.test_decision.DecisionFailureTests.test_budget_and_input_changed_are_separate_from_failures_and_abstentions
blackboard.routing.test_decision.DecisionFailureTests.test_changed_input_before_spawn_keeps_its_router_boundary_code
blackboard.routing.test_decision.DecisionFailureTests.test_in_flight_cancel_without_native_stop_evidence_is_honest
blackboard.routing.test_decision.DecisionFailureTests.test_json_string_answer_is_parsed_by_the_generic_collector
blackboard.routing.test_decision.DecisionFailureTests.test_malformed_native_output_fails_without_touching_the_table
blackboard.routing.test_decision.DecisionFailureTests.test_missing_native_result_keeps_infrastructure_failure_semantics
blackboard.routing.test_decision.DecisionFailureTests.test_native_error_envelope_fails_honestly
blackboard.routing.test_decision.DecisionFailureTests.test_out_of_candidate_is_not_adopted_and_has_its_own_health_count
blackboard.routing.test_decision.DecisionFailureTests.test_policy_facts_carry_no_task_preference_slot
blackboard.routing.test_decision.DecisionFailureTests.test_routing_health_distinguishes_failure_abstention_and_success_without_writes
blackboard.routing.test_decision.DecisionFailureTests.test_unconfirmed_shutdown_never_verifies_input_or_releases_capacity
blackboard.routing.test_decision.DecisionFailureTests.test_unknown_card_reference_is_recorded_before_adoption
blackboard.routing.test_decision.DecisionFailureTests.test_worker_deadline_is_bounded_without_inventing_native_stop_evidence
blackboard.routing.test_decision.DecisionGateTests.test_one_slot_maintenance_patch_runs_before_the_queued_selector_without_deadlock
blackboard.routing.test_decision.DecisionGateTests.test_queued_selection_cancel_is_durable_and_never_runs
blackboard.routing.test_decision.DecisionGateTests.test_writer_gate_keeps_request_frozen_until_claim
blackboard.routing.test_decision.DecisionSurfaceTests.test_selection_get_defaults_to_a_compact_host_summary
blackboard.routing.test_decision.DecisionWorkerProcessTests.test_independent_worker_records_native_logs_and_card_publication_stays_external
blackboard.routing.test_decision.SelectionRequestTests.test_fixed_fields_and_capabilities_are_rechecked_at_adoption
blackboard.routing.test_decision.SelectionRequestTests.test_new_pin_and_exclusion_cannot_adopt_an_old_frozen_candidate
blackboard.routing.test_decision.SelectionRequestTests.test_pins_and_excludes_are_enforced_in_python_before_and_after_the_call
blackboard.routing.test_decision.SelectionRequestTests.test_real_worker_publishes_a_bounded_input_and_records_evidence_references
blackboard.routing.test_decision.SelectionRequestTests.test_recommendation_never_creates_or_authorizes_a_business_task
blackboard.routing.test_readonly_tools_probe.ReadonlyToolsProbeTests.test_only_one_execution_is_possible_for_the_prepared_packet
blackboard.routing.test_sole_candidate_selection.SoleCandidateSelectionTests.test_multiple_candidates_still_create_a_router_task_and_call_it
blackboard.tasks.test_host_workflow_worker
blackboard.tasks.test_parallel_dispatch.DaemonPoolTests
blackboard.tasks.test_parallel_dispatch.ModelFamilyFairnessTests.test_decision_waits_for_total_capacity_then_completes
blackboard.tasks.test_parallel_dispatch.RetireRaceTests.test_a_retire_intent_written_during_an_attempt_never_cancels_it
blackboard.tasks.test_parallel_dispatch.RetireStartupTests
blackboard.tasks.test_parallel_dispatch.SupervisorRetirementTests
blackboard.tasks.test_workflow_routing.TestWorkflowRouting.test_oversized_goal_stops_before_selection_and_recovers_with_explicit_configuration
blackboard.tasks.test_workflow_routing.TestWorkflowRouting.test_real_decision_worker_selects_once_and_persists_owned_shutdown_evidence
blackboard.tasks.test_workflow_worker.ConsoleWorkflowRouteTests
blackboard.tasks.test_workflow_worker.RealWorkerTurnTests
blackboard.tasks.test_workspace_api.WorkspaceApiTests.test_five_named_methods_reach_the_real_ctwo_resource
buddy.harnesses.claude.test_claude_worker.ClaudeWorkerTests
buddy.harnesses.dsh.test_dsh_role_wiring.WorkerRegisteredRunTests
buddy.harnesses.test_c_two_live.NativeDeadlineTests
buddy.harnesses.test_c_two_live.SubprocessLifecycleTests
buddy.harnesses.test_inquiry_owner.InquiryOwnerPeerTests
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_a_prompt_failure_never_manufactures_attribution
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_assistance_comes_from_structured_receipt
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_blocked_native_stdin_does_not_disable_the_deadline
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_bound_native_resume_rejects_missing_or_different_native_sessions
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_cancel_and_unconfirmed_stop_are_distinct
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_child_finish_does_not_replace_or_disable_root_finish
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_confirmed_model_readback_mismatch_is_rejected_before_work
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_deadline_stops_owned_native_process_group
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_duplicate_protocol_members_fail_without_work
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_failed_turn_can_reconstruct_a_new_root_without_a_verified_session
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_missing_controller_receipt_cannot_confirm_the_separate_native_group
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_missing_native_resume_never_starts_fresh
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_native_resume_requires_same_configuration_and_reconstruction_uses_a_new_root
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_native_turn_failure_attribution_is_whitelisted_and_secret_free
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_positive_timeout_keeps_a_finite_stamped_deadline
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_protocol_failures_never_import_a_turn
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_resume_keeps_exact_session_and_reinjects_the_new_bridge
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_success_uses_native_root_receipt_and_keeps_secrets_private
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_unrecognized_model_readback_continues_and_retains_unknown_facts
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_zero_timeout_executes_unlimited_without_an_immediate_deadline
buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_zero_timeout_still_honors_explicit_cancellation
buddy.harnesses.zcode.test_zcode_checkpoint
buddy.harnesses.zcode.test_zcode_inquiry.LiveActivityTests.test_running_same_phase_native_events_refresh_the_published_observation
buddy.harnesses.zcode.test_zcode_inquiry.LiveActivityTests.test_the_runner_publishes_real_bound_metadata_only_live_activity
buddy.harnesses.zcode.test_zcode_inquiry.ZcodeInquiryIntegrationTests
buddy.harnesses.zcode.test_zcode_tool_refusals
buddy.roles.test_checkpoint_shutdown_guard
buddy.roles.test_registered_run_wiring.FastRegisteredRunTests
buddy.roles.test_registered_run_wiring.WorkerRegisteredRunTests
```

未运行 tests/python/install、完整检查、真实 harness 回合、探针、故障注入、安装、前端检查或 Windows/Linux 原生权限验证；没有为了未运行项绕过沙盒、请求人工批准或改测试的能力含义。Host 还需在真实 DSH 上复核两个换档方向确实创建新会话，并确认整合后的 Codex/ZCode 续接和 Claude 新会话行为；这些事实不由本轮离线模拟证明。

## 续接第 2 回合：评价断言与 ZCode 用量假程序

2026-10-13，Host 续接输入确认第 1 回合交付已读过并在整合分支临时合并验证，允许在原范围内修正三步合入后另外两处遗留测试；本回合以封存 8149c7ec 为输入，只修改测试、假程序与本说明，产品源码与第 1 回合的声明、续接规则及原生设置完全相同。

`blackboard/evaluation/test_evaluation.py::EvaluationPublishTests.test_catalog_permissions_persist_through_user_publication` 将 DSH 不带 full 的旧断言替换成显式的 read/write/full 三档集合，分别检查目录产生的画像与用户发布后的画像，保留“能力字符串经过用户发布不丢失”的测试含义。

`buddy/harnesses/zcode/fixtures/mock_zcode_usage.py` 新增 session/setMode，行为与现有 mock_zcode.py 对齐：创建 plan 时底层权限模式为 build，创建 yolo 时为 yolo；setMode plan 只开启独立的 plan_active，不把底层模式回显成 plan，setMode yolo 改变底层模式。快照的 session.mode 报 build，settings.mode.current 报底层权限模式。每次 session/send 在私有原生根保存 sessionId、selection 与 permissionMode，续接读回同一会话和底层模式并清除 plan_active，供本回合再 setMode；没有加入旧字段读法或回退。原有用量消息分页、游标、增量及拒绝记录的语义没有修改。

`buddy/harnesses/zcode/test_native_run.py` 新增离线用量假程序回归测试，经过产品现有原生 run 路径与签名结束工具，直接覆盖 read→write、write→read、read→read：同组两个回合共享原生根，各组独立；断言原生会话身份相同、发送本回合 session/setMode、底层回读分别为 build→yolo、yolo→yolo、build→build，以及新回合的 inputTokens、nativeRecords 和 completeness 与已脱敏用量夹具一致。它不启动真实角色控制器或 C-Two 端点，也不证明真实 ZCode 的工具限制。新增测试初次将三个独立情形放在同一原生根时，假程序基于该根生成的固定身份导致后两组初始回合被拒；改为每组独立的私有原生根后通过，没有为此改产品的身份判断。

按文件独立进程运行本次指定两目录的可运行范围，使用同一冻结只读解释器及 checks.child_environment 的私有 HOME、harness 配置和状态、运行、临时目录；最终 20 个文件、413 项全部通过，每个文件退出码均为 0，unittest 没有跳过项。先前的聚焦测试不重复计数。git diff --check 通过，产品 src 相对输入封存的差异为空；原始日志与逐文件退出码保留在 /tmp/buddy-resume-followup-results-98_munp0/summary.json 及同目录的各文件日志。

| 范围 | 已执行文件数 | 已通过项数 |
| --- | --- | --- |
| blackboard/evaluation | 6 | 128 |
| buddy/harnesses/zcode | 14 | 285 |
| 合计 | 20 | 413 |

ZCode 目录中会启动真实角色控制器及 C-Two 端点的选择器继续按第 1 回合“未运行的测试”清单排除，两个全为该类用例的文件 test_zcode_checkpoint.py、test_zcode_tool_refusals.py 没有运行。评价目录的 test_other_native_observations.py 只运行 ClaudeNativeRecordTests 与 ZcodeNativeRecordTests；下列两个 adapter 观察类会调用真实角色控制器 start，因此没有执行，包含 Host 指出的目标续接用例。该目标的假程序方法与新回合用量边界已由上述直接 run 的离线测试验证，完整控制器路径仍由 Host 补跑，不把两者当作同一验证事实。

```text
blackboard.evaluation.test_other_native_observations.ClaudeAdapterObservationTests.test_a_completed_turn_publishes_the_execution_summary_and_quota
blackboard.evaluation.test_other_native_observations.ClaudeAdapterObservationTests.test_a_missing_result_usage_falls_back_to_the_bound_assistant_records
blackboard.evaluation.test_other_native_observations.ClaudeAdapterObservationTests.test_a_quota_rejection_retains_usage_quota_and_the_last_root_message
blackboard.evaluation.test_other_native_observations.ClaudeAdapterObservationTests.test_no_native_usage_reports_unknown_not_zero
blackboard.evaluation.test_other_native_observations.ClaudeAdapterObservationTests.test_an_oversized_assistant_text_is_bounded_with_its_full_provenance
blackboard.evaluation.test_other_native_observations.ZcodeAdapterObservationTests.test_a_turn_publishes_the_bound_root_message_tokens
blackboard.evaluation.test_other_native_observations.ZcodeAdapterObservationTests.test_a_resumed_session_counts_only_the_new_turn
blackboard.evaluation.test_other_native_observations.ZcodeAdapterObservationTests.test_a_delta_only_turn_uses_the_partial_stream_fallback
blackboard.evaluation.test_other_native_observations.ZcodeAdapterObservationTests.test_a_lost_cursor_is_never_counted_as_this_attempt
blackboard.evaluation.test_other_native_observations.ZcodeAdapterObservationTests.test_a_failed_quota_turn_keeps_its_native_code_and_last_message
blackboard.evaluation.test_other_native_observations.ZcodeAdapterObservationTests.test_a_stalled_optional_read_cannot_change_the_turn
blackboard.evaluation.test_other_native_observations.ZcodeAdapterObservationTests.test_no_native_usage_reports_unknown_not_zero
```

本回合没有执行 tests/python/install、启动器、真实服务或 Worker、真实 harness、模型调用、完整检查、安装或故障注入；未改分支、标签、存储形式、schema 或版本。
