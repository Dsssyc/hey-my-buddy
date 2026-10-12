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

## 收尾

2026-10-12，以 `98cb1536`（0.30.0，共用出口已合入）为依据的窄微任务：接上共用出口，并做完上一片暂缓的三处。写入范围是 `buddy/harnesses/dsh`、`tests/python/buddy/harnesses/dsh` 与本文；未安装、未做真实回合，等待 Host 验收。本节取代“需要 Host 处理的范围外根因”的三条与上表两处标为“暂停”的行：那三处的现状拒绝已在同一改动里全部删除，没有留下第二种读法。

### 共用出口

新增的 `_native_evidence_facts(state)` 把 `dsh-native-facts` 已经记下的形状事实投影成 `RunResult.native_evidence`：`initialize`、`agentInfo`、`configOptions`、`modelReadback`、`effortReadback` 五个固定阶段标签各按 `_note_native_shape` 的计数进入 `unknown_events.countsByType`，其和进入 `unknown_events.total`；`toolScope` 为 unknown 时取共享名 `tool-scope`。模型与推理强度的回读有没有被证实只看结果里已核对的配置（为空就是没有证实），它不进 `unconfirmed`；两个回读没读懂的形状仍按上面的计数留在 `unknown_events`。类型名只有这五个有界标签，短名字只有 `tool-scope` 一个；DSH 没有 `model-catalog`、`configuration-readback`（它是 Codex 那件事的名字）与 `thread-receipt` 的对应情形，都没有使用，也没有自造名字。事实来源只有 `state.native_facts`，投影不读引用以外的原生正文；`dsh-native-facts.json` 证据文件与它的 `EvidenceRef` 照旧写，没有增删引用。

运行模块自己的 `native_evidence(result)` 投影保持 `eventCount`、`disabledRows`、`streamEof` 三个 harness 特有键：它本来就没有与共用两项重复的键，所以这一处没有可删的内容，既有断言 `test_native_run.RoleSeamFunctionTests.test_native_evidence_projects_the_launch_scope_and_stream_end` 与 `test_no_tool_dsh.test_capture_reports_the_launch_scope_as_native_evidence` 原样保留。流内未知种类计数（`_RunFacts.unknown_counts`）继续只走观察器事实，本轮没有为它们建第二份投影，因为共用出口的事实来源是任务指定的 `dsh-native-facts`；`test_an_unknown_event_is_retained_and_continues_to_a_result` 的断言因此未动。

测试：`test_refusal_facts.test_unread_shapes_reach_the_shared_outlet_as_unknown_events`、`test_unread_configuration_readback_stays_an_unknown_event`、`test_unconfirmed_tool_scope_reaches_the_shared_outlet`，以及 `test_the_three_role_results_carry_the_shared_native_facts`（同一份真实运行结果经 `run_execution` 的快速、审阅与 Worker 三个投影，三处 `nativeEvidence.unknownEvents` 一致）。

### 结束原因缺失

`_settle_stop_reason` 不再抛 `invalid-protocol`：没有可读的 stop reason 时把运行状态置为 `unknown`、原因码置为本模块自己的 `native-stop-reason-unread`、消息置为一句有界说明，然后返回。最终消息载体继续关闭会话并让 `finally` 的收集跑完，但不再把这一轮的答案当成已结算的答案（不返回 raw、不发 settled 通知）；受管载体同样完成关闭，但不调用 `evidence.settle`、不写 provenance、不建立可续接绑定；`run` 不再在载体返回后把 `unknown` 覆盖成 `ok`。证据照实不完整：`streamComplete` 仍为假，快速载体的 `value` 与 `completionEvidence` 都是空，`continuation` 也是空；`RootTurnEvidence.settle` 自己的 stop reason 守卫保留，它是证据契约自己的检查，从载体已不可达。关闭未确认、进程未确认停止、真实请求失败与既有停止核对全部未动；共享角色层原有的“只有明确 ok 才算成功”也没有改。

测试：`test_refusal_facts.test_missing_stop_reason_ends_unknown_after_close_and_stop`（模拟连接：`unknown`、原因码、无 value/completion/binding、`session/close` 已发、shutdown 一次、group gone）与 `test_native_run.WorkerSeamTests.test_a_missing_stop_reason_ends_unknown_without_a_turn_or_binding`（真实假 agent 的受管回合：会话已关闭、无 `turn-provenance` 引用、无绑定文件、group gone）。被取代的 `test_missing_stop_reason_remains_refused_pending_shared_unknown_end` 已删除；测试夹具为此新增 `--stop-reason-unread` 与 `session-closed` 日志事件，都是加法。

### 发现时配置选项形状读不懂

`run_discovery` 把会话标识核对与配置选项形状拆开：会话标识缺失仍以 `catalog-unavailable` 拒绝；`configOptions` 不是列表时不再抛错，改由新的 `_unread_catalog` 返回 `discoveries[0].status == "unknown"`、原因 `config-options-shape-unreadable`、空 `providers` 与一条只带形状类型名的警告，共享的目录聚合按共用那片保留这次读取的状态与原因，不会发布完整空目录。会话关闭的确认、真实请求失败与进程停止的核对都没动；停止未确认时仍以 `native-shutdown-failed` 报错并带 `discovery_shutdown_confirmed=False`，不会当成一次未知读取。第二次读取里 `initialize` 形状未知仍只加警告、不改变读取状态。

测试：`test_unknown_catalog_shape_reports_an_unknown_read_with_reason`（取代 `test_unknown_catalog_shape_remains_refused_pending_aggregate_fix`）与新增的 `test_unread_catalog_shape_still_refuses_an_unconfirmed_stop`；形状正常时的断言（`test_discovery_reads_the_declared_selectors_without_a_prompt`、`test_discovery_keeps_unknown_handshake_fact_without_prompt`）未动。

### 残留的空白拒绝

`_continuation_of` 删掉上一片留下的纯空白检查，上一会话标识只在校验边界校验一次（`RunContinuation` 的 `_nonblank`）；`validate_turn_provenance` 对续接身份归属的检查不是这处重复校验，未动。被取代的 `test_blank_previous_session_is_still_refused_at_dsh_until_boundary_fix` 在同一改动里删除，`test_continuation_boundary_rejects_invalid_previous_session` 扩到 `"   "` 与 `"\t\n"`，并覆盖两种模式与重建续接的 `None`。

### 已执行与留给 Host 的验证

每个文件单独一个进程，源码来自本检出，依赖用任务给的只读解释器；与检查运行器一样给每个测试进程一个私有的临时 `BUDDY_STATE_DIR`（否则读该变量的 8 条 C-Two 测试在收集时就报 `KeyError`），其余任务列出的继承环境变量已清除，没有读或写日常看板、用户配置与凭据。通过的 9 个文件共 204 条：`test_native_run` 61、`acp.test_acp_connection` 39、`test_refusal_facts` 27、`test_session_records` 18、`test_native_resume` 15、`acp.test_acp_launch` 15、`acp.test_acp_session` 12、`test_dsh_tool_evidence` 11、`test_dsh_session_storage` 6；`git diff --check` 通过。Host 打回 1 之后重跑了这 9 个文件与两个留给 Host 的文件，结果与打回前相同（`unconfirmed` 的收窄只改 `test_refusal_facts` 的一条断言并把它拆成两条，其余文件未受影响）。

留给 Host 的两个文件在本环境不过，且在本检出的基线上失败数与失败点相同（都停在受管角色控制器子进程里的 ACP initialize：`native-disconnected`、原生退出码 3，与这次改动无关）：`buddy.harnesses.dsh.test_no_tool_dsh`（6 条，5 条不过）与 `buddy.harnesses.dsh.test_dsh_role_wiring`（12 条，9 条不过，只有 3 条 `DescriptionSeamTests` 通过）。Host 需要在自己的环境里补跑这两个文件，它们覆盖受管 Worker、受管发现路由与注册快速 seam。完整检查、真实 DSH 读取与回合、故障注入与安装仍由 Host 做；本节没有跑 `apps/console`，也没有启动任何服务与 Worker。
