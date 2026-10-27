# 独立审查：schema 剪枝第一步（微任务 1）

审查对象是 `git diff b5e6b7cb acc55212`，构建产物 `src/hey_my_buddy/console/assets/` 没有读。依据是 AGENTS.md 的存储规则与 0.x 一段、`docs/design/schema-audit/plan.md` 的“怎样升级”与“微任务 1”、ADR-028 第 7 条 2026-10-20 的修订。

## 验证边界

- **检查运行器没能在这个沙箱里跑通。** `uv run --frozen` 写不进 uv 的缓存，被沙箱拒绝。换用开发者主检出里的 `.venv` 解释器加 `PYTHONPATH=src` 再跑，`test_upgrade_fixture.py` 的 13 个测试里 12 个通过，剩下 1 个和其余 6 个文件都死在 `ModuleNotFoundError: pydantic`，因为那个 venv 太旧。所以受影响的测试我**没有跑过**，下面关于测试的判断都来自读代码。
- **复现过的一处（R1）。** 脚本放在 `$TMPDIR`，用私有 HOME 和私有状态目录，没碰日常看板。步骤：用 `load_fixture` 恢复样板看板，执行 `upgrade.upgrade`，再对样板里还在删除中的宏任务调用 `macro_task_deletion.delete`；原生会话的删除替换为空操作，免得碰到真实的 harness 目录。结果如下：
  - 删除完成，`state` 为 `deleted`，`duplicate` 为 true；
  - `macro_task_deletions` 剩 0 行；
  - `commands` 从 15 行降到 7 行，剩下的只有 `user_policy.publish` 与 `quota.redetect`，都不提这个宏任务或它的 4 个微任务；
  - 剩下的事件也没有一条提到它们；
  - 再删与预览都得到 `NOT_FOUND`；
  - 4 个工作区分配因为样板路径是 `owner-unproven`，作为 `skipped` 报告，这是原有的行为。

## 1. 删除路径

删除分三段：

1. **加围栏：** 一个短事务写入删除记录，不再有 `state` 与 `deleted_at`。
2. **物理删除：** 依次是原生会话、工作区、私有根目录、会话记录、CLI 文件，都在事务之外做。
3. **删行：** 最后一个事务删掉所有行，同一事务删掉被删任务的命令记录和删除记录本身（`src/hey_my_buddy/blackboard/tasks/macro_task_deletion.py:462-510`）。

各处中断的后果：

- **第 1 段之后、第 2 段当中中断：** 记录还在，行也都在。`reject_macro_task` 与 `reject_deleting_run` 只看记录在不在，所以照旧拦住提交（`macro_task_deletion_guard.py:7-41`）。预览返回 `deleting`，再次删除时走 `duplicate=True` 的分支，跳过定数核对，重做物理段，再做第 3 段。
- **第 3 段：** 它是一个事务，中断就整个回滚，状态回到“删除中”。
- **第 3 段提交之后、回复之前中断：** 重试得到 `NOT_FOUND`。控制台只在“结果未确认 / 未完成”时把 `NOT_FOUND` 当作已删完（`apps/console/src/use-macro-task-deletion.ts:87`）。
- **删完之后：** 指名这个宏任务的提交得到 `NOT_FOUND`（`macro_tasks.py:152-155`），workflow 命令的重放因为找不到 run 也得到 `NOT_FOUND`。这些都和 ADR-028 第 7 条一致。

我没有找到新引入的、会让看板或私有目录永久不一致的中断。`owner-unproven` 的对象删完之后留在磁盘上而看板上没有记录，这是改动之前就有的行为，不是这次引入的。

## 2. 升级

- **换算：** 先删已完成删除名下的命令，再删已完成的删除记录，再删 `macro_task.deleted` 事件，然后重建表（`src/hey_my_buddy/blackboard/store/upgrade.py:22-39`）。
  - 删除顺序满足原来的 `ON DELETE RESTRICT`。
  - 0.48.0 只在最后一个事务里才给命令写 `deletion_id`，那个事务同时把记录置为 `deleted`，所以还在删除中的记录名下不会有已关联的命令。把它们留下是对的，R1 也证实了留下来的删除能做完。
- **外键与原子性：** 整个升级在 `foreign_keys=OFF` 下用一个 `BEGIN IMMEDIATE` 事务完成，前后各跑一次 `integrity_check` 与 `foreign_key_check`。
  - 重建的表里没有 `AUTOINCREMENT` 表，所以不会丢 `sqlite_sequence`；看板里也没有触发器和视图。
  - rowid 会重新编号，但没有哪个存下来的值引用这些表的 rowid。
  - 失败时整体回滚，有测试（`test_failed_conversion_rolls_back_all_rows_and_ddl`）。
- **安装与备份：** 安装的 `converting` 阶段、备份校验里对 schema 26 的试转换，都和 25 → 26 那一次的做法一致。转换之后失败、或在转换中断后恢复，都从备份还原，有测试（`tests/python/install/test_schema_upgrade_install.py`）。

## 3. 删掉或合并的核对

- 删掉的核对都在边界之内，随功能一起删：
  - `_receipt_entry` 的 `deleted_only`，以及按 `deletion_id` 放宽的主体与哈希核对；
  - 各 workflow 入口回答“已删除”的分支；
  - `preview`、`delete` 里处理 `deleted` 状态的分支；
  - `assert current['state']=='deleting'` 改为 `assert current is not None`。
- 合并的值改为现场推导，只改了读法，没有去掉边界上的校验：
  - `acceptanceVerdict` 改由 `accepted_at` 推导；
  - `correlationId` 改读 `inquiry_id`；
  - `questionBytes` 改由 `body` 计算。
- 升级里新加的两处拒绝在转换这条边界上，位置合适（见发现 3）。

## 发现

### 1. 公开文档仍然描述旧的形式（应修，属于 Host 维护的文档）

下列文档还写着已经删掉的表、列或行为，这是“两套说法”的残留：

- `docs/reference/architecture.md:50`、`:56`、`:57`：还列着 `cursors`、`evaluation_state`、`model_facts`。
- `docs/reference/architecture.md:60`：还写着 `commands.deletion_id` 关联删除记录，删除记录保留“删除状态与时刻”。
- `docs/reference/architecture.md:191`：还写着 “linked command receipts replay as deleted”。
- `docs/reference/operations.md:154`：“The upgrade base is 0.48.0 …, with no upgrade pending … Install and the service accept schema 26 only”。现在有一次待做的升级，安装接受 26 与 27，服务只接受 27。
- `docs/reference/operations.md:194`：还写着 `commands.deletion_id`、`{commandId, deleted:true}`，以及删完之后得到 `MACRO_TASK_DELETED`。
- `docs/reference/operations.md:202`：还写着 “deletion records, command receipts with deletion links”。
- `docs/reference/macro-tasks.md:3`：写着 schema 25；`:56`：写着 `deletionState` 可以是 `deleted`，以及完成之后的预览形状。
- `docs/reference/evaluation.md:125-129`：整段描述 `model_facts`、`HMB_MODEL_FACTS_FILE` 与刷新的触发。
- `AGENTS.md`：“No upgrade is pending; the next change of stored form starts one in `blackboard/store/upgrade.py`” 已经不成立。

**失败情形：** 读这些文档的 Host 或 AI 会以为删除后重放命令得到 `deleted:true`、还能指望 `model_facts`、或者以为没有待做的升级。

### 2. 合成预览和钉着它的测试仍然是旧的删除契约（应修）

- `tests/probes/macro_task_console_preview.py:1403-1408` 对已删除的目标仍返回 `deletionState: "deleted"`；`:1453-1454` 再次删除时仍返回 `duplicate: true` 的回执。
- `tests/python/cli/test_host_preview.py:500-535` 钉着这些旧行为：删除后 `cursor` 与 `eventCursor` 各加一（相当于假设追加了一条全局事件），预览为 `deleted`，再次删除得到同一回执。

**失败情形：**

- 在合成预览里删掉一个宏任务之后再读它的删除预览，会得到 `deletionState: "deleted"`；新的控制台解析器（`apps/console/src/macro-task-deletion.ts:93`）会把它当作无效回复拒绝。
- 测试仍是绿的，因为它测的是预览自己的旧模型，和真实服务已经分道。浏览器检查（`apps/console/browser/macro-delete.check.ts`）用的是自己拦截的路由，没有经过这条路径，所以也抓不到。

### 3. 升级可能拒绝真实看板上 0.46.0 之前写下的消息（应修，推断，未在真实看板上核对）

- `src/hey_my_buddy/blackboard/store/upgrade.py:28-30`：只要有 `messages.correlation_id IS NOT inquiry_id` 的行，升级就拒绝。
- 0.46.0 之前，公开的 CLI 操作 `message` 接受调用方给的 `correlationId`（见 `99f4df34^` 的 `docs/reference/cli.md:173` 与 `store.message_post`），那时写下的行，`correlation_id` 可以不等于 `inquiry_id`。

**失败情形：** 日常看板上只要有一条这样的行，备份校验的试转换就抛 `ValueError`，安装在备份这一步回滚为 `UPGRADE_ROLLED_BACK`。看板不受损，但升级装不上，代码里也没有出路。`body_bytes` 那一条检查与历史写入者一致：`bounded_text` 一直用的是 UTF-8 字节数。`acceptance_verdict` 那一条与 0.48.0 的写入者一致。

**建议：** 安装之前在日常看板的只读副本上数一下：

```sql
SELECT COUNT(*) FROM messages WHERE correlation_id IS NOT inquiry_id OR body_bytes != length(CAST(body AS BLOB));
SELECT COUNT(*) FROM tasks WHERE acceptance_verdict IS NOT CASE WHEN accepted_at IS NOT NULL THEN 'accepted' END;
```

数出来不为 0 时，再由开发者决定这些值怎么换算。

### 4. `MACRO_TASK_DELETED` 现在只表示“删除中”（小事）

- `src/hey_my_buddy/blackboard/tasks/macro_task_deletion_guard.py:15-18` 只在删除记录存在时报这个错，也就是删除进行中。删完之后得到的是 `NOT_FOUND`。
- 它的细节里 `state` 恒为 `"deleting"`，`deletedAt` 恒为 `None`，是写死的常量；`console/server.py:137` 映射到 HTTP 410 Gone。
- 错误码的名字和 410 都说“已删除”，而实际含义是“正在删除”。这次的契约版本本来就要升（0.49.0），现在顺手改名并去掉这两个常量字段代价最小。控制台的文案（`i18n/zh/macroHeader.ts:109`）已经按“已进入删除流程”写了。

### 5. 删除不再推进事件头（小事，按 ADR 接受的后果）

- 第 3 段不再追加事件，而 `_head_of` 是 `MAX(seq)`。被删宏任务的事件如果正好包含最大的 seq，事件头还会**变小**。这是从 `MAX(seq)` 推出来的，没有复现：R1 里 `store.initialize` 先追加了别的事件，事件头反而从 66 升到了 68。
- `store._notify` 只在事件头变大时才调用 `on_change`，也就是调度器的唤醒；对删除来说不需要唤醒，这一点没有问题。
- 拿 `eventCursor` 当变化键的读者看不到这次删除。例如 `apps/console/src/use-buddy-history.ts:97-99`：如果 buddy 历史显示的内容依赖被删的任务，它要等到下一次别的事件才会重读。

我没有找到会因此出错的读者，只把这一点记在这里供 Host 判断。

### 6. 并发删除时，后一个浏览器看到的是错误（小事）

- 浏览器 A 打开预览，浏览器 B 删完，A 再点删除：A 处在 `ready` 阶段，得到 `NOT_FOUND`，按新的测试（`macro-task-delete.test.tsx` 中 “shows NOT_FOUND on the first deletion attempt as an error”）显示为错误提示。
- 以前这种情况得到的是 `duplicate` 回执，显示为成功。
- 结果本身没有错，宏任务确实已经没了，只是用户看到的是一条失败。

### 7. 删除记录里还有没人读的列（小事，推论）

- `macro_task_deletions.started_at`（`src/hey_my_buddy/blackboard/store/db.py:60`）只在 `macro_task_deletion.py:557` 写入，全仓库没有读者。
- `micro_task_count` 只有 `_summary` 读。而围栏之后的范围是钉死的（`macro_task_deletion.py:575-576` 断言范围没有变），所以它等于 `len(_collect(...).runs)`，可以推导出来。
- 按这一批自己的规则，没人读的列和能推导的列都该删。不过 ADR-028 第 7 条的文字写着要“记下删除的时刻”，所以删不删由 Host 对照 ADR 来定。

### 8. 备份校验重复做完整性检查（小事）

- `src/hey_my_buddy/blackboard/store/backup.py:268-270` 重新加回了 `Database(trial).initialize()`，而且 schema 27 的备份也会走到。2fcc50fd 当时有意删掉了它，理由是“只对备份看板直接检查一次”。
- 对 schema 27 的备份，它重复的是几行之前刚做过的 `integrity_check` 与 `foreign_key_check`。对 schema 26 的备份，同一份副本要做 4 次完整性检查：`_verify` 一次，`upgrade` 前后各一次，`initialize` 一次。
- `initialize` 额外做的只有 `_load_secret`。
- 完整性检查的耗时随看板变大而增长，安装与回滚还原时都会走这条路径。

### 9. 推导出来的公开字段成了冗余的接口（小事，超出这一批的范围，可以记入待办）

- 存储里的冗余删掉之后，公开视图里的 `acceptanceVerdict`（`store.py:484`、`macro_tasks.py:626`、`workflow.py:5513`）只是 `acceptedAt` 的推导，`correlationId`（`store.py:543`）恒等于 `inquiryId`。
- 控制台的 `row.acceptanceVerdict === "failed"`（`apps/console/src/timeline/Tracks.tsx:188`、`RowName.tsx:47`）永远不会成立，是一条死分支。
- 按“交互面精简”，这些字段应该从接口里删掉。这要改契约，不在这次剪存储的范围之内。

### 10. 没有测试在升级之后把删除中的记录做完（小事，属于测试缺口）

- `test_upgrade_fixture.py` 只核对了还在删除中的记录被原样保留，没有在升级之后真的把这次删除做完。我在 R1 里复现了它能做完，名下的命令与事件都清干净了。
- 建议把 R1 的步骤做成测试，原生会话的删除替换为空操作。这样，将来有人在升级里误删或误留这类命令时，测试能当场抓住。

## 样板看板

这一步转换或删除的每一种形式，样板看板里都有存量：

- **已完成与删除中的删除记录：** 各 1 条，删除记录 2 条。
- **关联到已删除记录的命令与 `macro_task.deleted` 事件：** 都有。
- **三张死表：** `cursors`、`evaluation_state`、`model_facts`，都有行。
- **死列：**
  - `tasks.queue_position`、`attempt_interruptions` 是合成存量，在元数据里列明了；
  - `tasks.spec_canonical_json`、`acceptance_verdict`、`commands.created_at`、`resource_claims.released_at`（已释放的有 3 行），以及 `messages` 的两列，都由公开操作写下。
- **测试抓得到的回归：**
  - 升级之后，测试把每张保留表的行与升级前按谓词过滤的集合逐行比对。多删或少删已完成删除名下的命令、事件或记录，都会失败。
  - 结构与新建的看板逐条比对 `sqlite_master`。
- **样板的不足：** 只有 1 条消息，而且是 0.48.0 的写入者写的，没有 0.46.0 之前那种 `correlation_id ≠ inquiry_id` 的存量，所以抓不到发现 3 所说的风险。这是样板的本性：它是从升级基点生成的。所以这一项只能在日常看板的副本上核对。

## 结论

没有发现阻塞。删除路径的三段中断都能接着做完，守卫还在；升级的换算、顺序与回滚都对；死表、死列与旧的回放路径在源码里删干净了。需要处理的是三件应修：公开文档的残留与 AGENTS.md 的“没有待做的升级”（发现 1），合成预览和钉着它的测试仍是旧契约（发现 2），以及安装之前要在日常看板副本上核对升级会不会拒绝 0.46.0 之前写下的消息（发现 3）。其余发现都是小事。
