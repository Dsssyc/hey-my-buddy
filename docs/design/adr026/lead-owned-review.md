# Lead 名下凭据的独立审查

审查对象是 `git diff 55be5ebe a2fcd488 -- src tests`，对照 ADR-026（第 1、4、5、8 条与 2026-10-14 的修改）、ADR-030、AGENTS.md 的 Invariants 与 CONTEXT.md。只读了代码与文档，没有运行任何检查，下面每条的“测试做法”是建议的复现方式，不是已经跑过的结果。实现方的说明是在清点做完之后才读的。

结论：凭据在黑板上的目标绑定守住了，没有找到让带委派权的凭据读到、等到、改到或停到 Host 自己的、别的 Lead 名下的、Router 内部执行或它自己的调用；越界的是一条交给 harness 进程的环境，它没有按要求只给 Lead。过度或多余的机制有四处，其中一处有功能上的代价。

## A. 权限的边界

**A1（中）启动器环境给了每一个 Worker，而不只是 Lead。** `src/hey_my_buddy/buddy/harnesses/runtime_selection.py:59` 的 `worker_launcher_environment` 只看 `HMB_AGENT_CREDENTIAL_FILE` 在不在，在就把 `HMB_STATE_DIR`、`HMB_AGENT_CREDENTIAL_FILE`、`HMB_RUNTIME_ROOT`、`HMB_RUNTIME`、`HMB_RUNTIME_IDENTITY`（以及测试里的 `HMB_DEV_SOURCE`）交给 harness 进程；四处调用在 `claude/native_run.py:293`、`codex/native_run.py:430`、`zcode/native_run.py:730`、`dsh/acp/launch.py:127`。而凭据文件由 `src/hey_my_buddy/buddy/roles/turn_io.py:128-132` 为每一个带执行凭据的回合写出，也就是所有微任务的 Worker，不分有没有委派权。改动之前 `discovery.native_environment` 会把这些 `HMB_*` 全部清掉，harness 进程拿不到。后果：普通 Worker 的 harness 进程从此知道黑板的状态目录和自己的凭据文件，回合里的启动器能连上那个黑板。按凭据计算的权限没有变大（普通凭据在服务端仍只有 `AGENT_OPERATIONS`，见 `workflow.py:934`），但这超出了要求第 5 条“在 Lead 的回合里”与 ADR-026 第 8 条“Lead……像 Host 一样经启动器操作”，也在 ADR-030 那张清单之外多给了每个普通 Worker 一条进入黑板的通道；普通 Worker 与 Host 的往来本来走 buddy 工具（`buddy/roles/worker_services.py`），用不上启动器。回合的输入里已经有 `context.delegationAuthority`，控制器据此只在 Lead 的回合里写入这几个变量即可。测试做法：在 `tests/python/blackboard/tasks/test_workflow_worker.py` 里照 Lead 的那条用例，换成一个没有委派权的微任务，让启动器子进程打印它看到的 `HMB_STATE_DIR` 与 `HMB_AGENT_CREDENTIAL_FILE`，断言两者都不存在；或者在 `tests/python/buddy/harnesses/test_harness_discovery.py` 里给 `worker_launcher_environment` 一个普通回合的环境，断言返回空。今天这两种写法都会失败。

**已核对、没有发现越界的路径。** 下面这些逐一读过处理函数，说明凭据的边界在哪里落实，供另一位审查者对照。没有委派权的凭据：`authorize_agent_operation` 只在 `delegationAuthority` 为真时并入 `LEAD_OPERATIONS`（`workflow.py:933-939`），`delegationAuthority` 在 `resolve_credential` 里每次从 `workflow_runs` 读出（`workflow.py:905-929`），而 `workflow_runs.delegation_authority` 只在插入时写一次；CLI 去掉的两处客户端检查（`cli/main.py` 的 `_apply_control` 与提交令牌）只是客户端的重复，服务端照样拒绝。选择子：`runId`、`taskId`、`targetRunId` 与 `requestId`、`attemptId`、`messageId` 全部解析并逐个绑定（`workflow.py:942-965`），一个合法的选择子不再掩盖另一个越界的；各处理函数读目标用的正是这些键（`store.py:466` 的 `_task_row` 取 `taskId or runId`，`events_read`/`events_wait` 取 `taskId or runId`，`artifact_list` 优先 `attemptId`，`message_get` 用 `messageId` 或按任务限定的 `inquiryId`，`message_wait` 与 `store_task_wait` 再经 `message_get`/`task_get`），没有不经过绑定的别名。这顺带堵住了基点上的一个旧漏洞：基点只取第一个出现的选择子，`artifact_list {runId: 自己, attemptId: 别人的}` 会列出别人的产物。不针对单个目标的操作：`task_list` 的归属条件在 SQL 里，计数与分页都在同一个 `WHERE` 下（`store.py:1016-1020`）；`events_wait`、`message_wait`、`task_wait` 没有目标时直接拒绝（`workflow.py:970-971`），`events_read`、`message_list` 没有目标时落到自己的微任务（`workflow.py:972`）。Lead 自己：新增操作对自己一律拒绝，因为 `workflow_lead_tasks` 的 `CHECK (owned_run_id <> lead_run_id)` 使它不可能在自己名下。Router 内部执行与协助任务：都不在 `workflow_lead_tasks` 里，任何选择子指到它们都被拒绝。提交：`hostId`、`owner` 与宏任务选择子被拒绝，`hostId` 由凭据推出为 `lead:<runId>`，`delegationAuthority` 为真被拒绝（`workflow.py:1643-1652`），宏任务取 Lead 的（`workflow.py:1547-1548`），归属与插入同一事务（`workflow.py:1572-1578`）。撤销与换手：凭据在回合结算、取消与释放时撤销（`workflow.py:3631`、`3694`、`6556`、`6591`、`6626`），每个变更操作在写事务里经 `_authorize` 再核对凭据仍有效（`workflow.py:866-872`），提交在 `_insert_run` 里核对（`workflow.py:1545-1546`），验收之后的回收经 `cleanup_plan`/`cleanup_apply` 在写事务里再授权一次；归属记在微任务上而不是回合上，换手后新回合的凭据照常管理原有名下微任务。命令回执按 `commandId` 查找，但请求摘要里含已绑定的 `runId`，不能借重用 `commandId` 读出别人的回执。

**不计入的前提。** 执行凭据的范围是能力约束，不是操作系统隔离，`capabilities` 的 `limitations.osIsolation` 与 `agentScope` 已如实写明这一点（`service/service.py:379-388`）：服务令牌在状态目录的端点记录里，Host 的控制文件在 `<状态目录>/controls/`，同一用户的进程读得到它们就能不带凭据地以服务身份调用。ADR-026 第 8 条让 Lead 在放开一档、没有系统沙盒的情况下运行，A1 又把状态目录交到 harness 进程手上，所以第 5 条“由黑板机械地执行”只对按凭据调用的请求成立，对一个主动丢掉凭据的 Lead 不成立。这不是本次差异引入的，也不是靠改这批代码能解决的，记在这里供 Host 判断第 2 条授予前的说明是否要写明。

## B. 过度防御与多余的机制

### 站得住的

`authorize_agent_operation` 的操作集合与逐选择子绑定（`workflow.py:931-972`）守在服务入口这一个边界上，是唯一的一次检查，站得住。

`_authorize` 写事务里的 `_active_credential`（`workflow.py:871`）与 `_insert_run` 里的同一核对（`workflow.py:1546`）站得住：请求开始时的凭据解析与事务提交之间隔着 Git 准备和检出工作，回合可能在这段时间里结束，这是提交点上的围栏，不是重复。

提交时拒绝 `hostId`、`owner`、宏任务选择子与 `delegationAuthority`，站得住：这些都是外来输入，在边界上拒绝比静默覆盖清楚。

提交遇到已有 `requestId` 时的归属核对（`workflow.py:1666-1673`）与并发分支里的同一条件（`workflow.py:1744-1750`）站得住。初看它与请求指纹重复（指纹里含 `hostId='lead:<runId>'`），但 `hostId` 是 Host 自己可以填的标签，Host 用 `lead:<runId>` 提交时指纹会一致，`test_lead_owned.py:162` 正是这种情形；在那里归属才是权威，没有这条 Lead 可以凭相同的提交令牌取回那个微任务的控制三元组。更简单的另一种写法是在 Host 提交时保留 `lead:` 前缀，但那是另一条规则，不比现在简单。

`task_list` 在 SQL 里限定归属，站得住：它是唯一不针对单个目标、又会返回多行的新增读取，过滤必须在 `LIMIT` 和计数之前。

单独一张 `workflow_lead_tasks`、四个字段、外键与 `CHECK`，站得住，符合要求第 1 条与“有结构的记录有自己的表”；`workflow.lead_task_created` 事件让等待方能看到归属建立，不算多余。`blocking._find_run` 从分页扫描列表改为按 `requestId` 直接读取，是更简单的写法，也让带凭据的调用不需要列表权限。

### 过度的与更简单的写法

**B1（有功能代价）名下微任务的协助任务被额外绑定，重复了处理函数里已有的归属检查。** `workflow.py:944-945` 为 `workflow_decide` 把 `requestId` 改解析到 `workflow_requests`，`workflow.py:955-956` 再把代理请求的 `child_task_id`（名下微任务的协助任务）加为必须在 Lead 名下的目标；`targetRunId` 也同样要求在 `workflow_lead_tasks` 里（`workflow.py:942`）。可是处理函数本来就把这两样绑到受控的那一个微任务上：`_request_row(run_id, request_id)`（`workflow.py:256-262`）要求请求属于已经核对过的 `runId`，`_attention_chain` 沿 `workflow_children` 重新走一遍代理链，`_target_run`（`workflow.py:2708-2724`）要求 `targetRunId` 是 `runId` 的协助任务，所有接受 `targetRunId` 的操作都经过它。额外的绑定没有多守住什么，却让 Lead 不能回答名下微任务的协助任务代理上来的 `helper-attention` 边界，也不能对这些协助任务做续接、验收、写结论、回收、修订范围与解决工作区冲突；而 Lead 自己可以用 `workflow_decide` 批准这些协助任务（`test_lead_owned.py:253` 就是 Lead 批准之后再被拒绝）。后果是名下微任务一旦有协助任务请求关注，Lead 就卡在这个边界上，只剩 Host 经控制台处理。更简单的写法：`workflow_decide` 不对 `requestId` 做选择子解析，只绑定 `runId`；`targetRunId` 不进 `targets`，交给 `_target_run`。这需要 Host 定一件事：名下微任务的协助任务算不算 Lead 管的范围。若不算，那么同样应该不让 Lead 批准协助任务，现在是能批准、不能管。测试做法：把 `test_lead_owned.py:253` 的期望改为 Lead 能 `decline` 这个代理请求，并补一条 Lead 以 `{runId: 名下微任务, targetRunId: 它的协助任务}` 写结论的用例；再保留 `test_lead_owned.py:110` 里 `{runId: 名下微任务, targetRunId: Host 的协助任务}` 的用例，确认 `_target_run` 照样拒绝。

**B2 新的停止发起者 `lead` 与 `host` 完全同义。** `db.py:128` 的 `CHECK` 增加了 `'lead'`，随后在 `workflow.py:3606`、`3609`、`buddy/runtime/worker.py:981`、`buddy/roles/controller.py:107`、`135`、`buddy/roles/run_execution.py:744` 六处都写成 `in ("host", "lead")`，没有任何一处对两者区别对待。同一个含义有了两个存储值，与“存储没有歧义”的取向相反。谁停的已经由动作者 `lead:<runId>` 记在取消记录里。它还带来一个显示缺陷：控制台 `apps/console/src/task-activity.tsx:146-166` 不认识 `lead`，动作者又不以 `host:` 开头，于是 Lead 停下的微任务显示为“停止（发起者未知）”；`apps/console/src/types.ts:211`、`248` 的类型也没有 `lead`。更简单的写法：Lead 对名下微任务行使的是 Host 的操作，发起者记 `host`，`CHECK` 与六处判断都不用改；若 Host 认为要在发起者上区分 Lead，就要同时改控制台，而不是只改存储。测试做法：在 `apps/console/src/task-activity.test.tsx` 里给一个 `stopInitiator: "lead"`、`cancellation.actor: "lead:<id>"` 的任务，断言标签不是“停止（发起者未知）”；今天会失败。

**B3 `agent_credentials.scopes_json` 只写不读。** `workflow.py:6342` 把 Lead 的操作也写进 `scopes_json`，但授权从不读它，实际依据是每次从 `workflow_runs` 读出的 `delegation_authority`；全仓只有这一处写入。这是基点就有的死字段，本次又扩展了它的写入，形成两处说法。更简单的写法：在待定的 19→20 升级里去掉这一列，或者反过来让授权读它；只保留一个来源。`test_lead_owned.py:96` 末尾对 `scopes_json` 的断言随之删除或改为对授权的断言。

**B4 读阶段的凭据复核是重复的。** `_authorize` 在大多数操作里先在读事务里调用一次、再在写事务里调用一次（例如 `workflow.py:4328` 与 `4358`），两次都会进入 `_active_credential`；读阶段那一次离请求开始的解析很近，起不到提交围栏的作用。代价很小，不值得为它单开改动；若以后动 `_authorize`，可以只在写事务那一次核对。

**顺带（不是过度防御，是范围之内的小缺陷）。** 其一，`targets` 里的 `runId`、`taskId`、`targetRunId` 没有类型检查（`workflow.py:942`），Lead 传一个对象或数组时会在 `workflow.py:959` 的 SQL 绑定里抛出，返回 `INTERNAL_ERROR` 而不是 `INVALID_ARGUMENT`；不构成绕过，但边界上的校验应当在这里一次做完。其二，`capabilities` 的 `limitations.agentScope`（`service/service.py:383-388`）仍写着执行凭据“不能提交任务、批准、取消……不能寻址别的微任务”，对 Lead 的凭据已不成立，交互面上的说明与行为矛盾。

## 对照实现方的说明

说明与代码在事实上大体一致：表结构、事务、操作清单、逐选择子绑定、`task_list` 的 SQL 限定、提交字段、撤销围栏、`get` 与列表的投影、`wrap_up` 资源都与代码相符，新增测试的个数（`test_lead_owned.py` 12 个、`test_lead_workspace.py` 3 个）也相符。不符或没有说出的地方有四处。第一，说明的结尾写“与本微任务要求不一致……的情形：没有”，但它在前文自己写了启动器环境给的是“四种 harness 的原生子进程”，即所有 Worker，这与要求第 5 条“在 Lead 的回合里”不一致（A1）。第二，说明称代理请求绑定 `child_task_id`“因此不能借名下微任务的代理请求操作协助任务”，没有说出代价：Lead 能批准协助任务，却不能回答它们代理上来的边界，也不能对它们做续接、验收与写结论（B1）。第三，说明称“没有修改前端，因此没有运行前端单元检查”，但新增的存储值 `stop_initiator='lead'` 会到达控制台，并在那里显示为发起者未知（B2）。第四，说明没有提到 `capabilities` 里关于执行凭据的说明已与 Lead 的行为不符。
