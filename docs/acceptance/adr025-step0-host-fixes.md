# ADR-025 第零步：Claude Code Host 三项反馈修正

本次基线为暂不验收的 `32760edf00e46dec0f38141e678715ee4c8f338f`，仅处理用户列出的三项缺陷。用户已独立确认原 2,316 条编号加 4 条新增、279 个 Python 文件的路径迁移、嵌套失败上报、旧完整检查与 Console、wheel 命名空间和跨边界导入登记；这些结论不重写。本次没有开始第一步、DSH ACP 客户端或 C-Two 后端，完成后停止等待这三项的复核。

## 三项结果

1. `storage.process_inventory` 从共享 `install/entrypoints.py` 的 `ENTRY_MODULES` 派生两代 daemon/supervisor 的精确模块匹配，kind 来自命中的角色，原状态定位和观察结果结构不变。launcher 延迟导入同一张表，`entry_modules` 继续返回 client/daemon/cli 三键，保留旧布局升级必需的一代数据。新增测试把显式模拟 ps 输出送入真实函数，覆盖四种模块、正确 kind 和六条非精确干扰行；旧实现遗漏新代并误标旧 supervisor，修正后通过。详见 [进程识别记录](adr025-step0-fix-process-inventory.md)。
2. `test_a_resource_must_be_covered_by_a_declared_asset` 只将邻居改为 `src/hey_my_buddy-neighbor/runner.py`，测试编号和断言不变，生产 `_covered` 不改。私有副本施加同一个去斜杠前缀变异，正常通过 → 只有该邻居 subTest 失败（`BoardError not raised`）→ 恢复通过，聚焦模块 32 例通过。详见 [资源覆盖记录](adr025-step0-fix-resource-coverage.md)。
3. 新防护以 `os.walk` 得到 `tests/python` 下所有 `test*.py` 的独立全集，与真实 `python_test_modules` 的结果比对，不依赖 unittest 的包发现规则。私有副本移除 `console/__init__.py` 后，真实防护测试失败并点名 `console.test_console`、`console.test_console_access`、`console.test_console_cli`、`console.test_console_sessions`；恢复后通过，发现/调度聚焦 6 例通过。生产发现语义与实际检出的包入口不改。详见 [发现防护记录](adr025-step0-fix-test-discovery.md)。

## 微任务、固定交付与整合归属

原实现微任务在黑板已接受，服务明确拒绝继续 accepted goal。本次以同一宏任务的三个后续修正微任务、固定 `32760ed` 基线和独立 worktree 并行处理，没有取消原记录；未指定 buddy，由路由选择。每个任务描述写明唯一可写路径、公共整合归 Host、禁止日常数据/凭据改动及精确清理规则。修正中的记录缺陷均拒绝固定交付并以同一 run 的 continue 打回，原微任务自己修订，Host 没有代改它们的代码或记录。

| 微任务 | run | 最终 artifact / output | 实施分支整合提交 | integration |
| --- | --- | --- | --- | --- |
| 进程识别 | `d2a3f356-d27f-4f30-9843-a5c5fba778d3` | `1e2e1e0b-f87f-4b57-8065-c9740d164ade` / `746c063b41da1b3adcb1cf34bad4ea8304989bac` | `986bd2755538061a271db21b43b9ef6699c6cb4f` | `int-431a454a-46ae-4199-a859-84206cbca12e` |
| 资源邻居 | `235b1687-1b31-4b4b-8754-1e7eef20388e` | `43d67f8e-1b28-4343-92be-a55f66253bf5` / `118ce813cf933469c6752a2b337f6a649c14aaaf` | `4bb68df7df0c9520448a92dfe2b721cef8b74a23` | `int-37e12dca-d471-4e5f-a88d-55574fce3c00` |
| 漏测防护 | `52fed5b9-1e1e-4feb-91ce-74f6c7494b0e` | `dfa3076b-36fb-4510-aa32-80048ccedd5b` / `f6f4e574fb8f95cf6c8339e26ed3288be678b754` | `ff3b92838b3fdc16d3edb5a257dec500b1461d98` | `int-8add88e3-22aa-4168-ada7-78e8f5421188` |

三项首次交付均出现“无模型调用”口径问题，漏测记录另有失效链接；进程识别记录还需准确区分覆写、重采日志和清理归属缺口。每项各 continue 一轮，仅修记录，没有新增测试/构建/服务运行或删除。固定输出间的代码差异为空，最终累积 patch 的路径内容在整合后逐项与 seal 相等，integration 验证后接受微任务；这不代替 Claude Code Host 的整步验收。

Host 自行处理的公共文件整合为 `19214b21869d9386ac30fc4095adfc2de56aebe1`：`blackboard/service/daemon.py` 的 supervisor 启动、`protocol/transport.py` 的 daemon 启动、`cli/main.py` 的 supervisor 启动，共三处由硬编码字符串改为共享表引用，并各增加一个常量导入。三处均在原进程识别微任务的唯一可写路径之外，由其交付报告提出；Host 按公共整合归属处理，argv 的实际模块字符串和其他参数不变。程序源码中的两代 daemon/supervisor 模块名现在只在 `install/entrypoints.py` 定义。没有修改角色模块、注册表或新增运行通道。

## 验证与编号变化

整合后完整检查仅运行一次：`uv run --frozen python -m hey_my_buddy.cli.checks`，退出 0，404.97 秒，Python `2322 (skipped 1) in 158 of 158 files`、Node `110`。唯一 skip 仍是 `blackboard.evaluation.test_evaluation.InstalledHarnessDiscoveryTests.test_installed_harness_discovery_is_truthful_and_credential_free`，原因仍为未公布可用模型 profile。检查代码版本为 `19214b2` 的生产/测试树（运行时三处公共整合改动已在工作树，随后原样提交）；此后的变动仅为本记录。

相对 `32760ed` 新增编号恰为 `blackboard.tasks.test_storage.ProcessInventoryServiceTests.test_real_inventory_recognizes_both_generations_by_their_matched_module` 和 `cli.test_checks_parallel.ScheduledModulesTests.test_scheduling_covers_every_test_file_on_disk`；删除、改名为零。资源覆盖用例编号不变，原 4 条 `TargetEntryModuleTests` 保留，模块改名 TSV 字节不变。微任务的真实 loader 前后编号清单和固定测试文件 diff 支持这些变化；完整检查实际总数由 2,320 增至 2,322，测试模块仍为 158。没有重复重列或改写已核对通过的历史全量编号表。

Host 已逐项审阅三个固定代码 diff、原始变异失败与恢复日志、进程识别重采的旧实现失败日志、修复后 52 例聚焦日志和 bootstrap 校验。Console 源码与资产未变，用户已确认的 659 例结果沿用，没有重跑 Console 或额外构建。此次三项验证和 Host 完整检查均未调用原生 harness/模型（不含路由与执行 buddy），没有新增真实冒烟。

## 材料与偏差登记

原始材料在各修正 worktree 的 ignored tmp 中，接受前另存至 `<evidence-worktree>/tmp/adr025-evidence-retained/step0-host-fixes/`：进程识别 13 文件、资源邻居 21 文件、漏测防护 13 文件，逐文件 SHA-256 相等。Host 完整检查的日志、退出码、运行脚本和创建登记保留在 `<implementation-worktree>/tmp/adr025-step0-hostfix-8sd9wjly/`；检查器创建并打印的 `/private/tmp/buddy-checks-t_2p126u` 经既有收尾删除，按确切路径确认不存在。Host 没有删除任何其他对象，自己的验证容器仍保留。

进程识别微任务曾在自己原子新建的任务容器内执行 `rm -rf <task-container>/roots/buddy-checks-*`，违反“不用通配符”规则；创建登记可辨的一个半初始化根与通配实际展开范围的原始凭证缺口均在该记录保留，不能把未知展开数量写成已核实。另有失败日志/基线日志同名覆写后缺失、旧实现失败重采、临时换回源码时命令中断后恢复，以及一个子根个体路径未在创建时独立登记的缺口。Host 拒绝首份记录并通过原 run 的 continue 要求据已有工具记录准确披露，未扩大清理、扫描其他会话或尝试恢复。原 0-B2 的越界登记与撤回不动、不追查；这次披露也不抹去此前登记。

`AGENTS.md`、`docs/README.md` 和生产 `_covered` 均与 `32760ed` 字节相同；目录表和 Layout 留给 Claude Code Host 验收合入时重写。旧布局 `ENTRY_MODULES` 仍保留，未来删除由 Claude Code Host 记入待办。ADR、SKILL、Host 指南和此前验收记录未修改；不安装升级日常运行时，不改用户配置、凭据或日常数据，不读取凭据文件内容。

本次三项修正与记录提交后停止，等待用户复核；第零步尚未获 Claude Code Host 整体验收。
