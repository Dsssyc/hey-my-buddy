# Lead 名下的微任务：归属与凭据

本微任务以 `55be5ebe` 为原始输入，实现 Lead 名下的微任务归属与执行凭据权限；第二回合按 Host 的验收要求修正普通回合的环境、名下协助任务的控制、Lead 停止显示和凭据的死字段。交付的是源码、打包资产和离线检查证据，尚未安装，不包含下一步的 Lead 名下生命周期或泳道。

归属单独记在 `workflow_lead_tasks`：`owned_run_id` 是名下微任务的主键及 `workflow_runs` 外键，`lead_run_id` 是持有委派权的微任务外键，`lead_turn_id` 是创建时 Lead 执行回合的 `workflow_turns` 外键，`created_at` 是建立时刻；另有按 `lead_run_id` 的索引和不能归属于自身的约束。归属与微任务的提交、控制令牌和事件在同一个事务里建立，失败时一起回滚；不会写入协助任务的 `workflow_children`，也不把协助任务的批准命令或状态用于另一种意义。

存储改动并入已有 schema 19 → 20 升级：按当前声明创建空的归属表及其索引，schema 仍为 20；`attempts` 重建纳入 `stop_initiator='lead'`，`agent_credentials` 按当前声明重建，删除只写不读的 `scopes_json`，保留凭据的身份、令牌验证值、状态及时间字段。凭据写入及对旧操作集合的存储断言一并删除，授权始终从 `workflow_runs.delegation_authority` 读取。基点样板看板升级后的 SQLite 表和索引声明与新建看板一致，归属表的存量为空，原有原生会话绑定和冻结输入证据不被改写。

普通执行凭据继续使用 `AGENT_OPERATIONS` 和自己的微任务绑定。仅持有委派权的微任务的执行凭据增加 `workflow_submit`、`task_list`、`task_wait`、`events_wait`、`message_wait`、`workflow_decide`、`workflow_continue`、`workflow_cancel`、`workflow_accept`、`workflow_conclude`、`workflow_reclaim`、`workflow_scope_amend` 和 `workflow_workspace_resolve`；原有读取和询问操作可用于直属名下微任务，Worker 的 `workflow_suggest` 和 `message_update` 仍只绑定自己的微任务。接管、宏任务的停止、共享设置、评价、安装与升级不加入白名单。

凭据入口逐一绑定操作真正选择的 `runId`、`taskId`，以及读取操作的 `requestId`、`attemptId` 和 `messageId`，避免一个合法选择子掩盖另一个越界选择子。`workflow_decide.requestId` 是已绑定微任务的业务请求，由 `_request_row` 和代理链检查关联；`targetRunId` 由处理函数现有的 `_target_run` 检查是该微任务自身或它的协助任务，不再重复要求协助任务直属 Lead。Lead 可借名下微任务的控制三元组回答它的协助任务代理请求，并以该微任务为 `runId` 对它的协助任务验收、写结论及进行其他已有的目标操作；Host 或其他 Lead 的协助任务仍由同一处理函数拒绝。直接以不归属 Lead 的微任务或协助任务为 `runId` 的调用仍被凭据入口拒绝，列表仍在计数、分页和过滤之前限定直属归属。

Lead 提交仍经过 `normalize_workflow_submit`，包括完整 buddy、常规路由、执行工作区、时长、标题和锁定 buddy 的既有校验。调用方不能指定 `hostId`、`owner` 或任何宏任务选择子：黑板从执行凭据得出 `hostId='lead:<runId>'`，沿现有提交路径把它记为委派方，并从 Lead 的微任务读取 `macro_task_id`；没有宏任务时双方均为空。`delegationAuthority=true` 被拒绝，名下微任务及其执行凭据不会继承委派权。

名下微任务照常返回控制三元组 `hostId`、`ownerGeneration`、`controlToken`。CLI 沿用提交前保存私有提交令牌、收到控制三元组后保存按微任务和代际命名的私有控制文件、输出中去掉控制令牌的流程；带执行凭据的控制文件调用仍须通过黑板的归属和三元组校验。提交准备和 Host 操作的事务提交处核对执行凭据仍有效，Lead 回合结束或被撤销后，耗时准备中的修改不能继续提交；新回合可以操作原有名下微任务，其建立回合记录不变。

`get` 和列表的 `leadOwnership` 给出归属的 Lead 微任务、创建回合和建立时刻；Lead 的 `get` 给出最多 32 个 `ownedMicroTasks`，并在 `counts` 和 `truncated` 中给出总数及省略数。CLI 的简短读取和列表也保留这些事实，不需要读取审计全文。

Worker 的 Python 控制器仍持有其工具通信所需的黑板状态与执行凭据；四种 harness 的原生子进程仅在冻结回合输入的 `context.delegationAuthority` 为真时收到 `HMB_STATE_DIR`、`HMB_AGENT_CREDENTIAL_FILE` 及已有运行时选择变量。`HMB_LEAD_LAUNCHER` 只是在 Python 控制器内从该回合输入推导的标记，每次构造环境时清除继承值，不传给原生子进程，也不是用户配置或授权依据；普通 Worker 回合和发现、检查进程保持原有清理环境。真实普通 DSH 子进程的环境日志证明没有这些变量，Lead 的真实启动器子进程仍连接私有派发黑板，另一个黑板拒绝同一文件凭据。CLI 的 `await` 每次读取和等待均传递原执行凭据，按 `requestId` 直接读取目标；等待内部执行的停止证据时读取有权限的微任务的事件和汇总，不直接寻址内部 Router 或协助任务。

Lead 停止名下微任务沿用既有的有期限的有序收尾路径，保留 `stop_initiator='lead'`，六处判断共用 `protocol.stop.WRAP_UP_INITIATORS`，表示会给予有序收尾的发起者。控制台类型包括 `lead`，停止发起者、动作者和理由以与 Host 相同的结构显示为 Lead；有序收尾完成显示为“Lead 有序收尾完成”，不再落到“发起者未知”。`wrap_up.txt` 仍作为 `roles.wrap_up` 在资源声明中独立解析。

本回合 Python 检查均使用分配任务指定的解释器，经 `PYTHONPATH=src <指定测试解释器> -m hey_my_buddy.cli.checks <测试路径>` 运行，使用默认进程数和检查运行器自己创建的私有根。定向核心检查三个文件的 36 个测试通过；受影响检查选择 `tests/python/blackboard/tasks/`、`tests/python/blackboard/store/`、`tests/python/cli/`、`tests/python/protocol/`，以及 `tests/python/buddy/`、`tests/python/install/`、`tests/python/blackboard/service/`、`tests/python/blackboard/routing/` 和 `tests/python/blackboard/evaluation/test_native_observations.py`，覆盖 223 个文件。初次其中 221 个文件的 3,625 个测试通过，没有跳过，退出 1，私有根移除。

初次受影响 Python 检查的两个失败文件是 `buddy/harnesses/claude/test_claude.py` 和 `buddy/harnesses/test_controller.py`：前者的旧断言仍期待普通回合获得启动器环境，后者的两个日志清场用例使用的模拟上下文缺少新增读取的 `turn_input` 属性。更新断言和模拟上下文后，两个文件分别单独重跑，38 个和 45 个测试全部通过，均退出 0 且私有根移除；没有修改生产代码来迁就夹具，也没有遗留失败。

行为检查证明：普通执行凭据仍逐项被拒绝新增操作，普通回合不会继承 Lead 的启动器环境标记，Lead 启动器仍连接签发黑板；Lead 可回答名下微任务的协助任务代理请求、验收及写结论，指向 Host 或其他 Lead 的协助任务仍被处理函数拒绝；升级删除存储操作集合而完整保留凭据。第一回合已建立的归属、宏任务、提交事务回滚、提交准备撤销、后续回合继续管理旧归属、有限读取、控制文件及真实 Git 工作区操作，均在本回合受影响检查中再次通过。定向环境检查开发期间修正了夹具的授予前置条件和原生日志定位，最终真实 Worker 相关七个测试通过。

前端使用项目支持的 Node 24.15.0，先运行 `npm --prefix apps/console ci`，再在最终源码上顺序运行 `npm --prefix apps/console test` 与 `npm --prefix apps/console run test:browser`：78 个文件的 1,093 个前端测试与 96 个浏览器检查全部通过，后者完成构建并重建打包资产。初次将前端单元检查与浏览器检查重叠运行时，`console-session.test.tsx` 的三个无效登录会话用例超过 5 秒时限，`desktop-console.test.tsx` 的一个用例等待“还没有宏任务”标题失败；顺序全量重跑全部通过，两个失败文件还分别单独重跑，18 个和 13 个测试通过，两次结果均保留。打包 JavaScript 由旧文件替换为 `index-CRjilw0Y.js`，入口 HTML 更新，CSS 与图标不变。

没有运行完整检查、真实 harness 的原生检查或 `tests/probes/`，没有安装产品、操作日常黑板或读取用户 harness 配置与凭据。`git diff --check` 通过；本回合忽略的原始 Python 日志在 `tmp/lead-revision-core.log`、`tmp/lead-revision-affected.log`、`tmp/lead-revision-claude-retry.log` 和 `tmp/lead-revision-controller-retry.log`，前端单文件重跑日志在 `tmp/lead-revision-console-session.log` 与 `tmp/lead-revision-desktop-console.log`。

与本微任务及 Host 本回合修订要求不一致或要求未覆盖的情形：没有。Lead 停止时连带名下微任务、Lead 交付或被验收时名下微任务尚未结束的处理、等待方区分及泳道用时与用量，仍留给下一步；本次源码不能据此声明可安装。
