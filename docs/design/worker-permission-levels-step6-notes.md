# Worker 权限档位第 6 步说明：ZCode

2026-10-13，在输入提交 `2aded1c5` 的受管检出完成第一回合，Host 验收该交付并回交 ZCode 0.16.9 的原生探针结果后，第二回合落实只读与续接换档。本文记录最终实现、Host 交回的实测事实与 Worker 的验证边界；新增只读命令和续接写入的原生探针仍由 Host 跑。Worker 没有真实模型调用、付费调用、安装、存储形式或 schema 版本变更。

## 档位、载体与策略事实

| Worker 权限档位 | 原生 session/create 的 mode | 声明的 toolScope | enforcement |
| --- | --- | --- | --- |
| 只读 / read | `plan` | `read` | `enforced` |
| 写检出 / write | `yolo` | `write` | `convention` |
| 放开 / full | `yolo` | `full` | `enforced` |

放开一档的承诺是不额外限制写入范围，原生 `yolo` 对应这个承诺；`registry.py::_WORKER_PERMISSIONS` 只改 ZCode 的一行，增加 full 并把 read 改为 read/enforced。Claude Code 与 DSH 的相邻声明没有动，没有重排表。删除 `zcode/native_run.py::run` 中拒绝 full scope 或 full Worker level 的分支。生成的 ZCode 能力字符串因此增加 `permission:full`；写检出仍为 write/convention。

原生模式由 `_session_mode(request)` 从本回合的 Worker level 映射：read 为 plan，write/full 为 yolo；没有 Worker level 的已有工具运行仍为 yolo。两处 session/create 的工具分支复用这个映射，不改变其他创建参数。只读采用 plan 的依据是下文 Host 的原生实测；第一回合暂存的 read/write/convention 声明与 read→yolo 映射已被替换。

载体选择已经与原生工具模式分开：`run` 根据是否绑定 `SessionServices` 选择完成工具或最终消息，Worker 的角色层始终绑定自己的会话服务。只读 Worker 的原生 scope 现在是 read，仍走同一受管完成工具载体；full 也走这条载体，不再被入口拒绝。Host 已量到结束工具在 plan 中可用，载体选择代码无需修改。Router 的快速调用仍使用空 `toolAllowlist`、空 `mcpServers`、`offPeakToolEnabled: false` 与 `dynamicWorkflowEnabled: false`，参数不变；ZCode 没有审阅模式的能力声明不变。

原生会话成功打开后，带 Worker permission 的 `RunResult.effectivePolicy.tools.requested` 记录本回合实际送出的 `{"mode":"plan"}` 或 `{"mode":"yolo"}`，取值来自 `state.create_params`，不从声明、上一回合或任务当前档位反推。创建时通过 session/create 发送，续接时通过 session/setMode 发送；可读但不符的续接答复仍保留已请求的策略事实，回合在模型输入前失败。这是 requested 事实，不冒称原生确认了每一种工具限制。已有 Worker permission 证据继续记录 level、toolScope 和 enforcement；不新增保存格式、表、升级或另一套声明。没有 Worker permission 的最终消息工具运行及 Router 快速调用的策略事实保持原状；会话尚未打开时不制造已执行策略事实。

## Host 已量到的原生行为

Host 在本机以装着的 ZCode 0.16.9、本机回环假模型及私有主目录运行第一回合的探针，没有付费调用。交回的两种模式均正常完成，原生 end.status 为 ok、reasonCode 为 null，进程组确认退出；plan 未创建文件，yolo 创建 written.txt 与 command.txt。

| 模式 | 写文件 | 会写文件的 Bash 命令 | 读取文件 | 结束工具 | 回合正常结束 |
| --- | --- | --- | --- | --- | --- |
| plan | denied | denied | allowed | allowed | true |
| yolo | allowed | allowed | allowed | allowed | true |

plan 两次拒绝都以工具结果 `Plan mode only allows read-only, non-destructive tools` 返回给模型，deniedInteractions 为空，没有原生权限请求交给黑板。只读的强制来自这项原生工具限制；拒绝应由 Worker 在自己的结果中报告，不能指望权限请求回调自动变成 attention。实测覆盖写文件和会写文件的命令，尚未量到只读 Bash 命令会不会放行，也没有量原生联网工具或所有命令形式；不能把这份结果表述为 plan 禁止全部命令。

## 续接时切换模式

第二回合只读核对了装着的 ZCode app-server 协议：它列出 session/setMode，入参是 sessionId、mode 与可选 expectedRevision；处理已有会话并返回原生快照，模式位于 settings.mode.current。确认范围是这个调用存在及请求、答复的形状，没有做安装包静态分析来证明工具限制。实现没有复制安装包的代码。

`_admit_root_input` 在已有会话通过任务、检出及会话身份核对后，且本请求带 Worker permission、续接形式为 native-session 时，调用 `configure_mode`，以 session/setMode 把已有会话改为本回合的模式；随后仍走原有模型、推理强度设置、订阅及输入。每次 Worker 续接都发送模式，不依赖前一回合的档位，也不靠 session/resume 偷带没有定义的 mode 参数。新会话继续只由 session/create 指定模式。Router 没有加模式切换。

settings.mode.current 是可读的非空字符串且不符时，以 configuration-mismatch 拒绝，原生输入没有发送；不可识别时，通过已有 unknownEvents 事实记录 permission-mode-readback-unconfirmed，允许回合继续，不制造已确认的模式值。答复明确属于其他会话时，以 wrong-native-session 拒绝。处理规则与模型、推理强度的回读相同；没有回退模式或另开会话。真实续接后的工具执行仍待 Host 的扩展探针确认。

## 探针

有意运行的入口是 `tests/probes/zcode_plan_mode.py`，不在检查套件中。用冻结依赖的 Python 在检出根目录执行，并先清除 `AGENTS.md` 列出的继承运行身份、Worker、凭据与虚拟环境变量：

```sh
PYTHONPATH=src:tests/python python tests/probes/zcode_plan_mode.py --timeout 45
```

它沿用 `tests/probes/zcode_native.py::InstalledZcodeTests.setUp` 的安装包路径、私有 HOME、临时 provider 文件、本机回环 HTTP 假模型、私有会话目录，以及只允许回环网络、禁止共享配置写入的 sandbox-exec 包装器；在原生请求发生前换上本探针的假模型处理器。原有探针文件没有修改。探针启动装着的 macOS ZCode、Node、sandbox-exec 和会话私有的 Python 完成 MCP；不启动 hey-my-buddy 服务或 Worker，不读取用户的 ZCode 配置或凭据，不登录账户，不联系付费供应方。私有检出只含探针文件，安装包自带的 provider 模板用本机回环与占位密钥配置。

两个新会话依次使用 plan、yolo。探针直接经过产品的受管 ZCode 运行路径与完成工具，只在探针进程中把 `_session_mode` 的结果指定为本次待量模式。假模型顺序请求 `Write` 写入 written.txt、`Bash` 写入 command.txt、`Read` 读取预置的 read.txt、`Bash` 用 ls 列出私有检出，最后调用本会话实际挂载的 buddy_finish_turn。即使工具没有出现在模型工具表中，仍请求它，并记录 advertised 为 false；无工具字段的原生标题请求不会消费这些步骤。若原生在某一步结束或失败，余下操作如实记为 not-requested。

plan 正常完成后，探针在同一个私有目录、native_root 和原生会话上建立下一回合，使用 native-session 续接及本回合的新完成服务，切到 yolo，再请求 Write 创建 resumed-written.txt 和新的结束工具。原生切换走产品的 session/setMode 路径。每个回合的工具调用 ID 加上自己的 invocation 标识，历史工具结果不会被当成续接回合的结果。plan 未正常完成时不续接，JSON 明确记录 not-run。

最后一行是 JSON，modes 包含每个新会话的 harnessVersion、五项操作的请求参数、工具表和调用 ID、模型后续收到的原生工具结果、关联的原生事件、文件效果、handling、turnCompleted、原生结束原因、停止证据、实际策略事实、会话与回合身份、原生拒绝交互以及 stderr；resumePlanToYolo 给出续接的写入与结束结果，另有 previousSessionId、sameSession 和 modeCalls，后者保留 create/resume/setMode 的模式参数和答复。写入和写入命令用实际标记文件确认 allowed，读取用原生工具结果中的预置内容确认，只读命令用其原生 ls 结果中的 read.txt 确认，结束工具用有效完成结果与已确认停止确认；明确拒绝文字及 Host 已量到的 plan 拒绝原文记为 denied，不能判定的结果记为 other，保留原始结果供 Host 判断。探针在退出前清理自己创建的 HTTP 服务与私有目录；私有夹具准备失败时不换路径重试，输出失败阶段。任一新会话或续接回合不正常完成时退出码为 1，不能仅凭退出码判断某项工具是否被限制。

第一回合本环境尝试原生启动时，plan 与 yolo 均由 sandbox-exec 返回 sandbox_apply: Operation not permitted，原生退出码为 71，run 的原因是 native-disconnected。两次 owned group 均确认 gone；没有创建会话、没有模型请求，不能由该输出推断工具行为。原始本地输出在 ignored tmp/step6-plan-probe.json。第二回合遵照 Host 的要求没有再运行原生探针，也没有绕过沙盒；扩展的只读命令和 plan→yolo 续接写入由 Host 在本机补跑并回交最后一行 JSON。

## 验证

按 Host 指定的只读解释器执行，每个文件单独一个进程，以 `python -m unittest <该文件的模块或选择器>` 运行；没有使用 uv 或安装依赖。每个进程调用检查运行器的 child_environment，清掉继承身份并设置私有 HOME、harness 配置、状态、运行与临时根。只启动离线模拟 harness、角色控制器与测试夹具，没有真实启动器、服务、Worker 或模型调用。对包含真实 C-Two 子进程的两个文件，只运行可在沙盒中验证的测试选择器，同一文件仍独占一个进程。

第二回合最终去重为 78 个文件、1,349 项测试通过；另有 21 项明确留给 Host。逐文件结果及摘要在 ignored `tmp/step6-turn2-final-summary.json`，不是第二回合已被 Host 接受的验收记录；第一回合的可运行结果为 78 个文件、1,340 项通过。

| 范围 | 文件数 | 已通过项数 | Host 补跑项数 |
| --- | --- | --- | --- |
| buddy.harnesses.zcode | 16 | 324 | 0 |
| buddy.harnesses 顶层 test 文件 | 14 | 338 | 20 |
| buddy.roles | 15 | 157 | 0 |
| blackboard.routing | 33 | 530 | 1 |

三档 Worker 测试经过离线 mock App Server，检查完整的创建参数 mode 与会话 MCP 列表、受管完成载体、有效结构化结果、各档策略事实和声明强度；第二回合只读预期改为 plan/read/enforced。续接分别覆盖 read→write、write→read，断言同一会话、session/resume 后的 session/setMode 参数、切换在模型配置和输入前发生，以及本回合策略事实。另覆盖可读模式不符时输入未发送、模式不可识别时有事实且可以完成、模式答复明确属于其他会话时拒绝。mock 的原生会话保留各自模式并返回快照，没有模拟 ZCode 的工具限制。已有快速调用测试继续逐项检查原有参数。

共用测试只删除 ZCode full 必须拒绝的旧断言，更新 ZCode 的能力与声明部分，并让 ZCode full 进入冻结 Worker 请求的测试；Claude Code 与 DSH 的断言保留。路由 full 候选测试加入带当前声明能力字符串的 ZCode buddy，验证它可以与 Codex 一同进入 full 候选。两回合均按文件运行 routing 全目录，本轮可运行部分 530 项通过，已有手工路由夹具无需再修改。

`buddy.harnesses.zcode.test_plan_probe` 的七个离线测试核对假模型顺序、空工具表仍请求写入、未声明的写入与命令仍被请求、原生工具结果的调用 ID 关联、标题调用不消耗步骤、Host 已量到的拒绝原文识别、只读命令的原生结果识别，以及续接序列不会混入上一回合的工具结果。另经离线 mock App Server 实际调用探针的两回合 measure，检查同一会话、模式切换调用与答复、两回合的操作列表；只有 mock 模型 ID 替换成 fixture-model，没有启动装着的 ZCode。这些测试只验证探针逻辑，不验证 ZCode 的工具限制。

第一回合首次执行 routing_history 的多回合历史测试时，测试运行器把私有根放在检出的 ignored tmp 内，两个夹具因共有 Git 父检出触发写入互斥；私有根改到检出外的 /tmp 后，整个文件 11 项通过，没有为这个执行环境问题改产品源码或路由夹具。第二回合全部测试的私有根直接位于 /tmp。初次续接事实断言误把 immutable counts_by_type 的键值元组当成字典，修正断言读取方式后 native_run 全文件 71 项通过，没有为此改产品逻辑。`git diff --check` 通过。

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

## Host 补跑与验收

只读改为 plan/read/enforced 已按 Host 交回的实测结果落实。Host 需运行扩展探针，核对 modes 中 plan 的 read_command 是放行还是拒绝，以及 resumePlanToYolo 中 sameSession、session/setMode 的答复、本次写入和结束工具是否成功；新结论再补入本说明。只读命令是否可用目前仍未知，真实续接的工具效果也没有本轮原生运行证据，离线测试不代替这些实测或 Host 的第二回合验收。
