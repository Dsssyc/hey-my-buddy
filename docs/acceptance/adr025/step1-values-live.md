# ADR-025 第一步 1-A：公共值与实时通道（微任务交付记录）

2026-10-04，微任务 1-A 的执行记录（含对第一次交付 artifact `efb390aa` / 输出 `b17da62` 与第二次交付 artifact `14721c99` / 输出 `dfa4d16` 的 continue 修正），交付等待 Host 验收。输入基线 `4cf58dee3553195585b6224be3baca47401fb08e`（第零步验收后的合入基线），在独立 worktree 中完成；本文只记录本微任务自己的实现、转换边界、聚焦检查与清理，不宣告第一步完成，也不代表第一至五步任何后续项的状态。Host 历轮核对的只读探针材料（`~/.codex/worktrees/adr025-run-module/hey-my-buddy/tmp/adr025-host/values-fixed-review-grv7osw9/`、`values-second-review-udyphudf/`）与保留的历轮证据（`<evidence-worktree>/tmp/adr025-evidence-retained/step1a-first-evjy7qva/`、`step1a-second-vcvirxgi/`）本微任务只读未动。

## 交付物与写范围

全部改动为新增文件，没有任何已跟踪文件被修改（`git status --porcelain` 只有新增项），与授权的唯一写范围一致：`src/hey_my_buddy/buddy/harnesses/run_contract.py`、`live.py`、`legacy_facts.py`，`tests/python/buddy/harnesses/test_run_contract.py`、`test_live_channel.py`、`fixtures/run_contract/`（四个 harness 各一份 payload fixture，纯 JSON、不含 `__init__.py`：该目录没有测试模块，检查器只按包目录调度测试文件），以及本文。continue 轮只改了这七个路径内的文件，未动 base.py、各 harness 包、原生控制器、角色、注册表与任何调用点，未新增 IPC 服务或第二份转报。

## run_contract.py：冻结值与编解码

按计划 3.1 实现了 `RunRequest` 及嵌套冻结值（`RunIdentity`、`RunConfiguration`、`FrozenAccountReference`、`PrivateStatePaths`、`NetworkPolicy`、`RunBudget`、`RunContinuation`、`SessionService`），字段与计划表格一一对应；没有 role、routingMode、任务书、BoardClient、数据库连接或 agent 黑板凭据的容纳之处，请求的固定键集在解码时拒绝任何多余键（含 `agentCredential` 等，测试逐键断言）。冻结账户只携带标量非秘密引用（adapter/source/revision/credentialRevision/identity/nativeLocation），不携带内容；执行者不打开用户凭据文件。

按计划 3.2 实现了 `RunResult` 及全部嵌套值：事实 `end`（`nativeExitCode` 只来自 payload 的 `processState`，即内层原生进程退出；外层控制器退出在 `stopEvidence.controller.exitCode`，映射测试覆盖两者不同值的情形；退出码同时容纳 POSIX 信号终止的负返回码——`-15`/SIGTERM、`-9`/SIGKILL——与 Windows 32 位返回码，信号结束不变成接口错误，RunEnd 与双层 StopLayer 均有负码往返测试）；结果帧的固定键集在解码时强制（多余键如 `role`、缺键、非 object、错误版本都被结构化拒绝，不只测请求）、`modelStarted` 与 `ModelStartEvidence`（basis 闭集 native-start/input-admitted/input-sent/legacy-report/unknown）、requested/checked/observed 分离的配置（checked 每值带 value/basis/source/nativeIdentity，basis 闭集 catalog-membership/native-readback/unknown）、原生根身份与有序 `rootIdentities`、`RunValue`（schemaStatus/mechanism 闭集、显式必填的 validationBasis、有界 errors、correctionCount）、`CompletionEvidence`、现有 version-1 `toolEvidence` 包（空根列表与 `streamComplete: false` 的失败/未知包是结构可承载的事实，能否通过仍由角色与黑板判定）、`deniedInteractions`（不保存工具参数/输出，原生 request id 缺失时保留 None）、`unknownEvents`、按 tools/filesystem/network 的 `EffectivePolicy`、沿用现有正规化的 `activity/usage/quota/nativeFailure/lastAssistantMessage`、`ContinuationFacts`、双层 `StopEvidence`（interrupt 的 requested/acknowledged 缺失时保持 None/unknown，只有显式 true/false 才是事实）与 `evidenceRefs`。字段草案在本轮新增一处并有明确理由：`nativeError` 承载 ZCode/DSH 结果里已有的非额度原生失败记录 `{code, kind}`，与 `nativeFailure`（whitelist 额度失败归一包）分开往返，两者互不掩盖，也不借 end/reason 掩盖丢失。

冻结不靠共享可变结构：全部嵌套值为冻结 dataclass 与元组；自由 JSON 载荷以 `FrozenJson` 保存 canonical 文本、每次读取解析新副本，且 `RunValue.parsed` 等字段对裸 dict 在构造时统一冻结并按字段界限校验（对 `parsed["list"]` 的 append 与对元组项的赋值都被拒绝，有真实赋值/append 试验）。`formatVersion` 是精确整数 1，`true` 与 `1.0` 因 Python 相等性同样被拒（run 与 live 两侧均有测试）。三条解码路径（文本/字节/Mapping）走同一有界规则：Mapping 先 canonical 化并受同一帧上限约束，坏 UTF-8、重复成员、非有限数、过深嵌套都给结构化 BoardError，探针 130 KB 的 Mapping 快照与等价文本一样被拒。绝对路径同时接受现有 POSIX 与 Windows（盘符/UNC）写法；退出码容纳现有 Windows 32 位返回码（0..4294967295）。

界限（草案，均不触碰旧路径的普通 256 KiB/严格 512 KiB 读取上限与允许集合，理由如下）：`inputText` 上限 2 MiB——现有拼接后的实际最大输入是黑板任务文本上限 1 MiB（`MAX_TASK_BYTES`）加角色自有有界材料（协助提示约 2 KiB、工作区事实、fast 提示附带的 answer schema），不截断、不改写提示词，边界例是 1 MiB 任务文本加现有角色提示词的完整拼接（可构造、可往返）；请求帧上限 16 MiB，容纳该输入的最坏 JSON 转义开销（控制字符每字面六字节，2 MiB 输入至多 12 MiB）与 schemas/services 的同样开销，而不是为帧好看丢内容；`value.raw/parsed` 上限 512 KiB，等于现有最宽的严格控制器读取界限，旧范围内的合法值不再被拒绝或静默丢弃（70k raw 原样保留，超界显式 BoardError）；事实包上限 512 KiB，同样对齐严格读取范围——按本格式自身的字段/条数上限（128 条事件、512 字符标识）构造的最大合法 toolEvidence 包约 110,551 字节，旧严格/普通读取都能承载，新解码不再有更窄的 96 KiB 门槛（探针包已验证可入）；任何越界都按来源规则显式拒绝，不静默丢事实。

`HarnessRun` Protocol 按计划声明 `run(request, *, observer, services, cancelled) -> RunResult` 与不发 prompt 的 `discover() -> CatalogFacts`；observer 的观察事实集与 services 的方法面留给 1-C。`CatalogFacts` 为现有目录投影的冻结镜像：保留 `contextWindow` 等闭集名字之外的真实字段（经 `extra` 原样随行，投影回原 payload 逐字节相等）、容纳已支持的完整空目录（providers/models 可为空）、`warnings` 键的显式空列表与缺席两种投影都各自保留。

## legacy_facts.py：现有 payload 的事实转换

只承载真实现有控制器结果 payload 到新值的转换，逐字段映射，不提升也不削弱证明强度、不产生角色 verdict。声明并测试锁定的边界：`modelStarted` 只有布尔 True/False 是事实，缺失（DSH 受控结果）、`"unknown"` 占位或任何其它类型都映射为 None + `unknown`，不变成"已证明没开始"；True 的依据按 harness 声明（Codex 与 Claude Code `input-sent`、ZCode `input-admitted`、DSH 无工具插件 `legacy-report`）。checked 只来自 payload 自身的 `resolved` 块并按 harness 声明依据（Codex/Claude 目录成员资格、ZCode 会话快照回读、DSH 无工具 prepareCall 探针）；DSH 受控路径只有 requested（`reasoningEffort` 与 `effort` 识别为同一事实），checked 全空、observed 为空。`nativeTurnId`/`nativeSessionId` 等 `native*` 裸键与 `nativeIdentity` 对象一样被识别（ZCode Worker 结果的真实键就是 sessionId + nativeTurnId，fixture 不再补别名对象）。双层停止：`started` 只能来自持有侧显式输入，确认停止的报告不是 spawn 证据（confirmed 而 started 未给时保持 None）；内层观察依据按来源声明——Codex/Claude/ZCode 默认 `owned-process-group`，DSH 有两个真实来源（Node 运行器的 `legacy-node-report` 与 Python 无工具控制器的 `owned-process-group`），confirmed 的 DSH 停止必须显式声明来源，不按 harness 名一律标历史例外；确认未 spawn 是 `spawn-not-started`。interrupt 事实读取 payload 的 `nativeInterruptRequested`/`nativeInterruptAcknowledged`，调用方显式参数可覆盖，缺失保持 None。fast 值只读真实存在的 `rawAnswer`/`answerValid`/`correctionCount`（fixture 无任何编造字段）：`parsed` 是旧路径自己解码已检 raw 文本的那一步——解码对齐旧调用方的普通 `json.loads`，值有 bounded-JSON 表示时 `parsed` 携带它，没有表示的非有限值（旧普通校验器 `valid_answer('NaN', {})` 返回 True）保持 `parsed` 未知而 raw 原文与旧校验器自己的 schema 结论原样保留，测试用真实旧 validator 作对照——不把统一严校验变成新的业务规则，角色投影按原规则从 raw 取回值；超界 raw 显式拒绝；`governed_value` 要求调用方显式传入实际的 schemaStatus 与 validationBasis，不再默认把任意 dict 记为 valid。quotaFailure 键映射 `RunResult.nativeFailure`，payload 的 `nativeFailure` `{code, kind}` 映射 `RunResult.nativeError`，各自独立往返。

## live.py：唯一实时接口与现有设施适配面

`LiveChannel` Protocol 的签名严格按计划第五节：`capabilities()`、`request(request, *, timeout_ms)`、`observe(*, after_seq, limit, timeout_ms)`、`close(*, reason)`。能力位为 `LiveCapabilities`：activity、inquiryDelivery（realtime/cooperative-checkpoint/unsupported）、finishNotice、sessionContent；四个 harness 的现有能力表为——Codex 与 Claude Code 只有活动、问询 unsupported，ZCode cooperative-checkpoint，DSH 现有 Node realtime（第四步转 ACP 前保持）；finishNotice 与 sessionContent 全部 False，只声明 unsupported，不展开 ADR-022/024。现有问询限额原值保留并有测试与现有定义对账：每问/每答 4,000 UTF-8 字节、每运行 32 个问询、传输超时 100–5,000 ms、wait 最大 30,000 ms；跨运行帧建议上限 64 KiB，超过即显式拒绝，不静默截断，也不扩大各桥接自己更小的帧上限。

请求绑定按 identity + requestId 核对完整 kind/payload：同一请求的 replay 返回已记录的 reply、不再触桥、不重复送达；committed requestId 之下换 kind、换 questionId 或换问题文字都在调用桥之前以 request-payload-conflict 拒绝；requestId 与 questionId 是两个独立事实（测试覆盖 requestId≠questionId 的正常路径）；只有被桥接受的 ask 才被记忆，BoardError 或 accepted=false 的拒绝不记忆、同请求可按现有桥语义重试；每运行 32 个的限额仍由现有桥执行（第 33 个得到 too-many → unavailable）。`timeout_ms` 不再只做界限校验：它随窄绑定传入现有问询桥调用（`ask(questionId, question, timeout_ms)`），实际请求受所请求传输期限约束；未新增线程转报或角色通道。身份外来或渠道关闭时 unavailable，close 之后不再读取任何绑定，断开不改变原生 deadline 或结果归属。

观察定义了可继续取页的语义（字段草案新增一处并有理由：`InquiryState.seq`，条目级单调序号，状态变化取新号）：journal 去重后每问题一条、last-record-wins，状态不变的条目保持原 seq，变化取新 seq，条目列表按 seq 有序——更新总是拿到比已交付事实更高的序号，因此按 seq 排序后一切新事实都排在已读位置之后，分页不存在前排更新遮挡后排的缺口（Host 探针场景已作回归：首页读 q0..q9 后 q0 更新，第二页 q10..q19、第三页 q20..q31 加更新后的 q0，32 条全部可达且每条以最新状态出现恰好一次）。截断页的 `sequence` 是该页自己的安全游标（返回条目的最大 seq），只有完整页才携带通道水位——普通调用者跟随 `sequence` 或返回条目 seq 都不会跳过未返回的事实；活动按现有单调去重规则（`activity.is_newer`）推进并在每页随行，不影响条目游标；不同 limit（1/7/32/256）、按帧截断的页、以及同游标的重复 observe 均有真实进展与幂等测试，limit 与 64 KiB 都不产生不可继续的缺口。`events` 为 ADR-024 预留位：本步不承载其内容，允许集关闭——空列表是常态，非空 events 一律结构化拒绝，不偷渡本任务未允许的内容类型。能力位 supported 而本通道未接 ask 绑定时，回复是 unavailable（inquiry-binding-unavailable，本步的可用性事实），与能力位 unsupported（harness 原生不支持，Codex/Claude）明确区分。

## 聚焦检查（无完整检查，按微任务边界）

全部检查只用合成 fixture 与进程内合成绑定：本轮（continue）只运行了受影响的新契约测试与对 Host 探针语义的进程内复验，没有重跑其余 30 个 harness 模块，没有完整检查、Console、打包，也没有任何真实 harness 或模型启动；计划外付费运行为 0。本微任务自身的执行（zcode/GLM-5.3-Flash 经受控回合）单独计为 1 次，与上述分开。

- 本轮（第二次 continue）按指示只运行这两个新测试模块一次：`uv run --frozen python -m unittest tests.python.buddy.harnesses.test_run_contract tests.python.buddy.harnesses.test_live_channel`：62 个测试全部通过（34 + 28），通过后未做无理由重复；并做了对 Host 第二轮零进程探针六项发现的同语义进程内复验，全部反转（额外结果键被拒、信号退出码被接受、任务文本加角色提示词被接受、分页在前排更新后仍按序取全且截断页 sequence 为页游标、110,551 字节的合法 toolEvidence 包被承载、NaN raw 保持 valid 且原文保留）。未跑其余 harness 模块、真实 Native、模型、完整检查、Console、打包。
- 对 Host 探针的每项发现做了同语义的进程内复验，全部反转：真实 rawAnswer 的 parsed 可解码、`modelStarted: "unknown"` 为 None、interrupt 缺失 None、payload 的 `nativeInterruptRequested` 被读取、confirmed 停止不再隐含 started、同 requestId 换 payload 在桥前冲突且桥只被调一次、`true` 版本号被拒、Windows 路径被接受、300k 输入与 70k raw 保留、空根 toolEvidence 包与空目录被承载、dict parsed 不可变、超帧 Mapping 与文本同样被拒。

## 原生运行计数（对首轮记录的更正）

首轮记录"原生 harness/model 验证 0 次"不准确，现更正如下。首轮做过两类涉及 harness 测试的运行：其一，用检查器子进程机制对 `buddy.harnesses.*` 全部模块做了含/不含新文件的两轮探针，其中 `zcode.test_zcode_native`（InstalledZcodeTests，5 个用例）在两轮中都会启动本机已安装的 ZCode 原生 CLI（发现与 fixture-provider 回合，未调用付费模型且这些用例在本机失败）；确切的进程启动次数无法从现有日志读出，记为未知——只登记：两轮模块级运行、每轮至多 5 个用例、每用例可能多次启动。其二，一次进程内 unittest discovery（438 个编号）在该轮中 `test_zcode_native` 因模块加载失败未执行，不构成原生启动；其余 codex/claude/zcode/dsh fixture 运行全部是 stub 子进程，不是原生产品。本轮及上一轮 continue 均未运行任何 harness 测试模块，原生启动为 0；首轮两轮探针合计按上述登记，不写 0。

## 首轮两条失败登记（原因主张撤回，登记保留）

首轮探针中 `zcode.test_zcode_inquiry`（21 个失败/错误）与 `zcode.test_zcode_native`（5 个）失败，含与不含新文件两轮的失败编号集合一致。首轮记录把它们归因为"安装发现/真实 journal 在本环境不可用"——撤回这一原因主张：相同的失败集合不能证明原因。保留的事实是这两个模块在首轮探针条件下的失败登记本身。一个未经复验的可能解释（只登记，不定论）：首轮探针把检查私有根放在了本检出较长的 worktree 路径下，而检查器在 macOS 特意把私有根建在 `/tmp` 以保持根短（`checks.py` 的 `create_private_root(directory=Path("/tmp") if sys.platform == "darwin" ...)`），ZCode 问询桥对 Unix socket/journal 路径有长度预算（约 105/101 字符），长路径正是 `bridge-write-failed` 的典型条件；按 Host 指示本轮未再运行原生模块去补证，结论留给 Host 的完整检查。本轮 continue 不受其影响。

## 新增编号与未变集合

本轮 continue 后模块内编号：`buddy.harnesses.test_run_contract` 34 个、`buddy.harnesses.test_live_channel` 28 个，共 62 个（前缀按检查器的模块名登记；早前清单里的 `tests.python.*` 只是直接调用时的别名，整步 1-D 统一汇总变更/新增/删除与未变集合证明）。完整 62 行 ID 清单在被忽略的 `<repo>/tmp/step1a-continue2-test-ids.txt`。未变证明：`git status` 只有上列新增路径、无任何已跟踪文件修改，既有测试编号集合不可能被本微任务改变；本轮按指示未重跑其余 30 个 harness 模块。54 项早前通过只证明那 54 项，62 项本轮通过也只证明这 62 项，都不是整步验收。

## 清理与一次性材料

一次性材料都在本检出被忽略的 `<repo>/tmp/` 下并全部保留：本轮新增 `step1a-continue2-test-ids.txt`；历轮的 `step1a-probe-harnesses.py`、`step1a-probe-with-new-files{,.txt}` 与逐模块日志、`step1a-probe-baseline{,.txt}` 与逐模块日志、`step1a-probe-root-with-new-files/`、`step1a-probe-root-baseline/`、`step1a-harnesses-discovery-with-new-files.log`、`step1a-new-test-ids.{log,txt}`、`step1a-inquiry-fails-{with,base}.txt`、`step1a-native-fails-{with,base}.txt`，以及本轮的 `step1a-continue-test-ids.txt`。更正首轮清理表述：删除清单不完整——检查器子进程与 `TemporaryDirectory` 各自拆除了自己的私有根，其确切路径未逐项留存，现如实记为未知，不做回溯扫描推断归属；本轮明确记录的删除只有一处：首轮为做基线对比曾把 6 个新产物移入 `<repo>/tmp/step1a-parked/` 再原路移回，随后删除了这个本次创建、当时已空的目录（未屏蔽报错）。除上述与各子进程自拆私有根（未知清单）外，无其它删除。

## 接口余项（留给后续步骤或 Host 统一修改）

`RunRequest`/`RunResult`/live 值为草案，后续按实际接口缺口由 Host 统一改公共文件并记录；历轮草案新增/调整均已说明理由（`nativeError` 字段、`InquiryState.seq` 与分页游标语义、`warnings_key_present`、界限重选、events 允许集关闭）。其余余项：observer 的事件形状与 services 的方法面未定义（1-C 放置）；`discover()` 目前无参，与现有 `discover_models()` 一致；observe 的 timeout_ms 已传入 ask 绑定，observe 自身对现有文件/journal 后端仍是立即读，等待语义随第五步传输；`events` 允许集已关闭（非空一律结构化拒绝），到 ADR-024 实施时开启；两种问询 journal 的逐字段翻译由调用方绑定给出闭集视图，在各自 harness 步骤接线时核对；`InquiryState.seq` 的分页语义在第五步换 C-Two 传输时由 Host 复核。

## 状态边界

本微任务只是第一步的 1-A：交付公共值、编解码与实时接口的适配面，供 1-B（共用启动与收集）与 1-C（角色控制器与观察策略）在其上实现。第一步未完成，四个 harness 均未切换到新接口，本文所有测试都不启动原生进程，DSH Node 历史例外按计划原样保留。
