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

实施完成，交 Claude Code Host 验收。最终代码树为 `713425ad58a6d3707b0255d501d1f4062cfa7280`，包含最新 `socu/buddy-core` 的 `55fff85fbe4ddce8a88eccd898a977a9bcd16a24`；Host 工作树保留供审查。方案和验收由 `codex-adr027` 负责，新宏任务为 `obj-9f62bcab-3a1c-4878-ba75-38a10337ee4a`，三个首次提交均经过路由，省略 buddy 配置。schema 仍为 15，日常运行时保持 0.27；ADR、CONTEXT、AGENTS、README、待办与参考文档由 Claude Code Host 维护，本分支相对最新核心分支的这些文档无改动。

待确认条目最终在 `meta` 的 `catalog-pending:<adapter>` 保存模型身份、`since` 和最后合法 `efforts`，兼容实施早期的纯时间字符串；`catalog-read-at:<adapter>`、`catalog-account-status:<adapter>` 保存可信读取事实，`catalog-scan-after:<adapter>` 复用 180 秒扫描窗口约束未知读取后的重试。旧目录缺少新增 meta 时从既有采纳记录取时间，未知读取不冒充成功读取。恢复与首次缺席均按最后可信配置的完整身份和合法推理强度处理；账户 source 或 credentialRevision 改变时清除当前目录指针、确认窗口和读取事实，旧配置保留 profileId 与 enabled 意图，但不能用于新身份。账户、目录和健康检查的状态由 Python 服务在既有 SQLite 事务中判断，未增加表、列、计时器或用户设置。

| 微任务 | runId | 最终固定提交 | 最终整合记录 | Host 结论 |
| --- | --- | --- | --- | --- |
| A：黑板 | `543174ce-34b9-4a26-aeb1-b6537db0152b` | `55254f366c12dcf428e8fe0fcdd46469b445b081` | `int-7bcede94-3043-456c-aa35-a37a1663343d` | accepted |
| B：harness/Codex | `4e713942-3408-4407-ab6d-f457c5b0375f` | `f8e2ca1a66963d33ea26593ed7bbe59eee41de34` | `int-1df973fd-abe4-4c88-9349-39842f06e927` | accepted |
| C：控制台 | `4f9dfdf2-a795-4f61-8493-2b134cb900c2` | `d090e88e342405a1ada0d72a57f85197da7a8958` | `int-024ce269-e345-4c10-ace6-dcc11de075a2` | accepted |

三个最终整合记录均为 `verified`，分别匹配 15、16、8 个目标路径，无 missing、differing 或 unrecorded 路径；每个 acknowledgement 都绑定最终固定 artifact 和整合记录。机器可读摘要见 [adr027-verification.json](adr027-verification.json)。微任务验收与 Claude Code Host 对本步骤的验收分别记录，后者尚待进行。

A 的实现缺陷均返回原 run：先修扫描限流挡住过期刷新、冷启动未知目录永不过期和旧服务 fixture；再修重读导致健康失效时漏掉拒绝上下文、健康恢复与目录事实混用、旧目录读取时间；随后修退休 effort 被恢复、路径重查及初始化抹掉目录事实；最后修 source/credentialRevision 切换时采用旧账户目录。最后一轮还补足退休 effort 与模型恢复事件的组合。Host 保留失败用例并逐次核对转绿，没有改写早期失败结果。首轮摘要漏算新测试文件的统计由 Host 按固定 diff 登记为 14 个文件、955 插入/49 删除；后来步骤增加的差异另以最终代码树为准。

A 的第 4、5 回合分别在 GLM-5.3/max 和 GLM-5.3-Flash/max 上收到供应方 `rate_limited / 1308 / 429 / stream / not-retryable`；Host 用完整配置在原 run continue，保留失败事实与部分产物，后来切换到 `codex / openai / gpt-6.1-sol / high`。第 6 回合报告代码及 92 项检查，但 App Server 正常关闭未获确认，执行仍为 failed；监督器随后确认停止，部分产物 `60729030` 经 Host 固定审查与整合，未当作成功交付。第 7 回合完成账户隔离修正，native 回合 completed、自己和后代停止均已确认，最终输出才被 accepted。未修理或升级日常执行器的退出行为。

B 的套餐缺项、原生拒绝文字丢失、空推理强度、公开 worker 收据缺席事实和长中文错误越过字节界均返回原 run 修正。四个原始 Host 边界与长 Unicode 错误用例已转绿。完整检查发现的 ZCode 旧断言也返回 B，最终只补精确 equality 的 `accountStatus:not-applicable`。该回合 Worker 在自身环境报告 46 项中 19 failures/4 errors，目标断言通过后以 attention 交 Host，未扩大修改范围；Host 在隔离环境复跑 46 项全绿后直接验收该固定 turn，保留 Worker 原检查结果。早期 Worker 报告的 DSH/ZCode 失败也未直接认定为基线缺陷；Host 独立检查相关 10 项全部通过，环境差异的各个因子未逐项隔离。

B 用 `codex / openai / gpt-6-luna / low` 继续时收到 `CONFIGURATION_UNAVAILABLE`。Host 先按用户规则执行 `buddy adapters '{"refresh":true,"adapter":"codex"}'`，再用已启用的 `gpt-6.1-sol / high` 在原 B run continue。该日常刷新与下面的专项真实发现分开记录；未改日常模型启用开关、登录、密钥或运行时版本。C 的目录状态误用整体健康 availability 被原 run 修正，Host 原始复现与定向组件合计 18 项通过。

## 测试编号与检查结果

完整检查只在整合后执行一次，命令为 `uv run --frozen python -m hey_my_buddy.cli.checks`，清除 `BUDDY_CHECKS_JOBS` 的继承值，使用默认 4 并行、已安装的 Node 24 与短私有测试根。覆盖 180 个测试文件，耗时 461.52 秒，退出码 1；179 个通过文件报告 2,811 项（含 1 skip），失败文件实际运行 46 项、唯一失败为旧 ZCode 空目录断言缺少新账户事实。此次总覆盖为 2,857 个方法编号。私有根清理完成，未留下 teardown failure。原始退出码保持 1。

旧断言原 B run 修正后，Host 仅复跑 `buddy.harnesses.zcode.test_zcode_inquiry`：46/46，10.921 秒，退出码 0。完整检查之后的账户绑定代码修正整合后，Host 复跑受影响目录、账户、健康、明确指定、路由模块：102/102，8.726 秒，退出码 0；再检查账户绑定/native owner/操作/配额、quota retry 与 harness startup 的六个直接消费者模块：50/50，12.553 秒，退出码 0。12 个原始 Host 边界回归全部通过。上述定向结果补足最终改动覆盖，未再次执行完整命令，也不将首次完整检查标记为退出 0。

控制台完整检查为 55 个文件、667 项通过，退出码 0；`tsc --noEmit && vite build` 退出码 0，打包资源已由 Host 构建并提交。构建保留 Vite 的既有大 chunk 提示，不作为行为失败。私有 synthetic preview 使用本分支的 `catalog_store.record` 和 `EvaluationStore.snapshot` 产生实际模型状态，浏览器 DOM/无障碍树看到 `ADR027-pending` 的“待确认 · 自 10/07 11:00”、仍可用及已启用的 high 档位，console warning/error 为空；这项检查覆盖状态投影与页面，未连接生产黑板。

Python 编号基线 2,785，最终 2,867：新增 83、移除 1，净增 82。唯一移除项 `test_a_b_a_restores_identity_and_preserves_human_intent` 改名为 `test_a_ba_restores_identity_and_preserves_human_intent`，相应测试增加确认窗口断言，覆盖未删除。控制台声明编号从 648 到 656，新增 8、无移除；6 组参数化声明展开后额外执行 11 个用例，因此完整执行数为 667。编号变化全表见 [adr027-test-id-delta.tsv](adr027-test-id-delta.tsv)，采集无加载错误、编号无重复。C01–C09 对应 catalog trust、explicit admission、routing 和原生 fixture 测试；C10 对应组件及浏览器检查；C11 对应四个发现事实 fixture 与专项 Codex 发现；C12 对应本记录、编号表和机器摘要。

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

未真实验证 DSH、ZCode、Claude 在账户异常下的清单变化，也未做真实模型成功/拒绝调用；Codex 只确认本机一次正常发现的账户事实。没有安装或升级日常运行时，本步骤不声称日常 0.27 已具备 ADR-027 行为。未给最终代码再跑一次完整命令：首次完整退出 1 的旧断言已定向修复，随后源代码变化用受影响测试与直接消费者验证。尚未取得 Claude Code Host 对 ADR-027 的整体验收，未改它维护的架构/入口/参考文档或 ADR-025 工作树、分支及微任务。

## Claude Code Host 退回后的返修计划

Claude Code Host 对 `277eadd81606152f3ec4c774f5b08d54d108d6a6` 的验收未通过：公开摘要携带三个本机主目录路径，continue 的启用检查挡住目录重读，DSH 发现说明不符实际启动方式，而且最终代码缺少退出 0 的完整检查。Host 已将三处路径改成 `~` 并改写末提交为 `c8336e6a61d7bde7d1072adfdd24b40104541d79`；仓库卫生的 5 项检查退出 0，分支可达历史的该文件无本机主目录路径，尚未推送。原 A、B run 均已 accepted 且已回收；原 A 的 continue 实际返回 `CONFLICT: An accepted goal cannot be continued`。用户因此明确授权创建关联原 A、B run 的返修微任务，首次仍走路由，沿用原 hostId 与宏任务。

返修 A 修改 `blackboard/tasks/workflow.py` 的明确配置续跑入口与受影响的任务测试：原生目录先校验，缺失或不可用先有限重读，之后独立判断用户启用状态；拒绝携带目录读取时间与刷新办法，并区分未启用和目录不可用。待确认与账户读取事实仍存既有 meta，schema 保持 15。返修 B 只改 DSH 的发现说明，写明本次私有 DSH_HOME 启动已安装 DSH、设置与凭据按路径绑定原主目录；不做真实发现或模型验证。Worker 不删除、不运行完整检查，只跑受影响测试。

C13 覆盖 continue 缺失、不可用、重读恢复、仍然拒绝以及未启用的不同原因和事实；C14 核对去掉重读与去掉原因区分时测试失败；C15 核对公开记录路径脱敏与分支历史；C16 在两份返修产物整合、最新核心分支合入并提交最终代码后，运行一次默认并行完整检查，登记被检查的提交号、退出码与实际结果。已核对通过的模型发现、控制台产物与原有变异证据沿用，记录更正不重跑完整检查。
