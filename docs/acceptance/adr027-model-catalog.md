# ADR-027 实现步骤记录

本记录由 Codex Host `codex-adr027` 维护，范围仅为 ADR-027，完成后交 Claude Code Host 验收。实施分支为 `socu/adr027-model-catalog`，独立工作树为 `~/.codex/worktrees/adr027-model-catalog/hey-my-buddy`，起点为 `socu/buddy-core` 的 `d8a80b7497034aded4950265ba9f531911c6a4f7`。最终验收提交前合入当时最新的 `socu/buddy-core`。

## 执行计划

黑板在 `blackboard/catalog/catalog.py`、`catalog_store.py`、`blackboard/service/harness_health.py` 与 `service.py` 落实可靠读取、模型消失确认、明确指定时的有限重读和健康检查触发刷新。待确认状态及首次缺席时间沿用 SQLite 的 `meta`，按 harness 与模型身份保存，读取结果继续使用 `catalog_observations`、`catalog_current` 和 `evaluation_catalog`；不增加表、列或 schema 版本。发现结果的 `accountStatus` 取 `confirmed`、`unknown`、`not-applicable`，由黑板决定是否采信；缺少该事实的原生读取按不明处理。模型状态输出为 `catalogStatus`（`available`、`pending`、`unavailable`）和 `pendingSince`。一小时确认期、六小时保质期使用具名常量。

四个 `buddy/harnesses/*/native_run.py` 仅增加发现会话里的账户状态事实；Codex 同时放宽执行时缺席模型的核对，保留已知模型的推理强度校验，记录当场缺席事实并保留原生拒绝的错误。`buddy/roles/run_execution.py` 只在传递这些事实确有需要时最小修改，避免扩大与 ADR-025 第五步的重叠。控制台模型列表读取黑板给出的状态，标出待确认及其起始时间。

使用一个新的宏任务，所有首次微任务提交省略 buddy 配置，由路由决定。微任务 A 负责黑板规则及受影响测试；微任务 B 负责四个 harness、Codex 执行核对及受影响测试；微任务 C 负责控制台状态显示及组件测试。各 Worker 使用独立受管工作树，只改授权范围，禁止主动删除任何文件或目录。Host 复核固定产物与实际检查，范围内代码、测试或行为缺陷打回原 run；仅一句话的记录更正由 Host 直接改并登记。确需 schema 提升时先停止并说明。

Host 整合后以默认并行数运行一次 `uv run --frozen python -m hey_my_buddy.cli.checks`；只改记录不重复检查。测试使用私有状态与运行时根，清除继承的运行时和 Worker 凭据。真实验证只通过已安装的 Codex 原生发现读取一次模型及同会话账户状态，不设置 `CODEX_HOME`，沿用现有登录，不读取凭据文件内容、不登录/登出、不改密钥、不调用模型。日常运行时不安装或升级。验收后的任务根只由 Host 按确切路径回收。

## 验证编号

- C01：账户状态不明的读取不替换已有目录，缺少账户事实不被当成可靠读取。
- C02：可靠读取新增模型立即生效；模型首次缺席进入待确认，仍在路由边界内，明确指定照常接受。
- C03：首次缺席未满一小时仍可用；满一小时后的又一次可靠读取仍缺席才不可用；不明读取不推进确认。
- C04：待确认或不可用的模型在可靠读取里重新出现恢复，首次缺席时间清除。
- C05：明确指定且缺失或不可用时先有限重读一次；仍拒绝的错误带目录读取时间与刷新命令。
- C06：目录超过六小时后在下一次健康检查重读，未知读取不重置成功读取时间。
- C07：Codex 当场清单缺少所选模型仍交原生执行，记录缺席事实；原生拒绝时保留原生错误。
- C08：Codex 清单里存在模型但不支持所选推理强度时继续拒绝；其余 harness 选择校验保持原生机制。
- C09：进入待确认、恢复、确认不可用各产生事件，重复读取不重复状态变化事件。
- C10：控制台模型列表标出待确认与起始时间，正常可用及不可用状态显示正确。
- C11：四个 harness 都报告账户状态事实，已安装 Codex 的一次无模型调用发现得到 `confirmed`。
- C12：测试 ID 基线与最终集合差异可核对；完整检查结果、整合基线及未验证边界登记。

## 执行与验收证据

首次提交的代码树为 `713425ad58a6d3707b0255d501d1f4062cfa7280`，该次整体验收已被 Claude Code Host 退回；它包含当时最新 `socu/buddy-core` 的 `55fff85fbe4ddce8a88eccd898a977a9bcd16a24`；Host 工作树保留供审查。方案和验收由 `codex-adr027` 负责，新宏任务为 `obj-9f62bcab-3a1c-4878-ba75-38a10337ee4a`，三个首次提交均经过路由，省略 buddy 配置。schema 仍为 15，日常运行时保持 0.27；ADR、CONTEXT、AGENTS、README、待办与参考文档由 Claude Code Host 维护，本分支相对最新核心分支的这些文档无改动。

待确认条目最终在 `meta` 的 `catalog-pending:<adapter>` 保存模型身份、`since` 和最后合法 `efforts`，兼容实施早期的纯时间字符串；`catalog-read-at:<adapter>`、`catalog-account-status:<adapter>` 保存可信读取事实，`catalog-scan-after:<adapter>` 复用 180 秒扫描窗口约束未知读取后的重试。旧目录缺少新增 meta 时从既有采纳记录取时间，未知读取不冒充成功读取。恢复与首次缺席均按最后可信配置的完整身份和合法推理强度处理；账户 source 或 credentialRevision 改变时清除当前目录指针、确认窗口和读取事实，旧配置保留 profileId 与 enabled 意图，但不能用于新身份。账户、目录和健康检查的状态由 Python 服务在既有 SQLite 事务中判断，未增加表、列、计时器或用户设置。

| 微任务 | runId | 最终固定提交 | 最终整合记录 | Host 结论 |
| --- | --- | --- | --- | --- |
| A：黑板 | `543174ce-34b9-4a26-aeb1-b6537db0152b` | `55254f366c12dcf428e8fe0fcdd46469b445b081` | `int-7bcede94-3043-456c-aa35-a37a1663343d` | accepted |
| B：harness/Codex | `4e713942-3408-4407-ab6d-f457c5b0375f` | `f8e2ca1a66963d33ea26593ed7bbe59eee41de34` | `int-1df973fd-abe4-4c88-9349-39842f06e927` | accepted |
| C：控制台 | `4f9dfdf2-a795-4f61-8493-2b134cb900c2` | `d090e88e342405a1ada0d72a57f85197da7a8958` | `int-024ce269-e345-4c10-ace6-dcc11de075a2` | accepted |

三个最终整合记录均为 `verified`，分别匹配 15、16、8 个目标路径，无 missing、differing 或 unrecorded 路径；每个 acknowledgement 都绑定最终固定 artifact 和整合记录。机器可读摘要见 [adr027-verification.json](adr027-verification.json)。微任务验收与 Claude Code Host 对本步骤的整体验收分别记录；首次整体验收已退回，本轮重新提交尚待验收。

A 的实现缺陷均返回原 run：先修扫描限流挡住过期刷新、冷启动未知目录永不过期和旧服务 fixture；再修重读导致健康失效时漏掉拒绝上下文、健康恢复与目录事实混用、旧目录读取时间；随后修退休 effort 被恢复、路径重查及初始化抹掉目录事实；最后修 source/credentialRevision 切换时采用旧账户目录。最后一轮还补足退休 effort 与模型恢复事件的组合。Host 保留失败用例并逐次核对转绿，没有改写早期失败结果。首轮摘要漏算新测试文件的统计由 Host 按固定 diff 登记为 14 个文件、955 插入/49 删除；后来步骤增加的差异另以最终代码树为准。

A 的第 4、5 回合分别在 GLM-5.3/max 和 GLM-5.3-Flash/max 上收到供应方 `rate_limited / 1308 / 429 / stream / not-retryable`；Host 用完整配置在原 run continue，保留失败事实与部分产物，后来切换到 `codex / openai / gpt-6.1-sol / high`。第 6 回合报告代码及 92 项检查，但 App Server 正常关闭未获确认，执行仍为 failed；监督器随后确认停止，部分产物 `60729030` 经 Host 固定审查与整合，未当作成功交付。第 7 回合完成账户隔离修正，native 回合 completed、自己和后代停止均已确认，最终输出才被 accepted。未修理或升级日常执行器的退出行为。

B 的套餐缺项、原生拒绝文字丢失、空推理强度、公开 worker 收据缺席事实和长中文错误越过字节界均返回原 run 修正。四个原始 Host 边界与长 Unicode 错误用例已转绿。完整检查发现的 ZCode 旧断言也返回 B，最终只补精确 equality 的 `accountStatus:not-applicable`。该回合 Worker 在自身环境报告 46 项中 19 failures/4 errors，目标断言通过后以 attention 交 Host，未扩大修改范围；Host 在隔离环境复跑 46 项全绿后直接验收该固定 turn，保留 Worker 原检查结果。早期 Worker 报告的 DSH/ZCode 失败也未直接认定为基线缺陷；Host 独立检查相关 10 项全部通过，环境差异的各个因子未逐项隔离。

B 用 `codex / openai / gpt-6-luna / low` 继续时收到 `CONFIGURATION_UNAVAILABLE`。Host 先按用户规则执行 `buddy adapters '{"refresh":true,"adapter":"codex"}'`，再用已启用的 `gpt-6.1-sol / high` 在原 B run continue。该日常刷新与下面的专项真实发现分开记录；未改日常模型启用开关、登录、密钥或运行时版本。C 的目录状态误用整体健康 availability 被原 run 修正，Host 原始复现与定向组件合计 18 项通过。

## 测试编号与检查结果

首次提交前，Host 在当时的整合代码上执行了一次完整检查，命令为 `uv run --frozen python -m hey_my_buddy.cli.checks`，清除 `BUDDY_CHECKS_JOBS` 的继承值，使用默认 4 并行、已安装的 Node 24 与短私有测试根。覆盖 180 个测试文件，耗时 461.52 秒，退出码 1；179 个通过文件报告 2,811 项（含 1 skip），失败文件实际运行 46 项、唯一失败为旧 ZCode 空目录断言缺少新账户事实。此次总覆盖为 2,857 个方法编号。私有根清理完成，未留下 teardown failure。原始退出码保持 1。

旧断言原 B run 修正后，Host 仅复跑 `buddy.harnesses.zcode.test_zcode_inquiry`：46/46，10.921 秒，退出码 0。完整检查之后的账户绑定代码修正整合后，Host 复跑受影响目录、账户、健康、明确指定、路由模块：102/102，8.726 秒，退出码 0；再检查账户绑定/native owner/操作/配额、quota retry 与 harness startup 的六个直接消费者模块：50/50，12.553 秒，退出码 0。12 个原始 Host 边界回归全部通过。这些是当时的定向结果；首次提交前未再次运行完整检查，也不将首次退出 1 改为退出 0。

控制台完整检查为 55 个文件、667 项通过，退出码 0；`tsc --noEmit && vite build` 退出码 0，打包资源已由 Host 构建并提交。构建保留 Vite 的既有大 chunk 提示，不作为行为失败。私有 synthetic preview 使用本分支的 `catalog_store.record` 和 `EvaluationStore.snapshot` 产生实际模型状态，浏览器 DOM/无障碍树看到 `ADR027-pending` 的“待确认 · 自 10/07 11:00”、仍可用及已启用的 high 档位，console warning/error 为空；这项检查覆盖状态投影与页面，未连接生产黑板。

Python 编号基线 2,785，首次提交 2,867（新增 83、移除 1）；本轮增加 7 个 continue 回归，最终 2,874（新增 90、移除 1，净增 89）。唯一移除项 `test_a_b_a_restores_identity_and_preserves_human_intent` 改名为 `test_a_ba_restores_identity_and_preserves_human_intent`，相应测试增加确认窗口断言，覆盖未删除。控制台声明编号从 648 到 656，新增 8、无移除；6 组参数化声明展开后额外执行 11 个用例，因此完整执行数为 667。编号变化全表见 [adr027-test-id-delta.tsv](adr027-test-id-delta.tsv)，采集无加载错误、编号无重复。C01–C09 对应 catalog trust、explicit admission、routing 和原生 fixture 测试；C10 对应组件及浏览器检查；C11 对应四个发现事实 fixture 与专项 Codex 发现；C12 对应本记录、编号表和机器摘要。

专项发现使用已安装 `codex-cli 0.160.0`，在 `2026-10-07T03:47:24.222471+00:00` 通过同一次 no-prompt App Server 会话读取账户和模型，账户事实为 `confirmed`，返回 7 个模型。沿用本机已有登录，Host 未设置 `CODEX_HOME`、登录/登出、改密钥或读取凭据文件内容，没有模型调用。脱敏结果见 [adr027-codex-discovery.json](adr027-codex-discovery.json)。除实现微任务的正常委派外，验证中的原生模型请求均为本地 fixture；无额外真实模型验证。

## 对外可见变化

- 四个 harness 的 `discoveries[]` 增加 `accountStatus`：Codex/Claude 在原生账户验证后报告 confirmed，不明时 unknown；DSH/ZCode 的当前发现通道报告 not-applicable，并说明发现边界。采信由黑板判断，原生缺少事实也按 unknown。
- 未知读取的目录状态为 unknown，账户不明原因为 `ACCOUNT_STATUS_UNKNOWN`；已有目录和可信读取时间保留。模型 profile 增加 `catalogStatus` 与 `pendingSince`，账户绑定变化的旧配置按 `ACCOUNT_BINDING_CHANGED` 显示不可用。
- 明确指定缺失/不可用的配置有限重读一次；仍拒绝时错误详情包含已记录的 `catalogReadAt` 和 `Run buddy adapters with refresh:true` 的 remedy。重读使健康失效时仍保留这些上下文及新的 harness 诊断；冷启动没有可信目录时不伪造成功读取时间。
- 状态转换事件为 `catalog.model_pending`、`catalog.model_recovered`、`catalog.model_unavailable`，与目录/profile 变更同事务记录。重复、未知及未满确认期的读取不制造重复转换。
- 控制台模型族与档位详情显示待确认及起始时间，并保持 pending 模型可用；目录状态来自黑板字段，健康 availability 分别显示。
- Codex 公开 worker 收据及已有 fast/review 投影在所选模型当场缺席时报告 `selectedModelListed:false` 与 `selectedModel`，native evidence 的 model-check 引用按现有哈希/长度规则核对。已列出模型的非法或空 effort 仍拒绝；native RPC 拒绝保留 `error.message`，含方法前缀的整体文字在既有 512 UTF-8 字节界内安全截断，`error.data` 不展开。

## 回收与未验证边界

Host 验收后仅回收三条登记路径：A 的 `ws-46c8c752810e3897106ed1c6a40f1885/checkout`，B 的 `ws-3a14be25c4957ba5e3c5f33f4d923ea7/checkout`，C 的 `ws-daca4f4556dcd320844e40ee0c44212f/checkout`，均位于 `~/.local/share/hey-my-buddy/state/workspaces/`。三个 cleanup plan 最终都是 applied，`result.removed=true`，Host 核对路径均已不存在；A 显式 plan/apply，B/C 的服务回收与 Host 操作存在竞争，曾返回 Git/revision 错误，随后以 ledger applied 和路径不存在核实，不重复删除。固定 refs、输入/输出、补丁和命令收据保留。原始 Host 日志放在本机忽略的 `.dsh-skill-build/adr027-model-catalog/`；Host 工作树保留供 Claude Code Host 审查。

未真实验证 DSH、ZCode、Claude 在账户异常下的清单变化，也未做真实模型成功/拒绝调用；Codex 只确认本机一次正常发现的账户事实。没有安装或升级日常运行时，本步骤不声称日常 0.27 已具备 ADR-027 行为。首次提交未给最终代码重跑完整命令，已被 Claude Code Host 指出；本轮已在下述最终代码提交运行完整检查并取得退出 0，原先的失败事实仍保留。尚未取得 Claude Code Host 对 ADR-027 的整体验收，未改它维护的架构/入口/参考文档或 ADR-025 工作树、分支及微任务。

## Claude Code Host 退回后的返修计划

Claude Code Host 对 `277eadd81606152f3ec4c774f5b08d54d108d6a6` 的验收未通过：公开摘要携带三个本机主目录路径，continue 的启用检查挡住目录重读，DSH 发现说明不符实际启动方式，而且最终代码缺少退出 0 的完整检查。Host 已将三处路径改成 `~` 并改写末提交为 `c8336e6a61d7bde7d1072adfdd24b40104541d79`；仓库卫生的 5 项检查退出 0，分支可达历史的该文件无本机主目录路径，尚未推送。原 A、B run 均已 accepted 且已回收；原 A 的 continue 实际返回 `CONFLICT: An accepted goal cannot be continued`。用户因此明确授权创建关联原 A、B run 的返修微任务，首次仍走路由，沿用原 hostId 与宏任务。

返修 A 修改 `blackboard/tasks/workflow.py` 的明确配置续跑入口与受影响的任务测试：原生目录先校验，缺失或不可用先有限重读，之后独立判断用户启用状态；拒绝携带目录读取时间与刷新办法，并区分未启用和目录不可用。待确认与账户读取事实仍存既有 meta，schema 保持 15。返修 B 只改 DSH 的发现说明，写明本次私有 DSH_HOME 启动已安装 DSH、设置与凭据按路径绑定原主目录；不做真实发现或模型验证。Worker 不删除、不运行完整检查，只跑受影响测试。

C13 覆盖 continue 缺失、不可用、重读恢复、仍然拒绝以及未启用的不同原因和事实；C14 核对去掉重读与去掉原因区分时测试失败；C15 核对公开记录路径脱敏与分支历史；C16 在两份返修产物整合、最新核心分支合入并提交最终代码后，运行一次默认并行完整检查，登记被检查的提交号、退出码与实际结果。已核对通过的模型发现、控制台产物与原有变异证据沿用，记录更正不重跑完整检查。

## 返修产物与核对

返修 A 为 `108d43ea-44c6-4f9e-a8c6-eceeb3f43e60`，关联原 A；返修 B 为 `fed49e93-922d-4e00-834f-076857393cda`，关联原 B。首次均省略 buddy 并路由，A 选 ZCode / GLM-5.3 / max，B 选 ZCode / GLM-5.3-Flash / max。B 固定产物为 `9b14da0c0bd4c72ce5ee15c39ae9625080570b24`（artifact `ec14f5c7-9ff3-4519-88f5-5f7aaaf2ffab`），只改 DSH 一条 warning，Host 发现 fixture 3/3、退出 0。

A 首轮固定产物 `3213b6f5026e06cd4010e51f1752ed287ec7e71e`（artifact `6ce03385-852a-40d5-a18e-8dd0d542bdf7`）让两个原始 Host 回归转绿，但移除了写事务前对 available 的保护：pending 模型被事务外校验接纳后，另一已确认读取在写事务前完成一小时确认，continue 仍会写入 override。Host 额外回归失败，已以 rejected 返回同一返修 run；没有把该产物当作验收通过。第二回合用完整 `codex / openai / gpt-6.1-sol / high` 配置 continue 并登记原因，修正写入前保护。最终固定产物为 `713e87647b58417e202186f8464fd668e3a6b0f6`（artifact `cd8c93e1-d004-420b-a655-6538770e21a9`），仅改 workflow.py 与一个新增测试文件；两回合执行历史均保留。

C13 的 7 项新测试使用真实 catalog.validate_configuration 和真实 workflow_continue，绕开基类的透传 mock 与自动启用 helper；覆盖目录缺失后重读恢复、缺失后仍拒绝、已确认不可用后仍拒绝、不可用后重读恢复、未启用与目录拒绝的不同原因和事实、刷新不改用户 enabled，以及校验和写入之间的目录确认使配置、续跑、收据与事件均不写入。Host 最终限定检查为 43/43、13.708 秒、退出 0；独立 Host 边界 3/3、0.311 秒、退出 0。

C14 的 Host 变异均在独立进程内进行，不改最终源码：只移除 validator 中的 hook 调用，7 项中 4 failures/2 errors、退出 1；把 not-enabled 原因与文案改成目录通用拒绝，保留拒绝、配置、读取时间、刷新办法和启用开关，2 failures、退出 1；仅省略写入前可用性保护，1 failure、退出 1。未变异的同一组 7/7、退出 0。完整失败编号在机器摘要中。Claude Code Host 已核对的旧 26 处变异、24 处触发失败及六条规则的覆盖沿用，不重做或扩大其结论。

A 首轮 Worker 运行 211 项时有一个 routing 单例失败，将其归为基线问题；Host 在绝对 PYTHONPATH、短私有状态/运行时根下单独跑该项为 1/1、0.395 秒、退出 0，第二回合 Worker 的限定 43 项也两次通过。该归因已返回原返修 run，Worker 撤回“预先存在、与改动无关”的结论；211 项退出 1 的事实保留，环境差异的具体根因未调查。

A 首轮 Worker 还报告误执行共享仓库的 git stash pop，造成其工作树 AGENTS.md 与 docs/README.md 冲突；它恢复了两文件，并移除了被带入的 `.workbuddy/memory/2026-09-23.md` 副本。删除和越界处理违反 Worker 不删除与授权范围，不能以“最终两文件差异正确”抵消。Host 将事故和不准确的合规/验证结论返回同一返修 run，第二回合明确订正；未自行改成无违规完成。事后只确认原 stash 对象 `266543ce3b3b0d64f6c94fdb79ac847cc7242a9f` 仍在，副本的 blob `8007a4868b30038309d38d3c92a8f6675b5be1d2` 仍在该 stash 的第三父树；未读取内容或写回，缺少事故前 stash 快照，不能据此声称共享 Git 状态完整未变。最终固定源码的受保护文档无差异。

本轮外显变化补充：continue 指定 buddy 先做与提交相同的目录校验，目录缺失或不可用时有限重读；仍拒绝带 catalogReadAt 与刷新办法。已接纳但未启用的配置以 CONFIGURATION_UNAVAILABLE、reason=not-enabled、enabled=false 拒绝，说明启用动作及刷新不会启用；写入前刚变得不可用时以同一错误码、reason=catalog-unavailable 及当前目录事实拒绝。DSH 说明改为启动已安装 DSH 的本次私有 DSH_HOME、设置与凭据按路径指回所属主目录、无单独账户读取；accountStatus 保持 not-applicable。

建议项边界：没有增加明确指定重读的节流，每次目录缺失/不可用仍有限重读一次；健康检查的既有 180 秒扫描窗口不变。accounts.py 的 clear_confirmed_read 与 harness_health.py 的 restore_retained_availability 均保留，本轮没有补足其独立变异覆盖，不声称旧两处未捕获的变异已受保护。返修整合提交采用可读说明，原有 ws 输出提交说明未改写。已通过的真实免模型发现、控制台逐字节产物核对与 effort 单独消失的边界沿用；没有新增真实发现或模型验证，也未安装/升级日常运行时。

## 返修最终完整检查与重新提交

C16：最终代码提交 `1c5ec9e0389e2d1fb2d8bb9a7ee4c14cb2d0fd93` 包含当前最新 `socu/buddy-core` 的 `55fff85fbe4ddce8a88eccd898a977a9bcd16a24`。在干净工作树上运行 `uv run --frozen python -m hey_my_buddy.cli.checks`，不传 --jobs 并清除 BUDDY_CHECKS_JOBS，默认 4 并行；181/181 文件通过，运行 2,874 项（1 skip），退出码 0，耗时 465.628 秒，UTC `2026-10-07T08:17:51.525940+00:00` 至 `2026-10-07T08:25:37.160559+00:00`。本轮完整检查一次，检查时脱敏的验收 JSON 与 7 个新回归已经跟踪在该提交内。之后只更新验收记录，没有再改源码或重复完整检查。

C15：包含三处绝对主目录路径的 `277eadd8` 已改写为 `c8336e6a`；它不是本分支的可达祖先。在被完整检查的代码提交上，19 个本分支独有的可达提交中检查两份 ADR-027 记录，未发现本机主目录路径。最终记录继续使用 `~` 或相对路径，登记后再运行仓库卫生的 5 项定向检查；分支未推送。原始失败的 Host 完整检查与 Claude Code Host 在 `277eadd8` 的退出 1 分别保留，未改写成通过。

返修 A 的整合记录为 `int-09752cb8-f6cf-41f2-bf39-61ad1a4ba9ae`，匹配 2 个产物路径；返修 B 为 `int-5e5dcf82-548a-4de4-97c1-e7537ae19ada`，匹配 1 个路径，两者 verified 且无 missing/differing/unrecorded 路径。独立微任务的整合和步骤记录列为相应 hostPaths，未混入各 Worker 的授权改动。完整检查后 Host 对最终 artifact 分别 accepted，保留 A 首轮 rejected、事故与订正。两个 run 的自己和后代都确认停止。

Host 按已授权的确切路径回收返修检出：A 为 `~/.local/share/hey-my-buddy/state/workspaces/ws-b43554517fe8356e0da2d59446cca502/checkout`，计划 `cln-2a2026d6-ed1c-472f-a9d1-64188776bab8`；B 为 `~/.local/share/hey-my-buddy/state/workspaces/ws-637754ba43910d4dcf052450881d0c7b/checkout`，计划 `cln-0fb7bc90-5c0d-4ac5-a938-0bafa1b0666d`。两计划均 applied，事后路径均不存在；A 的 apply 返回 duplicate，回放的是同一个已应用计划，未另行删除。源码、固定产物、patch、refs、整合与历史收据保留，A 第二回合的本地验证日志已在回收前取到 Host 的 ignored tmp；没有全仓库 prune。Host 工作树继续保留供 Claude Code Host 验收。

本次重新提交保留以下边界：建议的明确读取节流与两处冗余保护的独立变异覆盖未增加；Worker 删除事故不能撤销为未发生，事前共享 stash 状态没有快照；首轮 routing 单例的环境差异根因未调查；新错误的真实控制台渲染与真实模型拒绝未验证。已核对通过的免模型发现、控制台产物与旧规则证据沿用，未安装或升级日常运行时，也未改受保护文档、ADR-025 工作树/分支/微任务。完成后停止，等 Claude Code Host 验收。
