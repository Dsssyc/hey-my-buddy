# ADR-021 逻辑线第二阶段执行计划

本文是 [ADR-021](../decisions/021-router-buddy-planes-and-routing-evidence.md) 第 2、3、4 条在 L4、L5、L6 中的实现设计，基线为 `socu/buddy-core` 的 `cf371da`，工作分支为独立 worktree 中的 `socu/adr021-logic-stage2`。已读 `AGENTS.md`、`CONTEXT.md`、[第一阶段验收](../acceptance/adr021-logic-stage1.md)、[ADR-007](../decisions/007-neutral-core-and-single-current-contract.md) 与 [当前架构](../reference/architecture.md)。本次先提交计划，等待用户回复后才委派或实现；L4 集成并完成模块验收后，才开始 L5、L6；本阶段结束于 L6，不开始 L7 或后续模块。

## 范围与交付边界

实现一个 Router 设置、取消自动降级、改用不调用模型的审阅资格检查、实施审阅运行的四项防护、删除付费审阅验证的代码与入口，随后分别实现 DSH、ZCode 的只读工具回合。Host 自行修正 `accept`、`conclude` 的帮助，把 `note` 标为必填。

Python 黑板仍是权威状态的唯一写入者；Worker 运行时仍拥有原生子进程、取消与停止回执。Router 没有黑板凭据、受管业务回合、完成工具或成果权限，结构化答案只经过程序的格式与路由边界检查，不增加 Host 验收。第 3 条的维护面与画像面只作为权限边界写进契约，本次不实现它们的触发、数据包、存储或工具。

本阶段不改 ADR 决定，不做界面设计，不改通用 `configuration`、`profile` 等接口与存储字段的用语，不改 schema 版本，不做 L15 的升级或存量转换，不改 `SKILL.md`、Host 指南、README。新增单一 Router 字段并删除两个位置是 L4 的行为变更；其余名字保留给 L14。只修改各模块拥有的参考文档，对 `apps/console` 只做类型、解析、删除失效控件和必要的表单接线。

不安装、不升级日常运行时，不修改用户配置或日常数据，不登录、退出、更换密钥或读取凭据文件。委派使用已安装并运行的 `~/.agents/skills/buddy/scripts/buddy` 0.27.0；其固定运行时独立于本 worktree，不另起用于委派的私有或隐藏服务。测试与原生检查的隔离目录不构成另一套委派服务。

## L4 的接口设计

### 单一设置与旧设置换算

现行对外设置定为 `configuration.routerProfileId: string | null`、`configuration.defaultRoutingMode: fast | review`、`configuration.routingBudget: brief | standard | deep`，保留现有 `revision` 与只读 `routingBudgetLimits`。`routerProfileId` 引用已发布、具有完整四元组的现有 `evaluation_profiles` 行，不另存一份 buddy 身份。删除 `fastRouterProfileId`、`reviewRouterProfileId`，不恢复已经退役的 `decisionProfileId`。保留 `defaultRoutingMode`、`routingBudget`、`profileId` 等现有名字，避免把 L14 的术语改名混入本阶段。

内部在 `router_settings.py` 定义不可变 `RouterSettings` 和设置补丁校验，使用 `meta.router_profile_id`、现有 `router_default_mode`、现有 `router_budget_preset` 与 `router_configuration_version=2`；不新增表，不改旧列名。全新测试看板初始化为 `{routerProfileId: null, defaultRoutingMode: fast, routingBudget: standard}`。设置仍只由认证控制台的人类写入授权发布，使用现有 writer grant、revision 比较与事务/事件原子性，Host 与 Worker 不能修改。

发布是字段补丁：省略的字段保留，`routerProfileId: null` 明确取消选择，空补丁拒绝，未知字段和旧位置拒绝，公开 API 不接受 `quick`。检查合并后的有效设置；选择 buddy 或变更模式时，非空 Router 必须已发布、启用、可用、身份完整，并满足所选模式的本地资格。清空 Router 允许成功。仅改预算或无关用户设置时，不因原 Router 已变得不可用而阻止保存；不可用事实由状态读取与路由边界报告。Router 是用户直接指定的，不按 Worker 的优先、固定或排除偏好重新挑选。

新增纯函数 `convert_legacy_router_settings(legacy) -> RouterConversion`，返回新的 `RouterSettings` 与被舍弃的另一位置的 ID，用于 L15 的审计。输入仅是旧设置的值；函数不接收连接、状态目录、harness、账户或时钟，不查询健康与额度，不调用模型，不写数据。换算规则固定如下。

- 原 `defaultRoutingMode=fast`：取 `fastRouterProfileId`，保留 fast。
- 原 `defaultRoutingMode=review`：取 `reviewRouterProfileId`，保留 review。
- 所选位置缺失或为 null：新 Router 为 null，模式保持；另一位置即使存在、健康或更便宜，也不替换。所选 ID 不可用时仍保留该 ID，由正常资格检查说明原因。
- 预算 `brief`、`standard`、`deep` 原样保留；历史 `quick` 换算为 `brief`；旧数据缺少模式或预算时分别采用原默认值 fast、standard。非法模式、预算、ID 类型拒绝，不猜测。
- 两个位置相同与重复调用都产生相同输出；被舍弃的另一位置只进入转换报告，不成为备用 Router。函数不会修改输入对象，也不解析更早的 `decision_profile_id` 版本，后者属于 L15 的升级前置整理。

换算函数只供显式升级调用，本阶段没有生产调用点。当前设置读取、看板启动、路由和用户发布都不调用它，不把旧位置投影成现行设置。读到旧设置标记时 `configuration()` 抛出 `BoardError(router-settings-upgrade-required)`；resolve 将它变为 Host 路由边界，涉及 Router 的设置发布拒绝写入。控制台快照此时返回 `configuration: null` 与 `configurationError: {code, message, revision}`，其余读数据正常返回；现行设置下 `configurationError: null`。不伪造已选择的 Router，不阻塞无关设置操作。已有旧键与证书数据的物理清理交给 L15，L4 只停止读写它们。

### 不调用模型的资格检查

适配器增加 `local_read_only_check() -> ReadOnlyEligibility`，结果含 `eligible`、`reasonCode`、`reason`、固定工具清单、是否具有原生沙盒、是否支持同一 attempt 的格式纠正。它校验本地实现、执行入口、限制工具集的配置生成器与工具处理器的一致性；只有布尔声明或方法继承自基类不算实现。基类返回不支持。检查不启动模型或会话，不探测账户、不读取凭据、不联网、不写状态；native 版本与路径健康继续使用黑板已有的本地发现结果。没有可确认的工具限制方式就判定不合格，不把未知当作可用。

`read_only_structured`、`no_tool_structured` 和 `start_read_only_structured(context, request)` 的名称与现有请求字段保留；删除 `read_only_structured_verified` 的证书语义及其使用点。`capability_report` 与 harness health 不再给出 `reviewVerification` 或按版本的验证状态，改为 `readOnlyStructured` 下的本地资格结果，说明检查不调用模型、不能代表原生调用已经验证。`implemented` 与 `sameAttemptContinuation` 保留，付费证书相关的 `verified` 删除。更新版本可以更新发现结果，但不会因为缺少新版本证书而失去资格；每次运行仍检查原生协议和策略是否真的生效。

`router.profile_problem` 保留统一入口，按设置标记、是否配置、已发布、完整身份、启用/可用、缓存的 harness 健康、额度耗尽、所选模式资格依次检查。快速模式沿用已实现的无工具结构化通道；审阅模式使用本地检查。错误分别保留可判断的 `router-*` 原因码，面向 Host 的说明统一以“Router 不可用：”开头并给出具体原因。容量满继续使用既有排队规则，不伪装成 Router 不可用；未确认停止继续占有容量。

资格检查与原生验收是不同事实。DSH、ZCode 在实现与本地检查完成后可按 ADR 的新规则取得资格，验收记录和能力说明同时标明“原生未验证”；不以未经批准的模型调用换取资格，也不新建原生验证证书。

### 只读工具与运行证据

保留 `ReadOnlyStructuredRequest(cwd, prompt, output_schema, budget, capture_evidence)`，删除仅为退役付费沙盒探针服务的 `native_probe` 与运行分支。`cwd` 只指向 `router_input.prepare` 从不可变 `manifest.inputTree` 生成的私有冻结副本；独立 `selection-request` 没有仓库时使用空副本。当前 materialize/verify/digest、Git replace 防护、链接逃逸与停止后回收规则继续有效。

新增公共 `read_only_policy.py`，定义纯数据的工具白名单、标准化工具事件与 `ReadOnlyToolEvidence` 累加器。标准化事件携带原生 session/turn/call ID、开始/结束阶段、工具名与只读操作种类，不保存文件内容、任意工具参数、提示词或推理。工具开始即计一次调用，以 call ID 去重；缺失身份、重复但不一致、完成无开始、结束时仍有未完成调用、未知事件、流截断或关闭后还有工具事件都不能产生有效证据。检查发生在原生 session/turn 过滤之前，子会话、MCP 或旧回合的非法调用不能因过滤而被漏掉；合法调用还必须绑定本次回合。

工具集合固定为读取、列目录和搜索。Claude/ZCode 只允许精确名称 `Read`、`Glob`、`Grep`，不接受别名或按字符串前缀放行。DSH/Codex 使用精确名称 `read_file`、`list_directory`、`search_files` 的受限处理器。写文件、编辑、shell、任意 JS、子代理、联网搜索/抓取、MCP 泛入口、审批和交互工具一律拒绝；模型文本声称自己没用工具不构成证据。出现非法调用马上请求停止，即使原生最终给出合法 JSON 也作废；停止未确认时仍报告未知并保留目录。

新增 `read_only_files.py` 的 Python 处理器供 Codex 动态工具使用，DSH 的 Node 处理器实现同一参数格式：`read_file({path, offset?, limit?})` 按行读取 UTF-8 文本，offset 从 0 起，limit 默认 200、上限 2000；`list_directory({path?})` 默认列根目录一层；`search_files({pattern, path?})` 在指定目录递归进行字面量文本搜索。路径只能是副本内的相对路径，拒绝绝对路径、`..`、NUL、逃逸链接与特殊文件；链接只能解析到副本内。单次结果至多 64 KiB，目录/搜索至多 1000 个条目/命中，遍历至多 10000 个文件；达到输出上限返回明确 `truncated`，路径或参数错误返回有界的工具错误，不能转成 shell。文件响应作为不可信数据返回，不能成为工具配置或指令。各工具不提供状态目录、配置目录或真实检出。

预算保持现有数值：brief 为 60 秒/8 次工具，standard 为 300 秒/24 次，deep 为 600 秒/64 次；快速模式固定 60 秒。一次格式纠正与此前工具调用共用同一 attempt 的绝对 deadline 与累计工具预算，不重置计数；越界候选不纠正，Claude 保持现有一次调用。现有 `bytesRead` 未能可靠测量时仍为 null，131072/524288/2097152 的既有读字节值继续明确为记录的预算参数，不新声称已实施跨 harness 的字节硬上限；新处理器的单次响应界限另外验证。

公共接口为 `ReadOnlyToolEvidence.observe(event)`、`finish(native_identity, stream_complete)` 与 `validate_tool_verification(summary, expected_binding, budget)`。标准化 event 的字段固定为 `nativeIdentity`、`callId`、`toolName`、`phase: start | end`；各 harness 的原生身份形状保持真实（DSH callId、ZCode sessionId/turnId、Codex thread/turn、Claude sessionId），不为缺失的原生字段捏造值。适配器负责确认完整原生结束，再调用 finish；计数和非法状态只由观察器计算。

控制器生成 `toolVerification` 的固定字段为 `version: 1`、`binding: {adapter, taskId, attemptId, generation}`、`nativeIdentity`、`streamComplete`、`allowedTools`、`toolCalls`、`forbiddenToolCalls`、`unsettledToolCalls`。binding 由 Python 控制文件确定，DSH 插件不自己声称黑板身份；原生身份需与结果的 nativeIdentity 相等。只有完整结束、身份一致、非法/未完成为零且计数合法才可成功，原生工具调用可以为零，不强迫 Router 为了“证明会读”多调用一次工具。摘要由运行时从原生事件计算，不采纳模型提交的同名字段；`AdapterOutcome` 继续携带实际停止证据、usage、rawAnswer 与失败码。`DecisionAdapter.collect` 和 `DecisionCoordinator` 的发布入口都检查摘要、预算、冻结副本与停止证据，绕过某一个收集层也不能发布不合格答案。

四项防护分别是冻结副本唯一输入、确认停止后核对副本与原 manifest、原生事件白名单、既有预算。失败码使用 `router-input-changed`、`router-tools-forbidden`、`router-tool-evidence-unverified`、`router-budget-exhausted`，未确认停止沿用现有停止边界。只有确认停止且副本未改动才删除镜像；有改动、证据不足或停止未知时保留私有证据。合法结构化选择经程序检查后直接生效，不新建人工审阅环节。

### Codex 与 Claude 的落地

Codex 目前的审阅入口主要统计 `commandExecution` 与 raw call，不能据此声称工具仅只读。采用现有无工具通道对内建工具、技能、项目配置、应用、联网与子代理的关闭方式，仅在 `thread/start.dynamicTools` 中加入上面三个只读工具，由控制器通过 `item/tool/call` 处理；不开放通用 exec/code-mode。复用受限工具处理器，不另起黑板或 MCP 服务。继续启用私有 `buddy-router` 只读/禁网权限，核对 `config/read`、thread/start 的原生生效策略、cwd、model/provider 与工具回报后才发送模型输入；原生不支持动态工具或策略回报不符时，在模型输入之前失败并报告原因。

动态工具采用 upstream 的实验 App Server 接口，设计依据是 [ThreadStartParams](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/src/protocol/v2/thread.rs) 和 [动态工具响应实现](https://github.com/openai/codex/blob/main/codex-rs/app-server/src/dynamic_tools.rs)。这说明接口的形状，不证明当前安装版已可运行；实现时以本地公开协议元数据和模拟协议验证支持，不调用模型。字段不符时停止并由 Host 修改本计划，Worker 不自行改用 shell 或另一个通道。

Claude 保留受限 CLI、空 MCP、隔离设置和原生沙盒，只向审阅回合提供 `Read,Glob,Grep`，移除仅在普通只读 Worker 路径需要的 Bash；不改变普通 Worker 的工具集合。私有审阅 settings 将冻结根加入 `filesystem.denyWrite`，使用空 network allowedDomains、strictAllowlist、禁用 unsandboxed fallback 和空 excludedCommands。模型输入前，通过现有 SDK control 通道的 `get_sandbox_dialog` 只读操作检查原生回报：supported/enabled 为 true、dependencies.errors 为空、no_sandbox_allowed/unsandboxed_fallback 为 false、限制中包含冻结根的 denyWrite、网络 allowedDomains/unixSockets 为空；缺失或不符就失败。该字段形状来自当前公开安装代码的 `buildSandboxDialogResponse`，不新增并不存在的 initialize 字段，不读取账户资料。

Claude 的审阅结构化输出采用 schema 入提示与本地校验，不使用会额外提供答案工具的 `--json-schema` 路径；普通 Worker 的原生 schema 路径保留。解析绑定会话的原生最终 result/assistant JSON，沿用 64 KiB 答案上限，不能把 prose 当成完成或放行额外 `StructuredOutput` 工具。system/init 的实际工具清单与后续 tool_use 原生流核对三工具集合；若 init 只能在发送输入后到达，工具清单检查发生在那时，失败诚实记录 modelStarted=true，不能宣称是 pre-model 失败。收到审批、非白名单调用、子代理调用或不能确认的策略时作废；只有沙盒回报不合格的 preflight 能记 modelStarted=false。模型输出流与完整关闭通过公共摘要判定。

### 持久路由边界与入口删除

`router.resolve(connection, *, frozen=None)` 只解析一个设置，不再接受 requested mode 或 allow fallback；返回 `(profile | None, facts, problem | None)`，facts 固定含 `routerProfileId`、`routingMode`、`configurationRevision`、`budget`，problem 固定为 `{code, reason}`。frozen 是 admission 保存的设置、完整身份与 revision，只用于重核对，不重选。`DecisionCoordinator` 删除 `_record_fallback`、`_fallback_after_preflight` 与自动重排为 fast 的分支。多候选请求在 admission 时冻结 Router 的 profile ID、完整四元组、模式、预算与 configuration revision。请求重放返回同一决策与冻结值；claim 前重新核对该 Router 的健康、额度、本地资格、设置身份和现有表 revision/reader gate，变化时进入原委派的 Host 边界，不刷新候选、不换 Router、不换模式。取消、takeover 和迟到答案继续用 owner generation/attempt 进行 fencing。

显式完整 buddy 与唯一合法候选的 L1 直接路径继续绕过 Router；零候选沿用独立的无合法候选原因。多候选但 Router 未设置、不健康、额度耗尽、模式资格不足或设置待升级时，记录 `needs-host` 和具体原因，原 governed goal 为 `awaiting-host`，不创建可执行 Worker 业务回合；claim 时才发现失效、或原生 preflight 失败时，在确认 Router attempt 停止后打开同一边界，不能把失败排入第二个 Router attempt。Host 可指定完整合法 buddy，或在用户重新设置之后显式 `reroute:true`；这不授权 Host 修改共享设置。

新决策继续用 `routingMode` 记录所选模式，不再产生 `requestedRoutingMode`、`fallback` 或 `decision.fallback`。历史审计 JSON 和既有事件不重写，includeAudit 原样保留历史事实；正常现行 API/前端不从历史 fallback 字段推断新的运行方式。预算、越界、输入变动计数沿用现有独立分类，并为工具证据失败保留具体错误，不将失败或拒绝包装成正常 abstention。

删除 `harness-verify`/`harness_verify` 的 CLI、help、transport、C-Two contract、service、console 白名单入口，删除 review-check adapter、调度/容量/Worker 专用分支、证书资源及其读取/发布、仅服务于付费证书的探针与回放代码。先以引用清单证明用途再删；一般的取消、停止、账户隔离、冻结副本、工具流与预算验证保留或迁移到公共运行检查，不能因旧证书功能退役而一起丢失。原生检查留在显式的开发 probe 中，不成为生产资格入口，不重新引入别名。

控制台后端快照返回单一设置、本地资格、具体不可用原因和最少的数据流向说明：快速发送任务包，审阅还发送冻结代码；DSH/ZCode 没有系统级沙盒，不能保证阻止副本外读取或外传。前端只沿用现有控件将两个位置缩为一个、保留模式和预算绑定，删除审阅验证按钮/证书状态、修正解析及必要的旧测试；不加入维护、画像、探索等尚未实现的控件，不调整视觉设计、布局或时间轴样式。

## L5 的接口设计

DSH 增加独立的 `read-only-structured.mjs` 插件和只读工具处理器，使用现有 DSH `llm.prepareCall`/`prepared.stream` 的原生结构化调用路径，实现有界工具循环；不借用可写 Agent、普通 Worker runner、completion/inquiry 插件或全局工具注册表。与现有 no-tool 插件一样，私有 headless profile 禁用原 headless-runner、标题模型与额外动态工作流，只注册上述三个工具。工具定义、模型发出的 tool call block、真实执行结果与 finish 组成原生证据；不得从最终 prose 或自报计数推断成功。

每轮只允许 text/reasoning/usage、三个工具的完整 call block 与合法 finish；工具 arguments 必须在完整 block 结束后解析并校验，非法名立即拒绝，不先执行。批量工具调用按每一个 call 计数，先检查剩余额度再执行，输出只允许本次 call ID 的有界结果。每轮完成后再向原生 stream 提交工具结果，直到得到最终答案或绝对 deadline/累计工具上限；schema 格式纠正至多一次，复用全部预算。原生 usage 已知项累计、未知保持 null，DSH 的 observed 身份继续为 null，不伪造 provider attestation。

Python 的 DSH 只读控制器消费既有 `ReadOnlyStructuredRequest`，启动方式复用当前私有 profile/owned child 模式，但不修改日常 DSH 设置、不增加全局插件。Node 请求固定为 `{callId, prompt, spec, cwd, budget}`，spec 只含 provider/model/effort；插件结果固定为 `{status, code?, modelStarted, rawAnswer?, resolved?, observed: null, nativeIdentity, usage, toolVerification}`，Python 按原始控制文件和 attempt 绑定检查，不接受多余的模型可写字段。模型输入前用免费 `--dump-config` 核对普通 runner 已关闭且唯一只读插件启用；本地资格检查只核对已安装的公共实现/资源，不运行模型。控制文件没有黑板凭据，toolVerification 从插件的已核对 stream 和 call/result 产生；controller 与 native process 都停止后才产生 `shutdownConfirmed`。

DSH 假 stream、假可执行程序和 Node 文件夹具覆盖合法读/列/搜、恶意工具名、半截 block、伪造 call ID、原生错误、模型身份不符、格式纠正、预算 N/N+1、超时、取消与停止未知；再通过 DecisionAdapter 夹具验证改动副本必作废及失败不能引起降级。本地/模拟验证后记录“原生未验证”。

## L6 的接口设计

新增 `zcode_read_only.py`，由 `ZcodeAdapter.start_read_only_structured` 经已有 `read_only.start` 启动 `zcode_runner` 的独立 readOnlyRequest 分支。复用原生 app-server 和模型/强度核对函数，不调用普通 governed turn、MCP finish、协作 inquiry 或原生续做。每个审阅 attempt 新建根 session，参数固定为 `toolAllowlist: [Read, Glob, Grep]`、`mcpServers: []`、`offPeakToolEnabled: false`、`dynamicWorkflowEnabled: false`、关闭标题生成；mode 不使用普通 Worker 的 yolo。安装内容中的 session/create 协议与工具注册名有这些参数，但静态核对不声称完成原生验证。

本地资格检查核对受限配置生成器、session/create 的严格参数 schema、原生 registry 的 allowlist 过滤实现与精确工具集合；运行时核对 create 成功、根 session、冻结 cwd、model/effort 配置回报与订阅握手，再发送模型输入。ZCode 没有原生 OS 沙盒，ADR 不要求它回报并不存在的沙盒策略，也不发明 snapshot 中没有的 toolAllowlist 回显字段；受限请求被拒绝、已知协议形状变化或有实际工具清单但不匹配时，在模型输入前失败，事件中出现额外工具时立即作废。无 OS 沙盒能力不构成资格否定，也不能宣称已有 OS 级禁网或目录隔离。

`ReadOnlyEvidence` 在现有 no-tool 的原生 session/event 规则上增加允许工具的 start/update/end 关联，使用公共事件摘要；canonical session/event 的完成、inputId/turnId 匹配、订阅序号和 session/close 回报是成功所需的事实。telemetry/state.updated 只作诊断，不能证明最终答案或代替工具完成。陌生 session、子代理、MCP、缺 toolCallId 的工具事件、未知 frame、断流、late tool event 全部拒绝。格式纠正仍属同一黑板 attempt，可以在已确认关闭的根 session 后另建一个受限 session，工具与时限累计，至多两次答案回合；正常工具循环中的模型往返不当成格式纠正。

在假 app-server 协议中验证请求参数、策略回报、合法读工具流、坏身份与序号、外来/子调用、断流、预算、一次纠正、close 失败、取消与停止未知，兼跑原 no-tool 与普通 ZCode Worker 回归；经 DecisionAdapter 验证副本变化和不可用边界。本地/模拟验证后记录“原生未验证”。

## 委派划分与依赖

下表每行是一件可独立实现、验证与审查的成果，模块验收仍由 Host 负责。先落地纯接口，再接消费者，避免适配器各自决定策略；中间的纯接口任务可以暂不替换旧调用点，但 L4 验收前所有旧路径必须删除。任务描述必须附本文对应契约、准确读写范围、预期产物与检查，不让 Worker 决定字段、转换、降级或权限策略。

| 任务 | 修改与产物 | 不修改 | 验证 | 依赖与次序 |
| --- | --- | --- | --- | --- |
| L4-A 设置契约与换算 | 新 `router_settings.py`；不可变设置、补丁验证、纯转换与转换报告；新 `test_router_settings.py` | 不读写看板，不做迁移，不接工作流或前端 | 转换矩阵、quick、缺省/非法/null、两位置相同、无副作用/幂等；工具/模型调用设为会失败的 stub | 第一批；独立成果合入后供 L4-F 使用 |
| L4-B 本地资格接口 | `adapters/base.py`、registry/capability report 及各 adapter 的本地检查；工具限制资源检查；`test_read_only_eligibility.py` | 不改 settings、路由决策或账户，不运行原生模型；DSH/ZCode 此时保持未实现 | 声明与实现不一致、资源缺失、未知支持、版本变化、每种模式；证明零模型/零状态写入 | 与 A 可并行；依赖相同固定接口文本 |
| L4-C 只读工具与证据库 | 新 `read_only_policy.py`、`read_only_files.py`；纯事件检查、冻结根的三种工具、结构化 toolVerification 验证 | 不启动 harness，不改四个原生 runner、设置或工作流 | 允许/拒绝工具、call 关联、漏帧/截断/foreign/late、N/N+1、路径/链接/特殊文件、响应上限 | 与 A/B 可并行；先合入，D/E/L5/L6 只消费它 |
| L4-D Codex 审阅适配 | Codex read-only 配置/runner、动态工具 dispatcher、模拟 App Server 夹具及对应测试 | 不改普通 Worker、no-tool 行为、Claude/DSH/ZCode 或公共策略定义 | 原生 config/policy/cwd 回报、仅三工具、stream/identity、预算累计、失败前 modelStarted=false、关闭/取消；no-tool 回归 | B、C 合入后；可与 E 并行写独立 worktree |
| L4-E Claude 审阅适配 | Claude read-only argv/settings、get_sandbox_dialog 回报检查、纯 JSON 答案通道、事件归一化及夹具/测试 | 不改普通 Worker 的 Bash/schema 等能力、Codex 或公共策略定义 | Read/Glob/Grep、Bash/MCP/StructuredOutput/子调用拒绝、真实形状的策略缺失/不符、原生流结束、预算、取消；普通 Claude 只读/可写回归 | B、C 合入后；与 D 独立 |
| L4-F 设置的存取与发布 | 接入 A/B；`router.py` 的 configuration/初始化、`evaluation.py`、`user_policy.py` 的字段补丁、修订、快照及相应 fixtures | 不改 DecisionCoordinator、运行收集/发布、迁移、前端或 paid API | 合并后的模式资格、字段省略/null、原子拒绝、权限/revision、旧板读取零写入与 upgrade-required、不阻塞无关设置 | A/B 合入后顺序执行；可在 D/E 完成前做，代码范围独立 |
| L4-G 持久路由与答案发布 | 接入 B/C/D/E/F；`router.py` 的 profile_problem/resolve、`decision.py`、`adapters/decision.py` 的 claim/收集/发布；需要时仅改 `workflow.py` 的边界呈现 | 不改设置 writer 或 conversion，不做入口批量删除、界面设计或 L7 数据 | 缺失/不健康/额度/资格边界、claim/preflight 失效无第二次 attempt、四防护、回放、唯一/零候选、显式 buddy、容量、gate、取消/takeover | D/E/F 合入后；同一请求从选择到发布的契约已固定 |
| L4-H 删除付费审阅验证 | CLI/help/transport/contracts/service/console/worker/store/scheduling/health 的 review-check 入口及专用资源；迁移其通用防护测试 | 不动普通执行/账户/停止逻辑，不重写历史证书或事件，不修改 skill | 入口拒绝、注册/包内无证书或 review-check、健康与预算/停止无退化；旧测试逐项登记去向 | G 后；机械删除按引用清单实施 |
| L4-I 控制台最小适配 | 现有类型/API parser、单 Router 表单接线、删除 HarnessReview/证书控件与失效状态，必要的 preview/test fixture | 不做 U1/U4 视觉设计，不加维护/画像/探索入口，不改后台策略 | 实际 parser/preview 合约、设置请求、失效入口消失、既有受影响 frontend tests、typecheck/build | H 后；代码只在 apps/console，Host 合入并检查资源生成 |
| L5-A DSH 只读原生插件 | 新 Node 只读插件与工具处理器、假 stream 与 Node tests | 不改 Python 路由、controller、普通 runner/no-tool/账户 | 三工具与真实文件、block/call/result 关联、非法工具、错误/断流、工具次数/期限、无工具回归 | L4 模块验收后；固定插件控制/回执格式由本计划确定 |
| L5-B DSH controller 与接线 | DSH adapter/start、独立 Python read-only controller、私有 profile preflight、夹具、包装集成 | 不改 A 的工具/事件策略，不运行真实模型、不修改日常 settings | dump-config 失败、身份、格式纠正、owned stop、timeout/cancel、DecisionAdapter 四防护；原 DSH/no-tool 回归 | L5-A 合入后；Host 最后写 L5 记录并跑完整检查 |
| L6-A ZCode 受限协议 | 新 `zcode_read_only.py` 的 session 参数、ReadOnlyEvidence 与结构化回合；mock app-server 和纯协议测试 | 不改普通 runner 调度、Router 设置、ZCode MCP/Worker | 严格参数/原生配置回报、允许工具和非法事件矩阵、序号/身份/完整流、纠正与累计预算、close/cancel，不虚构工具清单回显 | L4 模块验收后；可与 L5-A 并行写独立 worktree |
| L6-B ZCode controller 与接线 | ZCode adapter、runner 独立 readOnlyRequest 分支、资格启用与集成测试 | 不改 A 的事件规则、普通 Worker/no-tool/账户，不运行真实模型 | controller 生命周期、模型/强度核对、shutdown、四防护、不可用边界；no-tool 与普通 ZCode 回归 | L6-A 合入后；Host 最后写 L6 记录并跑完整检查 |

设置存取与持久路由分为 F、G 两个任务，前者只处理用户设置，后者只处理一个路由请求的执行与发布；两者都不能扩展为重新设计 Router。若实际引用面超出上表，Host 先写明新边界并修改计划再提交后续委派，不能在问询中把七八件新工作追加给原任务。L4-H 的批量删除虽然跨层，只承担一个退役结果；Host 会先生成引用与测试清单，避免删除尚有一般用途的验证代码。

Host 自己承担计划与模块记录、参考文档、`accept`/`conclude` 帮助的小修、最后集成审查与检查，不把它们追加到正在运行的委派。小修只改 help 提取器对公共必填字符串校验的识别或已有 override；不改 runtime 的 note 校验、验收或回收实现，增加能直接断言两个 help 将 note 标为 required 的既有 CLI help 用例即可。

## 检查安排与测试去向

准备工作已在独立 worktree 使用 Node 24.21.0 完成 `npm --prefix apps/console ci`；它满足 `apps/console/package.json` 的 Node 要求。计划阶段不运行原生检查或完整业务回归。计划提交前检查文档差异、单段单行、链接和本机路径；不将第一阶段的 1894 个 Python/106 个 Node 测试称为第二阶段结果。

委派只跑其表内的独立检查和受影响回归，所有 Python 检查经 `uv run --frozen` 使用本 checkout 与 test harness，复用 `buddy.checks.test_environment` 清除继承的 runtime/Worker/账户/代理环境；不手工给测试指向日常目录。重点回归文件包括 `test_routing_modes.py`、`test_router.py`、`test_decision.py`、`test_workflow_routing.py`、`test_user_policy.py`、`test_selection_policy.py`、`test_cli.py`、`test_no_tool_{dsh,zcode,codex}.py` 及各 harness 的现有 controller/protocol/私有目录测试；准确新增测试文件由任务产物固定，测试运行命令与结果保存在各任务日志中。

被改写的 `test_routing_modes` 旧用例需一一对照：两个位置的补丁保留改为单一设置中省略字段保留；旧按证书分槽改为纯转换保留原默认位置；审阅不可用时 fallback 改为同一 Host 边界且没有 Router/Worker 启动；claim 改派家族改为失效时不产生其他家族的 attempt；preflight 重排改为确认停止后边界且无第二个 attempt；回放冻结、零工具证据、预算和 schema 不变继续保留。`test_harness_review`/review probe 中仅验证证书授权的情形随功能退役，以入口拒绝和无自动 paid work 替代；原生流完整性、政策回报、工具越界、身份、预算、停止、目录/日志留存与敏感信息剔除迁移到新运行校验。每个被删除或改名的测试在 L4 验收中给出原 ID、新 ID 或退役依据，不能只列数量。

Host 验收每个委派时，读取固定 artifact 的 diff 与风险 hunk，确认改动范围，亲自跑相应测试；不能只读 Worker 的成功总结。迁移了防护的测试，选取代表性故障使其确实失败（例如去掉工具白名单、取消输入 digest 检查、放回 fallback），在隔离的测试副本/补丁中验证，再恢复固定产物；不写只复制实现分支的测试。所有旧情形仍须有覆盖或明确的功能退役理由。

L4 的 I 与 Host 文档/帮助改动全部合入之后，冻结当前 worktree，运行 `uv run --frozen python -m buddy.checks`；L5 和 L6 各自合入并写完记录后各再运行一次完整检查。完整检查期间该 worktree 不做编辑、merge、提交、生成资源或委派写入；后台 delegate 只能写自己的 worktree。失败则先等检查退出，修阻塞项后重跑对应检查，最后得到一次完整通过。frontend 修改在 L4 额外运行受影响 Vitest、typecheck/build；必要的 build 资源与最后文档稳定之后才跑完整检查。

外层检查命令清除 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`、`BUDDY_RUNTIME`、`BUDDY_RUNTIME_IDENTITY`、`BUDDY_WORKER_STATE`、`BUDDY_WORKER_ID`、`BUDDY_AGENT_CREDENTIAL`、`BUDDY_AGENT_CREDENTIAL_FILE`、`BUDDY_ACCOUNT_SELECTION`、`BUDDY_SUPERVISOR_START_ID`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`；子测试环境再使用 checks 的清理逻辑与未安装的原生 CLI sentinel，剔除继承的第三方 Claude 网关变量。测试框架创建私有 state/runtime/cwd，不依赖 `BUDDY_DEV_SOURCE=1` 覆盖已有 runtime pin。确定性的测试/打包长任务使用 command 方式，读取退出码、总数和失败摘要，原始日志放忽略的 `tmp/adr021-stage2/`。

## 原生检查与授权边界

L5、L6 的正常任务都只做到模拟夹具、公开安装代码/协议元数据的静态核对与不调用模型的检查。Host 准备显式的开发 probe，默认 prepare-only，参数 `execute` 才可调用模型，普通检查套件不能启用它；它直接运行本 worktree 的 adapter/controller，不经另一套 Buddy 服务，不写日常看板或用户配置。准备不接触凭据，只预留经用户允许的现有账户绑定消费路径。

每次付费原生检查前，Host 用中文列明 harness、准备验证的具体命题、冻结测试文件/输出、时长/工具上限、最多一次格式纠正和可能使用的 quota，然后停下逐次请求用户批准。一份批准只允许所列的一次 probe；重试、扩大范围、从 DSH 换到 ZCode、换 buddy 或再次调用模型都要另行批准。未批准不能以 delegate、health、资格刷新或测试名义绕过；记录写“原生未验证”。如用户不批准，完成其余代码与模拟检查后交付该边界，不把原生支持描述为已实测。

真实 probe 的验收需要一条完整原生工具调用与完成证据、结构化选择、预算与停止回报、副本未改动和真实工具结果；只能看到最终 JSON、RPC 成功、模型的“只读”声称或没有调用工具的超时都不够。若策略/协议不符，先记录事实并修正计划或实现，再请求下一次授权，不在同一次许可下自动重试。

## 委派、集成与记录

所有 buddy 提交省略 harness/provider/model/effort，不给部分四元组；任务需要的复杂度、推理和验证能力写入任务描述。一个议程用一个 objective，每行任务单独委派，固定 requestId，保存 runId/objectiveId/controlFile。并行写入使用 `executionWorkspace.kind=worktree` 的独立检出；Host 的分支只由 Host 合入已验收成果，不改 main、socu/buddy-core 或其他会话的 worktree，不推送。

每个运行的委派只有一个原生监控子智能体，依已安装 Codex Host 指南用 `gpt-6-luna`/`low`、`fork_turns=none` 明确启动，只执行同一 run 的 await；慢或 parent wait 超时继续等待原子智能体，需要进度时 Host 自己读 get。只在监控本身失败或提前结束时替换；无法建立监控才用前台 await。监控不读源码、不做实现、不使用控制文件、不改任务。Native 子智能体仅用于这类工具与上下文明确的监控，不代替 buddy 的代码委派。

成果验收使用已安装 0.27.0 的 `integration-record`、`acknowledge` 与 `workspace-cleanup-plan/apply`，不能用正在实现的源码 accept/conclude/reclaim 操作作为委派流程。Host 本地合入固定提交并跑相关检查后才登记确切 artifact/integration ID。成功立即回收受管检出；失败或取消先按已安装操作写 recorded 结论，再回收；回收受阻保留具体原因，不手工越过停止/成果证明。子任务分支合入并回收后删除，未合入的成果先确认结论和保留证据再删除。

Host 分别写 `docs/acceptance/l4-adr021.md`、`docs/acceptance/l5-adr021.md`、`docs/acceptance/l6-adr021.md`，每段一行，保持简短，列实际检查、未检查内容、计划偏离和提交；L4 附迁移测试去向表。更新拥有契约的 `docs/reference/decision.md`、`architecture.md`、`evaluation.md`、`harnesses.md`、必要的 `cli.md`、`workers.md`、`runner.md`，按模块只写已实现部分。路径用 `~` 或占位符，不将本机主目录或项目路径写进 tracked 文档、fixture、代码样本。

每个委派记录提交时间、进入终态/Host 边界时间与实际执行回合起止时间：报告的执行时长采用黑板/原生回执的 execution elapsed，另列从 submit 到交付的墙钟时长，排队/验收/监控不混进模型执行。记录失败、返工或取消的回合，不只统计成功委派。原始 get/await/测试日志留在 `tmp/`，不能把 controlFile 内容或凭据放进记录。

执行中发现接口或实现假设不成立，由 Host 先修改本计划、说明改变了哪个选择与任务边界，并单独提交，再发新的有界任务；不能让 Worker 默默扩大工作。最终汇报分支与模块提交列表、计划与三份记录、最后一次完整检查的实际总数、每个委派时长、计划变更和 Host 自行定下的选择，以及原生未验证/待批准内容。

## Host 在 ADR 留白处作出的选择

单一字段采用新的 `routerProfileId`，其余现有字段保留；纯换算只取旧默认模式对应的位置，缺失时保留未设置；旧设置只在 L15 显式升级转换。选择理由是保留用户原意、取消备用位置并避免读操作成为迁移。

资格采用无模型的本地接口/工具限制检查；运行证据采用严格的事件身份、完整流与调用关联，工具限制不足或无法确认时失败。Codex 只开放专门的动态文件工具，Claude/ZCode 只开放精确原生读工具，DSH 用原生 LLM 流上的有界工具循环。选择理由是工具集合本身只读，不能借只读 sandbox 或最终答案替代调用证据。

设置与路由输入冻结到同一请求，claim/preflight 失效停在 Host 边界；容量满继续排队；单候选和显式 buddy 的既有直接路径保留。预算沿用已实施的时间/调用数上限与未知字节计数，没有在本阶段发明另一个预算契约。原生检查与本地资格分开，DSH/ZCode 的未授权原生调用明确留空。

## 当前进度

已完成隔离分支、规定文档与源码边界阅读、Node 24.21.0 的 console 依赖准备和本文设计。尚未委派、实现、运行完整检查或调用真实模型；提交本文后停下等待用户回复。
