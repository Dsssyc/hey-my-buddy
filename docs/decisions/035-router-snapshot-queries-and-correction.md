# ADR-035：路由面与维护面的按需冻结查询、统一格式纠偏与跳过窗口修订

## 状态

已接受并实施：开发者于 2026-11-19 确认（经独立只读设计审查 `7e3539ad` 核实源码并收口），已于 `0.72.0`–`0.72.1` 落地并安装验证（见 [0.72.0](../acceptance/installs/0.72.0.md)、[0.72.1](../acceptance/installs/0.72.1.md)）；维护面批次预算、查询默认值与轮内覆盖门禁修订于 2026-11-23 确认并于 `0.78.0` 落地。本决定修改 [ADR-021](021-router-buddy-planes-and-routing-evidence.md) 第 2、3、4、6、7、8、9、10、11、16、20 条，[ADR-023](023-harness-integration-principles.md) 的“各角色共用一套档位”一节（含取消“维护大包切 `read`”的旧补充），[ADR-030](030-harness-layer-scope.md) 第 2、9 条（同步更新 `AGENTS.md` 中的清单表述），并沿用 [ADR-031](031-one-process-per-execution.md) 第 2、10、12 条及 [ADR-034](034-retire-helpers.md) 的历史封存不改写规则；同步更新 [CONTEXT.md](../../CONTEXT.md) 中 `不用工具 / no tools` 的表述、`src/hey_my_buddy/buddy/roles/texts/router.txt` 及相关参考文档。

## 背景

在日常黑板的持续使用中，路由面与维护面暴露出三类结构性问题：

- **提示词与执行启动载荷随规模增长无界膨胀。** 当前路由面在单条提示词中一次性灌入全部活跃领域节点、全部合法候选的三层统计矩阵（含每格重复生成的 Wilson 置信区间与依赖实时等待秒数的 `resultTime`）、全部同型 buddy 的归纳与模型画像，以及包含全部候选标识与领域节点标识的 JSON Schema `enum`。在 7 个候选、106 个领域节点时，单次路由提示词已达 `214,305` 字节，其中统计矩阵占 74.4%；不断变化的实时等待时间还使静态前缀无法命中提示词缓存。服务在本地调度器内通过 `execution-start.json` 启动执行进程时，也将整份 `decisionInput` 随启动文件传入；维护面虽然已改用私有文件跨进程交接，但执行进程启动时仍将整包还原进单条提示词（[ADR-021](021-router-buddy-planes-and-routing-evidence.md) 第 9 条记录 6 个待维护微任务已达 `293,844` 字节）。虽然单条微任务正文上限 1 MiB、维护单批上限 50 个微任务，但领域树节点数、同型数、回合数与续接/边界证据总量均随使用持续增长，仅靠删除重复统计字段无法守住“不为使用规模设假设”的原则。
- **格式纠偏口径割裂、维护面写入早于终判且存在冗余输出字段。** 当前角色层的 `correction_code` 只检查基础 JSON Schema，未覆盖 `router.validate_answer` 中的额外规则（例如审阅模式 `file` 证据必须为纯相对路径、不得附带 `:line` 行号）。在 DSH CLI 的审阅路由中，出现过耗时 70 秒、调用 5 次工具后因文件引用带行号在收集阶段被拒、而纠偏次数为零的情形；DSH 与 ZCode 纠偏时会关闭旧会话并新开原生会话，却未将上一份原答与具体出错位置带给新会话；Claude Code 的 `-p` 路径更直接拒绝纠偏。同时，维护面现行 `Maintenance.answer()` 在纠偏轮次内直接向数据库提交领域树变更、归类与归纳，且在请求结束后删除答案文件，若后续轮次发生工具违规、事件流不完整或停止未确认，终态 `complete()` 无法撤回已提交写入。此外，带探索名额的路由答案中，`reselected` 完全可由 `buddyId` 与 `preferredBuddyId` 推导，属于冗余字段。
- **单次格式错误即触发跨请求跳过窗口。** 按 [ADR-021](021-router-buddy-planes-and-routing-evidence.md) 第 2 条现行规则，Router 只要有一次未给出可采纳答案，即进入默认 10 分钟的跳过窗口。一次偶发的格式错本属于单次生成失误，却让后续所有独立微任务在整段重试间隔内绕开原本健康的首选 Router。

此外，现行 `不用工具 / no tools`（`none`）权限档位按字面要求零工具调用，使需要读取私有冻结数据的路由面与维护面只能靠单条巨型提示词交互，实际可用性很弱；而在 Worker 角色上，黑板早已通过会话私有服务（`session_mcp` 与 Codex `dynamicTools`）向回合提供角色自有的受控能力。

## 决定

### 一、权限档位 `none`、角色自有会话服务与资格边界

1. **保持五档不变，明晰 `不用工具 / no tools`（`none`）的边界（修改 ADR-023、ADR-030 与 CONTEXT.md）。** 五档权限仍为 `none`、`web`、`read`、`write`、`full`，不增设第六档。权限档位约束的是 harness 的原生工具与用户外部工具（文件系统、命令执行、网络与用户配置的 MCP 等）：`none` 表示**不开放任何原生 harness 工具与用户外部工具**，但允许调用执行进程自身为该角色挂载的私有只读会话查询服务。角色自有的私有会话服务（Worker 的交付与问询服务、Router 与维护面的冻结快照只读查询服务）作为黑板交付给回合的受控能力，明文补入 [ADR-030](030-harness-layer-scope.md) 第 2 条清单（同步更新 `AGENTS.md` 中的清单副本），不归为 harness 原生工具，也不构成按某一家 harness 开关原生功能的例外。
2. **快速路由、审阅路由与维护面统一共享冻结快照查询；资格与 Worker 权限解耦。** 快速路由（`fast`）与维护面（`maintain`）运行于 `none` 档并挂载本次执行的冻结快照只读查询服务；审阅路由（`review`）运行于 `read` 档并同时提供冻结源码副本与同一套冻结快照只读查询服务。`fast` 与 `review` 的差异收敛为：是否提供冻结源码副本及对应的 `read` 权限档位。画像面（`profile`）仍运行于强制的 `web` 档，不挂载快照查询服务。各 harness 独立声明是否具备“Router 冻结查询挂载能力”；若某家暂不具备该能力，仅撤销其 `routing:fast` / `decision` / 维护面资格，不影响其已具备的 `read` 档 Worker 权限。
3. **事前隔离守住 ADR-023 第 10 条，事后以根回合原生工具证据与单次应答凭据双向核对。**
   - **事前隔离声明**：不要求所有 harness 对 `none` 档叠加私有查询服务做到事前强制独占。只有同时满足“原生白名单或每次启动按完整工具清单禁掉全部内置与外部工具、仅暴露本次挂载查询服务”且经原生实测核实的 harness，方可在 `PERMISSION_LEVELS` 中声明 `none: "enforced"`（沿用 [ADR-023](023-harness-integration-principles.md) 第 10 条，一次探针成功不替代动态/白名单写法）；无法事前屏蔽用户外部工具或内置工具的 harness 声明 `none: "convention"`。
   - **固定服务名 + 单次应答凭据双向归属（防前缀缓存失效与同名外部工具碰撞）**：
     - stdio MCP 载体（DSH、ZCode、Claude Code）使用固定服务名（例如 `hey_my_buddy_snapshot`），保持跨请求工具定义稳定以利提示词前缀缓存；不靠随机服务名区分归属。
     - 私有查询服务复用 [session_receipts.py](../../src/hey_my_buddy/buddy/harnesses/session_receipts.py) 的每尝试独立 HMAC 密钥，为每一次**成功且实际返回数据**的查询应答签发唯一递增序号的应答凭据，并写入 `invocation_root` 下只追加的本地查询账本（记录凭据序号、工具名、参数摘要、成功标记与实际交付的字段/页覆盖范围）；失败调用或 `isError` 应答不签发成功凭据。
     - 原生驱动将根回合工具调用事件中的 `(nativeIdentity, callId)` 与对应工具结果里的签名凭据双向关联，并核对账本记录与工具调用一致（Codex `dynamicTools` 在同进程内直接按 `(threadId, turnId, callId)` 关联）。重放凭据、孤立账本行、包装器将 MCP `isError` 呈为成功、外来根或未配对事件均不得通过核验，也不得制造已读覆盖。
     - Codex 驱动与 `protocol/tool_evidence.py` 的 `_consistent_call_facts` 为本次挂载的 `dynamicToolCall` 补齐精确的 typed/raw 一致性配对（仅限本次挂载查询工具名，不放行泛化的 `other` 类）。
   - **`ToolEventEvidence` 升至版本 4（历史版本 3 封存保持原样可读）**：
     - 包头新增事实字段 `sessionServiceTools`（本次执行实际登记的完整查询工具名列表；未挂载时为 `[]`，含画像面多调用聚合器），每条调用事实增加 `sessionServiceVerified: bool`（仅当上述双向核对成立且属于根回合完整起止调用时为 `true`）；`toolCalls` 与 `toolSummary` 如实保留全部工具调用计数。
     - `judge_tool_evidence` 在 `none` 档下仅放行 `sessionServiceVerified == true` 且工具名属于 `sessionServiceTools` 的调用（对已匹配账本的私有查询失败调用同样按已归属查询处理而非外部工具违规，但不推进任何已读覆盖）；任何原生工具、用户外部 MCP（含同名碰撞工具）、未核验调用、孤立/迟到/外来根或事件流不完整一律判为 `router-tools-forbidden` 或 `router-tool-evidence-unverified` 并作废本次答案。在 `read` 档下除 `read`、`search`、`execute`、`fetch` 外同样放行已核验的私有查询调用；Claude Code 经验证的 `StructuredOutput` 交付豁免维持不变。（2026-11-20 补记）Codex 在挂载只读快照服务的 `none` 档下沿用 `web` 档的既有规则，放行完整且一致的受限 JavaScript 分发器 `exec` 根调用（`facts` 严格仅含 `{"toolName": "exec", "category": "execute"}`），因为 GPT-6 `code_mode_only` 模型须经该无 OS/网络 API 的分发器调用挂载的 `dynamicTools`；其内部派发的每个工具调用仍各自发出独立原生事件并按 `none` 档逐条裁决，未挂载快照服务的纯 `none` 档继续拒收 `exec`。
   - **驱动挂载与观察器解耦**：四家驱动将“只读快照查询服务挂载”与“Worker 完成/问询交付挂载”分离（不再以 `services is not None` 等同于 Worker 交付路径）；`FastCorrection`、`MaintenanceCorrection` 与 `ReviewObserver` 不再在首个已核验查询工具调用处中止，同时仍对不可解释的 tool marker、被拒原生交互与未归属/越界工具调用保持敏感并如实上抛，交由最终 `judge_tool_evidence` 裁决。

### 二、冻结快照按轮次交接、有界查询与维护面终判提交

4. **路由冻结快照按 `(decision_id, round)` 经私有文件交接至执行进程，表结构暂保 `SCHEMA_VERSION = 42`。**
   - 复用现行内容寻址私有文件模块（抽出共用层，隔离 `routing`、`maintenance`、`profile` 命名空间），按 `(decision_id, round)` 发布一份路由冻结快照文件（同轮切换 Router 共享同一轮快照，重路由 `selectionRetry` 生成新轮 `round + 1` 与独立文件）。服务通过 `execution-start.json` 向执行进程仅传递小体积固定请求头、轮次与该文件的 `{"sha256": ...}` 引用及派发 `inputSha256`（`tasks.spec_json` 维持只存 `decisionId` 与 `kind`，不重复落盘）。
   - 新路由快照在 `router_requests.snapshot_json` 中显式标记 `protocolVersion: 20`（独立于仅标工作区形式的 `snapshot_form`），历史审计与 `selection_get(includeAudit=True)` 仍可完全从 SQLite 读回；同步修补现行 `decision.py` 两处重路由审计缺陷（零候选重路由时清空上一轮残留的 `task_id`/`attempt_id`/`generation`；全列表不可用重路由返回前先补记 `_record_preflight_walk`）。
   - **生命周期与存储清理一致**：回滚只删本次新建且无其他轮次引用的文件；启动孤儿回收须在写锁内与派发互斥，并同时保留活动决策以及仍有未确认停止（`starting`/`executing`/`finalizing`/`uncertain`）attempt 关联的 `(decision_id, round)` 快照文件；宏任务删除覆盖全部轮次；在 `storage.py` 中将 `routing/` 登记为可回收的受管运行期目录而非未知持久化目录。
   - **有界承诺的诚实边界**：本期将**模型提示词、输出 Schema 与执行启动文件（`execution-start.json`）**收束为 O(1) 有界；Host `get` 的 `routingBoundary`（同时同步去掉其中 `facts.buddies` 与顶层 `candidates` 的双份重复）与控制台 `selection_get(includeAudit=True)` 仍沿用既有单次读取边界，后续需要时再另行分页。表结构本期保持 `SCHEMA_VERSION = 42`；`kind_summary_evidence` 的持久含义保持“该同型 buddy 的归纳已处理该微任务”，实读覆盖作为新执行的准入证据（不追认历史行，也不必为此勉强升级表结构；若后续确实需要持久化逐页来源或独立拆分快照表，再立 Schema 43）。
5. **初始提示词与输出 Schema 保持 O(1) 体积。**
   - 移除路由与维护输出 JSON Schema 中所有随规模增长的动态 `enum`（`buddyId`、`preferredBuddyId`、`domainNodeId`、`runId`、`citations`）与动态对象键（维护面 `summaries` 改为固定结构数组 `[{"kindId": ..., "text": ..., "citations": [...]}]`），由 Python 边界照冻结快照执行等价的唯一性、必交性与成员资格校验。
   - 初始提示词分为稳定的静态前缀（同步重写 `buddy/roles/texts/router.txt`，移除零工具、Wilson 区间与 `resultTime` 旧表述，统一协议版本 20 与查询指引及固定输出 Schema）与小型请求头：
     - **路由面请求头**：`requestId`、`routingMode`、`project`、`expectedSeconds`、`requiredCapabilities`、`policyFacts.hardConstraints`、`exploration` 标记、候选总数、活跃领域节点总数，以及任务正文的有界预览（不超过内联阈值如 8 KiB UTF-8 字节时直接全文内联；超出时内联前段并标明总字符/字节数与截断标记，由 `query_task` 无损分页读完）。`routingBasis` 仍仅作 Host 审计事实，不投喂模型；冻结领域节点补全 `definition` 供查询。
     - **维护面请求头**：`maintenanceRunId`、本批待归类微任务数、待重写归纳同型数、领域树节点总数、固定七种 `taskTypes`，以及单批上界说明。为同时满足静态数组完整性校验与 DSH 65,536 字节单次答案上限，`build_packet` 除现有最多 50 个待处理微任务外，对单批同时进入重写归纳的 `kinds` 数量施加有界配额（例如单批最多 8 个同型，确保最坏情况完整输出远低于 64 KiB），剩余待处理项留待下一批按 `continuePending` 续跑。（2026-11-23，0.78.0 修订）维护批次改为以 UTF-8 字节预算与件数双上界收敛：`build_packet` 按接受或结案时间升序（`runId` 破平）选取待处理微任务，单批最多 `16` 个微任务、`3` 个待重写归纳同型，且 `microTasks` JSON 数组（含方括号与逗号）在纳入下一条前不超过 `128 KiB` UTF-8 字节；若首条待处理微任务本身超过该预算，仍完整单条进入本批、绝不截断；待重写归纳同型仅从最终入选本批的微任务及其回合顺序中分配，剩余待处理项沿 `continuePending` 续跑。
6. **所有冻结查询接口逐页有界、可续页、无损遍历。** 每次调用均强制单次批量 ID 上限与序列化后 UTF-8 字节上限，所有会随规模增长的集合与长文本均提供显式分页游标或字符偏移（按 Unicode 码点偏移避免切裂多字节中文）：
   - **路由面查询工具**：
     - `query_domains(parentId?, search?, cursor?, limit?)`：按父节点分层浏览或关键词检索冻结活跃领域树，返回 `nodeId`、`name`、`definition`、`parentId`、`childCount` 与 `nextCursor`。
     - `query_compare(domainNodeId?, taskType?, cursor?, limit?)`：分页返回合法候选精简对比表，含 `buddyId`、四元组、`billing`、冻结时刻 `load`（`active`、`limit`、`queued`、`wait`）、用户偏好（`mode` 与短预览及是否有长备注）、以及 `domainNodeId × taskType`、`domainNodeId` 汇总与整体三层核心统计（不再计算或输出每格重复的 Wilson 区间与实时 `resultTime`），并标明 `hasSummary`、`hasProfile`、`profilePositioning`（已构建画像时的一句话定位，未构建时为 `null`）与 `nextCursor`。
     - `query_explore(domainNodeId?, cursor?, limit?)`（仅本请求带探索名额时暴露）：基于冻结领域树与统计，分页返回对应第一层领域（未归类时看整体）下各同型 buddy 组的样本数排序及组内候选列表，带 `nextCursor`。
     - `query_details(buddyIds[], include?, cursor?, limit?)`：分页读取入围候选的 `contextWindow`、`capabilities`、`modelId` / `modelDisplayName` / `modelNames`、`infrastructureFailures`、完整模型画像、维护面归纳与完整偏好理由（单条超页字段支持游标续读）。
     - `query_task(offset?, limit?)`：按码点偏移分页读取完整任务正文。
   - **维护面查询工具**：
     - 复用 `query_domains`（并支持查询已合并节点及其最终保留目标 `mergedIntoId`，供核对历史归类与树合并）。
     - `query_batch(cursor?, limit?)`：分页列出本批待处理微任务的索引（`runId`、`title`、已有 `classification` 或 `null`、是否待归类、参与的同型 `kindId` 列表、终态与返工数）。
     - `query_micro_tasks(runIds[], sections?, cursor?, limit?)`：按微任务与分段（`task`、`turns`、`continuations`、`hostBoundaryFacts`、`acceptanceAndIntegration`）逐页有界读取完整内容，单条长文本或长续接/边界列表均可通过 `cursor` 无损续读，并在每页明确返回本页完整覆盖的区间。
     - `query_kinds(kindIds[]?, domainNodeIds[]?, include?, cursor?, limit?)`：分页读取待重写归纳同型（及参与对比的无待重写归纳同型）的 `kindId`、`unreadRunIds`、`citationRunIds`、现有归纳与历史引用列表及指定领域/格子的对比统计，均带 `nextCursor`。（2026-11-23，0.78.0 修订）维护面下 `query_domains()` 不传 `parentId` 时默认分页遍历全部活跃节点，传 `parentId: null` 仅看根节点，传 `includeMerged: true` 纳入已合并节点（路由面仍保持按父节点分层浏览不变）；`query_kinds()` 默认返回所有同型的紧凑标识、`needsSummary` 及待重写归纳目标的 `unreadRunIds`，现有归纳（`summary`）、`citationRunIds` 与对比统计（`statistics`）通过 `include` 按需读取，并可用 `kindIds` 与 `domainNodeIds` 缩小范围。
7. **维护面实行“轮内校验暂存、终判原子发布”与精确已读覆盖（修复 P0 并修改 ADR-021 第 8、9、11 条）。**
   - **发布时点后移至终态裁决**：取消 `Maintenance.answer()` 在纠偏轮次内直接改写数据库的做法，与画像面保持一致——执行进程保留每轮私有答案文件直至终态收执；`execution_maintenance` 仅做校验、对照已暂存的合格项更新本轮暂存快照并返回精准纠偏反馈（同轮同摘要重放返回原反馈，异摘要报 `CONFLICT`）。只有在 `complete()` 同时验证 `shutdownConfirmed == true` 且整次运行的 `judge_tool_evidence` 通过后，才在同一写事务内复核当前存活微任务与活跃领域树、原子提交全部已暂存的合格项。工具违规、事件流不完整或停止未确认时整次不发布任何树/归类/归纳写入；仅格式纠偏耗尽而工具与停止证据均合格时，仍发布已暂存的合格项。（2026-11-23，0.78.0 修订，契约升至 `0.78.0`，表结构保持 `SCHEMA_VERSION = 43`）轮内调用 `execution_maintenance` 的私有载荷同步携带执行账本中已签发的 `query_micro_tasks` 页面覆盖快照 `coverage`（`{"answer", "observations", "coverage"}`）。`stage_answer` 仅暂存内容校验通过且所需分段均已签发的条目；未读完的条目不写入 `staged`（避免锁死后续重写触发 `conflicts with staged entry`），改为在反馈中以去重后的 `missingReads` 按 `runId × section` 返回精确续读游标（`cursor: null` 表示从该段起点读起，非空游标直接单任务单段续读直至 `nextCursor` 为 `null`）。若某条已暂存归纳在后续轮次前因所引微任务被删而失效，允许后续轮次重写替换。新会话纠偏发送未暂存草稿时剔除已暂存项并完整保留未暂存结构化条目（非 JSON 原文仍保留 4,000 字符上限）。轮内 `coverage` 仅用于暂存门禁与纠偏指引；终态 `complete()` 仍须在确认停止且 `judge_tool_evidence` 通过后，独立读取绑定在尝试目录内、经签名应答与根回合原生调用双向配对的 `snapshot-coverage.json` 复核已读覆盖与当前存活状态，再原子提交合格项。
   - **精确覆盖守卫与必交校验**：
     - 依据执行进程双向核验过的本地查询账本累计已确认返回的覆盖范围（绝不采信模型自述）：归类某 `runId` 须已完整读完该 `runId` 的全部 `task` 页及基础元信息；重写某 `kindId` 的归纳须已完整读完该同型在本批全部存活 `unreadRunIds` 的 `task`、`turns`、`continuations`、`hostBoundaryFacts` 与 `acceptanceAndIntegration` 全部页。单页未读完、仅读标题、失败查询或批量请求中漏返的 ID 均不算已读。
     - 只有归类需求（无未读同型归纳）的任务可单独完成归类；已归类任务只要求补齐对应同型的归纳；冻结后已被删除的微任务自动免除待办，归纳引用已删任务则打回重写，包外捏造 ID 直接报错；历史归纳的旧引用可继续保留，但不据此授予包外旧任务正文查询；领域树增改本身不推进任何微任务已读。
     - 针对静态数组 Schema，服务端显式计算 `remaining = surviving_targets - staged_valid` 并逐项报出遗漏与重复（禁止交 `[]` 零写入零报错）；`maintenance.finished` 仅在本运行**实际提交了至少一项新归类或新归纳**且仍有待办时置 `continuePending: true`，杜绝零进展批次死循环。

### 三、路由面统一格式纠偏、错误优先级与删除 `reselected`

8. **在 `protocol/` 建立冻结选择答案单一校验真源，明确错误优先级（修改 ADR-021 第 6、7、11 条）。** 角色观察者与黑板发布层共用同一份校验逻辑，并固定如下判定与发布顺序：
   - **工具证据优先于格式错**：`_publish_select()` 先核验停止证据与 `_tool_evidence_problem`，再判定答案格式；伴随禁用工具或未核验工具流的坏格式必须记为工具违规，不得落入纯格式错享受跳过窗口宽限。
   - **硬越界优先于可修格式错（永不纠偏，直接结束）**：只要回答可解析为 JSON 对象且包含对应字段，优先检查硬越界——类型合法但不在冻结合法候选集中的 `buddyId` / `preferredBuddyId`、类型合法的非空且不在冻结活跃领域节点集中的 `domainNodeId`（领域树不为模型拼写或幻觉兜底）、`fast` 模式下 `evidence` 出现 `kind == "file"`、带探索名额时合法改选的同型 `(harness, modelId, effort)` 在第一层领域（未归类看整体）的样本数不小于首选同型——额外字段或其他轻微格式错误不得掩盖上述硬越界。
   - **合法弃选优先于改选推导**：带探索名额时 `preferredBuddyId` 仍须为合法候选；若 `buddyId` 为 `null`，即合法弃选，按 `answered` 结束（记录 `quota-kept`，不查所选同型、不记 `quota-reselected`）。
   - **可修格式错（最多纠偏 1 次）**：收到空文本、非合法 JSON（报告行列）、根对象结构错、缺字段/多余字段、类型错、空串/超长、非法 `taskType` 或未知 `evidence.kind`、`file` 证据路径非法（绝对路径、`..`、反斜杠、冒号如 `path:line`、`.` 或控制字符）。原生根从未交回任何答案（如异常退出/未响应）与“已收到空文本/坏 JSON”分别归类，前者不进格式纠偏。提示词、[decision.md](../reference/decision.md) 与校验统一写明 `file` 证据只能是纯检出相对路径、不得附带行号。
   - **按结构化调用实际保留的上下文组装纠偏输入**：原生保留同一会话上下文的驱动（当前为 Codex 同 thread 新 turn）在原会话中仅追加精准出错字段路径/行列与修正要求；每次纠偏会新开原生会话的驱动（DSH、ZCode、Claude Code）须重新提供角色说明、O(1) 小请求头、同一冻结查询入口、上一份原答（有界）与具体出错位置，维持原权限与输出 Schema；跨轮聚合全部根会话标识、工具证据调用文件、已读覆盖与用量，`correctionCount` 仅计实际发起的纠偏轮次（取消或未启动不计）。维护面与画像面沿用各自最多 2 轮纠偏语义。
9. **移除探索答案中的冗余字段 `reselected`。** Router 协议版本升至 20；带探索名额时模型输出仅含 `preferredBuddyId`（必填合法候选），删除 `reselected` 字段。程序由 `buddyId is not None and buddyId != preferredBuddyId` 推导 `quota-reselected` / `quota-kept`，历史 `routingHistory.exploration.reselected` 等公开投影继续由程序产出，历史协议 19 封存记录保持原样可读。仅在发布时原合法候选因目录/偏好/冷却变化失效才触发 `selectionRetry`。

### 四、跳过窗口精确状态机与并发认领次序

10. **按 buddy 与 `event_seq` 推导跳过窗口与单次在途试探（修改 ADR-021 第 2 条）。** 单次请求内，Router 无有效答案且确认停止后仍照序切换下一位。跨请求跳过窗口继续纯由 `router_outcomes` 既有终态回执推导，保留原始连续未答计数供控制台显示，状态机规则如下：
    - **格式错代码集合**：明确包含现行与历史纯格式错码（`answer-shape`、`answer-invalid-json`、`answer-empty`、`answer-not-json`、`answer-unexpected-field`）；历史回执仅按已存 `code` 投影，不猜测历史 `answer-shape` 是否曾含非法领域。
    - **按 `event_seq` 顺序归约（自最后一次有效 `answered` / 合法弃选之后）**：
      - 维护 `window_triggered: bool` 与 `format_request_ids: set[str]`。
      - 遇到任一非格式错 `no_answer`：`window_triggered = True`，刷新 `skipUntil = at + interval`。
      - 遇到格式错 `no_answer`：若 `window_triggered` 已经为 `True`（含窗口到期后的试探再次报格式错），立即刷新 `skipUntil = at + interval`；否则将 `request_id` 加入 `format_request_ids`（同一 `request_id` 跨 `selectionRetry` 多轮均报格式错只计 1 个独立请求），当 `len(format_request_ids) >= 2` 时置 `window_triggered = True` 并设置 `skipUntil = at + interval`。
      - 仅有 1 个独立请求报格式错且无非格式错时：`window_triggered` 为 `False`，`inSkipWindow = False`，`skipUntil = None`，`retryAt = None`；Host 边界一致视其为立即可重试。
      - 遇到任一有效 `answered`（含合法弃选）：清零 `consecutiveNoAnswers`、`window_triggered`、`format_request_ids` 与 `skipUntil`。
    - **修正并发认领次序（`decision.py:prepare_router_claim`）**：必须先判定 `inSkipWindow`——处于有效跳过窗口内直接跳过并推进至下一位 Router；只有当 `not inSkipWindow` 且 `skipUntil is not None`（跳过窗口曾被激活且已到期）且存在活动/未确认尝试时，才认定 `retryInProgress = True` 并排队等待单次在途试探结束。这样可避免两请求并行时一个开窗、另一个仍在跑，导致第三个排队请求错误卡死在已进窗口的旧 Router 上。

### 五、原生实测门禁与验证边界

11. **先做四家 harness 受控原生探针，再定声明表并切入主实现。** 本决定进入主实现前，须经开发者逐次批准原生调用，在四家 harness（Codex CLI、Claude Code CLI、DSH CLI、ZCode CLI）的指定版本上完成隔离探针，调研证据记入 `docs/research/`，后续真实实现验收记入 `docs/acceptance/`。探针覆盖：
    - `none` 档叠加固定名私有查询服务（stdio MCP / Codex `dynamicTools`）时，各家对内置工具、用户外部 MCP（含故意制造的**同名外部工具**）及 Claude `ToolSearch` 的实际行为，结合 ADR-023 第 10 条确定每家 `none` 声明与 Router 资格；
    - 原生根回合事件中查询工具调用的 `callId`、`toolName`、起止相位、错误页（`isError`）、纠偏换根与迟到事件的完整归属表现；
    - 以同模型、同冻结真实样本冷/暖对比旧单次大提示词与新多步查询的端到端延迟、token 用量与前缀缓存命中率，核实实际查询步数与缓存收益（不预设“2–3 次查询必省时”）。

## 考虑过的方案

- **仅压缩单条提示词（删 Wilson 区间、动态 `resultTime` 与摘要裁剪）。** 最强理由是改动最小、保持单轮无工具调用、延迟最低。未采用：领域树、同型数量、回合与续接证据总量仍无界增长，压缩只能推迟撞墙时间且违背不截断原则。
- **新增第六个权限档位（如 `query-only`）。** 最强理由是在字面上区分“纯文本零工具”与“仅会话查询”。未采用：字面零工具档位退化为无状态普通 API 调用，缺乏独立可用性；新增档位会扩张全矩阵与界面复杂度，而将 `none` 明晰为“无原生/外部工具，允许角色自有只读会话查询”与 Worker 私有会话服务完全一致。
- **使用每次随机的 MCP 服务名隔离外部碰撞。** 最强理由是名称天然唯一、省去应答签名核对。未采用：随机服务名使工具定义前缀每次变化，破坏提示词前缀缓存；复用已有 `session_receipts` 的单次应答 HMAC 凭据既保持固定服务名可缓存，又实现更强的逐调用双向归属与已读覆盖核验。
- **保留维护面轮内直接落库，失败时尝试逻辑补偿回滚。** 最强理由是改动现有 `Maintenance.answer()` 与旧测试最少。未采用：领域树合并、统计累加与已读标记涉及多表不可逆归并，工具违规则必须保证零副作用；改为轮内校验暂存、终判原子发布与画像面机制统一，彻底消除半生效脏状态。
- **允许 `domainNodeId` 不存在时进入格式纠偏。** 最强理由是去 `enum` 后可能挽回偶发拼写错误。未采用：查询工具直接返回规范 `nodeId`，领域树不应为模型幻觉兜底，保持标识越界统一硬拒绝更干净。
- **同步新增 Schema 43 将 `router_requests.snapshot_json` 外置出数据库或拆分已读来源列。** 最强理由是减小 `board.sqlite3` 体积并逐行区分历史整包与新查询已读。未采用：SQLite 单行冻结快照目前并非瓶颈，留在库内保障审计与宏任务删除的事务原子性；`kind_summary_evidence` 的语义保持“已处理”，实读覆盖作为新执行的准入证据即可成立。未来若实测确有存储结构需求，再立显式 Schema 43，绝不为此绕路。

## 影响

**收益：**

- 路由面与维护面的提示词、输出 Schema 与 `execution-start.json` 均转为 O(1) 有界，静态前缀与固定服务名工具定义均稳定可命中前缀缓存；同时消除了 `routingBoundary` 内 `facts.buddies` 的双份冗余。
- 消除维护面“轮内提前写入、终态工具违规无法回滚”的 P0 语义漏洞，并与画像面统一采用“轮内暂存、终判原子发布”。
- 审阅与快速路由共用同一套冻结查询与 `protocol/` 单一校验真源，修复工具违规被坏格式掩盖、文件引用带行号漏纠偏、新会话纠偏缺上下文等真实缺陷，并移除 `reselected` 冗余字段。
- 跳过窗口精确处理混合错序与并发认领次序，单次偶发格式错不再误封健康首选 Router，也不会错误卡住并发排队请求。
- `none` 权限档位语义统一且具备真实实用性，Router 查询资格与 Worker 权限彻底解耦。

**代价：**

- 快速路由从单轮文本回复变为多步只读查询往返，端到端延迟与 token 成本的真实表现须以 Track 0 冷/暖原生探针实测为准，可能因模型与提示词缓存命中率而异。
- 维护面 `Maintenance.answer()` 改为终判原子发布后，须同步更新原本断言“第 0 轮即落库”的维护面单测。
- `ToolEventEvidence` 升至版本 4、Router 协议升至版本 20、契约升至 `0.72.0`，升级安装需等待黑板空闲（跨契约安装语义）；`none: "convention"` 的 harness 仍依赖事后 `toolEvidence` 判定作废。
- 去掉输出 Schema 全量 `enum` 后，依赖词表级受限解码的提供方失去词表硬约束，偶发拼错将按硬越界终止该次调用（但可被探针与健康记录清晰观测）。
