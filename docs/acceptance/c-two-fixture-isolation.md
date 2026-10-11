# 微任务 2-B：catalog 夹具隔离（部分验证）

基线固定为 `b2741294296045968f5ff7d92683a8d75802600c`；工作区 HEAD 未变，未提交、未操作 stash/分支/标签。只修改下列五个授权路径，未改产品、角色、Worker 接线、公开契约、schema 或 0755 权限拒绝逻辑。本记录不表示完整验收，真实 console/daemon 与原生 socket 验证等待 Host。

## 路径与实现

| 路径 | 改动 |
| --- | --- |
| `tests/python/support.py` | catalog_fixture 保存所有者路径；child_environment 显式传递，daemon/CLI 共用；继续清除所有继承 BUDDY_/ANTHROPIC_/C2_ 及 VIRTUAL_ENV/UV_PROJECT_ENVIRONMENT，复用第三方网关变量清单；私有 HOME、XDG、SDK/供应方配置路径并验证归属。 |
| `tests/python/blackboard/service/fixtures/daemon_with_catalog.py` | 保留 file catalog 分支，为原 _guard 绑定本 fixture 的 synthetic health；避免 capabilities 未绑定时回退到真实发现，未新建服务机制。 |
| `tests/python/cli/test_support_cleanup.py` | 原测试增加继承注入及显式夹具；新增真实解释器/SQLite/IPC/catalog 探针、所有者传递保护、无 socket 的 daemon fixture 能力与文件重读保护。 |
| `tests/python/console/test_console.py` | 新增 R-05：真实私有 daemon/CLI/HTTP，记录拒绝模拟 CLI，拦截 candidate_snapshot 以阻止系统 HOME/安装发现；修改文件内容后再次读取，检查私有 state、空 HOME。 |
| `docs/acceptance/c-two-fixture-isolation.md` | 本记录。 |

## 环境与依赖

短任务根为 `/tmp/b2b-iptu2A`（系统解析为 `/private/tmp/b2b-iptu2A`）。TMPDIR 与 BUDDY_CHECKS_TMPDIR、uv 环境/cache、wheel、比较与变异副本均在此根；没有手工删除。测试框架按原规则自行收尾私有测试目录。安装只使用该根的 uv 环境，未升级日常 runtime。

复用 `checks.child_environment` 的逐解释器私有 state/runtime/tmp 规则，不调用完整检查运行器。执行包装器额外固定私有 HOME/XDG/SDK，并在任何原生 candidate_snapshot 之前记录拒绝；测试 support 为每个 daemon/CLI 再固定其自身的 HOME 与 SDK 路径。原生发现使用系统 passwd HOME 的产品行为未改。

材料来自 Host 指定公开 wheelhouse；18 个文件逐项核对 provenance SHA256。`uv pip install --no-index --find-links <task-root>/wheelhouse --require-hashes -r <task-root>/locked-dependencies.txt` 安装发布版 C-Two `0.7.4`，其 wheel SHA256 为 `bb625650f186c6066992cac0948c0f44b95a22af46778c8e63bba30d80ee88b8`。核对证据为根内 `verified.json`、`setup.log`。未用 vendor 静态证明，没有真实模型、账户发现或凭据读取。

## 编号与原断言

基线 console 19 项，交付 20 项；support cleanup 5 项，交付 8 项。两文件编号删除差集均为空；原方法的 assert 调用 AST 均保留。详细差集在 `validation-record.json`。新增编号：

- `tests/python/console/test_console.py::ConsoleDaemonTests.test_r05_private_daemon_reads_owned_catalog_without_native_discovery`
- `tests/python/cli/test_support_cleanup.py::FixtureCleanupTests.test_r05_catalog_daemon_fixture_binds_capabilities_without_native_discovery`
- `tests/python/cli/test_support_cleanup.py::FixtureCleanupTests.test_r06_catalog_owner_passes_fixture_after_inherited_pins_are_removed`
- `tests/python/cli/test_support_cleanup.py::FixtureCleanupTests.test_r06_child_uses_private_state_and_explicit_catalog`

## 实际运行

以下命令均为 `<task-root>/env/bin/python -m unittest -v <目标>`，一文件一解释器；完整参数、命令、项数、退出码、墙钟耗时以及日志 SHA256 在根内 `validation-record.json` 与各同名 `.json/.log`。未跑完整检查或前端套件。

| 记录/日志 stem | 目标 | 项数 | 退出码 | 耗时（秒） |
| --- | --- | ---: | ---: | ---: |
| `baseline-cleanup` | `cli.test_support_cleanup` | 5 | 1 | 2.435 |
| `baseline-console` | `console.test_console` | 19 | 1 | 5.946 |
| `baseline-minimal-catalog-cli` | `cli.test_cli.ModelProfilesCliTests` | 1 | 1 | 1.917 |
| `blocked-r05-catalog-loss` | `console.test_console.ConsoleDaemonTests.test_r05_private_daemon_reads_owned_catalog_without_native_discovery` | 1 | 1 | 2.116 |
| `bounded-cleanup` | `同文件六项隔离保护（完整参数见同名 JSON）` | 6 | 1 | 2.611 |
| `delivered-cleanup` | `cli.test_support_cleanup` | 8 | 1 | 8.77 |
| `delivered-console` | `console.test_console` | 20 | 1 | 10.6 |
| `final-cleanup` | `cli.test_support_cleanup` | 7 | 1 | 3.902 |
| `final-console` | `console.test_console` | 20 | 1 | 7.311 |
| `final-owner` | `cli.test_support_cleanup.FixtureCleanupTests.test_r06_catalog_owner_passes_fixture_after_inherited_pins_are_removed` | 1 | 0 | 1.802 |
| `minimal-catalog-cli` | `cli.test_cli.ModelProfilesCliTests` | 1 | 1 | 1.864 |
| `passed-isolation` | `同文件六项隔离保护（完整参数见同名 JSON）` | 6 | 0 | 1.728 |
| `verified-cleanup` | `cli.test_support_cleanup` | 8 | 1 | 6.584 |

最终 `delivered-cleanup` 8 项中 6 项通过、2 项 daemon 生命周期测试因 loopback 绑定拒绝失败；独立 `passed-isolation` 六项全部通过，包含实际 SQLite 文件头、私有 IPC 根、显式 catalog 文件读取、默认 state 查找拒绝、所有者给 daemon/CLI 的环境及 fixture capabilities/file 重读。最终 `delivered-console` 20 项为 3 个 daemon 失败、17 个 HTTP 绑定错误，均无法越过 socket 权限边界，不能宣称 R-05 已通过。

基线原始失败已保存于 `baseline-console.log`（19 项，2 failures/17 errors）及 `baseline-cleanup.log`（5 项，2 failures）。代表原始错误是 `CONSOLE_PORT_IN_USE: Cannot bind 127.0.0.1:0`；发布版原生核对另得到 `c_two._native.CoreError: server failed to start: IO error: Operation not permitted (os error 1)`。`native-probe.json/log` 记录退出码 1、0.611 秒，且 `shutdown.completed=true`，`server_was_started=false`。这是沙箱权限限制，不是目标变异成功。

最小影响回归 `cli.test_cli.ModelProfilesCliTests` 的唯一用例失败为 RPC 桩 `lambda() got an unexpected keyword argument state_dir`，在固定基线副本 `baseline-minimal-catalog-cli.log` 同样复现。本批未改该七文件 2-C 桩适配，交 Host 整合。`checks.py` 已有逐解释器私有根，但未覆盖 HOME/SDK；本批只在授权 support 中补足，不修改产品检查运行器。

## 单点变异

每个变异只替换一个行为，修改发生在任务根的私有副本，未修改交付工作区；每项独立解释器、1 项测试、退出码 1。以下 20 项均得到目标 AssertionError、没有导入/环境初始化/权限错误；命令目标与原始替换文本在 `mutations.json`、`additional-mutations.json`、`validation-record.json`，日志为 `<stem>.log`。

| stem | 去掉/替换的保护 | 目标断言（节选） | 秒 |
| --- | --- | --- | ---: |
| `mutation-anthropic-prefix` | 继承 ANTHROPIC_ 清除 | `AssertionError: 'ANTHROPIC_MODEL'` | 1.134 |
| `mutation-c2-prefix` | 继承 C2_ 清除 | `AssertionError: 'C2_UNKNOWN_PIN'` | 1.084 |
| `mutation-virtual-env` | VIRTUAL_ENV 清除 | `AssertionError: 'VIRTUAL_ENV'` | 1.141 |
| `mutation-uv-project` | UV_PROJECT_ENVIRONMENT 清除 | `AssertionError: 'UV_PROJECT_ENVIRONMENT'` | 1.47 |
| `mutation-gateway` | 第三方网关变量清除 | `AssertionError: 'CLAUDE_CODE_USE_FOUNDRY'` | 1.375 |
| `mutation-actual-state` | 实际 state 固定（改指另一私有目录） | `AssertionError: '/pri[18 chars]A/mutation-actual-state/tmp/buddy-test-2gm3nqyg/unwanted-state' != '/pri[18 chars]A/mutation-actual-state/tmp/buddy-test-2gm3nqyg'` | 2.149 |
| `mutation-default-state` | state 缺失导致默认查找 | `AssertionError: default state lookup (子进程目标拒绝；父进程断言退出码失败)` | 1.682 |
| `mutation-runtime` | 实际 runtime 固定 | `AssertionError: '/pri[15 chars]tu2A/mutation-runtime/tmp/buddy-test-9hmknhko/unwanted-runtime' != '/pri[15 chars]tu2A/mutation-runtime/tmp/buddy-test-9hmknhko/runtime-root'` | 2.43 |
| `mutation-home` | HOME 固定 | `AssertionError: '/private/tmp/b2b-iptu2A/mutation-home/tmp/buddy-test-3rhq6u80/unwanted-home' != '/private/tmp/b2b-iptu2A/mutation-home/tmp/buddy-test-3rhq6u80/home'` | 2.455 |
| `mutation-sdk-codex` | Codex SDK 固定 | `AssertionError: '/pri[20 chars]mutation-sdk-codex/tmp/buddy-test-jud_t8qo/home/unwanted-codex' != '/pri[20 chars]mutation-sdk-codex/tmp/buddy-test-jud_t8qo/home/.codex'` | 2.22 |
| `mutation-sdk-claude` | Claude SDK 固定 | `AssertionError: '/pri[22 chars]tation-sdk-claude/tmp/buddy-test-1qh4wa50/home/unwanted-claude' != '/pri[22 chars]tation-sdk-claude/tmp/buddy-test-1qh4wa50/home/.claude'` | 2.612 |
| `mutation-sdk-dsh` | DSH_HOME 固定 | `AssertionError: '/private/tmp/b2b-iptu2A/mutation-sdk-dsh/tmp/buddy-test-4oywst07/inherited-dsh' == '/private/tmp/b2b-iptu2A/mutation-sdk-dsh/tmp/buddy-test-4oywst07/inherited-dsh'` | 2.358 |
| `mutation-zcode-builtin` | ZCode builtin provider 配置固定 | `AssertionError: '/private/tmp/b2b-iptu2A/mutation-zcode-builtin/tmp/buddy-test-hbta0wdp/inherited-builtin.json' == '/private/tmp/b2b-iptu2A/mutation-zcode-builtin/tmp/buddy-test-hbta0wdp/inherited-builtin.json'` | 2.249 |
| `mutation-zcode-personal` | ZCode personal provider 配置固定 | `AssertionError: '/private/tmp/b2b-iptu2A/mutation-zcode-personal/tmp/buddy-test-8ghvpqr5/inherited-personal.json' == '/private/tmp/b2b-iptu2A/mutation-zcode-personal/tmp/buddy-test-8ghvpqr5/inherited-personal.j` | 2.37 |
| `mutation-private-runtime-validation` | 越界 runtime 拒绝 | `AssertionError: ValueError not raised` | 1.484 |
| `mutation-fixture-binding` | fixture capabilities 的 synthetic health 绑定 | `AssertionError: True is not false : fixture capabilities attempted native discovery` | 1.579 |
| `mutation-owner-catalog-v2` | 所有者显式 catalog | `AssertionError: 'BUDDY_MODEL_CATALOG_FILE' missing (explicit fixture assertion)` | 1.53 |
| `mutation-cli-forwarding` | CLI 使用所有者环境 | `AssertionError: 'BUDDY_MODEL_CATALOG_FILE' missing (explicit fixture assertion)` | 1.561 |
| `mutation-daemon-forwarding` | daemon 使用所有者环境 | `AssertionError: 'BUDDY_MODEL_CATALOG_FILE' missing (explicit fixture assertion)` | 1.311 |
| `mutation-buddy-prefix-v2` | 未知继承 BUDDY_ 清除（真实子解释器） | `AssertionError: 'BUDDY_UNKNOWN_PIN'` | 1.53 |

早期 `mutation-buddy-prefix` 先因继承 catalog 越界触发路径 ValueError，不计入成功；改用实际子解释器的私有继承路径后，`mutation-buddy-prefix-v2` 命中继承未知 BUDDY_ 键目标断言。比较副本 `mutation-comparison` 的原保护测试先通过（1 项，退出码 0，1.754 秒）。早期 `mutation-owner-catalog` 只得到 KeyError，不计入成功；随后补充明确 assertIn，`mutation-owner-catalog-v2` 命中“owner lost its explicit catalog”目标断言。无 socket fixture 用例曾因比较未规范化 providers 全对象而失败，已改为核对文件中的模型身份并修改文件后再次读取；原始 `bounded-cleanup.log` 保留，最终六项已通过。

真实 console 的 fixture-loss 副本 `r05-catalog-loss-copy` 已准备：仅将 daemon 的 self.child_environment 调回 _child_environment。`blocked-r05-catalog-loss` 尝试为 1 项、退出码 1、2.116 秒，仍停在 daemon socket 绑定，未命中诱饵/目标断言，因此不计入变异成功，必须由 Host 补核。

## Host 补核与保留根

Host 需要在允许私有 TCP/Unix socket 的环境运行最终 console 20 项、support cleanup 8 项、C-Two 0.7.4 私有原生注册和真实 R-05 fixture-loss 副本。所有调用继续清除继承运行时/账户变量，使用该任务根下的新私有目录及记录拒绝诱饵；不能运行真实模型/原生账户 CLI。原保护应通过，fixture-loss 应在记录拒绝诱饵或文件源/内容目标断言处失败，并确认空私有 HOME。不得把 socket、导入或环境错误作为成功。

可复用根内 `run.py`，使用新 label `host-console`、`host-cleanup` 避免覆盖已有目录；fixture-loss 以 `B2B_CHECKOUT=<task-root>/r05-catalog-loss-copy` 调用同一 runner、目标 `console.test_console.ConsoleDaemonTests.test_r05_private_daemon_reads_owned_catalog_without_native_discovery`。`native_probe.py` 的旧目录已保留，Host 复核需使用新私有 probe 根并保存实际 shutdown 证据。补核结果写回本固定验收路径，由 Host 决定整合 2-C 与检查运行器缺口。

所有自建 Popen/测试解释器已等待退出；daemon 在 pool 启动之前退出，没有自建 Worker 启动证据；原生探针报告 shutdown.completed=true。根内材料、环境、日志、比较和变异副本全部保留待 Host 按确切根回收。进程列表观察 `ps` 实际被沙箱以 PermissionError 拒绝；无已知未等待自建句柄，但不能独立确认进程表，需 Host 复核任务根关联进程。记录为根内 `process-receipt.json`。
