# 命令回答与执行活动事件的有界保存方案

2026-10-16，调查基点 `c605b127`（0.41.0，schema 23）；状态为待 Host 审阅的方案，尚未实施。依据是本检出的源码、AGENTS.md 的 Invariants、[架构](../reference/architecture.md)、[ADR-007](../decisions/007-neutral-core-and-single-current-contract.md)、[积压事项](backlog.md)及[计划第 36、37 项](router-planes-and-lead-plan.md)。本文未访问日常看板，容量数字来自 Host 提供的三天只读测量；未运行服务、检查运行器或原生 CLI，也未观察任何已安装 harness 的版本与行为。

建议只增加一处由服务执行的历史收简：命令行保留身份、请求哈希与显式的回答形式，完整回答在其保留窗口结束后换成固定字段的提交摘要；微任务验收或写结论且执行确认停止后，每次执行只留最后一条原样的 `attempt.activity`。当前时间轴并不读取这些活动明细，无须新增活动摘要表、摘要事件或时间轴的另一种读法。命令的现有重放契约多数没有自然到期点，下面明确提出普通命令完整回答保留 24 小时的新契约，这个时长需要开发者决定，不能从现有代码推出。

## 命令：现有重放条件逐类核对

下表的文件缩写是 `S` = `src/hey_my_buddy/blackboard/store/store.py`，`W` = `src/hey_my_buddy/blackboard/tasks/workflow.py`，`V` = `src/hey_my_buddy/blackboard/service/service.py`，`E` = `src/hey_my_buddy/blackboard/evaluation/evaluation.py`。本文其余以 `blackboard/`、`buddy/`、`protocol/`、`cli/`、`console/`、`install/` 开头的源码引用均从 `src/hey_my_buddy/` 起算。行号均指调查基点；表中“普通窗口”是后文建议的新规则，不是当前行为。

`commands` 当前定义见 `src/hey_my_buddy/blackboard/store/db.py:251-261`：全局唯一 `command_id`、任务与执行标识、kind、请求哈希、完整回答 JSON、subject 和写入时间，没有期限、形式标签或清理。统一读取 `S:271-294` 先比 subject（调用者提供时），再比 kind 与请求哈希，之后直接返回 JSON；同编号不同请求为 `CONFLICT`，不同执行身份为 `UNAUTHORIZED`。统一写入在 `S:296-323`，与业务写入同一事务。回答用于避免丢包、服务重启或调用方重试重复产生业务副作用；`created_at` 现在不是重放期限。

“同一个回答”在当前实现中指同一提交事实和已记录的业务结果，并非所有接口逐字返回原响应。多数 workflow 操作给重复回答加 `duplicate:true`；`workflow.accept`、`workflow.conclude` 还在返回前重新叠加当前微任务视图，重放不再做本微任务的删除（`W:3993-4006,4553-4562`）。`execution.result` 也从当前执行与任务重新生成确认。这些差异不能作为重复执行业务写入的理由。

| kind | 谁发、回答保护的副作用 | 当前合法重放窗口及代码依据 | 建议收简后的重放 |
| --- | --- | --- | --- |
| `task.submit` | 普通执行提交的 store 接口可传 `command_id`；回答固定首次接纳的任务身份，避免重复排队。生产 named API 直接调用默认参数的 store 方法，没有传这个关键字（`V:845-846`），当前生产提交主要靠 `requestId` 去重。 | `S:849-880,934-935`：已有任务与微任务提交资格检查后查收据；无时间截止。即使任务终结，同 requestId 与输入仍可取已有任务，不能从任务终态推出命令失效。 | 对实际存在的这类收据用普通窗口；摘要保留 taskId，之后明确报告已经提交，用原 requestId 查询；不重新创建任务。 |
| `task.cancel` | 普通执行取消的 store 接口可传编号，避免重复取消与重复事件；named API 不传该关键字（`V:857-858`），服务停机也不传（`blackboard/service/daemon.py:217,230`）。 | `S:1092-1101,1136-1139`：收据先于任务控制与终态检查；已有收据无时间截止，任务终态本身仍允许无副作用的重复取消。 | 普通窗口；摘要只确认取消请求已经记录，不声称进程已经停止。 |
| `task.retry` | 普通执行显式重试的 store 接口可传编号，防止同一重试排出两次执行；named API 不传（`V:860-861`）。 | `S:1174-1187,1255-1256`：收据先于当前状态检查；成功后的任务代次改变不阻止原收据重放，没有期限。 | 普通窗口；摘要指向原任务和提交时产生的执行身份（回答有该身份时），不再排队。 |
| `task.acknowledge` | 普通执行验收的 store 接口可传编号，固定原验收事实；named API 不传（`V:863-864`）。 | `S:1290-1305,1352-1355`：收据先于控制和验收资格检查；已有收据无到期点。Router 执行不能算业务验收。 | 普通窗口；摘要保留 verdict 与任务引用，实际验收仍读任务事实。 |
| `message.post` | Host 的问询入口及 store 直接调用可传编号；回答固定 message/inquiry 身份，避免重复发问；named API 不传关键字（`V:941-942`）。 | `S:2457-2485,2535-2538`：收据先于已有 inquiry 查询；此外 `(task,inquiryId)` 自身去重，消息 answered/delivered/discarded/unavailable 后仍可读回。没有时间上限。 | 普通窗口；摘要保留 messageId/inquiryId，已回答与否从 `message.get` 读取，不能重发问题。 |
| `workflow.decide` | 拥有微任务控制权的 Host 或获准的 Lead 作出帮助请求的决定；固定请求决策、帮助微任务接纳与自动续接安排。 | `W:2202-2234,2396-2401,2440-2455`：预检和写入都在授权之后、revision 与 request.state 检查之前读收据。请求关闭、下一回合开始或微任务验收都不使旧收据自然过期；失去当前控制权可能先被授权拒绝。 | 普通窗口；摘要给 requestId、已记录的 decision 与任务引用，不再准备帮助微任务或续接。 |
| `workflow.continue` | Host/获准的 Lead 继续本微任务或有权控制的后代；固定一次 continuation，避免再次运行模型。 | `W:2921-2942,2961-2967,3125-3130`：授权后、revision/续接资格检查前读收据；expectedRevision 只限制首次写入，没有限制已有收据重放的时间。 | 普通窗口；摘要指向已提交的 continuation/目标微任务，之后用 get/status 观察，不能靠换编号盲目重发输入。 |
| `workflow.takeover` | Host 或已认证的控制台换手；首次回答带新 owner generation 和新控制凭据，丢失回答的原 Host 还需要取回它。 | `W:3312-3324,3362-3375,3380-3419`：先取收据，再以原控制凭据专门验证重放；只在当前 generation 仍等于该命令颁发的 generation、Host 仍匹配时允许，下一次换手后为 `STALE_GENERATION`。这个窗口可持续超过任何固定小时数。 | 完整回答保留到颁发的 generation 被替代；之后可收简，但仍先按摘要保留的 issued generation/Host 身份执行原重放授权并返回 `STALE_GENERATION`。不按 24 小时截断取回控制凭据的路径。 |
| `workflow.cancel` | Host/获准的 Lead 或控制台取消微任务及其受控工作；编号可选，带编号时防止重复提交取消意图。 | `W:3434-3448`：授权后先读收据，再拒绝 accepted/concluded 的新取消；因此“已验收不能新取消”不等于“旧取消收据不可重放”，没有时间截止。 | 普通窗口；摘要确认当时的取消意图，停止状态独立读取，绝不把取消摘要当作停止证据。 |
| `macro_task.stop` | 已认证控制台停止一个宏任务的当时工作集合；固定这一次取消范围，重放不能把后来新建的微任务也取消。 | `W:3453-3466,3506`：要求控制台身份，收据先于重新计算集合；前端保留不确定请求的原编号供重试，且无客户端时限（`apps/console/src/macro-task-stop.ts:131-164,176-213`）。 | 普通窗口；摘要确认该次取消范围已经记录，保留 macroTaskId 与数量，不重新枚举工作集合；控制台需显示已记录并刷新实际停止状态。 |
| `workflow.accept` | Host/获准的 Lead 验收固定交付物；服务从规范化请求派生 `accept-<hash>`，没有调用方任意换一个 commandId 的空间；固定验收与整合身份。 | `W:3924-3944,3974-4006,4401-4404`：授权后读收据；同一请求在验收、回收之后仍可重放并刷新视图，无截止。重放本身不再次回收，丢失回收结果走 reclaim。 | 普通窗口；摘要保留 verdict、artifactId、integrationId 和目标身份，告诉 Host 验收已完成；回收结果从当前事实读，需要再回收时显式 reclaim。 |
| `workflow.conclude` | Host/获准的 Lead 写结论；服务从目标执行、note 等派生 `conclude-<hash>`，固定结论和独立的 Host 部分交付物。 | `W:4475-4493,4506-4515,4535-4562,4718-4719`：已有收据跳过本微任务的再次封存；终态只拒绝新的结论，无时间期限。Lead 的重复结论仍可能重试其名下未完成的回收（`W:4543-4551,4574-4575`）。 | 普通窗口；摘要给 conclusionId/partialArtifactId；过窗重放只报告已写结论，不重做 Lead 回收级联，剩余回收改用明确 reclaim 操作。这也是需要接受的新行为边界。 |
| `workflow.scope_amend` | Host/获准的 Lead 修改停止后的授权范围；固定 scope version，不重复拓宽范围。 | `W:4917-4938,4967`：授权后、expectedRevision/expectedScopeVersion 与停止前置条件前读收据；原范围被后续版本替代也不让原收据到期。 | 普通窗口；摘要保留产生的 scopeVersion，当前授权读范围表，不再次追加版本。 |
| `workflow.workspace_resolve` | Host/获准的 Lead 处理固定冲突点，restore/adopt/abandon 的效果只能提交一次。 | `W:5139-5155,5189-5195,5317-5319`：授权后、revision 与冲突 open 检查前查收据；冲突解决不是收据到期点，另有冲突事实生成的重复回答路径。 | 普通窗口；摘要保留 conflictId、action 与交付物引用，不再改工作区，当前解决事实仍可查询。 |
| `workflow.cleanup_plan` | accept/conclude/reclaim 内部回收及存储清理调用；固定删除计划与目标 allocation，回答还带大的微任务视图。内部随机命令身份见 `W:4773-4802`，存储调用见 `blackboard/tasks/storage.py:851-854,936-941`。 | `W:5504-5530,5563-5569,5598`：授权后先读收据；计划 expires_at 只限制首次删除接纳，不限制已有命令回答重放。`W:5794-5799` 明确 applying 不因过期失去恢复资格。 | 普通窗口，但关联计划处于 applying 时继续保留完整回答，直到这次删除完成；过窗的已完成/未接纳计划重放只给摘要及 planId，不把摘要当作删除授权。 |
| `workflow.cleanup_apply` | 上述内部调用执行固定计划的回收；固定删除成功、保留证据和计划身份。 | `W:5642-5664,5754-5784,5794-5799`：授权后先查收据，首次完成后才写收据；中断时恢复依据 applying 日志，不依靠尚未存在的 commands 回答。成功后的原编号没有截止。 | 普通窗口；摘要确认该计划已执行、保留 planId/removed，当前保留证据读计划结果；不重新删除目录，尤其不能删除后来在同路径出现的目录。 |
| `execution.result` | 执行进程提交 durable result；服务恢复也可从其私有回执导入；防止结果、交付物和资源释放重复提交。 | `S:2029-2091,2279-2283` 先验执行身份/有效代次，再用 `attempts.result_json` 对比同值结果并直接返回 `duplicate:true,committed:true`，通常根本不读 commands。不同结果仍为 `CONFLICT`，被新代替代仍为 `STALE_GENERATION`（`S:1772-1819`）。执行端投递是无限退避重试，直到确认或明确拒绝；确认落盘前崩溃仍会重投（`buddy/runtime/receipts.py:25-57`，`buddy/runtime/execution.py:687-707`，`blackboard/tasks/interruption.py:225-231`）。 | 已有权威 result 后可立即把命令回答收简；继续允许身份有效的同值 durable result 重投并从 attempts 返回提交确认，不设 24 小时的投递截止，不把“finished”误当作执行进程已收到确认。保留原同值校验及补充停止证据路径。 |
| `harness.prepare` | 执行进程在启动原生 CLI 前做准备或一次失败重试，编号由 attemptId/retry 构成（`buddy/runtime/execution.py:409-417,499-501`）；固定选中的健康结果及重试决定。 | `V:563-588,598-600`：第一次读取先验执行身份且拒绝 finished/cancel_requested；可启动期间反复调用同编号合法，无固定时长。 | 保留完整回答至执行 finished 或已记录 cancel_requested，此后原入口仍给 `ATTEMPT_FINISHED`，无需拿摘要驱动启动。现写入只有 task_id，实施应补 attempt_id；存量绑定转换见升级段。 |
| `quota.redetect` | CLI/控制台显式重新探测某 harness/provider 的额度，内部以 `quota-redetect:<requestId>` 为命令身份；固定那一次重探测，防止重放再次改变额度准入事实。 | `V:610-627`：同 requestId 查收据后直接 duplicate；quota 恢复、窗口重置或后来又有观测，都不使原收据失效，没有期限。 | 普通窗口；摘要保留 harness/provider 和“该次重探测已记录”，当前额度读 quota 投影，不能自动换 requestId 再探测。 |
| `user_policy.publish` | 已认证控制台保存用户设置；固定某次 field patch，防止丢失回答后的重复 patch 覆盖后来设置。 | `E:202-217`：要求控制台身份，先读收据再写设置；前端保留 uncertain 保存并重试相同 payload（`apps/console/src/use-editor.ts:108-145`），没有 24 小时限制。当前回答只有 published/counts 等小字段。 | 普通窗口；摘要证明这次 patch 已保存，控制台刷新当前设置，不能把摘要当作失败后重新发布旧 patch。 |

以上共 20 个可写入 kind；前五种的 `command_id` 是 store 的可选关键字，并非当前 named API 的公共 commandId 参数。调查了所有 `_store_receipt` 调用及 `command_id=` 生产调用，没有把不存在的 Worker 认领/续租命令或旧评价准备命令算作当前生产者。`workflow.submit` 的持久准入、`storage.apply` 的文件回执、Router 结果事实和执行进程的磁盘回执均有各自身份，不是 commands 的另一个 kind，不能随本次收简删除。

## 活动与其余周期事件：生产者及读者

当前 `attempt.activity` 是原生活动收据，不是纯粹的存活心跳。唯一入库点在 `S:1821-1890`，只有调用方真的带 `data.activity` 且 `_store_activity` 判断单调前进才追加，重复/倒退收据不追加；没有 activity 的普通 progress 不会伪造活动（`S:1831-1835,1867-1879`）。与此同时 `attempt_activity` 每 attempt 仅一行，存最新规范化投影，非历史（`blackboard/store/db.py:282-298`；`S:789-829`）。表里的 event_seq 是原生收据序号，不要与 events.seq 混用。

| 生产者 | 实际触发与频率、代码依据 |
| --- | --- |
| DSH CLI 活动 | 原生消息 fold 时和 finishing 时发布；`buddy/harnesses/dsh/native_run.py:1411-1419,1448-1452`。 |
| ZCode CLI 活动 | 原生消息更新与请求/阶段状态节点发布；`buddy/harnesses/zcode/native_run.py:1324-1329,1341-1369`。 |
| Codex CLI 活动 | 原生观察变化时发布，自身同阶段节流约 2 秒，再使用零额外间隔的共享 Publisher；`buddy/harnesses/codex/native_run.py:378-412,1170-1173,1270`。 |
| Claude Code CLI 活动 | 流帧与活动状态变化时发布；`buddy/harnesses/claude/native_run.py:500-532,973`。 |
| 共享活动传递与入库 | `protocol/activity.py:53,195-219` 默认同阶段至少隔 2 秒，阶段变化立即发布，因此 2 秒不是严格的最大发送频率，也不是无新消息时仍定时发一条。live endpoint 只留最新值，执行控制每 0.25 秒读取、判断 newer、发送 hint（`buddy/runtime/execution.py:759-785`）；断联时合并最新 hint，恢复再送（同文件 `256-285`）。服务每个被接受的新收据都写上述两处。 |
| `attempt.progress`、`attempt.waiting` | `S:1857-1887` 分别在有 message/phase 或显式 waitingReason 时追加。执行首次身份确认带 executing，其他常规 hint 不带 phase（`buddy/runtime/execution.py:260-273,589,670`）。它们不是固定周期的空心跳，本方案保留。 |
| `attempt.reminder` | 超过 expectedSeconds 的整数倍时提醒，默认 1800 秒；原生静默超过 silenceSeconds 时按静默起点提醒，默认 900 秒。恢复活动后新的静默起点可产生新提醒；一次扫描不补造所有错过的倍数。生产为 `blackboard/tasks/reminders.py:81-130`，默认值见 `protocol/schemas.py:26,162-163`、`blackboard/store/db.py:1261-1264`。Scheduler 等最近截止时间或事件变化唤醒，没有每秒追加事件（`reminders.py:166-177`）。 |
| `harness.checked`、`harness.probe_unknown` | Host/控制台借用健康扫描触发真实检查；ready 结果有效 3600 秒，其他/unknown 扫描间隔 180 秒；没有调用触发就没有固定时钟事件。`blackboard/service/harness_health.py:15-16,172-205,215-230,248-274,421-437`，`console/reads.py:32-59`。 |
| `harness.account_observed` | Codex CLI 账户观测按需触发，最短 180 秒一次，成功或部分可用时记录事件；`blackboard/service/harness_health.py:348-363,378-419`。这些是账户/额度事实，不是活动心跳。 |
| `evaluation.catalog_discovered` | 目录存量达到 6 小时货架期后，可在后一次健康扫描时重读，只有 buddy/current/pending 等事实发生变化才追加；`blackboard/catalog/catalog.py:41`、`blackboard/service/harness_health.py:187-200,283-295`、`blackboard/catalog/catalog_store.py:217-237`。 |
| 周期恢复可引起的状态与故障事件 | scheduler 每 2 秒尝试调度/重连，daemon 每 15 秒恢复；`blackboard/service/execution_scheduler.py:36,128-146,267-349`、`blackboard/service/daemon.py:162-171`。`execution.connected/unknown` 只在状态变化时追加（`S:2766-2792`），`execution.issue/service.issue` 及 resolved 按故障 stage/cause 去重或真正清除时写（`S:2303-2377`）；`attempt.interruption_seal_pending` 只在封存错误内容变化时写（`blackboard/tasks/interruption.py:106-121`）。deadline、materials_cleaned、admitted、result 等是一次业务转移，没有每 tick 固定追加。全部保留，不能把恢复轮询次数当作可删的心跳。 |

| 读者 | 运行时读取什么 | 执行结束或微任务关闭后仍需要什么 |
| --- | --- | --- |
| `get`、`status`、控制台微任务/执行详情 | `S:470-509,940-948` 直接取 selected attempt 的 `attempt_activity`，`apps/console/src/task-activity.tsx:203-232` 展示该投影；不是从活动事件逐行重建。 | 保留每 attempt 最新投影、结果、原生用量与日志路径；切换代次不继承旧活动。终态可展示最后观察，但不能据此推断已停止。 |
| 宏任务时间轴、前端布局 | `blackboard/tasks/macro_tasks.py:645-684` 从 attempts/turns 的时间字段生成执行片段，其他 queue/Host wait 片段也来自业务记录；`:60-71,777-825` 只把 HOST_MARKERS 的事件传给前端。`apps/console/src/macro-task-timeline-layout.ts:341-365` 消费这些 spans/markers，不接收 attempt.activity。 | 保留 attempts/turns、请求/续接、停止证据与 Host 标记即可；不需要从几千条活动收据推导时间轴，不新增工具级时间轴。 |
| 宏任务最近活动、分页与 Lead 名下工作视图 | 每次 `_append_event` 都调用 record_activity（`S:150-170`；`blackboard/tasks/macro_tasks.py:157-176`），更新任务/祖先/宏任务的 activity_seq/at；列表按这个序号排序分页（同文件 `292-364`，`apps/console/src/use-macro-task-list.ts:12,96-111`）；Lead 用序号判 changedSincePreviousTurn（`W:6792-6809`）。 | 保留已经写入的 seq/at，保留末条真实事件，不能因为收简历史重新记一条“现在活跃”、改变排序或回退 head。Lead 唤醒集合及回看不含 activity（`W:129-139,6721-6733,6781-6789`）。 |
| 原生静默、预期时长提醒与 wait/await | `blackboard/tasks/reminders.py:25-55,81-130` 读最新投影、执行起止、expectedSeconds 和独立的 attempt_reminders；`136-158` 通过 delivery_id 持久单次领取，`cli/blocking.py:484-504` 消费提醒。 | 不再给已有结果/finished 的执行产生当前提醒；保留已产生的提醒与领取事实，确保 lost reply 不使同提醒二次消费。不能用末条活动覆盖未完成工具信息，也不删提醒事件。 |
| 评价、Router 历史、额度与控制台健康信息 | `blackboard/evaluation/native_observations.py:18-57` 读/写 attempts.token_usage_json 与独立额度投影；`blackboard/routing/router_history.py:152-168,236-245,332-352` 只读取明确的 Router 结果/领取事件、decision_requests 与 attempts，不消费活动明细。控制台健康/目录/账户读对应当前表。 | 保留业务结果、当时模型身份、用量、Router 结果事件与设置/观测事实；本次只收简 activity，不能把计划中尚未实现的评价机制当成已有活动历史读者。 |
| 通用 `events` / `watch` 与业务等待 | `S:176-245` 按 seq > after 分页/等待，CLI 是通用事件读取者；任何外部调用方都可以观察这些原始活动事件。任务 wait 也靠 head 唤醒并重读任务（`V:870-900`）。 | 已关闭微任务的中间活动将不再可回放，最后活动和所有业务事件仍在；游标允许跳过已删除序号，不重排、不复用 seq，不发一个同 seq 的新含义。当前文档的 replayable outbox 与每次活动追加说明（`docs/reference/cli.md:150-157`、`workers.md:126`、`architecture.md:114`）须改为明确的活动保留范围；不能再承诺这一 kind 的永久完整历史。 |
| 备份、升级校验与存储清理的引用扫描 | 备份/升级校验读取库中真实行，存储扫描包含 commands/events 等所有持久结构（`blackboard/tasks/storage.py:240-270`）。 | 收简是已定义的存储转移，备份反映收简后的库；固定交付物/manifest/计划/整合/结论引用仍保留。验证移除冗余快照中的路径不会移除唯一的 allocation 保留依据，不能以减日志为由删权威引用。 |

普通命令执行和 Router 执行不凭定时空消息产生 `attempt.activity`；它们的调度、停止、结果和提醒各走上述业务路径。一次执行结束但微任务仍 waiting/awaiting-host/delivered 时，本方案保留活动明细，供 Host 在验收前诊断；只有微任务 accepted/concluded 且该执行 finished、shutdown_confirmed=1 时才收简。未验收的失败/取消微任务、停止未知的执行、仍在等待名下工作的 Lead 均不按“看起来结束”提前收简。普通执行记录没有微任务验收状态；若未来它产生原生活动，需要另定归档边界，本次不猜测。

## 存储、压缩与重放的最小做法

命令行增加 `response_form TEXT NOT NULL CHECK(response_form IN ('full','summary'))`；`response_json` 保持 JSON，但它的结构只由行上明确的 form 决定。`full` 永远表示记录的完整回答，`summary` 永远表示已提交的固定摘要，不能根据 JSON 是否有某字段、是否为空或写入日期猜形式。原 command_id/kind/request_hash/subject/task_id/attempt_id/created_at 不变；summary 保留原回答的 SHA-256、提交身份与适用的少量结果引用（例如 targetRunId、attemptId、requestId、continuationId、planId、artifactId、integrationId、conclusionId、scopeVersion、verdict、removed、issued owner generation/Host）。用逐 kind 的固定字段表抽取，不复制任务/回合/模型全文、工作区清单、retention 大对象、问题正文或控制凭据；身份不可截断。摘要不再用模型生成，目标每行约 0.5–1 KiB，结构预算 2 KiB，超出预算在实施测试中修字段集，不靠截断身份或吞错误兜底。

普通窗口建议为首次成功提交后至少 24 小时保留 full，之后第一次历史维护可转 summary；重放不续期。服务暂时离线或维护延后期间尚未转换的 full 仍按原定义返回完整回答，24 小时是完整返回的保证时段，不是读路径通过当前时间偷偷改变 JSON 语义的精确闹钟。`harness.prepare`、`execution.result`、`workflow.takeover` 用表中已经证明的因果窗口；`workflow.cleanup_plan` 的 applying 例外保留恢复所需的完整计划回答。这些状态窗口没有假造一个进程必须在几小时内结束的前提。保留一个尚有效 generation 的 takeover 回答与一个未完成执行的 prepare 回答是必要的在途状态，不承诺全库所有 full 都有固定年龄上限。

所有收据读取统一改为显式 tagged receipt：比对身份/kind/hash，按操作现有权限规则授权，再解释 form。full 返回原业务回答；summary 统一返回明确的 `COMMAND_ALREADY_APPLIED` 结果错误，details 含 `committed:true`、`duplicate:true`、`responseForm:"summary"`、命令身份和摘要。这表示原命令已提交但旧完整回答已收简，不能理解为原操作失败，也不应被自动重试/重新编号。不同 payload 仍先给 CONFLICT；身份无权仍拒绝，不能因为摘要小就跳过权限。特别是 takeover 必须先拿摘要所留的 issued generation/Host 做原控制权重放校验，不能在未授权前由通用收据函数直接抛出含提交信息的摘要。

`execution.result` 是明确的确认接口，继续走 attempts.result_json 的同值校验和 `committed:true` 成功路径；它不把上述普通命令摘要错误交给无限重投循环。不能删 attempts 的原结果、回执、补充停止证据，也不能用 task 当前终态替代同值检查。`harness.prepare` 在其窗口外仍由入口先给 ATTEMPT_FINISHED；takeover 在下一代之后仍给 STALE_GENERATION。这两类不能被通用摘要结果覆盖。

CLI 已有错误信封透传，需确认 brief/full 输出都不丢 committed 和摘要，指南说明读同一工作身份的 get/status/消息/计划事实，禁止收到该结果后自动产生新命令。控制台的设置保存和宏任务停止必须识别这个特定结果为“原提交已记录”，刷新当前视图；停止只确认当时取消意图，继续显示真实停止证据。当前 `macro-task-stop.ts:141-162` 会校验普通完整回答并把其他错误判成拒绝，不能仅在服务里换返回体便声称完成；`use-editor.ts:127-158` 的 uncertain 保存也须接通。实施若选择这个统一契约，需要 Host 把 `apps/console/` 加入下一回合写入范围，并纳入前端验证；本回合只改本文。普通的权限拒绝、会话失效和服务不可用不能被这个特判吞掉。

活动不增加第二种 JSON：对满足归档条件的每个 attempt，保留 `kind='attempt.activity'` 中最大 events.seq 的一行，原 seq/kind/payload/created_at 全部不改，删除该 attempt 更早的同 kind 行；`attempt_activity` 最新投影也保留。没有活动就不造一条，有一条就不动。执行和 Host 等待的时间轴片段照读既有业务行，原生活动末态照读最新投影。既无读者需要活动总数、首条时间、每分钟样本或工具阶段持续时间，就不增加这些汇总；后续若真的需要工具级回放，应另定产品能力与保存成本。

保留末条原事件，使全局和按任务的 MAX(seq) 不下降；宏任务/微任务已有 activity_seq/at 不重算，也不因删除历史变成最近活跃。对游标落在被删区间的 events/watch，下一页直接给仍保留的后继事件，已读到尾部的调用者不需要收到一条“维护发生”的假业务事件。每个 events/watch 响应和能力说明应明确 `attempt.activity` 在微任务关闭后只留末条的保留策略；这个声明是固定策略元数据，不另存逐任务归档标记或伪造“历史完整”的保证。业务 outbox 事件原样完整保留。

收简由黑板服务的一个历史维护函数负责，放在 store 一侧；daemon 复用现有 `_maintain` 线程（`blackboard/service/daemon.py:162-171`），每 60 秒调一次，启动后也做一次，不另起线程/进程，不让执行进程、CLI、控制台或模型清理数据库。不在 accept/conclude/result 每处再挂一次同步压缩；同一函数在一次短写事务内重读资格，然后替换一批命令回答或收简一个已关闭微任务的活动，失败事务回滚，错误进入已有 service issue，不静默吞掉。写锁使相同命令提交/重放与收简只能看到 full 或 summary 的一个完整状态；活动归档也在锁内重验 stopped/closed，不能与仍合法的 progress 写入交错。以 full 行的部分索引按 created_at 查普通候选，状态窗口复用已有身份和计划表；不维护第二份到期队列或每行清理任务。

本机制限制的是冗余回答与已关闭工作的活动明细，不删除 command 身份行，不清空业务事件，不删除原生会话/日志，不替代存储清理和工作区回收。保留命令墓碑会随命令数线性增长，但每行固定很小；没有外部请求去重期限/命令身份不再重用的证明，删墓碑会让旧编号再次执行，不能为了表行数完全不增长而这样做。

## schema 23 到 24 与实施验证边界

必须显式升级到 schema 24：命令行新增 form，存量回答也要进入同一个明确契约；活动行的删除同样是有定义的存量转换。当前 `blackboard/store/upgrade.py` 不存在，SCHEMA_VERSION=23（`blackboard/store/db.py:22,1211-1245`），不能声称已有待做升级可直接扩展。下一次实现从 0.40.0/`9baa0524`/schema 23 的已记录基点新建这一个 pending upgrade；保持基点夹具不动，不能把候选 24 的新库重新标成旧基点。存储形式和 Host 重放结果契约变化按现行规则提升中间版本（本基点之后独立落地应为 0.42.0，CONTRACT_VERSION 同步），不只改补丁号。

升级在旧服务退出、离线备份验证通过并取得独占锁之后执行：先对全部旧 commands 标 full，保持原回答不变；再按同一个维护规则，把已经过普通窗口或因果窗口的行转换为 summary，把已验收/已写结论且确认停止的活动收简到各 attempt 末条。未到期、applying 中的计划、仍当前 generation 的 takeover 回答仍是 full。旧 `harness.prepare` 写入没有 attempt_id，其 subject 由 `_subject` 的 nonce verifier 与 attemptId 拼成（`S:267-269`、`V:570-600`），转换在这一边界解析并核对 task/attempt 绑定后补齐 attempt_id；不能用 task 最新 attempt 猜归属。新生产者一次写齐，运行期不保留“从旧 subject 猜”的第二条读取路径。

安装器当前只接受当前 schema，而且 contract 变更分支在迁移位置之前仍用 `BoardStore(state)` 备份、做 idle_snapshot（`install/upgrade.py:58-96,489-515`）。实施必须把“从声明的 23 基点做离线备份与快照校验 → 唯一 23→24 转换 → 用新 schema 校验”接成完整路径，不能只加升级函数而保留前面的 23 拒绝。升级后的 expected snapshot 要按显式转换重新建立并核对权威记录不变，不能把 commands/events 任意变化整表放行。只支持这个基点，不恢复历史版本转换或在正常启动时迁移；转换事务失败保留原库与备份，安装恢复按已有 journal/rollback 处理。

下一回合的关键验证是行为与存量一致性：窗口内丢回复的原身份仍得原提交事实；summary 后同身份绝不再次运行/再次删除，同编号不同请求仍冲突；当前 takeover 丢回复超过普通窗口仍能凭原控制取回本代凭据，下一代后拒绝；execution.result 已提交却丢确认并在服务重启/很久之后重投仍成功去重，异值和旧代拒绝；applying 跨期限仍可恢复；活动收简前后当前状态、提醒领取、时间轴、宏任务分页排序和 Lead changedSincePreviousTurn 不变，head 不退，未验收及停止未知的明细不删；公开 events/watch 能从被删区间的游标继续；重复维护和重复升级恢复不产生第二次业务效果。fixture 升级库与同一最终源码新建库比较结构和规范化业务语义，明确允许的差别只有既定的回答形式转换与活动保留政策。

第二回合先用仓库检查运行器跑受影响的 store/workflow/service/protocol、提醒和升级夹具测试，最后跑完整 `uv run --frozen python -m hey_my_buddy.cli.checks`；每个检查子进程清除 AGENTS.md 列出的继承 runtime/agent 凭据环境变量，仍用默认 4 并行，不自行起测试进程。若统一摘要结果需要上述控制台改动，最终源码另跑 `npm --prefix apps/console test`，并用私有合成预览验证设置保存/宏任务停止的不确定结果重试；需要浏览器安装时设 PLAYWRIGHT_SKIP_BROWSER_GC=1。公共说明更新 owning references 与 shared skill，必要时由 Host 扩大文件范围。本回合没有做上述运行验证，也不把这份方案当验收记录。

考虑过但不采用：以任务终态直接认定所有命令重放非法（表中多处收据检查早于状态检查，takeover/result 还有必要恢复窗口）；统一给所有执行回执设 TTL（会截断 durable 无限投递）；只有验收时做清理（漏掉全局设置、普通执行和后写的清理命令）；只在重放读取时清理（没人再读的大回答永不收简，并让读路径承担写事务）；另加收到回答的 ACK 协议（多一套跨进程事实与恢复状态）；无期限保留所有完整回答（达不到本项方向）；压缩 JSON 为另一种编码或把旧回答搬到另一库/文件（仍永久保存冗余全文）；新增活动摘要事件/分钟分桶/阶段片段表（当前读者不需要）；删除全部活动包括尾条（可能让 head 回退）；原位把尾条改成 summary（让同一 seq 变义）；运行时双格式猜测、静默降级为空回答或重新执行（破坏明确格式及幂等性）。

## 容量估算与待决定事项

Host 的当前样本是数据库 38 MB、230 条命令回答共 7.2 MB；四种大回答约合 6.86 MB（cleanup_apply 2.5、cleanup_plan 1.9、accept 1.6、continue 0.86 MB），占回答体约 95%。15,413 条事件中 12,532 条 activity 的 payload 约 4.1 MB，平均约 327 字节；活动约每天 5,000 条。下面的 MB 按 Host 原口径估算，回答体、payload、SQLite 表/索引/空闲页不能混为同一指标。

若 230 条都达到各自收简条件，按每条摘要 0.5–1 KiB，命令回答降到约 0.12–0.24 MB，省约 6.96–7.08 MB，即 97% 左右的回答体。采取普通 24 小时窗口且三天流量近似均匀时，第一轮只能收简约前两天的普通命令：预计省约 4.6–4.7 MB，保留最近一天约 2.4 MB 的完整回答及约 0.08–0.16 MB 的旧摘要；真实分布、takeover/prepare 的在途例外和 applying 计划数未知，不能报告精确回收值。

令这 12,532 条活动里，已关闭且可收简的原行数为 N，涉及有活动的执行数为 A，则保留 A 条末态，payload 可省约 `327 × (N − A)` 字节；没有这些数量不能声称 4.1 MB 全可删。若所有活动都合资格且 A 为 100–300 的纯示例，保留约 0.03–0.10 MB，可省约 4.0 MB；A 不是本次测量所得。若只一半活动已经验收/写结论，则活动部分能省的量接近一半，其余继续作为待验收诊断记录。

所有命令都过窗且活动全合资格时，二者逻辑数据合计可省约 11 MB；普通 24 小时窗口的第一轮、活动全合资格的示例则约省 8.6–8.7 MB，分别约为 38 MB 的 29% 和 23%。这不是磁盘文件立即缩小的承诺：SQLite 删除/更新通常把页交回 freelist 供之后重用，WAL checkpoint 也不是 VACUUM；备份若复制数据库文件，同样可能保留空闲页。建议本次不增加常驻 VACUUM 机制，升级私有验证另量 payload、page_count/freelist_count、WAL 与实际备份大小；若开发者要求立刻缩小物理文件，再单独在离线升级的已备份阶段决定是否一次性 VACUUM，并核对耗时与空间。

未来按该三天样本，命令平均约 77 条/日，回答体约 2.4 MB/日。窗口稳定后，最近一天约 2.4 MB 是滚动在途量，旧命令摘要新增约 0.04–0.08 MB/日，加上身份和索引，取代每天再累积约 2.4 MB 的完整回答；保留恢复窗口的例外单独计。活动当前约 `5000 × 327 ≈ 1.64 MB/日` 的 payload，归档稳定后，新增历史活动约为当天关闭的有活动执行数乘 327 字节，另加尚未验收的活动积压；例如每天 100 次合资格执行约 0.033 MB/日，100 是说明公式的假设。其余约 2,881 条非 activity 事件及权威业务表照常增长，现有测量没有其 payload/索引大小，不能给整个数据库的精确每日增长。未验收工作持续累积时，活动仍可按原速增长，这个方案不等于全库固定容量。

需要开发者决定的是普通命令完整回答保留窗口及过窗语义：建议至少 24 小时，之后原身份只收到明确的已提交摘要；当前源码与调用方允许更晚重放，无法证明 24 小时覆盖所有合法的完整回答需求。接受该新边界后，takeover/result/prepare/applying 的因果例外、微任务关闭后的活动末条保留和 schema 24 转换可以一起实施；若要求所有现有合法重放永远获得原完整回答，就不能同时承诺把这类全文有界删除，需要改变目标或增加显式 ACK 协议。其余机械取舍无需开发者再定；实施前由 Host 审阅本方案并授权下一回合，以及为统一控制台结果处理补足写入范围。
