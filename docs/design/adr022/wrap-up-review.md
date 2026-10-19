# 有序收尾与终止原因：独立审查

审查对象是微任务“有序收尾与终止原因”在基点 `62d4ef6b` 上交回的 `efb9e7e5`，范围是 `git diff 62d4ef6b efb9e7e5 -- src tests apps/console/src`，对照 ADR-022 第 4、7 条、AGENTS.md 的 Invariants 与 CONTEXT.md。方法是读代码；没有运行检查套件、服务或 harness。只做了一处可执行的核对：用系统 Python 直接调用升级里的纯函数 `_receipt_object`（见 A9），其余结论都来自读代码，标为“推导”的没有复现。`docs/acceptance/` 未读；实现方说明在清点完成后才读，只用于最后一段的对照。

## A. 停止证据

总的结论先说：没有找到让“不知道”被当成“已经停了”的路径。黑板在 `src/hey_my_buddy/blackboard/store/store.py:2481` 把“停机未确认且有停止意图、发起者或 cancelled 状态”一律记为 `reconciliation-needed` 并保留资源（2527-2534），比基点更严；五种停止原因在 2316-2321 都要求确认停机；Worker 只在 `shutdown_confirmed` 时释放端点与凭据（`buddy/runtime/worker.py:818-820`）；收尾回合用它自己的停止证据，第二个原生进程抛异常时记为 `group_state="unknown"`（`buddy/roles/run_controller.py:157-167`）；原生 harness 都以 `start_new_session=True` 启动，外层控制器被强杀时拿不到回执，结果为未确认而不是已停。下面十条按严重程度排列。

**A1 Worker 运行时先停、Host 后停：续租线程崩溃，发起者记错。** 事件序列：Worker 运行时收到停止（`worker.py:790-793`），设 `handle.stop_initiator="unknown"` 并经 `worker_cancel` → `_force_role_stop` 非独占写入 `role-stop-control.json`（`buddy/roles/controller.py:120-137`）；随后 Host 对同一微任务 `cancel`，黑板记 `stop_initiator=host` 并给 300 秒截止；续租线程 `_request_cancel`（`worker.py:974-988`）看到 `host != unknown`，调用 `worker_stop`，后者因可续接走收尾分支，用 `exclusive=True` 再写同一个文件（`controller.py:156-163`），`os.link` 抛 `FileExistsError`。`_Renewal.run`（`worker.py:949-960`）不接这个异常，线程结束：此后不再续租、不再转发活动、也观察不到控制台急停。Worker 最终回执报 `stopInitiator=unknown`，黑板在 `store.py:2385-2401` 因为 Worker 报的是 `unknown` 而不报错，改成当前耐久发起者 `host`、原因留空，而真正结束运行的是 Worker 运行时自己的停止。后果：续租中断可能让 attempt 被判失联而进入对账；收据与列都把发起者写成 Host。测试做法：在 `tests/python/buddy/runtime/test_worker_invariants.py` 的 `DurableStopObservationTests` 风格下，先对持有私有根的句柄调 `controller.worker_cancel`（发起者 unknown），再调 `_Renewal._request_cancel({"cancelRequested": True, "stopInitiator": "host", "graceDeadline": <未来时刻>})`，期望不抛异常、不转入收尾；黑板侧在 `workflow_cancel` 之后提交 `stopInitiator="unknown"` 的 `worker_result`，期望发起者保持 `unknown`。本条由读代码推导，`private_json(..., exclusive=True)` 经 `os.link` 实现（`buddy/roles/turn_io.py:88-90`）这一点已读到。

**A2 宽限之后交付的总结仍被接受，宽限内交付的总结可能被记成到期。** `WorkerRunExecutor.collect` 在状态为 `cancelled` 时总把合法的 `record` 放进 `payload["turn"]`（`buddy/roles/run_execution.py:730-731`），不看是否已过宽限；黑板对已取消的工作流走 `_late_cancelled_turn`（`blackboard/tasks/workflow.py:3558-3574`），只要停机确认且回合合法就把它导入为 `returned`，不看终止原因。事件序列一：截止到达，Worker 循环（`worker.py:794-799`）标记 `grace_expired` 并强制停止；模型恰在截止后、中断落地前调用了结束工具，回合文件已写；收集得到 `grace-expired`，回合仍随回执提交并被导入。事件序列二：控制器在截止前一秒判定总结合格（`run_controller.py:168-182`）并退出；Worker 每 0.25 秒轮询一次，收集时 `run_execution.py:702-703` 用收集时刻重新判断 `expired`，结果把按时交付的总结记成 `grace-expired`。后果：同一条记录里“宽限到期强制停止”与一份被导入的总结并存，Host 读到的原因与发生的事不符。测试做法：照 `tests/python/buddy/roles/test_graceful_stop.py` 的 `test_expired_grace_overrides_even_a_valid_summary`，用合法的 `read_turn` 返回值，断言过期时结果里没有 `turn`；黑板侧提交带合法回合的 `grace-expired` 回执，断言 `workflow_turns` 不进入 `returned`。

**A3 ZCode 声明可续接却从不收尾，Claude Code 不可续接却在黑板上得到宽限截止。** `blackboard/tasks/workflow.py:3514` 用硬编码清单 `("codex", "dsh", "zcode", "claude")` 决定是否写 300 秒截止，包括 `native_resume=False` 的 Claude Code（`buddy/harnesses/claude/adapter.py:35`）。ZCode 声明 `native_resume=True`（`buddy/harnesses/zcode/adapter.py:16`），但它的运行只在 `status == "ok"` 时报 `resumable`（`buddy/harnesses/zcode/native_run.py:1659-1663`），被中断的回合永远不可续接，`_summary_run` 在 `run_controller.py:108-111` 必然返回 `None`。事件序列：Host 停止一个 ZCode 或 Claude Code 微任务 → 黑板记下 `graceDeadline`，控制台与 Host 视图都显示一个五分钟宽限 → Worker 立即强制停止，原因 `forced-stop`。后果：黑板上的截止是一个从未生效的事实；ZCode 用户得不到 ADR-022 第 4 条承诺的总结，而声明的能力说它能续接，要求 1“harness 只声明能不能续接原生会话”在 ZCode 上与行为不符。测试做法：对 adapter 为 `claude` 的在跑 attempt 调 `workflow_cancel`，断言 `graceDeadline` 为空；对真实形状的 ZCode 运行（`tests/python/buddy/harnesses/zcode/test_native_run.py` 的模拟器）取消一个回合，断言 `continuation.resumable` 与 ZCode 的能力声明一致。

**A4 迟到的控制台或未知停止改写已经交付的收尾回执。** `store.py:2385-2401` 在 Worker 报的发起者与黑板当前发起者不同时，把回执和列都改成当前发起者、原因清空，并且 `request.update` 改写的是要存下来的回执本身。事件序列：Host 停止 → Worker 完成合法总结，收集得到 `graceful-stop`/`host` → 回执送达前开发者在控制台急停，黑板改为 `console`、清掉截止（`workflow.py:3503-3522`）→ 回执送达，存下的是 `stopInitiator=console`、`terminationReason=null`。这时控制台的请求没有停下任何东西，结束运行的是 Host 的收尾，Worker 的收集结果就是这件事的停止证据。同样的改写也发生在“控制台先、服务停止后”的组合：服务停止把发起者改成 `unknown`（`workflow.py:3505-3506`），控制台的真实停止被记成未知。后果：回执里写入了一个没有发生的发起者，`graceful-stop` 这个已证实的事实被丢掉。现有测试 `test_console_escalation_during_host_receipt_preserves_shutdown_and_replay` 把这种改写当成预期。建议：列可以记最新的耐久意图，回执保留 Worker 观察到的发起者与原因；要两者一致，就只在 Worker 报告的发起者确有更早的耐久请求时接受它，不改写。

**A5 升级对原生绑定的换算不能在崩溃后重做。** `blackboard/store/upgrade.py:232-235` 在数据库 `COMMIT` 之前就把 Codex 与 DSH 的绑定文件改写成带 `lastTurnStatus` 的新形式；异常时会写回，但进程被杀（断电、`SIGKILL`）时不会。之后重试，`_native_bindings`（`upgrade.py:153-172`）要求文件严格等于 schema 19 的形式，遇到已换算的文件就抛“differs from the schema-19 base form”，升级永远过不去，只能手工修文件。另外，升级前的备份只有数据库：升级成功后若退回备份与 0.35.0，已换算的绑定会被旧代码拒绝，原生续接降级为重建会话。测试做法：在 `tests/python/blackboard/store/test_upgrade_fixture.py` 的夹具里先把一份绑定手工改成新形式（模拟写完文件后进程被杀），再对 schema 19 的看板调用 `migrate`，期望成功；现在会抛 `ValueError`。更简单的做法是提交数据库之后再换算文件，并让换算对已是新形式的文件幂等。

**A6 服务停止被记成“发起者未知”，控制台把已知的“服务停止”显示成未知。** `workflow.py:3505-3506` 把 actor `service-stop` 映射为 `unknown`；控制台 `apps/console/src/task-activity.tsx:147-161` 先看 `stopInitiator`，`unknown` 在第 153 行就返回“停止（发起者未知）”，第 161 行的“服务停止”再也到不了。基点在这里显示“服务停止”。后果：一个黑板确知的发起者在列里和界面上都成了未知。测试做法：控制台单测给 `cancellation.actor="service-stop"`、`selectedAttempt.stopInitiator="unknown"`，期望标签为“服务停止”。是否给服务停止一个独立的发起者取值，属于要求 3 的词表，由 Host 定。

**A7 收尾回合不合格时，Host 已停下的任务被记成 `failed`，停止原因丢失。** `store.py:2349-2357` 在 `precheck_result` 失败时把状态改成 `failed` 并清空 `graceful-stop`；于是 2481-2500 的分支落到 `task_state="failed"`。事件序列：Host 停止 → 总结回合交付了一个身份或内容不合格的结果 → 停机已确认 → 任务行为 `failed`、原因为空，工作流行为 `cancelled`（测试 `test_invalid_summary_identity_never_keeps_a_graceful_completion_reason` 只断言了工作流状态）。真实情况是 Host 停止、宽限内没有合法交付、停机已确认，按本次词表就是 `forced-stop`。后果：任务行与工作流行两种说法，终止原因丢了一个已知事实。测试做法：在上面那个测试里再断言任务 `state` 与 `terminationReason`。

**A8 DSH 绑定的 `lastTurnStatus` 在升级前后含义不同。** 基点 DSH 在续接回合里保留已校验过的旧绑定（基点 `dsh/native_run.py` 中 “a resumed turn keeps the binding it already verified”），后来失败或取消的续接回合不会删它；所以 schema 19 的 DSH 绑定只证明“这个会话曾有一个完成的回合”。升级给它们一律写 `lastTurnStatus="completed"`（`upgrade.py:232-234`），而新代码在每次新 prompt 前删绑定（`buddy/harnesses/dsh/native_run.py:1414-1418`），新写的值表示“最后一个回合的状态”。同一个字段的含义随写入时间而变。DSH 的代码不读这个值（`dsh/native_run.py:404-407` 两种取值都接受），所以目前没有行为后果，见 B 第 12 条。测试做法：基点上先完成一回合、再取消一个续接回合，升级后断言该绑定没有被标成 `completed`，或者 DSH 不带这个字段。

**A9 升级不再清理任务视图 `resultMeta` 里旧的 Worker `deadline`。** 已执行核对：对 `{'runId':'t','adapter':'command','terminationReason':'deadline','resultMeta':{'terminationReason':'deadline',...}}` 调用 `upgrade._receipt_object`，顶层原因被清空，`resultMeta.terminationReason` 仍是 `deadline`。原因是 `upgrade.py:118` 递归进 `resultMeta` 时没有带上 `adapter`，`_termination`（92-101）只在知道 adapter 时清理 `deadline`；基点的同名函数显式处理了 `result` 与 `resultMeta` 两个成员。后果：同一条记录里一处说没有分类，一处说 Router 预算用完。实际影响取决于存量：我只找到 `store.py:1078` 的只读视图构造 `resultMeta`，没找到把它存进 `commands` 或 `events` 的写入方，也无权查看日常看板，所以存量里有没有这样的行未核对。测试做法：在 `test_old_worker_deadlines_are_cleared_without_rewriting_native_evidence` 里给缓存的任务视图加 `resultMeta`，断言它的 `terminationReason` 为空。

**A10 新回合入场前就删绑定，入场失败时删掉了仍然有效的绑定。** Codex 在 `turn/start` 之前删除（`buddy/harnesses/codex/native_run.py:1150-1153`），DSH 在配置之后、prompt 之前删除（`dsh/native_run.py:1414-1418`），ZCode 在会话绑定时删除（`zcode/native_run.py:1367-1371`）。如果入场请求本身被拒、原生会话里并没有新回合，原绑定仍然准确，却已被删，下一次 `continue` 只能重建会话。这是保守方向，不会让续接用上错误的绑定；后果是少一次原生续接。测试做法：让模拟的 `turn/start` 返回错误，断言绑定仍在或续接仍可用；若决定保守到底，至少在说明里写明。

## B. 过度防御

### 站得住的守卫

`store.py:2314-2315` 校验 `stopInitiator` 的取值：Worker 回执是另一个进程的输入，守在边界上。`store.py:2316-2321` 要求停止原因带发起者并且停机已确认：守的是“不知道不等于已停”，去掉就会出现未确认的停止原因。`store.py:2383-2384` 要求 `host`/`console` 发起者在黑板上有耐久请求：防止 Worker 冒称一次没有发生的 Host 或控制台停止。`store.py:2402-2404` 要求 `graceful-stop`/`grace-expired` 有耐久截止：防止 Worker 声称一个黑板从未给出的宽限。`workflow.py:6380-6387` 让 `graceful-stop` 的回合也经 `_validate_turn`：模型给的内容在边界上校验一次，这是本批修掉的真实漏洞。`workflow.py:3512-3513` 重复 Host 停止不改原截止、3515 只给 `starting`/`executing` 的 attempt 宽限：守的是“宽限不被延长”和“状态未知不续接”。`run_controller.py:108-112` 要求首个原生进程组 `gone`、会话身份明确、运行器声明可续接才发起总结：守的是“不在可能仍有进程写入时续接”，是本批最要紧的一处。`run_controller.py:67-80` 校验私有停止命令绑定本次调用身份：文件来自另一个进程，守在边界上（但见第 6 条，强制停止那一半可以去掉）。`run_execution.py:745-750` 收集时要求未请求强制停止、原生组已消失才算 `graceful`：保证强制停止永远不会被记成 `graceful-stop`。`worker.py:794-799` Worker 用本地单调时钟强制到期：不依赖黑板可达，守住宽限上限。Codex 的 `_save_binding` 对中断回合要求终止通知确认为 `interrupted` 且进程组已停（`codex/native_run.py:1435-1443`），续接时要求 checkpoint 并经 `thread/read` 核对末回合状态（645-669）：守的是“续接不用上不该用的绑定”，都在与另一个进程的边界上。DSH 要求 `stopReason=cancelled` 且 `session/close` 被确认（`dsh/native_run.py:1119-1121`、1436-1440）：同理。ZCode 只在完成回合且停机确认后写绑定（`zcode/native_run.py:1168-1179`）：修掉了基点“回合未完成先写绑定”的根因。`upgrade.py:153-172` 用不跟随链接的普通文件读取、限长并核对完整形式：这是会覆盖文件的动作，校验应当在这里做。

### 过度的与更简单的写法

1. **用 `getattr(..., 默认值)` 读没有声明的句柄属性。** `ProcessHandle`（`buddy/harnesses/base.py:229-251`）没有声明 `stop_initiator`、`grace_deadline`、`grace_expired`、`force_stop_requested`、`graceful_stop_started`、`role_run_active_control`、`role_run_active_identity`，代码在 `controller.py:112,123-124,145,153`、`run_execution.py:449-450,476-480,650,700-702,743,746`、`worker.py:794-796,847-851,976,988` 一律用 `getattr` 带默认值去读，执行器能力也用 `getattr(getattr(executor, "description", None), "native_resume", False)`（`controller.py:145`）。更简单：在 `ProcessHandle.__init__` 里声明这几项（或放进一个小的停止状态数据类），直接读；命令执行器显式声明 `native_resume = False`。
2. **与已声明能力重复的硬编码清单。** `workflow.py:3514` 的 `("codex", "dsh", "zcode", "claude")` 与各 adapter 的 `native_resume` 重复且相矛盾（见 A3）。同一文件 5793 已经用 `self._execution_adapter(harness).native_resume` 读能力，这里照用即可；ZCode 在能证明中断后可续接之前应当如实声明。
3. **角色层里写死 harness 名字。** `run_controller.py:114` 的 `if request.harness == "codex" and checkpoint is None` 重复了 Codex 原生模块自己的拒绝（`codex/native_run.py:654-656`），也违背“收尾业务只在角色层、harness 只声明能力”。更简单：总是把 `_stop_checkpoint` 的结果（可能为空）交给续接请求，由 harness 自己拒绝；或者写成不分 harness 的规则“运行留下了 native-checkpoint 证据，就必须核对通过”。
4. **总结的交付校验做了两遍。** `_summary_run` 里的 `format.parse_value` 与 `worker_delivery`（`run_controller.py:175-181`）在 `execute` 紧接着的 255-264 又做一遍。更简单：`_summary_run` 只回答“按时、同一会话、进程组已消失”，`execute` 在自己的交付校验之后令 `gracefulStop = summarized and code == 0`。
5. **“是不是收尾完成”判了三次。** 控制器写 `gracefulStop`，`run_execution.py:700-752` 重新核对进程组、回合、错误、是否过期并算出 `stopReason`，`worker.py:97-113` 的 `classify_termination` 再决定是采用 `stopReason` 还是按句柄重新计算，过期还各判一次。更简单：收集只报告事实（`gracefulStop`、回合是否合法），终止原因只在 `classify_termination` 判一次。
6. **强制停止有两套通知。** `_force_role_stop` 写一份 `force-stop` 命令覆盖收尾命令（`controller.py:120-137`），控制器每次轮询重新读文件（`run_controller.py:148-150`），同时又靠信号世代判断。强制停止总会经 `terminate` 发出信号，所以“收尾开始后又来了信号”本身就是急停的证据：令收尾的那次 `SIGTERM` 为第一个信号，世代大于一即急停。这样 `force-stop` 这种命令、它的独占与非独占两种写法（A1 的成因）都可以去掉，私有命令文件只剩“Host 收尾及其截止”一种。
7. **摘要描述文件带了一整份控制字典，再逐字段核对。** `run_execution.py:448-466` 读子进程写的 `role-stop-summary.json`，把其中 `control` 的五个路径与自己能推导出的值一一比较，再用它定位请求与裁决文件。Worker 知道摘要目录就是 `<privateRoot>/stop-summary`、文件名是常量，直接推导路径，描述文件只需提供第二次调用的身份。
8. **同一条件写了两遍。** `store.py:2329` 的 `graceful-stop` 要求 `shutdown_confirmed`，与 2319-2321 对所有停止原因的要求重复；只保留 `status == "cancelled"` 一项即可。
9. **发起者在回执里编码了两次，于是需要四条交叉校验和一个冲突分支。** 五种停止原因本身就决定了发起者（`graceful-stop`/`grace-expired`/`forced-stop` 是 Host，`console-stop` 是控制台，`unknown-stop` 是未知），`store.py:2323-2328` 的三条一致性检查和 2385-2401 带 `for_run` 与 actor 前缀解析的冲突分支，都是为两份编码对齐。更简单：停止原因已定时由原因推出发起者；原因为空（停机未确认）时沿用黑板上的耐久发起者；Worker 本地停止用 `unknown-stop` 表达。`attempts.stop_initiator` 这一列仍然按要求单独记录。
10. **把 actor 字符串再解析回发起者。** `workflow.py:3505-3506` 用 `startswith("host:")`/`"console:"` 还原发起者，而 `_authorize`（808-838）本来就知道调用者是 Host 还是控制台会话。更简单：`_authorize` 一并返回发起者，沿调用链显式传下去。
11. **边界之内的小重复。** `worker_stop` 的“已是 Host 就返回”（`controller.py:153-154`）与调用方 `worker.py:977` 的判断重复；`_summary_run` 先判 `time.monotonic() >= deadline` 再判 `remaining <= 0`（`run_controller.py:111-118`），`summary_cancelled()` 在启动前后查了四次，还核对同进程内 `run_harness` 返回值的身份与 harness（108、169）；`classify_termination` 的 `status == "ok" and not grace_expired`（`worker.py:104`）在角色运行里已由收集保证、命令执行器又没有宽限。这些都可以删到各留一处。
12. **没有使用者的字段迫使升级改写文件。** DSH 新增的 `lastTurnStatus`（`dsh/native_run.py:390-407`）没有任何读取方据它决定行为，却让升级去改写每个 DSH 绑定（A5、A8 的一部分）。更简单：DSH 不加这个字段，升级只换算 Codex 绑定；Codex 的字段有真实读取方（`codex/native_run.py:645-669`），站得住。
13. **为写不下一个续接缓存而丢掉已完成的回合。** 三个原生模块在写绑定失败时把整次运行改成 `error`/`native-binding-save-failed`（`codex/native_run.py:1448-1453`、`dsh/native_run.py:1128-1135`、`zcode/native_run.py:1168-1179`），一个已经合法交付、停机已确认的回合因此不被导入。代价与收益不相称：绑定只决定下一回合能否原生续接。更简单：把 `continuation.resumable` 记为 `False` 并保留回合结果（基点 DSH 的做法），下一回合由黑板选重建会话。
14. **升级里处理一种不存在的存量。** `upgrade.py:127-135` 从 `resultMeta` 推导任务视图的发起者，而我只找到只读视图在构造 `resultMeta`（`store.py:1078`），没有写入方把它存下来；这一支连同 A9 的遗漏都可以按“没有存量就不写”处理，或者先在日常看板的备份上量一次再决定。
15. **用鸭子类型区分角色运行与命令执行。** `_force_role_stop` 靠 `isinstance(control, dict)` 和身份是否为空判断句柄属于谁（`controller.py:123-126`）。更简单：让 `WorkerRunExecutor` 与 Router 执行器各自提供停止方法，命令执行器的同名方法直接终止进程组。

## C. 删掉或放宽的检查

**C1 升级不再清理任务视图 `result`、`resultMeta` 成员里的旧 `deadline`（无人守）。** 基点 `_receipt_object` 显式处理这两个成员；现在 `result` 改按原生证据原样保留（有测试断言，站得住），`resultMeta` 则因为递归时丢了 `adapter` 而不再清理（A9，已执行核对）。原来守的事实是“旧 Worker 时限不被读成 Router 预算”；现在没有地方守，后果见 A9。

**C2 强制停止给控制器的回收时间被统一缩短（无人守）。** 基点里 `worker_cancel` 用各执行器的默认值：Worker 角色 8 秒（`run_execution.py:757`）、Router 12 秒并注明“控制器把 SIGTERM 转成原生中断再回收自己的进程组”（`buddy/roles/router.py:164-166`）、命令执行 3 秒（`buddy/runtime/command.py:16`）。现在凡有角色控制器的句柄一律 5 秒（`controller.py:137`），包括 Worker 运行时自停（`worker.py:793`）、宽限到期（799）和 Router 预算到点（805）；命令执行器在 `worker_stop` 的强制分支里是 0 秒（`controller.py:150-151`），即立刻 `SIGKILL`、没有清理机会。原来守的事实是“控制器有足够时间中断并回收另起会话的原生进程组、写出停止回执”。现在没人守：超时后外层被强杀，原生进程组因 `start_new_session=True` 成为孤儿继续运行（仍可能在检出里写、继续用额度），停止证据如实变成未确认、任务进入对账、资源保留。停止证据本身不会说谎，但会出现更多孤儿进程和对账。急停范围（要求 2）在“回收时间”这一点上被改动了。

**C3 `_cancel_owned_run` 的 `AND cancel_requested_at IS NULL` 被改成“后来的非 Host 请求可以覆盖发起者与截止”（`workflow.py:3503-3522`）。** 原来守的是“第一次停止请求的时刻不被改写”；现在 `cancel_requested_at` 仍用 `COALESCE` 保留，这个事实还在。发起者被后来者覆盖是有意的设计，它的问题已在 A4 说明。

**C4 黑板去掉了“`cancelled` 且停机未确认才对账”的窄分支**，换成 `store.py:2481` 的宽条件，覆盖了带停止意图的 `failed` 回执。这是收紧，原来的事实仍被守着。

**C5 `classify_termination` 不再在停机未确认时给出 `user-cancel`。** 原来任何取消请求都记为用户取消；现在未确认时原因为空（`controller.py:98-107`）。这是收紧，测试 `test_the_classification_keeps_every_real_reason_distinct` 覆盖了未确认的三种发起者。

**C6 `_read_run` 不再要求结果身份必须是本次调用。** 现在在 Host 收尾时也接受第二次调用的身份（`run_execution.py:441-470`）。原来守的是“回执属于本次调用”，现在由私有描述文件、父身份比对与收集时的会话一致性检查（650-655）共同守着；Host 强制停止而没有描述文件时仍被拒绝。站得住，可简化见 B 第 7 条。

**C7 Codex 中断回合的绑定不再要求退出码为 0**（`codex/native_run.py:1440-1442`），改由终止通知 `interrupted` 加进程组确认消失来守；完成回合仍要求退出码 0。站得住。

**C8 DSH 取消了“已有相同绑定即视为已绑定”的读回，以及中断分支对 `provenance` 的要求**（`dsh/native_run.py:1118-1137`）；前者由“新 prompt 前删除、结算后独占新建”代替，后者由 `stopReason=cancelled` 与关闭确认代替。站得住。

**C9 ZCode 去掉了打开会话时写绑定**（基点 `zcode/native_run.py` 中 `bind_worker_session` 内的写入），`resumable` 改为要求 `status == "ok"`。这是收紧，也是 A3 中 ZCode 永远不能收尾的直接原因。

**C10 Codex `_write_binding` 从“临时文件加替换”改为独占新建。** 原来允许覆盖；现在不覆盖，由新回合前的删除保证不冲突。站得住，代价见 A10。

**C11 `store.cancel` 的停止意图从只写选中的 attempt 改为写该任务所有未结束的 attempt**（`store.py:1125-1129`，经 `_request_attempt_stop`）。范围变宽，没有丢掉事实。

**C12 控制台删掉了 `user-cancel` 的显示分支**（`task-activity.tsx` 原第 177 行）。旧取值由升级换算，除 A9 的 `resultMeta` 外不再出现；`terminationLabels` 对未知取值原样显示，不会报错。

## 与实现方说明的对照

`docs/design/adr022/wrap-up-notes.md`（当时名为 graceful-stop-notes.md） 与代码有六处不符：其一，第 11 段说“截止之后不接受收尾交付”，而收集与黑板都会把截止后写出的合法回合随 `grace-expired` 一起导入（A2）；其二，第 11 段说“命令执行器直接拥有工作进程组，保持直接停止”，实际是把命令执行的 3 秒宽限改成 0 秒，并把 Worker 8 秒、Router 12 秒（含 Router 预算到点）统一改成 5 秒，说明没有提这个改动（C2）；其三，第 13 段说 Claude Code 的 Host 停止“直接强制停止”，Worker 一侧如此，黑板一侧仍为 Claude 写下 300 秒的 `graceDeadline`（A3）；其四，第 17 段说冲突时“不捏造控制台执行的事实”，实际会把存下的回执的 `stopInitiator` 改写成后到的发起者，Worker 运行时自停后再来的 Host 停止还会被记成 Host 发起（A1、A4）；其五，第 31、33 段说旧微任务的 `deadline` 已按上一步规则清空、换算失败“不留下两种读法”、DSH 基点绑定“只在成功回合后保存，因此可以明确换算为已完成”，而任务视图 `resultMeta` 里的 `deadline` 不再清理（A9），进程被杀时文件已换算而数据库未提交且无法重试（A5），DSH 基点绑定在后来失败或取消的续接回合之后仍然保留（A8）；其六，第 51 段说“与要求没有新增范围偏离”，但终止原因比要求 3 列出的四种停止多了 `forced-stop`，写绑定失败会让已完成的回合变成错误（B 第 13 条），这两处超出了要求的字面，应当由 Host 决定是否接受。说明第 27 段对 ZCode 不收尾的陈述与代码一致，第 51 段对未验证范围（真实 harness 收尾、Windows 与 Linux 原生运行）的陈述也与我看到的一致。另记一处用词：CONTEXT.md 把这件事叫“有序收尾 / wrap-up”并列 graceful shutdown 为应避免的说法，而代码与取值用 `graceful-stop`、`graceful_stop`；Host 的任务说明本身也用 `graceful-stop`，所以这不算实现方的缺陷，是否在术语表里收录这个取值由 Host 定。
