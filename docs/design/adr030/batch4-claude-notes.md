# ADR-030 批次 4：Claude Code 候选说明

基线为 `7a5fa88a`，按清单的路径、函数和错误码重新定位了 F 3 行、C/merge 1 行、I 10 行；G 为 0 行。本文件是候选改动和 Worker 测试记录，尚未经过 Host 验收。写入只涉及 Claude 运行模块、对应测试和本文件，未修改启动权限或沙盒参数。

表中的 N 表示 `tests/python/buddy/harnesses/claude/test_native_run.py`，P 表示 `test_claude_protocol.py`，R 表示 `test_claude.py`；函数路径都在 `src/hey_my_buddy/buddy/harnesses/claude/` 下。同一错误码的各个拒绝点按条件分别记行。

| 函数与错误码、条件 | 清单类别 | 处理和依据 | 对应测试名 |
| --- | --- | --- | --- |
| `native_run._catalog` / `invalid-catalog`，模型列表缺失或不是列表 | F | 删除形状拒绝；读取返回空的未解析模型集合，`discoveries.status=unknown`，warnings 记本次字段形状；回合可继续，配置成员未证实时不生成 checked 值。聚合状态的范围外问题见下文。 | N `test_unread_catalog_continues_discovery_as_unknown`、`test_unread_catalog_continues_the_turn_without_checked_configuration` |
| `native_run._catalog` / `invalid-catalog`，模型超过 200 项 | I | 保留数量上限，属于别的进程送来的内容超过上限；未知字段形状不绕过列表数量检查。 | N `test_oversized_catalog_still_refuses_with_confirmed_stop` |
| `native_run._prepare` / `INVALID_ARGUMENT`，无工具执行 | I | 保留；Claude 的描述明确没有原生 no-tool 能力，本任务不实现新的执行能力。 | N `test_this_harness_declares_no_no_tool_service_or_resume_capability` |
| `native_run._prepare` / `INVALID_ARGUMENT`，原生续接 | C / merge | 删除运行模块的重复拒绝；唯一能力检查保留在已有 `WorkerRunExecutor.prepare` 请求边界；没有新增续接实现。删除直接模块测试中的重复断言。 | R `test_native_session_resume_is_refused_and_fresh_sessions_are_allocated` |
| `native_run._check_catalog` / `invalid-configuration`，供应方、模型及 effort 目录成员 | I | 固定 anthropic 子条件已不存在；按目录读取状态拆开：unknown 时不能比对，写 `configurationStatus=unknown`，省略 checked 配置并继续；complete 时缺少所选模型或 effort 属于能力缺失，仍在 user 消息前以原错误码拒绝；目录里有才生成 checked 配置，遵循 ADR-027 第 5 条。 | N `test_unread_catalog_continues_the_turn_without_checked_configuration`、`test_readable_empty_catalog_refuses_before_the_user_message`、`test_a_model_outside_the_readable_catalog_is_refused_before_the_user_message`、`test_an_effort_outside_the_readable_catalog_is_refused_before_the_user_message`、`test_the_catalog_check_confirms_only_what_native_readback_proved`；R `test_model_and_effort_absence_in_readable_catalog_refuses_before_model_start` |
| `native_run._TurnCollector.buffer` / `invalid-protocol`，初始化暂存超过 128 帧 | I | 保留别的进程输入的数量上限，与事件是否读懂无关。 | N `test_initialization_and_permission_request_bounds_still_refuse` |
| `native_run._TurnCollector.on_request` / `native-request-limit`，权限请求超过 32 条 | I | 保留别的进程输入的数量上限；既有拒绝回执继续关联其原生 request ID。 | N `test_initialization_and_permission_request_bounds_still_refuse`、`test_a_denied_interaction_reaches_the_role_before_further_waiting` |
| `native_run._check_terminal` / `native-turn-failed`，明确错误标志或错误 subtype | I | 原生明确的错误仍然失败，额度拒绝仍先归类；归属、初始化和背景任务检查保留。 | N `test_explicit_native_errors_remain_failures`、`test_a_native_failure_keeps_the_observed_stream_end`、`test_wrong_session_and_duplicate_results_are_rejected`、`test_background_work_must_settle_before_the_result_is_usable` |
| `native_run._check_terminal`，既未证实 success 且非错误，也未识别真实失败 | F | 最后接入共用 unknown，原因码为 `native-terminal-unread`；不生成完成证据、交付事实或 settled 成功通知。 | N `test_terminal_checks_separate_success_errors_and_unread_shapes`、`test_unread_terminal_ends_unknown_without_completed_delivery`、`test_unread_terminal_still_requires_confirmed_native_stop` |
| `native_run.run_discovery` / `<dynamic>`，启动异常附停止事实再抛出 | I | 保留；包装本身不增加资格检查，不吞掉异常，只携带已持有进程的真实停止事实；未知绝不表示已停。 | N `test_discovery_failures_carry_the_actual_native_stop_fact`、`test_discovery_refuses_an_unusable_cli` |
| `native_run.run_discovery` / `<dynamic>`，初始化及目录异常附停止事实再抛出 | I | 未读懂的形状已在下层转换为事实并正常返回；真实 RPC 失败和列表超限仍抛出并附实际停止事实；即使目录未知，停止未确认仍拒绝。 | N `test_discovery_failures_carry_the_actual_native_stop_fact`、`test_oversized_catalog_still_refuses_with_confirmed_stop`、`test_discovery_metadata_error_with_an_unknown_stop_reports_false` |
| `protocol.Connection._deliver` / `invalid-protocol`，type 不是字符串 | F | 删除形状拒绝；有界帧继续送给既有 collector，记录未读 type 的形状、帧字节数和 SHA-256，不保存任意 native 文本。事实状态为 incomplete。 | P `test_frame_without_a_type_continues_with_bounded_shape_facts`；N `test_untyped_event_continues_and_reaches_role_and_retained_facts` |
| `protocol.Connection.call` / `invalid-protocol`，未结清请求超过 16 个 | I | 保留；这是我方控制请求的资源边界，防止调用方超量发起请求，与原生答复形状无关。 | P `test_control_request_resource_bounds_still_reject` |
| `protocol.Connection.call` / `invalid-protocol`，累计请求编号预算耗尽 | I | 保留我方控制请求数量预算，与原生形状无关；没有改变预算或增加重试。 | P `test_control_request_resource_bounds_still_reject` |
| `protocol.Connection.call` / `invalid-protocol`，非 error 答复不是 success 对象 | F | 归属检查先完成；真实 error 仍抛 `native-rpc-error`，其他未知形状返回 None 并记关联 ID、字段形状及有界帧摘要。初始化读取未知，interrupt 的未知答复也不算确认。 | P `test_unknown_control_response_continues_with_correlated_shape_facts`、`test_error_control_response_surfaces_as_rpc_error`、`test_unknown_control_response_identity_is_rejected`、`test_duplicate_control_response_is_rejected`；N `test_unknown_initialize_reply_continues_with_correlated_result_facts`、`test_unknown_interrupt_reply_does_not_confirm_acknowledgement` |

## 事实出口和边界

回合沿用 `RunResult.evidence_refs` 中的 `native-observations`，保留 protocol 的 incomplete 状态、未读字段计数、形状样本、样本截断事实，以及 catalogStatus、catalogWarnings 和 configurationStatus；这些字段不生成 CompletionEvidence、checked 配置或停止事实。最后将两个已有协议计数接到共用 unknownEvents，observer 同样只带分类计数，删除携带样本的 protocolObservations 快照。单帧仍先经过 8 MiB 严格 JSON 边界，最多留 16 个脱敏样本，其他帧只增加计数；sample 不保存未知 payload、字段内容、账户或原生错误文本。初始化目录沿用 warnings 出口，保留最多 14 个同样脱敏的样本，包括未读控制答复的关联 ID；运行结果中的样本及含样本的 catalogWarnings 只保存在 native-observations 文件，不投影到三种角色结果。

被取代的缺失 type 拒绝断言已经替换；目录资格断言现区分读取 unknown 与读懂后确实缺失，后者保留能力拒绝。发现测试中的 invalid-catalog 失败样本改用真实原生 initialize error，停止未知测试则检查 native-shutdown-failed。`_interrupt` 只在原生答复读懂后确认 acknowledged；确认停止依旧独立。R 的夹具给控制器一个动态解析到当前检出 src 的绝对 PYTHONPATH，因为控制器切到私有检出后，相对 src 会误读冻结解释器安装的旧源码；这一修正不改变产品启动环境。

## 找不到的和留给 Host 的工作

14 个完整条目均找到了；找不到的是 `_check_catalog` 原复合条件中“供应方必须为 anthropic”的子条件，当前源码已随供应方限制移除而删除。本批未恢复它，第三方供应方的现有回归继续保留。

上一片留给 Host 的共享终态表示已在本轮输入中提供；本轮完成 `_check_terminal` 的拆分，见末尾“最后改动”。角色层只从 ok 组装完成回合，Claude 的导入 provenance 继续要求 success 和 resultIsError=False，未知终态不再使用真实失败码。

上一片指出的目录聚合覆盖 unknown 问题，Host 已说明在本轮输入中修复；本轮没有修改范围外的聚合代码，也没有重新运行其测试。

上一片指出的角色事实出口已由本轮输入中的共用 NativeEvidence 投影承接；本轮提供按类型的两个协议计数和 model-catalog 未证实项，并验证快速、审阅及 Worker 的共用位置。protocol 样本、catalogWarnings 和 configurationStatus 等 Claude 细节继续通过既有 evidence_refs 查看，本轮没有把样本带进结果摘要，也没有借用 sessionModel、quota 或 provenance 字段。

## 测试记录

使用 Host 提供的只读冻结 Python，运行前清掉 AGENTS.md 列出的 10 个 BUDDY/环境变量，未使用 uv 或安装依赖。第一次协议测试 28 条通过，新增事实路径的 9 条测试通过；完整受影响文件里发现被取代的旧断言后已同步替换。R 的子进程源路径问题由夹具修正后，目录成员未知的角色边界回归通过。

一次额外命令误包含了 test_claude_worker 及其导入的继承测试，六个 Claude 模块合计 180 条通过，但其中包含私有 daemon/Worker 和共享内存测试，超出了本任务“不启动任何服务与 Worker”的限制；测试命令已退出，夹具使用私有临时状态并按其 finally 和 cleanup 关闭子进程，未读取日常看板或启动已安装服务。此项明确作为执行偏差交给 Host；不以它替代 Host 要求的真实回合或完整检查。后续命令限于受影响的 P、N、R 三个文件，不再次运行 daemon/Worker 测试。

上一回合最后的聚焦验证使用绝对 PYTHONPATH，三个文件合计 132 条测试通过，退出码为 0，用时 45.753 秒；这记录的是打回前的候选，不能证明本轮修改。下面保留该命令供 Host 复核，Worker 本轮不执行其中的 R 控制器测试。真实 Claude Code、付费模型回合、全仓检查及 Host 故障注入未执行。

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

打回 1 按 ADR-027 第 5 条恢复读懂目录的能力检查，替换上一候选中“目录确实缺失仍继续”的三处断言，并增加“有模型而没有 effort”在 user 消息之前拒绝的直接运行回归。未知目录的继续与事实、已列出配置的 checked 回归继续保留。本轮只运行 N 全文件，69 条测试通过，退出码 0，用时 11.871 秒：它使用私有假 Claude 子进程和管道；RoleSurfaceTests 的 endpoint 用于内存中的 publish/observe，没有调用 start、注册服务器或分配共享内存，不启动 hey-my-buddy 服务或 Worker。R 修改后的源码通过 compile 语法检查，未执行其控制器测试。

上一轮需要 Host 跑 `test_claude.ClaudeAdapterTests.test_model_and_effort_absence_in_readable_catalog_refuses_before_model_start`，并建议按文件独立进程重跑 R 全文件，它通过角色控制器启动 C-Two 服务与共享内存，Worker 未执行；`test_claude_worker` 的 daemon/Worker 测试也没有再次执行。该轮尚未处理的终态与事实出口现由末尾“最后改动”更新。

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

## 最后改动

本轮输入为 `98cb1536`。`Connection.observations()` 的 unknownEvents 和 unreadResponses 分别映射为共用 `NativeEvidence.unknown_events` 的 `event-type-unread` 与 `control-response-unread`，只投影非零分类及其总和；分类是固定短名字，不来自原生载荷。本次运行实际读取的 catalogStatus 为 unknown 时，unconfirmed 只加入 `model-catalog`，不把目录成员检查当作设置生效的回读，也不新增其他未证实名字。无对应事实时共用角色投影仍省略空项。Claude 没有自己的 `native_evidence(result)` 函数，因此没有重复键需要从该函数删除；原有 observer 的 protocolObservations 样本快照被删除，改用相同的分类计数，样本、关联 ID、目录 warnings 与配置细节仍保存在原来的 native-observations 文件，三个角色结果只带共用两项。

`_check_terminal` 保留初始化、会话归属、背景任务与额度的既有检查，明确 `is_error=True`、`error` 或 `error_` subtype 继续使用真实失败 `native-turn-failed`，结构化额度拒绝继续使用 `quota-rejected`。只有 subtype 为 success 且 is_error 严格为 False 才确认成功；其他终态返回未证实，运行以 `unknown` 和本模块原因码 `native-terminal-unread` 结束，不制造原生失败包，不生成 CompletionEvidence 或 native-turn-facts，不发送 settled 成功通知，也不确认 StructuredOutput 交付工具。原生已给出的结构化值仍可作为 schema_status=unknown 的事实保留，角色层不采用它为答案或 Worker 完成交付。未知结束同样必须经过现有的停止确认；停止未确认或退出码非零时沿用 `native-shutdown-failed`。导入来源的 success 与 resultIsError=False 要求未改。真实失败仍在抛出后沿用原有结果组装路径，未借这次修改改变失败回合的值处理。

删除 `test_unknown_terminal_remains_refused_pending_shared_contract` 的未完成处置断言，替换为未知字段形状、明确错误、成功、停止未确认及来源导入检查。新增组合夹具同时产生两个未知分类、未知目录和未知终态，实际 read/write 运行经共用结果编码与解码后验证快速、审阅及 Worker 投影的唯一位置、分类总数、未证实项和样本不进入摘要；快速结果只验证共用投影，Claude 仍声明不支持原生 no-tool 运行，没有放松这项能力拒绝。未知终态的角色检查验证没有 rawAnswer、没有 Worker 交付调用或输出文件，仍保留模型启动、最后消息、流结束与停止事实。首轮共用出口检查有一条旧 Mock 夹具没有 observations 返回值，错误栈定位后只补齐该夹具，未增加运行时回退。

最终源码逐文件独立进程验证通过：`buddy.harnesses.claude.test_native_run` 74 条，11.378 秒，退出码 0；`buddy.harnesses.claude.test_claude_protocol` 28 条，0.132 秒，退出码 0，共 102 条，没有跳过。使用 Host 指定的只读冻结解释器，启动前清掉 AGENTS.md 列出的 10 个变量，再由 `cli.checks.child_environment` 为每个文件建立独立的私有 HOME、harness 配置根、状态根、运行时根与临时根，PYTHONPATH 指向本检出的源码。只运行管道、私有假 Claude 子进程及内存 endpoint 夹具，没有启动 hey-my-buddy 服务、Worker 或真实 harness，没有安装依赖、读日常看板或账户配置、发起模型调用。`git diff --check` 通过。

留给 Host 的环境受限检查为 `buddy.harnesses.claude.test_claude` 全文件（角色控制器使用 C-Two 服务及共享内存），其中受终态行为影响的既有回归包括 `ClaudeAdapterTests.test_duplicate_or_malformed_native_results_are_rejected`，成功与错误对照为 `test_completed_turn_uses_the_preallocated_session_and_never_resumes`；上一片交接的 `test_model_and_effort_absence_in_readable_catalog_refuses_before_model_start` 也仍需 Host 验证。`buddy.harnesses.claude.test_claude_worker` 的 daemon/Worker 检查本轮未运行，由 Host 的完整检查覆盖。全仓检查、真实 Claude 回合、故障注入及安装后行为均未验证，最终验收由 Host 完成；本轮没有修改角色层、目录聚合、前端、分支或标签，也没有提交。
