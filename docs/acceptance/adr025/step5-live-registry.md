# ADR-025 第五步 5-B1：黑板侧内存 live 登记与身份核验

本记录为第五步微任务 5-B1 的固定交付（宏任务 ADR-025，原基线 `8eb0d9c`）。本轮延续同一 run、受管检出与四路径 writeScope，核对并继续失败轮次留下的 `live_registry.py` 修改；交付仅为该模块、聚焦测试、此记录与测试编号表。公共值、角色、注册表、protocol/client/contracts、service.py/store.py/inquiry.py 均未改；不接 C-Two、不启动模型或安装版 harness、不改旧生产通道。

## 实现与身份来源

`LiveRegistry(store, channel_factory)` 提供 `attach(params)->dict`、`detach(params)->dict`、`channel_for(view)->LiveChannel|None`。帧直接复用现有 `WorkerLiveAttach`/`WorkerLiveDetach` 与同一个 `RunIdentity`；严格类型、extra/default、不可变与 JSON 由现有 InternalModel/pydantic 实现。模块对 buddy 侧只有 TYPE_CHECKING 引用，通道由 Host 的工厂注入；没有复制 C-Two 或手写另一种 channel。内存映射与懒建缓存用标准库 `threading.Lock` 保护，原因是登记与查询可能并发；它不是格式框架，全部核验只读，不新增数据库机制。

attach/detach 都在短 `db.read()` 内复用真实 `store._verify_attempt_actor`，核对 workerId/generation/nonce/有效 attempt，再要求已记录 `worker_instance` 非空且与调用者相等。NULL 是未知实例，不能采纳 caller 的新事实，也不保留无实例旧 client 的 live 兼容层。RunIdentity 的 task/attempt/generation 与 actor 必须一致；detach 即使没有映射也先过该门。attach 拒绝 finished/result 已有与 uncertain 状态；重启后必须先真实 `worker_reconcile` 再 attach，PID/address 不是采用依据。

治理身份的权威来源是 `workflow_runs.current_turn_id/current_attempt_id` 关联的本 run 当前 `workflow_turns` 行，行的 attempt_id/generation 也必须匹配。存在治理回合时，不允许以 turnId/inputSha256 双 null 绕过；旧 turn 行即使同 attempt/generation/hash 也不能替代当前回合。运行阶段 `input_sha256` 列通常为 NULL：复用 `db.sha256_text` 对服务 `_prepare_turn` 写入的规范化 `input_json` 求摘要，与 identity 核对；该来源也是实际 claim 回传的 inputSha256，不重新序列化输入。若结果回执已填摘要，摘要还必须与实际 input_json 一致。真正无治理 turn 的运行才保留 turn/hash 双 null 规则。

detach 可在本次 finished 后移除自己的映射，要求映射的完整 identity（包括 invocation/turn/hash）与 instanceId 都精确相等。正确 actor 可替换绑定并关闭旧通道，旧 full identity 或旧 endpoint instance 均不能删除新绑定。重复同一绑定幂等；回应仅含 attached/detached、attemptId、instanceId、replayed 必要事实，不回传 nonce/liveToken/address/完整 binding，不记录秘密。

每个 registry 新实例为空。映射最多 64 项，每绑定缓存一个通道；channel_for 逐次从真实 task view 的 selectedAttempt 核对 task/attempt/generation/workerId/workerInstance 与状态，拒绝 foreign/stale/terminal/uncertain 以及缺失实例的 view。工厂失败或返回 None 只表述不可达，不写停止事实；替换、摘除、构建期间被取代的通道分别以 replaced/detached/superseded 关闭。所有 DB 事务与 map 锁在实际工厂/peer 调用前释放。测试的工厂与 close 探针都核对真实业务 read/write 事务深度和 map lock，构建中 attach/detach 的重入窗口由假 peer 确定性触发。

## 本轮聚焦证据

所有测试命令固定 TMPDIR 与 BUDDY_CHECKS_TMPDIR 为 `<task-root>/t`，清除继承的 BUDDY_STATE_DIR/BUDDY_RUNTIME_ROOT/BUDDY_RUNTIME/BUDDY_RUNTIME_IDENTITY/BUDDY_WORKER_STATE/BUDDY_WORKER_ID/BUDDY_AGENT_CREDENTIAL/BUDDY_AGENT_CREDENTIAL_FILE/VIRTUAL_ENV/UV_PROJECT_ENVIRONMENT；PYTHONPATH 明确为本检出 src 与 tests/python 的绝对路径。使用 `uv run --frozen python -m unittest -v blackboard.service.test_live_registry`，零模型调用；本轮首次聚焦结果 29 项通过，3.502 秒，原始 stdout/stderr 保存于 `<task-root>/m/02-codex-gsrdmwem/green-initial.log`。

测试使用真实私有 BoardStore、既有 StoreConcurrencyTestCase/MockWorkspace 与生产 actor 核验。通用正向 identity 来自真实 governed submit/claim 的 turnId/inputSha256，明确断言运行阶段摘要列为 NULL；双 null 正向测试改为真正未治理的 command submit/claim（仅登记，不运行 argv）。直接 SQL 修改只用于隔离故障：空实例、错代次、断开 run/turn 的 attempt 关联、冲突的已填摘要；历史行只为证明不能用旧行替代当前回合。没有整体 mock `_verify_attempt_actor`。

本轮 7 个隔离单点变异 r1–r7 全部返回 exit 1，均有 unittest 断言失败，无 import/环境 ERROR；每次从同一 pristine 源文本精确替换恰一处，在独立 Python 子进程加载变异模块，真实 store 与夹具仍来自本检出，工作源码从未被变异覆盖。r1 把未知实例拒绝改为采用（attach/detach 两个子例及映射保留断言失败）；r2 恢复无 turnId 早 return（治理双 null 拒绝例失败）；r3 去掉输入摘要比较（错摘要/缺摘要两子例失败）；r4 按 caller turnId 查行（旧回合拒绝例失败）；r5 删 view workerInstance 门（真实 view 拒绝例失败）；r6 把工厂放进 db.read（事务外探针使通道为 None，正向可达断言失败）；r7 删已填摘要冲突门（冲突拒绝例失败）。测试编号、作用门和未使用单点变异项见同目录 TSV，变异源码、运行脚本、逐例原始日志与摘要 JSON 均在 `<task-root>/m/03-mutations-xt0so4rw/`。

首轮记录曾声称 NULL worker_instance 不采纳新事实、双 null 保留规则已可靠区分治理回合；首轮源码实际放行 NULL 与治理双 null，且手工插入已填摘要的 turn 行不能证明真实 claim 正向可用。本轮更正这些错误说明。任务根原有 `m/00-pristine/live_registry.py` 与 `m/01-mutations/mutate_live_registry.py`，没有找到首轮原始绿/红日志，因此首轮 21 项与 m1–m5 的成功记述不能当成本轮可复核原始证据；首轮 m5 锁内形态的挂起记述也没有保存日志，不作合格红。未删除或覆盖这些旧材料。

## Host 接线点与验证边界

本微任务交付的类尚无生产消费者。Host 在 ControlService/BoardService 初始化时设置 `store.live_registry = LiveRegistry(store, channel_factory)`；工厂接真实 WorkerRuntimeLive 客户端，使用 Worker address/liveToken，不能使用 controller 地址/token。Host 用现有 `_guard` 接两个 worker_live_attach/worker_live_detach 内部 operation；inquiry 仅经 `channel_for(view)` 访问 Worker 端点，None 诚实表达 unbound/unavailable。这些接线点及真实 Worker 端到端不在本轮范围，未声称已经运行。

完整检查、控制台、打包、安装、真实模型/安装版 harness、跨线程压力测试未运行；本轮只运行新增 registry 测试与必要单点变异。锁纪律与确定性构建竞态有聚焦证据，未将其描述为压力测试。首轮幂等/uncertain 等单点变异未重跑，当前行为由本轮对应测试覆盖；未使用已耗尽供应方重试、内部 subagent、外部 helper 或新增宏任务/微任务。

## 临时材料交接

Host 已登记的任务根记为 `<task-root>`。原有材料为 `m/00-pristine/`、`m/01-mutations/` 与 `t/` 内遗留私有测试状态/uv 锁；本轮新增 `m/02-codex-gsrdmwem/`（首轮提交源副本、首次验证时的工作差异及原始绿日志）、`m/03-mutations-xt0so4rw/`（pristine、变异源码、脚本、7 个原始红日志、results.json）以及最终验证目录（在补记中指定）。本轮 uv 缓存也仅位于这些新材料目录。任务根的最终路径清单保存为最终验证目录的 materials-final.json；清单只枚举位置，不读取凭据文件内容。Worker 未删除任何对象，既有夹具正常生命周期收尾保持；验收后由 Host 按确切根整体删除。

## 变异后最终核验

最终聚焦运行 29 项全部通过，2.982 秒，exit 0；原始日志 `<task-root>/m/04-final-xkdkfyes/green-final.log`，命令、29 个实际测试编号、四路径清单与源码 SHA-256 见同目录 verification.json。源码与隔离变异 pristine 完全相同；编号 TSV 与实际 unittest 集合一致，git diff --check 通过且从原基线至交付仅四个授权路径。最终验证材料目录为 `m/04-final-xkdkfyes/`，任务根全部路径交接清单为该目录 materials-final.json。

## 固定提交边界

实现与聚焦验证已完成，固定提交尚未产生：git add 四个授权路径时 exit 128，无法在 `<repository>/.git/worktrees/checkout/index.lock` 创建锁（Operation not permitted）。当前环境允许读取该检出外 Git 元数据但不允许写入；Worker 没有绕过权限、没有切分支或写其他检出。需 Host 在有权的受管环境提交这四路径并返回 fixed commit。未提交差异与原基线累计补丁分别保存为最终材料目录的 delivery-from-head.patch、delivery-from-baseline.patch；提交阻塞事实保存为 commit-blocker.json。
