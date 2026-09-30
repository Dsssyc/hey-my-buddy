# A 批：控制台与路由小修（0.22.0）

## 范围与状态

2026-09-30，在 `794a901`（`socu/buddy-core`）上建立独立 worktree `/Users/soku/.codex/worktrees/console-routing-fixes/hey-my-buddy` 与分支 `socu/console-routing-fixes`。本记录覆盖四项源码修复、自动化验证与合成浏览器核对。契约及包版本为 `0.22.0`，SQLite schema 仍为 `15`，没有修改数据库 schema、适配器、备份或升级实现。未合并主开发分支，未安装或升级日常服务，未修改用户配置，也未访问日常控制台。

先写已接受的 [ADR-020](../decisions/020-loopback-console-access.md)，依据用户 2026-09-29 的决定；随后更新 [console.md](../reference/console.md)，再实现登录行为。设计与契约提交为 `f56dfd4`，登录与滚动实现为 `eb0bc3c`，目标列表为 `53be4b6`，路由及 Host 补充为 `13c83d1`，列表失败恢复及最终资源构建为 `8457ac3`。提交未添加 AI 署名。

## 行为与原因

Buddy 配置页的外层原为固定高度但不滚动，模型区域允许 flex 收缩到零，再被 `.view-panel` 的 `overflow: hidden` 裁掉。1280×720 合成浏览器中展开 Router 与 DSH 详情后，模型区域和模型列表滚动区都为 `0px`，外层可见高度为 632px，内容高度已达 684px。修复让 `.buddy-page` 本身纵向滚动，模型列表和详情所在区域保留 420px 最小高度；原有内部列表、详情滚动和窄屏切换保留。

设置页虽有 `overflow-y: auto`，其 Grid 行却能缩小卡片：卡片继承 `.panel` 的 `min-height: 0; overflow: hidden`，内容被卡片内部裁掉，外层没有增加滚动范围。用 1280×300 强制溢出时，外层 `clientHeight/scrollHeight` 都为 212px，两行各被压为 98px，而两张卡片的内容分别需要 143px 和 218px。设置 `grid-auto-rows: max-content` 并限制列的最小宽度后，卡片保持自然内容高度，外层可以滚动。

工作目标列表在顶部且指针不在可见列表中时静默应用新顺序；悬停或向下滚动时，标题旁显示可点击的“有更新”。点击标记、指针离开顶部列表或回到顶部都会按条件应用。整行工作目标活动横幅已删除。隐藏页面、收起的列表和窄屏隐藏面板不能触发正在阅读的列表重排。刷新期间保留当前行及详情，重建分页游标；刷新失败后，下一次自动读取也会重建首屏和游标。全部执行记录页面仍使用的 `.new-records` 样式保留。

控制台默认免登录且不要求 cookie，`buddy console` 返回或打开固定的数值回环根地址，`expiresAt` 为 null。设置中的“需要登录”默认关闭，使用现有私有 `console-settings.json` 保存开关及独立修订号。开启时为当前浏览器建立登录会话，其他客户端使用现有一次性入口；入口有效期为 600 秒。服务端会话不再按年龄或闲置时间过期，cookie 使用可续期的 400 天上限，浏览器自身清理仍可能要求重新登录。退出和指定会话撤销立即撤销持久记录及内存权限；关闭登录开关会清空旧会话和入口，重新开启不能恢复旧凭据。只持久保存 token 哈希。只绑定 `127.0.0.1`、精确 Host、写入 Origin/Fetch Metadata/CSRF、不开放 CORS、CSP 和多窗口发布修订冲突检查均保留。

冻结合法候选恰好一个时，程序在同一事务内完成选择，记录“唯一合法候选，未调用 Router”；不创建 Router task、attempt、reader lease、模型调用、输入副本、预算、用量或停止证据。没有 Router 配置或任务超过 Router 的 8192 字节文本上限均不阻止单候选直选；完整目标仍保留，长任务的路由记录保留 `taskReference`。程序继续记录硬约束和偏好检查，并保存有效用户偏好（含固定偏好）及程序事实。多候选仍走原有 Router 路径，零候选仍停在 Host 边界。

`selection-get` 默认摘要及工作目标路由摘要携带冻结的 `constraints`、`requiredCapabilities` 和 `routingBasis`；后者包含候选数量、用户排除数量以及配置、原因和偏好来源。支持范围内最多 200 个配置的排除记录可保留，超出范围仍报告完整排除数量。控制台同时显示这些事实；历史记录缺失时不使用当前配置补写。未锁定目标重新路由时，展示该次路由自己的约束。程序直选的详情将实际模式、预算标为“未调用 Router”，偏好展示不依赖模型输入。

## 验证

最终验证使用 macOS、Python 3.13.3、Node 24.19.0。先执行 `npm --prefix apps/console ci`，再执行前端全量测试与构建，随后执行 `uv run --frozen python -m buddy.checks`。所有服务测试使用私有 `BUDDY_STATE_DIR` 和 `BUDDY_RUNTIME_ROOT`；清除了继承的 runtime、Worker 和 agent credential 固定值。自动化测试不调用模型。

- 前端：49 个文件、593 项测试通过；TypeScript 检查和 Vite 构建通过，已提交对应控制台资源。Vite 的 500 KB 单包提示仍存在；基线资源本身为 506,708 字节，此项未扩展为拆包改造。
- 完整检查：1618 项 Python 测试通过，161 项 DSH Node 测试通过，命令退出码为 0；私有测试根目录完成清理。
- 私有真实 HTTP：无 cookie 的固定入口、Host/Origin/Fetch Metadata/CSRF 拒绝、CSP/无 CORS、登录开关、600 秒票据复用与过期、跨 800 天及重启的会话、退出/撤销持久性、保存失败、开关修订冲突，以及两个免 cookie 窗口先后发布产生的实际旧版本冲突均覆盖。
- 路由：单候选没有 Router task/attempt 或 mock 调用、没有 Router 配置、长任务、幂等重放、偏好与排除快照、解锁后的重新路由约束、零候选 Host 边界、原有多候选和总量/模型并发路径均覆盖。
- 回归过程保留了失败证据：第一轮全量检查发现 CLI 摘要字段清单、单候选的旧并发测试夹具和 skill 的 4 KiB 限制共七处失败；更新字段、让并发夹具保留两个合法候选并压缩 skill 后，最终全量通过。固定偏好保存和分页失败恢复另有先失败、修复后通过的回归测试。

真实 Chrome 仅访问 `tests/probes/objective_console_preview.py` 的合成服务，并校验 `X-Buddy-Preview: synthetic-fixture-data`。布局测试展开全部 Router/harness 详情；设置页注入 16 条合成会话以确保长内容溢出。以下为最终布局记录，宽度均无横向溢出，模型区不低于 420px，页面错误为零。

| 视口 | Buddy 页可见/内容高度 | 设置页可见/内容高度 | 结果 |
| --- | --- | --- | --- |
| 1280×720 | 632 / 1846px | 632 / 1522px | 外层滚动、卡片内容可达 |
| 1440×900 | 812 / 1827px | 812 / 1522px | 外层滚动、卡片内容可达 |
| 390×720 | 610 / 3119px | 610 / 1619px | 无横向溢出，列表/详情可切换 |

`console_layout_check.mjs`、`console_interaction_check.mjs` 和 `console_routing_check.mjs` 是可复跑的浏览器探针。交互覆盖顶部静默重排、悬停和滚动延迟、标记点击、回顶部、保留选中详情、设置开关/撤销，以及程序直选依据。原始日志位于忽略的 `tmp/`，包括 `full-checks-first.log`、`full-checks.log`、`final-ui-tests.log` 和 `final-ui-build.log`；测量与截图在 `tmp/browser/`，其中 `layout.json`、`interactions.json`、`routing.json` 分别记录这三组结果。

## 委派与 Host 补充

通过共享 Buddy skill 委派两个独立 worktree，提交时均省略 adapter/provider/model/effort。列表任务 `1c81b5df-89d2-429a-9768-a6d47c84faaf` 的固定输出为 `cad1235c`；Host 检查后集成并修正共享横幅样式，完成集成记录和验收，之后补充自动恢复分页的回归修复。路由任务 `b6ad0a8f-65d5-4ee9-8e7e-25ef453bc876` 长时间未返回完整结果，Host 取消其执行；服务确认本身和后代均停止，并封存部分输出 `e62ae15d`（artifact `1d72fedc-1951-4b7d-ae79-ae2b9fe8db2a`）。Host 接手后补齐默认 CLI 约束字段、能力要求、长任务直选、历史约束、固定偏好、UI 展示、文档和验证。该 Worker 的执行结果仍为 cancelled，不能记作成功交付；本记录的完成结论来自 Host 集成后的源码和测试。

## 待办与合并注意事项

已从 `docs/design/backlog.md` 删除三条完成事项：工作目标活动横幅、本机控制台登录、单候选仍调用 Router。保留“服务与凭据”的两条事项给对应工作；ADR-019 背景中的滚动问题标为已修复并链接本记录。

与 ADR-019 第 11 条的 `socu/private-dirs` 批次分开交付。本批没有修改 `src/buddy/adapters/`、备份、升级或 schema 文件；在当前本地仓库没有可解析的该分支引用，因此没有验证两批组合合并。合并时保留 ADR-019 的滚动修复标记和另一批第 11 条的变更，分别处理 backlog；统一 `pyproject.toml`、`uv.lock` 与 `src/buddy/contracts.py` 的契约版本，并在最终树重新构建 `src/buddy/console_assets`，不要择一保留旧资源。备份/升级应继续完整保留 `console-settings.json` 的端口、登录开关和修订号；本批使用该现有 sidecar，没有引入新备份路径。

建议两批合入目标分支后，对组合树再次完成相同检查并重新构建共享 skill，再按已授权的安装流程切换日常服务。组合合并、安装以及安装后的固定地址验证均等待用户授权；本次源码验证不作为安装证明。

## Host 合并验收（2026-09-30）

Claude Code Host 在 `socu/integration-0.22` 上把本分支（`68be9d4`）合入 `socu/buddy-core` 当时的 `3c45c0e`，合并提交 `62fd32b`；唯一冲突在 backlog，保留"取消原因一律显示为用户取消"一条，删去本批完成的三条。独立只读审查（run `b5514429`，路由按软偏好选中 Codex GPT-6 Sol high）检查了免登录下的跨站写入、跨源读取、登录开关与会话撤销、单候选约束与容量及路由快照，没有发现安全问题；确认一处低严重度缺陷：零合法候选停在 Host 边界时，路由来源仍标为"模型选择"。修正任务（run `f92715f6`，路由选中 ZCode GLM-5.3-Flash max）让来源按记录冻结的 `routingBasis` 判定，候选数为 0 时记为 `no-candidate`、控制台显示"无合法候选"，缺少冻结依据的历史记录不补写；Host 读完改动后原样整合为 `9cf4bf7`。本批与 B 批第一阶段合并后的完整检查见 [B 批第一阶段记录](worker-accounts-phase1-0.23.0.md) 的 Host 合并验收一节。
