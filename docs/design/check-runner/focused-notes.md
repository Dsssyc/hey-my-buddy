# 检查程序定向运行与清扫

全量用法是 `python -m hey_my_buddy.cli.checks [--jobs N]`；定向用法是 `python -m hey_my_buddy.cli.checks [--jobs N] 路径...`；清扫用法是 `python -m hey_my_buddy.cli.checks --sweep`。路径相对仓库根目录，或使用绝对路径，只接受 `tests/python` 下的 `test_*.py` 文件及包含可发现测试文件的目录。目录按已有包发现规则递归选择，文件与目录可以混用，重复文件只运行一次。模块名、越界路径、不存在的路径、无可发现测试文件的路径，以及清扫与路径或 `--jobs` 混用，均在建立私有目录前以用法错误退出。没有新增其他开关；`BUDDY_CHECKS_JOBS` 的优先级仍低于显式 `--jobs`。

退出码 0 表示测试通过且私有根目录已经删除；1 表示测试失败，无论清场结果如何；2 表示用法错误；3 表示测试通过但清场不完整或不可观察，私有根目录和证据保留。最后一行报告测试与清场结果、退出码，并在保留时给出目录。清扫没有保留目录时返回 0，有保留目录时返回 3；跳过仍在运行的创建者不算保留。

文件选择集中在 `selected_test_modules`，使用原来的 `python_test_modules` 包发现结果；不给路径时返回同一完整文件集合。删除原来整个目录一次 discovery 的串行执行函数。现在所有运行都经过 `run_suites_parallel`，`--jobs 1` 使用一个工作线程，逐个文件建立独立子进程；每个文件仍有各自的状态、runtime、临时目录与 harness home，整次运行共用一个私有根目录和一次清场。并行调度顺序、每文件输出、总数统计、失败汇总与中断处理共用原来的实现。

`create_private_root` 在根目录写 `creator.pid`，内容是创建进程的 PID。`main` 与清扫共用 `private_root_directory`：优先使用 `BUDDY_CHECKS_TMPDIR`，macOS 默认 `/tmp`，其他平台使用 `tempfile.gettempdir()`。清扫只检查该位置直接包含的、属于当前用户的 `buddy-checks-*` 实体目录，忽略符号链接、其他所有者和其他前缀。只读 `ps` 进程表中仍有创建 PID 时跳过；没有创建记录时视为创建者已离开；PID 不在表中时调用原来的 `teardown_private_root`。PID 被复用时保守跳过。创建记录不可读或进程表不可观察时保留目录并写证据；缺少创建记录的目录仍须通过原有清场观察才能删除。清场判定与合作停止行为未改变，没有新增信号或杀进程的路径。清场抛出异常时也写下异常证据。每个候选目录在标准输出打印删除、跳过或保留及原因，最后报告删除、跳过、保留三个计数；原有清场诊断继续输出到标准错误。

验证使用 Host 指定的现成只读解释器，仓库根目录下设置 `PYTHONPATH=src:tests/python` 和 `PYTHONDONTWRITEBYTECODE=1`，清掉 `AGENTS.md` 所列十个继承变量；三个既有测试文件另清掉 `BUDDY_CHECKS_TMPDIR`，并把 `TMPDIR` 放在本次建立的 `/tmp/hmb-checks-worker-<随机值>` 中。每个测试文件单独运行一个 `python -m unittest -v <模块>` 进程，没有执行完整检查、安装测试或启动器测试，也没有写解释器环境。

通过的文件为 `cli.test_checks_focused`（14 项）、`cli.test_checks_parallel`（14 项）和 `cli.test_harness_test_safety`（3 项）。新增测试通过真实 unittest 子进程验证单文件、目录、混合选择与重复参数在 jobs 1 和 2 下只运行指定文件，检查共享根目录及每文件隔离，覆盖无路径集合、用法错误不创建目录、0 / 1 / 3 退出码、创建记录、清场异常证据及清扫行为。清扫和新测试的清场分支使用受控进程观察结果，不能据此声称沙盒中的真实进程枚举可用。既有并行测试包含本检出 discovery 与逐文件加载测试 ID 完全一致的验证，以及真实子进程的输出残留和中断处理验证。

`cli.test_checks_cleanup` 共运行 15 项，11 项通过，下列 4 项由于沙盒不允许 `ps -axo pid= -o args=` 列出进程而未能通过，须由 Host 在允许进程观察的环境复跑；没有修改这些测试、重试被拒绝的操作或绕过沙盒。失败均有 `ps process enumeration failed; residue status is unknown` 证据：两个删除断言得到保留结果，一个合作停止断言在不可观察时提前得到保留结果，一个残留进程断言因不能枚举而拿不到 PID。

- `cli.test_checks_cleanup.PrivateRootTeardownTests.test_teardown_removes_a_clean_private_root`
- `cli.test_checks_cleanup.PrivateRootTeardownTests.test_teardown_drains_a_cooperative_supervisor`
- `cli.test_checks_cleanup.PrivateRootTeardownTests.test_teardown_waits_out_a_live_process_with_unlinked_lock`
- `cli.test_checks_cleanup.PrivateRootTeardownTests.test_teardown_reports_a_surviving_process_with_unlinked_lock`

上述验证共 46 项，42 项通过，4 项受进程观察权限限制。测试自身已删除其他临时目录；仍有 `/tmp/hmb-checks-worker-I3IfWq9w/buddy-checks-36a7p7s8`，来自 `test_teardown_removes_a_clean_private_root` 的纯目录 fixture，其证据为没有 held lock、没有观测到的进程、两条 `ps` 观察失败。删除本次外层临时目录 `/tmp/hmb-checks-worker-I3IfWq9w` 的 `rm -rf` 操作被自动审批拒绝，理由为禁止 `rm -f` 类命令；未换工具或路径重试，需要 Host 清理该外层目录。`git diff --check` 通过。完整检查与最终验收由 Host 执行。
