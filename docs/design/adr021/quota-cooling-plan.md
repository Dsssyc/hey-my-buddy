# 额度改为冷却与透明改派：执行计划（第 2 版）

依据 ADR-021 第 22 条、ADR-019 第 6 条、ADR-018 第 23 条的 2026-10-18 修改及 `docs/research/{2026-10-18-quota-cooling-and-failover,2026-10-11-refusal-kinds-and-zhipu-funding}.md`。按 Host 复审：每次改派新开路由决策；credits 后续再做。

行号基于 `b8f88a66`；路径缩写：`S/`=`src/hey_my_buddy/`，`B/`=`S/blackboard/`，`H/`=`S/buddy/harnesses/`，`F/`=`apps/console/src/`，`T/`=`tests/python/`。只写本文；保持源码／契约 0.46.0（`pyproject.toml:3`、`S/protocol/contracts.py:17`），扩展唯一 24 → 25 升级（`B/store/upgrade.py:1219–1249`）。

## 改动清点

| 文件与行号 | 要做的 |
| --- | --- |
| `B/store/db.py:943–965` | 删除 quota_current、harness_quota_observations；建下文的 provider_cooling。`515–544` 的 workflow_turns 重建 CHECK，允许 rejected。 |
| `B/routing/quota_routing.py:20–58,69–155,158–274` | 删除窗口／available 推导、3600 秒等待、retry marker、claim 与 buddy_retry；改成拒绝计数、冷却计算、成功删行、手动解除和只读投影。 |
| `B/evaluation/native_observations.py:24–108,112–121` | 删除 latest_quota、record_quota、quota_view、warnings；persist 保留 token 写入，改记冷却；failure_view 只取结构化 quotaFailure。 |
| `B/service/harness_health.py:64–82,371–427` | 健康回复把 quota／quotaRouting 换为 providers；Codex 账户刷新只写计费标签，删 quotaRecorded。 |
| `B/service/service.py:577–602` | quota_redetect 保留参数校验、requestId、事务回执与事件，改为直接解除冷却及扁平回复。 |
| `B/catalog/catalog_store.py:285–291`、`B/evaluation/evaluation.py:63–69` | 删除 quotaExhausted／quotaRetry，提供方状态取统一冷却投影；保留 billing。 |
| `B/tasks/workflow.py:728–758,790–791,836` | 删除 quotaWarnings，保留真实 quotaFailure；明确指定冷却 buddy 时展示其提供方冷却事实，保留选择。 |
| `B/routing/decision.py:181–232,269–281,435–462,489–506,1349–1413` | 候选过滤改查冷却，删所有 claim 与 quotaRetryBuddyIds；发布时的 exhausted()+claim 改成冷却判断；routingBasis 冻结冷却排除项，零候选走现有程序路径。 |
| `B/routing/router.py:145–172`、`router_boundary_data.py:69–92,351–385` | Router 资格沿用 router-quota-exhausted；恢复时刻改取 cooling_until；原有无回答 skip 窗口和 _retry_at 合并规则保留。 |
| `B/tasks/workflow.py:1395–1487,6244–6435,7047–7141` | settle_turn 加开始前额度拒绝分支；标 turn 为 rejected，恢复本次 continuation，复用 _start_routing；begin_turn 的有效次数与 previous 排除 rejected。 |
| `B/store/store.py:2243–2366` | 保留执行结果基础事务、失败 attempt、事件、回执、停止处理与 settle_turn 调用顺序；冷却通过已有 persist 落库，改派通过 settle_turn 返回 autoContinued 让现有分支刷新 task。 |
| `B/tasks/macro_task_deletion.py:480–481` | 随旧表删除 retry_consumed_by 清理；删除宏任务不清提供方共享冷却。 |
| `B/store/upgrade.py:69–73,294–305,816–851,951–961,1219–1249` | 同一升级完成删旧表、建新表、turn CHECK、catalog 提醒丢弃、routingBasis 清理、原因参数转换与旧 redetect 回执删除； |
| `S/protocol/usage.py:40–41,272–368,370–414,496–504` | 删除 quota/window 规范化及导出；保留拒绝分类、quotaFailure 的 resetsAt 与 token 规范，给失败事实增加 rejectionPhase。 |
| `S/protocol/reason-codes.json:4–7,215`、`S/protocol/contracts.py:17,46`、`S/protocol/transport.py:46` | 删除读数提醒原因，注册零候选冷却原因及 retryAt 参数；保留 router-quota-exhausted、方法 quota_redetect、CLI 映射和 0.46.0。 |
| `H/codex/protocol.py:218–328,355–356,385–394`、`codex/native_run.py:1096–1109,1299–1353,1390,1768–1771,1856–1868` | 删除快照、窗口、余额、quota-snapshot 引用与读数查询；在共用 collector 中补阶段，保留 turn error 分类。 |
| `H/codex/account_probe.py:18–49` | 去掉 account/rateLimits/read 与 quota 返回值，仅保留计费标签需要的 account/read、config/read、provider 归属和停止确认。 |
| `H/claude/protocol.py:36–38,65–69,277–319,326–332,393–425,489–555,639–688`、`claude/native_run.py:769–770,825–878,1061–1075,1192–1199` | 保留结构化拒绝与 resetsAt；删除 utilization、unifiedWindows、quota_candidate、rateLimitObservations 读数；拒绝阶段取已有根活动。共同 quotaFailure 覆盖后删除 quota-rejected 专用分支。 |
| `H/zcode/protocol.py:302–369`、`zcode/native_run.py:922–928,1142–1152,1667–1682` | 保留 turn.failed attribution、turnPhase 与 non-retryable rate_limited 分类，补根活动判定；不从供应方消息猜恢复时刻。 |
| `H/dsh/session_records.py:239–250`、`dsh/native_run.py:899–900,1245–1267,1626–1651` | 保留本次根回合 turn/end 的 error.code、ACP 活动及 token 事实，补拒绝阶段；记录折叠不沿用上一回合的 failure。 |
| `H/run_contract.py:413,591–628`、`S/buddy/roles/run_execution.py:128–150,243–258,287–302,721–724` | 在共同失败事实中传 rejectionPhase，删除读数引用和搬运；quotaFailure 使用统一形状。 |
| `H/registry.py:105`、`S/buddy/roles/schema_worker.py:64` | 共同 quotaFailure 生效后删除 native_quota_failure 特例及两种失败读法。 |
| `S/buddy/runtime/execution.py:626–633,656–706`、`runtime/receipts.py:21–56`、`S/protocol/client.py:103–111`、`B/service/service.py:884–885` | 保留报告、durable receipt 与 execution_result 交付；本机启动重试照旧。 |
| `S/cli/main.py:41`、`cli_help.py:45,116`、`cli_views.py:221–222,452–453`、`blocking.py:299` | 保留 quota-redetect、幂等请求与真实失败；删 quotaWarnings，帮助与 brief 改成冷却、解除及零候选原因。 |
| `S/console/read_cache.py:58–65,95–123`、`S/console/reads.py:8–14`、`S/console/server.py:95` | 删除额度读数的 staleness 与单次窗口 deadline；加入 coolingUntil 到期失效，保留其他 expiresAt／scanAfter／Router 时限及控制台写权限。 |
| `F/types.ts:36–39,80–133,194–197`、`api.ts:354–394,433–442`、`quota.ts:1–110` | 删除 quota.ts、旧 quota／retry 类型与解析；解析 providers 和新原因，保留实际 quotaFailure。 |
| `F/buddies/harness.ts:42–198`、`HarnessHeader.tsx:28–47`、`HarnessDetails.tsx:97–156`、`CatalogView.tsx:74,238` | 删窗口／余额／进度／reset，仅显示冷却提供方及解除入口，保持其他提供方可用。 |
| `F/buddies/attention.ts:2–25`、`BuddyHeader.tsx:16`、`tokens.css:5` | 删除近限与过期提醒来源，冷却作为状态；原有 Router 配置／故障的待处理提醒保留。 |
| `F/buddies/harness-retry.ts:7–50`、`use-harness-actions.ts:64` | 解除动作读取新扁平回复，保留 requestId、未知回复重放与写权限，删除 opened／alreadyOpen 的解析与单次机会文案。 |
| `F/task-detail/parse.ts:436–451`、`facts.tsx:19–23` | 保留中途额度失败与部分成果；显示冷却零候选原因，微任务成功后不沿用先前被拒 attempt 的失败标签。 |
| `F/i18n/{zh,en}/buddies-harness.ts:80`、`buddies.ts:16`、`console.ts:78–80（zh）,80–82（en）`、`reasons.ts:4–9,133（zh）,135（en）`、`shell.ts:48–49（zh）,49–50（en）` | 删除窗口、余额、近限、过期和机会消费词条；新增冷却、解除、全部候选冷却中英文；真实失败词条保留于 taskDetail.ts:58（zh）,59（en）。 |
| `tests/probes/macro_task_console_preview.py:59,152–153,180,1752,1796–1844,2823–2870,3035–3067` | 删除读数／坏 quota／单次机会样本与清单项；改成多提供方冷却、到期、解除、改派成功、全部冷却与中途失败。 |
| `S/console/assets/assets/index-DCU-hBe3.js:1` 及资源入口、样式 | 构建资源，删旧哈希文件。 |
| `B/tasks/scheduling.py:7`、`B/store/store.py:798,1634`、`S/buddy/roles/router.py:161–162`、`run_observers.py:96`、`B/evaluation/user_policy.py:210` | 保留模型服务并发、Router 预算、无回答间隔与用户偏好；不增加执行 admission 的冷却复检，Router 身份可在暂时冷却时照常保存。 |

## 结果传递与改派

结果沿现有 quotaFailure → durable receipt → execution_result → store 事务传递（清点表中的 run_execution、execution、receipts、client、service）。store 仍记录失败 attempt 和事件（`2243–2301`），在 `workflow.py:7107` 的 native_resume_refused 旁新增额度改派分支，返回 autoContinued，由 `store.py:2353–2366` 刷新 task。

各 collector 在额度拒绝时给 `rejectionPhase: before-model | during-model | unknown`：根回合未观察到助手内容、推理、工具调用或输出 token 即 before-model；有活动为 during-model；collector 出错／流截断为 unknown。沿 Codex TurnEvidence（`codex/native_run.py:1309–1343`）、Claude 根活动（`claude/protocol.py:427–555`）、ZCode 共用 collector（`zcode/native_run.py:900–933,1142–1152`）、DSH MODEL_ACTIVITY_KINDS（`dsh/native_run.py:899–900`）。发送输入、init／turn/start 不算模型活动；改派读取 rejectionPhase。

settle_turn 仅在 quota-exceeded、before-model、停止确认、未取消、run 可执行，且当前路由产生的 buddy 未被 Host 指定／锁定时改派。冻结的 context.routing.decisionId／buddyRevision 对照当前 run（`workflow.py:6868–6874`），沿现有 owner／ancestor／scope 检查（`7070–7106`）。保留失败 attempt，turn 标 rejected、撤销凭据，task 回 queued、run 保持 executing；原事务释放容量，checkout／写入所有权保留。

本次 consumed continuation 按 turn_id 查出（`workflow.py:7110–7113`），恢复 queued 并清 attempt_id／turn_id／consumed_at，保留 input／helper／workspace／权限，然后 `_start_routing`。它递增 sequence（`1395–1419`），按 `workflow:<runId>:<sequence>` 新建决策并读取 queued 续做内容（`decision.py:603–635`），不增加 continuation_count。runId 不变，原决策证据保留，新选择沿单候选采纳／Router／routing_settled 与调度执行。

begin_turn 的 turn_count 与 previous 查询排除 state=rejected（`workflow.py:6256–6274`），首回合改派不再要求 continuation；checkpoint、lastAssistantMessage 和 resume 都从最近未被拒回合取得（`6280–6360,6881–6904`）。`workflow_turns` 的 UNIQUE(run_id,turn_index) 仍保留（`db.py:543`）：物理 turn_index 取所有行 MAX+1，逻辑有效次数单独用于“是否首回合”、continuation 要求和 initial／reconstructed 判定；将 `_turn_workspace` 的首回合判断同步改为有效次数（`workflow.py:6451–6461`）。拒绝历史不删除、不重排，连续多次首回合拒绝不能撞唯一键；续做改派始终使用原 continuation，不能凭被拒回合恢复会话。

中途额度打断及阶段 unknown 仍走原失败／Host 路径并进入冷却，保留部分成果；明确指定／锁定 buddy 给出冷却事实并保留选择。认证失败走原账户／harness 路径，不记额度冷却；短暂 rate-limited 分类照旧，ZCode 明确 non-retryable 的 rate_limited 仍按现有代码归 quota-exceeded（`zcode/protocol.py:350–369`）。Router 自身因额度无法作答时复用既有列表推进和无回答处理。

## 冷却表与契约

建 `provider_cooling(harness, provider, consecutive_rejections, cooling_until, last_rejected_at)`，主键 `(harness,provider)`，consecutive_rejections 的 CHECK 为整数且 >0，cooling_until 为 NULL 或 UTC，last_rejected_at 为 UTC。有行当且仅当连续拒绝次数 n>0；模型与推理强度共享该提供方冷却。每次新 quota-exceeded 拒绝将 n 加一、last_rejected_at 置本次服务拒绝提交时刻，冷却为 `min(1800×2^(n−1),86400)` 秒；n≥7 直接算 86400 秒。若本次 quotaFailure.resetsAt 晚于拒绝时刻，则截止不超过它；不晚于拒绝时刻或不可用时忽略。resetsAt 留在失败事实；固定值不做设置。

attempt 以 status=ok 结束、停止确认且 started_at>last_rejected_at 时删冷却行（`store.py:2243–2285`、`db.py:164`）。persist 接收已有 status／停止事实，删除“必须有 usage/quota”的早退（`native_observations.py:43–63`）；晚到旧成功不清新拒绝，身份与重放沿原事务。

`quota-redetect` 把 cooling_until 置 NULL、保留 n 与 last_rejected_at；无行或已为 NULL 时 cleared=false。参数 `{requestId,harness,provider}`，回复 `{harness,provider,cleared,duplicate}`；重放返回首次回执，新解除用新 requestId。操作、事件、回执沿 `service.py:577–602` 同事务提交；NULL／到期可再试，n 保留，不查询账户或模型。

投影 `providers:[{provider,coolingUntil,consecutiveRejections}]`，只列有行的提供方，NULL／过去时刻不排除。删除 quota、quotaWarnings、quotaRouting、quotaExhausted、quotaRetry、quotaRetryBuddyIds、窗口／百分比／余额／stale／单次机会与 rateLimitObservations 读数；保留 tokenUsage、计费标签、费用、nativeCode、quotaFailure.resetsAt；quotaFailure 加 rejectionPhase，历史回执不改。credits 本批不做。

Claude 的 `native_run.py:1197–1199` 已写 code=quota-exceeded、nativeCode、source、observedAt、resetsAt，`1075` 可放入共同 native_failure。将 `run_execution.py:243–244,723–724` 的 Claude 特例和 `native_run.py:847–849,866–878` 的专用失败块改成共同投影；测试确认所有拒绝 carrier 都收到这份结构化 quotaFailure 后，删除 `native_observations.py:120–121` 的 quota-rejected 回退，并将 `claude/native_run.py:1195` 的顶层终止原因统一为现有 harness-error 语义。保留原生拒绝分类，不能只删回退而让专用块继续覆盖共同事实。

候选扫描和发布检查直接读取 cooling_until>now；删除 claim 及单次重试。将被冷却排除、但满足其余硬约束／偏好的 buddy 冻结进 `routingBasis.coolingBuddies` 与 coolingCount，条目沿 `excludedBuddies` 的完整身份形状，增加 coolingUntil，并按现有 MAX_FROZEN_EXCLUSIONS 限额（`decision.py:234–281`）。没有自动候选且 coolingCount>0 时，沿 `decision.py:460–462` 的 needs-host／no-legal-candidate 程序路径，使用新原因 `all-candidates-cooling`，params 为 `{retryAt}`，retryAt 取全部冷却排除项的最早时刻，不能只在截断列表中取最小值。没有冷却排除项仍用 no-legal-candidate。

Host 经当前路由 reason／routingBasis 读取条目及 retryAt（`decision.py:1735–1742`、`workflow.py:1388`），对同一微任务稍后 reroute 或明确指定 buddy。零候选走程序边界（`router_boundary_data.py:396–399`）；Router 全冷却沿 router-quota-exhausted／_retry_at，仅将恢复时间换成 cooling_until（`69–79,351–385`），无回答 skip 照旧。到期只改变候选资格。

## 唯一的 24 → 25 升级

升级依据：0.42.0 的 `90bd0148` 与 0.45.3 的 `e006910b` 中 quota_routing 文件 blob 同为 `c799dfdbcfa81ac0fa59816b10fd425b2fde446c`；`record:122–155` 覆盖 blocked／available 并清单次标记，没有累计次数。旧 `native_observations.py:37–57` 同时接收读数与由真实 quotaFailure 合成的观测，旧 `quota_routing.py:41–58` 合并来源，不能恢复连续拒绝序列。因此丢弃旧额度派生状态，provider_cooling 初始为空，第一次新拒绝从 n=1 开始。基线仍为 `tests/fixtures/upgrade-base/metadata.json:149,318` 的 0.42.0，旧表证据在 `board.sql:333–337,411–429`。

| 转换及依据 | 转换后的读者／检查 |
| --- | --- |
| `upgrade_schema_24_to_25`（`upgrade.py:1219–1249`）内 DROP quota_current、harness_quota_observations，按新 DDL 建空 provider_cooling。 | 冷却规则的读者为 `quota_routing.py:158–191` 的替换实现、`decision.py:223–231,1360–1364`、`router.py:170–172`、`harness_health.py:64–82`；检查新库／升级库表与索引相同、旧表不存在。 |
| 按 `db.py:515–544` 新 DDL 重建 workflow_turns，仅扩展 state CHECK，复制全部旧行并保留主键、唯一约束、外键。 | `workflow.py:6256–6293` 的回合计数／previous／resume，`7055–7057,7084–7087` 的结算；检查旧 turn 内容与引用不变，新 rejected 可写、非法 state 被拒。 |
| 在 `_catalog_payload` 的同一 warnings 转换循环里遇到三种旧额度提醒就丢弃：HARNESS_RATE_LIMIT_REPORTED、HARNESS_QUOTA_LIMIT_REPORTED、HARNESS_QUOTA_NEAR_LIMIT（`upgrade.py:69–73,294–305`）。沿已有转换调用处理 response.catalog 与 evaluation_catalog（`324–350`）。 | 循环自身严格校验未知代码，不能只删映射；`catalog_store.py:25,76–80` 会重放 response_json，`current:31–50` 读取 catalog payload，检查带这三种提醒的旧库能升级、其他提醒保留。（2026-10-19 Host 验收时核对：0.42.0 到 0.45.3 没有写入者把这三个代码存进目录记录，日常看板上也一条都没有，这项转换随微任务三删去。） |
| `_upgrade_router_snapshot` 在 routingBasis 上 pop quotaRetryBuddyIds（`upgrade.py:816–851`），沿已有循环作用于 decision_requests.requested_json 与 router_requests.snapshot_json（`1023–1029`），含 baseInput 递归路径。 | `decision.py:1710,1740`、`workflow.py:1388` 读取 request 中的 routingBasis；`router_boundary_data.py:400–423` 读冻结 snapshot。检查旧机会名单不再经当前投影出现。 |
| `_decision_reason` 将旧“selected quota retry was consumed”映射到现有 bound=constraints-or-quota（`upgrade.py:951–961`），不再产生 quota-retry-consumed。 | `decision.py:1735` 的 reason_from_json 与前端 `i18n/{zh,en}/shell.ts:48–50` 的 bound 显示；检查升级原因参数属于当前枚举。 |
| 删除 commands 中 kind=quota.redetect 的旧回执行；其他回执原样沿现有升级。 | `service.py:591–598` 与 `store.py:360–362` 的回执查找／返回；旧单次机会回复不再被新命令读到，不加第二种回复解析。 |

attempts.result_json、catalog_observations.harnesses_json、decision input、dispatch 文档留作原始证据，删除其额度消费路径，当前状态只读新表；原有其他升级照常。所有转换同事务、外键检查后写 schema=25，失败回滚（`upgrade.py:1225–1249`）。补空／满旧表、多个 limit、manual／consumed、resetsAt、提醒、quotaRetryBuddyIds、旧原因／回执、rejected／约束测试；比较升级库与新库结构。0.45.3 安装演练只用旧服务接受的字段，契约仍为 0.46.0。

## 控制台与参考文档

仅在 coolingUntil>now 时显示“冷却中 · 约 {time} 可再试 / Cooling down · try again around {time}”与“解除冷却 / Clear cooldown”；平时该处不写文字。详情以提供方标识分行，不画窗口、百分比、余额、过期状态与进度条；解除成功显示“已解除冷却 / Cooldown cleared”，无操作显示“当前未在冷却 / No active cooldown”。Intl 显示时间，到期刷新。

删除近限与过期徽标；保留 Router 配置故障等已有提醒。实际中途失败仍显示“额度耗尽 / Quota used up”及部分成果；新原因显示“所有候选都在冷却，约 {retryAt} 可再试 / All candidates are cooling down; try again around {retryAt}”，详情展示 routingBasis 中被冻结的条目。解除沿已有写权限、CSRF、requestId 与未知回复处理，不调用账户查询。核对双语、390 窄屏、键盘／读屏、整行展开、选中项保持和解除后刷新。

Host 同步参考：`docs/reference/harnesses.md:3,31–59`、`decision.md:67,71,87–91`、`evaluation.md:3,32,54,67,73,79,121`、`workers.md:64–66,142–146`、`claude-code.md:21,27`、`codex.md:43`、`workflow.md:152`、`console.md:25,49,65–69,87–89`、`cli.md:118`、`operations.md:269–273`、`architecture.md:53–54,92,150,169,185,203,209`、`harness-integration.md:23–27,79,93,147`；改运行契约为冷却／改派，删读数与单次机会。入口 `skills/hey-my-buddy/SKILL.md:40`、`docs/README.md:91`、`README.md:39`、`README.zh-CN.md:39,123` 随 owning 页更新并保持双语一致；历史安装、原生版本与未验证范围如实保留。`usage.md:181` 的调度约定、`console.md:53` 的并发消耗说明保留。

## 三个微任务与检查

顺序一 → 二 → 三；每批删除被替代的代码、测试与 fixture，中间不安装。存储与升级同归二。测试用正式 runner、私有根并清除继承的 HMB runtime／agent 凭据及虚拟环境变量，不手工起测试或真实 harness。

### 一：harness 拒绝阶段与删除读数采集

写入范围：清点中的四家 harness 文件（含 account_probe、session_records）、run_contract.py、registry.py、`S/buddy/roles/{run_execution,schema_worker}.py`、`S/protocol/usage.py` 及对应测试／fixture。输出 rejectionPhase，统一 Claude quotaFailure，删除读数查询／采集／搬运；保留计费、token 与分类，补四家活动／截断及两种 carrier 检查。

检查文件：`T/protocol/{test_usage.py:121–265,test_billing_quota.py:25–259}`；`T/buddy/harnesses/{test_quota_failures.py:13–188,test_adapter_usage.py:72–251,test_run_contract.py:83}`；各家目录中的 `codex/{test_native_run.py:163,934,1085–1099,test_codex.py:557–568}`、`claude/{test_claude_protocol.py:47–163,test_claude.py:254–440,test_claude_worker.py:86–98,test_native_run.py:583–585,845–853,1365}`、`zcode/{test_zcode_protocol.py:619–686,test_zcode.py:293–303,test_zcode_tool_evidence.py:88,158–183}`、`dsh/{test_native_run.py:287–297,test_session_records.py:155,276}`。

改写 fixture：四家 `fixtures/native-usage-*.json`（Codex:73–122、Claude:113–137、DSH:116–123、ZCode:165–176），`fixtures/run_contract/codex-payloads.json:23–44`；`codex/fixtures/mock_quota_codex.py:13–15`、`mock_codex.py:51–87,486–488`；`claude/fixtures/fake_claude.py:136–275`、`fake_claude_usage.py:104–109`；`dsh/acp/fake_agent.py:795–798,1074`、`fixtures/workflow_agent.py:7–26,225`；`zcode/fixtures/mock_zcode.py:502–509`、`mock_zcode_usage.py:118–120`、`T/protocol/fixtures/mock_readonly.py:152`。

对本批上列文件运行 `uv run --frozen python -m hey_my_buddy.cli.checks <文件>`，例如 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/buddy/harnesses/test_quota_failures.py`。

### 二：服务冷却、改派、存储与升级

依赖一。写入范围：`B/routing/{quota_routing,decision,router,router_boundary_data}.py`、`evaluation/{native_observations,evaluation}.py`、`catalog/catalog_store.py`、`service/{harness_health,service}.py`、`store/{db,store,upgrade}.py`、`tasks/{workflow,macro_task_deletion}.py`；`S/protocol/reason-codes.json`、`S/cli/{cli_help,cli_views,blocking}.py`、`S/console/{reads,read_cache}.py`；对应 Python 测试及升级装载辅助文件。存储与所有指定转换同批完成；合同常量保持不动。删除旧 single-use 实现及测试 `test_quota_retry.py`，新增 `test_provider_cooling.py`。

检查文件（以下相对 `T/blackboard/`）：`evaluation/{test_native_observations.py:23–254,test_other_native_observations.py:147–570}`；`routing/{test_quota_retry.py:38–384,test_nonretryable_quota.py:19–113,test_current_router.py:173–184,291–294,test_routing_health.py:86–211,test_selection_publication.py:34–85,test_single_router_claims.py:51–186,test_single_router_requests.py:210–212,test_router_boundary_integration.py:205–229,646,test_router_failover_integration.py:154–661,test_router_list_health.py:170–240}`。旧机会检查替换为 test_provider_cooling.py。

同批运行：`T/blackboard/tasks/{test_workflow.py:300–313,test_host_workflow_worker.py:27–185,test_model_service_concurrency.py:43,310–324,test_macro_task_deletion.py:635,675,715–717}`、`store/{test_store.py:149,test_upgrade_fixture.py:1019,1040,1851}`、`service/{test_harness_health.py:961–1024,test_console_reads.py:80–96}`、`catalog/test_catalog_observations.py:98,140`；`T/install/test_upgrade_service.py:31`、`T/cli/{test_blocking.py:333,test_cli_views.py:1}`、`T/console/test_console_macro_task_cache.py:1`。

运行 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/blackboard/routing/test_provider_cooling.py`；其余上列文件同命令运行，test_quota_retry.py 除外；另跑 `tests/python/protocol/test_contract_version.py`。

验收：A 拒绝→B 成功同 runId、新 decision；多次首回合拒绝无 CONFLICT／唯一键冲突；续做保留 input／helper／workspace；中途／unknown／指定不改派；解除保留 n、成功删行、晚到旧成功／重放不污染冷却；过去 resetsAt 忽略；全冷却、Router skip、升级回滚。

### 三：控制台状态与合成预览

依赖二。写入范围：清点中的 `F/{types,api,quota}.ts`、相关 buddies／task-detail 文件、`i18n/{zh,en}/`、前端测试与 fixtures、`apps/console/browser/{catalogue,catalogue-gestures,task-detail}.check.ts`；`tests/probes/macro_task_console_preview.py` 及预览 fixture 清单、`S/console/assets/`。删除 quota.ts／quota.test.ts，旧窗口 UI 和提醒；用新 providers／解除回复作为唯一格式。

检查 `F/buddies/{harness-quota.test.tsx:37–319,harness-wire.test.ts:115–134,harness.test.ts:73–230,attention.test.ts:19–59,catalogue-actions.test.tsx:159–160,384,411,catalogue-gestures.test.tsx:37–57,english.test.tsx:49–54,105,navigation-page.test.tsx:58–71,189}`、`api.test.ts:103`、`preview-contract.test.ts:137–139,258–302`、`task-detail/{parse.test.ts:284–289,TaskDetail.test.tsx:443}`；`browser/{catalogue.check.ts:13–17,52,72–73,catalogue-gestures.check.ts:83,task-detail.check.ts:295}`。

运行 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/cli/test_host_preview.py tests/python/console/test_console_preview.py tests/python/blackboard/service/test_console_reads.py`（预览直接落点 `test_host_preview.py:194–246`）；再运行 `npm --prefix apps/console test` 与 `npm --prefix apps/console run test:browser`。前端两项在最终源码执行；使用已有依赖，不运行 npm exec／npx、不联网安装，缺项则报告。

集成运行 `uv run --frozen python -m hey_my_buddy.cli.checks`、`npm --prefix apps/console test`、`npm --prefix apps/console run test:browser`，覆盖清点中受旧字段牵连的其余配置、controller、discovery、交付及调度检查。本次仅运行 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/cli/test_repository_hygiene.py`，结果随 Worker 交付。