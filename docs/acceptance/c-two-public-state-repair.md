# C-Two 0.7.4 公开默认状态目录返修：夹具 continue 待补核

## 本次 continue：修复 Host 退回的两类夹具缺陷

原 run `ef9677e7-df3e-4f57-b03b-4c7add95b85f` 继续，当前 attempt `8b484a83-f0d8-47b0-a2fe-36d9f3dbf14d`。Host 明确拒绝固定候选 `cb33c21792bee0c589dfeaaeefd50595951ac713`，不是签收：真实 `protocol.test_public_state` 运行 22 项、3 failures、62.348 秒；真实 attach 28 项与 rpc_config 24 项均通过。Host 回报生产修复的默认 CLI、源码启动器、公开客户端等真实路径已通过，失败集中在本任务新增 fixture。此处引用 Host continue 的回报，没有把它冒充本轮 Worker 复跑；原始 Host 失败保留在 Host 台账，当前输入没有给出该台账的文件路径，Worker 未猜测或改写其他检出。

两类失败均属本任务范围：cold-service / cold-board 替换 Popen 命令后，生产子环境只带 src PYTHONPATH，新助手在 import support 时报 ModuleNotFoundError；readonly fixture 把 state 改为 0500 后请求 health，真实 SQLite 访问失败，又因恢复 chmod 位于 try 内而跳过恢复，旧 finally 的 stop 失败并跳过 wait，留下 Host 创建的 daemon PID 32358。旧 finally 没有保证全部 wait，不能以旧 Worker 的 27 个已记录回执覆盖这个 Host 实例。

Host 已只在自己创建、endpoint.serviceId/PID 一致的确切状态目录恢复 0700，显式 stop helper 退出 0、服务锁释放；初次随后检查 PID 仍在，Host 在 continue 时仍继续确认最终收尾。本轮 Worker 没有查询、chmod、stop 或接管 PID 32358，也没有把释放服务锁等同于该进程已经 wait。原失败、未回收实例与这次 Host 操作均保留，最终进程退出仍需 Host 补充证据。

本轮只返修 `tests/python/protocol/test_public_state.py` 和本记录。两个生产文件与 `cb33c217` 字节一致，原 attach 文件没有修改；基线 `9e45e890`、core 冻结 `12eb4fcd`、五路径范围、无手工删除、无 refs/stash 操作等规则继续有效。没有重试已受沙箱阻止的真实服务监听或原生 register，没有完整检查、前端或真实模型。

冷启动替换分支通过测试自己的 `prepare_daemon_spawn` 明确加入当前源码的 tests/python 和 src，并保留原 PYTHONPATH、私有 catalog/facts/checks pins 和字节码禁写设置。生产 launcher、service_environment 和 support 未改。新增 `--import-check` 助手在实际只含 src 的生产 allowlist 子环境中执行完整模块导入并核对目录 pins，在进入 daemon/register 前结束；它验证 import 接线，不是实际 RPC 或冷启动 green。

readonly 场景维持自有 state 的 0700，复用原 `test_transport_attach.MutationRecorder` 包围默认 BoardClient(False).ping()，比较 state 的既有 entry snapshot 与 state/ipc 权限模式前后相等，并由真实 ping 的 serviceId、已就绪 health 的 stateDir、endpoint PID 和 nativeRoot 绑定同一个服务。ping 不执行 SQLite 扫描，避免把 live SQLite 的写需求作为公开只读 attach 的结论。该分支的 connect 和服务响应仍是真实 RPC；本轮仅验证了故障注入收尾，真实分支等待 Host 复跑，旧 0500/unsafe/owner 防护测试全部保留。

新 `cleanup_owned_case` 在 finally 先恢复自己创建且仍为自有真实目录的已记录权限，仅在模式确实变化时 chmod，再分别尝试 stop_service、stop_workers、每个 Popen.wait 和 RPC shutdown。一项失败不会跳过后续项；次级失败在 cleanupFailures 中保存，未知 wait 标为 shutdownUnconfirmed。有目标错误时保留原异常及错误码，没有目标错误而收尾失败时仍使测试失败。日志关闭/证据写入的失败也不覆盖既有目标异常。没有给内部 RPC 添加默认根或改停止策略。

当前固定源码位于本轮新建且保留的确切短任务根 `/private/tmp/ps3b-21te13_d/fixed/`，哈希为：

| 路径 | SHA256 |
| --- | --- |
| `src/hey_my_buddy/protocol/transport.py` | `e5030a9108888c64824b7d15bd168c388f8e06d57c79ce55538ccc0de542c183` |
| `src/hey_my_buddy/protocol/client.py` | `4f6cb9a6b6d6c4a940a8ffef1410f1b0307f2a83a9c1f4d136a4b2b7b253b172` |
| `tests/python/protocol/test_public_state.py` | `e45c5e1e07101dc08e4518ee6b73d23209a32fd64eea4985c9d190ef1eafdbd1` |

创建时记录的任务根是 `/private/tmp/ps3b-21te13_d`，上一根 `/private/tmp/ps3a-34r52t5y` 及其材料保留。重新核对公开材料的全部 18 个 SHA256、检出锁与材料锁相等，再复制到新根 materials；uv 创建私有 CPython 3.13.11 环境和离线 require-hashes 同步 10 个依赖均退出 0，耗时 0.232 / 0.436 秒。TMPDIR、BUDDY_CHECKS_TMPDIR、HOME、uv 环境/cache、比较/变异与一次性材料均指入新根。`run.py` 清除继承 BUDDY_*、ANTHROPIC_*、C2_*、VIRTUAL_ENV、UV_PROJECT_ENVIRONMENT，使用 UV_OFFLINE=1；实际命令为 `uv run --frozen --no-sync --python "$TASK_ROOT/venv/bin/python" python -m unittest -v <固定编号>`，所有 argv 与结果保存在同名 json/log。

| 本轮命令选择 / 证据名 | 项数 | 退出码 | 外层用时（秒） | 实际边界 |
| --- | ---: | ---: | ---: | --- |
| `fixture-repair` | 4 | 1 | 5.520 | 首次本地补充测试 3 errors；Mock Popen 与 MutationRecorder 的 autospec 重叠，随后局部 mock 未完成进入导致另外两项报错；原日志保留，不算目标变异 |
| `fixture-repair-corrected` | 4 | 0 | 5.320 | 改为普通函数替身避免重叠，不修改公共 MutationRecorder；故障注入通过 |
| `public-repair-final` | 13 | 0 | 10.382 | 最终源码原 9 个可运行项、新 import 子进程与 3 个收尾故障项 |
| `mutant-call-service` | 1 | 1 | 3.071 | 实际 failure.code=PRIVATE_STATE_REQUIRED |
| `mutant-call-board` | 1 | 1 | 3.222 | 实际 failure.code=PRIVATE_STATE_REQUIRED |
| `mutant-readonly-client` | 1 | 1 | 2.723 | 实际 failure.code=PRIVATE_STATE_REQUIRED |
| `fixture-mutant-import` | 1 | 1 | 2.511 | 单点取消测试 PYTHONPATH 传递，实际子进程 ModuleNotFoundError: No module named 'support' |
| `fixture-mutant-restore` | 1 | 1 | 1.288 | 单点取消 finally 权限恢复，当前目标检查实际目录仍为 0500，320 != 448 |

最终 13 项通过不是新文件 26 项全量通过。收尾单元项明确标 substitutesOnly=true，PID 使用 substitute-handle，未启动真实 daemon、Worker 或 native RPC；它们确认请求失败后权限已在 stop 前恢复、stop 再失败时仍调用 wait 并保留同一个原异常、没有主错误时次级收尾失败不能假通过。两份 fixture 变异仅证明新增助手及收尾回归，导入/权限错误绝不作为 PS-08 的缺根 red。三份生产目录变异与本轮最终测试绑定，都是各取消一条目录传递，目标未改且实际保存 PRIVATE_STATE_REQUIRED；真实 RPC 目标变异仍等待 Host。

本轮新增 4 个静态编号，cb33 的原 22 个 public-state 编号全部保留，当前新文件共 26 个；对原基线共新增 26，原 3412 个定义保持、当前总定义 3438。完整本轮差集在 test-id-diff.json，原 22 个新增编号仍见下文历史登记。本轮四个新增编号为：

| 新编号（protocol.test_public_state 下） | 本轮结果 |
| --- | --- |
| `PublicStateTests.test_ps06_cold_helper_imports_with_source_only_environment` | 实际 import 子进程通过；取消路径传递后失败 |
| `FixtureRepairTests.test_ps05_request_failure_restores_permissions_before_stop_and_wait` | 通过；取消 finally 恢复后失败 |
| `FixtureRepairTests.test_ps05_stop_failure_keeps_primary_error_and_still_waits` | 通过；原请求异常对象保留，次级 stop 错误单独记录 |
| `FixtureRepairTests.test_ps05_cleanup_failure_without_target_is_reported` | 通过；无主错误的收尾失败仍导致场景失败 |

本轮没有创建真实 daemon 或 Worker，实际执行的是 unittest/import 场景和解释器助手；process-manifest.json 汇总 23 个具有真实整数 PID 的进程回执，全部已获得退出码，包括预期失败的变异进程。早期 setup 与前三次 test runner 只保留 subprocess.run 等待完成的命令/退出码，没有补造 PID；unit 的 substitute-handle 不计入真实进程台账。原 Host 实例 PID 32358 的最终 wait 不在本台账，仍待 Host 确认。

最终 `git diff --check`、源码/固定副本哈希、cb33 生产与 attach 字节对照、语法/空白/路径、编号登记和真实进程回执核对均退出 0，结果保存在本轮 final-hygiene.json。随后只补写本条卫生记录并再次检查空白，没有重复原生或广泛测试。

本轮根保留 `home/`、`venv/`、`uv-cache/`、`materials/`、`fixed/`、三份 `mutant-*`、两份 `fixture-mutant-*`、所有 `public-state-*` 与 `cleanup-unit-*`、run.py、原始日志及哈希/编号/变异/进程台账。权限负向变异留下确切自有目录 `/private/tmp/ps3b-21te13_d/cleanup-unit-2dodtin8/home/.local/share/hey-my-buddy/state` 为 0500，其中没有真实 daemon，记录在 permission-residuals.json，未手工删除或掩盖。两个任务根均由 Host 验收后按确切路径回收。

请 Host 在允许本地监听与 C-Two 原生 I/O 的环境，按固定源码先复跑 `PublicStateTests.test_ps06_cold_start_probe_and_request_share_default` 的两个域与 `PublicStateTests.test_ps05_readonly_attach_preserves_permissions`，再对最终新文件每文件独立解释器执行 `python -m unittest -fv protocol.test_public_state`（26 项，固定私有模型目录和四个 sentinel）。保存真实 stateDir/serviceId/nativeRoot、所有自有 Popen stop/wait、snapshot/权限前后证据；不要把单元替身当真实 RPC。按本轮 mutation-manifest.json 三份副本分别补核原三个真实 PS-02 目标变异，要求各实际命中 PRIVATE_STATE_REQUIRED。原 attach 28 与 rpc_config 24 的 Host green 绑定 cb33 且源码未变，本轮不因 fixture/记录变化重复它们；原 PID 32358 的最终收尾证据另由 Host 补充。需要公共接线则 Host 处理，范围内缺陷仍退回同 run continue。

## 上一回合 cb33c217 历史报告（已退回，不是当前验收）

以下保留上一回合证据、编号和未验证声明，其状态截止 cb33c217 交付；当前结果和 Host 退回/操作以本次 continue 节为准。特别是旧 finally 的全量 wait 保证已被真实失败否定，上一根的已记录回执不能证明 Host 后续实例的收尾。

微任务 3-A、run `ef9677e7-df3e-4f57-b03b-4c7add95b85f`、attempt `e35a7e41-c005-4da1-86a4-26d2e942d156` 的候选修复已留在授权检出，尚未通过微任务验收。本次基线为 `9e45e890e0c249250621a12d165001c9c76d7e8e`，core 冻结 `12eb4fcd`；遵循 [执行计划](c-two-public-state-plan.md)，没有合并 core、提交、操作分支/标签/stash、修改日常安装或调用真实模型。原 1-A accepted/completed rev20 与原产物 `5c67e96f-90da-4bff-b201-18515aec15ac` / `552ea251` 保留。

生产改动只有两个文件中的三处公开调用边界。最终可在本沙箱运行的 50 项聚焦回归通过；三份单点变异和基线参数对照均实际命中 `PRIVATE_STATE_REQUIRED`。真实服务首次启动在 console 本地监听处被沙箱阻止，未获得真实 RPC 成功证据，后续没有重复真实启动；本记录不能作为 PS-02..06 或真实 PS-08 的绿灯。

## 固定源码与风险接缝

`src/hey_my_buddy/protocol/transport.py` 的 `call_service` 在本地参数验证后用既有 `get_state_dir` 解析目录，向普通调用的 `ensure_service`、stop/restart 的只读 attach、最终 `_request` 传同一个 `directory`；`call_board` 对普通启动和显式 endpoint 路径都向实际请求传该结果。`ensure_service` 自己既有的解析与校验继续保留，收到的是已选定的显式目录。

`src/hey_my_buddy/protocol/client.py` 的 `BoardClient(autostart=False)` 在调用时解析原 `self.state_dir`，将结果同时交给 attach 和 `_request`。构造器仍存原参数，注入 call 仍先短路，autostart=True 仍复用 `call_board`。没有新增解析器、动态生产导入或兼容层，没有改内部 `_request`、`rpc_config`、Worker、控制器的缺根拒绝。

未改 `tests/python/protocol/test_transport_attach.py`，全部旧断言和保护保留。新增 `tests/python/protocol/test_public_state.py` 与本记录。唯一写入范围中的第五个文件不需要适配。CLI、启动器、schema、版本、角色、注册表、模型、停止策略及权限策略无源码改动；后续跨侧引用清理未执行。

固定候选的 SHA256 见任务根 `final-source-sha256.json`，与 `fixed-v2/` 源码副本相同：

| 路径 | SHA256 |
| --- | --- |
| `src/hey_my_buddy/protocol/transport.py` | `e5030a9108888c64824b7d15bd168c388f8e06d57c79ce55538ccc0de542c183` |
| `src/hey_my_buddy/protocol/client.py` | `4f6cb9a6b6d6c4a940a8ffef1410f1b0307f2a83a9c1f4d136a4b2b7b253b172` |
| `tests/python/protocol/test_public_state.py` | `9a64dcf1387806964af6bf8bafd573686bc640c28aed0a92ffa611bea3b220d4` |

风险集中在未能真实复核的默认目录请求、环境覆盖时机与服务生命周期接缝，不能从参数替身测试推断真实连接可用。生产补丁没有扩大内部默认路径策略；路径、owner 和只读保护的可运行旧断言通过，真实 SDK 访问拒绝和活动域 fencing 项仍待补核。

## 私有环境、固定助手与公共接线边界

创建时记录的确切系统临时任务根为 `/private/tmp/ps3a-34r52t5y`。TMPDIR、BUDDY_CHECKS_TMPDIR、uv 环境、uv cache、私有 HOME、比较/变异源码及一次性材料均位于该根。没有手工删除任何文件或目录；已有测试框架的 TemporaryDirectory 收尾仍按原实现运行，新 public-state 场景保留目录供 Host 审查。

先核对 `/tmp/c073-h-x6lhuldm/delegate-materials/provenance.json` 中全部 18 个材料的 SHA256，再复制到任务根 `materials/`。检出 `uv.lock` 与公开锁材料同为 `5ae675829eb4c9aca5e2c933b86799abd3ab9a979e80f8d00c074adf5e27cdf8`，C-Two 0.7.4 wheel 为 `bb625650f186c6066992cac0948c0f44b95a22af46778c8e63bba30d80ee88b8`；完整核对表为 `materials-verified.json`。用 uv 在任务根创建 CPython 3.13.11 环境，再以 `uv pip sync --require-hashes --no-index --find-links "$TASK_ROOT/materials/wheelhouse"` 安装固定的 10 个锁定依赖，两个步骤均退出 0，耗时分别 0.547 / 0.774 秒。未安装项目到日常 runtime。

`run.py` 是任务根内的固定执行助手：从 `<checkout>` 工作目录运行，清除继承 BUDDY_*、ANTHROPIC_*、C2_*、VIRTUAL_ENV 和 UV_PROJECT_ENVIRONMENT，再只设私有测试变量、PYTHONPATH、UV_OFFLINE=1、PYTHONDONTWRITEBYTECODE=1。最终测试命令为 `uv run --frozen --no-sync --python "$TASK_ROOT/venv/bin/python" python -m unittest -v <固定测试编号>`；每个被测文件使用独立解释器，新测试每个状态域再进入独立私有子进程，不在活动 C-Two 域间切换。

新测试复用 `support._child_environment`、`write_catalog_fixture`、`stop_private_service`、`stop_private_workers`、`shutdown_private_rpc` 和 `blackboard/service/fixtures/daemon_with_catalog.py`。默认目录场景在私有 HOME 下不设 BUDDY_STATE_DIR，daemon 明确启动到该 HOME 默认目录，模型目录文件始终位于该状态树。四个原生 CLI 都指向私有 sentinel，调用 sentinel 会留痕并退出 99。没有读登录文件或凭据内容，没有真实账户发现、模型请求或另一个 Buddy 任务。

固定测试助手的 `--daemon-fixture` 仅在测试中禁用 daemon 的 Worker 池启动和协调，以免传输探针生成 Worker；通过 runpy 复用已有 catalog daemon，C-Two register/connect、服务操作和响应没有被替换。冷启动测试只替换 Popen 的 daemon 命令为该固定助手，保留原参数、服务环境和生产 preflight/attach/request；额外显式保留私有 BUDDY_MODEL_FACTS_FILE 与 BUDDY_CHECKS_TMPDIR。现有 launcher 的环境 allowlist 不携带这两个 fixture 变量，普通 daemon 也不会自动启用 catalog daemon 的合成 harness 健康记录；若需要将此测试接线公共化，由 Host 统一修改 fixture/support/launcher，本任务没有扩大这些文件范围。此 fixture 边界不证明普通原生发现路径或安装路径。

## 实际验证与原始失败

首次真实探针编号为 `protocol.test_public_state.PublicStateTests.test_ps02_default_call_service`，当时测试助手尚未增加禁用 Worker 池的入口。外层退出 1、5.667 秒、1 项失败；原始 `initial-real.log` 和 `public-state-in8uboi6/daemon.log` 保留。自建 daemon PID 2016 在 endpoint 发布前失败，原始机制为 `buddy: CONSOLE_PORT_IN_USE: Cannot bind 127.0.0.1:0; free the port or configure BUDDY_CONSOLE_PORT`，持有的 Popen 已 wait，退出 2。当时收尾断言另报 waitExit=2，原错误仍在日志；随后测试收尾调整为保存原失败而不以退出断言遮蔽它。没有以这次权限/监听失败作为缺根变异红灯，没有重复服务启动，也没有绕过 console 监听去重试 register。

当前检出未包含 `tmp/c073-host/default-state-red-owned-root.json`，没有猜测或修改其他检出。只读查看了 `git show 8337b27f:docs/acceptance/c-two-074-host-review.md`：该 Host 记录给出默认 CLI、源码启动器、BoardClient(False) 三项真实 `PRIVATE_STATE_REQUIRED` 红灯，显式状态、公开解析对照与自有服务停止为绿灯。它是已有 Host 证据，不是本次 Worker 复跑结果；本次 PS-01 的独立真实重放仍需 Host。

以下是最终生产源码与最终测试对应的有效聚焦结果。测试命令完整 argv、选择的编号、退出码、用时和日志分别保存在同名 `.json` / `.log`，文件选择表为 `safe-test-selections.json`。

| 命令选择 / 证据名 | 项数 | 退出码 | 外层用时（秒） | 边界 |
| --- | ---: | ---: | ---: | --- |
| `public-final`，新文件的 9 个可在沙箱运行的编号 | 9 | 0 | 6.791 | 无服务 stop/restart、只读缺目录、注入 call、真实内部缺根/路径校验、4 个参数接缝替身 |
| `test_transport_attach-focused` | 26 | 0 | 1.569 | 原 28 项中未启动 native peer 的 26 项；保护断言未改 |
| `test_rpc_config-focused` | 15 | 0 | 0.974 | ProfileTests 8 项与 DaemonFixtureTests 7 项；未运行 register/活动连接项 |
| `initial-real` | 1 | 1 | 5.667 | 上述 console 监听受限；不是成功 RPC |
| `mutant-v2-call-service` | 1 | 1 | 1.861 | 实际错误码 PRIVATE_STATE_REQUIRED |
| `mutant-v2-call-board` | 1 | 1 | 1.657 | 实际错误码 PRIVATE_STATE_REQUIRED |
| `mutant-v2-readonly-client` | 1 | 1 | 1.740 | 实际错误码 PRIVATE_STATE_REQUIRED |
| `baseline-v2-parameters` | 3 | 1 | 3.478 | 三个目标均实际错误码 PRIVATE_STATE_REQUIRED |

最终通过的是 50 个不同编号；没有报告新文件 22 项全量通过，也没有报告旧 attach 28 项、rpc_config 24 项全量通过。早期 `public-nonnative` 4 项、`public-parameters` 4 项、`public-final-safe` 9 项，以及第一版三份变异/基线副本的全部结果保留在 `run-results.json` 和原日志中，不能与最终不同编号相加宣称更多覆盖。没有运行完整检查、前端或真实模型。

最后执行 `git diff --check` 与受限范围/语法/空白/路径/源码哈希/编号登记/进程回执核对，退出 0；结果保存在 `final-hygiene.json`。确认改动只有四个授权路径、固定副本和工作区生产/测试哈希相等、27 个已记录进程都有退出码、sentinel 调用记录为 0。随后只补写本条卫生记录并再次检查空白，没有因此重复测试。

## 单点变异与基线对照

`fixed-v2/` 是上述哈希绑定的固定源码副本。三份独立 `mutant-v2-*` 副本各只取消一条最终请求的解析目录传递：call_service 的 `_request(...state_dir=directory)` 改回 `state_dir=state_dir`；call_board 的同类传递改回原参数；BoardClient(False) 的传递改回 `state_dir=self.state_dir`。没有改探测、冷启动、内部校验或目标测试断言。`baseline-v2/` 的两个生产文件来自 `git show 9e45e890:<路径>`，测试使用最终新增文件。

三个当前参数目标分别为 `test_ps08_call_service_directory_parameter`、`test_ps08_call_board_directory_parameter`、`test_ps08_readonly_client_directory_parameter`。最终源码三项通过，单点取消对应目录后各自失败，基线副本三项均失败；场景证据实际记录 `failure.code=PRIVATE_STATE_REQUIRED`、`requests[0].state="None"`、`shutdownCompleted=true`，原始 traceback 保留。没有把导入、SDK 任意错误、权限、超时作为变异证据。

这些目标替换了 native connect 和 attach 的响应，但 `_request`、configure_client 与缺根检查实际执行；它们只证明目录参数回归。本次没有真实 RPC green，因此真实目标 `test_ps02_default_call_service`、`test_ps02_default_call_board`、`test_ps02_default_board_client_readonly` 的三份变异仍待 Host。固定副本、替换文本、参数目标和真实目标在 `final-mutation-manifest.json`，第一版材料全部保留。

## 编号差集与 PS 验证状态

对 `tests/python` 所有 test_*.py 以 AST 比较基线与当前定义的类/方法编号，未导入或执行全套测试：基线 3412、当前 3434、未变化 3412、删除 0、新增 22。此处是静态定义数，不是运行器展开继承和 subTest 后的运行项数；原 attach 的 28 个和 rpc_config 的 24 个定义全部相等。机器可读完整差集为 `test-id-diff.json`。

新增编号均在 `protocol.test_public_state.PublicStateTests` 下，逐项如下：

| 新编号 | 当前验证状态 |
| --- | --- |
| `test_ps02_default_cli` | 真实 RPC 待 Host |
| `test_ps02_source_launcher` | 真实 RPC 待 Host |
| `test_ps02_default_call_service` | 首次监听受限失败；最终 fixture 的真实 RPC 待 Host |
| `test_ps02_default_call_board` | 真实 RPC 待 Host |
| `test_ps02_default_board_client_autostart` | 真实 RPC 待 Host |
| `test_ps02_default_board_client_readonly` | 真实 RPC 待 Host |
| `test_ps03_default_stop_owned_service` | 已有服务真实 RPC / wait 待 Host |
| `test_ps03_default_restart_owned_service` | 已有服务真实 RPC / resume 身份 / wait 待 Host |
| `test_ps03_no_service_stop_and_restart_do_not_start` | 通过，默认状态未创建，无冷启动 |
| `test_ps04_explicit_precedes_environment` | 3 个独立域真实身份核对待 Host |
| `test_ps04_environment_precedes_default` | 3 个独立域真实身份核对待 Host |
| `test_ps04_environment_selected_after_construction` | auto/readonly 两个独立域真实核对待 Host |
| `test_ps04_injected_call_does_not_resolve_state` | 通过，注入短路保留 |
| `test_ps05_missing_readonly_client_never_creates_or_starts` | 通过，control/wait 均无冷启动 |
| `test_ps05_readonly_attach_preserves_permissions` | 真实 RPC 待 Host；旧只读防护项已通过 |
| `test_ps06_cold_start_probe_and_request_share_default` | service/board 两个独立域真实冷启动待 Host |
| `test_ps06_supplied_endpoint_keeps_default_directory` | 参数接缝通过，connect 替身 |
| `test_ps07_internal_request_still_requires_private_root` | 通过，缺根在 connect 前拒绝 |
| `test_ps07_public_resolution_preserves_path_guards` | 通过，symlink / .. 在 connect 前拒绝 |
| `test_ps08_call_service_directory_parameter` | 参数 green 与单点 PRIVATE_STATE_REQUIRED red 通过 |
| `test_ps08_call_board_directory_parameter` | 参数 green 与单点 PRIVATE_STATE_REQUIRED red 通过 |
| `test_ps08_readonly_client_directory_parameter` | 参数 green 与单点 PRIVATE_STATE_REQUIRED red 通过 |

PS-01 保留已有 Host 真红灯及本次基线参数红灯，独立真实重放未完成；PS-02 未验证；PS-03 只有无服务分支通过；PS-04 只有注入短路通过；PS-05 缺服务和原只读替身防护通过，真实只读连接未验证；PS-06 只有显式 endpoint 参数证明，真实冷启动未验证；PS-07 可运行缺根/路径/owner/只读断言通过，原生访问与活动域防护未复跑；PS-08 只有参数变异完成，真实变异待核；PS-09 编号核对完成，整合最终全检由 Host 执行。

## Host 所需补核与残留

请 Host 在允许私有 TCP console 监听、Unix IPC 和 C-Two register/connect 的执行环境补核固定候选，不改日常服务。先保留三项默认入口基线真实红灯/公开解析对照；对固定候选每文件独立解释器运行 `uv run --frozen --no-sync --python "$TASK_ROOT/venv/bin/python" python -m unittest -fv protocol.test_public_state`、`protocol.test_transport_attach`、`protocol.test_rpc_config`，出现监听/register 受限或 Too many open files 即停止。全部测试 HOME/state/runtime 使用新的短私有根，保留模型目录夹具与四个 sentinel。

然后分别在 `final-mutation-manifest.json` 指定的三份副本中仅运行对应真实目标：call-service → `test_ps02_default_call_service`、call-board → `test_ps02_default_call_board`、readonly-client → `test_ps02_default_board_client_readonly`。要求固定源码真实 RPC 成功、三个真实变异均命中 PRIVATE_STATE_REQUIRED；保存 CLI/launcher exit、stateDir、serviceId、nativeRoot、daemon PID、stop/restart 回复和每个自建进程 wait。若固定测试发现范围内缺陷，退回同 run continue；需要公共 fixture/launcher 接线则由 Host 统一处理。

唯一自建真实 daemon 为 PID 2016，wait 退出 2，没有发布服务 endpoint，也没有到达 Worker 池启动。新测试最终版禁止生成 Worker。新场景子进程的 PID/命令/waitExit 在 `public-state-*/case-process.json`，对应 RPC/缺根/路径与停止证据在 `evidence.json`；汇总为 `process-manifest.json`，记录的 27 个进程回执均已得到退出码（变异/基线场景退出 1 为预期）。早期助手未单列场景 PID，仅保留 subprocess.run 等待完成的外层命令/退出码和场景 stdout/stderr，不声称早期 PID 台账完整。没有未确认仍运行的自建服务或场景进程。

任务根全部保留：`home/`、`venv/`、`uv-cache/`、`materials/`、`fixed/`、`fixed-v2/`、`baseline/`、`baseline-v2/`、两版六份 `mutant-*` 副本、所有 `public-state-*` 私有场景、`run.py`、原始日志、哈希/编号/进程/结果/变异台账。验收后由 Host 按确切根 `/private/tmp/ps3a-34r52t5y` 回收；Worker 未手工清扫或移除 Buddy 管理检出。
