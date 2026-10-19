# ADR-030 批次 4：Codex 候选说明

基点为 `7a5fa88a`，按清单中的路径、函数与错误码定位，不依赖旧行号。此文件记录 Worker 的修改与验证，不代表 Host 验收；2026-10-13 按 Host 打回意见修正模型归属与审阅沙盒能力边界，目录消费者由另一个微任务处理。

| 函数与原错误码 | 类别 | 处理 | 对应测试名（省略测试类前缀） |
| --- | --- | --- | --- |
| `native_run._catalog` / `invalid-catalog`，`data` 非列表 | F | 结束本次分页读取并返回 `discoveries.status=unknown`、缺失原因与警告；运行保存 `catalog-read` 证据，未知目录中未见模型不记成已确认缺席，选择也不记成已确认；原生回合可以继续；服务消费者由另一个微任务处理，见下文。 | `test_unreadable_catalog_continues_as_unknown_but_unconfirmed_stop_still_refuses`、`test_unreadable_catalog_continues_worker_turn_without_confirming_selection` |
| `native_run._structured_rounds` / `no-tool-violation` | F | 删除固定启动项清单；先绑定回合身份，再把启动项送入已有工具投影与分类器；无法读取的项容器与项类型记录未知事件，并使工具证据不完整；工具项记录实际工具事实。 | `test_unfamiliar_startup_items_continue_and_reach_result_facts`、`test_review_unknown_item_shapes_continue_through_bounded_capture` |
| `native_run._drain_to_eof` / `invalid-native-result`，未知帧形状 | F | 未知帧继续经已有事实链处理，记录 `unknown-method`，正常 EOF 仍记录完整流；迟到响应的归属拒绝保留。 | `test_unfamiliar_drain_frame_continues_and_retains_unknown_shape`、`test_unknown_frame_shape_does_not_hide_explicit_foreign_identity` |
| `protocol.Connection._pump_one` / `invalid-protocol`，未知帧形状 | F | 原始有界帧原样进入通知回调，以保留显式会话/回合身份供载体检查；分类器支持非字符串方法，未知类型与次数进入事实及 `unknown-events` 证据。 | `test_unknown_frame_shapes_reach_classifier_and_call_continues`、`test_unknown_frame_shape_does_not_hide_explicit_foreign_identity` |
| `protocol.Connection.call` / `invalid-protocol`，非对象结果 | F | 记录有界方法名 `rpc/result-unreadable/<method>`，不保留未知原值，交给调用方空的可读取字段；需要会话/回合身份的收据仍单独拒绝缺失身份；目录读取把不可读结果记为 unknown，并通过警告保留事实。 | `test_unreadable_rpc_result_is_a_bounded_fact_and_call_continues`、`test_discovery_unreadable_rpc_result_retains_bounded_fact_without_a_turn`、`test_fast_ownership_and_history_still_refuse`、`test_unreadable_interrupt_reply_does_not_confirm_acknowledgement` |
| `config.prepare_no_tool_home` / `no-tool-policy-unverified` | G | 已随批次 3b 删除，当前 `config.py` 无缓存改写与该函数；本次无代码可改。 | 既有 `test_absent_user_model_cache_does_not_refuse_a_native_catalog` |
| `native_run._configure_review` / `readonly-policy-unverified`，配置回读 | G | 保留实际配置回读；读得出的请求控制项不符时，以原码在模型开始前拒绝；缺失或不可读时保留 unknown 并继续；不保存 MCP 参数、凭据或完整用户配置。 | `test_review_policy_mismatches_refuse_with_actual_readbacks_before_a_turn`、`test_different_review_config_controls_refuse_with_actual_readback`、`test_unreadable_policy_shapes_continue_as_unknown` |
| `native_run._configure_review` / `readonly-policy-unverified`，权限回执 | G | 实际回执先保存；sandbox 非对象或 type 不是 readOnly 属于本次运行未确认系统只读沙盒能力，在模型开始前仍以原码拒绝；权限档案标识、审批策略及 networkAccess 读得出而不符时以原码拒绝，缺失或读不懂则记 threadReceipt unknown 并继续；配置回读按同样的区别处理。 | `test_review_without_read_only_sandbox_is_refused_before_model_input`、`test_review_sandbox_capability_still_refuses_missing_or_non_read_only_type`、`test_unreadable_review_receipt_restrictions_continue_as_unverified`、`test_different_review_receipt_restrictions_refuse_before_a_turn`、`test_missing_network_readback_keeps_review_restriction_unconfirmed`、`test_read_only_refuses_a_conflicting_policy_receipt` |
| `native_run._configure_fast` / `no-tool-policy-unverified`，配置回读 | G | 保留实际 web_search 与 approval_policy 回读，差异记 unknown；使用已有 evidence_refs 与 native_evidence 出口给角色及 Host；回合继续。 | `test_unverified_no_tool_policy_continues_with_readback_facts`、`test_policy_readback_differences_and_deadline`、`test_large_unverified_policy_fields_keep_bounded_projectable_facts` |
| `native_run._prepare` / `no-tool-policy-unverified`，时限 | C / merge | 删除重复检查；`roles.run_execution.start_fast` 保留 1 至 60 秒的调用边界，`RunBudget` 保留通用预算类型边界。 | 既有快速调用与 deadline 测试（`test_policy_readback_differences_and_deadline`、`test_deadline_stops_reports_interrupt_and_removes_private_auth`） |
| `native_run._configure_worker` / `native-resume-unavailable`，前一会话参数 | C / merge | 删除重复空值检查；`RunContinuation._native_resume_names_its_session` 在请求构造时校验；私有绑定、检查点、最新原生回合与续接回执的归属检查保留。 | 既有 `test_native_session_resume_reuses_the_bound_thread`、`test_a_matching_resume_checkpoint_resumes_the_bound_thread` |
| `native_run.run` / `INVALID_ARGUMENT`，回合身份与摘要 | C / merge | 删除重复检查；`WorkerRunExecutor.prepare` 校验受治理输入与 turn_id，`worker_request` 在输入边界构造身份并计算 input_hash；harness 与服务绑定类型检查保留。 | 既有 `test_completed_worker_turn_reports_checkpoint_binding_and_activity` |
| `native_run.prepare_run_services` / `INVALID_ARGUMENT`，工具范围 | C / merge | 删除重复枚举；`RunRequest.tool_scope` 保留输入边界；函数仍返回本 harness 的 `RunServices`。 | 既有快速、审阅、Worker 各载体测试 |
| `native_run._catalog` / `invalid-catalog`，累计项数 | I | 资源上限保持拒绝，累计超过 200 项仍失败；与页形状未知分开。 | `test_catalog_count_budget_still_refuses` |
| `native_run._catalog` / `invalid-catalog`，游标 | I | 非空字符串以外的游标形状使读取未知并结束分页；重复游标属于已观察到的分页不前进，仍拒绝；服务消费者由另一个微任务处理，见下文。 | `test_catalog_unreadable_cursor_stops_reading_with_unknown_fact`、`test_catalog_repeated_cursor_still_refuses` |
| `native_run._configure_review` / `readonly-configuration-mismatch` | I | 拆开实际模型与检出；模型缺失或非字符串使 selectedModel unknown、checked.model 留空并继续；字符串模型与请求不同在模型开始前以原码拒绝，比较发生在有界证据投影之前；顶层及线程检出、非空线程身份仍拒绝；固定供应方限制已随批次 3b 删除；本次另比较回执供应方与请求，读得出而不同同样以原码拒绝。 | `test_unreadable_review_model_continues_with_unknown_checked_model`、`test_different_review_model_is_refused_before_model_input`、`test_different_native_model_still_refuses_even_beyond_readback_bound`、`test_review_checkout_and_thread_ownership_still_refuse` |
| `native_run._configure_fast` / `no-tool-policy-unverified`，线程回执 | I | 线程身份、检出及历史归属仍拒绝；字符串模型与请求不同在模型开始前以 configuration-mismatch 拒绝；模型缺失或非字符串记录 unknown、checked.model 留空并继续；供应方字符串与请求不同同样在模型开始前以 configuration-mismatch 拒绝，缺失或不可读的 checked.provider 留空；审批回读仍保留实际值与未证实事实，快速判定不增加沙盒能力检查。 | `test_different_no_tool_model_is_refused_before_model_input`、`test_unreadable_no_tool_model_continues_with_unknown_checked_model`、`test_fast_ownership_and_history_still_refuse`、`test_fast_unverified_approval_continues_and_retains_receipt`、`test_policy_readback_differences_and_deadline` |
| `protocol.Connection._pump_one` / `invalid-protocol`，响应身份及积压 | I | 分开整数响应身份、严格模式的请求关联/重复回复与 16 条积压上限；三者都守归属或数量边界，全部继续拒绝。 | `test_response_identity_is_checked_independently_of_backlog`、`test_strict_response_association_still_rejects_foreign_and_duplicate_ids`、`test_response_backlog_still_has_a_quantity_cap` |

## 已找不到的旧位置

上表的 `config.prepare_no_tool_home` 已删除。清单里额外的 I / delete 行 `native_run._coding_home` / `<dynamic>` 也已随批次 3b 删除；它不属于本次分拆的五个 I 条件。审阅与快速回读中原来围绕私有用户配置层、MCP、固定供应方与原环境的条件已在批次 3b 移除，本次只处理现存条件。

## 由另一个微任务处理的范围外位置

`blackboard/service/service.py::_refresh_harness_catalog` 与 `blackboard/catalog/catalog.py::discover` 把原生 discovery 的 status 写成 complete 的问题已由 Host 确认，2026-10-13 的继续指令指定另一个微任务修复。本任务保留 Codex 的 unknown 状态、reason 与事实，不改这两个范围外文件，也不再因其停止；服务端状态传播由 Host 结合另一片验收。

`_initialize` 与 `run_discovery` 中 config/read 的 config 缺失或不是对象时，本次目录读取为 unknown，不给供应方与模型；读得懂的空配置继续使用原生默认供应方，由 `test_unreadable_initial_config_keeps_catalog_unknown_and_continues`、`test_unreadable_config_never_invents_a_catalog_provider` 与 `test_readable_empty_config_still_uses_native_default_provider` 验证。

字段事实也有预算：每个允许保留的原生回读字段最多 64 KiB，超过时只保留 unknown、被省略的原因与字节数，保证每份证据能在现有 512 KiB 的读取预算内投影；8 MiB 原生帧输入上限没有放松。缺少 networkAccess 不确认禁网，不可读的 turn/interrupt 回答不确认中断；审阅捕获和原生观察器遇到非对象 item 只记录无法解读的事实，最终消息与唯一终态的归属检查仍单独守住。这些问题分别由 `test_large_unverified_policy_fields_keep_bounded_projectable_facts`、`test_missing_network_readback_keeps_review_restriction_unconfirmed`、`test_unreadable_interrupt_reply_does_not_confirm_acknowledgement`、`test_review_unknown_item_shapes_continue_through_bounded_capture` 验证。

Worker 沙盒参数及 `_admit_turn` 的 Worker 策略事实保持原样。摘要数组超限与 Router 的服务端事后判定没有修改；无工具或审阅回合继续不等于角色已认可答案或证明限制实际生效。真实原生失败、终态和最终消息证据、进程停止不明、凭据和私有路径、调用参数、响应与目录数量上限保持原来的拒绝。

## 验证

测试使用 Host 指定的只读既有解释器，在仓库根目录设置 `PYTHONPATH=src:tests/python`，清除 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`、`BUDDY_RUNTIME`、`BUDDY_RUNTIME_IDENTITY`、`BUDDY_WORKER_STATE`、`BUDDY_WORKER_ID`、`BUDDY_AGENT_CREDENTIAL`、`BUDDY_AGENT_CREDENTIAL_FILE`、`VIRTUAL_ENV` 与 `UV_PROJECT_ENVIRONMENT` 后运行，不安装依赖、不修改该环境。所有原生进程均为测试私有主目录下的离线 fixture。

2026-10-13 修订后的源码使用 `<existing-python> -m unittest discover -s tests/python/buddy/harnesses/codex -t tests/python` 运行整个 Codex 测试目录：210 项，87.552 秒，全部通过，无跳过；`git diff --check` 通过。没有因环境限制未跑的 Codex 目录内测试。完整仓库检查、真实 Codex 回合与故障注入未由本 Worker 运行；Host 在打回指令里报告的整合树检查不替代这次修订后源码的验收。

## Host 不调用模型的真实读取核对

Host 在其自己的验收环境中从此检出根目录运行下面的脚本；把 `<existing-python>` 换成依赖齐全的解释器，把 `<codex-executable>` 换成准备验收的原生 Codex 路径。先清除上列继承的 Buddy 运行时与凭据变量，然后临时设置 `BUDDY_DEV_SOURCE=1`、`BUDDY_CODEX_CLI=<codex-executable>` 与 `PYTHONPATH=src`。这会沿用 Codex 原生配置，只发 initialize、account/read、config/read 和 model/list，不发送 thread/start 或 turn/start，不登录、不启动 Buddy 服务。

```sh
<existing-python> - <<'PY'
import json
import tempfile
from pathlib import Path
from hey_my_buddy.buddy.harnesses.codex.native_run import run_discovery

with tempfile.TemporaryDirectory(prefix="codex-reading-probe-") as temporary:
    root = Path(temporary)
    catalog = run_discovery(cwd=str(Path.cwd()), invocation_root=root / "reading",
                            native_root=root / "native", timeout_seconds=30,
                            cancelled=lambda: False)
    print(json.dumps({"harnessVersion": catalog["harnessVersion"],
                      "discoveries": catalog["discoveries"],
                      "modelCount": sum(len(p["models"]) for p in catalog["providers"]),
                      "unknownEvents": catalog.get("unknownEvents"),
                      "warnings": catalog["warnings"]}, ensure_ascii=False, indent=2))
PY
```

正常原生形状应照常得到 complete 目录；出现未读懂的页或游标则得到 unknown 与原因，同时正常完成确认停止。没有确认停止或原生读取明确失败仍抛出真实错误。输出只汇总版本、读取状态、模型数量与有界未知事件，避免输出账户详情或用户配置。这个核对直接验证 Codex 读取路径；修复上文消费者后，Host 还需核对服务保存的状态仍为 unknown。

## 收尾

本片以 `98cb1536` 为输入，接入已合入的共用结果约定。`native_run._build_result` 把 `_RunFacts.unknown_counts` 的有界类型计数和总数写入 `RunResult.native_evidence.unknown_events`；把 `policy_checks.configReadback`、`threadReceipt` 的 unknown 分别写为 `settings-readback`、`thread-receipt`，本次 `catalog_read.discovery.status` 为 unknown 时写为 `model-catalog`。`selectedModel` 未证实仍由空的 checked 模型及原有 policyCheck 表达，不新增短名字；确认的读取和未产生的事实不列为未证实项。`native_evidence(result)` 删除重复的 `unknownEvents` 投影，保留 Codex 的 `policyCheck`、`catalogRead`、配置与线程回读；`unknown-events.json` 及其证据引用继续保存。对应 `NativeFactsTests.test_shared_native_facts_reach_all_three_carrier_results_without_duplicate_keys` 经三个载体的构建及真实角色投影确认共用两项只在 `nativeEvidence` 下出现，`test_confirmed_reads_and_unreadable_selected_model_add_no_unconfirmed_name` 确认空事实不进入角色结果，`NoToolCodexTests.test_unknown_event_keeps_a_valid_answer` 验证控制器编码与采集后的快速结果。

`_FastProtocol.__call__` 按协议实际位置读取显式会话标识：普通通知检查 `params.threadId`，`thread/started` 另检查 `params.thread.id`。嵌套标识是字符串且不同于本会话时仍拒绝；嵌套标识为本会话或读不出标识时继续检查顶层标识，顶层没有标识则放过。参数不是对象或不含顶层标识时放过；顶层键存在但不同（包括 null）时仍拒绝；属于本会话的消息继续执行原有回合标识、重复开始、重复最终答案、重复完成和真实错误检查。固定三种连接通知的提前返回与 opaque 形状的专门返回都由这一归属规则取代；带身份的连接通知也经过同一检查。分类器仍先记录未知通知。两个离线夹具在 `account/read` 回答之后都发送不含会话标识的 `account/updated`，无工具夹具的 `connection-notification` 情形在回合开始后再发一条；更新原有未知事件计数断言，超过类型数上限时仍保留 64 种与精确总数。对应 `FastSeamTests.test_connection_unknown_notifications_before_thread_and_during_turn_keep_answer` 验证启动阶段与回合中的两条通知不丢答案、共用事实与证据文件相符；`NativeFactsTests.test_fast_connection_notifications_without_thread_identity_continue` 覆盖非对象、空对象及只有回合标识的参数；`test_thread_started_checks_nested_session_identity` 覆盖真实嵌套形状的外来、本会话及无法读取标识，`test_fast_connection_notification_names_do_not_bypass_explicit_identity`、`test_unknown_frame_shape_does_not_hide_explicit_foreign_identity` 和 `FastSeamTests.test_explicit_foreign_identity_is_refused_with_method_in_message` 保留显式外来会话及回合的拒绝，`WorkerSeamTests.test_unknown_event_types_merge_at_the_public_bound` 核对有界计数出口。

`run_discovery` 删除把所有未知事件逐种追加为目录警告的循环，仍在返回值的 `unknownEvents` 保留计数；目录页、游标或调用结果读不懂时，原有读取状态、原因和读取警告继续返回。对应 `DiscoverySeamTests.test_discovery_reads_the_catalog_without_a_prompt_or_turn` 确认收到 `account/updated` 后目录仍为 complete 且 warnings 为空；`test_discovery_unreadable_rpc_result_retains_bounded_fact_without_a_turn` 和 `test_unreadable_catalog_continues_as_unknown_but_unconfirmed_stop_still_refuses` 确认读不懂的答复或页仍为 unknown 并保留读取警告；`NativeFactsTests.test_catalog_unreadable_cursor_stops_reading_with_unknown_fact` 保留游标形状验证。`_initialize` 与 discovery 的 config/read 处理、`_admit_turn`、`_configure_worker`、`_mode` 和 `run` 的权限拒绝均未修改。

2026-10-13 首轮使用任务指定的只读既有解释器，以本检出的 `src:tests/python` 为导入路径；清除继承的 runtime、Worker、agent 凭据及环境钉住变量后，使用 `cli.checks.child_environment` 为每个测试文件建立独立的私有 HOME、四个 harness 配置根、状态根、运行时根及临时目录。每个文件单独一个进程：`buddy.harnesses.codex.test_batch4_facts` 20 项、`test_native_run` 94 项、`test_no_tool_codex` 13 项、`test_codex` 36 项、`test_codex_continuation` 7 项、`test_codex_tool_evidence` 24 项、`test_codex_protocol` 22 项，共 216 项全部通过，无跳过；`git diff --check` 通过。新增三载体测试首次因夹具没有创建私有证据目录而失败，补齐夹具目录后整个文件通过，未以放宽证据写入规则处理。上述文件没有因套接字、共享内存或真实启动器限制而未跑的测试；运行的原生进程全部为离线夹具。完整仓库检查、真实 Codex 回合、实际故障注入及 Host 最终验收尚未运行，本片没有启动服务、读取日常看板、读取用户 harness 配置或发起模型调用。

2026-10-13 按 Host 第一次打回修订：`configReadback` 的未证实项改为 `settings-readback`，明确指原生设置回读；模型与推理强度仍只由 checked 配置表达。补回 `thread/started` 在 `params.thread.id` 中的显式归属检查，没有恢复固定通知清单；新增 `NativeFactsTests.test_thread_started_checks_nested_session_identity`，并在离线夹具及 `FastSeamTests.test_explicit_foreign_identity_is_refused_with_method_in_message` 中加入真实嵌套形状的外来会话通知。修订后的七个文件仍各用独立进程和上述私有环境运行：`test_batch4_facts` 21 项、`test_native_run` 94 项、`test_no_tool_codex` 13 项、`test_codex` 36 项、`test_codex_continuation` 7 项、`test_codex_tool_evidence` 24 项、`test_codex_protocol` 22 项，共 217 项全部通过，无跳过；`git diff --check` 通过。没有受影响测试因环境限制未跑，完整检查、真实回合、实际故障注入和最终验收仍留给 Host。

## 整批独立审查后的四处修正（2026-10-13）

供应方：`_configure_worker`、`_configure_fast`、`_configure_review` 在比较回执的原始 `modelProvider` 字符串与请求之后才保存有界 checked 值；Worker 和快速调用以 `configuration-mismatch` 拒绝不同供应方，审阅以 `readonly-configuration-mismatch` 拒绝，均发生在 `turn/start` 之前。Worker 改为读取 start/resume 回答顶层的供应方，与两种 Router 使用同一个原生字段；离线夹具也删除线程内的供应方读法，不保留两套形式。缺失或非字符串不拒绝，checked.provider 留空，目录未确认模型或推理强度也不再能掩盖供应方冲突。对应 `NativeFactsTests.test_different_provider_refuses_even_without_catalog_confirmation`、`test_unreadable_provider_continues_with_empty_checked_provider`、`FastSeamTests.test_fast_provider_mismatch_refuses_and_unreadable_provider_continues`、`ProviderReceiptSeamTests.test_worker_and_review_provider_mismatch_refuses_before_model_input`、`test_worker_and_review_unreadable_provider_continues`、`test_resumed_worker_provider_mismatch_refuses_before_another_turn`；覆盖空字符串、超出证据投影预算的字符串、初次运行与续接，并检查拒绝之后没有新增回合请求。

工具证据：`CodexToolEventProjector._typed` 与 `_raw` 的非对象 item 或非字符串类型不再直接忽略，改走已有 `_incomplete`；`_structured_rounds` 读不懂启动 items 时保留原有未知事件，同时用启动回执的真实线程与回合身份记录不完整工具事实。没有增加证据字段，没有把读得懂但不认识的工具类型改成零工具；连接及线程级未知通知继续只记事实。对应 `UnreadableCodexToolEvidenceTests.test_unreadable_typed_and_raw_items_cannot_prove_no_tools`、`test_unknown_connection_and_thread_notifications_preserve_tool_completeness`、`FastSeamTests.test_unreadable_startup_items_invalidate_fast_tool_evidence`：即使回合完成并排干流，不可读 item 的 streamComplete 仍为 false，现有 `judge_tool_evidence` 不采用该快速答案；`test_connection_unknown_notifications_before_thread_and_during_turn_keep_answer` 保留正常无工具答案的路径。

配置与目录：`_initialize`、`run_discovery` 删除把不可读 config 当成空配置并据此使用默认供应方的处理；`_catalog` 在供应方无法确定时返回 unknown、`config-shape-unreadable` 原因及配置读取警告，providers 与 listed_ids 为空，发现结果的 billingByProvider 也为空。原生 model/list 仍照常读取，所以真实 RPC 失败、数量上限及不前进的分页继续拒绝；进程停止确认保持原样。运行可以继续，checked 供应方取线程回执，模型与推理强度不伪称已经过目录确认，并留下 `model-catalog`；可读的空配置或原生默认供应方空值仍沿用 openai。对应 `DiscoverySeamTests.test_unreadable_config_never_invents_a_catalog_provider` 覆盖非对象 RPC 结果、非对象 config 与缺失 config，`NativeFactsTests.test_unreadable_initial_config_keeps_catalog_unknown_for_every_carrier` 覆盖三种载体，`WorkerSeamTests.test_unreadable_initial_config_keeps_catalog_unknown_and_continues` 验证结果出口，`test_readable_empty_config_still_uses_native_default_provider` 与既有三载体 `test_default_and_configured_provider_reach_*_thread_start` 验证默认名未改变。`Connection.call` 的有界未知结果事实保持原样，本次不改变通用 RPC 的返回形式。

审阅限制：`config.policy_status` 取代二值的 `policy_matches`，分别表示确认、缺失或不可读、读得懂的冲突；请求的配置项逐项比较，文件授权表额外的可读授权也判为冲突，默认附带的空值元数据继续不构成授权。`_configure_review` 先保存实际线程回执及配置回读，确认系统只读沙盒的要求保持原样，再把可读的档案标识、审批策略、networkAccess 或请求控制项冲突以 `readonly-policy-unverified` 拒绝；未知字段仍以 `thread-receipt`、`settings-readback` 留下并继续。对应 `NativeFactsTests.test_different_review_receipt_restrictions_refuse_before_a_turn`、`test_different_review_config_controls_refuse_with_actual_readback`、`test_unreadable_review_receipt_restrictions_continue_as_unverified`、`test_unreadable_policy_shapes_continue_as_unknown`，以及 `ReviewSeamTests.test_review_policy_mismatches_refuse_with_actual_readbacks_before_a_turn`；原来“明确冲突仍继续”的适配器测试改为 `CodexAdapterTests.test_read_only_refuses_a_conflicting_policy_receipt`、`test_config_readback_conflict_refuses_before_model_input`。大字段事实测试改用不可读类型，明确的字符串冲突继续在证据截断之前判定；快速调用的限制回读仍只作为事实，没有新增限制拒绝。

四处均先运行失败测试，再修改源码：目录配置的三种不可读回读、启动 items 的三种不可读载体、Worker/审阅供应方及 Worker 续接、审阅网络回执和配置回读均在输入源码上未能达到新断言。随后按任务指定的只读既有解释器及本检出的导入路径，以 `cli.checks.child_environment` 为每个测试文件建立私有主目录、harness 配置根、状态与运行时根，并清除继承的 runtime、Worker、agent 凭据及环境钉住变量；每个文件独立进程，最多三个文件同时检查。最终 Codex 七文件 234 项全部通过：test_batch4_facts 27、test_native_run 103、test_codex 36、test_codex_continuation 7、test_codex_protocol 22、test_codex_tool_evidence 26、test_no_tool_codex 13；roles 的 15 文件 157 项全部通过；blackboard/routing 的 33 文件 531 项中 530 项通过。适配器新断言首次误用运行模块的 error 状态名，改为既有 AdapterOutcome.failed 后重跑整个 test_codex 文件 36 项通过，没有改变共用结果或采集模块。

环境限制仅一项：`blackboard.routing.test_readonly_tools_probe.ReadonlyToolsProbeTests.test_only_one_execution_is_possible_for_the_prepared_packet` 无法完成私有测试根的进程观察与回收验证，检查报告 `ps process enumeration failed; residue status is unknown`，因此返回 failed；它没有执行真实模型。用输入提交 `80064b22` 的 src 在另一独立私有测试进程重跑相同文件，复现同一项失败与同一观察原因，其余七项通过。未改测试或检查器来绕过限制，Host 需在可执行进程枚举的环境重跑这一文件。其余要求的测试都实际执行，没有因套接字、共享内存或启动器而跳过；原生进程都是离线夹具。`git diff --check` 通过，修改仅在指定三个范围内；Worker 的 `_mode`、`_worker_sandbox`、`_admit_turn` 权限分支、共用模块与其他 harness 没有修改。完整检查、真实 Codex 回合、实际故障注入和最终验收留给 Host；范围外的 decision.md 审阅限制说明仍是上一轮文字，需要 Host 随整合更新。
