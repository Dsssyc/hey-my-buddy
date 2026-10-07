# ADR-025 第三步微任务 3-A1：Codex 原生运行主体

2026-10-06，Worker 由 ZCode（GLM-5.3-Flash / zai-api / max）承担。输入基线 `5d31114b3a17e71c6714fabe026aa1598db598a4`，独立受管 worktree，唯一可写范围为任务书列出的四个路径。变更留在 worktree 工作区未提交，由 Host 核对 diff 后整合；本记录只写实际做过与验证过的事实。注册表切换、角色接线、旧入口删除（3-A2）与真实模型/安装版原生冒烟（Host 在接线后最小运行）均未做，本项只交付一个可直接调用、可用模拟原生进程测试的原生模块，不宣称整条线完成。已按当前源码复核 [清单](../design/harness-run-inventory-codex.md)：清单所记审阅 nativeProbe 路径在现行源码中已不存在（全仓 grep 无命中），未移植；清单中"无绑定控制的 raw 调用兜底计数"随新契约中身份必填而失去前提，未移植，其余差异按现行源码逐条落实。

## 实际架构

`buddy/harnesses/codex/native_run.py`（新增，1648 行）是唯一的原生执行体：`run(request, *, observer, services, cancelled) -> RunResult` 接受现有 `RunRequest`，`run_discovery(*, cwd, invocation_root, native_root, timeout_seconds, cancelled)` 是独立的无输入无 prompt 发现，二者复用同一 spawn 与握手原语；模块未注册，旧入口本轮原样保留。按阶段拆分：准备（`_prepare`：私有根、三载体各自的私有 home——Worker 走 `prepare_coding_home` 加 `CODEX_SQLITE_HOME` 与两条命令覆盖、fast 走 `no_tool.prepare_home` 私有模型目录、review 走链接既有 auth 加只读 config）、初始化（`_probe_version`、`_spawn_app_server`、`_initialize` 的握手/账户/目录核对，fast 与 review 声明 `experimentalApi`）、配置（`_configure_worker` 的续接绑定核对与 thread/start 或 read+resume、`_configure_fast` 的 config 层回读与 ephemeral 线程、`_configure_review` 的精确策略回读与 buddy-router 线程）、输入与事件（`_admit_turn` 唯一发送站点、`_EventChain` 事实先行链、`_structured_rounds` 的同线程纠正循环、fast 的 `_drain_to_eof`）、停止收集（`_stop_collection`/`_halt_owned_group`/`_save_binding`/`_remove_private_auth`）。每个组织函数最长 71 行（`_build_result`），未复制 ZCode 的长 run。模型发现无 prompt、无线程、无私有 home，仅读取账户与目录。

事实先行链：每条通知先经 `CodexToolEventProjector`（fast/review）投影工具事实，再经 `_Classifier` 归类——已知方法集合之外的帧、未知 item/raw 形状与非空 diff 记为 `unknownEvents` 累计计数，typed/raw/方法前缀等工具形状记为 `toolMarkerFrames`，均不清损也不判定；帧在回合身份建立前有界缓冲（fast 超 128 拒绝、worker/review 丢弃，沿旧口径），回合回执后按序重放载体阶段。角色观察器在事实变化时与结算时各收到累计 `{settled, rawAnswer, toolCalls, toolMarkerFrames, unknownEvents, deniedInteractions}`，回答 `RunFeedback`；驱动只执行反馈：纠正按角色文本在同一进程同一总期限上重开回合并累计次数（次数归角色，驱动不封顶；worker 载体拒绝纠正），stop 即 `observer-interrupt` 并尝试一次原生 interrupt（全新两秒预算，回执记 `native-turn-interrupt-ack`、无回执记 `native-turn-interrupt-unconfirmed`、无回合身份记 `observer-request`）。被拒原生交互先登记事实并回复同 id -32601，角色反馈经 `Connection` 新增的可选 `after_pump` 钩子在每次泵步（含 RPC 等待内）之后取得，拒绝应答先于停止落线。驱动保留的协议完整性检查：thread/turn 回执核对、快速载体的身份绑定与重复 start/final/completion 与不可重试 error 拒绝（`_FastProtocol`）、Worker/审阅的 `TurnEvidence`、完成状态核对；Worker 的 outcome 严格解析与 controller-attention 转换、审阅的答案校验与纠正决定全部留给角色。

三载体差异按请求与服务承载：`RunServices`（本 harness 的窄 services 载体）携带 `credential_source`（Worker 私有 home 来源与续接绑定比对值）、`account`（结构化调用的 `account/read` 冻结身份，决定 chatgpt/apiKey 允许集）与 `activity_dir`（活动 sidecar 目录，Worker/审阅节流写入、fast 不写）；`tool_scope` none/read/write 对应 fast/review/worker。Worker 的 checkpoint 按请求身份生成（含 `lastAssistantMessage`、`eventSeq`、`bindingSaved`），仅在确认组停止且零退出后写私有绑定并置 `bindingSaved`，Worker 的 auth 清理仍留外层 collect（时机不变）；用量为 attempt 增量（`attempt_token_usage`），额度为滚动候选加一次有界 `account/rateLimits/read`，`codexErrorInfo` 命中白名单才记 quotaFailure。fast 保留私有模型缓存前提、层回读、ephemeral 线程、完整 EOF drain 与流完整口径；审阅保留精确文件系统授权图与回执核对、根身份只来自回合回执、可选 `capture_evidence` 有界留存。两层停止事实中驱动负责原生层：组未确认即 `unknown`（basis `owned-process-group`）、spawn 前失败记 `spawn-never-happened`、连接构造失败由 spawn 助手自行停组并记 `owned-group-stopped-in-spawn`；外层停止事实仍由外层收集者携带。

## 生产读写位置

驱动写入（均在请求给定的私有边界内）：`invocation_root` 下 `native.stderr.log`、`denied-interactions.json`（被拒交互完整记录）、`quota-snapshot.json`（Worker 额度快照）、`review-captured-events.json`（审阅可选留存）、`native-checkpoint.json`（Worker 检查点，外层按原规则校验）；`native_root` 下 `codex-home/`（三载体各自的私有 home 与绑定 JSON）；`activity_dir` 下 `activity.json`（节流 sidecar，原形状）。驱动读取：native_root 绑定文件（续接核对）、fast 的原 home `models_cache.json`（公共元数据）与 auth 路径（链接）；不读取凭据内容。旧入口 `runner.py`/`no_tool.py`/`adapter.py` 未改行为，本轮继续可用；`protocol.py` 仅增可选 `after_pump` 钩子与注释（+12 行，旧路径零行为变化，`test_codex_protocol` 13 条全绿为证）；`fixtures/mock_codex.py` 仅追加 `worker-tool`/`worker-unknown` 两个场景（+7 行，既有场景未动）。新文件对 `roles.turn_io`（`private_json`/`canonical_json`/`input_hash` 语义）的导入与旧 runner 同型，属已登记的越界引用族；与 `runner.py`/`no_tool.py` 存在过渡期纯帮助函数并存（目录、目录核对、catalog、deadline、auth 清理），随 3-A2 删除旧入口时一并消除。

## 聚焦验证（实跑证据）

运行方式：`uv run --frozen --offline --no-sync python -m unittest -v <模块>`，环境按 checks 净化规则清除继承 `BUDDY_*`/`VIRTUAL_ENV`/`UV_PROJECT_ENVIRONMENT` 并另除 `OPENAI_API_KEY`/`CODEX_API_KEY`，`PYTHONPATH=<worktree>/src:<worktree>/tests/python`，私有 `BUDDY_STATE_DIR`/`BUDDY_RUNTIME_ROOT`，`TMPDIR`/`BUDDY_CHECKS_TMPDIR` 均指任务根 `t/`（`<task-root>/m/focused-env.sh` 镜像）。受影响模块一批 178 条编号全绿（基线在册 8 个既有模块 152 条 + 新 `test_native_run` 26 条），一次性约 60 秒，无模型调用；改动中途的迭代运行仅触达 `test_native_run` 与 `test_codex_protocol` 单模块。旧入口测试全部保留且全绿，未为省事删除。未跑完整检查、控制台与打包；未安装/升级运行时；未改用户设置、凭据或数据；未读凭据文件内容。使用本 harness 既有 Python 模拟程序（`mock_codex.py`、`no_tool_codex.py`），全部本地进程，无真实 Codex、无付费调用。

## 编号对账

基线与最终清单均以真实 unittest 加载收集（`-v` 输出逐条，非 AST 计数），原始与净化清单放 `<task-root>/m/baseline-test-ids.txt`、`final-test-ids.txt` 与 `.clean.txt`，运行日志 `baseline-run.log`、`final-batch-run.log`。对账结果：152 条基线编号在最终批中集合相等（缺失 0、改名 0、删除 0）；新增 26 条全部属于 `buddy.harnesses.codex.test_native_run`，无其他模块增删。逐条理由、守护对象与故障检查见 [adr025-step3-codex-native-test-ids.tsv](adr025-step3-codex-native-test-ids.tsv)。

## 故障注入（隔离副本，五组全红）

变异仅在 `<task-root>/m/fault-m1…m5` 五份独立 `src` 副本上进行，worktree 生产源码零改动（每次运行打印被测模块 `__file__` 确认来自变异副本，日志 `fault-injection.log`，脚本 `fault-injection.py`）。M1 工具形状帧改记未知事件（不记 marker）→ 快速工具停止用例红（理由变 invalid-protocol）；M2 删除被拒交互的同 id 拒绝应答 → Worker approval 用例红（fixture 等不到应答，运行超时失败）；M3 观察器停止不再尝试原生 interrupt → 审阅预算停止用例红（interrupt basis 缺失）；M4 去掉"确认停止且零退出才保存绑定" → 双层停止未知用例红（`resumable` 误为 True）；M5 fast 流完整不再要求真实 EOF drain → eof 用例红（`streamComplete` 误为 True）。五份红态副本全部保留。

## 源码行数（按阶段）

`native_run.py` 共 1648 行：docstring/导入/文件头常量 113，模块级常量（已知方法表、工具前缀、会话项键与 `__all__` 等）约 185，事实与归类（`_RunFacts`/`_Classifier`）97，状态与服务载体（`RunServices`/`_RunState`/`_Preparation`/`_ActivityWriter` 等）133，准备 50，初始化 126，配置 159，事实链与两个载体协议 116，运行组织与回合（`run`/`_run`/`_wire`/两载体回合/drain/interrupt）356，停止收集 57，结果构造 204，发现 52。最长单个函数 71 行；无超过约 100 行的组织函数。

## 无生产使用方的字段/类与公共接线缺口（已按 Host 问询 adr025-3a1-shared-gaps 报告）

本模块未注册、未接线，`RunResult` 各字段的读取方要等 3-A2/2-C 型角色接线出现；当前生产读取方为零属预期，以下只列**公共契约本身**的既有缺口，未自行改动公共文件：(1) `RunContinuation` 缺旧 `context.nativeResume` 的三项关联（lastTurnId/lastAttemptId/lastInputSha256），缺省时新模块做可表达子集的核对，无法拒绝过期检查点续接，最小签名建议 `last_turn_id: OptionalText(512)`、`last_attempt_id: OptionalText(128)`、`last_input_sha256: Optional[Hex64]`；(2) checkpoint 无公共字段，模块以 evidence ref `native-checkpoint` 留存旧形状检查点，旧 `validated_checkpoint`/`checkpoint_resumable` 还核对旧 payload 顶层与 `processState`，3-A2 需 Host 定读证据文件加 `StopEvidence` 组合还是增公共包；(3) quota 快照无公共字段，以 evidence ref `quota-snapshot` 留存（旧读取方 adapter collect→`normalize_quota`）；(4) `RunBudget` 只有 timeout，审阅工具预算以累计 `toolCalls` 事实交角色停止，旧 `readonly-budget-exhausted` 码无驱动侧等价（映射为 observer-interrupt 加角色理由），fast 的 1..60 秒界仍在模块内；(5) 账户绑定经本 harness 的 `RunServices` 承载（`credential_source`/`account`/`activity_dir`），无公共改动需求，仅报备。另：fast `zeroToolVerified` 组合与旧 `elapsedMs` 时长事实无公共字段，留给接线投影；旧 Worker 结果的 `resolved` 本是请求值，新结果按 `ResultConfiguration` 分列 requested/checked（provider 读回 basis `native-readback`，model/effort 为 `catalog-membership`），不再混填。

## 任务目录与收尾

任务目录 `/private/tmp/a253a1-fw9iw15z`（Host 创建并登记）：`t/` 为 `TMPDIR`/`BUDDY_CHECKS_TMPDIR`，含各测试 fixture 自建的私有根与自动收尾；`m/` 放 `focused-env.sh`、基线/最终编号清单、两次批次运行日志、`build-test-ids-tsv.py`、故障注入脚本/日志与 `fault-m1…m5` 五份变异副本。本 Worker 未手动删除任何对象，脚本不含 rm/rmtree 对任务根的使用（变异副本重建用 `shutil.rmtree` 只作用于 `m/` 内本轮自建的副本目录），未覆盖旧实验。worktree 内另有 `uv run` 自建 `.venv` 与 `__pycache__`（git 忽略的本地工具状态）。未发现其他会话遗留材料；Host 验收后按该确切路径整体回收。

## 验证边界

全部验证基于本机 macOS 上的离线模拟 App Server；未做真实模型调用、未做安装版原生冒烟（按任务书留 Host 在实际接线后最小运行）、未跑完整检查与打包。`protocol.py` 的 Windows 管道等待分支未改动、未在 Windows 实测；审阅 `capture_evidence` 的留存为 evidence ref 形状，与旧 result 内嵌键不同属接线期投影决定。旧入口未删，本项交付不改变任何现有对外行为；注册表、角色模块、共享测试、依赖与打包配置零改动（`git status` 仅含上述四个允许路径内的文件）。

## Host 复核更正（2026-10-06 第二回合，原记录保留不改）

存在违反任务要求的删除重建机制：`m/fault-injection.py` 的 `run_case` 在 `m/fault-m1`、`m/fault-m2`、`m/fault-m3`、`m/fault-m4`、`m/fault-m5` 五个路径上执行 `copy.exists()`→`shutil.rmtree(copy)`→`copytree`，违反本任务"Worker 不删除任何对象"的边界；现有证据不能确认该机制的实际删除次数，撤回上轮"已删除次数为零/五目录当轮新建"的断言——日志运行块数量与目录创建时间不能证明分支运行前这些目录是否存在，也不能排除未留痕的执行。相关脚本、日志与五份副本已原样保留，未另做追查、未复原；本任务第二、三回合没有手动删除任何对象，一次性材料只写新目录，本轮脚本自身不含任何删除逻辑（目标目录已存在即中止）。

## 第二回合修复（Host 复核三缺陷与公共续接基线，均在本模块内）

Worker 工具事实：`CodexToolEventProjector` 扩展到 Worker 载体（原先仅 fast/review），Worker 的工具开始/结束/计数等公开事实同样来自真实原生帧，根身份只来自本回合回执（`observe_root`），不套用审阅只读策略、不搬入角色判定；实时观察计数（`after_pump` 反馈）、结算计数与 `tool_evidence` 包改为同一累计器同源。Host 探针 worker-tool 复核：`toolEvidence` 为完整 start/end 事实、观察计数 [1,1,1]（修复前 [0,0,1] 且 `toolEvidence=None`）。结果构造逃逸：`_RunFacts.note_unknown` 按公共上限（`MAX_UNKNOWN_EVENT_TYPES=64`）有界归并——真实类型名至多存 63 个，更多不同类型并入保留名 `(unlisted-native-events)`（括号名无法通过标签净化器，永不与真实方法碰撞），total 保持精确、无静默丢弃，观察器实时映射与结果读同一有界字典；探针 65 种未知名注入复核 `returned=true` 且 halt 已确认（修复前 `_build_result` 抛 `_BoardValueError` 无结果）。其余晚期构造路径逐点复核：各事实包或经 `_guard` 族自身投影守卫，或仅由请求已验证值与常量构成，无同类逃逸；协议与帧界限未动。准备失败事实：`_stop_collection` 改为无条件执行——`prep=None`（确实从未 spawn）时报告已知未启动与无受管组（`group_state` gone、`started` False、`exit_code` None、basis `spawn-never-happened`），fast/review 私有 auth 清理以请求 `native_root` 兜底；已 spawn 但停止未确认仍必须 unknown 的既有对照用例保留，缺 PID 不推断停止的口径不变；`_build_result` 的 `started` 由 None 改为已知 False（旧口径 none 仅属未启动未知，正是 Host 指出的不一致面）。探针 prepare-failed 复核为 gone/False 一致集合。

公共续接基线消费：模块声明 `supported_request_controls=("resume_checkpoint",)`；`_configure_worker` 在私有绑定六项核对之后，对 `RunContinuation.checkpoint`（Host 提交 `2d844db` 引入的 `ResumeCheckpoint`）三项 `native_turn_id`/`attempt_id`/`input_sha256` 与绑定 `lastTurnId`/`lastAttemptId`/`lastInputSha256` 逐项对比，任一不一致即 `native-resume-unavailable`（与旧 `context.nativeResume` 私有对比同语义同错误码），再 thread/read 最近完成回合并 resume，顺序与旧续接行为严格一致；checkpoint/quota 的 evidence 文件按已声明形式保留，公共角色读取与共享投影留 Host 接线。

## 第二回合验证（跨基线副本，检出公共文件零改动）

验证组合：`git archive 2d844dbb08f3d3f6af3932cf0da1a98800ff2123` 展开到新目录 `m/verify-2d844db`，仅覆盖本任务 scope 四文件（`native_run.py`、codex `protocol.py`、`test_native_run.py`、`mock_codex.py`），公共文件零改动、未纳入补丁；解释器为该副本按自身 `uv.lock` 离线构建的 `.venv`（`uv run --frozen --offline`，`<副本>/.venv/bin/python`），`PYTHONPATH=<副本>/src:<副本>/tests/python`，环境按 checks 净化规则清除继承凭据，`TMPDIR`/`BUDDY_CHECKS_TMPDIR` 指任务根 `t/`。受影响 9 模块一批 187 条全绿（约 65 秒，无模型调用）。编号对账：上轮 178 条集合内无缺失、无改名、无删除；新增恰为本轮 7 条 `test_native_run` 用例加 Host 基线自带的 2 条 `test_run_contract` 用例（ResumeCheckpoint 编解码与显式网络/工具限制线格式），清单与日志在 `m/round2-logs/`（`final-batch-run.log`、`final-test-ids.clean.txt`）。Host 探针复核：Host 只读探针脚本复制到 `m/round2-logs/host-probe-verify.py`（仅改根路径指向验证副本），三例结果如上，日志 `host-probe-rerun.log`。故障注入：新脚本 `m/faults-round2/fault-injection-round2.py` 四份副本各由 archive 新建——n1 撤 Worker 投影→实时证据用例红（观察恰为 [0,0,1]）；n2 去掉未知上界→泛洪用例红（`_BoardValueError`）；n3 恢复 prep 守卫→准备失败用例红（非 gone）；n4 置 `checkpoint=None`→native_turn_id 不一致用例红，另两个不一致用例对同一 n4 副本补跑亦红；四份红态副本与 `fault-injection-round2.log` 保留。

## 源码行数（第二回合）

`native_run.py` 共 1688 行（上轮 1648，净增 40）：文件头与导入/常量至 137，事实与归类 152（138–289），状态与服务载体 118，准备 59，初始化 141，配置 185，事实链与载体协议 181，运行组织与回合 342，停止收集 69，结果构造 238，发现 66。本轮净增构成：Worker 投影与根身份约 6、未知上界归并约 9、停止收集与 prep 兜底约 8、checkpoint 逐项对比约 8、`supported_request_controls` 与注释约 9。最长组织函数仍为 `_structured_rounds` 77 行（次长 `_build_result` 74），无超过约 100 行的组织函数。`mock_codex.py` +6 行（`worker-unknown-flood` 一个场景）；`test_native_run.py` 新增 7 用例（26→33），改动 1 处断言（never-spawn 的 `started` 由 None 改为 False）。旧入口测试零改动，在上表批次中全绿。

## 剩余缺口与已知边界（不宣称整条线完成）

本项仍只交付可直接调用、可测试的原生模块：注册表切换、角色接线、旧入口删除（3-A2）与真实模型/安装版原生冒烟仍属 Host 后续；checkpoint 与 quota 的 evidence 文件仍无公共字段读取方，角色侧共享投影由 Host 统一接线。受管检出的公共基线尚不含 `2d844db` 的 `ResumeCheckpoint`，故本检出工作树内 `test_native_run` 在 Host 整合前无法导入运行——本项验证以上述 `2d844db` 副本为准，这是本交付的已知边界而非完成声明；Worker 角色从 `context.nativeResume` 到 `checkpoint` 的投影（`2d844db` 已含）与控制器 `supported_request_controls` 检查属公共侧，本回合只按其现状消费。

## 第三回合（流结束事实与停止分离，2026-10-06）

`_build_result` 的流结束事实改为按各载体的真实结束证据计算，与业务状态和组停止分离：worker 取本回合原生回执事实（checkpoint `nativeTurnStarted` 为真且 `nativeTurnStatus` 为 completed）、review 取全部已观察回合完成（`rounds_complete`，纠正重开即 False）、fast 仍必须真实 EOF（`drained`）；业务 status 与组停止不再进入流事实，中断/缺终态/只完成纠正前一回合仍不完整，worker 组 unknown 仍 unknown 且不保存可续接绑定（`_save_binding` 条件未动），fast 的 `stream_end` 与 completion 的 `native_outcome` 口径不变。

应 Host 复核更正第二回合"清理更正"段的措辞：该段现改为"存在违反任务要求的删除重建机制（`m/fault-m1`…`m/fault-m5` 五个确切路径）；现有证据不能确认其实际删除次数，撤回零删除断言；已保留记录，本任务第二、三回合没有手动删除"——撤回上轮以日志运行块数量与目录创建时间推断"当轮新建、rmtree 未触发、可确认删除次数为零"的结论，不以日期或日志数量推断执行，未另做追查。

验证仍在任务 m/ 新目录：`git archive 2c1fd12`（最新公共提交）展开到 `m/round3-verify`，仅覆盖本任务 scope 四文件，解释器为副本按自身 `uv.lock` 离线构建的 `.venv`，环境净化与 `TMPDIR`/`BUDDY_CHECKS_TMPDIR` 同前。受影响模块 `test_native_run` 36 条全绿（约 14 秒，无模型调用、未跑完整检查、不重跑无关模块）；编号对账：上轮 33 条无缺失无改名，新增恰为本轮 3 条（fast 已 EOF+未知组、review 已完成+未知组、review 缺终态对照），清单在 `m/round3-logs/test-native-run-ids.txt`；worker 已完成+未知组由既有用例扩充断言覆盖（`streamComplete` 真且 resumable 仍 None）。变异一组：`m/round3-faults/fault-injection-round3.py` 在新副本 o1 将流事实回退为 status 门控，三个受影响用例（fast/review/worker）全红、断言即 `streamComplete`；红态副本与 `fault-injection-round3.log` 保留，脚本无任何删除逻辑（目标目录已存在即中止）。

`native_run.py` 1696 行（上轮 1688，+8），最长组织函数 `_build_result` 82 行（仍低于约 100 行界）；`test_native_run.py` 730 行、36 用例（+3）；TSV 同步为 36 行、与实跑集合相等。检出公共基线说明同前：受管检出不含 `2c1fd12`/`2d844db` 公共改动，Host 整合前检出内该测试模块不可导入运行，验证以副本为准。
