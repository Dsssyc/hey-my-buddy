# 有界保存方案的独立审查

2026-10-16，检出 `bdb27fee`，审查对象是同目录的 `bounded-history-design.md`。依据是本检出的源码、AGENTS.md 的 Invariants 与积压事项里“数据库的八成是命令日志与心跳事件”一条。只读源码：没有运行检查套件、服务或 harness，没有碰日常看板，所以文中关于体积的判断都是从代码推出的，没有测量。源码引用从 `src/hey_my_buddy/` 起算；`S` = `blackboard/store/store.py`，`W` = `blackboard/tasks/workflow.py`，`V` = `blackboard/service/service.py`。

结论：方案对现状的调查大体准确，活动部分基本站得住；命令部分多了一层。命令回答之所以大，是因为回答里嵌着一份微任务视图或清理计划视图，而这些视图都能从当前表里重新读出，验收与写结论的重放本来就会用当前视图覆盖存下的那份。所以收据在写入时只存提交身份、重放时补当前视图，一个机制就够：不需要保留窗口、`response_form` 列、`COMMAND_ALREADY_APPLIED`、控制台改动，也不需要命令侧的定期维护，takeover 与 applying 两个例外也就不存在了。另有一处方案没看到的冲突：服务启动后立即收简，会让以后每一次改契约的安装都在校验时失败（B5）。

## A. 每一层是否必要

A1 普通命令 24 小时窗口、过窗转摘要、`response_form` 列。守的事情真实存在：收据没有期限，`S:271-294` 比对身份后原样返回，且多数操作先读收据再检查状态（方案表中各行的引用我抽查了 accept、conclude、continue、takeover、cleanup 几类，属实）。但体积的来源是回答里的视图：accept 存 `{**self.compact(...), verdict, integrationId, integration, ...}`（`W:4392-4398`），conclude 同样（`W:4706-4717`），continue（`W:3116-3123`）、takeover（`W:3366-3376`）都以 `compact` 打头，cleanup_plan 与 cleanup_apply 存 `compact` 加 `_plan_view`（`W:5596-5597,5781-5783`），而 `_plan_view` 只是计划行的投影（`W:601-621`），`compact` 带有截断后的请求、子任务与交付物列表（`W:700-720`）。accept 与 conclude 的重放在返回前已经 `response.update(self.compact(...))`（`W:4001-4006,4557-4562`），存下的视图从写入那一刻起就不会被返回。更小的做法：写入时只存不能从表里读出的提交身份，重放时补上当前视图。这样存储只有一种形式，升级把存量转换一次，就不需要形式标签（AGENTS.md 只在两种形式并存时要求行上写明）。代价：continue、takeover、cleanup 的重放视图会从“提交时刻的”变为“当前的”，与 accept、conclude 一致；每个 kind 都要改写入和重放两处，并有对应的测试。

A2 `harness.prepare` 例外与升级补 `attempt_id`。例外守的事已经被入口守住：读事务先查 `finished`/`cancel_requested_at` 并给 `ATTEMPT_FINISHED`，然后才读收据（`V:563-574`），执行结束后这类收据就不会再被返回；它的回答是一条健康记录（`V:589`），不在 Host 测得的四类大回答里。升级时根据 subject 解析 attempt 并补 `attempt_id` 这一层没有必要：改契约的安装要求所有执行都已 finished 且 `shutdown_confirmed=1`，否则以 `UPGRADE_NOT_IDLE` 拒绝（`install/upgrade.py:78-81`），所以升级时的每一条 prepare 存量都已经失效，可以不问归属整类处理（不动或删掉）。以后写入时带上 `attempt_id`（`V:600` 加一个参数）代价很小，可以留；解析 subject 的那一步应当删掉。

A3 `execution.result` 例外。守的事真实：执行端会无限期重投（`buddy/runtime/execution.py:687-707`，`buddy/runtime/receipts.py:25-57`）。但这一类的 commands 行只写不读：`result_json` 一旦非空，就先走同值比较并返回（`S:2054-2077`）；只有 `result_json` 为空时才读收据（`S:2087-2091`），而收据和 `result_json` 是在同一个事务里写的（`S:2279-2283`），源码中也没有任何地方把 `result_json` 置回 NULL。命令编号固定为 `result:<attemptId>`（`buddy/runtime/execution.py:702`），恢复路径也沿用这个编号（`blackboard/tasks/interruption.py:226-229`）。方案打算“已有权威 result 后立即收简成摘要”，仍然要留一行永远读不到的墓碑。更小的做法：这一类不再写收据，升级时删除存量。积压事项 10-10 的测量中有 1,190 次交付结果、平均 33 KB，正是这一类。

A4 `workflow.takeover` 例外（完整回答保留到 generation 被替代）。重放授权真实存在（`W:3316-3324,3380-3419`）。不过控制凭据是推导出来的：`self.db.control_token(run_id, generation)`（`W:3370`，校验时 `W:3415` 也这样重算），回答的其余部分是 `compact`。只要存下 `ownerGeneration`、`control.hostId` 与 `previousOwnerGeneration`，就能完成原来的重放授权并重建全部回答，例外随之消失。要注意 `_authorize_takeover_replay` 读的是收据顶层的 `ownerGeneration`（`W:3392`），这个字段现在来自嵌入的 `compact`（`W:714`），收窄时它必须作为身份字段显式保存，否则重放会一律变成 `STALE_GENERATION`。方案的摘要字段里列了 issued generation/Host，在这一点上没有错。

A5 `workflow.cleanup_plan` 在 applying 期间保留完整回答。这一层没有必要。cleanup_plan 与 cleanup_apply 不在服务的命名接口上，服务只暴露 `workflow.reclaim`（`V:759-763`）；编号都在内部生成：reclaim 用 uuid（`W:4778`），已验收工作区的维护用 uuid（`blackboard/tasks/storage.py:935`），只有 storage.apply 用自己的编号派生（`storage.py:851-854`）。前两种在调用返回后不可能再被重放。中断后的恢复依据的是计划表的 applying 状态（`W:5662,5794-5799`），cleanup_plan 自己也有一条按计划表给重复回答的路径（`W:5525-5530`）。整个回答都能由计划行加当前视图重建。

A6 新错误 `COMMAND_ALREADY_APPLIED` 与控制台改动。只因为 A1 的摘要存在才需要；按 C1 收窄后，重放仍然给出完整形状的回答，这一层连同控制台对 `macro-task-stop.ts`、`use-editor.ts` 的改动都可以删掉。即使保留方案的窗口，控制台发出的两类回答本来就小：`user_policy.publish` 方案自己写了只有 published/counts，`macro_task.stop` 只有 macroTaskId 和每个 run 的 state/stopped（`W:3499-3505`）。把这两类排除在收简之外，控制台一行不改，节省的体积几乎不变。

A7 `events`/`watch` 每个响应都带保留策略说明。没有必要。这是一个不变的常量，放进 `docs/reference/cli.md`、`workers.md`、`architecture.md` 就够了。游标按 `seq > after` 读（`S:186-196`），本来就跳过空洞；`events.seq` 是 `AUTOINCREMENT`（`blackboard/store/db.py:223`），删除后也不会复用序号。在每个响应里加一个固定字段只会让交互面变大。

A8 每次执行只留最后一条 `attempt.activity`。做法可以接受，但方案给的理由不成立。方案说“全部删除会让 head 回退”，可是在它自己的资格条件下（微任务已验收或已写结论），`workflow.acknowledged` 或结论事件写在同一个微任务所有活动之后（例如 `W:4375-4390`），全局和按任务的 `MAX(seq)` 都不会因为删掉活动而下降；“最后观察”由 `attempt_activity` 投影提供（`S:789-829`），不读事件。全部删除只是一条更简单的 DELETE，不需要 MAX 子查询。保留末条每次执行约 327 字节，留不留由 Host 定，属于小事。

A9 活动收简的资格要求 `finished` 加 `shutdown_confirmed=1`，并在锁内重新核对已停止。活动事件不是停止证据：停止证据在 attempts 的列和 `attempt_shutdown_evidence`（`S:2059-2060`）里，生产代码中没有任何地方用活动事件推断停止（读者核对见 B1）。删除活动并不断言什么，所以“微任务 accepted/concluded”一个条件就够了；执行状态条件和锁内重验守的是一个不存在的风险。另外，`execution_progress` 对已 finished 的执行直接拒绝（`S:1846-1847`），执行结束后也不会再有新活动穿插进来。

A10 维护的节奏、事务，以及升级里再跑一遍。复用 `_maintain` 线程（`blackboard/service/daemon.py:162-171`）、短写事务、错误进入已有的持久化错误（同处 `persistence_error` 的写法），这几点站得住。多余的有两处。一是“启动后也做一次”，它和安装校验冲突，见 B5。二是升级时按同一规则把活动也收简一遍：删除活动行不是存储形式的变化，交给运行期同一个函数就够了；方案这样做，大概是为了让升级后的 expected 快照与启动后一致，但正确的修法是不在启动时立即改旧行（见 B5）。60 秒的独立节拍可以并入现有的 15 秒恢复节拍，小事。

A11 schema 24 的显式升级与安装器接线。站得住。AGENTS.md 规定存储形式的变化在显式升级里做，`idle_snapshot` 只接受当前 schema（`install/upgrade.py:58-64`），所以从 23 基点出发的接线是必须的，不是防御。按 C1 收窄后升级的内容会变少：不加列，只把几类大回答的存量剥成身份形式，删除 `execution.result` 和 `harness.prepare` 的存量。

## B. 有没有漏掉的

B1 活动明细的读者。我按代码核对了一遍，方案的结论成立。`attempt.activity` 只有一个生产点（`S:1875`）。生产代码里读 events 的地方都按 kind 过滤，或者只取 seq：`blackboard/tasks/micro_task.py:118-135`、`blackboard/tasks/cancellation.py:12-16`、`W:5896`、`W:6756`（seq 来自唤醒集合）、`W:6786`、`blackboard/tasks/macro_tasks.py:783`（只取 `HOST_MARKERS`）、`blackboard/routing/router_boundary_data.py:34`、`blackboard/routing/router_history.py:156,241`、`blackboard/routing/decision.py:1562`；时间轴的执行片段读的是 attempts 与 turns（`macro_tasks.py:645-660`）。不过滤 kind 的只有通用的 `events.read/wait`（`S:176-245`）、备份与升级的指纹（B5），以及下面 B2 的引用扫描。

B2 存储清理的引用扫描是删除路径上的读者。`_workspace_reference_rows` 把 `events` 和 `commands` 的每一行都当作可能的 allocation 引用来扫描（`blackboard/tasks/storage.py:236-253`），没有引用的 allocation 会被当作孤立工作区删除（`storage.py:845-850`）。方案只写了一句“验证移除冗余快照中的路径不会移除唯一的 allocation 保留依据”，没有指出这些路径的权威副本在哪张表。C1 从 commands 里剥掉视图、活动收简删掉活动行，都会撞上同一个问题。实施前应当先确认：`compact` 和 `_plan_view` 里出现的每一种路径，在 `workflow_runs`、`workspace_reservations`、`workspace_cleanup_plans`、`workflow_integrations` 中都有对应的行。这一点我没有核实完。它改动的是删除路径的守卫，按开发者的约定应当另请独立审查。

B3 方案没列到的重放：Host 隔很久才重放。`wait` 给 Host 的建议命令带有确定的编号，例如 `<runId>:continue:<revision>`、`<runId>:approve:<requestId>`（`cli/blocking.py:395,409,425,432`）。Host 会话中途断掉（额度用尽、窗口关闭）几小时乃至一天以上，再照原文发同一条命令，是现实中会发生的合法重放。今天这样的重放会得到原回答；按方案，过了 24 小时得到的是 `COMMAND_ALREADY_APPLIED`。方案自己也承认 24 小时“不能从现有代码推出”，这个例子说明它确实没有依据。按 C1 收窄就不需要窗口了。

B4 方案没列到的重放：storage.apply 续做。storage.apply 有自己的文件收据，可以在中断后没有期限地续做（`storage.py:777-799`），续做时按派生编号再次调用 cleanup_plan 与 cleanup_apply（`storage.py:851-854`）。按方案，过了窗口这两次调用会得到 `COMMAND_ALREADY_APPLIED`，被记成 skipped，理由是这个错误码（`storage.py:879-886`），而工作区可能还在。不会丢数据，但给出的理由会误导人。方案在 cleanup_plan 那一行只提到存储调用，没有说到这条路径。按 C1 收窄，重放会按计划表给出当前计划，续做照常进行。

B5 升级、安装校验与回滚。每次改契约的安装，都会在新服务启动后用 `idle_snapshot(event_head=...)` 对所有表重新计算指纹，并与停机时的 expected 比较；包括 `commands`，以及 `events` 中 `seq <= eventHead` 的行，没有豁免（`install/upgrade.py:297-313`，`blackboard/store/backup.py:160-222`）。方案的维护会在启动后立刻运行，并按 `created_at` 把过窗的命令转成摘要。这样一来，以后任何一次改契约的安装，只要库里有一条命令在停机与校验之间跨过了窗口，或者还剩一个已关闭但没来得及收简的微任务，校验就会报 `UPGRADE_VERIFY_FAILED` 并回滚。方案只处理了 23→24 这一次的 expected 快照，没有处理以后的安装。我是从代码读出这一点的，没有复现。修法：维护的第一次运行不放在启动时，或者按 C1 去掉按时间触发的命令转换。回滚本身是一致的：失败时恢复升级前的备份（`install/upgrade.py:560-577`），备份里是完整的 23 形式，转换在原库上不可逆，但有备份兜底，这一点没有问题。

B6 方案的命令表里，task.*、message.post、decide、scope_amend、workspace_resolve 几行的行号，我没有逐条核对，只抽查了 A 中引用到的那些行。

## C. 我会怎样收窄

C1 删掉“窗口加摘要”整层（`response_form` 列、24 小时窗口、命令侧维护、`COMMAND_ALREADY_APPLIED`、控制台改动、takeover 与 applying 两个例外），改为一条一般规则：收据只存不能从表里读出的提交身份，重放时从当前表重建视图。按 kind 列出要存的字段：accept 存 verdict、targetRunId、integrationId、integration 与 resolutionDelivery；conclude 存 verdict、conclusion 与 partialArtifactId；continue 存 continuationId、inputSha256、helperPolicy 与 targetRunId；takeover 存 ownerGeneration、hostId 与 previousOwnerGeneration；cleanup 两类存 planId，apply 另存 removed。小回答的几类（task.*、message.post、quota.redetect、user_policy.publish、macro_task.stop）不变。代价：几种重放的视图变为当前视图；逐 kind 改写入与重放；升级把存量剥成身份形式。收益：四类大回答在写入时就变小，没有滚动的“最近一天约 2.4 MB”；对 Host 与控制台不改接口。它和积压事项的原话（“只在重放还可能发生的时间里完整保存，之后只留编号与摘要”）方向一致，手段不同，需要由 Host 交开发者确认。

C2 `execution.result` 不再写收据，升级时删除存量（A3）。`harness.prepare` 不参与收简；如果要清，升级时整类删除存量，因为那时所有执行都已停止（A2）。删掉“解析 subject 补 attempt_id”这一步。

C3 删掉 events/watch 响应里的保留策略说明，只改参考文档（A7）。

C4 活动收简的资格只看微任务是否 accepted/concluded（A9），保不保留末条由 Host 定（A8）。如果核实了验收和写结论时该微任务的执行都已经 finished（`S:1846-1847` 拒绝 finished 之后的 progress；我没有核实验收的前置条件），可以直接在 accept/conclude 写入的事务里删除，连定期维护、启动时运行和 B5 的冲突一起省掉；没有核实之前，就保留定期维护，但第一次运行不放在启动时。

C5 升级只做命令存量的形式转换，活动存量交给运行期（A10）。B2 的引用扫描在两种做法下都要先核对清楚，再动手删除任何东西。
