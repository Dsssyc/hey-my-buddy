# ADR-012 提案：Claude Code 接入、全局 CLI 分发、基于证据的路由与宏任务视图

## 状态

实施进展（2026-09-26）：Claude Worker P1 与标题回退已经分别验收并安装，日常运行时为 0.12.0。用户随后授权继续控制台入口，并选择新会话接管写权限、旧会话只读；该切片在 0.13.0 源码候选中实现，验收和安装状态见[控制台入口记录](../acceptance/console-entry-0.13.0.md)。下面保留初稿时的设计背景；除这些已授权切片外，其余部分及第十二节未决选项仍为提案。

提案，2026-09-25 由用户与 Host 讨论形成，尚未实现、未验收，不改变当前契约（0.9.0、schema 11）。讨论中用户已确认的方向：以一台机器上的 `buddy` CLI 加单一 skill 取代按 Host 维护的 plugin，并允许 agent 执行升级；没有 effort 档位的模型使用 `default`；智能路由继续由黑板负责，不交还 Host，也不改为确定性排序；采用 Host 提供的 `routingBrief` 和预授权的升级阶梯；以被动方式统计 Host 注意力消耗；把宏任务放上黑板，只用于归档和面向人的浏览，并以时间轴呈现；先由 Codex 实现 Claude Code Worker，再由 Codex 作为 Host 把前端设计与审查委派给 Claude Code；用户使用额度有限的 Claude Pro 套餐，Claude 只用于设计与视觉审查，额度耗尽作为基础设施失败单独归类（见第六、十一节）。其余内容是本 ADR 的提议，待决事项见第十二节。

第二节列出的原生事实来自同日在本机 Claude Code 2.1.278 上的检查，全程没有发起模型回合；需要真实模型回合才能确认的能力均未验证，不得据此声明支持。本提案接受后，将取代 [ADR-007](007-neutral-core-and-single-current-contract.md) 中“唯一的 agent 入口是 plugin 内的 `skills/buddy/SKILL.md`、唯一的启动器是 `bin/buddy`”的分发表述及 AGENTS.md 的对应规则，单一 skill、单一 CLI 的原则保留。路由部分补充 [ADR-004](004-buddy-decision-support-and-console.md) 与 [ADR-010](010-production-workflow-repair-plan.md) 的选择输入，维持 [ADR-008](008-harness-owned-evaluation-maintenance.md) 的维护归属和 [ADR-011](011-runtime-refinement.md) 的模型族并发。

## 一、目标与不变量

目标有四个：让 Claude Code 既能作为 Host 使用 buddy skill，也能作为 Worker harness 接受委派；消除为每个 Host 维护一份 plugin 清单的负担；让路由真正服务于多智能体之间的比较优势；让委派记录对人可读。Host 聪明、世界知识丰富、执行能力强，但昂贵且额度有限；Worker 可以是快速的 flash 类模型，也可以是更强但性价比更高的模型，因此每个任务都应按其内容选择模型与 harness。

保持不变：Python 服务是 SQLite 的唯一写入者；Host 保留目标、授权、整合与验收；Worker 不能自行派发任务；未知保持未知。新增一条设计约束：Host 与 Worker 保持单纯，不为统计、路由或维护承担额外的规则和流程；所需事实应当作为它们既有动作的副产品，由黑板获得。

## 二、已核实的事实与验证边界

| 对象 | 已核实的事实（2026-09-25，未发起模型回合） |
| --- | --- |
| Claude Code 模型目录 | 以 `-p --input-format stream-json --output-format stream-json --verbose --safe-mode --no-session-persistence --strict-mcp-config` 启动，只经 stdin 发送控制协议 `initialize` 请求、不发送用户消息，即返回 `models[]`。每项含 `value`、`resolvedModel`、`displayName`、`description`、`supportsEffort`、`supportedEffortLevels`，另返回 `account.{apiProvider, tokenSource}`。`default` 与 `opus[1m]` 都解析为 `claude-opus-5[1m]`；`haiku`（`claude-haiku-4-5-20251001`）没有 effort 档位 |
| Claude Code CLI | 存在 `--session-id`、`--resume`、`--fork-session`、`--effort`（low 至 max）、`--json-schema`、`--mcp-config` 与 `--strict-mcp-config`、`--permission-prompts host\|none`、`--permission-mode`、`--setting-sources`、`--settings`、`--tools ""`、`--no-session-persistence`、`--safe-mode`；`--bare` 只接受 API key 或 apiKeyHelper。帮助中未列出的 `--permission-prompt-tool stdio` 被接受 |
| Claude Code 内嵌 schema | `result` 含 `session_id`、`permission_denials`、`modelUsage`、`total_cost_usd`，错误子类型含 `error_max_structured_output_retries`；`system/init` 回显 `cwd`、`model`、`permissionMode`、`tools` 与各 MCP server 的状态；hook 输入中的 `agent_id` 只在 subagent 内出现，可据此区分根线程；hook 事件含 `PostToolUse`、`PostToolBatch`、`Stop`、`SubagentStop`、`PermissionDenied`；存在 `can_use_tool` 与 `interrupt` 控制请求；`--json-schema` 通过一个 `StructuredOutput` 工具实现。stream-json 另有 `rate_limit_event`，其 `rate_limit_info` 含 `status`（`allowed`、`allowed_warning`、`rejected`）、`resetsAt`、`rateLimitType`（`five_hour`、`seven_day`、`seven_day_opus`、`seven_day_sonnet` 等）与 `utilization` |
| Host 侧 skill 加载 | Claude Code 扫描 `~/.claude/skills/`，加载 skill 时注入 “Base directory for this skill”；ChatGPT.app 附带的 codex 从 `$CODEX_HOME/skills`（默认 `~/.codex/skills`）读取独立 skill |
| 本仓库 | 写死 coding adapter 名称的只有 `schemas.CODING_ADAPTERS` 与 adapter 注册表；catalog 自动生成 `execution:<adapter>` 能力，console 不含 harness 名单。DSH workspace bridge 安装器写入运行它的那棵目录树中的绝对路径。运行时资产不含 `skills/buddy` 与 `docs` |
| 公开价格源 | `models.dev/api.json`（223 个 provider，使用原生模型 id，每个模型带 `last_updated` 与官方定价页链接，编码套餐以零成本 provider 单列）、OpenRouter `/api/v1/models`（460 个模型）、LiteLLM 价格 JSON 均可访问。同一模型在不同源的价格不一致：`deepseek-v4-pro` 在 models.dev 为每百万 token 输入 $0.435、输出 $0.87，在 OpenRouter 为 $0.783、$1.566；`deepseek-v4-flash` 分别为 $0.15/$0.6 与 $0.049/$0.098 |

未验证：`--json-schema` 能否接受 Buddy 结果中嵌套 `anyOf` 的 schema；`-p` 模式下 `can_use_tool` 的完整拒绝流程与 `interrupt`；hook 是否只投递给根线程；`--resume` 与私有绑定的配合；effort 被静默降级后的读回；订阅账号在 `-p` 模式下是否实际发出 `rate_limit_event`，以及额度耗尽时结果消息的具体形态；Host 沙箱下 buddy CLI 能否访问状态目录与 daemon IPC。检查所在的会话受沙箱限制，`claude auth status` 报告未登录、`tokenSource` 为 `none`，真实登录状态未验证。

## 三、分发：全局 CLI 与单一 skill

决定：规范的分发单元改为一台机器上的一个 `buddy` CLI 加一个 skill，不再为每个 Host 维护 plugin 清单与 marketplace。plugin 现在只承担三件事：让 skill 从自身位置找到 `bin/buddy` 和文档，为运行时提供源码，提供安装、更新和版本号。服务与 Worker 早已从独立于 plugin 缓存的内容寻址运行时执行，这三件事都可以不借助 plugin 完成；plugin 独有的 hook、MCP 与 agent 目前没有需要。

安装只做一次：从源码 checkout 执行 `bin/buddy install`，生成 READY 运行时并让 `current` 指向它，写入只转发到 `current` 的 `~/.local/bin/buddy` 小入口，把检测到的各 Host skill 目录链接到 `current/skills/buddy`，并记录升级源。此后源码 checkout 不再是运行依赖。写入 Host 配置目录需要用户同意，且只在安装时发生。

随版本变化的内容都经 `current` 访问：CLI、skill 文本、文档、DSH runner 与 bridge。运行时资产增加 `skills/buddy` 与 `docs`；运行时选择从“匹配 plugin 目录中的资产”改为读取 `current`，显式的 `BUDDY_RUNTIME` pin 保留给测试；workspace bridge 安装器改写 `current` 下的路径，避免 plugin 更新后 DSH 补丁仍指向旧的缓存路径。

SKILL.md 直接调用 PATH 上的 `buddy`，不再推算 plugin 根目录；文档链接改为 skill 目录内可解析的路径，或由 CLI 提供。Host 之间的差异写在正文的一个小节里：`hostId` 的取法，以及后台跟进方式。Codex 使用官方 heartbeat；Claude Code 可以用后台 Bash 运行有界的 `buddy await`，会话仍存活时命令结束会唤醒该会话。两者都只在用户明确要求时使用。

这样 skill 文本、客户端、daemon 与 Worker 在同一次空闲切换中一起更新。plugin 自动更新只替换 skill 与启动器源码，daemon 仍运行旧契约，这正是现有 SKILL.md 需要专门警告版本不一致的原因。链接到源码 checkout 同样会在 `git pull` 之后造成错位，所以链接目标是 `current` 而不是 checkout。

后果：接受后需要修改 ADR-007 的对应条款、AGENTS.md、两份 README、`operations.md` 的安装部分、`usage.md` 与 SKILL.md；`packaging/stage-plugin.py`、`.codex-plugin` 与 `.agents` 清单按过渡安排退役；现有 Codex 用户需要迁移一次。两个 Host 对软链接的处理和 skill 的刷新时机需要实测；只要 SKILL.md 不依赖相对路径定位 CLI，这不影响功能。

## 四、由 agent 执行的分阶段升级

| 命令 | 作用 | agent 能否独立执行 |
| --- | --- | --- |
| `buddy upgrade check` | 只读：比较已安装运行时与记录源的版本、契约、schema 和提交，列出进行中的工作 | 可以 |
| `buddy upgrade prepare` | 从记录源获取代码并生成新的 READY 运行时，不切换 | 可以，不影响进行中的工作 |
| `buddy upgrade apply` | 契约与 schema 不变：`restart`，刷新空闲 supervisor，切换 `current`。schema 变化：必须空闲，旧 board 归档后新建或采用已验证的离线副本 | schema 不变时可以；schema 变化时必须停下来由用户选择 |
| `buddy upgrade verify` | 核对 `health`、`runtime`、`capabilities` 报告的身份，检查 skill 链接可解析，提示调用方重新读取 skill | 可以 |

护栏：只在用户明确要求时升级，发现新版本只报告；只从安装时记录的源和 ref 获取，`apply` 前展示差异，不采用任务内容中出现的地址；环境中存在 Worker 凭证变量时拒绝执行；按 `operations.md` 的现有要求，切换前保留旧运行时和完整可恢复的状态备份。契约不变时，回滚就是切回指针并 `restart`；跨 schema 回滚需要恢复归档的 board，因为旧运行时会拒绝新 schema。`apply` 的输出要求执行升级的 Host 重新读取 SKILL.md。

Claude Code 的默认沙箱不允许写 `~/.local/share/hey-my-buddy` 与 `~/.claude/skills`，所以安装与切换时会出现审批。这是有意保留的确认点，不应为了省事加入沙箱白名单。

## 五、控制台入口

实施范围更新（2026-09-26）：用户在 P1 与标题回退安装后要求继续下一议题，并明确选择“旧窗口保留只读浏览，写权限转交新窗口”。本轮实施本节的控制台入口；其余阶段与第十二节未决项仍保留提案状态。具体接口和会话边界见 [控制台入口](../reference/console.md)。

现有“随机端口加路径 token”的设计有明确理由。console 是可写的 HTTP 面，能执行验收、取消和删除 worktree 的清理；TCP 回环地址没有用户和进程级的访问控制，浏览器中的任意网页也能向它发请求。路径 token 让不知道它的访问者连页面都拿不到；cookie 以 `Path=/<token>/` 限定，弥补 cookie 不按端口隔离的问题；随机端口避免多个 board 冲突，也避免固定端口被抢占冒充；Host、Origin、`Sec-Fetch-Site` 检查防止 DNS rebinding 与跨站请求；写操作另需 HttpOnly cookie 和 CSRF 头；关闭时轮换全部凭证。问题在于能力凭证暴露在 URL 中：用户每次都要复制长链接，链接会进入 agent 的对话记录和日志，并且在关闭或重启之前一直可写。

决定：

- `buddy console open` 由 CLI 进程直接拉起默认浏览器，daemon 不启动浏览器。
- CLI 打开一次性启动地址，启动令牌约 60 秒过期、只能使用一次；服务端验证后下发 HttpOnly 会话 cookie，并重定向到不含凭证的地址。打印到对话记录中的链接因此很快失效。
- 去掉路径前缀后，改用专属主机名 `buddy.localhost` 和只属于该主机名的 cookie 维持隔离。Safari 对 `*.localhost` 的解析需要实测；不满足时保留路径前缀，只去掉可长期复用的凭证。
- 页面每 3 秒轮询一次；连续若干分钟收不到轮询时自动关闭 console，也可以用 `open --wait` 在前台运行、Ctrl-C 关闭。关闭 console 不影响任何任务。
- 端口保持随机，入口始终是 `buddy console`，不支持书签。
- 沙箱不允许启动浏览器时，CLI 打印一次性链接；Claude Code 桌面端可以在内置浏览器面板中打开它。
- 现有的 Host、Origin、`Sec-Fetch-Site` 检查，写操作 CSRF，不使用通配 CORS，以及同用户诚实边界，全部保持不变。

原生窗口（例如 pywebview）可以彻底隔离 cookie，但会引入额外依赖，Linux 上还需要 GTK 或 Qt，暂缓。

## 六、Claude Code Worker adapter

| Buddy 要求 | Claude Code 机制 |
| --- | --- |
| 进程归属与取消 | Python controller 以独立进程组运行 `claude -p --input-format stream-json --output-format stream-json --verbose`；取消时先发 `interrupt`，再关闭输入并终止进程组，结构沿用 Codex controller |
| 不发起模型回合的目录发现 | `initialize.models` |
| 结构化结束结果 | 第一阶段使用 `--json-schema`，读取 `result.structured_output` 后仍由 `validate_outcome` 校验；需要 inquiry 时改为会话私有的 MCP `buddy_finish_turn`，复用 ZCode 的签名回执，以 `parent_tool_use_id` 为空认定根线程调用 |
| 原生权限请求 | 以 `--permission-prompt-tool stdio` 接收 `can_use_tool`，拒绝并记录，结合 `permission_denials` 生成 controller attention，做法与 Codex 一致 |
| 配置隔离 | 私有 `--settings`、`--strict-mcp-config`、限定的 `--setting-sources`，从不写全局设置 |
| 原生会话 | `--session-id` 由 Buddy 预先分配，续接用 `--resume` 并校验私有绑定；会话保存在用户自己的 Claude Code 项目目录，`storageScope` 为 `harness-user-store` |
| Inquiry | 只在根线程触发的 `PostToolUse` hook（输入中没有 `agent_id`）以 `additionalContext` 投递问题，由 MCP `buddy_answer_inquiry` 作答，`Stop` hook 在问题未答时阻止根线程结束；每次工具调用后自动投递，不注入原生回合输入 |
| 实际服务的模型 | `modelUsage` 可能包含 subagent 使用的其他模型，记录为观测到的模型集合，不写成单一的 `observed` |

目录规则：以 `resolvedModel` 作为模型 id，不使用会漂移的别名；跳过 `default`，相同解析结果去重。没有 `supportedEffortLevels` 的模型使用 effort `default`，执行时不传 `--effort`，读回记为不适用；有档位的模型不提供 `default`，以免默认档位变化后同一 profile 混入两种配置的证据。

provider 身份：第一阶段只支持第一方 Anthropic（订阅登录或 API key），provider 记为 `anthropic`；设置了 `ANTHROPIC_BASE_URL` 等第三方覆盖时拒绝执行，保证 provider 身份真实；Bedrock、Vertex 以后按 `apiProvider` 增加。`tokenSource` 为 `none` 时，`available()` 报告不可用。订阅额度可能与用户自己的交互会话共用，模型族并发计数看不到这部分占用。

额度耗尽单独归类，这是 P1 的要求。当 `rate_limit_event` 报告 `status` 为 `rejected`，或者结果因额度被拒时，controller 把该尝试记为额度失败：终止原因仍是现有的 `harness-error`，失败结果附带一个有界的额度信息块，只含 `rateLimitType` 与 `resetsAt`，做法与 ZCode 的 `nativeFailure` 白名单一致，不导入原始错误文本。额度失败不自动重试，不计入模型能力的统计，也不触发升级阶梯；Host 可以据 `resetsAt` 决定等待，或把工作转给其他 Worker。`allowed_warning` 与 `utilization` 作为该额度池的最新观测值记录下来，供第八节使用。测试可以用模拟 CLI 构造 `rate_limit_event`，不需要真的耗尽额度。

分阶段实施：P1 为 `--json-schema` 结果、权限拒绝转 attention、每次续接都重建会话；P2 为原生续接；P3 为 cooperative inquiry；P4 为可选的无工具决策实现，供只安装 Claude Code 或 Codex 的用户使用黑板路由。P4 使用 `--safe-mode --tools "" --strict-mcp-config --no-session-persistence --system-prompt`，以 init 中 `tools` 为空和单回合结果证明没有使用工具；它不能用 `--json-schema`，因为后者本身是一个工具。

用于前端设计与审查时，P1 还需要满足以下执行条件：Worker 可以在分配的 worktree 内编辑文件、运行构建和测试命令，Bash 在 Claude Code 自带的沙箱中执行，写入限于该 worktree；新 worktree 不含被忽略的 `node_modules`，因此网络只放行包管理器的注册表，或者由 Host 在准备阶段提供依赖；其他需要审批的请求一律拒绝并转为 attention。环境提供无头浏览器时，界面任务可以截图自查，并把截图随结果交给 Host，最终的视觉验收仍由 Host 负责。审查任务使用只读工作区，或把写入范围限定为一份审查报告；审查结论不替代 Host 的验收。

涉及的改动：新增 `adapters/claude.py`、`claude_runner.py`、`claude_protocol.py`、`claude_config.py`；更新注册表与 `CODING_ADAPTERS`；在 `tests/python` 下增加模拟 CLI 与测试，在 `tests/probes/` 下增加真实探针；新增 `docs/reference/claude.md`，并更新 workers、architecture、README 与 SKILL.md。真实探针会消耗模型额度，每次运行都需要用户授权。

## 七、路由：保留在黑板，补齐目标与输入

用户把路由从 Host 的职责中拆出，理由有两个：Host 有选模型的习惯性偏好；每次提交都重读候选表会破坏上下文缓存，并加速上下文腐烂。确定性排序看不到任务内容，无法实现按任务内容的比较优势，因此不采用。

现有选择器不可靠，主要原因在输入与目标，而不只是模型能力。`decision-prompt.mjs` 第 6 版的规则几乎都在约束合法性，没有说明降本增效的目标；“没有明显更优的候选时弃权”使证据稀疏时的决定以 `needs-host` 退回 Host；传入的 profile 只有目录描述、能力和上下文长度，没有价格档位、速度、额度池和实时占用，也不知道委派方 Host 是谁；卡片是散文，`sampleCounts` 只按 profile 汇总；任务文本是写给 Worker 的执行说明；0.6 启用时，选择器配置为 DSH Flash/off，即非思考模式。

决定：

1. **`routingBrief`**：Host 在 `submit` 中附带少量固定字段，例如 `kind`、`difficulty`、`specClarity`、`verification`、`scope`、`contextNeed`、`urgency`。字段定义写在 skill 的稳定前缀中；Host 不读候选表，也不写模型名，习惯性偏好没有传导渠道。这些字段是给选择器的特征，不是查表规则。例外情况继续使用带理由的 `host-override`；现有的任务级 `routingPreferences` 保留给用户明确提出偏好的场合。
2. **目标与经济数据**：选择指令写明目标——在满足任务风险要求的前提下，选择预期总成本最低的配置，总成本包含 Host 的监督成本。每个候选附带计费方式、价格档位、额度池、速度与实时占用；输入包含委派方 Host 的身份与额度池。
3. **结果统计**：输入包含第九节按任务特征汇总的结果账，而不只是散文卡片。
4. **不因证据稀疏而退回 Host**：选择器必须在合法候选中给出主选、备选和置信度；只有没有合法候选，或选择器本身失败时，才形成 Host 路由边界。
5. **预授权升级阶梯**：Host 在提交时可以授权有限次数的自动升档，建议默认最多一次。当尝试失败且没有有效结果，或以 assistance/attention 结束时，由选择器判断原因是否属于能力不足：属于，则按备选或更高档位自动续接；不属于（超出授权范围、缺少信息、需要 Host 决定），则仍停在 Host 边界。额度失败不属于能力不足，不触发升档。自动续接只发生在该尝试确认停止之后，不把不确定状态转换为重试。验收命令越可靠，越适合从便宜档起步。
6. **离线评估**：用少量历史目标加上用户标注的合适档位组成评估集，比较 prompt、选择模型和思考开关的不同组合。它只消耗路由调用，用来取代按个案修改 prompt 的迭代方式。

选择器提示词的稳定前缀和评估表前缀的缓存设计保持不变，`routingBrief` 位于可变的尾部。

## 八、经济数据

仓库不维护价格表，只为每个 adapter 维护一张从 Buddy provider 到 models.dev provider 的小型身份映射。价格在模型目录刷新时一并获取，并沿用目录观测的规则：记录来源与日期，单调更新；获取失败时保留上次结果并标记为未知，不影响其他数据。models.dev 为首选来源，因为它使用原生模型 id 并单列编码套餐；OpenRouter 的价格来自聚合平台，只作参考。

传给选择器的是计费方式（按量、订阅额度、免费）与价格档位，而不是精确单价。不同来源之间的价格可以相差数倍，但 flash、pro、sonnet 这类档位的顺序一致，1.5 倍以内的误差不会改变路由结果。人民币计价、错峰折扣、订阅额度等公开源表达不全的情况，由用户在 console 的模型卡片上覆盖，位置与并发限额相同；它属于用户策略，Host、Worker 和维护发布都不能写入。

额度池身份来自 adapter 的账号读回或用户在 console 中的声明，不根据名称推断，这与 ADR-011“相同 provider 名称不能证明是同一账号”的原则一致。例如 Claude Code Host 委派给同一账号下的 Claude Code Worker，节省的是单价，而不是额度。对于订阅额度池，Claude Code 的 `rate_limit_event` 可以提供实时使用率与重置时间，作为该额度池的观测值交给选择器；没有报告时保持未知。

长期以 harness 自报的实际用量为准，例如 Claude Code 的 `total_cost_usd` 与 `modelUsage`；实际用量已经包含了“便宜模型更啰嗦、消耗更多 token”的影响。没有报告时保持未知，不换算，也不补造。获取价格只请求公开 JSON，不携带任何凭据。

## 九、委派结果账：被动统计 Host 注意力

原则：最接近任务的角色只通过它们本来就要做的动作产生事实，判断在离线汇总中完成。不需要回调，Host 与 Worker 不增加任何规则或调用。

触发点是目标结算。在 acknowledge、cancel 或失败时，黑板在同一事务中从已有记录生成该目标的结果账：

| 信号 | 来源 | 含义 |
| --- | --- | --- |
| Host 边界次数 | assistance/attention 请求 | Worker 需要 Host 出面的次数 |
| 续接轮数 | turn 与续接记录 | Host 提供新输入的次数 |
| 改派与升档 | 路由记录、配置覆盖 | 初始路由是否合适 |
| 失败尝试 | 终止原因 | 执行是否稳定 |
| 验收结论 | acknowledge 的 verdict | 结果是否可用 |
| Host 修改量 | integration 验证 | Host 接手修补的范围 |
| 耗时与用量 | 时间戳与 harness 自报用量 | 速度与实际花费 |

Host 修改量是最可靠的信号。验收前 Host 必须记录整合，服务在 `workspace.integration_verify` 中直接从 Git 比较 Worker 产物与最终落地内容，把每个路径分为一致、不同、缺失三类，未申报的差异会使验证失败。所以“原样接收”还是“修补了哪些文件”，是经过机器验证的事实，不依赖 Host 自述。还可以进一步由整合前后的树计算 Host 在产物之外额外改动的路径。

结果账以 `routingBrief` 为分组键，由服务按“profile × 任务类型 × 难度”维护计数器；这些计数器与 `sampleCounts` 一样由服务派生，调用方不能写入。某一格样本不足时，依次回退到“profile × 难度”和 profile 总体，并把样本数一起交给选择器。升过档的目标记为“在 X 上失败、在 Y 上成功”；每个 turn 归属于执行它的配置，helper 各自有独立的结果账。评价维护 Harness 仍按 ADR-008 在用户要求时运行，可以选择把这些统计解读为卡片文字。

额度耗尽等基础设施失败单独计数，不计入模型能力的统计，这与 ADR-008 中“基础设施错误不能与质量样本混同”的原则一致。

局限：黑板看不到 Host 自身消耗的 token，只能用边界次数、续接轮数和修改量近似；不统计 Host 调用 `get`/`await` 的次数，那主要反映轮询习惯。黑板只能观察到被选中路线的结果，存在选择偏差；升级阶梯会产生一部分对照数据。如需更多，可以提供一个由用户控制、默认关闭的探索开关，只对低风险且有自动验收的任务偶尔尝试更便宜的档位。

## 十、宏任务与委派时间轴

### 黑板的定位

经典黑板架构把逐步演化的解状态放在共享空间，因为没有任何一个知识源能独立把握整个问题。Buddy 的 Host 能够把握整个问题，它委派工作的理由是经济上的，而不是认知上的。因此 Buddy 的黑板在控制侧符合黑板架构：发布的任务不写收件人，由黑板按能力边界集中路由，这相当于经典架构中集中评估各知识源触发条件的控制器；各方只通过黑板交流。解状态有意留在 Host 手里，这是设计选择，不是有待补全的缺口。

宏任务放上黑板只为两个目的：归档，以及面向人的浏览。Worker 看不到宏任务；它不是 Host 需要维护的状态；它不作为路由输入，不建依赖图，也不自动释放后续任务。Host 在自己的上下文和原生计划工具中规划与协调，黑板只从 Host 既有的动作中推导出可浏览的记录。

### 现状

委派列表的条目名是 Host 提示词的第一行，截断到 100 个字符（`apps/console/src/Tasks.tsx`），看不出微任务要做什么。同一用户请求派生的多个根目标之间没有任何关联，只有 Worker 求助产生的 helper 与父任务之间有父子关系。

### Host 多提供的两项

- `objective`：宏任务。用户一个新目标的第一次 `submit` 携带 `objective: {title}`，服务创建宏任务并返回 `objectiveId`；此后属于同一宏任务的 `submit` 携带这个 `objectiveId`。宏任务归属于提交时的项目和 Host；是否开启新的宏任务由 Host 判断，通常对应用户提出了新的目标。Host 丢失 id 时，可以按项目列出近期的宏任务找回。名称避开仓库中已有的术语：goal 指单个 run，request 指求助请求。
- `title`：每个微任务一条写给人看的简短标题。缺省时依次回退到该 run 最近一轮 Worker 结果中的 `summary`，再回退到任务文本的第一行。

两项都是可选的，不影响执行、路由和验收。helper 自动继承父任务所属的宏任务，Host 与 Worker 都不需要额外提供。每次提交只多十几个 token，Host 在微任务完成后也不需要“维护黑板状态”。

### 存储

新增 `objectives` 表，保存宏任务 id、项目标识、Host 归属、标题和创建时间；run 上增加可空的 `objective_id` 与 `title`。宏任务的状态不单独存储，而是由其下各 run 推导：进行中、待决定、待验收、已结束的数量，以及最后活动时间。宏任务没有“完成”操作，Host 不需要关闭它。旧记录没有宏任务，各自显示为只含一个微任务的宏任务；这是字段为空时的显示规则，不是兼容分支。schema 需要递增，按现有规则准备经过验证的离线副本，并在空闲时切换。

### 时间轴的推导

时间轴全部由已有记录只读推导：

| 时间轴元素 | 来源 |
| --- | --- |
| 微任务起点 | run 的创建时间，即 `submit` |
| 排队与路由 | 从创建到第一个 attempt 开始；路由决策任务自身的执行区间 |
| 执行片段 | 每个 attempt 的 `started_at` 至 `finished_at`；每轮续接是一段新片段，颜色表示实际执行的 adapter 与模型 |
| 等待 Host | 求助请求的 `created_at` 至 `decided_at` |
| helper 子轨 | `workflow_children` 中的父子关系，嵌套在父微任务下 |
| Host 标记轨 | 派发、决定、续接、整合、验收、取消等事件的时间 |
| 结算 | 验收时间或终止时间 |

“波次”不单独建模：并行的片段在时间轴上重叠，自然呈现为一波。

### 交互

- 委派记录页改为“项目 → 宏任务”两级分组。宏任务条目显示标题、微任务数量、各状态计数和最后活动时间；现有的进行中、待决定、待验收筛选作用于宏任务下的微任务。
- 进入宏任务后显示时间轴：每个微任务一行，按开始时间排序；helper 缩进为子行；顶部是 Host 标记轨；正在执行的片段随现有的 3 秒轮询延伸，并有一条“现在”线。
- 超过阈值的空闲区间（例如 30 分钟内没有任何片段和事件）折叠为标明时长的断点，可以展开，避免隔夜等待把时间轴压扁。
- 点击片段打开现有的任务详情，并定位到对应的轮次；点击 Host 标记显示对应的决定或验收。
- 颜色按执行配置区分并附图例，等待 Host 用虚线框表示；失败和取消的片段带明确的文字或图标标记，不只靠颜色区分。
- 窄屏下退化为按时间排序的列表。

### 接口

新增只读操作，例如 `objective_list` 和 `objective_timeline`。前者按项目、Host 和状态分页，使用与现有 `task_list` 一致的键集游标；后者返回单个宏任务的有界时间轴数据，微任务与片段数量都有上限，超出时报告截断，不静默丢弃。console 相应增加 HTTP 读取路由，CLI 提供同名命令供 Host 找回宏任务。这些读取不获取租约，也不调用模型。

宏任务为结果账提供按用户请求聚合的视图，例如这一次请求派生了哪些微任务、各自消耗了多少 Host 注意力。由于没有“Host 自己做”的对照数据，宏任务层面不声明节省了多少成本。Host 自己完成的微任务不要求登记，时间轴上只出现 Host 的派发、决定、整合和验收足迹。

## 十一、建议实施顺序

用户于同日调整了顺序：先由 Codex 实现 Claude Code Worker，此后 Codex 作为 Host 和整合者，把前端设计与视觉审查委派给 Claude Code，发挥它在视觉设计上的比较优势。Claude Code Worker 不依赖第三节的分发改动，Codex 在此期间继续通过现有 plugin 担任 Host。

1. **Claude Code Worker P1，由 Codex 实现。** 只改 adapter 模块、注册表、`CODING_ADAPTERS`、测试与文档，不改数据库 schema；契约版本递增，需要与 2026-09-25 正在进行的黑板性能优化约定版本号。第二节列出的未验证事项先用真实探针确认，每次探针都需要用户授权。
2. **按下方的初始分工开始委派。** 用户能操作 console 时，启用表中的 Claude profile，把各 Claude 模型族的并发上限设为 1，并用偏好和备注记录分工；在此之前，由用户把这张表交给 Codex，Codex 在这些任务上提交完整配置，黑板记为 `original-explicit`，不调用选择器。无论采用哪种方式，同一时间只保留一个 Claude 任务在运行：Opus 与 Sonnet 属于不同的模型族，各自的并发上限挡不住它们共用的额度。
3. **前端与审查工作经 Buddy 委派**：先做不改 schema 的标题回退；然后是控制台入口的前端部分，以及宏任务时间轴的界面，这两者的后端接口与 schema 由 Codex 实现，宏任务的 schema 需在性能优化合入之后递增；另外包括对 Codex 自身改动的视觉审查。被委派的是新的无头 Claude Code 进程，它不带本次讨论的上下文，任务包需要引用本 ADR 的相关章节。
4. **委派结果账。** Claude Code 加入后，按任务类型比较各 Worker 的结果对路由最有价值，越早上线，积累的数据越多。
5. **路由改进**：`routingBrief`、选择器的目标与输入、经济数据，之后是升级阶梯与离线评估集。
6. **Claude Code Worker P2、P3**，P4 视需要实施。
7. **分发与升级**（第三、四节），以及 Claude Code 作为 Host。它们不影响以上各步，可以放在后面。

每一步形成独立的契约版本和验收记录，按现有规则在空闲时切换。

### Claude 额度有限时的初始分工

用户使用 Claude Pro 套餐，额度有限，并与自己的交互会话共用。冷启动阶段没有 Claude 的评价卡片与结果账，分工由用户事先确定：

| 工作 | 执行者 | 思考强度 |
| --- | --- | --- |
| 新界面的视觉与交互设计（例如宏任务时间轴，一次性） | Claude Code，账号目录中最新的 Opus | high |
| 带截图的视觉与交互审查 | Claude Code，Sonnet 5 | medium |
| 按设计稿实现、接入数据、标题回退、编写测试 | ZCode 的 GLM（编码套餐，按量成本为 0）、DSH Flash 或 Codex 本身 | 由路由或 Codex 决定 |
| 代码正确性与安全审查（控制台令牌流程、adapter 进程管理） | Codex 本身 | — |

Claude 只用在最依赖审美判断的两处：先由 Claude 给出设计稿和一两个关键组件，再由其他 Worker 按稿实现，最后由 Sonnet 看截图审查一次。不使用 Fable 5.1（按 models.dev 的单价是 Opus 5 的两倍），不使用 xhigh 与 max 思考强度（思考 token 同样计入额度），也不使用没有思考档位的 Haiku。可用模型以 P1 在用户登录状态下发现的目录为准；按 models.dev 的单价，Opus 5.5 低于 Opus 5，目录中有它时优先使用。

额度耗尽时，任务按第六节以额度失败结束。Codex 不重复提交，而是等到 `resetsAt` 之后再委派，或把工作转给其他 Worker。这张表只用于冷启动；结果账积累足够的数据之后，改回由路由选择。

## 十二、待决事项

- Codex plugin 随第一步退役，还是保留一个过渡版本。
- 升级源的默认形式：git 远端加 tag，还是本地 checkout；无论哪种都记录在安装元数据中。
- 已决（2026-09-26）：通过新控制台入口建立的会话接管写权限；旧会话保留只读浏览，未保存的本地草稿保留。旧页刷新不得重新取得写权限；交接只在新入口成功兑换之后发生。
- `routingBrief` 的最终字段与取值。
- 升级阶梯的默认次数，以及判断“能力不足”的具体规则。
- 已决（2026-09-26）：用户在四次逐次授权的 P1 探针后确认，日常 Claude Worker 采用同一隔离策略：仅 Buddy 私有设置、空 setting-sources、严格 MCP 配置；不在 P1 加入额外的项目设置或 CLAUDE.md 继承策略。该确认不批准本节其他事项，也不授权额外的付费探针。实际验证与安装状态见 [P1 验收记录](../acceptance/claude-worker-p1-0.11.0.md)。
- 是否提供探索开关。
- 是否实施 P4。
- 宏任务的命名（本 ADR 暂用 `objective`，界面称“宏任务”），空闲折叠的阈值，以及宏任务列表的默认排序。
- 是否为 Host 自己完成的工作提供可选的登记。
- 额度池使用率达到多少时，选择器应避开该额度池的配置。

## 十三、验收要求

实现后按现有规则在 `docs/acceptance/` 记录实际证据：两个 Host 各完成一次全新安装、一次 schema 不变的升级和一次回滚，并核对软链接与 skill 刷新；console 启动流程在 Chrome、Firefox 和 Safari 中验证，并确认过期链接不可用；价格观测的单调更新与失败保留；结果账在私有状态目录中用构造的目标逐项验证各信号，包括 Host 修改量；路由的不同组合在离线评估集上的结果；Claude Code Worker 各阶段的真实探针，每次运行都经用户授权；额度失败的分类、不重试和不计入能力统计，用模拟 CLI 构造的 `rate_limit_event` 验证。宏任务时间轴在私有状态目录中用构造的记录验证推导结果，覆盖并行波次、续接、helper、等待 Host、取消与失败、空闲折叠、超出上限时的截断，以及没有宏任务的旧记录；界面在真实浏览器中按多种宽度检查，并确认这些读取不获取租约、不调用模型。源码验收、安装状态与运行中的服务仍是分别记录的事实。
