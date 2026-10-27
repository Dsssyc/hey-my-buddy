# 完整检查提速方案

2026-10-13；冻结基线 `398a275e794f38430f7a93c99aa2ae066397bd9d`。这是“完整检查提速”宏任务的测量与方案微任务，等待 Host 审阅。当前只交付本文件，测试、源码、分支与标签均未修改，未提交，也未实施下面的候选改法。

时间主要花在测试体执行的真实 Git 子进程，以及真实服务／Worker／安装器的运行和停止。7 个 Git 重文件共 190 条测试，剖析墙钟合计 1,110.259 秒，其中 36,102 次同步 Git 调用占 1,042.217 秒（93.9%）。初始仓库模板已经覆盖主要重文件；剩下三处模板候选合计推算只省约 4.8 个文件秒。把生命周期文件拆成四个后按当前调度复算，整轮也没有稳定收益。因此本轮建议暂缓测试改造：4 进程预计仍约 13 分钟；6 进程本轮约 13 分钟；8 进程实测 7 分钟至 9 分 26 秒，但全绿稳定性尚未成立。

建议通用默认继续取 4，不升到 6。下一微任务先补全隔离检出的测量条件、复核 6／8 进程发现的失败，再用显式 `--jobs 8` 做稳定性验证。若 Host 要求 4 进程也明显缩短，这次未找到能够说明行为不变并显著提速的测试改法，须另审产品工作区路径中的 Git 调用成本；不能靠删减、替换或放松测试来换时间。

## 1. 测量边界与完整命令结果

机器为 arm64、10 个逻辑核、32 GiB 内存；使用 Host 指定的现成 Python 3.13.3，主命令带 `PYTHONPATH=src`，不执行 uv run，不向该解释器环境安装内容。主测量环境额外设置 `PYTHONDONTWRITEBYTECODE=1`，所有五轮使用同一设置；测试自行重建的子进程环境可能不同。运行后观察到检出内有被 Git 忽略的 `__pycache__`，没有记录其初始状态，未删除；本轮没有建立所有子进程零缓存写入或严格冷启动的证明。检查器自动给每个文件独立 HOME、harness 配置根、state、runtime 和 TMPDIR，并清除继承的 Worker／运行时身份，目录位于本微任务独占的短临时根。测试内部的安装、服务和 Worker 生命周期都只发生在这些私有目录，检查器沿原有生命周期锁和进程观察完成清理。

完整测量依次为 4、6、8、6、8 进程，互不重叠。实际发现 232 个文件、3,868 条测试出现记录，无加载失败。下表的“汇总通过文件测试数”遵循检查器原输出：失败文件里的测试不计入这个数，不能用它当作整轮实际发现数量。文件秒是检查器每文件一位小数输出的和，包含子进程启动、文件内执行和输出收集；整轮墙钟另包含调度及最终清理。

复测命令如下，其中 `$PYTHON` 指 Host 给定的解释器；外部测量脚本先调用 `checks.test_environment(repo)` 净化环境，再给主命令设置独占的 `BUDDY_CHECKS_TMPDIR`。记录开始 UTC、monotonic 墙钟、退出码、全部文件时间和负载。

```sh
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m hey_my_buddy.cli.checks --jobs 4
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m hey_my_buddy.cli.checks --jobs 6
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m hey_my_buddy.cli.checks --jobs 8
```

| 轮次 | 墙钟 | 文件秒合计 | 最慢文件／秒 | 通过文件 | 汇总通过文件测试数 | 退出／清理 | 一分钟负载起／止 |
| --- | ---: | ---: | --- | ---: | ---: | --- | ---: |
| 1-j4 | 13:00.8（780.8s） | 3028.5 | `blackboard.tasks.test_workspace_lifecycle`／389.5 | 231/232 | 3865（跳过 0） | 1／私有根已移除 | 23.6／31.5 |
| 2-j6 | 12:52.2（772.2s） | 4274.1 | `blackboard.tasks.test_workspace_lifecycle`／566.3 | 230/232 | 3853（跳过 0） | 1／私有根已移除 | 31.5／49.2 |
| 3-j8 | 9:25.9（565.9s） | 4031.9 | `blackboard.tasks.test_workspace_lifecycle`／499.4 | 228/232 | 3786（跳过 0） | 1／私有根已移除 | 49.2／44.0 |
| 4-j6 | 12:58.4（778.4s） | 4112.6 | `blackboard.tasks.test_workspace_lifecycle`／508.1 | 227/232 | 3820（跳过 0） | 1／私有根已移除 | 44.0／78.9 |
| 5-j8 | 6:59.7（419.7s） | 3253.2 | `blackboard.tasks.test_workspace_lifecycle`／413.5 | 231/232 | 3865（跳过 0） | 1／私有根已移除 | 78.9／50.7 |

上述五轮均有失败，不能标记为完整检查通过。6 进程两轮没有给出稳定的整轮收益（772.2、778.4 秒，对比 4 进程 780.8 秒）；8 进程两轮为 565.9、419.7 秒，最后一轮生命周期文件 413.5 秒接近整轮 419.7 秒。最后一轮的 231 个其他文件通过，并不抵消上一轮的偶发失败。

负载不受本微任务控制，不能称为空闲机器对照；6 进程途中还观测到一分钟负载 104.2，8 进程途中为 115.8。这些观测不能区分本套件与外部负载的贡献，也不能证明“提高进程数导致了每个失败”。Host 给出的 4 进程 11:10～12:25、空闲 8 进程 7:24 是另一次条件下的证据，本表没有将其合并为本轮样本。

| 轮次 | 失败文件与现象 |
| --- | --- |
| 4-1 | `cli.test_host_preview`：vitest 缺失 |
| 6-1 | 同一 preview 缺失；`install.test_first_install`：私有 uv sync 非零（5 errors、2 failures） |
| 8-1 | preview 缺失；`test_first_install`、`blackboard.store.test_blackboard`：私有安装失败；`buddy.harnesses.test_discovery_stop_timing`：fixture registration missing |
| 6-2 | preview 缺失；`test_worker_runtime`、`install.test_distribution`、`install.test_packaging`：依赖下载连接超时；`buddy.harnesses.zcode.test_zcode_tool_evidence`：timeout 子场景缺少 toolEvidence |
| 8-2 | 仅 preview 缺失，其他 231 个文件通过 |

当前检查环境有三个独立限制。`cli.test_host_preview.PreviewFixtureTest.test_frontend_parsers_consume_the_emitted_fixtures` 因 `apps/console/node_modules/vitest/vitest.mjs` 不存在而失败，实际前端解析器没有跑到。若干安装相关测试的私有 `uv sync --frozen --no-dev --python 3.12` 返回非零，引起 `RUNTIME_INSTALL_FAILED`，随后可能令预期 `CLAUDE_LINK_FAILED` 或 `SKILL_TARGET_CONFLICT` 的断言也失败；`install/runtime.py` 未提供 log_path 的调用将输出送到 DEVNULL，正常清理又移除私有目录，首轮日志不足以定位 uv 的根因。8 进程还出现过 `buddy.harnesses.test_discovery_stop_timing.DiscoveryStopTimingTests.test_timeout_refreshes_group_fact_after_actual_pipe_eof` 的 `fixture registration missing`；源测试的 probe 截止预算为 0.15 秒，清理注册等待上限为 5 秒。该现象表明当前 8 进程运行存在偶发失败证据，不能仅凭后续通过声称稳定，也不能直接把它归因于产品停止机制。

这些失败保留在各轮统计中。本微任务不补装依赖、不改安装／harness 测试、不调整用户的代理、缓存或账户配置。后续剖析仅为原本送到 DEVNULL 的私有 uv 输出换了日志落点，并在失败的私有 CLI 返回时复制其已存在的安装日志，命令、环境、退出码和断言均不变；这些诊断不算未加剖析的性能样本。

第二轮 6 进程取得了直接网络证据：`buddy.runtime.test_worker_runtime` 构建私有 staged package 时下载 `hatchling` 失败，日志为 3 次重试、45.1 秒、连接超时；`install.test_distribution` 相同构建依赖失败为 45.8 秒；`install.test_packaging` 下载 `typing-extensions` 失败为 44.6 秒。由此可以解释这些具体安装失败，不能倒推首轮被 DEVNULL 丢弃的每个 uv 失败都有相同原因。同轮 `buddy.harnesses.zcode.test_zcode_tool_evidence.NoToolReceiptWiringTests.test_cancelled_and_timeout_receipts_carry_incomplete_evidence` 的 timeout 子场景出现 `KeyError: toolEvidence`，应独立复核，不能混成依赖网络故障。

## 2. 时间花在哪

以第一轮 4 进程的文件秒排序，选择最重的 14 个文件，共 1,586.7／3,028.5 秒，覆盖 52.39%，包含 Host 点名的全部 8 个文件。逐条测量共 313 条测试，使用 `checks.create_private_root`、`checks.run_suite_child`／`checks.child_environment` 创建单文件进程，私有环境与正式检查一致；采用独立 4 进程批次，调用原有 unittest loader/runner，startTest/stopTest 记录每条总时间，包裹原操作记录次数与墙钟，并用 cProfile 定位主线程阻塞。测量程序在检出外，测试逻辑、命令实参、断言与返回码保持原样。

这 14 个剖析文件全部通过，313 条无失败、无错误、无跳过；批次墙钟 443.0 秒，清理证据为空且私有根已移除。其文件墙钟合计 1,706.793 秒，受剖析开销、不同的并行排队和负载影响，不能与第一轮 1,586.7 秒相加或当成优化回归。附录 A 留下 232 个文件的五轮时间；附录 B 列出全部 313 条的编号和时间。

下表是剖析实测值。Git 列为主测试线程同步 `subprocess.run` 的次数／墙钟（包括启动和等待）；直接 Popen 列只计构造调用的次数／墙钟，不计该子进程整个寿命。建看板为 `InProcessBoard.__init__` 包含时间，sleep 为 cProfile 的主线程睡眠自耗时，均可能与其他列嵌套，不能相加成“节省总数”。子进程内再起的 Git／进程不在主线程计数中，例如 CLI 内的工作归在该 CLI 调用墙钟。

| 文件（相对 tests/python） | 条数 | 完整 4 进程秒 | 剖析秒 | 同步 Git 次／秒 | 直接 Popen 次／秒 | 建看板秒 | 主线程 sleep 秒 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `blackboard/tasks/test_workspace_lifecycle.py` | 49 | 389.5 | 410.41 | 12999／384.91 | 13070／60.89 | 2.96 | 3.09 |
| `blackboard/tasks/test_workflow_preparation.py` | 30 | 171.3 | 168.62 | 6089／158.44 | 6116／22.39 | 1.16 | 1.25 |
| `blackboard/tasks/test_storage_orphans.py` | 36 | 161.1 | 166.64 | 6091／157.17 | 6094／21.73 | 1.49 | 0.14 |
| `blackboard/tasks/test_parallel_dispatch.py` | 27 | 101.1 | 97.51 | 0／0.00 | 21／0.06 | 0.66 | 69.56 |
| `buddy/runtime/test_worker_runtime.py` | 4 | 98.7 | 124.97 | 2／0.06 | 8／0.03 | 0.00 | 3.13 |
| `blackboard/store/test_blackboard.py` | 57 | 95.1 | 144.12 | 0／0.00 | 63／0.27 | 3.53 | 66.95 |
| `blackboard/tasks/test_scope_recovery.py` | 18 | 92.6 | 119.24 | 2792／110.63 | 2822／22.16 | 1.59 | 1.72 |
| `blackboard/tasks/test_workspace.py` | 26 | 87.1 | 95.54 | 2862／91.41 | 2883／15.27 | 0.00 | 1.10 |
| `blackboard/tasks/test_workflow_real.py` | 11 | 87.0 | 85.90 | 2841／80.68 | 2854／11.88 | 0.55 | 1.14 |
| `install/test_packaging.py` | 12 | 67.6 | 54.64 | 2／0.05 | 42／0.15 | 0.00 | 3.23 |
| `blackboard/tasks/test_host_workflow_worker.py` | 4 | 66.6 | 65.26 | 22／0.93 | 54／0.18 | 0.00 | 14.19 |
| `blackboard/tasks/test_workspace_submission_cleanup.py` | 20 | 65.7 | 63.91 | 2428／58.98 | 2429／7.92 | 0.93 | 0.04 |
| `install/test_first_install.py` | 12 | 52.3 | 62.84 | 22／0.50 | 83／0.27 | 0.00 | 39.73 |
| `buddy/runtime/test_run.py` | 7 | 51.0 | 47.19 | 0／0.00 | 49／0.11 | 0.00 | 25.01 |

最重的 Git 路径位于测试体。生命周期文件准备阶段只有 2.371 秒，测试体 406.964 秒；工作区准备为 1.438／166.705 秒；存储 orphan 为 1.932／164.108 秒。生命周期里的 6,100 次 rev-parse 花 180.130 秒，2,091 次 ls-files 花 60.248 秒。七个 Git 重文件合计 rev-parse 16,184 次／463.201 秒、ls-files 7,016 次／201.444 秒；这两类实际命令占 Git 时间的 63.8%。这些数字定位了真实工作区捕获、验证、封存、接续和回收路径的成本，没有证明任何一次检查可以省略。

进程与等待文件的归因也不是统一的“起进程慢”：`test_parallel_dispatch` 准备仅 0.068 秒，16 次建看板共 0.658 秒；主线程 sleep 69.562 秒、875 次 native RPC call 共 17.233 秒，其中等待 slot 恢复的两个条件轮询分别 13.066／12.742 秒，清理 10.771 秒。`test_blackboard` 的 35 次同步子进程调用共 53.188 秒，冷启动 CLI health 一次 26.330 秒，57 条准备共 0.022 秒、建看板 3.529 秒，另有服务／Worker 等待与清理。它们必须继续观察实际进程和锁，不能把产品的轮询上限减小来记收益。

`test_worker_runtime` 只有一条重的 staged-runtime 用例：launcher install 96.625 秒、health 14.462 秒、runtime 查询 3.291 秒，准备仅 0.295 秒；没有多个重用例可通过拆文件并行。`test_packaging` 的两个 launcher health 共 45.244 秒；`test_first_install` 的七次 uv sync 共 33.722 秒、launcher install 5.240 秒、每例清理合计 8.836 秒。它们测的是私有安装、启动与路径稳定性，不可改成共用已启动服务或已安装 runtime 来逃过该路径。

`test_host_workflow_worker` 的六次 CLI await 共 31.129 秒、四次 submit 共 7.946 秒，准备仅 0.743 秒；`test_run` 的五次 await 共 9.614 秒、二十次 status 共 5.992 秒、清理 10.593 秒。`waitSeconds=60/90` 是成功提前返回的上限，不等于每条真的等满一分钟。`test_workspace_submission_cleanup` 则是 2,428 次 Git／58.983 秒，模板准备只有 0.736 秒；它已经在 setUpClass 建仓库模板。

## 3. 逐文件候选与取舍

按“去掉重复准备 → 缩短测试控制的等待 → 拆长文件”的顺序评估。以下节省量都是推算，未实施任何候选、未取得修改后的实际加速证据。表中“保留”表示本轮不建议为提速改该文件；仍给出可行候选及收益上限，供 Host 决定是否值得后续微任务的审查成本。

### 重复准备

已有模板的位置：`test_workspace_lifecycle.py:48`、`test_storage_orphans.py:19`、`test_workflow_preparation.py:23`、`test_workspace.py:18`、`test_workspace_submission_cleanup.py:22`。这些模板的收益已经包含在当前基线，不能再次计入。看板初始化也不是主要成本；没有理由在共用 support.py 新增数据库模板机制。

三处尚可减少重复建仓库：`RealWorkspaceTestCase.setUp` 对本文件 11 条每条做 5 次 init/config/add/commit；ScopeRecovery 的 18 条同样继承它；RestartPreparation 的 3 条明确不使用现有模板。按每文件创建一次同样的初始仓库、每例独立 copytree 加 update-index --refresh 推算。实测生命周期模板的复制均值 0.01560 秒／次、索引刷新 0.03115 秒／次，估式为旧建库 Git 秒 − 一次建库均值 − 条数 ×（复制均值 + 刷新均值）；模板只复制初始仓库，测试体全保留。该估式跨文件借用复制成本，故仅是预算，不是改后实测。

| 候选文件／范围 | 旧准备的建库 Git | 一次模板 + 独立复制／刷新推算 | 预计省文件秒 | 行为保持与决定 |
| --- | ---: | ---: | ---: | --- |
| `blackboard/tasks/test_workflow_real.py` 的本文件 11 条 | 1.642 秒／55 次 | 0.149 + 0.514 秒 | 约 0.98 | 保留真实 Git、独立 refs/index、原测试体；须显式限定本文件模板，不影响借用基类的其他文件。本轮暂缓 |
| `blackboard/tasks/test_scope_recovery.py` 的 18 条 | 4.700 秒／90 次 | 0.261 + 0.841 秒 | 约 3.60 | 每例仍有独立 BoardTestCase 私有根、目录环境与 catalog；不复制 scope failure／SQLite 快照。本轮暂缓 |
| `blackboard/tasks/test_workflow_preparation.py` 的 RestartPreparationTests 三条 | 0.549 秒／15 次 | 0.183 + 0.140 秒 | 约 0.23 | 为该文件的重启组明确准备模板，不能依赖 PreparationTests 先跑；重启前后的板和固定产物校验照常执行。本轮暂缓 |

合计只省约 4.8 个剖析文件秒；按第一轮文件成本折算约 4.0 个文件秒，4 进程整轮调度模拟约省 1 秒。现有三个文件的主要时间仍是测试体中真实产品 Git 操作。为这笔收益改动被多个文件借用的基类，不是本宏任务当前最有价值的一步。

### 测试控制的等待

`test_blackboard.TestLifecycle.test_work_survives_a_real_daemon_restart_and_reconnects_to_the_same_result` 中 sleep 8 与 CLI／服务重启工作重叠；确认同一 attempt 之后的完成条件轮询实测仅 0.378 秒，不能记作“去掉 sleep 8 就省 8 秒”。若以后改为本例私有 release-file gate，必须在原有同 attempt／Worker／generation 校验之后才放行，并保留产物断言；本轮可证的剩余等待预算只有 0～0.38 秒，不建议为此改造。另一个 correlated-answer 重启测试共 22.671 秒，其 sleep 8 也不能直接作为可省时间，保留原路径。

`test_parallel_dispatch` 的 0.75 秒观察窗口已经配合本例 0.25 秒取消轮询，2.5 秒窗口则跨真实 supervisor 的 2 秒产品轮询。继续缩短会影响“retire intent 被观察后仍不取消”的负面证据；为省不到几秒而添加对子进程的 sitecustomize 或第二套启动机制不合本任务约束。保留这两处，以及条件轮询、35 秒清理上限和真实停止证据。`test_run` 的 4 秒命令与 2 秒 await 超时专门验证等待窗口，30 秒命令由真实取消提前结束，同样不作为可删盲等。

### 拆长文件

生命周期文件可机械拆成 acceptance 11 条、cleanup 18 条、helpers 9 条、recovery 11 条，保留类名和方法名；三组新模块及 49 条逐一映射见附录 D。辅助方法提取为不带 test_ 方法的夹具类，每个测试文件自己建立一份不可变初始模板，每条仍复制到自己目录。原模块继续提供当前其他测试实际使用的 helper 和 git 入口，保留 `test_host_workflow.load_tests` 的自身方法筛选；不复制出会被 unittest 二次发现的整套带测试基类。

按剖析比例折算第一轮，原 389.5 秒将分为约 89.9／145.9／71.8／82.5 秒，另多付三份类准备／进程导入成本。单文件尾部变短并不等于整轮变快：目前 4 进程的理论工作量下界约 757.1 秒，距实测仅 23.7 秒；第二轮 8 进程下界约 406.7 秒，距实测仅 13.1 秒。按既有慢文件提示及字母余项的真实顺序复算，拆分没有稳定收益，因此本轮不建议执行。

主推算保持检查器不变，新文件进入现有字母余项。另算了把四个分片名放进旧 SLOWEST_FILES_FIRST 位置的敏感性对照，也没有稳定收益；它未被纳入执行建议，不借此增加第四种调度改造。没有改私有目录机制，没有按方法并行，没有跨文件服务、缓存层或执行顺序依赖。

| 重文件 | 本轮建议／若后续改动的具体方向 | 预计省文件秒与行为依据 |
| --- | --- | --- |
| `blackboard/tasks/test_workspace_lifecycle.py` | 保留；四片备选已给逐条映射 | 总量基本不降，增加约 0.7 秒夹具成本；断言原样移动，整轮模拟无稳定收益 |
| `blackboard/tasks/test_workflow_preparation.py` | 保留 PreparationTests 模板；RestartPreparation 模板为轻量备选 | 约 0.23 秒，仅替换初始 15 次 Git 构造，其余真实接续／重启步骤不变 |
| `blackboard/tasks/test_storage_orphans.py` | 保留现有模板和真实 orphan 证明／删除栅栏 | 0；准备仅 1.932 秒，主要是 157.172 秒被测 Git 调用 |
| `blackboard/tasks/test_parallel_dispatch.py` | 保留真实池、锁、重启和取消观察窗口 | 0；准备 0.068 秒，69.562 秒 sleep 多为真实状态等待 |
| `buddy/runtime/test_worker_runtime.py` | 保留一次完整 staged-runtime 生命周期 | 0；唯一重用例无可拆并行子用例，安装 96.625 秒就是被测路径 |
| `blackboard/store/test_blackboard.py` | 保留本轮等待；release gate 仅为以后候选 | 可证预算 0～0.38 秒；不得把两个 sleep 8 记成 16 秒收益 |
| `blackboard/tasks/test_scope_recovery.py` | 本文件独立初始仓库模板为备选 | 约 3.60 秒；所有 scope 恢复、CAS、原始证据与新 board 重启仍实际执行 |
| `blackboard/tasks/test_workspace.py` | 保留已有模板与 borrowed-setup 兼容调用测试 | 0；Git 91.408／95.540 秒，26 条断言覆盖的真实操作不替换 |
| `blackboard/tasks/test_workflow_real.py` | 本文件初始模板为备选；本轮不迁移 RealAdapterTests | 约 0.98 秒；多个继承测试编号必须保持，额外拆两例没有可证整轮收益 |
| `install/test_packaging.py` | 保留真实 launcher 和已有 SkillBuildTests 类夹具 | 0；两个 health 45.244 秒，预装／共用服务会改变被测路径 |
| `blackboard/tasks/test_host_workflow_worker.py` | 保留真实 CLI、失败后续行与进程清理 | 0；Git 准备仅 0.574 秒，await 31.129 秒是实际工作完成 |
| `blackboard/tasks/test_workspace_submission_cleanup.py` | 保留已有模板及真实失败回滚／删除栅栏 | 0；Git 58.983 秒，准备只有 0.736 秒 |
| `install/test_first_install.py` | 保留逐例私有安装；先处理依赖下载条件 | 0；七次 uv sync 33.722 秒，不能用已安装 runtime 替代 first-install 覆盖 |
| `buddy/runtime/test_run.py` | 保留 await 超时、取消与独立 daemon | 0；准备 0.002 秒，等待上限本就可提前返回 |

## 4. 默认进程数、推算和可达到的目标

推荐当前默认继续为 `max(1, min(4, CPU−1))`。本轮 6 进程两次均约 13 分钟且各有新增失败，没有支持默认升到 6 的证据。8 进程有明显的速度潜力：最后一次 419.7 秒，文件总量比 4 进程增 7.4% 而墙钟减 46.3%；但两次结果跨度 146.2 秒且第一轮有额外失败，只能作为下一微任务验证候选，不能宣布已经稳定。当前数据也没有单独建立并行度与偶发错误的因果关系。

以下是“若三处模板与四片拆分都做完”的条件推算，供否决或审阅候选，不是实施承诺。每个旧文件沿用该轮实测成本，模板按前述估式扣减，生命周期按 49 条实测比例分片，额外加三份类准备估值；用原 `_scheduled_test_modules` 顺序对空闲 worker 做贪心排队，再加回该轮实测墙钟与旧队列模拟值的差（1.1～1.7 秒）。没有重新测量拆分后的资源争用，也没有模拟缺失 parser 的真实运行时间。6／8 的失败轮保留原成本用于条件敏感性分析，不能冒充修复依赖后的成功基线。

| 进程／样本 | 当前实测秒 | 仅三处模板（推算秒） | 模板 + 拆分，调度不变（推算秒） | 连提示表也同步分片名（敏感性推算秒） |
| --- | ---: | ---: | ---: | ---: |
| 4／full-1-j4 | 780.8 | 779.8 | 780.4 | 780.0 |
| 6／full-2-j6 | 772.2 | 771.4 | 776.9 | 763.5 |
| 8／full-3-j8 | 565.9 | 565.1 | 572.7 | 566.8 |
| 6／full-4-j6 | 778.4 | 777.9 | 778.8 | 778.9 |
| 8／full-5-j8 | 419.7 | 418.9 | 421.3 | 423.0 |

因此，三类测试候选做完的同条件预计仍是：4 进程约 13:00；6 进程约 12:57～12:59；8 进程约 7:01～9:33。这个范围来自固定文件成本的调度推算，不是置信区间。恢复缺失前端解析器后的整轮时间还需实测，不能用当前 F=0.4 秒的失败替代它的真实成本；网络重试与未复现偶发也使当前范围不能成为全绿检查 SLA。没有依据承诺靠这些改动把 4 进程降到 7 分钟，甚至不能保证比当前快。

若目标明确为 4 进程约 7 分钟，仅工作量下界就要求把 3,028.5 个文件秒至少减到 1,680 秒，减少至少 1,348.5 秒（44.5%，还未计清理开销）。后续较大改动应先审产品 workspace 的同一次操作内 Git 查询图：16,184 次 rev-parse 与 7,016 次 ls-files 是可明确定位的候选调查对象，核对能否批量读取、避免同一边界内重复取相同事实，同时保留源 checkout 不变、固定产物、CAS、所有权与真实 shutdown 证据。是否存在可安全减少的调用尚未证明，需要单独的产品方案与 Host 扩大范围；本微任务不提出绕过这些检查的测试替身。

## 5. 微任务次序与范围

建议按下面顺序推进。本方案已经完成测量；后续每个执行微任务都需要使用合并后的冻结基线重新留下前后证据，不能把另一微任务的源码变化当成本计划的提速。

| 次序 | 要完成的事 | 涉及文件／边界 | 交付与停止条件 |
| --- | --- | --- | --- |
| 1 | Host 处理隔离检出缺失的 console 依赖、私有安装下载超时，并让负责 harness 的微任务复核两个偶发用例 | 开发依赖准备；故障归属 `test_discovery_stop_timing.py`、`zcode/test_zcode_tool_evidence.py`。它们不并入测试提速改造，本微任务不编辑 | 依赖完整、失败原因有证据，再冻结合并后基线；不能靠重跑掩盖失败 |
| 2 | 在可比较负载下验证显式 8 进程，按二审清单留下 4／6／8 对照和连续通过记录 | 使用现有 `cli/checks.py` 入口与参数，不修改源码／测试；证据由后续微任务写入 Host 授权的 `docs/acceptance/` 文件 | 只有完整通过和偶发原因处理后，Host 才决定是否把 8 作为默认候选。当前默认 4 保留 |
| 3（仅 Host 仍选择轻量候选时） | 本文件初始模板改造，先做 ScopeRecovery，再做 workflow_real 与 RestartPreparation | `test_scope_recovery.py`、`test_workflow_real.py`、`test_workflow_preparation.py`；不改全局 support／共享服务。借用基类的 cli views、workspace API、permission levels、workspace stash、cancellation、upgrade host cutover 必须纳入受影响验证 | 单次预算合计仅约 4.8 个文件秒；若三次实测不能分辨收益，停止这条路线，不扩大模板机制 |
| 4（本轮不推荐） | 只有 Host 有独立的维护理由才采用生命周期四片拆分 | 原 `test_workspace_lifecycle.py`；新 `workspace_lifecycle_fixture.py`、`test_workspace_lifecycle_cleanup.py`、`test_workspace_lifecycle_helpers.py`、`test_workspace_lifecycle_recovery.py`；主候选不改检查器调度 | 49 行映射、断言归属、导入方和逆序证据完整；当前模拟不支持把它作为显著提速步骤 |
| 5（若仍要 4 进程明显变快） | 另审产品 Git 调用成本的方案 | 从 `src/hey_my_buddy/blackboard/tasks/workspace.py` 与 workspace_identity 调用图开始调查；具体代码写入范围须由 Host 另定 | 保持原测试覆盖，先给减少调用的语义证明与测量，再决定是否执行 |

这一轮不动其余 218 个测量文件，也不通过缩短等待或拆分处理上述安装／harness 失败。下节列出的并行微任务区域全部避让。

## 不变项与 Host 二审所需证据

执行微任务的基线应是另一个微任务集成后的冻结提交；不能把本文件中 398a275e 的计时与修改过产品源码的提交直接相减。先补齐该隔离检出的 console 开发依赖，查清私有运行时安装失败，并获得负载可比较的测量窗口。本微任务的写入范围不含这些修复；本轮失败不会被归入提速收益。

每条测试的断言、比较值、异常类型与错误码、负面路径、循环次数、参数组合、状态转换、实际 Git／RPC／子进程边界都保持。本方案的模板候选只含正常初始 Git 仓库；工作目录、index、refs、worktree 注册、看板、数据库、凭据、锁、进程、事件和结果每条测试独立。带绝对路径与身份绑定的已提交工作区、已封存产物或活跃 SQLite 看板，尚无简单复制后仍保持原有身份与证据的方案，本轮不采用这类场景模板。模板用普通复制，不用硬链接或 alternates 共享可变对象；模板构建结束后只读，每例结束后核对其内容指纹未变。

编号核对使用检查器枚举的模块及 unittest 实际发现结果，记录“调度文件、module.Class.method、出现次数”。比较多重集，不能用集合去掉原有重复项；`load_tests`、继承方法和导入别名都以实际发现结果为准。未拆文件的编号逐字相同；拆分仅允许附录所列一对一改名，类名和方法名不变，禁止增加 skip、expectedFailure、条件性早退或把断言移到不被发现的基类。`test_host_workflow.load_tests` 只选择类自身的方法，必须保留这一事实。

断言证据按文件列出前后统计，并附迁移归属表：Python `assert`、属性名以 `assert` 开头的调用、`fail()` 和显式 `raise AssertionError` 分开数。拆出的夹具也参加统计；原文件减少的每个断言必须能定位到新的文件／函数／行号，所有相关文件总数不能减少。数量只是交叉核对，Host 还要看测试方法及 helper 的 AST／源码差异，检查实参、谓词、上下文管理器、循环／subTest 组合以及异常分支原样保留，避免“同样数量、不同力度”。模板构建中的命令成功检查仍在构建时执行，测试体中的产品断言不移到模板。

每个执行微任务交出：冻结基线与候选提交／树标识、实际 diff；全部测试编号的前后清单和映射；逐文件断言数及迁移归属；改动文件与受影响导入方的验证结果；4、6、8 进程下基线和候选各三次完整命令日志、每文件时间、退出码、实际运行／跳过数和清理结论。三次按相同进程数交替基线／候选测量，禁止同时跑另一套检查或剖析；记录负载与依赖条件，保留全部失败，不能只选最好一次。

候选还需 8 进程连续五次完整通过（不能忽略 frontend parser，也不能以重跑覆盖一次失败），并核对私有根均清理、跳过数量不增加。可以将前述三次候选 8 进程测量纳入五次序列，但序列中间不能改源码、测试或依赖配置。模板与拆分的文件另做私有环境内逆序／固定随机种子顺序运行，并比较模板前后指纹及测试编号多重集；使用既有 unittest 与 checks 的环境入口，诊断脚本留在检出外，不引入测试框架。运行时间改进不能替代 Host 对这些证据的二审。

建议 Host 在独立的临时副本逐个施加下表变异：先运行原测试得到通过，再只改变所列产品判断，确认同一测试因预期断言失败，最后恢复产品代码并复测。保存变异 diff、失败编号与断言；不能用导入错误、环境错误或超时来冒充检出产品缺陷。本微任务没有执行这些变异。

| 产品变异 | 应检出的现有测试（`tests/python` 下模块） | 需要看到的失败 |
| --- | --- | --- |
| `blackboard/tasks/workspace.py` 捕获工作区时忽略暂存区与工作树的差异，或丢掉二进制／模式信息 | `blackboard.tasks.test_workspace.WorkspaceTests.test_dirty_input_preserves_staging_binary_modes_symlinks_and_source` | 输入内容、模式或源 index 保持断言失败 |
| 分别在 `workspace.cleanup_inspect` 的现场证明、`storage._orphan_workspace` 的孤立分配证明中忽略 `unsealed-changes`（两个独立变异） | `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_cleanup_is_blocked_by_unsealed_changes`；`blackboard.tasks.test_storage_orphans.StorageOrphanTests.test_apply_refuses_new_unsealed_tracked_and_untracked_content` | 原应保留的 checkout 被判可删，NOT_READY／保留文件断言失败 |
| `workspace.resolve` 取消现场 fingerprint 的 compare-and-swap 检查 | `blackboard.tasks.test_scope_recovery.ScopeRecoveryTestCase.test_restore_compare_and_swap_preserves_a_later_change` | 后来的修改被覆盖或冲突错误消失 |
| `workflow.accept` 允许没有 verified／not-required 集成决定的最终产物被接受 | `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_acceptance_requires_a_verified_or_not_required_decision` | 拒绝接受、集成绑定断言失败 |
| `buddy/runtime/worker.py` 的取消轮询将 retire intent 当成运行中 attempt 的取消请求 | `blackboard.tasks.test_parallel_dispatch.RetireRaceTests.test_a_retire_intent_written_during_an_attempt_never_cancels_it` | running／cancelRequested／最终 completed 至少一处失败 |
| 重启服务时替换正在运行的 attempt ID，或不再发布子进程产物 | `blackboard.store.test_blackboard.TestLifecycle.test_work_survives_a_real_daemon_restart_and_reconnects_to_the_same_result` | 相同 attempt／generation／Worker 或 durable 产物断言失败 |

## 并行微任务的避让范围

另一个微任务正在删除版本号闸门。本表是根据当前源码和 Host 给定范围列出的保守避让清单，没有读取另一个工作区，也不声称取得了它的实际 diff。执行前由 Host 用最终改动清单确认；本方案只剖析这些区域中进入重文件名单的文件，提速候选不修改它们。

| 区域 | 本轮不修改的路径与重点文件 |
| --- | --- |
| 路由 | `src/hey_my_buddy/blackboard/routing/`、`tests/python/blackboard/routing/`；尤其 `test_read_only_eligibility.py`、`test_decision.py`、`test_router.py`、`test_routing_health.py`、`test_router_probe.py` |
| 评价与目录 | `src/hey_my_buddy/blackboard/evaluation/`、`src/hey_my_buddy/blackboard/catalog/` 及对应 `tests/python/blackboard/{evaluation,catalog}/`；尤其 `evaluation.py`、`catalog.py`、`catalog_store.py`、`test_evaluation.py`、`test_native_observations.py`、`test_user_policy.py` |
| 服务的 harness 能力入口 | `src/hey_my_buddy/blackboard/service/` 及 `tests/python/blackboard/service/`；尤其 `harness_health.py`、`test_harness_service.py`、`test_harness_startup.py` |
| harness 能力与原生证据 | `src/hey_my_buddy/buddy/harnesses/`、`tests/python/buddy/harnesses/` |
| 控制台和预览契约 | `src/hey_my_buddy/console/`、`apps/console/`、`tests/python/console/`、`tests/python/cli/test_host_preview.py`、`tests/probes/objective_console_preview.py` |
| 共用夹具与契约 | `tests/python/support.py`、`tests/python/blackboard/tasks/test_workflow.py`、`tests/python/protocol/fixtures/` 和产品 protocol 契约；它们供另一微任务共用，本轮不做全局模板或全局等待改造 |

## 本微任务的完成核对

已记录五轮完整命令、232 文件的测试数量／断言计数／逐文件时间，以及 14 个重文件的 313 条编号和逐条时间；另外通过实际发现清单校核了全部 3,868 条出现记录的数量，该完整编号清单未附入本文件。所有测量只读原测试／源码，五轮和剖析的私有根均已确认移除。文档卫生检查通过 5 条，退出 0；定向 `checks --sweep` 退出 0，deleted 0、skipped 0、retained 0。本文的预测没有实现验证，未执行变异、未验证修改后的速度，也没有把 Worker 结论当成 Host 验收。

## 附录 A：232 个文件的完整命令计时

所有时间单位为秒；路径相对 `tests/python/`。列顺序对应 4、6、8、6、8 进程五轮，`F` 表示该文件失败。断言列为 Python assert／assert* 调用／fail 调用／显式 AssertionError 四项静态计数；这是基线清单，执行微任务还须交前后对照。

| 文件 | 测试数 | 断言计数 | 4-1 | 6-1 | 8-1 | 6-2 | 8-2 |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| `blackboard/catalog/test_catalog.py` | 14 | 0/45/0/1 | 0.5 | 0.4 | 0.6 | 1.1 | 0.4 |
| `blackboard/catalog/test_catalog_observations.py` | 14 | 0/67/0/0 | 2.6 | 1.9 | 3.6 | 4.6 | 1.7 |
| `blackboard/catalog/test_catalog_trust.py` | 16 | 0/74/0/1 | 2.4 | 2.8 | 4.0 | 4.7 | 1.3 |
| `blackboard/catalog/test_native_discovery_stop.py` | 14 | 0/77/0/0 | 6.7 | 7.3 | 8.2 | 7.6 | 5.6 |
| `blackboard/evaluation/test_evaluation.py` | 17 | 0/85/0/0 | 4.3 | 3.9 | 5.8 | 5.5 | 2.5 |
| `blackboard/evaluation/test_model_facts.py` | 30 | 0/100/0/0 | 3.4 | 3.3 | 5.1 | 3.5 | 2.1 |
| `blackboard/evaluation/test_native_observations.py` | 12 | 0/57/0/0 | 1.6 | 1.8 | 1.9 | 1.7 | 1.4 |
| `blackboard/evaluation/test_other_native_observations.py` | 29 | 0/169/0/0 | 14.1 | 16.4 | 20.1 | 18.0 | 13.0 |
| `blackboard/evaluation/test_user_policy.py` | 6 | 0/22/0/0 | 2.3 | 3.1 | 3.8 | 3.3 | 2.2 |
| `blackboard/routing/test_catalog_trust.py` | 4 | 0/5/0/0 | 0.9 | 1.1 | 1.6 | 1.7 | 0.9 |
| `blackboard/routing/test_current_router.py` | 32 | 0/133/0/1 | 3.7 | 4.8 | 6.1 | 5.1 | 3.1 |
| `blackboard/routing/test_decision.py` | 35 | 0/258/0/0 | 39.3 | 48.8 | 55.3 | 36.2 | 39.0 |
| `blackboard/routing/test_fast_router_probe.py` | 3 | 0/12/0/0 | 0.2 | 0.4 | 0.3 | 0.3 | 0.2 |
| `blackboard/routing/test_nonretryable_quota.py` | 5 | 0/29/0/0 | 0.8 | 1.5 | 2.6 | 1.9 | 1.0 |
| `blackboard/routing/test_permission_route.py` | 3 | 0/8/0/0 | 0.4 | 0.8 | 1.3 | 0.7 | 0.5 |
| `blackboard/routing/test_quota_retry.py` | 20 | 0/108/0/0 | 11.3 | 14.7 | 17.8 | 19.9 | 9.8 |
| `blackboard/routing/test_read_only_eligibility.py` | 7 | 0/23/0/0 | 0.5 | 0.6 | 1.2 | 0.9 | 0.5 |
| `blackboard/routing/test_readonly_tools_probe.py` | 8 | 0/23/0/0 | 0.7 | 1.0 | 1.4 | 1.7 | 0.9 |
| `blackboard/routing/test_retired_review_verifier.py` | 6 | 0/20/0/0 | 2.5 | 3.3 | 6.7 | 5.7 | 2.7 |
| `blackboard/routing/test_router.py` | 19 | 0/59/0/0 | 15.3 | 23.1 | 30.9 | 25.1 | 17.1 |
| `blackboard/routing/test_router_boundary.py` | 11 | 0/48/0/0 | 1.1 | 1.6 | 3.0 | 3.4 | 1.3 |
| `blackboard/routing/test_router_boundary_integration.py` | 38 | 0/232/0/0 | 8.3 | 8.0 | 9.9 | 8.0 | 5.3 |
| `blackboard/routing/test_router_dispatch.py` | 22 | 0/145/0/0 | 5.4 | 4.7 | 6.1 | 4.1 | 2.8 |
| `blackboard/routing/test_router_failover.py` | 6 | 0/16/0/0 | 0.1 | 0.1 | 0.1 | 0.1 | 0.1 |
| `blackboard/routing/test_router_failover_integration.py` | 88 | 0/149/0/0 | 20.1 | 17.1 | 16.3 | 25.1 | 11.9 |
| `blackboard/routing/test_router_list_configuration.py` | 9 | 0/48/0/0 | 9.3 | 7.9 | 7.7 | 9.9 | 5.6 |
| `blackboard/routing/test_router_list_health.py` | 20 | 0/82/0/0 | 5.2 | 3.1 | 2.8 | 5.1 | 2.0 |
| `blackboard/routing/test_router_probe.py` | 17 | 0/69/0/0 | 29.5 | 29.2 | 29.2 | 29.6 | 29.0 |
| `blackboard/routing/test_router_sequence.py` | 23 | 0/106/0/2 | 3.2 | 2.8 | 2.8 | 3.2 | 2.0 |
| `blackboard/routing/test_router_settings.py` | 7 | 0/26/0/0 | 0.1 | 0.1 | 0.1 | 0.1 | 0.1 |
| `blackboard/routing/test_router_tool_evidence.py` | 13 | 0/33/0/0 | 6.4 | 5.6 | 4.9 | 10.9 | 4.2 |
| `blackboard/routing/test_routing_health.py` | 11 | 0/64/0/0 | 2.6 | 2.1 | 1.6 | 5.1 | 1.7 |
| `blackboard/routing/test_routing_history.py` | 10 | 0/128/0/0 | 4.3 | 4.0 | 3.5 | 6.6 | 3.2 |
| `blackboard/routing/test_routing_modes.py` | 11 | 0/51/0/0 | 2.1 | 2.3 | 2.0 | 3.3 | 1.7 |
| `blackboard/routing/test_routing_preferences.py` | 19 | 0/109/0/0 | 1.9 | 2.0 | 1.8 | 2.9 | 1.6 |
| `blackboard/routing/test_selection_policy.py` | 14 | 0/44/0/0 | 0.1 | 0.2 | 0.1 | 0.2 | 0.1 |
| `blackboard/routing/test_single_router_claims.py` | 17 | 0/77/0/0 | 8.1 | 7.6 | 7.3 | 9.2 | 5.4 |
| `blackboard/routing/test_single_router_configuration.py` | 6 | 0/23/0/0 | 1.6 | 1.5 | 1.6 | 1.8 | 1.3 |
| `blackboard/routing/test_single_router_requests.py` | 22 | 0/140/0/0 | 5.0 | 4.8 | 4.5 | 5.7 | 3.2 |
| `blackboard/routing/test_sole_candidate_selection.py` | 13 | 0/111/0/0 | 3.7 | 3.9 | 3.7 | 4.8 | 2.5 |
| `blackboard/routing/test_stage2_review_scope.py` | 3 | 0/13/0/0 | 0.4 | 0.4 | 0.3 | 1.0 | 0.3 |
| `blackboard/service/test_console_reads.py` | 14 | 0/65/0/0 | 1.8 | 1.4 | 1.7 | 1.9 | 1.1 |
| `blackboard/service/test_daemon.py` | 14 | 0/62/0/1 | 7.3 | 8.0 | 7.7 | 11.6 | 6.5 |
| `blackboard/service/test_governed_service_stop.py` | 3 | 0/23/0/0 | 1.8 | 2.0 | 1.7 | 2.2 | 1.4 |
| `blackboard/service/test_harness_health.py` | 38 | 0/197/0/0 | 6.0 | 7.4 | 6.5 | 9.3 | 5.3 |
| `blackboard/service/test_harness_service.py` | 12 | 0/33/0/0 | 1.4 | 1.9 | 1.5 | 3.2 | 1.4 |
| `blackboard/service/test_harness_startup.py` | 4 | 0/14/0/1 | 0.4 | 0.7 | 0.4 | 0.9 | 0.3 |
| `blackboard/service/test_live_holder_integration.py` | 1 | 0/13/0/0 | 1.5 | 1.7 | 1.2 | 2.7 | 1.3 |
| `blackboard/service/test_live_registry.py` | 29 | 0/91/0/3 | 4.5 | 4.7 | 4.7 | 7.7 | 3.8 |
| `blackboard/service/test_liveness.py` | 5 | 0/16/1/0 | 0.9 | 1.0 | 1.1 | 2.3 | 0.9 |
| `blackboard/service/test_service.py` | 8 | 0/41/0/0 | 1.7 | 1.4 | 1.8 | 2.7 | 1.2 |
| `blackboard/service/test_service_environment.py` | 12 | 0/68/0/0 | 0.5 | 0.4 | 0.4 | 1.3 | 0.4 |
| `blackboard/service/test_worker_pool.py` | 6 | 0/15/0/0 | 0.6 | 0.5 | 0.4 | 1.1 | 0.4 |
| `blackboard/store/test_blackboard.py` | 57 | 0/282/8/0 | 95.1 | 212.5 | 173.6 F | 94.2 | 149.2 |
| `blackboard/store/test_current_core.py` | 6 | 0/44/0/0 | 0.9 | 0.9 | 0.8 | 1.8 | 1.0 |
| `blackboard/store/test_store.py` | 11 | 0/41/0/0 | 2.2 | 2.4 | 2.4 | 3.5 | 2.2 |
| `blackboard/store/test_upgrade_fixture.py` | 4 | 0/33/0/0 | 12.4 | 16.0 | 13.7 | 19.3 | 12.1 |
| `blackboard/tasks/test_catalog_trust.py` | 25 | 0/123/0/0 | 5.3 | 4.9 | 5.1 | 6.2 | 3.9 |
| `blackboard/tasks/test_console_objective_fixture.py` | 4 | 0/56/0/0 | 5.9 | 5.4 | 5.6 | 7.5 | 4.4 |
| `blackboard/tasks/test_continue_catalog_trust.py` | 9 | 0/71/0/0 | 1.9 | 1.8 | 2.0 | 3.1 | 1.4 |
| `blackboard/tasks/test_delegation_history.py` | 29 | 0/178/0/0 | 6.3 | 7.4 | 6.0 | 10.0 | 5.4 |
| `blackboard/tasks/test_dsh_resume_selection.py` | 7 | 0/37/0/0 | 2.0 | 2.0 | 1.8 | 3.5 | 1.6 |
| `blackboard/tasks/test_empty_claims.py` | 6 | 0/59/0/0 | 1.3 | 1.5 | 1.3 | 2.4 | 1.1 |
| `blackboard/tasks/test_host_workflow.py` | 16 | 0/110/0/0 | 14.0 | 21.9 | 19.9 | 28.2 | 19.5 |
| `blackboard/tasks/test_host_workflow_worker.py` | 4 | 0/54/0/0 | 66.6 | 93.9 | 101.6 | 59.3 | 70.7 |
| `blackboard/tasks/test_inquiry.py` | 36 | 0/218/0/1 | 50.4 | 83.3 | 68.8 | 94.5 | 67.2 |
| `blackboard/tasks/test_model_concurrency.py` | 18 | 0/61/0/0 | 3.0 | 4.2 | 3.8 | 6.2 | 3.6 |
| `blackboard/tasks/test_objective_stop.py` | 5 | 0/30/0/0 | 3.6 | 4.5 | 3.9 | 4.7 | 3.8 |
| `blackboard/tasks/test_objective_summary_cache.py` | 30 | 0/170/1/0 | 7.4 | 11.2 | 12.0 | 19.6 | 11.2 |
| `blackboard/tasks/test_objectives.py` | 28 | 0/149/0/0 | 8.9 | 14.2 | 13.6 | 18.8 | 12.9 |
| `blackboard/tasks/test_parallel_dispatch.py` | 27 | 0/233/0/0 | 101.1 | 137.2 | 138.5 | 102.3 | 106.3 |
| `blackboard/tasks/test_permission_levels.py` | 8 | 0/36/0/0 | 28.6 | 47.0 | 38.6 | 53.9 | 36.7 |
| `blackboard/tasks/test_permission_resume.py` | 5 | 0/13/0/0 | 2.4 | 5.2 | 3.9 | 6.0 | 4.0 |
| `blackboard/tasks/test_scheduler_lanes.py` | 6 | 0/17/0/0 | 9.7 | 16.6 | 14.0 | 22.4 | 12.4 |
| `blackboard/tasks/test_scheduling.py` | 10 | 0/30/0/0 | 0.3 | 0.4 | 0.4 | 1.1 | 0.3 |
| `blackboard/tasks/test_scope_recovery.py` | 18 | 0/198/0/0 | 92.6 | 119.1 | 138.0 | 77.8 | 94.9 |
| `blackboard/tasks/test_storage.py` | 24 | 0/125/1/0 | 1.7 | 3.0 | 2.8 | 5.7 | 2.7 |
| `blackboard/tasks/test_storage_orphans.py` | 36 | 0/135/0/1 | 161.1 | 222.6 | 183.9 | 293.6 | 190.1 |
| `blackboard/tasks/test_title_fallback.py` | 10 | 0/29/0/0 | 2.6 | 3.2 | 3.1 | 5.2 | 2.4 |
| `blackboard/tasks/test_windows_workspace.py` | 10 | 0/43/0/0 | 0.2 | 0.2 | 0.3 | 0.5 | 0.2 |
| `blackboard/tasks/test_worker_claim_stash.py` | 9 | 0/52/0/0 | 2.0 | 2.7 | 2.8 | 4.3 | 2.2 |
| `blackboard/tasks/test_workflow.py` | 55 | 0/300/0/0 | 8.8 | 12.1 | 10.5 | 16.1 | 8.9 |
| `blackboard/tasks/test_workflow_boundaries.py` | 5 | 0/45/0/0 | 2.0 | 3.5 | 2.9 | 5.7 | 2.4 |
| `blackboard/tasks/test_workflow_cancellation.py` | 22 | 0/100/0/0 | 11.0 | 20.4 | 13.9 | 20.4 | 13.0 |
| `blackboard/tasks/test_workflow_preparation.py` | 30 | 0/156/0/0 | 171.3 | 270.2 | 275.1 | 168.5 | 192.3 |
| `blackboard/tasks/test_workflow_real.py` | 11 | 0/97/0/1 | 87.0 | 134.0 | 138.3 | 80.4 | 95.7 |
| `blackboard/tasks/test_workflow_routing.py` | 24 | 0/165/0/0 | 4.6 | 10.4 | 6.2 | 8.5 | 5.5 |
| `blackboard/tasks/test_workflow_stash.py` | 12 | 0/69/0/0 | 1.4 | 3.1 | 2.1 | 3.8 | 1.8 |
| `blackboard/tasks/test_workflow_stop_surface.py` | 8 | 0/42/0/4 | 1.0 | 1.8 | 1.0 | 2.2 | 1.0 |
| `blackboard/tasks/test_workflow_worker.py` | 5 | 0/60/2/0 | 21.0 | 43.4 | 31.3 | 47.4 | 31.5 |
| `blackboard/tasks/test_workspace.py` | 26 | 0/120/0/0 | 87.1 | 131.9 | 137.2 | 79.8 | 95.1 |
| `blackboard/tasks/test_workspace_api.py` | 4 | 0/43/0/0 | 21.2 | 31.7 | 25.6 | 35.1 | 26.1 |
| `blackboard/tasks/test_workspace_batch.py` | 8 | 0/42/0/0 | 29.7 | 44.0 | 33.6 | 45.1 | 35.2 |
| `blackboard/tasks/test_workspace_git_errors.py` | 13 | 0/48/0/0 | 0.2 | 0.4 | 0.2 | 0.3 | 0.2 |
| `blackboard/tasks/test_workspace_identity.py` | 5 | 0/16/0/0 | 9.0 | 13.6 | 9.4 | 13.3 | 9.3 |
| `blackboard/tasks/test_workspace_lifecycle.py` | 49 | 1/442/0/2 | 389.5 | 566.3 | 499.4 | 508.1 | 413.5 |
| `blackboard/tasks/test_workspace_stash.py` | 17 | 0/116/0/0 | 37.9 | 48.6 | 42.3 | 60.8 | 46.9 |
| `blackboard/tasks/test_workspace_submission_cleanup.py` | 20 | 0/111/0/0 | 65.7 | 83.5 | 72.4 | 121.5 | 78.5 |
| `buddy/harnesses/claude/test_claude.py` | 36 | 0/214/1/0 | 29.2 | 40.5 | 40.7 | 27.1 | 27.9 |
| `buddy/harnesses/claude/test_claude_config.py` | 11 | 0/64/0/0 | 1.1 | 1.0 | 0.7 | 1.0 | 1.1 |
| `buddy/harnesses/claude/test_claude_protocol.py` | 28 | 0/95/0/0 | 0.2 | 0.2 | 0.2 | 0.4 | 0.2 |
| `buddy/harnesses/claude/test_claude_tool_evidence.py` | 36 | 0/155/0/0 | 4.6 | 5.0 | 4.2 | 9.2 | 4.7 |
| `buddy/harnesses/claude/test_claude_worker.py` | 3 | 0/24/0/0 | 18.1 | 25.4 | 21.0 | 33.7 | 23.1 |
| `buddy/harnesses/claude/test_native_run.py` | 74 | 0/379/0/0 | 10.3 | 12.9 | 14.2 | 19.9 | 16.1 |
| `buddy/harnesses/claude/test_worker_permissions.py` | 7 | 0/46/0/0 | 2.1 | 2.4 | 2.9 | 4.1 | 3.5 |
| `buddy/harnesses/codex/test_batch4_facts.py` | 27 | 0/91/0/0 | 0.3 | 0.3 | 0.5 | 1.0 | 0.5 |
| `buddy/harnesses/codex/test_codex.py` | 36 | 0/185/1/0 | 34.5 | 49.3 | 54.5 | 36.4 | 37.6 |
| `buddy/harnesses/codex/test_codex_continuation.py` | 7 | 0/31/0/0 | 2.4 | 2.5 | 3.1 | 5.3 | 3.4 |
| `buddy/harnesses/codex/test_codex_protocol.py` | 22 | 0/72/0/0 | 0.4 | 0.4 | 0.5 | 0.8 | 0.5 |
| `buddy/harnesses/codex/test_codex_tool_evidence.py` | 30 | 0/121/0/0 | 11.4 | 12.0 | 13.4 | 25.2 | 15.8 |
| `buddy/harnesses/codex/test_native_run.py` | 103 | 0/527/0/0 | 36.0 | 38.7 | 37.6 | 48.3 | 39.0 |
| `buddy/harnesses/codex/test_no_tool_codex.py` | 14 | 0/93/0/0 | 22.6 | 23.8 | 23.0 | 44.7 | 24.8 |
| `buddy/harnesses/dsh/acp/test_acp_connection.py` | 39 | 0/195/0/0 | 11.1 | 11.3 | 11.2 | 12.3 | 11.4 |
| `buddy/harnesses/dsh/acp/test_acp_launch.py` | 15 | 0/63/0/1 | 0.2 | 0.2 | 0.2 | 0.4 | 0.2 |
| `buddy/harnesses/dsh/acp/test_acp_session.py` | 12 | 0/49/0/0 | 1.7 | 1.8 | 1.8 | 2.3 | 1.9 |
| `buddy/harnesses/dsh/test_dsh_role_wiring.py` | 12 | 0/96/0/0 | 6.3 | 6.8 | 6.8 | 13.2 | 7.0 |
| `buddy/harnesses/dsh/test_dsh_session_storage.py` | 6 | 0/25/0/0 | 2.9 | 2.9 | 2.8 | 3.9 | 2.9 |
| `buddy/harnesses/dsh/test_dsh_tool_evidence.py` | 11 | 0/34/0/0 | 0.1 | 0.1 | 0.1 | 0.2 | 0.1 |
| `buddy/harnesses/dsh/test_native_resume.py` | 16 | 0/116/0/0 | 6.7 | 7.5 | 6.9 | 12.6 | 7.2 |
| `buddy/harnesses/dsh/test_native_run.py` | 62 | 0/393/0/0 | 14.7 | 15.0 | 15.0 | 19.5 | 15.1 |
| `buddy/harnesses/dsh/test_no_tool_dsh.py` | 6 | 0/32/0/0 | 2.9 | 3.1 | 2.9 | 7.0 | 3.0 |
| `buddy/harnesses/dsh/test_refusal_facts.py` | 29 | 0/114/1/0 | 0.4 | 0.6 | 0.4 | 0.8 | 0.4 |
| `buddy/harnesses/dsh/test_session_records.py` | 18 | 0/65/0/0 | 0.6 | 0.7 | 0.5 | 1.6 | 0.5 |
| `buddy/harnesses/dsh/test_worker_permissions.py` | 6 | 0/35/0/0 | 1.6 | 1.9 | 1.8 | 3.0 | 1.8 |
| `buddy/harnesses/test_adapter_usage.py` | 13 | 0/76/0/0 | 7.5 | 7.8 | 7.7 | 14.9 | 8.1 |
| `buddy/harnesses/test_c_two_live.py` | 73 | 1/390/2/8 | 15.4 | 15.6 | 15.8 | 24.5 | 16.1 |
| `buddy/harnesses/test_controller.py` | 46 | 0/150/0/2 | 4.8 | 4.8 | 4.8 | 5.3 | 4.9 |
| `buddy/harnesses/test_discovery_stop_timing.py` | 10 | 0/33/1/0 | 10.3 | 10.3 | 14.5 F | 10.6 | 10.3 |
| `buddy/harnesses/test_harness_discovery.py` | 44 | 0/141/0/1 | 9.3 | 9.1 | 5.5 | 12.1 | 10.3 |
| `buddy/harnesses/test_inquiry_owner.py` | 15 | 0/116/1/2 | 2.0 | 2.0 | 2.1 | 3.1 | 2.1 |
| `buddy/harnesses/test_live_channel.py` | 36 | 0/155/0/0 | 0.4 | 0.3 | 0.4 | 0.8 | 0.3 |
| `buddy/harnesses/test_native_support.py` | 13 | 0/24/0/0 | 0.3 | 0.4 | 0.4 | 0.5 | 0.4 |
| `buddy/harnesses/test_pending_ready_receipt_guards.py` | 8 | 0/38/0/2 | 0.3 | 0.2 | 0.3 | 0.5 | 0.3 |
| `buddy/harnesses/test_private_adapter_invariants.py` | 14 | 0/91/0/0 | 10.3 | 9.8 | 10.1 | 21.5 | 10.7 |
| `buddy/harnesses/test_quota_failures.py` | 11 | 0/33/0/0 | 0.2 | 0.3 | 0.3 | 0.5 | 0.2 |
| `buddy/harnesses/test_run_contract.py` | 39 | 0/145/0/0 | 0.3 | 0.3 | 0.3 | 0.7 | 0.3 |
| `buddy/harnesses/test_runtime_version.py` | 19 | 0/96/0/0 | 20.5 | 21.7 | 22.5 | 39.8 | 24.2 |
| `buddy/harnesses/test_session_receipts.py` | 17 | 0/70/0/0 | 0.1 | 0.1 | 0.1 | 0.3 | 0.1 |
| `buddy/harnesses/zcode/test_batch4_facts.py` | 23 | 0/74/0/0 | 5.7 | 4.3 | 3.4 | 7.0 | 7.6 |
| `buddy/harnesses/zcode/test_native_config.py` | 12 | 0/58/0/0 | 6.6 | 6.9 | 5.6 | 9.7 | 9.3 |
| `buddy/harnesses/zcode/test_native_run.py` | 74 | 0/430/1/0 | 38.7 | 37.0 | 38.7 | 59.2 | 41.6 |
| `buddy/harnesses/zcode/test_no_tool_zcode.py` | 10 | 0/41/0/0 | 11.0 | 11.5 | 12.0 | 17.7 | 11.9 |
| `buddy/harnesses/zcode/test_plan_probe.py` | 9 | 0/37/0/0 | 1.5 | 1.7 | 1.6 | 3.1 | 1.7 |
| `buddy/harnesses/zcode/test_zcode.py` | 26 | 0/160/3/0 | 38.5 | 47.7 | 46.7 | 34.8 | 37.2 |
| `buddy/harnesses/zcode/test_zcode_checkpoint.py` | 10 | 0/106/0/0 | 13.3 | 13.4 | 13.2 | 23.5 | 13.6 |
| `buddy/harnesses/zcode/test_zcode_hooks_probe.py` | 12 | 0/48/0/0 | 0.2 | 0.3 | 0.2 | 0.4 | 0.3 |
| `buddy/harnesses/zcode/test_zcode_inquiry.py` | 47 | 0/262/2/0 | 7.4 | 7.3 | 6.9 | 10.0 | 7.3 |
| `buddy/harnesses/zcode/test_zcode_native.py` | 1 | 0/4/0/0 | 0.2 | 0.2 | 0.3 | 0.8 | 0.3 |
| `buddy/harnesses/zcode/test_zcode_protocol.py` | 39 | 0/156/0/0 | 0.4 | 0.4 | 0.5 | 0.5 | 0.5 |
| `buddy/harnesses/zcode/test_zcode_request_input_probe.py` | 16 | 0/68/0/0 | 3.4 | 3.9 | 4.1 | 8.1 | 3.5 |
| `buddy/harnesses/zcode/test_zcode_tool_evidence.py` | 27 | 0/118/1/0 | 9.7 | 9.5 | 9.6 | 16.1 F | 9.9 |
| `buddy/harnesses/zcode/test_zcode_tool_projection_order.py` | 11 | 0/24/0/0 | 0.1 | 0.1 | 0.1 | 0.1 | 0.1 |
| `buddy/harnesses/zcode/test_zcode_tool_refusals.py` | 8 | 0/47/0/0 | 5.7 | 6.1 | 5.8 | 12.8 | 5.8 |
| `buddy/harnesses/zcode/test_zcode_turn_io.py` | 4 | 0/20/0/0 | 0.2 | 0.3 | 0.3 | 0.6 | 0.2 |
| `buddy/roles/test_checkpoint_shutdown_guard.py` | 5 | 0/19/0/0 | 2.9 | 3.1 | 3.2 | 6.9 | 3.1 |
| `buddy/roles/test_dsh_resume_storage.py` | 5 | 0/56/0/0 | 1.1 | 0.9 | 1.0 | 2.3 | 0.9 |
| `buddy/roles/test_outcome_truncation.py` | 12 | 0/37/0/0 | 0.3 | 0.3 | 0.3 | 0.6 | 0.3 |
| `buddy/roles/test_permission_evidence.py` | 3 | 0/14/0/0 | 0.3 | 0.3 | 0.3 | 1.0 | 0.4 |
| `buddy/roles/test_registered_review.py` | 4 | 0/26/0/0 | 0.7 | 0.7 | 0.7 | 1.5 | 0.7 |
| `buddy/roles/test_registered_run_wiring.py` | 21 | 0/115/0/0 | 10.1 | 10.2 | 10.2 | 22.2 | 11.0 |
| `buddy/roles/test_role_controller.py` | 15 | 0/79/0/0 | 0.2 | 0.2 | 0.3 | 0.7 | 0.3 |
| `buddy/roles/test_role_live_seam.py` | 17 | 0/34/0/0 | 0.4 | 0.3 | 0.3 | 1.2 | 0.4 |
| `buddy/roles/test_role_prompts.py` | 4 | 0/17/0/0 | 0.3 | 0.3 | 0.3 | 0.7 | 0.3 |
| `buddy/roles/test_router_input.py` | 3 | 0/11/0/0 | 0.5 | 0.5 | 0.4 | 0.7 | 0.5 |
| `buddy/roles/test_run_controller_discovery.py` | 14 | 0/66/0/0 | 5.9 | 6.1 | 5.7 | 7.4 | 5.8 |
| `buddy/roles/test_run_controller_signals.py` | 2 | 0/12/0/0 | 1.3 | 1.4 | 1.4 | 3.3 | 1.5 |
| `buddy/roles/test_run_execution.py` | 18 | 0/51/0/0 | 0.3 | 0.3 | 0.3 | 0.7 | 0.3 |
| `buddy/roles/test_run_native_evidence.py` | 9 | 0/33/0/0 | 0.2 | 0.2 | 0.2 | 0.8 | 0.2 |
| `buddy/roles/test_schema_worker.py` | 14 | 0/55/0/0 | 0.9 | 0.9 | 0.8 | 2.1 | 0.8 |
| `buddy/roles/test_worker_services.py` | 15 | 0/65/0/0 | 0.2 | 0.2 | 0.1 | 0.3 | 0.2 |
| `buddy/runtime/test_live.py` | 36 | 0/250/0/0 | 6.4 | 6.8 | 6.8 | 14.5 | 6.8 |
| `buddy/runtime/test_live_lifecycle_integration.py` | 1 | 0/8/0/0 | 0.3 | 0.3 | 0.3 | 0.6 | 0.3 |
| `buddy/runtime/test_partial_output_safety.py` | 4 | 0/9/0/0 | 0.1 | 0.1 | 0.1 | 0.2 | 0.1 |
| `buddy/runtime/test_repair_recovery.py` | 10 | 0/106/0/0 | 43.0 | 46.9 | 45.0 | 44.4 | 42.6 |
| `buddy/runtime/test_run.py` | 7 | 0/63/0/0 | 51.0 | 58.6 | 89.2 | 47.8 | 51.8 |
| `buddy/runtime/test_windows_process.py` | 13 | 0/50/0/0 | 0.7 | 0.7 | 0.7 | 0.9 | 0.7 |
| `buddy/runtime/test_worker_invariants.py` | 11 | 0/52/0/0 | 1.2 | 1.4 | 1.2 | 4.3 | 1.3 |
| `buddy/runtime/test_worker_live_wiring.py` | 5 | 0/35/0/0 | 3.1 | 3.2 | 3.1 | 5.3 | 3.2 |
| `buddy/runtime/test_worker_runtime.py` | 4 | 0/42/0/0 | 98.7 | 25.5 | 134.7 | 47.7 F | 62.4 |
| `buddy/runtime/test_worker_sessions.py` | 3 | 0/22/0/0 | 0.6 | 0.7 | 0.7 | 1.5 | 0.6 |
| `cli/test_blocking.py` | 26 | 1/162/0/2 | 0.3 | 0.3 | 0.3 | 0.8 | 0.3 |
| `cli/test_checks_cleanup.py` | 15 | 0/58/0/0 | 5.9 | 6.2 | 6.9 | 9.7 | 6.1 |
| `cli/test_checks_focused.py` | 14 | 0/57/0/0 | 1.0 | 1.1 | 1.2 | 1.9 | 1.1 |
| `cli/test_checks_parallel.py` | 14 | 0/49/1/1 | 5.7 | 6.3 | 6.5 | 8.9 | 5.9 |
| `cli/test_cli.py` | 20 | 0/112/0/0 | 0.7 | 0.8 | 1.1 | 1.2 | 0.8 |
| `cli/test_cli_views.py` | 11 | 0/73/0/0 | 18.4 | 20.1 | 20.1 | 28.3 | 20.2 |
| `cli/test_harness_test_safety.py` | 3 | 0/9/0/0 | 1.5 | 2.1 | 1.9 | 2.9 | 1.5 |
| `cli/test_host_cli.py` | 35 | 0/145/0/2 | 4.6 | 5.7 | 5.8 | 11.1 | 5.0 |
| `cli/test_host_preview.py` | 3 | 0/67/0/2 | 0.4 F | 0.4 F | 0.4 F | 0.5 F | 0.4 F |
| `cli/test_repository_hygiene.py` | 5 | 0/5/0/0 | 0.2 | 0.4 | 0.3 | 0.4 | 0.3 |
| `cli/test_skill_workflow.py` | 12 | 0/22/0/0 | 0.1 | 0.1 | 0.1 | 0.2 | 0.1 |
| `cli/test_support_cleanup.py` | 8 | 0/53/1/0 | 5.6 | 7.3 | 7.2 | 13.0 | 7.1 |
| `console/test_console.py` | 20 | 2/217/0/0 | 27.3 | 34.8 | 45.1 | 30.6 | 30.2 |
| `console/test_console_access.py` | 8 | 0/57/0/0 | 6.4 | 6.4 | 6.1 | 7.0 | 5.6 |
| `console/test_console_cli.py` | 36 | 1/141/0/1 | 0.3 | 0.4 | 0.3 | 0.9 | 0.4 |
| `console/test_console_objective_cache.py` | 8 | 0/57/0/0 | 14.4 | 10.3 | 9.2 | 13.1 | 10.3 |
| `console/test_console_reads_http.py` | 19 | 1/136/0/0 | 13.6 | 12.0 | 11.7 | 12.8 | 12.2 |
| `console/test_console_sessions.py` | 16 | 0/70/0/0 | 21.9 | 19.9 | 18.6 | 23.9 | 19.2 |
| `console/test_console_version_info.py` | 17 | 0/86/2/0 | 3.8 | 3.2 | 2.9 | 3.9 | 3.5 |
| `install/test_backup.py` | 12 | 0/67/1/0 | 5.3 | 4.4 | 3.6 | 7.9 | 4.4 |
| `install/test_backup_preflight.py` | 12 | 0/50/0/1 | 2.8 | 2.3 | 1.7 | 2.9 | 2.1 |
| `install/test_backup_windows.py` | 7 | 0/22/0/0 | 11.0 | 9.2 | 7.5 | 10.0 | 7.9 |
| `install/test_distribution.py` | 2 | 0/22/0/0 | 7.0 | 7.0 | 6.0 | 46.1 F | 5.4 |
| `install/test_first_install.py` | 12 | 0/60/0/0 | 52.3 | 416.4 F | 234.8 F | 120.0 | 132.0 |
| `install/test_launcher.py` | 5 | 0/16/0/0 | 2.0 | 1.7 | 1.0 | 2.2 | 1.6 |
| `install/test_launcher_selection.py` | 5 | 0/13/0/0 | 0.6 | 0.4 | 0.3 | 0.5 | 0.3 |
| `install/test_local_launcher.py` | 3 | 0/8/0/0 | 0.3 | 0.2 | 0.2 | 0.2 | 0.2 |
| `install/test_packaging.py` | 12 | 0/46/0/0 | 67.6 | 107.2 | 45.0 | 154.5 F | 31.6 |
| `install/test_runtime.py` | 33 | 0/142/0/0 | 7.3 | 8.3 | 5.9 | 8.0 | 6.6 |
| `install/test_skill_install.py` | 6 | 0/21/0/0 | 1.8 | 2.0 | 1.6 | 2.4 | 1.4 |
| `install/test_source_identity.py` | 2 | 0/3/0/0 | 0.1 | 0.1 | 0.1 | 0.1 | 0.1 |
| `install/test_upgrade.py` | 18 | 0/77/0/0 | 2.2 | 2.6 | 1.5 | 2.3 | 1.5 |
| `install/test_upgrade_archive.py` | 6 | 0/47/0/0 | 13.7 | 13.1 | 14.3 | 15.4 | 11.4 |
| `install/test_upgrade_host_cutover.py` | 2 | 0/48/1/1 | 34.5 | 31.9 | 36.9 | 39.6 | 29.9 |
| `install/test_upgrade_migration.py` | 3 | 0/14/0/0 | 6.9 | 5.7 | 5.3 | 6.6 | 4.8 |
| `install/test_upgrade_service.py` | 1 | 0/9/0/0 | 5.5 | 5.3 | 5.4 | 5.8 | 4.7 |
| `install/test_upgrade_waiting_host.py` | 15 | 0/59/0/0 | 2.2 | 3.2 | 4.0 | 3.8 | 2.4 |
| `protocol/test_activity.py` | 15 | 0/86/0/0 | 1.4 | 2.2 | 2.7 | 2.4 | 2.2 |
| `protocol/test_billing_quota.py` | 12 | 0/55/0/0 | 2.3 | 3.8 | 4.4 | 4.3 | 3.2 |
| `protocol/test_ctwo_integration.py` | 2 | 0/43/1/0 | 11.9 | 14.0 | 16.1 | 15.2 | 13.2 |
| `protocol/test_ctwo_service.py` | 4 | 0/30/2/0 | 22.3 | 26.0 | 34.9 | 27.8 | 23.5 |
| `protocol/test_inquiry_transport.py` | 8 | 0/40/0/0 | 0.3 | 0.3 | 0.4 | 0.3 | 0.3 |
| `protocol/test_path_state.py` | 12 | 14/46/1/2 | 6.4 | 7.5 | 9.5 | 8.6 | 6.7 |
| `protocol/test_public_state.py` | 26 | 33/28/0/5 | 38.8 | 30.5 | 36.1 | 37.7 | 29.3 |
| `protocol/test_rpc_config.py` | 24 | 0/139/1/5 | 8.6 | 12.4 | 13.7 | 11.2 | 11.4 |
| `protocol/test_state_boundaries.py` | 11 | 0/38/0/0 | 1.0 | 1.5 | 1.9 | 1.3 | 1.4 |
| `protocol/test_tool_evidence.py` | 37 | 0/88/0/0 | 0.1 | 0.1 | 0.1 | 0.1 | 0.1 |
| `protocol/test_transport_attach.py` | 29 | 1/123/0/0 | 3.0 | 1.4 | 2.1 | 1.8 | 1.9 |
| `protocol/test_usage.py` | 31 | 0/88/0/0 | 0.2 | 0.1 | 0.1 | 0.1 | 0.1 |
| `protocol/test_workspace_access.py` | 2 | 0/5/0/0 | 0.1 | 0.1 | 0.1 | 0.1 | 0.1 |
| `test_locking.py` | 3 | 0/4/0/0 | 0.1 | 0.1 | 0.1 | 0.1 | 0.1 |
| `test_private_directories.py` | 6 | 0/19/0/1 | 1.1 | 0.4 | 0.6 | 0.4 | 0.4 |

## 附录 B：重文件的逐条时间

下列每个标题给出模块前缀，表中的 `类.方法` 与之连接就是完整测试编号。总计时从 unittest startTest 到 stopTest，包含该例的 setUp、测试体、tearDown 和 cleanups；类／模块夹具与导入不分摊给各条。剖析采用独立的 4 进程批次，其时间用于归因，不冒充完整检查同一轮的逐条分解。WorkspaceTests 的 borrowed_setup 用例在测试体内再运行两个 unittest，用例总时间仍精确计一次；其阶段包容计时有重叠，阶段列列为 —，不将重叠数充作省时依据。

### `blackboard.tasks.test_workspace_lifecycle`

49 条；剖析文件墙钟 410.408 秒，逐条合计 410.176 秒，类／模块准备及运行器剩余 0.233 秒；失败 0、错误 0。

| 类.方法 | 总计 | 准备 | 测试体 | 清理（含 tearDown） | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| `LifecycleTestCase.test_a_blocked_reclaim_needs_a_fresh_removal_for_the_same_exact_path` | 6.550 | 0.035 | 6.502 | 0.012 | ok |
| `LifecycleTestCase.test_a_borrowed_run_never_authorizes_its_own_cleanup` | 12.213 | 0.029 | 12.164 | 0.020 | ok |
| `LifecycleTestCase.test_a_forged_adopt_record_authorizes_nothing_for_conclude` | 9.276 | 0.037 | 9.231 | 0.008 | ok |
| `LifecycleTestCase.test_a_helper_that_only_borrowed_the_worktree_cannot_clean_it` | 5.472 | 0.032 | 5.431 | 0.009 | ok |
| `LifecycleTestCase.test_a_missing_registration_and_a_foreign_lock_keep_their_own_reasons` | 6.211 | 0.033 | 6.156 | 0.022 | ok |
| `LifecycleTestCase.test_a_new_writer_cannot_claim_the_checkout_across_the_remove_boundary` | 7.580 | 0.038 | 7.529 | 0.013 | ok |
| `LifecycleTestCase.test_a_stale_output_record_never_accepts_a_newer_final_artifact` | 8.262 | 0.034 | 8.217 | 0.011 | ok |
| `LifecycleTestCase.test_a_takeover_between_review_and_reclaim_blocks_only_the_removal` | 5.671 | 0.040 | 5.618 | 0.013 | ok |
| `LifecycleTestCase.test_a_target_detached_during_git_work_is_refused` | 6.115 | 0.042 | 6.057 | 0.016 | ok |
| `LifecycleTestCase.test_accept_refuses_a_detached_target_before_any_git_work` | 5.481 | 0.038 | 5.421 | 0.022 | ok |
| `LifecycleTestCase.test_accept_refuses_an_unrelated_commit_interval` | 7.122 | 0.047 | 7.063 | 0.012 | ok |
| `LifecycleTestCase.test_acceptance_payload_binds_the_recorded_review` | 4.929 | 0.036 | 4.886 | 0.006 | ok |
| `LifecycleTestCase.test_acceptance_requires_a_verified_or_not_required_decision` | 4.892 | 0.034 | 4.851 | 0.007 | ok |
| `LifecycleTestCase.test_after_the_recorded_detach_the_reclaim_completes` | 7.087 | 0.045 | 7.025 | 0.017 | ok |
| `LifecycleTestCase.test_an_accepted_helper_allocation_cleans_after_the_parent_is_accepted` | 13.167 | 0.036 | 13.109 | 0.022 | ok |
| `LifecycleTestCase.test_an_attached_branch_blocks_removal_with_the_branch_and_the_detach_retry` | 5.608 | 0.032 | 5.563 | 0.013 | ok |
| `LifecycleTestCase.test_apply_reports_the_same_branch_diagnostic_as_the_inspection` | 5.525 | 0.033 | 5.479 | 0.012 | ok |
| `LifecycleTestCase.test_cleanup_is_blocked_by_unsealed_changes` | 6.787 | 0.034 | 6.740 | 0.013 | ok |
| `LifecycleTestCase.test_cleanup_keeps_a_foreign_stale_worktree_registry_entry` | 5.841 | 0.035 | 5.794 | 0.011 | ok |
| `LifecycleTestCase.test_cleanup_never_targets_a_source_checkout_or_foreign_path` | 6.713 | 0.056 | 6.641 | 0.016 | ok |
| `LifecycleTestCase.test_cleanup_remove_is_idempotent_for_an_already_removed_allocation` | 5.932 | 0.040 | 5.876 | 0.016 | ok |
| `LifecycleTestCase.test_cleanup_remove_keeps_a_real_git_failure_on_a_present_checkout` | 7.310 | 0.109 | 7.134 | 0.067 | ok |
| `LifecycleTestCase.test_cleanup_removes_only_the_registered_worktree_and_is_idempotent` | 13.336 | 0.075 | 13.243 | 0.017 | ok |
| `LifecycleTestCase.test_continuation_cleanup_blocks_a_change_after_the_latest_seal` | 22.079 | 0.091 | 21.960 | 0.027 | ok |
| `LifecycleTestCase.test_continuation_cleanup_removes_the_original_physical_allocation` | 20.115 | 0.075 | 20.021 | 0.020 | ok |
| `LifecycleTestCase.test_deletion_and_mode_change_are_bound_to_final_tree` | 11.248 | 0.159 | 11.063 | 0.025 | ok |
| `LifecycleTestCase.test_delivered_output_without_integration_blocks_the_reclaim` | 5.143 | 0.095 | 5.034 | 0.013 | ok |
| `LifecycleTestCase.test_final_noop_continuation_still_requires_earlier_goal_change` | 23.803 | 0.038 | 23.710 | 0.054 | ok |
| `LifecycleTestCase.test_missing_or_spoofed_source_objects_cannot_verify` | 6.495 | 0.156 | 6.320 | 0.019 | ok |
| `LifecycleTestCase.test_reclaim_after_adopt_counts_the_adopted_paths` | 8.032 | 0.043 | 7.980 | 0.009 | ok |
| `LifecycleTestCase.test_reclaim_converges_when_another_owner_finishes_the_removal` | 6.102 | 0.037 | 6.053 | 0.012 | ok |
| `LifecycleTestCase.test_reclaim_converges_when_the_checkout_vanishes_during_the_proof` | 4.587 | 0.037 | 4.536 | 0.014 | ok |
| `LifecycleTestCase.test_reclaim_does_not_report_a_dangling_symlink_as_removed` | 4.340 | 0.035 | 4.290 | 0.016 | ok |
| `LifecycleTestCase.test_reclaim_helper_uses_the_control_roots_revision` | 8.851 | 0.038 | 8.795 | 0.018 | ok |
| `LifecycleTestCase.test_reclaim_reports_a_checkout_that_vanished_before_the_delete` | 3.584 | 0.034 | 3.536 | 0.014 | ok |
| `LifecycleTestCase.test_restart_abandon_returns_input_and_preserves_the_failed_site` | 7.207 | 0.036 | 7.153 | 0.018 | ok |
| `LifecycleTestCase.test_restart_cannot_remove_a_copied_worktree_git_directory` | 4.262 | 0.068 | 4.177 | 0.016 | ok |
| `LifecycleTestCase.test_restart_native_seams_continue_seal_integrate_and_reclaim_allocation` | 13.474 | 0.036 | 13.423 | 0.015 | ok |
| `LifecycleTestCase.test_restart_preserves_accepted_target_and_cleanup_plan_before_apply` | 8.251 | 0.038 | 8.200 | 0.013 | ok |
| `LifecycleTestCase.test_restart_scope_restore_keeps_the_failure_and_continues_helper` | 16.028 | 0.037 | 15.978 | 0.013 | ok |
| `LifecycleTestCase.test_retained_output_without_path_summary_uses_fixed_git_objects` | 9.462 | 0.043 | 9.403 | 0.016 | ok |
| `LifecycleTestCase.test_root_control_amends_a_helper_scope` | 5.482 | 0.038 | 5.429 | 0.015 | ok |
| `LifecycleTestCase.test_root_control_resolves_a_helper_scope_conflict` | 6.977 | 0.037 | 6.928 | 0.011 | ok |
| `LifecycleTestCase.test_root_control_targets_an_independently_allocated_helper` | 8.638 | 0.035 | 8.582 | 0.021 | ok |
| `LifecycleTestCase.test_root_takeover_fences_a_stale_helper_target_command` | 8.950 | 0.036 | 8.897 | 0.017 | ok |
| `LifecycleTestCase.test_started_reclaim_recovers_after_interruption_without_removing_twice` | 6.505 | 0.035 | 6.456 | 0.013 | ok |
| `LifecycleTestCase.test_storage_apply_reuses_workspace_protection_and_current_revision` | 7.668 | 0.052 | 7.604 | 0.012 | ok |
| `LifecycleTestCase.test_storage_native_removal_resumes_after_interrupted_delete` | 8.670 | 0.035 | 8.622 | 0.012 | ok |
| `LifecycleTestCase.test_verified_integration_binds_the_actual_target_checkout` | 7.141 | 0.035 | 7.086 | 0.020 | ok |

### `blackboard.tasks.test_workflow_preparation`

30 条；剖析文件墙钟 168.624 秒，逐条合计 168.424 秒，类／模块准备及运行器剩余 0.200 秒；失败 0、错误 0。

| 类.方法 | 总计 | 准备 | 测试体 | 清理（含 tearDown） | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| `PreparationTests.test_authorized_helper_seal_becomes_the_same_checkout_handoff` | 6.554 | 0.041 | 6.502 | 0.011 | ok |
| `PreparationTests.test_cancel_during_git_cannot_activate_prepared_files` | 3.959 | 0.031 | 3.923 | 0.006 | ok |
| `PreparationTests.test_checkout_change_during_git_cannot_overwrite_current_allocation` | 5.095 | 0.030 | 5.054 | 0.010 | ok |
| `PreparationTests.test_claim_gate_cannot_fall_back_to_original_manifest` | 2.435 | 0.039 | 2.390 | 0.006 | ok |
| `PreparationTests.test_concurrent_preparation_lock_retains_continuation_and_allows_recovery` | 5.093 | 0.033 | 5.051 | 0.009 | ok |
| `PreparationTests.test_continuation_attention_retains_actual_bounded_git_diagnostics` | 2.584 | 0.031 | 2.547 | 0.005 | ok |
| `PreparationTests.test_failed_helper_admission_revokes_only_its_new_allocation` | 4.917 | 0.043 | 4.868 | 0.005 | ok |
| `PreparationTests.test_handoff_ignores_outputs_from_other_actual_checkouts` | 4.661 | 0.032 | 4.617 | 0.012 | ok |
| `PreparationTests.test_helper_output_reference_never_impersonates_a_new_failed_attempt` | 6.614 | 0.029 | 6.573 | 0.012 | ok |
| `PreparationTests.test_latest_seal_uses_publication_order_after_clock_rollback` | 7.126 | 0.033 | 7.084 | 0.009 | ok |
| `PreparationTests.test_latest_seal_uses_publication_order_with_equal_timestamps` | 7.327 | 0.029 | 7.289 | 0.009 | ok |
| `PreparationTests.test_manual_continue_reclaims_released_helper_and_captures_partial_output` | 7.020 | 0.029 | 6.982 | 0.009 | ok |
| `PreparationTests.test_manual_override_during_git_cannot_activate_old_input` | 3.808 | 0.030 | 3.765 | 0.012 | ok |
| `PreparationTests.test_new_manual_input_supersedes_every_unconsumed_manual_intent` | 4.434 | 0.044 | 4.384 | 0.006 | ok |
| `PreparationTests.test_partial_recovery_is_fenced_by_a_new_manual_input` | 9.857 | 0.033 | 9.811 | 0.013 | ok |
| `PreparationTests.test_preparation_attention_default_view_names_the_git_operation` | 2.720 | 0.034 | 2.679 | 0.007 | ok |
| `PreparationTests.test_preparation_reason_line_is_bounded_and_redacted` | 0.047 | 0.031 | 0.012 | 0.003 | ok |
| `PreparationTests.test_queued_continuation_with_lost_ownership_becomes_visible_attention` | 4.217 | 0.030 | 4.179 | 0.009 | ok |
| `PreparationTests.test_real_worktree_continuation_retains_checkout_and_source_files` | 5.208 | 0.031 | 5.169 | 0.008 | ok |
| `PreparationTests.test_released_helper_cannot_reclaim_another_readers_checkout` | 6.404 | 0.030 | 6.365 | 0.009 | ok |
| `PreparationTests.test_released_helper_cannot_reclaim_another_writers_checkout` | 5.970 | 0.030 | 5.931 | 0.009 | ok |
| `PreparationTests.test_success_updates_current_reservation_without_rewriting_original_input` | 4.246 | 0.034 | 4.206 | 0.007 | ok |
| `PreparationTests.test_takeover_during_git_cannot_activate_old_owner_preparation` | 3.785 | 0.031 | 3.746 | 0.007 | ok |
| `PreparationTests.test_transferred_checkout_is_not_scanned_by_preparation` | 3.797 | 0.031 | 3.760 | 0.006 | ok |
| `PreparationTests.test_unvalidated_seal_in_failed_report_cannot_block_partial_recovery` | 6.914 | 0.031 | 6.872 | 0.012 | ok |
| `PreparationTests.test_workspace_drift_becomes_attention_and_another_task_can_be_claimed` | 5.184 | 0.033 | 5.141 | 0.011 | ok |
| `PreparationTests.test_wrong_prepared_checkout_becomes_attention` | 3.956 | 0.031 | 3.914 | 0.011 | ok |
| `RestartPreparationTests.test_initial_immutable_input_wins_over_later_manifest_reservation_and_cwd` | 3.523 | 0.145 | 3.365 | 0.012 | ok |
| `RestartPreparationTests.test_restart_failed_helper_partial_recovery_uses_the_same_checkout` | 10.071 | 0.140 | 9.915 | 0.016 | ok |
| `RestartPreparationTests.test_restart_helper_transfer_handoff_and_return_keep_single_ownership` | 20.898 | 0.271 | 20.613 | 0.014 | ok |

### `blackboard.tasks.test_storage_orphans`

36 条；剖析文件墙钟 166.636 秒，逐条合计 166.410 秒，类／模块准备及运行器剩余 0.226 秒；失败 0、错误 0。

| 类.方法 | 总计 | 准备 | 测试体 | 清理（含 tearDown） | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| `StorageOrphanTests.test_absent_checkouts_and_host_owned_records_need_no_reference_scan` | 1.416 | 0.065 | 1.346 | 0.006 | ok |
| `StorageOrphanTests.test_all_integration_states_and_path_identity_alias_forms_are_retained` | 5.276 | 0.042 | 5.227 | 0.007 | ok |
| `StorageOrphanTests.test_apply_rechecks_database_after_inventory` | 5.809 | 0.051 | 5.751 | 0.007 | ok |
| `StorageOrphanTests.test_apply_refuses_changed_original_manifest_path` | 2.305 | 0.044 | 2.254 | 0.007 | ok |
| `StorageOrphanTests.test_apply_refuses_lock_replacement` | 3.767 | 0.048 | 3.712 | 0.007 | ok |
| `StorageOrphanTests.test_apply_refuses_new_unsealed_tracked_and_untracked_content` | 3.121 | 0.070 | 3.044 | 0.006 | ok |
| `StorageOrphanTests.test_apply_refuses_new_workflow_registration` | 4.229 | 0.052 | 4.170 | 0.008 | ok |
| `StorageOrphanTests.test_apply_refuses_physical_checkout_replacement` | 2.711 | 0.047 | 2.658 | 0.006 | ok |
| `StorageOrphanTests.test_apply_refuses_same_request_admission` | 2.432 | 0.047 | 2.379 | 0.006 | ok |
| `StorageOrphanTests.test_apply_refuses_symlink_replacement` | 2.318 | 0.046 | 2.264 | 0.007 | ok |
| `StorageOrphanTests.test_apply_refuses_unlock` | 3.123 | 0.049 | 3.068 | 0.006 | ok |
| `StorageOrphanTests.test_artifact_location_protects_checkout_even_without_manifest` | 4.508 | 0.044 | 4.456 | 0.007 | ok |
| `StorageOrphanTests.test_cleanup_plan_states_and_path_identity_alias_forms_cannot_use_orphan_bypass` | 5.315 | 0.044 | 5.264 | 0.006 | ok |
| `StorageOrphanTests.test_concurrent_same_request_admission_holds_allocation_lock` | 3.297 | 0.046 | 3.234 | 0.017 | ok |
| `StorageOrphanTests.test_database_writer_fence_survives_until_actual_removal` | 4.218 | 0.045 | 4.167 | 0.006 | ok |
| `StorageOrphanTests.test_directory_identity_is_bound_only_to_original_request` | 5.219 | 0.045 | 5.166 | 0.007 | ok |
| `StorageOrphanTests.test_fixed_refs_must_remain_direct_exact_commit_objects` | 3.136 | 0.043 | 3.088 | 0.005 | ok |
| `StorageOrphanTests.test_ignored_new_content_blocks_orphan_inspection` | 3.048 | 0.046 | 2.995 | 0.006 | ok |
| `StorageOrphanTests.test_invalidated_continuation_still_protects_original_input` | 3.760 | 0.044 | 3.707 | 0.008 | ok |
| `StorageOrphanTests.test_known_allocation_with_rejected_ownership_is_never_an_orphan` | 1.587 | 0.046 | 1.534 | 0.006 | ok |
| `StorageOrphanTests.test_late_allocation_record_change_after_physical_proof_is_retained` | 4.023 | 0.040 | 3.976 | 0.007 | ok |
| `StorageOrphanTests.test_late_raw_request_alias_reference_is_rechecked_inside_writer_fence` | 5.927 | 0.046 | 5.873 | 0.009 | ok |
| `StorageOrphanTests.test_late_reference_after_physical_proof_is_rechecked_in_writer_transaction` | 4.132 | 0.051 | 4.074 | 0.007 | ok |
| `StorageOrphanTests.test_output_artifact_location_protects_allocation` | 4.629 | 0.052 | 4.570 | 0.007 | ok |
| `StorageOrphanTests.test_planning_streams_rows_and_parses_each_field_once_for_multiple_orphans` | 5.729 | 0.054 | 5.618 | 0.056 | ok |
| `StorageOrphanTests.test_proven_orphan_removes_only_checkout_and_replays_receipt` | 4.422 | 0.063 | 4.354 | 0.005 | ok |
| `StorageOrphanTests.test_public_integration_after_apply_inventory_is_rechecked_inside_writer_fence` | 7.069 | 0.057 | 7.004 | 0.008 | ok |
| `StorageOrphanTests.test_public_integration_after_plan_blocks_removal` | 6.067 | 0.047 | 6.013 | 0.008 | ok |
| `StorageOrphanTests.test_public_integration_before_inventory_protects_unchanged_target` | 7.793 | 0.045 | 7.739 | 0.008 | ok |
| `StorageOrphanTests.test_raw_request_alias_reference_protects_only_its_orphan_during_plan_and_apply` | 9.517 | 0.045 | 9.463 | 0.009 | ok |
| `StorageOrphanTests.test_raw_request_reference_survives_an_unrelated_verified_identity_alias` | 3.709 | 0.048 | 3.652 | 0.009 | ok |
| `StorageOrphanTests.test_released_reservation_still_protects_allocation` | 4.625 | 0.051 | 4.567 | 0.007 | ok |
| `StorageOrphanTests.test_retained_scope_manifest_hash_alone_protects_allocation` | 7.390 | 0.048 | 7.333 | 0.009 | ok |
| `StorageOrphanTests.test_unknown_directories_remain_blocked_candidates` | 0.227 | 0.156 | 0.064 | 0.006 | ok |
| `StorageOrphanTests.test_unreadable_retained_artifact_keeps_reference_unproven` | 11.109 | 0.071 | 11.017 | 0.020 | ok |
| `StorageOrphanTests.test_workflow_artifact_manifest_protects_allocation` | 9.447 | 0.093 | 9.303 | 0.044 | ok |

### `blackboard.tasks.test_parallel_dispatch`

27 条；剖析文件墙钟 97.506 秒，逐条合计 97.471 秒，类／模块准备及运行器剩余 0.036 秒；失败 0、错误 0。

| 类.方法 | 总计 | 准备 | 测试体 | 清理（含 tearDown） | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| `DaemonPoolTests.test_a_desired_slot_that_disappears_is_restarted_by_the_pool` | 18.043 | 0.005 | 16.475 | 1.563 | ok |
| `DaemonPoolTests.test_an_explicitly_stopped_slot_stays_down_until_explicitly_restarted` | 18.473 | 0.003 | 16.959 | 1.511 | ok |
| `DaemonPoolTests.test_custom_worker_ids_outside_the_manifest_are_never_touched` | 4.973 | 0.002 | 3.892 | 1.079 | ok |
| `DaemonPoolTests.test_decision_completes_on_the_pool_with_a_free_total_slot` | 7.893 | 0.003 | 6.215 | 1.676 | ok |
| `DaemonPoolTests.test_fresh_private_daemon_launches_the_pool_and_overlaps_two_business_attempts` | 5.518 | 0.002 | 3.960 | 1.556 | ok |
| `DaemonPoolTests.test_lowering_limits_retains_a_busy_surplus_worker_and_drains_it_later` | 13.820 | 0.004 | 13.380 | 0.435 | ok |
| `DaemonPoolTests.test_restart_preserves_pool_ids_and_the_same_attempt` | 6.316 | 0.002 | 4.916 | 1.398 | ok |
| `DaemonPoolTests.test_scale_up_withdraws_the_retire_intent_and_restores_desired_slots` | 6.504 | 0.002 | 4.987 | 1.515 | ok |
| `DaemonPoolTests.test_stop_reaches_every_managed_worker_and_confirms_shutdown` | 7.219 | 0.003 | 7.212 | 0.003 | ok |
| `HealthShapeTests.test_health_reports_total_limit_and_managed_worker_ids` | 0.300 | 0.002 | 0.296 | 0.001 | ok |
| `ModelFamilyFairnessTests.test_decision_waits_for_total_capacity_then_completes` | 0.705 | 0.002 | 0.699 | 0.003 | ok |
| `ModelFamilyFairnessTests.test_model_free_command_progresses_while_decision_family_is_full` | 0.125 | 0.002 | 0.122 | 0.001 | ok |
| `ModelFamilyFairnessTests.test_more_than_hundred_full_family_rows_cannot_hide_other_work` | 1.368 | 0.003 | 1.363 | 0.002 | ok |
| `RetireRaceTests.test_a_retire_intent_written_during_an_attempt_never_cancels_it` | 1.021 | 0.002 | 1.016 | 0.002 | ok |
| `RetireStartupTests.test_retire_at_startup_keeps_retrying_an_ambiguous_spawn_intent` | 4.155 | 0.003 | 4.150 | 0.002 | ok |
| `RetireStartupTests.test_retire_at_startup_reconciles_a_never_spawned_intent_before_retiring` | 0.100 | 0.002 | 0.096 | 0.002 | ok |
| `RetireStartupTests.test_retire_at_startup_replays_the_pending_receipt_and_never_claims` | 0.128 | 0.002 | 0.124 | 0.002 | ok |
| `SupervisorRetirementTests.test_supervisor_keeps_replaying_after_a_worker_exception_until_evidence_settles` | 0.050 | 0.000 | 0.049 | 0.001 | ok |
| `SurplusRetentionTests.test_an_explicit_stop_is_honored_and_the_slot_is_reported_unavailable` | 0.052 | 0.003 | 0.047 | 0.002 | ok |
| `SurplusRetentionTests.test_db_only_unresolved_surplus_is_retained_without_speculative_restarts` | 0.082 | 0.002 | 0.077 | 0.002 | ok |
| `SurplusRetentionTests.test_invalid_manifest_values_and_prefix_never_become_paths` | 0.006 | 0.002 | 0.003 | 0.001 | ok |
| `SurplusRetentionTests.test_missing_desired_slot_is_restarted_after_backoff_without_duplicate_spawns` | 0.054 | 0.002 | 0.044 | 0.008 | ok |
| `SurplusRetentionTests.test_missing_surplus_with_pending_receipt_is_retained_and_replayed_in_retirement_mode` | 0.070 | 0.004 | 0.064 | 0.002 | ok |
| `TotalCapacityTests.test_two_business_attempts_overlap_and_the_third_reports_capacity` | 0.123 | 0.002 | 0.119 | 0.001 | ok |
| `TotalCapacityTests.test_workspace_and_exclusive_resources_stay_serialized_with_free_capacity` | 0.133 | 0.002 | 0.130 | 0.001 | ok |
| `UncertainCapacityTests.test_uncertain_attempts_keep_their_total_and_family_slots` | 0.196 | 0.003 | 0.192 | 0.001 | ok |
| `WorkerPoolManifestTests.test_manifest_ownership_and_scale_up_reclaim` | 0.045 | 0.002 | 0.040 | 0.002 | ok |

### `buddy.runtime.test_worker_runtime`

4 条；剖析文件墙钟 124.973 秒，逐条合计 124.920 秒，类／模块准备及运行器剩余 0.053 秒；失败 0、错误 0。

| 类.方法 | 总计 | 准备 | 测试体 | 清理（含 tearDown） | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| `StagedWorkerRuntimeTests.test_staged_launcher_adds_runtime_worker_that_survives_stage_replacement` | 124.230 | 0.295 | 121.507 | 2.427 | ok |
| `WorkerLaunchEnvironmentTests.test_dev_source_with_private_empty_runtime_keeps_source_semantics` | 0.686 | 0.000 | 0.686 | 0.000 | ok |
| `WorkerLaunchEnvironmentTests.test_stable_target_replaces_inherited_cli_python_paths` | 0.002 | 0.000 | 0.002 | 0.000 | ok |
| `WorkerLaunchEnvironmentTests.test_worker_stop_does_not_select_runtime_or_spawn` | 0.002 | 0.000 | 0.002 | 0.000 | ok |

### `blackboard.store.test_blackboard`

57 条；剖析文件墙钟 144.123 秒，逐条合计 143.947 秒，类／模块准备及运行器剩余 0.175 秒；失败 0、错误 0。

| 类.方法 | 总计 | 准备 | 测试体 | 清理（含 tearDown） | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| `TestAtomicity.test_failed_artifact_validation_publishes_no_success_event` | 0.439 | 0.000 | 0.438 | 0.001 | ok |
| `TestAtomicity.test_injected_failure_between_state_and_event_rolls_back` | 0.349 | 0.001 | 0.346 | 0.002 | ok |
| `TestAtomicity.test_reconnect_sees_only_committed_state_and_integrity_passes` | 0.214 | 0.000 | 0.212 | 0.001 | ok |
| `TestAtomicity.test_state_change_and_event_commit_together` | 0.116 | 0.000 | 0.113 | 0.003 | ok |
| `TestConcurrency.test_a_different_worker_cannot_replay_another_workers_claim_receipt` | 0.281 | 0.000 | 0.279 | 0.001 | ok |
| `TestConcurrency.test_capacity_queues_instead_of_rejecting` | 0.159 | 0.000 | 0.157 | 0.002 | ok |
| `TestConcurrency.test_changed_idempotency_input_conflicts` | 0.157 | 0.002 | 0.155 | 0.001 | ok |
| `TestConcurrency.test_conflict_survives_completion_and_restart` | 0.112 | 0.000 | 0.111 | 0.001 | ok |
| `TestConcurrency.test_duplicate_claim_with_the_same_command_replays_one_generation` | 0.156 | 0.000 | 0.155 | 0.001 | ok |
| `TestConcurrency.test_duplicate_submission_returns_the_same_task` | 0.089 | 0.000 | 0.087 | 0.001 | ok |
| `TestConcurrency.test_exclusive_resources_reserve` | 0.201 | 0.000 | 0.191 | 0.010 | ok |
| `TestConcurrency.test_one_worker_runs_one_attempt_at_a_time` | 0.534 | 0.000 | 0.533 | 0.001 | ok |
| `TestConcurrency.test_overlapping_cwd_reserves_resources` | 0.250 | 0.001 | 0.248 | 0.001 | ok |
| `TestConcurrency.test_retry_re_holds_released_resources` | 0.545 | 0.000 | 0.543 | 0.001 | ok |
| `TestConcurrency.test_revision_conflict_is_rejected` | 0.297 | 0.002 | 0.293 | 0.001 | ok |
| `TestCrashWindows.test_a_killed_worker_keeps_the_surviving_child_uncertain_and_unretryable` | 52.734 | 0.000 | 51.214 | 1.520 | ok |
| `TestCrashWindows.test_a_marker_write_failure_still_supervises_the_live_child` | 0.465 | 0.000 | 0.462 | 0.002 | ok |
| `TestCrashWindows.test_a_receipt_that_cannot_be_committed_is_replayed_never_re_executed` | 4.473 | 0.000 | 4.470 | 0.002 | ok |
| `TestCrashWindows.test_commit_before_lost_reply_is_replayed_not_duplicated` | 0.119 | 0.000 | 0.118 | 0.001 | ok |
| `TestCrashWindows.test_conflicting_replay_of_the_same_attempt_fails` | 0.114 | 0.000 | 0.111 | 0.002 | ok |
| `TestCrashWindows.test_crash_before_spawn_releases_the_attempt_honestly` | 0.114 | 0.000 | 0.112 | 0.001 | ok |
| `TestCrashWindows.test_native_collect_proof_survives_later_cleanup_failure` | 0.012 | 0.000 | 0.009 | 0.003 | ok |
| `TestCrashWindows.test_native_collect_proof_survives_later_receipt_failure` | 0.016 | 0.000 | 0.011 | 0.004 | ok |
| `TestCrashWindows.test_native_controller_exit_does_not_prove_native_stop_after_collect_failure` | 0.057 | 0.000 | 0.044 | 0.012 | ok |
| `TestCrashWindows.test_persistent_cleanup_error_cannot_erase_native_stop_or_prevent_receipt` | 0.013 | 0.000 | 0.009 | 0.003 | ok |
| `TestCrashWindows.test_retry_preparation_failure_preserves_previous_stop_proof` | 0.015 | 0.000 | 0.012 | 0.002 | ok |
| `TestCrashWindows.test_retry_start_discounts_previous_stop_proof_and_keeps_credentials` | 0.015 | 0.000 | 0.012 | 0.003 | ok |
| `TestCrashWindows.test_start_error_without_handle_does_not_retry_unknown_child` | 0.012 | 0.000 | 0.008 | 0.004 | ok |
| `TestCrashWindows.test_virtual_adapter_receipt_retains_evidence_failure_paths` | 0.010 | 0.000 | 0.007 | 0.002 | ok |
| `TestDelivery.test_answers_require_the_correlated_id` | 0.125 | 0.001 | 0.123 | 0.001 | ok |
| `TestDelivery.test_cancellation_race_has_one_legal_outcome` | 0.107 | 0.000 | 0.105 | 0.001 | ok |
| `TestDelivery.test_cancelled_without_confirmed_shutdown_retains_resources` | 0.124 | 0.000 | 0.123 | 0.001 | ok |
| `TestDelivery.test_correlated_answer_survives_a_daemon_restart` | 22.671 | 0.000 | 21.741 | 0.930 | ok |
| `TestDelivery.test_cursor_replay_and_bounded_pages` | 0.131 | 0.000 | 0.130 | 0.001 | ok |
| `TestDelivery.test_events_wait_returns_committed_events_and_times_out_honestly` | 0.264 | 0.000 | 0.263 | 0.001 | ok |
| `TestDelivery.test_inquiry_bounds_and_honest_unsupported_adapter` | 0.060 | 0.000 | 0.059 | 0.001 | ok |
| `TestDelivery.test_more_waiters_than_capacity_never_blocks_mutations` | 0.400 | 0.000 | 0.399 | 0.001 | ok |
| `TestDelivery.test_question_deduplication_and_conflict` | 0.072 | 0.000 | 0.071 | 0.001 | ok |
| `TestExtensibility.test_an_external_agent_completes_a_real_task_through_the_public_api` | 5.482 | 0.000 | 3.524 | 1.958 | ok |
| `TestExtensibility.test_capabilities_are_reported_honestly` | 2.170 | 0.000 | 2.169 | 0.001 | ok |
| `TestExtensibility.test_command_adapter_rejects_a_missing_executable_honestly` | 0.358 | 0.000 | 0.356 | 0.001 | ok |
| `TestExtensibility.test_command_adapter_runs_explicit_argv_without_a_shell` | 0.390 | 0.000 | 0.388 | 0.002 | ok |
| `TestExtensibility.test_external_task_is_not_claimed_by_a_built_in_worker` | 0.081 | 0.000 | 0.079 | 0.001 | ok |
| `TestIdentity.test_a_stored_pid_is_never_used_to_signal_a_process` | 0.091 | 0.000 | 0.088 | 0.002 | ok |
| `TestIdentity.test_lease_expiry_is_uncertain_not_stopped` | 0.082 | 0.000 | 0.081 | 0.001 | ok |
| `TestIdentity.test_stale_generation_is_rejected_and_cannot_mutate` | 0.096 | 0.000 | 0.095 | 0.001 | ok |
| `TestIdentity.test_wrong_capability_is_rejected` | 0.067 | 0.000 | 0.067 | 0.001 | ok |
| `TestLifecycle.test_restart_keeps_worker_receipts_and_claims` | 0.064 | 0.000 | 0.063 | 0.001 | ok |
| `TestLifecycle.test_work_survives_a_real_daemon_restart_and_reconnects_to_the_same_result` | 16.229 | 0.000 | 15.086 | 1.144 | ok |
| `TestPackaging.test_no_secrets_or_environments_are_copied_into_a_runtime` | 0.120 | 0.000 | 0.119 | 0.000 | ok |
| `TestPackaging.test_runtime_identity_is_reported_and_stable` | 1.300 | 0.000 | 1.299 | 0.000 | ok |
| `TestPackaging.test_the_default_cold_start_installs_and_reports_the_stable_runtime` | 30.907 | 0.000 | 28.834 | 2.072 | ok |
| `TestTaskHistory.test_a_page_is_bounded_and_never_materializes_the_whole_board` | 0.112 | 0.000 | 0.110 | 0.001 | ok |
| `TestTaskHistory.test_every_task_view_carries_the_delegation_contract` | 0.197 | 0.000 | 0.197 | 0.001 | ok |
| `TestTaskHistory.test_existing_filters_compose_and_the_cursor_pages_the_filtered_set` | 0.122 | 0.000 | 0.121 | 0.001 | ok |
| `TestTransitionTables.test_illegal_task_transition_is_rejected` | 0.057 | 0.000 | 0.056 | 0.001 | ok |
| `TestTransitionTables.test_tables_cover_every_declared_state` | 0.001 | 0.000 | 0.000 | 0.000 | ok |

### `blackboard.tasks.test_scope_recovery`

18 条；剖析文件墙钟 119.238 秒，逐条合计 119.100 秒，类／模块准备及运行器剩余 0.138 秒；失败 0、错误 0。

| 类.方法 | 总计 | 准备 | 测试体 | 清理（含 tearDown） | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| `ScopeRecoveryTestCase.test_a_native_record_that_no_longer_validates_is_not_delivered` | 10.048 | 0.406 | 9.629 | 0.013 | ok |
| `ScopeRecoveryTestCase.test_a_scope_failure_without_a_native_turn_is_never_delivered` | 7.837 | 0.428 | 7.398 | 0.012 | ok |
| `ScopeRecoveryTestCase.test_abandon_preserves_the_site_and_returns_the_authorized_input` | 6.696 | 0.215 | 6.473 | 0.008 | ok |
| `ScopeRecoveryTestCase.test_adopt_binds_the_exact_recorded_site` | 14.077 | 0.251 | 13.813 | 0.013 | ok |
| `ScopeRecoveryTestCase.test_amended_scope_applies_only_to_the_next_prepared_stage` | 8.110 | 0.292 | 7.804 | 0.014 | ok |
| `ScopeRecoveryTestCase.test_continuation_never_absorbs_the_unauthorized_failed_site` | 5.371 | 0.440 | 4.902 | 0.029 | ok |
| `ScopeRecoveryTestCase.test_current_contract_runs_require_a_real_scope_v1_row` | 1.846 | 0.197 | 1.642 | 0.007 | ok |
| `ScopeRecoveryTestCase.test_helper_admission_records_its_own_scope_v1` | 4.845 | 0.146 | 4.688 | 0.011 | ok |
| `ScopeRecoveryTestCase.test_native_assistance_request_survives_as_the_next_boundary` | 10.029 | 0.246 | 9.740 | 0.043 | ok |
| `ScopeRecoveryTestCase.test_native_scope_only_failure_is_delivered_by_a_full_restore` | 15.212 | 0.935 | 14.259 | 0.018 | ok |
| `ScopeRecoveryTestCase.test_native_scope_only_failure_is_delivered_by_adoption` | 4.512 | 0.149 | 4.354 | 0.009 | ok |
| `ScopeRecoveryTestCase.test_partial_restore_and_abandon_never_deliver` | 4.714 | 0.146 | 4.556 | 0.012 | ok |
| `ScopeRecoveryTestCase.test_partial_restore_reports_the_remaining_paths` | 5.412 | 0.165 | 5.238 | 0.009 | ok |
| `ScopeRecoveryTestCase.test_restore_compare_and_swap_preserves_a_later_change` | 4.067 | 0.140 | 3.920 | 0.007 | ok |
| `ScopeRecoveryTestCase.test_restore_is_idempotent_and_the_failed_receipt_is_unchanged` | 4.581 | 0.143 | 4.429 | 0.009 | ok |
| `ScopeRecoveryTestCase.test_restore_preserves_legal_changes_and_reseals_without_a_model_turn` | 6.733 | 0.153 | 6.569 | 0.010 | ok |
| `ScopeRecoveryTestCase.test_scope_amendment_is_versioned_and_fenced_by_an_active_attempt` | 2.677 | 0.167 | 2.503 | 0.006 | ok |
| `ScopeRecoveryTestCase.test_scope_failure_records_evidence_and_opens_a_host_attention` | 2.329 | 0.151 | 2.171 | 0.007 | ok |

### `blackboard.tasks.test_workspace`

26 条；剖析文件墙钟 95.544 秒，逐条合计 95.313 秒，类／模块准备及运行器剩余 0.231 秒；失败 0、错误 0。

| 类.方法 | 总计 | 准备 | 测试体 | 清理（含 tearDown） | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| `WorkspaceTests.test_borrowed_setup_without_a_template_preserves_router_input_capture` | 7.161 | — | — | — | ok（嵌套用例） |
| `WorkspaceTests.test_changed_interrupted_checkout_is_not_overwritten` | 5.530 | 0.086 | 5.433 | 0.009 | ok |
| `WorkspaceTests.test_completed_checkout_recovers_after_interrupted_manifest_write` | 9.562 | 0.216 | 9.333 | 0.012 | ok |
| `WorkspaceTests.test_dirty_input_preserves_staging_binary_modes_symlinks_and_source` | 7.311 | 0.123 | 7.174 | 0.014 | ok |
| `WorkspaceTests.test_existing_checkout_continuation_uses_exact_sealed_baseline` | 7.879 | 0.098 | 7.771 | 0.010 | ok |
| `WorkspaceTests.test_existing_commit_requires_exact_checkout` | 2.257 | 0.067 | 2.183 | 0.007 | ok |
| `WorkspaceTests.test_existing_excluded_inputs_are_not_accidentally_sealed` | 3.324 | 0.089 | 3.213 | 0.022 | ok |
| `WorkspaceTests.test_explicit_ignored_directory_is_bounded_and_checked` | 3.244 | 0.074 | 3.161 | 0.009 | ok |
| `WorkspaceTests.test_explicit_ignored_untracked_input_and_unusual_filenames` | 2.784 | 0.055 | 2.720 | 0.009 | ok |
| `WorkspaceTests.test_frozen_input_survives_gc_before_public_input_ref_publication` | 1.820 | 0.069 | 1.745 | 0.006 | ok |
| `WorkspaceTests.test_identity_unifies_siblings_and_distinguishes_linked_checkout` | 0.373 | 0.056 | 0.311 | 0.006 | ok |
| `WorkspaceTests.test_ignored_caches_do_not_interfere_with_managed_input_or_output` | 9.549 | 0.060 | 9.472 | 0.017 | ok |
| `WorkspaceTests.test_ignored_dependencies_and_unreadable_environments_are_not_read` | 3.067 | 0.062 | 2.931 | 0.075 | ok |
| `WorkspaceTests.test_malformed_manifest_and_input_fail_with_structured_errors` | 1.458 | 0.058 | 1.393 | 0.007 | ok |
| `WorkspaceTests.test_non_git_and_unborn_checkout_are_explicit_errors` | 0.232 | 0.066 | 0.159 | 0.008 | ok |
| `WorkspaceTests.test_output_ref_collision_and_diff_corruption_are_honest_conflicts` | 2.564 | 0.057 | 2.499 | 0.008 | ok |
| `WorkspaceTests.test_outside_scope_edits_and_new_files_are_reported` | 2.329 | 0.055 | 2.265 | 0.009 | ok |
| `WorkspaceTests.test_prepare_replay_freezes_source_and_changed_intent_conflicts` | 3.460 | 0.062 | 3.387 | 0.010 | ok |
| `WorkspaceTests.test_read_only_seal_rejects_index_only_and_untracked_drift` | 3.890 | 0.066 | 3.816 | 0.009 | ok |
| `WorkspaceTests.test_replayed_output_rejects_inconsistent_top_level_fields` | 2.806 | 0.055 | 2.738 | 0.012 | ok |
| `WorkspaceTests.test_seal_pins_actual_output_binary_diff_and_replays_after_removal` | 4.989 | 0.072 | 4.910 | 0.007 | ok |
| `WorkspaceTests.test_seal_recovers_original_output_after_interrupted_publication` | 2.866 | 0.070 | 2.790 | 0.007 | ok |
| `WorkspaceTests.test_sibling_cwd_maps_to_equivalent_subdirectory_in_isolated_tree` | 2.167 | 0.086 | 2.072 | 0.009 | ok |
| `WorkspaceTests.test_symbolic_input_reference_cannot_modify_a_branch` | 1.082 | 0.058 | 1.015 | 0.009 | ok |
| `WorkspaceTests.test_unknown_worktree_target_is_preserved` | 1.122 | 0.064 | 1.053 | 0.005 | ok |
| `WorkspaceTests.test_verify_detects_drift_and_read_cannot_opt_out` | 2.485 | 0.056 | 2.413 | 0.016 | ok |

### `blackboard.tasks.test_workflow_real`

11 条；剖析文件墙钟 85.900 秒，逐条合计 85.852 秒，类／模块准备及运行器剩余 0.048 秒；失败 0、错误 0。

| 类.方法 | 总计 | 准备 | 测试体 | 清理（含 tearDown） | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| `ContinuationCheckoutReuseTests.test_parent_continuation_bases_on_the_helpers_sealed_commit` | 9.332 | 0.136 | 9.183 | 0.013 | ok |
| `ContinuationCheckoutReuseTests.test_sequential_helper_and_parent_edit_keep_one_checkout_and_latest_baseline` | 14.326 | 0.158 | 14.135 | 0.032 | ok |
| `DirtyInputTests.test_continuation_bases_on_the_sealed_commit_and_seals_new_output` | 5.724 | 0.180 | 5.536 | 0.008 | ok |
| `DirtyInputTests.test_submit_transports_dirty_input_without_changing_the_source_index` | 2.539 | 0.156 | 2.376 | 0.006 | ok |
| `DirtyInputTests.test_worktree_helpers_get_distinct_effective_checkouts` | 3.421 | 0.135 | 3.277 | 0.009 | ok |
| `PinnedHelperHandoffTests.test_helper_refs_are_frozen_into_the_parent_continuation` | 16.450 | 0.151 | 16.283 | 0.016 | ok |
| `RealAdapterTests.test_adapter_verifies_input_writes_exact_bytes_and_gates_the_seal` | 5.584 | 0.130 | 5.443 | 0.011 | ok |
| `RealAdapterTests.test_collect_imports_a_turn_only_with_confirmed_shutdown` | 4.975 | 0.161 | 4.801 | 0.014 | ok |
| `TransferredCheckoutContinuationTests.test_parent_continuation_bases_on_the_helpers_sealed_commit` | 9.189 | 0.164 | 9.013 | 0.012 | ok |
| `WorktreeContinuationReuseTests.test_parent_continuation_bases_on_the_helpers_sealed_commit` | 8.767 | 0.169 | 8.586 | 0.011 | ok |
| `WorktreeContinuationReuseTests.test_worktree_origin_continuation_reuses_the_same_allocated_worktree` | 5.545 | 0.137 | 5.395 | 0.013 | ok |

### `install.test_packaging`

12 条；剖析文件墙钟 54.638 秒，逐条合计 54.365 秒，类／模块准备及运行器剩余 0.273 秒；失败 0、错误 0。

| 类.方法 | 总计 | 准备 | 测试体 | 清理（含 tearDown） | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| `CheckHarnessTests.test_the_check_harness_covers_all_python_harness_suites` | 0.020 | 0.000 | 0.020 | 0.000 | ok |
| `CheckHarnessTests.test_the_check_harness_sanitizes_inherited_runtime_and_worker_variables` | 0.001 | 0.000 | 0.001 | 0.000 | ok |
| `CheckHarnessTests.test_unrelated_checks_cannot_inherit_a_real_claude_cli` | 0.001 | 0.000 | 0.001 | 0.000 | ok |
| `CwdIndependentCliTests.test_the_launcher_is_cwd_independent` | 44.200 | 0.000 | 41.234 | 2.965 | ok |
| `CwdIndependentCliTests.test_the_launcher_reaches_the_private_cli_from_an_unrelated_cwd` | 9.770 | 0.000 | 5.338 | 4.432 | ok |
| `SingleEntrypointTests.test_the_declared_runtime_manifest_is_current_and_complete` | 0.349 | 0.000 | 0.349 | 0.000 | ok |
| `SingleEntrypointTests.test_the_only_agent_entrypoint_is_the_shared_skill_and_no_plugin_is_published` | 0.000 | 0.000 | 0.000 | 0.000 | ok |
| `SingleEntrypointTests.test_the_skill_carries_the_one_public_launcher` | 0.000 | 0.000 | 0.000 | 0.000 | ok |
| `SkillBuildTests.test_build_refuses_a_destination_not_named_buddy` | 0.001 | 0.000 | 0.001 | 0.000 | ok |
| `SkillBuildTests.test_layout_frontmatter_and_version_marker` | 0.001 | 0.000 | 0.001 | 0.000 | ok |
| `SkillBuildTests.test_links_stay_inside_the_skill` | 0.005 | 0.000 | 0.005 | 0.000 | ok |
| `SkillBuildTests.test_package_carries_runtime_assets_and_no_unsupported_content` | 0.018 | 0.000 | 0.018 | 0.000 | ok |

### `blackboard.tasks.test_host_workflow_worker`

4 条；剖析文件墙钟 65.263 秒，逐条合计 65.216 秒，类／模块准备及运行器剩余 0.047 秒；失败 0、错误 0。

| 类.方法 | 总计 | 准备 | 测试体 | 清理（含 tearDown） | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| `HostQuotaRecoveryTests.test_quota_failure_seals_then_continues_a_new_configuration_in_the_same_checkout` | 22.500 | 0.181 | 20.633 | 1.686 | ok |
| `HostQuotaRecoveryTests.test_quota_failure_cannot_override_a_user_locked_configuration` | 11.825 | 0.198 | 9.992 | 1.635 | ok |
| `HostQuotaRecoveryTests.test_failed_goal_conclusion_reclaims_checkout_but_retains_partial_artifacts` | 14.021 | 0.175 | 12.517 | 1.328 | ok |
| `CrossHarnessQuotaRecoveryTests.test_failed_codex_reconstructs_in_dsh_with_bound_partial_output_and_message` | 16.870 | 0.189 | 15.525 | 1.157 | ok |

### `blackboard.tasks.test_workspace_submission_cleanup`

20 条；剖析文件墙钟 63.909 秒，逐条合计 63.705 秒，类／模块准备及运行器剩余 0.204 秒；失败 0、错误 0。

| 类.方法 | 总计 | 准备 | 测试体 | 清理（含 tearDown） | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| `SubmissionCleanupTests.test_all_prepared_helpers_hold_locks_until_database_admission` | 7.620 | 0.036 | 7.572 | 0.011 | ok |
| `SubmissionCleanupTests.test_allocation_lock_fences_parallel_same_request_until_winner_admitted` | 1.644 | 0.035 | 1.602 | 0.007 | ok |
| `SubmissionCleanupTests.test_changed_new_checkout_is_retained_after_admission_rejection` | 2.512 | 0.036 | 2.470 | 0.006 | ok |
| `SubmissionCleanupTests.test_cleanup_failure_preserves_original_error_and_recovery_manifest` | 3.231 | 0.035 | 3.189 | 0.007 | ok |
| `SubmissionCleanupTests.test_committed_winner_survives_post_commit_failure` | 1.927 | 0.035 | 1.884 | 0.008 | ok |
| `SubmissionCleanupTests.test_error_before_transaction_revokes_checkout` | 2.582 | 0.051 | 2.525 | 0.006 | ok |
| `SubmissionCleanupTests.test_existing_managed_reuse_holds_original_allocation_through_admission` | 11.033 | 0.041 | 10.981 | 0.010 | ok |
| `SubmissionCleanupTests.test_existing_source_is_preserved_on_rejection` | 1.440 | 0.032 | 1.403 | 0.005 | ok |
| `SubmissionCleanupTests.test_failed_git_remove_restores_allocation_lock_and_keeps_admission_error` | 3.201 | 0.032 | 3.163 | 0.007 | ok |
| `SubmissionCleanupTests.test_failure_after_materialization_revokes_new_checkout` | 1.773 | 0.038 | 1.730 | 0.005 | ok |
| `SubmissionCleanupTests.test_git_add_created_checkout_before_error_is_revoked` | 1.425 | 0.031 | 1.389 | 0.005 | ok |
| `SubmissionCleanupTests.test_oserror_admission_keeps_original_type_and_recovery_note` | 2.503 | 0.036 | 2.461 | 0.006 | ok |
| `SubmissionCleanupTests.test_partial_materialization_failure_revokes_new_checkout` | 1.599 | 0.032 | 1.562 | 0.005 | ok |
| `SubmissionCleanupTests.test_preexisting_prepared_worktree_is_preserved_on_rejection` | 1.905 | 0.039 | 1.858 | 0.008 | ok |
| `SubmissionCleanupTests.test_public_integration_during_failed_admission_retains_new_target` | 4.540 | 0.035 | 4.493 | 0.012 | ok |
| `SubmissionCleanupTests.test_read_tree_failure_revokes_materialized_checkout` | 1.365 | 0.039 | 1.320 | 0.006 | ok |
| `SubmissionCleanupTests.test_real_project_admission_rejection_removes_only_new_checkout` | 4.107 | 0.033 | 4.064 | 0.010 | ok |
| `SubmissionCleanupTests.test_same_request_conflict_recovers_concurrent_winner` | 1.488 | 0.047 | 1.435 | 0.006 | ok |
| `SubmissionCleanupTests.test_transaction_failure_rolls_back_task_and_revokes_checkout` | 2.291 | 0.030 | 2.255 | 0.005 | ok |
| `SubmissionCleanupTests.test_two_read_helpers_share_one_original_allocation_lock` | 5.518 | 0.042 | 5.450 | 0.026 | ok |

### `install.test_first_install`

12 条；剖析文件墙钟 62.840 秒，逐条合计 62.813 秒，类／模块准备及运行器剩余 0.027 秒；失败 0、错误 0。

| 类.方法 | 总计 | 准备 | 测试体 | 清理（含 tearDown） | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| `DocumentationTests.test_operations_document_the_shared_skill_install` | 0.000 | 0.000 | 0.000 | 0.000 | ok |
| `DocumentationTests.test_readmes_keep_first_install_parity` | 0.001 | 0.000 | 0.001 | 0.000 | ok |
| `SkillInstallTests.test_a_different_version_replaces_the_skill_without_leftovers` | 43.416 | 0.000 | 40.748 | 2.667 | ok |
| `SkillInstallTests.test_a_failed_claude_link_fails_before_the_service_and_rerun_recovers` | 2.432 | 0.000 | 1.663 | 0.768 | ok |
| `SkillInstallTests.test_a_second_host_with_the_same_version_only_repairs_the_link` | 2.705 | 0.000 | 2.007 | 0.697 | ok |
| `SkillInstallTests.test_a_worker_credential_cannot_install` | 0.516 | 0.000 | 0.002 | 0.513 | ok |
| `SkillInstallTests.test_an_installed_skill_copies_itself_and_reports_a_legacy_plugin` | 1.843 | 0.000 | 1.051 | 0.791 | ok |
| `SkillInstallTests.test_first_install_places_the_skill_and_links_claude` | 1.806 | 0.000 | 1.084 | 0.721 | ok |
| `SkillInstallTests.test_foreign_directories_are_never_replaced` | 1.673 | 0.000 | 0.980 | 0.693 | ok |
| `SkillInstallTests.test_paths_reports_skill_data_and_runtime_locations_without_starting_anything` | 1.882 | 0.000 | 1.194 | 0.688 | ok |
| `SkillInstallTests.test_the_repository_launcher_installs_from_any_cwd` | 6.018 | 0.000 | 5.240 | 0.778 | ok |
| `SkillInstallTests.test_windows_data_root_is_local_app_data` | 0.522 | 0.000 | 0.001 | 0.520 | ok |

### `buddy.runtime.test_run`

7 条；剖析文件墙钟 47.191 秒，逐条合计 47.186 秒，类／模块准备及运行器剩余 0.005 秒；失败 0、错误 0。

| 类.方法 | 总计 | 准备 | 测试体 | 清理（含 tearDown） | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| `TestExecutionRecords.test_await_never_launches_work_and_honours_its_wait_window` | 10.114 | 0.000 | 8.622 | 1.491 | ok |
| `TestExecutionRecords.test_await_resolves_an_existing_run_by_request_id_without_starting_it` | 5.986 | 0.000 | 4.528 | 1.457 | ok |
| `TestExecutionRecords.test_execution_cancel_then_acknowledge_records_a_reviewed_outcome` | 7.755 | 0.000 | 6.945 | 0.810 | ok |
| `TestExecutionRecords.test_execution_submit_completes_one_real_task_and_await_delivers_its_result` | 6.130 | 0.000 | 4.548 | 1.582 | ok |
| `TestExecutionRecords.test_execution_submit_is_idempotent_for_the_same_request_id` | 5.565 | 0.000 | 3.485 | 2.079 | ok |
| `TestExecutionRecords.test_governed_get_and_execution_status_stay_distinct` | 5.773 | 0.000 | 3.474 | 2.299 | ok |
| `TestExecutionRecords.test_status_result_and_list_keep_the_stable_envelope` | 5.864 | 0.000 | 4.988 | 0.876 | ok |

## 附录 C：证据摘要与重放边界

- `full-1-j4`：开始 2026-10-13T14:06:54Z，墙钟 780.844 秒，退出 1，日志 SHA-256 `99bd6f2ee7da5c1abf31b863108450a7a21ce56fe444efb3670de9e5c6106fab`。
- `full-2-j6`：开始 2026-10-13T14:19:55Z，墙钟 772.196 秒，退出 1，日志 SHA-256 `7f1fb185b4bf05b3d7b8ac5ab794833270a0cf86209544fde08f71f438830a49`。
- `full-3-j8`：开始 2026-10-13T14:32:48Z，墙钟 565.931 秒，退出 1，日志 SHA-256 `dbd83864d3bacf60d59bc42bc52a7dccf4f09a771d8eb5669f3900eae5374068`。
- `full-4-j6`：开始 2026-10-13T14:42:13Z，墙钟 778.425 秒，退出 1，日志 SHA-256 `aef00f1239fcf40ee5a0a0eaeca91158e68046251d2d8201de0e0d32a70f8bd7`。
- `full-5-j8`：开始 2026-10-13T14:55:12Z，墙钟 419.711 秒，退出 1，日志 SHA-256 `710ade8e9f7f2af55e07456a592e1fbecf6a7ab76a958c1721381ad433e02773`。

实际发现清单：232 个调度文件、3868 条测试出现记录；清单 SHA-256 `957a9d5abaf64d1781f0e20c68e3535dcd558f086ee11fdb1bbdd42dbc31dcb1`。上表完整列出 232 个文件的统计及 313 条重文件测试的编号／时间；其余测试仅列文件级数量与断言计数，没有交付全部 3,868 条编号。完整发现清单、脚本和原始进程日志保存在检出外临时测量资料中，不是本微任务交付文件。

## 附录 D：生命周期文件拆分的逐条候选映射

这是未实施的备选。按断言原样移动，原模块保留 acceptance 组；另外三组对应 cleanup、helpers、recovery 三个新文件。同名类 `LifecycleTestCase` 保留，所有无 test_ 方法的夹具提取到 `workspace_lifecycle_fixture.py`。新的实际发现清单必须按这 49 行逐一核对，未列出的测试编号不变。收益不足以单独达成明显提速，因此须由 Host 先决定是否值得承担迁移审查。

| 原测试编号 | 候选新测试编号 |
| --- | --- |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_acceptance_requires_a_verified_or_not_required_decision` | `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_acceptance_requires_a_verified_or_not_required_decision` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_a_stale_output_record_never_accepts_a_newer_final_artifact` | `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_a_stale_output_record_never_accepts_a_newer_final_artifact` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_verified_integration_binds_the_actual_target_checkout` | `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_verified_integration_binds_the_actual_target_checkout` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_retained_output_without_path_summary_uses_fixed_git_objects` | `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_retained_output_without_path_summary_uses_fixed_git_objects` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_missing_or_spoofed_source_objects_cannot_verify` | `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_missing_or_spoofed_source_objects_cannot_verify` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_final_noop_continuation_still_requires_earlier_goal_change` | `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_final_noop_continuation_still_requires_earlier_goal_change` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_deletion_and_mode_change_are_bound_to_final_tree` | `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_deletion_and_mode_change_are_bound_to_final_tree` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_storage_apply_reuses_workspace_protection_and_current_revision` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_storage_apply_reuses_workspace_protection_and_current_revision` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_storage_native_removal_resumes_after_interrupted_delete` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_storage_native_removal_resumes_after_interrupted_delete` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_cleanup_removes_only_the_registered_worktree_and_is_idempotent` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_cleanup_removes_only_the_registered_worktree_and_is_idempotent` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_reclaim_reports_a_checkout_that_vanished_before_the_delete` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_reclaim_reports_a_checkout_that_vanished_before_the_delete` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_cleanup_remove_is_idempotent_for_an_already_removed_allocation` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_cleanup_remove_is_idempotent_for_an_already_removed_allocation` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_reclaim_does_not_report_a_dangling_symlink_as_removed` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_reclaim_does_not_report_a_dangling_symlink_as_removed` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_cleanup_remove_keeps_a_real_git_failure_on_a_present_checkout` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_cleanup_remove_keeps_a_real_git_failure_on_a_present_checkout` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_reclaim_converges_when_another_owner_finishes_the_removal` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_reclaim_converges_when_another_owner_finishes_the_removal` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_reclaim_converges_when_the_checkout_vanishes_during_the_proof` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_reclaim_converges_when_the_checkout_vanishes_during_the_proof` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_delivered_output_without_integration_blocks_the_reclaim` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_delivered_output_without_integration_blocks_the_reclaim` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_cleanup_is_blocked_by_unsealed_changes` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_cleanup_is_blocked_by_unsealed_changes` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_cleanup_never_targets_a_source_checkout_or_foreign_path` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_cleanup_never_targets_a_source_checkout_or_foreign_path` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_cleanup_keeps_a_foreign_stale_worktree_registry_entry` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_cleanup_keeps_a_foreign_stale_worktree_registry_entry` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_a_new_writer_cannot_claim_the_checkout_across_the_remove_boundary` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_a_new_writer_cannot_claim_the_checkout_across_the_remove_boundary` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_continuation_cleanup_removes_the_original_physical_allocation` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_continuation_cleanup_removes_the_original_physical_allocation` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_continuation_cleanup_blocks_a_change_after_the_latest_seal` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_continuation_cleanup_blocks_a_change_after_the_latest_seal` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_a_borrowed_run_never_authorizes_its_own_cleanup` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_a_borrowed_run_never_authorizes_its_own_cleanup` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_a_helper_that_only_borrowed_the_worktree_cannot_clean_it` | `blackboard.tasks.test_workspace_lifecycle_cleanup.LifecycleTestCase.test_a_helper_that_only_borrowed_the_worktree_cannot_clean_it` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_root_control_targets_an_independently_allocated_helper` | `blackboard.tasks.test_workspace_lifecycle_helpers.LifecycleTestCase.test_root_control_targets_an_independently_allocated_helper` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_reclaim_helper_uses_the_control_roots_revision` | `blackboard.tasks.test_workspace_lifecycle_helpers.LifecycleTestCase.test_reclaim_helper_uses_the_control_roots_revision` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_an_accepted_helper_allocation_cleans_after_the_parent_is_accepted` | `blackboard.tasks.test_workspace_lifecycle_helpers.LifecycleTestCase.test_an_accepted_helper_allocation_cleans_after_the_parent_is_accepted` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_root_takeover_fences_a_stale_helper_target_command` | `blackboard.tasks.test_workspace_lifecycle_helpers.LifecycleTestCase.test_root_takeover_fences_a_stale_helper_target_command` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_a_target_detached_during_git_work_is_refused` | `blackboard.tasks.test_workspace_lifecycle_helpers.LifecycleTestCase.test_a_target_detached_during_git_work_is_refused` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_acceptance_payload_binds_the_recorded_review` | `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_acceptance_payload_binds_the_recorded_review` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_reclaim_after_adopt_counts_the_adopted_paths` | `blackboard.tasks.test_workspace_lifecycle_helpers.LifecycleTestCase.test_reclaim_after_adopt_counts_the_adopted_paths` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_a_forged_adopt_record_authorizes_nothing_for_conclude` | `blackboard.tasks.test_workspace_lifecycle_helpers.LifecycleTestCase.test_a_forged_adopt_record_authorizes_nothing_for_conclude` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_accept_refuses_a_detached_target_before_any_git_work` | `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_accept_refuses_a_detached_target_before_any_git_work` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_accept_refuses_an_unrelated_commit_interval` | `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_accept_refuses_an_unrelated_commit_interval` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_a_takeover_between_review_and_reclaim_blocks_only_the_removal` | `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_a_takeover_between_review_and_reclaim_blocks_only_the_removal` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_root_control_amends_a_helper_scope` | `blackboard.tasks.test_workspace_lifecycle_helpers.LifecycleTestCase.test_root_control_amends_a_helper_scope` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_root_control_resolves_a_helper_scope_conflict` | `blackboard.tasks.test_workspace_lifecycle_helpers.LifecycleTestCase.test_root_control_resolves_a_helper_scope_conflict` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_a_blocked_reclaim_needs_a_fresh_removal_for_the_same_exact_path` | `blackboard.tasks.test_workspace_lifecycle_recovery.LifecycleTestCase.test_a_blocked_reclaim_needs_a_fresh_removal_for_the_same_exact_path` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_started_reclaim_recovers_after_interruption_without_removing_twice` | `blackboard.tasks.test_workspace_lifecycle_recovery.LifecycleTestCase.test_started_reclaim_recovers_after_interruption_without_removing_twice` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_an_attached_branch_blocks_removal_with_the_branch_and_the_detach_retry` | `blackboard.tasks.test_workspace_lifecycle_recovery.LifecycleTestCase.test_an_attached_branch_blocks_removal_with_the_branch_and_the_detach_retry` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_after_the_recorded_detach_the_reclaim_completes` | `blackboard.tasks.test_workspace_lifecycle_recovery.LifecycleTestCase.test_after_the_recorded_detach_the_reclaim_completes` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_apply_reports_the_same_branch_diagnostic_as_the_inspection` | `blackboard.tasks.test_workspace_lifecycle_recovery.LifecycleTestCase.test_apply_reports_the_same_branch_diagnostic_as_the_inspection` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_a_missing_registration_and_a_foreign_lock_keep_their_own_reasons` | `blackboard.tasks.test_workspace_lifecycle_recovery.LifecycleTestCase.test_a_missing_registration_and_a_foreign_lock_keep_their_own_reasons` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_restart_native_seams_continue_seal_integrate_and_reclaim_allocation` | `blackboard.tasks.test_workspace_lifecycle_recovery.LifecycleTestCase.test_restart_native_seams_continue_seal_integrate_and_reclaim_allocation` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_restart_preserves_accepted_target_and_cleanup_plan_before_apply` | `blackboard.tasks.test_workspace_lifecycle_recovery.LifecycleTestCase.test_restart_preserves_accepted_target_and_cleanup_plan_before_apply` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_restart_cannot_remove_a_copied_worktree_git_directory` | `blackboard.tasks.test_workspace_lifecycle_recovery.LifecycleTestCase.test_restart_cannot_remove_a_copied_worktree_git_directory` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_restart_scope_restore_keeps_the_failure_and_continues_helper` | `blackboard.tasks.test_workspace_lifecycle_recovery.LifecycleTestCase.test_restart_scope_restore_keeps_the_failure_and_continues_helper` |
| `blackboard.tasks.test_workspace_lifecycle.LifecycleTestCase.test_restart_abandon_returns_input_and_preserves_the_failed_site` | `blackboard.tasks.test_workspace_lifecycle_recovery.LifecycleTestCase.test_restart_abandon_returns_input_and_preserves_the_failed_site` |
