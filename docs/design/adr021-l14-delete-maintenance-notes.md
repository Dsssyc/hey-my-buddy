# ADR-021 L14：提前删除 Host 维护评价

2026-10-13，在 `e9c1988f` 的独立检出上删除。依据 ADR-021 第 9、15 条及改名计划的第 3、4 个微任务；本记录供 Host 检查源码与安排后续工作，不代表安装或验收。待升级走归档旧板、新建板并带走设置；本次直接修改新板定义，没有工作历史换算，也没有修改升级基点夹具、安装代码、版本号或名称。

## 操作和许可

以下 9 个公开操作及其 CLI、C-Two `BuddyControl` 方法、transport 映射、客户端封装、服务处理、帮助与控制台入口已删除：`evaluation-write-begin` / `evaluation_write_begin`、`evaluation-write-renew` / `evaluation_write_renew`、`evaluation-write-abort` / `evaluation_write_abort`、`evaluation-reader-begin` / `evaluation_reader_begin`、`evaluation-reader-release` / `evaluation_reader_release`、`evaluation-evidence-record` / `evaluation_evidence_record`、`evaluation-prepare` / `evaluation_prepare`、`evaluation-history` / `evaluation_history`、`assessment-publish` / `assessment_publish`。没有留下兼容别名。

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

控制台删除评价卡片、样本、证据、维护详情、更新记录页面及其入口；快照、模型分页、解析、预览、测试夹具和样式随之清理。保留模型配置、备注、偏好、并发、Router 设置和选择记录。删除维护写者的保存、排队、续租、取消与租约恢复逻辑，设置直接按版本保存；未知回复仍按同一命令重试。读缓存删除写者/读者维护闸门的租约边界，快照不再显示该闸门。

`docs/reference/evaluation-maintenance.md` 删除；`docs/reference/evaluation.md` 保留模型、用户设置、控制台与路由的内容，并删除卡片和维护说明。`docs/README.md` 删除维护参考页入口并纠正 ADR-008 仍现行的陈述。共享 skill 删除模型卡片更新那一句；skill 构建按存在的参考页收集文件，删除页面后不再进入构建清单。纯维护测试 `blackboard/evaluation/test_external_evaluation.py`、`blackboard/routing/test_evaluation_routing.py` 和 `console/test_console_gate_deadlines.py` 删除；卡片/证据/维护写者的专用用例删除，其他测试迁移为直接设置发布与当前输入。

## 留给下一个微任务的版本闸门

`blackboard/store/db.py` 的 `evaluation_state.table_revision`、`configuration_revision` 和仍现存的旧名字不变，`migrations.py` 带走两种版本及所有既定设置的行为不变。`evaluation_revisions`、profile 的 created/updated revision、偏好/备注的更新版本继续存在。

`evaluation.user_policy_publish` 仍以 `expectedRevision` 检查当前表版本并在发布时增加表版本；`user_policy.publish` 更新 Router 设置时仍增加设置版本；`catalog_store.record` 在模型目录改变时仍推进表版本。控制台仍保存草稿的基准表版本并处理版本冲突。

`routing/decision.py` 的请求冻结表与设置版本、claim 前的变更检查、`_frozen_circumstances_problem`、发布前的表/设置版本检查、`_reader_open`、`stale` 结算与读者释放、`_mark_running` 的预期版本绑定原样保留。Router 更换、合法候选/任务输入改变和读者到期的停路仍成立；删除的是已经不存在的维护写者占用阻塞。

`evaluation_readers` 及其索引、`decision_requests.reader_id` 与 `expected_revision` 仍保留。Router 在自己的 claim 事务中入场，运行中续租，结算时释放；公开 Host 读者入口已删除。相关路由、工作流、Router 健康/历史与控制台版本字段未改名。这些地方需下一个微任务统一去掉版本闸门，不能仅凭本次卡片删除推断已经去掉。

## 验证和边界

验证结果待本轮最终检查完成后填入。所有 Python 测试只通过指定检查器并清空继承的运行时与凭据环境；前端依赖、测试与构建只在本检出运行。不运行完整 Python 检查、不安装产品、不读写日常看板、不启动或停止日常服务，不做真实 harness 或付费模型调用。

`packaging/generate-upgrade-fixture.py` 保留旧操作：它固定从发布基点源码生成 schema 18 的 stock，测试不执行它，不能把固定基点改成新板。`tests/fixtures` 原样保留。允许范围之外的 README 与现行 CLI/console 参考页仍有旧维护描述或链接，交由 Host 同步；本次没有修改它们。版本与安装由其所属微任务处理。

## 执行偏差

一个内部代理误按仓库通用命令跑了 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/console`，覆盖 8 个文件、61 项测试，退出 1；私有测试根已收尾。发现后停止使用 uv，后续使用任务指定解释器并清空继承的运行时/凭据环境。检出中存在 `.venv`，没有运行前的存在性记录，不能确认这一次 uv 是否创建或改动了它。没有向任务给定解释器环境安装包。

同一代理最初未设置浏览器下载目录，Chromium 下载向默认的 `~/Library/Caches/ms-playwright` 缓存写入，在约 50% 时中断；没有检查或删除该外部缓存。随后改用检出内 `apps/console/node_modules/.cache/ms-playwright` 安装并运行浏览器回归。这两项偏差不计为源码验证的通过证据；前端与 Python 的最终结果以规定命令的最后一次输出为准。
