# 控制台停止的范围

本次按 ADR-021 第 21 条与 L17 实现急停，保留宏任务标题栏里的入口，等待 Host 验收；宏任务不会因为停止而关闭。

## 状态与归属

| 微任务状态 | 控制台停止时 | 理由 |
| --- | --- | --- |
| `executing` | 取消，包括排队、路由与运行 | 这些工作仍会启动或继续执行。 |
| `waiting-helpers` | 取消 | 协助任务结束后会续接，必须先取消父项以阻止新回合。 |
| `waiting-owned` | 取消 | 名下微任务的结果会叫醒 Lead，必须先取消父项。 |
| `delivered` | 保持原状 | 交付等待 Host 验收。 |
| `awaiting-host` | 保持原状 | 决定留给 Host，开放的请求保留。 |
| `failed`、`cancelled` | 保持原状 | 不把失败改成取消，也不覆盖已有的取消说明。 |
| `accepted`、`concluded` | 保持原状 | 保留验收与结论。 |

停止在同一个写事务中冻结完整的归属树，包含协助任务与 Lead 名下的微任务，先取消进行中的父项，再取消进行中的下级；保持原状的父项不阻断遍历，它下面进行中的协助任务与名下微任务同样会取消。已交付的名下微任务在 Lead 被停止时保留。取消请求与进程结束是两件事：未确认结束的执行继续显示正在停止或停止未确认，凭 Worker 的真实收尾证据确认结束。

## 回复与存储升级

`macro_task.stop` 的新回复为 `{macroTaskId, results, duplicate}`，`results` 是统一清单，每项为 `{runId, state, stopped}`，覆盖此次冻结范围里的微任务；`stopped: true` 表示此次请求已将其取消，`state` 为 `cancelled`，不承诺进程已经结束，`stopped: false` 表示保持原状态。旧的 `runIds`、`acceptedRunIds` 与 `concludedRunIds` 字段已删除。同一个 `commandId` 重放保存的清单，仅把 `duplicate` 标为 `true`，不扩大到后来加入的工作。

命令回执的形式在待做的 schema 21→22 升级中换算。换算后的每项严格只有 `runId`、`state`、`stopped`，与新写入的回复同形；旧的 `cancelled`、`duplicate`、`note` 与 compact 字段不进入新清单，旧的已验收、已结论清单换成同一份 `results` 中的状态记录。旧回执仅记录当时的顶层范围，升级不补造下级历史；原始回执仍由安装的已验证备份保留。升级严格检查旧形式、身份与状态，无法识别的记录使整个事务回滚，控制台也只解析现在这一种形式。样板看板升级后的数据库结构与新建看板比较由升级测试验证。

安装迁移核对把这次回执换算作为声明过的改动：迁移前先对所有 `commands` 行作预期投影，只替换 `kind=macro_task.stop` 的 `response_json`，再计算整行、整表的指纹；只有迁移后的指纹和行数与该投影相同才通过。停止命令的其他列、其他命令行，以及原来要求保留的其他表仍严格比对。已验证备份的快照替换迁移前快照时保留这个预期指纹；启动后的 `verify_started` 使用迁移完成后的快照，继续核对包括换算后 `commands` 在内的原始指纹，不另开豁免。

Host 通过 `get` 或 `status` 读取取消说明中的 `actor.role: console` 和 `reason`。原有取消事件提供这些事实；本次补入 `workflow.lead_task_cancelled` 的读取，使名下微任务也能读到自己的取消说明。

## 控制台与提醒

后台汇总完整归属树里的 `stoppableRoots`、`stoppableHelpers`、`delivered` 与 `awaitingHost`，计数不受筛选与分页影响。没有进行中的工作时停止按钮禁用；确认框把宏任务标题放在独立的一行，超长时省略并可悬停读取完整标题，下方以简短的清单列出将停止的微任务与协助任务数量，以及已交付、等 Host、已验收的保留数量。英文计数使用 i18next 的单复数。回复解析、确认框与停止状态文字经中文和英文 i18n 提供，确认停止后仍由刷新取得的完整时间轴核对结束证据。微任务取消后晚到的正常执行回执可能仍记为 `completed`，只要微任务保持取消且结束证据已确认，界面会正确确认停止，不要求把真实的执行结果改成取消。

确认框打开后工作可能自行交回，若请求最终没有取消任何微任务，回复仍列出保留的状态，界面显示“无需停止”，不把这次请求描述为已经停止了工作。

设置页的提醒卡片删除两行说明段落，由标题、阈值单位与范围说明含义。界面只接受 1–1440 的整分钟，提交给服务和存储的值仍为秒。接口现存的非整分钟值不会显示长小数或自动舍入：输入先留空，简短显示当前保存的秒数，只有用户明确填写整分钟才可保存。提醒调度先在读事务中检查期限，只有发现到期提醒才开启写事务，并在写锁内重新检查，避免并发的结果、原生事件或其他收集者造成过期或重复提醒。

## 验证

本轮 Python 使用 Host 指定的解释器，通过 `PYTHONPATH=src <python> -m hey_my_buddy.cli.checks <文件或目录>` 运行，使用默认进程数和检查运行器的私有看板；41 个文件、492 项测试通过，私有根目录已清除，退出 0，未运行完整检查。

检查范围为 `tests/python/install/`、`tests/python/blackboard/store/`、`tests/python/console/`，以及以下文件：

```text
tests/python/blackboard/tasks/test_macro_task_stop.py
tests/python/blackboard/tasks/test_workflow_stop_surface.py
tests/python/blackboard/tasks/test_workflow_cancellation.py
tests/python/blackboard/tasks/test_wrap_up.py
tests/python/blackboard/tasks/test_reminders.py
tests/python/blackboard/service/test_micro_task_service_stop.py
tests/python/blackboard/service/test_reminders.py
tests/python/blackboard/service/test_console_reads.py
tests/python/cli/test_cli_views.py
```

停止范围的 14 项测试继续覆盖全部指定状态、保留父项下的活动协助任务、Lead 名下已交付微任务、命令重放、Host 读取取消说明，以及停止后的协助任务凭据不会消耗原来保留的 Host 边界。升级样板测试现在带上旧停止回执与另一种命令，经过真实备份、迁移、迁移核对和启动后数据核对，服务进程的切换用测试替身；另有反例确认篡改换算后的回执、停止命令的其他列、其他命令行、行数或其他保留表会被拒绝，启动后篡改两种命令的回复也会被拒绝。样板升级与新建结构比较、错误回滚，以及提醒空闲或未到期时不开写事务的检查通过。

前端最终在检出内的临时 Node 24.15.0 环境中运行下列命令，全部退出 0；单元测试通过 82 个文件、1124 项，浏览器检查通过 111 项，后者在合成预览上检查中英文停止确认框、单独省略的长标题、只读拒绝和窄窗口下的提醒设置。浏览器命令同时重建打包资源，最终入口引用 `assets/index-DqF5puBX.js` 与 `assets/index-DaWyZ47C.css`。这里只验证源码、私有服务与合成预览，没有安装到日常服务。

```sh
npm --prefix apps/console ci
npm --prefix apps/console run typecheck
npm --prefix apps/console test
npm --prefix apps/console run test:browser
```

前端本轮日志留在忽略的 `tmp/frontend-ci-revision.log`、`tmp/frontend-typecheck-revision.log`、`tmp/frontend-unit-revision.log` 与 `tmp/frontend-browser-revision.log`。`git diff --check` 通过。

最终资源落定后补跑 `tests/python/install/test_distribution.py` 与 `tests/python/install/test_packaging.py`，2 个文件、14 项测试通过，私有根目录已清除，退出 0。

## 执行偏差

首次安装 Playwright 浏览器依赖时未指定检出内的浏览器缓存，安装器报告清理 `~/Library/Caches/ms-playwright/chromium_headless_shell-1248` 与 `~/Library/Caches/ms-playwright/ffmpeg-1013`，随后在新浏览器下载的 0% 处中断，退出码为 130。这违反了本次只在分配检出内操作的要求；没有独立检查删除结果，记录的证据是安装器输出，保存在忽略的 `tmp/playwright-cache-cleanup.txt`。随后通过 `PLAYWRIGHT_BROWSERS_PATH` 指向检出内的 `tmp/playwright-browsers` 完成依赖安装，后续浏览器检查使用该路径。Host 已核对缓存现状，要求本轮不再处理；本轮继续使用检出内的浏览器目录，并设置 `PLAYWRIGHT_SKIP_BROWSER_GC=1`，没有再操作外部缓存。
