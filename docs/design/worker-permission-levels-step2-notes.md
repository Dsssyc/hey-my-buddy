# Worker 权限档位第 2 步说明

2026-10-13，在输入提交 `98cb1536` 的受管检出实现 Codex 的三档原生策略。本文记录 Worker 的实现与本环境验证；完整检查、真实 Codex 回合、安装和最终验收由 Host 负责。

## 实际交给原生的参数

`codex/native_run.py::_worker_sandbox` 从本次 `RunRequest.worker_permission.level` 一次映射出线程的 `sandbox` 与回合的 `sandboxPolicy`；`_configure_worker` 和 `_admit_turn` 使用同一映射。三档的审批策略都是 `approvalPolicy: "never"`。

| 权限档位 | thread/start 或 thread/resume 的 sandbox | turn/start 的 sandboxPolicy |
| --- | --- | --- |
| 只读 / read | `read-only` | `{"type":"readOnly","networkAccess":true}` |
| 写检出 / write | `workspace-write` | `{"type":"workspaceWrite","writableRoots":["<本次检出>"],"networkAccess":true}` |
| 放开 / full | `danger-full-access` | `{"type":"dangerFullAccess"}` |

写检出只显式给本次检出的可写根，平台临时目录由 Codex 的原生策略提供，没有另加可写位置。只读与写检出的网络显式放开；完全访问使用原生的 dangerFullAccess，不加额外限制。权限映射不读取或修改用户配置；已有初始化、账户和模型发现行为没有因档位而增加新的配置读取，也没有为任何档位创建配置主目录。

`_mode` 先判断是否带 Worker permission；带着档位的请求始终进入 Worker 载体，因此只读 Worker 不会进入 Router 审阅，full 也有同一条载体。没有 Worker permission 的 none/read 请求继续使用原来的快速或审阅载体；没有档位却请求 write/full 属于自身调用方参数错误，入口以 INVALID_ARGUMENT 拒绝。原有 Codex full 未实现的拒绝已删除；另外三个 harness 的 full 声明缺失和原生入口拒绝保持原样。

Router 的 `_configure_fast`、`_configure_review` 和 `_admit_turn` 的审阅分支没有改。另一个微任务负责的 `_FastProtocol`、`run_discovery`、`native_evidence`、`_evidence_refs` 及 `_build_result` 的 native_evidence 填充没有改。既有探针 `tests/probes/codex_permission_levels.py` 与 `permission_level_things.py` 已覆盖三种目标策略，本步没有另建探针。

## 续接与回合事实

线程续接的绑定只核对任务、会话、检出、模型配置和上一回合的身份；权限档位不属于绑定，也没有新增或放松这些核对。下一回合的 `_configure_worker` 从新请求取得 sandbox，发给 thread/resume；随后 `_admit_turn` 从同一请求取得 sandboxPolicy，发给 turn/start，所以同一线程里的新档位从新回合开始生效。旧回合的请求与证据保持自己原来的档位。

`state.worker_policy` 保存本次 turn/start 参数中的 sandboxPolicy 和 approvalPolicy。保存位置移到原生调用前，使原生拒绝这次 turn/start 时也能保留已请求的策略；它仍是 requested 事实，不表示原生已确认执行。控制器已有的 workerPermission 证据记录本次请求携带的声明；Codex 的 read、write、full 现在分别是 read/enforced、write/enforced、full/enforced，与本次送出的策略对应，不从上一回合或微任务当前值反推。

新增测试通过真实的离线 mock App Server 检查三档的线程启动参数、回合参数、effective_policy、Codex 的 nativePolicy 投影和控制器的 workerPermission。续接覆盖 read→read、write→write、full→full、read→write、write→full，断言线程身份复用、第二回合的 thread/resume 与 turn/start 参数以及策略事实；原生回合拒绝测试核对失败时仍保留实际请求的策略。mock_codex.py 仅新增续接参数记录，没有模拟或认证系统沙盒的执行效果。

## 声明只保留一处与升级

`buddy/harnesses/registry.py::_WORKER_PERMISSIONS` 是四个 harness 的唯一档位声明。新增 `worker_permission_capabilities` 从它生成 permission 字符串；四个 adapter 的类只保留其他能力，实例构造时附上生成的 permission 字符串。目录的 `catalog.permission_capabilities` 也直接调用同一生成函数，不再从另一份手写权限字符串读取。目录写入 buddy 时随之包含 Codex 的 permission:full；Claude、DSH、ZCode 的能力字符串逐项保持原值，Codex 只新增 permission:full。

删掉了四个 adapter 中各自手写的 permission:read/permission:write、Codex read 的 write/convention 声明、Codex 原生入口的 full 未实现拒绝和测试中 Codex 必须拒绝 full 的旧断言。角色请求夹具按新声明取得 Codex read 的原生只读范围；针对不支持 full 的三个 harness 的能力拒绝与原生拒绝测试保留。路由手工建立的 buddy 夹具补上所属 harness 当前声明的能力字符串，刻意缺能力的负面用例未放松。

既有唯一的 17→18 升级已经通过 catalog.permission_capabilities 读取当前声明，本步不需要修改升级源文件、schema 版本或样板基准。迁移测试确认历史 Codex buddy 获得 permission:full，其他字段、启用与可用状态保持；升级后的 buddy 可进入 full 候选，按各 harness 的当前声明过滤。`Schema17UpgradeTests.test_migration_has_the_complete_fresh_schema` 继续把升级样板与新建看板的完整 schema 比对；库存保存断言继续核对历史数据和计划内的转换。

## 逐文件验证

每个测试文件使用 Host 指定的现成只读解释器，分别执行 `python -m unittest <单个模块>`；没有安装依赖或使用 uv。子进程使用检查运行器的 child_environment：本检出的 src 与 tests/python 排在前面，清掉继承的运行身份、Worker 与凭据变量，每个文件使用自己的 HOME、harness 配置、状态、运行与临时目录。测试只启动离线夹具，没有真实模型调用、账户登录、日常看板访问或真实服务与 Worker 的启动、停止、重启。

最终去重结果为 51 个文件、923 项测试，其中 922 项通过，1 项因当前沙盒的 ps 进程枚举限制未通过。33 个 routing 文件由独立内部检查执行，每个文件一个进程；其余 18 个文件的最终逐文件日志保存在本检出的 ignored tmp，迁移、升级样板与目录文件另由主执行者重新运行确认。测试摘要在 `tmp/step2-test-summary.json`；这些本地材料不作为已接受的公共验收记录。

| 模块或目录 | 项数 | 最终结果 |
| --- | --- | --- |
| buddy.harnesses.codex.test_native_run | 96 | 通过 |
| buddy.harnesses.test_run_contract | 39 | 通过 |
| buddy.roles.test_schema_worker | 13 | 通过 |
| buddy.roles.test_permission_evidence | 3 | 通过 |
| buddy.harnesses.codex.test_batch4_facts | 16 | 通过 |
| buddy.harnesses.codex.test_codex_protocol | 22 | 通过 |
| buddy.harnesses.codex.test_codex_tool_evidence | 24 | 通过 |
| buddy.roles.test_run_execution | 18 | 通过 |
| buddy.roles.test_run_native_evidence | 9 | 通过 |
| buddy.harnesses.codex.test_codex | 36 | 通过 |
| buddy.harnesses.codex.test_codex_continuation | 7 | 通过 |
| buddy.roles.test_registered_run_wiring | 21 | 通过 |
| blackboard.tasks.test_permission_levels | 8 | 通过 |
| blackboard.evaluation.test_evaluation | 39 | 通过 |
| blackboard.service.test_harness_service | 12 | 通过 |
| blackboard.store.test_migrations | 12 | 通过 |
| blackboard.store.test_upgrade_fixture | 3 | 通过 |
| blackboard.catalog.test_catalog | 14 | 通过 |
| tests/python/blackboard/routing 全部 33 个 test 文件 | 531 | 530 项通过，1 项环境失败 |

环境失败的完整测试 ID 是 `blackboard.routing.test_readonly_tools_probe.ReadonlyToolsProbeTests.test_only_one_execution_is_possible_for_the_prepared_packet`。该用例使用离线模拟执行，但探针的检查收尾需要枚举进程；当前沙盒的 ps 失败，得到 `ps process enumeration failed; residue status is unknown`，从而把探针结果标为 failed。保留的 teardown 证据已复制到 `tmp/step2-readonly-probe-teardown.json`；没有绕过沙盒或改动收尾检查，Host 需在能枚举进程的环境补跑此项。初次新增控制器测试曾误把 nativePolicy 当成角色层 workerPermission 的同层字段，两次修正断言后完整 native_run 文件 96 项通过；没有为此更改角色层或原生投影。

本环境未运行完整检查、需要真实启动器或真实服务的验证、真实 harness 探针、付费模型回合、安装、故障注入和 Windows/Linux 验证；这些属于 Host 的整合验收。前端未修改，没有运行前端检查。`git diff --check` 已通过。

## Host 的真实 Codex 核对

既有无模型探针通过 command/exec 测量具体文件、网络、本机通信与进程能力，没有线程或模型回合，因此不能证明 thread/resume 的换档真实生效。Host 需在真实 Codex 上分别运行三档 Worker 回合，并用同一会话核对 read→write、write→full 两条换档链路；检查真实线程与回合身份、thread/start 或 thread/resume 的 sandbox、turn/start 的 sandboxPolicy、回合 nativePolicy 和 workerPermission，以及未确认停止时保持 unknown 的证据。

真实回合应核对只读不能写入检出或临时目录而可联网，写检出可以写检出与临时目录并联网及绑定本机端口而不能写检出外，放开可以完成共享内存与列出进程等前两档不能完成的操作；只读的原生搜索与抓取是否可用也在这次真实核对中记录。每项结果以实际原生行为为准；本步的 mock 测试只证明参数、绑定和事实流正确。

Host 整合后还需运行完整检查，补跑上面的 readonly_tools_probe 环境失败项，并将私有看板副本的 17→18 升级、三档真实回合及续接换档结果写入正式验收记录。skill、参考文档与公共能力边界的更新按执行计划由 Host 完成；本次没有改动这些文件。
