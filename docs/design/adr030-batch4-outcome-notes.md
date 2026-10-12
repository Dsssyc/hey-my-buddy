# ADR-030 批次 4：收尾摘要超限

本说明记录提交 `3a4a9637` 上本微任务的源码改动与 Worker 自测，供 Host 验收；它不代表完整检查、真实回合或安装验收已经完成。

## 改动与边界

`buddy/roles/turn_io.py` 新增唯一的截断函数 `truncate_outcome_summaries`，只处理 `remaining` 与 `decisions`：保留前 32 条，保留下来的每条按 UTF-8 字节截到 4096，切口处不完整的字符整体去掉。`droppedItems` 统计舍弃的条数，`shortenedItems` 只统计保留部分中被缩短的条数，舍弃部分不重复计入缩短。两个限额提为常量，原有严格校验引用相同的值。

整体 64 KiB 上限仍针对原始 outcome 的规范 JSON 字节数；原始文档已经超限时不进行摘要修复，仍由原有校验拒绝。数组的错误类型、空白条目与 NUL 也不修复，即使它们位于将被舍弃或缩短的部分，也保留给严格校验拒绝。字段集合、处置、摘要、artifact 与求助请求的原有校验继续执行；不新增 Worker 字段或拒绝类别。

`buddy/roles/schema_worker.py` 在 `parse_value` 的原有校验调用之前调用该函数，返回修复后的 outcome、原有错误诊断与独立的截断统计。最终结果与诊断使用同一个处理入口，既覆盖已经解析的值，也覆盖原始 JSON。交互拒绝导致角色层另造 attention outcome 时清除原结果的截断统计，避免把被替换结果的统计挂到新结果上。

`buddy/roles/run_execution.py` 只修改 `_worker_result` 和 `worker_delivery` 中携带统计所需的行：内部交付返回值增加独立的第三项，只有非空时才把它写成回合记录顶层的 `outcomeTruncated`。Host 通过既有 `turn` 记录读取它；Worker 的六字段 outcome、原生事实、provenance 与原生 RunResult 均不增加字段。完成工具路径返回空统计，继续使用原来的严格校验。公共头部、快速和审阅投影及另外三个禁止修改的文件均未改动。

被取代的二项内部返回值和相应解包在同一个改动里直接替换为三项，没有兼容分支、别名或第二种读法；未删除文件或既有测试、文档段落。

## 两条路径的行为

Codex 与 Claude Code 的最终消息路径接受只有这两个摘要数组超出条数或单条字节限额的结果，交付截断后的数组并在回合记录中写下统计。没有发生截断时不增加键，输出回合记录与原来规范序列化的字节完全相同。新的测试实际写入回合文件，再通过 `read_turn` 及两家现有的 `validate_turn_provenance` 导入带统计的记录，确认原生来源校验不受影响。

DSH 与 ZCode 的完成工具路径没有接入截断函数。测试调用共用的 `buddy_finish_turn` 处理器：条数或单条长度超限仍返回 `invalid-arguments` 的签名拒绝，参数本身不被改动；同一工具配置下再提交改短的结果可以拿到签名完成凭据并通过 `session_receipts.verify_receipt`。角色层对两家工具结果的严格交付校验也保持不变，已有效的结果没有截断键，超限结果仍拒绝。

## 新增回归测试

测试模块为 `buddy.roles.test_outcome_truncation.OutcomeTruncationTests`，以下各项均已通过。

| 测试名 | 核对内容 |
| --- | --- |
| `test_final_message_item_count_is_truncated_and_recorded` | 两家、两个数组、原始及已解析 JSON 的前 32 条与丢弃数，原始值和 provenance 不被改动 |
| `test_final_message_long_items_keep_complete_utf8_characters` | ASCII、中文和四字节字符的切口、单条字节上限及缩短数 |
| `test_both_arrays_report_dropped_and_retained_shortened_items` | 同时超限、丢弃与缩短的独立计数、两家原生来源校验及回合导入 |
| `test_untruncated_record_is_byte_identical_and_has_no_metadata` | 恰好 32 条、恰好 4096 字节和规范回合文件逐字节不变 |
| `test_other_validation_failures_are_not_hidden_by_truncation` | 空摘要、多字段、错误处置、artifact、请求、数组类型及截断范围外的错误条目继续拒绝 |
| `test_original_overall_byte_bound_remains_strict` | 摘要或数组撑破原始文档的 64 KiB 后继续拒绝 |
| `test_valid_help_request_survives_summary_truncation` | assistance 和 attention 的合法请求原样保留 |
| `test_receipt_diagnostic_accepts_truncated_final_message` | 摘要超限不再生成结果校验失败诊断 |
| `test_controller_attention_replaces_outcome_without_stale_truncation_metadata` | 原生交互拒绝的 attention 替换行为与统计清除 |
| `test_completion_tools_refuse_oversize_and_accept_corrected_retry` | 完成工具的签名拒绝与原回合改短重交 |
| `test_completion_tool_role_delivery_is_unchanged_and_still_strict` | DSH 与 ZCode 角色层工具交付路径继续严格校验 |

## 实际自测

使用 Host 提供的只读 Python 解释器和 `PYTHONPATH=src:tests/python`，未安装依赖或向该解释器环境写入文件。每次启动均清除 AGENTS.md 列出的继承运行时、Worker、凭据和虚拟环境变量；现有角色层测试还指定了临时 state、runtime、HOME 和各 harness 配置目录。

- `buddy.roles.test_outcome_truncation`：最终源码 11 项通过。
- `buddy.roles.test_run_execution` 与 `buddy.roles.test_worker_services` 完整文件，加上 `buddy.roles.test_schema_worker.CodexSchemaTests`、`SchemaWorkerTests.test_native_schemas_stay_fixed_and_full_prompt_hashes_include_the_worker_role` 和 `SchemaWorkerTests.test_discovery_errors_need_explicit_boolean_native_stop_evidence`：共 40 项通过。
- `buddy.harnesses.codex.test_codex_protocol.StructuredOutcomeTests`：3 项通过，确认既有严格 JSON、整体字节上限和长摘要行为。
- 原因复现：仅在测试进程里临时把 schema_worker 的截断函数替换为原值透传，条数超限测试的 8 个子用例全部重现原有交付错误；源码未回退或修改。
- `git diff --check` 通过，源码及新增文件均位于冻结写入范围内。

## 留给 Host 的验证

以下 `buddy.roles.test_schema_worker.SchemaWorkerTests` 方法未在此沙盒运行，它们经过现有控制器和原生 fixture 启动路径，需要 Host 的套接字与启动环境；本次没有尝试绕过沙盒或启动本机真实 harness。

- `test_native_schema_delivery_keeps_the_shared_worker_record_and_original_read_scope`
- `test_native_success_with_an_invalid_role_value_returns_a_failed_controller_exit`
- `test_denied_permissions_turn_a_native_completed_value_into_role_attention`
- `test_unreadable_role_evidence_keeps_the_native_result_and_stop`
- `test_an_initial_turn_with_a_previous_session_never_reaches_the_native_run`

完整 Python 检查、四家真实 Worker 回合及故障注入由 Host 执行；本次没有运行前端检查、真实模型调用、共享内存或真实启动器测试，也没有安装或操作服务、Worker、日常黑板、账户和用户 harness 配置。
