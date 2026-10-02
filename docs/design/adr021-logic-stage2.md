# ADR-021 逻辑线第二阶段执行计划

本文是 [ADR-021](../decisions/021-router-buddy-planes-and-routing-evidence.md) 第 2、3、4、20 条在 L4、L5、L6 中的实现设计，基线为 `socu/buddy-core` 的 `cf371da`，工作分支为独立 worktree 中的 `socu/adr021-logic-stage2`。已读 `AGENTS.md`、`CONTEXT.md`、[第一阶段验收](../acceptance/adr021-logic-stage1.md)、[ADR-007](../decisions/007-neutral-core-and-single-current-contract.md) 与 [当前架构](../reference/architecture.md)。用户已批准按 2026-10-02 修订的第 4 条执行，本分支已合入 `socu/buddy-core` 的 `29883a8`（合并提交 `e63cdc5`）；修订计划提交后直接委派与实现；L4 集成并完成模块验收后，才开始 L5、L6；本阶段结束于 L6，不开始 L7 或后续模块。2026-10-02 再合入 `socu/buddy-core` 的 `5c87585`；该提交将第 2 条改为 Router 有序列表并增加第 20 条。本修订取代下文单个 Router 的旧目标设计；已发出的 L5-B/L6-A 保持原范围，新增工作以独立任务完成。

## 范围与交付边界

实现一个含有序 Router 列表和重试间隔的设置、保留同模式的顺序切换、取消模式降级、改用不调用模型的审阅资格检查、实施审阅运行的四项防护、删除付费审阅验证的代码与入口，随后分别实现 DSH、ZCode 的只读工具回合。Host 自行修正 `accept`、`conclude` 的帮助，把 `note` 标为必填。

Python 黑板仍是权威状态的唯一写入者；Worker 运行时仍拥有原生子进程、取消与停止回执。Router 没有黑板凭据、受管业务回合、完成工具或成果权限，结构化答案只经过程序的格式与路由边界检查，不增加 Host 验收。第 3 条的维护面与画像面只作为权限边界写进契约，本次不实现它们的触发、数据包、存储或工具。

本阶段不改 ADR 决定，不做界面设计，不改通用 `configuration`、`profile` 等接口与存储字段的用语，不改 schema 版本，不做 L15 的升级或存量转换，不改 `SKILL.md`、Host 指南、README。新增 Router 列表与重试间隔并删除两个位置是 L4 的行为变更；其余名字保留给 L14。只修改各模块拥有的参考文档，对 `apps/console` 只做类型、解析、删除失效控件和必要的表单接线。

不安装、不升级日常运行时，不修改用户配置或日常数据，不登录、退出、更换密钥或读取凭据文件。委派使用已安装并运行的 `~/.agents/skills/buddy/scripts/buddy` 0.27.0；其固定运行时独立于本 worktree，不另起用于委派的私有或隐藏服务。测试与原生检查的隔离目录不构成另一套委派服务。

## L4 的接口设计

### 有序列表、设置与旧设置换算

设置定为 `configuration.routerProfileIds: string[]`、`routerRetryIntervalSeconds: integer`、`defaultRoutingMode: fast | review`、`routingBudget: brief | standard | deep`，保留现有 revision 和 routingBudgetLimits。默认值为空列表、600 秒、fast、standard。列表不设项数上限，保留用户顺序；重复 ID 拒绝，空列表明确清空，null 拒绝。重试间隔为 1 到 2147483647 秒的整数，布尔值拒绝；上限保证时间运算和存储可表示，不增加产品上的天数档。列表中的 ID 必须引用已发布且身份完整的 buddy；已禁用、暂时不健康、额度耗尽或不满足当前模式的项可以留在列表中，由解析入口解释不可用原因，不能因第一项临时故障阻止用户保存整个列表。

RouterSettings 的字段固定为 router_profile_ids:tuple[str,...]、router_retry_interval_seconds:int、default_routing_mode:str、routing_budget:str，as_dict 输出上述公开字段。RouterConversion 的 source_slots:tuple[str|None,str|None] 固定按 fast/review 排列，settings 是 RouterSettings；不再返回 discarded_profile_id。二者是不可变值对象，validate_router_settings_patch 保持字段补丁语义，省略保留、未知字段拒绝；原 routerProfileId、fastRouterProfileId、reviewRouterProfileId 均不是现行设置别名。设置仍由认证控制台的人类 writer grant 发布，Host 与 Worker 不写共享设置。meta 使用 router_profile_ids（JSON 数组）、router_retry_interval_seconds、router_default_mode、router_budget_preset 和 router_configuration_version=3；现有业务表字段不改名。本阶段不改 schema_version、不新增表；新增运行事实使用现有 meta、tasks、attempts 与 events，必要的事件查询索引随源码交付，实际日常升级仍归 L15。

convert_legacy_router_settings(legacy) -> RouterConversion 仍是可调用的纯函数，不读取数据库、健康、账户或时钟，不调用模型、不写数据。转换后保留两个旧位置：默认 fast 时 fast 在前、review 在后，默认 review 时 review 在前、fast 在后；去除 null/缺失位置，同 ID 只保留第一次。另一位置不再被舍弃；即使 ID 不可用也原样保留。转换报告保留原 source_slots 的两个位置供 L15 审计。模式缺省 fast，预算缺省 standard，quick 换 brief，重试间隔缺省 600 秒；非法类型、模式、预算或 ID 拒绝。函数无生产调用点，版本 2 和更早设置读取为 router-settings-upgrade-required；不在启动、读取、发布或路由中日常转换。控制台继续返回 configuration:null/configurationError，并允许无关读写。

### 当前 Router 与已有记录

唯一解析入口为 router.current_router(connection, *, frozen=None, after_index=-1, now=None) -> RouterResolution。RouterResolution 是不可变 dataclass，字段为 profile:dict|None、profile_id:str|None、router_index:int|None、facts:dict、inspections:tuple[dict,...] 和 problem:dict|None；ID、index 的公开投影分别是 profileId/routerIndex。它返回的 profile 是当前发布行的复制值，不暴露连接，不修改状态、不启动原生进程、不按 Worker 偏好挑选 Router。facts 包括有序 ID/完整身份快照、模式、预算、重试间隔与 configuration revision；inspections 按顺序给出此前各项的资格、跳过原因和 retryAt。无 frozen 时用于当前角色的只读投影；有 frozen 时只在同一请求的快照中继续，after_index 严格前进，不回绕、不重新冻结候选，不跟随后来用户对列表、模式或预算的改动。

后续维护面与画像面直接使用这个入口，不各自找 Router、不缓存自己的当前人选。角色资格继续按全局所选模式核对；本阶段只提供共用的解析与结果记录接口，不实现维护或画像的模型执行。router_history.record_outcome(connection, *, profile_id, plane, request_id, task_id, attempt_id, outcome, code, phase, facts, now) 只追加幂等的 router.no_answer/router.answered 事实事件；plane 可为 work/maintenance/portrait，后两项仅供未来调用与纯夹具检验。读取角色不追加事件。事件幂等键由 plane/requestId/routerIndex/phase/taskId/attemptId/outcome 组成，使用已有 meta 的事实 receipt 防重；与事件在同一事务提交。新增事件索引按 routerProfileId/seq 查询。若老记录没有新事件，依据其自身冻结 input、不可变终态事件与对应 receipt 只读推导；有效回答使用当时黑板接受的事实，不以今天的新证据规则反审历史。不明归属/性质单独报 unknown，不猜给第一项；新旧投影按 attemptId 防止双计。

跳过期由该 buddy 最后一次 router.no_answer 的时间加当前设置的重试间隔推出，后来有效 router.answered 消除它，包括有效的无法选择回答。先检查跳过期，处于期内的观察只记入该请求的尝试概览，不追加 no_answer、不滚动延长时间；过期后重新核对资格和再试。调用前仍不健康、耗尽或不合资格，记录一次 preflight no_answer 并取下一项。不同 buddy 的记录不混在一起，维护/画像未来的 no_answer 同样影响三个面的当前 Router。取消、输入/候选/设置变化、fencing 和停止未知不伪造 Router 的成功。停止未知可以记录真实的无答案故障，但不能推进该请求。

“间隔后再试一次”采用已有活动 attempt 约束：一个曾无答案且尚未有有效回答的 Router 到期后，最多有一个再试执行；其他指向它的请求排队为 router-retry-in-progress，不因容量切换 Router。此约束由 router.claimed 事件与活动/停止未确认的 attempts 推出，没有 half-open 标志或新的熔断状态。再试失败后，等待请求在下次认领复核中按新跳过期向后走；成功后恢复正常并发。正常可用 Router 的容量满仍只排队。

### 免费资格和审阅防护

local_read_only_check() -> ReadOnlyEligibility 保留 eligible/reasonCode/reason/systemSandbox/sameAttemptContinuation。Codex/Claude 沿用已有原生沙盒、只读/禁网/冻结副本权限、生效策略回报和工具流检查；不重做运行、动态工具或 read_only_files.py。DSH/ZCode 必须具有原生读取/搜索工具的限制入口。只检查已打包机制和公开本地安装代码，不启动模型或会话、不探测账户、不读取凭据、不联网。只有布尔声明或继承基类空实现不算资格。付费证书、harness-verify、review-check 和控制台验证入口已退役，物理存量清理仍归 L15。

统一 toolEvidence 的 ACP 分类、实际根身份、task/attempt/generation 绑定、最多 128 个 start/end 事件、去重与完整性规则保持 L5-0A/0B 的接口。适配器只投影事实，黑板是唯一判定处：fast 完整且零工具；review 有系统沙盒允许 read/search/execute，无系统沙盒仅 read/search；不允许或不完整均作废答案。systemSandbox 从 claim 的 attempt-tool-policy meta 取，不能由答案自报。原生 unknown/foreign/child/MCP/late/缺 ID 事件不得过滤或补造。Codex 仅按本文末尾明确的 typed/raw 执行等价投影规则去重。程序仍核对原生策略、deadline、预算和 owned stop。

四项防护仍是冻结副本唯一输入、确认停止后核对副本及原 manifest、统一工具证据判定、预算。ReadOnlyStructuredRequest 的 cwd 由 router_input.prepare 从不可变 inputTree 生成；独立 selection-request 使用空副本，不借用 live checkout。每个 Router 执行使用同一预算档：brief 60 秒/8 工具，standard 300 秒/24 工具，deep 600 秒/64 工具；fast 固定 60 秒。该 Router 的一次格式纠正共用其 deadline 和累计工具预算；切换后的 Router 获得同档的独立预算，避免首项超时耗尽所有后续项的机会。记录每项用量及请求总和，不把总和说成单项预算。bytesRead 无可靠观测时仍为 null，不新增字节硬上限声明。

### 请求冻结、执行顺序与停止

一个 Host 请求、requestId、decisionId、governed goal 和候选包保持不变，每个实际 Router 使用独立的内部 decision task/attempt；不把终结的通用任务重新置为 queued。meta 的 router-request:<decisionId> 保存不可变的列表/完整身份/模式/预算/间隔/revision、候选/程序事实/账户绑定及 baseInput；router-dispatch:<taskId> 保存该项 index、完整 buddy、原生输入文档与 hash；attempt-router:<attemptId> 将 claim 绑定到该 dispatch。它们是请求与运行事实，不是熔断状态。旧 dispatch 的文档与 hash 永不重写，decision_requests 只指向当前内部 task/input，requested_json 与 input_fingerprint 仍保留原请求。router_sequence 的固定内部接口为 freeze_request(connection, decision_id, *, snapshot, now)、request_snapshot(connection, decision_id)、dispatch(connection, task_id)、reserve_dispatch(connection, *, decision_id, task_id, router_index, profile, document, now)、record_claim(connection, *, task_id, attempt_id, generation, now)。保存键已存在且内容不同即 CONFLICT，相同为幂等；reserve_dispatch 不启动进程。每次切换追加 router 运行事实，旧 tasks/attempts/receipts 保留。末次已完成者及全部尝试在当前 view 中显式投影。

admission 先冻结完整公共包，随后通过唯一入口选择首个 Router；全部不可用或空列表也要留给 Host 同一候选/程序事实包。单一合法候选与显式完整 buddy 继续走 L1 的直接路径，没有 Router 调用；零候选保留独立的原因。pre-claim 准备步骤在 selector_family/capacity 计算之前复核当前 dispatch；若它已不可用，在同一事务证明没有已启动 attempt、关闭旧的 queued task、记录原因、建立下一项内部 task。store 在 selector_family 前调用 prepare_router_claim(connection, task, *, now)->reason|None；它可关闭确证未运行的旧 task 并排入新 task，此次 claim 跳过旧 task，下次调度取新 task，不造虚假的旧 attempt。容量忙不在此步换人。

统一的 _queue_router_dispatch(connection, row, *, resolution, now) 在请求初建、pre-claim 和完成后推进时复用；它返回新 task_id，初建在请求行已插入后调用；只使用冻结 baseInput，建立独立内部 task 和 immutable dispatch，并更新 decision_requests 的当前 task 指针，workflow_routes 保留同一个 decisionId 关联，保持 governed goal 等待路由，不打开业务 Worker turn。claim 冻结本次实际模型家族、账户、tool policy 与原生文档，所有公开结果按 task/attempt/generation 和 owner generation fencing。相同请求/receipt/event 重放不建重复任务、不重复计无答案；旧 attempt 的迟到回执不替换当前输入、结果或健康记录。

有已运行 attempt 时，仅确认停止后才能建立下一项；Worker 的 shutdownConfirmed 必须为 true，已报告的 native stop 也不得为 false/未知，正常原生运行的 native/controller stop 均须成立。模型前未启动的本地拒绝可使用 Worker 的真实 never-started 停止事实。未确认停止保留容量、私有目录与具体边界，不由 missing PID/租约到期推定停止，不修改不可变旧回执或新增停止证明操作。restart/owner takeover/reader fencing 沿用原规矩，先进入明确的变更/停止边界；显式 reroute 仍受原有停止前置条件约束。

### 切换政策与边界性质

纯函数 router_failover.classify_outcome(*, stage, code, answer_valid, abstained, cancelled, circumstances_changed, shutdown_confirmed) 的 stage 为 preflight/runtime/publication，布尔参数拒绝非 bool，code 为字符串或 null；返回 answered/abstained/no-answer/changed/cancelled/stop-unconfirmed。answer_valid 只能由黑板完成结构、工具、预算和当前硬边界检查后给出；circumstances_changed 来自明确的上下文检查，不仅靠字符串错误码猜测。用户取消优先禁止切换；变更边界禁止切换；有效回答与有效无法选择禁止切换；其余无可用答案只有在停止证据成立时才能推进。

必须切换的情形是模型前 harness 不健康、额度耗尽或模式资格不足，以及调用后的超时、provider/native 错误、空/无输出、坏 JSON/结构、工具违规或证据不完整、超工具/时间预算、回答引用非法证据或选择冻结候选之外的 ID。没有下一个合格项就停 Host 边界。不能切换的情形是有效选择、有效无法选择、原输入/副本改动、原冻结候选中选中项后来失去合法性、设置/模式/预算/revision 或账户/reader/owner fencing 变化、取消和停止未知。冻结外 ID 是 Router 没有可用答案；冻结内 ID 在发布前变得不合法是情况变化，尽管旧代码共用 router-out-of-bounds，新的调用上下文必须区分。

有效无法选择也记录 router.answered，由该 Router 完成这次路由，边界附其理由；它不是服务故障，不继续其他项。input-changed 和 cancelled 不追加 no_answer；工具违规、坏结构等只说明这次回答不可用，不审查模型判断。所有尝试一次走到末尾，不在同一请求中等待重试间隔、绕回第一项或自动扩大候选。reroute 是 Host 在同一 governed goal 上明确发起的新路由请求，恢复遵从原 owner/control/revision 与停止门槛。

### 第 20 条的 Host 信息与最小控制台适配

routingBoundary 的固定字段是 kind、code、reason、routerTrials、candidates、facts、retryAt、commands、userAction。kind 区分 router-unavailable/routing-changed/router-abstained；无合法候选沿用单独 no-legal-candidates。routerTrials 给每项 profileId/完整四元组/index/phase/outcome/code/事实原因/taskId/attemptId/确认停止/连续无答案次数/下次再试时间，跳过和实际执行区分。candidates 与 facts 是冻结的完整合法候选和 Router 本来会得到的程序事实，不查询后来已改的目录来冒充冻结包，不发出虚构价钱、额度、画像或 L7/L9 字段。

commands 给同一 governed run 的 continue 与 reroute CLI 参数模板：continue 按仍可合法采用的冻结候选给出完整 configuration 四元组，reroute 的 reroute:true 带 notBefore=retryAt；持有者填自己已保留的 controlFile，服务不向公开 payload 泄露它的内容。需要该拥有者的 revision/command 参数由现有契约填入。全部无答案的 retryAt 是可再次评估列表的最早明确时间；没有可知的恢复时间则 null 并说明原因；停止未知或现有硬约束会阻止操作时给 blocked/reason，不给可绕过的命令。独立 selection-request 没有 governed goal，不伪造 continue 命令，说明其无业务委派上下文。前两类边界明确“取消后重新提交没有用”；只有改共享 Router 设置才需要用户，userAction 表达 settingsChangeOnly，Host 可以据事实自行继续，不要求等待用户。

health.routingHealth 保留诊断总览，并增加 routers 的逐项统计和 currentRouterProfileId。每项显示资格/跳过/重试/活动再试、最后回答/无答案、连续无答案、窗口失败/预算/越界/输入改动计数及最后错误。统计用该 Router 的不可变事件和对应 attempt receipt，四次超时仍是四次失败；一个 Router 的成功不能清另一个的失败。available 根据当前解析入口与跳过期，不再依赖旧的三次故障诊断阈值。连续无答案以最近有效回答之后的真实记录计数，不受显示窗口 20 条截断。

控制台仅改类型/parser/现有表单数据绑定和必要夹具/资源构建：现有选择控件操作列表第一项并保留其余顺序，清空选择明确清空列表，换首项时移除尾部重复项；加一个沿用既有样式的重试秒数输入，显示当前列表的简短文本。完整列表增删排序和每项的视觉状态归 U1。本阶段不设计布局、样式、维护/画像/探索控件；backend 返回逐项事实与边界数据，frontend 的其余适配以完整检查通过为界。

## L5 的接口设计

DSH 的 read-only-structured.mjs 是小的原生桥接，不实现工具或 LLM 工具循环。使用已安装的公开 AgentRegistry.create 接口，在 setup(agentCtx, agent) 中调用原生 agentCtx.tools.restrict({allow:[read,glob,grep]}) 与 agentCtx.tools.presentAs(native)；原生 ToolRuntime 的限制同时作用于继承工具的可见性和执行。通过原生 ToolRuntime.guard 追加不可被后续 allow 覆盖的三工具执行限制，并在每次原生 pre-step 前核对 schemas 的名称只有这三项，阻止 scoped registrations 或 PTC run_code 增大工具面；不自行重新实现任何 handler。

根 Agent 的 meta.cwd 是冻结副本；agentOptions 使用本 attempt 的 provider/model/effort。私有 profile 禁用 headless-runner、标题模型和额外动态工作流，由桥接创建且只驱动一个根 Agent；不提供完成/inquiry/黑板凭据工具，不复用普通 Worker runner。原生 session 的 tool/call、tool/result、模型 step/turn 结束与 flush/stop 作为事实，分类由 runtime 上报，是否允许只由黑板判定。非法工具请求由原生 restriction/guard 拒绝，其类别仍如实记录，不能从日志消失。

Python controller 消费 ReadOnlyStructuredRequest，模型前用免费 dump-config 核对私有 profile 与桥接，记录真实 request/root session/turn 绑定；readOnlyResource 检查确认公共 Agent/ToolRuntime API 和注册名称可用，不读凭据、不跑模型。控制数据只含 task/attempt/generation、prompt/schema/budget、cwd 和所选 spec；Node 回执只含 status/code、modelStarted、rawAnswer、resolved/observed、nativeIdentity、usage、toolEvidence，binding 由 Python 补入并核对。原生 call 数与绝对期限跨格式纠正累计，至多一次纠正，越界选择不纠正；controller 与 native group 都停止才能确认 shutdown。

模拟原生 Agent/ToolRuntime 夹具验证 setup 在发布/首个模型步骤前安装限制、原生工具被复用、run_code/scoped 工具不能扩大视图、read/glob/grep 正常调用、非法工具事实保留、call/result/turn/flush 完整性、N/N+1、取消/超时/停止未知；普通 DSH/no-tool 回归继续覆盖，单独的开发 probe 默认为 prepare-only。原生验证仍只获批一次后执行，否则记录“原生未验证”。

## L6 的接口设计

新增 `zcode_read_only.py`，由 `ZcodeAdapter.start_read_only_structured` 经已有 `read_only.start` 启动 `zcode_runner` 的独立 readOnlyRequest 分支。复用原生 app-server 和模型/强度核对函数，不调用普通 governed turn、MCP finish、协作 inquiry 或原生续做。每个审阅 attempt 新建根 session，参数固定为 `toolAllowlist: [Read, Glob, Grep]`、`mcpServers: []`、`offPeakToolEnabled: false`、`dynamicWorkflowEnabled: false`、关闭标题生成；mode 不使用普通 Worker 的 yolo。安装内容中的 session/create 协议与工具注册名有这些参数，但静态核对不声称完成原生验证。

L6-A 的接口固定为 `session_parameters(workspace) -> dict`、`native_contract_problem(source_text) -> str | None`、`ReadOnlyEvidence.observe(message, ordinal)` 和 `read_only_call(connection, control, result, workspace, access, tools) -> session_id`。session 参数显式选原生 `mode: plan`，避免继承用户的 yolo 默认值；已有原生 schema 支持 plan。免费资格检查只读公开 CLI bundle，最多 32 MiB，核对严格 session/create schema 的限制字段、具名 registerBuiltInTools/resolveBuiltInToolAllowlist 的过滤链和 Read/Glob/Grep 注册名；不绑定版本号、hash 或付费证书，未能识别机制时不合格。它只证明安装代码具有该机制，真正生效由运行协议和事件证明。L6-B 只负责入口、私有环境、调用和 owned-group 停止，直接复用 A 的接口，不再决定 session 策略。

L6-A 的独立审查提出四个反例，由单独的 L6-A2 修复，不追加到已交付 A：当前根的所有 canonical 帧（包括 tool.updated）都先留事实再核对单调 seq；subscribe 回报必须是本 sessionId、非负整数 eventSeq 与数组 events，includeSnapshot=false 的新 session 未要求 afterSeq，故回放 events 应为空，未知形状模型前失败；observed 保持 null，配置回读只放 resolved。静态检查支持两种明确的注册控制流：for 循环内 `if(!allowSet || allowSet.has(tool.metadata.name)) registry.register(...)`，或顶层短路 OR 链以 `allowSet && !allowSet.has(tool.metadata.name)` 拒绝、最后一项才是 registry.register。绑定 options→同一个 Set→同一个循环变量的 metadata.name→同一个 registry 注册动作；链外、无条件、反向成员过滤及不调用已定位 registerBuiltInTools 的 resolver 链均拒绝。变量名可变，未识别的控制流不合格，不仅看字符串存在。原生运行方式、工具与输出接口不变。

本地资格检查核对受限配置生成器、session/create 的严格参数 schema、原生 registry 的 allowlist 过滤实现与精确工具集合；运行时核对 create 成功、根 session、冻结 cwd、model/effort 配置回报与订阅握手，再发送模型输入。ZCode 没有原生 OS 沙盒，ADR 不要求它回报并不存在的沙盒策略，也不发明 snapshot 中没有的 toolAllowlist 回显字段；受限请求被拒绝、已知协议形状变化或有实际工具清单但不匹配时，在模型输入前失败，事件中出现额外工具时立即作废。无 OS 沙盒能力不构成资格否定，也不能宣称已有 OS 级禁网或目录隔离。

`ReadOnlyEvidence` 在现有 no-tool 的原生 session/event 规则上增加工具的 start/update/end 关联，使用 L5 的公共分类证据；canonical session/event 的完成、inputId/turnId 匹配、订阅序号和 session/close 回报是成功所需的事实。telemetry/state.updated 只作诊断，不能证明最终答案或代替工具完成。陌生 session、子代理、MCP、缺 toolCallId 的工具事件、未知 frame、断流、late tool event 如实投影为相应类别或不完整，最终由黑板判定；原生协议无法继续时报告实际失败和已有证据。格式纠正仍属同一黑板 attempt，可以在已确认关闭的根 session 后另建一个受限 session，工具与时限累计，至多两次答案回合；正常工具循环中的模型往返不当成格式纠正。

在假 app-server 协议中验证请求参数、策略回报、合法读工具流、坏身份与序号、外来/子调用、断流、预算、一次纠正、close 失败、取消与停止未知，兼跑原 no-tool 与普通 ZCode Worker 回归；经 DecisionAdapter 验证副本变化和不可用边界。本地/模拟验证后记录“原生未验证”。

## 委派划分与依赖

下表保留已经执行的原任务和正在执行的 L5-B/L6-A 范围；其中单个 Router 的旧验收结论是历史阶段事实，最终目标以上述列表设计为准。每行仍是可独立实现、验证与审查的成果，模块验收由 Host 负责。先落地纯接口，再接消费者，避免适配器各自决定策略；中间的纯接口任务可以暂不替换旧调用点，但 L4 验收前所有旧路径必须删除。任务描述必须附本文对应契约、准确读写范围、预期产物与检查，不让 Worker 决定字段、转换、降级或权限策略。

| 任务 | 修改与产物 | 不修改 | 验证 | 依赖与次序 |
| --- | --- | --- | --- | --- |
| L4-A 设置契约与换算 | 新 router_settings.py；不可变设置、补丁验证、纯转换与转换报告；test_router_settings.py | 不读写看板，不迁移，不接工作流/前端 | 转换矩阵、quick、缺省/非法/null、无副作用/幂等；禁止模型 stub | 第一批，与 B 可并行；先合入再接消费者 |
| L4-B 本地资格接口 | base/registry 与各 adapter 的免费资格声明；systemSandbox 程序事实及 health 读取；取消证书 eligibility 使用 | 不重做 Codex/Claude 运行，不改设置、账户或 paid API；DSH/ZCode 尚未实现时返回不支持 | 两档检查、声明/实现不一致、版本变化、零模型/零状态写入 | 第一批，与 A 独立；F/G1/G2 消费 |
| L4-F 设置存取与发布 | router 的 configuration/初始化、evaluation/user_policy 的字段补丁、修订、快照和 fixtures | 不改路由 request/claim/发布，不迁移、不改前端 | 权限/revision、字段省略/null、模式资格、原子拒绝、旧板 upgrade-required 零写入、不阻塞无关设置 | A/B 合入后 |
| L4-G1 请求解析、冻结与 Host 边界 | router.resolve/profile_problem、DecisionCoordinator 请求创建和冻结、最小 workflow 边界呈现，删除 request 时 fallback | 不改 claim/答案发布、设置 writer、原生 runner | 多/零/唯一候选、完整 buddy、不可用具体原因、同一请求幂等和冻结设置、无自动第二个 Router | F 合入后；交付固定请求输入契约供 G2 |
| L4-G2 认领复核与答案发布 | DecisionCoordinator 的 selector_family/claim/complete/publish、DecisionAdapter 收集；删除 claim/preflight fallback | 不改请求 admission、设置发布、原生运行方式或工具分类；统一工具证据留 L5 | 认领前健康/额度/资格/设置变化、家族容量、gate、预算/冻结副本/stop、取消/takeover/迟到答案、无重排 | G1 后；沿用现有工具检查，L5 接单一证据判定 |
| L4-H 删除付费审阅验证 | CLI/help/transport/contracts/service/console/worker/store/scheduling/health 的 review-check 入口和专用资源；测试迁移 | 不重写历史状态，不动普通执行/账户/停止，不改 skill | 所有旧入口拒绝、包/registry 无证书或 review-check；通用防护有测试去向 | B/F/G1 后可与 G2 并行；不写 G2 的方法与文件，按引用清单机械删除 |
| L4-I 控制台最小适配 | 现有类型/parser、单 Router 表单接线、删除证书控件/验证入口、preview fixtures | 不做界面设计，不加维护/画像/探索，不改后台策略 | 设置请求、实际 parser/preview、受影响 Vitest、typecheck/build | B/F 后，可与 G1/J 并行；仅 apps/console，H 完成后才做模块完整检查，Host 验收资源生成 |
| L4-J Router 健康诊断 | DecisionCoordinator.health_summary 与独立健康统计测试；必要时终结事件保存机器错误码 | 不改请求/claim/原生运行、共享设置或自动熔断，不追加到 G1/G2 | 四次超时计为四次失败与连续失败、三次连续有效 Router 失败即 available=false、错误原因明确；成功恢复，取消/stale/程序唯一候选不能伪造恢复；预算/越界/输入变动计数仍独立 | 用户新增，和 G1 并行独立 worktree；仅自己的方法区 |
| L5-0A 统一证据契约与黑板判定 | 新 tool_evidence.py 分类/完整性结构、DecisionCoordinator 的唯一判定函数与格式校验 | 不改四个 harness 的运行/工具，不自造原生数据、不接 L7 | 同一矩阵：fast 零工具；sandbox 允许 read/search/execute；无 sandbox 仅 read/search；修改/联网/other/不完整/坏绑定作废 | L4 验收之后，L5 的第一项 |
| L5-0B-Codex 原生投影 | codex_runner/no_tool 的事实采集与专用夹具 | 不改沙盒、工具、执行/纠正方式或黑板判定 | raw/high-level、子/foreign/late、去重与完整性，Codex 回归 | 0A 后独立 worktree |
| L5-0B-Claude 原生投影 | claude_runner 的事实采集与专用夹具 | 不改原生允许工具、沙盒或普通 Worker | 原生 tool_use/result/stream、身份与完整结束，Claude 回归 | 0A 后，与 Codex 可并行 |
| L5-0B-DSH 快速证据 | dsh_runner/no-tool 原生事实收集 | 不提前接只读工具、重做原生工具或循环 | 原生无工具回执/失败/纠正绑定，不伪造工具事件 | 0A 后独立 worktree |
| L5-0B-ZCode 快速证据 | zcode_runner 无工具分支的事实投影 | 不提前实现审阅、不改普通 Worker/MCP | 工具/foreign/子/late/断流事实、无工具回归 | 0A 后独立 worktree |
| L5-0B-Host 合并接线 | DecisionAdapter 透传、read_only.collect 去重复政策判断、发布处替换旧零工具判定、模拟回执迁移 | 不改四个原生运行方式或纯证据规则 | 四种投影加同一发布矩阵；保留原测试场景 | 四个投影合入后；再开始 L5-A/L6-A |
| L5-A DSH 只读原生插件 | 新 Node 原生 Agent/ToolRuntime 桥接、原生 API 夹具、Node tests | 不改 Python 路由/controller、普通 runner/no-tool/账户，不跑真实模型 | 原生 read/glob/grep、scope/视图限制、call/result/flush、非法工具原样记录/不执行、断流/预算/期限、无工具回归 | L5-0A 后；Node 原生桥接只消费稳定事实 DTO，与四个 0B 投影独立；B 接线仍等全部 0B |
| L5-B DSH controller 接线 | adapter/start、独立 Python controller、私有 profile preflight、夹具与包装 | 不改分类/判定、不修改日常配置、不跑真实模型 | dump-config、身份、纠正、owned stop、timeout/cancel、黑板四防护；DSH/no-tool 回归 | L5-A 合入后；Host 写记录并完整检查 |
| L6-A ZCode 受限协议 | zcode_read_only.py 的 session 参数与结构化回合，mock app-server、使用 L5 证据 | 不改普通 runner 调度、设置或 MCP/Worker，不跑真实模型 | 严格参数/配置回报、工具事件/序号/身份、完整流、纠正/预算、close/cancel，不虚构工具回显 | L4 与 L5-0B 验收后，可与 L5-A 并行 |
| L6-B ZCode controller 接线 | adapter、runner 独立 readOnlyRequest 分支、资格启用和集成测试 | 不改分类/判定、普通 Worker/no-tool/账户，不跑真实模型 | 身份/模型/强度、生命周期/stop、四防护、不可用边界；ZCode/no-tool 回归 | L6-A 合入后；Host 写记录并完整检查 |

取消 L4-C/D/E：原 C 的一致性责任移到 L5-0A/0B，D/E 的运行重做取消。G 按用户要求分为请求边界 G1 与认领/发布 G2，互相不跨写各自的方法区；顺序集成避免共享文件冲突。若引用面超出表内范围，Host 先改计划再发新任务，不把新的设计/修复追加成漫长委派。

## 2026-10-02 有序 Router 补充任务与顺序

此补充在已发出的原生任务之后集成到本阶段，不取消、不打断、不追加 L5-B/L6-A。Host 先按原范围验收并立即回收 L5-B，完成 L5 的完整检查；新的纯契约任务可以同时在独立 worktree 实现。新 L4 接线与不相交的 L6 原生协议/入口任务可并行，最后在列表切换、Host 边界和 L6 都合入后冻结 worktree，做一次完整检查作为 L4 补充与 L6 的最终检查。付费原生检查仍只各一次且逐次批准，不用它验证设置或切换逻辑。

| 任务 | 改什么 | 不改什么 | 独立验证 | 依赖与顺序 |
| --- | --- | --- | --- | --- |
| L4-R1 列表值契约 | router_settings.py 的不可变列表/间隔、严格补丁、保留双位置的纯转换与原位置审计；专用纯测试 | 不接数据库/当前 Router/调度/控制台，不迁移日常 | 全部类型/默认/顺序/重复/空/null/边界秒数/两个位置的顺序与去重、输入无突变 | 可立即独立实现；R2 消费前由 Host 固定接口 |
| L4-R2 当前角色与设置后端 | router.py 的 version 3 读写与唯一 current_router、router_history.py 的事件查询/幂等事实记录/跳过与活动再试投影，user_policy/evaluation 的列表设置；必要事件索引 | 不推进执行、不改适配器，不实现维护/画像，不新增熔断状态 | 新设置发布/upgrade-required、本地不调用模型、顺序资格/跳过/到期/恢复、读取不改计时、跨 plane 使用同一入口、逐项索引查询 | R1 后；Host 负责外围旧字段测试迁移，R6 可随后并行 |
| L4-R3 请求与认领 dispatch | router_sequence.py 的不可变请求/dispatch/attempt 事实；DecisionCoordinator 请求构包/建内部 task/claim/selector_family 和 store 的 pre-claim 接口；workflow pending view 读取当前 task 的接线 | 不写完成后的切换政策，不修改 worker_result 的终结规矩或 Host 边界展示 | 原 request/候选/输入 hash 保留、模型前跳过、重放无新 task、实际家族/账户/tool policy、容量排队与活动再试唯一执行 | R2 后；API 固定为本文的 _queue_router_dispatch 与冻结记录，不留 Worker 决定 |
| L4-R4A 纯结果分类 | Host 实现 classify_outcome 及完整纯矩阵，严格输入类型，取消/变更/停止未知/有效选择或无法选择/无答案的优先级 | 不读数据库、不推断代码性质、不推进 task | 全部分类与非法输入、丢失停止证据不能返回 answered | 接口已定，可与 R3 并行；R4B 直接复用 |
| L4-R4B 回执判定与顺序推进 | DecisionCoordinator._complete/_publish_select/released 的上下文判定，确认停止后调用 R3 的 queue 接口，router outcome/usage/trials | 不重新设计 Router 解析/设置/原生控制器，不添加通用任务重试或停止证明操作 | 下表全部 switch/stop 情形，异常/nonce/generation/late/replay、无循环、父取消、unknown stop、原生/外层 stop；旧故障仍可抓到 | R3/R4A 后；与 R5 不共享方法，先 R4B 后集成 R5 |
| L4-R5A 纯 Host 边界 | Host 实现 router_boundary.build_boundary 的纯 payload/命令模板，固定如下字段 | 不读数据库、不执行命令、不代 Host 指定 | 三类性质、冻结数据不突变、完整四元组模板、stop/hard gate 阻止模板、standalone 不造业务命令 | 固定输入接口可与 R3/R4 并行 |
| L4-R5B 边界接线 | workflow._routing_attention、selection/workflow view 与 CONFIGURATION_REQUIRED 说明，按 R4 的不可变事实准备 R5A 参数 | 不改设置/调度/原生控制器，不实现 L7/L9 事实 | 原目标/owner/revision 的实际命令、每项事实/连续数/retryAt、冻结候选/事实，无用户等待门槛 | R3/R4B/R5A 后；Host 审查命令契约与隐私字段 |
| L4-R6 健康与 Console 最小适配 | health_summary 按 Router 归属、service snapshot、apps/console 类型/parser/既有首项选择与间隔绑定/夹具；构建资源由 Host 验收 | 不改调度、统计事实规则、CSS/布局或 U1 完整列表编辑器 | 每项四超时计四失败、成功独立恢复/abstention 恢复、窗口与连续数、读取无副作用、表单保留尾部与清空、Vitest/typecheck/build | R2 后可实现，R4 记录格式稳定后集成 |
| L4-R7 Host 迁移与验收 | 外围 Python fixtures/旧单项断言与手写回执、参考文档、L4 补充记录、最终资源与完整检查 | 不删除仍有效的情形，不改 skill/入口/ADR，不作日常升级 | 新矩阵集成全检，代表性 guard 缺失变异，全部相关 Python/Node/Console 检查 | Host 自己做；所有代码稳定后再写最终结论 |

R1、R2、R3、R4 是不同接口层，不把列表、调度、停止与 Host 信息交给一个“L4 模块”委派。共享 decision.py 只按上表的方法区依次集成，冲突由 Host 核对语义解决；每个子任务固定提交后单独审查、跑表内检查、登记集成、验收并立即回收，分支随后删除。

| ADR 第 2 条情形 | 本请求动作 | 必须证明 |
| --- | --- | --- |
| 第一项不健康、额度耗尽、fast/review 不合资格、未发布/禁用 | 记录模型前 no-answer，取下一项 | 没有模型/已启动 attempt；身份顺序与模式不变 |
| 已在跳过期 | 越过，不增加 no-answer 或延长计时 | 新请求、重复 get/health/当前角色读取不变更截止 |
| 到期后首次再试与同时来的请求 | 一次再试，其余排队 | 只有已有活动 attempt 约束，无半开状态；失败后后移、成功后恢复 |
| 超时/空输出/provider 或 native 错误/坏 JSON/坏结构 | 停止确认后下一项 | 同 request/decision/governed run，独立内部 task 与同档预算 |
| 工具违规、不完整/缺绑定/foreign/child/late/截断、N+1 或超时限 | 停止确认后下一项 | 所有事实保留，不能以兼容 zeroToolVerified 放行 |
| Router 选择冻结外 ID、非法证据 | 下一项 | 分类为无答案，不和后来候选失效混用 |
| 有效选择 | 完成，不切换 | 当前选择与原硬边界合法，最后 Router 明确，前面原因可读 |
| 有效回答无法选择 | Host 边界，不切换 | 原因来自 Router，记录有效 answered 并恢复该 Router |
| 输入/副本改动；冻结内选中候选后来失效 | 变更边界，不切换 | 不污染 no-answer；取消后重提没有用，给冻结与当前差异 |
| 列表/模式/预算/revision、账户、reader、owner fencing 变化 | 变更边界，不切换 | 不借新配置执行老请求，旧迟到回执不改变当前状态 |
| 请求取消（模型前、执行中、结果竞争、推进前） | 取消，不切换 | 不创建下一 task 或业务 Worker，不归咎 Router |
| Native/controller 任一停止未确认，租约过期或 missing PID | 停止边界，不切换 | 保留容量/目录，不能用推测启动下一项 |
| 容量满、writer gate、再试已在执行 | 排队，不切换 | 没有 no-answer、没有备用 task 规避容量 |
| 列表空或所有项无答案/跳过/不可用 | Router 不可用边界 | 第 20 条四项、真实每项次数与时间、两种 Host 命令 |
| 请求/receipt/事件重放、旧 generation/attempt 的迟到答案、重启 | 不重复推进、不改已冻结事实 | 所有尝试/旧 receipts 与最终完成者仍能审计 |

Host 自己承担计划与模块记录、参考文档、`accept`/`conclude` 帮助的小修、最后集成审查与检查，不把它们追加到正在运行的委派。小修只改 help 提取器对公共必填字符串校验的识别或已有 override；不改 runtime 的 note 校验、验收或回收实现，增加能直接断言两个 help 将 note 标为 required 的既有 CLI help 用例即可。

## 检查安排与测试去向

准备工作已在独立 worktree 使用 Node 24.21.0 完成 `npm --prefix apps/console ci`；它满足 `apps/console/package.json` 的 Node 要求。计划阶段不运行原生检查或完整业务回归。计划提交前检查文档差异、单段单行、链接和本机路径；不将第一阶段的 1894 个 Python/106 个 Node 测试称为第二阶段结果。

委派只跑其表内的独立检查和受影响回归，所有 Python 检查经 `uv run --frozen` 使用本 checkout 与 test harness，复用 `buddy.checks.test_environment` 清除继承的 runtime/Worker/账户/代理环境；不手工给测试指向日常目录。重点回归文件包括 `test_routing_modes.py`、`test_router.py`、`test_decision.py`、`test_workflow_routing.py`、`test_user_policy.py`、`test_selection_policy.py`、`test_cli.py`、`test_no_tool_{dsh,zcode,codex}.py` 及各 harness 的现有 controller/protocol/私有目录测试；准确新增测试文件由任务产物固定，测试运行命令与结果保存在各任务日志中。

被改写的旧用例仍逐一对照：省略补丁保留、严格设置、无模型资格、回放冻结、输入 hash、零工具/统一证据、预算、schema、容量/停止/generation 防护保留；纯转换改为两个位置都留下并检查默认位置在前。旧单项不可用/preflight 无第二 attempt 的情形改为单元素列表仍无替代；新增多元素列表时相同模式后移、确认停止后独立 task 和实际家族冻结。有效无法选择/输入变动/取消/停止未知仍证明没有第二执行，冻结外回答与后来候选失效分别覆盖。已退役证书授权情形的入口拒绝与无自动 paid work 不恢复。每个被删除或改名的测试在 L4 补充验收中给出原 ID、新 ID 或退役依据，不能只列数量。

Host 验收每个委派时，读取固定 artifact 的 diff 与风险 hunk，确认改动范围，亲自跑相应测试；不能只读 Worker 的成功总结。迁移了防护的测试，选取代表性故障使其确实失败（例如去掉工具白名单、取消输入 digest 检查、放回 fallback），在隔离的测试副本/补丁中验证，再恢复固定产物；不写只复制实现分支的测试。所有旧情形仍须有覆盖或明确的功能退役理由。

原 L4 的 I 与 Host 文档/帮助改动已合入并全检通过；最终列表补充按 R7 再验收。冻结当前 worktree，运行 `uv run --frozen python -m buddy.checks`；L5 和 L6 各自合入并写完记录后各再运行一次完整检查。完整检查期间该 worktree 不做编辑、merge、提交、生成资源或委派写入；后台 delegate 只能写自己的 worktree。失败则先等检查退出，修阻塞项后重跑对应检查，最后得到一次完整通过。frontend 修改在 L4 额外运行受影响 Vitest、typecheck/build；必要的 build 资源与最后文档稳定之后才跑完整检查。

外层检查命令清除 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`、`BUDDY_RUNTIME`、`BUDDY_RUNTIME_IDENTITY`、`BUDDY_WORKER_STATE`、`BUDDY_WORKER_ID`、`BUDDY_AGENT_CREDENTIAL`、`BUDDY_AGENT_CREDENTIAL_FILE`、`BUDDY_ACCOUNT_SELECTION`、`BUDDY_SUPERVISOR_START_ID`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`；子测试环境再使用 checks 的清理逻辑与未安装的原生 CLI sentinel，剔除继承的第三方 Claude 网关变量。测试框架创建私有 state/runtime/cwd，不依赖 `BUDDY_DEV_SOURCE=1` 覆盖已有 runtime pin。确定性的测试/打包长任务使用 command 方式，读取退出码、总数和失败摘要，原始日志放忽略的 `tmp/adr021-stage2/`。

## 原生检查与授权边界

L5、L6 的正常任务都只做到模拟夹具、公开安装代码/协议元数据的静态核对与不调用模型的检查。Host 准备显式的开发 probe，默认 prepare-only，参数 `execute` 才可调用模型，普通检查套件不能启用它；它直接运行本 worktree 的 adapter/controller，不经另一套 Buddy 服务，不写日常看板或用户配置。准备不接触凭据，只预留经用户允许的现有账户绑定消费路径。

付费原生检查仅 DSH、ZCode 各一次，不对 Codex/Claude 做付费检查。每次执行前，Host 用中文列明 harness、准备验证的具体命题、冻结测试文件/输出、时长/工具上限、最多一次格式纠正和可能使用的 quota，然后停下逐次请求用户批准。一份批准只允许所列的一次 probe；重试、扩大范围、从 DSH 换到 ZCode、换 buddy 或再次调用模型都要另行批准。未批准不能以 delegate、health、资格刷新或测试名义绕过；记录写“原生未验证”。如用户不批准，完成其余代码与模拟检查后交付该边界，不把原生支持描述为已实测。

真实 probe 的验收需要一条完整原生工具调用与完成证据、结构化选择、预算与停止回报、副本未改动和真实工具结果；只能看到最终 JSON、RPC 成功、模型的“只读”声称或没有调用工具的超时都不够。若策略/协议不符，先记录事实并修正计划或实现，再请求下一次授权，不在同一次许可下自动重试。

Host 的 DSH/ZCode 小探针放在 tests/probes/router_readonly_tools.py，准备阶段只创建私有夹具、冻结请求和 hash，调用次数为 0。模型输入只给读取 selection-guide.txt 的指令；文件含随机 marker 与两个合法候选中的目标，最终 reason 必须返回该 marker，原生证据必须有完整 read start/end。使用 brief 的 60 秒/8 工具，最多一次格式纠正，直接调用本源码的适配器而不启动看板或服务。执行复用完全相同的已准备请求，并以独占 execution.started 文件阻止任何重复执行；证据规则、真实 stop、累计预算和输入 hash 全部通过才报告原生通过。它不尝试本实现无法提供的 OS 级目录/禁网拒绝，不把文件未变当作系统沙盒证明；旧 Codex/Claude 权限探针及其故障测试保留。

## 委派、集成与记录

所有 buddy 提交省略 harness/provider/model/effort，不给部分四元组；任务需要的复杂度、推理和验证能力写入任务描述。一个议程用一个 objective，每行任务单独委派，固定 requestId，保存 runId/objectiveId/controlFile。并行写入使用 `executionWorkspace.kind=worktree` 的独立检出；includeUntracked 只列源检出中已存在的未跟踪输入，尚未创建的输出文件由 writeScope 声明并在 Worker 提交中跟踪，不能将未来路径放入 includeUntracked；Host 的分支只由 Host 合入已验收成果，不改 main、socu/buddy-core 或其他会话的 worktree，不推送。

每个运行的委派只有一个原生监控子智能体，依已安装 Codex Host 指南用 `gpt-6-luna`/`low`、`fork_turns=none` 明确启动，只执行同一 run 的 await；慢或 parent wait 超时继续等待原子智能体，需要进度时 Host 自己读 get。只在监控本身失败或提前结束时替换；无法建立监控才用前台 await。监控不读源码、不做实现、不使用控制文件、不改任务。Native 子智能体仅用于这类工具与上下文明确的监控，不代替 buddy 的代码委派。

成果验收使用已安装 0.27.0 的 `integration-record`、`acknowledge` 与 `workspace-cleanup-plan/apply`，不能用正在实现的源码 accept/conclude/reclaim 操作作为委派流程。Host 本地合入固定提交并跑相关检查后才登记确切 artifact/integration ID。成功立即回收受管检出；失败或取消先按已安装操作写 recorded 结论，再回收；回收受阻保留具体原因，不手工越过停止/成果证明。子任务分支合入并回收后删除，未合入的成果先确认结论和保留证据再删除。

Host 分别写 `docs/acceptance/l4-adr021.md`、`docs/acceptance/l5-adr021.md`、`docs/acceptance/l6-adr021.md`，每段一行，保持简短，列实际检查、未检查内容、计划偏离和提交；L4 附迁移测试去向表。更新拥有契约的 `docs/reference/decision.md`、`architecture.md`、`evaluation.md`、`harnesses.md`、必要的 `cli.md`、`workers.md`、`runner.md`，按模块只写已实现部分。路径用 `~` 或占位符，不将本机主目录或项目路径写进 tracked 文档、fixture、代码样本。

每个委派记录提交时间、进入终态/Host 边界时间与实际执行回合起止时间：报告的执行时长采用黑板/原生回执的 execution elapsed，另列从 submit 到交付的墙钟时长，排队/验收/监控不混进模型执行。记录失败、返工或取消的回合，不只统计成功委派。原始 get/await/测试日志留在 `tmp/`，不能把 controlFile 内容或凭据放进记录。

执行中发现接口或实现假设不成立，由 Host 先修改本计划、说明改变了哪个选择与任务边界，并单独提交，再发新的有界任务；不能让 Worker 默默扩大工作。最终汇报分支与模块提交列表、计划与三份记录、最后一次完整检查的实际总数、每个委派时长、计划变更和 Host 自行定下的选择，以及原生未验证/待批准内容。

## Host 在 ADR 留白处作出的选择

现行设置采用 `routerProfileIds` 有序列表与 `routerRetryIntervalSeconds`，其余现有字段保留；纯换算将旧默认模式对应的位置排在前，另一个位置排在后，去空并稳定去重；旧设置只在 L15 显式升级转换。一个请求向后使用独立内部 task，下一项获得同档预算，前项必须确认停止。单字段、舍弃另一旧位置和禁止第二项执行是已被用户的新决定取代的历史选择。

资格按有无系统沙盒分两档，Codex/Claude 保留已有运行；DSH/ZCode 限制工具为 read/search。统一证据放在 L5 开头，所有适配器只投影同一分类事实，黑板在现有快速零工具判定处统一决定；不在适配器或收集层另加 verdict。L4-G1 与 G2 分开请求边界和认领/发布，便于独立验证与审查。

设置与路由输入冻结到同一请求，claim/preflight 失效停在 Host 边界；容量满继续排队；单候选和显式 buddy 的既有直接路径保留。预算沿用已实施的时间/调用数上限与未知字节计数，没有在本阶段发明另一个预算契约。原生检查与本地资格分开，DSH/ZCode 的未授权原生调用明确留空。

## 计划修订与当前进度

2026-10-02 按用户批准的四点修订：合入 core 的第 4 条更新；取消 Codex/Claude 运行重做及动态/Python 文件工具；一致性责任移到 L5 开头并由黑板统一判定；G 分成请求/冻结/Host 边界和认领/发布两个任务；付费检查只保留 DSH、ZCode 各一次、执行前逐次批准。原计划提交为 89a498b，本次修订单独提交后直接开工。

已完成独立分支、core 合入、规定文档/源码阅读和 Node 24.21.0 的 console 依赖准备。L4 的 A/B/F 由 Host 实现，G1/G2/H/I/J 已分别委派、审查、定向验证、合入并回收；L4 完整检查已通过（1922 Python/106 Node），当前开始 L5。完整检查在每个模块全部集成后运行。后续进度与委派时长写入各模块 acceptance 记录，原始日志留 tmp/。

首次 L4-A 提交在 admission 前被 INVALID_WORKSPACE 拒绝：安装版 includeUntracked 只允许已有未跟踪输入，不允许未来输出路径。未创建有效委派；调整提交模板为新输出仅列 writeScope，原 intent 已形成不可变准备记录，修改输入后改用新 requestId，不删除旧证据，不改任务契约或日常数据。

2026-10-02 第二次 core 合入带来 ADR-023（core 7e86bb6）：统一词汇改为 ACP 原始类别，DSH 改为公开原生 AgentRegistry/ToolRuntime 的 scoped restriction 和 native presentation，不再自己实现文件工具或 LLM 工具循环。L4-A/B 两个委派在 Router 预算边界失败，A 同一委派重路由一次仍失败；均已取消、记录结论并回收。这两项由 Host 按原接口实现，后续委派使用更短任务描述再尝试；不改日常 Router 或运行时。

用户于 2026-10-02 确认 Router 失败来自 DeepSeek 官方 API 性能下降，授权当前 Host 边界和恢复前的新委派使用完整合法 buddy 四元组绕过 Router；恢复后回默认路由，不改共享设置。G1 使用 codex/openai/gpt-6.1-sol/high 继续同一 run；已完成的 A/B 不重复执行。另增 L4-J：预算耗尽仍保留专门计数，也进入 failureCount/consecutiveFailures；健康诊断以现有前端连续三次失败提示为阈值，明确 available=false 和机器原因码，不增加新的持久熔断或自动重试。

L4-I 只消费 B/F 已提交的确定接口，删除失效的前端控件无需等待 H 删除后台方法。因此将 I 的开工依赖由 H 改为 B/F，允许独立检出并行实现，模块验收仍等待 G2/H/I/J 全部集成。

L4-H 的入口、registry、Worker 专用分支和证书资源与 G2 的 decision/发布方法区独立；调整为 G1 后并行。H 不编辑 decision.py、adapters/decision.py、evaluation.py 或 G2 测试文件，通用流/停止防护仍保留。

L4 集成审查补齐 catalog 的免费资格投影，并迁移外围测试的双位置设置、证书属性和手写原生回执；保留原场景的历史绑定、偏好审计、停止证据、账户节流等断言。删除仅由已退役验证器引用的挑战分支与历史 replay 夹具。完整检查冻结 Host worktree，L5 尚未开工。

L4 完整检查前细化 L5-0B：四个原生事件面拆成独立 Codex、Claude、DSH-fast、ZCode-fast 任务，Host 最后接唯一发布判定与外围模拟回执。0A 先交付纯接口与黑板包装方法，不提前替换旧发布调用点；0B-Host 一次接入强制证据，避免中间提交靠兼容 fallback 通过。这样每件成果可单独验证与审查，不让一个 Worker 承担四种协议。

补足公共证据精确类型：ToolEventEvidence(binding) 的 binding 只来自 Python 控制文件；normalize_tool_event 接受 controller 已提取的原生工具事实字段并按适配器原生工具名/类型分类，不遍历任意模型输出。finish 的 nativeIdentity 是本次调用的可信根身份列表（每个字典只保留原生实际提供的 sessionId/turnId/inputId/callId 等字符串），来自根 session/turn 创建回报；格式纠正的根回合逐项加入，不从所有收到的事件反推允许身份。事件的 nativeIdentity 必须精确匹配列表中的本次根身份，foreign/子/旧回合仍保留并使完整性失败；call 去重键是身份加 callId，raw/high-level 同 ID 同事实去重，确证不同原生调用分别计数，无法关联或矛盾则不完整。缺证据/坏绑定/不完整返回 router-tool-evidence-unverified，明确不允许的类别返回 router-tools-forbidden；快速零调用规则由同一函数判定。系统沙盒事实在 claim 事务由 read_health 的本地程序事实写入 attempt-tool-policy:<attemptId> 的专属 meta；发布包装只把它作为临时 toolPolicy.systemSandbox，发布时不采信答案自报；旧未绑定的结果不能发布。

静态核对当前 DSH 公共类型后修正方法拼写：单调拒绝接口是 agentCtx.tools.guard(callback)，并非 registerGuard；其语义仍是在原生 pre-execute 扩展之后追加不可强制放行的 guard。驱动采用原生 Agent.followup/whenIdle 与 SessionRegistry.flush，工具回合由原生 Agent loop 执行；setup 安装 restrict/presentAs/guard，返回 commit 时再次核对精确三工具集合。此修订不影响正在执行的 0A，也不新增模型调用。

0A 成果审查确认沙盒事实存 attempt 专属 meta，由 _attempt_tool_policy 读取并临时交给发布包装；这比改写 admission 的 input_json 更符合请求字节与 hash 冻结的不变量，Host 据此调整原计划。Host 补强 named reasoning/两个未知标识不能隐去真实调用、end-before-start 不得后来补成完整、零工具也需非空根身份；新增 tool_calls 只读计数供运行时累计预算、observe_incomplete(adapter,facts) 保留缺失 ID 的分类事实并置不完整，不捏造标识。原生非工具 reasoning 类型可省略工具标识；真实工具名 reasoning/thinking 归 other。普通 nativeIdentity 只允许实际 sessionId/threadId/turnId/inputId/callId 字符串字段；DSH 原生数值 turn/step 在桥接的关联检查中核对，未提供 turnId 时不合成。

0B 的原生投影须先于旧过滤/拒绝路径观察事件；保留原生权限/有效策略、协议身份、预算、停止机制。既有 zeroToolVerified 可作为兼容名称的零调用事实保留，但不自行给允许类别结论；read_only.collect 和 DecisionAdapter 的重复工具政策门槛由 0B-Host 移至黑板唯一判定。只有协议/身份/原生执行无法继续时才报告实际控制器失败，分类事实仍保留；缺失 ID 用 observe_incomplete。Claude 缺逐帧 session_id 的根帧可由已确认 system/init 的单根流与 parent_tool_use_id 关联，子流不得继承根身份；Codex 缺 turnId 且无法证明当前根回合时作不完整，不能用后到事件猜测。

0B 开工前核对发现快速入口的私有 control 还没有 taskId/attemptId/generation；Host 补入这三个已有程序身份，给证据 binding 使用，不传给模型 prompt/schema，不增加 Router 权限。四个投影任务只消费已经提交的同一接口。

静态核对后解除 L5-A 对四个投影的过强依赖：Node 原生桥接只需要 0A 已固定的事实 DTO，改动仅新 Node 插件和其 native API 夹具，与四个 0B 的文件和运行独立，可先并行。L5-B 的 Python 接线、启用资格和模块验收仍等待 0B 全部合入；L6 保持原依赖。监控容量不足时仅临时前台 await 同一 Node run，空位出现后再建立一个监控，不增第二个 run。

L5-A 的桥接输入固定为 callId/cwd/spec/prompt/outputSchema/budget（timeoutSeconds/toolCalls；Python 已扣除之前纠正耗用），只读 profile 的 Node config 另含 timeoutMs。输出 status/code/modelStarted/rawAnswer/resolved/observed/nativeIdentity/usage/nativeToolEvents/nativeToolEventsTruncated/streamComplete；nativeToolEvents 仅含 nativeIdentity/callId/toolName-or-type/phase，Python 控制器用公共收集器补 binding 与完整 toolEvidence。原生 Agent.options 与每次 request/header.header.config、header.tools 都需核对请求身份与精确三工具集合；header 在原生 prepareCall 后、stream 前由 Session.append 产生，发现不符同步 cancel 并拒绝后续模型输入。session/event 的 turn/start、tool/call、tool/result、assistant/message、turn/end 和 SessionRegistry.flush 是完整性依据，纯文本自称完成不算。消息使用 Agent.followup/whenIdle，成功 turn/end.reason.kind 必须 completed，flush 成功后先保留结果再 dispose；异常/预算/取消统一 dispose，未能确认则留下明确停止未知。

0B 合并审查补充 close_root(nativeIdentity)：在真实 turn/completed 或 Claude 根 result 时标记该根结束，随后即使新调用有完整 start/end 且早于 EOF，仍属迟到事实并不能发布。保留原错误/停止边界，ZCode 缺 callId 的事件不再伪造 usage.toolCalls=1。Node 桥接允许格式纠正的剩余工具预算为 0，N+1 仍禁止任何新工具，并保留 guard 拒绝时缺 ID 的事实。107 项投影相关复查通过，旧测试改为检查不完整证据与无虚构计数，未删除情形。

Host 接线审查发现 Codex 的 typed commandExecution 与 raw 原生执行调用可共享 call ID，但工具名是不同原生投影，不能因名字差异拒绝同一次合法调用。根据 [原生执行工具声明](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/shell_spec.rs) 与 [raw namespace 类型](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/models.rs)，补入 exec_command/shell_command/write_stdin/local_shell_call 的 execute 分类；非默认 namespace 始终按 other 投影。黑板只允许同一绑定/根身份/call ID 中 commandExecution 与一个已知原生 execute 名称的等价投影，保留所有原名和事件，仍只计一次；两个不同 raw 名、类别冲突、不明关联仍作废。此修订只调整事实分类与唯一判定，不修改 Codex 运行、权限或沙盒。

再次合入 core 的 `0239935`：新增第 21 条及 ADR-024 的控制台议题归后续 L17/展示任务；本阶段仍只实施第 2、3、4、20 条及 L4/L5/L6。L4-R2 验收时修正历史判定，优先采用不可变 terminal event 和 attempt receipt，拒绝带错误码的 abstention 被当作成功，并为 router.claimed 的逐项查询补索引；R3/R6 消费这一固定事实接口。

L6-A2 的独立审查仍能用“丢弃成员测试后无条件注册”和“allowedTools 放在未被读取的第三参数”骗过静态资格检查，因此新增有界 L6-A3，仅修免费检查及其反例，不改协议运行或适配器。实现采用字符串/注释/括号感知的 token 与完整受支持模板匹配，不执行 JavaScript、不加入通用解释器，也不继续用局部正则证明控制流。完整消费 Set 声明与唯一 for-of 循环体；只接受完整正向 if 或顶层 OR 拒绝链，成员测试必须是链中的完整操作数，register 是最后的完整调用；禁止丢弃测试、逗号/赋值表达式、额外尾部和第二注册。register 的首参必须是同一循环变量，或经已核对 helper 保留 Read/Glob/Grep 身份的变换；options 从函数形参位置确定，调用的该位置必须是直接对象且唯一 allowedTools 的完整值为已核对 resolver(config)，拒绝错位、spread、重复键和嵌套诱饵。

resolver 的完整返回链只接受直接返回 toolAllowlist、局部变量原样返回，或公开原生 bundle 中已识别的 alias-map/explore-filter/root-child 分支模板。每个 helper 都核对完整返回值与 Read/Glob/Grep 的保留，读取 allowlist 后返回 void、丢弃 map 结果、将 Read 映射成 Bash 或无条件扩大集合均拒绝。注册链其余拒绝项仅接受无副作用布尔/属性/比较表达式和已核对的纯名称谓词；不绑定 minified 名称、版本或 hash。静态结论只证明公开代码中存在识别出的限制机制，根 session 的实际工具面及执行仍须协议事实、统一工具证据和获批的原生检查，不能把源码模板核对声称为原生生效证明。A3 的测试保留 A/A2 全部有效情形，增加上述反例、字符串/注释诱饵、变量重命名与真实公开 bundle 的只读文本核对；Host 单独审查后再启用 L6-B。

R3 执行期间将 R4 分成独立的纯分类 R4A 和生命周期接线 R4B：纯接口已在计划确定，由 Host 先实现并跑矩阵，R4B 等 R3 固定成果后消费。分类不凭 code 猜情况变化，依次处理 cancelled、circumstances_changed、shutdown_confirmed=false，再处理有效选择/有效无法选择，其余为 no-answer；abstained=true 而 answer_valid=false 不构成有效无法选择。这样停止未确认的答案不能成为 answered，不把大段回执校验连同分类设计一起留给一个 Worker。

补充冻结预算与发布接线细节：standalone selection-request 原有的 timeoutSeconds 覆盖继续保留，同请求的每项 Router 都使用 admission 冻结的同一预算，current_router(frozen=...) 返回该预算原值，不按当前默认档重算；fast 始终固定 60 秒。发布只复核请求/设置/身份/账户/reader/owner 与候选硬边界和原生执行证据，不因调用期间缓存健康变差而否定已经有效的答案；下一次请求的资格仍由唯一当前 Router 入口判断。R4B 将 _publish_select 改为只返回程序检查结果（answer_valid、abstained、changed、code、reason、output、profile_id、selected、evidence），不先调用 _finish 再把 decision 改回 queued。无答案时仅记录该 dispatch 的 router.no_answer、释放该项 reader 并排下一项；所有项用尽或有效完成后才终结请求和通知父 workflow。这样中间失败不会短暂打开 Host 边界，也不生成错误的父请求终态事件。quota retry 的最终原子 claim 若拒绝，归情况变化，不能记 answered 或切换。

核对现有 schema 后修正 R3 的持久化表述：workflow_routes 只按 decisionId 关联父目标，没有内部 task 字段；推进只改 decision_requests 的当前 task 指针，workflow 的 pending view 从它读取。保留该稳定关联，不新增 task 列或另一个权威指针。当前 R3 已发出的任务不追加内容；Host 在固定成果验收时处理这个接线差异。

等待 R3 固定成果时将 R5 同样拆成纯 payload R5A 和数据库/view 接线 R5B。build_boundary(*, kind, code, reason, candidates, facts, router_trials, retry_at, decision_id, run_id=None, revision=None, shutdown_confirmed=False, continuation_problem=None) 返回已规定 routingBoundary；所有数据深拷贝，只有三种 Router 性质，不执行命令。commands 固定为 continue:{blocked,reason,choices} 与 reroute:{blocked,reason,method,params,notBefore}；choices 每项为 profileId/method/params，params 使用同 runId、实际 revision、稳定唯一 commandId、完整 configuration、必填 input/reason 与 <saved-control-file> 占位符。notBefore 只作为模板元数据，不塞进现有 continue 的参数。停止未知或 continuation_problem 时两种操作 blocked；standalone 两种操作 blocked 并说明没有 governed goal；retryAt 未知时 reroute blocked 并说明尚无可知恢复时间，Host 仍可用合法候选继续。userAction 固定 settingsChangeOnly，表示仅改共享 Router 设置需用户，不给等待用户的状态。

R3 固定成果审查补两项反例：最终 dispatch 加入实际 Router 身份后再次核对字节上限；冻结 tableRevision 变化在记录 preflight 失败与推进前停止，不污染跳过史。Host 同时对齐 request/snapshot/dispatch 的超时覆盖、让 queue 接口设置 queued 状态供 R4 复用，并规定 freeze/reserve 的不同重放时钟不构成新事实，保留第一次时间；真正内容变化仍 CONFLICT。这些是 Host 的有界修正，没有追加到已运行的委派。
