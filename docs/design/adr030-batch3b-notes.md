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
| 审阅的 `default_permissions=buddy-router` 与该 profile 的 filesystem/network map | 通过原生 `-c` 保留 | 当前只读/禁网权限表达；路径键使用 TOML 引号，不限制其他用户配置层 |
| `initialize` 的 clientInfo；审阅/无工具 `experimentalApi` | 保留 | 原生连接身份及已有公开实验接口接线 |
| `account/read(refreshToken=false)`；`config/read`；`model/list` 的分页/隐藏项参数 | 保留 | 无模型的原生事实读取；配置来自 app-server，不直接读取用户文件 |
| Worker `thread/start`、`thread/resume` 的 cwd/model/modelProvider、serviceName、approvalPolicy、sandbox | 保留，modelProvider 用请求配置 | 绑定获配检出与请求配置、表达暂行权限；原生供应方回执单独记 checked 值，不因与 openai 不同拒绝 |
| 审阅 `thread/start` 的 permissions；无工具的 read-only sandbox；两者 raw events | 保留 | 权限表达与事后工具事实 |
| 无工具 `ephemeral`、`baseInstructions`、空 environments/dynamicTools/selectedCapabilityRoots、禁止供应方模型 fallback | 删除 | 会话普通运行；不另建指令层或挑选用户配置与原生能力 |
| `turn/start` 的 threadId/cwd/model/effort、任务输入、outputSchema、审批、Worker sandboxPolicy 与审阅 permissions | 保留 | 归属、请求配置、任务/结果约定及暂行权限；Worker 网络仍沿用原来的 false，不在本批设计档位 |
| 无工具 `turn/start` 的空 environments 与 thread/started 的空环境条件 | 删除 | 不再挑选原生环境来源；仍核对会话身份 |
| `thread/read(includeTurns=true)`；`turn/interrupt`；拒绝交互请求的原生错误响应 | 保留 | 续接归属、停止与没有 Host 审批渠道的事实 |
| 微任务绑定 JSON、checkpoint、有界最终消息、工具/中断/额度证据、配置与会话回执 | 保留 | 黑板执行证据，不是 harness 配置或登录状态 |

审阅的权限 map 仍拒绝隐式临时目录与根路径、允许 minimal 与冻结检出；本批没有重做权限档位。`config/read` 读的是分层后的有效配置，不能用模拟服务器把 thread-local 覆盖伪装成全局已生效配置；因此权限覆盖放在 app-server 启动的原生 `-c` 参数，模拟服务器按同样顺序先加载用户配置再合并这些权限参数，而不是模拟 thread/start 写配置。真实原生版本上的回读与权限效果仍需 Host 验收。官方接口依据是 [Codex App Server](https://learn.chatgpt.com/docs/app-server)，本次仅核对 config/read、thread/start/resume 与用量事件的文档描述，没有阅读或移植其他项目代码。

取消隔离后不能把整份用户配置写进回执：审阅的 `review-config-readback` 只保留相关权限/审批/搜索值、buddy-router profile、apps 状态与 MCP 是否存在，不保留用户 MCP 参数、环境或供应方凭据；这个取舍只限制输出证据，不限制进入原生进程的用户配置。

## 会话与存量

新绑定只有 `taskId`、`threadId`、`cwd`、`configuration`、`lastTurnId`、`lastAttemptId`、`lastInputSha256`；删去 `codexHome` 和整个 `credentialSource`，没有固定 `source=native` / `credentialRevision=0`，没有账户来源比较。续接仍核对 checkpoint 的回合、attempt、摘要，以及原生 thread/read 的最后已完成回合；没有缺失绑定时的隐式回退。

服务按已有的明确 `storageOwner` 区分位置：Codex 新回执为 `harness-user-store`，存储范围 `user-native`；旧 `buddy-goal` 回执不再原生续接，而以 `native-storage-changed` 重建。运行模块只读七字段新绑定，旧形式直接拒绝，不增加第二种读法或历史转换。历史回执与最终消息原样保留，会话历史列表仍可列出旧的归属事实；DSH 的归属判断与理由不变。没有改变 SQLite 存储字段形式，schema 保持 17。

在获配检出内以不跟随链接的目录遍历排除 git/依赖缓存后，量到旧 `codex-home` 目录 0 个；这不是实际状态目录的数量。实际状态目录与旧私有主目录在授权范围之外，故没有访问；没有把“没有量到”当作“那里是空的”。旧源码可证明曾写登录链接或刷新文件、配置、模型裁剪缓存并让 Codex 在里面保存 SQLite/rollout 等原生状态，但这是写入者清单，不是实际存量测量。

请 Host 仅量目录/文件名、类型、数量、字节数与链接目标的类别，不读取凭据内容、不跟随凭据链接；覆盖旧 goal-private 与 attempt-private Codex 主目录，另核对来源执行的停止证据。建议本次升级不清理：原生会话有历史价值，现有备份还排除了部分原生/凭据路径，未量清也未证实原来执行已停时不能安全删除；将来若决定清理，应单独列出范围、备份和停止依据。本批没有删除任何存量目录或凭据。

## 供应方、登录与计费

公开目录的 provider 来自同一次原生读取的 `config/read.config.model_provider`，model/list 给模型与努力档位；缺少供应方事实写 `unknown`，不猜 openai。运行请求中的 provider 原样交给原生 thread/start/resume；结果的 requested 保留请求，checked.provider 使用实际 thread 回执。缺少原生回执不编造 checked 值；远端 served-model 的 observed 仍为 null。

apiKey 登录的发现状态可确认，其他未知登录类型作为 unknown 账户事实，运行不因此拒绝。OpenAI 的 subscription/metered 标注沿用现有 `codex_account`；其他供应方不因账户类型或供应方名字推断计费，一律 unknown。account_probe 仍返回 `(billing, quota, partial_error)`，billing 增加一次原生读取的 provider；健康接线按该 provider 写 `billingByProvider`，内部 fact 不含冗余 provider；原生 OpenAI rate-limit scope 不再被健康接线强行覆盖。

本批不改 Claude 批次拥有的共享目录/计费函数；沿用 `canonical_payload` 接受任意 provider 与 `for_provider` 按健康的实际 provider 查 fact 的现有接口。发现原始结果附带 `billingByProvider`，当前 canonical_payload 不保留该字段；目录显示计费仍经健康投影。若 Claude 批次增加目录快照里的计费字段，假定接口是保留原生 `billingByProvider[provider] -> {kind,source,observedAt}`，没有事实仍 unknown，不调用尚不存在的接口，也不自行更改共用函数。供应方别名与地址的共同身份规则由该批/Host 决定，本批不根据 URL 或密钥创造供应方名。

## 三项顺带修改

Codex 的 NativeSchemaWorker 使用 `outcome_schema(suggested_profile=True)`，与 Claude 和 MCP schema 接受同一可空 `suggestedProfileId`；同步更新 Codex schema 固定摘要测试，其余三家的格式与提示词不变。

原生提供 `thread/tokenUsage/updated`，原来的 fast carrier 将它忽略；现在由已有 TurnEvidence/attempt_token_usage 收集该回合增量，在多轮格式纠正时相加、排除累计历史和重复观察，并在某轮缺失时标为 partial。没有用量保持 None，EOF 排干阶段的新用量也纳入；fast 角色结果把通用 usage 包投影为规范化 tokenUsage。官方文档描述此原生事件，具体 last/total 算法沿用仓库已有的 Worker 实现；没有付费检查当前真实 Codex。

角色资源测试不读 `docs/design/role-texts.md`、不允许 Worker 特殊多两行；直接守 Worker/Router/Lead 资源的角色、权限、求助和完成要点，保留实际四家提示词边界测试。设计文档转为资源索引由 Host 做。

## 验证与交接

所有命令都在检出根运行，使用 Host 指定的只读现成解释器、`PYTHONPATH=src:tests/python`，先清除 AGENTS.md 列出的十个环境变量；没有安装依赖，没有 uv，没有写只读环境。解释器路径在记录中写为 `<provided-python>`。

最终源码的可运行选择共 190 项，32.468 秒，`OK`；`git diff --check` 通过，删除文件的公开导出也逐项核对。覆盖 native_run/protocol、纯工具事件投影、角色资源/执行/schema/审阅、无进程的注册接线、健康与计费、三条发现/readiness，以及六条无需控制器的服务续接测试。源内使用离线模拟 app-server，验证不代表真实安装或 Host 接受。

可复现的选择：`env -u BUDDY_STATE_DIR -u BUDDY_RUNTIME_ROOT -u BUDDY_RUNTIME -u BUDDY_RUNTIME_IDENTITY -u BUDDY_WORKER_STATE -u BUDDY_WORKER_ID -u BUDDY_AGENT_CREDENTIAL -u BUDDY_AGENT_CREDENTIAL_FILE -u VIRTUAL_ENV -u UV_PROJECT_ENVIRONMENT PYTHONPATH=src:tests/python <provided-python> -m unittest` 后接：

- `buddy.harnesses.codex.test_native_run`、`buddy.harnesses.codex.test_codex_protocol`、`buddy.harnesses.codex.test_codex_tool_evidence.CodexToolEventProjectionTests`。
- `buddy.roles.test_role_prompts`、`buddy.roles.test_run_execution`、`buddy.roles.test_schema_worker`、`buddy.roles.test_registered_review`、`buddy.roles.test_registered_run_wiring.RegisteredDescriptionTests`、`buddy.roles.test_registered_run_wiring.NativeEvidenceProjectionTests`。
- `blackboard.service.test_harness_health`、`protocol.test_billing_quota`。
- `buddy.harnesses.codex.test_codex.CodexAdapterTests` 的 `test_capability_readiness_does_not_start_a_native_probe`、`test_complete_empty_native_catalog_is_not_a_discovery_failure`、`test_discovery_reports_user_configured_provider` 三个完整测试名。
- `buddy.harnesses.codex.test_codex_continuation.CodexContinuationTests` 的六个完整测试名：`test_invalid_outcome_can_resume_exact_completed_native_history`、`test_native_failure_reconstructs_with_previous_assistant_message`、`test_historical_private_thread_reconstructs_without_touching_native_history`、`test_harness_version_change_reconstructs_with_the_same_message`、`test_foreign_attempt_cannot_supply_resume_or_reconstructed_context`、`test_refused_resume_does_not_erase_the_last_message_before_reconstruction`。

受沙盒阻挡的测试没有改成 skip，没有绕过监听限制：角色控制器的 C-Two live endpoint 创建返回 `Operation not permitted (os error 1)`，由此出现 role-controller-failed 或 invalid-native-result，后续无法得到真实 checkpoint / 停止帧。Host 需在允许本地监听的环境运行：`buddy.harnesses.codex.test_codex` 的控制器生命周期部分、`buddy.harnesses.codex.test_no_tool_codex` 全部、`buddy.harnesses.codex.test_codex_tool_evidence.CodexReviewToolEvidenceTests`、`buddy.harnesses.codex.test_codex_continuation.CodexContinuationTests.test_controller_failure_to_workflow_continue_keeps_the_native_thread`，以及 `buddy.roles.test_registered_run_wiring` 的 FastRegisteredRunTests/WorkerRegisteredRunTests/DshPublishedReceiptTests。完整检查、真实启动器、真实 Worker/Router 回合和故障注入仍按冻结任务由 Host 做。

Host 的真实验收还应核对：用户第三方供应方可用，MCP/skill/钩子/用户指令可见且角色说明生效，普通会话写进原生目录并原生续接，原生和控制器两层停止证据真实，运行与异常结束均没有新建私有配置主目录或凭据链接；另量实际旧主目录并决定未来清理。Windows/Linux、本次真实权限回读与真实 fast 用量仍未验证。
