# ADR-025 第五步微任务 5-D1：路由/审阅原生夹具迁移

微任务 run `02471387-ac73-4a9d-9c31-648952b9141a`、attempt `ddc7eaa6-e906-4afc-95c3-795baf9056e0`、路由决定 `dec-5545abeb-8181-42bf-89e3-6f5d87ba7274`，固定基线 `3350d11`，独立受管 worktree，唯一可写三路径为 `tests/python/protocol/fixtures/mock_readonly.py`、本记录与 [编号表](adr025-step5-router-fixture-ids.tsv)。本记录只登记微任务的固定交付与实际验证边界；Host 整合与整步验收另行登记。运行历史如实分别记录：首轮交付固定为部分输出 commit `bd1cbaaf`（基线 `3350d11`，累计补丁 SHA-256 `6acf077bce3fbca6ef6f61f232e540a6fc5f87eb7064625f22d05242b9bb26c4`），其原生结束为 failed/duplicate-finish（完成工具之后又调度了工具），保留为真实历史、不作为正常交付宣称；其后的 finish-only 继续回合以完成工具正常签收 completed。本回合按审查意见只修正本记录中与原始日志不符的验证表述，夹具与编号表未改、测试未重跑。

## 真实原因

Host 在第五步清理中删除 `structured_call.collect` 的旧结果 else 与 `_collect_result` 后，`mock_readonly.install` 仍替换 `run_execution.start_review` 为返回旧 `ProcessHandle` 的替身：句柄没有 `role_run_control` 与 `role_run_identity` 绑定，子进程只把旧 read-only envelope 写上 stdout，而新收集路径读取 `handle.role_run_control["operation"]` 选择 `read_review_result`/`read_fast_result` 并按 `RunResult` 帧解码。收集在读取绑定键时抛 `AttributeError`，运行时把 attempt 存为 `result: null`、shutdown 未知。基线夹具的红色重跑原始输出为 `<task-root>/m/evidence/red-test-decision.raw`：unittest 汇总为 `Ran 46 tests`、`FAILED (failures=20, errors=3)`，46 项为本模块 40 项加 `test_router.RouterContractTests` 6 项；failures 行含子例程（subTest）失败，不等于失败测试编号的数量——`test_answer_shape_json_and_path_boundaries_keep_one_attempt_each` 的 4 条与 `test_fixed_fields_and_capabilities_are_rechecked_at_adoption` 的 2 条子例程行都计入这 20 条 failures，本模块 40 个编号中实际有 18 个编号出现 FAIL/ERROR（其中 2 个编号为 error），另有 1 个 error 是 `RouterContractTests` 的旧契约错误、与夹具无关（见缺口 3）。其 SHA-256 为 `3e487084b166f2dc2dd2b3c4edfcbe1efea14a7366967dd7865499f5f30ce03d`。

## 迁移后的夹具

`install` 的注入面保持原样：只补能力准入（`DshAdapter.read_only_structured`、`local_read_only_check`、`available`）与冻结输入镜像（`router_input.prepare/verify`），`collect` 一层不替换。替身 `start` 按 `run_execution._launch` 的同一形状冻结材料：以真实 `RunIdentity`（task/attempt/generation 与新 `invocationId`）写 `role-run-control.json`，把 `requestFile`/`verdictFile` 与替身增加的 `resultFile` 一并冻结进 `handle.role_run_control`，并冻结 `handle.role_run_identity`；以 `controller_environment(..., read_only=True)` 构造子进程环境，不继承任何 `BUDDY_AGENT_*`/`BUDDY_WORKER_*` 凭据，仍以 `start_new_session` 的自有进程组spawn，外部日志仍落在 `runner.stdout.log`/`runner.stderr.log`。

子进程用真实角色构造与编解码生成协议材料：`run_execution.review_request(control, None)` 构造实际 `RunRequest`，经 `encode_run_request` 独占写入 `role-run-request.json`；应答按模式构造 `RunResult`/`RunEnd`/`RunValue`/`NativeIdentity`/`StopEvidence` 模型，经 `encode_run_result` 写入 `role-run-result.json`；按生产子进程同一形状写 `role-run-verdict.json`（`stopReason`/`elapsedMs`）。收集路径完全不替换：`structured_call.collect` 经 `collect_controller`、`read_review_result` 与两层 `router_stop_confirmed` 读取冻结绑定指名的帧文件，验证帧身份等于持有身份、请求与 verdict 材料齐全后才投影。子进程的真实 stdout 保留同一帧的角色投影（`run_execution._review_result`），作为留存的原生运行日志；因此 `log_paths["stdout"]` 指名帧文件而非进程 stdout 是本夹具唯一的一处读取面偏离，已经在此写明。

各旧模式的含义逐项保留，机制换为新事实：`select_first` 与 `profile_id`/`evidence none|foreign` 照旧选择候选并给卡证据；`abstain`/`abstain_with_evidence` 的 `profileId: null` 现在是 `RunValue.parsed` 的合法枚举值；`out_of_candidate` 现在如实标 `schema_status: invalid`（enum 越界不可纠正）；`answer_error` 是 `raw` 中的非 JSON 文本、`parsed` 为空；`answer_extra`/`answer_bad_evidence`/`answer_escape` 保留原答文并标 `invalid`，边界判断仍在黑板；`json_answer` 是 `raw` 携带 JSON 字符串、`parsed` 为空，由通用收集解码；`error`/`protocol_error`/`budget`/`deadline` 变为 `RunEnd` 的 `call-timeout`/`invalid-native-result`/`observer-interrupt`（配 verdict `stopReason: readonly-budget-exhausted`）/`deadline` 加退出码 1；`malformed` 往帧文件写 `{not json`；`no_output` 与 `sleep` 保持零输出与挂起；`input_changed` 照旧改动镜像内 `input.txt`；`no_shutdown` 报 `group_state: unknown`；`string_shutdown` 把已编码帧的 `stopEvidence.native.groupState` 改写字符串后交收集器，由严格编解码整帧拒绝。

隔离边界：本夹具从未运行 harness 或模型。工具事实是共享 `router_tool_receipt` 夹具包（假 receipt），原生根身份是常量 `mock-native`，checked configuration 保持为空，verdict 的耗时是固定常量。帧报告的是"一次完成的审阅会报告的模拟原生结论"（模型已启动、停止已落定），以便黑板的发布、弃权与健康规则按真实原生审阅同一条路径裁决；进程级事实全部为真：自有进程组、外部日志、外层停止观察、请求/verdict/帧文件。不是精确布尔 `True` 的停止事实——包括被严格编解码拒绝的帧——永不验证关闭。

## 红绿与编号

迁移前（基线夹具）的整次红色运行共 46 项（本模块 40 项加 `test_router.RouterContractTests` 6 项）、unittest 汇总 `FAILED (failures=20, errors=3)`，failures 行含子例程、不等于失败编号数，本模块 40 个编号中 18 个出现 FAIL/ERROR；迁移后的整次绿色运行同样共 46 项、unittest 汇总 `FAILED (failures=1, errors=2)`，不笼统称为绿色通过：本模块 40 项子集中 38 项通过、1 failure 加 1 error（缺口 1、2），另一 error 是 `RouterContractTests` 的范围外旧契约错误（缺口 3）。两次运行的真实 unittest 编号集合恰好 40 项且逐项相等：`<task-root>/m/evidence/red-ids-test-decision.txt` 与 `green-ids-test-decision.txt` 的 SHA-256 相同（`59d22ef47d1b510841abc0ea9a2330f515c7328ca598d165ec268b7ac7c5c935`），未变集合相等即由此证明；changed/deleted/added 均为空，[编号表](adr025-step5-router-fixture-ids.tsv) 因此只有表头。绿色原始输出 `<task-root>/m/evidence/green-test-decision.raw`（46 项 = 本模块 40 项加 `test_router.RouterContractTests` 6 项，1 failure + 2 errors），SHA-256 `54a639a139395e1613c0e2d890e6284eb18c21089ae98cc9893157d615c08d99`。迁移后夹具文件 SHA-256 `615a757f577a1f8eddea5aa5aa6ddbe5fba13a20c3673dc3cf9d94edf7d5f088`，被替换的基线夹具 SHA-256 `a70f43232666808fd1068f1ec1b59af4a59ec236ba12ef3b99a2130ea474c9b1`。

| 直接消费者 | 结果 | 原始输出（`<task-root>/m/evidence/`） | SHA-256 |
| --- | --- | --- | --- |
| blackboard.routing.test_decision + RouterContractTests | 整次 46 项汇总 failures=1、errors=2（FAILED）；本模块 40 项子集 38 过 + 1 failure + 1 error | green-test-decision.raw | 54a639a139395e1613c0e2d890e6284eb18c21089ae98cc9893157d615c08d99 |
| blackboard.routing.test_router + test_routing_modes + test_single_router_requests | 55 项，1 error（缺口 3，与夹具无关） | green-routing-consumers.raw | f4a9db7323c68001927c1470c72270fbfca52b46cc04c9ac2131ba4cd28a7d27 |
| blackboard.evaluation.test_evaluation | 36 项 OK（跳过 1） | green-evaluation.raw | d1ba90c66be5365036152efde0d6ac077167613b8177f51a0c493c88a48876c0 |
| blackboard.evaluation.test_external_evaluation + blackboard.tasks.test_model_concurrency | 41 项 OK | green-eval-ext-concurrency.raw | 82b1a46528a5eb9385b5023b5f3b3a0af97556ad7e919dfea32790af663e3414 |
| blackboard.tasks.test_parallel_dispatch | 27 项 OK | green-parallel-dispatch.raw | f67d7ad1a21eba326d00fa4f808a63d8327bf3d6fd928da899b0dc63c1b58f8b |

`test_parallel_dispatch` 与 `test_evaluation` 的首次运行在同一台机器上并发执行，前者有一个容量时序测试失败，该原始失败如实保留、未删改。失败原因未确定：当时的并发安排是两个聚焦运行在同一台机器上同时进行；此后单独重跑全模块 27 项全部通过（`<task-root>/m/evidence/green-parallel-dispatch.raw`，SHA-256 `f67d7ad1a21eba326d00fa4f808a63d8327bf3d6fd928da899b0dc63c1b58f8b`），含该容量时序测试在内的 `ModelFamilyFairnessTests` 3 项再单独重跑亦全部通过（`green-parallel-dispatch-fairness.raw`，SHA-256 `452fbbf63fe7ec67b543d53236a1486d43a67f33e38d5c969afa29b597fb8514`）。这些重跑事实不足以证明"并发资源竞争是运行安排问题，不是夹具行为"，也不排除其他原因。

## 停止防护的故障注入

内层两支由迁移后的模式承担并已在绿色运行中通过：`no_shutdown` 让帧如实报 `group_state: unknown`，`string_shutdown` 交付一个被严格编解码整帧拒绝的字符串停止事实；`test_unconfirmed_shutdown_never_verifies_input_or_releases_capacity` 对两个子例程都断言决策失败、task `shutdownConfirmed` 为假、attempt 停在 uncertain、不改输入、不验证镜像——严格报文错误没有被报成有效帧，未知继续占用。

外层注入在 `<task-root>/m/stop_guard_wrapper.py` 与 `<task-root>/m/stop_guard_probe.py` 完成：替身子进程被包一层包装器，真实子进程写完报 `gone` 的合法帧后，包装器在同一自有进程组留下一个仍在睡眠的后代并退出。收集器读到的帧内层事实为 `True`，而外层组观察非 gone，两层规则必须拒绝：探针输出（`<task-root>/m/evidence/stop-guard-fault.raw`，SHA-256 `348ceeb2b4e044bf78303786f3d7adaf6b9451d0287ab4dd3359a67b5e41ace7`）显示决策失败、码 `router-stop-unconfirmed`、task `shutdownConfirmed: false`、attempt `uncertain`、输入未验证、读者槽位归零。注入的后代进程由探针收尾，无残留。

## 提给 Host 的接口缺口

以下三项都在本微任务可写范围之外，Worker 未改动生产代码或任何测试，只陈述事实与证据。

一，`SelectionRequestTests.test_recommendation_never_creates_or_authorizes_a_business_task` 断言 `decision["resolvedProfile"]["reasoningEffort"]`。旧 envelope 的 `resolved` 是 `{**profile, reasoningEffort}`；新回执的 `resolved` 是生产投影对 checked configuration 的读数（`{provider, model, effort}`），而替身没有任何被检查的读数，`resolved` 为 `None`，断言以 `TypeError` 失败。真实 DSH 审阅的 checked 读数同样是 `{provider, model, effort}`，不可能带 `reasoningEffort` 键，因此这不是夹具能补的形状，需要 Host 决定改断言还是改投影。

二，`DecisionFailureTests.test_json_string_answer_is_parsed_by_the_generic_collector` 断言 `decision["usage"]` 恰为 `{elapsedMs: 200, toolCalls: 1, bytesRead: 33}`。新审阅投影的 usage 是 `{toolCalls: <工具事件数>, bytesRead: None, elapsedMs: <verdict>}`，`bytesRead: None` 是生产常量；其余两项经 verdict 常量与工具事件数为 200/1。该测试的其余断言（完成状态、答案键集、`nativeIdentity`）已通过。

三，`RouterContractTests.test_cancelled_native_receipt_without_stop_proof_is_not_cancelled` 在基线上就已失败、与夹具无关：它用没有 `role_run_control` 的旧契约 `SimpleNamespace` 句柄直接调 `structured_call.collect`，正是本步删除的旧结果读取路径。它守护的"无停止证据的取消回执不得当作取消"如今由真实 `RunResult` 帧加 `end.status: cancelled` 承载，需要 Host 按新契约改写或退役。

## 材料与清理

一次性材料全部在 `<task-root>/m/`：`evidence/` 内为红绿原始输出、编号清单、迁移前后夹具副本及本记录引用的全部 SHA；`run-focused.sh`/`run-probe.sh` 是复刻检查套件私有环境的聚焦运行器，`probe_decision.py`/`stop_guard_wrapper.py`/`stop_guard_probe.py` 是一次性探针；各次运行的私有 state/runtime 根在 `<task-root>/m/<run-name>/`，`TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 指向 `<task-root>/t`。Worker 未删除任何对象；夹具与检查运行器自身的正常收尾照常。受管检出内只有夹具一个文件被修改，没有提交、没有切换或新建分支；日常黑板、其他会话对象、安装运行时与凭据均未触碰，也未读取任何凭据文件内容。
