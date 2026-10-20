# schema 26 → 27 的冗余剪枝：执行计划

2026-10-20，整合 Host。

**依据：**
- AGENTS.md 的存储规则：没有人读的表与列删掉，重复或能推出的合并或删掉；冻结的证据、键与约束需要的列不算重复，但冻结的证据随它描述的东西一起删；待做的升级要转换或删除的每一种形式，样板看板里都要有存量；
- [ADR-028](../../decisions/028-macro-task-deletion.md) 第 7 条 2026-10-20 的修订；
- [清点](inventory.md)：逐表逐列写明写入者、读者与归类，下面按它的编号称呼各表。

开发者 2026-10-20 定：

- 首次发版之前做完；
- 只写不读的列算没用，一起删。
- `meta.created_at` 也删：宏任务按创建时刻排序用的是 `macro_tasks.created_at`，它没有用处。

本文只安排实现，不表示已经实现。

**进度：** 微任务 1 于 2026-10-20 合入为 `25e0c123`，经 Opus 独立审查（[审查记录](review-m1.md)），在源码里，尚未安装。

## 怎样升级

- 一次待做的升级（26 → 27），四个微任务依次扩展它，后一个在前一个合入之后开工。
- **第一个微任务建起升级本身**，照上一次 25 → 26 的做法（`blackboard/store/upgrade.py`、安装的转换阶段、备份校验里对 schema 26 的试转换），基点仍是 0.48.0（`d9e8df5d`）。
- **每个微任务做到的形式**，样板看板里没有存量的，先在生成器里补上，再写转换（AGENTS.md）。
- **核对清点：** 每一项动手之前，在自己的基点上重新核对清点的结论（写入者与读者）。不属实的不动，在交付里写明。
- **封存的内容不改写：** 封存的快照，以及按摘要绑定在它上面的输入、原生检查点、回执与物理封存记录，一律不改。
- **清点里没能证实的推导不动：**
  - `tasks.required_capabilities/exclusive_resources/expected_seconds`；
  - `workflow_runs.continuation_count`；
  - `attempts.error/exit_code/signal/token_usage_json`；
  - buddy 当前投影与目录的重叠；
  - `activity_seq/activity_at`。
- **清点“看似冗余但要留的”一节里的各项照留**，只有 `meta.created_at` 例外：它只在建库时写，没有读者，开发者 2026-10-20 定删掉，放在微任务 4。

## 微任务 1：删除的残留、死表、重复索引与核心表的死列

1. **删除不留残留**（ADR-028 第 7 条的修订）：
   - 被删任务的命令记录，在删除任务行的同一个事务里删掉；`commands.deletion_id` 与回放时回答“已删除”的各条路径随之删掉。
   - 删除记录只在删除进行期间存在，完成的事务把它删掉；进行期间照旧拦住指名这个宏任务的提交，中断后能接着删。
   - 完成时不再追加全局的 `macro_task.deleted` 事件。
   - 删除完成之后，指名这个宏任务的提交得到 `NOT_FOUND`。
   - 升级清掉看板上已有的这三样：状态为 `deleted` 的删除记录、它们名下的命令记录与 `macro_task.deleted` 事件。还在删除中的记录照留。
   - 控制台的删除流程随之改（`apps/console/src/macro-task-deletion.ts`、`use-macro-task-deletion.ts`、`MacroTaskDeleteDialog.tsx`）：删完之后预览不再有“已删除”的状态，重试删除得到 `NOT_FOUND` 时按已删完处理；重建控制台的构建产物。
2. **整张删掉的表：**
   - `cursors`（16）；
   - `evaluation_state`（21），连同 `BoardService.admit_execution` 里过时的关联查询与各处写入；
   - `model_facts`（28），连同只写不读的刷新路径与没有调用者的读取函数。
3. **重复的索引：**
   - `tasks_request_idx`、`attempts_task_idx`、`workflow_turns_run_idx`、`workflow_scope_versions_run_idx`：与唯一约束或主键完全重复；
   - `commands_kind_idx`：随 `commands.created_at` 删。
4. **核心表没人读的列：**
   - `tasks.spec_canonical_json`、`tasks.queue_position`、`tasks.acceptance_verdict`（4）；
   - `commands.created_at`（14）；
   - `resource_claims.released_at`（15）；
   - `attempt_interruptions.observed_at`（7）；
   - `messages.correlation_id/body_bytes`（11）。

## 微任务 2：工作流里的重复列

- **`workflow_runs`**（31）：
  - `micro_task_json/micro_task_fingerprint`：与 `tasks.spec_json/input_fingerprint` 同次写下的副本，按 `run_id=task_id` 联结；
  - `workspace_id/workspace_manifest_sha256`：从 `workspace_manifest_json` 取。
- **`workflow_children`**（33）：`workspace_manifest_sha256`、`workspace_intent_json`、`revision`、`auto_continue`。
- **`agent_credentials`**（41）：`task_id/generation/created_at/revoked_at/revision/expires_at`，连同只有空分支的过期判断。
- **`workflow_artifacts.source_task_id`**（42），按 `kind` 与 `run_id` 推出。
- **`workflow_suggestions.author`**（43），按本行 `attempt_id` 推出。
- **`workflow_host_conclusions.evidence_json/integration_id`**（47）：恒为空，视图照旧返回空。
- **`attempt_activity`**（17）：`generation/event_seq/observed_at/created_at/revision`。
- **`attempt_reminders.elapsed_seconds`**（57）：用同一算法从固定的时刻算出。
- **只写不读的列：**
  - `workflow_routes.updated_at`（32）；
  - `workflow_continuations.input_bytes`（35）；
  - `workspace_reservations.updated_at`（36）。

## 微任务 3：冲突、集成与清理记录里 JSON 的副本

这一块要分清“冻结的证据”与“同一次写下的副本”：留下前者，删后者。

- **`workflow_workspace_conflicts`**（38）：
  - `manifest_sha256/blocking_paths_json` 从 `evidence_json` 取，`output_commit/output_tree` 经 `artifact_id` 从固定的清单取；
  - `delivery_json` 里与列同值的键和恒定的键删掉，只留独立的交付事实。
- **`workflow_integrations`**（39）：
  - 行内的副本只留一份权威；
  - `verification_json` 里能推出或恒定的键（`verified/artifactAncestor/adjustments/unrecordedPaths/summary`）删掉；
  - 验收那一刻真实的前后状态与逐路径的比较照留。
- **`workspace_cleanup_plans`**（40）：
  - `result_json` 里与列重复与恒定的部分，以及 `retention_json.commandReceipts`；
  - 只写不读的 `actor_role/actor_id` 连同配对的约束。

## 微任务 4：目录、评价、Router 与 harness 健康记录

- `catalog_observations.created_at/completed_at`（18）。
- `evaluation_catalog.source/harness_version/provider_version/discovered_at/created_at`（25），原始载荷不动。
- `decision_requests.created_at` 连同只为它建的索引，以及恒为 `select` 的 `kind`（26）。
- `model_service_concurrency.created_at/updated_at`（27）。
- `router_outcomes.recorded_at`（53）：时间按 `event_seq` 联结事件取。
- `harness_health.record_json` 里与列重复的 `harness/status/available`（46）。
- `meta.created_at`（3）：之后 `meta` 只有 schema 版本与密钥两项。

## 检查与验收

- **每个微任务：**
  - 用检查运行器跑存储、安装、升级样板，以及它碰到的模块的测试；
  - 删掉钉着被删列或被删路径的测试，新的形式有对应的测试。
- **Host 对每个微任务：**
  - 读差异、跑受影响与下游的测试；
  - 微任务 1 改了删除路径，请 Opus 独立审查一次（开发者已授权这类审查）。
- **四个都合入之后：**
  - 在日常看板的副本上，经启动器从 0.48.0 演练升级；
  - 加几处故障注入：转换失败、新服务起不来；
  - 核对数目、完整性、被删的形式确实没有了、保留的冻结证据原样；
  - 安装前跑一次完整检查。
- **版本：** 删除之后的回答从“已删除”变为 `NOT_FOUND`，存储形式也变了，所以契约与中间号移动（0.49.0）。

## Host 随后改的文档

- `docs/reference/operations.md`（删除之后的回答）；
- `docs/reference/architecture.md`（表数与 schema）；
- AGENTS.md（升级基点与待做的升级）；
- 删除宏任务在 `docs/reference/` 里的说明；
- 清点里点名的各处说明。
