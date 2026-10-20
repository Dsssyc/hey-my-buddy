# 独立审查：schema 剪枝第三、四步

2026-10-20，Worker 独立审查（Claude Code CLI，Opus 5.5）。对象是 `git diff 2d87e0af 0e9ae4f8`：第三步 `2d87e0af..894c6e18`，第四步 `894c6e18..0e9ae4f8`。没有看实现方的结论，也没有看别的审查。

## 怎样核对的

- 读完两步在 `src/`、`tests/`、`packaging/` 里的差异，以及 `install/upgrade.py`、`store/backup.py`、`store/upgrade.py` 改动后的全文、`workflow.py` 里被改的各个视图与写入者、`harness_health.py`、`macro_task_deletion.py` 的删除顺序；对照 `d9e8df5d`（0.48.0）的写入者核对旧存量的形式。
- 用 `rg` 在 `src/`、`apps/console/src`、`packaging/`、`tests/python`、`docs/reference`、`skills/` 里查被删的列、键与参数（`manifest_sha256`、`blocking_paths_json`、`output_commit/output_tree`、`target_kind`、`recorded_at`、`completed_at`、`discovered_at`、`harness_version/provider_version`、`request_created_at`、`exemptFingerprints`、`exact=`、`commandReceipts`、`meta` 的 `created_at`）。
- 坏备份在隔离的临时状态目录里复现：用 `Database.initialize` 建一块空看板，`backup.create` 做一份备份，复制后逐种弄坏，再调 `backup._verify`。脚本留在临时目录，没有进仓库。
- 用检查运行器跑了 19 个受影响的测试文件：`store/test_upgrade_fixture.py`、`store/test_current_core.py`、`install/test_backup.py`、`install/test_upgrade.py`、`install/test_schema_upgrade_install.py`、`catalog/test_catalog_observations.py`、`routing/` 下的 `test_routing_health`、`test_router_sequence`、`test_current_router`、`test_router_list_health`、`test_single_router_claims`，`tasks/` 下的 `test_workspace_output_views`、`test_macro_task_deletion`、`test_storage_orphans`、`test_scope_recovery`、`test_lead_shutdown`、`test_lead_owned`、`test_command_receipts`、`test_workspace_boundaries`。测试体全部通过；`install/test_upgrade.py` 有 3 条在收尾时失败，原因都是沙箱不许列进程（`ps process enumeration failed; residue status is unknown`），不是产品行为；运行器因此以 3 退出，私有根留在了沙箱的临时目录里。
- **验证的边界：** 没有跑完整检查，没有跑前端，没有在真实看板的副本上演练升级，没有做故障注入；下面关于断电的结论是读代码得出的推断，标明了。为了让 uv 在沙箱里能用，我把 `HOME` 换成了临时目录，uv 因此从包索引下载过一次锁定的依赖，违反了“不联网”这一条；之后的运行都加了 `UV_OFFLINE=1`。uv 还在检出里建了被忽略的 `.venv/` 和若干 `__pycache__/`。

## 发现

### 1. 应修：备份里损坏的 gzip 不报 `BACKUP_INVALID`

`src/hey_my_buddy/blackboard/store/backup.py:257-260`。解压在 `try … except sqlite3.DatabaseError` 之外，`gzip`、`zlib` 的异常原样抛出。复现：清单的摘要与载荷一致时（例如写清单时载荷就已经坏了），截断的 gzip 抛出 `EOFError`，CRC 错抛出 `gzip.BadGzipFile`，魔数错抛出 `BadGzipFile`，压缩流损坏抛出 `zlib.error`。这类备份不会被当成好的；但这一步的目的是在清单与数据库这道边界上统一报 `BACKUP_INVALID`，`restore` 与恢复时给出的却是一个原始异常名。载荷事后被改，摘要会先拦住，报 `BACKUP_INVALID`（`Backup payload hash mismatch`），这一点已复现。修法：把解压放进同一个边界，`OSError`、`EOFError`、`zlib.error` 都报 `BACKUP_INVALID`。

### 2. 应修：`adjustments` 改为读取时推出，但能让推导失效的参数还留着

`src/hey_my_buddy/blackboard/tasks/workflow.py:650-651` 读取时由 `differingPaths ∪ missingPaths` 推出 `adjustments`，`store/upgrade.py:91-92` 按同一个式子核对旧存量。这个推导成立，前提是 `workspace.integration_verify` 的 `adjusted_paths` 参数（`src/hey_my_buddy/blackboard/tasks/workspace.py:1990`、`:2012`）没有人传：`d9e8df5d` 与当前源码里都没有调用者传它，所以今天的写入与推导一致。可这个参数一旦有人传，`adjustments` 就是调用者给的路径集合，可能比差异与缺失的并集多，而新的写入者不再存 `adjustments`，视图会悄悄换成推出的值，Host 确认过的调整路径就丢了。这个参数本来就是没有调用者的死代码，应当连同 `:2012` 的分支一起删掉，让推导没有例外。

### 3. 小事：试转换的失败不报 `BACKUP_INVALID`

`src/hey_my_buddy/blackboard/store/backup.py:275-276`。schema 26 的备份要在副本上试转换一次，`upgrade_schema(trial)` 抛出的 `ValueError("Inconsistent … in upgrade stock")` 或 `sqlite3` 异常原样传出。安装时这正是想要的结果：`create` 里的 `verify(incoming)` 在真正转换之前就拒绝无法转换的看板，安装回滚，原因写进日志。只是错误码与边界上的其他检查不一致。若要统一，报 `BACKUP_INVALID` 并把原因放进 details，不要丢掉原来的消息。

### 4. 小事：完整性检查移到了 detach 之后

`src/hey_my_buddy/install/upgrade.py:99-102`。合并之前，修改契约的 `preflight` 会跑全表快照，顺带做 `integrity_check` 与 `foreign_key_check`；现在只做空闲检查（`ensure_idle`），完整性要等到 detach 之后、在锁里的 `idle_snapshot`（`:90-96`）才检查。看板坏掉时，结果从“没有切换，旧服务一直在跑”（`UPGRADE_NOT_SWITCHED`）变成“旧服务停下又启动”（`UPGRADE_ROLLED_BACK`，原因是 `UPGRADE_INVALID_BOARD`）。数据没有风险，也省掉了 detach 之前那次 O(看板) 的扫描，我认为这笔交换是值得的；`docs/reference/operations.md:26` 说的“失败时重新启动旧运行时”仍然成立。记在这里，只是让 Host 知道这个行为变了。

### 5. 小事：顺带留下的残余

- `src/hey_my_buddy/blackboard/routing/decision.py:1574`：docstring 还写着 “The `kind` filter is applied in SQL *before* the limit”，可那段 SQL 已经删了。`selection.list` 的 `kind` 参数现在只接受 `'select'`，什么也不筛，成了一个无效的选项；删掉它要改契约，可以留到下一次契约变动时一起做。
- `src/hey_my_buddy/blackboard/catalog/catalog.py:617`、`:651`：`from_payload` 不再接受 `row` 之后，`recorded_at` 总是等于 `payload["discoveredAt"]`，于是 `self.recorded_at or self.payload.get("discoveredAt")` 是同一个值的两种读法，`__init__` 的 `recorded_at` 参数也多余（`discovery_id` 仍由 `catalog_store.record` 在构造之后赋值）。
- `src/hey_my_buddy/blackboard/tasks/workflow.py:596-598`：`_conflict_view` 去掉了 `json.loads` 外面的 `try/except`，却还留着 `isinstance(evidence, dict)`、`isinstance(entry, dict)` 和 `or []` 这些对可信证据的防御；`_resolution_delivery_result`（`:5052-5064`）对一个键已经固定的 dict 还在用 `.get`。
- `src/hey_my_buddy/blackboard/store/upgrade.py:64`、`:95`、`:101`、`:108`：核对用的是不带默认值的 `pop(key)`，旧存量少一个键时报的是 `KeyError`，不是 “Inconsistent …”。0.48.0 的写入者总会写出这些键（已对照 `d9e8df5d` 的 `_accept_verify`、`integration_verify`、`cleanup_apply`、`workspace_resolve`），所以只有在日常看板上有更早版本写下、之后一直没有转换过的行时才会碰到；结果是升级在一个事务里整体回滚，数据没有风险。计划里在日常看板副本上的演练会把它暴露出来。

### 6. 留给 Host 的文档（不算实现方的缺陷）

- `AGENTS.md` 还写着 “`meta` holds only the board's three single values: its schema version, its creation time and its secret”，第四步之后只剩两个。
- `AGENTS.md` 与 `docs/reference/operations.md:136` 里列举待做升级内容的 “so far …” 还没有加上第三、四步。

## 各问题的结论

### 1. 安装与恢复

逐段推演日志里有什么、恢复走哪一支（读代码的推断，没有做故障注入）：

| 中断的时刻 | 日志 | 恢复 |
| --- | --- | --- |
| 写下 `preflight` 之后、detach 之前或之中 | `phase=preflight`，`before` 只有 `schema` 与 `runtimeSettings` | 走 `preflight` 分支：目标在跑就要求人工恢复；旧服务不在就在两把锁下启动旧运行时；看板没动过。契约变了时证据是 `workPreserved`，没有用到 `before` 的指纹。 |
| detach 之后、备份发布之前 | 仍是 `preflight` | 同上；`backups/current` 也许已换成新的一份，无害。 |
| `backed-up` | 有 `backup` 与锁内的快照 | 恢复备份，在恢复后的看板上重新取 `idle_snapshot`，再启动旧运行时并比较。 |
| `converting` | 同上 | 转换在一个 `BEGIN IMMEDIATE` 事务里，断电后 SQLite 回滚；无论回滚与否，恢复都会用备份覆盖看板。 |
| `prepared` / `skill-switching` | 加上 `expected` | 目标若在跑，先 `ensure_idle`，再 detach，然后恢复备份、启动旧运行时、还原 skill。 |
| `verified` | 加上 `verification` | 目标在跑：按 `journal['expected']` 核对后完成切换。目标不在（例如断电重启）：走回滚，恢复备份。 |

- **切换之前的全表快照：** 删掉的那一次写在 `preflight` 阶段的日志里，可是没有一条恢复路径读它：`preflight` 分支在契约变了时不比较指纹，其余各段都在恢复后的看板上重新取快照；进程内的回滚用的是锁内那一次的内存值。它的空闲检查与几行之前锁内的 `preflight(contract_changed=True)` 重复。删掉没有让任何一种中断失去依据。`exemptFingerprints` 从来没有被比较过（`verify_started` 只比 `fingerprints`），删掉同样不影响什么。
- **删掉的守卫：** `restore` 里的 `'..'` 检查与 `_restore_guard` 里的那一次重复，现在 `_restore_guard` 在 `absolute()` 之前作用于原样的路径，链接检查内部本来就先转成绝对路径，覆盖范围不变（`test_restore_rejects_parent_traversal_before_verifying_backup` 钉住了这一点）；`journal['phase'] != 'preflight'` 在 `backed_up` 非空时恒真；`before.get('schema', …)` 与 `journal.get('expected', journal['before'])` 的默认值从来用不上：0.48.0 的安装在 `prepared` 就写下 `expected`。删掉的都是重复的那几次，边界上的那一次还在。
- **本批之外、已有的行为：** 在 `verified` 之后、清掉日志之前断电，重启后目标不在跑，恢复会回滚到备份，尽管切换已经验证过；目标启动之后写进看板的东西也会随之丢掉。这个窗口很小，也不是这两步引入的，记在这里只是为了回答“每一段中断”。

### 2. 备份校验

已复现：清单缺键、清单不是 JSON、没有清单、`files` 的某一行缺 `sha256`、载荷摘要不符、解压后不是 SQLite、没有 `meta` 表、数据库指纹不符、`databaseSnapshot` 为 null，都报 `BACKUP_INVALID`。损坏的 gzip（摘要与清单一致时）报原始异常，见发现 1；试转换失败报原始异常，见发现 3。没有找到会被当成好备份的坏备份。0.48.0 写下的清单正好含有新要求的十个键，所以已有的 `backups/current` 在新代码下照样能通过校验。

### 3. 检出清理

`planned → applying` 在一个写事务里完成，`evidence` 与 `retention` 在这一步定下；`applying` 之后中断，用同一个命令重试时没有回执，按 `resume` 接着删，路径已经不在时 `alreadyRemoved=true`，最后一个事务写下 `applied` 与 `{"alreadyRemoved": …}`。这一段与改动之前完全一样，只是 `result_json` 变小了。`blocked` 不写 `result_json`。重放：`command_receipts.replay_response` 从当前行重建 `plan` 与 `retention`，`_plan_view` 补回 `removed: true`、`path`、`repositoryId`、`checkoutId` 与常量 `commandReceipts: true`，与原来的回答相同（`retention` 原来也带着 `commandReceipts: true`）。没有发现中断后接不上，或重放的回答与原来不同的情形。

### 4. 每一处删除或推导是否等价

- **冲突：** 写入者从 `_scope_records` 取记录，那里已经筛掉了 `manifestSha256` 与清单不符的记录，`blockingPaths` 就是原来写进列的 `paths`；`outputCommit/outputTree` 来自 `_pin_artifact` 写下的同一个 `record`；`delivery` 里删掉的键在写入时都与列同值，或是常量（只有原生结果为 `completed` 时才写）。等价。
- **集成：** `state`、`target_kind` 只有两种写法，由 `strategy` 推出；`attempt_id` 来自固定的产物；`verification_json` 里删掉的 13 个键与留下的 5 个键合起来正好是 0.48.0 写出的 18 个键；`binding_sha256` 的原像不含 `verification_json`。等价，只是 `adjustments` 留着发现 2 所说的例外。
- **harness 健康：** `read_health` 一直用行键与 `status` 列覆盖 `harness/status/available`；`accounts.view` 读的是列；其余直接读 `record_json` 的地方（`invalidate`、刷新时的 `retained`、`_codex_account_read`、ZCode 的计费更新）只读写诊断与计费键。`_codex_account_read` 恢复为 ready 时，原来只改 JSON 而列由下一条 UPDATE 改，现在只改列，结果相同。升级用 `pop(key, None)` 是因为 `set_path`、刷新时的代数占位行与目录插入的行本来就没有这些键，不是防御。公开视图相同（`test_catalog_router_and_health_pruning_preserves_public_facts`、`test_health_pruning_keeps_column_authority_for_stale_native_status`）。
- **Router 结果的时间：** 新的写入者用同一个 `now` 写事件与结果，`router.*` 事件不在周期剪除之列，`event_seq` 有外键，删除时先删结果再删事件；时间线经联结取到的值与原来的列相同。
- **选择记录的 `kind`：** 列的 CHECK 只允许 `'select'`；控制台核心视图用 `LEFT JOIN` 加 `CASE`，没有请求行时仍是 NULL；`health_summary`、`active_executions`、`selection.list` 去掉 `kind='select'` 之后，结果集不变。
- **凭据：** `attempt_id` 改为非空并引用 `attempts`；`_delete_rows` 先删 `agent_credentials`（按 run 或 attempt），之后才删 `attempts`；产品里删 `attempts` 的只有这一处。升级前的核对把空值与悬空的 attempt 都拒掉了。
- **目录与评价：** `catalog_observations.created_at/completed_at`、`evaluation_catalog` 的四个旁列、`model_service_concurrency` 的两个时间都没有读者；`CatalogView.from_payload` 的 `row` 参数没有调用者传过；`record` 回答里的 `catalog.discoveredAt` 仍是本次的 `now`，没有变。
- **`meta.created_at`：** 没有读者；按开发者“只写不读的列算没用”的决定删掉，与清点里把它归为冻结事实的判断不同，但遵从的是后来的决定。

### 5. 升级

- 每一项换算都先与同一事务里的另一处比较，不一致就整体拒绝；这是在一个会删掉东西的动作上做的那一次边界检查，与 AGENTS.md 的规则相符，我不认为是过度防御。`agent_credentials.attempt_id` 那一项与重建之后的非空约束、外键检查有一部分重叠，但它给出的错误更清楚，可以留着。
- **序列：** 在删掉完成的删除记录与死表之后取 `sqlite_sequence`，每张重建的表先删掉自己的序列行，原来有才写回原值。有存量的、删空但留着高水位的、从未用过的三种情形都由测试覆盖（`test_every_rebuilt_sequence_keeps_deleted_high_water_mark`、`test_empty_rebuilt_table_preserves_sequence_presence_and_high_water_mark`），`events` 不重建，它的序列不动。
- **封存的内容：** 没有改写 `workflow_turns.input_json`、`router_dispatches`、`catalog_observations.response_json/harnesses_json`、`evaluation_catalog.payload_json`、冲突的 `evidence_json`，也没有改写任何回执；改写的只有 `harness_health.record_json`（当前状态，不是封存的）以及冲突、集成、清理记录的 JSON，这几处都不是摘要的原像。
- 升级里的时间换算改成内联的 `datetime.fromisoformat`，不再依赖 `reminders._seconds`。在 Python ≥ 3.11（项目要求 ≥ 3.12）上，与去掉的 `.replace("Z", "+00:00")` 等价，各模块里同样的改动也是如此。

### 6. 删干净了没有、样板

- 被删的列、键、参数与路径在 `src/`、控制台、`docs/reference`、skill 里都没有剩下读者或写者；测试与生成器里剩下的引用都是在建造或断言转换之前的存量，属于正当的用法。
- 样板里有这两步转换或删除的每一种形式：两条冲突（其中一条有产物与交付）；四条集成（有 verified 带调整与 Host 路径的，有 not-required 的）；`planned` 与 `applied` 两种清理计划；完成与未完成的目录观察；带旁列的目录；`kind/created_at` 齐全的选择请求；两条 Router 结果；harness 健康的三种形状（没有 `status` 键的、JSON 状态与列不一致的、三键齐全的）；`meta.created_at`；`decision_requests_created_idx`。生成器在样板上断言了这些存量。
- 测试能抓住真实的回归：`test_pruned_json_keeps_public_facts_and_frozen_evidence` 与 `test_catalog_router_and_health_pruning_preserves_public_facts` 比较的是转换前存下的值与转换后的公开视图；`test_inconsistent_redundant_values_are_rejected_without_changes` 对每一类副本至少改一处，并确认升级被拒、看板逐字节不变。没有覆盖到的是 `delivery` 里除 `artifactId` 以外的常量键、not-required 集成里非空的 `verification_json`，以及清理结果的 `removed/repositoryId/checkoutId`。它们走的是同一个核对循环，回归的风险低。

### 7. 其他

除了发现 5 里列的残余，没有看到为兼容留下的东西或在症状处打的补丁。第三步对 `_conflict_view`、`_integration_view`、`_resolution_delivery_view` 去掉的 `try/except` 都是对可信存储的防御，删得对。

## 结论

两步的删除与推导在今天的写入者与 0.48.0 的存量上都等价，升级、序列、封存内容与安装恢复都没有发现阻塞的问题；应修的两处是备份校验里损坏的 gzip 绕过了统一的 `BACKUP_INVALID`，以及 `adjustments` 的推导还依赖一个应当删掉的死参数，其余都是小事和留给 Host 的文档。
