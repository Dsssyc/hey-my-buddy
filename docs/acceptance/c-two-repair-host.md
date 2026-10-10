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

2-D 返修固定为 `6b3f674fe57d2afbe019c1004413e43f9a60e2cc`，artifact `019047b0-1709-4d1f-90e4-17b0490e485f`，累积补丁 SHA-256 `193da66f3b01a56ca6a2983ace9c08b0491d4e2428ccfe599feceded743708d0`；仍仅原三路径。父与子根都在建立 TemporaryDirectory 时使用 canonical 父目录，再规范自身路径，没有放宽路径或身份断言。Host 将该固定输出与已提交的公共夹具接线合成独立副本，十项全部通过，退出 0，12.296 / 12.754 秒。四个实际进程持有者分别报告完整回合、controllerStopped=true、nativeStopped=true、rpcShutdownConfirmed=true、wait 退出 0，证据 `host-checkpoint-final-green.{json,log}` 与 `host-checkpoint-green-stop-proof.json`；此前失败回合的 unknown 不因此改写。

Host 从这份合成源码另建三份变异，保留现有防护断言：强制持有者使用共享 SDK 状态域，命中子夹具实际 IPC 根的归属断言；late 场景复用一个持有者，命中 ownerPid 不同的断言；own 管道发送 peer 的问询编号，命中 peer-question 记录必须为空的断言。三份各一项 failure、errors=0、退出 1，测试 / 命令耗时分别为 4.305 / 4.601、4.325 / 4.554、1.868 / 2.088 秒。没有把权限、路径别名或导入错误当红灯。没有采用旧助手中恢复整段旧测试并额外插入断言的第一种变异作证明：额外断言不能替代当前测试的防护证据。实际三份计划、改动与原始结果为 `host-checkpoint-mutants-{plan,results}.json` 及对应日志。

四个付费冒烟入口在修复后的源码上再次作静态导入，均退出 0、modelCalls=0、nativeStarts=0，`paid-started.json` 不存在。首次静态入口因 Host 未先建精确 round 根而退出 1，随后补建自己记下的四个根，原前提错误保留在 `native-smoke-static-root-precondition.json`，成功结果在 `native-smoke-static-after-repair.json`；没有运行或重跑付费模型。

## 最终 core 合并与编号核对

最新 `socu/buddy-core@1f7e8b60577cc0b8883aa5dca9e9a1bc392f6ad0` 已无冲突合入，合并提交 `fe9c5a8e10d5f0874a0eb1fa92dc003057bee7d9`；祖先关系实测通过。本线相对该 core 的 `apps/console` 差集为空，保留已验收的前端与资源，不重复前端测试。2-D 在 `c0a10e22` 整合，`int-9ccf425c-0fbb-4b67-a5a5-3b7b72980aec` verified 后 accepted/completed。

Host 分别对该 core 的归档副本与合并后的源码执行 loader，执行测试和模型的次数均为 0。core 为 212 个模块、3,421 个编号，合并后为 213 个模块、3,473 个编号；无加载错误或重复。3,401 个未变化编号集合相等，变更/删除 20 个、新增 72 个，净增 52 个。20 个旧编号按前面各微任务的原生期限、原生回收及关闭缓冲池迁移对照核对；RPC 夹具、R-05 返修和检查点两项均没有减少原编号。最新 core 自己增加的编号纳入基线，不归成本线新增。完整原始清单为 `repair-current-core-ids.json`、`repair-final-merged-ids.json`、`repair-final-core-id-delta.json`。

下面只列本线退出的编号；它们的原防护由所列迁移内容接替，新增原始编号及各次防护变异保留在前述证据。

| 退出编号 | 接替的验证 |
| --- | --- |
| `buddy.harnesses.test_c_two_live.ChannelUnitTests.test_bounded_call_slots_report_busy_and_drain_back` | 重复原生 deadline 无等待线程/permit；旧线程资源机制删除 |
| `buddy.harnesses.test_c_two_live.ChannelUnitTests.test_the_whole_connect_and_call_is_bounded_by_the_window` | 原生 connect 与 call 期限、fresh/warm 暂停及恢复；未确认停止继续为未知 |
| `buddy.harnesses.test_c_two_live.CleanupPrimitiveTests.test_a_replaced_file_refuses_and_a_missing_file_is_already_absent` | 原生回收的停止、地址、域与凭据绑定核对，失败不重试删除 |
| `buddy.harnesses.test_c_two_live.EndpointLifecycleTests.test_the_socket_identity_is_captured_from_the_registered_address` | inspect_endpoint 凭据绑定与 reap_endpoint；忙、目标替换、未知与外来域的真实/单点核对 |
| `buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_a_replaced_socket_file_is_refused_by_the_cleanup` | inspect_endpoint 凭据绑定与 reap_endpoint；忙、目标替换、未知与外来域的真实/单点核对 |
| `buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_a_stalling_endpoint_returns_within_the_window_and_stays_bounded` | 原生 connect 与 call 期限、fresh/warm 暂停及恢复；未确认停止继续为未知 |
| `buddy.harnesses.test_c_two_live.WireFrameTests.test_the_request_frame_is_the_request_plus_exactly_three_private_fields` | 同一公开请求加私有截止帧，整帧与时间窗防护 |
| `buddy.roles.test_role_live_seam.HandleBindingTests.test_ready_cleanup_cannot_name_an_unrelated_socket` | inspect_endpoint 凭据绑定与 reap_endpoint；忙、目标替换、未知与外来域的真实/单点核对 |
| `buddy.roles.test_role_live_seam.HandleBindingTests.test_unknown_controller_group_never_deletes_endpoint` | inspect_endpoint 凭据绑定与 reap_endpoint；忙、目标替换、未知与外来域的真实/单点核对 |
| `buddy.runtime.test_live.WorkerLiveRealPeerTests.test_b2_17_real_same_name_different_addresses_and_normal_socket_disappearance` | inspect_endpoint 凭据绑定与 reap_endpoint；忙、目标替换、未知与外来域的真实/单点核对 |
| `buddy.runtime.test_worker_live_wiring.WorkerEndpointClosureTests.test_controller_end_through_execute_clears_only_the_captured_socket` | inspect_endpoint 凭据绑定与 reap_endpoint；忙、目标替换、未知与外来域的真实/单点核对 |
| `buddy.runtime.test_worker_live_wiring.WorkerEndpointClosureTests.test_worker_exit_closes_its_own_c_two_socket` | inspect_endpoint 凭据绑定与 reap_endpoint；忙、目标替换、未知与外来域的真实/单点核对 |
| `protocol.test_inquiry_transport.TransportTests.test_stalled_sdk_calls_expire_without_reporting_a_stopped_owner` | 原生 connect 与 call 期限、fresh/warm 暂停及恢复；未确认停止继续为未知 |
| `protocol.test_rpc_config.IsolatedTransportTests.test_a_client_with_other_overrides_can_still_reach_the_profile_server` | 0.7.4 私有端点域、pool_enabled=false、非池分段上限、原生内存与往返、Windows 默认分支 |
| `protocol.test_rpc_config.IsolatedTransportTests.test_chunked_fallback_uses_the_production_reassembly_bounds` | 0.7.4 私有端点域、pool_enabled=false、非池分段上限、原生内存与往返、Windows 默认分支 |
| `protocol.test_rpc_config.IsolatedTransportTests.test_concurrent_large_transfers_fit_two_segments` | 0.7.4 私有端点域、pool_enabled=false、非池分段上限、原生内存与往返、Windows 默认分支 |
| `protocol.test_rpc_config.IsolatedTransportTests.test_configured_capacity_mapping_and_rss_are_separate_measurements` | 0.7.4 私有端点域、pool_enabled=false、非池分段上限、原生内存与往返、Windows 默认分支 |
| `protocol.test_rpc_config.IsolatedTransportTests.test_pool_enabled_false_still_maps_shared_memory` | 0.7.4 私有端点域、pool_enabled=false、非池分段上限、原生内存与往返、Windows 默认分支 |
| `protocol.test_rpc_config.ProfileTests.test_configure_applies_once_and_writes_no_environment` | 0.7.4 私有端点域、pool_enabled=false、非池分段上限、原生内存与往返、Windows 默认分支 |
| `protocol.test_rpc_config.ProfileTests.test_profile_is_bounded_and_offers_no_fake_off_switch` | 0.7.4 私有端点域、pool_enabled=false、非池分段上限、原生内存与往返、Windows 默认分支 |

## 合入新 core 后完整检查及五份夹具补齐

`0648162d45b473ed9580a118694e17595dc8a567` 已包含指定 `1f7e8b60`，完整命令 `uv run --frozen python -m hey_my_buddy.cli.checks` 未带 jobs，退出 1，665.333 秒；213 个模块中 207 个通过、6 个失败，通过模块汇总执行 3,359 项、跳过 1 项。3,473 是独立 loader 的全量编号数，不称为全通过。开始/结束 load 分别约 24.27 / 24.52，仅记录观察，不作为失败原因。完整原始日志、运行根、提交绑定与失败头为 `final-repair-check-{started,result}.json`、`final-repair-check.log`、`final-repair-failure-inventory.json`，未覆盖先前 26 模块失败的日志。

六份失败文件为 `buddy.runtime.test_repair_recovery`、`buddy.harnesses.zcode.test_zcode_native`、`buddy.harnesses.dsh.test_dsh_role_wiring`、`buddy.harnesses.zcode.test_zcode_inquiry`、`install.test_backup_preflight`、`protocol.test_transport_attach`。恢复测试在 daemon 启动前被 HOME 归属校验拒绝；DSH 问询与 ZCode 活动 Host peer 未给角色接缝传本 fixture 的根；备份 preflight 在构造子环境创建 HOME 之前取快照；RPC attach 仍把 SDK 允许的 0750 ipc 当 unsafe，mock 又绕过真正的首次 I/O 校验。两份独立只读审查与实际源码一致；没有因此恢复产品父目录权限要求或 SDK 当前域回退。

安装版 ZCode 的首次 2 errors / 3 failures 只证明 app-server 提前 exit 1，没有底层 stderr 或模型 mock 请求证明。Host 在私有根把本测试自建 wrapper 的 stderr 重定向到自建日志、正常清理前保留后单文件运行，六项通过，20.221 秒；之后未改源码的原文件再独立运行，六项通过，19.693 / 20.072 秒。保留首次失败和两份后续结果，不将其原因写成负载。实际模型服务只为已有 localhost 响应夹具，付费模型调用仍为 0。原始证据为 `installed-zcode-isolated-diagnostic.{json,log}`、`installed-zcode-unmodified-isolated.{json,log}`。

本轮补齐微任务 2-F 仍执行 Host 已授权的内部显式状态、私有子环境和 SDK 首次 I/O 校验规则，产品行为不再扩大。唯一写入为下列五份测试和新 `docs/acceptance/c-two-final-fixture-repair.md`，不修改 support、产品、角色、注册表或 schema：

- `tests/python/buddy/runtime/test_repair_recovery.py`：复用已有私有子环境构造，不能用整个继承环境覆盖 HOME；原恢复与停止防护保留。
- `tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py`：Host peer 显式传本 fixture 状态根，正常问询签收与停止断言保留。
- `tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py`：活动 peer 复用已有本 fixture 的显式状态接缝，原两项活动刷新断言保留。
- `tests/python/install/test_backup_preflight.py`：先建立夹具环境再取只读业务快照，不能删除快照或“不得写入”的断言。
- `tests/python/protocol/test_transport_attach.py`：按已接受规则区分 0750 可连接与可写端点被 SDK 拒绝；保留无 chmod、只读、路径边界，替换已失效的项目预先拒绝期待并登记编号/断言差异。

首次经路由省略全部四项配置，仍用同一宏任务；不指定 buddy 的返修、同 run 的不可重试限流恢复、唯一写入范围、Worker 不删除、共享 refs/stash 禁令、私有根与无真实模型规则全部保留。仅跑五份受影响测试并核对目标单点变异，Host 固定审查后整合。下一次完整检查只在这一批最终代码上运行，保留两次已失败的完整检查；没有安装或重启日常服务/Worker，没有清扫公共 IPC 或日常状态。

2-F run 为 `cc637748-00eb-4f62-9d0d-90b3aae9230b`，基线 `bd89119a0c5b961016f1d6d269267fcd869c8ac2`，路由决定 `dec-88585fc4-9fc7-4ba7-9b7f-6acafd20ccb4` 选 ZCode/zai-api/GLM-5.3-Flash/max。原生结果记录供应方不可重试 429/1310，原生与外层停止均确认；按用户既有许可在原 run 上 continue 改用已完成本批同类工作的 Codex/openai/gpt-6.1-sol/high。第一次配置恢复缺少独立 reason 字段，被 INVALID_ARGUMENT 拒绝，没有形成新回合；补齐 reason、使用新 commandId 后排队成功，两个原始请求/响应保留。没有改变用户路由配置。记录中的“真实模型冒烟 0 次”只指待用户逐次批准的四个 harness 回合；为微任务选择和运行 buddy 所用模型另按各 run 记录。

## 2-F 固定产物的 Host 独立验证

固定提交 `cd716e41847e42aba9d2e5ca5cba699018064d73`、artifact `f1dc502c-a2df-4565-8a48-16fa23bc7597`、累积补丁 SHA-256 `7a89ebb3e2376a4736792145ba3cb85e723589d8ee083ecd8416e54dadf00b29` 均已核对；六路径没有越界。Worker 的 assistance 与原生沙箱阻断记录原样保留。Host 在固定源码上用独立解释器、空的私有 HOME 与明确状态/运行时根执行五份完整聚焦测试，共 109 项，全部退出 0，没有实际模型调用。

| 文件 | 实际项数 | 测试 / 命令秒 |
| --- | --- | --- |
| `buddy.runtime.test_repair_recovery` | 10 | 41.618 / 42.194 |
| `buddy.harnesses.dsh.test_dsh_role_wiring` | 12 | 6.121 / 6.610 |
| `buddy.harnesses.zcode.test_zcode_inquiry` | 47 | 7.414 / 7.834 |
| `install.test_backup_preflight` | 12 | 1.432 / 2.005 |
| `protocol.test_transport_attach` | 28 | 1.062 / 1.439 |

Host 从同一固定源码重新生成六份只改一处的副本，不使用 Worker 的通过标签作判断：恢复 HOME 继承命中 `daemon overrides must leave HOME`；删除 DSH 与 ZCode 的显式状态根分别命中各自 channel 归属断言；恢复旧快照顺序命中原全快照相等断言；把 SDK 拒绝输入改回 0750 命中 `ServiceError not raised`；把允许输入改成 0770 命中 `0750 must reach the actual SDK peer`。六份都恰好一项 failure、无 errors、退出 1，命令耗时依次 0.355、0.888、0.948、1.145、0.571、0.530 秒。后两份是原生 SDK 输入边界负控，不称为删除了厂商的权限实现；测试确认接受 0750、拒绝 0770/0777，并确认 peer 收件与无 chmod。没有将权限沙箱、任意传输失败或导入错误算为红灯。

原始证据为 `tmp/c073-host/host-2f-{recovery,dsh,zcode,backup,attach}.{json,log}`、`host-2f-target-proofs.json`、六份 `host-2f-red-*.{json,log}`，副本位于本任务私有根。五文件旧编号 108 个全部保留，唯一新增 `protocol.test_transport_attach.RpcReadOnlySetupTests.test_group_readable_ipc_reaches_native_rpc_without_chmod`；整批 loader 为 213 模块、3,474 个编号，3,473 个未变化集合相等，无删除、重复或加载错误。没有改 Worker 的固定记录与范围内代码；本节只登记 Host 的补核事实。

用户明确指定的 `socu/buddy-core@1f7e8b60577cc0b8883aa5dca9e9a1bc392f6ad0` 已合入且祖先核对通过；该分支随后新增两笔文档提交，最新为 `fea30f2964fb15ead6da1a53f8abfc96abc1ee87`，本批按“完整检查前合入最新 core”的原要求合入。增量仅为 Host 维护的 ADR、术语、索引、待办与 AGENTS 文档，没有产品、测试或控制台改动，不冒认本线所写。最终检查将绑定实际实施提交及该 core，保留此前两次完整失败。

2-F 整合提交为 `3fb2c5c60c762a854b2ae1f3aca87c865c3e5cdb`，黑板登记 `int-5c9ab592-259b-4b13-9001-378a585b9206` 为 verified，原 run 已 accepted/completed。清理计划 `cln-ea16e156-765c-4fba-9ff3-2817efc117fc` 通过 eligibility 和两层停止核对，以计划原样给出的 confirmPath 执行，removed=true；只回收该微任务受管检出，固定产物、原始记录和任务根保留供 Host 复核。没有删除其他会话对象或清扫日常目录。

## 第三次完整检查与 Host 临时根修正

`3fb2c5c60c762a854b2ae1f3aca87c865c3e5cdb` / core `fea30f2964fb15ead6da1a53f8abfc96abc1ee87` 的完整检查仍用默认并行数，退出 1、689.527 秒；213 模块中 212 个通过、只有安装版 ZCode 的六项文件失败（3 failures、2 errors）。通过模块计 3,468 项、跳过 1 项；全量 loader 仍为 3,474，不写成全通过。此前两次全检与单文件 green 保留。原始证据为 `final-fixture-check-{started,process,result,summary}.json` 与 `final-fixture-check.log`。

Host 在现有 localhost 响应夹具中只给测试自有 native wrapper 附加 stderr 留存，再使用同样深度的私有检查根复跑。六项文件退出 1、13.376 秒，五处都得到 `Error: listen EINVAL: invalid argument .../tmp/znr-<uuid>.sock`；失败发生在原生 app server 建立会话前。Host 改用开始时创建并登记确切路径的短任务根，仍经检查运行器的 child_environment 构造同样独立的 p019/tmp、HOME、状态及运行时域；同一文件六项通过，退出 0、20.163 秒，native stderr 为空，两个发现用例的 localhost 模型请求为 0，三个回合用例分别 4、3、4 次 localhost 响应，没有真实供应方请求。

这次明确修正的是 Host 自己的忽略目录检查入口：原入口把系统临时根再嵌在较长的 Host 根与 label 之下，导致 ZCode 自建绝对 Unix 套接字路径过长。新的完整入口直接在系统临时目录创建短 `cf-` 任务根，创建时记下确切路径，TMPDIR、BUDDY_CHECKS_TMPDIR、HOME、state、runtime、uv cache 全在该根；不修改产品、测试、期限或并行数。长根 red 与短根 green 证据为 `installed-zcode-final-{long,short}.{json,log}` 及短根 ownership 登记。该定位支持临时路径原因，不把初跑统归为机器负载，也不改写任何一次失败。

再次全检前 core 又增加了纯文档提交 `40d82d937caa03a058b484ff0f660657cd595945`，已合入为 `7c44bba1a5936733daafe40a41deba1cf918194d`；相对上一候选的产品、测试、锁文件与控制台均无变化，指定 `1f7e8b60` 仍为祖先。下一次检查绑定实际最终实施提交和该 core，不改 Host 维护的文档内容。

## 最终完整检查通过与下一验收关口

最终代码提交 `e0ce3d2be333b1655397920d59a8014162730849`，core 绑定 `40d82d937caa03a058b484ff0f660657cd595945`，用户指定 `1f7e8b60577cc0b8883aa5dca9e9a1bc392f6ad0` 的祖先关系通过。命令为 `uv run --frozen python -m hey_my_buddy.cli.checks`，未带 jobs，使用默认并行数；退出码 0，用时 690.917 秒，213/213 个文件通过，实际执行清单 3,474 项（其中跳过 1 项）。完整检查已涵盖本批五份夹具、真实 C-Two 私有注册/回收/期限、四个 harness 的 localhost 或模拟路径、打包与运行时检查。安装版 ZCode 文件在完整检查中通过，22.2 秒；本次没有对其降低断言、超时或检查范围。

本次运行使用创建时登记确切路径的短私有系统任务根；检查运行器自行创建并收尾自己的 `buddy-checks-<nonce>` 根，Host 外层材料留待验收。开始与结束负载分别约 20.81 / 83.46，仅为运行条件观察，不用作通过或失败解释。原始证据为 `tmp/c073-host/final-short-check-{started,process,result,summary,owned-root}.json` 与 `final-short-check.log`。前三次完整失败全部保留，没有用最终 green 覆盖原结果。记录后的产品、测试、锁文件与控制台差集核对为空；只补记录时不重跑完整检查，仍重跑 `cli.test_repository_hygiene`。

四个最短真实 Worker 回合的脚本与独立批准门已准备，仍未执行（真实付费冒烟 0 次；各微任务委派的模型使用另按各 run 登记）。准备从一次 Codex Worker 回合开始，证明真实 CLI 经私有服务、Worker 主循环、角色、运行模块与 C-Two 通道完成最小交付，留存签收、工具事实与两层停止；其余 ZCode、Claude Code、DSH 及任何重跑都继续逐次请求用户批准。沿用已有登录，不设置 CODEX_HOME 或 CLAUDE_CONFIG_DIR，不读凭据文件，不安装、升级或重启日常运行时。真实冒烟与 Claude Code Host 的最终验收仍是后续关口，不把完整检查退出 0 写成整批已验收。

## 用户单次批准的 Codex 真实 Worker 冒烟

用户明确回复“批准这一次 Codex 回合”后，Host 才生成与配置、脚本哈希、准备文件、nonce 和确切根绑定的单次批准文件。源提交 `80916e3eb499b9768d90fd7873de8b5e5a7424ab`，与完整检查候选的生产代码相同；实际调用 Codex/openai/gpt-6.1-sol/low，一个 Worker 回合、没有重跑。原生版本 `codex-cli 0.160.1`，回合命令退出 0、32.542 秒。真实角色结果 ok/completed，私有黑板 run `18c33384-8894-4e86-8bc8-412e1f9d00db` 的固定 artifact `551bdc1e-f168-4ddf-baa5-57e2e77b315a` 已 accepted/completed，签收前状态为 delivered；没有以 RPC 排队响应代替交付。

工具事实 streamComplete=true、toolCalls=0、unsettledToolCalls=0、truncated=false，绑定实际 task/attempt/native session/turn；本探针请求没有任何文件或命令工作，通过既有 Codex 结构化交付接缝完成。控制器 shutdownConfirmed=true、wait 退出 0，原生 stopEvidence.native.groupState=gone；角色的 processState.shutdownConfirmed=true。模型用量来源为 Codex app-server，nativeRecords=1、inputTokens=17,850、cachedInputTokens=0、outputTokens=41、reasoningOutputTokens=0。只报告原生 token 事实，不推算费用。

持有的 Worker 与私有服务均停止，C-Two shutdown.completed=true；服务、Worker、控制器三个实际地址 inspect 均 absent，私有 state/ipc 套接字残留集合为空，没有手工 unlink。证据为 `tmp/c073-host/codex-approved-smoke-{started,result,proof}.json`、`codex-approved-smoke.log` 及 `<CODEX_SMOKE_ROOT>` 中的 run-request、run-result、round、delivered-view、acknowledgment、stop-and-endpoints 与固定原生 evidence；不在记录里写出凭据或完整主目录。没有设置 CODEX_HOME/CLAUDE_CONFIG_DIR，没有读取登录凭据文件内容或操作日常服务/Worker。

结合已定位的 ZCode 临时套接字长度条件，Host 将尚未运行的其余三个入口准备在开始时创建并登记确切路径的短私有系统任务根，脚本逐字节哈希不变；只重做 prepare，modelCalls=0、nativeStarts=0，旧准备材料保留、没有消耗旧批准门。三个真实回合仍各待用户批准，Codex 的这一次授权不延伸到其他 harness 或重跑。

## 用户单次批准的 Claude Code 真实 Worker 冒烟

用户另行回复“批准这一次 Claude 回合”后，Host 仅执行 Claude/anthropic/claude-haiku-4-5-20251001/default 的一次 Worker 回合，没有重跑。源提交 `624fe4bb4358dd0aa677b8ce145b0c62d861b185`，与完整检查候选的生产代码相同；原生版本 `2.1.284 (Claude Code)`，命令退出 0、27.764 秒。实际角色结果 ok/completed，私有 run `45a805b1-6d99-4e91-b539-8e2f71a60496` 的 artifact `bce91ab6-5e03-4cde-b69b-62c3a3d2e1a1` 已 accepted/completed，签收前真实交付为 delivered。

工具事实 streamComplete=true、toolCalls=0、unsettledToolCalls=0、truncated=false，绑定实际 task/attempt/session；结构化交付沿用既有角色接缝。控制器 shutdownConfirmed=true、退出 0，原生 groupState=gone、nativeExitCode=0，角色两层停止确认。stream-json 用量事实 nativeRecords=1、inputTokens=17,694（含 cachedInputTokens=17,684）、outputTokens=409、reasoningOutputTokens=307。Worker 与私有服务停止、SDK shutdown.completed=true，三个实际端点均 absent，socketResiduals=[]。

原始证据为 `tmp/c073-host/claude-approved-smoke-{started,result,proof}.json` 与 `claude-approved-smoke.log`，固定 run/result/交付/签收/端点材料保留在 `<CLAUDE_SMOKE_ROOT>`；没有设置 CLAUDE_CONFIG_DIR 或 CODEX_HOME，没有改登录或读凭据文件。真实冒烟累计为 Codex 1 次、Claude Code 1 次，DSH 与 ZCode 仍未运行，继续各自请求批准；这一记录更正没有代码变化，按既有规则只重跑仓库卫生检查。

## 用户单次批准的 DSH 真实 Worker 冒烟

用户另行回复“批准这一次 DSH 回合”后，仅运行 DSH/deepseek-official/deepseek-v4-flash/off 的一个 Worker 回合，没有重跑。源提交 `6f1aa5003917e53d6cf38a5f62e54dc3a80ba311`，生产代码与完整检查候选相同。经已安装 DSH 的 ACP 与现有角色、运行模块、C-Two 通道，命令退出 0、9.616 秒，角色 ok/completed，私有 run `5c03e3a1-7e65-40ab-8e32-fe95c32ee346` 的 artifact `bfd16765-afd5-4cd2-b618-b9f3f8fec591` 已 accepted/completed，签收前状态 delivered；运行模块的 harnessVersion 如实报告 `0.0.1`。

工具事实 streamComplete=true、toolCalls=0、unsettledToolCalls=0、truncated=false，绑定实际 task/attempt/session。控制器停止确认、退出 0，原生 groupState=gone、nativeExitCode=0，角色两层停止确认；Worker、私有服务与 SDK 也确认停止。三个实际端点均 absent，socketResiduals=[]，没有项目自行推算套接字路径并删除。私有会话用量 source=dsh/session-record、scope=attempt、completeness=complete、nativeRecords=2、inputTokens=22,737（含 cachedInputTokens=12,032）、outputTokens=150；这里只记录本回合原生报告，没有把多回合累计数当作本回合。

证据为 `tmp/c073-host/dsh-approved-smoke-{started,result,proof}.json`、`dsh-approved-smoke.log` 与 `<DSH_SMOKE_ROOT>` 中的固定交付/签收/用量/停止/端点材料。使用产品既有私有 DSH_HOME 与启动配置，没有改用户交互使用的 DSH、登录或凭据。真实冒烟累计为 Codex、Claude Code、DSH 各一次，ZCode 仍未调用，须另行批准。

## 提交整步验收的最终状态

用户对最后一次 ZCode 付费冒烟明确回复“zcode没额度了，这个暂时不调用模型”。本批 ZCode 真实供应方回合为 0 次，因额度不足记为未验证；没有运行已准备入口、改变配置或自动重试。localhost 安装版测试与真实模型回合是不同验证边界，前者已在短根完整检查通过，不能替代后者。真实付费 Worker 冒烟总计 3 次：Codex、Claude Code、DSH 各一次且全通过，没有重跑。

最终产品、测试与锁文件仍与全检提交 `e0ce3d2b` 相同；全检 3,474 项（跳过 1）、213/213 文件、690.917 秒、退出 0 的绑定及三次失败原始证据不变。后续只修改记录，仓库卫生检查重跑，不重复全检；控制台相对已合入 core 没有改动，不另跑前端测试。本分支不推送、不安装或升级日常运行时，到这里停止，等 Claude Code Host 整步验收。

已签收微任务的受管检出通过各自 cleanup-plan/apply 回收，记录中的确切路径与保留固定产物可复核。Host 私有根、短根原生冒烟/检查证据，以及 2-D、2-F 原始失败对应任务根留给整步复核；没有因为后来 green 改写它们曾经的 unknown 停止证据，也没有按相同名称、前缀或日期推断并清扫别的会话对象。后续回收只按创建时台账与已登记的确切根进行。日常 state/ipc 的先前越界残留、公共默认端点命名空间仍未触碰。
