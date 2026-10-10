# 微任务 2-C：七份 RPC 测试夹具适配（R-07，待 Host 原生补核）

基线为 `57837f2ff9bf4ae310618f88cc731057ae2692d7`。本轮只修改下列七份测试、`tests/python/support.py` 和本记录；没有修改产品源码、公共接线、契约、schema、停止证据、工具行为或角色判定，没有提交、操作 refs/stash、手工删除文件或目录。实际验证边界为七个模块共 84 项，其中 83 项通过，真实 daemon 健康测试受 socket 沙箱限制；不能据此声称原生端到端已通过。未运行完整检查、真实模型、真实账号发现或凭据读取。

## 交付路径

| 路径 | 改动 |
| --- | --- |
| `tests/python/cli/test_cli.py` | 将现有 `call_service(state_dir=...)` 绑定到本测试根；service/RPC 桩接收并严格核对根，保留原服务映射和断言。 |
| `tests/python/cli/test_cli_views.py` | 同上；另将现有 `await_run(state_dir=...)` 绑定到本测试根，保留真实 blocking 实现和投影断言。 |
| `tests/python/cli/test_host_cli.py` | board/capture 两种桩均核对本测试根，通过 partial 使用现有 CLI 接口。 |
| `tests/python/blackboard/service/test_liveness.py` | 回调接收并核对根；在原 attach 测试内确认缺根及另一个根会被拒绝，再运行原三次轻量 ping 与 integrity 断言。 |
| `tests/python/blackboard/service/test_harness_startup.py` | 注入的 Mock 明确提供真实私有 Worker state 路径并核对身份；重试次数、未知子进程和 whitelist 原断言保持。 |
| `tests/python/buddy/runtime/test_live_lifecycle_integration.py` | Mock 与 channel 提供合法根；真实构造器 spy 核对 Worker 明确传根，核对两次 channel 的根，保持两次 attach/detach 和同一 runtime 原断言。 |
| `tests/python/blackboard/service/test_daemon.py` | health 直接请求显式传根；标准 mock spy 先核对根并通过 DEFAULT 执行原生 `_request`，没有替换传输。 |
| `tests/python/support.py` | 只新增 `assert_rpc_state_dir`：要求明确 str/Path 且 resolve 后等于当前夹具根，不接受任意其他根；2-B 环境防护、显式目录夹具、私有 HOME/SDK 与正常收尾均保留。 |
| `docs/acceptance/c-two-rpc-fixtures.md` | 本轮实测记录及 Host 补核边界。 |

CLI 的现有公共入口 `main` 调用 `call_service` 时没有提供具体根，`await` 也没有向 `await_run` 提供根。本测试通过现有入口的 state_dir 参数进行夹具绑定，没有重写 API 或另造传输；这些测试验证绑定后的真实映射、事务和投影，不证明公共 CLI 的默认根选择。是否调整公共入口由 Host 统一判断和整合。

## 私有环境与复现命令

任务根为 `/tmp/c2c-K4T737`（实际解析为 `/private/tmp/c2c-K4T737`）。私有 uv 环境、cache、HOME、TMPDIR、BUDDY_CHECKS_TMPDIR、比较树、变异树、原始日志均在该根；未手工清扫。材料来自授权公开 wheel 目录 `/tmp/c073-h-x6lhuldm/delegate-materials`；使用前核对 provenance 中全部 18 个文件 SHA256，并确认材料 uv.lock 与当前仓库锁文件哈希一致：`5ae675829eb4c9aca5e2c933b86799abd3ab9a979e80f8d00c074adf5e27cdf8`。发布版 wheel `c_two-0.7.4-cp313-cp313-macosx_11_0_arm64.whl` 的 SHA256 为 `bb625650f186c6066992cac0948c0f44b95a22af46778c8e63bba30d80ee88b8`；每个测试解释器还通过 importlib.metadata 断言实际安装版本为 0.7.4，没有 vendor 静态替代证明。

`bootstrap.py` 在清除继承 BUDDY_、ANTHROPIC_、C2_、VIRTUAL_ENV、UV_PROJECT_ENVIRONMENT 后调用 `uv venv --offline --python /opt/homebrew/bin/python3.13 <task-root>/env` 与 `uv pip install --offline --python <task-root>/env/bin/python --no-index --find-links <materials>/wheelhouse --require-hashes -r <materials>/locked-dependencies.txt`；两条命令均退出 0，日志为 `bootstrap-0.log`、`bootstrap-1.log`，实际安装 10 个锁定依赖，未安装项目或升级日常 runtime。

以下每一行均对应命令 `python3.13 <task-root>/run.py <label> <module>`，工作目录为授权 `<checkout>`。runner 实际调用 `<task-root>/env/bin/python <task-root>/child.py <task-root>/<label> <module>`，每个模块独立解释器；第三个可选参数为比较/变异 checkout。每个 label 单独创建 HOME/state/runtime/tmp，TMPDIR 与 BUDDY_CHECKS_TMPDIR 同指该 label 的 tmp。runner 清除继承 BUDDY_、ANTHROPIC_、C2_、VIRTUAL_ENV、UV_PROJECT_ENVIRONMENT 与账户 token/key 环境，SDK/XDG 路径指向私有 HOME，并明确设置私有 state/runtime、离线 facts 与不存在的 native CLI 路径；保留 support 原有更细的 child 环境与夹具防护。runner 使用新 label，不覆盖先前日志；检测 Too many open files 会立即停止，本轮未出现该错误。

| 阶段/label | 模块 | 项数 | 退出码 | 耗时（秒） | 实际结果 |
| --- | --- | ---: | ---: | ---: | --- |
| `baseline-cli` | `cli.test_cli` | 19 | 1 | 4.274 | 5 failure，旧 RPC 签名 |
| `baseline-views` | `cli.test_cli_views` | 11 | 1 | 1.984 | 6 failure，旧 RPC 签名 |
| `baseline-host` | `cli.test_host_cli` | 31 | 1 | 5.233 | 8 failure，旧 RPC 签名 |
| `baseline-liveness` | `blackboard.service.test_liveness` | 5 | 1 | 1.249 | 1 error，旧回调签名 |
| `baseline-startup` | `blackboard.service.test_harness_startup` | 4 | 0 | 0.948 | 通过，当前 Worker 公共接线已显式传根 |
| `baseline-lifecycle` | `buddy.runtime.test_live_lifecycle_integration` | 1 | 0 | 0.834 | 通过，未显式提供的 channel 根由私有进程环境补足，不能证明夹具显式传根 |
| `baseline-daemon` | `blackboard.service.test_daemon` | 13 | 1 | 4.794 | 12 通过，1 socket 受阻 |
| `bound-cli` | `cli.test_cli` | 19 | 0 | 1.041 | 最终源通过 |
| `bound-await-views` | `cli.test_cli_views` | 11 | 0 | 15.509 | 最终源通过 |
| `bound-host` | `cli.test_host_cli` | 31 | 0 | 5.487 | 最终源通过 |
| `final-liveness` | `blackboard.service.test_liveness` | 5 | 0 | 0.930 | 最终源通过 |
| `final-startup` | `blackboard.service.test_harness_startup` | 4 | 0 | 0.625 | 最终源通过 |
| `final-roots-lifecycle` | `buddy.runtime.test_live_lifecycle_integration` | 1 | 0 | 0.632 | 最终源通过 |
| `final-spy-daemon` | `blackboard.service.test_daemon` | 13 | 1 | 3.846 | 12 通过，真实健康测试 socket 受阻 |

原始失败完整保存在各 `baseline-*/output.log`，对应结果、真实命令、PID、wait 证据、项数和耗时分别在 `result.json`、`receipt.json`。三个 CLI 原始目标失败均为既有成功断言失败，SERVICE_ERROR 包含 `got an unexpected keyword argument 'state_dir'`；liveness 是相同回调 TypeError。daemon 原始/最终失败均为 `daemon exited early: buddy: CONSOLE_PORT_IN_USE: Cannot bind 127.0.0.1:0`。startup/lifecycle 的本轮原始结果如实记录为通过，没有编造已被 Host 修复的历史 Mock 异常。

中间失败及修复证据亦保留：`final-cli`、`final-views`、`final-host` 在严格根断言下分别为 19/11/31 项、退出 1、耗时 0.931/2.078/5.680 秒，暴露 CLI 未提供根；绑定现有 call_service 参数后 `bound-views` 为 11 项、退出 1、15.745 秒，剩余 await 根缺失；显式绑定 await_run 后最终 11 项通过。`final-lifecycle` 为 1 项通过、0.412 秒，随后把 channel 根检查移到 Worker 捕获异常边界之外，以便变异直接给出根断言；最终源再次实测通过。`final-daemon` 为 13 项、退出 1、4.034 秒，同样停在 socket 边界。

## 编号和原断言保留

`id-diff.json` 对逐模块实际 unittest 收集结果比较：CLI 19、views 11、Host CLI 31、liveness 5、startup 4、lifecycle 1、daemon 13，共 84；每个模块的 removed=[]、added=[]。`audit.py` 还对初始八份文件与最终文件中所有 assert 调用进行 AST multiset 比较，原 assert 调用全部保留；没有减少原测试编号或断言。原文件副本在 `baseline/tests/python/`，最终比较树在 `comparison/`；工作区 `git diff --check` 实测退出 0。

## 代表性单点变异

变异只发生在任务根的独立源码副本，不改工作区产品源码。使用同一 runner，对每个代表性目标单独解释器执行；全部成功计数项都是实际 unittest failure（errors=0），命中明确断言，没有把导入、环境、SDK 或 socket 拒绝算作成功。重复 CLI 桩没有逐一制造镜像测试。

| label | 单点变更 | 目标测试 | 项数/退出码 | 耗时（秒） | 命中 |
| --- | --- | --- | --- | ---: | --- |
| `mutation-cli-binding` | 去掉 call_service 的夹具根绑定 | `cli.test_cli.ModelProfilesCliTests.test_model_profiles_uses_the_named_schema_and_cursor` | 1/1 | 0.828 | 原 `assertEqual(code, 0, first)` 失败，消息包含 `RPC requires an explicit private state_dir` |
| `mutation-old-rpc-signature` | RPC 桩恢复旧签名 | 同上 | 1/1 | 0.729 | 原成功断言失败，SERVICE_ERROR 明确为 state_dir 关键字不被旧桩接受 |
| `mutation-attach-root` | 比较树 transport 轻量 ping 去掉 state_dir | `blackboard.service.test_liveness.LivenessTests.test_each_client_attach_uses_light_ping` | 1/1 | 0.729 | `assertIsInstance`：`RPC requires an explicit private state_dir` |
| `mutation-permissive-stub` | 恢复不校验根的桩行为 | 同上 | 1/1 | 0.724 | 缺根拒绝的 `assertRaisesRegex`：`AssertionError not raised` |
| `mutation-foreign-root` | 仅去掉根身份相等校验 | 同上 | 1/1 | 0.832 | 外来根拒绝的 `assertRaisesRegex`：`AssertionError not raised` |
| `mutation-mock-root` | 注入恢复为无合法 state_dir 的 Mock | `blackboard.service.test_harness_startup.HarnessStartupTests.test_unknown_working_child_is_never_restarted` | 1/1 | 0.729 | Mock 与真实 Worker path 不等，`Injected client must carry the private Worker state path` |
| `mutation-worker-v2` | 比较树 Worker 去掉传给 WorkerLiveRuntime 的 state_dir | `buddy.runtime.test_live_lifecycle_integration.WorkerLiveLifecycleTests.test_two_direct_attempts_use_the_same_live_runtime_until_its_owner_closes` | 1/1 | 0.520 | 构造器 spy：`Worker must explicitly supply its private state to the live runtime` |
| `mutation-channel-v2` | channel 构造去掉 state_dir | 同上 | 1/1 | 0.513 | 两次 channel 根与 Worker 根不等：`Each live channel must use the private Worker state root` |

对应未变异比较用例分别为 `compare-cli-binding`（1 项、退出 0、0.836 秒）、`compare-attach-root`（1 项、退出 0、0.735 秒）、`compare-mock-root`（1 项、退出 0、0.727 秒）、`compare-worker-live-root`（1 项、退出 0、0.519 秒）、最终 `compare-channel-v2`（1 项、退出 0、0.526 秒）。CLI 的两个变异共用同一已通过目标，liveness 三个变异共用同一已通过目标；每次仍是独立解释器和私有环境。`mutations.json` 保存首批单点 diff 与目标，`copy-foreign-root` 保存额外身份校验变异；首批 `mutation-worker-live-root`（0.522 秒）也命中明确传根断言。早期 `mutation-channel-root`（0.522 秒）命中原 accepted 列表断言，但内层根断言被 Worker 捕获，已补为 v2 的外层直接根断言；原日志保留。

`compare-daemon-root`（1 项、退出 1、1.359 秒）和 `mutation-daemon-root`（1 项、退出 1、1.354 秒）都在 daemon socket 启动处受阻，未执行直接 health 请求，不能计为目标变异命中。已准备 `copy-daemon-root`，只去掉 health 请求的 state_dir；Host 实跑时应在 spy 的明确私有根断言失败，未变异用例则应通过原全部健康断言。

## 原生边界、Host 补核和收尾

独立进程 `native` 实际安装的 C-Two 0.7.4 使用私有 state/ipc，通过现有 configure_server/configure_client、BuddyControl、InProcessBoard 和原生 cc.register 路径尝试注册及 ping；1 项、退出 1、0.719 秒，register 报 `CoreError: server failed to start: IO error: Operation not permitted (os error 1)`，尚未进入 connect/ping。`native/native-report.json` 保存错误及实际 cc.shutdown 返回，completed=true、server_was_started=false、ipc_clients_drained=true。没有切回默认端点、绕过沙箱或借用其他任务 SDK 进程域。

Host 需要在允许私有 TCP/Unix socket 的环境，用保留的私有依赖与 runner、新 label 复跑最终 `blackboard.service.test_daemon` 全 13 项；再分别对 `comparison/` 和 `copy-daemon-root/` 执行 `blackboard.service.test_daemon.DaemonHealthTests.test_a_running_daemon_reports_the_shared_ceiling_and_pool`，要求原保护通过、变异命中 `RPC requires an explicit private state_dir` 目标断言而非启动/导入/SDK 错误。另以新 label 执行 `native_probe`，要求实际私有注册、connect/ping 成功且 shutdown.completed=true。命令格式为 `python3.13 <task-root>/run.py <new-label> <module-or-test> [<copy-root>]`；保持逐模块独立解释器、私有 HOME/state/runtime，无模型，无日常服务操作。补核证据应写回本固定验收记录，原始日志继续放任务根；公共 CLI/root 入口整合如需修改仍归 Host，不在本次测试范围内。

所有 runner 自建测试解释器均已有 wait/退出证据，`process-receipt.json` 中 knownUnwaitedChildren=[]；七份原测试正常框架收尾负责停止并等待各自拥有的 subprocess。未独立枚举系统进程表，不能据此宣称每日服务或未知进程停止。本轮 daemon 在 console 启动处退出，未由该健康测试启动 managed pool；原生探针 shutdown 证据如上。没有已知未停止自建进程。任务根内 uv 环境、cache、日志、比较/变异副本和材料全部保留，未手工删除；Host 验收后按确切根 `/tmp/c2c-K4T737` 回收。
