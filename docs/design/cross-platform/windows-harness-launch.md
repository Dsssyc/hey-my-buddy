# Windows 上发现与启动 harness：设计

2026-10-21，供 Host 审查的设计，基于输入提交 `14fead7da7f3744516dabdab8a76b214be258d27`（源码 0.50.1）。本文的“现状”仅描述这个提交中产品对 CLI 的调用代码；没有运行 Claude Code CLI、Codex CLI、ZCode CLI 或 DSH CLI，没有观察任何真实 CLI 版本在 Windows 上的行为。行号对应输入提交；实现、Windows 验证和安装均未发生。

## 范围与结论

依据[跨系统计划](plan.md)的“Windows 分批计划”、开发者的五项决定，以及[清单](inventory.md)的 W01、W02、T03。本项属于第一批的入口发现、启动和可执行测试夹具，不改变第二批的执行所有权、Job 生命周期、停止证据、ACL 与耐久性承诺。目标环境为 Windows 11 x64、本地 NTFS、同一登录会话和 Python 3.12；WSL2 按 Linux，Windows 10 未验证，Windows ARM、网络盘、FAT/exFAT 和 Windows 服务形态不在首发支持范围。Claude skill 的目录联接、检出的名字与链接限制、断电保证以及 C-Two 的相关决定照原计划分批处理，不能用本设计声称这些工作已完成。

建议采用一种启动规则：产品只把独立 argv 交给原生可执行程序，Windows 上不把 harness 的动态参数交给 `cmd.exe` 或 PowerShell。原生 `.exe` 直接启动；对完整识别的 npm Node `.cmd` shim，只读取其启动描述，解析为原生 `node.exe` 与原来的 JS 入口后直接启动；未知脚本明确拒绝。任务和角色文字继续用现有原生标准输入协议。这消除了 cmd 对整个命令的再次解析，也覆盖必须留在 argv 中的 Claude 模型名和 schema、ZCode 的 cwd、DSH 的 patch 路径。

这是一项有意收窄入口类型的设计：当前已有的任意 `.cmd` 与 `.ps1` 启动不再保留；普通 `.bat` 也不增设 shell 支持。已识别的 npm shim 是静态启动描述，不是任意 batch 解释器。若安装只提供未知脚本，报告该入口不可启动，并在诊断中写明原因和可选择的原生入口；不能回退到 shell 或更宽权限。四个 CLI 的实际 Windows 发行入口及 npm 模板均未验证，不能据此声称四家目前都可用。

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

发现得到的 `command` 原样进入健康记录（`discovery.py:756–757,822–831`；`blackboard/service/harness_health.py:252–260`），`runtime_selection.py:41–50` 的 `command_for` 取已有选择或重新发现，再返回 command。Claude 与 Codex 的 `config.py:53–68`、`21–39`，ZCode 的 `config.py:14–34` 通常取这份选择；开发态 `HMB_DEV_SOURCE=1` 且指定 `HMB_*_CLI` 时，各自还有直接返回路径的分支，ZCode 对 JS 另拼 Node。DSH 的 `native_run.py:603` 直接使用 `command_for`。实现唯一解析边界时也必须收敛这三条开发态路径，不能只修正式发现。

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

在 `buddy/harnesses/` 增加一个共享的入口解析模块，例如 `launch_command.py`，提供 `resolve_command(path: Path, environment: Mapping[str, str], *, deadline: float) -> list[str]`。它只接受一个已选候选路径，返回可以直接启动的 argv 前缀，或返回带路径与原因的解析失败；不接收 prompt，不启动一次回合，也不持有数据库。发现、Node 定位和三家的开发态显式 CLI 路径都使用它；常规运行继续从服务选择记录取得同一个结果。解析后内部运行器只按各自原生约定追加参数，不再各自判断后缀或补 shell。

Windows 上 `.exe` 是直接启动的共同基础；`.com` 虽可能出现在 PATHEXT，首批只列为候选并报不支持，原生启动语义未验证，不凭后缀推断 PE。`.js/.cjs/.mjs` 和已经识别为 Node 的无后缀入口保留现有 Node 机制，最终解释器必须是直接启动的原生 `node.exe`。不支持的无后缀非 Node 文件在 spawn 前拒绝。macOS/Linux 继续使用原生可执行文件和现有 Node 入口；本设计不增加 `.py` 生产入口或通用 shebang 解释器。脚本类型校验只在这一个边界做，不在 `owned_popen` 或四个运行器重复校验。

对 npm `.cmd` 的解析必须以完整模板为单位，限定文件大小和编码，只接受已经在离线样本中核对过的 Node shim：除定位脚本目录、选择 Node、调用一个固定 JS 入口并原样转交参数外，没有其他可观察操作。不是“找到一行 node 就运行”，不执行 wrapper，不读取 npm 包源代码来猜入口，也不忽略 wrapper 中的环境设置、额外命令或未知控制流。允许的模板语法及字节样本要随实现的测试固定下来；当前仓库没有足够样本，本轮未核实具体 npm/cmd-shim 版本，模板集合标为未验证，不能凭记忆写一个宽松正则后宣布支持。

解析只把已识别模板里的脚本目录变量还原为 shim 所在目录，并取其字面相对 JS 路径；不展开任意 `%变量%`、命令替换、shell 变量或用户参数。路径按本机规则组合并归一化，验证实际文件与允许的模板结构，不使用按 harness 名硬编码的 node_modules 位置。按该模板本来的规则先选旁边的 `node.exe`，不存在再使用同一发现机制选出的原生 Node；不接受 Node 自己再是一层 `.cmd/.bat/.ps1` 的递归解释。若模板要求额外语义而解析器无法等价表达，拒绝该模板。最终结果形如 `[绝对 node.exe 路径, 绝对 JS 入口路径]`，运行器将自己的参数作为独立元素追加。

这个机制按安装入口类型工作，不按 harness 名分支。不会修改用户的原生配置、强制关闭功能、替换 Node 包版本或绕过定制脚本；自定义 wrapper 做了什么，解析器无法完整表达就明确不支持。候选拒绝仍可按现有选择规则继续寻找其他候选，并在结果中呈现失败路径与最终选择；这里禁止的是把同一个失败候选偷偷改用 shell 启动，不取消现有候选优先级规则。

`.bat`、未知 `.cmd`、`.ps1` 在新规则下都在 spawn 前报告“该启动脚本暂不支持，请选择原生可执行文件或受支持的 Node 入口”。删除 `_launch_command` 中 cmd 和 PowerShell 分支，删除原来的 PowerShell 命令构造测试，改测明确拒绝和无进程创建。保留 PowerShell 作为第二启动策略需要另一个保真与安全论证，本设计没有选择它。纯粹把正文搬到 stdin 已由现状实现，却不足以保护其他 argv；另写 cmd 转义器会继续承担多层引号、变量展开和不同 wrapper 的转发语义，故本设计不采用。

| CLI | 本方案在源码调用层是否成立 | Windows 发行入口的未验证项与办法 |
| --- | --- | --- |
| Claude Code CLI | 成立：保留 stream-json，模型、effort 和 JSON schema 可继续作为原生进程的参数，不经 shell。 | 原生 `.exe` 的真实可用性、npm 安装是否提供可识别 Node shim均未验证。未来显式探针记录 CLI 与 npm/shim 版本、入口类型及完整转发样本；用无模型的 initialize 核对 schema 等参数是否原样到达。原生 Windows 权限档位是 W03 的独立边界。 |
| Codex CLI | 成立：仍运行 app-server，并经标准输入传 thread/turn 请求。 | `.exe` 与 npm Node shim 的具体安装形态、原生进程对参数的解析未验证；未来核对入口，再用已有无 prompt 的 App Server 初始化/目录读取探针验证。不能因此改成 exec 的另一套运行器。 |
| ZCode CLI | 成立：现有配置已经支持 `[node, JS]`；仍运行 app-server。 | Windows 是否有独立 CLI、npm shim 的内容、Node 版本要求与 provider 文件邻近布局未验证；未来收集版本化入口样本，用无 prompt 初始化核对。特别验证 `provider_paths(environment, command)` 在解析后的真实 JS 路径下仍找到原来的内置文件，不能复制或猜造配置来让检查通过。 |
| DSH CLI | 成立：仍用原来的 ACP launcher 和 `--patch` 文件。 | 真实 Windows 入口和 npm shim、ACP 管道运行情况未验证；未来核对入口，用版本探针和无 prompt 的 ACP initialize/session 配置验证。停止证据和 Job 问题另按第二批验收。 |

表中“成立”只表示本项目现有协议无需为这个启动方案另造通道；不表示厂商 CLI 在 Windows 上已经受测。真实 harness 探针按计划放在首次发版之后，经后续明确授权运行，结果记录 CLI 形态、版本、系统和验证范围。本轮与实现的常规 CI 均只使用假程序，不访问真实 harness 或调用模型。

### PATHEXT、路径来源与缓存

W02 的候选枚举改为使用调用方传入的环境。在一个公共候选函数中，枚举该环境 PATHEXT 给出的名字，对每个现有来源目录按 Python 3.12 的 `shutil.which` 查找规则核对，限定在该绝对目录内、排除隐式当前工作目录搜索，去重并保留后缀顺序；保留同目录多个候选以维持现有版本选择，而不只取 which 的第一个返回值。不再维护另一张固定 `.exe/.cmd/.ps1` 列表。显式 JS/无后缀 Node 与手动路径保留；没有列在 PATHEXT 的 `.ps1` 不额外猜测。PATHEXT 只决定去哪里找什么名字，解析器再按支持的入口类型给出命令或明确拒绝，不能把 PATHEXT 当作执行授权。

具体 `shutil.which` 在自定义 environment 下的 PATHEXT、大小写和当前目录行为必须先读实现环境的 Python 3.12 标准库并在 Windows CI 核对；不能在查找时临时改进程全局环境，因为服务有并发扫描。测试覆盖 PATHEXT 未设、空值、自定义顺序、大小写与重复项，以及当前目录的同名诱饵。`has_candidate` 和 `_node_for` 使用同一规则，避免 PATH hint 与真正发现各认一套名字；保留有界候选数和原有来源优先级。

注册表读取在输入边界保留类型，只对 `REG_EXPAND_SZ` 按 Windows 环境展开规则展开一次，明确区分注册表来源和给定进程环境；普通 `REG_SZ` 不误展开。常见目录从 APPDATA、LOCALAPPDATA、ProgramFiles、SYSTEMROOT 等实际环境/系统目录来源取得，不重造 `C:` 和 AppData 布局。优先用已有目录，缺少来源时不猜另一盘符；Windows app bundle 的默认位置没有证据，不在这批补猜。新增路径来源测试与 PATHEXT 测试分别覆盖它们，不能把后者通过当成 W02 全部完成。

缓存不能继续只看到 shim：当前 `candidate_snapshot:244–265` 的候选和 Node stat 指纹不包含从 `.cmd` 提取的 JS 入口；`HarnessHealth.refresh:190–204` 可能按位置指纹直接重用记录。实现时把解析出的 Node 与 JS 文件 stat 纳入命令选择的失效依据，wrapper 自身仍保留指纹；只改 shim、只改 JS、只换 Node 和删除任何一项，都应使下次 preflight 重新发现。保留位置扫描只 stat 不读取候选内容的性质（`test_harness_discovery.py:78–95`），可从已选 command 中的绝对文件路径派生这些 stat，首次解析后再形成完整位置签名；不在每轮廉价扫描重读所有 wrapper。

新的位置签名纳入固定的本次启动规则标识，使旧健康缓存的 cmd/PowerShell command 在新执行的 preflight 中自然失效并重新发现；这是现有不透明指纹的计算规则，无须增加一个兼容旧 launcher 的分支。`runtime_selection.command_for` 继续消费此次发现的 argv，不能把旧缓存命令未经 preflight 交给新的执行。检查 `buddy/runtime/execution.py:389` 起的 preflight 链、目录读取和其他服务调用，给旧健康记录加入回归用例，证明实际运行前已经刷新；不能只测显式 refresh。

缓存方案保持 `command` 为 argv、既有健康记录字段含义和跨进程事实格式不变，不新增存储字段或改写封存证据；位置签名的 Node/JS stat 从已有 command 派生。若实现确实需要新存储结构或服务与执行之间的新字段，应先调整本设计，按仓库规则作显式升级、扩充 schema 27 基准夹具并重判中版本与 CONTRACT_VERSION，不能悄悄靠旧新双格式共存。已运行的执行仍用其固定运行时，不据新源码声称它们已采用新启动规则。

## 5. 测试里的假 harness

先校正数量的含义：在这个提交中，以 `tests/python/**/test_*.py` 为范围全文搜索 `chmod`，得到恰好 42 个文件；同范围出现字面 `#!` 的只有 11 个文件。前者包括凭据权限、目录私有性、Git 可执行位和故意拒绝的路径，并非 42 个文件都“创建 shebang 假 harness”。例如 `cli/test_cli.py:326–357` 是凭据/控制文件，`install/test_backup_windows.py:23–25` 是备份材料权限，`buddy/harnesses/claude/test_native_run.py:73–75` 则对共享 Python fixture chmod。迁移验收应清除所有需要启动的 POSIX 假入口，不能机械删除这 42 个文件中的 chmod，也不能把原本仅测试模式位的材料都变成可运行程序。

新增测试侧 `tests/python/fake_executable.py`，给出一个公共形态 `make_fake_executable(directory: Path, name: str, *, source: str | Path) -> Path`。`source` 是内联 Python 测试程序或现有 Python fixture 路径，返回在该测试私有目录生成的真实入口路径：Windows 为 `<name>.exe`，macOS/Linux 为 `<name>`。它只负责形成可执行入口，不伪造健康结果、模型目录或权限声明；版本、登录、初始化和协议行为仍由各家原来的 fixture 负责。输入 name 是单个文件名，输出材料全部属于调用测试的私有根，正文显式 UTF-8/LF；不 chmod 或覆写仓库中的共享 fixture。

选择成熟的 console-script launcher 生成器，建议将 `distlib` 作为仅供检查环境使用的依赖锁定，在三系统由同一个 helper 使用其 ScriptMaker/PE launcher 能力生成入口，指定当前检查进程的 Python 3.12。当前 `pyproject.toml`、`uv.lock` 没有 distlib；这是实现需要补入的依赖，不依赖机器恰好安装的 pip 或其私有 vendored 包，不下载浮动版本，不自己拼写 PE 或在 CI 临时编译 C 程序。准确 API、x64 launcher 资源、许可与发行包包含情况本轮未验证；实现时从锁定分发包的公开接口核对并运行 helper 的三系统测试后，才可把它描述成可用。

helper 生成一份自包含的 Python 包装脚本，再经生成器的公开脚本接口形成系统入口，不依赖 harness 环境保留测试模块的 PYTHONPATH。内联 source 落在私有根；Path source 只读执行原 fixture，例如使用标准库 runpy 的 `run_name='__main__'`，并显式给其相邻导入保留搜索路径，避免搬走 `mock_codex.py` 后丢掉 `protocol_items.py`（后者见 `tests/python/buddy/harnesses/codex/fixtures/mock_codex.py:11`）。保持原 fixture 的 `__file__`、参数尾部、stdin/stdout/stderr、退出码、信号/取消观察和相邻资源语义，不 chmod 共享源文件，不将参数串交给 shell。生成器的公开脚本 API 在实现时核实，不能依赖私有方法。Windows PE launcher 可能多出一个启动器进程，必须由生命周期测试观察其子 Python 与后代的结束，不能在日志里把 launcher 退出等同于 harness 已停止；POSIX 的有空格解释器路径也要由生成器正确处理并测试。这些属于 helper 的可验证要求，不能把库名当作完成证明。

调用点只拿返回路径。例如发现测试把 `make_fake_executable(bin_dir, 'codex', source=...)` 的返回值交给 `discover(..., manual_path=...)`，并另测仅 PATH 发现；Claude/Codex/ZCode 协议测试将返回路径放进自己的 `HMB_*_CLI` 或真实候选目录，DSH 也通过同样入口走发现与 ACP 启动。这样 Windows 经产品原有 `.exe` 路径运行，POSIX 经原生可执行路径运行；不能为测试向产品增加识别 Python 文件、预制 ready record 或测试专属脚本语法的通道。已有只测试传输且显式用 `[sys.executable, fixture]` 的用例（如 `dsh/acp/support.py:57–59`）本来就可移植；保留传输单元测试，并新增经 helper 发现的 DSH 整链用例。

需要迁移的已核实入口包括 `buddy/harnesses/test_harness_discovery.py` 的本地 executable 工厂；Claude 的 `test_claude.py`、`test_claude_config.py`、`test_claude_worker.py`、`test_native_run.py`、`test_claude_tool_evidence.py`；Codex 的 `test_codex.py`、`test_in_turn_inquiry.py`、`test_native_run.py`；ZCode 的 `test_native_config.py`、`test_zcode.py`、`test_zcode_request_input_probe.py`、`test_zcode_tool_evidence.py`；以及 `test_adapter_usage.py`、`buddy/roles/test_registered_run_wiring.py`、`blackboard/evaluation/test_other_native_observations.py` 对共享 fixture 的直接执行。`protocol/test_path_state.py`、`test_public_state.py` 的“不得调用原生程序”哨兵与 `console/test_console.py` 的诱饵也用同一 helper，避免 Windows 上由于脚本根本起不来而出现假通过。

不能把迁移限制在 chmod 命中：`buddy/harnesses/codex/test_no_tool_codex.py:56` 直接把已跟踪的 shebang fixture 放进 `HMB_CODEX_CLI`，同目录的 `test_codex_continuation.py`、`test_codex_tool_evidence.py`、`test_delete_sessions.py` 和 `blackboard/tasks/test_host_workflow_worker.py:151` 也有 fixture 入口或消费者，应随公共 fixture 工厂一并迁移；`buddy/runtime/test_worker_sessions.py:90–99` 复用另一个测试的 setup，须确认它取得 helper 返回路径。实施清点须联合检查 shebang、chmod、HMB_*_CLI、选中 command、fixture 路径及被启动的子进程，验收标准是所有需要启动的假程序都用同一 helper 或本来可移植的显式 Python argv，不能以“42 个文件已动过”代替整条启动链的验证。

T03 中假 uv、假 Python、计数 Git wrapper 等属于共同的“假可执行文件”问题：`install/test_runtime.py:88–117`、`install/test_launcher.py:31–61`、`blackboard/tasks/test_workspace_batch.py:266–269` 的通用测试程序改写为 Python 并使用同一 helper。夹具内部再生成 venv 解释器时也要使用本机 `Scripts/python.exe` 或 `bin/python` 的布局，不能只修最外面的入口。故意不可执行、格式损坏、POSIX 信号、FIFO 或文件 mode 的测试保留各自明确的目的和系统条件；不能给整文件加 Windows skip 来掩盖本应共用的启动契约。T03 其他未涉及的文件占用与权限机制继续由原计划负责。

## 实现文件与验证

实现预计修改 `src/hey_my_buddy/buddy/harnesses/discovery.py`，新增共享 `launch_command.py`，收敛 `claude/config.py`、`codex/config.py`、`zcode/config.py` 的开发态入口；调整 `runtime_selection.py` 与 `blackboard/service/harness_health.py` 的选择及缓存失效衔接，并核对 `buddy/runtime/execution.py` 的 preflight。四个 `native_run.py` 只在确有调用点需要收敛时修改，不在其中增加平台 shell 分支。解析错误须在发现诊断中保留路径和可操作原因，沿用现有失败结果外形；若需要新增结构化 reason code，则同步 `protocol/reasons.py` 及其约束测试，并按 Host 合同变化审定版本，不能用 `not-found` 掩盖“已找到但不支持”。

测试侧新增 `tests/python/fake_executable.py` 和有实际启动断言的 helper 测试，迁移上节列出的所有可执行夹具调用点，清点其余 chmod 命中以免漏掉间接复制/运行共享 fixture 的路径；`pyproject.toml`、`uv.lock` 以及检查环境的依赖准备补入锁定的 launcher 生成依赖。新增 launcher 解析专项测试可放在 `tests/python/buddy/harnesses/test_launch_command.py`，继续扩充 `test_harness_discovery.py`、`blackboard/service/test_harness_health.py` 和四家的现有运行器测试。实现行为稳定后更新 `docs/reference/harness-integration.md`、相关 harness 参考页与 `docs/reference/operations.md` 的支持入口说明，验收记录写入 `docs/acceptance/`；本轮不改这些文件。

测试按以下独立证据验收，均不用真实 CLI 或模型：

1. 在原生 Windows CI 上，经公开发现路径找到 helper 生成的四家入口，完成 `--version`、必要的假登录、各自假初始化以及一次假协议回合；同时覆盖手动路径与仅 PATH、已选记录交给运行器的路径。macOS/Linux 跑同组用例，证明 helper 不使生产解析器出现测试特例。
2. 给入口、Node、JS、私有状态根和 cwd 使用 NTFS 允许的空格、括号、中文、`&`、`^`、`%`、`!` 组合；把 `& | < > ^ % " !`、换行、反斜杠尾部、空串、中文和超长正文放在参数/协议数据里。`| < > "` 不能作为 Windows 文件名字符，不伪造这样的目录测试。假进程逐元素记录实际 argv，逐字节核对 schema、模型与 cwd，逐字节核对 stdin 正文，并用私有 sentinel 证明没有附加命令被执行。
3. npm shim 的解析用完整已核实模板样本覆盖相邻 Node、PATH Node、CRLF、大小写扩展名和 JS 相对位置；用假 Node 原生入口记录真实转发结果。未知模板、额外命令、未知环境赋值、动态变量、缺失入口/解释器、超限文件、错误编码和递归 wrapper 均在 spawn 前失败。负例断言没有创建子进程；不能仅断言解析函数返回了列表。四家的 fixture 初始化仍核对原生参数，尤其 Claude schema 与 ZCode provider 路径。
4. PATHEXT、注册表类型展开、常见目录实际环境、隐式 cwd 诱饵分别测试；`.bat`、`.ps1`、`.com` 即使被发现也明确不可启动。发现探针与正式运行必须采用同一个解析结果，`HMB_DEV_SOURCE` 的显式路径也走同一边界。
5. 从旧健康记录中的 cmd/PowerShell 前缀开始，不手动 force refresh，走真实 preflight 取得新命令；分别替换 shim、Node、JS，确认缓存失效。未知模板被拒时保留失败诊断；其他可用候选按现有优先级选出且显示最终来源。位置扫描继续只 stat，不读取所有候选文件。
6. helper 证明版本/认证分支、退出码、无缓冲双向协议、EOF、取消、快速退出和后代进程的真实结束；测试环境的 Python 或夹具路径带空格/中文时仍可启动。进程未知仍是未知，不把第二批 Job/ACL 缺陷用 mock 或放宽断言抹掉。未通过的 Windows 停止证据仍要单独上报。

按仓库检查入口运行受影响测试：`uv run --frozen python -m hey_my_buddy.cli.checks` 后跟具体测试文件或目录；清除继承的日常 runtime、state 和 agent 凭据变量，依赖只在锁定范围内安装。实现影响发现、测试共同设施和多个运行器，合入前在三个系统跑完整 Python 检查并保留结果。若第二批的独立问题仍阻断检查，逐项报告实际失败，不声称整轮通过。本项没有改前端，不为设计或纯 Python 入口变更重复跑前端/浏览器检查。

本轮验证边界：只读源码、规划与 ADR，完成设计文档和静态一致性检查；没有实现 parser/helper，没有运行 Python 检查套件，没有 Windows 启动、真实 CLI、模型调用、安装、日常看板或服务验证。Host 审过本设计后另开实现微任务，不能把本文或独立只读审查当作实现验收。
