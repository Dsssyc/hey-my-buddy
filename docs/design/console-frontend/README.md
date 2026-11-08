# 控制台的静态稿

[ADR-029 执行计划](../adr029/console-frontend.md)的设计依据：每个页面一份静态稿，用浏览器直接打开 HTML 文件即可。数据是编的，稿不联网，也不是实现。在 `<html>` 上设 `data-theme="dark"` 看深色，移除该属性或设为 `light` 看浅色。

上一轮任务页、微任务详情、时间轴、Buddy 页与设置页的稿已在实现并验收之后删除（2026-10-17），要看可以到 git 的历史里找。颜色、记号与版式的规则写在 `base.css` 里，含义见执行计划的“设计”一节。页面实现并验收之后，对应的稿删除。

批次三的删除确认、拦截、详情顶部的时间片行与 Lead 泳道四份稿，连同共用的 `batch3.css` 与实际应用的前后对照图，在实现经 Host 验收之后删除（2026-10-18，[验收记录](../../acceptance/adr029/console-batch3-lead.md)）。

## ADR-032：开发者已确认（2026-11-05），待实现

本批是 [ADR-032 执行计划](../adr032/execution-plan.md)第 7 节与 D8 的 P6a 静态稿。开发者于 2026-11-05 看过四轮之后确认，P6b 按这些稿实现。依据 [ADR-029](../../decisions/029-console-frontend-foundation.md)和[设计及带日期的改定](../adr029/console-frontend.md#设计)。没有改动控制台实现，没有调用模型，没有连接服务。这里只核对静态渲染，未做控制台功能验证，也不是 Host 的实现验收记录。

每种画面有独立的中英文 HTML；英文文件在扩展名前加 `.en`。样式由 `base.css` 与本批的 [adr032.css](adr032.css)组成；后者只调整静态稿的排版，颜色全部引用 `base.css`，没有新增色值。主要审阅深色图，浅色图用于同时核对文字、线与浮层。顶栏、项目列表、28px 条目、灰色选中底、详情的头部／步骤带／内容、左侧标签列及折叠记号均沿用现有页面的结构。宏任务目标沿用现有图标与浮层，主截图中收起；另附展开状态。空宏任务保留微任务名称栏与时间轴区域，五页微任务详情都放回宏任务内部，左边名称栏常驻，Lead 的成员沿用其行下方的泳道。Git 交付和验收各画一个时刻，不表示同一时刻处于两种状态。

| 画面 | 中文 HTML | English HTML | 深色（中文 / English） | 浅色（中文 / English） |
| --- | --- | --- | --- | --- |
| 空宏任务与项目分组 | [打开](adr032-macros.html) | [Open](adr032-macros.en.html) | [中文](adr032-macros-dark.png) / [English](adr032-macros.en-dark.png) | [中文](adr032-macros-light.png) / [English](adr032-macros.en-light.png) |
| 文本搜索命中空宏任务 | [打开](adr032-macros-search.html) | [Open](adr032-macros-search.en.html) | [中文](adr032-macros-search-dark.png) / [English](adr032-macros-search.en-dark.png) | [中文](adr032-macros-search-light.png) / [English](adr032-macros-search.en-light.png) |
| 零成员删除确认 | [打开](adr032-delete-empty.html) | [Open](adr032-delete-empty.en.html) | [中文](adr032-delete-empty-dark.png) / [English](adr032-delete-empty.en-dark.png) | [中文](adr032-delete-empty-light.png) / [English](adr032-delete-empty.en-light.png) |
| none / read 固定结果 | [打开](adr032-none-read.html) | [Open](adr032-none-read.en.html) | [中文](adr032-none-read-dark.png) / [English](adr032-none-read.en-dark.png) | [中文](adr032-none-read-light.png) / [English](adr032-none-read.en-light.png) |
| none / write 固定结果与文件夹写入 | [打开](adr032-none-write.html) | [Open](adr032-none-write.en.html) | [中文](adr032-none-write-dark.png) / [English](adr032-none-write.en-dark.png) | [中文](adr032-none-write-light.png) / [English](adr032-none-write.en-light.png) |
| Git 回合交付 | [打开](adr032-git-delivery.html) | [Open](adr032-git-delivery.en.html) | [中文](adr032-git-delivery-dark.png) / [English](adr032-git-delivery.en-dark.png) | [中文](adr032-git-delivery-light.png) / [English](adr032-git-delivery.en-light.png) |
| Worker 提交区间与干净三方核验 | [打开](adr032-git-commits.html) | [Open](adr032-git-commits.en.html) | [中文](adr032-git-commits-dark.png) / [English](adr032-git-commits.en-dark.png) | [中文](adr032-git-commits-light.png) / [English](adr032-git-commits.en-light.png) |
| 固定补丁与有调整的核验 | [打开](adr032-git-patch.html) | [Open](adr032-git-patch.en.html) | [中文](adr032-git-patch-dark.png) / [English](adr032-git-patch.en-dark.png) | [中文](adr032-git-patch-light.png) / [English](adr032-git-patch.en-light.png) |

第 1 页另附目标展开图：[中文深色](adr032-macros-goal-dark.png)、[中文浅色](adr032-macros-goal-light.png)、[English dark](adr032-macros.en-goal-dark.png)、[English light](adr032-macros.en-goal-light.png)。第 1 页另有 800 × 900 的深色窄容器图：[中文](adr032-macros-narrow.png)、[English](adr032-macros.en-narrow.png)。常规截图宽 1360、高至少 1000，使用整页截图。截图都在 HTML 旁边。

none 写入提示另附：[中文深色悬停](adr032-none-write-changes-hover-dark.png)、[中文浅色键盘聚焦](adr032-none-write-changes-focus-light.png)、[English dark hover](adr032-none-write.en-changes-hover-dark.png)、[English light keyboard focus](adr032-none-write.en-changes-focus-light.png)。

## 本轮按开发者反馈修改

none 写入行改为“改动位置 / 原文件夹”，英文“Changes in / the original folder”。悬停与键盘聚焦提示保留“黑板不另存副本 / The blackboard keeps no copy”。

项目种类在宏任务头部的写法，开发者在对比稿的两种做法里选了做法 A：以“仓库 / Repository”或“文件夹 / Folder”为标签，名字前不放记号；列表里的项目组照旧用会开合的文件夹图标。对比稿选定后已删除。

## 此前修订：项目图标与提示

所有项目组恢复当前控制台的 FolderOpen／Folder 图标：展开时打开，收起时合上，Git 项目同样如此。列表不再用 Git 记号区分项目种类。种类只在宏任务头部表示，原来的“项目”改为“仓库 orchard / Repository orchard”或“文件夹 Field notes / Folder Field notes”，名字前不放图标。

none 写入行改为“改动 / 直接在文件夹里”，英文“Changes / directly in the folder”。“黑板不另存副本 / The blackboard keeps no copy”只在该值的悬停与键盘聚焦提示中出现，沿用 0.6 秒延迟、居中和不接收指针的规则，值本身使用普通字色。其余沿用上一轮。

## 此前修订：页面位置

空宏任务改用现有宏任务详情的名称栏与时间轴区域，名称栏显示“没有已提交的微任务 / No submitted micro tasks”，不另画居中的空状态。目标图标和说明沿用现有浮层。移除单独的 `adr032-macros-lead` 中英文页及截图，Lead 成员留在 Lead 行下方的泳道中。宏任务头部删除整个 Host 栏；微任务详情仍按谱系显示 Host 或实际的 Lead 归属。搜索框提示改为“搜索标题、项目或 ID / Search titles, projects or IDs”。

none 写入行改为“改动 / 写在文件夹里，不另存副本”，英文“Changes / in the folder itself, no copy kept”，使用普通字色。none 两页、Git 交付及两页验收均放回宏任务内，保留宏任务列表、宏任务头部、微任务名称栏及常驻时间片／步骤带，再展示内容。none 两页均为 Field notes 项目、Host 归属，结果位置改为与本页 run 和 attempt 编号一致的 `<state>/attempts/<run>/<attempt>/result-receipt.json`。当轮沿用的项目记号由本轮上面的决定替代。

## 画面中的事实

空宏任务是 `macro-archive`，目标可从现有图标打开，成员是 0，条目前的记号位留空，概览没有执行或验收统计。名称栏写“没有已提交的微任务 / No submitted micro tasks”，右边保留空的时间轴区域。列表是 4 个宏任务：文件夹 `Field notes` 中 2 个，Git 仓库 `orchard` 中 2 个。文本搜索画面输入该空宏任务的标题，列表总数为 1，项目计数也为 1。这些都是各自固定的画面，页签和搜索框不运行查询。

| 筛选 | 空宏任务 | 此组示例命中数 | 画面或实现要求 |
| --- | --- | --- | --- |
| 全部 / All | 可见 | 4 | `adr032-macros` |
| 文本搜索 / Text search | 标题匹配时可见 | 1 | `adr032-macros-search` |
| 进行中 / Running | 不命中 | 1 | 只含“校验导入 / Import checks” |
| 等待 Host / Host | 不命中 | 1 | 只含“目录索引 / Directory index” |
| 等待验收 / Acceptance | 不命中 | 1 | 只含“重试处理 / Retry handling” |

后面三种筛选的排除规则列在这里供实施核对，静态页签没有筛选行为；不能据此声称查询、计数、分页或缓存已经验证。空宏任务沿创建／活动时间排序，零成员不等于已结束。删除画面展示零微任务、零产物、零会话、零检出与零引用，删除按钮仍可用；确认文案沿用现有删除的不可恢复与滚动备份说明。按钮没有删除行为。

| 示例宏任务 | 项目 | 使用位置 |
| --- | --- | --- |
| 梳理归档 / Archive survey | Field notes（folder） | `~/Projects/field-notes` |
| 目录索引 / Directory index | Field notes（folder） | `~/Projects/field-notes` |
| 校验导入 / Import checks | orchard（git） | `~/Worktrees/orchard-import` |
| 重试处理 / Retry handling | orchard（git） | `~/Worktrees/orchard-retry` |

两处 Git worktree 在列表里只形成一个 `orchard` 组，组名取仓库名。项目组的文件夹图标只表示展开／收起，所有组相同；项目种类由宏任务头部的“仓库 / Repository”或“文件夹 / Folder”标签表示，名字前不再放图标。宏任务头部整栏去掉 Host，宏任务只能由 Host 建立。微任务归属仍来自谱系：Host 只写 Host，Lead 保留真实归属标识；Git 示例中 Lead 行与其下缩进的成员行在同一个名称栏。没有 Host 名称、Host 编号、来源 Host、当前 Host 列表或凭证字段，也不从 harness 或线程推测它们。

none 两页固定展示 `artifactId`、结果位置和 SHA-256、原生权限档位 `read`／`write`、工作目录及 `checkout: none`，展开“执行”可看 `runId`、`turnId`、`attemptId` 和 `generation`。内容是固定结果的示例正文；结果中的文件名不是封存文件。write 页用普通字色的“改动位置 / 原文件夹”表达文件夹写入这一事实；悬停或键盘聚焦该值才出现“黑板不另存副本”的提示，不用橙色，也不出现空提交、空补丁或零改动统计。结果路径按 `<state>/attempts/<run>/<attempt>/result-receipt.json` 展示，对应本页执行身份里的示例 run 和 attempt。read 不画 writeScope。按本轮指定文案，write 的文件夹写入事实直接放在“改动位置”一行。其余完整句子只出现在空状态、确认及模拟的任务书／结果／Host 说明正文里；模拟正文属于实际内容，不是界面帮助文案。

none 的验收仍按第 6 节检查任务书和确切的固定结果，只允许带理由的 notRequired 与核对 note；这两份稿展示验收前的回合交付，验收步骤的样式沿用现有详情，不引入 target、adjusted、hostPaths 或 beforeCommit 的控件。write 的目录文件本身不因结果被验收而成为封存证据。

Git 页把“封存证据”“整合来源”“整合”“核验结果”分成并列的标签行，避免把快照误读为要合并的提交。封存证据固定显示产物 ID、快照摘要、树与改动路径。整合来源在两个画面中分别是 Worker 自己的起止两个提交，或已经固定的补丁位置与摘要；稿不把起止值拼成可执行的 Git 命令，也不以存在 HEAD 代替来源校验。Git 回合页与验收页使用同一组产物身份和来源。

验收画面显示目标位置及 ref、整合前与整合后提交，再显示核验事实。干净三方合并没有异常路径行；有调整时展示 Host 的说明、`src/import/validate.py` 的冲突路径和产物以外的 `docs/releases.md` 补充路径，各自列出。调整不画失败红，不把 Host 补充路径混进冲突路径。核验方法仍由第 5 节的共同基线和目标树决定；画面中的“已核实”和“已验收”只是编造数据，没有运行 Git 核验。

## 颜色、提示与过渡

颜色取现有词表：`--ink`／`--ink2`／`--ink3` 用于信息层次，`--paper`／`--surface`／`--sunk` 与线色用于结构，`--past` 用于历史记号，`--past-wash` 用于中性的选中底，`--hand`／`--hand-mark` 用于需要接手的事实。实心橙菱形是等 Host，空心橙菱形是等验收；write 的文件夹写入事实使用普通字色。绿色只沿用顶栏的当前位置下沿和宏任务目标浮层的下沿，本批没有时间轴的“现在”线，也不新增 buddy 色；常驻时间片的模拟已结束执行用 `--past-wash`，`mock-model` 仅为编造数据，不代表模型调用。失败红与 diff 增删色在这些示例中没有使用，核验通过不用绿、调整不用红。列表的文件夹图标跟随中性文字色，当前页面的头部种类标签不用图标；独立对比稿的做法 B 另用随文字着色的 12px 小记号，不改变现有页面。

解释不常驻。目标图标的“查看目标说明 / View goal description”和名称栏筛选按钮的“只看未结束的 / Unfinished only”沿用现有悬停／键盘聚焦提示，沿现有规则延迟 0.6 秒、文字居中、不接收指针；更多、关闭、返回和展开的图形不加重复提示。none 写入值另按开发者本轮要求提供“黑板不另存副本 / The blackboard keeps no copy”的悬停与键盘聚焦提示。项目种类由头部标签直接读出。路径和摘要不加复述它们的提示。

| 位置 | 实现时的过渡 |
| --- | --- |
| 项目组的展开／收起 | 沿现有 Radix 可折叠组件，高度和透明度 150–200ms，FolderOpen／Folder 图标随开合切换 |
| 封存证据、整合来源、整合记录和执行身份的展开／收起 | 沿现有 Radix 可折叠组件，高度和透明度 150–200ms；尖角同步旋转 90°，不另写高度测量 |
| 条目和步骤的选中／悬停、无框图标按钮的悬停 | 底色和文字色 100–150ms；悬停不能盖掉选中态，键盘焦点完整可见 |
| 宏任务目标浮层、删除对话框 | 沿现有浮层的入场／退场，透明度与既有位移 150–200ms；关闭后焦点回到原按钮，删除确认初始焦点落在取消 |
| 放不下的单行标题和项目名 | 末尾 28px 淡出；悬停／聚焦时按现有约 40px/s 滚出后半截，滚到末尾停，离开复位 |
| 减少动态效果 | 关闭上述过渡和标题滚动，保留内容、形状、选中态与焦点 |

这些是实施交接，不是静态稿中的动效验证。稿只固定开合与选中状态；HTML 的原生 details 可供看内容，不代表产品的交互实现。

## 中英文文案逐条核对

下表集中列出本批页面的全部文字模板，连同沿用文案和编造的正文，便于逐条查看。名称、状态和数值采用短语；“干净的三方合并”“有调整”是核验事实，不增加微任务生命周期状态。“位置”在固定结果和固定补丁中复用，“SHA-256”也复用。`Host`、`Lead`、`Worker`、`read`、`write`、`none`、字段名、项目名、时间、路径、ID、摘要与数字是固定值或数据，不翻译，也不生成 Host 身份。

| 键 | 用途 | 中文 | English |
| --- | --- | --- | --- |
| `tasks` | 沿用 | 任务 | Tasks |
| `buddies` | 沿用 | Buddy | Buddies |
| `settings` | 沿用 | 设置 | Settings |
| `macros` | 沿用 | 宏任务 | Macro tasks |
| `all` | 沿用 | 全部 | All |
| `running` | 沿用 | 进行中 | Running |
| `hostWait` | 沿用 | 等待 Host | Host |
| `reviewFilter` | 沿用 | 等待验收 | Acceptance |
| `review` | 沿用 | 等待验收 | Awaiting acceptance |
| `accepted` | 沿用 | 已验收 | Accepted |
| `completed` | 沿用 | 已完成 | Completed |
| `search` | 拟新增或改写 | 搜索标题、项目或 ID | Search titles, projects or IDs |
| `project` | 沿用 | 项目 | Project |
| `owner` | 拟新增或改写 | 归属 | Owner |
| `goal` | 沿用 | 目标 | Goal |
| `members` | 拟新增或改写 | {n} 个成员 | {n} members |
| `microTasks` | 沿用 | 微任务 | Micro tasks |
| `identifier` | 沿用 | 编号 | ID |
| `elapsed` | 沿用 | 历时 | Elapsed |
| `empty` | 拟新增或改写 | 没有已提交的微任务 | No submitted micro tasks |
| `more` | 沿用 | 更多 | More |
| `repositoryLabel` | 拟新增或改写 | 仓库 | Repository |
| `folderLabel` | 拟新增或改写 | 文件夹 | Folder |
| `delete` | 沿用 | 删除 | Delete |
| `cancel` | 沿用 | 取消 | Cancel |
| `deleteTitle` | 沿用 | 删除「{title}」 | Delete “{title}” |
| `deleteBody` | 沿用 | 这个宏任务的全部内容将从黑板上删除，不能恢复。 | All content in this macro task will be deleted from the blackboard. This cannot be undone. |
| `artifacts` | 沿用 | 封存的产物 | Sealed artifacts |
| `sessions` | 沿用 | 会话 | Sessions |
| `workspaces` | 沿用 | 检出与 Git 引用 | Checkouts and Git refs |
| `backup` | 沿用 | 滚动备份里的副本在备份轮换之后才消失。 | Copies in rolling backups remain until backup rotation. |
| `back` | 沿用 | 收起详情 | Close detail |
| `turn` | 沿用 | 回合 1 | Turn 1 |
| `acceptance` | 沿用 | 验收 | Acceptance |
| `overview` | 沿用 | 概述 | Overview |
| `session` | 沿用 | 会话 | Session |
| `result` | 拟新增或改写 | 固定结果 | Fixed result |
| `artifactId` | 沿用 | 产物 ID | Artifact ID |
| `sha` | 沿用 | SHA-256 | SHA-256 |
| `location` | 沿用 | 位置 | Location |
| `execution` | 沿用 | 执行 | Execution |
| `permission` | 拟新增或改写 | 权限档位 | Permission level |
| `cwd` | 沿用 | 工作目录 | Working directory |
| `checkout` | 拟新增或改写 | 检出 | Checkout |
| `folderChanges` | 拟新增或改写 | 改动位置 | Changes in |
| `folderStorage` | 拟新增或改写 | 原文件夹 | the original folder |
| `folderCopyHint` | 拟新增或改写 | 黑板不另存副本 | The blackboard keeps no copy |
| `content` | 沿用 | 内容 | Content |
| `seal` | 拟新增或改写 | 封存证据 | Sealed evidence |
| `snapshotDigest` | 拟新增或改写 | 快照摘要 | Snapshot digest |
| `tree` | 拟新增或改写 | 树 | Tree |
| `source` | 拟新增或改写 | 整合来源 | Integration source |
| `workerRange` | 拟新增或改写 | Worker 提交区间 | Worker commit range |
| `fromCommit` | 拟新增或改写 | 起始提交 | Start commit |
| `toCommit` | 拟新增或改写 | 结束提交 | End commit |
| `fixedPatch` | 拟新增或改写 | 固定补丁 | Fixed patch |
| `paths` | 沿用 | 改动路径 | Changed paths |
| `integration` | 沿用 | 整合 | Integration |
| `verified` | 沿用 | 已核实 | Verified |
| `target` | 沿用 | 目标 | Target |
| `before` | 拟新增或改写 | 整合前提交 | Before commit |
| `after` | 拟新增或改写 | 整合后提交 | After commit |
| `verification` | 拟新增或改写 | 核验结果 | Verification result |
| `clean` | 拟新增或改写 | 干净的三方合并 | Clean three-way merge |
| `adjusted` | 拟新增或改写 | 有调整 | Adjusted |
| `note` | 沿用 | 说明 | Note |
| `conflictPaths` | 拟新增或改写 | 冲突路径 | Conflicting paths |
| `hostPaths` | 拟新增或改写 | Host 补充路径 | Paths added by Host |
| `reviewNote` | 拟新增或改写 | 核对说明 | Review note |
| `close` | 沿用 | 关闭 | Close |
| `legend` | 沿用 | 图例 | Legend |
| `turnCount` | 沿用 | 回合 | Turns |
| `minutes` | 沿用 | {n} 分 | {n} min |
| `checkoutCount` | 沿用 | {n} 个检出 | {n} checkouts |
| `refCount` | 沿用 | {n} 个引用 | {n} refs |
| `goalTitle` | 沿用 | 目标说明 | Goal description |
| `revision` | 沿用 | 版本 1 | Version 1 |
| `goalHint` | 沿用 | 查看目标说明 | View goal description |
| `waitingHostState` | 沿用 | 等待 Host | Waiting for Host |
| `unfinished` | 沿用 | 只看未结束的 | Unfinished only |
| `steps` | 沿用 | 步骤 {n} / 3 | Step {n} / 3 |
| `previous` | 沿用 | 上一步 | Previous step |
| `next` | 沿用 | 下一步 | Next step |
| `sample.emptyTitle` | 示例内容 | 梳理归档 | Archive survey |
| `sample.emptyGoal` | 示例内容 | 梳理归档目录中的资料，列出下一轮需要核查的主题。 | Survey the archive and list topics to check in the next round. |
| `sample.indexTitle` | 示例内容 | 目录索引 | Directory index |
| `sample.importTitle` | 示例内容 | 校验导入 | Import checks |
| `sample.retryTitle` | 示例内容 | 重试处理 | Retry handling |
| `sample.readTitle` | 示例内容 | 归档清单核对 | Archive inventory review |
| `sample.writeTitle` | 示例内容 | 索引格式整理 | Index formatting |
| `sample.gitTitle` | 示例内容 | 导入校验调整 | Import validation update |
| `sample.readContent` | 示例内容 | 已核对 18 份索引与目录清单，发现 2 处重复编号。核对记录见下表。 | Reviewed 18 indexes against the directory inventory and found 2 duplicate identifiers. Findings are listed below. |
| `sample.writeContent` | 示例内容 | 已统一 18 份索引中的日期格式，补齐 2 处缺失标题。目录索引已核对。 | Normalized dates in 18 indexes and added 2 missing headings. The directory index has been reviewed. |
| `sample.item` | 示例内容 | 条目 | Entry |
| `sample.finding` | 示例内容 | 核对结果 | Finding |
| `sample.duplicate` | 示例内容 | 重复编号 | Duplicate identifier |
| `sample.adjustNote` | 示例内容 | 保留目标分支的日期解析规则，调整导入校验的调用；补充发布记录。 | Kept the target branch’s date parsing rules, adjusted the validation call, and added release notes. |
| `sample.cleanNote` | 示例内容 | 已核对任务书、封存证据及目标树。 | Reviewed the task, sealed evidence and target tree. |
| `sample.patchReview` | 示例内容 | 已核对封存改动、冲突处的调整和补充的发布记录。 | Reviewed the sealed changes, conflict adjustments and added release notes. |
| `sample.gitContent` | 示例内容 | 已补齐导入校验，覆盖重复编号与日期解析。两个提交包含本次交付的全部改动。 | Added import validation for duplicate identifiers and date parsing. Both commits cover the full delivery. |

## 静态渲染核对

2026-11-05 在 macOS 上使用任务指定的本地 Playwright 依赖及其 Chromium 156.0.8078.4，只读使用依赖，页面以 `file:` 打开；没有安装依赖或联网。截图和检查脚本保留在忽略目录 `tmp/adr032-p6a/`（`generate.py`、`capture.cjs`、`verify.py`），不进提交。`render-report.json` 记录逐帧排版读数，`browser.json` 记录浏览器版本。

本轮共 18 份 HTML、46 张截图：中英文各 8 页及各 1 份独立对比稿的深浅主题，加第 1 页中英文窄容器、中英文目标展开的深浅主题，以及 none 写入值中英文的深色悬停与浅色键盘聚焦提示。已核对静态页面的加载错误、外部请求、横向溢出和主要标签／值／标题／页签区域的文字相交；本轮均为 0。采样文字色与底色的 940 对组合最低对比度为 4.93:1，删除遮罩下的背景不纳入这个数；对话框与代表性的明暗画面另经目视检查。英文 HTML 无中文，提交和树用 40 位、摘要用 64 位的编造十六进制值，none 页面无 Git 产物区块。脚本只检查这些静态事实；未检查真实服务、查询、分页、删除、原生权限、文件封存、整合核验、键盘焦点管理或实际过渡，也没有运行模型。

## Buddy 页与 Router 的重做：待开发者确认

2026-11-08 第二轮，按提交 `1af41755` 中的[修订计划](../adr029/buddy-router-redo-plan.md)更新。开发者已认可第一轮的方向；本轮恢复目录既有的视觉元素，并按 ADR-021 把评价放回选中 buddy 的设置展开里。仍为 8 个画面、中英文各一份 HTML、每份深浅色整页截图；只核对静态渲染，不是控制台实现或实现验收。只画 1440px，不含窄屏稿或窄屏样式，窄屏没有核对。

| 画面 | 中文 HTML | English HTML | 深色（中文 / English） | 浅色（中文 / English） |
| --- | --- | --- | --- | --- |
| 目录：提供方分组与状态 | [打开](buddy-redo-catalog.html) | [Open](buddy-redo-catalog.en.html) | [中文](buddy-redo-catalog-dark.png) / [English](buddy-redo-catalog.en-dark.png) | [中文](buddy-redo-catalog-light.png) / [English](buddy-redo-catalog.en-light.png) |
| 目录：模型服务、选中 buddy 与评价 | [打开](buddy-redo-service.html) | [Open](buddy-redo-service.en.html) | [中文](buddy-redo-service-dark.png) / [English](buddy-redo-service.en-dark.png) | [中文](buddy-redo-service-light.png) / [English](buddy-redo-service.en-light.png) |
| 目录：位置、账户、额度与收起的节 | [打开](buddy-redo-harness.html) | [Open](buddy-redo-harness.en.html) | [中文](buddy-redo-harness-dark.png) / [English](buddy-redo-harness.en-dark.png) | [中文](buddy-redo-harness-light.png) / [English](buddy-redo-harness.en-light.png) |
| 模型：已有画像与三个名字 | [打开](buddy-redo-models.html) | [Open](buddy-redo-models.en.html) | [中文](buddy-redo-models-dark.png) / [English](buddy-redo-models.en-dark.png) | [中文](buddy-redo-models-light.png) / [English](buddy-redo-models.en-light.png) |
| 模型：无画像与失败运行 | [打开](buddy-redo-model-failed.html) | [Open](buddy-redo-model-failed.en.html) | [中文](buddy-redo-model-failed-dark.png) / [English](buddy-redo-model-failed.en-dark.png) | [中文](buddy-redo-model-failed-light.png) / [English](buddy-redo-model-failed.en-light.png) |
| Router：路由面 | [打开](buddy-redo-routing.html) | [Open](buddy-redo-routing.en.html) | [中文](buddy-redo-routing-dark.png) / [English](buddy-redo-routing.en-dark.png) | [中文](buddy-redo-routing-light.png) / [English](buddy-redo-routing.en-light.png) |
| Router：维护面与领域树 | [打开](buddy-redo-maintenance.html) | [Open](buddy-redo-maintenance.en.html) | [中文](buddy-redo-maintenance-dark.png) / [English](buddy-redo-maintenance.en-dark.png) | [中文](buddy-redo-maintenance-light.png) / [English](buddy-redo-maintenance.en-light.png) |
| Router：画像面 | [打开](buddy-redo-profile-plane.html) | [Open](buddy-redo-profile-plane.en.html) | [中文](buddy-redo-profile-plane-dark.png) / [English](buddy-redo-profile-plane.en-dark.png) | [中文](buddy-redo-profile-plane-light.png) / [English](buddy-redo-profile-plane.en-light.png) |

样式由 `base.css` 与 [buddy-redo.css](buddy-redo.css)组成，界面颜色只引用 base.css 的变量。harness 标志是 `HarnessLogo.tsx` 引用的 `@lobehub/icons-static-svg` 四个原始 SVG：`codex-color.svg`、`claudecode-color.svg`、`deepseek-color.svg`、`zai.svg`，以原图内联；彩色保留原值，单色 Z.ai 随文字颜色。这些标志自带的颜色依照控制台设计的例外保留。标志为 24px；节头、模型行、评价与领域开合使用当前 Lucide 的 16px、2px 线宽箭头。节头箭头只表示本节模型开合，账户摘要的展开是另一个入口。

目录恢复单行：圆点只表示有 buddy 在用，未启用的名字用次要文字色；推理强度与生效偏好写成字，行尾是并发；不写“已找到”、逐行“可用”、第二行启用计数或每组列头。提供方分组、分区页签一份、异名模型链接、无画像提示与“这次没读到”按计划改。节头账户摘要显示最紧窗口与重置时间，按量计费显示余额，不再有“账户与位置”按钮。位置、账户、额度在节头自己的底色内展开，竖线分栏、较重底线收住。

所有模型、对应关系、画像来源、归纳、微任务引用、用量、账户状态、路径、版本、额度和余额均为编造数据，不是对真实账户、模型或 harness 的核实。共 4 个 harness、8 个模型服务、9 / 40 个 buddy 启用；Codex 的 openai 与 relay-lab 分组，relay-lab 冷却到约 2026-11-08 16:49。openai 的 gpt-6.1-sol 从 16:05 起这次没读到，行与抽屉的提示包含起始时间、一小时后复查以及用户无需操作。gpt-6.1-terra 已启用但未建画像，以空心圆提示。ZCode 的 glm-5 新发现且未启用：画面 1、2 为查看新发现而展开，画面 3 显示无启用 buddy 时默认收起的样子与“1 个模型 · 启用 0 / 5”。

账户摘要的编造事实：Codex 每周已用 68%，2026-11-12 09:00 重置，比其 5 小时窗口已用 42% 更紧；Claude Code 的 5 小时窗口已用 37%，2026-11-08 19:00 重置；DSH 余额 CNY 128.60，ZCode 余额 CNY 56.40。Codex CLI 的安装版本 0.134.0、核实版本 0.133.0、占位路径 `~/.local/bin/codex` 与 ChatGPT 账户也是样例，不含真实账户标识。Codex 的只联网示例按当前声明写强制限制；ZCode 的未声明强制限制不得被解释为画像资格，本批未另展开 ZCode 的位置块。

画面 2 选中 `codex / openai / gpt-6.1-sol / high`：启用、优先、理由、计入评价 30 次和基础设施失败 2 次属于这个 buddy。下面评价的标题是 `codex / gpt-6.1-sol / high`，去掉提供方；46 个样本来自 `gpt-6.1-sol` 的 30 次和 `sol-latest` 的 16 次，换提供方仍看这一份同型 buddy 的评价。累计成功 41、一次通过 34（74%）、返工 9 轮、失败 5；维护面的归纳带两项编造微任务引用。评价与领域表各有一层可收起内容，领域树的“软件工程”节点还可折叠子节点。没有样本的格子留空。最后用次要层级显示换代前的 `codex / sol-previous / high` 及其 sol-latest 来源的 12 个旧样本，不把它们加到当前的 46 个样本里。父领域含子领域，不把父子样本相加。

模型分区只放模型的对应关系、画像、最近画像运行和用户写给模型的备注，不显示评价或列表样本。gpt-6.1-sol 对应 3 个模型服务名字：openai 的 gpt-6.1-sol、relay-lab 的 gpt-6.1-sol 与 sol-latest；每行自己的“改对应／标出换代／撤回”紧跟该行服务，服务链接指向目录中的对应行。S1／S2／S3 为编造来源标号，来源明细可展开，没有外部网址或请求。模型列表 5 项中 2 项已建、1 项运行中、2 项无画像且未运行；批量构建示例勾选 terra 与 pro，共 2 次，左栏静态确认内容写明模型、次数和画像 buddy 的额度。

gpt-6.1-terra 未建画像，最近运行打回 1 轮后失败：原因是缺少可核查来源，网页搜索 6 次成功，网页抓取 8 次全部失败，两轮用量分列。构建按钮已恢复为“构建画像”，没有残留“画像已在运行”的点击回复。claude-sonnet-4-6 的画像从 16:25 起运行中。这些都是固定视觉状态，未验证真实状态同步。

Router 路由面列表依次为 `codex / openai / gpt-6.1-sol / high`、`claude / anthropic / claude-sonnet-4-6 / high`、`codex / relay-lab / gpt-6.1-sol / high`；第三项等待重试。到头的上下移按钮保留完整箭头，用较弱的图形区分，聚焦可读原因。默认快速、等待 60 秒、重试间隔草稿 30 秒，探索为“每 [5] 个里给 1 个”并附简短说明。失败 2 / 28，原因用文字，微任务有标题、编号、跳转与复制入口。维护 buddy 为 Claude 四元组，间隔 3600 秒、待处理 7 个微任务；最新一轮已完成，历史一轮无合格答案时写明输出结构未通过并保留上一版。画像 buddy 为 DSH 四元组，候选的搜索词是 deepseek，因此只展示两个具备强制只联网声明的 DSH 示例。

任一面没有指定 buddy 时，左侧摘要写“未设置 / Not set”，右侧“谁担任”写“本面不运行，请设置 buddy。 / This plane does not run. Set a buddy.”，并保留“添加”或“选择 buddy”入口；记录保留已有历史，不借用另一个面的 buddy。本批不另出这个状态的画面。

HTML 只在部分分区与模型服务之间做静态文件导航；原生 details 可查看确认、来源、候选与评价的开合，复选框也只是本地表单视觉。按钮、复制、账户、发现、保存、模型构建、领域改名合并、旧深链接迁移和真实服务均未实现或验证；原生 details 是静态稿的可查看内容，实施继续用现有组件。

第二轮先按盘点脚本的方法，以仓库已提交的资产启动独立的合成预览，仅接受 `X-Hey-My-Buddy-Preview: synthetic-fixture-data`，无头截取 1440px 深浅色目录与抽屉 4 张现状图，核对标志、箭头与坐标后已停止该预览并释放端口；未连接日常服务或黑板。现状图及读数在 `tmp/redo-drafts/current-*.png`、`current-preview.json`。

最终 32 张主截图由指定 Node.js v24.21.0、仓库 Playwright 1.64.0 与 headless Chromium 156.0.8078.4 经 file: 离线生成，宽 1440、deviceScaleFactor 1、高随内容。字体只有 12／14／16／18px；普通控件 32px、小控件 28px，目录节头与模型行按现有导航行分别为 48px、至少 40px。文字色与实际底色采样 3388 对，最低 4.91:1。32 帧的页面错误、外部请求、横向滚动、文字越界／裁切、主要同级区域相交与英文页中文残留均为 0；刻度格等宽。明暗、中英文主图均经联系表目视核对，代表性原图另看。

`verify-round2.cjs` 另核对四个标志的 SVG 子内容与依赖包一致、24px 标志、16px／2px 箭头及当前目录的相对坐标、缺席提示起始时间、两层评价和领域节点的原生开合、两项来源与旧组、模型页无评价／样本、逐行操作与目录定位、完整的不可用箭头。脚本、原始读数和第二轮对照表留在忽略目录 `tmp/redo-drafts/`。这些均为静态事实；没有改动控制台实现、计划或 ADR，没有调用模型或核实原生权限，也未做产品功能、窄屏或实际过渡验收。
