# 有序收尾与终止原因

本步基于 `62d4ef6b` 实现 ADR-022 第 4、7 条和第 3 步的 11；续接输入允许原生模块补齐中断结算、会话绑定与续接校验，收尾业务集中在共用角色层。schema 保持 20，产品版本不变，未安装产品；本文记录源码候选及模拟验证，最终验收由 Host 完成。

Host 经黑板 `workflow_cancel` 停止在跑的微任务时，`blackboard/tasks/workflow.py` 在同一事务中写入停止意图、发起者和 300 秒宽限截止；协助任务仍走 `_cancel_children`，Router 仍走 `_cancel_routes`，没有新增级联路径。Worker 通过已有的 `task_get` 轮询和续租响应取得意图，调用 `buddy/roles/controller.py` 的 `worker_stop`；收尾期间继续续租并观察控制台升级急停，重复 Host 停止保留原截止、不延长宽限。执行状态未知的 attempt 不取得收尾宽限，按强制停止处理。

宽限截止存为 `attempts.grace_deadline` 的 UTC 绝对时刻；`attempts.stop_initiator` 单独记录 `host`、`console`、`unknown`，未发起停止时为空。attempt、task 视图及续租、恢复响应使用 `graceDeadline`、`stopInitiator`。Worker 将截止换算为单调时钟；角色层写绑定当前调用身份的私有停止命令，同一个命令文件承载 Host 收尾与后续强制停止。控制器在总结前和总结期间检查最新命令，控制台或服务立即停止清除活动宽限。

共用角色控制器取消首个原生回合；仅在调用身份与 harness 一致、会话身份明确、原生进程组确认消失、运行器明确声明该终止回合可续接时，在原会话上发起短总结。Codex 还要求与原回合、attempt 和输入哈希绑定的 checkpoint。总结使用原结构化结果契约和权限，独立调用身份及证据目录，说明已做工作、剩余工作、决定和成果，不继续实现；最终输出仍绑定黑板原 turn 的身份与输入。合法总结以 `cancelled` 状态携带真实 `turn`，通过已有部分成果封存路径保留，不能把模型的 `completed` 结果提升为微任务完成或 Host 接受。

总结调用的整数预算覆盖剩余宽限，精确截止由独立取消回调和持有句柄的 Worker 执行；截止之后不接受收尾交付。强制停止先通知仍持有原生句柄的角色控制器立即取消原生工作，再给控制器最多 5 秒回收进程、提交停止回执，随后强制结束外层控制器；这 5 秒是机械清理时间，不增加模型回合宽限。命令执行器直接拥有工作进程组，保持直接停止。这避免只杀外层控制器而丢掉独立原生进程组的所有权与停止证据。

控制台 `stop_macro_task` 和控制台权限下的微任务停止立即发送强制命令，不发起总结，急停范围保持原状；Host 收尾可以被控制台覆盖。最新私有命令与信号世代共同阻止“急停已到达，但总结准备过程把它当作新的信号基线”的竞态。Claude Code 未声明原生续接，Host 停止也直接强制停止；离线集成确认只有中断和回收，没有第二模型回合。

终止原因统一为 `completed`（真实完成）、`graceful-stop`（收尾正常交付）、`grace-expired`（宽限用完后强制停止）、`forced-stop`（Host 停止但无法续接或未交付合法总结，强制结束）、`console-stop`（控制台停止）、`unknown-stop`（停止发起者未知）、`deadline`（Router 预算用完）、`harness-error`、`transport-error`。停止原因在角色层共用，Worker 写入耐久回执，黑板在提交边界验证原因、发起者与停机事实。五种停止原因都要求确认停机；停止未确认时原因留空、发起者保留，任务保持 `reconciliation-needed`、资源不释放，不封存仍可能被存活进程改写的工作区。Router 的预算用完仍为 `deadline`，微任务预期时长没有变回硬时限。

控制台急停与旧 Host 回执提交相遇时，提交边界核对旧发起者确有耐久停止请求，保留最新发起者和真实停机、成果证据；无法证明是哪一个竞争请求结束运行时，终止原因留空，不捏造收尾完成或控制台执行的事实，也不丢弃可重放回执。没有耐久 Host 或控制台请求的冒名回执仍被拒绝；持有句柄的 Worker 自行停止而发起者无法识别时，允许明确记录 `unknown`。对应竞态、相同回执重放及本地未知发起者均有黑板测试。

总结启动后的失败、超期、非法结构、会话不匹配或无回执，都使用第二次调用自己的停止证据；调用抛异常而没有可信回执时，第二次调用明确记为未知。第一回合的已停止证据不能代替第二回合，不能据此释放资源或封存成果。只有当前原生与外层进程组都确认已停止时，`runtime/partial_outputs.py` 才封存改动；收尾成功、宽限强制、不支持续接三条路径都保留部分成果标记，原因使用对应停止原因，未改动的工作区不伪造成部分补丁。

首原生回合已经自然完成、取消在收集前迟到的情形保留真实 `completed`：必须是原调用身份、合法 turn 与原生 provenance、退出 0、两层停机确认、没有错误或注意事项且宽限未过期。第二次总结、强杀或无合法结果不会被此规则提升为完成；已取消的工作流仍按迟到结果封存，不重新打开，不等同于 Host 接受。另一方面，取消意图已到而停机未知时，Worker 保留原有的未确认停止 `failed` 回执及 `shutdownConfirmed=false`，黑板根据耐久停止意图明确记为 `reconciliation-needed`；外部 Worker 的未确认 `cancelled` 回执也按这一状态保留。

Codex：原生模块在 `turn/interrupt` 后继续接收与确切 thread、turn 关联的 `turn/completed`；明确状态为 `interrupted` 且进程组确认消失后才保存绑定。中断绑定续接必须有原 attempt、输入哈希、原生 turn 的 checkpoint，并通过 `thread/read` 核对末回合身份和终止状态。新回合开始前失效旧绑定，防止旧成功回合证明后来未结算的回合；正常与中断绑定都显式写 `lastTurnStatus`，读取只接受一种完整形式。离线 App Server 集成经过真实控制器、原生协议和结果导入，确认同一会话先有 `interrupted` 回合，再交付合法总结，微任务状态仍为 `cancelled`。

DSH：ACP prompt 必须明确返回 `stopReason=cancelled`，`session/close` 必须获确认，原生进程组必须确认消失，才保存中断绑定；普通与中断绑定都显式记录 `lastTurnStatus=completed/cancelled`。新 prompt 入场前失效旧绑定，续接严格核对微任务、会话、工作区、buddy 与终止状态。ACP 公开结果没有原生 turn ID，不伪造 Codex 式 checkpoint；使用实际关联 prompt 返回、会话关闭与进程组事实，模拟覆盖确认取消后续接及关闭未确认时拒绝续接。

ZCode：当前原生协议没有可归属到中断回合的终止事件或原生中断确认，进程组信号不能把会话变为可续接状态；中断后不声明 `resumable`，共用角色层按未取得安全续接证据的路径强制停止并封存。原绑定早于回合完成写入的问题已修在原生模块：只在合法回合完成且进程组确认已停后写绑定，新回合入场前失效旧绑定。ZCode 的 `inputId` 包含黑板 turn 身份，同一 turn 再发输入会复用该 ID；本步不在缺少终止证据时启动这种总结，也没有增加另一个 turn 身份或 Lead 机制。

各原生模块写绑定失败时明确报告 `native-binding-save-failed`，不吞错误、不声称可续接，并保留已取得的真实停机事实。绑定仅在回合结算与停机确认后独占新建，不重读或覆盖已失效的旧绑定。原生模块没有实现总结提示、宽限、封存、停止发起者或停止原因业务，这些机制由共用角色层及既有黑板、Worker 通道承担。

存储变化并入唯一的 schema 19→20 升级：重建 `attempts` 增加两列，服务回执、缓存投影和事件明确补停止发起者；旧 `user-cancel` 无法可靠区分 Host 与控制台，发起者统一迁为 `unknown`，仅当旧回执明确 `shutdownConfirmed=true` 时迁为 `unknown-stop`，未确认或缺失证据时原因留空，不造停机事实或宽限。旧微任务硬 deadline 按上一步规则清空，Router 的 deadline 保留；原生 result、report、outcome、provenance 等证据不改写成黑板分类。

Codex、DSH 旧私有绑定也在 schema 19→20 中补 `lastTurnStatus=completed`，路径为 `harnesses/<harness>/micro-tasks/<task-hash>/native/<session-hash>.json`；基点的这两个运行器只在成功回合与确认停机后保存绑定，因此可以明确换算为已完成。升级核对基点完整字段、任务目录及会话文件哈希，通过受保护的普通文件读边界取得内容；转换失败时回滚已替换绑定与数据库，不留下两种读法。ZCode 绑定形式未新增字段，没有把缺少中断结算证据的旧绑定升级成可续接。

控制台只更新类型、停止与原因文字，布局、颜色未改；原 `user-cancel` 展示分支已移除，打包资产随构建替换。使用 Node 24.21.0 先运行 `npm --prefix apps/console ci --cache ./tmp/npm-cache --no-audit --no-fund`；`npm --prefix apps/console test` 的 78 文件、1091 项通过；`PLAYWRIGHT_BROWSERS_PATH=<检出>/tmp/playwright-browsers npm --prefix apps/console run test:browser` 的 96 项通过，其中包含最终前端构建。日志为 `tmp/stop-npm-ci.log`、`tmp/stop-console-test.log`、`tmp/stop-console-browser.log`，缓存与浏览器下载都在检出内。

Python 始终使用 `PYTHONPATH=src <指定解释器> -m hey_my_buddy.cli.checks <文件或目录>`、默认进程数及检查器私有根，没有手动启动测试进程、修改指定解释器环境或运行完整检查。第一组覆盖 `tests/python/buddy/`、`tests/python/blackboard/store/`、`tests/python/blackboard/routing/`、整个 `tests/python/blackboard/tasks/`、`tests/python/cli/test_host_preview.py`、`tests/python/console/test_console.py`，165 文件、2975 项通过，日志 `tmp/graceful-stop-affected-python-1.log`；第一组不替代后续源码的验证。

中间失败与重跑：升级夹具首轮 1 项失败、2 项报错，分别为缓存投影断言与 Worker 尚按旧 5 项解包枚举；修复后单文件 10 项通过，日志 `tmp/stop-upgrade-test.log`、`tmp/stop-upgrade-test-retry.log`。新增黑板夹具前两轮分别 4 项、2 项用法错误，第三轮 5 项通过，日志 `tmp/graceful-stop-check-{1,2,3}.log`。新增 Worker 测试误插入原测试类，首轮 3 项报错，恢复类与原断言后单文件 15 项通过，日志 `tmp/graceful-stop-runtime-final.log`、`tmp/graceful-stop-worker-invariants-rerun.log`。

中间失败与重跑续：角色原因统一后一个旧控制台预期仍为 `forced-stop`，更新为 `console-stop` 后单文件 13 项通过，日志 `tmp/graceful-stop-final-followup.log`、`tmp/graceful-stop-role-rerun.log`。后续 120 文件补跑有 119 文件、2230 项通过，角色夹具在源码修改期间读到不一致版本，报缺少 `force_stop_requested`；最终该文件独立重跑 16 项通过，日志 `tmp/graceful-stop-final-python.log`、`tmp/graceful-stop-final-role-rerun.log`。DSH 显式状态使一个旧结构断言失败，更新后 `test_native_run.py` 独立重跑 62 项通过；独占绑定写入使另一个三档循环测试复用模拟 session ID 冲突，隔离模拟绑定后 `test_native_resume.py` 独立重跑 19 项通过，原始结果在本轮工具记录中。没有时序失败被略去，也没有声称首次全过。

聚焦证据：Codex 收尾集成所在文件 37 项通过，日志 `tmp/codex-graceful-stop-check.log`；Claude 两种立即停止的离线集成所在文件 38 项通过，角色层 17 文件、177 项通过，原始结果在工具记录和 `tmp/role-stop-roles-suite.log`；升级绑定转换及失败回滚夹具 13 项通过，日志 `tmp/graceful-stop-native-binding-upgrade.log`。回执竞态、安全结果提交的最终补跑覆盖整个 tasks 目录、`test_store.py`、`test_repair_recovery.py`、`test_console.py`，46 文件、761 项通过，日志 `tmp/graceful-stop-final-receipts.log`；Router 预算到点换下一候选在 routing 目录覆盖。

后续验证及失败记录：运行时、角色层和 Codex、Claude 集成补跑 29 文件、350 项通过，日志 `tmp/graceful-stop-final-runtime-role.log`。存储目录与新增停止文件补跑时，其余 4 文件、93 项通过，新增文件一项身份校验断言失败，暴露旧 precheck 仅检查 `status=ok`、漏掉 `graceful-stop` 的问题；修复边界后失败文件独立重跑 8 项通过，`test_workflow.py` 57 项通过，日志 `tmp/graceful-stop-final-store.log`、`tmp/graceful-stop-final-summary-validation-rerun.log`、`tmp/graceful-stop-final-workflow-validation-rerun.log`。期间一个补跑命令误用了不存在的测试路径，退出 2、未执行测试，随后改为实际存在的 `test_workflow.py`；原使用错误日志为 `tmp/graceful-stop-final-workflow-validation.log`。

后续验证及失败记录续：自然完成与取消迟到边界新增后角色层 178 项通过；三文件收尾边界补跑 62 项通过，日志 `tmp/graceful-stop-final-boundaries.log`。补充未确认取消状态断言时引入一次缩进错误，20 文件检查中其余 19 文件、245 项通过；修正测试缩进后失败文件独立重跑 17 项通过，日志 `tmp/graceful-stop-final-stop-state.log`、`tmp/graceful-stop-final-stop-state-rerun.log`。黑板新增文件已有 9 个场景；收尾身份或成果校验失败时清除不可信的 `graceful-stop` 原因，保留真实停机与失败事实。

最终相关目录补跑：165 文件中 160 文件、2922 项通过，5 文件失败，日志 `tmp/graceful-stop-final-affected.log`。ZCode 文件暴露未确认停止应保留既有 `failed` 回执的约定，修复角色收集并在黑板按停止意图保存 `reconciliation-needed` 后，该文件独立重跑 26 项通过，日志 `tmp/graceful-stop-final-zcode-rerun.log`；存储目录、停止边界及恢复相关 7 文件、128 项也通过，日志 `tmp/graceful-stop-final-unknown-state.log`。其余四文件均为 DSH 模拟器每个进程从同一 session ID 开始、与独占绑定写入冲突；根因修在模拟器，使新会话按私有 sessions root 中的已有记录选择未用 ID，并以独占 marker 取得 ID，恢复仍使用原 ID，同时撤掉清理绑定的临时测试处理。四个失败文件各独立重跑一次，分别 12、6、13、14 项全部通过；DSH 全目录及三个工作流文件再跑 15 文件、255 项通过，日志 `tmp/graceful-stop-fixture-{dsh-role-wiring,dsh-worker-permissions,dsh-adapter-usage,dsh-private-adapter-invariants,related}.log`。所有最终失败文件均已修复并重跑通过，检查器私有根均清理成功，未运行完整检查。

与要求没有新增范围偏离；ZCode 在中断会话的可续接状态未知时强制停止，是要求规定的分支，不能宣称真实 ZCode 的中断后收尾已验证。全部真实 harness 收尾，以及 Windows、Linux 原生运行均未验证；未运行 `tests/probes/` 或付费模型调用，也未操作日常服务、Worker、用户 harness 配置或凭据。真实验证仍需 Host 取得开发者授权，源码候选不等同于安装或验收。
