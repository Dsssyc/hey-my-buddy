# ADR-032 执行计划

本文落实开发者于 2026-11-05 接受的 [ADR-032](../../decisions/032-host-interface-delegation-loop.md)及同日经 Host 转来的实施决定，源码依据固定为 `29cc34c5701a9cb72eda5920bf364c564c456c7c`。第 11 节记录已定内容与验证边界。本批包含核心契约、Git 交付与三方合并、项目与 none、运维与角色调用、控制台；引用寿命和新问题唤醒推迟。本轮仅修订计划，未改代码、运行测试或模型、访问日常黑板。

直接依据是 `docs/decisions/032-host-interface-delegation-loop.md:22–99,133–148`、`docs/design/backlog.md:53,191–214,224,232,250`、`AGENTS.md:9–19,39–41,51–65` 和 `CONTEXT.md:185–220,250–258`。`tmp/storage/skill-inventory/report.md:17–75` 与其 `help/` 是入口盘点，`tmp/storage/adr032-review/report.md:7–163` 是第三版之前的审查线索；它们不取代已接受的决定，例如旧审查要求保留 Host 控制凭证、私有执行目录的建议均已被 ADR-032 改定。

Host 提供的 2026-11-05 日常数据：900 个微任务的控制代均为 1，未使用 takeover；hostId 有 42 种写法；exclusiveResources 未使用；requiredCapabilities 仅见于 481 个内部路由任务，没有 Host 提交用例；buddy_locked=true 有 9 个微任务，日期为 2026-10-26 至 11-03。本轮没有复查。按已定内容删除 Host/helper 的 exclusiveResources、requiredCapabilities 和 buddyLocked；内部 Router 的能力需求保留。

下表使用文件缩写减少重复：`W` = `src/hey_my_buddy/blackboard/tasks/workflow.py`；`S` = `src/hey_my_buddy/protocol/schemas.py`；`DB` = `src/hey_my_buddy/blackboard/store/db.py`；`BS` = `src/hey_my_buddy/blackboard/store/store.py`；`SV` = `src/hey_my_buddy/blackboard/service/service.py`；`MT` = `src/hey_my_buddy/blackboard/tasks/macro_tasks.py`；`WS` = `src/hey_my_buddy/blackboard/tasks/workspace.py`；`CS` = `src/hey_my_buddy/console/server.py`；`CL` = `src/hey_my_buddy/cli/main.py`；`BL` = `src/hey_my_buddy/cli/blocking.py`；`PC` = `src/hey_my_buddy/protocol/client.py`；`CT` = `src/hey_my_buddy/protocol/contracts.py`；`FE` = `apps/console/src/`。形如 `W:965–1000` 均指本段列出的实际文件与行号；新增文件名是实施写入建议，不是现有实现证据。

## 1. 每个入口与调用的去处

只保留一份服务操作实现，按受众裁剪 CLI 与帮助，不为删掉的名字留转发别名。普通 Host 帮助列十个动词及 help、console；运维命令另组；凭角色凭证开放的调用有自己的参数模型。当前 52 个 METHODS 加本地 help 共 53 个入口，依据 `CL:34–91`、`src/hey_my_buddy/protocol/transport.py:49–103`。下表逐个覆盖；新建的 create、amend 在表后列出。

| 现有 CLI 方法 | 新去处 | 要改的实际调用方与依据 |
| --- | --- | --- |
| ping | 删除 CLI；保留内部探活 | transport.py:306 的 attach 仍用命名 ping；PC:40–41 与 SV:153 的升级准入保留。 |
| health | 用户运维 CLI；内部诊断共用 | SV:312–343、install/upgrade.py:128,154；Host 的路由前置读取改为 submit/await 携带原因，运维仍可明确调用。 |
| harnesses | 用户运维 CLI；控制台共用 capabilities | CS:94、console/reads.py:106、FE/api.ts:513–523；更新 help 受众，不删原生发现与能力读取。 |
| accounts | 只给控制台服务端的 accounts | CS:93；当前未找到 FE 独立调用，保留白名单的只读缓存入口，不把 CLI 存在算作 Host 需求。 |
| harness-set | 用户运维 CLI；控制台共用 harness_set | CS:95、FE/api.ts:529–540；用户选择路径的能力保留。 |
| quota-redetect | 用户运维 CLI；控制台共用 quota_redetect | CS:96、FE/buddies/harness-retry.ts:23、SV:623–648；用户设置与冷却重试的幂等不改成 Host 回应 id。 |
| runtime | 用户运维 CLI | SV 的 runtime_info、PC 的命名调用、docs/reference/operations.md 的运行时诊断；不成为 Host 日常前置步骤。 |
| install | 用户运维 CLI，本地执行 | CL:440–442、install/skill_install.py；安装按旧运行时发旧版本接受的运维请求。 |
| paths | 用户运维 CLI，本地读取 | CL:443–445、install/skill_install.py；保留位置诊断。 |
| storage-plan | 用户运维 CLI；控制台共用 storage_plan | CS:113、FE/api.ts:488–490；保留显式生成清理计划。 |
| storage-apply | 用户运维 CLI；控制台共用 storage_apply | CS:114、FE/api.ts:498–504；保留固定 planId、确认及运维重放。 |
| worker-sessions | 用户运维 CLI | SV:55 的服务操作及 blackboard/tasks/worker_sessions.py；原生会话操作继续要求已有身份与停止证据。 |
| submit | Host 动词；Lead 凭证变体 | W:1708–1860、buddy/roles/texts/lead.txt:3–11；改新显式字段、CLI 派生新建键及 none 分支。 |
| get | Host 动词；Lead/Worker 限域读取；控制台共用 | W:1991–2026、CS:108、FE/task-detail/client.ts:30–39；新增宏任务/项目列表，角色凭证不能借这些选择器扩读。 |
| decide | 删除 CLI 与旧命名操作，能力进 continue | W:2183–2290、SV:723–730；helper 审批、边界回答、autoContinue 进入 continue 分支。 |
| continue | Host 动词；Lead 凭证变体 | W:2828–3010；合并边界、scope 与现场处置，换 buddy 统一 route 或完整名字。 |
| takeover | 删除入口、命名操作与恢复路径 | W:3330–3435、PC:157–158、CL:125；旧代令牌轮换保证由 ADR 明确放弃。 |
| cancel | Host 动词；Lead 凭证变体 | W:3438–3469；保留有序收尾、后代停止、真实停止证据，删除 Host commandId。 |
| accept | Host 动词；Lead 凭证变体 | W:3853–3970、FE/task-detail/AcceptancePane.tsx:68–72 的读取；绑定 artifactId，改交付整合提示与三方合并核验。 |
| conclude | Host 动词；Lead 凭证变体 | W:4442–4530；保留失败/取消/未验收的结束与独立部分产物，none 只结束固定结果记录。 |
| reclaim | Host/Lead 入口删除；服务自动回收；搬移定位进 relocate | W:4726 起、blackboard/tasks/reclamation.py:18–37；已有周期重试继续用，新位置只由运维 relocate 的 cwd 提供。 |
| scope-amend | 删除 CLI/旧操作，进 continue | W:4957–5040；新范围只随新回合生效，历史授权不追认。 |
| goal-amend | 删除 CLI/旧操作，进 amend | W:4910–4955；改以 macroTaskId 定位，不再借宏任务内某个 run 的控制凭证。 |
| workspace-resolve | 删除 CLI/旧操作，进 continue | W:5159–5240；restore/adopt/abandon 可单独机械执行，只有同时给续接输入才开回合。 |
| await | Host 动词；Lead 限域等待 | CL:454–463、BL:301–425,450–540,607–664；五个底层读取保留，删完整命令模板；本批不改返回条件。 |
| suggest | Worker/Lead 的角色凭证调用 | W:5790–5814；保留 body 与本回合身份，Host 从 get 读，不列入普通 Host 帮助。 |
| macro-task-list | 删除 CLI；控制台与 get 共用读取 | CS:728–729、MT:379–480、FE/api.ts:435–445；改空宏任务与项目种类。 |
| macro-task-timeline | 删除 CLI；控制台服务端读取 | CS:730–736、FE/api.ts:455–471；保留时间轴读取，不成为 Host 新动词。 |
| execution-cancel | 删除 CLI；控制台/内部保留 task_cancel | FE/buddies/MaintenanceView.tsx:48–70、CS:101、BS:1248；微任务仍只能走 cancel。 |
| execution-retry | 删除 CLI 与无保留调用方的命名 task_retry | BS:1293–1401、SV:902–903、PC:62–63 仅实现/包装该入口；未找到保留的产品调用或开发使用证据，不建工具。 |
| execution-acknowledge | 删除 CLI 与无保留调用方的命名 task_acknowledge | BS:1402 起、SV:905–906、PC:65–66 仅实现/包装该入口；微任务复核继续走 accept，不建工具。 |
| status | 删除 CLI；await/执行进程/控制台内部 task_get | BL:514,520,642、buddy/runtime/execution.py:538、CS:841。 |
| list | Host 读取并入 get；Lead 保留凭证分页 list | buddy/roles/texts/lead.txt:5、W:1105–1108、micro_task.py:372–388；删 hostId 过滤，不新增历史检查工具。 |
| result | 删除 CLI；await/控制台内部 task_result | BL:322,561、CS:843；Host 精读走 get，不再输出 resultCommand。 |
| artifacts | 删除 CLI；内部角色读取保留 | PC:131–132、W:71–101；Host 所需固定产物与结果由 get 给出，不新增检查工具。 |
| events | 删除 CLI；await/角色内部 events_read | BL:512、PC:123–124；事件游标仍用于等待与 Lead，不用 Host 编号。 |
| wait | 删除 CLI；await 内部 task_wait | BL:522–524、PC:68–69；独立等待容量继续保留。 |
| watch | 删除 CLI；await 内部 events_wait | BL:519、PC:126–129；不把等待资源并入控制资源。 |
| inquire | Host 动词；角色限域观察共用 | blackboard/tasks/inquiry.py:70–98、BS:2702–2727；提问只给 runId/question，机制推出 inquiryId；用回复 id 读旧问题。 |
| messages | 删除 CLI；控制台及角色 message_list | CS:109、FE/task-detail/client.ts:46、PC:119–120；get 可带已存问询。 |
| message-get | 删除 CLI；Host 读并入 inquire；底层 message_get 保留 | PC:116–117、W:83,98、inquiry.py:70–90；保留问题关联与去重，不增另一套消息身份。 |
| wait-capacity | 删除 CLI/单独操作，读数并入 health | SV:97,335、WaitAdmission.stats:126–129、CT:225；内部直接读统计，不保留空壳方法。 |
| console | Host 便利入口；用户打开控制台共用 | CL:446–453、cli/console_cli.py:122,137,172–175；浏览器会话身份照旧独立。 |
| console-snapshot | 删除 CLI 与命名 console_snapshot | PC:135–136、SV:429 是包装；HTTP 经 CS:487 直接调用 console/reads.py:77–122 的投影，保留实际调用链，不建诊断工具。 |
| user-policy-publish | 删除 CLI；只给控制台服务端 user_policy_publish | CS:97、FE/use-editor.ts:143、SV:524–525；保留 require_console，不删用户设置发布。 |
| selection-request | 删除 CLI 与无保留调用方的命名 selection_request | SV:664–668 是 routing/decision.py:240 起的唯一产品包装；内部 workflow 路由继续用协调器，不新建路由研究工具。 |
| selection-get | 删除 CLI；控制台服务端保留 | CS:104、FE/task-detail/client.ts:50、SV:670–671；不新增开发工具。 |
| selection-list | 删除 CLI；控制台服务端保留 | CS:105、SV:673–674；保留控制台白名单，不新增开发工具。 |
| model-catalog-refresh | 删除 CLI；控制台服务端 | CS:106、FE/buddies/CatalogView.tsx:160、SV:676–702；目录事实仍供内部路由。 |
| buddy-list | 删除 CLI；控制台服务端 | CS:107、FE/use-buddy-history.ts:158、SV:704–710；完整 buddy 名由目录事实解析，help 说明表达式。 |
| restart | 用户运维 CLI；安装内部 service_control | CL:89、install/upgrade.py:128–154；保留执行进程跨服务重启的语义。 |
| stop | 用户运维 CLI | CL:90、SV 的 service_control；停止未知不能报成停止完成。 |
| help | Host/运维/角色的本地自助入口 | CL:14–15、cli/cli_help.py:1–27,94–119；由模型生成参数、合法组合与拒绝信息，不启动服务。 |

新增 `create` 独立创建宏任务及初始目标，新增 `amend` 以 macroTaskId 修订目标，新增运维 `relocate` 只收新位置 cwd、由服务读项目标记定位。修改 CL 的受众注册与帮助、transport、CT、SV、PC；新增和删改的契约与 CLI 接线在核心任务内闭合，relocate 阶段负责自己的新增接线。依据 ADR:22–30,37,89–96、MT:151–195、W:4910–4955，以及第 11 节 D7 的决定。

本轮只读检查 src/ 和 tools/，没有发现 task_retry、task_acknowledge、selection_request、console_snapshot 撤下 CLI 后仍有产品调用方，也未取得实际开发使用证据，因此不安排新的 tools/ 工具；不能把包装函数和文档例句当作使用证据。若 Host 之后提供真实使用证据，再按 ADR 第 10 条为该功能定去处。本轮不查日常板。现有 `tools/export_board_sample.py` 保留并适配 schema 35。

### 控制台 HTTP 路由

当前路由分支见 `CS:708–748,803–846,852–889`，本地入口与静态资源见 `CS:659–696,750–780`。下表逐条列 HTTP 去处；通用 POST 里的每个业务操作在下一表单列。

| 路由 | 新去处与要改的调用方 |
| --- | --- |
| GET /launch/{ticket} | 留控制台会话兑换，CS:673–696；与 Host 控制文件无关。 |
| GET /assets/{path} | 留静态资源，CS:717–718,761–780；随前端构建更新。 |
| GET / 或空路径 | 留页面入口，CS:722–723,750–759。 |
| GET /api/console | 留服务端投影，console/reads.py:77–122、CS:782–795；改 Host 标签；执行问题仍只作为可读事实，不加新问题唤醒。 |
| GET /api/runtime-version | 留控制台版本诊断，CS:797–801、FE/api.ts:480–481。 |
| GET /api/macro-tasks | 留 macro_task_list，CS:728–729、FE/api.ts:435–445；移除 hostId 参数，加入空组与项目种类。 |
| GET /api/macro-tasks/{id}/timeline | 留 macro_task_timeline，CS:730–736、FE/api.ts:455–471；空宏任务返回空时间轴与目标。 |
| GET /api/macro-tasks/{id}/goals | 留 macro_task_goal_history，CS:738–740、FE/api.ts:473–477；不要求存在首个微任务。 |
| GET /api/tasks/{id} | 留 task_get/task_result，CS:837–846、FE/api.ts:425–428；显式传递 none 的结果型产物。 |
| GET /api/tasks/{id}/attempts/{attempt}/session | 留会话记录读取，CS:741–744,829–835、FE/api.ts:429–432；不把会话记录当验收依据。 |
| GET 其他页面路径 | 留 CS:748,761–780 的单页应用/静态资源规则。 |
| GET 未知 /api/* | 保留 404，CS:746–747；删除 CLI 不添加等价 HTTP 绕行入口。 |
| POST /api/command | 留白名单命令边界，CS:855–889、FE/api.ts:407–424；服务端附加 consoleAuthority，浏览器不能自填。 |

| POST operation | 新去处、调用方与调整 |
| --- | --- |
| accounts | 仅控制台，CS:93；现有缓存读取保留。 |
| capabilities | 控制台与运维/内部共用，CS:94、FE/api.ts:513–523。 |
| harness_set | 控制台与用户运维共用，CS:95、FE/api.ts:529–540。 |
| quota_redetect | 控制台与用户运维共用，CS:96、FE/buddies/harness-retry.ts:23。 |
| user_policy_publish | 仅控制台，CS:97、FE/use-editor.ts:143；保留用户设置身份。 |
| reminder_settings_set | 仅控制台，CS:98、SV:499–522；不随 Host revision 删除用户设置版本。 |
| maintenance_start | 仅控制台，CS:99、FE/buddies/MaintenanceView.tsx:48–58；维护调用身份不改。 |
| maintenance_pending | 仅控制台，CS:100；保留维护待处理读取。 |
| task_cancel | 控制台/内部，CS:101、FE/buddies/MaintenanceView.tsx:68–70；微任务的底层绕过继续拒绝。 |
| domain_rename | 仅控制台，CS:102、SV:480–484、FE/buddies/MaintenanceView.tsx:48–61；仍要求 console 身份。 |
| domain_merge | 仅控制台，CS:103、SV:486–497、FE/buddies/MaintenanceView.tsx:48–61。 |
| selection_get | 控制台读取，CS:104、FE/task-detail/client.ts:50。 |
| selection_list | 控制台读取，CS:105；CLI 撤下后不删白名单。 |
| model_catalog_refresh | 控制台，CS:106、FE/buddies/CatalogView.tsx:160。 |
| buddy_list | 控制台，CS:107、FE/use-buddy-history.ts:158。 |
| workflow_get | 控制台与 get 共用，CS:108、FE/task-detail/client.ts:30–39；更新新回复模型。 |
| message_list | 控制台与角色内部共用，CS:109、FE/task-detail/client.ts:46。 |
| macro_task_stop | 仅控制台，CS:110、W:3471 起；保留用户急停与 Host 有序 cancel 的不同事实。 |
| macro_task_deletion_preview | 仅控制台，CS:111、blackboard/tasks/macro_task_deletion.py:99–119；空宏任务也预览。 |
| macro_task_delete | 仅控制台，CS:112、macro_task_deletion.py:569–644；删目标、幂等关系及本批新增行，none 的 cwd 不得回收。 |
| storage_plan | 控制台与用户运维共用，CS:113、FE/api.ts:488–490。 |
| storage_apply | 控制台与用户运维共用，CS:114、FE/api.ts:498–504。 |
| console_access_set | 控制台本地操作，CS:45,364–403；不经过新的 Host 模型。 |
| console_logout | 控制台本地操作，CS:45,403–409；保留本会话撤销。 |
| console_session_revoke | 控制台本地操作，CS:45,370–409；保留其他浏览器会话撤销。 |

### 角色凭证调用

现有允许集为 `W:71–112` 的 12 项基础调用与 10 项 Lead 增补；入口认证先于 handler，见 `SV:137–170,216–246`。下面列每一项的去处；“保留底层”不表示恢复已撤下的普通 CLI 名字，await 经私有操作注册表组合读取，角色工具也只在凭证允许的范围内可达。

| 现有角色调用 | 新去处与必须改的调用方 |
| --- | --- |
| ping | 保留凭证可达的内部探活，W:74；不授予 Host 写权限。 |
| health | 保留内部/运维诊断的角色边界，W:75；不成为 Worker 的 Host 入口。 |
| capabilities | 保留能力存在性读取，W:76；不扩成修改用户设置。 |
| workflow_get | get 的 Worker 本任务/Lead 名下变体，W:77,92,1097–1104；新增集合选择器不能逃出归属。 |
| workflow_suggest | suggest 的角色入口，W:78,93,5790–5814；服务根据执行凭证固定 attempt。 |
| task_get | await 与执行事实的限域底层，W:79,94；外部只读选择器同样验谱系。 |
| task_result | 限域结果底层，W:80,95；固定回合结果与旧文件产物都可读。 |
| artifact_list | 限域产物底层，W:81,96；new result form 显式解析。 |
| events_read | 保留限域事件底层，W:82,97；沿用当前 Lead 事件/等待机制，新消息统一与门铃本批不做。 |
| message_get | 限域读取，W:83,98；Host 名字并入 inquire 不删角色底层。 |
| message_list | 限域读取，W:84,99；归属不由调用者过滤条件决定。 |
| inquiry_observe | 限域 inquire，W:85,100；不把提问当续派输入。 |
| workflow_submit（Lead） | Lead submit 变体，W:106,1708–1718；宏任务与 owner 从凭证推出，禁止再授委派权。 |
| workflow_decide（Lead） | 进 Lead continue 的边界分支，W:106；删除旧操作后同时改允许集。 |
| workflow_continue（Lead） | 保留 Lead continue，W:106；仍只处理名下微任务及合法 helper 目标。 |
| workflow_cancel（Lead） | 保留 Lead cancel，W:106；保留后代停止核验。 |
| workflow_accept（Lead） | 保留 Lead accept，W:107；固定 artifactId 与整合目标仍必填。 |
| workflow_conclude（Lead） | 保留 Lead conclude，W:107；不能用结案抹去未确认停止。 |
| workflow_reclaim（Lead） | 移交服务自动重试，W:107；Lead 角色文本不再教手工回收。 |
| workflow_scope_amend（Lead） | 进 Lead continue，W:107；未来范围随新回合生效。 |
| workflow_workspace_resolve（Lead） | 进 Lead continue，W:108；机械处置不必启动模型。 |
| task_list（Lead） | 保留凭证分页 list，W:108,1105–1108；Lead 文本第 5 条及 task_list 查询一起改，禁止普通 Host 用它代替 get。 |

`buddy/roles/texts/lead.txt:3–11` 必须随新字段和新方法同步；这是产品角色说明，不交给 Host 任务书补救。`W:1066–1111` 的选择器所有权检查与 `W:1031–1064` 的失效凭证拒绝保留，删除 Host 凭证不能改成“没认出角色就按 Host 处理”。

### 命名内部操作与调用

为避免漏掉没有 CLI 名字的操作，以下逐项覆盖 `CT:27–211` 的 59 个 BuddyControl 方法、`CT:219–225` 的 3 个 BuddyWait 方法、`CT:233–245` 的 5 个 ExecutionLive 方法。与前表同名的是同一实现，不另建副本；现有注册同步改 `SV:40–97`、transport、PC。只在服务内部直接调用的协调器方法随所属操作改写，不保留废弃命名契约。

| 现有内部操作 | 去处与调用方 |
| --- | --- |
| BuddyControl.ping | 保留；transport.py:306 attach、安装探活。 |
| BuddyControl.health | 保留；install/upgrade.py:128,154、用户运维，合入 waitCapacity。 |
| BuddyControl.capabilities | 保留；控制台/运维/角色内部读取，console/reads.py:106。 |
| BuddyControl.accounts | 保留控制台缓存读取；CS:93。 |
| BuddyControl.harness_set | 保留用户运维/控制台；CS:95。 |
| BuddyControl.harness_prepare | 保留执行专用；buddy/runtime/execution.py:528,613；attempt/generation/nonce/command receipt 均非 Host 控制。 |
| BuddyControl.quota_redetect | 保留用户运维/控制台；SV:623–648。 |
| BuddyControl.service_control | 保留安装、运维与检查清场；install/upgrade.py:128–154、cli/checks.py:781。 |
| BuddyControl.console | 保留便利入口；cli/console_cli.py:122,137,172–175。 |
| BuddyControl.console_snapshot | 删除命名操作及 PC 包装；HTTP 快照仍经 CS:487 调 console/reads.py:77–122 的实际投影。 |
| BuddyControl.reminder_settings_set | 保留控制台；SV:499–522。 |
| BuddyControl.maintenance_start | 保留控制台与维护实现；CS:99。 |
| BuddyControl.maintenance_pending | 保留控制台；CS:100。 |
| BuddyControl.domain_rename | 保留控制台；SV 的 console 检查保留。 |
| BuddyControl.domain_merge | 保留控制台；SV:486–497。 |
| BuddyControl.storage_plan | 保留运维/控制台，仍是显式磁盘检查。 |
| BuddyControl.storage_apply | 保留运维/控制台，仍绑定固定清理计划。 |
| BuddyControl.worker_sessions | 保留运维与会话身份读取。 |
| BuddyControl.runtime_info | 保留用户运维与安装诊断。 |
| BuddyControl.task_get | 保留；execution.py:538、BL:514,520,642、CS:841。 |
| BuddyControl.task_list | 保留 Lead 分页；Host 改 get 集合读取，不新增开发历史工具。 |
| BuddyControl.task_result | 保留；BL:322,561、CS:843。 |
| BuddyControl.task_cancel | 保留控制台维护取消/内部；W:7707–7731 的微任务重定向拒绝改为无控制凭证版本。 |
| BuddyControl.task_retry | 删除；SV:902–903、PC:62–63 的公开包装撤下后无保留产品调用方，不建开发工具。 |
| BuddyControl.task_acknowledge | 删除；SV:905–906、PC:65–66 的公开包装撤下后无保留产品调用方，微任务仍走 accept。 |
| BuddyControl.task_wait | 删除 CT:104、SV:908–909 及控制资源登记；PC:69 与 transport.py:85 的产品调用都选 wait 资源，BuddyWait.task_wait 保留。 |
| BuddyControl.execution_progress | 保留执行专用；execution.py:373、PC:72–102；attempt 栅栏与进程身份不动。 |
| BuddyControl.execution_result | 保留执行专用；buddy/runtime/receipts.py:35、PC:104–113；none 也走同一回执路径。 |
| BuddyControl.execution_maintenance | 保留执行专用；execution.py:801；不并入 Host continue。 |
| BuddyControl.message_get | 保留角色/问询内部；PC:116–117、W:83。 |
| BuddyControl.message_list | 保留控制台/角色；CS:109、PC:119–120。 |
| BuddyControl.inquiry_observe | 保留并承载 inquire 读旧问询；inquiry.py:70–98。 |
| BuddyControl.artifact_list | 保留角色读取；PC:131–132，增加结果型产物；不新增开发工具。 |
| BuddyControl.events_read | 保留 await/角色内部；BL:512、PC:123–124。 |
| BuddyControl.user_policy_publish | 保留且限控制台；SV:524–525、CS:97。 |
| BuddyControl.selection_request | 删除无保留调用方的命名包装；SV:664–668、routing/decision.py:240 起；workflow 的内部路由协调路径保留。 |
| BuddyControl.selection_get | 保留控制台读取；SV:670–671、FE/task-detail/client.ts:50。 |
| BuddyControl.selection_list | 保留控制台读取；SV:673–674、CS:105。 |
| BuddyControl.model_catalog_refresh | 保留控制台；SV:676–702。 |
| BuddyControl.buddy_list | 保留控制台；SV:704–710。 |
| BuddyControl.workflow_submit | 保留，改 Host/Lead 模型、派生键与 none；W:1708–1860。 |
| BuddyControl.workflow_get | 保留，加入宏任务/项目集合读取；W:1991–2026。 |
| BuddyControl.workflow_decide | 删除命名操作；W:2183–2290 的有效能力进入 workflow_continue。 |
| BuddyControl.workflow_continue | 保留，统一续接各变体；W:2880 起。 |
| BuddyControl.workflow_takeover | 删除命名操作与实现；W:3330–3435。 |
| BuddyControl.workflow_cancel | 保留，删除 Host 凭证/命令号；W:3438–3469。 |
| BuddyControl.workflow_accept | 保留，固定产物与三方合并；W:3853 起。 |
| BuddyControl.workflow_conclude | 保留；有请求/产物时回应对应 id，无产物时 runId 即可，终态重发读原结案；W:4442 起。 |
| BuddyControl.workflow_reclaim | 删除 Host/Lead 命名入口；机械回收留服务内部，新位置交 relocate。 |
| BuddyControl.workflow_goal_amend | 删除旧命名操作；能力进入新增宏任务 amend 操作。 |
| BuddyControl.workflow_scope_amend | 删除旧命名操作；能力进入 workflow_continue。 |
| BuddyControl.workflow_workspace_resolve | 删除旧命名操作；能力进入 workflow_continue。 |
| BuddyControl.workflow_suggest | 保留角色调用；W:5790–5814。 |
| BuddyControl.macro_task_list | 保留控制台及 get 共用读取；MT:379–480。 |
| BuddyControl.macro_task_timeline | 保留控制台；CS:730–736。 |
| BuddyControl.macro_task_goal_history | 保留控制台与 get 的目标历史读取；CS:738–740、MT:198–213。 |
| BuddyControl.macro_task_stop | 保留仅控制台急停；W:3471 起。 |
| BuddyControl.macro_task_deletion_preview | 保留仅控制台；空宏任务也走同一删除范围模型。 |
| BuddyControl.macro_task_delete | 保留仅控制台；删新增关系行，保留冻结证据的既有删除边界。 |
| BuddyWait.events_wait | 保留；BL:519、PC:126–129，独立容量。 |
| BuddyWait.task_wait | 保留；BL:522–524、PC:68–69，沿用返回条件与独立等待容量。 |
| BuddyWait.wait_capacity | 删除单独操作；SV:126–129 的统计直接供 health 使用。 |
| ExecutionLive.send | 保留服务到执行的问询推送；protocol/execution.py:332。 |
| ExecutionLive.observe | 保留服务到执行的观察；protocol/execution.py:338。 |
| ExecutionLive.capabilities | 保留执行能力读取；protocol/execution.py:342。 |
| ExecutionLive.close | 保留执行连接生命周期；CT:242，不能因删 Host 控制删掉活连接清理。 |
| ExecutionLive.stop | 保留执行停止；protocol/execution.py:324，仍凭执行身份与 nonce。 |

## 2. Host 请求与回复的形状

所有 CLI 入口用请求模型声明，未知字段与不适用字段拒绝；服务在入口验证，内部传已验证对象。授权、归属和执行方式没有隐式默认；CLI 的 output、waitSeconds、浏览器等本地选择用各自模型。控制台专有与执行内部入口继续使用其现有校验，不因 C1 重做。依据 ADR:43–66、AGENTS.md:19、SV:137–170。

写动作的回复只带对象 id、状态、actions 和所回应的 id；首次创建或重发另说明是否已经存在。submit 重发须显示原任务实际的 access、buddy、checkout，不能让 Host 误以为新参数改了原授权。完整任务、执行身份、停止、问题和证据视图留给 get/await。actions 只列当前合法的动词，不生成完整命令，也不保证以后仍合法；需要回答的 requestId/artifactId/turnId 由服务原样给出，不用“最近看过”的缓存补。替换点是 W:745–898、cli/cli_views.py:250–283、BL:334–418 与 routing/boundaries.py。

| Host 动词 | 请求字段与合法组合 | 回复与拒绝/状况 |
| --- | --- | --- |
| create | cwd 为已存在目录的绝对路径；title 非空且最多 200 字符；goal 非空且最多 1 MiB。项目由 cwd 认，新建键由机制推出。 | macroTaskId、状态、actions=amend/submit/get；同键回原对象并明说已经存在，不改原目标。字段错拒绝，目录不可访问给事实。依据 ADR:23,30,44–47、S:434–450。 |
| amend | 只收 macroTaskId、goal；允许空宏任务。与当前 goal 相同即重发，不同就追加一版，不要求目标回应 id、revision 或 commandId。 | macroTaskId、目标历史身份、状态及 amend/submit/get；保留全部修订历史，两个 Host 的不同目标都可追加。依据 W:4910–4955 与开发者 D2 决定。 |
| submit | 必填 cwd、macroTaskId、title、task、buddy、checkout、access、expectedSeconds；macroTaskId 为已建宏任务 id 或字面量 standalone；条件字段见下表。 | runId、状态、actions；初始为 await/inquire/cancel，遇边界则给 requestId 与 continue/cancel。同键一律回原对象及实际授权/执行选择，不重做检出、路由或扩大授权。依据 ADR:44–66、W:1746–1785。 |
| await | runId 必填；waitSeconds 可选，1–86400，沿用当前本地等待窗口；不再用提交 requestId 选择。 | 同一 run 的边界、提醒、交付或窗口到期；给此刻所需 id 与动作，交付给 artifactId。完整视图可带当前执行问题，但本批不新增“问题出现就返回”。依据 BL:450–540,607–664。 |
| get | runId、macroTaskId、cwd 恰选一个；cwd 选项目。集合沿用 1–100 分页与游标；run 可选 includeAudit/routingHistory，集合不能带单任务专用字段。 | 单任务完整视图；宏任务的目标、成员与状态，允许成员为空；项目的宏任务、独立微任务与状态。各项给自身 id/actions，不再按 Host 名归属。依据 ADR:30,38,52–54、W:1991–2026、MT:379–480。 |
| inquire | 三选一：runId 观察；runId+question 提问；runId+回复给出的 inquiryId 读旧问题。question 非空且最多 4000 UTF-8 字节，question 与 inquiryId 不同填；timeoutMs 为 100–5000，waitMs 为 0–30000。 | runId、inquiryId、问题状态/答案；提问身份由 runId+question 机械推出，Host 不编 id。相同问题返回原问询，读取不存在 id 拒绝；无能力或执行不可达给事实。依据 inquiry.py:70–98、BS:2702–2727，与本轮问询决定。 |
| continue | runId 加所回应的 requestId/artifactId；失败后续接可用该回合的固定结果 id，缺少结果产物时用已有 turnId，三者恰选适用的一种。input、helper 决定、buddy/access/writeScope、现场处置按变体组合。 | 同一 runId、新 continuation/turn 身份及 actions；同一回应对象原样动作重发返回原动作，不同动作冲突；不另造失败请求、命令号或计数器。 |
| accept | runId、artifactId、note；Git 产物 target={path,ref} 或非空 notRequired 恰一项。none 只允许 notRequired。真实调整用 adjusted+note；hostPaths 配 beforeCommit。 | runId、artifactId、integrationId、已验收状态；原样重发读原记录，改目标/动作参数冲突。未整合是核验拒绝，回收受阻单独报告，不撤销验收。依据 W:3853–3909、ADR:86–88。 |
| conclude | runId 定位，沿用结案 note；有待结束的产物时带 artifactId，无产物时 runId 即可，不要求 requestId/turnId 或新发 id。仅失败、取消或已交付未验收且停止成立时结束。 | runId、conclusionId、状态；终态重发读原结案，不再封存第二份部分产物；活 helper、未处置现场与停止未知仍拦住不合法结束。依据 W:4442–4483。 |
| cancel | runId，加原有可选 note；不带回应 id/commandId。保留有序收尾。 | runId、同一个停止请求的身份/状态与 await/get；重发回同一停止请求，不改写首个请求，也不称请求成功就已停止。依据 W:3438–3469、ADR-022:20–29。 |

问询身份可用 `inq-` 加带版本前缀的规范化序列 `(runId,question)` 的 SHA-256，question 保留原文，不加 attempt、当前时间或随机值。同一微任务同问题只取第一次的状态/答案；再问须换说法。沿用 messages 的 UNIQUE(task_id,inquiry_id) 与消息投递/答复链，原记录 id 不回填；新增问询前先查已存在记录。读旧问询用回复 id 调现有 message_get，不重注入问题、不要求再交原文；get 的问询列表也可找回 id。messages.payload_hash 仍被原生问询日志的 questionSha256 核验读取（BS:561,1567），不能因幂等键可推导就误删这条执行证据。

help 只讲当前受众的方法和模型事实，不启动服务；console 沿用 cli/console_cli.py 的本地浏览器/等待与服务 open/status/close，Worker 凭证仍在本地动作之前被拒（CL:446–453）。完整 buddy 使用目录中已记录的名字查出完整四元组，不用冒号拆目录清理后的 buddyId（catalog/catalog.py:649–655）。

### submit 的合法组合

公共边界沿用 title 的 200 字符、task 的 1 MiB、expectedSeconds 的 1–86400 整数上限；expectedSeconds 必填且只用于提醒/路由输入，不停止工作。buddy 必填 route 或完整名字；cwd 决定项目和 none 的执行位置，Git 两种检出仍区分源 cwd 与实际执行 cwd。依据 S:23–43,219–240,434–450,468–536、ADR:55–76。

| 条件 | 必填及不适用字段 |
| --- | --- |
| worktree/existing + read | 必须 Git，inputs 显式给出且可空；writeScope、accessNote 不出现。保留冻结输入与只读共享规则。 |
| worktree/existing + write | inputs 显式给出；writeScope 非空、相对检出根，只有显式 ["."] 才授权整个检出；accessNote 不出现。 |
| worktree/existing + full | 上一行再加必填 accessNote，非空单行且最多 10000 UTF-8 字节；文件产物仍核 writeScope。 |
| none + read/write/full | Git 或普通文件夹皆可；inputs、writeScope、base、targetRef 不出现。full 必填 accessNote，其他档位不带理由；write/full 的文件改动未封存。 |
| delegationAuthority | 只在明确授权时写 true，要求完整 buddy 和 full；Lead 不可再授予。归属从执行凭证推出，不能让 Lead 自填 Host 名或宏任务。 |
| fixes | 只在修正时出现，指向已验收的同项目/宏任务微任务，并保留 Lead 名下限制；原验收不撤回。 |

inputs/writeScope 在提交边界沿用最多 256 项与现有越界校验；后续大量继承路径仍由 ADR-031 第 16 条的封存引用带过，不重新塞满请求。检出从 Host 当前工作树固定输入，不接受任意 commit 基线；none 不复制或固定文件输入。依据 S:324–371、WS:696–745,1011–1053。

### continue 的变体

| 变体 | 请求组合与执行边界 |
| --- | --- |
| 打回交付/失败后续接 | runId + 对应 artifactId 或已有固定结果/turnId + 非空 input（最多 1 MiB）；省略 buddy 沿用当前，不自动重新路由。旧结果可读但不能再当本次待验收产物。 |
| 回答边界 | runId+requestId+decision=approve/decline，可有 note；decline 不带 helpers/autoContinue。批准 helper 才带完整 helpers 包与必填 autoContinue，其他情形该字段不出现。 |
| 批准 helper | 最多 8 个完整任务包：title/task/cwd/buddy/checkout/access/expectedSeconds 和适用 inputs/writeScope/accessNote；归属由父请求/凭证推出，不填 Host 名、提交 requestId 或宏任务。固定请求身份避免重发多建 helper。 |
| 改派/换手 | buddy=route 或完整名字，删除 reroute；显式改派沿用 note 要求。带委派权的 Lead 仍需完整显式 buddy，不允许 route；普通显式选择不再有终身锁定。 |
| 换权限/范围 | 随新回合的 input 给 access、适用的 accessNote、writeScope；省略保留当前授权，变更只从本回合生效；read 不带 writeScope。历史 scope/输入/封存不改，相关执行停止仍要证明。 |
| 活 helper | 有活 helper 时必填 helperPolicy=keep/cancel，无活 helper 不出现；cancel 后确认停止再复用共享检出。 |
| 现场处置 | runId+requestId，resolution 指定 restore/adopt/abandon、observedFingerprint、适用 paths/note；只处置不带 input 不开回合，同时带 input 则先处置成功再续派。部分处置后剩余现场签发新 requestId，不在同一已回应请求上再写另一动作。 |

helper 控制继续用现有 runId/targetRunId 分开根与目标，并在每次调用按谱系复核（W:2828–2844）；不因删控制文件放开 Lead 名下任务。组合模型与执行由同一个微任务在 W:2183–2290,2828–3010,4957–5040,5159–5240 接好线，已经出界的现场不能靠扩大未来 scope 追认。处置与续派共享一次动作身份，后续准备失败按已提交的处置与 continuation 事实恢复，不能重复处置或静默开第二回合。

### 拒绝、状况与合法动作

输入错误指出字段路径、允许值与配套字段；角色越界指出限域；回应 id 已处理或已被替代是冲突，要求 get，不替换为新的 id。不可达、权限拒绝、排队/路由原因、停止未知是有原因事实的状况，不伪装成参数错误。必须先运维时直接给具体命令与 operations 的 Host 运维一节；submit/await 自带路由原因，不要求先 health。依据 ADR:81–96、protocol/transport.py:115–131。

| 当前事实 | 所需 id 与动作 |
| --- | --- |
| 准入/执行中 | runId；await/inquire/cancel；get 可读。 |
| 活跃边界 | runId+requestId+kind；continue/cancel。 |
| 交付且停止成立 | runId+artifactId；accept/continue/conclude。 |
| failed/cancelled 且停止成立 | runId；continue 回应该失败回合已有结果 id/turnId，conclude 无产物无需另发 id。 |
| accepted/concluded | runId 与原 integrationId/conclusionId；只读；重复原动作读原事实。 |
| 空宏任务 | macroTaskId；amend/submit/get；不画执行完成或待验收。 |
| 执行问题/停止未知 | get/await 完整视图保留问题事实位置与当下 actions；未证明停止就不给 accept，await 本批不因新问题返回。 |

execution_issues/service_issues 当前按 stage 替换（DB:250–264），selectedAttempt.issues 已可读。新问题一次返回、问题消费状态、Lead 新消息统一与门铃均推迟，不新增表、订阅或恢复游标；本批只保留回复中的事实位置，沿用 BL:450–540 的等待条件。

## 3. 删除字段、控制凭证与回应栅栏

| 删除或合并的字段 | 原保证与本批处理 |
| --- | --- |
| submit.requestId、submissionToken、submission_verifier | 原来恢复初代 Host 控制能力（W:1724–1785,1952–1969）；改机制推出新建键，同键返回原对象，旧行不回填。 |
| hostId/sourceHostId/newHostId、owner | 自报名字及同名加入检查删除（S:58–60,219–224、MT:172–174）；归属从 helper/Lead 谱系读，只核同项目，不再换一个自报标签。 |
| ownerGeneration/controlToken/controlFile/takeover | 原单 Host 互斥与接管旧代围栏明确放弃（W:965–1000,3330–3435）；不加新会话锁、令牌或隐含控制缓存。 |
| expectedRevision、Host commandId | 回应请求/产物时改确切 requestId/artifactId，失败续接必要时用已有 turnId；cancel、无产物 conclude、amend 按各自自然事实重发，不新发 id。 |
| expectedGoalRevision | amend 只比较当前 goal 的内容；相同回当前，不同追加。保留目标历史主键/排序，不再限制两个 Host 并发修订。依据 W:4919–4944。 |
| expectedScopeVersion | 新范围与本次续派绑定，仅从该回合生效；scope_version 留作历史主键，Host 不填数字。依据 W:4973–5015。 |
| harness/provider/model/effort | 合为 buddy，完整名字查目录仍得四元组与原有目录/启用校验，不接受部分约束。依据 S:516–524。 |
| buddyLocked/buddy_locked | 全删字段、列、冲突及原约束继承分支：W:1444,1487,2846–2855,6957、routing/router_boundary_data.py:269。省略 buddy 仍沿用当前；显式 route 可换模型。 |
| macroTask/macroTaskOf、宏任务归属默认 | create 独立创建；Host submit 明填 macroTaskId/standalone，Lead 从凭证推出。依据 S:434–451、W:1708–1718。 |
| executionWorkspace、base.kind/ref | 平铺为 checkout/access/inputs/writeScope，检出从 Host 当前工作树开始，放弃任意 commit 起点；每回合固定输入仍保留。依据 S:316–371。 |
| includeUntracked | 改 inputs 且显式可空，仅 Git 两种检出适用；none 不复制。依据 S:357–361。 |
| submit.targetRef | 删除输入，源工作树事实最多给候选；accept 仍明确目标，旧封存中的字段不改。依据 S:369–371、W:583–616。 |
| exclusiveResources | 删除 Host/helper 的外部资源互斥声明，放弃该保证，不另造机制；执行 cwd 准入与停止清理保留。依据 BS:835–859,1805–1810。 |
| requiredCapabilities | 删除 Host/helper 字段；permission:access 由机制加，内部 Router 的 decision 等需求保留。依据 routing/decision.py:418–432、BS:1676–1682。 |
| reroute | 换 buddy 一律 route 或完整名字；省略保持当前。依据 W:2912–2916。 |
| targetRunId | 不再承载 Host 控制能力，但 helper 操作继续区分根/目标并核谱系；Lead 不能借根 Host 动名下任务。依据 W:2828–2844。 |
| Host 自编 inquiryId | 提问改由 runId+question 推出，回复给 id；旧问询可按原 id 读取，不重写消息。依据 inquiry.py:70–90、BS:2702–2727。 |

控制文件的写、读和清理一起退役：CL:209–325,467–472，home.py:31–39，macro_task_deletion.py:418–460；安装只对私有 controls/submissions 目录作一次明确清理，用私有安装目录验证，不为此扩地板生成器。服务自己的 control.json、control-start/control-daemon/board-owner 锁照旧属于服务启动和所有权，不是 Host 控制文件（transport.py:144–164、install/upgrade.py:170,452–566、storage.py:110–118）。

服务删三元组、Host token 派生/恢复、控制代、takeover、hostId 比较和 waitingForHost 的按名聚合（S:375–403,454–503、W:965–1027,1587–1660,1952–1984,3330–3435、SV:182–214,741–747、DB:1496–1502）。路由有效性改查 current_routing_id、decision/turn/attempt 身份和状态，取消/改派仍挡晚到结果（W:1440–1511,5869–5873,6949–6964）。

Worker/Lead 的执行凭证保留：SV:216–246 先认证，坏凭证不降为 Host；W:1031–1111 检查活凭证、允许集、全部选择器与名下关系，落库前再核撤销。普通 Worker 不得执行 Host 写动作，Lead 只能操作名下微任务且不可再授委派权或改用户设置。agent_credentials、attemptId/generation/nonce/turnId 不随 Host 控制代删除（DB:831–845,1504–1510、W:6624–6642）。

当前 W:973 用 host_id 空值判 Lead，W:1002–1016 推 owner；删列后从 workflow_children 的根及 workflow_lead_tasks 推角色，不重复存 owner。新 Host 动作只记角色，重建 actor_id 的 NOT NULL/CHECK；Lead/console/service 的真实 id 保留。旧事件、封存输入与回执中的名字是历史证据，保留原字节，不再作当前授权。

回应事实优先放在已有 workflow_requests、workflow_artifacts、必要的 workflow_turns 上：一次回应身份指向已有决定、continuation、integration 或 conclusion 记录；事务内对未回应行条件更新，并以唯一约束防第二次消费。accept 与 continue 抢同一产物、approve 与 decline 抢同一请求都在被回应行上串行化。原样重发从领域记录比对动作参数并读原结果身份，不同动作拒绝；参数包括 input、helper 包、范围、权限、目标与 note，不能只比 input。缺少的实际参数补进所属领域记录；确实无法容纳时才提出新表及具体理由，不预设另一份回应表或第二份动作指纹。

部分现场处置关闭原 request，剩余现场签新 requestId；失败续接绑定已有固定结果或 turnId。cancel 重发回同一停止请求，无产物 conclude 终态重发回原结案，amend 按当前 goal 内容重发；这三类不制造回应令牌。升级前的关闭/验收/续派/结案事实继续从领域行读，commands 中 workflow.* 旧回执整类清除，不转换、不恢复旧格式重发。依据 DB:29–47,635–671,776–799,973–994 与本轮 D2/D4。

跨 Git I/O 的预检和事务重查保留，核的是当前 request/artifact/turn、谱系、停止证据、输入与目标指纹，不是重复校验同一 JSON（W:2244–2287,2930–2983,5199–5227）。内部 task_wait 的 tasks.revision、事件游标、目标历史与 scope 的主键按实际读者保留；退役的 workflow_runs.revision/expected_revision 改为这些具体事实，不留无人读列。

新建键的逻辑形式：create 为项目+规范化 title；submit 为 macroTaskId（独立则项目）+title+原文 task，用带类型/版本前缀的规范化序列化与 SHA-256，避免拼接歧义。title 沿 S:440 归一空白，task 保留原文；不混入时间、Host、随机 token、HEAD 或文件内容。键在准入事务受唯一约束保护，完成角色与请求形状校验后先查已有键，命中直接回原事实，不用新字段再做目录/能力/输入准备；未命中才冻结输入和路由（W:1746–1749）；同键即返回原对象，包括已结束对象及原 access/buddy/checkout，其他字段变化也不拒绝、不更新原授权。重做换标题，删除宏任务后同标题再建算新对象，不加 tombstone。

旧行的 creation_key 明确为空，部分唯一索引只覆盖非空键；不回填、不合并旧同名行、不建第二份 request_fingerprint。tasks.request_id 是既有执行/内部记录身份，不能与新的回应 requestId 混同。新建键按 ADR:44 由 CLI 推出；核心任务验证取得稳定项目身份、计算键与重发的完整调用链，不预设跨进程拼键协议。

## 4. schema 34 → 35 与库存

当前 DB:22 为 schema 34；store/upgrade.py:9–14,26–52 有四个已运行步骤。本批新增自足的 `src/hey_my_buddy/blackboard/store/schema_34_35.py`，带自身 DDL/常量/转换并写 35，不导入当前领域代码、不修改 30→34。地板仍为 schema 30 / 0.54.1，新板直接用当前 DDL。核心先交可运行步骤；本批在真实黑板首次运行前，后续阶段可扩展这一未部署步骤并同步新板 DDL，最后一个改存储的阶段统一重新生成一次库存，禁止每阶段另造一份。

| 当前形式 | 目标与存量处理 |
| --- | --- |
| workflow_runs.host_id/source_host_id/owner_generation/control_verifier/submission_verifier/request_fingerprint/buddy_locked | 整列删除；原 run/attempt/归组/Lead 关系不变。Host 名列共三处，另有 macro_tasks.source_host_id。依据 DB:499–535。 |
| macro_tasks.source_host_id | 删除，项目与目标历史保留；create 不依赖首个微任务。依据 DB:554–573、MT:151–195。 |
| workflow_routes.owner_generation | 删除，decisionId/runId、路由历史和当前状态保留；当前性按 current_routing_id 等事实核。依据 DB:588–594、W:1478–1480,6949–6964。 |
| workflow_runs.revision；requests.expected_revision/decision_command_id；continuations.expected_revision/command_id；children.decision_command_id | 删除 Host 计数/命令关系；保留请求关闭、helper 请求归属、续派输入与状态，用现有领域行的一次回应关联。依据 DB:29–47,527,540–548,635–671。 |
| scopes/conflicts/integrations/conclusions 的 command_id；conclusions.owner_generation/run_revision | 去无人读命令/控制代，保留对应领域证据与回应引用；不把旧命令迁成新账本。依据 DB:721–765,776–799,973–994。 |
| commands | 只清除 kind 为 workflow.* 的整类旧回执；执行、harness_prepare、运维、控制台仍读的其他行与整表保留。依据 DB:297–305、SV:588–617,637–645、command_receipts.py:16–64。 |
| tasks.owner、actor 约束 | 去自报 owner 输入/写者/显示后删无读者列；新 Host 只有角色，Lead/console 等仍需真实 id；旧冻结 actor 作为历史保留。依据 DB:141,280–292,721–734,776–799,973–992。 |
| 可执行规格与 execution_workspace_json | 在显式升级中去撤下的 Host/helper 字段，明确当前 checkout/access/归属；内部 Router requiredCapabilities 保留。原任务文字、封存 turn 输入/receipt/seal 及摘要不改，不在读取时按年代猜形式。 |
| exclusiveResources、resource_claims | 去 Host/helper 声明与无读者 exclusive 分支，cwd/执行准入仍用 resource_claims。旧 released 资源事实按实际保留读者处理；升级不转换活 claim，安装的 idle/停止边界见下文。依据 DB:307–317、BS:835–859,1805–1810。 |
| 新建键 | 宏任务/微任务各有可空键，旧行全部 NULL；非空键部分唯一索引；不回填、不为旧同名行加转换分支。 |
| 项目结构化记录 | 显式 git/folder；Git 仍凭 WS:232–278 的标记，folder 按规范化路径认。旧项目从已存项目/固定输入转为 git，不看现在目录是否还有 .git；数据不塞 meta。 |
| 宏任务/微任务项目事实 | 空宏任务有自己的创建项目位置/种类；run/回合固定当时项目种类和 checkout。沿用 macro/standalone 的项目 id 互斥存法，冻结种类是独立事实；不能从当前项目类型重解释旧行。依据 DB:501–503,532–535、MT:216–235。 |
| folder 后来有 Git | 首次以 Git 使用时沿用原 projectId 并写项目标记，新任务为 git，旧 folder/none 不改；folder 搬移/改名即新项目，不加追踪别名。依据 ADR:69–76。 |
| workflow_runs/turns/continuations 的 workspace CHECK | 按显式 checkout 分支：existing/worktree 要固定 Git 输入；none 记录 cwd/access/身份，无 Git 列，不制造空 manifest。旧行从已存输入/意图取现有检出方式，不从现场推。依据 DB:469–490,516–535,635–677。 |
| workflow_artifacts 新结果形式 | 增加明确的固定回合结果引用，绑定 run/turn/attempt、文件位置及摘要，不能用虚假 output_commit/workspace_id 填旧 CHECK；既有 sealed-inline-v1/input-reference/record-reference 原义不变。依据 DB:850–918。 |
| 新 Git 输出形式 | 区分 Worker 提交、私有证据对象与固定 patch；旧 seal 的 digest 绑定内容不改，新形式有自己的判别与版本。依据 WS:1574–1588,2182–2213。 |

安装 preflight(contract_changed=True) 已要求空闲，拒绝未结束、未证实停止或待清理的 attempt（install/upgrade.py:67–101；store/work.py:7–22）。BS:2451–2458 在确认停止时释放 claim，未确认则保留，未创建进程路径也释放（BS:2658–2671）。因此合法升级库存不应有活的 held/retained exclusive claim；沿用上述安装边界拒绝非空闲或停止未确认的升级，不另写活占用转换或强行释放。不要把长期 Git workspace_reservations 与执行 claim 混为一类。

冻结输入、native checkpoint、receipt、物理 seal、补丁及它们的摘要保持原字节；仅新记录采用新形式。关系行增加回应引用不改物理封存。当前并无独立 Host 控制表可整张删，不能误删 commands、agent_credentials、resource_claims、workspace_reservations；删除的是其中的无人读列/分支与 workflow.* 回执库存。

### 样板黑板与生成器

依据 `packaging/generate-upgrade-fixture.py:1937–1968`、`tests/python/blackboard/store/upgrade_fixture.py:21–35,52–95`、`tests/fixtures/upgrade-floor/README.md:3–29`。每个实际转换/删除的存储形式在 fixture 有存量，缺少才扩生成器；纯整列删除不依值转换，已有该列就够，不补 takeover、控制代大于 1、控制/提交文件或 buddy_locked=true 场景。

| 形式 | 库存与核验 |
| --- | --- |
| 删列与 workflow.* 清理 | 检查现存 Host/控制/锁定等列确有库存；至少有 workflow.* 回执与仍要保留的其他命令行，证明整类清除与保留边界。不造跨升级旧命令重放库存。 |
| Host/Lead/helper 及 macro/standalone | 覆盖按谱系推角色、项目和归组的转换分支；目标历史与旧关闭状态保留，不因删名字改归属。 |
| 可执行规格中的删除字段 | requiredCapabilities 的 Host 空/非空与内部 decision 非空；exclusiveResources 空/非空均有对应 stock。当前 README:5 只写 exclusiveResources 空形式，必须由地板 writer 补非空；不能拿普通测试代替库存。 |
| 旧检出与工作区事实 | existing/worktree、owner/reference/pending、实际被重建 CHECK 触及的形式有行；NULL 新建键原样保留，不补同名/缺 title 去重场景。 |
| 旧输入/输出与版本 | 复用现有 v1/v2 派生库存、v3 地板及 schema33 的 v4 增量；经旧链到 34 后覆盖已有内容形式，原封存/文件摘要全不变。 |
| 领域回应与 actor | 实际改结构的请求、续派、验收、结案、范围/现场和 actor 形式有行；不从旧 commands 还原参数。新请求/产物/失败回合的互斥回应由当前 writer 测试。 |
| 新项目/空宏任务/none/新 Git 输出 | 用本批当前 writer 验新形式、新 CHECK 和读取；地板从未写这些形式，不伪造其来源。 |

地板能写的由其原写者生成；只有旧版写、地板携带的形式才从地板行派生并记录来源。生成器记录 writer commit、命令及输入/输出摘要，补量不改已冻结的升级步骤；升级链比较新板结构，转换分支先断言库存非空。controls/submissions 退役在一个私有状态目录的安装检查验证，和 fixture 存储形式覆盖分开。

## 5. 快照提交与整合验证

当前 `_commit_tree` 为确定性恢复使用固定快照身份与日期（WS:617–626）；seal 在树与 inputTree 不同时总造快照提交，另记观察到的 headCommit（WS:1572–1603）。Host 视图把 record.commit 当 outputCommit（W:529–582、cli/cli_views.py:130–133），控制台显示它（FE/task-detail/AcceptancePane.tsx:68–72）。这条链必须一起改，不能只改 skill 的合并例句。

新交付分开表达固定证据与整合来源：artifactId/记录摘要仍固定封存；完整的 Worker 自己的提交/提交区间经服务检查其基线、祖先与树覆盖关系后作为可整合来源；有未提交内容、Worker 提交只覆盖部分封存、或 resolved/Host 产物时，给已固定的累计 patch 及摘要作为整合来源。不能仅凭存在 HEAD 就称“整合这个提交”。原始输入可能含 Host 未提交内容和选入的 inputs，核验必须确保候选提交带的是应交付改动，不能把基线带入目标历史。

Worker HEAD 的树与实际 seal 树完全相同且对应正确输入时可复用该真实提交，不再无条件另造；需要合成对象才能固定未提交树、保住崩溃恢复时，仍可作为私有证据，交付明确提示应用 patch，不把它列为要合并的提交。恢复使用同一已选记录与摘要，不换成仓库身份/真实时间制造新对象，不替 Worker 改历史。新 seal 的记录形式应把 worker commit/证据 commit/patch 的含义分清；旧 seal 的 headCommit 只能在已有证据足够时给候选，否则诚实给 patch。依据 backlog:204–213、WS:1485–1511,1572–1620。

`WS:2182–2213` 当前还硬性要求非 inputCommit 的产物只有一个父提交且恰为最终 inputCommit，这正是合成快照的假设；不能在 seal 复用 Worker HEAD 后漏改这个读者。新形式用固定的 Worker 提交区间/共同基线与树、patch 关系核验，允许 Worker 的多个真实提交；旧 version=1 仍按它原来的父绑定核，不改旧记录使其看起来符合新规则。恢复与验收必须共用对应形式的验证，不能只改可见 outputCommit。

accept 继续显式目标 `target.path/ref` 或 notRequired；不把执行检出自己、源 cwd 的当前 HEAD 或回复候选当成 Host 的默认整合选择。干净三方合并以原目标与产物从共同基线合成的树为证，目标等于它即原样整合；不能仅以 artifact commit 为目标祖先判成功，也不能继续要求同一文件逐 blob 等于 artifact。当前 `WS:2232–2321` 的内容比较及 `W:4060–4105` 的 differing 拒绝是修改处，保留 adjusted+note 表达 Host 真正改过的内容。

三方核验按开发者 D5b 决定：目标 ref 顶端是包含产物的合并提交时，从其父提交证明整合前目标与产物来源；其他情形由 Host 明写 beforeCommit，服务机械核对祖先与共同基线。目标树等于该基线下的干净三方合并树才算原样，不能只看产物是祖先或逐文件相等。实施先在私有 Git 做实验，覆盖同文件不同行、cherry-pick、patch、删除、重命名，再接 W:3895–3903 与 WS:2242–2304；冲突或真实调整仍需 adjusted+note，hostPaths 仍只表达产物路径以外的真实 Host 补充。

### 验收和回收之后的引用读者

本批只交以下引用读者清单，不改任何引用删除路径。保留期限与读者能否脱离 Git 对象留后续，当前引用继续按现有生命周期保留；不安排本批的断引用实验或存储回收策略。

| 读者 | 当前读取及源码依据 |
| --- | --- |
| get/控制台审查与累计 patch | W:496–527,529–582；sealed-inline-v1 读已存 cumulativePatch，record-reference 从固定 patch 用 git apply --numstat 读路径，源码明确不打开 checkout/对象库。这里可确认展示这份 patch 本身不需要保留 Git 对象；seal 时生成累计 patch（WS:1606–1620）仍需要对象，不能混成一条读取。 |
| accept 与封存真实性校验 | WS:2150–2230,2232–2254；按 input/output commit、tree、parent、patch hash 核固定来源。未验收及验收的有效重放完成前不能破坏来源。 |
| 下一回合输入/共享只读输入 | WS:696–713,772–824,1104–1124；可引用旧封存作为输入选择。别的未结束任务还持有某封存时，原任务验收不使该读者消失。 |
| 现场处置与结案部分产物 | W:4587–4666,5213–5232、WS:1984–2011；要用原输入与固定产物做 resolve/累计 patch，开放的冲突及尚未结案的现场仍有读者。 |
| 仓库搬移定位与检出回收 | WS:281–313,2406–2468,2637–2765；用 project 标记、retained/input、分配 provenance 证明所回收对象。检出回收受阻期间不能删掉定位与清场证据。 |
| 存储计划与宏任务删除 | tasks/storage.py:192–228、tasks/workspace_bindings.py:26–42、macro_task_deletion.py:370–377；固定范围与输出引用名参与所有权判断，删除所需所有权事实仍有读者，本批保持此路径。 |
| 导出样本/升级验收 | tools/export_board_sample.py:204–257；复制所选记录引用的 manifest、output record、patch；复制的文件范围按现有导出规则，不为本批演练复制整个仓库；更早删除引用是否可行留后续。 |

后续再按 input/staged/outputs/resolutions/abandoned/retained 的实际读者确定寿命；本批不删引用、不套原生目录的三天常量，也不改用户项目历史。快照不再成为整合来源与引用累积是两项独立结果，本批只完成前一项和读者清单。

## 6. checkout: none 的执行与验收

none 的 Worker 实际 cwd 就是请求 cwd，黑板不准备检出、不复制输入、不调用 Git 快照/状态检查；即使 cwd 已有 .git，也按这一回合固定的 none 意义执行。用户要缩小能读到的内容，Host 自己建空目录再提交；权限档位按 harness 的原生机制落实，读取范围仍取决于原生 harness。相关分支穿过 `W:1587–1660,1863–1886,6104–6293`、`protocol/turn_input.py`、`buddy/runtime/execution.py`、结果导入与工作区清理，不能只在 submit 校验器接受一个新取值。

每回合结果固定为一份带 runId、turnId、attemptId、generation、结果文件位置及摘要的产物，artifactId 是它的回应/验收身份。复用现有 execution_result 的私有结果回执与 publication 路径，在服务核归属、来源及摘要后登记引用，不复制一份可能漂移的报告文本当第二份结果；跨进程传文件引用，不塞无界结果。completed 才是可验收结果；assistance/attention 保留固定本回合结果并另给 requestId，后续回合产生新 artifactId；打回以后旧结果仍可审但不再是当前可验收项。依据 `PC:104–113`、`W:7176–7194`、DB:850–906，新增判别不能要求 Git output_commit。

none 的 read 只交结果；write 明确允许在 cwd 改文件，结果、get/await 和控制台同时标明“文件夹的改动未封存”。full 适用同样的未封存提示和必填理由。不能把结果里写的文件路径当成已封存产物或已固定输入，不设置能误解为“零文件改动”的 diff。验收检查任务书与固定结果；纯回答时报告与结果可以同一份，公开术语与流程不得称已经得到三份独立证据。

none 的 accept 只收明确的 notRequired 理由，绑定确切 result artifactId 和核对 note；target/adjusted/hostPaths/beforeCommit 均不出现。Host 另行查看或整合文件不使它成为黑板封存的验收对象。none 的 accept/conclude/cancel 与宏任务删除只清黑板私有结果、凭证/会话/记录，绝不回收或递归删除 cwd。

none 不持有 worktree/existing 的长期 workspace_reservation，不制造空 manifest。执行层现有按 cwd 的准入/claim 仍按实际执行目录运行，停止之后按原规则释放；不额外添加文件夹互斥机制或宣称 none 提供多个写者的隔离。`BS:835–859,1683–1687` 的执行资源与 `DB:691–710` 的长期检出预约必须区分。把普通文件夹当固定输入、封存文件夹改动均留后续。

## 7. 控制台的变化

宏任务列表当前从 matched members 构造 groups（MT:432–458），项目显示 JOIN 首个 run/input artifact（MT:216–235）。改为以 macro_tasks 为主体 LEFT JOIN 聚合，独立微任务保留原分组。空宏任务显示目标和 0 个成员，无执行/验收标记；“全部”与文本搜索可见，active/host/review 不命中，排序沿创建/活动时间；零成员删除预览/执行合法。计数、分页、项目汇总与 activity marker 一起改，避免 inner join 丢组。

项目摘要显式带 kind 及路径/label；Git 项目各 worktree 仍同组，folder 按规范化路径分组，后来 git init 继续同 projectId；旧 none 结果仍按旧形式显示。新增信息进入 `FE/macro-task-types.ts:15–32` 的 Zod 解析、`FE/api.ts:435–477`、MacroTaskList/Overview、预览夹具与列表查询缓存；不能只改一个卡片而让顶层项目统计继续把文件夹当缺失 Git 资料。

当前归属改按谱系显示 Host 或某个 Lead，删除 hostId/sourceHostId/currentHostIds、ownerGeneration 与 controlFile 的显示和筛选：`micro_task.py:204–207,305–327,386–388`、`MT:46,268–275`、`SV:182–214`；前端 `actor.ts:35–36`、`console-data.ts:100`、`macro-task-types.ts:15–32`、`MacroTaskOverview.tsx:73,101`、`task-detail/DelegationPane.tsx:39–43`、`task-detail/parse.ts:281,490,520`、`types.ts:140,190–191` 一起改。不得从 harness/线程号重建 Host 身份；Lead 的真实归属 id 继续可见，历史动作记录不作为当前凭证。

微任务的回合、交付与验收页显式区分 Git 文件产物和固定回合结果；none 显示固定结果身份/内容/权限与“文件夹的改动未封存”，不显示空壳 commit/patch。Git 交付区分封存证据和 Worker 提交/patch 来源；更新 AcceptancePane、TurnPane、types/parse、中英文词条。执行问题显示已有事实，等待条件仍不变；停止未知不画结束。控制台先出暗色静态稿，开发者确认后再实现，按 ADR-029:21–35 做层次、形状、颜色与真实浏览器核验。

## 8. 文档改写清单（整合 Host 负责）

本微任务只列文档，由整合 Host 在实现形状稳定后改写；skill 与两份 Host 指南按 ADR:148 先给开发者看。产品角色文本由运维/角色阶段负责接线，整合 Host 不代写产品代码。每个 Markdown 段落一行。

| 文档 | 改写内容与依据 |
| --- | --- |
| skills/hey-my-buddy/SKILL.md | 十个动词加 help/console、最小委派圈与不变原则；字段细节转 help，运维只链 operations 的 Host 一节；去控制文件/接管/先 health/低层轮询。 |
| docs/reference/host-codex.md | 等待流程只组合 await/get；不再让 Host/监视者记控制代、revision、命令号；收尾用项目/宏任务 get 找待处理集合，保留原样交回等待结果的职责。 |
| docs/reference/host-claude-code.md | 同样更新后台 await、重发恢复与动作 id；保留原生等待/唤醒边界，不承诺尚未接入的跨回合门铃。 |
| docs/reference/usage.md | 先 create 可空宏任务、submit 显式字段、none、continue 变体、准确整合来源、accept 固定目标/notRequired、问题事实与重发；明确新问题唤醒推迟。 |
| docs/reference/workflow.md | 删控制能力/接管流程，新增 id 回应一致性、helper 与 Lead 谱系、未来 scope 与现场处置、结果型验收、真实停止与自动回收。 |
| docs/reference/operations.md | 新增写给 Host 的运维一节：逐命令的触发场景、须用户同意的具体事项、调用方式；覆盖 install/stop/restart、health/harness 路径与冷却重试、storage plan/apply、relocate 只收 cwd、运行时/位置/会话诊断。共享操作的能力不随 CLI 精简删除。 |
| docs/reference/cli.md | 按 Host/运维/角色列现行入口，由模型给字段事实；撤下旧名字，不留下兼容例句；不为无证据的用途保留开发工具例句。 |
| AGENTS.md | owner fencing 改为 Worker/Lead 执行权限与 request/artifact 一次回应、task/attempt 与实际停止；记录 34→35 为新第五步、现有导出工具及相应检查，不再要求 Host 控制代。 |
| CONTEXT.md | 按 ADR 实现节改项目、允许空的宏任务，新增不带检出的微任务；封存与验收区分文件产物和固定结果，三方交叉验证允许纯回答只有两样；删除接管，保留 buddy 换手/改派；预期时长不再称默认半小时。 |
| docs/reference/architecture.md、macro-tasks.md、workspace-lifecycle.md、console.md | 更新命名操作、schema/升级链、空宏任务与项目模型、Git/none 结果与归属显示；引用删除生命周期照旧；保留所有仍有效的所有权、停止、保密与删除边界。 |
| docs/reference/decision.md、evaluation.md、workers.md、harnesses.md、harness-integration.md、codex.md | 改低层 CLI/开发工具例句、路由问题读取入口、角色调用与字段名；不把跨 harness 原生事实统一或 Router 画像工作顺带塞入本批。 |
| docs/README.md、README.md、README.zh-CN.md | 索引随拥有页更新，README 中英文对齐文件夹/结果工作范围与验收边界；详细字段留在参考/help。 |
| docs/design/backlog.md、相关旧 ADR 的修订说明、docs/acceptance/ 下本批记录 | 整合 Host 在实施与实际验收后记录哪些已完成、哪些待定；ADR-004 的接管政策及 ADR-022 的默认时长按已接受 ADR-032 说明修订，不能删除仍有效的不变量。 |
| tools/ 的说明 | 只更新现有 export sample 的新 schema 输入、授权/路径/清场规则；本批不新建无实际使用证据的工具。 |

## 9. C1 同批，覆盖全部 CLI 入口

按本轮决定，C1 合入核心契约微任务：覆盖十个 Host 动词、help/console、全部保留运维命令和角色入口，一次删除 cli_help.py 的语法树推断，不留两套 help。控制台专有 POST 与内部操作没有 CLI help，本批继续原校验；共用 CLI 的服务操作复用同一请求模型，不在另一入口重复维护字段条件。Pydantic 已在 pyproject.toml:16–24 声明，internal_models.py:1–35 有现成约束/错误转换经验。

这样缩窄后的请求、授权组合、字段上限、help 与拒绝只改一遍，不先重写手工校验再第二次迁模型。内部模型的 populate_by_name/默认值配置不能直接套进新公开请求（internal_models.py:7–14）；未知字段、旧字段和不适用字段拒绝，授权/归属/执行方式显式。CLI 处理本地选项与机械身份，服务对跨进程请求验证一次，内部信任类型；各执行边界的身份、摘要、停止核验照旧。

检查模型能否实际准入/拒绝、help 是否完整且只有当前受众字段、角色边界是否保持，不逐字节保留旧错误先后，不写只复述字段声明的测试。docs/acceptance/wheels/second-wave.md:5–7 的“55 入口、6 批”是未实施旧计划，本批按保留 CLI 入口收敛；不存在 AST 未退役的备选落法。

## 10. 阶段与微任务

后续任务逐阶段在前一阶段封存的产出上开工，各自拥有所需的 workflow.py/service.py 接线与受影响测试，各任务交付前接通自己的调用，不把产品代码留给整合 Host。以下范围中的 W/SV 行号按本文源码基线指示函数片段，实施后以函数职责为准；同一文件的写任务按顺序，不并行。核心同时完成模型、schema、生命周期与 CLI/client，交付时形成受影响检查通过的可运行闭环；未完成的后续能力不称已可用，整批结束前不安装日常候选。

| 阶段 | 写入范围（未来微任务） | 依赖与并行 | 检查与交付条件 |
| --- | --- | --- | --- |
| P1 核心契约 | protocol 的 CLI 请求模型、schemas/contracts/transport/client、cli/main/blocking/cli_help/cli_views、home.py；store/db.py、schema_34_35.py、upgrade/安装保存验证；tasks/workflow.py 的认证/准入/读取/边界决定/续派/取消/验收回应/结案/范围/现场（W:965–1111,1587–2026,2183–3435,3438–3469,3853–3909,4442–4530,4910–5375,5790–5814）、macro_tasks/micro_task/command_receipts/inquiry；SV:137–246,429,664–809,902–909 及新增 create/amend 的注册与 handler，先完成 Git 项目的 create/amend/get 闭环；Router 的 buddyLocked 读者；fixture 生成器/库存及对应测试；pyproject.toml、uv.lock（版本元数据）、CONTRACT_VERSION、contract_signatures.json | 第一个实施任务；本任务自己接通全部改动并交付，不能把模型/schema 单独作为不可运行前置。验证 CLI 项目身份解析与新建键调用链；不与后续写任务并行 | 经 check runner 跑 tests/python/cli/test_cli.py、test_host_cli.py、test_blocking.py、test_blocking_commands.py、test_cli_views.py、tests/python/blackboard/service/test_workflow_validation.py、tests/python/blackboard/tasks/test_host_workflow.py、test_workflow_boundaries.py、test_workflow_routing.py、test_workflow_cancellation.py、test_inquiry.py、test_lead_owned.py、test_lead_grant.py、test_lead_review_boundaries.py、tests/python/blackboard/store/test_upgrade_chain.py、test_upgrade_fixture.py、test_domain_schema.py、新增 test_schema_34_35.py、tests/python/protocol/test_contract_version.py、test_ctwo_service.py。证明同键回原选择、旧行无键、workflow.* 清除、问询去重/读取、一次回应互斥与角色限域；版本/契约在本任务升 0.61.0 并钉签名。 |
| P2 核心删守卫的独立审查 | 只读核心交付的固定差异及其证据；审查记录进 docs/acceptance/，产品修复退回核心任务 | 核心交付、Host 读完后立即审；审查通过后才能向上叠后续阶段，不等整批末尾 | 不复用本次计划审查或旧 ADR 草案审查；逐项核对下段边界。需要修复时由核心任务交新的封存和相应检查，审查确认修复。 |
| P3 Git 交付与干净三方合并 | tasks/workspace.py 的 seal/output/绑定/integration_verify；protocol/workspace_manifest.py 和输出事实类型；W:488–582,1290–1332,3853–4370 的产物视图、固定与验收接线；SV:757–763 的 accept 接线、cli_views；store/db.py 与未部署 schema_34_35.py 的新输出形式、相关测试 | 叠 P2 通过的核心产出，自己接线；P3→P4 串行 | 先私有 Git 实验覆盖同文件不同行、cherry-pick、patch、删除/重命名，再跑 tests/python/blackboard/tasks/test_workspace_output_views.py、test_workspace_lifecycle_acceptance.py、test_workspace_review.py、test_workspace_git_truth.py、tests/python/protocol/test_workspace_manifest_references.py。多次 Worker 提交/未提交产物来源正确，旧 seal 不改，干净合并算原样，真实调整仍显式。 |
| P4 项目、空宏任务与 none | 项目/结果模块、tasks/macro_tasks/micro_task/workspace_bindings/macro_task_deletion；turn_input/结果格式、buddy/runtime；W:1587–2026,2880–3010,3853–4126,4442–4666,6104–6293,6966–7194 的项目、none 准备/回合/结果/验收/结案接线，SV:713–721,732–775 及已有 create/amend handler 的 folder/none 扩展；store/db.py、schema_34_35.py，导出工具；最终库存/生成器和相关测试 | 叠 P3，自己负责 W/SV/DDL，不与前后写任务并行；这是最后计划改存储的阶段，统一重新生成一次本批最终库存 | 跑 tests/python/blackboard/tasks/test_macro_tasks.py、test_macro_task_goals.py、test_macro_task_deletion.py、test_lead_workspace.py、test_workflow_worker.py、tests/python/protocol/test_result_references.py、tests/python/buddy/roles/test_schema_worker.py 与新增 folder/none 场景；重跑受本次最终 DDL 影响的升级链/fixture/schema35 检查。空宏任务零成员可读可删，folder Git 化不改旧意，none 固定结果验收且删除不动 cwd。 |
| P5 relocate 与角色调用 | CLI 的 relocate 模型/注册/help、protocol contracts/client/transport 及签名 fixture；项目位置/回收定位模块；W:190–224,965–1111,1708–1718,2828–2844,4726–4909 的项目定位、Lead/helper 目标与自动回收接线，SV 的 relocate handler/角色允许入口；buddy/roles/texts/lead.txt/worker.txt、相关测试 | 叠 P4，自己接线，和控制台按顺序；不写新问题消费/Lead 门铃，不新建无证据工具 | 跑 tests/python/blackboard/tasks/test_workspace_project_relocation.py、test_project_relocation_workflow.py、test_workspace_lifecycle_reclaim_resume.py、test_lead_owned.py、test_lead_lifecycle.py、test_lead_shutdown.py、tests/python/cli/test_host_cli.py、tests/python/protocol/test_contract_version.py。relocate 只收 cwd、按标记认项目；Lead 的 list/get/委派与 Worker suggest 限域，不能操作别人的任务。 |
| P6a 控制台静态稿 | docs/design/adr032/ 下控制台稿与样例，覆盖空宏任务、folder、none、Host/Lead 归属及交付来源 | P5 后先出稿，开发者确认后 P6b；设计不并行改实现 | 暗色为主，同时说明英文/窄容器；目标与 0 成员、无执行/验收记号，none 的未封存提示清楚；这是设计稿，不声称浏览器验证。 |
| P6b 控制台实现 | console/、apps/console/、src/hey_my_buddy/console/assets/；MT 的列表/项目/空组查询，W:529–898,1991–2026,5817 起的读取投影，SV 的 workflow_get/macro 读取 handler、CS 的 GET/POST 参数与新视图；前端/后端/浏览器测试 | 叠 P5 与已确认稿，自己接全部读线；本阶段不改存储形式，若设计发现必须改存储先回到 P4 更新范围 | 跑 tests/python/console/ 和宏任务读取/删除测试，npm --prefix apps/console test、npm --prefix apps/console run test:browser、npm --prefix apps/console run build；最终模型预览覆盖空组/folder/none、去 Host 控制显示、交付来源、现有问题事实，检查暗色/英文/窄容器并提交构建资产。 |
| P7 整合与文档 | 整合 Host 改第 8 节拥有页与 docs/acceptance/；执行完整检查。产品问题交对应任务修复，Host 不代写产品代码 | P3–P6 全完成；核对它们没有搬回已删守卫，版本已由 P1 设置 | 完整 uv run --frozen python -m hey_my_buddy.cli.checks；最终前端/browser 检查仅在新改动影响它们时重跑。入口/帮助/文档同一现行契约，所有未验证边界有记录。 |
| P8 安装前演练 | 私有导出样本、私有安装/runtime 与 docs/acceptance/；安装代码缺陷退回实施任务 | P7 通过后，独立于日常安装 | 真实样本上的 34→35、从真实旧安装基线到候选的安装、旧版本字段、失败恢复与清场证据齐全；演练完成交 Host 验收，不等于已经安装。 |

P1 的版本必须就是 0.61.0，同时更新 CONTRACT_VERSION 和 tests/python/protocol/contract_signatures.json：test_contract_version.py:36–44 同时钉版本与方法签名，不能拖到整合阶段。后续本批若改命名契约，同步更新尚未安装的 0.61.0 签名。当前计划文档不改版本或代码。

P2 独立审查输入为核心交付相对基线的固定差异、ADR/本轮决定、调用方清单与检查证据。重点是只放弃同用户多 Host 的互斥/接管旧代，仍保留 Worker/Lead 允许集和选择器限域、控制台身份、task/attempt/generation/nonce、取消/改派晚到围栏、一次回应、固定产物、scope、删除归属、外部 I/O 后提交重查与真实停止。确认没有换名恢复控制缓存/令牌，也没有删掉跨进程核验。现有计划独立审查不算这次代码审查，Worker 成功报告不算 Host 验收。

未来 Python 检查统一用 `uv run --frozen python -m hey_my_buddy.cli.checks <tests/python/路径…>`，不手工启动测试文件；各行省略目录的文件沿用该行最近的 tests/python 目录。阶段使用假 harness、合成回执与私有 Git，不调用真实模型。平台证据按实际 macOS/Linux/Windows 运行结果记录，未跑明确未验证。本轮没有执行表中任何检查。

安装演练由 Host 按授权在源状态目录之外的新空目录运行 `uv run --frozen python tools/export_board_sample.py <outside-state>/<new-empty-directory>`，最多 100 个已验收微任务且无未结束执行；受管 checkout 不能作为源状态目录内的导出目的地，先导出到外面再使用该样本。禁止复制日常 SQLite/状态目录或反写日常板。记录 schema34 行数、归组、历史和文件摘要，经真实安装路径到 35 后核对 get/宏任务/控制台/固定 patch，空宏任务与 none 在样本副本另外验证，不伪称它们来自原板。

真实样本不含 open request/queued continuation 等状态，它们由地板库存和私有合成执行验证。现有 tools/export_board_sample.py:140–141 会写旧 verifier，P4 适配 schema34/35 的明确结构；演练不能靠给产品留旧契约实现。从旧运行时发它接受的 detach/health 等字段，保留 install/upgrade.py:67–101 的空闲及真实停止边界，不能只在新建板安装。演练后按检查规则清场，退出 3 不当通过；当前安装版本/平台由 Host 给证据，本轮不读取已安装包、不导出或改动日常板。

## 11. 已定

本节记录开发者于 2026-11-05 通过本轮 Host 任务书给出的决定，取代原候选方案。这里记录的是实施依据，不是实现、测试、审查或安装已经完成。

| 编号 | 已定内容 |
| --- | --- |
| D1 | 删除 buddyLocked 的字段、列、冲突和原约束继承分支，及 Router 候选边界读者（W:1444,1487,2846–2855,6957、router_boundary_data.py:269）。显式 route 可以换模型，省略仍沿用当前；不用 true 库存证明整列删除。 |
| D2 | 只有回应请求/产物的动作带回应 id；cancel 只用 runId 定位并重发同一停止请求；无产物 conclude 只用 runId 定位，终态读原结案；amend 用 macroTaskId/goal，相同当前目标重发、不同追加。剩余现场签新 requestId；失败续接用已有固定结果 id 或 turnId，不新造请求。 |
| D3 | 不适用字段不出现，read 不带 writeScope，none 的 accept 只允许 notRequired；有活 helper 才必填 helperPolicy，批准 helper 必填 autoContinue、其余不出现；get 在 runId/macroTaskId/cwd 恰选一项。 |
| D4 | 旧行不回填新建键，非空键部分唯一；同键回原对象及其真实选择，不存第二份指纹。清掉 workflow.* 旧回执而不转换；删宏任务后同标题新建是新的，不加 tombstone。 |
| D5a | 引用寿命推迟，本批只交第 5 节读者清单，不改引用删除路径，引用继续按现生命周期保留。 |
| D5b | 目标顶端是包含产物的 merge 时从父提交推出原目标，其他方式 Host 给 beforeCommit，服务核祖先/基线；先做同文件不同行、cherry-pick、patch、删除/重命名的私有 Git 实验。 |
| D6 | 新问题触发 await 返回、消费账本、Lead 新消息统一与门铃整项推迟；回复保留执行问题事实位置，本批等待返回条件不变。 |
| D7 | 一个用户运维 relocate，只收新位置 cwd；服务读项目标记认项目，不收 projectId，不跟踪普通文件夹搬家；保留停止与所有权边界。 |
| D8 | 空宏任务显示目标与 0 成员，无执行/验收标记；全部/文本搜索可见，active/host/review 不命中；控制台先看静态稿。 |
| C1/阶段 | 全部 CLI 入口改模型且删 AST；模型/schema/删控制/回应与新建键合为一个核心任务，并在此升 0.61.0；立即独立审查，之后各阶段串行叠产出且自己接线。未有使用证据的功能不新建 tools，控制资源 task_wait 无产品调用方则删。 |
| inquire | Host 不编 inquiryId；机制按 runId+question 推出，回复给 id 读回原问题；同问题重发取第一次结果，要再问须换说法。 |

新建键的计算位置按 ADR:44 保持 CLI 推出，逻辑字段、同键返回与旧行无键均已定。CLI 首次辨识 Git 项目及 folder 转 Git 时取得稳定身份的具体调用链尚未实现验证，P1 必须交出这项证据及回复丢失重发检查；这是实现验证项，不重新开放计算位置或设计一套拼键协议。

主要风险是删 Host 守卫时误删真实角色/执行/停止边界、多个动词绕开同一个回应槽、复用 Worker 提交后漏改旧的一父提交假设、none 的 CHECK 放宽到允许 Git 缺输入。用核心即时独立审查、各阶段受影响检查、冻结字节比对及最后真实样本演练控制这些实际改动；不为已放弃的多 Host 互斥、旧格式重放或未发生的情况另建机制。

交接区分计划、实现、检查、独立审查、演练、Host 验收与安装。本轮只完成计划修订，后续代码与平台结果以之后证据为准。
