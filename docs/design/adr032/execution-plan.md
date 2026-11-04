# ADR-032 执行计划

本文落实开发者于 2026-11-05 接受的 [ADR-032](../../decisions/032-host-interface-delegation-loop.md)，源码依据固定为 `29cc34c5701a9cb72eda5920bf364c564c456c7c`。这是一份实施安排，尚无代码、测试、升级或安装的验收结论；第 11 节的待定项先交开发者裁定，受它们影响的阶段不得自行补决定。本轮只读源码并写计划，没有调用产品方法、真实模型或测试，也没有访问日常黑板。

直接依据是 `docs/decisions/032-host-interface-delegation-loop.md:22–99,133–148`、`docs/design/backlog.md:53,191–214,224,232,250`、`AGENTS.md:9–19,39–41,51–65` 和 `CONTEXT.md:185–220,250–258`。`tmp/storage/skill-inventory/report.md:17–75` 与其 `help/` 是入口盘点，`tmp/storage/adr032-review/report.md:7–163` 是第三版之前的审查线索；它们不取代已接受的决定，例如旧审查要求保留 Host 控制凭证、私有执行目录的建议均已被 ADR-032 改定。

Host 提供的 2026-11-05 日常数据作为本计划的已给证据使用：900 个微任务的控制代均为 1，未使用 takeover；hostId 有 42 种写法；exclusiveResources 未使用；requiredCapabilities 仅见于 481 个内部路由任务，没有 Host 提交用例；buddy_locked=true 有 9 个微任务，日期为 2026-10-26 至 11-03。本轮没有复查这些数字。按 ADR 第 6 条删掉 Host/helper 提交的 exclusiveResources、requiredCapabilities；内部 Router 的能力需求继续保留。buddyLocked 等开发者再次决定，两个分支见待定 D1。

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
| reclaim | 从 Host/Lead 面删除；服务自动回收；定位部分留用户运维 | W:4726 起的回收实现、blackboard/tasks/reclamation.py:18–37；已有周期重试继续用，cwd 定位另见 D7。 |
| scope-amend | 删除 CLI/旧操作，进 continue | W:4957–5040；新范围只随新回合生效，历史授权不追认。 |
| goal-amend | 删除 CLI/旧操作，进 amend | W:4910–4955；改以 macroTaskId 定位，不再借宏任务内某个 run 的控制凭证。 |
| workspace-resolve | 删除 CLI/旧操作，进 continue | W:5159–5240；restore/adopt/abandon 可单独机械执行，只有同时给续接输入才开回合。 |
| await | Host 动词；Lead 限域等待 | CL:454–463、BL:301–425,450–540,607–664；内层五个读取保留，删整条命令模板，加入新问题一次返回。 |
| suggest | Worker/Lead 的角色凭证调用 | W:5790–5814；保留 body 与本回合身份，Host 从 get 读，不列入普通 Host 帮助。 |
| macro-task-list | 删除 CLI；控制台与 get 共用读取 | CS:728–729、MT:379–480、FE/api.ts:435–445；改空宏任务与项目种类。 |
| macro-task-timeline | 删除 CLI；控制台服务端读取 | CS:730–736、FE/api.ts:455–471；保留时间轴读取，不成为 Host 新动词。 |
| execution-cancel | 删除 CLI；控制台/内部保留 task_cancel | FE/buddies/MaintenanceView.tsx:48–70、CS:101、BS:1248；微任务仍只能走 cancel。 |
| execution-retry | 移到 tools/ 下的执行记录开发工具 | BS:1328、W:7707–7731；不许工具绕过微任务 continue，保留命名 task_retry 给工具。 |
| execution-acknowledge | 移到 tools/ 下的执行记录开发工具 | BS:1436、W:7707–7731；微任务仍要求 accept 的固定验收绑定。 |
| status | 删除 CLI；await/执行进程/控制台内部 task_get | BL:514,520,642、buddy/runtime/execution.py:538、CS:841。 |
| list | Host 读取并入 get；Lead 保留凭证分页 list；开发历史读取进 tools/ | buddy/roles/texts/lead.txt:5、W:1105–1108、micro_task.py:372–388；删 hostId 过滤，不能删 Lead 名下分页。 |
| result | 删除 CLI；await/控制台内部 task_result | BL:322,561、CS:843；Host 精读走 get，不再输出 resultCommand。 |
| artifacts | 删除 CLI；内部角色读取保留；开发记录检查进 tools/ | PC:131–132、W:71–101；Host 所需固定产物与结果由 get 给出。 |
| events | 删除 CLI；await/角色内部 events_read | BL:512、PC:123–124；事件游标仍用于等待与 Lead，不用 Host 编号。 |
| wait | 删除 CLI；await 内部 task_wait | BL:522–524、PC:68–69；独立等待容量继续保留。 |
| watch | 删除 CLI；await 内部 events_wait | BL:519、PC:126–129；不把等待资源并入控制资源。 |
| inquire | Host 动词；角色限域观察共用 | blackboard/tasks/inquiry.py:70–98、SV:964；合入按 inquiryId 读取旧问题的变体。 |
| messages | 删除 CLI；控制台及角色 message_list | CS:109、FE/task-detail/client.ts:46、PC:119–120；get 可带已存问询。 |
| message-get | 删除 CLI；Host 读并入 inquire；底层 message_get 保留 | PC:116–117、W:83,98、inquiry.py:70–90；保留问题关联与去重，不增另一套消息身份。 |
| wait-capacity | 删除 CLI/单独操作，读数并入 health | SV:97,335、WaitAdmission.stats:126–129、CT:225；内部直接读统计，不保留空壳方法。 |
| console | Host 便利入口；用户打开控制台共用 | CL:446–453、cli/console_cli.py:122,137,172–175；浏览器会话身份照旧独立。 |
| console-snapshot | 移到 tools/ 下的控制台诊断工具 | SV 的 console_snapshot、console/reads.py:77–122；保留命名操作，HTTP 快照直接调用同一投影实现。 |
| user-policy-publish | 删除 CLI；只给控制台服务端 user_policy_publish | CS:97、FE/use-editor.ts:143、SV:524–525；保留 require_console，不删用户设置发布。 |
| selection-request | 移到 tools/ 下的路由研究工具 | SV:664–668、routing/decision.py:246–284；产品路由仍经内部协调器，不添 Host 独立模型入口。 |
| selection-get | 删除 CLI；控制台服务端；开发读取复用 tools/ | CS:104、FE/task-detail/client.ts:50、SV:670–671。 |
| selection-list | 删除 CLI；控制台服务端；开发读取复用 tools/ | CS:105、SV:673–674；未找到 FE 独立调用，不因此删除现有白名单读取。 |
| model-catalog-refresh | 删除 CLI；控制台服务端 | CS:106、FE/buddies/CatalogView.tsx:160、SV:676–702；目录事实仍供内部路由。 |
| buddy-list | 删除 CLI；控制台服务端 | CS:107、FE/use-buddy-history.ts:158、SV:704–710；完整 buddy 名由目录事实解析，help 说明表达式。 |
| restart | 用户运维 CLI；安装内部 service_control | CL:89、install/upgrade.py:128–154；保留执行进程跨服务重启的语义。 |
| stop | 用户运维 CLI | CL:90、SV 的 service_control；停止未知不能报成停止完成。 |
| help | Host/运维/角色的本地自助入口 | CL:14–15、cli/cli_help.py:1–27,94–119；由模型生成参数、合法组合与拒绝信息，不启动服务。 |

新增 `create` → 一个宏任务及初始目标的独立事务；新增 `amend` → 按 macroTaskId 追加目标历史。修改 `CL` 的 METHODS/说明、transport 的面向受众映射、`CT` 与 `SV` 的命名操作，以及 `PC` 的实际被用方法；create/amend 的边界模型与 get 的集合读取先于调用方改写。依据 ADR:22–30、MT:151–195、W:4910–4955。

开发工具只从源码检出运行，建议按用途收为 `tools/inspect_board.py`（历史、产物与控制台投影）、`tools/control_execution.py`（非微任务执行记录的重试/复核）、`tools/request_selection.py`（主动路由研究）。它们调用保留的命名操作，帮助归 tools 自己与 AGENTS.md，不增加产品命令组或别名；这些建议路径由实施者在同一职责范围内落定。现有 `tools/export_board_sample.py` 留在原位并适配 schema 35。

### 控制台 HTTP 路由

当前路由分支见 `CS:708–748,803–846,852–889`，本地入口与静态资源见 `CS:659–696,750–780`。下表逐条列 HTTP 去处；通用 POST 里的每个业务操作在下一表单列。

| 路由 | 新去处与要改的调用方 |
| --- | --- |
| GET /launch/{ticket} | 留控制台会话兑换，CS:673–696；与 Host 控制文件无关。 |
| GET /assets/{path} | 留静态资源，CS:717–718,761–780；随前端构建更新。 |
| GET / 或空路径 | 留页面入口，CS:722–723,750–759。 |
| GET /api/console | 留服务端投影，console/reads.py:77–122、CS:782–795；改 Host 标签与新问题显示。 |
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
| events_read | 限域事件底层，W:82,97；Lead 门铃与 await 共用新消息定义。 |
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
| BuddyControl.console_snapshot | 保留开发工具调用；HTTP 共用 console/reads.py:77–122 的投影。 |
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
| BuddyControl.task_list | 保留 Lead 分页与开发工具；Host 改 get 集合读取。 |
| BuddyControl.task_result | 保留；BL:322,561、CS:843。 |
| BuddyControl.task_cancel | 保留控制台维护取消/内部；W:7707–7731 的微任务重定向拒绝改为无控制凭证版本。 |
| BuddyControl.task_retry | 保留开发工具；微任务不得绕过 continue，BS:1328、W:7724–7731。 |
| BuddyControl.task_acknowledge | 保留开发工具；微任务不得绕过 accept，BS:1436、W:7727–7731。 |
| BuddyControl.task_wait | 保留现有控制资源方法；内部等待优先走 BuddyWait，CT:104、PC:68–69。 |
| BuddyControl.execution_progress | 保留执行专用；execution.py:373、PC:72–102；attempt 栅栏与进程身份不动。 |
| BuddyControl.execution_result | 保留执行专用；buddy/runtime/receipts.py:35、PC:104–113；none 也走同一回执路径。 |
| BuddyControl.execution_maintenance | 保留执行专用；execution.py:801；不并入 Host continue。 |
| BuddyControl.message_get | 保留角色/问询内部；PC:116–117、W:83。 |
| BuddyControl.message_list | 保留控制台/角色；CS:109、PC:119–120。 |
| BuddyControl.inquiry_observe | 保留并承载 inquire 读旧问询；inquiry.py:70–98。 |
| BuddyControl.artifact_list | 保留角色与开发工具；PC:131–132；增加结果型产物。 |
| BuddyControl.events_read | 保留 await/角色内部；BL:512、PC:123–124。 |
| BuddyControl.user_policy_publish | 保留且限控制台；SV:524–525、CS:97。 |
| BuddyControl.selection_request | 保留开发工具；SV:664–668；workflow 路由仍经协调器直接调用。 |
| BuddyControl.selection_get | 保留控制台/开发读取；SV:670–671。 |
| BuddyControl.selection_list | 保留控制台/开发读取；SV:673–674。 |
| BuddyControl.model_catalog_refresh | 保留控制台；SV:676–702。 |
| BuddyControl.buddy_list | 保留控制台；SV:704–710。 |
| BuddyControl.workflow_submit | 保留，改 Host/Lead 模型、派生键与 none；W:1708–1860。 |
| BuddyControl.workflow_get | 保留，加入宏任务/项目集合读取；W:1991–2026。 |
| BuddyControl.workflow_decide | 删除命名操作；W:2183–2290 的有效能力进入 workflow_continue。 |
| BuddyControl.workflow_continue | 保留，统一续接各变体；W:2880 起。 |
| BuddyControl.workflow_takeover | 删除命名操作与实现；W:3330–3435。 |
| BuddyControl.workflow_cancel | 保留，删除 Host 凭证/命令号；W:3438–3469。 |
| BuddyControl.workflow_accept | 保留，固定产物与三方合并；W:3853 起。 |
| BuddyControl.workflow_conclude | 保留，绑定回应对象；W:4442 起。 |
| BuddyControl.workflow_reclaim | 删除 Host/Lead 命名入口；W 的机械回收作为服务内部方法保留，定位运维见 D7。 |
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
| BuddyWait.task_wait | 保留；BL:522–524、PC:68–69，新增问题消费走此边界。 |
| BuddyWait.wait_capacity | 删除单独操作；SV:126–129 的统计直接供 health 使用。 |
| ExecutionLive.send | 保留服务到执行的问询推送；protocol/execution.py:332。 |
| ExecutionLive.observe | 保留服务到执行的观察；protocol/execution.py:338。 |
| ExecutionLive.capabilities | 保留执行能力读取；protocol/execution.py:342。 |
| ExecutionLive.close | 保留执行连接生命周期；CT:242，不能因删 Host 控制删掉活连接清理。 |
| ExecutionLive.stop | 保留执行停止；protocol/execution.py:324，仍凭执行身份与 nonce。 |

## 2. Host 请求与回复的形状

以下是请求模型应表达的形状，字段名沿用现名，新增判别写法由模型落定；带 D 编号的部分尚未定案。外来 JSON 在服务入口按模型校验一次，内部传已验证对象；CLI 本地的 wait、output、打开浏览器等选择也有自己的模型，不把服务端领域规则再验证一遍。未知字段一律拒绝，不保留旧字段兼容。依据 ADR:43–66、AGENTS.md:19、SV:137–170。

公共回复应有 contractVersion、对象的 id、当前状态及 `actions`（合法动词名数组）；微任务回复还给 projectId、macroTaskId 或明确 standalone、当前回合/执行身份、此刻有效的 requestId 或 artifactId、真实 shutdown、当前问题事实及固定结果/产物引用。requestId 与 artifactId 是服务已签发的回应对象，不从“最近展示”缓存里补。这里 actions 是建议字段名，实现时统一一次即可；只给动词和需要的 id，不给完整命令模板。现有替换点为 `W:745–898`、`cli/cli_views.py:250–283`、`BL:334–418` 以及 routing/boundaries.py 中的 commands。

| Host 动词 | 请求字段、取值与组合 | 回复 id、动作及拒绝/状况 |
| --- | --- | --- |
| create | `cwd`（本机已存在目录的绝对路径）、`title`（非空，沿用 200 字符上限）、`goal`（非空，沿用 1 MiB 上限）；项目从 cwd 解析；CLI 派生新建键，无 Host requestId。 | projectId、macroTaskId、目标及其当前身份；`amend,submit,get`。重发给原宏任务及“已经存在”和当前状态；路径/字段错是拒绝，路径不可访问是具体状况。依据 ADR:23,30,44–47；S:434–450。 |
| amend | `macroTaskId`、新 `goal`；直接改宏任务，允许空宏任务；回应目标的 id 如何给出见 D2。只修订目标，不顺带加入改标题能力。 | macroTaskId、固定的新目标历史身份与当前目标；`amend,submit,get`。目标已变拒绝旧回应，拒绝信息引导 get；不用微任务 runId 代替宏任务身份。依据 ADR:23,38–51；W:4910–4955。 |
| submit | Host 必填 `cwd,macroTaskId,title,task,buddy,checkout,access,expectedSeconds`；macroTaskId 为 create 返回值或字面量 `standalone`。条件字段见下文。 | projectId、macroTaskId/standalone、runId、路由事实和当前状态；新准入可 `await,inquire,cancel`，立即遇边界则按实际边界给 requestId 与 `continue,cancel`，并保留 get。重发回原 runId，不重读源树、不重新路由、不升授权。依据 ADR:24,44–66；W:1746–1785。 |
| await | 必填 `runId`；可选 `waitSeconds` 为 1–86400，沿用本地等待窗口，窗口默认不属于授权默认。删旧提交 requestId 选择器；Lead 等待的集合形式见 D6。 | 同一 runId 的交付、边界、提醒、新问题或等待到期；交付带 artifactId，`accept,continue,conclude`；边界带 requestId 和种类，`continue,cancel`；执行未停的新问题保留 `await,inquire,cancel,get` 中当下合法者，不给 accept。依据 BL:450–540,607–664、ADR:81–85。 |
| get | 恰选 `runId`、`macroTaskId` 或项目（建议以 cwd/projectId 二选一的项目选择器表达）；集合可分页，沿用 1–100 的分页上限及游标；run 可选 includeAudit/routingHistory。不同选择器的字段不能混填。 | 微任务返回全部审查与下一步 id；宏任务返回目标、成员与状态，空成员也是合法结果；项目返回宏任务、独立微任务与待处理状态。列表每项带自身 id 与合法动作，不能暗示“属于这个 Host 会话”。读取细节/默认与项目选择器拼写见 D3。依据 ADR:30,38,52–54；W:1991–2026、MT:379–480。 |
| inquire | `runId`；不带问题时观察；带 `inquiryId,question` 时提问，question 非空且最多 4000 UTF-8 字节；仅带 inquiryId 时读已存问题。`timeoutMs` 100–5000，`waitMs` 0–30000 沿用原边界。 | runId、inquiryId、问题状态/答案及当前动作；可以再 inquire/await/get，取消是否可用看当前状态。同 inquiryId 同问题不重注入，改文冲突；仅读不存在的问题为 NOT_FOUND；无能力、执行已结束或连不上给事实。依据 inquiry.py:70–98、S:29–31,41；D3 冻结读变体。 |
| continue | `runId`，带所回应的 `requestId` 或 `artifactId`；续派 `input`、边界 decision/helpers、`buddy`、access、writeScope、现场 resolution 的条件见后表。有活 helper 必选 `helperPolicy:keep|cancel`。 | 同一 runId；开回合时给 continuation/turn 身份及 `await,inquire,cancel`；只做现场处置时给该 request 的处置结果、新边界或可验收 artifactId 及此刻动作。已回应 id 同动作重发返回原动作身份与当前状态，另一动作冲突；停止未知是状况。无回应 id 的续派见 D2。 |
| accept | `runId,artifactId,note`；`target:{path,ref}` 或非空 `notRequired` 恰一项。真实调整用现有 adjusted 与 note；hostPaths 与 beforeCommit 的条件保留。 | runId、artifactId、integrationId、已验收状态、整合核验与回收结果；终态无继续/重复验收的新动作，可 get。同参数重放已有验收，改目标/备注冲突；范围外差异或未整合是核验拒绝，回收受阻不撤销验收。依据 ADR:86–88、W:3853–3909。 |
| conclude | `runId,note`，以及所回应的 requestId/artifactId；仅失败、取消或已交付未验收可结束，仍需本任务和后代停止成立。没有边界/产物的失败如何给回应 id 见 D2。 | runId、conclusionId、固定回应对象、真实执行状态与回收结果；终态只读 get。同动作重发读取原结案，不生成第二份部分产物；未停、活 helper、未处置冲突仍拒绝或报告状况。依据 W:4442–4483。 |
| cancel | `runId` 与可选 note；所回应的 id 在尚无边界/产物的运行状态怎样给出见 D2，不擅自加 token 或计数器。保留现有有序收尾规则。 | runId、停止请求身份、当前真实 shutdown 与 `await,get`，尚能观察时可 inquire；请求取消不等于已停，停止后按结果可 conclude。重复不得再次开回合或改写首次事实。依据 W:3438–3469、ADR-022:20–29。 |

help 无需 service：列本受众的动词，按方法显示模型里的必填、条件、取值和合法组合；完整 buddy 是目录所记录的完整执行身份名字，不允许部分约束。`catalog/catalog.py:649–655` 的 buddyId 可能是清理后加摘要的身份，不能用冒号分割重造四元组，应查目录行得到 harness/provider/model/effort，再走现有显式准入。console 沿用 `cli/console_cli.py` 的本地浏览器/等待参数与服务 open/status/close；Worker 凭证仍在任何本地动作之前被拒，见 `CL:446–453`。

### submit 的合法组合

`title` 必填、非空且最多 200 字符，`task` 非空且最多 1 MiB；`expectedSeconds` 为 1–86400 的整数，不再默认 1800。`buddy` 为 `route` 或完整名字；显式 buddy 仍校验目录与用户启用事实，不自动成为锁定。`cwd` 同时给项目解析位置和 none 的实际执行目录；Git worktree/existing 保留源 cwd 与实际工作目录的区别。依据 `S:23–43,219–240,434–450,468–536`、ADR:55–68。

| checkout/access 条件 | 必填、允许与拒绝 |
| --- | --- |
| worktree + read | 要 Git 项目，显式 inputs（可空）；无可写授权，writeScope 的空列表/不出现写法由 D3 定，不给非空范围。准备固定输入与独立检出。 |
| existing + read | 要 Git 项目，显式 inputs；沿用具体不变输入的共享规则，不恢复“同路径即相同快照”。writeScope 同上。 |
| worktree/existing + write | 显式非空 writeScope 与 inputs；writeScope 为相对检出根的路径，显式 `["."]` 才授权整个检出；inputs 只列带入的未跟踪文件，不能默认带全部。 |
| worktree/existing + full | 上一行再加必填 accessNote（单行、非空、最多 10000 UTF-8 字节）；即使 harness 放开，黑板对文件产物的 writeScope 仍成立。 |
| none + read | Git 或普通文件夹皆可；不接受 inputs/writeScope/base/targetRef，不创建文件输入快照，只固定结果。 |
| none + write | Git 或普通文件夹皆可；在 cwd 执行，结果明确“文件夹的改动未封存”；不接受 inputs/writeScope，不虚构可验收文件清单。 |
| none + full | 按 ADR 的 access 三值允许，full 理由必填；同样不封存文件。harness 原生的 full 含义不由黑板偷偷改成 write，固定结果仍是验收对象。 |
| delegationAuthority | 仅显式 true 时出现，必须任务已明确用户授权、完整 buddy 且 access=full；Lead 自身提交不得再授予，不从权限推断委派权。none 的 Lead 也无文件封存，名下任务仍受项目归属约束。 |
| fixes | 仅修正时出现，指向已验收微任务，保留同项目、同宏任务和 Lead 名下约束；不撤回原验收，也不把不同 Host 字符串换成另一个自报 owner。 |

对 worktree/existing 的 inputs 与 writeScope，沿用提交边界最多 256 项及现有相对路径/越界规则；后续回合继承封存中的大量路径继续用 ADR-031 第 16 条的引用，不把生成的路径重新塞进有界请求或另设截断。`checkout:none` 不获得文件输入冻结能力。条件模型不得接受不适用字段再忽略它。依据 `S:324–371`、`workspace.py:696–745,1011–1053`、ADR:62,68–76。

### continue 的变体

| 变体 | 字段与组合、保留的副作用边界 |
| --- | --- |
| 打回交付 | runId + artifactId + 非空 input（沿用 1 MiB 上限）；只回应该固定产物，不自动取 latest；新回合后旧 artifact 仍可读但不能再 accept。 |
| 回答 assistance/attention/helper 边界 | runId + requestId + decision=approve/decline，可有 note；approve 才能带 helpers，decline 不接 helper 包。autoContinue 只在这个分支适用，沿用原本“helper 收齐后是否自动续接”的语义，字段是否必须显式见 D3。 |
| 批准 helper | 每项是完整任务包：title/task/cwd/buddy/checkout/access/expectedSeconds 及适用的 inputs/writeScope/accessNote；上限沿用 8。归属与 integrator 由父请求及凭证推出，helper 不填 Host 名、宏任务、owner 或提交 requestId；一次回答的固定身份使重发不会重复创建 helper。 |
| 改派/换手 | 可与合法续派输入同给 buddy=route 或完整名字；省略保持现有 buddy，删除 reroute。完整显式 buddy 仍需说明换手理由；Lead 不允许 route。自动 quota 改派的代际判断仍按当前路由/执行身份，不再按 Host 控制代。 |
| 换权限/范围 | 可与开启新回合的 input 一起给 access、适用的 accessNote、writeScope；没有 scope 变更则保留当前授权，给出新范围只作用于这个新回合。已执行回合、封存与当前冲突证据不重写；范围变更前照旧确认相关执行与 helper 已停。 |
| 处理活 helper | 有活 helper 时显式 helperPolicy=keep/cancel；keep 不增加它的授权，cancel 必须沿用停止证据后才可复用共享检出。无活 helper 时该字段不出现。 |
| 处置出界现场 | runId + requestId，resolution 含 action=restore/adopt/abandon、当前 observedFingerprint、适用 paths 与 note；request 绑定 conflictId，可不由 Host 再拼 conflictId。只处置不带 input 就不开回合；同时续派则先处置成功，再用同一动作身份安排新回合。部分处置产生的新回应身份见 D2。 |

变体的组合由一个请求 union 与明确的交叉约束描述，不能允许 approve/decline、另选 buddy、abandon、开回合互相矛盾的组合。已经发生的出界由现场处置决定，未来 writeScope 变更不能抹掉它。实现沿 `W:2183–2290,2828–3010,4957–5040,5159–5240` 拆出内部动作，不复制三套预检与落库。helper 目标的 runId/targetRunId 外形及组合原子性见 D3，冻结前不把建议写成可用接口。

### 动词集合与拒绝、状况

服务按当前事实计算 actions，CLI 投影不另判断合法性。表中的常见集合不包含每个回复都可用的 get；集合查询返回的成员按各自状态算。授权与输入错误、状态冲突、运行状况分别表达，沿用原因代码与参数，不用模型生成解释来代替事实。依据 ADR:48–54,81–92、`protocol/transport.py:115–131`、CONTEXT.md:269–280。

| 观察到的事实 | 必带的 id 与合法动作 | 拒绝或状况的分法 |
| --- | --- | --- |
| 已准入/执行中 | runId；await/inquire/cancel | 字段错或角色越界为拒绝；排队、容量、路由健康及原生拒绝为有事实的状况，不能伪装 INVALID_ARGUMENT。 |
| 活跃边界 | runId + requestId + kind；continue/cancel | 请求已被回应、被新请求取代或 action 不适用为冲突；给 get，不自动换成新 id。 |
| 已交付且停止成立 | runId + artifactId；accept/continue/conclude | 原生完成文本不构成交付；固定产物/结果缺失或摘要不符必须报告，不能展示 accept。 |
| 新执行/服务问题，工作仍活着或停止未知 | runId + attemptId（适用时）+ 问题事实；await/inquire/cancel 中实际可用者 | 如 EXECUTION_UNKNOWN、SHUTDOWN_UNCONFIRMED、原生权限拒绝给原因、停止事实与可行处置；不把未知降为失败或已停。 |
| failed/cancelled，停止成立 | runId + 可回应的 requestId/固定结果 id；continue/conclude | 不展示 accept；身份缺口按 D2 解决，不能随手用当前 revision。 |
| accepted/concluded | runId 与原 integrationId/conclusionId；只有读取 | 原动作原样重发读取原动作身份；另一个动作或改参数为冲突。 |
| 空宏任务 | macroTaskId、projectId、目标身份；amend/submit/get | 不声称执行完成或“全部验收”；删除仍是控制台能力。 |

输入拒绝应指出字段路径、收到值的类别、允许取值或缺少的配套字段；角色拒绝明确是 Worker/Lead 不可做的操作；回应冲突明确给所回应 id 及当前可读位置。服务连不上时只回连接事实和同 runId 的恢复方法，不能伪造“此刻合法”的领域动作。必须运维才能继续时，回复写出具体运维命令及 operations 的 Host 运维一节；submit/await 自带路由原因，删 skill 中先 health 的强制前置。

新问题一次返回：现有 `execution_issues`/`service_issues` 是按 stage 替换的事实表（DB:250–264），`selectedAttempt.issues` 已可读，但 BL:450–524 只因提醒/边界/终态返回。实施把问题出现、消除、再出现接入同一新消息读取与消费边界，Lead 门铃共用；持续问题不得让每次 await 立即返回，普通进展不叫醒。如何持久记录“已返回一次”及恢复等待游标见 D6；本计划不另造 Host 会话身份来实现通知。

## 3. 删字段与删守卫的完整路径

| 删除或合并的字段 | 原来的保证 | 本批怎样处理 |
| --- | --- | --- |
| submit.requestId、submissionToken、submission_verifier | 调用者自己选新建身份，回复丢失可恢复初代控制能力；W:1724–1785,1952–1969 | create/submit 的新建键由 CLI 派生；重发回原记录；不再恢复 Host 控制能力。旧记录的新键回填与同键不同授权参数见 D4。 |
| hostId、sourceHostId、newHostId | 自报标签、宏任务加入时同名检查；S:58–60、MT:172–174 | 删除输入、过滤、权威列与当前归属显示；只保留同项目检查。归属按 helper/Lead 谱系显示 Host 或某个 Lead。 |
| owner | 归属标签，未进入输入指纹；S:219–224、BS:475–491 | 从 Host/helper 输入与视图删；真实 Lead 归属从 workflow_lead_tasks 读。tasks.owner 若查无保留内部读者则整列删，不能改填另一个自报字符串。 |
| ownerGeneration、controlToken、controlFile、takeover | 单一 Host 控制及接管后旧代拒绝；W:965–1000,3330–3435 | 按 ADR 第 3 条明确放弃；不能补回“最后一个 Host”、会话锁、CLI 控制缓存或隐含代数。Worker/Lead 权限边界继续机械落实。 |
| expectedRevision、Host commandId | 迟到操作冲突与同命令重放；S:399–403、W:2234,2258–2287,2919–2939 | 用确切 requestId/artifactId 的一次回应与完整动作参数比对；并发预检后的落库再查同一身份，见下文。 |
| expectedGoalRevision | 宏任务目标并发比较；W:4919–4941 | Host 不再填计数器；目标的回应身份见 D2，目标历史的排序键可以保留为数据库主键。 |
| expectedScopeVersion | 新范围必须接在所看范围之后；W:4973–5009 | 范围与开启新回合的同一个回应绑定；保留历史 scope_version 的证据与主键，Host 不再填数字。 |
| harness/provider/model/effort 四个 Host 字段 | 显式完整选择且拒绝部分约束；S:516–524 | 合成 buddy；从完整目录身份还原四项内部事实，仍校验目录、能力及用户启用。 |
| buddyLocked | 显式 buddy 后续不可突破的硬约束；W:2846–2855 | 暂列 D1 的双分支，不把显式 buddy 自动等同锁定。 |
| macroTask、macroTaskOf、macroTaskId 的省略 | 新建宏任务、借另一 run 找归组或默认为独立；S:434–451 | create 单独新建，submit 明写 macroTaskId/standalone；Lead 归属由凭证推出，不能把 Host 的必填照搬给 Lead。 |
| executionWorkspace 包装、base.kind、base.ref | 检出方式、从指定提交或工作树开始；S:316–371 | 扁平化为 checkout/access/inputs/writeScope；从 Host 当前工作树准备固定输入，取消任意 commit 起点。后续回合仍固定自己的输入，不每次回头读 Host 当前树。 |
| includeUntracked | 指定进入冻结输入的未跟踪文件；S:357–361 | 改名 inputs 并显式给空列表；只适用于 worktree/existing，none 不复制。 |
| targetRef（submit 的检出意图） | 记录一个候选整合 ref；S:369–371、W:583–616 | 新 submit 清单不收；候选可从实际源工作树事实给出，accept 仍显式选 target；旧封存中的 targetRef 留作原证据，不能当默认验收位置。 |
| exclusiveResources | 跨 cwd 的外部资源互斥；BS:835–859,1805–1810 | Host 给定统计无人用，按 ADR 删除 Host/helper 声明，明确不再提供这项声明的保证；内部 cwd 准入与实际停止清理不删。没有事实支持另造共享资源机制。 |
| requiredCapabilities | Host 的硬能力要求；routing/decision.py:418–432、BS:1676–1682 | Host 给定统计无人用，删除 Host/helper 字段；permission:access 由机制加入；481 个内部 Router 任务用到的 decision 等需求保留，不从内部 spec 中一概删除。 |
| reroute | 与显式 buddy 互斥的改派开关；W:2912–2916 | 一律 buddy=route 或完整名字；省略表示当前 buddy，不能省略后自动改派。 |
| targetRunId | 根控制能力与 helper 目标分离；W:2828–2844 | 删除控制能力后仍要保持 helper/Lead 目标归属；最终公开形状见 D3，不因去掉三元组就放开任意后代。 |

控制文件路径连同清理一起删：`CL:209–325,467–472` 的三元组注入、显式文件读取、提交令牌落盘、结果脱敏并保存控制文件；`home.py:31–39` 的 controls/submissions 路径函数；`macro_task_deletion.py:418–460` 的这两类文件扫描、临时文件识别与清除。安装时对已存在的这两类私有文件作一次明确退役清理，归升级安装阶段，不让运行时保留旧控制文件阅读器；不能把服务自己的 control.json、control-start/control-daemon/board-owner 锁当作 Host 控制文件删除。后者的读取与锁仍在 `transport.py:144–164`、`install/upgrade.py:170,452–566`、`storage.py:110–118` 使用。

服务路径包括 `S:58–60,375–403,454–503`、`W:965–1027,1587–1660,1952–1984,3330–3435`、`SV:182–214,741–747`、`DB:1496–1502`。删除 Host token 派生和核对、控制代轮换、takeover 回执恢复、hostId 相等检查、waitingForHost 按 hostId 聚合。`W:1440–1511,5869–5873,6960–6964` 的路由有效性改为当前 routing/decision 身份与状态，不保留 owner_generation=1 的假常量；取消、改派使过期路由失效的约束继续成立。

保留的角色边界有三处：`SV:216–246` 先核私有服务 token，再识别执行凭证或控制台身份，失效的执行凭证不能落回服务权限；`W:1031–1111` 核活凭证、允许集与全部选择器对应的名下关系；变更事务中再查凭证仍有效。普通 Worker 不能 submit/create/amend/continue/accept 等 Host 写动作；Lead 只能操作名下微任务，不能修改用户设置、创建另一层委派权或借根 Host 获得名下任务控制权。角色凭证的 token_verifier、attemptId、generation、turnId 与撤销机制保留，见 `DB:831–845,1504–1510`、`W:6624–6642`。

`W:973` 目前用 host_id 的空值判 Lead，`W:1002–1016` 还用它推 owner。删列以后从 workflow_children 的根与 workflow_lead_tasks 的关系推控制角色；不新增一个与谱系重复的 owner 字符串。旧事件、封存输入与回执中的 actor/旧名字属于写下时的证据，保持原字节；新 Host 事件只记 Host 角色，不生成替代身份。需要 actor_id 的旧 NOT NULL/CHECK 与 Host 当前视图一起改为明确的 Host 无名字分支，Lead/console/service 的真实 id 保留，不能用空字符串冒充一个人。

回应用 id 的栅栏需要一份权威的“某对象已被哪次动作回应”事实：对 `(runId,回应种类,requestId或artifactId)` 至多成功一次；原样动作重发读取其已提交结果身份，参数改变、accept 与 continue 抢同一产物、approve 与 decline 抢同一请求均冲突。比对包含 input、helper 包、目标、授权、范围、处置、note 等完整规范化动作，不只摘要一段 input。重放给原 integration/continuation/conclusion 身份和当前状态，不存第二份可变运行视图。现有 `tasks/command_receipts.py:1–64` 已用小回执重建当前视图，可复用这条投影原则。

实施可新增专门的回应关系表，或者把现有 request/accept/continuation 事实收成同一权威关系；关键是一个唯一约束覆盖互斥动作，不能分别在 accept 与 continue 的表里各自“已经处理”。建议使用 `workflow_responses`，只存回应对象、动作的规范化摘要、结果记录的身份与提交时间；请求的批准参数若移入这张表，原 decision_json/command_id 等重复列同批移走。动作的领域事实仍在 integration/continuation/conclusion 表，不另存完整 get。无回应对象、部分现场处置多次动作等情形先解 D2，不能靠新 commandId 或 revision 冒充答案。

跨 Git I/O 的预检与事务重查仍然必要，检查的是 request/artifact 仍为当前、turn/attempt/route 身份、谱系、停止证据、工作区/目标观察指纹。`W:2244–2287,2930–2983,5199–5227` 的前后两次检查不是重复校验外来 JSON，而是外部事实会变的提交边界；独立审查必须确认没有随 Host 守卫一起误删。内部 task_wait 用的 tasks.revision、事件游标、目标历史主键与 scope_version 不属于 Host 编号；逐个按实际读者保留。workflow_runs.revision 及依附的 expected_revision 在改完读者后退役，长操作用上述事实身份核对，不能留一个无人读的列。

create 的键由规范化项目身份与 title 推出；submit 的键由 macroTaskId（独立则 projectId）、title、task 推出，用有版本前缀的规范化序列化加 SHA-256，避免字符串拼接歧义。项目先按 Git 标记或规范化文件夹路径认；键不含 Host 名、时间、随机 token、当时的 HEAD 或文件内容。新建键在准入事务受唯一约束保护，两个同键调用只创建一个；有 Git 输入时先查键再做检出冻结，沿用 `W:1746–1749` 的恢复优先顺序。回复丢失后重新调用 CLI 会算出同一个键，已结束的任务也回原对象；重做须换 title，不自动 continue。当前 `tasks.request_id` 还被内部路由/执行读取，不能把它与新的 Host 回应 requestId 混为一列；是否复用为机器生成的新建键由 D4 的旧行转换规则一起冻结。

CLI 没有数据库连接，也不能为得到项目 id 先另跑一个公开命令。可以把逻辑键存为“服务从 cwd 解析出的项目命名空间 + CLI 对 title（submit 再含 task/已知 macroTaskId）算出的规范化摘要”：服务在同一次 create/submit 内完成项目解析、按键查询，再准备工作区；完整唯一约束仍覆盖 ADR 指定的项目/宏任务、标题与任务书。这样首次 Git 标记由现有项目写者建立、folder 后来有 Git 时沿用旧项目，不会因 CLI 首次用路径、重发改用新标记而换键。具体 wire 字段与 namespace 存储随 P1/P2 一次落定，不恢复用户填写的 requestId 或私有提交文件。

title 的空白规范化沿 `S:440`，task 保留原文；哈希算法只是实现细节，字段发生实质变化时按 ADR 可能多出一项，Host 从 get 的宏任务列表看见。不能为了“更准确”再把工作树内容加入键，因为这会让丢回复的重发新建第二个任务。相同键但其他授权/执行字段不同是否拒绝，以及旧同名宏任务怎样回填，见 D4；重发在任何方案下都不得静默扩大原授权。

## 4. schema 34 → 35 与存量

当前 `DB:22` 是 schema 34，升级链 `store/upgrade.py:9–14,26–52` 已有四步且都在真实板运行过。本批只新增自足的 `src/hey_my_buddy/blackboard/store/schema_34_35.py`，内带它自己的 DDL、常量与转换，最后写 35；不能改 30→34 的任何步骤，不能从当前 db.py/领域代码导入转换逻辑。新板直接用当前 DDL，链从各来源 schema 运行到 35，逐表/index/CHECK 与新板比较；事务失败保留原板。升级地板仍是 schema 30 / 0.54.1。

| 当前存储 | 34→35 的目标与转换范围 |
| --- | --- |
| workflow_runs.host_id / source_host_id / owner_generation / control_verifier / submission_verifier | 全部删除。源码实际有三处 Host 名列：这里两列，另有 macro_tasks.source_host_id；不能只按旧盘点的“两列”漏掉 source_host_id。现有行的 run/attempt/归组/Lead 谱系保持身份不变。依据 DB:499–535。 |
| macro_tasks.source_host_id | 删除；项目身份与目标历史保留，创建不再依赖首个微任务。依据 DB:554–573、MT:151–195。 |
| workflow_routes.owner_generation | 删除；pending/resolved/needs-host/fenced 的实际历史与 decisionId/runId 保留，当前有效性由 current_routing_id 与路由/运行状态核。依据 DB:588–594、W:5869–5873,6960–6964。 |
| workflow_runs.revision；workflow_requests.expected_revision/decision_command_id；workflow_continuations.expected_revision/command_id | Host 计数/命令账本退役，改回应对象与自动触发身份；请求内容、关闭状态、续派输入和已消费状态原样留存。运行内的并发检查改具体事实，不把清列变成少一道提交检查。依据 DB:29–47,527,635–671。 |
| workflow_children.decision_command_id | 子任务已有 parent_run_id/request_id，改以一次请求回应事实关联，不保留另一个 Host 命令号。依据 DB:540–548。 |
| scope_versions/workspace_conflicts/integrations/host_conclusions 的 command_id；host_conclusions.owner_generation/run_revision | 改为指向权威回应事实，删冗余 Host 命令号及不再核的控制代/运行计数；保留 attempt、artifact、执行状态、时间、备注/原因等独立证据。依据 DB:721–765,776–799,973–994。 |
| 各 actor_role / actor_id | 重建受影响的 NOT NULL/CHECK：新 Host 只有角色，Lead/console 等仍需真实身份；旧事件中的历史名字留证，不再参与当前所有权。依据 DB:280–292,721–734,776–799,973–992。 |
| commands | 整表保留给执行、harness_prepare、运维与控制台；删除已退役 workflow.takeover 等无读者的命令库存，把仍需重放且可从领域记录证明的旧边界/验收/续派/结案转换到回应事实。旧表只存 request_hash，不能逆向假造新动作参数；不可还原部分按 D4 先定转换/清除规则。保留关闭状态与固定领域结果，不能把旧 closed request 重新变 open。依据 DB:297–305、SV:588–617,637–645、command_receipts.py:16–64。 |
| tasks.owner | 删除输入与显示后做完整读者检查；若仅自报标签读者均已撤下则删整列，并改内部写者，不留空列。依据 DB:141、S:219–224、BS:475–491。 |
| tasks.spec_json / workflow_runs.execution_workspace_json / request_fingerprint | 当前可执行规格转换为新形式，去 Host 名、提交令牌与已撤字段，明确 checkout/access/归属；内部 Router requiredCapabilities 保留。request_fingerprint 若仍负责同键不同意图冲突，换明确当前形式；若不再读则删列。原始任务文字与封存 turn 输入、receipt、物理 seal 的字节不动；不能运行时按写入年代猜 JSON 意义。旧幂等键转换受 D4 约束。 |
| exclusiveResources 与 resource_claims | 删 Host/helper 的可变声明及无读者分支；已冻结 spec 中的旧声明仍是原证据。resource_claims 整表仍给 cwd/执行准入使用，不能删表；若撤下 exclusive 分支，升级库存中的 exclusive claim 必须有明确转换/清除与 fixture，不能遗留 held 的无消费者事实。依据 DB:307–317、BS:835–859,1805–1810。 |
| buddy_locked | 保留或删除由 D1 决定；删时转换全部 9 个真实使用语义及 fixture 非零库存，保留时保持原值且只在显式 buddy 下允许条件字段。 |
| projects（新增结构化记录，名称建议） | 显式记录 projectId 与当前种类 git/folder；folder 存规范化路径身份，Git 仍用 WS:232–278 的标记，不能都改成按路径认。由现有 macro_tasks/standalone 及固定输入解析所有旧项目为 git，不访问当下目录猜种类。项目的路径定位事实不放 meta。 |
| 宏任务与微任务的项目事实 | 空宏任务自己持有创建时的项目路径/种类，不能再从首个 run 读。每个新 run/回合显式固定项目种类与 checkout 方式，使 folder 后来变 Git 不改变旧记录。项目 id 的现有 macro/standalone 互斥存法继续避免重复；新增的创建时种类是冻结事实，不能靠当前 projects.kind 推。依据 DB:501–503,532–535、MT:216–235。 |
| Git 后来出现在 folder | 首次以 Git 使用时，把原项目 id 写入 Git 标记并记录当前 git 种类；新任务按新种类，旧宏任务/微任务的 folder/none 事实不改。文件夹搬移/改名仍按新路径成新项目；不做路径别名、自动追踪搬家或改写历史。依据 ADR:69–76。 |
| workflow_runs/turns 的工作区约束 | 把必须有 workspace_id/manifest 的全称约束改为显式 checkout 分支：worktree/existing 仍要固定 Git 输入；none 的 cwd、access、任务/回合身份完整，但 Git 工作区列为空。新旧行的 checkout 值明确存下，已有行从封存输入/执行工作区事实转换，不能从现场 .git 推断。依据 DB:469–490,516–535。 |
| workflow_artifacts 的结果型分支 | 增加明确的固定回合结果形式，建议 content_form=turn-result-reference，携带 run/turn/attempt、结果文件位置与摘要；不能填虚构 output_commit/workspace_id 来通过原 CHECK。文件产物保留 sealed-inline-v1/input-reference/record-reference 各自含义。依据 DB:850–918、protocol/workspace_manifest.py:119–176。 |
| workflow_continuations 的 workspace_facts_role/kind | 为 none 增加明确不持有 Git 工作区事实的分支；pending/owner/reference 的旧库存继续按现义转换。Git owner/reference 的单一位置事实保留；新续派的回应身份、scope 与结果绑定同时写入。依据 DB:635–677、tasks/workspace_bindings.py:9–17。 |
| 空宏任务新建键、回应事实、通知消费事实 | 各自结构化存储，不塞 meta/任意 JSON key；跟所属 macro/run 一起删除。避免再存可由固定关系推出的计数、路径副本与完整当前视图。D2/D4/D6 冻结后才给最终 DDL。 |

这里没有一张可整表删除的独立“Host 控制表”：当前控制信息分布在 workflow_runs、workflow_routes、commands 与各记录列，文件位于 controls/submissions。不能为了表述成“删整套”而误删 commands、agent_credentials、resource_claims、workspace_reservations。删去的每一列都有读者清单；当回应表取代旧结构后，同批删除不再读的 decision_json 或重复列，实施审查逐列确认，没有“以后也许会用”的保留。

冻结形式单独处理：新 Git 输入、turn input、seal/output record 与 none result 使用各自明确的新版本/判别；旧 manifest、native checkpoint、receipt、物理封存、输出补丁及其摘要全保持原字节。当前 seal version=1 已记录 headCommit 与合成 commit（WS:1574–1588），新交付可以从这些事实给正确整合候选，但不能把旧 commit 字段就地改名或换值。只有新 seal 能采用新形式。关系行重建和索引变化与改写 digest 绑定内容是两件事。

### 样板黑板及生成器的库存清单

生成与恢复入口为 `packaging/generate-upgrade-fixture.py:1937–1968`、`tests/python/blackboard/store/upgrade_fixture.py:21–35,52–95`；固定地板和来源记载见 `tests/fixtures/upgrade-floor/README.md:3–29`。当前库存已有 requiredCapabilities 的空/decision 非空、exclusiveResources 的空形式，不能把普通测试中的非空例子当成 fixture 有存量（README:5）。补量要扩生成器并记录 writer commit、生成命令、原库存摘要、新库存摘要，禁止手写一个来源不明的 schema-34 数据库代替。

| 转换/删除的形式 | 必须确认或补出的库存与断言 |
| --- | --- |
| Host/Lead/helper、宏任务/standalone | 各有行；Host 三个标签列、Lead 的 NULL 旧分支、helper 根谱系都覆盖；升级后角色归属相同，名字不再授权，目标历史仍可读。 |
| owner_generation=1 与真实 takeover 后的代 | 地板 writer 经原 takeover 生成 >1 的库存，附初代提交和旧控制文件/令牌记录；日常无人用不能省这个已存在形式。升级后无控制列、无旧 takeover 回执恢复，原微任务与晚到路由历史不丢。 |
| submission_verifier 有值/无值、控制与提交文件 | 各有库存；生成器可捕获测试中的私有文件，断言安装退役只删两类已认定文件，不碰服务 endpoint、执行凭证。 |
| open/已批准/已拒绝/superseded/cancelled 请求 | 把每种存在状态及其 decision_json、command_id、helper 关系迁过去；回答后的重发仍认原动作，不得因删 commands 又创建 helper。 |
| manual/helper/lead-wakeup 续派及 recorded/queued/consumed/invalidated/cancelled | 覆盖实际要转换的每种 kind/state、expected_revision 和 workspace owner/reference/pending 形式；仍有效的自动触发只消费一次，旧失效触发不能复活。 |
| accept/conclude/scope/resolve/goal 的旧回执 | 覆盖目标整合、notRequired、adjusted、hostPaths/beforeCommit、部分/完整 resolve、终态 Host 结案与目标历史；转换后固定 action 身份与结果关系可读，真实执行状态不改成成功。 |
| buddy_locked=false/true | 必补 true，使用地板 writer 的完整 buddy 锁定提交；D1 删/留两方案各有明确断言。实际 9 条使用不等于 fixture 已有。 |
| owner 有/无；exclusiveResources 空/非空；requiredCapabilities Host 空/非空与内部 decision 非空 | 用地板原写者补所有要删/改的形态，特别是非空 exclusiveResources 与 Host 硬能力；断言内部 Router 的能力仍在、Host 新请求不再接受撤下字段；不把日常“没人用”当转换证据。 |
| Git 输入/输出各形式及版本 | 复用已有 v1/v2 派生库存、v3 地板、schema33 的 v4 增量；经 33→34 后覆盖 sealed-inline-v1/input-reference/record-reference、Worker/Host/resolve/partial/abandoned/shared-refs，逐个摘要与文件字节保持。新 result 形式只由当前 writer 写，不伪称地板曾写过 none。 |
| 回收计划 planned/applying/applied/blocked、已释放/仍持有 reservation | 被本步约束重建或清列的每种形式都有库存；原归属与停止边界保留，不因新 none 分支释放仍活的 Git writer。 |
| 项目与新建键 | 多个 worktree 同一 Git 项目、宏任务/standalone、无显式 title、相同项目同名宏任务/相同 brief 的旧任务；按 D4 的决策验证转换，不能取最早一条就悄悄去重。 |
| 事件 actor 与历史 Host 名 | 覆盖 host/lead/console/service/workflow/bridge 中本步会改结构的形式；旧历史字节保持，新 Host 无 id 分支合法；约束不放宽到 Lead 缺身份。 |

每项先查现 fixture 是否已有，缺少才补生成场景；旧版已经不写、地板只携带的形式按 AGENTS.md 从地板写出的行派生，并在 fixture 旁记下逐字段来源。不能改已运行的 schema_30_31 等步骤来“制造缺的形式”。所有 34→35 转换/删除分支必须与上述库存对应，链测试对库存非空先断言再跑转换，并比较升级板与新建板；另用当前 writer 检查空宏任务、folder、none 与新回应规则，二者不能互代。

## 5. 快照提交与整合验证

当前 `_commit_tree` 为确定性恢复使用固定快照身份与日期（WS:617–626）；seal 在树与 inputTree 不同时总造快照提交，另记观察到的 headCommit（WS:1572–1603）。Host 视图把 record.commit 当 outputCommit（W:529–582、cli/cli_views.py:130–133），控制台显示它（FE/task-detail/AcceptancePane.tsx:68–72）。这条链必须一起改，不能只改 skill 的合并例句。

新交付分开表达固定证据与整合来源：artifactId/记录摘要仍固定封存；完整的 Worker 自己的提交/提交区间经服务检查其基线、祖先与树覆盖关系后作为可整合来源；有未提交内容、Worker 提交只覆盖部分封存、或 resolved/Host 产物时，给已固定的累计 patch 及摘要作为整合来源。不能仅凭存在 HEAD 就称“整合这个提交”。原始输入可能含 Host 未提交内容和选入的 inputs，核验必须确保候选提交带的是应交付改动，不能把基线带入目标历史。

Worker HEAD 的树与实际 seal 树完全相同且对应正确输入时可复用该真实提交，不再无条件另造；需要合成对象才能固定未提交树、保住崩溃恢复时，仍可作为私有证据，交付明确提示应用 patch，不把它列为要合并的提交。恢复使用同一已选记录与摘要，不换成仓库身份/真实时间制造新对象，不替 Worker 改历史。新 seal 的记录形式应把 worker commit/证据 commit/patch 的含义分清；旧 seal 的 headCommit 只能在已有证据足够时给候选，否则诚实给 patch。依据 backlog:204–213、WS:1485–1511,1572–1620。

`WS:2182–2213` 当前还硬性要求非 inputCommit 的产物只有一个父提交且恰为最终 inputCommit，这正是合成快照的假设；不能在 seal 复用 Worker HEAD 后漏改这个读者。新形式用固定的 Worker 提交区间/共同基线与树、patch 关系核验，允许 Worker 的多个真实提交；旧 version=1 仍按它原来的父绑定核，不改旧记录使其看起来符合新规则。恢复与验收必须共用对应形式的验证，不能只改可见 outputCommit。

accept 继续显式目标 `target.path/ref` 或 notRequired；不把执行检出自己、源 cwd 的当前 HEAD 或回复候选当成 Host 的默认整合选择。干净三方合并以原目标与产物从共同基线合成的树为证，目标等于它即原样整合；不能仅以 artifact commit 为目标祖先判成功，也不能继续要求同一文件逐 blob 等于 artifact。当前 `WS:2232–2321` 的内容比较及 `W:4060–4105` 的 differing 拒绝是修改处，保留 adjusted+note 表达 Host 真正改过的内容。

三方核验需要明确“整合前目标”的证据。已有 beforeCommit 目前只在 hostPaths 场景要求（W:3895–3903、WS:2242–2269）；新增干净合并要覆盖多个 Worker 改同文件的不同行、cherry-pick、patch、删除/重命名、不同目标 checkout、未提交基线等实际允许的整合方式。原目标 commit 怎么取得、何时需要 Host 明填 beforeCommit 见 D5；不可由当前 HEAD 随意倒退一代猜测。冲突或目标实质调整继续需要 Host 明确承认，hostPaths 仍只用于产物路径以外的真实 Host 补充。

### 验收和回收之后的引用读者

下表是源码能证实的读者链。它足以说明“已回收检出”不等于“可删所有 Git 引用”；每条是否能在保留期内完全改读固定文件，还需后续在私有样本的断引用实验确认。本计划不指定一个未经决定的天数。

| 读者 | 现在读取什么、保留到什么事实成立 |
| --- | --- |
| get/控制台审查与累计 patch | W:496–527,529–582；sealed-inline-v1 读已存 cumulativePatch，record-reference 从固定 patch 用 git apply --numstat 读路径，源码明确不打开 checkout/对象库。这里可确认展示这份 patch 本身不需要保留 Git 对象；seal 时生成累计 patch（WS:1606–1620）仍需要对象，不能混成一条读取。 |
| accept 与封存真实性校验 | WS:2150–2230,2232–2254；按 input/output commit、tree、parent、patch hash 核固定来源。未验收及验收的有效重放完成前不能破坏来源。 |
| 下一回合输入/共享只读输入 | WS:696–713,772–824,1104–1124；可引用旧封存作为输入选择。别的未结束任务还持有某封存时，原任务验收不使该读者消失。 |
| 现场处置与结案部分产物 | W:4587–4666,5213–5232、WS:1984–2011；要用原输入与固定产物做 resolve/累计 patch，开放的冲突及尚未结案的现场仍有读者。 |
| 仓库搬移定位与检出回收 | WS:281–313,2406–2468,2637–2765；用 project 标记、retained/input、分配 provenance 证明所回收对象。检出回收受阻期间不能删掉定位与清场证据。 |
| 存储计划与宏任务删除 | tasks/storage.py:192–228、tasks/workspace_bindings.py:26–42、macro_task_deletion.py:370–377；固定范围与输出引用名参与所有权判断，新的保留策略必须同步删除路径。 |
| 导出样本/升级验收 | tools/export_board_sample.py:204–257；复制所选记录引用的 manifest、output record、patch；必须核清导出/恢复是否还依赖 Git 对象，不能把全仓库复制进样本来绕开。 |

实施先按引用种类建立“生产者→保留读者→终止条件”清单，并在私有样本中证明停止、验收、回收后 get/复核/删除仍完整；开发者再定 input、staged、outputs、resolutions、abandoned、retained 各类何时退役。建议实际仍有读者就保留，读者已全改读固定文件且停止/回收证据成立就撤下多余引用；不沿用原生目录的三天常量，也不因为已经 merged 就删除用户项目历史。引用寿命与“不要把快照提交合入项目”可以分别实施，最终保留政策未定前不得宣称引用累积已解决。

## 6. checkout: none 的执行与验收

none 的 Worker 实际 cwd 就是请求 cwd，黑板不准备检出、不复制输入、不调用 Git 快照/状态检查；即使 cwd 已有 .git，也按这一回合固定的 none 意义执行。用户要缩小能读到的内容，Host 自己建空目录再提交；权限档位按 harness 的原生机制落实，读取范围仍取决于原生 harness。相关分支穿过 `W:1587–1660,1863–1886,6104–6293`、`protocol/turn_input.py`、`buddy/runtime/execution.py`、结果导入与工作区清理，不能只在 submit 校验器接受一个新取值。

每回合结果固定为一份带 runId、turnId、attemptId、generation、结果文件位置及摘要的产物，artifactId 是它的回应/验收身份。复用现有 execution_result 的私有结果回执与 publication 路径，在服务核归属、来源及摘要后登记引用，不复制一份可能漂移的报告文本当第二份结果；跨进程传文件引用，不塞无界结果。completed 才是可验收结果；assistance/attention 保留固定本回合结果并另给 requestId，后续回合产生新 artifactId；打回以后旧结果仍可审但不再是当前可验收项。依据 `PC:104–113`、`W:7176–7194`、DB:850–906，新增判别不能要求 Git output_commit。

none 的 read 只交结果；write 明确允许在 cwd 改文件，结果、get/await 和控制台同时标明“文件夹的改动未封存”。full 适用同样的未封存提示和必填理由。不能把结果里写的文件路径当成已封存产物或已固定输入，不设置能误解为“零文件改动”的 diff。验收检查任务书与固定结果；纯回答时报告与结果可以同一份，公开术语与流程不得称已经得到三份独立证据。

accept 必须命中确切 result artifactId 并带核对 note；无 Git 整合时明写 notRequired 的理由，不能因为 checkout=none 自动替 Host 选择。若 Host 另外把文件整合到 Git，是否允许按 target 验收该未封存文件现场见 D3；建议本批的固定验收仍只绑定结果，Host 自看文件的说明留 note。none 没有可回收的工作目录，accept/conclude/cancel 与宏任务删除只能清黑板私有结果、凭证/会话/记录，绝不递归删 cwd。

none 不持有 worktree/existing 的长期 workspace_reservation，不制造空 manifest。执行层现有按 cwd 的准入/claim 仍按实际执行目录运行，停止之后按原规则释放；不额外添加文件夹互斥机制或宣称 none 提供多个写者的隔离。`BS:835–859,1683–1687` 的执行资源与 `DB:691–710` 的长期检出预约必须区分。把普通文件夹当固定输入、封存文件夹改动均留后续。

## 7. 控制台的变化

宏任务列表当前从 matched members 构造 groups（MT:432–458），项目显示还 JOIN 首个 run 与 input artifact（MT:216–235）。改为以 macro_tasks 为主体 LEFT JOIN 成员聚合，独立微任务仍走自己的分组；空宏任务计数为 0、目标可读、无执行与等待验收标记，删除预览/执行允许零成员。搜索、筛选、排序、分页、项目总数与 activity marker 都要定义空组的含义，不让空组因 inner join 丢失；建议沿用已有创建/活动时间排序，空组无执行状态，筛到 active/host/review 时不命中，具体呈现经 D8 冻结。

项目摘要显式带 kind 及路径/label；Git 项目各 worktree 仍同组，folder 按规范化路径分组，后来 git init 继续同 projectId；旧 none 结果仍按旧形式显示。新增信息进入 `FE/macro-task-types.ts:15–32` 的 Zod 解析、`FE/api.ts:435–477`、MacroTaskList/Overview、预览夹具与列表查询缓存；不能只改一个卡片而让顶层项目统计继续把文件夹当缺失 Git 资料。

当前归属改按谱系显示 Host 或某个 Lead，删除 hostId/sourceHostId/currentHostIds、ownerGeneration 与 controlFile 的显示和筛选：`micro_task.py:204–207,305–327,386–388`、`MT:46,268–275`、`SV:182–214`；前端 `actor.ts:35–36`、`console-data.ts:100`、`macro-task-types.ts:15–32`、`MacroTaskOverview.tsx:73,101`、`task-detail/DelegationPane.tsx:39–43`、`task-detail/parse.ts:281,490,520`、`types.ts:140,190–191` 一起改。不得从 harness/线程号重建 Host 身份；Lead 的真实归属 id 继续可见，历史动作记录不作为当前凭证。

微任务的回合、交付与验收页对 Git 文件产物与固定回合结果做明确分支；none 显示结果的固定身份、内容、权限与“文件夹的改动未封存”，不展示 Git commit/patch 的空壳字段。Git 交付则分开固定封存与正确的 Worker 提交/patch 整合提示；更新 AcceptancePane、TurnPane、types/parse、中文英文词条。执行问题来自当前事实，待验收/待 Host 的记号仍按现有语义，停止未知不能画成结束。遵守 ADR-029:21–35 的层次、形状、颜色和真实浏览器核验，不为新增字段另造一排诊断徽章。

## 8. 文档改写清单（整合 Host 负责）

本微任务只列需要改的文档，不在这里代写产品说明。整合 Host 在接口与待定项稳定后完成，skill 与两份 Host 指南的措辞按 ADR:148 先给开发者看；角色说明属于实现阶段必须同步的产品代码文本。每个 Markdown 自然段仍一行。

| 文档 | 改写内容与依据 |
| --- | --- |
| skills/hey-my-buddy/SKILL.md | 十个动词加 help/console、最小委派圈与不变原则；字段细节转 help，运维只链 operations 的 Host 一节；去控制文件/接管/先 health/低层轮询。 |
| docs/reference/host-codex.md | 等待流程只组合 await/get；不再让 Host/监视者记控制代、revision、命令号；收尾用项目/宏任务 get 找待处理集合，保留原样交回等待结果的职责。 |
| docs/reference/host-claude-code.md | 同样更新后台 await、重发恢复与动作 id；保留原生等待/唤醒边界，不承诺尚未接入的跨回合门铃。 |
| docs/reference/usage.md | 先 create 可空宏任务、submit 显式字段、none、continue 变体、准确整合来源、accept 固定目标/notRequired、新问题与重发。 |
| docs/reference/workflow.md | 删控制能力/接管流程，新增 id 回应一致性、helper 与 Lead 谱系、未来 scope 与现场处置、结果型验收、真实停止与自动回收。 |
| docs/reference/operations.md | 新增写给 Host 的运维一节：逐命令的触发场景、须用户同意的具体事项、调用方式；覆盖 install/stop/restart、health/harness 路径与冷却重试、storage plan/apply、仓库搬移定位、运行时/位置/会话诊断。共享操作的能力不随 CLI 精简删除。 |
| docs/reference/cli.md | 按 Host/运维/角色列现行入口，由模型给字段事实；撤下旧名字，不留下兼容例句；开发工具另链，不收进 Host 方法数。 |
| AGENTS.md | owner fencing 改为 Worker/Lead 执行权限与 request/artifact 一次回应、task/attempt 与实际停止；记录 34→35 为新第五步、开发工具入口及相应检查，不再要求 Host 控制代。 |
| CONTEXT.md | 按 ADR 实现节改项目、允许空的宏任务，新增不带检出的微任务；封存与验收区分文件产物和固定结果，三方交叉验证允许纯回答只有两样；删除接管，保留 buddy 换手/改派；预期时长不再称默认半小时。 |
| docs/reference/architecture.md、macro-tasks.md、workspace-lifecycle.md、console.md | 更新命名操作、schema/升级链、空宏任务与项目模型、Git/none 结果、回收引用与归属显示；保留所有仍有效的所有权、停止、保密与删除边界。 |
| docs/reference/decision.md、evaluation.md、workers.md、harnesses.md、harness-integration.md、codex.md | 改低层 CLI/开发工具例句、路由问题读取入口、角色调用与字段名；不把跨 harness 原生事实统一或 Router 画像工作顺带塞入本批。 |
| docs/README.md、README.md、README.zh-CN.md | 索引随拥有页更新，README 中英文对齐文件夹/结果工作范围与验收边界；详细字段留在参考/help。 |
| docs/design/backlog.md、相关旧 ADR 的修订说明、docs/acceptance/ 下本批记录 | 整合 Host 在实施与实际验收后记录哪些已完成、哪些待定；ADR-004 的接管政策及 ADR-022 的默认时长按已接受 ADR-032 说明修订，不能删除仍有效的不变量。 |
| tools/ 的说明 | 开发工具各自的用途、无模型检查与可能主动发模型请求的区别；export sample 的授权/路径/删除规则、AGENTS.md 的入口链接。 |

## 9. C1 建议同批实施

建议 C1 同批。ADR 第 6 条已经要求合法组合由请求模型定义，help 与拒绝都从它推出；若先用手写校验改完这些入口，再做 Pydantic，要在同一批新字段、条件与拒绝上做两遍迁移，并第二次触发 Host 契约熟悉成本。Pydantic 已在 `pyproject.toml:16–24` 声明，`protocol/internal_models.py:1–35` 已有模型与错误边界用法；本批不需要为它加新框架，也不需要真实模型验证。C1 的旧“55 入口、6 批”仅是 `docs/acceptance/wheels/second-wave.md:5–7` 的未实施计划，本批以第 1 节的保留入口重新划分，不能照旧数机械搬运。

一次完成的顺序是：先冻结本计划待定的请求形状与回应规则；为十个 Host 动词、运维/角色入口和保留命名操作声明请求模型；把授权、归属、执行方式做成没有默认值的必填或条件分支；把字段上限、合法组合、错误位置和 help 元数据收在同一声明。CLI 只处理自己的本地字段与派生新建键，服务对外来领域请求验证一次，内部传类型；执行进程收到的输入仍是另一进程边界，要保留其已有核验，不能借“只验证一次”删掉跨进程身份/摘要核验。

可复用 internal_models 的约束和错误转换思路，但不能直接继承它全部配置：该模块明确允许 populate_by_name 与默认值（internal_models.py:7–14），新公开请求不能因此意外接受旧字段、snake_case 别名、字符串布尔或隐式授权默认。模型拒绝未知字段，必要的字符串/字节/路径边界沿本批决定明确；不保留 AST 推断器以兜底未迁入口。帮助保留每方法一句用途文字，其余从模型 schema/条件声明生成；跨字段规则的说明要来自同一条件模型，不能维护一份 CLI 条件表、一份服务 if、一份 help 文案。

不逐字节保留旧校验器的报错先后与怪异默认，C1 已获准简化；保留真正的边界意义与上限，记录变化让 Host 审查。验证以真实请求能否准入/拒绝、help 是否只出现当前受众字段及其合法组合、模型是否影响角色边界为准，不写一排只是复述每个字段声明的测试。C1 是本批的实施建议，待整合 Host/开发者确认排期；若不同批，ADR 所需 Host 请求模型与 help 仍须同批落地，剩余运维/内部入口暂按现行边界，明确记录 AST 尚未完全退役，不能称 C1 已完成。

## 10. 阶段、微任务与验收顺序

实施分配前先冻结每份任务的文件写入范围。同一个 W、S、DB、SV 或 FE 类型文件只能有一个写者；并行任务在独立检出做各自模块，接口由整合 Host 持有，跨范围修改交回它处理。以下检查都是未来实施的要求，本计划轮次没有执行任何一项；完整 Python 检查均经 `uv run --frozen python -m hey_my_buddy.cli.checks`，不直接启动测试文件。命令后的路径是受影响集合，可在实际新增测试文件时扩展。

| 阶段与微任务 | 写入范围 | 前后依赖与并行 | 受影响检查及完成条件 |
| --- | --- | --- | --- |
| P0 冻结待定与基线 | docs/design/adr032/、私有证据；整合 Host 记录决定 | 必须先于涉及 D1–D8 的实现；可同时准备只读读者清单 | 核对第 1 节入口全覆盖、D 项逐条有决定/明确暂缓边界、确定 C1 范围及当前安装基线。没有定的能力不靠实现者猜。 |
| P1 请求模型、受众注册与帮助 | protocol 的新请求模型/类型、schemas.py、contracts.py、transport.py、cli_help.py、对应协议/CLI 测试；W/SV 改动由整合者串行接入 | P0 后，先提交稳定的输入/回复类型给 P2–P5；这是模型与名称的单一写者 | `tests/python/cli/test_cli.py`、`test_host_cli.py`、`test_blocking_commands.py`、`tests/python/blackboard/service/test_workflow_validation.py`、`tests/python/protocol/test_contract_version.py`；help 受众、严格组合、误字段拒绝与新建键计算可检查。删旧 AST/别名有完整消费者替换。 |
| P2a schema 35 与库存 | blackboard/store/db.py、schema_34_35.py、upgrade.py、packaging/generate-upgrade-fixture.py、tests/fixtures/upgrade-floor/、store 升级测试、install 的保存/验证适配 | P1 类型稳定后；可与 P2b/P3 的新模块准备并行，DB/原 W 的实际接线串行。P2a 与 P2b 合成一个可启动候选才算完成 | `tests/python/blackboard/store/test_upgrade_chain.py`、`test_upgrade_fixture.py`、`test_domain_schema.py`、新增 `test_schema_34_35.py`；各转换分支有非空库存、旧封存字节不变、升级后结构与新板相同，各源 schema 均可走到 35。 |
| P2b Host 守卫、新建与回应事务 | tasks/workflow.py、command_receipts.py、macro_tasks.py、micro_task.py、home.py、service/service.py、cli/main.py、protocol/client.py、相关生命周期测试 | P1 后；依赖 P0 的 id/旧键决定和 P2a 的 schema；中央文件独占，不能与 P3–P5 争写 | `tests/python/blackboard/tasks/test_host_workflow.py`、`test_workflow_boundaries.py`、`test_workflow_routing.py`、`test_lead_owned.py`、`test_lead_grant.py`、`test_lead_review_boundaries.py`、`tests/python/cli/test_host_cli.py`。重发/并发同 id 仅一次，另一动作冲突，Worker/Lead 选择器越权拒绝，晚到路由/旧自动触发不复活。 |
| P3 Git 交付来源与三方整合 | tasks/workspace.py 及相关 Git 验证模块、protocol/workspace_manifest.py、输出事实类型、workspace/integration 测试；W 适配由 P2b 写者接入 | P1 形状稳定后可在独立检出准备；依赖 D5 与 P2a 的新输出判别；不能与 P4 同改旧 WS/W | `tests/python/blackboard/tasks/test_workspace_output_views.py`、`test_workspace_lifecycle_acceptance.py`、`test_workspace_review.py`、`test_workspace_git_truth.py`、`tests/python/protocol/test_workspace_manifest_references.py`。真实 Worker 提交不换成快照提交，未提交/混合产物给正确 patch；干净三方合并算原样，真实 Host 调整仍需承认，恢复不造第二个 seal。 |
| P4 项目、空宏任务、none 执行 | 新项目/结果模块、tasks/workspace_bindings.py、turn_input/结果格式、buddy runtime/角色文本、macro_task_deletion 适配、相关测试；DB/W/SV 接线由中央写者做 | P2a/P2b 后，P3 输出类型稳定；可在不重叠的新文件准备逻辑，不与 P3 并改 WS | `tests/python/blackboard/tasks/test_macro_tasks.py`、`test_macro_task_goals.py`、`test_macro_task_deletion.py`、`test_lead_workspace.py`、`test_workflow_worker.py`、`tests/python/protocol/test_result_references.py`、`tests/python/buddy/roles/test_schema_worker.py`，新增 none/folder 场景。none 在请求 cwd 运行、无 Git/复制路径、旧结果固定、验收只绑定所选结果、删除不动 cwd；空宏任务零成员可读可删，Git 化不改历史种类。 |
| P5 await/Lead 新消息与运维入口、开发工具 | cli/blocking.py、任务等待/问题消费/Lead 唤醒模块、tools/、运维 CLI 与边界模型；W/SV 接线由中央写者处理 | P2b 后；D6/D7 冻结；可与 P3/P4 的独立模块并行，最后一起接线 | `tests/python/cli/test_blocking.py`、`test_process_observation_retry.py`、`tests/python/blackboard/tasks/test_workflow_stop_surface.py`、`test_lead_lifecycle.py`、`test_lead_shutdown.py`、`tests/python/protocol/test_state_boundaries.py`。持续问题只返回一次，恢复后再出现返回新件，服务重启/等待中断不丢未读项，Lead 只收门铃后读内容；运维/开发工具不能变成微任务旁路。 |
| P6 控制台与构建资产 | console/、apps/console/、src/hey_my_buddy/console/assets/、控制台 Python/前端/浏览器测试 | P1/P4/P5 回复稳定后；可与 P3/P5 的非界面工作并行，预览夹具必须用最终模型 | `tests/python/console/` 和相关 macro_task 读取/删除测试；`npm --prefix apps/console test`、`npm --prefix apps/console run test:browser`、`npm --prefix apps/console run build`。覆盖暗色/英文/窄容器下空宏任务、folder、none、问题事实与无 Host 控制显示；真实浏览器按几何事实断言，提交构建资产。 |
| P7 全面整合、独立审查与文档 | 整合 Host 持有的版本/契约签名、包装清单、文档拥有页、docs/acceptance/；修复由对应文件写者承担 | P2–P6 都完成；文档第 8 节在最终形状稳定后写。独立审查在完整删守卫差异形成后进行 | 完整 `uv run --frozen python -m hey_my_buddy.cli.checks`，加最终前端/browser 检查；协议签名与中间版本一致，旧入口/列/文件路径没有运行消费者。独立审查达到下面明确条件后才允许进安装演练。 |
| P8 安装前演练与安装候选交接 | 私有样本/私有安装与运行时目录、导出工具、安装保存验证与 docs/acceptance/；不让 Worker 使用日常板 | P7 通过后；与日常真实安装分开，演练不等于安装 | 从 Host 提供的当前安装版本到候选的完整演练；34→35 在真实数据导出的样本与生成 fixture 都通过，旧版本请求能发给旧服务，失败恢复/重新运行明确；输出差异、摘要、退出码、清场证据与未验证边界后交 Host。 |

P1–P6 的测试用现有 fake harness/合成回执与私有目录；不发真实模型请求。纯角色权限与机器状态转换不用真实模型证明。跨平台的路径、Git 对象与安装/进程行为按仓库已有 CI 的 macOS/Linux/Windows 边界给证据，未跑的平台明确记未验证；不能把一台机器的成功扩成所有平台，也不以运行真实模型来掩盖可确定性检查的缺口。

必须另请一次独立审查，专看删除守卫前后的完整差异。输入是最终候选的 immutable diff、本计划与 ADR、调用方/读者清单、迁移与并发检查证据；审查者未参与对应实现。逐条确认仅放弃同用户多 Host 的互斥/接管旧代保证，仍保留 Worker/Lead 限域、控制台用户设置身份、task/attempt/generation/nonce、路由取消围栏、一次回应、固定产物/结果、scope 与文件删除边界、提交时状态重查和真实停止证据；同时确认没有用新 token、默认值、缓存或多余 guard 把删掉的 Host 机制换名搬回。现有 ADR 草案 15 条审查不能充当本次实现审查，Worker 成功消息也不能当 Host 验收。

安装前真实样本演练由整合 Host 另行授权执行：在源状态目录之外的新空目录运行 `uv run --frozen python tools/export_board_sample.py <outside-state>/<new-empty-directory>`，最多 100 个已验收微任务且无未结束执行；在受管 checkout 开发时，先导出到源状态目录之外，再把这个导出的私有样本用于检查。禁止复制日常 SQLite/状态目录或从样本反写日常板。本轮没有执行导出。样本保持 schema 34，先记录行数、项目/归组、历史与文件摘要，再通过真正安装升级 34→35；打开 get/宏任务/控制台、读固定结果/patch、再用副本演练空宏任务和 none，不把对样本的加工称为原板数据。

真实样本只覆盖已验收存量，不能证明 open request、queued continuation、停止未知等分支，后者由地板 fixture 的真实写者库存加私有合成运行覆盖。导出工具要同时能读源 schema 34 和新 schema 35 的明确结构，这是开发工具的两个明确输入形式，不能通过给产品加旧契约兼容来实现；`tools/export_board_sample.py:140–141` 目前会写控制 verifier，须改为按来源结构处理。演练后依既有检查清场规则删除样本/私有进程与临时根，记录实际退出码；退出 3 的清场未证实不当通过。

安装候选改 Host 契约、执行输入/结果与存储，须升中间号并更新 CONTRACT_VERSION（基线版本为 0.60.1、契约 0.60.0，见 pyproject.toml:3、CT:17；若届时没有别的中间版本则候选为 0.61.0）。当前计划文档不升版本。按 ADR-031 第 10 条与 AGENTS.md 的安装要求，演练必须从当时真实安装的旧运行时发它支持的 detach/health 等字段，并证明契约切换时无旧执行继续向新板写旧形式；不能只在新建 schema-35 板跑一遍 install。当前安装版本与演练平台需 Host 提供新证据，本轮没有读取已安装包。

## 11. 风险与待定

下列是实施前的待定，建议不构成新决定。D1 来自本轮明确保留的开发者判断；其余是 ADR 定义了方向、源码尚缺无歧义落法的地方。计划可以先被审阅，受影响微任务的契约/DDL 必须等决定写回后冻结；无需为了写本计划去运行日常板或真实模型。

| 编号 | 需要开发者定的事、已有证据 | 建议、代价与还需证据 |
| --- | --- | --- |
| D1 | buddyLocked 删或留。ADR:66 写删，但 Host 本轮明确要求再次裁定，真实有 9 项 true；W:2846–2855 是实际约束。 | 建议看这 9 项的任务意图后裁定。删：去字段、列、路由/换手约束与 UI，显式 buddy 以后可换，放弃原硬锁保证，迁移非零库存；留：只对显式 buddy 开条件字段并保留原值与约束，route 不接受锁，增加一项模型/help/续派及自动改派测试成本。不能由实施者按“多数不用”决定。 |
| D2 | requestId/artifactId 尚未覆盖所有写动作：执行中 cancel、尚未交付的主动 continue、无产物失败 conclude、空宏任务 amend，以及可多次部分处置的同一 conflict。当前只有 assistance/attention 等会开 workflow_requests（DB:29–45、W:7334–7395）；旧 amend 用 goal revision、cancel 可不带 expectedRevision。 | 建议服务给这些实际可操作情形稳定的回应 requestId，amend 绑定确切目标版本，部分处置结束后另给剩余现场的新 requestId；不增加命令号、秘密句柄或“最后看过”缓存。需要开发者确认哪些动作必须带哪一个 id，以及 cancel 的重发是否永久作用于首次请求的停止范围；用源码状态表枚举反例后再定，不把活跃任务伪装为等待 Host。 |
| D3 | 最终请求组合与角色外形：read 的 writeScope 是 [] 还是不出现；none 的 target 验收是否有合法形式；helperPolicy/autoContinue 的省略规则；helper 的 runId/targetRunId、现场只处置/处置并续派的原子性；get 集合选择器与 inquire 只读旧问询的拼写。ADR 给能力，未给所有字段组合。 | 建议沿“无授权不默认、无变更保留当前、字段不适用就不出现”：read 不带非空 scope，none 只验固定结果且明写 notRequired，helper 目标随已回的 id 给出；full/Lead 沿 ADR 与现行约束保留，不另撤销。先写最终模型草图与一份合法/非法组合表，给开发者定；实现不要先宽接收再忽略字段。 |
| D4 | 新建键的旧行映射和冲突：旧 create 随 submit 可重复同名；旧 task 的 title 可缺；同新键不同 cwd/access/buddy/input 路径也可能出现。现指纹含 Host 与整个 workspace（S:415–431），不能直接当新键。旧 commands 只存 request_hash（DB:297–305），旧手动续派也未必记它打回的 artifactId，不能凭时间顺序猜出新的回应关系。 | 建议同键不同授权/执行意图拒绝且回原 id，重做换 title；旧任务/宏任务全部保留，不把重复行合并或任意选一条。需要开发者定旧键是仅给无歧义组回填、旧行不参加新键恢复，还是其他明确规则；旧动作只转换可由 request/continuation/integration/conclusion 证明的关系，其余清除无读者回执并保留关闭事实，具体是否影响新契约下重放须列清后决定。先用私有样本统计重复组、缺 title 及不可还原的回执，样本没有的边界用地板 writer 造 stock。还需明确删除宏任务后同 title 新建的寿命语义，建议沿现有“记录已删便无重放库存”，不另造 tombstone。 |
| D5a | 封存引用何时删除。backlog:212 要先查读者再定，当前仍有固定来源校验、输入续接、回收定位、删除与历史读取。 | 建议第 5 节每条读者链在私有样本断引用实验后给出终止条件，已无读者且停止/回收成立就删多余引用；不填任意保留天数。还需确认旧 seal 脱离 Git 对象后能否保留既有审查能力及被其他活任务引用的条件。此项未定，不能宣称存储累积已修复。 |
| D5b | 干净三方合并中的“原目标”从何而来。当前 beforeCommit 仅为 hostPaths 可选输入，祖先关系不足以还原 Host 实际合入前状态（WS:2242–2269）。 | 建议允许服务在真实 merge 的 parent 证据充分时推导，patch/cherry-pick 或不足时由 Host 明写 beforeCommit 并机械验证其祖先/基线；具体是否条件必填交开发者定。需要覆盖并行同文件不同行、目标后续提交、合成输入基线与非 merge 整合的私有 Git 实验，不能用当前 ref 的上一提交作通用推断。 |
| D6 | “新问题 await 返回一次”按什么持久范围消费，Lead 怎样只收门铃再经 await 读；没了 hostId 后，两个等待者、回复丢失、进程重启与问题清除再出现的读取位置尚未定义。DB:250–264 只有当前问题，不是已读账本；BL:522–524 目前消费的是提醒，W:143–153 是独立 Lead 事件清单。 | 建议复用已有事件/提醒消费思路和统一新消息定义，记录问题出现/消除的稳定身份，不把每次 observed_at 更新都当新问题，也不为 Host 建会话身份。需开发者明确“一次”针对 run/等待订阅/消费游标中的哪一个，以及恢复命令要带回什么；门铃平台接入若另批，明确暂缓部分，不能本批声称跨回合唤醒已完成。 |
| D7 | 仓库搬移定位的运维命令名与字段。ADR:37,90 已定归运维，旧 reclaim/continue 的 cwd 能告诉服务新路径（W:2962–2970、原 help/reclaim.txt）。 | 建议一个只登记/验证新仓库位置并触发现有回收的运维动作，命令名在 P0 定；保留 projectId/固定标记、停止与所有权检查，不恢复 Host reclaim。不加文件夹搬家跟踪，因为 folder 改名即新项目。需要最终 help 与 operations 的 Host 一节中给出具体调用。 |
| D8 | 空宏任务的列表状态/筛选/呈现及 folder 标识的最终画法。ADR 已定显示与删除，但当前列表状态只从成员算（MT:432–480），没有零成员状态。 | 建议空宏任务保留目标与 0 个成员，无执行/验收记号；创建/活动排序沿现规则，active/host/review 筛选不命中，全部/文本查询可见。先给开发者一份暗色静态稿及接口样例再实施，避免把显示决策藏进 SQL COALESCE 或状态默认。 |

最大的回归风险在删 Host 守卫时一并误删真正的跨进程/角色/删除边界，及新回应表只挡单一方法却挡不住同一产物被另一个动词消费；因此 P2 的并发事务检查与 P7 独立差异审查是验收门槛。普通同时两个 Host 管同一个任务的互斥已被 ADR 明确放弃，不再为这个已放弃保证另建机制。

迁移风险集中在三处：新幂等键与旧库存一对多、旧封存摘要被误改、关系 CHECK 只为 none 放宽却允许 Git 缺输入。每项都由明确形式、对应 writer 库存与新板结构比对约束；真实样本不含所有运行状态，必须和 fixture 合用。文档、前端类型、help 与服务操作同一契约发布，不把旧 CLI 别名或空列当过渡方案。

交接时逐阶段区分“计划、实现、受影响检查、完整检查、独立审查、样本演练、Host 验收、安装”。当前交付仅完成执行计划；D 项不是已实现能力，候选版本、实际平台和引用保留期限都须以之后的证据更新。
