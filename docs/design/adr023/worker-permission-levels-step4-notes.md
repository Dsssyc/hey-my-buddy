# Worker 权限档位第 4 步说明

2026-10-13，在输入提交 `2aded1c5` 的受管检出实现 Claude Code 的三档。第 2 回合由 Host 明确将 `src/hey_my_buddy/buddy/roles/schema_worker.py` 加入写入范围；第 1 回合只检查代码与官方文档，没有修改文件。本说明记录源码与离线测试的事实；真实 Claude Code、安装、完整检查与最终验收由 Host 负责。

## 本回合交给原生的参数与设置

每个 Worker 请求使用自己的 `RunRequest.worker_permission`。`config.worker_sandbox_settings` 生成本次的设置，`native_run._prepare` 写入原有的私有 `settings.json` 并以 `--settings <设置文件>` 交给进程；它保存同一个设置对象用于结果证据。只读沿用原有设置；无 Worker permission 的调用沿用原来的网络与额外拒绝工具参数。

| 档位 | permission-mode | tools | settings |
| --- | --- | --- | --- |
| 只读 / read | `default` | `Glob,Grep,LS,Read,Skill` | 原有沙盒与包仓库域名设置，逐项不变 |
| 写检出 / write | `acceptEdits` | `Agent,Bash,Edit,Glob,Grep,LS,MultiEdit,NotebookEdit,Read,Skill,Task,TodoWrite,Write` | 启用沙盒，域名 `*`，允许本机绑定，不允许退到沙盒外 |
| 放开 / full | `bypassPermissions` | 与写检出相同 | `{"sandbox":{"enabled":false}}` |

只读仍通过 `--disallowedTools Bash,Edit,MultiEdit,NotebookEdit,Write` 强制拒绝写入与 Bash，也不增加新的工具。写检出的完整设置如下；没有添加 filesystem 写入根，可写检出与每用户临时目录由原生的默认提供。

```json
{
  "sandbox": {
    "enabled": true,
    "failIfUnavailable": true,
    "autoAllowBashIfSandboxed": true,
    "allowUnsandboxedCommands": false,
    "excludedCommands": [],
    "network": {
      "strictAllowlist": false,
      "allowedDomains": ["*"],
      "allowLocalBinding": true
    }
  }
}
```

共同参数仍为 `-p --input-format stream-json --output-format stream-json --verbose --no-session-persistence`，配合 `--permission-prompt-tool stdio`、新分配的 `--session-id`、模型、非 default 时的 effort 和本回合的 `--json-schema`。没有增加危险模式确认开关、用户配置读取或配置写入；native_environment、原生配置来源与 initialize 目录读取路径不因档位而改变。网络许可只使用 sandbox.network，不增加 Bash 或 WebFetch 的逐项放行规则。

## 官方依据与待核对项目

本轮读取了 [设置参考](https://code.claude.com/docs/en/settings-reference)、[沙盒说明](https://code.claude.com/docs/en/sandboxing) 和 [权限说明](https://code.claude.com/docs/en/permissions)；设置参考的 HTML 超过读取器上限，改读同一官方页面的 Markdown 表示。这里只把文档作为参数依据，没有运行本机的 Claude Code；Host 给出的版本 `2.1.284` 没有在 Worker 内重新探测。当前官方页面还描述比这个安装版本更新的行为，真实核对必须绑定 Host 实际运行的版本。

[allowedDomains](https://code.claude.com/docs/en/settings-reference#sandbox-network-alloweddomains) 将条目定义为域名、通配模式或 IP 字面量，并示范子域通配，因此采用沙盒自己的 `allowedDomains: ["*"]` 表达所有域名，不需要 WebFetch 放行规则。[strictAllowlist](https://code.claude.com/docs/en/settings-reference#sandbox-network-strictallowlist) 负责清单外的严格拒绝，本步明确传 false。这里确认的是文档支持通配与源码传参；裸 `*` 在 Host 安装版本上的实际代理行为仍须实测。文档明确沙盒也认 WebFetch 的裸 `*`（需 2.1.186 以上），这个备用表达本步没有使用。所有域名放行不等于任意网络协议都能出沙盒：原生代理、用户的拒绝规则和受管域名限制仍可能影响实际连接。

[allowLocalBinding](https://code.claude.com/docs/en/settings-reference#sandbox-network-allowlocalbinding) 在 macOS 上允许监听本机地址与连接 localhost 的端口；Linux 与 WSL2 的回环属于每条沙盒命令，因此此开关在那里无效。设置参考的 [sandbox.enabled](https://code.claude.com/docs/en/settings-reference#sandbox-enabled)、[autoAllowBashIfSandboxed](https://code.claude.com/docs/en/settings-reference#sandbox-autoallowbashifsandboxed)、[failIfUnavailable](https://code.claude.com/docs/en/settings-reference#sandbox-failifunavailable) 和 [allowUnsandboxedCommands](https://code.claude.com/docs/en/settings-reference#sandbox-allowunsandboxedcommands) 是启用、自动许可、不可用时报错和禁止沙盒外重试的依据；写检出的 cwd 与临时目录范围依据沙盒说明的 Filesystem isolation。

[allowUnixSockets](https://code.claude.com/docs/en/settings-reference#sandbox-network-allowunixsockets) 描述的是 macOS 上逐个套接字路径，未给出目录、通配或递归允许目录下任意套接字的语法；Linux 与 WSL2 忽略这张路径表。[allowAllUnixSockets](https://code.claude.com/docs/en/settings-reference#sandbox-network-allowallunixsockets) 则会允许全部套接字。无法据这些文档表达“检出与临时目录下全部套接字”，所以本步没有添加 allowUnixSockets 或 allowAllUnixSockets；没有把目录伪装成套接字路径，也没有枚举文件或放开容器守护进程的套接字。用户已有设置照常由原生读取；本步没有为此另加用户权限上限。是否接受这个差别、是否需要新的原生依据，由 Host 与开发者决定。

权限说明的 Permission modes 是 default、acceptEdits 与 bypassPermissions 的依据；设置参考的 [permissions.disableBypassPermissionsMode](https://code.claude.com/docs/en/settings-reference#permissions-disablebypasspermissionsmode) 是禁用跳过权限的依据。bypassPermissions 不会自动关闭沙盒，因此放开一档单独传 enabled false。非交互与 root 启动拒绝的实际表现没有在这里测量。

没有为带环境赋值、make 参数赋值、eval、Monitor 或命中 ask 规则的命令增设 Bash 放行规则。2026-10-11 的 npm 与 node 被回绝的原因尚未查明，不能从那次未保存的命令写法推断根因；本步保留拒绝文本供 Host 复现。

## 模式、拒绝与换档的证据

删除了 Claude full 未实现的拒绝；`_WORKER_PERMISSIONS` 只修改 Claude 一行，增加 `full/full/enforced`，其余 harness 行与表顺序不动。full scope 没有 Worker permission 是自身调用方参数错误，仍以 INVALID_ARGUMENT 拒绝；Router 的 none 仍缺原生能力，审阅调用的 argv 和 settings 保持原值。

`effective_policy.tools.requested` 记录实际 argv 中的 permissionMode、tools、disallowedTools 与实际写入设置的完整 sandbox 对象，保留现有 sandboxNetworkAllowedDomains 投影。它说明本次交给原生的策略，不把 CLI 请求当成沙盒已生效的证明；这些是现有报告中的事实，没有改 schema、存储表或升级。

`TurnEvidence` 从 root system/init 读取 permissionMode，只有已识别的六种模式可以比对。Worker 的初始化报告与本次 argv 模式不符时，collector 在处理这个事件后以 unsupported-capability 结束，不再处理后面的模型或工具输出，并说明该 harness 给不出这个权限档位；原有会话、cwd、停止与结果绑定检查保留。缺字段、错误类型或未来模式留下 permissionMode null、permissionModeStatus unread，不新增拒绝。识别成功的字段状态 confirmed 只表示读懂原生报告，不表示已独立验证该档的文件范围。成功与失败的 native-observations 都保留这个模式事实。

原生 can_use_tool 仍收到 deny。运行模块只从输入的 command、file_path、path、notebook_path 或 url 中取摘要，写入内容和其他输入不进入摘要；无法读取时保留工具与 unread 标记。多次拒绝按收到的顺序累积为 deniedOperationSummary，整个事实在这个原生输入边界截断至 512 个 UTF-8 字节，避免截断一个字符。它通过 native-turn-facts 交给角色层；attention_outcome 只追加这一事实到原有 request.attempted，不解析原生输入、不再限长、不增加 request 字段。无该事实的 Claude 和 Codex 自动 attention 保持原文，测试逐字比较完整 outcome；已有模型自己给出的有效 attention 的保留规则未改。

每个回合继续使用新的 session-id，没有 --resume；本回合从自己的 worker_permission 生成参数，不借用上回合的策略。离线测试覆盖 read→write→full→write→read 的重建链，每次会话身份不同、策略随本次请求变化。

## 本环境验证

每个文件一个独立 Python 进程，使用 Host 指定的只读解释器与 `PYTHONPATH=src:tests/python`；没有安装依赖或使用 uv。批量脚本调用检查运行器的 child_environment，为每个文件建立私有 HOME、harness 配置、状态、运行与临时目录，并清掉继承的运行身份、Worker 与凭据。最多同时三个独立文件，日志在 ignored `tmp/step4-tests/`；没有接触日常看板、用户自己的 harness 配置或凭据，也没有付费模型调用。

运行选择发生过一次错误：全范围批次漏排 `buddy.harnesses.claude.test_claude_worker.ClaudeWorkerTests` 的两个测试，确实启动了私有真实 daemon 和 Worker，两个测试正常返回通过。这违反本轮“不运行真实服务”的限制；后续选择已排除该类，只运行纯目录规则测试，两个服务用例不计入受限环境的验证覆盖，也没有另行执行服务操作或 ps 来补证它们的进程状态。Host 应在自己的环境检查残留并决定如何采用这次记录。

最终去重结果为 68 个文件、1200 项可运行测试全部通过；其中 routing 的 33 个文件均运行，涉及 ps 的一个用例预先排除。最后的源码变更后重跑受影响的 10 个文件，全部通过；没有重新运行那两个真实服务用例。新增测试覆盖三档 argv、完整 settings 与策略事实，读得懂的模式不符时停止、缺字段或未来模式时记录 unread，命令与路径摘要、UTF-8 截断、多次拒绝共用 512 字节边界、无摘要的 Codex 与 Claude outcome 原文，以及升降档的新会话。Router 审阅逐项比较原有完整 argv 与 settings，快速调用仍在启动前以原来的 INVALID_ARGUMENT 拒绝。

| 目录 | 文件数 | 可运行测试数 | 结果 |
| --- | --- | --- | --- |
| Claude | 7 | 191 | 全部通过 |
| harnesses 顶层 | 14 | 338 | 全部通过 |
| roles | 14 | 141 | 全部通过 |
| routing | 33 | 530 | 全部通过 |

交给 Host 的用例共 40 项，完整 ID 如下；NativeDeadlineTests 与 SubprocessLifecycleTests 要真实 C-Two 传输和共享内存，InquiryOwnerPeerTests 要真实端点，role_live_seam 的所有用例在 setUp 要 native endpoint context，readonly_tools_probe 的一个用例在清场要 ps。ClaudeWorkerTests 的两个用例需要真实服务，已排除后续运行；前述误运行单独记录，不纳入本表。

```text
buddy.harnesses.claude.test_claude_worker.ClaudeWorkerTests.test_governed_worker_seals_only_a_proven_claude_result
buddy.harnesses.claude.test_claude_worker.ClaudeWorkerTests.test_quota_failure_is_persisted_as_harness_error_without_a_capability_sample
buddy.harnesses.test_c_two_live.NativeDeadlineTests.test_first_connection_expires_pre_dispatch_and_recovers
buddy.harnesses.test_c_two_live.NativeDeadlineTests.test_already_connected_peer_expires_pre_dispatch_and_recovers
buddy.harnesses.test_c_two_live.NativeDeadlineTests.test_zero_connection_and_call_budgets_are_native_pre_dispatch
buddy.harnesses.test_c_two_live.NativeDeadlineTests.test_dispatched_call_expires_but_same_connection_and_remote_call_survive
buddy.harnesses.test_c_two_live.NativeDeadlineTests.test_delayed_owner_never_consumes_an_expired_queue_entry
buddy.harnesses.test_c_two_live.NativeDeadlineTests.test_connection_time_does_not_renew_the_owner_queue_window
buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_one_run_one_endpoint_with_a_clean_stop
buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_the_owner_settles_the_real_handoff_with_refusal_then_retry
buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_the_same_name_on_two_addresses_does_not_cross_wire
buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_old_tokens_instances_and_identity_components_are_rejected
buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_wrong_and_oversized_frames_over_real_transport
buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_a_closed_endpoint_refuses_but_keeps_serving_facts
buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_the_per_run_inquiry_limit_over_real_transport
buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_a_killed_endpoint_is_unavailable_and_never_stopped
buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_a_live_endpoint_is_busy
buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_a_credential_for_another_endpoint_is_stale_target
buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_a_committed_request_id_holds_one_real_queue_entry
buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_a_stalling_endpoint_returns_within_the_native_window
buddy.harnesses.test_inquiry_owner.InquiryOwnerPeerTests.test_real_ctwo_owner_queue_requires_verified_cooperative_native_delivery_and_answer
buddy.harnesses.test_inquiry_owner.InquiryOwnerPeerTests.test_tampered_receipt_and_child_native_evidence_cannot_upgrade_rpc_queue_receipt
buddy.roles.test_role_live_seam.LiveBindingRegistryTests.test_only_registered_modules_carry_a_live_binding
buddy.roles.test_role_live_seam.LiveBindingRegistryTests.test_a_channel_binds_the_requests_own_complete_identity
buddy.roles.test_role_live_seam.LiveBindingRegistryTests.test_a_foreign_harness_request_is_refused_and_an_unregistered_one_answers_none
buddy.roles.test_role_live_seam.StoredRequestTests.test_a_whole_stored_relationship_verifies_its_complete_identity
buddy.roles.test_role_live_seam.StoredRequestTests.test_any_identity_gap_refuses_the_whole_request
buddy.roles.test_role_live_seam.StoredRequestTests.test_one_request_identity_component_mismatch_refuses_alone
buddy.roles.test_role_live_seam.HandleBindingTests.test_the_owned_request_binds_the_channel
buddy.roles.test_role_live_seam.HandleBindingTests.test_the_binding_states_are_the_three_the_consumer_acts_on
buddy.roles.test_role_live_seam.HandleBindingTests.test_a_missing_unreadable_or_foreign_request_never_binds
buddy.roles.test_role_live_seam.HandleBindingTests.test_each_held_identity_component_and_harness_must_match_the_request
buddy.roles.test_role_live_seam.HandleBindingTests.test_credential_address_and_domain_mismatch_never_reap
buddy.roles.test_role_live_seam.HandleBindingTests.test_unknown_controller_group_never_reaps_endpoint
buddy.roles.test_role_live_seam.HandleBindingTests.test_unreaped_or_wrong_popen_pid_never_reaps_endpoint
buddy.roles.test_role_live_seam.HandleBindingTests.test_release_revalidates_every_identity_instance_pid_and_cached_address
buddy.roles.test_role_live_seam.HandleBindingTests.test_exceptional_reap_is_not_retried
buddy.roles.test_role_live_seam.HandleBindingTests.test_a_native_refusal_is_reported_and_never_retried
buddy.roles.test_role_live_seam.ReadyFileBarrierTests.test_live_binding_rejects_symlinks_fifos_and_oversized_readiness
blackboard.routing.test_readonly_tools_probe.ReadonlyToolsProbeTests.test_only_one_execution_is_possible_for_the_prepared_packet
```

没有运行 tests/python/install、完整检查、真实 harness、安装、付费模型回合、故障注入或前端检查。git diff --check 通过。

## Host 的真实核对

在真实 Claude Code 的三档回合核对检出内、检出外与临时目录的文件写入，域名代理联网与本机绑定；逐项记录实际能力，不用离线夹具的模式回显证明原生强制效果。写检出的 Unix 套接字目录范围本步没有实现，需要记录这一差别；只读工具与 subagent 是否符合原有强制声明、放开是否确实没有 Bash 沙盒，都需要真实证据。

保留具体命令文本后，复现 `npm --prefix apps/console test`、`node node_modules/typescript/bin/tsc --noEmit` 及此前被尝试的写法，并检查 attention.attempted 与 native-turn-facts 的摘要是否相同；如仍拒绝，再根据真实请求判定 ask 规则、变量赋值或其他原因，不预先猜测。

核对受管设置禁用 bypassPermissions、要求沙盒或限制域名时的初始化模式及启动错误；可读模式不符应停止本回合，不可读模式应保留 unread。root 非交互启动的真实拒绝本轮未测。对同一微任务真实运行 read→write→full 及降档续接，确认新会话参数与各回合证据一致。完整检查、未运行用例、真实回合、安装、故障注入与 Windows/Linux 行为由 Host 补齐；skill 与参考文档仍由 Host 更新。
