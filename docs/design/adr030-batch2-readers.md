# ADR-030 批次 2：独立账户的读者清单

本清单读取受管检出的 `1140dd13` 源码，日期为 2026-10-12；它是第一回合的调查交付，不是实现或验收记录。本回合只新增本文件，没有修改源码、测试、skill、README 或其他文档，没有运行服务、Worker、账户登录或模型调用，也没有读取日常看板或真实钥匙串。下文“现状”来自源码及仓库夹具，“建议”等待 Host 审阅后才实施。

依据是 ADR-030 第 4 条及“影响”、执行计划的现状／贯穿各批／批次 2／清单改判、ADR-019、AGENTS.md 的存储不变量及 ADR-021 L18 升级设计；现源码优先于历史设计。路径以仓库根为基准；表中 `catalog/`、`service/`、`store/`、`routing/`、`evaluation/`、`tasks/` 均指 `src/hey_my_buddy/blackboard/` 下对应目录。

## 1. 四类指定记录与额外的操作记录

`accounts.identity()` 只取 `{source, credentialRevision}`；`source` 是 `native` 或 `worker`，`credentialRevision` 是黑板维护的代次，不是原生账户 ID。`selection()` 再加上 `adapter` 与选择用的 CAS `revision`；切换来源增加选择代次，完成凭据变更增加凭据代次，二者不能互换。

| meta 键 | 写入者与删除者 | 直接读者 | 读来做什么、后续建议 |
| --- | --- | --- | --- |
| `account-selection:<adapter>` | `catalog/accounts.py:Accounts.set()` 在来源改变时写 `{source,revision}`；默认 native/0 不落键 | `selection()`，经它到达下节列出的所有选择读者 | 决定后续调用用本机还是独立来源，检查选择 CAS，隔开 A→B→A 的目录重读窗口；整个机制删除，升级清存量 |
| `account-credential:[adapter,source]` | `Accounts.credential_change_stopped()` 写 `revision+1`，成功／后来的真实停止结清都会走这里 | `selection()`、`assert_credentials_current()`、`Accounts.credential_change()` | 冻结／比较凭据代次，阻止启动时换成别的凭据并隔离额度事实；删除并在升级清存量 |
| `account-mutation:[adapter,source]` | `Accounts.credential_change()` 预留 active，异常改 uncertain；`account_operations.py:_reserve()` 补 loginId/expiresAt/kind；`credential_change_stopped()` 删除 | `assert_credentials_current()`、`view()`、`credential_change()`、`credential_change_stopped()`、`AccountOperations._reserve()`；`install/upgrade.py:idle_snapshot()` 直接查前缀 | 变更凭据期间禁止使用／再次变更，呈现待登录／停止未确认，阻止升级；整个机制删除，升级清存量，不能以删记录充当进程停止证据 |
| `attempt-account:<attemptId>` | `freeze()` 在 `store/store.py:BoardStore._claim_in_transaction()` 的 claim 事务中写 | `attempt_account()`，经它到达 `_decorate()`、`native_observations.persist()`、`WorkflowCoordinator.begin_turn()`、`BoardService.harness_prepare()`、`Accounts.credential_change()` | 把账户冻结到执行，回执写回旧额度桶，续接比较账户，防止更换仍在用的凭据；删除字段与读写，保留 attempt 本身的配置／身份／结果归属 |

还有第五类 `account-operation:<uuid>`，执行计划的“删除四类”未列它：`accounts.native_operation()` 在原生目录读取／健康检查／Codex 账户查询前写 active，只有调用者提供 `shutdownConfirmed=True` 才删，否则写 uncertain；`Accounts.native_operation_stopped()` 是可信持有者的后续了结入口；`Accounts.credential_change()` 读它来保留凭据；`BoardStore.reconcile_startup()` 把前一服务的 active 改 uncertain；`upgrade.idle_snapshot()` 拒绝 active，放过 uncertain。建议一并删除运行时读写及升级存量，但保留原生进程的句柄归属、实际停止回执与 attempt 未确认状态。

测试与固定基点生成器也写上述记录：`packaging/generate-upgrade-fixture.py` 调用基点版本的账户接口；当前测试用公开账户操作或私有合成 SQL 布置。它们不是新的生产写入者；基点夹具的原始事实不应被手改成“从来没有账户”。

## 2. accounts.py 与 account_operations.py 的逐项去向

### accounts.py

以下覆盖模块内每个函数、类与类方法；嵌套的 `secure()` 也单列。测试替身不构成必须保留生产 API 的理由。

| 符号 | 当前调用方 | 建议去向 |
| --- | --- | --- |
| `_adapter()` | `key/selection/using_provider/execution_environment/secure_account_root`、`Accounts.set/credential_change/credential_change_stopped` | 随账户模块删除；harness 边界已有自己的名称校验，不把它移到普通命令读取路径 |
| `_source()` | `identity()`、`Accounts.set/credential_change/credential_change_stopped` | 删除 native/worker 选择校验 |
| `identity()` | 本模块 `key/native_operation/execution_environment/view/_invalidate`；`AccountOperations._publish`；`catalog.py:catalog_account_matches`；`catalog_store.current/record`；`native_observations.latest_quota/record_quota`；`quota_routing._account`；`router_boundary_data._cached_available`；`DecisionCoordinator._create/_frozen_circumstances_problem/claim/_account_binding_changed`；`read_health`、`HarnessHealth.invalidate/refresh/_codex_account_read`；`BoardService.harness_prepare`；claim 事务；`WorkflowCoordinator.begin_turn` | 删除本地来源／凭据桶身份，不能改名后冒充实际登录身份；调用处改用各自已有的执行配置、harness 观察与代次 |
| `key()` | `HarnessHealth._codex_account_read()` | 删除账户桶地址，主动查询限频归 harness |
| `_read()` | 本模块 selection/attempt_account/view/凭据变更；`AccountOperations._reserve()` | 随账户记录删除，不保留通用 meta 兼容读取 |
| `_write()` | 本模块 freeze/native_operation/来源与凭据变更；`AccountOperations._reserve()` | 随账户记录删除 |
| `selection()` | 本模块 binding_key/freeze/view/Accounts；`AccountOperations._reserve/_publish`；`catalog_account_matches`、`catalog_store.current/record`；`native_observations.latest_quota/record_quota`、`quota_routing._account`；上述 Router 边界与 DecisionCoordinator；`HarnessHealth._codex_account_read`；`BoardService.harness_prepare`；`BoardStore._admission_blocker` 与 claim 事务 | 整删；所有读取直接使用用户 harness，不另造固定 native/0 账户选择 |
| `binding_key()` | `catalog.py:_binding_key()`，经它到目录重读协调；`HarnessHealth._binding_is_current()` | 删除选择 epoch；目录单飞、180 秒窗口与健康代次守门仍归目录／健康模块，以 harness 为作用域 |
| `attempt_account()` | `Accounts.credential_change()`；`BoardStore._decorate()`；`native_observations.persist()`；`WorkflowCoordinator.begin_turn()`；`BoardService.harness_prepare()` | 整删；claim／selectedAttempt／turn.context 不再携带独立账户对象，回执仍绑定原 attempt |
| `freeze()` | `store/store.py:BoardStore._claim_in_transaction()` | 整删，保留 claim 冻结的 model_adapter/provider/model 与回合 effort |
| `assert_credentials_current()` | freeze/native_operation；`BoardStore._admission_blocker()`；`HarnessHealth.refresh()` | 整删；删账户 mutation/credential 拒绝，保留运行时可用性与任务准入边界 |
| `native_operation()` | `catalog.discover()`、`BoardService._refresh_harness_catalog()`、`HarnessHealth.refresh/_codex_account_read()` | 删凭据预留上下文及 meta 写入，继续消费各原生读取返回的真实停止事实，不能改成“异常都算已停” |
| `WorkerAccountProvider` | `account_integrations.providers()`；账户与 Codex 测试建立可信替身 | 整删环境／能力／已核实版本注入类 |
| `using_provider()` | 只在账户、Codex 等测试中使用，生产无调用 | 随替身及专属测试删除 |
| `_provider()` | capabilities/execution_environment | 删除集成加载和进程内 provider 注册表 |
| `capabilities()` | `execution_environment/view/Accounts.credential_change`；`AccountOperations._reserve()` | 整删 workerAccount/oauth/apiKey/logout/remove 能力投影；不影响 harness 自身运行能力 |
| `execution_environment()` | `catalog.discover()`、`BoardService._refresh_harness_catalog()`、`HarnessHealth.refresh/_codex_account_read()`；`buddy/runtime/worker.py:Worker._execute_selected()`；`buddy/roles/structured_call.py:_account_environment()` | 整删来源替换逻辑，沿用已有 harness 环境构造路径，不再注入账户目录、BUDDY_ACCOUNT_SELECTION 或 Claude key |
| `secure_account_root()` | execution_environment；`Accounts.set/credential_change()` | 整删运行时账户目录创建；升级删除的路径校验复用 private_dirs 边界 |
| 嵌套 `secure()` | secure_account_root 递归调用自己 | 随账户目录的权限整理删除；通用 private_dirs 安全机制保留 |
| `view()` | `Accounts.all/set`；`AccountOperations._account`；`service/harness_health.py:read_health()`（别名 account_view） | 只读状态投影并到健康读取，建议整文件 accounts.py 最终删除；保留 status/accountType/checkedAt/reasonCode/guidance 等事实，去来源、两个代次、独立能力与 pendingLogin |
| `_invalidate()` | `Accounts.set/credential_change_stopped()` | 删除来源／凭据切换失效路径；原生健康、目录确认与可用性变化的既有失效机制保留 |
| `Accounts`、`__init__()` | `BoardService.__init__()` 建 `account_settings`，测试直接建对象 | 删除状态管理类及 service 成员 |
| `Accounts.all()` | `BoardService.accounts()`，`accounts` CLI／C-Two 只读入口 | 保留只读入口，改为直接读取健康的登录事实，不再实例化账户管理器 |
| `Accounts.native_operation_stopped()` | 账户测试调用；无生产 RPC 或其他生产调用 | 随 account-operation 删除，不能复活成通用 PID 了结入口 |
| `Accounts.set()` | `BoardService.account_set()`、账户测试 | 整删来源切换、CAS、目录创建、account.source_changed 事件生产 |
| `Accounts.credential_change()` | `AccountOperations._reserve()`，账户测试及固定基点生成器的历史版本 | 整删凭据变更 guard 与预留 |
| `Accounts.credential_change_stopped()` | credential_change 正常退出；`AccountOperations._settle()` 的 uncertain 后续确认；测试 | 整删结清、代次写入、失效与 account.credentials_changed 事件生产 |

`BoardStore._claim_in_transaction()` 在同一事务冻结账号、Router 候选账户与输出 claim.account，三项一起删除，不能只删 freeze 留下输出载体。

### account_operations.py

整个文件只管理私有账户操作，建议整删；下面列内部调用以免把停止与线程所有权留在 daemon。

| 符号 | 当前调用方／职责 | 建议去向 |
| --- | --- | --- |
| `Operation` | `_reserve()` 建实例，保存 account、credential guard、mutation、owner、expiry、inflight/settled/state/native_identity | 整删，不迁成通用任务记录 |
| `Operation.id` 属性 | `_reserve/_publish/_settle/login/status/cancel/logout` 与操作映射 | 删除 login-<mutationId> 身份 |
| `Operation.view()` | status/cancel | 删除登录票据投影 |
| `AccountOperations`、`__init__()` | `BoardService.__init__()`；`daemon.py` 持有此 owner | 整删 lock、operations、closed 与 service.account_operations |
| `_reserve()` | login/logout | 删除 Worker 来源、revision、能力与 command 校验，凭据 guard、十分钟票据和 mutation 补写 |
| `_publish()` | `_settle()` | 删除登录结果对 health 的账户回填和 account.operation_completed 事件；原生只读观察由健康检查继续写 |
| `_settle()` | login/_wait_login/status/cancel/logout | 删除账户操作结清状态机；不能把其“未知不等于已停”保护从其他执行状态机删除 |
| `_account()` | login/status/cancel/logout | 删除账户操作结果中的账户重读，普通健康读取另保留 |
| `login()` | `BoardService.account_login()` | 整删 OAuth、API-key、钥匙串 put、私有登录线程与 secret 输入路径 |
| `_wait_login()` | login 启动的后台线程 | 随私有 OAuth 线程删除 |
| `_stop_owner()` | login/cancel/logout | 删除仅属于登录 owner 的停止调度，保留 ProcessHandle 通用真实停止机制 |
| `status()` | `BoardService.account_status()` | 删除 loginId 查询与 uncertain 操作后续结清 |
| `cancel()` | `BoardService.account_cancel()`、close | 删除登录取消 |
| `logout()` | `BoardService.account_logout/account_remove()`，remove=True 是同一路径 | 删除原生 account/logout、keychain delete、独立账户根移除；升级 cleanup 是另一个安装职责 |
| `close()` | `daemon.py:Daemon.cleanup()` | 删除 daemon 收尾的账户取消与二十秒 inflight 等待，不动 Worker／控制器的收尾 |

## 3. 删除后怎样归属

### 原生观察与额度路由：现状

`evaluation/native_observations.py:persist()` 在 actor、重放校验后的结果事务里把 tokenUsage 存进 attempts.token_usage_json；quota/quotaFailure 被规范化后，从 `attempt_account()` 取 claim 的账户，用它覆盖原生顶层 account 标签，provider 取 quota.scope.provider 或 attempt.model_provider。`record_quota()` 为每个本地账户桶写 `account-quota:[adapter,source,credentialRevision]`，并只在与当前选择相同时写 harness_health.quota_json；迟到的旧回执因此不会污染新选择显示。

`routing/quota_routing.py` 的 evidence→record→blocking_records→exhausted/claim/redetect/routing_facts/configuration_retry 使用 adapter/provider/limitId 加上述本地账户桶；key 在 native/0 时是三段，其他账户是五段。耗尽持久到 reset 或更新的可用观察，无 reset 时有一小时单次重试窗口，claim 在事务里只消费一次；显示陈旧与耗尽状态是两件事。删除账户不能删掉这些额度规则或改成额度未知即不可用。

Codex 原生 quota.accountId 经 `codex/protocol.py` 和 `protocol/usage.py:normalize_quota()` 留在 scope.nativeAccountId；目前 quota_routing.evidence/key 并不用它作归属。`account_probe.read()` 的 account/read 原对象只用于 `protocol/billing.py:codex_account()`，返回 billing/quota，不返回真实登录身份。Claude 握手的 apiProvider/tokenSource 与 auth status 被用来判断可用性和计费，也没有可靠的跨回合登录 ID 归属。DSH／ZCode 的目录 accountStatus 可以是 not-applicable。因此四家没有已实现的统一实际账户身份契约，不能声称删掉 attempt-account 后直接换用已有实际登录身份就完事。

### 目录、Router 与健康：现状

`catalog_store.begin()` 把账户与健康快照放在 catalog-accounts/catalog-harnesses，record/current/catalog_account_matches 比较冻结账户与当前选择，阻止切换后的迟到读取覆盖目录；`catalog.py` 的重读单飞以 directory/adapter/account binding 识别，catalog-read-at、catalog-account-status、catalog-scan-after 及 catalog_pending 保护 ADR-027 的可信读取与缺席窗口。

`DecisionCoordinator._create()` 冻结输入 accounts；`_frozen_circumstances_problem()`、claim 和 `_account_binding_changed()` 比较它，claim 还写 attempt-routing-accounts。`router_boundary_data._cached_available()` 不借别的账户的健康事实；`harness_prepare()` 比较 claim.account 及 Router 候选 accounts，再把选中 executable/environment 交给 Worker。Worker、structured_call、run_execution、registry、Codex RunServices 继续传这个账户直到原生子进程。

`read_health()` 把健康记录中 account 与当前 selection 比较，匹配才展示 ready、billing、quota 等；`HarnessHealth.refresh()` 冻结账户环境，保留 mutation 守门及 native_operation，提交时再比账户／revision／命令版本。`_codex_account_read()` 对账户桶持久化 180 秒读取窗口，还有旧 codex_account_read_at 的双读，结束后记录额度与 billing。这里的选择代次不能误当实际凭据身份，也不能在删账户时连同健康 revision、command/version 的迟到写入守门一起删掉。

### 评价与续接：现状

评价卡片、样本与证据的模型归属本来就靠四段配置，不按账户分表：`evaluation/evaluation.py:EvaluationStore._attempt_identity()` 读取冻结的 workflow_turns.context.executionConfiguration，`_requested_identity/_reported_identity/_matches_profile/_sample_basis` 比较 requested/resolved/observed 与 profile、检查真实停止和基础设施失败；当前模型选择不能把历史执行重新贴标签。账户间接影响 profile 的目录／健康可用性与 snapshot 中 quotaExhausted/quotaRetry，不是评价样本 ID。

`WorkflowCoordinator.begin_turn()` 目前还比较上一／当前 attempt-account，并把 context.account 注入回合；Codex 旧回合缺此字段会重建。Codex home.credential_source 及 native binding.json 带 source/credentialRevision/home identity，registry.frozen_account_for() 还有 workerAccount 的旧接口。独立账户删除后这些选择、代次与旧接口全部撤销；本批仍保留版本／配置／原生会话／工作区／实际停止的续接边界，批次 3b 才撤销 goal 私有 Codex home。

### 建议与限制

执行与评价继续归属冻结的 attempt／turn 四段配置；token 用量永远归这次 attempt，不把供应方同名当成共享实际账户。当前额度与重试状态归 harness/provider/limit，保留原生 observation 的来源、时间、scope 和已报告的 nativeAccountId 作事实，不靠黑板虚构 credentialRevision，也不把 model/effort 强行用作额度账户。健康与计费归一次有代次的 harness 原生观察；迟到写入仍用现有 health generation 和 monotone observationId 守门。

这可保住现在的模型／执行归属与耗尽、reset、一次重试、显式选择提醒，但不能保证用户在外部换登录后旧额度事实绝不会沿用；四段配置不识别登录切换，现有 harness 也不全提供可比身份。若 Host 要求保留跨原生账户的隔离，需先冻结新的可验证登录身份与 unknown 语义，不能在本批猜一个 ID 或散列密钥；建议本批明确这个验证边界，不扩展成账户管理。

删除当前派生事实时必须在唯一升级里清掉所有旧形状，之后代码只读当前形状；历史封存的 turn.input/result/event 与其哈希保留原样，旧 account 字段是历史证据，不能成为新执行的绑定依据。不要新增兼容 fallback、native/0 桶或按照写入时间解释记录。

## 4. 只读部分留下什么

| 代码 | 当前能无模型读取的事实 | 删除后的完整性与处理 |
| --- | --- | --- |
| `buddy/harnesses/codex/account_probe.py:read()` | app-server 初始化、account/read(refreshToken=False)、account/rateLimits/read；billing、规范化 quota；finally 检查真实进程停止 | 保留，接已有用户原生环境与 harness 限频；从不启动 thread/turn/model；当前独立账户登录状态并非由此直接返回，不能仅保留这个文件就声称登录状态完整 |
| `buddy/harnesses/discovery.py` 的原生健康探测 | Codex login status、Claude auth status --json、有界 executable 观察 | 保留原生 ready/login-required 与计费；去 inject_claude_key，权限／真实停止证据继续保留 |
| `buddy/harnesses/codex/native_run.py` | 执行／目录握手 account/read、account status/plan、原生 quota 事件与 usage | 保留原生状态与用量；去 Worker selection 分支，私有 coding home 的 native 路径本批暂留，供应方／登录方式限制按批次 3b 处理 |
| `buddy/harnesses/claude/native_run.py` | auth status 目录读取、initialize 的 apiProvider/tokenSource、结果 usage／quotaFailure | 保留原生健康与计费读取、真实回合用量；删 discovery.native_environment 对 inject_claude_key 的调用；不保留 Worker-keyready 的账户伪观察 |
| `buddy/harnesses/zcode/native_run.py` 及 ZCode 协议 | provider access 与模型清单、认证状态、真实回合 token/quota 事实 | 保留；无统一主动余额读取，未知继续未知，供应方限制属于批次 3c |
| `buddy/harnesses/dsh/native_run.py` 与 ACP 握手 | 原生连接／模型与供应方事实、usage/quota、api-key 计费事实 | 保留；不新建余额调用，不从未确认停止推导登录成功 |
| `service/harness_health.py:read_health/HarnessHealth` | needs-login／login-required、只读 account 投影、billingByProvider、quota 和限频 | 并入只读登录事实，不需要 Accounts 管理器；“需要登录”保留，来源切换／能力／待操作去掉 |
| `protocol/billing.py`、`protocol/usage.py`、`evaluation/native_observations.py` | 原生计费类型、规范化 token/quota/failure、提醒与耗尽投影 | 保留功能，移除独立账户参数与桶；nativeAccountId 是原生事实而非应删除的 Worker 身份 |

结论是只读的原生入口仍完整可用，但必须重接健康投影、环境、限频与额度存储，不能留下对 accounts.py 的 import；四家主动用量读取的覆盖程度并不相等，本批不扩大它，也不去问厂商余额接口。

## 5. schema、夹具、凭据、备份与恢复

### 唯一升级基点与 SQL 删除

当前 `store/db.py` 是 SCHEMA_VERSION=16、PREVIOUS_SCHEMA_VERSION=15；`store/migrations.py` 唯一入口 `migrate_15_to_16()`，`install/upgrade.py:migrate_board()` 与 `store/backup.py:_verify()` 都用它。`tests/fixtures/adr021-l18/metadata.json` 明示 schema 15、版本 0.27.0、基点提交 `e4fbc91696f354991252ea2abddc73d45f286aa5`。因此本批是源码目标 16→17，却应把同一个待发迁移延伸成唯一 15→17，PREVIOUS 仍为 15，不另加 16→17、链式迁移或启动转换。

四类指定 meta 加 account-operation 必须在 schema 事务内显式删除；`_migrate_15_to_16_transaction()` 的 planned_meta 精确比较应同步纳入允许删除集合，不能为省事豁免整个 meta。保持 Router 转换、三表重建保值、外键检查与新建／升级形式一致的现有守门；目标名、版本断言、安装及备份试迁移调用一起改。

如果按建议清 harness_health 的当前账户／额度字段，迁移的允许变化表与逐字段计划值也必须扩展到 harness_health，证明 manual_path、revision 等保留字段及其他业务行没有误改；catalog_pending 的清除同样明确声明。不能从 retained fingerprints 里整表去掉后就不再校验内容。

除了五类主记录，当前派生的旧形状还包括：`account-quota:*`、`account-read-at:*`、`codex_account_read_at`、`catalog-accounts:<observationId>`、`catalog-account:<adapter>`、`attempt-routing-accounts:<attemptId>`；quota-routing/quota-retry 的三段／五段 key 与 value.account；harness_health.record_json.account、quota_json.account、billingByProvider/quotaCheckedAt；catalog-harnesses 的账户快照；目录 read-at/scan-after/account-status 与 catalog_pending 曾属于选中来源。建议一次清旧账户派生缓存、限频与耗尽／retry 存量，清当前 health/目录可信投影并重新读取，保留历史 payload 及不可变证据；quota/retry 全清会暂时失去原耗尽事实，这个取舍须由 Host 明确接受，不能悄悄把 Worker 桶合进默认来源。

`catalog-account-status:*` 的内容是 ADR-027 可信读取状态，和 Worker 来源记录不同；当前 runtime 功能仍须保留。目录确认窗口与 catalog_pending 也仍是功能；升级可以按允许集合清旧来源的当前事实并置 unknown，但新代码仍要重建这些规则。如要引入新的结构化 quota 存储，应放专属表；不再新增 meta 例外，是否把现有 quota-routing/retry 一并表化需要 Host 冻结范围，现有例外的系统搬迁原属 ADR-021 L15。

### 仓库夹具的实际量

本回合用 Python 标准库把 `tests/fixtures/adr021-l18/board.sql` 载入内存 SQLite，未打开任何状态目录的数据库：schema=15；account-selection、account-credential、account-mutation 都是 0 条；attempt-account 是 3 条（attempt 尾号 00b、012、01a，均 codex/native/选择代次0/凭据代次0）；account-operation 是 1 条（`account-operation:00000000000000000000000000000021`，codex/native/credentialRevision=0/purpose=fixture-shutdown-unconfirmed/status=uncertain）。没有 Worker 来源记录或独立凭据事实；这不证明日常看板／文件系统无残余。真实看板的测量只能由 Host 在另行授权的数据副本上做。

原 fixture、metadata/provenance 与固定基点生成器继续证明 schema15 输入来源，不手改 board.sql，也不因为当前模块退休就删生成器对旧基点的 account_operation 调用。新迁移测试在私有还原库另外布置四类及派生记录，验证完整清除和其余逐值保留。

### 独立凭据的地址与清理位置

`private_dirs.py:account_root(state,adapter)` 为 `<state>/harnesses/<adapter>/accounts/worker`；Codex/Claude account_integrations 将 CODEX_HOME／CLAUDE_CONFIG_DIR 指到这里。Codex 私有登录显式 `cli_auth_credentials_store="file"`，凭据及原生派生内容在该根内。Claude API key 的 macOS generic-password 服务名是 `hey-my-buddy.worker-account.claude`，account 名是 `_absolute(state)` 的无链接绝对状态根字符串的 SHA-256；`account_keystore.identity()` 没有把名字存在数据库，也不枚举钥匙串。

建议把升级专用的最小精确删除放安装侧，原 account_keystore.py 整删：只删四个 harness 的固定 accounts/worker 根（以及确定空的账户专属容器），复用 private_dirs.remove_tree 的链接／重解析点边界，不追踪到原生用户目录，不动 goals/attempts/session 根；Claude 只按固定 service 和原状态根哈希调用 SecItemDelete，不 get 密钥、不列出条目、不登录。不能从升级临时 trial 目录或重定位副本的路径算另一个 hash，就宣布原条目已删；状态目录被移动过时旧 hash 的发现没有现成索引，需 Host 给出可验证范围或承认无法量到。

MacKeychain.delete() 当前把成功／errSecItemNotFound 视为完成，其他结果抛 ACCOUNT_SECRET_STORE_UNAVAILABLE；非 macOS open_store() 也不可用。永久不可访问的钥匙串，无法同时保证“不拒绝升级”和“所有条目删除成功且无孤儿”，必须有明确未完成状态；建议区分运行时/schema 已切换与凭据 cleanup 待办，不吞异常报成功。

### 现有升级／备份规则与建议提交点

删除 `install/upgrade.py:idle_snapshot()` 的 mutation／active operation 专用拒绝；删除 `BoardStore.reconcile_startup()` 的 active→uncertain 账户 UPDATE、账户计数与通知条件；其他 task、未确认停止 attempt、资源与所有权空闲边界保留。删除账户记录不表示旧进程已经停止，不给出伪 shutdownConfirmed，也不按旧 PID 杀进程。

backup.py 没有 account-operation 的专属备份拒绝 SQL；preflight_entries 的白名单不纳入整个 harnesses 区和钥匙串。因此账户目录／密钥从来不是 rollback 可恢复的备份内容，不应删通用私有区排除、attempt 证据白名单、链接安全与恢复校验。需要改的是 _verify 调用新唯一 SQL 迁移，并证明备份试迁移只操作自己的临时 SQLite；绝不能把 keychain／目录 cleanup 塞进 migrate 函数，让备份验证也删源凭据。

现有 upgrade.restore() 恢复数据库和备份内容，private_migration.rollback() 回滚自身移动的私有内容，两者都不能恢复已经删除的独立密钥。建议新 schema／运行时／skill 验证并提交之后，单独持久化准确 cleanup 步骤，recover 可重复继续删除，完成之前不能清掉唯一待办 journal；cleanup 失败不应重启宣称凭据完整的旧运行时。这个建议还须审阅与现 upgrade.json 对服务写入的 fencing 怎样衔接，不能无限期挂旧 journal 却宣称升级可用。

未知账户操作进程可能还在使用／写入独立根；撤销其账户功能与删记录并没有新增实际停止证据。允许这种历史 uncertain 记录在场完成升级，是 ADR-030 的清理方向，但何时可以不可逆删除它的凭据、如何表达该风险，需要 Host 在冻结方案里明确；本回合没有自行猜定此边界。

## 6. 删除入口、控制台与测试

### 命令与服务入口

| CLI 命令 | C-Two / 服务方法 | 去向 |
| --- | --- | --- |
| account-status | account_status | 整删登录操作轮询，不是原生 account/read |
| account-set | account_set | 整删来源选择 |
| account-login | account_login | 整删 OAuth／stdin API key 登录 |
| account-cancel | account_cancel | 整删登录取消 |
| account-logout | account_logout | 整删独立来源登出 |
| account-remove | account_remove | 整删独立来源移除 |

六项一起从 `cli/main.py:METHODS/main()`（含密钥 stdin 和 key_value）、`cli/cli_help.py:SUMMARIES`、`protocol/contracts.py:BuddyControl`、`protocol/transport.py:METHOD_MAP`、`service/service.py:CONSOLE_METHODS` 与六个方法、`console/server.py:CONSOLE_OPERATIONS`、`console/console_sessions.py:READ_OPERATIONS` 移除。service 的 Accounts/AccountOperations 初始化与 daemon 的 owner 挂接／close 也删除；保留 C-Two 边界对不存在方法的正常拒绝，不留 alias。

另有独立的只读 `accounts` CLI、BuddyControl.accounts 与 BoardService.accounts；计划明确删除六个 account-* 没列它，建议保留同名只读入口，直接返回登录／计费状态，帮助里的 sources/capabilities 改成只读事实。Host 如决定连它也删，需要明确扩展删除清单；不把 accounts 当六命令的兼容别名。

整删生产文件：`src/hey_my_buddy/blackboard/catalog/accounts.py`（只读投影先并到 health）、account_operations.py、account_keystore.py，以及 `src/hey_my_buddy/buddy/harnesses/account_native.py`、account_integrations.py。删除 account_native 的 key_value/harden_home/environment/_end/_cleanup_native_helpers/write_codex_key/_public_account/CodexAccountProcess 私有登录所有实现，通用 ProcessHandle、原生协议和 Windows 所有权机制保留。

改相关载体：`buddy/runtime/worker.py`、`buddy/roles/structured_call.py`、run_execution.py、schema_worker.py 的 bind_account_environment、`buddy/harnesses/registry.py:frozen_account_for()`、Codex home/native_run 的 account/workerAccount/BUDDY_ACCOUNT_SELECTION/credentialSource 分支，以及 discovery.py 的 inject_claude_key。`runtime_selection.py`、`install/launcher.py`、`cli/checks.py` 与 tests/support 的账户环境变量传递／净化列表同步去掉退休变量；其余运行时与 Worker／agent 凭据的净化保留。

`private_dirs.account_root` 的运行时 API 与 `tasks/storage.py` 中 accounts/user-account-protected 分支删除；升级清理精确地址归安装侧，不能把退休入口留下当兼容。`apps/console/src/StoragePanel.tsx` 的 user-account-protected 文案及其测试随实际受保护项删除，通用持久记录保留。

### 清单逐行与批次 3b 的边界

拒绝清单 E 类 10 行分别是 account_integrations.inject_claude_key 的 2 行、codex/home 的 frozen_account 1 行、credential_source 4 行、prepare_coding_home 的 Worker 来源检查 2 行、structured_call._account_environment 1 行，全部随独立来源删除。after 指“批次 2”的另外 10 行是 account_integrations._environment 的私有目录检查与 account_native 的九处密钥／私有目录／登录／停止拒绝，整个文件删除而非改判为宽松登录。

执行计划文末改判另把 codex/home 的五处私有主目录安全检查和 native_run._coding_home 一处包装留给批次 3b；这与批次 2 的 E 行不矛盾，按条件拆 Worker 来源分支与仍在用的 native 私有 home 分支。本批不整删 codex/home.py，不提前取消私有 CODEX_HOME/HOME/MCP／供应方限制，不把 retained credential 的真实安全边界删成空壳；批次 3b 再连同其专用实现／测试一起删除。

### 控制台源码、数据层、文案与预览

| 文件／区域 | 建议 |
| --- | --- |
| `apps/console/src/buddies/HarnessAccountDialog.tsx` 与 harness-account-dialog.test.tsx | 整删管理账户对话框及 25 项独立来源／密钥／OAuth／取消／登出／移除测试 |
| `apps/console/src/harness-account.ts` | 不能整删；HarnessAccount/parseHarnessAccount 供 harness.ts、types.ts 只读展示，改唯一登录事实形状；删 AccountSource、AccountCapabilities、AccountLogin、parseAccountLogin、accountCommand 与 URL／票据处理 |
| `buddies/HarnessDetails.tsx:AccountBlock` | 删 dialog open state、管理按钮、来源与能力展示、为其传入的 api/csrf/gate/onRefreshed；保留登录状态、类型／计费和原生登录指引 |
| `buddies/harness.ts`、`types.ts` | 改健康 account parser/view/类型，去 source/revision/credentialRevision/capabilities/pendingLogin，保留 status/type/plan 等可观察事实 |
| `buddies/HarnessHeader.tsx`、CatalogView.tsx | 保留账户与额度展开；删仅为管理入口的传参，位置／状态／额度读取仍有意义 |
| `i18n/zh/buddies-harness.ts`、en 对应文件及 buddies.ts | 删除来源切换／独立管理文案，保留状态／计费／需要登录；两个语言同时清，unused 测试守着无调用词条 |
| `styles.css` 的 harness-account-* 操作样式 | 删除无剩余 JSX 使用的操作样式，布局变化交 Host 构建与浏览器核验 |
| `tests/probes/objective_console_preview.py:harness_account/harness_rows` | 这是合成预览数据实际位置；去 native/worker 选择、两种代次、能力／OAuth／API-key 参数与 DSH worker 示例，保留 ready/logged-out/type/checkedAt 等状态 |
| `host-workflow.ts` 及 routing-timeline 的相关数据 | 删独立 account 在额度／重试类型中的投影，保留执行配置、限额与计费事实；无需用同名 account 字符串去扫掉原生 status |

本批只改控制台源与测试，绝不生成 `src/hey_my_buddy/console/assets/` 产物；Host 统一重建。

### 要删与要改的 Python 测试

整文件退休候选：`tests/python/blackboard/catalog/test_account_operations.py`、test_account_native_owner.py、test_account_bindings.py；`tests/python/blackboard/store/test_account_operation_recovery.py` 的五项账户恢复测试；`tests/python/blackboard/service/test_account_operation_recovery.py` 的账户 owner daemon 重启测试。它们的现有业务对象整体退休，通用原生所有权／停止检查由现存测试继续守。

`catalog/test_accounts.py` 的来源 CAS、切换、独立目录、credential_change/native_operation、source／凭据变化续接、账户 mutation 权限与 Router 候选账户改变等断言退休；只读 accounts=health、无刷新和 CLI 契约用例移入只读健康／入口测试，增加六命令不存在和 needs-login 仍展示后再删原文件。不要把“只读无需账户 owner”也随来源管理一起删除。

`catalog/test_account_quota.py` 六项都是账户桶形状，但包含要保留的功能：退休 credentialRevision 变化／untagged native0 双读断言；将旧回执、near-limit/rate-limit、redetect/retry 与并发一次 claim 改成 attempt 四段配置及 harness/provider/limit 归属，再把功能并入 native_observations／quota_retry 等所有者测试，原文件退休。

| 测试文件（tests/python/ 下） | 要改／保留的内容 |
| --- | --- |
| `blackboard/evaluation/test_native_observations.py`、`protocol/test_billing_quota.py`、`protocol/test_usage.py` | 保留 token/quota/failure、原生计费、Codex 无模型读取与 180 秒限频，调整去桶后的存储／调用形状 |
| `blackboard/routing/test_quota_retry.py`、test_nonretryable_quota.py | 保留耗尽不被 unknown 清掉、reset、显式选择提醒、单次重试、redetect 和并发事务规则 |
| `blackboard/catalog/test_catalog.py`、test_catalog_observations.py、test_catalog_trust.py；`blackboard/tasks/test_catalog_trust.py`；`blackboard/routing/test_catalog_trust.py` | 退账户切换及绑定 key；保留 monotone observation、失败保留目录、可信状态、缺席窗口、单飞／限频／pending 与用户策略 |
| `blackboard/catalog/test_native_discovery_stop.py`、`buddy/harnesses/test_runtime_version.py` | 去对 account-operation active/uncertain 的断言，保留真实创建／停止证据与未知版本可继续等原规则 |
| `blackboard/service/test_harness_health.py`、test_harness_service.py | 去 Worker account provider/selection，保留 login-required、只读状态、命令／健康代次、目录扫描、限频与迟到读取边界 |
| `blackboard/service/test_service.py`、test_service_environment.py；`protocol/test_rpc_config.py`；`console/test_console.py` | 去账户 mutation/RPC/secret 特例与对应允许列表，证明六入口不存在、只读 accounts／health 可用、其他权限不变 |
| `buddy/harnesses/codex/test_codex.py`、test_codex_continuation.py、test_native_run.py、test_no_tool_codex.py | 退 workerAccount／选择代次／凭据代次／独立 home，用 native 路径覆盖执行／Router；私有 home 安全与续接仍到批次 3b |
| `buddy/roles/test_registered_review.py`、test_registered_run_wiring.py、test_run_execution.py；`buddy/harnesses/test_harness_discovery.py` | 去冻结账户与注入断言，保留相同运行路径、权限、配置／原生身份与停止证据 |
| `test_private_directories.py`、`blackboard/tasks/test_storage.py`、test_workspace_lifecycle.py | 去 account_root／user-account-protected 特例，保留 goal 私有根／证据清理与未知停止保护 |
| `blackboard/store/test_migrations.py`、test_upgrade_column_order.py；`install/test_upgrade_migration.py`、test_upgrade_service.py | schema17、唯一15→17、五类及派生清理、精确保值、新建形式一致、备份 trial 无外部副作用；原 R8“uncertain 保留／凭据仍禁止变更”与 active mutation 阻升级规则退休 |
| `blackboard/store/test_upgrade_fixture.py`、upgrade_fixture.py | 保留 schema15 fixture 中 uncertain 记录及来源还原，不在还原时删除；升级之后改断言已清除 |
| `install/test_backup.py`、test_backup_preflight.py、test_backup_windows.py | 通用证据／私有区／链接／恢复规则保留，只改直接受新迁移及账户根退休影响的断言 |
| `cli/test_support_cleanup.py`、`install/test_packaging.py`、support.py | 去退休账户变量／模块／资源的对应项，保留 runtime/Worker/agent 凭据净化与单分发检查 |

Router 的 `test_router_boundary_integration.py` 的 cached_health_account_mismatch、test_router_dispatch.py、test_router_probe.py、test_router_sequence.py、test_single_router_requests.py、test_router_failover.py、test_router_failover_integration.py 也要去 accounts 冻结／changed account 条件；保留当前 Router 列表、身份、配置变化、容量、发布 fencing 与真实停止边界。`tests/python/blackboard/tasks/test_dsh_resume_selection.py` 的回合账户 fixture 同步削减；其他 harness 的测试文件出现 accountStatus/apiProvider 之类原生事实不能作为整删依据，只改受 account 载体参数变化影响的调用。

### 要改的前端／浏览器测试

`apps/console/src/buddies/catalogue-actions.test.tsx` 去 writes the source／changes the source in the dialog／opens the dialog 等管理行为，保留只读账户类型／状态；harness.test.ts 去 native/worker 来源断言；harness-wire.test.ts 保留唯一登录事实 parser，退票据 URL／能力 parser；english.test.tsx 去 Manage account dialog 遍历，保留英文状态／位置／额度；catalogue-view.test.tsx、catalogue-gestures.test.tsx、harness-quota.test.tsx 调整数据形状并保留只读行为；routing-timeline.test.tsx 去额度账户桶形状；`i18n/unused.test.ts` 继续验证中英文无弃用词条。

`apps/console/browser/catalogue.check.ts` 的 expands accounts independently 用例去 manage/dialog 像素，保留展开、check 与状态指引；catalogue-gestures.check.ts 的英文遍历去 Manage account 项，保留只读手势。实现回合不跑 browser 或构建，列交 Host 的检查边界。

### 命令执行读取缺陷的根因与回归

`tasks/workflow.py:_validated_configuration()` 对 command/external 返回原约束，`_configuration()` 读回；任务读扩展把普通 command 执行传给 `native_observations.warnings()`；后者无条件按 configuration.adapter 调 `latest_quota()`，再走 accounts.selection/_adapter，command 不在四个 harness 中，因而报 UNSUPPORTED_ADAPTER。建议在共享原生观察读取边界明确只有真实 harness 才有原生额度／计费投影，普通 command 返回空提醒／无原生状态；不要在 CLI/get 周围 catch 此错误，也不要把 command 填入账户 ADAPTERS。

回归应经 Host 直接提交 command、真实读取执行／结果路径证明读取成功，已有执行与结果身份／tokenUsage 不被重贴 harness 标签、quotaWarnings 为空且不调用任何原生账户查询；建议放 `tests/python/blackboard/tasks/test_workflow.py`，并在 `tests/python/blackboard/evaluation/test_native_observations.py` 检查原生观察边界，不启动日常服务。若额度函数去账户后刚好不再抛错，仍要测试 command 不被当作 harness，而不只检查“不抛异常”。

## 7. 与文档绑定的测试（文档由 Host 改）

本回合检索直接读 SKILL.md、README、docs/reference 的测试及账户字句，没有发现直接断言公共文档的“Worker 独立账户”文字、六命令说明或账户恢复章节的测试。`protocol/test_billing_quota.py` 的 README 只是私有 Git fixture 的内容，不是公共文档；其计费／account probe 用例应保留，不能误判成 Host 改文档后可整删的测试。

| 文件／测试符号 | 真正约束 |
| --- | --- |
| `tests/python/cli/test_skill_workflow.py:SkillWorkflowTests.test_details_keep_a_destination` | operations installation/backup、console、workflow routing、evaluation-maintenance assessment、harnesses unavailable 的 owning reference marker，不断言独立账户文字 |
| 同文件 `SkillLinkTests.test_every_skill_link_points_into_a_shipped_reference_page` | skill 的链接与锚点能找到随包参考页 |
| 同文件 `SkillLinkTests.test_the_packaging_rewrite_keeps_every_link_inside_the_skill` | 打包后链接仍在 skill 内 |
| `tests/python/install/test_packaging.py:SkillBuildTests.test_links_stay_inside_the_skill` | 打包 SKILL 和所有 references 的链接存在；Host 删除账户章节锚点时同步修对应引用 |
| `tests/python/install/test_first_install.py:DocumentationTests.test_operations_document_the_shared_skill_install` | owning operations 的共享 skill 安装说明 |
| 同文件 `DocumentationTests.test_readmes_keep_first_install_parity` | 两份 README 安装入口的语言 parity |

`catalog/test_accounts.py:AccountServiceTests.test_named_contract_and_cli_use_the_same_current_account_operations` 实际断言 CLI account-set 的 adapter/source/expectedRevision；它是本批退休的账户帮助／契约测试，不是公共文档绑定。SkillSizeTests 的大小／无版本与 HostGuideTests 的角色说明等其他文档约束仍保留，角色说明另一个微任务在改，不据账户删除扩大改动。

Host 修改的 owning reference 包括 harnesses、workers、codex、claude、cli、console、operations、evaluation 与 architecture 的旧账户归属／升级说明；README 与中文对应、共享 skill 摘要随后按语言 parity 跟随。本微任务只列这些关系，不改这些文件。

## 8. 执行计划的不足、建议审阅项与验证边界

1. “schema 从16到17”不能误落实为新输入基点16；当前唯一封存输入仍15，应扩展同一待发升级到15→17，并改安装／备份／测试调用。
2. 存储清理少了 account-operation 及账户衍生 quota/catalog/read/retry 形状；只删四类会留下不能了结的操作、依旧依赖凭据桶的缓存和新旧两套额度地址。
3. “额度与评价归属不变”需分开表述：评价已经归冻结四段配置；额度当前归本地 credential 桶，改成 harness/provider/limit 后不能声称实际账户切换隔离仍已实现，可靠登录身份契约并不存在。
4. 凭据 cleanup 必须是安装协调职责，SQL migration 被 backup trial 共用，不能携带外部删除副作用；不可逆提交点、故障重入、rollback 及 pending cleanup 与 upgrade fencing 尚无完整协议。
5. 钥匙串不可用时“不拒绝升级且无孤儿”的绝对要求不可同时实现；建议允许清理待办但不报完成，Host 冻结重试责任和完成条件；移动状态根导致原 hash 无索引也须说明边界。
6. 清旧 uncertain 操作不意味着进程已停；允许升级和何时删除仍可能被它使用的账户根／凭据，需明确策略，不能用删除记录猜停止或自行杀历史进程。
7. accounts 只读入口与 harness-account.ts 只读 parser 应保留重接，批次2的 Worker 分支和批次3b的 native 私有 home 应分开删除；目前清单行号与源码已有偏移，以符号／条件核对，不把原生 accountStatus 当退休账户对象。
8. 当前预览数据在 tests/probes/objective_console_preview.py，测试专属账户文件不止计划的“五个”；通用文档／打包、i18n unused、storage 保护、环境与载体净化、Host command 读投影回归也要列入实现检查。

实现仍等 Host 过目后授权；受影响 Python 模块按用户规定使用 uv frozen unittest，先清继承的 runtime／Worker／agent 环境，前端仅 typecheck/test，装不上照实交 Host。完整 Python 检查、真实浏览器、构建、空 HOME 安装演练、真实数据副本与故障注入都由 Host 执行，本回合没有宣称它们通过。

与角色说明微任务重叠的 schema_worker.py、worker_services.py、turn_io.py、router.py、router_input.py 及 packaging/runtime-assets.json，只改账户载体相关行；其他提示词与资源接线保留整合方的版本，不借批次 2 重写。


## 9. 冻结的存储方案

本节落实第二回合 Host 的定案，优先于上文第一回合建议：唯一升级基点改为已安装 0.29.0、提交 `2bee001e9ef37acb256c19f97a29b2a5ece3a7fb`、schema16，目标17；旧15→16迁移与专属夹具／测试退休。Host 在副本上已量到没有独立来源或账户目录，因此本批不做文件系统与钥匙串清理，也不保留清理协议。原生登录身份只作事实，不用于本批判断。

额度状态的键为 adapter/provider/limit_id；空字符串 limit_id 明确表示供应方整体限额，公开读取仍投影为 null，不保留两种存储形式。最新额度观察每个 harness 一行，与现有显示粒度一致；其 quota JSON 是规范化的原生正文，account 本地桶标签删除，原生标识同时在 native_account_id 列记录事实。主动账户读取的180秒窗口每个 harness 一行；目录可信读取与扫描窗口继续由 ADR-027 所有者管理，本批只清旧来源的存量。

```sql
CREATE TABLE quota_current (
    adapter TEXT NOT NULL,
    provider TEXT NOT NULL,
    limit_id TEXT NOT NULL,
    blocked INTEGER NOT NULL CHECK (blocked IN (0,1)),
    available INTEGER NOT NULL CHECK (available IN (0,1)),
    code TEXT NOT NULL,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    resets_at TEXT,
    native_account_id TEXT,
    retry_basis TEXT,
    retry_manual_at TEXT,
    retry_consumed_at TEXT,
    retry_consumed_by TEXT,
    PRIMARY KEY (adapter, provider, limit_id)
);
CREATE TABLE harness_quota_observations (
    adapter TEXT PRIMARY KEY,
    observed_at TEXT NOT NULL,
    native_account_id TEXT,
    quota_json TEXT NOT NULL
);
CREATE TABLE harness_account_reads (
    adapter TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL
);
```

| 旧键／字段 | 16→17 的处置 | 当前代码的唯一去向 |
| --- | --- | --- |
| account-selection、account-credential、account-mutation、attempt-account、account-operation 前缀 | 清除所有行，不判断状态 | 来源选择、变更与预留功能删除 |
| account-quota、quota-routing、quota-retry 前缀与 harness_health.quota_json | 清除存量，不把旧桶归到用户原生登录 | 新观察写 harness_quota_observations 与 quota_current；旧 quota_json 列物理删除 |
| account-read-at 前缀与 codex_account_read_at | 清除存量 | harness_account_reads 记录一次主动原生读取的限频时刻 |
| catalog-accounts、catalog-account、attempt-routing-accounts、attempt-harness 前缀 | 清除存量 | 账户 CAS 链删除；目录 observationId、health revision 守门保留 |
| catalog-harnesses:<observationId> 快照的 account 字段 | 清除整个旧快照缓存 | 新快照只带只读登录事实，不用于账户选择 |
| harness_health.record_json 的 account、billingByProvider、quotaCheckedAt 等旧当前投影 | 清当前观察并置 unknown，保留 manual_path 与 revision | 健康重读后写无来源／代次的只读登录、计费投影 |
| catalog-read-at、catalog-account-status、catalog-scan-after 与 catalog_pending | 清旧当前窗口／确认状态和 pending，catalog_current 置 unknown 并清当前payload指针 | ADR-027 继续按 harness 重建可信读取与缺席窗口，不继承独立来源的事实 |
| 封存 workflow_turns／Router 输入、result 与 events 中的历史 account | 原样保留，连同哈希不改 | 新执行不再读取历史账户作绑定 |

迁移在单个 SQLite 事务中建三表、删除旧键、重建 harness_health 去掉 quota_json、清当前目录／健康与 profile 可用投影并更新 schema marker；SQL 不带任何外部副作用。精确计划值验证允许变化的 meta、健康、目录、pending 与 evaluation_profiles（仅 available/unavailable_reason），其余业务表逐值保留；新表为空并与新建schema形式一致。夹具由新基点自己的公开操作生成到 tests/fixtures/upgrade-base，基点metadata与来源一起记录；不手写SQL来补账户存量。


## 10. 第二回合实现候选与待完成验证

本节记录实现候选，尚不是验收：六个账户管理命令、契约／服务入口、独立账户 owner、钥匙串／私有登录实现与账户选择／凭据 CAS 已删除；accounts.py 只保留从健康读取登录与计费的 view/Accounts.all。控制台账户管理对话框、操作数据层与专属测试已删，登录／计费／额度／重试仍是只读事实；没有构建产物变更。Host 授权的公共文档只删除退休功能描述。

三个新表已接入当前额度状态、最新原生观察与180秒读取窗口，旧 meta 双读／materialize 与本地账户桶去掉；原生 native_account_id 只作事实。执行用量归原 attempt，评价保留四段配置；额度归 harness/provider/limit。原生观察比较解析后的时间，避免整秒与小数秒字符串顺序误判；unknown／迟到观察不清耗尽，reset／更新的可用观察与单次重试规则保留。共享 harness 名称放 protocol/harness_names.py，黑板的 command/external 额度边界不再导入原生 runner 契约。

目录重读仍单飞与限频，去掉来源 epoch，保留 observationId 顺序、health revision、ADR-027 accountStatus 与缺席确认。升级清旧 evaluation_profiles 的 available/unavailable_reason 并全列核验其他字段不变，避免健康恢复而目录仍 unknown 时旧来源模型继续路由；enabled、身份、卡片与偏好不清。attempt-harness 也是旧来源健康快照，迁移一并清，之后新 claim 只写当前只读形状。

Codex 私有主目录未删除；它原有 native credentialSource 的 home/source=native/credentialRevision=0/identity 形状本批保留，仅由本机路径生成，用于私有会话绑定，不是额度桶，不再读取账户选择或 workerAccount。修改此私有持久形式与撤销 home 仍属于批次3b，避免本批在禁止文件系统升级时让旧 native 会话失去绑定。

Host command 读取缺陷修在 native_observations.warnings 的原生事实边界：非 harness 的 command/external 返回空提醒，不触发原生额度查询。新增 tests/python/blackboard/tasks/test_workflow.py 的 test_host_command_execution_reads_have_no_native_quota_projection 经 Host submit、claim、结果导入、workflow_get、task_get/list/result 覆盖该触发；该服务链回归尚未跑到，不能仅凭源码说已验证。

### 实际检查边界

父回合在最终源码上执行 compileall 与 git diff --check 通过；实际导入当前 quota／native_observations／accounts 模块、使用私有内存 SQLite 的检查通过：原生ID列只记录事实，整秒到小数秒的新观察能替换，unknown／迟到观察不清耗尽，重检测后只允许一个 decision 消费重试，新的可用观察清耗尽与标记，command/external 提醒为空，以及只读 ready/billing/login-required 形状不含来源／能力。静态 AST 核对 CLI METHODS 与 BuddyControl 六个管理入口不存在、accounts 保留。标准库DDL检查证明空 schema16 的实际新增表／health重建形式与新建17一致，BEGIN失败检查证明 foreign_keys 恢复；这些窄检查不等于完整升级／服务测试。

规定的 uv unittest 命令未进入测试，多个聚焦组在依赖下载／构建依赖阶段失败：PyPI DNS不可达，涉及 typing-inspection、typing-extensions、fastdb4py、zstandard、hatchling／portalocker。没有以系统 Python 或安装运行时替代规定的完整测试，也没有绕过网络沙盒。前端使用支持的 Node24尝试 npm ci，沙盒连接拒绝 EPERM；npm typecheck 缺 vitest/globals 退出2，npm test 因 vitest 不存在退出127。前端的语法／解析与文案静态核对不能替代这两个命令。

以下 Python 模块需在依赖可用后运行；路径均相对于 tests/python，执行时设置 PYTHONPATH=tests/python:src，清用户指定的十个继承绑定，用 uv run --frozen python -m unittest 加对应点分模块名。

| 组 | 模块 |
| --- | --- |
| 新基点与迁移（已尝试，未进入测试） | blackboard.store.test_migrations、blackboard.store.test_upgrade_fixture、install.test_upgrade_migration、install.test_upgrade_service |
| 原生观察与额度（已尝试，未进入测试） | blackboard.evaluation.test_native_observations、blackboard.routing.test_quota_retry、blackboard.routing.test_nonretryable_quota、protocol.test_billing_quota |
| 目录信任（已尝试，未进入测试） | blackboard.catalog.test_catalog、test_catalog_observations、test_catalog_trust、test_native_discovery_stop；blackboard.tasks.test_catalog_trust；blackboard.routing.test_catalog_trust |
| command读取／续接／Router（已尝试，未进入测试） | blackboard.tasks.test_workflow、test_dsh_resume_selection；blackboard.routing.test_router_settings、test_router_dispatch、test_router_failover_integration、test_router_boundary_integration、test_router_sequence、test_single_router_requests、test_router_failover、test_router_probe |
| 补退休转换引用（源码已改，未启动） | blackboard.routing.test_routing_modes、test_router_list_configuration |
| 运行时调用（部分组已尝试，其余未启动） | buddy.harnesses.codex.test_codex、test_native_run、test_codex_continuation、test_codex_tool_evidence；buddy.roles.test_registered_review、test_registered_run_wiring、test_run_execution、test_schema_worker |
| 只读服务／存储／环境（未启动） | blackboard.service.test_harness_service、test_harness_health、test_service、test_service_environment；blackboard.tasks.test_storage、test_workspace_lifecycle；test_private_directories；protocol.test_rpc_config；console.test_console |
| CLI／文档／分发（未启动） | cli.test_cli、test_host_cli、test_skill_workflow、test_support_cleanup、test_checks_parallel、test_host_preview；install.test_packaging、test_runtime、test_first_install |
| 原有备份边界（未启动） | install.test_backup、test_backup_preflight、test_backup_windows |

### 实质剩余工作：新夹具尚未产出

生成器已改为固定 0.29.0 基点的公开操作，目标五文件为 tests/fixtures/upgrade-base/board.sql、operations.json、catalog.json、workspace-resources.json、metadata.json；18类可写前缀的生产writer覆盖与计数assert已经接入。codex_account_read_at 在此基点没有writer，生成器不伪造该键，metadata 明确这个边界，私有迁移测试另行覆盖其清除。尝试曾在私有基点的 workflow_integration_record 得到 METHOD_NOT_FOUND，已改用现行 workflow_accept/continue/cancel；新增的 Router、账户及额度writer接线尚未重新实跑，可能仍需根据实际错误修正。

受依赖限制，upgrade-base 的五个固定产物尚未生成，tests/fixtures/adr021-l18 暂时保留，当前测试源码已经指向新目录，故迁移交付不完整；不能把这件事仅记成“Host补跑测试”。依赖准备好后，本 Worker 仍须真实生成、核对 provenance／哈希／全类存量、退休旧stock，并继续修实际生成或测试揭示的范围内缺陷。本批不会手写SQL补造fixture，也不会把schema15旧stock改marker冒充16。

可在本检出内恢复生成：先准备冻结依赖，再运行 `uv run --frozen python packaging/generate-upgrade-fixture.py --work-dir tmp/adr030-upgrade-base --output-dir tests/fixtures/upgrade-base`；work-dir须尚未存在，生成器会在此检出的ignored tmp归档固定基点、建立自己的私有工作区与依赖环境，不启动服务或模型。当前请求只需 Host 在本检出准备Python／前端依赖并让本 Worker 继续；若另授权一个 prepared-python，也必须明确它的读取范围，不自行借用日常运行时。

完整检查、浏览器检查、控制台构建、空HOME演练、真实看板副本与故障注入仍由 Host 执行；本回合没有运行或宣称通过它们。
