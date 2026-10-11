# ADR-021 L18：升级基点存储清点与夹具

本记录只交付微任务 1，未实现升级、未改产品代码、未安装、未读取日常看板、未运行模型或完整检查；Worker 的验证事实不是 Host 的验收结论。冻结基点 B 为 `e4fbc91696f354991252ea2abddc73d45f286aa5`（0.27.0），冻结输入 C 为 `df9318f38b732681e1004cf190e71bb4bc25fe17`（0.29.0）。依据是 [执行计划](adr021-l18-upgrade.md)、[失败演练](../acceptance/c-two-074-host-review.md#从已安装的版本升级)、AGENTS.md、CONTEXT.md、[architecture](../reference/architecture.md) 与 ADR-007；以这两个固定提交的源码取证，不以运行中的安装推断。

下文 `B:path:line`、`C:path:line` 指相应固定提交的文件位置，可以用 `git show <完整提交>:<path>` 读取。SQL 附表中 `B:D` 是 B 的 `src/buddy/db.py`，`C:D` 是 C 的 `src/hey_my_buddy/blackboard/store/db.py`。新增对象在 B 不存在时，给出其所在旧分区或旧写入点，明确标记“无”，不伪造旧定义。机器可读的完整定义、列类型/默认值/非空/主键/隐藏标记、外键删除动作、显式与自动索引的列/表达式/排序/部分条件和视图在 [storage-inventory.json](../../tests/fixtures/adr021-l18/storage-inventory.json)；所有对象都保留两个提交的来源。

## SQL 形式：计划中的四项完整，另有文档计数差异

静态执行两份源码的 SQL 常量到各自内存 SQLite，再比较 `sqlite_master`、`table_xinfo`、`foreign_key_list`、`index_list`、`index_xinfo`；不调用当前产品的建库或打开旧看板。B 有 49 张业务表、114 个索引（含自动索引）、1 个视图；C 有 50 张业务表、117 个索引、1 个视图；两者另有 SQLite 自身的 `sqlite_sequence`。没有表/列/索引/视图消失，既有表的列名、类型、默认值、非空、主键与外键均未改变。差异是 1 张新表及其自动主键索引、2 个新显式索引、1 个部分索引条件、1 个 CHECK 条件；其余 SQL 定义逐字相等（不计源码注释）。architecture 写的“49 tables”比 C 的实际业务表数少 1，供 Host 修订说明，不属于另一项 schema 换算。

| 变化 | B 的具体位置 | C 的具体位置 | 升级处理与保留边界 |
| --- | --- | --- | --- |
| 新建 `model_facts` | `B:src/buddy/db.py:588`（`model_concurrency` 后直接到 `effective_preferences:615`，无此表） | `C:src/hey_my_buddy/blackboard/store/db.py:634` | 显式创建空表，不联网补事实。列为 adapter/provider/model/status/facts_json/identity_json/source/payload_sha256/fetched_at/reason；复合主键 `(adapter,provider,model)`，status 仅 current/unknown；两 JSON 列默认 `{}`，无外键。 |
| 新自动索引 `sqlite_autoindex_model_facts_1` | 同上，无 | 同上，新表主键 | 随表由 SQLite 建出；它是索引计数增加的第三个，不是漏掉的一条独立 DDL。 |
| 新 `events_router_outcome_idx` | `B:src/buddy/db.py:208` 后无此索引 | `C:src/hey_my_buddy/blackboard/store/db.py:217` | 显式建索引；以受 json_valid 保护的 payload.profileId 表达式及 seq 索引 answered/no_answer，不改历史 events。 |
| 新 `events_router_claimed_idx` | 同上，无 | `C:src/hey_my_buddy/blackboard/store/db.py:220` | 显式建索引；同一表达式，条件为 router.claimed，不回填声称旧 Router 被调用的事件。 |
| `events_review_seq_idx` 条件增加 workflow.review_rejected | `B:src/buddy/db.py:571` | `C:src/hey_my_buddy/blackboard/store/db.py:584` | 显式重建同名索引，保留所有事件及 seq。原条件为 task.accepted/task.rejected/workflow.acknowledged；IF NOT EXISTS 不能替换已有定义。 |
| `workflow_host_conclusions.execution_status` CHECK 增加 delivered | `B:src/buddy/db.py:1054`（条件在 1060） | `C:src/hey_my_buddy/blackboard/store/db.py:1090`（条件在 1096） | 显式更换约束；保留该表全部列与记录、主键/外键/索引，不把 delivered 微任务自动写成 Host 结论。旧值 failed/cancelled 仍合法。 |

`SCHEMA_VERSION=15` 与 `PREVIOUS_SCHEMA_VERSION=14` 在 B `src/buddy/db.py:21` 和 C `src/hey_my_buddy/blackboard/store/db.py:22` 不变；两份 initialize 都执行 IF NOT EXISTS（B:1171，C:1202），所以版本号相等不能证明形式相等。待实现的 15→16 必须在当前打开器之前执行，不能用现有打开器生成这些变化再称它为显式升级。

## meta：全部写入族及版本值

下面覆盖产品中写入 meta 的所有键族。`Database.set_meta`（B `src/buddy/db.py:1287`；C `src/hey_my_buddy/blackboard/store/db.py:1318`）是任意键的低层 API，不另造一组固定键；CLI、console 与读取投影引用这些键不代表增加了新存储。来源文字与版本常量也保存在机器清单中。

| 键或键族 | B 证据 | C 证据 | 形式及处置 |
| --- | --- | --- | --- |
| schema_version / created_at / capability_secret | `src/buddy/db.py:1185,1188,1214` | `src/hey_my_buddy/blackboard/store/db.py:1216,1219,1245` | schema 15，创建时间字符串，32 字节 hex 密钥；形式不变。升级只改 schema 标记，保留创建时间与密钥。夹具密钥固定 ab×32。 |
| router_configuration_version | `src/buddy/router.py:188` | `src/hey_my_buddy/blackboard/routing/router.py:23,237` | **1→3**，不是只改 marker。只有换算完成后才能记 3。中间版本 2 不是本升级基点，不新增 2 的兼容入口。 |
| router_fast_profile_id / router_review_profile_id | `src/buddy/router.py:22,183` | `src/hey_my_buddy/blackboard/routing/router.py:24,212` | B 两槽字符串（空串代表未设置）；C 不再读。换算至下行有序数组：默认模式对应槽先行，另一槽其次，保留不可用 ID、去重、丢弃空槽。旧设置键是否作为换算证据留存应由 Host 明确，不能在读时继续兼容它们。 |
| router_profile_ids / router_retry_interval_seconds | 上行 B 两槽；无列表和重试键 | `src/hey_my_buddy/blackboard/routing/router.py:24,194`；`src/hey_my_buddy/blackboard/routing/router_settings.py:20,114` | 新 JSON 数组和整数字符串；转换 helper 默认 retry=600。纯 helper 返回 source_slots，可供显式换算记录两槽证据。 |
| router_default_mode / router_budget_preset | `src/buddy/router.py:22,128` | `src/hey_my_buddy/blackboard/routing/router.py:24,130` | fast/review 与 brief/standard/deep 形式相同。旧 quick 在 B 读时映射 brief，在 C helper `router_settings.py:126` 显式映射；升级须将 quick 存量变成 brief，不能依赖读时投影。 |
| catalog-accounts:<observation_id> / catalog-harnesses:<observation_id> / catalog-account:<adapter> | `src/buddy/catalog_store.py:13,21,95` | `src/hey_my_buddy/blackboard/catalog/catalog_store.py:13,21,115` | 冻结账户字典、健康记录数组、所选 source/credentialRevision，编码不变；原样保留观察绑定与历史事实。新选择 revision 的 binding_key（C accounts.py:68）是读时计算，不增加持久化键。 |
| **新** catalog-pending:<adapter> | `src/buddy/catalog_store.py:100`（完整读取立即退休缺席模型；无此键） | `src/hey_my_buddy/blackboard/catalog/catalog.py:61,213,226,242`；`catalog_store.py:151` | 每 provider/model 对应 `{since,efforts}`，没有版本字段；不存在时是空 map。不能把旧 unavailable 认作“已被两次可信读取确认缺席”。升级须明确当前目录状态的初始化/失效策略，不改历史观察和用户 enablement。C 的 `_pending_entry` 还接受旧时间字符串，而 B 从未有此键：这是中间开发历史的读时换算，按 0.x 规则应由 Host 纳入消除范围，不能成为本升级的新兼容层。 |
| **新** catalog-read-at:<adapter> / catalog-account-status:<adapter> / catalog-scan-after:<adapter> | `src/buddy/catalog_store.py:95,98`（只存当前 payload/time，无账户可信状态和扫描窗） | `src/hey_my_buddy/blackboard/catalog/catalog.py:62,164,299,379,387,395` | 时间字符串、confirmed/unknown/not-applicable 字符串、扫描窗时间。历史 complete 不证明账户 confirmed，不能回填虚构的可信读取；应明确 unknown 初始化与后续显式真实发现的边界。缺少键的当前读取也是 unknown。 |
| **新** router-request:<decision_id> / router-dispatch:<task_id> / attempt-router:<attempt_id> | `src/buddy/decision.py:745`（写 decision_requests；无这些键） | `src/hey_my_buddy/blackboard/routing/router_sequence.py:19,49,97,136` | 无版本字段的不可变请求/分派/claim JSON，包含 facts/baseInput/inspections、四元身份、输入摘要、Router 索引和 attempt 绑定。基点公开操作产生的非终态决策绑定活动内部 task，不能通过升级 idle 边界（证据见下节）；合法空闲看板中的旧终态请求、结果和事件原样保留，不补造新分派或收据。 |
| **新** router-outcome:<sha256> | `src/buddy/decision.py:1366`（旧 decision 终态事件；无 receipt） | `src/hey_my_buddy/blackboard/routing/router_history.py:36,100,145` | `{payload,seq,recordedAt}`，无版本字段；新增 Router 事实的事务内幂等收据。保留旧 decision.* 事件作历史证据，不补造 answered/no_answer/claimed。 |
| account-selection:<adapter> / account-credential:<[adapter,source]> / attempt-account:<attempt_id> | `src/buddy/accounts.py:60,75` | `src/hey_my_buddy/blackboard/catalog/accounts.py:60,88` | source/revision、credentialRevision 整数、冻结账户字典，形式不变；保留。 |
| account-operation:<uuid> / account-mutation:<[adapter,source]> | `src/buddy/accounts.py:98,309` | `src/hey_my_buddy/blackboard/catalog/accounts.py:111,328` | active/uncertain 与代际 JSON 形式不变；不是可清除的历史噪声。升级 idle 检查拒绝未停机记录（B upgrade.py:65；C install/upgrade.py:65）。 |
| account-quota:<账户桶> / quota-routing:<桶> / quota-retry:<桶> | `src/buddy/native_observations.py:18,37`；`quota_routing.py:73,95` | `src/hey_my_buddy/blackboard/evaluation/native_observations.py:18,37`；`routing/quota_routing.py:73,95` | 配额观察与重试标记形式不变，保留。旧 native exhaustion 的延迟导入 helper 在两提交均为 quota_routing.py:269 起，不是本次新增换算。 |
| account-read-at:<账户桶> / codex_account_read_at | `src/buddy/harness_health.py:230,237` | `src/hey_my_buddy/blackboard/service/harness_health.py:365,372` | 时间字符串，旧 native 桶的读取保留在 C，未变形。不要拿账户查询时间当新 catalog 可信读取时间。 |
| harness-review:<[adapter,version,platform,...]> | `src/buddy/harness_review.py:21,143,186` | `src/hey_my_buddy/buddy/harnesses/base.py:160,189`（C 无旧证书读写模块；能力为声明） | 退休的付费 review 证书 JSON 不再被当前代码消费。保留存量为历史证据；清理属于 L15，不在本轮偷偷删除。夹具没有运行证书探针，旧 eligibility 使用 B 自带静态证书。 |
| **新** workspace-identity:<old_digest> / workspace-identity-migration | `src/buddy/workspace.py:73`（path/device/inode 摘要；无映射） | `src/hey_my_buddy/blackboard/tasks/workspace_identity.py:18,31,62`；`store/workspace_identity_migration.py:242,294` | version=1 的 alias 与证明/kept 原因；详见下节。这一项已经有独立换算，不是 SQL 新表；L14 仍负责将结构化 meta 改成表。换算校验导入的 SCHEMA_VERSION，随当前 schema 常量变化；没有写死 schema 15 的执行条件。 |

新增 Router 请求/分派/收据与 catalog pending 是结构化 meta 值（来源见上表），不只是新增可选标量；AGENTS.md 的“有结构的记录应有自己的表”只明示保留工作区身份别名这个例外。这组新增存储与该不变量的冲突也交给 Host 修订计划，不能由本清点默认赋予新的例外，也不能在第二微任务只初始化缺键便称所有形式债务已解决。

计划外必须由 Host 决定的是模型目录的当前可信状态初始化；新增 JSON 和键族也应明确加入形式一致性守门的覆盖规则。旧非终态 Router 请求不是本升级 idle 边界内的换算存量，撤回此前要求另定处置的建议。只有机械定义相同的 meta 键值参与一致性比较：密钥、创建时间、账户代际、旧事件、目录观察和各任务收据是数据，不能要求它们与空的新看板逐值相等，更不能为达到“形式相等”而清空。

## 旧 Router 请求与升级空闲边界

这里核对的是 B 的公开创建和状态转换路径能否产生通过 `upgrade.idle_snapshot` 的非终态存量，不凭 SQL 列允许 NULL 就推断公开操作能产生孤立决策，也没有向夹具添加 Router 事实。B `src/buddy/decision.py:29,30` 与 C `src/hey_my_buddy/blackboard/routing/decision.py:31,32` 的非终态都只有 queued/running；needs-host 已是终态，表示请求结束后留给 Host 判断，不表示一个可续跑的 Router 调用。

| 基点路径 | B 的具体位置及状态关系 | C 对应位置 |
| --- | --- | --- |
| 创建 | `src/buddy/decision.py:633,663,696,712,721,745`：仅 select 创建路径；单候选直接 completed，无候选、无 Router、显式需要 Host 或输入超限变成 needs-host；仍为 queued 时在同一写事务调用 _create_task，并将 task_id 写入 decision_requests。`decision.py:830,847,865`：内部 task 的 owner=decision、adapter=decision、state=queued。 | `src/hey_my_buddy/blackboard/routing/decision.py:606,639,781,855,985,1007`：新路径在事务内排队独立 dispatch 并绑定 task_id；这是新写入路径，不能倒填旧请求。 |
| 领取 | `src/buddy/decision.py:889,955,961,982`：claim 仅接受 queued，_mark_running 绑定 attempt/generation 并置 running；`src/buddy/store.py:1627,1673,1732` 在同一领取事务建 attempt 并把关联 task 置 running。阻塞领取不关闭任务；无冻结输入等拒绝情况以 needs-host/stale 结束决策并取消排队 task（`decision.py:909,932,937,944,949`）。 | `src/hey_my_buddy/blackboard/routing/decision.py:1150,1163,1231,1243,1264`：领取同样校验并绑定当前 attempt，状态置 running。 |
| 旧 review→fast fallback | `src/buddy/decision.py:198,213,215,1174`：仅确认停机的 pre-model 失败可 fallback；decision 重置 queued 的同时，原 task 也转回 queued，没有“终态 task 配 queued decision”的已提交窗口。 | `src/hey_my_buddy/blackboard/routing/decision.py:991,1007,1011`：新 Router 序列由新 task/dispatch 承接；不会给历史请求制造这条证据。 |
| 结束、取消、释放、失联 | `src/buddy/store.py:2437` 与 `decision.py:1084,1144,1363`：结果和决策终态在同一事务提交，fallback 是上行的例外；`store.py:1152,2557,2922,2974` 调用 `decision.py:1041,1025,1056`，分别将取消、释放、重启/租约失联决策置 cancelled、failed、stale。未确认停机的 attempt 仍阻止升级。 | `src/hey_my_buddy/blackboard/routing/decision.py:1307,1329,1344,1373,1433,1734`：相应释放、取消、fence、complete、_finish 路径仍保留终态与历史证据。 |
| 空闲边界 | `src/buddy/upgrade.py:54,60,61,65`：拒绝 queued/running/cancelling/reconciliation-needed task，以及 execution_state 非 finished 或 shutdown_confirmed 非 1 的 attempt；没有自动取消 Router。 | `src/hey_my_buddy/install/upgrade.py:57,63,64,68`：相同 task/attempt 停机条件。 |

由上述创建和转换路径，B 的 queued/running Router 决策必然伴随 idle 检查拒绝的内部 task 或未停机 attempt；没有找到基点公开操作产生且能通过该边界的反例。通过边界的旧 Router 请求按终态历史保留，包括 needs-host 的冻结输入、proposal/output 和 decision.* 事件，不重编码、不补 router-request/dispatch/claim/outcome，不增加历史请求续跑或停止换算。此结论是固定提交的静态路径核对，不是一次真实 Router 执行验收；手工篡改 SQL 或更早开发版本的孤立行不属于本基点公开操作生成的合法存量。

## JSON 列及带版本结构

全部 JSON 列的名字、SQL 类型、默认值与双提交定义位置在文末附表与机器清单中；既有 JSON 列无 SQL 定义变化。不是每一条业务 JSON 都带版本：卡片、meta pending、Router 收据与 catalog payload 等不能把 schema 15 误当作它们自己的版本字段。

| 结构 / 存放处 | B 具体位置 | C 具体位置 | 换算还是保留 |
| --- | --- | --- | --- |
| `workflow_turns.input_json.version` / `outcome_json` 与 `attempts.result_json` 中的 turn.version | `src/buddy/workflow.py:76,4833,5611` | `src/hey_my_buddy/blackboard/tasks/workflow.py:82,5778,6605`；`buddy/roles/turn_io.py:239` | 输入/结果 version 均为 1。旧冻结输入、结构化结果、native provenance 原样保留；后续新回合由新代码重新生成输入，不将历史证据伪装成新 harness 执行。 |
| version=1 输入 context 新增 sharedStash，以及结果内 version=1 的共享引用比较 | `src/buddy/workflow.py:5313`；`workspace.py:839`（无该证据） | `src/hey_my_buddy/blackboard/tasks/workflow.py:5896,6867,6890`；`tasks/workspace.py:1351,1401,1441` | 新证据仅属于实际被新代码观察的回合。旧行缺少字段代表没有当时的观察，保留缺失，不能在升级时检查仓库后回填历史。 |
| `workflow_runs.execution_workspace_json`、`workflow_artifacts.manifest_json`、turn 输入/结果中的 manifest 与 output.version=1 / exclusionPolicy.version=1 | `src/buddy/workspace.py:27,73,524,636,839` | `src/hey_my_buddy/blackboard/tasks/workspace.py:29,137,816,1007,1248`；`tasks/workspace_identity.py:31` | 容器仍 version=1，但身份摘要从 `[path,device,inode]` 变为 `[path,inode]`；旧 immutable manifest/摘要/输出不能重写。已存在的独立 identity migration 仅改 objectives.project_id、workspace_reservations 身份投影及新增证明 meta，留原始证据并为不证明的旧身份记录 kept 原因；无真实 inode 的脱敏夹具不能证明身份迁移。 |
| Router 提示文本版本（不是 JSON 版本字段）、decision_requests.input_json / requested_json / output_json、events.payload_json 中 routing facts | `src/buddy/router.py:10,219`；`decision.py:621,745` | `src/hey_my_buddy/blackboard/routing/router.py:11,353`；`src/hey_my_buddy/blackboard/routing/decision.py:622,951`；`router_sequence.py:49,97` | PROMPT_VERSION **10→11**，冻结输入 JSON 没有 promptVersion 字段；由双槽及 fallback 改为列表/索引/重试窗/独立分派。历史冻结请求、input_sha256、answer/evidence/decision.* 不重新编码或归因。新请求用 11。旧非终态请求被升级 idle 边界排除，旧终态历史保留，见前节证据。 |
| `evaluation_catalog.payload_json.discoveries[*]` 与 catalog 当前投影 | `src/buddy/catalog.py:49,109`；`catalog_store.py:90` | `src/hey_my_buddy/blackboard/catalog/catalog.py:423,481`；`catalog_store.py:109` | 旧无 accountStatus，新有 accountStatus；catalog payload 无显式格式版本。旧 observation/discovery_id 的哈希与 payload 不能改写；在显式升级中给当前可信状态建立诚实的 unknown 边界。后续读取模型目录仍应读出旧 providers/models/efforts，不能把它们删除当换算。 |
| `attempts.token_usage_json.version=1`、`harness_health.quota_json.version=1`、result.quotaFailure.version=1 / lastAssistantMessage.version=1 | `src/buddy/usage.py:42,204,325,402,433`；`native_observations.py:60` | `src/hey_my_buddy/protocol/usage.py:39,196,317,394,425`；`blackboard/evaluation/native_observations.py:60` | 编码与 version 均不变，normalize 只迁移模块；历史观察、未知 NULL、时间与代际保留。不把移除 activity/native-usage sidecar 的传输路径误当 SQLite JSON 换算。 |
| Codex continuation checkpoint.version=1，存于 attempts.result_json，包含绑定与原生 thread/turn 证据 | `src/buddy/adapters/codex_protocol.py:187,207` | `src/hey_my_buddy/buddy/roles/turn_io.py:282` | 版本与身份绑定意义未变；历史保留，不在本轮补造 checkpoint 或验证原生账户。 |
| `attempt_activity.activity_json`、message question/answer JSON、卡片/卡片历史/评价证据、用户/家族偏好、Host conclusion.evidence_json | `src/buddy/activity.py:78,127`；`db.py:276,491,1054`；`host_conclusions.py:9` | `src/hey_my_buddy/protocol/activity.py:52,107`；`blackboard/store/db.py:277,504,1090`；`tasks/host_conclusions.py:9` | 活动正文不是 sidecar 容器，既有正文和结构化评价/证据格式未改；`artifactSetSha256` 仍绑定固定 artifact。保留原文、历史、关联与 Hash；delivered 的新结论只由 Host 的后续公开操作写入。 |
| model_facts.facts_json / identity_json | `src/buddy/db.py:601`（无） | `src/hey_my_buddy/blackboard/store/db.py:634`；`evaluation/model_facts.py:364` | 新表的新 JSON，无 schemaVersion 包装；只是未来获取的数据，升级建空表。 |

存储之外的原生侧文件/传输版本有变化（DSH 原生运行记录由 C 读取 version=3、共享 run contract format=1、toolEvidence.version=1、activity/native-usage sidecar 退休），但没有借此增加 SQLite 的历史换算要求：B `src/buddy/adapters/zcode_runner.py:37`、`usage.py:70`、`activity.py:33`；C `src/hey_my_buddy/buddy/harnesses/dsh/session_records.py:37`、`run_contract.py:55`、`protocol/tool_evidence.py:27`。原生执行文件不能靠升级改装成新执行证据；本夹具不含真实原生账户或历史会话目录。

## migrations.py 的所有历史换算与调用方

两提交的文件内容相同，位置分别是 B `src/buddy/migrations.py` 与 C `src/hey_my_buddy/blackboard/store/migrations.py`；引用 db 常量随模块搬迁，所以 `migrate_14_to_15` 在 C 建出的 Host conclusion CHECK 已含 delivered。这不是 B→C 的显式换算。

| 定义（两个文件同一行） | 完整作用 | B 生产调用方 | C 生产调用方 |
| --- | --- | --- | --- |
| migrate_12_to_13:71 | 以 family 合并相同 effort 偏好，不同的保留 override；合并 notes（不同文本附 effort 来源）；换 preferences CHECK 加 none；移除 evaluation_annotations，建立 family_* 表、effective_preferences；marker 12→13，事务与 FK/integrity。 | `src/buddy/board_prepare.py:176` | `src/hey_my_buddy/blackboard/store/board_prepare.py:176` |
| migrate_13_to_14:139 | 新建无 quota_json 的空 harness_health，marker 13→14；不发现 harness、不改用户偏好。 | `src/buddy/board_prepare.py:177` | `src/hey_my_buddy/blackboard/store/board_prepare.py:177` |
| migrate_14_to_15:178 | workflow_runs 加 configuration_locked 默认 0；attempts 加 token_usage_json；harness_health 加 quota_json；Host conclusion 表/索引；只保留扩展表旧列并验摘要；marker 14→15。 | `src/buddy/board_prepare.py:178`、`upgrade.py:309`、`backup.py:436` | `src/hey_my_buddy/blackboard/store/board_prepare.py:178`、`install/upgrade.py:309`、`blackboard/store/backup.py:438` |

没有无调用方的历史换算可由本微任务宣称删除。12→13、13→14 都由单独离线 `board_prepare.prepare`（B/C 相应文件:116 起）消费：它接受 schema 11，复制新数据库，先加 objectives/活动投影并设为 12（:171），再串接 12→13→14→15。若 Host 按 ADR-007 的 current-only 原则退休该辅助路径，应一起删除调用链与对应测试，而不能只删函数留下坏入口；本轮无删除权限，也不改这条产品路径。

helper 与常量：schema_version:58 被三个换算函数和迁移测试调用；_families:64 仅被 12→13 调用；retained_columns:160 被 14→15 内部、upgrade.migrate_board 的前后检查（B upgrade.py:306,316；C install/upgrade.py:306,316）及 Host migration 测试调用；MIGRATED_TABLES_13:15 被 board_prepare 的未触及表指纹检查消费，MIGRATED_TABLES_14:17 在两棵产品源码及测试中只有定义，没有调用，MIGRATED_TABLES:18 被 upgrade.migrate_board 消费；_STATEMENTS:20 / _VIEW:46 只由 12→13 消费。没有函数被普通启动直接调用。backup.verify 的 schema-14 试恢复也调用 14→15，不能漏改这一处。

测试直接调用方：B `tests/python/test_migrations.py:45`、`test_harness_migration.py:8`、`test_host_migration.py:24`、`test_upgrade_migration.py:58,95`；C `tests/python/blackboard/store/test_migrations.py:45`、`tests/python/install/test_harness_migration.py:8`、`test_host_migration.py:24`、`test_upgrade_migration.py:58,95`。board_prepare 自身的行为测试为 B `tests/python/test_board_prepare.py` / C `tests/python/blackboard/store/test_board_prepare.py`；本轮未运行或修改这些测试。

必须审视的升级调用合同是 C `src/hey_my_buddy/install/upgrade.py:303,318` 对 schema 之外全部 meta 逐值不变的检查，B `src/buddy/upgrade.py:303,318` 也有该检查；它与两槽换算及新增目录状态不能同时成立，Host 须明确允许变化的键和值。身份换算的校验则没有固定 schema 15 的阻塞：C `src/hey_my_buddy/blackboard/store/workspace_identity_migration.py:20` 导入 `.db.SCHEMA_VERSION`，`:322` 比较该常量；固定写着 15 的只是 `:323` 的错误消息。随 db 常量改为 16，该条件检查的就是 16，撤回此前“先执行 15→16 会拒绝”的误诊及其计划修订要求。B `src/buddy/workspace.py:73` 无这个独立换算，C `src/hey_my_buddy/install/upgrade.py:538` 已调用它；仍需在后续真实升级路径验证身份换算，但不能把未运行的验证或错误消息当作实际升级阻塞。L14 的别名表重构范围不变。

## 固定夹具、生成路径与验证边界

固定 artifact 为 [board.sql](../../tests/fixtures/adr021-l18/board.sql)、[metadata.json](../../tests/fixtures/adr021-l18/metadata.json)、[operations.json](../../tests/fixtures/adr021-l18/operations.json)、[catalog.json](../../tests/fixtures/adr021-l18/catalog.json)、[storage-inventory.json](../../tests/fixtures/adr021-l18/storage-inventory.json) 和 [生成脚本](../../packaging/generate-upgrade-fixture.py)。metadata 记录完整基点/输入提交、基点 tree、锁文件和源文件摘要、实际导入的旧模块、生成脚本与 SQL/目录/操作记录摘要、稳定样本标识、固定假值与边界。operations 逐次保留公开操作的输入/响应，明示模拟 Worker 结果。

脚本以 `git archive B` 导出未修改源码，`uv sync --frozen --no-install-project --link-mode copy` 准备基点环境，导入 `buddy` 而不是 `hey_my_buddy`。旧 `Database.initialize` 建 schema 15，旧 BoardService 的命名公开操作经过验证、事务、事件和幂等规则写入：harness_set/model_catalog_refresh、evaluation_write_begin/user_policy_publish/assessment_publish、workflow_submit、worker_register/worker_claim/worker_result、workflow_integration_record/workflow_acknowledge、workflow_cancel。没有手写 INSERT 伪造样本或当前 schema。只有外部 native discovery 边界用固定事实代替；没有原生线程/模型实际运行，结果 provenance 是测试输入，不能作为新 harness 能力的验收证据。两个 Router 分别指向不同模拟模型，两槽均经旧公开设置校验写入，default=review，可验证换算后的顺序为 beta、alpha。

工作区使用仓库中已有的五文件提交 `122ec20edcdeb97cc655ae3993c402c2c9c966a3`，仅用于微任务输入快照，**建库的源码仍全部是 B**。它被导入本轮私有仓库，四个 detached worktree 在同一私有 repository 下，保证同宏任务且独立 checkout；没有创建或移动分支/标签，没有 git commit，也没有修改授权检出的 .git。旧公开 workspace.prepare/seal 产生 refs/buddy 固定快照，不产生新 commit（输入与输出 tree 相等）。脚本保留临时 Git 索引目录，过程数据未清理。

微任务稳定 ID：delivered `00000000-0000-0000-0000-000000000007`，accepted `00000000-0000-0000-0000-00000000000f`，rejected `00000000-0000-0000-0000-000000000017`，cancelled `00000000-0000-0000-0000-00000000001e`。前三个各有 stopped 的模拟 attempt 与完整封存输出；delivered 没有 Host conclusion，accepted 的集成记录为显式 not-required，rejected 的 Host 边界为 awaiting-host。旧 governed API 的验收/打回事件均叫 `workflow.acknowledged`，payload.verdict 分别 accepted/rejected；它没有冒称基点会生成当前新增的 workflow.review_rejected。还有评价、卡片修订、家族备注、模型目录观察/当前指针/可用 profile 和宏任务绑定。

导出通过旧数据库 iterdump，再把本轮工作根替换为 `<fixture-root>`；秘密从首次建库就是固定 ab×32，服务/提交令牌也是固定 fixture 假值。脱敏之后仍原样保留原始 manifest/hash/fingerprint 作为历史字段，没有用当前源码重算旧证据。因路径/设备/inode 的散列由原始旧公开操作产生，样本 ID 稳定不等于跨机器 SQL 字节完全复现。它是看板读取与待结论样本，不是可续跑工作区：文件系统 verify、真实 workspace identity 证明/集成/恢复需另外的私有仓库演练，不能拿占位符证明活目录身份。

生成命令（从授权检出运行，`tmp/adr021-l18/run` 必须不存在，离线运行需先有基点锁定依赖的私有 uv 缓存；缓存未准备时可在允许网络的环境去掉两处 --offline，环境清除前缀还由脚本对子进程统一执行）：

```sh
env -u BUDDY_STATE_DIR -u BUDDY_RUNTIME_ROOT -u BUDDY_RUNTIME -u BUDDY_RUNTIME_IDENTITY -u BUDDY_WORKER_STATE -u BUDDY_WORKER_ID -u BUDDY_AGENT_CREDENTIAL -u BUDDY_AGENT_CREDENTIAL_FILE -u VIRTUAL_ENV -u UV_PROJECT_ENVIRONMENT UV_CACHE_DIR="$PWD/tmp/adr021-l18/uv-cache" uv run --offline --no-project python packaging/generate-upgrade-fixture.py --offline --work-dir tmp/adr021-l18/run --output-dir tests/fixtures/adr021-l18
```

本轮实际最终生成目录为 `tmp/adr021-l18/run6/`；退出 0：49 张业务表、37 条事件、4 个预期状态、schema 15、源 SQL 定义完全一致、integrity=ok、foreign_key_check=[]。首次联网获取依赖被 DNS 限制拒绝；随后只复制基点锁定的五个 uv 包到私有缓存，离线同步成功（c-two 0.6.0、fastdb4py 0.2.1、numpy 2.5.3、portalocker 3.2.0、pyyaml 6.0.3），未安装产品。中间调试分别发现公开 console 身份必须为 24 hex、完整源码工作区捕获太大（主动中止）、同 checkout 不同冻结 manifest 不能共享 read reservation，以及旧 governed 事件名应核验 verdict；均据基点代码修正生成脚本，没有绕开产品验证。

新增测试编号：无；消失测试编号：无。夹具检查是生成脚本的独立 `--verify`，不注册测试或运行现有测试套件。独立复核命令及源码证据结果在同目录 [verification.json](../../tests/fixtures/adr021-l18/verification.json)；未验证 15→16、真实 install/upgrade、私有 daemon/IPC、Host conclusion 写入、真实账户/模型、原生会话续跑、Windows 或日常看板。这些没有伪称已通过，仍属于后续微任务与 Host 验收。

## 交给 Host 的计划修订点

1. 将“模型目录当前状态初始化”加入第二微任务，决定无 accountStatus 的旧 complete 读取如何诚实地保留目录、设定 unknown，并覆盖 pending/read-at/scan-after 及中间 pending 字符串读时换算；不凭旧时间构造可信读取。
2. 旧 Router JSON/事件按终态历史保留，不补造新收据；基点非终态决策的创建和转换路径被 idle 边界排除，不另加续跑/停止换算，也不人为增加 Router 事实。夹具本轮没有执行 Router；此结论由上文固定源码路径核对支持。
3. 处理旧 upgrade 对 meta 逐值不变的检查与 backup 的 14→15 调用；identity migration 的执行条件跟随 SCHEMA_VERSION，不存在写死 15 的阻塞，不再提出该项修复或额外升级顺序要求。后续真实升级仍按既定路径验证身份换算，L14 的别名表重构及 L15 的历史清理范围不变。
4. 根据 AGENTS.md 确定新增结构化 Router/catalog meta 记录应如何落入独立表，而不是默许更多例外；形式一致性测试同时覆盖新增 meta/JSON 结构的声明；原始历史值/Hash 不与空白新看板比较。基点不含的新结构无需制造存量，但新源码不能保留只为中间开发版本服务的读时兼容。

Host 独立验收上述固定 artifact 并修订执行计划后，再授权第二微任务；本 Worker 在此边界停止。

## SQL 全量表与 JSON 列附表

下表由两个提交的 SQL 常量生成；每一行指向两个提交的 CREATE TABLE，完整约束与索引定义以机器清单为准。`—` 表示该表没有 JSON 编码列；非 `_json` 后缀的 tasks.required_capabilities/exclusive_resources、workers.capabilities（B:D:147 / C:D:148，TEXT 默认 `[]`）与 attempts.log_paths（B:D:117 / C:D:118，TEXT 默认 `{}`）也在相应行列出。decision_requests.considered_evidence 则在两提交都是 INTEGER NOT NULL DEFAULT 0（B:D:544 / C:D:557）：B `src/buddy/decision.py:961,965,975,1730` 与 C `src/hey_my_buddy/blackboard/routing/decision.py:1243,1247,1257,2214` 按整数写入/读出，它是计数，不能标为 JSON；runtime_identity 是普通字符串。其余列见同一机器清单的 columns，列顺序也参与核对。

| 表 | 全部列（同顺序，变化已在正文标明） | JSON 编码列 | B / C 定义位置 |
| --- | --- | --- | --- |
| `agent_credentials` | credential_id, run_id, task_id, attempt_id, generation, turn_id, token_verifier, scopes_json, state, created_at, expires_at, revoked_at, revision | scopes_json | B:D:977 / C:D:1013 |
| `artifacts` | artifact_id, task_id, attempt_id, kind, location, content_hash, size_bytes, verified, created_at | — | B:D:184 / C:D:185 |
| `attempt_activity` | attempt_id, task_id, generation, event_seq, observed_at, activity_json, created_at, updated_at, revision | activity_json | B:D:247 / C:D:260 |
| `attempts` | attempt_id, task_id, generation, worker_id, worker_identity, worker_instance, capability_version, nonce_verifier, claim_request_id, lease_expires_at, lease_seconds, execution_state, ownership, runtime_identity, adapter, model_adapter, model_provider, model_model, log_paths, result_json, result_command_id, error, shutdown_confirmed, cancel_requested_at, exit_code, signal, started_at, finished_at, created_at, updated_at, revision, token_usage_json | log_paths, result_json, token_usage_json | B:D:92 / C:D:93 |
| `catalog_current` | adapter, observation_id, discovery_id, status, reason, updated_at | — | B:D:272 / C:D:285 |
| `catalog_observations` | observation_id, request_id, created_at, completed_at, response_json | response_json | B:D:265 / C:D:278 |
| `commands` | command_id, task_id, attempt_id, kind, request_hash, response_json, subject, created_at | response_json | B:D:211 / C:D:224 |
| `cursors` | consumer, seq, updated_at | — | B:D:236 / C:D:249 |
| `decision_requests` | decision_id, request_id, kind, input_fingerprint, configuration_revision, task_id, attempt_id, generation, reader_id, writer_id, writer_generation, expected_revision, published_revision, selected_json, input_json, input_sha256, output_json, proposal_json, considered_evidence, pending_after, auto_publish, requested_json, created_at, updated_at | selected_json, input_json, output_json, proposal_json, requested_json | B:D:525 / C:D:538 |
| `evaluation_aggregates` | name, value, updated_at | — | B:D:514 / C:D:527 |
| `evaluation_card_history` | table_revision, profile_id, card_revision, summary, strengths_json, limitations_json, risks_json, evidence_ids_json, sample_count, published_at | strengths_json, limitations_json, risks_json, evidence_ids_json | B:D:491 / C:D:504 |
| `evaluation_cards` | profile_id, revision, summary, origin, strengths_json, limitations_json, risks_json, evidence_ids_json, sample_count, updated_at | strengths_json, limitations_json, risks_json, evidence_ids_json | B:D:333 / C:D:346 |
| `evaluation_catalog` | discovery_id, discovered_at, source, harness_version, provider_version, payload_json, created_at | payload_json | B:D:394 / C:D:407 |
| `evaluation_decisions` | decision_id, status, task, profile_id, table_revision, reason, evidence_ids_json, created_at, error | evidence_ids_json | B:D:381 / C:D:394 |
| `evaluation_evidence` | evidence_id, profile_id, kind, summary, project, conditions_json, source, run_id, verified, counted, identity_json, recorded_revision, created_at | conditions_json, identity_json | B:D:360 / C:D:373 |
| `evaluation_evidence_pending` | evidence_id, profile_id, created_at | — | B:D:445 / C:D:458 |
| `evaluation_maintenance_checkpoints` | scope, review_seq, updated_at | — | B:D:575 / C:D:588 |
| `evaluation_preferences` | profile_id, mode, reason, updated_revision | — | B:D:351 / C:D:364 |
| `evaluation_profiles` | profile_id, label, adapter, provider, model, effort, available, enabled, capabilities_json, context_window, description, source, unavailable_reason, created_revision, updated_revision | capabilities_json | B:D:314 / C:D:327 |
| `evaluation_readers` | reader_id, kind, admitted_at, expires_at, released_at, expired | — | B:D:405 / C:D:418 |
| `evaluation_revisions` | revision, kind, writer_id, actor, counts_json, created_at | counts_json | B:D:304 / C:D:317 |
| `evaluation_samples` | profile_id, attempt_id, task_id, verdict, evidence_id, created_at | — | B:D:473 / C:D:486 |
| `evaluation_state` | id, table_revision, configuration_revision, decision_profile_id, writer_sequence, created_at, updated_at | — | B:D:293 / C:D:306 |
| `evaluation_writers` | writer_id, request_id, kind, state, generation, expected_revision, token_verifier, requested_at, granted_at, expires_at, released_at | — | B:D:418 / C:D:431 |
| `events` | seq, task_id, attempt_id, revision, kind, payload_json, created_at | payload_json | B:D:198 / C:D:199 |
| `family_annotations` | adapter, provider, model, text, revision, updated_at | — | B:D:282 / C:D:295 |
| `family_preferences` | adapter, provider, model, mode, reason, updated_revision | — | B:D:602 / C:D:615 |
| `harness_health` | adapter, manual_path, revision, status, record_json, checked_at, expires_at, scan_after, quota_json | record_json, quota_json | B:D:1038 / C:D:1074 |
| `messages` | message_id, task_id, attempt_id, inquiry_id, direction, author, recipient, correlation_id, body, body_bytes, payload_hash, state, reason, delivery_json, answer_json, attempts_count, created_at, updated_at, revision | delivery_json, answer_json | B:D:158 / C:D:159 |
| `meta` | key, value | — | B:D:58 / C:D:59 |
| `model_concurrency` | adapter, provider, model, concurrency_limit, updated_revision, created_at, updated_at | — | B:D:588 / C:D:601 |
| `model_facts` | adapter, provider, model, status, facts_json, identity_json, source, payload_sha256, fetched_at, reason | facts_json, identity_json | B 无（D:588 后） / C:D:634 |
| `objectives` | objective_id, title, project_id, project_path, source_host_id, created_at, activity_seq, activity_at | — | B:D:635 / C:D:671 |
| `resource_claims` | claim_id, task_id, attempt_id, resource, kind, state, created_at, released_at | — | B:D:223 / C:D:236 |
| `tasks` | task_id, request_id, owner, spec_json, spec_canonical_json, input_fingerprint, fingerprint_version, adapter, required_capabilities, cwd, exclusive_resources, timeout_seconds, state, queue_reason, revision, selected_attempt_id, active_attempt_id, created_at, updated_at, accepted_at, acceptance_note, acceptance_verdict, queue_position | spec_json, spec_canonical_json, required_capabilities, exclusive_resources | B:D:63 / C:D:64 |
| `workers` | worker_id, identity, adapter, capabilities, host, pid, state, current_attempt_id, registered_at, last_seen_at, revision | capabilities | B:D:143 / C:D:144 |
| `workflow_artifacts` | artifact_id, run_id, turn_id, attempt_id, source_task_id, kind, manifest_json, manifest_sha256, created_at | manifest_json | B:D:1003 / C:D:1039 |
| `workflow_children` | child_task_id, parent_run_id, request_id, decision_command_id, role, integrator, state, auto_continue, workspace_intent_json, workspace_manifest_json, workspace_manifest_sha256, created_at, updated_at, revision | workspace_intent_json, workspace_manifest_json | B:D:769 / C:D:805 |
| `workflow_continuations` | continuation_id, run_id, command_id, authorized_by, request_id, expected_revision, input_text, input_bytes, reason, helper_policy, helper_outcomes_json, workspace_manifest_json, state, attempt_id, turn_id, created_at, consumed_at | helper_outcomes_json, workspace_manifest_json | B:D:793 / C:D:829 |
| `workflow_host_conclusions` | conclusion_id, run_id, attempt_id, run_revision, owner_generation, execution_status, note, evidence_json, artifact_id, integration_id, actor, command_id, created_at | evidence_json | B:D:1054 / C:D:1090 |
| `workflow_integrations` | integration_id, run_id, artifact_id, attempt_id, state, strategy, binding_sha256, target_kind, target_path, target_ref, target_repository_id, target_checkout_id, source_commit, source_tree, before_commit, after_commit, before_tree, after_tree, verification_json, reason, command_id, actor, created_at | verification_json | B:D:914 / C:D:950 |
| `workflow_requests` | request_id, run_id, turn_id, attempt_id, child_task_id, kind, summary, payload_json, state, decision_json, decision_command_id, expected_revision, created_at, updated_at, decided_at | payload_json, decision_json | B:D:746 / C:D:782 |
| `workflow_routes` | decision_id, run_id, owner_generation, state, reason, created_at, updated_at | — | B:D:696 / C:D:732 |
| `workflow_runs` | run_id, objective_id, title, activity_seq, activity_at, host_id, owner_generation, control_verifier, goal_json, goal_fingerprint, request_fingerprint, execution_configuration_json, execution_configuration_revision, validated_configuration_revision, current_routing_id, submission_verifier, execution_workspace_json, workspace_manifest_json, workspace_id, workspace_manifest_sha256, state, active_request_id, current_turn_id, current_attempt_id, continuation_count, final_artifact_id, final_attempt_id, revision, created_at, updated_at, configuration_locked | goal_json, execution_configuration_json, execution_workspace_json, workspace_manifest_json | B:D:652 / C:D:688 |
| `workflow_scope_versions` | run_id, scope_version, command_id, actor, reason, write_scope_json, manifest_sha256, stopped_evidence_json, created_at | write_scope_json, stopped_evidence_json | B:D:857 / C:D:893 |
| `workflow_suggestions` | suggestion_id, run_id, attempt_id, author, body, created_at | — | B:D:1021 / C:D:1057 |
| `workflow_turns` | turn_id, run_id, attempt_id, generation, turn_index, resume_mode, previous_session_id, session_id, prompt_sha256, input_sha256, input_json, state, disposition, outcome_json, provenance_json, turn_result_path, sealed_artifacts_json, created_at, updated_at | input_json, outcome_json, provenance_json, sealed_artifacts_json | B:D:713 / C:D:749 |
| `workflow_workspace_conflicts` | conflict_id, run_id, attempt_id, turn_id, manifest_sha256, observed_fingerprint, blocking_paths_json, evidence_json, state, action, resolved_paths_json, conflicting_paths_json, artifact_id, output_commit, output_tree, actor, reason, command_id, delivery_json, created_at, updated_at | blocking_paths_json, evidence_json, resolved_paths_json, conflicting_paths_json, delivery_json | B:D:881 / C:D:917 |
| `workspace_cleanup_plans` | plan_id, run_id, workspace_id, checkout_id, repository_id, path, kind, state, evidence_json, retention_json, reasons_json, result_json, command_id, actor, revision, created_at, applied_at, expires_at | evidence_json, retention_json, reasons_json, result_json | B:D:948 / C:D:984 |
| `workspace_reservations` | reservation_id, workspace_id, checkout_id, repository_id, path, access, holder_task_id, holder_kind, parent_run_id, manifest_sha256, state, created_at, updated_at, released_at | — | B:D:824 / C:D:860 |

## SQL 全量显式索引与视图附表

每一项都有两份源码的位置，新增对象的 B 为“无”；自动索引的来源是所属表的 PRIMARY KEY/UNIQUE，完整名字和 key/xinfo 在机器清单中，不遗漏表达式索引或自动索引。

| 类型 / 名称 | B 定义位置 | C 定义位置 | 结果 |
| --- | --- | --- | --- |
| index / `agent_credentials_attempt_unique` | B:D:994 | C:D:1030 | 相同 |
| index / `agent_credentials_run_idx` | B:D:998 | C:D:1034 | 相同 |
| index / `artifacts_task_idx` | B:D:196 | C:D:197 | 相同 |
| index / `attempt_activity_task_idx` | B:D:258 | C:D:271 | 相同 |
| index / `attempts_active_idx` | B:D:139 | C:D:140 | 相同 |
| index / `attempts_effective_unique` | B:D:137 | C:D:138 | 相同 |
| index / `attempts_model_family_idx` | B:D:140 | C:D:141 | 相同 |
| index / `attempts_task_idx` | B:D:133 | C:D:134 | 相同 |
| index / `commands_kind_idx` | B:D:221 | C:D:234 | 相同 |
| index / `decision_requests_created_idx` | B:D:553 | C:D:566 | 相同 |
| index / `decision_requests_task_idx` | B:D:554 | C:D:567 | 相同 |
| index / `evaluation_card_history_profile_idx` | B:D:506 | C:D:519 | 相同 |
| index / `evaluation_decisions_created_idx` | B:D:557 | C:D:570 | 相同 |
| index / `evaluation_evidence_counted_idx` | B:D:467 | C:D:480 | 相同 |
| index / `evaluation_evidence_created_idx` | B:D:460 | C:D:473 | 相同 |
| index / `evaluation_evidence_pending_created_idx` | B:D:456 | C:D:469 | 相同 |
| index / `evaluation_evidence_pending_profile_idx` | B:D:452 | C:D:465 | 相同 |
| index / `evaluation_evidence_profile_idx` | B:D:377 | C:D:390 | 相同 |
| index / `evaluation_evidence_run_idx` | B:D:464 | C:D:477 | 相同 |
| index / `evaluation_preferences_mode_idx` | B:D:357 | C:D:370 | 相同 |
| index / `evaluation_profiles_adapter_idx` | B:D:348 | C:D:361 | 相同 |
| index / `evaluation_profiles_current_idx` | B:D:347 | C:D:360 | 相同 |
| index / `evaluation_readers_open_idx` | B:D:415 | C:D:428 | 相同 |
| index / `evaluation_samples_profile_idx` | B:D:484 | C:D:497 | 相同 |
| index / `evaluation_writers_queue_idx` | B:D:433 | C:D:446 | 相同 |
| index / `events_decision_status_idx` | B:D:208 | C:D:209 | 相同 |
| index / `events_review_seq_idx` | B:D:571 | C:D:584 | 条件变化 |
| index / `events_router_claimed_idx` | 无（B:D:208 或 588 后） | C:D:220 | 新增 |
| index / `events_router_outcome_idx` | 无（B:D:208 或 588 后） | C:D:217 | 新增 |
| index / `events_task_idx` | B:D:207 | C:D:208 | 相同 |
| index / `messages_state_idx` | B:D:182 | C:D:183 | 相同 |
| index / `messages_task_idx` | B:D:181 | C:D:182 | 相同 |
| index / `objectives_activity_idx` | B:D:645 | C:D:681 | 相同 |
| index / `objectives_project_idx` | B:D:646 | C:D:682 | 相同 |
| index / `resource_claims_state_idx` | B:D:234 | C:D:247 | 相同 |
| index / `tasks_request_idx` | B:D:90 | C:D:91 | 相同 |
| index / `tasks_state_idx` | B:D:89 | C:D:90 | 相同 |
| index / `workers_state_idx` | B:D:156 | C:D:157 | 相同 |
| index / `workflow_artifacts_run_idx` | B:D:1017 | C:D:1053 | 相同 |
| index / `workflow_children_parent_idx` | B:D:787 | C:D:823 | 相同 |
| index / `workflow_continuations_auto_unique` | B:D:817 | C:D:853 | 相同 |
| index / `workflow_continuations_run_idx` | B:D:814 | C:D:850 | 相同 |
| index / `workflow_host_conclusions_run_idx` | B:D:1070 | C:D:1106 | 相同 |
| index / `workflow_integrations_run_idx` | B:D:942 | C:D:978 | 相同 |
| index / `workflow_requests_run_idx` | B:D:765 | C:D:801 | 相同 |
| index / `workflow_routes_run_idx` | B:D:707 | C:D:743 | 相同 |
| index / `workflow_runs_activity_idx` | B:D:690 | C:D:726 | 相同 |
| index / `workflow_runs_objective_idx` | B:D:689 | C:D:725 | 相同 |
| index / `workflow_runs_state_idx` | B:D:688 | C:D:724 | 相同 |
| index / `workflow_scope_versions_run_idx` | B:D:871 | C:D:907 | 相同 |
| index / `workflow_suggestions_run_idx` | B:D:1031 | C:D:1067 | 相同 |
| index / `workflow_turns_attempt_idx` | B:D:741 | C:D:777 | 相同 |
| index / `workflow_turns_run_idx` | B:D:738 | C:D:774 | 相同 |
| index / `workflow_workspace_conflicts_run_idx` | B:D:907 | C:D:943 | 相同 |
| index / `workspace_cleanup_plans_run_idx` | B:D:970 | C:D:1006 | 相同 |
| index / `workspace_reservations_checkout_idx` | B:D:846 | C:D:882 | 相同 |
| index / `workspace_reservations_holder_idx` | B:D:849 | C:D:885 | 相同 |
| index / `workspace_reservations_writer_unique` | B:D:842 | C:D:878 | 相同 |
| view / `effective_preferences` | B:D:615 | C:D:651 | 相同 |
