# buddy 栏的存储、投影与升级

开发者于 2026-10-15 决定，微任务仅接受 buddy 执行：Host 给完整的 `harness`、`provider`、`model`、`effort` 四项，或省略全部四项由 Router 选择；命令与外部执行使用 `execution-submit` 建立普通执行记录。此前命令与外部微任务无法提供结构化回合，本批按该决定关闭入口，没有为命令伪造回合。本说明记录源代码候选与验证，等待 Host 验收；版本由 C 组统一调整，参考文档与术语表由 Host 更新。

微任务与协助任务使用明确的规格字段集合，不再沿用普通执行记录的整套 `SUBMIT_FIELDS`。`adapter`、`argv` 和其他退役写法只在原始提交或协助任务请求边界拒绝，包括显式给出空值，错误指向 `execution-submit`；内部规格投影和规范化不重复拒绝已检查的字段。服务先完成规范化，再触发原生探测或刷新，把同一规范化结果传入提交或审批；授权、幂等回执和修订号栅栏继续在执行路径检查。服务测试断言非法请求没有原生读取或刷新、没有准备工作区、没有写入任务，并验证成功的提交和协助规格只规范化一次。

`workflow_runs.buddy_json` 选定后只存四项 buddy，选定之前为 SQL `NULL`。`buddy_constraints` 只返回 buddy 字段，协助任务的适配器由已选 harness 决定，不再从规格的 `adapter` 后备读取；命令与外部微任务专用的路由、校验、派发、续做及权限分支已移除。新微任务只记录模型执行的 `workflow.buddy_selected`，运行时代码不再产生 `workflow.execution_selected`；普通命令与外部执行记录仍走原有路径，不建立微任务运行。

微任务完整视图及简略输出显示 buddy，不额外显示顶层 `adapter`。`microTask` 摘要总是相同的 `harness` 字段形状；旧命令与外部微任务的 buddy 和摘要 harness 为空，原始规格在审计视图中保留。普通执行记录的 `status`、`list` 继续使用已有 `adapter` 字段。CLI 帮助按外层请求字段白名单约束内部规格投影，不再把普通执行记录的 `adapter`、`argv` 列为微任务或协助任务的参数。

回合视图按该回合绑定的尝试类型读取冻结 buddy：模型回合必须具有完整合法的四项且 harness 与尝试一致；已知旧命令或外部回合必须具有相符的旧适配器标记，派生 buddy 投影为空；缺项、未知类型或不一致形状失败。不会把任意不完整对象解释为空，也不跨模块引用宽泛的私有投影函数。原始 `input_json` 与哈希不改写；测试覆盖完整视图、当前回合、回合列表、审计回合、错误形状及原始证据保留。

待做的 schema 21→22 升级严格识别旧适配器约束，核对当前运行所属任务的 `adapter` 后把 `buddy_json` 清为空。历史选择事件与不可变的 `micro_task_json` 核对：旧命令或外部标记必须符合原始执行类型，模型显式选择必须与原始四项完全一致；之后改选 buddy 不会改变历史事件的依据。旧标记事件原地改为 `workflow.execution_selected`，载荷为 `{"source":"original-explicit","adapter":"command"}` 或相同字段的 `external`，保留序号。测试覆盖原始命令后来改选为 Codex CLI buddy、未指定 buddy 却出现显式选择事件及伪造历史四项；无法识别或不一致的数据使整次升级回滚。

原始命令或外部微任务中保存旧完整视图的操作回执在升级中清除。清理按不可变的运行身份、`commands.task_id` 和明确的视图回执种类绑定，包括工作流操作与会携带任务视图的 Worker、普通任务操作；保留消息和其他运行的回执。预期快照同时声明删除后的命令数与完整行指纹，存量回执不会在升级后重放旧 buddy 形式。

迁移发生在旧服务与 Worker 停止、持有看板锁之后，迁移前后使用 `database_snapshot(exact=True)`。预先投影 `commands` 的换算和删除、`workflow_runs`、`events` 以及 `meta` 仅 schema 版本变化后的完整行指纹，同时核对 `workers` 与包括事件序号的完整 `sqlite_sequence` 指纹。稀疏变更只使用更新、插入、删除三项一种形式；在线运行后的就绪核对保留原有生命周期快照语义。样板升级与新建看板继续比较 schema 结构，篡改测试覆盖保留内容、回执、workers、meta 和序列。

C1 提醒只对有原生事件流的编码 harness 执行调度和记录 `native-silence`，命令与外部执行保留预期时长提醒。服务层原生静默场景现使用离线 DSH CLI 夹具与私有 Worker；真实命令执行服务测试验证超过静默阈值后仍没有“可能卡住”及原生静默提醒。控制台继续通过通用界面读取空 buddy 的旧记录，没有命令或外部微任务专用文案、组件或预览分支。

Python 检查只使用 Host 指定的解释器、`PYTHONPATH=src` 和 `hey_my_buddy.cli.checks`，默认进程数，清除继承的运行时、Worker、凭据及虚拟环境变量。服务与状态由检查运行器建立在私有根目录，没有运行完整 Python 检查、付费模型调用、产品安装或日常服务操作。

本次已运行整个 `tests/python/install/`、`tests/python/blackboard/store/`、`tests/python/blackboard/service/`、`tests/python/blackboard/tasks/`、`tests/python/blackboard/routing/`、`tests/python/cli/`，以及读取 buddy 的 `tests/python/blackboard/catalog/test_catalog.py`，共 144 文件；目录运行中 142 文件、2012 项通过，升级与停止行为两个文件暴露的测试夹具问题已修正。升级夹具明确使用与原值不同的 harness；停止能力测试通过匹配的模型规格建立回合，并给所有未核实停止的尝试保留足够容量，不再只篡改尝试类型。

最终定向复测中，服务请求边界、工作流、CLI 帮助、服务提醒与升级样板共 5 文件、124 项通过，停止行为文件另 16 项通过，均退出 0；升级样板目前共 19 项。备份与各安装升级路径也经定向检查通过；服务原始请求边界与帮助的 134 项检查、严格回合与读取的 115 项检查均通过。所有检查运行器都确认清理了私有根，未跳过测试。该记录表示目录运行加修正后的定向复测，不表示重新运行完整 Python 检查或完成安装验收。

控制台使用 Node 24.21.0 执行 `npm --prefix apps/console ci`、`npm --prefix apps/console run typecheck`、`npm --prefix apps/console test` 和 `npm --prefix apps/console run test:browser`：类型检查通过，前端 82 文件、1127 项测试通过，浏览器 115 项通过。浏览器检查重建了 `src/hey_my_buddy/console/assets/`，替换脚本资源及入口引用；最终 `git diff --check` 通过。
