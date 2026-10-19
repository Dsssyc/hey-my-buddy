# 升级基点挪到 0.38.0

开发者指定的新升级基点为 `f620f1e2719ccd170e6183e89c3f9eb04d084f32`（0.38.0，schema 21）；`packaging/generate-upgrade-fixture.py` 的 `BASE`、`BASE_VERSION`、`BASE_SCHEMA` 已更新，`tests/fixtures/upgrade-base/` 已由该提交的源码重新生成。当前产品版本和 `SCHEMA_VERSION` 未改，schema 21 的表、字段、约束和存量语义未改。

生成命令为 `<prepared-python> packaging/generate-upgrade-fixture.py --python <prepared-python> --work-dir tmp/upgrade-base-0380 --output-dir tests/fixtures/upgrade-base`，其中 `<prepared-python>` 是本微任务指定的只读依赖解释器；没有写入该解释器的环境。生成器退出 0，结果为 `integrity=ok`、`foreignKeyViolations=0`、`schema=21`、52 张业务表、42 条事件、`sourceSchemaMatches=true`，四个任务的状态分别为 concluded、accepted、concluded、cancelled；原始输出保存在忽略目录的 `tmp/upgrade-base-0380-generation.log`。生成器仅在独立子进程内导入冻结基点源码，以进程内服务操作写样板，原生发现及 Worker 结果为合成输入，没有启动服务、访问账户或调用模型。生成的 SQL、操作记录、工作区资源及 metadata 已替换，catalog 内容未变；metadata 记录来源提交、源码摘要、生成器摘要和依赖版本。

删除 `src/hey_my_buddy/blackboard/store/upgrade.py` 整个模块，共 15 个函数和 4 个常量：`BASE_SCHEMA_VERSION` 固定旧 schema 20；`_REBUILT` 指定这次换算重建的表；`_ACTOR_TABLES` 指定从单列 actor 拆成角色与标识的表；`_BARE_ACTORS` 是 schema 20 的 actor 值映射。模块没有独立的通用安装或备份功能，所有函数均由这次换算内部调用。

删除的 `_actor`、`_normalize_actor` 只转换旧 actor 字符串；`_generation`、`_nested_generation`、`_cancellation_generation`、`_owner_at_generation` 只从旧事件及回执推导换算所需的所有者代次；`_host_id`、`_current_owners` 只恢复旧 Lead 关系对应的身份；`_rewrite_receipt_view` 只重写这次升级涉及的产品回执字段；`_remove_legacy_evidence`、`_remove_legacy_attempt_result`、`_remove_legacy_receipt_result` 只删除旧 `legacyStartedAt` 证据；`_target_structure`、`_rebuild` 只为这次表重建准备和写入目标结构；`migrate` 负责这次 20→21 离线事务、旧表清理及提交后的完整性检查，已随换算整体删除。

安装协调器删除对已删除模块的导入及迁移、迁移中日志阶段，备份成功后以原快照作为切换验证基准。`idle_snapshot` 现在只接受当前 schema 21，继续在只读事务中检查空闲、真实停机证据、SQLite 完整性和外键。备份 `_verify` 删除仅为旧换算保留的 schema 20 接受分支，所有接受的备份都执行当前 `Database.initialize` 校验；清单格式、证据策略、路径、文件清单、大小、摘要、数据库指纹、完整性、外键及 schema 一致性校验均保留。

删除 `tests/python/blackboard/store/test_upgrade_fixture.py` 中 4 条仅为旧换算存在的测试：`test_lead_relationship_converts_authority_and_preserves_opaque_actor_text` 测旧 Lead、actor、回执和中断证据换算；`test_unsettled_legacy_attempt_refuses_conversion_without_consuming_base` 测旧 `attempt_legacy_starts` 阻止换算；`test_unknown_actor_refuses_conversion_atomically` 和 `test_malformed_product_actor_refuses_conversion_atomically` 测旧 actor 输入拒绝及事务原子性。原 `test_old_submission_digest_is_cleared_and_replay_is_explicitly_refused` 保留重放语义，改为 `test_unavailable_submission_digest_refuses_replay_and_new_submissions_replay`：直接构造 schema 21 允许的空指纹存量，验证相同及不同输入都拒绝重放，且新请求仍可重放；删去它对旧换算清空指纹及回执的断言。

保留样板来源与摘要验证、Host seal、私有路径重绑定和历史不变测试；`test_schema_upgrade_preserves_stock_and_matches_fresh_structure` 继续通过完整安装协调器验证备份、运行时切换、新建看板结构比对和历史保留，改为比较所有表的指纹及请求指纹原值，不再排除旧换算重写的表。`test_current_core` 的 meta 写入清单删除已不存在的迁移写入者。两个安装测试删除准备阶段的旧迁移调用，原同 schema 连续升级、故障回退、历史文件与归档保留、私有服务读取历史的验证仍在。`test_upgrade_refuses_older_or_unknown_schema` 新增 schema 20 拒绝用例，备份测试新增 `test_verify_refuses_consumed_upgrade_schema`，验证旧基点不能再通过清单校验。

逐项判断后保留的通用部分包括备份生成、原子发布、预检和恢复，升级锁与 admission fence，停机与空闲检查，升级日志写入、清除和恢复，故障回退，skill 的校验、发布和恢复，运行时物化、选择、切换、启动后验证及保留清理。同 schema 看板继续原样保留；没有新增开关、空迁移函数或兼容入口。旧归档文件不被修改或删除，当前备份入口只接受当前 schema。

挪基点之后，专为 schema 20 换算后存量保留的形式是 `workflow_runs.request_fingerprint` 可为 NULL；`WorkflowCoordinator._require_replayable_submission` 及 `submit` 的正常重复请求与并发冲突恢复调用点，继续以 `SUBMISSION_REPLAY_UNAVAILABLE` 拒绝这些旧请求，并要求新 requestId。上述保留的重放测试覆盖这一形式。已保存的空指纹产品回执通过普通 JSON 读取返回，没有新增或保留专用换算器。Lead 的空 host、结构化 actor、integrator 和删除旧中断证据后的字段均是 schema 21 的正常形式，当前新写入也使用，不能作为只为旧存量存在的代码删除；冻结 turn 的缺失 routing/sharedStash 观察属于独立的历史证据边界，本次没有改动。

先运行 `PYTHONPATH=src <prepared-python> -m hey_my_buddy.cli.checks tests/python/blackboard/store/test_upgrade_fixture.py`，1 个文件的 5 条测试通过，跳过 0，退出 0，私有根目录已删除；随后运行 `PYTHONPATH=src <prepared-python> -m hey_my_buddy.cli.checks tests/python/install/ tests/python/blackboard/store/`，25 个文件的 260 条测试全部通过，跳过 0，退出 0，私有根目录已删除。第二次检查覆盖全部改动过的测试文件及样板恢复辅助模块；进程数均使用默认值（4），未运行完整检查。原始输出分别保存在忽略目录的 `tmp/upgrade-base-0380-focused.log` 和 `tmp/upgrade-base-0380-checks.log`。最终 `git diff --check` 通过，样板 metadata 的生成器和四份资源摘要与现有文件一致。

与任务要求不一致的情形：没有。没有修改其他 docs、skills、README、AGENTS.md、CONTEXT.md、产品版本或 schema 版本，没有安装产品、变更分支和标签或创建提交；操作范围为分配的检出及生成器、检查运行器创建的私有目录。
