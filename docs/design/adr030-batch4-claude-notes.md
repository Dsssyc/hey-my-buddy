# ADR-030 批次 4：Claude Code 候选说明

基线为 `7a5fa88a`，按清单的路径、函数和错误码重新定位了 F 3 行、C/merge 1 行、I 10 行；G 为 0 行。本文件是候选改动和 Worker 测试记录，尚未经过 Host 验收。写入只涉及 Claude 运行模块、对应测试和本文件，未修改启动权限或沙盒参数。

表中的 N 表示 `tests/python/buddy/harnesses/claude/test_native_run.py`，P 表示 `test_claude_protocol.py`，R 表示 `test_claude.py`；函数路径都在 `src/hey_my_buddy/buddy/harnesses/claude/` 下。同一错误码的各个拒绝点按条件分别记行。

| 函数与错误码、条件 | 清单类别 | 处理和依据 | 对应测试名 |
| --- | --- | --- | --- |
| `native_run._catalog` / `invalid-catalog`，模型列表缺失或不是列表 | F | 删除形状拒绝；读取返回空的未解析模型集合，`discoveries.status=unknown`，warnings 记本次字段形状；回合可继续，配置成员未证实时不生成 checked 值。聚合状态的范围外问题见下文。 | N `test_unread_catalog_continues_discovery_as_unknown`、`test_unread_catalog_continues_the_turn_without_checked_configuration` |
| `native_run._catalog` / `invalid-catalog`，模型超过 200 项 | I | 保留数量上限，属于别的进程送来的内容超过上限；未知字段形状不绕过列表数量检查。 | N `test_oversized_catalog_still_refuses_with_confirmed_stop` |
| `native_run._prepare` / `INVALID_ARGUMENT`，无工具执行 | I | 保留；Claude 的描述明确没有原生 no-tool 能力，本任务不实现新的执行能力。 | N `test_this_harness_declares_no_no_tool_service_or_resume_capability` |
| `native_run._prepare` / `INVALID_ARGUMENT`，原生续接 | C / merge | 删除运行模块的重复拒绝；唯一能力检查保留在已有 `WorkerRunExecutor.prepare` 请求边界；没有新增续接实现。删除直接模块测试中的重复断言。 | R `test_native_session_resume_is_refused_and_fresh_sessions_are_allocated` |
| `native_run._check_catalog` / `invalid-configuration`，供应方、模型及 effort 目录成员 | I | 固定 anthropic 子条件已不存在；目录成员检查改为布尔事实，不因本次目录未列出而拒绝。匹配仍能证明 checked 配置；未匹配写 `configurationStatus=unknown` 并省略 checked 配置，原生真实拒绝继续上报。 | N `test_catalog_membership_absence_continues_as_unconfirmed`、`test_a_model_outside_the_catalog_continues_without_confirmed_membership`；R `test_model_and_effort_absence_is_reported_without_refusing_the_turn`；N `test_a_native_failure_keeps_the_observed_stream_end` |
| `native_run._TurnCollector.buffer` / `invalid-protocol`，初始化暂存超过 128 帧 | I | 保留别的进程输入的数量上限，与事件是否读懂无关。 | N `test_initialization_and_permission_request_bounds_still_refuse` |
| `native_run._TurnCollector.on_request` / `native-request-limit`，权限请求超过 32 条 | I | 保留别的进程输入的数量上限；既有拒绝回执继续关联其原生 request ID。 | N `test_initialization_and_permission_request_bounds_still_refuse`、`test_a_denied_interaction_reaches_the_role_before_further_waiting` |
| `native_run._check_terminal` / `native-turn-failed`，非 success 或 is_error 非 false | I | 暂不修改整个条件，等待 Host 决定共享表示；真实失败、归属和背景任务仍然拒绝，未知终态目前同样保留拒绝，不能伪造成功。原因见下文。 | N `test_unknown_terminal_remains_refused_pending_shared_contract`、`test_a_native_failure_keeps_the_observed_stream_end`、`test_wrong_session_and_duplicate_results_are_rejected`、`test_background_work_must_settle_before_the_result_is_usable` |
| `native_run.run_discovery` / `<dynamic>`，启动异常附停止事实再抛出 | I | 保留；包装本身不增加资格检查，不吞掉异常，只携带已持有进程的真实停止事实；未知绝不表示已停。 | N `test_discovery_failures_carry_the_actual_native_stop_fact`、`test_discovery_refuses_an_unusable_cli` |
| `native_run.run_discovery` / `<dynamic>`，初始化及目录异常附停止事实再抛出 | I | 未读懂的形状已在下层转换为事实并正常返回；真实 RPC 失败和列表超限仍抛出并附实际停止事实；即使目录未知，停止未确认仍拒绝。 | N `test_discovery_failures_carry_the_actual_native_stop_fact`、`test_oversized_catalog_still_refuses_with_confirmed_stop`、`test_discovery_metadata_error_with_an_unknown_stop_reports_false` |
| `protocol.Connection._deliver` / `invalid-protocol`，type 不是字符串 | F | 删除形状拒绝；有界帧继续送给既有 collector，记录未读 type 的形状、帧字节数和 SHA-256，不保存任意 native 文本。事实状态为 incomplete。 | P `test_frame_without_a_type_continues_with_bounded_shape_facts`；N `test_untyped_event_continues_and_reaches_role_and_retained_facts` |
| `protocol.Connection.call` / `invalid-protocol`，未结清请求超过 16 个 | I | 保留；这是我方控制请求的资源边界，防止调用方超量发起请求，与原生答复形状无关。 | P `test_control_request_resource_bounds_still_reject` |
| `protocol.Connection.call` / `invalid-protocol`，累计请求编号预算耗尽 | I | 保留我方控制请求数量预算，与原生形状无关；没有改变预算或增加重试。 | P `test_control_request_resource_bounds_still_reject` |
| `protocol.Connection.call` / `invalid-protocol`，非 error 答复不是 success 对象 | F | 归属检查先完成；真实 error 仍抛 `native-rpc-error`，其他未知形状返回 None 并记关联 ID、字段形状及有界帧摘要。初始化读取未知，interrupt 的未知答复也不算确认。 | P `test_unknown_control_response_continues_with_correlated_shape_facts`、`test_error_control_response_surfaces_as_rpc_error`、`test_unknown_control_response_identity_is_rejected`、`test_duplicate_control_response_is_rejected`；N `test_unknown_initialize_reply_continues_with_correlated_result_facts`、`test_unknown_interrupt_reply_does_not_confirm_acknowledgement` |

## 事实出口和边界

回合沿用 `RunResult.evidence_refs` 中的 `native-observations`，增加 protocol 的 incomplete 状态、未读字段计数、形状样本、样本截断事实，以及 catalogStatus、catalogWarnings 和 configurationStatus；这些字段不生成 CompletionEvidence、checked 配置或停止事实。既有 observer 的事实快照携带 protocolObservations；泛指所有未知事件的原 unknownEvents 字段仍为 None，避免将只计了缺失 type 的计数冒充全面分类。单帧仍先经过 8 MiB 严格 JSON 边界，最多留 16 个脱敏样本，其他帧只增加计数；sample 不保存未知 payload、字段内容、账户或原生错误文本。初始化目录沿用 warnings 出口，保留最多 14 个同样脱敏的样本，包括未读控制答复的关联 ID；没有另外建立日志或结果协议。

被取代的缺失 type 拒绝断言和即时目录资格断言已经替换；发现测试中的 invalid-catalog 失败样本改用真实原生 initialize error，停止未知测试则检查 native-shutdown-failed。`_interrupt` 只在原生答复读懂后确认 acknowledged；确认停止依旧独立。R 的夹具给控制器一个动态解析到当前检出 src 的绝对 PYTHONPATH，因为控制器切到私有检出后，相对 src 会误读冻结解释器安装的旧源码；这一修正不改变产品启动环境。

## 找不到的和留给 Host 的工作

14 个完整条目均找到了；找不到的是 `_check_catalog` 原复合条件中“供应方必须为 anthropic”的子条件，当前源码已随供应方限制移除而删除。本批未恢复它，第三方供应方的现有回归继续保留。

`_check_terminal` 暂不动：共享 `harnesses/run_contract.py` 的 RunEnd.status 只有 ok、error、cancelled，CompletionEvidence 只表示 stream_end，`roles/run_execution.py` 又只从 ok 组装 Worker 回合；Claude 的导入 provenance 明确要求 success 和 resultIsError=False。需要 Host 决定“终态已到达但成功未知”的共享表示及角色如何接收它，再拆分真实失败与未知终态；不能在本目录内把未知改成 ok，也不能把未知继续包装成新的真实失败码。本批对这个保留拒绝的条件加了回归，明确标为未完成处置。

`blackboard/catalog/catalog.py` 的目录聚合在读取成功返回的 payload 后，硬写 discoveries.status=complete，只复制 accountStatus。因此原生 `_catalog` 和 canonical_payload 已能保持 unknown，整份聚合目录仍会覆盖它；Host 需要在聚合边界保留 native_fact.status 及 reason，并加“未知目录不能清除已有完整目录”的回归。本批未改范围外文件，也未用抛错绕过这条缺陷。

`roles/run_execution.py` 的 `_worker_reports` 读取 native-observations 后只投影固定旧字段，丢掉 protocol、catalogStatus、catalogWarnings、configurationStatus，Review 摘要也走这个投影。事实目前可通过 RunResult 的既有 evidence_refs 和私有 native-observations.json 查看，observer 能收到 protocolObservations；Host 最终的 Worker/Review 摘要还需要在共享投影中保留这些有界字段，并用两种角色的结果测试验证。本批没有借用 sessionModel、quota 或 provenance 字段携带其他含义。

## 测试记录

使用 Host 提供的只读冻结 Python，运行前清掉 AGENTS.md 列出的 10 个 BUDDY/环境变量，未使用 uv 或安装依赖。第一次协议测试 28 条通过，新增事实路径的 9 条测试通过；完整受影响文件里发现被取代的旧断言后已同步替换。R 的子进程源路径问题由夹具修正后，目录成员未知的角色边界回归通过。

一次额外命令误包含了 test_claude_worker 及其导入的继承测试，六个 Claude 模块合计 180 条通过，但其中包含私有 daemon/Worker 和共享内存测试，超出了本任务“不启动任何服务与 Worker”的限制；测试命令已退出，夹具使用私有临时状态并按其 finally 和 cleanup 关闭子进程，未读取日常看板或启动已安装服务。此项明确作为执行偏差交给 Host；不以它替代 Host 要求的真实回合或完整检查。后续命令限于受影响的 P、N、R 三个文件，不再次运行 daemon/Worker 测试。

最终聚焦验证使用绝对 PYTHONPATH，确保控制器切换 cwd 后仍加载此检出的源码；下面的三个文件合计 132 条测试通过，退出码为 0，用时 45.753 秒。真实 Claude Code、付费模型回合、全仓检查及 Host 故障注入未执行；本任务没有剩余未跑的受影响测试方法，Host 仍须复核涉及共享内存的 R 控制器测试与 N 中 RoleSurfaceTests 的 C-Two endpoint 路径，以及 `test_claude_worker.ClaudeWorkerTests.test_governed_worker_seals_only_a_proven_claude_result`、`test_quota_failure_is_persisted_as_harness_error_without_a_capability_sample`。

```sh
env -u BUDDY_STATE_DIR -u BUDDY_RUNTIME_ROOT -u BUDDY_RUNTIME \
  -u BUDDY_RUNTIME_IDENTITY -u BUDDY_WORKER_STATE -u BUDDY_WORKER_ID \
  -u BUDDY_AGENT_CREDENTIAL -u BUDDY_AGENT_CREDENTIAL_FILE \
  -u VIRTUAL_ENV -u UV_PROJECT_ENVIRONMENT \
  PYTHONPATH="$PWD/src:$PWD/tests/python" <frozen-python> -m unittest \
  buddy.harnesses.claude.test_claude_protocol \
  buddy.harnesses.claude.test_native_run \
  buddy.harnesses.claude.test_claude
```

## Host 的真实 Claude Code 无模型读取核对

Host 可从当前源码根目录直接运行下面的初始化专用路径，把 `<frozen-python>` 和 `<claude-executable>` 替换为验收用解释器与已选定的 Claude Code 可执行文件。该路径只运行 version 与 initialize 控制请求，不发送 user 消息，不创建服务或 Worker，不发起模型调用，不向 Claude 传入配置覆盖；原生 Claude 会读取 Host 自己的配置。本 Worker 没有执行它。真实目录正常形状应读到 complete；字段变动应读到 unknown 和 warnings，成功返回本身只确认读取流程及原生进程组已停止。

```sh
env -u BUDDY_STATE_DIR -u BUDDY_RUNTIME_ROOT -u BUDDY_RUNTIME \
  -u BUDDY_RUNTIME_IDENTITY -u BUDDY_WORKER_STATE -u BUDDY_WORKER_ID \
  -u BUDDY_AGENT_CREDENTIAL -u BUDDY_AGENT_CREDENTIAL_FILE \
  -u VIRTUAL_ENV -u UV_PROJECT_ENVIRONMENT \
  BUDDY_DEV_SOURCE=1 BUDDY_CLAUDE_CLI="<claude-executable>" \
  PYTHONPATH="$PWD/src" <frozen-python> - <<'PY'
import json
import tempfile
from pathlib import Path
from hey_my_buddy.buddy.harnesses.claude.native_run import run_discovery

root = Path(tempfile.mkdtemp(prefix="buddy-claude-initialize-"))
catalog = run_discovery(cwd=str(Path.cwd()), invocation_root=root / "invocation",
                        native_root=root / "native", timeout_seconds=30,
                        cancelled=lambda: False)
print(json.dumps({"discoveries": catalog["discoveries"],
                  "modelCount": sum(len(p["models"]) for p in catalog["providers"]),
                  "warnings": catalog["warnings"]}, ensure_ascii=False, indent=2))
print("privateProbeRoot:", root)
PY
```

私有探针目录留给 Host 查 stderr；若异常的 discovery_shutdown_confirmed 不是 True，继续保留目录和停止证据，不能按“读取未知”自动删除。上述方法验证的是初始化读取，未知形状的继续及脱敏已由可重复的管道/夹具测试验证，真实模型回合仍由 Host 单独验收。
