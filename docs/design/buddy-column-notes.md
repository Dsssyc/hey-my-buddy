# buddy 栏的存储、投影与升级

本批以 `f53496fc` 为输入，清理命令与外部执行占用 buddy 栏的形式；版本由 C 组统一调整，参考文档由 Host 更新，本说明记录源代码候选与检查证据，等待 Host 验收。

`workflow_runs.buddy_json` 选定模型执行 buddy 后只存 `harness`、`provider`、`model`、`effort` 四项，选定之前为 SQL `NULL`；命令与外部微任务始终为 `NULL`，执行类型读取 `tasks.adapter`。`buddy_constraints` 对这两类执行返回空约束，提交、辅助微任务与派发直接按任务适配器跳过模型路由和 buddy 校验；续做保留相同适配器，拒绝补选 buddy 或 reroute，原有 `full` 权限拒绝继续生效。

`workflow_get` 和 CLI `get`、`status`、`list` 在命令与外部执行中投影 `buddy: null`，保留 `adapter`；任务历史查询也直接读取任务适配器。宏任务时间线的行和执行区间分别携带任务及尝试的适配器，执行区间不再制造适配器形状的 buddy。控制台列表、详情、微任务面板、宏任务时间线与时间顺序视图根据 `adapter` 显示命令执行或外部执行，新增文字经过中英文 i18n。

在原来记录选择事件的时刻，命令与外部微任务记录 `workflow.execution_selected`，载荷固定为 `{"source":"original-explicit","adapter":"command"}` 或相同字段的 `external`。模型执行继续记录四项 buddy 的 `workflow.buddy_selected`，两种事件不共用载荷形式。

待做的 schema 21→22 升级严格识别旧适配器约束，核对运行所属任务的适配器后把 `buddy_json` 清为空；旧选择事件原地改种类和载荷，保留事件序号。无法识别的 JSON、额外字段、未知适配器、不一致绑定及伪造的四项命令 buddy 都使整次升级回滚。当前四项 buddy 还要与当前任务适配器一致；历史模型选择事件只校验其自身四项编码 harness 身份，允许之后发生合法的 Host harness 更换。

安装升级的 `idle_snapshot` 在换算前按完整行投影 `commands`、`workflow_runs`、`events` 应有的指纹，`verify_migrated_snapshot` 比对这些预期和其余保留表；不会把整张表排除出内容核对。升级样板测试保留 schema 结构与新建看板的比对，并构造带命令和外部旧形式的看板，覆盖换算、序号保留、不一致回滚、历史模型选择保留及运行和事件的篡改检测。

真实私有服务检查发现一个输入提交已有的执行契约缺陷：普通命令进程退出码为 0，`CommandAdapter.collect` 只提供命令结果，没有结构化 turn；服务的微任务结果导入要求该 turn，因此任务和微任务均失败，错误为 `the micro-task result could not be imported: the runner produced no structured turn outcome`。本批保留真实服务回归测试要求 `delivered` 的失败见证，已在它之前核对 buddy 为空、适配器字段及独立选择事件；未凭退出码伪造模型会话或结构化 turn，命令完成契约仍需 Host 决定。

检查使用 Host 指定的 Python 解释器和 `PYTHONPATH=src`，只经 `hey_my_buddy.cli.checks` 运行指定文件与目录，默认并发，清除继承的运行时、Worker、凭据及虚拟环境变量；服务、Worker 和状态均由检查运行器建立在私有根目录。没有运行完整 Python 检查、安装产品或操作日常服务。

本轮已运行 `tests/python/install/`、`tests/python/blackboard/store/`、`tests/python/blackboard/routing/`、`tests/python/cli/`，以及提交、路由、准备、派发、调度、并发、权限、续做、宏任务与微任务历史相关文件；另运行目录与评估、服务、询问、Lead、存储及工作区中读取 buddy 的相关测试。首次 92 文件范围运行中 89 文件通过，暴露的 CLI 空 buddy 夹具和预览依赖时序问题已在最终复测消除；普通命令执行与冷启动检查在源码和打包资源固定后也已复测通过。

最后一轮 `tests/python/blackboard/store/test_blackboard.py`、`tests/python/blackboard/store/test_upgrade_fixture.py` 与整个 `tests/python/cli/` 共 15 文件、255 项测试通过，退出码为 0；升级样板含新增的历史 harness 更换测试，共 14 项。最终的 `test_workflow.py`、`test_micro_task_history.py` 和目录权限检查均通过；模型路由、派发及原有使用离线夹具的模型执行回合测试通过，没有进行付费模型调用。`test_workflow_worker.py` 共 8 项中 7 项通过，新增的 `test_command_execution_has_no_buddy_or_model_selection` 仍失败，失败位置仅为命令微任务未到达 `delivered`；它之前的真实服务 `get`、`status`、`list`、SQL 空 buddy 和独立选择事件检查均已通过。所有检查运行器的私有根均已确认清理，未跳过测试。

控制台在 Node 24.15.0 下执行 `npm --prefix apps/console ci`、`npm --prefix apps/console run typecheck`、`npm --prefix apps/console test` 和 `npm --prefix apps/console run test:browser`：类型检查通过，前端 82 文件、1127 项测试通过，浏览器 117 项通过，其中新增命令与外部执行的中英文显示覆盖；预览自检和 smoke 通过。浏览器检查重建了 `src/hey_my_buddy/console/assets/`，替换旧脚本资源及入口引用；最终 `git diff --check` 通过。
