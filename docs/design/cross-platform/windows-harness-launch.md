# Windows 上发现与启动 harness：设计

2026-10-21，供 Host 审查的设计，基于输入提交 `14fead7da7f3744516dabdab8a76b214be258d27`（源码 0.50.1）。本文的“现状”仅描述这个提交中产品对 CLI 的调用代码；没有运行 Claude Code CLI、Codex CLI、ZCode CLI 或 DSH CLI，没有观察任何真实 CLI 版本在 Windows 上的行为。行号对应输入提交；实现、Windows 验证和安装均未发生。

## 范围与结论

依据[跨系统计划](plan.md)的“Windows 分批计划”、开发者的五项决定，以及[清单](inventory.md)的 W01、W02、T03。本项属于第一批的入口发现、启动和可执行测试夹具，不改变第二批的执行所有权、Job 生命周期、停止证据、ACL 与耐久性承诺。目标环境为 Windows 11 x64、本地 NTFS、同一登录会话和 Python 3.12；WSL2 按 Linux，Windows 10 未验证，Windows ARM、网络盘、FAT/exFAT 和 Windows 服务形态不在首发支持范围。Claude skill 的目录联接、检出的名字与链接限制、断电保证以及 C-Two 的相关决定照原计划分批处理，不能用本设计声称这些工作已完成。

建议采用一种启动规则：产品只把独立 argv 交给原生可执行程序，Windows 上不把 harness 的动态参数交给 `cmd.exe` 或 PowerShell。原生 `.exe` 直接启动；对可识别的 npm Node `.cmd` shim，只读取其启动描述，解析为原生 `node.exe` 与原来的 JS 入口后直接启动；未知脚本明确拒绝。任务和角色文字继续用现有原生标准输入协议。这消除了 cmd 对整个命令的再次解析，也覆盖必须留在 argv 中的 Claude 模型名和 schema、ZCode 的 cwd、DSH 的 patch 路径。

这是一项有意收窄入口类型的设计：当前已有的任意 `.cmd` 与 `.ps1` 启动不再保留；普通 `.bat` 也不增设 shell 支持。已识别的 npm shim 是静态启动描述，不是任意 batch 解释器。若安装只提供未知脚本，报告该入口不可启动，并在诊断中写明原因和可选择的原生入口；不能回退到 shell 或更宽权限。四个 CLI 的实际 Windows 发行入口及 shim 识别结果均未验证，不能据此声称四家目前都可用。

[ADR-023](../../decisions/023-harness-integration-principles.md) 第 1、2、7、8 条要求保留原生能力、如实声明差异、复用执行载体和仅作能力检查；因此四个运行器的 stream-json、App Server、ACP 协议及权限映射继续沿用，不改用另一套“Windows 专用运行协议”。解析 shim 只解决如何启动原来的程序，不检查厂商代码以证明工具限制。[ADR-030](../../decisions/030-harness-layer-scope.md) 第 2、3、6、8 条继续适用：不改用户的配置、登录、MCP、钩子或主目录，不执行登录 shell，角色文本仍在任务正文最前面。

## 1. 现在怎样发现和启动

四家共用 `src/hey_my_buddy/buddy/harnesses/discovery.py`。`HARNESSES` 和 `_NAMES`（25–26 行）将名字映射为 `claude`、`codex`、`zcode`、`dsh`；`candidate_snapshot`（224–266 行）依次枚举手动路径、版本管理器默认版本目录、常见目录、app 路径、Windows 注册表 Path 和调用方 PATH；`_priority` 与 `_discover`（570–572、772–831 行）按来源优先级选候选，同层选择较高版本。手动候选失败后仍会尝试其他候选，已有测试 `tests/python/buddy/harnesses/test_harness_discovery.py:97` 对此作了约定。下文的 `claude/`、`codex/`、`zcode/`、`dsh/`、`runtime_selection.py` 和 `discovery.py` 均相对于 `src/hey_my_buddy/buddy/harnesses/`，其他产品路径相对于 `src/hey_my_buddy/`；测试路径相对于 `tests/python/`。

Windows 的 `_variants`（212–215 行）固定生成 `.exe`、`.cmd`、`.ps1`、无后缀四种名字，不读取 PATHEXT；它也用于 `has_candidate`（218–221 行）、相邻 Node 与 Node 候选（255–265、587–622 行）。因此普通 PATH 目录里的 `codex.bat` 或 `.com` 不会仅因列在 PATHEXT 就被这套发现枚举出来；手动路径则不受这张后缀表限制。`native_environment`（283–301 行）保留 PATHEXT，只是把变量传下去，不等于发现使用了它。

`_registry_paths`（174–191 行）读取 HKCU Environment 和 HKLM Session Manager Environment 的 Path，丢弃注册表类型，按路径分隔符拆分，未展开 `REG_EXPAND_SZ`。`_common_dirs`（194–199 行）在 Windows 写死了主目录下的 AppData 子目录和 `C:\Program Files\nodejs`；`_app_paths`（202–209 行）只有 macOS 的三个 app bundle 路径。Windows 桌面应用自带 CLI 的位置没有在这里实现，不能将 macOS app bundle 的支持外推给 Windows。

候选启动类型的现状如下。这里描述的是代码构造的命令，不是 Windows 实测结果。

| 入口 | 发现与启动代码 | 当前可确认的行为 |
| --- | --- | --- |
| `.exe` | `_variants`；`_launch_command:630–644` | 列为候选，返回 `[路径]`；Windows 分支没有 POSIX 的 `X_OK` 检查，由启动结果判断。 |
| `.cmd` | `_script_uses_node:575–584`；`_launch_command:631–634` | 明确不按 Node shebang 识别；构造 `[SYSTEMROOT/System32/cmd.exe, /d, /s, /c, 路径]`，SYSTEMROOT 缺失时写死 `C:\Windows`。 |
| `.bat` | `candidate_snapshot:233–234`；`_launch_command:630–644` | 自动枚举遗漏，手动指定可能进来；没有 batch 专用分支，返回 `[路径]`。不能把这个列表认作绕过 cmd 的保证；实际 Windows 启动表现未验证。 |
| `.ps1` | `_variants`；`_launch_command:635–641` | 用 `shutil.which` 查 pwsh 或 powershell.exe，再尝试固定系统路径；构造 `-NoProfile -NonInteractive -File 路径`。其参数保真、执行策略及包装脚本的转发行为未验证。 |
| 无后缀、JS | `_script_uses_node:575–584`；`_candidate_handshake:683–697` | 解析后缀为 `.js/.cjs/.mjs`，或首行含 node 的候选，经过 `_node_for` 后返回 `[node, 原候选路径]`；其余返回 `[路径]`。Windows 能否执行其他无后缀文件，代码没有提供保障。 |

`_candidate_handshake`（673–758 行）给这个命令追加 `--version`；Codex 再追加 `login status`，Claude 再追加 `auth status --json`。普通探针由 `probe:463–512` 启动，标准输入为 DEVNULL；DSH 的版本探针由 `_probe_dsh_version:515–562` 复用 ACP launcher，并关闭输入。两者都没有对 cmd 参数作专门编码。

发现得到的 `command` 原样进入健康记录（`discovery.py:756–757,822–831`；`blackboard/service/harness_health.py:252–260`），`runtime_selection.py:41–50` 的 `command_for` 取已有选择或重新发现，再返回 command。Claude 与 Codex 的 `config.py:53–68`、`21–39`，ZCode 的 `config.py:14–34` 通常取这份选择；开发态 `HMB_DEV_SOURCE=1` 且指定 `HMB_*_CLI` 时，各自还有直接返回路径的分支，ZCode 对 JS 另拼 Node。DSH 的 `native_run.py:603` 直接使用 `command_for`。实现时这三条显式路径也使用同一入口解析边界，并删除其中走不到的查找回退，不能只修正式发现。

正式启动最终仍是 Python 的 `owned_popen`：Claude `native_run.py:308–335`，Codex `native_run.py:472–501`，ZCode `native_run.py:729–768`，DSH `acp/launch.py:202–228`。这些调用都使用 argv 列表和管道，没有 `shell=True`；如果列表本身已含 cmd 前缀，`shell=False` 并不会消除 cmd 的解释过程。`protocol/owned_process.py:68–89` 处理进程归属并调用 Popen，不提供 cmd 语言的转义。

## 2. 任务、角色和其他内容怎样交过去

共同入口是 `buddy/roles/prompts.py:8–26`：固定角色文本在前，Worker 的 Lead 说明、交付说明、任务和 turn input 随后拼成一个文本；Router 也有自己的固定前缀。`buddy/roles/run_execution.py:406–440,462–498` 把这些内容放进 `RunRequest.input_text`。`input_text` 包含用户或模型参与形成的任务、历史上下文、上一回合结果等，应视为不受信任的数据，JSON 序列化不使它变成 shell 安全的字符串。

| CLI 运行器 | 正文与后续输入 | argv 中的动态数据与文件 |
| --- | --- | --- |
| Claude Code | `claude/native_run.py:493–501,688–699` 通过 stream-json 的 user message 发送整个 input_text；`claude/protocol.py:112` 将 JSON 帧写入管道。Host inquiry 的协议答复也经同一 connection。 | `claude/config.py:96–133` 将 model、非 default effort、UUID session-id、settings 路径、可选 MCP 配置路径与 allowedTools、完整 JSON schema 放进 argv。`native_run.py:286–299` 把权限设置和 MCP 配置写到私有 JSON 文件，命令行携带文件路径；schema 本体仍作为 `--json-schema` 的值。 |
| Codex | `codex/native_run.py:706–718,745–749,792–797,1051–1066` 在标准输入的 thread/start、thread/resume、turn/start 中发送 cwd、模型、提供方、权限、effort、正文和 outputSchema；`codex/protocol.py:78` 编码写管道。 | `native_run.py:447–466,486–488` 正式 argv 是已选 command 加 `app-server --listen stdio://`；fast 模式另加两个源码固定的 `-c` 配置值。任务、schema、模型名、会话 id 和 cwd 不作为该启动命令的动态参数；cwd 同时作为 Popen 的 cwd。 |
| ZCode | `zcode/native_run.py:902–938` 经 session 配置、subscribe、session/send 的 content 发送输入，`zcode/protocol.py:697` 写标准输入。完成与 inquiry 的 MCP 挂载由 `native_run.py:319–349` 的私有桥接文件及会话参数描述，不拼成外层 harness 命令。 | `native_run.py:737–752` 用 argv `app-server --cwd <cwd>`；存储、日志、内置 provider 文件的位置经环境变量传入。模型与 effort 经会话配置，正文经 session/send；`provider_paths` 会使用 command 的末项定位 CLI 邻近文件（`zcode/config.py:37–47`），解析后的命令必须保持 JS 入口在末项。 |
| DSH | `dsh/native_run.py:1202–1225` 调用 ACP prompt；`dsh/acp/client.py:123–135,147–163` 的 session/new、resume、set_config_option、prompt 经连接传 cwd、MCP 挂载、模型配置和正文；`acp/connection.py:463` 写标准输入。 | `dsh/native_run.py:603–615` 用 argv `--profile acp --patch <文件路径>`；会话记录位置与权限 scope 写到 `dsh-launch-patch.json`，只把路径放 argv。启动器的 cwd 是 Popen 的独立参数。 |

正文还会进入各家的会话记录或证据文件，但这些本地写入不等于通过文件给 CLI 喂 prompt。四个现行运行器都没有把任务或角色文字作为外层 CLI 的位置参数，也没有以 prompt 文件替代标准输入。来自模型的工具调用、完成结果和问题答复在原生协议或已有 MCP 通道中流动，不会被产品再拼进一次 harness 启动命令；harness 自己执行工具的权限仍由原生权限和用户配置负责。

## 3. 哪些内容存在 cmd 风险

本项的危险边界是 `.cmd` command 前缀后面继续追加普通 argv。Python 的 Windows argv 编码服务于程序参数解析，不能拿来转义 cmd 的命令语言；`& | < > ^ % "` 以及延迟展开情形中的 `!` 都必须纳入测试。当前代码没有 cmd 转义器。本文没有在 Windows 运行利用载荷，因此结论是存在静态的二次解释路径，不宣称已经复现了某一种注入结果。

| 内容 | 当前是否进入可能经 cmd 的 argv | 风险判断 |
| --- | --- | --- |
| 四家的任务、角色、历史消息、格式纠正、Host inquiry 与模型回答正文 | 否；见第 2 节的标准输入路径 | 这些正文不会在产品这层作为 cmd 命令重新解析。含恶意命令文本也不改变外层启动 argv。 |
| Claude 的 model、effort | 是；来自 `RunRequest.buddy`，由 `execution_args` 追加 | `run_contract.py:134–139` 只声明有界非空文本；`protocol/internal_models.py:55–59,160–162` 只检查非空、NUL 和 UTF-8 长度，不是 shell 字符白名单。即使上游选自目录，也不能把外部 CLI 报告的模型标识当作 cmd 程序片段。 |
| Claude 的 output_schema | 是；`canonical_json(output_schema)` 直接作为 `--json-schema` 值 | Worker 的固定 schema 也含引号；fast/review 的 schema 来自调用输入（`roles/run_execution.py:466–478,485–497`），其 description、enum、属性名等可能带外部内容。JSON 合法和有界不等于 cmd 安全。 |
| ZCode 的 cwd | 是；`--cwd` 的值 | 工作目录由任务与本机路径产生；合法目录名可以含空格、括号、`&`、`^`、`%`、`!` 和中文。绝对路径校验不阻止 shell 二次解释。 |
| Claude 的 settings、MCP 路径；DSH 的 patch 路径；四家的入口及 Node/JS 安装路径 | 是；文件本体没有放入 argv，但路径有 | 私有文件的根路径可以由用户环境决定。随机文件名和可信 JSON 内容不能使其路径自动安全。 |
| Codex 的模型、提供方、effort、schema、cwd 和原生 session id；ZCode 与 DSH 的会话内配置 | 否，均在协议或文件内容中 | 不构成这些启动参数的 cmd 注入源；仍保留协议边界原有校验。 |
| `--version`、`login status`、`auth status --json`、`app-server`、`stdio://`、DSH 的 `acp`、Codex fast 的两个固定配置值、Claude 的新 UUID 与固定权限开关 | 是，但值由源码或产品生成 | 它们本身不含用户或模型的命令片段；安装路径仍可能使整个调用失败。不能因为探针只追加固定词，就断言该入口已通过任意动态参数的安全验证。 |

手动 `.bat` 缺少专用分支也必须处置，不能以“不显式添加 cmd”作安全依据。`.ps1` 是另一解释器入口：现有代码使用 `-File` 而非 `-Command`，因此不能直接把 cmd 注入结论套给它；脚本接收与转发 argv 的保真性仍未验证。两者都不能未经验证便纳入新的直接启动集合。

## 4. 选定的做法与边界

沿用 `discovery.py` 中 `_script_uses_node`、`_node_for`、`_launch_command` 和 `_candidate_handshake` 的现有边界，不新增模块。将 `_script_uses_node` 的布尔判定改为给出 Node 入口路径：直接 JS 或现有 POSIX Node 入口返回自身，已识别的 npm `.cmd` 返回它引用的 JS 路径；原生程序不产生 Node 入口。启动前缀的组装集中在现有 `_launch_command` 边界，发现、探针和三家开发态显式路径都复用，避免在调用方复制 Node 解析。最终结果为 `[原生可执行文件]` 或 `[原生 Node, JS 入口]`，常规运行继续从服务选择记录取得结果，各运行器按自己的原生协议追加参数。

Windows 自动候选名固定为 `<name>.exe`、`<name>.cmd`，按这个顺序枚举；`.ps1`、`.bat`、`.com` 和无后缀名都不列为候选。手动指定这些不支持的入口也在 spawn 前拒绝，不给 `shell=False` 的 batch 隐式启动留下路径。手动指定的 `.js/.cjs/.mjs` 仍复用现有 Node 机制，Windows 的最终 Node 程序必须为直接运行的原生 `node.exe`。macOS/Linux 保留当前原生可执行文件和 Node 入口机制；本设计不增加 `.py` 生产入口或通用 shebang 解释器。

npm shim 的识别只提取一个启动事实：程序是 Node，恰好有一个带引号、以 `%dp0%` 或 `%~dp0` 开头的相对目标，而且该目标文件存在。这里的目标是 Node 调用的目标操作数，不把旁边 `node.exe` 的位置或它在条件赋值中的引用算成另一个 JS 目标；有多个符合条件的目标、目标不带引号、不是相对目标、程序不是 Node 或目标不存在，都拒绝。Node 名字可以直接写出，也可以由 shim 的字面 Node 选择赋值表明；只提取该事实，不模拟 batch 的控制流、环境或参数展开。

读取 shim 复用 `_record_text:56–62`，沿用其 4096 字节界限和 UTF-8 读取行为，不另立大小或编码规则。只将目标前面的脚本目录记号还原为 shim 所在目录，按本机路径规则组合目标；不运行 shim，不展开其余 shell 表达式。识别不作整模板逐字节比对，不因无关或未知的环境赋值拒绝，不把任何第三方 shim 模板正文抄入仓库；测试自行写出只包含必要事实的最小 shim。安全边界是永远不经 cmd 执行，用户和模型的动态参数只能成为原生 argv 或标准输入的数据，不取决于 shim 是否与某一模板逐字相符。

对已识别的 `.cmd`，把解析出的 JS 路径交给原来的命令组装过程，Node 选择复用 `_node_for` 的“先相邻、后发现”。相邻位置以原 shim 所在目录为准（现有 `_node_for:590–591` 本来就在解析符号链接前取 launcher 目录），不能改成深处的 node_modules/JS 目录；最终命令为 `[绝对 node.exe 路径, 绝对 JS 路径]`。Node 自己不再接受 batch 或 PowerShell 转发，也不递归解析 Node shim。ZCode 的 `provider_paths` 仍从 command 末项定位原来的 CLI 相邻配置，不能为了通过初始化而复制或猜造配置文件。

删除 `_launch_command:631–641` 中 cmd 与 PowerShell 两个分支；未知 `.cmd` 和不支持的脚本类型在这个解析边界失败，候选诊断保留失败路径。候选拒绝后仍按当前优先级规则尝试其他候选，并呈现最终选择；禁止的是把同一个失败入口改用 shell 运行。四家共用入口类型规则，不按 harness 名另造机制，不改各家原生的任务输入、配置、登录、权限或工具。

三家 `config.py` 的开发态条件已经要求 `HMB_*_CLI` 非空，后续 `value = env.get(...) or shutil.which(...)` 中的 which 走不到。Claude 的缺失值分支、Codex 的 which/app bundle/缺失值回退，以及 ZCode 的 which/DEFAULT_CLI 回退均是这个条件之下的死分支（分别见 `claude/config.py:53–68`、`codex/config.py:21–39`、`zcode/config.py:14–34`），实现时一并删除及清理无用导入/常量。开发态若显式给路径，就只解析这条路径；未给时按当前条件走 `command_for`。ZCode 为直接 JS 寻找 Node 的行为仍有实际用途，改为复用上述发现边界，不把它误当作死代码一起删掉。

### 失败原因、命令行上限与版本

本设计确定专用原因码 `harness.entry-unsupported`，参数为 `operation`，发现时取 `probe`；具体路径沿用候选诊断的 `path` 字段。含义是“已找到入口，但该入口类型或 Node 目标无法识别；请选择原生可执行文件或可识别的 npm Node 入口”。在 `discovery.PROBE_REASON_CODES`、`protocol/reason-codes.json` 和控制台中英文原因文本中注册，同步契约测试。不能把这个情况归为 `not-found`、`interpreter-missing` 或一般启动失败；它发生在创建进程之前，不运行版本或认证探针。Node 解释器确实缺失仍用既有 `node-missing`，两类失败不能混在一起。

还要处理 Windows 命令行长度。`buddy/harnesses/run_contract.py:81` 的 `MAX_SCHEMA_BYTES` 为 65536，而 Claude `--json-schema` 会把整个 canonical JSON 放入 argv（`claude/config.py:132`）。Windows CreateProcess 的命令行上限是 32767 个 UTF-16 代码单元，含末尾 NUL；schema 即使符合请求大小上限，也可能无法形成可启动的 Windows 命令行，中文、emoji、引号与反斜杠的编码还会影响最终长度。不能只检查 schema 的 UTF-8 字节数，也不能只把各参数的 Python 字符数相加。

在共享进程创建边界检查最终的完整 argv：使用与 Python Windows Popen 一致的 `subprocess.list2cmdline(argv)` 结果，计算其 UTF-16 代码单元数并加上结尾 NUL；超过 32767 时，在创建进程和登记创建意图之前明确失败，不截断 schema、参数或正文，也不自动改用另一套 CLI。检查放在 `protocol/owned_process.py` 的共同创建路径；`discovery.probe` 不经 owned_popen 的分支复用同一检查，每条创建路径只检查一次，不把检查散落到四个运行器。失败必须保留“未创建进程、已确认无进程需要停止”的现有归属证据约定，不能留下未决 spawn intent，不能被探针的通用异常处理吞成不明原因。

长度失败确定为 `harness.command-line-too-long`，参数为 `operation`、`utf16Units` 和 `limit`，limit 为 32767；诊断只报告计数与上限，不复述大段任务/schema。发现探针与正式运行均保留这个明确原因。对接四家的原生错误结果时可以沿用各家的错误封装，但要保住同一原因和未创建进程的证据。此前没有为任意长参数执行提供保证，这个拒绝不是对 schema 的静默改写。CreateProcess 实际边界与这套计算在 Windows 上尚未验证，实现必须用原生假可执行文件确认临界值、非 BMP 字符和引号扩展，不调用真实 harness。

新增专用原因码属于 Host 合同变化；实现这一批必须提升中版本，并把 `CONTRACT_VERSION` 更新为新中版本的 `.0`，同步 `pyproject.toml`、`uv.lock`、`protocol/contracts.py` 和 `tests/python/protocol/contract_signatures.json` 等版本约束。当前源码为 0.50.1、`CONTRACT_VERSION` 为 0.50.0，下一中版本由 Host 整合时确定，不能把这批当作仅补丁版本安装。C-Two 方法签名不因原因码变化而改写，但其新合同版本和既有签名摘要须一致；`tests/python/protocol/test_contract_version.py:36–44` 是对应检查。设计文档本身不改版本。

| CLI | 本方案在源码调用层是否成立 | Windows 发行入口的未验证项与办法 |
| --- | --- | --- |
| Claude Code CLI | 成立：保留 stream-json，模型、effort 和 JSON schema 继续作为原生参数；最终命令行超限则在启动前明确失败。 | 原生 `.exe` 的真实可用性、npm 安装的目标是否满足识别条件均未验证。未来显式探针记录 CLI 与安装器版本、入口类型和识别结果，用无模型 initialize 核对参数；真实超长 schema 的原生接受能力不作承诺。原生 Windows 权限档位仍是 W03 的独立边界。 |
| Codex CLI | 成立：仍运行 app-server，并经标准输入传 thread/turn 请求。 | `.exe` 与 npm Node shim 的具体安装形态、原生参数解析未验证；未来核对入口，再用已有无 prompt 的 App Server 初始化/目录读取探针验证。不能改用 exec 的另一套运行器。 |
| ZCode CLI | 成立：现有配置已经支持 `[node, JS]`；仍运行 app-server。 | Windows 独立 CLI、shim 目标、Node 版本要求与 provider 文件邻近布局未验证；未来核对入口，用无 prompt 初始化验证。特别验证解析后真实 JS 入口仍能定位原来的内置 provider 文件。 |
| DSH CLI | 成立：仍用原来的 ACP launcher 和 `--patch` 文件。 | 真实 Windows 入口、shim 目标和 ACP 管道运行情况未验证；未来核对入口，用版本探针和无 prompt 的 ACP initialize/session 配置验证。停止证据和 Job 问题按第二批验收。 |

表中“成立”只表示本项目现有协议无需为此另造通道，不表示厂商 CLI 已在 Windows 受测。真实 harness 探针按计划放在首次发版之后，后续明确授权后记录 CLI 形态、版本、系统和验证范围。本轮与实现的常规 CI 均只使用假程序，不访问真实 harness 或调用模型。

### 固定候选、路径来源与 PATH

W02 的 Windows 候选枚举明确保留固定表，但收窄为 `.exe`、`.cmd`；不改用 PATHEXT 枚举或 shutil.which，不增加系统可能执行而本产品不支持的类型。`_variants`、`has_candidate` 与 `_node_for` 使用同一候选规则，Node 最终只选原生 `.exe`。PATHEXT 未设、为空、改变顺序、添加 `.JS/.PS1/.BAT/.COM` 都不改变产品枚举的名字和优先级。环境白名单可以继续传递用户的 PATHEXT，这与产品选定哪些 harness 入口是两回事；用户的配置不被产品改写。

注册表读取在输入边界保留类型，只对 `REG_EXPAND_SZ` 按 Windows 环境展开规则展开一次，普通 `REG_SZ` 不误展开，明确所用环境来自 Windows。常见目录从 APPDATA、LOCALAPPDATA、ProgramFiles 等实际环境/系统目录来源取得，不重造 `C:` 和 AppData 布局；缺少来源时不猜另一盘符。Windows app bundle 的默认位置没有证据，不在这批补猜。注册表展开与目录来源各有独立测试，不能把候选后缀修正当作 W02 全部完成。

已核实 `native_environment:303–313` 目前反向遍历整个 command，对每一个绝对且存在的文件把其父目录前置 PATH。解析为 `[node.exe, JS]` 后会把 JS 目录和 Node 目录都前置，而且反向遍历使 JS 目录先于 Node 目录；Windows 默认 PATHEXT 包含 `.JS`，后续按名字启动程序时可能命中那个目录中的 JS。这个目录扩大来自产品，而不是用户自己提供的 PATH；具体命中行为未在本轮 Windows 实测。

实现时只从 `command[0]` 取实际执行程序的目录并前置 PATH；command 为空则不前置，原来环境中的 PATH 照常拼接在后，不从其余绝对参数取目录，不移除用户原有的路径或 PATHEXT。对 `[node.exe, JS]` 只加 Node 目录，对协议测试的 `[sys.executable, fixture]` 只加 Python 目录。单元测试逐项核对前缀和保留的原 PATH，Windows 整链用例在 JS 目录放同名诱饵，确认它不会因这次前置逻辑获得搜索优先级。

### 缓存边界

沿用现有位置指纹、过期与失败失效机制。`candidate_snapshot:244–265` 已记录 shim 自身与 Node 候选的 stat；不把解析出的 JS 入口 stat 纳入签名，不做先解析再补签名的两阶段过程。`HarnessHealth.refresh:190–204` 按现有位置指纹和有效期判断是否重用，`READY_SECONDS` 在 `blackboard/service/harness_health.py:17` 为 3600；启动失败可经既有 `invalidate:129–148` 路径令记录失效。JS 内容单独改变不会立即改变位置签名，最多到现有过期/重新发现边界再重判，接受这个边界，不添加第二种监测机制。

Windows 尚未发版，没有要兼容的 Windows cmd/PowerShell 健康记录存量；不向位置签名加入启动规则标识，不为旧记录加迁移、保留分支或回归用例，不因这批新增存储字段或 schema 升级。只验证当前形态下已有的 shim 指纹、正常过期和真实启动失败后的失效路径；不把封存的执行证据改写为新启动描述。本批中版本变化由新增 Host 原因码触发，和存储升级无关。

## 5. 测试里的假 harness

数量的口径保持核实结果：这个提交的 `tests/python/**/test_*.py` 中含 chmod 的恰好 42 个文件，含字面 `#!` 的只有 11 个。42 个文件还包括凭据权限、目录私有性和 Git 可执行位；不能按这个数量把所有 chmod 都改成启动夹具。需要解除的是对 POSIX 直接执行 shebang 的依赖，选择记录通道已有完整 argv 能力，协议测试无需每次再造一个系统 launcher。

只为“发现加启动整条链”的用例提供 `tests/python/fake_executable.py` 中的 helper，形态为 `make_fake_executable(directory: Path, name: str, *, source: str | Path) -> Path`。它返回产品实际能发现的入口路径：Windows 为 `<name>.exe`，macOS/Linux 为 `<name>`；版本、认证、初始化和协议行为仍由 fixture 自己提供。四家各一条整链用例经过真实发现、选择记录和运行器；手动路径与 PATH 可以作为同一用例的子场景，不把这个生成器扩散到所有协议测试。

在 `pyproject.toml` 的 `[dependency-groups] dev` 中加入并在 `uv.lock` 锁定 distlib，只供上述整链测试生成 console-script 入口，明确用当前检查进程的 Python 3.12。当前项目没有 distlib 依赖，这是实现新增的开发依赖，不进入产品运行依赖；检查环境安装该组，不依赖机器恰好有的 pip 私有包，不下载浮动版本，不自己拼 PE 或编译 C 程序。distlib 的公开脚本 API、x64 launcher 资源、许可与分发包包含情况本轮未验证，实施时核对锁定分发包并在三系统验证。

helper 生成自包含的 Python 包装脚本，再经生成器公开接口形成入口，不依赖 harness 环境保留测试模块的 PYTHONPATH。内联 source 落在私有根，Path source 只读运行原 fixture，例如通过标准库 runpy 的 `run_name='__main__'` 并给相邻导入保留搜索路径，使 `tests/python/buddy/harnesses/codex/fixtures/mock_codex.py:11` 仍能导入 protocol_items。保留 `__file__`、argv 尾部、标准流和退出码，不 chmod 或改写共享 fixture，不经 shell 传参。整链用例观察 Windows launcher 的子 Python 与后代结束，不能把 launcher 自己退出当作停止证明。

协议与运行器测试统一复用产品现有的选择记录通道。直接运行的测试用 `runtime_selection.bound`（19–27 行）给出 `{'harness': 名字, 'status': 'ready', 'command': [sys.executable, str(fixture)]}`；需要跨 controller 子进程的测试把按 harness 名索引的同形记录写入私有 JSON 文件，通过 `HMB_HARNESS_RECORD_FILE` 传入，`selected:30–38` 读取，`controller_environment:59–88` 已能传递/物化选择记录。必须去掉相应 `HMB_*_CLI` 覆盖，避免三家开发态分支抢先命中；需要用户环境、fixture case/state 或 ZCode 邻近资源的内容仍按原通道提供。

这不是为测试新增生产接口：现有 `codex/test_native_run.py:1754–1778` 已用 `[sys.executable, native.py]` 配合 bound；`dsh/test_no_tool_dsh.py:45–75` 已用私有 record 文件。测试保留真实 Python 子进程、原生协议、错误与归属观察，所省略的是这类测试不负责的命令发现。只有每家一条整链用例负责从真实路径证明发现和启动，不能拿选择记录测试替代它；也不能为测试给生产发现增加 `.py` 支持。

迁移清单因此缩小到两类。`buddy/harnesses/test_harness_discovery.py` 不再用本地 executable 工厂为所有场景生成 launcher，把真实发现加启动的断言集中到四家各一条整链用例并使用 distlib helper；纯候选/解析单元测试只造文件与必要的 probe 边界 mock，直接检验 probe 生命周期的用例用显式 Python argv，保留原来的实际子进程观察。共同的参数保真、长度和拒绝类型用例复用这四条整链测试的假入口，不新增四套 launcher 工厂。Claude 的 `test_claude.py`、`test_claude_worker.py`、`test_native_run.py`、`test_claude_tool_evidence.py`，Codex 的 `test_codex.py`、`test_in_turn_inquiry.py`、`test_native_run.py`、`test_no_tool_codex.py`、`test_codex_continuation.py`、`test_codex_tool_evidence.py`、`test_delete_sessions.py`，ZCode 的 `test_native_config.py`、`test_zcode.py`、`test_zcode_request_input_probe.py`、`test_zcode_tool_evidence.py`，以及 `test_adapter_usage.py`、`buddy/roles/test_registered_run_wiring.py`、`blackboard/evaluation/test_other_native_observations.py`、`blackboard/tasks/test_host_workflow_worker.py` 的协议 fixture 改用选择记录，不生成可执行副本；`buddy/runtime/test_worker_sessions.py:90–99` 复用的 setup 随对应工厂修改。已经采用显式 Python argv 的 ACP 传输测试保持现有方式。

`claude/test_claude_config.py` 等对显式 CLI 入口的边界测试用上述整链测试入口或 mock 检查解析调用，不把所有配置测试都变成生成器测试。路径哨兵和诱饵若需证明绝无启动，应断言产品 spawn 边界未被调用；若需要运行它来检验发现，就归入整链用例。凭据 chmod、Git mode、私有目录测试、假 uv/安装布局、FIFO、信号等 T03 其他内容不因本设计批量改写或给整文件加 Windows skip，仍按各自系统机制处理。验收清点联合检查 chmod、shebang、HMB_*_CLI 与实际被启动的 fixture，确保所有共用的协议测试不再把 shebang 路径当可执行文件，不要求“42 个文件全部动过”。

## 实现文件与验证

实现修改 `src/hey_my_buddy/buddy/harnesses/discovery.py` 的现有入口识别、Node 选择、固定候选、注册表展开、目录来源、PATH 前置和原因映射；不新增入口解析模块，不改健康位置签名。三家 `claude/config.py`、`codex/config.py`、`zcode/config.py` 删除走不到的回退代码，显式路径复用原有解析边界。`protocol/owned_process.py` 加共享的 Windows 最终命令行长度边界，`discovery.probe` 的普通 Popen 分支复用；四家 `native_run.py` 只在需要保留明确长度失败及归属证据的映射处调整，不加平台 shell 逻辑，不另造协议。

新增原因注册放在 `protocol/reason-codes.json`，对应消费者由 `protocol/reasons.py` 读取；更新 `apps/console/src/i18n/zh/reasons.ts` 和 `en/reasons.ts` 的文本及其原因码一致性测试。中版本及 CONTRACT_VERSION 的修改同步 `pyproject.toml`、`uv.lock`、`protocol/contracts.py`、`tests/python/protocol/contract_signatures.json` 和既有版本约束材料，不为未发布 Windows 记录新增存储升级。最终变更若触及前端原因文本，按仓库规则运行前端测试；只有纯 Python 时才无需前端检查，不因本批仅改原因文案要求浏览器布局检查。

测试侧新增 `tests/python/fake_executable.py`，dev 组锁定 distlib；在 `tests/python/buddy/harnesses/test_harness_discovery.py` 或其同目录专项文件集中四条整链测试，其他协议调用点改用现有 record/bound。扩充 `blackboard/service/test_harness_health.py` 的正常过期和启动失败失效测试、进程创建边界的长度测试及三家 config 的不可达代码清理测试。实现稳定后更新 `docs/reference/harness-integration.md`、相关 harness 参考页、`docs/reference/operations.md` 的支持入口与长度边界说明，验收记录写入 `docs/acceptance/`；本轮只改本文。

实施按以下证据验收，均不使用真实 CLI 或模型：

1. 原生 Windows CI 上四家各一条整链用例，以 helper 入口经过发现、必要的假认证/版本探针、选择记录、初始化和假协议回合；macOS/Linux 跑同样四条用例。协议/运行器用例则经 record/bound 启动 `[sys.executable, fixture]`，无 shebang 执行依赖，argv、标准流、退出码与原来的断言一致。
2. 在入口、Node、JS、私有根和 cwd 中使用 NTFS 允许的空格、括号、中文、`&`、`^`、`%`、`!`；在参数/协议中使用 `& | < > ^ % " !`、换行、尾部反斜杠、空串和非 BMP 字符，逐元素核对真实 argv、逐字节核对 stdin，并确认没有私有 sentinel 附加命令被执行。`| < > "` 不能作为 Windows 文件名字符，不造这样的目录测试。
3. shim 样本由测试自行写出最小内容，覆盖 `%dp0%` 与 `%~dp0`、引号目标、相邻 Node/PATH Node 和已有目标；增加无关环境赋值仍可接受的正例。不带引号、零个/多个目标、非 Node 程序、缺失目标或超出 `_record_text` 现有界限时，在 spawn 前以 `harness.entry-unsupported` 失败；缺少 Node 用 `node-missing`。不保存第三方模板全文，不测试逐字节模板相等。
4. 固定候选只含 `.exe/.cmd`，不同 PATHEXT 值不改变候选；`.ps1/.bat/.com/无后缀` 自动搜索不出现，手动指定时明确拒绝。注册表按类型展开、常见目录实际环境、三家显式开发路径及 PATH 只前置 `command[0]` 分别测试；JS 目录的同名诱饵不获额外优先级。保留位置扫描只 stat 的原有测试。
5. 在最终 list2cmdline 编码后测 32767 个 UTF-16 代码单元的边界，计入末尾 NUL、emoji 的代理对、引号与反斜杠的编码扩展及 exe/Node/JS 前缀；超限时没有创建进程和未决 spawn intent，原因是 `harness.command-line-too-long`，schema 与任务均未截断。用请求大小合法但命令行过长的 Claude schema 覆盖这条实际路径，并检查探针异常映射没有吞掉原因。
6. 保留 shim 自身指纹变化、ready 记录正常过期、启动失败后现有 invalidate 的测试，不新增 JS stat 或两段签名用例。四条整链与 record/bound 协议测试均观察 EOF、取消、快速退出和子孙进程的真实结束；进程未知仍是未知，不把第二批 Job/ACL 问题以 mock 或放宽断言抹掉。

按仓库检查入口 `uv run --frozen python -m hey_my_buddy.cli.checks` 后跟受影响文件或目录运行，清除继承的日常 runtime、state 和 agent 凭据变量，只安装锁定依赖与 dev 组。实现触及发现、进程创建、测试共同设施和 Host 原因码，合入前在三个系统跑完整 Python 检查并保留结果；更新前端原因文本则另跑 `npm --prefix apps/console test`。第二批独立问题仍阻断时逐项报告实际失败，不声称整轮通过。设计文档修改不运行这些实现测试。

本轮验证边界：按 Host 的十项审查意见修订并复核源码位置、PATH 前置、现有记录通道、原因注册与版本约束；没有实现 parser/helper，没有运行 Python 检查套件，没有 Windows 启动、真实 CLI、模型调用、安装、日常看板或服务验证。Host 审过定稿后另开实现微任务，本文不作为实现验收。

## 2026-10-21：静态 shim 启动描述的扩展

本节取代上文实现前设计中“只认 Node”的 shim 限制。`.cmd` 的同一解析边界读取一个静态启动描述：带引号的程序、按原顺序保留的字面参数、恰好一个带引号且以 `%dp0%` 或 `%~dp0` 开头的相对入口，最后以 `%*` 转发产品追加的 argv。程序可以是 npm 的 Node 选择器，也可以是从 shim 目录出发、带引号的相对 `.exe` 路径；解析相对路径时允许 `..`，最终程序必须存在。产品直接启动解析出的程序，所有动态参数仍为独立 argv，不经过 shell。规则不读取 harness 的名字。

npm Node shim 是这个描述的一种情形：`node`、`node.exe`、shim 相邻 Node 以及赋给程序变量的这些 Node 值，都使用现有的相邻原生 Node、再发现 Node 的选择流程。npm 的目录变量、两支字面 Node 选择赋值和固定转发前导只被识别，不执行 batch 控制流；用于 Node 命令扩展名选择的既有 PATHEXT 表达式不传给子进程。Electron shim 是同一个描述的另一种情形：启动其相对 `.exe`，保留例如 `--expose-internals` 的字面参数；不替换成另一个 Node，也不增加按 harness 名字判断的规则。带引号的 `set "NAME=literal"` 赋值成为该入口的子进程环境，覆盖调用方环境中的同名值；普通环境仍通过原有允许列表，调用方额外的变量不会因此全部透传。

入口后缀限定为 `.js`、`.cjs` 或 `.mjs`，普通入口必须是现有文件。入口落在 `.asar` 内时，解析边界要求最近的 `.asar` 祖先是现有普通文件，保留归档后的入口路径，并让原有有界版本探针验证该程序确实能执行此入口。理由是归档内路径属于 Electron 的文件系统语义，产品不另加 ASAR 格式实现，也不扫描厂商代码；归档文件存在只允许尝试探针，不能单独得到 ready。空归档、缺少内部入口或其他执行错误由探针报告；不能确认进程已停时仍返回 `harness.shutdown-unverified`。

不带引号的程序或入口、绝对程序或入口路径、多个入口或调用、缺失程序或普通入口、缺失归档、非字面环境值、计算参数、额外 shell 命令和未识别的控制流，均在解析边界拒绝为 `harness.entry-unsupported`。相对路径中出现其他环境或参数表达式也拒绝，不尝试求值。识别范围外的 `.cmd`、`.bat`、`.ps1`、`.com` 与无后缀 Windows 入口继续拒绝，不回退到 cmd.exe 或 PowerShell。

健康记录继续只保存现有的原始入口 `executable` 和最终 `command` 等字段。每次使用已选择的入口时，由同一解析器从 `executable` 推导字面环境，不另存一份可推导的环境映射；跨 controller 的记录文件保留该原始入口。版本及认证探针、原生回合环境、DSH ACP 版本启动和回合启动都携带这些值。Codex 的账户读回使用健康记录中的原始入口。存储形式、Host 字段和服务与执行进程之间的契约形式均未改变，不开启 schema 升级，不增加 fixture 存量，不移动中间号或 `CONTRACT_VERSION`；安装此修复时按 0.x 规则移动补丁号。

验证使用自行编写的合成启动描述和假原生程序，覆盖 npm、普通文件和归档入口、拒绝形式、探针环境、记录文件传递以及 DSH ACP 假回合的实际子进程环境；没有保存厂商 shim 原文作为测试夹具。真实 Windows 发现与版本观察的结果及原始输出由本任务交付，不能用假协议回合证明真实 DSH 的 ACP 或模型能力。docs/reference 的入口支持、环境传递及验证边界说明由 Host 在验收后同步。

Windows 实测另外复现了同一发现模块内的管道 EOF 误判：子进程写完版本、关闭 stdout 后仍短暂存活，`PeekNamedPipe` 返回 `ERROR_BROKEN_PIPE`（109），原逻辑将其当作 `pipe-failed` 并丢弃已读输出。探针现在按 109 识别 EOF，再用剩余探针预算等待真正退出；其他管道错误仍失败，EOF 后未退出仍超时，最终进程与 Job 的停止证据仍由原规则确认。这项修正没有以 EOF 代替进程停止。
