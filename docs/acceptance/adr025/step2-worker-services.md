# ADR-025 第二步微任务 2-A：Worker 角色与完成服务

2026-10-06，Worker 由 ZCode（GLM-5.3 / max）承担，微任务 `60b8504f-cf57-46ec-8fd5-258f419d9b96`、attempt `05c3a6e4-772c-46e8-bf9e-5d3f1753e8c4`、turn `5b0f4b01-ecc5-44ba-a73e-0445e146c137`。输入基线 `cc6e96ab251f38c482b38e5fe9f8d8956a2d7d4e`（2-P 已整合）。变更留在托管 worktree 工作区未提交，由 Host 核对 diff 后整合；本记录只写实际做过与验证过的事实。2-B/2-C 的外层 prepare/collect、RunRequest/RunResult 接线、注册表切换与旧入口删除均未做。

## 实际边界与变更

`buddy/roles/worker_services.py`（新增）：`governed_prompt` 的完整文本与组装自 `zcode/runner.py` 原样迁入（含 `ASSISTANCE_HINTS` 拼接与 canonical turn input 收尾，原生完全限定工具名仍是实参）；`OUTCOME_SCHEMA`/`ANSWER_SCHEMA`/`CHECKPOINT_SCHEMA`、三条工具描述、`ATTENTION_REFUSAL`、`INQUIRY_CHANNEL_ABSENT`、`attention_requests`、`read_inquiry_entries`、`pending_inquiries`、`inquiry_refusal`、`_footprint_bounded`、`_signed`、`_refusal`、`_checkpoint_batch`、`_finish_receipt`、`_answer_inquiry` 自 `zcode/mcp.py` 原样迁入；新入口 `session_tools()`（tools/list 内容，顺序不变）与 `call_session_tool(name, arguments, configuration)` 承载 tools/call 分派，完成边界顺序保持原样：`validate_outcome` 拒绝在前，`completed` 且 attention 未决次之，`completed` 且问询未答再次，仅 `completed` 被两类条件阻断。模块不 import codex/claude/zcode/dsh 四个具体 harness 包、不按 harness/模式字符串分叉；服务只拿 bridge 配置（身份、输入哈希、签名密钥、私有 journal/attention 路径），不拿黑板数据库或客户端。

`buddy/harnesses/session_receipts.py`（新增，确有生产共用）：`sign_receipt`、`serialized_footprint`、`read_shared_snapshot` 与问询字节预算（`MAX_QUESTION_BYTES`/`MAX_ANSWER_BYTES`/`MAX_INQUIRIES`/`MAX_INQUIRY_ID_BYTES`）、`MAX_JOURNAL_BYTES`（原 runner 与 mcp 各一份，现归一）、`INQUIRY_JOURNAL_VERSION`、`MAX_INQUIRY_RECEIPT_BYTES`、`TOOL_REFUSAL_REASONS` 与拒绝信封三项字节预算自 `zcode/protocol.py` 迁入。签名只用标准库 `hmac`/`hashlib`；canonical 编码与严格解码仍走根包 `json_codec` 一份；共享锁读仍走根包 `locking`；`MAX_TOOL_REFUSAL_DETAIL_BYTES = MAX_OUTCOME_BYTES` 仍从 `roles/turn_io` 单源取值。

`buddy/harnesses/zcode/mcp.py`：改为同一次原生会话的 stdio 载体，仅保留 `respond`（initialize/tools/list/tools/call/ping 的 JSON-RPC 组帧）与 `main`；工具清单与每次调用分派到 `worker_services.session_tools()`/`call_session_tool()`，未知工具错误文本不变；入口读帧上限（`MAX_OUTCOME_BYTES + 16384`）不变；严格解码直接取根包 `decode_strict_json`（原 `protocol.decode_json` 即其别名，同一实现）。六字段与问询规则的旧实现全部删除，无兼容转发。

`buddy/harnesses/zcode/protocol.py`：不再 import 角色模块。`verify_receipt(raw, configuration, validate_outcome)` 的六字段校验改为当前调用方显式注入的窄函数；`RootTurnEvidence` 新增必填关键字 `validate_outcome`（无默认值，不留暗选策略），在 `observe` 内原位传入。原生 root/session/call/order 核对、签名与 attempt 绑定、拒绝信封验证（`SESSION_TOOLS`、`COOPERATIVE_INQUIRY_NOTE` 仍属本模块）、问询回执验证与失败重试关联（`_retry_failed_finish`）全部保留，字节预算与 `sign_receipt` 改自 `session_receipts` 导入；`read_shared_snapshot`、`serialized_footprint`、`hashlib`、`locking` 等迁出后的残留引用清零。

`buddy/harnesses/zcode/runner.py`：仅做调用适配——`governed_prompt` 改自 `roles.worker_services` 导入（本地定义删除，`ASSISTANCE_HINTS` 导入随之移除）；`RootTurnEvidence(...)` 构造注入 `validate_outcome`（`roles.turn_io` 既有实现）；问询常量与 `read_shared_snapshot` 改自 `session_receipts` 导入。`InquiryBridge` 的事务、journal、socket 传输零改动（第五步才换 C-Two）；`MAX_JOURNAL_BYTES` 本地定义删除。

测试：`tests/python/buddy/harnesses/zcode/test_zcode_protocol.py` 与 `test_zcode_inquiry.py` 只改 import 来源与 `verify_receipt`/`RootTurnEvidence` 的注入实参，无断言语句变化；新增 `tests/python/buddy/roles/test_worker_services.py`（14 条，见编号表）。

## 范围偏离（一处，披露待 Host 裁定）

`tests/python/buddy/roles/test_assistance_hints.py` 不在本次列出的可写范围内，但它从 `zcode.mcp`/`zcode.runner` import `ATTENTION_REFUSAL`/`FINISH_DESCRIPTION`/`governed_prompt`，搬迁后必断。已做最小两行 import 适配（改自 `roles.worker_services`），断言与测试语义零变化；该文件超出字面 writeScope，特此披露，Host 可改判。

## 聚焦验证（实跑证据）

运行方式：`uv run --frozen python -m unittest -v <module>`，一模块一私有子进程，环境按 checks 的净化规则清除继承 `BUDDY_*`/`ZCODE_*`/`VIRTUAL_ENV`/`UV_PROJECT_ENVIRONMENT` 并设 `PYTHONPATH=<repo>/src:<repo>/tests/python`、`BUDDY_DEV_SOURCE=1`、`BUDDY_CONSOLE_PORT=0`、`BUDDY_STATE_DIR`/`BUDDY_RUNTIME_ROOT` 指向 `<task-root>/t` 下私有根；`TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 均为 `<task-root>/t`。共 15 个模块：ZCode 12 个全部（含 `test_zcode_native` 用本机已装 ZCode 加 localhost 模型 fixture，基线与事后各跑一次，无真实模型、无付费调用）加角色 3 个。基线（改动前）232 条编号全绿并冻结于 `<task-root>/m/baseline-test-ids.txt`；改动后同一批 232 条编号集合相等且全绿（`m/final-test-ids.txt`，`diff` 为空），新增 14 条、删除 0、改名 0。新增编号：`test_a_checkpoint_lists_only_this_attempts_still_answerable_questions`、`test_a_pending_inquiry_blocks_only_completed_and_names_the_question`、`test_a_withdrawn_question_no_longer_blocks_completion`、`test_an_accepted_finish_mints_a_receipt_bound_to_this_attempt_and_input`、`test_an_attention_record_is_counted_but_never_fatal`、`test_an_invalid_outcome_is_corrected_before_any_host_condition_is_named`、`test_an_outstanding_native_request_blocks_only_completed`、`test_answer_rules_are_bounded_and_bound_to_the_committed_question`、`test_attention_is_named_before_a_pending_inquiry_for_a_completed_outcome`、`test_importing_the_role_module_imports_no_harness_package`、`test_the_inquiry_paragraph_arrives_only_with_both_fully_qualified_tool_names`、`test_the_prompt_assembles_scope_finish_contract_hints_task_and_input_in_order`、`test_the_three_session_tools_carry_the_six_field_contract`、`test_without_a_journal_the_channel_is_absent_for_every_tool`（均在 `buddy.roles.test_worker_services`）。未跑完整检查、控制台与打包；未安装或升级日常运行时；未改用户配置、凭据或数据；未读凭据文件内容。

## 故障注入（隔离副本）

变异在 `<task-root>/m/fault-m1`…`fault-m3` 四份独立 `src` 副本上进行，worktree 生产源码零改动（`git status` 仅含交付改动为证）；每次运行先打印被测模块 `__file__` 确认来自变异副本。红 → 恢复（自 worktree 回拷该文件）→ 绿，四份副本恢复后 manifest 一致（`c114df642d25d06b5005fe3603e5bdbea6886f4cadfb9fb29ba1bb75ee74f4ab`，即基线字节）。日志与脚本在 `<task-root>/m/fault-injection-isolated.{log,zsh}`。

| 组 | 变异位置与做法 | 红（退出码 1） | 恢复后 |
| --- | --- | --- | --- |
| 完成格式 | `worker_services.call_session_tool`：`error = validate_outcome(arguments)` 置为 `None` | `FinishBoundaryTests.test_an_invalid_outcome_is_corrected_before_any_host_condition_is_named`（角色层）与 `FinishToolTests.test_unknown_tools_and_invalid_outcomes_are_bounded_errors`（经 mcp 载体）各失败一次 | 两测试 exit 0 |
| attention 拒绝 | 同函数：`if arguments["disposition"] == "completed" and attention_requests(...)` 改 `if False and ...` | `FinishBoundaryTests.test_an_outstanding_native_request_blocks_only_completed` 与 `FinishToolTests.test_completed_is_refused_while_a_native_request_is_unresolved` 各失败 | 两测试 exit 0 |
| inquiry 拒绝 | 同函数：`refusal = inquiry_refusal(...)` 置为 `None` | `FinishBoundaryTests.test_a_pending_inquiry_blocks_only_completed_and_names_the_question` 与 `FinishToolTests.test_completed_is_refused_while_a_question_is_unanswered` 各失败 | 两测试 exit 0 |
| 签名核对 | `zcode/protocol.verify_receipt`：跳过 `hmac.compare_digest` 比对 | `ReceiptTests.test_receipt_failures_distinguish_malformed_signature_identity_and_outcome` 与 `ReceiptTests.test_bridge_receipt_is_bound_to_input_identity_and_outcome` 各失败 | 两测试 exit 0 |

## 任务目录与收尾

任务目录 `/private/tmp/a252a-1yfl50kg`（Host 创建并登记）。`t/` 为 `TMPDIR`/`BUDDY_CHECKS_TMPDIR`，另含运行脚本自建的 `state/`、`runtime/` 私有根与测试 fixture 自动收尾后的空目录残留；`m/` 放运行脚本、基线/事后/最终编号清单、各模块原始输出、四份变异副本与日志，另有一次被隔离副本法取代的早期 worktree 内变异尝试留下的 `*.fault-backup` 与 `fault-injection.log`（该轮同样 SHA 校验恢复且事后全绿，仅方法不如隔离副本严格，未作为正式见证）；`h/`、`d/` 未用未动。本 Worker 未手动删除任何对象；未发现其他会话遗留材料。Host 验收后按该确切路径整体回收。

## 验证边界与接口缺口

- 角色层经 `mcp.respond` 与安装版 ZCode（`test_zcode_native`，localhost fixture）端到端复验；未做 2-D 范围的付费原生冒烟。
- `zcode/protocol.py` 仍持有 `SESSION_TOOLS`（验证侧闭集）与 `COOPERATIVE_INQUIRY_NOTE`（ZCode 问询送达事实）：前者是原生核对的工具名白名单，后者是本 harness 的能力表述，均非角色规则；2-B/2-C 若需要可将 `SESSION_TOOLS` 随信封格式并入 `session_receipts`，由 Host 统一裁定。
- `roles/turn_io.py`、`run_contract`、`live`、注册表、runtime 与其他 harness 零改动（`git status` 为证）；`test_assistance_hints.py` 为上文披露的唯一范围外触碰。
- 打包按 `packages = ["src/hey_my_buddy"]` 整包发现，新模块无需打包配置改动；wheel/sdist 未在本微任务重建（整步集成时一并验证）。

## 续轮修复（2026-10-06，同微任务续 turn `f02fd651-caf9-4153-bfea-fc167505c6a5`）

基线为 `cc6e96a` 加已 adopt 的 resolved artifact `b3af86b`；Host 续轮指派只修三项已核实缺陷，不重做已通过部分。`tests/python/buddy/roles/test_assistance_hints.py` 的两行导入适配已由 Host scope-amend 并 adopt（writeScope 现含该文件），本轮未再改动该文件，上文范围偏离登记维持。`MAX_OUTCOME_BYTES` 的根归属（现仍定义于 `roles/turn_io`、`session_receipts` 自其取值）与 `SESSION_TOOLS` 归属由 Host 在 2-B/2-C 统一处理，本轮未动任何公共只读文件。本轮实际改动三处：

1. `tests/python/buddy/roles/test_worker_services.py` 仅 `RoleBoundaryTests.test_importing_the_role_module_imports_no_harness_package`：原 `Path(__file__).resolve().parents[3] / "src"` 误解析为 `tests/src`，且子进程 env 只带 `PYTHONPATH`+`PATH`，在无本包安装的解释器上子进程丢失导入路径而报 `ModuleNotFoundError`（Host 实测），在本项目 venv 上则经 `_editable_impl_hey_my_buddy.pth` 兜底假绿（首轮自跑为绿的机制）。改为 `parents[4] / "src"`（真实本检出 `src`，本文件位于 `<checkout>/tests/python/buddy/roles/`）；子进程回报 `worker_services.__file__` 并断言其 resolve 后确属该 `src`；子进程 cwd 取测试私有临时目录（必在检出外，规避 cwd 假绿）；env 为 `PYTHONPATH` 加继承的 `PATH`/`TMPDIR`/`BUDDY_CHECKS_TMPDIR`。测试编号与名称不变。
2. `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py`：删除本地 `MAX_TOOL_REFUSAL_PREFIX_BYTES = 256` 遮蔽定义（连其注释块），仅保留 `session_receipts` 的共享单处定义；本模块自身使用经既有 import 行满足，`_refusal_payload` 行为不变。`test_zcode_protocol.py` 本就从 `session_receipts` 导入该常量，测试无需改动，未加静态厂商检查。
3. 本记录更正披露（见下节）。

### 续轮验证（实跑）

宿主场景复现（干净解释器 `/opt/homebrew/bin/python3.14`，其中无 `hey_my_buddy`；父进程 `PYTHONPATH` 指向本检出 `src`，另借项目 venv 的纯 Python `portalocker` 补依赖）：旧缺陷副本（`<task-root>/m/red-2a/`，仅把 source 行改回 `parents[3]`，本轮新建、未运行任何删除命令）该单测失败，失败信息即 Host 观察到的 `ModuleNotFoundError: No module named 'hey_my_buddy'`，退出码 1（`m/red-2a/red.out`）；同一副本换项目 venv 解释器仍失败——子进程经 editable 兜底导入成功，但新 `__file__` 来源断言发现模块来自 `<checkout>/src` 而非声称的 `tests/src`，退出码 1，证明新守卫独立于路径修正关闭了假绿通道。修复后完整模块 14 条从检出外 cwd、显式 `PYTHONPATH` 运行全绿，退出码 0（`m/red-2a/green-full.out`）。聚焦回归（`uv run --frozen python -m unittest`，净化环境、私有根与 `TMPDIR`/`BUDDY_CHECKS_TMPDIR` 同首轮）：`buddy.roles.test_worker_services`（14 条）、`buddy.harnesses.zcode.test_zcode_protocol`（41 条）、`buddy.harnesses.zcode.test_zcode_tool_refusals`（7 条）、`buddy.harnesses.zcode.test_zcode_inquiry`（46 条）全绿，输出存 `<task-root>/m/continuation-*.out`。编号无新增/删除/改名；未重跑 232 整批、原生 CLI、完整检查与旧四组隔离变异。

## 更正：隔离副本脚本含删除命令，前文"未手动删除"不实

前文"任务目录与收尾"一节称"本 Worker 未手动删除任何对象"，与事实不符；前文原句保留，以下为更正。`<task-root>/m/fault-injection-isolated.zsh` 第 40 行（`family()` 函数内）为 `rm -rf "$copy"; ditto "$ROOT/src" "$copy"`，在每组变异复制前执行；`$copy` 由 `TASK=/private/tmp/a252a-1yfl50kg` 与组名拼出，实际执行的删除指令即该 `rm -rf "$copy"`，确切目标恒为以下四个任务目录内路径之一：`<task-root>/m/fault-m1`、`<task-root>/m/fault-m2a`、`<task-root>/m/fault-m2b`、`<task-root>/m/fault-m3`（`<task-root>` 即上文登记的 `/private/tmp/a252a-1yfl50kg`），该命令无通配符，无法触达任务目录外对象。能确认的执行次数：现存日志记录了一次完整四组运行（每组开头各执行一次，共 4 次，日志含 8 个 expected-failure 与 8 个恢复 PASS、无 RECOVERY-FAILED/UNEXPECTED-PASS 并以完成行收尾）；脚本每次运行开头以 `: > "$LOG"` 截断日志，故更早是否还有整脚本重跑无法从日志确认，总执行次数不确定（未知），不把日志缺失当作未发生。影响：每次执行至多删除该组自己上一次的副本目录再由 `ditto` 重建；现存四个副本目录均在且为恢复后的基线字节（manifest 见故障注入一节）。本 Worker 自本轮起不再运行任何删除命令、不复用含删除的脚本；不追查其他会话、不尝试恢复可能已被删除的对象（其是否存在亦未知）。另明确：前文故障注入一节"worktree 生产源码零改动"一语仅指正式四组隔离变异；更早一次在本 worktree 内变异并按 SHA 校验恢复的尝试（遗留 `<task-root>/m/*.fault-backup` 与 `fault-injection.log`）是发生过的事实，前文"任务目录与收尾"已披露，维持有效、不予抹去。"未发现其他会话遗留材料"一语维持。
