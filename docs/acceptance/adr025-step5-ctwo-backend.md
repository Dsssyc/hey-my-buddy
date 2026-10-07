# ADR-025 第五步微任务 5-A：可独立验收的公共 C-Two 实时后端

Status: 待 Host 验收。2026-10-07 于隔离受管 worktree 完成，固定基线 `2e6c2dbc9924e065886c1bad8f682a712d5b6217`（与任务输入一致），交付 commit 见本记录末尾。开工前已读 ADR-025 第 13 条、执行计划第五节与第七节、第五步开头整合登记（`adr025-step5-integration.md`）与已验收的 F-C1 事实（`adr025-ctwo-feasibility.md`）；未静态审阅 C-Two 厂商源码，仅使用其公开 API 与 F-C1 实测事实。本线只写新后端，实时通道的整合等待第五步开头清理批次完成，由 Host 统一切换；未修改任何现有公共值、角色、注册表、协议模块、运行模块、Worker、服务或既有文档（对新模块的导入只读）。公共接线与 C-Two `CONTRACT_VERSION` 0.28→0.29 归 Host。

## 交付范围

唯一可写路径内新增四个文件，无既有文件改动：`src/hey_my_buddy/buddy/harnesses/c_two_live.py`（后端本体，822 行）、`tests/python/buddy/harnesses/fixtures/c_two_live_peer.py`（真实 C-Two 子进程 peer 夹具与测试 CRM）、`tests/python/buddy/harnesses/test_c_two_live.py`（34 项测试）、本记录与 `adr025-step5-ctwo-backend-test-ids.tsv`。

后端是 `LiveChannel` 接缝背后的传输替换，不触碰角色业务：`CTwoLiveEndpoint` 运行在控制器进程里，以一个随机人名注册 C-Two 资源，contract 只公开具名 `request`/`observe`/`capabilities` 三个操作，无任意方法名 dispatch；`CTwoLiveChannel` 是持有方一侧的 `LiveChannel` 客户端，满足 `live.LiveChannel` 运行时协议。CRM contract 类是两侧的可信构造参数——生产的 `HarnessRunLive`/`WorkerRuntimeLive` 由 Host 在 protocol 下声明，测试用夹具内声明的真实 `@cc.crm` 测试 CRM（两进程以同一模块名装载同一份源，ABI 一致）。

## 设计与事实规则

- **地址路由与命名。** 注册后以 `cc.server_address()` 回读的地址是唯一路由键，`cc.connect(contract, name, address)` 的 name 参数只选择该地址上的资源；人名仅显示，不作身份或授权判据，不做重名重试、不按名字寻址。ready 材料发布 `{address, name, instanceId, hostPid, socket}`（0600、O_EXCL 无链接），窄 token 只存构造方私有绑定，不进任何公开材料、回复、快照或诊断。
- **帧。** 跨进程帧全部走现有公共实现：`InternalModel` 严格基类（extra=forbid、frozen、驼峰别名）+ `json_codec` 的规范编码与严格解码 + 现有 Live 模型（`LiveReply`/`LiveSnapshot`/`LiveCapabilities`/`InquiryState` 原样复用）。新增私有 envelope 字段仅 `instanceId`、`token`，加请求帧上服务端真实消费的 `timeoutMs`；保留 64KiB 帧、4000 字节问答、32 问询、100–5000ms 窗口与 1..256 分页上限，语义与 `live.py` 现行校验逐条对齐。
- **逐项绑定。** 服务端对完整运行身份六个分量（taskId、attemptId、generation、invocationId、turnId 含 None 态、inputSha256）、窄 token（`secrets.compare_digest`）与 instanceId 逐项核对，首个不符即以 `identity-mismatch:<分量>`/`token-mismatch`/`instance-mismatch` 拒绝。错帧（非严格 JSON、多余成员、类型不符）拒绝为 `frame-invalid`，超 64KiB 为 `frame-too-large`，处理程序不抛异常。
- **RPC 线程与 owner loop。** 请求线程校验后只进有界队列（容量=32 问预算），提供 `consume_request(timeout_s)` 供 owner loop 消费；队列项是无信封的纯 `LiveRequest`，token 不达 owner。受理回复如实 `queued(observed,state)`，绝不升级为 delivered；重放/同问重发按问题当前状态回 `duplicate=true` 且不再入队，换 payload 为 conflict；关闭、能力不支持、32 问上限、受理超窗、队列满各有明确不可用事实。
- **快照只读。** `observe` 只服务有界最新已发布事实：活动经 `protocol.activity` 的 `normalize_activity`+`is_newer` 单调合并（相同幂等、更旧不换），问询状态与 observation 只留最新值；seq 机制沿用 live 接缝的"携带事实变化才取新序"；点查与 fields 选择只读所选源；不延长 deadline、不启动新 turn。持久 journal、结果与回执仍归现有生产代码。
- **失败分类不伪造停止。** 客户端把传输异常、超窗、槽位占满、坏回复分别映射为 `transport-unreachable`/`transport-window-expired`/`transport-busy`/`reply-invalid` 不可用事实；端点失联、被杀、关闭、阻塞都绝不写成进程已停。

## 所有权清理原语

`start()` 时从回读地址按 F-C1 实测布局（macOS `/tmp/c_two_ipc/<server_id>.sock`，与 TMPDIR 无关）推导本端点文件的确切路径并捕获 `(st_dev, st_ino)` 文件身份；推导失败则不捕获，后续清理一律拒绝。`cleanup_abandoned_socket(descriptor, evidence)` 只在外部持有者传入 `ConfirmedProcessGone`（收割到的 exit_code + 组消失观察为 True + pid 与描述符 hostPid 一致）且文件身份仍相同才删除该一个文件；未确认、异 pid、身份未知、文件被替换各按其理由拒绝，其他文件一律不动，不扫描 `/tmp/c_two_ipc` 旧残留。

## 验证

聚焦运行（未跑完整检查、未调用真实 harness 或模型、未安装升级日常运行时）：`uv run --frozen python -m unittest -v buddy.harnesses.test_c_two_live buddy.harnesses.test_live_channel buddy.harnesses.test_run_contract`，环境按检查器口径清除继承变量、`PYTHONPATH=src:tests/python`、`BUDDY_DEV_SOURCE=1`、`TMPDIR`/`BUDDY_CHECKS_TMPDIR` 指向任务根。结果 109 项全部通过：新模块 34（其中 8 项为真实 C-Two 子进程生命周期）、相邻 `test_live_channel` 44、`test_run_contract` 31，两相邻模块编号与断言未动。迭代期间的失败原始输出（合并逻辑缺陷、帧缺 limit、同名跨线用例身份错误等）保留在任务材料目录。

生命周期证据（每端点的创建/停止/删除，测试打印并写私有根）：正常 stop 后 `addressAfter=null` 且本次套接字文件消失；SIGKILL（exit -9、组消失）后持有者清理 `deleted`；同 server_id 重绑替换文件后旧描述符清理 `refused: socket-file-replaced` 且文件保留、新端点干净停止后消失。收尾扫描无本任务遗留 peer 进程，`/tmp/c_two_ipc` 中他人条目未触碰。

## 未用内容与 API 整合说明

新增字段/类/参数全部有真实读写方，无预想功能预留（目标为空）：envelope 两字段与请求帧 `timeoutMs` 被服务端真实核对；`EndpointSocketFact`/`LiveEndpointDescriptor`/`ConfirmedProcessGone`/`CleanupOutcome` 由描述符发布与清理原语真实读写。尚需 Host 接入的调用点：protocol 下声明生产 `HarnessRunLive`/`WorkerRuntimeLive` contract 并作为构造参数传入两侧；控制器启动处 `CTwoLiveEndpoint(...).start()` + `write_ready_material`（私有根），owner loop 在检查点 `consume_request` 交付并 `publish_inquiry_state`/`publish_activity`/`publish_observation`，结束时 `close`+`stop`；持有方以私有绑定构造 `CTwoLiveChannel`，确认进程组消失后调用 `cleanup_abandoned_socket`；InquiryBridge、活动 producer、角色控制器与 Worker 的转达（5-B）及活动迁移与旧通道删除（5-C）接在这份 API 上。

两处如实说明：`queue-full` 是防 stalled owner 的防御性事实（容量等于 32 问预算，正常路径先触 `inquiry-limit`，测试以预填队列覆盖该路径）；跨平台布局与清理原语在 macOS arm64（Darwin 25.6.0）实测，Windows 与 Linux 未在本机验证，`_capture_socket_fact` 推导失败时保守拒绝。

## 清理与材料

任务短根为 Host 创建并登记的 `<task-root>`（`/private/tmp/a255a-88dphywi`）；`TMPDIR`/`BUDDY_CHECKS_TMPDIR` 指向 `<task-root>/t`，一次性材料在 `<task-root>/m` 各新子目录：`smoke-1`（3 方法 CRM 跨进程冒烟探针及其 peer 私有家目录）、`run-subprocess-1/2/3`（子进程测试三轮原始输出，含失败迭代）、`run-focused-1`（最终 109 项聚焦输出与生命周期证据行）。未执行任何手动删除命令，未删除或重建旧副本；测试私有根由夹具自行收尾，被测端点按自身生命周期清理。Host 验收后按该确切根回收；其他会话对象未动。

## 交付 commit

隔离 worktree 内基线 `2e6c2db` 之上两笔提交（不在受管检出创建或切换分支）：首轮新增五个文件，固定审查 continue 再加一笔修正；提交哈希以整合登记为准（worktree 内 `git log 2e6c2db..HEAD`）。完成即停，等待 Host 固定审查。

## 2026-10-07 固定审查 continue：三处修正

固定审查不通过，原 run、原配置、原 writeScope 继续，Host 不代改。三处缺陷与修正（修正前行为均为实测复现或变异证明）：

1. **受理只按 questionId 索引。** 同 requestId 改 questionId 会再次入队并回 queued。修正为双索引：`_admitted[questionId]` 存该问题已受理的 payload digest，`_requests[requestId]` 存每次实际观察到的 requestId 绑定的问题；已提交 requestId 按整体 payload 幂等/冲突（换 questionId 同为 `request-payload-conflict`），问题级冲突独立为 `question-payload-conflict`，被拒的 requestId/questionId 不入索引、仍可重试。新增进程内逐队列大小断言与真实传输上 peer 恰消费一条的测试；变异副本（`_requests.get` 恒 None）命中队列 2!=1 断言 FAILED，原件通过。
2. **客户端超时只在同步返回后判定。** 修正为真正有界的整条 connect+调用：C-Two 0.6.0 公开客户端表面无逐调用超时（connect 与调用代理无 timeout 参数、`set_transport_policy` 只调分块阈值，已在模块 docstring 说明该选择与边界），故用标准库机制——每次调用由一个守护 worker 执行，调用方只等到窗口截止；超窗是 `_LiveCallExpired` 明确事实（`transport-window-expired`），槽位（每通道 `MAX_INFLIGHT_LIVE_CALLS=4` 的 BoundedSemaphore）占满时整窗等待后报 `transport-busy`，重复超时最多累积固定数量守护线程，`close` 与进程退出不等待任何被卡调用。新增真实延时 stub 与真实 C-Two 阻塞 peer（夹具 `StallingLive`，每操作 stall 2s）对 300ms 窗口的时限返回/多次仍有界/排空后干净 stop 测试（证据 `stalling-endpoint.json`）；变异副本（`_call` 改回同步直连）命中 `reply-invalid != transport-window-expired` 断言 FAILED，原件通过。服务端受理窗口核对与首次设置先于一切连接/注册不变。
3. **分页首条不检查、最终帧不复核。** 合法单字段范围内的转义文本（4000 字节答案等六个字段均逐字段合法）加 20 事件大 observation 可合成超 64KiB 帧，客户端 `reply-invalid`。修正为每一候选（含首条）都以携带最坏小字段（较长 `false` 字面量与含溢出理由的 unavailable）的完整探针快照量帧，最终 canonical 整帧复核；溢出时空页显式报 `frame-too-large`（unavailable+reason）且 truncated 如实，绝不裁剪或静默丢事实，收窄 fields/点查仍完整取回全部事实，afterSeq/limit/fields 语义与公开限额未动。新增组合溢出测试断言 server 返帧有界、客户端可解码、截断/不可用事实明确；变异副本（`_frame_fits` 恒 True）命中 `frame-too-large` 断言 FAILED，原件通过。

**修正后验证。** 聚焦运行 `uv run --frozen python -m unittest -v buddy.harnesses.test_c_two_live buddy.harnesses.test_live_channel buddy.harnesses.test_run_contract cli.test_repository_hygiene` 共 120 项全部通过：新模块 40（进程内 30 + 真实子进程 10，较首轮 +6：requestId 绑定进程内与真实传输两份、溢出组合、整窗约束、槽位有界、阻塞端点），相邻 `test_live_channel` 44、`test_run_contract` 31 未动通过，卫生检查 5。三处变异（材料 `mutation2-1/`，各含驱动脚本与输出）均被对应新测试抓住，原件全过。未跑完整检查、未启动 harness 或模型；旧材料全部保留，本轮新实验在 `<task-root>/m` 新目录：`mutation2-1`、`run2-subprocess-1`、`run2-focused-1`；`TMPDIR`/`BUDDY_CHECKS_TMPDIR` 仍指 `<task-root>/t`，未执行任何手动删除，无遗留 peer 进程。

## 2026-10-07 第二次固定审查 continue：三处缺口

第二次固定审查不通过，原 run、原配置、原 writeScope 继续；一轮的三处修正原样保留。Host 以同一份最新真实函数、不启动 C-Two/模型的探针核出缺口（Host 证据在其实施检出 `tmp/adr025-host/step5-20261007-014905/5a-fixed2-consumer-gap-probe.{py,json,log}`）。已对照现有消费者核实参考语义：`InquiryBridge._ask` 先落 journal、失败即 `journal-unavailable` 绝不报 queued（"Never report a queued question the journal did not durably record"），其 `_question_value` 携带 `reason` 与 `delivery`；黑板 `_import_snapshot_journal` 实际消费 `LiveSnapshot.journal`（`fields=(inquiries,)` 时由共享绑定携带）。

1. **requestId 别名无上界。** 同 questionId 同文本挂 129 个新 requestId 全部 queued、`_requests`=129。修正为：两个提交索引（`_admitted[questionId]=digest`、`_requests[requestId]=questionId`）与待决槽 `_pending` 共同满足 `len(已提交)+len(待决) ≤ 32` 的不变式，任何绑定不做驱逐；满预算时新 requestId 明确 `request-limit`（已知 id 仍幂等/冲突，同问异 id 待决合流不加索引）。预算取现有每运行 32 的请求/问询口径：生产 CLI 的 inquiryId 即 requestId，公开 CLI 的 32 问口径不变；内部传输的不同 ID 别名共用同一预算，理由是别名的唯一来源是调用方重开 id，超出即属滥用，且槽位（队列容量 32）与索引同量级。索引/队列有界由测试逐项断言（含满预算重放与已知 id 正确性），不靠注释。
2. **入队即回 observed/queued 伪造已提交事实。** 修正为标准库的公共 owner 应答机制：`request` 校验入有界队列后，在原 transport 窗口内以一个 `threading.Event` 等待 `settle_request(request_id, reply)`（本地方法，不是新 RPC 操作；`consume_request` 仍只返回 `LiveRequest`）。settle 的 observed 回复（最早为 owner 在原生桥实际 journal 提交后的 `queued`）才发布首个 InquiryState、绑定两索引并把 `{questionId, duplicate:false}` 补进 correlation（owner 自带 correlation 时不覆盖）；settle 拒绝（journal 不可用/not-ready 等）原码原样回传、清槽不提交、可重试；窗口期满在无 owner 应答时明确 `unavailable`，未被消费的队列项由 `consume_request` 丢弃不再触原生；`close` 唤醒全部等待方为 `channel-closed` 且其队列项过期作废。请求/问题幂等以 `_pending`（待决）与两索引（已提交）区分，并发同问合流到同一槽保证无第二次交付；无新原生读线程，RPC 线程不触原生。重放投影把已发布 InquiryState 的 `reason`/`delivery` 带入 `nativeCorrelation`（对应 `_question_value` 读法），queued 不升 delivered。
3. **journal 事实源缺失。** 旧 `LiveSnapshot` 在 `fields=(inquiries,)` 时携带 `LiveJournal`（available/reason/entries/rejections）且黑板实际消费；新增本地 `publish_journal(LiveJournal)`（Mapping 则经其严格模型），observe 选 inquiries 时携带，纳入每候选探针与最终整帧预算（溢出仍显式 `frame-too-large`+truncated，可收窄 fields 取回）。事实完全由 owner 输入：未发布即缺，不推断可用、不编零、不与问询状态混用；真实 producer（现有受锁读与有效记录投影）由 Host 接线时提供。

**修正后验证。** 聚焦运行同口径共 128 项全部通过：新模块 48（进程内 37 + 真实子进程 11），原有 40 编号全部保留（9 项适配受理-结算与 journal 机制，编号表逐条登记），新增 8 项（结算前无事实、owner 拒绝可重试、超窗丢弃不迟达、close 唤醒、并发合流、requestId 预算、journal 事实源、真实传输 owner 拒绝-重试）；相邻 `test_live_channel` 44、`test_run_contract` 31 未动通过，卫生检查 5。peer 夹具新增 `settle`/`pendingCount`/`publishJournal`/`autoSettle`（模拟 owner 循环，测试替身）命令。未跑完整检查、未启动 harness 或模型；旧材料全部保留，本轮新实验在 `<task-root>/m` 新目录：`run3-subprocess-1..4`（含失败迭代）、`run3-focused-1`；未执行任何手动删除，无遗留 peer 进程。

**Host 问询补（observation 读事实）。** 审查问询指出第三项同族缺口：既有 `ExistingLiveChannel.observe` 在选 observation 且无读源时回 `observed=False/reason=observation-unavailable`、真实读失败保留 `observed=False/reason/error`，而新 Endpoint 一概 `observed=True if selected` 且无发布失败事实的入口。已修正：新增本地 `publish_snapshot(LiveSnapshot)`（现有格式，不加 wire 模型或参数矩阵）采纳 owner 实际发布的 `observed/reason/error` 与 journal/observation 值——发布失败原样携带且不供 look-alike 观察值、保留最后活动不伪造成功；`publish_observation` 成功即确立 True；从未发布时 `_observation_facts()` 回既有的无来源事实，绝不推断成功；fields 不选 observation 时不携带其读失败；三个读事实全部计入每候选探针与最终整帧。新增对照测试 `test_observation_read_facts_match_the_existing_three`。接口无问题：公开三个 RPC 方法不变，无新 wire 字段。

## 2026-10-07 第三次固定审查 continue：四处缺口

第三次固定审查不通过，原 run、配置、writeScope 继续；Host 以真实函数无模型/无 C-Two 注册探针复现四处（材料在其实施检出 `tmp/adr025-host/step5-20261007-014905/5a-fixed3-owner-probe.{py,json,log}`），前几轮修正全部保留。四处与修正：

1. **close 后仍可消费。** close 同时置 `expired=True`、填拒绝 reply 并清 `_pending`，但 consume 只在 `reply is None and expired` 时丢弃，故 close 后已入队请求仍返回给 owner（探针 closedRequestStillConsumed=true）。修正：只有活槽交给 owner——`not expired and reply is None and _pending[rid] is slot` 三者同时成立；关闭、超窗未消费、已结算（含 owner 未消费即 settle 的接线错误情形）、不再属于当前 pending 映射的陈旧槽一律丢弃并清除其全部别名映射，已填拒绝 reply 不解除过期门。
2. **合流别名未绑定。** 两个 rid 并发同问合流都能收到 observed queued，但 settle 只绑 r1；r2 随后改 questionId 仍二次入队。修正：合流本身是预算内别名——join 时检查 `len(已提交)+len(待决) ≥ 32 → request-limit` 并把新 rid 映射到同一 pending 槽；settle 绑定该槽当前全部别名并逐个出清，每个成功受理的 requestId 都落入提交索引、保留整 payload 冲突门，不驱逐不绕过预算。
3. **settle 覆盖已发布状态。** owner 先经 journal 回调 `publish_inquiry_state`（带 delivery/limitation）再 settle 时，裸 InquiryState 覆盖已发布状态、delivery/limitation 变 None、seq 无故增加。修正：settle 只应答、绑定、并在该问尚无任何已发布状态时发布首个最小状态；已有 owner 发布（含较新 answered）原样保留不重排 seq；`reply.state=None` 时按实际 `reply.status` 投影，不把已观察 answered/delivered 默认为 queued；correlation 携 reason/delivery 仅在有真实来源（owner 自带或已发布条目投影）时出现。
4. **consume_request(0) 不排空。** 先判 `remaining<=0` 直接返回 None，与原 `queue.get(timeout=0)` 非阻塞取现有值不同（探针 zeroTimeoutConsumesQueued=false）。修正：窗口为 0 时用 `get_nowait` 非阻塞排空（每次调用取一条、空即 None、不等待），正数仍是一个总等待窗口，过期丢弃预算不变，不靠调用方微小睡眠。

**修正后验证。** 按指示只重跑新增/改变的 backend 测试与真实 peer 队列案例：`buddy.harnesses.test_c_two_live` 全模块 53 项全部通过（进程内 42 + 真实子进程 11；较上轮 +4：close/死槽不交付、零等待排空、满预算合流拒绝、settle 不覆盖已发布状态；并发合流用例正文随预算别名适配并加双 rid 绑定与改问拒绝断言）；live_channel 44、run_contract 31、卫生 5 未变未重跑（上轮 129 全过记录在案）。未跑完整检查、未启动 harness 或模型；旧材料全保留，本轮新实验在 `<task-root>/m/run4-unit-1`；未执行任何手动删除，无遗留 peer 进程。

**现有消费者对照与接线点（如实：当前仅测试使用方）。** 本后端尚无生产调用方；`settle_request`/`publish_journal`/`publish_inquiry_state`/`publish_activity`/`publish_observation` 是 Host 接 InquiryBridge、活动 producer 与角色控制器时的调用点（owner 仅在原生桥实际 journal 提交后 settle `queued`，journal 事实用现有受锁读投影发布），`cleanup_abandoned_socket` 归持有方收尾；生产 `HarnessRunLive`/`WorkerRuntimeLive` contract、`CONTRACT_VERSION` 0.28→0.29 与公共接线仍归 Host。
