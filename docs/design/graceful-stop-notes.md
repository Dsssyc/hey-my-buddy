# 有序收尾与终止原因

本步基于 `62d4ef6b` 实现有序收尾（wrap-up），按 Host 第三回合的修正要求替换前一候选中的绑定转换、重复判定和停止通知；源码候选仍待 Host 验收，schema 保持 20，产品版本不变，未安装产品。前两回合的检查记录保留在 `tmp/graceful-stop-*.log`，它们对应已被替换的实现，不作为本次最终验证依据。

Host 经黑板 `cancel` 停止运行中的微任务，`blackboard/tasks/workflow.py` 在事务中记录停止意图；`_authorize` 一并返回 actor 与明确的发起者，沿调用链传递，不从 actor 字符串还原。停止协助任务仍走 `_cancel_children`，Router 仍走 `_cancel_routes`，没有第二条级联路径，也没有改变宏任务急停范围。

宽限截止存于 `attempts.grace_deadline`，为 UTC 绝对时刻；`attempts.stop_initiator` 记录最近一次耐久停止意图的发起者，取值为 `host`、`console`、`service`、`unknown`，没有停止意图时为空。Worker 本地停止而黑板没有停止意图时，由 `unknown-stop` 回执补记 `unknown`。Worker 回执只带实际终止原因，不再重复携带发起者；有竞争请求时，列保留耐久意图，回执保留实际结算事实。Host 的 `get`、task 和 attempt 视图、续租与恢复响应提供发起者和截止，`get` 与 task 视图还直接提供终止原因。

宽限使用命名常量 `WRAP_UP_SECONDS=300`；黑板仅给明确在启动或执行、且 Adapter 声明 `interrupted_resume` 能力的 attempt 宽限。Codex、DSH 声明能，Claude Code、ZCode 声明不能；外部调用方持有的 `external` 没有内置执行器，也不取得宽限。重复 Host 停止保留原截止，控制台或服务急停也保留曾给过的截止；立即停止由发起者决定，历史截止不被清空。停止状态未知时不续接。

Worker 通过既有的 `task_get` 轮询及续租响应取得停止意图，在角色层 `controller.worker_stop` 交给所选执行器自己的 `stop` 方法。句柄的停止状态在 `ProcessHandle.__init__` 声明，停止请求由持有句柄的锁串行化；只有第一次停止请求可以开始收尾。Worker 已经强制停止时，后来到达的 Host 请求不能再开启收尾，也不会独占写同一文件而使续租线程退出；停止期间续租和控制台意图观察继续到 attempt 结束。

收尾业务只在共用角色层实现。角色控制器中断当前原生回合；只有原生停止已确认、会话身份明确、运行器说明该中断回合可续接时，才在原会话续一次短总结。提示词存于 `buddy/roles/texts/wrap_up.txt`，由 `prompts.py` 组装；它要求总结已经做的、没有做的及成果，使用原结构化结果契约和权限，不继续实现、不声称 Host 已接受。第二次调用使用新的 invocation 身份；描述文件仅记它的身份，其余路径由常量推导。有 checkpoint 就传给 harness，由 harness 校验，角色层不写死 Codex。

精确截止由控制器的单调时钟及原生取消回调执行，整数调用预算向上取整覆盖剩余宽限，回调仍按绝对截止取消。控制器在第二次原生调用返回时判定是否及时，并在一次交付校验后记录 `wrapUpDelivered` 和 `graceExpired`；Worker 收集时不按当前时刻重算，不把按时交付但稍后收集的总结记成到期。超期或不合格的第二回合不写入或导入结构化 turn；第二调用的停止证据只来自它自己，异常或未知停止不能借用第一回合的已停证据。

强制停止只使用执行器的信号通知，不再写 force-stop 命令。Host 收尾文件只在首次收尾请求时写一次，之后的信号使控制器停止收尾；控制台的停止立即发送该通知，不开始总结。回收时间恢复执行器原有的命名常量：Worker 角色 8 秒、Router 12 秒、命令执行 3 秒；这些时间供原生中断及进程回收，不延长模型总结宽限。Claude Code 与 ZCode 的 Host 停止也直接停止并封存。

终止原因统一为 `completed`、`wrap-up`、`grace-expired`、`forced-stop`、`console-stop`、`service-stop`、`unknown-stop`、`deadline`、`harness-error`、`transport-error`。它们分别表示正常完成、Host 收尾合格交付、宽限到期后停止、Host 停止但没有合格总结、在控制台停止、服务停止、停止发起者未知、Router 预算用完、harness 错误和传输错误。原因在 Worker 提交前统一判一次，部分成果 `reason` 使用同一个原因。所有停止类原因要求停机已确认；停机未知时停止原因留空，资源保留，有停止意图的任务记为 `reconciliation-needed`。Router 的预算仍到点换下一候选并记录 `deadline`。

黑板在回执边界核对停止类原因的停机事实、`console-stop` 的耐久控制台意图，以及 `wrap-up`、`grace-expired` 曾取得的宽限。控制台急停晚于已交付的总结时，合法收尾回执原样保存并可重放，不因最新意图不同而清除或改写原因。Host 停止后总结不合格、停机已确认，记 `forced-stop` 与 `cancelled`；边界拒绝不合格的收尾声明时同步修正部分成果原因，不把它保存成 `failed` 或收尾成功。首回合已经正常完成、取消在收集前迟到时保留真实 `completed`，不重新打开已经取消的工作流。

Codex 原生绑定保持基点字段，不保存 `lastTurnStatus`。确认中断终止事件与确切 thread、turn 关联且原生进程组已停后，按原形式原子替换绑定；续接通过 `thread/read` 核对末回合 id 与绑定相同，且原生状态是 `completed` 或 `interrupted`，中断续接的 checkpoint 由 Codex 自己校验。DSH 绑定仍只说明会话属于哪个微任务、检出与 buddy；prompt 确认取消、会话关闭获确认且进程组已停时也保存，已有绑定核对相同，没有则独占新建。ZCode 缺少中断回合的结算证据，所以不声明中断后收尾能力；合法完成后已有绑定核对相同，没有则新建。三个 harness 在新回合开始前均不删除旧绑定，绑定写入失败只报告 `resumable=False`，不把已经交付的回合变成错误。

普通续接只使用已正常完成的原生回合。Codex 的 checkpoint 能证明正常原生完成，因此控制器的结果导入失败不抹去这份原生完成证据；没有这种 checkpoint 的 DSH 等 harness 则要求上一 attempt 正常完成且停机确认。取消或失败的续接不能仅凭一份旧会话所有权绑定再次普通续接；收尾入口由角色层另外取得明确中断结算证据后使用。

schema 19→20 的待做升级只改 SQLite，不再读取、转换或回滚原生绑定文件；升级前后 Codex 与 DSH 绑定逐字节相同，因此数据库提交前被杀不会留下混合绑定形式，SQLite 事务可重跑。新增列与原因转换并入同一升级，没有推进 schema 版本。旧 `user-cancel` 无法可靠区分实际结束运行的请求，停机确认时换成 `unknown-stop`，否则原因为空；原有取消请求或旧停止原因的 attempt 发起者记 `unknown`，不补造历史宽限。非 Router 的旧 `deadline` 清空，Router 的保留；转换服务回执、事件与缓存投影，原生结果、outcome、输入和 provenance 保持原样。没有 `resultMeta` 存量处理分支，也没有额外绑定形式或兼容读法。

控制台只改取值与文字，保留样式；收尾原因使用 wrap-up，控制台停止统一显示“在控制台停止”，未知发起者统一显示“停止（发起者未知）”，服务停止单独显示“服务停止”，宏任务时间线显示“Host 停止了”。前端依赖使用受支持的 Node 24，经 `npm --prefix apps/console ci` 准备；npm 缓存和 Playwright 下载目录都在检出内的 `tmp/`，浏览器检查会重建打包资产。

Python 检查始终使用 `PYTHONPATH=src <指定解释器> -m hey_my_buddy.cli.checks <文件或目录>`，默认进程数及检查器私有根，没有向解释器环境安装内容，没有手动启动测试进程或运行完整检查。本轮黑板广测覆盖 store、tasks、routing 共 80 文件，79 文件、1279 项通过，唯一失败文件 `test_blackboard.py` 的旧模拟句柄缺少新声明的停止字段，导致 7 处故障；改成标准 `ProcessHandle` 夹具后该文件独立重跑 58 项通过，日志 `tmp/wrap-up-blackboard-fixture-rerun.log`。最初的升级及停止夹具用法、投影断言和重复 request ID 失败已修，两个文件 25 项与停止文件后续 14 项、16 项分别通过，最终日志 `tmp/board-stop-capability-rerun.log`。

本轮 buddy 及服务停止、Worker 池、控制台读取、Host preview/CLI、公开状态和 console 广测共 97 文件，94 文件、1739 项通过，3 文件失败，日志 `tmp/wrap-up-buddy-affected-final.log`。Codex 普通续接门禁曾过严，恢复按原生完成 checkpoint 判断后失败文件独立重跑 7 项通过，与 DSH 相关文件合跑 15 项通过，日志 `tmp/board-codex-continuation-single.log`、`tmp/board-native-continuation-rerun.log`；Codex 原生测试的旧控制器夹具缺 inputFile/outputFile，补齐真实输入路径、哈希与输出路径后独立重跑 108 项通过，日志 `tmp/wrap-up-codex-native-final-rerun.log`；Worker live wiring 的旧包装句柄未初始化停止状态，改用 ProcessHandle 基类后独立重跑 5 项通过，日志 `tmp/wrap-up-live-wiring-final-rerun.log`。本轮两组广测的最终失败文件均已修复并单独重跑通过，检查器私有根均清理成功。

聚焦验证覆盖：原生绑定 203 项、能力声明 1 项、Codex 收尾离线集成 37 项、Claude 立即停止离线集成 38 项、角色目录 181 项通过；角色单文件最终 19 项通过。Worker 定向三文件首轮因旧回执发起者断言和旧句柄夹具失败，修后失败文件分别单独重跑 17 项与 9 项通过，日志 `tmp/wrap-up-runtime-repair.log`、`tmp/wrap-up-worker-invariants-rerun.log`、`tmp/wrap-up-recovery-rerun.log`；之后新增各执行器回收时间断言也在 buddy 广测通过。最终停止、收尾、DSH 续接、工作流和仓库路径卫生五文件 105 项通过，日志 `tmp/wrap-up-final-contracts.log`；收尾成功、超期、不支持续接、未知停机、部分成果、服务与控制台停止、及时交付迟收、回执重放、绑定逐字节不变和 Router 预算都有模拟或夹具覆盖。

前端首轮 78 文件中 76 文件、1085 项通过，两个文件的 6 项超时或超时后的元素查找失败；两个失败文件分别独立重跑 18 项与 13 项通过，日志 `tmp/wrap-up-console-test.log`、`tmp/wrap-up-console-session-rerun.log`、`tmp/wrap-up-desktop-console-rerun.log`。改过的文案两文件最终补跑 25 项通过，日志 `tmp/wrap-up-console-changed-final.log`；浏览器首轮 96 项通过，日志 `tmp/wrap-up-console-browser.log`。统一未知发起者文案后的前端与浏览器最终检查结果将在下段记录。

最终前端源码的 `npm --prefix apps/console test` 全部 78 文件、1091 项通过，日志 `tmp/wrap-up-console-final.log`；随后 `npm --prefix apps/console run test:browser` 重建打包资产并通过 96 项，日志 `tmp/wrap-up-console-browser-final.log`。`git diff --check` 通过，修改均在授权范围内，版本号和 schema 版本未变。

与要求没有新增范围偏离；没有检查真实 harness 的中断、续接或收尾，也没有验证 Windows、Linux 原生进程行为。未运行 `tests/probes/`、付费模型调用、日常服务或 Worker 操作，未读取或修改用户 harness 配置与凭据，未安装产品。真实 harness 的收尾验证仍由 Host 另取得开发者授权，本次通过的模拟检查不等同于真实能力验收。
