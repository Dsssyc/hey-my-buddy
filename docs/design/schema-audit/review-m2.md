# 独立审查：schema 剪枝第二步（工作流里的重复列）

审查对象：`git diff 839fefff 2d87e0af`，检出在 `2d87e0af`。审查者：Claude Code CLI 上的 Opus 5.5 Worker，2026-10-20。只读，不改源码。

## 结论先行

没有阻塞的缺陷。每一处删除或推导，我都在 `839fefff` 上找过写入者与读者，结论是等价：被当作权威的那一份（`tasks.spec_json/input_fingerprint`、`attempts.generation/started_at`、`workflow_runs.workspace_manifest_json`、`workflow_children.workspace_manifest_json`、`workflow_artifacts.kind/run_id`、`workflow_suggestions.attempt_id`）没有任何产品路径在写下之后改动它；唯一会改 `workflow_runs.workspace_manifest_json` 的地方（工作区准备，`workflow.py` 原 6739 行）原本就同时改两份副本。下面列出的都是小事，以及一处要 Host 在真实看板副本上核对的风险。

## 怎样核对的

- 读完源码差异（`src/`、`packaging/`），逐列在基线 `839fefff` 上 `git grep` 写入者与读者；对推导的来源列再查一遍 `UPDATE`（`tasks.spec_json`、`input_fingerprint`、`attempts.generation`、`attempts.started_at`、`workflow_children`、`workflow_artifacts`、`workflow_suggestions`、`workflow_host_conclusions` 都没有改写它们的 `UPDATE`）。
- 追溯提醒的 `elapsed` 算法：从引入（`1622179e`）起一直是 `instant=_seconds(now)`、`created_at=now`、`elapsed=max(0, instant-started)`，所以 `max(0, _seconds(created_at)-_seconds(started_at))` 与存的值逐位相同。
- 追溯凭据的 `generation`：`_issue_credential` 收到的 `generation` 就是同一事务里插入 `attempts` 的那个值（`store.py` 1745–1815 行 → `begin_turn` → `_issue_credential`），`attempts.generation` 之后不改。
- 把 `tests/fixtures/upgrade-base/board.sql` 载入内存 SQLite，看了各表的存量与关系。
- **测试：** 检查运行器经 `uv run` 起不来，沙箱拒绝写 uv 的缓存（`failed to open file ~/.cache/uv/sdists-v9/.git: Operation not permitted`）。改用主检出里已有的 `.venv` 的解释器、`PYTHONPATH` 指向本检出、`HMB_CHECKS_TMPDIR` 指向沙箱可写的短路径跑检查运行器：`test_upgrade_fixture.py` 15 个测试里 14 个通过，剩下一个（`test_current_file_stock_and_stored_evidence_are_not_empty`）因为那个 `.venv` 缺 `pydantic` 而导入失败；`test_macro_task_deletion.py`、`test_reminders.py`（service）、`test_command_receipts.py`、`test_workflow.py` 都因同一原因没能导入，**没有跑成**。这一批的受影响测试要由 Host 在正常环境里跑。我留下的临时检查根已删除。

## 对七个问题的回答

1. **等价。** 逐列都成立，理由见上。公开视图、审计（`audit.microTask/microTaskFingerprint`）、续接与改路由（`_start_routing`、`routing_settled`、`_buddy_matches_micro_task`、Router 边界的约束）、宏任务列表与缓存（`_run_presentation`、`macro_task_summary_cache`、`TASK_HISTORY_SQL`）现在都从 `tasks` 读，值与原副本相同。`_turn_context` 拿到的 `task` 就是本 run 的任务行（`begin_turn` 以 `task["task_id"]` 取 run）。`micro_task.py` 原来的 `COALESCE(r.micro_task_json, t.spec_json)` 两边本就同值，简化为一边不改结果。封存的回合输入里以前写进去的 `sourceTaskId:null`（样板 `workflow_turns` 三行）与现在推出的值一致，且不被改写。
2. **Worker 凭据。** 签发（按 attempt 撤旧、插新）、作用范围（`run_id`、`attempt_id`、`turn_id`、操作集合）、撤销（`state='revoked'`）与“同一 attempt 只有一张有效凭据”的唯一索引都在。`expires_at` 在基线上没有任何写入者（唯一的 INSERT 不写它），`'expired'` 状态也从没被写过，所以删掉的过期判断是死代码，不是边界校验；边界上的校验（按 verifier 查、只认 `active`、联结 run）原样保留。`taskId` 改由 `run_id` 给出，`generation` 改由联结 `attempts` 给出，二者都与原列同值，升级的核对也守着这一点。
3. **删除路径。** 回执里的 `microTaskCount` 现在是 `len(scope.runs)`。删除的行只在最后一个事务里删，物理阶段之间中断再接着删时，`_collect` 从仍在的行重算出同一个范围；围栏挡住新的成员，最后那个事务还断言范围没有变大，所以数目与原来存的相同。守卫 `reject_macro_task`、`reject_deleting_run` 与会话内容的检查只用 `target_kind/target_id`，原样在。样板里还在删除中的那次删除，升级后做完，命令与事件里不再出现范围内的任何标识，记录没了，此后预览与删除都得 `NOT_FOUND`（`test_upgraded_in_progress_deletion_removes_its_commands_events_and_fence`，我那次运行里通过）。
4. **升级。**
   - 各项换算对。重建走“临时表复制 → 删表 → 用新 DDL 建表 → 拷回 → 按新 schema 建索引”，不经过 `RENAME`，所以别的表对 `workflow_runs` 的外键引用不会被改名；整个过程在 `foreign_keys=OFF` 的一个 `BEGIN IMMEDIATE` 里，前后都做 `integrity_check` 与 `foreign_key_check`，失败整体回滚（新的十四种不一致各有一个变异子测试，都要求行数改动大于零并且回滚到逐字相同）。schema 里没有触发器与视图，不会因删表丢东西。被重建的表里只有 `attempt_reminders` 是 `AUTOINCREMENT`，它的高水位在重建前取出、重建后写回，并有专门的测试（序列先加 100 再升级）。
   - 备份校验去掉 `Database(trial).initialize()` 是对的：对一个已存在的文件，它只重复版本、`integrity_check`、`foreign_key_check` 三项，前两项 `_verify` 已经对备份做过，试转换之后 `upgrade()` 自己又做了一遍；剩下的 `_load_secret` 只会往试转换的临时副本里写一个密钥，不是校验。说明里写的是“只在试转换时做一次初始化”，实际是一次也不做；按上面的理由，这样更准确。
   - 推导值与存储值不一致就整体回滚，是必要的一次边界校验，不是过度防御：升级会不可逆地丢掉这些列，而看板里的存量是多个版本写下的（例如 `5c13158a` 那次转换用两个不同的函数分别改写 `micro_task_json` 与 `tasks.spec_json`）；只核对有读者的列，没读者的列（`workspace_intent_json`、`revision`、`updated_at`、`input_bytes` 等）直接删，比例得当。
   - 封存的内容没有被改写：`workflow_turns`、原生检查点、回执与物理封存记录都不在重建的表里；`workflow_artifacts` 重建时 `manifest_json/manifest_sha256` 逐字拷回，被删的 `source_task_id` 不在任何摘要的原像里。`test_upgrade_matches_fresh_structure_and_preserves_every_retained_value` 在我那次运行里通过。
5. **删干净了没有。** 代码里被删的列没有残留的 SQL 读写（`rg` 过 `src/`、`apps/console/src`、`skills/`）；残留的是一个参数、一组重复的读法与几处文档，见下面的发现。
6. **样板看板。** 这一步动到的每张表都有存量：`workflow_runs` 4 行、`agent_credentials` 3 行、`workflow_artifacts` 14 行（`input` 4 行为空，其余为 `run_id`）、`workflow_host_conclusions` 2 行、`workspace_reservations` 4 行，来自基线的操作；`workflow_children`、`workflow_suggestions`（有 attempt 与无 attempt 各一）、`attempt_activity`、`attempt_reminders`（两种 kind）、`workflow_routes`、`workflow_continuations` 来自生成器里显式列出的合成行（`syntheticRetiredStock`），`metadata.json` 的 `sourceVersion` 仍是 0.48.0、`sourceTree` 未变。合成行是照旧写入者的样子手写的 SQL，证明的是“这种形式能被转换”，不证明基线真的写出过这些值；真正守住真实存量的是升级里的核对。变异子测试与序列测试能抓住转换上的真实回归。
7. **其他。** 计划“微任务 2”列出的各项都做了；计划之外的改动只有随 `workflow_routes.updated_at` 一起删掉的 `routing_dispatched`（它只写那一列），合理。

## 发现

### F1 小事：`attach_micro_task` 留着已无用的 `microTaskFingerprint` 参数

- `src/hey_my_buddy/blackboard/tasks/workflow.py:1596`（形参）与 `:1816`（调用方仍传 `fingerprint`）。
- 函数体不再读它；它原本只写进被删的 `workflow_runs.micro_task_fingerprint`。失败情形：没有错误结果，但留下了一条看上去还在写指纹的接口，下一个读者会以为 run 上还存着一份指纹。按 0.x 的规则属于被取代而未删的代码，删掉形参与调用方的那一项。

### F2 小事：同一列有两个读取函数，审计视图给出两个同值字段

- `src/hey_my_buddy/blackboard/tasks/workflow.py:295`（新的 `_micro_task`）与 `:7877`（原有的 `_original_spec`，`except ValueError: return None`）；`:2155–2156` 审计视图同时给 `microTask` 与 `spec`。
- 改动之前两份副本就同值；现在两者读的是同一个 `tasks.spec_json`，`spec` 与 `microTask` 在任何状态下都逐字相同。`_original_spec` 还吞掉解析异常，与“边界之内出错直接失败”相抵。失败情形：一个损坏的 `spec_json` 会让 `microTask` 抛错而 `spec` 悄悄变成 `null`，同一视图里两种说法。建议删 `_original_spec`，`spec` 字段要不要留由 Host 定（契约本就随这次升级移到 0.49.0，删字段不另付代价）。

### F3 小事：恒为常量的公开字段

- `src/hey_my_buddy/blackboard/tasks/workflow.py:460` 子任务视图的 `"autoContinue": False`；`src/hey_my_buddy/blackboard/tasks/host_conclusions.py:21–22` 的 `"evidence": []`、`"integrationId": None`。
- 后两者是计划写明的“视图照旧返回空”。`autoContinue` 计划没写；它在基线上也只可能是 `false`（唯一的 INSERT 写死 0）。失败情形：没有错误值，但交互面上留着永远不变的字段，而 `decide` 的参数里另有一个意义不同的 `autoContinue`，读者容易混。契约这次本就移动，Host 可以顺带决定删不删。

### F4 小事：凭据的 `attempt_id` 仍可为空且没有外键，而鉴权已改成内联结 `attempts`

- `src/hey_my_buddy/blackboard/store/db.py:808`；鉴权查询 `src/hey_my_buddy/blackboard/tasks/workflow.py:1008`；升级核对 `src/hey_my_buddy/blackboard/store/upgrade.py:44–46`。
- `generation` 现在依赖 `attempt_id` 指向一个 attempt。失败情形（推测，无现实写入者）：一行 `attempt_id IS NULL AND generation IS NULL` 的旧凭据能通过升级核对，升级后却因内联结而再也认证不了，且没有任何报错说明原因。基线唯一的写入者总是写 `attempt_id`，所以这只是形式上的歧义。这张表这次本就整表重建，把列收紧为 `NOT NULL REFERENCES attempts(attempt_id)` 不另付代价，也让约束与推导一致。

### F5 小事：还在删除中的记录，升级没有核对它的 `micro_task_count`

- `src/hey_my_buddy/blackboard/store/upgrade.py:67–71` 一带；读者是 `src/hey_my_buddy/blackboard/tasks/macro_task_deletion.py:256–258`。
- 别的被推导替代的列都先核对再删，这一列直接删。按问题 3 的推理，围栏下范围不会变，所以值相同（样板里存的 4 与推出的 4 一致）。失败情形只在围栏失效时出现，而那时删除最后的断言也会失败。记一笔是为了处理一致；不改也可以。

### F6 小事：升级引用产品代码里的私有函数

- `src/hey_my_buddy/blackboard/store/upgrade.py:60` `from ..tasks.reminders import _seconds`。
- 升级是对基线存量的一次性转换，它的判定却随一个产品私有函数变化。失败情形：以后有人改 `_seconds`（例如换时间解析），这次升级对同一份存量的判定会跟着变，样板测试未必能显出来。可以在 `store/` 里写一个一行的解析，或者把这个函数移到两边共用的位置。

### F7 小事：合成的 `workflow_children` 行描述的是产品造不出的关系

- `packaging/generate-upgrade-fixture.py:380` 一带。
- 合成行把 Host 直接提交的 `accepted` run 记作 `delivered` 的 helper，`request_id` 是 `fixture-synthetic-child-request`，而 `accepted` 的任务行 `request_id` 是 `fixture-accepted`；真实的 helper 由 `_create_helpers` 建，两者一致。失败情形：现在没有测试因此出错，但在删除中的那次删除、宏任务时间线这些按血缘走的测试，是在一棵产品不会出现的树上跑的。最好让合成行指向一个用基线操作建出的真实 helper；做不到就在生成器的注释里写明这一点。

### F8 小事（Host 的文档）：文档还写着被删的形式

- `docs/decisions/028-macro-task-deletion.md:21`：第 7 条仍写“黑板在开始删除时记下删除的时刻……与要删的微任务数”，这一步删掉了这两样，要修订第 7 条。
- `docs/reference/workflow.md:46`、`:49`：仍说 `micro_task_json`、`micro_task_fingerprint` 是 run 上不可变的部分，应改为 `tasks.spec_json/input_fingerprint`。
- `AGENTS.md` 里待做升级的描述只写到第一步。
- 这些属于计划“Host 随后改的文档”，不算实现方的缺陷，列出来免得漏。

## 未验证的风险：日常看板的历史存量

这一步的升级核对以“同次写下的副本”为前提。样板满足它，但日常看板经过多次历史转换，例如 `5c13158a` 用 `_run` 与 `_task` 两个函数分别改写 `workflow_runs.micro_task_json/micro_task_fingerprint` 与 `tasks.spec_json/input_fingerprint`，其中 `input_fingerprint` 当时取自 `spec_canonical_json`。我查到的那个版本里两份 spec 的写入值相同，所以推测不会分叉，但没有核对过真实存量，也无权读日常看板。若有分叉，结果是升级拒绝并整体回滚、安装受阻，不会改坏数据。建议 Host 现在就把 `upgrade.py:34–66` 的八个核对只读地跑在日常看板的副本上，不必等四个微任务都合入之后的演练。

## 总的结论

这一步把工作流各表里的副本与死列删得等价、干净，删除路径、凭据边界与升级的原子性都守住了，封存内容未被改写；没有阻塞项，F1–F8 可作下一批的顺带项。前提是 Host 在正常环境里补跑我没能跑成的受影响测试，并在日常看板副本上先跑一遍升级核对。
