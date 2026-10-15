# 已验收微任务的修正

`submit` 接受可选的 `fixes`，值为一个已验收微任务的 `runId`。请求边界拒绝非字符串、空值和不合法的标识；入库事务中检查目标存在且状态为 `accepted`，并复用 `_owner_actor` 沿 `workflow_children` 找到控制目标的根，从根的 `host_id` 或 Lead 关系取得当前拥有者，再与提交方的 Host 标识或 Lead 微任务标识比较。协助任务创建时复制的 `host_id` 不参与权限判断，因此接管后由新的拥有者提交修正，Lead 也能修正自己名下微任务的已验收协助任务。Lead 仍凭当前执行的凭据提交，不能提供 Host 或宏任务字段。拒绝时整个提交事务回滚，不留下新微任务、关联或事件。

修正默认沿用目标的 `macro_task_id`，目标没有宏任务时修正也没有宏任务。Host 的宏任务选择复用 `macro_tasks.attach_macro_task` 的选择器解析和项目核对；不写选择器时先取目标的宏任务，写了选择器则必须选出同一个宏任务，跨项目提交拒绝。`macroTaskOf` 指向没有宏任务的微任务时仍报 `MACRO_TASK_REQUIRED`；`macroTask` 会创建另一个宏任务，因此拒绝。修正已证明当前拥有权，不再要求宏任务的来源 Host 与提交方相同。Lead 直接继承其宏任务，目标及其协助任务的宏任务归属由原有准入规则保证。`fixes` 参与提交的幂等指纹，同一 `requestId` 改成修正另一个目标会发生冲突；它不进入执行规格。

`workflow_fixes` 独立保存关联：`run_id` 为显式 `NOT NULL` 的主键，表示修正；`fixed_run_id` 表示被修正的微任务；`created_at` 为记录时刻。两个标识均外键引用 `workflow_runs` 并限制删除，自关联由约束拒绝；`(fixed_run_id, created_at)` 索引用于反向读取。一次修正只指向一个目标，一个目标允许多次修正，修正验收后仍可成为后续修正的目标。新微任务、关联与 `workflow.fix_submitted` 事件在同一个事务中写入，目标继续保持 `accepted`。

这张表及其索引加入当前待执行的 schema 21 → 22 升级，与新建看板共用同一份建表定义。迁移后的核对声明该表为新增空表，非空时拒绝；schema 21 看板若已存在同名结构，升级拒绝并回滚。样板看板通过实际迁移流程、迁移后的核对和与新建看板的完整 SQLite 结构比对。

`get` 与 `status` 为修正返回 `fixes: "<runId>"`，为目标返回 `fixedBy: ["<runId>", ...]`；没有关联时字段不出现。`fixedBy` 按记录时刻升序排列，同一时刻按插入顺序排列。CLI 的简短输出保留这两个字段，`list` 不增加关联字段。时间轴每行采用相同的可选字段，前端在响应边界校验其类型。

时间轴用弯箭头与目标标题表示修正方向，被修正行用回指箭头 `↩` 及数量表示已有修正，与等待状态的菱形区分。目标不在当前响应中时显示它的标识；执行 buddy 仍可从提示与可访问名称读到。关联文案提供中文与英文。合成预览增加同一个已验收微任务被修正两次的例子，并核对目标已在修正创建前验收；浏览器用例在两种语言、两种宽度下检查字段、形状、可访问名称和布局。

本次仅登记与展示修正，不把原微任务的交付缺陷计入评价。评价依赖结果事实，按开发者确定的范围留到首次发版之后；公开参考文档和共享 skill 由 Host 更新。本说明记录 Worker 的验证结果，最终验收由 Host 执行。

验证使用 Host 指定的独立解释器，以 `PYTHONPATH=src <test-python> -m hey_my_buddy.cli.checks <文件或目录>` 运行，沿用默认进程数并清除继承的运行时与执行凭据环境。`tests/python/install/`、`tests/python/blackboard/store/`、`tests/python/cli/`，以及提交、Lead、宏任务、修正和相关读取的指定测试，共 57 个文件、801 项通过；最终强化后的修正与升级测试又以 2 个文件、20 项通过。检查退出码均为 0，私有测试根目录均已移除，未运行完整 Python 检查。回归用例覆盖新建与升级表的实际非空约束、宏任务接管后协助任务的当前拥有者、Lead 修正已验收协助任务、跨项目提交和独立微任务的 `macroTaskOf`，拒绝均断言零新增。

前端在受支持的 Node 24.21.0 下执行 `npm --prefix apps/console ci`、`npm --prefix apps/console run typecheck`、`npm --prefix apps/console test` 和 `npm --prefix apps/console run test:browser`。浏览器使用 `HMB_PREVIEW_PYTHON=<test-python>` 指定合成预览解释器，浏览器安装设置 `PLAYWRIGHT_SKIP_BROWSER_GC=1`，浏览器检查同时重建 `src/hey_my_buddy/console/assets` 中的打包资源。

最终前端依赖准备、类型检查、82 个文件的 1127 项测试和 115 项浏览器检查均通过，退出码均为 0；重建资源与 Python 打包检查时验证的资源逐文件哈希一致。`git diff --check` 通过。
