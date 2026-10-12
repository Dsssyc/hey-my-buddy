# ADR-030 批次 4：ZCode 逐处说明

本轮基于 `8bdb7358`，按路径、函数与错误码重新定位 `06b4e99f` 清单中的 20 处：F 5 处、G 0 处、C merge 4 处、I 11 处。修改仅在分配的 ZCode 源码、ZCode 测试和本文件内，没有提交，没有改变启动权限参数、共享模块、其他 harness、服务或日常配置。本文件记录 Worker 的源码与验证事实，Host 验收另行进行。

事实沿用 `_RunFacts.unknown_counts` 和观察器的 `unknownEvents`；原生答复只保留有限种类的操作名、事件名和 JSON 形状，不保留未知载荷、任意字段名、凭据或用户配置。运行结束时以现有 `EvidenceRef` 形式保存 `native-run-facts.json`，引用在运行模块的 `RunResult.evidence_refs` 中；事实保留与观察器可见性由 `test_batch4_facts` 验证。证据引用仅供读取证据文件，不作为角色或 Host 的事实投影；`native_evidence()` 保持 eventCount、toolAllowlist、streamEof 三个键，不携带 evidenceRefs。未识别事件怎样进入角色与 Host 的结果，由共用的出口统一处理，四个 harness 后续一起接入，本轮不另建投影。无法识别的选择字段是 `None`，不会用请求值代替，也不会进入 `ResultConfiguration.checked`；已识别的供应方、模型、模型 options 中的推理强度或 thoughtLevel.current 只要有一项与请求不同，就在模型开始之前以原错误码 `configuration-mismatch` 拒绝，即使其他字段仍未知；未知字段不会掩盖已确认的差异。最终答案无法识别时 raw 是 `None`、schemaStatus 是 `unknown`；原生成功终态只证明原生完成，不能证明答案满足角色契约。模型目录无法识别时 discoveries.status 是共享目录契约已有的 `unknown`，并给出不完整警告，只有已识别的空模型列表才是 complete。

下面的测试名省略共同模块前缀 `buddy.harnesses.zcode.`，`test_batch4_facts` 中的纯单元与私有假原生进程测试全部属于本轮验证范围。

| 函数与原错误码（以 TSV 原记录区分） | 类别 | 处理及拒绝边界 | 对应测试 |
| --- | --- | --- | --- |
| `native_run.selected / configuration-unavailable` | F | 删除固定选择对象的拒绝，逐字段返回已识别值或 None，配置调用记未识别事实，继续原生设置与回合。 | `test_batch4_facts.NativeShapeRoundTests.test_unknown_native_selection_continues_with_result_and_observer_facts`；`test_zcode_protocol.ConfigurationTests.test_unknown_selection_readback_continues_with_retained_facts` |
| `native_run.NoToolProtocol.observe / invalid-protocol`（通知参数对象） | F | 非归属通知的参数形状记事实并继续；承载会话或回合归属的通知缺少身份参数仍拒绝。 | `test_batch4_facts.NoToolShapeTests.test_notification_shape_continues_with_fact`、`test_missing_lifecycle_parameters_and_payload_still_cannot_prove_identity`；`NativeShapeRoundTests.test_notification_and_event_payload_shapes_continue_with_result_facts` |
| `native_run.NoToolProtocol.observe / invalid-protocol`（事件 payload 对象） | F | 普通载荷形状记事实并继续；会话、序号、回合核对仍执行，开始或完成缺少 inputId 不能建立完成证据。 | `test_batch4_facts.NoToolShapeTests.test_event_payload_shape_continues_with_fact`、`test_missing_lifecycle_parameters_and_payload_still_cannot_prove_identity`；`NativeShapeRoundTests.test_notification_and_event_payload_shapes_continue_with_result_facts` |
| `protocol.NativeConnection.call / invalid-protocol` | F | 非对象 RPC 结果通过回调记形状事实，规范化为没有已识别字段的对象并继续；所需身份及关闭字段由操作调用处核对，原生 error 仍是 native-rpc-error。 | `test_zcode_protocol.NativeConnectionShapeTests.test_nonobject_rpc_results_continue_with_bounded_content_free_facts`、`test_native_rpc_error_still_rejects_without_retaining_error_content`；`test_batch4_facts.NativeShapeRoundTests.test_rpc_shape_continues_with_result_and_observer_facts` |
| `protocol.RootTurnEvidence.observe / invalid-protocol`（事件 payload 对象） | F | 普通事件及没有待核对回执的工具事件记形状事实并继续；开始、完成、真实失败、待完成工具或问询回执缺少必需证据仍拒绝，不能产生签收回执。 | `test_zcode_protocol.ReceiptTests.test_nonobject_ordinary_events_are_retained_and_root_receipt_can_complete`、`test_nonobject_evidence_events_keep_identity_and_completion_guards`、`test_unrecognized_tool_payload_without_pending_receipt_is_a_fact` |
| `native_run.configure_session / invalid-configuration`（完整 spec） | C merge | Host 在打回 1 中决定本轮原样保留：共享 RunConfiguration 允许纯空白字符串，直接删除会放松我方输入校验，共享边界修复另做。 | `test_batch4_facts.BoundaryAndDrainTests.test_whitespace_configuration_guard_remains_until_shared_boundary_is_fixed`；`test_zcode_protocol.ConfigurationTests.test_run_configuration_boundary_refuses_missing_or_blank_fields`（这里 blank 指空字符串） |
| `native_run._continuation_of / invalid-resume-mode`（previous 字符串） | C merge | Host 在打回 1 中决定本轮原样保留：共享 OptionalText 同样允许纯空白 previousSessionId，此处拒绝仍必要，共享边界修复另做。 | `test_batch4_facts.BoundaryAndDrainTests.test_continuation_boundary_rejects_invalid_mode_and_missing_session` |
| `native_run._continuation_of / native-resume-unavailable`（native-session 无 previous） | C merge | 删除重复判断，RunContinuation 的构造及解码边界已要求 native-session 提供 previousSessionId。 | `test_batch4_facts.BoundaryAndDrainTests.test_continuation_boundary_rejects_invalid_mode_and_missing_session` |
| `native_run._continuation_of / invalid-resume-mode`（mode 枚举） | C merge | 删除重复枚举拒绝，直接使用已通过 RunContinuation.mode Literal 边界的值。 | `test_batch4_facts.BoundaryAndDrainTests.test_continuation_boundary_rejects_invalid_mode_and_missing_session` |
| `config.provider_access_types / invalid-provider-config` | I | 原拒绝在当前源码中已不存在；批次 3c 的实现把不可读取或无法识别的可选计费元数据归为未知，供应方和模型身份仍来自原生目录，本轮不重复改写。 | `test_native_config.NativeConfigTests.test_unreadable_billing_files_preserve_native_catalog_and_names`、`test_unreadable_builtin_billing_content_does_not_refuse_preparation` |
| `native_run.configure_session / configuration-mismatch` | I | 按 Host 对待定项的处置拆开：缺失或无法识别的回读记事实，checked 对应字段为空，继续回合；任何已识别值与请求不同仍在模型开始前以原错误码拒绝，模型 options 与 thoughtLevel 的已确认差异也不能被改写为未知。已识别目录明确缺少模型或 effort 的能力拒绝保留。 | `test_batch4_facts.NativeShapeRoundTests.test_unknown_native_selection_continues_with_result_and_observer_facts`、`test_confirmed_readback_mismatch_is_rejected_before_model_start`；`test_zcode_protocol.ConfigurationTests.test_unknown_selection_readback_continues_with_retained_facts`、`test_identified_selection_mismatch_is_rejected_even_with_other_unknown_fields`、`test_unrecognized_thought_level_is_unknown_and_configuration_continues`；Host 补跑 `test_zcode.ZcodeAdapterTests.test_confirmed_model_readback_mismatch_is_rejected_before_work`、`test_unrecognized_model_readback_continues_and_retains_unknown_facts` |
| `native_run.check_preparation / ADAPTER_UNAVAILABLE` | I | 原来混入的供应方内容解析已不存在；当前只检查命令与安装内置文件位置，实际命令不可用或安装能力缺失仍拒绝；可选内容不可读不会拒绝准备。 | `test_native_config.NativeConfigTests.test_availability_and_preparation_do_not_read_provider_files`、`test_missing_installed_builtin_is_unavailable_before_any_process`、`test_unreadable_builtin_billing_content_does_not_refuse_preparation` |
| `native_run.NoToolProtocol.observe_metadata / invalid-protocol` | I | 拆开种类清单和归属、序号；未知 kind 记事实，未知的会话级元数据可以没有 turnId，已有 turnId 仍核对，外会话、外回合、乱序仍拒绝。 | `test_no_tool_zcode.NoToolPolicyTests.test_unknown_metadata_continues_with_fact_but_reordered_metadata_is_refused`；`test_batch4_facts.NoToolShapeTests.test_metadata_identity_and_sequence_still_reject`、`test_unknown_session_metadata_without_turn_identity_is_a_fact` |
| `native_run.NoToolProtocol.observe / native-turn-failed`（完成条件） | I | 重复完成或 inputId 不符分别归为 wrong-native-turn；非成功的 resultType 仍如实报 native-turn-failed。 | `test_batch4_facts.NoToolShapeTests.test_completion_identity_failure_and_size_limit_still_reject` |
| `native_run.NoToolProtocol.observe / invalid-native-result`（答案形状及 65536 字节） | I | 非字符串答案记未知事实，继续到结算，raw 保留 None；字符串超过 65536 UTF-8 字节仍拒绝，不截断，也不把数组转成答案。 | `test_batch4_facts.NoToolShapeTests.test_unknown_answer_shape_is_unknown_and_settlement_continues`、`test_completion_identity_failure_and_size_limit_still_reject`；`NativeShapeRoundTests.test_unknown_final_answer_continues_with_unknown_value` |
| `native_run._drain / no-tool-violation` | I | 已确认停止后的原生交互改成 interaction-after-settlement 事实，继续排干并交给观察器；无主 RPC 答复仍拒绝，观察器的停止仍使流不完整。 | `test_batch4_facts.BoundaryAndDrainTests.test_late_interaction_continues_drain_and_reaches_observer`、`test_unclaimed_response_still_rejects`；`test_native_run.FastSeamTests.test_a_late_tool_frame_after_settlement_is_the_role_call_not_the_scopes` |
| `native_run._admit_root_input / native-admission-failed` | I | 会话不符独立报 wrong-native-session；同会话明确未接受输入仍报 native-admission-failed，两者都保留拒绝。 | `test_batch4_facts.NativeShapeRoundTests.test_admission_session_mismatch_and_real_refusal_still_reject` |
| `native_run.run_discovery / native-shutdown-failed` | I | 未确认进程组停止仍报 native-shutdown-failed；已确认停止但非零退出独立报 native-process-failed，并保留 discovery_shutdown_confirmed=True。 | `test_native_run.DiscoveryStopTests.test_abnormal_exit_keeps_actual_confirmed_stop`、`test_successful_read_with_unconfirmed_stop_is_refused` |
| `protocol.NativeConnection._pump / invalid-protocol`（16 个未领用答复） | I | 保留：这是明确的外进程未领用答复数量上限，符合 ADR-030 第 7 条；不把它改成串回合猜测，也不取消上限。 | `test_zcode_protocol.NativeConnectionShapeTests.test_sixteen_unclaimed_native_responses_remain_a_resource_cap` |
| `protocol.RootTurnEvidence.observe / native-turn-failed`（完成条件） | I | 重复完成或 inputId 不符独立报 invalid-provenance，非成功 resultType 仍报 native-turn-failed；签名、顺序、回执和唯一完成边界保留。 | `test_zcode_protocol.ReceiptTests.test_completion_duplicate_and_wrong_input_remain_provenance_failures`、`test_wrong_input_and_reordered_events_cannot_establish_a_root` |

找不到的原条件有两项：`provider_access_types / invalid-provider-config` 的抛错与 `check_preparation` 中的供应方内容读取或解析失败。函数还在，前者已经返回未知计费，后者没有读内容；不把这些旧行号当成仍存在的拒绝。本范围没有 G 项。

Host 在打回 1 中已决定本轮保留表中的 C 配置完整性与 previousSessionId 字符串检查。实际复现为 `RunConfiguration(provider=" \t", model="m", effort="e")` 与 `RunContinuation(mode="native-session", previous_session_id=" \t")` 都能构造成功，而原 ZCode 检查拒绝这些值。若要完成 C 的全部合并，需在共享 `buddy/harnesses/run_contract.py` 的这两个请求字段上补齐纯空白校验（底层通用类型来自 `protocol/internal_models.py`，不建议为本任务改变所有 Text 的含义），给共享边界加相应测试，再删除这两处本地判断。共享文件不在本轮写入范围，所以这两行停在原检查，没有通过范围外修改完成它们。

验证命令使用 Host 指定的现成只读 Python，记为 `<prepared-python>`；先清除 AGENTS.md 列出的十个继承变量，再以 `PYTHONPATH=src:tests/python <prepared-python> -m unittest` 运行 `buddy.harnesses.zcode.test_batch4_facts`、`buddy.harnesses.zcode.test_zcode_protocol`、`buddy.harnesses.zcode.test_no_tool_zcode`、`buddy.harnesses.zcode.test_native_config`、`buddy.harnesses.zcode.test_native_run.FastSeamTests`、`buddy.harnesses.zcode.test_native_run.DiscoveryStopTests`、`buddy.harnesses.zcode.test_native_run.NoToolReverseRequestTests`。这些测试不安装依赖，不调用模型，假 ZCode 使用私有目录。打回 1 替换确认差异继续运行的旧断言，并增加未知 thoughtLevel 形状的测试；最终源码上的同一组 119 项测试全部通过（57.819 秒），git diff --check 通过。修复前，新测试复现了六种已识别差异未拒绝及一次错误模型实际进入回合的问题；修复后，私有假进程测试证明确认差异没有发出 session/send，未知回读仍继续并留下运行结果与观察器事实。打回 2 只修改角色测试断言与本文件，重新运行 test_batch4_facts 的 20 项测试全部通过（4.816 秒），git diff --check 通过；修改后的角色控制器测试因本环境限制未运行，由 Host 补跑 `buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_unrecognized_model_readback_continues_and_retains_unknown_facts`。

打回 3 在最终源码上运行 `buddy.harnesses.zcode.test_batch4_facts`、`buddy.harnesses.zcode.test_no_tool_zcode` 和 `buddy.harnesses.zcode.test_native_run.FastSeamTests`，44 项全部通过（32.483 秒），git diff --check 通过；证据文件内容与运行结果引用的断言保留，投影断言改为没有 evidenceRefs。共用的 test_registered_run_wiring.py 未修改、未运行，由 Host 验证。

Host 补跑的受影响测试是 `buddy.harnesses.zcode.test_zcode` 整个文件（含上表修改的配置回读断言），以及 `buddy.harnesses.zcode.test_native_run.WorkerSeamTests`、`buddy.harnesses.zcode.test_native_run.ProducerContractTests`；这些 Worker/Producer 路径会建立 C-Two IPC、共享内存或启动受管角色，不在本环境尝试。Host 还负责完整检查、真实回合及四项故障注入。角色层的摘要目前不带 `native-run-facts`，等共用的一片补上；打回 3 恢复 `native_evidence()` 的三个原键，证据文件及 `RunResult.evidence_refs` 中的引用照旧保留，Host 需原样补跑 `buddy.roles.test_registered_run_wiring`，包括 NativeEvidenceProjectionTests 与 FastRegisteredRunTests；打回 2 的角色测试只断言回合完成和 resolved 未冒充已核对配置。本轮没有运行真实 ZCode、HTTP 模型服务或前端检查。

Host 可在允许真实启动器与进程枚举的环境下、不调用模型地检查真实读取路径：使用最终源码的 Python，把实际冻结的 ZCode 命令配置交给既有发现环境，创建一个临时工作目录与两个私有运行目录，直接调用 `native_run.run_discovery()` 并打印 discoveries、warnings 和供应方/模型数。此路径只做 capabilities、session/create、模型快照读取、session/close 与停止，源码中没有 session/send、setModel 或 setThoughtLevel。关闭进程仍须得到确认；未知目录不能写成 complete。调用示例如下，`<prepared-python>` 和用户的发现环境由 Host 提供，运行前清除继承 Worker 凭据，不把示例变成日常服务操作。

```python
import json
import tempfile
from pathlib import Path
from hey_my_buddy.buddy.harnesses.zcode import native_run

with tempfile.TemporaryDirectory(prefix="zcode-no-prompt-") as root:
    base = Path(root)
    cwd = base / "empty"
    cwd.mkdir(mode=0o700)
    receipt = native_run.run_discovery(
        cwd=str(cwd), invocation_root=base / "reading", native_root=base / "sessions",
        timeout_seconds=20, cancelled=lambda: False,
    )
    print(json.dumps({
        "discoveries": receipt["discoveries"], "warnings": receipt["warnings"],
        "providers": len(receipt["providers"]),
        "models": sum(len(p["models"]) for p in receipt["providers"]),
    }, ensure_ascii=False, indent=2))
```

如需在真实读取中直观看到“未知形状仍返回”，Host 可在上述脚本中临时用 unittest.mock 包住 `NativeConnection.call`：调用原方法取得真实 session/create 快照后，只把内存里的 `snapshot["settings"]["model"]["available"]` 改成 None 再返回；不写配置、不送提示词、不改变其他操作。该读取得到 `status="unknown"` 与形状不完整警告，并照常关闭真实原生进程。私有假原生进程的 `test_batch4_facts.NativeShapeRoundTests.test_unrecognized_catalog_read_continues_without_a_model_prompt` 已验证这个注入方法，真实 ZCode 的执行与验收由 Host 进行。
