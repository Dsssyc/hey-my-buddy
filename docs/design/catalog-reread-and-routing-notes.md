# 目录重读、评价表 revision 与路由边界

本说明对应 `128c3f6a` 上的受管检出修改，版本 0.30.0、schema 17；记录 Worker 的实现与局部验证，最终验收由 Host 完成。修改保持 ADR-027 的采信、首次缺席一小时确认、六小时保质期以及共享 180 秒读取窗口，不改存储形式、不调用模型、不启动服务或 Worker。

## 位置指纹

`candidate_snapshot` 保留候选、空位置、来源、版本管理器记录和 Node 候选的完整诊断列表；`discover` 的选取与控制台诊断仍使用这份列表。新增 `location_fingerprint` 只把存在的 harness 与 Node 候选按原有顺序、连同版本管理器记录送入哈希，健康检查使用它；原有 `scanFingerprint` 继续标识完整诊断快照。选择在哈希入口投影，是为了避免让位置缓存的判据改变诊断列表的含义。

扫描中的文件身份在 realPath、size、mtimeNs 之外加入 SHA-256 内容摘要；位置哈希排除 mtimeNs，所以单纯 touch 不重建健康记录，同大小且还原时间戳的内容修改仍被发现。版本管理器记录也读内容摘要；指向目录的 fnm 符号链接继续用链接及其目标身份。缺席的固定管理器记录仍在列表中，因此记录出现、消失或改变目标同样改变指纹。原有 `file_fingerprint` 保留命令绑定的 stat 身份，内容摘要仅用于扫描，不扩大其他命令环境或运行路径的读取。

原先截断完整候选列表和 Node 列表的 96 项上限已移到 `discover` 排序后的存在候选上，最多握手 96 个存在的文件，20 秒总时限、单次五秒时限和 12 项公开诊断上限继续生效；完整位置哈希包含全部存在候选，空目录不会挤掉后面的文件。服务的 `add_path_hint` 原来还会按目录截断前 64 项，同样会让大量空目录挤掉已有可执行位置，因此去掉该截断；单次 PATH 输入的 32768 字符边界、绝对路径筛选、去重、最新线索优先和只保留在内存的做法继续生效，未改协议中的线索清除或持久化行为。代价是扫描要对全部候选做文件检查与摘要读取，进程存活期间会保留全部去重后的 PATH 目录线索；实际安装上的扫描开销尚未测量。

已有健康行里的旧位置哈希不会被解释或迁移，升级后的第一次扫描可能重新检测一次并写入新哈希，之后使用同一个判据；这一轮相同目录读取不会再推进评价表版本。没有新增兼容读法或 schema 字段。

## 评价表变化的判据

`catalog_store.record` 在同一写事务内比较记录前后的 `evaluation_profiles` 行内容与按 adapter/provider/model 排序的完整 `catalog_pending` 行。profile 的身份、可用性及原因、启用状态、标签、能力、上下文窗口、说明都纳入比较；仅排除 `source`、`created_revision`、`updated_revision`，其中 `source` 是 `catalog:<discovery_id>` 的读取归属指针。行真正变化才写本次 `updated_revision`，新增行的创建 revision 仍是本次 revision；未变化的行保留原 revision，避免每次重读改变 `newlyDiscovered` 标记。

新增模型、模型第一次缺席、缺席超过确认期后的停用、待确认或已停用模型恢复、模型仍在但努力档位消失，以及 metadata 或可用原因变化，都会推进一次 table revision。重复的待确认缺席在一小时内不推进，已确认停用后继续缺席也不推进；相同 discovery_id 在确认期结束后仍可改变表，因此不能用 discovery_id 相同直接跳过处理。一次读取涉及多个 harness 时仍只发布一次表修订，修订里的 `profiles` 数量改为实际内容变化的行数，只有 pending 改变时这个数量可以是 0。

采信与版本判据各自执行：不被采信的读取仍记录 unknown 观察并保留已有目录，不更新确认读取时间；如果没有改变 profile 或 pending，不发布修订或任何事件。ADR-027 现有的保留目录恢复路径仍执行，所以一次 unknown 读取若恢复了先前因健康故障被置为不可用的行，也会推进 revision。晚到的读取仍受 observation 次序及健康 generation fencing 约束，不覆盖较新的事实。

无变化读取仍写 `evaluation_catalog`（新内容身份时）、`catalog_observations` 的完成时间与响应、`catalog_current` 的观察归属及更新时间；可信读取还写 `catalog-read-at` 与账户确认事实。它不修改 `evaluation_state.table_revision` 或 `updated_at`，不写 `evaluation_revisions`，不发 `evaluation.catalog_discovered`、模型转换事件或 `_notify`。只改变读取归属、catalogState/catalogReason 等观察状态不算评价表变化，这些状态仍可通过读接口与控制台轮询观察。

核对 `canonical_payload` 及四个 harness 的目录构造后，目录中的每次变化时间是 `discoveredAt`，它已经从 discovery_id 输入排除；native quota、billing 的 `observedAt`、账户身份与额外未知字段不会进入 canonical catalog。ZCode 的 billing 观察时间仍按每次可信读取更新健康记录，它不写 profile 内容。`harnessVersion`、`providerVersion`、provider package metadata、warnings、source 和 discovery reason 仍可能让 discovery_id 不同；本次不改变它们的观察身份，只比较实际写入的表，所以这些字段单独变化不会推进表版本。若它们改变了 profile 的说明、能力或其他内容，比较仍会发现。

## revision 的所有消费者

| 消费者 | 现有依据与本次影响 |
| --- | --- |
| 路由冻结 | `DecisionCoordinator._request` 冻结当时的 `tableRevision` 并写入 `evaluation_decisions` 及 Router 数据包；无变化读取保留这个值，有变化读取仍使旧包失效，候选与设置冻结规则不改。 |
| 派发准备 | `_frozen_circumstances_problem` 仍比较当前表与冻结的 baseInput revision；无变化读取通过，实际变化仍以 `router-table-changed` 交给 Host。 |
| 认领 | `DecisionCoordinator.claim` 的独立 revision 检查继续生效；测试同时覆盖正常认领路径和不经派发准备直接调用这一道检查的路径。 |
| 路由结果及审计 | 决策视图、workflow 的路由/回合绑定、Host routingBoundary 都读取当时冻结的 revision，不重写历史记录；两个检查和 Host 投影共享下述一句理由。 |
| 控制台变更与草稿 | `console_core`/snapshot、model profiles、维护准备包公开当前表 revision，`use-editor` 用它识别草稿冲突；重复读取不再制造草稿冲突，实际改变仍触发。HTTP 缓存通过持久连接的 `PRAGMA data_version` 观察所有数据库提交，观察状态和读取时间可以更新，不依赖新增 revision 或事件。 |
| 更新记录 | `EvaluationStore.history` 直接按 revision 分页读取 `evaluation_revisions`；现在只出现有内容改变的 catalog 修订，无变化观察去 `catalog_observations` 查，既有修订全部保留。 |
| 评价与用户设置并发 | writer begin/renew/grant、reader begin、`assessment_publish` 与 `user_policy_publish` 保留当前 revision/expectedRevision 和 generation 校验；无变化目录读取不会让已取得的 writer grant 失效，真实改变仍冲突，人工及维护发布的 revision 行为未改。新增回归分别验证两种发布 grant。 |
| 证据及维护审计 | evidence 的 recorded_revision、事件关联 revision、card history 的 table_revision、维护准备响应继续标记发布时所见的表版本；本次不改证据计数、卡片修订或维护发布。 |
| 升级 | `upgrade`/backup 使用数据库内容指纹验证，schema 16 → 17 的迁移保留 evaluation_state 与 evaluation_revisions；本次没有改这些表形状或迁移。`upgrade.json` 对健康和 catalog 发布的写入 fence 仍先于修改，新旧位置哈希的切换通过正常扫描完成。 |

## Host 的回应

`router-table-changed` 的理由统一为“评价表在请求冻结之后已变化；请由 Host 显式 reroute”。两个检查复用 `TABLE_CHANGED_REASON`；Host 边界构造只对这一代码取消通用的“取消后重新提交没有用”后缀，其他边界的理由与命令模板不变，没有自动重新路由。公开投影也有精确理由的回归断言。

## 验证记录与 Host 后续验证

最终源码的 13 个受影响测试文件共 341 项通过，命令退出码 0，耗时 38.031 秒；`git diff --check` 通过。测试覆盖路径空位置顺序与长 PATH、候选出现/同大小改字节并恢复时间戳/消失、存在候选换序、管理器记录改变、邻接 Node 改变、服务交替线索不重建健康与目录不重读、观察/确认时间、metadata、增减模型及确认/恢复、未采信和晚到读取、努力档位停用、冻结与两道检查、Host 边界、故障切换以及两类并发发布。本地测试使用 Host 指定的现成只读解释器，源代码优先，清除继承的 runtime/Worker/agent 凭据变量，禁用 pyc 写入，未使用 uv 或安装依赖。

运行模块如下，实际命令是在仓库根目录以 `env -u BUDDY_STATE_DIR -u BUDDY_RUNTIME_ROOT -u BUDDY_RUNTIME -u BUDDY_RUNTIME_IDENTITY -u BUDDY_WORKER_STATE -u BUDDY_WORKER_ID -u BUDDY_AGENT_CREDENTIAL -u BUDDY_AGENT_CREDENTIAL_FILE -u VIRTUAL_ENV -u UV_PROJECT_ENVIRONMENT PYTHONPATH=src:tests/python PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m unittest` 加上下列模块；`$PYTHON` 指向任务给出的现成解释器。

```text
buddy.harnesses.test_harness_discovery
blackboard.service.test_harness_health
blackboard.service.test_harness_service
protocol.test_billing_quota
blackboard.catalog.test_catalog_observations
blackboard.catalog.test_catalog_trust
blackboard.catalog.test_catalog
blackboard.routing.test_catalog_trust
blackboard.routing.test_router_dispatch
blackboard.routing.test_router_boundary
blackboard.routing.test_router_boundary_integration
blackboard.routing.test_router_failover_integration
blackboard.evaluation.test_evaluation
```

这些文件全部能使用私有 SQLite、进程内服务与合成 CLI 跑完，没有因沙盒跳过的受影响测试。DSH discovery 测试中“stop unconfirmed; private directory retained”是故意保留未确认停止证据的 fixture 诊断，整个文件通过；未将这条输出当成真实 harness 验证。首次验证失败来自旧测试快照缺少新位置投影需要的结构、一个测试调用了错误的 lifecycle 方法名，以及复用同一个私有看板的 writer requestId；都已修正并在最终测试中通过，没有保留旧的“每次读取推进 revision”断言或注释。

完整 check suite、安装/冷启动及真实 harness 回合与故障注入留给 Host，不由 Worker 执行；需要真实启动器、服务或共享内存的 `tests/python/install/test_launcher.py`、`tests/python/install/test_upgrade_service.py` 与 `tests/python/buddy/harnesses/test_c_two_live.py` 由 Host 在其允许这些能力的环境运行。Frontend 和浏览器测试本批未运行，未修改 `apps/console`。Host 仍需在私有服务重跑已有 PATH 交替实验，并核对首次采用目录后的 revision 保持、没有新的目录 observation、健康 revision 没有被线索变化推进；超过六小时或强制刷新可以产生新 observation，但相同内容仍不改变 table revision。源码修改不证明已安装服务的行为，验收应绑定实际候选安装。

另一个无需模型或服务的验证办法如下：在候选检出根目录运行，使用已清理继承凭据的 Python 环境及 `PYTHONPATH=src`，只创建临时 SQLite 看板，用合成健康行与可信目录读取驱动生产 `record`；不调用原生发现或账号读取。这个脚本本地已执行，输出为 `tableRevisions: [1, 1, 2]`、修订 `(1, catalog)` 与 `(2, catalog)`，目录事件总数 2。

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.evaluation.evaluation import EvaluationStore
from hey_my_buddy.blackboard.catalog.catalog_store import record

def reading(label):
    return {'source': 'host-validation-fixture',
            'discoveries': [{'adapter': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}],
            'providers': [{'adapter': 'dsh', 'provider': 'fixture', 'models': [
                {'id': 'alpha', 'name': label, 'efforts': ['max']}]}]}

with TemporaryDirectory(prefix='catalog-change-check-') as directory:
    board = BoardStore(Path(directory))
    board.initialize()
    evaluation = EvaluationStore(board)
    with board.db.write() as db:
        db.execute("INSERT INTO harness_health(adapter,status,record_json) VALUES('dsh','ready','{}')")
    versions = [record(evaluation, reading(label))['tableRevision']
                for label in ('Alpha', 'Alpha', 'Alpha changed')]
    with board.db.read() as db:
        history = [tuple(row) for row in db.execute(
            'SELECT revision,kind FROM evaluation_revisions ORDER BY revision')]
        events = [row[0] for row in db.execute(
            "SELECT kind FROM events WHERE kind='evaluation.catalog_discovered'")]
    print({'tableRevisions': versions, 'history': history, 'catalogEvents': len(events)})
    assert versions == [1, 1, 2]
    assert history == [(1, 'catalog'), (2, 'catalog')]
    assert len(events) == 2
```

## 留给 Host 的文档更新

`docs/reference/harnesses.md` 的 Local discovery / Health and execution，以及 `docs/reference/operations.md` 的 Harness discovery 中以 real target、size、modification time 描述服务位置判据的段落需要区分新的内容与存在候选顺序哈希和原有命令 stat 绑定；补充空位置不会引起重读。`docs/reference/architecture.md` 的 Local discovery and installation 可补充同一语义。

`docs/reference/evaluation.md` 的 Discover and enable profiles、更新记录与草稿并发说明需要明确“读取观察与内容修订不同”：每次照常记录观察，实际表内容改变才发布目录修订，来源指针不算改变。`docs/reference/decision.md` 的共享冻结与 Host 边界说明需要补充 tableRevision 的内容含义及 `router-table-changed` 的显式 reroute 理由。这些页未改；README 与共享 skill 没有找到本批需要删除的“每次读取都加一”文字，不因这次修改扩大它们的内容。ADR-027 规则本身未变。
