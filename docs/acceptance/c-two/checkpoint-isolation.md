# C-Two checkpoint process isolation: micro task 2-D / R-08

固定基线为 `57837f2ff9bf4ae310618f88cc731057ae2692d7`。本轮只修改 `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py`，新增无版本号私有夹具 `tests/python/buddy/harnesses/zcode/fixtures/checkpoint_domain.py` 和本记录；产品代码、注册表、公共角色、公共 ZCode 夹具和其余 harness 均未修改。改动留在工作区，未提交、stash 或操作分支与标签。

两个并发编号继续使用 `test_private_fixture_states_keep_concurrent_inquiry_bridges_separate` 和 `test_concurrent_private_attempts_keep_late_questions_bound`。其父进程跳过会配置 C-Two 的原 setup，只通过现有 `protocol.fixtures.ctwo_controller.exchange` 的有限等待 JSON pipe 驱动自己创建并保存的 Popen。每个子进程在导入 SDK 前创建并设置自己的 HOME/state/runtime/ipc，清除继承的 `BUDDY_*`、`ANTHROPIC_*`、`C2_*` 和虚拟环境 pin，再复用 `ZcodeFixtureCase.setUp/context/ready_channel/ask/release`、注册表 Worker executor、原 `mock_zcode.py` 和真实 checkpoint/answer/finish 接缝；没有另写生产执行或 C-Two 传输。

代码要求两边先 admitted/live，再在任一问询或释放前复核两边仍 live；两边分别问询与释放后才收集。保留原 attempt/instance/token/address 差异、不串线记录、排队/投递/回答、late question 的关联 SHA/attempt、native send 次数和停止断言；另外核对持有者 PID、实际 SDK IPC 根、每边完整回合、分别观察的 controller/native 停止、自己的签收和整个 journal。原来的 wait 与 finish-accepted 断言由子进程中的同一夹具执行。其余八个测试函数 AST 与原件一致；编号新增与删除差集均为空，完整清单在 `<task-root>/test-id-diff.json`。

验证尚未通过，不能据此宣称两个并发回合或两层停止已实测通过。任务私有 Unix socket bind 返回 `PermissionError: [Errno 1] Operation not permitted`，见 `<task-root>/socket-probe.json`。测试中的控制器只导出 `{"code":"role-controller-failed","processState":{"shutdownConfirmed":false},"status":"error"}`，没有底层异常；因此 socket 的 EPERM 证明当前沙箱限制 IPC，不能进一步证明控制器失败的唯一原因，也不算目标变异红灯。

## 实际命令与结果

每次只在独立解释器运行 `buddy.harnesses.zcode.test_zcode_checkpoint`，没有完整检查、真实 ZCode、模型或账号发现。启动脚本清除上述继承变量，用任务私有 HOME/state/runtime，并将 TMPDIR 与 BUDDY_CHECKS_TMPDIR 指向任务根，PYTHONPATH 只指向相应源码与测试目录。

| 启动命令 | 内层命令 | 项数 | 内层退出码 | 墙钟耗时 | 原始材料 |
| --- | --- | ---: | ---: | ---: | --- |
| `python3.13 <task-root>/run.py baseline` | `<task-root>/venv/bin/python -m unittest -v buddy.harnesses.zcode.test_zcode_checkpoint` | 10 | 1 | 204.842 s | `baseline.log`、`baseline.json` |
| `python3.13 <task-root>/run.py final` | 同上 | 10 | 1 | 202.561 s | `final.log`、`final.json` |
| `python3.13 <task-root>/run.py final-source` | 同上；最后的私有夹具收尾版本 | 10 | 1 | 202.315 s | `final-source.log`、`final-source.json` |

三次均为 `FAILED (failures=10)`，业务入口错误为 `AssertionError: the held controller endpoint never became ready`，不是有效的业务断言变异结果。原测试源保存在 `<task-root>/original-checkpoint.py`；日志的 review 副本将检出路径替换为 `<checkout>`，原始日志只保存在任务根。最终源码哈希在 `<task-root>/source-hashes.json`。语法编译、编号/八项 AST 比较和 `git diff --check` 成功；它们不替代原生 IPC 验证。

公开 wheel 材料复制到 `<task-root>/materials`，逐项对照所给 provenance 的 SHA-256 后，使用 `uv venv --python /opt/homebrew/bin/python3.13 <task-root>/venv` 和 `uv pip install --python <task-root>/venv/bin/python --no-index --find-links <task-root>/materials/wheelhouse --require-hashes -r <task-root>/materials/locked-dependencies.txt`，均退出 0；uv cache 也在任务根。已通过私有解释器导入确认 `c_two.__version__ == '0.7.4'`，其 wheel SHA-256 为 `bb625650f186c6066992cac0948c0f44b95a22af46778c8e63bba30d80ee88b8`，见 `hashes.json`、`version.json`、`setup-*.log`。未安装或升级日常 runtime，也未静态证明 vendor 实现。

## R-08 交给 Host 的固定助手

可从检出根运行 `python3.13 <task-root>/host_r08.py <task-root>/h`，其中 `h` 必须是新目录。助手只复制 src/tests 到私有比较副本，不改原检出、不删除材料，使用本任务已核对的私有解释器和 wheel。它先要求修复版本全部 10 项绿色，再依次运行以下单点变异；任一权限、环境或导入错误均拒绝认定为红灯。若出现 Too many open files，立即停止并保留根。

| 变异副本 | 单点撤销的行为 | 必须到达的目标断言 | 本轮结果 |
| --- | --- | --- | --- |
| `same-interpreter` | 恢复原两项在同解释器创建两套 fixture 的执行；保留全部原业务断言，并在绑定前比较实际 SDK root 与本 attempt 的私有 root | `process-domain isolation violated` 的实际根差异断言，或配置冻结转换成同一域断言 | 待 Host 原生 IPC 补核 |
| `collapsed-late-owner` | late 场景只创建一个 live 持有者并复用它 | 两名持有者的 `ownerPid` 不同 | 待 Host 原生 IPC 补核 |
| `crossed-question` | own pipe 的单个 inquiry ID 由 own-question 改为 peer-question | `self.assertEqual(own_records['peer-question'], [])` | 待 Host 原生 IPC 补核 |

助手生成的四个源码副本已实际准备到 `<task-root>/prepared-mutations` 并分别编译语法，清单见 `prepared-mutations.json`；没有执行这些变异，也没有声称助手或 reviewer 已运行。Host 需返回 `green.{log,json}`、三个变异各自的 `.log/.json` 和收尾事实，确认绿色 10 项、三个目标红灯均非环境错误、无编号减少，再更新本记录。

另有范围外接缝需要 Host 核对：原 `ZcodeFixtureCase.setUp` 只更新 `self.environment`，而 `ready_channel` 调用的 `handle_live_binding` 默认经 `rpc_config.resolve_state_dir` 读取进程环境的 BUDDY_STATE_DIR。若该值与 fixture.root/state 不同，角色通道默认域可能与 setup 选定的 SDK 域不同。本轮子进程将进程环境与 fixture 域一致设置；未改公共 fixture 或角色行为。沙箱恢复后若原八项仍因此失败，应由 Host 统一修复该接缝，不扩大本轮写范围。

## 保留与停止边界

任务根确切路径为 `/tmp/c2d-16weKt`（macOS 解析别名 `/private/tmp/c2d-16weKt`）；材料、uv 环境/cache、一次性脚本、原始日志和比较副本全部保留，没有手工删除文件或目录。只允许原 TestCase/TemporaryDirectory 的正常收尾处理它们自己的目录；Host 验收后按此确切根回收。

最后一次运行的已保存直接持有者 Popen 为 PID 16067 和 20002：各自协作 stop 后 wait 的退出码为 0，自己的 controllerStopped 与 rpcShutdownConfirmed 均为 true，见 `<task-root>/checkpoint-domain-processes.jsonl`。它们的 nativeStopped 均为 null，不能解释成 native 已停止；控制器未提供正面的 native 停止收据。没有已知仍运行的已保存直接 Popen，也没有通过扫描 PID 接管任何进程；native 停止材料仍不清，需 Host 随绿色运行补齐。三个测试解释器均已 wait 返回退出码 1，未触碰日常 state/ipc、服务或 Worker。

## 同一 run 的路径别名修复续回合

Host 退回固定产物 `8a35816360fbcbb8fb39288006594d724e44e2f3`，确认范围内路径比较缺陷。本段保留上述三次受限检查及 native 停止未知事实；`<task-root>` 仍指 `/tmp/c2d-16weKt`，本次新 `<repair-root>` 为 `/tmp/c2dr-kyv192`（canonical 路径 `/private/tmp/c2dr-kyv192`）。两个根均保留，由 Host 验收后回收。

| Host 补核（据 Host 本次提供的结果） | 项数 | 退出码 | 耗时 | 原始失败与材料 |
| --- | ---: | ---: | ---: | --- |
| 两项并发；允许 socket 的私有环境 | 2 | 1 | 8.810 s | 两项已到真实 ready，随后 `checkpoint_domain.py:171` 比较 `/private/tmp/.../ipc` 与 `/tmp/.../ipc` 失败；原始材料为 `<实施检出>/tmp/c073-host/host-checkpoint-two-domains-green.log/json`，输入封存副本为 `/tmp/c073-h-x6lhuldm/repair-checkpoint-sealed-8a35816360fb` |
| 原 `test_one_send_delivers_and_answers_a_question_and_finishes_completed` | 1 | 1 | 21.189 s | `ready_channel` 的 observation 未就绪；Host 确认是公共 `ZcodeFixtureCase.live_channel` 的默认 state 接缝，进程 BUDDY_STATE_DIR 与 context 私有根不同 |

Host 原始日志没有出现在本分配检出或所给材料根的同名位置，上表明确是 Host 提供的补核事实，不冒充本轮独立重跑。路径别名失败与公共接缝失败都不算目标变异红灯。Host 将在自己的补核副本合成公共夹具的显式 state_dir 修正，再独立验收本轮最终产物全部 10 项和三处变异；本轮未改 `test_zcode.py` 或公共角色。

父持有者与 serve 子持有者现在均以 `Path(tempfile.gettempdir()).resolve()` 为 TemporaryDirectory 的创建目录，并在建立自身 root 时再次 resolve。因此 TemporaryDirectory.name（由原 ZcodeFixtureCase.setUp 使用）、父 root 和子 root 都是 canonical 路径；后续 state/runtime/HOME/ipc 材料从同一根派生。SDK 实际 root 的原相等断言、两域差异断言和全部业务断言保持原样，没有通过接受别名或跳过断言弱化隔离。

本次未重复已确认受限的 socket/10 项业务运行，也未运行模型。无 IPC 启动核对命令为 `/tmp/c2d-16weKt/venv/bin/python <repair-root>/verify_roots.py`（由清除继承变量并设置私有 HOME/state/runtime 的启动脚本执行；TMPDIR 与 BUDDY_CHECKS_TMPDIR 均为本次根）；退出 0、墙钟 1.043 s，见 `<repair-root>/verify-roots.log/json` 和 `bootstrap-paths.json`。它调用真实父/子夹具的建根与环境初始化代码，在父 Popen 和子 SDK 导入前停止：即使输入 TMPDIR 使用 `/tmp` 别名，实际 temporary.name、root、state/runtime/HOME/ipc 均为 `/private/tmp/c2dr-kyv192/...`，并确认子进程初始化清除了合成的继承标记。该检查执行业务测试 0 项、未启动持有者 Popen，不能替代真实 ready、回合或停止证据。

编号重新比较仍为 10 对 10，新增/删除差集为空，原八个非并发测试函数 AST 不变；fixture、测试文件以及更新后的助手与四份变异副本语法编译成功，`git diff --check` 退出 0。更新后的固定助手为 `<repair-root>/host_r08.py`，从 Host 已合成公共夹具修正的检出根运行 `python3.13 <repair-root>/host_r08.py <repair-root>/h`；它继续使用前一任务根已核对的私有 0.7.4 环境，不安装或升级日常 runtime。same-interpreter 变异的实际与预期 IPC 根比较也均先 resolve，防止路径别名被误认作域隔离红灯。四份更新源码副本仅准备并编译于 `<repair-root>/prepared-mutations`，未执行变异，见 `prepared-mutations.json`；最新固定源码与助手哈希见 `source-hashes.json`。

本次启动核对的私有解释器已正常 wait 完成，未新增持有者、控制器或 native Popen，没有本次新增的已知未停止进程；先前及 Host 失败回合缺失的 native 停止证据仍为未知。没有手工清理、修改共享 stash/refs 或触碰日常服务。完成条件仍为 Host 对合成副本全 10 项绿色、三个有效目标红灯以及每边完整回合与两层停止的独立补核。
