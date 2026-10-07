# ADR-025 第五步 5-C1：问询 owner 交付与验证边界

本微任务基线为 `93a764807822b01fb7511119568b83a51b423db3`，仅修改分配的五个文件。本轮从原 run 封存的部分产物 `47bd87313eb773b1520cf5c2a715ba46d4dee4f5` 继续，完成删参、删除无生产使用方法及对应测试迁移；12 项受影响本地聚焦测试通过，两项本次真实故障变异按预期转红。Host 续跑输入报告已在旧部分产物的独立固定副本完成公共重放修复和 15 项 owner 集成验证；这不是本轮最终代码的跨进程验证或本微任务已验收结论。最终固定产物仍需 Host 适配新构造签名、复跑受影响 peer 并审阅提交。没有修改公共值、角色、注册表、protocol、服务接线或操作文档；没有切换/新建分支。

## 实现

新构造签名为 `InquiryBridge(*, identity: dict, journal_path: str, error_factory, event_metadata, limitation: str, attention_path: str | None = None, live: CTwoLiveEndpoint | None = None)`，删除没有读取方的 `credentials` 参数。`live` 接受实际 `CTwoLiveEndpoint`。存在 endpoint 时，把传入 `taskId`、`attemptId`、`generation`、`turnId` 与 `live.identity` 逐项比较，包含严格标量类型；`invocationId` 与 `inputSha256` 始终来自实际 `RunIdentity`。不存在 endpoint 时，原生 journal 和 MCP 仍可使用，且不启动消费线程或另一个运输通道。

`start()` 加载原 journal，发布读取事实，启动唯一消费线程。线程只使用 `live.consume_request()`，经原 `_ask` 的锁、exclusive journal barrier、完整 append 和真实 fsync 后调用 `settle_request(LiveReply)`；入队没有 queued 事实。真实 native 收据仍由 ZCode 的 `RootTurnEvidence` 验证，原 `deliver_inquiries` / `record_answer` 才能追加 delivered / answered。RPC 不写原生连接，不启动回合，不延长截止期限。

`activate`、`note_event`、`note_attention`、journal 写入与 `close` 分别发布严格 `LiveObservation`、`InquiryState`、`LiveJournal`；没有把 activity/inquiries 塞进 `publish_snapshot`。纯投影复用公共 `live._journal_states`，原字段包括答案来源、字节数、toolCallId、时间、truncated、delivery、reason 和 limitation。序列仍由 endpoint 的 merge 规则管理；owner 的丰富状态在 settle 之前发布，没有用 settle 的最小状态覆盖它。

`read_inquiry_journal` 从旧 binding 中提取原完整读取逻辑：缺路径、未写、不可读、超限和真实可读空文件；只通过现成 `read_shared_snapshot` 做 shared-locked read，额外检查读取时增长到超限的事实。每 id 最后有效记录决定拒绝或接受；最后 foreign 记录拒绝整个 id 的历史，后续合法记录清除旧拒绝并保留合法来源连续历史。加载复用此逻辑，保留 crash-tail 隔离、部分写入 rollback 和锁内 truncate/fsync。读取失败保留原 reason/error；严格投影失败发布 observed=false / journal-unavailable，不造可用快照。

`close()` 先尝试把仍 answerable 的条目 durably terminalize 为 unavailable，发布 ended / ready=false，再停止并 join 消费线程。写失败仍保留原状态与错误；close 不构成 native stop 证据，也不关闭 controller endpoint。endpoint 的 finally 生命周期属于 Host 的 controller 接线。

删除 socket listener、stale socket 清理、accept/serve、帧解码/token、`handle`、`bridge_request` 依赖、`_bridge_call`、`bind_live_channel`、`ExistingLiveChannel` 使用以及旧帧常量。本轮进一步删除失去旧 socket 调用方的 `describe_answer` 和 `_discard`，没有保留兼容入口；原 `start/close/activate/note_event/note_attention/attention_report/deliver_inquiries/record_answer/snapshot/report` 本地方法保留。显式 `discarded` 事实词汇、历史 journal 读取、报告计数和合法晚到答案的终态保护继续保留。

复用成熟机制：C-Two 的既有端点、pydantic 公共严格模型、现成 journal locked reader、既有 receipt verifier 和原 fsync/rollback writer。新增标准库 Thread/Event 仅连接 endpoint 的 owner 消费 API；小的本地 `_owner_response` 只承接原 `_ask` 回调结果，没有线缆格式、监听器或客户端。未新写 transport 或通用 DTO 校验器。

## 原 run 聚焦验证与原始证据

只运行受影响聚焦测试，未跑完整检查。所有测试命令清除 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`、`BUDDY_RUNTIME`、`BUDDY_RUNTIME_IDENTITY`、`BUDDY_WORKER_STATE`、`BUDDY_WORKER_ID`、`BUDDY_AGENT_CREDENTIAL`、`BUDDY_AGENT_CREDENTIAL_FILE`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`，设置 `PYTHONPATH=src:tests/python`、`PYTHONDONTWRITEBYTECODE=1`。`TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 使用 `<task-root>/t`，新 owner 一次性材料使用 `<task-root>/m` 的新目录；没有读取凭据内容。

普通 `uv run --frozen` 准备依赖时下载被 sandbox 网络权限拒绝。随后从本机缓存复制 uv.lock 的确切 macOS wheel 到私有环境：CPython 3.13.3、c-two 0.6.0、pydantic 2.13.5；wheel 版本清单在 `<task-root>/m/owner-wheel-env-wbbny0mp/packages.json`。首个缓存候选是 editable C-Two，导入缺 libfastdb，其失败日志保留；修正使用真实 wheel 后依赖导入成功，没有改缓存、外部 checkout 或日常运行时。

最终命令通过 `uv run --frozen --no-sync <private-env>/bin/python` 执行 `<task-root>/m/owner-final-focused-zhkl8vyv/run_focused.py`。脚本明确加载旧 `BridgeQueueTests` 18、`JournalBarrierTests` 4、`FinishToolTests` 15，以及新 owner/journal 本地 12 项，不包含已确认的富字段重放阻塞用例或共享内存权限阻塞 peer。49 项通过，退出码 0，原始绿证据为 `m/owner-final-focused-zhkl8vyv/local.green.log`，不是全模块通过。

原 run 富字段重放单独实际运行 `python -m unittest -v buddy.harnesses.test_inquiry_owner.InquiryOwnerTests.test_replay_and_republished_snapshot_keep_seq_and_rich_delivery`，退出码 1，`BoardError: value is not bounded JSON` 来自冻结公共 endpoint 的 `_question_reply`。原始红证据为 `m/owner-final-focused-zhkl8vyv/public-replay.red.log`，未删字段绕过。此失败后来由 Host 在公共代码中修复，见下文续跑更正；原始失败证据保留。

原 run 真实 C-Two 子进程两项已经实际尝试，均在 `endpoint.start()` 失败，错误为 `server error: config error: response pool init: shm_open failed: Operation not permitted (os error 1)`。原始证据在 `m/owner-peer-diagnostic-3ivyigqv/focused.raw.log`，同次五项 journal 验证通过；peer stderr 与私有 journal 材料同任务根保留。没有将这些失败写成通过或把 RPC queued 回执当 native receipt。peer 案例实现 queue owner → durable question → 真实 MCP signed receipt → native root scheduled/result evidence → cooperative delivery/answer，以及 child exclusion、伪造签名与 close 后事实保留。Host 后来验证旧部分产物的两项 peer 通过，最终代码仍需 Host 复跑，见下文。

旧模块余下 7 项（Activity 3、原生 attention/config 3、catalog 1）方法正文未改，本轮未跑；其 controller/registry 导入依赖已删除 binding 的 Host 接线，不能由本微任务宣称通过。真实模型调用 0，未启动 DSH 或其他真实 harness，未做安装版付费冒烟，未改普通 HOME、用户配置、凭据、数据或登录，未安装/升级日常运行时。

## 迁移防护：真实故障变异

在 `<task-root>/m/owner-mutations-_suz4hju` 保存原实现的独立变异源码与执行器，通过重新执行该源码加载真实 owner 实现，再运行同一个真实测试；生产文件始终保持原件，没有覆盖旧证据。正常原件同两项测试通过，`guards.green.log` 退出码 0。

- F1：仅删掉 journal commit 的 `os.fsync(fd)`。真实 fsync 拒绝案例从 unavailable 误报 queued，`assertFalse(refused.observed)` 失败；`skip-fsync.red.log` 退出码 1。
- F2：仅取消 journal 的 version/四项身份绑定判定。实际 foreign/unversioned 文件记录进入有效历史，连续历史断言失败；`ignore-binding.red.log` 退出码 1。

两项都走真实 owner/journal 路径，没有 mock 掉迁移函数来制造失败。变异源码、完整红/绿日志与 `results.json` 一并保留，未删对象或覆盖材料。

## 本轮续跑、更正与验证

Host 的本次续跑输入报告：原 run 曾返回 assistance，但安装版 Codex 在关闭阶段报 `native-shutdown-failed`；现有两层任务停止最终均已确认，原 run 没有记作已验收。Host 从封存部分产物 `47bd873` 构建独立固定副本，纳入四个已验收 producer 和公共后端整合修复：`_question_reply` 使用 `entry.delivery.value`；新增公共回归 1 项通过，owner 模块 15 项全部通过（1.660 秒，包含 2 项真实 C-Two 跨进程 peer）。Host 原始证据引用为 `phase/review-5c1-integration-probe1.log`，固定副本为 `<host-task-root>/m/5c1-host-integration-probe1`。这些事实来自 Host 输入，本轮没有读取或修改该副本，也没有把原 run 失败改写成原 run 通过。

本轮生产 AST 与封存 `47bd873` 比较，只删除 `credentials` 参数、`describe_answer` 和 `_discard`；其余生产实现 AST 完全相同，消费、锁、真实 fsync、投影和历史终态规则没有改动。测试工厂同步改为 keyword-only；需要 typed endpoint 的本地断言使用既有 `TEST_CRM`、wire helpers 与 `CTwoLiveEndpoint.observe` 读取实际 `LiveSnapshot`，不启动 IPC 或第二 transport。历史 discarded 案例使用真实 journal 文件并经 `start()` 加载，检查原 delivery/limitation/reason、foreign 拒绝和非 pending；晚到答案案例在真实 signed receipt 验证后仍检查终态不变，错误 id/hash 继续拒绝。答案视图断言改读实际发布的 `InquiryState`，完整检查 bytes、via、toolCallId、at、truncated、delivery 和 seq。旧 discard 写失败断言改为真实 close 追加失败仍保留 queued 与错误，不把 ended 当 native stop。

沿用原私有 wheel 环境与变量清理，通过 `uv run --frozen --no-sync <private-env>/bin/python <material-dir>/run_focused.py` 执行 12 项删参/旧方法影响的本地聚焦测试，退出码 0，1.023 秒。逐项列表、执行器、结果和原始日志在 `<task-root>/m/owner-slim-focused-al1yh804`（`run_focused.py`、`results.json`、`focused.raw.log`）。包括 5 项 owner 构造/入口/本地 MCP/队列真实 commit/fsync 测试、6 项旧 ZCode 历史/答案/失败保护/restart 测试和 1 项受新工厂影响的 journal shared-reader barrier 测试；未重跑旧未变套件、旧公共失败、被拒绝的 IPC 或完整检查。

本次故障防护在 `<task-root>/m/owner-slim-mutations-w59xynhq` 保存最终生产源码的独立副本和执行器。原件对同两项迁移测试通过（`original.green.log`，退出码 0）；F3 仅删除 `record_answer` 的 discarded/unavailable 终态保护，晚到答案测试的已发布快照不再等于原终态（`skip-terminal-guard.red.log`，退出码 1）；F4 仅删掉答案 writer 的原生 `toolCallId` 来源字段，实际 published `tool_call_id` 从原生调用 id 变成 None，被来源字段断言拒绝（`drop-native-tool-source.red.log`，退出码 1）。原件、两个变异源码、`run_fault.py`、原始红绿日志与 `results.json` 均保留；没有改生产文件来跑变异，也未覆盖旧证据。

本轮真实模型调用 0；未启动 DSH 或其他真实 harness，未改 HOME、SDK、权限、用户配置/凭据/数据/登录或安装版运行时。本轮未运行真实 C-Two peer；构造签名和 peer 共用测试工厂有变化，因此 Host 的旧 15 项绿证据不覆盖最终代码，Host 需在自己的允许 C-Two 共享内存环境复跑最终两项 `InquiryOwnerPeerTests` 并保存原始证据，相关 producer 工厂签名适配也须由 Host 验证。

## 编号集合

[编号表](adr025-step5-inquiry-owner-test-ids.tsv)只列 changed/deleted/added。最终旧模块 46 → 44：12 项方法正文变更（其中旧 discard 编号映射到历史 discarded 投影编号），2 项旧 socket 集成删除，由新真实 C-Two peer 案例替代；32 项方法正文 AST 未变。新增 owner 模块 15 项，合计 59 个显式编号。基线、旧部分产物、最终编号清单和 AST 核对结果在 `<task-root>/m/owner-slim-audit-xao72_yc`；`unchanged-baseline.ids` 与 `unchanged-current.ids` 集合逐项相等，对应方法正文 AST 相等，双方清单 SHA-256 同为 `2fca3fb9e8fa65f14f1d3ce903122087391cbc67cc95886acef33555a1d18a74`。`audit.json` 同时记录本轮 8 项方法正文变更及生产 AST 仅三项删除的核对结果。原 run 的 34 项未变审计材料仍保留在 `<task-root>/m/owner-id-audit-iav8o9a5`。本地 fixture 工厂和调用助手变更不构成 controller 已完成接线的证明。

## 无生产使用项与 Host 接口缺口

本微任务没有新增仅为未来使用的生产 DTO/transport。删除无生产引用的旧 `ANSWERED_STATES` 常量、本轮无读取方的 `credentials` 参数和无生产调用方的 `describe_answer` / `_discard`。原 run 记录曾将本地测试调用列作保留 `describe_answer` 的理由，现予以更正：测试调用不是生产使用方，这两个方法已删除。新 `read_inquiry_journal` 已由 owner 加载和发布实际调用，`_owner_response` 已由消费线程实际调用。新测试借用现成测试 CRM 和 wire helpers，不新增公共契约或生产注册表。

Host 需要处理以下具体接口，不在此次五文件写入范围内：

1. 公共 `_question_reply` 的 FrozenJson 重放缺口已由 Host 报告修复并在旧部分产物副本实测通过；本 checkout 的冻结公共源码仍是原版本，未自行修改或重跑此旧失败。整合时保留 Host 的 `entry.delivery.value` 修复。
2. 本轮 `InquiryBridge` 构造改为全 keyword-only 并删除 `credentials`，不能再传旧首个位置参数。Host 按其续跑输入统一适配已验收 ZCode/DSH 的 `make_inquiry_bridge` 跨微任务调用，把 identity、journal_path、原生 callbacks、limitation、attention_path 和实际 live endpoint 传入新签名；凭据仍由原生工具/配置拥有，不转移到 owner。此 checkout 的冻结两处生产工厂仍是旧调用，未越权修改。controller 的 endpoint 关闭责任、独立 `publish_activity` 和原生 stop 证据合同维持原整合约定。
3. Host 清理公共 `ExistingLiveChannel` 时保留或提取现有 pure `live._journal_states` 投影；此次 owner 只复用该函数，没有复制公共字段规则。公共 endpoint 原身份与序列合同不变。
4. 最终两项真实 `InquiryOwnerPeerTests` 因构造接口/共用工厂变化需 Host 复跑，并以最终固定代码配合 Host 公共修复保存原始绿证据；旧 `47bd873` 的 15 项通过不能直接替代。任何相关 DSH 启动都须私有 DSH_HOME。Worker 不再尝试被拒绝的 IPC，不修改权限/SDK/配置。

## 交付和清理边界

五个固定文件为 owner 实现、新 owner 测试、旧 ZCode 测试迁移、本记录和编号表。临时任务根只在交付报告中给出确切路径；原始材料保留在其 m/，既有 fixtures 按正常 teardown 收尾，没有手动删除任何东西，Host 验收后按登记根回收。首次 uv 自动准备产生 checkout 内忽略的 `.venv`（后续未使用），也保留未删除，交由 Host 决定回收。

实际 `git diff --check` 退出码 0；随后只对五个 scope 文件执行 `git add -- ...`，退出码 128，`<shared-git>/worktrees/<checkout>/index.lock` 创建被 `Operation not permitted` 拒绝。未运行 commit，未重建仓库、切换分支或更改 Git 权限。原始结果在 `<task-root>/m/owner-submit-f2fvlyg2`。需 Host 按五文件摘要代执行 staging/commit；公共接口与真实 peer 未验证完成前，本微任务不声称固定交付已验收。

本轮固定交付前再次检查三份 Python 源码可解析、五文件无机器 home 绝对路径、Git 变更集合恰等于五路径 scope，均通过；`git diff --check` 退出码 0。仅对五路径尝试一次 `git add -- ...`，仍退出码 128，错误同为 `<shared-git>/worktrees/<checkout>/index.lock` 创建被 `Operation not permitted` 拒绝；未执行 commit 或尝试更改共享元数据权限。原始日志和结果在 `<task-root>/m/owner-slim-submit-rk2m3teh`。最终五文件摘要在交付报告引用的新 `scope-manifest.json`；本轮固定交付后停止，由 Host 核对系统封存 output artifact、代执行 Git 提交、整合新构造签名并验证最终 peer。原 run 的 Git 拒绝、IPC 拒绝和公共失败材料完整保留。
