# Router 第五步执行计划：只限模型开始之前的等待、画像建议与撤回、工具调用的成败

2026-11-07，Host 起草。这一步装为 0.65.0（契约 0.65.0，schema 38），先于 Buddy 页与 Router 的重做，2026-11-07 装上（[安装记录](../../acceptance/installs/0.65.0.md)）；控制台只做跟上新字段的最少改动。

## 开发者 11-07 的决定

- 三个面的 Router 调用都只限“模型开始之前”的等待；模型开始之后不设时限，也不设工具调用上限。审阅的简要、标准、深入三档与快速模式固定的 60 秒去掉，画像预算一并去掉；等多久算没开始是 Router 设置里的一项（[ADR-021](../../decisions/021-router-buddy-planes-and-routing-evidence.md) 第 2、14、16 条同日的修改）。
- 画像面只提“同一个模型”的建议，名字不变的换代只由用户标出；撤回删掉最近一次的改动，改过去的模型若再没有东西用到它，一起删掉（第 24 条）。
- 运行证据记下每次工具调用的成败，画像运行的记录据此写明网页抓取等失败了几次（第 14 条）。
- DSH 的只联网与不用工具两档，启动时读出 DSH 实际的工具行，有清单之外的就不运行并写明原因（[待办](../backlog.md)“DSH 升级后，只联网一档可能悄悄放宽”）。
- 0.65.0 先于界面重做单独做。

## 不在这一步

- 界面的重做，以及待办里留到重做时再定的几处显示（失败原因合成一条、打回的原因、点击后不消失的“画像已在运行”）。
- Worker 回合的时限：Worker 回合本来就没有时限，由 Host 取消，不变。
- 基础设施失败按模型服务还是按模型归组、进行中标出换代给旧一代记失败：两条都待开发者定，不随这一步。

## 现状（读源码得到的，2026-11-07）

- **模型开始**：`RunResult.model_started`（`buddy/harnesses/run_contract.py`）。四个 harness 的含义不一样：Codex 在 `turn/start` 回复带回回合标识时置上，ZCode 在 `session/send` 被接收时置上，这两个都只是本机进程收下了回合；Claude Code 看根会话里模型的第一段输出（文字、思考、工具调用或输出 token），DSH 看第一条模型活动的 `session/update`。它只出现在最后的结果里，运行中的观察者事实（各 harness 的 facts 映射）没有它；DSH 与 ZCode 的 Router 调用连实时端点都没有。读它的只有执行进程的模型前重试（`buddy/runtime/execution.py`，`modelStarted is False` 时换 harness），以及存下的结果的两个读者（`blackboard/tasks/workflow.py`、控制台 `task-detail/InputFailureFacts.tsx`）。Codex 的 `turn/start` 由本机进程应答，提供方没有回应时它可能照样报“已开始”（推断，没有实测），所以现在的含义挡不住 Codex 上的那种卡住。
- **时限与上限**：定义在 `blackboard/routing/router.py`（`BUDGETS`、`FAST_BUDGET`、`budget`、`configured_budget`、`_expanded_budget`、`budget_view`、`routing_facts`）；`blackboard/routing/decision.py` 建任务时把秒数写进规格的 `routerBudgetSeconds`（快速模式是写死的 60，外加 10 秒余量），发布时按用时与工具调用次数拒收（`router-budget-exhausted`）；执行进程据它写 `execution-budget.json` 并在到点时取消，服务的调度器读同一个文件、到点强停（`blackboard/service/execution_scheduler.py`，文件不在就没有截止）；角色层另有 `RunBudget`、审阅的工具调用上限（`ReviewObserver`）、画像循环的剩余时限与剩余次数（`buddy/roles/inline_run.py`）、冻结副本准备与核对的截止（`buddy/roles/router_input.py`）；Router 的提示里带着预算，审阅说明末尾写“Stay within the supplied budget.”。
- **没有答案时**：`router-budget-exhausted` 记为没有答案，换列表里的下一位，并按重试间隔先跳过这一位；列表用完停在 Host 的路由边界。维护与画像没有列表，记失败。`health` 与控制台显示预算耗尽的计数。
- **设置与 Host 面**：`router_settings.budget_preset` 与 `profile_budget`；控制台读写 `routingBudget`、`profileBudget`、`routingBudgetLimits`；`get` 的 `routing.routingBudget` 与 `routing.budget`、`inquire` 的 `routerBudgetSeconds`、时间线里的路由片段、`health` 的 `budgetExhaustedCount` 都对 Host 可见。负载估算（`blackboard/routing/evidence.py`）用 `routerBudgetSeconds` 估一次路由占多久。
- **画像的对应建议**：答案的每条建议带 `relation`（same 或 successor）；`blackboard/routing/profile_data.py` 的 `apply_answer` 对 successor 新建一个以候选名字命名的模型并把候选改对应过去。候选只含对应到别的模型的服务（`build_packet`）。
- **撤回**：`blackboard/evaluation/models.py` 的撤回追加一行，复制同一模型服务倒数第二行的模型；表上有禁止更新与删除的触发器。读整段历史的有两处：`model_view` 的 `names`（路由输入、维护数据包、控制台、画像数据包里“曾经对应到这个模型的名字”）与 `correspondence_view` 的 `canUndo`。外键指向 `models(model_id)` 的表有 11 张，全是 `ON DELETE RESTRICT`。
- **工具证据**：`protocol/tool_evidence.py` 第 2 版，每次调用一行，只有工具名与类别，没有成败；键集合是严格的，加字段就要升版本。四个 harness 的原生事件里都有成败：Codex 的命令执行有 `status` 与 `exitCode`（网页搜索的条目在我们的样本里没有状态），Claude Code 的 `tool_result.is_error`，DSH 的 ACP `status`（completed 或 failed），ZCode 的 `result` 或 `error` 与 `result.success`；各 harness 的证据收集都把它丢掉了。
- **DSH 的工具行**：`buddy/harnesses/dsh/native_run.py` 的 `NONE_SCOPE_DISABLED_ROWS` 是 2026-10-06 读出的 15 行，只联网一档留着 `tool-web`；启动时不检查有没有别的行。2026-11-07 在 DSH CLI 0.2.2-rc.1 上重跑只联网的原生核实，五项都通过，但没有读工具行清单本身。 同日 Host 用不含用户那一层的 `--dump-default-config` 读出：0.2.2-rc.1 的 `acp` 配置档有 18 个 `tool-` 行，多出 `tool-executor-native`、`tool-executor-ptc`、`tool-plugin-manager`、`tool-working-directory` 四行，少了 `tool-ralph`、`tool-subagent-list-agents`。

## 口径（Host 定，开发者可改）

### 模型开始

- **定义**：根会话里模型的第一个输出，即文字、思考、工具调用或大于零的输出 token。四个 harness 按同一个定义报；Codex 与 ZCode 不再以回合被收下为准。“请求已发出”另有 `nativeRequestSent`，模型前重试改为只看它：请求一旦发出就不换 harness 重发。
- **运行中报出**：每个 harness 在运行中把它作为观察者事实交出，与工具调用次数走同一条路；Router 的各种调用与 Worker 回合都报。不依赖实时端点。
- **存下的值**：一个值的含义不随写入的时间而变。实现方先列出存下的 `modelStarted` 的全部读者，并核对 `attempts.result_json` 是否由摘要绑定在回执上；然后在两条路里提出一条，停下来问：存下的旧值按旧含义留作当时的事实、新的事实换一个名字；或者在 37→38 一步里把能从同一条结果推出的旧值转换，推不出的写成未知。

### 只限模型开始之前的等待

- **设置**：`router_settings` 加“等待模型开始的秒数”，整数，至少 1，默认 60；`budget_preset` 与 `profile_budget` 删掉。控制台的 Router 设置里一项，中文“等待模型开始作答（秒）”，英文“Wait for the model to start responding (seconds)”。
- **冻结**：建 Router 任务时把当时的等待写进规格，取代 `routerBudgetSeconds`。
- **执行进程**：启动时照旧写预算文件，截止是启动时刻加等待；一收到模型开始的事实就删掉这个文件，此后不再有截止。harness 的 `RunBudget` 一律不设截止。画像的每一轮是新会话，每一轮各等一次。冻结副本的准备与核对不再带截止。
- **服务**：调度器照旧读预算文件，文件不在就没有截止，不用改读法。
- **原因**：到点时模型还没开始，记 `router-not-responding`（中文“Router 的模型在 {{seconds}} 秒内没有开始作答。”），路由面按没有答案换下一位，维护与画像记失败。发布时按用时与工具调用次数拒收的检查删掉，工具证据与用量一致的检查留着。`router-budget-exhausted`、`readonly-budget-exhausted`、`profile-budget-exhausted` 与没有产出者的 `router-timeout` 删掉，连同它们的文字与计数。
- **路由输入与 Host 面**：Router 的提示不再带预算，审阅说明删掉那一句，Router 的协议版本加一。`get`、`inquire`、时间线与 `health` 里关于预算的字段：不再有含义的删掉，换成等待的秒数与“没有开始作答”的计数；实现方列出每一处与它的读者。负载估算里，路由不再有上限，按现有的“至少”处理。

### 画像建议与撤回

- **答案**：每条对应建议只有模型服务（harness、provider、model）与来源链接、来源日期，去掉 `relation`；校验与写入只把它对应到这个模型。说明改为只建议与这个模型是同一个的候选。
- **撤回**：删掉这个模型服务最新的一行，回到上一行；只有一行时不能撤回。改过去的模型若再没有任何表引用它，同一事务里删掉。37→38 一步把禁止删除的触发器换成只许删“同一模型服务最新、且不是唯一一行”的触发器，禁止更新的照旧。判断“再没有引用”的表随 schema 列出，测试对照 schema 里指向 `models` 的外键，不让两者走样。
- **已经存下的错误建议**：日常黑板上 gpt-6-sol 那一条，装上之后由开发者在控制台撤回，升级不为它特殊处理。

### 工具调用的成败

- **证据第 3 版**：每次调用一行加成败，取值成功、失败与未知；各 harness 从原生事件取，拿不到的写未知，不猜。第 2 版的文件在它的 attempt 结束后若还有读者，实现方先列出并停下来问。
- **画像运行的记录**：按工具名的调用次数与失败次数，在读的时候从这次运行的执行结果（证据第 3 版的汇总）推出，不另存进 `profile_runs`，也就没有存量要转换；证据是第 2 版的旧运行，读出来是未知。（2026-11-07 Host 改：原来写另存一份、旧行写成未知，另存的那一份能从执行结果推出，是冗余。）
- **DSH 的工具行**（2026-11-07 改定，按 [ADR-023](../../decisions/023-harness-integration-principles.md) 第 10 条）：只联网与不用工具两档启动 DSH 前，用不含用户那一层的 `dsh --profile acp --dump-default-config` 读出现有的全部 `tool-` 行（0.2.2-rc.1 起是参数，输出是 YAML），在启动补丁里关掉这一档不需要的全部行，只联网只留联网所需的几行。读不出或认不出格式时，只联网一档不运行并写明原因，不用工具一档照旧由黑板事后判定。用户用 `dsh plugin add` 装的插件所带的行是否在不含用户层的输出里，实现方先查清；不在的话，改读完整的配置，在内存里只取行名，其余不保存、不写日志。

### 控制台（最少的改动）

- Router 设置去掉审阅预算的三档与画像预算，加等待的秒数；新原因的中英文字；画像运行的用量一行加上各工具的失败次数；撤回之后，空模型不再出现在模型列表里。预览数据与受影响的浏览器检查跟上。显示的方式留给界面重做。

### 存储（schema 38）

- 新的一步 37→38：`router_settings` 删两列、加等待的秒数；对应关系表换触发器。“模型开始”选了换名字、不转换旧值（2026-11-07），画像运行的工具成败在读的时候推出，这两样都不进这一步。夹具里已有 `router_settings` 的存量。

## 微任务与顺序

1. **画像建议只认同一个模型，撤回删掉最近一次改动**：新建 37→38 一步（触发器、`router_settings` 暂不动）、`profile_data.py`、`models.py`、画像说明、受影响的测试。动了删除的路径，Host 验收时另请一次独立审查。
2. **模型开始的事实统一并在运行中报出**：四个 harness 的 `model_started` 与观察者事实、模型前重试改看 `nativeRequestSent`、存下的值的两条路先停下来问。与第 1 个同时开工。
3. **Router 调用只限模型开始之前的等待**：在第 1、2 个之上；扩展 37→38 一步的设置；执行进程、调度器、角色层、路由、Host 面与原因码。删掉了守卫，Host 验收时另请一次独立审查。
4. **运行证据记下每次工具调用的成败**：在第 2 个之上（同一批 harness 文件）；证据第 3 版与画像运行读出的计数。可与第 3 个同时，写入范围不重叠时。
4b. **DSH 的两档按 ADR-023 第 10 条改写**：在第 4 个之后（同在 DSH 的文件里）；`buddy/harnesses/dsh/native_run.py` 的启动补丁、读出工具行、新的原因码与中英文字。
5. **控制台**：在第 3、4 个之上。

文档（ADR-021、`docs/reference` 里写到预算的几页、`CONTEXT.md`、harness 文档）由 Host 随各批改。

## 验收与安装

- 每批读完差异、跑受影响与共用下游的测试；第 1、3 个各请一次独立审查（删除的路径、删掉的守卫）。
- 安装前：完整检查与浏览器全量各一次；在从日常黑板导出的样本上像用户那样升级演练，基线是装着的 0.64.1，候选是 0.65.0，并做启动失败与篡改两处故障注入；升级前在日常黑板上只读预数要转换的行。
- 真实调用：每个担任过 Router 的 harness 跑一次真实的路由调用，看运行中报出的“模型开始”；DSH 跑一次只联网的启动，看工具行的检查。这些是付费调用，逐次请开发者批准。
- 契约与存储都变，安装要等黑板空闲，开始前告诉开发者。

## 0.65.1（2026-11-07 安装 0.65.0 前的核实里发现）

0.65.0 的 DSH 只联网核实里，探针的五项都过，服务的判定却是 `router-tools-forbidden`（[待办](../backlog.md)“DSH 担任画像 buddy 时，服务把它的联网调用判为越权”）。这一处与 0.65.0 验收时记下的几处顺带项做成补丁版本，不改契约与存储。

- **DSH 的联网调用按 `fetch` 判定**：工具的归类表里 DSH 加上 web_search、web_fetch（`fetch`）与 working_directory（`read`，它读的是本机的状态）；只联网一档的白名单只留 `tool-web` 与 `tool-executor-native`，按 ADR-023 第 10 条只留这一档需要的；联网核实的探针把服务的判定也算作一项。
- **顺带项**：Claude Code 不带 `is_error` 的 `tool_result` 记成功；导入执行结果的两处校验只接受第 2 版 `inputFailure`；检查入口按 `apps/console/package.json` 的 `engines` 核对 Node。

微任务：

1. **DSH 的联网调用按 fetch 判定**：`protocol/tool_evidence.py` 的归类表、`buddy/harnesses/dsh/native_run.py` 的保留清单、`tests/probes/web_permission_native.py` 的判定项与相应测试。
2. **0.65.0 留下的三处小毛病**：`buddy/harnesses/claude/tool_evidence.py`、两处导入边界、`cli/checks.py`。

两个的写入范围不重叠，同时开工。验收读差异、跑受影响的测试；安装前完整检查一次；DSH 只联网一档做一次逐次批准的原生核实，这一次连服务的判定一起看。补丁版本，安装不要求黑板空闲。文档（harness 参考页里 DSH 只联网一档留的行）由 Host 改。
