# Lead 名下的微任务：归属与凭据

本次以 `55be5ebe` 为输入，实现 Lead 名下的微任务归属与执行凭据权限；交付的是源码和离线检查证据，尚未安装，也不包含下一步的 Lead 名下生命周期或泳道。

归属单独记在 `workflow_lead_tasks`：`owned_run_id` 是名下微任务的主键及 `workflow_runs` 外键，`lead_run_id` 是持有委派权的微任务外键，`lead_turn_id` 是创建时 Lead 执行回合的 `workflow_turns` 外键，`created_at` 是建立时刻；另有按 `lead_run_id` 的索引和不能归属于自身的约束。归属与微任务的提交、控制令牌和事件在同一个事务里建立，失败时一起回滚；不会写入协助任务的 `workflow_children`，也不把协助任务的批准命令或状态用于另一种意义。

存储改动并入已有 schema 19 → 20 升级：按当前声明创建空的归属表及其索引，schema 仍为 20；原有的 `attempts` 重建同时纳入 `stop_initiator='lead'`。基点样板看板升级后的 SQLite 表和索引声明与新建看板一致，归属表的存量为空，原有原生会话绑定和冻结输入证据不被改写。

普通执行凭据继续使用 `AGENT_OPERATIONS` 和自己的微任务绑定。仅持有委派权的微任务的执行凭据增加 `workflow_submit`、`task_list`、`task_wait`、`events_wait`、`message_wait`、`workflow_decide`、`workflow_continue`、`workflow_cancel`、`workflow_accept`、`workflow_conclude`、`workflow_reclaim`、`workflow_scope_amend` 和 `workflow_workspace_resolve`；原有读取和询问操作可用于直属名下微任务，Worker 的 `workflow_suggest` 和 `message_update` 仍只绑定自己的微任务。接管、宏任务的停止、共享设置、评价、安装与升级不加入白名单。

目标绑定仍由原有凭据入口完成，逐一检查调用中提供的 `runId`、`taskId`、`targetRunId`、`requestId`、`attemptId` 和 `messageId`，避免一个合法选择子掩盖另一个越界选择子。决定边界时的 `requestId` 解析到 `workflow_requests`，代理协助任务的请求还同时绑定其 `child_task_id`，因此不能借名下微任务的代理请求操作协助任务；列表的计数、分页和过滤在 SQL 中限定到该 Lead 的直属归属，不能读取 Host 自己、其他 Lead 或协助任务的行。已有的自己观察权限不扩大为全表读取；新增的单个目标操作也不能用于 Lead 自己。

Lead 提交仍经过 `normalize_workflow_submit`，包括完整 buddy、常规路由、执行工作区、时长、标题和锁定 buddy 的既有校验。调用方不能指定 `hostId`、`owner` 或任何宏任务选择子：黑板从执行凭据得出 `hostId='lead:<runId>'`，沿现有提交路径把它记为委派方，并从 Lead 的微任务读取 `macro_task_id`；没有宏任务时双方均为空。`delegationAuthority=true` 被拒绝，名下微任务及其执行凭据不会继承委派权。

名下微任务照常返回控制三元组 `hostId`、`ownerGeneration`、`controlToken`。CLI 沿用提交前保存私有提交令牌、收到控制三元组后保存按微任务和代际命名的私有控制文件、输出中去掉控制令牌的流程；带执行凭据的控制文件调用仍须通过黑板的归属和三元组校验。提交准备和 Host 操作的事务提交处核对执行凭据仍有效，Lead 回合结束或被撤销后，耗时准备中的修改不能继续提交；新回合可以操作原有名下微任务，其建立回合记录不变。

`get` 和列表的 `leadOwnership` 给出归属的 Lead 微任务、创建回合和建立时刻；Lead 的 `get` 给出最多 32 个 `ownedMicroTasks`，并在 `counts` 和 `truncated` 中给出总数及省略数。CLI 的简短读取和列表也保留这些事实，不需要读取审计全文。

Worker 已将派发黑板的 `HMB_STATE_DIR` 放进执行环境；四种 harness 的原生子进程现在同时保留该状态目录、文件凭据及已有运行时选择变量，使回合里的启动器连接同一个黑板。发现与检查进程仍使用原有清理环境。真实私有黑板和启动器子进程测试确认这条连接，另一个黑板拒绝同一个文件凭据。CLI 的 `await` 每次读取和等待都传递原执行凭据，按 `requestId` 直接读取目标，不再扫描列表；需要等待内部执行的停止证据时，带执行凭据的调用等待拥有权限的微任务的事件，再读取它的汇总，不直接寻址内部 Router 或协助任务。

Lead 停止名下微任务使用已有的有期限收尾路径，记录 `stop_initiator='lead'`；Worker 和角色层沿用 Host 收尾的宽限与终止原因判定。`wrap_up.txt` 已在 `packaging/runtime-assets.json` 中声明为 `roles.wrap_up`，`wrap_up_prompt()` 直接以资源名读取，源码和独立运行时资源测试均覆盖它。

Python 检查均使用分配任务指定的解释器，经 `PYTHONPATH=src <指定测试解释器> -m hey_my_buddy.cli.checks <测试路径>` 运行，使用默认进程数和检查运行器自己创建的私有根。受影响检查选择 `tests/python/blackboard/tasks/`、`tests/python/blackboard/store/`、`tests/python/cli/`、`tests/python/protocol/`，以及经过改动源码的 `tests/python/buddy/`、`tests/python/install/`、`tests/python/blackboard/service/`、`tests/python/blackboard/routing/` 和 `tests/python/blackboard/evaluation/test_native_observations.py`，共 223 个文件；初次运行 221 个文件的 3,589 个测试通过，没有跳过，退出 1，私有根移除。

初次受影响检查的两个失败是 `test_blackboard.py` 的读模型字段集合遗漏 `leadOwnership`，以及 `test_workflow.py` 的省略字段集合遗漏 `ownedMicroTasks`，均为旧断言未随新增字段更新；修正后，`test_blackboard.py`、`test_cli.py`、`test_blocking.py` 的 109 个测试通过，`test_workflow.py` 单独重跑的 57 个测试通过，均退出 0 且私有根移除。开发过程中还修正了新增测试的导入、字段选择、并发名额和终态前置条件，以及列表 SQL 的外层别名；修正后的 `test_lead_owned.py` 12 个测试与 `test_host_preview.py` 3 个测试最终一起通过，退出 0，私有根移除，没有遗留失败或因机器时序失败而重跑的文件。

真实 Git 的 `test_lead_workspace.py` 三个测试通过，证明拥有者可以修订范围、解决范围冲突、验收和回收，其他 Lead 即使持有正确控制三元组也被拒绝；`test_workflow_worker.py` 六个测试通过，其中启动器确实连接私有派发黑板。新增权限逐项拒绝普通执行凭据，所有受限操作逐项检查 Host、其他 Lead 和协助任务及混合选择子，另覆盖代理请求、提交事务回滚、准备期间撤销、回合续接、有限读取和名下凭据不能再授予。

预览回归前已运行 `npm --prefix apps/console ci`；初次使用的 Node 25.8.1 有引擎版本提示，随后用项目支持的 Node 24.15.0 再次准备依赖并运行最终预览回归，均成功。没有修改前端，因此没有运行前端单元检查或浏览器布局检查；没有运行完整检查、真实 harness 的原生检查或 `tests/probes/`，没有安装产品、操作日常黑板或读取用户 harness 配置与凭据。`git diff --check` 通过；忽略的原始日志在 `tmp/lead-affected-checks.log`、`tmp/lead-final-focused.log`、`tmp/lead-workflow-final.log` 和 `tmp/lead-list-preview-final.log`。

与本微任务要求不一致或要求未覆盖的情形：没有。Lead 停止时连带名下微任务、Lead 交付或被验收时名下微任务尚未结束的处理、等待方区分及泳道用时与用量，按任务范围留给下一步；本次源码不能据此声明可安装。
