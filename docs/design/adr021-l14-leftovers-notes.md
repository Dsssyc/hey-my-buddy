# L14：界面文字与源码遗留措辞

以 `c8950dc3` 为输入，在受管检出内完成范围内的文字与注释修改；未更改布局、样式、接口名称、字段、命令、存储形式、状态取值、版本号或 schema 版本号。

## 界面与注释

计数按非测试控制台源码里被替换的旧术语每次出现计，含显示文字、aria-label、title、错误提示、演示数据与注释，不把更新断言或重建资产重复计入。共 115 处：工作目标 40 处、名词委派 67 处、演示标题与摘要中的旧表和接口名称 5 处、policy 注释中指 harness 的 adapter 3 处。原输入的 35 行工作目标和 72 行委派是行数，与出现次数不同。

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

## 验证

所有 npm 命令的缓存均在检出内的 `tmp/npm-cache`，Playwright 浏览器在 `tmp/playwright-browsers`，浏览器预览用的 uv 缓存在 `tmp/uv-cache`；测试和构建使用检出内临时准备的 Node 24.15.0，符合 package.json 的 engines。首次默认 Node 25.8.1 的 npm ci 完成但报告引擎不符，随后在 Node 24.15.0 下重跑 npm ci，退出 0；锁文件与依赖版本未改。npm 的现有依赖审计报告 1 项 high，未在这次改名任务中升级依赖。

前端首轮 `npm --prefix apps/console test`：78 个文件、1,091 项测试，76 个文件与 1,089 项通过，desktop-console.test.tsx 的“keeps one note draft per model across rows and main pages without a lease”和 buddies/english.test.tsx 的“says nothing in Chinese through sections, account blocks, model rows, drawers and dialogs”分别因原有 5,000 ms 上限超时。两文件各单独重跑一次，分别 13 项和 6 项全部通过；没有修改测试超时、断言边界或实现来消除超时。最终源码的完整前端测试采用 `npm --prefix apps/console test -- --maxWorkers=2`，78 个文件、1,091 项全部通过，退出 0，耗时 107.31 秒。

`npm --prefix apps/console run test:browser` 退出 0：先完成 TypeScript 检查与 Vite 生产构建，再通过 96 项 Playwright 检查；没有因文字变化而修改布局或样式。生产构建替换旧 JS 资产为 `index-DKv13ZhN.js`，更新打包 assets/index.html；CSS 与图标资产没有差异。构建仍有既存的大于 500 kB chunk 提示。

Python 全部由 Host 指定的解释器执行 `PYTHONPATH=src <Host 指定解释器> -m hey_my_buddy.cli.checks <路径…>`，未传 --jobs 或设置 HMB_CHECKS_JOBS。首批选择 `tests/python/console/`、`tests/python/cli/`（含 test_host_preview.py）、`tests/python/protocol/`、`tests/python/buddy/roles/`、`tests/python/test_private_directories.py`、`tests/python/buddy/harnesses/test_private_adapter_invariants.py`、`tests/python/buddy/harnesses/test_adapter_usage.py`、`tests/python/buddy/harnesses/codex/test_codex.py`、`tests/python/buddy/harnesses/claude/test_claude.py`、`tests/python/blackboard/routing/test_router_boundary.py`、`tests/python/blackboard/routing/test_router_boundary_integration.py`、`tests/python/blackboard/routing/test_decision.py`、`tests/python/blackboard/tasks/test_macro_tasks.py`、`tests/python/blackboard/tasks/test_macro_task_summary_cache.py`、`tests/python/blackboard/tasks/test_macro_task_stop.py`、`tests/python/blackboard/tasks/test_console_macro_task_fixture.py`、`tests/python/blackboard/tasks/test_host_workflow.py`、`tests/python/blackboard/tasks/test_host_workflow_worker.py`、`tests/python/blackboard/tasks/test_workflow_preparation.py` 与 `tests/python/blackboard/tasks/test_workflow_real.py`；65 个文件、984 项测试全部通过，跳过 0，退出 0，运行器报告私有根已移除，没有失败文件需要重跑。

首批 Python 检查运行期间重建了前端资产，所以随后对最终资产补跑 `tests/python/console/` 与 `tests/python/cli/test_host_preview.py`；8 个文件、127 项测试全部通过，跳过 0，退出 0，私有根已移除。首批与补跑都使用运行器自己的系统临时私有根；未修改指定解释器的环境。

验证边界为本检出的源码、打包资产、合成预览与上述定向测试。未跑完整 Python 检查，未运行真实 harness 或模型调用，未安装产品，未操作日常服务、Worker、用户设置或凭据；Host 整合后的完整检查和验收另行进行。原始日志与临时工具均留在忽略的 tmp/ 下。
