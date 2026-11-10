# 0.68.0 执行计划：buddy 的别名，删掉 label

2026-11-10，Host 起草。装为 0.68.0：Host 面变了（`buddy` 认别名，回复里写“四元组 [别名]”），存储形式也变了（schema 39→40），所以要黑板空闲才能装，装前做备份与演练。

## 依据

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

## 微任务 2：控制台

建在微任务 1 封存的输出上。

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
   - 别名 / Alias；
   - 这个别名已被 {name} 使用 / This alias is already used by {name}；
   - 别名不能含“ / ”、方括号或控制字符 / An alias cannot contain “ / ”, square brackets or control characters；
   - 别名不能是 route / An alias cannot be “route”；
   - 别名最多 64 个字符 / An alias has at most 64 characters。

## Host 做的

- **skill：** `SKILL.md` 里 `buddy` 的那一句加上可以写别名。它已在 4,096 字节的预算上，同一次编辑里删掉等长的字；同时跑 `test_skill_workflow.py`。
- **文档：** Host 面与控制台的参考文档；`CONTEXT.md` 的“别名”词条去掉“not built yet”；待办里这一条删去。
- **版本 0.68.0：** 改版本号、`CONTRACT_VERSION` 与 `contract_signatures.json`，然后演练、安装。安装要黑板空闲。

## 验收

- **存储与升级：** 动到了，按高风险验收。
  - 在导出的真实样本上跑升级，再像用户那样装一次：空 HOME、经启动器、私有服务。
  - 故障注入：升级中途失败时，黑板留在 39；
  - 只读地在日常黑板上干跑清描述的判定，核对行数（量出来是 36 行）；
  - 别名的唯一性：一次发布里互换、重名、只差大小写的重名。
- **Host 面：** 在私有服务上分别用别名、“四元组 [别名]”与对不上的组合 `submit`；`continue` 换一种写法重放。
- **控制台：** 前端全量、浏览器全量，真实数据回放的像素（深浅两色、中英文）。

## 不做的

- 不加数据库层的唯一约束，不另存折叠后的别名。
- Router 不看别名，记录不存别名，封存的路由输入不改。
- `buddies` 其余与目录载荷重复的列（harness 给的描述、上下文窗口、能力）不在这一批：对已不在目录里的模型，它们是唯一的记录。要不要改成从载荷读，是另一个设计问题，待开发者定。
