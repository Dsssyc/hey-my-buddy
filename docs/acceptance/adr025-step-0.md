# ADR-025 第零步整体验收记录（0-D）

本记录是 ADR-025 第零步（只搬动、目录重构）的整步收尾交付：在固定基线 `dfb8159ac4ad3be6dec56bd9508b6a154f84224c`（输入树 `9646e68e5cc0c49788546c85b20f1d61ce6590c9`）上重列全部测试编号、按 TSV 验证模块改名双射、登记跨边界 import、核对冻结文件表与受保护文件、重跑最终完整检查与构建，并汇总各微任务固定结果；本微任务未改任何源码、测试、既有记录或 ADR，新增 tracked 文件仅本记录与 [adr025-step-0-cross-imports.tsv](adr025-step-0-cross-imports.tsv)，[模块改名表](adr025-step-0-module-map.tsv) 经逐项与再生产物核对后保持原样未动。本记录当前版本是同一 run（`dc5dfbbf-d961-4b13-b98b-8dabf3643ab8`）的更正交付：Host 拒绝了首次固定交付（artifact `0439ced0-bc64-4fb1-ae81-943712f83b8c`，output `7691e348d54a438d626ffbaa7892c765fef6a60e`）并授权 continue，更正仅限记录文字（清理计数与归属、临时文件位置偏离的登记、调用与代码树证明的口径、本微任务登记行），全部核对与检查结果沿用已通过证据，该更正回合没有重跑任何测试、构建、服务启动，也没有删除任何对象；拒绝/continue 登记与最终 artifact 由 Host 随后登记。第零步尚未整体验收，须由 Claude Code Host 经用户转达后验收；第一步与独立 ACP 客户端均未开始。

## 基线、微任务固定结果与整合

宏任务分支 `socu/adr025-run-module` 从 `2bdb497` 建立；上游 ADR 与 Host 验收基线已更新到用户指定 `fcf2947` 的最新原文；证据分支为 `socu/adr025-feasibility`。各微任务的固定交付、审查拒绝与 continue 轨迹均以 [Host 整合登记](adr025-host-integration.md) 为权威汇总：

| 微任务 | run / 固定 artifact | 记录提交 | 整合 | 结果与主要 continue 问题 |
| --- | --- | --- | --- | --- |
| 0-A 基线与编号 | `cdc575a5-9b86-4989-a871-58ea6a33f5a7` / `8e5c3e77-84f4-42e3-b9d4-74690c413e60` | `b22fc63` | `int-70d0b40b` | 已验收；旧布局完整检查 exit 0，Python 2,316（skip 1）、Node 110、Vitest 659、tsc 0；记录字节与 seal 一致 |
| 0-B1 源码搬动 | `37e8b8db-b4c5-40eb-b685-f170ea64f90e` 最终 / `268d5c0a-38a7-402f-98b6-ababe757fa2e`（中断轮 partial `f8e9500f` 仅作未完成材料） | `2fadf95` | `int-b3e4f146` | 已验收；前提失败（包初始化循环、`THEME_STORAGE_KEY`/钥匙串服务名损坏）经用户批准两行修订（`45c39ad`、`4e430e5`）后同一 run continue 修复；固定审查再拒一次并 continue 修复旧目标运行时三处硬编码入口（`ENTRY_MODULES` 定位），新增 4 条 `TargetEntryModuleTests`（编号例外，非旧测试删除），真实旧布局一次性私有复现证明修复 |
| 0-B2 测试搬动 | `633a8b0d-5689-4fcb-aae9-04272c43378f` 最终 / `0bf83c69-8c00-4389-8387-70df3b4c4bb5`（attention `abb0f48b`、审计 `838ea3c5`/`abef96ad` 未验收） | `9dc4b95` | `int-63e89157` | 已验收；越界清理事件按用户裁定保留原登记与撤回、删除对象不恢复不追查；恢复回合按新清理规则仅追加 `harnesses/dsh/tests/native-usage.test.mjs` 入 scope 并修一行 fixture URL；`WORKSPACE_BASE_MISMATCH` 后经 `9bf26e8` 文档延后与 revert 完成恢复 |
| 0-C 私有安装升级 | `73dc5a3a-946b-4c0d-bf88-9638b5264408` / `1828a4e4-e258-4fbe-934d-c34502b88773` | `a093a05` | `int-26ec13fe` | 已接受；真实旧布局安装升级与故障自动回滚链、旧回执逐字节相等、四项合成 command 微任务完成、六个 install 测试模块 90 例 0 skip；原生 harness/model 调用 0 |
| 0-D 编号对照与完整交付 | run `dc5dfbbf-d961-4b13-b98b-8dabf3643ab8`；首次固定 artifact `0439ced0-bc64-4fb1-ae81-943712f83b8c`（output `7691e348…`）被 Host 拒绝并 continue，最终 artifact 由 Host 随后登记 | — | 由 Host 随后登记 | 更正回合只修记录（见导语）；0-D 自身的检查/收集/冒烟原生 harness/model 调用 0 |

Host 自行处理的范围外与整合变更（均登记于整合登记，Host 未代改任何微任务的源码/测试）：计划修订与 635 行文件表提交 `006b2fb`；0-B 拆分为 0-B1/0-B2 提交 `053b5c4`；两行搬动修订建议 `45c39ad` 与批准版 `4e430e5`；0-B2 恢复期的六份公共文档延后 `9bf26e8` 及验收后 revert `1814467`；上游 `fcf2947` 的 ADR-025 原文与文档索引合入；0-C 记录提交 `a093a05` 与整合登记提交 `dfb8159`。

## 测试编号对照（本步重列，方法与 0-A 相同）

Python 用真实 unittest 加载收集（一模块一子进程，脚本与逐模块载荷在 `<repo>/tmp/adr025-step0/0d/`）：当前 158 个模块、2,320 个唯一 `TestCase.id()`、无重复、无 loader 错误、加载期 skip 0；继承 62 条与历次口径一致（60 条 `test_router_failover_integration` 导入期 `setattr` 参数化 + 2 条 `test_workflow_real` 真实继承）。排序唯一清单 `python-ids-0d.txt` SHA-256 `f844219ba9db5ba9ea8c2bc813e66a8e653ba88f298a0c246955c43ae61c8336`，原始全量清单只放 `<repo>/tmp/adr025-step0/0d/`。

双射核对（`<repo>/tmp/adr025-step0/0d/verify-bijection-0d.py`，结果 `bijection-0d.json`）：[模块改名表](adr025-step-0-module-map.tsv) 171 行 = 156 个搬动测试模块（`test-module`）+ 2 个原位测试模块（`test-module-kept-at-root`，显式标记为 `test_locking`、`test_private_directories`）+ 13 个改点分名的 fixture 模块（`fixture-module`），与冻结文件表的模块级投影逐项相等，按表的分组序再生产物与入库文件字节相等（表 SHA-256 `3cae769847d510a3fb7ff5250b8841d3e4f73292b31158feb6cc29fd83dc1d7f`）。0-A 的 2,316 条编号全部命中映射，映射后与当前 2,320 条逐项比较：Counter 差为恰好 4 条已登记例外、反向无缺失；把 4 条例外计入后 **Counter 相等且集合相等（分别验证，均为真）**。4 条例外（0-B1 新增回归，非模块搬动误记）逐条为：`install.test_launcher_selection.TargetEntryModuleTests.test_entry_modules_follow_the_target_layout_not_the_coordinator`、`install.test_launcher_selection.TargetEntryModuleTests.test_derived_modules_resolve_in_an_old_layout_targets_own_namespace`、`install.test_launcher_selection.TargetEntryModuleTests.test_the_coordinator_builds_target_layout_invocations`、`install.test_launcher_selection.TargetEntryModuleTests.test_the_launcher_execs_an_old_targets_own_cli_module`。除此之外无任何新增、删除或改名例外。

Node（DSH，`harnesses/dsh/` 原位）按检查套件同一文件清单逐文件 `node --test`（9 文件全部退出 0，逐文件计数 18/3/7/31/4/16/14/14/3），110 条叶子与 0-A 原始清单 diff 为空、SHA-256 相同（`90aae667edd078d385bb36a5ec579e933d3414572fc728b27efd291684cf3612`）；0 skip、0 not-ok，`native-usage` 的 3 条叶子自 0-B2 一行 fixture 修复后恢复原编号执行。Console 用一次 `vitest run --reporter=default --reporter=json --outputFile=…`：659/659 通过（54 文件、180 套件），0 失败 0 skip，叶子清单与 0-A 原始清单 diff 为空、SHA-256 相同（`3135939355f2c5758490f5784f64e31dd587543f59fec94d295d8c8d5ed4e43e`）；`tsc --noEmit` 退出 0（日志为空）。skip 口径：加载期 skip 0，未把任何 skip 计成 pass；运行期唯一 skip 见下节完整检查。

## 最终完整检查与 Console

`uv run --frozen python -m hey_my_buddy.cli.checks` 在本步重跑一次：退出码 0，运行器输出 `python tests run: 2320 (skipped 1) in 158 of 158 files` 与 `node tests run: 110`；4 个 Python worker 并行，node 套件并行完成（28.1 秒）。唯一运行期 skip 仍是 `blackboard.evaluation.test_evaluation.InstalledHarnessDiscoveryTests.test_installed_harness_discovery_is_truthful_and_credential_free`，原因串 `the installed harness did not advertise an available model profile`（净化私有环境读到的机器目录状态，非代码行为，与 0-A 定位一致）。运行器自建私有根 `/private/tmp/buddy-checks-7c3yr49u` 在收尾自动整根删除，事后复核该路径不存在。日志完整保存在 `<repo>/tmp/adr025-step0/0d/checks-full-0d.log`（含逐文件完成行、用时与 `EXIT=0`）。

Console 代码树自 0-B1 重建资产后零变化：`git diff 2b08c945..HEAD` 限定在 `apps/console` 与生产/打包/测试路径（`src/`、`harnesses/`、`packaging/`、`tests/`、`pyproject.toml`、`uv.lock`、`skills/`、`install.sh`、`install.ps1`）为空；整个 `git diff 2b08c945..HEAD` 并不为空——两提交间的全部差异就是 `docs/acceptance/adr025-step0-upgrade.md` 与 `adr025-host-integration.md` 两份记录文件。本步 `vite build` 不再重跑以避免对 tracked 资产的非必要重建，0-B1 的构建产物证据（重建 bundle 含正确 `hey-my-buddy.console.theme`、无损坏签名，资产已入库 `src/hey_my_buddy/console/assets/`）经上述限定路径的树相等证明对本树继续有效，vitest/tsc 由本步以当前代码树实际重跑通过。

## 跨边界 import 登记（只登记，不修）

[adr025-step-0-cross-imports.tsv](adr025-step-0-cross-imports.tsv) 按当前 AST 的真实 import 语句（含相对导入解析到绝对模块名）登记三类跨边界依赖，共 54 条边、涉及 27 个文件：`protocol->blackboard` 4 条（4 文件）、`protocol->buddy` 1 条（1 文件）、`buddy->blackboard` 16 条（10 文件）、`blackboard->buddy` 33 条（12 文件），列为 `from_file/line/imported_module/category/kind`，行号取当前文件。可静态识别的 `importlib.import_module`/`__import__` 字符串调用为 0（`src/` 下无此类调用，`kind` 列全部为 `import`），动态不可解析项为 0。登记口径：源/目标两侧仅按 `hey_my_buddy` 下 `protocol/`、`buddy/`、`blackboard/` 三个包归类，根包基础模块（`errors`、`home`、`locking`、`private_dirs`）与 `cli/`、`console/`、`install/` 均不归 protocol 侧，不产生类别边；本步只报告事实，未修任何边界，54 条边的方向构成与 0-B1 搬动后登记一致（33/16/4/1）。

## 冻结文件表、受保护文件与打包约束

冻结文件表 [adr025-step0-file-map.tsv](../design/adr025-step0-file-map.tsv) 与当前树逐行核对（`<repo>/tmp/adr025-step0/0d/verify-file-map.py`）：635 行、`old_path` 与 `step0_path` 均无重复；`git-mv` 292 行 = 源码/打包资产 114 + 测试侧 178（156 个测试模块 + 13 个 fixture `.py` + 9 个非 Python fixture），全部旧路径已不在树、新路径 tracked 且在盘；`keep` 162、`keep-step0` 23（`harnesses/dsh/` 原位）、`path-only` 19、`path-review` 11、`preserve` 128，全部原位文件 tracked 且在盘；`tests/python/support.py`、`mock_workspace.py`、`test_locking.py`、`test_private_directories.py` 四个共用/根包测试文件按表原位。

相对旧布局基线 `2bdb497` 的受保护文件状态：128 个 `preserve` 行全部字节相等，唯一例外是 ADR-025 本身——当前内容与用户指定 `fcf294773818c909c4fca6c9c869a1fdb5cbd86c` 的上游原文逐字节相等（用户批准的上游更新，非本步违规，本宏任务未自行改 ADR）。`harnesses/dsh/` 23 个文件全部原位，其中 3 个相对基线有机械修订且仅为路径引用：`plugins/activity.mjs` 与 `scripts/lib/yaml.mjs` 的注释/`-m` 模块串指到新模块名，`tests/native-usage.test.mjs` 除同类注释外仅含 0-B2 经用户授权追加 scope 后的一行 fixture URL 修复。`SKILL.md`、Host 指南、历史设计、四份旧清单与既有 acceptance 记录均在其 `preserve`/原基线字节不变；`AGENTS.md`、两个 README 与 `docs/README.md` 的差异只有路径、命令与 Host 整合的文档索引句。

文件表之外的 tracked 新增路径逐项说明：46 份 0 字节空 `__init__.py`（19 份源码侧由 0-B1、27 份测试侧由 0-B2 按计划补齐）+ 9 份 acceptance 记录/表（三项可行性核对、Host 整合登记、0-A/0-B1/0-B2/0-C 四份微任务记录、本模块改名表）+ 1 份用户批准的 [两行修订提案](../design/adr025-step0-import-cycle.md)；本记录再新增跨边界 TSV 与本记录两个路径。打包约束实测：本步构建的 wheel ZIP namelist 共 310 个成员，顶层只有 `hey_my_buddy` 与 `hey_my_buddy-0.28.0.dist-info`，无顶层 `buddy/`（以 ZIP 清单判定，不用 `import buddy`）；`tests/python/buddy` 只作为检查运行时测试 PYTHONPATH 上的测试包存在，不构成产品命名空间或兼容层；`src/buddy` 在树与磁盘上均为零。

## 故障防护证据（引用既有实际日志，未新增厂商静态证明）

迁移路径/fixture 防护的“应失败 → 恢复通过”证据引用既有实际记录：0-B2 的一次性私有副本注入（改名被搬 fixture `schema-12.sql` 后 `blackboard.store.test_migrations` 基线 exit 0 → 注入态 exit 1（`FileNotFoundError`、7 用例 import error）→ 恢复文件名后 exit 0，日志 `<repo>/tmp/adr025-step0/test-move/fault-injection.log`）；0-C 的安装定位四项注入（删 `src/hey_my_buddy`、同放两代、删声明资源、删 `packaging/runtime-assets.json` 分别得到显式 `RUNTIME_LAYOUT_UNKNOWN`/`RUNTIME_LAYOUT_UNKNOWN`/`missing_resources` + `is_ready=False`/`RUNTIME_MANIFEST_MISSING`，结果表 `<repo>/tmp/adr025-step0/upgrade/fixtures/guard-results.tsv`）；0-B1 的旧目标入口定位先复现 `ModuleNotFoundError` 再修复（`old-target-repro/` 前后日志）。本步未新增注入、未把任何静态扫描升格为厂商证明。

## 构建、私有升级链与最小无模型启动

本步构建（输入树即固定基线，输出在 ignored 的 `<repo>/tmp/adr025-step0/0d/build/`）：`uv build` 退出 0，wheel `hey_my_buddy-0.28.0-py3-none-any.whl` SHA-256 `f16ea3d2dfee1829fa4887c5f50bb220e6991d4a293af7ae4c33b070123e5e0c`（2,108,133 字节），sdist `hey_my_buddy-0.28.0.tar.gz` SHA-256 `feb53ab39b16dcd476cadb60680d7e02884db769b4af01af84477d85f9d543f1`；`packaging/build-skill.py` 退出 0，技能包 171 个文件齐全、启动脚本 3 处均指向 `src/hey_my_buddy/install/launcher.py`，逐文件 SHA-256 见 `<repo>/tmp/adr025-step0/0d/build/verify-artifacts.json`。本次 wheel 与 0-C 链上新 wheel（`dcab9795…`，sourceCommit `2b08c945`）哈希不同属预期：嵌入 build-info 的 sourceCommit 随构建树变化；二者在上述限定生产/打包/测试路径上以 `git diff 2b08c945..HEAD` 为空证明相等（整个 diff 因两份记录文件而不为空）。

实际旧安装升级/回滚链引用 0-C 固定证据（`docs/acceptance/adr025-step0-upgrade.md`、ignored 的 `<repo>/tmp/adr025-step0/upgrade/`）：旧 wheel `9029c29a…` 确来自 `2bdb497`、新 wheel 确来自 `2b08c945`、故障 wheel `cb9380e9…` 仅含一行私有 daemon 启动注入；chain1 真实升级成功、chain2 真实启动失败后自动回滚到旧布局，两链保留的旧回执逐字节相等，升级后与回滚后合成 command 微任务均实际完成，链条 daemon/supervisor PID 与私有端口在两次 `stop` 后均退出。0-C 记录的未验证项照抄为准：真实链未触发 schema 14→15 `migrate_board`、journal `recover()` 路径、“指针存在而服务未运行”安装分支与三代以上 runtime 裁剪未在真实链走过，日常（非私有）安装升级不在授权内，Windows 平台行为未验证。

最小无模型启动：在私有 `BUDDY_STATE_DIR`/`BUDDY_RUNTIME_ROOT` 与 `BUDDY_DEV_SOURCE=1` 下经新入口 `python -m hey_my_buddy.cli.main` 冷启动——首次尝试未设私有控制台端口，被日常安装占用的默认端口以 `CONSOLE_PORT_IN_USE` 显式拒绝（0-C 已登记的环境事实，日常进程全程未触碰），改用私有端口 50337 后 `health` 退出 0（冷启动 serviceId `58ba2592-3991-4384-acae-75e67cd596db`、PID 58924、schemaVersion 15、contract 0.28.0、runtimeIdentity `source:0a27a720f227`），`capabilities` 退出 0，`stop` 退出 0 且 `unresolvedAttempts` 为空；事后按 PID 与私有 state 路径复核无存活进程。完整日志 `<repo>/tmp/adr025-step0/0d/startup-0d.log`。

无模型运行计数：0 这个断言只适用于 0-D 自身的检查、编号收集与最小冒烟——本 run 的完整检查套件、Console 检查、构建与最小启动均无原生 harness/模型调用、无 command 微任务；它不概括其他核对线（已验收的 F-D1/F-D2 含真实 prompt 共 5 次，属各自固定记录的口径，本步未重跑也未扩大）；黑板对本微任务的路由与执行 buddy 自身的模型运行是另一口径，不计入原生冒烟。

## 私有根收尾与删除登记

创建与归属依据：本执行在开始任何核对前创建任务专用系统临时根（`mkdir -p /tmp/adr025-0d-204844/tmp`）并当场把路径写入 `<repo>/tmp/adr025-step0/0d/task-root.txt`（原始材料），此后全部系统临时材料只放该根内；该根内对象（含完整检查子进程留下的残留）的归属据此成立——来自这个先创建并记录的专用容器，不以时间戳、内容抽查或无进程引用作为归属依据。完整检查运行器私有根 `/private/tmp/buddy-checks-7c3yr49u` 由运行器创建时打印、本执行当场登记进同一 `task-root.txt`（原始材料），由运行器既有收尾自动整根删除，事后按打印的确切路径复核不存在。

删除序列与结果（除注明原始材料外均为会话回写事实，已按回写整理追加到 `<repo>/tmp/adr025-step0/0d/deletion-transcript.md` 并明确标注为回写，不充当原始凭证）：第一次收尾对任务根的 `rmdir /tmp/adr025-0d-204844/tmp` **失败**（`Directory not empty`，失败退出码 1 当场记录），同一批次中 `<repo>/tmp/adr025-step0/0d/private-root-0d/`（编号收集的私有 state/runtime/tmp）、`<repo>/tmp/adr025-step0/0d/startup/`（最小启动的私有 state/runtime）、`apps/tmp_vitest_0d.json`、`apps/console/.vitest/` 四处按确切路径删除成功；第二次先按确切路径删除内层锁文件 `/tmp/adr025-0d-204844/tmp/uv-3c03a1d4fcbd6bd9.lock`（成功，与下述清单区分：锁不在清单内），随后的 `rmdir` **再次失败**（同样 `Directory not empty`）；第三次先把删除前的完整 `ls -la` 枚举原样落盘为 `<repo>/tmp/adr025-step0/0d/task-root-residue-manifest.txt`（原始材料，**45 项**：42 个 `deepseek-delegate-logs-*`、1 个 `deepseek-delegate-settings-*`、2 个客户端临时目录 `_UneY-LsXvL5wXE0OEGpO` 与 `v-3QoIvaLvBUtQkBBMuye`），再对清单内每个名字逐项执行删除（循环逐项取名，无通配符，逐项无报错为回写事实），随后两次 `rmdir` 成功、按确切路径复核不存在。两次 `rmdir` 失败按原样保留于本登记与回写整理，不写作"删除命令全部成功无报错"。

位置规则偏离登记：本执行首次 Console 运行把 JSON 输出重定向写到 `apps/tmp_vitest_0d.json`、vitest 运行在 `apps/console/.vitest/` 建立未跟踪缓存——两处均为本执行创建的一次性材料，落在"一切一次性材料放 ignored `tmp/`"规则之外，属位置规则偏离；上段第一批删除已按确切路径删去这两处本执行对象，`apps/` 下其他文件未触碰，此后未再产生新的偏离。保留为原始材料与回写整理（ignored 的 `<repo>/tmp/adr025-step0/0d/`）：收集脚本与逐模块载荷、三套编号清单与 SHA、双射核对脚本与结果、跨边界扫描脚本与 TSV 正文、完整检查/构建/启动日志、残留枚举清单、删除回写整理、构建制品与逐文件哈希。0-B1/0-B2/0-C 在 `tmp/` 下的既有留存材料与 0-C 两条私有链按原登记保留、未扫描未触碰；本次执行未发现其他会话遗留对象（未做扫描），也未清理任何同名前缀的兄弟目录。

## 边界

本记录全部数字来自固定基线 `dfb8159` 上的本机实际执行（macOS arm64，Node `~/.nvm/versions/node/v24.21.0`，CPython 3.13.3）；Linux 与 Windows 未验证。第零步到此收尾，等待 Claude Code Host 经用户转达验收；第一步、DSH ACP 客户端与 C-Two 后端均未开始。
