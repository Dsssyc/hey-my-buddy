# 有序收尾与终止原因：本轮阻塞记录

本轮基于 `62d4ef6b` 检查并试做第 3 步的 11，未完成实现。发现现有 Codex 原生续接机制不支持从正在执行而被中断的回合续接；仅修改共用角色层不能满足必需的收尾成功场景。未完成的源码、测试与打包资产改动已逐文件撤回，本轮交付仅为本说明与检出内的临时检查日志，等待 Host 判断原生中断续接机制是否纳入本步。

预期流程仍按 ADR-022：Host 经黑板 `cancel` 写入停止意图和 5 分钟宽限截止，Worker 中断原生回合，确认停止证据后由共用角色层在原会话续一个短总结回合，使用原结构化结果契约；到宽限时仍未交付则强制停止；不能续接的 harness 立即强制停止；所有确认停止的取消路径封存部分成果。控制台停止保持立即生效；微任务名下协助任务仍走 `workflow.py` 的 `_cancel_children`，不增加级联入口。此流程本轮没有落地。

Codex 的具体阻塞在 `src/hey_my_buddy/buddy/harnesses/codex/native_run.py`：`_interrupt`（第 1358–1373 行）发送 `turn/interrupt`，并把原生通知接收器换成丢弃通知的函数，只记录请求与确认情况；`_save_binding`（第 1407–1419 行）仅在 checkpoint 表示原生回合已开始、状态为 `completed`、进程组已确认停止且原生退出码为 0 时保存续接 binding；`_configure_worker`（第 636–667 行）要求已有绑定，并通过 `thread/read` 核对绑定的末次回合状态仍为 `completed`；`_continuation_facts`（第 1547–1550 行）的 `resumable` 只来自 `binding_saved`。因此，中断首个在跑回合后，既没有可用绑定，也不能通过末次回合状态校验；已续接过的会话若保留旧绑定，也会因为末次回合与绑定不一致而被拒绝。真实会话 ID 和进程组已停止本身不能替代这些事实。

需要 Host 明确的范围是：允许补齐原生执行机制对已确认中断回合的安全续接证据、保存与校验，同时把“停止后总结”的业务流程继续只放在角色层；或者继续限制为角色层加能力声明，并接受 Codex 当前无法实现所要求的收尾成功场景。前一方案至少要处理 Codex 的中断结算、绑定保存与续接校验，并检查其它声明可续接的 harness 对中断回合是否提供同样的证据；不能仅靠把 `interrupted` 放进允许列表、只看 session ID，或把原生中断请求的确认当成回合已停止。原生停止状态未知时必须保留未知，不能发起续接；总结回合的正常交付也必须与强制停止分开记录。此处是对冻结范围的适用性判断，不是某个 skill 要求另行审批。

拟采用的存储形式是 `attempts.stop_initiator` 独立记录 `host`、`console`、`unknown`，未发起停止时为空；`attempts.grace_deadline` 记录 Host 收尾宽限的绝对截止时刻，控制台立即停止时为空；终止原因继续作为明确字段保存在结构化回执中，取值拟为 `completed`、`graceful-stop`、`grace-expired`、`forced-stop`、`console-stop`、`unknown-stop`、`deadline`、`harness-error`、`transport-error`。前三种停止结果分别说明收尾正常交付、宽限到时被强制停止，以及因无法续接或无法取得续接证据而立即强制停止；`deadline` 仍只用于 Router 的预算耗尽，停止发起者与原因分开记录。这些只是待 Host 确认并继续实现的方案，当前源码未改变。

存储改动应并入现有 schema 19→20 升级，不改变 schema 或产品版本。旧 `user-cancel` 无法可靠判断是否来自 Host 或控制台，拟迁为 `unknown-stop`；已有旧停止意图的 attempt 拟标记 `stop_initiator=unknown`，不凭旧时间补算宽限截止；原生 harness 的原始诊断与 provenance 不应被改写成新的黑板分类。本轮曾试做升级与投影，已撤回，当前两列、原因枚举和 19→20 升级均保持基点内容。

所有 Python 检查使用指定解释器经 `PYTHONPATH=src <指定解释器> -m hey_my_buddy.cli.checks <测试文件>` 执行，使用默认进程数与检查器私有根；没有手动启动测试进程、没有运行完整检查或 `tests/probes/`。本轮没有验证真实 harness 上的收尾，没有调用付费模型，没有安装产品或操作日常服务及 Worker。

试做阶段的 `tests/python/blackboard/store/test_upgrade_fixture.py` 首轮共有 10 项，其中 8 项通过，`test_installed_upgrade_restores_schema19_backup_when_conversion_fails` 与 `test_schema_upgrade_preserves_stock_and_matches_fresh_structure` 两项报错：新增的 9 项终止原因尚未与 Worker 按 5 项解包的旧代码接上，导入产生 `ValueError`。这是实现尚未接完的错误，非时序失败。撤回源码和新增测试后单独重跑同一文件，基点的 9 项全部通过、退出 0，日志在 `tmp/graceful-stop-fixture-rerun.log`；复跑通过只证明撤回后基点检查正常。

撤回后另用指定检查器运行 `tests/python/buddy/harnesses/codex/test_native_run.py`，现有离线 App Server 模拟夹具的 103 项全部通过、退出 0，检查器私有根已移除，日志在 `tmp/graceful-stop-codex-native-check.log`。该文件已有正常会话续接、缺少 binding 时拒绝续接、绑定与末次回合不一致时拒绝续接、停止未知不得保留可续接 binding，以及取消后的原生中断事实等覆盖；它通过说明当前检查支持上述源码约束，不能证明“中断后的原会话收尾”已经存在。

试做阶段另新增过 `tests/python/blackboard/tasks/test_graceful_stop.py`，首轮 7 项中 1 项通过、5 项失败、1 项报错，退出 1：旧 `user-cancel` 被拒绝的断言通过，5 项停止归因断言因黑板写入路径尚未接上而失败，1 项因 `worker_result` 尚未接受顶层 `stopInitiator` 而报 `Unknown worker.result parameter: stopInitiator`。该文件作为未落地实现对应的测试已撤回，原始结果在本轮工具记录中；检查器已清理私有根，未另存磁盘日志。这些失败不代表已经交付过收尾功能，也不构成验收。

试做前端期间使用受支持的 Node 24.21.0，运行 `npm --prefix apps/console ci --cache ./tmp/npm-cache --no-audit --no-fund`；`npm --prefix apps/console test` 的 78 个文件、1091 项通过；`PLAYWRIGHT_BROWSERS_PATH=<检出>/tmp/playwright-browsers npm --prefix apps/console run test:browser` 的 96 项通过，其中包含构建。npm 缓存与浏览器下载均在检出内。前端原始结果在本轮工具记录中；这些结果属于已撤回的改动，打包资产也已恢复，不能据此声称当前已实现新取值与停止文案。

与要求不一致的情形是本步没有完成实现：现有 Codex 原生中断与续接证据契约和要求的角色层收尾流程之间存在上述缺口，需要 Host 决定如何处理原生机制的修改范围。未执行要求的全部受影响测试，因为本轮已撤回实现；没有以模拟中的可续接结果替代真实源码不具备的事实，也没有把强制停止或停止未知报告成收尾完成。
