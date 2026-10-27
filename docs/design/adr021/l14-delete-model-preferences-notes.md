# 删掉模型服务一层的偏好

2026-10-14，基于 `2f36659b` 的受管检出实现开发者于 2026-10-13 定下的第 9′ 个微任务。本记录是 Worker 的实现与检查说明，交 Host 验收。

## 删除清单与当前读写

删除新看板的 `family_preferences` 表与 `effective_preferences` 合成视图；`evaluation_preferences` 只存一个 buddy 自己的 `pin`、`prefer` 或 `exclude`，没有记录表示不设偏好。表的 CHECK 与发布边界都删除旧取值 `none`，清除继续使用 `preferenceChanges[].mode: null`。

删除快照、控制台核心及 buddy 分页里的 `familyPreferences` 和 `preferenceOverrides`，偏好条目与路由冻结的排除条目里的 `source`，发布请求与回应计数里的 `familyPreferenceChanges`。沿用 `preferences`、`preferenceChanges`、`profile`、`adapter` 与 `family` 的现有名字；其他来源字段继续表达各自的来源。

控制台删除模型默认偏好选择器、默认理由、模型默认固定的校验与提示、模型默认偏好的草稿、差异、计数、重放与历史分页合成。buddy 控件保留固定、优先、排除与理由，不设直接清除这一行；删除跟随模型选项。中英文文本与未保存标记都按 buddy 自己的偏好读取。模型备注、并发上限与按 buddy 启用保持现有路径。

当前路由候选、发布时的合法性复核、Host 边界与偏好输入直接读取 `evaluation_preferences`；固定仍收窄到固定的合法 buddy，排除仍移除合法边界中的被排除 buddy，优先仍只作为 Router 的软选择提示，合法替代项仍允许发布。程序原本按目录顺序给 Router 候选，没有新增程序排序；控制台已有的 `preferredVariant` 仍优先选出具体 buddy，effort 的显示顺序保持稳定。

## 从基点带设置

升级基点仍是固定的 0.34.1、schema 18 夹具，当前新看板仍在待做的 schema 19 升级里调整形式。`read_settings` 只在升级基点上读取一次旧视图的 `profile_id,mode,reason`，把基点已经合成的结果变成新看板的 `evaluation_preferences` 行；`write_settings` 按当前单层形式写入。模型服务默认值不再作为单独的表带走，覆盖值的理由与旧 `none` 抑制默认值的结果均由基点视图确定。

新增 `test_upgrade_preserves_each_buddys_composed_preference`：加载固定夹具到私有副本，补出两个 effort 变体，放入模型服务 prefer、一条 buddy exclude 覆盖与一条旧 none 覆盖，把一个继承默认值的 buddy 设为不可用并禁用；记录升级前每个 buddy 合成后的 mode 与 reason，走真实 `idle_snapshot` 与 `migrate_board` 后逐个比较，包括没有偏好的 buddy，核对新看板没有旧表或视图。原有升级与新建看板逐表对照测试继续运行，夹具文件未改。

旧名称的必要读取只留在升级基点的视图读取及基点夹具验证、私有旧形式测试输入中；旧接口与取值也出现在拒绝或缺席断言里。当前运行的源码、前端与测试数据没有双层读法。

## 顺带项

`harness_set` 只接收 adapter 与 path，删除公开与 `HarnessHealth.set_path` 内部已经无人使用的 expectedRevision 参数及冲突检查；路径后写的生效。健康刷新所用的 revision 递增与代际比较保留。`console_access_set` 只接收 requireLogin，删除设置前的版本冲突检查；会话撤销、轮换、CSRF、持久化与回应中的其他字段保留。控制台删除这两项写入的版本参数及专用冲突刷新，普通失败仍可重试。

任务关系 SQL 直接给出 `project_id`，删除 `recorded_project_id` 与 `project_identity_reason`，缓存成员列随之缩减；删除元数据、宏任务跨项目冲突与检出预备冲突中永远为空的 `identityReason`。微任务自己的 revision 与 expectedRevision 原样保留。

删除本次触及文件中未使用的 Python json、sqlite3、blocking 导入、harness 路径设置读取的旧行、测试中无使用的 frozen 与刷新返回值、HTTP 和预览中未读取的返回项，以及前端被删逻辑的导入和局部变量。`db.canonical_json` 是其他模块实际导入的导出，保留。Playwright 预览命令改用 PATH 中的 python3，检查时 PATH 指向 Host 给定的解释器，避免使用 uv；不把机器路径写进配置。

## Python 测试清单

- `test_fresh_snapshot_is_unconfigured_without_a_verified_router`：原守新看板未设置 Router 与空策略，删除两层字段断言，其他边界保留。
- `test_core_carries_the_state_keys_and_snapshot_wraps_them`：原守核心与快照的字段一致，改为只有 preferences。
- `test_family_preference_covers_every_effort_and_a_none_override_wins`：原守默认值扩散、覆盖胜出与 none 抑制；替换为 `test_buddy_preferences_are_saved_per_profile_and_can_be_cleared`，走真实发布与读取，证明 prefer 只属于指定 buddy、可修改为 exclude、按 ID 读取与 null 清除。
- `test_family_preference_null_clears_and_bad_families_are_refused`：原守默认值清除、未知模型服务与重复补丁拒绝；替换为 `test_retired_preference_shapes_are_rejected_without_changes`，证明旧发布字段与 none 拒绝且无写入。
- `test_disabled_family_policy_publish_does_not_fetch`：原守仅发布默认偏好不会触发模型事实抓取；改为按 buddy prefer，继续证明没有启用转换就不抓取。
- `test_user_pin_on_a_pending_profile_still_resolves`：原守 pending buddy 可被用户固定后解析；查询改读单层表。
- `test_pins_and_excludes_are_enforced_in_python_before_and_after_the_call`：原守路由调用前后的固定与排除硬边界；保留断言，路由偏好条目删除 source。
- `test_alternative_without_model_notes_completes_and_is_audited`：原守合法替代项可完成并留存审计；增加 prefer 不改候选集与目录顺序、进入 policyFacts、替代选择仍完成的断言。
- `test_routing_basis_freezes_the_user_exclusions_of_the_request`：原守排除事实在请求时冻结；条目删除 source，并断言没有 source。
- `test_direct_selection_retains_the_pin_that_narrowed_the_candidates`：原守唯一候选的固定证据；保留单层偏好证据，旧 none 清除改为 null。
- `test_manual_path_compare_and_swap_does_not_invoke_shell`：删除，原只守路径设置的旧 revision 冲突与第一次路径保留，未实际断言 shell 调用。
- `test_manual_path_requires_current_revision`：原守公开路径设置拒绝旧版本；替换为 `test_manual_path_write_uses_latest_value`，证明后写的路径生效。
- `test_upgrade_preserves_each_buddys_composed_preference`：新增，守上一节逐个 buddy 的升级等价。
- `test_verified_schema_18_stock_becomes_new_schema_with_only_settings`：原守设置表原样保留与历史清空；偏好预期改读基点合成视图，不再要求带走模型服务偏好表。
- `test_routing_brief_keeps_the_frozen_basis_and_constraints`：原守 CLI 简报保留冻结事实与硬约束；排除夹具删除 source。
- `test_bootstrap_is_the_snapshot_object_and_sets_a_private_session`：原守控制台 bootstrap 与快照形状及私有会话；快照清单删除两个偏好层字段。
- `test_host_origin_fetch_metadata_and_csrf_refusals_without_cookie`：原守来源与 CSRF 拒绝，设置请求删除版本参数，拒绝边界保留。
- `test_switch_enrolls_current_browser_and_requires_ten_minute_entry_for_others`：原守登录开关、当前浏览器会话与其他浏览器的十分钟入口，并含旧版本冲突；删除冲突断言，验证关闭成功与会话撤销。
- `test_access_fields_and_local_logout_are_refused`：原守字段、版本取值及本机退出拒绝；改为 requireLogin 校验与额外 expectedRevision 字段拒绝，本机退出断言保留。
- `test_access_setting_changes_invalidate_the_snapshot`：原守登录设置使快照 ETag 失效；请求删除版本参数。

- `test_fixed_fields_and_capabilities_are_rechecked_at_adoption`：原守发布时复核固定字段与能力；删除未使用的 frozen 读取，断言不变。
- `test_public_operation_stock_retains_source_schema_and_history_hashes`：原守固定基点的 schema 与历史哈希；循环中未读取的 label 改为丢弃，夹具与断言不变。
- `test_commands_use_the_same_validated_operations`：原守真实 HTTP 使用同一验证、发布与读写路径；丢弃未读取的控制台说明与目录刷新返回值，调用和断言不变。
- `test_assets_are_served_with_headers_and_traversal_is_refused`：原守资源标头与路径穿越拒绝；丢弃未使用的控制台说明，断言不变。
- `test_backup_preflight_is_computed_on_demand_only`：原守预检按需计算及条件读取；丢弃未读取的重放标头，断言不变。
- `test_pending_count_counts_distinct_roots_awaiting_their_host`：原守等待 Host 的根微任务计数；丢弃未读取的 HTTP 状态，断言不变。
- `test_a_database_replacement_invalidates_every_cached_route`：原守看板替换让缓存读路径重算；丢弃未读取的重放正文，断言不变。

## 前端与浏览器测试清单

以下逐条列出有输入或断言改动、被删除或被替换的原测试标题；原本守的行为由标题直接记录。同名保留项继续守原行为，只把两层输入改成 buddy 自己的偏好；新标题另列。只存在于模型服务默认值、none 覆盖与跟随关系的断言随那一层删除。

`apps/console/browser/catalogue.check.ts`：

- 原测试：`aligns the effort strip and selected panel, stacking the same drawer on narrow screens`。

`apps/console/src/buddies/catalogue-actions.test.tsx`：

- 原测试：`saves an absolute path with the recorded revision, closes, and applies the returned row`。
- 原测试：`clears the stored path back to automatic detection with null`。
- 原测试：`reports a revision conflict, rereads the record and retries against the new revision`。
- 新增或替换标题：`saves an absolute path without a revision, closes, and applies the returned row`。
- 新增或替换标题：`reports a failed save and retries the same path`。

`apps/console/src/buddies/catalogue-view.test.tsx`：

- 原测试：`offers pinning only to a model with a buddy that is available and on, and says so on the choice`。
- 原测试：`takes a reason once a default is set and publishes both as one patch of the model`。
- 原测试：`marks changed effective preferences on the effort strip and closed model row`。
- 新增或替换标题：`keeps the model row free of preference controls and retains a buddy control`。

`apps/console/src/buddies/harness-wire.test.ts`：

- 原测试：`saves a path or null with the expected revision and refuses a mismatched reply`。
- 原测试：`parses free local qualification from snapshots and command replies without retaining certificates`。
- 新增或替换标题：`saves a path or null without a revision and refuses a mismatched reply`。

`apps/console/src/buddies/models.test.ts`：

- 原测试：`marks a buddy whose switch or own preference differs from what is published, and nothing else`。

`apps/console/src/buddies/roles-view.test.tsx`：

- 原测试：`keeps the preferences of the catalogue editable and publishes them without the Router settings`。

`apps/console/src/buddy-settings.test.tsx`：

- 原测试：`shows the model's default, sets an explicit 不设 on one level and publishes mode none`。
- 原测试：`returns a level to 跟随模型 and publishes mode null`。
- 原测试：`writes enabled preferences as words and gives them the same styling`。
- 新增或替换标题：`clears one buddy's preference with 不设 and sends mode null`。

`apps/console/src/console-access.test.tsx`：

- 原测试：`enables login using the shown revision and refreshes the rotated authority`。
- 原测试：`refuses a stale switch response, reloads and shows the conflict`。
- 新增或替换标题：`enables login and refreshes the rotated authority`。
- 新增或替换标题：`reports a failed switch and keeps the recorded setting`。

`apps/console/src/console-data.test.ts`：

- 新增或替换标题：`selects the preferred buddy from effort order without changing that order`。

`apps/console/src/desktop-console.test.tsx`：

- 原测试：`removes the header refresh and displays the actual snapshot verification time`。
- 原测试：`uses the exact top navigation and presents the 22 native buddies as six rows of models`。

`apps/console/src/draft.test.ts`：

- 原测试：`publishes only the dirty user patches, never a table, a card or an effective preference`。
- 原测试：`sends a clear for an emptied note, a null family mode and a null override`。
- 原测试：`publishes an explicit none override distinctly from following the family`。
- 原测试：`keeps a stale pin and a stale Router out of every patch`。
- 原测试：`allows several pins: a new pin never clears another family's or effort's pin`。
- 原测试：`detects human edits independently of array order and program facts`。
- 原测试：`rebases the draft onto refreshed directory facts without losing user patches`。
- 原测试：`mirrors the view: an override wins, none hides the family default, others follow the family`。
- 原测试：`replays family and effort preferences over concurrent changes`。
- 原测试：`keeps a removed override and family default removed when the published rows come back`。
- 新增或替换标题：`sends a clear for an emptied note, a cleared preference`。
- 新增或替换标题：`keeps 不设 as an absent preference after clearing a buddy`。
- 新增或替换标题：`allows several buddy pins independently`。
- 新增或替换标题：`replays a buddy preference over a concurrent change`。
- 新增或替换标题：`keeps a local preference removal when the published row returns`。

`apps/console/src/edit-mode.test.tsx`：

- 原测试：`counts every unsaved change in the save bar`。

`apps/console/src/policy.test.ts`：

- 原测试：`allows disabling an unavailable configuration and editing its family note`。
- 原测试：`blocks a new pin for an available but disabled configuration, matching the board`。
- 原测试：`allows a reason-only edit of an existing unavailable pin`。
- 原测试：`blocks a new family pin only when no effort it pins can be selected`。
- 原测试：`reports a stale override pin while leaving other patches untouched`。
- 原测试：`reports a family pin only when none of its pinned efforts can be selected`。
- 新增或替换标题：`reports a stale buddy pin while leaving other patches untouched`。

`apps/console/src/unavailable-configs.test.tsx`：

- 原测试：`allows a reason-only edit of a retired pin while a new pin on an unavailable buddy stays blocked`。
- 原测试：`does not resurrect a preference the user removed when a page is re-read`。
- 原测试：`keeps a stale pin visible without requiring a repair before saving`。

`apps/console/src/use-profile-history.test.ts`：

- 原测试：`invalidates retained history when an event changes outside the bounded snapshot`。
- 原测试：`rereads a retained-only family note when the board event cursor moves`。
- 原测试：`does not render an old retained page during the cursor-change render`。

纯上一层测试 `draft.test.ts` 的 “mirrors the view: an override wins, none hides the family default, others follow the family”、`policy.test.ts` 的 “blocks a new family pin only when no effort it pins can be selected” 与 “reports a family pin only when none of its pinned efforts can be selected” 删除。模型默认固定与理由的两项 UI 测试由无模型控件、buddy 控件可用、请求只有 preferenceChanges 的同一真实保存流程替换；buddy 的 none 与跟随模型两项由不设清除并发布 null 的测试替换。harness 与登录开关的版本冲突测试改为普通失败可重试或保留已记录设置，不再保留冲突语义。

只调整共享快照夹具而未改变测试本体的文件：

- `apps/console/src/App.test.tsx`：删除旧快照中的两层字段，其原页面、导航、权限、查询或展示断言保留。
- `apps/console/src/api.test.ts`：删除旧快照中的两层字段，其原页面、导航、权限、查询或展示断言保留。
- `apps/console/src/buddies/navigation-page.test.tsx`：删除旧快照中的两层字段，其原页面、导航、权限、查询或展示断言保留。
- `apps/console/src/catalog-status.test.tsx`：删除旧快照中的两层字段，其原页面、导航、权限、查询或展示断言保留。
- `apps/console/src/console-session.test.tsx`：删除旧快照中的两层字段，其原页面、导航、权限、查询或展示断言保留。
- `apps/console/src/history-navigation.test.tsx`：删除旧快照中的两层字段，其原页面、导航、权限、查询或展示断言保留。
- `apps/console/src/model-concurrency.test.tsx`：删除旧快照中的两层字段，其原页面、导航、权限、查询或展示断言保留。
- `apps/console/src/objective-detail.test.tsx`：删除旧快照中的两层字段，其原页面、导航、权限、查询或展示断言保留。
- `apps/console/src/objective-tab.test.tsx`：删除旧快照中的两层字段，其原页面、导航、权限、查询或展示断言保留。
- `apps/console/src/profile-display.test.tsx`：删除旧快照中的两层字段，其原页面、导航、权限、查询或展示断言保留。
- `apps/console/src/routing-console.test.tsx`：删除旧快照中的两层字段，其原页面、导航、权限、查询或展示断言保留。
- `apps/console/src/theme.test.tsx`：删除旧快照中的两层字段，其原页面、导航、权限、查询或展示断言保留。

共享测试帮助文件 `buddies/page.testing.ts`、`buddies/roles.testing.ts`、`buddies/harness.fixtures.ts` 删除默认偏好帮助器与两层数据；`objective_console_preview.py` 直接给出三个 buddy 的偏好；`console_interaction_check.mjs` 登录设置请求只传 requireLogin，原来源、CSRF 与浏览器交互检查保留。

## 检查记录

执行环境使用 Node `24.21.0` 与 Host 指定的 Python 解释器；未给解释器环境安装任何东西。所有 Python 检查经检查 runner 运行，清掉继承的运行时、Worker 与代理凭据环境变量，使用默认 4 个进程，runner 的私有目录随每次检查退出清除。npm 缓存、下载的 Node 与 Playwright 浏览器放在检出里本次创建的临时目录。

`npm --prefix apps/console ci` 通过，安装 202 个包；锁文件未变。其输出报告 1 个 high severity vulnerability 与可选 fsevents 安装脚本提示，依赖审计或升级没有纳入本微任务。

最终 `npm --prefix apps/console test` 通过：78 个文件、1,088 项测试。首轮 3 个文件共 5 项失败是测试自身残留的旧控件或提醒文本、被删帮助器及遗漏的 level 导入：`unavailable-configs.test.tsx`、`buddies/catalogue-view.test.tsx`、`edit-mode.test.tsx`；先各自单独重跑，再修改，三个文件分别通过 17、17 与 12 项，随后完整前端测试通过。最终类型检查通过；额外未使用符号扫描在本次触及文件上没有残留，未触及文件的原有结果未扩大处理。

`npm --prefix apps/console run test:browser` 通过：96 项，56.3 秒，5 个浏览器 worker；中英文、宽窄屏与明暗主题的现有几何与页面错误检查通过，并断言模型默认偏好控件缺席、buddy 控件及不设选项仍在。Chromium 安装时 FFmpeg 的第一个下载端点超时，Playwright 自动重试后完整安装通过。

浏览器检查之后再次运行 `npm --prefix apps/console run build`，通过并重建打包的资产：`index-DhqWdDyC.js`、`index-Qm-l-qr6.css` 与 index.html；构建输出脚本 763.42 kB、样式 114.67 kB，gzip 分别 237.04 与 21.38 kB。构建保留既有的超过 500 kB 分块提示；产物没有删除的表或字段名称。

首轮完整 Python 检查执行全部 233 个文件：230 个文件通过，runner 统计通过文件内 3,841 项；3 个文件因 PyPI 下载超时失败。按规定逐个单独重跑，`buddy/runtime/test_worker_runtime.py` 4 项、`install/test_distribution.py` 2 项、`install/test_packaging.py` 12 项全部通过，退出 0 且私有目录清除。首轮失败输出中的关键部分如下，路径已省略：

```text
buddy.runtime.test_worker_runtime::test_staged_launcher_adds_runtime_worker_that_survives_stage_replacement
Failed to download `portalocker==3.2.0`
Request failed after 3 retries in 44.8s
client error (Connect)
operation timed out

install.test_distribution::test_wheel_and_sdist_are_complete_and_clean
No solution found when resolving: `hatchling`, `portalocker>=3, <4`
Request failed after 3 retries in 49.8s
Failed to fetch: `https://pypi.org/simple/portalocker/`
operation timed out

install.test_packaging::test_the_launcher_is_cwd_independent
Failed to download `typing-extensions==4.16.0`
client error (Connect)
operation timed out

install.test_packaging::test_the_launcher_reaches_the_private_cli_from_an_unrelated_cwd
Failed to download `pydantic-core==2.46.5`
client error (Connect)
operation timed out
```

局部检查也曾发现本微任务新增测试的准备错误：升级测试最初假设基点已有多个 effort，随后补私有变体时遗漏了基点必填的 updated_revision；目录候选顺序断言最初把第二、第三候选写反。均已改正并单文件重跑通过；升级夹具最终局部 6 项、decision 最终局部 35 项通过。最后整理时清理了未使用的测试变量，并补上新升级测试的连接关闭与 buddy ID 全集等价断言之后，在最终树上再次执行完整 Python 检查。

最终完整 Python 检查通过：233 个文件、3,859 项测试，跳过 0 项；使用默认进程数，不带选择参数，退出 0，私有目录清除。最终树没有下载失败或其他失败留待重跑。

最后核对改动都在授权范围，HEAD 仍为 `2f36659b`，没有提交；差异空白检查通过。清理本次临时目录的命令 `rm -rf tmp/delete-model-preferences apps/console/test-results` 被执行器拒绝，原因为 “rm -f style commands are not permitted. Use a safer approach”。整条复合命令没有执行；随后单独完成说明文件的结果记录与范围核对，没有换工具或路径重试删除。按微任务的拒绝处理规则，以 attention 交 Host 清理这两个目录；Node 下载、npm 缓存、浏览器下载、检查日志及 Playwright 结果仍保留在检出内，其他写入已完成。


## 核对边界

全仓库按删除的表、视图、字段与偏好取值搜索。当前源码唯一旧视图读取在待做升级的基点设置读取里；Python 中仅旧夹具验证、私有旧形式准备与拒绝断言保留旧名，前端仅缺席断言保留 familyPreferenceChanges。project_identity_reason、recorded_project_id 与 identityReason 在当前源码和测试中无使用；harness 与登录开关无版本写入参数，微任务的同名版本参数保留。

固定夹具、其基点生成脚本与历史设计或验收记录仍含基点形式；写入范围之外的 reference 与其他设计页尚待计划第 12 行的文档最后整理。本次没有修改 tests/fixtures、packaging 或其他 docs，没有改动日常安装或运行日常服务、Worker，没有读取用户自己的 harness 配置、凭据或执行模型调用。安装类检查只在 runner 提供的私有目录里构建或安装，浏览器仅访问带 synthetic-fixture-data 标头的预览；真实日常看板、真实 harness、Windows 与实际安装后的运行不在本次验证内。
