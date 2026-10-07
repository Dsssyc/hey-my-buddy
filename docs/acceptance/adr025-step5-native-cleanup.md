# ADR-025 第五步开头：四 harness 原生运行主体的小片段清理与 ZCode run 分段

本记录是第五步开头清理微任务的交付材料：在基线 `43d8d63` 的受管 worktree 中，比较四个 `native_run.py` 里重复的小段，把实际相同且被生产消费的实现收敛到新模块 `src/hey_my_buddy/buddy/harnesses/native_support.py`，并把 ZCode 的 482 行 `run` 按其余三个 harness 的既有做法拆成清楚阶段。只重构，不改行为（未触及已批准的 DSH 行为差异、停止口径与实时通道）；不开始 C-Two 切换；角色模块、注册表、公共格式与实时接口零改动。固定提交为本记录所在提交（哈希见交付摘要）；改动路径全部在本任务 executionWorkspace.writeScope 内。

## 更正（继续轮对首版记录的修正）

首版记录声称"全程零已安装 harness 启动：聚焦测试全部使用各 harness 既有的 Python 模拟程序"，该声称不成立，现予撤回并更正如下；本继续轮只改本 MD 的表述与路径占位，未改任何生产代码、测试或 TSV，未重跑任何检查。
事实：zcode 树的 255 项聚焦测试中有 7 项走了本机已安装 ZCode bundle 的代码路径——`test_zcode_native.py` 的 `InstalledZcodeTests` 5 项（启动真实已安装 ZCode CLI 的 app-server 进程，模型提供方是 127.0.0.1 本地 HTTP fixture，builtin/personal 私有 provider 配置指向该本地地址、API key 为假值，依赖 macOS `sandbox-exec`；无真实凭据、无付费模型调用）与 `test_zcode_hooks_probe.py` 的 `FreeProbePipelineTests` 2 项（已安装 bundle 的免费 `--version`/plugin 列表/hook 自检与版本收集，`paid=False`，无模型回合）。其余 zcode 测试与 codex/claude/dsh 三树全部确为 Python 模拟程序（claude 的 `skipUnless(_claude_registered())` 门控的是 run seam 注册，其启动的是 `fixtures/fake_claude.py`；codex/dsh 分别为 `mock_codex.py` 与经补丁 `command_for` 的 `fake_agent.py`）。
能确认的程度：终态持久化日志（`m/003-final/focused-pristine.log`）为 zcode 255 项 plain `OK`、无 `(skipped=N)` 标记，而 unittest 会把跳过数写进裁决行，故上述 7 项确已执行而非跳过；基线侧留存的是编号清单（`m/000-baseline/test-ids-before.txt` 同样包含这 7 项编号）与会话输出（255 OK 无 skip 标记，该基线运行未持久化到任务根）。每次测试实际启动的进程次数未留存于日志（测试 7 项≠进程 7 次，逐测试的进程数无法从留存材料确认），按用户规则不追查日常数据、不重跑。任务说明要求"不调用真实模型或安装版 harness"，本轮聚焦验证在 zcode 树上确实偏离了该要求（该偏离无模型费用、配置与沙盒均私有），首版把它误记为"全部使用模拟程序"是记录错误；已验证的代码与测试结果本身不受影响。
Host 侧复核（Host 结论，本 run 未重放）：固定 artifact/commit 的代码审查未发现具体回归；Host 在整合提交加 scope 副本上复核 731 个受影响测试，其中默认 4 workers/36 模块运行里两个测试文件因检查根路径过长失败，换短私有检查根后原基线与固定输出的 `zcode.test_zcode_inquiry` 46 项、`test_zcode_native` 5 项分别全部通过——确认为 Host 环境问题，与本交付代码无关。既有变异原始日志 Host 已读取，原样保留在任务根。
本继续轮同步把正文中的任务根绝对路径改为 `<task-root>` 占位（确切路径经交付摘要交 Host 私有台账）；受管检出中早期 uv 误留的空 `.venv/` 维持披露不删除；`codex/protocol.py` 的 `utc_now` 副本等 outsideScope 缺口维持原记录。

## 边界与方法

- 运行方式：`uv run --frozen --offline --no-sync`，`UV_PROJECT_ENVIRONMENT` 指向任务根 `<task-root>/t/venv`（冻结锁解析安装，11 个包，离线缓存），`PYTHONPATH=<checkout>/src:<checkout>/tests/python`，`BUDDY_DEV_SOURCE=1`，私有 `BUDDY_STATE_DIR`/`BUDDY_RUNTIME_ROOT`，`TMPDIR`/`BUDDY_CHECKS_TMPDIR` 均指任务根 `t/`，清除继承的凭据与运行时变量（环境镜像 `m/focused-env.sh`）。一次早期的 `uv run` 未先设 `UV_PROJECT_ENVIRONMENT`，在受管检出里留下一个空的、自忽略的 `.venv/`（uv 写入其自身 `.gitignore`；git status 不显示）——未删除，如实报告，等 Host 随任务根一并处置。
- 模型调用与已安装 harness（更正后口径）：全程零模型调用成立——包括安装版路径在内，模型请求只指向 127.0.0.1 本地假模型 fixture 或免付费的 `--version`/探针管线；"零已安装 harness 启动"不成立（zcode 树 7 项测试启动了本机已安装 ZCode bundle，见更正节），首版相关声称已撤回。未运行完整检查，未安装/升级日常运行时，未改配置/登录/凭据/日常数据，未读凭据文件内容。
- 只运行受改动影响的聚焦测试：四个 harness 测试树、新 `test_native_support`，以及经 claude 夹具执行 `run` 的两个 roles 消费模块（`test_registered_review`、`test_schema_worker`）。

## 交付内容：共享小片段（native_support.py，137 行）

只收实际相同且被生产消费的片段，逐个说明差异保留：

- `execution_deadline`：四份实现逐字节相同（仅 docstring 措辞差异），共享后 zcode/codex/dsh 的 `__all__` 导出经导入绑定继续可用（三个既有测试文件按原路径导入该名，全部通过）。
- `CancelFlag`：zcode/codex/claude 三份完整实现相同；dsh 原本是仅含 `is_set` 的子集，现直接使用共享完整类——dsh 生产只调用 `is_set`，继承到的 `set`/`wait` 对 dsh 是不可达代码，语义无差（超集替换，不是行为合并）。
- `ObserverInterrupt`：四份同一语义的标记异常（raise 于观察者 stop、catch 于各 run 的 except），共享一份，docstring 取各 driver 共同成立的表述（记录先行、不绕过记录器与拒绝 I/O）。
- `identity_or_none`：四份实现相同（返回注解差异不影响运行时），共享。
- `shape_guard`：zcode/codex/dsh 三份 `_guard` 相同（claude 的 `_usable` 是 converter 式的另一语义，未统一，见缺口清单），共享为 `shape_guard`，内部经 pydantic `TypeAdapter` 与 run_contract 既有形状校验，未手写框架；各 harness 的派生 guard（`_tool_package = shape_guard(ToolEvidencePackage)` 等）保留在各模块——每个 harness 守护哪些形状是它自己的声明。
- `halt_owned_group`：zcode/codex 两份 `_halt_owned_group` 相同（claude 的 `_stop_native` 签名与返回不同、dsh 走 AcpClient.shutdown，均不动），共享。codex 以 `halt_owned_group as _halt_owned_group` 别名导入：既有防护测试 `mock.patch.object(codex.native_run, "_halt_owned_group", ...)` 依赖该模块全局名，别名是对共享实现的直接引用（非兼容副本），codex 内部三处调用点仍按全局名解析，补丁行为不变。
- `utc_now`：zcode/claude/dsh 的 `_now` 与 codex `native_run` 从 `.protocol` 导入的 `utc_now` 是同一实现（ISO 8601、Z 后缀），四个 `native_run` 现在都直接引用共享实现；`codex/protocol.py` 自身的 `utc_now`（其内部一处消费）不在本任务 writeScope，保留为该文件内部的单份使用，见缺口清单。zcode/codex 的 catalog `discoveredAt` 用的是无 Z 后缀的另一拼法，语义不同，未强行统一。
- `configuration_spec`：zcode/codex 两份 `_spec` 相同，共享；dsh/claude 无此形状，不引入。
- 不合并的近似重复（差异真实存在）：`_RunFacts`/`_Classifier` 三 harness 各不相同（codex 有 dirty 标志与残差桶、dsh 的残差桶名与 mapping 签名不同、zcode 无上限折叠）；zcode 与 dsh 的 `prepare_session_service` 高度相似但 checkpoint/answer 工具推导与 mcp 行内容不同，合并需要开关矩阵，按任务书不合并；`_Spawn`/`_Preparation`/`_probe_version` 字段与语义各异，保留各 harness。

各 harness 的 import 清理：codex 删除 `math`/`threading`/`TypeAdapter`/`NativeIdentity`（随之失去使用方）；claude 删除 `NativeIdentity`；dsh 删除 `math`/`datetime`/`NativeIdentity`；zcode 删除 `math`/`threading`/`NativeIdentity` 与一份基线即无使用方的 `FrozenJson` 导入（基线核对：原文件同样只有导入行，无任何使用）。生产使用的公共函数（`execution_deadline` 等）全部直接引用新实现，未留旧实现副本。

## 交付内容：ZCode run 分段（482 行 → 阶段函数）

按 codex（`run` 校验后委托 `_run`，载体各有函数）与 dsh（载体轮次函数）的既有做法拆分，仍只有一个公共 `run`，同一 owner loop（`_run`）持有原生连接：

- `run`（22 行）：接缝校验（harness 名、服务绑定类型、no-tool 挂载拒绝、无服务续接拒绝）后委托 `_run`——与 codex 的 run/_run 分层同款。
- `_run`（136 行，owner loop）：准备（deadline、私有根、stderr 路径、cancel、facts/classifier/state/tools/outcome）、`notify`/`attention_sink`/`dispatch_feedback` 三个闭包、spawn 与 `runtime/capabilities` 握手、按 `completion_carrier` 分派载体、三个 except 分支、finally 调 `_stop_collection`、工具流包计算、证据留存与结果组装。zcode 测试对 `zcode.native_run._spawn_app_server`/`NativeConnection` 的补丁点不变（调用按模块全局名解析）。
- `_final_message_rounds`（81 行）：final-message 载体轮次（fresh root、每轮新 `input_id`、settled 检查闭包读上一轮 protocol 的生命周期与原实现一致——修正轮不重置 protocol，直到 `before_send` 换新）。
- `_governed_turn`（153 行）：governed completion-tool 载体（绑定校验、续接绑定读取、根开启、投影/用量/sidecar、问询桥、`observe`/`bind_worker_session`/`note_configured`/`after_admit`、settle 后收尾与 close、record/provenance）。
- `_stop_collection`（55 行）：保留观测、桥关闭、保守组停止、EOF 排空、stdout 关闭、取消判定，返回 drained。
- `_evidence_refs`（32 行）：私有证据按原顺序留存（provenance → inquiry → attention → denied → stderr），与 dsh 的 `_evidence_refs` 同款。
- `_finish_result`（37 行）：`_build_result` 组装与 BoardError 降级 fallback（保留已观测阶段事实，只丢弃形状失败的包），与 claude 的 `compose(packages=...)` fallback 同款。
- 载体间的共享可变状态收进 `_CarrierOutcome` 记录（24 行）：原 run 级 17 个局部变量（session_id、evidence、last_protocol、record 等）逐字段等价迁移，初值与写入时机不变；`_build_result`（93 行，未改逻辑）与 `run_discovery`（49 行）保持原位。

基线上即无使用方而被删除的两处死代码（行为等价删除，如实报告）：`started_at = time.monotonic()`（原 run 内单次赋值、无任何读取）与 governed 分支的 `input_id = mount.input_id`（赋值后无读取；`_admit_root_input` 直接收 `mount.input_id`）。

## 顺序证明（调用/异常/停止/私有证据）

`m/002-order-proof/compare_run_order.py`（输出 `order-proof.txt`）把基线 `run` 与拆分后的 `run`+`_run`+五个阶段函数按执行顺序展平为有序操作序列（def/call/assign/raise，except/finally 前缀标注，阶段函数调用处展开为其函数体），归一化仅限：`outcome.X` 还原为 `X`、共享片段新名映射回旧名（`CancelFlag`→`_CancelFlag` 等）、无观察效果的两类位置不敏感项（None 初值、闭包构造与空列表创建）不参与排序。结果：**IDENTICAL OPERATION SEQUENCE**（两侧各 210 个有序操作，diff 为空）。工具流包计算与各 retain 写文件的相对顺序、except 分支次序、finally 内停止→排空→stdout 关闭→取消判定次序均逐项一致。

## 验证证据

- 聚焦测试（均在受管检出上，命令为 `uv run --frozen --offline --no-sync python -m unittest discover -s tests/python/buddy/harnesses/<tree> -t tests/python` 与按名加载）：zcode 255 项 OK（其中 7 项为已安装 ZCode bundle 路径，见更正节）、codex 131 项 OK、claude 165 项 OK、dsh 156 项 OK、新 `buddy.harnesses.test_native_support` 13 项 OK；roles 消费模块 `buddy.roles.test_registered_review` + `buddy.roles.test_schema_worker` 11 项 OK。基线（改动前）四树同样全绿（255/131/165/156），逐树计数一致（基线侧的逐项日志未持久化，见更正节的确认程度）。日志：`m/003-final/focused-pristine.log`。
- 测试编号对账（真实 unittest 加载收集，`m/list_test_ids.py`）：基线 707 项（`m/000-baseline/test-ids-before.txt`）、改后 720 项（`m/003-final/test-ids-after.txt`）；改名 0、删除 0、新增 13（全部在 `test_native_support`，逐项见 `docs/acceptance/adr025-step5-native-cleanup-test-ids.tsv`），未改 707 项以集合相等证明。
- 迁移防护故障注入（隔离单点变异，原件通过与变异失败的实际日志都在任务根）：M1 `CancelFlag.is_set` 不读 cancel 可调用 → 新测试失败，且既有 claude 取消测试在变异副本上失败（8.4s 超时后 FAILED）、原件同测试 OK（0.4s）；M2 `shape_guard` 拒绝不丢弃 → drop-alone 测试失败；M3 `halt_owned_group` 跳过终止升级 → 两项停止测试失败。均为真实断言失败，无导入/环境错误冒充。摘要 `m/fault-injection-summary.md`，日志在各 `m/mutation-*` 目录。

## 无使用方内容与行数

- 无使用方的内容：经逐名核对为空——`native_support` 的 8 个导出各有 2–4 个 harness 生产消费；zcode 新阶段函数各有 `_run` 调用；`_CarrierOutcome` 的 16 个字段在载体/停止/结果组装中各有读写方；派生 guard 各被对应 `_build_result` 消费。两处基线死代码与一份基线死导入已删（见上）。
- 行数：zcode `native_run.py` 1627（基线 1647；run 482 → run 22 + _run 136 + 载体 81/153 + 停止 55 + 证据 32 + 组装 37，`_build_result` 93 与 `run_discovery` 49 未改）；codex 1858（基线 1935）；claude 1244（基线 1285）；dsh 1991（基线 2032）；新增 `native_support.py` 137、`test_native_support.py` 157。
- 阶段边界如上节所列；每个阶段的入口都在其 docstring 中声明。

## 缺口与交 Host 事项（不扩大本任务范围）

- `codex/protocol.py` 的 `utc_now` 与共享实现仍是一式两份：该文件不在本任务 writeScope，其内部仅一处消费（quota candidate 时间戳）；是否并入 `native_support` 由 Host 在整合时定。
- claude 的 `_usable(converter, value)` 与 `shape_guard` 语义相近但机制不同（接受归一化 converter，服务于 `normalize_activity` 等协议投影），本任务未统一；如需统一应由 Host 决定形状。
- dsh 现持有共享 `CancelFlag` 的完整 API 而生产只用 `is_set`（超集，无行为差）；codex 保留 `_halt_owned_group` 模块别名作为既有防护测试的补丁点（直接引用共享实现，非兼容副本）。
- 未发现角色模块/注册表/公共格式/实时接口的调用缺口：本任务未改任何这些面，四树与 roles 聚焦测试全绿。
- 受管检出中早期 `uv run` 留下的空 `.venv/`（自忽略、git 不可见）未清理，等 Host 处置。

## 任务根与一次性材料

任务根 `<task-root>`（确切路径经交付摘要交 Host 私有台账，本记录只用占位符）：`t/`（TMPDIR、BUDDY_CHECKS_TMPDIR、私有 state/runtime、uv venv）与 `m/`（`focused-env.sh` 环境镜像、`list_test_ids.py` 编号收集、`000-baseline/` 基线编号清单、`001-explore/` 与 `001-zcode-run-section.py` 拆分段草稿、`002-order-proof/` 顺序证明脚本与输出、`003-final/` 终态编号清单与原件通过日志、`mutation-m1..m3` 三份隔离变异副本与失败日志、`fault-injection-summary.md`）。Worker 未执行任何删除或清理脚本，未动其他会话对象；任务根整体等 Host 验收后按确切路径回收。本记录中的路径均为仓库相对、`~` 或任务根占位，不含本机主目录布局。
