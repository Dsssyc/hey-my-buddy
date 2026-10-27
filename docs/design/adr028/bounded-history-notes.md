# 命令身份与周期状态替换的实现记录

2026-10-16，基于 `c605b127`，在上一轮输出 `9749b60b` 上按 Host 本轮核对的返修项完成最后一轮修改；本记录是 Worker 的源码与验证证据，待 Host 验收。最终设计见 [方案](bounded-history-design.md)。修改限于分配检出内授权的源码、测试、版本元数据和两份设计记录；未读写日常状态、服务、Worker、账户、原生会话或用户 harness 配置，未安装到日常环境，未做付费模型调用，未提交、改分支/标签或操作 stash。

## 最终实现

新增 `blackboard/tasks/command_receipts.py`，十种 workflow 命令只保存固定的提交身份，重放从对应版本、计划、整合、结论、交付物与当前微任务表重建回答；九种小回答保持原样。统一收据读取继续先比 kind/request_hash/subject，各入口的控制权、代次和目标授权继续生效。takeover 的颁发代次只存顶层一份，control 只保留 hostId，重放从该代次推导 token 并经过原控制能力验证。取消说明由提交和重放共用一个常量。局部 workspace resolution 保留不能从可更新 conflict 行重建的首操作事实，避免第二次恢复改写第一次提交的结果。

execution.result 以 attempt 为唯一提交身份，不再查写 commands，也不再接受 commandId 参数。先对生产源码做过读者核对：result_command_id 唯一显式读取只是 confirm_execution_shutdown 再写回，没有控制台、视图或其他业务读者；因此一并移除 attempts 的该列、_commit_execution_result 的参数、执行进程生成的 result 编号、磁盘回执的 commandId 字段及投递/恢复传递编号的代码。权威 result_json、代次与 nonce 校验、结果同值重投、停止证据补充和事务式结算保留；旧编号参数由当前输入边界拒绝，不兼容旧回执形式。

周期状态在同一写事务中替换：attempt.activity 每 attempt 一条，三类 harness 健康/未知探测/账户观察按 kind 与 harness 各一条。新 seq 使用 AUTOINCREMENT，最新投影、activity_seq/at 与 head 同时前进，失败事务一起回滚。活动删除附带已知 task_id，harness 删除附带 task_id IS NULL；生产 SQL 的 EXPLAIN QUERY PLAN 回归检查要求四条删除都使用已有 events_task_idx，没有新索引。提醒与领取、目录变化、业务阶段/等待转移及故障/恢复/停止事实继续累计，不引入维护线程或启动维护。

23→24 的唯一待定升级先离线备份原库，再在一个事务里转换 workflow 回答、删除 execution.result 旧收据及 attempts.result_command_id 列、折叠周期事件，最后改 schema marker。commands 候选先 fetchall，再更新；编码使用共享 json_codec.canonical_json，哪些种类原样保留只由 stored_response 决定，不再预过滤或检查代码常量。backup.create 只收 Path，生产与测试调用统一。基点样板仍为 0.40.0/9baa0524/schema 23，没有重生成或改标。转换后的全表快照参与安装校验，失败事务回滚，原 23 备份可供原运行时恢复。

安装校验继续依赖原有 upgrade.json 屏障：harness 状态生产入口在标记存在时不写，改契约安装要求没有在途执行，因此校验期间不会用新状态替换 eventHead 以内的旧行。没有启动时清理，也没有对 commands/events 指纹设置豁免。方案已记录这个生产入口约束。

本轮 scopeVersion 2 已授权 pyproject.toml 与 uv.lock。软件版本、CONTRACT_VERSION 和签名 fixture 现均为 0.42.0，SCHEMA_VERSION 为 24；按要求运行 uv lock，锁文件只改本项目一项版本，依赖集合不变。C-Two 方法签名未变化，签名摘要仍为 f4ae9329eb636fd604a14051963b575f42d325c0de408108dcd57639932740a5。test_current_core 的契约断言已更新，新 schema 无旧列；meta 写入清单加入唯一升级中合法的 schema marker 写入，仍守住只有三个单值。

## 路径引用与审查边界

逐项核对的结构性路径来源仍在 workflow_runs、workflow_continuations、workflow_children、workflow_artifacts、workspace_reservations、attempts/artifacts、workflow_integrations、workspace_cleanup_plans 与冲突/授权记录中，完整映射见方案。存储清理的引用扫描及删除守卫未改。上一轮只读原生子代理对当前源码独立复核，未发现剥离视图导致真实 allocation 引用保护丢失；升级回归测试直接使用删除路径的 allocation_references 核对权威保护集合。

本轮读取 `0fc3f2a2` 的独立实现审查作为线索，并逐项按代码确认 Host 列出的返修范围。每种 workflow 共用同一个回答构造器及 artifact 复用下的字段同源问题由 Host 记为后续，本轮没有扩大成全面重构；取消说明的单定义按本轮要求完成。两份 README、参考文档和 AGENTS 的升级状态更新由 Host 负责，本轮未修改这些文件。

## 验证

所有行为检查从仓库根经 `uv run --frozen python -m hey_my_buddy.cli.checks` 指定受影响文件运行，默认 4 并行；启动前 env -u 清除 HMB_STATE_DIR、HMB_RUNTIME_ROOT、HMB_RUNTIME、HMB_RUNTIME_IDENTITY、HMB_AGENT_CREDENTIAL_FILE、HMB_AGENT_CREDENTIAL_FD、VIRTUAL_ENV、UV_PROJECT_ENVIRONMENT，测试子进程及服务由运行器分配私有根和回收。没有手动起测试进程，没有运行完整检查，没有前端改动或 npm/浏览器检查。原始输出放在忽略的 tmp/，记录不包含机器主目录或私有根的绝对位置。

上一轮已记录的 21 文件 343 项，以及最终收据 6 项、升级样板 9 项局部检查均通过；那一轮的软件版本范围缺口已在本轮补齐。Host 在上一轮输出补版本后跑完整检查，指出 current_core 的两个断言、README 版本和未安装控制台依赖导致的失败；本轮只处理授权的 current_core，README 和控制台依赖仍按 Host 的安排处理，不把旧完整检查描述为通过。

| 本轮检查 | 实际结果 | 本地证据 |
| --- | --- | --- |
| 22 个受影响文件 | 270 项通过、退出 0，运行器确认私有根已清理；四条生产删除语句的 EXPLAIN 检查均使用 events_task_idx | tmp/bounded-history-revision-check.log |

22 文件为 blackboard/store 的 test_current_core、test_interruption_recovery、test_result_shutdown_proof、test_blackboard、test_store、test_upgrade_fixture；protocol 的 test_contract_version、test_activity、test_ctwo_service；install 的 test_backup、test_backup_windows、test_upgrade、test_upgrade_archive、test_upgrade_service、test_execution_install_fence；buddy/runtime 的 test_execution_delivery、test_execution_process；blackboard/tasks 的 test_command_receipts、test_scope_recovery；blackboard/service 的 test_execution_recovery、test_execution_scheduler、test_service。

新增/调整的检查覆盖：当前 schema 无结果命令列、23 样板删列后与 fresh schema 及规范化业务行一致、升级失败回滚；结果无编号仍能在离线/丢确认后重投和补充停止证据，旧参数被拒绝；四条生产删除语句的查询计划走 events_task_idx；带解决冲突交付的 accept 同请求重放，resolutionDelivery 与首次回答一致；takeover 代次与原能力不变、取消说明不漂移；备份 Path 接口及版本/签名一致性。最终源码和记录完成后只交 Host 验收，完整检查留待 Host 合并后运行。
