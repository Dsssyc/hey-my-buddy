# 只联网一档：四家 harness 能不能把回合工具收窄到联网搜索与网页抓取

2026-11-06。

## 问题

画像面（[ADR-021](../decisions/021-router-buddy-planes-and-routing-evidence.md) 第 12 至 14 条）要用权限档位里的只联网一档运行：只用联网搜索与网页抓取，不碰本机文件，而且限制要在事前由 harness 自己生效（第 14 条 2026-10-13 与 2026-10-20 的补记；[ADR-023](../decisions/023-harness-integration-principles.md) "各角色共用一套档位"第 1 至 5 条）。开发者要据此定画像面用哪家 harness，本份逐家弄清：只经 harness 自己文档化的参数或单次调用的配置（不改用户的配置文件），能不能让一个回合里模型可用的工具只有联网搜索与网页抓取——没有执行命令、读写或搜索文件的工具，没有 MCP 工具，也没有能间接读文件的子代理、skill 或 hook；这种限制是 harness 强制的还是只靠指令约定；网页抓取在提供方的服务器上还是由本机的 harness 进程执行，本机取的能不能取 file:// 或 localhost；harness 有没有原生回报（初始化消息、事件）让产品核对本回合实际可用的工具或策略；关掉用户自己配置的 MCP、hook、skill、插件用哪个参数，与 [ADR-030](../decisions/030-harness-layer-scope.md)（用户的配置照原样用）怎样对照——只列事实，不下结论。四家的已装版本（日常黑板记下）：Codex CLI 0.164.0（产品经 app-server 驱动）、Claude Code CLI 2.1.312（产品以 print 模式加 stream-json 驱动）、DSH CLI 0.2.1-rc.2（产品经 ACP 驱动）、ZCode CLI 0.17.1（产品经 app-server 驱动；桌面 App 另用 3.x 版本号）。

## 看了什么

对照的起点是产品现有的落法（本仓库源码，2026-11-06 读）：`src/hey_my_buddy/protocol/capabilities.py` 的 `PERMISSION_LEVELS`，Codex 适配器的 `_permission_sandbox` 与 none 一档的 fast 模式，DSH 适配器的 `NONE_SCOPE_DISABLED_ROWS`，ZCode 适配器会话创建参数里的 `toolAllowlist`，Claude 适配器的 `permission_sandbox_settings` 与 `--tools` 列表。四家外部来源只读公开材料，全部 2026-11-06 读：

- Codex：公开源码在已装版本对应的 rust-v0.164.0 标签上。读了 app-server 协议定义（thread/start 的参数与应答）、回合工具的装配处、功能旗标表、`-c` 覆盖的解析与合并、联网工具的规格与装配条件，读到能回答五个问题为止；没有通读整个仓库。官方文档站重定向到不带版本的页面，除注明外未用作依据。
- Claude Code：官方文档（CLI 参考、无头运行、工具参考、hooks、MCP、skills、settings、Agent SDK 类型参考），页面不带版本、描述的是最新发布（读时 CHANGELOG 顶部是 2.1.318，文档页修改日期 2026-11-03/04，离 2.1.312 六个补丁号内）；官方 CHANGELOG（2.1.312 在列、无日期；npm registry 记录 2.1.312 发布于 2026-10-27）。没有读本机安装内容，没有实测。
- DSH：公开仓库（MIT）在已装版本对应的 dsh-v0.2.1-rc.2 标签上（2026-10-17 发布的预发布）。读了 CLI 参考、工具目录文档、基础 bundle 的行表，tool-web 与 web 各后端、skill、MCP 客户端、ACP 各包的说明与配置行；DeepSeek 官方的 Search 与 Fetch API 文档；npm registry 的版本记录。随包约 300 个插件没有逐个读。
- ZCode：官方文档站（zcode.z.ai，页面无版本无日期、只写桌面 App）与开源仓库（Apache-2.0）的主干——仓库只给 3.x（App 版本号）标签，没有 0.17.x 的 CLI 标签；主干里 CLI 的版本串恰是 0.17.1、App 的版本串 3.17.2，但主干与装好的 0.17.1 之间差多少提交无法从公开来源判断，本份的源码结论都按"主干 2026-11-06"记。另读官方反馈仓库里的第三方条目作旁证（2026-10 的报告）。没有读本机安装内容，没有运行 ZCode。

没有做任何模型回合或本机探针：这类原生核实要付费、由开发者逐次批准；公开来源回答不了的，下面写"不知道"，并说明怎样的探针才能定下来。

## 看到的事实

标注：〔源码〕＝已装版本（ZCode 为主干，见上）对应公开源码里的原文与条件；〔文档〕＝厂商文档自述；〔第三方〕＝社区或反馈仓库里他人的报告；〔记录〕＝本项目早前已记录的事实；〔推断〕＝我们的推断。参数与旗标名保持原文；引号里是原文短句。

### Codex CLI 0.164.0（CLI 形态，产品经 app-server 驱动）

1. **能不能只留联网工具：不能完全。**〔源码〕一个回合的工具在一处装配，模型可见的有：exec_command 与 write_stdin（shell）、apply_patch、update_plan、view_image、托管的 web_search、独立的 web.run、多代理工具、MCP 工具与动态工具。能经单次调用关掉的：`--disable shell_tool`（全局旗标，源码注明等价于 `-c features.shell_tool=false`）让 shell 工具整个不注册；`view_image` 与 `sleep_tool` 同为功能旗标（默认开）；`tools.update_plan.enabled=false` 关计划工具；`web_search=disabled` 移除托管联网搜索。thread/start 还收一个 `config` 参数：逐回合的覆盖表，按与 `-c` 相同的合并落到本回合（字段本身没有文档注释，语义读自消费它的代码）。关不掉的：apply_patch 没有功能旗标或配置键，按模型元数据注册；多代理工具没有功能旗标；用户配置的 MCP 服务器移除不了——`-c` 覆盖只增改不删，空表合并等于没写（`mcp_servers={}` 不生效），也没有工具允许清单机制。
2. **强制还是约定：关得掉的部分是强制。**〔源码〕功能旗标关闭时工具根本不注册（装配处的条件原文如此），不是靠指令；`web_search=disabled` 同样是工具不出现。沙箱（read-only 等）只管命令的文件与网络，不移除任何工具。
3. **联网在哪里执行：都在提供方服务器。**〔源码〕web_search 序列化为提供方 Responses API 的 web_search 工具类型，由提供方执行；装配只看提供方能力与 web_search 模式，不看沙箱的 networkAccess。web.run 默认关（开发中的旗标），启用后也是经远端 Search API 执行，不是本机抓取。一个常规回合里没有本机网页抓取工具，file:// 与 localhost 无从谈起。
4. **原生回报：有政策回显，没有工具集回报。**〔源码〕thread/start 的应答回显 sandbox 与 approvalPolicy（产品 review 档已在核对 sandbox 回显），不回显工具集；config/read 返回合并后的生效配置，含 web_search 模式（mcp_servers 是否出现在应答里是〔推断〕：读自序列化方式，没有端到端引文）。没有任何方法或事件报告本回合实际可用的工具集。
5. **关用户 MCP 等：没有逐次参数。**〔源码〕`-c` 与 `--enable/--disable` 只增改不删；app-server 的启动旗标里没有关 MCP 的；`CODEX_HOME` 环境变量可以把整个配置根指到别处（源码注释记默认 `~/.codex`）。与 ADR-030 对照（只列事实）：`-c`、`--disable`、thread/start 的 `config` 都是 harness 自己的单次调用参数，不改用户文件；`CODEX_HOME` 换的不是一档权限，而是这个进程读哪份用户配置。

### Claude Code CLI 2.1.312（CLI 形态，产品以 print 模式加 stream-json 驱动）

1. **能不能只留联网工具：文档层面能。**〔文档〕`--tools`："Restrict which built-in tools Claude can use"，取值可以是空串（全关）、default 或工具名列表；`--allowedTools` 是免批准名单，文档明确写"to restrict which tools are available, use `--tools` instead"，两者是两回事。`--tools WebSearch,WebFetch` 这个确切组合文档没有专门说；`--restricted` 的措辞（会移除 WebFetch 等，"unless you name them individually in `--tools`"）说明逐个点名联网工具是文档预期内的用法。文档明说这个旗标"doesn't affect MCP tools"——要连 MCP 一起拒，配 `--disallowedTools "mcp__*"`（裸工具名"removes the matching tools from Claude's context"）。子代理与 skill 的入口（Agent/Task、Skill）本身是内建工具，不列入 `--tools` 即不在上下文里；ToolSearch 也是内建工具，不列它则 MCP 工具改为启动时全量装载而非按需发现。
2. **强制还是约定：文档写的是可用性层面的强制**（限定可用、从上下文移除），不是免批准，也不只是指令；2.1.312 上没有我们的实测。
3. **联网在哪里执行：搜索在提供方服务器，抓取在本机。**〔文档〕WebSearch"runs a query against Anthropic's web search backend"，后端不可配置；WebFetch 由本机 CLI 取页、转 Markdown，再用一次单独的模型调用提炼；"WebFetch refuses `localhost` and any other hostname without a dot"，HTTP 自动升 HTTPS；file:// 文档没提。WebFetch 在 Manual 与 acceptEdits 模式要批准，auto 与 bypassPermissions 跳过提示。
4. **原生回报：有。**〔文档〕stream-json 的 system/init 事件报告"the model, tools, MCP servers, and loaded plugins"；Agent SDK 类型参考列出 `tools: string[]`、`mcp_servers`、`permissionMode`、`skills`、`plugins` 等字段（capabilities 字段要 CLI 2.1.205 起，2.1.312 已过）——产品可在回合开始核对本回合实际装载的工具。回合中被拒的调用以 permission_denied 系统消息出现（配 `--permission-prompts none` 时最终消息还汇总）。
5. **关用户 MCP、hook、skill、插件的参数（都是 harness 自己的单次调用旗标，不改用户文件）：**〔文档〕MCP：`--strict-mcp-config`——"Only use MCP servers from `--mcp-config`, ignoring all other MCP configurations"。hooks：`--settings '{"disableAllHooks": true}'` 逐次全关（用户与项目级的能关；受管设置里的 hooks 只有受管级的同键能关）；hooks 各层设置相加合并、无法只禁某一条，用户级的 hooks 在 `-p` 下照常装载。skills：skills 文档写拒掉 Skill 工具即全关，所以不把 Skill 列进 `--tools` 就没有入口，另有 `--disable-slash-commands`（"Disable all skills and commands for this session"）。插件：没有单独的关闭旗标；`--bare` 跳过 hooks、skills、命令、子代理、已装插件、MCP 服务器与 CLAUDE.md 的自动发现，`--setting-sources` 可以不装 user/project/local 任一层。与 ADR-030 对照（只列事实）：以上都经 harness 自己的参数单次生效、不改用户文件；`--bare` 与 `--setting-sources` 关掉的是用户配置的装载本身，超出"给一个权限档位"的词汇，ADR-030 的清单里没有这类动作。

### DSH CLI 0.2.1-rc.2（CLI 形态，产品经 ACP 驱动）

1. **能不能只留联网工具：能，用产品已在用的同一机制。**〔源码〕基础 bundle 的 `tool-web` 行同时提供 web_search 与 web_fetch 两个模型可见工具（"lets models search the web with web_search and retrieve pages with web_fetch"），行内配置还能单独关一个（"{ search: false }" 或 "{ fetch: false }"）；其余工具行（tool-bash、tool-fs、tool-fs-search、tool-skill、tool-subagent 系列、tool-workflow 等）逐行有公开的行 id，`--patch` 覆盖层按 id 置 `disabled: true` 即逐次不装（"Later layers win per row"）。只联网一档＝保留 tool-web 行、禁其余工具行，正是产品 none 一档（`NONE_SCOPE_DISABLED_ROWS`）去掉"也禁 tool-web"的变体。〔记录〕本项目 2026-10-06 的读包另记下 `commands` 行不能禁；公开文档没有这句话，本条按本仓库的记录转述。
2. **强制还是约定：强制。**〔源码〕行禁用即插件不装、工具不提供；〔记录〕产品 none 一档在实跑中工具也不出现——capabilities 里 DSH 的 none 记为 enforced 的依据即此。
3. **联网在哪里执行：搜索在提供方服务器，抓取在本机、但只许公共目的地。**〔源码〕搜索的默认后端是 deepseek-official：CLI 进程只发 API 请求，"Search is a full auxiliary model request with server-side retrieval"，检索在 DeepSeek 的服务器上；抓取的默认后端 web-fetch-http 由本机进程直接 HTTP 取页，且这一版"the provider rejects non-public destinations before connecting"、"accepts only public HTTP(S) destinations"——回环与私网取不到，file:// 也不在允许的目的地里。〔源码〕主干（下一版的方向）已把抓取改为允许回环与私网；预发布以天计，升级要重核。DeepSeek 另有独立的 Search 与 Fetch API（0.2.1-rc.2 的发布说明提到），挂载了但不是默认。
4. **原生回报：会话内没有，组合态有。**〔源码〕ACP 的 initialize"advertises only mounted support"，session/new 只回模型与推理强度这些配置项；没有工具清单的事件或字段（会话进行中的 tool_call 事件是逐次调用的事实，不是事前的工具面）。`--dump-config` 不启动、不调模型即打印组合后的行，每行注明来源文件；禁用态随之打印这一点由条目方言与实现推出，文档没有整句写明。它能核对补丁是否按行生效，但它是组合态，不是运行时的工具面。
5. **关用户 MCP、skill、插件：同一 `--patch` 机制，没有专门旗标。**〔源码〕MCP 服务器逐个是补丁行（写在 profile 或 DSH_HOME 的 cordis.patch.yml 里），且"no MCP server is enabled by default"；没有一个行或旗标能一键全禁，要按行 id 逐个置 disabled。skills：行 skill、skill-filesystem、tool-skill 可禁行，或把 skill-filesystem 配成 `includeDefaultRoots: false` 加空的 customSkillDirs，即不扫任何目录（默认扫项目与用户级的五个根，含 `~/.dsh/skills` 与 `~/.agents/skills`）。hooks：仓库文档里没有默认装载的用户 hooks，只有把别家 harness 的 hooks 配置接进来的实验性桥接插件。与 ADR-030 对照（只列事实）：`--patch` 是产品表达工具范围已在用的同一公开机制，单次调用生效、不改用户文件。

### ZCode CLI 0.17.1（CLI 形态，产品经 app-server 驱动；源码读的是主干，见"看了什么"）

1. **能不能只留联网工具：内建工具能，MCP 工具不知道。**〔源码〕session/create 收 `toolAllowlist`（名字数组，另有 toolDenylist）：注册处的过滤是穷尽的——给了清单，清单外的内建工具不注册；空清单即全无内建工具（产品 none 一档已这样用，〔记录〕装好的 0.17.1 上实跑成立）；名字与内建注册表精确匹配（有 web_search 到 WebSearch 一类的别名折算），WebFetch 与 WebSearch 都在表里，未知名字不报错、只是不匹配。所以 `toolAllowlist: ["WebSearch", "WebFetch"]` 应只剩这两个内建工具——这是〔推断〕，依据是主干源码的注册逻辑，装好的 0.17.1 上没有实测。MCP 工具（mcp__ 前缀）与 app__ 插件工具不走这个注册过滤，公开源码里只见权限层（denylist 与显式允许）把着它们；allowlist 是否在更晚的阶段滤掉 mcp__ 名字，公开源码没有读到，**不知道**——要一次只联网的原生根回合看模型实际见到哪些工具才能定。
2. **强制还是约定：内建工具是注册层强制**（不注册即不提供，源码条件明确）；MCP 工具见上，不知道。
3. **联网在哪里执行：搜索在提供方服务器，抓取在本机。**〔源码〕WebSearch 是 provider_native 的 web_search 工具，经一次内部模型请求在提供方执行（工具描述写明 "US-only"）；WebFetch 在本机进程取页：只支持 http 与 https（别的协议抛 UnsupportedProtocol，file:// 取不了），拒绝 localhost、*.local、无点主机名与非公共 IP，HTTP 升 HTTPS，跨主机重定向不跟随，按 URL 缓存 15 分钟；权限元数据标记要批准，另有约 90 个文档域名的预放行表。
4. **原生回报：有模式回显，没有工具集回报。**〔源码〕session/create 应答的 settings 里有 mode.current 与 permission.mode（产品对 mode 回读的用法已在 0.17.1 上落过）；没有 toolAllowlist、工具清单或 MCP 服务器清单的回报。runtime/capabilities 只有七个与会话处理有关的布尔值，不谈工具。分发表里有一个 mcpList 方法，其内容本份没读——**不知道**它是否足用作 MCP 侧的核对。
5. **关用户 MCP、skill、hook、插件：MCP 有，其余没有。**〔源码〕session/create 的 `mcpServers` 是"完整覆盖配置"：不传则用户与项目配置文件里的 mcp.servers 照常装载；传了——包括空数组——就整体替换，空数组之后只剩插件发现的 MCP 服务器（插件的服务器总会并入）。skills、hooks、插件没有逐会话的关法：创建参数里没有对应字段，运行时配置从用户的配置装载。与 ADR-030 对照（只列事实）：`toolAllowlist`、`mcpServers` 都是 harness 自己的会话创建参数，单次调用生效、不改用户文件；产品的 none 一档已经在传 `toolAllowlist: []` 与 `mcpServers: []`。

### 对照表

| harness | 只留联网工具 | 强制还是约定 | 抓取在哪里执行 | 原生回报 | 关用户 MCP 等的参数 |
| --- | --- | --- | --- | --- | --- |
| Codex CLI 0.164.0（app-server） | 不能完全：shell、view_image、计划、联网搜索可关（`--disable shell_tool` 等、`web_search=disabled`、thread/start 的 `config` 覆盖），apply_patch、多代理工具关不掉，用户 MCP 移除不了 | 关掉的部分强制（不注册） | 搜索：提供方服务器（托管 web_search）；没有本机抓取工具 | thread/start 回显 sandbox 与批准策略；config/read 给生效配置；无工具集回报 | 无逐次参数；`-c` 只增改不删；`CODEX_HOME` 换整个配置根 |
| Claude Code CLI 2.1.312（print 模式） | 文档层面能：`--tools WebSearch,WebFetch` 加 `--disallowedTools "mcp__*"` | 强制（文档措辞；未实测） | 搜索：Anthropic 的服务端；抓取：本机 CLI（拒绝 localhost 与无点主机名；file:// 未提） | 有：system/init 报告 tools、mcp_servers、permissionMode 等 | MCP 用 `--strict-mcp-config`；hooks 用 `--settings '{"disableAllHooks": true}'`；skills 不列 Skill 或 `--disable-slash-commands`；插件无单独旗标（`--bare`、`--setting-sources`） |
| DSH CLI 0.2.1-rc.2（ACP） | 能：`--patch` 禁 tool-web 之外的工具行；tool-web 行内可再单独关 search 或 fetch | 强制（行禁用即不装） | 搜索：DeepSeek 的服务器（默认后端）；抓取：本机 HTTP，此版只许公共目的地（回环、私网、file:// 都取不到） | 会话内无工具集回报；`--dump-config` 无模型核对组合态（含禁用行） | MCP 逐服务器是补丁行、默认全关、无一键全禁；skills 禁行或 `includeDefaultRoots: false`；同一 `--patch` 机制 |
| ZCode CLI 0.17.1（app-server；源码为主干） | 内建工具能：`toolAllowlist: ["WebSearch", "WebFetch"]`；MCP 工具是否受管不知道 | 内建：注册层强制；MCP：不知道 | 搜索：提供方服务器（provider_native web_search，US-only）；抓取：本机（拒绝 file://、localhost、无点主机名与非公共 IP） | settings 回显 mode 与 permission.mode；无工具集或 allowlist 的回报 | `mcpServers: []` 整体替换用户与项目的 MCP（插件服务器仍并入）；skills、hooks、插件无逐会话关法 |

## 取了什么，没取什么

这份只是调研，不替开发者选画像面的 harness，也没有改动任何代码；结论留给 [ADR-021](../decisions/021-router-buddy-planes-and-routing-evidence.md) 第 12 至 14 条与 [ADR-023](../decisions/023-harness-integration-principles.md) 的后续讨论。空着的格子就是逐次授权的原生核实要补的，每项一次付费回合即可定：

- Claude 的 `--tools WebSearch,WebFetch` 加 `--disallowedTools "mcp__*"` 在 2.1.312 上的实际工具集——init 事件的 tools 与 mcp_servers 字段本身就能核对；
- ZCode 装好的 0.17.1 上 `toolAllowlist: ["WebSearch", "WebFetch"]` 的实际效果，尤其 MCP 工具与插件服务器是否仍可见（源码读不到的那段）；
- DSH 只留 tool-web 行时回合实际可见的工具集——dump-config 核对组合之外，还要一次回合看 tool_call 事件；
- Codex 关掉 shell_tool 等之后回合实际可见的工具面——apply_patch、多代理工具与用户 MCP 工具是否仍出现。

没取的：把某一家的强弱读成选择。四家在"能不能只留联网"上事实不同（两家能、一家文档层面能、一家不能完全），强制与抓取位置也不同，怎样权衡是开发者的决定。也没有做任何实测：本份全部依据公开来源与本仓库已有记录。

## 时效

- 任一家升级都要重核：Codex 越过 0.164.0（功能旗标表与 `-c` 合并语义都可能动）；Claude Code 越过 2.1.312（本份引的文档页描述最新发布，读时已是 2.1.318，措辞要对照）；DSH 的 rc 以天计（0.2.2-rc.1 已于 2026-11-04 发布，主干已把 web_fetch 改为允许回环与私网，与本份记的 0.2.1-rc.2 相反）；ZCode 越过 0.17.1（主干没有 0.x 标签，装好的版本与主干的关系每次都要重判）。
- ZCode 给 CLI 发布标签，或其反馈仓库里"session/create 收工具配置"的请求落地时，ZCode 一节的推断可以升级为核对。
- Claude 的 `--tools` 对 MCP 工具的措辞、hooks 的合并规则、`--bare` 的范围，DSH 的行表或 `--patch` 语义（按行整体替换 config）、DeepSeek Search 与 Fetch API 或默认后端，任一变动。
- 本仓库 DSH 适配器对 2026-10-06 读包的重记，会使本份转述的那条 `commands` 行记录过时。
