# L14：界面文字与源码遗留措辞

以 `c8950dc3` 为输入，在受管检出内完成范围内的文字与注释修改；未更改布局、样式、接口名称、字段、命令、存储形式、状态取值、版本号或 schema 版本号。

## 界面与注释

计数按非测试控制台源码里被替换的旧术语每次出现计，含显示文字、aria-label、title、错误提示、演示数据与注释，不把更新断言或重建资产重复计入。第一回合共 115 处：工作目标 40 处、名词委派 67 处、演示标题与摘要中的旧表和接口名称 5 处、policy 注释中指 harness 的 adapter 3 处。原输入的 35 行工作目标和 72 行委派是行数，与出现次数不同。

`apps/console/src/MacroTaskChronology.tsx`：1 处（工作目标 0，名词委派 1）。

`apps/console/src/MacroTaskList.tsx`：30 处（工作目标 20，名词委派 10）。

`apps/console/src/MacroTaskOverview.tsx`：8 处（工作目标 1，名词委派 7）。

`apps/console/src/MacroTaskTimeline.tsx`：6 处（工作目标 1，名词委派 5）。

`apps/console/src/MacroTasks.tsx`：14 处（工作目标 10，名词委派 4）。

`apps/console/src/RunDetailPane.tsx`：3 处（工作目标 0，名词委派 3）。

`apps/console/src/TaskDetails.tsx`：4 处（工作目标 0，名词委派 4）。

`apps/console/src/Tasks.tsx`：9 处（工作目标 0，名词委派 9）。

`apps/console/src/WorkflowPanel.tsx`：1 处（工作目标 0，名词委派 1）。

`apps/console/src/api.ts`：3 处（工作目标 2，名词委派 1）。

`apps/console/src/inspector-card.ts`：7 处（工作目标 0，名词委派 7）。

`apps/console/src/macro-task-display.ts`：8 处（工作目标 1，名词委派 7）。

`apps/console/src/macro-task-fixtures.ts`：10 处（工作目标 4，名词委派 1，旧表和接口名称 5）。

`apps/console/src/macro-task-metrics.ts`：1 处（工作目标 0，名词委派 1）。

`apps/console/src/macro-task-stop.ts`：2 处（工作目标 0，名词委派 2）。

`apps/console/src/macro-task-types.ts`：1 处（工作目标 0，名词委派 1）。

`apps/console/src/task-state.ts`：3 处（工作目标 0，名词委派 3）。

`apps/console/src/use-macro-task-timeline.ts`：1 处（工作目标 1，名词委派 0）。

`apps/console/src/policy.ts`：3 处，将注释中的执行能力 token 与原生 harness 说明写成 `execution:<harness>`、executable harness 与 harness；实际能力 token 和代码不变。

保留“委派”8 处，均在“委派方”“原始委派方”“全部委派方”“未记录委派方”等表述中，表示由谁委派，分布于 Tasks.tsx（3 处）、TaskDetails.tsx（2 处）、MacroTaskList.tsx（2 处）与 console-data.ts（1 处）；非测试源码已无“工作目标”与名词“委派”。除上述演示数据旧名外，未发现需改写的英文界面 objective、goal、名词 delegation 或 profile；历史设计文件名 `objective-browser-0.15.1.md` 保留，像素布局的 adapter 仍表示连接层，执行记录的 adapter 字段保留。

将 macro-task-fixtures.ts 的参考路径改为 `docs/reference/macro-tasks.md`；preview-contract.test.ts 的三处注释改指 `tests/python/cli/test_host_preview.py`。这些路径修正另计，不纳入 115 处术语数。钉住新显示文字、演示标题与可访问名称的前端断言已同步更新。

## Python 文字

`blackboard/tasks/macro_tasks.py` 的未命名标题改为“未命名微任务”；`blackboard/tasks/workflow.py` 的 Host 协助请求改为“指定完整合法 buddy 继续同一微任务”；`blackboard/routing/router_boundary.py` 的独立 selection-request 报错改为“没有关联业务微任务”，继续 input 改为“继续既定微任务。”，重新路由 reason 改为“重新路由同一微任务”，缺少 revision 的报错改为“当前微任务 revision 未提供”。这些模板携带 runId、targetRunId 和微任务 revision，继续与重新路由的对象是微任务，因此按实际含义命名。Python 源码已无上述两类旧词。

## 三份说明逐条核对

Host 接口 S01 属实：normalize_buddy 以 CODING_HARNESSES 接受 claude，拒绝文字漏列它；补齐 claude，验证规则不变。

Host 接口 S02 属实：Store.task_submit 对所有 coding harnesses 拒绝低层执行提交；main.py 的 EPILOG 改为 coding harnesses。

Host 接口 S03 属实：macro_tasks.py 的 accepted 数只计已验收的根微任务；修正 MacroTaskCounts.accepted 注释，并说明它是相对于根数的进度分子。

Host 接口 S04 属实：HOST_MARKERS 已将 workflow.host_concluded 映射为 conclude，而 TimelineEventKind 漏列它；补齐现有服务事件类型，不改事件输出或状态取值。TimelineEvent.kind 原本为开放的 string，解析与现有显示路径没有被封闭。

Internals 第 1 条属实：Codex 与 Claude 的角色前缀改为“This is a Worker buddy turn for a micro task”，末句改为“Do not create another micro task”；更新 test_schema_worker.py 中两份完整提示词哈希，schema 哈希不变。

Internals 第 2 条属实：私有路径支持编码 harness 的连接件及 command，Unknown private harness adapter 混合了两个概念；改为“Unknown adapter for private state”。adapter 参数、错误详情与现有私有路径接口保持原形。

Internals 第 3 条属实：全仓 Python 搜索只找到 TABLE_CHANGED_REASON 的声明，没有调用者；删除这一个常量。

Internals 第 4 条属实：Router 边界继续、reroute 与 revision 提示中存在旧词；按上面的 Python 文字修正。

Internals 第 5 条按 Host 指令跳过：cli/cli_help.py 由另一微任务负责，本次未更改。

Operations 第 1 条按 Host 指令跳过：skills/hey-my-buddy/SKILL.md 由 Host 负责且已报告修改，本次未核验或更改。

Operations 第 2 条属实：user_policy.publish 的写入按提交顺序与 command receipts 处理，没有旧 revision gate；server.py 的模块说明改为 named, validated operations and command receipts。

Operations 第 3 条属实：当前分发是 shared skill；main.py 模块说明中的 plugin 改为 shared skill。

Operations 第 4 条属实：Host 预览测试实际在 tests/python/cli/test_host_preview.py；三处前端测试注释已修正。

共 13 条：11 条属实并完成，2 条按任务指令跳过；没有判断为不属实的条目，没有需要修改接口才能解决而未做的条目。

## 第一回合验证

所有 npm 命令的缓存均在检出内的 `tmp/npm-cache`，Playwright 浏览器在 `tmp/playwright-browsers`，浏览器预览用的 uv 缓存在 `tmp/uv-cache`；测试和构建使用检出内临时准备的 Node 24.15.0，符合 package.json 的 engines。首次默认 Node 25.8.1 的 npm ci 完成但报告引擎不符，随后在 Node 24.15.0 下重跑 npm ci，退出 0；锁文件与依赖版本未改。npm 的现有依赖审计报告 1 项 high，未在这次改名任务中升级依赖。

前端首轮 `npm --prefix apps/console test`：78 个文件、1,091 项测试，76 个文件与 1,089 项通过，desktop-console.test.tsx 的“keeps one note draft per model across rows and main pages without a lease”和 buddies/english.test.tsx 的“says nothing in Chinese through sections, account blocks, model rows, drawers and dialogs”分别因原有 5,000 ms 上限超时。两文件各单独重跑一次，分别 13 项和 6 项全部通过；没有修改测试超时、断言边界或实现来消除超时。最终源码的完整前端测试采用 `npm --prefix apps/console test -- --maxWorkers=2`，78 个文件、1,091 项全部通过，退出 0，耗时 107.31 秒。

`npm --prefix apps/console run test:browser` 退出 0：先完成 TypeScript 检查与 Vite 生产构建，再通过 96 项 Playwright 检查；没有因文字变化而修改布局或样式。生产构建替换旧 JS 资产为 `index-DKv13ZhN.js`，更新打包 assets/index.html；CSS 与图标资产没有差异。构建仍有既存的大于 500 kB chunk 提示。

Python 全部由 Host 指定的解释器执行 `PYTHONPATH=src <Host 指定解释器> -m hey_my_buddy.cli.checks <路径…>`，未传 --jobs 或设置 HMB_CHECKS_JOBS。首批选择 `tests/python/console/`、`tests/python/cli/`（含 test_host_preview.py）、`tests/python/protocol/`、`tests/python/buddy/roles/`、`tests/python/test_private_directories.py`、`tests/python/buddy/harnesses/test_private_adapter_invariants.py`、`tests/python/buddy/harnesses/test_adapter_usage.py`、`tests/python/buddy/harnesses/codex/test_codex.py`、`tests/python/buddy/harnesses/claude/test_claude.py`、`tests/python/blackboard/routing/test_router_boundary.py`、`tests/python/blackboard/routing/test_router_boundary_integration.py`、`tests/python/blackboard/routing/test_decision.py`、`tests/python/blackboard/tasks/test_macro_tasks.py`、`tests/python/blackboard/tasks/test_macro_task_summary_cache.py`、`tests/python/blackboard/tasks/test_macro_task_stop.py`、`tests/python/blackboard/tasks/test_console_macro_task_fixture.py`、`tests/python/blackboard/tasks/test_host_workflow.py`、`tests/python/blackboard/tasks/test_host_workflow_worker.py`、`tests/python/blackboard/tasks/test_workflow_preparation.py` 与 `tests/python/blackboard/tasks/test_workflow_real.py`；65 个文件、984 项测试全部通过，跳过 0，退出 0，运行器报告私有根已移除，没有失败文件需要重跑。

首批 Python 检查运行期间重建了前端资产，所以随后对最终资产补跑 `tests/python/console/` 与 `tests/python/cli/test_host_preview.py`；8 个文件、127 项测试全部通过，跳过 0，退出 0，私有根已移除。首批与补跑都使用运行器自己的系统临时私有根；未修改指定解释器的环境。

第一回合验证边界为本检出的源码、打包资产、合成预览与上述定向测试。未跑完整 Python 检查，未运行真实 harness 或模型调用，未安装产品，未操作日常服务、Worker、用户设置或凭据；Host 整合后的完整检查和验收另行进行。原始日志与临时工具均留在忽略的 tmp/ 下。

## 第二回合：单独的“目标”

根据 Host 补充的范围，对非测试控制台源码中余下的 22 处“目标”逐处核对；9 处指宏任务、11 处指微任务，共修改 20 处，2 处指整合去向而保留。累计界面文字、注释与演示数据的改名为 135 处；第一回合保留的“委派”仍为 8 处，均表示委派方。

宏任务层依据：MacroTaskList 搜索的是宏任务列表；MacroTasks 的停止按钮、确认按钮与提示调用 macro_task_stop；macro-task-stop-reply 验证同一命令的回应；MacroTaskOverview、MacroTaskTimeline 与 TaskDetails 的停止动作注释明确说明宏任务层；macro-task-fixtures 的 description 属于 macroTask。对应文字分别改为“搜索宏任务、项目或 ID”“停止宏任务”“确认停止宏任务”“该宏任务”及“本宏任务”。

微任务层依据：TaskDetails.parentRunId 指上级微任务；Tasks 搜索的是微任务执行记录；WorkflowPanel 的 Host 结论、runRevision、回合和 shutdown 事实属于当前微任务，原始文字为 task.task；StoragePanel 的 not-accepted 来自按 run_id 检查微任务验收状态的 cleanup_accepted_workspace，因此改为“微任务尚未验收”。MicroTaskDetailRows 的 taskSummary 取当前微任务 timeline row.taskSummary 或当前 task.task 的首行，macro-task-types 的同一字段也如此，故三处“目标摘要”显示与注释改为“微任务摘要”。其余分别改为“上级微任务”“微任务结果”“微任务版本”“记录细节（微任务与回合）”“微任务范围内”和“原始微任务”。

WorkflowPanel 的“整合目标”显示 integration.target.path/ref，“目标变更”显示 integration.beforeCommit/afterCommit，二者描述整合去向，按 Host 要求保留。没有层级拿不准的条目，没有需要改接口而未做的条目；所有修改只涉及文字与注释。

第二回合各文件的改名次数：

`apps/console/src/MacroTaskList.tsx`：1 处。

`apps/console/src/MacroTaskOverview.tsx`：3 处。

`apps/console/src/MacroTaskTimeline.tsx`：1 处。

`apps/console/src/MacroTasks.tsx`：3 处。

`apps/console/src/StoragePanel.tsx`：1 处。

`apps/console/src/TaskDetails.tsx`：2 处。

`apps/console/src/Tasks.tsx`：1 处。

`apps/console/src/WorkflowPanel.tsx`：5 处。

`apps/console/src/macro-task-fixtures.ts`：1 处。

`apps/console/src/macro-task-stop-reply.ts`：1 处。

`apps/console/src/macro-task-types.ts`：1 处。

更新 macro-task-detail.test.tsx、WorkflowPanel.test.tsx 与 macro-task-timeline.test.tsx 中钉住停止操作、记录细节、停止范围和摘要名称的断言及注释，测试结构和超时不变；没有新增照搬文字实现的测试，没有改布局与样式。

第二回合在检出内的 Node 24.15.0 下重跑 `npm --prefix apps/console ci`，退出 0，缓存仍在 `tmp/npm-cache`；`npm --prefix apps/console test -- --maxWorkers=2` 通过 78 个文件、1,091 项测试，退出 0，耗时 96.03 秒，没有失败或单独重跑。

第二回合 `npm --prefix apps/console run test:browser` 先完成 TypeScript 与 Vite 构建，再运行 96 项检查：95 项通过，catalogue.check.ts 的“the catalogue in zh-CN at 1280px in dark / shows unsaved marks on a setting, an effort and the closed row, and keeps the save bar at the bottom”在定位“未保存的修改”区域时达到 30,000 ms 上限，尚未执行几何断言；该目录页与该区域的文字本轮未改。使用 `npm --prefix apps/console run test:browser -- --grep 'the catalogue in zh-CN at 1280px in dark.*shows unsaved marks'` 单独重跑一次，1 项通过，退出 0，耗时 2.2 秒；重建出的 JS 名称相同，没有修改样式或超时。

第二回合打包 JS 为 `index-YXKcBnJj.js`，assets/index.html 已引用该资产，CSS 与图标无变化；缓存与浏览器目录仍全部在检出内。

第二回合对最终资产运行 `PYTHONPATH=src <Host 指定解释器> -m hey_my_buddy.cli.checks tests/python/console/ tests/python/cli/test_host_preview.py`，8 个文件、127 项测试全部通过，跳过 0，退出 0，私有根已移除；未修改 Python 源码，因此没有重跑第一回合的其他定向文件或完整 Python 检查。

## 第三回合：把 buddy 称为“配置”

根据 Host 补充的范围，逐处核对非测试控制台源码的 38 处“配置”；33 处指一个 buddy，改为 buddy；api.ts 的“Buddy 配置 page”改为“Buddy page”，另计 1 处；保留 4 处设置含义的“配置”。draft.ts 的“家族备注”“模型家族”分别改为“模型服务备注”“模型服务”，共 2 处。本轮合计 36 处，累计界面文字、注释与样例数据的改名为 171 处；保留的“委派”仍为 8 处，指整合去向的“目标”仍为 2 处。

执行、当前选择、候选、路由历史与回合里的旧称均来自 buddy、selectedBuddy、buddyRevision、row.buddy、span.buddy 或 buddies 数组，这些值表示 harness/provider/model/effort 的一组身份；据此分别改为“执行 buddy”“当前执行 buddy”“选中的 buddy”“buddy 版本”“N 个 buddy”“尚未确定执行 buddy”“未选定 buddy”“buddy 未记录”“补齐 buddy”“补充 buddy”“更换 buddy”“buddy 来源未记录”“执行回合与 buddy 对应”“buddy 历史响应不完整”，图例改为“执行 buddy 图例”，检查器卡片的字段名称和注释使用 buddy。draft.ts 的非模型服务冲突主体携带 buddyId，改为“buddy <id> 的…”；模型服务备注与并发主体用“模型服务 <id> 的…”。

保留 DecisionDetails.tsx 的“预算配置”，因为它显示 Router 的 budget.preset；保留 buddies-harness.ts 的“已配置”，因为它表示 harness 的原生路径与账户就绪事实。DecisionDetails.tsx 与 RoutingDetails.tsx 各有一处“冻结记录，不随当前配置变化”，显示的是 routingBasisSummary 对已冻结 candidateCount、excludedBuddies 与 excludedCount 的说明，指当前候选和用户排除等路由设置，不称呼某个 buddy，故按 Host 要求保留。没有拿不准的条目，没有需要修改接口才能解决而未做的条目。

第三回合各文件的改名次数：

`apps/console/src/DecisionDetails.tsx`：4 处（配置 4，家族 0）。

`apps/console/src/MacroTaskTimeline.tsx`：2 处（配置 2，家族 0）。

`apps/console/src/RoutingDetails.tsx`：9 处（配置 9，家族 0）。

`apps/console/src/RoutingPanel.tsx`：3 处（配置 3，家族 0）。

`apps/console/src/WorkflowPanel.tsx`：3 处（配置 3，家族 0）。

`apps/console/src/api.ts`：1 处（配置 1，家族 0）。

`apps/console/src/console-data.ts`：1 处（配置 1，家族 0）。

`apps/console/src/draft.ts`：3 处（配置 1，家族 2）。

`apps/console/src/host-workflow.ts`：1 处（配置 1，家族 0）。

`apps/console/src/inspector-card.ts`：5 处（配置 5，家族 0）。

`apps/console/src/macro-task-display.ts`：3 处（配置 3，家族 0）。

`apps/console/src/use-buddy-history.ts`：1 处（配置 1，家族 0）。

本轮同步更新 App.test.tsx、WorkflowPanel.test.tsx、buddy-name.test.tsx、macro-task-detail.test.tsx、macro-task-display-016.test.ts、macro-task-timeline.test.tsx、routing-console.test.tsx、routing-timeline.test.tsx、timeline-layers.test.tsx 与 buddies/roles-view.test.tsx 的文字断言、注释和 buddy 样例名称；保留 harness“已配置”和 Router 预算、审阅设置的样例。没有修改布局、样式、标签宽度阈值、接口、测试超时或测试结构。

第三回合使用检出内的 Node 24.15.0 重跑 `npm --prefix apps/console ci`，退出 0，缓存仍在 `tmp/npm-cache`；`npm --prefix apps/console test -- --maxWorkers=2` 通过 78 个文件、1,091 项测试，退出 0，耗时 91.91 秒，没有失败或单独重跑。

第三回合 `npm --prefix apps/console run test:browser` 先完成 TypeScript 检查与 Vite 生产构建，再通过 96 项 Playwright 检查，退出 0，浏览器检查耗时 17.2 秒，没有失败、标签超长或单独重跑。打包 JS 为 `index-AshVBtk4.js`，assets/index.html 已引用该资产，CSS 与图标未变；未改变布局、样式或标签宽度阈值。

第三回合使用 Host 指定解释器执行 `PYTHONPATH=src <Host 指定解释器> -m hey_my_buddy.cli.checks tests/python/console/ tests/python/cli/test_host_preview.py`，8 个文件、127 项测试全部通过，跳过 0，退出 0，私有根已移除；没有失败文件需要重跑。未修改 Python 源码，未重跑其他定向文件或完整 Python 检查；未安装产品或运行真实 harness、日常服务、Worker 与模型调用。第三回合原始日志保留在忽略的 tmp/turn3-*.log。
