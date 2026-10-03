# ADR-025 第零步 0-A：旧布局基线与全部测试编号

本记录固定 ADR-025 第零步开工前的旧布局基线：全部测试的真实编号与一次旧布局完整检查。输入 commit 为 `006b2fb9bf9266f3f8e85fd7975d999bbef285b2`（tree `bafaa6c3fd1360a692ae7e00ce98828c6f5cad56`，即合入 `socu/buddy-core` 的 `dd8a9ab` 之后的计划修订提交），在一个独占 worktree 中完成，开始与结束时工作树对 tracked 文件都是干净的；本微任务没有开始任何 `git mv`，没有改源码、测试、打包或既有文档，唯一新增的 tracked 文件就是本记录。

## 搬动表核对

读取 [adr025-step0-file-map.tsv](../design/adr025-step0-file-map.tsv) 并与 `git ls-files` 核对：表 635 行恰好等于 tracked 文件 635 个，old_path 集合与 tracked 集合相等，old_path 与 step0_path 都没有重复。按 action 计数：`git mv` 292（其中源码/打包资产 114，测试侧 178——156 个测试模块加 22 个 fixture）、`keep` 162、`preserve` 128、`path-only` 19、`path-review` 11、`keep-step0` 23（`harnesses/dsh/` 原位，第四步删除）。`tests/python/support.py`、`mock_workspace.py`、`test_locking.py`、`test_private_directories.py` 四个文件按表原位保留，因此 158 个 Python 测试模块中有 156 个要搬动。该表作为后续 0-B 搬动的唯一映射依据，本微任务未改动它。

## 收集方法与环境

Python 用真正的 unittest 加载取编号：`unittest.TestLoader.loadTestsFromName` 逐个加载 `buddy.checks.python_test_modules` 发现的模块，与完整检查排程同一份模块清单、同样一模块一子进程的隔离；脚本为 `<worktree>/tmp/adr025-step0/collect-python-ids.py`。加载不走 AST 方法计数，继承与运行时生成（`import` 期 `setattr` 参数化）的用例都按实际 `TestCase.id()` 记录。DSH Node 按 `buddy.checks.dsh_node_tests` 的同一排序文件清单逐文件执行 `node --test`，同时取内置 TAP（机器可比）与 spec（人工日志）两种报告，再由 `<worktree>/tmp/adr025-step0/parse-dsh-tap.py` 还原层级全名；逐文件执行与检查套件的合批执行同样是一文件一 node 子进程。Console 用一次 `vitest run --reporter=default --reporter=json`，从执行报告取文件与完整测试名（`<worktree>/tmp/adr025-step0/parse-vitest.py` 归约）。

环境按检查套件的口径构造：继承的 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`、`BUDDY_RUNTIME`、`BUDDY_RUNTIME_IDENTITY`、`BUDDY_WORKER_STATE`、`BUDDY_WORKER_ID`、`BUDDY_AGENT_CREDENTIAL`、`BUDDY_AGENT_CREDENTIAL_FILE`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT` 全部清空（本环境本就没有设置），Python 子进程由 `buddy.checks.test_environment` 给出 `BUDDY_DEV_SOURCE=1` 与私有 `BUDDY_STATE_DIR`、空私有 `BUDDY_RUNTIME_ROOT`；一次性脚本、原始清单与子进程载荷都在 ignored 的 `<worktree>/tmp/adr025-step0/`，探测用的短私有临时根 `/tmp/adr025-s0`（收完后已删除）。Node 用 `~/.nvm/versions/node/v24.21.0`（满足 `apps/console` engines `^24.15.0` 与 `.node-version` 24；npm 11.19.0），Python 为 uv 0.10.12 管理的 CPython 3.13.3，机器是 macOS 26.6.2 arm64。`npm --prefix apps/console ci` 一次成功，无 engine 警告。

收集过程中确认了两条既存的环境事实，不是基线缺陷，但重复本记录时必须满足。其一，本环境非交互 shell 里 `node`/`npm` 是懒加载 nvm 的 shell 函数且加载失败，需要直接使用上述固定版本二进制。其二，DSH Node 测试依赖检查套件子进程的两个环境事实：`BUDDY_PYTHON` 必须指向检出 venv 的解释器（`testEnv()` 把 PATH 收窄到 node 与系统目录，yaml 桥接的 `uv run` 兜底会找不到 uv），且 `TMPDIR` 必须够短（DSH 问询桥要绑 Unix 套接字，macOS `sun_path` 上限 104 字节，worktree 相对路径会超限——`src/buddy/checks.py` 的注释记录了同一限制）。缺这两项时逐文件收集曾出现失败，证据保留在 `<worktree>/tmp/adr025-step0/dsh-env-failures/`（长 TMPDIR 且无 `BUDDY_PYTHON` 时 `inquiry-bridge.test.mjs` 18 个用例失败，错误均为套接字路径超限）；满足后 9 个文件全部通过。另一次试跑发现 `--test-reporter=json` 不是 node 内置报告器，node 会把它当模块加载而崩溃（`<worktree>/tmp/adr025-step0/dsh-run-stderr.log`），因此机器可读格式改用内置 TAP。

## 测试编号集合

| 套件 | 文件 | 用例 | 重复 | skip | 列表 SHA-256 |
| --- | --- | --- | --- | --- | --- |
| Python（unittest 加载） | 158 | 2,316 个唯一 `TestCase.id()` | 0 | 加载期 0 | `6b58591bf938ec30c0463036230e57d6fd146ee07ee062ae86af2a0a512a91cb` |
| DSH Node（`node --test` 执行） | 9 | 110 个叶子测试 | 0 | 0 | `90aae667edd078d385bb36a5ec579e933d3414572fc728b27efd291684cf3612` |
| Console（vitest 执行） | 54 | 659 个测试（180 个套件） | 0 | 0 | `3135939355f2c5758490f5784f64e31dd587543f59fec94d295d8c8d5ed4e43e` |

Python 的 2,316 条编号里没有同 id 重复，也没有 `FunctionTestCase` 等非 `TestCase` 叶子，158 个模块全部一次加载成功、无 loader 错误。62 条编号的取得方式需要说明：60 条属于 `test_router_failover_integration.FailoverTests`，是模块导入期用 `setattr` 按参数表生成的（边界、候选变化与停止各一组），AST 方法计数数不到它们；另外 2 条是 `test_workflow_real` 里 `WorktreeContinuationReuseTests` 与 `ContinuationCheckoutReuseTests` 从同模块 `TransferredCheckoutContinuationTests` 继承的真实继承用例。DSH Node 逐文件计数为 inquiry-bridge 18、native-usage 3、no-tool 7、run 31、turn-contract 4、turn-result 16、turn-runner 14、usage 14、yaml 3。unittest 的 `subTest` 只在运行期出现，加载清单不含它们，运行期计数由下面的完整检查口径覆盖。

## 旧布局完整检查

先做完全部收集，再跑一次完整检查：`uv run --frozen python -m buddy.checks`（默认并行度 4），退出码 0，实际用时 411.1 秒。运行器输出 `python tests run: 2316 (skipped 1) in 158 of 158 files` 与 `node tests run: 110`，与加载清单（2,316）和 DSH 逐文件清单（110）一一相等；没有文件未运行，没有失败的文件。唯一的运行期 skip 已定位到 `test_evaluation.InstalledHarnessDiscoveryTests.test_installed_harness_discovery_is_truthful_and_credential_free`，原因串为 `the installed harness did not advertise an available model profile`——净化私有环境读到的已安装 harness 目录没有可用模型画像，属机器目录状态而非代码行为，用聚焦重跑复现确认（加载期 skip 为 0，故编号集合仍与运行计数完全相等）。收尾的私有根回收干净：`/private/tmp/buddy-checks-szotkm37` 整根删除，无存活的锁、无可观察的残留进程、无观察错误，也没有保留 teardown 证据文件。

Console 侧的补充核对：vitest 659 个测试全部通过（`success: true`，无 skip、无失败、无重复），`tsc --noEmit` 退出 0（`<worktree>/tmp/adr025-step0/tsc-typecheck.log` 为空）。`vite build` 本次没有运行——它的输出目录是 tracked 的 `src/buddy/console_assets`，写它超出本微任务的可写范围；构建验证由第零步后续微任务承担。全部收集、探测与检查都没有调用模型，也没有触碰日常黑板、运行时、凭据或用户设置。

## 机器可比材料的位置与对应关系

原始清单、一次性脚本与日志只在 ignored 的 `<worktree>/tmp/adr025-step0/`，与记录的对应关系如下。

| 文件 | 内容 |
| --- | --- |
| `env.sh` | 全程使用的净化环境与固定版本二进制（含上文两条环境事实的处理） |
| `collect-python-ids.py`、`python-children/`、`python-collect-*.log` | Python 逐模块加载脚本、158 份子进程载荷与运行日志 |
| `python-ids.txt`、`python-ids-all.txt`、`python-ids.json`、`python-summary.json` | 排序唯一 id 列表、按出现次数的列表、逐条记录（类、定义处、加载期 skip）与汇总 |
| `collect-dsh-tests.sh`、`dsh-filelist.txt`、`dsh-per-file/`、`dsh-run.log` | DSH 逐文件执行脚本、文件清单、每文件 TAP/spec/stderr/exit 与退出码记录 |
| `parse-dsh-tap.py`、`dsh-tests.txt`、`dsh-tests-all.txt`、`dsh-tests.json`、`dsh-summary.json` | TAP 解析脚本与机器可比列表、逐条记录、汇总（含逐文件计数） |
| `vitest-run.log`、`vitest-report.json`、`parse-vitest.py`、`vitest-tests.txt`、`vitest-tests-all.txt`、`vitest-tests.json`、`vitest-summary.json` | vitest 执行日志、原始 JSON 报告、归约脚本与机器可比列表 |
| `tsc-typecheck.log`、`npm-ci.log` | Console 类型检查输出与 `npm ci` 日志 |
| `checks-full.log` | 完整检查的完整输出（含运行器合计、逐文件完成行、用时与退出码） |
| `reconcile.py`、`reconcile.json` | 加载清单与完整检查计数的核对脚本及结果（无失配） |
| `dsh-env-failures/`、`dsh-run-stderr.log` | 上述环境事实与报告器问题的失败证据 |

## 边界

本记录只覆盖 macOS arm64 上的本机事实；Linux 与 Windows 没有验证，0-C 的私有安装升级与 0-D 的编号对照按执行计划在各自范围处理。全部数字都来自本基线 commit 的实际执行，可作为 0-B 搬动后双射核对的旧侧输入；`dsh-json.ndjson` 是报告器试跑的残留输入，无对照价值。0-B 等待 Host 对本记录的验收，本微任务到此停下。
