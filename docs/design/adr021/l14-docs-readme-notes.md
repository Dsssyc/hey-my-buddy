# ADR-021 L14：两份 README 的源码核对说明

以输入提交 `68e7d2e2` 的当前源码为准，只修改 `README.md`、`README.zh-CN.md` 与本说明。两页原有结构与标题保留，安装命令的版本号、日常安装版本与安装状态的原说法保留，英文仍为英文。核对采用源码阅读与 `rg`，没有运行 CLI、测试、检查器、服务、安装或模型调用；没有联网、读取用户 harness 设置或操作日常黑板。此说明是 Worker 的交付记录，尚待 Host 验收。

## 名字的处数

`README.md` 改名 47 处；`README.zh-CN.md` 改名 49 处。按实际替换的独立名字或散文用语的出现位置计数；完整安装路径连同启动器算一处，同一个导航串里的不同页名分别计数，shell 变量的定义与各次引用分别计数。整句删除中没有留下对应新名字的旧词不计入改名数。下表可逐项相加；名称项与后面的行为修正可能出现在同一句，但两者统计不同的东西。

| 原名字或用语 | 当前名字或用语 | 英文页 | 中文页 |
| --- | --- | ---: | ---: |
| ~/.agents/skills/buddy/scripts/buddy | ~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy | 1 | 1 |
| $HOME/.agents/skills/buddy/scripts/buddy | $HOME/.agents/skills/hey-my-buddy/scripts/hey-my-buddy | 1 | 1 |
| ~/.agents/skills/buddy | ~/.agents/skills/hey-my-buddy | 2 | 2 |
| ~/.claude/skills/buddy | ~/.claude/skills/hey-my-buddy | 2 | 2 |
| buddy backup-preflight | hey-my-buddy backup-preflight | 1 | 1 |
| buddy | hey-my-buddy | 2 | 2 |
| ... adapters | ... harnesses | 1 | 1 |
| adapter/provider/model/effort | harness/provider/model/effort | 1 | 1 |
| adapter/provider/model | harness/provider/model | 1 | 1 |
| BUDDY= | HMB_CLI= | 1 | 1 |
| $BUDDY | $HMB_CLI | 4 | 4 |
| objective reuse | macro task reuse | 1 | 0 |
| work objectives | macro tasks | 1 | 0 |
| model profiles | buddies | 1 | 0 |
| the profiles in Buddy 配置 | the buddies in Buddy 配置 | 1 | 0 |
| model configuration | buddy | 1 | 0 |
| complete configuration | complete buddy | 1 | 0 |
| a configuration change | a buddy change | 1 | 0 |
| configuration changes on continuation | buddy changes on continuation | 1 | 0 |
| configuration binding | buddy binding | 1 | 0 |
| Claude Code adapter | Claude Code harness | 1 | 0 |
| P1 adapter | P1 harness | 1 | 0 |
| harness adapters | harnesses | 1 | 0 |
| Goals | Micro tasks | 1 | 0 |
| goal | micro task | 6 | 0 |
| delegations | micro tasks | 1 | 0 |
| delegation | micro task | 2 | 0 |
| governed attention/assistance boundary | attention/assistance boundary | 1 | 0 |
| 委派记录, Buddy 配置 and 设置 | Tasks, Buddies and Settings | 3 | 0 |
| Buddy 配置 | Buddies | 2 | 0 |
| The 设置 page | The Settings page | 1 | 0 |
| 重新检测 | Check | 1 | 0 |
| 拥有目标 | 拥有微任务 | 0 | 1 |
| 整体目标 | 微任务 | 0 | 1 |
| 目标、决定 | 微任务、决定 | 0 | 1 |
| 确定目标 | 确定微任务 | 0 | 1 |
| 同目标委派 | 同一宏任务内委派 | 0 | 1 |
| 有界目标 | 有界微任务 | 0 | 1 |
| 停止目标 | 停止微任务 | 0 | 1 |
| 仅在目标、 | 仅在微任务、 | 0 | 1 |
| 工作目标以 | 宏任务以 | 0 | 1 |
| 共享模型配置 | 共享 buddy | 0 | 1 |
| 模型参数 | buddy | 0 | 1 |
| 给出完整配置 | 给出完整 buddy | 0 | 1 |
| 改配续做 | 更换 buddy 续做 | 0 | 1 |
| 配置绑定 | buddy 绑定 | 0 | 1 |
| 配置发生变化 | buddy 发生变化 | 0 | 1 |
| 选择配置 | 选择 buddy | 0 | 1 |
| Claude Code 适配器 | Claude Code harness | 0 | 1 |
| P1 适配器 | P1 harness | 0 | 1 |
| harness 适配器 | harness | 0 | 1 |
| 只读委派链路 | 只读微任务链路 | 0 | 2 |
| 委派时间轴 | 微任务时间轴 | 0 | 1 |
| 独立委派折叠 | 独立微任务折叠 | 0 | 1 |
| 每个运行中的委派 | 每个运行中的微任务 | 0 | 1 |
| 委派按来源项目 | 微任务按来源项目 | 0 | 1 |
| 原始委派方 | 原始 Host | 0 | 1 |
| “委派记录 / Buddy 配置 / 设置” | “任务 / Buddy / 设置” | 0 | 2 |
| Buddy 配置 | Buddy | 0 | 2 |
| 重新检测 | 检测 | 0 | 2 |

`HMB_CLI` 是示例自己定义的 shell 变量，不是产品读取的环境变量；源代码实际读取的变量统一使用 `HMB_`。示例文件 `buddy-doc-review.md`、请求值 `doc-links-1`、Host 值 `codex`、`/abs/repo` 与返回标识占位符是调用者自选的输入，不是系统的固定名字。两页没有指向 `docs/reference/objectives.md` 的链接，因此没有新增宏任务参考链接；将来指向该页应写 `docs/reference/macro-tasks.md`。

## 源码已删除的说明

以下逐项指出删掉或改掉的旧功能说明；每页与小节的具体前后说法及源码依据在下一节逐处列出。

- 两页“如何协作 / How buddies collaborate”第一步：删掉部分 buddy 身份可以路由与最多八条任务级软偏好的说明。当前仅接受完整四元组或全部省略，Router 输入没有 Host 维护的评价卡片；依据 `src/hey_my_buddy/protocol/schemas.py:78-110,576-618`、`src/hey_my_buddy/blackboard/routing/decision.py:323-343`。
- 两页安装示例及说明：删掉 `workspace:false` 与 DSH 分组功能；当前提交不接受 `workspace`，工作区内也不接受重复的 `cwd`、`integrator`；依据 `src/hey_my_buddy/protocol/schemas.py:352-377,557-561`、`src/hey_my_buddy/blackboard/tasks/worker_sessions.py:93-99`。
- 两页控制台设置说明：删掉版本闸门及按版本产生的冲突说明；保存后写生效，草稿仍按窗口保留；依据 `src/hey_my_buddy/blackboard/evaluation/evaluation.py:205`、`src/hey_my_buddy/blackboard/evaluation/user_policy.py:120-239`、`src/hey_my_buddy/console/server.py:415-450`。
- 两页控制台 buddy 说明：删掉各 effort 是独立评价目标的说法，改为独立 buddy 身份。英文页独有的“更新记录显示发布修订”删除，中文原页没有这句，不补入已删除功能；依据 `src/hey_my_buddy/blackboard/store/db.py:317`、`src/hey_my_buddy/blackboard/routing/decision.py:323-343`、`apps/console/src/App.tsx:25`。
- 两页编辑范围：偏好与启用状态明确归 buddy，备注与并发归模型服务，删掉隐含的模型服务偏好层；依据 `src/hey_my_buddy/blackboard/evaluation/user_policy.py:7-9,120-171,203-229`。
- 两页路由说明：删掉提交参数 `routingMode`、`allowRoutingFallback` 与审阅不可用时切快速路由的说法；按共享模式遍历有序 Router buddy 列表；依据 `src/hey_my_buddy/protocol/schemas.py:78-110`、`src/hey_my_buddy/blackboard/routing/router.py:316-395`。
- 两页许可证之后的 B 批说明：删掉 `harness-verify`、当前版本审阅验证和新版本先降级的说明。CLI 没有该命令，资格判断只确认能力及当前可用性；依据 `src/hey_my_buddy/cli/main.py:37-115`、`src/hey_my_buddy/blackboard/routing/router.py:140-183`。
- 两页安装、执行与恢复及备份预检说明：删掉旧默认目录归档、旧布局整理与唯一滚动备份的描述，写明待执行的升级归档旧黑板与证据、新建黑板并只带走设置；依据 `src/hey_my_buddy/home.py:19-24`、`src/hey_my_buddy/install/upgrade.py:377-399,730-750`。

两页没有 Host 评价维护操作、读者租约字段、表列名或视图的 `governed` 字段可删；英文页边界名称中的修饰词 `governed` 已去掉。没有新增被删除操作的替代入口或兼容名字。

## 与源码不符而修正的说法

共 42 处，英文页 21 处、中文页 21 处；一个独立错误断言或无效参数为一处，两页分别计数。安装示例的三个无效字段分别计数。原说法与新说法是对应位置的摘录，已同步改名的术语不再重复写旧拼写。

1. `README.md`，How buddies collaborate。原：A complete harness/provider/model/effort tuple is validated and dispatched directly; anything less is routed through the Router of the effective mode (fast or review) and the bounded evaluation table, while up to eight task-local soft routing preferences influence only that micro task and never change shared settings. 现：A complete harness/provider/model/effort tuple is validated and dispatched directly; omit all four fields to let the Router choose from bounded candidates using the shared routing mode and user preferences. 依据：`src/hey_my_buddy/protocol/schemas.py:78-110,576-618；src/hey_my_buddy/blackboard/routing/decision.py:323-343`。
2. `README.zh-CN.md`，如何协作。原：完整的 harness/provider/model/effort 组合经过校验后直接派发；信息不完整时通过生效模式的路由模型（快速或审阅）和有界评价表完成路由，最多八条任务级软路由偏好只影响该微任务，不改变共享设置。 现：完整的 harness/provider/model/effort 组合经过校验后直接派发；省略全部四个字段时，Router 按共享路由模式与用户偏好从有界候选中选择。 依据：`src/hey_my_buddy/protocol/schemas.py:78-110,576-618；src/hey_my_buddy/blackboard/routing/decision.py:323-343`。
3. `README.md`，Install／bounded micro task example。原：,"workspace":false 现：删除该参数或该句，不增加替代参数。 依据：`src/hey_my_buddy/protocol/schemas.py:78-110（没有 workspace）`。
4. `README.zh-CN.md`，安装／有界微任务示例。原：,"workspace":false 现：删除该参数或该句，不增加替代参数。 依据：`src/hey_my_buddy/protocol/schemas.py:78-110（没有 workspace）`。
5. `README.md`，Install／bounded micro task example。原："kind":"worktree","cwd":"/abs/repo","access":"write" 现："kind":"worktree","access":"write" 依据：`src/hey_my_buddy/protocol/schemas.py:352-377,557-561（cwd 与 integrator 由提交派生）`。
6. `README.zh-CN.md`，安装／有界微任务示例。原："kind":"worktree","cwd":"/abs/repo","access":"write" 现："kind":"worktree","access":"write" 依据：`src/hey_my_buddy/protocol/schemas.py:352-377,557-561（cwd 与 integrator 由提交派生）`。
7. `README.md`，Install／bounded micro task example。原：,"integrator":"codex" 现：删除该参数或该句，不增加替代参数。 依据：`src/hey_my_buddy/protocol/schemas.py:363-371,557-561`。
8. `README.zh-CN.md`，安装／有界微任务示例。原：,"integrator":"codex" 现：删除该参数或该句，不增加替代参数。 依据：`src/hey_my_buddy/protocol/schemas.py:363-371,557-561`。
9. `README.md`，Install／example explanation。原：The example leaves the model choice to routing and disables DSH session grouping with `workspace:false`. Grouping is a DSH option independent of Git isolation; `executionWorkspace` is the ownership and artifact-verification contract. 现：The example leaves the buddy choice to routing; `executionWorkspace` is the ownership and artifact-verification contract. Its source directory and integrator are derived from the top-level working directory and submitting Host. 依据：`src/hey_my_buddy/blackboard/tasks/worker_sessions.py:93-99；src/hey_my_buddy/protocol/schemas.py:352-377,557-561`。
10. `README.zh-CN.md`，安装／示例说明。原：示例把模型选择交给路由，并用 `workspace:false` 关闭 DSH 会话分组。分组是独立于 Git 隔离的 DSH 选项；`executionWorkspace` 才是工作区所有权和产物校验契约。 现：示例把 buddy 选择交给路由；`executionWorkspace` 是工作区所有权和产物校验契约。来源目录与整合者由顶层工作目录和提交的 Host 派生。 依据：`src/hey_my_buddy/blackboard/tasks/worker_sessions.py:93-99；src/hey_my_buddy/protocol/schemas.py:352-377,557-561`。
11. `README.md`，Console, routing and models／settings。原：Every admitted window may edit settings under revision checks; drafts and conflicts stay per window. 现：Every admitted window may edit settings; later saves take effect, and drafts stay per window. 依据：`src/hey_my_buddy/blackboard/evaluation/user_policy.py:120-239；src/hey_my_buddy/console/server.py:415-450；apps/console/src/draft.ts:268-317`。
12. `README.zh-CN.md`，控制台、路由与模型／设置。原：每个获准访问的窗口都可以在版本检查下编辑设置；草稿与冲突按窗口保留。 现：每个获准访问的窗口都可以编辑设置；后保存的内容生效，草稿按窗口保留。 依据：`src/hey_my_buddy/blackboard/evaluation/user_policy.py:120-239；src/hey_my_buddy/console/server.py:415-450；apps/console/src/draft.ts:268-317`。
13. `README.md`，Console, routing and models／buddy page。原：keeps thinking efforts as independent evaluation targets 现：each reasoning effort identifies a distinct buddy 依据：`src/hey_my_buddy/blackboard/store/db.py:317（buddies 表的四段身份）；src/hey_my_buddy/blackboard/routing/decision.py:323-343（输入没有评价卡片）`。
14. `README.zh-CN.md`，控制台、路由与模型／buddy 页面。原：各推理强度保留独立评价 现：每种推理强度标识一个独立的 buddy 依据：`src/hey_my_buddy/blackboard/store/db.py:317（buddies 表的四段身份）；src/hey_my_buddy/blackboard/routing/decision.py:323-343（输入没有评价卡片）`。
15. `README.md`，Console, routing and models／model rows。原：each model service row shows its enabled efforts and a Router mark, and enabled variants have a visible check, with the routing status line showing both Router slots and the default mode. 现：Each model service row shows its enabled efforts and concurrent-attempt occupancy; enablement is edited separately for each buddy. The roles view shows the ordered Router list and the shared mode. 依据：`apps/console/src/buddies/ModelLine.tsx:22-59；apps/console/src/buddies/ModelDrawer.tsx:71-75,170-181；apps/console/src/buddies/RolesView.tsx:96-116`。
16. `README.zh-CN.md`，控制台、路由与模型／模型行。原：模型服务一行显示已启用的推理强度数与 Router 标记，已启用的推理强度有勾选标记，路由状态行分别显示两个 Router 位置与默认模式。 现：模型服务一行显示已启用的推理强度和并发占用；每个 buddy 分别编辑启用状态。“角色”分区显示有序 Router 列表与共享模式。 依据：`apps/console/src/buddies/ModelLine.tsx:22-59；apps/console/src/buddies/ModelDrawer.tsx:71-75,170-181；apps/console/src/buddies/RolesView.tsx:96-116`。
17. `README.md`，Console, routing and models／editable fields。原：your own preferences, model service notes, enablement, model service concurrency and the Router slots 现：your own buddy preferences and enablement, model service notes and concurrency, and the Router list 依据：`src/hey_my_buddy/blackboard/evaluation/user_policy.py:7-9,120-171,203-229`。
18. `README.zh-CN.md`，控制台、路由与模型／可编辑字段。原：你自己的偏好、模型服务的备注、启用状态、模型服务的并发设置和 Router 位置 现：你自己的 buddy 偏好与启用状态、模型服务的备注与并发设置和 Router 列表 依据：`src/hey_my_buddy/blackboard/evaluation/user_policy.py:7-9,120-171,203-229`。
19. `README.md`，Console, routing and models／buddy page。原：`更新记录` shows published revisions, and the 设置 page holds 现：The 设置 page holds 依据：`src/hey_my_buddy/cli/main.py:37-115；apps/console/src/App.tsx；src/hey_my_buddy/blackboard/evaluation/user_policy.py（已删除评价修订历史）`。
20. `README.md`，Console, routing and models／routing。原：A submission may pick `routingMode`, and an unavailable review Router falls back to fast routing unless the Host passes `allowRoutingFallback:false`. 现：The shared mode applies to an ordered Router buddy list; routing uses its first available enabled buddy. If none is available, the micro task returns to the Host at a routing boundary. 依据：`src/hey_my_buddy/protocol/schemas.py:78-110；src/hey_my_buddy/blackboard/routing/router.py:316-395；src/hey_my_buddy/blackboard/tasks/workflow.py:routing_settled`。
21. `README.zh-CN.md`，控制台、路由与模型／路由。原：提交时可指定 `routingMode`；审阅 Router 不可用时默认降级到快速路由，除非 Host 传入 `allowRoutingFallback:false`。 现：共享模式适用于有序 Router buddy 列表；路由使用其中第一个可用且已启用的 buddy。没有可用项时，微任务在路由边界交回 Host。 依据：`src/hey_my_buddy/protocol/schemas.py:78-110；src/hey_my_buddy/blackboard/routing/router.py:316-395；src/hey_my_buddy/blackboard/tasks/workflow.py:routing_settled`。
22. `README.md`，Current status and limits／Claude。原：Claude P1 requires first-party Anthropic authentication, uses isolated settings by default and reconstructs every continuation without inquiry. 现：Claude P1 reads the user's native provider, account and settings, and reconstructs every continuation without inquiry. 依据：`src/hey_my_buddy/buddy/harnesses/claude/config.py:56-61,136-151；src/hey_my_buddy/buddy/harnesses/claude/native_run.py:141-146,1076-1082`。
23. `README.zh-CN.md`，当前状态与限制／Claude。原：Claude P1 需要 Anthropic 第一方认证，默认使用隔离设置，每次接续都重建会话，未声明 inquiry。 现：Claude P1 读取用户原生的提供方、账户与设置，每次接续都重建会话，未声明 inquiry。 依据：`src/hey_my_buddy/buddy/harnesses/claude/config.py:56-61,136-151；src/hey_my_buddy/buddy/harnesses/claude/native_run.py:141-146,1076-1082`。
24. `README.zh-CN.md`，引言／Claude。原：默认采用开发者确认的隔离设置； 现：删除该参数或该句，不增加替代参数。 依据：`src/hey_my_buddy/buddy/harnesses/claude/config.py:136-151（用户设置源保持启用；英文原页没有此句）`。
25. `README.md`，License／B-stage source paragraph。原：B-stage source adds native billing labels, exhaustion-aware candidate eligibility and explicit current-version review verification in Buddy configuration or `harness-verify`; new versions fall back to fast routing until verified. 现：B-stage source adds native billing labels and exhaustion-aware candidate eligibility. 依据：`src/hey_my_buddy/cli/main.py:37-115；src/hey_my_buddy/blackboard/routing/router.py:140-183,316-395；src/hey_my_buddy/blackboard/routing/decision.py:211-241`。
26. `README.zh-CN.md`，许可证／B 批源码段落。原：B 批源码加入原生计费标注、额度耗尽的候选过滤，以及 Buddy 配置和 `harness-verify` 的当前版本审阅验证；新版本验证前沿用快速路由降级。 现：B 批源码加入原生计费标注和额度耗尽的候选过滤。 依据：`src/hey_my_buddy/cli/main.py:37-115；src/hey_my_buddy/blackboard/routing/router.py:140-183,316-395；src/hey_my_buddy/blackboard/routing/decision.py:211-241`。
27. `README.md`，License／backup preflight paragraph。原：The installer checks this inventory before stopping the service and relocates recognized stopped old layouts only after verified backup; credentials and native private directories stay outside backup. 现：The installer checks this inventory before stopping the service; the pending upgrade archives the verified old board and creates a new board carrying only settings. Credentials and native private directories stay outside backup. 依据：`src/hey_my_buddy/install/upgrade.py:730-750；src/hey_my_buddy/blackboard/store/migrations.py:read_settings,write_settings；src/hey_my_buddy/blackboard/store/backup.py:preflight`。
28. `README.zh-CN.md`，许可证／备份预检段落。原：安装器在停机前检查该清单，已识别且停止已证明的旧布局只在已验证备份后整理；凭据与原生私有目录不进入备份。 现：安装器在停机前检查该清单；待执行的升级归档已校验的旧黑板，新建只带走设置的黑板。凭据与原生私有目录不进入备份。 依据：`src/hey_my_buddy/install/upgrade.py:730-750；src/hey_my_buddy/blackboard/store/migrations.py:read_settings,write_settings；src/hey_my_buddy/blackboard/store/backup.py:preflight`。
29. `README.md`，Install／upgrade readiness。原：refuses while work is running (`UPGRADE_NOT_IDLE`, listing the running tasks) 现：refuses while micro tasks remain open, stops are unconfirmed or managed checkouts remain (`UPGRADE_NOT_IDLE`, listing the blockers) 依据：`src/hey_my_buddy/install/upgrade.py:346-374`。
30. `README.zh-CN.md`，安装／升级前提。原：有任务在运行时拒绝并列出这些任务（`UPGRADE_NOT_IDLE`） 现：有未结束的微任务、未确认的停止或未回收的受管检出时拒绝并列出阻塞项（`UPGRADE_NOT_IDLE`） 依据：`src/hey_my_buddy/install/upgrade.py:346-374`。
31. `README.md`，Install／backup and rollback。原：with one verified rolling backup and rollback. 现：with a verified backup and rollback; the pending schema upgrade keeps that backup as a permanent archive. 依据：`src/hey_my_buddy/install/upgrade.py:377-399,730-750`。
32. `README.zh-CN.md`，安装／备份与回滚。原：并保留唯一一份校验过的滚动备份用于回滚。 现：并保留校验过的备份用于回滚；待执行的 schema 升级把这份备份保留为永久归档。 依据：`src/hey_my_buddy/install/upgrade.py:377-399,730-750`。
33. `README.md`，License／repository verification。原：the complete check includes synthetic preview data consumed by the actual frontend parsers. 现：the complete Python check includes synthetic preview data consumed by the actual frontend parsers. Frontend tests and browser checks run separately; see [AGENTS.md](../AGENTS.md). 依据：`src/hey_my_buddy/cli/checks.py（仅发现 tests/python 下的测试）；apps/console/package.json:12-14`。
34. `README.zh-CN.md`，许可证／仓库验证。原：完整检查包含由真实前端解析器读取合成预览数据的回归测试。 现：完整 Python 检查包含由真实前端解析器读取合成预览数据的回归测试。前端测试与浏览器检查单独运行，见 [AGENTS.md](../AGENTS.md)。 依据：`src/hey_my_buddy/cli/checks.py（仅发现 tests/python 下的测试）；apps/console/package.json:12-14`。
35. `README.md`，Execution and recovery／private state。原：an older board directory at the previous default location is retained as an archive and is never read, converted or imported. 现：the pending upgrade keeps the old board and historical evidence in a verified archive and creates a new board carrying only settings. 依据：`src/hey_my_buddy/home.py:19-24；src/hey_my_buddy/install/upgrade.py:377-399,730-750（归档在当前状态目录；旧布局换算已删除）`。
36. `README.zh-CN.md`，执行与恢复／私有状态。原：位于旧默认位置的旧黑板目录保留为归档，不会被读取、转换或导入。 现：待执行的升级把旧黑板与历史证据保留为校验过的归档，新建只带走设置的黑板。 依据：`src/hey_my_buddy/home.py:19-24；src/hey_my_buddy/install/upgrade.py:377-399,730-750（归档在当前状态目录；旧布局换算已删除）`。
37. `README.md`，Console, routing and models／standalone micro tasks。原：standalone delegations fold under 未归档委派 现：standalone micro tasks form a separate collapsible group 依据：`apps/console/src/MacroTaskList.tsx:237-247（当前显示历史独立委派；用宏任务之外的微任务说明折叠方式，不沿用旧名称）`。
38. `README.zh-CN.md`，控制台、路由与模型／独立微任务。原：独立委派折叠在“未归档委派”中 现：独立微任务另行折叠 依据：`apps/console/src/MacroTaskList.tsx:237-247（当前显示历史独立委派；用宏任务之外的微任务说明折叠方式，不沿用旧名称）`。

39. `README.md`，Console, routing and models／provider exposure。原：fast routing sends every task description to the fast Router's model provider 现：a fast Router call sends the task description to that Router's model provider 依据：`src/hey_my_buddy/protocol/schemas.py:576-618；src/hey_my_buddy/blackboard/routing/decision.py（仅一个候选时由程序选择，不调用 Router）`。
40. `README.zh-CN.md`，控制台、路由与模型／提供方可见内容。原：快速路由会把每个任务的描述发送给快速 Router 所在的模型提供方 现：快速 Router 调用会把任务描述发送给该 Router 所在的模型提供方 依据：`src/hey_my_buddy/protocol/schemas.py:576-618；src/hey_my_buddy/blackboard/routing/decision.py（仅一个候选时由程序选择，不调用 Router）`。
41. `README.md`，Execution and recovery／cancel。原：only an explicit `cancel` stops a micro task, and shutdown is reported only with real stop evidence. 现：Use an explicit `cancel` to stop a micro task; shutdown is reported only with real stop evidence. 依据：`src/hey_my_buddy/protocol/schemas.py:160-164；src/hey_my_buddy/buddy/roles/run_execution.py:422-423（执行到期也能停止运行）`。
42. `README.zh-CN.md`，执行与恢复／取消。原：只有显式 `cancel` 才会停止微任务，并且只有拿到真实停止证据才报告已关闭。 现：可用显式 `cancel` 停止微任务，并且只有拿到真实停止证据才报告已关闭。 依据：`src/hey_my_buddy/protocol/schemas.py:160-164；src/hey_my_buddy/buddy/roles/run_execution.py:422-423（执行到期也能停止运行）`。

另按任务写法要求，删除两页引言中 DeepSeek Harness 的外部仓库地址，保留 harness 名称；这两处只涉及链接写法，不计入上述 42 处源码行为修正。

## 代码字体名字的逐项核对

相同名字在两页或一页重复出现时复用同一源码依据。命令未执行，只读取其入口、验证器与对应实现；以下覆盖现有行内代码与代码块中的固定命令、字段、取值、错误码和路径。

| README 中的名字 | 当前源码依据 |
| --- | --- |
| hey-my-buddy 包命令、install、uvx 的两种安装写法 | `pyproject.toml:14`，`src/hey_my_buddy/install/package_install.py`，`install.sh:66`、`install.ps1:62`；固定版本及 wheel 名字按任务要求保留，未验证发布可用性。 |
| hey-my-buddy skill、两侧 skill 目录、scripts/hey-my-buddy | `src/hey_my_buddy/install/skill_package.py:9,30,105-111`，`src/hey_my_buddy/install/skill_install.py:3-4,34,74,116`。 |
| health、runtime、harnesses、submit、get、await、console、status、cancel、backup-preflight | `src/hey_my_buddy/cli/main.py:37-115`，`src/hey_my_buddy/cli/blocking.py`，`src/hey_my_buddy/blackboard/store/backup.py:295-304`。 |
| UPGRADE_NOT_IDLE、LAUNCH_ACCESS_DENIED、updated | `src/hey_my_buddy/install/upgrade.py:372`，`src/hey_my_buddy/protocol/transport.py:326`，`src/hey_my_buddy/install/skill_install.py:112,234`。 |
| requestId、hostId、task、cwd、timeoutSeconds、executionWorkspace | `src/hey_my_buddy/protocol/schemas.py:78-104,246-263,545-569`。 |
| kind、worktree、access、write、base、working-tree、includeUntracked、writeScope | `src/hey_my_buddy/protocol/schemas.py:332-410`。 |
| runId、controlFile | `src/hey_my_buddy/cli/main.py:337-429`、`src/hey_my_buddy/blackboard/tasks/workflow.py`；标识字段仍叫 runId，没有改成 microTaskId。 |
| console 的 browser:false、wait:true | `src/hey_my_buddy/cli/console_cli.py:27-30,106-107`。 |
| health.capacity | `src/hey_my_buddy/blackboard/service/service.py:298-321` 的 health 回应下的 capacity；不是一个独立命令或字面字段。 |
| dsh、zcode、codex、claude；harness/provider/model/effort | `src/hey_my_buddy/protocol/schemas.py:40-42,576-618`、`src/hey_my_buddy/buddy/harnesses/registry.py:25`。 |
| standard | `src/hey_my_buddy/blackboard/routing/router.py:17-20`、`src/hey_my_buddy/blackboard/routing/router_settings.py:11-24`。 |
| openai / gpt-6-sol / high | openai 见 `src/hey_my_buddy/buddy/harnesses/codex/config.py:11`；具体模型及 effort 来自原生目录，当前源码无 gpt-6-sol 字面记录，这四处历史验证说明列入“拿不准”。 |
| sourceCommit | 中文页已有的字段见 `src/hey_my_buddy/install/skill_package.py:45,122-123`；英文页保留同一原意。 |
| ~/.local/share/hey-my-buddy/state、%LOCALAPPDATA%、$HOME、PATH | `src/hey_my_buddy/home.py:1-24`、`install.sh`、`install.ps1`；后面三项是操作系统或 shell 名字，不是产品改名对象。 |
| uv、uv build --wheel --out-dir dist、install.sh、install.ps1 | 仓库的两份 bootstrap 脚本和 `pyproject.toml` 的构建及安装入口；uv 的命令语法属于外部工具，未联网或执行构建。 |
| apps/console/package.json、npm --prefix apps/console ci、uv run --frozen python -m hey_my_buddy.cli.checks | 实际文件 `apps/console/package.json`，`src/hey_my_buddy/cli/checks.py:1-17,212-241`、`tests/python/cli/test_host_preview.py:17,93-106`；只核对名字与执行边界，没有执行这些命令。 |

`timeoutSeconds` 并未在这个输入提交中改成预期时长：当前验证器仍以 1800 秒为默认、接受 0，Worker 运行层仍使用执行期限（`src/hey_my_buddy/protocol/schemas.py:23-29,160-164`、`src/hey_my_buddy/buddy/roles/run_execution.py:342,422-423`）。两页按源码保留原段；术语表描述的预期时长不是这里已接通的能力。

## 拿不准

共 6 处，英文页 3 处、中文页 3 处，均保留行为原意，只同步旧术语。未把历史记录扩写成当前版本、当前用户的可用性结论。

1. `README.md`，Execution and recovery 第一段：每个运行中的微任务由最低成本、最低推理强度的 Codex 原生子代理监控，Claude Code 使用后台 Bash。当前运行源码不驱动 Host 的原生子代理或选择其模型，`skills/hey-my-buddy/SKILL.md` 也没有这条明确规则，不能仅凭源码判断该 Host 使用指南是否仍成立；原意保留。
2. `README.zh-CN.md`，执行与恢复第一段：与英文同一监控策略；当前源码同样无法确认，原意保留。
3. `README.md`，Console, routing and models 路由段：0.19.0 / CLI 0.157.0 的 openai / gpt-6-sol / high 实测结论。当前源码通过原生目录读取模型、提供方和 effort，没有 gpt-6-sol 的字面名字，不能据此确认历史实测组合或其目前是否仍可用；原句保留。
4. `README.zh-CN.md`，控制台、路由与模型路由段：与英文同一历史实测组合；当前源码同样无法确认，原句保留。
5. `README.md`，Current status and limits：重复出现的同一历史 Router 实测组合；按一个独立页面位置另计，原句保留。
6. `README.zh-CN.md`，当前状态与限制：重复出现的同一历史 Router 实测组合；按一个独立页面位置另计，原句保留。

## 标题与锚点

两份 README 的标题文字均未修改，没有旧锚点与新锚点的对应项。

## 核对时发现的源码自身问题

- `src/hey_my_buddy/cli/main.py:3` 的模块说明仍写 “Everything the plugin offers”，与当前共享 skill 的分发说法不符。只记录，没有修改。
- `src/hey_my_buddy/buddy/harnesses/registry.py:94,101` 的 Codex 与 Claude 提示词仍有 “Buddy root turn” 和 “Buddy micro task”，此处 Buddy 用作产品或运行边界的修饰词。只记录，没有修改。
- `src/hey_my_buddy/protocol/schemas.py:633` 的错误文字列出 “dsh, zcode or codex”，同一验证器实际支持的 CODING_HARNESSES 还含 claude；`schemas.py:578` 的注释重复写 “micro-task micro task”。只记录，没有修改。
- `apps/console/src/MacroTaskList.tsx:47,185,238-246`、`apps/console/src/MacroTaskTimeline.tsx:840` 仍显示“工作目标”“历史独立委派”等旧用语；新导航已是“任务 / Buddy / 设置”。README 对功能使用宏任务与微任务，不伪造源码里不存在的新界面标签。只记录，没有修改。

## 交付边界

仅完成两页与本说明的静态源码核对。所有临时记录位于系统临时目录；没有写入源码、测试、前端、打包或其他文档，也没有提交、切换分支、操作标签或 stash。版本、发布及安装状态由 Host 后续统一处理。
