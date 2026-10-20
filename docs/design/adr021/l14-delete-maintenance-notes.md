# ADR-021 L14：提前删除 Host 维护评价

2026-10-13，在 `e9c1988f` 的独立检出上删除，随后从未验证的部分产物 `63306ec5` 续做。依据 ADR-021 第 9、15 条及改名计划的第 3、4 个微任务；本记录供 Host 检查源码与安排后续工作，不代表安装或验收。待升级走归档旧板、新建板并带走设置；本次直接修改新板定义，没有工作历史换算，也没有修改升级基点夹具、安装代码、版本号或名称。

## 操作和许可

以下 9 个公开操作及其 CLI、C-Two `BuddyControl` 方法、transport 映射、客户端封装、服务处理、帮助与控制台入口已删除：`evaluation-write-begin` / `evaluation_write_begin`、`evaluation-write-renew` / `evaluation_write_renew`、`evaluation-write-abort` / `evaluation_write_abort`、`evaluation-reader-begin` / `evaluation_reader_begin`、`evaluation-reader-release` / `evaluation_reader_release`、`evaluation-evidence-record` / `evaluation_evidence_record`、`evaluation-prepare` / `evaluation_prepare`、`evaluation-history` / `evaluation_history`、`assessment-publish` / `assessment_publish`。没有留下兼容别名。

另删除 `BoardClient.evaluation_write_publish`：它只封装已经不受服务支持的 `evaluation_write_publish`，并非本次之前仍可调用的第 10 个公开操作。客户端关于维护写者、读者与证据的便捷方法随所属操作删除。

`model-profiles` 与 `console-snapshot` 保留为模型、设置和路由记录的读法，但不再返回卡片、证据、样本、待处理证据或维护状态。`selection-get` 与 `selection-list` 保留路由面的路由记录，删除维护分支及其发布材料。公开 `evaluation-reader-*` 被删，Router 内部的选择读者继续由路由生命周期持有。

服务能力说明中删除 `maintenanceScope`，快照能力删除 `maintenance`、`maintenanceMode`、`evaluationWriteGate`、`readerAdmission`、`evidenceRecord`。没有新增角色或许可。Worker 的回合凭据继续只允许观察自己及提交结果等原有操作；维护操作已经从允许的方法集合中消失。数据库的写者 HMAC 生成、校验、版本常量和写者序号一并删除。

原控制台用户设置也借用上述写者许可。删除写者之后，`user_policy_publish` 直接接受 `commandId`、`expectedRevision` 与既有设置补丁字段；在一个事务中检查版本、更新设置、增加原有版本、写事件与幂等收据。控制台认证、输入指纹、相同命令重放、变更命令冲突、原子失败与旧版本拒绝继续成立。保存不再等待或续租维护写者；已开始的 Router 如果遇到设置版本变化，仍走原有 `stale` 路径。

## 存储

| 删除的表 | 原写者 | 原读者 |
| --- | --- | --- |
| `evaluation_cards` | Host 的 `assessment_publish` | 维护数据包、Router 选择输入、控制台与模型分页 |
| `evaluation_card_history` | 卡片发布时的历史快照 | 维护准备、卡片历史读法 |
| `evaluation_evidence` | Host 证据录入、维护准备、微任务验收后的重新判定 | 卡片引用校验、维护数据包、Router 选择输入、控制台 |
| `evaluation_evidence_pending` | 证据录入、引用同步与维护发布 | 维护准备、待处理计数与控制台 |
| `evaluation_samples` | 已核验的微任务证据录入与验收重新判定 | 样本归属、维护统计、卡片计数 |
| `evaluation_aggregates` | 证据、待处理与样本变更 | 维护、控制台样本数和待处理计数 |
| `evaluation_maintenance_checkpoints` | 增量维护准备 | 后续维护准备与剩余计数 |
| `evaluation_writers` | 写者 begin/renew/abort、用户与维护发布 | 写者许可、队列、公平提升、维护和路由入场、控制台租约 |

删除这些表的索引及维护专用 `events_review_seq_idx`。`evaluation_state.writer_sequence`、`evaluation_revisions.writer_id`、`evaluation_decisions.evidence_ids_json` 也删除；`decision_requests` 的 `writer_id`、`writer_generation`、`published_revision`、`proposal_json`、`considered_evidence`、`pending_after`、`auto_publish` 删除，`kind` 只接受 `select`。结果和验收的原始记录及事件本身保留，取消为评价证据重新判定的调用。

`evaluation_revisions` 保留：模型目录观察与用户设置发布仍写入它，模型目录检查仍读取其中的计数；不再有维护历史的公开读法。`evaluation_catalog`、`catalog_observations`、`catalog_current` 与 pending 模型目录记录保留，它们是程序发现事实。`evaluation_profiles`、`evaluation_preferences`、`family_preferences`、`family_annotations`、`model_concurrency`、`model_facts` 与 `effective_preferences` 保留，分别承担 buddy、启用、偏好、备注、并发上限和模型事实；归档升级的 `SETTINGS_TABLES` 原样保留。

## 模块、输入和界面

`blackboard/evaluation/evaluation.py` 删除卡片发布、证据归属与核验、样本计数、维护扫描与检查点、写者许可、读者公开操作及维护历史投影，剩下发布的 buddy/设置/路由快照、内部时间与版本辅助、模型目录记录入口和控制台设置发布。`user_policy.py` 删除维护角色和写者授权字段；`catalog_store.py` 的模型分页删除卡片和样本。`store.py`、`tasks/workflow.py` 删除验收时评价重新判定；`tasks/storage.py` 删除已不存在的证据表引用扫描。没有删除真实微任务、回合、验收、路由或工具运行证据。

Router 输入删除卡片、评价证据和它们的引用集合，只保留任务、合法候选、程序事实、偏好和模型备注。答案的卡片引用种类与校验删除，偏好、模型备注和审阅文件引用保留；原生工具调用与实际停止证据继续校验。Router 角色文字不再要求读取或引用评价卡片。`buddy/roles/router_input.py` 只负责冻结 Git 输入的物化、摘要与验证，没有卡片数据，原样保留。

控制台删除样本计数、候选详情中的评价卡片与证据、维护详情与维护状态；快照、模型分页、解析、前端测试夹具与文字随之清理。基点前端已没有独立的更新记录页面或入口，本次删除的是其后端读法及文档中的入口说明，没有删除不存在的组件。保留模型配置、备注、偏好、并发、Router 设置和选择记录。删除维护写者的保存、排队、续租、取消与租约恢复逻辑，设置直接按版本保存；未知回复仍按同一命令重试。读缓存删除写者/读者维护闸门的租约边界，快照不再显示该闸门。

具体界面与解析落点：`App.tsx` 删除顶部维护状态、保存排队与取消等待；`buddies/ModelDrawer.tsx` 删除样本计数；`DecisionDetails.tsx` 删除维护分支、整理建议和发布版本，以及候选快照中的卡片/证据；`types.ts`、`decision-types.ts`、`console-data.ts`、`draft.ts`、`use-profile-history.ts` 删除对应类型、计数与分页合并；`use-editor.ts` 删除写者许可的生命周期并保留草稿、版本冲突与不确定发布的同命令重试。`Card`、`Evidence`、`Gate`、`WriterGrant` 类型不再存在。`api.ts` 不再要求 `gate`/`cards`，并删除写者错误文案；API 的读取时间单测改用现存的 `selection_list`。其余界面没有重新设计。

`docs/reference/evaluation-maintenance.md` 删除；`docs/reference/evaluation.md` 保留模型、用户设置、控制台与路由的内容，并删除卡片和维护说明。`docs/README.md` 删除维护参考页入口并纠正 ADR-008 仍现行的陈述。共享 skill 删除模型卡片更新那一句；skill 构建按存在的参考页收集文件，删除页面后不再进入构建清单。纯维护测试 `blackboard/evaluation/test_external_evaluation.py`、`blackboard/routing/test_evaluation_routing.py` 和 `console/test_console_gate_deadlines.py` 删除；卡片/证据/维护写者的专用用例删除，其他测试迁移为直接设置发布与当前输入。

## Host 续做决定与测试清理

第一续做回合发现基点中缺失的 `WorkspaceIdentityFixture.snapshot`、`migrate`、`all_rows`、`historical` 导致 7 个测试文件失败。Host 确认第一个升级微任务删除身份归一换算时删掉了这些助手，但遗漏了调用它们的测试；随后授权在本微任务内处理，并把 `tests/probes` 加入范围第 2 版。没有恢复任何助手、换算或旧形式读法，也没有修改运行期读别名的源码。

本次只删除以下两条身份归一专用测试；通用的宏任务筛选、游标、路由边界与请求重放仍由各自现有测试覆盖。

| 删除的测试（`blackboard/tasks/test_workflow_preparation.py`） | 原来守护的行为 | 删除依据 |
| --- | --- | --- |
| `test_three_identity_eras_keep_macro_filters_cursors_attach_and_replay` | 归一前几代 repositoryId 的宏任务与微任务筛选、旧游标、附着和请求重放，归一后均映射到当前项目 | 身份归一换算已删除；不恢复几代存量换算 |
| `test_router_boundary_keeps_the_legacy_checkout_after_index_normalization` | 旧 checkoutId 索引归一之后，路由继续边界仍找到旧检出并使用当前索引身份 | 只服务于已归一存量上的别名读取；按 Host 决定随旧准备步骤删除 |

`test_workflow_preparation.py` 保留并改写三条测试：`test_initial_immutable_input_wins_over_later_manifest_reservation_and_cwd` 用当前提交创建不可变 input artifact，再让可变记录偏离，重建私有 board 后确认最初输入仍决定项目归属；`test_restart_helper_transfer_handoff_and_return_keep_single_ownership` 用当前协助操作移交检出，重建 board 后验证竞争者被挡、封存交还与唯一持有者；`test_restart_failed_helper_partial_recovery_uses_the_same_checkout` 用当前失败协助任务留下部分文件，重建 board 后确认同一检出恢复，旧回合输入及摘要不变。类改为当前操作的 `RestartPreparationTests`，删除了身份归一专用的导入。

`test_workspace_lifecycle.py` 没有删除测试。五个末尾场景改用当前操作创建状态并重建私有 board：`test_restart_native_seams_continue_seal_integrate_and_reclaim_allocation` 保留续跑、封存、集成、归还和不可变输入；`test_restart_preserves_accepted_target_and_cleanup_plan_before_apply` 保留目标身份、验收重放与跨重启清理计划；`test_restart_cannot_remove_a_copied_worktree_git_directory` 保留复制 Git 目录后的身份拒绝；`test_restart_scope_restore_keeps_the_failure_and_continues_helper` 保留协助范围冲突恢复与续跑；`test_restart_abandon_returns_input_and_preserves_the_failed_site` 保留放弃冲突与失败现场。测试名移去旧身份准备的描述，不再绑定缺失的助手，仍使用原来存在的 `fixed_files` 检查证据文件不变。

`tests/probes/objective_console_preview.py` 删除 `evaluation_history`、`evaluation_write_begin`、`evaluation_write_renew`、`evaluation_write_abort` 的允许入口及维护历史回复；删除快照的 `gate`、`cards`、`evidence`、`pendingEvidence`、`sampleCounts` 和维护能力，删除模型分页的卡片/样本、决策的维护发布字段、`SAMPLE_COUNTS` 与 `sample_counts()`。保留设置版本、用户策略、路由健康统计、微任务结果与原生执行证据；决策的现存引用仍用 `evidence`。`tests/probes/router_fast.py` 同步删除准备包的空 `cards` 字段，没有执行真实 Router 调用。真实前端解析器的预览检查现在也核对快照不再携带旧字段；`App.test.tsx` 与 `desktop-console.test.tsx` 删除混合用例中的旧卡片/观察字段断言，保留设置和草稿行为。

## Host 第三回合打回：Claude 额度测试与全 tests 扫描

Host 在整合后的完整检查中发现 `buddy/harnesses/claude/test_claude_worker.py` 的额度失败测试仍调用已删除的 `EvaluationStore._sample_basis`。该文件不在前两回合的五个测试目录内。本回合把 `test_quota_failure_is_persisted_as_harness_error_without_a_capability_sample` 改名为 `test_quota_failure_is_persisted_as_harness_error_without_retry`，只删除末尾构造评价存储与样本归属的断言；保留额度拒绝记为 `harness-error`、错误码 `quota-rejected`、结果没有 `turn` / `workspaceSeal`、没有封存、只执行一个回合而不自动重试，以及 `quotaFailure` 的字段断言。

从 `e9c1988f` 到当前源码的差异提取 90 个已删除的方法/函数名，包含下划线开头的内部方法；对整个 `tests/` 同时搜索这些名字、已删除常量、8 张表、保留表中的 10 处删除列及对应的传输字段，再用 Python AST 核对代码中的属性调用和名字加载。除本次已移除的 `_sample_basis` 使用处，未发现其他仍调用旧评价实现的代码，没有恢复样本或维护操作的替身。

保留的命中有明确边界：`tests/fixtures/upgrade-base/board.sql` 与 `operations.json` 是禁止改动的升级基点；CLI / console 测试中的旧操作名用于确认入口已消失或返回 `METHOD_NOT_FOUND`；Router 答案的 `evidenceIds` / `card` 用于确认旧输入被拒绝，快照与有界输入中的旧字段断言用于确认不会再返回它们；`CONFIGURATION_FIELDS`、`_catalog`、`_checkpoint`、`_publish`、`prepare`、`history` 解析到现存协议、harness 或其他测试助手；`tests/probes/console_layout_check.mjs` 的 `cards` 变量表示设置页面容器。Router 自用的 `evaluation_readers` 继续留给下一微任务，没有把这些现存行为当作旧维护实现删除。

本回合按任务指定解释器和私有检查器运行 `tests/python/buddy` 整个目录，清除原有运行时与凭据环境：82 个文件、1,608 项测试通过，0 跳过，退出 0，私有测试根已删除。说明完成后，`cli/test_repository_hygiene.py` 的 5 项也通过、退出 0，私有根已删除，`git diff --check` 通过。没有重复运行前端或重建资产，因为只改动该 Python 测试及本说明；前两回合的前端与资产证据仍以各自当时的源码为边界。Host 指出的范围外参考文档链接由 Host 处理，本回合没有修改。

## 留给下一个微任务的版本闸门

`blackboard/store/db.py` 的 `evaluation_state.table_revision`、`configuration_revision` 和仍现存的旧名字不变，`migrations.py` 带走两种版本及所有既定设置的行为不变。`evaluation_revisions`、profile 的 created/updated revision、偏好/备注的更新版本继续存在。

`evaluation.user_policy_publish` 仍以 `expectedRevision` 检查当前表版本并在发布时增加表版本；`user_policy.publish` 更新 Router 设置时仍增加设置版本；`catalog_store.record` 在模型目录改变时仍推进表版本。控制台仍保存草稿的基准表版本并处理版本冲突。

`routing/decision.py` 的请求冻结表与设置版本、claim 前的变更检查、`_frozen_circumstances_problem`、发布前的表/设置版本检查、`_reader_open`、`stale` 结算与读者释放、`_mark_running` 的预期版本绑定原样保留。Router 更换、合法候选/任务输入改变和读者到期的停路仍成立；删除的是已经不存在的维护写者占用阻塞。

`evaluation_readers` 及其索引、`decision_requests.reader_id` 与 `expected_revision` 仍保留。Router 在自己的 claim 事务中入场，运行中续租，结算时释放；公开 Host 读者入口已删除。相关路由、工作流、Router 健康/历史与控制台版本字段未改名。这些地方需下一个微任务统一去掉版本闸门，不能仅凭本次卡片删除推断已经去掉。

## 验证和边界

第二续做回合在当时源码上运行指定的五个 Python 目录：`tests/python/blackboard`、`tests/python/buddy/roles`、`tests/python/console`、`tests/python/cli`、`tests/python/protocol`。检查器使用任务指定解释器及 `PYTHONPATH=src`，清除继承的 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`、`BUDDY_RUNTIME`、`BUDDY_RUNTIME_IDENTITY`、`BUDDY_WORKER_STATE`、`BUDDY_WORKER_ID`、`BUDDY_AGENT_CREDENTIAL`、`BUDDY_AGENT_CREDENTIAL_FILE`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`，并使用支持的 Node 24.21.0 供跨组件检查调用。146 个文件、2,253 项测试通过，0 跳过，最终退出 0，私有测试根已删除。没有运行完整 Python 检查，没有安装、日常看板或真实 harness / 付费模型调用。

第一续做回合的五目录检查曾退出 1：138 个文件通过，8 个文件失败。目录回滚测试引用了基点里已删除的 `blackboard.store.test_migrations.rows`，已改为比较私有数据库全部当前表的行，保留整个目录发布事务回滚的断言，单独重跑 16 项通过；其余 7 个文件由上节缺失助手造成，现已在最终五目录检查中全部通过。第二续做回合还分别聚焦检查了 `test_workspace_lifecycle.py`（49 项）、`test_workflow_preparation.py`（30 项）和 `cli/test_host_preview.py`（3 项），均退出 0、私有根已删除。第一续做回合的后端两文件聚焦检查另有 52 项通过。

前端依赖在第一续做回合以 `npm --prefix apps/console ci` 准备，退出 0。本回合最终使用支持的 Node 24.21.0 运行 `npm --prefix apps/console test -- --maxWorkers=4`，78 个文件、1,098 项通过、退出 0。此前默认并发下 `desktop-console.test.tsx` 的备注草稿交互用例触发 5 秒超时，单文件重跑 13 项通过；最终限制测试进程并发，没有修改断言或超时阈值。

前端全套通过后运行 `npm --prefix apps/console run test:browser -- --config .worker-playwright.config.ts`，其前置构建退出 0，并在清理后的合成预览上运行 `apps/console/browser/*.check.ts`，96 项通过，命令退出 0。仓库默认浏览器配置用 uv 启动预览，本回合的临时配置只把启动命令换成任务指定解释器，并清除继承的运行时/凭据变量；配置已删除。Chromium 使用检出内 `apps/console/node_modules/.cache/ms-playwright`，npm 使用检出内缓存。最终打包文件为 `assets/index-BR2BiGHz.js`（772.41 kB）与 `assets/index-BN6Y1rxs.css`（114.81 kB），`index.html` 指向这两个文件；旧 JS 已被替换。构建只有大于 500 kB 分块的提示，没有编译错误。

说明文件完成后，按指定检查器重跑 `cli/test_repository_hygiene.py`，5 项通过、退出 0，私有根已删除。`git diff --check` 通过；安装代码、安装测试、固定 `tests/fixtures` 与运行期读别名源码没有差异。源码与合成预览验证不代表安装或 Host 验收。

`packaging/generate-upgrade-fixture.py` 保留旧操作：它固定从发布基点源码生成 schema 18 的 stock，测试不执行它，不能把固定基点改成新板。`tests/fixtures` 原样保留。版本与安装由其所属微任务处理。

Host 另需同步的范围外现行文档：`README.md` 与 `README.zh-CN.md` 的维护说明；`docs/reference/cli.md` 的维护操作与写者字段；`docs/reference/decision.md` 的 Host 维护段；`docs/reference/architecture.md` 的写者排队理由与维护契约；`docs/reference/console.md` 的维护/写者描述。`docs/design/adr029/console-frontend.md` 中“evaluation-history 不受影响”的记录也已过时。历史 ADR 与旧验收记录可作为历史证据保留；本微任务没有修改这些范围外文档。

## 执行偏差

以下两项来自上一个留下 `63306ec5` 的执行回合，保留供 Host 追踪。该回合一个内部代理误按仓库通用命令跑了 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/console`，覆盖 8 个文件、61 项测试，退出 1；私有测试根已收尾。发现后停止使用 uv，后续使用任务指定解释器并清空继承的运行时/凭据环境。检出中存在 `.venv`，没有运行前的存在性记录，不能确认这一次 uv 是否创建或改动了它。没有向任务给定解释器环境安装包。

同一代理最初未设置浏览器下载目录，Chromium 下载向默认的 `~/Library/Caches/ms-playwright` 缓存写入，在约 50% 时中断；没有检查或删除该外部缓存。随后改用检出内 `apps/console/node_modules/.cache/ms-playwright` 安装并运行浏览器回归。这两项偏差不计为源码验证的通过证据；前端与 Python 的最终结果以规定命令的最后一次输出为准。

第一续做回合前端代理的 npm 命令未覆盖 npm 缓存，沿用本机配置，未核实其确切目录；没有检查或删除外部 npm 缓存。主代理随后的一次额外前端测试使用了默认 Node 25.8.1，虽也通过 1,098 项，但不作为支持版本的最终证据；最后已在 Node 24.21.0 上重跑全部前端测试并重建资产，两者退出 0，且为这两次命令显式设置检出内 `apps/console/node_modules/.cache/npm` 缓存。三个续做回合没有使用 uv，也没有向指定解释器环境安装包。
