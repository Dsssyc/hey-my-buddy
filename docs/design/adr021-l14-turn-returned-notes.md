# 回合状态改称 returned 的说明

开发者于 2026-10-14 决定将回合的状态取值 `concluded` 改为 `returned`；微任务与任务的 `concluded` 继续表示 Host 已写入结论。本次直接修改新看板形式，版本号与 schema 版本不变，不增加旧值兼容、历史换算或迁移；升级基点的样板与生成脚本均未修改。

## 修改位置

`src/hey_my_buddy/blackboard/store/db.py:459` 将 `workflow_turns.state` 的约束改为 `prepared`、`running`、`returned`、`failed`。

`src/hey_my_buddy/blackboard/tasks/workflow.py:3549` 与 `:6487` 分别修改取消后的迟到结果和正常有效结果的写入；`:3981`、`:3984` 修改 Host 直接完成与普通验收的回合状态读取；`:5492` 修改任务视图的最新回合摘要查询；`:5754` 修改原生续接资格判断；`:5798` 修改 Codex 后续输入中 `validatedOutcome` 的判断；`:789`、`:3969`、`:3991` 同步等待原因、注释与错误文字。

`src/hey_my_buddy/blackboard/tasks/macro_tasks.py:242` 修改宏任务与独立微任务摘要的回合查询；`src/hey_my_buddy/blackboard/tasks/macro_task_summary_cache.py:194` 同步缓存依赖查询；`src/hey_my_buddy/blackboard/store/store.py:38` 同步回合返回结果的注释。

`tests/probes/macro_task_console_preview.py:928` 同步等待原因，`:943` 将合成预览中已结束回合的状态改为 `returned`。Worker 与协议中没有独立的回合 `concluded` 状态取值需要修改；协议的任务终态常量属于 `tasks.state`。

`tests/python/blackboard/tasks/test_dsh_resume_selection.py:90`、`:205`，`tests/python/blackboard/tasks/test_macro_task_summary_cache.py:413`、`:429`，以及 `tests/python/blackboard/tasks/test_title_fallback.py:4`、`:34`、`:59`、`:93`、`:104` 同步回合状态断言、SQL、测试名称与注释。

`tests/python/blackboard/tasks/test_workflow.py:216` 补充当前回合、紧凑回合列表与审计回合的 `returned` 断言，`:1539` 补充取消后迟到有效结果的回合状态断言；`tests/python/cli/test_cli_views.py:134` 验证 CLI `get` 的紧凑视图透传 `returned`；`tests/python/buddy/harnesses/codex/test_codex_continuation.py:65` 至 `:70` 扩展现有场景，验证有效返回之后续接输入的 `validatedOutcome` 为真。没有新增独立测试用例；修改的各处回合状态读取均由现有场景经过。

## 保留的 concluded

计数按 `src/hey_my_buddy/` 中 Python 源码内不区分大小写的字面出现次数计算，包含标识符、注释和字符串；不计测试、文档与生成资产。保留 58 处，全部属于微任务、任务或 Host 结论；Python 源码没有其他含义的剩余项，没有归属存疑项。

| 文件 | 微任务相关出现次数 | 行号与对象 |
| --- | ---: | --- |
| `src/hey_my_buddy/blackboard/routing/router_boundary_data.py` | 1 | 278；祖先微任务终态 |
| `src/hey_my_buddy/blackboard/service/live_registry.py` | 1 | 60；任务终态 |
| `src/hey_my_buddy/blackboard/store/db.py` | 4 | 38, 43, 78, 518；任务终态常量及 tasks、workflow_runs 约束 |
| `src/hey_my_buddy/blackboard/store/store.py` | 5 | 46, 47, 48, 49（2 处）；tasks 状态转换 |
| `src/hey_my_buddy/blackboard/tasks/host_conclusions.py` | 1 | 8；微任务的 Host 结论 |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py` | 1 | 168；任务终态 |
| `src/hey_my_buddy/blackboard/tasks/macro_tasks.py` | 1 | 69；Host 结论事件 |
| `src/hey_my_buddy/blackboard/tasks/workflow.py` | 35 | 111, 792, 793, 2274, 2284, 2318, 2341, 2770, 3236, 3277, 3282, 3283, 3290（2 处）, 3337, 3496, 3508, 3531, 3880, 4279, 4281, 4286, 4361, 4366, 4368, 4374, 4530, 5064, 5573, 5574, 6419, 6422, 6948, 6994, 7092；workflow_runs、tasks、祖先与根微任务、Host 结论及其返回字段 |
| `src/hey_my_buddy/cli/blocking.py` | 2 | 46, 499；任务与微任务终态 |
| `src/hey_my_buddy/cli/cli_help.py` | 1 | 64；conclude 帮助中的微任务终态 |
| `src/hey_my_buddy/cli/main.py` | 2 | 152, 156；微任务结论与终态帮助 |
| `src/hey_my_buddy/install/upgrade.py` | 3 | 323, 357, 372；归档基点微任务的 Host 结论判断与升级空闲条件 |
| `src/hey_my_buddy/protocol/schemas.py` | 1 | 98；任务终态 |

续接回合按 Host 补充的范围，将 `src/hey_my_buddy/blackboard/tasks/workflow.py:6395` 的方法改为 `settle_turn`；这个名字涵盖有效交回、执行失败、拒收、取消后的迟到结果与未启动释放等结束情形，不把全部结束情形称作 returned。调用点 `src/hey_my_buddy/blackboard/store/store.py:2531` 与 `src/hey_my_buddy/blackboard/tasks/workflow.py:3575` 同步修改，未保留旧名或别名。

续接回合将 `src/hey_my_buddy/blackboard/tasks/workflow.py:6520` 与 `:6531` 的事件改为 `workflow.turn_returned`。先核对控制流：两处都在同一事务中 `:6487` 将回合写为 `returned` 之后发生，分别对应 completed 与 assistance/attention；失败、拒收与迟到结果分支在此前返回，不写该事件。本次只改事件名字，不增加新事件、不改变触发条件，不转换旧看板事件。`tests/python/blackboard/tasks/test_console_macro_task_fixture.py:49` 同步事件名，`:51` 至 `:55` 扩展现有样例测试，逐条关联事件与回合，验证该事件对应的回合均为 `returned`。样例由真实写入路径生成，没有另一个写死旧名的当前样例数据需要修改；升级基点 SQL 中两条旧事件保留原样。

控制台没有读取回合 `state` 的逻辑：`apps/console/src/workflow-types.ts:48` 的 `TurnRouting` 不包含状态，`WorkflowPanel.tsx` 使用回合配置、摘要、会话和用量；`ui.tsx:204` 与 `macro-task-display.ts:201` 的 `concluded` 标签读取微任务状态，`macro-task-stop-reply.ts` 与 `macro-task-types.ts` 的 `concludedRunIds` 表示保留 Host 结论的根微任务，因此均不改。本次没有修改前端或重建资产，也没有执行前端全量或浏览器检查。

除上述 Python 清点外，仍有 3 处前端文字使用 concluded 描述旧回合结果摘要，逐条列出如下；它们不读取回合状态，按“前端没有这样的地方就不动前端”的限定保留，归属明确，不属于存疑项。

- `apps/console/src/types.ts:224`：`workflow.resultSummary` 的注释把最新回合结果称为 concluded。
- `apps/console/src/desktop-console.test.tsx:327`：已有测试名中的 newest concluded result。
- `apps/console/src/desktop-console.test.tsx:346`：关于 0.16 T1 摘要刷新的历史注释。

## 顺带修改的帮助文字

共 3 句：`src/hey_my_buddy/cli/cli_help.py:43` 的 `capabilities` 改为 “Harness report with availability, capabilities, named operations and the honest limitations map.”；`:44` 的 `harnesses` 改为 “Harness report; the same operation as capabilities.”；`:70` 的 `macro-task-list` 改为 “Bounded summaries of macro tasks and standalone micro tasks by latest activity.”。其他帮助中的 adapter 指执行连接件，保留；现有帮助测试没有固定上述旧句子的断言。

## 验证记录

所有 Python 检查均通过 Host 指定的解释器运行 `PYTHONPATH=src <Host 指定解释器> -m hey_my_buddy.cli.checks <文件或目录>`；未传 `--jobs`、未设置并发覆盖环境变量，运行器采用默认的 4 个进程并自行在系统临时目录创建与清理私有根。没有执行完整检查，没有安装产品，没有运行真实 harness 或付费模型调用。

首轮参数为 `tests/python/blackboard/tasks tests/python/blackboard/store tests/python/cli/test_host_cli.py tests/python/cli/test_cli.py tests/python/cli/test_cli_views.py tests/python/cli/test_host_preview.py tests/python/buddy/harnesses/codex/test_codex_continuation.py tests/python/install/test_upgrade_migration.py`。共 51 个测试文件，50 个文件的 858 项测试全部通过、无跳过；另一个文件 `test_host_preview.py` 的 3 项测试中 2 项通过、1 项失败。运行器退出 1，确认私有根已删除；原始输出保存在忽略的 `tmp/turn-returned-checks.log`。

| 首轮测试文件 | 结果 |
| --- | --- |
| `tests/python/blackboard/store/test_blackboard.py` | 通过 |
| `tests/python/blackboard/tasks/test_parallel_dispatch.py` | 通过 |
| `tests/python/blackboard/tasks/test_workflow_preparation.py` | 通过 |
| `tests/python/blackboard/tasks/test_workflow_real.py` | 通过 |
| `tests/python/blackboard/tasks/test_workspace.py` | 通过 |
| `tests/python/blackboard/tasks/test_scope_recovery.py` | 通过 |
| `tests/python/blackboard/store/test_current_core.py` | 通过 |
| `tests/python/blackboard/store/test_store.py` | 通过 |
| `tests/python/blackboard/tasks/test_catalog_trust.py` | 通过 |
| `tests/python/blackboard/tasks/test_console_macro_task_fixture.py` | 通过 |
| `tests/python/blackboard/tasks/test_continue_catalog_trust.py` | 通过 |
| `tests/python/blackboard/tasks/test_dsh_resume_selection.py` | 通过 |
| `tests/python/blackboard/store/test_upgrade_fixture.py` | 通过 |
| `tests/python/blackboard/tasks/test_empty_claims.py` | 通过 |
| `tests/python/blackboard/tasks/test_host_workflow_worker.py` | 通过 |
| `tests/python/blackboard/tasks/test_host_workflow.py` | 通过 |
| `tests/python/blackboard/tasks/test_macro_task_stop.py` | 通过 |
| `tests/python/blackboard/tasks/test_macro_task_summary_cache.py` | 通过 |
| `tests/python/blackboard/tasks/test_micro_task_history.py` | 通过 |
| `tests/python/blackboard/tasks/test_macro_tasks.py` | 通过 |
| `tests/python/blackboard/tasks/test_model_service_concurrency.py` | 通过 |
| `tests/python/blackboard/tasks/test_permission_resume.py` | 通过 |
| `tests/python/blackboard/tasks/test_scheduler_lanes.py` | 通过 |
| `tests/python/blackboard/tasks/test_scheduling.py` | 通过 |
| `tests/python/blackboard/tasks/test_storage.py` | 通过 |
| `tests/python/blackboard/tasks/test_inquiry.py` | 通过 |
| `tests/python/blackboard/tasks/test_permission_levels.py` | 通过 |
| `tests/python/blackboard/tasks/test_windows_workspace.py` | 通过 |
| `tests/python/blackboard/tasks/test_title_fallback.py` | 通过 |
| `tests/python/blackboard/tasks/test_worker_claim_stash.py` | 通过 |
| `tests/python/blackboard/tasks/test_workflow_boundaries.py` | 通过 |
| `tests/python/blackboard/tasks/test_workflow.py` | 通过 |
| `tests/python/blackboard/tasks/test_workflow_cancellation.py` | 通过 |
| `tests/python/blackboard/tasks/test_workflow_routing.py` | 通过 |
| `tests/python/blackboard/tasks/test_workflow_stop_surface.py` | 通过 |
| `tests/python/blackboard/tasks/test_workflow_stash.py` | 通过 |
| `tests/python/blackboard/tasks/test_workspace_lifecycle.py` | 通过 |
| `tests/python/blackboard/tasks/test_workspace_api.py` | 通过 |
| `tests/python/blackboard/tasks/test_workspace_git_errors.py` | 通过 |
| `tests/python/blackboard/tasks/test_workspace_identity.py` | 通过 |
| `tests/python/blackboard/tasks/test_workflow_worker.py` | 通过 |
| `tests/python/blackboard/tasks/test_workspace_batch.py` | 通过 |
| `tests/python/buddy/harnesses/codex/test_codex_continuation.py` | 通过 |
| `tests/python/cli/test_cli.py` | 通过 |
| `tests/python/blackboard/tasks/test_workspace_stash.py` | 通过 |
| `tests/python/cli/test_cli_views.py` | 通过 |
| `tests/python/cli/test_host_preview.py` | 失败：缺少控制台开发依赖 |
| `tests/python/cli/test_host_cli.py` | 通过 |
| `tests/python/install/test_upgrade_migration.py` | 通过 |
| `tests/python/blackboard/tasks/test_workspace_submission_cleanup.py` | 通过 |
| `tests/python/blackboard/tasks/test_storage_orphans.py` | 通过 |

唯一失败为 `cli.test_host_preview.PreviewFixtureTest.test_frontend_parsers_consume_the_emitted_fixtures`：检出中缺少 `apps/console/node_modules/vitest/vitest.mjs`，在调用前端解析器之前的依赖断言失败；`test_preview_self_check_passes` 与 `test_preview_tree_is_deterministic` 均通过。这是缺失测试依赖，不是观察到回合状态错误。

按 Host 要求单独重跑 `tests/python/cli/test_host_preview.py` 一次，仍为 3 项中 2 项通过、同一个缺依赖断言失败，退出 1，私有根已删除；原始输出保存在忽略的 `tmp/turn-returned-preview-retry.log`。首回合没有重跑已通过的测试。

首回合未改前端，因此没有安装前端依赖。Host 在续接指令中报告，已在合并后的树上运行 `tests/python/cli/test_host_preview.py` 且通过，并明确本回合不用再跑或安装前端依赖；这是 Host 提供的合并后验证结果，本 Worker 未在当前检出复跑。原有两次依赖缺失的记录保留，不再把它列为待办。

## 续接回合的验证

续接回合先按与首回合相同的指定命令检查 50 个受影响测试文件（首回合参数去掉 `tests/python/cli/test_host_preview.py`）。测试启动后复核差异发现，一处方法调用被事件名的字符串替换误改成 `self.workflow.turn_returned`，已立即修正为 `self.workflow.settle_turn`；更正前载入源码的测试进程仍持有错误调用，因此以下首次失败属于本次修改错误，不能归为时序问题。

主批次中 46 个文件通过，3 个文件返回失败，剩余的 `tests/python/blackboard/tasks/test_parallel_dispatch.py` 未完成：该文件退休回执场景直接同步调用 `worker.run()`，更正前载入的错误调用导致回执无法提交，持续重试。确认仅剩该测试子进程后，对所属私有检查运行器发送 SIGINT，运行器按自身中断流程停止它的测试子进程并执行清理；本批次退出 130，随后只读核对确认其私有根已删除，没有操作日常服务或 Worker。原始输出保存在忽略的 `tmp/turn-returned-event-checks.log`。

除下面列出的 4 个文件外，本回合 46 个通过文件的范围为首回合表去掉 `test_host_preview.py` 后的其余文件；没有重新运行已通过文件。以下各次单文件重跑均使用最终源码、原指定检查命令与默认并发设置，未修改版本、schema 或升级基点。

| 测试文件 | 首次结果 | 最终源码的单文件重跑 |
| --- | --- | --- |
| `tests/python/blackboard/tasks/test_workflow_preparation.py` | 31 项，29 个错误，文件失败 | 31 项全部通过，退出 0，私有根删除 |
| `tests/python/blackboard/tasks/test_workspace_lifecycle.py` | 53 项，54 个错误（含清理错误），文件失败 | 53 项全部通过，退出 0，私有根删除 |
| `tests/python/blackboard/store/test_blackboard.py` | 58 项，1 个失败与 10 个错误，文件失败 | 58 项全部通过，退出 0，私有根删除 |
| `tests/python/blackboard/tasks/test_parallel_dispatch.py` | 未完成，随上述私有检查运行器中断 | 27 项全部通过，退出 0，私有根删除 |

单文件重跑日志分别保存在忽略的 `tmp/turn-returned-event-retry-test_workflow_preparation.log`、`tmp/turn-returned-event-retry-test_workspace_lifecycle.log`、`tmp/turn-returned-event-retry-test_blackboard.log` 与 `tmp/turn-returned-event-retry-test_parallel_dispatch.log`。

本回合选择的 50 个受影响文件最终全部有通过结果：46 个文件在更正后启动的主批次中通过，另外 4 个文件单独重跑各一次全部通过，4 次单文件检查均退出 0、无跳过且私有根均已删除。该结论不把中断的主批次描述为整批退出 0，也不把首回合的验证代替本回合验证；预览回归仅引用前述 Host 的合并后树验证，本 Worker 没有复跑。

`git diff --check` 通过。以上为 Worker 的修改与检查记录，最终验收由 Host 负责。
