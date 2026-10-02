# ADR-021 逻辑第二阶段收尾

分支 socu/adr021-logic-stage2，Host 独立 worktree；未推送，未修改 main、socu/buddy-core 或其他会话检出，未开始新 L6、L7 及以后。合入最新 core 26f9dd0 的方向与术语决定。执行计划 [adr021-logic-stage2.md](../design/adr021-logic-stage2.md)，模块记录 [L4](l4-adr021.md)、[L5](l5-adr021.md)、[移出的 L6](l6-adr021.md)。备查分支 socu/adr021-review-carrier-reference 固定在 15be750，保留旧通道、静态证明及全部历史专属检查，没有创建运行服务或安装它。

实际交付是 L4 全部与 L5 统一工具证据及四个 harness 已验收的事件投影。DSH/ZCode 的专用只读 Node 桥、Python controller、ZCode 受限协议和静态证明及专属测试已移出，真实审阅资格返回 readonly-worker-carrier-unimplemented。普通 Worker、fast、公共投影与黑板唯一判定保留。最新 ADR 改为复用 Worker 载体，另起阶段先设计；本阶段不再原生检查或静态核对。停止时没有原生进程运行，新准备的 DSH 第三包/ZCode 第五包未执行。

移除后 101 项资格/公共投影/fast/发布回归通过；223 项 Router/打包首轮只有 12 个 review 切换场景依赖已撤下的真实资格而失败，改为全测试期间使用私有 capability shim 后 90 项切换回归通过，原断言和故障情形未删。免费资格/健康 7 项再次通过。移除后首轮完整检查只有 test_current_router 的 3 个旧资格依赖失败，其余 157/158 Python 文件和 110 Node 通过；32 个解析场景保留，私有资格夹具迁移后与真实不可用防护合计 42 项复查通过。最终完整检查通过：2316 Python（skip 1，158/158 文件）、110 Node，测试私有根回收成功；命令 uv run --frozen python -m buddy.checks，Node 24、两并发，检查期间 worktree 冻结。日志 tmp/adr021-stage2/removal-full-check-2.log。Console 最小适配已有 659 项、typecheck/build 通过，此次不改前端；原始日志 tmp/adr021-stage2/。

当前原生证据：Codex 本阶段一次通过（16.289 秒，2 个 execute 调用）；Claude Code 一次完成真实读取和结构化回合，但 StructuredOutput 被分类为 other，且未回传随机 marker，检查失败（13.048 秒）。DSH 两次在模型前失败，ZCode 四个已执行包一包模型前失败、三包进入模型但未完整通过；最后一包已经有 Read start/end。所有已执行包的输入未改、实际停止/框架回收确认，读取字节与未提供的 applied-effort/served-model 证明保持未知。失败不改写为验收，也不为通过滤掉事件或改变 ADR。

主要模块提交：L4 基础 98a9716/94f25cd/dc2c047/cdfc1b3/a841dad，加 2019db0 的 note 帮助；有序列表 9be9aa1/9d2d64b/7195fdf/3e836e0/2855666/f408fa2/3b41076/20ad8ba/8df231e/d595e30/e41a932/a8886d8。L5 公共事实与四投影 0fe9491/e3dd4ea/621ae33/55f0302/a867ffe/e184e2a，发布接线 4a2da11，后续投影 9755ab7/9ef733b/f421c46。移出的 DSH 通道 7e5e765/70e1fd4/639163b/df8c222，ZCode/静态从 8a436df 至 4b46316 的相关实现及 15be750；完整保存在备查分支。方向合入 503190d、收尾计划 7471bb5、移除与资格/回归 87f042b；8aabc08 只补四 harness 探针与 Claude 的真实零纠正事实，未重做原生工具或沙盒。下面的完整列表包含过程提交；已移出的代码不算最终交付。

设计选择和中途修订：单个 Router 改为有序列表与默认 600 秒重试，旧两位置均保留；current_router 共用一个入口，跳过从不可变记录推出，不新增熔断状态；同请求每项独立 dispatch，实际停止确认后推进，全局模式/预算不降级；G 拆请求边界与认领/发布，R4 拆纯分类与推进，R5 拆纯模板与读取；四种适配器只记录事实，黑板唯一判定。早先给 DSH/ZCode 建专用通道和厂商代码静态证明的选择已被用户新决定撤下，后续复用 Worker。只改变人类用词为宏任务/微任务，接口/存储重命名留 L14，SKILL/Host 指南/README 留 L16，界面设计留 U1/U6。未做日常迁移、登录/退出、密钥修改或凭据文件检查。

微任务时长取黑板 durable Worker turn 的创建至实际停止结果跨度，含准备；不以 Router、Host 审查或 monitor 等待时长代替，没有 Worker 回合为 0。所有本宏任务已终结的受管检出按验收/失败结论回收，子分支删除；不碰其他宏任务的检出。收尾 R8A–R8E 由 Host 单写，没有新增 Worker 微任务。只读审查使用用户允许的 Codex subagent；它们不承担写入或原生检查。

| 微任务 | 时长 | 结果 |
| --- | ---: | --- |
| L4-A | 0 分 0 秒 | 失败/拒绝结论记录并回收 |
| L4-B | 0 分 0 秒 | 失败/拒绝结论记录并回收 |
| L4-G1 | 18 分 38 秒 | 验收并回收 |
| L4-J | 12 分 24 秒 | 验收并回收 |
| L4-I | 13 分 16 秒 | 验收并回收 |
| L4-G2 | 22 分 24 秒 | 验收并回收 |
| L4-H | 24 分 9 秒 | 验收并回收 |
| L5-0A | 30 分 36 秒 | 验收并回收 |
| L5-0B-CODEX | 45 分 20 秒 | 验收并回收 |
| L5-0B-CLAUDE | 41 分 56 秒 | 验收并回收 |
| L5-0B-DSH | 47 分 28 秒 | 验收并回收 |
| L5-A | 28 分 16 秒 | 验收并回收 |
| L5-0B-ZCODE | 45 分 27 秒 | 验收并回收 |
| L5-B | 47 分 25 秒 | 验收并回收 |
| L6-A | 42 分 17 秒 | 失败/拒绝结论记录并回收 |
| L4-R1 | 14 分 22 秒 | 验收并回收 |
| L6-A2 | 23 分 29 秒 | 失败/拒绝结论记录并回收 |
| L4-R2 | 58 分 16 秒 | 验收并回收 |
| L4-R3 | 43 分 33 秒 | 验收并回收 |
| L4-R6 | 61 分 26 秒 | 验收并回收 |
| L6-A3 | 58 分 9 秒 | 失败/拒绝结论记录并回收 |
| L4-R4B | 33 分 58 秒 | 验收并回收 |
| L6-A4 | 85 分 47 秒 | 失败/拒绝结论记录并回收 |
| L4-R5B | 36 分 3 秒 | 验收并回收 |
| L6-A5 | 55 分 1 秒 | 验收并回收 |
| L6-B | 33 分 12 秒 | 失败/拒绝结论记录并回收 |

提交清单按本分支 first-parent 顺序，core 合并只发生在 Host 分支；验收填录数字的最后提交另见最终报告。

| 提交 | 内容 |
| --- | --- |
| 89a498b | docs: design ADR-021 logic stage two execution plan |
| e63cdc5 | Merge branch 'socu/buddy-core' into socu/adr021-logic-stage2 |
| b51406f | docs: align stage-two plan with sandbox tiers and central evidence judgement |
| 2c79165 | docs: clarify installed workspace input selection for delegations |
| d633fd8 | Merge branch 'socu/buddy-core' into socu/adr021-logic-stage2 |
| 98a9716 | feat: define single Router settings and explicit upgrade conversion |
| 2019db0 | fix: report finalization notes as required in CLI help |
| 94f25cd | feat: expose model-free review eligibility and native sandbox facts |
| 2039650 | docs: use native DSH tools and ACP evidence per ADR-023 |
| dc2c047 | feat: publish one Router setting without implicit legacy conversion |
| 967ba8b | docs: add Router timeout health diagnostics and authorized outage recovery |
| c70acba | docs: allow minimal console adaptation after its fixed data contract |
| f3ad69d | ws-82016fe93b2394629020e513877bc535 output 351a2e52af24ff9c735f207c8bd1789723c1520fdeaedda5d9140caf4a09fa5c |
| 7f8c33c | ws-318c0982c1266c82b8a9c9d9e9f6248b output 7f19052ca1333322be9ee446567b1439e85899f499c6e0aa0f84bfd12bf62ffb |
| c793dab | fix: adapt console parsers and controls to the single Router contract |
| 34d2ca6 | docs: isolate paid verifier retirement from claim and publication work |
| cdfc1b3 | feat: recheck frozen Router claims and fence answer publication |
| a841dad | refactor: retire paid review verification while preserving native safeguards |
| 23cf67e | fix: integrate single Router fixtures and document L4 validation |
| 4f25684 | fix: preserve routing timeline checks under the current Router facts |
| d450a11 | docs: record passing L4 checks before starting shared tool evidence |
| b9dfad0 | docs: correct native DSH guard API after inspecting installed public types |
| 0fe9491 | feat: add common ACP tool evidence and claim-frozen sandbox facts |
| e3dd4ea | fix: retain malformed tool facts and reject incomplete native identities |
| a84a9a7 | docs: bind sandbox policy to attempts and refine native evidence edge cases |
| 37b1599 | feat: bind no-tool controller evidence to the owned attempt |
| 2f3891e | test: parse raw console preview fixtures without temporary Router conversion |
| a58f59a | docs: decouple native DSH bridge from parallel event projections |
| 621ae33 | feat: project Claude native read-only tool facts into common evidence |
| 55f0302 | feat: project Codex typed and raw native tool facts |
| a867ffe | feat: report DSH no-tool native stream evidence without fabricated calls |
| e184e2a | feat: project ZCode scheduled and settled tool facts |
| 7e5e765 | feat: drive restricted native DSH Agent turns for read-only calls |
| 07e9f3c | fix: preserve late native calls and accurate incomplete evidence counts |
| 4a2da11 | feat: judge native Router tool evidence at blackboard publication |
| 40851ab | docs: fix ZCode read-only protocol seam and local qualification checks |
| 9ee254e | fix: bound DSH native answers and report pre-model refusals accurately |
| c4814db | docs: define Codex native projection aliases before evidence integration repair |
| a3be23e | fix: correlate Codex execution projections and quarantine tool namespaces |
| 0ed0b31 | docs: specify one-shot restricted-tool native acceptance probe |
| 5500912 | Merge branch 'socu/buddy-core' into socu/adr021-logic-stage2 |
| 3c1deea | docs: redesign stage 2 for ordered Routers and actionable Host boundaries |
| a16d7fa | docs: align legacy test migration with Router-list behavior |
| 70e1fd4 | feat: wire DSH restricted read-only controller |
| 639163b | fix: preserve DSH stop uncertainty and verify the official review stack |
| 2006b86 | test: prepare one-shot native read-only probes and record L5 evidence |
| 459f1a9 | docs: pin ZCode protocol repair contracts from independent review |
| 9e59caa | test: preserve native eligibility and publication fault cases after L5 |
| f766887 | docs: record passing L5 full checks before Router-list integration |
| 9be9aa1 | feat: define ordered Router settings and preserve both legacy slots |
| a19c10d | feat: resolve the current Router from ordered settings and outcome records |
| 9d2d64b | fix: derive Router skip history from immutable outcomes and indexed claims |
| 3ad9ff9 | Merge latest core decisions before ordered Router dispatch work |
| 10b626e | docs: pin fail-closed ZCode static qualification repair and current Router choices |
| a84f931 | docs: split pure Router outcome classification from lifecycle advancement |
| 46794f6 | test: migrate setting fixtures to ordered Router lists without removing fault scenarios |
| e802ce6 | feat: classify Router outcomes before sequential failover |
| c78c88d | docs: pin frozen timeout and publication sequencing without intermediate Host boundaries |
| 5704d16 | docs: preserve workflow decision links while advancing internal Router tasks |
| befd228 | fix: preserve request-frozen Router deadlines across ordered resolution |
| 40f1b13 | test: exercise the shared current Router entry and preserve frozen routing checks |
| 099cec8 | docs: separate pure Host boundary templates from workflow wiring |
| 1e2f3f8 | feat: prepare fact-based Host routing boundary and continuation templates |
| 7195fdf | feat: freeze ordered Router requests and claim independent dispatch tasks |
| c6d6386 | docs: record fixed-artifact Router dispatch review corrections |
| 3e836e0 | fix: fence Router preflight drift and final dispatch input limits |
| 8df231e | feat: report each Router health and bind ordered settings in the console |
| 8a436df | ws-85ef5161d165602c1f23c32d1e982078 output ecf9c37409382dec2c711b070c13f2f7a4e88b68f4c16556d86bfc58a2524137 |
| d0cf78b | ws-0955b913c946e083f6a14e57a12a73d1 output 637f50e37df5ca3a5911fcf06a4d6c5faf4c7b6cc12c75cd699a2ad1d5ec8d55 |
| 9975af8 | ws-c9b1f1347a1e66e1873a56fd6d0d9c1b output 52e24fe9854b1e3e1de9f50c7b658d5bb816e9f43c149860441f171d4576eeec |
| f48f805 | docs: pin lexical binding corrections for ZCode qualification after fixed review |
| d595e30 | fix: preserve current Router absence and allow temporarily ineligible list entries |
| 786bd1d | docs: run Host boundary reads independently of Router completion writes |
| 2855666 | feat: advance stopped Router no-answer outcomes within the frozen request |
| 09c430e | docs: record Router completion artifact acceptance boundary |
| f408fa2 | fix: normalize malformed native errors before recording Router failover |
| 3b41076 | feat: expose frozen Router trials and actionable Host routing boundaries |
| 20ad8ba | fix: retain queued Router preflight failures in Host trial evidence |
| 04b778a | feat: retain sealed ZCode lexical qualification repair pending counterexample fixes |
| 04bac1b | docs: split ZCode static counterexample repairs by lexical and proof ownership |
| e41a932 | fix: preserve ordered Router history and original receipt regression coverage |
| 7385617 | fix: bind ZCode proof dependencies and parse real restriction metadata |
| 7d1c2cd | fix: retain proof writes with unreadable destructuring defaults |
| 685556f | docs: describe ordered Router failover evidence and rebuild minimal console adaptation |
| 58de379 | docs: record ordered Router acceptance and preserved guard regressions |
| eeae788 | fix: close comment writes optional mutations and enum binding gaps |
| a8886d8 | fix: preserve health on malformed receipt codes and extend historical summaries |
| 76370df | fix: reject truncated proof expressions and preserve prefix updates |
| d079826 | docs: fix ZCode entry eligibility cache and controller ownership before dispatch |
| 05fea3b | fix: distinguish prefix updates from adjacent postfix arithmetic |
| f7d2f04 | docs: bind ZCode start qualification to its actual command environment |
| 428cb58 | fix: bind ZCode lexical scopes and exclude regexp decoy calls |
| 688e978 | docs: fix remaining ZCode lexical variants and reverse-scan cost before acceptance |
| 9e054c5 | fix: inherit virtual ZCode scopes and bound reverse lexical lookup cost |
| 83b94cf | docs: record ordered Router complete check and ZCode lexical acceptance |
| 0eea5ca | fix: run approved native probes inside framework-owned private roots |
| e67a1ce | docs: finish failed ZCode partial with environment and EOF corrections |
| 4cae8a4 | feat: retain stopped ZCode controller partial for Host completion |
| e73979f | fix: bind ZCode review to controller environment and retain complete failure facts |
| 6eac76a | fix: preserve malformed ZCode facts and bind final receipts to actual shutdown |
| 4b46316 | fix: finalize ZCode tool receipt after the last cancellation gate |
| fbccd32 | docs: record ZCode controller acceptance and pre-model probe failure |
| e1f5a40 | docs: record paid ZCode failure and native tool lifecycle projection gap |
| 9755ab7 | fix: project native ZCode tool lifecycle and correlated intermediate frames |
| 87c9db4 | docs: record native ZCode failure and complete stage 2 commit and delegation ledger |
| 55ee226 | docs: design bounded native startup and lifecycle follow-up repairs |
| 767a7a0 | docs: record authorized native repair and verification scope |
| df8c222 | fix: wait for native DSH factory and provider startup within the review deadline |
| 9ef733b | fix: correlate ZCode native tool telemetry and completed batch acknowledgments |
| 3ce68a4 | test: isolate native batch counters and ambiguous optional turn association |
| 2f4c6dd | docs: design authorized native checks for existing sandbox harnesses |
| f421c46 | fix: preserve conflicting canonical ZCode tool outcomes in evidence |
| 569602f | docs: bound native constructor readiness and persistence metadata fixes |
| 8aabc08 | test: verify four native review harnesses with frozen sandbox-class evidence |
| 15be750 | fix: await native constructor completion and retain bounded failure diagnostics |
| 503190d | Merge branch 'socu/buddy-core' into socu/adr021-logic-stage2 |
| 7471bb5 | docs: close stage 2 around Router and tool evidence and defer Worker-carrier review |
| 87f042b | refactor: remove dedicated DSH and ZCode review channels and defer Worker-carrier review |
| 83ab39f | docs: record retained stage 2 work and archived review channels with micro-task ledger |
| 378cd05 | test: preserve current Router review scenarios with private deferred-carrier capability |
