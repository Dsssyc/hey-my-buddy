# ADR-025 第五步微任务 5-D3：Router 只读探针旧测试夹具迁移

微任务 run `c1ebefff-445a-409d-bf92-e0d0b8f04245`、attempt `0908f747-6962-4abc-ab11-ce52bb6786f4`、路由决定 `dec-44904f35-0420-4e78-a3a4-62bb941a2c9a`，固定基线 `2278c40`，独立受管 worktree，唯一可写三路径为 `tests/python/blackboard/routing/test_router_probe.py`、本记录与 [编号表](adr025-step5-router-probe-fixture-ids.tsv)。本记录只登记微任务的固定交付与实际验证边界；Host 整合与整步验收另行登记。

## 真实原因

Host 在第五步退休 `structured_call.collect` 的旧 fallback 后，`test_router_probe` 的 `mock_execute` 仍返回没有 `role_run_control` 与 `role_run_identity` 绑定的 `Mock(spec=ProcessHandle)`，并把旧角色投影 envelope 写上 runner 日志；真实收集路径读取 `handle.role_run_control["operation"]` 选择 `read_review_result`，在该绑定键上抛 `AttributeError`，探针把异常折进 `error: "AttributeError"`，报告因此没有任何 `checks`。17 项中 8 项失败（6 errors + 2 failures），全部出自同一夹具；另有 4 项 mock_execute 消费者因断言只看失败状态而仍在基线绿，但同样消费旧夹具，一并迁移。替换前基线原始输出为 `<task-root>/m/5d3-baseline-router-probe-failure.log`（17 项，2 failures + 6 errors），SHA-256 `376a18fde842d748de7d756e825ea45278eb31ace1fd52db37105c43bbbaec80`。

## 迁移后的夹具

注入面保持原样：`mock_execute` 仍只替换探针的 `start_review`、`adapter_for` 与 `start_network_control`，`probe.read_only.collect` 一层在所有成功路径上从不替换。替身 start 按 `run_execution.start_review`/`_launch` 的同一形状冻结材料：以真实 `RunIdentity`（task/attempt/generation 与新 `invocationId`）建 invocation 私有根与 `review-native` 根，写 `role-run-control.json`，把 `requestFile`/`verdictFile` 与替身增加的 `resultFile` 一并冻结进 `handle.role_run_control`，并冻结 `handle.role_run_identity`；`canCorrect` 与生产一致取自注册表适配器的 `read_only_structured_resume`，不经过探针可被替换的 `adapter_for`。

子进程替身在本进程内以真实角色构造与编解码生成协议材料：`run_execution.review_request(control, None)` 构造实际 `RunRequest`，经 `encode_run_request` 独占写入 `role-run-request.json`；应答按测试选项构造 `RunResult`/`RunEnd`/`RunValue`/`NativeIdentity`/`StopEvidence`/`EvidenceRef` 模型，经 `encode_run_result` 写入 `role-run-result.json`；按生产子进程同一形状写 `role-run-verdict.json`（`stopReason`/`elapsedMs`）。收集路径完全不替换：`structured_call.collect` 经 `collect_controller`、`read_review_result` 与两层 `router_stop_confirmed` 读取冻结绑定指名的帧文件，验证帧身份等于持有身份、请求与 verdict 材料齐全后才经 `_review_result` 投影。生产子进程 stdout 写 RunResult 帧；本夹具有意把帧放在 resultFile 供真实收集器读取，把同一帧的角色投影另放 runner 日志作脱敏样本；因此 `log_paths["stdout"]` 指名帧文件而非进程 stdout 是本夹具唯一的一处读取面偏离，与 5-D1 相同，在此写明。

句柄是真实 `ProcessHandle`，不再是无绑定的 Mock：三种替身进程状态分别承担原有场景——正常退出（组观测为 gone）、`timeout`（leader 永不被 reap，`wait` 真实抛 `TimeoutExpired`，持有组经持有 job 持续报告存活）、`stopped=False`（leader 已退出但收集时持有组仍活，`shutdown_confirmed` 走真实 2 秒落定观察返回假；模拟 terminate 后 job inactive，而 timeout 分支的 leader 仍未 reap，仍不能确认停止）。`wait`/`terminate` 用真实句柄之上的记录 spy 保持旧断言（`wait(70)`、`terminate(grace_seconds=3)`、调用次数）；`setUp` 的子进程/网络守卫原样保留，替身不产生任何真实进程或网络连接。

各旧 payload 形参逐项映射到新事实，不再有自由字典覆盖：`rawAnswer` 覆写改为 `answer`（非 JSON 文本是 `RunValue(schema_status="invalid", raw=…, parsed=None)`，错 marker 是 parsed dict）；`usage.toolCalls` 改为真实 tool-evidence 包的计数（共享 `router_tool_receipt` 夹具），`usage.elapsedMs` 改为严格 verdict 的 `elapsedMs`；`correctionCount` 是 `RunValue.correction_count`；`resolved` 覆写改为 checked configuration 读数（`CheckedValue`），requested 与 checked 严格分离，不虚构 observed；native 收据（`nativePolicy`）经私有 evidenceRef（size+sha 绑定）由 codex `native_evidence` 真实读取后投影，缺收据时保持 unverified。`shutdown=False` 如实报 `group_state: unknown`。

## 隔离边界与虚构值

本夹具从未运行 harness、模型、真实子进程或网络。工具事实是共享 `router_tool_receipt` 夹具包（假 receipt），原生身份是常量 `mock-session`/`mock-turn`，verdict 耗时是固定常量 100ms，checked configuration 只是镜像 mock CLI 参数（`openai`/`mock-native-model`/`medium`）。脱敏测试里的全部敏感字符串——`person@example.com`、`unseen-secret`、`unknown-credential`、`unknown-key`、`unknown-account`、`super-private-credential-value` 及 `TEST_API_KEY` 的值——都是虚构的夹具值，不是任何真实账号、凭据或环境秘密；`TEST_API_KEY` 环境补丁原样保留，恰好证明探针的结构化脱敏从不读取环境值。帧报告的是"一次完成的只读审阅会报告的模拟原生结论"（模型已启动、停止已落定、marker 准确回传），以便探针的判定与脱敏规则按真实原生运行同一条路径裁决；不是精确布尔 `True` 的停止事实永不验证关闭。

## 红绿与编号

迁移前 17 项中 8 项失败（6 errors + 2 failures）；迁移后同一模块 17 项全部通过（`Ran 17 tests`，OK，28.7s，其中三个外层未停场景按真实句柄语义各消耗约 5–10 秒真实落定时间）。迁移前后全17个实际加载编号集合相等，编号变化、删除、新增均为零；12处旧夹具消费者机制有迁移，不能将正文变化算作编号变化。[编号表](adr025-step5-router-probe-fixture-ids.tsv)因此仅保留表头；实际加载集合与相等比较见 Host 原始编号核对（<checkout>/tmp/adr025-host/<phase>/5d3-host-proposal-ids1.json）。此前5个正文未动测试的原始集合材料仍保留，不替代全17个编号相等证明。绿色原始输出 `<task-root>/m/5d3-green-router-probe-fourth.log`（17 项 OK），SHA-256 `e0940a3ac1ebb69cb43661d5dda3affb1ad12c04e8320d4f8bc68c6d9bbd9a38`。迁移后夹具文件 SHA-256 `509685b129b4febd026aabff67d811315cc681d895ea47fecbe9ecb88314eabc`，被替换的基线夹具 SHA-256 `007985495a45e616f7ed9e7f8f99a39ee0f5fd7e8681501675a9ee9ae4c19333`。

## 停止与脱敏防护的故障注入

两个单点变异分别注入在 `<task-root>/m/mutant-stop/` 与 `<task-root>/m/mutant-redact/` 两个全新的 source 副本内（受管检出本身不动），每次只改一处，原绿变异真实红，且都是断言失败而非 import 错误。停止防护：`run_execution._base_result` 的 `group_state == "gone"` 单点变异为 `!= "alive"`，使帧内 `unknown` 被当作已确认关闭；`test_missing_native_shutdown_fails_even_if_owned_group_stopped` 在未变异绿色运行中通过，变异后转红（`AssertionError: 'unverified' != 'failed'`，`<task-root>/m/evidence/mutant-stop.log`，SHA-256 `95bec054544dfb37bfe7ad2a7267ae70e4501415382730c3cc4110eb0afe6275`）。脱敏防护：删除探针 `redact` 的 Bearer 规则单行后，`test_redacts_report_and_retained_native_logs` 转红（`assertNotIn` 捕获 `super-private-credential-value` 泄漏进 report.json；通用的 authorization 键规则只遮住了 "Bearer" 一词，证明被删规则本身承载防护），`<task-root>/m/evidence/mutant-redact.log`，SHA-256 `c7ec219a097525e791759f699e4fa53b03ece6ae5b38b385a57fecbb0b5cd09b`。

## 运行边界

按微任务约束只运行了 `blackboard.routing.test_router_probe` 这一个受影响模块，未运行完整检查套件。没有启动用户安装版 harness、没有真实模型调用；未安装或升级任何运行时，未修改用户配置、登录或凭据，未读取任何凭据文件内容。测试私有 state/runtime 根与临时目录都落在 `<task-root>/t`，全部一次材料在 `<task-root>/m`。

## 提给 Host 的接口缺口

无。迁移全程复用既有模型/codec/角色投影与注册表入口（`review_request`、`encode_run_request`/`encode_run_result`、严格 verdict、`read_review_result`、`router_stop_confirmed`、codex `native_evidence`），没有遇到需要生产代码改动才能继续的缺口；review 回执上 codex 的 `outcomeValidationError` 诊断字段被 `_review_result` 的既有键白名单自然略去，是生产行为，不是夹具缺口。

## 材料与清理

一次性材料全部在 `<task-root>/m/`：`5d3-baseline-router-probe-failure.log` 与 `5d3-green-router-probe-fourth.log` 是红绿原始输出，`5d3-green-router-probe-first.log` 至 `-third.log` 是迁移过程中的中间运行记录；`evidence/` 内为编号清单、两个变异副本的红色输出、迁移前后夹具 SHA 及本记录引用的全部哈希；`debug_freeze.py`/`debug_receipt.py`/`debug_frame.py`/`debug_ref.py` 是一次性探针脚本；`mutant-stop/` 与 `mutant-redact/` 是注入故障的 source 副本。各次运行的私有 state/runtime 根在 `<task-root>/t`，`TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 指向 `<task-root>/t`。Worker 未删除任何对象；夹具自身的 TemporaryDirectory 收尾与 unittest 的正常 teardown 照常，没有创建任何需要清理的真实进程端点。受管检出内只有夹具一个文件加本记录两文件被修改，没有提交、没有切换或新建分支、未使用 stash；日常黑板、其他会话对象与凭据均未触碰。
