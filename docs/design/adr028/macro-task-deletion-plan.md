# 宏任务删除：执行计划（第 2 版）

2026-10-17，按 Host 与独立模型对第 1 版的审查意见修订；源码基点仍为 `20406ee6`，schema 24、软件 0.45.1、`CONTRACT_VERSION=0.45.0`。本次只写计划，未实现、演练或安装。依据为 [ADR-028](../../decisions/028-macro-task-deletion.md)、[AGENTS.md](../../../AGENTS.md) Invariants、[第 4 步批次二](../adr029/step4-remaining-plan.md)；开发者本日新决定以本轮 Host 输入为准：L7 之前不保留结果事实，先打删除标记、再删物理内容、最后事务删记录。下文“拟”表示实施方案；源码路径从 `src/hey_my_buddy/` 起算。

## 1. 删除集合与表

依据：`blackboard/store/db.py` 的 `CORE_SCHEMA`、`EVALUATION_SCHEMA`、`WORKFLOW_SCHEMA`、`HARNESS_SCHEMA`、`ROUTER_SCHEMA`、`CATALOG_PENDING_SCHEMA`、`HOST_CONCLUSION_TABLE`、`FIX_SCHEMA`、`REMINDER_SCHEMA` 定义当前 57 张业务表；`tasks/macro_tasks.py:MEMBERS_SQL`、`_members` 与 `tasks/micro_task.py:RELATION_SQL` 给出归属。ADR 背景里的数量是历史值。

删除只接受真实宏任务与 `run:<rootRunId>` 两种控制台分组，不新增普通 execution 的删除入口。`M` 为宏任务编号；`R` 为 `workflow_runs.macro_task_id=M` 的全部微任务，并沿 `workflow_children.parent_run_id` 收齐协助任务、沿 `workflow_lead_tasks.lead_run_id` 收齐 Lead 名下的树，递归至闭包；`run:` 从该根用同一闭包建 R。`D` 为 R 全部历史 `workflow_routes.decision_id`；`T` 为 R、`decision_requests.decision_id∈D` 的 task_id 与 `router_dispatches.decision_id∈D` 的全部 task_id 的并集；`A` 为 `attempts.task_id∈T` 全部代次；`U` 为 R 的全部回合。归属一致由现有提交、协助与 Lead 写入方保证，按不变量使用；破坏它是内部错误，不新增公开的范围冲突错误码。集合不经列表/时间轴的分页、筛选或截断。

已核对的根因修订：`routing/decision.py` 派下一位 Router 时更新 `decision_requests.task_id`，`router_sequence.reserve_dispatch/dispatches` 才保留所有内部任务。当前 `workflow._routing_tasks` 与 `micro_task.RELATION_SQL` 的 dr 联接只看最新 task_id，`storage.inspect` 的 native_owners 又依赖前者；实施须在这些归属来源统一纳入 router_dispatches，与删除引擎共用完整关联，不能只在 DELETE 时临时补 SQL。`RELATION_SQL` 的 owned_executions 同样要收齐各次 dispatch，停止资格、读取与存储盘点才一致。这是本任务对路由归属根因修复的范围修订。

Host 审后的删减（2026-10-17）：第 1 版审查提出的无宏任务 Lead 分组修订（让 Lead 名下的树归到 Lead 的 `run:` 分组）不随本批做。当日日常看板只读计数，没有宏任务的 Lead 名下微任务为 0 个。`run:<root>` 的 R 照上面的闭包收齐 Lead 名下的树，确认框的数来自同一个闭包；分组显示的修订记入待办，碰到时再做。

| 表 | 命中的行与处理 | 当前依据 |
| --- | --- | --- |
| `macro_tasks` | `macro_task_id=M`，最后删除 | `macro_tasks.attach_macro_task` |
| `workflow_runs` | `run_id∈R`，删除；其当前/最终回合、执行、产物指针随行消失 | `db.WORKFLOW_TABLES` |
| `tasks` | `task_id∈T`，删除；包括路由执行 | `micro_task.RELATION_SQL`、`workflow._routing_tasks` |
| `attempts` | `attempt_id∈A`，删除全部代次 | `db.CORE_SCHEMA` |
| `attempt_process_identities` | `attempt_id∈A`，删除 | `db.CORE_SCHEMA` |
| `attempt_interruptions` | `attempt_id∈A`，删除 | 同上 |
| `attempt_shutdown_evidence` | `attempt_id∈A`，删除 | 同上 |
| `execution_issues` | `attempt_id∈A`，删除 | 同上 |
| `attempt_activity` | `task_id∈T` 或 `attempt_id∈A`，删除 | 同上 |
| `attempt_reminders` | `task_id∈T` 或 `attempt_id∈A`，删除 | `db.REMINDER_SCHEMA` |
| `messages` | `task_id∈T` 或 `attempt_id∈A`，问询、回答与投递内容一起删除 | `db.CORE_SCHEMA`、`tasks/inquiry.py` |
| `artifacts` | `task_id∈T` 或 `attempt_id∈A`，删除记录；location 只作文件归属证据，不直接授权删除任意路径 | `db.CORE_SCHEMA` |
| `events` | `task_id∈T` 或 `attempt_id∈A`，删除；没有外键的 attempt_id 同样处理 | `db.CORE_SCHEMA`、`router_history._append_event` |
| `commands` | 不删行；按第 3 节移除内容与指向被删内容的标识 | `store._receipt`、`_store_receipt`、`command_receipts.stored_response` |
| `resource_claims` | `task_id∈T` 或 `attempt_id∈A`，删除所有状态 | `db.CORE_SCHEMA` |
| `workflow_turns` | `run_id∈R`，含输入、结果、来源与原生会话绑定，删除 | `db.WORKFLOW_TURNS_DDL` |
| `workflow_requests` | `run_id∈R`，含协助/关注/报告、决定与 child_task_id，删除 | `db.WORKFLOW_TABLES` |
| `workflow_children` | `parent_run_id∈R` 或 `child_task_id∈T`，删除 | 同上 |
| `workflow_lead_tasks` | `lead_run_id∈R` 或 `owned_run_id∈R` 或 `lead_turn_id∈U`，删除 | 同上 |
| `workflow_continuations` | `run_id∈R`，删除；先拒绝仍 recorded/queued 的续接 | 同上 |
| `workspace_reservations` | `holder_task_id∈T` 或 `parent_run_id∈R`，删除 | 同上 |
| `workflow_scope_versions` | `run_id∈R`，删除 | 同上 |
| `workflow_workspace_conflicts` | `run_id∈R`，删除；对应现场/封存按第 4 节处理 | 同上、`workspace.resolve` |
| `workflow_host_conclusions` | `run_id∈R` 或 `attempt_id∈A`，删除；须先于产物与整合 | `db.HOST_CONCLUSION_TABLE` |
| `workflow_integrations` | `run_id∈R`，删除记录；目标仓库中的用户提交和目标检出不删 | `workflow._accept_verify`、`workspace.integration_verify` |
| `workspace_cleanup_plans` | `run_id∈R`，删除；applying 时先拒绝 | `workflow.cleanup_apply` |
| `agent_credentials` | `run_id∈R` 或 `task_id∈T` 或 `attempt_id∈A`，删除 | `db.WORKFLOW_TABLES` |
| `workflow_artifacts` | `run_id∈R`，输入与输出均删；写入方 source_task_id 只有本 run 或 NULL，无外部 run 的链接需要断开 | 同上 |
| `workflow_suggestions` | `run_id∈R` 或 `attempt_id∈A`，删除 | 同上 |
| `workflow_fixes` | 任一端在 R，删除关系；提交时已限制修正与原微任务同属宏任务，仍按两端列清依赖 | `db.FIX_SCHEMA`、`macro_tasks.attach_macro_task`、`fixes.view` |
| `workflow_routes` | `run_id∈R`，全部 owner_generation 的决定，删除 | `db.WORKFLOW_TABLES` |
| `decision_requests` | `decision_id∈D` 或 `task_id∈T` 或 `attempt_id∈A`，收齐 D 后删除 | `db.EVALUATION_TABLES` |
| `evaluation_decisions` | `decision_id∈D`，含 task 文本、reason/error，删除 | 同上 |
| `router_requests` | `decision_id∈D`，冻结输入，删除 | `db.ROUTER_SCHEMA` |
| `router_dispatches` | `decision_id∈D` 或 `task_id∈T`，删除 | 同上 |
| `router_claims` | `task_id∈T` 或 `attempt_id∈A`，删除 | 同上 |
| `router_outcomes` | `task_id∈T` 或 `attempt_id∈A`，以及 plane=work 且 request_id 属于 D 对应请求的行，先于事件删除；本批不保留事实，见第 3 节 | 同上、`router_history.record_outcome` |
| `quota_current` | 不删当前额度；`retry_consumed_by∈D` 时清空这个决定链接，保留 consumed_at 防止清理重新打开已用过的重试 | `routing/quota_routing.py:_marker`、`_write_marker`、`retry_facts` |

另外 19 张表不整体属于目标，保留：`meta`、`cursors`、`service_issues`、`catalog_observations`、`catalog_current`、`catalog_pending`、`model_service_notes`、`evaluation_state`、`buddies`、`evaluation_preferences`、`evaluation_catalog`、`model_service_concurrency`、`model_facts`、`harness_quota_observations`、`harness_account_reads`、`harness_health`、`router_settings`、`router_buddies`、`reminder_settings`。命令与额度表仅按表中规则更新，不整体删除。`quota_current.retry_consumed_by` 是决定编号，清空链接但保留 consumed_at，不能因为删任务而再开一次已用过的重试；依据 `routing/quota_routing.py:_marker/_write_marker/retry_facts`。

## 2. 顺序与最后的删除事务

拟新增 `blackboard/tasks/macro_task_deletion.py`，只在删除业务边界校验一次；流程为“第一个短事务打标记 → 事务外物理删除 → 最后一个事务删全部内容行”。沿用 `Database.write` 的 BEGIN IMMEDIATE，外键始终开启，现有 ON DELETE RESTRICT 保留，不改 CASCADE、不关外键、不用可延迟的 Router 复合外键掩盖顺序错误。

第一个短事务核对控制台权限、目标与第 6 节资格，求完整集合并重算 microTaskCount；与确认框带回的数不同即 REVISION_CONFLICT，尚未动文件。名下集合只会增长，数量相等即确认的集合未变，不再引入 scopeToken。通过后写第 7 节删除记录，state=deleting，业务行均保留。已存在 deleting 记录的重试直接使用原记录及仍在库内的归属证据，不新建标记、不因部分文件已消失要求重新确认；deleted 则返回原删除摘要。

删除入口先取得 storage.locked，覆盖首次标记、物理阶段与最后提交；物理阶段按第 4 节顺序删除。失败或进程中断不删业务行、不改标记为 deleted；宏任务及 run: 分组显示“删除未完成”，用户从控制台重试，从头执行，已经不存在即该项完成。没有清理项表、没有子表、没有阶段游标或另一套 pending 围栏。业务记录留到最后，提供重试所需的任务、会话、allocation 与引用证据。不能证明物理归属的对象留原处，按现有存储分类列出；它们不授权猜测删除，也不阻止已证明部分与数据库记录最终删除。

物理阶段无真实 I/O/协议失败后，在最后一个 BEGIN IMMEDIATE 内按以下顺序删除；此时同一删除记录仍须为 deleting。因标记禁止重新准入，集合不再增长；若不变量被破坏则内部失败，保留标记，不扩展到另一宏任务。

1. 将命中 `commands` 更新为第 3 节无内容形式，清空命中的 quota_current.retry_consumed_by；删除属于 T/A/D 的 router_outcomes，须先于 events/attempts/tasks。没有事实归一、复制或新事实表。
2. 删 `workflow_host_conclusions`、`workflow_lead_tasks`、`workflow_fixes`、`workflow_requests`、`workflow_continuations`、`workflow_suggestions`、`workflow_scope_versions`、`workflow_workspace_conflicts`、`workspace_cleanup_plans`、`workspace_reservations`、`agent_credentials`。
3. 删 `workflow_integrations`、`workflow_artifacts`、`workflow_turns`、`workflow_children`、`workflow_routes`；所有指向这些对象与 workflow_runs 的子表先处理。
4. 删 `router_claims`，再删 `router_dispatches`、`router_requests`、`decision_requests`、`evaluation_decisions`，包括换过 Router 的决定的全部内部任务。
5. 删 `messages`、`artifacts`、`resource_claims`、`attempt_activity`、`attempt_reminders`、`attempt_process_identities`、`attempt_interruptions`、`attempt_shutdown_evidence`、`execution_issues`、目标 events；随后删 `attempts`、`workflow_runs`、`tasks`、`macro_tasks`。
6. 原删除记录改为 deleted 并写 deleted_at；追加无内容的 macro_task.deleted 通知事件（task_id/attempt_id 为空，只含分组编号与删除微任务数），同事务提交；保持事件序列高水位，调用 `BoardStore._notify` 使汇总与控制台缓存失效。

events 还要包含 task_id 为空、但固定 payload 格式中的 decisionId/requestId 属于 D 的路由事件；由写入方固定格式精确匹配，不做任意文本子串搜索。事务任一步失败整体回滚，此时物理内容可能已删，deleting 标记和原业务行留下供重试，界面不能把这些行继续当可用成果。故障注入与外键检查验证顺序，不在每层扫描全库。

## 3. 结果事实与命令记录

开发者 2026-10-17 已定：L7 之前删除不保留结果事实。当前 `router_outcomes` 只有 Router 回答事实，`router_history._fact_entries` 从 events 读，`_record_rows` 从决定与执行记录推导。删本宏任务路由执行的 router_outcomes、events、决定与执行，Router 页相关计数和跳过窗口会少掉这些记录；这是本批接受的效果。当前表与读取形式均不重建、不转换，不建 execution_outcome_facts，不实现 L7。L7 完成后 ADR-028 第 6 条恢复按原文执行，由那时的升级负责分离与保留。

commands 行长期保留，只加可空 `deletion_id REFERENCES macro_task_deletions(deletion_id) ON DELETE RESTRICT`。NULL 统一表示对象未删除，非 NULL 统一表示对象已随该删除记录删除；删除前后均不从 JSON 大小、字段有无或写入时间猜形式，不加 response_state。最后事务把命中行的 task_id、attempt_id 置空、response_json 改为 `{}`、deletion_id 指向本标记，保留 command_id、kind、request_hash、subject、created_at 这些提交身份。

命中命令除 task_id/attempt_id 外，收齐 requests/children 的 decision_command_id，以及 continuations、scope、conflicts、integrations、cleanup、conclusions 的 command_id；按 `command_receipts.stored_response` 每种 kind 的固定字段收齐 runId、targetRunId、requestId、planId、artifactId 等对象，覆盖标识列为空而 response_json 含目标的收据。`workflow.workspace_resolve` 保存的文件路径列表及小回答同样清空。依据：`store._store_receipt/_receipt`、`command_receipts.stored_response/replay_response`。

deletion_id 非空的收据在入口读取旧 run 之前处理：只核对命令编号、kind 与 subject，不重算 request_hash（`decide`、`conclude` 的摘要含着随内容删掉的值，Host 2026-10-17 定），相符即只返回 `{"commandId":"...","deleted":true}`，不读已删的 run、不返回原内容与控制凭据、不再执行。入口现在有些先取 run/授权再查收据，实施要使 deleted 收据能在旧 run 不存在时完成上述身份校验与回复；current 收据的授权规则不变，不在每层加相同判断。无收据的旧 run 仍 NOT_FOUND。不建 deleted_submission_ids，不为提交重放新增 CONTENT_DELETED；名下全部了结后才删，指名宏任务的提交由第 6 节删除记录拒绝。

## 4. 物理内容与 Git 引用

顺序固定为原生会话 → 受管检出 → 黑板引用 → allocation 记录目录与封存 → 私有区与执行证据（含 CLI 凭据文件）。这些动作都在两次数据库事务之间，依据仍在库内的行；SQLite 事务不跨 Git、harness RPC 或文件遍历。移除 allocation 记录之前先完成其检出与 refs；移除私有证据之前先完成原生会话删除。

| 对象 | 当前归属依据与拟删除动作 |
| --- | --- |
| 原生会话 | 按第 5 节，从创建收据得到精确 sessionId；不接受浏览器传入会话编号 |
| 受管 checkout | `workspace.resolve_allocation`、`_worktree_record`、`_cleanup_proof` 的 allocation/repository/checkout 身份与 hey-my-buddy 专用锁；取 allocation 自己的锁，复用 `cleanup_remove/_cleanup_resume_registration` 单个 worktree 删除与中断证明。宏任务确认替代普通回收的验收前提，不伪造 acceptance/integration；Host existing checkout 不删，附着用户分支、换锁或身份不明的目录留原处 |
| 黑板 Git refs | 从仍保留的原始输入、历次回合输入/输出、冲突处理记录和 allocation 身份收齐 workspaceId，自己调用不设数量上限的 for-each-ref 枚举精确 namespace；覆盖 input、staged、outputs、resolutions、abandoned、retained/*。对照清单证明归属，按预期旧 OID 用 update-ref 删除；已无 ref 成功，OID 改变保留并报 REF_CHANGED。不用 `_allocation_refs`，它在当前源码最多返回 64 个 |
| allocation 记录目录及封存 | `workspace._workspace_directory/_prepare_locked/_seal_output/_finish_output/_finish_record`、`workflow._allocation_provenance` 与 workflow_artifacts 的固定绑定，定位 `<state>/workspaces/<allocationId>/`。检出与 refs 完成后删整个专属记录目录，含 outputs/resolutions/abandoned 的 patch 与清单；检出已回收也删这些残留。只凭 artifacts.location 不授权删任意用户文件 |
| 各 adapter 私有根 | `private_dirs.micro_task_root/native_root/attempt_root` 为 `<state>/harnesses/<adapter>/micro-tasks/<sha256(taskId)>/`，按 T 删整个根，含 native、attempts、Router 的 frozen-input 与 no-tool-cwd；实际承载 harness 从冻结 buddy/dispatch 与原生收据确定，不把 decision 当唯一 adapter |
| 旧布局私有根 | `<state>/harnesses/zcode/<sha256(taskId)>/` 及其他 adapter 根下旧布局项：按 T 的精确摘要和既有归属记录证明后同删；未知项照旧只列出，不按“看起来像缓存”删除。依据 `storage.inspect` 的旧 ZCode/legacy-layout 分支 |
| 每次执行证据 | `<state>/attempts/<taskId>/<attemptId>/` 属于 T/A：spawn.intent/marker、执行日志、task.txt、turn-input.json、turn-output.json、inquiry.results.jsonl 等随整个 `<state>/attempts/<taskId>/` 删除。依据 `protocol/execution.py:launch_execution`、`buddy/runtime/execution.py:Execution.__init__/_execute_selected` 的证据目录与 ExecutionContext 构造、`buddy/harnesses/base.py:ExecutionContext`、`buddy/roles/turn_io.py:write_turn_files/inquiry_paths` 的落盘调用；不从 attempts.log_paths 追入任意外部目录 |
| CLI 控制与提交凭据 | `home.control_file_path`、`cli/main.py:_control_path/_save_control/_submission_token`：逐个 R 删除 `<state>/controls/<runId>.g<N>.json`，N 为正整数代次；以对应 tasks.request_id 的 UTF-8 sha256 前 32 位精确定位 `<state>/submissions/<摘要>.json`。只删这些任务的文件，保留 controls/submissions 的其他项；临时项只有能由其文件绑定证明归属时才删 |

核对与 Host 输入的一处差别：Router 冻结输入确是用户文件的副本，但生产路径是 `buddy/roles/router.py:prepare_router_review` 把 `private_dirs.context_root(context,native.name)` 传给 `router_input.prepare`，因此 frozen-input 在 harness 私有 attempt 根下，不在 `<state>/attempts/<taskId>/<attemptId>/` 下；两处都要删，不能因位置纠正而遗漏副本。无工具 Router 用同处的 no-tool-cwd。`router_input.discard` 正常会清副本，异常残留仍随根清除。

共享只按结构化记录中的精确 workspaceId/checkoutId/repositoryId、artifact 绑定、目标身份判断：`workflow_runs.workspace_manifest_json`、workspace_reservations、workflow_artifacts 的固定输入清单、workflow_integrations 的目标。若另一微任务仍依赖同一物理对象，删除边界返回 MACRO_TASK_STORAGE_IN_USE，给 holderRunIds 与对象种类；不能连带删另一任务。相关字段在边界解析一次，关联限定在候选精确身份；别处一行坏 JSON 不构成所有删除的阻塞，目标自身证据损坏仍失败。不得调用 `storage._workspace_reference_rows/_allocation_reference_sets` 扫所有历史内容来证明共享。

归属不明的目录/引用原地留下；`storage.inspect` 沿现有 workspaces、harnesses、zcode 分类列 owner-unproven/无记录项，不增删除专用分类。对已经没有 checkout 的 allocation 记录目录，补齐现有 workspaces 枚举的遗漏，照现有字段列出，不能仍然只认 checkout 是否存在。`private_dirs.remove_tree` 的根路径/链接边界照旧，短 socket 后备目录沿 `cleanup_inquiry_fallback` 的固定绑定清除；不删用户分支、tag、stash，不作仓库级 worktree prune/gc。

旧存储回收材料按实际格式处理：`storage.apply/_save_receipt/_finish_pending` 的 receipt 只保存 planId、removedBytes、removed/skipped 的 id/path/bytes/reasons，以及 pending 的 path/tomb/fingerprint/device/inode 等黑板身份，不含标题、用户文件清单或补丁，保留。`storage.plan/inspect` 的持久候选有时含 orphan manifest、retained 与清理证明；仅当这些字段实际带目标任务的内容/用户文件清单时，在物理阶段删除相应计划文件（它是可重新生成的整份计划，不重写一半冒充原已确认计划）。只含黑板路径与编号的文件不改写。依据 `inspect`、`_orphan_workspace`、`plan`、`_save_receipt` 的实际字段；不因出现 taskId 或 path 就清除所有历史收据。

## 5. 各 harness 会话

本节区分源码调用事实与 Host 提供的原生探针事实；本回合未运行 harness 或模型。CLI 适配路径的依据是本源码基点，不将其描述为所有原生 app 版本都如此。

| harness 形式 | 当前存放与源码依据 | 删除方案 |
| --- | --- | --- |
| DSH CLI，ACP | `dsh/native_run.py:run/_launch_agent/session_facts`：Worker 的微任务私有 native/sessions 与同级 storages；快调用/审阅在调用私有 native/dsh-home/sessions 与同级 storages，用户登录/设置在用户 home | 删 T 对应的整个私有根，记录与索引一起消失；不调用已移除的 grouped listing/archive，不动用户 home |
| ZCode CLI，app-server | `zcode/native_run.py:_spawn_app_server/session_facts`：私有 native/sessions.sqlite、native/storage 与会话摘要绑定；另核对第 4 节旧布局 | 删整个私有根，SQLite 旁文件、storage 和绑定一起删；无需用户会话库删除接口，本仓库也没有这样的调用 |
| Codex CLI，app-server | `codex/native_run.py:_configure_worker/session_facts`：普通会话在用户原生库，私有 native 根留绑定；`_configure_fast/_configure_review` 则 ephemeral=true | 经同 harness 的 thread/delete 删能证明创建的普通会话，再删私有证据；ephemeral 的私有证据照删，不用用户目录文件操作代替接口 |
| Claude Code CLI，stream-json | `claude/config.py:execution_args`、`native_run.py:session_facts`：每次新 session-id，传 --no-session-persistence，storageScope=none | 没有本调用路径产生的持久原生会话可删，删黑板私有证据根；不扫描用户原生会话库 |

已核实的接口依据由 Host 提供：2026-10-17，Codex CLI 0.162.1 的 app-server，在临时空 CODEX_HOME、不登录、不起回合、不调用模型时实测 `thread/delete {threadId}` 返回 `{}`；会删除 rollout、shell 快照及 session_index.jsonl 对应行，也能删除另一个 app-server 进程写入、当前 thread/read 可读取的会话。这是原生能力已存在的证据，仓库适配器当前尚无删除调用，实现放 `buddy/harnesses/codex/`，沿现有 app-server 连接/初始化路径，不建立另一套模型执行或用户配置 home。

已删除或从未存在的会话：Host 实测 JSON-RPC code=-32600，message 为 `no rollout found for thread id <id>`；格式错误的编号也是 -32600，message 为 `invalid thread id: ...`。因此只接受“code=-32600 且 message 以 `no rollout found for thread id ` 开头”为已经没有了；单看错误码不够，其他协议、权限、I/O 错误均失败并保留 deleting 标记。需在 `tests/probes/` 增加按需运行的无模型探针，钉住该消息前缀、重复删除、非法编号、跨进程删除与三类原生内容消失；用独立临时 CODEX_HOME，记录实际 CLI 版本，正常检查不运行它。Codex 的 logs_2.sqlite 诊断日志可能仍提及会话编号，属于 Codex 自身诊断，不由黑板清理；不把诊断残留说成会话删除失败。

归属沿 `tasks/worker_sessions.py:_inventory/_completed_codex_turn` 的创建收据：任务/执行绑定、初建或重建的 thread 身份、checkpoint/provenance 与停止确认；续接同一会话只删一次，不按 cwd/标题或用户会话列表猜归属。中断创建而未有最终回合的情况，要由已有 thread/start 耐久回执和 nativeCheckpoint 证明；证明不了的保留并报告，不能扩大到用户会话库。登录、原生设置与别的任务会话均不删；会话仍被别的任务实际持有时按精确身份共享依赖拒绝。

## 6. 拒绝与回复

资格沿 `micro_task.FILTER_SQL/RELATION_SQL`、`workflow._stop_proven`：R 只允许 accepted、concluded、cancelled、failed；delivered 是待验收，拒绝。tasks 的 queued/running/cancelling/reconciliation-needed，run 的 executing/awaiting-host/waiting-helpers/waiting-owned，以及 open 请求、recorded/queued 续接、pending 路由均拒绝；所有历史路由执行也检查。A 必须全部 finished 且 shutdown_confirmed=1，悬空 active/selected attempt 或没有尝试却声称运行不能当已停止。取消/失败未有结论无需补 Host 结论。

错误 `MACRO_TASK_NOT_SETTLED`，details 为 `{macroTaskId,blockerTotal,blockers:[{runId,taskId,state,runState,reasonCode,resolution,attemptIds}]}`；返回总数与前 50 项，界面明确只是前若干项，不做 blockers 游标分页。reasonCode 区分 queued/routing/running/awaiting-host/review/stop-unconfirmed，resolution 为 wait-or-stop、host-decide-or-review、resolve-stop 等固定代码。还有 applying 的旧 workspace_cleanup_plans 时返回 MACRO_TASK_BUSY，等其完成再预览；共享物理依赖返回 MACRO_TASK_STORAGE_IN_USE。

删除标记写下后，用一个共用检查放在现有提交、继续、重试与回收准入边界：指名宏任务的 `macro_tasks.attach_macro_task` 拒绝 deleting/deleted；对 R 的 continue、task.retry、reclaim（包括内部 cleanup 准入）拒绝 deleting。错误 `MACRO_TASK_DELETED` 的 details 为 `{macroTaskId,state,deletedAt,resolution:"create-new-macro-task"}`，state 明确 deleting 或 deleted；对微任务操作返回 `TASK_DELETING` 并带 deletionId，不伪称已删完。宏任务标记检查必须在提交事务中，也覆盖 macroTaskOf 和协助/Lead 的继承归属；不从删除记录恢复宏任务。不存在且无删除记录的目标仍 NOT_FOUND。

C-Two 沿 BoardError 形状返回 `{"error":{"code":"...","message":"...","details":{...}}}`；HTTP 沿 `console/server.py:Handler._error` 返回 `{"ok":false,"error":{...}}`。未了结、共享、删除中/围栏忙与计数变化映射 409，已删除映射 410，未知目标 404，来源/CSRF/非控制台权限 403；message 与原因参数依批次二结构化原因规则，不塞中文操作散文。

成功或未完成的删除回复为 `{"deletionId":"...","macroTaskId":"...","state":"deleting|deleted","deletedAt":null,"microTaskCount":0,"duplicate":false}`，HTTP 外层仍为 `{"ok":true,"result":...}`；deletedAt 仅在 deleted 时有值。物理失败回复保留 deletionId/state=deleting 及本次失败的固定 reasonCode/对象种类，界面显示删除未完成和重试入口；错误路径不保存任务标题或内容进标记。再次删除同目标只恢复原标记，不新建第二条；最终删除通知只写一次。命令重放的两字段回复见第 3 节。

## 7. 删除记录与 schema 24 → 25

拟建 `macro_task_deletions(deletion_id PRIMARY KEY,target_kind,target_id,state,started_at,deleted_at,micro_task_count,UNIQUE(target_kind,target_id))`。target_kind 仅 macro-task/micro-task-tree，target_id 分别为真实 M 或 run: 分组编号；state 仅 deleting/deleted，约束 deleting 时 deleted_at 为 NULL、deleted 时非 NULL。只存身份、状态、时间、微任务数，不存标题、项目路径、Host、任务清单或错误日志，不增加宏任务业务状态，不写 meta。commands 只加第 3 节 deletion_id；新库和升级库统一当前形式。

按 AGENTS 新建 `blackboard/store/upgrade.py`，只做 upgrade base 24 → 25；本批其他存储改动以后扩展同一个待做升级，不另开历史链。`db.SCHEMA_VERSION=25` 与新 DDL 同次修改；`Database._require_current_schema/initialize` 继续只认当前结构，启动不自动迁移。`install/upgrade.py:preflight/idle_snapshot/upgrade` 先按旧结构验证空闲与停止证据、做验证过的 24 备份，在已有独占安装/服务锁与停止旧服务后执行迁移事务，建空删除表、为旧 commands 补 deletion_id=NULL，最后写 schema_version=25；失败恢复完整 24 备份。不能先用新 SCHEMA_VERSION 的当前 preflight 拒掉 24，也不在打开旧库时顺便加表冒充升级。

样板仍是 `tests/fixtures/upgrade-base/metadata.json` 指定的 0.42.0/90bd0148/schema 24；`tests/python/blackboard/store/upgrade_fixture.py:load_fixture` 先在私有根重绑定，再作为迁移输入。这次不把样板改成 25、不重生成基线。修改 `test_upgrade_fixture.py` 中“24 是当前”的断言，明确运行显式升级，再比较新建 25 的 sqlite_master/外键/索引/约束；未改变表逐行/指纹保持，commands 仅多 NULL 列，新删除表为空。安装沿用上一次升级的流程（`627d0f6c^` 的 `install/upgrade.py`）：先升级，再从升级后的看板取 `expected`，`verify_started` 照旧比对新服务启动前后；升级本身的正确性由样板升级测试与 Host 在日常副本上的演练证明，不在每次安装时另算一份转换后的指纹（Host 2026-10-17 审微任务 1 时改）。补迁移失败回滚、重复安装、非 24 输入拒绝、24 启动不写入、样板摘要不变。25 真在实际看板跑过后才按 AGENTS 消费本升级，更新基线提交并重新生成样板，由 Host 留验收证据。

## 8. 控制台入口与契约

拟新增命名 C-Two `macro_task_deletion_preview` 与 `macro_task_delete`。参数沿用 macroTaskId，值只准真实 macro- 编号或 run: 分组；删除携带预览的 microTaskCount 与 confirm=true，不带 scopeToken/原生 sessionId/任意文件路径。预览返回计数、阻塞摘要与已有删除状态；恢复 deleting 不再新建执行计划。幂等身份为唯一 target_kind/target_id 的删除记录，不额外新增删除计划 token。

入口沿 `console/server.py:Handler.do_POST` 的 `/api/command`、`_trusted_request/_authenticated_path/_write_trusted`：精确 Origin、Host/Sec-Fetch-Site 与当前会话 CSRF；`ConsoleServer._command` 拒绝浏览器自带权限字段，由服务端附 consoleAuthority。加入 CONSOLE_OPERATIONS；服务 `_guard/_authenticate` 已将会话登记归一为 CONSOLE_KIND，删除/预览 handler 要求该权限，只有服务 token、Host controlToken 或 Worker credential 不授权删除。不给 CLI parser、命令别名、共享 skill 的 Host/Worker 工具集加删除入口；不能只靠“不显示按钮”保护操作。

修改 `protocol/contracts.py:BuddyControl`、transport/client 的实际命名映射、service 方法和 `tests/python/protocol/contract_signatures.json`；软件与 CONTRACT_VERSION 同进 0.46.0，schema 25，更新 pyproject 与锁文件中本包版本。与批次二其他改动共一次升级；前端在批次三。本次计划不改版本或任何代码。

## 9. 并发与停止证据

只与 `storage.apply` 共用 `storage.locked` 的 `<state>/storage/.lock`，整次删除持锁，忙即 STORAGE_BUSY；每个 allocation 取它自己的锁，不把 accept、reclaim、cleanup_apply 纳入全局存储锁。顺序为 storage 锁 → 当前 allocation 锁 → 短数据库事务；不在 SQLite 事务里等文件锁、跑 Git 或调 harness。名下的排队、运行、待处理与未确认停止先按资格拒绝；首次标记与提交/继续/重试/回收在同一个 SQLite 写入顺序里决定谁先，标记后这些入口统一拒绝，不修改 `_cleanup_fence`、`_held_writer`、`submission_preparation` 去读另一套 pending 围栏。

当前 `_reclaim_checkout` 只是 cleanup_plan/cleanup_apply 的删除编排，并不重新准备检出；会补写文件的是 `workspace.cleanup_inspect(resume=True)` 调用 `_cleanup_restore`，它恢复缺失 gitfile/已保留树的叶子，再走普通回收证明。删除自己的恢复只在持 allocation 锁下复用其身份证明，不为删目录先恢复用户文件；删除中状态禁止另一路回收准入，先已进入 applying 的回收使首次删除拒绝。整合必须先把任务了结，delivered 尚不可删除；普通接纳/最终提交仍受原 revision 与数据库状态约束，不加全局锁或每层重复资格检查。

Host 审后的删减（2026-10-17）：已 finished 而 `shutdown_confirmed=0` 的执行，当前的中断恢复不再判定（`tasks/interruption.py` 的 `recover` 与 `_recover_one`）。当日日常看板只读计数为 0 个，本批不改中断恢复。删除资格照旧拒绝这类执行（reasonCode stop-unconfirmed）；恢复的缺口记入待办，碰到时再修。

## 10. 确认框数据

preview 在一个读快照建立第 1 节完整分组：microTaskCount 为 R 的 distinct run_id 数，包括协助与 Lead 名下全部微任务，排除内部 Router 执行。unconcludedFailureCount 为 R 中失败/取消终态、无 accepted_at 且无 `workflow_host_conclusions` 的数；已有 concluded 不算无结论。依据 `macro_tasks._summary` 的状态来源，但不复用它只统计根的 failed/roots 口径，也不取时间轴的截断 totals。

预览回复 `{macroTaskId,microTaskCount,unconcludedFailureCount,blockerTotal,blockers,backupRetention:"until-backup-rotation",deletionState}`；确认写不可恢复、这两个数及备份副本待轮换才消失。第一次标记事务重算 microTaskCount，不同即 REVISION_CONFLICT；不再比较摘要 token。`backup.py:create/_backup_paths/recover` 当前是上次升级前的单代 backups/current，删除不改备份、不许诺按日到期、不在此操作 VACUUM、清 WAL 或 Git gc 宣称物理擦除。

## 11. 测试与 Host 演练

| 测试 | 验证内容 |
| --- | --- |
| 删除集合与外键 | 第 1 节每张命中表造有实际 FK 的记录；多代次、多层协助、Lead/run: 分组、修正、超过列表上限的完整集合；换过 Router 的决定收齐全部 dispatch/task/claim/attempt 并整体删除，foreign_key_check 通过；别的任务不变 |
| 标记、资格与恢复 | queued/routing/running/awaiting-host/waiting-owned/helpers/delivered/open 请求/待续接/未确认停止拒绝；failed/cancelled 无结论可删；计数增长拒绝；标记后提交、继续、重试、回收拒绝；两个删除只建一次标记/通知；每个物理阶段、标记提交后、最后事务提交前后中断，重试均可收敛 |
| 内容与收据 | Router 事实与事件确随删，其他决定不删；所有命中 kind 的 commands 身份留存、内容清空、deletion_id 明确、重放只有 commandId/deleted；不同输入仍 CONFLICT、无权 subject 拒绝；不新增事实转换与提交墓碑 |
| 文件与 refs | `<state>/attempts/<taskId>/` 全删且其他任务证据不动；Router frozen-input、旧 adapter 根、controls/submissions、已回收检出留下的封存/记录目录一起处理；超过 64 个 refs 不截断；缺失成功、换 OID/换锁/链接/用户检出/未知归属保留；别处一行坏 JSON 不阻塞共享判断 |
| 停止与并发 | finished 且未确认停止的执行被拒绝（不放行）；旧 applying 回收、`_cleanup_restore` 与删除的 allocation 锁不冲突；准入事务先赢/标记先赢，检查原子性 |
| harness | DSH 记录与索引同删、ZCode DB/storage/旧根同删、Claude 无持久会话；Codex thread/delete 成功、重复删除的专门消息、非法 ID 同码不同消息、其他错误保留 deleting、跨回合去重/创建收据绑定；离线协议夹具不读真实 home、不调模型；tests/probes 的原生无模型探针按需另跑 |
| 升级、HTTP、契约 | 24 → 25 对新建结构一致/保全/失败回滚；contract_signatures 0.46.0；Origin/CSRF/会话撤销与 Host/Worker/服务 token 拒绝；确认数、删除未完成、重复操作、409/410 的封套；最终通知驱动列表/详情刷新 |

实施者按 AGENTS 用 `uv run --frozen python -m hey_my_buddy.cli.checks <受影响测试>`，Host 整合后跑完整 Python 检查。前端批次三另跑 `npm --prefix apps/console test` 与 `npm --prefix apps/console run test:browser`，验证确认框、阻塞摘要、删除未完成/重试、分组计数、列表/详情缓存更新；本回合不运行前端命令或原生探针。Windows 未验证的边界继续保留。

Host 的日常规模私有副本演练先做重绑定：借鉴 `load_fixture` 对私有路径、Git 身份与依赖哈希的处理，并针对真实副本补 allocation 身份与资源重建，不能只替换路径。`workspace._workspace_directory` 的编号是 state_dir 绝对路径与 request_id 的摘要，checkoutRoot、Git worktree 锁及固定清单又绑定这个编号；只改路径会使每个检出都无法证明归属。演练前记录“总受管检出数/副本上可证明归属数”，不达预期先修副本的重绑定，不拿 owner-unproven 的跳过当删除演练成功。所有 Git refs、文件与原生会话都指向私有副本/隔离测试 home，不能让副本操作日常原生会话库。

演练前盘点每个宏任务能否删除及阻塞原因，数 finished 且 shutdown_confirmed=0 的执行、换过 Router 的决定，以及 `<state>/attempts/` 的文件数/字节；如有未确认停止，记下数量与受影响的宏任务（它们照资格删不掉，见第 9 节）。挑小、中、大且含大量事件/已回收封存的宏任务，量资格与标记事务、原生会话、检出、refs、allocation/封存、私有证据、最后删除事务及重试各阶段耗时；记录逐表删除前后行数、删掉的文件/字节/refs 数，做 foreign_key_check 与 integrity_check。不量峰值内存或别的任务读写延迟。

删后以 R/T/A/D/会话编号作一次全库文本列、状态目录文件名与内容、`refs/hey-my-buddy/workspaces/` 的残留扫描；逐项区分应删内容、允许保留的命令身份/删除记录/路径编号、备份、归属不明项及 Codex 诊断日志，不把“没有扫描到”当作物理擦除承诺。记录每阶段中断后重试的最终状态与残留清单。Host 将实测耗时、计数、扫描解释和未动日常状态的证据写 `docs/acceptance/`；本计划不填虚构测量数。

## 12. 实施微任务

| 微任务 | 写入范围 | 检查与依赖 |
| --- | --- | --- |
| 1：升级与存储形式 | `blackboard/store/{db,upgrade}.py`、`install/upgrade.py`、`tasks/command_receipts.py`、`store/store.py` 及对应 store/install/收据测试；24 样板不改成新基线 | 删除记录与 commands.deletion_id；显式 24 → 25、新建结构对比、保全/回滚/重放；本批其他存储改动以后扩展同一 pending upgrade |
| 2：Router 归属的根因修复 | `tasks/{workflow,micro_task,storage}.py` 及对应测试 | `workflow._routing_tasks`、`micro_task.RELATION_SQL` 的 dr 联接与 owned_executions、`storage.inspect` 的 native_owners 都按 `router_dispatches` 收齐换过 Router 的决定的全部内部任务；不改存储形式，可与 1 并行，先于 3 合入 |
| 3：删除引擎 | 新 `tasks/macro_task_deletion.py`，`tasks/{macro_tasks,workflow,storage,workspace}.py`，必要 `private_dirs.py`、CLI 文件定位公共函数及对应测试 | 集合、资格、标记、物理阶段、最后事务；精确共享身份；`storage.inspect` 补列没有检出的 allocation 记录目录；依赖 1 与 2 |
| 4：harness 会话 | `buddy/harnesses/codex/`、必要共享 harness 接口、`tasks/worker_sessions.py` 的创建收据判定、对应 harness 测试与 `tests/probes/` 无模型探针 | thread/delete 与仅专门消息算不存在；DSH/ZCode 私有根、Claude 无持久会话；归属沿创建收据，黑板 DB 连接不传入 buddy 层；依赖 1，可与 2、3 并行，公共接缝与写入文件先定归属 |
| 5：控制台后端与契约 | `console/{server,reads,console_sessions}.py`、`blackboard/service/service.py`、protocol 实际命名映射/contract、签名夹具、版本文件及对应 console/protocol 测试 | 1–4 合并验证后最后接通；仅控制台权限，0.46.0；前端入口/确认/未完成显示在批次三 |

Host 负责跨交付整合、完整检查、另一模型复审、第 11 节日常规模演练与安装决定。前端批次三的 `apps/console/`、 owning reference/skill 与 `docs/acceptance/` 分配在其自身任务中，不扩大本次写入范围。本回合只改本文，不重启服务、不写日常看板、不联网、不调用模型、不操作 stash/分支/tag。

## 13. 已核实事项与待开发者定的事项

Codex 原生 thread/delete 及不存在消息按第 5 节 Host 的 Codex CLI 0.162.1 无模型实测记录采用，已经核实；本仓库调用尚待实现，本回合未独立重跑。L7 之前的事实随删、两次事务之间删物理内容，均由开发者 2026-10-17 定；不再列作待定。没有新增待开发者定的产品事项。

代码核对的一处位置差别是 Router frozen-input 实际位于 harness 私有 attempt 根，见第 4 节；finished 未确认停止的恢复缺口与无宏任务 Lead 的分组，Host 审后移到待办（见第 1、9 节）。其余本轮列明的源码事实核对相符。计划交 Host 审，尚未实施或验收。
