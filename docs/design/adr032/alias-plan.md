# 0.68.0 执行计划：buddy 的别名，目录按模型服务存

2026-11-10，Host 起草。装为 0.68.0：Host 面变了（`buddy` 认别名，回复里写“四元组 [别名]”），存储形式也变了（schema 39→40），所以要黑板空闲才能装，装前做备份与演练。同一天开发者定：目录的存储一并规范化，并进这一版，一步到位。

## 依据

- [ADR-027](../../decisions/027-model-catalog-trust.md) 第 7 条（2026-11-10）：目录按模型服务存一次，可用与否在读时由三层合成，读取的整份结果不再存。

- [ADR-032](../../decisions/032-host-interface-delegation-loop.md) 第 12 条（2026-11-10）：
  - 别名由用户在控制台的模型抽屉里设，Host 不设；写法的限制；
  - Host 可以写四元组、别名或“四元组 [别名]”，看到的名字是“四元组 [别名]”；
  - 记录存 buddy，不存别名；Router 不看别名；
  - 同一次升级删掉 `buddies.label`，搜索改为匹配别名、模型 id 与四元组，封存的路由输入不改。
- [ADR-029](../../decisions/029-console-frontend-foundation.md) 第 8、13 条（2026-11-10）：控制台用小圆角的别名标签，别名色只表示别名，buddy 的生成色避开它。
- [AGENTS.md](../../../AGENTS.md) 的存储规矩：
  - 改了存储形式，同一次升级把碰到的表里重复的、能推导的值清掉；
  - 每一步自带定义，不引用现在的代码；
  - 夹具板要有被转换的存量。
- [待办](../backlog.md)“buddy 的别名，并删掉现在的 label”。

## 现状（Host 读代码与日常黑板得到的，2026-11-10）

- **label 的写与读：** `buddies.label TEXT NOT NULL`（`blackboard/store/db.py`）只由目录刷新写：`catalog/catalog_store.py` 的 upsert，值来自 `catalog/catalog.py` 的 `proposed_buddies()`，写法是“显示名 · 强度”。读它的有：
  - Router 的候选：`routing/decision.py` 的 `_buddy_input`，原样进 Router 的提示；
  - 控制台的视图：`evaluation/evaluation.py` 的 `_buddy_view`；
  - `buddy_list` 的搜索：`catalog_store.py`；
  - 目录刷新的回复：`proposed_buddies()`。
  - 另有十几个测试文件与控制台的预览探针（`tests/probes/macro_task_console_preview.py`）在插入时写它。
- **拼出来的描述：** harness 没给描述时，`proposed_buddies()` 把 `buddies.description` 拼成“显示名 via 提供方显示名”。日常黑板 157 个 buddy 里：
  - 36 个是这样拼出来的，能用记下的目录载荷逐条证明；
  - 109 个是 harness 给的描述；
  - 12 个的模型已不在任何目录载荷里。
- **`named_buddy`（`tasks/workflow.py`）：** 只认 `route` 与按 ` / ` 拆开的四元组，四列精确匹配；不认的报 `INVALID_ARGUMENT`。`route` 的比较到处都区分大小写。
- **续做的记录：** `continue` 把 Host 写的 `buddy` 原样存进 `workflow_continuations.buddy_selection_json`，重放时按原文比较。
- **Host 看到的名字：**
  - `get {"buddies":true}` 用 `schemas.buddy_name`；
  - 其余回复给出四个字段的身份，由 `cli/cli_views.py` 的 `_named_buddies` 写成四元组；
  - 例外是 `routing.selectedBuddy` 与 `routingHistory[].selectedBuddy`：它们是封存的整个 Router 候选（带 `label`、`buddyId` 与统计），没写成名字。控制台的任务详情也读它们（`apps/console/src/task-detail/parse.ts`）。
- **用户设置：** 控制台经 `user_policy_publish` 写用户设置，其中 `buddySettings` 现在只认 `{buddyId, enabled}`。目录刷新的 upsert 不碰 `enabled`。
- **控制台：**
  - 还读 `label` 的只剩搜索文本（`buddy-display.ts`）；
  - `task-detail/RoutingPane.tsx` 把 `preferredBuddyId` 按冒号拆成四元组，遇到被清洗过或带散列的 buddy id 就拆错。
- **升级链：**
  - 每一步是 `blackboard/store/schema_<from>_<to>.py`，登记在 `store/upgrade.py` 的 `STEPS`；新板按 `db.py` 的 `SCHEMA` 建。
  - `test_upgrade_chain.py` 拿升级后的夹具板与新板逐字比对表定义。
  - 是否引用了现在的代码，只查了 32_33、34_35、35_36 三步。
- **目录的存储：**
  - 每次读取的整份结果存在 `evaluation_catalog`，每一次读取记在 `catalog_observations`，都从不删除：日常黑板三周积下 23 份结果、440 次读取，`catalog_current` 给每个 harness 指着当前的一份。
  - 刷新时，把模型的描述、上下文窗口、能力（由输入模态与强度算出）、可用与否及原因，抄进这个模型每个强度的 buddy 行：日常黑板 36 个模型服务、157 行，平均每个模型 4.4 行；同一模型各行在这些列上逐条一致，能力都正好是 harness、强度与输入模态。
  - 可用与否分三层，却都写在 buddy 行上：
    - harness 检测不过时，`service/harness_health.py` 把这个 harness 的 buddy 行改成不可用，恢复时再由 `restore_retained_availability` 改回；
    - 模型的不可用原因来自读取结果，模型消失先记进 `catalog_pending`，确认之后各行写 `CONFIRMED_ABSENCE_REASON`；
    - 模型还在、某个强度不在了，只有那一行写 `RETIRED_EFFORT_REASON`。
    - 代码注释里记着这样做出过的错：旧的检测代码覆盖过“强度已停用”的原因。
  - 读取记录除了排先后，只在重发同一个请求 ID 时被读到；控制台每次点“检测”都生成新的 ID，内部的读取也是。
  - 读整份结果的地方：`catalog_store.current()` 与 `record()`（含上一次采信的读取列出了哪些模型）、`catalog.py` 的 `identities_from_payload` 与 `validate_buddy` 所用的目录视图、`harness_health.py` 读 DSH 的提供方、重放一次读取请求时重建回复。强度的先后（`effort_ranks`）也来自结果里的顺序。
  - 提供方的显示名只用于拼默认描述；模型的显示名只用于 label 与默认描述。
  - 用户设的并发上限在 `model_service_concurrency`（日常黑板 12 行，未设的按默认 2）。
- **其他事实：**
  - 安装只在契约变时迁移存储，所以 schema 一变就要升中间版本。
  - SQLite 的 `NOCASE` 只折叠 ASCII，而别名可以用中文等非 ASCII 文字。
  - `skills/hey-my-buddy/SKILL.md` 正好 4,096 字节，在预算上限。

## 微任务 1：服务、存储与 Host 面

1. **升级步骤 39→40**（`blackboard/store/schema_39_40.py`，自带定义与常量）：
   - `buddies` 删去 `label`，加上可空的 `alias`，空就是没设；
   - 清掉能证明是拼出来的描述：harness 没给描述，而存下的描述正是某个记下的目录载荷里那个模型的“名字 via 提供方显示名”的，改为空串；
   - 模型已不在任何载荷里的保持原样：不知道就不动，而且它是那段文字留下的唯一记录；
   - 写目标版本 40，`db.py` 的当前形式同步；
   - 夹具板要有这两种存量：带 label 的行，拼出来的描述。生成器缺的就补上，派生方式记在夹具旁边。
   - `test_upgrade_chain.py` 不引用现在代码的检查扩到所有步骤。
2. **目录发现：** `proposed_buddies()` 不再产出 `label`，harness 没给描述就是空串，刷新回复随之不再带 `label`。upsert 不碰 `alias`，与 `enabled` 一样。
3. **别名的写入：** 经 `user_policy_publish` 的 `buddySettings` 写入。每一项是 `buddyId`，再加 `enabled`、`alias` 中的至少一个；`alias` 为 `null` 即删去。校验只在这一处边界上做一次：
   - 去掉首尾空白后非空，至多 64 个字符；
   - 不含 ` / `、`[`、`]` 与控制字符；不分大小写不等于 `route`；
   - 整次发布应用之后，所有别名按 Unicode casefold 两两不同，所以两个 buddy 互换别名可以一次发布完成；
   - 拒绝时给出控制台能对上文字的原因：名字已被哪个 buddy 用了、写法不合、是保留字。新原因码登记进 `protocol/reason-codes.json`。
   - 不加数据库层的唯一约束，也不另存折叠后的别名：唯一性只由这一处写入保证。
4. **Host 写 `buddy`：** `named_buddy` 认三种写法，其余照旧报 `INVALID_ARGUMENT`：
   - 四元组；
   - 别名，按 casefold 比，不分大小写；
   - “四元组 [别名]”：取最后一个 ` [` 与结尾的 `]` 之间为别名，两半须指同一个 buddy，对不上就拒绝，并说明各指向哪个 buddy。
   - help 的说明（`protocol/requests.py` 的 `BUDDY_NAME_DESCRIPTION` 与 `get` 的规则）和 Lead 的角色文字（`buddy/roles/texts/lead.txt`）同步。
5. **记录里不留别名：**
   - `continue` 存进 `buddy_selection_json` 的是解析之后的四元组或 `route`；
   - 重放时按解析之后的 buddy 比较，所以同一次续做换一种写法重发仍认得；
   - 现存的行本来就是四元组或 `route`，形式不变，不需转换。
6. **Host 看到的名字：** 回复里凡指名 buddy 的地方，设了别名就写成“四元组 [别名]”：
   - `get {"buddies":true}` 的 `name`；
   - 运行视图、提交回复与回合视图里的 `buddy`；
   - `routing.selectedBuddy` 与 `routingHistory[].selectedBuddy` 改成那个 buddy 的身份加上当前的别名，不再原样给出封存的整个 Router 候选；候选本身照旧留在记录里；
   - 名字只在一处写成文字（`schemas.buddy_name` 与 `cli_views`），服务给出的身份带上当前的别名；
   - 控制台的任务详情读 `selectedBuddy` 的解析与类型在这个微任务里随之改好，界面不变。
7. **搜索：** `buddy_list` 的查询匹配别名、模型 id 与四元组的各段，不再匹配 label。别名按 casefold 比较。
8. **Router 的输入：** 候选里不再有 `label`，描述用上面的新值。封存的 `router_requests`、`decision_requests` 照旧不动。
9. **不改的：** 版本号、`CONTRACT_VERSION` 与 `contract_signatures.json` 由 Host 在版本提交里一起改。

## 微任务 2：目录按模型服务存

建在微任务 1 封存的输出上，扩展同一个升级步骤 39→40（它还没在真实黑板上跑过）。

1. **存储形式：**
   - 新表 `model_services`，主键 harness、provider、model。存模型级的事实：
     - 描述（没有就是空串）、上下文窗口、输入模态；
     - harness 给的模型级不可用原因与内容；
     - 第 2 条的缺席：待确认的起始时间、确认的时间；
     - 用户设的并发上限，空即默认；
     - 这些事实来自哪一次读取。
   - `buddies` 只留 `buddy_id`、四元组、强度在 harness 列出的顺序、这个强度是否已停用、启用与别名，四元组的前三段引用 `model_services`。
   - `catalog_current` 每个 harness 一行，记着当前的读取：状态、原因、读取时间与账户状态；同时进行的读取靠这一行里递增的序号判断先后。
   - 删去 `evaluation_catalog`、`catalog_observations`、`catalog_pending`、`model_service_concurrency`，以及 `buddies` 里抄来的列（可用与否、能力、上下文窗口、描述、来源、不可用原因与内容）。旧的整份结果与读取记录在升级里一次删光，之后不再写，所以没有要定期清理的东西。
   - 读取不再按请求留记录：`model_catalog_refresh` 不再接受 `requestId`（控制台每次点都生成新的，内部的读取也是），重发就是再读一次；回复照当前的目录给出。
2. **可用与否在读时合成：**
   - harness 的健康来自 `harness_health`，模型服务的状态与强度是否停用来自上面两张表；
   - 一处合成，所有读者都用它：路由边界、提交与续做的校验、Router 的候选、控制台与 Host 的视图、容量；
   - 能力由 harness、强度与输入模态算出，不再存。
3. **写入只剩采信的读取与用户：**
   - 采信的读取改写模型服务与强度的行；
   - 健康的变化只写 `harness_health`；
   - `restore_retained_availability` 以及为“覆盖过的原因”所做的修补随之删去。
4. **行为不变：**
   - 哪些 buddy 可用、不可用的原因及其先后，以及待确认、确认、恢复、停用这几种转变与它们的事件，都与现在相同；
   - 钉着这些的测试照旧要过，只有直接查旧表、旧列的断言改写。
5. **升级步骤里的转换：**
   - 模型级的事实从这个模型的 buddy 行取。同一模型各行不一致时，取当前读取里列出的那一组；当前读取里没有的，取最后写入的那一行。
   - 强度的先后取自当前读取的顺序；已不在其中的强度排在后面。
   - 停用的强度来自 `RETIRED_EFFORT_REASON`；缺席来自 `catalog_pending` 与 `CONFIRMED_ABSENCE_REASON`。
   - 健康写进去的原因丢弃，因为它在读时合成。
   - 并发上限搬进模型服务。
   - 来源指向的读取已不在的，记为未知。
   - 夹具板要有每一种存量：各种不可用原因、待确认、停用的强度、设过的并发上限、多次读取。缺的就扩生成器。

## 微任务 3：控制台

建在微任务 2 封存的输出上。

1. **设别名：** 模型抽屉里每个推理强度的设置，在“偏好”与“理由”之间加“别名”一行，是一个输入框。
   - 改了进草稿，随“保存”发布，清空即删去。
   - 服务拒绝时，在这一行下面写出原因。
2. **别名标签 `AliasTag`：** 跟在四元组或模型名后面，小圆角（4 像素）、20 像素高、12 像素字。
   - 颜色是新的 token：浅色下字与边框 `#104aee`、底 `#dee6fd`；深色下字与边框 `#5593e6`、底 `#1f2837`。
   - 出现在每个指名 buddy 的地方：Router 列表、目录里推理强度的那一行、模型抽屉、时间轴与任务详情里的 buddy 名字、路由说明。
3. **搜索：** 控制台的搜索文本不再读 `label`，改为别名、模型 id 与四元组。
4. **生成色避开别名色：** `timeline/buddyColors.ts` 把两种主题的别名色放进与状态色同样的保留表，距离至少 25。
5. **`task-detail/RoutingPane.tsx`：** 不再把 `preferredBuddyId` 按冒号拆成四元组，改用 buddy 记录的四个字段。
6. **类型与样例：** `types.ts` 的 `Buddy` 去掉 `label`、加上 `alias`，样例数据与合成预览跟着改。
7. **界面文字**由 Host 给出，走 i18n：
   - 这里只加抽屉里的字段名“别名 / Alias”。
   - 拒绝的原因在微任务 1 已经加好：
     - `alias.invalid`：“别名{{violation}}。”，`violation` 有四种说法：必须是文字、不能为空、最多 64 个字符、不能含“ / ”、方括号或控制字符；
     - `alias.reserved`：“别名不能是 route。”；
     - `alias.conflict`：“这个别名已被 {{conflictingBuddy}} 使用。”；
     - 英文各有对应。

## Host 做的

- **skill：** `SKILL.md` 里 `buddy` 的那一句加上可以写别名。它已在 4,096 字节的预算上，同一次编辑里删掉等长的字；同时跑 `test_skill_workflow.py`。
- **文档：** Host 面、目录与控制台的参考文档；`CONTEXT.md` 的“别名”词条去掉“not built yet”；待办里这一条删去。
- **版本 0.68.0：** 改版本号、`CONTRACT_VERSION` 与 `contract_signatures.json`，然后演练、安装。安装要黑板空闲。

## 验收

- **存储与升级：** 动到了，按高风险验收。
  - 在导出的真实样本上跑升级，再像用户那样装一次：空 HOME、经启动器、私有服务。
  - 故障注入：升级中途失败时，黑板留在 39；
  - 只读地在日常黑板上干跑清描述的判定，核对行数（量出来是 36 行）；
  - 别名的唯一性：一次发布里互换、重名、只差大小写的重名。
- **可用与否的等价：** 在导出的样本上，用 0.67.9 与候选各算一遍每个 buddy 可不可用及原因，逐个比对。
  - 故障注入：harness 检测不过再恢复、模型待确认再确认、模型重新出现、强度停用、两次读取交错完成，候选与基线逐项对照。
  - 微任务 2 删掉了恢复路径并改了删除的路径，另请一位审查者独立审一次它的差异，发现只算线索。
- **Host 面：** 在私有服务上分别用别名、“四元组 [别名]”与对不上的组合 `submit`；`continue` 换一种写法重放。
- **控制台：** 前端全量、浏览器全量，真实数据回放的像素（深浅两色、中英文）。

## 不做的

- 不加数据库层的唯一约束，不另存折叠后的别名。
- Router 不看别名，记录不存别名，封存的路由输入不改。
- 不按读取的时间推断缺席已确认：确认照旧只在又一次采信的读取里发生。
