# 控制面时限改动的独立审查

审查对象是提交 `9506f225` 相对基点 `cbb5ffc6` 的 `src` 与 `tests` 差异。只读代码与文档，没有运行检查套件、服务、harness 或任何测试；下面每条“后果”都是读代码推出的，复现做法是建议的测试，没有执行。行号指 `9506f225` 上的文件。

## A. 去掉的时限有没有留下永远等下去的地方

### A1（高）Worker 预检在探测一直“未知”时无限循环，原因对 Host 不可见

位置：`src/hey_my_buddy/buddy/runtime/worker.py:641-681` 的预检循环；`src/hey_my_buddy/blackboard/service/harness_health.py:189-190` 与 `:211-228`；`src/hey_my_buddy/buddy/harnesses/discovery.py:29`（`_SCAN_TIMEOUT = 20.0`）与 `:773`。

调用：Worker 反复调用 `harness_prepare`，只要返回的记录 `status == 'unknown'` 或 `lastProbe.status == 'unknown'` 就 `stop.wait(CLAIM_IDLE_SECONDS)`（2 秒）后用新的 `commandId`（`-unknown-N`）再调。服务端每次都 `refresh(preflight=True)`，而 `lastProbe` 为 unknown 时缓存被绕开（`harness_health.py:189-190`），于是每轮都重跑一次最长 20 秒的扫描。

后果：原则写的是“到点只记未知，由下一次尝试、下一次扫描或更长的等待来定”，但下一次扫描用的仍是同一个固定的 20 秒总预算，没有任何地方把等待放长。一台机器上候选多或 harness CLI 冷启动慢、稳定超过 20 秒时，每一轮都是 unknown，这个微任务永远不启动也永远不失败。Host 看到的是一个已领取、没有进展的微任务；控制台上 harness 显示的是保留下来的 `ready`（`lastProbe` 不在控制台读取范围内，`apps/console/src` 与 `src/hey_my_buddy/console` 都不读它），或首次扫描时的 `unknown`。基点在同样情形下给出 `unhealthy` / `scan-timeout`，并带有可操作的补救文字（`discovery.py:724`“请缩小候选范围后重新检测”）；现在这条信息不再到达 Host 或用户。另有两点：一是记录仍是 `ready` 且 `available` 为真时 Worker 也不用它，只因 `lastProbe` 未知（`worker.py:677-679`），保留下来的 ready 实际不被信任；二是每轮循环在服务端写一条 `harness.probe_unknown`、一条名为 `harness.execution_selected` 的事件和一条命令回执（`src/hey_my_buddy/blackboard/service/service.py:566-568`），约每 22 秒一组，等待一天就是几千行，而 `execution_selected` 记下的是一次并没有被选用的未知记录。

能结束它的途径：Host 取消。预检期间新加的续租线程会观察到取消并以 `cancelled` 结束（`worker.py:645-647`），属于“后台的执行可以一直等，Host 能停下它”，但 Host 没有任何依据知道该停。

复现：在 `tests/python/buddy/runtime/test_control_waits.py` 的夹具上让 `client.call('harness_prepare')` 始终返回 `{'status': 'ready', 'available': True, 'lastProbe': {'status': 'unknown'}}`，让 `stop.wait` 计数，在第 N 次调用时由测试置取消；断言 `_execute_selected` 从未被调用、`client.progress` 从未被调用。证伪方向：如果实现让后续扫描放长预算或把“等待探测”作为进度报给 Host，这个测试应改为断言那条事实出现。

### A2（高）用户敲的命令在服务或升级的替换服务“活着但不就绪”时无输出地一直等，并且持有启动锁

位置：`src/hey_my_buddy/protocol/transport.py:376` 取得 `control-start.lock`，`:417-430` 的 `while True`；`src/hey_my_buddy/install/upgrade.py:183-196` 的 `while True`，调用方 `upgrade.py:497`、`:545`。

调用：冷启动时 `_ensure_service` 拉起 daemon 后，只在 `_healthy()` 成功或子进程退出时返回；`upgrade.start` 只在 `control.json` 写出对应 pid 或子进程退出时返回。两处原先的 20/300 秒与 45 秒都已删除。

后果：daemon 进程活着却始终不就绪（启动中卡在存储初始化或升级、文件系统挂起、或 `ping` 一直得到 `SERVICE_UNAVAILABLE`）时，用户在终端敲的命令、或 Host 经技能启动器发出的那一条调用没有任何输出地停住；更糟的是它一直持有 `control-start.lock`，之后所有需要冷启动的调用都排在 `locking.lock(lock_fd)` 上一起停住。唯一的结束途径是外部杀掉这个 CLI 进程（例如 Host 所在 harness 的工具超时，Claude Code CLI 与 Codex CLI 都有自己的命令时限），这时调用方看到的只是被杀，不是一句“服务还在启动、pid 与日志在哪里”。对升级要分开看：基点的 45 秒到点抛错后，`upgrade.py:523-545` 会走回滚并在 30 秒锁等待上进入 recovery-required，这确实是“凭时间下结论”，去掉是对的；但修法应当是在调用方把“启动未知”与“启动失败”分开（保留日志与 journal、不回滚、返回未知），而不是让等待没有尽头。服务启动处的 `SERVICE_START_TIMEOUT` 本来只结束这一次等待、不改任何状态、不发信号，按要求属于“只结束等待的那一类不改”，除非长度明显不合理；20 秒对非安装的启动在有升级要做时可能偏短，但这是调整长度或把到点改成“启动中”而不是失败的问题，不是删掉的理由。

复现：在私有状态根上用一个只 `sleep` 不写 `control.json` 的假 daemon 模块替换启动入口（或 `mock` `subprocess.Popen` 返回一个 `poll()` 恒为 None 的对象、`_healthy` 恒为 None），在线程里调 `_ensure_service`，断言 2 秒后线程仍阻塞；再在第二个线程里调一次，断言它阻塞在启动锁上。升级侧用 `test_upgrade.py` 现有的 `child.poll.return_value=None` 夹具且不写 `control.json`，断言 `upgrade.start` 不返回。

### A3（中高）Router 预算的起算点前移后，基点里会成功的 Router 调用会被换掉；执行后的核验也被算进预算

位置：`src/hey_my_buddy/buddy/roles/router.py:106-119`、`:144-158`；`src/hey_my_buddy/buddy/roles/router_input.py:145`、`:178-191`；对照 `src/hey_my_buddy/blackboard/routing/decision.py:66`（`TIMEOUT_GRACE_SECONDS = 10`）、`:678`、`:1353-1355`，以及 `src/hey_my_buddy/buddy/runtime/worker.py:813`、`:866-883`。

先说与基点的真实差别。基点里 Worker 的监督期限已经从 `worker_start` 之前算起（`worker.py:813` 的 `started`，期限是 `routerBudgetSeconds`，即预算加 10 秒宽限），所以“准备算进 Router 预算”在 Worker 一层早已成立，只是有 10 秒宽限吸收准备时间；原生调用自己拿满额预算，发布端（`decision.py:1353`）比对的是原生自报的 `elapsedMs`。现在原生只拿 `floor(deadline - now)`，`deadline = started + timeoutSeconds`，不含那 10 秒宽限；发布端比对的是 `max(外层墙钟, 原生自报)`（`router.py:152-158`），外层墙钟从准备之前量到执行后核验结束。

后果一：以 brief 预算 60 秒为例，准备用 8 秒、原生回答用 55 秒。基点：原生在自己的 60 秒内答完，Worker 期限 70 秒未到，发布端看到 55 秒，发布。现在：原生只拿 52 秒，到点 `deadline` 映射为 `router-budget-exhausted`（`router.py:179-180`），黑板换下一个 Router，下一个 Router 重做同样的准备，同样吃掉 8 秒。也就是说准备慢会让本来不会失败的 Router 被换掉，而换 Router 对准备慢毫无帮助。这改动了 ADR-022 第 5 条里预算的含义：那条把硬预算定为“路由内部换下一个 Router 的触发条件”，换人的依据应当是 Router 自己慢；把我们自己的准备与核验时间算给 Router，是把与 Router 无关的“慢”当成了换人的结论。这与给实现方的要求“Router 的预算不动、收尾宽限不动”也不一致：数值没动，但 10 秒宽限对准备时间的吸收被拿掉了。

后果二：执行后的核验共用同一个期限。原生在期限前几秒答完时，`router_input.verify` 开头的 `_remaining()` 立即抛出，或 Git/摘要中途到点，结果都是 `unchanged: None`、`router-budget-exhausted`，一个已经得到的有效回答被丢弃并换 Router。`verify` 末尾（`router_input.py:185`）与 `prepare` 末尾（`:145`）还各有一次 `_remaining()`：核验与摘要已经完成、事实已经拿到，仅因为此刻过了期限就把“未变”改记为“未知”，这正是“凭到点改结论”。另外 `unchanged` 为 None 时镜像不删除（`router.py:150-151`），每次这样的预算耗尽都留下一份完整输入树的副本，直到尝试目录被别的清理带走（我没有追到这条清理路径）。

复现：在 `tests/python/buddy/roles/test_role_controller.py` 的 `RouterCallPointTests` 里用现有的 `router.time.monotonic` 补丁，让 `prepare` 推进 8 秒、预算 60，断言 `preparation.request.budget['timeoutSeconds']`；现在是 52，基点语义下原生有完整的 60。核验一侧：仿照 `test_final_input_verification_uses_original_router_deadline`，让 `collect` 时的 monotonic 为 `started + 61`、原生 `usage.elapsedMs` 为 55000、`digest` 返回期望值，断言结果是否发布；现在得到 `router-budget-exhausted`。

### A4（中）服务与 Worker 内的工作区 Git 现在没有任何尽头，也没有停止途径

位置：`src/hey_my_buddy/blackboard/tasks/workspace.py:43-50`、`:124`、`:155`、`:338`。

调用：`remaining_git_seconds()` 只在 Router 设了 `git_deadline` 时有值，其余所有调用（服务里的 `prepare`、`verify`、`host_seal`、`resolve`、`cleanup_*`，Worker 里经 `turn_io` 与 `partial_outputs` 的封存）都以 `timeout=None` 运行，`fast-import` 的 `process.wait(None)` 同理。

后果：基点的 60 秒到点在一些调用方确实变成了状态结论（例如 `src/hey_my_buddy/blackboard/tasks/workflow.py:6267-6290` 把续接准备失败记为 attention、把续接作废），去掉它符合原则。但现在一个不返回的 Git（部分克隆的懒取在网络上停住——服务一侧的 `_git_command` 没有设 `GIT_NO_LAZY_FETCH`，Router 一侧设了；过滤器或其他外部进程停住）会让服务的一个请求线程和该检出的工作区锁一直被占着。Host 的取消不会到达这个 Git 子进程，服务停止也只能靠重启；Host 敲的那条 `submit`、`conclude` 等调用没有输出地停住。概率不高，但这是唯一一处“后台也停不下来”的等待。

复现：在 `tests/python/blackboard/tasks/test_workspace_git_errors.py` 里把 `PATH` 前置一个名为 `git` 的、只会 `sleep` 的脚本，不进入 `git_deadline`，在线程里调 `workspace._git(root, 'rev-parse', 'HEAD')`，断言 2 秒后仍阻塞。证伪方向：如果要保留“只结束等待”的上限，到点应返回一个不触发作废的未知错误码，测试改为断言该码并断言续接没有被作废。

### A5（低）Claude Code CLI 的元数据读成“未知”时被当作可用，且每次读取都重跑一次原生读取

位置：`src/hey_my_buddy/buddy/harnesses/claude/adapter.py:52-64`、`:149-150`；`src/hey_my_buddy/buddy/roles/run_execution.py:807-826`。

后果：`discover_models` 等待到点后返回 `status: unknown`、`providers: []`，`_load_native_metadata` 不报错也不缓存，于是没有选定记录时 `available()` 返回 `(True, None)`，未知被当成了“可用”；而且每一次 `available()`（`registry.py:194`、`:254` 的能力报告与 Worker 能力列表）都会在 `_metadata_lock` 下重跑一次最长 30 秒加 8 秒回收的原生读取，串行排队。docstring（`adapter.py:130-135`）仍写着“有界失败也缓存，以免控制台反复拉起 CLI”，与现在的行为不符。

复现：补丁 `run_execution.discover_models` 返回未知目录并计数，连续两次调 `ClaudeAdapter().available()`（不设 `runtime_selection`），断言返回值与调用次数；现在是 `(True, None)` 且调用两次。

### 站得住的部分

DSH CLI 在普通 Worker 下的协议请求现在用无穷的执行期限（`src/hey_my_buddy/buddy/harnesses/dsh/native_run.py:610` 及各 `_native_call` 处）。阻塞在请求里时不会看取消标志，但 Host 停止会在宽限到点后走强制停止，整个进程组被杀，连接关闭后请求以 `AcpConnectionClosed` 结束，所以这是“后台可以一直等、Host 能停下”的一类，站得住；显式中断后的 close 仍保留 30 秒（`native_run.py:1428`），属于转向更安全一侧，站得住。运行时依赖安装去掉 900 秒（`src/hey_my_buddy/install/runtime.py:393-400`）：uv 自己的网络读超时会让停住的下载以错误退出，这是事实而不是时间结论；这一点依据的是 uv 的公开默认值，我没有在本机核对。本机 IPC 自检去掉 1 秒的 `settimeout`（`transport.py:293-318`）：回环上对一个已在监听、积压为 1 的套接字只连一次，`connect` 与 `accept` 都不会无尽阻塞，沙箱拒绝会以错误返回，站得住。未知在下游的消费：健康扫描未知时保留原记录、不使可用的 buddy 失效（`harness_health.py:211-228`）；Codex CLI 账户读取到点记未知、保留计费；模型目录未知在目录存储里是既有的一等状态（`src/hey_my_buddy/blackboard/catalog/catalog_store.py:91-107` 保留上一份可信目录），这几处都没有把未知当成不可用、失败或已停，站得住。唯一的例外是 A5 把未知当成了可用。

## B. 过度防御与多余的机制

### 站得住的

预检期间的续租线程（`worker.py:641-645`、`_Renewal` 在 `handle is None` 时只续租与观察取消）是必需的：预检现在可以等任意久，没有续租租约就会过期。读 blob 时借一个单线程池给阻塞的 `readline` 计时（`router_input.py:92-113`），是在 Router 预算内结束一次可能永远阻塞的管道读的直接做法，基点在两个 blob 之间才检查期限，阻塞的读本来停不下；用 `selectors` 也可以，但不更简单。`discover_models` 把外层等待到点改为未知目录（`run_execution.py:821-824`）是必需的，因为服务里的 `_refresh_harness_catalog` 对任何 `BoardError` 都会 `invalidate` 这个 harness（`service.py:505-507`），不改就是凭 30 秒判失效。DSH CLI 的 close 统一走 `_native_call`，顺手删掉了发现路径里重复的三段异常映射，是减法。把 Router 准备阶段的 `BoardError` 附上 `ProcessCreationEvidence(False, True, None)`（`router.py:120-123`）复用的是 `launch_controller` 已有的 `owned_popen_evidence` 约定，不是新机制，方向站得住（细节见 B4）。

### B1 绝对期限跨进程层层传递，是对同一个预算的第四、五道执行

位置：`router.py:124-126`；`src/hey_my_buddy/buddy/roles/controller.py:207-232`；`run_execution.py:430-431`、`:545`、`:576`；`src/hey_my_buddy/buddy/roles/run_controller.py:223-236`；`src/hey_my_buddy/buddy/harnesses/native_support.py:42-69`。

同一个 Router 预算现在由这些地方各执行一次：原生的 `timeoutSeconds`（已经是 `_review_seconds` 算出的剩余秒数）、`launch_controller` 的 `handle.deadline`、Worker 循环的 `started + routerBudgetSeconds`、发布端的 `elapsedMs` 比对，再加上新的 `deadlineMonotonic` → `absolute_execution_deadline` ContextVar → `execution_deadline` 取最小值，以及 `run_controller` 里“已过期就不调用原生、合成一个 `group_state='gone'` 结果”的预检查。新增这一条通道换来的只是控制器进程启动那一两秒的延迟不被原生重新计入；而这一两秒本来就会被 Worker 循环的取消与 `handle.deadline` 截住。它还把 `time.monotonic()` 的取值写进控制文件交给另一个进程用：Python 文档说 monotonic 的参照点未定义、只有同一进程内两次取值之差有效，在 macOS 与 Linux 上它实际是全系统的时钟，所以今天能用，但这是一个文档之外的假设。更简单的写法：保留已经算好的剩余 `timeoutSeconds`，删掉 `deadlineMonotonic`、ContextVar 与 `run_controller` 的预检查，`_launch` 的 `handle.deadline` 沿用原来的相对计算。验证办法：删掉后跑 `tests/python/buddy/roles/test_registered_review.py`、`test_role_controller.py` 与 `test_router_failover_integration.py`，除了专门钉这条通道的三个新测试外应当都通过。

### B2 同一个剩余预算紧挨着算两次

`prepare_router_review` 在 `router.py:74-78` 算一次 `_review_seconds` 并在失败时 `discard`，`DecisionAdapter.start` 紧接着在 `:113-119` 再算一次、再写一套 `discard`，然后 `replace` 回请求。保留一处即可；第二次只为吃掉两行代码之间几微秒的差。

### B3 预检续租到执行续租的交接写得比需要的复杂

`worker.py:774-777`、`:820-822`、`:853-862`：预检线程在原生启动后被 `stop`、`join`，若期间看到取消就把 handle 塞回这个已停的线程再手动 `_request_cancel`，随后再起一个新的 `_Renewal`；为此 `_execute_selected` 里有三处 `cancel_view` 检查。更简单的写法是只有一个 `_Renewal`：预检时 `handle` 为 None，原生启动后把 `handle` 与 `implementation` 赋给同一个线程，若已记下取消就立即对新 handle 发一次取消；`_request_cancel` 本来就按 `handle.cancel_requested` 去重。这样不需要第二个线程，也不需要三处检查点。

### B4 “没有子进程”的证据只对 Router 生效，且同一判断写了两遍

`worker.py:551-553` 先算 `no_child_proof`，`:571-573` 又用几乎相同的条件改 `shutdown_confirmed`，两处都限定 `spec['adapter'] == 'decision'`。`owned_popen_evidence` 是通用约定，`launch_controller` 对所有执行器都会附上；如果这份证据可信，就应当对所有执行器一视同仁，不为 Router 单列一条规则；如果不可信，Router 也不该用。另一个更小的办法是把 Router 的输入准备移到 `start_invoked` 之前（放进 `prepare_worker_run` 那一段），这样基点已有的“`start()` 之前失败即可确认没有子进程”（`worker.py:568-570`）直接覆盖它，不需要任何新证据与新分支。

### B5 发现路径里手写的“哪些错误码不算拒绝”清单

`run_execution.py:817-820` 列了七个错误码，用来区分“外层 30 秒到点后我们自己 `terminate` 引起的错误”和“原生明确拒绝”。真正需要区分的只是外层等待到点之后的那一瞬间，而发现的内层期限是 25 秒、到点已由 `run_controller` 报成 `unknown`；外层到点本身就说明控制器没有按时结束，之后读到的错误几乎都是我们的终止造成的。清单要随各 harness 的错误码一起维护，漏一个就会把我们自己的终止当成拒绝而判 `HARNESS_UNAVAILABLE`。更简单的规则是：外层等待到点且停止已确认，一律记未知。

## C. 规矩上的问题

### C1 新增一处没有登记的两侧引用

`src/hey_my_buddy/buddy/roles/router.py:12` 新增 `from ...blackboard.tasks import workspace`（buddy 引 blackboard），`docs/acceptance/adr025/step5-cross-imports.tsv` 里没有这一行（该表也没有测试强制）。它只为在 `router.py:71`、`:145` 设 `git_deadline`。`router_input.py:11` 已经登记引用了 `workspace`，把期限作为参数交给 `router_input.prepare` / `verify`、由 `router_input` 自己进入 `git_deadline`，就不需要这条新引用。若保留，至少要登记；按 ADR-025 第 12 条，两侧都要的东西应放在 `protocol/` 或根包，而一个只有 Router 用的 Git 期限上下文放在 blackboard 的 `workspace` 里本身也值得再想。

### C2 只为测试保留的可选参数与 None 分支

`controller.py:223-232` 的 `if deadline_monotonic is None` 两组分支、`run_execution.start_fast` / `start_review` 的 `deadline_monotonic=None` 默认值、`router.prepare_router_review` 的 `started=None` 默认值：生产里唯一的调用方（`router.py:112`、`:124`）总是传值，None 路径只被旧测试与夹具用到（`tests/python/protocol/fixtures/mock_readonly.py`、`test_registered_review.py`、`test_role_prompts.py`）。这正是“为旧调用方保留的第二条读法”；实现方说明第 240 行写“没有在生产调用方增加兼容分支来迁就旧 fixture”，与代码不符。若 B1 采纳则这些分支随之消失；不采纳也应改为必填参数并更新测试。

### C3 被删掉或放宽的断言

- `tests/python/buddy/harnesses/test_discovery_stop_timing.py:265` 起的 `test_unreaped_real_group_exhausts_stop_budget_without_confirmation` 删掉了 `assertLess(time.monotonic() - started, 1.5)`。这条断言守的正是“组回收不掉时探测在自己的停止预算内放弃”，删掉后剩下的断言只看结果，探测若无尽等待测试会挂住而不是失败。应改为等事实：在线程里调用，用一个宽松上限 `join` 后断言线程已结束且结果为 `shutdown-unverified`。
- `tests/python/blackboard/routing/test_decision.py:572` 把 `usage == {"elapsedMs": 200, ...}` 放宽为 `>= 200`，是 A3 里改了 `elapsedMs` 语义的直接后果；它掩盖了发布端比对的量已经从原生自报换成墙钟这件事，应随 A3 的结论一并定。
- `test_discovery_stop_timing.py` 里夹具的等待上限 15→30、5→20、`process.wait` 5→20：这些是等文件或进程这类事实的保护性上限，放长不改变断言的含义，可以接受；实现方说明第 238 行“没有把这些上限简单放大”与此不符。
- `tests/python/cli/test_checks_parallel.py` 删掉了两处 `assertLess(elapsed, 60, ...)`：剩下的 `process_exists(leftover)` 在留下的进程 120 秒后自行退出的前提下仍能判出“运行器等了它”，属于改成等事实，站得住。
- `tests/python/blackboard/routing/test_routing_preferences.py`、`mock_readonly.py` 把预算相等改为“除 `timeoutSeconds` 外相等且 `0 < t <=` 原值”，是 A3 设计的直接后果，随 A3 定。

### C4 冷启动测试改为离线，与仓库写明的验证前提不一致

`tests/python/blackboard/store/test_blackboard.py:1479` 给私有安装子进程设了 `UV_OFFLINE=1`。AGENTS.md 的验证一节写的是安装测试“在 uv 缓存备齐锁定依赖之前需要网络”；现在缓存不全时这个测试直接失败而不是下载。这条改动的起因是 `runtime.py` 去掉了 900 秒后，测试里的 uv 在网络问题上可能长时间不返回——它本身就是 A2/A4 那一类“没有尽头”的信号。要么恢复测试的联网前提，要么由 Host 改 AGENTS.md 的这句话；不能只改测试。

### C5 删掉的错误码仍被引用

`SERVICE_START_TIMEOUT` 已不再由任何代码抛出，但 `worker.py:335`、`:348` 仍列着它，新代码 `worker.py:671` 又新写了一处；`docs/reference/cli.md:296`、`:298` 仍列着 `SERVICE_START_TIMEOUT` 与 `RUNTIME_INSTALL_TIMEOUT`。按“被取代的代码与文档一并删除”，代码里的三处应删掉，文档由 Host 改。

### C6 存储里的新形式（提示，不一定构成问题）

`harness_health.record_json` 新增了 `lastProbe` 对象，`accountReadFailure` 新增了 `status`；旧行的 `accountReadFailure` 没有 `status`，含义靠“缺省即失败”。现有读取只看新构造的 `failure['status']`，下一次账户读取会整体替换它，实际不会读错；但它是“值的含义随写入时间而变”的一个小例子，是否要随待做的升级（schema 20）一并转换，请 Host 判断。另见 A1 里每轮写入的 `harness.execution_selected` 事件记下的是未被选用的记录。

没有发现把时限做成用户设置的地方；没有新增配置项或环境变量。

## 对照实现方的说明

说明与代码不符或有遗漏的地方：第 7 行与第 23 行说“收尾宽限保留”“Router 的预算值、收尾宽限……没有增加”，并把基点描述为“材料化之后才起算原生调用预算”；实际基点的 Worker 期限早已从准备之前起算并含 10 秒宽限，现在原生预算从准备之前起算且不含这 10 秒，宽限对准备时间的吸收被拿掉了（A3）。第 240 行与第 232 行说没有兼容分支与兼容路径，代码里有只为测试保留的 None 分支（C2）。第 238 行说夹具上限没有简单放大，`test_discovery_stop_timing.py` 里有三处放大（C3，可接受但说法不实）。第 37 行说外层等待到点且停止已确认时返回未知目录，没有提到还要经过一张手写的错误码清单（B5）。第 5 行说 Claude Code CLI“不缓存未知元数据为不可用、下一次读取会重试”，与代码一致，但没有说出未知因此被 `available()` 当成可用、且每次能力读取都重跑原生读取（A5）。第 11 行说工作区 Git、依赖安装、IPC 自检与服务启动“依据进程退出、明确错误或就绪事实结束”，没有说明这些事实可能永远不出现、届时谁也停不下来（A2、A4）。第 282 行已说明 `router.py` 的跨侧引用留给 Host 登记，与代码一致（C1）。
