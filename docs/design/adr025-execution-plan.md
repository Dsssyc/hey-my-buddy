# ADR-025 执行计划

状态：Claude Code Host 已审阅通过，用户于 2026-10-03 转达并补充执行决定。本分支 `socu/adr025-run-module` 从 `2bdb497` 建立，现已合入 `socu/buddy-core` 的 `dd8a9ab`。本次修订按用户决定提交后直接开始第零步、F-D1 接 F-D2、F-C1，无须再审计划；各步的 Host 验收仍是必经暂停点。未经核对的原生能力仍明确标为待核对。

宏任务是完成 [ADR-025](../decisions/025-harness-run-module.md)。步骤编号仍为第零步目录、第一步公共格式与外层、第二步 ZCode、第三步 Codex 与 Claude Code、第四步 DSH、第五步 C-Two 实时通道。依赖只有：第零步先完成并验收，第一步随后完成并验收，第五步切换等待所有 harness 抽取完成并验收。第一步之后四个 harness 在各自 worktree 并行；DSH ACP 客户端、C-Two 后端等不碰公共文件的新代码可更早独立开始。现在并行启动第零步、F-D1 接 F-D2、F-C1。每条线完成后提交记录和完整检查结果、停下等 Host 验收，互不依赖的线可以继续。

## 1. 范围与当前源码复核

已读本分支的 `AGENTS.md`、`CONTEXT.md`、ADR-023、ADR-025、当前架构、ADR-007，以及四份 `harness-run-inventory-*.md`。四份清单是第二阶段合入前的事实；本计划按 `2bdb497` 的实际文件、调用点及与清单基线 `26f9dd0` 的差异复核，旧清单和历史文档原样保留。下表的定位均为本基线的仓库相对路径，实施后仍能通过 Git 查看该版本。

| 旧清单中的描述 | 当前源码事实与定位 | 对实施和验收的影响 |
| --- | --- | --- |
| 统一工具词汇与黑板判定尚未实现 | `src/buddy/tool_evidence.py` 已有 ACP 分类、完整性与调用配对；`src/buddy/decision.py` 的 `_tool_evidence_problem` 调用 `judge_tool_evidence`，并核对冻结身份及工具数 | 迁移现有事实包和判定位置，不能重建一套 harness 自行发布资格结论；`zeroToolVerified` 不能替代事实包 |
| Codex 审阅依赖版本证书；Claude Code 审阅不会进入决策适配器 | `adapters/base.py:152` 的 `local_read_only_check` 取代证书属性；`catalog.py:269`、`router.py:182`、`adapters/decision.py:50` 使用本地资格；Codex 声明 darwin/linux/win32，Claude Code 声明 darwin/linux | 保留当前免费资格、平台边界与原生策略回读；不恢复付费证书，也不要求审阅比 Worker 多做厂商证明 |
| DSH、ZCode 审阅只是没有入口 | 两者的 `local_read_only_check` 明报 `readonly-worker-carrier-unimplemented`；`read_only.start` 拒绝两者；ZCode runner 明确拒绝 `readOnlyRequest`，DSH Python runner 只实现 no-tool；专用审阅载体、`review_check.py`、安装包静态证明及证书/probe 系列已删除 | 后续沿现有 Worker 载体抽取，不能复活删除的审阅程序。ZCode 本次保持审阅不可用；DSH 如要新增审阅能力，列入第四步差异确认 |
| 快速路由控制没有黑板执行身份 | `adapters/read_only.py:181` 已写 `taskId/attemptId/generation`，用于冻结工具事实的绑定 | 公共请求必须携带这组身份；新的 `invocationId` 只区分一次运行，不能替换 attempt 身份 |
| 快速收集器核对 `zeroToolVerified` 和零工具数 | `adapters/read_only.py:249` 已移除这一判定；决策适配器透传 `toolEvidence`，黑板按统一规则发布 | 公共收集器只读事实和停止证据，不能把旧判定搬回运行层 |
| Codex 的观察器只有模式内的计数 | 新增 `codex_tool_evidence.py`；快速和审阅共用投影器；观察先于模式过滤；快速纠正的每个根 turn 都登记，EOF 前的晚到事实仍保留；审阅保留完成根 turn 的流语义 | 迁移投影先后顺序、所有根身份、去重、晚到事件和失败路径中的事实；不把审阅的流结束口径擅自改成快速的 EOF 口径 |
| Claude Code 审阅只使用 `TurnEvidence.tool_calls` | 新增 `claude_tool_evidence.py`；审阅预算使用统一计数，流关闭后生成事实包，异常路径仍报告不完整包；`StructuredOutput` 的 `tool_use` 目前会被归为 `other` | 第三步修正原生最终值交付机制的分类，同时覆盖计数、预算与配对；其他 MCP/未知工具继续保留，不能按字符串名字全面豁免 |
| ZCode 快速只报告工具数 0 或失败时 1 | 新增 `zcode_tool_evidence.py`，汇总 canonical 与生命周期投影；观察先于拒绝，跨纠正会话累计；`_drain_structured` 在失败后继续读取 EOF；原生非零退出也失败 | 必须保留 scheduled/result 配对、元数据缺字段、子调用、纠正累计、close ack 与 EOF 的回归；不把所有事件强行改成同一生命周期 |
| DSH 快速只报告 direct-LLM 是否给了工具 | `dsh_runner.py` 与 `no-tool-structured.mjs` 已产出带绑定的规范化工具事实、调用身份和完整性；仍用 direct-LLM；Worker 仍是 Node `run.mjs` 加插件 | 第一步纳入现有公共外层但保留两种原生实现；第四步统一 ACP 时逐项迁移第二阶段增加的事实包测试 |
| Router 选择与健康只围绕旧的单项配置 | 已新增 `router_sequence.py`、`router_history.py`、`router_settings.py`、`router_boundary*.py`、`router_failover.py`；Worker 保留真实停止后的重试/回执边界 | 文件表包含这些新文件；公共格式投影不能改变有序 Router、单候选跳过模型、容量、健康、候选与输入变化判定 |
| 问询与活动仍靠文件、套接字 | DSH 的 Node 问询桥和 ZCode 控制器桥仍存在；服务在 `inquiry.py` 直接连接；Worker `_Renewal` 每 2 秒读取活动，租约续期至少相隔 5 秒 | 第一至四步只统一访问接口；第五步取消服务直连与活动文件转报。现有取消轮询、租约和持久回执不能被实时通道代替 |

现有平台声明与本机实测是两项事实。清单中 Codex 的模式差异仍成立：Worker 不做 Router 那样的实际配置回读；请求模型与目录成员资格不能写成实际服务模型已核对。Claude Code 仍无快速入口、无原生续接、无问询；ZCode 仍保留会话私有的签名完成/问询机制；DSH Worker 仍不能提供与快速相同的配置核对强度。原生状态、额度、用量或事件完整性缺失时保留未知，不补零、不补成功。

只有 DSH 重做、ADR-025 第 8 条的停止口径、第三步明定的 `StructuredOutput` 修正和第五步实时通道可改变相关行为。其他部分保持原有成功/失败、提示词、预算、续接、资格、工具拒绝、凭据清理与结果留存行为。发现顺手可修的问题先记录；超出本计划的改动或可行性前提失败时停止说明。

## 2. 第零步的文件搬动与路径规则

[逐文件搬动表](adr025-step0-file-map.tsv) 是第零步的完整输入，按合入 `dd8a9ab` 后的 `git ls-files` 列出全部 635 个文件（原基线 633 个，加本计划及对照表），每个文件恰好一行、一个目的地，没有重复目的地。表中 114 个源码/打包资产文件、178 个 Python 测试及 fixture 文件要 `git mv`，共 292 个；其中 Python 测试模块为 158 个。`tests/python/support.py` 和 `mock_workspace.py` 是共用测试设施，原位保留；根包基础模块对应的 `test_locking.py` 和 `test_private_directories.py` 也留在测试根目录。表中的原位文件也逐个列出，包括 DSH 的 23 个文件、控制台源码、文档、CI 与启动脚本。

| 当前位置或职责 | 第零步目的地 | 选择理由 |
| --- | --- | --- |
| `src/buddy/__init__.py` | `src/hey_my_buddy/__init__.py` | 根包使用项目名，发布名、版本、`buddy` 命令与 `BUDDY_*` 不变 |
| DB、store、迁移、离线 board 准备、backup、private migration | `blackboard/store/` | 持有数据库或核对数据库事实；整文件搬动 |
| workflow、宏任务、检出、问询导入、验收、存储回收、worker sessions、调度 | `blackboard/tasks/` | 黑板机械规则与权威读取；`workflow.py` 等大文件不拆 |
| decision、Router 边界、列表、健康记录、输入发布和偏好规则 | `blackboard/routing/` | 黑板持有路由事实与发布权 |
| catalog、catalog_store、accounts、account_keystore、account_operations | `blackboard/catalog/` | 模型目录与账户归在一起；对应账户/目录测试同迁，服务持有账户进程的职责不变 |
| evaluation、model facts、native observations、user policy | `blackboard/evaluation/` | 评价、模型事实与偏好 |
| service、daemon、harness health | `blackboard/service/` | 保留现有服务生命周期与句柄归属 |
| `worker/`、command、Windows 子进程、partial outputs | `buddy/runtime/` | 保留 Worker 运行时的认领、进程对象、截止时间和持久回执 |
| `adapters/decision.py`、`turn_io.py`、`read_only.py`、`router_input.py` | `buddy/roles/router.py`、`turn_io.py`、`structured_call.py`、`router_input.py` | 按角色整理现有任务书、回合文件、冻结副本与旧 Router 公共启动收集；旧公共通道等所有 harness 抽完后删除 |
| 每个 harness 的 adapter/config/protocol/runner/工具事实文件 | `buddy/harnesses/<harness>/`，主 adapter 文件名为 `adapter.py` | 第零步只按表改路径，内部函数和类名不改 |
| adapter 注册表、base、native observations、harness discovery/runtime selection | `buddy/harnesses/` | 共用原生运行机制，之后的公共模块直接在这里形成 |
| `account_native.py`、`account_integrations.py`、`codex_account_probe.py` | 共用 harness 目录及 `codex/account_probe.py` | 原生账户驱动归原生机制一侧；调用方仍是原服务对象，账户进程不交给 Worker，不并入模型运行接口 |
| contracts、schemas、client、transport、rpc config、activity、usage、billing、跨边界证据格式 | `protocol/` | 只放两侧之间的操作、传输、客户端和跨边界事实格式 |
| errors、home、locking、private_dirs | 根包下的同名文件 | 基础模块不属于协议；对应测试留在测试根目录 |
| yaml_bridge.py | `buddy/harnesses/dsh/yaml_bridge.py` | 仅供 DSH Node 运行器/profile 安装使用，第四步随旧集成删除 |
| launcher、runtime、package/skill install、skill package、upgrade | `install/` | 显式安装及运行时材料，不改变安装/升级算法 |
| CLI、help/views、blocking、console CLI、checks | `cli/`；CLI 文件名为 `main.py` | 入口与检查定位集中；无旧包重导出 |
| console server、sessions、已有构建资产 | `console/server.py`、`console_sessions.py`、`console/assets/` | 控制台只作构建输出和取资源路径适配 |
| `harnesses/dsh/` | 原位 | 第四步核对通过后直接删除，避免搬两次 |

表中的目录均相对 `src/hey_my_buddy/`。测试按表进入 `tests/python/blackboard/`、`buddy/`、`protocol/`、`console/`、`install/`、`cli/` 的对应目录，单个交叉测试文件仍整体保留。为源包和被 unittest 递归发现的测试目录补空 `__init__.py`；不增加转发 import、旧模块别名或兼容包。fixture 根据使用的 harness 或权威数据归属搬动，并逐个更新所有调用点。

第零步登记 protocol→任一侧、buddy→黑板、黑板→buddy 的实际跨边界导入，含文件、行号与目标；只登记，不修。第零步的整文件规则优先于彻底消除历史依赖。例如 `db.py` 同时定义存储和 JSON/时间小函数，`tool_evidence.py` 同时有事实收集和纯判定，`router.py` 同时有持久规则和 prompt；本步只按其持久职责或共用接口归属放置，跨包 import 按真实目的地改写，不拆成新实现。`protocol/tool_evidence.py` 的纯判定仍只由黑板发布路径调用。后续只抽 ADR-025 需要的角色/运行接缝；不趁机整顿全黑板。安装和 CLI 入口原有的离线数据库读取保留在其原调用路径，目录调整不授予新的状态所有权。

路径替换按完整 Python 模块名和表中完整文件路径进行，不能全局替换单词 `buddy`。覆盖相对 import、测试间 import/patch 目标、`python -m`、源码路径断言、`Path(__file__).parents[...]`、CLI help 的 AST 来源定位、runtime manifest 与资源键、Hatch 的 contracts/build-info 定位、wheel/sdist、launcher shell/PowerShell、CI 检查命令和 Vite 输出目录。保留现有资源键名与 CLI 输入/输出；`yaml.bridge` 更新到新文件，`console.assets` 更新到新资产目录。预计入口如下：

```text
完整检查：uv run --frozen python -m hey_my_buddy.cli.checks
公开 CLI 的内部模块：hey_my_buddy.cli.main
安装 console script：hey_my_buddy.install.package_install:main
控制器：hey_my_buddy.buddy.harnesses.<harness>.runner
Worker supervisor：hey_my_buddy.buddy.runtime.supervisor
Vite 输出：../../src/hey_my_buddy/console/assets
```

`AGENTS.md`、两个 README 和现行参考文档只更新路径与命令。ADR、`SKILL.md`、Host 指南、已有验收记录、历史设计与四份旧清单不改。打包时引用这些文件的定位可以机械更新，其文本不重写。变更控制台源码只限维持现有检查所需的路径定位；资产用 `git mv`，若一次必要重建产生内容变化，必须说明构建原因和最小 diff。不在本步调整 schema、contract/version、依赖版本或用户入口。

## 3. 目标接口与所有权

目标调用链为 `Worker 运行时 → 角色控制器 → Worker/Router 角色模块 → HarnessRun.run → 原生 harness`。Worker 运行时继续认领 attempt、持有外层控制器句柄、监督截止时间并提交回执；控制器持有原生进程对象和原生组/Job。角色与 harness 代码可以在同一个控制器进程中执行，按 Python 模块分开职责，不新增角色专用 IPC。黑板继续独占 SQLite、资格/路由边界及结果发布。command adapter 保持独立的确定性 argv 执行，外部 Worker client 不改语义。

每个 harness 只实现 `run(request, *, observer, services, cancelled) -> RunResult` 和不送 prompt 的 `discover(...) -> CatalogFacts`。外层提供统一 `launch_controller`、`collect_controller` 与 `ProcessHandle`。所有已抽出的角色调用同一个角色控制器入口；harness 的 `runner.py` 成为原生驱动，不保留独立的 Worker/fast/review CLI 分支。第一步只合并外层，尚未抽出的 harness 继续使用其现有原生 runner；对应 harness 抽完的那一步立即删除旧的两个 structured 启动入口和模式分支。全部抽完删除旧 Router 公共启动/收集通道及两个旧请求类型，保留必要的纯 schema/prompt 小函数到有明确归属的模块。

注册表对已抽出的 harness 使用公共运行接口，不继续继承带旧 structured 方法的 Adapter 基类；未抽出的 harness 仍只用其当前入口，过渡选择集中在唯一注册/调用点，随最后一个 harness 抽完删除。验收用“旧方法不存在且角色只有一个运行调用点”检查删除，不能以返回 unsupported 或加一层转发来替代删除。最终 command/外部 Worker 的基类也不再携带 harness structured 启动方法。

角色控制器的私有输入包含 RoleInvocation，角色在其中消费任务书、黑板凭据、turn 输入、检出与服务绑定，再形成下述 RunRequest。RoleInvocation 不进入 harness 接口，不进入模型 prompt。`observer` 是角色拥有的进程内回调，接收已经归一并留存的运行事实，返回继续或请求中断；格式纠正、未知事件是否拒绝、累计工具预算、被拒原生交互后的 attention 转换均由角色代码决定。运行模块只执行回调要求的原生 interrupt，并在结果中记下请求与回执。这样保留快速遇未知事件立即结束、Worker 忽略未知事件等当前顺序，不把角色判定藏在运行模块，也不等到最终收集才丢失现有及时拒绝行为。

### 3.1 公共运行请求

Python 用冻结的 dataclass 表达值；跨控制器的内部 JSON 用 `formatVersion: 1`、固定键集和现有有界 JSON 规则。以下字段是当前公共接口草案，不暴露公开 CLI；序列化在外层边界验证一次。后续步骤可根据实际接口缺口调整字段，由 Host 统一修改公共文件并在该步记录写明改变及理由；并行微任务不得自行修改公共值。旧控制文件的读取上限、严格 JSON 与普通 JSON 的已有差异先保留，在该 harness 微任务迁移并证明等价；不借统一格式收紧或放宽角色输入。

| 字段 | 类型与含义 |
| --- | --- |
| `formatVersion` | 精确整数 `1`；内部格式版本，不改变黑板的公开 contract |
| `identity` | `{taskId: str, attemptId: str, generation: int, invocationId: str, turnId: str|null, inputSha256: str|null}`；前三项保持已冻结值，invocationId 由可信外层生成；没有 Worker 回合时后两项为空 |
| `harness` | `codex/claude/zcode/dsh`；选择模块，不携带角色名或 routingMode |
| `configuration` | `{provider: str, model: str, effort: str}`；不自动填默认 buddy，不回退其他账户/配置 |
| `frozenAccount` | 冻结账户的非秘密引用，保留 `adapter/source/revision/credentialRevision/identity` 及原生账户位置引用；缺项按当前 harness 规则处理；不含密钥、token 或文件内容 |
| `cwd` | 已由角色核对的真实绝对目录；运行模块只负责原生回读与绑定，不创建/封存检出 |
| `privateState` | `{invocationRoot, nativeRoot}`；进程/日志临时材料和原生会话的私有目录，保持 attempt 与微任务 native 根的现有生命周期差别 |
| `inputText` | 一段角色已组装的 UTF-8 文本；不带任务书对象、协助规则或黑板授权对象 |
| `toolScope` | `none/read/write`，对应“不给工具”“只读”“可写”；原生机制由 harness 决定 |
| `network` | `{requested: bool, allowedDomains: list[str]|null}`；`false` 不禁止模型连接；Worker 的现有注册表域名与 Router 禁网差异可准确表达，不引入新网络能力 |
| `outputSchema` | 给最终值的原生 schema；Worker 提供当前 outcome schema，Router 提供冻结答案 schema；harness 选择原生结构化输出、私有完成工具或最终消息 |
| `budget` | `{timeoutSeconds, toolCalls: int|null, bytesRead: int|null, maxOutputBytes: int}`；0 的无期限语义、Worker 0/fast 60/review 预算和外层宽限仍由原路径投影保留；未知已读字节不估计 |
| `continuation` | `null` 或 `{mode, previousSessionId, bindingRef, previousNativeTurnId, previousAttemptId, previousInputSha256}`；字段按已有绑定提供，无证据不启用 native resume |
| `sessionServices` | 可选会话内服务的描述列表 `{serviceId, kind, toolNames, inputSchema, outputSchema, deliveryMode}`；实例由角色持有，通过 `services` 参数提供局部调用能力，不把黑板凭据序列化进请求 |
| `captureEvidence` | 布尔；保留当前证据捕获开关及“快速总要保留冻结请求/结果”的既有行为 |

`services` 只能提供本次完成、问询或检查点的窄接口，harness 看不到 BoardClient 或数据库连接。完成工具的校验/拒绝及 inquiry-pending/attention-outstanding 规则由 Worker 角色服务承担；harness 驱动负责从本次原生工具事件与签名回执核对它确实被调用并完成。Router 不获得 Worker 完成服务或 agent authority。冻结账户引用只供原生认证准备，继续由原生 harness 管理认证；本次执行者不打开用户凭据文件。

### 3.2 公共运行结果

`RunResult` 表达一次原生运行的事实；`ok` 只表示原生交互完成，不表示 Worker 成功、Router 答案可发布或 Host 已验收。字段可为空时用 `null/unknown`，没有有效事件根不编造原生身份。外层收集后补充外层停止事实，再交角色投影为当前 AdapterOutcome/黑板结果，因此不扩大公开输出。

| 字段 | 类型与含义 |
| --- | --- |
| `formatVersion`, `identity`, `harness`, `harnessVersion` | 内部版本与请求绑定；版本未知保留 `unknown` |
| `end` | `{status: ok/error/cancelled, reasonCode: str|null, nativeExitCode: int|null, signal: str|null}`；原生/协议结束事实与原生原因，不含角色发布 verdict |
| `modelStarted` | `true/false/null`；保留各 harness 当前的 send/原生 started 证据口径，标明依据，不能从成功启动进程推为已经调用模型 |
| `modelStartEvidence` | `{basis: native-start/input-admitted/input-sent/legacy-report/unknown, nativeIdentity, eventSequence}`；给上述布尔值的来源，不把 send 或目录回读写成实际模型证明 |
| `configuration` | `{requested, checked, observed, checks}`；checked 每个值带 `{value, basis, source, nativeIdentity}`，basis 为目录成员资格、原生会话回读或未知；observed 保留原生实际回报；请求值不能冒充已核对值 |
| `nativeIdentity` | 最终根身份与有序 `rootIdentities`，仅含原生返回的 session/thread/turn/input/call ID；纠正产生的所有根另列，不从工具事件推导根 |
| `value` | `{raw, parsed, schemaStatus, validationBasis, errors, correctionCount, mechanism}`；schemaStatus 为 valid/invalid/unknown；mechanism 为 native-schema/completion-tool/final-message；错误有界，原文仍可留存供角色诊断 |
| `completionEvidence` | `{mechanism, nativeIdentity, callId, eventOrder, streamEnd, receiptRef, receiptVerified, nativeOutcome}`；保留原生成功/失败、调用关联、flush/close/EOF 等真实依据；不同原生证据不能被一个宽松布尔取代 |
| `toolEvidence` | 现有 version 1 事实包：`binding/nativeIdentity/streamComplete/events/toolCalls/unsettledToolCalls/truncated`；ACP 类别与 start/end 不变；没有证据为 null，未识别工具保持 other |
| `deniedInteractions` | 有界数组，保存原生 request ID、method、原生身份、拒绝动作及原因；不保存秘密或工具参数/输出 |
| `unknownEvents` | `{countsByType, total, truncated}`；原生合法但未归类的事件计数；malformed JSON/协议仍是原生失败事实，是否忽略未知类型由角色决定 |
| `effectivePolicy` | 按 tools/filesystem/network 分别给出 `{requested, enforcement: native/unrestricted/unknown, reported, basis, limitations}`；只有请求无回读时如实标明，不能宣称本机强制已经验证 |
| `activity`, `usage`, `quota`, `nativeFailure`, `lastAssistantMessage` | 沿用现有正规化的活动、用量、额度、白名单失败归因和有界末条文本；完整性、覆盖范围、来源、时间、去重和未知计数不改变 |
| `continuation` | 原生会话位置引用、绑定、checkpoint、是否存在可续接的证据；由角色判断是否采用，不单凭 capability 或 sessionId 声明可续接 |
| `stopEvidence` | `{native, controller, interrupt}`；native/controller 各含 started、leaderExited、groupState: gone/alive/unknown、exitCode、observationBasis；interrupt 区分请求与应答；双层 gone 或可信未 spawn 才可确认停止 |
| `evidenceRefs` | 本次日志、请求/结果、回合/完成来源等私有文件的绑定、大小、摘要与留存情况；角色决定哪些可进入普通 artifacts |

schemaStatus 记录该路径实际执行的检查及依据，不新增一套能改变已有允许集合的“统一严校验”。Codex/Claude 原生 Worker schema 与本地 outcome validator 的不同允许集合保留；Router 使用的 schema 子集、enum 不纠正、最多一次格式纠正，Claude Code 不纠正，都在角色层保持。Worker 继续校验六个 outcome 字段、来源关联、检出与 seal；Router 继续核对候选、引用、预算、冻结副本与黑板发布规则。`resolved` 等旧公开字段由角色按原语义投影，内部 checked 的含义不会让旧结果看起来有更强证明。

停止证据要求内层原生与外层控制器都被确认消失。unknown 在所有安全判定中按仍存活处理；PID 丢失、EOF、native interrupt ack、实时端点不可达、租约过期或控制器退出都不能单独证明原生组停止。未启动必须有持有进程对象的一侧确认 spawn 未发生，不能仅看缺一个文件。第零步不改 DSH Node 的历史例外；第一步保留其原始观察并注明依据，第四步删除 Node 运行器时消除例外。若要提前改变这个例外，先更新执行边界并等验收，不偷偷混进搬动。

## 4. 三种工具范围与现有行为

“工具范围”指任务工具；原生结构化输出和确实用于本次最终值的交付机制单列 completionEvidence。ZCode 快速沿用最后消息，DSH 重做后的完成 MCP 工具由确认过的本次局部服务提供。普通 MCP、子代理和仅名字相似的工具不因名字而获豁免。network 的生效策略另外记录，不把关搜索写成整个进程禁网。

| harness | 不给工具 `none` | 只读 `read` | 可写 `write` |
| --- | --- | --- | --- |
| Codex | 保留私有 no-tool config、模型工具字段、空 dynamicTools/environments、native config 层回读及快速的 EOF 完整性；不能静态扫描厂商实现证明它正确 | 保留原生 buddy-router 文件权限图、never 审批、禁网、原生工具及策略回读；可接受 execute 的判定仍由黑板根据系统沙盒事实决定 | 保留 Worker workspace-write、授权 cwd 为 writableRoots、never 审批、当前 networkAccess=false；不新增强回读或额外工具限制 |
| Claude Code | 当前无可达实现，能力仍为 unavailable，调用前按当前规则拒绝；本次不新增快速路由或试用另一条 CLI 通道 | 复用当前 `execution_args`，Glob/Grep/LS/Read、原生拒绝表、default 权限和原生沙盒；Router 网络白名单为空；Worker 只读保留其注册表白名单差异 | 保留现有 WRITABLE_TOOLS、acceptEdits、stdio 权限请求一律 deny、受限沙盒与包注册表白名单；不改成 bypassPermissions |
| ZCode | 复用 app-server、空 toolAllowlist/MCP、关闭 offPeak/dynamicWorkflow、no-tools runtime preferences；保留快速未知事件拒绝和 EOF/close 证据 | 当前 Worker 读范围没有原生只读强制；驱动如实报告 unrestricted，不能由工作区核对或 yolo 名字得出只读结论；Router 审阅继续 unavailable | 保留同一个 app-server 的 yolo 默认工具与会话私有完成/问询 MCP；原生权限 deny、用户交互 decline；源 provider 偏好不回写 |
| DSH | 第四步前保持当前 direct-LLM；重做后使用 ACP 代理会话，检查是否能原生关闭任务工具，否则报告 unrestricted，Router 按零工具证据判定；不再直接调模型 | ACP 是否能拒绝写/命令/联网必须逐项核对；未覆盖的范围报告 unrestricted；只有核对证据和上述授权范围支持时才声明相应能力 | ACP 运行原生代理与工具；controller 回答权限请求的策略按核对结论及已授权差异固定；不为了获得权限另建任务工具或外层沙盒 |

上述 Codex 机制中的原生实验开关是现有事实，重构保持当前调用；不为新能力新增实验接口依赖。资格仅检查本地公开接口、设置/版本能力和本项目所需资源是否存在，不解析厂商安装包来证明限制；原生有效策略与事件来自本次运行。没有现成能力的 harness 如实报告未限制或不可用，角色保持当前资格判定；统一接口本身不扩展 ZCode 审阅或 Claude Code 快速能力。平台未核对的事实写进验收记录，当前方案不引入系统级统一沙盒。

角色的检出 `access: read` 与原生工具请求分别记录：当前 Codex Worker 总请求 workspace-write，ZCode Worker 总请求默认 yolo；角色层的读清单核对/封存不等于原生只读。迁移它们的 Worker 时 RunRequest 继续表达原有可写原生请求，角色的检出 read 授权与结束核对仍保留；不能因为两个字段都叫 read 就自动收紧原生行为。Claude Code Worker 才按现有 access 映射 read/write。completionEvidence 的分离也不顺手重算已有 Worker 活动计数，除明定 StructuredOutput 修正和经确认的 DSH 差异外，角色投影仍与当前统计一致。

## 5. 四个 harness 的公共实时接口

第一步确定 `LiveChannel`，第二至四步把各 harness 的原生支持接到同一接口；第五步只换传输。身份和限额属于公共接口，实际问询送达时机属于 harness 的能力事实。unsupported 必须返回明确状态；不能为了四栏整齐注入新原生 turn。

```python
class LiveChannel(Protocol):
    def capabilities(self) -> LiveCapabilities: ...
    def request(self, request: LiveRequest, *, timeout_ms: int) -> LiveReply: ...
    def observe(self, *, after_seq: int | None, limit: int,
                timeout_ms: int) -> LiveSnapshot: ...
    def close(self, *, reason: str) -> None: ...
```

`LiveCapabilities` 分别声明 `activity`、`inquiryDelivery: realtime/cooperative-checkpoint/unsupported`、`finishNotice`、`sessionContent`。Codex 与 Claude Code 当前只接活动，问询返回 unsupported；ZCode 保留 cooperative-checkpoint；DSH 在第四步前保留当前实时送达，ACP 后以核对结果替换。ADR-022 的提醒收尾和 ADR-024 的会话视图只预留消息种类与能力位，本任务不实现其业务、采集隐藏推理或扩大实时内容。

`LiveRequest` 为 `{formatVersion, identity, requestId, kind, payload}`，kind 为 inquiry/finish-notice；payload 的身份、问题/提醒字段使用闭集。`LiveReply` 为 `{identity, requestId, status, deliveryMode, nativeCorrelation, reasonCode}`，status 区分 queued/delivered/answered/unavailable/unsupported；queued 或插入缓冲不能升级成 delivered。`LiveSnapshot` 为 `{identity, sequence, activity, inquiries, events, unavailable, truncated}`，活动沿用现有闭集；会话事件仅承载上游已允许的事实类型。对同 requestId 同 payload 重放不重复送达，换 payload 为 conflict；传输认证材料由运行时的私有绑定持有，不进入模型或公共记录。

复用现有问询限额：问题/回答各最多 4,000 UTF-8 字节、每运行 32 个问询、transport timeout 100–5,000 ms、wait 最大 30,000 ms；跨运行帧建议上限 64 KiB，以容纳既有有界观察。超过上限显式拒绝/截断并报告，不能静默丢弃必需事实。原始 socket 兼容实现仍按各自原有的更小帧上限工作，第五步不借换传输扩大公开问询限额。活动的同阶段合并与单调去重保持当前规则。

第一至四步的实现使用 `ExistingLiveChannel`：读现有活动文件、调用现有问询桥、核对已有持久 journal；原生更新通过同一个适配面交给此接口。旧实现只作为该接口的唯一后端，不加角色专用桥、不保留一份新旧平行转报。必要的签名完成/问询回执、结果文件、journal 与停止证据仍落盘；断开 live channel 不改变原生 deadline 或结果归属。

## 6. DSH 的核对门槛与差异确认

用户已一并授权本计划中的原生核对、各步真实冒烟及为同一目的必需的重跑，不再逐次询问；每次保持最小，在验收记录列 harness 与运行次数。额度或登录导致不能运行时，记未验证并继续其他工作，不反复尝试、不改登录；计划外的付费运行仍先问。DSH 首选 Python 控制器驱动本机已安装命令的 `--profile acp`，不用 `deepseek-harness-sdk`，不下载内置 DSH、不寻找本项目用的 Node、不读凭据内容、不更改日常设置或目录。可行性核对在计划通过后作为 F-D1、F-D2 两个微任务进行；握手不得发送 prompt 或触发模型。空会话、私有存储覆盖和配置改变均限定在核对目录，若安装的接口不能保证隔离，停止该操作并记录未验证。

| 第 11 条问题 | F-D1 不调用模型能取得的证据 | F-D2 已授权最小真实运行必须取得的证据与失败条件 |
| --- | --- | --- |
| 哪些工具发权限请求，能否拒写/命令/联网 | 初始化的能力、公开权限选项、会话工具/模式声明及本次 client 应答接口；未出现实际调用的工具覆盖仍标待验证 | 在隔离 fixture 中请求代表性的写入、命令、联网；逐条关联 permission request、deny、工具结束与受控 sentinel/本机服务结果；没有权限请求或没有可核对事件不宣称被阻止 |
| 用量能取得多少 | 公开协议的 usage/update 字段及能力声明，逐个注明本地握手是否实际回报 | 收到输入/输出/cache/total 等实际字段、作用域及累计/增量依据；缺项为空，纠正累计不重复计数；不得用模型自述补齐 |
| 会话存储能否指向私有目录 | 用公开配置覆盖创建/关闭空会话，核对私有存储位置和日常目录未写入；不得靠复制用户账户目录实现 | 模型会话只在核对根产生记录，读取/恢复仅限该 session；任何写入日常存储即失败，不能先做再清理掩盖 |
| 模型与推理强度选择、回报是否可靠 | 枚举会话配置，选择已声明项并回读；区分目录项与实际生效回报；设置不持久化到用户偏好 | 本次已选择的 provider/model/effort 与原生会话/turn 回报可关联；仅回显请求不算实际回读，按 checked/observed 分开保留 |
| Python MCP 完成工具是否可行 | 向空会话挂本次临时 Python MCP，观察工具发现/能力声明和安全关闭；不得调用模型 | 原生根实际调用，参数经本次 schema 校验，回执和 native call/结果/结束事件有真实 ID、顺序及关联；普通末条文本不能伪装完成回执 |
| 问询只能检查点还是实时送达 | 公开 prompt/steer/cancel 与 client 回调接口；未运行时无法证明实时插入，标待验证 | 运行中送一个 inquiryId，观察在原 turn 实时或下一检查点签收、回答工具相关事件，原工作继续且 deadline 不变；只存在 API 名不等于实时可用 |

F-D2 在脚本、配置、私有任务和停止标准明确后直接运行，授权来自用户本次决定。一次运行不能覆盖所有问题则记录部分/未知；为同一核对目的必需的最小重跑已有授权，逐次登记原因和次数，不扩大为计划外实验。因额度或登录失败时不反复尝试，记未验证并继续。记录只保存筛选后的公开事实，原始日志放忽略的 `tmp/`，位置使用 `~` 或 `<probe-root>`。

ACP 某项失败时，先只核对同一已安装 DSH 的 `--profile sdk` 公开接口能否补上，并登记 ACP 的失败项、SDK 能力与局限；不用 Python SDK 包。仍失败时，按 ADR 的“Python 控制器加最少的进程内插件”列出确切缺口和所需最小插件。此退路与 Python-only 目标及目录删除会有差异，必须停止实施，把修改后的边界交用户/Claude Code Host 确认；不自行写新 JavaScript 或把未核对项视为通过。

第四步记录逐项列出下表的实际差异。ADR-025“影响”已写明的三项——问询可能只能在检查点送达、快速路由加入 DSH 系统提示词、原生续接可能成为新能力——按计划进行，无须再次确认。表中的其他项是核对事项，不代表用户同意扩大行为；若发现超出这三项及已明确批准的 DSH 重做/停止口径的行为差异，先停下说明。

| 行为 | 当前基线 | 拟议 ACP 行为与差异记录边界 |
| --- | --- | --- |
| Worker 载体 | headless + Node 运行器/进程内插件 | ACP 代理会话 + Python 控制器/Python MCP；完成/会话事件证明转换，仍保留 six-field outcome、身份与 seal |
| 快速路由 | direct-LLM，不创建 Agent/Session，单条 user 消息 | 经代理运行时，有 DSH 自己的系统提示词、会话和原生开销；响应/用量可能不同；不向原生 prompt 加第二套本项目角色规则 |
| 最终值机制 | Worker 完成插件 + awaited flush；fast 末条 stream 文本 | Worker Python MCP 签收并关联 ACP root/end；fast 选择经核对的完成机制，若 MCP 是必需则作为最终值交付服务，不开放任务工具 |
| 工具与权限 | Worker 默认原生工具；fast tools=[]；review unavailable | ACP 原生工具开关/权限应答覆盖以六问结果为准；不能强制的记 unrestricted；增加 review 资格须另有逐项确认 |
| 模型/推理配置 | Worker 报 requested；fast 严格核对 prepareCall；有硬编码 Node 默认 | 统一冻结配置和会话回读，拒绝静默改用另一个 buddy；若改变既有配置失败语义且超出已批准差异，先停下说明 |
| 问询 | 原生 agent.steer 可实时插入，reply 工具相关联 | ACP 可能只能 cooperative-checkpoint；按实际送达模式和签收/未答问题收尾逐项记录，不能模拟另一条运行通道 |
| 会话私有存储 | attempt 私有 JSONL；Python 快速配置了 JSONL 但没有 Session | ACP 私有会话存储；需要证明配置覆盖确实生效；原生 user store 不能作为替代 |
| 续接 | reconstructed-new-session，不 native resume | 依六问事实实现或记录 ACP load/resume 能力；原生续接成为新能力已在授权范围，仍要求私有状态、来源绑定与停止证据，不扩大历史/账户迁移 |
| 取消与停止 | Node 对未知组观察异常可能当作 gone | Python 统一 conservative 双组观察；ACP cancel ack 只是中断事实；权限/未知 OS 错误均不报 stopped |
| 用量与额度 | Worker 与 fast 不同，fast 两次纠正未累计；部分缺失 | ACP 原生语义字段、跨纠正运行的实际累计及诚实完整性；额度未提供仍为空；每个字段差异需登记 |
| 发现与依赖 | Node catalog helper，项目维护 DSH profile/plugin 资源 | 无 prompt 的 ACP/SDK 元数据操作；删除项目的 runner、插件、目录脚本和 Node 测试/fixture；原生 DSH 的 Node 由 DSH 自己负责 |

## 7. C-Two 本机核对与 Worker 转达选择

选择 Worker 运行时自己注册临时 C-Two 端点接收服务的活请求。当前 lease renewal 至少 5 秒、取消读取间隔 2 秒，而问询传输默认超时为 1,500 ms；把问询塞进这些往返会改变延迟与 wait 行为，并把 live 交互与租约绑在一起。因此 lease/claim/renew 保持当前职责，服务通过 Worker 的独立具名操作转达。所有角色与 harness 共用这一组运行通道。

F-C1 现在与第零步、DSH 核对并行：在独立私有根，用项目锁定的 C-Two 0.6.0 公开 API 核对每次运行一个临时端点的生命周期、注册重名、异常退出与端点不可达、再次创建，以及 `set_server`/`set_client` 设置是否进程全局和对多个端点的影响。用户作为 C-Two 作者已确认一个进程可以 `cc.register` 自己为服务并用 `cc.connect` 连接别的服务，这一点作为输入事实，不重复证明。只用合成事实，不启动 harness 或模型，不审阅 C-Two 内部去证明能力。关键项失败且需要改 ADR 时停止报告，不安装新版或改回自制 socket。

控制器每次启动选随机人名作为 C-Two resource 名，使用短的进程私有地址，重名注册失败换另一个人名；名字不含 task ID，不充当身份。Worker 端点每个持有进程一个，按该 Worker 当前内存句柄表转发，控制器端点每次运行一个。端点描述 `{address, name, instanceId}` 在私有启动/ready 材料里发布；认证 token 只存运行时私有绑定，另加完整执行身份与新 instanceId，使用 0600、无链接的发布方式。退出只关闭自己持有的资源和原生句柄，不删除别人的端点。

新增内部具名操作 `worker_live_attach`/`worker_live_detach`，以现有 attempt actor 的 workerId/attemptId/generation/nonce 加 workerInstance 核对当前持有者，在服务内存保存这次运行的 Worker 地址与服务到 Worker 的窄能力。它们不添加公开 CLI 命令、不修改现有 claim/renew 参数、数据库 schema 或历史行。Worker 到 controller 使用另一份仅对本次执行有效的 token；服务拿不到 controller 地址/token，也不能绕过 Worker。服务重启使映射失效，持有句柄的原 Worker 在成功 reconcile/renew 后重新 attach；单靠保存的 PID、地址或 instanceId 不恢复所有权。这两个操作是 C-Two 契约变化；第五步将 `CONTRACT_VERSION` 提高到届时当前契约的下一 minor 版本（当前 0.28.0 对应计划值 0.29.0），所有具名操作/客户端与私有运行时一致切换，不留旧协议兼容层。记录版本前后值、旧客户端/服务拒绝混用、私有打包与 idle cutover 的影响；公开 CLI 参数与黑板 schema 不变，日常安装仍不在授权内。若实施前基线版本改变，以整合记录锁定的新版本值为准。

同一 `WorkerRuntimeLive` 和 `HarnessRunLive` contract 只公开 `request`、`observe`、`capabilities`，使用第五节的固定帧；不提供任意方法名 dispatch、启动其他运行、延长租约或操作别的 attempt。C-Two 请求线程先核对身份并进入有界队列，原生连接仍由控制器的既有 owner loop 操作；不让多个 RPC 线程同时写原生 stdio。Worker 的转达不持有 SQLite 事务或业务锁，活动更新走现有 `worker_progress.data.activity`，不会延长模型 deadline。

服务先提交 question 的权威 message 行，释放事务后调用持有它的 Worker；Worker 转达本次控制器，签收/回答使用现有持久 journal 和相关原生事实，黑板保持幂等导入与第一份关联回答不覆盖的规则。调用失败显示不可达/不可用，保留原 deadline 与尚未完成的事实；observe 可返回持久的最后活动并注明 live 不可达。同步 observe 超时不能触发新模型 turn、改为直连或推断进程已停。

活动通过 controller live snapshot/poll 进入 Worker，再走已有单调 progress 规则；可在控制器保留有界最新活动供恢复读取。最终 RunResult、回合文件、journal、回执、checkpoint 与停止证据继续落盘。第五步完成后删除活动 `activity.json` 的实时转报读写和两套问询 socket 实现、socketPath/Unix 路径预算、专用 token 文件的活通信用途；用于持久回执或旧验收事实的内容保留。提醒收尾/会话视图仅完成公共传输接口，不抢先做 ADR-022/024 的业务。

## 8. 微任务、顺序与验收边界

微任务通过路由提交，任务描述写清必要能力、输入基线、预期文件、结果和检查，不填 adapter/provider/model/effort，也不指定 buddy。第一个微任务携带宏任务标题，后续绑定同一宏任务。先读正在运行黑板的 health 与当前版本的 help/Host 使用指南，仅作能力确认，不升级或重启日常黑板。若日常黑板不能表达计划要求，停止报告，不通过改用户配置解开限制。确定性检查可在微任务内作为后台 command 子步骤运行。

每个表格行是一项独立可验收微任务。验收固定 seal/artifact 和实际 Git diff、测试与证据；模型声称完成、queued、RPC 成功或进程结束都不足以验收。范围内缺陷拒绝并对原 run 使用 `continue`，逐条写出复现、预期与具体文件/证据；不代改、不取消另开。通过后再按固定 artifact 与 commit 关系整合；只有多个交付合并后新增的冲突/问题或原范围之外的问题可由本 Host 修，并登记在该步整合记录。顺序依赖满足前不提交下一项；有多步依赖的微任务不能用一个笼统“完成这一步”替代。

### 第零步：只搬动，全部串行

| 微任务 | 依赖与范围 | 独立交付及验收 |
| --- | --- | --- |
| 0-A 基线与全部测试编号 | 计划获准；旧布局未改；只写证据 | 用私有环境列出真正加载的 Python 每个 `TestCase.id()`、Node 每个测试路径/层级名称、Vitest 每个文件/完整测试名，记录重复/skip/动态与继承用例；运行旧完整检查并记录退出码、组件计数与清理证据。不能用 AST 方法数量代替全部编号 |
| 0-B1 源码与引用路径搬动 | 0-A 验收；独占整个实施 worktree | 按 TSV 用 `git mv` 搬 114 个源码/打包资产文件，补空源包目录、修改全库 import/启动/打包/检查/Vite/help 来源路径；测试文件暂留原位，仅改源码引用。只路径与配置定位，无函数/测试断言语义变化；现有测试完整检查通过后验收 |
| 0-B2 测试目录搬动 | 0-B1 验收；继续独占同一实施 worktree | 按 TSV 用 `git mv` 搬 178 个测试/fixture 文件，补空发现目录，只改测试间 import、patch 与 fixture/源码定位；完成模块映射、集合对应及完整检查。0-B1/0-B2 一起审阅去掉映射后的 diff、资产摘要及保护文档摘要；保持文件整体 |
| 0-C 私有旧布局安装升级 | 0-B2 验收；私有安装/运行时目录 | 使用 `2bdb497` 的固定旧布局 wheel/skill，在 `<upgrade-root>` 中运行旧安装 launcher，再用新 wheel/skill 执行现有 idle upgrade；旧包安装、活动指针、Python/源码/资源定位、备份和新 CLI 全链均有证据，覆盖升级失败回滚。fixture/检查可补到对应 install 测试，不能改升级行为来迁就测试 |
| 0-D 编号对照与完整交付 | 0-C 验收；只补映射/记录 | 再列全部编号，按 TSV 模块前缀改名求双射，Node/Vitest 编号保持；入库模块改名表和脚本的集合/计数对应结果，例外逐条说明；原始编号留 `tmp/`，漏发现、无解释的额外用例、失败导入均阻塞。运行新完整检查、wheel/sdist/skill 与最小无模型启动，记录路径更新范围和私有根完全收尾 |

第零步不并行委派任何搬动或搬动中的修复；0-A 至 0-D 只有一个写入者。旧布局安装的构建输入使用单独固定 checkout/导出的材料，不能为测试改日常安装。新入口必须脱离源码目录仍可运行；合成 command 微任务通过同一黑板路径完成并保留回执，无须调用模型。验证“版本化旧 runtime 包仍保留、新活动指针可启动”与“构建 wheel 文件清单中不存在顶层 `buddy/` 兼容 namespace”是两项独立断言。后一项直接检查 wheel 的 ZIP 文件清单，不用 `import buddy`：`tests/python` 在 PYTHONPATH 上，其测试目录里的 `buddy` 包会令 import 成功。0-D 还列出三类跨边界 import：protocol 导入黑板/buddy 任一侧、buddy 导入黑板、黑板导入 buddy；保留文件/行号/目标模块和类别计数，只登记、不修。

实施前核对到正在运行的黑板 0.27.0 的单份整合凭据最多核对 512 条变更路径；292 次搬动在未做重命名合并的清单中已占 584 个旧/新路径。因此把原 0-B 细分为上述两个串行微任务，各自形成固定 artifact、检查与整合凭据，再进入下一微任务；不改变搬动表、目标布局或第零步的验收门槛，不为此升级日常黑板。0-B1 在旧测试位置验证新源码，0-B2 再验证测试发现的改名，便于分别定位路径问题。两个微任务都不并行搬动，共用文件始终只有当前微任务的写入者。

### 第一步：公共格式、外层与角色接缝

| 微任务 | 依赖与范围 | 独立交付及验收 |
| --- | --- | --- |
| 1-A 公共值与 LiveChannel | 第零步获 Host 验收；`buddy/harnesses` 与协议/契约测试 | 落实第三、五节的冻结类型、序列化边界、当前值的转换和实时接口；将真实现有成功/失败/未知结果 fixture 逐项映射，不升级证明强度、不产生角色 verdict |
| 1-B 共用启动与收集 | 1-A 验收；四个 harness 外层、runtime、旧 structured_call | 将重复的日志、owned spawn、deadline、bounded stdout、双层停止收集合成一份；DSH Worker 仍启动旧 Node；保持各路径字节/解码/宽限与凭据清理时机；原生 controller 本体不改 |
| 1-C 角色控制器与观察策略 | 1-B 验收；`buddy/roles`、注册表/调用点 | 放置公共角色控制器、Worker/Router 准备和事实观察策略，保留候选、未知事件、即时预算/attention、检出/seal 和回合来源规则；给后续逐 harness 切换建立唯一调用点，未抽 harness 不另造执行载体 |
| 1-D 外层迁移与防护验证 | 1-C 验收；受影响测试与验收材料 | 提供测试编号变化表及未变化集合相等结果；注入 controller/native 两组未消失、stdout 无效、spawn 后 marker 写失败、证据路径替换等故障，确认原防护仍失败；四个 harness 的 fixture 路径和完整检查通过 |

1-A 的新格式只在内部；当前 AdapterOutcome 与 role output 按基线投影。1-B 的共用读取器有显式读取规则参数，不把 Codex/Claude 严格 512 KiB 与 Router 普通 256 KiB 简单替换成一个更大/更严格值。第一步的原生启动参数、原生工具策略与接口未改，因此不为验证公共值另花模型调用；如审阅 diff 发现涉及计划内原生行为，列出具体检查后按现有授权最小运行并登记次数；计划外的付费检查先问。

### 第二步：ZCode

| 微任务 | 依赖与范围 | 独立交付及验收 |
| --- | --- | --- |
| 2-A Worker 角色与完成服务 | 第一步获 Host 验收；ZCode/Worker role 的 prompt、MCP 与来源边界 | 把任务书、six-field outcome、协助、attention/inquiry-pending 和检出处理留给角色；MCP 作为本次服务承载 schema/签名回执；原生 root/call/order 验证留在 ZCode 驱动；保留旧拒绝信封大小与 HMAC |
| 2-B 单一原生运行 | 2-A 验收；ZCode driver/config/protocol/事实投影 | 将 create/resume、configure、subscribe/send、settlement、close/EOF 收成同一 run；通过请求的工具范围/schema/续接及角色 observer 表达差异；发现不发送输入；问询接 ExistingLiveChannel，read 未限制事实不冒充资格 |
| 2-C 切换调用、删除旧入口 | 2-B 验收；注册表、角色调用和 ZCode 测试 | Worker、fast 均走一个 run；删除 ZCode 的 `start_no_tool_structured`/基类审阅占位调用和 runner 的旧模式路由，不留 compat wrapper；read-only-worker-carrier-unimplemented 的外部资格结论保持 |
| 2-D 证据与整步验证 | 2-C 验收；迁移表、防护故障、原生脚本/验收记录 | 逐项核对 signed finish、拒绝、checkpoint/inquiry、resume、消息用量、工具投影顺序、失败 drain、unknown fast/Worker 差异；故障复现并跑完整检查。按已授权计划做一次最小私有原生冒烟，保存真实产物/停止证据和运行次数 |

### 第三步：Codex 与 Claude Code

| 微任务 | 依赖与范围 | 独立交付及验收 |
| --- | --- | --- |
| 3-A Codex 单一运行 | 第一步获 Host 验收；Codex driver 与角色调用、对应测试 | 合并现有 App Server 上的运行分支、重复配置核对和 Router 纠正骨架；保留 mode 对应的原生 config/权限、Worker checkpoint/private home/绑定、fast EOF 与 review 完成根口径；删除两个旧 structured 启动入口 |
| 3-B Claude Code 单一运行 | 第一步获 Host 验收，与 3-A/ZCode/DSH 并行；Claude driver/config/protocol、角色与测试 | 合并用户消息边界、发送/等待/drain 和目录核对；Worker/审阅共用 run；发现 initialize-only；无快速、native resume、问询的资格保持；删除其旧审阅入口和 inherited no-tool 使用路径 |
| 3-C StructuredOutput 修正 | 3-B 验收；Claude 原生交付识别、事实投影与预算测试 | 在 CLI `--json-schema` 的原生输出路径识别真正属于本次根的内建 StructuredOutput 交付；交付事件进入 completionEvidence，排除普通工具 start/end 和预算计数。保持其他工具/不完整流/子会话/同名 MCP 工具的计数与拒绝；不得往公共工具名字白名单加全面豁免 |
| 3-D 迁移与整步验证 | 3-C 验收；两 harness 的逐测试对照与原生材料 | 覆盖 Codex 账户/检出/配置/checkpoint/断流、Claude 第三方账户拒绝/消息边界/权限/背景沉降/结构化结果；注入相关故障，完整检查。Codex、Claude Code 两线分别完成自己的迁移检查、最小真实冒烟和完整检查，分别提交记录并停下等 Host；登记各自 harness 与次数，不互相等待 |

3-C 的识别依据是启用本次原生 schema、真实根会话、原生内建操作来源、call ID 关联及最终结构化结果；普通 `mcp__...StructuredOutput`、伪造子会话、只有名字无原生来源、缺失终态均不能豁免。对 stream tool-use 与 assistant 完整块的重复事件、后续 tool_result 配对、预算为 0 的结构化审阅、真正 Read 调用计数及仍需拒绝的 other 调用分别验证；防护测试临时恢复旧投影后必须失败。若原生事件不足以按这些条件识别，停止说明并调整经过审阅的识别规则，不静态证明厂商安装包。

### 可提前进行的 DSH 核对

| 微任务 | 依赖与范围 | 独立交付及验收 |
| --- | --- | --- |
| F-D1 无模型握手 | 本计划获准；只用隔离核对材料，不改运行模块 | 按第六节完成六问的免费部分，列 actual/declared/unknown；空会话、私有状态、配置/MCP 与停止证据；失败项只读核对 SDK profile 退路并停止报告 |
| F-D2 已授权的真实核对 | F-D1 经本 Host 核对；无需再次询问模型授权 | 一个明确脚本/配置/私有任务，记录六问需要实际运行的事实、真实 artifacts 和双组停止；形成行为差异逐项结论；已列三项按计划，超出范围才停止说明；F-D1/F-D2 线完成后提交记录等 Host |

### 第四步：DSH Python ACP 重做

| 微任务 | 依赖与范围 | 独立交付及验收 |
| --- | --- | --- |
| 4-A ACP 控制与元数据 | 第一步与 DSH 核对获 Host 验收；仅不碰公共文件的 ACP 客户端可在核对后提前写；Python DSH 模块 | 用已安装 `dsh --profile acp` 实现 stdio client、能力/会话配置/无 prompt 发现、私有存储与保守组停止；不采用 Python SDK、不安装 native runtime；模拟乱序/断帧/取消/失联验证 |
| 4-B 结果与可选会话服务 | 4-A 验收；Python MCP、Worker/Router role 的 DSH 接线 | 同一 run 服务 Worker/fast/经确认的 read 范围；完成工具、问询/检查点、权限 deny/allow 的已确认策略、语义工具事件与用量转成公共事实；接 ExistingLiveChannel；保持 outcome 与 seal 外层职责 |
| 4-C 删除项目 Node 集成 | 4-B 验收；DSH 老入口/目录、manifest/discovery/打包/checks、测试 fixture | 删除 `harnesses/dsh/` 的运行器/全部插件/目录脚本与 Node 测试，并删除 Python direct-LLM controller、旧入口、只供这些代码使用的 Node fixture/YAML bridge 资源；按逐测试意图表迁移 Node/Python 场景。更新打包资源、discovery/available 中本项目的 Node 要求和检查定位，DSH 命令只来自已安装 harness；只剩控制台开发需要 Node |
| 4-D DSH 整步验收 | 4-C 验收；旧/新测试表、差异实现清单与真实核对 | 故障证明停止未知、配置不一致、completion/问询失配、权限/迟到工具事实仍被发现；完整检查与新分发包私有验证。实现后的最小真实冒烟已有授权，记录次数；F-D2 早期核对不能当作最终实现已验证 |

Node 测试不按“删掉文件即删掉要求”处理；变化/删除的旧编号对应 Python 新编号或已授权行为差异/仅 Node 实现细节删除原因，未变的编号以集合相等证明。DSH 完整抽取依赖第一步及 DSH 核对验收，与其他 harness 并行。六问揭示需要改 ADR 的不可行项、超出已授权行为差异时停止；额度/登录导致未验证则如实记录，可继续无需这份证据的工作。ACP 不能满足的退路先做审阅与批准，任何采用 SDK profile/最小插件的实施微任务必须补明确输入、行为差异、删除范围及验证后重新停下确认。

### 第五步：C-Two 实时通道

| 微任务 | 依赖与范围 | 独立交付及验收 |
| --- | --- | --- |
| F-C1 无模型本机核对 | 现在独立 worktree 并行开始；不得先改生产通道 | 按第七节核对真实临时端点生命周期、碰撞/异常退出、set_server/set_client 进程全局设置；不重复证明作者确认的 client/server 共存；需要改 ADR 的失败项停止说明 |
| 5-A 公共 C-Two 后端 | F-C1 获 Host 验收后可提前实现独立后端；切换须所有 harness 验收；公共 live contract/后端与 controller | 替换 ExistingLiveChannel 背后的实现，随机人名的每运行端点、完整身份、窄 token、有界队列/帧、原生 owner loop 调用；用四 harness 的合成 session/活动/问询验证同一 contract |
| 5-B Worker 持有与服务转达 | 5-A 验收、四个 harness 全部获 Host 验收；Worker/runtime 与 protocol/service/inquiry | 实现 Worker endpoint、内部 attach/detach、服务内存定位、reconcile 后重新登记；去掉服务到 controller 的活连接；不改 claim/lease/receipt/schema，服务事务外转达 |
| 5-C 活动迁移与旧通道删除 | 5-B 验收；四 harness 的发布/观察、runtime 转报、问询桥/测试 | 活动经 C-Two snapshot 进入 Worker progress；删除两套 socket 和 activity 文件实时转报及相关路径预算，只保留持久证据。迁移旧问询/活动测试，删入口检查证明无双通道 |
| 5-D 失联与整步验收 | 5-C 验收；故障场景、迁移表、原生脚本和分发验证 | 验证服务重启、Worker/控制器死亡、同名/旧地址/旧 token/旧 attempt、消息重放/冲突、队列满/超时、端点失联但原生仍活、取消与 lease 独立、持久回执恢复；完整检查。准备受影响 DSH/ZCode 的真实问询冒烟，按已有授权最小运行并分别记录次数 |

除第零步串行独占实施 worktree 外，尽可能并行。第一步验收后启动 ZCode、Codex、Claude Code、DSH 四个独立 worktree；若公共接口需先用 ZCode 校验，可只让其先完成一个小切片，随后铺开，不把整步 ZCode 验收变成其他 harness 的额外依赖。每个任务描述固定基线、唯一可写目录与公共文件整合归属。并行线只写自己的 harness 包和对应测试/证据；公共值、角色模块、注册表、公共实时接口由本 Host 统一修改。发现接口缺口先在交付中提出，本 Host 处理并提交公共变更后，对原微任务 continue；不得私改公共文件。共享接口调整在相关步骤记录写明字段变化及原因。

## 9. 逐测试迁移、故障注入与完整检查

每步先冻结输入 commit 和实际收集的测试编号。第零步入库 `docs/acceptance/adr025-step-0-module-map.tsv` 及核对结果：模块级 old/new 改名表、按映射后的测试集合与多重计数一一对应、例外逐条说明；不为每个测试入库一行。真正加载的 unittest ID、继承/动态用例、Node/Vitest 文件与完整测试名的原始清单放 `tmp/`，以脚本比较，不能只比总数或用 AST 方法数替代。

后续每步的测试表只列编号变化、被删除和新增的测试，字段为 `old_id/new_id/disposition/reason/guard/fault_check`；未变化部分以集合相等证明。一对多/合并/删除仍说明覆盖目的和原因，不用新增测试掩盖旧覆盖丢失。迁移防护的测试在一次性测试 worktree/私有 fixture 注入破坏防护的故障，保存“应失败 → 恢复 → 通过”的摘要；原始列表和运行日志放 `tmp/`。第零步只变路径而未迁移防护逻辑的项目不扩大为厂商证明；路径/fixture 防护的代表性故障检查仍执行。

| 防护族 | 对应现有测试输入 | 注入故障与必须观察到的结果 |
| --- | --- | --- |
| 身份/来源/回执 | blackboard、Worker runtime、turn IO、ZCode MCP/protocol、Codex/Claude Worker | 改 attempt/generation/input hash、HMAC 或 tool/root 身份，缺少/乱序结束证据；不能导入回合或发布 Router 答案 |
| 停止与恢复 | liveness、windows process、worker runtime、governed service stop | 原生后代不退出、组观察 OSError/权限异常、controller 已停而原生未停、spawn 后 marker 写失败；未知继续占用，不能 seal/清凭据/重跑 |
| 工具流 | tool evidence、四 harness 投影、ZCode projection order、Router tool evidence | 缺 start/end/根身份、foreign/child、晚到/截断、重复冲突、观察在拒绝后才记录；事实不能被丢掉，黑板不发布不完整/越界答案 |
| StructuredOutput | Claude 结构化结果/工具事实/预算 | 恢复旧 ordinary-tool 投影，合法结构化审阅测试必须失败；同名 MCP、普通未知工具、子会话仍不能获豁免 |
| 角色/资格/边界 | current Router、stage2 review scope、routing modes、workflow/workspace | Worker 与 fast 未知事件互换、enum 越候选、输入改变、只读载体未实现却资格 true；保持各自当前结论 |
| 私有路径/安装 | private adapter/directories、runtime、packaging、upgrade/backup | source/fixture/asset 路径失效，证据路径换链接、源 checkout 替换、旧布局 cutover/rollback 错误；检查必须失败并保留可核对恢复材料 |
| 活交互 | inquiry、ZCode checkpoint/refusal、DSH inquiry 场景、activity、第五步新后端 | 同 ID 换 payload、假签收/假回答、错误 session、live 断开/队列满/旧实例重连；不重复送达、不启动新 turn、不延长 deadline、不把失联写成 stopped |

完整检查从全 checkout 用 uv 运行。第零步旧命令为 `uv run --frozen python -m buddy.checks`，新命令为 `uv run --frozen python -m hey_my_buddy.cli.checks`；每步最终都跑新完整检查，保留 Python、DSH Node（第四步删去前）、Console 测试/类型/构建及清理结果。第四步后检查器应明确列出 DSH 测试已迁移到 Python，不能空 Node 发现仍被算作通过。Console 使用受支持 Node，工作树自己的 `npm --prefix apps/console ci` 不影响日常 native harness；已有锁定依赖不升级。

测试子进程按当前 checks 的 SANITIZED_VARIABLES 清除继承 runtime/worker/agent authority、`VIRTUAL_ENV` 与 `UV_PROJECT_ENVIRONMENT`，使用独立 `BUDDY_STATE_DIR`、空私有 `BUDDY_RUNTIME_ROOT` 和 `BUDDY_DEV_SOURCE=1`。私有安装升级场景明确关闭源码模式并从其私有安装启动，检查实际解释器、包、manifest/资源、launcher、service 和 Worker 来源；不能只从当前 checkout import 来宣称安装通过。每天的配置、凭据、board/runtime 都不修改，凭据仅用 synthetic fixture 或由 native 自己处理已授权登录。

各微任务只跑受影响检查；整步集成时跑一次完整检查。通过后仅有新改动/失败/未解决问题才重跑对应检查或完整检查。本计划原生核对/冒烟及同目的必要重跑按用户的一并授权执行，每次记录 harness、次数及最小目的；额度/登录不能运行则记未验证并继续，不反复尝试或更改登录。计划外付费运行仍先问用户。真实冒烟用任务实际成果、原生身份、工具事实、回合/结果、双层停止和清理材料验证，不靠模型宣称或 dashboard 状态。Linux/Windows 未实际运行的部分明确记未验证，不为本机重构强行加平台环境或升级依赖。

## 10. 整合登记、暂停点与交付

每步交付 `docs/acceptance/adr025-step-N.md`、第零步模块表/后续编号变化表及实际必要的核对记录。记录包含输入/输出 commit、微任务 run/artifact/attempt 身份、逐项验收/continue 问题与后续 seal、整合 commit、Host 自行修过的范围外或合并问题、完整检查退出码和组件摘要、故障注入摘要、原生授权及真实证据边界、保留/回收的私有目录。原始输出和一次性实验脚本只放忽略的 `tmp/`；记录中的主目录/工作目录使用 `~`、`<repo>`、`<worktree>`、`<probe-root>`、`<upgrade-root>`。

固定暂停点为每条线的步骤记录与完整检查结果提交后：第零步、第一步、ZCode、Codex、Claude Code、DSH、第五步，以及两项核对完成。等用户转达 Claude Code Host 验收后才启动依赖该步的工作；互不依赖的并行线继续。另在可行性核对不通过而需要改 ADR、DSH 行为差异超出已写明三项、改动明显超出计划时停止说明。额度/登录造成未验证按前述规则记录，不把未验证冒充验收通过。

本次修订提交后直接并行开始第零步、F-D1 接 F-D2、F-C1，不再等待计划审阅。日常安装、升级和最终发布仍不在本宏任务授权内；不改用户配置、凭据或日常数据，不读取凭据文件内容。
