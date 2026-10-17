# 有结构的原因：执行计划（第 2 版）

2026-10-17，第 1 版经 Host 与另一模型分别审后，按 Host 定下的原则收缩；以下是第 2 版，只写计划，未实现、安装或验收。归属是[第 4 步余项](step4-remaining-plan.md)批次二第 1 项，随这一批进 0.46.0，存储扩展 `blackboard/store/upgrade.py:upgrade_schema_24_to_25` 已有的 24 → 25。约束取 `AGENTS.md`、[ADR-007](../decisions/007-neutral-core-and-single-current-contract.md)、[ADR-029](../decisions/029-console-frontend-foundation.md)、[控制台参考](../reference/console.md)和 `CONTEXT.md`；参考页开头仍写 schema 24，当前 `store/db.py:SCHEMA_VERSION` 已为 25，以源码为准。

## 1. 清点与边界

术语：原因 / reason 是代码加参数，文字由读的一侧给出；内容 / content 是 Host、Worker、Lead 或 Router 写的文字，原样显示；诊断 / diagnostic 是给开发者看的错误细节。内部执行的任务描述和角色指令按内容保全。下列路径除 `apps/` 外都相对 `src/hey_my_buddy/`；方法名省略其类前缀，`workflow.py` 的方法属于 `WorkflowCoordinator`，`decision.py` 的方法属于 `DecisionCoordinator`。花括号表示当前插值，斜线并列表示同一函数的分支，非原文的字段概括写在字段列。读者来自返回路径和调用方的静态核对，没有实测任何 harness。中、英指当前程序文字的语言；编号、状态、原生输出、路径与命令本身不是文案。

### 1.1 路由不可用与发布原因

`blackboard/routing/router.py:buddy_problem` 返回 `(buddy, code, reason)`，以下各条 `reason` 都有中文前缀 `Router 不可用：`；`current_router` 将它放进 `inspections[].reason` 或 `problem.reason`。读者为 Host、控制台，也随冻结输入到 Worker。现有 `router-unavailable` 不能区分禁用、目录缺失、harness 健康；它的实际读取用途与是否需要保留见第 2 节，已有细代码不再另造同义代码。

| 函数 | 字段 | 当前文字（中） |
| --- | --- | --- |
| `buddy_problem` | `reason` | 尚未设置 Router buddy |
| 同上 | 同上 | `buddy {buddyId} 未发布或已被移除` |
| 同上 | 同上 | `buddy {buddyId} 的 harness/provider/model/effort 身份不完整` |
| 同上 | 同上 | `buddy {buddyId} 已禁用` |
| 同上 | 同上 | `buddy {buddyId} 在已发布目录中不可用（{detail}）`；缺省 detail 为 `目录未声明可用` |
| 同上 | 同上 | `harness {harness} 不可用（{reasonCode}: {detail}）`，detail 来自 health.reason/remedy/status |
| 同上 | 同上 | `buddy {buddyId} 有仍然生效的原生额度耗尽记录` |
| 同上 | 同上 | `harness {harness} 不满足 {routingMode} 模式资格（{detail}）`；fast detail 是 `harness 未实现无工具结构化入口`，review detail 是 `{reasonCode}: {reason}`，异常 detail 是 `{error.code}: {error.message}` |
| `current_router` | `problem.reason` | `Router 不可用：{error.message}` |
| 同上 | `inspections[].reason` | Router buddy 在请求冻结时尚未发布 |
| 同上 | 同上 | `Router 不可用：在暂时跳过期内，{retryAt} 后可再试` |
| 同上 | `problem.reason` | Router 不可用：尚未设置 Router buddy 列表 |
| 同上 | 同上 | `Router 不可用：列表第 {afterIndex+1} 项之后没有更多 Router` |
| 同上 | 同上 | `Router 不可用：列表中 {count} 项当前都不可用（{codes}）` |

`blackboard/routing/decision.py` 还有直接选择、排队、拒绝发布等说明；它们写入 `evaluation_decisions.reason`，经 `_view`、`_finish` 的事件、`workflow.routing_settled` 与宏任务路由片段到 Host、控制台、Worker。下表保留当前文字用于核对；哪些说明删除、哪些代码直接复用，统一按第 2 节。

| 函数 | 当前文字与语言 |
| --- | --- |
| `_create`，常量 `SOLE_CANDIDATE_REASON` | 唯一合法候选，未调用 Router（中） |
| `_create`，常量 `NEEDS_HOST_NO_BUDDY` | Router 不可用：尚未设置可用的 Router buddy；请指定完整合法 buddy，或由用户更新 Router 设置后显式 reroute。（中） |
| `_create`，常量 `NEEDS_HOST_NO_CANDIDATE` | no enabled, available, capability-matching buddy is a legal candidate under the published pins and excludes, so there is nothing honest to recommend（英） |
| `_assemble_input` | `the complete bounded table slice carries {count} {name}, above the helper's {limit}-entry request bound; nothing was truncated and nothing was sent to a model. Curate the published candidates or preferences first.`（英） |
| 同上 | `the complete bounded table slice is {bytes} bytes, above the {limit}-byte decision input ceiling; nothing was truncated and nothing was sent to a model. Curate the published table first.`（英） |
| `_select_input` | The legal candidate set exceeds the bounded decision buddy limit; narrow the task constraints（英） |
| `_create` | queued for a Router worker（英） |
| 同上 | `The complete frozen routing input contains {bytes} UTF-8 bytes, above the {limit}-byte decision input ceiling; nothing was truncated or sent to a model.`（英） |
| `route_workflow` | `The complete Micro task contains {bytes} UTF-8 bytes, above the selector's {limit}-byte task limit. No model call was made. Continue the same Micro task with a complete buddy; its full task remains unchanged.`（英） |
| `_queue_router_dispatch` | queued for the next frozen Router dispatch（英） |
| `prepare_router_claim` | superseded by a newer Router dispatch；No frozen Router request snapshot is recorded for this dispatch；missing frozen Router request snapshot；the Router item is unavailable before the model call（英，后两类也作取消内部任务的说明） |
| `claim` | No frozen Router dispatch is recorded；missing frozen Router dispatch（英） |
| `_mark_running` | Router is running over its frozen input（英） |
| `cancelled` | `the decision run was cancelled: {reason}`（英） |
| `fence_attempt` | `{reason}; no delivered answer can become a current recommendation`（英） |
| `complete` | `the model output could not be adopted ({code}: {message}); the Host decides`；the decision result could not be processed; no recommendation is published（英） |
| `_complete` | The bounded Router call produced no usable answer；The parent route or its owner was fenced before publication；The current attempt has no immutable Router dispatch；The current input differs from the immutable dispatch；The outer Worker reported a failed Router call（英） |
| 同上 | `The selected buddy remains invalid after rerouting: {reason}`；The Router stop is unconfirmed; no recommendation is published；The routing request was cancelled; no recommendation is published（英） |
| `_retry_selection` | `{reason}; no legal candidate remains after rerouting`；`{reason}; rerouting cannot continue: {problem.reason}`（英） |
| `_tool_evidence_problem` | Router publication requires unified tool evidence；The receipt reports no native root identity；The tool evidence binding does not equal this attempt's frozen binding；The tool evidence does not cover the receipt's root identities；No claim-frozen systemSandbox fact exists for this attempt；Router tool evidence is incomplete, inconsistent or malformed；Router tool evidence records a call outside the frozen tool policy（英） |
| `_publish_select` | Router input verification differs from the frozen copy or manifest；Router publication requires native and controller stop evidence；The bounded Router call produced no usable answer；Router publication requires complete frozen input verification；The helper answered a different operation；Router publication requires elapsed time and native tool count evidence；Router publication exceeds the frozen time or cumulative tool budget；The receipt's tool count differs from its native events（英） |
| 同上 | `Selected buddy {buddyId} is {problem}`；problem 为 removed from the candidate table / disabled / unavailable / excluded by the user / missing a required task capability / outside current pins, task constraints, or quota limits（英） |
| `_finish` | The selected quota retry was consumed by another decision（英） |

`_publish_select` 验证成功后采用 `router.validate_answer` 返回的 Router `reason/evidence`，这是 Router 的内容，保留原文；失败输出里的原生错误详情仍是诊断，但服务释放执行所写的说明是原因的来源，不能混为诊断。`route_workflow` 的 `Micro task {runId} (full task retained in workflow)` 与 `_create_task` 的 `Bounded selection decision over the current evaluation table.` 是内部执行的任务描述，参与 spec_fingerprint，按内容原样保留，不改任务描述或指纹。

### 1.2 harness 检测的处置办法

`buddy/harnesses/discovery.py:discover` 从 `_REMEDIES` 写 `remedy`，`blackboard/service/harness_health.py:refresh` 将整份结果存进 `harness_health.record_json`；`read_health`、`capabilities` 和控制台读取。下面当前全是中文，空 reason 时 remedy 为 null；原因编号本来已有，统一连字符探测编号与大写 HARNESS_* 的映射见第 2 节；删除 remedy，不改探测、可用性或资格判断。

| `_REMEDIES` 键 | 当前 `remedy` |
| --- | --- |
| `not-found` | 安装该 harness，或提供其可执行文件的手动路径后重新检测。 |
| `node-missing` | 安装与 CLI 配套的 Node.js，或将 node 放在 CLI 的 bin 目录。 |
| `login-required` | 使用所选 CLI 的原生登录命令登录后重新检测。 |
| `interpreter-missing` | 安装脚本所需的解释器后重新检测。 |
| `timeout` | 检查 CLI 本地启动是否卡住，然后重新检测。 |
| `output-limit` | 检查 CLI 的版本或登录检查输出，然后重新检测。 |
| `auth-unverified` | 检查 CLI 的本地登录状态与版本后重新检测。 |
| `version-unknown` | CLI 握手通过，但版本文本未识别；请核对本地版本。 |
| `scan-timeout` | 候选检测超过总时间上限；请缩小候选范围后重新检测。 |
| `shutdown-unverified` | 探测子进程的停止状态未获确认；请检查残留进程后重新检测。 |
| `pipe-failed` | 探测输出管道失败；请检查 CLI 的本地启动。 |
| `probe-failed` | 检查 CLI 安装与本地启动依赖后重新检测。 |
| `launch-failed` | 检查可执行权限与安装路径后重新检测。 |

| 文件、函数 | 字段与当前英文 | 读者 |
| --- | --- | --- |
| `blackboard/service/harness_health.py:read_health` | `remedy`: Run hey-my-buddy harnesses with refresh:true | Host、控制台 |
| 同文件 `refresh` | `remedy`: Repair the native CLI installation and run hey-my-buddy harnesses with refresh:true | 同上 |
| `buddy/harnesses/base.py:registered_read_only_check` | `reason`: No implemented native read-only structured call / The native read-only sandbox is unsupported on this platform / A harness without a system sandbox must restrict tools to read and search / The registered native read-only run is missing | Host、控制台、路由 |
| `buddy/harnesses/dsh/adapter.py:local_read_only_check`、`zcode/adapter.py:local_read_only_check` | `reason`: DSH / ZCode review on the Worker carrier is not implemented; its separate read-only channel was removed from stage 2 | 同上 |
| `buddy/roles/router.py:prepare_router_fast/prepare_router_review` | `BoardError.message`: Router 不可用：harness 未实现无工具结构化入口 / `Router 不可用：{detail}`，review 缺省 detail 为 `harness 不满足审阅模式本地资格`（中） | 由执行进程带回服务 |

### 1.3 路由边界与等待原因

`blackboard/routing/router_boundary_data.py` 投影下表；所有文字均在 `reason`，读者为 Host、控制台和持有该边界的 Worker。参数取现有关系与冻结身份，不重新探测或重算历史选择。保留条目自己的 code 和已有事实，删除 reason 文字，不另存一个同义原因。

| 函数 | 当前文字（英，逐分支） |
| --- | --- |
| `_preflight_no_answer`、`router_trials` | Cached Router qualification, health or quota is unavailable；追加 `; no recovery time is recorded` |
| `router_trials` | Eligible item was not dispatched；This item was not reached by a recorded dispatch or inspection；The attempt has no matching immutable dispatch binding；The dispatch was cancelled；The dispatch stop is unconfirmed；No immutable adopted outcome is recorded |
| `_continuation_context` | No micro task is recorded；The target is not owned by this controlling micro task；The routing request or owner was fenced；The micro task or an owning ancestor is closed or cancelled；The micro task checkout is being cleaned or was removed；The micro task's execution has no confirmed stop；An owned helper's stop is unknown；An owned Router dispatch has no confirmed stop；The micro task no longer owns its frozen checkout allocation；Another owner holds the micro task's checkout |
| `_candidate_problem` | The published identity no longer matches the frozen buddy tuple；The frozen buddy is no longer enabled and available；The frozen buddy no longer satisfies the micro task's hard constraints；The frozen buddy no longer provides the required capabilities；The frozen buddy no longer satisfies the published pins and exclusions；Cached harness health does not permit this buddy；Recorded native quota still blocks this buddy; no recovery is assumed |
| `routing_boundary` | `Router 不可用：没有可用答案`，必要时前缀 `Router 不可用：`（中）；追加 `; cached qualification, health or quota has no recorded recovery time`（英）；A Router dispatch stop is unknown；Every frozen candidate is currently outside the hard bounds（英） |

`blackboard/routing/router_boundary.py:build_boundary` 还写下面这些中文，返回给 Host、控制台；`commands.*.params` 可以成为 Host 下一次提交，必须连输入接缝一起改。

| 字段 | 当前文字 |
| --- | --- |
| `commands.continue.reason`、`commands.reroute.reason` | 独立 selection-request 没有关联业务微任务，不能生成 continue 命令 / Router 或拥有的执行尚未确认停止 / 当前微任务 revision 未提供 / 没有冻结的合法候选 / 尚无可知的 Router 恢复时间；可先读取 health 再决定 |
| `commands.*.params.input` | 继续既定微任务。 |
| `commands.continue.choices[].params.reason` | Host 根据路由边界的冻结候选指定 buddy 继续 |
| `commands.reroute.params.reason` | 在 Router 再试时间之后重新路由同一微任务 |
| 顶层 `reason` | 追加 `；取消后重新提交没有用。` |

`blackboard/tasks/workflow.py:_wait_reason` 写 `compact` 返回的 `waitReason`，`cli/cli_views.py:micro_task_brief` 等投影保留它，`docs/reference/cli.md` 列出它；`apps/console/src` 无 waitReason 使用。以下全能从 state、任务状态与 activeRequest 推出，删除 `_wait_reason`、回复字段和 CLI 投影，不建等待原因代码；控制台保留按事实显示的现有行为。

| 条件 | 当前文字 |
| --- | --- |
| routing attention | model routing needs Host buddy; no new coding turn was started |
| assistance/helper-attention | the current turn requested Host assistance |
| helper-report | all helpers reached a terminal state; the Host decides the next turn |
| 其他 awaiting-host | the current turn returned and the Host decides the next turn |
| waiting-helpers | authorized helpers are still running |
| waiting-owned | the Lead turn ended and is waiting for its owned micro tasks |
| concluded | the micro task work was concluded by its Host without acceptance |
| delivered | the micro task work is delivered and awaits final acknowledgement |
| accepted | the final artifact was acknowledged |
| cancelled | the run was cancelled |
| failed | the run failed without a Host decision boundary |
| 执行 running | a turn is executing |
| 其他排队 | the run is queued for its next turn |

### 1.4 黑板替 Lead 写的关注与结论

| 文件、函数 | 字段、当前文字（英） | 读者 |
| --- | --- | --- |
| `blackboard/tasks/workflow.py:settle_turn` | 拒绝理由：Lead completion requires every owned micro task to be accepted or concluded / Lead waiting requires an owned micro task still queued, running or stopping | `_record_lead_attention` → Host、控制台 |
| 同文件 `_record_lead_attention` | `summary` 及 request.summary 重复上述理由；attempted: The Lead returned a lifecycle result that cannot be accepted；neededWork[0]: Continue the Lead or conclude its work and owned micro tasks；neededWork[1]: `Unfinished owned micro tasks ({total} total): {runId} ({state}), ...`；acceptance: The Host records the next action | Host、控制台 |
| 同文件 `conclude` → `_conclude_record`；`tasks/host_conclusions.py:view` | 名下微任务的 `workflow_host_conclusions.note`: `Concluded because Lead micro task {leadRunId} received a Host conclusion` | Host、控制台 |
| 同文件 `_stop_lead_for_conclusion` | 取消说明：Owned micro task stopped because its Lead is being concluded | Host、控制台 |
| 同文件 `_wake_lead` | 自动续接 input: An owned micro task reached a boundary. Review the current ownedMicroTasks in this turn input.；reason: owned micro task needs its Lead | Lead；input 是执行指令；reason 仅传给不读取它的 `_requeue`，删除这个参数 |

### 1.5 存储分类、宏任务事件与缺省标题

| 文件、函数 | 字段、当前文字（中） | 读者 |
| --- | --- | --- |
| `blackboard/tasks/storage.py:inspect`，常量 `LABELS` | `categories[].label`: harnesses → Harness 私有会话；zcode → ZCode 旧私有主目录；workspaces → 受管检出；runtimes → 运行时；backup → 上次升级之前的看板；durable → 看板与记录 | 控制台、Host 的 storage-plan |
| `blackboard/tasks/macro_tasks.py:_markers`，`MARKER_LABELS` | `markers[].label`: dispatch 派发、decide 决定、continue 续接、integrate 整合、accept 验收、reject 验收问题、conclude 结论、cancel 取消、takeover 接管 | 控制台 |
| 同文件 `_summary/_timeline_row`，`UNTITLED` | `title`、`fixesTitle`: 未命名微任务 | 控制台、Host |

这些 label/缺省 title 是读时产生，没有对应持久化标题需要替换；`_run_presentation` 从 workflow_runs.title、spec.task 和 Worker summary 取的真实内容不动。`macro_task_summary_cache.py` 的缓存是进程内投影，随服务更新重建。

### 1.6 控制台入口失效页面

`console/server.py:Console._handler` 内的 `Handler._launch` 捕获 BoardError 后直接返回中文 HTML，字段为 `<title>` 的 `hey-my-buddy 控制台入口不可用`、`<h1>` 的 `控制台入口已失效或暂不可用`、段落的 `请重新运行 hey-my-buddy console 打开控制台。后台任务不受影响。`，`lang=zh-CN`。读者只有浏览器；这是纳入项，返回主包 index.html，加固定 meta 标记 `console.entry-unavailable`；主应用在调用 API 前显示它，见第 5 节。状态码、no-store、可信 loopback 与 CSP 保持。

### 1.7 清点中新发现的项

下列为背景清单以外的程序说明；有读者的原因一并纳入，纯诊断与角色指令明确排除，不因它们是英文就漏掉。

| 文件、函数 | 字段、当前文字与语言 | 读者 |
| --- | --- | --- |
| `blackboard/tasks/workflow.py:routing_settled/_routing_attention` | reason 缺省 No legal coding buddy was selected；summary 为边界原因；neededWork: 读取路由边界的冻结候选和事实，指定完整合法 buddy 继续同一微任务，或在列明的时间后显式 reroute=true；仅更改共享 Router 设置需要用户。；attempted: Bounded model routing and native buddy validation / 已冻结合法候选并检查请求边界；未调用 Router，未创建 Worker 业务回合。；acceptance: A legal harness/provider/model/effort tuple is ready for execution（中英混用） | Host、控制台 |
| `blackboard/catalog/catalog.py` 常量及 `catalog_store.py:record` | `buddies.unavailable_reason`: not present in the latest confirmed native discovery / not present in the latest complete native discovery；`CATALOG_REMEDY`: Run hey-my-buddy harnesses with refresh:true；`canonical_payload` 缺省 unavailableReason: native route is unavailable（英） | Host、控制台、路由 |
| `buddy/harnesses/claude/adapter.py:ClaudeAdapter.available`、`buddy/harnesses/registry.py:capability_report` | reason: Native metadata is still unknown（英），或原生 metadata 的 unknown.reason / BoardError.message；其他 adapter 也可能将 remedy 返回为 available 的第二项 | Host、控制台 |
| 同文件 `discover` | warnings[]: `{harness}: {reason or 'harness unavailable'}`（英及原生文字） | Host、控制台 |
| `blackboard/catalog/catalog_store.py:record` | note: Native catalog facts were refreshed; existing user settings were retained. No model call was made.（英）；存在 catalog_observations.response_json | Host、控制台 |
| `blackboard/catalog/accounts.py:view` | guidance: Sign in using the harness native account flow. / Refresh the harness to obtain a native observation.（英） | Host、控制台 |
| `blackboard/evaluation/native_observations.py:warnings` | message: A recent native observation reports this selected buddy has exhausted its quota. / A recent native observation reported a temporary rate limit. / A recent native observation reported a quota limit; its current reset state may be unknown. / Recent native quota observation is near its limit; review before continuing.（英） | Host、控制台 |
| `blackboard/tasks/workflow.py:prepare_continuation_workspace` | summary: Continuation workspace preparation needs Host attention；attempted: `Prepare continuation {continuationId}`；neededWork: Resolve the workspace conflict and explicitly continue the micro task；acceptance: The allocated checkout has a verified fixed input（英） | Host、控制台 |
| 同文件 `_record_scope_attention` | summary: Workspace changes outside the authorized write scope need a Host decision；attempted: `Seal attempt {attemptId} on turn {turnId}`；neededWork: Record restore, adopt or abandon for the conflict, then continue the micro task；acceptance: The failed site has an explicit resolution and a fixed handoff（英） | Host、控制台 |
| 同文件 `takeover/stop_macro_task/_cancel_owned_run/continue_run` | Host takeover fenced model selection；Host takeover interrupted model selection; wait for its confirmed stop, then choose a buddy or reroute；User stopped macro task；Owning Micro task cancelled；manual continuation cancelled live helpers（英） | Host、控制台，进取消/路由/事件 reason |
| 同文件 `_record_helper_report` | summary/reason 已是 helpers-settled 或 error.code；`_record_request` 接收 Worker 的 request | Host、控制台 |
| `blackboard/tasks/command_receipts.py:current_response`，`CANCELLATION_NOTE` | note: Cancellation is durable. An active execution becomes cancelling and its worker stops the owned process group; this response never claims that a surviving process is already stopped.（英） | Host |
| `blackboard/tasks/workspace.py:_attached_branch_report` | note: `The checkout is registered and locked correctly, but its HEAD is attached to branch {name}`（无名称时 a branch）` instead of a detached commit, so it is not the disposable detached state this cleanup removes.`；retry: After detaching, plan or apply the cleanup again; the branch, its ref and its commits are untouched and stay the Host's to keep or delete.（英） | Host、storage 控制台 |
| 同文件 `shared_stash_inventory/shared_stash_comparison`，`_STASH_CAUSE_NOTE` | reason: the stash reflog did not parse as commit ids and descriptions；partialNote: `One observation was truncated to its bounded entry list, so {lost and/or new} entries beyond the observed window cannot be proven from these records; no such disappearance or appearance is claimed.`；causeNote: Why a stash entry disappeared is unknown: the user or another session may have popped or dropped it. Nothing was restored automatically and the turn is not judged failed on this fact; review the recorded commit and description, then recover it with the recorded git stash store command if it is wanted.（英） | Host、Worker、审计 |
| `blackboard/tasks/inquiry.py:observe` | bridge.reason: this adapter has no observation or inquiry capability / the attempt has ended; an idle or finished agent cannot be woken / this adapter observes native activity but has no correlated inquiry capability / the owning Worker has not attached this live run；live.reason: no live bridge for this attempt（英） | Host |
| 同文件 `_inquiry_view/_live_answer_value/_mark_unavailable/_live` | answer.reason: no correlated answer yet；message.reason: `the bridge cannot deliver this question ({code})`；live.reason: the bridge did not report readiness（英） | Host |
| 同文件 `_journal_identity_mismatch/_apply_bridge_answer/_apply_journal` | `the journal record belongs to another {task/attempt}`；the live bridge reported answered without usable answer text；the bridge recorded an answered journal entry without usable answer text（英） | Host、messages |
| `blackboard/store/store.py:_transition_attempt/message_post` | messages.reason: the attempt ended before this question could be delivered / no live agent to receive this question（英） | Host |
| `blackboard/service/service.py:capabilities` 的内层 handler | limitations.steer: an active attempt cannot be re-scoped; submit authorized continuation input after its turn ends；resume: micro-task continuation creates a new owned attempt; native session support is declared per harness；nativeAppWakeup: not provided: notifications are post-commit hints for clients；postgres: not provided: SQLite is the deliberate local database；remoteTenancy: not provided: same-user local service only（英） | Host |
| 同上 | selectionFallback: not provided: with no configured decision buddy or no legal candidate the decision is needs-host; the service never guesses a buddy and never selects a selector；decisionModelIdentity: the Router records its requested and resolved buddy; the served identity is unknown and is never invented；osIsolation: not claimed: the console token and session separate browser origins, not a same-user process with full shell and credential access（英） | Host |
| 同上 | agentScope: enforced in the supported API: an attempt-scoped credential may observe its own run and record suggestions only; it cannot submit tasks, create helpers, approve, take over, cancel, change shared settings or address another run. This is capability enforcement, not OS isolation against a same-user process that steals the credential file；hostAuthority: a Host decision requires the owner control capability (or an authenticated console session); a hostId label and a shared cache of the newest generation are never authority（英） | Host |
| `blackboard/tasks/inquiry.py:NOTE`，由 `observe` 返回 | note: Inquiry is bounded, read-only observation plus correlated questions. It never cancels, restarts, re-scopes or extends the attempt, and observation is not a percentage or a guaranteed ETA: fields the bridge cannot see are named in live.unavailable instead of being reported as zero. Repeating an inquiryId never injects the question twice; the same id with different text is a CONFLICT.（英） | Host |

`errors.py:BoardError.payload` 的 message 与 `console/server.py:Handler._error` 的 message 保持诊断，封套 `{code,message,details}` 不变；本项的业务原因若当前从 message 拼出，仍须在生产者处改成原因，不能把旧句子塞进 params。`workflow.py:_validate_turn`、Git/文件系统/原生异常详情、`execution_issues/service_issues` 和 stderr 日志是诊断；`tasks/reminders.py:run`、`service/daemon.py:_maintain`、scheduler 的 `_issue/_scheduler_issue` 不做原因文案。控制台 `api.ts:errorText` 已优先读本地错误码词条；批次三去掉把 error.message 当业务译文的回落，诊断仍可原样查看。

角色提示、发给 Worker/Lead 的续接指令、内部执行的任务描述保持内容；`console/server.py:SETUP_PAGE` 是缺构建产物的英文开发诊断页，不新增原因或另一个入口。原始执行证据保持现有名字、字节、摘要与读法，不改成 rawInput/rawOutput，不加 kind=execution-evidence；它们不作为原因的来源。

补正清点（以下均已按调用链核对，读者为 Host、控制台；附带内容到 Worker 时原样保留）：

| 文件、函数 | 字段、当前文字（英） | 当前传递路径 |
| --- | --- | --- |
| `blackboard/service/execution_scheduler.py:ExecutionScheduler._admit_available` | release 的 reason: service stopped before execution launch / execution launch failed before process creation；`store.py:release_execution` 缺省 Execution process could not be started | `release_execution` 写 attempts.error、attempt.released，再调 `decision.py:released` → `_complete/_finish`，可进入 evaluation_decisions.reason；不是仅日志 |
| `blackboard/store/store.py:_commit_execution_result` | the execution ended before result delivery | recovered 且有 interruption 时调 `decision.py:fence_attempt`，追加 no delivered answer can become a current recommendation，写决策原因 |
| `blackboard/routing/decision.py:_queue_router_dispatch` | `The complete frozen Router dispatch contains {bytes} UTF-8 bytes, above the {limit}-byte input ceiling; nothing was truncated or sent to a model.` | 抛 router-input-too-large，`_create/prepare_router_claim/_complete` 三处捕获，将 message 写进决策原因；`_retry_selection` 也会拼接过大输入说明 |
| 同文件 `_publish_select` | `Selected buddy {buddyId} is {problem}` 的 problem | 六种：removed from the candidate table、disabled、unavailable、excluded by the user、missing a required task capability、outside current pins, task constraints, or quota limits；当前粗代码 router-out-of-bounds，仍供路由分类使用 |
| `blackboard/tasks/workflow.py:_require_enabled_buddy` | BUDDY_UNAVAILABLE.details 的 reason= catalog-unavailable / not-enabled，remedy= Run hey-my-buddy harnesses with refresh:true / Enable this buddy; Run hey-my-buddy harnesses with refresh:true only re-reads the catalog and never enables a buddy | 指定 buddy 的写入准入，直接给 Host；reason 直接成为代码，删除 remedy；catalogReadAt、enabled、buddy 是参数/事实 |
| 同文件 `child_settled` → `_requeue` | authorized helpers settled | actor=workflow/auto；`_requeue` 没有读取 reason，随后 workflow.auto_continued 事件也不写 reason。这是无使用者的参数，删除传递，不新增原因或迁移 |
| `blackboard/routing/decision.py:_buddy_input` | buddies[].unavailableReason = buddies.unavailable_reason | `_table_input/_assemble_input` → 冻结 dispatch 模型输入；列改成原因后，新输入形状一并改。已发出的文档及 inputSha256 原样保留 |

## 2. 原因形式与各字段的代码

原因只用 `{code:string,params:object}`，无原因用 null；内容继续是字符串，诊断继续是错误详情。不附英文句子，不把原句塞进参数，不加来源字段或历史内容分支。分类、事件和可推导的等待说明直接由既有事实给读侧，固定说明从回复删除；不为它们登记代码。

一个事实一个代码：已有细代码直接作为原因 code；只有粗分类仍被逻辑使用且不足以说明具体原因时，保留分类并另加细原因。新增代码用小写连字符，确需领域前缀时用点，如 `lead.result-refused`、`console.entry-unavailable`；不把已有 router-*、answer-*、catalog-unavailable 等全部换一遍名字。参数按实际事实使用 string、integer、finite number、boolean、null 及有界数组/记录，时间仍用 UTC 字符串；不预先格式化日期或拼接列表。每种参数的类型只由实际跨进程载荷定义，不做通用模板类型系统。

| 当前字段与依据 | 统一后的处理 |
| --- | --- |
| `router.py:current_router` 的 `inspections[].code/reason` | router-not-configured、router-not-published、router-incomplete、router-quota-exhausted、router-no-tool-unsupported、router-review-unsupported、router-skip-window 直接用其现有 code，删除 reason 文字。已有 buddyId、identity、retryAt 等即参数来源，不复制到另一个 reason 对象；不再造同义的 routing.* 代码 |
| 同上及 `buddy_problem` 的 router-unavailable | code 本身没有被 `_CHANGED_CODES` 或 failover 按值消费，history 对它只按一般非空代码处理；因此 inspection/problem 的这个粗代码一次换成 router-disabled、router-catalog-unavailable、router-harness-unavailable、router-not-published-at-freeze、router-list-exhausted、router-all-unavailable，不再并留第二个 code。边界 kind=router-unavailable 仍是边界分类，含义不同，保留；释放路径按明确的未创建进程原因给代码，不用一个不可用代码覆盖所有失败 |
| `decision.py:_finish/_complete` 的 `evaluation_decisions.error` | 当前混有机器代码、代码加诊断与异常文字。删除混合 error 列：机器代码移入唯一 reason_code，必要事实放 params，诊断留现有失败输出/issue；与 reason 同值的不再存两份。router-input-too-large、router-input-changed 等精确代码直接复用。router-out-of-bounds 仍由 `_complete` 的重路由分支、`_CHANGED_CODES` 和 health_summary 计数消费，保留这个 code，细分放 params.bound；业务逻辑直接读相同的 reason_code，无须再造 classification_code。仅当某处已证实另有独立分类且参数不足以表达时，才保留那个现有分类事实；本轮已核对的这些字段不需新增分类列 |
| `_publish_select/_tool_evidence_problem` 的既有代码 | answer-*、router-input-changed、router-stop-unconfirmed、router-budget-exhausted 等精确代码直接复用。router-out-of-bounds 的 bound 参数明确六值：removed、disabled、unavailable、excluded、capability-missing、constraints-or-quota；router-tool-evidence-unverified 覆盖多个缺证据分支时，仅在确有区分读者的分支加具体事实参数，不重复造同义代码。`_finish` 的 quota retry 被消费仍属 router-out-of-bounds，参数明确 quota-retry-consumed |
| `router_boundary_data.py:router_trials` 的 code/reason | 删除 reason。每条只用自己的 code、phase/outcome、buddyId、retryAt 和绑定事实给文案，不能拿当前决策的原因替代旧条目；无 dispatch/unvisited 等已有 phase/outcome 足够时不加原因代码。有 code 的 inspection 同理，只有新增细分且确有必要的参数随事实保存 |
| `harness_health.record_json` 的 reasonCode/remedy、candidates/lastProbe/accountReadFailure、本地只读资格的 reasonCode/reason | reasonCode 统一为 reason 的 code，删除 reasonCode、remedy 和旧 reason 文字；候选已有位置/状态等事实不重复。全套探测与健康代码一次归为 `harness.*`：探测 not-found/node-missing/login-required/interpreter-missing/timeout/output-limit/auth-unverified/version-unknown/scan-timeout/shutdown-unverified/pipe-failed/probe-failed/launch-failed 分别加 harness.；HARNESS_NOT_CHECKED → harness.not-checked；HARNESS_START_FAILED → harness.launch-failed；HARNESS_HANDSHAKE_FAILED、HARNESS_UNHEALTHY → harness.probe-failed；HARNESS_ACCOUNT_READ_FAILED → harness.account-read-failed；HARNESS_ACCOUNT_STOP_UNCONFIRMED → harness.shutdown-unverified，operation=account-read，与探测的 operation=probe 区分。readonly-* 资格代码同样一次加 harness.，DSH/ZCode 共用 readonly-worker-carrier-unimplemented，harness 是参数。原生账户协议的具体 BoardError 保持诊断，跨入健康结果时按已知事实映射，不能从英文推断 |
| `buddies.unavailable_reason`、`catalog.py:CATALOG_FACT_REASONS` | 目录确认缺失/推理强度退役两句变为 catalog.model-absent、catalog.effort-retired；原生 unavailable 声明为 catalog.native-unavailable，原生描述仍是内容。健康造成的不可用直接引用同一 harness.* 原因，不再生成第二个 catalog 或 HARNESS_* 同义代码。恢复目录/健康的所有比较一次改为代码 |
| `accounts.py:view` 的 reasonCode/guidance；额度 warnings 的 code/message | account 的 ACCOUNT_LOGIN_REQUIRED/ACCOUNT_NOT_CHECKED 一次改成 account.login-required/account.not-checked，只留一个原因，删除 guidance。额度既有 HARNESS_RATE_LIMIT_REPORTED/HARNESS_QUOTA_LIMIT_REPORTED/HARNESS_QUOTA_NEAR_LIMIT 与明确耗尽记录的代码直接作为原因 code，删除 message，保留窗口、比例、重置时间等实际事实，不再另造 quota.* 同义代码 |
| `workflow.py:_record_scope_attention` 的 payload.reason=workspace-scope-violation | 这个代码直接成为唯一原因 code，参数为 conflictIds、paths（保留现有限额）；删除 summary、attempted、neededWork、acceptance 的程序句子，不另造 workspace.scope-attention 等四个代码 |
| `prepare_continuation_workspace/_record_lead_attention/_record_helper_report` | 每个黑板请求一个原因及其事实：workspace.continuation-prepare-failed（continuationId，preparationError 仍为诊断）；lead.result-refused（disposition、unfinished、total）；helpers-settled 或实际拒绝的已有错误代码（helper 结果事实）。不复制 Worker 的四段请求文字。Lead 不再伪造 Worker turn，直接调用共用系统请求写入函数 |
| `_require_enabled_buddy` 的 BUDDY_UNAVAILABLE.details | 外层 BUDDY_UNAVAILABLE 仍是错误分类；details 原有 catalog-unavailable / not-enabled 直接成为原因 code，保留 buddy/catalogReadAt/enabled 等事实；删除 remedy。catalog-unavailable 与目录具体不可用原因是不同事实，不把精确目录原因伪装成通用错误分类 |
| `inquiry.py`、`store.py` 的 inquiry reason | no-journal-path、journal-not-written、journal-exceeds-limit、journal-unreadable、journal-unavailable、channel-unbound、no-live-channel、bridge-refused、observation-unavailable 等已有代码直接复用；bridge 支持与 delivery.state 已说明事实的重复文字删除。只对尚无代码的 answer-pending、attempt-ended、no-live-agent 等必要分支登记代码。message.reason 是可由调用方传来的内容，不能全列当程序代码；程序写入改 reason_code/params，内容仍在 text，详见第 3 节 |

边界顶层 reason 在读时取决策的唯一原因；`routerBoundary.kind` 与 retryAt 足以推出“取消后重新提交没有用”和“恢复时间未知”，删掉后缀拼接，不增加 resubmitUseful/recoveryKnown 参数。commands 的阻塞原因只在不能由现有 blocked、runId、revision、shutdownConfirmed 和候选事实推出时传代码；同一条件不另存中文说明。候选越界等现有代码加精确事实，不为每个英文分支机械增加一条代码。

续接模板删掉 params.reason，并删掉写死的 `input="继续既定微任务。"`；Host 实际提交时自己写 input/reason。`router_boundary.py:build_boundary.command_id` 的摘要包含 base、operation、index、buddy：移除 base.input 会改变新模板的 commandId，params.reason 当前在摘要外，单独删它不会改变 commandId。已有 commands 身份与 Host 提交过的 input/reason、request_hash 不改，旧请求重复提交仍重放旧收据；新模板用新摘要，不把已执行命令重新执行，也不为旧模板留兼容分支。

删除 `_wait_reason/waitReason`；删除 `capabilities.limitations`、`inquiry.NOTE`、`CANCELLATION_NOTE` 及回复 note。存储 categories 只留 id，markers 只留 kind 与结构化事实；工作区附着分支的 note/retry、stash 的 partialNote/causeNote 从现有 branch/headCommit/truncatedSides/lostEntries 等事实推导，删除固定说明，不逐句造代码；缺标题为 null，保留 titleSource；这些已有事实不再登记 Reason。`_requeue` 的 reason 未被读取，删参数与各调用点的无用实参，不把 authorized helpers settled 或 owned micro task needs its Lead 造为新代码；Host 已存的续接内容不动。

代码表只放 `protocol/reason-codes.json` 的代码与参数名，例 `{"router-skip-window":["buddyId","retryAt"]}`，不放类型、英文或数据库记录。`protocol/reasons.py` 只是构造原因的最小接缝，blackboard/buddy 都可导入；生产者不逐次做参数校验，静态测试检查源码里的构造及有限映射表使用的代码均已登记。按类型逐项校验参数只在执行进程 → 服务接收原因这一个边界做，沿实际 receipt/result 的载荷定义校验，不在构造函数、CLI、组件里重复。现有外围 API 的对象/封套检查保留，不追加一套通用 Reason validator。JSON 资源随现有明确资源清单打包并补冷启动资源检查。

控制台在 `apps/console/src/i18n/{zh,en}/reasons.ts` 各一份文案，经现有 i18n 注册与一个 reasonText 格式化；日期/数字在读侧格式化。Host 的完整与简略回复只带 code/params 与事实，不另留英语、不增加 CLI 翻译。代码说明由 Host 写在 `docs/reference/reasons.md`：代码/参数名的表从 reason-codes.json 生成并校验一致，再写语义说明；不手写第二份代码清单。共享 skill 只链接该页，CONTEXT 的术语更新由 Host 处理。

## 3. 存储与唯一升级

按 AGENTS，改变存储形式的同一个改动同时修改新库 DDL 与 `store/upgrade.py:upgrade_schema_24_to_25` 的对应步骤；各微任务依次扩展它，不另起升级、不写 meta 业务值。一个值在新库与升级库的含义相同；混合内容与原因的行用互斥列和 CHECK 明确，不靠写入年代区分，不留 historical-evidence 或旧句式运行时分支。

| 拥有的表/字段与写入点 | 新形式与 24 存量处理 |
| --- | --- |
| `evaluation_decisions.reason/error`；`decision.py:_create/_mark_running/_finish` | 程序原因存 reason_code/reason_params_json；删除 reason/error 两个混合文本列，逻辑仍需的粗代码在 reason_code 保留，细分参数不另存一份同义 code。已采用的 Router reason 从 `decision_requests.output_json` 中的 Router 答案读取，它是内容，决策不再复制一份。对外 reason 仅为原因/null，另以 content 字段给读取侧从已采用答案取出的文字；这是同一原文的投影，不新增存储，不改原始审计输出里的 reason 名字。Router 弃选的系统关注原因从现有 boundary.kind=router-abstained 得到，模型写的解释单列为内容，不能假装程序原因。升级以已采用输出、状态、路由来源与错误代码判明直接选择/程序拒绝/Router 内容，不因一句话相同就覆盖内容 |
| `workflow_routes.reason`；`workflow.py:routing_settled/_routing_attention/_cancel_routes` | 删除此列；原因只在 decision_id 指向的决策存一次。当前路由关注常先取 `decision.error or decision.reason`，可能是代码本身，有 boundary 才写拼句；副本不逐份解析，升级按关联删除。取消内容已在拥有它的取消/续接记录中，route 仅留关系与状态 |
| `events.payload_json` 的 decision.* reason/error，workflow.routing_needs_host 等路由副本 | 删除路由原因副本与同义 error 文字；保留已有 decisionId、状态、身份、时间、seq 和执行事实。需要看原因时按 decisionId 取，事件不再持久化 routingBoundary 文本快照；Router 历史每次尝试的结果代码按其现有 router_outcomes/事件事实保留，不用当前决策替换旧结果 |
| `router_requests.snapshot_json`、`decision_requests.requested_json` 中 inspections/selectionRetryInspections/routerProblem 与未发送的 Host 快照 | inspections 的 reason 文字直接删，沿 code 与已有事实；已细分的原因转其代码，只有旧 router-unavailable 的粗分支才从冻结事实和模板识别补细原因。删快照里的路由原因/boundary 副本，保留 decisionId 及冻结候选。snapshot 按设计不可改，本次显式升级一次转换；运行时不加可变写入口 |
| `workflow_requests.summary/payload_json`；系统请求写入与 `_propagate_boundary` | summary 改为可空，给 buddy 请求保留原文；新增 reason_code/reason_params_json，黑板的工作区/Lead/helper 请求用它，summary 为 NULL；黑板的路由请求只留现有 payload.decisionId，引回唯一决策，不另存原因。CHECK 明确 summary 内容、原因列、路由引用三种形式互斥。读回复给路由请求解析出的 reason；工作区/Lead 请求直接给 reason，没有原因的才是 buddy 内容。沿已有 source、conflictIds、continuationId、Lead 关系及代理链识别存量；不新加 source/origin。现有 payload.origin、proxy 仍表示代理请求链，保全并继承实际来源，不改变其含义 |
| `workflow_host_conclusions.note`；`conclude/_conclude_record` | note 内容与 reason_code/params 互斥（CHECK）；名下自动结论用 lead.owned-concluded，leadRunId 为参数；Host note 原样保留。父结论、workflow_lead_tasks、commands 与既有事件证明来源；仅 actor 相同或原句相同不足以转换。参数从关系行取，不从截断的 ID 猜 |
| `workflow_continuations.reason`、相关非路由事件与自动停止原因 | Host 提交的 reason、包括复制模板提交过的两句 reason，均是内容，逐字保留；确知为黑板自动停止的说明转代码与参数，用互斥 text/code 列明确。`_requeue` 的闲置 reason 没有存量。`input_text` 始终是内容，不改 fingerprint；停止角色、实际停止证据和原来的幂等身份不变 |
| `harness_health.record_json`、`buddies.unavailable_reason`、`catalog_observations.response_json`、`evaluation_catalog.payload_json`、`catalog_current.reason` | 健康/目录那一微任务同时改 DDL、升级、生产者与读者；health、candidate、lastProbe、资格和 accountReadFailure 的代码按第 2 节一次归一，删 remedy/reasonCode/旧句子。buddies 改 unavailable_code/params_json，原生说明独立保留为内容；目录两英文常量精确映射。目录刷新成功 note 删除，warnings 用已有原因与事实；由状态可推出的 guidance 删除。原始原生目录说明不递归替换。比较代码的目录保留/恢复逻辑同步修改 |
| `messages.reason`、delivery/answer 的程序说明；`inquiry.py`、`store.py:message_post/_transition_attempt` | 内容 reason_text 与 reason_code/params 互斥，程序消息已存在 delivery/code 的直接沿用，不再保存相同事实的 reason_text；消息正文/回答是内容，坏 journal 的 error 为诊断。升级依消息状态、作者/事件、delivery 与桥结果认出程序默认说明；有明确外部来源的说明保留内容，不能全列按英语映射 |
| `commands.response_json` 及其他已经缓存的完整回复 | 当前视图类收据继续只存身份；其余按 command.kind 删已移除字段、程序原因变码、路由副本变 decisionId。不改 request_hash、commands 身份、deletion_id，不解析 Host 提交的模板内容为程序原因。重放给新形状，但绝不重做已执行副作用 |
| `attempts.error`/释放事件中服务写的原因及路由发布的接缝 | release 的三句与 interrupted-before-delivery 是明确程序原因，在拥有记录里转原因代码与事实，决策以对应原因收尾，不当诊断丢掉。原生异常仍按诊断。代码接缝涵盖 scheduler、store.release_execution/_commit_execution_result、decision.released/fence_attempt，不改真实释放/停止证据 |

原始 `workflow_turns.input_json/outcome_json`、`router_dispatches.document_json/input_sha256`、已发送的 `decision_requests.input_json/output_json`、attempt result/封存清单保持原样，它们是执行过什么的证据，不改名，不按旧 reason 形状读取业务原因。未来 `_buddy_input` 改为从不可用代码/参数构造新模型输入的 unavailableReason，`spec_fingerprint/inputSha256` 仍按实际新字节计算；已发出的内部任务描述与原文指纹不变。Router 内容从已采用输出读取；业务原因从关系行与程序代码读取。未绑定发送摘要的 snapshot 升级处理如上，不能把它与已发出的 document 混在一起。

只在离线升级中保留旧句式转换器：先用来源标记、关系、状态和错误代码确定归属，再用旧句子认模板。多层拼接拆掉副本/后缀，取末次实际失败原因；参数从关系行、冻结计数、预算和字节事实取，截断句不负责还原 ID/数值。已确定是黑板写、但因更早措辞或截断无法识别的原因，转 `history.reason-unavailable`，只带 recordId；不捏造参数，不存旧句作兼容。不能确定作者的值使整个升级失败，修转换器后再装；不猜为内容、不清掉它、不加历史分支。

安装仍沿 `install/upgrade.py` 已有离线锁、空闲/停止检查、验证过的 24 备份与失败恢复，所有转换在同一事务内，最后写 schema_version=25；FK/CHECK 或归属失败整体回滚。升级基线仍是样板的 0.42.0/90bd0148/schema 24，不重生成 25 样板、不消费尚未实际运行的升级。新建 25 与升级 25 比结构/外键/索引/CHECK；内容、原始证据摘要、身份、事件 seq、幂等与停止事实保全。转换日志只给计数，不输出内容。

## 4. 契约与文档

回复变化：selection-request/get/list 的 reason/error 与路由检查/边界；workflow 当前与审计请求的系统原因、路由引用、自动结论；capabilities/harness/accounts/catalog/buddy 的原因；inquiry/message 的程序原因；task-get/result/list、events-read、CLI 简略视图与快照中承载这些对象的字段。`waitReason`、limitations、NOTE、CANCELLATION_NOTE、remedy/guidance、分类与 marker 的 label 删除；缺 title/fixesTitle 给 null，marker 的当前拼接 summary 改为内容/原因事实，由读侧排版。真正的 Worker/Host/Lead/Router 内容及原始审计字段不改。

`protocol/contracts.py:BuddyControl` 现有方法仍是 `(request_json:str)->str`，本项不加方法，C-Two 方法签名不变；JSON 形状和执行进程回服务的原因事实变了，软件版本、CONTRACT_VERSION、contract_signatures 夹具随整批统一进 0.46.0，schema 25。签名夹具之外补具体载荷测试；不加旧进程/旧客户端兼容解码器，整批安装按现有停止与备份流程。

文档由 Host 在整合时修改：`docs/reference/reasons.md` 放经代码表校验的 Host 代码说明；`docs/reference/cli.md` 删 waitReason、重复说明并列出新请求/回复形状；`docs/reference/workflow.md` 说明系统请求、取消确认与停止证据；`docs/reference/workers.md` 说明 inquiry 的观察/问询边界及 LIMITS；`docs/reference/harnesses.md` 说明健康、资格与账户原因；`docs/reference/architecture.md` 说明原 capabilities.limitations 的能力边界，`docs/reference/console.md` 说明入口错误页与显示规则。共享 skill 链接 owning reference、删除重复返回说明的承诺；`CONTEXT.md` 由 Host 加原因/内容/诊断用词。本回合不改这些文件。

## 5. 控制台一侧（批次三）

原因词条集中进 `i18n/{zh,en}/reasons.ts`，一个 formatter；`en` 沿现有 `satisfies typeof zh` 保证键与中文一致。`buddies/harness.ts` 现有 reasonText、remedyText、账户 guidance 与候选 reasonCode 的表一起并入原因词条或按状态推导后删除；不保留旧服务文本回落。存储分类、事件名、缺省标题继续用各页面现有文案；waitReason 与固定限制不建立新词条。恢复未知/重新提交提示从 boundary.kind/retryAt 推出，系统请求只格式化一个原因与其事实列表，原内容不翻译。

全集测试直接比较 `protocol/reason-codes.json` 与 zh reasons 叶子集合，并给每个代码的参数做中英文格式化，断言非键名、无未展开插值；en 键一致交给类型检查，不另建清单。代码表只含参数名，测试对实际使用的参数提供样例，不做第二份运行时类型注册。沿语言切换测试验证已显示原因随 browser/zh/en 选择更新、内容不变；长参数与字段层次由组件测试和真实浏览器检查，不加 jsdom 像素断言。

入口失效只用主应用：`console/server.py:Handler._launch` 按原错误状态码返回主包 `index.html`，注入固定 meta 标记（如 name=hey-my-buddy-reason、content=console.entry-unavailable），不写票据、诊断或另一份译文。主应用启动先读 meta，在任何 API 调用之前绘制这个原因的标题/正文与重新打开命令；复用同一 i18n 与语言选择，不新增 Vite 入口。`Handler.do_GET` 在 `_trusted_request` 后、认证前允许 `/assets/*` 的静态构建资源，沿已有安全路径解析/CSP/MIME，不做资源清单白名单；`/api/*` 及其他页面仍按原认证流程。验证过期页不调用 API、无需有效会话仍加载资源、状态码不变、两语言均可读，且未经可信 loopback 的资源请求仍拒绝。

## 6. 分批与检查

先落最小代码表；之后按表纵向切分，表的 DDL、升级、生产者、读取与测试在同一微任务内。`upgrade.py`、共用 `workflow.py` 和注册表由各微任务依次修改，不并行覆盖；每次加存储字段就扩展同一个 24 → 25。路由关注随路由，工作区/Lead 与问询分开。下表为后续写入范围，不授权本回合写代码。

| 微任务 | 拥有的表与写入范围（源路径省略包根） | 检查与依赖 |
| --- | --- | --- |
| 0：最小原因接缝 | `protocol/{reasons.py,reason-codes.json}`、必要打包资源、对应 protocol 静态构造/资源测试；无 DDL | 表只列代码和参数名；无生产者运行时重复校验，无旧字段适配；登记后续实际会用的代码，不为删除项登记 |
| 1：健康与目录 | harness_health、buddies、catalog_observations、evaluation_catalog、catalog_current；`store/{db,upgrade}.py` 对应部分，`service/harness_health.py`、`catalog/`、`evaluation/native_observations.py`、`buddy/harnesses/{base,discovery,registry}` 与实际 adapter、`buddy/roles/router.py`；`decision.py:_buddy_input` 及相应测试 | 依赖 0；健康两套编号归一、catalog 保留/恢复比较、原生内容保全、处置文字删除；新模型输入按新原因构造、旧 document/hash 不变；同时覆盖 DDL 与升级 |
| 2a：系统请求 | workflow_requests 中工作区、继续准备、Lead 拒收与 helper 汇报的请求，`workflow.lead_result_refused` 事件；`store/{db,upgrade}.py` 对应部分，`tasks/workflow.py` 的系统请求写入与读取、`tasks/macro_tasks.py` 的等待片段、`cli/cli_views.py` 的请求投影及测试 | 依赖 0、1；一个共用的系统请求写入函数，各处经它写一个原因及其事实，Lead 不再伪造 turn；buddy 请求的 summary 仍是内容；代理照抄来源的形式；来源链保全；路由请求留给 3 |
| 2b：结论与停止说明 | workflow_host_conclusions、workflow_continuations 里黑板写的值、取消与停止事件里黑板写的说明；`store/{db,upgrade}.py` 对应部分，`tasks/{workflow,host_conclusions,command_receipts,workspace}.py` 及测试 | 依赖 2a；名下自动结论用 lead.owned-concluded，Host 的 note 与续接 reason 是内容，模板续接保全；黑板写的停止说明换代码，Host 写的保留；`_requeue` 的闲置参数、BUDDY_UNAVAILABLE 的 remedy、附着分支与 stash 的固定说明删除 |
| 3：路由与路由关注 | evaluation_decisions、decision_requests、router_requests、workflow_routes、workflow_requests 中的路由请求、attempts 中程序释放/中断原因及对应事件；`store/{db,upgrade}.py` 对应部分、`routing/`、`tasks/workflow.py` 的 routing/attention、`service/execution_scheduler.py` 与 `store/store.py` 的释放/中断原因接缝、对应测试 | 依赖 1、2a；决策唯一原因，粗细代码用途、条目 code、三处 dispatch 过大捕获、六种越界、释放原因、snapshot 清理、模板 commandId 与重放；路由请求经 2a 的共同写入函数只存 decisionId，按现有 decisionId 清副本（不解析决策），请求表的 CHECK 在这里加上这第三种形式；没有决策的路由关注给自己的原因 |
| 4：问询 | messages（含现有 delivery/answer）、相关事件；`store/{db,upgrade}.py` 对应部分、`tasks/inquiry.py`、`store/store.py` 的消息写入、必要执行进程到服务的载荷接收处、对应 inquiry/protocol 测试 | 依赖 0、3；已有 code 复用、状态可推导文字删除、程序与外部内容归属、消息来源、参数仅在执行进程到服务校验一次；NOTE 从返回移除 |
| 5：无新存储的出口与版本整合 | `tasks/{storage,macro_tasks,macro_task_summary_cache,command_receipts}.py`、`service/service.py`、`tasks/workflow.py` 与 CLI 的 waitReason/固定说明删除、`console/server.py`、实际 protocol 载荷接缝、签名/版本文件及测试 | 依赖 1–4；label/缺标题、默认说明、Host 简略/完整同契约、入口 meta/静态资源授权；版本随批次二统一到 0.46.0，宏任务删除操作的新增签名由 Host 同批整合 |
| 6：控制台（批次三） | `apps/console/` 的 API/类型/视图/i18n/fixtures/browser 测试与主包构建；`console/assets/` | 依赖 5；收编现有按代码译文、全集/切语言/长参数、入口失效零 API 调用与安全边界；最终源上两个前端命令通过，不留旧字符串解析 |

2026-10-18，Host 给第 2 项写说明之前清点了读写点，改成上表：原来的第 2 项拆成 2a 与 2b，各自可以单独审；路由请求的形式移到第 3 项，因为路由关注的原因不总是决策的原因。派发前校验 buddy 失败时（`workflow.py:prepare_dispatch`），Host 指定的 buddy 根本没有决策；路由选出的 buddy 有决策，但失败的是校验，不是决策。这些要和决策的原因一起设计。控制台的任务详情要求请求的 summary 与结论的 note 是字符串（`apps/console/src/task-detail/parse.ts`），批次二改了它们之后，0.46.0 只能和批次三的控制台一起装。

每个 Python 微任务用 `uv run --frozen python -m hey_my_buddy.cli.checks <对应 tests/python 文件或目录>`：1 覆盖 harness/catalog/health/store 升级，2 覆盖 tasks/workflow/结论/CLI/store 升级，3 覆盖 routing/释放/路由关注/store 升级，4 覆盖 inquiry/store/protocol，5 覆盖 console/tasks/CLI/protocol。源码中不存在的候选测试路径由实施者在相同 owning 测试目录新增，不直接起测试进程。共享文件串行落地，Host 最后跑完整 Python 检查；控制台跑 `npm --prefix apps/console test` 与 `npm --prefix apps/console run test:browser`。实现验证不在本计划回合运行。

增加三类有实际用途的检查：一是服务源码守卫，在 `src/hey_my_buddy` 的 Python 服务/协议/harness 源码中排除注释及 `buddy/roles/texts/` 后不留中文；SQL 注释也按注释处理，`console/assets/` 的前端构建译文不算服务源码，英文诊断不受该守卫限制。升级转换器必须匹配的旧中文句子照原文写在 `blackboard/store/upgrade.py` 里，守卫明确豁免这一个文件并写明理由：这些是旧输出的样子，不是服务的输出；这一次升级被消耗之后，转换器连同这些句子一起删除，豁免随之取消。不用 Unicode 转义绕过守卫（Host 2026-10-17 审第 2 版时改）。二是静态检查源码构造/有限映射的每个原因代码均已登记，动态代码只从已声明的有限外部代码映射进入，不用“动态”免检。三是升级用例覆盖多层拼接、截断模板（关系行取参数）、模板 reason 已被 Host 作为内容提交的续接、归属失败回滚、明确黑板但不可还原转 history、决策副本删除、原始文档/内容/指纹与命令重放保全；样板升级后与新建 25 同构。

Host 在日常规模的私有 24 副本上演练升级，先用现有样板/复制流程做身份与路径重绑定，不能让副本指到日常工作区或原生会话。记录总耗时、事务开始到提交耗时、每张受影响表的前后行数、转换数/history 数、FK/完整性结果与内容/摘要保全；出现归属不明即失败，修转换器重演，不带未知分支安装。Host 在 `docs/acceptance/` 记录实测；本计划不虚构计数或时长，文档更新也由 Host 负责。

本回合只运行 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/cli/test_repository_hygiene.py`，清除 AGENTS 指定的继承状态/运行时/credential/虚拟环境变量并令 uv 离线。无日常看板、原生探针、联网或模型调用，不改写入范围之外的文件。

## 7. 核对差异与待开发者定的事

与审查文字不符的两点：`router_boundary_data.py:_CHANGED_CODES` 当前没有 router-unavailable，它列 router-input-changed、router-out-of-bounds、router-dispatch-unverified、workflow-routing-fenced、ACCOUNT_BINDING_CHANGED、BUDDY_CONFLICT、router-input-too-large；`router_failover.py:classify_outcome` 只检查 code 的类型，分类用 changed/cancelled/answer_valid/shutdown_confirmed 等事实，不按 router-unavailable 值分类。因此 inspection/problem 的粗 router-unavailable 可以一次改为细代码，边界 kind 不变；真正仍被逻辑消费的 router-out-of-bounds 保留为原因 code，用参数细分，不新增分类列。另一个是 `child_settled` 确有 `_requeue(reason="authorized helpers settled")`，但 `_requeue` 函数体完全没有读 reason，后面的 workflow.auto_continued 事件也没有 reason；因此删除无用参数，不新增原因或迁移。这些是源码事实，不是推断。

其余补正已核对相符：释放/中断说明进入决策，路由副本常存错误代码，dispatch 过大三处写入，越界六值，BUDDY_UNAVAILABLE.details 带目录原因/处置，buddies 不可用说明进入冻结模型输入。本回合未读日常看板，归属是否全部可证、实际转换计数和演练耗时尚未知，留给上述副本演练；不据此预加历史分支。待开发者定的事：没有。
