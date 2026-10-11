# C-Two 路径与状态权限返修 3-B：沙箱验证与 Host 待补核

本记录绑定基线 `b772198d45525f7d2cff12474be4a4103c2e8115`（已包含 3-A 整合 `99cd2b06`），core 仍冻结 `12eb4fcd`。开始前读取公开状态计划的“第二次 Host 复核补充”及 3-A 验收。本份是可审查源补丁和受限验证记录，尚非微任务签收，更非整批 Host 通过。原 ZCode 供应方 429/1310、不可重试及两层停止的输入记录保留；本回合为获授权的同 run Codex 恢复，不改变路由偏好或自行重试供应方。

## 修补与边界

公开 `get_state_dir` 恢复 `expanduser().resolve()`，显式根优先于环境、环境优先于默认及客户端调用时选择语义不变。`rpc_config.resolve_state_dir` 仅接受显式参数或环境，缺根仍报 `PRIVATE_STATE_REQUIRED`；先拒绝内部原始 `..` 再规范化所选状态根。私有 guard 从状态边界检查自身及下层条目，不再从 filesystem anchor 拒绝用户选用的链接祖先；`state/ipc` 和内部链接依然拒绝，结构错误有具体 `details.path`。没有仅靠导出环境掩盖问题。

`_cold_start_preflight` 与 `Daemon.run` 各自在 RPC 配置前验证目录结构、mkdir 并 chmod 0700。只读 attach 路径仍不创建、不 chmod。preflight 只捕获 `OSError` 映射 `LAUNCH_ACCESS_DENIED`，不捕获并改写 `BoardError`；既有错误 code/message/details 保留。产品 preflight 不再读取 `BUDDY_CHECKS_TMPDIR`，短 socket probe 使用标准 tempfile/TMPDIR，并保留自己的正常收尾。没有新增失败码或公开 schema。

旧“0755 状态可用”证据仅说明 rpc_config 可以选域、创建私有 IPC，不证明普通服务冷启动已可用。本份本地单元证明所有者修正；实际冷启动与直接服务启动成功仍待 Host 补核。旧历史记录保持原样。

写入七个源/测试路径及本记录，共八个，均在九路径范围内；`test_daemon.py` 未改。client、role、runtime、registry、support、launcher、保护文档、数据库、console、harness 均未改。没有扩大队列、创建 Buddy 任务、修改 refs/stash 或 commit；没有产品模型调用、原生账户发现、凭据读取、日常安装或服务操作。共享文件接口缺口目前未发现；若 Host 补核发现夹具之外接缝，由 Host 统一整合。

## 私有根与依赖

```json
{"taskRoot":"/tmp/c073-b-cIzzzx","unconfirmedOwnedProcesses":[]}
```

其下保留私有 uv 环境/cache、空 HOME、TMPDIR/BUDDY_CHECKS_TMPDIR、独立基线归档、变异、日志及一次性材料。清除继承 BUDDY_/ANTHROPIC_/C2_、VIRTUAL_ENV、UV_PROJECT_ENVIRONMENT；最终运行环境仅保留进程基础变量和自己的私有根。`delegate-materials/provenance.json` 的 18 项 SHA-256/size 与文件核对，材料 uv.lock 与仓库锁字节相等；私有 uv CPython 3.13.11 环境通过 offline/no-index/require-hashes 安装十个锁定运行依赖，无依赖升级。证明为 `$TASK_ROOT/dependency-hashes.json`。Worker 没有手工删除任何目录或文件；既有测试框架和产品自建 probe 正常自行收尾。整个任务根留给 Host 按确切路径回收。

## 实际聚焦验证

五组独立解释器验证共 73 个不同测试通过；表中的额外两项是为补充 Popen/wait 证据的重复验证，不增加不同测试数。没有完整检查、前端检查或旧付费冒烟。命令、完整 stdout/stderr、exit/总墙钟秒分别保留在 `$TASK_ROOT/<name>.json/.log`；其中计时含解释器启动。

| 证据名 | 实际测试项数 | exit | 秒 |
| --- | ---: | ---: | ---: |
| `path-final-r4` | 8 | 0 | 2.779 |
| `public-safe-final` | 13 | 0 | 8.403 |
| `attach-safe` | 26 | 0 | 1.557 |
| `rpc-safe` | 15 | 0 | 1.186 |
| `daemon-safe` | 11 | 0 | 3.127 |
| `daemon-process-proof` | 2 | 0 | 1.679 |
| `baseline-path-red-r2` | 5 | 1 | 1.134 |
| `path-native-first` | 1 | 1 | 2.707 |

PATH 单元的 socket 替身仅用于权限/错误边界，不算真实 IPC 成功。PATH-04 的 CLI 是实际子解释器，实际 unsafe IPC 结构拒绝的 code/message/details.path 与 preflight 原错误完全相等；模拟 OS 写入及 socket.bind 拒绝仅各自映射 accessDenied。既有 attach 0500 单元用实际配置、替代连接，证明客户端无 mkdir/chmod/写 open/Popen；它不是唯一真实成功证明，也不冒充成功真实 RPC。

PATH-01 真实服务首试在自有 daemon 的 console bind `127.0.0.1:0` 阶段受沙箱阻止，报 `CONSOLE_PORT_IN_USE`，尚未到 C-Two register/listen/RPC。没有放宽权限、改产品跳过 console 或重复监听尝试；其他三项新增真实场景及旧真实测试未运行。本回合真实 RPC 成功数为 0、实际 RPC stop 回执为 0，0755 实际冷启动与 0500 实际读接入仍未验证。

## PATH-01..05 与新编号

| 新 loader ID | 本回合状态 |
| --- | --- |
| `protocol.test_path_state.PathStateTests.test_path01_public_canonical_selection_and_precedence` | 已通过 |
| `protocol.test_path_state.PathStateTests.test_path02_private_ipc_and_inner_links_and_dotdot_have_paths` | 已通过 |
| `protocol.test_path_state.PathStateTests.test_path03_both_owner_entries_create_before_rpc_configuration` | 已通过 |
| `protocol.test_path_state.PathStateTests.test_path03_daemon_run_repairs_before_rpc_configuration` | 已通过 |
| `protocol.test_path_state.PathStateTests.test_path03_preflight_repairs_755_without_test_environment` | 已通过 |
| `protocol.test_path_state.PathStateTests.test_path04_actual_cli_reports_structure_code_message_path` | 已通过 |
| `protocol.test_path_state.PathStateTests.test_path04_os_write_and_ipc_denial_only_map_access_denied` | 已通过 |
| `protocol.test_path_state.PathStateTests.test_path04_preflight_preserves_structural_error_object` | 已通过 |
| `protocol.test_path_state.ReadonlyPathStateTests.test_path03_500_state_real_ping_peer_has_no_mutations` | 未执行；Host 补核 |
| `protocol.test_path_state.RealPathStateTests.test_path01_linked_cli_health_and_canonical_native_root` | 已尝试，console bind 受限；Host 补核 |
| `protocol.test_path_state.RealPathStateTests.test_path03_real_cold_start_repairs_755` | 未执行；Host 补核 |
| `protocol.test_path_state.RealPathStateTests.test_path03_real_direct_daemon_repairs_755` | 未执行；Host 补核 |

新文件提供 Host 可直接执行的固定真实助手：`RealPathStateTests` 复用 3-A `DAEMON_COMMAND`、补 test PYTHONPATH 的 `prepare_daemon_spawn`、固定 catalog/facts、四个 native CLI sentinel、禁用 Worker pool 的真实服务；链接根及链接祖先均通过实际 CLI health 核对 serviceId/stateDir，另经真实请求观察规范根和 C-Two nativeRoot。0755 cold 场景还在 preflight 返回瞬间断言 0700，避免后续 daemon 修正掩盖 preflight 缺陷。direct daemon 场景不调用 transport preflight。`ReadonlyPathStateTests` 复用既有 tiny native ping peer；先建立客户端，再把状态根设 0500，检查真实 ping、两条新增 peer trace、变更观察器、模式和其他 entry snapshot，最后恢复自身模式并收集 peer stop/wait 和 RPC shutdown。peer trace 是服务端受控写入，不计入客户端只读快照；不使用 live SQLite 0500 失败来替代传输结论。这些助手在受限环境尚未走到真实成功，Host 必须核对。

3-A 的全部 26 个 loader ID 保留。旧 `PublicStateTests.test_ps07_public_resolution_preserves_path_guards`（PS-07/unsafe-path）由公开根链接和公开 `..` 拒绝迁移为实际 `state/ipc` 链接与内部 rpc_config 原始 `..` 拒绝，并断言 path、SDK connect 未触达。新 PATH-01 则明确要求公开所选链接成功。旧 `ProfileTests.test_bad_state_or_ipc_is_refused_without_repair` 内的 `link/child` 改为显式状态边界下两个内层链接 guard；保留 state/ipc 链接、文件、`..` 及原 native 权限拒绝。旧 `TrustBoundaryTests.test_group_or_world_accessible_directory_is_not_trusted` 保留只读拒绝，再验证授权所有者 cold fallback 修正 0700；旧 `ColdStartTests.test_cold_start_fails_honestly_when_the_state_directory_cannot_be_written` 的文件状态改断言结构 code/message/path，OS 拒绝另由 PATH-04 验证。旧 cold setup 观察器改为准确断言所有者 mkdir(exist_ok=True)/chmod700，不放宽只读观察器。没有删除旧编号或通过动态导入、重导出/compat namespace 改变计数。3-A finally 恢复、全部 stop/wait/shutdown 尝试及次级错误登记未削弱。

实际 unittest loader 3500→3512，旧 3500 集合全等保留，删除 0、新增 12、重复 0、装载错误 0。AST 静态类内 test 方法口径 3438→3450；它与 loader 实例 ID 不是同一计数口径。原集合及差集为 `baseline-id-set.json`、`current-id-set.json`、`id-delta.json`。早期编号 JSON 输出名与运行元数据名冲突已修正并重新装载，冲突文件不作为集合证据；本记录用上述最终 set 文件。

## 旧红灯与对照

只在任务根的 `git archive` 独立副本操作：`b772198d` 加相同新断言后五项各 failure、无 errors，exit 1、1.134 秒；原因分别为公开未规范化、unsafe IPC 无 path、0755 preflight accessDenied、direct daemon 保持 0755、BoardError 被改写。`baseline-path-red-r2.{json,log}` 是有效原回归红灯。第一次复制时 cwd 选错造成五项导入 errors，保留 `baseline-path-red` 但不计为回归证据。

另以相同私有文件/preflight 对照脚本在当前补丁、`b772198d`、冻结 `12eb4fcd` 执行，均 exit 0（表示比较完成），0.531/0.400/0.383 秒。为了不重复受限监听，仅 socket 对象为替身；实际路径、mkdir/chmod、IPC 配置和错误均来自对应产品源。旧候选链接未规范化并 accessDenied，0755 accessDenied 且保持 0755；冻结 core 与补丁均规范化链接、0755 修正700、新 HOME 默认本地设置通过。记录为 `candidate-comparison.json`、`old-comparison.json`、`core-comparison.json` 和 `path_compare.py`；不是 native IPC、真实 cold 或跨版本互通成功。原 Host `e8f57266` path_probe 历史保留，仅据已读计划引用其结论，未寻找/改写 Host 忽略目录材料。

## 目标单点变异

从自己的固定基线副本覆盖范围内补丁后，每份只做表中一个变异，独立解释器执行原目标测试。13 个最终结果全部恰好 1 failure、无 errors，非沙箱/导入/任意超时红灯；三个 3-A 缺传递场景都包含实际 `PRIVATE_STATE_REQUIRED` 堆栈，不过连接为既有 parameter 替身，因此不外推真实 RPC。client 仅在自己的比较副本变异，受管 checkout 未写该文件。每个目标方法的最终源 AST 与变异运行所用目标 AST 相等，证明为 `mutation-final-targets.json`。

| 单点 | 对应现有目标断言 | exit / 结果 | 秒 |
| --- | --- | --- | ---: |
| `public-realpath` | `test_path01_public_canonical_selection_and_precedence` | 1 / 1 failure / 0 errors | 1.060 |
| `preflight-chmod` | `test_path03_preflight_repairs_755_without_test_environment` | 1 / 1 failure / 0 errors | 0.900 |
| `daemon-chmod` | `test_path03_daemon_run_repairs_before_rpc_configuration` | 1 / 1 failure / 0 errors | 0.952 |
| `preflight-mkdir` | `test_path03_both_owner_entries_create_before_rpc_configuration` | 1 / 1 failure / 0 errors | 1.083 |
| `daemon-mkdir` | `test_path03_both_owner_entries_create_before_rpc_configuration` | 1 / 1 failure / 0 errors | 1.070 |
| `boarderror-preservation` | `test_path04_preflight_preserves_structural_error_object` | 1 / 1 failure / 0 errors | 1.038 |
| `private-linked-component` | `test_path02_private_ipc_and_inner_links_and_dotdot_have_paths` | 1 / 1 failure / 0 errors | 0.900 |
| `private-dotdot` | `test_path02_private_ipc_and_inner_links_and_dotdot_have_paths` | 1 / 1 failure / 0 errors | 0.912 |
| `structural-path` | `test_path02_private_ipc_and_inner_links_and_dotdot_have_paths` | 1 / 1 failure / 0 errors | 0.986 |
| `test-env-coupling` | `test_path03_preflight_repairs_755_without_test_environment` | 1 / 1 failure / 0 errors | 0.892 |
| `service-propagation` | `test_ps08_call_service_directory_parameter` | 1 / 1 failure / 0 errors | 1.811 |
| `board-propagation` | `test_ps08_call_board_directory_parameter` | 1 / 1 failure / 0 errors | 1.806 |
| `readonly-propagation` | `test_ps08_readonly_client_directory_parameter` | 1 / 1 failure / 0 errors | 1.821 |

初次变异中去 chmod 得到目标 accessDenied 的 1 error，随后将测试捕获该 BoardError 并明确 fail，最终表计的是对应 assertion failure；两次变异工具因文本定位命中数量不符中止，未记为产品 red。原始日志与各次副本留存，不删除或覆写历史日志；最终全表为 `mutation-results-r3.json`，具体改动、命令、结果及 AST 绑定为 `mutation-final-targets.json`。

## 进程、收尾与残留

首次真实助手创建的 daemon PID 23924 已由直属 Popen wait，exit 2；场景子解释器 PID 23917 communicate/wait exit 1。`path-real-r3ejc10q/evidence.json` 记录自身 service/Worker locks released=true、RPC shutdown completed=true、cleanupFailures=[]；服务未发布 endpoint，因此没有虚构 serviceId、成功 RPC 或 service_control stop。未触达日常状态/服务，也没有按未知 PID 发送信号。

旧 daemon 两个选定测试额外捕获真实 Popen receipts：五个受控 lock/sleep 子进程收到自己实例的 stop 文件并 wait exit 0，一个 pass 子进程 wait exit 0。六个 PID/command/wait 记录在 `daemon-owned-processes.json`，两测试 exit 0、1.679 秒。public 安全场景的实际 case 子进程及 cold-import 子进程也都 communicate/wait exit 0，保留各场景 `case-process.json`、`evidence.json`；三个 cleanup-failure 测试的 substitute-handle/预期 stop error 不计为真实进程。CLI 结构拒绝子进程 wait exit 1（预期），证据在各 path-state 根的 cli-process.json。所有外层聚焦/变异/编号/比较子解释器均使用阻塞 subprocess.run 收集退出。当前没有未确认停止的自建进程，未手工回收任务根。

Host 待补：在封存补丁上执行新 path-state 全12项（含四个真实场景）及受影响旧文件完整聚焦验证，记录真实 serviceId/PID/nativeRoot、mode、stop/wait/shutdown；核对链接祖先与直接链接 CLI、两处 owner setup、0500 tiny peer 读接入。若真实验证暴露本范围缺陷，继续原 3-B run。Host 在两微任务整合后才运行最终完整检查；本回合不代跑、不声称已由 reviewer/helper 验证。

## 精确聚焦命令与绑定

以下命令在受管源码根执行，`PYTHON` 为任务根的已锁定私有解释器，清洁环境与 TMPDIR/BUDDY_CHECKS_TMPDIR 已按前述配置；baseline 命令改 cwd 为自己的 baseline 副本。`run.py` 是任务根里记录完整命令、计时、stdout/stderr 与 exit 的本地工具。Host 可直接用固定新测试文件作为验证助手：`$PYTHON -m unittest protocol.test_path_state`，每个真实场景会再启动独立解释器，并在自有根保留证据。

`path-final-r4`：

```sh
$PYTHON -m unittest protocol.test_path_state.PathStateTests
```

`public-safe-final`：

```sh
$PYTHON -m unittest protocol.test_public_state.PublicStateTests.test_ps03_no_service_stop_and_restart_do_not_start protocol.test_public_state.PublicStateTests.test_ps05_missing_readonly_client_never_creates_or_starts protocol.test_public_state.PublicStateTests.test_ps07_internal_request_still_requires_private_root protocol.test_public_state.PublicStateTests.test_ps04_injected_call_does_not_resolve_state protocol.test_public_state.PublicStateTests.test_ps08_call_service_directory_parameter protocol.test_public_state.PublicStateTests.test_ps08_call_board_directory_parameter protocol.test_public_state.PublicStateTests.test_ps08_readonly_client_directory_parameter protocol.test_public_state.PublicStateTests.test_ps06_supplied_endpoint_keeps_default_directory protocol.test_public_state.PublicStateTests.test_ps07_public_resolution_preserves_path_guards protocol.test_public_state.PublicStateTests.test_ps06_cold_helper_imports_with_source_only_environment protocol.test_public_state.FixtureRepairTests
```

`attach-safe`：

```sh
$PYTHON -m unittest protocol.test_transport_attach.ReadOnlyAttachTests protocol.test_transport_attach.TrustBoundaryTests protocol.test_transport_attach.ColdStartTests protocol.test_transport_attach.RpcReadOnlySetupTests.test_missing_ipc_does_not_create_on_transport_or_board_client_attach protocol.test_transport_attach.RpcReadOnlySetupTests.test_read_only_state_with_private_ipc_reaches_rpc_without_mutation protocol.test_transport_attach.RpcReadOnlySetupTests.test_missing_state_does_not_create_on_board_client_attach protocol.test_transport_attach.RpcReadOnlySetupTests.test_explicit_state_reaches_rpc_setup
```

`rpc-safe`：

```sh
$PYTHON -m unittest protocol.test_rpc_config.ProfileTests.test_profile_disables_pool_and_preserves_non_pool_limits protocol.test_rpc_config.ProfileTests.test_configure_is_idempotent_and_writes_no_environment protocol.test_rpc_config.ProfileTests.test_windows_never_passes_root_override protocol.test_rpc_config.ProfileTests.test_missing_state_refuses_before_sdk_or_default_paths protocol.test_rpc_config.ProfileTests.test_explicit_state_and_environment_are_the_only_sources protocol.test_rpc_config.ProfileTests.test_0755_state_creates_0700_ipc_without_parent_repair protocol.test_rpc_config.ProfileTests.test_read_only_setup_does_not_create_or_chmod_missing_directories protocol.test_rpc_config.ProfileTests.test_wait_admission_cannot_consume_every_native_callback protocol.test_rpc_config.DaemonFixtureTests
```

`daemon-safe`：

```sh
$PYTHON -m unittest blackboard.service.test_daemon.DaemonCeilingTests blackboard.service.test_daemon.DaemonPoolLifecycleTests.test_stop_waits_for_initial_late_slots_and_keeps_every_stop_intent blackboard.service.test_daemon.DaemonPoolLifecycleTests.test_signal_style_reentry_cannot_erase_late_slot_stop_intents blackboard.service.test_daemon.DaemonPoolLifecycleTests.test_signal_style_reentry_during_reconcile_preserves_stop_intents blackboard.service.test_daemon.DaemonPoolLifecycleTests.test_failed_late_start_drains_spawned_processes_but_preserves_reused_workers blackboard.service.test_daemon.DaemonPoolLifecycleTests.test_failed_start_never_stops_another_owner_after_its_child_lost_the_lock blackboard.service.test_daemon.DaemonPoolLifecycleTests.test_private_start_abort_targets_only_the_matching_supervisor_instance blackboard.service.test_daemon.DaemonPoolLifecycleTests.test_signal_style_reentry_followed_by_spawn_error_keeps_every_stop_intent
```

`daemon-process-proof`：

```sh
$PYTHON $TASK_ROOT/trace_tests.py blackboard.service.test_daemon.DaemonPoolLifecycleTests.test_failed_late_start_drains_spawned_processes_but_preserves_reused_workers blackboard.service.test_daemon.DaemonPoolLifecycleTests.test_failed_start_never_stops_another_owner_after_its_child_lost_the_lock
```

`baseline-path-red-r2`：

```sh
$PYTHON -m unittest protocol.test_path_state.PathStateTests.test_path01_public_canonical_selection_and_precedence protocol.test_path_state.PathStateTests.test_path02_private_ipc_and_inner_links_and_dotdot_have_paths protocol.test_path_state.PathStateTests.test_path03_preflight_repairs_755_without_test_environment protocol.test_path_state.PathStateTests.test_path03_daemon_run_repairs_before_rpc_configuration protocol.test_path_state.PathStateTests.test_path04_preflight_preserves_structural_error_object
```

`path-native-first`：

```sh
$PYTHON -m unittest protocol.test_path_state.RealPathStateTests.test_path01_linked_cli_health_and_canonical_native_root
```

各产品/测试最终文件 SHA-256（不含此记录自哈希）：

```json
{
  "src/hey_my_buddy/protocol/transport.py": "398edcfdc90c27d2a96e72e936bdb456828e1cda30a147e18fcca2dd182ea7c4",
  "src/hey_my_buddy/protocol/rpc_config.py": "129f677a1059dcc7df80c9ac709acd582c85a65767ba580ef4933fed05e8b963",
  "src/hey_my_buddy/blackboard/service/daemon.py": "6ebfe32e00971d563e3aca694faad6b3e0b710b014437893812a7bb4ec59dda7",
  "tests/python/protocol/test_path_state.py": "f5e99ae8e9c4cd04ae152595def44fba2554e691831b669e093765ba57cf661f",
  "tests/python/protocol/test_public_state.py": "183665954aac24bafe5e2b29c1c58e76395bac66cad0a25e182c8bcdb8aab956",
  "tests/python/protocol/test_rpc_config.py": "5d7cf68f56b0732425ab5346e16c22499dcb3b00aa9763f25c7878e4eef2b91f",
  "tests/python/protocol/test_transport_attach.py": "6a0fcabecbb03ace75eddfe195219f7d986b567ae2546ab11e0a56ddcfabcc4e"
}
```
