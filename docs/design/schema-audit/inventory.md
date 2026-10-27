# schema 26 存储冗余清点

本清点针对提交 `908384a54a3555de14287bf15faada1f4856bcfa` 的 schema 26，仅阅读产品源码，不读取日常看板、不调用模型或真实 harness。判据为 AGENTS.md 的存储规则和 ADR-028 第 7 条的 2026-10-20 修订；本文不是升级实现计划。

验证：按项目检查运行器执行 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/cli/test_repository_hygiene.py`，5 项测试通过、0 跳过，退出码 0，私有测试根已清除。运行前清除继承的运行时/agent 凭据/虚拟环境变量，并设置 `UV_OFFLINE=1`；新文档另做未跟踪文件的个人路径检查，因为规定的 hygiene 用例只枚举 Git 已跟踪文件。未运行产品模型、真实 harness、日常服务或日常看板操作。

核对方法：按 `db.py` 的显式 CREATE TABLE 建立 57 表、571 列的清单，以仓库 `src/hey_my_buddy` 为产品 Python 范围，并追到 `apps/console/src` 的非测试消费者；测试、fixture 及 probes 不算产品读写者。先按表名/列名检索 SQL，再检查 SELECT * 之后的 row 取值、序列化、调用可到达性与动态 SQL 包装。整体 API 返回是实际读者；仅存储回收的字符串/JSON 泛扫单列记为引用保护，不充当领域语义消费。下文短路径均相对 `src/hey_my_buddy/blackboard`，`protocol/`、`buddy/` 则相对包根；各节给出缩写的完整文件与函数。负向结论只针对当前产品代码，未知对象键域与历史存量差异逐项标明，不从测试人工构造反推产品支持。

<!-- BEGIN workflow_requests -->
## 1. workflow_requests

以下 W 表示 src/hey_my_buddy/blackboard/tasks/workflow.py 的 WorkflowCoordinator 方法，M 表示同目录 macro_tasks.py，D 表示 macro_task_deletion.py；读者只列实际字段消费，storage.py:_workspace_reference_rows 的 SELECT * 递归保留扫描另记于段末。

列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据
--- | --- | --- | --- | ---
request_id | W:_propagate_boundary/_record_request/_record_system_request | W:_request_row/_request_view/_attention_chain，M:_run_spans，D:_delete_rows | 键约束（PRIMARY KEY） | 决策、代理与删除均按身份关联。
run_id | W:_propagate_boundary/_record_request/_record_system_request | W:_request_row/compact/_audit，M:_run_spans，D:_delete_rows | 键约束（workflow_runs FK） | 归属与查询谓词。
turn_id | W:_propagate_boundary/_record_request/_record_system_request | W:_request_view，D:_collect | 冻结证据（请求发生时的 turn） | 系统请求可为空；不能拿当前 turn 回填。
attempt_id | W:_propagate_boundary/_record_request/_record_system_request | W:_request_view/child_settled，D:_collect | 冻结证据（请求发生时的 attempt） | 代理沿用源请求身份。
child_task_id | W:_propagate_boundary | W:_request_view/_attention_chain，D:_collect | 在用 | 区分 helper 代理来源。
kind | W:_propagate_boundary/_record_request/_record_system_request | W:_request_view，M:_run_spans | 在用 | 区分 assistance/attention/代理报告。
summary | W:_propagate_boundary/_record_request | W:_request_view，M:_run_spans | 重复/可推出（仅 Worker 请求） | _record_request 同时写 payload.summary；从 payload_json 的 summary 可取；系统 reason 请求为空，代理复制源值。需在升级中核对存量不同值，避免改变 timeline 显示。
reason_code | W:_propagate_boundary/_record_system_request | tasks/request_reasons.py:request_reason，M:_run_spans | 在用 | 结构化原因与 summary 互斥。
reason_params_json | W:_propagate_boundary/_record_system_request | tasks/request_reasons.py:request_reason，W:workspace_resolve，M:_run_spans | 在用 | 结构化参数且可含 conflictIds。
decision_id | W:_propagate_boundary/_record_system_request | W:_request_view，M:_run_spans，D:_collect | 键约束（evaluation_decisions FK） | 路由边界冻结所指决策。
payload_json | W:_propagate_boundary/_record_request/_record_system_request | W:_request_view/_attention_chain/_propagate_boundary | 在用 | 顶层键见下。
state | W:_accept_record/_cancel_owned_run/_close_proxy_ancestors/_propagate_boundary/_record_request/_record_system_request/_resolve_helper_attention/continue_run/decide_normalized/workspace_resolve | W:_request_view/compact，M:_run_spans | 在用 | 请求状态机及 open 谓词。
decision_json | W:_close_proxy_ancestors/_resolve_helper_attention/decide_normalized/workspace_resolve | W:_request_view/_turn_context | 冻结证据（Host 决策时的答复、note、actor） | 之后 turn 引用当时决定；不能由当前状态还原。
decision_command_id | W:_close_proxy_ancestors/_resolve_helper_attention/decide_normalized | W:_request_view（audit），D:_delete_rows | 在用 | 命令与被删记录关联。
expected_revision | W:_propagate_boundary/_record_request/_record_system_request | W:decide_normalized/_request_view | 在用 | 决策防陈旧边界。
created_at | W:_propagate_boundary/_record_request/_record_system_request | W:compact/_request_view/_audit，M:_run_spans | 冻结证据（请求开启时刻） | 排序与 Host 等待跨度。
updated_at | W:_accept_record/_cancel_owned_run/_close_proxy_ancestors/_propagate_boundary/_record_request/_record_system_request/_resolve_helper_attention/continue_run/decide_normalized/workspace_resolve | M:_run_spans | 在用 | cancelled/superseded 等无 decided_at 的关闭时刻。
decided_at | W:_close_proxy_ancestors/_resolve_helper_attention/decide_normalized/workspace_resolve | W:_request_view/_turn_context，M:_run_spans | 冻结证据（Host 决定时刻） | 和 updated_at 的关闭语义不同。

payload_json 的允许顶层键是 Worker 请求六键、系统请求 source/helperOutcomes/preparationError、代理新增 origin/proxy 的联合；系统请求也可为 {}，构造点均列在下表。

键 | 写入者（产品函数） | 读者（产品函数） | 判断
--- | --- | --- | ---
payload_json.summary | workflow.py:_record_request | workflow.py:_propagate_boundary；_request_view 的 audit 整体读取 | summary 与列同次重复
payload_json.attempted | workflow.py:_record_request | workflow.py:_request_view/_propagate_boundary；audit 整体读取 | Worker 请求原话/任务，保留
payload_json.neededWork | workflow.py:_record_request | workflow.py:_request_view/_propagate_boundary；audit 整体读取 | Worker 请求原话/任务，保留
payload_json.expectedArtifacts | workflow.py:_record_request | workflow.py:_request_view/_propagate_boundary；audit 整体读取 | Worker 请求原话/任务，保留
payload_json.acceptance | workflow.py:_record_request | workflow.py:_request_view/_propagate_boundary；audit 整体读取 | Worker 请求原话/任务，保留
payload_json.suggestedBuddyId | workflow.py:_record_request | workflow.py:_request_view/_propagate_boundary；audit 整体读取 | Worker 请求原话/任务，保留
payload_json.source | workflow.py:_routing_attention | workflow.py:_request_view/_routing_view/decide_normalized；audit 整体读取 | 系统/代理请求按来源可选
payload_json.helperOutcomes | workflow.py:_record_helper_report | workflow.py:_request_view；audit 整体读取 | 系统/代理请求按来源可选
payload_json.preparationError | workflow.py:prepare_continuation_workspace | workflow.py:_request_view；audit 整体读取 | 系统/代理请求按来源可选
payload_json.origin | workflow.py:_propagate_boundary | workflow.py:_request_view；audit 整体读取 | 系统/代理请求按来源可选
payload_json.proxy | workflow.py:_propagate_boundary | workflow.py:_request_view/_attention_chain；audit 整体读取 | 系统/代理请求按来源可选
decision_json.decision | workflow.py:decide_normalized/_resolve_helper_attention | workflow.py:_turn_context/_request_view | Host 决定时冻结
decision_json.note | workflow.py:decide_normalized/_resolve_helper_attention | workflow.py:_turn_context/_request_view | Host 决定时冻结
decision_json.actor | workflow.py:decide_normalized/_resolve_helper_attention | workflow.py:_request_view（audit） | Host 决定时冻结
decision_json.helpers | workflow.py:decide_normalized | workflow.py:_request_view（audit） | Host 决定时冻结
decision_json.parentRunId | workflow.py:_resolve_helper_attention | workflow.py:_request_view（audit） | Host 决定时冻结；helper 专属键可选
decision_json.targetRunId | workflow.py:_resolve_helper_attention | workflow.py:_request_view（audit） | Host 决定时冻结；helper 专属键可选
reason_params_json 的当前请求写入键域按 protocol/reason-codes.json 注册并核对所有 _record_system_request 调用：routing-unusable-selection、helpers-settled 和自动续接错误码用 {}，其余仅下列六键；代理原样复制源请求。decision_id 路由请求以 evaluation_decisions 的 reason 代替本列。

键 | 写入者（产品函数） | 读者（产品函数） | 判断
--- | --- | --- | ---
reason_params_json.continuationId | workflow.py:prepare_continuation_workspace → protocol/reasons.py:reason | tasks/request_reasons.py:request_reason；macro_tasks.py:_run_spans；workflow.py:_request_view | 按 reason_code 使用
reason_params_json.disposition | workflow.py:_record_lead_attention → protocol/reasons.py:reason | tasks/request_reasons.py:request_reason；macro_tasks.py:_run_spans；workflow.py:_request_view | 按 reason_code 使用
reason_params_json.unfinished | workflow.py:_record_lead_attention → protocol/reasons.py:reason | tasks/request_reasons.py:request_reason；macro_tasks.py:_run_spans；workflow.py:_request_view | 冻结当时未完成 owned 列表
reason_params_json.total | workflow.py:_record_lead_attention → protocol/reasons.py:reason | tasks/request_reasons.py:request_reason；macro_tasks.py:_run_spans；workflow.py:_request_view | 按 reason_code 使用
reason_params_json.conflictIds | workflow.py:_record_scope_attention → protocol/reasons.py:reason | tasks/request_reasons.py:request_reason；macro_tasks.py:_run_spans；workflow.py:_request_view / workspace_resolve | 按 reason_code 使用
reason_params_json.paths | workflow.py:_record_scope_attention → protocol/reasons.py:reason | tasks/request_reasons.py:request_reason；macro_tasks.py:_run_spans；workflow.py:_request_view | 按 reason_code 使用

macro_task_deletion.py:_delete_rows 按 run_id 删除本表，未见本表删除后遗留；删除后的跨表事件另见 macro_tasks。

本表是一 run 多个 Host 请求及代理链的状态记录，不能并入 workflow_runs 的单一 active_request_id；该指针只标当前边界。索引 workflow_requests_run_idx 服务 run/state 开放请求查询。
<!-- END workflow_requests -->

<!-- BEGIN macro_task_deletions -->
## 2. `macro_task_deletions`

产品路径：`tasks/macro_task_deletion.py::delete/_record/_delete_rows` 建立、续跑并标记删除；`tasks/macro_task_deletion_guard.py::reject_macro_task/reject_deleting_run` 和 `tasks/session_content.py::identity/deleting` 读取。ADR-028 第 7 条 2026-10-20 修订已定删除完成事务应移除墓碑和被删任务命令，随 26 → 27 实现；这里记录现状，不重新提议。

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `deletion_id` | `tasks/macro_task_deletion.py::delete` | 同文件 `_delete_rows`；`tasks/macro_task_deletion_guard.py::reject_deleting_run`；`commands` 外键 | 键约束 | 删除阶段的稳定身份与命令残留绑定。 |
| `target_kind` | `tasks/macro_task_deletion.py::delete` | 同文件 `_record/_delete_rows`；`tasks/macro_task_deletion_guard.py::reject_macro_task/reject_deleting_run` | 键约束 | 与 `target_id` 组成唯一目标。 |
| `target_id` | `tasks/macro_task_deletion.py::delete` | 同文件 `_record/_delete_rows`；`tasks/macro_task_deletion_guard.py::reject_macro_task/reject_deleting_run`；`tasks/session_content.py::identity/deleting` | 在用 | 删除期间阻止同目标提交、定位会话目录。 |
| `state` | `tasks/macro_task_deletion.py::delete/_delete_rows` | 同文件 `delete`；`tasks/macro_task_deletion_guard.py::reject_macro_task/reject_deleting_run` | 在用 | 区分未完成与已完成；后者为应清的现存墓碑。 |
| `started_at` | `tasks/macro_task_deletion.py::delete` | 未见产品读 | 无读/写（只写） | `rg -n "started_at|macro_task_deletions" src/hey_my_buddy/blackboard/tasks --glob "*.py"`：本表仅 INSERT，`_summary` 未投影开始时间。 |
| `deleted_at` | `tasks/macro_task_deletion.py::_delete_rows` | 同文件 `_summary`；`tasks/macro_task_deletion_guard.py::reject_macro_task` | 冻结证据删除残留 | 只描述已删除目标；ADR-028 #7 已定清理。 |
| `micro_task_count` | `tasks/macro_task_deletion.py::delete` | 同文件 `_summary/_delete_rows` | 冻结证据 | 删除开始时定格预览数量，完成事件仍复制此数；随墓碑删除。 |

表判断：删除进行中保留以恢复，完成事务删除该行及绑定命令，状态是“已定，随 26 → 27 实现”。`_delete_rows` 当前另写 `events` 中无 `task_id/attempt_id` 的 `macro_task.deleted`，`payload_json` 留 `macroTaskId` 和 `microTaskCount`；它绕过本表清理范围，需按 ADR-028 #7 一并判定为冻结证据删除残留。
<!-- END macro_task_deletions -->

<!-- BEGIN meta -->
## 3. `meta`

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `key` | `store/db.py::initialize/_load_secret` | `store/db.py::_require_current_schema/initialize/_load_secret`；`store/backup.py::_verify/create`；`install/upgrade.py::idle_snapshot/preflight`；`store/store.py::integrity` | 键约束 | 仅 `schema_version`、`created_at`、`capability_secret` 三键。 |
| `value` | `store/db.py::initialize/_load_secret` | 同上，其中版本校验与令牌签名读取；`created_at` 无逐值业务读，按存储三项规则保留 | 在用 | 单值槽。对 `created_at` 的产品业务读未在直接 SQL 搜索中找到；表仍承载版本与密钥。 |

JSON：无。三行按键区分：`schema_version` 由 `db.py::initialize` 写、`db.py::_require_current_schema/initialize`、`store/backup.py::_verify/create`、`install/upgrade.py::idle_snapshot/preflight`、`store.py::integrity` 读；`capability_secret` 由 `db.py::_load_secret` 写读，用于能力令牌；`created_at` 由 `db.py::initialize` 写，直接产品读未见，属冻结的数据库创建事实，不能从当前行重建。检索 `rg -n "SELECT value FROM meta|INSERT INTO meta|created_at" src/hey_my_buddy/blackboard/store src/hey_my_buddy/install --glob '*.py'`：`created_at` 仅 INSERT，备份复制整库不是字段消费。表判断：保留三项单值。
<!-- END meta -->

<!-- BEGIN tasks -->
## 4. `tasks`

缩写：下表的 `store.py` 指 `blackboard/store/store.py`，`workflow.py` 指 `blackboard/tasks/workflow.py`，`decision.py` 指 `blackboard/routing/decision.py`；三者是本表产品写入主入口，`store.py::_task_view/_decorate` 是 SELECT * 后实际逐字段投影，另列专门消费者。`workflow.py::_admit_helper_task`、`decision.py` 的路由任务提交也写初始行。

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `task_id` | `store.py::task_submit`；`workflow.py::_admit_helper_task`；`decision.py` 路由任务提交 | `store.py::_task_row/_task_view/_decorate`；`workflow.py` 多处关联；`macro_task_deletion.py::_collect` | 键约束 | 主键及跨表归属。 |
| `request_id` | 同上 | `store.py::task_submit/_task_row/_task_view`；`workflow.py::authorize_agent_operation` | 键约束 | 唯一提交幂等键；`tasks_request_idx` 复制已有 UNIQUE 索引前缀，候选删除该普通索引，需 `EXPLAIN` 验证规划。 |
| `owner` | 同上 | `store.py::_task_view`；`workflow.py` 授权路径 | 在用 | 所有权不能由提交文本可靠推出。 |
| `spec_json` | 同上 | `store.py::_task_view/task_retry/_admit_in_transaction`；`workflow.py::effective_spec`；`decision.py` 路由 | 在用 | 已验证的冻结任务说明，部分投影从中读取。 |
| `spec_canonical_json` | 同上 | 未见 | 可推出/重复 | `store.py::task_submit` 同次两次 `canonical_json(spec)`；`workflow.py`、`decision.py` 的 INSERT 亦为相同规范值。检索 `rg -n 'spec_canonical_json' src/hey_my_buddy apps/console/src --glob '!**/assets/**'`：除 DDL 与三处 INSERT 无读取。直接用 `spec_json` 保持原义，无版本差异。 |
| `input_fingerprint` | 同上 | `store.py::task_submit/_task_view`；`workflow.py::submit_normalized` | 冻结证据 | 提交时算法与版本固定，历史幂等核验。 |
| `fingerprint_version` | 同上 | `store.py::_task_view`；`workflow.py` 提交核验 | 冻结证据 | 版本声明使旧指纹不随新算法重释。 |
| `adapter` | 同上；`workflow.py::_start_routing/routing_settled/continue_run` | `store.py::_task_view/_admit_in_transaction`；`workflow.py` 分发；`session_content.py::identity` | 在用 | 路由期间可变，不能从初始 `spec_json` 推出当前值。 |
| `required_capabilities` | 同上 | `store.py::_task_view/_admit_in_transaction` | 可推出/重复候选 | 初次由 `spec.requiredCapabilities` 写入，调度按此列筛；要删除需改为从冻结 `spec_json` 提取并验证路由路径的规格更新是否同步。差异核对未完成。 |
| `cwd` | 同上；`workflow.py::attach_micro_task` | `store.py::_task_view/_admit_in_transaction`；`workflow.py` 工作区相关路径 | 在用 | 附着微任务可改当前工作目录，与原始 spec 有差异。 |
| `exclusive_resources` | 同上 | `store.py::_task_view`；认领时 `workflow.py::effective_spec` 间接取规格 | 可推出/重复候选 | 初次由 `spec.exclusiveResources` 复制；需核对有效规格是否能因 scope 改动，不直接宣称可删。 |
| `expected_seconds` | 同上 | `store.py::_task_view`；`tasks/reminders.py::_scan`；`workflow.py` | 在用 | 任务预计时长；与初始 spec 重复但提醒用当前列，是否会重算待查。 |
| `router_budget_seconds` | `decision.py` 路由任务提交 | `store.py::_task_view`；`routing/decision.py` 路由控制 | 在用 | decision 任务专用预算，与普通 expected 秒互斥。 |
| `state` | `store.py::_transition_task`；`workflow.py` 事务状态更新 | `store.py::_task_view/_claim_candidates/task_list`；`workflow.py` 各生命周期路径 | 在用 | 权威任务状态，索引供队列排序。 |
| `queue_reason` | `store.py::_set_queue_reason/execution_progress`；`workflow.py` 多处状态事务 | `store.py::_task_view/_claim_candidates`；`workflow.py` | 在用 | 当前等待原因，与 `state` 不一一对应。 |
| `revision` | `store.py::_transition_task`；`workflow.py` 并发事务 | `store.py::_task_view/_transition_task`；`workflow.py` 乐观并发 | 键约束 | 乐观并发代际。 |
| `selected_attempt_id` | `store.py::_admit_in_transaction/task_retry` | `store.py::_selected_attempt/_task_view`；`workflow.py` | 在用 | 历史可选 attempt，重试与 active 分离。 |
| `active_attempt_id` | `store.py::_admit_in_transaction/_commit_execution_result`；`workflow.py::_requeue` | `store.py::_selected_attempt/_task_view`；`workflow.py` | 在用 | 当前有效 attempt 的直接指针，不能仅取最大 generation：不确定关闭及重试语义。 |
| `created_at` | 三处提交入口 | `store.py::_task_view/_claim_candidates/task_list`；`workflow.py` | 在用 | 队列/分页稳定顺序。 |
| `updated_at` | `store.py::_transition_task`；`workflow.py` 状态事务 | `store.py::_task_view`；`workflow.py` 视图/排序 | 在用 | 最近状态时间。 |
| `accepted_at` | `store.py::task_acknowledge`；`workflow.py::_accept_record` | `store.py::_task_view`；`workflow.py::_accept_conflicts_with_recorded` | 冻结证据 | 验收发生时刻。 |
| `acceptance_note` | 同上 | `store.py::_task_view`；`workflow.py::_accept_conflicts_with_recorded` | 冻结证据 | Host 原文不可从结构化状态恢复。 |
| `acceptance_verdict` | 同上，固定 `accepted` | `store.py::_task_view`；`workflow.py::_accept_conflicts_with_recorded` | 可推出/重复候选 | CHECK 仅允许 `accepted`，可由 `accepted_at IS NOT NULL` 得出；同事务设置并供冲突比较。需校验历史存量有无单列异态。 |
| `review_rejected_at` | `store.py::task_acknowledge`；`workflow.py::continue_run/_requeue` | `store.py::_task_view`；`workflow.py` | 冻结证据 | 最近审查打回时刻，重排时可清空。 |
| `review_rejection_note` | 同上 | `store.py::_task_view`；`workflow.py` | 冻结证据 | 打回说明原文。 |
| `queue_position` | 未见 | 未见 | 无读/写 | 检索 `rg -n 'queue_position' src/hey_my_buddy apps/console/src --glob '!**/assets/**'` 仅 `store/db.py` DDL。 |

JSON 顶层：`spec_json` 的普通路径由 `protocol/schemas.py::normalize_spec` 写入，微任务与 helper 路径由同文件 `normalize_workflow_spec` 写入，Router 专用路径由 `blackboard/routing/decision.py::_create_task` 构造；`store/store.py::task_submit`、`tasks/workflow.py::_admit_helper_task` 分别持久化。以下“读”仅指键的实际使用；`store/store.py::_task_view`、`tasks/workflow.py::effective_spec` 还会整体解码和投影。`requestId` 与 `owner` 虽属于提交参数，却只入独立列，不属于规范化 `spec_json`。`BUDDY_FIELDS` 是固定四元组，并不使键域开放。

| JSON 列及顶层键 | 写入者文件与函数 | 读者文件与函数 | 判断 |
| --- | --- | --- | --- |
| `spec_json.task` | `protocol/schemas.py::normalize_spec`；`routing/decision.py::_create_task` | `store/store.py::_task_view`；`tasks/workflow.py::effective_spec` 经执行规格消费 | 原始任务文字；Router 内部固定文字。 |
| `spec_json.cwd` | 同上 | `store/store.py::_admit_in_transaction` 经有效规格；`tasks/workflow.py::effective_spec` | 微任务执行时可被当前 `tasks.cwd` 覆盖，初始值是冻结输入。 |
| `spec_json.adapter` | `protocol/schemas.py::normalize_spec`；`routing/decision.py::_create_task` | `store/store.py::_admit_in_transaction`；`tasks/workflow.py::effective_spec` | 普通任务与 Router 任务有此键；微任务规范化后移除，当前选择由 `tasks.adapter` 投影。 |
| `spec_json.harness` | `protocol/schemas.py::normalize_workflow_spec` | `tasks/workflow.py::effective_spec`；`store/store.py::_admit_in_transaction` | 仅显式 Buddy 四元组的微任务有此键。 |
| `spec_json.provider` | `protocol/schemas.py::normalize_spec/normalize_workflow_spec` | `store/store.py::_admit_in_transaction` 经有效规格；`tasks/workflow.py::effective_spec` | 可选；显式 Buddy 微任务必有。 |
| `spec_json.model` | 同上 | 同上 | 可选；显式 Buddy 微任务必有。 |
| `spec_json.effort` | 同上 | 同上 | 可选；显式 Buddy 微任务必有。 |
| `spec_json.expectedSeconds` | `protocol/schemas.py::normalize_spec/normalize_workflow_spec` | `store/store.py::_task_view`；`tasks/workflow.py::effective_spec` | 普通任务与微任务必有，另存 `tasks.expected_seconds`，当前差异须查。 |
| `spec_json.requiredCapabilities` | 同上；`routing/decision.py::_create_task` | `store/store.py::_admit_in_transaction` 经有效规格 | 另存同名列；Router 固定包含 decision。 |
| `spec_json.exclusiveResources` | `protocol/schemas.py::normalize_spec/normalize_workflow_spec` | `store/store.py::_admit_in_transaction` 经有效规格 | Router 专用规格没有此键；另存同名列。 |
| `spec_json.argv` | `protocol/schemas.py::normalize_spec` | `buddy/runtime/command.py::CommandAdapter.start` 经执行规格 | 只在 command adapter 的模型免调用检查路径。 |
| `spec_json.routerBudgetSeconds` | `routing/decision.py::_create_task` | `store/store.py::_admit_in_transaction` 经 decision 规格 | Router 专用；另存 `tasks.router_budget_seconds`。 |
| `spec_json.decision` | `routing/decision.py::_create_task` | `routing/decision.py::decision_spec` 与 `store/store.py::task_retry` | Router 专用内层对象，含 `decisionId/kind/routerBudgetSeconds`。 |
| `spec_canonical_json` 上述每个键 | `store/store.py::task_submit`、`tasks/workflow.py::_admit_helper_task`、`routing/decision.py::_create_task` 同 INSERT 从相同 spec 再作 `canonical_json` | 无产品字段读者 | 与 `spec_json` 逐键同值；仅表中已有重复候选结论。 |

这些是当前三个 INSERT 的全部顶层键，条件字段不得被误认作所有行都有。表判断：保留任务权威行；删除 `spec_canonical_json`、`queue_position` 和可能冗余的 `tasks_request_idx` 为候选，其他复制列需核对变更路径。
<!-- END tasks -->

<!-- BEGIN attempts -->
## 5. `attempts`

缩写：`store.py` 指 `blackboard/store/store.py`；`interruption.py` 指 `blackboard/tasks/interruption.py`。本表 INSERT 于 `store.py::_admit_in_transaction`，`store.py::_attempt_view/_decorate` 是 SELECT * 后逐列投影；`interruption.py::recover/_recover_one` 消费执行状态和身份。

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `attempt_id` | `store.py::_admit_in_transaction` | `store.py::_attempt_row/_attempt_view`；`interruption.py::recover`；诸子表外键 | 键约束 | attempt 身份。 |
| `task_id` | 同上 | `store.py::_attempt_view/_require_effective_attempt`；`tasks/workflow.py`；删除路径 | 键约束 | 与 generation 唯一约束及归属。 |
| `generation` | 同上 | `store.py::_attempt_view/_admit_in_transaction`；`interruption.py::_marker_identity` | 键约束 | `(task_id,generation)` 唯一，绑定进程凭据与收据。 |
| `nonce_verifier` | 同上 | `store.py::_verify_attempt_actor`；`routing/decision.py` actor 验证 | 冻结证据 | capability nonce 单向验证值。 |
| `execution_state` | 同上；`store.py::_transition_attempt/reconcile_startup` | `store.py::_attempt_view/_total_active/_require_effective_attempt`；`interruption.py::recover` | 在用 | `attempts_effective_unique` 约束仍可能存活的尝试。 |
| `ownership` | 同上；`store.py::execution_connection/reconcile_startup` | `store.py::_attempt_view/active_work`；`service/execution_scheduler.py` | 在用 | 与阶段不同，表达是否仍由 service 持有。 |
| `runtime_identity` | `store.py::_commit_execution_result` | `store.py::_attempt_view`；`interruption.py::recover` | 冻结证据 | 执行报告的 runtime 绑定；与 OS 进程身份不同。 |
| `adapter` | `store.py::_admit_in_transaction` | `store.py::_attempt_view`；`interruption.py::_directory`；`tasks/worker_sessions.py` | 冻结证据 | 认领时冻结的 harness，任务路由字段后续可能变。 |
| `model_harness` | 同上 | `store.py::_model_active_counts`；`interruption.py::_directory` | 冻结证据 | 模型服务槽三元组。 |
| `model_provider` | 同上 | `store.py::_model_active_counts` | 冻结证据 | 同上。 |
| `model_model` | 同上 | `store.py::_model_active_counts`；索引筛选 | 冻结证据 | 同上，NULL 表示无模型执行。 |
| `log_paths` | 默认 `{}`；`store.py::_commit_execution_result` | `store.py::_attempt_view/_decorate`；`interruption.py::_recover_one` | 在用 | 执行/恢复日志位置。 |
| `result_json` | `store.py::_commit_execution_result` | 同文件 `_attempt_view/_decorate/_total_active/confirm_execution_shutdown`；`tasks/workflow.py::precheck_result` | 冻结证据 | 原始结算载荷是幂等比较与工作流导入依据，补证仅改 `shutdownConfirmed`。 |
| `error` | `store.py::_commit_execution_result` | `store.py::_attempt_view`；`tasks/macro_tasks.py::_routing_spans` | 可推出/重复候选 | 与 `result_json.error` 同事务同值；需核对 `release_execution` 无 result 的行如何表达。 |
| `shutdown_confirmed` | `store.py::_commit_execution_result/release_execution` | `store.py::UNRESOLVED_SQL/_attempt_view/_decorate`；`interruption.py::recover` | 在用 | 可在初始结果后由补证改变，不能由冻结原始收据直接推出。 |
| `cancel_requested_at` | `store.py::task_cancel/record_execution_deadline`；`tasks/workflow.py::_request_attempt_stop` | `store.py::_attempt_view/_commit_execution_result`；`interruption.py::_stop_reason` | 冻结证据 | 停止请求时间。 |
| `stop_initiator` | 同上；`store.py::_commit_execution_result` | `store.py::_attempt_view`；`interruption.py::_stop_reason` | 冻结证据 | 发起者影响终止语义。 |
| `grace_deadline` | `store.py::record_execution_deadline`；`tasks/workflow.py::_request_attempt_stop` | `store.py::_attempt_view`；`interruption.py::_stop_reason` | 在用 | 宽限到期时决定强停原因。 |
| `force_reason` | `store.py::record_execution_deadline` | `store.py::_attempt_view`；`interruption.py::_stop_reason` | 冻结证据 | 明确强停原因优先。 |
| `force_requested_at` | `store.py::record_execution_deadline` | `store.py::_attempt_view` | 冻结证据 | 强停请求时间。 |
| `identity_confirmed_at` | `store.py::execution_progress` | `store.py::release_execution/execution_identity_confirmed`；`interruption.py::_stop_native` | 在用 | 是否已确认原生进程启动；停机恢复不等同于身份表存在。 |
| `cleanup_pending` | `store.py::_admit_in_transaction/complete_execution_cleanup` | `interruption.py::recover` | 在用 | 即使 attempt 已 finished，私有材料清理仍可能未完。 |
| `exit_code` | `store.py::_commit_execution_result` | `store.py::_attempt_view` | 可推出/重复候选 | 与 `result_json.exitCode` 同事务同值，初始空值及 released attempt 需检查。 |
| `signal` | 同上 | 同上 | 可推出/重复候选 | 与 `result_json.signal` 同事务同值。 |
| `started_at` | `store.py::_admit_in_transaction` | `store.py::_attempt_view`；`tasks/macro_tasks.py::_run_spans` | 冻结证据 | 认领起点。 |
| `finished_at` | `store.py::_commit_execution_result/release_execution` | `store.py::_attempt_view`；`tasks/macro_tasks.py::_run_spans` | 冻结证据 | 结算时间。 |
| `created_at` | `store.py::_admit_in_transaction` | `store.py::_attempt_view`；`interruption.py::recover` 顺序 | 可推出/重复候选 | 同 INSERT 的 `started_at` 与 `created_at` 都是 `now`；需检查后续是否改 `started_at` 或历史 stock 异值，当前代码无更新。 |
| `updated_at` | `store.py::_transition_attempt/execution_progress/_commit_execution_result` | `store.py::_attempt_view`；调度/console | 在用 | 最近阶段或心跳时间。 |
| `revision` | `store.py::_transition_attempt/execution_progress/_commit_execution_result` | `store.py::_attempt_view/_transition_attempt` | 键约束 | 乐观并发版本。 |
| `token_usage_json` | `evaluation/native_observations.py::persist` | `store.py::_attempt_view/_decorate`；`tasks/macro_tasks.py::_run_spans`；`tasks/workflow.py::compact` | 冻结证据 | 归一化用量可能与原始 result 不同。 |

JSON 顶层：`result_json` 的外层是 `store/store.py::_commit_execution_result` 固定构造的结算回执；`result` 子对象经 `store/store.py::execution_result` 仅验证为对象，来源为 `buddy/runtime/execution.py` 的执行报告、各 harness 的原生结果、`tasks/interruption.py::_partial_result` 的恢复结果及 `tasks/workflow.py::precheck_result` 的失败补项。它没有统一顶层允许集，下面单独标明此子对象的证据缺口，不把外层错称为开放对象。`_attempt_view` 经 `_public_result_payload` 整体返回结算回执，`_same_result` 作整份幂等比较。

| JSON 列及顶层键 | 写入者文件与函数 | 读者文件与函数 | 判断 |
| --- | --- | --- | --- |
| `result_json.status` | `store/store.py::_commit_execution_result` | 同文件 `_same_result/_attempt_view`；`tasks/workflow.py::precheck_result`；`tasks/macro_tasks.py::_routing_spans` | 结算状态。 |
| `result_json.result` | 同上，内容来自 `execution_result` 入参 | 同文件 `_attempt_view/_decorate`；`tasks/workflow.py::result_payload/precheck_result`；`routing/router_boundary_data.py` | 开放的原生/工作流结果子对象，不能从外层键清单裁剪。 |
| `result_json.error` | 同上 | 同文件 `_same_result/_attempt_view` | 与同事务写的 `attempts.error` 重复候选；released 无结果行例外需核对。 |
| `result_json.exitCode` | 同上 | 同文件 `_same_result/_attempt_view` | 与 `attempts.exit_code` 重复候选。 |
| `result_json.signal` | 同上 | 同文件 `_same_result/_attempt_view` | 与 `attempts.signal` 重复候选。 |
| `result_json.shutdownConfirmed` | 同上；同文件 `confirm_execution_shutdown` 补证 | 同文件 `_commit_execution_result/_same_result`；`tasks/workflow.py::precheck_result` | 与单列同时更新；补证前值另由 shutdown evidence 冻结。 |
| `result_json.logPaths` | `store/store.py::_commit_execution_result` 从请求复制 | 同文件 `_attempt_view/_decorate/_same_result`；`tasks/interruption.py::_recover_one` | 与 `attempts.log_paths` 同事务复制，空值及补证路径需核对。 |
| `result_json.runtimeIdentity` | 同上 | 同文件 `_attempt_view/_same_result`；`tasks/interruption.py::_recover_one` | 与 `attempts.runtime_identity` 同事务复制，可能冻结不同阶段，未据此宣布删除。 |
| `result_json.terminationReason` | 同上 | 同文件 `_attempt_view/_decorate/_same_result`；`tasks/workflow.py::precheck_result` | 原因不能由状态推出。 |
| `result_json.artifacts` | 同上，经 `execution_result` 核验 | 同文件 `_attempt_view/_same_result`；`tasks/workflow.py::precheck_result` | 冻结原始列表；产物表另存核验后的行。 |
| `result_json.completedAt` | 同上 | 同文件 `_attempt_view` 整体透传，未见产品逐键读 | 与 `attempts.finished_at` 同事务时间，重复候选须核对。 |
| `log_paths.stdout` | `protocol/execution.py::launch_execution`；`buddy/runtime/execution.py::run`，经 `store/store.py::_commit_execution_result` | `store/store.py::_attempt_view/_decorate`；`buddy/runtime/command.py::CommandAdapter.start` 对执行中的路径；`tasks/interruption.py::_recover_one` 完整日志映射 | 当前执行路径明写；存储边界只查对象类型。 |
| `log_paths.stderr` | 同上 | 同上 | 当前执行路径明写；不能据此断言历史或外部请求没有其他键。 |
| `token_usage_json.version` | `protocol/usage.py::normalize_token_usage`，`evaluation/native_observations.py::persist` 入库 | `store/store.py::_attempt_view/_decorate`；`tasks/workflow.py::compact` 整体透传，无产品逐键读 | 规范化格式版本。 |
| `token_usage_json.inputTokens` | 同上 | `apps/console/src/task-detail/parse.ts::parseTokenUsage`；`tasks/macro_tasks.py::_run_spans` 整体投影 | 可为未知；原生口径先规范化。 |
| `token_usage_json.cachedInputTokens` | 同上 | `apps/console/src/task-detail/parse.ts::parseTokenUsage` | 与输入总量不同事实。 |
| `token_usage_json.outputTokens` | 同上 | `apps/console/src/task-detail/parse.ts::parseTokenUsage` | 输出计数。 |
| `token_usage_json.inputBasis` | 同上 | `apps/console/src/task-detail/parse.ts::parseTokenUsage` | 固定 `includes-cached`，可由规范版本推出的候选。 |
| `token_usage_json.source` | 同上 | `apps/console/src/task-detail/parse.ts::parseTokenUsage` | 原生来源标签。 |
| `token_usage_json.scope` | 同上 | `apps/console/src/task-detail/parse.ts::parseTokenUsage` | 固定 `attempt`，可由规范版本推出的候选。 |
| `token_usage_json.completeness` | 同上 | `apps/console/src/task-detail/parse.ts::parseTokenUsage` | 原生观察的完整性。 |
| `token_usage_json.nativeRecords` | `protocol/usage.py::normalize_token_usage`，`evaluation/native_observations.py::persist` 入库 | `store/store.py::_attempt_view/_decorate`；`tasks/workflow.py::compact` 整体透传，无产品逐键读 | 原生记录数；当前无逐键消费。 |
| `token_usage_json.coverage` | `protocol/usage.py::normalize_token_usage`，`evaluation/native_observations.py::persist` 入库 | `store/store.py::_attempt_view/_decorate`；`tasks/workflow.py::compact` 整体透传，无产品逐键读 | 条件字段；有确定的 native 覆盖声明才写；当前无逐键消费。 |
| `token_usage_json.reasoningOutputTokens` | `protocol/usage.py::normalize_token_usage`，`evaluation/native_observations.py::persist` 入库 | `store/store.py::_attempt_view/_decorate`；`tasks/workflow.py::compact` 整体透传，无产品逐键读 | 条件字段；不等同于总输出；当前无逐键消费。 |

证据缺口：`result_json.result` 的任意顶层键可穿过 `execution_result`，静态检查不能证明封闭全集；已追到 `buddy/runtime/execution.py`、harness 收集器、Router 与恢复路径，已知在用键包括 `nativeSession/partialOutput/interruptionEvidence/tokenUsage/quotaFailure/workspaceSeal/partialWorkspaceSeal/workspaceManifest/microTaskError/harnessAttempts`，但这不是穷举，不据缺席判无人读。`log_paths` 同样允许外来任意键和值，仅能确认当前内建执行路径明写 `stdout/stderr`；新增键缺规范化与消费者证据。泛用 `tasks/storage.py::_workspace_reference_rows` 扫描 JSON 中的位置以保护存储引用，不代表上述键具有领域语义。表判断：保留 attempt 权威行；`error/exit_code/signal/created_at` 是待验证合并候选。

索引补充：普通 `attempts_task_idx(task_id,generation)` 与表级 UNIQUE(task_id,generation) 自动索引完全相同，可删普通索引；两个 Router 复合 UNIQUE 被 FK 引用不能删。
<!-- END attempts -->

<!-- BEGIN attempt_process_identities -->
## 6. `attempt_process_identities`

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `attempt_id` | `tasks/interruption.py::record_identity` | 同文件 `read_identity`；`store/store.py::_process_identity`；`tasks/macro_task_deletion.py::_delete_rows` | 键约束 | 一次 attempt 恰有至多一份不可变身份。 |
| `boot_id` | `tasks/interruption.py::record_identity` | 同文件 `read_identity` → `recover/_recover_one` | 冻结证据 | 开机轮次证明进程身份。 |
| `root_pid` | 同上 | 同上 | 冻结证据 | 需与开机和开始时间共同解释 PID。 |
| `root_started_at` | 同上 | 同上 | 冻结证据 | 防 PID 复用。 |
| `process_group_id` | 同上 | 同上 | 冻结证据 | 与 `job_name` 二选一；停止孤儿进程。 |
| `job_name` | 同上 | 同上 | 冻结证据 | Windows 作业标识；与进程组二选一。 |

表判断：保留；`read_identity` 返回完整身份给 `protocol/process_identity.py` 的检查与停止操作，不能由裸 PID 推出。删除 attempt 时 `tasks/macro_task_deletion.py::_delete_rows` 先删该行。JSON：无。
<!-- END attempt_process_identities -->

<!-- BEGIN attempt_interruptions -->
## 7. `attempt_interruptions`

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `attempt_id` | `tasks/interruption.py::_reserve` | 同文件 `_partial_result/_recover_one`；`store/store.py::_verify_attempt_actor/_commit_execution_result` | 键约束 | 每 attempt 一份中断封存进度。 |
| `evidence_kind` | `tasks/interruption.py::_reserve` | 同文件 `_recover_one` | 冻结证据 | 恢复时保留原始死亡证据种类。 |
| `observed_at` | `tasks/interruption.py::_reserve` | 未见产品读 | 无读/写（只写） | 在 `src/hey_my_buddy` 与 `apps/console/src` 对 `attempt_interruptions` 的 SQL 检索中，只有 `_reserve` INSERT/UPSERT、`_partial_result` 读 `seal_error`、`_recover_one` 读 `evidence_kind`、`store.py` 读存在/状态；`SELECT *` 未用于此表。 |
| `state` | `tasks/interruption.py::_reserve`；`store/store.py::_commit_execution_result` | `store/store.py::_verify_attempt_actor` | 在用 | `sealing` 阻挡执行 actor；结果提交后 `settled`。 |
| `seal_error` | `tasks/interruption.py::_partial_result`；`store/store.py::_commit_execution_result` 清空 | `tasks/interruption.py::_partial_result` | 在用 | 对相同失败去重，避免重复事件。 |

表判断：保留进行中的封存状态；`observed_at` 是无读的候选，若保留须明确其独立审计用途。无 JSON。
<!-- END attempt_interruptions -->

<!-- BEGIN attempt_shutdown_evidence -->
## 8. `attempt_shutdown_evidence`

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `attempt_id` | `store/store.py::_commit_execution_result/release_execution` | 同文件 `_decorate/_commit_execution_result`；`tasks/interruption.py::_recover_one` | 键约束 | 一次 attempt 最多一份最终停机证明。 |
| `evidence_kind` | 同上 | `store/store.py::_decorate` | 冻结证据 | 单列供 task detail 直接显示。 |
| `detail_json` | 同上 | `store/store.py::_decorate/_commit_execution_result`；`tasks/interruption.py::_recover_one` | 冻结证据 | 停机证明原文及补证重放判断。 |
| `observed_at` | 同上 | `store/store.py::_decorate` | 冻结证据 | 停机证明记录时间。 |

JSON 顶层：`kind` 由 `_commit_execution_result/release_execution` 写入、`_decorate` 通过 `evidence_kind` 表示，同时完整 `detail` 透传；为重复候选，但冻结证据中的原文不能改写。`receiptShutdownConfirmed` 由 `_commit_execution_result` 写、同函数在幂等补证读取；`terminationReason` 由恢复路径写、`_decorate` 兜底读取；`execution`、`native`、`partialWorkspaceSeal`、`workspaceSealError` 来自 `tasks/interruption.py::_recover_one`，完整透传给控制台，需以来源对照判定是否可裁剪。对象也接受跨进程 `stop_evidence/completion_evidence`，属开放证据载荷，不能仅按本地字面键定为无人读。表判断：保留冻结停机证明；删除 attempt 时同步删除。
<!-- END attempt_shutdown_evidence -->

<!-- BEGIN execution_issues -->
## 9. `execution_issues`

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `attempt_id` | `store/store.py::_record_runtime_issue`；`routing/decision.py` 运行错误记录 | `store/store.py::_decorate/clear_execution_issue` | 键约束 | 与 `stage` 共同唯一，归属 attempt。 |
| `stage` | 同上 | `store/store.py::_decorate/clear_execution_issue` | 键约束 | 当前阶段错误可覆盖及清除。 |
| `code` | 同上 | `store/store.py::_decorate` | 在用 | 作为 `selectedAttempt.issues` 投影。 |
| `message` | 同上 | 同上 | 在用 | 用户可见错误原文。 |
| `observed_at` | 同上 | 同上 | 在用 | 投影发生时刻。 |

表判断：保留；一 attempt 的阶段错误是独立多行事实。无 JSON；删除 attempt 时同步删除。
<!-- END execution_issues -->

<!-- BEGIN service_issues -->
## 10. `service_issues`

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `stage` | `store/store.py::_record_runtime_issue` | 同文件 `service_issues/clear_service_issue` | 键约束 | 当前每阶段至多一条。 |
| `code` | 同上 | 同文件 `service_issues` → `service/service.py` 状态投影 | 在用 | 服务错误分类。 |
| `message` | 同上 | 同上 | 在用 | 服务错误说明。 |
| `observed_at` | 同上 | 同上 | 在用 | 最近故障时刻。 |

表判断：保留替换式状态；无 JSON。
<!-- END service_issues -->

<!-- BEGIN messages -->
## 11. `messages`

产品主写入者为 `blackboard/store/store.py::_message_post/_message_update`，`_message_view` 是 SELECT * 后逐列响应；`blackboard/tasks/inquiry.py::_apply_inquiry_record` 提供 bridge/journal 证据，`tasks/macro_tasks.py::_run_spans` 读时间轴询问标记。

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `message_id` | `store.py::_message_post` | 同文件 `_message_view/message_get/_message_update`；`workflow.py::authorize_agent_operation` | 键约束 | 主键。 |
| `task_id` | 同上 | `store.py::message_list/_decorate`；`tasks/macro_tasks.py::_run_spans` | 键约束 | `(task_id,inquiry_id)` 唯一与任务归属。 |
| `attempt_id` | 同上 | `store.py::_message_view/_transition_attempt`；`tasks/macro_tasks.py::_run_spans` | 在用 | 问题绑定提出时的 attempt，不能从当前 task attempt 推出。 |
| `inquiry_id` | 同上 | `store.py::_message_post/_message_update/_message_view`；`tasks/inquiry.py::_known_question` | 键约束 | 任务内幂等问答键。 |
| `direction` | 同上，固定 `question` | `store.py::_message_view`；`tasks/macro_tasks.py::_run_spans` | 可推出/重复候选 | 当前产品唯一 INSERT 恒 `question`，时间轴以此筛；迁移需检查 fixture 是否有其他方向。 |
| `author` | 同上，固定 `cli` | `store.py::_message_view`；`tasks/macro_tasks.py::_run_spans` | 冻结证据 | 显示原始询问者；当前提交入口固定，但历史 stock 可能不同。 |
| `recipient` | 同上，固定 NULL | `store.py::_message_view` | 无读/写（固定写入、透传） | `rg -n 'recipient' src/hey_my_buddy/blackboard apps/console/src --glob '!**/*.test.*'` 除 DDL、`_message_post` 写 NULL、`_message_view` 透传外无行为消费者。 |
| `correlation_id` | 同上，复制 `inquiry_id` | `store.py::_message_view` | 可推出/重复 | `correlation_id = inquiry_id` 同 INSERT，后续无 UPDATE；由 `inquiry_id` 原样给 API，没版本差异。检索该列仅 DDL、INSERT、投影。 |
| `body` | `store.py::_message_post` | `store.py::_message_view`；问答 UI | 冻结证据 | 问题原文。 |
| `body_bytes` | 同上 | `store.py::_message_view`；问答 UI | 可推出/重复 | 算法 `len(body.encode('utf-8'))`；同函数计算并写入，无后续修改；不依赖版本。 |
| `payload_hash` | 同上 | `store.py::_message_post/_message_view` | 可推出/重复候选 | 算法 `sha256_text(body)`，幂等重复比较；同函数写入，当前算法固定。若保留旧版本写入的不同规范/散列，需 fixture 检查，故先候选。 |
| `state` | `store.py::_message_post/_message_update/_transition_attempt` | `store.py::_message_view/_decorate`；`tasks/macro_tasks.py::_run_spans` | 在用 | 问答生命周期。 |
| `reason_code` | 同上 | `store.py::_message_view` 经 `reason_from_json` | 在用 | 标准化原因键。 |
| `reason_params_json` | 同上 | `store.py::_message_view` 经 `reason_from_json` | 在用 | 随原因键的参数对象；调用源 `protocol/reasons.py` 可扩展，不能按 `{}` 存量假设。 |
| `delivery_json` | `store.py::_message_update` | 同文件 `_message_view/_message_update`；控制台询问视图 | 在用 | 逐次合并桥接投递事实。 |
| `answer_json` | 同上 | 同上；`tasks/inquiry.py::observe` | 在用 | 首次回答冻结。 |
| `attempts_count` | `store.py::_message_update` | 同文件 `_message_view` | 在用 | 每次更新计数，无法从当前状态还原。 |
| `created_at` | `store.py::_message_post` | 同文件 `_message_view/message_list`；`tasks/macro_tasks.py::_run_spans` | 在用 | 分页和时间轴时刻。 |
| `updated_at` | `store.py::_message_update/_transition_attempt` | `store.py::_message_view` | 在用 | 最近问答状态时刻。 |
| `revision` | 同上 | `store.py::_message_view` | 在用 | 问答变更代际，响应消费者可能依赖。 |

JSON 顶层：`reason_params_json` 由 `store/store.py::_message_post/_message_update/_transition_attempt` 写入，`_message_view` 用 `protocol/reasons.py::reason_from_json` 还原完整参数。当前消息路径的已知原因参数仅 `errorCode`（`tasks/inquiry.py::observe` 的 `bridge-refused`）；其他所列原因写 `{}`。`_message_update` 接受调用者给出的 `reason.params` 且没有参数允许集，故对该列明确缺少排除额外键的边界证据，不能将 `{}` 存量外推为完整键域。`delivery_json` 和 `answer_json` 由 `_message_update` 合并或首次冻结，以下列出当前 `tasks/inquiry.py` 的归一化产物及各键读法。

| JSON 列及顶层键 | 写入者文件与函数 | 读者文件与函数 | 判断 |
| --- | --- | --- | --- |
| `reason_params_json.errorCode` | `tasks/inquiry.py::observe` 经 `store/store.py::_message_update` | `protocol/reasons.py::reason_from_json`；`store/store.py::_message_view`；控制台原因视图 | 仅 `bridge-refused` 条件参数；其他当前原因 `{}`。 |
| `delivery_json.messageId` | `tasks/inquiry.py::_apply_inquiry_record` 经 `store/store.py::_message_update` | `store/store.py::_message_view/_message_update` 整体解码与合并；`apps/console/src` 消息视图 | 桥接投递身份。 |
| `delivery_json.agentStatusAtInject` | 同上 | 同上 | 注入时状态证据。 |
| `delivery_json.injectedAt` | 同上 | 同上 | 注入时刻。 |
| `delivery_json.insertedAt` | 同上 | 同上 | 写入时刻。 |
| `delivery_json.claimedAt` | 同上 | 同上 | 领取时刻。 |
| `delivery_json.deliveredAt` | 同上 | 同上 | 投递时刻。 |
| `delivery_json.observedAt` | 同上 | 同上 | 观察时刻。 |
| `delivery_json.observedAgentStatus` | 同上 | 同上 | 观察到的 agent 状态。 |
| `answer_json.text` | `tasks/inquiry.py::normalize_journal_answer/_apply_inquiry_record` 经 `store/store.py::_message_update` | `store/store.py::_message_view/_message_update`；`tasks/inquiry.py::observe` | 回答原文。 |
| `answer_json.bytes` | 同上 | 同上 | `text` UTF-8 长度，可重算；写入前用于大小限制。 |
| `answer_json.via` | 同上 | 同上 | 原生回答途径。 |
| `answer_json.toolCallId` | 同上 | 同上 | 原生工具调用身份。 |
| `answer_json.at` | 同上 | 同上 | 回答时刻。 |
| `answer_json.truncated` | 同上 | 同上 | 原生截断标记。 |
| `answer_json.source` | 同上 | 同上 | `live-bridge` 或 `bridge-journal` 来源。 |

`delivery_json` 中仅 `deliveredAt` 被 `apps/console/src/task-detail/parse.ts::parseInquiry` 逐键读取；其他七键在已检索的产品代码中无逐键读，仍经 `store/store.py::_message_view/_message_update` 完整透传和合并。`answer_json` 中 `text/at/truncated` 被同一 `parseInquiry` 逐键读取，`bytes` 仅在写入前供 `tasks/inquiry.py::_apply_inquiry_record` 限额，持久化后无逐键读且可由 text 重算；`via/toolCallId/source` 持久化后未见逐键读，但由 `_message_view` 整体透传。`delivery_json` 的八键由 `_apply_inquiry_record` 白名单形成；`answer_json` 的七键由 `normalize_journal_answer` 构造，随后只改 `source`。列经 `_message_view` 整体对外，单个键未在服务 SQL 中取出不能等于无人读。泛用 `tasks/storage.py::_workspace_reference_rows` 仅保护位置引用。表判断：保留问答状态；`correlation_id/body_bytes` 为直接合并候选，`recipient/direction/payload_hash` 需先核对存量与消费者。
<!-- END messages -->

<!-- BEGIN artifacts -->
## 12. `artifacts`

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `artifact_id` | `blackboard/store/store.py::_commit_execution_result` | 同文件 `_artifact_view/artifact_list` | 键约束 | 主键与公开引用。 |
| `task_id` | 同上 | `store.py::_decorate/artifact_list`；`tasks/macro_task_deletion.py::_delete_rows` | 键约束 | 任务归属；即使 attempt 有任务，这列支持无 attempt 产物的模式。 |
| `attempt_id` | 同上 | `store.py::_artifact_view/artifact_list`；`tasks/macro_task_deletion.py::_delete_rows` | 键约束 | `(attempt_id,location,content_hash)` 唯一绑定。 |
| `kind` | 同上 | `store.py::_artifact_view`；控制台产物视图 | 在用 | 产物类型。 |
| `location` | 同上 | `store.py::_artifact_view`；唯一约束 | 冻结证据 | 提交时已验证的位置。 |
| `content_hash` | 同上 | `store.py::_artifact_view`；唯一约束 | 冻结证据 | 文件内容摘要，不可由稍后可能变动的路径推回。 |
| `size_bytes` | 同上 | `store.py::_artifact_view`；控制台 | 冻结证据 | 验证时字节数。 |
| `verified` | 同上，INSERT 固定 `1` | `store.py::_artifact_view` | 可推出/重复候选 | 当前产品仅写通过 `_verify_artifacts` 的产物，故逻辑 `verified=True`；但默认 0 及升级前存量须查 fixture，不可直接改写历史含义。 |
| `created_at` | 同上 | `store.py::_decorate/artifact_list/_artifact_view` | 在用 | 排序与记录时刻。 |

表判断：保留核验后产物的冻结绑定；`verified` 是统一值候选，但只有核对存量无 0 且接口允许取消该标志后才能合并。无 JSON。
<!-- END artifacts -->

<!-- BEGIN events -->
## 13. `events`

产品主写入在 `blackboard/store/store.py::_append_event`，另有各任务/路由/评价模块调用该函数；`events_read/_event_view` 将历史按序向 Host 与控制台开放，`tasks/macro_tasks.py::_markers/_summary`、`tasks/cancellation.py::for_run`、`tasks/workflow.py::_resume_recovery` 和路由状态查询按 kind 消费。

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `seq` | `store.py::_append_event` SQLite AUTOINCREMENT | 同文件 `events_read/_event_view/_head_of`；`tasks/macro_tasks.py::_markers`；`workflow.py::owned_event` | 键约束 | 全局单调事件顺序与订阅游标。 |
| `task_id` | `store.py::_append_event` | 同文件 `events_read/_event_view`；`macro_tasks.py::_markers`；`macro_task_deletion.py::_delete_rows` | 键约束 | 可空：全局事件与任务事件区别；清理依据。 |
| `attempt_id` | 同上 | `store.py::_event_view/_decorate`；`macro_tasks.py::_markers`；删除路径 | 冻结证据 | 事件发生时 attempt 绑定，不应从现时 task 指针推。 |
| `revision` | 同上 | `store.py::_event_view/events_read`；控制台增量 | 冻结证据 | 事件发生时任务版本，不能从最新任务推出。 |
| `kind` | 同上 | `store.py::_event_view`；`macro_tasks.py::_markers`；`workflow.py`/路由各 kind 查询 | 在用 | 事件语义分派；`events_decision_status_idx`/`events_router_claimed_idx` 服务特定查询。 |
| `payload_json` | 同上，按调用点提供 dict | `store.py::_event_view`；`macro_tasks.py::_markers/_summary`；`workflow.py::_resume_recovery`；路由查询 | 冻结证据 | 按 kind 变化的事件发生时内容；不可由当前状态普遍重建。 |
| `actor_role` | `store.py::_append_event` 从 payload.actor 拆出 | `store.py::_event_view`；`macro_tasks.py::_markers`；`tasks/cancellation.py::for_run` | 冻结证据 | 事件发生时行动者身份。 |
| `actor_id` | 同上 | 同上 | 冻结证据 | 与 role 成对，不能从现时 owner 推。 |
| `created_at` | `store.py::_append_event` | `store.py::_event_view`；`macro_tasks.py::_markers` | 冻结证据 | 时间轴原始时刻。 |

JSON 清点范围：产品 `src/hey_my_buddy` 中全部两处 `INSERT INTO events`（`store.py::_append_event` 与 `routing/router_history.py::_append_event`）及其调用包装；`apps/console/src` 已检索，控制台消费 `macro_tasks.py::_markers` 投影的时间线字段，未直接解析原始 `events.payload_json`。下表列出源码可到达的事件种类和每种载荷的全部明确顶层键；`actor` 在 `store.py::_append_event` 写入前拆为 `actor_role/actor_id`，故不列作 JSON 键。可选键同列列出。

读者缩写：`G` = `store.py::events_read/_event_view` 经 `service.py::events_read` 向 Host 完整返回原始 payload，适用于每一行；`M` = `tasks/macro_tasks.py::_markers` 向控制台投影选定 Host 事件。其他注明的是额外按 kind 或按键的产品读者；仅有 `G` 不等于字段可删除，因为 Host 事件合同仍完整开放。

| kind | `payload_json` 明确顶层键 | 产品写入者文件::函数 | 产品读者文件::函数（另有 G） |
| --- | --- | --- | --- |
| `attempt.activity` | `activity` | `store.py::execution_progress` | G（仅完整透传） |
| `attempt.interruption_seal_pending` | `error`, `evidence` | `interruption.py::_partial_result` | G（仅完整透传） |
| `attempt.progress` | `message`, `phase` | `store.py::execution_progress` | G（仅完整透传） |
| `attempt.released` | `previousState`, `reason`, `releaseEvidence` | `store.py::release_execution` | `store.py::_decorate(reason)`；`decision.py::released(releaseEvidence)` |
| `attempt.reminder` | `kind`, `attemptId`, `createdAt`, `elapsedSeconds`, `expectedMultiple`, `lastNativeEventAt`, `reminderId`, `runId`, `toolInProgress` | `reminders.py::_scan` | G（仅完整透传） |
| `attempt.uncertain` | 空对象 | `store.py::reconcile_startup` | G（仅完整透传） |
| `attempt.waiting` | `reason` | `store.py::execution_progress` | G（仅完整透传） |
| `catalog.model_pending` | `harness`, `model`, `pendingSince`, `provider` | `catalog_store.py::record` | G（仅完整透传） |
| `catalog.model_recovered` | `harness`, `model`, `pendingSince`, `provider`, `recoveredAt` | `catalog_store.py::record` | G（仅完整透传） |
| `catalog.model_unavailable` | `confirmedAt`, `harness`, `model`, `pendingSince`, `provider` | `catalog_store.py::record` | G（仅完整透传） |
| `decision.cancelled` | `buddyId`, `decisionId`, `status` | `decision.py::_finish` | `router_boundary_data.py::router_trials(decisionId,kind/seq)`；`macro_task_deletion.py::_delete_rows(decisionId/requestId)` |
| `decision.completed` | `buddyId`, `decisionId`, `status` | `decision.py::_finish` | `router_boundary_data.py::router_trials(decisionId,kind/seq)`；`macro_task_deletion.py::_delete_rows(decisionId/requestId)` |
| `decision.failed` | `buddyId`, `decisionId`, `status` | `decision.py::_finish` | `router_boundary_data.py::router_trials(decisionId,kind/seq)`；`macro_task_deletion.py::_delete_rows(decisionId/requestId)` |
| `decision.needs_host` | `buddyId`, `decisionId`, `status` | `decision.py::_finish` | `router_boundary_data.py::router_trials(decisionId,kind/seq)`；`macro_task_deletion.py::_delete_rows(decisionId/requestId)` |
| `decision.requested` | `budget`, `decisionId`, `kind`, `requestId`, `routerBuddy`, `routerBuddyId`, `routerBuddyIds`, `routerCalled`, `routerIdentities`, `routerIndex`, `routerProblem`, `routerRetryIntervalSeconds`, `routingBudget`, `routingMode`, `status`, `taskId` | `decision.py::_create` | `router_boundary_data.py::_events/router_trials(requestId,decisionId)`；`macro_task_deletion.py::_delete_rows(decisionId/requestId)` |
| `decision.selection_rerouted` | `decisionId` | `decision.py::_retry_selection` | `router_boundary_data.py::router_trials(decisionId,kind/seq)`；`macro_task_deletion.py::_delete_rows(decisionId/requestId)` |
| `decision.started` | `attemptId`, `decisionId`, `generation`, `inputSha256` | `decision.py::_mark_running` | `router_boundary_data.py::_events(decisionId)`；`macro_task_deletion.py::_delete_rows(decisionId/requestId)` |
| `evaluation.catalog_discovered` | `appliedHarnesses`, `discoveryId`, `observationId`, `staleHarnesses` | `catalog_store.py::record` | G（仅完整透传） |
| `evaluation.published` | `counts`, `kind` | `evaluation.py::user_policy_publish` | G（仅完整透传） |
| `evaluation.routing_budget_changed` | `preset` | `user_policy.py::publish` | G（仅完整透传） |
| `execution.admitted` | `adapter`, `generation` | `store.py::_admit_in_transaction` | G（仅完整透传） |
| `execution.connected` | 空对象 | `store.py::execution_connection` | G（仅完整透传） |
| `execution.deadline` | `reason` | `store.py::record_execution_deadline` | G（仅完整透传） |
| `execution.issue` | `code`, `message`, `stage` | `decision.py::_record_publication_issue`、`store.py::_record_runtime_issue` | G（仅完整透传） |
| `execution.issue_resolved` | `stage` | `store.py::clear_execution_issue` | G（仅完整透传） |
| `execution.materials_cleaned` | 空对象 | `store.py::complete_execution_cleanup` | G（仅完整透传） |
| `execution.shutdown_confirmed` | `evidence` | `store.py::_commit_execution_result` | G（仅完整透传） |
| `execution.unknown` | 空对象 | `store.py::execution_connection` | G（仅完整透传） |
| `harness.account_observed` | `billing`, `harness`, `observedAt` | `harness_health.py::_codex_account_read` | `store.py::_append_event(harness,周期替换)` |
| `harness.account_read_failed` | `accountReadFailure`, `currentRevision`, `harness`, `reason`, `revision` | `harness_health.py::_codex_account_read`、`harness_health.py::invalidate` | G（仅完整透传） |
| `harness.account_read_unknown` | `accountReadFailure`, `currentRevision`, `harness`, `reason`, `revision` | `harness_health.py::_codex_account_read` | G（仅完整透传） |
| `harness.checked` | `harness`, `reason`, `revision`, `status`, `version` | `harness_health.py::refresh` | `store.py::_append_event(harness,周期替换)` |
| `harness.execution_selected` | `attemptId`, `harness` | `service.py::harness_prepare/handler` | G（仅完整透传） |
| `harness.invalidated` | `accountReadFailure`, `harness`, `reason` | `harness_health.py::invalidate` | G（仅完整透传） |
| `harness.path_changed` | `harness`, `manualPath` | `harness_health.py::set_path` | G（仅完整透传） |
| `harness.probe_unknown` | `harness`, `reason`, `revision` | `harness_health.py::refresh` | `store.py::_append_event(harness,周期替换)` |
| `harness.quota_redetect` | `cleared`, `duplicate`, `harness`, `provider` | `service.py::quota_redetect/handler` | G（仅完整透传） |
| `harness.start_failed` | `attemptId`, `harness` | `service.py::harness_prepare/handler` | G（仅完整透传） |
| `harness.start_retry` | `attemptId`, `harness` | `service.py::harness_prepare/handler` | G（仅完整透传） |
| `macro_task.deleted` | `macroTaskId`, `microTaskCount` | `macro_task_deletion.py::_delete_rows` | G（仅完整透传） |
| `message.posted` | `inquiryId`, `questionBytes`, `state` | `store.py::_message_post` | G（仅完整透传） |
| `message.updated` | `inquiryId`, `state` | `store.py::_message_update`、`store.py::_transition_attempt` | G（仅完整透传） |
| `reminder.settings_changed` | `revision`, `silenceSeconds` | `service.py::reminder_settings_set/handler` | G（仅完整透传） |
| `router.answered` | `attemptId`, `buddyId`, `code`, `outcome`, `phase`, `plane`, `requestId`, `routerIndex`, `selectionRetry`, `taskId` | `router_history.py::record_outcome` | `router_boundary_data.py::router_trials(requestId,buddyId,routerIndex,taskId,attemptId,outcome,phase,code)`；`macro_task_deletion.py::_delete_rows(decisionId/requestId)` |
| `router.claimed` | `attemptId`, `buddyId`, `generation`, `plane`, `requestId`, `routerIndex`, `taskId` | `router_sequence.py::record_claim` | `router_boundary_data.py::router_trials(requestId,buddyId,routerIndex,taskId,attemptId)`；`macro_task_deletion.py::_delete_rows(decisionId/requestId)` |
| `router.no_answer` | `attemptId`, `buddyId`, `code`, `outcome`, `phase`, `plane`, `requestId`, `routerIndex`, `selectionRetry`, `taskId` | `router_history.py::record_outcome` | `router_boundary_data.py::router_trials(requestId,buddyId,routerIndex,taskId,attemptId,outcome,phase,code)`；`macro_task_deletion.py::_delete_rows(decisionId/requestId)` |
| `service.issue` | `code`, `message`, `stage` | `store.py::_record_runtime_issue` | G（仅完整透传） |
| `service.issue_resolved` | `stage` | `store.py::clear_service_issue` | G（仅完整透传） |
| `task.accepted` | `evidence`, `noteBytes`, `verdict` | `store.py::task_acknowledge` | G（仅完整透传） |
| `task.cancel_requested` | `decisionId`, `note`, `owningRunId` | `store.py::task_cancel`、`workflow.py::_cancel_routes` | G（仅完整透传） |
| `task.cancelled` | `artifacts`, `cancelRaced`, `decisionId`, `note`, `owningRunId`, `phase`, `shutdownConfirmed`, `status`, `taskState` | `decision.py::_cancel_queued_task`、`store.py::_commit_execution_result`、`store.py::task_cancel`、`workflow.py::_cancel_routes` | `workflow.py::owned_event(kind)` |
| `task.completed` | `artifacts`, `cancelRaced`, `shutdownConfirmed`, `status`, `taskState` | `store.py::_commit_execution_result` | `decision.py::_complete(cancelRaced)` |
| `task.failed` | `artifacts`, `cancelRaced`, `shutdownConfirmed`, `status`, `taskState` | `store.py::_commit_execution_result` | G（仅完整透传） |
| `task.queued` | `artifacts`, `cancelRaced`, `shutdownConfirmed`, `status`, `taskState` | `store.py::_commit_execution_result` | G（仅完整透传） |
| `task.reconciliation-needed` | `artifacts`, `cancelRaced`, `shutdownConfirmed`, `status`, `taskState` | `store.py::_commit_execution_result` | G（仅完整透传） |
| `task.rejected` | `evidence`, `noteBytes`, `verdict` | `store.py::task_acknowledge` | G（仅完整透传） |
| `task.retried` | `note`, `previousAttemptId` | `store.py::task_retry` | G（仅完整透传） |
| `task.review_archived` | `acceptedAt`, `artifactId`, `note`, `reviewRejectedAt`, `verdict` | `store.py::task_retry`、`workflow.py::_accept_record`、`workflow.py::_requeue` | G（仅完整透传） |
| `task.submitted` | `adapter`, `cwd`, `decisionId`, `inputFingerprint`, `microTask`, `queueReason`, `requestId` | `decision.py::_create_task`、`store.py::task_submit` | `M, micro_task.py::TASK_HISTORY_SQL(sourceHostId)` |
| `workflow.access_changed` | `access`, `accessNote`, `continuationId`, `previousAccess` | `workflow.py::continue_run` | G（仅完整透传） |
| `workflow.acknowledged` | `artifactId`, `integrationId`, `noteBytes`, `resolutionDelivery`, `verdict` | `workflow.py::_accept_record` | `M`；`workflow.py::_recorded_acceptance_integration(integrationId)` |
| `workflow.auto_continued` | `kind`, `autoContinued`, `continuationId`, `decisionId`, `detail`, `helpers`, `previousDecisionId`, `reason`, `resumeMode`, `trigger`, `turnId` | `workflow.py::_wake_lead`、`workflow.py::child_settled`、`workflow.py::settle_turn` | `workflow.py::_resume_recovery(reason,完整 payload)` |
| `workflow.boundary_updated` | `activeRequestId`, `state` | `workflow.py::_sync_boundary` | `workflow.py::owned_event/_owned_since_input(state)` |
| `workflow.buddy_overridden` | `buddy`, `buddyRevision`, `note`, `source` | `workflow.py::continue_run` | `workflow.py::_routing_view(buddyRevision)` |
| `workflow.buddy_selected` | `buddy`, `buddyRevision`, `source` | `workflow.py::_create_helpers`、`workflow.py::attach_micro_task` | G（仅完整透传） |
| `workflow.buddy_validated` | `buddy`, `buddyRevision` | `workflow.py::prepare_dispatch` | G（仅完整透传） |
| `workflow.cancelled` | `continuationId`, `honestShutdown`, `leadRunId`, `note`, `stopInitiator` | `workflow.py::_cancel_in_transaction` | `M`；`cancellation.py::for_run(note)`；`store.py::_commit_execution_result(stopInitiator)` |
| `workflow.cleanup_applied` | `alreadyRemoved`, `path`, `planId` | `workflow.py::cleanup_apply` | G（仅完整透传） |
| `workflow.cleanup_planned` | `eligible`, `path`, `planId`, `reasons` | `workflow.py::cleanup_plan` | G（仅完整透传） |
| `workflow.continued` | `kind`, `continuationId`, `controllingRunId`, `helperPolicy`, `reroute` | `workflow.py::continue_run` | `M`；`workflow.py::prepare_continuation_workspace(continuationId,reroute)` |
| `workflow.delegation_authority_granted` | `buddy` | `workflow.py::attach_micro_task` | G（仅完整透传） |
| `workflow.fix_submitted` | `fixes` | `workflow.py::attach_micro_task` | G（仅完整透传） |
| `workflow.helper_admitted` | `integrator`, `parentRunId`, `requestId`, `sourceHostId` | `workflow.py::_admit_helper_task` | `micro_task.py::TASK_HISTORY_SQL(sourceHostId)`（由 `macro_tasks.py::_summary` 执行） |
| `workflow.helper_cancelled` | `cancelledByRunId`, `continuationId`, `leadRunId`, `note`, `parentRunId`, `stopInitiator` | `workflow.py::_cancel_children` | `cancellation.py::for_run(note)`；`store.py::_commit_execution_result(stopInitiator)` |
| `workflow.helper_resumed` | `decision`, `dependencyHelpers`, `parentRunId`, `requestId`, `sourceRequestId` | `workflow.py::_resolve_helper_attention` | G（仅完整透传） |
| `workflow.helper_settled` | `disposition`, `parentRunId`, `state` | `workflow.py::child_settled` | G（仅完整透传） |
| `workflow.host_completed` | `artifactId`, `integrationId` | `workflow.py::_accept_record` | `workflow.py::_accept_record(artifactId)` |
| `workflow.host_concluded` | `conclusionId`, `executionStatus`, `partialArtifactId`, `reason` | `workflow.py::_conclude_record` | `M` |
| `workflow.integration_recorded` | `afterCommit`, `artifactId`, `integrationId`, `state`, `strategy` | `workflow.py::_accept_record` | `M` |
| `workflow.late_turn` | `disposition`, `state`, `turnId` | `workflow.py::_late_cancelled_turn` | G（仅完整透传） |
| `workflow.lead_result_refused` | `requestId`, `turnId`, `unfinishedOwnedMicroTaskTotal`, `unfinishedOwnedMicroTasks` | `workflow.py::_record_lead_attention` | G（仅完整透传） |
| `workflow.lead_task_cancelled` | `cancelledByRunId`, `continuationId`, `leadRunId`, `note`, `parentRunId`, `stopInitiator` | `workflow.py::_cancel_children` | `cancellation.py::for_run(note)` |
| `workflow.lead_task_created` | `leadRunId`, `leadTurnId` | `workflow.py::attach_micro_task` | G（仅完整透传） |
| `workflow.macro_task_created` | `description`, `macroTaskId` | `workflow.py::attach_micro_task` | `macro_tasks.py::_summary(macroTaskId,description)`；`macro_task_summary_cache.py::dependencies(macroTaskId,description)` |
| `workflow.native_resume_refused` | `autoContinued`, `code`, `continuationId`, `detail`, `modelStarted`, `reason`, `resumeMode`, `shutdownConfirmed`, `turnId` | `workflow.py::settle_turn` | G（仅完整透传） |
| `workflow.owned_stop_reported` | `reportedTaskId`, `sourceEventSeq`, `stopConfirmed` | `workflow.py::_owned_descendant_reported` | G（仅完整透传） |
| `workflow.partial_output_preserved` | `artifactId`, `final`, `partial`, `verified` | `workflow.py::_conclude_record`、`workflow.py::settle_turn` | G（仅完整透传） |
| `workflow.request_approved` | `autoContinue`, `helpers`, `requestId` | `workflow.py::decide_normalized` | `M` |
| `workflow.request_declined` | `autoContinue`, `note`, `requestId` | `workflow.py::decide_normalized` | `M` |
| `workflow.review_rejected` | `artifactId`, `note`, `opinion`, `verdict` | `workflow.py::continue_run` | `M` |
| `workflow.routing_needs_host` | `decisionId`, `requestId` | `workflow.py::_routing_attention` | G（仅完整透传） |
| `workflow.routing_requested` | `decisionId`, `ownerGeneration`, `routingTaskId` | `workflow.py::_start_routing` | G（仅完整透传） |
| `workflow.routing_resolved` | `buddy`, `buddyId`, `decisionId`, `source` | `workflow.py::routing_settled` | G（仅完整透传） |
| `workflow.scope_amended` | `previousScopeVersion`, `scopeVersion`, `writeScope` | `workflow.py::scope_amend` | G（仅完整透传） |
| `workflow.scope_conflict` | `conflictIds`, `paths`, `requestId` | `workflow.py::_record_scope_attention` | G（仅完整透传） |
| `workflow.submit_race` | `preparedManifestSha256`, `requestId` | `workflow.py::submit_normalized` | G（仅完整透传） |
| `workflow.suggestion` | `bodyBytes`, `suggestionId` | `workflow.py::suggest` | G（仅完整透传） |
| `workflow.takeover` | `newHostId`, `ownerGeneration`, `previousHostId`, `previousOwnerGeneration` | `workflow.py::takeover` | `M`；`micro_task.py::TASK_HISTORY_SQL(previousHostId,previousOwnerGeneration)`（由 `macro_tasks.py::_summary` 执行） |
| `workflow.turn_failed` | `state`, `turnId` | `workflow.py::settle_turn` | G（仅完整透传） |
| `workflow.turn_rejected` | `reason`, `turnId` | `workflow.py::settle_turn` | G（仅完整透传） |
| `workflow.turn_returned` | `disposition`, `requestId`, `state`, `turnId` | `workflow.py::settle_turn` | G（仅完整透传） |
| `workflow.workspace_preparation_failed` | `continuationId`, `errorCode`, `requestId` | `workflow.py::prepare_continuation_workspace` | G（仅完整透传） |
| `workflow.workspace_prepared` | `checkoutId`, `continuationId`, `manifestSha256`, `recoveredAttemptId` | `workflow.py::prepare_continuation_workspace` | G（仅完整透传） |
| `workflow.workspace_reclaimed` | `checkoutId`, `previousReservationId` | `workflow.py::_reclaim_manual_workspace` | G（仅完整透传） |
| `workflow.workspace_resolved` | `action`, `artifactId`, `conflictId`, `delivered`, `remainingPaths`, `resolvedPaths`, `state` | `workflow.py::workspace_resolve` | G（仅完整透传） |

动态来源核对：`decision._finish` 的终态映射固定为 completed、needs-host、failed、cancelled 四种 kind；`decision.requested` 的路由键来自 `routing/router.py::routing_facts` 的固定十键投影；`catalog.model_*` 来自 `catalog_store.py::record` 三种 transition，写前弹出 `kind`；`router.answered/no_answer` 由 `router_history.py::record_outcome` 的固定 payload 加可选 `selectionRetry` 构造；`attempt.reminder` 由 `reminders.py::view` 的固定九键构造；`harness.quota_redetect` 由 `quota_routing.py::redetect` 三键加 `service.py::quota_redetect` 的 `duplicate` 构造；`workflow.workspace_prepared/workspace_preparation_failed` 来自 `workflow.py::prepare_continuation_workspace` 两处分支；`workflow.native_resume_refused/auto_continued` 的 `facts` 有原生恢复拒绝与配额改派两处分支。`task.{task_state}` 的状态分支为 completed、cancelled、queued、reconciliation-needed、failed。取消事件的 `stop_facts` 来源已追至 `continuationId`、`leadRunId` 两种调用。

判定：本次静态源码枚举没有找到一个可断言“schema 26 无产品读写、可直接删除”的 `events.payload_json` 顶层键：每一事件都经 G 完整向 Host 开放，部分键另有表内专用读者。`macro_task.deleted` 的 `macroTaskId` 与 `microTaskCount` 是例外的删除残留：`macro_task_deletion.py::_delete_rows` 在目标行清理后写无 `task_id/attempt_id` 的全局事件，该事件不会随目标删除；ADR-028 #7 已定 26 → 27 应消除该残留。这里是清点结论，不修改封存或升级。

证据边界：静态分析只覆盖当前产品源码与控制台源码；Host 对完整事件载荷的外部实际读取行为不在仓库内，不能由源码证明某个仅 G 的键无人使用。`router_history.py` 独立写入口无 `actor` 拆分，其固定 payload 不含 `actor`。
<!-- END events -->

<!-- BEGIN commands -->
## 14. `commands`

产品主写入与幂等读取在 `blackboard/store/store.py::_store_receipt/_receipt_entry/_receipt_response`；`blackboard/tasks/command_receipts.py::stored_response/replay_response` 将多数 workflow 命令缩成身份键并从当前表重建答复；删除路径在 `tasks/macro_task_deletion.py::_delete_rows` 改写被删目标命令。

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `command_id` | `store.py::_store_receipt` | 同文件 `_receipt_entry/_receipt_response`；`tasks/macro_task_deletion.py::_delete_rows` | 键约束 | 幂等键。 |
| `task_id` | `store.py::_store_receipt`；`macro_task_deletion.py::_delete_rows` 置 NULL | `macro_task_deletion.py::_delete_rows` | 在用 | 当前删除关联定位；完成后命令按 ADR-028 #7 应清。 |
| `attempt_id` | 同上 | 同上 | 在用 | 执行命令删除关联。 |
| `kind` | `store.py::_store_receipt` | 同文件 `_receipt_entry/_receipt_response`；`command_receipts.py::replay_response`；删除路径 | 在用 | 同命令 id 类型冲突核验及答复分派。 |
| `request_hash` | `store.py::_store_receipt` | 同文件 `_receipt_entry` | 冻结证据 | 初始请求摘要，跨重放一致性。 |
| `response_json` | `store.py::_store_receipt`；`macro_task_deletion.py::_delete_rows` 清空 | `store.py::_receipt_response` → `command_receipts.py::replay_response`；删除路径 JSON 归属扫描 | 在用 | 当前按 kind 的身份/少量操作事实；已删除时 `{}` 是残留。 |
| `subject` | `store.py::_store_receipt` | 同文件 `_receipt_entry` | 冻结证据 | 执行 nonce 绑定；删除期间只允许原主体读。 |
| `created_at` | `store.py::_store_receipt` | 未见产品读；`commands_kind_idx` 索引含此列 | 无读/写（只写） | 检索 `rg -n 'FROM commands|JOIN commands|UPDATE commands|DELETE FROM commands|created_at' src/hey_my_buddy/blackboard --glob '*.py'`：唯一 SELECT * 在 `_receipt_entry`，实际只访问 `subject/kind/deletion_id/request_hash/response_json/command_id`；删除路径不取时刻。 |
| `deletion_id` | `macro_task_deletion.py::_delete_rows` | `store.py::_receipt_entry/_receipt_response`；删除路径筛选 | 冻结证据删除残留 | 清空命令指向已完成墓碑，ADR-028 #7 已定随 26 → 27 清理命令与墓碑。 |

JSON 顶层：`store/store.py::_store_receipt` 调用 `tasks/command_receipts.py::stored_response`。对 `_CURRENT_VIEW_KINDS`，保存的是下列身份及少量不可从当前状态还原的操作事实，`replay_response` 逐键读；对其他 kind，`stored_response` 原样保存调用方的回答，`replay_response` 原样返回，没有通用允许集，必须依各调用方逐个判定。

| `response_json` 顶层键 | 写入者文件与函数 | 读者文件与函数 | 判断 |
| --- | --- | --- | --- |
| `runId` | `tasks/command_receipts.py::stored_response` 从各 workflow 回答复制 | 同文件 `replay_response` | `_CURRENT_VIEW_KINDS` 共同身份。 |
| `requestId` | 同上，`workflow.decide` | 同文件 `replay_response`；`tasks/macro_task_deletion.py::_delete_rows` | 决策请求身份及删除归属。 |
| `continuationId` | 同上，`workflow.continue` | 同上 | continuation 身份。 |
| `ownerGeneration` | 同上，`workflow.takeover` | `command_receipts.py::replay_response` | 接管时冻结代际。 |
| `previousOwnerGeneration` | 同上，`workflow.takeover` | 同上 | 接管前代际。 |
| `control` | 同上，`workflow.takeover` | 同上，读取子键 `hostId` | 保存接管时 Host 身份。 |
| `integrationId` | 同上，`workflow.accept` | 同上；`macro_task_deletion.py::_delete_rows` | 验收集成身份。 |
| `resolutionConflictId` | 同上，`workflow.accept` 有冲突处置时 | `command_receipts.py::replay_response` | 条件键，定位处置。 |
| `conclusionId` | 同上，`workflow.conclude` | 同上；`macro_task_deletion.py::_delete_rows` | 结论身份。 |
| `targetRunId` | 同上，scope、workspace、cleanup 命令 | 同上；`macro_task_deletion.py::_delete_rows` | 目标宏任务。 |
| `scopeVersion` | 同上，`workflow.scope_amend` | `command_receipts.py::replay_response` | 冻结范围版本。 |
| `conflictId` | 同上，`workflow.workspace_resolve` | 同上；`macro_task_deletion.py::_delete_rows` | 冲突身份。 |
| `action` | 同上，`workflow.workspace_resolve` | `command_receipts.py::replay_response` | 原操作动作。 |
| `resolutionState` | 同上，`workflow.workspace_resolve` | 同上 | 原操作结果；当前冲突行可能已变。 |
| `resolvedPaths` | 同上，`workflow.workspace_resolve` | 同上 | 原操作路径事实。 |
| `preservedPaths` | 同上，`workflow.workspace_resolve` | 同上 | 原操作路径事实。 |
| `remainingPaths` | 同上，`workflow.workspace_resolve` | 同上 | 原操作路径事实。 |
| `observedFingerprint` | 同上，`workflow.workspace_resolve` | 同上 | 原观察指纹。 |
| `resolvedFingerprint` | 同上，`workflow.workspace_resolve` | 同上 | 原处置指纹。 |
| `artifactId` | 同上，`workflow.workspace_resolve` | 同上；`macro_task_deletion.py::_delete_rows` | 处置产物身份。 |
| `nextBoundaryRequestId` | 同上，`workflow.workspace_resolve` | `command_receipts.py::replay_response` | 下一边界请求身份。 |
| `delivered` | 同上，`workflow.workspace_resolve` | 同上 | 处置投递结果。 |
| `planId` | 同上，`workflow.cleanup_plan/cleanup_apply` | 同上；`macro_task_deletion.py::_delete_rows` | 清理计划身份。 |

非当前视图命令的当前调用方也可确定下列外层键；`task`、`harness`、`counts`、`results` 的内层结构属于各自的返回视图或被调函数，不扩作 `response_json` 顶层键。

| `response_json` 顶层键 | 写入者文件与函数、kind | 读者文件与函数 | 判断 |
| --- | --- | --- | --- |
| `task` | `store/store.py::task_submit/task_cancel/task_retry/task_acknowledge`，各 `task.*` kind | `tasks/command_receipts.py::replay_response` 原样回放；`store/store.py::_receipt_response` | 保存当时任务视图，非当前视图命令无重建。 |
| `duplicate` | `store/store.py::task_submit/task_acknowledge`，`task.submit/task.acknowledge`；`service/service.py::quota_redetect`，`quota.redetect`；`evaluation/evaluation.py::user_policy_publish`，`user_policy.publish`；`tasks/workflow.py::stop_macro_task`，`macro_task.stop` | `command_receipts.py::replay_response` 原样回放 | 提交时的幂等标记；上层回放可能另置 True。 |
| `alreadyTerminal` | `store/store.py::task_cancel`，`task.cancel` | 同上 | 取消时是否已终结。 |
| `retry` | `store/store.py::task_retry`，`task.retry`；`service/service.py::harness_prepare`，`harness.prepare` | 同上 | 两种 kind 各有自己的布尔语义。 |
| `harness` | `service/service.py::harness_prepare/quota_redetect`，`harness.prepare/quota.redetect` | 同上 | 前者为健康记录对象，后者为 harness 名；依 kind 区分。 |
| `provider` | `blackboard/routing/quota_routing.py::redetect` 经 `service/service.py::quota_redetect`，`quota.redetect` | 同上 | 冷却来源。 |
| `cleared` | 同上 | 同上 | 当次是否清除冷却。 |
| `published` | `evaluation/evaluation.py::user_policy_publish`，`user_policy.publish` | 同上 | 当次发布成功。 |
| `counts` | 同上 | 同上 | 发布计数对象。 |
| `macroTaskId` | `tasks/workflow.py::stop_macro_task`，`macro_task.stop` | `command_receipts.py::replay_response`；`tasks/macro_task_deletion.py::_delete_rows` | 删除归属可定位到明确 writer。 |
| `results` | 同上 | `command_receipts.py::replay_response` | 当次停止范围的结果列表。 |

非 `_CURRENT_VIEW_KINDS` 的当前调用方及外层键已列于上表；其内层返回视图由各调用方生产，存储层只原样保留，不能据此推断内层键域封闭。删除路径 `tasks/macro_task_deletion.py::_delete_rows` 还以 JSON 路径检查 `runId/targetRunId/requestId/continuationId/planId/artifactId/integrationId/conclusionId/conflictId/macroTaskId`；其中 `macroTaskId` 来自 `tasks/workflow.py::stop_macro_task`。`tasks/storage.py::_workspace_reference_rows` 是泛用位置引用保护读者，不构成键的领域消费。被删命令当前清为 `{}`，ADR-028 #7 已定删除，不为它建立兼容语义。表判断：保留未删除目标的幂等账本；完成删除后清其命令及墓碑已定。`created_at` 和 `commands_kind_idx` 无明确排序消费者，需评估索引。
<!-- END commands -->

<!-- BEGIN resource_claims -->
## 15. `resource_claims`

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `claim_id` | `blackboard/store/store.py::_admit_in_transaction` | 同文件 `resource_claims` 公共投影 | 键约束 | 稳定公示身份；重试冲突重持原行。 |
| `task_id` | 同上 | `store.py::_admission_blocker/resource_claims`；`tasks/macro_task_deletion.py::_delete_rows` | 键约束 | `(task_id,resource)` 唯一及资源排斥。 |
| `attempt_id` | 同上，重试时 UPSERT 更换 | `store.py::_retain_claims/_release_claims/execution_connection/resource_claims` | 在用 | 将 claim 与生存的 attempt 绑定。 |
| `resource` | 同上 | `store.py::_admission_blocker/resource_claims` | 在用 | cwd 交叠及 exclusive 名称排斥。 |
| `kind` | 同上 | `store.py::_admission_blocker/resource_claims` | 在用 | cwd 与 exclusive 的冲突算法不同。 |
| `state` | 同上；`store.py::_retain_claims/_release_claims/execution_connection` | 同文件 `_held_claims/reconcile_startup/resource_claims` | 在用 | `held/retained/released`；不确定 shutdown 时 retained 挡重试。 |
| `created_at` | `store.py::_admit_in_transaction` | 同文件 `resource_claims` 排序与投影 | 在用 | 重持时重置，体现当前 claim 的起点。 |
| `released_at` | `store.py::_release_claims/_admit_in_transaction` | 未见 | 无读/写（只写） | 检索 `rg -n 'released_at|resource_claims' src/hey_my_buddy/blackboard --glob '*.py'`：本表该列仅写/清与 DDL；`workflow.py` 同名字段属于 `workspace_reservations`。`resource_claims` 的 SELECT * 消费只用 `claim_id/task_id/attempt_id/resource/kind/state/created_at`。 |

表判断：保留当前/历史资源 claim 以执行排斥与重持；`released_at` 可删候选。`released` 记录是否仍需长期保留尚未由当前查询证明：`_held_claims` 只查 held/retained，但 `(task_id,resource)` 唯一和 UPSERT 使用历史行，若改为 release 后删除，需保证并发事务与稳定 claim id 契约。无 JSON。
<!-- END resource_claims -->

<!-- BEGIN cursors -->
## 16. `cursors`

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `consumer` | 未见 | 未见 | 无读/写 | 只出现在 `store/db.py::CORE_SCHEMA` DDL。 |
| `seq` | 未见 | 未见 | 无读/写 | 同上。 |
| `updated_at` | 未见 | 未见 | 无读/写 | 同上。 |

表判断：整表可删候选。检索模式 `rg -n '\bcursors\b|(?:FROM|INTO|UPDATE|JOIN) cursors\b' src/hey_my_buddy apps/console/src --glob '!**/assets/**'`，命中仅 `store/db.py` 表定义；`store/store.py::_claim_candidates` 的同名局部字典并非表读写。无 JSON、索引。
<!-- END cursors -->

<!-- BEGIN attempt_activity -->
## 17. `attempt_activity`

| 列 | 产品写入者文件与函数 | 产品读者文件与函数 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `attempt_id` | `store/store.py::_store_activity` | 同文件 `_activity_latest`；`tasks/reminders.py::_native_at`；`tasks/macro_task_deletion.py::_delete_rows` | 键约束 | 最新投影每 attempt 一行。 |
| `task_id` | `store/store.py::_store_activity` | `tasks/macro_task_deletion.py::_delete_rows`；`attempt_activity_task_idx` | 可推出/重复 | 用 `attempt_id → attempts.task_id` 推导；写在同一 `_store_activity` 调用，删除路径可按 attempt 集合定位；索引 `(task_id,updated_at)` 当前可服务删除路径的 task_id 前缀；若去此副本列，须同步迁移删除 SQL 与索引。 |
| `generation` | `store/store.py::_store_activity` | 未见产品读 | 可推出/重复 | `attempt_id → attempts.generation`；同一 attempt 行不可换代。检索 `rg -n 'attempt_activity|generation' src/hey_my_buddy/blackboard --glob '*.py'`：除 `_store_activity` INSERT/UPSERT 与 DDL，没有 SELECT generation。 |
| `event_seq` | `store/store.py::_store_activity` | 未见产品读 | 可推出/重复 | `json_extract(activity_json,'$.eventSeq')` 可取；`protocol/activity.py::recency` 实际读解析后的 JSON。检索 `rg -n 'attempt_activity|event_seq' src/hey_my_buddy/blackboard --glob '*.py'`：只有写和 DDL。 |
| `observed_at` | `store/store.py::_store_activity` | 未见产品读 | 可推出/重复 | `json_extract(activity_json,'$.observedAt')` 可取；活动 JSON 为规范化投影。检索同表名加 `observed_at`，仅写与 DDL。 |
| `activity_json` | `store/store.py::_store_activity` | 同文件 `_activity_latest` → `_decorate`；`tasks/reminders.py::status` 间接消费 | 在用 | 最新 native 活动及单调比较的权威投影。 |
| `created_at` | `store/store.py::_store_activity` | 未见产品读 | 无读/写（只写） | `rg -n 'attempt_activity|created_at' src/hey_my_buddy/blackboard --glob '*.py'`：只见 INSERT 与 DDL，`_activity_latest` 仅 SELECT `activity_json`。 |
| `updated_at` | `store/store.py::_store_activity` | `tasks/reminders.py::_native_at` | 在用 | 提醒以最近服务接收时间评估。 |
| `revision` | `store/store.py::_store_activity` | 未见产品读 | 无读/写（只写） | 同表 SQL 搜索只在 UPSERT 中递增；`_activity_latest` 不投影 revision，前端看的是 JSON。 |

JSON 顶层：`protocol/activity.py::normalize_activity` 拒绝未知键，`store/store.py::_store_activity` 写入规范化结果，`_activity_latest` 解码后由 `_decorate` 投影；`apps/console/src/task-detail/parse.ts::parseActivity` 消费展示字段。以下九键是完整允许集，只有 `phase` 必需，其余按值存在时写入。

| `activity_json` 顶层键 | 写入者文件与函数 | 读者文件与函数 | 判断 |
| --- | --- | --- | --- |
| `phase` | `protocol/activity.py::normalize_activity` → `store/store.py::_store_activity` | `apps/console/src/task-detail/parse.ts::parseActivity`；`store/store.py::_activity_latest` | 必需状态。 |
| `observedAt` | 同上 | `protocol/activity.py::recency`；`apps/console/src/task-detail/parse.ts::parseActivity` | 与独立列 `observed_at` 重复候选。 |
| `eventSeq` | 同上 | `protocol/activity.py::recency`；`apps/console/src/task-detail/parse.ts::parseActivity` | 与独立列 `event_seq` 重复候选。 |
| `nativeSessionId` | 同上 | `apps/console/src/task-detail/parse.ts::parseActivity` | 原生会话身份。 |
| `lastNativeActivityAt` | 同上 | 同上 | 原生活动时刻。 |
| `lastToolActivityAt` | 同上 | 同上 | 工具活动时刻。 |
| `toolName` | 同上 | 同上 | 最近工具名。 |
| `waitingReason` | 同上；`protocol/reasons.py::validate_waiting_reason` 校验 | `store/store.py::execution_progress` 写入前校验；`store/store.py::_activity_latest` 整体透传，持久化后无逐键读 | 有限原因对象；当前服务接受原生授权或用户输入。 |
| `counts` | `protocol/activity.py::normalize_activity` → `store/store.py::_store_activity` | `apps/console/src/task-detail/parse.ts::parseActivity` | 子键仅 `modelTurns/toolCalls`，两者均可缺席。 |

表判断：保留最新 JSON 与 `updated_at`，其余重复/无读列需连同索引审视。泛用存储位置扫描仅是引用保护读者。
<!-- END attempt_activity -->

<!-- BEGIN catalog_observations -->
## 18. `catalog_observations`

结论：保留观察请求的幂等回执和冻结响应有依据；`created_at`、`completed_at` 是候选剪除列，须先确认外部接口是否承诺观察时间。此表的 `response_json.catalog` 与 `evaluation_catalog.payload_json` 共享发现内容，但回执还含本次应用/过期结果，不能仅按内容相同删除整表。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| observation_id | catalog/catalog_store.py `begin`（自增） | `begin`, `record`, `current`; catalog_current 外键式引用 | 键约束 | 观察顺序决定旧结果不能盖新结果；`current` 通过它连接本次响应。 |
| request_id | catalog/catalog_store.py `begin` | `begin` | 键约束 | UNIQUE 是发现请求重放键。 |
| created_at | catalog/catalog_store.py `begin` | 无领域读者 | 无读/写 | 检索 `created_at`、`catalog_observations` 于 `src/hey_my_buddy` 与 `apps/console/src`：只有 INSERT；`SELECT *` 的 `begin` 仅取 id、response、harnesses。 |
| completed_at | catalog/catalog_store.py `record` | 无领域读者 | 无读/写 | 同范围检索 `completed_at`：仅 UPDATE；完成与否由 `response_json` 空值判别。 |
| response_json | catalog/catalog_store.py `record` | `begin`, `record`, `current` | 冻结证据 | 固定请求的返回、冲突检测和发现展示；完成判断也读此列。 |
| harnesses_json | catalog/catalog_store.py `begin` | `begin` | 冻结证据 | 记录开始观察时的 harness 健康快照。 |

JSON：`harnesses_json` 是 `read_health` 结果数组，顶层无对象键；`begin` 原样返回，键集合来自 service/harness_health.py `read_health` 的开放健康视图。 `response_json` 的固定顶层键 `discoveryId, observationId, duplicate, catalog, buddies, appliedHarnesses, staleHarnesses` 由 `record` 写，`begin` 整体回放，`record` 读 `discoveryId` 作冲突判定，`current` 读 `catalog.discoveries[].harness/content`。其余键没有领域内逐键读取，但属于返回回执；`observationId` 可由行键推出，`catalog.observationId/discoveryId` 与外层重复，`catalog` 的主体与 `evaluation_catalog.payload_json` 同源。需核对公开回执契约后才可裁剪。无本表显式次级索引。
JSON 核对补充：`harnesses_json` 的每个数组元素是 `harness_health.read_health` 当时的完整 view，顶层键由第 46 节的 record 键和强制添加的 `providers/account/permissions/revision/manualPath/checkedAt/expiresAt/scanAfter` 组成；writer=`catalog_store.begin`，reader=`begin` 返回的 harnesses→`catalog.discover`→各 native availability binding。这不是任意未知对象。`response_json` 每个固定顶层键均由 `catalog_store.record` 写，`begin/record` 原样 replay，因此每键有完整回执读者；专用读取为 discoveryId（冲突判断）、catalog（current 读 discoveries[].content）。`catalog.observationId/discoveryId` 与本行身份或外层值同源，保留一份可以投影重建；删除回执中重复字段之前不能改变幂等返回的公开形状。
<!-- END catalog_observations -->

<!-- BEGIN catalog_current -->
## 19. `catalog_current`

结论：每 harness 的当前观察及保留的可信 catalog 是独立投影，仍需要；`updated_at` 与 `read_at` 分别表示任何应用和可信读取，不能合并。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| harness | catalog/catalog_store.py `record` | `current`、catalog/catalog.py `catalog_read_at` 等按 harness 查，catalog_pending 外键 | 键约束 | 当前投影主键。 |
| observation_id | catalog/catalog_store.py `record` | `record`, `current` | 在用 | 比较新旧观察编号并取本次响应；没有声明数据库 FK。 |
| discovery_id | catalog/catalog_store.py `record` | `current`, `record`, catalog/catalog.py `recorded_catalog`, service/harness_health.py `read_health` | 在用 | 未可信观察仍保留此前可信 payload。 |
| status | catalog/catalog_store.py `record` | `current`, evaluation/evaluation.py `console_core`, service/harness_health.py `read_health` | 在用 | 当前观察可信性，不等于历史 payload 可用性。 |
| reason | catalog/catalog_store.py `record` | `current`, evaluation/evaluation.py `_buddy_view` | 在用 | 当前未知原因供控制台展示。 |
| updated_at | catalog/catalog_store.py `record` | service/harness_health.py `read_health` | 在用 | DSH billing 观察时刻。 |
| read_at | catalog/catalog.py `note_confirmed_read` | `catalog_read_at`, service/harness_health.py 刷新门限 | 在用 | 最近可信读取时刻。 |
| account_status | catalog/catalog.py `note_confirmed_read` | `catalog_account_status` | 在用 | 账户状态与 harness 健康状态不同。 |

JSON：`reason` 为 `{code,params}`，写端 `record`，读端 `current`/控制台；`params` 的键由原因注册表定义，开放于各原因码，无证实的无人读固定键。无显式次级索引。
`reason` 实际是 JSON 文本而非普通说明。顶层精确为 `code`、`params`，两键写者=`catalog_store.record`，读者=`catalog_store.current`→健康/控制台展示。params 的当前来源：黑板构造的 `catalog.account-status-unknown/catalog.native-unknown` 带 `harness`；`catalog.discover` 的 `catalog.discovery-failed` 带 `harness/errorCode`；不能执行原生发现时沿用健康原因的 `operation`；原生发现允许集按 `catalog._receipt_reason` 与 `protocol/reason-codes.json` 校验，其当前 discovery reason 大多 `{}`。`params` 是 cause 当时的事实，不与本表 status 一一对应。
<!-- END catalog_current -->

<!-- BEGIN model_service_notes -->
## 20. `model_service_notes`

结论：用户写的模型服务注释独立于 native catalog，应保留。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| harness | evaluation/user_policy.py `publish` | `model_service_notes`、routing/decision.py `_select_input` | 键约束 | 模型服务复合键成员。 |
| provider | 同上 | 同上 | 键约束 | 同上。 |
| model | 同上 | 同上 | 键约束 | 同上。 |
| text | 同上 | `model_service_notes`；控制台 `draft.ts`、`ModelDrawer.tsx` | 在用 | 用户注释被展示，也进入 Router annotations。 |
| updated_at | 同上 | `model_service_notes`，控制台模型注释类型/视图 | 在用 | 用户最后编辑时间，是独立事实。 |

无 JSON 列及显式次级索引。
<!-- END model_service_notes -->

<!-- BEGIN evaluation_state -->
## 21. `evaluation_state`

结论：整表可剪候选。`decision_buddy_id` 有一处旧读路径但没有产品写入者，现值只能是 NULL；应与该读路径一起移除或改用当前 Router 设置，不能只删列。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| id | store/db.py 新板初始化 | service/service.py `admit_execution` | 键约束 | 常量 1 的单行键；仅支撑下述失效读。 |
| decision_buddy_id | 无产品写入者 | service/service.py `admit_execution` | 无读/写 | 检索 `decision_buddy_id`、`evaluation_state` 于 `src/hey_my_buddy`、`apps/console/src`：只发现服务的 JOIN 读取；创建时此列默认 NULL。 |
| created_at | store/db.py 新板初始化 | 无领域读者 | 无读/写 | 同范围检索仅 INSERT。 |
| updated_at | catalog/catalog_store.py `record`、evaluation/user_policy.py `publish` | 无领域读者 | 无读/写 | 同范围检索只有 UPDATE，没有取值；事件头另有更新信号。 |

无 JSON 列及显式次级索引。
<!-- END evaluation_state -->

<!-- BEGIN buddies -->
## 22. `buddies`

结论：用户 `enabled` 与 native `available` 必须分开；身份、显示、可用性仍需投影表。与 catalog payload 重叠的标签/能力是当前快照的可推出候选，但保留的旧 Buddy 在新读数中可能缺席，需证明历史身份恢复规则后才可合并。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| buddy_id | catalog/catalog_store.py `record` | `record`、evaluation/evaluation.py `console_core`、routing/decision.py `_scan_candidates`、router.py 等 | 键约束 | 稳定 Buddy 身份，其他表引用。 |
| label | catalog/catalog_store.py `record` | evaluation/evaluation.py `_buddy_view`、routing/decision.py `_buddy_input` | 在用 | 展示和模型候选输入。 |
| harness | catalog/catalog_store.py `record` | catalog、policy、routing、调度按 harness 查 | 键约束 | Native 身份成员及查询前导。 |
| provider | 同上 | routing/decision.py、evaluation/evaluation.py、catalog | 在用 | 模型服务身份。 |
| model | 同上 | 同上 | 在用 | 同上。 |
| effort | 同上 | routing/decision.py、catalog/catalog.py、evaluation/evaluation.py | 在用 | 努力档位身份。 |
| available | catalog/catalog_store.py `record`、service/harness_health.py `_set_buddies_unavailable` | evaluation/evaluation.py、routing/decision.py、catalog/catalog_store.py | 在用 | native 状态，不能从用户 enabled 推出。 |
| enabled | evaluation/user_policy.py `publish` | evaluation/evaluation.py、routing/decision.py、store/store.py 调度 | 在用 | 用户选择，不能从 native 读数推出。 |
| capabilities_json | catalog/catalog_store.py `record` | evaluation/evaluation.py `_buddy_view`、routing/decision.py `_buddy_input` | 在用 | 候选能力。 |
| context_window | catalog/catalog_store.py `record` | evaluation/evaluation.py `_buddy_view`、routing/decision.py | 在用 | 模型上下文事实。 |
| description | catalog/catalog_store.py `record` | evaluation/evaluation.py `_buddy_view`、routing/decision.py | 在用 | 显示及候选描述。 |
| source | catalog/catalog_store.py `record` | evaluation/evaluation.py `_buddy_view` | 在用 | provenance；`_buddy_content` 比较时刻意忽略。 |
| unavailable_code | catalog/catalog_store.py `record`、service/harness_health.py | evaluation/evaluation.py `_buddy_view`、catalog/catalog_store.py `record` | 在用 | 判定确认缺席、努力档退休与健康失败。 |
| unavailable_params_json | 同上 | evaluation/evaluation.py `_buddy_view` | 在用 | 原因参数。 |
| unavailable_content | catalog/catalog_store.py `record` | evaluation/evaluation.py `_buddy_view` | 在用 | Native 原文展示。 |

JSON：`capabilities_json` 是能力字符串数组，由 `proposed_buddies` 写、`buddy_capabilities` 与 Router 输入读，顶层无键；`unavailable_params_json` 是随 `unavailable_code` 变化的参数开放对象，写端 catalog/健康模块，读端原因解码。未发现稳定的无人读键。索引 `buddies_current_idx(available,buddy_id)` 可服务可用候选扫描；`buddies_harness_idx(harness,buddy_id)` 可服务按 harness 重新发布与健康失效，未列为无用。
字段函数补充：上表简称 routing/decision.py 的读者具体是 `DecisionCoordinator._candidate_bounds/_scan_candidates/_buddy_input/_publish_select`，catalog 的读者具体是 `catalog_store.record` 与 `catalog.validate_buddy/_check_recorded`，evaluation 的读者是 `EvaluationStore.console_core/_buddy_view`。`unavailable_params_json` 当前顶层键可以为空，健康失败来源带 `operation`（`harness_health._set_buddies_unavailable`），native model unavailable、确认消失和 effort retired 均 `{}`（`catalog_store.record`），所有值由 `_buddy_view`→`reason_from_json` 原样输出；没有确认无读键。harness 被表定义设成普通 NOT NULL，原表“键约束”在这里指 buddy 的身份组成/碰撞检查，而非该单列有 UNIQUE：实际防碰撞在 `catalog_store.record` 比较已存 harness/provider/model/effort。
<!-- END buddies -->

<!-- BEGIN evaluation_preferences -->
## 23. `evaluation_preferences`

结论：用户偏好表应保留；`note` 是人的解释，不与 mode 重复。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| buddy_id | evaluation/user_policy.py `publish` | `preferences`、routing/decision.py、routing/router_boundary_data.py | 键约束 | Buddy 外键与唯一偏好。 |
| mode | 同上 | 同上 | 在用 | pin/prefer/exclude 各影响选择。 |
| note | 同上 | evaluation/user_policy.py `preferences`、控制台 `draft.ts` | 在用 | 人的注释。 |

无 JSON 列。`evaluation_preferences_mode_idx(mode,buddy_id)` 对 `mode='pin'` 存在性查询有前导列用途。
<!-- END evaluation_preferences -->

<!-- BEGIN evaluation_decisions -->
## 24. `evaluation_decisions`

结论：公开的有界列表与 `decision_requests` 审计分表有读取依据；同一个 decision 的状态与事实应随任务删除。对 `task` 与 `requested_json.task` 的重复应区分：两者来自同一次请求，需核对更新路径后可只存一份。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| decision_id | routing/decision.py `_create` | `_row`/列表、evaluation/evaluation.py `console_core`、workflow_routes FK | 键约束 | 决策主键。 |
| status | routing/decision.py `_create`, `_finish`、重试 | `_status`、`health_summary`、控制台与工作流 | 在用 | 生命周期。 |
| task | routing/decision.py `_create` | `_view`、evaluation/evaluation.py `_decision_view` | 可推出/重复 | 与 `decision_requests.requested_json.task` 同一次写入；两者更新一致性须核对旧记录和所有重试后确认。 |
| buddy_id | routing/decision.py `_finish` | `_view`、控制台、工作流 | 在用 | 最终选中的 Worker。 |
| reason_code | routing/decision.py `_create`, `_finish` | `_view`、tasks/request_reasons.py | 在用 | 结构化失败/请 Host 处理原因。 |
| reason_params_json | 同上 | 同上 | 在用 | 同一原因的参数。 |
| note | routing/decision.py `_finish` | `_view`、控制台 | 在用 | 模型说明或用户可见文字，与结构化原因有互斥约束。 |
| created_at | routing/decision.py `_create` | `_view`、有界列表排序、控制台 | 在用 | 建立时间；`decision_requests.created_at` 同时写入。 |

JSON：`reason_params_json` 是原因码注册的开放参数对象，由 `_create/_finish` 写、`reason_from_json` 读，不能据没有固定键就判无读。`evaluation_decisions_created_idx(created_at,decision_id)` 对按两列倒序的控制台最新列表有用途。
<!-- END evaluation_decisions -->

<!-- BEGIN evaluation_catalog -->
## 25. `evaluation_catalog`

结论：完整 native catalog payload 是保留旧可信目录与核对发现身份的源；其旁列 `discovered_at/source/harness_version/provider_version/created_at` 有重复或无读候选。当前已确认只有 `payload_json` 被产品语义读取，历史多份记录是否需全留还需追保留策略。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| discovery_id | catalog/catalog_store.py `record` | `record` 去重、catalog_current 连接 | 键约束 | 内容摘要身份。 |
| discovered_at | 同上 | 无产品调用到达的字段消费 | 无读/写 | 检索 `discovered_at` 和 `CatalogView.from_payload` 于 `src/hey_my_buddy`、`apps/console/src`：`CatalogView.from_payload(payload,row)` 可读此字段，但所有产品调用只传 payload；无 row 参数。 |
| source | 同上 | 无领域读者 | 可推出/重复 | `payload_json.source` 同事务写入；全源码检索本表 SELECT 仅取 payload。 |
| harness_version | 同上 | 无领域读者 | 可推出/重复 | `payload_json.harnessVersion` 同事务写入；无读列查询。 |
| provider_version | 同上 | 无领域读者 | 可推出/重复 | `payload_json.providerVersion` 同事务写入；无读列查询。 |
| payload_json | 同上 | catalog/catalog_store.py `current/record`、catalog/catalog.py `recorded_catalog`、service/harness_health.py `read_health` | 冻结证据 | 完整可信发现，是旧 Buddy 保留/诊断的时间点证据。 |
| created_at | 同上 | 无领域读者 | 无读/写 | 检索本表所有 SELECT/JOIN 与 `created_at`：只有 INSERT；且与 `discovered_at` 同一 `now`。 |

JSON：`payload_json` 顶层 `source, providers, discoveries, warnings, harnessVersion, providerVersion, discoveredAt`（七键均由 catalog/catalog.py `canonical_payload` 固定构造，可为空值），写于 `record`，`current` 读 providers/discoveries，`record` 读 providers 与 catalog 提议，`recorded_catalog` 保留完整对象。未逐键读取的 `warnings`、版本与时间仍可能作为返回/审计的原始发现；`source/harnessVersion/providerVersion` 与旁列重复，`discoveredAt` 与旁列可能不同（payload 自带观察时间），不能无证据合并为一时刻。无显式次级索引。
JSON 精确键域补充：`catalog.py:canonical_payload` 总是构造七键（非可选）：`source`、`harnessVersion`、`providerVersion`、`discoveredAt`、`providers`、`discoveries`、`warnings`。每键 writer=`catalog_store.record` 保存 canonical payload；完整 reader=`CatalogView.payload` 的 catalog 返回/审计，专用 reader：`providers` 由 `CatalogView.lookup/efforts_for/effort_ranks/proposed_buddies`、`catalog_store.current/record` 和 `harness_health.read_health` 读取，`discoveries` 由 `catalog_store.current/record` 及 `CatalogView.metadata` 读取，`source` 由 `CatalogView.source/proposed_buddies/metadata` 读取，两个 version、`discoveredAt/warnings` 由 `CatalogView.metadata` 输出。前三个 scalar 旁列与 JSON 同值但没有列级读者；`discovered_at/created_at` 是记录写入 now，与 payload.discoveredAt 可不同。
<!-- END evaluation_catalog -->

<!-- BEGIN decision_requests -->
## 26. `decision_requests`

结论：请求幂等、当前执行绑定、输入与输出审计都在用；`created_at` 是重复候选，`kind` 目前恒为 select，但有查询和输出依赖，改动须同时调整契约与投影。冻结的 `input_json` 与 dispatch document 可在时间上分叉（重试），不能直接按相似字段合并。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| decision_id | routing/decision.py `_create` | `_row`、workflow_routes/router_requests 外键 | 键约束 | 同 evaluation_decisions 主键关联。 |
| request_id | 同上 | `_row_by_request`、router_sequence.py `record_claim`、删除路径 | 键约束 | UNIQUE 请求幂等键。 |
| kind | 同上 | routing/decision.py `_view`、`health_summary`、`_finish` | 可推出/重复 | `DECISION_KINDS=('select',)`，现存单一值；要核对对外 kind 输出契约。 |
| input_fingerprint | 同上 | routing/decision.py `_create` 重放 | 在用 | 比较原始 admission 输入；`requested_json` 后续会变，无法从其当前值重算。 |
| task_id | routing/decision.py `_queue_router_dispatch`、重试 | `_view`、工作流、删除、router_history.py | 在用 | 当前 Router 任务，非不可变 dispatch 列表。 |
| attempt_id | routing/decision.py `_start`、重试 | `_view`、router_history.py、结果门禁 | 在用 | 当前尝试绑定。 |
| generation | 同上 | 同上 | 在用 | 与尝试身份配套。 |
| selected_json | routing/decision.py `_finish` | `_view` | 冻结证据 | 决策当时 Worker 身份；当前 buddies 可改变。 |
| input_json | routing/decision.py `_queue_router_dispatch`、重试 | `_view` 审计、store/store.py 排队恢复、工作流 | 冻结证据 | 实际模型输入；重试会替换当前值，先前 dispatch 自有 document。 |
| input_sha256 | 同上 | routing/decision.py 结果校验、`_view` 审计 | 冻结证据 | 固定当前输入字节摘要；与 dispatch 摘要同值是门禁，不可仅凭可计算性删。 |
| output_json | routing/decision.py `_finish` | `_view`、边界/工作流读者 | 冻结证据 | 模型输出及 native 证据。 |
| requested_json | routing/decision.py `_create`、重试 | `_view`、`_retry_selection`、router.py、工作流 | 在用 | 原请求扩成当前路由事实；与原始指纹含义不同。 |
| created_at | routing/decision.py `_create` | 无领域读者 | 可推出/重复 | 与 evaluation_decisions.created_at 同 `now`；`_ROW_COLUMNS` 虽选作 `request_created_at`，全源码检索没有消费别名。 |
| updated_at | routing/decision.py `_create/_finish` 等 | `_view`、evaluation/evaluation.py `console_core` | 在用 | 当前请求更新时刻。 |

JSON 逐键补充：下表 `DC` 为 `src/hey_my_buddy/blackboard/routing/decision.py:DecisionCoordinator`；`RA` 为 `src/hey_my_buddy/buddy/roles/router.py:DecisionAdapter.collect`。`selected_json` 的每个键来自 `DC._buddy_input` 的冻结候选，`DC._finish` 写入，`DC._view` 完整输出。精确键为 `buddyId,label,harness,provider,model,effort,available,enabled,capabilities,contextWindow,description,source`，条件键 `unavailableReason,unavailableContent`。`buddyId` 与 evaluation_decisions.buddy_id 同次写入，其他身份/属性是当时的候选，不能换成当前 buddies 行。

| JSON 顶层键 | 写入者 | 读者 | 判断 |
| --- | --- | --- | --- |
| input：`operation`、`requestId`、`task` | `DC._assemble_input`→`_create/_queue_router_dispatch/_retry_selection` | `RA.prepare/collect`、`DC._publish_select/_view` | operation 当前恒 select；requestId/task 与请求相关，但实际发给模型的输入是独立冻结文档。 |
| input：`buddies`、`preferences`、`annotations`、`policyFacts` | `DC._table_input/_select_input` | native Router 输入；`DC._publish_select` 复核 policy；`_view` 审计 | 是请求时点的候选、用户设置与程序事实。 |
| input：`routerBuddyIds`、`routerIdentities`、`routingMode`、`routingBudget`、`routerRetryIntervalSeconds`、`budget` | `router._facts/_frozen_facts`→`DC._create/_retry_selection` | `RA.prepare/collect`、`DC._complete/_publish_select/_view`、`router_boundary_data.routing_boundary` | 初次、重试和后续派发可能不同，不合并历史版本。 |
| input：`routingBasis`、`constraints`、`requiredCapabilities` | `DC._create/_retry_selection` | `DC._publish_select/_view`、边界读者 | 同时写入 requested，但 input 是实际派发冻结证据。 |
| input：条件 `executionWorkspace`、`outputSchema` | `DC._create/_retry_selection` | `RA.prepare`、`DC._publish_select/_view`、Router review input | 具体输入/结果合同，不能按当前清单重建。 |
| input：`buddy`、`routerBuddy`、`routerBuddyId`、`routerIndex` | `DC._queue_router_dispatch` | `RA.prepare/collect`、`router_sequence._dispatch`、`DC._complete/_view` | 与本次 dispatch 行四元组/ID/index 同值；封存输入不改写，关系旁列可另作去重评估。 |
| requested：`kind`、`task`、`requiredCapabilities` | `DC.request_select/route_workflow/_create` | `DC._create/_retry_selection/_view` | kind 与列重复且恒 select；task 与 evaluation_decisions.task 重复，读者不依赖差异。 |
| requested：条件 `constraints`、`executionWorkspace`、`workflowRouting`、`taskReference` | `DC.route_workflow` | `DC._retry_selection/_publish_select/_view`；workflowRouting 无专用读者但 `_view(include_audit)` 完整返回 | constraints/workspace 是 workflow 硬边界；taskReference 是过长任务原文指针。workflowRouting 仅标来源的常量标记，若不承担冻结来源目的可剪，不能声称有业务分支消费。 |
| requested：`routingBasis` | `DC._create/_retry_selection` | `DC._view`、工作流边界 | 保存当次合法候选边界。 |
| requested：`routerBuddyIds`、`routerIdentities`、`routingMode`、`routingBudget`、`routerRetryIntervalSeconds`、`budget` | `DC._create/_retry_selection` 经 router facts | `router.current_router`、`DC._queue_router_dispatch/_view`、边界 | 可更新的当前选择设置证据；不能用 router_requests 首份 snapshot 替代重试版。 |
| requested：`routerBuddyId` | `DC._create` 在无候选/单候选时写 NULL | `router.routing_facts`、`DC._view` | 非 Router 调用路径的显式空值；有派发时投影以 actor 覆盖。 |
| requested：条件 `routerCalled` | `DC._create/_retry_selection`=false | `DC._view` | 程序直接选择的明确事实，未写不代表 true。 |
| requested：条件 `routerBudgetSeconds`、`routerProblem` | `DC._create/_retry_selection` | `DC._queue_router_dispatch`、`router.routing_facts`、边界 | budget 展开秒数是同次 budget.timeoutSeconds 的重复，可合并；problem 是原因证据。 |
| requested：条件 `selectionRetry`、`selectionRetryInspections` | `DC._retry_selection` | `DC._complete/_queue_router_dispatch`；边界及 audit 完整返回 inspections | 控制至多一次重新选 Worker，与序列内 Router failover 不同。 |
| output：`operation`、`status`、`requested`、`resolved`、`observed`、`modelStarted` | `RA.collect`→`DC._finish` | `DC._publish_select/_complete/_view`，audit 完整输出 | requested/resolved/observed 是不同来源，不把希望运行的值当已运行。 |
| output：`nativeIdentity`、`usage`、`toolEvidence`、`harnessVersion`、`nativeEvidence`、`nativeFailure`、`correctionCount` | `RA.collect`→`DC._finish` | `_publish_select/_tool_evidence_problem`、`_view` 审计 | native 过程证据；不是当前 harness 状态的副本。 |
| output：`budget`、`inputVerification`、`stopEvidence` | `RA.collect`→`DC._finish` | `DC._publish_select/_complete/_view` | 真实输入校验与实际停止不能从 success 状态推出。 |
| output：`routerBuddyIds`、`routerIdentities`、`routerBuddyId`、`routerBuddy`、`routerIndex`、`routingMode`、`routingBudget`、`routerRetryIntervalSeconds`、`routerProblem` | `RA.collect` 经 `router.routing_facts`；`DC._complete` 可添加 routerProblem | `DC._view` audit、边界 | 前几项与对应输入/派发事实同次同义；routerProblem 在所有 Router 失败后可新增，不能直接等同初始 problem。 |
| output：条件 `decision`、`reason`、`code`、`policyCheck` | `RA.collect`；`DC._publish_select/_complete/_finish` | `DC._publish_select/_view`、边界 | 模型回答和程序核验后的事实；reason 仅只读权限验证失败时给说明。 |
| output：条件 `programSelection` | `DC._adopt_sole_candidate`→`_finish` | `DC._view(include_audit)` | 程序直接选唯一候选的独立证据，未发生 native 调用。 |

实际持久化边界 `DC._complete` 接收执行结果 dict，按已知字段验证后仍保留整个 output，不拒绝扩展顶层键，因此未知未来键不是“已穷举且无人读”；上表穷举当前产品构造器的键，完整审计投影读取所有已接受键。`output_json.elapsedSeconds/shutdownConfirmed` 只有 `_view` 的兼容式取值，`RA.collect` 当前不构造这些顶层键：实测时间在 usage.elapsedMs、停机证明在 stopEvidence；删除无 writer 的读取分支不需要增加新功能，但不重写旧封存结果。

索引：`decision_requests_created_idx(created_at,decision_id)` 没有匹配的当前分页/排序消费；`DecisionCoordinator.list_decisions` 按 rowid，控制台最新决策按 evaluation_decisions.created_at；`decision_requests_task_idx(task_id)` 支持当前执行反查与删除。检索 `FROM decision_requests`、`ORDER BY r.created_at`、`request_created_at` 于产品 src/apps，别名只有定义无消费。
<!-- END decision_requests -->

<!-- BEGIN model_service_concurrency -->
## 27. `model_service_concurrency`

结论：用户设置与代码默认值有区别，且设置须跨 native 暂时不可用保留，独立表必要；两个时间列无产品读取。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| harness | evaluation/user_policy.py `publish` | store/store.py `model_capacity_rows` | 键约束 | 模型服务复合键。 |
| provider | 同上 | 同上 | 键约束 | 同上。 |
| model | 同上 | 同上 | 键约束 | 同上。 |
| concurrency_limit | 同上 | store/store.py `model_capacity_rows`，控制台 `ModelDrawer.tsx` | 在用 | 显式上限覆盖代码默认值。 |
| created_at | 同上 | 无领域读者 | 无读/写 | 检索 `model_service_concurrency` 与 `created_at` 于 src/apps：仅 INSERT。 |
| updated_at | 同上 | 无领域读者 | 无读/写 | 同范围仅 INSERT/UPDATE。 |

无 JSON 列及显式次级索引。
<!-- END model_service_concurrency -->

<!-- BEGIN model_facts -->
## 28. `model_facts`

结论：目前产品会在启用模型服务时写快照，但没有产品调用 `model_service_facts`；整表属于当前无消费者的候选清理。未来 profile 面的预留不能按在用算。若保留以供稍后 L11 使用，须标未接线，不能声称现已影响路由。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| harness | evaluation/model_facts.py `refresh_model_services` | `model_service_facts`（无产品调用） | 无读/写 | 只作为快照键写入。 |
| provider | 同上 | 同上 | 无读/写 | 同上。 |
| model | 同上 | 同上 | 无读/写 | 同上。 |
| status | 同上 | 同上 | 无读/写 | 仅本模块孤立读 API。 |
| facts_json | 同上 | 同上 | 无读/写 | 同上。 |
| identity_json | 同上 | 同上 | 无读/写 | 同上。 |
| source | 同上 | 同上 | 无读/写 | 同上。 |
| payload_sha256 | 同上 | `snapshot_marker`（无产品调用） | 无读/写 | 同上。 |
| fetched_at | 同上 | `model_service_facts/snapshot_marker`（无产品调用） | 无读/写 | 同上。 |
| reason | 同上 | `model_service_facts`（无产品调用） | 无读/写 | 同上。 |

检索模式/范围：`rg 'model_service_facts\(|snapshot_marker\('` 覆盖 `src/hey_my_buddy` 排除定义文件，没有命中；`rg 'model_facts'` 覆盖产品 src/apps，只有刷新调用、DDL 及模块内查询。`snapshot_marker` 注释自身限定测试/审计。JSON：`facts_json` 的顶层由 `extract_facts` 写成可选 `price,billing,contextLength,outputLimit,releaseDate,capabilities`；`identity_json` 在 provider 未解析时为 `{}`，仅 model 未解析时为 `{devProvider}`，两者均解析时为 `{devProvider,devModel}`，两列仅孤立读 API 解析，当前无领域读键。无显式次级索引。
<!-- END model_facts -->

<!-- BEGIN workflow_turns -->
## 29. workflow_turns

列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据
--- | --- | --- | --- | ---
turn_id | W:begin_turn | W:_turn_row/_turn_view/_record_request，D:_collect | 键约束（PRIMARY KEY） | turn 身份及其他表引用。
run_id | W:begin_turn | W:compact/_audit/begin_turn，M:_run_spans，D:_delete_rows | 键约束（workflow_runs FK；UNIQUE(run_id,turn_index)） | 所属 run 与顺序。
attempt_id | W:begin_turn | W:_turn_view/settle_turn，M:_run_spans，D:_collect | 键约束（attempts FK） | 一次 turn 的执行绑定。
generation | W:begin_turn | W:_turn_view/settle_turn | 冻结证据（准备 turn 时 attempt generation） | 显示历史执行归属。
turn_index | W:begin_turn | W:_turn_view/begin_turn，M:_run_spans | 键约束（run_id,turn_index UNIQUE） | 顺序与续接。
resume_mode | W:begin_turn | W:_turn_view/settle_turn | 冻结证据（turn 开始时恢复方式） | 事后运行意图可变。
previous_session_id | W:begin_turn | W:_turn_view/begin_turn | 冻结证据（turn 开始时前次会话） | 历史 native 续接身份。
session_id | W:_late_cancelled_turn/settle_turn | W:_turn_view/begin_turn | 冻结证据（turn 实际会话） | 不能从后来 turn 推导。
prompt_sha256 | W:_late_cancelled_turn/settle_turn | W:_turn_view（audit） | 冻结证据（当次 prompt 哈希） | 审计精确输入。
input_sha256 | W:_late_cancelled_turn/settle_turn | W:_turn_view/_accept_verification_inputs | 冻结证据（当次输入哈希） | 封存输入校验，不等同当前 run 数据。
input_json | W:begin_turn | W:_turn_input_context/_turn_view/_turn_context/begin_turn，D:_delete_private_roots | 冻结证据（turn 开始时完整输入） | 与 digest、原生 checkpoint 绑定，不能改写封存内容。
access | W:begin_turn | W:_turn_view/begin_turn | 重复/可推出（封存镜像） | 与 input_json.executionWorkspace.access 同次写入；列供历史投影及 native resume 权限比较，若旧行二者不同会影响行为，须核对 stock，旧 seal 不改写。
access_note | W:begin_turn | W:_turn_view | 冻结证据（full turn 的 Host note） | 与当时 execution_workspace_json 同源，历史快照。
expected_seconds | W:begin_turn | W:_turn_view | 重复/可推出（turn 时冻结镜像） | begin_turn 从 tasks.expected_seconds 复制，产品无 UPDATE tasks.expected_seconds；可按 run_id=task_id 读取原值，但需先核对 stock 差异，旧封存输入不改写。
state | W:_late_cancelled_turn/begin_turn/settle_turn | W:_turn_view/compact/begin_turn | 在用 | turn 生命周期。
disposition | W:_late_cancelled_turn/settle_turn | W:_turn_view/_turn_context | 冻结证据（当次 Worker 结论类别） | 后续请求状态不能替代。
outcome_json | W:_late_cancelled_turn/settle_turn | W:_turn_view/_turn_context，M:_run_presentation | 冻结证据（当次完整结构化结论） | summary/remaining/request/artifacts 来自当次结果。
outcome_truncated_json | W:_late_cancelled_turn/settle_turn | W:_turn_view | 冻结证据（当次截断报告） | 审计说明当次输出限界。
provenance_json | W:_late_cancelled_turn/settle_turn | W:_turn_view/begin_turn | 冻结证据（结果验证来源） | outputSchemaValidated 决定下一 turn 的提示。
turn_result_path | W:_late_cancelled_turn/settle_turn | W:_turn_view（audit） | 冻结证据（结果文件位置） | 关联当次有界进程传输文件。
sealed_artifacts_json | W:_late_cancelled_turn/settle_turn | W:_turn_view（audit） | 冻结证据（当次封存工件） | sealed digest 绑定，历史不改写。
created_at | W:begin_turn | W:_turn_view，M:_run_spans | 冻结证据（turn 准备时刻） | timeline 起点。
updated_at | W:_late_cancelled_turn/begin_turn/settle_turn | W:_turn_view，M:_run_spans | 在用 | 当次阶段结束时刻。

JSON 键级清点：下表逐键列出现行构造来源与消费。input_json.executionWorkspace 是准备后的 manifest，准确的 v1/v2/v3 顶层键、摘要校验和读者见第 42 节 input/turn-input；context 由 _bound_context 有界裁剪后整包交给 Worker，裁剪只改变数组长度。outcome_json 的 request 非 null 时由 buddy/roles/turn_io.py:validate_outcome 限定 summary、attempted、neededWork、expectedArtifacts、acceptance，另可有 suggestedBuddyId；workflow.py:_record_request 各键实际消费。provenance_json 按 harness 分支，以下四组覆盖 schema 26 当前 driver；可选键只在相应 native 观察发生时写，不以别的 harness 键域推断为冗余。

键 | 写入者（产品函数） | 读者（产品函数） | 判断
--- | --- | --- | ---
input_json.version | workflow.py:begin_turn | buddy/roles/turn_io.py:read_turn；buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 完整输入由 input_sha256 绑定；身份键与列同次冻结
input_json.taskId | workflow.py:begin_turn | buddy/roles/turn_io.py:read_turn；buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 完整输入由 input_sha256 绑定；身份键与列同次冻结
input_json.attemptId | workflow.py:begin_turn | buddy/roles/turn_io.py:read_turn；buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 完整输入由 input_sha256 绑定；身份键与列同次冻结
input_json.generation | workflow.py:begin_turn | buddy/roles/turn_io.py:read_turn；buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 完整输入由 input_sha256 绑定；身份键与列同次冻结
input_json.turnId | workflow.py:begin_turn | buddy/roles/turn_io.py:read_turn；buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 完整输入由 input_sha256 绑定；身份键与列同次冻结
input_json.resumeMode | workflow.py:begin_turn | buddy/roles/turn_io.py:read_turn；buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 完整输入由 input_sha256 绑定；身份键与列同次冻结
input_json.previousSessionId | workflow.py:begin_turn | buddy/roles/turn_io.py:read_turn；buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 完整输入由 input_sha256 绑定；身份键与列同次冻结
input_json.context | workflow.py:begin_turn | buddy/roles/turn_io.py:read_turn；buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 完整输入由 input_sha256 绑定；身份键与列同次冻结
input_json.executionWorkspace | workflow.py:begin_turn | buddy/roles/turn_io.py:read_turn；buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 完整输入由 input_sha256 绑定；身份键与列同次冻结
input_json.context.task | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view/_quota_reroutable | 该 turn 的上下文；taskId/turnIndex 与列镜像
input_json.context.taskTruncated | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view/_quota_reroutable | 该 turn 的上下文；taskId/turnIndex 与列镜像
input_json.context.taskId | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view/_quota_reroutable | 该 turn 的上下文；taskId/turnIndex 与列镜像
input_json.context.turnIndex | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view/_quota_reroutable | 该 turn 的上下文；taskId/turnIndex 与列镜像
input_json.context.buddy | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view/_quota_reroutable | 该 turn 的上下文；taskId/turnIndex 与列镜像
input_json.context.delegationAuthority | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view/_quota_reroutable | 该 turn 的上下文；taskId/turnIndex 与列镜像
input_json.context.routing | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view/_quota_reroutable | 该 turn 的上下文；taskId/turnIndex 与列镜像
input_json.context.ownedMicroTasks | workflow.py:_owned_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_owned_since_input | 仅 Lead turn；当时 owned 事实
input_json.context.ownedMicroTaskTotal | workflow.py:_owned_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_owned_since_input | 仅 Lead turn；当时 owned 事实
input_json.context.ownedFactsCursor | workflow.py:_owned_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_owned_since_input | 仅 Lead turn；当时 owned 事实
input_json.context.ownedMicroTasksTruncated | workflow.py:_owned_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_owned_since_input | 仅 Lead turn；当时 owned 事实
input_json.context.microTaskInputCommit | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 条件上下文，后续 turn 使用当时值
input_json.context.lastAssistantMessage | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 条件上下文，后续 turn 使用当时值
input_json.context.previousEvidenceNotice | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 条件上下文，后续 turn 使用当时值
input_json.context.interruptedTurn | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 条件上下文，后续 turn 使用当时值
input_json.context.lastCheckpoint | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 条件上下文，后续 turn 使用当时值
input_json.context.continuation | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 条件上下文，后续 turn 使用当时值
input_json.context.hostDecision | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 条件上下文，后续 turn 使用当时值
input_json.context.helperOutcomesFrozen | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 条件上下文，后续 turn 使用当时值
input_json.context.helperOutcomes | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 条件上下文，后续 turn 使用当时值
input_json.context.helperOutcomeTotal | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 条件上下文，后续 turn 使用当时值
input_json.context.artifacts | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 条件上下文，后续 turn 使用当时值
input_json.context.artifactTotal | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 条件上下文，后续 turn 使用当时值
input_json.context.artifactsTruncated | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 条件上下文，后续 turn 使用当时值
input_json.context.nextActions | workflow.py:_turn_context | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view（audit） | 条件上下文，后续 turn 使用当时值
input_json.context.nativeResume | workflow.py:begin_turn | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view | 条件恢复/共享 stash 观察
input_json.context.resumeReason | workflow.py:begin_turn | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view | 条件恢复/共享 stash 观察
input_json.context.resumeDetail | workflow.py:begin_turn | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view | 条件恢复/共享 stash 观察
input_json.context.sharedStash | workflow.py:begin_turn | buddy/roles/run_execution.py:worker_request；workflow.py:_turn_view | 条件恢复/共享 stash 观察
outcome_json.disposition | buddy/roles/schema_worker.py:delivery 或 native finish-tool → buddy/roles/turn_io.py:validate_outcome → workflow.py:settle_turn | workflow.py:settle_turn/_turn_view/_turn_context；macro_tasks.py:_run_presentation | validate_outcome 要求六键恰好齐全；request 可为 null
outcome_json.summary | buddy/roles/schema_worker.py:delivery 或 native finish-tool → buddy/roles/turn_io.py:validate_outcome → workflow.py:settle_turn | workflow.py:settle_turn/_turn_view/_turn_context；macro_tasks.py:_run_presentation | validate_outcome 要求六键恰好齐全；request 可为 null
outcome_json.remaining | buddy/roles/schema_worker.py:delivery 或 native finish-tool → buddy/roles/turn_io.py:validate_outcome → workflow.py:settle_turn | workflow.py:settle_turn/_turn_view/_turn_context；macro_tasks.py:_run_presentation | validate_outcome 要求六键恰好齐全；request 可为 null
outcome_json.decisions | buddy/roles/schema_worker.py:delivery 或 native finish-tool → buddy/roles/turn_io.py:validate_outcome → workflow.py:settle_turn | workflow.py:settle_turn/_turn_view/_turn_context；macro_tasks.py:_run_presentation | validate_outcome 要求六键恰好齐全；request 可为 null
outcome_json.artifacts | buddy/roles/schema_worker.py:delivery 或 native finish-tool → buddy/roles/turn_io.py:validate_outcome → workflow.py:settle_turn | workflow.py:settle_turn/_turn_view/_turn_context；macro_tasks.py:_run_presentation | validate_outcome 要求六键恰好齐全；request 可为 null
outcome_json.request | buddy/roles/schema_worker.py:delivery 或 native finish-tool → buddy/roles/turn_io.py:validate_outcome → workflow.py:settle_turn | workflow.py:settle_turn/_turn_view/_turn_context；macro_tasks.py:_run_presentation | validate_outcome 要求六键恰好齐全；request 可为 null
outcome_truncated_json.remaining | buddy/roles/turn_io.py:truncate_outcome_summaries → workflow.py:settle_turn | workflow.py:_turn_view | 各值为 droppedItems/shortenedItems；未截断时整个列 NULL
outcome_truncated_json.decisions | buddy/roles/turn_io.py:truncate_outcome_summaries → workflow.py:settle_turn | workflow.py:_turn_view | 各值为 droppedItems/shortenedItems；未截断时整个列 NULL
sealed_artifacts_json[].kind | workflow.py:_sealed_artifacts → settle_turn/_late_cancelled_turn | workflow.py:_turn_view（audit）；changedPaths/outsideScope 限界投影 | 数组元素固定七键，绑定当次 seal
sealed_artifacts_json[].manifestSha256 | workflow.py:_sealed_artifacts → settle_turn/_late_cancelled_turn | workflow.py:_turn_view（audit）；changedPaths/outsideScope 限界投影 | 数组元素固定七键，绑定当次 seal
sealed_artifacts_json[].snapshotSha256 | workflow.py:_sealed_artifacts → settle_turn/_late_cancelled_turn | workflow.py:_turn_view（audit）；changedPaths/outsideScope 限界投影 | 数组元素固定七键，绑定当次 seal
sealed_artifacts_json[].outputCommit | workflow.py:_sealed_artifacts → settle_turn/_late_cancelled_turn | workflow.py:_turn_view（audit）；changedPaths/outsideScope 限界投影 | 数组元素固定七键，绑定当次 seal
sealed_artifacts_json[].tree | workflow.py:_sealed_artifacts → settle_turn/_late_cancelled_turn | workflow.py:_turn_view（audit）；changedPaths/outsideScope 限界投影 | 数组元素固定七键，绑定当次 seal
sealed_artifacts_json[].changedPaths | workflow.py:_sealed_artifacts → settle_turn/_late_cancelled_turn | workflow.py:_turn_view（audit）；changedPaths/outsideScope 限界投影 | 数组元素固定七键，绑定当次 seal
sealed_artifacts_json[].outsideScope | workflow.py:_sealed_artifacts → settle_turn/_late_cancelled_turn | workflow.py:_turn_view（audit）；changedPaths/outsideScope 限界投影 | 数组元素固定七键，绑定当次 seal
provenance_json.codex.harness | buddy/harnesses/codex/native_run.py:run + buddy/roles/schema_worker.py:delivery | buddy/harnesses/codex/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.codex.nativeThreadId | buddy/harnesses/codex/native_run.py:run + buddy/roles/schema_worker.py:delivery | buddy/harnesses/codex/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.codex.nativeTurnId | buddy/harnesses/codex/native_run.py:run + buddy/roles/schema_worker.py:delivery | buddy/harnesses/codex/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.codex.finalItemId | buddy/harnesses/codex/native_run.py:run + buddy/roles/schema_worker.py:delivery | buddy/harnesses/codex/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.codex.finalMessageCompleted | buddy/harnesses/codex/native_run.py:run + buddy/roles/schema_worker.py:delivery | buddy/harnesses/codex/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.codex.nativeTurnStarted | buddy/harnesses/codex/native_run.py:run + buddy/roles/schema_worker.py:delivery | buddy/harnesses/codex/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.codex.nativeTurnCompleted | buddy/harnesses/codex/native_run.py:run + buddy/roles/schema_worker.py:delivery | buddy/harnesses/codex/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.codex.eventSeq | buddy/harnesses/codex/native_run.py:run + buddy/roles/schema_worker.py:delivery | buddy/harnesses/codex/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.codex.nativeRequestMethod | buddy/harnesses/codex/native_run.py:run + buddy/roles/schema_worker.py:delivery | buddy/harnesses/codex/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.codex.nativeRequestThreadId | buddy/harnesses/codex/native_run.py:run + buddy/roles/schema_worker.py:delivery | buddy/harnesses/codex/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.codex.nativeRequestTurnId | buddy/harnesses/codex/native_run.py:run + buddy/roles/schema_worker.py:delivery | buddy/harnesses/codex/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.codex.unansweredDeliveredInquiries | buddy/harnesses/codex/native_run.py:run + buddy/roles/schema_worker.py:delivery | buddy/harnesses/codex/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.codex.turnEnd | buddy/harnesses/codex/native_run.py:run + buddy/roles/schema_worker.py:delivery | buddy/harnesses/codex/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.codex.outputSchemaValidated | buddy/harnesses/codex/native_run.py:run + buddy/roles/schema_worker.py:delivery | buddy/harnesses/codex/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.codex.controllerAttention | buddy/harnesses/codex/native_run.py:run + buddy/roles/schema_worker.py:delivery | buddy/harnesses/codex/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.harness | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.nativeSessionId | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.resultSubtype | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.resultIsError | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.structuredOutputSource | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.initObserved | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.backgroundSettled | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.eventSeq | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.sessionModel | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.modelUsage | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.permissionDenials | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.deniedControlRequestIds | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.deniedToolUseIds | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.deniedToolNames | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.deniedOperationSummary | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.unansweredDeliveredInquiries | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.turnEnd | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.structuredOutputValidated | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.claude.controllerAttention | buddy/harnesses/claude/native_run.py:_native_turn_facts + buddy/roles/schema_worker.py:delivery | buddy/harnesses/claude/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.version | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.harness | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.tool | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.nativeTool | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.turnEnd | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.stopReason | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.rootSessionMatched | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.nativeSessionId | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.toolCallId | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.receiptId | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.receiptVerified | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.callOrdinal | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.resultOrdinal | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.settledOrdinal | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.sessionCloseOrdinal | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.nativeEventCount | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.dsh.sessionClose | buddy/harnesses/dsh/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/dsh/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.harness | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.tool | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.turnEnd | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.rootSessionMatched | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.inputId | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.nativeSessionId | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.nativeTurnId | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.toolCallId | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.receiptId | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.receiptVerified | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.turnStartSeq | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.toolCallSeq | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.toolResultSeq | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.turnEndSeq | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.turnCompletedOrdinal | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.promptCompletedOrdinal | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.sessionCloseOrdinal | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.toolResultSuccess | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.toolResultTruncated | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.turnResultType | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造
provenance_json.zcode.settlement | buddy/harnesses/zcode/protocol.py:RootTurnEvidence.provenance | buddy/harnesses/zcode/native_run.py:validate_turn_provenance；workflow.py:_turn_view（audit） | 该 harness 当次 native 证明；optional 键见源码构造

D:_delete_rows 按 run_id 删除 turn；sealed input/outcome/provenance/sealed_artifacts 及各摘要在存活 turn 中不可改写。旧 seal 内的重复身份键仍是原件的一部分，新封存形式变更必须自带版本；当前删除路径未见 turn 表残留。

本表是一 run 多 turn 的独立执行与 seal 事实，不能并入 workflow_runs 当前指针；workflow_turns_attempt_idx 用于 attempt 查找；workflow_turns_run_idx(run_id,turn_index) 与 UNIQUE(run_id,turn_index) 的 SQLite 自动索引同列同序，是明确重复索引候选。
<!-- END workflow_turns -->

<!-- BEGIN macro_tasks -->
## 30. macro_tasks

列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据
--- | --- | --- | --- | ---
macro_task_id | M:attach_macro_task | M:attach_macro_task/_summary，D:_delete_rows | 键约束（PRIMARY KEY） | 分组身份及 workflow_runs 外键。
title | M:attach_macro_task | M:_summary | 在用 | 用户设定的分组标题。
project_id | M:attach_macro_task | M:attach_macro_task/_summary | 在用 | 由提交时 manifest.repositoryId 或 spec.cwd 定归属；后来项目位置可能变化，不从当前成员反推。
project_path | M:attach_macro_task | M:_summary | 冻结证据（建组时项目路径） | 项目展示及建组上下文。
source_host_id | M:attach_macro_task | M:attach_macro_task/_summary | 在用 | 后续加入分组的 Host 约束；不能由当前 owner host 替代。
created_at | M:attach_macro_task | M:_summary | 冻结证据（建组时刻） | 分组生命周期起点。
activity_seq | M:record_activity | M:macro_task_list/_summary | 在用（索引投影，派生性未证） | 每次带 task_id 的 event 先由 record_activity 的当时 CTE 找到直接 run 或当前 decision_requests.task_id 的 route，再沿当时 helper/Lead 父链传播，并按更新后的 run.activity_seq 把同 seq 赋给所属 macro；现存关系未记录每次历史传播边，不能简单对当前成员事件取 MAX(seq)。列表排序/游标直接使用此值。
activity_at | M:attach_macro_task/record_activity | M:_summary | 在用（索引投影，派生性未证） | 对应 activity_seq 的 event.created_at；只有在上列历史传播关系可重建且该事件保留时才可推出，不能用 run.updated_at 代替。

本表是有独立标题、来源 Host 与创建时间的实体，不能合并到单个成员 run。D:_delete_rows 删除宏任务行，但同事务 append 的 macro_task.deleted 事件以无 task_id 的 payload 保留 macroTaskId 与 microTaskCount，属于删除后跨表冻结证据残留；ADR-028 第 7 条 2026-10-20 已定的墓碑和命令清理随 26→27 实现，不在此提出新选择。
<!-- END macro_tasks -->

<!-- BEGIN workflow_runs -->
## 31. workflow_runs

列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据
--- | --- | --- | --- | ---
run_id | W:_admit_helper_task/attach_micro_task | W:_run_row/compact，M:macro_task_list，D:_delete_rows | 键约束（PRIMARY KEY，tasks FK） | 当前 run_id 等于 task_id，但键兼作协议实体身份。
macro_task_id | W:_admit_helper_task/attach_micro_task | W:compact，M:macro_task_list/record_activity，D:_collect | 键约束（macro_tasks FK） | 可为空的分组归属。
title | W:attach_micro_task | W:compact，M:_run_presentation | 在用 | 独立微任务标题可与 spec.task 不同。
activity_seq | M:record_activity（初值 DDL DEFAULT） | M:macro_task_list/_summary，W:_owned_turn_context | 在用（索引投影，派生性未证） | record_activity 对每个有 task_id 的新事件：事件 task 是 run，或当时 decision_requests.task_id 对应 workflow_routes.run_id；从起点经当时 workflow_children.parent_run_id 与 workflow_lead_tasks.lead_run_id 递归上行，将 seq 写到所有 owners。当前 route task 指针/父链不能证明事件时关系，故不能仅用当前 lineage 上 MAX(events.seq) 替代；列表游标及 Lead changedSincePreviousTurn 依赖此值。
activity_at | M:record_activity | M:_summary | 在用（索引投影，派生性未证） | 对应上述被传播事件的 created_at；只有历史传播边和事件都可恢复时才可推出，不能用 run.updated_at 替代。
host_id | W:_admit_helper_task/attach_micro_task/takeover | W:_authorize/compact，M:_summary，blackboard/service/service.py:_waiting_for_host | 在用 | 当前 Host 会接管变更，非提交时冻结值。
owner_generation | W:_admit_helper_task/attach_micro_task/takeover | W:_authorize/_start_routing/compact，routing/router_boundary_data.py:_continuation_context | 在用 | owner fencing。
control_verifier | W:_admit_helper_task/attach_micro_task/takeover | W:_authorize | 在用 | 当前控制 capability 验证。
micro_task_json | W:_admit_helper_task/attach_micro_task | W:_turn_context/compact，M:_run_presentation | 重复/可推出（提交时冻结 spec） | 与 tasks.spec_json 在同一次提交使用相同 spec 的 canonical_json，后续均不改写；按 run_id=task_id 读取 tasks.spec_json 可保留原始证据，升级前须逐行比对。
micro_task_fingerprint | W:_admit_helper_task/attach_micro_task | W:compact/_micro_task_view | 重复/可推出 | 与 tasks.input_fingerprint 同次写入 schemas.spec_fingerprint(spec)，按 run_id=task_id 可直接读原存值；升级前逐行核对差异，保留原提交审计值。
request_fingerprint | W:_admit_helper_task/attach_micro_task | W:compact/submit_normalized | 在用 | 由 schemas.workflow_request_fingerprint 对 spec、executionWorkspace、hostId 及可选 presentation、buddyLocked、delegationAuthority、fixes 计算；其中 presentation 的原请求形式并未全存于 run 当前列，Host 变更后也不能重算原身份。
buddy_json | W:_create_helpers/_start_routing/attach_micro_task/continue_run/routing_settled | W:_buddy/compact/prepare_dispatch | 在用 | 当前 Buddy 会变，不能从封存 turn 取当前值。
buddy_revision | W:_create_helpers/attach_micro_task/continue_run/routing_settled（初值 DDL DEFAULT） | W:routing_settled/_turn_context/compact | 在用 | 当前选择代数。
validated_buddy_revision | W:_create_helpers/_start_routing/attach_micro_task/continue_run/prepare_dispatch/routing_settled | W:prepare_dispatch/claim_blocker | 在用 | 表示该 revision 已通过验证，不能单凭 buddy_revision 推出。
current_routing_id | W:_start_routing/continue_run | W:_routing_view/_turn_context/routing_settled | 在用 | 当前 routing decision；历史 routes 不能仅按时间替代 fencing。
submission_verifier | W:attach_micro_task | W:_recovered_control | 在用 | 可空的独立 token verifier。
execution_workspace_json | W:_admit_helper_task/attach_micro_task/continue_run | W:compact/continue_run/begin_turn | 在用 | 当前选择会改，区别封存 turn 的 workspace。
workspace_manifest_json | W:_admit_helper_task/attach_micro_task/prepare_continuation_workspace | W:_workspace_view/prepare_continuation_workspace/begin_turn，routing/router_boundary_data.py:_continuation_context | 在用 | 当前准备位置及状态可变；保留与 frozen turn input 的区别。
workspace_id | W:_admit_helper_task/attach_micro_task/prepare_continuation_workspace | W:_workspace_view/_allocation_provenance | 重复/可推出 | 同次从 workspace_manifest_json.workspaceId 提取；NULL manifest 对应 NULL。移除列改为 json_extract 后须保持 malformed/NULL 处理及索引查询一致。
workspace_manifest_sha256 | W:_admit_helper_task/attach_micro_task/prepare_continuation_workspace | W:_workspace_view/prepare_continuation_workspace | 重复/可推出 | 同次从 manifestSha256 提取；当前 manifest 更新时三处在同一事务更新，删除列需统一投影。
state | W:_accept_record/_admit_helper_task/_cancel_owned_run/_conclude_record/_record_request/_requeue/_routing_attention/_sync_boundary/attach_micro_task/decide_normalized/settle_turn/workspace_resolve | W:compact/claim_blocker/_authorize，M:macro_task_list | 在用 | 微任务状态机，不等同 tasks 执行状态。
active_request_id | W:_accept_record/_cancel_owned_run/_record_request/_requeue/_routing_attention/_sync_boundary/decide_normalized/workspace_resolve | W:_open_boundary/compact/_turn_context | 在用 | 当前打开的 Host 边界，不能按 created_at 猜测。
current_turn_id | W:_record_request/_record_system_request/begin_turn/settle_turn/workspace_resolve | W:settle_turn/_owned_turn_context | 在用 | 当前 turn 指针，可为已返回 turn。
current_attempt_id | W:_record_request/_record_system_request/begin_turn/settle_turn/workspace_resolve | W:settle_turn/_owned_turn_context | 在用 | 执行期指针，与 final_attempt_id 含义不同。
continuation_count | W:_requeue（初值 DDL DEFAULT） | W:compact/_start_routing | 在用（派生性未证） | _requeue 每次成功重排便加一；同一 continuation 可被拒绝再入队，单计 continuation 行数会漏掉次数。若要去列，须找到可重放且完整的每次 _requeue 事件映射与存量核对；当前没有此证据。
final_artifact_id | W:_accept_record/_requeue/settle_turn/workspace_resolve | W:compact/_owned_turn_context/_accept_record | 在用 | 当前交付物指针，历史 artifacts 可多项。
final_attempt_id | W:_accept_record/_requeue/settle_turn/workspace_resolve | W:compact/_owned_turn_context | 在用 | 当前最终执行指针。
revision | W:_accept_record/_admit_helper_task/_bump_run/_cancel_owned_run/_record_request/_requeue/_sync_boundary/attach_micro_task/begin_turn/decide_normalized/prepare_continuation_workspace/routing_settled/settle_turn/takeover | W:_authorize/_expect_revision/compact | 在用 | 乐观并发，不能用行更新时间代替。
created_at | W:_admit_helper_task/attach_micro_task | W:compact，M:macro_task_list/macro_task_timeline | 冻结证据（run 创建时刻） | 身份起点。
updated_at | W:_accept_record/_admit_helper_task/_bump_run/_cancel_owned_run/_record_request/_requeue/_routing_attention/_sync_boundary/attach_micro_task/begin_turn/decide_normalized/prepare_continuation_workspace/routing_settled/settle_turn/takeover/workspace_resolve | W:compact/claim_blocker | 在用 | 当前最近变更。
buddy_locked | W:attach_micro_task（初值 DDL DEFAULT） | W:compact/_buddy_matches_micro_task | 在用 | 用户 Buddy 选择约束。
delegation_authority | W:attach_micro_task（初值 DDL DEFAULT） | W:compact/_turn_context/settle_turn | 在用 | Lead 委派授权。

micro_task_json 的固定规范化键域由 normalize_workflow_spec 给出；normalize_spec 固定五键并仅在完整显式 Buddy 时添四键。execution_workspace_json 由 normalize_execution_workspace 拒绝未知键，其顶层是以下九键；base 内只允许 kind/ref。buddy_json 只允许四键。

键 | 写入者（产品函数） | 读者（产品函数） | 判断
--- | --- | --- | ---
micro_task_json.task | protocol/schemas.py:normalize_workflow_spec → workflow.py:attach_micro_task/_admit_helper_task | workflow.py:_turn_context/_start_routing；routing/decision.py:route_workflow；audit 整体读取 | 原提交 spec；与 tasks.spec_json 同值
micro_task_json.cwd | protocol/schemas.py:normalize_workflow_spec → workflow.py:attach_micro_task/_admit_helper_task | workflow.py:_turn_context/_start_routing；routing/decision.py:route_workflow；audit 整体读取 | 原提交 spec；与 tasks.spec_json 同值
micro_task_json.expectedSeconds | protocol/schemas.py:normalize_workflow_spec → workflow.py:attach_micro_task/_admit_helper_task | workflow.py:_turn_context/_start_routing；routing/decision.py:route_workflow；audit 整体读取 | 原提交 spec；与 tasks.spec_json 同值
micro_task_json.requiredCapabilities | protocol/schemas.py:normalize_workflow_spec → workflow.py:attach_micro_task/_admit_helper_task | workflow.py:_turn_context/_start_routing；routing/decision.py:route_workflow；audit 整体读取 | 原提交 spec；与 tasks.spec_json 同值
micro_task_json.exclusiveResources | protocol/schemas.py:normalize_workflow_spec → workflow.py:attach_micro_task/_admit_helper_task | workflow.py:_turn_context/_start_routing；routing/decision.py:route_workflow；audit 整体读取 | 原提交 spec；与 tasks.spec_json 同值
micro_task_json.harness | protocol/schemas.py:normalize_workflow_spec → workflow.py:attach_micro_task/_admit_helper_task | workflow.py:_turn_context/_start_routing；routing/decision.py:route_workflow；audit 整体读取 | 后四键仅完整 Buddy 约束时同存
micro_task_json.provider | protocol/schemas.py:normalize_workflow_spec → workflow.py:attach_micro_task/_admit_helper_task | workflow.py:_turn_context/_start_routing；routing/decision.py:route_workflow；audit 整体读取 | 后四键仅完整 Buddy 约束时同存
micro_task_json.model | protocol/schemas.py:normalize_workflow_spec → workflow.py:attach_micro_task/_admit_helper_task | workflow.py:_turn_context/_start_routing；routing/decision.py:route_workflow；audit 整体读取 | 后四键仅完整 Buddy 约束时同存
micro_task_json.effort | protocol/schemas.py:normalize_workflow_spec → workflow.py:attach_micro_task/_admit_helper_task | workflow.py:_turn_context/_start_routing；routing/decision.py:route_workflow；audit 整体读取 | 后四键仅完整 Buddy 约束时同存
execution_workspace_json.kind | protocol/schemas.py:normalize_execution_workspace（cwd/integrator 由 workflow 提交者补） → workflow.py:attach_micro_task/_admit_helper_task/continue_run | workflow.py:begin_turn/continue_run/_workspace_view；tasks/workspace.py:prepare/verify；audit 整体读取 | 当前工作区意图
execution_workspace_json.access | protocol/schemas.py:normalize_execution_workspace（cwd/integrator 由 workflow 提交者补） → workflow.py:attach_micro_task/_admit_helper_task/continue_run | workflow.py:begin_turn/continue_run/_workspace_view；tasks/workspace.py:prepare/verify；audit 整体读取 | 当前工作区意图
execution_workspace_json.accessNote | protocol/schemas.py:normalize_execution_workspace（cwd/integrator 由 workflow 提交者补） → workflow.py:attach_micro_task/_admit_helper_task/continue_run | workflow.py:begin_turn/continue_run/_workspace_view；tasks/workspace.py:prepare/verify；audit 整体读取 | 可选
execution_workspace_json.base | protocol/schemas.py:normalize_execution_workspace（cwd/integrator 由 workflow 提交者补） → workflow.py:attach_micro_task/_admit_helper_task/continue_run | workflow.py:begin_turn/continue_run/_workspace_view；tasks/workspace.py:prepare/verify；audit 整体读取 | 当前工作区意图
execution_workspace_json.includeUntracked | protocol/schemas.py:normalize_execution_workspace（cwd/integrator 由 workflow 提交者补） → workflow.py:attach_micro_task/_admit_helper_task/continue_run | workflow.py:begin_turn/continue_run/_workspace_view；tasks/workspace.py:prepare/verify；audit 整体读取 | 可选
execution_workspace_json.writeScope | protocol/schemas.py:normalize_execution_workspace（cwd/integrator 由 workflow 提交者补） → workflow.py:attach_micro_task/_admit_helper_task/continue_run | workflow.py:begin_turn/continue_run/_workspace_view；tasks/workspace.py:prepare/verify；audit 整体读取 | 可选
execution_workspace_json.targetRef | protocol/schemas.py:normalize_execution_workspace（cwd/integrator 由 workflow 提交者补） → workflow.py:attach_micro_task/_admit_helper_task/continue_run | workflow.py:begin_turn/continue_run/_workspace_view；tasks/workspace.py:prepare/verify；audit 整体读取 | 可选
execution_workspace_json.cwd | protocol/schemas.py:normalize_execution_workspace（cwd/integrator 由 workflow 提交者补） → workflow.py:attach_micro_task/_admit_helper_task/continue_run | workflow.py:begin_turn/continue_run/_workspace_view；tasks/workspace.py:prepare/verify；audit 整体读取 | 当前工作区意图
execution_workspace_json.integrator | protocol/schemas.py:normalize_execution_workspace（cwd/integrator 由 workflow 提交者补） → workflow.py:attach_micro_task/_admit_helper_task/continue_run | workflow.py:begin_turn/continue_run/_workspace_view；tasks/workspace.py:prepare/verify；audit 整体读取 | 当前工作区意图
buddy_json.harness | protocol/schemas.py:normalize_buddy → workflow.py:routing_settled/attach_micro_task/continue_run | workflow.py:_buddy/prepare_dispatch/begin_turn；buddy/harnesses/registry.py:adapter | 当前 Buddy 身份，四键成组
buddy_json.provider | protocol/schemas.py:normalize_buddy → workflow.py:routing_settled/attach_micro_task/continue_run | workflow.py:_buddy/prepare_dispatch/begin_turn；buddy/harnesses/registry.py:adapter | 当前 Buddy 身份，四键成组
buddy_json.model | protocol/schemas.py:normalize_buddy → workflow.py:routing_settled/attach_micro_task/continue_run | workflow.py:_buddy/prepare_dispatch/begin_turn；buddy/harnesses/registry.py:adapter | 当前 Buddy 身份，四键成组
buddy_json.effort | protocol/schemas.py:normalize_buddy → workflow.py:routing_settled/attach_micro_task/continue_run | workflow.py:_buddy/prepare_dispatch/begin_turn；buddy/harnesses/registry.py:adapter | 当前 Buddy 身份，四键成组
workflow_runs.micro_task_json 与 tasks.spec_json、micro_task_fingerprint 与 tasks.input_fingerprint 在 attach_micro_task/_admit_helper_task 同事务分别写同一 canonical_json(spec)、schemas.spec_fingerprint(spec)；原始内容仍由 tasks 行保留，升级前核对每一行差异。workspace_manifest_json 的精确 v1/v2/v3 顶层、读取与 digest 约束见本清点第 42 节 input/turn-input 联合类型；写者 tasks/workspace.py:_prepare_locked，当前 run 投影读者 workflow.py:_workspace_view/begin_turn/prepare_continuation_workspace，workspace.py:verify。workspace_id、workspace_manifest_sha256 分别与该 JSON 的 workspaceId、manifestSha256 同次写；不能从后来 turn 的 manifest 代替。macro_task_deletion.py:_delete_rows 按 run_id 删除本表。

本表承担 tasks 之外的 Host 治理、当前 Buddy、工作区和多 turn 指针；可合并的只是已指出的与 tasks 同值字段，整个表不能用 tasks 代替。
<!-- END workflow_runs -->

<!-- BEGIN workflow_routes -->
## 32. workflow_routes

列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据
--- | --- | --- | --- | ---
decision_id | W:_start_routing | workflow.py:_routing_view/routing_settled，routing/router_boundary_data.py:_continuation_context，macro_task_deletion.py:_delete_rows | 键约束（PRIMARY KEY，decision_requests FK） | 一次路由决策唯一连接。
run_id | W:_start_routing | workflow.py:routing_settled/_routing_history，macro_tasks.py:record_activity，macro_task_deletion.py:_delete_rows | 键约束（workflow_runs FK） | 一 run 可多次路由；decision_requests 本身无 run_id。
owner_generation | W:_start_routing | workflow.py:routing_settled/_routing_history，routing/router_boundary_data.py:_continuation_context | 冻结证据（该次路由申请时 owner generation） | 与当前 run generation 比较以 fence，历史不能回填。
state | W:_cancel_routes/_routing_attention/_start_routing/routing_settled | workflow.py:_routing_view/_routing_history，routing/router_boundary_data.py:routing_boundary | 在用 | 路由生命周期独立于 decision status。
created_at | W:_start_routing | workflow.py:_routing_history | 冻结证据（路由请求时刻） | 历史排序/显示。
updated_at | W:_cancel_routes/_routing_attention/_start_routing/routing_dispatched/routing_settled | 未见列级读取 | 无读/写（有写无语义读） | 在 src/hey_my_buddy 与 apps/console 搜索 route.updated_at、link.updated_at、r.updated_at、workflow_routes.*updated_at：仅 INSERT/UPDATE；路由历史显式 SELECT 不选此列。storage.py:_workspace_reference_rows 的 SELECT * 是保守位置引用扫描，非路由消费。

本表的历史 owner generation 和状态不能并入当前 workflow_runs：一次 run 可有多条 route 且旧连接须保留路由结果与 fencing 证据。索引 workflow_routes_run_idx 服务 run 历史及计数，未发现单独索引冗余结论。
<!-- END workflow_routes -->

<!-- BEGIN workflow_children -->
## 33. workflow_children

列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据
--- | --- | --- | --- | ---
child_task_id | W:_create_helpers | workflow.py:_child_view/child_settled，macro_tasks.py:macro_task_list/macro_task_timeline，macro_task_deletion.py:_delete_rows | 键约束（PRIMARY KEY，tasks FK） | helper 身份。
parent_run_id | W:_create_helpers | workflow.py:compact/_attention_chain/_terminal_ancestor，macro_tasks.py:macro_task_list/macro_task_timeline | 键约束（workflow_runs FK） | helper 所属 parent。
request_id | W:_create_helpers | workflow.py:_child_view，macro_task_summary_cache.py:dependencies | 在用 | 创建 helper 的批准请求身份。
decision_command_id | W:_create_helpers | macro_task_deletion.py:_delete_rows | 冻结证据（批准时命令） | 删除时关联清理；该命令可能涵盖多个 helper。
role | W:_create_helpers | workflow.py:_child_view，macro_task_summary_cache.py:dependencies | 在用 | helper/integrator 类别展示。
integrator | W:_create_helpers | workflow.py:_child_view，macro_task_summary_cache.py:dependencies | 在用 | 同次角色选择标志。
state | W:_cancel_owned_run/_create_helpers/_propagate_boundary/_resolve_helper_attention/child_settled/continue_run | workflow.py:_child_view/_unfinished_helpers，routing/router_boundary_data.py:_continuation_context | 在用 | helper 状态机。
auto_continue | W:_create_helpers | workflow.py:_child_view，macro_task_summary_cache.py:dependencies | 重复/可推出 | 只插入常量 0，无 UPDATE；投影可统一给 false；存量若非 0 须明确转换/拒绝差异，摘要 cache 键同步改变。
workspace_intent_json | W:_create_helpers | 未见字段级读取 | 无读/写（有写无语义读） | 在 src/hey_my_buddy 与 apps/console 搜索 workspace_intent_json：除 db.py DDL，仅 _create_helpers INSERT；storage.py:_workspace_reference_rows 的 SELECT * 递归保留扫描不是意图消费。原始 item.executionWorkspace 同时写入子 workflow_runs.execution_workspace_json；须核对存量差异和是否确为同次镜像。
workspace_manifest_json | W:_create_helpers | workflow.py:_unproven_checkout_holder/prepare_continuation_workspace，routing/router_boundary_data.py:_continuation_context | 冻结证据（helper 建立时准备 manifest） | 子 run 后续可续接、改当前 manifest；此列保存原 helper 绑定。
workspace_manifest_sha256 | W:_create_helpers | workflow.py:_child_view/_helper_outcomes | 重复/可推出（冻结镜像） | 同次从 workspace_manifest_json.manifestSha256 提取；在新行可按冻结 JSON 推出，但旧行不可重写，移列前须核对存量差异与查询消费。
created_at | W:_create_helpers | workflow.py:compact | 冻结证据（helper 建立时刻） | child 排序和视图。
updated_at | W:_cancel_owned_run/_create_helpers/_propagate_boundary/_resolve_helper_attention/child_settled/continue_run | workflow.py:_child_view | 在用 | helper 最新状态时间。
revision | W:_cancel_owned_run/_propagate_boundary/_resolve_helper_attention/child_settled/continue_run（初值 DDL DEFAULT 1） | 未见字段级读取 | 无读/写（有写无语义读） | 在 src/hey_my_buddy 与 apps/console 搜索 workflow_children.*revision、child[revision] 及显式 SELECT：只有 SET revision=revision+1 和默认值；macro_task_summary_cache.py 的 child 投影不含 revision。通用 row[revision] 访问是 workflow_runs 等表，未见 child revision 比较。

workspace_intent_json 的顶层严格由 protocol/schemas.py:normalize_execution_workspace 限定为 kind、access、accessNote、base、includeUntracked、writeScope、targetRef，再由 protocol/schemas.py:normalize_helpers 加入 cwd、integrator；base 的子键只允许 kind/ref。workflow.py:_create_helpers 写子行，_admit_helper_task 同批写 child workflow_runs.execution_workspace_json；子行九个顶层键均无字段级业务读者，只有 tasks/storage.py:_workspace_reference_rows 全行保守位置扫描。与 child run 值相等可由同次 item.executionWorkspace 证明，升级前仍须比对存量。workspace_manifest_json 由 tasks/workspace.py:_prepare_locked 的固定 v1/v2/v3 联合类型构造，所有顶层键及读取见第 42 节 input/turn-input；此列由 workflow.py:_create_helpers 冻结最初 helper 输入，workflow.py:_unproven_checkout_holder 仅取 checkoutRoot 等位置用于冲突保护，不能用之后 child run 的当前 manifest 替代。 macro_task_deletion.py:_delete_rows 同时按 parent_run_id 和 child_task_id 删除子行；未见此表删除后冻结证据残留。

本表的一 parent 可有多个 helper，保存批准来源与独立状态；不可整体并入 workflow_runs 或 workflow_requests。
<!-- END workflow_children -->

<!-- BEGIN workflow_lead_tasks -->
## 34. workflow_lead_tasks

列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据
--- | --- | --- | --- | ---
owned_run_id | W:attach_micro_task | workflow.py:compact/_owned_turn_context，macro_tasks.py:macro_task_list/macro_task_timeline，macro_task_deletion.py:_delete_rows | 键约束（PRIMARY KEY，workflow_runs FK） | 一个被 Lead 拥有的 run 仅有一条归属。
lead_run_id | W:attach_micro_task | workflow.py:compact/owned_event/_wake_lead，macro_tasks.py:macro_task_list/macro_task_timeline | 键约束（workflow_runs FK） | Lead 与被拥有 run 的独立实体连接。
lead_turn_id | W:attach_micro_task | workflow.py:compact，macro_task_deletion.py:_delete_rows | 键约束（workflow_turns FK）；冻结证据（授权创建时 turn） | 当前 Lead turn 会继续变化，此值表明当时由谁创建。
created_at | W:attach_micro_task | workflow.py:compact | 冻结证据（Lead 归属创建时刻） | 历史展示。

本表是一对多 Lead 归属事实，不能合并到单个 Lead run；并入 owned run 会让归属查询与建立时 turn 证据耦合到当前 run 状态。macro_task_deletion.py:_delete_rows 按 lead/owned/turn 任一删除作用域清理。
<!-- END workflow_lead_tasks -->

<!-- BEGIN workflow_continuations -->
## 35. workflow_continuations

列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据
--- | --- | --- | --- | ---
continuation_id | W:_resolve_helper_attention/_wake_lead/continue_run/decide_normalized | workflow.py:prepare_continuation_workspace/begin_turn/_audit，macro_task_deletion.py:_delete_rows | 键约束（PRIMARY KEY） | 一次续接授权身份。
run_id | W:_resolve_helper_attention/_wake_lead/continue_run/decide_normalized | workflow.py:_requeue/begin_turn/_audit，macro_task_deletion.py:_delete_rows | 键约束（workflow_runs FK） | 所属 run。
command_id | W:_resolve_helper_attention/continue_run/decide_normalized | macro_task_deletion.py:_delete_rows | 冻结证据（创建续接的命令） | lead-wakeup 无 Host 命令。
kind | W:_resolve_helper_attention/_wake_lead/continue_run/decide_normalized | workflow.py:_turn_context/_audit/_wake_lead | 在用 | manual/helper/lead-wakeup 语义不同，两个部分唯一索引依赖它。
request_id | W:_resolve_helper_attention/decide_normalized | workflow.py:_audit；db.py:workflow_continuations_auto_unique | 键约束（run_id,request_id WHERE kind=helper 的唯一索引） | 绑定批准来源。
trigger_event_json | W:_wake_lead | workflow.py:_turn_context | 冻结证据（唤醒时事件） | 顶层 seq、kind、runId、state 来自当时 events/owned run，下一 turn 用作 trigger。
expected_revision | W:_resolve_helper_attention/_wake_lead/continue_run/decide_normalized | workflow.py:_audit | 冻结证据（授权时 run revision） | 目前实际消费只在审计；没有证据可由之后 revision 反推当时值。
input_text | W:_resolve_helper_attention/_wake_lead/continue_run/decide_normalized | workflow.py:_turn_context/_audit/_failed_turn_input，routing/decision.py:route_workflow | 冻结证据（Host 或自动续接输入） | 当次内容须保留。
input_bytes | W:_resolve_helper_attention/_wake_lead/continue_run/decide_normalized | 未见字段级读取 | 重复/可推出且有写无语义读 | 创建时取 len(input_text.encode('utf-8'))，可由 input_text 再算；在 src/hey_my_buddy 与 apps/console 搜索 SELECT input_bytes、row[input_bytes]、continuation[input_bytes] 均无命中，rg input_bytes 仅局部变量及 INSERT/DDL。
note | W:_resolve_helper_attention/_wake_lead/continue_run/decide_normalized | workflow.py:_turn_context/_audit | 在用 | 与 input_text 不总相同，保留 Host note。
helper_policy | W:_resolve_helper_attention/continue_run/decide_normalized | workflow.py:_audit | 冻结证据（创建续接时 cancel/keep 选择） | cancel/keep 在 continue_run 当次由本地 policy 执行，存列仅供后来审计；不能从后续 helper 状态推出原 Host 选择。
helper_outcomes_json | W:_resolve_helper_attention/child_settled/continue_run/decide_normalized | workflow.py:_audit/_turn_context，routing/decision.py:route_workflow | 冻结证据（续接形成时 helper 结果） | 后续 child 会变，不能按当前 children 重构。
workspace_manifest_json | W:prepare_continuation_workspace | workflow.py:begin_turn/_turn_workspace/prepare_continuation_workspace | 冻结证据（此续接准备好的工作区） | 初始 NULL，消费时用于当次 turn，不等同随后 run 当前 manifest。
state | W:_cancel_owned_run/_requeue/_resolve_helper_attention/_wake_lead/begin_turn/child_settled/continue_run/decide_normalized/prepare_continuation_workspace/settle_turn | workflow.py:prepare_continuation_workspace/begin_turn/_audit | 在用 | one-use 权限状态，唯一待唤醒索引依赖。
attempt_id | W:begin_turn/settle_turn | workflow.py:_audit，macro_tasks.py:_run_spans | 冻结证据（消费续接的 attempt） | 拒绝 turn 可清回 NULL 后重试。
turn_id | W:begin_turn/settle_turn | workflow.py:_audit/settle_turn | 冻结证据（消费续接的 turn） | 被拒绝可清回 NULL。
created_at | W:_resolve_helper_attention/_wake_lead/continue_run/decide_normalized | workflow.py:_audit/begin_turn，macro_tasks.py:_run_spans | 冻结证据（续接记录时刻） | 排队次序和跨度。
consumed_at | W:begin_turn/settle_turn | workflow.py:_audit | 冻结证据（成功消费时刻） | 被拒绝重试会清回 NULL。

trigger_event_json 的顶层 seq、kind、runId、state 全由 workflow.py:_wake_lead 从当时 events 和 owned run 构造，workflow.py:_turn_context 解码整个对象作为下一 turn 的 trigger；seq 是事件身份，state 是唤醒时状态，不能用当前 run 状态补。helper_outcomes_json 是数组，初始 [] 由 workflow.py:decide_normalized/_resolve_helper_attention/continue_run 写入，child_settled 在自动续接进入 queued 时固定 _helper_outcomes 的快照；workflow.py:_audit/_turn_context 与 routing/decision.py 消费。每个元素的固定顶层 taskId、role、state、taskState、integrator、disposition、summary、remaining、attemptId、generation、workspaceManifestSha256 由 workflow.py:_helper_outcomes 构造；可选 workspace（workspaceId、path、checkoutId、manifestSha256）和 artifact（artifactId、attemptId、turnId、inputCommit、outputCommit、snapshotSha256、diffPath、diffSha256、changedPaths，partial-output 另有 kind、partial、verified、final）同函数构造。_turn_context 在封存 turn 中使用整个数组；后续 child 改动不能回算它。workspace_manifest_json 的顶层和验证见第 42 节 input/turn-input，writer tasks/workspace.py:_prepare_locked → workflow.py:prepare_continuation_workspace，reader workflow.py:begin_turn/_turn_workspace；它是本次续接准备状态。 macro_task_deletion.py:_delete_rows 按 run_id 删除续接；当前无本表删除后冻结证据残留。

本表保存可排队、失效和消费的一次性续接授权，同一 run 有历史多条；不能并入 workflow_runs.continuation_count，两个部分唯一索引分别约束 helper 请求与待处理 Lead 唤醒。
<!-- END workflow_continuations -->

<!-- BEGIN workspace_reservations -->
## 36. workspace_reservations

列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据
--- | --- | --- | --- | ---
reservation_id | W:_reserve | workflow.py:_reserve/_audit，macro_task_deletion.py:_delete_rows | 键约束（PRIMARY KEY） | 历史一次占用身份。
workspace_id | W:_reserve/prepare_continuation_workspace | workflow.py:_audit/_reserve，macro_task_deletion.py:_other_holders | 在用 | 创建时 manifest.workspaceId 或 checkoutId；续接可更新，且 allocation_references 保守扫描，不能由当前 run manifest 推历史值。
checkout_id | W:_reserve | workflow.py:_held_writer/_reserve/_reclaim_manual_workspace，routing/router_boundary_data.py:_continuation_context | 键约束（held/write 部分唯一索引） | checkout writer 排他。
repository_id | W:_reserve | workflow.py:_reserve/_reclaim_manual_workspace，routing/router_boundary_data.py:_continuation_context | 在用 | 身份三元组核对。
path | W:_reserve | workflow.py:_unproven_checkout_holder/_reclaim_manual_workspace，routing/router_boundary_data.py:_continuation_context | 在用 | checkout 路径冲突证明。
access | W:_reserve/prepare_continuation_workspace | workflow.py:_reserve/prepare_continuation_workspace，routing/router_boundary_data.py:_continuation_context | 键约束（held/write 部分唯一索引） | read 可共享，write 排他；续接可变。
holder_task_id | W:_reserve | workflow.py:_reserve/_release_reservations/prepare_continuation_workspace，macro_task_deletion.py:_delete_rows | 键约束（tasks FK） | 当前及历史 holder。
holder_kind | W:_reserve | workflow.py:_audit | 冻结证据（申请时 parent/helper/reader 身份） | holder 所属角色事后可变，audit 显示。
parent_run_id | W:_reserve | workflow.py:_audit，macro_task_deletion.py:_delete_rows | 键约束（workflow_runs FK） | helper 占用归属 parent；删除与审计查询。
manifest_sha256 | W:_reserve/prepare_continuation_workspace | workflow.py:_reserve/_audit | 在用 | read 共享要求同 digest；续接时同事务更新，不能从当前 run 推历史占用。
state | W:_release_reservations/_reserve/_settle_helper_reservation | workflow.py:_reserve/prepare_continuation_workspace/_audit，routing/router_boundary_data.py:_continuation_context | 键约束（held/write 部分唯一索引） | held/transferred/released 生命周期。
created_at | W:_reserve | workflow.py:_audit（排序） | 冻结证据（占用开始时刻） | 历史顺序。
updated_at | W:_release_reservations/_reserve/_settle_helper_reservation/prepare_continuation_workspace | 未见字段级读取 | 无读/写（有写无语义读） | 在 src/hey_my_buddy 与 apps/console 搜索 workspace_reservations.*updated_at、reservation[updated_at]、当前 row[updated_at] 的占用上下文：仅 INSERT/UPDATE；_audit 明确只投影 createdAt/releasedAt。storage.py:_workspace_reference_rows 的 SELECT * 仅作保守位置引用扫描。
released_at | W:_release_reservations/_reserve/_settle_helper_reservation | workflow.py:_audit | 冻结证据（释放时刻） | transferred 时 NULL，重新持有清空。

本表有独立的占用历史及排他约束，不能合并到 run 或 manifest。workspace_id 与 manifest.workspaceId 有初始同源关系，但 _reserve 有 checkoutId 回退，续接可变；其他保留事实也可能引用旧值，现有证据不足以认定该列冗余。macro_task_deletion.py:_delete_rows 按 holder_task_id 或 parent_run_id 删除；当前路径未发现本表删除后冻结证据残留。
<!-- END workspace_reservations -->

<!-- BEGIN workflow_scope_versions -->
## 37. `workflow_scope_versions`

表仍需要，记录每次授权的历史版本，不能并进只有当前状态的 `workflow_runs`。本节 `WF` 指 `src/hey_my_buddy/blackboard/tasks/workflow.py:WorkflowCoordinator`，`CR` 指 `src/hey_my_buddy/blackboard/tasks/command_receipts.py:replay_response`；其他节沿用。删除由 `macro_task_deletion.py:_delete_rows` 按 `run_id` 同事务处理。

| 列 | 写入者 | 读者 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `run_id` | `WF.attach_micro_task/_admit_helper_task/scope_amend` | `WF._current_scope`、`CR`、删除路径 | 例外：键或约束需要 | 主键 `(run_id,scope_version)`、外键 `workflow_runs`。 |
| `scope_version` | 同上；首次 1，后续加 1 | `WF._current_scope/_scope_precheck`、`CR` | 例外：键或约束需要 | 同一 run 的授权序号，不能以 `revision` 替代。 |
| `command_id` | `WF.scope_amend`；初次为 NULL | `macro_task_deletion.py:_delete_rows` | 在用 | 删除时找出授权命令；它是一次动作的身份。 |
| `actor_role` | 三个插入路径 | `WF._current_scope` | 例外：冻结的证据 | 本次授权由谁作出，另有角色 CHECK。 |
| `actor_id` | 三个插入路径 | `WF._current_scope` | 例外：冻结的证据 | 授权时的操作者，不随接管变更。 |
| `note` | `WF.scope_amend`；初次 NULL | `WF._current_scope` | 例外：冻结的证据 | 记录当次追加授权的说明。 |
| `write_scope_json` | 三个插入路径 | `WF._current_scope`、`CR` | 例外：冻结的证据 | 当次批准的路径数组；原始输入及此前版本不变。 |
| `manifest_sha256` | 三个插入路径 | `WF._current_scope`、`CR` | 例外：冻结的证据 | 该授权看到的输入清单身份，不是后来最新清单。 |
| `stopped_evidence_json` | 三个插入路径 | 无领域读取；`storage.py:_allocation_reference_sets` 泛扫 | 例外：冻结的证据 | 初次授权的来源，或追加授权时整条 lineage 的停止证明，不能用后来新增/恢复的子任务重算。 |
| `created_at` | 三个插入路径 | `WF._current_scope` | 例外：冻结的证据 | 授权版本形成时刻，投影为 `updatedAt`。 |

JSON：`write_scope_json` 是字符串数组，没有对象顶层键。`stopped_evidence_json` 的来源和消费如下；泛扫只用于保守回收引用，不解释停机语义。

| 顶层键 | 写入者 | 读者 | 判断 |
| --- | --- | --- | --- |
| `kind` | `WF.attach_micro_task`=`initial`；`_admit_helper_task`=`helper` | 无领域读者 | 初次授权的冻结来源。 |
| `parentRunId` | `WF._admit_helper_task` | 仅存储引用泛扫 | helper 初次授权的父任务证据。 |
| `selfConfirmed`、`descendantsConfirmed`、`unconfirmedRunIds`、`unconfirmedCount`、`truncated` | `WF.scope_amend` 原样存 `WF.shutdown_summary` | 无领域读者；ID 可被泛扫 | 均为授权时的停止证据，不可换成当前状态。 |

检索 `rg -n 'stopped_evidence_json' src/hey_my_buddy apps/console/src`：只见 DDL 与上述三处 INSERT；再读 `_current_scope`、`CR` 和 `storage.py:_allocation_reference_sets`，确认不存在结构化读取。保留依据是明确的授权证据，不是预留功能。`workflow_scope_versions_run_idx(run_id,scope_version)` 与主键自动索引完全重复，可删；`_current_scope` 的倒序 LIMIT 与 `CR` 的等值查询均能用主键索引。
<!-- END workflow_scope_versions -->

<!-- BEGIN workflow_workspace_conflicts -->
## 38. `workflow_workspace_conflicts`

表需要：封存被拒的现场及其后续 Host 处理与原始失败 attempt 是不同事实；不能把失败结果改成成功结果。`WS` 指 `src/hey_my_buddy/blackboard/tasks/workspace.py`。删除由 `macro_task_deletion.py:_delete_rows` 按 run 处理。

| 列 | 写入者 | 读者 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `conflict_id` | `WF._record_scope_conflicts` | `WF._conflict_row/_conflict_view/workspace_resolve`、`CR` | 例外：键或约束需要 | 主键与 Host 处置身份。 |
| `run_id` | 同上 | `WF._open_conflict/_conflict_row/_resolution_delivery`、删除路径 | 例外：键或约束需要 | 外键；唯一键 `(run_id,attempt_id,observed_fingerprint)`。 |
| `attempt_id` | 同上 | `WF._resolution_native_completion/_resolution_delivery/_conflict_view` | 例外：键或约束需要 | 唯一键绑定原始失败执行。 |
| `turn_id` | 同上 | `WF._conflict_manifest/_resolution_native_completion/_conflict_view` | 例外：冻结的证据 | 现场所属实际 turn，后来当前 turn 可改变。 |
| `manifest_sha256` | 同上，从 turn 输入取 | `WF._conflict_manifest/_conflict_view` | 重复 | 等于 `evidence_json.manifestSha256`；同一次 INSERT 复制，比较读者只要求相等，无差异语义。 |
| `observed_fingerprint` | 同上 | `WF._record_scope_conflicts/_conflict_view` | 例外：键或约束需要 | 唯一键的一部分；JSON 有副本，但该列承担唯一性。 |
| `blocking_paths_json` | 同上，从 record 复制 | `WF._conflict_view/_conclude_precheck/_record_scope_attention` | 重复 | 是 `evidence_json.blockingPaths` 的 canonical JSON；INSERT 同步，之后不改。 |
| `evidence_json` | 同上，来自 `WS.scope_conflicts` | `WF._conflict_view`、存储引用泛扫 | 例外：冻结的证据 | 封存失败时授权内容与实际内容的现场；物理记录只写一次。 |
| `state` | 初始 `open`；`WF.workspace_resolve` | `WF._open_conflict/_resolution_delivery/workspace_resolve/_conflict_view` | 在用 | restore 可部分完成，不能只从 action 判定是否关闭。 |
| `action` | `WF.workspace_resolve` | `WF.workspace_resolve/_resolved_duplicate/_conflict_view` | 例外：冻结的证据 | 最近一次 Host restore/adopt/abandon 选择；与 state 不一一对应。 |
| `resolved_paths_json` | 初始 `[]`；`WF.workspace_resolve` | `WF._conflict_view/_resolved_duplicate` | 例外：冻结的证据 | 本次实际恢复/采纳路径；不能用最初 blockingPaths 替代。 |
| `conflicting_paths_json` | 初始 `[]`；`WF.workspace_resolve` 成功或 CAS 失败 | `WF._conflict_view/_resolved_duplicate` | 在用 | 最近的剩余路径或冲突路径，现场后续可改变。 |
| `artifact_id` | `WF.workspace_resolve` | `WF._resolution_delivery/_resolved_duplicate/_conflict_view` | 在用 | 指向本次处置的不可变产物；参与查询筛选。 |
| `output_commit` | `WF.workspace_resolve` 从产物 record 复制 | `WF._conflict_view/_resolved_duplicate` | 重复 | `artifact_id` 联结 `workflow_artifacts.manifest_json.commit`；无产物时 NULL，同事务维护。 |
| `output_tree` | 同上 | 同上 | 重复 | 同上取 `.tree`；无读者允许与产物不同。 |
| `actor_role` | `WF.workspace_resolve` | `WF._conflict_view/_resolved_duplicate` | 例外：冻结的证据 | 处置时操作者；配对 CHECK。 |
| `actor_id` | 同上 | 同上 | 例外：冻结的证据 | 处置时操作者身份。 |
| `note` | 同上 | 同上 | 例外：冻结的证据 | adopt/abandon 的显式理由。 |
| `command_id` | 同上 | 删除路径 | 在用 | 定位最近的处置命令，历史部分 restore 的独立事实另在 commands。 |
| `delivery_json` | `WF.workspace_resolve` | `WF._resolution_delivery_view/_resolution_delivery_result/_resolution_delivery`、`CR` | 例外：冻结的证据 | Host 处理现场后形成的交付，独立于原始失败 attempt/turn。 |
| `created_at` | `WF._record_scope_conflicts` | `WF._open_conflict/compact/_conflict_view` | 例外：冻结的证据 | 现场入库时刻及排序。 |
| `updated_at` | `WF._record_scope_conflicts/workspace_resolve` | `WF._conflict_view` | 在用 | CAS 失败也会更新，不等于交付时刻。 |

`resolved_paths_json`、`conflicting_paths_json`、`blocking_paths_json` 是字符串数组。`evidence_json` 顶层来自 `WS._scope_evidence/_record_scope_evidence`，完整拷贝物理失败现场，不能改写物理原件；若删除数据库中的重复副本，读取物理原件及绑定身份的算法必须维持。

| JSON 顶层键 | 写入者 | 读者 | 判断 |
| --- | --- | --- | --- |
| `version` | `WS._scope_evidence` 经 `WF._record_scope_conflicts` | 无领域逐键读；物理原件原样保存 | 现场形式的冻结证据。 |
| `workspaceId` | 同上 | `WS._scope_records` 读取物理原件；数据库仅泛扫 | 冻结现场身份。 |
| `manifestSha256` | 同上 | `WS._scope_records`；数据库列副本被 `_conflict_manifest` 读取 | 与列重复。 |
| `observedFingerprint` | 同上 | `WF._record_scope_conflicts` 插入去重；数据库列承担 UNIQUE | 与唯一键列重复；原件身份不改。 |
| `writeScope` | 同上 | 数据库无领域读者；`WS.resolve` 使用原清单/现场 | 冻结失败现场当时的授权；不能以当前 scope 替换。 |
| `blockingPaths` | 同上 | `WS.resolve` 的物理原件；数据库列投影 | 与 `blocking_paths_json` 重复。 |
| `changedPaths` | 同上 | 数据库无领域读者 | 冻结失败现场的受管变更。 |
| `entries` | 同上 | `WF._conflict_view`；`WS.resolve` 读取物理原件进行 CAS | 有效内容证据，含每路径授权/观察/索引状态。 |
| `taskId`、`attemptId` | `WS._record_scope_evidence` | `WS._scope_records` 校验原件；数据库对应 run/attempt 列 | 数据库层重复身份，物理原件绑定必须保留。 |
| `delivery_json.state` | `WF.workspace_resolve`=`delivered` | `WF._resolution_delivery_view/_resolution_delivery_result` | 可由 delivery 非 NULL 推出；当前无其他写值。 |
| `delivery_json.kind` | 同上=`host-resolution` | `WF._resolution_delivery_result` | 常量，可按记录种类推出。 |
| `delivery_json.action` | 同上 | `WF._resolution_delivery_result` | 与 `action` 相同，同事务复制。 |
| `delivery_json.artifactId` | 同上 | `WF._resolution_delivery` 校验及 `_resolution_delivery_result` | 与 `artifact_id` 相同，同事务复制，读者要求一致。 |
| `delivery_json.attemptId`、`delivery_json.turnId` | 同上 | `WF._resolution_delivery_result` | 与本表身份列相同。 |
| `delivery_json.turnOutcome` | 同上=`completed` | `WF._resolution_delivery_result` | 已重新验证原始 native completed 才写，当前恒定。 |
| `delivery_json.resolvedFingerprint` | 同上 | 领域投影只透传；`_resolution_delivery_result` 不读 | 处置时现场摘要证据；后续现场可变，不能用当前观察重算。 |
| `delivery_json.commandId` | 同上 | 仅完整 delivery 透传与泛扫 | 与 `command_id` 相同。 |
| `delivery_json.deliveredAt` | 同上 | `WF._resolution_delivery_result` | Host 交付时刻；本表 `updated_at` 不是稳定替代。 |

重复项的推导都是 JSON 取键或通过 `artifact_id` 等值联结，不重读现存检出、不重新封存。`workspace_resolve` 在同一事务写两份，`_resolution_delivery` 反而拒绝不一致，没有读者依赖差异。`workflow_workspace_conflicts_run_idx(run_id,state)` 服务 open 查找、run 列表与删除，不列为无用索引。
<!-- END workflow_workspace_conflicts -->

<!-- BEGIN workflow_integrations -->
## 39. `workflow_integrations`

表需要，记录验收时真实目标的整合结果或显式 `not-required`，不能从验收之后的 Git HEAD 重算。列与 `verification_json` 的同次副本可合并；不建议把独立目标证据并进执行产物。删除由 `macro_task_deletion.py:_delete_rows` 按 run 处理。

| 列 | 写入者 | 读者 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `integration_id` | `WF._accept_record` | `WF._find_integration/_integration_view`、`CR` | 例外：键或约束需要 | 主键；结论表外键引用。 |
| `run_id` | 同上 | `WF._find_integration/compact/_accept_record`、删除路径 | 例外：键或约束需要 | 外键，限定归属。 |
| `artifact_id` | 同上 | `WF._find_integration/_integration_view/_accept_record` | 例外：冻结的证据 | 当次接受的固定产物，不能用最新 `final_artifact_id` 替代。 |
| `attempt_id` | 同上，从 artifact 复制 | `WF._integration_view` | 重复 | `workflow_artifacts[artifact_id].attempt_id`；同事务选定且产物不更新，无差异语义。 |
| `state` | 同上，只有 `verified/not-required` | `WF._find_integration/_accept_conflicts_with_recorded/_integration_view` | 可推出 | `strategy=='not-required'` 则 `not-required`，否则 `verified`；DDL 的 `conflict` 无产品写者。 |
| `strategy` | 同上 | `WF._integration_view` | 重复 | verified 行等于 `verification_json.strategy`；not-required 行为常量；同事务复制。 |
| `binding_sha256` | 同上 | `WF._accept_record` 查询去重 | 例外：键或约束需要 | UNIQUE；canonical binding（产物、策略、目标 before/after、Host 路径等）的摘要。 |
| `target_kind` | 同上，checkout/not-required | `WF._integration_view` | 可推出 | not-required 时常量，否则 `verification_json.target.kind`；无差异依赖。 |
| `target_path` | 同上 | `WF._accept_conflicts_with_recorded/_integration_view` | 重复 | `verification_json.target.path`，not-required 则空串。 |
| `target_ref` | 同上 | 同上 | 重复 | `verification_json.target.ref`，not-required 则空串。 |
| `target_repository_id` | 同上 | `WF._integration_view` | 重复 | `verification_json.target.repositoryId`，not-required 则 NULL。 |
| `target_checkout_id` | 同上 | `WF._integration_view` | 重复 | `verification_json.target.checkoutId`，not-required 则 NULL。 |
| `source_commit` | 同上 | `WF._find_integration/_integration_view` | 重复 | `verification_json.sourceCommit`，not-required 则 NULL；通常也等于固定 artifact.commit。 |
| `source_tree` | 同上 | `WF._integration_view` | 重复 | `verification_json.sourceTree`，not-required 则 NULL。 |
| `before_commit` | 同上 | `WF._integration_view` | 重复 | `verification_json.beforeCommit`，记录当时目标，不可从当前 ref 推出。 |
| `after_commit` | 同上 | `WF._integration_view` | 重复 | `verification_json.afterCommit`，同次证据副本。 |
| `before_tree` | 同上 | `WF._integration_view` | 重复 | `verification_json.beforeTree`；不依赖以后 Git 对象是否仍可读取。 |
| `after_tree` | 同上 | `WF._integration_view` | 重复 | `verification_json.afterTree`。 |
| `verification_json` | `WF._accept_record`，来自 `WS.integration_verify`、`WF._accept_verify` | `WF._integration_view`、存储引用泛扫 | 例外：冻结的证据 | 验收时目标、逐路径差异与 Host 调整；包含待剪重复键，见下。 |
| `note` | `WF._accept_record`，只存 not-required 理由 | `WF._accept_conflicts_with_recorded/_integration_view` | 例外：冻结的证据 | 不需要整合的显式决定；verified 行说明另在 `verification_json.note`，二者不应误合。 |
| `command_id` | 同上 | 删除路径 | 在用 | 定位验收命令。 |
| `actor_role` | 同上 | `WF._integration_view` | 例外：冻结的证据 | 验收当时的操作者；角色 CHECK。 |
| `actor_id` | 同上 | `WF._integration_view` | 例外：冻结的证据 | 同上。 |
| `created_at` | 同上 | `WF._find_integration/compact/_integration_view` | 例外：冻结的证据 | 验证形成时刻与历史排序。 |

JSON 顶层，以下 writer 均为 `WS.integration_verify` → `WF._accept_verify` → `WF._accept_record`；`not-required` 存 `{}`。`WF._integration_view` 对完整 JSON 作有界透传，`apps/console/src/task-detail/parse.ts:parseIntegration` 消费 `summary/hostPaths`；其余也作为 API 证据返回，不把前端暂未画出误判为无人读。

| 顶层键 | 写入者 | 读者 | 判断 |
| --- | --- | --- | --- |
| `verified` | 上述 writer | `WF._accept_verify` 入库前检查；`_integration_view` 透传 | 成功入库恒 true；state 已表达，可删除存值并重建投影。 |
| `strategy` | `WF._accept_verify` 改为 merge/patch | `_integration_view` 透传，列读者见上 | 与 `strategy` 列重复。 |
| `target` | `WS.integration_verify` | `_integration_view` 透传，列读者见上；存储引用泛扫 | 与五个 target 列重复。 |
| `beforeCommit`、`beforeTree`、`afterCommit`、`afterTree` | 同上 | `_integration_view` 透传及相应列投影 | 各与对应列重复。 |
| `sourceCommit`、`sourceTree` | 同上 | 同上 | 各与对应列重复。 |
| `artifactAncestor` | 同上 | `WF._accept_verify` 选策略；`_integration_view` 透传 | 当前 `strategy=='merge'` 可推出该布尔值，存值无差异语义。 |
| `matchingPaths`、`differingPaths`、`missingPaths` | 同上 | `_accept_verify` 检查，`_integration_view` 透传 | 验证当时的逐路径证据；以后目标变化不可重算。 |
| `adjustments` | 同上 | `_integration_view` 透传 | `_confirm_adjustments=True` 时为 differingPaths.path 与 missingPaths 的排序去重并集；普通成功行两者为空，故为可推出副本。 |
| `hostPaths` | 同上 | `_accept_record` binding 来源、`parseIntegration` | Host 在真实 before/after 区间添加的路径证据。 |
| `unrecordedPaths` | 同上 | `_accept_verify` 检查；`_integration_view` 透传 | 合法入库恒为空数组；有未确认差异时拒绝入库。 |
| `note` | 同上 | `_integration_view` 透传 | verified 的 Host 调整说明，不能由表 `note` 推出，后者是 not-required 理由。 |
| `summary` | `WF._accept_verify`=`""` | `parseIntegration` 去空白后变 NULL | 只有空串写者，没有有效内容写者，可删并投影为空。 |

上述重复的保持方式只有 `_accept_record` 一次 INSERT 同时复制，没有触发器；读者要求同一事实，不依赖差异。可选择保留列并剔除 JSON 中的副本，或保留 JSON 并将列读者改为取键；必须只选一份权威存储。`binding_sha256` 的唯一性不可随重复字段一起删。检索 `rg -n 'workflow_integrations|verification_json|unrecordedPaths|summary' src/hey_my_buddy apps/console/src` 并逐读 `_accept_verify/_accept_record`，确认没有 `state='conflict'` 的产品写路径，summary 只有空值写路径；测试人工构造不构成产品 writer。`workflow_integrations_run_idx(run_id,artifact_id,created_at)` 支持固定产物查找、run 列表前缀与删除，不列为无用。
<!-- END workflow_integrations -->

<!-- BEGIN workspace_cleanup_plans -->
## 40. `workspace_cleanup_plans`

表需要，`planned→applying→applied/blocked` 是物理删除的恢复日志；不能仅从当前检出是否存在推出整次清理是否完成。已应用的清理证据描述仍然存在的任务及其清理动作，正常随任务删除，而不在删检出时清空。

| 列 | 写入者 | 读者 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `plan_id` | `WF.cleanup_plan` | `WF.cleanup_apply/_plan_view`、`CR` | 例外：键或约束需要 | 主键与恢复身份。 |
| `run_id` | 同上 | `WF.cleanup_plan/cleanup_apply/compact/_cleanup_fence`、删除路径 | 例外：键或约束需要 | run 外键与归属。 |
| `workspace_id` | 同上，物理 allocation 身份 | `WF._plan_view` | 例外：冻结的证据 | 清理对象的物理 allocation，可能与最新逻辑清单 workspaceId 不同。 |
| `checkout_id` | 同上 | `WF._reserve/_cleanup_fence/cleanup_plan/_plan_view` | 例外：冻结的证据 | 正在删除的检出身份，恢复时作围栏。 |
| `repository_id` | 同上 | `WF._plan_view/cleanup_apply` | 例外：冻结的证据 | 同一物理对象身份；result 中另有重复。 |
| `path` | 同上 | `WF.cleanup_apply/_cleanup_fence/_reserve/_plan_view` | 例外：冻结的证据 | 必须与 confirmPath 完全一致，不能改用后来 manifest 路径。 |
| `kind` | 同上 | `WF._plan_view` | 例外：冻结的证据 | 本次物理 allocation 种类。 |
| `state` | `WF.cleanup_plan/cleanup_apply/_requeue` | `WF.cleanup_apply/_cleanup_apply_precheck/_cleanup_fence/_reserve/_plan_view` | 在用 | applying 真实围栏不可用目录存在性替代。 |
| `evidence_json` | `WF.cleanup_plan/cleanup_apply` | `WF._plan_view/cleanup_apply`、存储引用泛扫 | 例外：冻结的证据 | plan/apply 当时的接受、停止、整合与现场证据；开始 applying 后不可改写。 |
| `retention_json` | 同上 | `WF._plan_view/cleanup_apply` | 例外：冻结的证据 | 本次清理承诺保留的产物/引用/目录；不等于未来的任务全量产物。 |
| `reasons_json` | 同上，`WF._requeue` 写 continued | `WF._plan_view` | 在用 | 有独立于 `evidence.workspace.reasons` 的任务前置失败及继续失效原因。 |
| `result_json` | `WF.cleanup_apply` | `WF._plan_view`、`CR` | 例外：冻结的证据 | 实际清理结果，其中若干顶层键重复，见下。 |
| `command_id` | `WF.cleanup_plan/cleanup_apply` | 删除路径 | 在用 | 清理动作命令关联。 |
| `actor_role` | `WF.cleanup_plan` | 无领域读者；存储泛扫字符串 | 没有读者 | `_plan_view` 不返回 actor；检索与消费核对见下。 |
| `actor_id` | 同上 | 同上 | 没有读者 | 没有清理 actor 审计投影；若作为独立历史证据保留须明确其已有证据目的，当前不靠它作判断。 |
| `revision` | `WF.cleanup_plan/cleanup_apply` | `WF._plan_view` | 在用 | 对外暴露计划版本；不与 run revision 同步。 |
| `created_at` | `WF.cleanup_plan` | `WF._plan_view` | 例外：冻结的证据 | 计划创建时刻。 |
| `applied_at` | `WF.cleanup_apply` | `WF._plan_view` | 例外：冻结的证据 | 实际应用被记录时刻；不是 expires_at。 |
| `expires_at` | `WF.cleanup_plan` | `WF.cleanup_plan/_cleanup_apply_precheck/_plan_view` | 在用 | 计划期限判断，applying 恢复不受同一期限限制。 |

JSON：`reasons_json` 是字符串数组，其他三列的顶层如下。整体读者 `WF._plan_view` 返回完整证据/保留/结果，`CR` 在 replay 取结果 retention，`storage.py:_allocation_reference_sets` 递归检查引用。

| JSON 顶层键 | 写入者 | 读者 | 判断 |
| --- | --- | --- | --- |
| `evidence_json.hostConclusion` | `WF._cleanup_reasons` 经 cleanup_plan | `_plan_view` | 当时允许清理的 Host 结论快照。 |
| `evidence_json.ownerGeneration` | 同上 | `_plan_view` | 当时 owner generation；之后可改变。 |
| `evidence_json.attemptId` | 同上 | `_plan_view`、引用泛扫 | 当时选中的 attempt。 |
| `evidence_json.acceptance` | 同上 | `_plan_view` | 当时 accepted/concluded 及时间/verdict。 |
| `evidence_json.shutdown` | 同上 | `_plan_view` | 当时完整 lineage 的有界停止证明。 |
| `evidence_json.finalArtifactId`、`evidence_json.integrationId` | 同上 | `_plan_view`、引用泛扫 | 当时固定成果/整合依据。 |
| `evidence_json.workspace` | `WF.cleanup_plan`，`cleanup_apply` 换成现场 inspection | `_plan_view/cleanup_apply` | 物理对象身份、inspection、未封存路径及锁状态；planned 与 applying 的 shape 明确由 state 区分。 |
| `retention_json.workspaceDirectory` | `WF._cleanup_reasons/cleanup_plan` | `_plan_view/cleanup_apply`、引用泛扫 | 物理 allocation 私有目录。 |
| `retention_json.logicalWorkspaceDirectory` | `WF._cleanup_reasons` | 同上 | 逻辑快照目录，可能不同于物理 allocation；不可直接去重。 |
| `retention_json.fixedRefs` | `WF._cleanup_reasons/cleanup_apply` | 同上 | 从固定产物及现场合并得到的当时引用列表。 |
| `retention_json.outputPatches`、`retention_json.artifactIds` | `WF._cleanup_reasons` | 同上 | 当时承诺保留的 patch 与 artifact 列表。 |
| `retention_json.commandReceipts` | `WF._cleanup_reasons`=true | `_plan_view` 透传 | 当前常量；可从清理契约重建投影。 |
| `retention_json.checkoutRoot`、`retention_json.cwd` | `WF.cleanup_plan/cleanup_apply` | `_plan_view`、引用泛扫 | 当时物理根与逻辑 cwd，二者不必相同。 |
| `result_json.removed` | `WF.cleanup_apply`=true | `_plan_view` 透传 | `state=='applied'` 可推出。 |
| `result_json.path` | 同上 | 同上 | 与 `path` 列相同。 |
| `result_json.alreadyRemoved` | 同上，根据实际 removal | 同上 | 独立的真实执行证据；此前是否已不在，无法从最终 state 推出。 |
| `result_json.repositoryId`、`result_json.checkoutId` | 同上 | 同上 | 与同名身份列相同。 |
| `result_json.retention` | 同上，解码 retention_json 拷贝 | `_plan_view`、`CR` | 与 `retention_json` 整体相同；进入 applied 后无更新 retention 的路径。 |

`result_json` 重复项的算法是列取值、解码 `retention_json` 和比较 `state`，由同一 `cleanup_apply` 事务复制。重复回放有 `alreadyRemoved` 独立语义，必须保留；没有读者依赖 result.retention 与 retention_json 不同。检索 `rg -n 'workspace_cleanup_plans|actor_role|actor_id' src/hey_my_buddy/blackboard/tasks/workflow.py src/hey_my_buddy/blackboard/tasks/command_receipts.py src/hey_my_buddy/blackboard/tasks/storage.py`，逐读全部 cleanup SQL、`_plan_view` 与引用扫描：actor 只有 INSERT/CHECK，无领域消费；`rg -n 'cleanup.*actor|plan\.actor' src/hey_my_buddy apps/console/src` 无清理 actor 使用。删除 actor 时同步移除相关 CHECK，不扩充新审计功能。`workspace_cleanup_plans_run_idx(run_id,state)` 支持 run 恢复/围栏和删除；path/checkout 的额外全局围栏当前是扫描，不构成该索引无用的证据。
<!-- END workspace_cleanup_plans -->

<!-- BEGIN agent_credentials -->
## 41. `agent_credentials`

表需要，为已发出的 attempt 凭据提供有效/撤销状态；不能只从 token 能否按密钥推导判断授权。当前只签发一个 turn 绑定的 attempt credential，无期限签发功能；不为可能的未来期限保留列。删除按 run/task/attempt 处理。

| 列 | 写入者 | 读者 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `credential_id` | `WF._issue_credential`=`cred-`+attempt_id | `WF.resolve_credential/_active_credential` | 例外：键或约束需要 | 主键；提交时撤销围栏用它定位。 |
| `run_id` | 同上 | `WF.resolve_credential`、删除路径 | 例外：键或约束需要 | 外键；联结 run 的 delegation_authority。 |
| `task_id` | 同上，与 run_id 同值 | `WF.resolve_credential`、删除路径 | 重复 | 唯一产品 writer 用 run_id 写两列；返回 taskId 可由 run_id 得到，无差异路径。 |
| `attempt_id` | 同上 | `WF.resolve_credential/_issue_credential/_revoke_credentials`、删除路径 | 例外：键或约束需要 | partial UNIQUE `attempt_id WHERE state='active'`；绑定执行。 |
| `generation` | 同上 | `WF.resolve_credential` | 重复 | 按 attempt_id 联结 `attempts.generation`；签发调用由真实 attempt 传入，没有允许二者不同的读者。 |
| `turn_id` | 同上 | `WF.resolve_credential` | 例外：冻结的证据 | 发出 token 时绑定的 turn；同 attempt 的 credential 访问不能用 run.current_turn_id 代替。 |
| `token_verifier` | 同上，经 `Database.agent_token_verifier` | `WF.resolve_credential` 等值查找 | 在用 | 验证输入 token 并只选 active 行；是授权边界，不作明文 token 保存。 |
| `state` | 签发 active；`WF._revoke_credentials/_issue_credential` 改 revoked | `WF.resolve_credential/_active_credential/_revoke_credentials` | 在用 | 撤销事实不能从 task/attempt 结果简单推出，重签也先撤销。 |
| `created_at` | `WF._issue_credential` | 无 | 没有读者 | 没有 credential 审计输出或时间判断，见检索。 |
| `expires_at` | 无有效 writer，仅 DDL 默认 NULL | `WF.resolve_credential` 分支 | 没有写者 | 签发 INSERT 不包含此列，产品从不 UPDATE 期限。 |
| `revoked_at` | `WF._revoke_credentials/_issue_credential` | 无 | 没有读者 | 只按 state 判断授权，不按撤销时刻。 |
| `revision` | 默认 1；两条撤销 UPDATE 自增 | 无 | 没有读者 | 没有 credential revision 比较或投影，只有递增。 |

无 JSON 列。检索 `rg -n 'agent_credentials' src/hey_my_buddy` 列出 DDL、resolve、签发/撤销、提交围栏和删除的全部 SQL；逐读这些函数并检索 `rg -n 'created_at|expires_at|revoked_at|revision' src/hey_my_buddy/blackboard/tasks/workflow.py`，仅 `_issue_credential` 写 created_at，两撤销 UPDATE 写 revoked_at/revision；resolve 只读 expires_at，其余没有读取。`storage.py:_workspace_reference_rows` 不包含 agent_credentials，亦无泛扫读者。再以 `rg -n 'agent_credentials|credential.*expires|credential.*revoked' src/hey_my_buddy apps/console/src` 核对没有第二个产品 writer/投影。`expires_at` 读取分支是未获得有效存值的空功能，应连同列删除；DDL 允许的 expired 状态也无产品写入，按当前授权/撤销契约收窄即可。`agent_credentials_attempt_unique` 有并发有效 credential 约束；`agent_credentials_run_idx(run_id,state)` 用于按 run 撤销及删除，不列无用。
<!-- END agent_credentials -->

<!-- BEGIN workflow_artifacts -->
## 42. `workflow_artifacts`

表需要：固定输入、turn-input、封存输出、Host 处置/partial-output、shared-refs 的不可变清单，检出回收之后仍是任务成果。与 `artifacts` 的通用执行文件记录不是相同对象，不整表合并。封存 JSON 及按摘要绑定的物理原件保持逐字不变；数据库索引和投影可以剪重复，不能因此改写封存内容。删除在 `macro_task_deletion.py:_delete_rows` 按 run 进行；`_collect/_delete_workspaces/_delete_private_roots` 先收集并处理实际清单、引用、物理目录。

| 列 | 写入者 | 读者 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `artifact_id` | `WF._pin_artifact` | `WF._artifact_view/_find_integration/_artifact_row_view/_accept_record/_latest_handoff`、`CR` | 例外：键或约束需要 | 主键；final、integration、conclusion 绑定此身份。 |
| `run_id` | 同上 | `WF.compact/_pin_artifact/_cleanup_reasons/_allocation_provenance/_latest_handoff`、删除路径 | 例外：键或约束需要 | 外键；UNIQUE `(run_id,attempt_id,manifest_sha256)`。 |
| `turn_id` | 同上 | `WF._artifact_view/_latest_handoff` | 例外：冻结的证据 | artifact 形成时所属 turn，原始 input 无 turn。 |
| `attempt_id` | 同上 | `WF._pin_artifact/_accept_record/_accept_verification_inputs/_latest_handoff/_artifact_view` | 例外：键或约束需要 | UNIQUE 部分；真实原始执行绑定。 |
| `source_task_id` | 同上；初次 input 为 NULL，其余 run_id | `WF._artifact_view` | 可推出 | 当前所有 `_pin_artifact` 调用：input（包括 helper input）传 NULL；其他 kind 全传 run_id。可按 kind 重建，无差异语义。 |
| `kind` | 同上 | 多个按 input/output/turn-input/resolved/partial/shared-refs 筛选读者 | 在用 | 数据库角色不等于 manifest.kind：host-output 可投影 partial-output。 |
| `manifest_json` | 同上；输入来自 `WS._prepare_locked`，输出来自 `WS._seal_output/_resolution_record/_abandoned_record`，shared-refs 来自 `WF._pin_shared_refs` | `WF._artifact_view/_accept_verification_inputs/_latest_handoff/_cleanup_reasons/_allocation_provenance`、`micro_task.py:resolve/resolve_many`（`RELATION_SQL`） 的 JSON 提取、删除物理清单与引用泛扫 | 例外：冻结的证据 | 见下按 kind 的完整顶层清单；记录具体执行输入或封存时刻。 |
| `manifest_sha256` | 同上，按 kind 选 snapshotSha256 或 manifestSha256 | `WF._pin_artifact/_artifact_view/_accept_verification_inputs` | 例外：键或约束需要 | UNIQUE 及固定绑定身份；output 指 snapshot 身份，并非输入 manifest 摘要。 |
| `created_at` | 同上 | `WF.compact/_artifact_view` | 例外：冻结的证据 | pin 发生的时刻、历史排序，不等于 Git commit 时间。 |

`manifest_json` 是按 kind 的封存联合类型。下表列全当前 writer 的顶层键；每组中的每个键有相同 writer/reader。`version` 及少数字段还有现存 v1/v2 输入的读取分支，其确切原件不转换。

| 类型与顶层键 | 写入者 | 读者 | 判断 |
| --- | --- | --- | --- |
| input/turn-input：`version`、`workspaceId`、`kind`、`path`、`checkoutRoot`、`checkoutId`、`repositoryId`、`access` | `WS._prepare_locked` → `WF._pin_artifact` | `WS._validate_manifest/verify/resolve_allocation`；`WF._allocation_provenance/_artifact_view/_cleanup_reasons`；存储/删除引用扫描 | 输入身份与位置的封存证据。 |
| input/turn-input：`baseCommit`、`inputCommit`、`inputTree`、`writeScope`、`integrator`、`targetRef`、`exclusionPolicy`、`snapshot`、`manifestSha256` | 同上 | `WS._validate_manifest/_artifact_binding/_output_entries`；`WF._accept_verification_inputs/_latest_handoff` | 当时输入、授权和排除规则；全部参与完整清单摘要验证，不能删掉 JSON 的键。 |
| 输入历史形式可选 `accessReason` | v1/v2 writer 的冻结存量；当前 v3 不再写 | `WS._validate_manifest` 对 v1/v2 full 加入精确字段集 | 无当前 writer，但封存存量原样保留，不能以当前无写入删除旧键。 |
| output/host-output/resolution/abandoned：`version`、`workspaceId`、`taskId`、`attemptId`、`manifestSha256`、`baseCommit`、`inputCommit` | `WS._seal_output/_resolution_record/_abandoned_record` | `WS._finish_output/_finish_record/_artifact_binding`；`WF._accept_verification_inputs` | 与列或输入有相同内容，但在封存原件中绑定身份和前提，不改写。 |
| 同上：`commit`、`tree`、`changedPaths`、`includedUntracked`、`snapshot`、`snapshotSha256`、`ref`、`diffPath`、`diffSha256` | 同上 | `WS._finish_output/_finish_record/_artifact_binding/verify_sealed_output`；`WF._artifact_view/_cleanup_reasons/_latest_handoff`；物理删除 | 固定输出、patch 和实际现场证据；snapshot 中部分键与顶层重复，但受摘要绑定，存量保留。 |
| 可选 `kind` | `WS._seal_output` host-output；resolution/abandoned writer 固定各 kind | `WS._replay_record/_artifact_binding`；`WF._artifact_view` 按数据库 kind 投影 | 原件形式标识；不能从数据库 kind 全量重建。 |
| 可选 `action`、`actor`、`note`、`conflictFingerprint` | `WS._resolution_record/_abandoned_record` | `WS._replay_record/_finish_record`、`WF._artifact_view` 读 action；整条记录绑定摘要 | Host 处置的封存证据；数据库只画部分不等于其余无人读。 |
| 可选 `adoptedPaths` | `WS._seal_output/_resolution_record` | `WF._adopted_board_paths/_board_adopted_paths`；`WS.cleanup_inspect` | 保留原来批准的域外路径，不能从后来的 scope 推出。 |
| 可选 `cumulativePatch` | `WF` 调用 `WS.cumulative_patch` 后 pin | `WF._artifact_view/_cleanup_reasons`；`WS._artifact_binding` | 从原始输入到当前交付的累积 patch，原始每 turn patch 不是同一基线。 |
| shared-refs：`version`、`kind`、`taskId`、`attemptId`、`turnId`、`turnInputSha256`、`repositoryPath`、`manifestSha256` | `WF._pin_shared_refs` | `WF.verify_shared_refs_record/shared_refs_record_digest/_shared_refs_summary` | 逐字段身份检查或整条 canonical 摘要检查；封存不能改。 |
| shared-refs：`workspaceManifestSha256`、`workspaceId`、`startStash`、`sealStash`、`comparison` | 同上 | `WF._shared_refs_summary`；`verify_shared_refs_record` 整体校验；存储/删除引用扫描 | 开始与封存两个时刻的真实 shared-stash 观察，不能从当前 stash 重算。 |

没有确认可删除的封存 JSON 顶层键。`source_task_id` 的推导只调整数据库元数据及 API 投影，不改原件。检索所有 `_pin_artifact(` 及 `source_task_id=`，覆盖 `attach_micro_task/_create_helpers/_conclude_record/workspace_resolve/_pin_shared_refs/_pin_result_artifacts`，确认上述 writer 集合；若 stock 存在当前来源之外的历史非空 source_task_id，升级前需在私有 fixture 验证后决定是否保留不同来源，不能在运行时按写入时间猜形式。`workflow_artifacts_run_idx(run_id,created_at)` 支持列表、删除，不列无用。
<!-- END workflow_artifacts -->

<!-- BEGIN workflow_suggestions -->
## 43. `workflow_suggestions`

表需要，存 Worker 的有界建议；`WF._audit` 返回最多 50 条，`WF.suggest` 写入，任务删除按 run/attempt 清除。与事件只有 suggestionId/bodyBytes 的通知不同，不能合并成不含正文的事件。

| 列 | 写入者 | 读者 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `suggestion_id` | `WF.suggest` | `WF._audit` | 例外：键或约束需要 | 主键。 |
| `run_id` | 同上 | `WF._audit` WHERE、删除路径 | 例外：键或约束需要 | run 外键。 |
| `attempt_id` | 同上，从 scope | `WF._audit`、删除路径 | 例外：冻结的证据 | 建议产生时的 attempt，不能用当前 selected attempt 替代。 |
| `author` | 同上 | `WF._audit` | 可推出 | attempt_id 非 NULL 时 `"attempt:"+attempt_id`，否则 `"agent"`，writer 无其他取值。 |
| `body` | 同上 | `WF._audit` | 在用 | 建议正文没有第二份权威存储。 |
| `created_at` | 同上 | `WF._audit` 排序与输出 | 例外：冻结的证据 | 建议创建时刻。 |

无 JSON 列。推导 `author` 不访问外部数据，只需本行 attempt_id；由 `WF.suggest` 同一次 INSERT 保持一致，`_audit` 不依赖差异。检索 `rg -n 'workflow_suggestions|suggestionId' src/hey_my_buddy apps/console/src` 并读所有命中，未发现其他 author writer。`workflow_suggestions_run_idx(run_id,created_at)` 适合审计列表和删除，保留。
<!-- END workflow_suggestions -->

<!-- BEGIN provider_cooling -->
## 44. `provider_cooling`

结论：这是未解决配额拒绝序列的唯一状态，保留；成功且开始于最近拒绝之后才清理。不能由时间和计数重算 `cooling_until`，因为 native `resetsAt` 可以缩短期限，手动重新检测还会清空期限而保留计数。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| harness | routing/quota_routing.py `record_rejection` | `provider_state/providers/redetect/clear_on_success` | 键约束 | 提供者的 harness 范围。 |
| provider | 同上 | 同上 | 键约束 | 复合主键第二项。 |
| consecutive_rejections | routing/quota_routing.py `record_rejection` | `record_rejection`, `_view`，控制台 `cooling.tsx` | 在用 | 冷却退避级数及显示。 |
| cooling_until | routing/quota_routing.py `record_rejection/redetect` | `cooling`, `_view` | 在用 | 当前禁止时限；native reset 与人工重检测使其不可由计数/最近时间推出。 |
| last_rejected_at | routing/quota_routing.py `record_rejection` | `clear_on_success` | 在用 | 防止旧尝试的晚到成功清掉新拒绝。 |

无 JSON 列及显式次级索引。
<!-- END provider_cooling -->

<!-- BEGIN harness_account_reads -->
## 45. `harness_account_reads`

结论：Codex 账户观察节流时钟独立于 harness 健康检查时间，保留；目前只写 codex，但不据此推断可合并到健康表，因为两种观察各自推进。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| harness | service/harness_health.py `_codex_account_read` | 同函数 | 键约束 | 每 harness 的独立时钟。 |
| checked_at | 同上 | 同函数 | 在用 | 180 秒账户 native 读取门限，失败也先推进。 |

无 JSON 列及显式次级索引。
<!-- END harness_account_reads -->

<!-- BEGIN harness_health -->
## 46. `harness_health`

结论：当前健康投影在运行准入、catalog 信任、账户和删除预检中均使用。列 `status` 与 `record_json.status` 可能双写同义，须核对四种 harness 的 discovery 结果、旧库存及恢复路径才能选一种权威；`record_json.harness/available` 被 `read_health` 用行键与状态覆盖，是可剪的对象键候选。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| harness | service/harness_health.py `initialize/set_path/refresh` | `read_health`、catalog 和路由按 harness 查 | 键约束 | 固定四种 harness 主键。 |
| manual_path | service/harness_health.py `set_path` | `read_health/refresh`、harness 发现 | 在用 | 用户显式路径。 |
| revision | service/harness_health.py `set_path/refresh/invalidate` | `refresh`、catalog/catalog_store.py `record`、执行准备 | 在用 | 防过期发现覆盖新路径/健康代。 |
| status | service/harness_health.py `refresh/invalidate` | `read_health`、evaluation/evaluation.py、routing/decision.py、删除预检 | 在用 | 查询和准入使用的权威健康状态。 |
| record_json | service/harness_health.py `refresh/invalidate/_codex_account_read`、catalog/catalog_store.py `record` | `read_health`、catalog/accounts.py、刷新过程 | 在用 | Native 诊断、fingerprint、账户事实。 |
| checked_at | service/harness_health.py `refresh` | `read_health`，控制台 harness 状态 | 在用 | 最后健康观察时间，区别账户读取时钟。 |
| expires_at | service/harness_health.py `refresh`、Codex 恢复 | `read_health`、`refresh`、tasks/workflow.py | 在用 | 缓存有效性。 |
| scan_after | service/harness_health.py `refresh/check`、catalog/catalog.py | `refresh/check`、catalog/catalog.py | 在用 | 限速的下次扫描时刻。 |

`record_json` 当前精确顶层域与消费者补充（writer 缩写 `HH`=`service/harness_health.py:HarnessHealth`，`HD`=`buddy/harnesses/discovery.py`）。表内状态的冗余评估已经追到四 harness 共用的 `_discover`，不需要调用任何真实 harness。

| 顶层键 | 写入者 | 读者 | 判断 |
| --- | --- | --- | --- |
| `harness` | `HD._discover`→`HH.refresh` | `read_health` 强制用行键覆盖 | 存储重复，可按主键重建；删除的是入库副本，不改 native discovery 的返回合同。 |
| `status` | 同上；`HH._codex_account_read` 恢复 ready | `read_health` 强制用列覆盖 | 失效路径可只改列，旧 JSON status 允许过时，但没有读者依赖旧值；应合并成列权威。 |
| `available` | 同上；`HH.invalidate` 改 false | `read_health` 用 status=='ready' 覆盖 | 可推出，旧 JSON 允许过时但永远被覆盖。 |
| `command` | `HD._discover`→refresh | `HH._codex_account_read`、`buddy/harnesses/runtime_selection.py` 的已绑定 native 启动 | 实际可执行命令。 |
| `executable`、`version`、`source` | 同上 | `read_health` 完整 view，runtime selection/控制台诊断 | 发现时选出的文件、版本、来源，不从现存文件重新推断。 |
| `fingerprint` | 同上 | `HH.refresh` 比较、runtime selection | 缓存失效 identity，不等于 locationFingerprint。 |
| `scanFingerprint` | 同上 | `read_health` 诊断透传 | 完整扫描快照摘要，候选诊断列表已截断，不能从 retained candidates 重算。 |
| `candidates` | 同上 | `read_health` view、HH.refresh 的 unknown lastProbe | 有界发现诊断。 |
| `reason` | `HD._discover`、`HH.initialize/set_path/invalidate/refresh/_codex_account_read` | `read_health`、`catalog.discover`、路由/删除预检 | code/params 原因事实，非 status 的重复。 |
| `billingByProvider` | `HD._discover`、`HH._codex_account_read`、`catalog_store.record` 的 zcode 更新 | `read_health`、`catalog/accounts.py:view` | 独立账户观察；DSH view 从当前可信 catalog 动态重建，但其他 harness 有实际 stored 消费。 |
| `locationFingerprint` | `HH.refresh` | `HH.refresh` unchanged 检查 | 搜索位置摘要，不等于文件 fingerprint。 |
| `lastProbe` | `HH.set_path/refresh` | `HH.refresh` 重试门限、`read_health` view | 最新探测完成/unknown事实，unknown 不覆盖此前可靠记录。 |
| `accountReadFailure` | `HH.invalidate/_codex_account_read`，refresh 保留 | `HH.refresh/_codex_account_read`、`catalog/accounts.py:view`、控制台 | 实际失败及停止不明恢复依据。 |

`revision/manualPath/checkedAt/expiresAt/scanAfter/providers/account/permissions` 是 `read_health` 计算 view 的键，当前 native `_discover` 不把它们作为 record 顶层写入；不可把这些并不存在的副本算成清理收益。检索 `_discover` 返回 literal、`record.update`、`record[...]`、retained 合并与 record_json SQL 的完整调用链，仅上表 16 个顶层键有当前存储 writer。

本表无显式二级索引。存储引用扫描会解码 record_json 的字符串值，属于保守回收保护，不说明诊断键的领域消费。
<!-- END harness_health -->

<!-- BEGIN workflow_host_conclusions -->
## 47. `workflow_host_conclusions`

表需要，Host 对 failed/cancelled/delivered 的结论与成功验收不同；状态会转 concluded，必须冻结结论前实际结果。`HC` 指 `src/hey_my_buddy/blackboard/tasks/host_conclusions.py`，唯一写者 `WF._conclude_record`。随 run/attempt 删除。

| 列 | 写入者 | 读者 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `conclusion_id` | `WF._conclude_record` | `HC.view`、`CR` | 例外：键或约束需要 | 主键。 |
| `run_id` | 同上 | `HC.current`、`WF._audit/compact`、`macro_tasks.py:_summary`、删除路径 | 例外：键或约束需要 | 外键，UNIQUE run 保证一项最终结论。 |
| `attempt_id` | 同上 | `HC.view`、删除路径 | 例外：冻结的证据 | 当时 selected attempt，可为从未执行的 NULL。 |
| `run_revision` | 同上，run.revision+1 | `HC.view`、`apps/console/src/task-detail/parse.ts:parseConclusion` | 例外：冻结的证据 | 结论提交后版本，后来清理还能增加 run revision。 |
| `owner_generation` | 同上 | `HC.view` | 例外：冻结的证据 | 作结论时的 owner generation。 |
| `execution_status` | 同上，转换前 run.state | `HC.view`、`macro_tasks.py` failed 汇总、`macro_task_summary_cache.py` | 例外：冻结的证据 | failed/cancelled/delivered 不能从转为 concluded 的当前 state 恢复。 |
| `note` | 同上 | `HC.view`、`AcceptancePane.tsx:AcceptancePane` | 例外：冻结的证据 | Host 结论说明；与结构化原因互斥。 |
| `reason_code` | 同上 | `HC.view` → `reason_from_json` | 例外：冻结的证据 | 服务/Host 结论的结构化原因。 |
| `reason_params_json` | 同上 | 同上 | 例外：冻结的证据 | 与 reason_code 同时形成，CHECK 保证 object；不是任意说明。 |
| `evidence_json` | 同上，恒 `{"references":[]}` | `HC.view` 只取 references；前端画非空 evidence | 没有有效内容写者 | 所有插入都写空 references，没有入口提供引用、没有 UPDATE。 |
| `artifact_id` | 同上，Host partial seal | `HC.view`、`CR`、`WF._cleanup_reasons` | 例外：冻结的证据 | 结论保住的部分成果，artifact 外键。 |
| `integration_id` | 同上，恒 NULL | `HC.view`，前端仅非空显示 | 没有有效内容写者 | INSERT 参数始终 None，无后续 UPDATE；空引用不是已实现的整合功能。 |
| `actor_role` | 同上 | `HC.view` | 例外：冻结的证据 | 结论操作者；角色 CHECK。 |
| `actor_id` | 同上 | `HC.view` | 例外：冻结的证据 | 同上。 |
| `command_id` | 同上 | 删除路径 | 例外：键或约束需要 | UNIQUE 命令身份。 |
| `created_at` | 同上 | `HC.view`、`storage.py:_zcode_reasons` 的 grace-period | 例外：冻结的证据 | 结论时刻，真实回收条件读者。 |

JSON：`evidence_json` 仅有顶层 `references`，writer=`WF._conclude_record` 恒空数组，reader=`HC.view` 和 `parseConclusion/AcceptancePane`，无可写入有效内容的产品路径。`reason_params_json` 由 `WF.conclude` 的两个 `_conclude_record` 调用点决定：普通 Host 结论传 note，reason 为 NULL；递归关闭 Lead 已拥有任务时只传 `reason("lead.owned-concluded",leadRunId=target_id)`，因此 `reason_params_json` 唯一当前顶层键为 `leadRunId`，writer=`WF.conclude/_conclude_record`，reader=`HC.view`→`protocol/reasons.py:reason_from_json`→前端原因文本投影。此键记录导致该项被结论关闭的 Lead，不能用以后当前归属替代。检索 `rg -n "_conclude_record\(|lead.owned-concluded" src/hey_my_buddy` 覆盖两个调用点，没有任意 Host reason 写入口。

检索 `rg -n 'workflow_host_conclusions|evidence_json|integration_id' src/hey_my_buddy/blackboard/tasks/workflow.py src/hey_my_buddy/blackboard/tasks/host_conclusions.py src/hey_my_buddy/blackboard/tasks/command_receipts.py` 并在 `src/hey_my_buddy` 全范围搜索表名，只有 `_conclude_record` 一处 INSERT、没有 UPDATE；参数固定 `canonical_json({"references":[]})`、integration_id=`None`。`rg -n 'hostConclusion|conclusion\.evidence|conclusion\.integrationId' apps/console/src` 的非测试命中只有解析和条件显示，不提供 writer。存储泛扫不产生内容。建议删两列并在仍需要的输出契约中返回空 evidence/NULL integrationId；外键约束并不为永不写非空的预留列提供保留理由。`workflow_host_conclusions_run_idx` 是 UNIQUE，保留。
<!-- END workflow_host_conclusions -->

<!-- BEGIN router_settings -->
## 48. `router_settings`

结论：单行用户设置与按次冻结 Router 事实是两个时间点，保留。budget preset 与展开的秒数不能以当前设置重建历史请求。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| id | routing/router.py `initialize_configuration` | `configuration/configured_budget/_write_settings` | 键约束 | 单行 CHECK id=1。 |
| default_mode | routing/router.py `initialize_configuration/_write_settings` | `configuration`、Router 解析、控制台 `RolesView.tsx` | 在用 | fast/review 设置。 |
| budget_preset | 同上 | `configured_budget/configuration` | 在用 | brief/standard/deep 设置。 |
| retry_interval_seconds | 同上 | `configuration`、routing/router_history.py 跳过窗口 | 在用 | 用户调的重试间隔。 |

无 JSON 列及显式次级索引。
<!-- END router_settings -->

<!-- BEGIN router_buddies -->
## 49. `router_buddies`

结论：有序 Router 列表为当前设置，必须保留顺序。它与冻结请求中的 `facts.routerBuddyIds` 是不同时间点，不能合并。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| position | routing/router.py `_write_settings` | `configuration` 的 ORDER BY | 键约束 | 顺序和唯一位置。 |
| buddy_id | routing/router.py `_write_settings` | `configuration`、Router 解析 | 键约束 | UNIQUE 防重复 Router。 |

无 JSON 列及显式次级索引。
<!-- END router_buddies -->

<!-- BEGIN router_requests -->
## 50. `router_requests`

结论：单次决策的初始 Router 设置、共同输入和准入检查是冻结证据；重试可以更新 `decision_requests.requested_json`，所以它不能重建第一次快照。宏任务删除的决策必须带走本表行，当前删除路径按 decision_id 做到了。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| decision_id | routing/router_sequence.py `freeze_request` | `request_snapshot`、`router_dispatches` FK、删除路径 | 键约束 | 一次请求一份初始冻结。 |
| snapshot_json | 同上 | routing/decision.py `_queue_router_dispatch/prepare_router_claim`、routing/router_boundary_data.py | 冻结证据 | 首次准入的完整证据，后续重试不能覆写。 |
| frozen_at | 同上 | `request_snapshot`、边界审计视图 | 冻结证据 | 首次冻结时刻，由 `freeze_request` 一次写定；不可用当前设置时间代替。 |

JSON：固定顶层 `facts,baseInput,inspections`，另可含 `hostPacket`，写端 routing/decision.py `_create` 与 router_sequence.py `freeze_request`，读端 decision.py、router_boundary_data.py；`facts` 内 `routerBuddyIds,routerIdentities,routingMode,routingBudget,routerRetryIntervalSeconds,budget`，初始 `baseInput` 含 `task,buddies,preferences,annotations` 等模型输入，`inspections` 是逐 Router 的准入记录。`hostPacket` 若出现由边界读者直接使用；当前写入 `_create` 的 snapshot 只见前三键，检索 `hostPacket` 于产品 src/apps 发现读取但未见本表写入，属无写键候选/可能旧库存，须核对 upgrade fixture 再清。`facts` 与 `decision_requests.requested_json`、`baseInput` 与第一次 dispatch document 有共享字段，因独立历史时点而例外；没有读者依赖同一时刻副本的值差异，重试后的差异确有读者。无显式次级索引。
JSON 逐键补充：`facts/baseInput/inspections` 三键均由 `DecisionCoordinator._create` 构造、`router_sequence.freeze_request` 入库；`request_snapshot` 完整返回，专用 reader 分别为 `router.current_router/_frozen_facts`，`DecisionCoordinator._queue_router_dispatch`，`router_boundary_data.routing_boundary/router_trials`。`hostPacket` 的函数接受能力仍在 `freeze_request` 的“保留所有字段”逻辑中，但唯一产品调用 `_create` 不传该键，`router_boundary_data` 的读取不能证明有 writer。snapshot 内部 baseInput 的精确键域同第 26 节 input JSON 表，actor 四键只在 dispatch 增加；不能把 snapshot 没有保存的字段当成已有冗余。
<!-- END router_requests -->

<!-- BEGIN router_dispatches -->
## 51. `router_dispatches`

结论：每次 Router 尝试各有不可改写的输入，重试/故障转移会产生多行；保留。行内 buddy 身份与 `document_json` 的 `buddy/routerBuddy/routerBuddyId` 可能重复，但四元组用于当前读投影与绑定核对，裁剪须证明 actor 字段完全同义且旧库存同形。文档摘要是固定输入绑定，保留作证据。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| task_id | routing/router_sequence.py `reserve_dispatch` | `dispatch`、`router_claims` FK、删除路径 | 键约束 | 内部 Router 任务主键。 |
| decision_id | 同上 | `dispatches`、routing/decision.py `health_summary`、删除路径 | 键约束 | 指向冻结请求，支持多次派发。 |
| router_index | 同上 | `dispatches` 排序、`_dispatch`、结果校验 | 冻结证据 | 当次列表序号，当前列表可改变。 |
| buddy_id | 同上 | `_dispatch`、claim 事件、边界/历史视图 | 冻结证据 | 当次选择的 Router ID。 |
| harness | 同上 | `_dispatch`、删除私有目录定位 | 冻结证据 | 当次完整 native 身份成员。 |
| provider | 同上 | `_dispatch`、结果校验 | 冻结证据 | 同上。 |
| model | 同上 | `_dispatch`、结果校验 | 冻结证据 | 同上。 |
| effort | 同上 | `_dispatch`、结果校验 | 冻结证据 | 同上。 |
| document_json | 同上 | `_dispatch`、routing/decision.py 结果校验/执行、边界审计 | 冻结证据 | 实际传入 Router 的逐 actor 输入。 |
| input_sha256 | 同上 | `_dispatch`、routing/decision.py 结果校验 | 冻结证据 | `sha256(canonical_json(document))`，虽可重算，正作为固定输入绑定校验，与文档同事务写。 |
| created_at | 同上 | `_dispatch`、边界审计 | 冻结证据 | 派发时刻，区别首次请求冻结。 |

顶层精确键清单以第 26 节 input JSON 表为准，包含 `policyFacts/routerBuddyIds/executionWorkspace/outputSchema`。每键 writer=`DecisionCoordinator._queue_router_dispatch`→`router_sequence.reserve_dispatch`，完整 reader=`router_sequence._dispatch`→Router 执行和 Host 边界；每键的专用语义 reader 亦见第 26 节。这里的输入 JSON 作为实际执行文档参与 input_sha256 固定绑定，不因旁列可重建而重写原件。四元组、buddy_id/router_index 的旁列可从同一 immutable document 推出，`reserve_dispatch` 当前写者确实同源，`_complete` 只允许一致；它们是否保留应按具体查询/约束而非“都是冻结”决定。

本表无显式二级索引；按 decision_id 的 `router_sequence.dispatches` 当前扫描/排序，外键不会自动建立该子表索引，这不构成要删除的既有索引。
<!-- END router_dispatches -->

<!-- BEGIN router_claims -->
## 52. `router_claims`

结论：每次尝试与不可变 Router 派发的关系由复合 FK 约束；虽然 `router.claimed` 事件载相同身份，这张关系表保障事务绑定和快速按 attempt 查，保留。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| attempt_id | routing/router_sequence.py `record_claim` | `claim`、routing/router_boundary_data.py | 键约束 | 主键及 `attempts` 复合 FK。 |
| task_id | 同上 | `claim`、router_dispatches/attempts FK、删除路径 | 键约束 | 与派发 task 保持同一身份。 |
| generation | 同上 | `claim`、attempts FK、边界视图 | 键约束 | 防跨代 claim。 |

无 JSON 列；`UNIQUE(task_id,generation)` 防同代双认领；`attempts_router_binding_unique` 为复合 FK 的被引用唯一键，必须保留，`router.claimed` 事件副本是冻结审计而非可用的关系约束。
<!-- END router_claims -->

<!-- BEGIN router_outcomes -->
## 53. `router_outcomes`

结论：Router 结果回执支持幂等、按 buddy 时间线、跳过窗口与健康统计，保留关系投影；`recorded_at` 与 `events.created_at` 同事务同 `now` 是可推出候选，其他大部分字段也复制同事务事件 payload，但关系列承载索引/约束。删除宏任务时，routing plane 且 request_id 属删除范围的结果与按 task/attempt 结果会删；没有任务关联的未来 maintenance/portrait 事实需随其所描述对象清理，当前写入调用只见 routing plane。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| receipt_id | routing/router_history.py `record_outcome` | 同函数 | 键约束 | 哈希包含 selectionRetry（列未单存），保证相同事实重放。 |
| plane | 同上 | `_fact_entries`、routing/decision.py `health_summary`、删除路径 | 在用 | routing/maintenance/portrait 分类；当前产品调用仅 routing。 |
| request_id | 同上 | `_fact_entries`、`health_summary`、删除路径 | 在用 | 将结果接回一次选择请求。 |
| router_index | 同上 | `_fact_entries`、重放核对 | 冻结证据 | 当次列表位置。 |
| buddy_id | 同上 | `_fact_entries`、`buddy_timeline` | 在用 | 时间线筛选键。 |
| phase | 同上 | `_fact_entries`、重放核对 | 冻结证据 | 发生阶段。 |
| task_id | 同上 | `health_summary`、删除路径 | 键约束 | 可空任务 FK、查找/清理键。 |
| attempt_id | 同上 | `health_summary`、删除路径 | 键约束 | 可空、与 task 联合 FK。 |
| outcome | 同上 | `_fact_entries`、`health_summary` | 在用 | answered/no_answer。 |
| code | 同上 | `_fact_entries`、重放核对 | 冻结证据 | 原因码。 |
| event_seq | 同上 | `_fact_entries` 排序、删除路径、events FK | 键约束 | UNIQUE 连到实际事件。 |
| recorded_at | 同上 | `_fact_entries` | 可推出/重复 | `_append_event(...,now=now)` 与本行同事务写 `now`；可由 `events.created_at` 经 `event_seq` 读出，保持一致方式是同一入参；目前时间线读本列，没有读者要求两者差异。 |

无 JSON 列；完整事实也写入 `events.payload_json`，由 `event_seq` 严格绑定，若考虑合并需同时保留幂等 receipt 唯一性、时间线查询与 FK，不能仅凭副本判整表多余。索引 `router_outcomes_buddy_idx(buddy_id,event_seq)` 对时间线精确匹配；`router_outcomes_task_idx(task_id,event_seq)` 对删除 task 范围与健康关联；`router_outcomes_attempt_idx(attempt_id)` 对删除 attempt 范围，均有产品查询，尚无无用索引证据。
<!-- END router_outcomes -->

<!-- BEGIN catalog_pending -->
## 54. `catalog_pending`

结论：首次可信缺席时间与当时合法 efforts 是冻结状态，不能从当前可信 catalog 重建，因为之后发现会替换当前指针；保留直到再次发现确认或恢复。行只对应仍在的 catalog_current harness。

| 列 | 写入者（产品文件/函数） | 读者（产品文件/函数） | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| harness | catalog/catalog.py `_write_pending_map` | `_pending_map/pending_model_services`、catalog_current FK | 键约束 | 模型服务复合主键。 |
| provider | 同上 | 同上 | 键约束 | 同上。 |
| model | 同上 | 同上 | 键约束 | 同上。 |
| since | catalog/catalog_store.py `record` 经 `_write_pending_map` | `_pending_map/pending_model_services`、evaluation/evaluation.py `_buddy_view` | 冻结证据 | 第一次确认缺席时刻，用于 1 小时确认窗口和 UI。 |
| efforts_json | catalog/catalog_store.py `record` 经 `_write_pending_map` | `_pending_map/pending_model_efforts`、`restore_retained_availability` | 冻结证据 | 首次缺席前合法努力档位，新 catalog 已不含这些项。 |

JSON：`efforts_json` 是努力档位字符串数组，顶层没有对象键；写端 `catalog_store.record` 从旧可信 payload 提取，读端 `catalog.py:_pending_map` 完整解析。无显式次级索引。
<!-- END catalog_pending -->

<!-- BEGIN workflow_fixes -->
## 55. `workflow_fixes`

表需要，修正任务和被修任务的关系不等于父子协助、Lead 所属或事件同组，不能并入其他关系。写者为 `src/hey_my_buddy/blackboard/tasks/workflow.py:WorkflowCoordinator.attach_micro_task`，读者 `src/hey_my_buddy/blackboard/tasks/fixes.py:view`；删除按两端 run_id/fixed_run_id 一起处理，跨宏任务的关系也不会悬挂。

| 列 | 写入者 | 读者 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `run_id` | `WF.attach_micro_task` | `fixes.py:view` 正向/反向查询，删除路径 | 例外：键或约束需要 | 主键和 run 外键。 |
| `fixed_run_id` | 同上 | 同上 | 例外：键或约束需要 | fixed-run 外键及非自身 CHECK；独立关系事实。 |
| `created_at` | 同上 | `fixes.py:view` 反向排序 | 例外：冻结的证据 | 相同时间由 rowid 破同序，不能由 run 更新时间替代。 |

无 JSON。`workflow_fixes_target_idx(fixed_run_id,created_at)` 支持反向列表和删除，保留。
<!-- END workflow_fixes -->

<!-- BEGIN reminder_settings -->
## 56. `reminder_settings`

表需要，控制台拥有的原生静默提醒设置，不是 task.expected_seconds，也不是 Router 预算。写者 `src/hey_my_buddy/blackboard/store/db.py:Database.initialize` 首次默认值及 `src/hey_my_buddy/blackboard/service/service.py:BoardService.reminder_settings_set`；读者为 `BoardService.reminder_settings_view/reminder_settings_set` 与 `src/hey_my_buddy/blackboard/tasks/reminders.py:_scan`。

| 列 | 写入者 | 读者 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `id` | `Database.initialize`=1 | 上述全部 WHERE id=1 | 例外：键或约束需要 | 主键与 id=1 单例 CHECK。 |
| `silence_seconds` | initialize/set | `reminders._scan`、view/set | 在用 | 提醒静默阈值；值改变通知调度器重算。 |
| `updated_at` | initialize/set | view/set | 在用 | 设置更新时刻，对外展示。 |
| `revision` | initialize=1；set 自增 | view/set | 在用 | 设置独立版本，不与任务或 evaluation revision 相同。 |

无 JSON、无显式二级索引；主键支撑单例访问。
<!-- END reminder_settings -->

<!-- BEGIN attempt_reminders -->
## 57. `attempt_reminders`

表需要，记录已到期的提醒与一次 await 领取。`RM` 指 `src/hey_my_buddy/blackboard/tasks/reminders.py`。expected-duration 的最高倍数和 silence_started_at 负责去重，delivery_id 负责跨 CLI 调用重放；不能只保留 events。随 task/attempt 删除。

| 列 | 写入者 | 读者 | 归类 | 依据 |
| --- | --- | --- | --- | --- |
| `reminder_id` | `RM._scan` SQLite 自增 | `RM.consume/view` | 例外：键或约束需要 | 主键、领取顺序与精确 UPDATE。 |
| `task_id` | `RM._scan` 从 attempt 复制 | `RM.consume/view`、删除路径 | 例外：键或约束需要 | tasks 外键；虽可由 attempt_id 联结，仍承担约束。 |
| `attempt_id` | 同上 | `RM._scan/status/consume/view` | 例外：键或约束需要 | attempts 外键、两个 partial UNIQUE 的前缀。 |
| `kind` | `RM._scan` | `RM._scan/status/view` | 例外：键或约束需要 | expected/native 两种 CHECK 与 partial UNIQUE 条件。 |
| `expected_multiple` | `RM._scan` | `RM._scan` MAX、`RM.view` | 例外：键或约束需要 | expected-duration 去重 UNIQUE；补写过去多倍提醒时并非由本行 elapsed 唯一推出。 |
| `silence_started_at` | `RM._scan` | `RM._scan/status` | 例外：键或约束需要 | native-silence episode 去重 UNIQUE；可等于 started_at 或 native-at。 |
| `elapsed_seconds` | `RM._scan` | `RM.view`，经 event/await 输出 | 可推出 | `max(0, parse(created_at)-parse(attempts.started_at))`；两个时刻固定，浮点精度需与 `_seconds` 一致。 |
| `last_native_event_at` | `RM._scan` 从当时 activity | `RM.view` | 例外：冻结的证据 | 提醒触发时最后原生活动；最新 activity 会被覆盖。 |
| `tool_in_progress` | `RM._scan` 从当时 phase | `RM.view` | 例外：冻结的证据 | 提醒触发时 tool 状态，不是当前状态。 |
| `created_at` | `RM._scan` | `RM.view` | 例外：冻结的证据 | 真正生成提醒时刻；补写提醒可晚于理论 deadline。 |
| `delivered_at` | `RM.consume` | `RM.consume` WHERE IS NULL | 例外：键或约束需要 | 与 delivery_id 配对 CHECK；标记是否领取。 |
| `delivery_id` | `RM.consume` | `RM.consume` replay | 例外：键或约束需要 | UNIQUE；跨 CLI reminderWaitId 幂等身份。 |

无 JSON 列。`elapsed_seconds` 同一 `_scan` 用 `now` 写 created_at，并由相同 instant 减 immutable started_at 写值，reader 没有允许不一致的分支；删列可在 view 通过 join 的 started_at 重建，不能改写已发 `attempt.reminder` 事件或封存输入中的原值。两个 partial UNIQUE 索引分别证明多倍/静默段去重，必须保留。
<!-- END attempt_reminders -->

<!-- BEGIN conclusions -->
## 建议剪掉的

以下是当前代码能支持的清理候选，不是实施步骤或新的功能。凡涉及改存储形式的项，都应在 26 → 27 显式转换；没有任何建议授权改写已封存输入、snapshot、native checkpoint、receipt 或按摘要绑定的物理记录。writer/reader 的精确位置及负向检索见对应表节；表中“改投影”指继续从唯一权威存储生成现有返回值，不让用户承担内部形式变化。

| 项目 | 为什么（证据） | 存量换算 | 要改的写者与读者 | 风险与边界 |
| --- | --- | --- | --- | --- |
| `cursors` 整表 | 产品范围只有 DDL，三列无 writer/reader（第 16 节） | 删表，stock 若有行不代表存在产品消费者 | `db.py:CORE_SCHEMA`；无当前调用方 | fixture 为空，升级应仍能处理非空死表。 |
| `evaluation_state` 整表 | decision_buddy_id 无产品写者；两个时间列无读者；唯一 selector JOIN 已过时（第 21 节） | 移除旧 selector 依赖后删表 | `Database.initialize`、`catalog_store.record`、`user_policy.publish`、`BoardService.admit_execution` | 不能只删表而保留 service JOIN；当前 Router 权威设置在 router_settings/router_buddies。 |
| `model_facts` 整表及唯一写触发 | refresh 有产品调用，两个 read API 只有定义/测试，无可到达产品消费者（第 28 节） | 删表、停止启用动作的无消费者持久化 | `EvaluationStore.user_policy_publish` 的 refresh 调用、`model_facts.py:refresh_model_services/model_service_facts/snapshot_marker` | 不以未来 profile 面保留；本项不是提前实现 profile，也不能保留会写已删表的调用。fixture 无 stock。 |
| `tasks.spec_canonical_json` | 与 spec_json 三处同次 canonical_json 复制且无读取（第 4 节） | 删列，用原 spec_json | `BoardStore.task_submit`、`WorkflowCoordinator._admit_helper_task`、`DecisionCoordinator._create_task` | 不重新规范化旧 spec 字节，不改变 input_fingerprint。fixture 6 行两份完全相同。 |
| `tasks.queue_position` | 只有 DDL | 删列 | `CORE_SCHEMA`；无产品读写者 | 不引入新的队列排序；当前按 state/created_at。 |
| `workflow_runs.micro_task_json/micro_task_fingerprint` | 同 task 身份的 spec_json/input_fingerprint 精确副本，提交及 helper 同事务写（第 31 节） | 先核对差异，再去副本；按 run_id=task_id 联结 | `WF.attach_micro_task/_admit_helper_task`、`_turn_context/compact`、`micro_task.RELATION_SQL`、路由/列表投影 | 不能把有效续接 spec 覆盖成原始 spec；只合并两份本就不可变的原提交。fixture 4 行两字段差异均 0。 |
| `workflow_runs.workspace_id/workspace_manifest_sha256` | 从同次 workspace_manifest_json 的同名键提取（第 31 节） | 删旁列，JSON 取键；保留 NULL 语义 | 当前 workspace 写事务、`WF._workspace_view` 等全部列读者 | 不能从最新物理目录计算；fixture 4 行差异 0。 |
| `workflow_children.workspace_manifest_sha256` | 同次冻结 manifest 的摘要副本（第 33 节） | 从本行冻结 manifest 取键，删旁列 | `WF._create_helpers/_child_view` 及校验 | 不改成读取子 run 的最新 manifest；fixture 无 child 行，需补升级 stock。 |
| `attempt_activity.generation/event_seq/observed_at/created_at/revision` | generation 可由 attempts 联结，event_seq/observed_at 已在 JSON；其余两个无消费（第 17 节） | 删副本/无读列；不改 activity_json | `BoardStore._store_activity`；保留 `_activity_latest` 和 `reminders._native_at` 的 updated_at | 最新活动只有一行，不能顺便删提醒的服务接收时间。fixture 本表为空。 |
| `attempt_interruptions.observed_at` | `_reserve` 写观察时刻，产品只读 kind/state/seal_error 等，不消费此时刻（第 7 节） | 删未消费旁列 | `interruption._reserve`，无现有领域读者 | 若将其认定为明确保留的冻结停止观察时刻，应在裁剪决定中保留该事实；不能混同 attempt_shutdown_evidence.observed_at 的实际展示读者。 |
| `resource_claims.released_at` | 写释放时间但产品不读（第 15 节） | 删列，释放 state 仍存 | `BoardStore._release_claims/_admit_in_transaction` 等释放 writer | 不删 held/retained 的资源占用事实。 |
| `commands.created_at` | 所有回放按 command_id；没有时间读取（第 14 节） | 删列 | `BoardStore._store_receipt`、DDL | 不设命令到期策略；被删任务命令按已定 ADR-028 规则消失，其他幂等到原期限。 |
| `catalog_observations.created_at/completed_at` | INSERT/UPDATE 之后无领域消费，是否完成实际由 response_json 判别 | 删两列 | `catalog_store.begin/record` | 不把 response_json 内的冻结发现时间删掉；这是旁列清理。 |
| `evaluation_catalog.source/harness_version/provider_version` | 与 payload_json 三键同次同值，产品只读 JSON（第 25 节） | 删旁列，保留原 payload | `catalog_store.record`；删 `CatalogView.from_payload(...,row)` 无调用的旁列读取能力 | 不重新发现目录，不重写 payload/discovery_id。 |
| `evaluation_catalog.discovered_at/created_at` | 记录 now 而无可到达读者，不等于 native discoveredAt | 删列 | `catalog_store.record`，无 row 参数的 dead 读取分支 | 保留 JSON 原始 discoveredAt；不能以本行 now 替换。 |
| `decision_requests.created_at` | 同事务等于 evaluation_decisions.created_at；request_created_at 别名没人消费 | 删列及仅为它建的索引 | `DecisionCoordinator._create/_ROW_COLUMNS` | 继续按现有 decision 时间/rowid 列表分页，不能改变 cursor 语义。 |
| `router_outcomes.recorded_at` | 与 event_seq 指向的 events.created_at 同一个 now 入参（第 53 节） | 删旁列，时间线按 event_seq 联结取值 | `router_history.record_outcome/_fact_entries` | 保留 event_seq 外键、receipt 幂等与真实事件时刻，不改既有事件。 |
| `model_service_concurrency.created_at/updated_at` | 只有用户策略写入，没有产品读取 | 删列 | `user_policy.publish` | 模型服务三元组及并发限制保留，native 下架不能抹掉用户设置。 |
| `workflow_routes.updated_at` | 生命周期写入、所有具体投影均不读 | 删列 | `WF._start_routing/routing_dispatched/routing_settled/_routing_attention`、取消 writer | 仍保留 created_at 与冻结 owner_generation。 |
| `workflow_children.workspace_intent_json/revision` | 子表的 intent 无领域消费；revision 仅递增（第 33 节） | 删列，执行意图继续用 child run 的权威值 | `WF._create_helpers/child_settled`、相关 UPDATE；引用扫描不再扫已删字段 | 初始 intent 与当前 run intent 可能不同，不把过去的意图假装当前值；fixture 无 stock。 |
| `workflow_continuations.input_bytes`、`workspace_reservations.updated_at` | 写入后无字段级业务读取（第 35/36 节） | 删列 | continuation INSERT、reservation UPSERT/转移/释放 writer | 保留 created_at/released_at 和继续输入原文/摘要。 |
| `workflow_workspace_conflicts.manifest_sha256/blocking_paths_json/output_commit/output_tree` | 前二者可从完整 evidence_json，后二者经 artifact_id 从固定 manifest 得到（第 38 节） | 删除旁列并改为 JSON 取键/固定 artifact 联结 | `WF._record_scope_conflicts/workspace_resolve/_conflict_manifest/_conflict_view/_resolved_duplicate` | 不读当前检出替代证据；保留 UNIQUE 需要的 observed_fingerprint；物理失败原件不改。 |
| `workflow_workspace_conflicts.delivery_json` 的同次副本/常量 | action/artifactId/attemptId/turnId/commandId 与列同值；state/kind/turnOutcome 当前固定 | 只保留独立交付事实并重建原 view | `WF.workspace_resolve/_resolution_delivery_view/_resolution_delivery_result/_resolution_delivery`、`CR` | 保留 deliveredAt/resolvedFingerprint；不能把原失败 turn 改 returned。 |
| `workflow_integrations` 行内副本 | attempt_id 可从固定 artifact 推出；strategy/target/source/before/after 在 verification_json 与列中重复（第 39 节） | 选择一份权威，删除另一份并改投影；state 可由策略推出 | `WF._accept_record/_integration_view/_find_integration/_accept_conflicts_with_recorded`、`CR` | 必须保留验收当时真实 before/after，而非重新读当前 ref；保留 binding_sha256 的 UNIQUE。fixture 唯一 integration 是 not-required，没有 verified JSON stock，须补。 |
| `verification_json.verified/artifactAncestor/adjustments/unrecordedPaths/summary` | 合法入库 verified=true，ancestor 与 merge 策略对应，adjustments 是差异路径并集，unrecorded 为空，summary 恒空（第 39 节） | 删除可推导键或常量，投影时重建 | `WS.integration_verify` 的持久化选择、`WF._accept_verify/_accept_record/_integration_view` | 不删除不同路径内容的实际比较证据；原始执行 receipts 不改写。 |
| `workspace_cleanup_plans.result_json` 的副本与常量 | path/repositoryId/checkoutId 来自列，retention 全量重复，removed 可由 applied 推出（第 40 节） | 保留 alreadyRemoved 等独立结果，重建原返回 | `WF.cleanup_apply/_plan_view`、`CR` | 保留 applying 恢复围栏；不能以目录不存在替代已完成。fixture 两条计划尚无 applied result stock。 |
| `workspace_cleanup_plans.actor_role/actor_id` | 当前只写不读；原 view 不输出（第 40 节） | 候选删列及配对角色约束；若 Host 判定为刻意保留的授权来源事实则按冻结例外留 | `WF.cleanup_plan`；无领域读者 | 这是有争议的冻结属性，不能只凭未展示就抹掉独立历史身份；须在裁剪决定中明确是否属既有审计证据。 |
| `agent_credentials.task_id/generation` | 分别重复 run_id、attempt.generation | 删旁列，从本行/attempt 联结生成 scope | `WF._issue_credential/resolve_credential`、删除关联 | 不改变凭据签名算法、attempt/turn 绑定和 active credential UNIQUE。fixture 3 行差异 0。 |
| `agent_credentials.created_at/revoked_at/revision/expires_at` | 前三者只写不读；expires_at 只有读取空分支、没有有效 writer | 删列及空的过期分支，撤销仍按 state | `WF._issue_credential/_revoke_credentials/resolve_credential` | 不新增期限功能；active/revoked 的真实授权边界保留。 |
| `workflow_artifacts.source_task_id` | 当前 input→NULL，其他 kind→run_id，所有调用点一致（第 42 节） | 删旁列，投影按 kind/run_id 推出 | `WF._pin_artifact/_artifact_view` 及调用点 | 不碰 manifest_json；fixture 14 行规则差异 0，但其他历史来源仍须升级核对。 |
| `workflow_suggestions.author` | 唯一 writer 为 attempt_id 非空时前缀拼接，否则 agent（第 43 节） | 删列，读时按本行 attempt_id 拼接 | `WF.suggest/_audit` | fixture 无 suggestion，需补原形式 stock；不改历史 attempt 绑定。 |
| `workflow_host_conclusions.evidence_json/integration_id` | 唯一 writer 恒空 references/NULL（第 47 节） | 删空槽，必要 view 继续返回空数组/NULL | `WF._conclude_record`、`HC.view`、前端 parser/显示 | 不把未实现引用描述成已有功能；保留真正 partial artifact 绑定。fixture 2 行均空。 |
| `harness_health.record_json.harness/status/available` | `read_health` 总被主键/status列与其布尔投影覆盖（第 46 节） | 入库时不存副本，存量删三键；保持 native result 合同和 read view | `HH.refresh/invalidate/_codex_account_read`、`read_health` | JSON status 可以因 invalidate 留旧值，不能用旧值覆盖权威列；无业务读者依赖这个差异。 |
| `attempt_reminders.elapsed_seconds` | 从固定 started_at/created_at 用同一 _seconds 算法重建（第 57 节） | 删列，view 联结 attempt 时间 | `RM._scan/view/consume` | 毫秒解析与浮点算法保持一致，补写提醒的 expected_multiple 不能按此值反推；不改既有事件/输入。 |
| 确认恒定/同源的轻量槽：`messages.correlation_id/body_bytes`、`tasks.acceptance_verdict`、`workflow_children.auto_continue`、`decision_requests.kind`、`retention_json.commandReceipts` | 算法分别见第 11/4/33/26/40 节；源为 inquiry_id、UTF-8 长度、accepted_at、false、select、true | 校验 stock 后删除并重建现有投影 | 相应 writer 和 view/parser；CHECK/UNIQUE 真正需要的字段不能一起误删 | artifacts.verified 当前 writer 恒 1 但默认 0、fixture 无行，暂不并入已确认集合；旧库存差异不能静默解释。 |

仍未证成的推导：`tasks.required_capabilities/exclusive_resources/expected_seconds` 与最初 spec 的关系，需核清认领及后续有效 spec 的更新；`workflow_runs.continuation_count` 必须以各 continuation kind/state 与自增点证明算法，不能直接 COUNT(*)；`attempts.error/exit_code/signal/token_usage_json` 与原始 result 的重叠要考虑 release/补证及 observer 路径；Buddy 当前投影与可信 catalog 的重叠须考虑缺席确认、努力档退休、健康覆盖、保留旧身份。以上均在逐列表中标待核实，不据相似命名建议直接删。`macro_tasks/workflow_runs.activity_seq/activity_at` 的当前值由事件发生当时的归属传播生成，现有关系不保证可逆，不能采用简单 MAX(events.seq) 推导。

索引次要清点如下。“重复索引”与“当前无匹配消费”分开：前者有同等自动索引即可证明，后者只是本版产品 SQL 的静态结果，不是所有未来查询或生产性能的测量。

| 索引 | 本版查询核对 | 判断 |
| --- | --- | --- |
| `tasks_request_idx(request_id)` | request_id 已有 UNIQUE；`BoardStore._task_row/task_submit` 等值查找 | 完全重复普通索引，可删。 |
| `attempts_task_idx(task_id,generation)` | `attempts` 已有 UNIQUE(task_id,generation)；`Blackboard` 按 task/generation 查找、排序 | 完全重复普通索引，可删，不删 UNIQUE。 |
| `workflow_turns_run_idx(run_id,turn_index)` | 表级 UNIQUE 同两列；`WF.compact/_audit/begin_turn` 按 run 排序 | 完全重复普通索引，可删。 |
| `workflow_scope_versions_run_idx(run_id,scope_version)` | PRIMARY KEY 已建同列自动索引；`_current_scope/CR` 可等值或逆序使用 | 完全重复普通索引，可删。 |
| `commands_kind_idx(kind,created_at)` | 检索所有 commands SQL：回放按 command_id；删除以 task/attempt/命令/JSON 关联为 OR，kind 只有前缀 LIKE，未按时间排序 | 未见需要此索引的当前查询；在最终删除命令改写后复核 query plan。 |
| `decision_requests_created_idx(created_at,decision_id)` | 列表用 request.rowid，公开最新项用 evaluation_decisions.created_at，request_created_at 别名无消费 | 无匹配排序/过滤，随副本列删除。 |
| `attempt_activity_task_idx(task_id,updated_at)` | 当前 `_delete_rows` 可用 task_id 前缀 DELETE；无活动列表按 task/time 查询 | 不是“完全无查询”；若删 task_id 旁列并改按 attempt 删除则随删，否则保留到改查询时再核。 |

`attempts_router_binding_unique/attempts_router_outcome_binding_unique` 看似含重复主键，实际被复合外键引用，不列冗余。所有 partial UNIQUE（active attempt、reservation writer、auto continuation、active credential、提醒去重）都承载约束，不能按少见查询删除。其余显式索引的作用在各表节列出；本次没有在日常库执行 EXPLAIN/量测。

只读 fixture 交叉核对：把仓库 `tests/fixtures/upgrade-base/board.sql` 载入 `:memory:`，不调用 `load_fixture`、旧源码或任何产品服务；metadata.sourceCommit 为 `d9e8df5d039708654f098015fbc8fc76f9fbd11d`。查询只统计行数和差异数，不取内容、令牌或路径。meta 恰为 `capability_secret/created_at/schema_version`。spec 副本 6 行、run spec/fingerprint 4 行、run manifest 镜像 4 行、credential 镜像 3 行、artifact.source_task_id 14 行、Host 空槽 2 行、acceptance 等价 6 行，所述差异数均为 0；1 行 integration 为 not-required。children/suggestions/model_facts/artifacts/attempt_activity/attempt_reminders 无 stock；cleanup 有 2 行但无 applied result；这组空结果不证明历史形态已覆盖。按 AGENTS.md，未来实际触及这些未覆盖的旧形式时必须在同次修改扩展 fixture 生成器，本次没有权限改生成器或测试。日常看板没有读取，也没有用此 fixture 声称真实库存全部相等。

## 看似冗余但要留的

| 项目 | 例外类别 | 独立事实或约束 |
| --- | --- | --- |
| `attempts.model_harness/model_provider/model_model` | 冻结的证据 | claim 时选出的模型服务槽，之后任务改 Buddy 或停止不明都不能换占用身份。 |
| attempt nonce/runtime/process identity 与 shutdown/interruption evidence | 冻结的证据；键或约束 | capability 验证和完整 OS 身份/实际停止观察不是 PID 或 finished 状态的重复。 |
| `tasks.cwd` 与原始 spec.cwd | 在用且不同事实 | attach/续接准备后的真实执行路径可以不同于提交路径。 |
| request 的原始 input_fingerprint 与会更新的 requested_json | 冻结的证据 | 原始幂等身份不随 retry 的候选/Router 设置变动。 |
| `router_requests.snapshot_json`、每份 dispatch 文档与当前 decision input | 冻结的证据 | 首次准入、各 Router 派发、重新选择 Worker 是不同的真实时刻；只合并同次副本，不能丢历史版本。 |
| `workflow_turns.input_json`、实际 prompt/input 摘要、provenance 与 sealed_artifacts | 冻结的证据；封存内容不改写 | 准备/执行/返回时的真实输入和来源，不能用后来 run 当前值重写过去。 |
| `workflow_scope_versions` 的历史 writeScope/manifest/actor/stopped evidence | 冻结的证据；复合主键 | 每次授权当时的范围与全 lineage 停止事实，以后恢复子任务或扩大范围不改旧授权。 |
| `workflow_children.workspace_manifest_json` 与 child run 当前 workspace | 冻结的证据 | helper 创建当时的绑定，后来 child run 可续接或重新准备。 |
| helper/Lead/route 的原始 turn 与 owner_generation | 冻结的证据；关系约束 | 授权关系形成时的身份与 owner fencing，不能用当前 owner/turn 替代。 |
| Host conclusion 的 execution_status/run_revision/owner_generation/note/actor/artifact | 冻结的证据 | 最终 concluded 状态已经丢失结论前 failed/cancelled/delivered，后来的清理又可增加版本。 |
| integration 的真实目标 before/after、逐路径比较与 Host 调整说明 | 冻结的证据 | 验收那一刻真实 Git 对象和内容差异；可以删同次副本，不能重读当前 ref 来冒充原验证。 |
| cleanup 的 applying 状态、独立 evidence、retention 和 alreadyRemoved | 冻结的证据；恢复围栏 | 物理清理当时的条件/承诺/实际执行结果；描述任务清理动作，任务未删时保留有依据。 |
| `workflow_artifacts.manifest_json` 中的身份重复、snapshot/摘要/patch/ref | 冻结的证据；键或约束；封存内容不改写 | 原件自证身份和完整性；新形式只对新 seal，旧原件不得做“去键压缩”。 |
| shared-refs 的 startStash/sealStash/comparison | 冻结的证据 | 两个不同时刻观察到的全局 Git stash 事实，不能以现存 stash 覆盖历史。 |
| reminder 的 last_native_event_at/tool_in_progress/expected_multiple/silence_started_at | 冻结的证据；唯一性约束 | 生成提醒那刻的活动，及到期倍数/静默 episode 去重；最新活动不是历史观察。 |
| provider_cooling.cooling_until 与拒绝计数 | 在用，不可从计数推出 | native reset、手动 redetect、晚到成功 fence 导致独立语义。 |
| catalog_pending.since/efforts_json | 冻结的证据；模型服务键 | 第一次可信缺席的时刻和此前允许努力档；当前 catalog 可已缺席。 |
| harness_account_reads.checked_at 与 harness_health.checked_at | 在用，不同观察时钟 | 一次账户读与一次可执行文件健康扫描可以独立推进。 |
| user enabled/preferences/notes/concurrency 与 native catalog availability/facts | 在用，不同权威来源 | 用户设置要跨原生下架/登录失效存活，不能由 catalog 重建。 |
| meta.created_at | 冻结的证据 | 整个 board 的创建时刻；虽然暂无逐值业务读取，正是 AGENTS.md 允许的三项单值之一。 |
| 所有实际 PK/UNIQUE/FK/CHECK 依赖列及 partial UNIQUE | 键或约束需要 | 逐表列明具体约束；不能为删一个数据副本而解除 attempt/process/ownership/idempotency 的关系约束。 |

## 违反“冻结的证据随删除一起删”的

| 项目 | 删除后如何残留 | 定性与需要的 26 → 27 处理 |
| --- | --- | --- |
| `macro_task_deletions` 已完成行，包括 deletion_id/target_kind/target_id/state/started_at/deleted_at/micro_task_count | `macro_task_deletion._delete_rows` 最后把 state 更新为 deleted，保留对象身份/时间/数量；guard 继续可查墓碑 | 已定，随 26 → 27 实现。完成事务连记录一起删；升级清理既有完成行，不把它重新列为选择题。deleting 行仍是恢复日志，保留。 |
| `commands` 中 deletion_id 绑定的被删任务命令 | `_delete_rows` 将 task_id/attempt_id 清 NULL、response_json 清 `{}`、设置 deletion_id，命令ID/hash/kind/subject/created_at 等仍在；回放返回已删除语义 | 已定，随 26 → 27 实现。删除完成同事务删除这些命令，升级清既有残留；幂等到删除为止，不继续保留空回执。 |
| `events.kind='macro_task.deleted'` 的全局事件 | `_delete_rows` 已清目标事件之后，另 append 无 task_id/attempt_id 绑定的事件，payload 顶层含 macroTaskId/microTaskCount；后续按任务集合删不到它 | 与 ADR-028 #7“看板上不留下这个宏任务的任何东西”直接冲突，归已定删除不留残留规则的遗漏实现；26 → 27 应清已有事件并改完成路径不留对象证据。它不是要求新增日志功能。 |

除以上三项，已逐读 `_collect/_delete_rows` 的当前作用域与外键删除顺序：workflow 的 scope/conflict/integration/plan/artifact/suggestion/conclusion、attempt 的停止/进程证据、Router 的 request/dispatch/claim/outcome/decision 以及绑定事件均在所属任务删除集合内。`workflow_fixes`、Lead/child 关系按任一端删除；这些关系本身不会留悬挂键。独立 catalog/账户/用户设置描述的对象不是被删微任务，不因此列违规。未来 maintenance/portrait 仅是 DDL 允许值，当前无产品 outcome 调用；不能用未实现面来推断已有遗留。

删除 scope 中事件 payload、命令响应和物理清单都有通用或按 kind 的所有权提取；本清点是源码与固定 fixture 的只读证据，不是日常规模删除演练，未据此宣称所有异常旧库存、人工破坏或未来事件类型均已覆盖。实际升级须保住仍在 deleting/applying/uncertain 的状态和未改写的 seal，不能用未知当作已停或已删。
<!-- END conclusions -->
