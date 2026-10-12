# harness 拒绝清单

| 部分 | 总数 | A | B | C | D | E | F | G | H | I |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Codex | 88 | 26 | 7 | 14 | 4 | 7 | 5 | 4 | 15 | 6 |
| Claude Code | 61 | 19 | 0 | 7 | 8 | 0 | 3 | 0 | 14 | 10 |
| DSH | 88 | 22 | 8 | 22 | 0 | 0 | 4 | 1 | 28 | 3 |
| ZCode | 90 | 33 | 0 | 16 | 4 | 0 | 5 | 0 | 21 | 11 |
| 共用部分 | 176 | 58 | 8 | 78 | 0 | 3 | 0 | 0 | 14 | 15 |
| 合计 | 503 | 158 | 23 | 137 | 16 | 10 | 17 | 5 | 92 | 45 |

结论：供应方与登录方式限制有 16 处；固定原生形状有 17 处；限制未经证实有 5 处。这 38 处分别建议删除或记成事实。另有独立账户检查 10 处建议随账户功能删除。归属、凭据、输入与真实失败共 410 处，其中 14 处输入检查建议合并；45 处交 Host 判断。

2026-10-11，依据提交 `06b4e99ff7d746f3006676176fb512df5de77a8e`。逐处表为 `docs/design/harness-refusal-inventory.tsv`。共用部分包括 `harnesses/` 顶层模块、`roles/` 与 `runtime/`。下文函数的同名实现以表里的路径和行号区分。

口径只认指定脚本的一行：503 处，不是 503 个不同原因。转包原错误、用同一函数多次拒绝、无参数的中断异常都照计；裸 `raise`、普通内置异常、返回不可用而不抛错的分支不计。`code` 原样取脚本的首个参数，所以有的单元格是消息、`<dynamic>` 或空值，不擅自改成运行时错误码。

A 留归属与证据，B 留凭据和私有路径，C 留我方输入边界，H 留真实失败。D、E 删限制或独立账户检查；F、G 记事实。I 暂不定。内部共用格式的枚举归 C；不能因为字段固定就当成原生版本白名单 F。

这里只提出处置与将来的可见形式，尚未实现。`after` 是改后应交付的事实，不表示当前结果已有这些字段。原生未知形状须保留有界、脱敏的类型与缺失事实，不能把凭据或全量用户配置写成证据；未知也不能冒充成功、零工具或已停。

先读了待办中的收束条目、ADR-023、ADR-017 第 9 至 15 条、ADR-019 决定、ADR-025、四份旧运行盘点、两份指定调研、`AGENTS.md` 与 `CONTEXT.md`。旧盘点只作阅读路线，当前路径与条件以源码为准。

## Codex

- 归属、唯一完成、续接与停止：26 处，A，留。例如 `_configure_worker` / `native-resume-unavailable`、`__call__` / `invalid-native-result`、`run_discovery` / `native-shutdown-failed`。最近回合、输入摘要与检查点不能只靠会话 ID 代替。
- 凭据源与私有目录：7 处，B，留。例如 `_pinned_home` / `PRIVATE_PATH_UNSAFE`、`prepare_coding_home` / `CODEX_CREDENTIAL_RETAINED`、`remove_coding_auth` / `PRIVATE_PATH_UNSAFE`。清理只能指向持有的私有目录。
- 我方参数与观察器反馈：14 处，C；10 处留，4 处合并。例如 `_prepare` / `no-tool-policy-unverified` 实际查的是 1 至 60 秒时限，`run` / `INVALID_ARGUMENT` 查回合输入，`_take_feedback` / `INVALID_ARGUMENT` 查反馈。重复时限归 `start_fast`，上一会话归 `RunContinuation._native_resume_names_its_session`，回合身份与摘要归 `WorkerRunExecutor.prepare`，工具范围归 `RunRequest.tool_scope`。
- 固定供应方与账户类型：4 处，D，删。例如 `_initialize` / `account-plan-required`、`_check_thread_receipt` / `wrong-native-provider`、`check_preparation` / `INVALID_ARGUMENT`。改后原生供应方和账户类型进入模型目录或运行事实；真实登录失败由 harness 返回，Host 看本次失败。
- 独立账户选择与禁止回退：7 处，E，随独立账户删除。例如 `frozen_account` / `INVALID_ARGUMENT`、`credential_source` / `CODEX_ACCOUNT_UNAVAILABLE`、`prepare_coding_home` / `CODEX_ACCOUNT_UNAVAILABLE`。其中只针对独立来源的链接检查守的是账户隔离；通用私有目标检查另归 B。
- 原生目录、帧与对话项清单：5 处，F，改事实。例如 `_catalog` / `invalid-catalog`、`Connection.call` / `invalid-protocol`、`_structured_rounds` / `no-tool-violation`。改后模型目录记读取不完整，运行结果记未知形状或工具项，Router 判断是否采用；会话与回合归属仍留 A。
- 只读或无工具限制未证实：4 处，G，改事实。例如 `prepare_no_tool_home` / `no-tool-policy-unverified`、`_configure_review` / `readonly-policy-unverified`、`_configure_fast` / `no-tool-policy-unverified`。改后记录缓存缺失、实际权限与配置回读、未证实项；角色判断能否用，Host 看到边界。
- 命令不可用、断管道、取消、超时与原生错误：15 处，H，留。例如 `cli_command` / `<dynamic>`、`_remaining` / `deadline`、`Connection.call` / `native-rpc-error`。这是发生了什么，不是新增资格关卡。
- 混合条件与数量预算：6 处，I，逐处列在末节。

带工具 Worker：能沿用文件登录，但读不到用户全局供应方配置。`home.credential_source` 选冻结的本机或独立来源，`prepare_coding_home` 只把源 `auth.json` 链进微任务私有 `codex-home`，不复制源 `config.toml`。`native_run._prepare` 保留原 `HOME`，替换 `CODEX_HOME` 与 `CODEX_SQLITE_HOME`，再用命令参数固定 SQLite 位置和文件凭据库。检出仍是原生 cwd；这里没有全面禁止原生读取检出配置，但它不能代替被隔开的全局设置。`_initialize` 与 `_configure_worker` 仍写死 `openai`，本机来源只接受 ChatGPT，独立来源额外接受 API key。

无工具 Router：`config.prepare_no_tool_home` 从选定的原生主目录读 `models_cache.json`，只复制所选模型并改掉工具字段，链接源 `auth.json`，自写无工具 `config.toml`；原生 `HOME` 沿用，`CODEX_HOME` 换成调用私有目录。用户的登录与所选缓存可到达；源供应方、MCP 和其他配置没有复制。`_configure_fast` 还要求私有用户层唯一、无外来设置层，并核对空历史与权限。原生 API key 或其他供应方仍受 `_initialize` 限制。

只读审阅：`native_run._review_home` 沿用 `HOME`，替换 `CODEX_HOME`，只链接源 `auth.json`，用 `config.read_only_config` 自写只读禁网配置。用户全局供应方与 MCP 设置没有复制；`_configure_review` 还回读权限、配置层与 MCP。用户文件登录能到达，但仍受相同账户类型和供应方关卡。三条路径都经 `discovery.native_environment`，只在 shell 导出的 API key 不会传入。

## Claude Code

- 会话、控制请求、后台工作与流结束：19 处，A，留。例如 `_check_terminal` / `wrong-native-session`、`replay_early` / `native-turn-started-early`、`Connection._deliver` / `invalid-protocol`。后台任务追踪超限也归 A；漏掉活任务会使结束证据失真。
- 我方调用与续接能力参数：7 处，C；6 处留，1 处合并。例如 `_notify` / `INVALID_ARGUMENT`、`run` / `INVALID_ARGUMENT`、`Connection.send` / `invalid-protocol`。`native_run._prepare` 的原生续接能力检查与 `WorkerRunExecutor.prepare` 重复，合并到请求边界。
- 第一方账户、第三方变量与设置来源：8 处，D，删。例如 `_catalog` / `first-party-auth-required`、`_prepare` / `third-party-provider`、`check_preparation` / `ADAPTER_UNAVAILABLE`。改后让 Claude 读取用户供应方设置；模型目录与运行结果分别显示原生账户、供应方或未知，以及真实认证失败，交给 Host。
- 原生返回形状：3 处，F，改事实。例如 `_catalog` / `invalid-catalog`、`Connection._deliver` / `invalid-protocol`、`Connection.call` / `invalid-protocol`。改后保留缺失模型列表、事件类型、答复形状的有界事实，目录或结果显示未知与不完整，角色判断。
- 原生不可用、取消、超时与明确拒绝：14 处，H，留。例如 `cli_command` / `<dynamic>`、`_remaining` / `deadline`、`Connection.call` / `native-rpc-error`。两处 `QuotaRejected` 也是真实供应方失败。
- 能力缺失、资源上限与混合检查：10 处，I，逐处列在末节。没有单独归 B、E、G 的抛错点，不表示共用层没有凭据边界。

带工具 Worker：本机登录可达，用户设置里的供应方配置不可达。`runtime.worker._execute_selected` 先用 `accounts.execution_environment` 冻结来源；本机来源沿用 `HOME` 与 `CLAUDE_CONFIG_DIR`，独立来源经 `account_integrations._environment` 替换配置目录。`native_run._prepare` 在运行私有根自写 `settings.json`，内容仅是 `config.sandbox_settings`；`execution_args` 指定 `--setting-sources ""`、空 MCP 配置、safe-mode 与 restricted，挡住用户全局、项目设置及其中的 `env`。原生 `HOME` 没换成回合私有目录。`config.native_environment` 留登录目录与用户名，去掉继承 API key 与供应方覆盖；独立来源的密钥只在真实原生子进程中由 `inject_claude_key` 注入。

无工具 Router：当前没有进程，也没有配置继承路径。`ClaudeAdapter.no_tool_structured=False`，`prepare_router_fast` 会拒绝；直接调用运行模块也被 `_prepare` 的 `tool_scope == "none"` 拒绝。它是未实现，不能写成仅缺一次验证。

只读审阅：`start_review` 经 `_account_environment` 冻结本机或独立来源，`_launch` 给控制器使用 `read_only=True` 并去掉黑板 agent 凭据。原生仍走同一 `_prepare`、`native_environment`、`execution_args`；用户登录可达，供应方设置仍被空设置来源挡住。工具换为读搜索工具；`review_request_controls` 清空网络域名，另拒绝 MCP、联网和子代理工具。私有 `settings.json` 不是用户设置的副本。

## DSH

- 会话、回执、工具顺序与结算：22 处，A，留。例如 `run` / `native-resume-unavailable`、`RootTurnEvidence.provenance` / `invalid-provenance`、`_close_session` / `session-close-unconfirmed`。更新队列丢帧时也不能宣称证据完整。
- 私有启动、主目录与日志路径：8 处，B，留。例如 `ensure_private_log_path` / `<dynamic>`、`child_environment` / `extra environment may not override the reserved private-home keys`、`launch` / `<dynamic>`。防止原生状态或日志落到日常目录，也防止链接改变写入目标。
- 我方 MCP、工具范围、续接与观察器输入：22 处，C；17 处留，5 处合并。例如 `_checked_mcp_servers` / `ACP_MCP_SERVER`、`tool_scope_launch` / `invalid-tool-scope`、`_continuation_of` / `native-resume-unavailable`。两处绝对 cwd 合并到 `RunRequest.cwd`，工具范围合并到 `RunRequest.tool_scope`，两处上一会话参数合并到 `RunContinuation`。
- 原生握手、配置选项和结束原因形状：4 处，F，改事实。例如 `initialize` / `ACP_PROTOCOL_FAULT`、`_configure` / `configuration-unavailable`、`_settle_stop_reason` / `invalid-protocol`。改后握手与运行结果记形状未识别、配置或结束原因未知，模型目录记读取不完整；不能把缺失结束原因当成功。
- 无工具清单未证实：1 处，G，改事实，即 `tool_scope_launch` / `scope-configuration-unconfirmed`。改后结果记录工具行清单未证实和实际补丁，角色按原生工具事件与能力事实判断。
- 管道写入、请求超时、断连、取消与原生拒绝：28 处，H，留。例如 `AcpConnection.request` / `<dynamic>`、`_native_call` / `native-disconnected`、`_prompt_and_observe` / `deadline`。原生目录确实找不到请求的模型也属于本次真实不可用。
- 配置回读差异与最终文本上限：3 处，I，逐处列在末节。没有 D、E 抛错点；不等于用户配置的每一层都到达原生。

带工具 Worker：日常 DSH 的供应方与文件登录可达，但用户自定义 profile 不会原样进入。`native_run._launch_agent` 调 `source_home`、`source_binding_rows`，把原生 `settings.yaml` 与 `.credentials.yaml` 的路径写进启动补丁，内容由 DSH 自己读取，项目不复制这两份文件。`run` 建本次调用私有 `DSH_HOME`，`materialize_acp_profile` 自建 ACP profile；`acp.launch.child_environment` 保留 `HOME`，强制私有 `DSH_HOME`。会话记录另固定在微任务私有根；工具范围补丁、关闭标题生成与遥测的补丁覆盖相应设置。

无工具 Router：同样经 `_launch_agent` 绑定两份原生文件，供应方与文件登录可达；调用自己的私有 `DSH_HOME`、会话目录和 ACP profile 替代用户 profile。`tool_scope_launch("none")` 追加关闭工具的清单与公开范围标记，不阻止 DSH 读绑定的供应方和凭据文件。调用 cwd 是角色准备的空私有目录。

只读审阅：`DshAdapter.read_only_structured=False`，当前角色入口不启动 DSH。`tool_scope_launch("read")` 虽可映射原生只读预设，不能据此宣称审阅已接通。另有共同限制：`source_home` 能识别 `DSH_HOME`，但受管控制器的 `controller_environment` 经白名单不传入用户 `DSH_HOME`，实际默认从继承 `HOME` 找日常 DSH 目录；仅靠该环境变量指定的另一主目录到不了这条链。只在 shell 导出的供应方密钥也不传入。

## ZCode

- 根会话、递增事件、输入准入、签名回执与关闭：33 处，A，留。例如 `_checked_root_session` / `wrong-native-session`、`RootTurnEvidence.observe` / `invalid-provenance`、`_close_root_session` / `session-close-unconfirmed`。完成工具必须有唯一调用、完整结果与本次签名回执。
- 我方配置、续接、服务工具与观察器输入：16 处，C；12 处留，4 处合并。例如 `configure_session` / `invalid-configuration`、`_continuation_of` / `invalid-resume-mode`、`run` / `INVALID_ARGUMENT`。配置完整性合并到 `RunConfiguration`，三个上一会话和模式条件合并到 `RunContinuation`。
- 固定设置来源与密钥类登录：4 处，D，删。例如 `provider_paths` / `adapter-unavailable`、`configure_session` / `unsupported-provider`、`NativeConnection._pump` / `unsupported-provider`。改后原生设置来源、供应方与认证能力进入模型目录或交互事实；真实认证失败到达角色与 Host。缺少认证宿主仍会失败，删除限制并不补齐它。
- 选择对象、通知载荷与 RPC 返回形状：5 处，F，改事实。例如 `selected` / `configuration-unavailable`、`NoToolProtocol.observe` / `invalid-protocol`、`NativeConnection.call` / `invalid-protocol`。改后请求与实际选择分开，未知载荷与缺失字段记事实；身份与完成回执需要的字段仍在 A 边界核对。
- 不可用命令、取消、超时、断连与明确原生失败：21 处，H，留。例如 `cli_command` / `adapter-unavailable`、`_pump` / `native-disconnected`、`RootTurnEvidence.observe` / `native-turn-failed`。目录确实没有所选供应方与模型时也报告本次不可用。
- 来源读错、复合终态与资源上限：11 处，I，逐处列在末节。没有单独归 B、E、G 的抛错点；私有路径仍经共用边界保护。

带工具 Worker：用户密钥类供应方文件可达，原生 OAuth 登录不可按现有通道使用。`config.provider_paths` 从选定可执行文件旁找内建配置，从 `ZCODE_DATA_BASE_DIR` 或原生用户目录找个人配置，也接受控制器白名单保留的两个显式配置文件变量。`snapshot_provider_files` 把这两份文件完整复制为私有快照，原生归一化只改快照，不复制其他用户存储或 OAuth store。`native_run._spawn_app_server` 沿用 `HOME` 与 `ZCODE_DATA_BASE_DIR`，覆盖供应方文件路径、会话数据库、存储和日志目录，并关闭遥测。`configure_session` 只接受 `api-key` 与 `zhipu-coding-plan-api-key`；`NativeConnection._pump` 还拒绝供应方认证头的 OAuth 宿主请求。

无工具 Router：同一 `_spawn_app_server` 与快照链，供应方文件和密钥可达，OAuth store 不复制且访问类型仍受限。调用使用自己的私有会话数据库和存储，cwd 是空私有目录；无工具会话不挂完成服务。`NativeConnection` 的无工具交互分支拒绝原生交互，没有替 OAuth 供应方提供认证宿主。只在 shell 导出的密钥不传入。

只读审阅：`ZcodeAdapter.read_only_structured=False`，角色层不会启动审阅进程，因而没有可交付的配置继承路径。原生 `plan` 等模式是否足以接通，不在本次源码盘点里下结论。

## 共用部分

- 回执、来源、问询、冻结输入与停止：58 处，A，留。例如 `verify_receipt` / `invalid-finish`、`record_answer` / `conflicting-inquiry-answer`、`_read_request` / `INVALID_ARGUMENT`。签名格式是本项目证据格式，属于 A，不是厂商版本白名单 F。
- 凭据与私有路径：8 处，B，留。例如 `account_native.descend` / `PRIVATE_PATH_UNSAFE`、`guard_private_path` / `PRIVATE_PATH_UNSAFE`、`_private_regular_bytes` / `INVALID_ARGUMENT`。错误码不决定类别；它们防止链接、特殊文件或失控的私有读取。
- 我方请求、登记与内部事实格式：78 处，C，留。例如 `register_run_seam` / `INVALID_ARGUMENT`、`_validated_tool_evidence` / `a tool evidence event category is not an ACP category`、`WorkerRunExecutor.prepare` / `INVALID_ARGUMENT`。调用方是本项目的角色、运行模块或另一进程；这些不是固定厂商原生形状。
- 独立账户冻结与注入：3 处，E，随独立账户删除。例如 `inject_claude_key` / `ACCOUNT_LOGIN_REQUIRED`、`inject_claude_key` / `ACCOUNT_BINDING_UNAVAILABLE`、`_account_environment` / `INVALID_ARGUMENT`。账户代码里保护私有路径、真实停止或失败的其他抛错按实际作用归 B、A、H；这些原则仍要留在以后实际存在的操作里，不要求为已删除入口保留死代码。
- 命令、原生账户操作与实时连接真实失败：14 处，H，留。例如 `command_for` / `<dynamic>`、`CTwoLiveChannel.capabilities` / `LIVE_UNAVAILABLE`、`CommandAdapter.prepare` / `ADAPTER_UNAVAILABLE`。实时断连不证明原生进程停了。
- 未实现能力、动态包装与复合条件：15 处，I，逐处列在末节。共用部分没有单独归 D、F、G 的抛错点；`prepare_router_review` 会聚合多个资格原因，不能整处改成 G。

重复检查只合并同一个输入条件。原生回读、持有句柄的核对、签名验证与模型输出验证是不同边界，不能因为字段名字相同就删掉第二次。14 处 `merge` 已在 TSV 指明归并边界；Codex 的回合摘要须在归并后边界补齐，不能只删下层。

## 最费判断的五处

- `harnesses/codex/native_run.py:424` / `_prepare` 归 C：错误码说未证实，条件却只查我方给的时限，且与 `start_fast` 重复。
- `harnesses/codex/native_run.py:1231` / `_structured_rounds` 归 F：固定三种对话项把未来的非工具项也当工具违规，改后未知项必须记事实。
- `harnesses/claude/protocol.py:436` / `observe` 归 A：追踪超限后漏掉后台任务会破坏结束证据，不能仅凭一个数字上限归 F。
- `harnesses/zcode/protocol.py:703` / `_pump` 归 D：它按 OAuth 登录方式拒绝；删除后还缺认证宿主，必须报告真实能力缺失。
- `harnesses/run_contract.py:447` / `_validated_tool_evidence` 归 C：检查的是我方已归一的 ACP 类别，不是原生事件种类清单。

## 拿不准的

以下 45 处均为 I / `undecided`。路径相对 `src/hey_my_buddy/buddy/`，每一项对应 TSV 的一行；需要 Host 决定，复合条件应先拆开再处置。

- `harnesses/account_native.py:152` / `initialize` / `<dynamic>`：账户握手失败按实际停止与否分报登录失败或停止未确认，同一处同时承载 H 与 A。
- `harnesses/account_native.py:179` / `start_login` / `ACCOUNT_LOGIN_FAILED`：原生 OAuth 答复同时限制登录 ID、网址大小、HTTPS 与固定域名，凭据安全和供应方限制混在一起。
- `harnesses/base.py:202` / `discover_models` / `CATALOG_UNAVAILABLE`：基类实际没有模型发现实现，属于能力缺失而非调用参数错误或未经证实。
- `harnesses/c_two_live.py:488` / `start` / `PRIVATE_PATH_UNSAFE`：C-Two 注册抛出的 ValueError 或 RuntimeError 一律包成私有路径错误，守的是哪一项要看底层原因。
- `harnesses/claude/native_run.py:155` / `_catalog` / `invalid-catalog`：原生模型列表不得超过 200 项；这是资源上限，是否保留或截断并记不完整尚未定。
- `harnesses/claude/native_run.py:279` / `_prepare` / `INVALID_ARGUMENT`：无工具运行能力实际未实现；这不是未经证实，是否继续拒绝或新增原生能力不在已定处置里。
- `harnesses/claude/native_run.py:405` / `_check_catalog` / `invalid-configuration`：请求供应方必须为 anthropic 且模型与推理强度必须在本次目录；一处混合 D 与即时目录资格检查，需 Host 决定拆分。
- `harnesses/claude/native_run.py:450` / `buffer` / `invalid-protocol`：初始化前暂存帧不得超过 128 条；这是资源上限，不能仅因固定数值归为形状白名单。
- `harnesses/claude/native_run.py:457` / `on_request` / `native-request-limit`：保留的原生权限请求不得超过 32 条；上限兼顾资源与拒绝证据，超限怎样保留完整性需 Host 决定。
- `harnesses/claude/native_run.py:609` / `_check_terminal` / `native-turn-failed`：非 success 或 is_error 非 false 都算失败；同一处混合真实回合失败与未知终态形状，不能一律归 H 或 F。
- `harnesses/claude/native_run.py:1075` / `run_discovery` / `<dynamic>`：启动阶段任何异常都附实际停止事实后转抛；原异常可属不可用、停止证据或其他错误，包装点无法单归一类。
- `harnesses/claude/native_run.py:1091` / `run_discovery` / `<dynamic>`：初始化和目录读取的多种异常附实际停止事实后转抛；底层包含 D、F、A、H，需按原因拆分归类。
- `harnesses/claude/protocol.py:210` / `call` / `invalid-protocol`：未结清控制请求不得超过 16 个；这是并发资源上限，其处置不在已定九类的明确规则中。
- `harnesses/claude/protocol.py:214` / `call` / `invalid-protocol`：控制请求累计编号不得超过固定预算；这是循环与资源保护，需 Host 定上限处置。
- `harnesses/codex/native_run.py:405` / `_coding_home` / `<dynamic>`：把 prepare_coding_home 的各种错误重新包装，实际检查混合凭据安全与独立账户限制。
- `harnesses/codex/native_run.py:514` / `_catalog` / `invalid-catalog`：原生模型目录累计超过 200 项即拒绝；这是资源数量上限，不只是形状检查。
- `harnesses/codex/native_run.py:519` / `_catalog` / `invalid-catalog`：下一页游标必须是非空字符串且不能重现，混合形状限制与不前进的分页保护。
- `harnesses/codex/native_run.py:756` / `_configure_review` / `readonly-configuration-mismatch`：同一条件同时检查实际模型、固定 openai 供应方及获配检出，混合配置事实、供应方限制与归属。
- `harnesses/codex/native_run.py:805` / `_configure_fast` / `no-tool-policy-unverified`：无工具会话回执同时要求会话与检出归属、空历史、固定模型供应方、禁审批与空环境。
- `harnesses/codex/protocol.py:114` / `_pump_one` / `invalid-protocol`：原生响应 id 必须为整数且暂存响应少于 16 条，混合关联身份与积压预算。
- `harnesses/dsh/native_run.py:835` / `_configure` / `configuration-mismatch`：原生回读模型与请求不同；这是配置一致性要求，是否属于必须硬拒绝的归属证据待 Host 决定。
- `harnesses/dsh/native_run.py:841` / `_configure` / `configuration-mismatch`：原生回读推理强度与请求不同；是否必须拒绝而不能并列记录请求与实际值待 Host 决定。
- `harnesses/dsh/native_run.py:1383` / `chunk_fold` / `answer-too-large`：原生最终文本不得超过 65536 字节；资源上限是否继续硬拒绝而非截断留事实待 Host 决定。
- `harnesses/zcode/config.py:67` / `provider_access_types` / `invalid-provider-config`：同一处把文件读取失败与固定供应方结构解析失败合并，真实失败和形状白名单需拆开。
- `harnesses/zcode/native_run.py:134` / `configure_session` / `configuration-mismatch`：模型或推理强度回读与请求不符；配置事实是否必须作为硬拒绝条件待 Host 决定。
- `harnesses/zcode/native_run.py:308` / `check_preparation` / `ADAPTER_UNAVAILABLE`：准备阶段把命令不可用与供应方文件读取或形状失败合为一处拒绝，拆分后才能定留删。
- `harnesses/zcode/native_run.py:506` / `observe_metadata` / `invalid-protocol`：一处同时检查元数据种类白名单、会话归属与单调序号，不能整体删或改事实。
- `harnesses/zcode/native_run.py:558` / `observe` / `native-turn-failed`：同一条件同时拒绝重复完成、输入不匹配与非成功结果，证据保护和真实失败需拆开。
- `harnesses/zcode/native_run.py:561` / `observe` / `invalid-native-result`：最终答案须为字符串且不超过 65536 字节；形状与资源上限混合，是否改成截断事实待 Host 决定。
- `harnesses/zcode/native_run.py:755` / `_drain` / `no-tool-violation`：结算之后任何原生交互请求都导致排干失败；是否仅记交互事实交角色判断待 Host 决定。
- `harnesses/zcode/native_run.py:846` / `_admit_root_input` / `native-admission-failed`：准入检查同时要求原生 accepted 为真和会话标识匹配，真实拒绝与归属证据需拆开。
- `harnesses/zcode/native_run.py:1618` / `run_discovery` / `native-shutdown-failed`：目录读取同时要求原生退出码为零与进程组停止确认，真实失败与停止证据需拆开。
- `harnesses/zcode/protocol.py:728` / `_pump` / `invalid-protocol`：未领用原生响应数达到 16 就拒绝；这是累积数量上限而非已证明串回合，是否保留待 Host 决定。
- `harnesses/zcode/protocol.py:846` / `observe` / `native-turn-failed`：完成事件同时拒绝重复终态、输入不匹配和非成功结果，证据保护与真实失败需拆开。
- `roles/controller.py:166` / `start_router_preparation` / `router-review-unsupported`：审阅入口实际没有实现，不能当成仅缺验证的 G；要先决定是否新增能力。
- `roles/router.py:33` / `prepare_router_fast` / `router-no-tool-unsupported`：harness 实际没有无工具结构化入口，是能力缺失而非未经证实。
- `roles/router.py:54` / `prepare_router_review` / `router-review-unsupported`：本地审阅资格函数同时含未实现、能力限制与本机准备失败，动态原因不能归为一种拒绝。
- `roles/router_input.py:95` / `prepare` / `router-input-unavailable`：一处同时拒绝越界路径、Git 元数据和非 blob 条目，安全边界与不支持子模块混在一起。
- `roles/router_input.py:111` / `prepare` / `router-input-unavailable`：拒绝审阅副本里的链接指向副本之外，守住只读输入边界但不属于凭据或回合来源三类。
- `roles/run_execution.py:260` / `worker_delivery` / `INVALID_ARGUMENT`：同一处合并结果 schema、角色六字段与来源对象校验，哪些属 A 哪些属角色语义须拆开决定。
- `roles/run_execution.py:607` / `collect` / `<dynamic>`：这一处转报上游 invalid-result 或 invalid-role-result，可能是角色结果语义失败或证据失败。
- `roles/run_execution.py:706` / `discover_models` / `CATALOG_UNAVAILABLE`：登记模块实际没有模型发现操作，属于缺失能力。
- `roles/run_execution.py:737` / `discover_models` / `ADAPTER_UNAVAILABLE`：同一处把未收据、非零退出、原生失败和停止未确认合成不可用，A 与 H 混在一起。
- `roles/schema_worker.py:116` / `delivery` / `invalid-result`：模型最终值不符合本项目 Worker 结果合约，与原生版本形状或执行身份并非同一问题。
- `runtime/live.py:96` / `start` / `PRIVATE_PATH_UNSAFE`：C-Two 注册的 ValueError 或 RuntimeError 全部转包成私有路径错误，具体原因未在这处区分。

已定处置在这些位置仍有风险：

- `codex/native_run.py:_configure_review` 的 G 改事实后，放大的文件授权或失效禁网可能已经造成访问；角色拒收不能撤回访问，必须显示实际权限与未证实项。
- `codex/native_run.py:_structured_rounds` 的 F 改事实后，未知对话项不能默认为零工具；未知与完整性必须独立记录。
- `claude/config.py:execution_args` 放开设置来源时，用户 hooks、工具与 MCP 也可能进入；供应方设置的可达性与 Host 工具范围须分别落实。
- `claude/native_run.py:_catalog` 删除第一方关卡时，写死 `anthropic` 或已确认账户会制造假事实；须按原生回读或未知填写。
- `dsh/native_run.py:tool_scope_launch` 的 G 改事实后，缺少工具行清单时不能宣称限制已生效，也不能在工具事件未完整时宣称零工具。
- `dsh/native_run.py:source_home` 的自定义主目录变量在受管上游已丢失；即使删完拒绝，也不代表能沿用用户另一个 DSH 主目录。
- `zcode/protocol.py:_pump` 删除 OAuth 限制后还没有认证宿主，必须把未满足的原生请求交角色或 Host，不能假装认证成功。
- 所有 F 的未知形状事实都要有界且脱敏；不能借事实记录公开 ZCode 快照、Codex 登录文件或 Claude 密钥。
- 原生目录缺少请求模型的 H 处置与模型目录的 pending 语义要再衔接；一次读取不完整不能被当成确认模型已不存在。

阅读与验证边界：本次逐处读了 503 个条件及配置构造的调用链，没有未读的拒绝行。各平台与原生版本的实际效果、用户文件存在与登录状态、harness 内部最终如何读这些路径都没有运行验证；本机配置和凭据未打开。源码里的返回值拒绝、警告、静默丢弃不属于脚本行，不能据这张表声称已覆盖全部行为；例如 Claude 推理强度未知值只产生目录遗漏警告。

交付核对：TSV 为 504 行（表头 1 行、拒绝 503 行）；对给定脚本重跑的路径、行号、函数、首参数逐行比较，顺序相同，无缺失、无重复、无额外行。类别总数与总表一致；D、F、G 的 38 行都有 `after`，其余为空；Markdown 为 174 行。只写这两份指定文件，未跑测试或完整检查，未运行 harness、服务或 Worker。Host 的抽样验收尚未发生。

建议提交信息：`docs: inventory and classify buddy-side harness refusal sites`。本次不提交；到此停下，等 Host 验收。

## Host 对待定 45 处的处置

2026-10-11，整合 Host 逐条读过“拿不准的”45 处。用户问能不能整体删掉：不能，其中没有一处是早期遗留；它们按下面的规则了结，不需要逐条再定。逐处的表不改，这里的规则在收束那一批开工时落到每一处。

| 情形 | 约 | 处置 |
| --- | ---: | --- |
| 一个条件里混着两件事：一半守归属、停止证据或真实失败，另一半是供应方限制或固定形状 | 26 | 拆开；前一半留，后一半按上文已定的类别删除或改成记为事实 |
| 对别的进程送来的内容设的大小与数量上限 | 8 | 留，这是边界上的校验 |
| 如实说明没有这项能力 | 5 | 留 |
| harness 读回的模型或推理强度与请求的不同就拒绝 | 3 | 留：四段名字是评价的单位，跑的不是这个 buddy 就不能记在它名下 |
| 结算之后又收到原生的交互请求就判失败 | 1 | 改成记为事实 |
| 审阅副本里的链接指向副本之外；Worker 的结果不合本项目的约定 | 2 | 留 |

同一天的一个实例说明“留”也有要改的地方：一个微任务的回合做完了工作，却因为最终结果里的两个摘要数组超过条数或长度的上限，整个回合被判为失败，只留下部分产出。上限本身该留，超限之后的处理不该是让整个回合失败；这一处随收束那一批一并改，待办里另有记录。

## 随 ADR-030 的改判（2026-10-12）

[ADR-030](../decisions/030-harness-layer-scope.md) 接受之后，Host 按它重读了提到账户、私有主目录与供应方的各行，改了 17 行，理由与归到哪一批见[执行计划](adr030-execution-plan.md)末尾。改判之后 `action` 一栏的合计是：保留 383、删除 42、待定 42、记成事实 22、合并 14。上文各表是改判之前的数。

## 批次 4 开工之后的改判（2026-10-12）

做到各片时，Host 对照下游依赖每一处检查的代码，改了下面几处的处置。逐处的表不改。

- `codex/native_run.py` 的 `_configure_review` / `readonly-policy-unverified`（线程回执）：原判整个记成事实。改为拆开：回执没有确认只读沙盒的照旧拒绝，其余三项记成事实。服务事后判定 Router 的审阅调用时，允许执行命令这一类工具的前提是系统沙盒，而 Codex 读文件只能靠执行命令；这一项守的是“这一次运行有没有这项能力”。
- `roles/run_observers.py` 的 `FastCorrection.observer`：快速调用里出现未知事件就以 `invalid-protocol` 中止。它不是一处抛错，所以清单没有收；它与 F 类是同一回事，改成不中止，未知事件记成事实，答案采用与否仍按工具事件判定。2026-10-12 在真实的 Codex 0.162.1 上复现了它的后果：一条名单之外的 `account/updated` 使每一次无工具调用在回合开始之前被中止。
- `codex/native_run.py` 的 `_FastProtocol` / `invalid-native-result`（无工具流里没有绑定的事件）：原判留着，理由是归属。改为拆开：事件带着别的会话或别的回合的标识，照旧拒绝；事件没有带任何会话标识，不能证明它属于别处，记成事实并继续。随之不再需要那份固定的三种连接级通知的清单。
