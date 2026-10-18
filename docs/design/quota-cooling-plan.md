# 额度改为冷却与透明改派：执行计划

本计划落实 [ADR-021 第 22 条的 2026-10-18 修改](../decisions/021-router-buddy-planes-and-routing-evidence.md)、[ADR-019 第 6 条的修改](../decisions/019-worker-accounts-and-usage.md)与 [ADR-018 第 23 条的修改](../decisions/018-routing-modes-and-host-workflow.md)。依据是[冷却与改派调研](../research/2026-10-18-quota-cooling-and-failover.md)；拒绝的分类仍按[拒绝种类调研](../research/2026-10-11-refusal-kinds-and-zhipu-funding.md)与现有 harness 解析。本文只规划后续实现，未改变产品行为。

核对基线为输入提交 `b8f88a66`。下文的 `src/hey_my_buddy/` 简写为 `S/`，`tests/python/` 简写为 `T/`；文件后的数字是该基线的源码行号，多组数字表示同一文件的多个落点。历史提交的行号另写提交号。实现开始后应按符号重定位，不能把本文的行号当作固定接口。现有源码版本与契约都是 0.46.0（`pyproject.toml:3`、`S/protocol/contracts.py:17`），schema 是 25（`S/blackboard/store/db.py:22`）；尚未安装的状态见 `README.md:39`，不从本次 checkout 推断日常服务状态。

## 完成后的行为

自动路由只按真实拒绝建立冷却，不保存或显示额度窗口、百分比与余额。一个 harness 的一个提供方因额度用尽或余额不足拒绝请求后，共享这个提供方的模型与推理强度一起冷却；冷却结束后的下一个正常请求就是试探。模型开始前的拒绝只留下执行与路由证据，同一个微任务沿同一次决策继续选择其他候选；模型已经开始或无法证明尚未开始时仍把执行中断交给 Host。认证失败与短暂限流沿现有分类处理。只有冷却挡住全部本来合法的候选时，才以专门的路由边界告诉 Host 每个提供方何时可以再试。

冷却采用固定的 30 分钟、连续拒绝次数翻倍、最长 24 小时；拒绝本身给出的明确恢复时刻可以缩短这次冷却。一次成功的真实模型回合清零；读健康、读目录、读原生账户元数据与显示 credits 都不能清零。`quota-redetect` 立即解除指定 harness／提供方的当前冷却，不查询余额、不调用模型，也不宣称额度恢复。计费标签、实际 token 用量、实际费用与“正在使用 credits”的事实各有含义，保留；不建立设置项。

## 现状清点与处置

下面的清点覆盖源码中的额度读写与投影、各 harness 的解析、命令入口、控制台、相应检查及当前参考文档。表里的“改写”都要求同批删除被替代的代码与断言；不保留旧字段、别名、兼容解析或失败后改走旧机制的分支。普通 token 用量、并发配额、Router 预算与 Router 无回答的重试间隔不属于额度读数，相关保留项也列出以免误删。

### 服务、持久化、路由与协议

| 文件与行号 | 今天的职责与后续处置 |
| --- | --- |
| `S/blackboard/store/db.py:943–965` | `quota_current` 按 harness／provider／limit 存 blocked、原生恢复时刻与单次重试标记；`harness_quota_observations` 存整份读数 JSON。删除这两张表，改为下文的提供方冷却表与 credits 状态表。 |
| `S/blackboard/routing/quota_routing.py:25–58,122–155` | `evidence` 从窗口、ordinaryUsageAllowed、balanceZero 等得出 blocked／available，`record` 覆盖现状；100% 窗口参与推导恢复时刻。删除窗口及 available 观测恢复逻辑，改成只处理真实拒绝／真实成功／手动解除的冷却规则。源码第 41、51 行会用满窗口推导 reset；不能沿用 `docs/reference/harnesses.md:51` 中“100% 完全不影响排除”的笼统表述。 |
| `S/blackboard/routing/quota_routing.py:20–22,69–114,158–225,228–274` | 一小时等待、retry marker、claim、redetect、quotaRouting 与 buddy_retry。删除单次机会的打开／消费／归属；改为按 cooling_until 过滤、按提供方投影冷却与直接解除冷却。不得把旧 claim 改名后继续消费机会。 |
| `S/blackboard/evaluation/native_observations.py:24–40,43–63,66–108` | 保存最新读数、从 quotaFailure 合成读数、stale 与 90% 提醒。删除 latest_quota、record_quota、quota_view、warnings；persist 保留执行归属与 token 写入，改为记录冷却及 credits，成功清零必须不再依赖有没有 quota/token 字段。 |
| `S/blackboard/evaluation/native_observations.py:112–121` | `failure_view` 从结构化失败或 quota-rejected 显示额度原因。保留分类用途，统一到一个新失败事实格式，删除仅凭旧顶层 code 补造事实的分支。 |
| `S/blackboard/service/harness_health.py:64–82,371–427` | 读健康时附 quota／quotaRouting，Codex 账户读取时保存 quota 并发 account_observed。改投影冷却与 credits；账户刷新只更新计费与 credits，删除 quotaRecorded，不能使冷却恢复。账户读取限频与停进程证据保留。 |
| `S/blackboard/service/service.py:577–602` | quota_redetect 校验 harness/provider/requestId，在一个事务保存结果、事件与回执。保留入口及幂等性，改成下文的解除回执与事件；所有有恢复时刻和无恢复时刻的冷却都可解除。 |
| `S/blackboard/store/store.py:2001,2126,2243–2366` | execution_result 经 fencing、回执校验后提交：错误变 task.failed，persist 写额度，settle_turn 再把微任务变失败。改在终态／事件和 settle_turn 之前判定透明改派，不能先发布 task.failed 再撤销。回执、冷却、attempt 结果及下一路由动作必须同事务提交。 |
| `S/blackboard/store/store.py:553–563` | task 读取附 quotaFailure。保留真实额度／限流失败分类；开始前拒绝的 attempt 仍可审计，但微任务的最终结果不能沿用前一个拒绝作为失败。 |
| `S/blackboard/tasks/workflow.py:728–758,790–791,836` | 微任务视图带 quotaWarnings 与 quotaFailure。删除 quotaWarnings，增加明确选择冷却提供方时的结构化选择提示；quotaFailure 保留真实失败含义。 |
| `S/blackboard/catalog/catalog_store.py:285–291`、`S/blackboard/evaluation/evaluation.py:63–69` | Buddy 列表、evaluation 快照附 billing、quotaExhausted、quotaRetry。保留 billing，删除后两个旧字段，换成提供方冷却与 credits。 |
| `S/blackboard/routing/decision.py:181–232,269–281,435–506,1349–1413` | 候选扫描、routingBasis、发布边界与最终 adoption 使用旧 exhaustion／claim／quotaRetryBuddyIds。改为统一的提供方冷却检查，删除 quotaRetryBuddyIds 及 quota-retry-consumed；发布与执行 admission 都要重新检查冷却，但读取不写状态。 |
| `S/blackboard/routing/decision.py:1123–1125,1170–1233,776` | 已有一次 selectionRetry 与重选，内部 Router 请求键只有 0／1。复用并扩展为同一 decision 下多次额度改派，保留原来“非额度的边界变化最多重选一次”的语义；详见后文。 |
| `S/blackboard/routing/router.py:145–172,322–380` | Router 的资格检查也排除额度耗尽；列表按现有顺序前进。改查同一冷却表，冷却中的 Router 跳过并显示 nextTryAt，不增加第二套额度熔断。 |
| `S/blackboard/routing/router_boundary.py:9–82`、`router_boundary_data.py:69–92,101–118,347,351–382,416–434` | 目前边界只有三种，quota 恢复时间来自 blocking_records；重选只识别 selectionRetry。扩展冷却边界与提供方列表，删旧 reset／单次窗口推导；保留现有 continue／reroute 模板、停止证据与权限判断。 |
| `S/blackboard/routing/router_sequence.py:55–75,97–121`、`router_history.py:80–115` | 冻结请求与每次 dispatch；历史键含 selectionRetry 布尔。复用每次 dispatch 的不可变文档与身份，用明确 selectionRound 替代布尔作为轮次，升级同时转换旧副本；不覆盖以前的 dispatch。 |
| `S/blackboard/tasks/workflow.py:1395–1487,3209–3283,7040–7141` | _start_routing 新建一次路由，routing_settled 采纳推荐，_requeue 续做，settle_turn 将普通失败写入 run。改派复用采纳、排队和关闭旧 turn 的内部机制；不调用会新建 decision 的 _start_routing，不冒充 Host continue，不增加 continuation_count。 |
| `S/blackboard/tasks/scheduling.py:7`、`S/blackboard/store/store.py:798,1634` | “quota”也指模型服务并发 admission；保留并发容量与 attempt 冻结身份。只调整其中实际涉及提供方冷却的 admission，不把并发限制删掉。 |
| `S/blackboard/tasks/macro_task_deletion.py:480–481` | 删除宏任务时清 quota_current.retry_consumed_by 对决策的关联。随单次重试表一起删除；新冷却归提供方，不随删除一个宏任务清空。 |
| `S/blackboard/store/upgrade.py:17,69–73,125–129,310–348,816,995–1098,1219–1249` | 当前唯一 24 → 25 升级已经转换健康、catalog、decision、事件及其副本。扩展同一个升级，删额度读数及旧重试副本，加入新表与 selectionRound 转换；删除已无消费者的额度提醒原因转换。不能加 25 → 26 或在普通读取时转旧数据。 |
| `S/blackboard/tasks/command_receipts.py:21–34,66–71`、`S/blackboard/store/store.py:376–398` | 小回执原样存，大部分 workflow 回执只存身份并重建。保留幂等性；升级要按 command kind 处理 quota.redetect 的旧小回执，不能假设所有回执都含 quotaWarnings 或全删 commands。 |
| `S/protocol/usage.py:40–41,272–368,370–414,496–504` | quota/window normalization 与导出、拒绝 code 分类、quotaFailure normalization。删除 quota 版本、窗口及相关导出，保留拒绝码集合和分类；新事实只规范拒绝阶段、原生拒绝恢复时刻与 credits 使用状态。token normalization 保留。 |
| `S/protocol/reason-codes.json:4–7,215` | 旧余额、用尽、近限提醒与 Router quota 原因。删读数提醒码，新增冷却的原因码；保留原生错误码作为 nativeCode，不借余额读数发 HARNESS_BALANCE_ZERO。 |
| `S/protocol/contracts.py:17,46,208`、`S/protocol/transport.py:46` | C-Two 契约版本与 quota_redetect 方法／CLI 映射。保持 0.46.0 和方法名，JSON 回复一次切换新格式；执行结果仍走已有 execution_result，不增专用改派 RPC。 |
| `S/blackboard/evaluation/user_policy.py:210` | 发布 Router 身份不因暂时 quota／健康阻止保存。保留原则，措辞改为冷却；偏好、固定与排除不被自动改写。 |

### harness、执行进程与 CLI

| 文件与行号 | 今天的职责与后续处置 |
| --- | --- |
| `S/buddy/harnesses/codex/protocol.py:218–328,355–356,385–394` | 原生快照读 primary／secondary、百分比、reset、credits 余额、ordinaryUsageAllowed 与多 limit 合并。删读数及桶恢复算法；原生账户接口／滚动事件只输出经核实的 credits 状态。`turn_failure_code:331–335` 的结构化拒绝提取保留。 |
| `S/buddy/harnesses/codex/account_probe.py:18–49` | 无模型 account/read、config/read、account/rateLimits/read，返回 billing/quota/error。保留无模型查询与原生配置归属，第二个返回值改成 credits；不再把窗口、余额或 available 写进服务。 |
| `S/buddy/harnesses/codex/native_run.py:1096–1109,1299–1353,1390,1768–1771,1856–1868` | Worker 读取 quota，native turn error 分类，quota-snapshot 引用及 native_failure 通过共同结果传出。保留拒绝分类，删 quota-snapshot 及读数读取；同一原生接口只为 credits 使用状态服务。两个 carrier 都补拒绝阶段，不能只改 Worker。 |
| `S/buddy/harnesses/claude/protocol.py:36–38,65–69,277–319,326–332,393–425,639–688` | quota 事件与 result 拒绝、utilization／unifiedWindows 解析与 quota_candidate。保留结构化拒绝及其原生恢复时刻；删窗口、百分比与 allowed_warning 提醒投影，改只取额外用量状态。 |
| `S/buddy/harnesses/claude/native_run.py:769–770,825–878,950–955,1061–1075,1192–1198` | 两种拒绝来源汇入 QuotaRejected，observations 仍带 quota／rateLimitObservations；注册表声明原生格式。删除 utilization、窗口与旧专用 quotaFailure 形状，转成共同失败／credits 事实，保留原生诊断中与额度无关的内容。 |
| `S/buddy/harnesses/zcode/protocol.py:43–45,302–369`、`zcode/native_run.py:922–928,1142–1152,1667–1682` | turn.failed 的结构化 attribution／turnPhase，非 retryable 的 rate_limited 才归额度用尽；native_run 保留失败并输出 quotaFailure。保留分类及原始代码，改把阶段证据带到服务；不根据 1310 的消息文字猜时区或恢复时间。源码已共用无工具／Worker 的失败路径，不能把 10-11 调研里的历史缺陷当成当前结论。 |
| `S/buddy/harnesses/dsh/session_records.py:239–250`、`native_run.py:899–900,1245–1267,1626–1651` | session record 的 turn/end 只取结构化 error.code，ACP 活动／stop reason 与该 failure.code 进入共同结果；没有主动余额读法。保留分类与 token 用量，补本次根回合的拒绝阶段；没有原生 credits 来源时给未知，不新增厂商 HTTP 请求。 |
| `S/buddy/harnesses/run_contract.py:413,591–628` | RunResult 的 native_failure 规范化、model_started 可空、原生 stop 与证据包。增加可验证的拒绝阶段及 credits 事实，保留停止证据、根身份和 nativeCode；不得把 model_started 的 None 当成尚未开始。 |
| `S/buddy/roles/run_execution.py:128–150,243–258,287–302,721–724` | 角色层将共同结果投影成回执，读 quota-snapshot 和 Claude observations，二次规范 quota。删所有旧 quota 搬运，改为同一规范的新事实，删除 `model_started is True` 把未知压成 false 的危险推断用途。 |
| `S/buddy/harnesses/registry.py:105`、`S/buddy/roles/schema_worker.py:64` | Claude native_quota_failure 特例控制旧失败块搬运。共同失败格式落定后删除这个额度特例，不保留两种读法；其他 carrier 能力声明保留。 |
| `S/buddy/runtime/execution.py:626–633,656–665,706`、`S/buddy/runtime/receipts.py:21–56`、`S/protocol/client.py:103–111`、`S/blackboard/service/service.py:884–885` | 执行结果形成 durable receipt，经 execution_result 送服务并可重放。保留整条交付路径；本进程不自行挑下一模型。626 行的本机 pre-model 协议重试是另一种故障，不能拿它作为额度拒绝的重试路径。 |
| `S/buddy/roles/router.py:161–162`、`run_observers.py:96` | readonly-budget-exhausted 是 Router 执行预算，并非账户额度。保留，不让它触发提供方冷却。 |
| `S/cli/main.py:41`、`S/cli/cli_help.py:45,116`、`S/cli/cli_views.py:221–222,452–453`、`S/cli/blocking.py:299` | 保留 quota-redetect 命令、requestId 与真实 quotaFailure；删 brief 里的 quotaWarnings，帮助从“开放单次机会”改成“立即解除冷却”，brief 同样保留冷却边界和明确选择提示。 |

### 控制台、预览与构建产物

| 文件与行号 | 今天的职责与后续处置 |
| --- | --- |
| `S/console/read_cache.py:58–65,95–123`、`S/console/reads.py:8–14` | 缓存按 eligibleAt／resetsAt、quota stale 期限失效。删除额度读数专属 staleness 计时与窗口 key，加入 coolingUntil／nextTryAt；保留 expiresAt、scanAfter、Router skip/retry 等其他时限，冷却自然到期必须使只读快照更新。 |
| `S/console/server.py:95` | quota_redetect 在控制台白名单。保留会话写权限与 CSRF 校验，不开放 Host 委派入口。 |
| `apps/console/src/types.ts:36–39,80–133,194–197`、`api.ts:354–394,433–442` | quota、QuotaRetryWindow、QuotaRoutingRecord、quotaExhausted／quotaRetry 类型与解析。删除旧类型／解析，严格读取冷却和 credits，真实 quotaFailure 保留；不兼容接收旧 quota。 |
| `apps/console/src/quota.ts:1–110` | parseQuota、quotaView、90% 提醒和过期显示。删除整个旧模块及专属测试，由提供方状态的小型展示函数承担新视图，不重建窗口工具库。 |
| `apps/console/src/buddies/harness.ts:42–68,74–198` | 最近上限、窗口行、exhausted、retryProviders 与恢复文案。改为每提供方冷却／credits 行及可解除的提供方；不保留百分比排序或“机会已消费”。 |
| `apps/console/src/buddies/HarnessHeader.tsx:28–47`、`HarnessDetails.tsx:97–156` | 标题的余额／额度栏、reset 与详情 QuotaBlock。改为下文双语状态；冷却不能把整个 harness 标成不可用，其他提供方仍可用。 |
| `apps/console/src/buddies/CatalogView.tsx:74,238` | 任一 quotaRouting.blocked 把 harness 行整体标 unavailable，并接 retry 动作。改成提供方状态汇总与解除动作；保持目录选择／展开交互。 |
| `apps/console/src/buddies/attention.ts:2–25`、`BuddyHeader.tsx:16`、`tokens.css:5` | 近限读数成为页签提醒圆点。删除额度近限提醒来源；普通冷却与 credits 是状态，无须用户操作，不点亮“待处理”徽标；真正需要 Host 的全冷却边界由任务入口呈现，已有 Router 配置错误提醒保留。 |
| `apps/console/src/buddies/harness-retry.ts:7–50`、`use-harness-actions.ts:64` | 记住 requestId、读 opened／alreadyOpen 并给单次重试成功提示。保留请求身份与未知回复恢复，改成 clearProviderCooling 与新回复；旧名字、storage key 语义与 opened 解析一起替换。 |
| `apps/console/src/task-detail/parse.ts:436–451`、`facts.tsx:19–23`、`types.ts:194–197` | 真实 quotaFailure 作为一次执行结束原因。保留中途额度／限流失败；开始前拒绝归路由历史，不给最终成功微任务贴失败标签。新边界和路由改派记录也通过 task-detail／shell 的既有解析入口接入。 |
| `apps/console/src/i18n/{zh,en}/buddies-harness.ts:80`、`buddies.ts:16`、`console.ts:78–80（zh）,80–82（en）`、`reasons.ts:4–9,133（zh）,135（en）`、`shell.ts:48–49（zh）`、`taskDetail.ts:58（zh）,59（en）` | 改写 quota／余额／reset／近限／retry 词条，删除旧 reason 参数；保留实际额度失败翻译，并补新冷却原因与 credits 三态。英文具体偏移以对应 key 重定位。 |
| `tests/probes/macro_task_console_preview.py:59,152–153,180,1752,1796–1844,2823–2827,2848–2870,3035–3067` | 合成 quota 读数、零余额、过期／未知窗口、重试及坏 quota 样本。改成冷却、到期、解除、credits、开始前改派成功与中途失败样本，删除旧样本及其清单项；保留中途失败的部分成果案例。该脚本虽在 probes 下，纯合成构建用法不等于真实 harness probe。 |
| `S/console/assets/assets/index-DCU-hBe3.js:1` 及同目录入口、样式 | 已打包旧 UI。控制台批次经仓库既有 build 重建整套产物，删除旧哈希文件；不手改压缩 JS，不把源码改动视为已更新安装包。 |

### 测试落点

以下是直接包含额度语义或旧字段的检查清单。“改写”表示只替换相关分支，文件中无关测试保留；“保留”也要更新受类型／fixture 改动牵连的输入，不把旧语义换个文件名留下。行号是代表落点，所属 fixture 同列给出。

| 文件与行号 | 处置 |
| --- | --- |
| `T/protocol/test_usage.py:121–265`、`test_billing_quota.py:25–259` | 改写：删除 quota/window 规范化与余额恢复断言，覆盖新的拒绝／credits 规范与 Codex 无模型账户查询；计费与 token 测试保留。 |
| `T/blackboard/evaluation/test_native_observations.py:23–254`、`test_other_native_observations.py:147–570` | 改写：删除 90%、stale、窗口与 available 恢复；检验真实拒绝计数、成功清零、晚到结果、provider 归属及重放。 |
| `T/blackboard/routing/test_quota_retry.py:38–384` | 删除旧单次窗口测试，替换为 `test_provider_cooling.py` 的固定时钟、指数冷却、24 小时上限、原生时刻上限、解除与幂等测试。 |
| `T/blackboard/routing/test_nonretryable_quota.py:19–113`、`test_current_router.py:173–184,291–294`、`test_router.py:123–187`、`test_routing_health.py:86–211`、`test_routing_modes.py:99` | 改写冷却过滤、Router nextTryAt、已知／未知恢复与真实分类；保留非 retryable 限流归 quota-exceeded。 |
| `T/blackboard/routing/test_selection_publication.py:34–85`、`test_single_router_claims.py:51–186`、`test_single_router_requests.py:210–212`、`test_decision.py:606–619` | 改写：删机会消费，验证冷却在 adoption／实际执行 admission 的边界检查与新轮次身份。 |
| `T/blackboard/routing/test_router_boundary_integration.py:205–229,646`、`test_router_failover_integration.py:154,188,326–328,527–661`、`test_router_failover.py:17–22`、`test_router_list_health.py:170–240`、`test_router_list_configuration.py:4` | 改写额度条件及边界，保留 Router 列表、停止／取消、健康历史与非额度失败规则；覆盖多个提供方连续拒绝与 Router 自身冷却。 |
| `T/blackboard/tasks/test_workflow.py:300–313`、`test_host_workflow_worker.py:27–185`、`test_model_service_concurrency.py:43,310–324`、`test_scheduler_lanes.py:39` | 改写 quotaWarnings／旧额度 fixture，加入同一微任务同一 decision 的改派；并发 admission、lane 与 Host 身份语义保留。 |
| `T/blackboard/tasks/test_macro_task_deletion.py:635,675,715–717` | 删除 retry_consumed_by 清理断言；验证删除宏任务不清另一任务共享的提供方冷却。 |
| `T/blackboard/store/test_store.py:149`、`test_upgrade_fixture.py:1019,1040,1851`、`T/install/test_upgrade_service.py:31` | 改写字段断言与升级预期；在升级 fixture 测试中加入真实旧写入形状，验证删表、JSON 副本、回执与 schema 一致性，不重新生成升级基线。 |
| `T/blackboard/service/test_harness_health.py:961–1024`、`test_console_reads.py:80–96`、`test_service_environment.py:110–119`、`T/console/test_console_macro_task_cache.py:1` | 改写账户事实与快照；缓存文件当前没有 quota 专属字样，但需新增冷却期限失效断言；服务环境隔离与无模型检查保留。 |
| `T/buddy/harnesses/test_quota_failures.py:13–188`、`test_adapter_usage.py:72–251` | 保留四家结构化拒绝分类表，删除读数与窗口部分，新增开始前／中途／未知、credits 与 carrier 一致性。 |
| `T/buddy/harnesses/codex/test_native_run.py:163,934,1085–1099`、`test_codex.py:557–568`、`test_codex_tool_evidence.py:522` | 改写 quota 引用与阶段；保留 native error、工具及停止证据；新增没有模型输出的拒绝与已有输出的拒绝。 |
| `T/buddy/harnesses/claude/test_claude_protocol.py:47–163`、`test_claude.py:254–440`、`test_claude_worker.py:86–98`、`test_claude_tool_evidence.py:584–588`、`test_native_run.py:583–585,845–853,1365` | 删除 utilization／window 提醒断言；保留拒绝及停止，加入额外用量状态和原生结果阶段，更新旧 Claude 专用失败格式。 |
| `T/buddy/harnesses/zcode/test_zcode_protocol.py:619–686`、`test_zcode.py:293–303`、`test_zcode_tool_evidence.py:88,158–183`、`test_native_config.py:239–245`、`T/buddy/harnesses/dsh/test_native_run.py:287–297`、`test_session_records.py:155,276` | 保留 attribution、非 retryable 与原生配置分类；改写共同结果，增加阶段证据，不凭余额或消息猜分类；DSH 的记录折叠要证明只取本次根回合，不能沿用前一回合的 failure。 |
| `T/buddy/harnesses/test_run_contract.py:83`、`test_controller.py:42,308`、`test_c_two_live.py:495`、`test_session_receipts.py:264`、`test_discovery_stop_timing.py:266`、`T/buddy/roles/test_registered_review.py:72–87`、`test_run_controller_discovery.py:115` | 改写额度形状，保留结果、交付、只读能力与 discovery 的停止边界。 |
| `T/blackboard/catalog/test_catalog_observations.py:98,140`、`test_native_discovery_stop.py:79` | 改写复制的 quota／quotaRouting；保留 snapshot 归属与 discovery 停止事实。 |
| `T/cli/test_blocking.py:333`、`test_host_preview.py:194–246`、`test_cli_views.py:1`、`T/console/test_console_preview.py:1` | 改写失败／quota fixture；为 brief 增加旧字段消失与新边界不被裁掉的断言，更新预览真实解析。 |
| `T/buddy/harnesses/{codex,claude,dsh,zcode}/fixtures/native-usage-*.json`（分别 `codex:73–122`、`claude:113–137`、`dsh:116–123`、`zcode:165–176`）及 `fixtures/run_contract/codex-payloads.json:23–44` | 删除本项目保存的 quota 读数预期与旧结果字段；保留 token 原生样本、拒绝码与必要的最小原生 credits 证据，不保留整份余额快照作产品事实。 |
| `T/buddy/harnesses/codex/fixtures/mock_quota_codex.py:13–15`、`mock_codex.py:51–87,486–488`、`claude/fixtures/fake_claude.py:136–275`、`fake_claude_usage.py:104–109`、`dsh/acp/fake_agent.py:795–798,1074`、`dsh/fixtures/workflow_agent.py:7–26,225`、`zcode/fixtures/mock_zcode.py:502–509`、`mock_zcode_usage.py:118–120`、`T/protocol/fixtures/mock_readonly.py:152` | 改写对应假原生事件与测试输入，删除仅服务旧窗口的模式，保留真实分类、停止、token 与 transport 场景。 |
| `apps/console/src/quota.test.ts:4–72` | 随 quota.ts 删除；新状态测试不复刻百分比算法。 |
| `apps/console/src/buddies/harness-quota.test.tsx:37–319`、`harness-wire.test.ts:115–134`、`harness.test.ts:73–230`、`attention.test.ts:19–59` | 改写状态、严格解析、解除动作与徽标；删除近限、单次机会、旧坏 quota 被忽略的断言。 |
| `apps/console/src/buddies/catalogue-actions.test.tsx:159–160,384,411`、`catalogue-gestures.test.tsx:37–57`、`english.test.tsx:49–54,105`、`navigation-page.test.tsx:58–71,189`、`roles-view.test.tsx:710–730`、`roles.test.ts:219`、`harness.fixtures.ts:56`、`harness.testing.ts:1` | 改写旧状态输入，保留单击、选择、权限、刷新、未保存设置与 Router 行为；新增多提供方并排及解除后选择不跳动。 |
| `apps/console/src/api.test.ts:103`、`preview-contract.test.ts:137–139,258–302`、`i18n/language.test.ts:60`、`unavailable-buddies.test.tsx:200`、`task-detail/parse.test.ts:284–289`、`TaskDetail.test.tsx:443` | 改写预览与旧 wire 字段；保留真实中途额度失败及部分成果，增加新边界中英文与透明改派后的成功。 |
| `apps/console/src/buddies/ConcurrencyNotice.test.tsx:18` | 保留并发会增加消耗的说明；它不是额度读数或近限提醒。 |
| `apps/console/browser/catalogue.check.ts:13–17,52,72–73`、`catalogue-gestures.check.ts:83`、`task-detail.check.ts:295` | 改写旧徽标／quota-summary 断言；验证状态可读、窄屏不重叠、整行点击、全冷却边界与中途部分成果。 |

### 参考文档与分发入口

| 文件与行号 | 处置 |
| --- | --- |
| `docs/reference/harnesses.md:3,31–59` | 改写为冷却、credits、成功清零与手动解除的唯一运行契约，删除读数 freshness、单次窗口和旧回复；保留计费标签与无模型查询的归属。 |
| `docs/reference/decision.md:67,71,87–91`、`evaluation.md:3,32,54,67,73,79,121` | 改写候选／Router 冷却、额度改派轮次、边界、列表事实与新 replies；Router 无回答的重试设置保留，不混作冷却设置。 |
| `docs/reference/workers.md:64–66,142–146`、`claude-code.md:21,27`、`codex.md:43`、`workflow.md:152` | 删除“所有额度失败都绝不自动重试”与读数搬运描述，写明开始前改派／中途 Host 分界、credits 来源及验证边界；历史 native probe 证据保留。DSH、ZCode 的参考页本次检索无额度专段，在 owning 运行参考补阶段能力与未知项。 |
| `docs/reference/console.md:25,49,65–69,87–89`、`cli.md:118`、`operations.md:269–273` | 改写展示、徽标、解除命令、CLI brief 与全冷却处理；`console.md:53` 的并发消耗说明保留。 |
| `docs/reference/architecture.md:53–54,92,150,169,185,203,209`、`harness-integration.md:23–27,79,93,147` | 改写持久化、事实传递、共用运行路径与测试清单；保留 token、运行所有权及证据原则。 |
| `skills/hey-my-buddy/SKILL.md:40`、`docs/README.md:91`、`README.md:39`、`README.zh-CN.md:39,123` | owning 参考更新后同步入口：“开放一次重试”和显示余额改成冷却／解除；README 中英文保持对等，历史安装版本与历史验收不重写成新能力已安装。 |
| `docs/reference/usage.md:181` | 保留：不能为了省额度擅自取消用户要求的调度，与读数机制无关。 |
| 三份 ADR 的旧条款与两份调研 | 保留决策与调查历史，当前行为由同日修改及 owning 参考说明；不删除历史原生版本与未验证范围来营造新能力已验证。 |

## 从真实拒绝到透明改派

当前传递链是 harness 的 `RunResult.native_failure` → `S/buddy/roles/run_execution.py:243–258` 的 `quotaFailure`（Claude 另经 observations）→ `S/buddy/runtime/execution.py:656–706` 的报告与 durable receipt → `S/buddy/runtime/receipts.py:25–56` → `S/protocol/client.py:103–111` 的 `execution_result` → `S/blackboard/service/service.py:884–885` → store 的结果事务。store 在 `2243–2264` 把错误定为 failed，`2284–2301` 写额度与 task.failed，`2353–2360` 调 settle_turn；`workflow.py:7079–7141` 把 turn 和 run 标 failed。Host 随后读到失败微任务，而非可继续的路由。这是本计划要改的服务分界，不能在 CLI 收到失败后悄悄再 submit。

今天四家的拒绝分类已经存在，但没有一个可直接共用的“模型开始前拒绝”判定。Codex 在 `native_run.py:1299,1390` 发输入前就把 model_started 设为 true；ZCode 在 `native_run.py:922` 的 session/send 前这样做；Claude 的 `collector.user_sent` 在 `native_run.py:514` 记录已送输入，`1061` 把它当 model_started；DSH 的 `native_run.py:899–900,1262–1263` 则依据模型活动与 end_turn。共同结果允许未知（`run_contract.py:615`），角色投影却在 `run_execution.py:138` 把非 true 压成 false。因此不能用旧 modelStarted=false、零 token、无工具或进程已退出单独证明开始前拒绝，也不能把 session/send 成功当成模型已产出。

后续在各家现有 collector 内建立根回合的拒绝阶段事实，统一为 `rejectionPhase: before-model | during-model | unknown`。`before-model` 必须同时有绑定本次根回合的明确额度拒绝、完整覆盖到拒绝的原生事件证据，以及从输入 admission 到拒绝没有模型内容、推理、工具活动或已消费模型 token 的证据；原生若有明确拒绝阶段，保留它的来源。任一模型活动出现即为 during-model；事件覆盖不完整或原生无法确定阶段则为 unknown。这是解析证据的共同约定，保留各家原生事件、根身份、序号与内容的差异，不新增事后扫描日志猜测的通道。

Codex 复用 TurnEvidence 的根 thread／turn 事件和 turn.error；Claude 复用根 stream-json 事件与 QuotaRejected，区分原生 init／user admission 与 assistant 活动；ZCode 保留 `protocol.py:335–338` 已提取的 turnPhase，并结合两种 carrier 共用 collector 的实际活动与事件覆盖，不臆造该字段的枚举；DSH 复用 ACP 根活动和 stop/failure。现有源码足以定位这些落点，尚不足以证明四家每个原生版本都能提供完整的拒绝前覆盖：本文不声明已验证。实现用有原生依据的 fixture 固定解释；拿不到证明时明确报告 unknown，不能为了达到“透明”而猜 before-model。资格检查仍只问 harness 声明的能力，不新增运行前模型探针（ADR-023）。

服务在已通过 attempt/generation/nonce、不可变结果与停止证据的同一导入事务中先记录提供方冷却。对自动选择的 Worker，只有 `quotaFailure.code=quota-exceeded`、`rejectionPhase=before-model`、原生组已结束且该 attempt 的执行占用已有足够的停止证据、未被取消或 fencing、run 的 owner generation／current decision 仍一致时，才进入透明改派。持久 attempt 保留其真实拒绝与结束，旧 turn 作为未交付的被拒回合归入路由证据，撤销它的凭据；run 保持 executing，task 回 queued，关闭本次 active attempt，释放本次进程／容量占用，但保留受管 checkout、固定输入、写入范围与工作区所有权。下一 attempt 仍经正常调度、buddy validation 和新凭据发放；不跳过执行的完整身份与停止证据。

下一候选通过现有候选扫描、单候选程序采纳、多候选 Router、publication 检查和 `routing_settled` 采纳产生。Router 的答案目前只有一个选择，没有可机械取用的第二名（`decision.py:1359–1381`），所以“下一个候选”是剩余合法集合中的下一次正常选择，不能虚构排名列表。把 `_retry_selection` 拆出可复用的决策内重选动作，去掉额度改派的一次限制，保留同一 runId、decisionId、workflow_routes 关联和原始冻结请求证据；更新该决策的当前选择轮次及 route 的 pending 状态，再走现有 adoption。不能调用 `_start_routing`，因为 `workflow.py:1396–1408` 会增加 sequence 并新建另一 decision；不能直接调用 `_requeue`，因为 `3279–3281` 会增加 continuation_count。提取两者真正共用的内部排队／采纳动作，保留 Host continuation 原语义。

`selectionRetry` 的布尔和 `decision:<request>:0|1:<routerIndex>` 请求键不能承载第三次选择。统一改成非负整数 `selectionRound`，第 0 轮为初选，重选递增；request、dispatch 文档、Router outcome 幂等键与边界历史同步使用这个值。原“有效答案发布时边界变化，只重选一次”的预算单独依据该原因的既有重选事件计数，额度拒绝不消耗它。每次尝试的输入与哈希不可变，新的轮次生成新的 Router task 身份；旧 outcome 的 receipt 身份在升级时按新键重算，不留布尔别名。所有持久形式与 JSON 副本转换都在批次二同一 24 → 25 升级里完成。

同一 decision 中已因额度被拒的 harness／provider 不再被选第二次，即使原生恢复时刻很近或用户恰在这一轮执行 quota-redetect；从该决策下已记录的拒绝 attempt／路由事件得到已试集合，不另设第二张黑名单表。新决策可以使用到期或已解除的提供方。候选仍服从 enabled、目录可用、能力、任务硬约束、固定与排除；不自动放宽 pin 或任务指定字段。重放同一拒绝结果不增加轮次或冷却次数，服务重启从同一事务记录恢复，不再调用一次模型。并发的旧选择在实际 admission 时重新查冷却；冷却到期没有“单次机会”与持久 lease，其他普通请求是否同时运行由已有模型服务并发控制负责。

如果拒绝发生在 Router 自身的无工具／审阅调用，先记录同一个提供方冷却，然后复用 `router_failover.classify_outcome` 与既有 Router 列表推进。普通调用无回答的统计规则保留，quota 不再叠加另一段更长的无回答 skip 时限来覆盖 30 分钟冷却；审计仍保留这次无回答。额度拒绝发生在 Worker 的途中或阶段 unknown 时，保留 quotaFailure、部分成果与现有失败／Host 边界，进入冷却但不自动改派。认证失败走现有 harness／账户不可用和凭据修复路径，不写 quota 冷却；短暂 rate-limited 保留原分类与原停止规则，只有 ZCode 明确 non-retryable 的 rate_limited 仍按现有规则归 quota-exceeded。取消、unknown stop、模型安全拒答、上下文限制和协议错误不被改造成额度拒绝。

Host 明确选择或锁定的 buddy 遵守 ADR-019：显示冷却提示，保留其选择，不静默换人；这次真实调用仍可执行并更新冷却，若被拒则由 Host 决定后续。自动路由在所有候选都冷却时不建立假的 Worker 完成或失败，也不无限等待冷却到期自动重发，而进入既有的 awaiting-host 路由请求。Host 可以在 retryAt 到来后对同一微任务 reroute，或明确选择一个完整 buddy；只有修改共享 Router 设置才涉及用户。冷却到期只改变可选事实，不自行发起调用。

## 持久化与 24 → 25 升级

新表 `provider_cooling` 以 `(harness, provider)` 为主键，字段为 `consecutive_rejections`（非负整数）、`cooling_until`（UTC 或 NULL）、`native_resumes_at`（这次拒绝明确给出的 UTC 恢复时刻或 NULL）、`last_rejected_at`、`last_success_at`、`cleared_at`、`source`、`native_code`。时间与失败归属都取已冻结的 attempt；不保存账号标识、余额、limit_id、窗口和旧 retry_consumed_by。保留次数为零的记录可以表达已成功／已解除的事实；无行表示没有冷却历史。不要再存一个与 cooling_until 重复表达状态的 blocked 布尔，`cooling` 在读取时按当前时间计算。

另设 `provider_credits_usage`，主键仍为 `(harness, provider)`，仅存 `using_credits`（0 或 1）、`observed_at`、`source`。无行表示未知，不把未知存成 false，不与静态 subscription／metered 标签混用。它是有结构的原生事实，不放 meta、不塞进 health.record_json。不会有原生观测的 harness 不造记录；清除冷却不改 credits 状态，成功也不等于正在使用 credits。credits 按最近明确观测展示并附时间／来源，不定义百分比过期后自动变冷却的机制。

服务在一次新的额度拒绝提交时令 n = 原连续次数 + 1；冷却秒数为 `min(1800 × 2^(n−1), 86400)`。实际运算在 n ≥ 7 时直接取 86400，避免构造无用的大整数；次数仍表示真实连续拒绝次数。若该拒绝有可验证的原生恢复时刻，则 `cooling_until = min(rejected_at + 冷却秒数, native_resumes_at)`；已过去的原生时刻不制造额外等待，当前 decision 的已试集合仍防止热循环。拒绝时刻缺失时由服务本次已验证回执的接收时刻定时，并明确记录 source；不得从无时区消息字符串推导 UTC。重置窗口里的时刻不作为这个值。

一次服务确认的、绑定本次根回合且成功完成的模型调用将 n 清零并清除 cooling_until／native_resumes_at；Worker 的业务 outcome 为 assistance 或 attention 不妨碍它成为一次模型调用成功，Host 是否验收也不参与判断。反过来，本机预检、没调模型的 discovery、账户查询、只有 allowed_warning、传输失败和阶段未知的错误都不是成功。调用成功事实由共同结果单独表达，不能拿角色层的业务完成或工作区封存成功替代。按原生事件／回合结束时刻拒绝晚到旧成功覆盖新拒绝，读取不修数据；边界规范化与回执幂等性只做一次。

`quota-redetect` 的解除把 cooling_until 与本次 native_resumes_at 清为 NULL，记 cleared_at，保留 n：立即解除只是允许试一次正常调用，只有成功才证明连续拒绝结束；下一次仍被拒继续加倍。该命令适用于所有冷却，不再分“有 reset／无 reset”。重复 requestId 返回原有操作结果，即使其后又冷却也不二次解除；新 requestId 才表达新的解除意图。额度未恢复并非异常，命令不调用 Codex 的账户查询。SQLite 更新、命令回执、解除事件提交仍为同一事务。

升级基线必须仍为 0.42.0 的 `90bd0148`，不是拿当前 fixture 的字段猜历史：`tests/fixtures/upgrade-base/metadata.json:149,318` 固定 sourceCommit／sourceVersion，`board.sql:333–337,411–429` 实际有两张旧表和人工解除标记。只读比较 `90bd0148..e006910b`（0.45.3）的 quota_routing 写入者没有变化，两端该文件 blob 相同 `c799dfdbcfa81ac0fa59816b10fd425b2fde446c`；其 `record:122–155` 会按 blocked／available 覆盖旧值并重置 retry 标记，`redetect:228–249` 只打开机会，未存连续次数。0.42.0 的 `native_observations.py:37–57` 会从 quotaFailure 合成 ordinaryUsageAllowed=false 的观测，也会接受原生窗口／账户观测；同一时期的 `quota_routing.py:41–58` 把不同来源合并到同一 blocked 行。

因此不能把一条旧 blocked 行解释成“连续被拒一次”，不能用 retry_consumed_at 数出拒绝次数，也不能把旧 resets_at 不加区分地迁成 native_resumes_at：行里已经丢失是真实拒绝还是读取推导的区别。升级明确清空这部分旧派生状态，新建的两张表都为空，旧读数与 quota_current 连表删除；第一次新真实拒绝从 n=1 开始，credits 从未知开始。这是一次显式丢弃不可转换的派生观测，不宣称账号已恢复，也不把旧失败 attempt 改成成功。不扫描历史失败重新制造当前冷却，因为缺少完整成功序列会复活已结束的限制。升级后可能让过去被排除的提供方获得一次真实试探，这一转换结果写入验收。

升级在 `upgrade_schema_24_to_25` 的一个事务内执行，保持 schema=25 与 contract=0.46.0。对 `attempts.result_json`、catalog_observations 的 harnesses／response、decision_requests 的 requested/input/output/selected、router_requests.snapshot、router_dispatches.document、相关任务 spec、workflow turn/context 与相关 events／commands，先按 0.42.0—0.45.3 的具体写入者逐项列出拥有的 JSON 路径，再删除旧 quota、quotaWarnings、quotaRouting、quotaRetry、quotaExhausted、quotaRetryBuddyIds、rateLimitObservations 中的读数与旧窗口标记；保留真实 token、费用、原生失败代码及部分成果。`catalog_store.py:22–26` 复制健康视图，`upgrade.py:324–348,1023–1049` 已展示这些副本不能只改主表。不能递归按 key 名误删模型输出或 Host authored 内容。

当前计划对这些 JSON 容器给出范围，但尚未逐个还原每个历史 task spec／workflow context 的写入路径；这是批次二必须交付的迁移路径表与 fixture 证据，不能把这个范围列表冒充已完成的转换证明。新增 selectionRound 的转换按旧写入者规则：缺失 selectionRetry 为 0，true 为 1，若有显式 false 也为 0；旧字段删除，未知形状升级失败并整体回滚。dispatch 文档删除读数或转换轮次后，所有对应 input_sha256 与引用它的服务自有副本一并重算，原生签名／不可改证据文件不伪造重签；如已结束记录需要保留原生绑定，则把原生证据保留在原生事实域，服务投影显式重建。`quota.redetect` 旧回执转换为“历史操作，仅解除旧单次机会”的操作事实，不能重放新解除：保留 commandId、request_hash 与发生时间，标 `appliedToCurrentCooling:false`，读取该固定迁移事实时不再接受旧响应形状。此标志属于一次升级的历史语义，不依据写入时间猜格式，也不是旧字段别名。

批次二必须同时检查 schema validator／新库 DDL 对新表、旧表缺失的严格要求，扩展 fixture 测试比较升级库与新库的表／索引结构及外键；补充空旧表、blocked／available、多个 limit、manual／consumed、已过／未过 reset、乱序未知观测、真实失败、带复制字段回执与多次 selection 的测试。输入 SQL 与 metadata 仍由 0.42.0 产生并保持不改，只在测试的私有装载库里补各旧写入者能产生的记录。注入升级失败必须回滚表与 JSON；安装演练从 0.45.3 走本次升级，不能向旧运行服务发送它不认识的新字段。普通运行不含旧表探测、双格式读取或隐式修复。

## 对外与跨进程契约

删除 health/account/execution/selection/evaluation/CLI/console 回复及 Router 输入中的 `quota` 整块和 `quotaWarnings`、`quotaRouting`、`quotaExhausted`、`quotaRetry`、`quotaRetryBuddyIds`；不再发布 usedPercent、utilization、windows、windowDurationMins、窗口 resetsAt、ordinaryUsageAllowed、balanceZero、ambiguousLimits、coveredLimits、nativeAccountId 及 retry 的 eligibleAt/open/pendingManual/manualAt/consumedAt/consumedBy。Claude 的 rateLimitObservations 不再携带窗口或用量，只保留必要拒绝与 credits 事实，取消专用旧失败块。这里删除的是额度字段，`tokenUsage`、计费标签 billing／billingByProvider、实际 totalCostUsd 与其他领域同名的 expiresAt／retryAt 保留。

每个 harness 的健康视图新增 `providers` 列表；每项有 provider、`cooling` 对象与 `creditsUsage`。`cooling` 的固定字段为 `active`、`consecutiveRejections`、`coolingUntil`、`nextTryAt`、`nativeResumesAt`、`lastRejectedAt`、`lastSuccessAt`、`clearedAt`、`source`、`nativeCode`；nextTryAt 仅在 active 时等于 coolingUntil，否则为 NULL。没有冷却历史也用完整空状态投影，不用“quota 缺失就换一种读法”。creditsUsage 为 NULL（未知）或 `{usingCredits:boolean,observedAt,source}`。Buddy 只投影自己 harness／provider 对应的这两个事实；Router 输入沿同一个事实生成器，不让 UI 复制路由判断。

执行回执保留统一 `quotaFailure:{code,nativeCode,source,observedAt,nativeResumesAt,rejectionPhase}`，code 仍为 quota-exceeded 或 rate-limited；原生恢复时间可空，不能承诺真实恢复。成功模型调用另有明确的根回合成功事实供服务清零，creditsUsage 是独立可空事实。原生失败字段只在信任边界规范一次，服务按冻结提供方归属，harness 不把 provider 自选成其他身份；原生 error 详情与业务 outcome 不承担冷却状态存储。`modelStarted` 继续表达其被明确定义后的执行活动事实，未知不得压成 false，透明改派只消费 rejectionPhase 证明。

全部候选冷却时沿用 routingBoundary 的字段及 commands 模板，新增 `kind:routing-cooling`，`code` 和 `reason.code` 都为 `routing-all-candidates-cooling`，reason.params 为 `{}`。`facts.coolingProviders` 每个 harness／provider 一项，含 buddyIds、nextTryAt 和冷却原因；`facts.availableCandidateIds=[]`，retryAt 是这些候选最早的 nextTryAt。`candidates` 留符合非冷却硬约束的完整 buddy 及冷却状态，供 Host 明确选择；自动集合仍为空，不把这些条目当成自动可用。停止未确认或权限不满足时，commands 继续 blocked；满足时 choices 带完整 buddy，reroute.notBefore=retryAt，现有 userAction=settingsChangeOnly 保留。Router 列表全部冷却则用现有 router-unavailable kind，原因改 `routing-all-routers-cooling`，同样给每项时刻，不把“Worker 没候选”和“Router 无法作答”混为一条原因。

明确选择正在冷却的 buddy 时新增 `selectionNotice:{code:harness.provider-cooling,params:{harness,provider,nextTryAt}}`，不复用已删的 quotaWarnings；提示随 submit／continue 与 brief 显示。新增 reason 及参数在 `reason-codes.json` 注册并配中英文。如果没有候选的原因还包括禁用、硬约束、目录缺失或 Router 未配置，保留对应现有边界原因，facts 分别说明；只在“去掉冷却过滤本有合法候选且这些候选全冷却”时发专用 all-candidates 原因。retryAt 表达可试时间，不表达保证恢复或自动唤醒。

方法名称、CLI quota-redetect 与参数 `{requestId,harness,provider}` 保留，回复统一为 `{cooling:{harness,provider,cleared,clearedAt,state,appliedToCurrentCooling},duplicate}`，删除旧 quota.records/opened/alreadyOpen。当前操作 appliedToCurrentCooling=true；升级历史回执为 false，cleared=false，state=null，表示历史操作没有触碰新冷却。重复操作返回已提交操作事实，不能用当前 state 替换首次结果而掩盖新的拒绝。契约版本保持 0.46.0；本批在它安装之前完成，合同签名 fixture 仍校验 0.46.0，不新增 0.47.0、不兼容旧 JSON。安装请求旧服务的部分只用 0.45.3 已接受的 drain／备份／切换字段。

## credits 与控制台呈现

“正在使用 credits”只取原生已说明正在使用的状态：Codex 使用已有 account/rateLimits/read 或原生 rate-limit 更新；Claude Code 使用 rate_limit_event 的额外用量标志。现在的 Codex 代码只见 `hasCredits/unlimited/balance` 和 ordinaryUsageAllowed（`codex/protocol.py:275–280`），Claude 只保存 status／utilization／unifiedWindows（`claude/protocol.py:277–319`），都尚未给出一个已验证的 usingCredits 投影。不能把“持有 credits”“按量账户”或“额度到 100%”当成正在使用；也不能为了产生这个状态保留百分比与余额算法。

两个原生来源的具体“正在使用”字段名、不同桶的适用关系及缺失语义，在本 checkout 的样本里尚未证明；Claude 的 seven_day_overage_included 是窗口名，不能拿它替代额外用量标志。实现批次一要用可追溯的原生字段定义／最小 fixture 给出精确映射与适用的 CLI 版本；没有证据就输出未知，不臆造原生字段名，不加猜测回退。DSH／ZCode 的这类来源仍未知。此计划没有联网、调用真实 harness 或读取用户凭据，不能把后续原生核查写成已完成；若现有公开／本地可读定义不足，作为 Host 后续单独安排的验证边界记录。

目录的原额度栏改名“提供方状态 / Provider status”，按提供方显示“冷却中 · 约 {{time}} 可再试 / Cooling down · try again around {{time}}”；到期显示“可再试 / Ready to try”，没有拒绝记录显示“未在冷却 / No cooldown”。可再试不等于远端账户已经恢复。多个提供方时逐个标识，不用某一个 blocked 让整个 harness unavailable。正常状态用低强调文字，冷却用既有等待的语义色和文字，credits 是事实标签，不借警告色推断风险。

creditsUsage=true 显示“正在使用 credits / Using credits”，false 显示“当前未使用 credits / Not using credits”，NULL 显示“credits 状态未知 / Credits usage unknown”；详情附“最近观测 / Last observed”的来源与时间，不能把旧观测说成实时余额。冷却与 credits 可同时出现，因为后者可能来自更早的明确观测；显示时间即可解释，不用其中一个覆盖另一个。

详情删除额度窗口、进度条、余额、额度 reset 和近限说明，换为每提供方的冷却原因、连续拒绝次数、下次可试时刻，以及 credits 最近观测。原生拒绝恢复时刻只在冷却详情中作为“原生报告可恢复时间 / Native reported recovery time”出现，不能另起窗口块。解除按钮为“解除冷却 / Clear cooldown”，简短说明“允许下次正常调用立即尝试，不查询额度 / Allow the next normal call to try now; no quota query”。回复为“已解除冷却 / Cooldown cleared”“当前未在冷却 / No active cooldown”“历史操作未改变当前冷却 / Historical operation did not change the current cooldown”；未知回复复用同一 requestId 与已有错误显示。按钮的可用权限、键盘焦点及整行展开／选择返回路径保留。

移除近限徽标和过期额度徽标，普通冷却不制造“需要用户处理”的圆点。全冷却导致的 awaiting-host 在现有任务／路由边界显示“所有候选都在冷却 / All candidates are cooling down”及逐项时刻、明确选择与稍后 reroute；中途失败继续显示“额度耗尽 / Quota used up”及部分成果。路由历史以一条可展开记录说明“因额度拒绝，已改派 / Reassigned after quota rejection”，不把中间拒绝伪装成 Worker 业务失败。中文／英文、宽屏／390 窄屏、键盘／读屏名称、时间换行、展开、解除后刷新和选中项保持都在真实浏览器中验收；遵守 ADR-029，复用现有组件和 Intl 格式化。

## 分批交付与验证

按一 → 二 → 三 → 四顺序集成；不把中间批次安装到日常服务，也不为让中间状态可安装而加旧字段兼容。每批只运行相关检查，最终集成再跑一遍完整规定检查。微任务输入冻结具体目录与文件，不能把本计划的“大范围”当成无限写权限；若实现需新文件，归入相应 owning 目录并在该批明确列名。以下是后续微任务的范围与检查要求，本次只写本文。

### 一：原生拒绝阶段与 credits 证据

写入范围：`S/buddy/harnesses/{codex,claude,dsh,zcode}/{native_run.py,protocol.py}`、`codex/account_probe.py`、`dsh/session_records.py`、`S/buddy/harnesses/run_contract.py` 及相应 harness／run-contract 测试与 fixture；阶段能力与未验证项记录进对应原生事实说明。本批只完善共同 RunResult 的内部事实采集和精确语义，不改服务回执的持久形式、不让新字段提前落入 board JSON；对外切换与旧 quota 采集删除统一归批次二。若内部变化本身需要保存新字段，本批必须移入批次二与升级同交付，不能留下未迁移的新存量。本批禁止凭未核实字段给出 credits=true。

检查用 `uv run --frozen python -m hey_my_buddy.cli.checks` 分别跟 `tests/python/buddy/harnesses/test_run_contract.py`、`tests/python/buddy/harnesses/test_quota_failures.py`、`tests/python/buddy/harnesses/test_adapter_usage.py`、`tests/python/buddy/harnesses/codex/test_native_run.py`、`tests/python/buddy/harnesses/claude/test_native_run.py`、`tests/python/buddy/harnesses/claude/test_claude_protocol.py`、`tests/python/buddy/harnesses/dsh/test_native_run.py`、`tests/python/buddy/harnesses/dsh/test_session_records.py`、`tests/python/buddy/harnesses/zcode/test_zcode_protocol.py`、`tests/python/buddy/harnesses/zcode/test_zcode_tool_evidence.py`；命令可一次带这些文件，仍由正式 runner 运行。覆盖两种 carrier、真实分类、开始前／中途／缺证、额外用量字段真假缺失及停止证据。本批交付原生事实映射表与模拟验证结果，不把 fixture 通过写成原生版本通过。

### 二：服务冷却、透明改派、全部持久形式与唯一升级

依赖批次一。写入范围：`S/blackboard/routing/` 中 quota_routing、decision、router、router_boundary、router_boundary_data、router_sequence、router_history、router_failover；`S/blackboard/evaluation/{native_observations,evaluation,user_policy}.py`、`S/blackboard/catalog/catalog_store.py`、`S/blackboard/service/{harness_health,service}.py`、`S/blackboard/store/{db,store,upgrade}.py`、`S/blackboard/tasks/{workflow,macro_task_deletion,command_receipts}.py`；`S/protocol/{usage,reason-codes.json,contracts,client,transport}` 的相关事实；`S/buddy/roles/{run_execution,schema_worker}.py`、`S/buddy/harnesses/registry.py` 与批次一已定位的旧 quota 采集删除；`S/cli/{cli_help,cli_views,blocking}.py`、`S/console/{reads,read_cache}.py`；表中对应 Python 检查和 fixture 装载辅助文件。完整存储形式、selectionRound、所有 JSON 副本、回执、schema 校验、新表及 24 → 25 升级同一批审查，不拆到后续补迁移。

必须交付的回归是：A 拒绝→B 成功保持 runId／decisionId；A／B／C 连续拒绝不因布尔请求键冲突；同提供方不同模型不会绕过冷却；全部冷却只开一次 Host 边界；已明确选择／锁定不静默改；中途拒绝保留部分成果；unknown 阶段、unknown stop、取消／接管／scope 改变不替换执行；重复／崩溃恢复不多计数、不重复调度；新旧成功／拒绝乱序；手动解除重放不清掉后来冷却；到期只读投影变化；Router 冷却复用列表路径；新库与升级库严格一致；所有旧字段和表在服务自有投影中消失。

检查命令为 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/blackboard/routing/test_provider_cooling.py tests/python/blackboard/routing/test_nonretryable_quota.py tests/python/blackboard/routing/test_selection_publication.py tests/python/blackboard/routing/test_router_boundary_integration.py tests/python/blackboard/routing/test_router_failover_integration.py tests/python/blackboard/routing/test_single_router_claims.py tests/python/blackboard/routing/test_single_router_requests.py tests/python/blackboard/routing/test_current_router.py tests/python/blackboard/routing/test_router_list_health.py`；其中 test_provider_cooling.py 为替换 test_quota_retry.py 的新文件，旧文件同批删除。另运行 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/blackboard/evaluation/test_native_observations.py tests/python/blackboard/evaluation/test_other_native_observations.py tests/python/blackboard/tasks/test_workflow.py tests/python/blackboard/tasks/test_host_workflow_worker.py tests/python/blackboard/tasks/test_model_service_concurrency.py tests/python/blackboard/tasks/test_macro_task_deletion.py tests/python/blackboard/store/test_store.py tests/python/blackboard/store/test_upgrade_fixture.py tests/python/install/test_upgrade_service.py`。

事实边界另运行 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/protocol/test_usage.py tests/python/protocol/test_billing_quota.py tests/python/protocol/test_contract_version.py tests/python/blackboard/service/test_harness_health.py tests/python/blackboard/service/test_console_reads.py tests/python/blackboard/catalog/test_catalog_observations.py tests/python/console/test_console_macro_task_cache.py tests/python/cli/test_cli_views.py tests/python/cli/test_blocking.py tests/python/buddy/harnesses/test_c_two_live.py tests/python/buddy/harnesses/test_session_receipts.py`。批次二也重跑批次一的全部相关 harness 文件，因为最终对外切换发生在这里；验收迁移的期望必须来自历史写入者与新 DDL，不以升级函数自己的结果生成 expected。

### 三：控制台状态、解除入口与合成预览

依赖批次二的新回复。写入范围：`apps/console/src/{types,api,quota}.ts`（删除 quota.ts）、`buddies/{harness,HarnessHeader,HarnessDetails,CatalogView,attention,BuddyHeader,harness-retry,use-harness-actions}`、task-detail 的解析／事实、shell 边界解析、`i18n/{zh,en}/` 的相关命名空间、相关前端测试与 fixtures、`apps/console/browser/{catalogue,catalogue-gestures,task-detail}.check.ts`；`tests/probes/macro_task_console_preview.py`、其输出 fixture 清单与预览测试、`S/console/assets/` 构建产物。本批把新回复作为唯一格式，删除旧 quota 模块、i18n key、坏 quota 降级场景与全部旧哈希产物。

检查命令为 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/cli/test_host_preview.py tests/python/console/test_console_preview.py tests/python/blackboard/service/test_console_reads.py`、`npm --prefix apps/console test`、`npm --prefix apps/console run test:browser`。后两项必须在最终源码跑完，不能用局部 Vitest 或 jsdom 像素断言替代；browser 命令沿用既有 build，不运行 npm exec 或 npx。依赖与浏览器须已准备好，本次任务不安装、不联网。未准备好时由 Host 安排后续准备并明确记录未验证，不绕过约束。验收至少含上述双语、390 窄屏、多提供方、过期冷却、credits 三态、解除权限／未知回执及成功微任务没有旧失败标签。

### 四：参考文档、集成核对与验收记录

依赖二、三。写入范围：前述 owning 参考页、`docs/README.md`、README 中英文、`skills/hey-my-buddy/SKILL.md`，以及新建 `docs/acceptance/quota-cooling.md`。先更新 harnesses／decision／workers 等 owning 页，再同步入口，删除当前文档中“显示百分比／余额”“一小时单次机会”“所有额度失败都必须 Host 处理”的旧契约；历史决策、调研和验收仍作为历史。验收记录注明本地模拟、原生核查、安装演练、真实安装与 Host 验收各自完成到哪一步，版本仍为 0.46.0，不能把未安装当成功安装。

最终检查为 `uv run --frozen python -m hey_my_buddy.cli.checks`、`npm --prefix apps/console test`、`npm --prefix apps/console run test:browser`；仓库卫生单项为 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/cli/test_repository_hygiene.py`。完整 Python runner 应覆盖本计划清点中未列进局部命令的受牵连文件，包括 discovery、controller、原生配置、CLI、调度与协议一致性；不手工起 test 进程、不触日常 board。测试子进程按 AGENTS.md 清除继承的 HMB runtime／agent 凭据和虚拟环境变量；若离线依赖缺失，报告具体缺项，不隐式联网。安装／真实 harness 的核查不在本文编写任务内，也不自动作为完成本计划的条件。

## 本次计划的核验边界

本次只允许写 `docs/design/quota-cooling-plan.md`，没有实现、原生调用、模型调用、日常看板操作或安装。文中源码事实由 checkout 与本地 Git 对照；具体原生 credits 字段、各家拒绝前事件覆盖及全部历史 JSON 写入路径已明确标为后续必须核实的项，不能被摘要成“已全部具备”。本计划的规定检查是 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/cli/test_repository_hygiene.py`；实际退出状态随 Worker 结果交付，本文不预写通过。
