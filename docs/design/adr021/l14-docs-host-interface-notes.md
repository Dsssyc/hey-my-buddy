# L14：Host 接口参考文档对照说明

基于 `1ce8488e` 的当前源码静态核对，仅修改分配的五页与本说明；未运行测试、产品检查器、服务、安装、联网或真实 harness，未访问检出以外的产品状态及凭据。现有版本号与安装状态的句子按 Host 要求保留，只有其中旧名称改动。

## 改名处数

计数按原文旧名称的一次出现计，复合标识按一个词计，重复出现分别计；与最终文本对齐后只计发生改写或删除的位置，删除旧说明涉及的旧名也计，Router 设置意义的 configuration 与连接件意义的 adapter 保留。路径与命令中的多个旧名称分别计。

- `docs/reference/cli.md`：146 处。
- `docs/reference/usage.md`：71 处。
- `docs/reference/workflow.md`：126 处。
- `docs/reference/objectives.md（待 Host 改名为 docs/reference/macro-tasks.md）`：59 处。
- `docs/reference/workspace-lifecycle.md`：17 处。

## 源码核对的边界

对五页代码字体与 JSON 示例里的名称逐项提取后用 rg 静态检索；命令及参数对照 cli/main.py、protocol/transport.py、protocol/schemas.py 与实际方法实现，返回字段对照 cli/cli_views.py、workflow.py、micro_task.py、macro_tasks.py、evaluation.py、catalog_store.py 与前端类型，状态/事件/表列对照 store/db.py 和事件写入点，环境变量与路径对照启动器和 install 源码。没有运行命令帮助。组合属性路径按父对象与子字段核对，不要求源码恰好含整条点分路径；task.failed 由 store.py 的 f"task.{task_state}" 生成；示例 requestId、占位目录、src/state.ts 等是使用示例，不是产品固定名称。

配置与连接件的区分：Router 设置对象 configuration、configuration.defaultRoutingMode、configuration.routerBuddyIds、configuration.routingBudget 保留；task/attempt/Worker 记录、execution-submit 的 adapter 及 capabilities 回应的 adapters 是连接件事实，保留；微任务 buddy、模型服务、目录、健康及原生会话筛选使用 harness。

## 源码已无而删除或改掉的说明

- B07：`docs/reference/cli.md` / Evaluation, routing and console；原来：快照有配置版本号、逐 profile 计数。现在：快照使用 eventCursor，没有旧配置版本与逐 buddy 计数；保留实际列表、策略、并发与任务。依据：`src/hey_my_buddy/blackboard/evaluation/evaluation.py:32-115`。
- B08：`docs/reference/cli.md` / Evaluation, routing and console；原来：用户发布仍提自动评价卡片。现在：删除自动卡片说明，保留实际可写的 buddy 设置、偏好、模型服务备注、并发与 Router 设置。依据：`src/hey_my_buddy/blackboard/evaluation/user_policy.py:PATCH_FIELDS, publish`。
- B09：`docs/reference/cli.md` / Evaluation, routing and console；原来：仍说明 Host 维护评价证据与卡片的字段、种类和边界。现在：整段删除；当前 Host 接口没有该维护面。依据：`src/hey_my_buddy/blackboard/evaluation/evaluation.py; src/hey_my_buddy/protocol/transport.py:METHOD_MAP`。
- B10：`docs/reference/cli.md` / Error codes；原来：旧控制台只读会话错误码仍列为可返回。现在：删除已不生成的 CONSOLE_READ_ONLY 条目。依据：`src/hey_my_buddy/console/server.py`。
- B11：`docs/reference/cli.md` / 0.16.0 maintenance commands；原来：升级回应仍列 privateMigration，漏掉归档与旧 skill 清理。现在：删除 privateMigration，按完整切换返回 archive、legacySkillCleanup 等当前字段。依据：`src/hey_my_buddy/install/upgrade.py:776-778; src/hey_my_buddy/install/skill_install.py:install`。
- B12：`docs/reference/workflow.md` / Configuration and routing；原来：Router 仍读评价卡片，编码 harness 漏列 claude。现在：Router 读偏好、模型备注与程序事实；补列 claude。依据：`src/hey_my_buddy/blackboard/routing/router.py:INSTRUCTIONS, FAST_INSTRUCTIONS, render_prompt; src/hey_my_buddy/protocol/schemas.py:CODING_HARNESSES`。
- B13：`docs/reference/workflow.md` / Configuration and routing；原来：reroute 永远保留最初配置硬约束；评价表或设置的版本变化使路由作废。现在：最初 buddy 字段仅在 buddyLocked 时约束继续；发布时验证当前选中 buddy，失效最多重新选择一次，不以共享版本变化作废。依据：`src/hey_my_buddy/blackboard/tasks/workflow.py:1292-1316,2638-2646; src/hey_my_buddy/blackboard/routing/decision.py:1148-1157,1198-1267,1318-1414`。
- B14：`docs/reference/workflow.md` / Configuration and routing；原来：决策仍持有 table_revision 与 configuration_revision。现在：删除决策版本字段，只保留实际微任务 buddy_json/buddy_revision/validated_buddy_revision 与路由绑定。依据：`src/hey_my_buddy/blackboard/store/db.py:workflow_runs, evaluation_decisions, decision_requests; src/hey_my_buddy/blackboard/tasks/workflow.py:compact`。
- B17：`docs/reference/workflow.md` / Review, cancel and recover；原来：安装可跨等待 Host 的微任务保留控制文件和检出，升级后重发旧命令可取得旧回执。现在：待执行的 schema-18 归档升级先拒绝未关闭工作与未回收检出，通过后仅带设置，不带工作历史或控制回执。依据：`src/hey_my_buddy/install/upgrade.py:346-375,402-449,727-751; src/hey_my_buddy/blackboard/store/migrations.py`。
- B22：`docs/reference/cli.md` / Error codes；原来：列出已不存在的 INTEGRATION_REQUIRED。现在：删除错误码条目；缺少 target/notRequired 在请求边界以 INVALID_ARGUMENT 拒绝。依据：`src/hey_my_buddy/blackboard/tasks/workflow.py:accept`。
- B23：`docs/reference/cli.md` / Error codes；原来：列出已不存在的 PACKET_TOO_LARGE。现在：删除错误码条目。依据：`src/hey_my_buddy/protocol/schemas.py; src/hey_my_buddy/blackboard/tasks/workflow.py:_bounded_input`。
- B24：`docs/reference/cli.md` / Review eligibility；原来：说明源码已完全删除的 harness-verify 与 review-check。现在：删除旧命令与执行器说明，保留当前 capabilities/harnesses 资格读取契约。依据：`src/hey_my_buddy/cli/main.py:METHODS; src/hey_my_buddy/protocol/transport.py:METHOD_MAP`。
- B25：`docs/reference/cli.md` / Messages and inquiry；原来：通道可返回 transport-busy。现在：删掉当前通道不再生成的 transport-busy。依据：`src/hey_my_buddy/buddy/harnesses/c_two_live.py:1050-1074`。
- B33：`docs/reference/objectives.md` / 0.16.0 human presentation contract；原来：概览有层级解释弹窗、要求删除时间线图例。现在：删除不存在的层级弹窗与其旧说明，按实际保留时间线图例弹窗。依据：`apps/console/src/MacroTaskOverview.tsx:281-325; apps/console/src/MacroTaskTimeline.tsx:994-1009`。
- B37：`docs/reference/cli.md` / Evaluation, routing and console；原来：Router 设置与快照带代码持有的 revision。现在：删除设置版本字段，快照用 eventCursor，设置后写生效。依据：`src/hey_my_buddy/blackboard/evaluation/evaluation.py:console_core; src/hey_my_buddy/blackboard/evaluation/user_policy.py:publish`。
- B38：`docs/reference/workflow.md` / Buddy and routing；原来：冻结设置版本、写者闸门与读者租约会阻止路由或历史读取。现在：删除版本、闸门和租约说明，保留真实派发与 attempt 证据。依据：`src/hey_my_buddy/blackboard/routing/decision.py; src/hey_my_buddy/blackboard/tasks/workflow.py:_routing_history`。
- B39：`docs/reference/workflow.md` / Buddy and routing；原来：Schema-14 微任务迁移为 unlocked。现在：删除已经移除的历史转换说明。依据：`src/hey_my_buddy/blackboard/store/migrations.py`。
- B40：`docs/reference/usage.md` / Operating rules；原来：所有设置保存都有 revision 冲突。现在：共享 Router 与 buddy 设置后写生效，commandId 约束重放。依据：`src/hey_my_buddy/blackboard/evaluation/user_policy.py:publish; src/hey_my_buddy/blackboard/evaluation/evaluation.py:user_policy_publish`。
- B41：`docs/reference/usage.md` / 6. Decide, continue or reroute at the boundary；原来：要求读取 fallback。现在：删除不再提供的 fallback 字段。依据：`src/hey_my_buddy/blackboard/routing/decision.py:get; src/hey_my_buddy/blackboard/tasks/workflow.py:_routing_view`。
- B44：`docs/reference/workspace-lifecycle.md` / Workspace lifecycle operations；原来：继续会作废旧结论的回收权，回收还要判断结论是否当前。现在：按终态 concluded 判断回收，不再声称写结论之后可继续。依据：`src/hey_my_buddy/blackboard/tasks/workflow.py:_cleanup_reasons; src/hey_my_buddy/blackboard/tasks/host_conclusions.py:current`。
- B48：`docs/reference/usage.md` / 1. Install once；原来：要求 macOS/Linux、Node 20+ DSH runner，harness 只列 DSH/ZCode。现在：按当前 Python 范围与四个原生 harness 写要求，删除已退役 Node runner 的要求，不扩大平台验证声明。依据：`pyproject.toml:5; src/hey_my_buddy/buddy/harnesses/dsh/adapter.py:39-57; src/hey_my_buddy/protocol/schemas.py:40`。
- B51：`docs/reference/cli.md` / Defaults and bounds；原来：保存的 quick 在升级时自动换成 brief。现在：删除已不存在的 quick 升级换算；当前设置仅接受 brief、standard 与 deep。依据：`src/hey_my_buddy/blackboard/store/migrations.py:read_settings, write_settings; src/hey_my_buddy/blackboard/routing/router_settings.py:PRESETS, validate_router_settings_patch`。

## 其余与源码不符的说明

- B01：`docs/reference/cli.md` / Invocation；原来：启动器每次都经 uv 选择 Python 3.12。现在：安装、升级和开发源模式使用 uv，日常调用使用固定运行时解释器。依据：`skills/hey-my-buddy/scripts/hey-my-buddy:4-27`。
- B02：`docs/reference/cli.md` / Service；原来：capabilities 返回带 adapter 字段的统一 Adapter report。现在：区分连接件报告 adapters 与按 harness 记录的健康，列出 refresh/harness 参数。依据：`src/hey_my_buddy/blackboard/service/service.py:339-370; src/hey_my_buddy/buddy/harnesses/registry.py:capability_report`。
- B03：`docs/reference/cli.md` / Micro task lifecycle；原来：编码 harness 与 command/external 共用 adapter 参数，宏任务只列两种加入方式。现在：编码 buddy 用完整 harness/provider/model/effort，command/external 保留 adapter，并补列 macroTaskOf。依据：`src/hey_my_buddy/protocol/schemas.py:normalize_workflow_spec, normalize_presentation`。
- B04：`docs/reference/cli.md` / Micro task lifecycle；原来：executionWorkspace 接受 cwd 与 integrator。现在：这两个字段从顶层 cwd 与提交 Host 派生，在 executionWorkspace 内给出会被拒绝。依据：`src/hey_my_buddy/protocol/schemas.py:normalize_execution_workspace, normalize_workflow_submit`。
- B05：`docs/reference/cli.md` / Host workflow fields；原来：结论只记录在执行旁边，不改微任务状态。现在：conclude 进入终态 concluded，hostConclusion.executionStatus 保存原执行状态。依据：`src/hey_my_buddy/blackboard/tasks/workflow.py:4186-4378; src/hey_my_buddy/blackboard/tasks/host_conclusions.py:15-22`。
- B06：`docs/reference/cli.md` / Display summary；原来：控制台先用 Worker 结果作标题，且没有提交 title 或宏任务字段。现在：意图标题取显式 title、任务首个非空行、未命名委派；结果摘要单独显示。依据：`src/hey_my_buddy/blackboard/tasks/macro_tasks.py:236-256; apps/console/src/macro-task-display.ts:245-263; apps/console/src/TaskDetails.tsx`。
- B15：`docs/reference/workflow.md` / Manual continuation and takeover；原来：接管之后已结束的微任务仍可继续。现在：accepted 与 concluded 在新 Host 下仍为不可继续的终态。依据：`src/hey_my_buddy/blackboard/tasks/workflow.py:3084-3175,2770-2772`。
- B16：`docs/reference/workflow.md` / Review, cancel and recover；原来：写结论后仍保留 delivered 等微任务状态，结论会被后续继续作废。现在：微任务与 task 都进入 concluded，原执行状态保存在结论内；相同请求可重放，不能再继续、验收、取消或写另一条结论。依据：`src/hey_my_buddy/blackboard/tasks/workflow.py:4186-4378,2770,3236,3880; src/hey_my_buddy/blackboard/tasks/host_conclusions.py:current`。
- B18：`docs/reference/workspace-lifecycle.md` / Workspace lifecycle operations；原来：结论不改变 failed/cancelled/delivered，且要判断结论是否当前。现在：结论原子地写入并进入 concluded，不再判断结论是否当前；原执行状态单独保留。依据：`src/hey_my_buddy/blackboard/tasks/workflow.py:4186-4378; src/hey_my_buddy/blackboard/tasks/host_conclusions.py:current`。
- B19：`docs/reference/usage.md` / 2. Choose the execution workspace；原来：executionWorkspace 参数示例列 cwd 与 integrator。现在：参数说明明确 cwd 与 integrator 派生且拒绝重复输入。依据：`src/hey_my_buddy/protocol/schemas.py:normalize_execution_workspace, normalize_workflow_submit`。
- B20：`docs/reference/usage.md` / 8. End a micro task without accepting it, or retry a reclaim；原来：结论只记录在执行结果旁，没有终态。现在：说明 concluded 终态、原执行状态位置和不可继续。依据：`src/hey_my_buddy/blackboard/tasks/workflow.py:4186-4378,2770-2772`。
- B21：`docs/reference/cli.md` / Micro task lifecycle；原来：helper 被说成接受 submit 的所有顶层字段。现在：改为 helpers 的实际白名单，包含顶层 integrator 布尔值。依据：`src/hey_my_buddy/protocol/schemas.py:105,637-682`。
- B26：`docs/reference/cli.md` / Error codes；原来：BUDDY_CONFLICT 总是要求保留最初四元组。现在：仅 buddyLocked 时最初 buddy 字段构成这道约束。依据：`src/hey_my_buddy/blackboard/tasks/workflow.py:2638-2646`。
- B27：`docs/reference/cli.md` / Micro task lifecycle；原来：必须两种路由模式都不可用才到 Host。现在：资格按本次选定模式判断，不会自动切换模式。依据：`src/hey_my_buddy/blackboard/routing/router.py:buddy_problem; src/hey_my_buddy/blackboard/routing/router_sequence.py:resolve`。
- B28：`docs/reference/workflow.md` / Buddy and routing；原来：手动换 buddy 总被最初字段约束。现在：仅 buddyLocked 的最初字段不可修改。依据：`src/hey_my_buddy/blackboard/tasks/workflow.py:2638-2646`。
- B29：`docs/reference/objectives.md` / Read shapes；原来：拒绝验收共用 workflow.acknowledged，漏列 Host 结论标记。现在：分列负面评审、验收与结论的当前事件。依据：`src/hey_my_buddy/blackboard/tasks/macro_tasks.py:HOST_MARKERS`。
- B30：`docs/reference/objectives.md` / Presentation；原来：独立微任务折叠区文字还是未归档委派（已加载 N）。现在：按当前 MacroTaskList 文字写历史独立委派（N）。依据：`apps/console/src/MacroTaskList.tsx:238-246`。
- B31：`docs/reference/objectives.md` / 0.15.1 presentation and metadata；原来：helpers 只能通过根微任务验收。现在：独立 helper 可由根的控制能力通过 targetRunId 验收，进度仍只计根。依据：`src/hey_my_buddy/blackboard/tasks/workflow.py:_target_run, accept; src/hey_my_buddy/blackboard/tasks/macro_tasks.py:371-375`。
- B32：`docs/reference/objectives.md` / 0.15.1 presentation and metadata；原来：统计是折叠段落且三项文字与源码不符。现在：按当前弹窗与有条件显示的累计项更新。依据：`apps/console/src/MacroTaskOverview.tsx:295-311`。
- B34：`docs/reference/usage.md` / 4. Submit the micro task once；原来：前文说 grouping 已删除，后文仍把 workspace 描述为可用的 grouping。现在：明确 workspace 被拒绝。依据：`src/hey_my_buddy/protocol/schemas.py:SUBMIT_FIELDS, WORKFLOW_SUBMIT_FIELDS`。
- B35：`docs/reference/usage.md` / 6. Decide, continue or reroute at the boundary；原来：控制台只能只读显示决策。现在：控制台有经过认证的控制接口；Host 使用 CLI。依据：`src/hey_my_buddy/console/server.py; src/hey_my_buddy/blackboard/tasks/workflow.py:_authorize`。
- B36：`docs/reference/workspace-lifecycle.md` / Workspace lifecycle operations；原来：get 会提供此次操作的 commandId。现在：get 提供 revision，调用方为新命令选稳定 commandId。依据：`src/hey_my_buddy/blackboard/tasks/workflow.py:compact, scope_amend, workspace_resolve`。
- B42：`docs/reference/cli.md` / Error codes；原来：ANCESTOR_TERMINAL 未列 concluded。现在：将 concluded 纳入禁止继续的终态祖先。依据：`src/hey_my_buddy/blackboard/tasks/workflow.py:3491-3503`。
- B43：`docs/reference/usage.md` / 4. Submit the micro task once；原来：submit 的 JSON 示例包含被拒绝的 executionWorkspace.cwd/integrator。现在：从示例删除这两个字段。依据：`src/hey_my_buddy/protocol/schemas.py:normalize_execution_workspace`。
- B45：`docs/reference/objectives.md` / Submission metadata；原来：宏任务加入方式只列 objective 与 objectiveId。现在：补列当前 macroTaskOf，通过同项目与原 Host 的运行关联加入。依据：`src/hey_my_buddy/protocol/schemas.py:normalize_presentation; src/hey_my_buddy/blackboard/tasks/macro_tasks.py:120-137`。
- B46：`docs/reference/objectives.md` / Read shapes；原来：counts 的完整字段列表漏列 accepted。现在：补列 accepted，与实际根微任务计数一致。依据：`src/hey_my_buddy/blackboard/tasks/macro_tasks.py:371-375`。
- B47：`docs/reference/cli.md` / Execution records；原来：仅点名 dsh/zcode 和缺省 adapter 会被 execution-submit 拒绝。现在：概括为全部 coding harnesses，包含 codex 与 claude。依据：`src/hey_my_buddy/blackboard/store/store.py:task_submit; src/hey_my_buddy/protocol/schemas.py:CODING_HARNESSES`。
- B49：`docs/reference/usage.md` / Operating rules；原来：备份与升级一律只有滚动 backups/current/、只要 idle 就可切换。现在：分清滚动备份和待执行的永久归档升级；后者要求工作关闭、停止已证实、检出已回收，只带设置进入新看板。依据：`src/hey_my_buddy/install/upgrade.py:346-397,727-751; src/hey_my_buddy/blackboard/store/backup.py:create`。
- B50：`docs/reference/workspace-lifecycle.md` / Workspace lifecycle operations；原来：accept 校验固定 Git refs，容易理解为要求引用当前仍指向原提交。现在：明确 accept 校验固定 Git 对象与清单绑定；引用存在且未移动的要求在 reclaim 处说明。依据：`src/hey_my_buddy/blackboard/tasks/workspace.py:1910-1999,2308-2343`。

以上共 51 处名称之外的说明修正；同一项终态或参数规则在不同页面各计一处，单纯字段改名不计入本数。

## 拿不准

- U01：`docs/reference/usage.md` / 2. Choose the execution workspace：2026-10-12 与 2026-10-13 的 macOS 权限测量表，包括 Unix socket、共享内存、进程列表与网络的实际表现，无法仅由当前源码复验；它已明确标作历史测量，原样保留，本次不运行真实 harness。

- U02：`docs/reference/usage.md` / Background work that outlives the turn：外部 App heartbeat 注册、原任务唤醒及工具可用性不属于本仓库实现，无法由源码确认；保留其 Host 操作规则，未把它写成 hey-my-buddy 已提供的调度能力，也未尝试调用外部账户。

## 改过的标题与锚点

- `docs/reference/cli.md`：`#buddy-cli-reference` → `#hey-my-buddy-cli-reference`（`buddy` CLI reference → `hey-my-buddy` CLI reference）。
- `docs/reference/cli.md`：`#governed-goal-lifecycle` → `#micro-task-lifecycle`（Governed goal lifecycle → Micro task lifecycle）。
- `docs/reference/cli.md`：`#work-objectives` → `#macro-tasks`（Work objectives → Macro tasks）。
- `docs/reference/cli.md`：`#0151-objective-presentation` → `#0151-macro-task-presentation`（0.15.1 objective presentation → 0.15.1 macro task presentation）。
- `docs/reference/usage.md`：`#4-submit-the-governed-goal-once` → `#4-submit-the-micro-task-once`（4. Submit the governed goal once → 4. Submit the micro task once）。
- `docs/reference/usage.md`：`#8-end-a-goal-without-accepting-it-or-retry-a-reclaim` → `#8-end-a-micro-task-without-accepting-it-or-retry-a-reclaim`（8. End a goal without accepting it, or retry a reclaim → 8. End a micro task without accepting it, or retry a reclaim）。
- `docs/reference/workflow.md`：`#governed-goal-lifecycle` → `#micro-task-lifecycle`（Governed goal lifecycle → Micro task lifecycle）。
- `docs/reference/workflow.md`：`#configuration-and-routing` → `#buddy-and-routing`（Configuration and routing → Buddy and routing）。
- `docs/reference/objectives.md`：`#work-objectives-and-delegation-timelines` → `#macro-tasks-and-micro-task-timelines`（Work objectives and delegation timelines → Macro tasks and micro task timelines）。

负责页内指向 objectives.md 的链接已改为 macro-tasks.md；cli.md 指向 workflow.md 的 #configuration-and-routing 已改为 #buddy-and-routing。范围以外的链接及锚点由 Host 汇总处理。

## 源码自己的问题（未改）

- S01：`src/hey_my_buddy/protocol/schemas.py:629-634`，normalize_buddy 用 CODING_HARNESSES 接受 claude，但拒绝信息仍写“dsh, zcode or codex”，漏列 claude。

- S02：`src/hey_my_buddy/cli/main.py:172-173`，静态 EPILOG 的执行路径介绍仍只点名 dsh/zcode 被拒绝，实际 task_submit 拒绝全部编码 harness；本文改为 coding harnesses，源码未动。

- S03：`apps/console/src/macro-task-types.ts:8-10`，accepted 的注释写 roots + helpers，实际 `blackboard/tasks/macro_tasks.py:371-375` 仅计 accepted 根；文档按实际实现保留根进度。

- S04：`apps/console/src/macro-task-types.ts` 的 TimelineEventKind 不含 conclude，而 `blackboard/tasks/macro_tasks.py:HOST_MARKERS` 将 workflow.host_concluded 映射为 conclude；文档列出服务实际生成的标记，类型未改。

## 未完成的改名操作

`git mv docs/reference/objectives.md docs/reference/macro-tasks.md` 被当前权限层拒绝：无法创建 `<repository>/.git/worktrees/<checkout>/index.lock`，错误为 Operation not permitted；没有通过其他工具或路径重试、移动或复制该文件。五页内容与本说明已写入授权文件，原 objectives.md 保留在原路径。需要 Host 在能写 Git 元数据的环境执行这条 git mv，再核对链接；本次不能作为完整交付验收。
