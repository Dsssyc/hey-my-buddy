# 升级基点移至 0.42.0 的说明

本次从输入提交 `7d23c296` 更新升级基点。按开发者确认，2026-10-16 09:35Z 安装的 `90bd0148230fe1b030cc65e325026dea0cdfb8b9`（0.42.0、schema 24）已经在日常看板上执行 23 → 24 的换算，因此该次升级已用掉；生成器与样板元数据现在以这个提交为基点，没有待做的换算。当前源码版本改为 0.42.1，`CONTRACT_VERSION` 仍为 0.42.0，`SCHEMA_VERSION` 仍为 24，C-Two 签名摘要仍为 `f4ae9329eb636fd604a14051963b575f42d325c0de408108dcd57639932740a5`。

在本检出根目录执行的样板生成命令为 `<prepared-python> packaging/generate-upgrade-fixture.py --python <prepared-python> --work-dir tmp/upgrade-base-0420 --output-dir tests/fixtures/upgrade-base`，其中 `<prepared-python>` 是任务指定的 `<整合检出>/.venv/bin/python`，使用现成依赖且不修改该解释器环境。生成器退出 0，输出 `integrity=ok`、`foreignKeyViolations=0`、`schema=24`、`tables=57`、`events=40`、`sourceSchemaMatches=true`，四个微任务分别为 concluded、accepted、concluded、cancelled。日志保存在忽略目录的 `tmp/upgrade-base-0420-generation.log`，生成工作目录保留在 `tmp/upgrade-base-0420/`；没有启动服务、调用模型或读取账户。

重新生成了 `tests/fixtures/upgrade-base/` 的 `board.sql`、`metadata.json`、`operations.json` 和 `workspace-resources.json`；`catalog.json` 的内容没有改变。元数据记录了基点的源码树 `ca947ee5c3c76d1c7517a9f14fc572a782ab3b79`、生成器与各输入输出的摘要，以及已准备依赖的版本。`WORKSPACE_INPUT=122ec20edcdeb97cc655ae3993c402c2c9c966a3` 仍是用于小型工作区的固定输入提交，与生成看板的源码基点分别记录；测试只恢复文本样板、绑定私有路径及其身份摘要，不运行生成器或基点源码。

删除了 `src/hey_my_buddy/blackboard/store/upgrade.py` 整个文件：唯一的生产函数 `upgrade_schema_23_to_24` 和常量 `UPGRADE_BASE_SCHEMA=23` 都只用于这次换算，包括删除 `execution.result` 回执、把历史命令答案改为已提交身份、删除 `attempts.result_command_id`、压缩周期事件、更新 schema 标记及包围这些操作的事务与校验。上述事务与外键检查属于已删除函数内部；仍在用的安装与备份边界上的完整性及外键检查全部保留。

删除了安装器 `before['schema'] == UPGRADE_BASE_SCHEMA` 分支及其换算调用，`idle_snapshot` 只接受当前 schema，备份 `_verify` 也只接受当前 schema。备份中的 `if manifest['schema'] == SCHEMA_VERSION` 原先只为了让旧基点备份跳过当前结构检查，现在去掉条件、无条件执行 `Database(trial).initialize()`；文件清单、路径与链接保护、大小与摘要、SQLite 完整性和外键、数据库指纹、实际 schema 与 manifest 一致性校验都保留。

删除了 `tests/python/blackboard/store/test_upgrade_fixture.py` 中的两个专用测试：`test_authoritative_allocation_references_survive_receipt_and_state_conversion` 只比较旧回执与状态换算前后的分配引用，`test_failed_conversion_rolls_back_all_receipts_and_schema` 只注入换算中断并检查旧 schema 的事务回滚；后者内部唯一的故障注入辅助函数 `interrupt` 随测试删除。计数为 1 个生产函数、1 个测试内辅助函数、2 个测试方法及 1 个常量，没有删除其他函数或测试文件。

保留并更新了 `test_contract_cutover_preserves_stock_and_matches_fresh_structure`：新基点与当前 schema 相同，因此删除旧换算所需的人工事件、期望回执和列转换，改为直接逐表比较所有存量行，再与新建看板比较完整 `sqlite_master` 结构；仍检查工作区历史、设置、偏好、请求指纹、约束、索引、完整性与外键。`test_current_core.py` 的 meta 写入清单删除了唯一的旧换算写入项；其通用写入约束测试保留。

`test_upgrade_archive.py`、`test_upgrade_service.py` 和样板中的原生执行读取、提交重放测试只移除恢复样板后的换算调用，保留各自的数据、证据、日志、回滚、服务读取与重放断言。样板恢复辅助模块的说明改为 schema 24；启动验证的健康数据使用 `SCHEMA_VERSION`。现有升级拒绝测试增加 schema 23，现有备份拒绝测试保留 schema 21 并增加 schema 23，确保已消耗的基点被边界拒绝。

逐项检查后保留了通用的 `idle_snapshot` 空闲与实际停机证据检查、`preflight` 按契约版本分流、锁与写入围栏、备份创建和验证、`write_journal` 与恢复、`restore`、`verify_started` 的运行时身份及数据指纹检查、skill 的准备与发布回滚、活动运行时指针及保留策略。契约不变时保留执行并接回、契约变化时要求空闲并备份的两条路径均未改动；同一 schema 切换原样保留看板的测试继续存在。`command_receipts.stored_response` 是当前命令回执写入的通用规则，保留实现，只删除说明中关于换算 schema 23 的文字。

版本更新执行了 `uv lock --python <prepared-python>`，退出 0；`uv.lock` 只把 `hey-my-buddy` 自身的版本从 0.42.0 改为 0.42.1，其他包的版本、依赖和摘要没有变化。

测试前仅在命令进程中清除了继承的 `HMB_STATE_DIR`、`HMB_RUNTIME_ROOT`、`HMB_RUNTIME`、`HMB_RUNTIME_IDENTITY`、`HMB_AGENT_CREDENTIAL_FILE`、`HMB_AGENT_CREDENTIAL_FD`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`，并清除检查运行器的目录与进程数覆盖，随后只使用任务规定的命令形式：`PYTHONPATH=src <prepared-python> -m hey_my_buddy.cli.checks tests/python/install/ tests/python/blackboard/store/ tests/python/protocol/test_contract_version.py`，输出保存在 `tmp/upgrade-base-0420-checks.log`。使用默认 4 个 worker，共运行 27 个文件；26 个文件的 259 个测试全部通过，`install/test_first_install.py` 的 12 个测试中 11 个通过、1 个失败，总计 271 个测试中 270 个通过。唯一失败为 `DocumentationTests.test_readmes_keep_first_install_parity`，仍为 0.42.0 的 README 缺少 `uvx hey-my-buddy@0.42.1 install`，与任务预告一致；没有修改或跳过该测试。检查退出 1，运行器确认私有根目录已移除。

`blackboard/store/test_upgrade_fixture.py` 的 7 个测试全部通过，包括新基点的切换保留及新建结构比对；`install/test_upgrade_archive.py` 的同 schema 连续升级、启动失败、备份失败与中断恢复测试均通过。`install/test_install_without_upgrade.py`、`install/test_execution_install_fence.py`、`install/test_upgrade_host_cutover.py` 与 `install/test_upgrade_service.py` 通过，分别覆盖契约不变时保留执行、安装围栏、实际私有运行时切换及私有服务读取；契约版本与签名摘要的 3 个测试也通过。内部只读复核未发现问题，`git diff --check` 通过；未运行完整检查、前端检查或日常安装，最终整合与验收由 Host 完成。

与任务要求不一致的情形：没有。两份 README、`AGENTS.md`、`CONTEXT.md`、`skills/` 及说明文件之外的 `docs/` 均未修改；没有变更存储形式、契约或 schema，也没有安装产品、操作日常服务、移动分支或标签、使用 stash 或提交。源码工作仅发生在分配的检出中，生成器的工作副本与日志留在本检出的 `tmp/`，检查运行器使用并清理自己的系统临时目录。
