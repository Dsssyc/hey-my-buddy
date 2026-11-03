# 0.60.0 执行计划：工作区的事实只存一份

依据 [ADR-031](../../decisions/031-one-process-per-execution.md) 第 16 条、[ADR-023](../../decisions/023-harness-integration-principles.md)“输入上限”，以及 Host 本次续派给出的 W0 结论修订。调查基点仍是 baa1cfa7971298caf940522340df7437527f7e2a；下文包内路径从 src/hey_my_buddy/ 起算，行号指该提交。原计划的 D1–D7 及 R2 已由 Host 决定；R1、R3 在相应章节各给一个具体建议，明确标为“Host 2026-11-03 确认”。本回合只修改这份计划，不提交，不运行检查套件，不调用真实模型，不查询日常黑板或读取用户 harness 配置、主目录及已安装包源码。

## 已定的边界

manifest 正文继续只写在现有的 workspaces/<workspaceId>/manifest.json，数据库存工作区 id 与 manifestSha256；复用 workspace.py:44 的 manifestFile 投影。升级不写新文件，不建 manifest 目录或索引表。现有 request.json、input.json、manifest.json 都保留。SQL 要用的工作区事实改为真正的列，同一事实只在一个关系位置保存，冻结证据和索引约束所需的键按 AGENTS 的规定处理。

删除 workflow_children.workspace_manifest_json。新回执不再写 workspaceManifest，连它的引用也不回传；新回合也不再产生 turn-input 产物。服务从自己冻结的 turn input 知道本回合输入，原始输入仍由 input 产物及其引用固定。输出、解冲突的记录格式及版本不变，新产物表引用其现有文件，不再复制正文。shared-refs 唯一的正文继续留在 workflow_artifacts.manifest_json，不另开文件；重建产物表时把原来随 kind 改变含义的 manifest_sha256 分开。

manifest 升到 5。executionSelection 只有三个明确的 kind：初次 existing 的 selectors、续派的 seal、无封存失败恢复的 recovery；初次 worktree 没有 executionSelection。stash 起点继续封存在 turn input 中，只在提示词投影中去掉，不建表、迁移文件或改观察版本。最近封存由 _latest_handoff 选择，context 只带其摘要和 workflow 产物总数；helperOutcomes 保持本批现有行为。Lead 通过已有 list/get 读其他任务，不增加产物历史接口。

输入上限声明写在代码中。在执行进程把最终提示词交给 harness 前用一个纯函数核对；超限不启动原生回合，保留一次没有模型调用的 attempt。预检超限和原生输入拒收使用同一种原因，区别仅在阶段，由服务为 Worker/Lead 开 Host 边界；Router、维护各走原来的失败路径。correction、wrap-up、问询回答不预检。声明和计量不新建表、证据文件或服务端提示词构造流程。

单代备份格式不变，仍只承担安装回滚，工作区与输出记录不加入备份。新 schema 样本按记录明示的文件形式带齐 manifest 与输出记录，旧内联历史及 shared-refs 随数据库行导出；schema 33 及以下样本保持来源的内联形式。实施前由 Host 对日常黑板只读统计库存，不复制黑板。各检查使用默认多进程；W3a、W3b 的受影响集合均为整个套件，由 Host 在合并树运行完整检查。

## 现状与证据

[待办](../backlog.md)和 [0.59.5 安装记录](../../acceptance/installs/0.59.5.md)记录：existing 工作区产生 5,445 个未跟踪文件后，一次续派输入达到 1,120,125 个字符，Codex turn/start 拒收，上限报告为 1,048,576 个字符。0.59.5 只使提示词不带 executionWorkspace.snapshot，未改冻结输入与存储。这一事故是动因，声明的数值依据另见下文版本化公开源码。

基点 manifest 为 4（tasks/workspace.py:1201），turn input 为 3（tasks/workflow.py:111），schema 为 33（store/db.py:23），包版本 0.59.5，CONTRACT_VERSION 为 0.59.0。workflow reference 的 manifest 3 与 architecture 的 schema 32 落后于代码，随 owning docs 修正。result.workspaceManifest 已由 roles/run_execution.py:746、runtime/partial_outputs.py:26 的写者以及 workflow.py:6688、7353、store/store.py:114 的读者核实。

working-tree 分支的 excludedUntracked 等于 excludedEntries 的键；commit/worktree 分支却可能记录源检出的未跟踪列表，而 excludedEntries 为空（workspace.py:679、685）。Host 已同意：该 commit 分支列表视为对输入/执行不起作用的历史源观察，新 manifest 5 删除它，不补造 excludedEntries，也不改旧摘要。

## 读写点清单

R 是读，W 是持久写，P 是公开投影。紧邻使用点合列；每一行都包括对应的恢复、重放及 fence。工作区解析在 protocol/workspace_manifest.py 集中：当前引用读取现有文件，历史封存按自身形式解码，再进入同一套业务判断；执行进程不读 SQLite。

| 位置 | 读写 | 修改 |
| --- | --- | --- |
| blackboard/tasks/workspace.py:44，651，879，1149，1188，1201 | R/W | 复用现有 manifest 文件及文件摘要投影；新 writer 写 5，删三个派生清单，引入 executionSelection；同一 workspaceId 仍只写一次。 |
| blackboard/tasks/workspace.py:973，1012，1027，1084，1117，2258，2287 | R/W | 准备重放、借用分配、撤销与归属证明继续读取现有 request/input/manifest；修改数据库保留扫描以认识引用，升级不删这一批文件。 |
| blackboard/tasks/workspace.py:463，473，501，710，943，959 | R/W | 用一个私有 index 观察；按 selectors、seal 或 recovery 在进程内筛选，消除 ls-files 的批量路径 argv，不展开进持久请求。 |
| blackboard/tasks/workspace.py:1271，1315，1362，1393，1411，1443 | R/W | scope、seal、Host seal、累计补丁读解引用后的 manifest；封存自身的记录格式、版本、固定 Git 对象和摘要算法保持不变。 |
| blackboard/tasks/workspace.py:1696，1719，1730，1785，1846，1881，1986，2018，2052，2068 | R/W | restore/adopt/abandon、验证、整合的输入改用解析后的引用；旧封存内容不重写，消除数据库副本读取。 |
| blackboard/tasks/workspace.py:2232，2241，2435，2460，2551，2620，2658，2685 | R/W | 分配来源、回收与删检出证明从引用和保留证据读取，仍有引用的 manifest 文件不能按孤儿删除；物理 checkout 删除不代表证据可删。 |
| protocol/workspace_manifest.py:4，8 | R | 当前引用及历史 manifest 的统一读取；1–3 版取 repositoryId，4、5 版取 projectId；删除当前 SQL 使用 project_id_sql 解析正文的做法。 |
| blackboard/tasks/workflow.py:187，201，219，232 | R | known_repository_paths 从真列选择项目/身份，再按显式记录形式读取历史位置；去掉把 result.workspaceManifest、continuation、artifact 全文当 SQL 目录的查询。 |
| blackboard/tasks/workflow.py:1192，1350，1392，1569，1582，1734，1815 | R/W | 提交、父任务原子写入、读共享、claim blocker、回收读取引用及 SQL 事实列；请求 fingerprint 仍绑定原始 Host 意图。 |
| blackboard/tasks/workflow.py:1231，1261，1632，2653 | R/W | _pin_artifact 对 input 写现有 manifest 文件的身份，对输出写记录引用；分开三种摘要的列与幂等键；不复制新正文。 |
| blackboard/tasks/workflow.py:1841，2389，2616，2664，2737，2895 | R/W | 撤销、helper 批量准备、child/run 写入与 scope amendment；删 child 的 manifest 列，helper 输入从自己的 input/turn 绑定读，保留 owner/reservation fence。 |
| blackboard/tasks/workflow.py:3128，3131，3144，3993，4004，4227，4362 | R/W/P | helper 报告、原始与最终输入、整合、Host seal、adopted 路径统一读 artifact 记录形式及服务冻结的 turn；不能依赖新的 turn-input 产物。 |
| blackboard/tasks/workflow.py:4454，4462，4586，4705，4969，4972，5131，5310 | R/W | 整合与接管 fence 比较引用身份；冲突恢复取该 turn 的固定输入，不回退到 run 的当前 manifest。 |
| blackboard/tasks/workflow.py:5347，5432，5449，5491，5551，5607 | R/W | allocation provenance、cleanup、保留清单使用身份列与记录引用；保留已冻结 evidence/retention，删除对新 input/输出 artifact.manifest_json 的假设；shared-refs 的唯一正文仍读此列。 |
| blackboard/tasks/workflow.py:5747，5792，5801，5810，5832，5846，5852 | R | 待准备 continuation 用引用列 NULL 判断；stash 收集与仓库定位读取现有 manifest 文件，SQL 选择不解完整 JSON。 |
| blackboard/tasks/workflow.py:5863，5877，5917，5992，6078，6101，6125 | R/W | 中断封存、检查点及 turn 输入用 ManifestRef；stash 起点照常冻结在 context；native inputSha256 继续绑定实际冻结输入字节。 |
| blackboard/tasks/workflow.py:6168，6180，6253，6276，6291，6375，6439 | R/W | _latest_handoff、续派准备与回写采用 seal/recovery 规则；删除把 inherited includedUntracked 抄入 includeUntracked 和恢复套 256 条限制的代码。 |
| blackboard/tasks/workflow.py:6520，6547，6557，6575，6591，6608，6642，6652 | R/W | owned 变化集合与总数、唤醒、原始输入、最近 handoff 摘要和产物总数；中断提示不再指向完整 context.artifacts；helperOutcomes writer/reader 保持现有语义。 |
| blackboard/tasks/workflow.py:6688，6713，6832，6882，7090，7331，7353 | R/W | 导入不再读取新回执中的 workspaceManifest；从 turn 解析输入，删除新 turn-input 产物写者；新 seal 记录引用不作为 raw manifest 解码。输入拒收在确认停止后进入 Host 边界。 |
| blackboard/tasks/workflow.py:7214，7230，7267，7275，7290 | R/W/P | shared-refs 保持 version 1 原字段/算法及数据库唯一正文，start 仍来自 turn input；只适配拆开的 shared_refs_sha256 和输入读取，不另开文件。R2 已由 Host 定。 |
| blackboard/tasks/micro_task.py:210；blackboard/tasks/macro_tasks.py:216；blackboard/routing/router_boundary_data.py:245 | R/P | 项目与仓库位置通过真列/归属关联读取，Router 冻结输入在需要时解析 manifest 文件。 |
| blackboard/tasks/reclamation.py:19，37；blackboard/tasks/storage.py:308；blackboard/tasks/command_receipts.py:141 | R/W | checkout、回收依赖、存储保留扫描与整合回执改为真列和显式引用；不读旧正文列。 |
| blackboard/tasks/macro_task_deletion_guard.py:62；blackboard/tasks/macro_task_deletion.py:127，205 | R/W | 所有者、跨任务持有与后代路径扫描改为列/关联；child 不再贡献一份 manifest，历史 sealed-inline 按记录形式读取。 |
| blackboard/tasks/checkout_processes.py:21 | R | 首次执行时间按 turn 的工作区绑定及唯一 checkout_root 事实列 join；不能拿当前 run 的引用误替历史 turn 所在检出。 |
| buddy/roles/turn_io.py:32，55，121，134 | R/W | 写新 turn 输入引用并从现有文件验证 workspace；seal、native checkpoint 的绑定不因删除回传副本而改变。 |
| buddy/roles/run_execution.py:354，415，746；buddy/runtime/partial_outputs.py:13，26，36 | R/W | 在内存保留 effective manifest 供验证/封存，彻底删除新回执的 workspaceManifest 字段，不改名回传引用；输出记录按新外层结果形式引用。 |
| protocol/execution.py:58，79，221；buddy/runtime/execution.py:162，196，816 | R/W | 新 claim/startup 只携 turn 内的工作区引用；结果/回执明示当前形式，旧已封存回执不补字段；不新增计量证据文件。 |
| buddy/roles/prompts.py:13，27；buddy/roles/schema_worker.py:75；buddy/roles/worker_services.py:134；buddy/roles/texts/lead.txt:3，5 | R | 从 ManifestRef 解析 Worker 要用的路径/权限/范围，移除 prompt 中的 sharedStash 起点和内部账目；Lead 文本改为变化项、总数及 list/get 指引。 |
| protocol/capabilities.py:4；buddy/harnesses/registry.py:137；blackboard/service/harness_health.py:54 | R/P | 像 PERMISSION_LEVELS 一样在代码声明 InputLimit，通过现有能力视图公开；不写健康表、meta 或新表。 |
| buddy/roles/inline_run.py:136；buddy/roles/run_execution.py:354，444；buddy/roles/router.py:63 | R | 执行进程内对实际初始请求的完整 prompt 作一次纯函数核对；Worker/Lead、Router/maintenance 复用计量，失败路由按角色走现有路径。 |
| buddy/harnesses/codex/protocol.py:126；codex/native_run.py:1050，1269，1663；claude/native_run.py:502；dsh/acp/client.py:113，dsh/native_run.py:566；zcode/native_run.py:901 | R/W | 原生拒收保留结构化 error.data/码、阶段和有界 message，附同一输入的分区大小，不把输入过大归入 quota/overload。 |
| buddy/runtime/execution.py:140，761；blackboard/tasks/workflow.py:6832，6882，7146，7565 | R/W | 输入失败 attempt 的结算与服务 Host attention；原生未起时据实际创建证据确认停止。Host 单字段 1 MiB 的边界仍由原输入校验承担。 |

公开视图和控制台也要切换，不能继续读取已删除的正文或要求回执把原输入再传回来。

| 位置 | 读写 | 修改 |
| --- | --- | --- |
| blackboard/tasks/workflow.py:254，271，404，490，506，534，546，669，703，747，774 | R/P | 当前 workspace、child、artifact、cleanup、owned/sharedStash 摘要由真列及引用生成；新 artifact 的摘要名称与数据库三类身份一致。 |
| blackboard/tasks/workflow.py:1934，2053 | R/P | get/audit 返回冻结输入自身形式；旧输入保持正文，新输入保持 ManifestRef；不通过补展开结果改写原输入。 |
| blackboard/store/store.py:78，114，133，508，2396 | R/W/P | 新 result 不展示或补造 workspaceManifest/其引用；要查看输入由 workflow get 的服务端绑定提供。command/decision/maintenance 的通用结果不强加工作区形状。旧 result 按显式归档形式投影。 |
| cli/cli_views.py:46，178 | P | brief/full 接受三类产物身份、记录引用、输入失败结构；full 仍是完整视图，引用不会自动展开。 |
| apps/console/src/task-detail/client.ts:29，parse.ts:172，259 | R/P | 当前控制台从 get/audit 解析输出摘要与路径，未读取原始 manifest、owned 列表或 shared-refs 全文；按新字段归一，保留旧审计阅读。 |
| apps/console/src/task-detail/types.ts、TurnPane.tsx、EventPane.tsx、reasons.test.tsx；apps/console/src/reason.ts、apps/console/src/i18n/en/reasons.ts | R/P | 增加输入拒收的阶段和大小展示、中英文原因；这些是新增字段，没有旧字段行号。 |
| tools/export_board_sample.py:65 起；tests/python/blackboard/store/upgrade_fixture.py；packaging/generate-upgrade-fixture.py | R/W | schema 34 样本按 R1/R3 补实际文件引用与 owner 元数据闭包；旧内联历史和 shared-refs 随数据库行导出；旧 schema 导出不迁移。 |

## SQL 真列与唯一事实归属

以下是基点所有直接在 SQL 中解析工作区/产物正文的生产读者，不包括已冻结升级步骤。删除 JSON 列之前，W3a 要固定它们读取的真列及归属，W3b 逐项切换。不能只在 Python 加文件解析器却把这些查询留给运行时失败。

| SQL 读者 | 现在取的字段 | 新查询的答案 |
| --- | --- | --- |
| workflow.py:201–214 | input/turn-input 的 projectId（旧版 repositoryId）；输出 repositoryPath 的正文 | 项目筛选读唯一项目归属列；初始输入由 input 产物的工作区引用取，后续 turn 从服务冻结绑定取；按原事件发布顺序读取历史文件，取消新 turn-input 产物这一来源。 |
| workflow.py:219–229 | continuation 的 projectId、manifestSha256 | 项目归属关联加 continuation 的 workspace_id/manifest_sha256 列；已准备的引用成对非 NULL，事件仍按对应摘要关联。 |
| workflow.py:232–242 | attempts.result.workspaceManifest 的 projectId | 新 result 不再是工作区目录；从 attempt→workflow_turn 的固定输入关联取得身份/项目。旧结果只作历史封存阅读，不能继续靠扫描回执重建当前来源。 |
| workflow.py:5354–5364 | input artifact、turn input、continuation 的 workspaceId、manifestSha256、snapshot，按摘要去重 | 以 input 产物、turn 固定工作区引用、prepared continuation 的真列/身份 UNION，按 (workspace_id, manifest_sha256) 去重；历史 inline 内容进入集中历史解码，不把所有新文件正文读进 SQL。 |
| workflow.py:7331–7334 | output 记录内部的 kind，排除 host-output | 产物表增加 output_origin 真列，只有 outer kind 不能区分 Worker 与 Host output；过滤 output_origin，不再 json_extract(manifest_json,'$.kind')。 |
| micro_task.py:210–219 | root input 的 projectId/repositoryId、snapshot.repositoryPath | root/project 归属 join 真列；初始仓库位置是一份冻结的源位置，不能随当前 manifest 换成另一个位置。 |
| macro_tasks.py:216–223 | root input 的 repositoryPath | project_id 复用 macro_tasks.project_id；repository_path 使用与 micro_task 同一份源位置事实，不在 macro 再存一列。 |
| reclamation.py:19–24 | 初始意图 kind、run.checkoutRoot 与 cleanup plan.path | 分配 kind、checkout_root 使用唯一工作区事实列；cleanup plan.path 是清理决定当时的冻结证据，保留。 |
| reclamation.py:37–44 | parent/child checkoutRoot 相等 | 父子各自的分配绑定 join 到真实 checkout_root；不把 tasks.cwd 当根目录，cwd 允许是子目录。 |
| macro_task_deletion.py:205–232 | runs/artifacts/continuations/children 和 turn input 中的 workspaceId、checkoutRoot/后代路径 | 列举实际持有者的引用，join 唯一 checkout_root；去掉 child 的独立 manifest 来源。保留跨任务/后代目录的检查，不依赖 JSON 是否存在。 |
| macro_task_deletion_guard.py:62–68 | input 的 kind、checkoutRoot、workspaceId | input 产物仍固定初始分配的身份；从分配的真列查 worktree/root，再联删除日志，不能拿续派逻辑 existing 代替原来的物理 worktree。 |
| checkout_processes.py:21–27 | 每个 turn 的 executionWorkspace.checkoutRoot | 从该 turn 的冻结绑定 join checkout_root，再对 attempt.started_at 求 MIN；不能按后来可变的 run 绑定缩小历史扫描。 |
| protocol/workspace_manifest.py:8–12 | project_id_sql 生成上述版本分支 | 当前 SQL 全部改用项目真列/归属关联；旧 manifest 版本只在封存解码器区分。 |

workflow.py:4194 和 macro_tasks.py:848、973 对 result.status/turn disposition 的 JSON 查询不读工作区正文，保持原语义。store/schema_32_33.py:126–153 虽也遍历 manifest 库存，属于已在真实黑板运行的步骤，不修改。

### R1 Host 2026-11-03 确认：事实放在现有输入/准备行，历史 turn 按冻结 pair 关联

只采用这一种归属：一个 (workspace_id, manifest_sha256) 的 kind、checkout_root、repository_path，由登记它的现有 input 产物行或 prepared continuation 行唯一持有；其他行只持引用，不复制三列。项目复用 macro_tasks.project_id；没有 macro 的 run 在 workflow_runs.standalone_project_id 写一次自己的项目。查询内 CTE 联合两类 owner，不新建表或持久 view。

| 事实 | 唯一拥有位置 | 依据与写入时刻 |
| --- | --- | --- |
| 准备出的 kind、checkout root、repository | 初始输入用 workflow_artifacts.workspace_kind、checkout_root、repository_path（kind=input）；新准备用 workflow_continuations 的同名三列。每个 pair 恰有一个 owner。 | workflow.py:1632–1640、2653–2675 固定初始 input；:6385–6405 区分复用 previous 与调用 prepare，:6439–6452 发布 continuation；workspace.py:1201–1209 写唯一文件。三列与对应引用在同一事务写一次。 |
| macro 项目 | macro_tasks.project_id，经 workflow_runs.macro_task_id 关联。 | macro_tasks.py:151–180 已校验并写项目；workflow.py:1568–1590、2750–2754 记录 run/helper 归属。artifact/continuation 不存 project_id。 |
| standalone 项目 | 新增 workflow_runs.standalone_project_id，macro_task_id 为 NULL 时才非 NULL；每个 standalone run 从初始输入写一次。 | macro_tasks.py:175–176 明确允许没有 macro；不能把 NULL 当项目未知，也不能从父任务后来的归属猜自己的项目。continuation 不改项目。 |
| reservation 根、cleanup 路径 | 保留 workspace_reservations.checkout_root、workspace_cleanup_plans.path。 | 现有 writer 唯一索引及 workflow.py:5469、5596 的清理冻结证据各有职责；它们不是另一份供历史输入查询的元数据目录。 |

input 候选行增加 workspace_facts_role=owner/reference；continuation 用 pending/owner/reference；其他 artifact 为 NULL。owner 的三列全有，reference 的三列全 NULL，pending 的 pair 与三列都为 NULL；workspace_kind 仅 existing/worktree。各表对 owner 的 pair 建部分唯一索引，跨两表唯一性在登记/升级边界核对。已存在 owner 的 pair 只登记 reference；文件 prepare 的 replay 不决定谁拥有数据库事实，不能拿文件已存在当成数据库已有 owner。

项目 CHECK 明确：macro_task_id 非 NULL 则 standalone_project_id 为 NULL；macro_task_id 为 NULL 则 standalone_project_id 非 NULL。admission 第一次 INSERT 就带齐，不沿用“先插无 macro 再 UPDATE”的瞬时不合法形状。升级从自己的初始输入回填 standalone；有 macro 的 run 核对 manifest 项目与 macro 一致，不一致拒绝升级并标出 run，不任取一份。

所有 SQL 读者用同一个查询片段，owner_run_id 再关联上述项目列；只存事实，事件顺序、引用次数和展示计数仍从现有数据求出：

~~~sql
WITH manifest_owners AS (
  SELECT workspace_id, input_manifest_sha256 AS manifest_sha256,
         workspace_kind, checkout_root, repository_path, run_id AS owner_run_id
  FROM workflow_artifacts WHERE kind='input' AND workspace_facts_role='owner'
  UNION ALL
  SELECT workspace_id, workspace_manifest_sha256,
         workspace_kind, checkout_root, repository_path, run_id
  FROM workflow_continuations WHERE workspace_facts_role='owner'
)
SELECT o.checkout_root, MIN(a.started_at)
FROM workflow_turns t JOIN attempts a ON a.attempt_id=t.attempt_id
JOIN manifest_owners o ON o.workspace_id=t.workspace_id
 AND o.manifest_sha256=t.workspace_manifest_sha256
GROUP BY o.checkout_root;
~~~

这给 checkout_processes.py:21–27 明确的历史路径：attempt → turn 冻结 pair → owner，不能 join run 当前 pair。reclamation.py:19–24、37–44 与删除扫描按 run/初始 input/持有者 pair join 同一 CTE；macro_task_deletion_guard.py:62–68 取初始 worktree owner。micro_task.py:210–219、macro_tasks.py:216–223 都读 root 初始 input owner.repository_path。known_repository_paths（workflow.py:187–247）仍按既有事件顺序选位置，receipt 来源通过 attempt→turn 取得身份；provenance（:5346–5367）UNION 引用 pair 后 join owner，不读 SQL 正文。

升级从旧 input/continuation 回填 owner 三列，其他位置只回填关系键；每个被引用 pair 必须恰有 owner，事实冲突拒绝升级。旧 sealed bytes 不动。删除/导出纳入 owner 闭包：样本不拉入未选任务，源 owner 属于未选任务时，在样本已有的 input/continuation 引用行承接同一三列并重指 owner，原封存正文/摘要不动。这是新私有板的 SQL 元数据重绑，不声称承接行当年创建过 manifest。

## 新的存储形式

ManifestRef 固定为 {workspaceId, manifestSha256}，定位既有 workspaces/<workspaceId>/manifest.json。正文的自摘要仍按原算法计算，文件原始字节摘要仍由 manifest_file_reference 在读/交付边界计算，二者不互换，也不为第二个摘要建索引或列。workspaceId 由稳定请求派生，_write_once 保证该位置只有一份；升级不能在同一个位置写不同摘要的内容。当前引用读不到或身份不符就报缺失/不一致，不转读另一份当前副本。旧结案历史按 R3 明示的记录形式读取，不尝试打开 manifest 文件。

manifest 5 删除 snapshot.includeSelectors、includedUntracked、excludedUntracked。需要准备时的未跟踪集合，用 paths(inputTree) − paths(stagedTree) 的路径集合差求出；不能用完整 tree diff，后者还包括 tracked 文件内容变化。排除清单取 excludedEntries 的键。请求里的原始 includeUntracked 仍是 Host 的显式意图；它不接收续派产出展开的路径。新 executionSelection 带 kind；seal 的引用部分为 {artifactId, snapshotSha256}；recovery 表达“写入范围内的非忽略文件”，不含 paths；初次 worktree 不设置该字段。

| 表/记录 | 新形式、键与约束 |
| --- | --- |
| workflow_runs | 删除 workspace_manifest_json；workspace_id、workspace_manifest_sha256 为成对 NULL 的当前槽位。R3 建议 accepted/concluded 时均 NULL，其他状态均非 NULL，以 CHECK 固定。新增 standalone_project_id 及 R1 项目约束；位置三列不在 run 复制。保留原始 execution_workspace_json；没有 manifest 索引表外键，当前文件在边界核对。 |
| workflow_children | 直接删除 workspace_manifest_json，不加替代列；parent/child/task/request/role 关系保留。child 初始输入由自己的 input 产物固定，当前输入由自己的 turn/run 绑定读取。 |
| workflow_continuations | 删除 workspace_manifest_json，增成对 NULL 的 workspace_id、workspace_manifest_sha256；按 R1 增 workspace_facts_role 与仅 owner 使用的 workspace_kind/checkout_root/repository_path 三列。pending 尚未准备，owner/reference 均冻结 pair；SealRef 仍随有界准备意图保存。 |
| workflow_turns | 新 input_json.version=4，持久 executionWorkspace 为 ManifestRef；stash context 保留原观察。为 SQL provenance/历史 checkout 需要的冻结身份提供 workspace_id、workspace_manifest_sha256 真列，并由该回合 input 绑定；这两列是关系键，不能改指 run 的最新输入。access/access_note 仍是回合冻结权限证据。 |
| workspace_reservations | 复用 workspace_id、manifest_sha256；checkout_root 及 access/holder/state 仍承担写者排他约束与归属。不改为指向不存在的索引表，也不另存 manifest 正文。 |
| workflow_artifacts | 原混合 manifest_sha256 拆成 input_manifest_sha256、output_snapshot_sha256、shared_refs_sha256，按 kind 恰有一列非 NULL。content_form 为 sealed-inline-v1、input-reference、record-reference；sealed-inline-v1 表示数据库封存正文，既供旧记录，也供当前 shared-refs 的唯一 manifest_json。input-reference 保存 workspace_id/输入摘要；record-reference 仅用于已有文件的输出/解冲突记录。input 按 R1 增 owner/reference 角色与仅 owner 使用的位置三列。正文与文件引用不能共存。 |
| workflow_artifacts 的 kind/键 | input 和旧 turn-input 使用输入摘要；output/resolved-output/partial-output/abandoned-site 使用 snapshot 摘要；shared-refs 使用该记录自身摘要。artifact_id、run/turn/attempt、created_at 和原有 rowid 次序保留。按三种摘要分别建与原 run/attempt 幂等语义一致的唯一条件；不再把不同摘要类型当同一种身份比较。新 writer 不写 kind=turn-input。 |
| workflow_artifacts.output_origin | 只在可能来自 Worker 或 Host 的 output/partial-output 行记录 worker/host，枚举 CHECK；其他 kind 为 NULL，其内层记录种类可由 kind 推出，不再重复存。它是 :7333 现有 SQL 所需而无法由 outer kind 推出的事实；新旧行均从记录固定值回填。 |
| attempts | result_json 不重写旧字节；增加 result_form，非 NULL 结果显式标 archived-report-v1/current-report-v2，NULL 结果对应 NULL form。该表也承载 command、Router、维护，不能强加 workspace 字段。新 workflow report 不含 workspaceManifest 或替代引用；输出记录可引用，输入只由服务的 turn 绑定提供。 |
| 私有 receipt/startup | 新 receipt 明示当前外层形式（version:2），旧无 version 回执原样保留并只在旧归档入口读。启动材料中的 turn input 是新引用形式，执行内存里解析 manifest；不写另一份 manifest，不为计量增加文件。 |
| 私有 request/input/manifest、output/resolve；数据库 shared-refs | request/input/manifest 原文件保留，新 manifest 写 5；output/resolve 文件的字段、版本、摘要不改，数据库停止复制它们。shared-refs 与 stash observation 仍为 1；shared-refs 唯一正文仍在数据库，不生成物理文件。 |

新产物表的 CHECK 要把 content_form、kind 和列的 NULL 组合写全：sealed-inline-v1 必须有 manifest_json；shared-refs 只允许这种正文形式，record_path/record_sha256 均 NULL；input-reference 必须有工作区 id 和输入摘要，不带 record_path；record-reference 必须有文件位置、文件字节摘要和对应种类身份，不带 manifest_json。历史 turn-input 只存在于 sealed-inline-v1 分支，新 writer 没有它的写入口。原结果中 output/partial seal 正文的存储也要通过新结果形式持有记录引用，避免在 attempts 又复制一份新输出；workspaceManifest 的删除则没有替代字段。

不存引用次数、派生的路径数、额外正文缓存或每次成功预检的计量副本。共享 stash 起点、shared-refs 及 output 内部已由现有记录封存的字段保持原形式；本批不清理它们内部的重复字段。新增真正 SQL 事实列的边界以 R1 为准，不能用“以后可能用”保留任何旧可变列。

### schema 33 → 34 与库存

新增 store/schema_33_34.py，升级链增加 33→34，db.py 直接创建 schema 34。三条已运行的步骤 30→31、31→32、32→33 一字不改。新步骤自带受影响表的 DDL、索引、常量、旧形式转换与校验，不 import 当前 db、workflow、workspace、protocol 业务实现；最后在同一升级事务写 schema_version=34。floor 仍为 0.54.1/schema 30，不能把新表定义导入历史步骤。

| 库存 | 33→34 的处理 |
| --- | --- |
| run/continuation 的 manifest_json | 按 R1 回填 owner 三列/项目与引用键；R3 建议旧 accepted/concluded 清空 run 当前槽位，其他 run 保留并校验当前引用。continuation 的固定 pair/owner 元数据仍在，NULL 为 pending；删除旧可变正文列，不创建或改写文件。 |
| workflow_children 的正文 | 删除整列，先确认 input/turn/run 的绑定足以承接其读者；不迁成第二份引用。 |
| 旧 artifact.manifest_json | 封存原字节不改，明确标 sealed-inline-v1；按 kind 拆旧摘要，按 R1 补唯一 owner 三列，保存原 artifact_id、rowid、时间和绑定。即使物理文件已不存在也不把旧 inline 封存重写成文件引用。 |
| 旧 turn input、结果、checkpoint、receipt、物理 seal | 原样保留；turn 的既有 version 区分旧输入，结果行明示 archived-report-v1。旧未版本化回执由归档归属明确选择阅读器，不补 version、重算 inputSha256 或改路径。 |
| request.json、input.json、manifest.json | 全部保留，升级没有文件写入或删除，也没有“发布后登记”的机制。 |
| reservation、cleanup、scope、integration、Host conclusion、command receipt、events | 保留约束所需键与冻结判断事实；可变正文列的使用者在同批切换，已不读的列随重建删掉，不为审计留下当前格式双写。 |

安装器现有单事务负责表重建、外键与完整性检查、版本标记；备份/回滚格式不变，不添加目录、工作区、输出、原生会话或执行回执。新形式引用的原有工作区文件不在单代回滚备份里，恢复数据库后仍使用留在原位置的文件；升级本身不改变这些文件。

实施前先由 Host 对日常黑板只读计数：连接 mode=ro、query_only=ON，聚合输出，不打印任务文字、路径正文或凭据，不复制黑板。至少按表/版本/kind/state 分组统计 run/child/continuation 中的 manifest 与 NULL、input/turn-input/各输出/shared-refs、turn input 1/2/3、result 有无 workspaceManifest、项目身份旧/新字段、准备/封存/回收状态、stash observed/unknown/空列表，并统计各字段不一致和孤立引用。三种旧 artifact 摘要身份的唯一性也分别计数。只有查清库存、R1 建议的唯一事实归属和 R3 建议的文件边界，才能固定 W3a 的转换；缺失不靠升级重建文件补齐。

夹具在 packaging/generate-upgrade-fixture.py 同批扩展：existing 的 staged/unstaged/显式 ignored/排除项，commit/worktree 源有未跟踪文件，首回合与多次续派，helper 初始与后续输入，NULL/已准备 continuation，正常/Host/partial/resolved/abandoned 输出，旧 result.workspaceManifest，旧 turn-input，完整/空/unknown stash，已回收检出和仍保留的证据，多个持有者同一引用及不同引用。沿用 v3 delivered stock 的升级后继续、验收与故障恢复。

floor 能写的库存必须由 0.54.1 写者产生；floor 仍保留但更早才写的形态从 floor-written row 可追溯派生，在 fixture README 记录。扩生成器、metadata、资源模板与测试，不改已冻结步骤和旧 oracle，不伪装 33 版 writer 的行属于 floor。纯 schema 33 的独立库存可以验证直接 33→34，但不能冒充已覆盖 floor 链；若某种形式无法按规则补进链夹具，交 Host 明确缺口。路径只用占位符。

链测试从 30 跑到 34，与直接新建 34 比较表、列、索引、约束；核对存活行的身份和旧 sealed bytes/hash 不变，旧可变列确已消失，child 没有替代列，新 writer 没有 turn-input artifact 或 workspaceManifest 回传。另验证每一步之前/表重建中/版本提交前故障会回滚数据库，原有工作区文件完全未改。新源须继续支持 floor 到 current，不为旧样本临时写另一种在线形式；样本缺文件与升级边界见 R3。

### 新旧封存的阅读

当前 writer 只写 manifest 5、turn input 4、当前 report/receipt。当前提交、claim、执行结果入口只认这一次契约；不接受旧客户端整份 manifest。新回执没有输入 manifest，不能用“缺字段就退回 run 的最新 workspace”补救，必须直接使用服务冻结的 turn。

历史分派依据 artifact.content_form、attempt.result_form、turn input.version，以及 manifest 自己的 version；output/resolve/shared-refs 的内容版本本批不变，content_form 区别数据库封存正文和另有文件的记录；shared-refs 始终读数据库唯一正文，没有文件分支。不根据日期、文件是否存在、是否有 snapshot 尝试第二种读法。旧回执缺版本由 archived-report-v1 的归档入口选旧阅读器；新在线 receipt 接收只认 version:2。

历史 decoder 只负责把旧封存解析成统一内存模型，此后的验证、scope、整合、回收等走同一条业务路径。新引用也可能指向版本 1–4 的旧 manifest 文件，须按正文 version 解码；这属于旧封存例外。不能因为新的引用形状就断言正文一定是 5，也不重写旧记录凑当前版本。

只遇到新在线形状的是新执行的 claim/receipt 接收、当前输入预检及 prompt 投影；可能读取旧封存的是 get/audit/result、旧 delivered 继续、原始/最终输入验证、Host seal、整合、cleanup/reclaim、宏任务删除、项目位置历史与样本导出。新结果阅读器不会提供 workspaceManifest 引用，新 turn 的输入查看走 workflow get；历史旧 result 的正文仍可按原形式审查。

### R3 Host 2026-11-03 确认：仅结案槽位清空，旧内联历史不依赖文件

采用 Host 方向，但不可再续派的自有状态限定为 accepted、concluded；failed、cancelled 仍可重试，不在此批改变它们的行为。workflow.py:2931–2937 只拒绝 accepted/concluded 与 applying/applied cleanup，:3163–3195 在停止证据满足后 requeue，:3680–3694 另查祖先门禁。tests/python/blackboard/tasks/test_host_workflow_worker.py:205–218 验证 failed 后继续，test_workflow_cancellation.py:239–245 验证 cancelled 后继续；本回合只读，未运行。祖先门禁不把本 run 自动改写为已结案。

普通回收不删 manifest：workspace.py:2685–2695 的 cleanup_remove 明确不碰父目录、outputs 或 refs，:2760–2776 只 remove_tree(checkout_root) 并移除该 worktree 的 Git 注册；workflow.py:4701–4768 只执行 cleanup_plan/apply。reclamation.py:183–191 即使 owner 已 accepted/concluded，只要 manifest.json 在，仍标 request-task-retained，阻止把它当无主准备目录。workspace.py:1084–1103 的 unlink 属于已证明无引用的提交撤销，不能称为普通检出回收。

建议 workflow_runs 当前 pair 只在非 accepted/concluded 状态非 NULL。升级清空旧 accepted/concluded 的这两个新列，之后的新 writer 在接受/结案事务内同步清空；历史 turn 冻结 pair、input/continuation 元数据 owner、旧 input_json/产物正文不动。没有 archived manifest 副本，也不根据文件是否存在换读取来源。

SQL 需要一个运行的有效工作区时，统一先取 binding：非结案运行取当前 pair；accepted/concluded 取其最后冻结 turn 的 pair，没有开始过 turn 才取初始 input 产物的 pair，再 join R1 的 manifest_owners。这是按明确状态和历史记录种类分派，不是 NULL/文件缺失 fallback。reclamation.py:19–24 的终态候选查询、父子根比较、删除扫描都用这个 binding；否则清空当前槽位会误让 accepted 的 checkout 无法回收。历史 first_execution_time 仍逐个 turn 关联，不改成最后 turn。

continue_run 当前在 workflow.py:2895 先解析 workspace、:2905–2916 做目录/仓库定位，直到 :2932 才拒绝已结案状态；W3b 必须把该终态门禁移到当前引用解析和文件操作之前，仍保留写事务中的 revision/owner fencing。已结案的 continue 直接返回原 CONFLICT，不先尝试打开不存在的当前槽位。cleanup/reclaim 的入口则取上述历史 binding 和封存内容，再执行实际分配的物理证明。

旧结案历史的 workspace 视图从冻结 turn input 1/2/3 的 executionWorkspace 读；审计某回合只读那个回合。从未开始 turn 的 concluded 运行明确取自己的 kind=input 封存产物，workflow.py:4585–4591 已有未开始 attempt 无需 seal 的分支，:1632–1640 保证初始输入固定。入口由结案状态和记录版本决定，不能找不到 turn 就尝试当前文件。旧内联历史不返回声称可用的 manifestFile 链接；原字段/摘要不改。新 version 4 turn 历史本来是引用，仍读其文件，按既定规则保留/导出。

升级边界对所有非 accepted/concluded 的 run 核对一次当前文件：由 state 根目录和 workspace_id 定位现有 manifest.json，检查安全私有普通文件、workspaceId、manifestSha256 及正文摘要；并覆盖实际分配证明所需的初始 worktree manifest（workspace.py:2258–2291），不能只检查最新 logical existing。还要核对结案运行仍待物理清理的分配证明：已有 planned/blocked/applying cleanup，或私有 state 下仍有其受管 checkout 的，不因 run 已结案就免掉该动作必需的文件。workflow.py:5369 起允许结案后清理，:5529–5567 仍读取分配证据，所以“不可续派”本身不足以证明文件永远不再使用。若既有 applying 日志已能按 :5616–5625 的 missing_allocation 路径仅清除原 Git 注册，保持这种已经冻结的证明，不额外要求它本来就不需要的文件，也不在升级中推进清理。

这些边界检查按 pair 去重；缺失、摘要错误或绑定不一致就拒绝整次升级，错误带 run_id、workspace_id、预期摘要和失败类别；不修文件、不重建、不自动结案 failed。步骤自己的文件/摘要与日志校验独立固化，不 import 当前 workspace 代码。

accepted/concluded 的旧内联历史视图没有文件存在性门槛；仅当它们还有上述物理动作时，升级另查那个动作的证明。R1 元数据从数据库库存回填。export_board_sample.py:59–64 只选 accepted，:84–98 的导出清单不带 cleanup plan/reservation，也不复制受管 checkout，所以 schema ≤33 的无 manifest 历史样本能沿 30→31→32→33→34 升级并读原历史；这不是另一套当前引用读法。schema 34 样本只复制封存形式明示依赖的文件：input-reference、record-reference 与 version 4 turn 引用；已清空的 run 槽位及旧内联 turn/continuation 的 SQL 身份键不强制产生文件依赖。shared-refs 随数据库行导出。历史可读不授予样本外部路径的 reclaim/整合权限，物理动作仍需原文件/Git 所有权证明。

W3a 补 accepted/concluded 且无文件/无待执行物理动作、无 turn 的 concluded、结案但清理仍需文件时拒绝缺失、applying 日志合法支持 missing_allocation、failed/cancelled 保留并验证当前引用、旧 delivered 可继续、摘要错误、回收后 checkout 不在但 manifest 仍在的库存。故障要证明数据库/版本回滚且文件不改。W3b 让 _workspace_view、cleanup/reclaim、项目/删除扫描按状态/记录形式分派，不能先无条件打开当前文件再判断已结案；新旧样本在完整套件和 Host 演练覆盖。

## 续派准备与 Git 观察

续派复用当前物理检出，保留 path、project、reservation 与 owner fencing。先按 _latest_handoff 的原语义选择同一检出可交接的最近封存，固定 artifactId 与 snapshotSha256；它可能来自父任务、Host resolution 或 helper 移交，不改成只看本 run 最后一个 output。准备意图只含 seal 规则，准备时验证封存的 task/attempt、输入绑定、固定 Git ref/tree/patch，临时读 includedUntracked；路径不进入请求或新 turn input。

没有封存且允许恢复已停止失败回合时，用 recovery 规则选择写入范围内的非忽略未跟踪文件；不能把恢复的文件名单当新的 Host 显式 includeUntracked，也不套 256 条提交上限。未解决越界冲突仍阻止准备。只换权限而没有新封存时保留原来的有界选择规则；初次 worktree 的输入由冻结树物化，没有 executionSelection。新 inputTree 的实际内容仍须符合交接基线，规则不是吸收封存后任意修改的授权。

prepare_continuation_workspace 继续在读事务取 continuation、run/task、scope、owner generation、buddy 与 reservation fence，事务外执行 Git，回写前复核，在一个写事务登记 run/continuation 引用、reservation 更新与事件。没有新的文件索引登记阶段。并发取消、接管或 scope 改动使准备失效时，按原来的证据与重试规则处理，不改原封存。

Git 的 [add pathspec-from-file / pathspec-file-nul](https://git-scm.com/docs/git-add)支持文件或 stdin 的 NUL 分隔 literal 路径，[update-index --stdin / --index-info -z](https://git-scm.com/docs/git-update-index)支持索引输入；现有 add、restore、checkout-index 已用这些入口（workspace.py:486、1673、2448）。[ls-files 的公开选项](https://git-scm.com/docs/git-ls-files)不提供 pathspec-from-file，--exclude-from 是忽略模式，不能代替路径选择。本批继续用 Git 和现有 Python/codec，不重做 ignore/pathspec 解释器。

按 Host 决定，一个私有 index 承担整次观察。先在该 index 记录原始 entries，并以不带路径参数的 ls-files --others --exclude-standard -z 取得非 ignored 未跟踪候选；清除原条目的 assume-unchanged 标志沿用现有 stdin 机制。selectors 或 seal 需要的选择器通过 add --force --pathspec-from-file=- --pathspec-file-nul 加入同一个 index，再从 ls-files --stage -z 的路径集合减去原 index 路径，得到这次明确选择的未跟踪项，包含显式 ignored 输入。它们与原非 ignored 候选合并，selectors、seal、recovery 和 capture/writeScope 的准入过滤均在进程中做。

继续在同一个 index 用 add -u 观察 tracked 工作树变化；已经明确选中的新条目本来就属于准入集合，保留在 index，不再另建发现 index。只有尚未加入、属于 capture 范围的非 ignored 实际路径，再以 NUL 输入交给 add；不把 writeScope 直接当 force-add 的目录选择器。然后取最终 entries 与各未跟踪文件原始字节观察，按原来 included/excluded/fingerprint 语义计算。recovery 无显式 ignored 选择，只处理写入范围内的非 ignored 候选；初次 worktree 的源输入仍可按请求选择，物化后执行侧没有选择规则。ls-files 从此不接 *selected，也不遍历全部 ignored 目录后再截取。

（2026-11-04 Host 改定，取代本节下面“一个私有 index 承担整次观察”的做法）W1 三次试着在 Git 之外预判 Git 会怎样匹配选择器（dry-run 的输出不以 NUL 分隔也不转义；lstat 会顺着中间目录的符号链接走，而 Git 跳过这样的路径），都撞上新的语义差异。所以观察照旧用现在的命令与流程，匹配完全交给 Git；只把随路径增长的选择器分块，每块命令行不超过平台的实际上限（POSIX 取 `SC_ARG_MAX` 扣掉环境与其余参数，Windows 取 CreateProcess 的 32,767 个字符），合并各块的结果；私有 index 的用法维持现状。续派改为引用封存之后（W4），带过来的文件不再经选择器，选择器只剩 Host 显式给的那些。以上调用顺序来自 Git 的原生索引和路径输入接口，尚未做本机等价实验。W1 用临时 Git 仓库验证：显式 ignored 文件/目录和兄弟项，seal 的精确路径，recovery 非忽略规则，tracked 的 staged/unstaged/删除，缺失选择器，符号链接、大小写别名、空格/换行/引号/通配字符/前导“-”，以及累计路径字节超过平台 argv 上限的情形。测试同时核对 clean filter、同大小近期修改、Git 的 racy-clean 水位和 fingerprint，断言源 HEAD/index 不受影响，不把测试数据量当产品上限。若单 index 调用顺序不能复现既有语义，交 Host 处理实际失败证据，不自行扩大观察或增加第二 index。

## 提示词上下文与 Lead 的读取

_owned_turn_context 只查询 activity_seq 大于前一 frozen ownedFactsCursor 的 owned run，并带 ownedMicroTasksTotal、本次 ownedFactsCursor。同一事务决定变化集合、总数与高水位；首次无前游标时把已有任务视作初始变化。每项恒为真的 changedSincePreviousTurn 删除，唤醒继续使用冻结游标，不能用 Lead 实际浏览了多少项推进它。

Lead 回合实际拿到共享 launcher 与本回合 credential file（roles/texts/lead.txt:3，roles/run_execution.py:361）；会话 inquiry/finish 工具不是通用黑板查询。凭据授权 task_list/workflow_get，读取范围为自身及直接 owned run（workflow.py:68、1007）。store/store.py:1084、1130、1150 的 list 按 workflow_lead_tasks 过滤，支持 limit 1–100、before/offset，返回 total、nextCursor；Lead 按游标遍历，再逐项 get，需要审计时 includeAudit:true。更新角色文字和唤醒说明，去掉“下一回合带全部 current states”的承诺。

context 的最近封存摘要采用 _latest_handoff 的结果，另加该 run workflow_artifacts 的总数；总数包含该表的实际产物行，不改成另一套“可交接产物”计数。没有 handoff 时摘要为 NULL。新的 turn-input 产物不再写入，所以新总数自然不增加这一项；旧历史行保留，仍计数。helperOutcomes 和它的 frozen 语义保持本批原样。

其余内容逐项 get 读取。workflow_get 的 owned 和产物展示各最多 32 条；artifact_list 读取另一张执行 artifacts 表，不是完整 workflow 产物历史（store.py:2836）。计划不承诺 Lead 能分页读全部 workflow artifact，不增加新历史接口；它通过各 owned task 的 get 验收当前交付与证据。列表是变化提示和总数，不能自行截断变化集合，最终过大仍由输入边界处理。

stash 起点照常通过 collect_claim_stash_facts 冻结进 turn input，_pin_shared_refs 从该 input 取 start，与完整 seal 观察按原多重集语义比较。新 Worker/Lead prompt 的投影去掉 sharedStash 起点，get/audit 仍可按原记录看事实。观察 version、entries、totalCount、unknown、comparison 等不改；不建 stash 表/文件，也不挪到消息里。

## 输入上限

### 代码声明与版本化依据

protocol/capabilities.py 定义 InputLimit 代码声明，registry 的能力视图公开它；它是 harness 能力，不是模型预算、数据库配置或健康观测记录。known 必须说明数值、计量、native scope、适用形态/版本和公开依据；unknown 明说未取得数值，不表示无限。版本不在已核实范围时不套用已知值；沿现有 discovery 的公开版本事实选择声明，不加配置读取或安装包扫描。

本轮只运行了公开版本查询，返回 codex-cli 0.164.0；启动器同时警告无法创建 PATH aliases，未重试该辅助操作。未读取安装包源码或用户配置。已从官方 tag rust-v0.164.0 解到 commit 6aaac694803004a63c6f393d153c96d22521a6b9，查阅其公开源码，得到以下依据；这些是版本化源码结论，没有重新做模型拒收实验。

| harness 形态 | 声明 | 依据与适用范围 |
| --- | --- | --- |
| Codex CLI 0.164.0，app-server turn/start | known，maximum=1048576，unit=unicode-scalars，scope=本次原生文本输入合计；等于上限允许 | 常量定义；计数方式；turn/start 校验。多个 input Text 项相加；toolOutput 的文本也计入，且不能与非空 input 合用。 |
| Claude Code CLI，-p / stream-json | unknown | [官方 CLI reference](https://code.claude.com/docs/en/cli-usage)说明输入形式，所查输入参数没有该单次原生 prompt 的固定数值。 |
| DSH CLI，ACP session/prompt | unknown | 官方 CLI 帮助的 ACP/headless 入口未给出这一上限；不把模型窗口外推成 ACP 字符上限。 |
| ZCode CLI，app-server session/send | unknown | 官方 CLI 说明及本项目 native_run.py:901 的接口，未取得该通道的数值依据。 |

Codex 的 text_char_count 调 Rust text.chars().count()，计 Unicode 标量值；非 BMP 字符计一个，组合字符按各自标量计，不是 UTF-8 字节、UTF-16 code unit 或字素簇。非文本输入项在该检查计零；outputSchema 不在这一求和式中。HMB 当前 turn/start 只有一个 Text 项，所以核对其最终 input_text；不要把 JSON RPC 外壳、schema 或文件大小加进这个文本上限。若以后改变输入项形状，按同一原生求和规则更新构造，不能沿用仅第一个文本的判断。

同一版本的 边界测试覆盖恰好上限与多个 Text 合计超限。拒绝响应为 invalid_params，data 含 input_error_code=input_too_large、max_chars、actual_chars；原生错误的细节要由适配器保留。这些是公开源码和测试的阅读，不是本批运行了上游测试。

### 执行进程中的计量与判定

PromptParts 区分 role（含 Lead 角色）、task、hostInput、context、delivery 和 format；角色/交付工具/inquiry 说明、credential 路径、JSON 键名与转义、所有分隔换行均计入实际所在部分。Host input 当前位于序列化 context 内，只归入一次，不能同时计进 context；用构造时的来源分区计量，不在最终字符串中搜索相同文本猜来源。复用公共 prompt 构造、canonical codec 和标准库，不写另一套 prompt 或自制 tokenizer。

在执行进程中，最终初始 prompt 完整构造后、调用 run seam 启动 harness/原生回合前，纯函数以实际声明的单位计量并核对。Codex 对有效 Unicode 字符串按标量计数，不用字节数替代；可编码性由既有输入/序列化边界负责，不再在内部重复验证。未知上限不拦启动；仍能对同一文本给出 UTF-8 bytes、Unicode scalars 等诊断大小，原生拒收时附上。所有 parts 之和必须等于对应 total。

Worker/Lead、native resume 的初始请求都经过该处。Router fast/review、维护的实际 structured_prompt 在自己的初始发送前复用纯函数，但错误仍走各自既有失败/结算路径。correction、wrap-up、问询回答不做预检；不新增每次发送的日志索引、计量表或证据文件。原生 harness 继承的用户配置、MCP、指令等不由黑板改写；本检查只声称覆盖已声明的输入字段。

声明在代码，构造和计量都在本次执行内存中；失败时把本次声明、版本及 parts 放进现有失败结果，供服务记 Host 原因，不再引入一份持久成功预检记录。未知值也不通过厂商某一次拒收自动修改代码声明。

### 同一种输入失败与 Host 边界

统一规范原因拟名 workflow.input-rejected，底层 code=HARNESS_INPUT_REJECTED。stage=preflight 表示本地按已知声明拒绝，stage=native-admission 表示原生拒收；两者都带 harness/形态/版本、声明的 value/unit/scope/source、实际 total/parts、发送及模型调用事实。原生明确返回 input_too_large 时保留 nativeMethod/nativeCode/max_chars/actual_chars 与有界 message；不另造第二种 Host 原因。

~~~json
{
  "reason": {"code": "workflow.input-rejected", "params": {"harness": "codex"}},
  "inputFailure": {
    "version": 1,
    "stage": "preflight",
    "harness": {"name": "codex", "form": "cli-app-server", "version": "0.164.0"},
    "limit": {"state": "known", "value": 1048576, "unit": "unicode-scalars", "scope": "turn-start-text", "source": "codex-0.164.0-public-source"},
    "measurement": {"unit": "unicode-scalars", "total": 1048577, "parts": {"role": 1000, "task": 1000000, "hostInput": 20000, "context": 26000, "delivery": 1000, "format": 577}},
    "nativeRequestSent": false,
    "modelStarted": false
  }
}
~~~

示例数字只演示求和与字段，实际来自纯函数。unknown 的 limit 用明确的 unknown 形状，不把声明值写为零；原生报出的 observedLimit 与冻结失败结果一起展示，不冒充代码声明。原生拒收即使无法确定是输入过长，现有 harness-error 也应附各部分的大小；证据不足不能只匹配“large”就强归此原因。

预检失败留下普通 attempt 的实际执行事实，没有原生模型调用；执行进程按已持有句柄/创建证据结算，不启动 native turn。服务导入失败后以同一系统 attention、revision/owner fence 进入 awaiting-host，Host 修改输入/任务或换 harness 再 continue；不在服务组装提示词，不伪装成一次没有 attempt 的准入拒绝。Router 和维护保留自己的失败处理，不开 Worker 的 Host request。

Codex 当前在发送前置 modelStarted=True（native_run.py:1269），不能据它认定原生已接收或调用模型。native-admission 的真实发送、native turn 是否开始和停止证据分别记录，不能仅靠错误码声称 stopped。已创建进程的 shutdown unknown 不释放工作区；输入原因不进入 quota/overload 自动重路由。更新 protocol reasons、结果解析、服务结算、CLI 与控制台中英文说明，Host 能查看同一种失败的两个阶段。

## 样本、版本与安装

tools/export_board_sample.py 仍最多选择 100 个 accepted 微任务，不包含未结束执行，读取源板只读，不复制日常状态目录。来源 schema 34 时，按 R3 从 input-reference、record-reference 和 version 4 turn 求实际文件闭包；旧内联历史只导数据库，shared-refs 无文件。不带 checkout、Git 大数据或用户原生会话。共享的同一文件只复制一次；不拉入未选任务的行。来源 schema ≤33 时，原行和内联形式保持来源 schema，不在导出时偷偷迁移。旧样本按 R3 建议使用结案后的内联历史升级，实施前由 Host 确认这一建议。

包版本、CONTRACT_VERSION 最终均为 0.60.0，schema 34；版本号在最后一个改契约的微任务统一修改，不放到 W3a/W3b。安装改中间号，必须等黑板空闲：无自主推进工作，全部 attempt 已结束、停止已确认、cleanup_pending 清除；unknown 不算 stopped，不为安装自动取消任务。0.59.5 的无升级补丁安装边界不适用本批。

安装演练从 0.59.5 已装提交 4edbe59e 起私有旧运行时和合成 schema 33 黑板；Host 若实际已装后续补丁，再从那个版本补演练。新安装器对旧服务只发其认识的字段，特别是 detach/idle/restart，不携新引用或输入能力字段。备份格式不变，不归档工作区文件或新输出记录；升级也不动这些文件，回滚沿旧数据库和旧运行时使用原文件。

演练包括：活动夹具/命令执行时升级被拒且不动它；确认停止清理后备份、升级、固定新 runtime、启动并核对 schema/contract、外键和引用；旧 accepted/delivered/awaiting-host、回收后的 worktree、旧部分与解冲突封存的展示/继续/验收；预检与原生拒收两阶段均能恢复到 Host 边界；导出/装入 schema 34 样本和旧 schema 样本升级；升级事务失败、启动失败和回滚保持旧 sealed 字节与原文件不变。使用合成私有板，实数据只由授权工具导出 accepted 样本到状态目录之外，用完由 Host 按规矩删除。

## 分批、写入范围与检查

各微任务在整合分支顺接，中间不安装；独立写者用隔离 worktree。未来测试统一经 uv run --frozen python -m hey_my_buddy.cli.checks，使用默认多进程，不设单进程环境变量。以下是实施检查，不是本回合已经运行的结果。W3a/W3b 改变整个读取基础，受影响集合是完整套件，由 Host 在各次合并树运行全套，不能凭 focused 绿色宣布该批通过。

| 微任务 | 前置、写入范围、必读文件 | 自己的检查与 Host 检查 |
| --- | --- | --- |
| 开工前库存与剩余字段 | Host 只读统计日常板上述库存，确认 R1、R3 的单一建议；R2 按已定的数据库唯一正文实施；读 W0、本计划、AGENTS、两个 ADR、floor fixture 和源 SQL。 | 交聚合计数、文件存在性边界与字段唯一归属；不复制黑板、不调用模型。库存不是验收。 |
| W1：Git 观察 | 只写 workspace.py 观察/选择部分和对应 Git/workspace 测试；读 prepare/verify/seal/restore/cleanup 全链及 Git 官方接口。 | runner 跑 tests/python/blackboard/tasks/test_workspace.py、test_workspace_git_truth.py、test_workspace_git_errors.py、test_workspace_windows_checkout.py、test_windows_workspace.py；以临时真实 Git 验证单 index、ignored、recovery、特殊文件名和大路径。报告实际平台，Windows/Linux 由 Host/CI 补证据。 |
| W2：代码声明与纯计量 | 可与 W1 并行；只写 protocol 输入声明/计量值类型、capabilities、registry、相关纯测试，不写 db/workflow/prompts/前端。读 RunRequest/live、各 native 输入、Codex 0.164.0 公开源码及 ADR-023/030。 | runner 跑 tests/python/buddy/harnesses/test_run_contract.py 和对应能力测试；新纯计量测试覆盖中文、非 BMP/组合字符、转义、各 parts 求和、等于/超过、unknown 和版本匹配。无原生模型调用。 |
| W3a：引用契约与升级 | W1/W2 和字段归属固定后；只拥有 protocol 引用/形式定义、db.py、schema_33_34.py、upgrade.py、新库 DDL、夹具生成器/库存/升级链及其测试。读所有 SQL 读者、历史步骤与 fixture 来源。包版本/CONTRACT_VERSION 此时不改。 | 自查旧 sealed bytes、三类摘要迁移、删除 child 列与可变正文、引用 NULL 约束、floor→34/33→34/新库结构和故障回滚；交可供 W3b 使用的固定列/字段契约。完整套件为受影响集合，Host 合并后跑；中间失败若来自未切换消费者明确记为待 W3b，不留在线双写让它暂时变绿。 |
| W3b：全部消费方切换 | 接 W3a；拥有清单中所有生产 Python 读写者、SQL、文件/结果阅读器、公开 get/audit/result/CLI 投影、样本导出、runtime/turn_io/partial outputs、相关跨组件测试，以及新产物/引用形状必需的 console parser/types/fixture 和 assets 调整；不把仍读旧正文的消费方留给后续。读完整读写/SQL 两表，不重写已定 schema。 | 自查所有 SQL 真列、旧输入/新引用、没有新 turn-input 和 workspaceManifest、helper、整合/回收/删除/项目位置、公开视图与样本闭包；必要前端变更须跑 frontend suite/build。完整套件为受影响集合，Host 在合并后的 W3a+W3b 树完整运行；仍失败不能进入后续验收。 |
| W4：续派与上下文 | W3b 后；只写 workflow/workspace 的 executionSelection、handoff/recovery、owned/artifact context、prompt 投影/Lead 文本和相应测试。读 scope/helper/continuation、stash、Lead 授权/list/get。 | runner 跑 tests/python/blackboard/tasks 的 test_workflow_preparation.py、test_workflow_worker.py、test_workflow.py、test_workspace_stash.py、test_workflow_stash.py、test_lead_workspace.py、test_workspace_output_views.py，以及 tests/python/buddy/roles/test_role_prompts.py；验证多轮大量文件不抄入请求、stash 只从 prompt 去掉、handoff/总数/helperOutcomes 口径、凭据分页与重放 fence。 |
| W5：执行前核对与输入拒收 | W4 后；拥有 run_execution/inline_run、公共 prompt 分区、四家 native 错误归一、runtime 终止结果、workflow 服务导入/attention、protocol reasons 和测试。读 scheduler/停止证据/Router/维护的原失败路径。此项为最后一个改契约的微任务，统一把 pyproject/uv.lock、CONTRACT_VERSION、签名快照改到 0.60.0。 | runner 跑 tests/python/buddy/roles、tests/python/buddy/runtime、受影响 harness、tests/python/blackboard/routing、test_execution_scheduler.py、test_workflow_validation.py、test_workflow_boundaries.py；夹具证明预检无 native/model 调用却有 attempt、两阶段同原因、Host 能继续、unknown 拒收附 parts、无 quota 自动重试。correction/wrap-up/问询回答保持无预检。 |
| W6：控制台呈现与 owning docs | W5 字段固定后；只拥有既定输入失败字段的 console parser/types/reasons/显示、assets、owning references、技能及必要入口文档；公开 Python/CLI 契约投影已在 W3b/W5 完成，此项不改 Host/执行契约。读 ADR-029 及现有 get/audit/frontend。 | 前端 suite、build，布局变化跑 test:browser 并由 Host 看实际页面；文档路由、Lead 指引和中英文与已定字段一致，committed assets 一致。不以新增后端字段来解决展示问题。 |
| W7：Host 整合与安装 | W6 后，Host 读全差异、检查同批删除、记录 docs/acceptance，做私有安装演练和最终空闲安装。 | 最终树默认多进程完整 runner；安装/回滚/样本演练、独立审查与实际平台证据分别记录。Worker 交付、检查通过、合入、安装是不同状态。；另外复现 2026-11-03 gridmen 的场景（开发者要求）：在私有服务上用假 harness，让 existing 工作区里生成几千个未跟踪文件，跑一回合再续派，核对 turn input、manifest 与提示词都不随文件数增长，Git 观察不撞命令行上限；用真实 harness 再跑一遍属于付费调用，届时另请开发者批准。 |

W1/W2 可并行；W3a→W3b→W4→W5 顺接，因为它们共享 workflow.py、workspace.py、run_execution.py；W6 接已固定契约，只做前端呈现与文档。W3a 的结构可暂未接全，不作为可安装版本；W3b 完成后只保留当前在线契约。前端准备依赖用支持的 Node 版本 npm --prefix apps/console ci，最终源码跑 npm --prefix apps/console test 与 run build；布局变化独立 run test:browser。最终完整命令为 uv run --frozen python -m hey_my_buddy.cli.checks；本批不设置单进程。真实 harness/模型未测的能力不写成实测，Git/安装的 macOS、Windows、Linux 证据分开。

## 一并删除与更新

同批删除 run/continuation 的 workspace_manifest_json、child 的整列、旧通用 artifact.manifest_sha256 及其混合身份比较、当前 SQL 的正文 json_extract/project_id_sql、向数据库复制新 manifest/输出的写者、新 turn-input 产物的全部写入/依赖、新 receipt.workspaceManifest 及其任何替代输入回传、续派路径抄写、recovery 的 256 条限制、ls-files 的批量 argv、Lead 全量 owned/context.artifacts、提示词中的 stash 起点与“全部 current states”承诺。保留旧 sealed-inline 阅读、历史 turn-input/旧 result、原生 checkpoint、现有 request/input/manifest 文件与已冻结步骤；不清理输出/stash 记录内部格式。

替换依赖旧在线字段形状的测试，0.59.5 的回归目的并入新 prompt 投影测试；旧封存 byte-preservation 和旧 delivered 继续测试继续存在。备份测试不改成归档工作区，声明测试不建数据库配置，预检测试不假装没有 attempt 或验证未授权的后续交互。

文档先更新 docs/reference/workflow.md、workers.md、harness-integration.md、operations.md、各 harness owning reference 与 architecture，再更新技能/Lead 指南及 docs/README 的路由摘要。修正文档的 manifest/schema；写明单次输入声明的形态/版本/计法、Lead 变化集合和 get 读法、样本边界与默认多进程。backlog 事故条目只在实际验收后转为验收记录；ADR、0.59.5 安装记录和历史证据不删除。若整份旧文档被替代，先迁出仍有效的边界，再与实现同批删；没有依据不整批删 adr031 设计记录。

## 待定项

没有了。原 D1–D7 已关闭；R2 由 Host 定，shared-refs 唯一的一份记录留在 `workflow_artifacts.manifest_json`，只拆出它的摘要列；R1、R3 的建议由 Host 于 2026-11-03 确认，开发者同日同意按本计划执行。

本次计划修订的验证仅为源码定位、公开版本/源码资料、文件内容、链接与改动范围复核；没有运行产品检查套件、上游测试、原生模型探针或日常黑板库存查询。提交按 Host 本次要求留给 Host，Worker 不操作暂存区、分支或标签。
