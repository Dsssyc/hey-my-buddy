# ADR-030 批次 3a：Claude Code 取消隔离

2026-10-12。基于 `c7607564` 的受管检出；只改 Claude Code 与这一批所需的共用供应方、计费及环境传递代码。以下是源码与模拟原生进程的验证记录，不是 Host 验收；完整检查、真实 Worker 与 Router 调用、故障注入及安装仍由 Host 完成。未启动真实 harness、服务或 Worker，未读取日常看板、用户配置或凭据，未调用模型。

## 参数逐项核对

| 参数 | 处理 | 理由 |
| --- | --- | --- |
| `-p` | 发现、执行保留 | 原生非交互执行载体 |
| `--input-format stream-json`、`--output-format stream-json`、`--verbose` | 两种调用保留 | 原生控制协议与证据载体 |
| `--safe-mode` | 两种调用删除 | 挡住用户配置的隔离策略 |
| `--setting-sources ""` | 两种调用删除 | 不再清空设置来源 |
| `--strict-mcp-config`、空的 `--mcp-config` | 两种调用删除 | 不再清空用户 MCP 或挑选来源 |
| `--no-session-persistence` | 发现保留，执行新增 | ADR-030 第 3 条；没有原生续接能力 |
| `--settings <native-root>/settings.json` | 执行保留 | 只表达当前沙盒权限，顶层只有 `sandbox` |
| `--restricted` | 执行保留 | 现有原生文件权限边界，属于冻结的权限档而非设置来源选择 |
| `--tools` | 执行保留 | 写入为现有 WRITABLE_TOOLS，只读为 READONLY_TOOLS |
| `--permission-prompt-tool stdio` | 执行保留 | 原生权限请求由控制器明确拒绝 |
| `--permission-mode acceptEdits/default` | 执行保留 | 写入接受普通检出编辑，只读沿用 default |
| `--disallowedTools` | 按请求保留 | 只读写工具拒绝与已有额外会话级拒绝；不在这一批改变 Router 工具政策 |
| `--session-id` | 执行保留 | 每次新建，用于原生证据归属 |
| `--model`、条件 `--effort` | 执行保留 | 表达已选配置；default effort 不传旗标 |
| `--json-schema` | 执行保留 | 角色的结构化结果约定 |
| `--resume`、`--fork-session` | 继续不用 | harness 未声明原生续接 |
| `--version` | 保留 | 同一可执行文件的有界版本观察 |
| 初始化后的额外 `auth status --json` | 删除 | 不再以另一进程证明初始化的登录方式 |
| 公共健康扫描的 `auth status --json` | 保留 | 原有只读、无模型登录状态及计费观察；去掉其中的第一方供应方限制 |

`settings.json` 只包含原来的 `sandbox.enabled`、`failIfUnavailable`、`autoAllowBashIfSandboxed`、`allowUnsandboxedCommands`、`excludedCommands` 与 `network.strictAllowlist/allowedDomains`。Worker 的注册表域名白名单、只读请求给出的空网络列表及额外拒绝工具不变。这是执行计划中已经冻结的暂行权限表达，不设计新的权限档，不把用户的其他设置复制到该文件。

## 环境与文件核对

原生进程继续调用公共 `discovery.native_environment`，没有新建 HOME、CLAUDE_CONFIG_DIR 或配置主目录。删除 Claude 本地那份没有被实际构造器使用的重复 allowlist、模型回退变量说明常量及第三方覆盖变量清单；实际允许列表只有公共构造器一处。ADR-030 第 6 条要求白名单不变，因此 shell-only 供应方变量被丢弃而不是导致执行拒绝，用户写在 harness 配置文件里的供应方由 Claude 自己读取。

| 环境项 | 处理与依据 |
| --- | --- |
| `HOME, USER, LOGNAME, USERNAME, USERPROFILE, HOMEDRIVE, HOMEPATH, APPDATA, LOCALAPPDATA` | 公共列表保留，原生账户与用户配置路径，不创建替代目录 |
| `XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_CACHE_HOME, XDG_STATE_HOME, XDG_RUNTIME_DIR` | 公共列表保留，用户自己的配置与缓存位置 |
| `CODEX_HOME, CLAUDE_CONFIG_DIR, ZCODE_DATA_BASE_DIR` | 公共列表原样保留，不由本批重定向其他 harness 的目录 |
| `LANG, LANGUAGE, LC_ALL, LC_CTYPE, LC_MESSAGES, LC_COLLATE, LC_NUMERIC, LC_TIME, LC_MONETARY, LC_PAPER, LC_NAME, LC_ADDRESS, LC_TELEPHONE, LC_MEASUREMENT, LC_IDENTIFICATION` | 公共列表保留，原生本地化环境 |
| `TMPDIR, TMP, TEMP, SYSTEMROOT, WINDIR, COMSPEC, PATHEXT, TERM, COLORTERM` | 公共列表保留，平台运行环境 |
| `PATH` | 公共构造器保留并加入选定可执行文件所在目录，保障同一原生启动器与 Node 可定位 |
| `HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, NO_PROXY, http_proxy, https_proxy, all_proxy, no_proxy` | 公共列表保留，当前代理行为不在本批改变 |
| `SSL_CERT_FILE, SSL_CERT_DIR, REQUESTS_CA_BUNDLE, CURL_CA_BUNDLE, NODE_EXTRA_CA_CERTS` | 公共列表保留，证书路径不是登录凭据 |
| `BUDDY_CLAUDE_SETTINGS_POLICY` | 删除策略函数与拒绝；从服务、控制器 allowlist 删除，没有替代开关 |
| `BUDDY_CLAUDE_FIXTURE_AUTH_STATUS` | 删除已失去用途的假登录证明控制及服务、控制器、原生传递 |
| `BUDDY_CLAUDE_FIXTURE_CASE, BUDDY_CLAUDE_FIXTURE_STATE` | 只在显式开发模式保留，模拟 CLI 的测试控制，无生产凭据 |
| `BUDDY_CLAUDE_CLI, BUDDY_DEV_SOURCE` | 继续只在开发模式选测试启动器，不进入原生进程 |
| 其他 `BUDDY_*`、agent 凭据、其他 harness 密钥、`ANTHROPIC_*`、`CLAUDE_CODE_*`、`CLAUDECODE` | 不在公共列表，不进入模型驱动的原生进程；不再对供应方变量另加预拒绝 |

| 写出内容 | 处理与理由 |
| --- | --- |
| `settings.json` | 保留 0600 的私有权限文件，仅沙盒项 |
| `native.stderr.log` | 保留本次原生进程的私有诊断证据 |
| `denied-interactions.json` | 保留实际权限拒绝与请求关联编号 |
| `native-observations.json`、`native-turn-facts.json` | 保留本次已观察到的模型、用量、额度、终态与归属事实 |
| 公共角色层的任务、输入、请求、控制、输出、凭据与选择文件 | 不修改其形式和权限；它们承载任务、结果、调用归属及私有凭据，不是替代用户配置的主目录 |
| 原生会话文件 | 执行和发现都要求 `--no-session-persistence`；session_facts 改报 `storageScope: none`、`nativeAppVisibility: none`，实际不写盘仍待真实验收 |

没有遗留含糊的可选隔离开关，也没有需要 Host 另定的参数归类；冻结的 Router 额外工具拒绝与权限强度不在这一批重判。

## 供应方、登录与共用函数

清单 D 类八行全部删除：`_catalog` 的账户第一方/登录证明两行，`_prepare` 的供应方覆盖/isolated 策略两行，`run_discovery` 的覆盖拒绝一行，`check_preparation` 的固定 anthropic/isolated/覆盖拒绝三行。相关 `account_problem`、`token_source_missing`、`auth_status_problem`、`read_auth_status`、`third_party_overrides`、`settings_policy` 与仅为它们存在的测试同批删除。Adapter 的可用性预拒绝和公共健康握手的第一方供应方门槛也删除。

`_check_catalog` 只保留本次目录的模型与 effort 成员检查，不再要求 anthropic。目录使用 `account.apiProvider` 原名，`firstParty` 不映射为 anthropic，缺失为 unknown；四段配置名因此直接使用该供应方。执行 checked.provider 使用本次初始化实际报告值，不能由调用方的请求冒充已读回值。原生额度事实也使用同一次初始化的供应方，独立的 TurnEvidence 测试没有账户来源时是 unknown。原生真正失败、归属校验、有界输入与实际停止证据不变。

初始化里有非空且非 none 的 tokenSource 时账户事实为 confirmed，缺失、null、none 或不可读时为 unknown，不再拒绝或另起登录证明进程。ADR-027 的公共目录信任规则未改：unknown 账户读数被记录但不替换已确认目录，新的配置因此可能仍等进一步原生账户证据。这不是计费标签带来的拒绝。公共健康扫描仍处理原生 loggedIn:false、命令非零与停止未知；没有在这一批处理 F/G 类的固定字段判定。

共用改动，供 3b/3c/3d 沿用：

- `protocol.billing.native_provider(account, field)` 是新增的原生供应方读取函数，保持 harness 的拼写，缺失为 unknown，不限制供应方或登录方式。
- `protocol.billing.claude_status` 保持原生已知 subscription/metered 分类，第三方或未知供应方的 API key 不能单独证明付费方式，标 unknown；API key 可能是套餐或网关凭据，不能估算计费。
- `buddy/harnesses/discovery._candidate_handshake` 的 Claude 分支接受原生第三方供应方，并把计费放在实际报告的 provider 键下。
- `buddy/harnesses/discovery._discover` 原先丢掉候选握手的 billingByProvider，本批修在返回结果的根因处：保留被选中候选的已有计费事实，才会到达健康记录和 `billing.for_provider`。
- `buddy/harnesses/runtime_selection.controller_environment` 与 `install/launcher` 的 allowlist 只清掉被删除的 Claude 策略与假 auth-status 控制变量，其他 harness 的运行模块和环境选择不变。

`blackboard/catalog` 的归一化、lookup、提议四段名字、存储和配置选择原本已经不限定供应方，直接沿用并补模拟握手→目录→配置选择→执行的覆盖；其源码无需改。既有 `billing.for_provider` 按确切供应方读取计费、缺失返回 unknown 的公共函数也无需改。没有新增存储形式、schema 或旧名称别名。

## 假进程竞态

原 `fake_claude.py` 的 early-result 在初始化答复 flush 之后才发送 assistant/result。控制器收到答复便可发送用户消息，而读线程只把已经入队的帧交给 pump_available；忙时后续帧落在用户消息之后，确实没有观察到 init，于是产品如实判 native-init-missing。产品的终态判定顺序不是根因，不能改成无证据地声称输出早发。

反馈循环：用临时副本在原 early-result 分支入口加入 150ms 延迟，直接调用实际 native_run，稳定得到 `error native-init-missing`，断言期望 native-turn-started-early 失败。修复把 premature assistant/result 写在 initialize 答复之前，管道有序，控制器必须先消费这些帧才能取得握手答复；同样延迟注入得到 `error native-turn-started-early`，断言通过。新增直接运行模块回归，并以四个子进程并行重复 16 次，全部通过；原 `test_claude.py` 用例沿用修好的同一假进程。没有延长睡眠来掩盖竞态，没有改产品拒绝顺序。

删除已经无法运行的 `tests/probes/claude_p1.py`；现存 `router_readonly.py` 探针只移除退役的 SETTINGS_POLICY 环境项，未运行真实探针。

## 验证边界与命令

使用 Host 指定的只读已准备解释器，以下 `<prepared-python>` 指它，未安装依赖或写入它的环境；源码和测试来自本检出。每次先清除 `BUDDY_STATE_DIR BUDDY_RUNTIME_ROOT BUDDY_RUNTIME BUDDY_RUNTIME_IDENTITY BUDDY_WORKER_STATE BUDDY_WORKER_ID BUDDY_AGENT_CREDENTIAL BUDDY_AGENT_CREDENTIAL_FILE VIRTUAL_ENV UV_PROJECT_ENVIRONMENT`，设置 `PYTHONPATH=src:tests/python`。没有运行完整检查。

最终受影响的可运行套件命令：

```sh
<prepared-python> -m unittest buddy.harnesses.claude.test_claude_config buddy.harnesses.claude.test_native_run buddy.harnesses.claude.test_claude_protocol buddy.harnesses.test_harness_discovery protocol.test_billing_quota blackboard.catalog.test_catalog install.test_launcher_selection install.test_launcher buddy.harnesses.claude.test_claude_worker.ClaudeCatalogRuleTests buddy.harnesses.claude.test_claude.ClaudeAvailabilityTests.test_concurrent_capability_reads_share_one_metadata_probe
```

163 项通过。另运行 `test_claude_tool_evidence` 中不经过真实角色控制器的 29 项单元测试，全部通过；早发输出直接回归以四路子进程重复 16 次，全通过。`git diff --check` 通过。最后一次合并运行上述 163 项与工具证据单元测试 29 项，总计 192 项通过，日志保留在忽略的 `tmp/batch3a-tests-final.log`，不作为 Host 验收。

曾尝试原 test_claude 的早发输出用例与 test_claude_tool_evidence 的 RunnerReceiptTests：原生假进程未启动，控制器输出 role-controller-failed、shutdownConfirmed:false；这是受管环境无法使用角色控制器套接字的未验证边界，不能把它说成原生停妥或通过。以下精确测试名由 Host 在可使用套接字的环境运行；其余未执行的服务/控制器相关测试也不能用单元结果替代。

```text
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_a_completed_task_notification_does_not_block_the_turn
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_a_free_text_rate_limit_identity_is_sanitized_not_copied
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_assistance_outcome_is_imported
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_attempt_scoped_credential_stays_private_and_out_of_result
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_cancel_interrupts_the_native_child_before_stopping_the_group
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_completed_result_cannot_also_request_assistance
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_completed_turn_uses_the_preallocated_session_and_never_resumes
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_deadline_ends_the_native_process_group
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_default_effort_omits_the_flag_for_a_model_without_levels
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_discovery_catalog_uses_resolved_ids_without_a_user_turn
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_discovery_records_unknown_login_without_a_proof_process
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_duplicate_or_malformed_native_results_are_rejected
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_execution_keeps_user_sources_and_native_permission_settings
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_generic_read_only_call_has_no_workflow_turn_or_agent_credential
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_governed_turn_leaves_unknown_login_to_the_native_cli
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_missing_controller_receipt_is_not_imported
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_model_and_effort_must_be_in_the_native_catalog
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_model_output_before_the_user_message_is_rejected
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_native_permission_settings_and_file_mode_are_preserved
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_native_session_resume_is_refused_and_fresh_sessions_are_allocated
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_non_root_subagent_result_does_not_end_the_root_turn
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_observed_model_identity_stays_null_with_a_bounded_observed_set
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_permission_request_denial_becomes_controller_attention
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_preserved_final_activity_tracks_the_native_turn
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_quota_event_while_interrupting_does_not_lose_the_stop_receipt
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_quota_rejection_is_sanitized_infrastructure_failure_without_import
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_quota_warning_records_latest_bounded_observations
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_quota_warning_survives_a_later_rejection
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_read_only_workspace_refuses_write_and_bash_tools
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_rejected_rate_limit_with_malformed_identity_still_fails_as_quota
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_result_permission_denials_alone_convert_a_completed_outcome
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_structured_result_quota_denial_is_classified_without_copying_errors
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_unsetted_background_work_blocks_a_terminal_result
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_wrong_session_identity_is_rejected
buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_zero_timeout_executes_unlimited_without_an_immediate_deadline
buddy.harnesses.claude.test_claude_tool_evidence.RunnerReceiptTests.test_a_block_stop_receipt_keeps_the_call_unsettled
buddy.harnesses.claude.test_claude_tool_evidence.RunnerReceiptTests.test_a_settled_receipt_finishes_evidence_after_the_observed_stream_close
buddy.harnesses.claude.test_claude_tool_evidence.RunnerReceiptTests.test_budget_exhaustion_reports_the_unified_count_and_an_unfinished_stream
buddy.harnesses.claude.test_claude_tool_evidence.RunnerReceiptTests.test_facts_after_the_result_frame_settle_only_after_the_stream_closes
buddy.harnesses.claude.test_claude_tool_evidence.RunnerReceiptTests.test_premature_tool_output_is_collected_before_the_boundary_rejection
buddy.harnesses.claude.test_claude_tool_evidence.RunnerReceiptTests.test_replayed_and_stream_projected_calls_count_once_in_the_receipt
buddy.harnesses.claude.test_claude_tool_evidence.RunnerReceiptTests.test_subagent_facts_reach_the_receipt_without_inheriting_the_root
buddy.harnesses.claude.test_claude_worker.ClaudeWorkerTests.test_governed_worker_seals_only_a_proven_claude_result
buddy.harnesses.claude.test_claude_worker.ClaudeWorkerTests.test_quota_failure_is_persisted_as_harness_error_without_a_capability_sample
```

Host 后续：在无套接字限制的环境跑上述测试与完整 Python 检查；执行计划所需的真实 Claude Worker/Router 回合要核对实际供应方、用户 MCP/skill、角色说明、会话不写盘；故障注入要核对杀子进程不虚报已停以及不留隔离主目录或凭据残余；将实际验收写入 `docs/acceptance/`，再决定安装。
