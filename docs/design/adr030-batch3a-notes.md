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
| `--restricted` | 执行删除 | Host 提供的 2.1.284 帮助说明它同时忽略用户、项目与本地设置；原归类有误 |
| `--tools` | 执行保留并补 Skill | 写入为 WRITABLE_TOOLS，只读为 READONLY_TOOLS；Skill 在两边均可调用 |
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

`_check_catalog` 只保留本次目录的模型与 effort 成员检查，不再要求 anthropic。Claude 自己的 `config.provider_from_account` 将内部接口标签 `firstParty` 表达为既有供应方名字 `anthropic`，其他值用 `account.apiProvider` 原拼写，缺失为 unknown；四段配置名使用该语义供应方。执行 checked.provider 使用本次初始化报告的语义供应方，不能由调用方的请求冒充已读回值。原生额度事实也使用同一次初始化的供应方，独立的 TurnEvidence 测试没有账户来源时是 unknown。原生真正失败、归属校验、有界输入与实际停止证据不变。

初始化里有非空且非 none 的 tokenSource 时账户事实为 confirmed，缺失、null、none 或不可读时为 unknown，不再拒绝或另起登录证明进程。ADR-027 的公共目录信任规则未改：unknown 账户读数被记录但不替换已确认目录，新的配置因此可能仍等进一步原生账户证据。这不是计费标签带来的拒绝。公共健康扫描仍处理原生 loggedIn:false、命令非零与停止未知；没有在这一批处理 F/G 类的固定字段判定。

共用改动，供 3b/3c/3d 沿用：

- `protocol.billing.native_provider(account, field)` 是新增的原生供应方读取函数，保持 harness 的拼写，缺失为 unknown，不限制供应方或登录方式。
- `protocol.billing.claude_status` 接收已由 Claude 模块统一为 anthropic 的状态，只凭已报告的 Anthropic 订阅事实标 subscription；任一供应方的 API key 都不能单独证明接口地址与付费方式，标 unknown，不能估算计费。
- `buddy/harnesses/discovery._candidate_handshake` 的 Claude 分支接受原生第三方供应方，并把计费放在 Claude 模块统一后的语义 provider 键下。
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

## Host 首轮打回后的修正

Host 同意参数、环境与文件归类和假进程竞态根因，但未接受首轮产物；首轮的 192 项局部通过没有覆盖检查运行器和已有 Anthropic buddy 的持久目录重读，因此不能作为这两处行为的证明。Host 报告的前端通过由 Host 验证，本回合不运行前端。

`cli/checks.test_environment` 曾从被删除的 Claude 配置模块导入第三方覆盖变量清单；它原本用来清除开发者 shell 中的供应方开关。原生子进程已由公共 allowlist 构造，额外按供应方清单过滤没有作用，现删掉该依赖与附加过滤。全仓审计又发现 `tests/python/support._child_environment` 的同一依赖，一并删除；两个函数保留私有测试根、运行时 pin 和凭据的过滤，不改原生环境的公共 allowlist。旧 SETTINGS_POLICY 在 Worker 夹具与原生用量夹具中的残余设置也删除，首轮测试里对退役开关的设置及断言同步清掉。

供应方命名只在 `buddy/harnesses/claude/config.provider_from_account` 处理：firstParty 是 Claude Code 对厂商自己接口的标签，对应同一个既有 anthropic 供应方，不是新供应方，也不是限制第三方。目录、执行、额度与公共发现的计费键统一经过此函数；共用 `billing.native_provider` 仍保持任意 harness 的原值，没有 Claude 特例。计费函数只看已统一的 anthropic 名字，不再以 API key 推定按量收费。

边界：用户把接口地址改到别处时，Claude Code 仍可能报告第一方，黑板分不出来，目录里就是 `anthropic`，计费标未知，这是它报告的事实，不另行识别接口地址。

新增持久目录回归先建立并启用 `claude:anthropic:claude-fixture:max`、写入偏好，再用取消隔离后的 firstParty 原生握手目录进行两次相隔超过一小时的重读，断言只有同一个 profile_id，创建代次、启用、可用性和偏好保持，不进入 pending，也不出现 firstParty 的第二个 buddy；另保留 bedrock 原名进入目录并选中执行的回归。新增环境回归直接调用检查运行器与公共测试夹具的环境函数，确认 shell provider 开关不用旧清单过滤、仍不能进入原生子进程。修复前，两个新增回归分别因 ImportError 与 firstParty≠anthropic 失败；修复后通过。

删除项审计使用 `rg -n -w` 在 `src tests packaging skills` 四个目录搜索下列每个名字，最终无匹配，没有引用、导入、设置或旧读法残留；文档中列名只说明删除事实。

```text
FIRST_PARTY_API_PROVIDERS
THIRD_PARTY_OVERRIDE_VARIABLES
MODEL_FALLBACK_VARIABLES
NATIVE_ENVIRONMENT_ALLOWLIST
EMPTY_MCP_CONFIG
AUTH_STATUS_ARGUMENTS
AUTH_STATUS_TIMEOUT_SECONDS
AUTH_STATUS_MAX_BYTES
third_party_overrides
account_problem
token_source_missing
auth_status_problem
read_auth_status
settings_policy
BUDDY_CLAUDE_SETTINGS_POLICY
BUDDY_CLAUDE_FIXTURE_AUTH_STATUS
AUTH_STATUS_RESPONSES
verify_login
```

实际检查运行器验证用只读解释器 import `hey_my_buddy.cli.checks` 并调用 `test_environment(Path.cwd())`，成功；随后经真实 `checks.run_suite_child` 和它的 `child_environment` 跑 `buddy.harnesses.claude.test_claude_config`，9 项通过，未分发完整检查。CLI 没有只选一个文件的参数，`--jobs 1` 会跑全套并启动沙盒不支持的服务，因此按 Host 给出的最小验证边界运行；完整入口和其余需套接字的用例仍由 Host 运行。

本回合最终复验 240 项通过，覆盖上一轮可运行测试、目录持久重读、checks 环境及并行运行器、纯原生用量回放；日志在忽略的 `tmp/batch3a-return-tests-final.log`，`git diff --check` 通过。纯第一方用量回放已有 anthropic 预期，现显式传入其夹具握手供应方，避免用没有账户来源的 unknown 默认来测试已知的第一方录音；未改原生额度的判定。

一次较广的 244 项试跑中，除已修复的这处用量夹具外，以下四项因沙盒禁止 ps 进程枚举失败，错误明确为 residue status is unknown；最终 240 项不包含它们，不修改生产观察、不模拟已停，由 Host 在可观察进程的环境运行。

```text
cli.test_checks_cleanup.PrivateRootTeardownTests.test_teardown_drains_a_cooperative_supervisor
cli.test_checks_cleanup.PrivateRootTeardownTests.test_teardown_removes_a_clean_private_root
cli.test_checks_cleanup.PrivateRootTeardownTests.test_teardown_reports_a_surviving_process_with_unlinked_lock
cli.test_checks_cleanup.PrivateRootTeardownTests.test_teardown_waits_out_a_live_process_with_unlinked_lock
```

受影响的原生用量文件还包含以下启动完整控制器的用例，未运行，由 Host 补全文件级检查（ZCode 类没有改动）：

```text
blackboard.evaluation.test_other_native_observations.ClaudeAdapterObservationTests.test_a_completed_turn_publishes_the_execution_summary_and_quota
blackboard.evaluation.test_other_native_observations.ClaudeAdapterObservationTests.test_a_missing_result_usage_falls_back_to_the_bound_assistant_records
blackboard.evaluation.test_other_native_observations.ClaudeAdapterObservationTests.test_a_quota_rejection_retains_usage_quota_and_the_last_root_message
blackboard.evaluation.test_other_native_observations.ClaudeAdapterObservationTests.test_an_oversized_assistant_text_is_bounded_with_its_full_provenance
blackboard.evaluation.test_other_native_observations.ClaudeAdapterObservationTests.test_no_native_usage_reports_unknown_not_zero
blackboard.evaluation.test_other_native_observations.ZcodeAdapterObservationTests.test_a_delta_only_turn_uses_the_partial_stream_fallback
blackboard.evaluation.test_other_native_observations.ZcodeAdapterObservationTests.test_a_failed_quota_turn_keeps_its_native_code_and_last_message
blackboard.evaluation.test_other_native_observations.ZcodeAdapterObservationTests.test_a_lost_cursor_is_never_counted_as_this_attempt
blackboard.evaluation.test_other_native_observations.ZcodeAdapterObservationTests.test_a_resumed_session_counts_only_the_new_turn
blackboard.evaluation.test_other_native_observations.ZcodeAdapterObservationTests.test_a_stalled_optional_read_cannot_change_the_turn
blackboard.evaluation.test_other_native_observations.ZcodeAdapterObservationTests.test_a_turn_publishes_the_bound_root_message_tokens
blackboard.evaluation.test_other_native_observations.ZcodeAdapterObservationTests.test_no_native_usage_reports_unknown_not_zero
```

## Host 第二轮打回后的修正

Host 在 `cf780888` 上报告检查运行器正常、前端全量通过、Python 完整检查 216 个文件中 215 个通过，并认可 anthropic 身份连续性；这是 Host 提供的验证事实，本回合不代称运行或复验完整检查。以下三处在当前候选修正，真实权限效果仍由 Host 验收。

### 测试环境的封闭性

检查运行器 `checks.test_environment` 与公共夹具 `support._child_environment` 使用完全相同的四个前缀过滤：`BUDDY_`、`ANTHROPIC_`、`CLAUDE_CODE_`、`C2_`，另保留各自运行时 pin 的删除。测试自己的封闭环境与产品允许读取用户配置是两回事；没有恢复删除的逐项供应方清单。测试既放入已知的 Bedrock/Vertex/Foundry 开关，也放入没有被枚举过的 CLAUDE_CODE_UNLISTED_PROVIDER，按前缀断言；不在这四个前缀下的变量不再因旧清单被误当成必须删除的项。测试随后自行注入的私有根与 fixture 控制照旧。

### 删除 restricted 后的权限边界

`--restricted` 整个删除；其余已有模式、允许内置工具、只读写工具拒绝、额外会话拒绝、stdio 权限回调和沙盒设置照旧，Skill 是本轮补入的唯一内置工具。`settings.json` 顶层仍只有 sandbox，没有新增用户配置覆盖、路径白名单、模式开关或权限档。

| 要守住的事 | 现有原生机制与控制器 | 从硬模式变成请求拒绝的部分、用户可放开的部分 |
| --- | --- | --- |
| Bash 在沙盒内运行，无沙盒外退路 | `sandbox.enabled/failIfUnavailable`、`allowUnsandboxedCommands:false`、现有 `excludedCommands:[]`，加上 stdio 权限请求统一 deny | 禁止的是沙盒失败后的自动重试退路，不能宣称覆盖所有用户主动例外；额外目录、sandbox.filesystem.allowWrite/disabled 或合并后仍有效的 excludedCommands 可能扩大或取消对应隔离，排除命令走普通权限流程，触发请求时由控制器拒绝，用户预批准则随配置放开；managed 策略仍由 Claude 决定，本批不清空这些用户字段 |
| 可写回合的文件工具只在普通检出内自动编辑 | 启动 cwd 是分配的检出，CLI `--permission-mode acceptEdits` 只对原生工作目录里的普通编辑自动允许；剩余请求由 `_native_request_response` 一律 deny | restricted 的固定目录围栏已经消失，检出之外的访问依赖原生路径权限判定；用户 additionalDirectories、allow 规则或 skill 的工具预授权可能使访问直接被批准，不到达控制器，这些随用户自己的配置放开，不能再声称只写检出是无条件硬保证 |
| 只读回合不调用写内置工具 | `--tools Glob,Grep,LS,Read,Skill`，`--disallowedTools Bash,Edit,MultiEdit,NotebookEdit,Write`，`--permission-mode default`，权限请求统一 deny | Skill 只是加载指令，不给它另建执行通道；会话中的内置写工具仍受 deny 规则约束，普通 allow 与 skill 预授权不能覆盖 deny；MCP 和钩子是用户原有配置，内置 tools 列表不管它们，不能以此声称任意 MCP/钩子的副作用都被阻止；Router 已有额外 MCP 等拒绝保持原样 |
| 不进入绕过权限模式 | 两种执行均显式传一次 `--permission-mode acceptEdits/default`，不传 dangerously-skip-permissions、allow-dangerously-skip-permissions 或 bypassPermissions | 显式模式覆盖设置文件里的默认模式，包括默认 bypassPermissions；用户自己的 allow 规则/skill 授权让某些操作不触发请求，这属于原生预批准，并不等于本产品启用了 bypass 模式；其余权限请求仍 deny，不注入 disableBypassPermissionsMode 来再盖住用户设置 |

命令行模式的优先级直接核对官方 [CLI reference 的 permission-mode 项](https://code.claude.com/docs/en/cli-reference)：该参数覆盖设置文件的 defaultMode，因此无需另加开关。`--tools` 只限定内置工具，`--disallowedTools` 是会话 deny，依据同页的对应项；原生规则先 deny 再 ask 再 allow，工作目录可被 additionalDirectories 扩展，依据 [Configure permissions](https://code.claude.com/docs/en/permissions)。Bash 沙盒是单独的机制，不约束 MCP/钩子或直接文件工具，路径扩展与禁止沙盒外重试的依据是 [sandboxing](https://code.claude.com/docs/en/sandboxing)。这些是文档核对和由已有原生机制推得的边界，未在 2.1.284 上做真实优先级或越界实验；Host 给出的该版本 restricted 帮助是删除它的直接版本依据。

### Skill 的名字与授权

官方 [Extend Claude with skills 的权限章节](https://code.claude.com/docs/en/skills) 直接以 Skill 作为工具名，并给出 Skill(name) 权限规则；因此两份原生内置工具清单加入 `Skill`，不使用旧 SlashCommand 名字。该文档也说明 skill 的 allowed-tools 可以预授权调用，本产品不删除它；它不能把已经被会话 deny 的写内置工具重新允许。只读调用中 Skill 仍可载入用户指令和只读流程，其后调用的内置工具仍过原有可用工具/deny/权限判定；任意用户 MCP、钩子或其他外部执行的副作用不在这份只读内置清单的保证内，真实回合由 Host 观察。

来源核对只读取以上官方文档，没有读取厂商代码、用户配置或凭据，也没有启动 Claude CLI；本文保留当前版本和原生事件证据边界，不把模拟测试当作真实权限证明。新版参数回归断言读写启动均无 restricted，模式、stdio 权限回调、沙盒设置和只读写工具拒绝保持，读写都含 Skill；prefix 与参数回归在修复前分别因泄漏 CLAUDE_CODE_、仍有 restricted、缺 Skill 失败，修复后通过。

当前 Router 工具事件表没有把 Skill 注册为只读类别，仍会把该工具事实记成 other，审阅答案可能因此不能发布；本轮只修原生工具可用性，没有把一个可带动态授权和 fork 的 Skill 调用冒称 read，也没有修改批次 4 所属的分类和判定规则，后续由 Host 在真实 Router 验收与后续批次里处理。

本回合 `cli.test_support_cleanup` 运行五个不启动真实服务且不需要共享内存的用例，包含 Host 指定的 `test_child_environment_drops_inherited_ownership_and_credentials`；以下三个未运行，文件级完整通过仍需 Host 在其环境确认，不能把五项局部通过称作全文件通过：

```text
cli.test_support_cleanup.FixtureCleanupTests.test_r06_child_uses_private_state_and_explicit_catalog
cli.test_support_cleanup.FixtureCleanupTests.test_cleanup_stops_cli_started_replacement_after_restart
cli.test_support_cleanup.FixtureCleanupTests.test_private_state_context_stops_its_cli_started_service
```

本回合最终复验 245 项通过，日志在忽略的 `tmp/batch3a-third-review-tests-final.log`；另外单独运行 `cli.test_support_cleanup` 的上述五项，五项全部通过，指定失败用例明确为 ok；完整八项没有在此环境运行。`git diff --check` 通过，src/tests/packaging/skills 的 18 个删除项审计仍无匹配，Claude 生产启动构造器也没有 restricted。测试目录里保留的 restricted 字样仅用于断言它不存在，文档字样仅用于说明删除依据。没有运行前端、真实 Claude 回合、完整检查或故障注入。
