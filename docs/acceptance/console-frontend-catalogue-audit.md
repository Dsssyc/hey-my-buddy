# Buddy 页目录重做之后的核对

2026-10-12，在提交 `a3f465eb` 之上。开发者看过真实页面之后不采信“已完成”，要求逐项核对重做（提交 `10b14c91`）有没有偷懒与遗漏，并改掉指出的交互问题。核对由这一个微任务做：自己读了现在的代码与旧代码（`7a6438f5`），逐项跑了测试；没有引用 `docs/acceptance/console-frontend.md` 里实现方与 Host 写下的对照。

## 怎样核对的，哪些没有核对

读：计划的“设计”“开发者看过真实页面之后改定的”“现有功能的去向”“目录的重做”各节，四份静态稿，`apps/console/src/buddies/` 全部非测试源码，旧的六个组件与它们的测试。旧测试的标题用脚本从 `7a6438f5` 的 74 个测试文件里全部取出，与现在树里的标题逐条比对；标题没变而断言改了的，读了 `git diff 7a6438f5 10b14c91` 里的每一处。

跑：类型检查通过；前端全量 78 个文件、1,141 项通过（交回时的数；开始时是 74 个文件、1,120 项）。把构建输出到临时目录跑过一次，通过，没有写 `src/hey_my_buddy/`；在生成的样式表里读了折叠组件的关键帧、`motion-safe` 与 `motion-reduce` 的媒体查询和选中行悬停的规则，确认它们被生成。变异检查：把额度说明改回冻结层的原文、把节头折角的 `motion-reduce` 去掉，各自被一项测试抓到，之后已还原。

浏览器检查：在这个环境里一直没有执行（Chromium 启动即被沙盒拒绝，`bootstrap_check_in … Permission denied`，macOS 的 Mach 端口；Playwright 1.64 要的 Chromium 1248 也没有装），第三回合又被明令不许跑。第二回合交付之后，Host 在沙盒外跑了一次：88 项中 86 项通过，2 项不过，是 `catalogue-gestures.check.ts` 里 `keeps the drawer's note level with the buddy panel …` 的中文与英文各一项；这 2 项是页面的真问题（备注框与 Buddy 面板的底边差 8px），不是检查写错，已在 `ModelDrawer` 里改（见第四节）。第三回合对页面与检查的改动（折角记号、页签的下划线、备注与面板的底边、`Hint`、工具条两个按钮的说明，以及新补的页签检查）同样没有在浏览器里执行过，只做了源码层面的核对；这些要由 Host 再跑。下面凡写“浏览器检查”的地方，都是这个意思。

要装依赖，`npm ci` 得绕开用户级的 npm 配置（它把代理指向一个被沙盒拦住的本机端口）：用两份空配置文件加 `--userconfig` 与 `--globalconfig`，没有改任何设置。

## 一、功能：计划里 Buddy 页那张表的每一行

“在哪里”是现在的页面上的位置；“测试”写 `文件 › 标题`，同名标题在前面的文件里只写一次。

| 计划里的功能 | 在不在 | 在哪里 | 守着它的测试 |
| --- | --- | --- | --- |
| 搜索 | 在 | 目录工具条的搜索框（`CatalogView`） | `buddy-settings.test.tsx › the harness directory › matches adapter id, harness display name, provider and model name, case-insensitively`；`buddies/models.test.ts › which models are listed › searches the label, the model, the provider, the adapter and the harness's name without regard to case`；`unavailable-configs.test.tsx › unavailable buddies › sends the search text to the request for unavailable buddies` |
| 只看已启用 | 在 | 工具条的复选框 | `buddy-settings.test.tsx › the harness directory › filters to models with an enabled buddy and restores the full list`；`buddies/models.test.ts › which models are listed › keeps the models with an enabled buddy among those shown` |
| 显示不可用（默认隐藏、数目、分页读取、浏览器记住两个筛选） | 在 | 工具条的复选框与数目；分页读取由 `useProfileHistory` 自己接着读 | `unavailable-configs.test.tsx › unavailable buddies › hides them by default and reveals them with the 显示不可用 filter, which says how many there are`、`› combines 只看已启用 with 显示不可用`、`› reads the remaining unavailable pages through the server cursor on its own`、`› counts a listed offline Router once and pages the rest in by itself`；`› the two filters as browser habits`（3 项）；`view-preferences.test.ts`（3 项）；`use-profile-history.test.ts`（10 项）；`buddies/catalogue-view.test.tsx › what the view says about its own reads`（正在读取、读取失败与重试、列表被截断各一项） |
| 折叠没有启用 buddy 的 Harness，写启用数与总数 | 在 | 节头左端的折角；收起的节头写“N 个模型 · 启用 a / b” | `buddy-settings.test.tsx › the harness directory › folds a harness without enabled buddies into one row of counts, and says why a harness cannot work`；`buddies/models.test.ts › the table's blocks › starts a harness folded when none of its buddies is enabled, and when it cannot work at all`、`› counts a harness's models, providers and enabled buddies for its folded row`；本次新增 `buddies/catalogue-gestures.test.tsx` 的 7 项 |
| 不可用的原因，一处说一遍 | 在 | harness 不能工作：节头名字旁的状态，原因在节头那一行的说明里；否则模型名字旁的“不可用”；展开后“模型”一块右端的状态 | 同上的折叠测试（说明的全文，本次从 `toContain` 收紧为 `toBe`）；`buddies/models.test.ts › recorded reasons in words`（2 项）、`› the table's blocks › falls back to plain unavailability for a harness without a health record, and keeps an unknown state as recorded`；`buddies/catalogue-view.test.tsx › the harness sections › keeps an unavailable harness, its state and account when filters hide every model`；`catalog-status.test.tsx › a model the catalogue no longer confirms (ADR-027 §6) › keeps a model unavailable when its harness cannot run it, whatever the catalogue lists` |
| 发现模型，新发现的做标记 | 在 | 工具条的“发现模型”；“新”在模型名字旁 | `edit-mode.test.tsx › direct editing and the save bar › refreshes program directory facts on discovery without uploading a draft`、`› keeps a conflicting draft at its own revision when discovery finds another value`、`› keeps an unsaved note while discovery refreshes the directory`；`buddies/catalogue-view.test.tsx › … › reports a failed discovery as a failure and leaves the catalogue and the button usable`；`console-session.test.tsx › … › pauses discovery and saving once the login lapses (401), without any takeover copy`；`buddies/models.test.ts › the table's blocks › marks a model as new when this page discovered it or the board says so` |
| 更新记录（已发布的版本） | 不在，按计划删除 | 代码里没有它的入口、组件与文案 | 没有测试（删除的行为）；`App.test.tsx › console interactions › offers no maintenance or evidence-submission action anywhere` 守着控制台里没有写入评价的入口 |
| 模型待确认及起始时间 | 在 | 模型名字旁的“待确认”（悬停给起始时间）；展开后“模型”一块右端 | `catalog-status.test.tsx › … › marks the pending model once, with its recorded start as the explanation, and leaves other rows unmarked`、`› explains the one mark of a model with the earliest recorded first-absence time`、`› keeps a pending buddy usable, and writes the state with its time in the drawer`、`› shows a pending model without a recorded time as plain 待确认, never an invented date` |
| 启用开关；已退役的只能停用 | 在 | 展开模型之后 Buddy 面板里的开关；刻度带的格子与收起的行显示状态 | `unavailable-configs.test.tsx › unavailable buddies › allows disabling a retired buddy but never re-enabling it`；`edit-mode.test.tsx › … › toggles a level from its switch and marks it unsaved until publication`；`buddies/catalogue-view.test.tsx › the inline model drawer › chooses the first enabled effort, and selecting another never toggles it` |
| 偏好：默认与单个推理强度的覆盖，带理由，每种偏好有记号 | 在 | 默认偏好在“模型”一块，覆盖与理由在 Buddy 面板；行与刻度带写生效的偏好，是字 | `buddy-settings.test.tsx › a level's own preference`（4 项，含 `writes enabled preferences as words and gives them the same styling`）；`buddies/catalogue-view.test.tsx › a model's default preference`（2 项）；`unavailable-configs.test.tsx › … › allows a reason-only edit of a retired pin while a new pin on an unavailable buddy stays blocked` |
| 各项设置旁的“未保存” | 在 | 设置名旁的橙点；刻度带的格子与收起的行也有 | `buddies/catalogue-view.test.tsx › the inline model drawer › marks edited settings and the effort cell outside field labels and carries marks to a closed row`、`› inherited preference changes › marks changed effective preferences on the effort strip and closed model row`；`edit-mode.test.tsx` 的存档条各项 |
| 并发上限与当前占用 | 在 | 模型行的行尾；展开后“模型”一块 | `model-concurrency.test.tsx › a model's concurrency`（6 项）；`buddies/catalogue-view.test.tsx › the selected model › writes an unknown occupancy as unknown in the model row and drawer, never as 0`；`buddies/models.test.ts › the table's blocks › reads the limit from the draft and the occupancy from what is published, unknown when unreported` |
| 备注 | 在 | 展开后“模型”一块 | `desktop-console.test.tsx › desktop console › keeps one note draft per model across rows and main pages without a lease`；`buddies/catalogue-view.test.tsx › a session that cannot write › shows every setting and changes none, and says why on the switch`（备注只读） |
| 评价（只读）：计入评价的次数 | 在（只有这一个数） | Buddy 面板的“计入评价”，悬停给说明 | `edit-mode.test.tsx › … › shows how many results of each level count towards its evaluation and offers nothing that records evidence`；评价卡片、证据、发布者按计划暂时看不到，旧测试里“发布者没有记录的卡片不能称为自动评价”等三条要由以后的评价分区接过来，现在没有测试，这是计划里记着的 |
| 设为 Router，连同不能设的原因 | 在 | 角色分区 Router 列表的“添加”对话框 | `buddies/roles-view.test.tsx › adding to the list`；`buddies/roles.test.ts › the buddies that can be added`（本次改为理由代码，见下） |
| 当前担任 Router、默认模式、审阅预算、重试间隔、路由健康与失败明细 | 在 | 角色分区的 Router 一块 | `buddies/roles-view.test.tsx › the Router list`、`› what the list says needs the user`、`› the settings of the role`、`› the records of routing`；`buddies/roles.test.ts`（7 组） |
| Harness 的检测结果、试过的位置与处置办法、手动路径、本地资格、单个检测 | 在 | 节头展开的“位置”一块与“指定路径”对话框 | `buddies/catalogue-actions.test.tsx › the location block`（4 项）、`› the path dialog`（6 项）、`› checking › checks one harness through the existing capabilities operation and reloads the snapshot`；`buddies/harness-review.test.tsx`（8 项）；`buddies/harness-wire.test.ts` |
| 全部检测 | 在 | 工具条“全部检测” | `buddies/catalogue-actions.test.tsx › checking`（5 项）；`› a session that cannot write`；`› a disconnected session` |
| Harness 的账户：换来源、独立密钥、OAuth 登录与取消、移除 | 在 | 节头展开的“账户”一块，操作在“管理账户”对话框里 | `buddies/harness-account-dialog.test.tsx`（全部 25 项，密钥不留存、登录链接只给一次、登出与移除要确认等）；`buddies/catalogue-actions.test.tsx › the account block`（5 项） |
| 额度：接近上限的提醒、来源、窗口、重置，过期与未知，额度恢复 | 在 | 摘要在节头那一行里；其余在展开的“额度”一块；提醒在顶栏“Buddy”与分区页签的橙点上 | `buddies/harness-quota.test.tsx`（全部 22 项，含重做时新增的两项按量计费）；`buddies/attention.test.ts`；`buddies/navigation-page.test.tsx › marks for what waits`（3 项） |
| 全局状态条 | 不在，按计划删除 | 提醒移到所属位置（顶栏与分区页签的橙点、节头的状态） | `buddies/navigation-page.test.tsx › marks for what waits › marks the roles and directory sections, and the Buddy tab of the top bar` |
| 直达某个 buddy、harness 与 Router 的地址 | 在 | `#buddies/catalog/buddy|model|harness/<id>`、`#buddies/roles/<id>`；旧的 `models`、`harness` 地址已删 | `buddies/navigation.test.ts`（4 项）；`buddies/navigation-page.test.tsx › deep links`（7 项）；`buddies/catalogue-actions.test.tsx › opening a harness by its address`（3 项）；模型地址的页面一级测试原先缺，本次新增 `› opens a whole model from #buddies/catalog/model/<key> on its first enabled effort, and a click on a row writes that address` |
| 草稿、底部保存条、版本冲突的处理 | 在 | `use-editor` 与顶栏下的提示、页面底部的保存条 | `edit-mode.test.tsx › direct editing and the save bar`（30 项）；`console-session.test.tsx`（两组） |

结论：计划的表有 21 行（上面把第一行拆成了三行写），没有一行缺功能。删除的两行（更新记录、全局状态条）确实删了。没有测试的是两处：评价分区以后要接的三条断言，与更新记录的“不在”（没有可以写的断言）。

## 二、旧断言：旧实现的测试现在由谁守着

旧测试在 `7a6438f5` 的 74 个文件里。按标题比对的结果：除下面列出的 38 个标题之外，其余每一个旧标题在现在的树里都有同名的测试，且全部通过。标题没变而断言改过的，在第二张表里。

### 标题没有了的 38 条

| 旧文件 › 旧标题 | 现在由谁守着 | 这条行为现在还在不在 |
| --- | --- | --- |
| `harness-view.test.tsx › the harness table › shows each harness's recorded state, version and path without calling anything` | `buddies/catalogue-actions.test.tsx › the harness sections › shows recorded harness states and opens no account automatically`；`› the location block › names a found harness's path, source and last check, and offers only the two actions`（版本与路径） | 在。弱了一处：旧测试断言四个 harness 的全部行序，新的只断言第一个节与三个状态；排序本身由 `models.test.ts › puts the harnesses that can work first and writes the state of the one that cannot` 守着 |
| `› marks a found harness with a filled circle and any other with a hollow one, and writes the others lighter` | 没有 | 不在，按新稿：节头不再给 harness 画状态记号，能工作的什么都不写，不能工作的写状态的字；稿与计划“目录”一节都是这样 |
| `› writes the account by its source, and by its state besides when it is not configured` | `buddies/harness.test.ts › a harness summary › writes each recorded harness state and reads its account source and state in details`；`catalogue-actions.test.tsx › the account block › writes the source on its head, then its status, the billing of each provider and the guidance` | 在，但移到了展开的“账户”一块；节头那一行不再写账户来源，这是稿的设计 |
| `› selects a row, tints it and shows its side column; the first row is open before any is chosen` | `catalogue-actions.test.tsx › the harness sections › expands accounts independently without changing model folding`；`catalogue-view.test.tsx › the selected model › opens no model until chosen and restores that drawer after filtering or folding` | 部分在。着色与右栏没有了（没有右栏）；“第一行在选之前就打开”改成了计划里的“没有任何东西选中时什么都不展开” |
| `› lets the side column take the place of the toolbar and the table in a narrow window, and gives them back` | 没有 | 不在：没有右栏，窄屏用同一个结构 |
| `› keeps what an action said outside the parts a narrow window hides` | `catalogue-actions.test.tsx › the harness sections › keeps check feedback below the catalogue toolbar` | 在，断言弱了：旧的断言结果行是视图自己的一行，新的只断言它没有隐藏类并且工具条在 |
| `› draws the side column only while the view is in front` | 原先缺；本次新增 `catalogue-actions.test.tsx › entering the view › draws an account and a model's drawer only while the view is in front, and brings them back with it` | 行为一直在（`CatalogView` 里的 `active`），重做之后没有测试守着，本次补上 |
| `› the toolbar › says when the harnesses were last checked, from the latest of them` | 没有 | 不在：工具条上“上次检测”这一项没有了，`lastChecked` 函数连同它的测试（`harness.test.ts › takes the latest check among the harnesses`）也删了。计划的功能表没有写这一项，是重做时自己去掉的；每个 harness 自己的检测时间还在“位置”一块（`catalogue-actions.test.tsx › the location block`）。没有恢复：同一件事在节里已经各说一遍，工具条再汇总一次是重复 |
| `› writes a dash while no harness was checked` | 原先缺；本次新增 `catalogue-actions.test.tsx › entering the view › writes that a harness was never checked as not recorded, in its location block` | 在（位置一块里“检测”写“未记录”） |
| `› an empty snapshot › has only the toolbar and an empty state, and invents no health` | `catalogue-actions.test.tsx › models without harness observations › retains the model catalogue and invents no harness account or health` | 部分在。“没有 Harness 记录”的空状态与它的两句文案没有了（`harness.empty.*` 是没人用的文案，本次删除，并新增 `i18n/unused.test.ts` 守着）；没有任何 harness 与模型时页面写“还没有模型”并指向“发现模型” |
| `› opening a harness by its address › opens the Codex row focused from #buddies/harness/codex` | `catalogue-actions.test.tsx › opening a harness by its address › opens the selected account and focuses its summary` | 在，地址改写了；旧断言里“本地只读运行资源不可用”的原因字样不再在这条里断言（在 `harness-review.test.tsx` 里） |
| `› … › opens the DSH row with its quota from #buddies/harness/dsh` | 同上 › `opens the addressed harness with its recorded quota` | 在 |
| `› … › leaves the table as it was for an address that names no harness` | 同上 › `keeps every account closed when the address names no harness` | 在 |
| `› entering the view › is reached from the models table, and opening it calls nothing and makes no draft` | `catalogue-actions.test.tsx › entering the view › makes no request of its own and keeps no draft` | 在（只有一个视图，不再有“到达”） |
| `harness.test.ts › takes the latest check among the harnesses` | 没有 | 不在，见上 |
| `› a row of the table › is marked found only for a harness that was found, and writes the version or nothing` | 没有 | 不在，见上（状态记号） |
| `› … › writes the account's source, and its state only when it is not configured` | `harness.test.ts › a harness summary › writes each recorded harness state and reads its account source and state in details` | 在，移到展开的账户一块 |
| `models-view.test.tsx › an empty table › says that there are no models yet and how to find some, and opens no side column` | `catalogue-view.test.tsx › an empty catalogue › says that there are no models yet and how to find some, and opens no drawer` | 在，旧断言里的表头列名（Buddy、Harness、提供方……）随表删除 |
| `› the row the side column follows › opens the first row on screen, and returns to the chosen one when it is on screen again` | `catalogue-view.test.tsx › the selected model › opens no model until chosen and restores that drawer after filtering or folding` | 部分在。“自动打开第一行”按计划去掉了；“筛选或折叠之后回到选中的那一行”在 |
| `› … › writes an unknown occupancy as unknown in the table and in the side column, never as 0` | `catalogue-view.test.tsx › the selected model › writes an unknown occupancy as unknown in the model row and drawer, never as 0` | 在 |
| `› what the view says about its own reads › reports a failed discovery as a failure and leaves the table and the button usable` | `catalogue-view.test.tsx › … › reports a failed discovery as a failure and leaves the catalogue and the button usable` | 在 |
| `navigation-page.test.tsx › … › opens the models table by default and moves between sections and tables` | `› opens one directory by default and moves between directory and roles` | 在，两张表的切换随表删除 |
| `› … › keeps the selected model, the search and the draft across sections and tables` | `› keeps the selected model, the search and the draft across sections` | 在 |
| `› marks for what waits › marks the roles section and the harness table from every view, and the Buddy tab of the top bar` | `› marks the roles and directory sections, and the Buddy tab of the top bar` | 在，“Harness 表”上的提醒记号随表删除，现在在目录分区的页签上 |
| `› deep links › opens and focuses the encoded profile from #buddies/models/<profileId>` | `› opens and focuses the encoded profile from #buddies/catalog/buddy/<profileId>` | 在 |
| `› … › opens the models table for an address that names no view of the page` | `› shows the directory for the bare Buddy page without rewriting an unknown hash`；`buddies/navigation.test.ts › rejects the removed table addresses and unknown routes without redirects` | 在（旧地址不再转向，按 0.x 不留兼容） |
| `navigation.test.ts › writes a view and an encoded target` | `› writes one directory with encoded harness, model and buddy selections` | 在 |
| `› reads back what it wrote` | `› reads directory and roles selections` | 在 |
| `› opens the models for the bare address and for a view the page does not have` | `› rejects the removed table addresses and unknown routes without redirects` | 在，语义按计划改成拒绝 |
| `› gives nothing for another page and keeps the view when a target cannot be decoded` | `› rejects malformed selections and other pages` | 在，语义改成拒绝（无法解码的目标不再保留视图） |
| `roles.test.ts › … › gives the reason routerRefusal gives for each that cannot` | 同文件 › `gives the reason code for each that cannot, and the page's words for a code are the words routerRefusal gives` | 在，本次随英文化改成理由代码，并加了与 `policy.ts` 不许漂移的对照 |
| `› … › names the first buddy and why the default mode refuses it, in the words routerRefusal gives` | 同文件 › `… by the reason code` | 在，同上 |
| `buddy-settings.test.tsx › the models table › draws each buddy as a chip that says whether it is on, and names no role in the catalogue` | `› the harness directory › shows only enabled reasoning efforts in rows and opens a single drawer without naming roles` | 在，行上只写已启用的，关着的在刻度带里 |
| `› a level's own preference › writes every preference as a word on the chip, never as a colour` | `› writes enabled preferences as words and gives them the same styling` | 在 |
| `› … › closes a level's open list when the page leaves the front, without reopening it on return` | `› has no detached preference list to survive leaving and returning to the page` | 行为没有了：偏好不再是下拉浮层，是抽屉里的分段选择 |
| `catalog-status.test.tsx › … › keeps a pending buddy usable, and writes the state with its time in the side column` | 同文件 › `… in the drawer` | 在 |
| `model-concurrency.test.tsx › … › edits one setting shared by all the model's levels` | 同文件 › `… reasoning efforts` | 在，只是改名 |
| `profile-display.test.tsx › the models table's names › names a row by its identity and writes the native level on its chip` | `› the catalogue's names › names a row by its identity and writes the native reasoning effort on its enabled entry` | 在 |

### 标题没变、断言改了的

读完 `git diff 7a6438f5 10b14c91` 里这些测试的每一处，值得写的有以下几类。

| 改动 | 判断 |
| --- | --- |
| 找元素的办法从表格（`closest("tr")`、`harnessCells`）换成分节与行 | 等价 |
| `disabled` 换成 `aria-disabled` 加 `readOnly`（偏好、并发、备注、理由） | 按设计规则；强度相当，`console-session.test.tsx` 与 `model-concurrency.test.tsx` 还各加了一处 `aria-disabled` 的断言 |
| 偏好从下拉列表（`combobox`、`option`）换成分段选择（`radiogroup`、`radio`），“固定需先启用”从选项文字换成 `aria-description` | 等价；并补了点击后仍是未选中的断言 |
| 没有启用任何 buddy 的 harness 不再从列表里隐去 | 按计划（节头永远在）；`buddy-settings.test.tsx` 把“不见了”的断言换成“还在而模型不在” |
| 行上的每个格子不再写“已启用 / 未启用 / 不可用” | 按计划（行上只写已启用的）；逐个格子的状态改由抽屉里的开关与刻度带断言 |
| `harness-quota.test.tsx` 里表格单元格的全文断言改为 `toContain`，额度条的宽度（93%）断言去掉 | 弱了一点，原因是整行是一个按钮，文字里含名字；额度条按稿没有了，改成断言 `text-hand` 的颜色类，这一条仍在；新增了两项按量计费的断言 |
| `harness-review.test.tsx` 里本地资格的原因从 `status` 的文字换成说明（`aria-description`） | 等价 |
| `buddy-settings.test.tsx › folds a harness …` 里说明的断言从 `toBe` 变成 `toContain`；抽屉里“ZCode 未找到 · 本机未检测到 zcode CLI”只断言后半句 | 弱了；说明现在多了额度观测一段，所以用 `toContain`。本次把抽屉里的那一条收紧回全文 `toBe` |
| `App.test.tsx` 里右栏的文本框数从 1 变 2、按钮只剩刻度带的一个 | 随结构变化，等价 |

## 三、规则：计划“设计”一节的每一条，现在的页面有没有违反

先说读法：违反的写出处；“有出入”是页面与规则字面不一致、但按的是开发者确认过的静态稿，要 Host 定；“冻结层”是根因在这次不许改的文件里。

| 规则 | 现在有没有违反 |
| --- | --- |
| 颜色各有一个含义 | 没有违反。悬停用中性的灰（`sunk`、`foreground/5`），选中的行用绿色的淡底；已展开的行悬停时用绿色再加深一级，仍是同一个含义。刻度带选中的格子用墨色的描边而不是绿，这是稿的画法（上一轮按稿改的），与规则里“绿是选中的”字面不一致，有出入 |
| 形状先于文字 | 没有违反。节头不画状态记号，只在不能工作时写状态的字；抽屉“模型”一块右端用实心圆与空心圆 |
| 文字是名称、状态与数值，不是句子 | 有一处：`models.limit`（“只显示前 600 个不可用的 buddy；更早的请用搜索查找。”）常驻在读取被截断时，它不是空状态、出错或确认。没有改，因为去掉“用搜索查找”这句指引会让用户不知道更早的在哪里，要 Host 定。别的句子都在出错、确认与空状态里。冻结层的编辑提示是句子，不在这里算 |
| Buddy 页的目录按 harness 分节 | 没有违反（节头内容、状态排序、默认收起与数目、按钮只写“检测”与“全部检测”都核对了）。冻结层的一点：计划说“额度只在节头出现”，现在节头的摘要加展开的“额度”一块，是计划本身写的 |
| 节里一行是一个模型 | 没有违反。行上只写已启用的推理强度与偏好的字，绿点，没有启用的名字用浅色，行尾并发，“待确认、新、不可用”在名字旁，harness 说过的状态模型不再重复。“界面上不说家族”：页面自己的文案里没有，但冻结层的 `policy.ts`（两条）与 `draft.ts`（三处字段名）会在提示里写“家族”，在抽屉的错误与注意段落、顶部冲突列表里出现，冻结层 |
| 点模型，设置就地展开，不设右栏 | 没有违反。有出入的一处：刻度带的顺序在计划里写“这个 harness 自己的顺序”，现在沿用 `console-data.ts` 里写死的表，计划自己记着这是留给之后的 |
| 窄屏用同一个结构 | 没有违反（浏览器检查里有 390 宽的断言，本次没有执行） |
| harness 的标志用图标包里的彩色原样 | 没有违反（`HarnessLogo`；ZCode 的单色用文字颜色，浏览器检查断言） |
| Buddy 页的分区是并列的几个方面 | 没有违反（`buddy-settings.test.tsx` 守着目录里不写 Router） |
| 用线和留白分区 | 没有违反：容器没有圆角；控件 4px；只有关注环的小目标用 2px（`rounded-sm`，与共用的 `Section` 同写法），不是容器 |
| 版式只有几种 | 有出入：规则写“页面的头部放标题、分区页签与一行状态”，Buddy 页没有可见的标题与状态行，分区页签并进了目录的工具条，页面标题只有读屏可见（`h1.sr-only`）。这是目录重做稿的画法 |
| 舒适：字号四档，控件 32px、小的 28px | 两处。违反一处，已改：`CatalogView` 里读取失败与动作失败的“重试”按钮是 `size="xs"`，高 24px，改为 `size="sm"`（28px）。有出入一处，没有改：抽屉与展开块里小标题（“模型”“Buddy”“位置”“账户”“额度”）是 12px，规则写“面板标题 16px”；稿里这些是小的灰字，浏览器检查也断言 12px，按稿 |
| 看得清（对比度不低于 4.5） | 没有违反：浏览器检查逐处量默认状态下的文字对比度。有一处没有量：这次新增的悬停与加深的底色（灰与加深的绿）下的文字，浏览器检查只量默认状态，要 Host 在像素里看 |
| 没有常驻的说明句；没有问号按钮与 `title`；暂时不能用的控件用 `aria-disabled` 加说明 | 前两项没有违反（源码里没有 `title=`，没有 `Help`）。第三项原先有一处：工具条的“全部检测”与“发现模型”在正在执行时是 `aria-disabled`，没有说明。共用的 `Hint` 改成不换树之后已补上（“检测进行中”“发现进行中”，中英文各一份），见第四节 |
| 组件与文字：浮层等用 `components/ui`，文字全部走文案，中英各一份 | 页面自己的组件里没有写死的中文（`i18n/sources.test.ts` 现在也读顶栏所在的 `App.tsx` 与 `BuddyConfig.tsx`）。冻结层的中文见第五节 |
| 每个页面有浏览器检查 | 有；本次新增 34 项（`catalogue-gestures.check.ts`：每种语言 6 项，另有亮暗两种主题下的选中与悬停各 1 项，四种宽度下每种语言各 2 项，英文下扫中文 2 项），其中第三回合之前的由 Host 跑过，之后的改动见开头 |

### 开发者看过真实页面之后改定的 7 条

| 规则 | 之前 | 现在 |
| --- | --- | --- |
| 展开、收起、选中与悬停都要有过渡 | 违反：没有任何过渡，展开的记号是文字符号，行与格子的颜色直接跳 | 已改，见第四节 |
| 一行只有一种展开的手势，箭头要看得见 | 违反：节头右端有第二个小箭头；记号是 16px 的文字符号 | 已改，见第四节 |
| 选中的样子不能被悬停盖掉 | 违反：已展开的模型行，按钮上的 `hover:bg-sunk` 盖掉容器的选中底色 | 已改 |
| 英文界面里不出现中文，名词用术语表的 | 违反：`App.tsx` 里三句保存不可用的原因与一句“保存结果未知”写死；Router 的拒绝原因（来自 `policy.ts`）与额度观测的说明（来自 `host-workflow.ts`）在英文界面出现中文 | 页面一侧能改的都改了：前者进文案，后两者由页面按代码与状态写自己的文案；冻结层里整句传过来的中文见第五节，仍在 |
| 语言可以在设置里选 | 后做（随设置页） | 没有动 |
| 稿没有画到的宽度也要成样子 | 违反：约 800px 时控制带折行会把一个按钮单独留在第二行 | 已改 |
| 不采信实现方的“已完成” | — | 就是这份记录 |

## 四、按开发者改定的做了什么

过渡：新增 `components/ui/collapsible.tsx`（Radix 的可折叠组件，高度用它给出的 `--radix-collapsible-content-height`，200ms，`motion-safe` 下才有动画；展开的记号 `Disclosure` 用 200ms 的转动）。分节的模型、账户的展开、模型的抽屉都用它；没有任何自己量高度的代码（`motion.test.ts` 里有一项读源码断言没有 `getBoundingClientRect`、`scrollHeight`、`ResizeObserver`）。颜色的过渡 150ms：节头与模型行与刻度带的格子、刻度带的尖角（改成每格都有，用不透明度的过渡）、开关、复选框、输入框、分段选择、按钮、顶栏与分区的链接、添加 Router 的列表项都加了 `transition-colors duration-150 motion-reduce:transition-none`（或对应的属性）；`motion.test.ts` 读所有类名列表，凡是悬停或状态会改颜色的，都要有过渡且在 `motion-reduce` 下去掉。共用的 `ui` 组件改了按钮、复选框、输入框、滚动区、选择、开关与分段选择这几处，别的页面也跟着有了同样的过渡。

节头：整行是一个按钮（`HarnessHeader`，名称“X 账户”，`aria-expanded` 管位置、账户与额度）；左端的折角是另一个按钮（名称“展开 / 收起 X”，`aria-expanded` 管这一节的模型），32px 见方；折角是 `lucide-react` 的 `ChevronRight`，16px，`strokeWidth` 1.5，次要文字色，收起朝右，展开转 90 度朝下，沿用 200ms 的转动，页面上不再有实心三角与文字符号（`Disclosure`、节头左端的按钮、模型行都是这一个组件）；两个是并列的，不嵌套，右端没有第二个箭头。因为整行成了一个按钮，原来挂在状态词与额度摘要上两个可聚焦的小目标没有了，它们的说明合并在整行按钮的说明里。测试：`catalogue-gestures.test.tsx`（7 项：两个按钮各有名称与状态、互不包含、没有第二个箭头，点行的任何地方与点折角各管各的，键盘 Enter 与 Space，Collapsible 的类，控制带的结构）；浏览器检查里量折角按钮不小于 28px、行铺满头部其余的宽度。

选中不被悬停盖掉：已展开的行悬停时是绿色再加深一级（`aria-expanded:hover:bg-live/15`），不是灰；容器的选中底色与绿线不随悬停变。刻度带选中的格子悬停时描边与尖角都在，别的格子悬停变灰。浏览器检查里读计算样式比较悬停前后。jsdom 里没有样式，只能由浏览器检查断言。

控制带：在 640px 以上，搜索与“其余”分成两个成员，其余成一组（筛选、数目、两个按钮），两个按钮是一对不拆开；放不下时先是搜索独占一行，再是这一组。浏览器检查在 700、800、900、1000px 与中英文下把控制带的各项按行分组，断言没有一个按钮单独在一行，折行时第一行只有搜索。

英文：顶栏里 `App.tsx` 写死的三句保存不可用的原因与一句“保存结果未知”搬进 `shell` 文案；`CatalogView` 与 `use-harness-actions` 里用的 `LOGIN_EXPIRED_ACTION_REFUSAL`（冻结的中文常量）换成 `buddies` 文案的 `loginExpired`；Router 的拒绝原因由 `roles.ts` 给出代码（`fast`、`review`、`unavailable`、`disabled`），页面用 `roles.refusal` 的文案写；额度观测的说明改在 `harness.ts` 里用 `harness.quota.note` 的文案写。后两处的中文原文与冻结层的原文一致，`roles.test.ts` 与 `harness.test.ts` 各有一项与冻结层逐一对照，防止漂移。新增 `buddies/english.test.tsx`（6 项）：在英文下把整个 Buddy 页（各节、各账户块、每一行与每个格子、保存条、两个对话框、写不了的会话、角色与添加 Router 的对话框）的文字与读出来的属性（`aria-label`、`aria-description`、`placeholder`、`title`、`alt`）扫一遍，不许有中文；浏览器检查里有同样的两项扫真实的合成预览。

第三回合改的四处：（1）备注与 Buddy 面板的底边：右列改成纵向弹性布局，面板 `flex-1` 撑到列底，左列的备注本来就在列底，两条底边是同一条线（页面的问题，检查没有放宽，仍是 2px 容差）；（2）折角：见上；（3）页签与下划线：`BuddyHeader` 的页签不再 `self-stretch`，是 32px 高的行，整个分区导航 `self-start`，字与下划线在一起，不随工具条折成两行而分开；`catalogue-gestures.check.ts` 新增一项，在 700、800、900、1000px 与中英文下量当前页签的字的底边与下划线（`::after`）的顶边，不超过 12px（没有执行过）；（4）`Hint`：见第五节。

另：删了没有人用的文案 `harness.empty.*`（两种语言），新增 `i18n/unused.test.ts` 守着“Buddy 文案里没有没人用的键”；补了模型地址的页面测试、视图不在前台的测试、从未检测的“未记录”测试；把抽屉里说明的断言收紧；两个“重试”按钮改成 28px。

## 五、没补上的，与原因

这些都是英文界面里仍然会出现中文，或仍与规则不一致的地方，根因在这次不许改的文件，或要 Host 定。

数据与编辑层的中文消息（冻结）：`use-editor.ts` 设的提示与错误（例如放弃草稿后的“草稿已放弃”，英文下页面顶部的提示里就是中文，`buddies/english.test.tsx` 开发时抓到过这一条），`console-session.ts` 里被编辑器设进提示的几个常量（登录已失效、保存结果未知、连接中断），`draft.ts` 的字段名与冲突说明，`policy.ts` 的阻止与注意消息（出现在模型抽屉顶部的两个段落与顶部的冲突列表里，含“家族”），`api.ts` 的错误消息表与 `errorText` 的兜底句（动作失败时的提示，以及断线时顶栏下那一条红色提示）。每一处都是字符串从冻结文件里整句传过来的，页面一侧只能原样显示，翻译要在这些文件里做（让它们给代码，页面按代码写）。这一批没有改，英文界面里因此仍会在这些情形出现中文：保存或放弃之后的提示、保存冲突、写入被拒绝、断线、动作失败。`buddies/english.test.tsx` 没有触发这些情形，所以它通过不代表它们是英文。

服务一侧记下的字符串：harness 的处置办法、来源、账户说明里服务自己写的中文（例如“版本管理器（nvm 默认版本）”）是黑板的数据，页面按原样显示；已有代码对应的（原因码、账户说明码）页面按文案写。

浏览器检查：见开头。第二回合交付之后 Host 在沙盒外跑的是 88 项中 2 项不过，就是备注与 Buddy 面板底边那一条的中文与英文；这是页面的真问题，不是检查写错，第三回合已改页面、没有放宽检查（见下）。更早那一次的 12 项失败是检查写错，已在第二回合改过（开合在 React 提交之前就读样式，现在点击之后等一帧；Radix 关上之后内容元素仍在但 `hidden`；几何与颜色在动画未止时量，现在用 `settle`；选中刻度格的定位器与英文扫中文的取格都改了）。第三回合的源码由 Host 跑过：类型检查通过；前端全量 79 个文件、1144 项通过；浏览器检查 96 项中 94 项通过、2 项不过（`catalogue-gestures.check.ts › changes every colour on a transition of 100 to 150ms`，中文与英文各一项，等不到抽屉里的 `[data-slot=segmented-item]`）；折角的记号、备注与 Buddy 面板的底边（414 对 414）、700 与 800px 下页签与下划线，像素都对了。第四回合改了 `Hint`（见下），这一回合的修改没有在这个环境里跑过（`npm`、`node` 与浏览器命令都不许执行），仍由 Host 来跑。

`Hint`（共用）原先在有无说明之间会换掉子树：节头那一行按钮的说明在“有”与“没有”之间变化时（例如点“检测”之后），这个按钮会被重建，有焦点时丢焦点。第三回合在根因上改了 `components/hint.tsx`：子元素永远在同一棵树的同一个位置上，没有说明时只是没有浮层、也没有 `aria-description`。`components/hint.test.tsx` 守着：有焦点的按钮，说明从有变无、从无变有，焦点都在，按钮是同一个 DOM 节点。工具条的“全部检测”与“发现模型”在正在执行时加了说明（“检测进行中”“发现进行中”，两种语言的文案各一份），`catalogue-actions.test.tsx › lets no second action start while one is under way` 与 `catalogue-view.test.tsx › says why the discovery button is closed …` 各断言说明出现、按钮仍是同一个、结束后说明消失。第四回合又改了一处根因：`Hint` 原先用 `ui/tooltip` 里的 `TooltipTrigger`，它带着 `data-slot="tooltip-trigger"`，经 `asChild` 合并到子元素的属性上，把共用控件自己先写的 `data-slot` 盖掉（`Hint` 永远包一层之后，抽屉里的 9 个单选、1 个开关、3 个输入框与工具条的两个按钮全都成了 `tooltip-trigger`，浏览器检查按 `segmented-item` 找不到格子，就是上面那 2 项不过的原因）。现在 `Hint` 用 `radix-ui` 的 `Tooltip.Trigger` 原件（不带 `data-slot`），其余不变（`aria-description`、只在键盘带来的焦点上显示）；`hint.test.tsx` 补了一项：把 `Button` 放进 `Hint`，说明有与没有时 `data-slot` 都还是 `button`。一处遗留：`BuddyHeader` 里分区链接的 `ViewLink` 有同样的“有无说明换树”的写法，没有改；它是链接，不是在操作中会被点击而失去焦点的按钮。

`models.limit` 的句子，与抽屉小标题 12px，见第三节，要 Host 定。

没有做的：语言的选择（计划里随设置页）、任务页与设置页的英文。

## 六、Host 在最终源码上的结果

以上各节由做这个微任务的 Worker 写成，Host 改了两处数字：开头的日期，与第三回合浏览器检查的总数（那一次 Host 报给 Worker 的 103 项里有 7 项是 Host 自己拍图用的临时检查，实际是 96 项中 94 项通过）。

第四回合交付之后，Host 在最终源码上跑过：类型检查通过；前端全量 79 个文件、1,145 项通过；浏览器检查 96 项全部通过。Host 另外核对了这份记录本身：第二节“标题没有了的 38 条”用脚本从 `7a6438f5` 重新数过，正好 38 条；第一节引用的 63 个测试标题都在现在的树里。验收的经过与留给后面的事项在 [console-frontend.md](console-frontend.md) 的“目录的交互修改与遗漏核对”一节。
