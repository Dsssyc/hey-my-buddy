# 控制台读取与受管检出：执行计划与验收记录

2026-10-07，Codex Host `codex-adr027`。从 `socu/buddy-core` 的 `54d159082c3ec2bb995c897c0833da216876485c` 建立 `socu/console-reads-and-checkouts`，Host 工作树为 `~/.codex/worktrees/console-reads-and-checkouts/hey-my-buddy`，不推送。来源是待办中每 3 秒读取成本（含 2026-10-07 复测）、每文件启动 git、共享 stash 的前两项、附着分支时的回收诊断，以及 ADR-027 留下的前三项。下文保留开始时的执行计划与各轮拒绝、整合、独立核对事实；历史候选的检查不能替代最终提交的检查。

## A：读取的变化来源与未变化判据

| 快照部分 | 驱动来源 | 未变化判据与时间边界 |
| --- | --- | --- |
| 配置、profile、目录状态、评价卡、证据摘要、偏好、计数 | 事件序号，评价表与配置修订，目录/账户 meta，聚合表 | 一致的数据库变化标记及修订；不能因无业务事件而忽略 SQL 变动 |
| 待处理数量、执行占用、模型家族并发 | tasks、workflow、attempt、request、worker/lease 与容量记录 | 现有服务端语义的全量计数；不用快照最近 100 条结果推算，过滤和去重口径固定测试 |
| harness 健康与额度 | harness_health 修订、quota/账户观测 meta、路径选择、扫描与成功读取事实 | 观测变化即失效；健康检查/扫描到期与额度新鲜度、重试窗口到期都要失效，不能永久跳过健康检查 |
| 路由健康与当前 Router | 路由/尝试/事件记录、Router 设置、额度与跳过期 | 持久记录变化或可选 Router 的时间边界变化；只缓存读取投影，不改路由或实时通道 |
| 评价读写门 | writer、reader、排队记录及 lease 到期 | 记录或生效 lease 集合变化即失效；时间越过最早到期边界必须反映 |
| 会话与控制台权限、服务状态、能力/资产可用 | 每次认证、会话与 access 修订、关闭/升级/写入资格的内存状态、资产存在性 | 认证在 304 判定之前；按会话隔离，权限/服务状态变化必须失效，不能复用其他会话的响应 |
| 与时间有关的字段 | 上述绝对期限与状态转换；timeline 的展示时钟、生成时间 | 不以不断变化的生成时间强制每次全算；动态显示在浏览器据绝对时间更新。逐项核对所有 now 派生字段，无法证明稳定时宁可报变化；越过实际语义边界不能回复未变化 |
| 备份预检 | 状态文件、证据目录、存储内容 | 从定时快照移除；仅打开存储面板或显式刷新时计算，沿用既有 preflight，不新增目录扫描协议 |
| 宏任务列表与时间轴 | 任务/回合/事件/验收、元数据与查询过滤/分页参数 | 复用事件与数据库变动判据，按查询缓存汇总；原生进度、时间边界和 observedAt 的消费者另核对，不用生成时间掩盖数据变化 |

使用 HTTP ETag / If-None-Match / 304；内部缓存仅保存已有投影与序列化响应，设置容量边界。先取一致的变化标记，再生成投影；生成前后标记不一致时不得把旧投影缓存到新标记之下。数据库标记优先复用 SQLite 的变化机制（PRAGMA data_version 需使用同一存活连接并覆盖本进程写入，不能每次新建连接后误认为未变）、既有修订和事件，不提升 schema。服务内存状态与真实时间边界另纳入判据，后续固定产物必须给出逐字段核对表。

标准库 gzip 负责协商压缩，保留正确 Content-Encoding、Vary、Content-Length 和空的 304；浏览器 Page Visibility 暂停所有定时 GET，恢复可见立即读取，沿用 AbortController 与现有请求顺序保护。顶栏待处理数量由服务端给出，快照不含执行完整结果；全部执行记录改为只用已有 `/api/tasks` 的分页读取。快照和宏任务列表命中时不调用投影计算或目录预检；认证、轻量变化检查和到期判定仍执行。

## 微任务与写入范围

| 微任务 | 工作与依赖 | 允许写入 |
| --- | --- | --- |
| A-S 服务端读取 | 完成条件请求、变化判据、瘦快照/待处理数、按需预检、gzip、宏任务汇总缓存；提供最终前端接口和私有规模夹具/前后测量脚本 | `src/hey_my_buddy/console/server.py`、必要的新 console 读取缓存模块，`blackboard/service/service.py` 的 console 读取部分、`blackboard/evaluation/evaluation.py` 的快照部分、`blackboard/tasks/objectives.py` 的读取汇总，以及相应 console/评价/宏任务/服务测试与私有合成夹具测试 |
| W1 检出批量对象 | B：保持原 hash-object --no-filters --stdin 的字节与写对象语义，批量化 git 调用；提交后才启动 W2 | `blackboard/tasks/workspace.py` 的对象编号/对象存储路径及相应 workspace 测试 |
| E 目录小问题 | 复用 180 秒扫描间隔限流明确读取；统一 reason；逐个处理五处未触发失败的保护 | `blackboard/catalog/catalog.py`、`catalog/accounts.py`、`service/harness_health.py`、`service/service.py` 的目录重读部分、`tasks/workflow.py` 的明确配置拒绝部分及对应 catalog/health/tasks 测试 |
| A-F 前端读取 | 在 A-S 固定接口整合后开始；条件 GET、可见性、分页执行记录、打开存储才预检、受影响前端测试 | `apps/console/src/` 中 api、类型、读取 hooks、App、Objectives、Settings、StoragePanel、相应组件与其测试；不处理其他界面问题 |
| W2 共享 stash 与回收 | 在 W1 整合后开始；C2 回合输入与封存的 stash 事实、消失项的恢复命令/原因未知；D 如实报告附着分支及处理办法 | `blackboard/tasks/workspace.py`；只有现有 get 无法呈现固定事实时才扩展 `tasks/workflow.py` 的事实投影并登记；相应 workspace/生命周期测试 |

C1 是明确的一句产品说明，由 Host 在 `buddy/roles/worker_services.py::governed_prompt` 直接加入并登记：分配工作区与所有者仓库共用 stash、分支与标签；不使用 git stash，不创建、切换、移动或删除分支/标签，改动保留在工作区或当前分离 HEAD。除此一句不改 buddy/roles，不改 buddy/harnesses、protocol 实时通道或 live_registry.py。控制台打包产物由 Host 在整合后从源码重建提交。

A-S 与 E 使用不同函数区域并由 Host 整合 service.py；W2 与 E 若需要同一 workflow.py 区域则依次整合。独立写者各使用黑板受管 worktree，不共用实体检出。新建一个宏任务，所有首次提交完全省略 adapter/provider/model/effort；退回时原 run continue 同样不指定，确需换 buddy 用 reroute。仅供应方不可重试限流允许原 run 完整配置恢复并登记，不改用户偏好、启用开关或 Router 设置。

## 现成机制与验证编号

- R01：变化标记逐字段覆盖事件、修订、SQL 观测、内存资格和时限；命中条件 GET 为 304、零内容，投影/序列化/宏任务汇总调用计数不增加；分别注入各类变化必须返回新内容，认证撤销不得 304。
- R02：定时快照不调用备份预检或证据目录遍历、不含完整执行结果；全量待处理数口径与超过 100 条的情形正确；固定合成规模的响应字节上限，不固定耗时。
- R03：gzip 与 identity 协商、q=0、Vary、Content-Length、解压一致性和无内容 304；移除压缩或缓存时目标测试失败。
- R04：页面隐藏后所有定时 GET 停止，恢复可见立即一次读取；旧请求与 StrictMode、304 保留正确数据；新增分页执行记录和存储面板按需预检回归。
- R05：私有合成看板不复制日常看板；目标约 200 MB 数据库、4,000 个证据对象、足够宏/微任务与 100 条大结果。按快照、宏任务列表、时间轴、执行分页、预检分别报告未变化/变化下的 identity/gzip/304 内容字节、响应头字节与服务端耗时，测量条件/次数/分位值同表登记；前后使用同一个生成配方和固定种子。
- R06：检出文件数从小到约 800 时 git 进程数量不随文件数增长；二进制、CRLF、空文件、大文件与原逐个 hash-object 对照；覆盖过滤器/换行配置、读/写对象、SHA-1 与可用的 SHA-256 对象格式及现有稳定性核对，移除批量化必须失败。
- R07：governed_prompt 含产品层面共享与禁令提醒；删去目标提醒时契约检查失败，不把提醒当作强制隔离。
- R08：stash 的输入/封存两次事实均固定在已有 JSON 产物；丢失条目报告提交与说明、git stash store 命令、原因未知，不自动执行、不据此失败。无变化、新增、重复提交、特殊说明、其他会话变动和失败/部分封存逐项核对；不观察分支/标签变化。
- R09：回收遇到附着分支时准确显示分支和处理办法；没有登记/锁不匹配保持独立拒绝，分离后重新核对可回收；不自动分离、删除或修改分支。
- R10：同一 harness 的明确缺失读取在 180 秒窗口内有限重读一次，窗口满后再读，分别覆盖模型恢复与仍不可用、并发请求和账户变化；不影响显式手动 refresh 或努力档单独消失的边界。目录拒绝均有 reason，未启用仍独立。
- R11：ADR-027 五项逐个给出变异/失败注入结果：账户切换清读事实、健康恢复还原、enabled:false 键的存在、既不可用又未启用的优先原因、缺档位不可达分支；不能证明区别且移除时明确说明，不保留无证据的合规结论。
- R12：每份固定产物独立审查与定向核对；删除目标行为时测试失败；控制台整合后源码构建并提交，私有合成看板在真实浏览器核对网络行为；全整合的最终提交运行一次默认并行完整检查，记录提交号与退出码 0，文档随后变化只重跑仓库卫生。

## 执行规则与记录边界

每个微任务 packet 明写只运行受影响测试、不运行完整检查；不删除任何文件或目录、不使用 git stash、不新建或切换分支；开始时建立任务专用目录，一次性材料放进去，交付报告确切路径。测试仅操作自身私有合成资料，真实共享仓库的引用只读取；涉及实际 stash/分支动作的原生失败注入由 Host 在自己的私有合成仓库执行。每个测试子进程清除全部继承 BUDDY_*、ANTHROPIC_*、VIRTUAL_ENV、UV_PROJECT_ENVIRONMENT，再设置明确私有状态、运行时、源代码与夹具路径；不安装升级日常运行时、不动登录和凭据，除委派外不调用模型。

原始日志与一次性材料置于 Host 的 ignored `tmp/console-reads-host/`，公开位置只用 `~` 或占位符。Host 对代码/测试/行为缺陷退回原 run，单句记录更正直接登记；验收后按精确路径回收任务检出与已报告专用目录，保留固定 refs、patch 和收据。ADR、CONTEXT、AGENTS、README、待办、docs/reference 由 Claude Code Host 维护；本次对外变化仅写这里，提交无 AI 署名。schema 保持 15，需要提升即停止说明。完成后停止，等待 Claude Code Host 验收，不开始其他工作。

## 首批启动与 Host 的 C1 核对

新宏任务为 `obj-e7f5f4a6-85a5-4338-a8af-8c396266b34d`。A-S run `b9955292-15e2-45e6-98d9-e367109fc281`，W1 run `6cada123-97cc-4a81-80a8-6926de525fb7`，E run `7fb2d7a2-553a-4620-89d9-8fd1f8fd98ea`；三次提交均完全省略四个 buddy 字段，经路由。微任务固定输入为计划提交 `e52af0ee61ea1f37165479225c0ce6c2e2cf87a0`。

C1 由 Host 直接提交为 `ff8bcee`，源码只在 governed_prompt 现有作用域段落加一句提醒，未改动回合协议或段落顺序。新增契约用例覆盖初始、重建与原生续接三种输入；Worker services/assistance hints 的 18 项定向检查退出 0，去掉提醒的内存变异使新增用例失败（退出 1，三个 subtest），原始日志在 Host 的 ignored 专用目录。uv/npm 仅准备本工作树的开发依赖，没有安装或升级日常运行时。

W2 的准备核对补充：workspace.prepare 在提交/准备时运行，不能将其时间冒充真正回合开始；begin_turn 位于 SQLite claim 事务内，不能在那里直接启动 git。因此 W2 必要的写入范围补充 `blackboard/store/store.py` 的 claim 准备传递、`tasks/workflow.py` 的冻结回合与 Host 事实投影及对应 mock/claim 测试，Git 采集仍在事务外并绑定该次回合。此项是现有黑板与 JSON 事实的扩展，不触碰实时通道或角色执行。

E 首次固定产物 `3e468305` 的受影响 209 个测试由 Host 独立重跑通过，但另加的并发恢复核对失败（退出码 1）：两个请求同时指定新模型，首个重读发布新模型后成功，第二个在读完前已按旧目录拒绝。独立审查还发现健康扫描与明确请求没有共用原子认领。已把固定产物登记 rejected，并在原 run `7fb2d7a2-553a-4620-89d9-8fd1f8fd98ea` continue，未提供任何配置字段；要求共用进行中读取、账户边界与回归测试。此产物尚未验收或提交整合。

R06 的 Host 基线由独立生成的私有 Git 仓库测得：SHA-1 与 SHA-256 下，`_observe` 在 8 个文件时启动 12 个 Git 进程、800 个文件时启动 804 个进程，读与写对象两种路径一致。二进制、CRLF、空文件与 4 MiB 大文件的编号逐个与原生 `hash-object --no-filters --stdin` 对照，写入后的 `cat-file blob` 内容一致。原始日志与生成脚本保留在 Host 的 ignored `tmp/console-reads-host/`；耗时只作观测，不作为回归条件。

R03 的标准核对参照 [RFC 9110 第 8.8.3.3 节](https://www.rfc-editor.org/rfc/rfc9110.html#section-8.8.3.3) 与 [第 13.1.2 节](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.2)：强 ETag 区分 gzip/identity 编码，If-None-Match 使用标准弱比较；gzip;q=0 与无内容 304 分别覆盖。尚未把计划中的核对登记为通过。

A-S 首轮原生完成但输出与 partial seal 均以 `WORKSPACE_REF_INVALID` 失败，没有固定产物；Host 的候选代码 HTTP 注入有两处失败：gzip/identity 共用强 ETag，缓存命中延迟 180 秒健康扫描。升级 fence 的核对通过。原 run 已 continue 且四个配置字段仍全部省略，接口说明和测量结论必须随代码修正；原始第一轮材料保留在 Host 的 ignored `tmp/console-reads-host/as-first-retained/`。

共同 Git 读取环境故障随后影响 E 第二轮封存与 W1 封存。Host 能解析同一提交，但旧后台进程不能读共享 Git 对象。确认全局活动执行为 0 后，使用现有 `restart` 替换守护进程（30699→33956），`runtimeContentId` 仍为 `7aadcb4af814adbde51eaa088e8c7ae1`、schema 15、Worker 保留；新守护进程能准备固定输入，旧 Worker 仍在启动前失败。随后核对七个 Worker idle、无活动或可执行排队任务，用现有 `worker-stop`/`worker-start` 协作重启七个旧 Supervisor；逐个有匹配旧 PID 的 `stopped` 持久记录与新 PID 登记。另一个等待 Host 的 run 原样保留，没有使用全局 stop 或 cancel，没有安装升级或修改登录凭据。恢复操作及失败的观察尝试保留在 ignored 日志中。原 A-S、E、W1 都继续使用原 run，未创建替代任务。

E 第二轮的冻结候选仍检出窗口/超时分支抛旧 `first` 错误：首次查找后另一合法 confirmed 读取已恢复模型，节流窗口命中仍拒绝；Host 实际注入退出码 1。独立审查另见账户核对到真实读取回调的间隙与 A→B→A 复用旧 flight。已将这些范围内缺陷继续交回原 E run，要求现有选择修订与真实服务回调接线的失败测试，不提升 schema。

W1 冻结候选的五轴只读审查未发现正常场景阻塞；Host 以清除继承环境、私有状态与运行时根独立运行 workspace、API、batch、Windows 结构与生命周期五个受影响模块：87 项、328.795 秒、退出码 0。这是受影响检查，不是完整检查；Windows 原生执行、总字节内存与 GC 的边界另记录。Host 的追加换行编号、跳过对象写入两处进程内变异均使目标测试退出码 1；逐文件 Git 变异与独立 8/800 文件对照仍在进行。W1 尚未登记最终验收，原 run 已请求在恢复后的 Worker 上重新封存。

A-F 写入范围在首次提交前补充 `tests/probes/objective_console_preview.py` 和必要的 `tests/python/cli/test_host_preview.py`：现有前端 `preview-contract.test.ts` 实际消费该合成预览的 `tasks.runs` 快照合同，瘦快照及按需预检必须同步其 DTO/端点，才能保持既有前端解析回归。补充仅用于 A 的既有合成夹具，不扩展控制台其他界面问题；对应 preview 检查属于受影响测试。

R06 的最终固定 W1 产物为 `66ecabc5-c878-43a2-90c2-97159b389bfa`，输出提交 `305d64b19e8774f9465fc7621a3dc1c88b54ba39`；两文件 SHA-256 与 Host 已测试的冻结候选一致。Host 独立 8/800 文件对照中，SHA-1/SHA-256 的观察读均为 5 个 Git 进程，写对象均为 7 个，不随文件数增长；原始编号及 `cat-file` 存储字节逐个一致（退出码 0）。Host 的逐文件 Git 变异使独立进程数量断言失败（800 文件、804 个进程，退出码 1），加上追加换行/跳过写入两处变异，共三处捕获。这里只整合代码，最终 accepted 与检出回收待整合后完整检查。

A-S 固定输出 `97711ff5` 的 Host 受影响 264 项检查通过（1 skip、97.056 秒、退出码 0），但独立跨会话 HTTP 注入失败（退出码 1）：A 缓存后 B 正常登录，未发生 SQL 变化，A 仍收到 304 和旧会话列表。已登记该产物 rejected 并原 run continue，要求会话增删/活动分钟与时间边界纳入标记。R05 方法也退回原 run：第一版将客户端往返填作服务端时间，并以虚构 reason 的状态行估算头字节；最终要分列逐路由服务端 wall/producer 与客户端时间，按真实状态行计头字节，显式使用私有运行时根。首次四处累积变异且 gzip 未触达 server 绑定的旧证据已由 Worker 作废，后续七处隔离变异仍待 Host 独立核对。此输出尚未整合。

E 第三轮越过原 packet 写入范围，`test_service.py` 的两行既有消费者断言导致黑板拒绝封存。Host 核对新增 `expected_binding=None` 参数所需的断言，只将这个精确文件登记为 scope Version 2，再按固定 fingerprint adopt；原 scope 冲突与 failed turn 保留，resolved-output `b4ffecdd` 尚未验收。独立固定源审查未发现单服务正常场景阻塞，但已登记 rejected 并原 run continue，要求在已登记范围内实际验证与诚实封存，不能把补登记写作第一轮通过。另有末尾多余空行使 diff --check 退出码 2，已通过同一 run 的 inquiry 通知修正。

R10/R11：E 固定 `b4ffecdd` 的 Host 独立受影响 225 项检查全部通过（41.526 秒、退出码 0）；九处进程内变异全部使目标测试失败，源码文件未改。包含新事实重判、选择纪元、真实服务绑定、账户清读事实、独立健康恢复、`enabled:false` 键、不可用/未启用优先级、正常目录文件覆盖下无 profile 行及目录 reason。reason 变异第一次选了不走该拒绝路径的测试，未触发；Host 更正为实际缺失目录拒绝两例后捕获，原始尝试日志保留，未计作保护通过。结果与完整失败测试 ID 在 ignored `tmp/console-reads-host/e-host-mutations.json`；该源仍待原 run 完成范围返修/最终封存与整合，未登记 accepted。

A-S 最终固定输出 `2baaeb60bb8b27fa40d58394fdee926ef2043afe`、产物 `1f2ec035-1549-410a-87d8-039f12c3f39a` 的增量只读审查未发现阻塞。Host 在整合字节上独立运行 112 项受影响检查（60.552 秒、退出码 0），跨会话登录注入与强 ETag/升级 fence/180 秒健康扫描三个 HTTP 注入均退出码 0。九处独立隔离变异覆盖标记缓存、gzip、编码验证器、扫描时限、数据库文件身份、预检拆分、瘦快照、会话标记与活动分钟时限；第一版缓存变异错误处理 callable 参数使 HTTP 500，作废此项，改为保留正确参数并强制每次标记变化后重新验证。源码不写入变异，最终结果以 corrected 日志为准。

E 最终固定输出 `2d0e86bf58768433b2356d08fd28bf57415b1eec`、产物 `46275e26-992c-4f58-b610-b9ae7a2c62b3` 相对已验证 `b4ffecdd` 仅删除末尾空行，十个文件的固定摘要已核对（共享 test_service.py 保留 A-S 新测试与 E 消费者断言）。本轮 Worker 在补登记范围内实际验证 49+11+3 项、退出码均为 0；Host 前述 225 项与九处变异仍对应相同代码。Worker 的第三/四轮报告把旧 ADR-027 返修的 stash 事故误写成此次 E 首轮退回原因：旧事故在 adr027-host-review.md:60，本次 E 首轮记录明确为并发恢复拒绝与扫描认领缺陷。此为一句历史记录更正，由 Host 直接订正并登记，不改旧事故、原失败回合与 scope 违规的结论。

W2 首次固定输出 `023860373003da0b647c21a2377b83315451fc10` 的 D 真实私有 Git 分支回收诊断/拒绝/分离后重试通过，但 C2 的实际 claim 在 common .git 路径运行 stash list 报 worktree 错误，开始事实为 unknown；另在 64 条完整观察之后仅新增第 65 条，截断观察误报旧条目丢失。独立审查同时发现 shared-refs 自摘要无法复算及采集候选与实际认领不一致。Host 已登记 rejected 并原 run continue，四配置字段仍全部省略。最初 Host 夹具误以为连续 store 同一 OID 会新增 reflog 条目，失败未到目标行为，已作废；用不同真实 stash 提交更正后上述两项实际失败（退出码 1），原日志保留。此产物未整合或 accepted。

R01/R03 的 Host corrected 变异逐个先在未改行为上通过目标测试，再隔离移除行为并复原：九处均捕获、退出码 0（变异目标自身退出 1），失败 ID 和详情保留在 `tmp/console-reads-host/as-host-mutations-corrected.log`。修正后的缓存变异是合法标记返回值每次变化，目标因收到 200 而应为 304 失败；没有沿用 HTTP 500 的无效证据。

A-S 在整合后追加 R01 私有 SQLite 替换注入失败（`as-marker-epoch-before.log`，1 项、退出码 1）：旧数据库缓存 query a/b；用同 schema、评价修订 97 的真实数据库替换文件，a 重算为 97，b 仍 304 返回旧修订 0。`data_version` 在重新连接时可与旧连接值相同，单次返回 None 未失效其他 URL/会话的旧 entry。已对最终 A-S 产物登记 rejected 并原 run continue，要求连接/文件纪元或等效全缓存失效与可失败回归；旧整合记录仍是未经验收的历史事实，前端接口不变。此前单次 marker 测试与九处变异不能证明这个多缓存恢复边界，本项尚未通过。

W2 第二轮固定 `af3c6f0d` 的 Host 真实 Git 三项通过（11.194 秒、退出码 0），但新增认领漂移用例失败（0.199 秒、退出码 1）：首次选择 A 后只重采 A，A 在第二事务前被合法取消，第二事务关闭覆盖围栏并重扫启动 B，将可读的 B stash 记为 not-collected。已登记 rejected、原 run continue，四配置字段仍省略；第二轮尚未整合。摘要现可复算、工作区摘要另存，截断比较以 partial 明示不可证方向；读时人为破坏权威 SQLite JSON 的完整性校验、异常长 stash 描述体积仍为未验证边界，不把直接摘要 helper 的篡改检查写成常规 get 的拒绝能力。

A-S 连接纪元返修固定输出 `59043194a8ca4bbb7ae2162327425e30ed87506c`、产物 `791ba07f-8225-43a8-b0c3-c47b55770351`：内部标记为连接纪元加 data_version，每次重开失效全部 URL/会话的旧 entry，HTTP DTO 不变。Host 在三文件固定增量上独立运行 33 项受影响检查（11.192 秒、退出码 0），原真实数据库替换注入退出码 0；新增回归覆盖多键多会话、同文件异常重开、生成中替换与真实 HTTP 多路由。移除纪元递增的独立进程内变异先确认两目标未变时通过，再使两目标均失败（目标退出码 1），失败日志保留，源码未被变异改写。全整合完整检查仍未运行。

W2 第三轮 `5aa01ba5` 的 A→B 漂移与三项真实 Git 核对通过，但同一个 A 被另一个已登记 Worker 实际 claim、合成回合 completed、Host continue、标准工作区准备和 dispatch 都完成后，第二趟仍会启动未被本趟采集的 A（2 项中 1 失败、1.062 秒、退出码 1）。此为正常 API 链，不是伪造 SQLite 状态；只 continue 未完成准备的变体诚实返回 awaiting-workspace-preparation，不计作失败。首个错误的 workflow_acknowledge 操作也未到目标，作废并保留日志。已登记 rejected 并原 run continue，四配置字段全省略。

R08/R09 最终固定 W2 输出 `fb560d9cd2a28629776d8434e10d3280564161fb`、产物 `2155c77f-7fbc-402d-9f77-16bd53a441ce` 在每趟事务核对仓库观察成员：第一次漏采回滚定向重采，第二次仍漏则返回瞬时空 claim `reason=stash-observation-pending`、`retryAfterMs=1000`，无永久收据/attempt/lease/resource/turn，下一次再读；真实读取失败保留仓库键与 unknown 原因，仍可开始回合。Git 在写事务外。Host 的 A→B、同 A 重排两项注入，以及公共 .git 读取、唯一 stash 丢失/恢复命令/不自动恢复和附着分支拒绝/人工分离重试三项真实私有 Git 核对全部退出码 0。Host 独立运行 213 项受影响检查（含批量对象回归，523.605 秒、退出码 0），11 处进程内隔离变异在未改目标先通过后均捕获、目标退出码 1，涵盖开始/封存/Host 投影/恢复命令/原因未知/分支诊断/gitdir/截断/摘要/第一与第二趟覆盖围栏；源码未被变异改写，失败编号在 `tmp/console-reads-host/w2-host-mutations.json`。

共享 stash 事实冻结于回合 input 的 context.sharedStash 与既有 workflow_artifacts 中的 shared-refs JSON，分别保存开始/封存条目、原因未知及安全 git stash store 命令，旧 workspace 摘要另存，记录摘要可从 canonical payload 复算。governed 回合产物数 2→3；比较状态为 observed/partial/unknown，changed 独立。每侧只保留最新 64 条，摘要最多列 16 个丢失/新增项，截断方向明确不可证，不把未见条目宣称消失；异常长说明的输入体积、权威数据库被人为改坏后正常 get 的摘要核对、Windows 原生执行及日常共享仓库注入未验证。restrict_to 本轮移除变异存活，因为每趟成员围栏独立保证正确性；保留它用于有界目标范围，不称为额外正确性保护。

A-F 首次固定 `c1b810ab` 的完整 42 路径在声明范围内，两个独立固定源审查发现隐藏换范围、过滤成员变化、选项新鲜度、请求票号无界、会话在途发布与预检关闭保护问题。Host 在真实 hooks/api 的四个 Vitest 注入中全部失败（901 毫秒、退出码 1），已登记 rejected 并原 run continue，四配置字段全省略。首次 Host 夹具每次 render 重建 api 导致循环，停止自己确切的 Node/npm 测试进程后改为稳定 api，再计上述四个目标失败，旧尝试不计保护结果。React StrictMode 双 effect 只在开发构建，不把它写成生产重复预检的证明；关闭/隐藏与在途代次的产品边界仍要求修正。当前前端产物尚未验收、构建或真实浏览器核对。

## R05：私有合成规模与最终后端测量

夹具从固定配方独立生成，再分别复制给基线与最终后端；不是日常看板，也没有复制日常资料。SQLite 为 193,835,008 字节，60 个宏任务、360 条委派执行记录及 40 条普通执行记录、4,000 份证据文件、100 个约 1.9 MB 的大结果、130 个待 Host 根任务。基线为 `54d15908`，最终后端为 `50c058fa217501073431e21bb882d6e1445ac264`；原始材料在 ignored `tmp/console-reads-host/host-benchmark/`，完整分编码/分变化类型的样本统计见 [测量 JSON](console-reads-and-checkouts-measurements.json)。
所有字节来自真实 raw HTTP 状态行、响应头和 Content-Length 内容；服务端 do_GET 入口至 finally 的 wall 时间与客户端往返分列，按完成通知对应同一请求，投影/命名操作时间另附。200 首读和后续 gzip 200 各单次；未变化条件读取每编码 10 次；评价表 SQL 修订变化后每编码 5 次。毫秒只作观测，不作固定回归条件；结构回归固定 304 零内容、零重投影、零定时预检及响应大小上限。

| 读取 | 基线无变化：状态／内容＋头字节／服务端中位 ms | 最终 identity 200：内容＋头／单次 ms | 最终 gzip 200：内容＋头／单次 ms | 最终无变化 identity／gzip 304：内容＋头／服务端中位 ms |
| --- | --- | --- | --- | --- |
| 定时快照 | 200／286870＋577／725.061 | 139458＋669／20.556 | 5543＋691／0.106 | 0＋655／0.060；0＋655／0.061 |
| 宏任务列表 50 条 | 200／34451＋576／2110.820 | 34451＋668／2133.292 | 2211＋691／0.118 | 0＋655／0.100；0＋655／0.063 |
| 单宏任务时间轴 | 200／10855＋576／98.712 | 10855＋668／94.642 | 1259＋691／0.082 | 0＋655／0.089；0＋655／0.074 |
| 执行记录分页 50 条 | 200／115508＋577／58.196 | 115508＋669／59.682 | 4887＋691／0.059 | 0＋655／0.053；0＋655／0.061 |
| 按需备份预检 | 定时快照内重算，分项内容 8,110 字节；无独立接口 | 8110＋667／650.584 | 588＋690／616.773 | 0＋655／625.324；0＋655／639.055 |

| 读取 | 基线 SQL 变化 identity：状态／内容＋头／中位 ms | 最终 SQL 变化 identity：状态／内容＋头／中位 ms | 最终 SQL 变化 gzip：状态／内容＋头／中位 ms |
| --- | --- | --- | --- |
| 定时快照 | 200／286870＋577／728.484 | 200／139458＋669／13.761 | 200／5547,5548＋691／13.351 |
| 宏任务列表 50 条 | 200／34451＋576／2102.321 | 304／0＋655／2149.430 | 304／0＋655／2061.682 |
| 单宏任务时间轴 | 200／10855＋576／94.357 | 200／10855＋668／93.586 | 200／1259＋691／93.634 |
| 执行记录分页 50 条 | 200／115508＋577／57.265 | 304／0＋655／57.709 | 304／0＋655／57.347 |
| 按需备份预检 | 快照内重算 | 304／0＋655／635.556 | 304／0＋655／634.655 |

数据库标记保守覆盖所有 SQL 变化：评价修订变化也会重算宏任务/执行分页，内容相同则重算后仍为 304，宏任务重算仍约 2 秒；没有把它称作变化时汇总的优化。备份预检按需接口即使 304 也重新计算，约数百毫秒，页面定时请求不触发它。缓存暖窗口的额外五次快照合计内容 0 字节、重投影 0、预检 0，snapshot 无 runs 与 backupPreflight，pendingCount=130；identity 小于 2 MB、gzip 小于 400 KB。强 ETag 按编码区分，跨编码借用验证器返回 200；gzip 解压与 identity 内容相同。完整 median/p90/max、客户端时间和投影归属均保留在测量 JSON，不能拿客户端时间代替服务端时间。

## R11：ADR-027 五处保护的独立失败证据

五处均保留，测试覆盖的是对应公开/独立入口下的实际差别；移除目标保护的进程内变异均使下列测试退出码 1，没有把另一层保护下的通过当作这一层的证明。源码未写入变异。

| 保护 | 失败测试编号 |
| --- | --- |
| 账户切换 clear_confirmed_read | `blackboard.tasks.test_catalog_trust.ExplicitCatalogTrustTests.test_account_round_trip_never_resurrects_the_old_accounts_read_facts` |
| 健康恢复 restore_retained_availability | `blackboard.service.test_harness_health.HealthRecoveryRestoreTests.test_recovery_restores_the_retained_catalog_without_any_publication` |
| enabled:false 键存在 | `blackboard.tasks.test_continue_catalog_trust.ContinueCatalogTrustTests.test_override_file_route_without_a_profile_row_refuses_not_enabled` |
| 不可用先于未启用 | `blackboard.tasks.test_continue_catalog_trust.ContinueCatalogTrustTests.test_simultaneously_unavailable_and_disabled_reports_the_catalog_reason_first` |
| 目录覆盖无 profile 行 | `blackboard.tasks.test_continue_catalog_trust.ContinueCatalogTrustTests.test_override_file_route_without_a_profile_row_refuses_not_enabled` |

R04 前端第二轮 `3e5d4a5e25f5a94353d65f08615f470c22b68c9e`、产物 `b782ec36-12a9-4df9-a19f-c2532bb05e79` 的七个首轮 Host 探针通过，但四个新增独立 Vitest 探针均失败（`af-host-r2-before.log`，7 pass / 4 fail、退出码 1）：旧响应 JSON 解析晚于同 URL 新响应时覆盖新 ETag；80 个已结束失败/中止请求留下无界 ticket；document 已隐藏但 React 清理尚未执行时仍启动定时快照；已加载 70 条筛选记录的首屏仍有下一页时保留离开筛选的旧行。独立源码审查另指出缓存的 observedAt 冻结时间轴展示时钟，已连同上述四项退回原 A-F run 第三轮 continue，四个配置字段均省略。测试中的 document 状态注入只是失败探针，真实浏览器可见性另核对；未改动的 RunDetailPane 既有手工刷新问题属于其余界面问题，不纳入本轮。

## 对外可见的变化与边界

服务端快照的 tasks 从完整执行结果改为 pendingCount；顶栏数量按全量根任务事实计算。执行记录由既有 /api/tasks 分页接口读取，备份预检改用独立 /api/backup-preflight，打开存储面板或显式重算时获取。各只读路由支持标准条件请求、按编码区分的强 ETag、304 与 gzip，失败和权限错误仍按既有错误形式返回。宏任务汇总命中缓存时不重新计算；可选参数不同的查询独立缓存，认证始终先执行。

Worker 回合说明新增共享 stash、分支与标签的提醒及对应操作禁令。回合 input.context.sharedStash 和封存 shared-refs JSON 记录两次 stash 观察、丢失项提交号与说明、原因未知及 git stash store 恢复命令；仅报告，不自动恢复，也不据此判失败。采集覆盖漂移后暂缓启动时返回 reason=stash-observation-pending、retryAfterMs=1000；没有创建回合或持久收据。附着分支的回收拒绝带 attachedBranch 事实、真实分支名和人工分离后重新核对的办法；未登记与锁不符仍分别拒绝。

明确指定目录缺失时，同一 harness 复用已有 180 秒扫描窗口与进行中读取；期间新目录事实仍重新判定，可恢复就接受。账户选择变化作废旧读取认领；手动 refresh 不受该限流限制。目录原因的拒绝统一带 reason，未启用的 reason=not-enabled 单列，目录不可用优先；继续指定配置与提交使用同样的目录事实边界。推理强度单独消失的边界没有改变。

本次没有量日常看板 CPU，没有复制日常资料，也没有验证 Windows 原生 Git 执行。批量对象读取仍会暂存合计文件字节，写入路径使用 Git 自身对象格式与对象打包机制；内存峰值和 Git GC 行为不是本次的优化目标。stash 观察上限、未知与截断方向如 R08 记录，不宣称能归因于 Worker；异常长说明和权威数据库人为损坏后的常规 get 摘要验证未覆盖。schema 未提升；日常运行时、登录与凭据未作安装、升级或修改，除本批委派外没有真实模型调用。最终浏览器、源码构建与默认并行完整检查结果另在收尾记录中给出。

R04 第三轮固定 `42f341e4d2a6b6eb0836055ededc7be0cbf6ec6c`、产物 `e7793acf-845c-489a-b271-84737af14323` 的完整 44 路径范围、无删除及 SHA-256 与整合树一致；材料和源码已冻结保留。Host 原有 11 个探针通过（854 ms、退出码 0），但新增四个扩展探针失败（11 pass / 4 fail、退出码 1）：目标列表和执行列表的首次 180 ms 防抖分支仍在隐藏清理间隙发 GET；执行列表首屏返回后在隐藏间隙续读；已加载 100 行、范围总计 400 行时，离场成员无法在有界续走中被剔除。覆盖数大于等于已载行数加新见行数的判据实际上要求看见每个已载 ID，缺成员只有走到全范围末尾才能证明；本次以符合现有 keyset 降序的唯一 createdAt 夹具核实仍失败，日志 `af-host-r3-sorted-boundary.log`。独立源码审查捕获两处首次防抖漏检，没有捕获大范围成员问题；实际失败结果优先，第三轮已 rejected、原 run 第四轮 continue，四个配置字段仍完全省略。

Host 在 Vite 临时转换中单独移除 ObjectiveTimeline 的 displayObservedAt 布局接线，原 39 项组件测试通过、移除后仍通过（`af-clock-wire-clean/mutant.log`，没有源码写入）。这不是目标行为已被测试守住的证据；已在同一个第四轮原 run 排入协作问题 `af-r4-clock-wire-329563fb-dd21-496b-b561-ff96265f59e8`，要求补组件与父组件接线回归，不改正确行为、不扩其他界面。消息入队不代表修复或答案，本项待最终产物核对。

Host 对第三轮未要求改动的 API/Hook 进行六处隔离 Vite 变异，分别先跑干净目标再移除行为：If-None-Match、解析后的票号核对、所有结束路径清票、会话纪元和 Hook 时钟推进五处被现有目标测试捕获。响应缓存上限 32→1024 在 api.test 的 29 项中存活；改用已有 Host 私有成功 URL 缓存边界探针后捕获，目标原实现通过、变异失败（共收集 15 项，实际执行该目标 1 项，其余跳过）。没有把未触达目标的整套测试当作上限保护已证明；日志与 JSON 均保留，并在同一个第四轮 run 排入 `af-r4-body-bound-874a8a48-778a-4576-8e74-88436dc6e781` 请求将可失败的成功请求缓存上限回归加入仓库，行为本身不需改。两条协作消息仅已入队，最终交付是否包含回归仍待固定产物核对。

R04 第四轮 `f939a9de` 的 Host 15 个探针通过（872 ms、退出码 0）；固定 44 路径范围/字节核对通过。Host 独立 28 文件、390 项受影响 Vitest（28.391 秒、退出码 0）及 3 项 Python 预览检查通过；源码构建成功，4 个 Vite 产物的哈希保留，构建没有修改 44 份固定源码。目标行为的六处独立 Vite 变异均在干净目标先通过后捕获：两个首次防抖、隐藏续读、移除范围下界证明、错误提前证明、关闭面板时仍预检。随后因两处永久回归缺口退回同一原 run；两条已排队协作消息未进入固定产物，不能按已交付处理。

仅测试第五轮 `cb257afc87e97598a8b602ff2f9a71cdd7fa0121`、产物 `44aee6c4-ee4b-4614-935e-61816a762a01` 只改 3 个测试文件，共 45 个累计范围路径；Host 76 项定向测试和 tsc 通过，布局接线、父组件传值及成功响应缓存上限三处隔离变异均捕获（组件 42 项中 2 失败、父组件 4 项中 1 失败、API 30 项中 1 失败），先验证干净目标通过且没有写入源码。然而真实浏览器新窗口首次读取服务端旧缓存时间轴时，现在标记从旧 observedAt 起算，比实际时刻落后约 15 分钟；当前 HTTP Date 加有效缓存体的真实 createApi/Hook 探针精确失败 900,000 ms。第五轮已 rejected、原 run 第六轮继续修这项功能缺陷，四配置字段省略；此前相对锚点测试不足以覆盖首次取得旧缓存体的边界。

真实浏览器核对使用固定配方的新私有状态、运行时与实际服务；没有复制日常看板。IAB 的显示开关/新标签仍报告 visible，原生窗口隐藏配合检查工具也仍持续读取，这些尝试不登记通过。改用任务专用 Chrome 窗口的真实后台标签，宏任务/列表/时间轴期间约 49.8 秒没有任何新 GET/POST，回到前台约 148 ms 启动三个零内容 304；执行记录从自己的分页 API 加载 50→100 / 360 条，后台约 103.4 秒零读取，返回约 349 ms 启动快照和两页的零内容 304，保留 100 条已加载记录。字节与耗时来自服务端请求日志，时间只记录，不设回归断言；新构建的最终源码变化后仍需复核相关场景。临时检查工具的停靠布局已恢复，日常窗口未用于夹具或修改。

打开设置中的存储区域只触发一次预检，定时快照没有调用预检；额外一次是 Host 对私有端点的只读 HTTP 检查，不能算浏览器重复请求。该真实报告 HTTP 200，领域 ok=false、没有错误 envelope，前端却报请求未成功；新增真实 createApi/负向报告探针失败（退出码 1），完整报告仅存 ignored 私有日志。已经在第六轮原 run 排入 `af-r6-negative-preflight-b8885119-4c4c-4d82-b36c-5a2265743afb` 要求修复领域报告与协议错误的区分，最终产物待核对。完整检查仍未运行，暂不登记本批验收。

第六轮时钟产物 `f7e57eecd5bfbc47c2d79d7bf498b4f80a746d9c`、`9f134f4c-6bff-413d-93c1-ddea39da1617` 从每次成功 HTTP 200/304 的标准 Date 取得当前核对时间，不把时间与缓存体一同保存；缺少或无法解析 Date 时以本机读取完成时间作为回退，原始 observedAt 不改。内部客户端时间轴方法返回投影和核对时间，不新增服务端协议或 schema。Host 69 项受影响测试、tsc 通过，三处独立变异均先确认干净目标通过后失败：忽略 Date、丢弃 304 新时间、再次锚定旧 observedAt。Host 隐藏切目标探针的 stub 更新为新的客户端返回形状，并先断言旧目标确实加载成功；原来形状不匹配而提前失败的检查不能证明后续切换行为。修正后的 17 个 Host 探针为 16 pass / 1 fail，唯一失败仍是合法负向预检报告。第六轮已 rejected、原 run 第七轮只修此接口合同，四配置字段仍省略。

## 最终前端产物与浏览器复核

最终 A-F 固定产物为 `58ab160e-007a-40b5-9088-ebca221c4da6`，提交 `00f32cf578b18900e2ea3e4a8bd4ae1b25723e86`。累计完整 45 路径由 Git 固定，范围和 SHA-256 与整合树逐项相同，不依赖截断为 32 路径的 envelope。第七轮只改 API 与对应测试：负向预检领域报告可正常读取和重验证，实际 HTTP/错误信封仍拒绝。Host 17 个探针全部通过，55 项受影响检查、tsc 退出 0；恢复错误分类和忽略真实信封的两处隔离变异都使目标断言失败。此前固定代码的 390 项、后续时钟 69 项与追加测试 76 项分别对应当时的影响面，不相加冒充不重复的全量测试。最终全量验证另列提交号与退出码。

最终源码用现有 Vite 与 tsc 构建，4 个产物的 SHA-256 固定保留，JS 为 index-BvBeWtDi.js；仅提交构建器的标准输出。真实 Chrome 的任务专用合成窗口请求了该新 JS，设置中的合法负向预检显示将跳过 4,000 项、拒绝 0 项的具体提醒，未再显示读取失败；首次取得旧缓存时间轴时，现在标记与实际 02:32 一致，原始生成时刻仍为 01:20。最终源码再放入真实后台标签 29.29 秒，零新 GET/POST；恢复约 109 ms 内启动快照、宏任务列表与时间轴三个零内容 304，现在标记推进到 02:34。时间差仅为观察值，不锁入回归测试。临时 IAB 标签、专用 Chrome 窗口与私有服务已回收；服务的确切 PID 停止后进程退出 0。截图与原始请求记录留在 ignored 专用目录，没有放入公开仓库。

合入 core 前的 Python 测试编号为 2,874→2,987，新增 113、删除 0；前端编号 667→728，新增编号 64、退出原编号 3（原预览契约与两条快照供数的执行历史入口改名或替换）。完整增删清单见 [测试编号 JSON](console-reads-and-checkouts-test-ids.json)，以真实 unittest discovery 和 Vitest 非静态收集获得；收集未运行测试。前端最终清点第一次输出与运行器的元数据文件同名，数据被覆盖，已作废并换独立文件重新收集成功，没有把元数据误当测试编号。

## 待 Claude Code Host 验收的整合边界

五项范围与新宏任务保持本批归属；所有首次提交和原 run 的 continue 均省略 adapter/provider/model/effort，没有使用限流完整配置例外。本批独立补丁不修改 harnesses、实时协议、live_registry.py，也不修改 roles 中除 C1 提醒之外的内容；ADR、CONTEXT、AGENTS、README、待办、docs/reference 的本批独立差异为零，schema 仍为 15。core 的维护与 ADR-025 改动通过其整合提交原样引入。R12 保留补充指令到达前的微任务验收、回收和检查事实；合入 core 后最终代码的检查结论见 R13、R14。日常运行时、登录和凭据没有安装、升级或改动，模型调用仅限本批委派。

最终 API 文件再独立重放 If-None-Match、解析后票号核对、所有结束路径清票、会话纪元和成功响应缓存上限五处移除，干净 34 项目标先通过、五处均产生实际断言失败，没有写入源码。此前旧锚点推进实现已被当前核对时钟替换，其旧变异不计作最终实现的防护；有效失败证据按功能目标关联各固定源码版本保存。最终源码与公开记录的仓库卫生预检退出 0，未含本机主目录的绝对路径。

## R12：合入 core 前的代码、完整检查与回收（历史结果）

合入 core 前的候选代码提交为 `92f2a8c5f2a502dc81c0607e4edefa3e3fdf52e4`（`perf(console): reuse conditional reads and pause hidden polling`），当时工作树干净后执行了一次完整检查：`uv run --frozen python -m hey_my_buddy.cli.checks`。UTC 2026-10-07 18:42:08 开始、18:50:27 结束，约 498.921 秒，退出码 **0**；沿用默认并行数，Python 4 个 Worker、187/187 文件、2,987 项测试、跳过 1 项。每个测试使用检查器的全新私有状态和运行时，启动 uv 前清除继承的 BUDDY_*、ANTHROPIC_*、VIRTUAL_ENV 与 UV_PROJECT_ENVIRONMENT。完整检查后核对四个构建产物与该提交逐字节相同、工作树没有源码或产物变化，随后只补验收记录。完整检查的 stdout 只给出上述 Python 数量，不把前端编号清点数量冒充它打印的执行数量。命令、完整提交号、开始/结束时刻、退出码和产物哈希见 [最终核对 JSON](console-reads-and-checkouts-verification.json)，原始日志留在 ignored `tmp/console-reads-host/full-check.log`。

启动包装器最初误用了旧 ADR-027 任务的“最新 core 必须为祖先”门槛，在实际检查命令启动前退出；该次没有运行完整检查。更正 ignored 包装器为本批用户指定的 `54d15908` 起点后，运行了上述一次实际完整检查。开始时 `socu/buddy-core` 已推进到 `fbfa8eac881ee7da8d23465df155d270b8707b60`；当时按并行互不依赖的起始指令保留原起点，没有合入 ADR-025 的并行改动。此为补充合入指令到达前的历史边界，不能作为合入后代码的验证结论；新的整合与验证在 R13、R14 记录。

Host 在上述合入前候选的完整检查通过后，对以下五个固定产物和各自已核对的 integration ID 登记 accepted；早期 rejected、失败回合、范围更正和原 run continue 均保留。这里的微任务验收与本批等待 Claude Code Host 验收是两个阶段。

| 微任务 | 固定产物 ID | 整合记录 ID | 精确回收路径 | 回收后的补丁／固定 refs 数量 |
| --- | --- | --- | --- | --- |
| W1：B | `66ecabc5-c878-43a2-90c2-97159b389bfa` | `int-30d76e78-c0d0-49e2-a327-9f3f25c8f689` | `~/.local/share/hey-my-buddy/state/workspaces/ws-583e2f0d0bc68a74047b0c30a7c95c2a/checkout` | 2／10 |
| A-S | `791ba07f-8225-43a8-b0c3-c47b55770351` | `int-6e397de0-8072-4882-ac3f-62c039fb6596` | `~/.local/share/hey-my-buddy/state/workspaces/ws-fea15e63020cd44ec10b1687615751c3/checkout` | 6／26 |
| E | `46275e26-992c-4f58-b610-b9ae7a2c62b3` | `int-7890aca7-1e68-4a61-b698-9516033d36c4` | `~/.local/share/hey-my-buddy/state/workspaces/ws-210c7b90a632d599db3dea4e7ce34ca8/checkout` | 6／17 |
| W2：C2、D | `2155c77f-7fbc-402d-9f77-16bd53a441ce` | `int-151fd1ab-3649-4e44-b3db-2d41cdea331a` | `~/.local/share/hey-my-buddy/state/workspaces/ws-57bcfd51de4eb45a698a700ba3106efe/checkout` | 8／24 |
| A-F | `58ab160e-007a-40b5-9088-ebca221c4da6` | `int-f76c7a95-7ca6-4e59-b1e2-db613307a9b0` | `~/.local/share/hey-my-buddy/state/workspaces/ws-4067d6910ce27bc5721014b79db717ec/checkout` | 14／42 |

五项均有持久 `applied` 计划，计划的路径与分配路径完全一致；Host 独立确认检出路径及符号链接均不存在，计划中每个输出补丁实际存在，每个固定 ref 仍可解析。Worker 的一次性任务目录随其所属检出由正常回收机制删除，所需源码快照、材料和日志已先复制到 Host 的 ignored 专用目录，原始产物与失败证据保留。Host 分支和 `~/.codex/worktrees/console-reads-and-checkouts/hey-my-buddy` 留给 Claude Code Host 验收，不推送。

现有守护进程在 accepted 后会调用 `cleanup_accepted_workspace`，沿用正常的受保护 plan/apply 并确认确切路径；这次五项实际 apply 的命令号均为既有 `accepted-cleanup-…-apply`，已按仅本批 run 的只读持久记录核对。Host 在同时发起 W1 回收时收到 WORKSPACE_CHANGED（采集时未跟踪文件消失），重放旧修订收到 REVISION_CONFLICT；A-S 的同时计划请求也收到 REVISION_CONFLICT。没有把这些失败回复记成成功，也没有手工删除或恢复；重新读取五项持久计划、实际路径与保留产物后确认回收已完成。异常回复及实际执行主体均保留在 ignored 原始记录和最终核对 JSON。

最终验收记录变更后，仅重跑 `cli.test_repository_hygiene`，5 项测试通过、退出码 0；在暂存完整记录（含新增 JSON）后再以同一私有环境核对该项，未重复完整检查。上述历史检查后的源码、构建产物与当时的检查提交相同。本批的标准机制、影响范围、测试编号变化、逐项失败注入、服务端字节/耗时、真实浏览器事实、对外变化和未验证边界均已列出；完成后停止，等待 Claude Code Host 验收，不开始其他工作。

## R13：补充指令后的 core 整合计划

2026-10-08 收到 Claude Code Host 补充：提交前合入最新 `socu/buddy-core`。确认本分支干净且 core 为 `fbfa8eac881ee7da8d23465df155d270b8707b60`，用原生 `git merge --no-ff --no-commit socu/buddy-core` 合入；没有冲突，没有手工修改 ADR-025 实现。两侧唯一共同改动的产品文件为 `service.py`：保留本批目录重读入场与账户绑定逻辑，加入 core 的 live_registry 初始化、C-Two Worker 通道工厂及 attach/detach 操作。workspace、Worker 提醒与控制台源码均没有 core 新增差异，契约随 core 为 0.29.0，数据库 schema 仍为 15。

R13 验证合并实际字节和本批相对 core 的写入边界，复用现有 checks 的私有子进程环境、默认并行调度与回收，只跑受影响的服务初始化/目录消费者、HTTP 条件读取、两跳 C-Two 服务消费者、共享 stash claim/封存、Worker 提醒/角色消费者与控制台预览，不执行完整入口。R14 在这些结果稳定、整合提交固定且 worktree 干净后执行一次 `uv run --frozen python -m hey_my_buddy.cli.checks`，不设置并行数；此为合入后最终代码的完整检查，R12 的历史结果保留而不充作替代。新完整检查改变验证结论，所有状态与日志按实际结果填写；源码与资产不变的浏览器/字节测量仍绑定原来已验证的固定源码，不宣称重做了真实原生模型核对。

R13 实际结果：13 个受影响模块、159 项测试、跳过 0，默认 4 个测试子进程，14.873 秒、退出码 0，私有根回收已确认。含真实服务注册、holding Worker 与 controller 的两跳 C-Two 夹具，只有模拟原生日志，没有模型调用。core 其余 139 个变动路径与该固定提交逐字节相同；service 的自动整合差异仅为上述三处 core 新增，workspace、Worker 提醒、控制台源码和四个现有构建产物未变化。相对 core 核对本批独立写入范围，受保护文档与实时通道的额外差异为零。

为免将 core 自己新增的测试算成本批贡献，重新从固定 `fbfa8eac` 的 Git archive 和整合工作树收集 Python 编号：core 3,049 项、整合 3,162 项，新增 113、删除 0，新编号集合与合入前本批的 113 项完全相同；两次 discovery 无错误，只收集未执行。测试编号 JSON 的主基线改为固定 core，另保留合入前历史清单；前端源码未被 core 改动，667→728 的编号变化仍有效，没有重复其源码未变化的浏览器测试或性能测量。合入后真实原生程序和已安装日常运行时未验证，本次整合只使用私有测试与模拟原生夹具。

## R14：合入 core 后的最终完整检查

最终代码为整合提交 `5c9c9fcc119ca74e781a9daeea84d30fc1db03a5`（`merge: integrate buddy-core C-Two channels with console and checkout changes`），两父提交为原本批记录 `5884d76b` 与最新 core `fbfa8eac881ee7da8d23465df155d270b8707b60`。先核对 worktree 干净和 core 为祖先，再执行一次 `uv run --frozen python -m hey_my_buddy.cli.checks`：UTC 2026-10-08 01:25:10→01:33:27（Asia/Singapore 09:25→09:33），497.041 秒、退出码 **0**；默认 4 个 Worker，195/195 个文件、3,162 项测试、跳过 1 项，测试私有根回收完成。命令启动前清除全部继承 BUDDY_*、ANTHROPIC_*、VIRTUAL_ENV 与 UV_PROJECT_ENVIRONMENT，不设置并行覆盖值。原始日志为 ignored `tmp/console-reads-host/post-core-full-check.log`，完整元数据在最终核对 JSON 的 fullCheck，旧结果留在 preCoreFullCheck。

本批实际有补充指令到达前旧候选的一次完整检查，以及合入 core 后最终代码的一次完整检查；两个提交号与日志分别保留。新结果替代旧候选的最终验证结论，不称旧结果已覆盖 0.29.0。本次合并之后没有产品修复、没有额外运行完整入口；测试和最终检查都没有模型调用，没有安装或升级日常运行时，也没有修改登录、凭据或 schema。

检查结束后 Host 再确认 HEAD 为上述整合提交、worktree 干净、当前最新 core 仍为 fbfa8eac 且为其祖先；4 个控制台构建产物同时与原始固定哈希和该整合提交的 Git 对象逐字节一致。相对 core 的本批范围核对通过：角色代码仍只有 C1 一句，受保护文档、harnesses、实时协议与 live_registry 的本批额外差异为零。既有浏览器和字节测量证据对应的控制台源码与产物未变化，合入后的服务读取、注册和实际两跳消费者另由 R13 与本次完整检查覆盖；没有宣称再次使用真实 CLI 或日常运行时验证。

合入后完整检查通过之后只补记录；暂存全部最终记录后重跑 `cli.test_repository_hygiene`，5 项通过、退出码 0，没有再次执行完整检查。记录以本地提交交付，源码仍与已检查的 5c9c9fc 相同，保留本分支和 Host 工作树、不推送，停止等待 Claude Code Host 验收。五个微任务已 accepted、各自检出已按精确路径回收，历史产物与整合记录继续保留，不创建替代微任务或改写其执行事实。

## R15：Claude Code Host 退回后的测试修正计划

2026-10-08，Claude Code Host 拒绝 `0011980b`：完整前端 728 项中两项 objective-api 仍断言旧 timeline 形状；access_revision 从快照标记移除没有失败证据。确认原 A-F `890cc404-0b42-47f2-b0f5-642fe5f69645` 与 A-S `b9955292-15e2-45e6-98d9-e367109fc281` 已 accepted 且检出回收，不能 continue。按本次授权选择新建一个关联两条原 run 的测试修正微任务，沿用 Host，首次四个 buddy 字段全部省略，经路由；不直接更改 Worker 的测试产物。

修正写入范围仅 `apps/console/src/objective-api.test.ts` 与 `tests/python/console/test_console_reads_http.py`：修正两条成功断言为 `{timeline, verifiedAtMs}`，保留非法响应拒绝与路径/信号断言；补同一已缓存有效会话中访问修订变化的实际 HTTP 回归，冻结时间、数据库与其他失效来源，使去掉 access_revision 的隔离内存变异真的返回旧 304 而使新测试失败。明确区分受控修订事实注入与真实切换登录模式，不能再次由会话更换或 requireLogin 变化掩盖目标。Worker 只跑这两处受影响测试，不跑前端或 Python 的完整检查；日志置于新建 ignored 任务目录，保留确切路径，不删除、不用 stash、不创建或切换分支、公开记录用 ~ 或占位符。

R15 由 Host 独立核对固定产物、受影响测试，以及恢复两个旧前端断言和移除后端 access_revision 的失败证据；代码/测试缺陷退回该新 run。R16 在固定整合提交上运行一次完整 `npm --prefix apps/console test`，记录命令、提交号、项数和实际退出码；只更改测试与记录，按用户指令不重复 Python 完整检查，只跑仓库卫生。旧 Python 完整检查的 3,162 项通过与之前缺失完整前端检查分别保留，不把 Python 命令说成包含前端。控制台产品源码、构建产物与对外行为无需修改；最后验收并核对自动受保护回收，停止等 Claude Code Host 验收，不合入新的 core 文档变化、不推送。

首次试图复用原宏任务 `obj-e7f5f4a6-85a5-4338-a8af-8c396266b34d` 的 submit 被 CONFLICT 拒绝，没有 runId；只读核对原宏任务 Host 仍为 codex-adr027，但其 project_id 与当前 Git 公共目录的仓库身份不同。没有修改旧宏任务/黑板记录或跳过归属检查；为这次修正创建新的宏任务，并在 task 说明与记录中关联两条原 run。原失败请求与响应保留。Host 已在原源码上复现 objective-api 的 9 项中 2 fail / 7 pass，退出码 1，原始日志 ignored `tmp/console-reads-host/repair-objective-api-before.log`；未重复原已通过的全量核对。
