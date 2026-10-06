# ADR-025 第四步 4-B2：DSH ACP 主体接入共享角色、合作检查点问询与删除 Python 旧入口

本记录是 ADR-025 第四步微任务 4-B2 的交付材料：在基线 `ee0e2f4` 的受管检出（唯一可写范围为 DSH Python 包、对应测试与两份验收记录）内，把 4-B1 的原生运行主体接到共享角色上——`DshAdapter` 改为纯能力描述（无 prepare/start/collect/cancel 与 direct-LLM 入口，`dsh/runner.py`、`dsh/catalog.py` 删除且无兼容层），Worker、fast 与无输入发现实际经 `dsh/native_run.py` 的 `run`/`run_discovery` 与唯一角色控制器 `roles/run_controller` 执行，Host 问询经公共 `harnesses/inquiry_bridge.py` 的合作检查点送达（共享 `bind_live_channel`，`EXISTING_CAPABILITIES["dsh"]`），读工具范围在原生 run 可用，DSH 公共 review 资格继续不开放，`native_resume` 保持 false（`reconstructed-new-session` 重建新根会话、`native-session` 明确拒绝）。输入状态 `2ded751` 是上一轮超时取消轮的未验收 partial（Host 已核对累计补丁摘要与路径、未整合未签收）；本轮在其上收尾三件事：恢复可用性回退、把测试与故障注入写成固定证据、补齐本记录与编号表。`harnesses/dsh/` Node 树与 `yaml_bridge.py` 及其安装资源留 4-C 删除，本项内部验收不等于第四步外部验收。

## 本轮整改（对上一轮 partial 的修复）

- 可用性回退恢复（Host 指出的非批准行为差异）：`DshAdapter.available` 与 `native_run.check_preparation` 在无绑定选择记录时不再直接拒绝，而是走 `runtime_selection.command_for` 同款的既有有界 `discover("dsh")`（与启动路径同一机制，不新增第二个发现外层、不做厂商静态证明），绑定记录仍按 ready+command 判定、坏选择仍拒；Node runner 要求与 runner_path 检查随载体删除一并去除。
- 上一轮取消时留下的半成品补齐（Host 问询 `adr025-4b2-partial-home-nameerror` 独立复核的同一缺陷）：`_launch_agent` 签名已去 `home` 形参但 `AcpClient.start` 调用残留 `home=home`，这一公用启动点上的 NameError 使 partial `2ded7516` 的任何原生启动（run、发现、假程序）都不可运行；已移除该残留并以真实假 ACP 启动见证修复（见"验证证据"，全部十模块经该启动点）。`AcpClient.start`/`launch` 的 home 能力保留，属已验收启动包装面且有 ACP 测试消费；4-B1 记录曾标记 `run_discovery` 的 `home` 覆盖参数仅测试消费，该参数连同 `_launch_agent` 的透传已一并删除。
- `run_discovery` 失败携带的 `discovery_shutdown_confirmed` 事实的注释改为如实：事实由本操作自己的停止收集产生并随错误携带，共享控制器在 1d28a16 已消费该事实；本轮固定组合仍包含该消费方，Host 据当前源码更正原记录的待接线判断。
- 测试可复现性缺口（本轮发现并修复）：`test_no_tool_dsh` 与 `test_dsh_role_wiring` 的子进程链继承调用方 shell 的 `HOME`，假 ACP 程序的强制私有 HOME 绊线随之误触（agent exit 3）；前轮组合副本运行仅在外层钉私有 HOME 时可复现。两个夹具现已像进程内用例一样把 `HOME` 钉到各自私有根，套件自洽，任何调用环境可复现（两基线均验证：不钉必失败、钉后全绿）。

## 边界与方法

- 全程零模型调用、零安装版 DSH 启动：原生行为全部由本 harness 的假 ACP 程序覆盖；真实冒烟（只读命令写文件、Worker 用量不倒退、实际生效工具集）按任务书由 Host 在接线后执行。配置与凭据仅指路径不读内容；用户日常 DSH 不受影响。
- 任务根 `/private/tmp/a254b2-5vbuva5_/`；所有命令 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 指其 `t/`，本轮材料在新目录 `m/final-comb/`；未覆盖任何旧实验（前轮 `m/build2/comb`、`m/fault-m1..m4`、`m/ids/` 原样保留），未回收此前实验或任务根；新组合镜像为复现范围内源码删除，使用 rsync --delete 移除了旧 catalog.py 与 runner.py（两棵源码树经 Host 对比确认）。
- 正式验证副本 `m/final-comb/`：`base-patched` = Host 基线 `525d4cb` archive 加仅 DSH 激活两处；`comb` = `base-patched` 加本任务 scope 覆盖（`rsync --delete`，副本内 `dsh/runner.py`、`dsh/catalog.py` 确认不存在，scope 树与检出逐字节相等）。1d28a16 版 Host 补丁上下文已不匹配 525d4cb（Host 期间自行激活了 codex/claude 同一 seam），按"仅 DSH 激活两处"在副本内应用等价编辑——registry 导入+注册各一行；原基线 525d4cb 的 `live.py` 仍是 realtime，验证组合实际改为 cooperative-checkpoint（Host 逐字核对）；原说明把组合状态误记成基线状态（留存说明 `m/final-comb/applied-activation.txt`，Host 原补丁文件未动）。
- 解释器用 Host 实施检出 `~/.codex/worktrees/adr025-run-module/hey-my-buddy/.venv/bin/python`，不解析符号链接；作者检出内快跑用任务 `m/iter/registered_unittest.py`（进程内镜像两处激活），正式证据一律以真实补丁副本为准。

## 验证证据

- 组合副本正式运行（`m/final-comb/dsh-scope-final.log`，OVERALL-RC 0）：DSH 测试树十模块 174 项全部通过——acp 三模块 71 项（未改保护）、`test_dsh_activity` 6、`test_dsh_role_wiring` 11、`test_dsh_session_storage` 11、`test_dsh_tool_evidence` 8、`test_dsh_usage_plugin` 7、`test_native_run` 54、`test_no_tool_dsh` 6。完整检查按任务书归 Host 整合后统一执行。
- 作者检出发育期快跑：两个改动测试类 7 项通过；`test_native_run`+`test_dsh_role_wiring` 全模块 65 项中 8 失败 1 错误，全部是依赖激活注册的子进程用例——无注册时在 seam 处大声失败正是这些用例的设计（模块 docstring 声明），正式结论以组合副本为准。
- 测试编号（真实 `python_test_modules`+unittest 集合，装载错误如实记为 load_errors，不留 FailedTest 假行）：`525d4cb`+激活 2790 项/176 模块/0 装载错误；加本任务 scope 后 2793 项/177 模块/2 装载错误。同基线 scope 增量 +41/−38，未变集合 2752 项两侧相等；上一轮固定 scope 到本轮最终集合的差 +20/−12 = 本任务 +3/−2（两条 check_preparation 见证更名、一条前轮尾部补建后其枚举未及收录的 Worker 两层停止见证）+ Host 基线自身前进 +17/−10（claude/codex，与无 scope 基线差逐项一致）。逐行处置见 `docs/acceptance/adr025-step4-dsh-wiring-test-ids.tsv`，原始清单与账目在任务根 `m/final-comb/ids/` 与前轮 `m/ids/`。
- 迁移防护故障注入未改未重跑：m1（journal 外来记录绑定）、m2（检查点/回答回执验签）、m3（Worker 结果两层停止）、m4（回合序数顺序）四项"变异 FAILED→原件 OK"仍以前轮 `m/fault-injection-summary.md` 为准；前轮 4-B1 的 M1–M5 防护未动。本轮可用性回退恢复以 mock 记录四象限断言为守卫，未重复变异。

## 交付内容（按运行阶段）

- 描述与准备：`DshAdapter` 只剩能力声明、本地资格（review 不开放）、可用性（选中即确认、未绑定走既有发现）、turn 来源校验委托与经 seam 的发现；`check_preparation(spec, environment)` 在任何进程存在前判定；Worker 准备沿用共享 executor 的窄签名与 `BoundSessionServices`。
- 启动：原生命令只用已安装 DSH（`command_for`，含未绑定时同一发现回退），所有启动（run、发现、假程序）走已验收 `dsh/acp/launch.py` 私有包装：强制本运行私有 `DSH_HOME`、`HOME` 继承、`native_environment` 白名单保留代理/CA；统一 patch 顺序（源 home 指路行、私有记录根钉住、工具范围行、两项始终关闭行）不变；15 项工具行清单、清单外新工具行只报保留事实、optional 私有 zstd 记录读取、按原生工具名归类均保持。
- Worker 治理回合：共享角色读取 SHA/size 核对后的 turn-provenance、inquiry-report、attention-report 事实引用，复用已验收 `RootTurnEvidence` 的签收、工具排除与问询回执核对；只报事实，outcome 与 seal 留角色；`validate_turn_provenance` 只接受经验证的有序 finish 证据；两层停止（结果自身原生层 + 持有侧）缺一即不可发布。
- 合作问询：`InquiryBridge(credentials, identity, journal_path, error_factory=NativeError, event_metadata=仅 kind/toolName, limitation=仅检查点送达)`；确认签收后才推进 journal；答案经验证回执后 completed 成立，已送达未回答转 attention；被拒升级为 attention 事实且 completed 不能顶替。
- 续接：`native_resume=false`；`reconstructed-new-session` 建新根会话（专属见证），`native-session` 明确拒绝；`session_facts` 只报存储/可见性事实（attempt 私有 sessions 根、原生 app 不列出、无绑定），不决定业务续接。
- fast 与发现：fast 经注册 seam 与真实角色控制器（公开回执、两层停止、纠正轮同进程新会话、capture 报启动范围原生证据）；无输入发现经 `roles.run_execution.discover_models` → `run_discovery`（不发送 prompt，失败携带自有停止事实）。

## 行为差异与声明边界

- 五项已接受差异不变：检查点送达、fast 多出 DSH 系统提示词、原生续接为未接线的新能力（本次仍拒绝）、所有 hey-my-buddy 启动关闭 session-title-llm、关闭 session-telemetry-otel（后两项不影响用户交互 DSH）；未知停止不报 gone；除五项外本轮未发现需报告的新行为差异。
- 未验证（不因测试通过而声称）：只读预设下"用命令写文件"的原生强制、真实会话记录上的 Worker 用量不倒退、实际生效工具集，均待 Host 接线后计划内冒烟；`run_discovery` 的停止事实由已有共享控制器消费。

## 公共整合清单（Host / 4-C）

- `roles/run_controller.py` discover 分支已在 1d28a16 捕获异常，只采信 `error.discovery_shutdown_confirmed is True`，并返回有界错误原因；正式组合与 Host 当前代码相同，无额外待接线项。本任务没有修改这个公共文件。
- 两个不可装载的邻接测试模块（comb 上 load_errors，生产 src 无同引用）：`tests/python/blackboard/routing/test_stage2_review_scope.py` 第 14 行 `from ...dsh import runner as dsh_runner`（模块已删，其三项"未接线即拒"见证待 Host 按注册 seam 重写）；`tests/python/buddy/harnesses/test_adapter_usage.py` 第 22 行 `from ...dsh.adapter import ... native_usage_sidecar_path`（符号已随记录读模型迁移，其 8 项 DSH 用量见证的意图由未变集合中的记录读模型见证承载、5 项 Codex 见证为连带，Host 重接导入后编号自动回归）。
- 两个邻接模块的 4 个旧接缝用例在 comb 上失败：`tests/python/buddy/harnesses/test_private_adapter_invariants.py` 的 `test_dsh_coding_and_reconstructed_claude_continuation`、`test_mock_dsh_fast_router_and_command_leave_only_evidence`（引用已删 adapter 面），`tests/python/buddy/harnesses/test_controller.py` 的 `DshCallerFdBoundaryTests` 两项（旧 runner fd 行为）；均属 Host/4-C 重接范围，本任务未代改。
- 打包与 Node 树（4-C）：`packaging/runtime-assets.json` 的 `dsh.runner`/`dsh.catalog` 资源行、`harnesses/dsh/scripts`、`harnesses/dsh/plugins` 目录与 `yaml.bridge` 行，随 Node 树整体删除。
- 公共 discovery/runtime_selection 无阻塞：`discover("dsh")` 直接对已安装 dsh 做版本握手，不依赖项目 Node 要求；项目级 Node 要求与打包调整按计划归 Host 4-C 统一处理。

## 无生产使用方与实现说明

- 本轮删除的无消费方项：`_launch_agent`/`run_discovery` 的 `home` 透传参数（4-B1 记录在案的唯一测试消费方，残留引用一并清理）；除此之外本轮 diff 未发现新的无读写方字段或类；`discovery_shutdown_confirmed` 已由共享控制器生产读取，事实保留。
- 源码行数：`dsh/native_run.py` 2105、`dsh/adapter.py` 66、`dsh/protocol.py` 677；测试 `test_native_run.py` 1257、`test_dsh_role_wiring.py` 359（新模块）、`test_no_tool_dsh.py` 160、编号表 70 行。删除的 `dsh/runner.py`、`dsh/catalog.py` 为整文件删除，无兼容层。

## 清理与环境偏差

- 偏差一：1d28a16 版激活补丁不适用于 525d4cb（上下文移动），按 Host 指示在副本内应用等价两处编辑（见"边界与方法"），未写任何受管公共文件，Host 原补丁与旧副本未动。
- 偏差二：前轮子进程套件只有在外层调用环境钉私有 HOME 时才可复现（本轮定位并修复夹具，两基线对照实验存于本轮后台命令输出，结论已入"本轮整改"）；前轮 `m/fault-injection-summary.md` 的"原件 OK"结论在本轮同 scope 同基线材料上可复现（需钉 HOME 或用本轮后夹具）。
- 偏差三：`m/final-comb/comb` 内存在首轮测试运行写入的 `__pycache__` 目录（此后运行均设 `PYTHONDONTWRITEBYTECODE=1`）；按"不手动删除"保留，组合清单（`m/final-comb/combined-manifest.txt`，find+sha256，排除 `__pycache__`）如实排除。
- 本记录提交即本线固定交付（提交哈希随结构化结果报告）；任务根 `<task-root>` 未回收；新组合镜像同步两条已授权源码删除的事实见上，旧实验保留，等 Host 验收后按登记确切根整体回收；记录中路径均为仓库相对、`~` 或占位。
