# ADR-025 第四步格式瘦身：四个原生运行模块测试的断言迁移

## 状态

已完成待验收：Worker 在受管独立 worktree（基线 dc8d77d96cd244dd9f959e6ef7f5c773c3d2d97b）完成四个 `test_native_run.py` 的旧字段断言迁移，未新增、删除或改名任何测试，四份对照与证据清单见本文与 [test-ids 表](adr025-step4-format-tests-test-ids.tsv)。

## 背景与任务边界

Host 在 dc8d77d 按 [step3 格式使用清单](adr025-step3-format-usage.tsv) 删除了无生产读取方的运行结果字段：`ModelStartEvidence`、`DeniedInteraction`、`UnknownEvents` 整类，以及 `RunEnd.signal`、`ResultConfiguration.checks`、`CheckedValue.basis/source`、`RunValue.mechanism/validation_basis`、`CompletionEvidence` 除 `stream_end` 外的全部组成、`PolicyFact.enforcement/basis`、`EffectivePolicy.filesystem`、`NativeIdentity.thread_id/input_id/call_id`、`RunResult.root_identities`、`ContinuationFacts` 除 `resumable` 外的组成、`StopLayer` 除 `group_state` 外的全部组成。

本任务只允许写四个 `tests/python/buddy/harnesses/{zcode,codex,claude,dsh}/test_native_run.py` 与本验收记录两文件；源码、公共值、roles、registry、其他测试与 fixture 归 Host。迁移不重建兼容格式、不新造生产读取方：每条旧断言要么改读实际产生的对应事实（`evidence_refs` 中经 size/sha256 校验的保留文件、observer 实际收到的累计统计、`tool_evidence` 的原生根集合、`end.native_exit_code`），要么因"仅断言已删字段本体、无运行语义"而删除并逐条写明理由。

## 迁移原则与保留的防护

保留语义优先级依次为：签收验签与根身份、native group unknown 的真实进程测试、被拒交互与未知事件的直接事实、其余载体与停止语义。四份文件的既有防护测试全部保留其原有测试主体与真实假进程路径：zcode 与 codex 的"未确认组停报告 unknown"仍以真实停顿后拒绝确认为前提，dsh 的 `test_a_tampered_finish_receipt_is_fatal` 与 `test_a_forged_checkpoint_receipt_fails_the_turn` 仍走真实签名校验，claude 的结构化交付豁免族仍以共享包的排除接缝为唯一判定。

替换事实的四个来源：一、`result.evidence_refs` 中实际产生且 size/sha256 匹配的 `turn-provenance`（zcode/dsh 的签收回执事实：`receiptVerified`、`receiptId`、`toolCallId`、`stopReason`）、`denied-interactions`（zcode/codex/claude 的被拒交互全记录）、`acp-connection-facts`（dsh 持有连接的证明）与 `review-config-readback`（codex 审阅载具的文件系统授权 readback）；二、observer 实际收到的累计统计（`unknownEvents.countsByType/total`、`deniedInteractions` 计数），以包装 observer 捕获；三、`tool_evidence` 包的原生根集合 `nativeIdentity`（替代 `root_identities` 的全部用途）；四、`RunEnd.native_exit_code`（替代 `StopLayer.exit_code` 的存活用途，含 SIGTERM 的 -15）。

## 各文件断言迁移与删改理由

### zcode（37→37，编号不变）

`test_a_settled_answer_is_a_final_message_fact_with_role_checked_schema`：`value.mechanism` 删（final-message 载体是该路径唯一写 `value.raw` 的分支，raw 断言保留载体语义）；`assertIsNone(result.unknown_events)` 改为捕获 observer 统计断言每次通知 `unknownEvents.total == 0`；`stop.native.exit_code` 改读 `end.native_exit_code`；`policy.basis` 删（`PolicyFact` 仅剩 requested；tools 事实本身仅在 session create 被接受后存在，`toolAllowlist` 断言保留该语义）。

`test_one_format_correction_runs_a_second_session_on_the_same_process` 与 `test_a_second_correction_feedback_is_executed_and_stays_bounded`：`len(root_identities)` 改读 `tool_evidence.value["nativeIdentity"]` 长度（2 与 3），同一事实的原生集合来源。

`test_a_write_scope_without_a_service_takes_the_final_message_path` 与 `test_a_read_scope_reports_unrestricted_honestly`：`enforcement == "unrestricted"` 改为 tools 事实存在且 `requested is None`（write/read 范围不发送原生策略请求，空 requested 即该事实的如实形状，不再有结论字段）。

`test_a_failed_connection_setup_still_stops_the_spawned_child`：`native.started`、`observation_basis`、`exit_code` 三条删——spawn 助手记录的子进程退出码随 StopLayer 瘦身不再进入结果任何字段；幸存断言为 end error/`invalid-native-result` 与 group gone（见"接口缺口"节）。

`test_an_unconfirmed_native_group_stop_reports_unknown_not_gone`（真实进程防护）：`observation_basis`、`started`、`leader_exited` 删；group unknown、end error/`native-shutdown-failed`、两次确认拒绝、interrupt `owned-group-signal`、以及停后 `handle.process.poll()`/`shutdown_confirmed`/`group_alive` 真实复核全部保留；exit 事实改 `end.native_exit_code` 非空，leader 被收尸由 handle 复核保留。

`test_a_reverse_request_before_the_rpc_reply_stops_after_the_refusal` 与 `test_a_reverse_request_ends_the_fast_run_immediately_with_the_fact_kept`：`denied_interactions` 改读 retained `denied-interactions` 文件（前者加 size/sha256 校验，记录数与方法名断言保留）。

`test_a_failed_close_keeps_the_final_message_facts`：mechanism 删；`root_identities[0].turn_id` 改为包根集合中同 sessionId 条目的 `turnId`。`test_a_pre_spawn_failure_claims_nothing_that_never_happened`：`model_start_evidence.basis` 删（`model_started` None 保留）；`configuration.checks == ()` 改 checked 三值全 None；`observation_basis`/`started` 改 `end.native_exit_code` None 加 group gone。

Worker 侧：`test_one_unrepresentable_package_keeps_every_other_observed_fact` 的 mechanism 删（parsed 钉住载体）、`receipt_verified` 改 provenance `receiptVerified`；`test_a_post_configure_failure_keeps_every_reached_stage_fact` 的 checks 元组删（checked readback 值保留语义）、`model_start_evidence.basis` 删；`test_a_governed_turn_settles_with_a_verified_completion_tool_value` 的 mechanism 两条删，`receipt_verified`/`call_id` 改 turn-provenance（加 size/sha256 校验）的 `receiptVerified`/`receiptId`/`toolCallId`；`test_a_failed_close_keeps_the_observed_completion_tool_facts` 的 `call_id == "root-call"` 与 `receipt_verified` 删（失败 close 不保留 turn-provenance，既有证据种类子集断言钉住该事实），`completion.native_identity.turn_id` 改 `result.native_identity.turn_id`；`test_foreign_same_name_finish_calls_stay_facts_only_verified_delivery_leaves` 与 `test_every_carrier_reports_task_tool_facts_with_delivery_kept_separate` 的签收事实改 provenance（`evidence_call` 帮助函数改读 `toolCallId`）。

### codex（37→37，编号不变）

`test_a_settled_no_tool_answer_is_a_final_value_fact`：mechanism 删（raw 钉住）；`model_start_evidence.basis` 改 `model_started` True；`stop.exit_code` 改 `end.native_exit_code`；`policy.enforcement == "native"` 删（requested 私有 no-tool 配置块断言保留，零工具完成事实保留接受语义）。`test_one_format_correction_runs_a_second_turn_on_the_same_thread`：roots 改包 `nativeIdentity` 长度 2。

`test_unknown_events_stop_as_invalid_protocol_through_the_observer`：`unknown_events.counts` 改捕获 observer 统计（`{"countsByType": {"futureThing": 1}, "total": 1}`）；`fast_run` 加可选 observer 参数。`test_a_denied_interaction_is_refused_then_stops_the_run`：`denied_interactions` 两条删（`.action == "refused-jsonrpc-error"` 是投影构造常量，原生记录只有 method/threadId/turnId），retained 文件断言加 size/sha256 与记录数。

`test_a_drained_fast_stream_keeps_its_eof_fact_without_a_confirmed_stop`：`stop.started` 与 `completion_evidence.native_outcome` 删（stream_end True、包 streamComplete、end error/`native-shutdown-failed` 保留 EOF 与 unknown 两个事实）。`test_deadline_stops_reports_interrupt_and_removes_private_auth` 与 `test_cancelled_run_reports_cancel_and_native_interrupt`：`end.signal == "SIGTERM"` 改 `end.native_exit_code == -15`（同一事实的存活编码）。`test_an_unrunnable_executable_reports_spawn_never_happened` 与 `test_a_preparation_failure_reports_known_not_started_facts`：`observation_basis`/`started`/`leader_exited` 删，加 `end.native_exit_code` None 与 group gone（未 spawn 的"无退出码"事实保留区分）。

审阅侧：`test_a_completed_review_stream_stays_complete_without_a_confirmed_stop` 的 `started`/`exit_code`/`native_outcome` 改删，roots 改包长度 1；`test_a_review_stream_without_a_final_state_stays_incomplete` 的 `native_outcome` None 改 `stream_end` None；`test_a_review_call_reports_policy_readback_roots_and_raw_answer` 的 roots 改包集合、`enforcement` 删、`effective_policy.filesystem` 改读 retained `review-config-readback` 的 `permissions.buddy-router.filesystem` 中本检出目录的授权项、`"config-policy-readback" in configuration.checks` 删（readback 文件即该事实）；`test_review_correction_is_once_and_never_for_enum_violations` 的 roots 改包长度。

Worker 侧：`test_completed_worker_turn_reports_checkpoint_binding_and_activity` 的 `continuation.binding_ref` 文件存在断言删（`resumable` 由绑定保存计算，`bindingSaved` 与 `resumable` 两条保留）；`test_denied_native_requests_are_facts_on_a_completed_worker_turn` 的 `denied_interactions` 两条删，retained 文件加 size/sha256、记录数与 method；`test_worker_unknown_events_are_counted_never_failed` 与 `test_unknown_event_types_merge_at_the_public_bound` 改捕获 observer 统计（后者以 `list(counts)` 切片断言插入序，上界桶 `(unlisted-native-events)` 语义不变）；`test_worker_tool_facts_are_live_public_evidence` 的 roots 改包集合（含 `turnId == "native-turn-1"`）；`test_unconfirmed_native_group_stop_reports_unknown_not_gone` 的 `observation_basis`/`started`/`leader_exited` 删（leader 收尸由 handle 复核保留）。

### claude（49→49，编号不变）

`test_one_write_run_delivers_the_native_schema_value_and_stop_facts`：`value.mechanism`、`completion.mechanism/native_outcome/event_order`、`model_start_evidence.basis`、`stop.started` 删；`model_start_evidence.native_identity.session_id` 与 `root_identities[0].session_id` 两条合并为"`native_identity.session_id` 等于 CLI 实际收到的 `--session-id`"加"`tool_evidence` 包根集合等于该 session"；`stop.exit_code` 改 `end.native_exit_code`。`test_the_catalog_check_confirms_only_what_native_readback_proved`：两条 `basis` 与 checks 元组删（checked 三值断言保留"只报证实值"的语义）。`test_a_model_outside_the_catalog_is_refused_before_the_user_message`：`basis`/checks 删，checked 三值 None 保留。

`test_a_denied_native_permission_is_a_reported_fact_not_an_outcome`：`denied_interactions` 三元组删（`can_use_tool` 是方法名、`deny` 是驱动固定应答，均非保留事实），retained `denied-interactions` 文件断言扩为 `toolName == "WebFetch"` 加 `requestId == "perm-1"`。`test_one_review_read_takes_the_role_requested_posture_and_reports_it`：`tools.enforcement` 与 `filesystem.enforcement` 删（requested 块与 argv/settings 沙箱断言保留实际姿态事实）。`test_the_shared_send_wait_drain_serves_both_carriers`：`native_event_count == completion.event_order` 改 `native_event_count >= 1`（event_order 已删，计数事实保留下界断言）。

`test_third_party_provider_overrides_are_refused_before_spawn`：`observation_basis`/`started` 改 group gone 加 `end.native_exit_code` None。`test_the_native_identity_is_published_only_after_native_confirmation`：`model_start_evidence.native_identity`、`root_identities == ()`、`model_start_evidence.basis` 删；roots 语义改包根集合（invalid-json 为空列表、init-wrong-session 为 `[{"sessionId": "not-the-allocation"}]`），`model_started` True 保留。`test_a_native_failure_keeps_the_observed_stream_end`、`test_an_oversized_delivered_value_drops_alone_and_keeps_every_confirmed_fact`：`stop.exit_code` 改 `end.native_exit_code`。`test_an_unconfirmed_native_group_stop_reports_unknown_not_gone`（真实进程防护）：`observation_basis`/`exit_code` 删，group unknown、end error、stream 两事实与 handle 复核保留。`test_a_late_unrepresentable_package_keeps_every_other_observed_fact`：mechanism 改 parsed disposition 断言，`receipt_verified is None` 改 `completion_evidence.stream_end` True。

结构化交付族（`StructuredDeliveryTests`）：`completion_evidence.call_id == "toolu_so_1"` 与 `call_id is None` 全部改读工具证据包的排除事实——验证成功的豁免使该调用离开包（`toolCalls` 计数、事件列表、`judge_tool_evidence` 判定不变），交付本体以 `result.value.parsed` 等于交付值为证；验证失败或未验证的调用留在包内（事件 `callId` 断言保留原生身份）。原生 call id 本身不再公布（见"接口缺口"节）。`test_a_verified_delivery_...` 另加"`StructuredOutput` 不在包事件中"的显式排除断言。

### dsh（55→55，编号不变）

`test_a_settled_answer_is_a_final_message_fact`：mechanism 删（raw 钉住）；`unknown_events` None 改捕获 observer 统计全零；`model_start_evidence.basis` 删（`model_started` True 保留）；`stop.exit_code` 改 `end.native_exit_code`；`completion.native_outcome` 删（stream_end 保留）。`test_one_format_correction_runs_a_second_session_on_the_same_process`：roots 改包长度 2。`test_an_unknown_event_is_retained_then_stopped_by_the_role`、`test_a_foreign_root_chunk_never_becomes_the_answer`、`test_sixty_five_unknown_kinds_stay_bounded_and_keep_the_total`：`unknown_events` 改捕获 observer 统计（含 65 类上界折叠与总数不变，上界桶 `unclassified-surplus` 为该 harness 惯用名）。

`test_a_write_scope_without_a_service_takes_the_final_message_path`：`enforcement`/`basis` 三条删，改 tools 事实 requested None 加 launch 记录的 `--patch` 与无 permission-mode 环境键（write 预设的实际启动事实）；`test_a_read_scope_reports_the_preset_and_injects_only_its_key` 与 `test_the_none_scope_patch_reaches_the_launch_argv_and_policy`：`enforcement` 删，requested 与 launch 记录断言保留。

`test_an_unconfirmed_row_list_refuses_to_launch`、`test_a_spawn_failure_reports_an_unavailable_agent_and_no_started_model`：`observation_basis` 删，加 `end.native_exit_code` None（未 spawn 区分事实）。`test_an_agent_lost_before_any_session_never_claims_spawn_never_happened`：`started`/`observation_basis` 改"retained `acp-connection-facts` 或 `acp-frame-log` 存在"加 `end.native_exit_code` 非空（进程曾持有且 leader 退出的存活事实）。`test_an_unobserved_agent_before_any_session_stays_unknown` 与 `test_launch_bookkeeping_failure_keeps_the_wrapper_stop_evidence`：`started`/`observation_basis` 删（group unknown 与 end error 保留包裹证据语义）。`test_an_unconfirmed_native_group_stop_reports_unknown_not_gone`：`leader_exited` 删（group unknown 与流完整断言保留）。`test_a_non_end_turn_stop_is_a_real_native_failure_not_an_answer`：`stop.exit_code` 改 `end.native_exit_code`。

`test_a_governed_turn_settles_with_a_verified_completion_tool_value`：`value.mechanism`/`validation_basis`、`completion.receipt_verified/call_id/native_outcome`、`native_identity.call_id`、`model_start_evidence.basis`、`checked.model.basis` 全部改读 turn-provenance（加 size/sha256 校验）的 `receiptVerified`/`receiptId`/`toolCallId == "call_finish_1"`/`stopReason == "end_turn"`/`nativeSessionId`，加 `model_started` True 与 checked 两值断言。`test_a_refused_upgrade_request_becomes_attention_not_completed`：`denied_interactions` 断言删并加"无 `denied-interactions` 引用"的负断言——该场景的被拒升级是权限决定，保留处是 `permission-decisions` 文件（`opt-reject` 断言保留并加记录数）。`test_a_failed_finish_result_is_retryable_inside_the_same_turn`：`completion_evidence.call_id == "call_finish_2"` 改 provenance `toolCallId`。`test_a_reconstructed_new_session_continuation_runs_a_fresh_root`：原 `root_identities == root_identities[:1]` 只能排除多于一个根，仍允许空集合；改为包根集合长度恰为 1。

## 迁移防护的相称变异

两项变异均在 HEAD 的全新副本上操作（任务根一次性目录内，未触碰工作区），迁移后的四份测试文件同步进副本后仅运行对应单模块：

- 变异一（unknown 停止）：副本去掉 zcode `_build_result` 的 `StopLayer(group_state="gone" if state.shutdown else "unknown")` 的 unknown 分支（恒报 gone）。结果：37 个测试恰 1 个失败，`FastSeamTests.test_an_unconfirmed_native_group_stop_reports_unknown_not_gone` 在 `assertEqual(native.group_state, "unknown")` 失败（`'gone' != 'unknown'`）。防护仍被发现。
- 变异二（signed receipt）：副本去掉 dsh `verify_finish_receipt` 的 `hmac.compare_digest(签名, sign_receipt(...))` 比对（保留非字符串检查）。结果：55 个测试恰 1 个失败，`WorkerSeamTests.test_a_tampered_finish_receipt_is_fatal` 在 `assertEqual(result.end.status, "error")` 失败（`'ok' != 'error'`，伪造回执被当成功接受）。防护仍被发现。

## 检查与实际结果

单模块运行器复刻检查套件的子进程环境（检出 src 与 tests/python 置于 PYTHONPATH 前、私有 state/runtime、哨兵 CLI、任务根下私有 tmp），仅运行四个受影响模块，未跑完整检查：

- 基线（dc8d77d，迁移前）：zcode 37 例 18 错、codex 37 例 19 错、claude 49 例 25 错、dsh 55 例 19 错；全部为已删字段的 AttributeError，与 [step3 使用清单](adr025-step3-format-usage.tsv) 的删除项一致。
- 迁移后：zcode 37 通过、codex 37 通过、claude 49 通过、dsh 55 通过（合计 178，OK）。
- 原始编号清单（自 dc8d77d 提取）与迁移后清单逐文件 diff 为空，计数 37/37、37/37、49/49、55/55；清单与 diff 存任务根一次性目录 `…/a254f-0hvv8lgo/m/4d2-manifest/`，基线失败名单与各次运行日志存 `…/a254f-0hvv8lgo/m/4d2-runs/`。

## 接口与实现缺口（报告，不代改）

- zcode 的 owned-spawn 路径（连接建立失败、spawn 助手自行停子进程）记录的子进程退出码，随 StopLayer 瘦身不再进入结果任何字段：结果层面"曾 spawn 并确认停止"与"从未 spawn"同为 group gone 加无退出码，仅 `end.reason_code`（`invalid-native-result` 对 `unsupported-provider` 等）可区分。`observation_basis`/`started` 删除前是该区分事实的载体；若日后需要该区分，需 Host 决定由哪个存活字段承载。
- claude 结构化交付的原生 call id（原 `completion_evidence.call_id`）不再有任何结果或保留文件承载：交付验证只经共享包的排除接缝（包计数与事件）和交付值本身可观察。step3 使用清单记录该字段无业务读取方，删除成立；本表仅记录"按 call id 直接断言豁免"的测试写法已不可行。
- dsh 被拒升级请求在当前实现中是权限决定（`permissionDecisions`），`deniedInteractions` 仅承载非权限类反向请求：旧的 `denied_interactions` 投影删除后该事实保留在 permission-decisions 引用，测试已按此迁移。

## 验证边界

变异仅在一次性副本上验证对应单模块；未运行完整检查套件、未运行安装版 harness、未做模型调用与登录操作、未安装或升级依赖、未读取凭据内容。原始清单、运行日志与两份变异副本保留在任务根一次性目录内待 Host 验收后按根回收。
