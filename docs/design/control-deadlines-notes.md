# 控制面的时限——慢不等于坏

基点为 `cbb5ffc6`，本文件记录源码候选的控制等待、修改范围和实际检查；安装态与真实 harness CLI 或 app 的忙机耗时不在本次验证内，Host 另行测量并验收。

固定窗口只结束一次观察，不能凭经过时间判 harness CLI 不可用、模型目录消失或微任务失败。发现的 5 秒单次探测与 20 秒总扫描到点为 unknown，健康扫描保留原状态和已知事实；Codex CLI 的 8 秒账户窗口到点记录未知且保留尚未被新事实替换的计费；四个 harness CLI 的 25 秒原生目录窗口和 30 秒外层窗口到点返回未知目录，不覆盖最后可信目录。Claude Code CLI 不缓存未知元数据为不可用，下一次读取会重试。停止未确认、明确原生拒绝、进程退出和输入不安全各按自己的事实处理。

Router 从输入准备之前的 `started` 起算 `started + document.budget.timeoutSeconds`，冻结验证、Git 查询、阻塞 blob 读取、材料化与摘要共用该期限；原生请求只拿剩余整数秒。原绝对期限经 Router seam、private controller control frame 的 `deadlineMonotonic` 传至 controller setup 后和 native driver，启动阶段不能重置时钟；已经耗尽时不进入 native 运行，以正面的未创建子进程证据结算 deadline，并由黑板调度下一 Router。执行后的输入核验也绑定 handle 保存的同一原期限；耗尽时 `inputVerification.unchanged` 为 null、code 为 router-budget-exhausted，保留镜像和实际停机证据，不推断输入已变。回执的 elapsedMs 至少包含从准备开始的总耗时。普通 Worker 没有这个硬预算。Router 的预算值、收尾宽限、各执行器的停止与排空窗口没有增加。

DSH CLI 的初始化、开会话、续接、配置和正常逻辑 close 都等待同一次协议请求的事实，使用执行期限；普通 Worker 无硬预算时可继续等待，Router 和模型目录仍受原预算限制。显式中断后的 close 保留原 30 秒窗口，实际进程回收的 settle、drain、abandon 与 terminate 宽限保持原值。协议明确拒绝、连接实际结束与停止未确认仍单独报告。

工作区 Git、运行时依赖安装、本地 IPC 自检和服务启动的独立短失败上限已移除，普通操作依据进程退出、明确错误或就绪事实结束；Router 内的 Git 仍受本次硬预算。删除授权有效期、所有权 lease、安全凭证有效期、升级互斥锁与收尾窗口保留：到点不提交不安全的删除/升级，不把 lease 过期或未获停止证据推断为进程已停。

清点覆盖全部 `src/hey_my_buddy/**/*.py` 的 timeout、deadline、TTL、expiry、monotonic、wait、sleep、SQLite busy 与秒数常量，逐处追到消费分支；打包前端的产品计时以对应 `apps/console/src` 标明，依赖内部调度另列。位置以函数/调用点和本次源行为为准，定义与同一等待的消费点合并，独立等待分别列行。“等待”与“等待/未知”是只结束等待的一类，“判结论”是原先到点改变结果的一类；后者的保留项均给出预算、安全或明确资源拒绝的依据。不把输出大小、容量数量或纯 elapsed 测量计入时限。

| 类别 | 位置（相对 `src/hey_my_buddy/`，前端另注明） | 时长 | 原来 | 现在 | 为什么 |
| --- | --- | --- | --- | --- | --- |
| 判结论 | `blackboard/routing/router.py:16` | brief 60 秒 | Router 硬预算耗尽，候选回答不能发布为成功 | Router 硬预算耗尽，候选回答不能发布为成功 | Host 指定保留的 Router 预算 |
| 判结论 | `blackboard/routing/router.py:17` | standard 300 秒，默认 preset | 同上 | 同上 | 同上 |
| 判结论 | `blackboard/routing/router.py:18` | deep 600 秒 | 同上 | 同上 | 同上 |
| 判结论 | `blackboard/routing/router.py:22` | fast 60 秒 | fast Router 输入的预算 | fast Router 输入的预算 | 同上 |
| 判结论 | `blackboard/routing/decision.py:66,678` | Router 预算外加 10 秒 | 给执行收尾；发布端仍检查冻结预算 | 给执行收尾；发布端仍检查冻结预算 | 保留停止证据，不能扩大可接受答案窗口 |
| 判结论 | `buddy/roles/router.py:40` | Router 原生调用 60 秒 | 原生超时后无有效回答 | 原生超时后无有效回答 | Router 快速调用硬界限 |
| 判结论 | `buddy/roles/router.py:28-33,61-115` | review 从已选 Router preset 的 60/300/600 秒计算剩余预算 | 材料化之后才起算原生调用预算，输入等待有独立失败上限 | 材料化前起算唯一绝对期限，传至 controller 与原生运行，耗尽走 Router deadline 和下一候选 | ADR-022 的 Router 硬预算；包含准备与启动耗时，收尾宽限保留 |
| 等待/未知 | `buddy/roles/router.py:23,166` | 取消 grace 12 秒 | 超过进入强制停机，记录停机事实 | 超过进入强制停机，记录停机事实 | 回收期不证明任务完成 |
| 判结论 | `buddy/roles/router_input.py:14-19,70-166` | 原 Git 30 秒、材料化 120 秒；现为 Router 剩余预算 | 独立上限到点报 router-input-unavailable | Git、阻塞 blob 读取、验证和摘要均用 Router 期限；耗尽报 router-budget-exhausted | 输入慢不能证明输入不可用；只有 Git 真实错误、非零退出及输入校验拒绝判不可用 |
| 判结论，保留 Router 预算 | `buddy/roles/router.py:DecisionAdapter.collect`、`buddy/roles/router_input.py:verify` | 从准备开始的同一原绝对期限，没有新窗口 | 执行后核验没有绑定预算，Git 和摘要可阻塞；核验错误统一记 input-changed | 原期限耗尽记未知输入和 router-budget-exhausted，保留镜像；下一 Router 沿现有预算规则调度 | 硬预算不能在执行后重置；没有变化证据不能判输入已变 |
| 等待/未知 | `buddy/roles/router_input.py:158` | `cat-file` 收尾等 5 秒 | 之后 kill 再 wait | 之后 kill 再 wait | 子进程回收，不证明材料化成功 |
| 等待 | `blackboard/routing/router_settings.py:13-20` | retry interval 默认 600 秒，用户配置范围 1–2,147,483,647 秒 | 候选跳过到 `skipUntil` 后重试 | 候选跳过到 `skipUntil` 后重试 | 用户参数非执行失败时限 |
| 等待 | `blackboard/routing/quota_routing.py:22` | quota retry 3600 秒 | 到 `eligibleAt` 再考虑候选 | 到 `eligibleAt` 再考虑候选 | 配额避让窗口 |
| 判结论 | `blackboard/catalog/catalog.py:37` | 缺席二次确认 3600 秒 | 连续缺席才使 catalog 判不可用 | 连续缺席才使 catalog 判不可用 | 降低瞬时缺席误判；不能把单次超时当缺席 |
| 等待/未知 | `blackboard/catalog/catalog.py:41` | catalog shelf 21600 秒 | 到点刷新过期 catalog | 到点刷新过期 catalog | 缓存新鲜度 |
| 等待/未知 | `blackboard/catalog/catalog.py:63,88-107` | 并发重读 join 55 秒 | 到点返回 `timeout`，再按当前 board 事实判断 | 到点返回 `timeout`，再按当前 board 事实判断 | 不证明重读线程停了；风险在下游误当终局 |
| 等待 | `blackboard/service/harness_health.py:15` | scan 节拍 180 秒 | 触发下一次探测 | 触发下一次探测 | 健康事实新鲜度 |
| 等待/未知 | `blackboard/service/harness_health.py:16` | ready TTL 3600 秒 | 已知 ready 状态过期后再看探测事实 | 已知 ready 状态过期后再看探测事实 | 只触发重读；未知观察保留最后已知健康状态 |
| 等待/未知 | `blackboard/service/harness_health.py:434` | close join 55 秒 | 等待健康扫描线程结束，超时线程可仍在运行 | 等待健康扫描线程结束，超时线程可仍在运行 | 不可用作停止证明 |
| 等待/未知 | `buddy/roles/run_execution.py:782` | native model discovery 25 秒 | 原生 discovery deadline 经 controller 被判 HARNESS_UNAVAILABLE | controller 保留 deadline 码并返回 unknown；目录保留最后可信读取，后续可再读 | 25 秒只结束本次元数据等待，不是 harness CLI 失效证据 |
| 等待/未知；未证实停机判结论 | `buddy/roles/run_execution.py:799-816` | 外层 controller wait 30 秒 | 30 秒未返回即终止并统一判 HARNESS_UNAVAILABLE | 停止已确认时返回 unknown 目录；停止未确认时单列 HARNESS_DISCOVERY_STOP_UNCONFIRMED | 等待耗尽不判不可用；未知停止仍需安全拒绝，8 秒回收宽限不变 |
| 等待/未知 | `buddy/roles/run_execution.py:800` | discovery terminate grace 8 秒 | 停机证据决定能否清理临时目录 | 停机证据决定能否清理临时目录 | 保留 ownership 证据 |
| 等待/未知 | `buddy/harnesses/discovery.py:28,478,516,648-682` | 单候选探测 5 秒 | timeout 或 scan-timeout 被记为 unhealthy | 记 unknown；健康服务保留原状态、已知命令和目录可用性；Node 的未知不会被旧失败码覆盖 | 探测窗口结束不能证明 CLI 坏；真实退出、明确登录拒绝和停止未确认分别处理 |
| 等待/未知 | `buddy/harnesses/discovery.py:29,724-780` | 总扫描 20 秒 | timeout 或 scan-timeout 被记为 unhealthy | 记 unknown；健康服务保留原状态、已知命令和目录可用性；Node 的未知不会被旧失败码覆盖 | 探测窗口结束不能证明 CLI 坏；真实退出、明确登录拒绝和停止未确认分别处理 |
| 等待/未知 | `buddy/harnesses/discovery.py:377,395` | 被终止后 pipe 排空 0.5 秒 | 结束后仍有持有者则 containment 未证实 | 结束后仍有持有者则 containment 未证实 | 停机证明界限 |
| 等待/未知 | `buddy/harnesses/discovery.py:388` | 进程 wait 最低 0.01 秒，其余取总 deadline | 超时归 `timeout` 并回收 | 结束本次探测、回收自己创建的子进程；确认停止后 timeout 为 unknown | 不从回收信号造成的退出反推原生故障 |
| 等待 | `buddy/harnesses/discovery.py:339,442` | 轮询 0.01 秒 | 观察子进程与 pipe | 观察子进程与 pipe | 节拍不产生独立状态 |
| 等待/未知 | `buddy/harnesses/discovery.py:450` | Windows `taskkill` 1 秒 | 到点后忽略异常、再 kill 本进程 | 到点后忽略异常、再 kill 本进程 | 强制树停机仍未证明 |
| 等待/未知 | `buddy/harnesses/version.py:32` | 版本探测最多 5 秒 | 影响本次版本证据 | 影响本次版本证据 | 版本是辅助证据，不能覆盖 harness 运行事实 |
| 等待/未知 | `buddy/harnesses/codex/account_probe.py:31` | 账户探测 8 秒 | 原生 deadline 向服务传播，记录 account_read_failed，未知计费可替换原计费 | 记 account_read_unknown 与 status=unknown；健康状态不变；未读到账户时保留已知计费，读到的部分事实仍采纳 | Codex CLI 账户和额度是观察，之后扫描照常重试；停止未确认仍按安全侧拒绝 |
| 等待/未知 | `blackboard/evaluation/model_facts.py:34,121` | 远程 model facts 获取 10 秒 | 获取失败，旧/缺席资料按调用方处理 | 获取失败，旧/缺席资料按调用方处理 | 网络资料辅助判断，非 harness 状态 |
| 等待 | `buddy/harnesses/claude/adapter.py:25,140` | metadata cache 60 秒 | 到期重新读取 metadata | 到期重新读取 metadata | 缓存新鲜度 |
| 判结论 | `buddy/harnesses/base.py:138` | `NoToolStructuredRequest` 默认 60 秒 | 未显式覆盖时限制结构化调用 | 未显式覆盖时限制结构化调用 | 默认原生预算；实际 Router 也传 60 秒 |
| 判结论 | `buddy/roles/run_execution.py:514` | no-tool request 最大 60 秒 | 超出上限拒绝启动 | 超出上限拒绝启动 | 控制输入验证边界 |
| 判结论 | `buddy/harnesses/controller.py:66-67` | 调用方 `timeout_seconds + grace_seconds` | controller handle 总截止时间 | controller handle 总截止时间 | 参数来源需按每个调用方判断，非全局固定值 |
| 判结论 | `buddy/harnesses/run_contract.py:171` | 原生 run timeout 参数 1–86400 秒或 None | 有值为执行硬截止；None 无总截止 | 有值为执行硬截止；None 无总截止 | Router/discovery 使用有值，Worker 普通任务可无值 |
| 等待 | `protocol/schemas.py:24-26,160`；`blackboard/store/upgrade.py:17` | `expectedSeconds` 默认 1800 秒，允许 1–86400 秒 | 期望时长，仅供规划/展示；不设 Worker 执行截止 | 期望时长，仅供规划/展示；不设 Worker 执行截止 | 保留用户预期与实际硬预算的语义区别 |
| 判结论 | `buddy/harnesses/native_support.py:45-52` | 从请求预算算 monotonic deadline | 四个原生 harness 的共享总截止规则 | 四个原生 harness 的共享总截止规则 | Router 预算及 discovery 上限应区分 |
| 等待 | `buddy/harnesses/native_support.py:76-81` | cancel flag 轮询最多每 0.05 秒 | 只观察取消或给定 timeout | 只观察取消或给定 timeout | 不自行定性执行 |
| 等待/未知 | `buddy/harnesses/native_support.py:131` | 停机前 wait 最多 3 秒 | 检查进程是否自然退出 | 检查进程是否自然退出 | 仍需实际停机证据 |
| 等待/未知 | `buddy/harnesses/base.py:21,329-333` | command stop grace 3 秒 | 超过进入强制停机 | 超过进入强制停机 | 回收，不等于已停 |
| 等待/未知 | `buddy/harnesses/base.py:340-344` | 进程输出排空最多 2 秒 | 结束输出观察 | 结束输出观察 | 后续仍查进程结果 |
| 等待/未知 | `buddy/harnesses/base.py:356-365` | settle_seconds 由调用方给出，轮询 0.025 秒 | 观察退出 | 观察退出 | 动态收尾窗口 |
| 等待/未知 | `buddy/runtime/command.py:16` | terminate grace 3 秒 | 命令任务停止宽限 | 命令任务停止宽限 | 子进程回收 |
| 判结论 | `buddy/runtime/command.py:69-70` | `context.timeout_seconds`，来源 `routerBudgetSeconds` 或 None | 有预算才设置 handle deadline | 有预算才设置 handle deadline | 普通 Worker 期望时长不等于截止时间 |
| 等待 | `buddy/runtime/worker.py:27,894` | retry 默认 120 秒 | 服务错误后退避重试 | 服务错误后退避重试 | 避免快速重试 |
| 等待 | `buddy/runtime/worker.py:28,442,449` | 空 claim 2 秒 | 再查任务 | 再查任务 | 轮询节拍 |
| 等待/未知 | `buddy/runtime/worker.py:554` | 退出后 handle wait 15 秒 | 后续记录运行/停止事实 | 后续记录运行/停止事实 | 不能由 wait 结束推断子进程已停 |
| 等待 | `buddy/runtime/worker.py:794` | 执行监视每 0.25 秒 | 查 controller 完成/取消/预算 | 查 controller 完成/取消/预算 | 观察节拍 |
| 等待/未知 | `buddy/runtime/worker.py:805` | 预算或取消后 wait 10 秒 | 给 controller 收尾 | 给 controller 收尾 | 停机仍取实际证据 |
| 等待/未知 | `buddy/runtime/worker.py:809` | lease renewal thread join 2 秒 | 回收线程 | 回收线程 | 不证明线程已停止 |
| 等待 | `buddy/runtime/worker.py:925` | 错误重试 sleep 1 秒 | 退避过程检查 | 退避过程检查 | 节拍 |
| 等待 | `buddy/runtime/worker.py:950,956` | 取消轮询 2 秒 | 检查 stop 并续 lease | 检查 stop 并续 lease | 观察节拍 |
| 等待 | `buddy/runtime/supervisor.py:22` | restart backoff 初值 2 秒 | 延后重启 Worker | 延后重启 Worker | 避免重启风暴 |
| 等待 | `buddy/runtime/supervisor.py:23` | restart backoff 最大 30 秒 | 同上 | 同上 | 同上 |
| 等待/未知 | `buddy/runtime/windows_process.py:222` | Windows 进程 wait 2 秒 | 回收观察；超时保留未确认 | 回收观察；超时保留未确认 | 平台停机证据 |
| 等待/未知 | `blackboard/service/daemon.py:38,695,726` | drain 默认 10 秒 | 停服务时尝试排空，返回未解决 attempt | 停服务时尝试排空，返回未解决 attempt | 不把仍运行视为已停 |
| 等待 | `blackboard/service/daemon.py:39,685` | lease sweep 15 秒 | 下次扫描超期 lease | 下次扫描超期 lease | 恢复节拍 |
| 等待 | `blackboard/service/daemon.py:210` | spawn backoff 初值 5 秒 | Worker 启动失败后延后重试 | Worker 启动失败后延后重试 | 避免启动风暴 |
| 等待 | `blackboard/service/daemon.py:211` | spawn backoff 最大 60 秒 | 同上 | 同上 | 同上 |
| 等待 | `blackboard/service/daemon.py:213` | replay backoff 5 秒 | 延后重放尝试 | 延后重放尝试 | 防快速循环 |
| 等待/未知 | `blackboard/service/daemon.py:487-491` | 等待 Worker 退出：参数 timeout 剩余量 | 返回未停止者以便处理 | 返回未停止者以便处理 | 不能把 join 超时当退出 |
| 等待/未知 | `blackboard/service/daemon.py:783-788` | drain 参数，轮询 0.25 秒 | 到点返回 unresolved | 到点返回 unresolved | 保留实际状态 |
| 等待/未知 | `blackboard/store/store.py:62,101,1907-1910` | lease 默认 120 秒，允许 15–3600 秒 | 到期 ownership 变 `uncertain`，不证明停机 | 到期 ownership 变 `uncertain`，不证明停机 | owner fencing 与恢复安全 |
| 等待/未知 | `blackboard/store/store.py:3064-3096` | lease sweep 可传 grace 秒 | 超期后标记 uncertain | 超期后标记 uncertain | 宽限为恢复参数 |
| 等待 | `blackboard/store/store.py:188-216` | events.wait 默认/上限 30 秒 | 到点返回当前页，stop 可 `WAIT_ABANDONED` | 到点返回当前页，stop 可 `WAIT_ABANDONED` | 长轮询不改任务 |
| 等待 | `blackboard/store/store.py:216` | events.wait 内部重查最多 1 秒 | 检查新事件及停止 | 检查新事件及停止 | 响应节拍 |
| 等待 | `blackboard/store/store.py:2960-2979` | message.wait 默认/上限 30 秒 | 返回当前消息状态 | 返回当前消息状态 | 无答仍无答 |
| 等待 | `blackboard/store/store.py:2975` | message.wait 内部最多 1 秒 | 重查消息状态 | 重查消息状态 | 响应节拍 |
| 等待 | `blackboard/service/service.py:837-864` | task.wait 默认/上限 30 秒 | 返回当前 task view | 返回当前 task view | 长轮询不改任务 |
| 等待 | `blackboard/service/service.py:859` | task.wait 内部最多 1 秒 | 重查 task view | 重查 task view | 响应节拍 |
| 等待 | `protocol/client.py:95` | task.wait 客户端默认 30000 毫秒 | 传给服务 wait | 传给服务 wait | 默认参数，与服务上限一致 |
| 等待 | `protocol/client.py:288` | message.wait 客户端默认 30000 毫秒 | 同上 | 同上 | 同上 |
| 等待 | `protocol/client.py:297` | events.wait 客户端默认 30000 毫秒 | 同上 | 同上 | 同上 |
| 等待/未知 | `protocol/inquiry.py:2-4` | live 传输默认 1500 毫秒，合法 100–5000 毫秒 | 一次 native C-Two request/observe 到点返回 timeout 事实 | 一次 native C-Two request/observe 到点返回 timeout 事实 | 单次问询不可推断整个任务失败 |
| 等待 | `blackboard/tasks/inquiry.py:23,257-272` | inquiry wait 最多 30000 毫秒 | 到点返回当前问答事实 | 到点返回当前问答事实 | 无回答不等于执行失败 |
| 等待 | `blackboard/tasks/inquiry.py:26,272` | inquiry poll 0.3 秒 | 重读 correlated answer | 重读 correlated answer | 节拍 |
| 等待/未知 | `buddy/harnesses/c_two_live.py:514,521` | 队列等待从请求 timeout 参数继承 | 到点留下传输不可用事实 | 到点留下传输不可用事实 | 与 request deadline 同源 |
| 等待/未知 | `buddy/harnesses/c_two_live.py:789` | slot done 等剩余 deadline | 超时形成 transport failure/unknown | 超时形成 transport failure/unknown | 不推断 native 执行结束 |
| 等待/未知 | `buddy/harnesses/c_two_live.py:1052,1110-1116` | native connect/call 同一 100–5000 毫秒窗口 | 连接或调用超时，报告对应 transport 错误 | 连接或调用超时，报告对应 transport 错误 | 不把单次观察当任务终局 |
| 等待/未知 | `buddy/harnesses/inquiry_bridge.py:154` | bridge thread join 1 秒 | 到点线程仍可能存活 | 到点线程仍可能存活 | 收尾证据 |
| 等待 | `protocol/activity.py:53,199-213` | 活动发布最小间隔默认 2 秒 | 同 phase 事件节流 | 同 phase 事件节流 | 不改任务状态 |
| 判结论 | `protocol/transport.py:296,314` | loopback listener 1 秒 | 单次身份握手超时，服务调用失败 | 取消独立 1 秒上限，等待本线程已监听的本地 socket 连接/接受事实 | 慢本地握手不能推断沙盒禁止访问；OS 明确拒绝仍报访问错误 |
| 判结论 | `protocol/transport.py:297,315` | loopback connector 1 秒 | 同上 | 取消独立 1 秒上限，等待本线程已监听的本地 socket 连接/接受事实 | 慢本地握手不能推断沙盒禁止访问；OS 明确拒绝仍报访问错误 |
| 判结论 | `protocol/transport.py:421` | installed service cold start 300 秒 | 到点 `SERVICE_START_TIMEOUT` | 取消固定启动失败上限；等服务 ping 就绪或实际子进程退出（包括零退出） | 慢启动维持未知；等待不删除活动 owner 的端点，不停止服务 |
| 判结论 | `protocol/transport.py:421` | source service cold start 20 秒 | 同上 | 取消固定启动失败上限；等服务 ping 就绪或实际子进程退出（包括零退出） | 慢启动维持未知；等待不删除活动 owner 的端点，不停止服务 |
| 等待 | `protocol/transport.py:435` | 启动 poll 0.05 秒 | 检查 ready/child exit | 检查 ready/child exit | 节拍 |
| 等待/未知 | `protocol/transport.py:478` | stop drain 默认 10 秒 | 传给服务 stop | 传给服务 stop | 仅默认参数 |
| 等待 | `cli/blocking.py:38-41,477-543` | await 默认 86400 秒，合法 1–86400 秒 | 到点 `wait-timeout`，原 run 继续 | 到点 `wait-timeout`，原 run 继续 | Host 可重复 await 同一 run |
| 等待 | `cli/blocking.py:41,520` | RPC wait 分片最多 30000 毫秒 | 每片重查 run | 每片重查 run | 长等待不占单个 RPC |
| 等待 | `cli/console_cli.py:35,189` | CLI console 启动 poll 默认 2 秒 | 等待 console 可用 | 等待 console 可用 | 可由参数覆盖 |
| 判结论 | `console/console_sessions.py:16,81-87` | entry link 600 秒 | 过期拒绝入场 | 过期拒绝入场 | 一次性入口凭证保留 |
| 判结论 | `console/console_sessions.py:18` | cookie Max-Age 400 天 | 浏览器丢弃 cookie；服务器会话不按年龄过期 | 浏览器丢弃 cookie；服务器会话不按年龄过期 | 浏览器持久性设置 |
| 等待 | `console/read_cache.py:64` | quota observedAt 陈旧界限 3600 秒 | 失效时间派生投影 | 失效时间派生投影 | 页面事实新鲜度 |
| 等待 | `console/read_cache.py:69` | read cache 最长 3600 秒 | 到点重新生成 | 到点重新生成 | 防漏掉时间边界 |
| 判结论 | `console/read_cache.py:161` | read cache SQLite busy 10 秒 | 仍被锁住则报数据库读错误 | 仍被锁住则报数据库读错误 | SQLite 明确报告锁争用或错误时不提交事务；它是资源拒绝事实，不改变 harness/任务为坏状态 |
| 等待 | `console/server.py:54` | session stamp 60 秒 | 会话投影更新 | 会话投影更新 | UI 新鲜度 |
| 等待 | `console/server.py:376` | expiry sweep 5 秒 | 扫过期 ticket | 扫过期 ticket | 节拍 |
| 等待 | `console/server.py:584` | snapshot wait 30 秒 | 到点返回现有投影 | 到点返回现有投影 | 页面长轮询 |
| 等待 | `console/reads.py:59` | snapshot 最迟 health scan 180 秒边界 | 到点刷新 harness 信息 | 到点刷新 harness 信息 | 防 idle console 饿死扫描 |
| 等待 | `blackboard/tasks/macro_task_summary_cache.py:21,75` | 摘要 cache 最长 3600 秒 | 到点重算 | 到点重算 | 界定陈旧摘要 |
| 判结论 | `blackboard/store/db.py:1039` | SQLite `busy_timeout` 10000 毫秒 | 争用超时报数据库错误 | 争用超时报数据库错误 | SQLite 明确报告锁争用或错误时不提交事务；它是资源拒绝事实，不改变 harness/任务为坏状态 |
| 判结论 | `blackboard/store/db.py:1045` | SQLite read/write connect timeout 10 秒 | 连接仍忙时报错误 | 连接仍忙时报错误 | Python sqlite 默认 busy 界限的显式设置 |
| 判结论 | `blackboard/store/db.py:1084` | SQLite read-only connect timeout 10 秒 | 同上 | 同上 | 同上，独立连接路径 |
| 判结论 | `blackboard/tasks/workflow.py:106,3610-3619` | Host wrap-up 300 秒 | `graceDeadline` 到点可成 `grace-expired` | `graceDeadline` 到点可成 `grace-expired` | 主动停止后的有界收尾，保留 |
| 等待/未知 | `buddy/roles/run_controller.py:114-160` | 单次 wrap-up 等待最多 86400 秒且受剩余 grace 限制 | 到点只检查是否已写摘要 | 到点只检查是否已写摘要 | 不能把超时视作摘要交付 |
| 判结论 | `blackboard/tasks/workflow.py:113,5332` | cleanup plan TTL 900 秒 | 到点 `PLAN_EXPIRED` | 到点 `PLAN_EXPIRED` | 删除前的授权新鲜度，保留 |
| 判结论 | `blackboard/tasks/storage.py:739` | storage cleanup plan TTL 15 分钟 | 到点计划失效 | 到点计划失效 | 独立存储清理计划 |
| 判结论 | `blackboard/tasks/storage.py:22` | retention grace 3 天 | 宽限后才可考虑清理 | 宽限后才可考虑清理 | 删除前保护期 |
| 等待/未知 | `blackboard/tasks/storage.py:67` | `ps` 10 秒 | 诊断超时，打开状态未证明 | 诊断超时，打开状态未证明 | 删除前不能猜测无占用 |
| 等待/未知 | `blackboard/tasks/storage.py:93` | 单 PID `lsof` 5 秒 | 同上 | 同上 | 同上 |
| 等待/未知 | `blackboard/tasks/storage.py:117` | 用户级 `lsof` 30 秒 | 同上 | 同上 | 同上 |
| 判结论（Router） | `blackboard/tasks/workspace.py:30-48,120-168` | 原每次 Git 60 秒；现普通调用无上限，Router 调用取剩余预算 | 固定 60 秒到点把 Git 操作判失败 | 普通调用等退出事实；Router 的动态 deadline 保持独立于 Git 真实错误 | 不把慢 Git 当坏仓库；ContextVar 只绑定本次 Router 的预算 |
| 判结论 | `blackboard/tasks/workspace.py:344` | 原批量写入 wait 60 秒；现同一 Router 剩余预算或 None | 到点 kill，再记录操作失败 | 普通调用等退出事实；Router 期限耗尽回收已创建进程并报 deadline，保留诊断 | 固定写入上限不能判仓库坏；真实 broken pipe 与非零退出仍报 Git 错误 |
| 判结论（条件式） | `blackboard/tasks/workflow.py:919,6328-6346` | 凭证 `expires_at` 校验；当前创建语句未给固定 TTL | 非空且过期会 `UNAUTHORIZED`；当前插入通常为 NULL | 非空且过期会 `UNAUTHORIZED`；当前插入通常为 NULL | 安全过期校验保留；不可误报成当前固定 TTL |
| 判结论 | `install/runtime.py:332,400-406` | runtime materialization 默认 900 秒 | 到点 `RUNTIME_INSTALL_TIMEOUT` | 取消 900 秒安装失败上限及内部参数；仅依据 uv 实际退出和 READY 校验发布运行时 | 慢安装不能被定时器杀掉后判失败 |
| 判结论 | `install/upgrade.py:41-52` | `file_lock` 默认 0 秒 | 无锁即 `TimeoutError` | 无锁即 `TimeoutError` | 升级锁防并发；调用方通常覆盖 |
| 等待 | `install/upgrade.py:52` | lock retry sleep 0.1 秒 | 再查锁 | 再查锁 | 节拍 |
| 判结论 | `install/upgrade.py:121` | service command 45 秒 | 超时升级命令失败 | 取消 45 秒子进程上限；等旧/新运行时客户端实际返回 | 慢控制命令不等于升级失败；明确错误仍拒绝 |
| 判结论/未知 | `install/upgrade.py:142-156` | retire workers 默认 35 秒 | 取锁/停 Worker 逾期，升级未完成 | 取锁/停 Worker 逾期，升级未完成 | 不能猜测旧进程已停 |
| 判结论 | `install/upgrade.py:166,546,596,611` | daemon lock 30 秒 | 逾期升级失败 | 逾期升级失败 | 安装与运行互斥 |
| 判结论 | `install/upgrade.py:166,546,596,611` | board-owner lock 30 秒 | 逾期升级失败 | 逾期升级失败 | 独立 owner 锁等待 |
| 判结论 | `install/upgrade.py:183-197` | 服务启动验证 45 秒 | 到点升级失败 | 取消 45 秒启动失败上限；等匹配 PID 的新端点健康结果或子进程实际退出 | 未知启动继续观察，原升级互斥、退休与回收时限不变 |
| 等待 | `install/upgrade.py:197` | 启动 poll 0.2 秒 | 重查状态 | 重查状态 | 节拍 |
| 等待/未知 | `cli/checks.py:198,324-326` | 测试子进程 stdout/stderr drain 2 秒 | 收集已到达输出；不能保证输出完整 | 收集已到达输出；不能保证输出完整 | 检查日志回收 |
| 等待/未知 | `cli/checks.py:202,384-389` | interrupt 后 grace 10 秒 | 未退出 suite 会 kill | 未退出 suite 会 kill | 中断回收 |
| 等待 | `cli/checks.py:389` | interrupt poll 0.05 秒 | 重查 suite 是否退出 | 重查 suite 是否退出 | 节拍 |
| 等待/未知 | `cli/checks.py:491` | 残留检查 `ps` 30 秒 | 枚举失败，teardown 证明不充分 | 枚举失败，teardown 证明不充分 | 检查 runner 的私人根保护 |
| 等待/未知 | `cli/checks.py:523` | 残留检查 `lsof` 120 秒 | 同上，可能使 runner 返回 3 | 同上，可能使 runner 返回 3 | 不应转成测试逻辑失败 |
| 等待 | `cli/checks.py:614` | 两次残留观察间隔 0.3 秒 | 稳定性复核 | 稳定性复核 | 防瞬时 indexer 噪声 |
| 等待/未知 | `cli/checks.py:634` | 发送 stop 的 drain 10 秒 | 要求测试私有 daemon 协作停止 | 要求测试私有 daemon 协作停止 | 留待实际锁/进程证明 |
| 等待/未知 | `cli/checks.py:646-684,716` | 私有根合作停机默认 30 秒 | 到点仍有 survivor 则保留根和证据 | 到点仍有 survivor 则保留根和证据 | 检查隔离清理，不能删未确认根 |
| 等待 | `cli/checks.py:684` | 合作停机 poll 0.1 秒 | 重查存活进程 | 重查存活进程 | 节拍 |
| 等待 | `protocol/examples/external_worker.py:92` | 示例任务 sleep 2 秒 | 模拟外部任务进展 | 模拟外部任务进展 | 示例，非服务规则 |
| 等待 | `protocol/examples/external_worker.py:98` | 示例主循环 sleep 2 秒 | 下次读任务 | 下次读任务 | 示例，非服务规则 |
| 等待/未知 | `buddy/harnesses/dsh/acp/client.py:29,114` | initialize 默认 30 秒 | ACP request 到点 `AcpTimeout` | ACP request 到点 `AcpTimeout` | 底层调用只报本次等待耗尽；生产原生运行显式传同一执行期限，不以这些默认值判执行失败 |
| 等待/未知 | `buddy/harnesses/dsh/acp/client.py:30,124-138` | session 操作默认 60 秒 | 同上 | 同上 | 底层调用只报本次等待耗尽；生产原生运行显式传同一执行期限，不以这些默认值判执行失败 |
| 等待/未知 | `buddy/harnesses/dsh/acp/client.py:31,147` | config 默认 30 秒 | 同上 | 同上 | 底层调用只报本次等待耗尽；生产原生运行显式传同一执行期限，不以这些默认值判执行失败 |
| 等待/未知 | `buddy/harnesses/dsh/acp/client.py:32,153` | close 默认 30 秒 | 到点关闭未确认 | 到点关闭未确认 | 不证明会话已关闭 |
| 等待/未知 | `buddy/harnesses/dsh/acp/client.py:33,158` | prompt 方法默认 3600 秒 | 到点 `AcpTimeout` | 到点 `AcpTimeout` | 底层调用只报本次等待耗尽；生产原生运行显式传同一执行期限，不以这些默认值判执行失败 |
| 判结论 | `buddy/harnesses/dsh/native_run.py:138` | session/initialize 覆盖 30 秒 | 慢会话建立转原生失败 | 初始化、开/续会话、模型和强度配置共用执行期限；Worker 无硬预算时持续等同一次请求 | DSH CLI 慢协议请求不判 buddy/harness 不可用；Router 及目录读取用各自既有预算 |
| 判结论 | `buddy/harnesses/dsh/native_run.py:139` | config 覆盖 30 秒 | 慢配置转原生失败 | 初始化、开/续会话、模型和强度配置共用执行期限；Worker 无硬预算时持续等同一次请求 | DSH CLI 慢协议请求不判 buddy/harness 不可用；Router 及目录读取用各自既有预算 |
| 等待/未知 | `buddy/harnesses/dsh/native_run.py:140` | close 覆盖 30 秒 | 关闭未确认 | 正常逻辑会话 close 共用执行期限；显式中断后的 close 仍保留 30 秒回收窗口 | 正常完成要等 close 事实；目录 close 预算耗尽记 deadline/unknown，真实拒绝与断连仍保留 |
| 等待/未知 | `buddy/harnesses/dsh/native_run.py:141` | settle 5 秒 | 原生处理/进程停止观察 | 原生处理/进程停止观察 | 超时不证明已停 |
| 等待/未知 | `buddy/harnesses/dsh/native_run.py:142` | drain 10 秒 | 收尾排空 | 收尾排空 | 同上 |
| 等待/未知 | `buddy/harnesses/dsh/native_run.py:147` | abandon 5 秒 | 放弃前观察 | 放弃前观察 | 同上 |
| 等待/未知 | `buddy/harnesses/dsh/native_run.py:1019` | cancel RPC 10 秒 | 超时取消送达不确定 | 超时取消送达不确定 | 回收边界 |
| 等待 | `buddy/harnesses/dsh/native_run.py:1233` | prompt update poll 0.05 秒 | 重读流更新/取消 | 重读流更新/取消 | 节拍 |
| 等待/未知 | `buddy/harnesses/dsh/native_run.py:1239` | prompt thread join 5 秒 | 到点线程仍可能活 | 到点线程仍可能活 | 不能宣布完整停止 |
| 等待/未知 | `buddy/harnesses/dsh/acp/connection.py:77,555` | reply write 3 秒 | 未开始写的 queued reply 到点记 expired-queued；开始后仍等实际写结果 | 同左；不把排队耗尽记原生故障或执行失败 | 记录的是确定未发送或未知送达；writeFailureCount 只依据实际写错误、队列满或 connection shutdown |
| 等待/未知 | `buddy/harnesses/dsh/acp/connection.py:78,543` | notify write 默认 10 秒 | 到点 AcpTimeout，未发则取消排队、已开始则 writeUncertain | 同左；生产调用用于 cancel 通知，后续走实际中断/停机证明 | 回收窗口内未知送达不能当作已取消，必须等停止证据 |
| 等待/未知 | `buddy/harnesses/dsh/acp/connection.py:79,867` | writer join 5 秒 | writer 未退出仍未知 | writer 未退出仍未知 | 收尾证据 |
| 等待 | `buddy/harnesses/dsh/acp/connection.py:372` | writer wake 0.05 秒 | 再查写队列 | 再查写队列 | 节拍 |
| 等待/未知 | `buddy/harnesses/dsh/acp/connection.py:841` | shutdown 的 stdin lock 5 秒 | 未取锁则记 stdinBusy，交给组停止释放 pipe | 同左；不把锁等待到点记协议写失败 | 回收观察，不能通过抢关仍被 writer 使用的 pipe 假报停止 |
| 等待/未知 | `buddy/harnesses/dsh/acp/connection.py:877` | stderr join 1 秒 | 日志线程仍可能活 | 日志线程仍可能活 | 输出回收 |
| 等待/未知 | `buddy/harnesses/codex/native_run.py:480` | owned group halt 5 秒 | 停机后仍可能未确认 | 停机后仍可能未确认 | 需实际退出证明 |
| 等待/未知 | `buddy/harnesses/claude/native_run.py:347` | owned group halt 5 秒 | 同上 | 同上 | 同上 |
| 等待/未知 | `buddy/harnesses/zcode/native_run.py:773` | owned group halt 5 秒 | 同上 | 同上 | 同上 |
| 等待/未知 | `buddy/harnesses/claude/native_run.py:195` | 版本/metadata 读取 2 秒 | 证据读取失败；不应倒置已结算 run | 证据读取失败；不应倒置已结算 run | 附加证据界限 |
| 等待/未知 | `buddy/harnesses/codex/native_run.py:985` | 已结算 metadata 读取 2 秒 | 同上 | 同上 | 同上 |
| 等待/未知 | `buddy/harnesses/codex/native_run.py:1377` | 另一次原生读取 2 秒 | 到点本次资料缺席 | 到点本次资料缺席 | 不把辅助资料当执行结论 |
| 等待/未知 | `buddy/harnesses/zcode/protocol.py:33,381-398` | metadata 读取默认 2 秒 | 同上 | 同上 | 同上 |
| 等待/未知 | `buddy/harnesses/claude/native_run.py:630` | 进程等待最多 3 秒且受总 deadline 限制 | 到点进入下一步观察/回收 | 到点进入下一步观察/回收 | 总预算内的收尾 |
| 等待 | `buddy/harnesses/claude/protocol.py:167-174` | message read 由调用方 seconds 决定；poll 最多 0.2 秒 | 等待一条原生消息 | 等待一条原生消息 | 动态窗口 |
| 等待 | `buddy/harnesses/codex/protocol.py:90` | cancel poll 0.05 秒 | 重查取消 | 重查取消 | 不自行定性结果 |
| 等待 | `buddy/harnesses/codex/protocol.py:106` | message queue poll 0.2 秒 | 重查原生回复 | 重查原生回复 | 同上 |
| 等待 | `buddy/harnesses/claude/protocol.py:130` | cancel poll 0.05 秒 | 重查取消 | 重查取消 | 同上 |
| 等待 | `buddy/harnesses/claude/protocol.py:139` | message queue poll 0.2 秒 | 重查原生回复 | 重查原生回复 | 同上 |
| 等待 | `buddy/harnesses/zcode/protocol.py:716` | cancel poll 0.05 秒 | 重查取消 | 重查取消 | 同上 |
| 等待 | `buddy/harnesses/zcode/protocol.py:741` | message queue poll 0.2 秒 | 重查原生回复 | 重查原生回复 | 同上 |
| 等待 | `buddy/harnesses/zcode/native_run.py:811` | message queue poll 1 秒 | 重读消息 | 重读消息 | 节拍 |
| 等待 | `apps/console/src/use-console.ts:64` | poll 3 秒 | 刷新 console 数据 | 刷新 console 数据 | 可见时轮询 |
| 等待 | `apps/console/src/use-task-history.ts:283` | poll 3 秒 | 刷新 task history | 刷新 task history | 同上 |
| 等待 | `apps/console/src/use-workflow.ts:55` | poll 3 秒 | 刷新 workflow | 刷新 workflow | 同上 |
| 等待 | `apps/console/src/use-macro-task-list.ts:224` | poll 3 秒 | 刷新 macro task list | 刷新 macro task list | 同上 |
| 等待 | `apps/console/src/StoragePanel.tsx:122` | clock tick 30 秒 | 更新时间显示 | 更新时间显示 | 不改后端状态 |
| 等待/未知 | `apps/console/src/global-refresh.ts:3-5` | pending-read 最多 30 秒 | 到点抛“现有读取尚未完成” | 到点抛“现有读取尚未完成” | 仅结束一次前端刷新等待并显示尚未完成，后端状态和已有数据不变；本任务未改前端 |
| 等待 | `apps/console/src/global-refresh.ts:6` | pending-read poll 50 毫秒 | 重查可用性 | 重查可用性 | 节拍 |
| 等待 | `apps/console/src/use-task-history.ts:259` | 防抖 180 毫秒 | 延后筛选/刷新 | 延后筛选/刷新 | 非任务结论 |
| 等待 | `apps/console/src/use-macro-task-list.ts:198` | 防抖 180 毫秒 | 同上 | 同上 | 同上 |
| 等待 | `apps/console/src/use-buddy-history.ts:18,185` | 防抖 300 毫秒 | 同上 | 同上 | 同上 |
| 等待 | `apps/console/src/use-macro-task-timeline.ts:127` | tick 3 秒 | 更新 timeline | 更新 timeline | 同上 |
| 等待/未知 | buddy/runtime/worker.py:Worker._execute_guarded / _Renewal.run | 复用 claim idle 2 秒、取消检查 2 秒、lease/3（最低 5 秒）续租节拍 | 两次短预检均未知时可判不可用；预检与 native start 期间没有独立续租 | 未知持续尝试；传输超时重放同一 commandId；空结果后新观察不消费失败重试；续租覆盖准备/启动，取消依实际事实 | 准备慢不能使微任务失败或丢失租约；交给 native handle 前结束旧续租者 |
| 判结论，保留 | buddy/harnesses/native_support.py:absolute_execution_deadline / roles.run_controller.execute / roles.run_execution._launch | 同一次 Router 的绝对 monotonic deadline；原收尾 grace 不变 | controller 启动后以相对秒数重新起算 | controller setup 后已过期则不调用 native；仍有时间时四个 native driver 取原绝对期限的上界 | 硬预算属于 Router；正面无 native 子进程的停止证据允许调度下一 Router |
| 等待/未知 | console/assets/assets/index-CRjilw0Y.js（Scheduler immediate priority） | -1 毫秒 | 调度、加载或显示回调 | 未修改；只结束显示或加载等待，不写 authoritative board 状态 | 打包依赖的内部计时；按语义调用点列出，同一生成表达式的重复代码不重复列 |
| 等待/未知 | console/assets/assets/index-CRjilw0Y.js（Scheduler user-blocking priority / React lanes） | 250 毫秒 | 调度、加载或显示回调 | 未修改；只结束显示或加载等待，不写 authoritative board 状态 | 打包依赖的内部计时；按语义调用点列出，同一生成表达式的重复代码不重复列 |
| 等待/未知 | console/assets/assets/index-CRjilw0Y.js（Scheduler normal priority / React lanes） | 5000 毫秒 | 调度、加载或显示回调 | 未修改；只结束显示或加载等待，不写 authoritative board 状态 | 打包依赖的内部计时；按语义调用点列出，同一生成表达式的重复代码不重复列 |
| 等待/未知 | console/assets/assets/index-CRjilw0Y.js（Scheduler low priority） | 10000 毫秒 | 调度、加载或显示回调 | 未修改；只结束显示或加载等待，不写 authoritative board 状态 | 打包依赖的内部计时；按语义调用点列出，同一生成表达式的重复代码不重复列 |
| 等待/未知 | console/assets/assets/index-CRjilw0Y.js（Scheduler idle priority） | 1073741823 毫秒 | 调度、加载或显示回调 | 未修改；只结束显示或加载等待，不写 authoritative board 状态 | 打包依赖的内部计时；按语义调用点列出，同一生成表达式的重复代码不重复列 |
| 等待/未知 | console/assets/assets/index-CRjilw0Y.js（React view-transition 等待） | 500 毫秒 | 调度、加载或显示回调 | 未修改；只结束显示或加载等待，不写 authoritative board 状态 | 打包依赖的内部计时；按语义调用点列出，同一生成表达式的重复代码不重复列 |
| 等待/未知 | console/assets/assets/index-CRjilw0Y.js（React 样式 Suspense 等待） | 60000 毫秒加动态偏移 | 调度、加载或显示回调 | 未修改；只结束显示或加载等待，不写 authoritative board 状态 | 打包依赖的内部计时；按语义调用点列出，同一生成表达式的重复代码不重复列 |
| 等待/未知 | console/assets/assets/index-CRjilw0Y.js（React 图片 Suspense 等待） | 62500 毫秒减动态 elapsed | 调度、加载或显示回调 | 未修改；只结束显示或加载等待，不写 authoritative board 状态 | 打包依赖的内部计时；按语义调用点列出，同一生成表达式的重复代码不重复列 |
| 等待/未知 | console/assets/assets/index-CRjilw0Y.js（React navigation 轮询） | 20 毫秒 | 调度、加载或显示回调 | 未修改；只结束显示或加载等待，不写 authoritative board 状态 | 打包依赖的内部计时；按语义调用点列出，同一生成表达式的重复代码不重复列 |
| 等待/未知 | console/assets/assets/index-CRjilw0Y.js（React navigation 初次延后） | 100 毫秒 | 调度、加载或显示回调 | 未修改；只结束显示或加载等待，不写 authoritative board 状态 | 打包依赖的内部计时；按语义调用点列出，同一生成表达式的重复代码不重复列 |
| 等待/未知 | console/assets/assets/index-CRjilw0Y.js（i18next、Radix 与 React 异步回调） | 0 毫秒 / 无显式延后 | 调度、加载或显示回调 | 未修改；只结束显示或加载等待，不写 authoritative board 状态 | 打包依赖的内部计时；按语义调用点列出，同一生成表达式的重复代码不重复列 |
| 等待/未知 | console/assets/assets/index-CRjilw0Y.js（第三方 UI 事件记录移除） | 1 毫秒 | 调度、加载或显示回调 | 未修改；只结束显示或加载等待，不写 authoritative board 状态 | 打包依赖的内部计时；按语义调用点列出，同一生成表达式的重复代码不重复列 |
| 等待/未知 | console/assets/assets/index-CRjilw0Y.js（第三方 IntersectionObserver 退避） | 1000 毫秒 | 调度、加载或显示回调 | 未修改；只结束显示或加载等待，不写 authoritative board 状态 | 打包依赖的内部计时；按语义调用点列出，同一生成表达式的重复代码不重复列 |
| 等待/未知 | console/assets/assets/index-CRjilw0Y.js（Radix UI 隐藏、tooltip 与防抖回调） | 动态 scrollHideDelay / tooltip delay / debounce delay | 调度、加载或显示回调 | 未修改；只结束显示或加载等待，不写 authoritative board 状态 | 打包依赖的内部计时；按语义调用点列出，同一生成表达式的重复代码不重复列 |
| 等待/未知 | console/assets/assets/index-CRjilw0Y.js（i18next backend 重读） | 动态 retryTimeout，指数翻倍 | 调度、加载或显示回调 | 未修改；只结束显示或加载等待，不写 authoritative board 状态 | 打包依赖的内部计时；按语义调用点列出，同一生成表达式的重复代码不重复列 |

## 合并位置

`blackboard/tasks/workflow.py` 和 `blackboard/store/` 的源码未修改；两份 README、AGENTS、CONTEXT、skills 及其他 docs 未修改。Host 后续放开的范围只用于时限及其所在函数，以下列出 Worker 回合相关文件的实际修改，供与并行微任务合并。

| 文件 | 函数 | 改了什么 |
| --- | --- | --- |
| `buddy/roles/run_execution.py` | `discover_models` | 分离原生 deadline / 外层等待耗尽、真实失败、停止未确认；未知目录不判不可用 |
| `buddy/roles/run_execution.py` | `start_fast`、`start_review`、`_launch` | private controller 接收原绝对期限；handle 期限沿用原 deadline 加既有 grace |
| `buddy/roles/run_controller.py` | `execute` | discovery 的 deadline 回传 unknown 与停止证据；Router native 前检查原期限，并给 native driver 绑定同一期限 |
| `buddy/roles/controller.py` | `start_router_preparation` | 把可选的 Router 绝对期限交给唯一现有运行入口；普通当前 structured call 继续相对预算 |
| `buddy/roles/router.py` | `prepare_router_review`、`DecisionAdapter.start`、`DecisionAdapter.collect` | 材料化前起算、剩余预算和正面 prelaunch 无子进程证据；执行后核验用同一期限，总 elapsed 包含准备 |
| `buddy/roles/router_input.py` | `_remaining`、`digest`、`prepare`、`verify` | 共享 Router deadline，阻塞批量读取受同一期限约束；真实 Git 错误保持原输入拒绝，核验预算耗尽只记未知 |
| `blackboard/tasks/workspace.py` | `git_deadline`、`remaining_git_seconds`、`_git`、`_git_in`、`_write_objects`、`_commit`、`inspect` | 只移除独立 Git 时限并让 Router 的 deadline 原样穿过 Git 边界，不改 Git 操作或工作区语义 |
| `buddy/runtime/worker.py` | `execute`、`_execute_guarded`、`_execute_selected`、`_preflight_cancelled`、`_Renewal` 的等待/取消/续租函数 | 未知预检不消费失败重试；RPC 重放身份不变；预检续租、取消事实和 native handle 交接；Router 无子进程证明结算为 deadline |

其他改动集中于 `blackboard/service/harness_health.py` 的未知扫描和账户观察、`buddy/harnesses/discovery.py` 的超时分类、`claude/adapter.py` 的未知元数据缓存、`dsh/native_run.py` 的协议等待、`native_support.py` 的共享执行期限，以及 `install/runtime.py`、`install/upgrade.py`、`protocol/transport.py` 的进程与就绪等待；均没有新增用户设置或兼容路径。

## 测试如何等待事实

`cli/test_checks_parallel.py` 等两个 suite 的启动 JSON marker 后再中断，以退出码和真实进程组状态断言；`buddy/harnesses/test_discovery_stop_timing.py` 等 ready/joined/reaped marker，再用受控 clock 命中探测窗口或等真实组消失；`blackboard/store/test_blackboard.py` 等 WaitAdmission 实际有两个 admitted 与一次 rejected；`console/test_console_sessions.py` 等 console status 的会话实际关闭；`buddy/harnesses/claude/test_claude.py` 的 early-result 不再受无关的 8 秒预算影响，依原生帧顺序判定；`buddy/harnesses/zcode/test_zcode_tool_evidence.py` 的超时分支等真实 prompt-ready 后才触发连接 deadline，取消分支仍核对 controller；`blackboard/tasks/test_workflow_worker.py` 的手动 claimant 场景先用已有 stop_private_workers 等私有自动 Worker 实际退出，避免它抢走 claim，再等 transient empty claim 后实际出现 claim；`buddy/harnesses/codex/test_native_run.py` 的三处取消场景等 TurnEvidence.observe 实际接受原生 started 事实后才取消，移除无关的短启动预算。保护性等待上限没有被用作启动或结束的同步信号。

待办中 ZCode CLI 与 Claude Code CLI 的其他取消测试已经等待原生回合记录，未再修改；安装/打包与 Worker 冷启动测试原有 subprocess 上限用于测试防挂死，本次没有把这些上限简单放大。黑板冷启动测试明确设私有安装子进程的 UV_OFFLINE=1，使用检查运行器共享的已备齐 uv cache，仍实际材料化和启动私有运行时；它依赖已准备好的缓存，不再把 PyPI 索引重验证混入路径稳定性断言，未往指定解释器环境写入内容。新行为覆盖扫描未知后恢复、账户未知保留计费后再读、目录未知后再读、真实原生拒绝在外层等待耗尽后仍优先保留、未知停止、未知预检续租及取消竞态、同一次 RPC 重放、DSH CLI 慢协议请求只发送一次、晚 close、Router 阻塞材料化与慢启动、执行后核验耗尽仍是未知输入、预算耗尽的真实 Worker 到黑板下一 Router 接线。

旧 Git 超时断言已改为 Router 预算下的 deadline；真实 Git 错误仍保留实际 argv、退出码、二进制诊断和因果。旧 Router 准备异常“没拿到 handle 就是不知是否停止”的断言，改为核对创建边界给出的正面 no-child 证据。共享假 controller fixture 接受并保留绝对期限，测试使用有效的冻结预算输入；没有在生产调用方增加兼容分支来迁就旧 fixture。

## 实际验证

所有 Python 检查仅用 `PYTHONPATH=src <Host-provided-python> -m hey_my_buddy.cli.checks <文件或目录>`，使用默认进程数，没有 --jobs 或 HMB_CHECKS_JOBS，没有改指定解释器环境。检查运行器自行建立私有系统临时根并负责清理。原始日志在忽略目录 `tmp/control-deadlines/`，下列结果不把子代理的成功报告当作 Host 验收。

受影响的主选择为 `buddy/harnesses/`、`blackboard/service/`、`buddy/roles/`、`buddy/runtime/`、`blackboard/tasks/`、`blackboard/routing/`、`install/`、`protocol/`，以及 `cli/test_checks_parallel.py`、`console/test_console_sessions.py`、`blackboard/store/test_blackboard.py`，共 209 个文件；没有运行完整检查。两轮主选择及后续新源码边界选择出现的失败均已按下面记录修复并复跑通过，不能把它们的原始 exit 1 当作整轮通过；最终证据由相同选择的成功文件与失败文件修复后的复跑共同构成。

| 日志（`tmp/control-deadlines/` 下） | 选择与结果 | 后续处理 |
| --- | --- | --- |
| `focused-first.log` | 初始四个边界文件，82 tests，4/4 通过，exit 0 | 后续按源码变化扩大到受影响选择 |
| `affected-first.log` | 主选择 209 文件，205/209 通过；成功文件 3468 tests，exit 1 | 修复 decision 旧停止证据断言、workflow_worker claimant 竞态、workspace_git_errors 旧超时码断言、run_execution 旧剩余预算断言 |
| `repair-focused.log` | 五个修复文件，3/5 通过，71 tests，exit 1 | Git 真实诊断用例与 DSH 新测试的运行方法名修正；后续边界通过 |
| `repair-focused-final.log`、`fixtures-focused.log` | 分别 3/4 与 2/3 通过，86 与 48 tests，exit 1 | 假 controller 的绝对期限签名与 routing_preferences 的有效预算 fixture 修正；后续角色/路由目录通过 |
| `flaky-round-1.log`、`flaky-round-2.log`、`flaky-round-3.log` | 早期六文件各轮 164 tests、6/6 通过、exit 0 | 最终八文件仍按要求重新连跑三轮，以下表为交付证据 |
| `affected-final.log` | 主选择 206/209 通过；成功文件 3378 tests，exit 1 | 黑板冷安装失败为 PyPI 实际网络错误，改用现有共享缓存；私有自动 Worker 抢 claimant 与 Codex CLI 启动前定时取消改为等事实 |
| `flaky-final-1.log`、`facts-cache-focused.log` | 分别 5/7 与 2/3 通过，106 与 65 tests，exit 1 | 后者已确认缓存安装和私有 claimant 修复；Codex CLI 的取消同步改为实际 started 观察，最终三轮通过 |
| `changed-boundaries-final.log`、`elapsed-focused.log` | 分别 131/132 与 2/3 通过，2383 与 38 tests，exit 1；唯一失败均为 decision 的 elapsed 精确 200 毫秒旧断言 | 改为包含准备总耗时且不小于 native 200 毫秒；最终 decision 通过 |
| `last-fact-focused.log` | Codex CLI native_run、健康服务、Worker control_waits，156 tests、3/3 通过、exit 0 | 包含 108 个 Codex CLI 合成测试、41 个健康测试、7 个预检测试 |
| `final-followup.log` | decision、目录发现、真实 Router failover、Git 错误、健康、预检，193 tests、6/6 通过、exit 0 | 校验真实拒绝优先与预算结算 |
| `router-verification-final.log` | 新增执行后核验预算后，角色/路由目录 49/50 通过、689 tests，exit 1 | 唯一失败为假核验回执缺失败码；fixture 改为真实核验逻辑 |
| `router-verification-passed.log` | `buddy/roles/` 与 `blackboard/routing/` 完整目录，724 tests、50/50 通过、exit 0 | 包含最后的核验预算回归 |
| `remaining-consumers-final.log` | evaluation 与 native_observations、upgrade_fixture、model_service_concurrency、parallel_dispatch、workflow_routing、CLI cleanup/views/cli/host/support、console_reads_http，225 tests、12/12 通过、exit 0 | 覆盖 Router fixture 与服务启动等待的额外消费者 |
| `fixture-consumers-final.log` | 最终真实核验 fixture 的 decision、router、routing_modes、single_router_requests，85 tests、4/4 通过、exit 0 | 加上上一行的三个消费者，最终 fixture 使用处全部复跑 |

最终八个偶发测试文件作为同一选择在默认 4 个进程下依次运行三轮；每轮共 279 tests、8/8 文件通过、exit 0，私有根都已删除。三个日志为 `flaky-verified-1.log`、`flaky-verified-2.log`、`flaky-verified-3.log`；下列秒数为运行器报告的实际文件耗时，不是断言窗口。

| 测试文件（相对 `tests/python/`） | 第一轮 | 第二轮 | 第三轮 |
| --- | --- | --- | --- |
| `cli/test_checks_parallel.py` | 通过，8.0 秒 | 通过，6.0 秒 | 通过，6.0 秒 |
| `buddy/harnesses/test_discovery_stop_timing.py` | 通过，10.9 秒 | 通过，10.5 秒 | 通过，10.3 秒 |
| `blackboard/store/test_blackboard.py` | 通过，123.3 秒 | 通过，107.6 秒 | 通过，95.5 秒 |
| `console/test_console_sessions.py` | 通过，29.2 秒 | 通过，21.6 秒 | 通过，20.4 秒 |
| `buddy/harnesses/claude/test_claude.py` | 通过，57.9 秒 | 通过，56.8 秒 | 通过，40.3 秒 |
| `buddy/harnesses/zcode/test_zcode_tool_evidence.py` | 通过，19.0 秒 | 通过，14.5 秒 | 通过，13.5 秒 |
| `blackboard/tasks/test_workflow_worker.py` | 通过，68.1 秒 | 通过，61.2 秒 | 通过，48.5 秒 |
| `buddy/harnesses/codex/test_native_run.py` | 通过，57.9 秒 | 通过，57.8 秒 | 通过，52.0 秒 |

最后的静态复核发现 Router 执行后核验没有绑定期限，已修复并复核通过；这里只记录源码审查，不将其当作测试或 Host 验收。`git diff --check` 通过。运行器对上述成功与失败运行均完成私有根清理，没有留下 exit 3 的未证明清理。

## 要求边界

完整检查未运行，安装日常产品、真实 harness CLI 或 app 的原生检查、tests/probes、产品付费模型调用与日常服务/Worker 启停均未进行。忙机上真实 harness CLI 的时长、Linux/Windows 原生运行和安装态验收仍由 Host 另外处理。本次没有修改前端，编译依赖的相同生成表达式按其语义计时归并；没有已知与授权范围或要求不一致的情形。其他文档按冻结范围由 Host 更新，包括新增 `buddy/roles/router.py` 对已有 `blackboard/tasks/workspace.py` Git 预算边界的跨侧引用登记。
