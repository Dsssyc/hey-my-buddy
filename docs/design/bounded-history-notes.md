# 命令身份与周期状态替换的实现记录

2026-10-16，基于 `c605b127`，按开发者本日定下的两条规则实现；本记录是 Worker 的实现与验证证据，待 Host 验收。只修改分配检出内授权的源码、测试和两份设计记录；未操作日常状态/服务/Worker、账户、原生会话或用户 harness 配置，未安装到日常环境，未调用付费模型。方案已重写为 [最终做法](bounded-history-design.md)，原方案中的期限及附加响应层已经移除。

## 实现

新增 `src/hey_my_buddy/blackboard/tasks/command_receipts.py`。十种 workflow 命令的写入只保存固定的提交身份，重放从对应版本/计划/整合/结论/交付物与当前微任务表重建回答；普通小回答保持原样。`BoardStore._receipt` 继续先比 kind/request_hash/subject，再调用重建；原 workflow 各入口的控制权、代次、revision 和目标检查未替换。takeover 的颁发代次保留，控制 token 仍从该代次推导，并经过原控制能力的专门重放授权。局部 workspace resolution 的旧结果不能从可更新的 conflict 行重建，保留该次 action/state/paths/fingerprint/交付身份，避免下一次恢复改写第一次提交的事实。

`execution.result` 删除统一命令收据的查写路径，attempts 的 result_json/result_command_id、同值重投、代次与 nonce、停止证据补充和事务式结算继续保留。已提交结果丢了磁盘确认后重投仍得到 committed 确认；异值与旧代由原权威结果规则拒绝。相关恢复测试改为核对 attempt 中的命令身份及没有多余 commands 行。

事件写入口在追加新行后、同一事务中替换旧状态：attempt.activity 按 attempt_id，harness.checked/harness.probe_unknown/harness.account_observed 按 kind 与 harness，只留最新。新事件继续使用 AUTOINCREMENT，activity_seq/at、head 和原唤醒路径照常前进。attempt_activity 的最新投影不变；重复或倒退活动不产生事件。提醒及领取记录、目录变化、连接/故障/恢复/停止事实、业务阶段和等待转移继续累计，没有新增维护线程或启动维护。

新增 `blackboard/store/upgrade.py` 的唯一 23→24 离线事务。安装器接受基点 23 与当前 24，在旧服务停止和独占锁之后先按 Path 备份原库，再转换 workflow 收据、删除 execution.result 旧收据并折叠周期状态，最后提升 schema；harness.prepare 不变，不解析 subject。转换后的完整快照作为新服务启动后的 expected，未放宽对 commands/events 指纹的校验。备份验证明确支持这一个基点和当前 schema，回滚保留原 23 库。基点夹具仍是 0.40.0/`9baa0524`，未重新生成或改标。

源码契约与协议签名 fixture 已改到 0.42.0，SCHEMA_VERSION 为 24。C-Two 方法签名没有变化，所以重算签名摘要仍为 `f4ae9329eb636fd604a14051963b575f42d325c0de408108dcd57639932740a5`，只更新其绑定的 contractVersion。软件发行版本来自 `pyproject.toml`，该文件与 `uv.lock` 不在本回合冻结的 writeScope 中，均未修改，现仍是 0.41.0；因此本次交付尚未完成软件版本一致性，不能视作可安装的完整 0.42.0 候选。需要 Host 补这两份元数据或扩大文件范围后继续。

## 路径引用与独立复核

按当前源码逐项核对视图中路径的权威来源：微任务与旧回合的 manifest 在 workflow_runs、workflow_continuations、workflow_children、workflow_artifacts，物理路径和 checkout 身份还在 workspace_reservations；执行结果/日志在 attempts/artifacts；整合目标与比较证据在 workflow_integrations；计划中的 path/evidence/retention/result 在 workspace_cleanup_plans；冲突与授权记录在 workflow_workspace_conflicts/workflow_scope_versions。完整字段表见方案。存储清理的引用扫描与删除守卫没有改动。

另由一个只读原生子代理独立复核当前源码，未发现剥离快照后丢失真实 allocation 的结构性引用，也未发现提交身份或权限回归。其核对覆盖 helper 目标、continuation/旧输入 manifest、局部恢复后再次解决、takeover 原代授权及计划应用后的重放；没有把旧审查记录当作当前实现证据，未运行服务或测试。主代理另补升级回归测试，直接通过删除路径所用的 allocation_references 比较转换前后权威引用保护，去掉 commands/events 的重复引用后保护集合仍相同。Host 最终复核仍未进行。

## 检查

以下均从仓库根经 `uv run --frozen python -m hey_my_buddy.cli.checks` 运行，只指定受影响文件，默认 4 并行。每次启动前用 env -u 清除 HMB_STATE_DIR、HMB_RUNTIME_ROOT、HMB_RUNTIME、HMB_RUNTIME_IDENTITY、HMB_AGENT_CREDENTIAL_FILE、HMB_AGENT_CREDENTIAL_FD、VIRTUAL_ENV、UV_PROJECT_ENVIRONMENT；运行器继续为每个测试文件分配私有根。没有直接启动测试子进程，没有运行完整检查。原始输出仅在忽略的 tmp/ 下，公开记录不包含机器主目录或私有测试根的绝对路径。

| 检查组 | 实际结果 | 本地证据 |
| --- | --- | --- |
| protocol/test_activity、store/test_interruption_recovery、service/test_reminders、service/test_harness_health、store/test_store，另含 protocol/test_contract_version | 前五个文件的 110 项通过；契约签名 fixture 校验通过，但软件版本与契约版本一致性一项失败，0.42.0 与未获范围的 0.41.0 不一致；运行器退出 1，私有根已清理 | tmp/bounded-history-core-check.log |
| 21 个工作流、工作区、Lead、store、service、protocol 与安装升级文件 | 343 项通过、退出 0、私有根已清理 | tmp/bounded-history-integration-check.log |
| 新命令收据测试及新 allocation 引用测试的局部复跑 | 命令收据 6 项通过；新引用测试最初遗漏私有辅助函数必填 request_id 参数，补为 None 后只重跑该升级文件，未改产品守卫 | tmp/bounded-history-final-check.log |
| 最终 upgrade fixture 文件 | 9 项通过、退出 0、私有根已清理；含全表规范化存量/新库结构比较、周期事件折叠、事务异常回滚、命令指纹篡改拒绝及权威 allocation 引用保护 | tmp/bounded-history-reference-check.log |
| 最终命令收据文件 | 6 项通过、退出 0、私有根已清理；小回答测试使用真实 task.submit 收据，在取消后重放并拒绝不同请求 | tmp/bounded-history-receipt-check.log |

21 文件组包括 blackboard/tasks 的 test_command_receipts、test_workflow、test_workspace_lifecycle、test_scope_recovery、test_storage、test_host_workflow、test_lead_lifecycle、test_lead_review_boundaries、test_workflow_boundaries、test_workflow_cancellation；blackboard/store 的 test_upgrade_fixture、test_result_shutdown_proof；install 的 test_upgrade、test_upgrade_archive、test_upgrade_service、test_upgrade_host_cutover、test_upgrade_waiting_host、test_install_without_upgrade；blackboard/service 的 test_harness_startup、test_service；protocol 的 test_ctwo_service。包含真实私有服务与安装切换的测试，全部由运行器管理，最终源码没有前端改动，因此没有运行 npm 或浏览器测试。

新增行为验证包括：收据来自真实生产写入、当前视图已前进时仍对应同一个提交身份；计划从 planned 到 applied 后读取同一计划的当前事实；局部冲突的第一次恢复在第二次恢复后仍重放自己的剩余路径/指纹；活动替换和投影在失败事务中一起回滚、旧游标从删掉的位置继续、不同执行代次互不替换；健康/账户状态按 harness 隔离且序号前进。原有工作流测试继续覆盖冲突、无权与失效代次拒绝；提醒测试覆盖累计整数倍和不同静默起点的持久单次领取。

收尾还核对 git diff --check、20 个调查 kind 的对应处理、设计记录每段一行以及没有机器绝对主目录。实际阻塞只有发行版本元数据的冻结范围缺口；没有产品取舍待开发者决定。需要 Host 补齐 pyproject.toml/uv.lock 的 0.42.0 一致性并通过 test_contract_version，更新两份 README 和参考文档，再按既定流程合并后跑完整检查与最终验收。
