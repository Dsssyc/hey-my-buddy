# 控制台界面与黑板可靠性：执行计划与验收记录

2026-10-08，Codex Host `codex-adr027`。从最新 `socu/buddy-core@613faa4089c02ef1d5ef9b1d0743920de6f4a7cc` 建立 `socu/console-ui-and-board-fixes`，工作树 `~/.codex/worktrees/console-ui-and-board-fixes/hey-my-buddy`，不推送。来源为待办中 B1–B5 与控制台 U1–U11，原控制台读取批次的 Host 验收已读。第一部分先完成并由本 Host 验收，再开始界面实现；新宏任务的实际 ID 与微任务 ID 在首次提交成功后填写，Host ID 沿用。

日常服务与 Worker 属于所有会话，不重启、不停止、不替换，也不安装升级或改登录凭据。只经路由委派，四个 buddy 字段全部省略；每个微任务的 Worker 只运行影响范围内的测试，禁止完整检查、删除材料、stash 与创建/切换分支，改动留在工作区供服务封存；先建 ignored 专用目录并报告精确路径。所有测试清除继承 BUDDY_*、ANTHROPIC_*、VIRTUAL_ENV、UV_PROJECT_ENVIRONMENT，再设置明确的私有状态/运行时/源路径。日常委派受故障阻断时停下报告给用户，不自行恢复进程。范围内缺陷退回原 run，继续同样不指定，换 buddy 用 reroute；仅不可重试供应方限流的许可例外使用原 run 完整配置并登记。

## 黑板方案与身份比对清单

| 项 | 做法与复用机制 | 验证编号 |
| --- | --- | --- |
| B1 | 仓库公共 Git 目录与检出私有 Git 目录以规范路径＋inode 建立稳定身份，移除设备号，保留替换目录的防护。旧值是不可逆摘要，不能仅修改 `_identity` 后直接与旧数据库值比较；旧身份处理作为独立的升级数据迁移方案，先停下说明并取得用户决定。复用既有 upgrade、事务、meta 和固定 Git 产物，不提升 schema。 | B01–B04 |
| B2 | 在 submit 的检出准备之后、事务拒绝或后续入场失败时，仅撤销本次新分配的受管检出；复用分配清单、Git worktree 锁与精确路径回收。存储规划发现目录有、数据库无绑定的孤立检出，证明没有活动/引用/未封存内容后列为候选，apply 重新核对。日常现有孤立项仅作为已知现象，不手工删除。 | B05–B07 |
| B3 | 按宏任务分别缓存汇总与查询投影。变动驱动覆盖根/协助树的 task、workflow、attempt、验收、路由与展示元数据；只重算受影响的宏任务，不能仅依赖“数据库未提交”。事件用于定位影响对象，实际汇总用到但未保证有事件的 SQL 字段也列入标记；范围/排序/过滤变化重组列表。复用当前缓存、有界查询和 SQLite；不做新的通知协议。 | B08–B10 |
| B4 | Git 失败统一带实际 argv 与有界 stderr/stdout，覆盖 `_git` 与流式批量 Git 路径；保持错误码和原始异常链，敏感值及位置按现有脱敏规则处理。不追查旧日常故障原因。 | B11 |
| B5 | 新增排队写入者和读取者到期的两类真实快照回归：数据库与其他时限不变，缓存先命中，到期后 gate 的数量/状态更新；移除 `reads.py` 对 gate 租约截止的采集时目标测试必须失败，避免当前写入者已有另一层时限掩盖缺口。 | B12 |

B1 对比点逐项：`workspace.inspect`（公共/私有 Git 目录）；`prepare/_snapshot/_materialize`（来源与分配身份）；`verify/_validate_manifest`（原清单与当前检出）；`begin_turn` 与 stash 采集认领（仓库绑定）；`seal` 与部分/放弃产物（原仓库/检出）；`resolve`（冲突恢复对象）；`_artifact_binding/verify_sealed_output`（固定输入/输出原仓库）；`integration_verify`（目标仓库与检出）；`_allocation_provenance/cleanup_inspect/cleanup_remove`（分配、锁、封存、回收）；`workflow._reserve/_release` 与预约冲突（checkout_id 的独占索引）；`workspace_reservations`；`workflow_integrations` 的目标身份；`workspace_cleanup_plans`；`objectives.attach_objective`（旧宏任务 project_id 与新微任务来源）；宏任务列表/时间轴及 delegation 的 project 归属、projectId 筛选；replay、continue、dispatch 准备与 startup recovery 的已存清单。微任务交付须用源码搜索补齐每处调用及间接对比，不能只改最初报错的宏任务入口。

B1 需要用户决定的数据方案：升级时按已记录路径与现有 Git 分配/固定引用证明重新建立稳定锚点；可变的宏任务归属和预约索引使用稳定键，旧固定清单、inputSha256、manifestSha256、输出摘要和已接受记录原文保持。在 schema 15 的现有 meta 中登记有来源的旧→新身份映射，身份比对通过映射核对当前路径/inode，并防止旧预约与新预约重复占用。缺路径、证据不足或碰撞的项目不猜测、不删除，保留旧值并在读取中带未完成原因。只在私有夹具演练和实现升级入口，本任务不对日常看板执行迁移或安装。此方案会改变数据库里的身份索引/映射数据，属于用户要求先停下说明的“迁移数据”；用户于 2026-10-09 明确“我确认授权”，批准实现该方案并在私有夹具验证；本批不在日常看板执行迁移或安装。

## 界面方案与写入分组

| 项 | 做法与现成机制 | 验证编号 |
| --- | --- | --- |
| U1 | 依据在同一段落截断/全文切换，展开/收起按钮和 aria-expanded；检查同类截断入口，保留全文一次。 | U01 |
| U2 | 时间片底色/边框、标记、文字、交互高亮明确分层，消除片段独立 stacking context 的遮挡；图标可见且可操作。 | U02 |
| U3 | 复用现有 Popover/portal 与焦点机制，左对齐按钮、约束可见宽度；外点/Esc/焦点移出关闭并回到按钮，检查其他 details 浮层。 | U03 |
| U4 | 与 theme 同样使用 localStorage 记两个浏览器筛选，失败退回默认；恢复不可用筛选时发一次有界读取，选项暂不显示也保留偏好。 | U04 |
| U5 | 先在真实浏览器逐页查徽标、片段、标记、分段按钮、筛选、标题、浮层，列症状/原因/盒尺寸；统一 border-box、行高、padding、outline 等规则，再修共享样式，避免逐处偏移。保留改前/后对照。 | U05 |
| U6 | 设置页只展示已安装/实际服务的版本、契约、schema、来源提交、安装时间；复用服务已有真实元数据，未知明确显示；不加检查更新、更新或重启入口。 | U06 |
| U7 | 卡片展开/收起两态，带 aria-expanded；收起为前两行，选中或键盘进入隐藏卡片仍能辨认并回到它。 | U07 |
| U8 | 委派列表持久手动收起/展开，继续保留详情自动收起；视图切换移到标题旁按钮，两视图与收起条都可找到，tooltip/可访问名说明目的。 | U08 |
| U9 | 去顶栏全局刷新，三处非周期读取的本地入口、连接错误重试和数据截至各归其处；不新增固定间隔读取。按当前源码核对上一批已改变的周期读取。 | U09 |
| U10 | 安全证明的空白超过 5 分钟为固定像素空闲窄块，带断刻度/区分样式，去展开状态和按钮；悬停/聚焦看起止及长度，文字列表保留，范围不全/时钟不可靠不推断空闲。 | U10 |
| U11 | 单独读数线随 pointer 移动，普通区读时刻、空闲块读区间；离开消失，不重渲染整幅图。现在线保留但弱化，二者均在片段/旗标下面。 | U11 |

| 微任务 | 写入范围与顺序 |
| --- | --- |
| BG-ID：B1 | `blackboard/tasks/workspace.py` 的身份与比较、workflow/objectives 身份消费者、受影响存储/upgrade 元数据入口及对应测试；迁移决定后先做。不得改固定历史摘要。 |
| BG-CLEAN：B2、B4 | BG-ID 验收整合后，workspace/workflow 的提交撤销与 Git 诊断、`tasks/storage.py` 的孤立候选及对应测试；与 BG-ID 顺序写同一文件。 |
| BG-LIST：B3 | BG-ID 后，`tasks/objectives.py` 的汇总，console/read_cache/server 的宏任务读取、对应目标/HTTP/合成夹具测试；与 BG-CLEAN 函数边界分开，若需要共享 workflow 则串行整合。 |
| BG-GATE：B5 | 独立新测试 `tests/python/console/test_console_gate_deadlines.py`，现有 gate/console 实现只读；不与 BG-LIST 写同一测试文件。 |
| UI-FRAME：U2、U5 | 黑板部分整合后先做：ObjectiveTimeline/ui/styles 与相关组件测试，基于 Host 改前浏览器排查和共享盒模型清单，建立分层/尺寸基础。 |
| UI-TIME：U3、U7、U10、U11 | UI-FRAME 验收后：ObjectiveOverview/ObjectiveTimeline、timeline layout/scale、Popover/MarkerPopover、相关 CSS 与测试；顺序写时间轴/样式文件，保持已验收基础。 |
| UI-SHELL：U1、U4、U6、U8、U9 | 时间轴 writer 验收后或无共享文件时并行：DecisionDetails、BuddyConfig/theme、Settings/App/Objectives/Tasks/RunDetail/配置记录、api/types 和版本数据的必要 console 读取，相关测试；不改时间轴/Overview/CSS，共享 CSS 需求先发给 Host 在下一顺序回合授权。 |

每个 packet 将按实际源码收窄为精确文件/函数，并重复禁止删除、stash、分支操作、日常重启、完整检查和凭据/模型调用的边界；需要新文件先列入范围。同文件的不同微任务只有前一份验收后才启动，产品/测试/行为缺陷退回该原 run；Host 只处理明确的单句记录更正。ADR、CONTEXT、AGENTS、README、待办与 docs/reference 不改；DSH、buddy/harnesses、buddy/roles、实时通道、C-Two 0.7 与跨边界引用不纳入。

## 独立验证、测量与浏览器清单

B01：不同设备号、同路径/inode 重启模拟，新仓库/检出身份不变；不同 inode/替换目录拒绝。B02：旧宏任务可接受新微任务，归属查询/筛选/重放不分裂。B03：旧检出继续、封存、冲突处理、整合、占用拒绝与回收都可核对，固定摘要未改。B04：缺路径/碰撞迁移保留和原因、幂等重试/原子失败与 alias 防旁路。B05：准备之后拒绝无新孤立，原已有/复用检出不删；重复 requestId、事务失败和封存/锁防护。B06：合成孤立项目存储候选、重新占用/被替换后 apply 拒绝；日常孤立不操作。B07：收据与保留引用/补丁不被撤销误清。B08：无关数据库提交不调用该宏任务的汇总，相关根/协助树/验收/路由/元数据变化只重算必要对象；时间与分页/排序/过滤准确。B09：缓存边界与生成期间变化不漏判，移除选择性失效时测试失败。B10：按上一批配方重新生成约 200 MB 看板（60 宏、360 受管＋40 普通、4,000 证据、100 条大结果），不复制日常数据；固定种子、真实服务 handler 耗时和响应字节，分别量无关写入、相关单宏写入、全量变化，记录 median/p90/max，不断言耗时。B11：各 Git 入口失败 argv/输出截断与脱敏；移除诊断字段时断言失败。B12：排队 reader/writer 到期使真实快照更新，移除取租约截止的行时每个目标失败。

U01–U11 各有受影响回归与目标行为移除失败证据；布局层级和盒尺寸用真实 DOM/computed style/截图证明，不靠 jsdom 推断可见。U12 为真实浏览器矩阵：新生成私有合成服务，不使用日常状态/凭据；明/暗主题 × 宽/窄窗口，另覆盖 <=800px 高度、常见 100%/125% 缩放、悬停/选中/键盘焦点/运行/失败；条目点名的贴边片段、折行统计、隐藏选中卡、无不可用选项、断线重试、不完整/不可靠时间轴、多个固定空闲块逐项覆盖。U5 先留改前截图与症状/原因清单。每项至少一张改后截图，路径 `tmp/console-ui-and-board-host/screenshots/U01-…png` 至 `U11-…png`，U05-before/after 单列，均 ignored 不提交；矩阵与每图实际 viewport/theme/状态在记录里写明。

V01：全部固定产物独立全路径/哈希、实际受影响检查、隔离变异先干净通过后目标失败，保留原始失败与退回事实；测试编号以实际枚举变化登记。V02：最终源码从标准 Vite/tsc 重建并提交资产，核对字节。V03：最终代码干净提交上各一次 Python 完整检查 `uv run --frozen python -m hey_my_buddy.cli.checks`（默认并行）和完整前端 `npm --prefix apps/console test`，分别记录真实项数、提交号、退出码；只有失败或代码再变才再运行，记录变化仅跑仓库卫生。原始日志/夹具/屏幕图统一放 ignored `tmp/console-ui-and-board-host/`。完成后验收 microtask 并按确切路径正常回收，保留 Host 工作树供 Claude Code Host 验收，停止其他工作。

2026-10-09 恢复执行：原计划提交 c4cabf70 与原起点 613faa40 保留。为落实日常服务不启动/恢复的规则，Host 使用已安装 CLI 的原验证、权限处理与命令映射，但在本次客户端进程内把自动启动入口替换为现成 `transport._attach_read_only`（与公开 BoardClient.autostart=False 同样的附着机制），所有 health/submit/await/继续/验收调用只能附着既有服务；辅助代码在 ignored 目录，不更改安装包或日常服务。客户端拒绝 install/upgrade/restart/stop/worker-start/worker-stop 等命令。此为用户的进程约束优先于 skill 默认启动行为，不是变更产品 CLI。日常服务 PID 4965 已通过只读进程表观察为运行中，版本证书以实际 health 回复为准。

首批明确范围：BG-ID 允许 workspace.py、workflow.py、objectives.py、delegation.py 的身份消费者，install/upgrade.py 的已授权升级入口，必要的新 tasks/workspace_identity.py 与 store/workspace_identity_migration.py，以及对应身份/预约/升级测试；schema 与数据库 DDL 不改。固定清单/产物原文不得重写。BG-GATE 只允许新增 tests/python/console/test_console_gate_deadlines.py，与 BG-ID 不共写。微任务都禁止自行 git commit，改动留给黑板封存。日常 health 证书为 0.27.0、schema 15、PID 4965、7 个已有 Worker，未启动/已停止列表为空；这是日常版本事实，私有源码验证仍使用本批代码。

首批实际提交：宏任务 `obj-1ba05143-a5d7-496b-9b03-5d16681b2b33`，BG-ID run `90d65865-7d69-4df2-bfc4-a3b86099ef37` 与 BG-GATE run `898fdc6c-3b56-4e0f-ac4f-719d13b02f03`；Host 为 codex-adr027，首次请求均完全省略 adapter/provider/model/effort。两次由已安装 CLI 的标准权限/提交处理完成，RPC 附着入口只读、禁止冷启动/恢复，日常进程未操作。Worker 使用分别受管工作树，固定输入为 e10c22c6；只有交付后再独立审查、检查与变异，不将 queued/running 记为验收。

BG-GATE（B12/B5）独立核对：固定封存提交 be553cf5082d1cedb9a2140e0bd90764b1836e60，artifact ce4129d7-ef91-453e-8b2a-11a6536de6d4，全差异仅新增 tests/python/console/test_console_gate_deadlines.py（2 项），累计补丁 sha256 83c141856bd8e96dbe979b9247963afc895a32446313a029de17f3aa7addb8df。Host 以固定 blob 在私有状态/运行时复跑 2 项，退出码 0；独立进程内把 reads.py 的 `lease = gate_lease_deadline(service.store.db, now)` 替为 `lease = None`，同 2 项各因陈旧 304 != 200 失败，errors=0，退出码 1。排队 writer 到期时 active writer 仍有 479 秒、reader 场景没有 writer；其他扫描/会话/缓存时限不掩盖目标，正常 HTTP 304→200→304，数据库无人为删过期行。未覆盖边界：active writer 自身到期、登录会话分钟边界、混合租约、续期及 gzip 表示留给既有或其他测试，本微任务不改变产品。Worker 专用目录 `~/.local/share/hey-my-buddy/state/workspaces/ws-7a6457c672ac2721861402d9130fa371/checkout/tmp/bg-gate-b5-console-gate-deadlines/` 已在验收前保留至 Host 的 ignored `tmp/console-ui-and-board-host/review-bg-gate/worker-material/`；独立日志/探针为同目录 host-clean.log、host-no-lease-line.log 与 host-probes.json，不提交。尚未运行完整检查。

BG-GATE 回收事实：验收后日常服务按既有 accepted sweep 自动执行精确 checkout 计划 cln-33a2ae72-33db-489c-bdbe-ccf40b910f51，状态 applied；Host 首次请求与 sweep 发生 revision race（REVISION_CONFLICT），重新读 revision 8 后复得已应用计划，没有据此重启服务。精确路径 `~/.local/share/hey-my-buddy/state/workspaces/ws-7a6457c672ac2721861402d9130fa371/checkout` 已不存在，任务材料已提前复制、outputs/补丁与固定引用保留。

BG-ID 第一回合退回事实：原路由 zcode/zai-api/GLM-5.3/max 在 2026-10-09 返回供应方不可重试 rate_limited，429、代码 1310，原生 root 未完成，停止证据 self/descendants 均 confirmed；部分输出 d979963a3554286a19497d6a4ada3c014e1a2d52 未验证、未整合、无 finalArtifactId。Host 保留原始 get/result 与任务目录，依据用户的限流例外用原 run 的 continue（command bg-id-continue-rate-limit-1310-r1），完整配置 codex/openai/gpt-6.1-sol/max 与限流 reason 均登记；该配置经当次目录读取为 enabled、available、quotaExhausted=false，不改用户设置。原 run 返回 revision 5、executing/queued，配置续接不是验收。首次原消息已排队；续接阶段部分代码盘点消息因当前 Codex 问询能力 unavailable 没有投递，不把它计为 Worker 已处理。最终固定产物仍需逐项独立核对；任何代码/测试/行为缺陷在同一 run 退回。日常服务与 Worker 未停止/替换，未升级或迁移日常状态。

BG-ID 第二回合返回 attention（request req-558da8d9-d112-44ba-bac8-72a33b8c2b13）：未新增源码修改，保留的 7 份源码与 d979963a 相同，私有诊断 6 项中 2 通过、4 未通过（3 个断言失败、1 个错误），不是验收通过。原冻结范围遗漏 routing/router_boundary_data.py 的 `_continuation_context` 身份消费者，该函数自行读取旧清单/预约，无法只在 workflow 调用入口改副本。Host 在 B1 已授权的比对范围内用原 run scope-amend（bg-id-scope-router-identity-r1）将此精确文件加入，限身份比较/预约查询及复用 helper，scopeVersion 1→2；不修改 Router 决策或实时通道。随后原 run continue（bg-id-continue-scope-router-r1）完全省略配置四字段及 configuration/reroute，返回 revision 10、executing/queued，保留已选 Codex 配置。复制了本回合 report/调用清单/诊断日志到 ignored `tmp/console-ui-and-board-host/review-bg-id/round2-attention/`；本回合没有验收/整合。最终仍核对不同设备号、多个旧身份归属、物理替换防护、旧检出继续/封存/回收、完整升级保留/原子回滚及行为变异。

B3 拆分补充：合成夹具/旧代码基线不依赖 B1，先启动独立 BG-BENCH；精确范围仅新增 tests/python/fixtures/console_objective_board.py 与 tests/python/blackboard/tasks/test_console_objective_fixture.py，复用现有测试服务/MockWorkspace/SQLite，只做私有数据生成和结构 smoke。原 BG-LIST 的产品汇总缓存仍待 BG-ID 验收后，且先复用经本 Host 验收的夹具；同一 helper 若需修改顺序处理并登记。基线固定 613faa40，公开旧配方只用规模/关系作为来源，重新生成，不复制日常看板。计时明确取真实 handler 边界，客户端 RTT 单列，不固定耗时。
