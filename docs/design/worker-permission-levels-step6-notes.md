# Worker 权限档位第 6 步说明：ZCode，第一回合

2026-10-13，在输入提交 `2aded1c5` 的受管检出完成不依赖 `plan` 实测的部分。本文记录实现与 Worker 的验证边界；Host 补跑原生探针、交回结果之后，下一回合再决定只读的落法。本轮没有真实模型调用、付费调用、安装、存储形式或 schema 版本变更。

## 档位、载体与策略事实

| Worker 权限档位 | 原生 session/create 的 mode | 声明的 toolScope | enforcement |
| --- | --- | --- | --- |
| 只读 / read | `yolo` | `write` | `convention` |
| 写检出 / write | `yolo` | `write` | `convention` |
| 放开 / full | `yolo` | `full` | `enforced` |

放开一档的承诺是不额外限制写入范围，原生 `yolo` 对应这个承诺；`registry.py::_WORKER_PERMISSIONS` 只改 ZCode 的一行，增加 full。Claude Code 与 DSH 的相邻声明没有动，没有重排表。删除 `zcode/native_run.py::run` 中拒绝 full scope 或 full Worker level 的分支。生成的 ZCode 能力字符串因此增加 `permission:full`；只读与写检出的声明保持原值。

原生模式由 `_session_mode(request)` 从本回合的 Worker level 映射，本轮三档均为 `yolo`；没有 Worker level 的已有工具运行仍为 `yolo`。两处 session/create 的工具分支复用这个映射，不改变其他创建参数。没有为 `plan` 的任何实测结果预写产品行为。

载体选择已经与原生工具模式分开：`run` 根据是否绑定 `SessionServices` 选择完成工具或最终消息，Worker 的角色层始终绑定自己的会话服务。只读 Worker 的原生 scope 目前是 write，但也走同一受管完成工具载体；full 现在走这条载体，不再被入口拒绝。载体选择代码无需修改。Router 的快速调用仍使用空 `toolAllowlist`、空 `mcpServers`、`offPeakToolEnabled: false` 与 `dynamicWorkflowEnabled: false`，参数不变；ZCode 没有审阅模式的能力声明不变。

原生会话成功打开后，带 Worker permission 的 `RunResult.effectivePolicy.tools.requested` 记录本回合实际送出的 `{"mode":"yolo"}`，取值来自 `state.create_params`，不从声明、上一回合或任务当前档位反推。这是 requested 事实，不冒称原生确认了文件限制。已有 Worker permission 证据继续记录 level、toolScope 和 enforcement；不新增保存格式、表、升级或另一套声明。没有 Worker permission 的最终消息工具运行及 Router 快速调用的策略事实保持原状；会话尚未打开时不制造已执行策略事实。

## 探针

有意运行的入口是 `tests/probes/zcode_plan_mode.py`，不在检查套件中。用冻结依赖的 Python 在检出根目录执行，并先清除 `AGENTS.md` 列出的继承运行身份、Worker、凭据与虚拟环境变量：

```sh
PYTHONPATH=src:tests/python python tests/probes/zcode_plan_mode.py --timeout 45
```

它沿用 `tests/probes/zcode_native.py::InstalledZcodeTests.setUp` 的安装包路径、私有 HOME、临时 provider 文件、本机回环 HTTP 假模型、私有会话目录，以及只允许回环网络、禁止共享配置写入的 sandbox-exec 包装器；在原生请求发生前换上本探针的假模型处理器。原有探针文件没有修改。探针启动装着的 macOS ZCode、Node、sandbox-exec 和会话私有的 Python 完成 MCP；不启动 hey-my-buddy 服务或 Worker，不读取用户的 ZCode 配置或凭据，不登录账户，不联系付费供应方。私有检出只含探针文件，安装包自带的 provider 模板用本机回环与占位密钥配置。

两个新会话依次使用 plan、yolo。探针直接经过产品的受管 ZCode 运行路径与完成工具，只在探针进程中把 `_session_mode` 的结果指定为本次待量模式；产品只读映射不变。假模型顺序请求 `Write` 写入 `written.txt`、`Bash` 写入 `command.txt`、`Read` 读取预置的 `read.txt`，最后调用本会话实际挂载的 `buddy_finish_turn`。即使工具没有出现在模型工具表中，仍请求它，并记录 advertised 为 false；无工具字段的原生标题请求不会消费这四步。若原生在某一步结束或失败，余下操作如实记为 not-requested。

最后一行是 JSON，包含每个模式的 harnessVersion、四项操作的请求参数与调用 ID、模型后续收到的原生工具结果、关联的原生事件、文件效果、handling、turnCompleted、原生结束原因、停止证据、实际策略事实、会话与回合身份、原生拒绝交互以及 stderr。写入和命令用实际标记文件确认 allowed，读取用原生工具结果中的预置内容确认，结束工具用有效完成结果与已确认停止确认；明确拒绝文字记为 denied，不能判定的结果记为 other，保留原始结果供 Host 判断。探针在退出前清理自己创建的 HTTP 服务与私有目录；私有夹具准备失败时不换路径重试，输出失败阶段。原生回合不正常完成时退出码为 1，因此 `plan` 被拒绝或不能结束也有 JSON，不能仅凭退出码判断只读是否成立。

本环境实际运行到了 Node/ZCode 包装器的原生启动阶段：plan 与 yolo 均由 sandbox-exec 返回 `sandbox_apply: Operation not permitted`，原生退出码为 71，run 的原因是 native-disconnected。两次 owned group 均确认 gone；没有创建会话、没有模型请求，四件事均为 not-requested，不能由这份输出推断 plan 或 yolo 的工具行为。没有绕过沙盒或换启动通道。原始本地输出在 ignored `tmp/step6-plan-probe.json`；Host 需在本机补跑同一探针，回交最后一行 JSON。

## 验证

按 Host 指定的只读解释器执行，每个文件单独一个进程，以 `python -m unittest <该文件的模块或选择器>` 运行；没有使用 uv 或安装依赖。每个进程调用检查运行器的 child_environment，清掉继承身份并设置私有 HOME、harness 配置、状态、运行与临时根。只启动离线模拟 harness、角色控制器与测试夹具，没有真实启动器、服务、Worker 或模型调用。对包含真实 C-Two 子进程的两个文件，只运行可在沙盒中验证的测试选择器，同一文件仍独占一个进程。

最终去重为 78 个文件、1,340 项测试通过；另有 21 项明确留给 Host。逐文件结果及摘要在 ignored `tmp/step6-final-summary.json`，不是 Host 已接受的验收记录。

| 范围 | 文件数 | 已通过项数 | Host 补跑项数 |
| --- | --- | --- | --- |
| buddy.harnesses.zcode | 16 | 315 | 0 |
| buddy.harnesses 顶层 test 文件 | 14 | 338 | 20 |
| buddy.roles | 15 | 157 | 0 |
| blackboard.routing | 33 | 530 | 1 |

新增三档 Worker 测试经过离线 mock App Server，检查完整的创建参数 `mode` 与会话 MCP 列表、受管完成载体、有效结构化结果、各档策略事实和声明强度；已有快速调用测试继续逐项检查原有参数。共用测试只删除 ZCode full 必须拒绝的旧断言，更新 ZCode 的能力与声明部分，Claude Code 与 DSH 的断言保留。路由 full 候选测试加入带当前声明能力字符串的 ZCode buddy，验证它可以与 Codex 一同进入 full 候选。已有手工路由夹具在本轮可运行目录测试中无需再修改。

新增 `buddy.harnesses.zcode.test_plan_probe` 的三个离线测试核对假模型顺序、空工具表仍请求写入、未声明的写入与命令仍被请求、原生工具结果的调用 ID 关联，以及标题调用不消耗步骤。这些测试只验证探针逻辑，不验证 ZCode 的工具限制。

首次执行 `blackboard.routing.test_routing_history.WorkflowRoutingHistoryTests.test_history_pages_over_many_reroutes_and_isolates_one_run` 时，测试运行器把私有根放在检出的 ignored tmp 内，两个夹具因共有 Git 父检出触发写入互斥。把该测试的私有根改为检出外的 `/tmp` 后，整个文件 11 项通过；没有为这个执行环境问题改产品源码或路由夹具。最后新增探针测试后只重跑该文件，3 项通过。`git diff --check` 通过。

以下 21 项未运行，Host 需要在允许真实 C-Two 本机通信、共享内存和进程枚举的环境补跑；没有为了它们绕过当前沙盒：

```text
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
blackboard.routing.test_readonly_tools_probe.ReadonlyToolsProbeTests.test_only_one_execution_is_possible_for_the_prepared_packet
```

没有运行 install 目录、完整检查、真实 harness 回合、故障注入、前端检查或 Windows/Linux 验证。Host 负责整合后的完整检查、真实回合、补跑与最终验收；本任务没有改动 skill、参考文档、其他 harness、存储或版本。

## 下一回合

Host 交回原生 JSON 后，只在 plan 确实挡住写入与命令，且受管回合能正常完成时，把只读落到 plan 并声明 enforced；否则保留只读的现有 convention，并记录实测事实。本轮的离线测试通过和原生启动失败均不代替这项决定。
