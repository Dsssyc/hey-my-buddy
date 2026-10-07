# 控制台读取与受管检出：执行计划与验收记录

2026-10-07，Codex Host `codex-adr027`。从 `socu/buddy-core` 的 `54d159082c3ec2bb995c897c0833da216876485c` 建立 `socu/console-reads-and-checkouts`，Host 工作树为 `~/.codex/worktrees/console-reads-and-checkouts/hey-my-buddy`，不推送。来源是待办中每 3 秒读取成本（含 2026-10-07 复测）、每文件启动 git、共享 stash 的前两项、附着分支时的回收诊断，以及 ADR-027 留下的前三项。当前仅为计划，检查与验收结果会按固定产物和实际执行补充。

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
