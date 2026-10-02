# ADR-021 逻辑第二阶段收尾

分支 socu/adr021-logic-stage2，仅在 Host 独立 worktree 工作；未推送，未修改 main、socu/buddy-core 或其他会话的检出，未开始 L7 及以后。执行计划 [adr021-logic-stage2.md](../design/adr021-logic-stage2.md)，模块记录 [L4](l4-adr021.md)、[L5](l5-adr021.md)、[L6](l6-adr021.md)。

L4 有序列表与 shared current_router、同请求停后切换、分 Router 健康和 Host 四类边界信息已完成；L5 四适配器公共事实/黑板判定与 DSH 原生工具桥已完成；L6 受限原生协议与 controller 接线、免费资格已完成模拟和 Source 验证。DSH 原生未验证，待单次批准；ZCode 一次付费模型回合的验收失败，后续免费修正没有再调用模型，完整原生通过证据仍缺。

最近一次完整检查（补原生生命周期前）通过 2456 Python（skip 1，164/164 文件）、125 Node；Console 659 项、typecheck/build 通过。原生生命周期免费修正后的最终结果将在检查退出后填录；运行期间 worktree 冻结。原始日志 tmp/adr021-stage2/。

委派时长取黑板 durable turn 的创建至实际停止结果跨度，含其准备；不以 Host 等待或 monitor 运行时长代替。所有已终结的检出已按验收/失败结论回收，失败或取消不标为验收。

| 委派 | 时长 | 结果 |
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

提交清单采用本分支 first-parent 顺序；core merge 只合到 Host 分支。L4 基础实现从 98a9716 至 d450a11，列表补充从 9be9aa1 起含 R2/R3/R4/R5/R6 与 e41a932/a8886d8；L5 从 0fe9491 至 f766887 的公共事实、四投影与 DSH 实现（其中穿插 L6 设计/core merge）；L6 为 8a436df/d0cf78b/9975af8/04b778a 的保留输入、A5/A6 后续修正，以及 4cae8a4/e73979f/6eac76a/4b46316/9755ab7 的协议/controller，未把早先拒绝的委派误写为已验收。下面保留全部计划、实现、测试、记录提交，模块以标题和相应记录为准。

| 提交 | 内容 |
| --- | --- |
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
