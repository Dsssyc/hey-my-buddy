# ADR-021 L14：提前删除 Host 维护评价

2026-10-13，在 `e9c1988f` 的独立检出上删除，随后从未验证的部分产物 `63306ec5` 续做。依据 ADR-021 第 9、15 条及改名计划的第 3、4 个微任务；本记录供 Host 检查源码与安排后续工作，不代表安装或验收。待升级走归档旧板、新建板并带走设置；本次直接修改新板定义，没有工作历史换算，也没有修改升级基点夹具、安装代码、版本号或名称。

## 操作和许可

以下 9 个公开操作及其 CLI、C-Two `BuddyControl` 方法、transport 映射、客户端封装、服务处理、帮助与控制台入口已删除：`evaluation-write-begin` / `evaluation_write_begin`、`evaluation-write-renew` / `evaluation_write_renew`、`evaluation-write-abort` / `evaluation_write_abort`、`evaluation-reader-begin` / `evaluation_reader_begin`、`evaluation-reader-release` / `evaluation_reader_release`、`evaluation-evidence-record` / `evaluation_evidence_record`、`evaluation-prepare` / `evaluation_prepare`、`evaluation-history` / `evaluation_history`、`assessment-publish` / `assessment_publish`。没有留下兼容别名。

另删除 `BoardClient.evaluation_write_publish`：它只封装已经不受服务支持的 `evaluation_write_publish`，并非本次之前仍可调用的第 10 个公开操作。客户端关于维护写者、读者与证据的便捷方法随所属操作删除。

`model-profiles` 与 `console-snapshot` 保留为模型、设置和路由记录的读法，但不再返回卡片、证据、样本、待处理证据或维护状态。`selection-get` 与 `selection-list` 保留工作面路由记录，删除维护分支及其发布材料。公开 `evaluation-reader-*` 被删，Router 内部的选择读者继续由路由生命周期持有。

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

## 留给下一个微任务的版本闸门

`blackboard/store/db.py` 的 `evaluation_state.table_revision`、`configuration_revision` 和仍现存的旧名字不变，`migrations.py` 带走两种版本及所有既定设置的行为不变。`evaluation_revisions`、profile 的 created/updated revision、偏好/备注的更新版本继续存在。

`evaluation.user_policy_publish` 仍以 `expectedRevision` 检查当前表版本并在发布时增加表版本；`user_policy.publish` 更新 Router 设置时仍增加设置版本；`catalog_store.record` 在模型目录改变时仍推进表版本。控制台仍保存草稿的基准表版本并处理版本冲突。

`routing/decision.py` 的请求冻结表与设置版本、claim 前的变更检查、`_frozen_circumstances_problem`、发布前的表/设置版本检查、`_reader_open`、`stale` 结算与读者释放、`_mark_running` 的预期版本绑定原样保留。Router 更换、合法候选/任务输入改变和读者到期的停路仍成立；删除的是已经不存在的维护写者占用阻塞。

`evaluation_readers` 及其索引、`decision_requests.reader_id` 与 `expected_revision` 仍保留。Router 在自己的 claim 事务中入场，运行中续租，结算时释放；公开 Host 读者入口已删除。相关路由、工作流、Router 健康/历史与控制台版本字段未改名。这些地方需下一个微任务统一去掉版本闸门，不能仅凭本次卡片删除推断已经去掉。

## 验证和边界

本轮在最终删除源码上运行指定的五个 Python 目录：`tests/python/blackboard`、`tests/python/buddy/roles`、`tests/python/console`、`tests/python/cli`、`tests/python/protocol`。检查器使用任务指定解释器及 `PYTHONPATH=src`，清除继承的 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`、`BUDDY_RUNTIME`、`BUDDY_RUNTIME_IDENTITY`、`BUDDY_WORKER_STATE`、`BUDDY_WORKER_ID`、`BUDDY_AGENT_CREDENTIAL`、`BUDDY_AGENT_CREDENTIAL_FILE`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`。共调度 146 个文件，138 个文件通过，检查器汇总这批通过文件为 2,105 项、0 跳过；8 个文件失败，最终退出 1，私有根已删除。这不是全量 Python 检查，也没有任何安装、日常看板或真实 harness / 付费模型调用。

8 个失败中的 `blackboard/catalog/test_catalog_trust.py` 引用了基点里已经不存在的 `blackboard.store.test_migrations.rows`。本轮将该用例改为直接比较私有数据库全部当前表的行，保留目录发布失败时整个事务回滚的断言；最后单独通过同一检查器重跑该文件，16 项通过，退出 0，私有根已删除。后端审查还单独运行了 `blackboard/evaluation/test_evaluation.py` 与 `blackboard/routing/test_decision.py`，52 项通过、退出 0；二者也在上述目录检查中通过。

剩余 7 个文件受同一个基点夹具缺失影响：`blackboard/tasks/test_workspace_lifecycle.py`、`blackboard/tasks/test_host_workflow.py`、`blackboard/tasks/test_permission_levels.py`、`blackboard/tasks/test_workspace_api.py`、`blackboard/tasks/test_workflow_preparation.py`、`cli/test_cli_views.py`、`cli/test_checks_parallel.py`。`test_workspace_lifecycle.py` 的类定义引用不存在的 `WorkspaceIdentityFixture.snapshot`，同一处还引用缺失的 `migrate`、`all_rows`、`historical`；`test_workflow_preparation.py` 的 5 个历史身份准备场景调用缺失的 `historical` 或 `migrate`。`test_workspace_identity.py`、这两份测试及其缺失引用在 `e9c1988f` 与输入 `63306ec5` 中已是同样形式，删除维护的差异没有修改它们。单独重跑 `test_workspace_lifecycle.py` 也在加载类时以相同 AttributeError 退出 1，私有根已删除。这些测试在要求旧历史保留的迁移助手，须由 Host 与安装/升级微任务确定当前测试契约；本轮未恢复旧换算、未删除这些非维护用例，也未跳过失败文件，因此“全部规定测试通过”尚未达成。

前端使用支持的 Node 24.21.0：`npm --prefix apps/console ci` 退出 0；最终 `npm --prefix apps/console test` 为 78 个文件、1,098 项通过、退出 0；随后 `npm --prefix apps/console run build` 退出 0。最终打包文件为 `assets/index-BR2BiGHz.js`（772.41 kB）与 `assets/index-BN6Y1rxs.css`（114.81 kB），`index.html` 指向这两个文件；旧 JS 被构建替换。构建只有现存的大于 500 kB 分块提示，没有编译错误。

真实浏览器在同一打包资产的合成预览上运行 `apps/console/browser/*.check.ts`，96 项通过、退出 0。仓库的 `test:browser` 默认用 uv 启动预览，因此本轮使用检出内临时 Playwright 配置，将预览启动命令改为指定解释器并清除继承的运行时/凭据变量；配置已删除。Chromium 只使用检出内 `apps/console/node_modules/.cache/ms-playwright`。浏览器回归验证绘制行为；下述范围外预览的旧数据字段仍须清理，不能将该通过结果当作预览契约已迁移的证据。`git diff --check` 通过；安装代码、安装测试与固定 `tests/fixtures` 没有差异。

`packaging/generate-upgrade-fixture.py` 保留旧操作：它固定从发布基点源码生成 schema 18 的 stock，测试不执行它，不能把固定基点改成新板。`tests/fixtures` 原样保留。允许范围之外的 README 与现行 CLI/console 参考页仍有旧维护描述或链接，交由 Host 同步；本次没有修改它们。版本与安装由其所属微任务处理。

范围外还发现合成预览 `tests/probes/objective_console_preview.py` 保留旧契约：`ALLOWED_OPERATIONS` 仍列 `evaluation_history`、`evaluation_write_begin`、`evaluation_write_renew`、`evaluation_write_abort`；`READ_OPERATIONS` 仍列 `evaluation_history`；快照仍返回 `gate`、`cards`、`evidence`、`pendingEvidence`、`sampleCounts` 与维护能力，模型分页仍返回卡片/样本，路由详情仍带维护发布字段，帮助仍介绍旧读法。该文件不在本微任务写入范围，未修改，需 Host 删除这些旧操作、字段与专用样本夹具，保持微任务结果及原生执行证据。它在浏览器回归中额外提供旧字段，测试通过不能证明预览夹具已经遵守新契约。

Host 另需同步的范围外现行文档：`README.md` 与 `README.zh-CN.md` 的维护说明；`docs/reference/cli.md` 的维护操作与写者字段；`docs/reference/decision.md` 的 Host 维护段；`docs/reference/architecture.md` 的写者排队理由与维护契约；`docs/reference/console.md` 的维护/写者描述。`docs/design/console-frontend.md` 中“evaluation-history 不受影响”的记录也已过时。历史 ADR 与旧验收记录可作为历史证据保留。

## 执行偏差

以下两项来自上一个留下 `63306ec5` 的执行回合，保留供 Host 追踪。该回合一个内部代理误按仓库通用命令跑了 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/console`，覆盖 8 个文件、61 项测试，退出 1；私有测试根已收尾。发现后停止使用 uv，后续使用任务指定解释器并清空继承的运行时/凭据环境。检出中存在 `.venv`，没有运行前的存在性记录，不能确认这一次 uv 是否创建或改动了它。没有向任务给定解释器环境安装包。

同一代理最初未设置浏览器下载目录，Chromium 下载向默认的 `~/Library/Caches/ms-playwright` 缓存写入，在约 50% 时中断；没有检查或删除该外部缓存。随后改用检出内 `apps/console/node_modules/.cache/ms-playwright` 安装并运行浏览器回归。这两项偏差不计为源码验证的通过证据；前端与 Python 的最终结果以规定命令的最后一次输出为准。

本轮前端代理的 npm 命令未覆盖 npm 缓存，沿用本机配置，未核实其确切目录；没有检查或删除外部 npm 缓存。主代理随后的一次额外前端测试使用了默认 Node 25.8.1，虽也通过 1,098 项，但不作为支持版本的最终证据；最后已在 Node 24.21.0 上重跑全部前端测试并重建资产，两者退出 0，且为这两次命令显式设置检出内 `apps/console/node_modules/.cache/npm` 缓存。本轮没有使用 uv，也没有向指定解释器环境安装包。
