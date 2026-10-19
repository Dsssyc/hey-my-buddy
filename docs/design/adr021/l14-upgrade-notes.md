# L14 开头：归档、新看板与升级基点

这一步的升级基点是 `4b7f494670efe7a3fe053062f16cce9312a5e042`，版本 0.34.1、schema 18；当前 schema 是 19，表的形式与 schema 18 相同。升级只接受 18 与 19，日常启动仍只接受当前 schema，启动不做升级。

## 阶段与失败恢复

协调器先通过只读查询检查这一次待做升级的前提与备份清单，再准备运行时；持有升级与启动锁后重查，写入 `preflight` 日志并封住 Host 操作。旧服务以自己的客户端请求停止，协调器取得服务与数据库所有权锁，等 Worker 合作退出，再重查前提。schema 19 的升级保留原来的空闲含义和工作历史。

备份清单增加 `workspaces/*/input.json`、`manifest.json` 与 `outputs/` 的证据；这些文件由工作区模块写入，包含输入绑定、封存制品、补丁、冲突与解决记录，原清单漏了它们。扫描只进入这些明确的证据根，不进入 checkout 或分配锁。

没有写者之后，升级拒绝仍有非空 WAL 的数据库（不能把尚未 checkpoint 的事务遗漏在原字节归档之外），以原字节复制停止的数据库，按备份声明复制证据与状态文件，完成 SQLite 完整性、外键、清单及 SHA-256 校验，记录 `backed-up`。在线的普通备份仍用 SQLite backup API，保留 `backups/current` 一代。

18 → 19 把校验通过的备份改为永久归档。缺少上一代源码提交元数据时拒绝归档；先持久化归档目标，再移动目录并记录 `archived`；恢复既能找到移动前的 `backups/current`，也能找到移动后的归档。随后在删除边界再次校验归档以及现场每一份待删历史文件的大小与摘要，确认现场数据库逐字节等于归档。任何一项失败都不替换数据库、不删现场历史。

记录 `board-replacing` 后，在临时目录新建当前看板、写入设置，再替换当前数据库，记录 `board-created`。按已经校验过的清单删除现场历史文件，只删除已空的祖先目录，记录 `history-cleared`。启动目标服务，检查运行时、schema、容量、全表指纹与源目录依赖；随后发布 skill 与当前运行时指针，记录 `skill-switching`、`verified`，把结果保存到 `upgrade-last.json` 并清掉本次日志。

新服务启动或验证失败时，协调器取得真实的停止证据后，从归档恢复旧数据库与备份清单里的文件，启动上一代并验证，记录 `rolled-back`。校验失败且尚未替换数据库时直接启动上一代。无法证明恢复或停止时保留 `upgrade.json` 与归档，报告 `UPGRADE_RECOVERY_REQUIRED`，不把未知当作已经停止。归档永久留下，回滚也不删归档。

## 归档与查看

位置是 `<state>/archives/<UTC YYYYMMDDTHHMMSSffffffZ>-schema-18/`。目录含 `board.sqlite3.gz`、`state/` 下的证据、控制文件、提交记录、已声明的 Worker 收据及状态文件，以及 `manifest.json`。`archivedFrom` 记录写它的 source commit、版本与 schema；原有 `pluginCommit`、`contract`、`runtime`、数据库指纹和文件摘要仍在清单里。UTC 名字含微秒，后续升级不会覆盖这个目录。存储检查跳过整个 `archives`，计划和执行都不处理它。

查看时先复制归档到另一个私有目录，校验清单并把 `board.sqlite3.gz` 解压成 `board.sqlite3`，把归档的 `state/` 内容复制到那份查看目录，再使用 `archivedFrom.sourceCommit` 对应版本的运行时与私有状态路径打开。不要让新版本打开 schema 18，也不要直接启动归档内的数据。只读 SQL 检查可以直接针对解压副本进行。查看用的旧版本运行时、依赖准备及实际查看不在本微任务中执行。

## 带走的设置

| 来源 | 新看板写入处 | 原因 |
| --- | --- | --- |
| `meta.router_profile_ids` | 同名键 | Router 的有序列表 |
| `meta.router_default_mode` | 同名键 | Router 模式 |
| `meta.router_budget_preset` | 同名键 | Router 预算档 |
| `meta.router_retry_interval_seconds` | 同名键 | Router 重试间隔 |
| `evaluation_profiles` 全行 | 同名表 | buddy 身份、目录属性及启用状态，按原值复制 |
| `evaluation_preferences` 全行 | 同名表 | 固定、优先、排除及显式清除的 buddy 偏好 |
| `family_preferences` 全行 | 同名表 | 模型级固定、优先与排除偏好 |
| `family_annotations` 全行 | 同名表 | 模型备注 |
| `model_concurrency` 全行 | 同名表 | 每个模型的并发上限 |
| `harness_health` 全行 | 同名表 | 保留手工路径与本机命令解析、健康缓存；Router 通过这些行绑定 harness，缺失时所有 buddy 都不可用 |
| `evaluation_state.table_revision` 与 `configuration_revision` | 新默认行的同名字段 | 与带走设置各行的修订计数一致，当前路由和设置发布仍使用这两个计数 |

新看板初始化自己的 `capability_secret`、创建时间、Router 形式标记及 `evaluation_state` 默认行；不带旧密钥、评价卡片、评价证据、租约、修订历史、原生执行观察、任务和事件。`evaluation_state` 的 `writer_sequence` 与旧决策指针不带走。`router_configuration_version` 由新看板生成，不把旧形式标记当作设置复制。

控制台设置不在看板里，`console-settings.json` 原样保留；备份声明的六个根状态文件（控制台会话、控制台设置、Worker 池、运行时保留、当前运行时、启动设置）均不当历史删除。当前运行时及保留指针在成功切换时由原协调流程发布。白名单以外的私有 harness 数据不进归档，也不删除。

## 这一次待做升级的前提

`require_archive_readiness` 只对 schema 18 生效；它复制数据库及存在的 WAL 到私有临时目录再查询，避免只读打开离线 WAL 看板在现场创建 sidecar。在线的预检样本可能遇到并行写者；真正的删除前检查在停止并取得两把所有权锁后执行。`workflow_runs` 中执行、等 Host、等协助都阻止升级；已交付的微任务必须有现行有效的 Host 结论，其判定复用 `host_conclusions.current`，包括执行状态、选中执行、继续事件与制品集合摘要，不要求当前修订号等于结论当时的修订号。已验收、取消与失败是结束状态。

`tasks` 中排队、运行、取消中与待核对的工作都阻止升级，Router 和协助工作在同一清单内；`attempts` 中执行未完成或停止未确认都阻止升级；`workspaces/*/checkout` 仍在也阻止升级。`UPGRADE_NOT_IDLE` 列出全部 `active`、`unresolvedAttempts` 与 `managedCheckouts`，不截成前二十项。最早检查发生在创建升级日志、锁或准备运行时之前，拒绝不修改看板与现场文件。`idle_snapshot` 的原有空闲定义没有更改，19 → 19 仍允许没有待续工作的 Host 边界。

## 删除与验证边界

删除 `private_migration.py`、`workspace_identity_migration.py`、旧 `migrate_17_to_18`，删除它们的专属测试 `test_migrations.py`、`test_workspace_identity_migration.py` 与 `test_private_directories.py` 中的 `PrivateMigrationTests`；替换原先守旧换算的升级测试。升级日志没有 `identity-migrated`、`privateMigration` 阶段，预检没有 `legacyPlan`。运行时的别名读取保留，相关测试直接登记有效别名。`test_upgrade_host_cutover.py` 不再收集未使用的 `self.children`。

`test_upgrade_host_cutover.py` 原来就用同 schema 的两个私有运行时；当前样板随源码变为 19 → 19，测试名明确指出这一点，Host 等待、控制能力、受管检出、命令重放及跨升级 wait 的断言逐项保留。它没有跨 schema 保留开放微任务的断言。`test_upgrade_waiting_host.py` 的 `test_stopped_host_boundaries_are_idle_and_leave_the_board_untouched` 把写死的 schema 18 断言改为当前 `SCHEMA_VERSION`；attention、排队/运行、待续工作、停止未确认与 Host 操作封锁的原断言保留。新增的 schema 18 检查逐项拒绝等 Host、等协助、排队/运行、停止未确认、受管检出；未结论的已交付微任务由 `test_upgrade_migration.py` 拒绝，并比较看板字节。原来预检要求每一份跳过的私有文件有搬迁计划的断言删除；当前预检只拒绝备份声明位置的不安全项，跳过的文件继续显示 `needsAttention` 并原样留下。

样板由基点源码的真实命名操作生成，原生发现和 Worker 结果是合成输入，不登录、不读账户、不调用模型。它包含四项非默认 Router 设置、六类设置表、启用与禁用的 buddy、固定/优先/排除偏好、已验收、已交付而 Host 结束、取消与失败的历史，没有开放工作，也没有受管检出。样板加载器先绑定测试私有路径与摘要，这一步是夹具准备。逐表比较排除两块新看板各自生成的密钥与时间，其他行与“新建看板加设置”一致。

最终组合检查以指定的现成 Python 与 `PYTHONPATH=src` 经 `hey_my_buddy.cli.checks` 运行 `tests/python/install`、`tests/python/blackboard/store`，再加 `test_storage.py`、`test_storage_orphans.py`、`test_workspace_identity.py`、`test_private_directories.py` 与 `test_harness_test_safety.py`：27 个文件、304 项、无跳过，退出码 0，私有根已清理。此前的组合检查曾因工作区非分配项扫描和只读打开创建 sidecar 失败，均修在根因后复验通过。样板生成也完成基点 schema、完整性与外键核对；最后补强新密钥不同于旧密钥的断言，单独复验样板测试，4 项全部通过、退出码 0，私有根已清理。未执行日常安装、日常服务操作、付费模型路由或真实账户探测；路由能力验证限于私有服务与合成 harness 事实。未执行 Windows 本机升级；Windows 目录重命名的断电保证沿用现有备份边界，不新增保证。
