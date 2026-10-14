# 改名这一步的执行计划

[ADR-021](../decisions/021-router-buddy-planes-and-routing-evidence.md) 的 L14，连同并进来的挪升级基点、先删的那一套与随它做的待办。整合 Host 于 2026-10-13 起草，依据是开发者当日逐项定下的东西；它是 ADR 与微任务之间的那一层，微任务的简报从这里取范围与次序。还没有开始实现。

## 定下的东西

- 挪升级基点并进这一步，作为它的开头；基点是装着的 0.34.1（`4b7f4946`，schema 18）。
- 这一次升级清空工作历史，只带走设置，旧看板归档（ADR-021 第 15 条 2026-10-13 的补充）。
- 评价表与设置的版本号作为闸门的用法、读者与写者的租约、“作废”这个状态、Host 维护评价的那组操作与评价卡片，在改名之前先删；Router 的答案发布时只检查选中的 buddy 此刻仍是合法候选。微任务自己的 `revision` 与 `expectedRevision` 保留。
- 名字：`family` 一族改称模型服务 / model service（模型 / model 指具体的大语言模型，不论由谁提供；开发者 2026-10-13 稍后改定）；环境变量的前缀改为 `HMB_`；被 Host 结束的微任务的终态是 `concluded`；skill、包与命令统一叫 `hey-my-buddy`。
- 这一步做完才安装，一次升级。中途的树不装到日常看板上：从第一个微任务合入起，任何一次安装都会清空工作历史。

## 做完时的样子

- 一块新看板只有现在用得着的表，名字都是术语表里的词；`meta` 只剩单值的键。
- 从 0.34.1 的看板升级上来：旧看板连同证据文件成为一份单独的归档，新看板里只有带过来的设置，其余是空的。
- Host 一侧的接口、控制台的接口、事件名、状态值与排队原因里没有已经废弃的词；装好的 skill、启动器与命令叫 `hey-my-buddy`，环境变量以 `HMB_` 开头。
- 版本 0.35.0。

## 升级怎样做

升级不再逐表换算。备份校验通过之后，这份备份就是归档：它移到状态目录下单独的归档位置，以后的升级不覆盖它，存储回收不把它列进计划，何时删除由用户决定；归档里记着写它的那个提交与 schema。随后新建一块当前形式的看板，把设置原样带过去，再清掉状态目录里属于旧历史的证据目录、控制文件与提交记录。带过去的设置只有这几样：Router 的列表、模式、预算档与重试间隔；buddy 表里各行的启用状态；固定、优先与排除偏好；模型的备注；模型的并发上限。控制台自己的设置文件不在看板里，原样留着。

升级之前要满足的，不满足就拒绝，并且什么都不改：没有还开着的微任务（执行中、排队、等 Host、等协助、已交付而既未验收也未结束），没有停止未确认的执行，没有还留着的受管检出。回应里列出是哪些。

新服务验证不过时照现在的做法退回上一代，旧看板原样恢复；归档校验不过时不删除任何东西。

因为升级的结果是“新看板加设置”，后面的每个微任务可以直接改新看板的形式，不写换算；要跟着改的只有带设置的那一小段：它读的是 schema 18 的名字，写的是当前的名字。已经用掉的三段换算（schema 17 到 18、工作区身份的归一、旧的私有文件搬迁）与它们的测试、日志阶段和预检里的旧布局计划一并删除；没有存量之后，工作区身份的别名与迁移报告也不再需要。

检查套件里守着它的一项：从 0.34.1 生成的样板看板带着设置与一段已经结束的历史，升级之后与“新建的看板加这些设置”逐表相同，归档与升级前的看板逐字节相同。

## 次序与微任务

每一行是一个微任务，上一行合入并验收之后才开始下一行，标了“可并行”的除外。难的由 Host 指定 buddy，机械的交给路由。

| 次序 | 微任务 | 内容 | 随它做的待办 |
| --- | --- | --- | --- |
| 1 | 升级与基点 | 上一节的全部：归档、新看板、带设置、前提、删掉用掉的三段换算、从 0.34.1 重新生成样板看板 | 升级交接的测试里没有使用的子进程收集 |
| 1′（可并行） | 名字清单 | 只读地列出每一个带旧词的名字与按下一节的规则得出的新名字，交 Host 核定后成为后面各行的依据 | 无 |
| 2 | 安装这一块的收尾 | 起服务时把安装用的虚拟环境从搜索路径里去掉；安装成功后清掉本次的锁文件，清理超过保留期、没有被锁持有的未完成目录与孤立的锁文件；删掉升级协调器里旧布局一代的入口表 | 装上的服务的搜索路径；运行时目录与控制目录的残留；旧布局一代的入口表；补一条测试，守住归档发布之后现场的历史文件或看板被改时升级拒绝、不换看板也不删历史 |
| 3 | 删 Host 维护与评价卡片 | 删掉 Host 维护评价的操作、评价卡片与它们的表、读法、帮助与控制台里的显示；路由的输入不再带卡片 | 无 |
| 4 | 删版本号的闸门与租约 | 删掉评价表与设置的版本号作为闸门的用法、读者与写者的租约、`stale`，以及因为“表变了”“设置变了”“读者未结束”“写者未结束”而停下的路；改成答案发布时检查选中的 buddy 仍是合法候选，不是就重新路由一次，再不是就停到 Host 的边界；设置的修改后写的生效。读清每个微任务上“配置验证的版本号”那一对，把能不能换成认领时校验写进说明，由 Host 定 | ZCode 运行模块里没有使用的 `import re`；控制台的状态码表里不会再出现的 `WRITER_NOT_ACTIVE` 与 `TABLE_BUSY` |
| 5 | 改名：宏任务一族 | `objective` 一族的表、列、字段、命令、事件与前端的名字 | 无 |
| 6 | 改名：buddy 一族 | 指 buddy 的 `profile` 与 `configuration`，连同事件名与排队原因 | `suggestedProfileId` 的两种形态留给第 3 步 |
| 6′ | 改名：harness | 指 buddy 的第一段、指一个 harness 的 `adapter` 改称 `harness`：提交与继续的参数、回应里 buddy 的各段、按 buddy、模型或 harness 记的表的列、错误码与 `adapters` 命令；指连接件的 `adapter` 不动（见“名字的规则”） | 无 |
| 7 | 改名：模型服务一族 | `family` 一族的表、字段与前端的名字改称模型服务（`modelService`、`model_service`）；按它记的并发上限 `model_concurrency` 与 `modelConcurrency` 同样改名 | 无 |
| 8 | 微任务的终态与三处顺带 | 行为改动，不改名。终态 `concluded`：`conclude` 把微任务的状态改成它，失败或取消之后被结束的也归到它；它与 `accepted` 一样是终态，之后不能再继续、验收或另写结论，于是不再判断“结论是否当前”；`list` 的待验收筛选、宏任务的待验收计数与控制台的那一处判断只看状态 | `get` 看不出摘要被截断；被打回过的微任务失败之后写不了结论（打回与验收分开记） |
| 8′ | 改名：微任务一族 | 指微任务的 `goal`、名词的 `delegation` 与 `governed` | 无 |
| 9 | `meta` 里有结构的记录搬进表 | Router 的设置成为表；按 harness 的目录读取时间、账户状态与下次扫描时间成为目录当前行的属性；其余按 L18 的清点逐项搬，或在已经没有读者时删掉 | 升级之后不在目录里的 buddy 的不可用原因 |
| 9′ | 删掉模型服务一层的偏好 | 开发者 2026-10-13 定：偏好是对 buddy 的偏好。删掉 `family_preferences` 表、把两层合成一层的视图与“来源”、只为盖过上一层而存在的取值 `none`、接口与控制台里那一层；日常看板上这一层是 0 条，buddy 一层 5 条 | `harness_set` 与 `console_access_set` 去掉 `expectedRevision`（设置后写的生效）；上一行留下的两个永远为空的字段（任务关系里的 `project_identity_reason`、冲突里的 `identityReason`）与同值的 `recorded_project_id` 一并删掉 |
| 10 | 名字：产品的名字与环境变量 | skill 的目录、启动器与安装位置叫 `hey-my-buddy`，升级时移走旧的；命令在帮助与提示里的名字；其余以 `buddy` 指产品的名字；全部 `BUDDY_` 变量改为 `HMB_` | 无 |
| 11 | 两侧互相引用清零 | 前面删改之后还剩的跨两侧的引用，搬到 `protocol/` 或根包 | 两侧互相引用的登记表 |
| 12（Host） | 文档与 skill | L16：SKILL.md、Host 指南、参考文档、两份 README 与 `AGENTS.md` 按新名字改写 | 文档里的两处旧说法 |

第 5 到 8 行每一行都连同控制台前端的源码按同一张清单改，并重建打包的前端资产；前端只改名，不改样子。

第 11 行先出了一份[搬法方案](adr021-l14-cross-imports-plan.md)（2026-10-14）：现在是 50 处跨侧的引用，其中 29 处是两侧都要用的数据、函数与格式，分 6 批搬到 `protocol/` 或根包就能消除；另外 21 处是一侧在调用另一侧的行为（服务读 harness 的声明与发现、做原生的健康与账户探测，Worker 一侧调服务的工作区操作），搬文件消除不了，要先定这些行为归谁。所以这一行做不到“清零”，能做到的是 29 处加一条守住其余 21 处的卫生检查。它不改存储的形态，也不改 Host 一侧的接口；放在这次安装之前还是之后、21 处要不要另起一步，待开发者定。

第 8 行原来把终态与改名放在一个微任务里，整合 Host 于 2026-10-13 拆成两个：行为改动先做，免得淹在两千处改名里没法逐处核对。“终态”的意思是写了结论之后不能再继续这个微任务，要接着做就另交一个；这是整合 Host 对开发者那句话的读法，已告诉开发者。

## 名字的规则

[清单](adr021-l14-names.tsv)由第 1′ 行给出，Host 已核定，共 367 行，没有待定的。它是按 `c87fc4a8` 的树列的：先删的那两行会让其中一部分名字不再存在，各次改名以当时的树为准。规则如下。

| 旧词 | 意思 | 新词 | 几个要紧的名字 |
| --- | --- | --- | --- |
| `objective` | 宏任务 | macro task | `objectives` 表成为 `macro_tasks`；`objectiveId`、`objectiveOf` 成为 `macroTaskId`、`macroTaskOf`；`objective-list`、`objective-timeline`、`objective-stop` 成为 `macro-task-list`、`macro-task-timeline`、`macro-task-stop`；标识的前缀由 `obj-` 改为 `macro-` |
| `objective` | 交给 Worker 的任务文字 | task | 回合输入里的 `objective`、`objectiveTruncated` 成为 `task`、`taskTruncated` |
| `profile`、`configuration` | 一个 buddy | buddy | `profileId` 成为 `buddyId`；`evaluation_profiles` 表成为 `buddies`；`model-profiles` 成为 `buddy-list`；`routerProfileIds` 成为 `routerBuddyIds`；`executionConfiguration` 与继续时的 `configuration` 成为 `buddy`；`configurationLocked` 成为 `buddyLocked`；事件 `workflow.configuration_selected` 一类成为 `workflow.buddy_selected` |
| `adapter` | buddy 的第一段，一个 harness | harness | 提交与继续时的 `adapter` 成为 `harness`；buddy、模型、目录、健康与额度各表的 `adapter` 列成为 `harness`；`adapters` 命令成为 `harnesses`；`ADAPTER_UNAVAILABLE` 成为 `HARNESS_UNAVAILABLE` |
| `family` | 一个模型服务 | model service | `family_annotations` 表成为 `model_service_notes`；`familyAnnotations` 成为 `modelServiceNotes`；装三个字段的对象 `family` 成为 `modelService`；`model_concurrency`、`modelConcurrency` 成为 `model_service_concurrency`、`modelServiceConcurrency`；`family_preferences` 那一层已经删掉 |
| `goal`、名词的 `delegation`、`governed` | 微任务 | micro task | `goal` 对象成为 `microTask`，任务种类 `goal` 成为 `micro-task`；视图里布尔的 `governed` 删掉，由任务种类表达；`delegation` 这个对象（种类、项目、来源与当前的 Host、上级与根）成为 `lineage`（开发者 2026-10-13 定）；`runId` 等标识字段的名字不变 |
| `BUDDY_` | 环境变量的前缀 | `HMB_` | 全部变量，例如 `HMB_STATE_DIR` |
| `buddy`（指产品的地方） | 产品 | `hey-my-buddy` | `skills/hey-my-buddy`、`scripts/hey-my-buddy`；C-Two 的资源名、控制台的会话 cookie 与 CSRF 请求头、Git 引用的命名空间、会话归属的取值与标识的前缀同样改；给 Worker 用的三个工具成为 `hey_my_buddy_finish_turn`、`hey_my_buddy_checkpoint`、`hey_my_buddy_answer_inquiry` |

模型服务这一级的记录用三个字段 `harness`、`provider`、`model` 标识；装这三个字段的对象叫 `modelService`。

`adapter` 有两个意思，只改一个（开发者于 2026-10-13 定：buddy 的第一段就是 harness，术语表与通行的说法都是这样）。指一个 harness 的改称 `harness`。指连接件的不动：术语表里适配器 / adapter 是一个 harness 以某个角色接入黑板的连接件，命令任务与外部 Worker 也各有一个连接件而它们不是 harness；所以任务、执行与 Worker 运行时上记“由哪个连接件执行”的那一列，以及 `execution-submit` 这条只给命令任务与外部 Worker 用的路上的 `adapter` 参数，仍叫 `adapter`。微任务对 Host 显示的是它的 buddy，不显示连接件。两个意思的划分是整合 Host 的建议，逐个名字的归属由名字清单列出。

不改的：别的意思的 `configuration`（Router 的设置、用户的 harness 配置）；别人的词（Windows 的 `USERPROFILE`、Codex 的 `activePermissionProfile`、React 的 `Profiler`、样式里的 `font-family`）；术语表里的模型画像 / model profile；指一个参与者的 buddy；中性的工程用词 `workflow`、`run`、`task`、`attempt` 与包里的 `buddy/` 目录。

## 验收

- 第 1 行动的是数据与升级：Host 在日常看板的一份私有副本上、空的主目录里经启动器跑一次升级，核对归档逐字节等于升级前的看板、新看板里只有设置并且逐项等于日常看板上的设置；对前提与失败各做几处故障注入（还有开着的微任务、还有受管检出、归档校验不过、新服务起不来）。差异大到读不完时，在验收开始就并行请一次互不通气的第二审查。
- 第 3、4 行改的是路由：读完差异，跑路由与评价的测试，再在私有服务上走一次没有模型调用的路由。
- 第 5 到 10 行是按清单改名：核对旧词在源码、测试与前端里只剩清单列明不改的那些，帮助里的命令名与清单逐项相同，跑受影响的测试；改到控制台的，在最终源码上跑前端全量并看像素。
- 每一行在草稿检出里合入之后、验收之前跑完整检查：这一步的每一行都在删改共用的东西，一行就是一批。原先只打算在第 4、8、11 行之后各跑一次；第 1 行就这样漏掉了两个用到被删夹具助手的测试文件，到第 3 行才被发现。改到控制台的行另跑前端全量。
- 全部做完之后的安装另行授权。装之前日常看板上所有微任务都要已经结束、检出都已回收，包括做这一步的那些。

## 没有纳入的

- `workflow_runs` 一类表名里的 `workflow`、`run` 不改，理由见上。
- 权限档位并成各角色共用的一套（[ADR-023](../decisions/023-harness-integration-principles.md)）与 Router 的几个面，是下一步。
- 维护面的统计从这次升级之后的微任务算起；需要时从归档里另写一次性的换算把结果事实带回来。
