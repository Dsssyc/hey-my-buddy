# ADR-030 批次 4：DSH 局部修改与未决边界

2026-10-12，以输入提交 `8bdb7358`（0.30.0）为依据，按路径、函数和错误码重定位清单的 13 处。清单的旧行号未用于定位。本记录是 Worker 的局部实现与验证说明，尚未经过 Host 验收。

运行事实通过已有的 `RunResult.evidence_refs` 保存为 `dsh-native-facts`，文件位于本次调用的私有根；引用包含字节数和 SHA-256。角色接收的运行结果携带该引用，Host 可从保留的运行结果帧及其引用检查文件，常规 Worker 摘要当前没有自动展开这份诊断。事实只保留固定阶段名称、未知状态、累计次数、最后一次形状以及既有 `content_meta` 的字节数和短摘要，不保存原生配置正文或凭据；实际发送的工具补丁另由已有的 `effective_policy.tools.requested` 记录。原生响应仍先通过 ACP 的帧大小、请求关联和队列上限，新增事实没有另设传输格式。

下表的测试名除特别说明外均属于 `buddy.harnesses.dsh.test_refusal_facts.RefusalFactsTests`；所有列出的新测试均已执行。F 类的两处和 C 类的一部分遇到范围外根因，按任务要求保留拒绝并等待 Host，不声称已完成整个微任务。

| 函数与错误码 | 类别 | 当前处理与依据 | 对应测试名 |
| --- | --- | --- | --- |
| `acp/client.py::initialize` / `ACP_PROTOCOL_FAULT` | F | 删除字典形状拒绝，返回有界原生结果；`_initialize` 记录握手形状未知，版本保持未知，回合和无提示词读取继续；嵌套 `agentInfo` 形状未知也记脱敏事实。 | `test_unknown_initialize_shape_continues_with_redacted_handshake_fact`；`test_unknown_agent_info_shape_is_retained_without_failing_handshake`；`test_discovery_keeps_unknown_handshake_fact_without_prompt` |
| `acp/client.py::new_session` / `ACP_SESSION_CWD` | C / merge | 删除重复绝对路径检查；运行路径由 `RunRequest.cwd` 校验，发现路径使用角色分配的绝对私有目录；内部客户端直接发送已校验参数。 | `test_request_boundary_rejects_relative_cwd_and_invalid_scope`；`test_client_transmits_boundary_validated_cwd_for_new_and_resume` |
| `acp/client.py::resume_session` / `ACP_SESSION_CWD` | C / merge | 删除重复绝对路径检查，依赖请求边界。 | `test_request_boundary_rejects_relative_cwd_and_invalid_scope`；`test_client_transmits_boundary_validated_cwd_for_new_and_resume` |
| `native_run.py::tool_scope_launch` / `scope-configuration-unconfirmed` | G | 清单空时继续使用实际可构造的补丁，记录工具范围未知与实际关闭行；不宣称已证实工具限制。结果的补丁取自本次实际发送的行，不在收集时重新读常量。 | `test_unconfirmed_tool_rows_continue_with_actual_patch_and_unknown_fact` |
| `native_run.py::tool_scope_launch` / `invalid-tool-scope` | C / merge | 删除重复枚举拒绝；唯一源码调用方 `run` 接收已经校验的 `RunRequest.tool_scope`，内部仅投射三种合法值。 | `test_request_boundary_rejects_relative_cwd_and_invalid_scope`；原有 `test_native_run.FastSeamTests.test_a_read_scope_reports_the_preset_and_injects_only_its_key`、`test_the_none_scope_patch_reaches_the_launch_argv_and_policy` 由 Host 跑 |
| `native_run.py::_continuation_of` / `invalid-resume-mode` | C / merge，部分待定 | 删除边界已覆盖的类型检查；保留纯空白检查，因为 `RunContinuation.previous_session_id` 当前仅校验非空字符串，实际接受纯空白，直接删掉会放松我方参数拒绝。 | `test_continuation_boundary_rejects_invalid_previous_session`；`test_blank_previous_session_is_still_refused_at_dsh_until_boundary_fix` |
| `native_run.py::_continuation_of` / `native-resume-unavailable` | C / merge | 删除重复必填检查，`RunContinuation` 的 native-session 模式已经要求上一会话 ID；保留其他地方对真实私有绑定、任务、会话、工作目录和配置的归属核对。 | `test_continuation_boundary_rejects_invalid_previous_session`；`test_continuation_projection_uses_validated_modes_and_identities` |
| `native_run.py::_configure` / `configuration-unavailable`（configOptions 形状） | F | 非列表形状记录未知，继续回合，不猜另一种原生格式，也不发送猜测的选择值；provider、model、effort 的 checked 值均为空。识别出的目录确实没有请求模型时仍拒绝。 | `test_unknown_config_options_continues_without_claiming_checked_values`；`test_recognized_catalog_without_requested_model_still_refuses`；`test_native_configuration_refusal_is_not_an_unknown_shape` |
| `native_run.py::_configure` / `configuration-mismatch`（model） | I | 拆为未识别/缺失回读和明确不同两支：前者记未知，provider/model 的 checked 值为空，继续核对 effort；后者按清单的 Host 处置规则保留拒绝，不能将另一个配置的调用记在请求的评价单位下。非空但与声明值不同的原生编码也保守拒绝，未增加第二套解析。 | `test_unknown_model_readback_continues_and_only_effort_is_checked`；`test_confirmed_different_model_still_refuses_before_prompt` |
| `native_run.py::_configure` / `configuration-mismatch`（effort） | I | 同样拆开：未知回读记事实且 checked effort 为空；已确认不同的强度仍拒绝。识别出的目录没有请求强度也保持拒绝。 | `test_unknown_effort_readback_continues_and_model_stays_checked`；`test_confirmed_different_effort_still_refuses_before_prompt`；`test_recognized_catalog_without_requested_effort_still_refuses` |
| `native_run.py::_settle_stop_reason` / `invalid-protocol`（缺失 stopReason） | F，暂停 | 保持原实现：共享结束状态没有 unknown，角色以 ok 判定成功，无法在 DSH 范围内既继续又如实表达未知；具体依赖见下节。 | `test_missing_stop_reason_remains_refused_pending_shared_unknown_end` |
| `native_run.py::_final_message_rounds::chunk_fold` / `answer-too-large` | I | 保留 65536 字节外来文本上限；这是边界资源限制，没有可以移成形状事实的另一半。本项不涉及收尾摘要数组。 | `test_final_answer_byte_limit_still_refuses`；`test_final_answer_at_byte_limit_continues` |
| `native_run.py::run_discovery` / `catalog-unavailable`（configOptions 形状） | F，暂停 | 保持原实现：目录聚合器会把返回的 unknown 改成 complete，单改 DSH 会把未读懂的目录发布成完整空目录；具体依赖见下节。会话 ID 缺失与关闭/进程停止未确认必须继续拒绝。 | `test_unknown_catalog_shape_remains_refused_pending_aggregate_fix` |

## 找不到的与替换的

13 处全部找到，没有随早前批次消失的清单行。删除的旧断言是 `test_native_run.FastSeamTests.test_an_unconfirmed_row_list_refuses_to_launch`，同一改动中由新的继续运行、实际补丁与未知事实测试取代；没有保留两种预期。两处暂缓的 F 类仍有明确的现状测试，后续完成转换时应在同一改动里替换这些现状断言。

## 需要 Host 处理的范围外根因

1. `buddy/harnesses/run_contract.py::EndStatus` 只允许 ok、error、cancelled，`buddy/roles/run_execution.py` 和 `run_controller.py` 根据结束状态给出业务判定与退出码。把缺失 `stopReason` 变成事实需要共享结果与角色语义支持未知结束，同时守住“未知不能成为确认成功”。DSH 的受管路径还经过 `protocol.py::RootTurnEvidence.settle`、`provenance` 和 `_governed_round`，依赖完成回执、结算与关闭的完整顺序；共享契约确定之后才能在 DSH 中继续关闭和收集，保留不完整证据且不建立可续接绑定。当前未修改这处 F 类及其后续证据要求。
2. `blackboard/catalog/catalog.py::discover` 在读取 `native_fact` 之后仍硬编码 `status: complete`，虽读取账户状态却丢弃本次目录读取的 status/reason。其 `canonical_payload` 已支持 unknown，因此需要在聚合边界保留 harness 的读取状态与原因，并验证空 providers 加 unknown 不变成模型缺席；然后才能把 DSH 未识别的 `configOptions` 变成未知目录，同时保留正确会话 ID、关闭确认、真实请求失败和真实进程停止检查。当前未修改这处 F 类，避免发布错误的完整目录。
3. `buddy/harnesses/run_contract.py::RunContinuation` 使用的 `OptionalText` 只要求非空、无 NUL 和有界，实测 `previous_session_id="   "` 被接收；native-session 模式校验也只看真值。Host 应在这一请求边界明确拒绝纯空白，且同时覆盖 reconstructed-new-session 的非空白身份，再删除 DSH 残留的空白拒绝。当前仅删了确实重复的部分，没有为共享模块添加补丁。

如需在常规角色摘要里直接显示诊断，`buddy/roles/run_execution.py` 还需按已有引用验哈希的读法展开 `dsh-native-facts`；本修改只交付运行结果中的既有证据引用，没有修改共享角色摘要协议。

## 已执行与留给 Host 的验证

指定只读解释器、检出源码优先、清除任务给出的 10 项继承环境变量后，最终局部命令通过 61 条测试，退出码 0。新增 `test_refusal_facts` 的 23 条测试整文件通过；`test_native_run.RoleSeamFunctionTests`、`test_dsh_tool_evidence`、`test_session_records` 也通过；另执行了 ACP `EnvelopeValidationTest` 的三个纯内存超大帧测试。实际回合、完整检查和真实原生启动器未运行，没有安装依赖、读取日常看板、读取用户配置或发起模型调用。

在检出根设置 `PYTHON_BIN` 为 Host 提供的冻结依赖解释器，然后可重放已执行命令；不要令它指向待安装的新环境。

```sh
env -u BUDDY_STATE_DIR -u BUDDY_RUNTIME_ROOT -u BUDDY_RUNTIME \
  -u BUDDY_RUNTIME_IDENTITY -u BUDDY_WORKER_STATE -u BUDDY_WORKER_ID \
  -u BUDDY_AGENT_CREDENTIAL -u BUDDY_AGENT_CREDENTIAL_FILE \
  -u VIRTUAL_ENV -u UV_PROJECT_ENVIRONMENT PYTHONPATH=src:tests/python \
  "$PYTHON_BIN" -m unittest \
  buddy.harnesses.dsh.test_refusal_facts \
  buddy.harnesses.dsh.test_native_run.RoleSeamFunctionTests \
  buddy.harnesses.dsh.test_dsh_tool_evidence \
  buddy.harnesses.dsh.test_session_records \
  buddy.harnesses.dsh.acp.test_acp_connection.EnvelopeValidationTest.test_oversized_lines_become_one_fault_each \
  buddy.harnesses.dsh.acp.test_acp_connection.EnvelopeValidationTest.test_line_without_newline_is_discarded_with_fixed_accounting \
  buddy.harnesses.dsh.acp.test_acp_connection.EnvelopeValidationTest.test_line_still_oversized_at_eof_is_also_a_fault
```

需要真实启动器、套接字或相关进程能力的回归，交给 Host 按以下模块名运行：`buddy.harnesses.dsh.test_native_run`（除上面已执行的纯函数类之外的运行/发现/受管回合/问询类）、`buddy.harnesses.dsh.test_native_resume`、`buddy.harnesses.dsh.test_no_tool_dsh`、`buddy.harnesses.dsh.test_dsh_role_wiring`、`buddy.harnesses.dsh.test_dsh_session_storage`、`buddy.harnesses.dsh.acp.test_acp_launch`、`buddy.harnesses.dsh.acp.test_acp_session`、`buddy.harnesses.dsh.acp.test_acp_connection`（除上面已执行的三个纯内存测试之外）。其中两个被拆开的 configuration-mismatch 和 answer-too-large 的保留拒绝已在本轮局部测试中验证；私有路径、原生续接归属与签名的跨进程回归仍由 Host 跑。

## Host 在真实 DSH 上的无模型读取办法

在检出源码下，由 Host 指定已确认的 DSH 可执行文件，以 `runtime_selection.bound` 只绑定该命令，使用新建的临时私有根直接执行 `run_discovery`，不经过服务或日常看板。它只发送 initialize、session/new、session/close，读取原生选择器后由现有代码确认进程停止；保留用户自己的原生配置供 DSH 自己读取。下面的第二次读取在真实 initialize 应答返回后只把客户端看到的形状替换为列表，验证改过的握手路径仍能在真实 DSH 上继续读取目录，返回 unknown 握手事实；这是 Host 的无模型形状故障注入，不代表原生 DSH 真返回了列表，也不验证暂缓的目录形状转换。

将代码作为临时探针运行，`sys.argv[1]` 传入 DSH 可执行文件的绝对路径；解释器从该检出的 `src` 导入源码。探针只打印方法名、目录读取状态和诊断，不输出用户配置正文。

```python
import json
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch

from hey_my_buddy.buddy.harnesses.dsh import native_run
from hey_my_buddy.buddy.harnesses.dsh.acp.client import AcpClient
from hey_my_buddy.buddy.harnesses.dsh.acp.connection import AcpConnection
from hey_my_buddy.buddy.harnesses.runtime_selection import bound

command = str(Path(sys.argv[1]).resolve(strict=True))
native_request = AcpConnection.request
native_initialize = AcpClient.initialize
methods = []

def read_request(self, method, params, timeout):
    assert method in {"initialize", "session/new", "session/close"}, method
    methods.append(method)
    return native_request(self, method, params, timeout)

def unknown_initialize(self, timeout):
    native_initialize(self, timeout=timeout)
    return []

for inject_unknown in (False, True):
    root = Path(tempfile.mkdtemp(prefix="adr030-dsh-read-")).resolve()
    cwd = root / "cwd"
    cwd.mkdir(mode=0o700)
    methods.clear()
    with bound([{"adapter": "dsh", "status": "ready", "command": [command]}]), \
         patch.object(AcpConnection, "request", read_request), \
         patch.object(AcpClient, "initialize",
                      unknown_initialize if inject_unknown else native_initialize):
        result = native_run.run_discovery(
            cwd=str(cwd), invocation_root=root / "invocation", native_root=root / "native",
            timeout_seconds=60, cancelled=lambda: False)
    assert methods == ["initialize", "session/new", "session/close"], methods
    if inject_unknown:
        assert result["harnessVersion"] == "unknown"
        assert any('"initialize"' in item and '"status": "unknown"' in item
                   for item in result["warnings"])
    print(json.dumps({"injectedUnknownHandshake": inject_unknown,
                      "methods": methods, "discoveries": result["discoveries"],
                      "warnings": result["warnings"], "evidenceRoot": str(root)}))
```

成功条件是两次都完成三项读取操作且 `run_discovery` 返回（它内部已确认正常退出与进程组停止），第二次保留握手未知诊断且不出现 session/prompt 或 authenticate。临时证据根供 Host 检查，收集后由 Host 删除；本 Worker 没有运行此探针。
