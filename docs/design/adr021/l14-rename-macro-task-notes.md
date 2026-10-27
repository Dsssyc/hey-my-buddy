# 宏任务一族改名说明

在输入提交 `652deaec` 的独立检出上执行第 5 行微任务：只改宏任务一族和回合任务文字的名字，版本仍为 `0.34.1`，schema 仍为 `19`。宏任务新标识为 `macro-<id>`，独立微任务的 `run:<rootRunId>` 不变。未安装产品，未连接日常服务或 Worker，未调用模型；本说明是 Worker 的执行记录，等待 Host 验收。

计数按原源码与当前源码的同一行词法拼写对照：共 2518 处改动、157 对旧名与新名，含注释、内部符号、CSS 与 i18n 键、测试和样例标识；不计重建资产的压缩输出或文件路径改名。清单之外有 131 个不同的旧名字，按具体原拼写计数，表中“补充”为这些名字；`objective_created` 是清单中完整事件 `workflow.objective_created` 的词干，归入清单。其余完整路径与操作标签的补充核对与词法处数重叠，不另相加。

同一个旧词按含义分开：宏任务对象与回应是 `macroTask`、`macroTaskId`、`macroTaskOf`、`macroTasks`，种类与标题来源是 `macro-task`，Python 表与内部名字按 `macro_task` 写；任务文字字段是 `task` 与 `taskTruncated`，SQL 查询任务文字的内部列别名是 `task`，回合输入里原局部变量 `objective` 写成 `task_text`，以免覆盖该函数已有的任务记录参数 `task`。

| 旧名字 | 新名字 | 处数 | 来源 |
| --- | --- | ---: | --- |
| `ConsoleObjectiveCacheTests` | `ConsoleMacroTaskCacheTests` | 1 | 补充 |
| `ConsoleObjectiveFixtureTests` | `ConsoleMacroTaskFixtureTests` | 1 | 补充 |
| `ConsoleObjectiveTests` | `ConsoleMacroTaskTests` | 1 | 补充 |
| `OBJECTIVES` | `MACRO_TASKS` | 6 | 补充 |
| `OBJECTIVE_ID` | `MACRO_TASK_ID` | 4 | 补充 |
| `OBJECTIVE_LIST_PARAMETERS` | `MACRO_TASK_LIST_PARAMETERS` | 2 | 补充 |
| `OBJECTIVE_PATTERN` | `MACRO_TASK_PATTERN` | 3 | 补充 |
| `OBJECTIVE_REQUIRED` | `MACRO_TASK_REQUIRED` | 1 | 清单 |
| `OBJECTIVE_TIMELINE_PARAMETERS` | `MACRO_TASK_TIMELINE_PARAMETERS` | 2 | 补充 |
| `OBJ_A` | `MACRO_A` | 22 | 补充 |
| `OBJ_B` | `MACRO_B` | 12 | 补充 |
| `Objective` | `MacroTask` | 2 | 补充 |
| `Objective-level` | `MacroTask-level` | 6 | 补充 |
| `ObjectiveAdmissionTests` | `MacroTaskAdmissionTests` | 1 | 补充 |
| `ObjectiveCategory` | `MacroTaskCategory` | 6 | 补充 |
| `ObjectiveChronology` | `MacroTaskChronology` | 4 | 补充 |
| `ObjectiveCounts` | `MacroTaskCounts` | 5 | 补充 |
| `ObjectiveFilter` | `MacroTaskFilter` | 9 | 补充 |
| `ObjectiveList` | `MacroTaskList` | 44 | 补充 |
| `ObjectiveListProps` | `MacroTaskListProps` | 2 | 补充 |
| `ObjectiveMetrics` | `MacroTaskMetrics` | 2 | 补充 |
| `ObjectiveOverview` | `MacroTaskOverview` | 10 | 补充 |
| `ObjectivePage` | `MacroTaskPage` | 20 | 补充 |
| `ObjectiveQuery` | `MacroTaskQuery` | 13 | 补充 |
| `ObjectiveReadCacheIntegrationTests` | `MacroTaskReadCacheIntegrationTests` | 1 | 补充 |
| `ObjectiveReadTests` | `MacroTaskReadTests` | 1 | 补充 |
| `ObjectiveStopEntry` | `MacroTaskStopEntry` | 10 | 补充 |
| `ObjectiveStopPhase` | `MacroTaskStopPhase` | 3 | 补充 |
| `ObjectiveStopResult` | `MacroTaskStopResult` | 9 | 补充 |
| `ObjectiveStopStatus` | `MacroTaskStopStatus` | 2 | 补充 |
| `ObjectiveStopTests` | `MacroTaskStopTests` | 1 | 补充 |
| `ObjectiveSummary` | `MacroTaskSummary` | 55 | 补充 |
| `ObjectiveSummaryCacheTests` | `MacroTaskSummaryCacheTests` | 1 | 补充 |
| `ObjectiveTimeline` | `MacroTaskTimeline` | 215 | 补充 |
| `ObjectiveTimelineData` | `MacroTaskTimelineData` | 21 | 补充 |
| `ObjectiveTimelineProps` | `MacroTaskTimelineProps` | 26 | 补充 |
| `Objectives` | `MacroTasks` | 21 | 补充 |
| `ObjectivesWorkspace` | `MacroTasksWorkspace` | 2 | 补充 |
| `QUOTA_OBJECTIVE` | `QUOTA_TASK` | 2 | 补充 |
| `Same-objective` | `Same-macroTask` | 1 | 补充 |
| `Work-objective` | `Work-macro-task` | 1 | 补充 |
| `_OBJECTIVE_ID` | `_MACRO_TASK_ID` | 2 | 补充 |
| `_objective_read` | `_macro_task_read` | 3 | 补充 |
| `_objective_summary_cache` | `_macro_task_summary_cache` | 5 | 补充 |
| `attach_objective` | `attach_macro_task` | 3 | 补充 |
| `console-objective-cache-` | `console-macro-task-cache-` | 1 | 补充 |
| `console-objective-environment-` | `console-macro-task-environment-` | 1 | 补充 |
| `console-objective-fixture-` | `console-macro-task-fixture-` | 1 | 补充 |
| `console-objective-http-` | `console-macro-task-http-` | 1 | 补充 |
| `console-objective-isolation-` | `console-macro-task-isolation-` | 1 | 补充 |
| `console_objective_board` | `console_macro_task_board` | 1 | 补充 |
| `console_objective_board_fixture` | `console_macro_task_board_fixture` | 1 | 补充 |
| `data-objective-id` | `data-macro-task-id` | 9 | 补充 |
| `emptyObjectives` | `emptyMacroTasks` | 7 | 补充 |
| `expectedObjectiveId` | `expectedMacroTaskId` | 2 | 补充 |
| `hostEventsOpenByObjective` | `hostEventsOpenByMacroTask` | 2 | 补充 |
| `new-objective` | `macro-new` | 1 | 补充 |
| `obj-` | `macro-` | 78 | 清单 |
| `objective` | `macro task` | 35 | 清单 |
| `objective` | `macro-task` | 17 | 清单 |
| `objective` | `macroTask` | 200 | 清单 |
| `objective` | `macro_task` | 28 | 清单 |
| `objective` | `task` | 14 | 清单 |
| `objective` | `task_text` | 5 | 清单 |
| `objective-description` | `macro-task-description` | 4 | 补充 |
| `objective-display` | `macro-task-display` | 25 | 补充 |
| `objective-filters-button` | `macro-task-filters-button` | 1 | 补充 |
| `objective-filters-popover` | `macro-task-filters-popover` | 2 | 补充 |
| `objective-fixtures` | `macro-task-fixtures` | 18 | 补充 |
| `objective-http-1` | `macro-task-http-1` | 1 | 补充 |
| `objective-http-2` | `macro-task-http-2` | 1 | 补充 |
| `objective-level` | `macro-task-level` | 6 | 补充 |
| `objective-list` | `macro-task-list` | 5 | 清单 |
| `objective-list-title-text` | `macro-task-list-title-text` | 2 | 补充 |
| `objective-metrics` | `macro-task-metrics` | 3 | 补充 |
| `objective-response-cache-` | `macro-task-response-cache-` | 1 | 补充 |
| `objective-row` | `macro-task-row` | 5 | 补充 |
| `objective-row-progress` | `macro-task-row-progress` | 2 | 补充 |
| `objective-row-state` | `macro-task-row-state` | 2 | 补充 |
| `objective-row-vitals` | `macro-task-row-vitals` | 5 | 补充 |
| `objective-stop` | `macro-task-stop` | 2 | 补充 |
| `objective-stop-reply` | `macro-task-stop-reply` | 2 | 补充 |
| `objective-summary-cache-` | `macro-task-summary-cache-` | 1 | 补充 |
| `objective-summary-seed-` | `macro-task-summary-seed-` | 1 | 补充 |
| `objective-timeline` | `macro-task-timeline` | 11 | 清单 |
| `objective-timeline-layout` | `macro-task-timeline-layout` | 12 | 补充 |
| `objective-timeline-scale` | `macro-task-timeline-scale` | 4 | 补充 |
| `objective-title-row` | `macro-task-title-row` | 7 | 补充 |
| `objective-types` | `macro-task-types` | 37 | 补充 |
| `objective-update` | `macro-task-update` | 3 | 补充 |
| `objective-updates` | `macro-task-updates` | 1 | 补充 |
| `objective-vitals` | `macro-task-vitals` | 5 | 补充 |
| `objectiveCount` | `macroTaskCount` | 2 | 补充 |
| `objectiveId` | `macroTaskId` | 379 | 清单 |
| `objectiveListWallMs` | `macroTaskListWallMs` | 1 | 补充 |
| `objectiveListWallNs` | `macroTaskListWallNs` | 3 | 补充 |
| `objectiveMetrics` | `macroTaskMetrics` | 13 | 补充 |
| `objectiveOf` | `macroTaskOf` | 14 | 清单 |
| `objectivePageFixture` | `macroTaskPageFixture` | 1 | 补充 |
| `objectiveProgressText` | `macroTaskProgressText` | 6 | 补充 |
| `objectiveRail` | `macroTaskRail` | 15 | 补充 |
| `objectiveRow` | `macroTaskRow` | 2 | 补充 |
| `objectiveStateLabel` | `macroTaskStateLabel` | 9 | 补充 |
| `objectiveSummary` | `macroTaskSummary` | 19 | 补充 |
| `objectiveTimeline` | `macroTaskTimeline` | 34 | 补充 |
| `objectiveTimelineFixture` | `macroTaskTimelineFixture` | 167 | 补充 |
| `objectiveTitle` | `macroTaskTitle` | 5 | 补充 |
| `objectiveTruncated` | `taskTruncated` | 2 | 清单 |
| `objective_console_preview` | `macro_task_console_preview` | 15 | 补充 |
| `workflow.objective_created` | `workflow.macro_task_created` | 18 | 清单 |
| `objective_id` | `macro_task_id` | 72 | 清单 |
| `objective_list` | `macro_task_list` | 25 | 清单 |
| `objective_page` | `macro_task_page` | 6 | 补充 |
| `objective_stop` | `macro_task_stop` | 43 | 清单 |
| `objective_summary` | `macro_task_summary` | 4 | 补充 |
| `objective_summary_cache` | `macro_task_summary_cache` | 2 | 补充 |
| `objective_timeline` | `macro_task_timeline` | 39 | 清单 |
| `objectives` | `macro tasks` | 5 | 清单 |
| `objectives` | `macro-tasks` | 41 | 清单 |
| `objectives` | `macroTasks` | 216 | 清单 |
| `objectives` | `macro_tasks` | 109 | 清单 |
| `objectives-page` | `macro-tasks-page` | 4 | 补充 |
| `objectives_activity_idx` | `macro_tasks_activity_idx` | 1 | 清单 |
| `objectives_project_idx` | `macro_tasks_project_idx` | 1 | 清单 |
| `openObjective` | `openMacroTask` | 21 | 补充 |
| `parse_objective_query` | `parse_macro_task_query` | 2 | 补充 |
| `per-objective` | `per-macroTask` | 1 | 补充 |
| `same-objective` | `same-macroTask` | 2 | 补充 |
| `secondObjective` | `secondMacroTask` | 3 | 补充 |
| `selectObjective` | `selectMacroTask` | 4 | 补充 |
| `selectionByObjective` | `selectionByMacroTask` | 2 | 补充 |
| `setHostEventsOpenByObjective` | `setHostEventsOpenByMacroTask` | 2 | 补充 |
| `setSelectionByObjective` | `setSelectionByMacroTask` | 2 | 补充 |
| `stop-objective-body` | `stop-macro-task-body` | 2 | 补充 |
| `stop-objective-title` | `stop-macro-task-title` | 2 | 补充 |
| `stop_objective` | `stop_macro_task` | 2 | 补充 |
| `test_console_objective_fixture` | `test_console_macro_task_fixture` | 2 | 补充 |
| `test_duplicate_objective_id_corrections_cannot_reuse_old_description` | `test_duplicate_macro_task_id_corrections_cannot_reuse_old_description` | 1 | 补充 |
| `test_list_groups_objectives_and_standalone_roots_by_latest_activity` | `test_list_groups_macro_tasks_and_standalone_roots_by_latest_activity` | 1 | 补充 |
| `test_objective_description_never_enters_selector_or_worker` | `test_macro_task_description_never_enters_selector_or_worker` | 1 | 补充 |
| `test_objective_id_survives_brief_submit_and_context_recovery_get` | `test_macro_task_id_survives_brief_submit_and_context_recovery_get` | 1 | 补充 |
| `test_objective_list_and_timeline_routes` | `test_macro_task_list_and_timeline_routes` | 1 | 补充 |
| `test_objective_metadata_and_description_raw_corrections` | `test_macro_task_metadata_and_description_raw_corrections` | 1 | 补充 |
| `test_objective_of_is_atomic_scoped_and_idempotent` | `test_macro_task_of_is_atomic_scoped_and_idempotent` | 1 | 补充 |
| `test_query_matches_objective_project_hosts_and_configuration` | `test_query_matches_task_project_hosts_and_configuration` | 1 | 补充 |
| `test_task_objective_and_timeline_reads_are_conditional` | `test_task_macro_task_and_timeline_reads_are_conditional` | 1 | 补充 |
| `timeline-obj-a` | `timeline-macro-a` | 10 | 补充 |
| `use-objective-list` | `use-macro-task-list` | 2 | 补充 |
| `use-objective-timeline` | `use-macro-task-timeline` | 2 | 补充 |
| `useObjectiveList` | `useMacroTaskList` | 7 | 补充 |
| `useObjectiveStop` | `useMacroTaskStop` | 3 | 补充 |
| `useObjectiveTimeline` | `useMacroTaskTimeline` | 6 | 补充 |
| `validObjectiveSummary` | `validMacroTaskSummary` | 3 | 补充 |
| `views-objective` | `views-macro-task` | 1 | 补充 |
| `work-objective` | `work macro task` | 12 | 补充 |
| `work-objective` | `work-macro-task` | 4 | 补充 |
| `workflow_runs_objective_idx` | `workflow_runs_macro_task_idx` | 1 | 清单 |

三个索引是 `objectives_activity_idx → macro_tasks_activity_idx`、`objectives_project_idx → macro_tasks_project_idx`、`workflow_runs_objective_idx → workflow_runs_macro_task_idx`，各 1 处。新板直接建 `macro_tasks` 和 `macro_task_id`，既不读取旧宏任务表，也不转换工作历史；升级测试的 `HISTORY_TABLES` 清点的是升级后的新板，因此改为 `macro_tasks`。升级基点的样板及生成器按 0.34.1 的形式保留，设置从 schema 18 的旧表读取的逻辑不变。

完整复合名字的核对如下；`/api/objectives` 的前缀计数包括其时间线路由。

| 旧名字 | 新名字 | 已改处数 |
| --- | --- | ---: | --- |
| `objective.list` | `macro_task.list` | 2 |
| `objective.stop` | `macro_task.stop` | 4 |
| `objective.timeline` | `macro_task.timeline` | 2 |
| `/api/objectives` | `/api/macro-tasks` | 34 |
| `/private/api/objectives` | `/private/api/macro-tasks` | 2 |

`normalizedObjectives` 及其旧工作区身份换算已经不存在，跳过。输入树的 CLI 和传输映射只提供 `objective-list` 与 `objective-timeline`，已改为 `macro-task-list` 与 `macro-task-timeline`；输入树没有 `objective-stop` CLI，停止宏任务继续只通过现有控制台路由与 C-Two 操作 `macro_task_stop` 提供，没有新增 CLI、别名或第二套读法。

共改名 38 个源码、测试或设计文件，逐项如下。

| 原文件 | 当前文件 |
| --- | --- |
| `apps/console/design/work-objective-timeline.html` | `apps/console/design/work-macro-task-timeline.html` |
| `apps/console/src/ObjectiveChronology.tsx` | `apps/console/src/MacroTaskChronology.tsx` |
| `apps/console/src/ObjectiveList.tsx` | `apps/console/src/MacroTaskList.tsx` |
| `apps/console/src/ObjectiveOverview.tsx` | `apps/console/src/MacroTaskOverview.tsx` |
| `apps/console/src/ObjectiveTimeline.tsx` | `apps/console/src/MacroTaskTimeline.tsx` |
| `apps/console/src/Objectives.tsx` | `apps/console/src/MacroTasks.tsx` |
| `apps/console/src/objective-api.test.ts` | `apps/console/src/macro-task-api.test.ts` |
| `apps/console/src/objective-detail.test.tsx` | `apps/console/src/macro-task-detail.test.tsx` |
| `apps/console/src/objective-display-016.test.ts` | `apps/console/src/macro-task-display-016.test.ts` |
| `apps/console/src/objective-display.ts` | `apps/console/src/macro-task-display.ts` |
| `apps/console/src/objective-fixtures.ts` | `apps/console/src/macro-task-fixtures.ts` |
| `apps/console/src/objective-list.test.tsx` | `apps/console/src/macro-task-list.test.tsx` |
| `apps/console/src/objective-metrics.test.ts` | `apps/console/src/macro-task-metrics.test.ts` |
| `apps/console/src/objective-metrics.ts` | `apps/console/src/macro-task-metrics.ts` |
| `apps/console/src/objective-stop-reply.ts` | `apps/console/src/macro-task-stop-reply.ts` |
| `apps/console/src/objective-stop.test.ts` | `apps/console/src/macro-task-stop.test.ts` |
| `apps/console/src/objective-stop.ts` | `apps/console/src/macro-task-stop.ts` |
| `apps/console/src/objective-tab.test.tsx` | `apps/console/src/macro-task-tab.test.tsx` |
| `apps/console/src/objective-timeline-016.test.tsx` | `apps/console/src/macro-task-timeline-016.test.tsx` |
| `apps/console/src/objective-timeline-layout.test.ts` | `apps/console/src/macro-task-timeline-layout.test.ts` |
| `apps/console/src/objective-timeline-layout.ts` | `apps/console/src/macro-task-timeline-layout.ts` |
| `apps/console/src/objective-timeline-scale.test.ts` | `apps/console/src/macro-task-timeline-scale.test.ts` |
| `apps/console/src/objective-timeline-scale.ts` | `apps/console/src/macro-task-timeline-scale.ts` |
| `apps/console/src/objective-timeline.test.tsx` | `apps/console/src/macro-task-timeline.test.tsx` |
| `apps/console/src/objective-types.ts` | `apps/console/src/macro-task-types.ts` |
| `apps/console/src/use-objective-list.test.tsx` | `apps/console/src/use-macro-task-list.test.tsx` |
| `apps/console/src/use-objective-list.ts` | `apps/console/src/use-macro-task-list.ts` |
| `apps/console/src/use-objective-timeline.test.tsx` | `apps/console/src/use-macro-task-timeline.test.tsx` |
| `apps/console/src/use-objective-timeline.ts` | `apps/console/src/use-macro-task-timeline.ts` |
| `src/hey_my_buddy/blackboard/tasks/objective_summary_cache.py` | `src/hey_my_buddy/blackboard/tasks/macro_task_summary_cache.py` |
| `src/hey_my_buddy/blackboard/tasks/objectives.py` | `src/hey_my_buddy/blackboard/tasks/macro_tasks.py` |
| `tests/probes/objective_console_preview.py` | `tests/probes/macro_task_console_preview.py` |
| `tests/python/blackboard/tasks/test_console_objective_fixture.py` | `tests/python/blackboard/tasks/test_console_macro_task_fixture.py` |
| `tests/python/blackboard/tasks/test_objective_stop.py` | `tests/python/blackboard/tasks/test_macro_task_stop.py` |
| `tests/python/blackboard/tasks/test_objective_summary_cache.py` | `tests/python/blackboard/tasks/test_macro_task_summary_cache.py` |
| `tests/python/blackboard/tasks/test_objectives.py` | `tests/python/blackboard/tasks/test_macro_tasks.py` |
| `tests/python/console/test_console_objective_cache.py` | `tests/python/console/test_console_macro_task_cache.py` |
| `tests/python/fixtures/console_objective_board.py` | `tests/python/fixtures/console_macro_task_board.py` |

构建替换了 `src/hey_my_buddy/console/assets/assets/index-BI7ePOsQ.js` 和 `index-Qm-l-qr6.css`，当前产物是 `index-DLvjMU6R.js` 和 `index-BWfWCvCN.css`；`src/hey_my_buddy/console/assets/index.html` 指向这两个新文件。其他构建资源的内容没有改动。

在 `src`、`tests/python`、`tests/probes`、`apps/console`、`packaging` 按 `objective`（不分大小写）与 `obj-` 自查；按每次字面命中计数，保留 23 处，分布在 15 行，`obj-` 为 0 处。源码与打包资产里没有未说明的旧名，当前打包资产没有这两个词。以下每行对应一处命中，列号是该旧词的起点。

| 文件位置 | 留下的名字或文字 | 原因 |
| --- | --- | --- |
| `packaging/generate-upgrade-fixture.py:209:22` | `objective` | 固定的 0.34.1 样板生成器，按任务要求保留旧参数与旧表名。 |
| `packaging/generate-upgrade-fixture.py:209:82` | `objectiveOf` | 固定的 0.34.1 样板生成器，按任务要求保留旧参数与旧表名。 |
| `packaging/generate-upgrade-fixture.py:409:57` | `objectives` | 固定的 0.34.1 样板生成器，按任务要求保留旧参数与旧表名。 |
| `tests/python/cli/test_skill_workflow.py:54:14` | `objectiveId` | 核对当前被冻结的 skills/buddy/SKILL.md 原文；Host 在文档微任务中同步。 |
| `tests/python/cli/test_skill_workflow.py:55:14` | `objectiveOf: "<runId>"` | 核对当前被冻结的 skills/buddy/SKILL.md 原文；Host 在文档微任务中同步。 |
| `apps/console/design/work-macro-task-timeline.html:8:45` | `docs/design/adr012/work-objective-timeline.md` | 现有文档文件名，由 Host 后续同步；保留当前有效的引用。 |
| `apps/console/design/work-macro-task-timeline.html:349:27` | `objectives` | 界面展示的原始任务示例或其文字断言；开发者要求显示文字一字不改。 |
| `apps/console/design/work-macro-task-timeline.html:387:36` | `objectives` | 界面展示的原始任务示例或其文字断言；开发者要求显示文字一字不改。 |
| `apps/console/design/work-macro-task-timeline.html:387:49` | `objective_list` | 界面展示的原始任务示例或其文字断言；开发者要求显示文字一字不改。 |
| `apps/console/design/work-macro-task-timeline.html:393:40` | `ObjectiveTimeline` | 界面展示的原始任务示例或其文字断言；开发者要求显示文字一字不改。 |
| `apps/console/src/MacroTasks.tsx:24:36` | `docs/design/objective-browser/0.15.1.md` | 现有文档文件名，由 Host 后续同步；保留当前有效的引用。 |
| `apps/console/src/macro-task-fixtures.ts:3:57` | `docs/reference/objectives.md` | 现有文档文件名，由 Host 后续同步；保留当前有效的引用。 |
| `apps/console/src/macro-task-fixtures.ts:92:28` | `objectives` | 界面展示的原始任务示例或其文字断言；开发者要求显示文字一字不改。 |
| `apps/console/src/macro-task-fixtures.ts:92:41` | `objective_list` | 界面展示的原始任务示例或其文字断言；开发者要求显示文字一字不改。 |
| `apps/console/src/macro-task-fixtures.ts:92:84` | `objectives` | 界面展示的原始任务示例或其文字断言；开发者要求显示文字一字不改。 |
| `apps/console/src/macro-task-fixtures.ts:92:98` | `objective_list` | 界面展示的原始任务示例或其文字断言；开发者要求显示文字一字不改。 |
| `apps/console/src/macro-task-fixtures.ts:92:113` | `objective_timeline` | 界面展示的原始任务示例或其文字断言；开发者要求显示文字一字不改。 |
| `apps/console/src/macro-task-fixtures.ts:98:32` | `ObjectiveTimeline` | 界面展示的原始任务示例或其文字断言；开发者要求显示文字一字不改。 |
| `apps/console/src/macro-task-fixtures.ts:98:81` | `ObjectiveTimeline` | 界面展示的原始任务示例或其文字断言；开发者要求显示文字一字不改。 |
| `apps/console/src/macro-task-api.test.ts:119:66` | `no such objective` | 模拟服务错误的原消息文字，不是接口字段或操作名。 |
| `apps/console/src/task-activity.test.tsx:134:71` | `Stop objective` | 模拟外部取消原因的原消息文字，不是接口字段或操作名。 |
| `apps/console/src/macro-task-timeline.test.tsx:618:61` | `objectives` | 界面展示的原始任务示例或其文字断言；开发者要求显示文字一字不改。 |
| `apps/console/src/macro-task-timeline.test.tsx:618:74` | `objective_list` | 界面展示的原始任务示例或其文字断言；开发者要求显示文字一字不改。 |

`tests/fixtures/` 的升级基点样板、生成样板的脚本、`docs/` 的其他文件、`skills/`、两份 README、`AGENTS.md` 与 `CONTEXT.md` 均未修改；其他命名族、版本号与 schema 版本号未修改。没有保留不确定归属的运行时旧名。

Node 使用仓库声明支持的 24.21.0。最初默认 Node 25.8.1 下的 `npm --prefix apps/console ci` 退出 0，但有 engines 警告；切到 Node 24.21.0 后重新 `npm --prefix apps/console ci`，退出 0。npm 缓存和 Playwright 下载分别在检出内 `apps/console/node_modules/.cache/npm`、`apps/console/node_modules/.cache/ms-playwright`；没有改变依赖清单或锁文件。

前端第一次 `npm --prefix apps/console test` 为 77 个文件通过、`preview-contract.test.ts` 失败：并行改名中 Python 合成预览脚本尚未出现在新路径。类型检查通过；修正种类与标题来源的 10 处取值为 `macro-task` 后，4 个重点文件的 62 项测试通过。探针同步后，最终 `npm --prefix apps/console test` 退出 0，78 个文件、1,088 项测试全部通过，耗时 25.48 秒；日志在 `apps/console/coverage/rename/npm-test-final.log`。

以检出内浏览器下载目录执行 `npm --prefix apps/console exec -- playwright install --only-shell chromium`，退出 0。最终 `npm --prefix apps/console run test:browser` 退出 0，96 项浏览器检查全部通过，耗时 23.9 秒；其中的仓库构建命令 `npm run build` 完成 TypeScript 检查与 Vite 资产重建。构建有大于 500 kB 的现有包大小提示，改名前源码构建也有相同提示，本次没有拆包。日志在 `tmp/macro-task-rename/browser.log`。

补充像素核对将提交 `652deaec` 的前端源码与合成预览复制到检出内临时目录，用同一依赖重建改名前资产，再与最终资产分别加载对应的合成 API。初稿使用了条件渲染的“打开详情”按钮和委派条作为等待条件，三次试跑超时；改为始终存在的时间线视图，并固定浏览器 Date 与 HTTP Date 之后，1280 px 和 390 px、亮暗主题、列表与选中宏任务详情共 8 组 PNG 逐字节相同，浏览器没有 pageerror。已查看列表和详情截图；截图在 `output/playwright/macro-task-rename/`，完整哈希在 `tmp/macro-task-rename/pixel-report.json`。该核对基于改名前源码重建的资产，输入提交原有的打包资产更早，不用于证明此次改名的视觉差异。

Python 检查均使用开发者指定的解释器和 `PYTHONPATH=src <Host 提供的 Python> -m hey_my_buddy.cli.checks`，不更改其环境，不传 `--jobs`，清除继承的 runtime、Worker、agent 凭据和虚拟环境变量并使用默认进程数。检查器自己的私有根位于系统临时目录，由它创建与清理。第一次定向检查 5 个文件，升级测试的 3 项通过，宏任务、停止、摘要缓存和控制台缓存 4 个文件因机械改名将任务文字 SQL 列别名误写为带空格的文字而失败；在定义处修正为 `AS task` 后，这 4 个文件重跑退出 0，67 项通过，私有根已清理。两次日志在 `tmp/macro-task-rename/python-focused.log` 与 `python-focused-rerun.log`。

最终完整 Python 检查不带路径，退出 0：234 个文件、3,862 项测试全部通过，未跳过，检查器确认私有根已清理。没有全量检查失败文件或因机器忙需单独重跑的文件；前述定向重跑用于验证本次改名造成的列别名错误，不能混记为全量检查的时序失败。完整日志在 `tmp/macro-task-rename/python-full.log`。

只读自查比对了宏任务与任务文字清单、后端与测试差异、新文件和原文件、协议/服务/CLI/控制台 API 的对应名称，以及升级新板与旧基点的边界，没有发现可复现的命名错误或行为差异。该自查没有运行测试，独立于上述实际检查；未验证安装后的运行时或真实 harness 调用，Host 的最终验收仍待执行。

`git diff --check` 通过。临时脚本、原始失败与成功日志、基线副本、依赖和截图仅保留在检出内的忽略目录，未写入公开记录；没有提交，没有操作分支、标签或 stash。
