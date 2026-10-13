# ADR-021 L14 harness 改名说明

基点为 `fa1706e8`；版本保持 0.34.1，schema 保持 19。本说明记录这个微任务的源码改动与私有测试结果，交 Host 验收；没有安装到日常环境，没有调用真实模型，没有访问日常服务、Worker、看板、用户自己的 harness 配置或凭据。

完整 Python 首轮在 234 个文件中有 11 个失败文件；逐个修正后，用指定解释器和检查入口对每个失败文件单独重跑一次，11 个均退出 0，私有根均已移除。最终完整 Python 检查通过（234 个文件、3870 项，跳过 0 项），退出 0，私有测试根已移除；日志为 tmp/python-full-verified.log。前端最终测试通过（78 个文件、1088 项），浏览器检查通过（96 项），构建通过。

## 名字与计数

下表按基点与最终源码的改动片段逐个词法替换统计，包含源码、测试、探针和注释，排除重建的压缩资产以免把同一改动重复计数；指连接件的同名出现不计入改名。普通小写 adapter 的统计也包含参数名和断言标签；字段路径 discovery.adapter 与 toolEvidence.binding.adapter 的改名包含在 adapter → harness 中。

| 旧名 | 新名 | 处数 |
| --- | --- | ---: |
| `ADAPTER` | `HARNESS` | 19 |
| `ADAPTERS` | `HARNESSES` | 7 |
| `ADAPTER_UNAVAILABLE` | `HARNESS_UNAVAILABLE` | 36 |
| `Adapters` | `Harnesses` | 1 |
| `CODING_ADAPTERS` | `CODING_HARNESSES` | 12 |
| `HARNESS_ADAPTERS` | `HARNESS_ORDER` | 3 |
| `UNSUPPORTED_ADAPTER` | `UNSUPPORTED_HARNESS` | 5 |
| `_adapter` | `_harness` | 2 |
| `adapter` | `harness` | 1718 |
| `adapter-unavailable` | `harness-unavailable` | 25 |
| `adapters` | `harnesses` | 11 |
| `appliedAdapters` | `appliedHarnesses` | 4 |
| `model_adapter` | `model_harness` | 23 |
| `native_adapter` | `native_harness` | 2 |
| `per-adapter` | `per-harness` | 1 |
| `private_adapter` | `private_harness` | 2 |
| `staleAdapters` | `staleHarnesses` | 5 |
| `test_adapter_identity_is_required_without_dsh_default` | `test_harness_identity_is_required_without_dsh_default` | 1 |
| `test_explicit_permission_gate_uses_adapter_declaration` | `test_explicit_permission_gate_uses_harness_declaration` | 1 |
| `test_foreign_adapter_return_after_stop_is_rejected` | `test_foreign_harness_return_after_stop_is_rejected` | 1 |
| `test_model_service_is_the_exact_adapter_provider_model_tuple` | `test_model_service_is_the_exact_harness_provider_model_tuple` | 1 |
| `test_native_fixture_absence_is_an_honest_adapter_unavailable_outcome` | `test_native_fixture_absence_is_an_honest_harness_unavailable_outcome` | 1 |

清单之外发现并改了 14 个不同拼写：`ADAPTER` → `HARNESS`（19 处）；`ADAPTERS` → `HARNESSES`（7 处）；`Adapters` → `Harnesses`（1 处）；`CODING_ADAPTERS` → `CODING_HARNESSES`（12 处）；`HARNESS_ADAPTERS` → `HARNESS_ORDER`（3 处）；`_adapter` → `_harness`（2 处）；`native_adapter` → `native_harness`（2 处）；`per-adapter` → `per-harness`（1 处）；`private_adapter` → `private_harness`（2 处）；`test_adapter_identity_is_required_without_dsh_default` → `test_harness_identity_is_required_without_dsh_default`（1 处）；`test_explicit_permission_gate_uses_adapter_declaration` → `test_explicit_permission_gate_uses_harness_declaration`（1 处）；`test_foreign_adapter_return_after_stop_is_rejected` → `test_foreign_harness_return_after_stop_is_rejected`（1 处）；`test_model_service_is_the_exact_adapter_provider_model_tuple` → `test_model_service_is_the_exact_harness_provider_model_tuple`（1 处）；`test_native_fixture_absence_is_an_honest_adapter_unavailable_outcome` → `test_native_fixture_absence_is_an_honest_harness_unavailable_outcome`（1 处）。这些名字都是原生 harness 标识、原生身份的局部名、测试名或其注释；`HARNESS_ADAPTERS` 本身表示显示顺序，改成 `HARNESS_ORDER`。

清单里已不存在的项跳过：旧 `evaluation_profiles_adapter_idx` 已经由此前的微任务改为 `buddies_harness_idx`，这次仅把其索引列改为 harness；`evaluation-prepare` 与维护检查点的 `adapter:<harness>` scope 已被删除；旧私有迁移模块及 `adapter-unproven` 已被删除。当前需要保留的 `adapter:` 只有连接件认领能力令牌，没有留下一个表示原生 harness 的 adapter: 前缀。

## 存储与边界

新看板的 buddies、catalog_current、catalog_pending、model_service_notes、model_service_concurrency、model_facts、harness_account_reads、harness_health、harness_quota_observations、quota_current、router_dispatches 使用 harness 列；attempts 冻结的 model_adapter 改为 model_harness；tasks、attempts、workers 的执行连接件 adapter 列保留。

提交与继续的原生身份使用 harness/provider/model/effort；不可通过原生 adapter 字段继续提交 buddy，也没有保留旧命令 adapters 的别名。command/external 的连接件参数、Worker 注册和认领仍使用 adapter。原始原生规格只保留 harness 身份；Worker 执行投影从任务的连接件列给出 adapter，原生运行与命令/外部任务分别有其字段语义，未增加双读或兼容路径。目标摘要的原生字段改为 harness；连接件提交事件仍叫 adapter，并由所选 harness 或显式连接件给出值。

升级映射从固定 schema 18 的 adapter 列读取，向当前 harness 列写入；旧健康记录 JSON 的顶层 adapter 在升级边界改成 harness，原生不可用错误码随之改名。新板与设置读写均不接受新旧两种身份键。固定升级样板和 packaging/generate-upgrade-fixture.py 原样保留，归档中的旧形式不动；已有升级样板测试增加了对升级后健康 JSON 不再含 adapter 的检查。

为保持帮助行为，帮助提取器增加了对 `{key: value for key, value in params.items() ...}` 参数限制副本的识别；改名时需要把原生 harness 交给连接件规格标准化，帮助必须仍能从同一验证器读取 task、cwd、预算和模型等字段的类型、默认值与边界。没有静态复制这些帮助值；现有帮助来源与边界回归测试重跑通过。

顺带完成三处指定修正：workflow.py 的两句 model buddy 改为 buddy；macro_task_console_preview.py 的 Router 设置错误字段恢复 configurationError；test_no_tool_dsh.py 的 PYTHONPATH 直接使用该文件的 parents[3]，指向检出内 tests/python。

## 文件

本次没有将连接件模块 `adapter.py` 或连接件测试文件改称 harness；它们指连接件实现。打包资产将 `src/hey_my_buddy/console/assets/assets/index-C157hAkK.js` 替换为 `index-Bo1EQ740.js`，并更新 assets/index.html 的引用；CSS 与图标资产没有字节改动。前端只改标识符和原生字段，没有修改显示文字或样式；树中没有属于这一族的 i18n 键需要另改。

除重建的资产外，修改了以下 236 个文件：

- `apps/console/src/App.test.tsx`
- `apps/console/src/DecisionDetails.tsx`
- `apps/console/src/RoutingPanel.tsx`
- `apps/console/src/WorkflowPanel.test.tsx`
- `apps/console/src/WorkflowPanel.tsx`
- `apps/console/src/api.test.ts`
- `apps/console/src/api.ts`
- `apps/console/src/buddies/CatalogView.tsx`
- `apps/console/src/buddies/HarnessDetails.tsx`
- `apps/console/src/buddies/HarnessHeader.tsx`
- `apps/console/src/buddies/HarnessLogo.tsx`
- `apps/console/src/buddies/HarnessPathDialog.tsx`
- `apps/console/src/buddies/ModelDrawer.tsx`
- `apps/console/src/buddies/attention.test.ts`
- `apps/console/src/buddies/attention.ts`
- `apps/console/src/buddies/catalogue-actions.test.tsx`
- `apps/console/src/buddies/catalogue-gestures.test.tsx`
- `apps/console/src/buddies/catalogue-view.test.tsx`
- `apps/console/src/buddies/english.test.tsx`
- `apps/console/src/buddies/harness-quota.test.tsx`
- `apps/console/src/buddies/harness-retry.ts`
- `apps/console/src/buddies/harness-review.test.tsx`
- `apps/console/src/buddies/harness-wire.test.ts`
- `apps/console/src/buddies/harness.fixtures.ts`
- `apps/console/src/buddies/harness.test.ts`
- `apps/console/src/buddies/harness.ts`
- `apps/console/src/buddies/models.test.ts`
- `apps/console/src/buddies/models.ts`
- `apps/console/src/buddies/navigation-page.test.tsx`
- `apps/console/src/buddies/roles-view.test.tsx`
- `apps/console/src/buddies/roles.test.ts`
- `apps/console/src/buddies/roles.testing.ts`
- `apps/console/src/buddies/roles.ts`
- `apps/console/src/buddies/use-harness-actions.ts`
- `apps/console/src/buddy-display.ts`
- `apps/console/src/buddy-name.test.tsx`
- `apps/console/src/buddy-settings.test.tsx`
- `apps/console/src/catalog-status.test.tsx`
- `apps/console/src/console-data.test.ts`
- `apps/console/src/console-data.ts`
- `apps/console/src/console-session.test.tsx`
- `apps/console/src/desktop-console.test.tsx`
- `apps/console/src/draft.test.ts`
- `apps/console/src/draft.ts`
- `apps/console/src/edit-mode.test.tsx`
- `apps/console/src/harness-account.ts`
- `apps/console/src/host-workflow-panel.test.tsx`
- `apps/console/src/macro-task-detail.test.tsx`
- `apps/console/src/macro-task-display-016.test.ts`
- `apps/console/src/macro-task-display.ts`
- `apps/console/src/macro-task-fixtures.ts`
- `apps/console/src/macro-task-timeline.test.tsx`
- `apps/console/src/macro-task-types.ts`
- `apps/console/src/model-service-concurrency.test.tsx`
- `apps/console/src/native-session.test.tsx`
- `apps/console/src/native-session.tsx`
- `apps/console/src/policy.test.ts`
- `apps/console/src/policy.ts`
- `apps/console/src/preview-contract.test.ts`
- `apps/console/src/routing-console.test.tsx`
- `apps/console/src/routing-display.ts`
- `apps/console/src/task-state.ts`
- `apps/console/src/timeline-layers.test.tsx`
- `apps/console/src/types.ts`
- `apps/console/src/unavailable-buddies.test.tsx`
- `apps/console/src/use-buddy-history.test.ts`
- `apps/console/src/use-buddy-history.ts`
- `apps/console/src/workflow-types.ts`
- `src/hey_my_buddy/blackboard/catalog/accounts.py`
- `src/hey_my_buddy/blackboard/catalog/catalog.py`
- `src/hey_my_buddy/blackboard/catalog/catalog_store.py`
- `src/hey_my_buddy/blackboard/evaluation/evaluation.py`
- `src/hey_my_buddy/blackboard/evaluation/model_facts.py`
- `src/hey_my_buddy/blackboard/evaluation/native_observations.py`
- `src/hey_my_buddy/blackboard/evaluation/user_policy.py`
- `src/hey_my_buddy/blackboard/routing/decision.py`
- `src/hey_my_buddy/blackboard/routing/quota_routing.py`
- `src/hey_my_buddy/blackboard/routing/router.py`
- `src/hey_my_buddy/blackboard/routing/router_boundary_data.py`
- `src/hey_my_buddy/blackboard/routing/router_sequence.py`
- `src/hey_my_buddy/blackboard/service/harness_health.py`
- `src/hey_my_buddy/blackboard/service/service.py`
- `src/hey_my_buddy/blackboard/store/db.py`
- `src/hey_my_buddy/blackboard/store/migrations.py`
- `src/hey_my_buddy/blackboard/store/store.py`
- `src/hey_my_buddy/blackboard/tasks/delegation.py`
- `src/hey_my_buddy/blackboard/tasks/macro_tasks.py`
- `src/hey_my_buddy/blackboard/tasks/scheduling.py`
- `src/hey_my_buddy/blackboard/tasks/worker_sessions.py`
- `src/hey_my_buddy/blackboard/tasks/workflow.py`
- `src/hey_my_buddy/buddy/harnesses/claude/adapter.py`
- `src/hey_my_buddy/buddy/harnesses/claude/config.py`
- `src/hey_my_buddy/buddy/harnesses/claude/native_run.py`
- `src/hey_my_buddy/buddy/harnesses/claude/protocol.py`
- `src/hey_my_buddy/buddy/harnesses/claude/tool_evidence.py`
- `src/hey_my_buddy/buddy/harnesses/codex/native_run.py`
- `src/hey_my_buddy/buddy/harnesses/codex/tool_evidence.py`
- `src/hey_my_buddy/buddy/harnesses/discovery.py`
- `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py`
- `src/hey_my_buddy/buddy/harnesses/dsh/protocol.py`
- `src/hey_my_buddy/buddy/harnesses/registry.py`
- `src/hey_my_buddy/buddy/harnesses/run_contract.py`
- `src/hey_my_buddy/buddy/harnesses/runtime_selection.py`
- `src/hey_my_buddy/buddy/harnesses/zcode/config.py`
- `src/hey_my_buddy/buddy/harnesses/zcode/native_run.py`
- `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py`
- `src/hey_my_buddy/buddy/harnesses/zcode/tool_evidence.py`
- `src/hey_my_buddy/buddy/roles/router.py`
- `src/hey_my_buddy/buddy/roles/run_execution.py`
- `src/hey_my_buddy/buddy/roles/structured_call.py`
- `src/hey_my_buddy/buddy/roles/turn_io.py`
- `src/hey_my_buddy/buddy/runtime/worker.py`
- `src/hey_my_buddy/cli/blocking.py`
- `src/hey_my_buddy/cli/cli_help.py`
- `src/hey_my_buddy/cli/cli_views.py`
- `src/hey_my_buddy/cli/main.py`
- `src/hey_my_buddy/console/server.py`
- `src/hey_my_buddy/protocol/billing.py`
- `src/hey_my_buddy/protocol/schemas.py`
- `src/hey_my_buddy/protocol/tool_evidence.py`
- `src/hey_my_buddy/protocol/transport.py`
- `tests/probes/console_routing_check.mjs`
- `tests/probes/dsh_native_resume_smoke.py`
- `tests/probes/macro_task_console_preview.py`
- `tests/probes/router_fast.py`
- `tests/probes/router_readonly.py`
- `tests/probes/router_readonly_tools.py`
- `tests/probes/zcode_native.py`
- `tests/python/blackboard/catalog/test_catalog.py`
- `tests/python/blackboard/catalog/test_catalog_observations.py`
- `tests/python/blackboard/catalog/test_catalog_trust.py`
- `tests/python/blackboard/catalog/test_native_discovery_stop.py`
- `tests/python/blackboard/evaluation/test_evaluation.py`
- `tests/python/blackboard/evaluation/test_model_facts.py`
- `tests/python/blackboard/evaluation/test_native_observations.py`
- `tests/python/blackboard/evaluation/test_user_policy.py`
- `tests/python/blackboard/routing/fixtures/router_tool_receipt.py`
- `tests/python/blackboard/routing/test_catalog_trust.py`
- `tests/python/blackboard/routing/test_current_router.py`
- `tests/python/blackboard/routing/test_decision.py`
- `tests/python/blackboard/routing/test_fast_router_probe.py`
- `tests/python/blackboard/routing/test_nonretryable_quota.py`
- `tests/python/blackboard/routing/test_permission_route.py`
- `tests/python/blackboard/routing/test_quota_retry.py`
- `tests/python/blackboard/routing/test_read_only_eligibility.py`
- `tests/python/blackboard/routing/test_readonly_tools_probe.py`
- `tests/python/blackboard/routing/test_retired_review_verifier.py`
- `tests/python/blackboard/routing/test_router_boundary.py`
- `tests/python/blackboard/routing/test_router_boundary_integration.py`
- `tests/python/blackboard/routing/test_router_dispatch.py`
- `tests/python/blackboard/routing/test_router_failover_integration.py`
- `tests/python/blackboard/routing/test_router_list_configuration.py`
- `tests/python/blackboard/routing/test_router_list_health.py`
- `tests/python/blackboard/routing/test_router_probe.py`
- `tests/python/blackboard/routing/test_router_sequence.py`
- `tests/python/blackboard/routing/test_router_tool_evidence.py`
- `tests/python/blackboard/routing/test_routing_preferences.py`
- `tests/python/blackboard/routing/test_selection_policy.py`
- `tests/python/blackboard/routing/test_single_router_claims.py`
- `tests/python/blackboard/routing/test_sole_candidate_selection.py`
- `tests/python/blackboard/service/fixtures/daemon_with_catalog.py`
- `tests/python/blackboard/service/test_harness_health.py`
- `tests/python/blackboard/service/test_harness_service.py`
- `tests/python/blackboard/service/test_harness_startup.py`
- `tests/python/blackboard/service/test_service.py`
- `tests/python/blackboard/store/test_blackboard.py`
- `tests/python/blackboard/store/test_store.py`
- `tests/python/blackboard/store/test_upgrade_fixture.py`
- `tests/python/blackboard/tasks/test_catalog_trust.py`
- `tests/python/blackboard/tasks/test_dsh_resume_selection.py`
- `tests/python/blackboard/tasks/test_host_workflow_worker.py`
- `tests/python/blackboard/tasks/test_inquiry.py`
- `tests/python/blackboard/tasks/test_macro_tasks.py`
- `tests/python/blackboard/tasks/test_model_service_concurrency.py`
- `tests/python/blackboard/tasks/test_parallel_dispatch.py`
- `tests/python/blackboard/tasks/test_permission_levels.py`
- `tests/python/blackboard/tasks/test_permission_resume.py`
- `tests/python/blackboard/tasks/test_scheduling.py`
- `tests/python/blackboard/tasks/test_worker_claim_stash.py`
- `tests/python/blackboard/tasks/test_workflow.py`
- `tests/python/blackboard/tasks/test_workflow_real.py`
- `tests/python/blackboard/tasks/test_workflow_routing.py`
- `tests/python/blackboard/tasks/test_workflow_stash.py`
- `tests/python/blackboard/tasks/test_workflow_worker.py`
- `tests/python/blackboard/tasks/test_workspace_stash.py`
- `tests/python/buddy/harnesses/claude/test_claude.py`
- `tests/python/buddy/harnesses/claude/test_claude_tool_evidence.py`
- `tests/python/buddy/harnesses/claude/test_claude_worker.py`
- `tests/python/buddy/harnesses/claude/test_native_run.py`
- `tests/python/buddy/harnesses/codex/test_batch4_facts.py`
- `tests/python/buddy/harnesses/codex/test_codex_continuation.py`
- `tests/python/buddy/harnesses/codex/test_codex_tool_evidence.py`
- `tests/python/buddy/harnesses/codex/test_native_run.py`
- `tests/python/buddy/harnesses/codex/test_no_tool_codex.py`
- `tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py`
- `tests/python/buddy/harnesses/dsh/test_dsh_session_storage.py`
- `tests/python/buddy/harnesses/dsh/test_dsh_tool_evidence.py`
- `tests/python/buddy/harnesses/dsh/test_native_run.py`
- `tests/python/buddy/harnesses/dsh/test_no_tool_dsh.py`
- `tests/python/buddy/harnesses/dsh/test_refusal_facts.py`
- `tests/python/buddy/harnesses/dsh/test_worker_permissions.py`
- `tests/python/buddy/harnesses/fixtures/run_contract/codex-payloads.json`
- `tests/python/buddy/harnesses/test_harness_discovery.py`
- `tests/python/buddy/harnesses/test_private_adapter_invariants.py`
- `tests/python/buddy/harnesses/test_run_contract.py`
- `tests/python/buddy/harnesses/test_runtime_version.py`
- `tests/python/buddy/harnesses/zcode/test_native_config.py`
- `tests/python/buddy/harnesses/zcode/test_native_run.py`
- `tests/python/buddy/harnesses/zcode/test_zcode.py`
- `tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py`
- `tests/python/buddy/harnesses/zcode/test_zcode_native.py`
- `tests/python/buddy/harnesses/zcode/test_zcode_protocol.py`
- `tests/python/buddy/harnesses/zcode/test_zcode_tool_evidence.py`
- `tests/python/buddy/harnesses/zcode/test_zcode_tool_projection_order.py`
- `tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py`
- `tests/python/buddy/roles/test_outcome_truncation.py`
- `tests/python/buddy/roles/test_role_controller.py`
- `tests/python/buddy/roles/test_run_controller_discovery.py`
- `tests/python/buddy/roles/test_run_execution.py`
- `tests/python/buddy/runtime/test_worker_invariants.py`
- `tests/python/buddy/runtime/test_worker_sessions.py`
- `tests/python/cli/test_blocking.py`
- `tests/python/cli/test_cli.py`
- `tests/python/cli/test_cli_views.py`
- `tests/python/cli/test_host_cli.py`
- `tests/python/cli/test_support_cleanup.py`
- `tests/python/console/test_console.py`
- `tests/python/console/test_console_reads_http.py`
- `tests/python/fixtures/console_macro_task_board.py`
- `tests/python/install/test_upgrade_migration.py`
- `tests/python/protocol/fixtures/ctwo_controller.py`
- `tests/python/protocol/fixtures/mock_readonly.py`
- `tests/python/protocol/fixtures/native_turn.py`
- `tests/python/protocol/test_billing_quota.py`
- `tests/python/protocol/test_tool_evidence.py`
- `tests/python/support.py`

## 检查记录

所有 Python 测试均通过 `PYTHONPATH=src <指定的既有虚拟环境>/bin/python -m hey_my_buddy.cli.checks` 执行；没有修改解释器环境，没有指定 --jobs 或 BUDDY_CHECKS_JOBS，默认 4 个检查子进程。启动检查时清除了指定的继承状态、运行时、Worker、凭据与虚拟环境变量；临时测试根由检查器放在系统临时目录内并回收。

最初的局部检查：catalog/test_catalog.py 与 store/test_upgrade_fixture.py 通过；tasks/test_workflow.py 首次有 51 个错误、5 个失败，原因是 _spec_fields 投影漏带 harness。修正该投影后第二次有 54 项通过、1 项错误，原因是原生命令合成记录误要求命令任务存在 harness；修正夹具后，它在完整首轮及之后的单独重跑中通过。原始日志保存在忽略的 tmp/focused-first.log 与 tmp/workflow-second.log。

完整首轮日志为 `tmp/python-full-first.log`：234 个文件中 223 个文件通过，成功文件运行 3691 项，11 个失败文件如下；没有把这些改名漏项归因于机器时序。单独重跑日志为 `tmp/rerun-<模块>.log`，结果集中记录在 tmp/rerun-summary.json，以下文件均单独退出 0。

| 首轮失败文件 | 原因 | 单独重跑 |
| --- | --- | --- |
| `tests/python/blackboard/evaluation/test_native_observations.py` | failure_view 的旧 adapter 关键字参数。 | 通过，退出 0 |
| `tests/python/blackboard/routing/test_quota_retry.py` | 原生 harness 错误码的旧断言。 | 通过，退出 0 |
| `tests/python/blackboard/service/test_harness_startup.py` | Worker 有效执行规格的连接件字段误改为仅有 harness。 | 通过，退出 0 |
| `tests/python/blackboard/tasks/test_catalog_trust.py` | 原生错误码与 harnesses 诊断命令的旧断言。 | 通过，退出 0 |
| `tests/python/blackboard/tasks/test_workflow_routing.py` | 普通 execution-submit 路径的连接件参数被误改为 harness。 | 通过，退出 0 |
| `tests/python/buddy/harnesses/dsh/test_dsh_session_storage.py` | normalize_spec 的连接件参数被误改为 harness。 | 通过，退出 0 |
| `tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py` | 直接准备回合的夹具未同时给出连接件 adapter 与原生 harness。 | 通过，退出 0 |
| `tests/python/buddy/roles/test_dsh_resume_storage.py` | 私有目录回收候选的连接件字段误改为 harness。 | 通过，退出 0 |
| `tests/python/cli/test_host_cli.py` | 帮助提取器未追踪新增的参数限制副本，丢了公共字段的类型、默认值与边界。 | 通过，退出 0 |
| `tests/python/protocol/test_tool_evidence.py` | 原生工具分类错误码的旧断言。 | 通过，退出 0 |
| `tests/python/test_private_directories.py` | 私有目录分区的连接件字段误改为 harness。 | 通过，退出 0 |

前端用 Node 24.21.0 执行 npm --prefix apps/console ci，安装通过；npm --prefix apps/console test 最终 78 个文件、1088 项通过，TypeScript 检查通过。前端首轮只有 preview-contract.test.ts 的夹具失败，是同一行里执行记录连接件 entry.adapter 被误改，恢复后该文件 3 项与最终前端全量均通过。npm 缓存放在检出内 tmp/npm-cache，浏览器下载放在 tmp/playwright-browsers。

构建由仓库已有 npm --prefix apps/console run test:browser 命令中的 build 执行，tsc --noEmit 与 Vite 构建通过，浏览器检查 96 项全部通过（21.5 秒），使用合成预览；原始日志为 tmp/browser-first.log。构建仍报告单一 JS 块超过 500 kB 的大小提示，未为此扩大改动范围。未额外运行真实 harness 探针、账户登录、日常服务操作或安装。

## 未改的 adapter 逐处清单

按大小写不敏感的 adapter 子串，在 src、tests/python、tests/probes、apps/console、packaging 的可交付文件中共留下 1476 处；包括重建资产 2 处与固定基点生成器 16 处，排除受保护的顶层 tests/fixtures 和忽略的依赖、缓存、日志。下表列出每一处所在的文件、行、列和保留原因；同一行同一原因的多个出现合在一行列出，各列号均计入处数。没有无法判断而留待命名决定的出现。

| 位置（行:列） | 留下的名字 | 原因 |
| --- | --- | --- |
| `apps/console/src/TaskDetails.tsx:122:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `apps/console/src/TaskDetails.tsx:128:19` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `apps/console/src/WorkflowPanel.test.tsx:95:33` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `apps/console/src/macro-task-timeline-layout.ts:23:61` | `adapter` | 像素单位转换，不指 harness。 |
| `apps/console/src/macro-task-timeline-layout.ts:243:4` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `apps/console/src/native-session.test.tsx:13:48` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `apps/console/src/policy.test.ts:135:27,59` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `apps/console/src/policy.test.ts:137:8` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `apps/console/src/policy.test.ts:141:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `apps/console/src/policy.ts:46:16,47` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `apps/console/src/policy.ts:47:4` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `packaging/generate-upgrade-fixture.py:115:45,65` | `adapter`、`CodexAdapter` | 固定的 0.34.1 升级生成器，保留旧板的名字。 |
| `packaging/generate-upgrade-fixture.py:126:54` | `adapter` | 固定的 0.34.1 升级生成器，保留旧板的名字。 |
| `packaging/generate-upgrade-fixture.py:127:32` | `adapter` | 固定的 0.34.1 升级生成器，保留旧板的名字。 |
| `packaging/generate-upgrade-fixture.py:164:85` | `adapter` | 固定的 0.34.1 升级生成器，保留旧板的名字。 |
| `packaging/generate-upgrade-fixture.py:166:14` | `adapter` | 固定的 0.34.1 升级生成器，保留旧板的名字。 |
| `packaging/generate-upgrade-fixture.py:167:47` | `CodexAdapter` | 固定的 0.34.1 升级生成器，保留旧板的名字。 |
| `packaging/generate-upgrade-fixture.py:189:31` | `adapter` | 固定的 0.34.1 升级生成器，保留旧板的名字。 |
| `packaging/generate-upgrade-fixture.py:193:44` | `adapter` | 固定的 0.34.1 升级生成器，保留旧板的名字。 |
| `packaging/generate-upgrade-fixture.py:195:37` | `adapter` | 固定的 0.34.1 升级生成器，保留旧板的名字。 |
| `packaging/generate-upgrade-fixture.py:199:44` | `adapter` | 固定的 0.34.1 升级生成器，保留旧板的名字。 |
| `packaging/generate-upgrade-fixture.py:207:18` | `adapter` | 固定的 0.34.1 升级生成器，保留旧板的名字。 |
| `packaging/generate-upgrade-fixture.py:236:39` | `adapter` | 固定的 0.34.1 升级生成器，保留旧板的名字。 |
| `packaging/generate-upgrade-fixture.py:255:65` | `adapter` | 固定的 0.34.1 升级生成器，保留旧板的名字。 |
| `packaging/generate-upgrade-fixture.py:279:82` | `adapter` | 固定的 0.34.1 升级生成器，保留旧板的名字。 |
| `packaging/generate-upgrade-fixture.py:281:44` | `adapter` | 固定的 0.34.1 升级生成器，保留旧板的名字。 |
| `src/hey_my_buddy/blackboard/catalog/catalog.py:382:45` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/catalog/catalog.py:383:83` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/catalog/catalog.py:390:45` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/catalog/catalog.py:392:27` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/catalog/catalog.py:454:45` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/catalog/catalog.py:455:16` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/catalog/catalog.py:577:49` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/catalog/catalog.py:578:20` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/evaluation/evaluation.py:122:9,28,67` | `adapter_available`、`adapter_reason`、`_adapter_available` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/evaluation/evaluation.py:124:31` | `adapter_available` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/evaluation/evaluation.py:125:22,37` | `decisionAdapter`、`adapter_available` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/evaluation/evaluation.py:126:22,38` | `decisionAdapterReason`、`adapter_reason` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/evaluation/evaluation.py:137:49` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/evaluation/evaluation.py:138:18` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/evaluation/native_observations.py:51:52` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/routing/decision.py:25:10` | `DECISION_ADAPTER` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/routing/decision.py:133:10` | `_adapter_available` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/routing/decision.py:134:51` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/routing/decision.py:140:28` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/routing/decision.py:680:14,33` | `adapter`、`DECISION_ADAPTER` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/routing/decision.py:681:61` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/routing/decision.py:687:47` | `DECISION_ADAPTER` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/routing/decision.py:694:36` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/routing/decision.py:705:26` | `DECISION_ADAPTER` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/routing/decision.py:724:18,37` | `adapter`、`DECISION_ADAPTER` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/routing/decision.py:1880:22` | `DECISION_ADAPTER` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/routing/router.py:147:45` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/routing/router.py:174:22` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/service/harness_health.py:41:45` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/service/service.py:358:18` | `adapters` | capabilities.adapters 连接件报告，含 command/decision/external。 |
| `src/hey_my_buddy/blackboard/service/service.py:491:49` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/service/service.py:498:27` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/service/service.py:543:61` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/service/service.py:877:76` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/service/service.py:886:57` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/db.py:71:5` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/db.py:108:5` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/db.py:146:5` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/db.py:550:7` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:31:41` | `adapter` | schema 18 升级源列、JSON 或原生错误码，只在升级边界读旧写新。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:35:30` | `adapter` | schema 18 升级源列、JSON 或原生错误码，只在升级边界读旧写新。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:36:36` | `adapter` | schema 18 升级源列、JSON 或原生错误码，只在升级边界读旧写新。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:37:25` | `adapter` | schema 18 升级源列、JSON 或原生错误码，只在升级边界读旧写新。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:52:13` | `adapter` | schema 18 升级源列、JSON 或原生错误码，只在升级边界读旧写新。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:53:45` | `adapter` | schema 18 升级源列、JSON 或原生错误码，只在升级边界读旧写新。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:55:39,74,85` | `ADAPTER_UNAVAILABLE`、`UNSUPPORTED_ADAPTER`、`adapter-unavailable` | schema 18 升级源列、JSON 或原生错误码，只在升级边界读旧写新。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:56:35` | `ADAPTER_UNAVAILABLE` | schema 18 升级源列、JSON 或原生错误码，只在升级边界读旧写新。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:57:47` | `UNSUPPORTED_ADAPTER` | schema 18 升级源列、JSON 或原生错误码，只在升级边界读旧写新。 |
| `src/hey_my_buddy/blackboard/store/migrations.py:58:35` | `adapter-unavailable` | schema 18 升级源列、JSON 或原生错误码，只在升级边界读旧写新。 |
| `src/hey_my_buddy/blackboard/store/store.py:339:14,29` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:378:14,29` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:446:14,29` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:498:90` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:631:32` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:765:30` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:769:36,58` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:776:42` | `DECISION_ADAPTER` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/store/store.py:860:39` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:900:44` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:911:58` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:930:26,69` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:971:18` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:986:9,31` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:987:12,36,59` | `adapter`、`ADAPTERS` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/store/store.py:988:59` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1007:12` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/blackboard/store/store.py:1008:29` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1009:27` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1065:64` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1398:39` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1403:9,52` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1410:66,76` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1420:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1422:43` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1427:53` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1429:32` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1432:84,94` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1464:18` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1572:13,31` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1609:75` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1610:43` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1612:27,40` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1613:31` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1614:27,42` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/blackboard/store/store.py:1616:32` | `adapter-mismatch` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/blackboard/store/store.py:1645:26,59` | `adapter`、`DECISION_ADAPTER` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/store/store.py:1665:26,59` | `adapter`、`DECISION_ADAPTER` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/store/store.py:1682:26,59` | `adapter`、`DECISION_ADAPTER` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/store/store.py:1758:47` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1775:29` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1812:76,94` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:1830:53,62` | `adapter`、`adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:2615:46` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:2630:51` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:2639:9,31` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:2640:12` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:2641:29` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:2642:27` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/store/store.py:2708:54` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/delegation.py:61:57` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/delegation.py:62:45` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/delegation.py:69:16` | `effective_adapter` | 任务连接件投影，包含普通命令/外部任务。 |
| `src/hey_my_buddy/blackboard/tasks/delegation.py:88:39` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/delegation.py:183:48` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/blackboard/tasks/delegation.py:186:48` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/blackboard/tasks/delegation.py:209:57,79` | `adapter`、`effective_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/blackboard/tasks/delegation.py:276:49` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:50:5,26` | `adapter_capabilities`、`adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:51:12` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:53:49` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:55:26,37` | `adapters`、`adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:60:21` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:61:21` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:68:25,46` | `adapter_capabilities`、`adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:71:21` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:73:20,41` | `adapter_capabilities`、`adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:125:59` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:152:5,25` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:169:31` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:170:35` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:173:35` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:187:34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:191:15` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:193:34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py:599:39` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/macro_task_summary_cache.py:31:42` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/blackboard/tasks/macro_task_summary_cache.py:35:31` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/macro_tasks.py:272:18` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/macro_tasks.py:653:48,67` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/scheduling.py:23:17` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/scheduling.py:26:10` | `DECISION_ADAPTER` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/tasks/storage.py:461:13,44` | `adapter`、`ADAPTERS` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/tasks/storage.py:462:13,40` | `adapter_root`、`adapter` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/tasks/storage.py:463:36` | `adapter_root` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/tasks/storage.py:464:34,65,73` | `adapter_root`、`adapter` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/tasks/storage.py:466:20` | `adapter_root` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/tasks/storage.py:468:21` | `adapter_root` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/tasks/storage.py:470:58,66` | `adapter` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/tasks/storage.py:475:67,75` | `adapter` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/tasks/storage.py:476:16` | `adapter` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/tasks/storage.py:477:36` | `adapter_root` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/tasks/storage.py:479:78,86` | `adapter` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/tasks/storage.py:613:46` | `ADAPTERS` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/tasks/storage.py:702:63` | `adapter` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/tasks/storage.py:703:27,51` | `adapter`、`ADAPTERS` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/tasks/worker_sessions.py:37:38` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:10:33` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:731:18,34,58` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:744:14,35` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:1192:29` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:1206:20` | `_execution_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:1207:49` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:1209:16` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:1216:58,74` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:1312:46` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:1361:54` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:1418:69` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:2552:36` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:2562:50` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:2878:54` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:2891:54` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:5404:17,41` | `adapter`、`ADAPTERS` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:5405:87` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:5628:67` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:5630:54` | `DECISION_ADAPTER` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:5632:48,70` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:5638:39` | `DECISION_ADAPTER` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:5777:44` | `_execution_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:5905:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:5993:9` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:6386:16` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py:6594:32,49` | `_execution_adapter`、`adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/base.py:1:4` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/base.py:3:1` | `Adapters` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/base.py:23:22` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/base.py:34:64` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/base.py:35:44` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/base.py:38:15` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/base.py:84:58` | `private_adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/base.py:94:7` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/base.py:182:7` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/base.py:183:36` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/base.py:211:22` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/base.py:217:31` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/base.py:222:78` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/base.py:369:17` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/buddy/harnesses/base.py:370:36,54,62` | `UNSUPPORTED_ADAPTER`、`adapter` | 连接件错误码/HTTP 映射，原生 harness 失败另有码。 |
| `src/hey_my_buddy/buddy/harnesses/claude/adapter.py:30:13` | `ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/claude/protocol.py:392:15` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/codex/adapter.py:8:12` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/adapter.py:18:10` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/dsh/adapter.py:71:16` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/native_observations.py:1:63` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/native_support.py:28:26` | `TypeAdapter` | Pydantic/原生 SDK 的上游名字。 |
| `src/hey_my_buddy/buddy/harnesses/native_support.py:107:5,19` | `adapter`、`TypeAdapter` | Pydantic/原生 SDK 的上游名字。 |
| `src/hey_my_buddy/buddy/harnesses/native_support.py:113:20` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:1:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:4:16` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:9:19,28` | `Adapter`、`AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:11:38` | `CommandAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:12:14,35` | `adapter`、`ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:13:13,33` | `adapter`、`CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:14:36` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:15:11,29` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:16:13,33` | `adapter`、`ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:22:16,32,49,63,77,92` | `DshAdapter`、`CommandAdapter`、`DecisionAdapter`、`ZcodeAdapter`、`CodexAdapter`、`ClaudeAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:27:46` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:130:34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:165:5,29,44` | `adapters`、`Adapter`、`ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:166:13,27,41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:169:5,27,42` | `adapter`、`Adapter`、`ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:170:65` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:175:16` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:183:26` | `UNSUPPORTED_ADAPTER` | 连接件错误码/HTTP 映射，原生 harness 失败另有码。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:184:15` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:186:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:193:27` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:196:14` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:208:10` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:230:21,44` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:235:14` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:239:26` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:253:27` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:266:64` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:270:6` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:271:6` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:272:13` | `CommandAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:273:12` | `ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:274:11` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:275:14` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:276:9` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:277:11` | `ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:283:6` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:284:6` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/run_contract.py:608:28` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/runtime_selection.py:1:52` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/adapter.py:11:12` | `ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py:85:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py:252:33` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/controller.py:10:5` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/controller.py:11:5` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/controller.py:43:23` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/controller.py:55:35` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/controller.py:63:38` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/controller.py:68:34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/controller.py:71:12` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/controller.py:74:34` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/controller.py:75:12` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/controller.py:86:28` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/controller.py:91:30,112` | `Adapter`、`AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/controller.py:95:29` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/controller.py:96:61` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/controller.py:135:13` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/controller.py:185:40` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/router.py:15:5` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/router.py:24:62` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/router.py:45:64` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/router.py:65:15,23` | `DecisionAdapter`、`Adapter` | 内部 Router 的执行连接件及其可用性。 |
| `src/hey_my_buddy/buddy/roles/router.py:70:42` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/router.py:71:23` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/router.py:77:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/router.py:82:18` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/run_controller.py:27:34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/run_controller.py:57:49` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/run_execution.py:22:30` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/run_execution.py:529:38` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/run_execution.py:541:23` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/run_execution.py:567:25` | `private_adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/run_execution.py:607:76` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/run_execution.py:694:16` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/structured_call.py:8:30` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/structured_call.py:127:39` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/structured_call.py:156:12` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/roles/turn_io.py:127:76` | `private_adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/turn_io.py:144:87` | `private_adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/turn_io.py:268:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/roles/turn_io.py:269:31` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/command.py:1:20` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/buddy/runtime/command.py:13:30,39` | `Adapter`、`AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/runtime/command.py:19:14,22` | `CommandAdapter`、`Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/runtime/command.py:26:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/command.py:31:18` | `ADAPTER_UNAVAILABLE` | 连接件错误码/HTTP 映射，原生 harness 失败另有码。 |
| `src/hey_my_buddy/buddy/runtime/command.py:33:17` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/command.py:74:76` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/runtime/command.py:103:16` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/runtime/supervisor.py:140:97` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/windows_process.py:1:36` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:76:19` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:78:5` | `Adapters` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:100:34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:188:9` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:209:14,25` | `adapters` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:409:9,30` | `adapter_names`、`adapters` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:413:13,21` | `adapter`、`adapter_names` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:507:64` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:531:18,34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:538:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:543:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:551:61` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:554:26` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:615:22` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:654:86` | `ADAPTER_UNAVAILABLE` | 连接件错误码/HTTP 映射，原生 harness 失败另有码。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:695:72` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:701:58` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:739:17` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:759:22,38` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:773:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:801:19` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:820:103` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:841:17,33` | `private_adapter`、`adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:842:20` | `private_adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:843:21` | `private_adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:844:20` | `private_adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/buddy/runtime/worker.py:846:69` | `private_adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/cli/blocking.py:308:64` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/cli/cli_help.py:43:22` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/cli/cli_help.py:44:19` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `src/hey_my_buddy/cli/cli_help.py:72:87` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/cli/cli_help.py:78:35` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/cli/cli_help.py:88:48` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/cli/cli_help.py:911:14,49,75` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/cli/cli_views.py:391:6` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/cli/cli_views.py:457:52` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/cli/main.py:7:49` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/cli/main.py:172:48` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/cli/main.py:174:82` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/cli/main.py:180:7` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/cli/main.py:190:19` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/console/assets/assets/index-Bo1EQ740.js:53:10344,10818` | `adapter` | 打包资产中的执行连接件字段，与前端源码一致。 |
| `src/hey_my_buddy/console/server.py:60:35` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/console/server.py:176:18` | `UNSUPPORTED_ADAPTER` | 连接件错误码/HTTP 映射，原生 harness 失败另有码。 |
| `src/hey_my_buddy/console/server.py:180:6` | `ADAPTER_UNAVAILABLE` | 连接件错误码/HTTP 映射，原生 harness 失败另有码。 |
| `src/hey_my_buddy/install/runtime.py:4:25` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/install/runtime.py:599:33` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/private_dirs.py:15:1` | `ADAPTERS` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/private_dirs.py:101:6,14` | `_adapter`、`adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/private_dirs.py:102:8,23` | `adapter`、`ADAPTERS` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/private_dirs.py:103:71,81,89` | `adapter` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/private_dirs.py:104:12` | `adapter` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/private_dirs.py:107:28` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/private_dirs.py:110:46,54` | `_adapter`、`adapter` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/private_dirs.py:113:30` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/private_dirs.py:114:29` | `adapter` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/private_dirs.py:117:31` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/private_dirs.py:120:29` | `adapter` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/private_dirs.py:123:27` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/private_dirs.py:126:16,45` | `adapter` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/private_dirs.py:202:46` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/private_dirs.py:204:32` | `adapter` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/protocol/client.py:12:39` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/client.py:103:9` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/protocol/client.py:113:44` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/client.py:114:18,28` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/client.py:115:80` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/examples/external_worker.py:11:93` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/examples/external_worker.py:85:48` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:42:1` | `ADAPTERS` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/protocol/schemas.py:86:10` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:216:25` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `src/hey_my_buddy/protocol/schemas.py:218:8` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:220:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:222:82` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:252:5,40` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:253:8,23` | `adapter`、`ADAPTERS` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/protocol/schemas.py:255:26` | `UNSUPPORTED_ADAPTER` | 连接件错误码/HTTP 映射，原生 harness 失败另有码。 |
| `src/hey_my_buddy/protocol/schemas.py:256:23,32,75` | `adapter`、`ADAPTERS` | 连接件私有目录/集合，含 command。 |
| `src/hey_my_buddy/protocol/schemas.py:257:13,21` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:260:10,20` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:273:26` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:582:5` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:583:46` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:587:5,27` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:588:8` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:595:24,33` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:598:10` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:599:89` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:613:24` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:616:37` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:617:22` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:618:19` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:623:18` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/schemas.py:624:18,34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/transport.py:38:4` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `src/hey_my_buddy/protocol/usage.py:3:1` | `Adapters` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/dsh_native_resume_smoke.py:4:22` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/dsh_native_resume_smoke.py:25:39,57` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/dsh_native_resume_smoke.py:156:59` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/dsh_native_resume_smoke.py:166:21` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/dsh_native_resume_smoke.py:263:20` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/dsh_native_resume_smoke.py:264:30` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/dsh_native_resume_smoke.py:270:65` | `_execution_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/dsh_native_resume_smoke.py:271:79` | `_execution_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/dsh_native_resume_smoke.py:297:24` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/macro_task_console_preview.py:264:51` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/macro_task_console_preview.py:389:65` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `tests/probes/macro_task_console_preview.py:401:10,20` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/macro_task_console_preview.py:757:25` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/macro_task_console_preview.py:776:27` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/macro_task_console_preview.py:846:52,69` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/macro_task_console_preview.py:862:5,22` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/macro_task_console_preview.py:863:53,63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/macro_task_console_preview.py:867:15` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/macro_task_console_preview.py:871:41,51` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/macro_task_console_preview.py:900:47` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/macro_task_console_preview.py:981:19,36` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/macro_task_console_preview.py:1219:20` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/macro_task_console_preview.py:1221:31,94` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/macro_task_console_preview.py:1426:35,60` | `decisionAdapter`、`decisionAdapterReason` | 内部 Router 的执行连接件及其可用性。 |
| `tests/probes/macro_task_console_preview.py:1672:75` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/router_fast.py:20:53` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/probes/router_fast.py:78:16` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/router_fast.py:81:30` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/probes/router_readonly.py:3:40` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/router_readonly.py:54:5` | `adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/router_readonly.py:57:49,69` | `adapter`、`CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/router_readonly.py:58:21` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/router_readonly.py:60:50,71` | `adapter`、`ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/router_readonly.py:61:22` | `ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/router_readonly.py:63:47,65` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/router_readonly.py:64:19` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/router_readonly.py:65:45,65` | `adapter`、`ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/router_readonly.py:66:17` | `ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/router_readonly.py:266:5,15` | `adapter`、`adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/router_readonly.py:267:25` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/router_readonly.py:309:16` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/probes/router_readonly_tools.py:23:51,62` | `adapter`、`adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/router_readonly_tools.py:37:45,65` | `adapter`、`CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/router_readonly_tools.py:38:46,67` | `adapter`、`ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/router_readonly_tools.py:39:32` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/router_readonly_tools.py:40:34` | `ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/router_readonly_tools.py:162:14` | `adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/probes/zcode_request_input.py:29:15` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/catalog/test_catalog.py:25:55` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/catalog/test_catalog.py:27:30` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/catalog/test_catalog.py:36:59` | `adapters` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/catalog/test_catalog.py:57:59` | `adapters` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/catalog/test_catalog.py:71:59` | `adapters` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/catalog/test_catalog.py:78:57` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/catalog/test_catalog.py:82:33` | `ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/catalog/test_catalog.py:88:59` | `adapters` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/catalog/test_catalog.py:138:46` | `test_buddy_permissions_follow_worker_adapter_declarations` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/catalog/test_catalog.py:170:30` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/catalog/test_catalog.py:171:30` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/catalog/test_catalog.py:180:34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/catalog/test_catalog.py:228:126` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/catalog/test_catalog_trust.py:56:63` | `adapters` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/catalog/test_native_discovery_stop.py:46:59` | `adapters` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/evaluation/test_native_observations.py:83:46` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/evaluation/test_native_observations.py:122:52` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/evaluation/test_native_observations.py:226:69` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/evaluation/test_other_native_observations.py:1:54` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/evaluation/test_other_native_observations.py:19:49` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/evaluation/test_other_native_observations.py:177:13` | `ClaudeAdapterObservationTests` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/evaluation/test_other_native_observations.py:195:14` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/evaluation/test_other_native_observations.py:212:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/evaluation/test_other_native_observations.py:215:21` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/evaluation/test_other_native_observations.py:446:46` | `AiSdkModelAdapterError` | Pydantic/原生 SDK 的上游名字。 |
| `tests/python/blackboard/evaluation/test_other_native_observations.py:452:12` | `ZcodeAdapterObservationTests` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/evaluation/test_other_native_observations.py:473:14` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/evaluation/test_other_native_observations.py:489:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/evaluation/test_other_native_observations.py:492:30` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_current_router.py:33:67,78` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_current_router.py:77:38` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_current_router.py:85:34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_current_router.py:437:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_current_router.py:611:34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_decision.py:6:43` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_decision.py:170:36` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_decision.py:330:48` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_decision.py:369:48` | `decisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/blackboard/routing/test_decision.py:389:47` | `decisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/blackboard/routing/test_decision.py:391:43` | `adapters` | capabilities.adapters 连接件报告，含 command/decision/external。 |
| `tests/python/blackboard/routing/test_decision.py:399:26` | `UNSUPPORTED_ADAPTER` | 连接件错误码/HTTP 映射，原生 harness 失败另有码。 |
| `tests/python/blackboard/routing/test_decision.py:402:79` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_decision.py:691:45` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_decision.py:735:44` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_fast_router_probe.py:9:47` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_fast_router_probe.py:25:41` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/blackboard/routing/test_fast_router_probe.py:39:19` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_fast_router_probe.py:40:41` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/blackboard/routing/test_fast_router_probe.py:41:44` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/blackboard/routing/test_nonretryable_quota.py:40:58` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_read_only_eligibility.py:7:47` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_read_only_eligibility.py:8:41,61` | `adapter`、`CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_read_only_eligibility.py:9:42,63` | `adapter`、`ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_read_only_eligibility.py:10:39,57` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_read_only_eligibility.py:11:41,61` | `adapter`、`ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_read_only_eligibility.py:24:31,48` | `CodexAdapter`、`ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_read_only_eligibility.py:31:40` | `test_unimplemented_non_sandbox_adapters_stay_ineligible` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_read_only_eligibility.py:34:26,40,56` | `Adapter`、`DshAdapter`、`ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_read_only_eligibility.py:44:24` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_read_only_eligibility.py:52:28` | `ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_read_only_eligibility.py:57:35` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_read_only_eligibility.py:60:26` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_readonly_tools_probe.py:11:47` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_readonly_tools_probe.py:38:16` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_readonly_tools_probe.py:40:59` | `test_preparation_never_selects_or_starts_a_native_adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_readonly_tools_probe.py:41:35` | `adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_readonly_tools_probe.py:57:35` | `adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_readonly_tools_probe.py:77:35` | `adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_readonly_tools_probe.py:88:35` | `adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_readonly_tools_probe.py:112:75` | `test_all_harness_packets_freeze_policy_without_selecting_a_native_adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_readonly_tools_probe.py:114:70` | `adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_readonly_tools_probe.py:141:70` | `adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_retired_review_verifier.py:22:51,66,75` | `adapter`、`get_adapter`、`adapters` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_retired_review_verifier.py:56:42` | `adapters` | 断言旧字段或退役连接件元数据不存在。 |
| `tests/python/blackboard/routing/test_retired_review_verifier.py:59:17` | `get_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_retired_review_verifier.py:60:63` | `UNSUPPORTED_ADAPTER` | 连接件错误码/HTTP 映射，原生 harness 失败另有码。 |
| `tests/python/blackboard/routing/test_retired_review_verifier.py:62:18` | `test_the_adapter_certificate_attribute_is_gone_not_false` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_retired_review_verifier.py:63:25` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_retired_review_verifier.py:89:50` | `adapters` | 断言旧字段或退役连接件元数据不存在。 |
| `tests/python/blackboard/routing/test_router.py:6:39,57` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router.py:97:67,78` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router.py:100:96` | `_adapter_available` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router.py:106:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router.py:198:54,65` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router.py:206:37` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_boundary_integration.py:621:70` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_boundary_integration.py:648:70` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_dispatch.py:38:43` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_dispatch.py:89:57` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_dispatch.py:90:53` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_dispatch.py:289:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_dispatch.py:294:23` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_dispatch.py:315:38` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_dispatch.py:320:19` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_dispatch.py:417:38` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_dispatch.py:448:45` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_dispatch.py:450:48` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_dispatch.py:563:54,65` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_failover_integration.py:36:71,82` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_failover_integration.py:39:54,65` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_failover_integration.py:74:54,65` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_failover_integration.py:161:49` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_failover_integration.py:449:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_failover_integration.py:452:21` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_list_health.py:90:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_list_health.py:95:38` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_list_health.py:290:38` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_list_health.py:295:34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_probe.py:96:55,75` | `adapter`、`registry_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_probe.py:113:49` | `adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_probe.py:114:32` | `registry_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_probe.py:247:35` | `adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_probe.py:262:24` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_probe.py:263:32` | `adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_probe.py:267:43` | `adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_probe.py:291:28` | `adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_probe.py:294:39` | `adapter_for` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_sequence.py:84:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_sequence.py:96:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_tool_evidence.py:55:62,73` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_tool_evidence.py:110:61` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_router_tool_evidence.py:168:54,65` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_tool_evidence.py:268:54,65` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_router_tool_evidence.py:297:67,78` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_health.py:48:26` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_routing_health.py:54:61` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_routing_health.py:59:39` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_routing_history.py:4:55` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_routing_history.py:50:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_routing_history.py:208:36` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_routing_history.py:331:61,80` | `DecisionAdapter`、`adapter_start` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/blackboard/routing/test_routing_history.py:340:13` | `adapter_start` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_routing_modes.py:21:67,78` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_modes.py:90:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_routing_modes.py:91:54,65` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_modes.py:121:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_routing_modes.py:152:65` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:22:47` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:23:53` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:25:39,57` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:26:41,61` | `adapter`、`CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:27:42,63` | `adapter`、`ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:28:41,61` | `adapter`、`ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:149:18` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:150:47` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:153:22` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:160:22` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:168:68` | `adapters` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:169:42` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:172:24,36,50,65,79` | `Adapter`、`DshAdapter`、`CodexAdapter`、`ClaudeAdapter`、`ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:175:26` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:176:29` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:177:31` | `ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:178:29` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:179:30` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:180:31` | `ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:181:28` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:182:30` | `ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:183:32` | `ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:186:22` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:211:56` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:213:34` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:237:64,92` | `adapter`、`CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:239:46` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:242:25` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:337:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_routing_preferences.py:377:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_selection_publication.py:153:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_single_router_claims.py:9:53` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/blackboard/routing/test_single_router_claims.py:10:39,57` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_single_router_claims.py:39:61` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_single_router_claims.py:134:54,65` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_single_router_claims.py:265:14` | `test_adapter_start_and_collect_use_local_eligibility_without_certificate` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_single_router_claims.py:272:42` | `adapter-unit` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_single_router_claims.py:274:26` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/blackboard/routing/test_single_router_claims.py:275:37` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_single_router_claims.py:288:14` | `test_adapter_local_check_failure_never_prepares_input_or_starts_native` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_single_router_claims.py:294:20,80` | `adapter`、`blocked-adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_single_router_claims.py:296:54,65` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_single_router_claims.py:298:25` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/blackboard/routing/test_single_router_claims.py:348:54,65` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_single_router_configuration.py:47:54,65` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_single_router_requests.py:151:54,65` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_single_router_requests.py:208:58,69` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_sole_candidate_selection.py:272:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_sole_candidate_selection.py:343:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/routing/test_stage2_review_scope.py:11:39,57` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_stage2_review_scope.py:12:41,61` | `adapter`、`ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/routing/test_stage2_review_scope.py:33:33,49` | `DshAdapter`、`ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/service/fixtures/daemon_with_catalog.py:31:21` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/service/test_governed_service_stop.py:84:39` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/service/test_harness_health.py:751:59` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/service/test_harness_service.py:28:34` | `adapters` | capabilities.adapters 连接件报告，含 command/decision/external。 |
| `tests/python/blackboard/service/test_harness_service.py:29:34` | `adapters` | capabilities.adapters 连接件报告，含 command/decision/external。 |
| `tests/python/blackboard/service/test_harness_service.py:49:33` | `adapters` | capabilities.adapters 连接件报告，含 command/decision/external。 |
| `tests/python/blackboard/service/test_harness_service.py:50:34` | `adapters` | capabilities.adapters 连接件报告，含 command/decision/external。 |
| `tests/python/blackboard/service/test_harness_service.py:65:33` | `adapters` | capabilities.adapters 连接件报告，含 command/decision/external。 |
| `tests/python/blackboard/service/test_harness_service.py:94:56,69` | `adapter`、`CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/service/test_harness_service.py:136:59` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/service/test_harness_startup.py:40:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/service/test_live_holder_integration.py:23:47` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/service/test_live_registry.py:126:9` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/service/test_live_registry.py:127:43,51,74` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/service/test_live_registry.py:133:17` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/service/test_live_registry.py:224:43` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/service/test_live_registry.py:398:43` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/service/test_service.py:31:59` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/service/test_service.py:116:59` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:27:47` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:40:77` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:63:77` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:73:77` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:85:77` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:117:75` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:118:75` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:127:71` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:129:80` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:136:87` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:142:37` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:149:71` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:150:38` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:163:73` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:164:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:165:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:175:69` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:176:87` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:177:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:188:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:189:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:190:74` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:193:84` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:206:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:207:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:208:71` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:210:78` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:217:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:219:73` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:224:73` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:233:79` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:234:43` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:252:73` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:260:72` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:261:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:269:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:283:38` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `tests/python/blackboard/store/test_blackboard.py:284:33` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:285:33` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:286:37,64` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:293:30` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:297:18,28` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:301:16` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:316:71` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:317:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:345:29` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:385:30` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:526:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:546:63` | `CommandAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:556:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:563:33` | `CommandAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:576:16` | `CommandAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:581:20` | `CommandAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:595:49` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:601:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:602:31,39` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:603:61` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:604:46,73` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:616:22` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:617:49` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:622:102` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:640:14` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:641:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:644:47` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:659:94` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:678:14` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:679:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:682:47` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:697:94` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:715:14` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:716:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:718:47` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:721:94` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:729:22` | `test_virtual_adapter_receipt_retains_evidence_failure_paths` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:739:14` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:740:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:742:47` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:747:94` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:773:14` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:774:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:777:47` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:783:94` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:807:14` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:808:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:814:94` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:840:14` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:841:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:843:47` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:849:94` | `fake_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/store/test_blackboard.py:860:73` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:861:43` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:880:76` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:881:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:910:26` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1004:68` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1005:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1019:77` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1020:40` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1031:73` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1032:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1036:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1059:73` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1060:46` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1079:67` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1081:43` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1101:77` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1114:80` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1123:73` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1124:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1160:67` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1162:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1180:82` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1181:40` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1195:77` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1204:55` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1206:39` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1216:52` | `test_inquiry_bounds_and_honest_unsupported_adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1220:74` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1238:89` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1261:74` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1262:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1273:26` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1278:38` | `adapters` | capabilities.adapters 连接件报告，含 command/decision/external。 |
| `tests/python/blackboard/store/test_blackboard.py:1279:42` | `adapters` | capabilities.adapters 连接件报告，含 command/decision/external。 |
| `tests/python/blackboard/store/test_blackboard.py:1280:34` | `adapters` | capabilities.adapters 连接件报告，含 command/decision/external。 |
| `tests/python/blackboard/store/test_blackboard.py:1284:22` | `test_command_adapter_runs_explicit_argv_without_a_shell` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1293:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1307:22` | `test_command_adapter_rejects_a_missing_executable_honestly` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1314:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1322:49` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1323:24` | `ADAPTER_UNAVAILABLE` | 连接件错误码/HTTP 映射，原生 harness 失败另有码。 |
| `tests/python/blackboard/store/test_blackboard.py:1331:88` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1350:54` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1374:119` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1469:22` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1500:83` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1535:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1571:74` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1576:52` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_blackboard.py:1578:90` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_current_core.py:46:108` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_current_core.py:151:110` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_current_core.py:152:44` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_store.py:56:50` | `adapter` | Worker 认领的连接件能力令牌/匹配状态。 |
| `tests/python/blackboard/store/test_store.py:57:51,59,82` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_store.py:133:53` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_store.py:138:17` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_store.py:144:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_store.py:147:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_upgrade_fixture.py:117:72` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_upgrade_fixture.py:163:35` | `adapter` | 升级基点的旧列/JSON，或升级结果不含旧键的断言。 |
| `tests/python/blackboard/store/test_upgrade_fixture.py:187:21` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_upgrade_fixture.py:192:67` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_upgrade_fixture.py:197:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/store/test_upgrade_fixture.py:201:53` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_delegation_history.py:41:18` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_delegation_history.py:266:83` | `_adapter_available` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_delegation_history.py:353:23` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_delegation_history.py:360:83` | `_adapter_available` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_delegation_history.py:557:44` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_delegation_history.py:662:83` | `_adapter_available` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_delegation_history.py:666:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_delegation_history.py:685:71` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_delegation_history.py:732:83` | `_adapter_available` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_dsh_resume_selection.py:9:27` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/tasks/test_dsh_resume_selection.py:25:51,71` | `adapter`、`registry_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/tasks/test_dsh_resume_selection.py:33:52` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_dsh_resume_selection.py:37:49` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_dsh_resume_selection.py:49:35` | `registry_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/tasks/test_dsh_resume_selection.py:61:27` | `registry_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/tasks/test_dsh_resume_selection.py:94:27` | `registry_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/tasks/test_dsh_resume_selection.py:156:27` | `registry_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/tasks/test_dsh_resume_selection.py:211:27` | `registry_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/tasks/test_empty_claims.py:10:9` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_empty_claims.py:26:56` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_empty_claims.py:31:39` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_inquiry.py:204:45` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_inquiry.py:206:39,47,70` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_inquiry.py:469:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_inquiry.py:484:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_inquiry.py:505:29` | `test_an_unsupported_adapter_answers_with_a_capability_error` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_inquiry.py:509:70` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_inquiry.py:515:48` | `test_inquiry_capability_comes_from_the_adapter_registry` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_inquiry.py:526:53` | `not-an-adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_inquiry.py:527:31` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_inquiry.py:1060:56` | `adapter_capabilities` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/tasks/test_inquiry.py:1061:30` | `test_an_observe_only_adapter_refuses_a_question_without_asking_the_bridge` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_inquiry.py:1077:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_inquiry.py:1079:102` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_model_service_concurrency.py:331:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_model_service_concurrency.py:353:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_model_service_concurrency.py:379:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_parallel_dispatch.py:22:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_parallel_dispatch.py:67:8` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_parallel_dispatch.py:154:14` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_parallel_dispatch.py:162:51` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_parallel_dispatch.py:204:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_parallel_dispatch.py:314:77` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_parallel_dispatch.py:335:79` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_parallel_dispatch.py:503:78` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_permission_levels.py:74:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_permission_levels.py:75:31,39` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_permission_levels.py:76:48` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_permission_levels.py:78:61,70,78` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_permission_resume.py:5:47` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/tasks/test_permission_resume.py:6:51` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_permission_resume.py:13:24` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_permission_resume.py:21:26` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/tasks/test_permission_resume.py:24:34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_scheduler_lanes.py:50:45` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_scheduler_lanes.py:55:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_scheduler_lanes.py:94:51` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_scheduling.py:31:15` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_scheduling.py:32:15` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_scheduling.py:33:15` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_scheduling.py:34:15` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_scheduling.py:35:15` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_scheduling.py:38:15` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_storage.py:341:62` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_storage.py:346:18` | `test_all_adapters_private_goals_are_visible` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_storage.py:349:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_storage.py:350:84` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_storage.py:355:36` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_title_fallback.py:87:18` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow.py:39:89` | `_execution_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/tasks/test_workflow.py:48:36` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow.py:205:44` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow.py:209:62` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow.py:213:47` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow.py:215:44` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow.py:217:81` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow.py:220:35` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow.py:680:58` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_cancellation.py:343:105` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_cancellation.py:358:101` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_preparation.py:336:73` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_real.py:4:64` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_real.py:250:11` | `RealAdapterTests` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/tasks/test_workflow_real.py:253:43` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/blackboard/tasks/test_workflow_real.py:333:14` | `test_adapter_verifies_input_writes_exact_bytes_and_gates_the_seal` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_real.py:338:39` | `adapter-1` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_real.py:401:39` | `adapter-2` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_routing.py:42:63` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_routing.py:84:53` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_routing.py:129:31` | `adapter` | 断言旧字段或退役连接件元数据不存在。 |
| `tests/python/blackboard/tasks/test_workflow_routing.py:197:67` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_routing.py:356:59` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_routing.py:373:59` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_routing.py:393:59` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_routing.py:408:14` | `test_adapter_specific_provenance_can_refuse_an_otherwise_valid_turn` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_routing.py:411:59` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_routing.py:423:79` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_routing.py:465:82` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_routing.py:517:84` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/blackboard/tasks/test_workflow_routing.py:519:61` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/blackboard/tasks/test_workflow_stop_surface.py:47:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/claude/test_claude.py:24:49` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/claude/test_claude.py:27:42,63` | `adapter`、`ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/claude/test_claude.py:73:14,30` | `adapter`、`ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/claude/test_claude.py:109:41` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/claude/test_claude.py:126:13` | `ClaudeAdapterTests` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/claude/test_claude.py:131:59,79` | `adapter`、`registry_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/claude/test_claude.py:134:14,33` | `adapter`、`registry_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/claude/test_claude.py:137:43` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/claude/test_claude.py:414:28` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/claude/test_claude.py:440:36` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/claude/test_claude_tool_evidence.py:20:49` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/claude/test_claude_tool_evidence.py:22:42,63` | `adapter`、`ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/claude/test_claude_tool_evidence.py:503:14,30` | `adapter`、`ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/claude/test_claude_worker.py:12:51` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/claude/test_claude_worker.py:73:26` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/claude/test_native_run.py:9:31` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/claude/test_native_run.py:603:63` | `ClaudeAdapterTests` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/claude/test_native_run.py:619:71` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/claude/test_native_run.py:625:50,71` | `adapter`、`ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/claude/test_native_run.py:626:29` | `ClaudeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/fixtures/mock_codex.py:291:46` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:5:70` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:22:41,61` | `adapter`、`CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:29:12` | `CodexAdapterTests` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:51:14` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:65:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:68:21` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:100:75` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:108:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:111:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:240:28` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:322:61` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:323:37` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:324:35` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:328:28` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:383:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:387:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:394:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:399:14` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:401:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:408:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:415:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:418:14` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:420:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex.py:476:75` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex_continuation.py:8:41,61` | `adapter`、`CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex_continuation.py:19:40` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex_continuation.py:20:9` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/codex/test_codex_continuation.py:45:22` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/codex/test_codex_continuation.py:48:20` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/codex/test_codex_continuation.py:68:40` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex_tool_evidence.py:11:41,61` | `adapter`、`CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex_tool_evidence.py:392:14,29` | `adapter`、`CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_codex_tool_evidence.py:416:75` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_no_tool_codex.py:4:57` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/buddy/harnesses/codex/test_no_tool_codex.py:16:41,61` | `adapter`、`CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_no_tool_codex.py:68:30` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/codex/test_no_tool_codex.py:119:14` | `test_adapter_entrypoint_and_collector` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py:24:47` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py:25:51` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py:114:23` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py:117:47` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py:136:23` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py:152:23` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py:377:27` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/dsh/test_dsh_session_storage.py:30:40` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/dsh/test_dsh_session_storage.py:36:47` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/dsh/test_no_tool_dsh.py:22:51` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/dsh/test_no_tool_dsh.py:40:16` | `adapter-invariants` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/dsh/test_worker_permissions.py:8:51` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/dsh/test_worker_permissions.py:88:26` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_adapter_usage.py:1:4` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_adapter_usage.py:53:14` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_adapter_usage.py:67:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_adapter_usage.py:70:21` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_controller.py:232:32` | `CommandAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_live_channel.py:245:59` | `test_queued_is_never_upgraded_to_delivered_by_the_adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:21:41,61` | `adapter`、`CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:22:55` | `CommandAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:23:53` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:25:39,57` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:26:41,61` | `adapter`、`ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:60:15` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:211:14` | `PrivateAdapterInvariants` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:230:49` | `CodexAdapterTests` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:231:51` | `ClaudeAdapterTests` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:259:15` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:274:15` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:276:9,26` | `adapter`、`CommandAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:277:17` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:280:26` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:284:19,72` | `adapter`、`CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:285:79` | `ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:286:31,39` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:288:20` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:301:23,33,58` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:303:26` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:313:98` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:314:83` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:318:49` | `CodexAdapterTests` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:328:11` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:331:75` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:343:53` | `test_decision_fast_router_wraps_each_native_adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:350:31` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:367:23` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:373:34` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:376:35` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:394:31` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:417:50` | `ClaudeAdapterTests` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:433:46` | `CodexAdapterTests` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/test_private_adapter_invariants.py:435:31` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/zcode/fixtures/checkpoint_domain.py:152:29` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/fixtures/checkpoint_domain.py:168:38` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/fixtures/checkpoint_domain.py:195:39` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/fixtures/mock_zcode.py:507:41` | `AiSdkModelAdapterError` | Pydantic/原生 SDK 的上游名字。 |
| `tests/python/buddy/harnesses/zcode/fixtures/mock_zcode.py:552:19` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/zcode/fixtures/mock_zcode.py:569:105` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/zcode/fixtures/native-usage-zcode.json:167:34` | `AiSdkModelAdapterError` | Pydantic/原生 SDK 的上游名字。 |
| `tests/python/buddy/harnesses/zcode/test_native_config.py:13:41,61` | `adapter`、`ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_native_config.py:120:35` | `ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_native_run.py:819:40` | `adapter-path` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:18:51` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:48:14` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:49:28` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:66:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:69:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:186:12` | `ZcodeAdapterTests` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:306:71` | `AiSdkModelAdapterError` | Pydantic/原生 SDK 的上游名字。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:422:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:426:14` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:427:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:434:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:452:26` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:462:14` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:476:30` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:488:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:490:14` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:491:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode.py:495:30` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:3:33` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:94:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:99:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:139:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:144:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:154:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:163:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:173:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:178:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:187:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:197:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:207:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:212:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:261:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:266:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:274:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:278:14` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py:279:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py:1041:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py:1055:31` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py:1064:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py:1085:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_protocol.py:609:39` | `AiSdkModelAdapterError` | Pydantic/原生 SDK 的上游名字。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_protocol.py:618:59` | `AiSdkModelAdapterError` | Pydantic/原生 SDK 的上游名字。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_evidence.py:21:41,61` | `adapter`、`ZcodeAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_evidence.py:162:35` | `AiSdkModelAdapterError` | Pydantic/原生 SDK 的上游名字。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:36:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:39:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:59:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:64:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:79:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:84:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:94:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:97:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:106:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:109:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:117:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:120:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:131:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:134:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:146:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_tool_refusals.py:149:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py:14:39,57` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py:30:29` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py:42:14` | `test_adapter_specific_provenance_does_not_bypass_common_identity_or_stop` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py:44:29` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py:48:66` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py:49:70` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py:53:73` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py:54:11,43` | `Adapter-specific`、`adapter-owned` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py:66:76` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py:69:35` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py:89:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/roles/test_checkpoint_shutdown_guard.py:38:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_checkpoint_shutdown_guard.py:88:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_checkpoint_shutdown_guard.py:114:32` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_checkpoint_shutdown_guard.py:124:28` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_checkpoint_shutdown_guard.py:139:32` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_checkpoint_shutdown_guard.py:156:32` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_dsh_resume_storage.py:7:29` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_dsh_resume_storage.py:27:39,57` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_dsh_resume_storage.py:28:51,71` | `adapter`、`registry_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_dsh_resume_storage.py:59:70` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/roles/test_dsh_resume_storage.py:62:24` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_dsh_resume_storage.py:69:29` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_dsh_resume_storage.py:74:35` | `registry_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_dsh_resume_storage.py:98:27` | `registry_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_dsh_resume_storage.py:135:27` | `registry_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_dsh_resume_storage.py:148:27` | `registry_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_dsh_resume_storage.py:181:64` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/roles/test_registered_run_wiring.py:13:47` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_registered_run_wiring.py:14:62` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/roles/test_registered_run_wiring.py:32:23` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/roles/test_registered_run_wiring.py:33:47` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_registered_run_wiring.py:257:23` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_registered_run_wiring.py:267:24` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_registered_run_wiring.py:283:28` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_registered_run_wiring.py:294:28` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_registered_run_wiring.py:311:36` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_registered_run_wiring.py:336:28` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_registered_run_wiring.py:353:32` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_registered_run_wiring.py:371:32` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_role_controller.py:4:27` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/roles/test_role_controller.py:5:45` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/roles/test_role_controller.py:17:47` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_role_controller.py:18:69,86,98` | `CommandAdapter`、`DecisionAdapter`、`DshAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/buddy/roles/test_role_controller.py:125:21` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/roles/test_role_controller.py:128:48` | `CommandAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_role_controller.py:228:77` | `CommandAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_role_controller.py:232:79` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/buddy/roles/test_role_controller.py:245:77` | `CommandAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_role_controller.py:326:67` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_role_controller.py:368:34` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_role_controller.py:369:34` | `Adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_role_controller.py:385:67` | `adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/roles/test_role_prompts.py:11:51` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/roles/test_role_prompts.py:106:34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/roles/test_run_native_evidence.py:114:35` | `DecisionAdapter` | 内部 Router 的执行连接件及其可用性。 |
| `tests/python/buddy/roles/test_schema_worker.py:16:62` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/roles/test_schema_worker.py:38:34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/roles/test_schema_worker.py:45:32` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_live_lifecycle_integration.py:52:83` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_partial_output_safety.py:6:47` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_partial_output_safety.py:19:26` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_partial_output_safety.py:28:18` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_partial_output_safety.py:42:26` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_partial_output_safety.py:52:18` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_repair_recovery.py:26:69,84` | `adapter`、`get_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_repair_recovery.py:27:47` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_repair_recovery.py:101:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_repair_recovery.py:105:43` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_repair_recovery.py:128:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_repair_recovery.py:159:30` | `get_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_repair_recovery.py:330:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_repair_recovery.py:362:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_repair_recovery.py:375:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_repair_recovery.py:389:21` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_repair_recovery.py:390:18` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_repair_recovery.py:391:21` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_repair_recovery.py:410:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_repair_recovery.py:434:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_repair_recovery.py:506:30` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_run.py:4:55` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_run.py:7:62` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_run.py:29:22` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_run.py:64:18` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_worker_invariants.py:56:32` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_worker_invariants.py:124:32` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_worker_invariants.py:154:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_worker_invariants.py:176:55` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_worker_invariants.py:224:24` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_worker_invariants.py:299:37` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_worker_live_wiring.py:35:47` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_worker_live_wiring.py:237:16` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_worker_live_wiring.py:437:41` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_worker_runtime.py:166:43` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_worker_runtime.py:176:48` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_worker_sessions.py:90:48` | `test_codex_list_accepts_actual_stopped_adapter_fixture` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/buddy/runtime/test_worker_sessions.py:91:59` | `CodexAdapterTests` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/buddy/runtime/test_worker_sessions.py:92:24` | `CodexAdapterTests` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/cli/test_host_cli.py:122:64` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/cli/test_host_cli.py:564:27` | `adapter` | 断言旧字段或退役连接件元数据不存在。 |
| `tests/python/cli/test_host_cli.py:570:9,78` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/cli/test_host_cli.py:571:26` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/cli/test_host_cli.py:572:60,77,93` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/cli/test_support_cleanup.py:242:47` | `adapters` | capabilities.adapters 连接件报告，含 command/decision/external。 |
| `tests/python/console/test_console.py:305:18` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/console/test_console.py:408:18` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/console/test_console.py:414:44` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/console/test_console.py:432:40` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/console/test_console.py:449:18` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/console/test_console.py:838:18` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/console/test_console_sessions.py:78:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/console/test_console_sessions.py:242:46` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/fixtures/console_macro_task_board.py:152:51,69` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/fixtures/console_macro_task_board.py:158:93` | `_execution_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/fixtures/console_macro_task_board.py:161:54` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/fixtures/console_macro_task_board.py:162:54` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/fixtures/console_macro_task_board.py:165:54` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/fixtures/console_macro_task_board.py:283:72` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/fixtures/console_macro_task_board.py:312:89` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/install/test_runtime.py:319:27` | `adapterScript` | 断言旧字段或退役连接件元数据不存在。 |
| `tests/python/install/test_runtime.py:616:27` | `adapterScript` | 断言旧字段或退役连接件元数据不存在。 |
| `tests/python/install/test_upgrade.py:173:39` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/install/test_upgrade.py:179:113` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/install/test_upgrade.py:190:106` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/install/test_upgrade.py:311:107` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/install/test_upgrade_migration.py:31:30` | `adapter` | 升级基点的旧列/JSON，或升级结果不含旧键的断言。 |
| `tests/python/install/test_upgrade_migration.py:32:36` | `adapter` | 升级基点的旧列/JSON，或升级结果不含旧键的断言。 |
| `tests/python/install/test_upgrade_service.py:11:41,61` | `adapter`、`CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/install/test_upgrade_service.py:25:37` | `CodexAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/protocol/fixtures/ctwo_controller.py:24:47` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/protocol/fixtures/ctwo_controller.py:216:18` | `AdapterOutcome` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/protocol/fixtures/ctwo_controller.py:240:23` | `adapters` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/protocol/fixtures/ctwo_controller.py:294:43,61` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/protocol/fixtures/ctwo_controller.py:313:22` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/protocol/fixtures/ctwo_controller.py:319:56` | `_execution_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/protocol/fixtures/mock_readonly.py:4:28` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/protocol/fixtures/mock_readonly.py:64:43,61` | `adapter`、`DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/protocol/fixtures/mock_readonly.py:79:43` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/protocol/fixtures/mock_readonly.py:80:43` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/protocol/fixtures/mock_readonly.py:85:43` | `DshAdapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/protocol/fixtures/native_turn.py:5:71` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/protocol/fixtures/native_turn.py:115:27,49` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/protocol/test_activity.py:24:69,84` | `adapter`、`get_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/protocol/test_activity.py:186:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/protocol/test_activity.py:190:46` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/protocol/test_activity.py:298:48` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/protocol/test_activity.py:330:13` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/protocol/test_activity.py:361:30` | `get_adapter` | 连接件模块、类、注册表 API、对象或其实现测试。 |
| `tests/python/protocol/test_activity.py:481:47` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/protocol/test_ctwo_service.py:72:34` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/protocol/test_ctwo_service.py:95:79` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/protocol/test_ctwo_service.py:120:112` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/protocol/test_rpc_config.py:734:93` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/protocol/test_rpc_config.py:780:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/protocol/test_usage.py:317:55` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |
| `tests/python/test_private_directories.py:31:42` | `adapter` | task/attempt/worker、execution-submit 或连接件实现的字段、参数、说明与测试。 |

第二轮完整检查日志为 tmp/python-full-final.log：233 个文件通过（成功文件 3861 项），readonly_tools_probe.py 因两处连接件模块路径被误改为 harness 而失败；恢复 adapter.py 的模块引用后，该文件单独重跑 8 项通过、退出 0，私有根已移除。

补充自查确认 command/external 的权限校验仍须从连接件约束进入；保留这条入口，增加了 full 档位在准入前被拒绝、没有任务落库的检查。permission_levels.py 与 upgrade_fixture.py 在最终源码上共 15 项通过、退出 0，记录见 tmp/permission-upgrade-final.log。最后一轮完整检查日志为 tmp/python-full-verified.log：234 个文件、3870 项均通过，退出 0，私有测试根已移除。

文档同步、安装与 Host 的最终验收仍属于 Host 后续步骤；本微任务未修改其他 docs、skills、README、AGENTS.md 或 CONTEXT.md，未修改版本、schema、分支或标签，没有提交。
