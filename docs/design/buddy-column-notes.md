# buddy 栏的存储、投影与升级

开发者于 2026-10-15 决定，微任务仅接受 buddy 执行：Host 给完整的 `harness`、`provider`、`model`、`effort` 四项，或省略全部四项由 Router 选择；命令与外部执行使用 `execution-submit` 建立普通执行记录。此前命令与外部微任务无法提供结构化回合，本批按该决定关闭入口，没有为命令伪造回合。本说明记录源代码候选与验证，等待 Host 验收；版本由 C 组统一调整，参考文档与术语表由 Host 更新。

微任务与协助任务各自使用明确的微任务规格字段集合，不再沿用普通执行记录的整套 `SUBMIT_FIELDS`。提交中的 `adapter`、`argv` 均在准备工作区、读取原生目录或写入任务前被明确拒绝，包括显式给出空值，错误指向 `execution-submit`；`harness` 的四项 buddy 校验和全部省略的路由行为继续使用原有路径。CLI 帮助按外层请求的字段白名单约束内部规格投影，微任务与协助任务帮助不再列出 `adapter`、`argv`。

`workflow_runs.buddy_json` 选定后只存四项 buddy，选定之前为 SQL `NULL`。`buddy_constraints` 只返回 buddy 字段；命令与外部微任务专用的提交、协助任务、原生 buddy 校验、派发、续做及权限分支已移除。新微任务只记录模型执行的 `workflow.buddy_selected`，运行时代码不再产生 `workflow.execution_selected`；普通命令与外部执行记录仍走原有路径，不建立微任务运行，也没有 buddy。

待做的 schema 21→22 升级保留旧形式的换算：严格识别旧适配器约束，核对运行所属任务的 `adapter` 后把 `buddy_json` 清为空；旧 `workflow.buddy_selected` 事件原地改为 `workflow.execution_selected`，载荷为 `{"source":"original-explicit","adapter":"command"}` 或相同字段的 `external`，保留事件序号。无法识别的 JSON、额外字段、未知适配器、不一致绑定及伪造的四项命令 buddy 都使整次升级回滚。当前四项 buddy 还要与当前任务适配器一致；历史模型选择事件只校验自身四项编码 harness 身份，允许之后发生合法的 Host harness 更换。

安装升级的 `idle_snapshot` 在换算前按完整行投影 `commands`、`workflow_runs`、`events` 应有的指纹，`verify_migrated_snapshot` 比对这些预期及其余保留表。样板升级与新建看板的 schema 结构继续比对，另构造命令与外部旧形式，覆盖序号保留、不一致回滚、历史模型选择保留和两类改动的篡改检测。

迁移后的旧行在 `workflow_get`、CLI `get`、`status`、`list` 中继续投影 `buddy: null`，执行类型从任务的 `adapter` 读取。历史回合的派生 buddy 同样只投影完整四项或空值，回合原始输入及哈希证据保留；测试覆盖当前回合、回合列表和审计回合列表。宏任务时间线也不再把适配器形状当作 buddy。控制台删除前一回合新增的命令与外部微任务文案、组件分支、预览数据和浏览器用例，旧的空 buddy 记录通过现有通用界面读取。

C1 提醒在写入边界限定原生静默的适用范围：只有 `CODING_HARNESSES` 中提供原生事件流的编码 harness 执行才调度和记录 `native-silence`。命令与外部执行保留预期时长提醒，不占用静默调度期限，也不产生“可能卡住”提醒；读取继续依据已记录的事实。测试使用既有离线 harness 夹具检查原生事件、静默与恢复，并分别覆盖普通命令与外部执行的期限和预期时长提醒。

Python 检查只使用 Host 指定的解释器、`PYTHONPATH=src` 和 `hey_my_buddy.cli.checks`，默认进程数，清除继承的运行时、Worker、凭据及虚拟环境变量，服务与状态由检查运行器建立在私有根目录。已运行整个 `tests/python/install/`、`tests/python/blackboard/store/`、`tests/python/blackboard/tasks/`、`tests/python/blackboard/routing/`、`tests/python/cli/`，以及目录中读取 buddy 的相关测试 `tests/python/blackboard/catalog/test_catalog.py`；没有运行完整 Python 检查、付费模型调用、产品安装或日常服务操作。

目录检查共 127 文件，其中 126 文件、1863 项测试通过，剩余标题性能测试把已有的续做恢复事件查询误算为标题查询。该测试现限定到使用 `workflow_turns` 的标题查询，并验证它仍共享 `workflow_runs` 读取；没有修改标题投影或续做恢复的生产代码。修正后定向复跑标题、宏任务、宏任务摘要缓存、提醒、工作流、升级切换、真实工作区执行和控制台看板夹具共 8 文件、154 项测试，全部通过，退出码为 0。涉及最终历史回合投影的升级、工作流、真实私有服务和 CLI 视图另经 4 文件、94 项定向检查通过；升级样板目前共 15 项，包含旧运行、选择事件与旧回合的读取和证据保留。所有检查运行器均确认清理私有根，没有跳过测试。

控制台使用 Node 24.15.0 执行 `npm --prefix apps/console ci`、`npm --prefix apps/console run typecheck`、`npm --prefix apps/console test` 和 `npm --prefix apps/console run test:browser`：类型检查通过，前端 82 文件、1127 项测试通过，浏览器 115 项通过。浏览器检查重建了 `src/hey_my_buddy/console/assets/`，替换脚本资源及入口引用，专用显示移除后没有新增的 i18n 文案；最终 `git diff --check` 通过。
