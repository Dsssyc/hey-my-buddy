# 服务调度独立审查：ADR-031 M2

审查对象是 `eaa65537..486d42c6` 的 `src/` 与 `tests/` 差异，依据 AGENTS.md 的 Invariants、ADR-007、ADR-031 和执行计划的 M2 范围。以下编号只统计需要处理的发现：A 5 条、B 5 条、C 4 条、D 10 条；路径和行号均指 `486d42c6`，标注“旧”的位置指 `eaa65537`。P1 表示会丢失调度、停止或恢复能力，P2 表示契约、状态或回归覆盖缺陷，P3 表示清点误报。

本次只读源码、文档和测试，未运行检查套件、服务、harness 或故障演练，未读取 `docs/acceptance/`，未操作日常环境。表中的测试是可复现或证伪的方法，不是本次已通过的证据；关于 Codex CLI、Claude Code CLI、DSH CLI、ZCode CLI 的表述仅描述本提交的接线和离线夹具，没有宣称任何已安装 CLI 版本或 app 的实测行为。主审在完成自己的清点后才全文读取实现方说明；升级子审曾在一次过宽的文本检索中误读该说明的两行，已停止使用该材料，其发现由主审重新核对源码，未以该说明作事实依据。

## A. 停止证据与“不知道不等于已停”

A1（P1）：冻结执行进程时，强停的 8 秒计时可能永远不开始。`src/hey_my_buddy/blackboard/service/execution_scheduler.py:220–227` 先调用 `client.stop()`，成功返回后才设置 `forced_at`；`protocol/execution.py:197–203` 将连接或调用超时转成 `LIVE_UNAVAILABLE`。只要 `execution-live.json` 仍可读，每轮都会在 scheduler 的第 152 行构造 client，进入第 160 行分支；stop 超时后走第 169–172 行，进不到第 173–177 行的无 client 降级计时。身份仍为 alive 时也不会恢复结算，于是第 188–190 行永远没有可用的强停起点。结果保持未确认，未把未知伪报为停止，但冻结的执行和另起进程组中的原生命令可以一直存活。复现：在私有服务启动长跑命令，取得执行身份后对执行 PID 施加 SIGSTOP，再提交 Host 强停、到期 wrap-up 或到期 Router 预算，保持描述符存在；等超过调用超时加 8 秒，要求服务终止经过核验的执行组、回收原生组后才确认停止。也可用持续抛 `LIVE_UNAVAILABLE` 的 stop 替身和可控时钟验证。`tests/python/blackboard/tasks/test_parallel_dispatch.py:168–190` 只让原生命令忽略 SIGTERM，没有冻结执行进程，不能证伪本条。

A2（P1）：零等待服务停止可以在停止投递前撤掉唯一监控者。`blackboard/service/daemon.py:176–183` 写入看板取消意图后调用 `drain(0)`，立即置 `self.stopping`；`daemon.py:256–260` 随即停止 scheduler，`execution_scheduler.py:39–48,148–162` 退出观察循环，既没有逐个端点投递的屏障，也没有把计时后的强停工作交给仍在运行的所有者。若观察线程正在先前的 wait 中，下一轮读停止意图前就退出；较短的非零 drain 同样可能早于 8 秒升级。返回的 `unresolvedAttempts` 和 `stopped=false` 是诚实的，但执行已不轮询看板，服务退出后取消意图无人送达，原生工作可继续跑。复现：在观察线程进入 wait 后用屏障暂停它，调用 `service_control` 的 stop 且 `drainSeconds=0`，完成 daemon cleanup，再放开屏障，检查端点收到停止且最终消失，或检查存在明确接续停止职责的机制；当前路径两者都没有。新 `test_parallel_dispatch.py:125–133` 调的是普通 cancel，未测试服务的零等待停止。

A3（P1）：恢复先交出未确认结果后，补全停止证据会撞上同一命令号的幂等栅栏。`blackboard/tasks/interruption.py:175–195` 在原生日志不完整时就提交 `shutdownConfirmed=false`，命令号固定为 `interrupted:<attemptId>`；下一次日志完整或开机会话改变时，会以同一命令号提交不同结果和 `shutdownConfirmed=true`。`blackboard/store/store.py:2012–2030` 虽设置 `improving_stop`，仍调用 `_receipt`；第 288–293 行因请求哈希改变而抛 `CONFLICT`。另一种可达情况是执行已提交未确认结果，但确认回答丢失、私有回执还在，随后执行死亡；恢复使用原 `result:<attemptId>`，第 2011 行改用 recovery subject，与原 nonce subject 不同，会在第 282–286 行被拒绝。资源继续保留，明明已有停止证据也不能结束，启动时的同一错误还会触发 B1。复现：分别在私有板上做“缺日志恢复一次、补齐日志再恢复”和“提交未确认结果并保留原回执、执行消失后恢复”，要求第二轮确认停止、释放名额、保留原结果，第三轮幂等。现有 `test_interruption_recovery.py:124–146` 分开测首轮缺日志和无私有回执的补全，没有覆盖上述组合。

A4（P1）：Popen 成功、ProcessHandle 构造失败的窗口会丢失仍活的执行句柄。`protocol/process_controller.py:32–40` 先获得 Popen，再构造 `ProcessHandle`；构造中的创建时刻读取（`protocol/process_handle.py:24–27`，实际 OS 读取错误见 `protocol/process_start.py:60–66,81–86`）可以抛异常。此时局部 handle 尚未赋值，第 49–60 行只留下 `created=true` 的布尔回执，没有停止该 Popen，也没有把 Popen 传出。`protocol/execution.py:87–108` 因此传出 `execution_handle=None`，scheduler 第 104–110 行只尝试读尚不存在的身份标记便返回，不创建观察线程。若子进程随后自报身份，scheduler 第 53–54 行又把“没有句柄”当成重启情形直接放行，它可以继续启动原生工作，但本服务没有对应 monitor。复现：仅在服务父进程把首次 `ProcessHandle` 的创建时刻读取注入 OSError，子进程保持正常；检查失败后不存在无人监控的活执行，且未凭布尔创建事实释放资源。现有 `test_execution_process.py:94–106` 注入的是后面的 `capture_process_identity`，那时 handle 已经创建，覆盖不到此窗口。

A5（P2）：服务停止不属于工作流的普通命令执行时，发起者会丢失。微任务及其 Router 经 `blackboard/tasks/workflow.py:3511–3556` 记录 service 发起者；但 `blackboard/service/daemon.py:221–228` 对剩余执行调用通用 `task_cancel`，`blackboard/store/store.py:1102–1104` 固定写 unknown。端点正常结束时 `buddy/roles/controller.py:109–113` 得到 `unknown-stop`；服务强行结束后又可能按 scheduler 第 258 行及持久 deadline 记成 `forced-stop`，都失去了本来明确的 service 来源。停止确认仍取决于实际原生停止，错误在原因分类。复现：通过命令执行接口提交未绑定微任务的长跑命令，执行服务 stop 并给足 drain，断言 `stopInitiator=service`、`terminationReason=service-stop`，再做执行进程被强行终止的同样路径。

各条必查路径的证据与结算边界如下；未另列编号的行是当前实现的事实或 M3 边界。

| 路径 | 何时可以确认停止、现有结算 | 证据位置与可证伪方法 |
| --- | --- | --- |
| 准入后、真正创建进程前失败 | 创建边界明确返回 `spawn_created=false` 时 `release_execution` 写确认停止并释放名额；原因是确知未创建，不能把所有启动异常都按此处理 | `protocol/execution.py:104–108`、scheduler `:104–109`、store `:2230–2257`；在启动文件写入、Popen 前分别注入失败，检查无进程且只释放本次执行。A4 是已经创建后的另一条路 |
| 正常完成 | 命令由持有句柄确认根退出且组/Job 无存活者；CLI 运行由归一化 native stop evidence 确认。执行先写私有回执，再提交结果，服务同事务完成状态、产出和资源结算；执行本身在交付后退出 | `buddy/runtime/command.py:73–109`、`protocol/process_handle.py:142–162`、`buddy/runtime/execution.py:585,653–673`、`buddy/runtime/receipts.py:48–70`、store `:2062–2127`；留一个同组后代，检查 exit 0 不足以确认停止 |
| Host wrap-up、宽限到期 | 持久 grace deadline 由 workflow 记录；端点先传 wrap-up，服务到期发 force。有效总结且原生停止确认才为 wrap-up；超时强停后仍须原生组停止才能为 true | workflow `:3737–3754`、scheduler `:210–228`、`buddy/roles/inline_run.py:167–205`；在宽限内完成总结、耗尽宽限和停止 RPC 不应答三种情况下分别断言原因及停止证据，第三种受 A1 影响 |
| Host 强停 | 没有宽限时立即发送 force，服务应在 8 秒后按完整身份结束执行组，再由统一恢复处理原生组；发送成功不是停止证据 | scheduler `:218–227,260–270`、interruption `:155–176`；对照 A1，在收到和未收到 force 回答时都验证升级 |
| 控制台停止 | 宏任务/微任务停止记录 console 发起者；端点停止成功且原生停止后为 console-stop，无回执时由统一恢复保留该原因 | workflow `:3737–3754`、scheduler `:250–258`、interruption `:117–131`、store `:2002–2010`；停止宏任务后核查所有未确认后代，不能只查根状态 |
| 服务停止 | 微任务路径先留 service 取消意图，drain 只观察资源仍未解决的执行；未收齐证据应返回 false。零等待投递和普通命令的原因分别见 A2、A5 | daemon `:173–186,233–250`；分别测试零等待、足够等待、端点冻结与命令/微任务两类入口 |
| Router 预算到期 | 预算在私有 `execution-budget.json`，服务读取 binding 和 monotonic deadline，写 `execution.deadline` 后发 force；有真实停止证据才结束资源占用，无回执恢复能保留 deadline | `buddy/runtime/execution.py:539–547`、scheduler `:214–238`、interruption `:117–123`；同时验证正常应答与 A1 的端点冻结，不把 expectedSeconds 当预算 |
| 执行进程冻结、连接失败但身份仍活 | 没有证明原生已停，因此不能确认；当前不会仅以超时设 true，但强停可能因 A1 不发生。普通连接失败后也没有在本服务里把 ownership 改成 uncertain，此状态判断属 M3 | scheduler `:169–193`、`protocol/process_identity.py:299–333`；对冻结、权限不足和 PID 复用分别返回 alive/unknown，检查资源保留 |
| 执行在交出前崩溃，有回执 | 完整执行身份已消失或 boot 改变后才能读取回执；绑定及报告经结果边界验证，回执自身已确认停止则沿回执结算，未确认则先尝试原生恢复 | interruption `:155–174`；测试有效回执、不同执行绑定、未确认回执和结果已提交但确认回答丢失，后两种需覆盖 A3 |
| 执行在交出前崩溃，无回执 | 当前 boot 内，原生 journal 存在、格式及 binding 正确、pending 为零、每个原生身份已不在或经核验终止才为 true；boot 改变也能证明之前进程全部结束。缺失、损坏、未决创建或未知原生身份保持 false 并留住资源 | interruption `:61–73,175–195`、`protocol/process_identity.py:127–147,336–373`；先留存活原生命令、再留 pending，检查不能仅凭执行根消失确认；重复恢复需验证 A3 |
| 服务在准入后、启动前崩溃 | 已提交的 starting 执行在重启时变 uncertain；没有身份和 marker 时 recovery 直接跳过，当前会一直占位，不依据经过的时间、裸 PID 或“没看到进程”确认停止 | store `:2598–2624`、interruption `:144–154`；在准入事务提交后暂停启动并杀服务，检查重启后没有二次执行及没有错误释放。这种无身份启动窗口的继续处理尚未实现，本报告没有把它冒充已恢复 |
| 服务在创建后、写 marker/身份前崩溃 | 子进程首次身份被服务接受前不启动原生工作；若它存活，可在新服务上用 nonce 交身份。若双方都在 marker 和 DB 身份之前消失，则无可用完整身份，保守保留；已有 marker 时能补入身份 | `buddy/runtime/execution.py:278–285,767–774`、`protocol/execution.py:101–102`、interruption `:38–58,144–154`；在三个写入边界各注入崩溃，分别检查有标记和无标记结果。重新连接存活执行由 M3 完成 |
| 服务重启时执行仍活 | startup 变 uncertain 并保留名额；旧执行可以继续交结果，但新 scheduler 只观察新起的执行，没有遍历重连、恢复外部计时和补发停止的实现 | store `:2598–2652`、scheduler `:70–77`；`test_parallel_dispatch.py:135–149` 只证明执行最后能交结果，没有证明端点接回；这是已声明的 M3 范围 |
| 服务重启时执行已不在 | scheduler 启动及 daemon 每 15 秒恢复均调用同一 recovery，按已有身份/marker，先回执、再原生 journal；alive/unknown 均跳过 | scheduler `:72`、daemon `:162–171`、store `:2586–2596`；组合测试有回执、无回执、未完成 Git 封存和 A3 的第二轮恢复，不能仅调用一次证明恢复可收敛 |

在已核对的身份判断中，没有发现把 alive、unknown、未决原生创建或缺失 journal 直接改成 `shutdownConfirmed=true` 的分支；存在的是 A1、A2、A4 的停止职责丢失和 A3 的恢复不能收敛。正常回执的 true 表示原生工作停止已确认，不能再把它扩张成“执行进程自身已经退出”的证明；其自身的端点和进程清理由后续退出观察完成。

## B. 调度与端点

B1（P1）：正常可重试的准入错误会永久终止唯一调度线程。`blackboard/store/store.py:1393–1405` 的两轮共享 stash 覆盖收集仍可能抛出 `CONFLICT`，这是保留的并发边界；但 scheduler 的 `_run` 第 70–77 行和 `_admit_available` 第 84 行没有错误处理，daemon 也不重建这条线程。一次这样的竞争之后，服务仍能回答 health 和提交请求，却不会再启动任何排队执行；启动 recovery 的异常也可在第 72 行造成同样结果。复现：让两次 stash 观察都遗漏随后选中的仓库，随后恢复正常输入，检查无需重启服务或产生额外事件就能继续调度下一微任务；同时查看 scheduler 存活及状态报告。`test_worker_claim_stash.py:132–155` 只证明下一次手动调用能成功，删掉的旧 Worker 循环测试才守着自动继续能力，见 D1。

B2（P2）：跨进程端点材料的解码错误会撤销仍活执行的监控。`protocol/execution.py:132–144,258` 的严格 JSON 解码和 `EndpointCredential.from_json` 可以抛 ValueError/TypeError，普通文件打开也可抛 OSError；scheduler 第 151–156 行只接住 BoardError，其他异常会进入第 194–200 行，解绑 client 并移除 handle 和观察线程。之后的周期 recovery 遇到 alive 身份只跳过，不重建观察，这个执行便不再收到取消和外部预算控制。此处是私有边界损坏或 I/O 故障的后果，没有发现模型输入能直接触发它。复现：在重连前将私有 `execution-live.json` 换成损坏 JSON、无效 C-Two 凭据，或让打开操作临时失败；检查错误被归一为不可用、句柄和停止职责仍保留，修好材料后能重新观察，不能让边界读取异常静默丢掉所有者。

B3（P2）：成功启动的执行一直停留在 starting。`buddy/runtime/execution.py:266–269` 自报身份时发送 `phase="running"`；`blackboard/store/store.py:1845–1846` 只接受 starting/executing/finalizing 的状态转换，其他 phase 仅写进事件。后续控制线程只交 activity/waitingReason，没有另一次 executing 进度，因此一个确实在跑的命令或 CLI 回合会直到结果到达才从 starting 转 finalizing/finished，状态展示及以阶段分支的规则都拿不到真实 executing 状态。复现：启动由文件闸门保持的命令，在原生工作已开始且身份已落库后检查 `selectedAttempt.executionState == "executing"`，再释放闸门；现有集成测试只等启动文件、容量或最终 completed，没有该阶段断言。

B4（P2）：同一服务内端点重新连接后，没有按要求补发已经确认过的停止。scheduler 第 144 行的 `stop_sent` 跨重连保存；第 166–172 行清掉 client 时没有清掉它，下一轮第 152–158 行重建 client 后，第 220 行会跳过同一停止命令。这与“每次连上都补发看板上的停止”不一致。复现：让第一个 client 成功接收 stop、随后 wait 失败，再由第二个 client 成功连接相同执行，要求两个 client 都收到同一持久停止且原始宽限不延长。执行侧 `_ExecutionControl.request_stop`（`buddy/runtime/execution.py:692–701`）对重复 Host/Lead wrap-up 不延长期限，底层 `worker_cancel`（`buddy/roles/controller.py:116–127`）也有已强停去重，因此应补齐服务侧重发，不能用当前 `stop_sent` 代替重连协议。

B5（P2，范围未落实）：执行对服务的操作尚未收缩到进度与结果两种。除这两种新操作，`buddy/runtime/execution.py:384–388,469–472` 仍用 nonce 调 `harness_prepare`，第 394 行在预检竞争中还读 `task_get`；`blackboard/service/service.py:574` 仍公开准备入口，准备会维护健康观测和调用回执。实际保留的是“删除 Worker 专属操作后，仍有 harness 准备协议”，与本微任务明确给定的双操作范围不同。复核方法是用记录所有 BoardClient 方法的离线 CLI 夹具，分别走普通启动、预模型失败重试和停止竞争，断言执行到服务的方法集合。若 Host 要保留这项协议，应在任务约束中作明确决定，当前源码不能按原要求算已收缩；这不构成 nonce 鉴权绕过。

准入事务本身未发现同一次执行重复创建或竞争下突破容量的路径：`blackboard/store/db.py:1166–1169` 用 BEGIN IMMEDIATE；store `:1424–1443,1480–1546` 在写事务中检查总额、模型服务和独占资源，第 1596–1668 行把执行、资源、running 状态、事件和回合一起提交。已经取走的微任务不再 queued，后续准入不会再取同一执行；uncertain 和未确认结果仍被 `UNRESOLVED_SQL`（第 525–527 行）计入总额与冻结的模型服务额度。服务的 admission lock 同时保护 stop/restart 标志（service `:932–950`、daemon `:176–177,191–192`），所以停止标志落下后不再提交新准入。已准入到 Popen 之间另有检查（scheduler `:90–93`），但检查后停止仍可并发到来，须靠 A2 的停止投递保证完成结束；这不能用事务原子性替代。

建议 Host 在私有板上让多个线程同时请求同一微任务和不同微任务，验证只出现一个有效执行、总额和模型服务上限均不超额、不同 effort 共用一个模型服务额度、uncertain 继续占位，并在 admission lock 前后插入 stop/restart 屏障。现有 `tests/python/blackboard/store/test_store.py` 和 `test_model_service_concurrency.py` 仍有容量竞争覆盖，D5 指出的是删掉的深队列与 uncertain 组合覆盖。

| 材料或连接 | 当前来源、绑定、权限与清理 |
| --- | --- |
| nonce 和回合凭据 | scheduler `:83` 用服务随机数生成 nonce，store `:1596–1605` 只存校验值；回合凭据由 `workflow.begin_turn` 返回，随 claim 写进 `<state>/harnesses/<adapter>/micro-tasks/<task-hash>/attempts/<attemptId>/execution-start.json`。`protocol/execution.py:34–52,65–92` 创建私有目录和 0600 文件并 fsync，argv 只携带路径。nonce 不进入 spawn.intent/marker 或结果回执 |
| 写服务的认证 | store `:1765–1788` 核对执行号、generation、恒定时间比较的 nonce 校验值、有效执行和中断封存栅栏；无 workerId。持有句柄时 scheduler `:50–68` 对完整身份逐字段核对；缺少句柄时直接允许的分支是 M3 边界，也导致 A4 无法拒绝未接管的启动 |
| 启动、进程身份材料 | `spawn.intent` 在 Popen 前，`spawn.marker` 在父侧捕获完整身份后写到普通执行证据目录；子进程自主取身份、先安装原生创建日志、注册端点，再等身份进度被确认，之后才启动原生工作。对 marker 失败且完整 handle 已存在的情形，scheduler 能继续观察；A4 指出更早的构造窗口 |
| 端点地址、实例、令牌 | `buddy/runtime/execution.py:117–131` 将地址、实例、C-Two 凭据、进程身份、RunIdentity 和 64 位十六进制 token 写入私有 `execution-live.json`；看板不存 token。服务可由 adapter/task/attempt 推导目录，所以服务重启后材料仍可再得；当前缺的是 M3 使用它接回的动作 |
| 端点验证 | `protocol/execution.py:225–268` 验证普通私有文件、完整 RunIdentity、记录的进程身份、hostPid、实例形状、地址与本地端点域；执行侧 `buddy/runtime/execution.py:151–162` 对身份、实例和 token 恒定时间比较。未见错误 token、其他 generation 或其他端点实例能进入原生通道的路径；材料读取的错误类型处理见 B2 |
| 长调用和重复停止 | `_ExecutionLive.wait`（execution `:219–222`）在执行端最多等 3 秒；`ExecutionLiveClient.wait`（protocol execution `:192–195`）给客户端 5 秒，再由观察循环重发，因此该长调用确实有执行侧边界。其余常规 BoardClient 请求没有在这次改动中统一加入显式业务调用超时，不能把 3 秒 wait 的保证扩张到所有 RPC。重复 wrap-up 不延长宽限，force 可以升级，服务重连重发仍缺 B4 |
| 备份和删除时间 | 私有 harness 树不在备份白名单内；启动文件在执行写出确认停止的报告前由 `cleanup_attempt_credentials` 删除（execution `:653–663`、`private_dirs.py:18–23,207–241`），结果回执在持久交付确认/拒绝记录后删除（receipts `:68–69`），端点文件在退出或服务证明身份已消失并清理端点后删除。创建前失败和服务接管的无回执结算没有调用同一凭据文件清理：这些私有文件会留下到工作区/存储清理，不能声称所有停止路径都立即擦除启动材料；未知停止时保留材料有助于继续恢复 |

## C. 存储与升级

C1（P2）：schema 23 的新写入仍使用已被升级替换的 `awaiting-worker`。`blackboard/routing/decision.py:703,718` 在 Router 提交及事件里写旧值，`blackboard/tasks/workflow.py:2767,3272` 在创建辅助微任务和继续微任务时也写旧值；升级却在 `blackboard/store/upgrade.py:52–53,66–67,136` 把存量改成 `awaiting-execution`。服务准入前的自动 CLI 健康刷新筛选仅包含新值（service `:940–948`），所以同样的排队原因会因写入路径和升级时间不同而走不同的预检；同时产生两种当前存储表达。复现：在新板提交 Router、创建辅助微任务、继续 Host 边界，各查 queue_reason 和事件，再和升级后的对应投影比较；把看板限制为这些排队执行，验证自动刷新不会漏掉其 CLI harness。

C2（P2）：`worker-stopped` 的转换遗漏保留命令响应中的扁平 task 投影。升级 `_interruption_result`（`upgrade.py:24–34`）能转换结果信封，`_view`（第 37–60 行）能转换 selectedAttempt 内的结果，却不处理 `task.interruptionEvidence.kind`。该字段由 `blackboard/store/store.py:477–483` 从服务中断结果直接展开，`task_cancel`（第 1118–1121 行）等入口可把完整 task 投影保存到 command response。升级后，同一响应里的 selectedAttempt 可以是 orphan-stopped，外层 task 却仍是 worker-stopped；新查询又从已升级的结果派生出 orphan-stopped。同一命令重放与新读仍有两种读法。工作流缩减后的 task 投影还可能没有 `_view` 用来识别 task 的 spec 字段。复现：从 schema 22 样板的私有副本补入经服务中断产生的结果及包含扁平、嵌套证据的 Host 命令响应，升级后比较命令重放与新查询；只转换明确的服务证据字段，继续保留用户输出中的同名文本。`test_upgrade_fixture.py:299–329` 只合成普通 opaque result，没有这组 interruption 投影。

C3（P2）：旧 Worker 目录清理失败后，正常再次升级无法重试。`install/upgrade.py:525–534` 先删 `upgrade.json` 再删 `workers/` 与 `worker-pool.json`；删除失败只作为 `legacyCleanupError` 返回，已没有待恢复日志。下一次相同源码的 upgrade 在第 438–439 行返回 already-current，不再执行清理；恢复分支第 603–612 行也同样先删日志。故障或在清理前的进程退出会留下永久的退休材料，无法满足该次升级的目录清理要求。复现：在私有升级副本对 `remove_legacy_worker_state` 注入一次 OSError，随后不再注入并重复同一 upgrade，要求清掉残留且无需伪造另一个源码版本。已经验证的新服务不必因此回滚数据库，但需要可重试的清理状态或确定入口。

C4（P3）：状态目录含空格时，活执行会被清点为孤儿。`blackboard/tasks/storage.py:85–89` 用 `--startup\s+(\S+)` 从 ps 命令串截取启动路径，遇到空格即截断，无法找到正确的 harnesses 上级目录；第 98–106 行回退只找 daemon 锁，执行没有持有这些锁，第 110–112 行因此产生 `orphanProcesses`。复现：用 `<private-root>/state with spaces` 的状态目录产生一条执行进程命令行，令 lsof 无 daemon 锁，核对 storage 清点能够归属该执行；可以完全用 ps/lsof 输出夹具测试。`runtime_usage` 第 119 行另按完整运行时路径匹配，当前未由此证明会删除在用运行时，不扩大为回收安全缺陷。

升级正常路径的清点如下。本次未执行样板迁移；`tests/fixtures/upgrade-base` 的基点为 0.39.0、`b9632dd9`、schema 22，fixture 测试中有新旧 DDL 完整比较（`test_upgrade_fixture.py:111–118,240–249`），但 D9 所述的数据保真断言仍不足。

| 对象 | 当前转换或保留策略 |
| --- | --- |
| workers、attempts、身份与中断表 | `blackboard/store/upgrade.py:89–117` 在一笔事务内保存记录、删除旧表，按新 SCHEMA 重建 attempts、attempt_process_identities、attempt_interruptions 和 attempts 索引；workers 不重建。执行的 worker_id/worker_identity/worker_instance、claim_request_id、lease_seconds/lease_expires_at，以及已无用途的 claim capability 随新 DDL 删除 |
| 身份链 | 按旧 ordinal 顺序保留每个执行最后一条身份，新表只剩 attempt、boot、PID、启动时刻、进程组/Job；Worker 身份和 ordinal 不再存储。既有完整记录和 fresh DDL 同形，没有运行期双格式解释 |
| worker-stopped | 中断表 evidence_kind、结果信封的服务 interruptionEvidence、task.failed 事件和 interruption_seal_pending 事件转换成 orphan-stopped；C2 所列的响应扁平投影遗漏 |
| 事件与命令回执 | worker.*、harness.prepare 的旧命令回执删除，保留命令按已知 task/attempt 结构转换 response；当前生产的相关事件定向转换。已不再生产的 worker.registered 等历史事件保留原 kind 与原载荷，kind 已表明其历史事实，读取处没有新增旧 schema 分支；用户结果内同名键不应递归改写 |
| 原子性和校验 | foreign_keys 暂停期间在 BEGIN IMMEDIATE 中重建，更新 schema_version 后做 foreign_key_check 和 integrity_check，再 COMMIT；任何异常 ROLLBACK（upgrade `:83–85,136–147`）。不能用目标新服务启动后的指纹等于迁移后指纹，替代迁移前后保真比较 |
| 空闲和旧 Worker 退休 | 安装在准备前、写升级栅栏后、停止旧 daemon 后反复确认无活动执行及 shutdown_confirmed=1（`install/upgrade.py:57–86,431,447,480,492`）；Host 边界只有没有已记录/排队续办时才算空闲。detach 先让旧 daemon 退出并取得 owner 锁，再按 0.39.0 的 `retire.request` 协议等待旧 supervisor 锁释放（第 146–173 行），不是拿 PID 猜停止 |
| 备份和回滚 | 新 `backup.STATE_FILES` 不含 worker-pool，preflight 不再收 workers，harness 私有根也不备份；旧 Worker 文件直到新服务及保留数据验证成功后才删除。转换或目标启动失败时，第 545–581 行恢复备份和旧运行时，无法证明回滚则保留升级日志与备份；因此未把未完成回滚伪装成成功。删除后的失败重试另见 C3 |
| 运行时保留与检查清场 | `install/entrypoints.py:8–20` 将服务角色改成 daemon/execution，storage 的 argv 和打开文件观察仍保守保留在用或观察未知的运行时。`cli/checks.py:446–458,641–665,692–746` 不再认 supervisor 锁或写 Worker stop，仍检查私有根关联进程、daemon 锁及观察失败；有执行幸存时应保留证据并报清场未完成，不能误称已清理。A2 可能使这一清场只能报告残留 |

## D. 被删或削减的测试

D1（P1）：自动重试临时准入冲突的测试丢失，且对应 B1 的实际缺陷。删除的 `tests/python/blackboard/tasks/test_worker_claim_stash.py::test_worker_retries_a_collection_conflict_without_waiting_for_an_unrelated_event`（旧约 159–193 行）守的是驱动循环会继续，不只是单次 claim 的返回值；当前第 132–155 行仍只验证手动第二次 admission。应把旧的循环级断言搬到 ExecutionScheduler，要求先抛暂时 CONFLICT、再无新事件地成功调度。

D2（P1）：停止职责移到服务后，零等待投递和执行冻结的关键断言未移植。`test_daemon.py` 删除的 late-slot、signal-style reentry 三组测试（旧 111–179 行）要求零等待停止也保留每个受管进程可收到的停止；`buddy/runtime/test_worker_invariants.py` 旧 `test_host_grace_deadline_forces_stop_and_cannot_be_reported_as_a_summary`、`test_immediate_stop_keeps_each_executors_reaping_grace` 守着宽限、强停与回收。新 `test_execution_scheduler.py:132–154` 只给成功 stop 替身；`test_parallel_dispatch.py:168–190` 让原生命令忽略 SIGTERM，未冻结执行端点。对应 A1、A2，需恢复零等待投递屏障、RPC 持续失败、真实执行冻结与强停后原生组停止的断言。

D3（P2）：原生日志从不完整到完整的多轮恢复没有替代。旧 `blackboard/store/test_interruption_native_tree.py::test_native_journal_group_must_be_gone_before_settlement`（旧约 52 行）检查原生工作结束后最终能结算；新 `test_interruption_recovery.py:124–132`、`test_interruption_native_tree.py:14–23` 仅检查第一次保守 false。第 134–146 行的“较早未确认结果”测试没有相同私有回执或相同 recovery 命令号。应增加 A3 的两个顺序场景，最后检查资源释放、同一回执重放和保留原结果，不能只测一次调用没有误判。

D4（P2）：保留的原生会话查询接口失去全部专门覆盖。整个 `buddy/runtime/test_worker_sessions.py` 被删，但 `blackboard/tasks/worker_sessions.py:16` 和 service 的 worker_sessions 入口仍在。旧三例中的 DSH CLI 拒绝，以及 `test_codex_list_uses_board_only_and_never_guesses_other_sessions`、`test_codex_list_accepts_actual_stopped_adapter_fixture` 所守的 Codex CLI 回执归属、错误 taskId 排除、同会话续回合去重和保留回执读取仍是当前行为；现有测试没有同等 worker_sessions 调用。应在新执行回执下恢复这些断言，避免把查询 Worker buddy 会话的功能误当成 Worker 运行时一起删除。

D5（P2）：深模型队列和 uncertain 执行同时占总额/模型额的回归用例缩掉。`blackboard/tasks/test_parallel_dispatch.py` 旧 `test_more_than_hundred_full_model_service_rows_cannot_hide_other_work`、`test_uncertain_attempts_keep_their_total_and_model_service_slots` 被删。新第 57–80 行仅用一个被占满模型服务的后续 Router 和一个命令，不能约束 `store.py:706–759` 的窗口前筛选、分页扫描，也没有 uncertain 的模型槽断言。总额/模型额的一般竞争、effort 合并在 `test_store.py`、`test_model_service_concurrency.py` 仍有覆盖，不能替代这两种组合。可直接把旧规模和 uncertain 场景改用 admission 辅助函数。

D6（P2）：Windows Job 身份判断与恢复停止的专门分支测试丢失。`protocol/test_process_identity.py` 旧 `test_windows_job_presence_absence_and_reverified_stop`、`test_empty_windows_job_waits_for_original_root_exit`、`test_windows_system_source_reopens_named_job_for_query_and_stop` 删除，而 `protocol/process_identity.py:209–233,305–312,343–345` 保留这些逻辑。当前 `test_windows_process.py` 检查创建/Job 持有边界，未替代身份恢复时的 job_members/stop_job 分支。应恢复源替身的 Job 存在、空 Job 但根仍在、重开 Job 核验与停止断言；这类离线测试不等于 Windows 实机验收。

D7（P2）：新端点的边界拒绝、文件权限和令牌不泄漏断言没有完整迁移。删除的 `buddy/roles/test_role_live_seam.py::test_missing_malformed_linked_or_oversized_material_never_binds` 和 `buddy/harnesses/test_pending_ready_receipt_guards.py` 三例守着非普通文件、超限、读取时增长；`buddy/harnesses/zcode/test_zcode.py:248–253` 还删掉旧 executionLiveFile 的 0600、descriptor 结构和 token 不进入报告的断言。新 `test_execution_process.py:109–166` 测新身份鉴权、正常连接及材料保留，但没有等价覆盖所有拒绝路径与令牌泄漏边界。源码目前确实使用 0600，不据测试缺失声称已泄密；B2 则是已能从代码追到的错误处理缺陷。应把断言移到 `protocol/execution.py` 的新 reader、ExecutionLive 输出和公共视图。

D8（P2）：原生活动到服务静默提醒的真实链路测试删除。`blackboard/service/test_reminders.py` 的 `test_private_service_silence_is_once_per_native_activity_period`、`test_private_service_silence_reports_a_native_tool_in_progress` 原来让真实私有服务接收 CLI 夹具活动；现在只剩 store 层直接注入 execution_progress 的 `test_native_silence_records_tool_evidence_then_resets_after_native_event`，`protocol/test_activity.py` 也主要守事实格式与去重。新的 in-process native channel、执行 flush_hints、服务入库再到提醒之间没有同等端到端替代。应用离线 CLI 工具活动夹具恢复一次提醒、重复静默不膨胀、原生活动后重新计时的整条链路。

D9（P2）：样板升级的数据保真比较削弱，尚无完整的定向转换断言替代。`blackboard/store/test_upgrade_fixture.py:230–234` 把旧 `self.assertEqual(after, before)` 换成 schema、event head、无 workers 表和 attempts 行数相等，随后只比较部分设置与记录；`backup.database_snapshot` 仍提供各表指纹。升级确实应改表和指定字段，所以旧整体相等必须修改，但不能只以行数和迁移后的指纹作目标。应逐表列出允许变化，对未改表全量比较，对 attempts、身份、interruptions、commands、events、tasks 按明确转换比较，并增加 C2 的扁平证据投影；目前没有这样的完整替代。

D10（P2）：安装阶段跨真实运行时的 Host 回执与等待边界回归被删除。`install/test_upgrade_host_cutover.py::test_same_schema_upgrade_preserves_host_boundary_receipts_and_retriable_wait` 原来走两个运行时，验证等待中断可重试、Host 命令回执及后续继续；现有 `test_upgrade_waiting_host.py` 只分别检查存储/CLI 栅栏，不能覆盖整个切换。M5 会扩展安装规则，当前 Host 边界与幂等控制却仍需保存；应在安装阶段移植这条整合测试，并保持本次 schema 22→23 的空闲要求。本条只指出回归屏障缺失，没有声称 M5 已完成或必须在 M2 实现其新能力。

以下是 18 个整体删除的测试/夹具文件的逐项归类；表内省略共同前缀 `tests/python/`。只依赖已删除 Worker 池、登记、认领、续租、转发或 supervisor 身份的断言不再成立，删除合理；保留行为的替代与缺口分别列出。

| 删除文件 | 归类与目前承接处 |
| --- | --- |
| `blackboard/service/test_live_holder_integration.py` | Worker holder 转发层已删除；服务直连与执行死亡由 `test_execution_process.py`、`test_execution_scheduler.py`、`test_parallel_dispatch.py:151` 承接，边界异常见 D7 |
| `blackboard/service/test_live_registry.py` | Worker 挂接/续租重挂契约已删除；新 registry 的身份过滤经 inquiry 测试与 scheduler 观察测试覆盖，重连重发仍缺 B4 |
| `blackboard/service/test_worker_event_recovery.py` | Worker 登记/回收事件不再生产；执行活动幂等在 `protocol/test_activity.py`，执行恢复在 `test_interruption_recovery.py` |
| `blackboard/service/test_worker_pool.py` | 池大小、slot 名称、supervisor 拉起/退休随池删除；真实容量改由 `test_parallel_dispatch.py:105`，其余容量缺口见 D5 |
| `blackboard/service/test_worker_visibility.py` | health 中 Worker 池可见性已删除；capacity/activeWork 仍由 liveness、daemon 和 parallel_dispatch 测试检查 |
| `blackboard/store/test_worker_orphan_settlement.py` | Worker orphan/owner instance 路径删除；新原生日志恢复由 `test_interruption_recovery.py`、`test_interruption_native_tree.py` 和 `test_interrupted_seal.py` 承接，多轮缺口见 D3 |
| `buddy/roles/test_role_live_seam.py` | 旧角色侧跨进程文件桥已删除；新执行端点鉴权由 `test_execution_process.py:109` 承接，材料拒绝覆盖见 D7 |
| `buddy/runtime/fixtures/live_runtime_peer.py` | 已删除 Worker live 协议专用夹具，无独立保留行为 |
| `buddy/runtime/test_execution_handoff.py` | Worker 接力改服务接力；`test_interruption_recovery.py:85`、`test_execution_scheduler.py:96` 和真实死亡测试承接，含未确认回执的第二轮见 D3 |
| `buddy/runtime/test_execution_signals.py` | 私有停止文件协议删除；早期信号保存由 `test_execution_delivery.py:77` 承接，端点强停由 scheduler 测试承接，真实冻结和零等待仍缺 D2 |
| `buddy/runtime/test_live.py` | Worker 在线转发与生命周期已删除；通用 C-Two/live 协议测试仍在，新执行端点测试承接 direct path |
| `buddy/runtime/test_live_lifecycle_integration.py` | supervisor/Worker 的挂接回路删除；直连、退出和材料保留有新测试；M3 的活执行重连尚未替代旧重挂场景，不能据此宣称恢复已通过 |
| `buddy/runtime/test_repair_recovery.py` | 租约重挂与 Worker 本地 repair 删除；未知身份保留、nonce 恢复在 `test_interruption_recovery.py:44–83`，本次不要求恢复被删除的租约机制 |
| `buddy/runtime/test_worker_idle.py` | 空闲 Worker 等待不再存在；空准入不自唤醒改在 `blackboard/store/test_store.py:331`，无命令回执增长改在 `blackboard/tasks/test_empty_claims.py:30` 检查 |
| `buddy/runtime/test_worker_invariants.py` | Worker 身份与循环专属断言删除；执行回执、部分产出、活动分别在 `test_execution_delivery.py`、`test_interrupted_seal.py`、`test_activity.py`，强停故障覆盖仍缺 D2 |
| `buddy/runtime/test_worker_live_wiring.py` | Worker→执行的中间一跳已删除；直接请求用新的 runtime execution 与 inquiry 测试，不能保留旧双跳协议断言 |
| `buddy/runtime/test_worker_runtime.py` | Worker 进程管理、注册、续租不再有实现；命令进程执行/交付由 `test_parallel_dispatch.py` 承接，DSH CLI 离线完整执行仍在 `test_workflow_worker.py:132` |
| `buddy/runtime/test_worker_sessions.py` | 查询 Worker buddy 原生会话的功能仍在；没有等价替代，见 D4 |

105 个修改测试文件中，26 个 diff 包含被删的 test_ 定义行。逐项归类如下；同名改写也算入这一清单，不能仅凭删除行数判断丢了覆盖。

| 目录与文件 | 删减的性质与仍需保留的行为 |
| --- | --- |
| `blackboard/service/`：`test_daemon.py`、`test_reminders.py`、`test_service_environment.py` | 池规模、late supervisor、Worker 启动入口随实现删除；daemon/执行容量与服务环境 allowlist 仍有替代；零等待停止与提醒完整链路分别见 D2、D8 |
| `blackboard/store/`：`test_blackboard.py`、`test_interruption_native_tree.py`、`test_interruption_recovery.py` | Worker 同号认领重放、每 Worker 一个执行、external 执行和租约失效已无原契约；代次/nonce、未知身份与真实原生停止仍由新测试约束；多轮恢复缺口见 D3 |
| `blackboard/tasks/`：`test_empty_claims.py`、`test_inquiry.py`、`test_parallel_dispatch.py`、`test_reminders.py`、`test_scheduling.py`、`test_worker_claim_stash.py`、`test_workflow_preparation.py` | Worker 命名和认领回执退出；空准入、被准备的固定输入、原生能力与问询结果仍保留。删除 external 静默提醒特例合理；准入循环、深队列/unknown 配额见 D1、D5；旧 Worker attach 身份断言改成执行端点鉴权 |
| `buddy/harnesses/test_pending_ready_receipt_guards.py`、`buddy/runtime/test_execution_process.py` | 旧文件桥和 Worker 代交部分退休；命令/离线 DSH CLI 完整执行迁到 parallel_dispatch/workflow_worker，交付退避与先提示后结果迁到 execution_delivery，身份/marker 故障迁到新 execution_process；新 reader 的拒绝矩阵见 D7，A4 的更早构造窗口未覆盖 |
| `cli/`：`test_checks_cleanup.py`、`test_cli.py` | supervisor 专用停止请求和 Worker CLI 枚举随实现删除；仍要求幸存执行/观察失败保留私有根，新的 daemon/进程残留断言继续保留 |
| `install/`：`test_launcher.py`、`test_packaging.py`、`test_upgrade.py`、`test_upgrade_host_cutover.py` | Worker 环境变量、入口及 claim 栅栏断言换为当前执行机制；凭据仍须留在调用者的窄权限路径、服务使用 allowlist；真实跨运行时 Host 边界缺口见 D10 |
| `protocol/`：`test_ctwo_integration.py`、`test_ctwo_service.py`、`test_process_identity.py`、`test_rpc_config.py`、`test_state_boundaries.py` | Worker holder、等待退休与 supervisor --state-dir 专属断言不再成立；真实执行端点回收和私有服务清场由新 direct path 测试承接；完整身份保守判断仍保留，Windows 恢复分支见 D6 |

其余 79 个未删除 test_ 定义的修改文件也逐处核过，共有 22 个文件的 45 行断言删改。routing 下的 16 个文件、工作流/Lead/工作区/权限等 tasks 文件、各 CLI harness 的 native_run 与 controller 测试、原生活动/日志/公共状态、CLI Host/有序收尾以及测试支撑夹具主要是 Worker actor、claim replay 与 lease 字段换成 execution admission/actor，业务结果断言仍在。`test_router_dispatch.py`、`test_workflow_stash.py`、`test_refused_resume.py` 中原“同号 claim 重放”改成“再次准入为空”符合已删除认领协议；不是继续要求旧的 claim 回执。发现的实质削弱已列 D7（ZCode CLI 旧端点安全断言）及 D9（fixture 保真比较）；`test_current_core.py:139–140` 还留有一条重复 METHOD_MAP 断言，仅属冗余。本次没有以这些机械替换掩盖删掉的行为，也没有把仅改调用名称的测试重复计算成新覆盖。

最后对照 `docs/design/adr031/service-scheduling-notes.md`：说明的“每次重新连上端点都会补发”与 B4 不符，“已经创建的句柄由服务继续持有”漏掉 A4 的 Popen 已成功但 ProcessHandle 未构造完成窗口，“新的完整停止证据后仍进入同一结果提交代码结算”漏掉 A3 的命令号/subject 冲突；“冻结执行约 8 秒后结束”没有被当前提交的冻结测试证明，现有测试只让原生命令忽略 SIGTERM，A1 的执行端点冻结仍会失去升级计时。说明把 execution_progress/execution_result 称为仅有的写操作，同时又保留会写健康记录和命令回执的 harness_prepare，B5 所述范围尚未落实；worker-stopped 的定向转换和退休目录清理也分别有 C2、C3 的缺口。说明对 M3 活执行接回、M4 控制台、M5 安装边界尚未完成的限定与源码一致；其所列测试批次和退出码本次没有运行核验，不作为审查通过依据。已读完本微任务要求的差异与四项清点，未运行验证的边界如前述。
