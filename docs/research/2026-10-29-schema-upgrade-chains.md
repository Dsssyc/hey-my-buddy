# 按版本号串起的升级：成熟工具怎样分步、旧步骤留多久、预发布给数据什么保证

## 问题

黑板的全部状态在一个 SQLite 数据库里，`meta` 记着它的 schema 版本。按 [AGENTS.md](../../AGENTS.md) 的存储一段与 [ADR-021](../decisions/021-router-buddy-planes-and-routing-evidence.md) 工作分解里“schema 的升级跟着存储形式的改动走”一条，源码只带一份待做的升级（pending upgrade），把升级基点的黑板升到当前形式：基点是上一个发布的版本，现在是首个发布 0.54.1（schema 30），当前是 31，更早的换算已经删掉；首次发布之前，跑过真实黑板的升级就用掉，基点挪到写出那块黑板的提交。现在的情形是：开发者把每个验收通过的版本装在有价值的日常黑板上，日常黑板已是 31（[0.57.2 的安装记录](../acceptance/installs/0.57.2.md)）；用户（目前是用发布版 0.54.1 的实验室同学）的黑板是 30；设计还在变，存储形式改得很勤。待开发者定的是三件事：要不要从一个支持下限起保留一串分步的升级，每一步在真实黑板上跑过就冻结；这些步骤留多久，发布时要不要压缩；要不要设一个数据保证较弱的、beta 之前的阶段。这份记录只收集成熟工具的做法和它们自己写明的理由，不做选择。别的工具把这件事叫 migration，下文说它们时写“迁移”，说本项目时写“升级”。

## 看了什么

- **本仓库**（提交 `6449ace8`）：AGENTS.md 的存储一段；ADR-021 工作分解里升级那一条；`src/hey_my_buddy/blackboard/store/upgrade.py` 全文，它从 `db.py` 引用的几个定义，以及 `db.py` 里新建黑板的 `initialize`。
- **公开文档**，2026-10-29 经网页抓取工具读取。这个工具对每页只按提问摘出相关段落，长页面没有逐字通读；括号里是页面自己标的版本或日期。
  - 迁移链：Django 的 [Migrations](https://docs.djangoproject.com/en/6.1/topics/migrations/) 与 [Migration Operations](https://docs.djangoproject.com/en/6.1/ref/migration-operations/)（6.1）；Rails 的 [Active Record Migrations](https://guides.rubyonrails.org/active_record_migrations.html)（8.1）；Alembic 的 [Cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html) 与 [Operation Reference](https://alembic.sqlalchemy.org/en/latest/ops.html)（1.20.1）；Redgate 的 Flyway 文档 [Baseline migrations](https://documentation.red-gate.com/fd/baseline-migrations-273973336.html)（2025-01-15 更新）、[baseline](https://documentation.red-gate.com/fd/baseline-277578867.html) 与 [validate](https://documentation.red-gate.com/fd/validate-277578898.html) 两个命令（2026-10-21 更新）、[Flyway schema history table](https://documentation.red-gate.com/fd/flyway-schema-history-table-273973417.html)（2026-09-25 更新）；Liquibase 的 [What is a changeset checksum?](https://docs.liquibase.com/pro/user-guide/what-is-a-changeset-checksum)（Pro 4.33，2026-08-12 更新）、[How do I trim my changelog?](https://docs.liquibase.com/community/user-guide-5-0-3/how-do-i-trim-my-changelog)（Community 5.0.3）、[Best Practices](https://docs.liquibase.com/concepts/bestpractices.html)（现在落到 Secure 5.1 的一页 FAQ），以及 Liquibase 博客的 [Dealing with changing changesets](https://www.liquibase.com/blog/dealing-with-changing-changesets)（2008-10-03，2025-09 更新）。Redgate 网站上一篇 2021 年的客座文章 Flyway Baselines and Consolidations 读了，没有采用：那是客座作者自己的做法，不是产品文档。
  - SQLite 上的做法：sqlite.org 的 [PRAGMA](https://www.sqlite.org/pragma.html) 与 [ALTER TABLE](https://www.sqlite.org/lang_altertable.html)（后者 2026-10-05 更新）；Android Developers 的 [Migrate your Room database](https://developer.android.com/training/data-storage/room/migrating-db-versions)（2026-10-23 更新），`RoomDatabase.Builder` 的 API 参考页抓到的只有导航，没读到正文；Apple 的 [Staged migrations](https://developer.apple.com/documentation/coredata/staged-migrations) 与 [NSManagedObjectModelReference](https://developer.apple.com/documentation/coredata/nsmanagedobjectmodelreference)（经文档站的 Markdown 接口），归档的 Core Data Model Versioning and Data Migration Programming Guide 的[引言](https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/CoreDataVersioning/Articles/Introduction.html)与 [Lightweight Migration](https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/CoreDataVersioning/Articles/vmLightweightMigration.html) 两节（2012-01-09）；MozillaWiki 的 [DOM/QuotaManager/SchemaChanges](https://wiki.mozilla.org/DOM/QuotaManager/SchemaChanges)（2018-05-24）与 [Places:Migration](https://wiki.mozilla.org/Places:Migration)（2007-05-20，只涉及 Firefox 3 的测试版，没有用上）；Home Assistant 的 [Recorder](https://www.home-assistant.io/integrations/recorder/) 集成页（2026.10.4）。
  - 分站升级：GitLab 的 [Upgrade paths](https://docs.gitlab.com/update/upgrade_paths/) 与 [Background migrations](https://docs.gitlab.com/update/background_migrations/)，开发文档的 [Required stops](https://docs.gitlab.com/development/database/required_stops/) 与 [Migration squashing](https://docs.gitlab.com/development/database/migration_squashing/)；Sentry 的 [Self-Hosted Releases](https://develop.sentry.dev/self-hosted/releases/)；Nextcloud 管理手册的 [How to upgrade](https://docs.nextcloud.com/server/latest/admin_manual/maintenance/upgrade.html) 与 [built-in updater](https://docs.nextcloud.com/server/latest/admin_manual/maintenance/update.html)（标为 36，即将发布）；CockroachDB 的升级页 [v26.3](https://docs.cockroachlabs.com/docs/stable/upgrade-cockroach-version) 与 [v24.1](https://docs.cockroachlabs.com/docs/v24.1/upgrade-cockroach-version)；NetBox 的 [Release Checklist](https://netboxlabs.com/docs/netbox/development/release-checklist)；PostgreSQL 18.6 的 [pg_upgrade](https://www.postgresql.org/docs/18/pgupgrade.html) 与 [pg_dump](https://www.postgresql.org/docs/18/app-pgdump.html)；Gitea 28 的 [Upgrade from an old Gitea](https://docs.gitea.com/installation/upgrade-from-gitea)。
  - 预发布与 1.0 之前：PyPA 的 [Version specifiers](https://packaging.python.org/en/latest/specifications/version-specifiers/) 规范（经 PEP 440 通过）；[pip install](https://pip.pypa.io/en/stable/cli/pip_install/)（pip 26.2.2）；uv 的 [Resolution](https://docs.astral.sh/uv/concepts/resolution/)（页脚 2026-10-14）；PyPI 的 [Classifiers](https://pypi.org/classifiers/)；[Semantic Versioning 2.0.0](https://semver.org/)；Elastic 的 [Elasticsearch 升级页](https://www.elastic.co/guide/en/elasticsearch/reference/current/setup-upgrade.html)（9.5.5）；DuckDB 2.0 文档的 [Storage](https://duckdb.org/docs/2.0/internals/storage.html) 与 [0.10.0 的发布公告](https://duckdb.org/2024/02/13/announcing-duckdb-0100.html)（2024-02-13）；Immich 博客的 [stable release](https://immich.app/blog/stable-release)（2025-10-01）；Zulip 的 [Schema migrations](https://zulip.readthedocs.io/en/latest/subsystems/schema-migrations.html)（latest）；Synapse 开发文档里讲数据库 schema 的一页（没标版本）。
- **搜过、没找到第一方文档的**：Chromium 的 SQLite meta 表（搜索结果只有源码头文件）；Firefox 现在的配置文件数据库升级下限与降级（支持站的相关文章抓不到正文）；Home Assistant recorder 的迁移链留多久、从多老的版本能升；写明“预发布之间不迁移”或“发布时压缩预发布期间的迁移”的项目。
- **没有读任何项目的源码。** 搜索结果的摘要里出现过 Chromium 的源码头文件、Mozilla 的测试代码与 Home Assistant 的拉取请求，没有打开，也没有采用；下文标“二手”的几条只来自搜索摘要。

## 看到的事实

依据的写法：**自述**是该项目自己的文档、规范或第一方工程文档，第一方博客另行注明；**二手**是转述，没能追到第一方原文；**推断**是我们的推论；**本仓库**是读本仓库源码所得；**未找到**是第一方来源里没找到。

**按版本串起的迁移链，旧步骤留多久**

- **Django 6.1。** 迁移按应用组成依赖图：每个迁移在文件里写明依赖哪些迁移，名字里的数字只是给人看的（自述）。压缩把一串迁移合成一个新迁移，新迁移标明它取代了哪些，可以与旧文件并存：新装的库只走压缩后的那个，走到一半的库先走完原来那串，再切到压缩后的历史（自述）。推荐的流程分两次发布：压缩、保留旧文件、发布；等所有系统都升过这次发布（第三方项目则要保证用户按顺序升、不跳发布），再删旧文件、发第二次；删的时候把压缩后的迁移改成普通迁移，也就是删掉被取代的文件、把依赖它们的迁移改为依赖压缩后的那个、去掉 `replaces`（自述）。6.0 起可以再压缩还没改成普通迁移的压缩迁移，文档仍建议先改成普通迁移，才能清掉旧文件（自述）。`RunSQL` 与 `RunPython` 可以标为 `elidable`，压缩时被丢掉（自述）。
- **Rails 8.1。** 迁移以文件名里的 UTC 时间戳为版本，库里的 `schema_migrations` 记着哪些跑过（自述）。schema 文件（`schema.rb` 或 `structure.sql`）是重建数据库的权威来源；新库载入它比重放全部迁移更快、更少出错，旧迁移若依赖会变的外部东西，可能跑不通（自述）。所以旧迁移文件可以删；跑过它们的环境在 `schema_migrations` 里仍留着它们的时间戳，状态命令把这些显示为没有文件；来自引擎（engine）的迁移一般应当保留（自述）。
- **Alembic 1.20.1。** 修订之间靠 `down_revision` 相连（cookbook 删旧文件的做法里用到它，自述）。新库可以用 SQLAlchemy 的 `create_all` 直接建成当前形式，再用 `stamp` 记为最新修订，不必走几百个迁移；这样迁移脚本只负责把过时的库搬到新版本，不再负责新库（自述）。于是任何现存环境都不再停在其上的旧修订文件可以直接删，并把留下的最早一个的 `down_revision` 设为空（自述）。
- **Flyway。** 历史表记着每个迁移由谁、在何时应用，它的校验和，以及是否成功（自述）。validate 在名称、类型或校验和不一致、已应用的版本在本地找不到、本地有未应用的版本时都判失败；改过已应用的迁移会造成校验和不符，改动是有意的，就用 repair 重置历史表（自述）。以 B 开头的 baseline migration 是一份累积脚本，代表到某个版本为止的状态：新的或空的环境从最新的一份起步，低于它的版本标为忽略；已经应用过迁移的环境忽略它；它不取代版本化迁移（自述）。baseline migration 之下的版本化迁移能不能删，文档没写（未找到）。另有 baseline 命令，用来接手 Flyway 之前就有的库：在历史表里写一个标记，此后把不高于该版本的迁移都当作已应用（自述）。
- **Liquibase。** changeset 以 id、作者与文件路径识别，部署过的记在 `DATABASECHANGELOG`，并存一份 MD5 校验和；部署过的 changeset 被改过，下次运行时校验失败并停下（自述）。应对办法有逐行把校验和置空、在 changeset 上加 `validCheckSum`、用 `clear-checksums` 重算全部，以及给存储过程这类对象用的 `runOnChange`（自述）。博客：还没分享出去的 changeset 可以在自己的环境回滚、改好再应用；一旦提交，就控制不了它在哪里跑过，只能加一个新的 changeset 往前修；`validCheckSum` 只留给已在某些环境跑过、又有无法挽回的错误的情形（第一方博客）。缩减 changelog 的页面只讲把大文件拆成几个用 include 引入的文件，不讲删除或合并旧 changeset（自述；合并的官方做法未找到）。

**SQLite 上“一个版本号，每步一段迁移”**

- **SQLite。** `user_version` 是文件头里留给应用随意使用的一个整数，SQLite 自己不用它（自述）。`schema_version` 由 SQLite 在 schema 改变时自增，已准备的语句靠它发现 schema 变了；改写它可能让语句按旧 schema 执行，甚至损坏数据库（自述）。`ALTER TABLE` 只能直接改名与加减列，别的改动要按文档的 12 步重建表：关外键，开事务，存下相关的索引、触发器与视图，建新表，拷数据，删旧表，把新表改成原名，重建索引、触发器与视图，跑 `foreign_key_check`，提交，再开外键；顺序必须照做，先把旧表改名再建新表是错的（自述）。
- **Room。** 自动迁移依赖两个版本各自导出的 schema JSON；这些 JSON 就是数据库的 schema 历史，文档要求放进版本控制，用来重建低版本做测试和生成自动迁移（自述）。删除、改名这类有歧义的改动，自动迁移在编译时报错，要开发者提供一份 `AutoMigrationSpec`（自述）。手写的 `Migration` 声明起止版本；同一版本两种都有时用手写的（自述）。找不到从设备上的版本到当前版本的路径时抛异常；`fallbackToDestructiveMigration` 这时删表重建，文档写明会永久删掉全部数据；`fallbackToDestructiveMigrationFrom` 只对列出的起始版本这样做，用在 schema 历史里某些版本的错误无法用迁移路径解决的时候；另有只在降级时生效的一种（自述）。迁移里要写完整的 SQL，不要引用代表查询的常量；测单个迁移时不能用 DAO，因为 DAO 按最新的 schema 写（自述）。有几条路径、有直达的一段时 Room 怎样选，指南没写（未找到）。
- **Core Data。** 存储只能用创建它的那个模型版本打开；迁移需要创建它的版本与当前版本，所以带版本的模型里存着多个版本，其中一个标为当前（自述，2012 年的归档指南）。改动符合明显的模式时，轻量迁移自动推断映射；Core Data 在应用与框架的包里找源模型，找不到就要自己用迁移管理器（自述，同上）。现在的文档：几个版本累积起来超出轻量迁移的能力时，分阶段迁移把它拆成一串兼容的阶段，每个模型版本各要一个阶段；`NSManagedObjectModelReference` 以版本校验和指认一个具体的模型版本，iOS 17 与 macOS 14 起可用（自述）。轻量迁移能否一次跨过几个版本，文档没写（未找到）。
- **Firefox。** MozillaWiki 2018 年的一页把各存储组件按降级时的表现分成两类：发现版本被降就拒绝工作的硬失败（Cache API、IndexedDB、Quota Manager 等），以及照常使用、可能出错的软失败；Places 属于软失败，并把 schema 版本调回旧版本认识的值，让新版本再用这个配置文件时重跑迁移；页上还记着 Places 只照顾到 ESR52 为止的版本（自述，旧页面）。支持站说 Firefox 67 起拒绝打开被更新的版本用过的配置文件，并提供新建一个（二手：只来自搜索摘要，原文没抓到）。
- **Home Assistant。** Recorder 页只说：有 schema 变化的升级几乎总要临时复制数据库的一部分，所以要留够磁盘；换数据库引擎时不支持迁移数据（自述）。迁移链留多久、从多老的版本能升、能否降级，第一方文档里没找到（未找到）；社区回答说不支持降级，2025 年 1 月有一处改动让旧版本拒绝在更新的 schema 上启动（二手：只来自搜索摘要）。
- **Chromium。** meta 表的当前版本、兼容版本与废弃版本，没有找到第一方文档（未找到）。

**要用户一站一站地升的产品，以及它们写明的理由**

- **GitLab。** 必经的升级站点（required upgrade stop）是升到更新版本之前必须先装上的版本；每到一站，等后台迁移跑完再去下一站；17.5 起站点固定在 x.2、x.5、x.8、x.11（自述）。用户文档给的理由是这些版本修了升级过程本身的问题（自述）。开发文档列出数据库一侧需要加站点的情形：后台迁移还没确认完成（finalize），而后续迁移依赖它；确认完成耗时太长，会让升级超时；删除一个迁移，要等它进过一个计划中的站点；时间戳早于上一站的迁移，可能在压缩时被一并压掉（自述）。压缩在 .2、.5、.8、.11 进行，压到当前站点往前数两站的那个版本（例如在 18.8 压到 18.2 的稳定分支），把迁移并进初始化用的结构文件，同时清掉已确认完成的批量后台迁移；16.3 起有，18.9 起自动（自述）。升级之前全部后台迁移必须已完成，否则升级报错停下（自述）。
- **Sentry 自托管。** 升级必须经过每一个 hard stop，才能带上重大的数据库变更；文档列出九个站点，并说升得越不勤，直接升到最新越可能不成（自述）。
- **Nextcloud。** 必须一步一步升，不能跳过大版本，并且先升到当前大版本的最新小版本；不支持降级，降级可能损坏数据；大版本升级之后有些迁移作为后台任务跑，再升下一个大版本之前要确认它们跑完（自述）。为什么不能跳，文档没写（未找到）。
- **CockroachDB。** 集群总可以升到下一个大版本，支持期短的 Innovation 版可以跳过（v26.3，自述）。大版本升级要经过 finalization 这一步：它启动的迁移不能回滚；在它之前可以换回旧的二进制回滚，之后不能（自述）。
- **NetBox。** 发布清单测试的升级路径是同一大版本里的小版本之间，以及从上一个小版本的最新补丁版升上来；测法是载入源版本的演示数据再跑迁移（自述）。
- **Gitea 28。** 版本号前两位相同的补丁版之间保持兼容；跨小版本时，新版本把库升成可能不同的结构，降级只能靠备份（自述）。
- **对照：PostgreSQL 18.6 的 pg_upgrade。** 支持从 9.2 起的任何版本直接升到当前大版本，包括每日构建（snapshot）与 beta 版（自述）。它能跨这么多版本，是因为大版本常改系统表的布局，而用户数据的存储格式很少改：pg_upgrade 建新的系统表，直接沿用旧的数据文件；哪天存储格式改得旧格式不可读，它就用不了（自述）。恢复 schema 出错时只能退回旧集群；链接模式在新集群启动之后、交换模式在开始搬文件之后，旧集群就不能再用（自述）。pg_dump 能从老到 9.2 的服务器导出，导出的内容能载入更新的服务器，但它拒绝从比自己新的大版本导出（自述）。它不是一串逐版本的步骤，而是当前版本带着一个能读许多旧版本的读取器（推断）。

**预发布与 1.0 之前的数据保证**

- **Version specifiers（PEP 440）。** alpha、beta、候选版这一轮预发布，是让用户在正式版之前测试的；开发版在处理时也算预发布（自述）。安装工具默认把预发布排除在所有版本约束之外，除非它已经装着、没有正式版满足约束，或者用户明确要求（自述）。版本号里的 0 没有特殊含义，只是排序最低（自述），所以 0.x 不是这个意义上的预发布（推断）。pip 默认只找稳定版，加 `--pre` 才包括预发布（自述）；uv 默认偏好稳定版，满足约束的稳定候选都被排除时才退到预发布（自述）。
- **PyPI 的 Development Status。** 从 Planning 到 Inactive 七级，由维护者自己选，用来让人检索；页面不定义各级的含义，也不核对（自述）。分类的原始规范 PEP 301 没有读（未读）。
- **SemVer 2.0.0。** 0.y.z 是初始开发，任何东西随时可以变，公共 API 不应视为稳定；预发布版表示不稳定，可能达不到预定的兼容要求；软件已经在生产里用，或者很在意向后兼容，大概早该是 1.0.0（自述）。它说的是 API，没说存储的数据（推断）。
- **明说预发布没有升级路径的。** Elastic：从候选版升级不受支持，预发布只用于临时环境里的测试（自述）。CockroachDB：alpha、beta、rc 这些测试版不适合生产；升到 alpha 二进制或从 master 自建的二进制的集群，之后不能再升到正式版，没给理由（v24.1，自述）；v26.3 的同一页已经没有这一段（自述）。
- **从某个版本起才承诺存储格式兼容的。** DuckDB 0.10 是第一个存储格式向后兼容的版本，此后的目标是新版本都能读旧版本写的文件；跨不兼容的版本，文档的办法是用旧的二进制导出（`EXPORT DATABASE`），再用新的导入（自述）。0.10 之前不兼容，是从“第一个”推出来的（推断）。Immich 2025-10-01 宣布 stable：从 2.0.0 起遵循语义化版本，以兼容为先；文中没讲 stable 之前对数据承诺过什么（第一方博客）。
- **发布时压缩预发布期间的迁移、或明说预发布之间不迁移的项目，第一方文档里没找到**（未找到）。最接近的是 Django 的两次发布与 GitLab 的按站点压缩，二者针对的都是正式发布。
- **没发布的可以改，发出去的冻结。** Rails：跑过的迁移改了也不会重跑，要先回滚再改；已提交的迁移一般不该改，应写一个新的；还没提交、没离开开发机的可以改，这很常见（自述）。Liquibase 的博客同理（第一方博客）。Flyway 与 Liquibase 用校验和把“已应用的不改”变成每次运行时的检查（自述）。Synapse：应用过的 delta 文件不会再跑，哪怕文件后来改了（自述）。Zulip：还没合入的迁移撞号时，删掉重新生成或重新编号；要挑回稳定分支的迁移，依赖设为稳定分支上最后一个迁移（自述）。Django 的专题页说很少需要手改迁移，但没有一般地区分已应用的与未应用的（自述；这一区分在页上未找到）。
- **Synapse 的兼容版本。** 库里记着能与它一起工作的最老的 Synapse，启动时发现它比代码的 schema 版本新就拒绝启动；不向后兼容的改动分几个版本做完，以便还能回退（自述）。新库用的 full schema 只取编号最新的一份（自述）。

**迁移步骤引用当前的 schema 或模型**

- **Django。** `RunPython` 要用迁移给的历史模型；直接导入模型起初也许能跑，以后重跑旧迁移时会失败，最常见的就是新装、从头跑全部迁移的时候（自述）。字段选项引用的函数、标了 `use_in_migrations` 的管理器、自定义字段和模型的具体基类会被迁移直接引用，只要还有迁移引用它们就得留着；要去掉，就压缩，或者把它们抄进迁移文件（自述）。
- **Alembic。** 迁移脚本里用到的表定义最好自包含在脚本里，不要从可能破坏旧迁移兼容的模块导入（自述）；单独写的数据迁移脚本可能要一个专门的 ORM 模型来对付中间状态（自述）。
- **Rails。** 旧迁移依赖会变的外部东西就可能跑不通；指南一般不建议在迁移文件里做数据迁移，理由是 schema 与数据的生命周期不同、难以稳妥回滚、可能长时间锁表（自述）。
- **Room、Zulip、Core Data。** Room 的自动迁移比较的是各版本导出的 schema JSON，不是当前的实体类，迁移里也不引用常量（自述）。Zulip 的 `RunPython` 用 `apps.get_model`，不导入 Zulip 自己的其他代码，需要的常量抄进迁移并加注释，由 linter 检查（自述）。Core Data 按模型版本迁移，旧的模型版本留在应用里（自述）。
- **本仓库。** 新黑板由 `db.py` 当前的整份 `SCHEMA` 直接建出，版本记为当前的 `SCHEMA_VERSION`（本仓库）。30 → 31 的升级用 `db.py` 里当前的 `DOMAIN_EVALUATION_SCHEMA`、`STATISTICS_SCHEMA` 与 `ROUTER_MAINTENANCE_COLUMNS` 建表加列，调用 `routing/statistics.py` 里当前的 `backfill`，最后把版本写成当前的 `SCHEMA_VERSION`，不是常数 31（本仓库）。在“只有一份升级、随存储改动一起改”的规则下，这与规则一致；改成分步冻结之后，这几处会随 31 → 32 的改动一起变，30 → 31 就不再是它跑过日常黑板时的样子（推断）。

**小结（推断）**

- 有长期用户数据的工具几乎都按版本记下库已经走到哪一步，并把发出去的一步当作不可改：错了就加新的一步。
- 新库多半不重放历史，而由一份当前形式的整份定义建出（Rails、Alembic、Synapse、GitLab、Flyway 的 baseline migration），本项目也是这样；旧步骤只为已有的库存在，所以能删到“没有现存的库还停在那里”为止。
- 何时删，有按“所有已知的安装都过去了”（Django、Alembic）、按公布的站点（GitLab、Sentry）、一次只许升一个大版本（Nextcloud、CockroachDB）几种；能跨很多版本直达的 pg_upgrade 靠的是用户数据的格式很少变，不是一串步骤。
- 逐个记下哪些迁移应用过的工具（Django、Rails、Flyway、Liquibase、Synapse）才有条件让压缩后的一步与被它取代的几步并存，Django 的 `replaces` 与 Flyway 的 baseline migration 就是这样；只记一个整数版本的（SQLite 的 `user_version`、Room、本项目的 `meta`）要压缩，就要每块库都恰好停在压缩的两端之一，停在中间的库还得走原来的几步。本项目的发布标签打在装过的那个提交上（AGENTS.md），所以发布那一刻，日常黑板正停在发布的形式上；用户只装发布版，也只会停在两次发布的形式上。在那之后把两次发布之间的各步压成一步，没有哪块已知的黑板停在中间（本仓库，推断；Host 补）。
- 明说预发布不提供升级路径的 Elastic 与 CockroachDB，同时要求预发布只用在临时环境。

**没能从第一方来源回答的问题**

- Chromium 的 meta 表有没有第一方文档。
- Firefox 现在对配置文件数据库的升级下限与降级怎样处理（只有 2018 年的 MozillaWiki，支持站的文章没抓到）。
- Home Assistant recorder 的迁移链留多久、从多老的版本能升、能否降级。
- Room 有几条迁移路径时怎样选；Core Data 的轻量迁移能否一次跨过几个模型版本。
- Flyway 在 baseline migration 之下的版本化迁移能不能删；Liquibase 有没有合并旧 changeset 的官方做法。
- Nextcloud 不许跳大版本的理由；CockroachDB 不许从 alpha 构建升到正式版的理由；Synapse 从多老的 schema 还能升上来。
- 有没有项目在第一方文档里写明“预发布之间不迁移”或“发布时压缩预发布期间的迁移”。
- PyPI 的 Development Status 各级在别处（例如 PEP 301 或打包指南）有没有定义。

## 取了什么，没取什么

这份记录不做选择，下面各项待开发者定。定下之后落到 AGENTS.md 的存储一段、ADR-021 工作分解里升级那一条与 `docs/reference/operations.md` 的升级契约。

**一、要不要从支持下限起保留一串分步的升级，每步在真实黑板上跑过就冻结**

- **甲：分步，跑过即冻结。** 最强的理由：除了 pg_upgrade，所看的有长期数据的工具都是这个形状；每一步只写一次、之后不再碰，开发者的 31 与用户的 30 都能沿链走到当前（推断）；“发出去就不改，错了往前加一步”正是 Rails、Liquibase、Flyway 的规矩。代价：每一步要自带 DDL、常量与数据逻辑，不再引用 `db.py` 的当前定义和 `backfill` 这类当前代码（Django、Alembic、Zulip、Room 的要求；本仓库的 30 → 31 现在正引用当前定义）；开发者每个验收通过的版本都装，几乎每一步都在第一次安装时冻结，装上之后才发现的问题都成了新的一步（推断）；样板黑板（fixture board）要有每一步转换或删除的形式的存量，检查要走完整条链，链越长越重（推断）。
- **乙：仍是一份升级，但它接受下限与当前之间的每一种形式，各自直达当前。** 最强的理由：只有一段转换要审，像 pg_upgrade 那样从老版本直达，Room 也允许直达的一段。代价：一段代码要分别处理每个起点，实质是把链折进一个函数，每改一次形式就要改写对所有起点的转换（推断）；pg_upgrade 能直达的前提是用户数据的格式很少变，本项目改的恰是表的形式（推断）。
- **注意：** 照首次发布之前的做法，跑过真实黑板就把基点挪到写出日常黑板的提交，与 AGENTS.md 里“基点是上一个发布的版本”一条相抵：挪走之后，0.54.1 写的黑板就升不上来（推断）。
- **与上面的选择无关的一点：** 像 Room 导出每个版本的 schema、Core Data 保留每个模型版本那样，可以把每个发布或每次冻结时的整份 schema 作为文件留下，冻结的步骤与它的测试只引用这份文件，不引用 `db.py`（推断，供参考）。

**二、步骤留多久，发布时要不要压缩**

- **留到所有已知的黑板都过去**（Django、Alembic）。最强的理由：不扔下任何已知的安装。代价：用户的黑板停在哪个版本，开发者只能去问（推断）；Django 对第三方项目的办法是要求用户按顺序升、不跳发布。
- **按公布的站点压缩**（GitLab 压到往前两站，Sentry 的 hard stop）。最强的理由：链长有上限，用户事先知道要经过哪些版本。代价：落后很多的用户要一站一站地装中间版本，那些版本在他们的机器上得还装得上（推断）。
- **一次只升一个发布**（Nextcloud、CockroachDB）。最强的理由：每个发布只带从上一个发布来的那一步。代价：等于每个发布都是站点，跳过发布的用户要补装（推断）。
- **发布时把两次发布之间的各步压成一步。** 最强的理由：发出去的链短，用户只走一步。代价：本项目只有一个整数版本，压缩前停在中间版本上的黑板，压缩后的那一步对它没用。不过发布标签打在装过的提交上，发布那一刻日常黑板正停在发布的形式上，所以压缩若在发布之后做，就不必像 Django 那样让新旧几步并存（本仓库，推断；Host 补）。没找到第一方文档写明这样做的项目。

**三、要不要设一个数据保证较弱的、beta 之前的阶段**

- **最强的理由：** Elastic、CockroachDB 对预发布不提供升级路径，DuckDB 在 0.10 之前不承诺格式兼容，SemVer 说 0.y.z 任何东西都可以变；在这样的阶段里，存储形式可以改而不写升级。若经 PyPI 这样的索引分发，PEP 440 的预发布版本号默认不会被 pip 与 uv 选中，用户不主动要就拿不到（自述）；用户现在怎样安装 0.54.1，这份记录没有核对。
- **代价：** 那些项目同时要求预发布只用在临时环境，而本项目的开发者恰在有价值的日常黑板上跑每一个没发布的版本，弱保证先落在开发者自己身上（推断）；SemVer 与 PEP 440 只谈 API 与安装，不谈数据，0.x 本身不带数据上的含义（推断）；AGENTS.md 已规定 0.x 期间不留兼容代码，同时要求能从基点升级，两条之间的界线要一起考虑（推断）；PyPI 的 Development Status 只是维护者自选的标签，页面没给它任何含义（自述）。

## 时效

- Django 改了压缩与 `replaces` 的语义，Rails 指南改了 schema 文件与旧迁移的说法；
- Flyway 写明 baseline migration 之下的迁移能否删除，Liquibase 给出合并旧 changeset 的官方做法；
- Room 的文档写明路径怎样选，Apple 写明轻量迁移能否跨版本；Chromium、Firefox 或 Home Assistant 有了第一方的迁移文档；
- GitLab 改了站点间隔或压缩的往前距离，Nextcloud 允许跳大版本，PostgreSQL 把 pg_upgrade 的下限提到 9.2 之后或改了存储格式；
- Elastic、CockroachDB 改了预发布的升级政策，pip 或 uv 改了预发布的默认处理，PyPA 给 Development Status 定了含义；
- 本仓库的升级不再引用当前定义，或升级规则改了（这份记录读的是提交 `6449ace8`）。
