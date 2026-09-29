# ADR-019：Worker 独立账户、用量可见性与私有目录

## 状态

已接受：用户于 2026-09-30 决定开始 B 批实施，并修订第 6、7 条。第一阶段实现计费标注、额度耗尽的候选过滤、Codex 按需额度查询及原生检查方案，同时完成控制台文案、取消发起者与审阅能力验证记录；第一阶段源码与验证已完成，验收见[记录](../acceptance/worker-accounts-phase1-0.23.0.md)；当前暂停，等待合并与安装授权。第 11 条另批实施，目前尚未并行开展；用户通知其合入 `socu/buddy-core` 后，先合入再继续第二阶段的账户来源、登录、密钥与权限功能。日常安装、用户配置变更及每次原生检查均需单独授权。以下静态核查不等于登录、隔离、用量读取或 Windows 验收。

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

- **已修复：Buddy 配置页与设置页在内容超出窗口高度时无法向下滚动。** 用户于 2026-09-29 在 0.20.0 报告该问题；A 批在 0.22.0 源码候选中完成修复。真实浏览器确认 Buddy 配置页的模型区被 flex 压至零高度，设置页的 Grid 行则将卡片压缩并由卡片自己的 `overflow: hidden` 裁掉内容，因此外层已有 `overflow-y: auto` 仍无可滚动范围。两页现由外层整体滚动，模型区保留最小高度，设置卡片保持内容高度；1280×720、1440×900 与 390 宽度的合成预览验证见[验收记录](../acceptance/console-routing-fixes-0.22.0.md)。本文后续账户、额度和备份预检入口可沿用该布局；日常安装另行授权。

### 符号链接与备份事故

两次日常安装都在升级前的备份阶段失败，服务按设计回滚，数据与运行中的工作都没有受影响，但安装只能中止：

- **2026-09-29，0.20.0。** Codex Router 的只读调用在 `attempts/<runId>/<attemptId>/native/codex-home/` 建私有 CODEX_HOME，把 `auth.json` 做成指向用户 `~/.codex/auth.json` 的符号链接，结束后不删。看板上有 4 个这样的目录，备份遇到链接即拒绝（`BACKUP_UNSAFE_PATH`）。修复 `0ca7bba` 按位置排除该目录与 ZCode 服务商快照，并在进程确认停止后删除该链接。
- **2026-09-30，0.21.0。** 用户配置的 DSH 快速 Router 在 `attempts/<runId>/<attemptId>/no-tool-<hex>/dsh-home/` 建私有 DSH 目录，其中 `profiles/node_modules` 含 pnpm 式符号链接；6 个目录共 2,886 个链接，备份再次拒绝。Codex 快速 Router 的 `no-tool-<hex>/native/codex-home/auth.json` 也不在第一次的排除范围内，只是尚未运行过。修复 `f742488` 按位置排除无工具调用的私有目录，并加了兜底：`attempts/` 中其他位置的链接或特殊文件只跳过、不跟随，记入备份清单，不再让备份失败；仍被拒绝时错误给出状态目录内的相对路径。

根本原因不是某个 harness，而是结构：每个适配器自行决定在 `attempts/` 里放什么，备份却把整棵 `attempts/` 当作证据复制，只用一份逐次追加的例外清单排除私有内容。harness 自带的工具（pnpm 的 node_modules、凭据链接、socket）随时可能在私有目录里产生链接或特殊文件，新功能一上线就可能让下一次升级失败，而且要到安装时才暴露。2026-09-30 日常看板上 attempt 目录中的内容说明了这种混放：

| 类别 | 例子（出现的 attempt 目录数） | 目前是否进入备份 |
| --- | --- | --- |
| 黑板证据 | `runner.stdout.log`/`runner.stderr.log`（313）、`task.txt`（263）、`turn-input.json`（246）、`turn-output.json`（209）、`activity.json`（198）、`decision-input.json`/`decision-output.json`（38）、`harness-selection.json`（52）、`no-tool-<hex>/call-N/` | 是，应当 |
| attempt 作用域的凭据 | `agent-credential.json`（246）、`inquiry.json`（174）、`finish-bridge.json`（110） | 是，不应当 |
| 可能含 API key 的服务商快照 | `builtin-provider.json`/`personal-provider.json`（110） | 0.20.0 起排除，仍留在磁盘上 |
| harness 私有内容 | `claude-private`（16）、`sessions`（32）、`native`（5）、`no-tool-<hex>` 下的私有主目录（7）、旧 DSH 运行目录 | 部分按位置排除，其余照常复制 |

0.21.0 的兜底只保证安装不再因此中止；它仍是逐次追加的例外清单，也仍把凭据类文件复制进备份。第 11 条给出完整的解决办法并取代这种做法。

## 决定

1. **两种账户来源。** 每个 harness 默认“沿用本机登录”：服务只读取登录状态和可无副作用读取的用量，不发起登录、登出或密钥变更；用户在原生工具中自行处理这些变更。选择“Worker 独立账户”时，服务为该 harness 建立私有配置目录，让 harness 自己的登录或密钥机制保存凭据；服务不解析凭据，不把凭据写入数据库或日志。此选择让 Worker buddy 使用 BYOK 或 OAuth 账户，并使 Host buddy 本人的 Codex、Claude Code 登录与额度保持独立。
2. **一个生效来源与会话边界。** 初版每个 harness 同时只有一个生效的账户来源。切换仅作用于之后开始的 attempt，运行中的 attempt 保留原账户；切换后，与旧账户绑定的原生会话续做改为重建新会话，按 harness 版本变化的既有规则记录。同一 harness 的多账户并存需扩展配置身份 `(adapter, provider, model, effort)`，留待后续决定。
3. **密钥输入与可见性。** 密钥仅从 CLI stdin 或已认证的“Buddy 配置”表单进入，只经过进程内存与私有通道（CLI 到服务的本机 RPC、配置页到服务的本机请求、服务到原生进程的 stdin 或 stdio 管道）交给 harness 的原生机制；不得出现在命令行参数中，也不得进入事件、日志、SQLite（包括请求幂等与审计记录）、备份清单、成果或评价元数据。携带密钥的请求不按普通请求重放保存，丢失回复时由用户重新输入。配置页只显示是否已配置、账户类型、套餐和脱敏状态，绝不回显密钥。Codex API key 可经原生 `codex login --with-api-key` 的 stdin 或 app-server `account/login/start` 的 `apiKey` 变体写入私有主目录，两者都只走私有管道，实现时择一并在原生检查中确认。其他 harness 未确认安全入口时拒绝相应设置。
4. **系统凭据库的有界例外。** 如果某 harness 对一种密钥没有原生凭据存储，服务只将该密钥存入系统凭据库（macOS 钥匙串、Windows 凭据管理器、Linux Secret Service），仅在相应 attempt 的原生子进程环境中注入；系统凭据库不可用时拒绝，不回退到明文文件。这是“服务不保存密钥”原则的有界例外：服务管理系统凭据库条目，但不把密钥纳入自身持久状态。Claude 原始 API key 是待验证候选；已安装 DSH 包有原生文件凭据库，不可预先归入“没有原生存储”。
5. **OAuth 登录。** 已核实可无终端启动的 harness，才在“Buddy 配置”提供“登录”：服务在私有目录启动 harness 自己的流程，把授权链接仅交给当前已认证用户的浏览器，完成后刷新脱敏状态。服务持有进程，设置时限并允许取消；授权链接不进入事件与日志。登录必须由用户本人在浏览器完成。尚不能证明无终端流程的 harness 只给出原生工具操作指引。沿用本机登录时此入口始终不执行账户变更。
6. **计费方式与路由。** 每个配置按原生账户信息标注“订阅／按量／未知”：沿用本机登录时采用 Codex 的 `account/read`、Claude 的 `auth status`、ZCode 服务商访问类型或 DSH 的 DeepSeek API key 接入事实，无法判定即为“未知”。计费方式只用于标注和显示，不影响自动路由，所有已启用的配置照常参与。只有新的原生观测明确报告额度受限、额度错误或余额为零时，才暂时将该配置移出自动路由候选；重置时间过后或新的观测显示可用时恢复，额度未知时不排除。Host buddy 明确指定已耗尽的配置时给出提醒，保留其选择，不静默改选。
7. **用量与额度沿用 0.21.0 的记录。** ADR-018 第 22、23 条已实现逐次执行的 token 用量、各 harness 最新的原生额度观测（含来源、观测时间、窗口与重置后的有效性判断），以及窗口使用率达到 90% 或原生明确受限时的提交提醒，CLI 与控制台均已展示（[验收记录](../acceptance/host-workflow-0.21.0.md)）。本文不另建用量快照；第一阶段沿用本机登录，第二阶段将观测与提醒按生效账户来源区分，切换后旧观测不适用于新账户。Codex 的 `account/rateLimits/read` 作为沿用本机登录时的只读主动查询来源，不发起模型回合；查询借用 harness 健康的按需限频，最多每三分钟一次，不新增周期扫描。服务商接口（如 DeepSeek 余额）须先核实并另行授权原生检查。额度耗尽会暂时移出自动路由，其余用量信息只用于显示和提醒。
8. **操作入口。** CLI 草案沿用现有单命令加 JSON 对象参数的风格，增加 `accounts`（查看账户）、`account-login`（启动独立账户登录，密钥从 stdin）、`account-logout`（独立账户登出）、`account-remove`（移除独立账户）；查看用量沿用 0.21.0 已有的输出，按账户汇总的需求在实现时再定；具体参数与错误码留待实现契约确定。已认证“Buddy 配置”在各 harness 行增加“账户”和“额度”，只展示脱敏事实及经验证可用的操作。
9. **备份与权限。** 账户私有目录权限为 `0700`、其中凭据文件为 `0600`；账户私有目录与系统凭据库条目不进入滚动备份，也不列入备份清单。恢复后独立账户需要重新登录。模型驱动的原生进程在运行中原本就能读取自己使用的凭据，独立账户不能阻止这一点；它隔离 Worker buddy 与本机登录，并允许服务管理 Worker buddy 账户。
10. **验收门槛。** 每种原生登录流程、私有目录隔离和用量读取都需要逐次授权的原生检查；登录时由用户本人完成浏览器授权。测试只能先用私有根与模拟 CLI 验证不泄露、切换、限频、备份排除和会话重建。Windows 保持未验证，不得因静态代码或 macOS 结果宣称可用。
11. **私有目录与备份范围。** 背景中的两次事故说明，链接问题要靠存放结构解决，不能靠逐次追加的例外清单。实现本文时一并完成以下各项；它们不依赖账户功能，可以先于其余决定实施。
    - **分区。** `attempts/<runId>/<attemptId>/` 只保存服务、Worker 与控制器写出的黑板证据：控制记录、任务与轮次的输入输出、结构化结果、路由输入输出、活动、日志、补丁与清单。harness 私有主目录（Codex 的 CODEX_HOME、DSH 的 DSH_HOME 与 profile、ZCode 的存储与服务商快照、Claude 的私有设置目录）、原生会话、无工具调用的私有根，以及 attempt 作用域的凭据文件（`agent-credential.json`、`inquiry.json`、`finish-bridge.json` 及以后同类文件），一律放在状态目录中备份本来就排除的 harness 私有区（现有 `harnesses/` 之下，按 adapter、goal 与 attempt 分目录）。证据文件只以普通文件存在，不写入链接。
    - **备份改为白名单。** 备份只复制 `attempts/` 中声明为证据的文件类别，不再"整棵复制再减例外"；白名单与各适配器写出的证据文件一起维护。白名单以外的内容不复制，数量与最多 20 个相对路径记入备份清单，并在 health 与控制台显示为需要处理的提醒；白名单内出现链接或特殊文件仍以 `BACKUP_UNSAFE_PATH` 拒绝并给出相对路径。这一条取代 0.21.0 的按位置排除与兜底跳过。
    - **生命周期。** 原生进程确认停止后，删除该 attempt 的凭据文件、凭据链接与服务商快照；续做需要的原生会话按 goal 保存在私有区，随受管工作区的清理一起回收。停止状态未知时一律保留，不当作已停止，由 `storage plan` 列出等待处理。
    - **链接规则。** 凭据只能以链接形式出现在私有区，并在停止后删除；备份、恢复、存储回收与清理都不跟随链接，也不向链接写入。Windows 的 junction 与其他重解析点按链接对待（尚未在真机验证）。
    - **已有看板的整理。** 升级在已验证备份之后、切换运行时之前，把旧位置中的私有内容移入私有区，已结束 attempt 的凭据文件与服务商快照直接删除，并在升级结果中列出处理数量与路径样例；停止状态未知的 attempt 不动。这属于日常数据变更，随该版本的安装一起由用户授权。
    - **防回归。** 不变式测试用模拟 CLI 驱动每个适配器的执行、审阅 Router、快速 Router 与续做路径，检查结束后 `attempts/` 中只有白名单内的普通文件、私有区的凭据已删除、备份不跳过也不拒绝任何内容；新增适配器或新文件类别必须先登记到白名单。提供只读的备份预检命令，报告将被复制、跳过和拒绝的内容及原因，不写任何数据；Host 在请求安装授权前运行，安装器在停机前先运行，发现问题就在切换前停下并给出路径。

## 实现状态（B 批第一阶段）

| 条目 | 当前实现状态 |
| --- | --- |
| 1，账户来源 | 第二阶段待实现；当前沿用本机登录，只读账户信息。 |
| 2，生效来源与会话 | 第二阶段待实现；第一阶段不切换账户。 |
| 3，密钥通道 | 第二阶段待原生入口验证后实现；本阶段没有密钥输入。 |
| 4，系统凭据库例外 | 第二阶段待决定与验证；没有明文回退。 |
| 5，OAuth | 第二阶段待逐次授权验证；当前只提供原生工具指引。 |
| 6，计费与候选 | 第一阶段源码已实现，模拟测试覆盖标注、耗尽过滤、恢复和明确选择提醒；原生读回仍待单次授权。 |
| 7，用量与额度 | 第一阶段实现 Codex 无模型按需查询、180 秒限频及持续耗尽状态；按账户来源区分留第二阶段。 |
| 8，操作入口 | 第一阶段提供审阅重验入口与计费展示；accounts/login/logout/remove 留第二阶段。 |
| 9，权限与备份 | 第二阶段待实现；独立账户私有区先依赖第 11 条。 |
| 10，验收 | 第一阶段源码、模拟与完整检查已通过；[原生检查方案](../design/adr019-native-checks.md) 已完成，本轮尚未取得单次执行同意。 |
| 11，目录与备份范围 | 本阶段不实施，目前没有并行工作；用户通知合入 `socu/buddy-core` 后再继续第二阶段。 |

审阅能力从适配器版本常量改为按 harness、版本和平台保存的验证记录。CLI 与 Buddy 配置可以显式授权当前版本的原生只读检查；新版本显示“新版本待验证”并沿用快速路由降级，失败保留原因且不开放能力。原有 macOS 0.157.0 证据作为数据证书保留；本轮没有把模拟测试当成 0.159.0 的原生验证。源码、原生检查与安装事实见[第一阶段验收](../acceptance/worker-accounts-phase1-0.23.0.md)。

## 考虑过的方案

- **服务充当通用密钥库。** 会扩大服务与备份的秘密暴露面，并重复 harness 自己的凭据机制；只在原生无存储时采用第 4 条限定的系统凭据库例外。
- **服务管理本机共用登录。** 登录、登出或替换密钥可能登出用户本人作为 Host buddy 使用的 Codex、Claude Code；用户已否决。
- **维持现状。** 不支持 Worker buddy 独立 BYOK，Host buddy 与 Worker buddy 共用额度，额度问题往往到失败后才知道。
- **按消耗高低或计费方式排序路由。** 用量快照的完整度与时效性因 harness 而异；只排除原生明确耗尽的配置，其余用量不参与候选排序。

## 影响

- 服务需在 `harness_discovery.py` 与 `harness_health.py` 增加账户来源、私有主目录与限频状态；各 `adapters/codex*.py`、`claude*.py`、`zcode*.py`、`dsh.py` 及 DSH bridge 需按已核实能力接入认证检查、登录进程、取消与脱敏用量。Codex 当前的 ChatGPT 套餐限制和 Router 私有目录的本机 `auth.json` 链接尤其需要按账户来源重新划界；ZCode 当前 OAuth 拒绝也需单独解决。
- `backup.py` 应显式保持账户私有目录及系统凭据库排除，并验证 SQLite、事件、回执、备份清单和评价元数据不含秘密。只保存非秘密账户设置，账户维度的额度观测沿用 0.21.0 的存储，优先使用现有 `meta`；若需要修改 schema，按既有规则另行确认。对外 CLI、服务与控制台契约版本需升级，并由迁移与兼容性检查记录。
- 第 11 条需要调整各适配器私有内容与凭据文件的位置、把备份改为证据白名单、在升级中整理已有看板，并新增只读备份预检命令与覆盖全部适配器的不变式测试；它不依赖账户功能，用户于 2026-09-30 同意把它拆出来先行实施。
- 先修复背景中记录的 Buddy 配置页与设置页无法滚动的问题，再增加下述入口。
- `apps/console/src/BuddyConfig.tsx` 的 harness 行及详情增加“账户”“额度”和经核实的操作入口；CLI 增加第 8 条草案命令。需同步更新 `docs/reference/harnesses.md`、`workers.md`、`codex.md`、`claude.md`、`cli.md`、`console.md`、`operations.md`、`evaluation.md`，以及 README 与共享 `SKILL.md` 摘要；状态为提议期间不把这些入口写成已实现。
- 实现测试使用私有状态与运行时根、模拟 CLI 与系统凭据库替身，覆盖账户来源切换、运行中 attempt 固定账户、续做重建、秘密不落黑板与备份、计费标注不影响路由、额度耗尽暂时排除与恢复、显式选择提醒、用量展示与限频。源代码完成、原生检查、安装及用户配置变更分别记录，不互相代替。
