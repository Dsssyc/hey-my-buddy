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

## R-05 目标识别结论撤回与用户授权返修

Host 复读真实 catalog-loss 日志后发现，前述“额外一处命中拒绝诱饵”的结论不成立：固定测试失败于 `Browser.bootstrap` 的 HTTP 500 / INTERNAL_ERROR 断言，没有明确核对诱饵记录或文件 source/content。这份失败保留，但不计为指定目标变异的有效证据；其余 20 份具体环境/fixture 目标断言不受影响。错误来自 Host 对任意 AssertionError 的宽泛分类，不能写成已完成 R-05 验证。

Host 在原 2-B run 发出不指定配置的 continue，黑板拒绝 `CONFLICT: An accepted goal cannot be continued`，没有创建新回合。用户明确授权例外新增一个经路由的窄微任务 2-E，唯一可写 `tests/python/console/test_console.py` 中的新增 R-05 测试与新 `docs/acceptance/c-two-r05-guard.md`；不碰旧 run、原固定记录与正在由 2-C 独占的 support。目的为在 CLI/HTTP/bootstrap 错误时也明确核对记录拒绝诱饵，正常路径仍验证真实服务与修改后 fixture 重读。旧签收、原失败与撤回全部保留，R-05 等新固定交付与实际目标变异通过后再登记。

首批整合为 `57837f2f`，2-A 的 `int-9f3931f7-e1e3-49e8-9763-cd4d5a40311f`、2-B 的 `int-8b1f1f78-4f9f-400f-85ab-578282f80aca` 均 verified 后内部签收。原始产物日志与脚本已保存在 Host 根的 `retained-c2a-8p4Pqv` / `retained-b2b-iptu2A`，Host 按创建时记录的两个确切任务根整体回收，删除错误未屏蔽；清单在 `repair-ab-task-root-cleanup.json`。受管检出经黑板 cleanup-plan 核对，2-A 已自动 applied，2-B 后续按其 eligible 计划 apply，不手工删除受管检出。

2-B 的受管检出回收已完成，计划 `cln-6d49a5d2-1194-4f99-9623-ac975c796f20` 返回 applied、removed=true。首次 apply 缺少 confirmPath 被拒绝，未发生回收；随后用计划给出的确切路径确认后成功，两个原始响应均保留。Host 只补充回收事实，没有修改 Worker 的固定记录、代码或测试，因此不重跑其聚焦测试。

第二批 2-C/2-D 基线 `57837f2f`，例外新增的 2-E 基线 `27b91e0d`。三个路由决定、首次不可重试限流与同 run 完整配置继续均列在执行计划；不指定 buddy 的首次请求及失败停止证据完整保留。最终完整检查将使用新的独立日志和空私有 HOME，不覆盖初跑的 `final-check.log` 或结果。

## 2-C 与 2-E 的固定产物及 Host 实际补核

| 微任务 | 固定提交 / artifact | 累积补丁 SHA-256 | 路径核对 |
| --- | --- | --- | --- |
| 2-C | `5bed049962c2e954c8a780bf6cd61b4fd69ec04c` / `856f42ea-2d85-476d-9c43-ae18905339a9` | `1f985b7b0dd6aa0fa2041ab2ab592457d798a8924029c7470544dbf74762a482` | 七份测试、support 与新记录，九路径全部在原范围内 |
| 2-E | `c1822bbb5443a62d12429f37e52be4d11d6082bd` / `1270ff24-19ed-4941-ba8e-2157954562c3` | `74675c1c87c870b3b1d34f55616c9a715d2ef19d9cf31ccaf7ae0074db8315d2` | 仅新增 R-05 测试的内容与新记录，原十九项方法未改 |

两份 Worker 都如实停在 assistance：私有 daemon 启动被其运行环境阻断，不能把已有静态、加载或其他通过结果称为原生通过。Host 从各自固定输出提交重新归档，核对原基线、全部路径与补丁 SHA-256，再用 0.7.4 私有解释器、空 HOME 与独立状态域逐文件执行。范围内记录按固定输出保留，补证写在本文件；没有代改 Worker 代码。

| 日志前缀 | 模块与项数 | 退出码；测试 / 命令秒数 |
| --- | --- | --- |
| `host-rpc-green-test_cli` | `cli.test_cli` 19 项 | 0；0.367 / 1.095 |
| `host-rpc-green-test_cli_views` | `cli.test_cli_views` 11 项 | 0；14.542 / 14.851 |
| `host-rpc-green-test_host_cli` | `cli.test_host_cli` 31 项 | 0；3.819 / 4.567 |
| `host-rpc-green-test_liveness` | `blackboard.service.test_liveness` 5 项 | 0；0.342 / 0.615 |
| `host-rpc-green-test_harness_startup` | `blackboard.service.test_harness_startup` 4 项 | 0；0.024 / 0.291 |
| `host-rpc-green-test_live_lifecycle_integration` | `buddy.runtime.test_live_lifecycle_integration` 1 项 | 0；0.007 / 0.265 |
| `host-rpc-green-test_daemon` | `blackboard.service.test_daemon` 13 项 | 0；4.631 / 4.913 |
| `host-r05-console-green` | `console.test_console` 20 项 | 0；26.648 / 27.345 |

2-C 的七模块共 84 项全部通过，真实 daemon 文件包含实际注册、连接与健康 RPC。Host 从固定源码分别复制九份单点变异：去掉 CLI 根绑定、恢复旧桩签名、去掉 attach 根、放宽缺根拒绝、放宽外来根身份校验、恢复无路径 Mock、去掉 Worker 显式传根、去掉 channel 显式传根、去掉 daemon 直接 health 根。九份均为一项目标 failure、errors=0、退出 1；每份日志还核对对应私有根、关键字签名或身份绑定的具体失败消息。最后一份实际启动私有 daemon 后命中根断言，不是 socket 拒绝。证据为 `host-rpc-green-results.json`、`host-rpc-mutants-plan.json`、`host-rpc-mutants-results.json` 及逐项原始日志。

R-05 真实 catalog-loss 变异退出 1，一项目标 failure、errors=0，5.105 / 5.780 秒。HTTP 500 仍先出现，随后 finally 中明确断言 `R-05: attempted native discovery/start; rejecting decoy calls:` 失败，附三条自建拒绝脚本的 `["dsh"]` 记录，进入真实发现或程序启动之前即阻断，没有模型调用。此前撤回的旧结论不恢复为旧产物的证据；这一份是新 2-E 的实际目标证明，保存在 `host-r05-catalog-loss-red.log`、`host-r05-target-proof.json`。正常文件与变异均使用 Host 自己创建的私有域，没有访问日常状态或默认公共端点目录。

两份补丁在 `688f0938` 整合，原所有路径的目标 blob 与固定输出逐字节相等。2-C 的 `int-b9bd3d7c-7dcf-4903-897b-eb82c4fb7d3a`、2-E 的 `int-9d5d5531-e0a0-4587-803f-82edfce4f687` 均 verified 后 accepted/completed。黑板回收计划 `cln-bdee4e11-2351-4bf6-b527-fcdb8a65ae0b`、`cln-fd8d3013-6476-4291-a503-d98db3ce4731` 回读均 applied、removed=true，没有重复 apply 或手工删受管检出。Host 保留两项原始日志、脚本与结果材料后，按固定交付报告的确切任务根整体回收；169 与 13 份保留文件的清单、哈希、删除结果在 `repair-ce-retained-evidence.json`、`repair-ce-task-root-cleanup.json`。argv 观察只作补充，实际收尾依据已保存直接子进程的 wait 与签收停止证据，不把缺少 PID 当作停止。

## 2-D 首份退回及 Host 共享夹具接线

首份固定 `8a35816360fbcbb8fb39288006594d724e44e2f3` 的三路径与 SHA-256 已核对。Host 两项并发实跑为退出 1、8.272 / 8.810 秒、两项失败，均实际走到 ready 后在子夹具的 IPC 根断言失败：SDK 返回 canonical `/private/tmp`，自建根仍为 `/tmp`。此为微任务范围内的代码缺陷，已在原 run `a4e106fd-82f7-44ca-8312-9b3a11bd320d` continue 打回，全部四项配置省略；要求在归属建立时规范根，保留隔离断言。原固定失败、三次受限检查及未知 native 停止材料继续保留，不先行签收。

Host 同时复现其范围外提示：原单项 `test_one_send_delivers_and_answers_a_question_and_finishes_completed` 退出 1、20.659 / 21.189 秒，等待 ready observation 超时。共享 `ZcodeFixtureCase` 只设置控制器环境里的状态根，角色通道却从测试进程环境取得另一个根。Host 在公共 `tests/python/buddy/harnesses/zcode/test_zcode.py` 的 `live_channel` 统一传本 fixture 的显式状态根，并在观察就绪前核对 channel 根归属；这一公共改动不属于 2-D 的三路径，不由 Worker 自行扩范围，不改变任何产品行为。

用固定 2-D 源码与这项 Host 公共改动合成独立副本，原单项通过，0.951 / 1.278 秒；从该副本只去掉显式传根，保留根归属断言，目标即退出 1、failure=1、errors=0，0.547 / 0.835 秒，明确报 `the role channel must use the fixture's private state root`。它没有依靠超时或导入错误识别目标。材料为 `host-checkpoint-existing-state-seam`、`host-checkpoint-shared-seam-green`、`host-checkpoint-shared-seam-red` 的原始日志与 JSON；2-D 最终全十项及三处变异仍待其返修固定输出。

共享夹具的工具拒绝文件 `buddy.harnesses.zcode.test_zcode_tool_refusals` 八项独立通过，5.093 / 5.424 秒，原工具拒绝与停止断言保留。该聚焦验证不替代最终完整检查，日志为 `host-zcode-shared-refusals.{json,log}`。
