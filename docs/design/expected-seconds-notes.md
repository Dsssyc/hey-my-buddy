# 预期时长改动说明

本改动基于 `50148378`，完成第 3 步的 07 在授权范围内的源码、测试和控制台资产；包版本与契约版本仍为 0.35.0，schema 从 19 前进到 20，升级基点样板 `tests/fixtures/upgrade-base/` 没有改动。本记录是受管检出里的验证证据，尚未经 Host 验收，也没有安装到用户环境。

`protocol/schemas.py` 将微任务、协助微任务和命令提交的字段统一改为 `expectedSeconds`，不写时默认 1800，接受 1 至 86400 的整数，拒绝 0、负数、布尔值、空值和非整数；旧 `timeoutSeconds` 在提交边界明确提示已更名，即使同时提供新字段也拒绝，没有别名。`blackboard/store/store.py`、`blackboard/tasks/workflow.py` 的提交、协助微任务写入、普通读取和紧凑读取同步改名；`cli/main.py` 的示例、`cli/cli_help.py` 的动态帮助与说明、`cli/cli_views.py` 的简略结果同步更新，Router 简略结果提供 `routerBudgetSeconds`。

预期时长存于 `tasks.expected_seconds`，普通命令的每次执行可通过 attempt 关联的不可变 task 读到它；微任务另外在每个 `workflow_turns.expected_seconds` 冻结一份，准备回合时从 task 写入，`workflow_get` 的回合列表也给出 `expectedSeconds`。这样以后可以按实际回合读取当时的预期，不依赖可变 JSON、写入年代或当前运行意图。`tasks` 用两个分离的整数列和 CHECK 约束：`decision` 记录只有正数 `router_budget_seconds`，其他记录只有正数 `expected_seconds`；`workflow_turns.expected_seconds` 为非空正数。没有加入提醒、事件、重复提醒、活动阈值或设置。

`blackboard/tasks/inquiry.py` 删除以任务创建时间估算硬截止的 `_deadline` 和 `deadline` 响应，观察响应直接给出已存储的 `expectedSeconds`，Router 则给出 `routerBudgetSeconds`；没有在这里提前实现超过预期的提醒或倒计时。

`buddy/harnesses/base.py` 的执行上下文只从 `spec.routerBudgetSeconds` 读取硬预算，普通微任务与命令不携带此字段；`buddy/runtime/worker.py` 的停止循环只在它存在时计算硬截止。`buddy/runtime/command.py` 为普通命令使用无限 handle deadline；`buddy/harnesses/run_contract.py` 的原生 `RunBudget.timeout_seconds` 以 `None` 表示没有整段执行预算，并拒绝 0；`buddy/harnesses/native_support.py`、`buddy/harnesses/controller.py` 和 `buddy/roles/run_execution.py` 将这个不存在的预算传到原生执行路径和 handle，删去旧 0 哨兵分支与 `unbounded_deadline` 参数。DSH ACP 与 ZCode 协议的相应源码说明也同步更新。原生发现、元数据读取、无工具结构化请求和控制帧中的有限 `timeoutSeconds` 仍是相应内部操作的预算，不会从普通工作预期时长推导整段停止时刻。

`blackboard/routing/decision.py` 为 Router 专门存储并提交 `routerBudgetSeconds`：spec 和 relational 列保存用户设置的实际预算加原有 10 秒停止宽限，嵌套 `decision.routerBudgetSeconds` 与冻结请求顶层保存实际预算。Router 设置和冻结 `budget.timeoutSeconds` 的名称保留，用途始终是 Router 内部硬预算；实际预算仍取自 Router 的档位与模式。独立 `selection_request` 不再接受调用者提供的 `timeoutSeconds` 覆盖档位，换下一个 Router 的名单、冻结输入、容量与 owner fencing 规则沿原路径执行。

真实子进程的预算测试发现既有 Worker 路径在到点取消 Router 后可能收到适配器的 `cancelled`，从而未换下一个 Router。修正在预算触发的根因处：Worker 记录 `failed`、`router-budget-exhausted` 与 `deadline`，仍使用适配器实际给出的停止证据；没有把 unknown 改成 stopped，也没有补造 native 停止证据。只有确认停止后，现有黑板路由路径才派发下一个 Router。`worker_result` 的边界拒绝普通执行提交 `deadline`，它从此仅表示 Router 预算用完；其他终止原因、Host 取消、急停和收尾方式未改。

新增 `blackboard/store/upgrade.py`，由显式、空闲升级路径在已验证的 schema 19 备份之后调用，单事务重建 `tasks` 与 `workflow_turns` 并写入 schema 20；启动普通 Database 仍只接受当前 schema，不会自动迁移旧板。DDL 从当前 schema 取出再重建，外键和完整性在事务提交前检查，升级样板测试逐项比对升级后的 SQLite 定义与新建看板。`install/upgrade.py` 只接收基点 19 或当前 20，记下 migrating/migrated 阶段，并以转换后的快照核验目标；转换或启动失败恢复原 schema 19 备份。`blackboard/store/backup.py` 在该待做升级期间允许验证带显式 schema 19 标记的备份，以便转换前备份和失败回滚。

普通存量 `timeoutSeconds` 的正数按同值改为 `expectedSeconds`，旧 0 改为 1800；Router 外层旧值包含宽限，按原值改为 `routerBudgetSeconds`，嵌套与请求中的实际预算也按原值改名，Router 设置与 `budget.timeoutSeconds` 不变。微任务原始规范、task canonical 规范和相应 input fingerprint 转成新形态，回合预期从转换后的 task 回填；服务命令回执里的 task/attempt 视图也转换，`task.submit` 回执 request hash 随新 fingerprint 更新。原始回合 input JSON、其 input hash、native outcome、provenance、prompt hash 和工作区成果文件都保留；命令回执里的模型 result 与原始 input 不作为服务视图递归改写。

旧普通 Worker 的回执若写着 `terminationReason=deadline`，升级清除这个顶层原因及服务缓存中的相应投影，保持状态、实际停止证据与原始 native 结果；不能把历史微任务硬停重新说成 Router 预算用完，原始事实仍在升级前 schema 19 备份和原生证据中。Router 的旧 `deadline` 保留，其他原因不变。`workflow_runs.request_fingerprint` 保留为原始提交身份，存量中未保存当初宏任务参数的 presentation，不能凭结果反造一个新 hash；因此旧微任务 submission 的 requestId 不保证能以新字段重放，重放冲突时应使用新 requestId，没有两套 fingerprint 读法。

替换了“不限时长”的 0 提交和无限执行测试，改为正数预期、超过预期继续执行与显式取消仍有效的测试；删除了旧 inquiry deadline 估算测试、controller 的 0 与额外无限参数测试，以及通过普通 command 夹带 Router 预算的测试，后者由真实 decision 记录、真实子进程、真实 Worker 和真实黑板派发下一个 Router 的集成测试取代。其他夹具中普通执行规范改用 `expectedSeconds`，Router 规范改用 `routerBudgetSeconds`，原生控制帧与 Router budget 的 `timeoutSeconds` 不改。新增默认 1800、0 与旧字段拒绝、回合可读、SQL 形态约束、普通执行不能报告 deadline、升级换算与失败回滚、保留 native 结果的测试。

控制台只把 `task-activity.tsx` 中 `deadline` 的文案改为“Router 预算用完”，相应测试与 waitingReason 样例同步；前端没有普通工作时长提交或读写字段，Router 档位的 `timeoutSeconds` 继续保留。使用符合 package engines 的 Node 24.21.0，npm 缓存位于检出 `tmp/npm-cache`，浏览器位于检出 `tmp/playwright-browsers`；运行 `npm --prefix apps/console ci`、`npm --prefix apps/console test`（78 文件、1091 项通过）和 `npm --prefix apps/console run test:browser`（包含 tsc、Vite 构建和 96 项浏览器检查，全部通过）。打包资产重新生成：新 `index-BpeCH3Ba.js` 替换旧 JS，`index.html` 同步，布局、CSS 和 SVG 没有改动。

本回合 Python 检查始终使用 `PYTHONPATH=src <Host 指定的私有解释器> -m hey_my_buddy.cli.checks <测试文件或目录>`，没有直接启动测试、传 jobs 或设置 HMB_CHECKS_JOBS，也没有修改解释器环境；进程数取运行器默认值。本回合没有运行完整检查。所有最终通过的运行均退出 0，运行器确认删除私有根；原始日志保留在忽略的 `tmp/expected-seconds/` 和 `tmp/buddy-expected-seconds-*.log` 中。

第一组检查选择整个 `blackboard/routing`、`blackboard/store/test_store.py`、`blackboard/tasks/test_workflow.py`、`blackboard/tasks/test_inquiry.py`、`cli/test_host_cli.py` 与 `cli/test_host_preview.py`，共 38 文件；其中 11 文件失败，原因是旧规范/控制帧夹具、普通 SQL 夹具错误写入 Router 列、帮助范围仍断言 10 和新增回合测试漏注册 Worker，均据堆栈修正。第二组 `blackboard/routing` 加 workflow 与 host_cli 共 35 文件，32 文件/479 项通过，3 文件失败：漏导入 patch、两处夹具字段、普通容量 fixture 写错列；修正后都在后续受影响检查中通过。没有将这些实现/夹具失败说成机器时序失败。

受影响检查 `blackboard/store`、`blackboard/tasks`、`protocol`、`cli`、`console`、`blackboard/service/test_live_registry.py` 及修正后的 decision/failover/router_probe 共 83 文件，82 文件/1457 项通过，唯一失败是 current_core 的 meta 写入白名单未包含显式升级；白名单按新增 schema_version writer 更新后，在最终检查中通过。首次真实 Router 子进程检查 78 项中 1 项失败，定位到预算到期仍报告 cancelled 的根因并修复；紧随的 6 文件检查 5 文件/194 项通过，旧的“普通命令夹带 Router 预算仍应取消”断言失败，该测试已被合法 decision 子进程切换测试替代。没有未解释或仍失败的授权范围测试。

最终 `blackboard/store`、`install`、`buddy/runtime`、`blackboard/routing/test_router_failover_integration.py` 和 `cli/test_cli_views.py` 共 33 文件、417 项全部通过，日志为 `tmp/expected-seconds/final-boundaries.log`，包含最终源码的 schema 19→20 样板/新板比对、9 项升级 fixture 检查、备份回滚、正常工作超过预期、Router 停止与切换、停止未知 fencing。另跑四个 harness 的 `test_native_run.py`、Codex/ZCode `test_batch4_facts.py`、`test_runtime_version.py`、`blackboard/evaluation/test_native_observations.py` 及 install，共 25 文件/539 项通过（`native-install.log`）；`blackboard/evaluation/test_other_native_observations.py` 与 `blackboard/catalog/test_native_discovery_stop.py` 共 2 文件/43 项通过（`remaining-observations.log`）。这些检查都没有真实模型调用。

buddy 侧分阶段跑过 contract/controller/protocol、四个 harness 的 fixture 执行与 DSH role/storage、Codex 超过预期、Worker/role/private adapter 及私有打包 runtime 的检查，均通过；最终补查的 7 个 harness/工具证据文件 140 项通过，4 个角色/controller 文件 86 项通过，Claude Worker 文件 3 项通过，对应 `tmp/buddy-expected-seconds-harness-checks.log`、`tmp/buddy-expected-seconds-role-checks.log` 与 `tmp/buddy-expected-seconds-claude-worker-checks.log`。没有发生仅凭机器繁忙判定的时序失败，因而没有无改动的时序重跑记录。`git diff --check` 通过；说明文件写完后单独运行 `cli/test_repository_hygiene.py`，5 项通过，日志为 `tmp/expected-seconds/notes-hygiene.log`。

授权范围之外发现未同步的 probe 调用：`tests/probes/dsh_native_resume_smoke.py` 的实际 `workflow_submit` 仍发送 `timeoutSeconds:180`，新契约将明确拒绝，需 Host 更新或扩充写入范围；`tests/probes/macro_task_console_preview.py` 的合成 task 视图仍写旧字段、默认 0，`tests/probes/router_fast.py` 与 `tests/probes/router_readonly.py` 的手构 ExecutionContext 也仍写旧字段。后两者的实际原生请求预算仍单独传入，但规范应由 Host 改为 Router 专用字段。这四个文件均不在冻结写入范围内，故没有修改或运行其中的真实 probe；它们是交付中需要 Host 处理的范围缺口，其余无不一致情形。

没有实现 08/09 提醒、10 急停范围或 11 有序收尾，没有改 docs 的其他文件、skills、README、AGENTS、CONTEXT、升级基点 fixture 或版本号；没有提交、stash、分支或标签操作，没有操作日常服务、Worker、状态、配置、凭据、账户或产品安装。最终验收与整合后完整检查由 Host 负责。
