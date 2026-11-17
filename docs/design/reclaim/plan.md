# 检出与原生目录的主动回收：执行计划

2026-10-30，整合 Host。依据是 [ADR-002](../../decisions/002-host-directed-assistance-and-workspaces.md) 2026-10-30 的补充与 [ADR-028](../../decisions/028-macro-task-deletion.md) 第 4、5 条。随还没安装的中间版本 0.58.0 一起装：存储形式的改动扩展还没在真实黑板上跑过的 31 → 32，`CONTRACT_VERSION` 留在 0.58.0。

## 已核对的根因

两次在私有样本里复现，Host 在源码里核对过。

1. **挑选封存漏掉了结束时的封存。** `blackboard/tasks/workflow.py` 的 `_latest_handoff` 把产物接到回合上，要求那个回合输入的清单等于产物的清单。微任务续做时为同一个检出准备了新清单，回合还没开始就被取消、结束；结束时封存的 Host partial-output 绑定的是新清单，没有同清单的回合，于是被滤掉。回收选到首轮旧清单上的输出，`blackboard/tasks/workspace.py` 的 `_cleanup_proof` 报 `unsealed-handoff`。
2. **内容一样也报未封存。** `_cleanup_proof` 在观察指纹不同时会逐项比较检出与封存树，比完之后却不论结果都追加 `unsealed-changes`。树相同、只是 HEAD 或索引不同的检出因此被误挡。
3. **删除宏任务套用了副本回收的内容证明。** `deletion_inspect` 遇到 HEAD 不等于输入提交就报 `identity-changed`，`macro_task_deletion_workspace.delete_workspaces` 跳过检出，`macro_task_deletion.delete` 照样删掉记录，留下检出、记录与引用，再没有出口。ADR-028 只要求证明归属。
4. **`keepCheckout` 不起作用。** `service.workflow_accept` 对每个 accepted 都调 `on_accepted` 入队，后台的 `cleanup_accepted_workspace` 只看状态，约两秒后照样回收；skill、Host 指南与控制台都没有用它。
5. **被挡下之后没有人重试。** 后台队列取出即清空，`NOT_READY` 不放回，服务重启也不重建；concluded 的不进队列。

日常黑板只读盘点（2026-10-30）：2,192 个分配目录里，1,134 个有黑板引用；没有引用、但有清单的 2 个，都不带检出；另有 1,056 个只有 `.lock` 与 `request.json` 的空壳，共 0.7 MB，是准备到一半中止留下的。回收计划里被挡下的有 44 条 `not-a-managed-worktree`，属于用开发者自己检出（`existing`）的微任务，本来就没有受管检出可回收。

## 一个微任务做完的内容

**A. 修根因，不动存储。**
1. `_latest_handoff`：只要产物封存的清单属于同一检出的分配来历里的某一份，就承认它；不再要求有同清单的回合。
2. `_cleanup_proof`：只在比较出未封存的路径、或排除项与封存时不同时，才报 `unsealed-changes`。
3. 删除宏任务时，`deletion_inspect` 只证明物理归属：分配、登记、正反指针、锁、没有进程停在其中。去掉 HEAD 的比较与内容证明。证明不了归属的，照 ADR-028 第 5 条留在原处、由存储检查列出，任务照常删除。
4. 附着分支照旧拒绝回收；回收结果把分支名与 `git switch --detach` 报给 Host。

**B. 服务主动回收检出。**
1. 删掉 `accept` 的 `keepCheckout`，连同说明、帮助与测试。
2. 回收对象从数据库推出：已验收或已结束的微任务，有受管（worktree）分配，它的 `(run_id, checkout_id)` 还没有 `applied` 的计划。`existing` 的分配不是对象。
3. 在现有的 `_maintain` 线程里做：服务启动时一次，之后每 60 秒一次。`accept` 与 `conclude` 照旧当场试一次。60 秒是固定节奏，不做成设置。
4. 每个 `(run_id, checkout_id)` 只保留一行当前的 `blocked` 记录，原因没变就原样不动，不写新事件。
5. 回收的依据是“终态 + 物理与内容证明”。不比较 owner generation，不冒用控制台的身份。
6. 删掉被取代的：内存队列与 `on_accepted` 回调、`storage.cleanup_accepted_workspace`、冒用的维护身份（`accepted-workspace-maintenance`、`storage-maintenance` 对终态 run 的那条回收路径），以及 `storage-plan` 里终态 run 的检出候选。

**C. 原生目录。** 同一个循环里，对已验收或已结束满三天、停止已经证实的微任务，用 `private_dirs.remove_tree` 删掉它的原生根 `harnesses/<harness>/micro-tasks/<…>/`，包括失败尝试的目录。不做占用观察，不加墓碑或日志：这些根只有黑板起的进程会用，它们的停止已由停止证据证明。`storage-plan` 不再列出这些根。用户自己的 harness 目录与原生会话库不在范围内。顺带做掉待办里留下的那一处：借用同一检出的协助任务以失败或取消结束、上级已经结束时，按上级的终态算，不再当作还能续做。

**D. 准备中止的空壳与没有任务的记录。** 同一个循环里，持着分配目录自己的锁：
- 没有清单的空壳，且它 `request.json` 记的请求已经没有任务、或任务已经结束，就删掉这个目录；
- 有清单、但请求已经没有任务的分配：删掉与清单相符的固定引用，检出若在、并且登记、锁与归属都证明属于它，就一并移除，最后删记录目录。

任务还在、没有结束的，不碰：它的准备可能正在进行。

**E. 存储与契约。** 在 `schema_31_32` 里删掉 `workspace_cleanup_plans.expires_at`，连同 `PLAN_EXPIRED` 分支与 `expiresAt` 投影；样板黑板里这张表有存量，升级链测试照规矩覆盖。契约去掉 `keepCheckout` 与 `expiresAt`。不加新表。

**F. 文档。** `docs/reference/` 里讲回收、`keepCheckout` 与存储计划的地方照新行为改：`workspace-lifecycle.md`、`operations.md`、`cli.md`、`architecture.md`。不在 `docs/acceptance/` 下写记录。

## 检查

经检查运行器、单进程，只跑受影响的测试。至少覆盖：
- C-Two 那个生命周期：worktree 首轮封存、manual continue 准备新清单、续做前 cancel、conclude，之后检出被回收；
- 同树、HEAD 不同的检出被回收；
- 删除宏任务时，Worker 自己提交过的检出、记录与引用都删干净，不留孤儿；证明不了归属的照 ADR-028 留下并列出；
- 被挡下的检出在挡的原因消失后，由循环回收；只保留一行当前记录；
- 原生根满三天被删，不满三天、或停止未证实的不删；
- 空壳与没有任务的记录被清掉，任务还在的不碰；
- 附着分支照旧拒绝，原因报给 Host；
- 升级链：样板黑板上 `expires_at` 删掉之后与新建的黑板一致。

## 验收

- Host 读完差异；这批改动删除路径，请 Opus 独立审一次差异。
- 合入之后跑受影响与下游的测试；装之前，在最终的树上跑完整检查与五次安装演练。
- 装上之后，看日常黑板上被挡下的计划与空壳是否按预期收掉。
