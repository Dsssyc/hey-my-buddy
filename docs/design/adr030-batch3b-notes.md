# ADR-030 批次 3b：Codex 交接说明

日期：2026-10-12；基点：`c7607564c56abc062322cb1860c12f17fc11b547`；范围：Codex 运行模块、它的服务接线与测试、被取代行为的参考文档。

这是实现方的验证与待验收说明，不是 Host 验收记录；尚未安装，没有调用真实模型，没有读取日常看板、用户配置或凭据文件，没有启动、停止或重启 hey-my-buddy 服务或 Worker。

## 删除与保留

删除 `buddy/harnesses/codex/home.py` 全文件，以及 Worker、审阅、无工具三种路径的私有配置主目录准备、登录链接、链接清理、保留凭据拒绝、目录描述符固定检查、`coding-home` 证据、`codingHomePrepared` 与 `credentialCleanup` 投影；其他 harness 没有实现这个清理钩子，所以同时删除角色收集器中仅为它存在的钩子。删除 `config.py` 的私有模型缓存读取、模型工具字段改写、`no-tool-models.json` 与私有 `config.toml` 写入，以及全部用于挑选配置的 feature/skill/指令层开关。

D 类四处删除：`_initialize` 的 ChatGPT-only 与固定 openai 条件、`_check_thread_receipt` 的固定供应方条件、`check_preparation` 的固定供应方条件；审阅与无工具混合条件里的固定 openai 部分也删除。`_configure_review` 不再拒绝 MCP 或继承配置；`_configure_fast` 不再要求唯一自建 user 层、排除外来层或 MCP。会话身份、检出归属、模型匹配、回合开始/完成证据、输出大小、实际失败与停止确认仍保留，其余 F/G 拒绝交批次 4。

`HOME` 与已有的 `CODEX_HOME` 经共享服务环境白名单原样传递；不设置新的值，不注入 `CODEX_SQLITE_HOME`，不指定 `sqlite_home` 或 `cli_auth_credentials_store`。白名单及 PATH 中的原生命令目录、代理、证书路径、语言和平台变量保持原共享行为，供应方密钥环境变量仍不继承，用户通过 harness 的原生配置使用供应方。`BUDDY_CODEX_FIXTURE_CASE` 与 `BUDDY_CODEX_FIXTURE_STATE` 只在开发夹具模式传递，不是产品配置。

## 参数与文件逐项核对

| 路径 / 参数 / 文件 | 处理 | 理由 |
| --- | --- | --- |
| `--version`；`app-server --listen stdio://`；独立进程组、管道、私有 stderr | 保留 | 原生发现、传输及实际停止证据，不挑选配置 |
| `CODEX_HOME`、`HOME` 的赋值；`CODEX_SQLITE_HOME` 注入；`-c sqlite_home`、凭据存储后端强制 | 删除 | 配置、凭据与会话的隔离 |
| `auth.json` 链接；私有 `config.toml`、`models_cache.json` 读取与 `no-tool-models.json` 写入 | 删除 | 私有主目录及配置挑选 |
| 关闭 apps、plugins、hooks、skills、用户指令、快照、browser、multi-agent、memories 等配置键 | 删除 | 无权替用户挑选其 harness 配置 |
| `web_search=disabled`、`approval_policy=never` | 审阅/无工具通过原生 `-c` 保留 | 表达当前联网与审批权限，不写用户文件 |
| 审阅的 `default_permissions=buddy-router` 与该 profile 的 filesystem/network map | 通过原生 `-c` 保留 | 当前只读/禁网权限表达；整张 profile 是 TOML 内联表值，路径不进入点号键；不限制其他用户配置层 |
| `initialize` 的 clientInfo；审阅/无工具 `experimentalApi` | 保留 | 原生连接身份及已有公开实验接口接线 |
| `account/read(refreshToken=false)`；`config/read`；`model/list` 的分页/隐藏项参数 | 保留 | 无模型的原生事实读取；配置来自 app-server，不直接读取用户文件 |
| Worker `thread/start`、`thread/resume` 的 cwd/model/modelProvider、serviceName、approvalPolicy、sandbox | 保留，modelProvider 用请求配置 | 绑定获配检出与请求配置、表达暂行权限；原生供应方回执单独记 checked 值，不因与 openai 不同拒绝 |
| 审阅 `thread/start` 的 permissions；无工具的 read-only sandbox；两者 raw events | 保留 | 权限表达与事后工具事实 |
| 快速与审阅 Router `thread/start.ephemeral=true` | 恢复 / 增加 | Host 于第二回合定：Router 不续接，不将每次路由决策写进用户会话历史；Worker 不传此开关 |
| 无工具 `baseInstructions`、空 environments/dynamicTools/selectedCapabilityRoots、禁止供应方模型 fallback | 删除 | 不另建指令层或挑选用户配置与原生能力 |
| `turn/start` 的 threadId/cwd/model/effort、任务输入、outputSchema、审批、Worker sandboxPolicy 与审阅 permissions | 保留 | 归属、请求配置、任务/结果约定及暂行权限；Worker 网络仍沿用原来的 false，不在本批设计档位 |
| 无工具 `turn/start` 的空 environments 与 thread/started 的空环境条件 | 删除 | 不再挑选原生环境来源；仍核对会话身份 |
| `thread/read(includeTurns=true)`；`turn/interrupt`；拒绝交互请求的原生错误响应 | 保留 | 续接归属、停止与没有 Host 审批渠道的事实 |
| 微任务绑定 JSON、checkpoint、有界最终消息、工具/中断/额度证据、配置与会话回执 | 保留 | 黑板执行证据，不是 harness 配置或登录状态 |

审阅的权限 map 仍拒绝隐式临时目录与根路径、允许 minimal 与冻结检出；本批没有重做权限档位。`config/read` 读的是分层后的有效配置，不能用模拟服务器把 thread-local 覆盖伪装成全局已生效配置；因此权限覆盖放在 app-server 启动的原生 `-c` 参数，模拟服务器按同样顺序先加载用户配置再合并这些权限参数，而不是模拟 thread/start 写配置。真实原生版本上的回读与权限效果仍需 Host 验收。官方接口依据是 [Codex App Server](https://learn.chatgpt.com/docs/app-server)，本次仅核对 config/read、thread/start/resume 与用量事件的文档描述，没有阅读或移植其他项目代码。

取消隔离后不能把整份用户配置写进回执：审阅的 `review-config-readback` 只保留相关权限/审批/搜索值、buddy-router profile、apps 状态与 MCP 是否存在，不保留用户 MCP 参数、环境或供应方凭据；这个取舍只限制输出证据，不限制进入原生进程的用户配置。

## 会话与存量

新绑定只有 `taskId`、`threadId`、`cwd`、`configuration`、`lastTurnId`、`lastAttemptId`、`lastInputSha256`；删去 `codexHome` 和整个 `credentialSource`，没有固定 `source=native` / `credentialRevision=0`，没有账户来源比较。续接仍核对 checkpoint 的回合、attempt、摘要，以及原生 thread/read 的最后已完成回合；没有缺失绑定时的隐式回退。

服务按已有的明确 `storageOwner` 区分位置：Codex 新回执为 `harness-user-store`，存储范围 `harness-user-store`；旧 `buddy-goal` 回执不再原生续接，而以 `native-storage-changed` 重建。运行模块只读七字段新绑定，旧形式直接拒绝，不增加第二种读法或历史转换。历史回执与最终消息原样保留，会话历史列表仍可列出旧的归属事实；DSH 的归属判断与理由不变。没有改变 SQLite 存储字段形式，schema 保持 17。

Host 于验收时测量真实状态目录并提供结果：10 个旧 `codex-home`，7064 个文件，495 MiB，7 个链接，其中 1 个是指向用户自己 Codex 目录的 `auth.json`；只量名字、数量与大小，未读取凭据内容。该组数字来自 Host，不是实现方在本回合重新访问状态目录的结果。

Host 已同意本批不清理旧私有主目录，是否清理由用户另定；本批不删除这些存量。今后若授权清理，仍需明确范围、来源执行的停止依据及备份；现有备份排除了部分原生/凭据路径，不能据升级或缺少进程 ID 推断可以删除。

## 第二回合：会话归属读者核对

`harness-user-store` 表示 harness 用户目录持有会话，不授予黑板删除该目录的权力；Worker 的 storageScope 与 storageOwner 都使用这个已有值，删除上一回合新造的 `user-native` 输出值。Router 的快速与审阅路径均传 `ephemeral=true`，不记录可续接的 Worker 绑定；Worker 的 thread/start/resume 不传 ephemeral，继续普通持久化和原生续接。没有恢复 baseInstructions、空 environments/dynamicTools/selectedCapabilityRoots 或禁模型 fallback。

| 读者 / 关联移除路径 | 核对结果 |
| --- | --- |
| `buddy/roles/run_execution.py` WorkerRunExecutor.collect | 从 Codex session_facts 原样投影 storageScope/storageOwner；可续接还须绑定、回合及两层停止证据；不由这些字段推导删除路径 |
| `blackboard/tasks/workflow.py` 续接选择 | Codex 只认可 storageOwner=harness-user-store 的当前存储；旧 buddy-goal 回执重建，storageScope 不参与授权；DSH 分支不变 |
| `blackboard/tasks/worker_sessions.py` 会话历史列表 | 只读黑板内停止确认的归属回执，列出用户存储与旧 buddy-goal 历史；nativeAccess=false，只允许 list，原生会话 ID 不成为文件访问或删除路径 |
| `blackboard/tasks/storage.py` inspect / apply / _validate_pending | 不读 storageOwner/storageScope 或会话 ID 来找删除位置；候选从黑板 state 下已知 harness goal 根、注册检出或运行时根生成；harness 恢复删除的父目录必须是 state/harnesses/<adapter>/goals；不枚举用户 Codex 主目录 |
| `blackboard/tasks/workflow.py` cleanup_apply | 只从 board.directory、adapter 和微任务 ID 构造 private_dirs.goal_root；回收绑定与执行证据，不把用户原生会话归为这棵树 |
| `private_dirs.py` goal_root / native_root / remove_tree | 根固定在黑板 state/harnesses/.../goals 下；根路径链接拒绝，树内链接仅解除链接，不跟随到用户目录 |
| `blackboard/store/private_migration.py` 旧布局迁移 | 按旧 state 内目录与记录固定路径搬迁，非 nativeSession 归属读者；本批未更改迁移或赋予用户原生目录任何搬迁/清理权限 |
| `apps/console/src/native-session.tsx` 与 `WorkflowPanel.tsx` | 从选中 attempt 的冻结回执读取两字段，既有标签分别是 Harness 用户存储、由 Harness 用户目录持有；会话区为只读，未接原生归档/删除动作；这次不改前端 |

`rg` 核对源码与测试中的两字段后，除其他 harness 自己的事实输出与夹具外，所有生产读者就是上述角色投影、续接选择、会话列表和会话页；存储回收与旧布局迁移没有读取这两字段。新增 `blackboard.tasks.test_storage.StorageTests.test_codex_user_sessions_are_not_inventory_or_goal_removal_targets`：私有测试目录模拟用户原生会话，在黑板 goal 内保留到用户目录的链接，验证用户目录不成为 storage 候选、用户文件不计入私有树大小，且删除该私有 goal 后用户会话原样保留。

第二回合测试修正：恢复 test_codex 的 stat 引入；无工具用量只检查规范化的 inputTokens/cachedInputTokens/outputTokens/reasoningOutputTokens 及事实标志，明确没有 totalTokens，不向生产结果另加键；Worker 会话列表夹具改期望 harness-user-store，保留历史 buddy-goal 列表测试。规范化契约以 protocol/usage.py.normalize_token_usage 为准，fast 与 Worker 均使用它。

## 供应方、登录与计费

公开目录的 provider 来自同一次原生读取的 `config/read.config.model_provider`，model/list 给模型与努力档位；缺少供应方事实写 `unknown`，不猜 openai。运行请求中的 provider 原样交给原生 thread/start/resume；结果的 requested 保留请求，checked.provider 使用实际 thread 回执。缺少原生回执不编造 checked 值；远端 served-model 的 observed 仍为 null。

apiKey 登录的发现状态可确认，其他未知登录类型作为 unknown 账户事实，运行不因此拒绝。OpenAI 的 subscription/metered 标注沿用现有 `codex_account`；其他供应方不因账户类型或供应方名字推断计费，一律 unknown。account_probe 仍返回 `(billing, quota, partial_error)`，billing 增加一次原生读取的 provider；健康接线按该 provider 写 `billingByProvider`，内部 fact 不含冗余 provider；原生 OpenAI rate-limit scope 不再被健康接线强行覆盖。

本批不改 Claude 批次拥有的共享目录/计费函数；沿用 `canonical_payload` 接受任意 provider 与 `for_provider` 按健康的实际 provider 查 fact 的现有接口。发现原始结果附带 `billingByProvider`，当前 canonical_payload 不保留该字段；目录显示计费仍经健康投影。若 Claude 批次增加目录快照里的计费字段，假定接口是保留原生 `billingByProvider[provider] -> {kind,source,observedAt}`，没有事实仍 unknown，不调用尚不存在的接口，也不自行更改共用函数。供应方别名与地址的共同身份规则由该批/Host 决定，本批不根据 URL 或密钥创造供应方名。

## 三项顺带修改

Codex 的 NativeSchemaWorker 使用 `outcome_schema(suggested_profile=True, require_suggested_profile=True)`，suggestedProfileId 必填但可为空，满足 Codex 的严格结构化输出规则；其他 harness 仍沿用各自的可选字段契约。同步更新 Codex schema 固定摘要与递归严格对象测试，Worker 角色资源不改。

原生提供 `thread/tokenUsage/updated`，原来的 fast carrier 将它忽略；现在由已有 TurnEvidence/attempt_token_usage 收集该回合增量，在多轮格式纠正时相加、排除累计历史和重复观察，并在某轮缺失时标为 partial。没有用量保持 None，EOF 排干阶段的新用量也纳入；fast 角色结果把通用 usage 包投影为规范化 tokenUsage。官方文档描述此原生事件，具体 last/total 算法沿用仓库已有的 Worker 实现；没有付费检查当前真实 Codex。

角色资源测试不读 `docs/design/role-texts.md`、不允许 Worker 特殊多两行；直接守 Worker/Router/Lead 资源的角色、权限、求助和完成要点，保留实际四家提示词边界测试。设计文档转为资源索引由 Host 做。

## 验证与交接

所有命令都在检出根运行，使用 Host 指定的只读现成解释器、`PYTHONPATH=src:tests/python`，先清除 AGENTS.md 列出的十个环境变量；没有安装依赖，没有 uv，没有写只读环境。解释器路径在记录中写为 `<provided-python>`。

第一回合源码的可运行选择共 190 项，32.468 秒，`OK`；`git diff --check` 通过，删除文件的公开导出也逐项核对。覆盖 native_run/protocol、纯工具事件投影、角色资源/执行/schema/审阅、无进程的注册接线、健康与计费、三条发现/readiness，以及六条无需控制器的服务续接测试。源内使用离线模拟 app-server，验证不代表真实安装或 Host 接受。

可复现的选择：`env -u BUDDY_STATE_DIR -u BUDDY_RUNTIME_ROOT -u BUDDY_RUNTIME -u BUDDY_RUNTIME_IDENTITY -u BUDDY_WORKER_STATE -u BUDDY_WORKER_ID -u BUDDY_AGENT_CREDENTIAL -u BUDDY_AGENT_CREDENTIAL_FILE -u VIRTUAL_ENV -u UV_PROJECT_ENVIRONMENT PYTHONPATH=src:tests/python <provided-python> -m unittest` 后接：

- `buddy.harnesses.codex.test_native_run`、`buddy.harnesses.codex.test_codex_protocol`、`buddy.harnesses.codex.test_codex_tool_evidence.CodexToolEventProjectionTests`。
- `buddy.roles.test_role_prompts`、`buddy.roles.test_run_execution`、`buddy.roles.test_schema_worker`、`buddy.roles.test_registered_review`、`buddy.roles.test_registered_run_wiring.RegisteredDescriptionTests`、`buddy.roles.test_registered_run_wiring.NativeEvidenceProjectionTests`。
- `blackboard.service.test_harness_health`、`protocol.test_billing_quota`。
- `buddy.harnesses.codex.test_codex.CodexAdapterTests` 的 `test_capability_readiness_does_not_start_a_native_probe`、`test_complete_empty_native_catalog_is_not_a_discovery_failure`、`test_discovery_reports_user_configured_provider` 三个完整测试名。
- `buddy.harnesses.codex.test_codex_continuation.CodexContinuationTests` 的六个完整测试名：`test_invalid_outcome_can_resume_exact_completed_native_history`、`test_native_failure_reconstructs_with_previous_assistant_message`、`test_historical_private_thread_reconstructs_without_touching_native_history`、`test_harness_version_change_reconstructs_with_the_same_message`、`test_foreign_attempt_cannot_supply_resume_or_reconstructed_context`、`test_refused_resume_does_not_erase_the_last_message_before_reconstruction`。

受沙盒阻挡的测试没有改成 skip，没有绕过监听限制：角色控制器的 C-Two live endpoint 创建返回 `Operation not permitted (os error 1)`，由此出现 role-controller-failed 或 invalid-native-result，后续无法得到真实 checkpoint / 停止帧。Host 需在允许本地监听的环境运行：`buddy.harnesses.codex.test_codex` 的控制器生命周期部分、`buddy.harnesses.codex.test_no_tool_codex` 全部、`buddy.harnesses.codex.test_codex_tool_evidence.CodexReviewToolEvidenceTests`、`buddy.harnesses.codex.test_codex_continuation.CodexContinuationTests.test_controller_failure_to_workflow_continue_keeps_the_native_thread`，以及 `buddy.roles.test_registered_run_wiring` 的 FastRegisteredRunTests/WorkerRegisteredRunTests/DshPublishedReceiptTests。完整检查、真实启动器、真实 Worker/Router 回合和故障注入仍按冻结任务由 Host 做。

Host 的真实验收还应核对：用户第三方供应方可用，MCP/skill/钩子/用户指令可见且角色说明生效，Worker 普通会话写进原生目录并原生续接、两种 Router 调用使用 ephemeral，原生和控制器两层停止证据真实，运行与异常结束均没有新建私有配置主目录或凭据链接；旧私有主目录本批不清理，未来清理由用户另定。Windows/Linux、本次真实权限回读与真实 fast 用量仍未验证。

第二回合最终源码的可运行选择共 108 项，27.295 秒，`OK`：`buddy.harnesses.codex.test_native_run`、`blackboard.tasks.test_storage`、`buddy.harnesses.codex.test_codex_protocol` 全文件，`buddy.runtime.test_worker_sessions.WorkerSessionTests` 的 `test_dsh_sessions_are_no_longer_listed_or_archived` 与 `test_codex_list_uses_board_only_and_never_guesses_other_sessions`，以及上列三条 Codex 发现/readiness 测试；清环境与解释器命令同上。`git diff --check` 通过；未改前端。

同时用指定解释器执行 `-m unittest buddy.harnesses.codex.test_codex buddy.harnesses.codex.test_no_tool_codex buddy.runtime.test_worker_sessions`，51 项、42.864 秒，未通过（42 个失败、16 个错误，含 subTest）；控制器无法建立 C-Two live endpoint，因而没有有效原生结果帧或确认停止，后续断言失败。没有改成 skip 或用模拟停止来掩盖它。Host 需重跑这三个文件，具体不可运行范围为 test_codex 的控制器生命周期用例、test_no_tool_codex.NoToolCodexTests 全类、test_worker_sessions.WorkerSessionTests.test_codex_list_accepts_actual_stopped_adapter_fixture；它们的三个原始测试缺陷已在相应测试与输出归属处修正，用量生产契约没有增加 totalTokens。

此前 Host 在第一回合候选上报告前端 78 个文件、1119 项通过，完整 Python 216 个文件里 213 个通过，上述三个文件未过；这是 Host 提供的验收事实，不是本回合的复跑。

## 第三回合：真实接口暴露的四处修正

Host 在 `82638e50` 上报告完整 Python 3661 项、216 个文件与前端全量均通过，并用真实 codex-cli 0.162.1 经运行模块试了 Worker、快速与审阅三条路径；进程读用户自己的 Codex 目录和 skill，私有原生目录没有写入，Worker 会话写入用户会话库、快速路径没有会话文件，原生进程组确认消失。这些是 Host 提供的上一候选观察；审阅启动、Worker schema 和快速通知处理仍失败，所以不代表本次修正已经真实通过，本回合没有进行新的真实调用。

### 权限参数：路径留在表值里

根因是 `-c` 的键按点号拆分，不按 TOML 引号键解析；上一版把带点号的冻结路径放进键，即使加引号仍会拆坏。现在只传 `web_search`、`approval_policy`、`default_permissions` 等简单键和固定的 `permissions.buddy-router` 键，该 profile 的完整 filesystem/network 表作为一个 TOML 内联表值；动态路径从不出现在 `-c` 键里。只覆盖这一个运行所用的 profile，不替换其他用户权限 profile，不写配置文件。

语法依据：[官方高级配置的单次覆盖](https://learn.chatgpt.com/docs/config-file/config-advanced#one-off-overrides-from-the-cli)规定 `--config/-c key=value`，值按 TOML 解析、点号用于嵌套键；本批将 TOML 表序列化为值的做法来自这条接口规则。具体形式是 `-c 'permissions.buddy-router={ "filesystem" = { ":root" = "deny", "<checkout>" = "read", ... }, "network" = { "enabled" = false } }'`，省略号仅为说明，执行时由源码生成完整表。字符串、引号、反斜线与 Unicode 都按 TOML 基本字符串规则编码；测试的冻结路径包含点号、空格和双引号。

两份离线 app-server 夹具也修正为分别解析 CLI 键与 TOML 值，不再用 TOML 解析整条赋值去错误接受带引号的点号键；新增回归核对实际启动参数的键集合、内联表值及模拟 config/read 回读。

### 严格 schema、失败事实与会话通知

[官方结构化输出规则](https://developers.openai.com/api/docs/guides/structured-outputs#all-fields-must-be-required)要求对象的 properties 全部出现在 required，非必选内容用 null 表达，并关闭 additionalProperties。只有 Codex 选择 `require_suggested_profile=True`，将 suggestedProfileId 纳入 required，string/null 联合类型保留；Claude 的 schema 与摘要、ZCode/DSH 的 schema、Worker 角色资源都不变。递归回归遍历所有 object 节点而不是只检查 request，Codex 夹具的 assistance 请求也提供该字段的 null 值。

原生错误事实使用已有 `RunResult.native_error`，不把 schema/HTTP 错误伪装成 `native_failure` 的额度归因；角色回执的 nativeFailure 与原生证据投影保留该事实。按 [App Server 的错误与完成事件](https://learn.chatgpt.com/docs/app-server#errors)读取本 thread/turn 的 turn/completed.error，或本 thread 的非重试 error 通知，只保留 message、additionalDetails 与 codexErrorInfo；文字分别最多 4096 UTF-8 字节，信息对象最多 2048 字节，截断/省略有标记，不保留无关字段。事实写明方法、原生会话，只有通知明确给出本回合标识时才附 nativeTurnId；显式外国回合不提供错误归因。Worker、审阅、快速三条路径均投影到同一原生错误字段，非额度错误仍不产生 quotaFailure；Worker 的重复额度读取同时减为一次。带错误的完成、HTTP 400 结构信息、非额度归因、中文加非 BMP 字符的安全截断都有回归。

快速路径将“缺少回合标识”与“明确指向另一个回合”分开：同会话且没有回合标识的通知不会被当成外国回合，明确的外国 thread/turn 仍拒绝，FastProtocol 的拒绝消息附有方法名并限制字节数。未知的线程级通知在已有事实流中单独记为 threadNotifications，计数采用现有的类型上限与溢出桶，并以私有 thread-notifications 证据保存，避免原有 FastCorrection 将它们按 unknownEvents 拒绝；不保存通知的参数。工具事实、未知 turn-scoped 方法与其他 F 类规则不变，仍交批次 4；身份回归用已知方法单独测试，避免与尚未移除的未知回合事件规则混为一件事。

核对同文件 Worker 与审阅：Worker carrier 委托 protocol.TurnEvidence.observe，它对缺少回合标识或外国标识返回 None，不会抛 Foreign native turn；审阅 carrier 允许缺少 params.turnId，最终也由 TurnEvidence 排除非本回合数据。它们没有同样的误拒绝，所以未增加归属拒绝；两者也能保留线程通知计数。新增快速回归验证同会话未知通知继续完成、明确外国身份拒绝且带方法名，以及超长多字节方法名的消息仍有界。

### Host 无模型权限读回步骤

在已获准读取用户 harness 配置的 Host 环境中，从候选源码根运行下面步骤，用 Host 选中的真实 codex 可执行文件及冻结检出替换两个占位参数；解释器与清环境规则同上。代码沿用本批的参数生成、环境白名单、连接与进程组收尾，只发送 initialize、initialized 通知及 config/read，不发送 account/read、model/list、thread/start 或 turn/start，也不创建任何私有 Codex 主目录；这是配置语法/回读检查，不是沙箱效果或真实回合的验收。本实现方没有运行这个真实 CLI 步骤。

```sh
PYTHONPATH=src:tests/python <provided-python> - '<codex-executable>' '<frozen-checkout>' <<'PY'
import os
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path
from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.buddy.harnesses.codex.config import read_only_config, policy_matches
from hey_my_buddy.buddy.harnesses.codex.native_run import _config_overrides
from hey_my_buddy.buddy.harnesses.codex.protocol import Connection
from hey_my_buddy.buddy.harnesses.discovery import native_environment
from hey_my_buddy.buddy.harnesses.native_support import halt_owned_group
from hey_my_buddy.buddy.runtime.windows_process import owned_popen

cwd = str(Path(sys.argv[2]).resolve())
expected = tomllib.loads(read_only_config(cwd))
command = [sys.argv[1]]
for key, value in _config_overrides(expected).items():
    command.extend(('-c', key + '=' + value))
process = owned_popen([*command, 'app-server', '--listen', 'stdio://'], cwd=cwd,
                      env=native_environment(dict(os.environ), command=command),
                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                      start_new_session=True, close_fds=True)
handle = ProcessHandle(process, own_group=True, log_paths={})
try:
    connection = Connection(process, time.monotonic() + 15, threading.Event())
    connection.call('initialize', {'clientInfo': {'name': 'hey_my_buddy', 'version': '0.30.0'}})
    connection.send({'method': 'initialized', 'params': {}})
    actual = connection.call('config/read', {'cwd': cwd, 'includeLayers': False})['config']
    assert policy_matches(actual, expected)
    profile = actual['permissions']['buddy-router']
    filesystem = {key: value for key, value in profile['filesystem'].items() if value is not None}
    assert filesystem == expected['permissions']['buddy-router']['filesystem']
    assert profile['network']['enabled'] is False
    print('Read-only filesystem/network, approval and search readback match')
finally:
    stopped, _ = halt_owned_group(process, handle, time.monotonic() + 5)
    process.stdout.close()
    assert stopped, 'Native process group stop is unconfirmed'
    assert process.returncode == 0, f'Native app-server exit: {process.returncode}'
PY
```

第三回合最终源码的可运行选择共 145 项，26.402 秒，`OK`；使用上述清环境命令与指定解释器，模块为 `buddy.harnesses.codex.test_native_run`、`buddy.harnesses.codex.test_codex_protocol`、`buddy.roles.test_schema_worker`、`buddy.roles.test_run_execution`、`buddy.roles.test_role_prompts`、`buddy.roles.test_registered_run_wiring.RegisteredDescriptionTests`、`buddy.roles.test_registered_run_wiring.NativeEvidenceProjectionTests`、`buddy.harnesses.codex.test_codex_tool_evidence.CodexToolEventProjectionTests`。另外以 AST 解析确认上述 Host 脚本语法有效，RPC 调用只有 initialize 与 config/read；没有执行真实 CLI。`git diff --check` 通过。

本回合未重复运行已确认受 C-Two 监听限制阻挡的 `buddy.harnesses.codex.test_codex` 控制器生命周期用例、`buddy.harnesses.codex.test_no_tool_codex.NoToolCodexTests`、`buddy.harnesses.codex.test_codex_tool_evidence.CodexReviewToolEvidenceTests`；由 Host 在允许监听的环境复验，未增加 skip 或模拟停止。Host 在上一候选上的完整 Python 与前端通过是该候选的机械证据，不是第三回合源码的验收；真实配置读回、三种路径重跑与安装验收仍由 Host 在获得用户批准后完成。

## 第四回合：原生续接被明确拒绝后的原因码（按 Host 第五回合更正）

Host 报告：在第三候选 `db3126fa` 上，真实 codex-cli 0.162.1 使用含点号的冻结检出运行无模型 initialize/config/read 步骤，权限读回符合预期；审阅启动语法问题已由 Host 确认修好。第三回合其余三项真实验证仍待用户批准；本实现方没有重跑真实 CLI 或读取用户配置。

Host 随后在真实 codex-cli 0.162.1 上做了无模型探测，只发送 initialize、thread/read、thread/resume，没有发送回合：不存在的会话对 includeTurns 的 thread/read 回答关联 JSON-RPC 错误 `thread not loaded: <id>`；thread/resume 回答 `no rollout found for thread id <id>`；存在但未加载的会话，thread/read 正常返回其回合。这三条是 Host 提供的实测事实，不是本实现方的探测，也不是官方文档保证的措辞。[官方 App Server 文档](https://learn.chatgpt.com/docs/app-server#start-or-resume-a-thread)说明读取和续接操作，但没有提供稳定的不存在专用错误码或文案契约。

最终判据只认“原生给了关联的拒绝回答”：native-session 的 thread/read 或 thread/resume 调用收到 Connection.call 的 native-rpc-error，就将原因码改为 native-resume-unavailable，消息原样沿用 Connection 已经限制在 512 UTF-8 字节内的原生文本，保留方法名；没有比较文案、没有根据原生通用错误码推断会话不存在。其他原因码不改：无回答、EOF/传输中断、超时仍为原来的 transport-error/deadline 等事实；停止未确认仍不能导入回合或授予续接。任务/检出/配置、绑定与 checkpoint 比较和原生历史检查保持原样。

第四回合同回合重建机制全部撤回：删去 native-session-not-found 文案匹配、native_continuation 状态、native-continuation 证据、回合事实与 provenance 新分支、角色收集器的两处投影，以及仅供它们使用的模拟分支和测试；不留兼容、别名或第二种读法。native-session 被拒后不会发送 thread/start，模型未启动即返回失败；重建仍只由黑板服务在 Host 再次 continue 后决定，下一回合是 reconstructed-new-session，黑板保存的上一回合最后消息继续保留。服务源码与已有 test_refused_resume_does_not_erase_the_last_message_before_reconstruction 均未修改。

最终模拟 app-server 分别采用 Host 实测的 thread/read 与 thread/resume 拒绝文本，测试断言 native-resume-unavailable、有界原生消息保留、模型未启动、没有 thread/start；额外采用不同的 MCP 拒绝文本守住规则不依赖措辞，并保留没有回答仍为 deadline 的反例。docs/reference/workers.md 在 Codex 的既有续接描述中补上原生一侧已读不到会话的情况；Host 引用的原句实际位于 ZCode 小节，因此仅补 Codex 小节，ZCode 内容保持原样，没有改变回执或存储形式。

第五回合最终可运行选择共 123 项，29.952 秒，`OK`；使用上述清环境命令与指定只读解释器运行 `buddy.harnesses.codex.test_native_run`、`buddy.harnesses.codex.test_codex_protocol`、`buddy.roles.test_run_execution`、`buddy.roles.test_schema_worker` 四个完整文件，以及已有的 `buddy.harnesses.codex.test_codex_continuation.CodexContinuationTests.test_refused_resume_does_not_erase_the_last_message_before_reconstruction`，`git diff --check` 通过。撤回后的 protocol.py、run_execution.py、test_codex.py、test_codex_protocol.py 均与第三候选对应文件一致；源码与测试内已无第四回合同回合重建的名称和模拟分支。test_codex 的完整控制器生命周期测试与 test_codex_continuation 的 test_controller_failure_to_workflow_continue_keeps_the_native_thread 需要本沙盒不允许的 C-Two 监听，仍由 Host 复验。第四回合新增的控制器重建测试已随机制删除，不再列为待运行项目；真实三种路径与完整验收仍由 Host 在得到用户批准后完成。
