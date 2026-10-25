# Windows 阶段 E（共同接收与最终结算）：执行计划

2026-10-25。依据 [Windows 所有权设计](windows-ownership.md) 第 5、8 节与 [D2 执行计划](windows-d2-plan.md)。初稿由只读微任务 `0a6679b5` 写出，Host 与一次 Opus 独立审查各自审过、互不看对方的结论；Opus 指出的缺口都由 Host 在源码里核对过才写进这里。

## 1. 范围与拆批

E 要做到：native 工作停止后写不可变回执；服务核对调用方、固定路径、摘要、绑定与回执形状，在一个事务里插入接收行并把 attempt 置为 `finalizing`，随即回答“已接收”；执行收到回答就退出；服务取得执行根的停止证据后，在一个事务里导入结果、结清问询、写事件、释放容量与 claims，并删除接收行。回执在最终结算之前不删（ACK 之后也不删，恢复要读它）；结算之后结果已在看板里、回执没有读者，它随 attempt 的私有材料由现有的检出回收与存储回收照常删掉，不另设保留（2026-10-25 E2a 第一回合查到，原写“随所属微任务删除”与这两条回收冲突；现有守卫只回收已结算的 attempt）。

不单列初稿的 E1：它要的共同停止次序已经有了。四个 harness 的正常、取消与启动失败都经 `buddy/harnesses/native_support.py` 的 `halt_owned_group`（关 stdin、等主进程、确认、不空就终止、再确认），停止确认不了就不算成功、不封存（`buddy/roles/run_execution.py`），也有测试；“记录实际清理”没有读者，不加字段。剩下的只有停止来源的一般规则，随 E2a 做。

E 在 D2 的整合结果 `13e94123` 上开工（Host 已读完 D2a 与 D2b 的差异），与 D2 同属 0.53.0 与同一次 28 → 29 升级，不开 29 → 30。两个依次的微任务，各自让树保持通过，中间不安装：

- **E2a 协议主体。** 执行侧不再写 `result-delivery.json`、不再删回执；接收事务与最终结算事务；下面第 2 节的规则；恢复与调度的改写；接收表的 DDL 在新建与 28 → 29 里共用；`verify_started` 的预期表集合加上这张表；测试支持；停止来源的一般规则（`blackboard/tasks/workflow.py` 的 `_request_attempt_stop`：已有的、不是收尾的停止从不被替换，`unknown` 也一样）。
- **E2b 退役旧存量。** 生成器用基线的写入路径补一份已接受的 delivery 存量并重新生成样板看板；`cleanup_legacy_layout` 加上由看板上的 attempt 推出的 `result-delivery.json`；对应的升级测试与文档。

初稿的 E3（独立审查、三系统完整检查、安装演练）归阶段 G，不单列。

## 2. E2a 的规则

1. **接收只查边界上的事。** 调用方（actor、generation、nonce）、固定路径、摘要、绑定与回执形状。接收事务在写事务里核对 attempt 还没结束（`_verify_attempt_actor` 不拒绝已结束的 attempt）。产物哈希、workflow 的预检与实时 stash 观察都放到最终结算，因为接收行只存摘要。
2. **最终结算里的确定性错误当场结清。** 导入时抛出的确定性业务错误（如 `ANCESTOR_TERMINAL`、`SHUTDOWN_UNCONFIRMED`、`PREPARATION_CONFLICT`、`WORKSPACE_INVALID`、`ARTIFACT_*`，见 `buddy/runtime/receipts.py` 的 `REFUSED`）就在这次结算里按现有的 failed 加 `receiptRejection` 形式结清；只有暂时的错误才留给重试。否则 attempt 会永远停在 `finalizing`，名额、claims 与检出都放不出来。
3. **不加 `receipt-rejection.json`。** 接收时的拒绝只取决于冻结的回执与 attempt 行，恢复时在文件边界重读回执就能得出同样的结论；执行收到明确的拒绝，记日志后退出。
4. **已接收的 attempt 只等停止证据。** 执行收到 ACK 后退出，连接随之关闭；这时不重连、不记“连接未知”、不改 `uncertain`、不写 issue（`store.py` 的 `execution_connection` 现在会做这些）。只有取不到停止证据时才报告。
5. **强停不看回执。** 删掉 `blackboard/service/execution_scheduler.py` 的 `_force_if_required` 里“回执写着已停就不杀”的短路，连同 `test_confirmed_receipt_prevents_force_kill`：回执已经落盘，强停到期就按已核实的身份停根。
6. **`finalizing` 只能走向 `finished`。** 改 `store.py` 的状态转换表；`execution_progress` 不再接受外来的 `finalizing`；`reconcile_startup` 与断线都不抹掉已接收的事实。
7. **收尾期间不发提醒。** `blackboard/tasks/reminders.py` 选提醒对象时排除已接收的 attempt。
8. **未确认停止的回执照写、照收。** native 停止确认不了时，执行照实写 `shutdownConfirmed=false` 的失败回执，这是它唯一的事实。服务照常接收；最终结算的条件是执行根的停止证据，加上回执已确认或服务自己证明 native 已停（Windows 由 D2 的根 Job 证据覆盖，POSIX 由 `_stop_native` 读 native journal）。满足后只导入一次。回执路径上“先提交未确认的报告、再补证明”的两段（`interruption.py` 里收到回执后补 `confirm_execution_shutdown` 的分支、`store.py` 里原报告重放的分支）与 `test_result_shutdown_proof.py` 一并删掉；`confirm_execution_shutdown` 只留给没有回执的中断路径。设计第 5 节“只有全空才……写回执”改为“回执如实写明 native 是否已空；整条边界停止之前，服务不导入任何回执”。
9. **测试支持的默认不变。** `tests/python/support.py` 的默认路径仍是“接收后由测试给出停止证据并结算”，现有一百多处用法不动；只有 E 自己的用例用拆开的 helper 去看 ACK 之后的窗口。不往产品里加暂停点或测试钩子。

## 3. 存储与升级

- 接收表 `attempt_received_receipts`：`attempt_id` 是主键与 attempts 的外键，只存回执的 sha256；固定路径由 attempt 推出，不存路径、报告或时间戳。新建与升级共用同一份 DDL；schema 28 不可能有这种记录，升级建空表。安装核验只把它加进预期的表集合：有接收行就意味着有未结束的 attempt，空闲门会先拒绝，不另查表空。宏任务删除不加它：守卫拒绝未结束的 attempt，残留行由外键报错。
- `result-delivery.json` 退役：删写入、读取与常量。基线 `fd9f04e8` 只写 `{"attemptId","accepted"}`，接受与拒绝都删回执，所以样板只需要一份已接受的 delivery 存量；清理按固定路径删，不看内容。清理并进 `cleanup_legacy_layout`，时机与失败语义照 S2：安装核验之后删，失败写进安装回复，不回滚。空闲看板上新旧两版都没人读这些文件，留下也无害。
- 回执、封存与按摘要绑定的内容不改写。

## 4. 测试

设计第 8 节阶段 E 的崩溃时机各一条，用一个小 helper 断言“只结算一次”的四件事：完成事件只有一次；接收行从 1 到 0；`finalizing` 期间名额与 claims 仍占着；结算后释放。

- 回执已落盘、执行在提交前崩溃：恢复从固定路径核对同一份回执，走同一条接收与结算，只结算一次。
- 接收已提交、回答丢失：同摘要重送得到同一种已接收回答，接收行仍只有一行，重启不丢 `finalizing`。
- ACK 之后执行正常退出或先崩：不写 delivery、不删回执；根未停时不导入；根停后只靠接收行与原回执就能结算；正常退出不记“连接未知”。
- 强停时还有残留成员或观察未知：不释放、不启动冲突工作、不回收检出；全空后才结算。
- 最终事务提交前后崩溃：提交前全部回滚、重试能完成；提交后恢复不重建接收行、不重发完成事实。
- ACK 之后最终导入遇到确定性错误：当场按 failed 加 `receiptRejection` 结清一次。
- 名额与 claims：一条用例同时看 `UNRESOLVED_SQL` 与 resource_claims，不按五种资源各测一遍。
- 提醒不发给已接收的 attempt；停止来源：`unknown` 不被后来的普通停止替换，收尾升级为立即停止仍允许。

不写测 SQLite 原子性本身的用例，不按 harness 重测原生停止次序（`HaltOwnedGroupTests` 已有），不为升级拒绝时数据不变另写用例（现有升级测试已覆盖）。

每个微任务只跑它受影响的测试文件；完整检查由 Host 在整合之后跑一次。

## 5. Windows 上的证据

与 D2 的复跑合成一次，在最终源码上由 Host 另派 Worker 做（测试机眼下关机，等开发者通知）。E 只加三项：正常回合 ACK → 执行退出 → 按原名得到错误 2 且根身份缺席 → 只结算一次；接收之后、执行退出之前杀掉服务，新服务接回后只结算一次；强停时还有残留成员就不释放。初稿里 Codex 版本发现与 native 残留的实验已有证据（a3、桌面实验与 shim 的复跑），28 → 29 的升级由 CI 的 Windows 任务覆盖，桌面操作与 D2 计划第 5 节相同，都不再单做。Windows ARM64 目前没有 CI 任务，归发版前的 Windows 工作。

## 6. 文档

随 E2a 改：设计第 5 节第 1 条的措辞（见第 2 节第 8 条）、`docs/reference/architecture.md` 关于“相同报告按已提交回答”的那句、operations 与 workers 里“收到即释放”和 ACK 之后删回执的说法、ADR-031 第 1、7 条的落实情况。由 Host 写。

## 7. 待开发者定

- native 停止永远证明不了时，任务停在 `finalizing`（界面上算进行中），不像今天那样显示为失败并待核对；名额与 claims 两种做法都一样保留。开发者 2026-10-25 定：不单设状态；改为一条一般的做法，排在首次发版之后：控制台显示执行当前的问题，Host 也能得知（[待办](../backlog.md)“控制台”一节）。
- E 没有用 Lead，由 Host 直接委派 E2a 与 E2b。
