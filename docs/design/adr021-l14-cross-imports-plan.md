# ADR-021 L14：两侧互相引用的搬动方案

本文依据 `dcdff0274cda225bd41532cc5c656dd3c7df2cce` 的源码、[ADR-025 第 12、13 条](../decisions/025-harness-run-module.md)、[架构](../reference/architecture.md)、[ADR-007](../decisions/007-neutral-core-and-single-current-contract.md)与[改名计划第 11 行](adr021-l14-rename-plan.md)，供 Host 审查。只做静态阅读与 AST 清点，没有运行源码、测试、原生 harness 或模型，也没有改动源码、测试或旧登记表；本文没有实现或验收效力。后续各行合入之后，必须在当时的提交上重数，不能把本文的行号当成后续提交的事实。

现在有 50 处跨侧导入：`blackboard → buddy` 36、`buddy → blackboard` 10、`protocol → blackboard` 3、`protocol → buddy` 1，其中 1 处只在 `TYPE_CHECKING` 下发生。按不同的「目标模块＋导入名字」计，共 36 个目标：A 共用纯数据或纯函数 15 个，B 被引名字本身应归引用侧 0 个，C 多余导出 1 个，D 调用另一侧行为 20 个。D 包括读取时间、文件、上下文与传输等可共享的基础行为，不能把这些说成纯函数；其中 10 个目标可以在下面的搬动中消除，另有 10 个目标留下 21 处导入。建议 6 批，消除 29 处；执行器、原生探测与受管工作区操作不能靠这 6 批清零，理由与最小改法列在后面。开发者若坚持第 11 行最终为零，还需要另定这些行为的归属与执行流程；不能把 21 处改藏进根包后宣称完成。

## 现在的清单

一处指一条跨侧 `Import` 或 `ImportFrom`，同一语句引多个名字仍算一处；不同函数重复导入分别计数。解析 `src/hey_my_buddy/**/*.py`，把相对导入还原为绝对模块，检查两侧彼此的导入以及 `protocol` 导入任一侧；`TYPE_CHECKING`、函数内导入与所有条件分支都计入。另查了 `import_module`、`__import__`、模块路径字符串与进程启动字符串，没有发现这四个方向额外的动态导入或跨侧 `-m` 启动；说明文字中的模块地址不计入。模块对象的导入保留原语句形态，例如 `from blackboard.routing import router`，对旧表比较时再展开为 `blackboard.routing.router`。

下面源码文件省略 `src/hey_my_buddy/`，目标模块省略 `hey_my_buddy.`。T 为顶层，L 为函数内延迟导入；L 后写所在函数，嵌套函数用点分隔。N 编号对应下一节，同一个目标反复出现不重复归类。

| 编号 | 文件与行 | 目标模块 | 导入名字与归类编号 | 位置 |
| --- | --- | --- | --- | --- |
| I01 | `blackboard/catalog/catalog.py:382` | `buddy.harnesses.registry` | `adapters`（N01） | L：discovery_available |
| I02 | `blackboard/catalog/catalog.py:390` | `buddy.harnesses.registry` | `adapters`（N01） | L：discover |
| I03 | `blackboard/catalog/catalog.py:395` | `buddy.harnesses.runtime_selection` | `selected`（N04）、`bound`（N05） | L：discover |
| I04 | `blackboard/catalog/catalog.py:454` | `buddy.harnesses.registry` | `adapter`（N02） | L：validate_buddy |
| I05 | `blackboard/catalog/catalog.py:542` | `buddy.harnesses.registry` | `worker_permission_capabilities`（N03） | L：permission_capabilities |
| I06 | `blackboard/catalog/catalog.py:577` | `buddy.harnesses.registry` | `adapters`（N01） | L：proposed_buddies |
| I07 | `blackboard/catalog/catalog_store.py:122` | `buddy.harnesses.runtime_selection` | `bound`（N05） | L：record |
| I08 | `blackboard/evaluation/evaluation.py:137` | `buddy.harnesses.registry` | `adapters`（N01） | L：_buddy_view |
| I09 | `blackboard/routing/decision.py:134` | `buddy.roles.router` | `DecisionAdapter`（N06） | L：_adapter_available |
| I10 | `blackboard/routing/decision.py:136` | `buddy.harnesses.runtime_selection` | `bound`（N05） | L：_adapter_available |
| I11 | `blackboard/routing/router.py:147` | `buddy.harnesses.registry` | `adapter`（N02） | L：buddy_problem |
| I12 | `blackboard/routing/router.py:149` | `buddy.harnesses.runtime_selection` | `bound`（N05） | L：buddy_problem |
| I13 | `blackboard/service/daemon.py:36` | `buddy.runtime.worker` | `RETIRE_REQUEST_NAME`（N07）、`ReceiptSpool`（N08） | T |
| I14 | `blackboard/service/harness_health.py:25` | `buddy.harnesses.discovery` | `candidate_snapshot`（N09） | L：_snapshot |
| I15 | `blackboard/service/harness_health.py:30` | `buddy.harnesses.discovery` | `discover`（N10） | L：_discover |
| I16 | `blackboard/service/harness_health.py:41` | `buddy.harnesses.registry` | `adapter as executor`（N02） | L：read_health |
| I17 | `blackboard/service/harness_health.py:90` | `buddy.harnesses.discovery` | `has_candidate`（N11） | L：add_path_hint |
| I18 | `blackboard/service/harness_health.py:186` | `buddy.harnesses.discovery` | `location_fingerprint`（N12） | L：refresh |
| I19 | `blackboard/service/harness_health.py:340` | `buddy.harnesses.codex.protocol` | `CodexProtocolError`（N13） | L：_codex_account_read |
| I20 | `blackboard/service/harness_health.py:343` | `buddy.harnesses.codex.account_probe` | `read`（N14） | L：_codex_account_read |
| I21 | `blackboard/service/live_registry.py:49` | `buddy.harnesses.live` | `LiveChannel`（N15） | T，TYPE_CHECKING |
| I22 | `blackboard/service/service.py:23` | `buddy.harnesses.registry` | `capability_report`（N16） | T |
| I23 | `blackboard/service/service.py:168` | `buddy.harnesses.runtime_selection` | `bound`（N05） | L：_guard |
| I24 | `blackboard/service/service.py:272` | `buddy.harnesses.c_two_live` | `CTwoLiveChannel`（N17） | L：__init__ |
| I25 | `blackboard/service/service.py:342` | `buddy.harnesses.registry` | `local_capabilities`（N18） | L：capabilities.handler |
| I26 | `blackboard/service/service.py:344` | `buddy.harnesses.runtime_selection` | `bound`（N05） | L：capabilities.handler |
| I27 | `blackboard/service/service.py:491` | `buddy.harnesses.registry` | `adapter`（N02） | L：_refresh_harness_catalog |
| I28 | `blackboard/service/service.py:492` | `buddy.harnesses.runtime_selection` | `bound`（N05） | L：_refresh_harness_catalog |
| I29 | `blackboard/service/service.py:645` | `buddy.harnesses.runtime_selection` | `bound`（N05） | L：model_catalog_refresh.handler |
| I30 | `blackboard/tasks/inquiry.py:21` | `buddy.harnesses.live` | `MAX_OBSERVE_LIMIT`（N19）、`InquiryPayload`（N20）、`LiveRequest`（N21） | T |
| I31 | `blackboard/tasks/inquiry.py:53` | `buddy.harnesses.registry` | `adapters`（N01） | L：adapter_capabilities |
| I32 | `blackboard/tasks/inquiry.py:171` | `buddy.harnesses.registry` | `live_binding`（N22） | L：observe |
| I33 | `blackboard/tasks/workflow.py:1202` | `buddy.harnesses.registry` | `require_permission`（N23） | L：_require_permission |
| I34 | `blackboard/tasks/workflow.py:1207` | `buddy.harnesses.registry` | `adapter`（N02） | L：_execution_adapter |
| I35 | `blackboard/tasks/workflow.py:5756` | `buddy.roles.turn_io` | `checkpoint_resumable`（N24） | L：begin_turn |
| I36 | `blackboard/tasks/workflow.py:5895` | `buddy.roles.turn_io` | `validated_checkpoint`（N25） | L：_previous_codex_checkpoint |
| I37 | `buddy/harnesses/codex/account_probe.py:12` | `blackboard.store.db` | `utc_now`（N26） | T |
| I38 | `buddy/roles/router.py:10` | `blackboard.routing` | `router`（N27） | T |
| I39 | `buddy/roles/router_input.py:11` | `blackboard.tasks` | `workspace`（N28） | T |
| I40 | `buddy/roles/turn_io.py:154` | `blackboard.tasks.workflow` | `workspace_module`（N29） | L：verify_workspace |
| I41 | `buddy/roles/turn_io.py:282` | `blackboard.tasks.workflow` | `workspace_module`（N29） | L：seal_workspace |
| I42 | `buddy/runtime/partial_outputs.py:18` | `blackboard.tasks.workflow` | `workspace_module`（N29） | L：capture |
| I43 | `buddy/runtime/partial_outputs.py:43` | `blackboard.tasks.workflow` | `workspace_module`（N29） | L：enrich |
| I44 | `buddy/runtime/worker.py:22` | `blackboard.store.db` | `TERMINATION_REASONS`（N30） | T |
| I45 | `buddy/runtime/worker.py:612` | `blackboard.service.harness_health` | `HARNESSES`（N31） | L：_execute_guarded |
| I46 | `buddy/runtime/worker.py:665` | `blackboard.routing.router` | `routing_facts`（N32） | L：_review_unavailable |
| I47 | `protocol/billing.py:47` | `blackboard.service.harness_health` | `read_health`（N33） | L：for_provider |
| I48 | `protocol/examples/external_worker.py:25` | `buddy.runtime.worker` | `fsync_json`（N34） | T |
| I49 | `protocol/schemas.py:16` | `blackboard.store.db` | `canonical_json`（N35）、`sha256_text`（N36） | T |
| I50 | `protocol/tool_evidence.py:19` | `blackboard.store.db` | `canonical_json`（N35） | T |

与旧表按「源文件＋展开后的目标模块」的出现次数比较，45 处保留、11 处消失、5 处新增，`56 - 11 + 5 = 50`。只改行号或把 `from package.module import name` 改写为 `from package import module` 不算新增；旧表没有记录名字与调用函数，因而不能从旧表证明同一对文件间每个名字都原样保留。

| 变化 | 旧表中的源文件与目标模块，或新增的当前位置 | 处数 |
| --- | --- | --- |
| 消失 | `blackboard/catalog/account_operations.py → buddy.harnesses.account_native`、`buddy.harnesses.base`、`buddy.harnesses.runtime_selection` | 3 |
| 消失 | `blackboard/catalog/accounts.py → buddy.harnesses.account_integrations`、`buddy.harnesses.runtime_selection` | 2 |
| 消失 | `blackboard/service/harness_health.py → buddy.harnesses.runtime_selection`（旧行 194） | 1 |
| 消失 | `buddy/harnesses/account_integrations.py → blackboard.catalog.accounts`、`blackboard.catalog.account_keystore` | 2 |
| 消失 | `buddy/harnesses/account_native.py → blackboard.store.db` | 1 |
| 消失 | `buddy/roles/structured_call.py → blackboard.catalog.accounts` | 1 |
| 消失 | `buddy/runtime/worker.py → blackboard.catalog.accounts`（旧行 696） | 1 |
| 新增 | `blackboard/catalog/catalog.py:577 → buddy.harnesses.registry.adapters`，`CatalogView.proposed_buddies` | 1 |
| 新增 | `blackboard/service/harness_health.py:90 → buddy.harnesses.discovery.has_candidate`、`:186 → location_fingerprint` | 2 |
| 新增 | `blackboard/service/harness_health.py:340 → buddy.harnesses.codex.protocol.CodexProtocolError` | 1 |
| 新增 | `blackboard/tasks/workflow.py:1207 → buddy.harnesses.registry.adapter`，`_execution_adapter` | 1 |

## 每个被引名字的归类

「读」表示定义、依赖与当前调用点直接支持该事实；「推」表示由这些事实得出的搬动或取舍建议。下表的类别描述当前导入目标，不把调用者错误归位误写成被引名字错误归位：N33 的 `read_health` 本来就应在 blackboard，错放的是调用它的 `protocol/billing.py.for_provider`。因此 B 的目标数为 0，但第 1 批确实有一个调用者归位。N27、N28 是模块对象；它们实际取用的成员也逐个列出。N29 的 `workspace_module()` 返回的仍是 N28 的模块，没有独立的工作区实现。

| 编号 | 被引模块与名字 | 类别 | 依据与去向 |
| --- | --- | --- | --- |
| N01 | `buddy.harnesses.registry.adapters` | D | 读：构造 BUILT_IN 的六类实例；blackboard 既用其声明也调用 available/discover_models/local_read_only_check。推：不能整个搬，实际执行与原生探测留 buddy，详见余项。 |
| N02 | `buddy.harnesses.registry.adapter` | D | 读：由 adapters 构造并取执行器；调用点还读取续接声明或 validate_turn_provenance。推：不可把 factory 当纯查表搬进 protocol，详见余项。 |
| N03 | `buddy.harnesses.registry.worker_permission_capabilities` | A | 读：只从 _WORKER_PERMISSIONS 生成 permission:* 元组，无探测、DB 或进程。推：连同实际声明与 WorkerPermission 搬到 protocol/harness_permissions.py。 |
| N04 | `buddy.harnesses.runtime_selection.selected` | D | 读：先读 ContextVar，缺席时读 RECORD_FILE 的 JSON；catalog.discover 在模型目录读取前调。推：现有基础选择实现搬根包 harness_selection.py，command_for 的 discover 回退不搬。 |
| N05 | `buddy.harnesses.runtime_selection.bound` | D | 读：上下文管理器设置两份 ContextVar，finally 恢复；服务在请求、资格判断与目录发布时调。推：实际 ContextVar 与函数一起搬根包，不能每侧各定义一次。 |
| N06 | `buddy.roles.router.DecisionAdapter` | D | 读：服务只调 available；Worker 调 start/collect/cancel 驱动 Router。推：不能把含进程执行的整类搬给服务，资格循环的最小改法见余项。 |
| N07 | `buddy.runtime.worker.RETIRE_REQUEST_NAME` | A | 读：固定字符串 retire.request；daemon 发退休意图，Worker/supervisor 在执行边界读。推：协议值搬 protocol/worker_receipts.py。 |
| N08 | `buddy.runtime.worker.ReceiptSpool` | D | 读：Worker 与 daemon 共用启动意图/回执的实际文件读写，pending 还建目录，没有 DB 或 child handle。推：完整类搬 protocol/worker_receipts.py，持久写函数搬根包。 |
| N09 | `buddy.harnesses.discovery.candidate_snapshot` | D | 读：扫候选、指定版本管理器记录、注册表与 Node 位置；服务刷新前调，不启动进程。推：连同已有依赖闭包搬根包 harness_locations.py。 |
| N10 | `buddy.harnesses.discovery.discover` | D | 读：服务健康刷新同步调；函数沿 _discover/_candidate_handshake 启动 version/login 探测并确认停止。推：不可当基础扫描搬根包，运行流程改法见余项。 |
| N11 | `buddy.harnesses.discovery.has_candidate` | D | 读：服务接收 PATH hint 时调，以文件指纹过滤目录，无进程。推：实际文件扫描闭包与 candidate_snapshot 一起搬根包。 |
| N12 | `buddy.harnesses.discovery.location_fingerprint` | A | 读：只投影输入快照并经 _scan_hash 散列，不读取当前位置。推：与其使用的候选位置格式同批搬根包 harness_locations.py。 |
| N13 | `buddy.harnesses.codex.protocol.CodexProtocolError` | A | 读：只保存 code/message 的异常类，service 与原生协议同时捕获它。推：原类搬已有 errors.py，不搬 Connection。 |
| N14 | `buddy.harnesses.codex.account_probe.read` | D | 读：service 的账户扫描启动 Codex app-server，读 account/config/rateLimits 后确认停止。推：服务仍判定记录资格，子进程应归 buddy；当前同步流程不能只改路径，详见余项。 |
| N15 | `buddy.harnesses.live.LiveChannel` | A | 读：现有 typing.Protocol 只描述 identity、capabilities、request、observe、close；live_registry 仅在 TYPE_CHECKING 导入。推：随实际线格式搬 protocol/live.py，不另建接口。 |
| N16 | `buddy.harnesses.registry.capability_report` | D | 读：Service.capabilities 触发 available 与 local_read_only_check，未绑定时可能原生读取；不是只序列化数据。推：汇总与真实探测职责需拆开，整函数搬走仍跨侧，详见余项。 |
| N17 | `buddy.harnesses.c_two_live.CTwoLiveChannel` | D | 读：Service 初始化以此连接持有执行的 Worker；真实 C-Two connect/call 带 deadline/token，不启动 harness。推：已有传输整体搬 protocol/c_two_live.py。 |
| N18 | `buddy.harnesses.registry.local_capabilities` | D | 读：Service.capabilities 调 available，再汇总可执行 capability，external 被排除。推：不能改成所有源码声明的并集，那会把不可用也宣布可用；详见余项。 |
| N19 | `buddy.harnesses.live.MAX_OBSERVE_LIMIT` | A | 读：固定页上限 256，inquiry 的 observe 使用；现有帧上限仍独立生效。推：随 live 格式搬 protocol/live.py，不改数值。 |
| N20 | `buddy.harnesses.live.InquiryPayload` | A | 读：问题 ID 与有界问题文本的 InternalModel，服务创建、控制器解析。推：搬 protocol/live.py，原字节限额不变。 |
| N21 | `buddy.harnesses.live.LiveRequest` | A | 读：身份、请求 ID、kind、InquiryPayload 的 InternalModel，两侧同一格式。推：搬 protocol/live.py，RunIdentity 直接取已有 protocol 定义。 |
| N22 | `buddy.harnesses.registry.live_binding` | D | 读：查 RUN_SEAMS 后返回实际 CTwoLiveChannel 类；inquiry 只看是否为 None，角色侧真的构造该类。推：服务不能把登记存在偷换成 harness 名字存在，不另造转发，详见余项。 |
| N23 | `buddy.harnesses.registry.require_permission` | D | 读：workflow 入场校验调用，现有实现为了读 permission:* 构造所有 adapters，不检查原生 available。推：保留函数与分支，改读同一 _WORKER_PERMISSIONS 推导值并搬 protocol/harness_permissions.py，当前四个构造器的权限追加与此等价。 |
| N24 | `buddy.roles.turn_io.checkpoint_resumable` | A | 读：workflow.begin_turn 读取已验证 checkpoint、nativeExitCode 与 bindingSaved，只计算布尔值。推：搬 protocol/turn_checkpoint.py，保留 completed、shutdown/退出证据条件。 |
| N25 | `buddy.roles.turn_io.validated_checkpoint` | A | 读：workflow._previous_codex_checkpoint 校验被冻结输入、原生会话/回合、停止事实、消息散列与边界，无进程/DB。推：与实际依赖 input_hash 一起搬 protocol/turn_checkpoint.py。 |
| N26 | `blackboard.store.db.utc_now` | D | 读：account_probe 在原生元数据读回时调用数据库模块的时间函数；函数只读时钟，输出固定毫秒 UTC，但不是数学纯函数。推：已有时间函数搬根包 timestamps.py，db 和探测都直接取定义。 |
| N27 | `blackboard.routing.router` | A | 读：buddy/roles/router.py 只取 render_prompt、routing_facts、validate_answer，均只处理传入文档；同模块其他函数拿 DB。推：只搬这三函数与其常量到 protocol/router.py，不搬模块对象或 DB 部分。 |
| N28 | `blackboard.tasks.workspace` | D | 读：router_input.prepare/verify 在启动前与停止后调 workspace.verify，操作实际 Git 与文件身份。推：没有仅数据的替代品，不能整个搬服务的工作区生命周期，详见余项。 |
| N29 | `blackboard.tasks.workflow.workspace_module` | D | 读：getter 返回 blackboard.tasks.workspace，turn_io 与 partial_outputs 调 verify、seal、cumulative_patch；getter 也有缺失模块的错误分支。推：去掉顺手转出仍依赖三项真实行为，不能归为无读者删除；最小拆取范围见余项。 |
| N30 | `blackboard.store.db.TERMINATION_REASONS` | A | 读：固定五项词汇，Worker 的 tuple 解包顺序与 store 的合法值检查共用。推：原元组搬 protocol/worker_receipts.py，保留次序和原拼写。 |
| N31 | `blackboard.service.harness_health.HARNESSES` | A | 读：Worker 只做成员判断；服务把它用于扫描先后，已有 protocol.HARNESS_NAMES 是同一集合但顺序不同。推：Worker 直接用现有 HARNESS_NAMES，服务扫描顺序留服务，不造兼容别名。 |
| N32 | `blackboard.routing.router.routing_facts` | A | 读：Worker 在 review 启动不可用时为回执投影 Router 字段；纯字典投影，可与 N27 的同名实际函数共用。推：实际定义仅搬一次到 protocol/router.py。 |
| N33 | `blackboard.service.harness_health.read_health` | D | 读：protocol.billing.for_provider 收到 connection 后调它查询服务健康、账户、目录与配额。推：read_health 留 blackboard，错放的调用者 for_provider 整体归 blackboard/service/harness_health.py，不把 SQL 搬进 protocol。 |
| N34 | `buddy.runtime.worker.fsync_json` | D | 读：external_worker 示例在 claim 之前调，写启动意图；Worker/supervisor 也用其实际持久发布。推：原函数体搬根包 durable_json.py，不能被只转发的薄模块或行为不同的 private_json 代替。 |
| N35 | `blackboard.store.db.canonical_json` | C | 读：db.py:20 从已有根包 json_codec 引入它，protocol 的两个调用者借 db 重新导出；不是 db 定义，也不是未使用。推：两调用者直取根包，清掉旧导出路由，保留 codec 实现。 |
| N36 | `blackboard.store.db.sha256_text` | A | 读：对传入 UTF-8 文本计算 SHA-256，schemas 与 blackboard 共用，无 DB 行为。推：原函数搬已有根包 json_codec.py，所有引用直取定义。 |

## 建议的 6 批搬动

以下新路径承接原函数、类或数据的实际定义，不是转发文件，不增加基类、接口、注册表、注入参数或第二条运行载体。所有引用一次改完，删除旧定义、旧导出与兼容入口。每批的源码引用文件与测试引用文件列在本节末尾；列表包含定义文件、限定属性访问、mock 路径与夹具中嵌入的模块路径，测试只规划后续改路径，本次不修改或运行它们。

第 1 批，基础名字与调用者归位，风险最低。`blackboard/store/db.py.sha256_text` 的函数体搬入已有 `json_codec.py`，`utc_now` 的原函数体搬入根包 `timestamps.py`，`TERMINATION_REASONS` 的原五项元组搬入 `protocol/worker_receipts.py`；`db.py` 自己与所有调用者直接用新定义，`canonical_json` 全部直接取已有 `json_codec.canonical_json`，清掉从 `db.py` 取得它的导出路线。`protocol/billing.py.for_provider` 整个函数搬入 `blackboard/service/harness_health.py`，去掉函数内对同模块 `read_health` 的导入，`fact` 仍取 `protocol.billing`。Worker 的 `_execute_guarded` 只把 HARNESSES 用作成员判断，直接用已有 `protocol/harness_names.py.HARNESS_NAMES`；`harness_health.HARNESSES` 留作服务的扫描顺序，不做别名，也不改变它的顺序。`discovery.py.HARNESSES` 在本提交无读者，删掉。预期消除 I37、I44、I45、I47、I49、I50，共 6 处，剩 44 处。散列、时间精度、五项终止词汇、账单回退与扫描顺序均不改变；`utc_now` 仍读取时钟，不称为纯函数。不要把已有 `HARNESS_NAMES` 的顺序改成扫描顺序，那会顺带改变账户和目录返回数组的次序。

第 2 批，共用判定与格式。`blackboard/routing/router.py` 的 `render_prompt`、`validate_answer`、`routing_facts` 与它们用到的 `INSTRUCTIONS`、`FAST_INSTRUCTIONS`、`MAX_REASON`、`MAX_REFERENCES` 搬入 `protocol/router.py`；配置、预算、候选读取、`RouterResolution` 与所有接收 connection 的函数留在 blackboard。`buddy/roles/turn_io.py` 的 `input_hash`、`validated_checkpoint`、`checkpoint_resumable` 搬入 `protocol/turn_checkpoint.py`，保留输入散列、原生身份、停止证据、消息字节与 eventSeq 的全部条件；`MAX_ASSISTANT_MESSAGE_BYTES` 直接取已有 `protocol.usage`。`buddy/harnesses/codex/protocol.py.CodexProtocolError` 的实际类定义搬入已有 `errors.py`，保留 code 与异常类型，不把服务 catch 简化成吞掉所有异常。`registry.py` 的 `_WORKER_PERMISSIONS`、`worker_permission`、`worker_permission_capabilities`、`require_permission` 与 `run_contract.py` 的 `ToolScope`、`WorkerPermission` 搬入 `protocol/harness_permissions.py`；`require_permission` 内唯一一次读取 `adapters()[name].capabilities` 的判定改为读取搬来的 `worker_permission_capabilities(name)`，原有 command/external 分支和错误内容完整保留。读：四个 harness 构造器只在各自原 capabilities 元组后追加同一 `_WORKER_PERMISSIONS` 的 `permission:*`，因此这一步的权限判定等价，不需要新能力目录；这个等价只涵盖权限字段，不能据此替换 `available`、`local_read_only_check` 或 provenance。预期消除 I05、I19、I33、I35、I36、I38、I46，共 7 处，剩 37 处。不改变提示词、答案校验、续接资格或权限强度，搬函数时不能顺手删掉看似严格的停止条件。第 1、2 批可合为一个低风险微任务，但仍分块审查；风险来自引用范围大与权限模型的依赖一起移动。

第 3 批，候选位置与指纹的基础实现，风险高于纯数据。`buddy/harnesses/discovery.py` 的 `candidate_snapshot`、`has_candidate`、`location_fingerprint` 与静态全局依赖闭包 `file_fingerprint`、`_home`、`_record_text`、`_version_parts`、`_installed_version_dirs`、`_manager_records`、`_registry_paths`、`_common_dirs`、`_app_paths`、`_variants`、`_scan_hash`、`_NAMES`、`_VERSION_RECORD` 搬入根包 `harness_locations.py`；原 `discovery.py.native_environment`、`_node_for`、`_discover` 直接使用搬来的 helper。这个闭包只扫描位置、读取指定记录、文件元数据与 Windows 注册表、计算散列，不启动进程；`probe`、`_terminate_group`、`_probe_posix`、`_probe_windows`、`_probe_dsh_version`、`_candidate_handshake`、`discover`、`_discover` 与命令构造继续在 buddy。本批消除 I14、I17、I18，共 3 处，剩 34 处；I15 的 discover 不能跟着搬。保留候选先后、手动路径、Node 候选、指纹字段与只读范围，不把用户的配置复制到本产品的配置目录。本批单独验收三平台的位置与优先级语义；其余确定性探测代码的归属不变。第 3、4 批可在一个微任务里顺序完成，能少一次调整 runtime_selection 的 discovery 依赖，但不要并行编辑相同文件。

第 4 批，已选命令上下文。从 `buddy/harnesses/runtime_selection.py` 抽出 `_records`、`_environment`、`RECORD_FILE`、`environment_for`、`bound`、`selected` 的完整已有实现，搬入根包 `harness_selection.py`。这是真实 ContextVar 和选择文件的读法，不是转发；`command_for`、`native_environment`、`controller_environment` 留在 buddy，直接读新定义。所有 bound/selected 调用者直接改路径，文件内容与环境变量值不改。本批消除 I03、I07、I10、I12、I23、I26、I28、I29，共 8 处，剩 26 处。读：`bound` 设置并在 finally 恢复两份上下文，`selected` 在上下文缺席时会读选择文件；共享的只是已选命令事实，不把 `command_for` 的原生 discover 回退搬进根包。ContextVar 的定义必须只留一份，不能新旧文件各建一份，否则服务绑定与执行器读取会分离；不能顺手把 `environment_for` 的真假值语义改成 `is None`。本批单独一个微任务，重点核对后台线程、异常恢复与子进程选择文件。

第 5 批，持久回执的共同实现。`buddy/runtime/worker.py.fsync_json` 的原函数体搬到根包 `durable_json.py`，`ReceiptSpool` 的完整类搬到第 1 批的 `protocol/worker_receipts.py`；`RETIRE_REQUEST_NAME` 同时从 Worker 搬到该文件，Worker、supervisor、daemon 与外部 Worker 示例直接使用。`buddy/runtime/api.py` 不再重新导出 `ReceiptSpool`、`fsync_json`，调用者改取实际定义；它对 Worker/Supervisor 的现有用途不扩展。本批消除 I13、I48，共 2 处，剩 24 处。读：daemon 的 `_local_evidence` 调 `pending` 与 `read_startup`，Worker 在认领前落启动意图、结束后落回执；两者共享的是持久传输证据格式与读写实现，不是 child handle 或 SQLite 权威。保持文件名、nonce/attempt 关联、落盘顺序、0600、fsync 与确认后删除规则；`pending()` 现有的建目录副作用也原样保留。不能用 `turn_io.private_json` 代替，两个函数对链接检查、目录创建与发布方式不同。本批单独验收持久性与回放，不与高风险批一起做。

第 6 批，实时格式与真实 C-Two 传输。把 `buddy/harnesses/live.py` 整个已有实现搬到 `protocol/live.py`，把 `buddy/harnesses/c_two_live.py` 整个已有实现搬到 `protocol/c_two_live.py`；模块内的 `RunIdentity` 直接改取已有 `protocol/run_identity.py`，其余错误、codec、activity、rpc_config、inquiry、internal_models 已在根包或 protocol，搬后没有返回 buddy 的依赖。`live.py` 的实际定义为 `LiveCapabilities`、`InquiryPayload`、`LiveRequest`、`LiveReply`、`InquiryState`、`LiveEvent`、`LiveObservation`、`InquiryJournalRejection`、`LiveJournal`、`LiveSnapshot`、`LiveChannel`、`_bounded_bytes`、`_bounded_word`、`_record_facts`、`_journal_states`，以及原有类型别名、限额、状态映射、`EXISTING_CAPABILITIES`；`c_two_live.py` 的全部类、函数、线格式与限额一并移动，具体名字见下面的定义清单。本批消除 I21、I24、I30，共 3 处，剩 21 处。读：这些实现只做跨进程格式、认证、传输、队列及端点清理，不启动或驱动 harness，也不拿 connection；角色侧仍控制真实请求的处理与 settle，服务仍只连接持有执行的 Worker，绝不跳过 Worker 直连控制器。保持同一截止时间、身份/token、无响应分类、confirmed-gone 清理前提与帧大小。它是现有传输的搬迁，不能把 callback 或执行器塞进 protocol。本批单独一个微任务；不能因模块名改变就删掉状态边界与 deadline 的现有验证。

`c_two_live.py` 需要移动的定义逐个是 `_PendingRequest`、`random_person_name`、`_secret`、`_timeout_ms`、`LiveWireRequest`、`LiveWireObserve`、`LiveWireQuery`、`LiveEndpointDescriptor`、`ConfirmedProcessGone`、`CleanupOutcome`、`_carried_facts`、`_request_digest`、`_question_reply`、`_selected_fields`、`_observe_arguments`、`write_ready_material`、`cleanup_owned_endpoint`、`CTwoLiveEndpoint`、`authenticate_live_frame`、`decode_live_wire_frame`、`_frame_fits`、`_oversized_snapshot_reply`、`CTwoLiveChannel`、`_decode_reply`，以及 `MAX_PENDING_LIVE_REQUESTS`、`_HEX64`、`_PERSON_NAMES`、`_IDENTITY_COMPONENTS`、`_REPLY_STATE_STATUSES`、`LiveField`、`TransportWindowMs`、`CleanupOutcomeValue`。`__all__` 跟着真实定义走，原文件删除。

以下路径清单按本提交的静态导入、限定属性和夹具字符串核对，源码路径仍省略 `src/hey_my_buddy/`；测试路径完整写出。新定义文件包含在源码清单里，移动之后删除的旧文件也列出，便于核对整个微任务的范围。阶段重数必须在真实新提交上进行，不能按列表的文件数替代验收。

第 1 批源码文件：`blackboard/catalog/catalog_store.py`、`blackboard/evaluation/evaluation.py`、`blackboard/evaluation/model_facts.py`、`blackboard/evaluation/native_observations.py`、`blackboard/routing/decision.py`、`blackboard/routing/quota_routing.py`、`blackboard/routing/router.py`、`blackboard/routing/router_history.py`、`blackboard/routing/router_sequence.py`、`blackboard/service/daemon.py`、`blackboard/service/harness_health.py`、`blackboard/service/live_registry.py`、`blackboard/service/service.py`、`blackboard/store/backup.py`、`blackboard/store/db.py`、`blackboard/store/migrations.py`、`blackboard/store/store.py`、`blackboard/tasks/storage.py`、`blackboard/tasks/workflow.py`、`buddy/harnesses/codex/account_probe.py`、`buddy/harnesses/discovery.py`、`buddy/runtime/worker.py`、`install/upgrade.py`、`json_codec.py`、`protocol/billing.py`、`protocol/schemas.py`、`protocol/tool_evidence.py`、`protocol/worker_receipts.py`、`timestamps.py`。

第 1 批测试、夹具与其他引用文件：`tests/probes/router_fast.py`、`tests/probes/router_readonly_tools.py`、`tests/python/blackboard/routing/test_current_router.py`、`tests/python/blackboard/routing/test_permission_route.py`、`tests/python/blackboard/routing/test_router_boundary_integration.py`、`tests/python/blackboard/routing/test_router_dispatch.py`、`tests/python/blackboard/routing/test_router_failover_integration.py`、`tests/python/blackboard/routing/test_router_list_configuration.py`、`tests/python/blackboard/routing/test_router_list_health.py`、`tests/python/blackboard/routing/test_router_sequence.py`、`tests/python/blackboard/routing/test_routing_health.py`、`tests/python/blackboard/routing/test_single_router_requests.py`、`tests/python/blackboard/service/fixtures/daemon_with_catalog.py`、`tests/python/blackboard/service/test_harness_health.py`、`tests/python/blackboard/tasks/test_dsh_resume_selection.py`、`tests/python/blackboard/tasks/test_macro_tasks.py`、`tests/python/blackboard/tasks/test_workflow_preparation.py`、`tests/python/blackboard/tasks/test_workflow_real.py`、`tests/python/blackboard/tasks/test_workflow_stash.py`、`tests/python/blackboard/tasks/test_workspace_lifecycle.py`、`tests/python/blackboard/tasks/test_workspace_stash.py`、`tests/python/buddy/runtime/test_repair_recovery.py`、`tests/python/install/test_upgrade_host_cutover.py`、`tests/python/mock_workspace.py`、`tests/python/protocol/test_billing_quota.py`。

第 2 批源码文件：`blackboard/catalog/catalog.py`、`blackboard/routing/decision.py`、`blackboard/routing/router.py`、`blackboard/service/harness_health.py`、`blackboard/tasks/macro_tasks.py`、`blackboard/tasks/workflow.py`、`buddy/harnesses/claude/adapter.py`、`buddy/harnesses/codex/account_probe.py`、`buddy/harnesses/codex/adapter.py`、`buddy/harnesses/codex/native_run.py`、`buddy/harnesses/codex/protocol.py`、`buddy/harnesses/dsh/adapter.py`、`buddy/harnesses/registry.py`、`buddy/harnesses/run_contract.py`、`buddy/harnesses/zcode/adapter.py`、`buddy/roles/router.py`、`buddy/roles/run_execution.py`、`buddy/roles/turn_io.py`、`buddy/runtime/worker.py`、`errors.py`、`protocol/harness_permissions.py`、`protocol/router.py`、`protocol/turn_checkpoint.py`。

第 2 批测试、夹具与其他引用文件：`tests/probes/codex_permission_levels.py`、`tests/probes/router_readonly_tools.py`、`tests/probes/zcode_plan_mode.py`、`tests/python/blackboard/catalog/test_catalog.py`、`tests/python/blackboard/evaluation/test_native_observations.py`、`tests/python/blackboard/routing/test_router.py`、`tests/python/blackboard/routing/test_routing_modes.py`、`tests/python/blackboard/routing/test_routing_preferences.py`、`tests/python/blackboard/routing/test_selection_policy.py`、`tests/python/blackboard/service/test_harness_health.py`、`tests/python/blackboard/tasks/test_inquiry.py`、`tests/python/buddy/harnesses/claude/test_worker_permissions.py`、`tests/python/buddy/harnesses/codex/test_batch4_facts.py`、`tests/python/buddy/harnesses/codex/test_codex_continuation.py`、`tests/python/buddy/harnesses/codex/test_codex_protocol.py`、`tests/python/buddy/harnesses/codex/test_native_run.py`、`tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py`、`tests/python/buddy/harnesses/dsh/test_native_run.py`、`tests/python/buddy/harnesses/dsh/test_worker_permissions.py`、`tests/python/buddy/harnesses/test_quota_failures.py`、`tests/python/buddy/harnesses/test_run_contract.py`、`tests/python/buddy/harnesses/zcode/test_native_run.py`、`tests/python/buddy/harnesses/zcode/test_zcode_turn_io.py`、`tests/python/buddy/roles/test_checkpoint_shutdown_guard.py`、`tests/python/buddy/roles/test_outcome_truncation.py`、`tests/python/buddy/roles/test_permission_evidence.py`、`tests/python/buddy/roles/test_registered_run_wiring.py`、`tests/python/buddy/roles/test_role_live_seam.py`、`tests/python/buddy/roles/test_schema_worker.py`、`tests/python/buddy/runtime/test_windows_process.py`。

第 3 批源码文件：`blackboard/service/harness_health.py`、`buddy/harnesses/discovery.py`、`harness_locations.py`。

第 3 批测试、夹具与其他引用文件：`tests/python/blackboard/service/test_harness_health.py`、`tests/python/buddy/harnesses/test_harness_discovery.py`、`tests/python/cli/test_support_cleanup.py`。

第 4 批源码文件：`blackboard/catalog/catalog.py`、`blackboard/catalog/catalog_store.py`、`blackboard/routing/decision.py`、`blackboard/routing/router.py`、`blackboard/service/service.py`、`buddy/harnesses/claude/adapter.py`、`buddy/harnesses/codex/adapter.py`、`buddy/harnesses/dsh/adapter.py`、`buddy/harnesses/dsh/native_run.py`、`buddy/harnesses/runtime_selection.py`、`buddy/harnesses/zcode/adapter.py`、`buddy/runtime/worker.py`、`harness_selection.py`。

第 4 批测试、夹具与其他引用文件：`tests/probes/dsh_native_resume_preflight.py`、`tests/python/blackboard/catalog/test_native_discovery_stop.py`、`tests/python/blackboard/service/fixtures/daemon_with_catalog.py`、`tests/python/buddy/harnesses/codex/test_native_run.py`、`tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py`、`tests/python/buddy/harnesses/zcode/test_native_config.py`、`tests/python/buddy/roles/test_run_controller_discovery.py`。

第 5 批源码文件：`blackboard/service/daemon.py`、`buddy/runtime/api.py`、`buddy/runtime/supervisor.py`、`buddy/runtime/worker.py`、`cli/main.py`、`durable_json.py`、`install/upgrade.py`、`protocol/examples/external_worker.py`、`protocol/worker_receipts.py`。

第 5 批测试、夹具与其他引用文件：`tests/python/blackboard/store/test_blackboard.py`、`tests/python/blackboard/tasks/test_parallel_dispatch.py`、`tests/python/buddy/runtime/test_worker_invariants.py`、`tests/python/buddy/runtime/test_worker_live_wiring.py`。

第 6 批源码文件：`blackboard/service/live_registry.py`、`blackboard/service/service.py`、`blackboard/tasks/inquiry.py`、`buddy/harnesses/c_two_live.py`、`buddy/harnesses/claude/native_run.py`、`buddy/harnesses/codex/native_run.py`、`buddy/harnesses/dsh/native_run.py`、`buddy/harnesses/inquiry_bridge.py`、`buddy/harnesses/live.py`、`buddy/harnesses/registry.py`、`buddy/harnesses/zcode/native_run.py`、`buddy/roles/live.py`、`buddy/roles/run_controller.py`、`buddy/runtime/live.py`、`protocol/c_two_live.py`、`protocol/live.py`。

第 6 批测试、夹具与其他引用文件：`tests/python/blackboard/service/test_live_holder_integration.py`、`tests/python/blackboard/tasks/test_inquiry.py`、`tests/python/buddy/harnesses/claude/test_native_run.py`、`tests/python/buddy/harnesses/codex/test_native_run.py`、`tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py`、`tests/python/buddy/harnesses/dsh/test_native_resume.py`、`tests/python/buddy/harnesses/dsh/test_native_run.py`、`tests/python/buddy/harnesses/fixtures/c_two_live_peer.py`、`tests/python/buddy/harnesses/test_c_two_live.py`、`tests/python/buddy/harnesses/test_inquiry_owner.py`、`tests/python/buddy/harnesses/test_live_channel.py`、`tests/python/buddy/harnesses/test_pending_ready_receipt_guards.py`、`tests/python/buddy/harnesses/zcode/test_native_run.py`、`tests/python/buddy/harnesses/zcode/test_zcode.py`、`tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py`、`tests/python/buddy/roles/test_role_live_seam.py`、`tests/python/buddy/runtime/fixtures/live_runtime_peer.py`、`tests/python/buddy/runtime/test_live.py`、`tests/python/buddy/runtime/test_live_lifecycle_integration.py`、`tests/python/buddy/runtime/test_worker_invariants.py`、`tests/python/buddy/runtime/test_worker_live_wiring.py`、`tests/python/protocol/fixtures/ctwo_controller.py`、`tests/python/protocol/test_activity.py`、`tests/python/protocol/test_ctwo_integration.py`、`tests/python/protocol/test_inquiry_transport.py`、`tests/python/protocol/test_rpc_config.py`、`tests/python/protocol/test_state_boundaries.py`。

## 搬后仍有的 21 处与最小改法

这里的「最小改法」说明真实依赖的下一步，不把它冒充六批里的机械搬迁。涉及行为归属的改变必须让开发者决定，不能在改路径时悄悄做；下面每项都给出现有名字、实际调用时机与不可整体搬的原因。

| 当前导入与涉及位置 | 读：谁在什么时候调什么 | 推：最小改法与本方案的取舍 |
| --- | --- | --- |
| `registry.adapters`：I01、I02、I06、I08、I31 | `catalog.discovery_available` 看 `model_discovery`、调 `discovery_available`；`catalog.discover` 调 `discover_models`；`CatalogView.proposed_buddies` 与 `EvaluationStore._buddy_view` 调 `local_read_only_check` 并读取权限/no-tool 声明；`inquiry.adapter_capabilities` 只读 capabilities。`adapters()` 构造六类实例，registry 顶层还导入四个 native_run 并登记 `RUN_SEAMS` | 不能把整个 registry 或 adapters 函数搬入 protocol，它仍要引执行器。仅为声明的 I06/I08/I31 最少要把四个 Adapter 的 `capabilities`、`no_tool_structured`、`read_only_structured`、`read_only_structured_resume`、`system_sandbox_platforms` 及 `base.registered_read_only_check` 实际判定从执行器实现中分离；其中 `registered_read_only_check` 又读 `run_seam`，不是只搬属性就结束。本任务不新增一张替代执行器的能力表。I01/I02 的原生调用还要按下一行处理，留 5 处 |
| `registry.adapter`：I04、I11、I16、I27、I34 | `catalog.validate_buddy` 检查 model_discovery 后读 DB；`router.buddy_problem` 在缓存健康与配额合法后检查 no-tool/review；`harness_health.read_health` 每次读健康时取 review 资格；`Service._refresh_harness_catalog` 在健康刷新后调用 discover_models；`WorkflowCoordinator._execution_adapter` 在 begin_turn 取 native_resume/native_resume_permission_change，在 `_validate_turn` 调 validate_turn_provenance | DB 与资格/续接决定必须留 blackboard，启动与发现必须留 buddy。最小拆取范围是上述静态声明、`base.registered_read_only_check` 和四个 `native_run.validate_turn_provenance` 的真正校验体，不能把验证改成相信 Worker 的布尔值。I27 的进程执行仍不能靠这种拆取消除；整体移动 adapter 会把 command、Router 与 harness 执行载体一起带走。本方案保留 5 处，不给一张新能力表或校验分发接口 |
| `DecisionAdapter`：I09 | `DecisionCoordinator._adapter_available` 在选择前从 DB 取四个健康事实、bound，然后调用 `DecisionAdapter.available`；后者遍历执行器并读取 eligible/available，不在这里调用 start。类的 start/collect/cancel 则由 Worker 调，真正驱动 Router 的统一载体 | 最小是把 available 的资格循环归服务，并依赖服务已有健康与真正分离后的声明；不能只把类搬到 blackboard，因为 start/collect/cancel 与子进程持有属于 buddy，也不能另建 Router 执行路径。当前没有一个已分离且够用的声明函数可以原样搬，留 1 处 |
| `discovery.discover`：I15 | `HarnessHealth.refresh → _discover` 在候选变化、到期或显式刷新时同步执行原生 version/login 握手；过程中会启动进程并确认进程组停止，再由服务带 generation 写健康记录 | 最小是让 buddy 按既有拥有进程的执行路径完成这段探测，服务接收现有事实并保留 revision/generation 资格判断。当前服务的发现是同步调用，已有 Worker 任务/回执能持有进程，但没有一个现成命令能保持当前同步刷新、single-flight 与停止异常语义；迁移需要改执行流程。本方案不新增 RPC、命令任务包装器或把探测挪到根包，留 1 处 |
| `codex.account_probe.read`：I20 | `HarnessHealth._codex_account_read` 在 ready 的 Codex 扫描中、读窗口允许时启动 app-server，读 account/config/rateLimits，finally 停止并核对，再由服务原子记录账单/配额；read 没有模型回合，但确实拥有原生子进程 | 最小与 I15 一样迁到 buddy 持有的现有运行路径，服务保留 `_codex_account_read` 的限流、generation、额度发布与停止失败判定。不能把 Codex 的 Connection、ProcessHandle 与 owned_popen 连带搬进黑板或 protocol；也不能删这次读取，否则原生配额会失去当前来源，留 1 处 |
| `registry.capability_report`、`local_capabilities`：I22、I25 | `Service.capabilities` 在自动发现关闭或没有绑定健康时也会调用 `available`；Claude 未绑定时可能读取原生元数据，DSH 未绑定时可能 discover。report 又读取 `local_read_only_check`；external 声明及命令任务声明也在其中 | 最小是把 report/local_capabilities 的现有汇总逻辑归服务，真实原生读取归 buddy，并把已经存在的静态声明移出执行器；不能只靠 DB ready 替代所有 available 路径，尤其 automatic_discovery=False 分支会变。当前汇总体没有独立的声明来源，整函数搬走仍会引 adapters。本方案保留 2 处，不能宣称只是格式搬动 |
| `registry.live_binding`：I32 | `inquiry.observe` 只用返回值是否为 None 判断是否有抽出的 live 机制；同一函数在 `buddy/roles/live.py.handle_live_binding` 还会返回实际 CTwoLiveChannel 类，依据 `RUN_SEAMS` | 最小可把 inquiry 的资格判定归自己的已有 live 能力判断，但必须保留「已登记而暂未 attach」与「根本不支持」的区别。直接删 extracted_live 会丢失 active-unbound 的正确原因；直接换成 HARNESS_NAMES 成员检查会把真实登记状态偷换为名字存在。搬 live_binding 到 protocol 又会把执行器 registry 拉回来。本方案不为这 1 处另造标志或薄函数，保留 |
| `blackboard.tasks.workspace`：I39；`workflow.workspace_module`：I40–I43 | `router_input.prepare` 在 Router 启动前、`router_input.verify` 在停止后调 workspace.verify；`turn_io.verify_workspace` 在回合准备时调 verify；`turn_io.seal_workspace` 在已确认停止后调 seal；`partial_outputs.capture/enrich` 在失败/取消且停止确认时调 seal、cumulative_patch。`workspace_module` 只是带缺失模块错误的 getter，不提供新实现 | verify/seal/cumulative_patch 操作 Git、固定 refs、私有输出与权限边界，不能说成纯函数；整个 workspace 还包括 prepare、resolve、integration_verify、cleanup_inspect、cleanup_remove。最小有意义的搬迁是先抽出 verify、seal、cumulative_patch 的完整现有依赖闭包供两侧使用，保留服务的 prepare/resolve/cleanup 与授权；这会跨 `_observe`、`_output_entries`、`_seal_output`、`_finish_output`、`_artifact_binding`、Windows 路径与锁，不是删 getter 能解决的 5 行。另一条路是在既有回执接受流程中由服务封存，但会改变停机后到回执前的证据保留与失败分类。本方案不为清零拆这套持久工作区实现、不删除失败输出封存，留 5 处 |

上述最小改法中的纯声明拆取与真正行为迁移必须分开核定；静态声明能再少几处，不会消除 I02/I15/I20/I27 的原生进程操作或 I39–I43 的受管工作区操作。把 `workspace.py`、`account_probe.py`、`discovery.py` 的剩余进程部分或 registry 全部搬到根包，会使计数为零却失去第 12 条的归属意义，本方案不建议这样做。

## 做完之后守住什么

最小护栏是在 `tests/python/cli/test_repository_hygiene.py` 加一个纯 AST 的仓库卫生检查，只读 Python 文件，不 import 产品模块、不启动进程。解析全部源码，用所在包与 ImportFrom.level 还原相对路径，展开 `from package import child_module`，拒绝 blackboard 导入 buddy、buddy 导入 blackboard、protocol 导入任一侧；检查所有函数、条件与 TYPE_CHECKING 分支，报告文件、行号、目标模块与名字。对 `importlib.import_module` 和 `__import__` 的字面量调用用同一规则；这些三个目录内新增非字面量动态导入直接失败，避免把静态依赖改写成扫描不到的路径。用仓库现有卫生文件即可，不增加运行时检查、依赖图框架或产品开关。

如果只完成这 6 批，护栏不能伪称「零」。阶段性办法是在这一个测试中列出上表 21 处的准确允许项，键用「源文件＋所在函数链＋目标模块＋导入名字」和重复次数，不用易漂移的行号；断言实际清单与允许项完全相等，不许只检查总数。其中 adapters 在 catalog 的三处、evaluation 的一处、inquiry 的一处，以及 workspace_module 的四个调用者都必须分别登记，不能用整个文件或模块作豁免。每次真消除一处，就在同一改动删掉允许项；如果开发者批准行为归属改动并最终清零，删掉所有允许项，检查直接要求空集。护栏只约束源码，测试中跨侧的集成夹具不是产品依赖；这些夹具仍随原定义路径更新。

为避免从根包绕回另一侧，共用根模块 `json_codec.py`、`timestamps.py`、`durable_json.py`、`harness_selection.py`、`harness_locations.py`、`errors.py` 同样不得导入 blackboard/buddy；这是上述同一 AST 检查中的固定路径规则。CLI、install、console 是组装调用方，不能以它们为借口给共享模块放行。最小护栏覆盖导入及已列的字面量动态调用，不声称能证明任意 exec、反射或运行期构造的依赖；本提交没有这些跨侧规避形式。

旧的 `docs/acceptance/adr025-step5-cross-imports.tsv` 建议留作历史证据，保持当时的 56 行，不拿它当当前允许清单，也不继续刷新其行号；ADR-025 和当时验收记录引用的是那次未消除跨侧引用的事实。最终完成时，只在当次验收记录写清新提交、零清单与卫生检查结果，把 AGENTS.md 中把该表当当前登记表的说明改成历史记录。本任务不改这两份文件。如果只做到 21 处，验收记录必须写 21，不能标第 11 行清零已完成。

## 不建议为了清零而动的地方

不建议把 `registry.adapters/adapter`、`DecisionAdapter` 整个搬进 protocol/root，不建议给 Service 增加一套转发执行器的门面；保留代价是执行器导入会把四个 native_run 和登记副作用带入服务进程，资格检查与实现仍有耦合。I01/I02/I04/I06/I08/I09/I11/I16/I22/I25/I27/I31/I34 共 13 处属于这组，值得在明确声明和执行职责的任务里处理，不值得用文件改址掩盖。

不建议只为 I15、I20 改造原生健康/账户读取的任务调度，或只为 I32 新增一层登记状态接口。前两处留下服务进程拥有短命原生探测子进程的实际例外，后一处留下服务对执行器注册事实的直接依赖；它们是未完成的边界工作。代价必须保留在验收说明里，不能把「没有模型回合」写成「没有启动 harness」。

不建议只为 I39–I43 把 `blackboard/tasks/workspace.py` 两千多行及 `windows_paths.py` 整体挪到根包，也不建议提前把失败输出封存放到服务来减少导入。保留代价是 Worker 的准备、停止后封存与累积补丁继续依赖服务侧工作区实现及 `workflow.workspace_module`；getter 的过时缺失模块回退也仍存在。它可以在工作区职责整理时删除，但删除 getter 后直接引 workspace 仍是 5 处跨侧引用，不能记作清零进度。固定产物、停机前提、owner fencing 和删除证明的稳定性比这 5 行的数量重要。

`buddy/harnesses/run_contract.py` 整份格式目前只有 buddy 使用，不为 I21/I24 连带把它整体搬走；第 6 批只把其重新导出的 RunIdentity 使用改到已经存在的 protocol 定义，第 2 批只搬确实共享的 WorkerPermission/ToolScope。服务的 HARNESSES 扫描顺序也不为 I45 合并成另一份不同顺序的元组。这些没有留下新的跨侧导入，避免的是无必要的格式迁移与数组顺序行为改动。
