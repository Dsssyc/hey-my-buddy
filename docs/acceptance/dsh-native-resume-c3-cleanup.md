# DSH原生续接第一部分微任务C3：退休旁路与重复定义清理

2026-10-08，微任务C3，编号V-C11至V-C14。固定基线7391081f0a7b59c1583693f65cbb09d71687f97b，独立受管worktree执行，改动留工作区由黑板封存，未提交、未动分支。一次性材料在本任务短根`<task-root>`：`<task-root>/t`为每次运行的私有TMPDIR/BUDDY_CHECKS_TMPDIR与私有state/runtime根，`<task-root>/m`为命令日志、基线与变更源码副本、六个单点变异副本及编号清单；确切路径已随交付报告交给Host，入库记录一律用占位符。

## 生产源码改动

| 文件 | 编号 | 改动 |
| --- | --- | --- |
| `src/hey_my_buddy/protocol/usage.py` | V-C11 | 删除`read_sidecar`及其独有常量`MAX_SIDECAR_BYTES`、`SIDECAR_VERSION`、`SIDECAR_FIELDS`（含`__all__`导出）；仅由其使用的`os`/`stat`/`pathlib`导入一并移除；仍被生产调用的`normalize_*`、`classify_quota_code`、`identifier`与全部数值约束保留。 |
| `src/hey_my_buddy/buddy/roles/turn_io.py` | V-C12 | `inquiry_paths`不再写attempt根`inquiry.json`；仍按原顺序创建私有根与execution目录，仍返回`directory`/`resultsPath`，`run_execution`的控制记录流程原样。 |
| `src/hey_my_buddy/buddy/roles/worker_services.py` | V-C13 | `governed_prompt`问询段与`inquiry_refusal`两处提示去掉withdrawn说法，改写为真实规则：仅仍在queued/delivered可答状态的问询阻塞completed，journal已记answered、discarded或不再可答的问询不阻塞；问询状态机与完成条件代码未动。 |
| `src/hey_my_buddy/buddy/harnesses/c_two_live.py` | V-C14 | 无生产改动：基线上该模块已经引用`harnesses/live.py`的`MIN_/MAX_TRANSPORT_TIMEOUT_MS`（100至5000毫秒）且无本地重定义，本次以新增守卫测试钉住该引用与全部边界行为。 |

删除前的使用方核对（全仓grep，固定源码审查副本同查）：`usage.read_sidecar`与`SIDECAR_*`在`src/`与`tests/`只剩`tests/python/protocol/test_usage.py`引用，设计/验收文档中的历史记载不算调用方；其余`protocol.usage`导入方（native_observations、evaluation、quota_routing、turn_io、service）只使用保留的符号。`turn_io.inquiry_paths`唯一生产调用方是`run_execution.prepare`，只消费`resultsPath`写入控制记录；attempt根`inquiry.json`的生产读取方只剩`private_dirs`回收与`private_migration`迁移（按任务要求保留），`blackboard/tasks/inquiry.py`读取的是`inquiry.results.jsonl`journal而非该文件。

## 测试改动

`tests/python/protocol/test_usage.py`：整类删除`SidecarBoundaryTests`及其仅测试用的`os`/`tempfile`导入；不重建任何载体。

`tests/python/buddy/harnesses/zcode/test_zcode.py`：`inquiry_results_path`辅助方法与`test_success_uses_native_root_receipt_and_keeps_secrets_private`不再读取`inquiry.json`，改从真实返回的`role-run-control.json`控制记录取`inquiry.resultsPath`并断言其等于`<execution目录>/inquiry.results.jsonl`；同一断言新增守卫`inquiry.json`不再被写（`assertFalse`存在）。

`tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py`：`test_the_bridge_config_and_prompt_wire_the_cooperative_channel`的journal绑定改为对控制记录的`inquiry.resultsPath`核对；新增`TransportWindowBoundaryTests`三个守卫（V-C14）：live.py常量恰为100/5000且wire frame准入边界（`TransportWindowMs`的ge/le）由live.py常量构成；`LiveWireRequest`恰接受100与5000、拒绝99、5001、`True`与`1500.0`；`CTwoLiveChannel`入口（`request`/`observe`的整窗校验）保持同样边界与严格bool拒绝（合法窗到达关闭通道的诚实回执，非法窗抛`BoardError`，全程无网络）。未造兼容层或dummy `inquiry.json`。

`tests/python/buddy/roles/test_worker_services.py`：`test_a_withdrawn_question_no_longer_blocks_completion`按实际规则改名并扩展为`test_questions_no_longer_answerable_stop_blocking_completion`（answered与discarded两个subTest均不再阻塞completed）；未把服务私有方法冒充产品撤回功能，仅按journal真实状态措辞。

## 编号记录（以实际加载的TestCase.id为准）

删除（5）：`protocol.test_usage.SidecarBoundaryTests.test_a_bound_document_is_read_verbatim`、`...test_a_foreign_or_stale_binding_is_refused`、`...test_malformed_oversized_and_missing_documents_are_refused`、`...test_a_symlinked_document_is_refused`（载体与调用方`usage.read_sidecar`/`SIDECAR_*`一并消失，无生产调用方故不重建载体）；`buddy.roles.test_worker_services.FinishBoundaryTests.test_a_withdrawn_question_no_longer_blocks_completion`（改名，见下）。

新增（4）：`buddy.harnesses.zcode.test_zcode_inquiry.TransportWindowBoundaryTests.test_the_window_constants_live_once_in_the_live_seam`、`...test_the_wire_frame_admits_exactly_the_window_edges`、`...test_the_channel_entry_keeps_the_same_edges_and_refuses_bools`；`buddy.roles.test_worker_services.FinishBoundaryTests.test_questions_no_longer_answerable_stop_blocking_completion`。

同编号正文适配（不当编号变化，2项）：`buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_success_uses_native_root_receipt_and_keeps_secrets_private`（改用控制记录并新增不写`inquiry.json`守卫，原0600权限断言随文件一起消失）；`buddy.harnesses.zcode.test_zcode_inquiry.ZcodeInquiryIntegrationTests.test_the_bridge_config_and_prompt_wire_the_cooperative_channel`（journal绑定改对控制记录核对）。非测试辅助`inquiry_results_path`同步迁移，不产生编号。

未变集合相等：四个scope模块基线合计117项（`protocol.test_usage`34、`buddy.roles.test_worker_services`14、`buddy.harnesses.zcode.test_zcode`与`...test_zcode_inquiry`合计69，另只读参考`buddy.harnesses.test_c_two_live`54项），变更后逐字diff仅上述删除5项/新增4项，其余166项（含c_two_live参考54项）两边逐项相同；清单存`<task-root>/m/baseline-ids-*.txt`与`after-ids-*.txt`。

## 验证

所有子进程以清空继承环境（`env -i`起点，清除`BUDDY_*`、`ANTHROPIC_*`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`、`PYTHONPATH`）后设置本任务私有state/runtime根、`BUDDY_DEV_SOURCE=1`、`TMPDIR`/`BUDDY_CHECKS_TMPDIR`（`<task-root>/t`下每次新名）运行；解释器为`uv run --project <Host检出> --frozen --offline --no-sync python`，`PYTHONPATH`显式指向本受管检出的`src`与`tests/python`，未测试Host源码；harness fixture选择与原测试隔离约定未动；未跑完整检查、未启动用户安装的harness、未调用真实模型、未删除任何文件或目录。

| 轮次 | 范围 | 结果 |
| --- | --- | --- |
| 基线（改动前，同一基线提交） | `protocol.test_usage`+`buddy.roles.test_worker_services`＝48 OK；`buddy.harnesses.zcode.test_zcode`+`...test_zcode_inquiry`＝69 OK | 全绿 |
| 改动后 | 同上四模块合计116 OK（44+72） | 全绿 |
| 旧文件回收（只读核对，代码未改） | `test_private_directories`＝14 OK（含`inquiry.json` fallback回收两用例）；`install.test_upgrade`＝21 OK（private_migration旧尝试`inquiry.json`处理）；`buddy.harnesses.test_private_adapter_invariants`＝14 OK | 全绿 |
| 邻域（只读，生产未改的`c_two_live`/`live`） | `buddy.harnesses.test_c_two_live`：53/54 OK；唯一错误是`test_the_socket_identity_is_captured_from_the_registered_address`因本任务私有TMPDIR路径过长触发macOS `AF_UNIX path too long`（socket路径约128字节＞104上限），换短TMPDIR重跑该用例即OK，与本次改动无关（`c_two_live.py`零改动） | 环境事实，已定位 |

## 单点变异（均在`<task-root>/m`的源码副本上，测试仍用本检出）

| 变异 | 单点 | 结果 |
| --- | --- | --- |
| M1 | `turn_io.inquiry_paths`恢复写`inquiry.json` | `test_success_uses_native_root_receipt_and_keeps_secrets_private`失败（"the retired per-turn inquiry.json must not be written"），守卫守住"不再写遗留文件"。 |
| M2 | `harnesses/live.py` `MIN_TRANSPORT_TIMEOUT_MS`100→99 | `TransportWindowBoundaryTests`三项全失败，窗口下界被钉在live.py常量。 |
| M3 | `harnesses/live.py` `MAX_TRANSPORT_TIMEOUT_MS`5000→5001 | 同上三项全失败，上界同样被钉住。 |
| M4 | `c_two_live._timeout_ms`去掉显式bool拒绝子句 | 无测试失败，且此变异在100毫秒下界下本无可观察差异：bool只有0/1，恒在窗外，范围校验必然先行拒绝；守卫仍断言`True`在frame与channel入口都抛`BoardError`（由范围达成），严格bool子句按任务要求原样保留在生产代码中。此为边界事实而非守卫缺口。 |
| M5 | `private_dirs.cleanup_inquiry_fallback`判定永假 | `test_private_directories`恰2项失败（fallback回收用例与迁移回滚用例），证明既有测试真实守住旧尝试`inquiry.json`/fallback回收，本次只停写不删回收是可验证的。 |
| M6 | `worker_services.pending_inquiries`把answered也当作pending | 改名后的完成条件测试失败，证明其真实守住"不再可答即不阻塞completed"的实际规则。 |

## 剩余事项

`protocol/inquiry.py`仍平行定义`MIN_/MAX_TRANSPORT_TIMEOUT_MS`=100/5000（黑板侧`blackboard/tasks/inquiry.py`消费），与`harnesses/live.py`的定义并存；该文件不在C3可写范围，两侧常量的合并归Host公共文件整合，合并时`TransportWindowBoundaryTests`的常量等值断言会要求同步更新。
