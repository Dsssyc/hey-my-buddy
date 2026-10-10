# C-Two 0.7.4 补充修复：Host 整合登记

本记录保存 Claude Code Host 复核后新增的微任务与公共接线核对。完整初跑绑定 `0dc618ed`、退出 1、26 个模块失败的事实保留在 `c-two-failure-review.md`；本记录的聚焦通过不替代最终完整检查。所有本轮核对使用私有状态、运行时、临时根与空 HOME；日常状态目录留下的 `ipc` 未动，默认公共端点命名空间未清扫，日常服务与 Worker 未重启或替换，付费冒烟仍为 0 次。

## 固定产物

| 微任务 | run / 固定提交 | artifact / 累积补丁 SHA-256 | 范围 |
| --- | --- | --- | --- |
| 2-A | `ad73f506-d3f9-49f5-85fc-7b72fcbb643c` / `02a45fbec3bf781ad1782cf56f16309077d52d8f` | `639faabd-f17a-46cb-a8f7-5d26348e0ee2` / `aee41d399274f0d8d033434155f393fdfd5e8a0347932d139d7fd382a38d5590` | 原七路径，集合与授权一致 |
| 2-B | `7bba6511-75fe-4aa3-b042-2fd5df372bc3` / `35373f49ddf4e80b3021deb77e5b569c360f9780` | `37c1d055-514e-45a8-af4c-07168123704d` / `f0657f5d0380729af3184da38df7d7be4e6d4d1191a0acde64ee562bc66d795f` | 原五路径，集合与授权一致 |

Host 从黑板固定输出独立读回补丁，核对基线 `b2741294`、全部路径、SHA-256，并从输出提交归档成独立源码副本。两份 Worker 范围内记录逐字节保留，Host 实际补核写在本文件，避免破坏 integration-record 的固定产物绑定。两个供应方不可重试限流及原 run 改用完整配置继续的路由编号已登记在执行计划，未另开 run。

## 实际聚焦与变异

下表日志与 JSON 相对 `<实施检出>/tmp/c073-host/`，计时分别为 unittest 测试耗时与命令墙钟耗时。每个模块使用独立解释器，没有合跑不同 SDK 状态域的模块。

| 日志前缀 | 目标与项数 | 退出码；测试 / 命令秒数 |
| --- | --- | --- |
| `host-repair-domain-rpc` | `protocol.test_rpc_config` 24 项 | 0；14.660 / 15.930 |
| `host-repair-domain-owner` | `buddy.harnesses.test_inquiry_owner` 15 项 | 0；5.309 / 6.287 |
| `host-repair-domain-live` | `buddy.harnesses.test_c_two_live` 70 项 | 1；27.409 / 28.216，1 项失败 |
| `host-repair-domain-live-idle` | 同一固定源码的实时通道文件 70 项 | 0；14.780 / 14.999 |
| `host-repair-isolation-console` | `console.test_console` 20 项 | 0；31.497 / 32.307 |
| `host-repair-isolation-support` | `cli.test_support_cleanup` 8 项 | 0；8.185 / 8.958 |

实时通道首次失败发生在 warm 客户端准备、尚未进入目标超时调用的看门狗；观察时负载约 39、随后升至 72。低峰观察为 load 16.98、CPU 50.3% 空闲时，原文件单独重跑通过，代码与断言未改。保留首次失败与后续通过，不预先证明其原因是负载。原生 fresh/warm 连接均按期返回 pre_dispatch，dispatch_uncertain 的同一连接随后可用，过期请求未被消费；正常退出与自建进程强杀后的回收事实仍通过。

2-A 的七份 Host 单点源码副本分别恢复默认状态回退、恢复父目录权限拒绝、去掉 SDK 注册拒绝映射、去掉链接保护、去掉 `..` 保护、去掉 peer 注册根、去掉客户端连接根；七份均退出 1，目标 AssertionError 实际失败。原生 0755 父目录下新建 0700 ipc 的服务/客户端往返通过，问询真实签收、fsync、身份绑定与私有空 HOME 防护通过。证据为 `host-domain-mutants-plan.json`、`host-domain-mutants-results.json` 和对应 `host-domain-red-*.{json,log}`；星号在此只表示日志命名说明，未用于删除。

2-B 的 20 处环境或 fixture 保护及真实 daemon 丢 catalog 的额外一处变异共 21 份，均在 Host 从固定源码另建的单点副本上退出 1、命中目标断言。真实 catalog 丢失变异由自建记录拒绝诱饵抓住，没有调用真实原生程序或读取登录凭据。真实服务、CLI、HTTP 先读取私有 fixture，再读取修改后的模型身份；空私有 HOME 没有原生目录。证据为 `host-isolation-mutants-{plan,results}.json`、`host-isolation-extra-mutants-{plan,results}.json` 及对应原始日志。

## Host 公共接线归属与偏离

Host 统一修改 `buddy/runtime/live.py`、`buddy/runtime/worker.py`、`buddy/roles/live.py`、`buddy/roles/run_controller.py` 四个生产文件及两个受影响测试文件 `buddy/runtime/test_live.py`、`buddy/roles/test_role_live_seam.py`；它们不属于两份 Worker 产物的路径集合。Worker 给运行时与续租/活动转发接缝传自身状态根；运行时不从 SDK 当前域借用状态；控制器给 Endpoint 传显式环境中的状态；角色接缝在读取就绪材料和 SDK 域之前解析状态。Worker 真实注册的库拒绝沿用 Endpoint 的 BoardError 映射。公开 CLI 默认解析、角色判定、工具与停止行为、registry、schema 均未改。

首次 Host 运行时测试有三处失败，是 `/tmp` 与 `/private/tmp` 的同目录别名比较；FakeBoard 与新增所有者夹具按已有规范取 canonical 路径，原断言和编号保留，35 项重新通过。独立只读审查指出角色接缝缺状态时仍先查询 SDK 当前域；Host 提前解析并加强“不查询当前域”的断言，18 项重新通过。这两处是 Host 整合归属，未代改 Worker 范围内产物。

| 日志前缀 | 目标与项数 | 退出码；测试 / 命令秒数 |
| --- | --- | --- |
| `host-repair-public-runtime` | 运行时 35 项，第一次 | 1；5.898 / 6.255，3 个别名比较失败 |
| `host-repair-public-runtime-canonical` | 运行时 35 项 | 0；5.906 / 6.206 |
| `host-repair-public-role-early` | 角色接缝 18 项 | 0；0.049 / 0.401 |
| `host-repair-public-wiring` | Worker 主循环接线 5 项 | 0；2.801 / 3.158 |
| `host-repair-public-invariants` | Worker 不变量 11 项 | 0；0.670 / 0.995 |
| `host-repair-registered` | 注册运行接线 21 项 | 0；8.965 / 9.334 |

Host 另作两份公共接线单点变异：运行时恢复借用 SDK 当前域、角色恢复在校验状态前查询 SDK；两份目标断言均失败，退出 1，0.319 与 0.398 秒。证据 `host-public-mutants-{plan,results}.json` 及对应原始日志。既有 `9b73fe42` 清理顺序代码修正已由 Claude Code Host 接受，其偏离与两处点变异继续保留，不改写成记录措辞调整。

实际 loader 清单为 203 个模块、3,282 个唯一编号，无加载错误或重复；原 3,270 个编号集合全部保留，无删除或改名，仅新增 12 个。原始清单和集合证明为 `repair-ab-ids.json`、`repair-ab-id-delta.json`。新增编号如下，两份产物整合、固定登记和签收后才开始 2-C/2-D；最终完整检查与四个逐次批准的付费冒烟尚待。

- `buddy.harnesses.test_c_two_live.ExplicitStateTests.test_internal_endpoint_and_channel_refuse_missing_state_before_sdk`
- `buddy.roles.test_role_live_seam.LiveBindingRegistryTests.test_a_ready_handle_cannot_borrow_the_ambient_sdk_state_when_its_owner_is_missing`
- `buddy.runtime.test_live.WorkerLiveUnitTests.test_missing_owner_root_refuses_before_sdk_even_with_an_ambient_domain`
- `buddy.runtime.test_live.WorkerLiveUnitTests.test_worker_supplied_root_wins_over_an_injected_client_root`
- `cli.test_support_cleanup.FixtureCleanupTests.test_r05_catalog_daemon_fixture_binds_capabilities_without_native_discovery`
- `cli.test_support_cleanup.FixtureCleanupTests.test_r06_catalog_owner_passes_fixture_after_inherited_pins_are_removed`
- `cli.test_support_cleanup.FixtureCleanupTests.test_r06_child_uses_private_state_and_explicit_catalog`
- `console.test_console.ConsoleDaemonTests.test_r05_private_daemon_reads_owned_catalog_without_native_discovery`
- `protocol.test_rpc_config.IsolatedTransportTests.test_0755_state_creates_0700_ipc_and_round_trips`
- `protocol.test_rpc_config.ProfileTests.test_0755_state_creates_0700_ipc_without_parent_repair`
- `protocol.test_rpc_config.ProfileTests.test_explicit_state_and_environment_are_the_only_sources`
- `protocol.test_rpc_config.ProfileTests.test_missing_state_refuses_before_sdk_or_default_paths`
