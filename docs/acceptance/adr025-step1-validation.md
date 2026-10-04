# ADR-025 第一步 1-D：外层迁移与防护验证（微任务交付记录）

2026-10-04，微任务 1-D 的执行记录，交付等待本 Host 审查。固定输入 `a5c3c21587e668d72678a3b24dcab0e1a94de38d`（1-A、1-B、1-C 已由本 Host 内部验收并整合，独立 ACP 客户端线另线整合），本 run `cc0e12cf-834b-4ec7-bca4-203bd9b4c857`、attempt `579219ab-73d9-41ac-81ad-7265be876b30`，在 Host 分配的独立 worktree 按普通路由完成，未指定 buddy/配置，未再委派。第一步整步的 Claude Code Host 外部验收尚未开始，本文不宣告第一步完成，也不进入任何 harness 抽取（第二步及以后）。本微任务自身的执行（zcode / GLM-5.3 经受控回合）计 1 次，与下文检查计数分开。

## 交付物与写范围

改动恰好落在授权写范围内，共三个路径：新增本文与[第一步测试变化表](adr025-step1-test-map.tsv)，修改 `tests/python/buddy/harnesses/test_controller.py`（只新增 `ObservationErrorStopTests` 与 `ReplacedEvidenceTests` 两个防护测试类，未改任何既有测试）。生产源码、公共值/role/registry/runtime、ADR、SKILL、Host 指南、README/AGENTS/CONTEXT 与既有历史记录全部只读；跟踪的变更恰为上述三个路径；检查另创建了被 git 忽略的 `<worktree>/.venv/`，它不在普通 `git status` 的输出中，`src/hey_my_buddy/buddy/harnesses/base.py`（SHA-256 `e0e7609403eea51c…`）与 `buddy/harnesses/controller.py`（`8460e0f4e9e6354…`）与本 run 固定输入逐字节相同。发现源代码缺陷时按约定在交付中报告（本次未发现，见"未验证项"），未代改任何来源产物。

## 基线原始清单的披露与重建

任务描述中的 `<repo>` 指 Host 实施检出；本微任务的隔离检出及已检索范围不可见首次 baseline 原始清单 `<host-repo>/tmp/adr025-host/step1-baseline-ids-6jjv5tze/ids.json`。本微任务因此以权威锚点 `4cf58dee3553195585b6224be3baca47401fb08e` 在任务根通过 `git archive` 建只读副本，用与当前树相同的真实加载器动态重建基线：158 个模块、2,322 个唯一编号、重复 0、导入错误 0。Host 已独立确认原始清单在实施检出存在，且重建的 2,322 个编号与原始集合完全相等。这里更正原交付把隔离检出不可见扩大为“本机不存在”的表述；重建的操作、计数与验证结论不变。

## 编号集合与变化表

编号由真正加载的 unittest 收集：`hey_my_buddy.cli.checks.python_test_modules` 枚举模块（仅按包目录与 `test*.py`，与检查器同一发现规则），`importlib` 导入后 `loadTestsFromModule` 展开，继承与动态用例按实际加载计入，不用 AST 数量；收集环境按检查器净化规则（清除继承 runtime/Worker/凭据变量与 `VIRTUAL_ENV`/`UV_PROJECT_ENVIRONMENT`，`BUDDY_DEV_SOURCE=1`，Claude/Codex CLI 用不存在的哨兵，`BUDDY_CONSOLE_PORT=0`）。三次收集结果：基线 `4cf58de` 为 158 模块 / 2,322 编号；本 run 固定输入（不含本微任务测试改动）为 166 模块 / 2,530 编号，与任务描述的预期 2530/166 相符——预期只作预期，本节为实际收集与集合比较；补入本微任务两个防护测试后最终为 166 模块 / 2,532 编号。三次收集均为重复 0、导入错误 0、加载期跳过 0（记录在案的那 1 项 skip 属运行期跳过，不在加载编号里）。原始清单与差异脚本在任务根（`m/ids-baseline-4cf58de.json`、`m/ids-current-a5c3c21.json`、`m/ids-final-1d.json`、`m/ids-added.txt`、`m/ids-removed.txt`、`m/compare_ids.py`）。

集合比较的结论：相对基线最终新增 210、删除 0、改名 0；基线 2,322 个编号全部出现在最终集合中，未变部分以集合相等证明，不逐行写表。新增构成为 1-A 两模块 62、1-B `test_controller` 35、1-C 两模块 40、独立 ACP 线三模块 71、本微任务 2。变化表 [adr025-step1-test-map.tsv](adr025-step1-test-map.tsv) 只列最终新增：139 行逐条登记 1-A/1-B/1-C/1-D 的每个新编号（含所属微任务、防护族与故障检查出处），ACP 线的 71 项不逐条复制——其原新增表（69 行）与整改变化表（6 行）已有拥有者，只以三行模块归属行链接 [adr025-dsh-acp-client-test-map.tsv](adr025-dsh-acp-client-test-map.tsv) 与[整改记录](adr025-dsh-acp-host-fixes.md)（69→71：新增 3、删除 1、改名 2，launch 20 / connection 39 / session 12 与该记录一致），不混作第 1 步 harness 抽取。一处如实登记：1-A 记录的 continue 运行拆分为 34+28，整合产物实际加载为 `test_run_contract` 33 + `test_live_channel` 29（合计同为 62）；整合提交 `da8c25d` 之后这两个模块再无任何改动（该范围内的 `git log` 为空），变化表按实际加载登记。

Node 与 Console 编号本步源码未改，以源字节证明：`git diff --quiet 4cf58de..a5c3c21 -- harnesses apps` 退出 0、零文件名，即 DSH Node 测试与控制台整棵树与本基线字节相同。Node 110 项（0-A/0-D 固定清单，排序 SHA-256 `90aae667…`）与 Console 659 / tsc 退出 0 的既有通过证据据此仍适用于本固定输入；本次未重跑发现或测试，也不把那些旧执行写成新执行。

## 防护族核对：已有证据与本轮补测

按任务约定，1-B 与 1-C 已有有效红→恢复→绿的防护族不重复注入（证据见各自记录）：严格 512 KiB 读取上限、Worker/发现双层停止、Router 外层 `is True`（1-B 三次变异）；marker 写失败仍持句柄、未知事件角色差异、冻结镜像绑定、预算边界、重试边界、累计计数幂等、登记与调用点绑定（1-C 各变异）。"controller/native 一组不消失"另有既有钉住测试不重复：`StopConfirmedTests`（外组活着不确认）、`RealStopTests`（真实子进程活组）、zcode 的 `test_missing_controller_receipt_cannot_confirm_the_separate_native_group`（内组回执缺失不确认）、`buddy.runtime.test_windows_process`（Windows Job 观察错误不确认）与 `CodexAdapterTests.test_unconfirmed_stop_retains_goal_auth_and_confirmed_cleanup_handles_refresh`（未确认停止不清凭据）。本微任务只补尚缺的三项代表性保护，全部优先用既有用例或真实调用方，未写照搬实现的测试：

| 防护族 | 代表性缺口与本轮动作 | 见证测试（红） | 变异位置 |
| --- | --- | --- | --- |
| POSIX 组观察错误仍按活着处理 | Job 分支已有钉住，POSIX owned-group 分支在共享收集面无钉住；新增 `ObservationErrorStopTests`（真实子进程退出后 `killpg` 抛 `PermissionError`/`OSError`，`group_alive` 为真、`shutdown_confirmed` 为假、共享收集面不确认停止） | 新增 `buddy.harnesses.test_controller.ObservationErrorStopTests.test_an_observation_error_of_the_owned_group_keeps_the_attempt_unconfirmed`（m1，OSError 子项红） | `base.py` `group_alive` 的 OSError 分支改为按已消失 |
| stdout 无效导致原拒绝 | 真调用方已有钉住（`RouterCollectCoercionTests.test_an_unreadable_router_result_stays_invalid_and_unconfirmed`：坏字节 → failed + `invalid-native-result`）；共享严格读取的"非对象值拒绝"无故障注入，以既有用例为见证补一次 | 既有 `StrictReadTests.test_non_object_values_and_undecodable_bytes_return_none`（m2，`[1, 2]` 被原样返回，3 处子项红） | `controller.py` `read_strict_result` 删去 `isinstance(value, dict)` 门 |
| 证据路径替换仍被发现 | 组件级链接已有钉住（`test_router_read_is_the_same_raising_read_the_retention_uses` 与 `NoToolEvidenceSafetyTests` 的保留面）；最终组件被替换后在真实 Router 收集面无钉住；新增 `ReplacedEvidenceTests`（stdout 被换成指向外部"ok+已确认停止"载荷的符号链接，真 `structured_call.collect` 必须折叠为 failed + `invalid-native-result`，外部文件原样未动） | 新增 `buddy.harnesses.test_controller.ReplacedEvidenceTests.test_a_replaced_stdout_link_still_refuses_at_the_real_collect`（m3，变异下状态变 'ok' 红） | `controller.py` `read_plain_evidence` 删去 `guard_private_path` 调用与 `lstat` 普通文件/大小检查 |

三次故障注入均在任务根的一次性代码副本内进行（`git archive` 固定输入 + 本微任务测试文件），工作区只读源未动；每次按字节恢复并复核 SHA-256 与固定输入一致后再复跑。m1/m3 的红见证是本次新增的两个测试，m2 的红见证是 1-B 已入库的既有测试——新增防护不自我镜像充当迁移证明：迁移证明是基线编号集合完整保留（既有调用方测试一字未动）加上上述真实失效被既有或新增测试钉住。明细（pristine = restored，均与工作区文件一致）：

| 注入 | 变异文件 | pristine=restored SHA-256 | mutated SHA-256 | 红退出码 | 绿退出码 |
| --- | --- | --- | --- | --- | --- |
| m1-observation-error | `src/hey_my_buddy/buddy/harnesses/base.py` | `e0e7609403eea51cc84b05b4619c2d17d5bc958faab731d498436a0a1e80615f` | `e5949424ded6f991781e13f669491fbab140bc0bfdfc1d4965dcb66ade38e0d2` | 1 | 0 |
| m2-strict-nondict | `src/hey_my_buddy/buddy/harnesses/controller.py` | `8460e0f4e9e63540136296339a7a92829982917f7f5873b9621a31dbea6fb41b` | `b56bc92974c501932d4ba283195724bad476eba8eacd5b5a8df20a2793e4de0b` | 1 | 0 |
| m3-replaced-evidence | `src/hey_my_buddy/buddy/harnesses/controller.py` | `8460e0f4e9e63540136296339a7a92829982917f7f5873b9621a31dbea6fb41b` | `08c32786c63482d22c198b258a195afb9e08a79f07d5dcc3e3fdada6d8e1ef7d` | 1 | 0 |

## 聚焦检查（无完整检查，按微任务边界）

本轮实际运行且只运行：两个新测试类单跑（2 项 OK，退出 0）；`buddy.harnesses.test_controller` 全模块一次通过（37 项 = 整合后 35 + 本轮 2，OK，退出 0，此后未重复）；三次变异红（各退出 1）与三次恢复绿（各退出 0）的单用例运行；三次编号收集（加载器收集，非测试执行）。全部命令显式设置 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 指向任务根 `t/`，并清除继承的 runtime/Worker/凭据变量。没有完整检查、没有其余模块的重跑、没有 Console、没有打包，也没有任何真实 harness CLI 或模型启动：全部材料为合成 fixture、mock、进程内收集与真实普通 Python 子进程（`sys.executable -c pass` 一类的短命子进程，仅用于进程组观察）。原生 harness/model 检查 0 次。

本次验收目标的"行为保持"由三点支撑：基线 2,322 个编号完整保留（没有任何既有测试被删除或改名，Worker 运行时与 Router 的既有调用方测试原样在集合中）；`test_controller` 模块一次全绿；1-B/1-C 记录中已验收的红→绿族未重复注入、其证据继续有效。这不是完整检查，Worker/runtime、Router 资格/预算/unknown/凭据清理行为保持的最终确认归 Host 在整步整合后的一次 `uv --frozen` 完整检查与 Console 测试/类型/构建。

## 未验证项与接线余项（如实声明，不伪称实现）

本整合尚无完整检查：本微任务按约定未运行完整检查、Console 测试/类型/构建与打包；`57f6a63` 的 2,453 项 Python（跳过 1）/163 模块/110 项 Node 与 `f8b42a5` 的动态收集 164 模块/2,488 编号都是当时（不含 1-B/1-C/1-D 或不含 1-C/1-D）的事实，仅作历史引用，不填作本次成绩。公共事实/外层/角色模块（`run_contract.py`、`live.py`、`legacy_facts.py`、`harnesses/controller.py`、`roles/controller.py`）已放置，但 `RUN_SEAMS` 仍为空：四个原生 harness 均未注册、未抽取，Worker 与 Router 全部继续走旧载体，`ROLE_RUN_NOT_MIGRATED` 守卫在位。1-C 已披露的公共接线余项归 Host 在首个 harness 抽取切片统一处理：`HarnessRun` 协议对 `services` 的 `Any` 标注收紧、`RunRequest.sessionServices` 描述对象（SessionService）的生成方、已抽取 harness 在 Worker 运行时下的监督形状（外层句柄/截止/取消与 `run_harness` 的对应）；本步不伪称已实现全部运行路径，第五步的 C-Two live 迁移也未开始。DSH Node 停止口径的历史例外按计划原样保留至第四步。独立 ACP 客户端线仍待外部验收：其 71 项编号只作计数与归属链接，不写成通过。本轮仅在 macOS 上运行，Linux/Windows 未验证；本轮未发现新的源代码缺陷。

## 任务根与清理登记（Worker 未删除任何对象）

任务根为 Host 开始时原子创建并登记的 `<system-tmp>/a25v-w6zbxl88/`（t/m/h/d 齐备）。每条检查/诊断命令都显式将 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 指向其 `t/`。本微任务创建的对象全部在该根内并原样保留：`m/` 下 `collect_ids.py`、`compare_ids.py`、`make_test_map.py`、`run_mutations.py`、三份编号 JSON（`ids-baseline-4cf58de.json`、`ids-current-a5c3c21.json`、`ids-final-1d.json`）、`ids-added.txt`、`ids-removed.txt`、`mutation-summary.json`、基线只读副本 `baseline-4cf58de/`、三个一次性变异副本 `mutants/m1-observation-error/`、`mutants/m2-strict-nondict/`、`mutants/m3-replaced-evidence/`（各含 pristine 副本、`mutation-red.log`、`restore-green.log`）；`t/` 下仅 uv 工具自建的锁对象；`h/`、`d/` 未使用。检出内另由 `uv run --frozen` 创建了 gitignored `<worktree>/.venv/`（AGENTS 规定的项目验证环境，不是日常安装，未安装或升级任何日常 runtime）。Worker 未进行手动清理；测试中替换 stdout 的 fixture 构造及普通 `TemporaryDirectory` 自有收尾按用户规定的例外执行。没有用通配符清理，未按同名/日期/无进程推断归属；任务根由 Host 验收后按这一个确切路径整体删除，其他会话物品一律未动。未读取凭据文件内容，未改用户配置、凭据或日常数据。

## 状态

固定 artifact 收齐（本文、变化表、两个防护测试）后停在此处，等本 Host 审查；整步完整检查、Console 测试/类型/构建与整步记录归 Host 在收齐整合后统一进行，随后停等 Claude Code Host 的第一步整步外部验收。本记录不宣告第一步完成，第四、五步及任何 harness 抽取均未开始。
