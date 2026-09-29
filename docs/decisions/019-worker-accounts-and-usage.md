# ADR-019：Worker 独立账户与用量可见性

## 状态

提议：用户于 2026-09-29 确认本文方向，排在 [ADR-018](018-routing-modes-and-host-workflow.md) 第二批之后作为后续目标。本文尚未实现；实现、安装与改变用户配置均需另行授权。以下原生能力的静态核查不等于登录、隔离、用量读取或 Windows 验收。

## 背景

目前 Worker buddy 使用 harness 的本机登录，Host buddy 自己使用的 Codex、Claude Code 登录与 Worker buddy 共用账户及额度。服务无法为 Worker buddy 单独接入 BYOK 或 OAuth 账户；用户通常要到原生执行失败后才知道额度不足。黑板可以显示已记录的执行用量与额度观察，但尚无按账户管理的入口。备份不含凭据可以缩小备份暴露面，代价是恢复后必须重新登录。

### 已核实的本机事实（2026-09-29，只读静态检查）

- **Codex CLI 0.157.0。** `codex login --help` 确认 `--with-api-key` 和 `--with-access-token` 从 stdin 读取，另有 `login status`。重新运行 `codex app-server generate-json-schema --out <系统临时目录>` 后，v2 `LoginAccountParams` 确认 `chatgpt`、`chatgptDeviceCode` 与 `apiKey` 等变体；`LoginAccountResponse` 对浏览器登录给出 `authUrl`、`loginId`，设备码变体给出 `verificationUrl`、`userCode`、`loginId`；`AccountLoginCompletedNotification` 与 `CancelLoginAccountParams` 支持完成与取消关联。`GetAccountParams`、`GetAccountResponse`、`GetAccountRateLimitsResponse`、`GetAccountTokenUsageResponse` 分别提供状态、套餐与额度窗口、余额及按天 token 用量；这些属于账户协议方法，不要求发起模型回合，但实际运行中的无模型调用行为仍须原生检查。安装包原生可执行文件的 `strings` 含 `CODEX_HOME` 的解析与配置路径信息，当前适配器亦用它选择私有主目录；主目录隔离的效果尚须原生验证。依据：上述帮助、生成的 v2 schema，以及已安装 `@openai/codex-darwin-arm64` 可执行文件的 `strings`。
- **当前 Codex 接入。** `src/buddy/adapters/codex_runner.py:_run` 以 `account/read` 检查账户，并在 `account.type != "chatgpt"` 时以 `account-plan-required` 拒绝 API key；普通执行沿用环境中的主目录，审阅与无工具 Router 则设置 `CODEX_HOME` 到私有目录，审阅目录还把原有 `auth.json` 作为符号链接接入。`src/buddy/adapters/codex_config.py:native_environment` 与 `src/buddy/harness_discovery.py:native_environment` 不传入调用者的 API key。
- **Claude Code 2.1.284。** `claude auth login --help` 列出默认订阅 `--claudeai`、按量的 `--console`、`--sso`、`--email`；`claude auth status --help` 支持 JSON 状态；`claude setup-token --help` 只说明需订阅的长效令牌设置，未给出安全的无终端写入选项。对 `/Users/soku/.local/bin/claude` 的 `strings` 检出 `CLAUDE_CONFIG_DIR` 被用作配置主目录、`apiKeyHelper` 设置及 macOS Keychain 读写提示，说明配置目录可定位，但不足以证明 OAuth 凭据与本机登录的隔离。没有独立的原生用量查询命令；`src/buddy/adapters/claude_protocol.py` 解析运行中的 `rate_limit_event`，`claude_runner.py` 记录观察。`src/buddy/adapters/claude_config.py:NATIVE_ENVIRONMENT_ALLOWLIST` 与 `src/buddy/harness_discovery.py:native_environment` 均未放行 `ANTHROPIC_API_KEY`；现有代码的注释提及该环境变量，不应把注释当作已实现的密钥入口。
- **ZCode 0.16.9。** 先检查已安装 `zcode.cjs` 的 CLI 分发：`values.help` 在 `case "login"` 调用 `runLoginCommand` 之前返回，随后才执行 `node zcode.cjs login --help`；帮助确认 `--no-browser` 会打印 OAuth URL。源码的 `resolveSharedZCodeCredentialsPath` 使用 `ZCODE_DATA_BASE_DIR` 或用户主目录下的 `.zcode/v2/credentials.json`，本文没有打开该文件；`runLoginCommand` 把授权链接输出到终端，登录进程等待原生流程。`src/buddy/adapters/zcode_config.py:SUPPORTED_ACCESS` 与 `zcode_runner.py` 当前只接受 `api-key`、`zhipu-coding-plan-api-key`，拒绝 OAuth 账户服务商。未从静态检查确认可供本提案读取的额度接口。
- **DSH 0.1.5-rc.1。** `~/.local/bin/dsh` 指向已安装 DSH 包；顶层帮助没有密钥管理或用量命令。包内 `@deepseek-ai/dsh-credentials-local/README.md` 说明原生凭据库默认位于 `$DSH_HOME` 或 `~/.dsh` 下的 `.credentials.yaml`，可由 DSH 配置界面或其 `ctx.credentials.set` 写入，`DEEPSEEK_API_KEY` 也可从启动环境读取；没有确认可直接调用的非交互 CLI 密钥设置命令。`harnesses/dsh/scripts/run.mjs:credentialsStore` 标明凭据由 harness 用户存储持有；本次未打开凭据文件，也未确认 DSH 的余额或用量接口。
- **服务接触点。** `src/buddy/harness_discovery.py:native_environment` 已白名单传递 `CODEX_HOME`、`CLAUDE_CONFIG_DIR`、`ZCODE_DATA_BASE_DIR`，未传递 `DSH_HOME`；`src/buddy/harness_health.py:SCAN_SECONDS` 为 180 秒，`refresh`/`kick` 借 Host 请求按需限频。`src/buddy/backup.py` 的滚动备份显式包含 SQLite、`attempts`、`controls`、`submissions`、部分 Worker 回执及若干状态文件；账户私有目录并非当前备份项，后续也不得放入。`apps/console/src/BuddyConfig.tsx` 的 `HarnessStatusBar` 按 harness 渲染检测行，适合增加账户和额度入口；配置选择仍由该组件下方的 harness 分组呈现。

### 待核实

- Codex 的账户协议与 `CODEX_HOME` 隔离是否在实际私有目录中无模型回合地完成登录、登出、状态及额度读取；`account/rateLimits/read` 对 API key 与各套餐返回哪些字段，账户类型能否可靠映射订阅或按量。
- Claude 的 `CLAUDE_CONFIG_DIR` 在 macOS 上是否隔离钥匙串 OAuth 登录、钥匙串条目的具体服务名与账户名；仅凭允许的二进制字符串未确认命名。`claude auth login` 没有帮助中声明的 `--no-browser` 入口，静态证据不足以确认服务持有的无终端浏览器登录；原始 `ANTHROPIC_API_KEY` 或 `apiKeyHelper` 的安全写入与读取路径也待原生验证。在此之前配置页不提供 Claude OAuth 登录按钮。
- ZCode 的私有 `ZCODE_DATA_BASE_DIR` 是否隔离 OAuth 与 API key 服务商配置、`--no-browser` 链接是否可由服务稳定捕获并取消、是否有可用的用量或额度接口；目前适配器仍拒绝 OAuth 服务商。DSH 的原生配置界面或 `ctx.credentials.set` 能否提供不经过命令行参数和公共 JSON 的安全写入通路、是否有余额或用量接口。
- Host 提供的 DeepSeek 官方 API 文档称 `GET https://api.deepseek.com/user/balance` 可凭 API key 查询余额；本机未核实文档或接口，亦未联网。该服务商接口不得在原生检查授权前描述为已接通。
- 各 harness 的登录、私有目录权限与隔离、账户状态及用量读取都尚未做逐次授权的原生检查；Windows 未验证。

### 界面问题（用户报告）

- **Buddy 配置页与设置页在内容超出窗口高度时无法向下滚动。** 2026-09-29 用户在 0.20.0 的 Buddy 配置页上看到：Router 面板、harness 列表之后的模型列表与模型详情被截在窗口底部，页面无法继续向下滚动；设置页有同样的现象。初步判断（未在浏览器中核实）：控制台外壳是固定高度且整体 `overflow: hidden`（`apps/console/src/styles.css` 的 `.app-shell`、`.main-content`、`.view-panel`），只有内部区域自己滚动；Buddy 配置页把几块面板纵向堆叠，外层没有滚动区域，所以最下面的模型区域被压缩。设置页的 `.settings-page` 本身声明了 `overflow-y: auto`，原因仍需查明。建议：两页的外层内容区可以整体滚动，模型列表与详情在其中保留最小高度，并在常见笔记本窗口高度和窄屏下验证。本文要在这两页增加账户、额度入口和备份预检入口，必须先修复此问题。

## 决定

1. **两种账户来源。** 每个 harness 默认“沿用本机登录”：服务只读取登录状态和可无副作用读取的用量，不发起登录、登出或密钥变更；用户在原生工具中自行处理这些变更。选择“Worker 独立账户”时，服务为该 harness 建立私有配置目录，让 harness 自己的登录或密钥机制保存凭据；服务不解析凭据，不把凭据写入数据库或日志。此选择让 Worker buddy 使用 BYOK 或 OAuth 账户，并使 Host buddy 本人的 Codex、Claude Code 登录与额度保持独立。
2. **一个生效来源与会话边界。** 初版每个 harness 同时只有一个生效的账户来源。切换仅作用于之后开始的 attempt，运行中的 attempt 保留原账户；切换后，与旧账户绑定的原生会话续做改为重建新会话，按 harness 版本变化的既有规则记录。同一 harness 的多账户并存需扩展配置身份 `(adapter, provider, model, effort)`，留待后续决定。
3. **密钥输入与可见性。** 密钥仅从 CLI stdin 或已认证的“Buddy 配置”表单进入，只经过进程内存与私有通道（CLI 到服务的本机 RPC、配置页到服务的本机请求、服务到原生进程的 stdin 或 stdio 管道）交给 harness 的原生机制；不得出现在命令行参数中，也不得进入事件、日志、SQLite（包括请求幂等与审计记录）、备份清单、成果或评价元数据。携带密钥的请求不按普通请求重放保存，丢失回复时由用户重新输入。配置页只显示是否已配置、账户类型、套餐和脱敏状态，绝不回显密钥。Codex API key 可经原生 `codex login --with-api-key` 的 stdin 或 app-server `account/login/start` 的 `apiKey` 变体写入私有主目录，两者都只走私有管道，实现时择一并在原生检查中确认。其他 harness 未确认安全入口时拒绝相应设置。
4. **系统凭据库的有界例外。** 如果某 harness 对一种密钥没有原生凭据存储，服务只将该密钥存入系统凭据库（macOS 钥匙串、Windows 凭据管理器、Linux Secret Service），仅在相应 attempt 的原生子进程环境中注入；系统凭据库不可用时拒绝，不回退到明文文件。这是“服务不保存密钥”原则的有界例外：服务管理系统凭据库条目，但不把密钥纳入自身持久状态。Claude 原始 API key 是待验证候选；已安装 DSH 包有原生文件凭据库，不可预先归入“没有原生存储”。
5. **OAuth 登录。** 已核实可无终端启动的 harness，才在“Buddy 配置”提供“登录”：服务在私有目录启动 harness 自己的流程，把授权链接仅交给当前已认证用户的浏览器，完成后刷新脱敏状态。服务持有进程，设置时限并允许取消；授权链接不进入事件与日志。登录必须由用户本人在浏览器完成。尚不能证明无终端流程的 harness 只给出原生工具操作指引。沿用本机登录时此入口始终不执行账户变更。
6. **计费方式与路由。** 每个账户由原生账户信息标注“订阅／按量／未知”，无法判定时标为“未知”，并按“按量”对待。按量及未知配置默认不进入自动路由候选；用户可在“Buddy 配置”逐一开启。Host buddy 明确指定一个已启用的按量配置仍允许执行，黑板记录其计费方式。账户用量仅用于显示，不参与路由；按额度路由仍属于 [ADR-012](012-bounded-control-overhead.md) 未批准的部分。
7. **有界用量快照。** 每个账户保留一份有界快照：额度窗口、已用比例、重置时间、余额或额度、来源（主动查询／运行中观察／服务商接口）及查询时间；原生未给出的字段保留未知，不推算。刷新借用 harness 健康的按需限频，最多每三分钟一次，不增加周期扫描；服务商接口须先核实并另行授权原生检查。
8. **操作入口。** CLI 草案沿用现有单命令加 JSON 对象参数的风格，增加 `accounts`（查看账户）、`account-login`（启动独立账户登录，密钥从 stdin）、`account-logout`（独立账户登出）、`account-remove`（移除独立账户）、`usage`（查看快照）；具体参数与错误码留待实现契约确定。已认证“Buddy 配置”在各 harness 行增加“账户”和“额度”，只展示脱敏事实及经验证可用的操作。
9. **备份与权限。** 账户私有目录权限为 `0700`、其中凭据文件为 `0600`；账户私有目录与系统凭据库条目不进入滚动备份，也不列入备份清单。恢复后独立账户需要重新登录。模型驱动的原生进程在运行中原本就能读取自己使用的凭据，独立账户不能阻止这一点；它隔离 Worker buddy 与本机登录，并允许服务管理 Worker buddy 账户。
10. **验收门槛。** 每种原生登录流程、私有目录隔离和用量读取都需要逐次授权的原生检查；登录时由用户本人完成浏览器授权。测试只能先用私有根与模拟 CLI 验证不泄露、切换、限频、备份排除和会话重建。Windows 保持未验证，不得因静态代码或 macOS 结果宣称可用。
11. **私有目录的位置与备份安全。** 2026-09-29 的 0.20.0 升级曾因 Codex Router 在 `attempts/` 中留下指向本机 `auth.json` 的符号链接而在备份阶段回滚；当时以按位置排除和进程停止后删除链接修复，但同类问题会随任何新的私有目录重演，且要到安装时才暴露。因此实现本文时：`attempts/` 只保存回执、结果、日志等黑板证据，harness 私有主目录、凭据快照与 Worker 独立账户目录一律放在备份本来就排除的区域，已有看板中的旧位置继续按位置排除；增加不变式测试，每个 harness 用模拟 CLI 完成一次 attempt 后对状态目录做备份检查，出现不可备份的路径即失败；`BACKUP_UNSAFE_PATH` 的错误详情给出状态目录内的相对路径；提供只读的备份安全预检，Host 在请求安装授权前运行，把问题暴露在授权之前。

## 考虑过的方案

- **服务充当通用密钥库。** 会扩大服务与备份的秘密暴露面，并重复 harness 自己的凭据机制；只在原生无存储时采用第 4 条限定的系统凭据库例外。
- **服务管理本机共用登录。** 登录、登出或替换密钥可能登出用户本人作为 Host buddy 使用的 Codex、Claude Code；用户已否决。
- **维持现状。** 不支持 Worker buddy 独立 BYOK，Host buddy 与 Worker buddy 共用额度，额度问题往往到失败后才知道。
- **让用量直接参与路由。** 用量快照的完整度与时效性因 harness 而异，也改变 ADR-012 的未批准边界；暂不实施。

## 影响

- 服务需在 `harness_discovery.py` 与 `harness_health.py` 增加账户来源、私有主目录与限频状态；各 `adapters/codex*.py`、`claude*.py`、`zcode*.py`、`dsh.py` 及 DSH bridge 需按已核实能力接入认证检查、登录进程、取消与脱敏用量。Codex 当前的 ChatGPT 套餐限制和 Router 私有目录的本机 `auth.json` 链接尤其需要按账户来源重新划界；ZCode 当前 OAuth 拒绝也需单独解决。
- `backup.py` 应显式保持账户私有目录及系统凭据库排除，并验证 SQLite、事件、回执、备份清单和评价元数据不含秘密。只保存非秘密账户设置与有界快照，优先使用现有 `meta`；若需要修改 schema，按既有规则另行确认。对外 CLI、服务与控制台契约版本需升级，并由迁移与兼容性检查记录。
- 第 11 条需要调整各适配器 attempt 私有内容的位置（Codex 的 `native/codex-home`、ZCode 服务商快照等），保留对旧位置的排除，并新增备份安全预检命令与覆盖全部 harness 的不变式测试；预检属于对外 CLI 契约，按第 8 条一并确定名称与参数。
- 先修复背景中记录的 Buddy 配置页与设置页无法滚动的问题，再增加下述入口。
- `apps/console/src/BuddyConfig.tsx` 的 harness 行及详情增加“账户”“额度”和经核实的操作入口；CLI 增加第 8 条草案命令。需同步更新 `docs/reference/harnesses.md`、`workers.md`、`codex.md`、`claude.md`、`cli.md`、`console.md`、`operations.md`、`evaluation.md`，以及 README 与共享 `SKILL.md` 摘要；状态为提议期间不把这些入口写成已实现。
- 实现测试使用私有状态与运行时根、模拟 CLI 与系统凭据库替身，覆盖账户来源切换、运行中 attempt 固定账户、续做重建、秘密不落黑板与备份、按量候选默认排除、显式选择、用量展示与限频。源代码完成、原生检查、安装及用户配置变更分别记录，不互相代替。
