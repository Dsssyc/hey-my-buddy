# 命令提交身份与周期状态的保存

开发者于 2026-10-16 定下：命令身份与回答长期保留，收据只保存不能从权威表重新读取的提交身份，重放时重建当前视图；周期性的状态随写入替换上一条。本文记录这个最终做法。调查与实现基点为 `c605b127`，schema 从 23 显式升级到 24，契约目标为 0.42.0；实际验证与当前交付边界见 [实现记录](bounded-history-notes.md)。Host 提供的数据库体积来自日常看板的只读测量，本实现未访问日常状态或原生账户。

## 命令的身份与回答

下文 `S` 指 `src/hey_my_buddy/blackboard/store/store.py`，`W` 指 `src/hey_my_buddy/blackboard/tasks/workflow.py`，`V` 指 `src/hey_my_buddy/blackboard/service/service.py`，`R` 指新增的 `src/hey_my_buddy/blackboard/tasks/command_receipts.py`；其他以 blackboard、buddy、protocol、install 开头的路径从 `src/hey_my_buddy/` 起算。已有行为的行号对应冻结基点 `c605b127`，新增实现以文件中的函数名定位。

`commands` 仍保存 command_id、task_id、attempt_id、kind、request_hash、response_json、subject、created_at，编号不删除、不复用。统一读收据先核对执行 subject、kind 与请求哈希（基点 `S:271-294`），再按 kind 重建回答；统一写收据在业务事务里调用 `R.stored_response`，统一读取调用 `R.replay_response`。相同编号与不同请求仍为 CONFLICT，执行身份无权仍拒绝，原操作的授权顺序继续由各入口负责。重放不会再次准备检出、排执行、修改工作区或删除目录。

每个 kind 只有一种当前存储形式，由 kind 明确决定，旧库在显式升级中一次转换；正常读取不根据写入时间、字段有无或 JSON 大小猜旧形式。十种 workflow 回答存提交身份，九种小回答保持原样；execution.result 的唯一提交身份是 attempt，持久结果与同值校验已经足够，不再额外写 commands。回答里的 duplicate 标志继续由各入口设置。提交身份不变，微任务状态、revision、已公开请求、子微任务与交付物列表从当前权威表读取。

| kind | 谁发及为何去重、合法重放条件与源码依据 | 收据实际保存、重放从何处恢复 |
| --- | --- | --- |
| task.submit | 普通执行提交的 store 方法可传 command_id，保护首次接纳；named API 未传该关键字，生产准入主要以 requestId 去重。已有任务同输入仍可读回，无时间截止（`S:849-880,934-935`，`V:845-846`）。 | 小回答原样保存；不新增公共 commandId 参数。 |
| task.cancel | store 可选编号保护一次取消意图，named API 与服务停机不传编号；已有收据先于任务状态检查，终态不使旧收据失效（`S:1092-1101,1136-1139`，`V:857-858`）。 | 原样保存；取消回答不证明进程停止。 |
| task.retry | store 可选编号保护一次重新排队，named API 未传；旧收据先于新代次的状态检查（`S:1174-1187,1255-1256`，`V:860-861`）。 | 原样保存，重复不排第二次执行。 |
| task.acknowledge | store 可选编号保护验收事实；已有收据先于当前验收资格检查，Router 执行不算业务验收（`S:1290-1305,1352-1355`，`V:863-864`）。 | 原样保存。 |
| message.post | Host 问询的 store 可选编号保护消息身份，另有 task/inquiryId 去重；已回答的消息仍能读回，named API 未传编号（`S:2457-2485,2535-2538`，`V:941-942`）。 | 原样保存，不能重发同一问题。 |
| workflow.decide | Host 或获准的 Lead 决定一个帮助请求；授权后、revision 和请求关闭检查前查收据（`W:2202-2234,2390-2401,2440-2455`）。 | runId、requestId；decision 从该 workflow_requests.decision_json 读取，其余为当前 run 视图。 |
| workflow.continue | Host/获准的 Lead 为本微任务或受控后代续接；授权后、expectedRevision 检查前重放，保护唯一 continuation（`W:2921-2942,3115-3130`）。 | runId、continuationId；目标 run、输入哈希、helperPolicy 从该 workflow_continuations 行读取；当前视图仍属于原控制微任务。 |
| workflow.takeover | Host 或控制台换手，原 Host 丢回复后仍需取回新一代凭据；重放专门验证原 capability，当前 generation 必须仍等于此命令颁发的 generation，否则 STALE_GENERATION（`W:3312-3324,3362-3375,3380-3419`）。 | runId、颁发的 ownerGeneration、previousOwnerGeneration、control.hostId/control.ownerGeneration；controlToken 仍由 db.control_token(runId,generation) 推导；保留 issued generation 供原重放授权，当前视图不能覆盖它。 |
| workflow.cancel | Host/获准的 Lead 或控制台取消受控工作，编号可选；授权后先查已有收据，accepted/concluded 只拒绝新的取消（`W:3434-3448,3567-3593`）。 | runId；固定的 cancelled=true 与说明由操作定义重建，当前停止状态照读当前视图。 |
| macro_task.stop | 已认证控制台停止当时的宏任务工作集合；原编号重放必须保留当时范围，不能扩大到后来新增的微任务（`W:3453-3466,3499-3506`；`apps/console/src/macro-task-stop.ts:176-213`）。 | 小回答原样保存，包括当次的范围与数量；不改控制台。 |
| workflow.accept | Host/获准的 Lead 验收固定交付物，命令号由规范化请求派生；授权后先查收据，回收后仍可重放，原接口已经用当前视图覆盖旧快照（`W:3924-3944,3974-4006,4392-4404`）。 | runId、integrationId，以及有解决冲突交付时的 resolutionConflictId；integration 与 resolutionDelivery 从固定整合行和冲突交付记录重建。verdict 为 accepted；原重放不再次回收，丢失回收结果走 reclaim。 |
| workflow.conclude | Host/获准的 Lead 写结论，命令号绑定目标执行与 note；收据防止再次封存和结论写入，当前视图原本就会刷新（`W:4475-4515,4535-4562,4681-4719`）。 | runId、conclusionId；结论与部分交付物身份读 workflow_host_conclusions，verdict 为 concluded。保留原 Lead 结论重放对未完成名下回收的既有行为。 |
| workflow.scope_amend | Host/获准的 Lead 修改停止后的授权范围；授权后先读收据，再检查 expectedRevision/expectedScopeVersion（`W:4917-4938,4940-4967`）。 | runId、targetRunId、scopeVersion；从该版本的 workflow_scope_versions 取 writeScope 和 inputManifestSha256，previousScopeVersion 为上一版。 |
| workflow.workspace_resolve | Host/获准的 Lead 对固定冲突点 restore/adopt/abandon，授权后先查收据；一个局部恢复后同一冲突行仍可被下一次解决更新（`W:5139-5155,5290-5319`）。 | runId、targetRunId、conflictId、action、resolutionState、resolvedPaths、preservedPaths、remainingPaths、observedFingerprint、resolvedFingerprint、artifactId、nextBoundaryRequestId、delivered；这些保留首命令的提交结果，不能被后一次局部恢复覆盖。artifact/manifest 和已交付的 delivery 从固定交付物与冲突行读取；当前工作视图重建。 |
| workflow.cleanup_plan | accept/conclude/reclaim 内部回收及 storage.apply 生成命令号，保护固定 allocation 的计划；收据先于 revision 检查。计划到期限制首次删除授权，不限制已有回答；applying 必须能跨期限续做（`W:4773-4802,5504-5530,5563-5598,5794-5799`；`blackboard/tasks/storage.py:851-854,936-941`）。 | runId、targetRunId、planId；计划、path、retention、evidence、当前 state/result 全从 workspace_cleanup_plans 读取，可以从原来的 planned 前进到 applied。 |
| workflow.cleanup_apply | 同一内部调用完成计划，成功后才记录收据；中断恢复依据 applying 日志，成功后的旧编号仍可重放（`W:5642-5664,5754-5784`）。 | runId、targetRunId、planId；计划已成功提交这一事实不变，removed=true，retention 从该计划 result_json 读取，不重复删除后来在同一路径出现的目录。 |
| execution.result | 执行进程 durable 投递或服务恢复导入；result_json 存在时先校验同值并确认 committed，不读取 commands（`S:2029-2091,2279-2283`）；磁盘确认前可无限重投（`buddy/runtime/receipts.py:25-57`）。 | 不再写或读 commands；attempts.result_json/result_command_id、代次/nonce 校验、补充停止证据、交付物与资源结算仍在原事务中。同执行异值为 CONFLICT，旧代为 STALE_GENERATION。 |
| harness.prepare | 执行进程在启动原生 CLI 前做准备或一次失败重试；finished/cancel_requested 的检查在初次收据读取之前（`V:563-588,598-600`；`buddy/runtime/execution.py:409-417,499-501`）。 | 健康记录的小回答不变；升级时所有执行已停止，无须从 subject 解析旧 attempt_id。 |
| quota.redetect | CLI/控制台显式重探测，以 quota-redetect:requestId 固定这一次准入事实（`V:610-627`）。 | 小回答原样保存，不能用当前额度替换当时的提交事实。 |
| user_policy.publish | 已认证控制台一次设置 patch；收据先于设置写入，避免丢回复后重复覆盖新设置（`blackboard/evaluation/evaluation.py:202-217`）。 | published/counts 等小回答不变；不修改控制台保存流程。 |

十种 workflow 都经 R 的固定分派；不能只处理 Host 测量到的四种大回答，而遗漏 decide、cancel、scope、resolution 等带当前视图的种类。scope/冲突/计划等身份字段定位原提交的记录，不改成“取最新一行”，避免后续操作冒充原提交。业务表不会因收据变小而删除；当前架构没有删除微任务历史的公共操作，固定记录仍可读。

## 周期状态与读者

attempt.activity 的唯一入库点是 execution_progress，只有合法的新原生活动收据才更新 attempt_activity 最新投影并写事件；倒退和相同收据不写，空心跳不伪造原生活动（基点 `S:1821-1890`）。四家原生 CLI 的来源是 DSH native fold/finishing（`buddy/harnesses/dsh/native_run.py:1411-1419,1448-1452`）、ZCode native message/阶段状态（`zcode/native_run.py:1324-1369`）、Codex 观察（`codex/native_run.py:378-412,1170-1173,1270`）与 Claude Code 流帧（`claude/native_run.py:500-532,973`）。共享 ActivityPublisher 同阶段默认至少隔 2 秒，阶段变化立即发布，不是无消息时每 2 秒写一次；执行控制每 0.25 秒读取 live endpoint 的最新值、判断 newer、合并断联 hint 后转发（`protocol/activity.py:53,195-219`；`buddy/runtime/execution.py:256-285,759-785`）。这些是源码路径调查，不是某个已安装 CLI 版本的运行验证。

写入一条新的 attempt.activity 时，先得到新的 AUTOINCREMENT events.seq，再在同一个写事务中删除该 attempt 的较早同 kind 行。对外只能看到旧的一条或新的一条，序号不回退、不复用；不改旧事件的含义。attempt_activity 的最新投影和事件替换原子提交，事务失败时两者及删除一起回滚。不同 attempt 各留一条，微任务是否验收、执行是否结束都不影响这个状态保存规则。

| 读者 | 实际依赖与替换后的表现 |
| --- | --- |
| events/read、watch、events/wait | `S:176-245` 按 seq > after 读取；旧游标落在已删记录处仍从新的较大序号继续。观察者可能跳过被新状态替换的中间观察，依靠最新状态及业务事实观察进度，不再把周期状态当完整历史；响应形状不加字段。业务事件仍逐条保留。 |
| status/get、控制台详情 | `S:470-509,940-948` 直接读 selected attempt 的 attempt_activity，`apps/console/src/task-activity.tsx:203-232` 展示该投影，不从活动明细重建；结束后保留最后观察，不能据此证明停止。 |
| 提醒与 wait/await | `blackboard/tasks/reminders.py:25-55,81-130` 读最新原生活动、attempt 起点和 expectedSeconds；静默起点/未完成工具从最新投影取，不需要旧活动行。提醒本身有独立持久事实和领取身份，全部保留。 |
| 时间轴 | `blackboard/tasks/macro_tasks.py:645-684` 的执行片段来自 attempts/turns；`:60-71,777-825` 只传 Host 标记，不传 attempt.activity；前端 `macro-task-timeline-layout.ts:341-365` 照读既有 spans/markers，不增加新读法。 |
| 宏任务排序、Lead 变化摘要 | 每次新事件仍通过 record_activity 更新微任务/祖先/宏任务 activity_seq/at（`S:150-170`，`macro_tasks.py:157-176,292-364`）；Lead 的 changedSincePreviousTurn 比较序号（`W:6792-6809`），不会因旧行替换而丢失“有变化”。Lead 唤醒集合不含 activity（`W:129-139,6721-6733`）。 |
| 评价、额度与 Router 历史 | 用量/额度从 attempts 与独立观测投影读取（`blackboard/evaluation/native_observations.py:18-57`）；Router 历史读取明确的 router 结果/领取事件、decision_requests 与 attempts（`blackboard/routing/router_history.py:152-168,236-245,332-352`），均不累计原生活动。 |
| 备份、升级和引用扫描 | 备份对实际保留行计算完整指纹，升级显式转换后建立 expected；引用保护的结构性路径在权威表中，见下一节。没有在启动时改旧行。 |

| 其余事件/生产节奏 | 按同一规则判断后的处理 |
| --- | --- |
| harness.checked、harness.probe_unknown | Host/控制台借用健康扫描，ready 有效期 3600 秒，其他/unknown 重检间隔 180 秒；没有调用触发就不产生固定时钟事件（`blackboard/service/harness_health.py:15-16,172-274,421-437`）。每种 kind、每个 harness 只留最新观察；健康与 lastProbe 的权威最新值在 harness_health，没有需要这些状态累计历史的内部读者。成功检查与未知探测是两类不同观察，各自替换。 |
| harness.account_observed | Codex CLI 账户按需最多每 180 秒读一次（`harness_health.py:348-363,392-419`），每 harness 只留该 kind 最新一条；billing 与 quota 的最新权威投影仍在 harness_health/harness_quota_observations/quota_current。其他 harness 的记录互不替换。 |
| attempt.reminder | 预期时长的每个整数倍提醒（默认 1800 秒）与每个静默起点的阈值提醒（默认 900 秒）是可逐条消费的通知事实，非覆盖式状态。扫描可以补记已到的多个倍数；attempt_reminders 的唯一键与 delivery_id 支撑一次持久领取（`reminders.py:81-158`、`cli/blocking.py:484-504`）。表与事件都累计保留。 |
| evaluation.catalog_discovered | 目录 6 小时陈旧后可随下一次健康扫描重读，但只有目录事实变化才写事件（`blackboard/catalog/catalog.py:41`、`harness_health.py:187-200,283-295`、`catalog_store.py:217-237`）。这记录一次业务目录变化，保留。 |
| attempt.progress、attempt.waiting | 有 message/phase 或显式 waitingReason 才追加，非周期空消息；身份首次确认带 executing，常规 activity hint 不带 phase（`buddy/runtime/execution.py:260-273,589,670`；`S:1857-1887`）。保留业务阶段与等待原因转移。 |
| 重连/恢复、故障、停止 | scheduler 每 2 秒尝试，daemon 每 15 秒恢复；connected/unknown 仅状态变化写入，issue 按 stage/cause 去重，seal_pending 仅错误变化时写入（`blackboard/service/execution_scheduler.py:36,128-146,267-349`；`blackboard/service/daemon.py:162-171`；`S:2303-2377,2766-2792`；`blackboard/tasks/interruption.py:106-121`）。这些是故障、恢复与停止的事实，保留，不按轮询次数制造事件。 |

没有新增维护线程、启动维护、验收清理 hook 或另一份到期队列。状态替换和业务事实累计使用既有事件写入口，区别来自固定的 kind 和它的身份键，所有写入都由服务的单一数据库事务负责。events/watch 的保留说明、参考文档及共享 skill 由 Host 更新，本次不修改响应或前端。

## 删除保护里的权威引用

存储清理的 `_workspace_reference_rows` 扫描 commands/events 以及业务表，命令视图里重复出现的路径会被视为引用（`blackboard/tasks/storage.py:236-318`）。本次不改这条删除守卫、不减少其扫描表：移除的是视图副本，不是业务引用。下面逐项列出结构性路径的原始来源；固定 manifest 和交付物记录不会随一次重放或一个状态事件被删除。

| 回答中的路径/身份 | 权威表与列 |
| --- | --- |
| microTask.cwd、task.cwd/spec/exclusive resources、执行输入中的源路径 | workflow_runs.micro_task_json、tasks.cwd/spec_json/exclusive_resources，以及 workflow_turns.input_json、workflow_continuations.workspace_manifest_json（`W:624-775,784-840` 及对应表定义）。 |
| workspace.path、checkoutRoot、repositoryPath、checkoutId、workspaceId、原输入/各轮 manifest | workflow_runs.workspace_manifest_json、workspace_reservations 中的路径与身份、workflow_artifacts.manifest_json；输入和各回合 manifest 被固定保存（`W:478-493,624-775`）。 |
| 回合原生会话/日志/交付结果中的路径 | workflow_turns 的输入和结果、attempts.log_paths/result_json、artifacts.location。execution.result 不写收据后这些权威行仍在原结果事务中写入。 |
| 输出 diffPath、累计补丁、输入与输出仓库引用、partial/Host output | workflow_artifacts.manifest_json；原生结果中的 seal 也保留在 attempts.result_json。`W:432-475` 从该 manifest 生成公开交付物视图。 |
| 整合 target.path、target checkout/repository、比较证据里的路径 | workflow_integrations.target_path/verification_json 及身份列，`W:570-598` 只是其视图。 |
| 计划 path、retention、evidence、result 内部的所有保存位置 | workspace_cleanup_plans.path/retention_json/evidence_json/result_json 与 workspace/checkout/repository 身份列，`W:601-621` 直接投影这些列；计划从 planned 前进到 applied 不移除路径。 |
| 冲突点、已授权/保留/恢复路径与其交付物 | workflow_workspace_conflicts 的结构列/evidence_json/delivery_json、workflow_scope_versions 的范围与输入 manifest、固定 workflow_artifacts；局部恢复中会被后续覆盖的首操作字段仍在该命令收据中保留。 |
| 周期原生活动 | schema 定义的字段是阶段、原生序号/时间、会话标识、工具名称、等待文本与计数，没有 allocation 路径/manifest 字段；最新值仍同时保留在 attempt_activity 和末条 events。不能把工具名称或诊断文本中的偶然提及提升成 allocation 的所有权来源。 |
| 周期 harness 健康/账户观察 | 有 harness、版本、状态、原因、计费/额度观察，不承载受管 allocation 的路径身份；对应最新状态表保持不变。 |

固定输入/输出、整合目标、计划以及旧回合的权威 manifest 仍被引用扫描覆盖，包括当前 allocation 已换过之后的历史引用。`workflow.workspace_resolve` 的保留路径字段不丢弃。测试应验证转换前后剥离重复视图仍不能把有权威引用的 allocation 判作孤立；独立源码复核和实际检查证据列入实现记录。若真实的唯一结构性路径只在被剥掉的副本中存在，就必须修其权威写入来源，不能在清理调用点加 fallback。

## 显式升级与验证

schema 24 的唯一形式在新库直接创建；正常启动仍只接受当前 schema。新建 `blackboard/store/upgrade.py` 承接唯一 pending 23→24 升级，从 0.40.0、`9baa0524` 的 schema 23 样板出发；不重写基点夹具，不恢复更早历史转换。安装接受基点 23 与当前 24；改契约时原执行必须 finished 且 shutdown_confirmed=1，保留 Unknown 不等于 stopped 的前置条件。

安装先使旧服务退出、取得独占锁并用旧库的原字节建立且验证离线备份，再执行一个 SQLite 写事务：十种 workflow 的 response_json 剥为提交身份，execution.result 的旧收据删除，harness.prepare 和其他小回答不动；attempt.activity 按 attempt_id 保留最大 seq，三类 harness 状态按 kind/harness 保留最大 seq；最后更新 schema marker。迁移不解析 prepare 的 subject，不修改保留事件的序号/内容/时间，不改变其他业务表。失败回滚整个转换，安装原备份仍为 23，可用原运行时恢复。

安装 expected 是转换后的完整快照；新服务启动后按全部表与既定 eventHead 做指纹比对。状态规则只在真实新写入时生效，没有启动收简造成指纹漂移，也没有对 commands/events 放宽任意变化。相同契约安装仍要求当前 schema 并保留在途执行。

验证覆盖每种收据的提交身份/当前视图、不同请求冲突与原有授权拒绝；takeover 用原控制取回本代凭据及更晚一代拒绝；result 丢确认重投及补充停止证据；局部冲突连续解决后的首命令重放；计划从 planned 变 applied 后按同身份读取；活动每 attempt 一条、跨代隔离、单调游标、失败回滚、提醒/时间轴/Lead 的读路径；样板库升级后与新建当前库比较结构和规范化业务行、转换失败回滚与私有服务安装校验。全部通过检查运行器，只跑受影响文件，Host 合并后运行完整检查；本回合不安装产品、不操作日常服务、不做原生模型调用。

## 估算

Host 样本中 230 条命令回答共 7.2 MB，cleanup_apply/cleanup_plan/accept/continue 共约 6.86 MB，约占回答体 95%。这些大视图从首次提交时就不再写进命令表，没有滚动保留的一日完整回答。以每条提交身份约 0.1–0.5 KiB 估算，230 条若都属于可剥离的 workflow，留下约 0.02–0.12 MB，回答体减少约 7.1 MB；实际还有小回答和不可丢的局部恢复路径，具体值应从私有样板/测试库测量，不能把估计作为日常库的实测。

12,532 条活动 payload 约 4.1 MB，平均约 327 字节。令有活动的执行数为 A，存量保留 A 条后约为 `327 × A` 字节，节省约 `4.1 MB − 327 × A`；A 为 100–300 的纯示例对应约 0.03–0.10 MB，省约 4.0 MB，实际 A 未由 Host 提供。活动在运行中也替换，长执行不再每天累计约 5,000 条；一天新增历史量取决于新增的有活动执行数，非运行秒数。

按三天平均约 77 条命令/日，若提交身份平均 0.1–0.5 KiB，则该部分约增 0.008–0.04 MB/日，取代原回答体约 2.4 MB/日；活动若每天新增 100 个有活动执行，payload 约增 0.033 MB/日，100 是示例。每个 harness 每种状态最多一条，不再随健康/账户观察次数增长。提醒、业务事件、业务表与执行/会话文件继续按各自事实增长，本项不声称整个状态目录固定容量。

上述约 11 MB 是逻辑 payload 的潜在减少，不等于 38 MB SQLite 文件立刻缩小；删除与更新后的页可重用，离线备份也可能带空闲页，本次不新增 VACUUM 或物理回收机制。命令编号持续保留是幂等性要求，后续增长是小身份行；实施与测试的真实字节数及未完成边界记在实现记录，最终验收由 Host 负责。
