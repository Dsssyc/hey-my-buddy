# ADR-025 第四步 4-E1：黑板机械规则测试的合成原生事实迁移

本记录是 ADR-025 第四步收尾微任务 4-E1 的交付材料（基线 `60682b0`，独立受管 worktree）。完整检查（Host 日志 `<Host实施检出>/tmp/adr025-host/step34-20261006-105245/step4-full-check1.log`，只读）的主要根因是：黑板机械规则测试自行合成的 Worker 回执仍携带旧 DSH Node provenance `{tool,turnEnd,flush,rootSessionMatched}`，已被当前 ACP validator 正确拒绝，因此 workspace_lifecycle/evaluation/external_evaluation 与连带消费者 decision/host_workflow/workspace_api/cli_views 丢失 `finalArtifactId`；`test_workflow_real` 的 RealAdapterTests 另外直接调用已删除的 `DshAdapter.prepare/_import_turn/_seal_workspace/collect/workspace_cwd`。本轮把七个写范围模块的合成回合记录迁到当前格式，并把 RealAdapterTests 迁到已存在的共享角色与 turn_io 接缝；不启动安装版 harness、不调用模型（原生链路只用本项目假程序）、不安装/升级日常运行时、不改用户配置与凭据、不改公共源码。

## 迁移方式

- 新增共享夹具 `tests/python/protocol/fixtures/native_turn.py`（与既有 `protocol/fixtures/mock_readonly.py` 同类的跨模块测试夹具）：提供当前格式的合成原生事实——`dsh_provenance`（version/adapter/tool/turnEnd/stopReason/rootSessionMatched/receiptVerified/sessionClose + nativeSessionId/toolCallId/receiptId 与有序 ordinals）、`zcode_provenance`（九个期望字段 + inputId 按身份机械推导 + 两组有序序号，无 `flush` 键）、按 turnId 派生的去重会话 id（native-session 续接保留精确前会话）、`governed_record`/`claim_record` 组装十二字段回合记录。其模块 docstring 与各使用点明确声明：这些记录通过与真实回合相同的导入校验，但没有任何 harness 运行过，不是真实原生验真。
- 提交路径不变：所有回合仍经原有 worker API（`worker_result`/`client.submit_result`）提交，不直接改 DB 伪造交付，不跳过或放宽生产 provenance 校验，不恢复任何旧 adapter 入口或旧 Node flush 格式。这些测试原本就无模型进程，其原有边界保留（test_workflow 的 executor 替身与 mock workspace 边界原样，仅记录格式更新为真实当前格式）。
- 负例仍然改变真实的当前字段：test_zcode_turn_io 的三个适配器特有负例从"删 flush/换 tool 名"改为 `receiptVerified=False`、`nativeSessionId` 指向外来会话、`settledOrdinal` 破坏顺序，分别命中当前 validator 的三个不同错误分支（finish-tool evidence / root session / out of order）；test_scope_recovery 既有的 `rootSessionMatched=False` 存库负例是真实当前字段，原样保留。
- RealAdapterTests 迁到共享接缝：`prepare` 走 `worker_executor("dsh").prepare`（经 `BUDDY_HARNESS_RECORD_FILE` 指向本项目假 ACP 程序的选择记录满足能力检查，prepare 全程不启动任何进程，工作区核验/精确输入字节/凭据 0600 断言不变）；导入与封存走 `roles.turn_io.read_turn`/`seal_workspace`（即共享 collect 内部调用的同一函数，registered validator 作实参）；收集走共享 `executor.collect`——未确认停止一段用假程序 `--wait-for-inquiry 20` 在协作检查点持住回合后于存活时收集，确认一段为普通治理回合跑完再收集（回执由真实角色规则经假程序铸造，真实 Git 工作区真实封存）。执行身份的 spec 用假程序自己声明的选择项（provider `fake`/model `m1`，与 buddy.harnesses.dsh.test_dsh_role_wiring 同款），board 提交只提供真实 runId。

## 断言处置

- 逐模块断言意图不变：lifecycle/scope_recovery/workflow/evaluation/external_evaluation 的守卫断言（交付、验收、回收、恢复、评价计数等）全部原样；test_workflow_real 的续接/助手/工作树复用族仅换记录构造器。会话 id 语义保持（lifecycle 与 scope_recovery 原 `sess-{turnId[:8]}` 即夹具默认；evaluation/external_evaluation 显式 `fixture-{request_id}` 保留；多回合显式会话 id 本就互异，满足 reconstructed-new-session 的去重语义）。
- test_zcode_turn_io 一项改名：`test_adapter_specific_flush_does_not_bypass_common_identity_or_stop` → `test_adapter_specific_provenance_does_not_bypass_common_identity_or_stop`（`flush` 字段已随 Node 载体退役，名称随之；公共身份/停止门与三个身份变异负例原样，另加一条当前 zcode 格式记录在 zcode validator 下通过的正例，钉住夹具 zcode 分支的真实性）。默认路径断言从含 `flush`/`terminal tool` 的旧错误文案改为当前错误文案子串。
- 编号账目：七个模块编号集合相等（AST 比对 HEAD，test_workflow_real 9、test_workflow 54、test_scope_recovery 18、test_workspace_lifecycle 40、test_evaluation 36、test_external_evaluation 22、test_zcode_turn_io 4；unittest 运行计数 11/54/18/40/36/22/4 与基线日志一致），唯一改动为上述一项改名，逐项登记见[编号表](adr025-step4-board-fixtures-test-ids.tsv)；新增夹具文件一并列于表内。

## 验证证据

- 任务根以下记作 `<task-root>`（确切路径只在交付摘要与任务 tmp 台账）；全部命令 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 指 `<task-root>/t/`，材料在 `m/4e1-baseline/`、`m/4e1-faults/`，均为全新子目录；未触碰旧实验或他人会话对象；普通测试夹具与检查运行器自有收尾照常。运行方式与检查运行器一致（`python -m unittest -v <module>`，`PYTHONPATH` 为检出 `src`+`tests/python`，清除继承的 runtime/worker 环境变量）。
- 基线（改动前，本检出复现）：`buddy.harnesses.zcode.test_zcode_turn_io` → FAILED failures=1（`'the dsh turn lacks its completed finish-tool evidence' is not None`）；`blackboard.evaluation.test_evaluation` → FAILED failures=2 errors=3 skipped=1（`artifactId is required` 等）。其余五个模块的失败形态以 Host 完整检查日志定位（39 项 ERROR、9 项 ERROR、6 项 FAIL 等，均为 finalArtifactId/activeRequest 丢失或 `DshAdapter` 无 `prepare` 属性）。
- 迁移后（逐模块一次）：test_workflow_real 11 项 OK（约 57 秒）、test_workflow 54 项 OK（约 6 秒）、test_scope_recovery 18 项 OK（约 54 秒）、test_workspace_lifecycle 40 项 OK（约 207 秒）、test_evaluation 36 项 OK（skipped=1，约 18 秒）、test_external_evaluation 22 项 OK（约 4 秒）、test_zcode_turn_io 4 项 OK（约 0.01 秒）。
- 连带消费者复核（模块全量，非完整检查）：`blackboard.routing.test_decision` 40 项 OK（约 36 秒）、`blackboard.tasks.test_host_workflow` 12 项 OK（约 10 秒）、`blackboard.tasks.test_workspace_api` 4 项 OK（约 15 秒）、`cli.test_cli_views` 11 项 OK（约 12 秒）；另跑四个行为不变的导入方 `test_workflow_routing/boundaries/cancellation/stop_surface` 共 59 项 OK（约 12 秒）。
- 故障注入（恰好两项，均命中目标断言，变异 FAILED→还原后目标用例 OK）：F1 把夹具 `dsh_provenance` 的 `receiptVerified` 改为 False → `test_workspace_lifecycle.LifecycleTestCase.test_acceptance_requires_a_verified_or_not_required_decision` 在 `run_worktree` 的输出工件查找处 `StopIteration` ERROR（合成回执被真实 validator 拒绝即不交付——守卫正是"合成事实必须通过真实校验才有交付"）；F2 把 `settledOrdinal` 由 2 改为 1（破坏 result<=settled 顺序）→ `test_evaluation.EvaluationEvidenceTests.test_card_counters_are_derived_and_a_profile_with_evidence_cannot_vanish` 在 `workflow_accept` 处 `BoardError: artifactId is required`（`finalArtifactId` 为 None，命中评价样本依赖的交付断言路径）。两项注入后夹具逐字段还原，目标用例复跑 OK。
- 偏差：无（未改写范围外文件，未建/切换分支，未再委派）。Host 完整检查日志中另有五处失败不在本微任务授权范围、本轮未触碰，特此报告：`blackboard.store.test_blackboard` 的 TestPackaging 冷启动安装错误、`blackboard.tasks.test_inquiry` 的 journal/bridge 形状失败、`buddy.harnesses.test_live_channel` 的 dsh `inquiry_delivery` 期望 `realtime` 实为 `cooperative-checkpoint`、`buddy.roles.test_role_live_seam` 的绑定状态元组差异、`install.test_packaging` 仍期望已退役的 Node 测试目录 `harnesses/dsh/tests/*.test.mjs`。

## 未验证边界

- 完整检查套件、控制台前端解析、打包与安装面未运行，归 Host 统一；本轮各模块与连带消费者的通过不等于全套件结论。
- 合成原生事实不是真实原生验真：夹具 ordinals/receiptId 是常量而非观察证据；真实铸造回执仅存在于 RealAdapterTests 的共享收集路径与既有 fake-agent 套件。真实安装版 DSH 上的回合未运行（全程零安装版 harness、零模型调用）。
