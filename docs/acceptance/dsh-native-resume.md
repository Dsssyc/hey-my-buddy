# DSH 原生续接：执行计划与验收记录

当前整合状态：R1 的停止缺陷已在原 run 修正，固定产物 da175238 已独立核对、整合至 51a17bd，并按登记的确切路径回收受管检出与任务根；两层停止的完整证明仍待 Worker 冒烟。R2 用量读取缺陷在原 run 修正，固定产物 76560ad9 的 reader 与 native_resume 共 31 项独立通过，尚待最终变异和整合。R3 固定产物 c123f18d 的 12 项独立通过，但测试永久固定产品能力为 false，需在原 run 修正后复核。付费冒烟未授权、未运行，产品能力声明仍为 false。

Host 公共接线登记：独立诊断证明旧 DSH 回合上报 storageOwner=buddy-attempt 时，在候选能力开启后仍被共同判定选为 native-session，随后只能在原生模块因缺少 goal binding 失败。为满足升级后的重建规则，沿用既有 native_home_changed 判断，将原 Codex 的 buddy-goal 归属条件同时用于 DSH；仍用 private-native-home-required 原因，没有新增选择器、CLI 或 schema，其他三个 harness 的判断条件不变。该公共文件不在 R2/R3 写入范围，由 Host 整合；R3 将补新目录的正向事实与旧目录的重建断言，并去掉永久固定能力 false 的测试。原基线诊断失败与各交付初版结论均留存。

2026-10-08，Codex Host；hostId沿用codex-adr025。输入socu/buddy-core的fbfa8eac，包含已验收的ADR-025第五步、ADR-027，包/契约0.29.0、schema15。实施分支socu/dsh-native-resume，检出为~/.codex/worktrees/dsh-native-resume/hey-my-buddy，不推送。先读[第五步Host验收](adr025-step5-host-review.md)，按其中已经确认的缺口处理第一部分，再实施DSH续接；第一部分不设单独外部验收停点。最终提交后停下，等Claude Code Host验收。

## 范围与已有机制

运行模块只产生原生事实，续接选择仍由黑板和角色决定。复用workflow的原生续接选择、配置/版本/账户与已有回合核对，run_execution的RunContinuation构造、角色收集与两层停止，turn_io的输入摘要和回合/检查点验证，private_dirs的goal_root/native_root/attempt_root以及现有留存、回收与备份白名单。DSH只接公开ACP session/resume和它自己的会话事实，不另写一套DSH续接选择器、不另造运行通道。Codex、ZCode、Claude Code的行为、快速路由、审阅、公开CLI与schema不变；如现有机制不能满足而确需改变这些边界，停止说明。

DSH的ACP客户端已经提供resume_session，当前运行模块明确拒绝native-session、适配器native_resume为false；既有会话记录读取按会话累计，不能直接用于恢复后的本回合。当前角色根据已声明的原生续接能力选择按微任务的native根，凭据与控制材料仍由attempt_root持有。实现分阶段留住false能力声明：候选原生模块先经模拟程序及私有探针验证；付费核对通过后才启用产品的native-session声明及相应的共同角色路径，不在核对前发布能力。

记录里的位置一律用~或占位符；原始清单、固定源码审查副本、变异、命令日志与SHA清单在本检出被忽略的tmp/dsh-native-resume/<phase>/。Host开始时创建并登记一个短系统临时根，微任务各有开始时创建、登记确切路径的短根及t/m子目录。TMPDIR与BUDDY_CHECKS_TMPDIR指入其t；所有一次性材料在其m，交付报告确切根给Host，入库记录改用<task-root>。

## 第一部分：第五步留下的清理

| 微任务 | 唯一写入范围（均相对检出） | 成果与验证编号 |
| --- | --- | --- |
| C1 Worker主循环接线 | 新增tests/python/buddy/runtime/test_worker_live_wiring.py；docs/acceptance/dsh-native-resume-c1-worker-wiring.md | V-C1至V-C4：真实Worker tick发现就绪后登记；合法nonce下续租、对账成功后重新登记；退出关闭Worker端点；控制器结束后只清其捕获端点。复用LiveActivityForwardTests夹具、记录attach/detach的客户端与真实C-Two端点，不能只调用WorkerLiveRuntime。每处独立去掉目标接线必须使实际断言失败。 |
| C2 公共事实防护 | 新增tests/python/buddy/harnesses/test_pending_ready_receipt_guards.py、tests/python/buddy/roles/test_checkpoint_shutdown_guard.py；docs/acceptance/dsh-native-resume-c2-guards.md | V-C5、V-C6：两种排队中同号异内容冲突；V-C7：完成回执inputSha256失配；V-C8、V-C9：就绪材料非普通文件、超过上限；V-C10：只有检查点、没有完整回合，任一停止层未知或未确认时不可续接。真实调用被测入口，复用已有夹具；每个目标条件单点去掉后失败。 |
| C3 退休旁路与重复定义 | protocol/usage.py、buddy/roles/turn_io.py、buddy/roles/worker_services.py、buddy/harnesses/c_two_live.py（以上均在src/hey_my_buddy下）；tests/python/protocol/test_usage.py、tests/python/buddy/roles/test_worker_services.py、tests/python/buddy/harnesses/zcode/test_zcode.py、tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py；docs/acceptance/dsh-native-resume-c3-cleanup.md | V-C11：删除无生产调用方read_sidecar及独有常量/测试，保留仍用的用量归一化；V-C12：inquiry_paths不再写inquiry.json，测试改用真实返回控制材料，旧尝试文件仍能由现有回收函数处理；V-C13：回合提示去掉撤回说法；V-C14：传输时间窗100至5000毫秒统一引用protocol/inquiry.py的既有定义，不再重复；harnesses/live.py的公共整合由Host处理。聚焦验证受影响模块、旧文件回收及边界值。 |

三项从计划提交的同一固定基线在隔离受管worktree路由，可并行；C1/C2只加测试，不碰生产公共文件；C3只做列出的退休和等价适配，不削弱原保护。公共文件、角色、注册表与总体记录的整合归Host，接口缺口由交付提出。非普通文件条件若同时被底层安全打开方式拒绝，测试可以隔离底层打开原语并使用真实文件描述符，保留目标函数逻辑，明确这一验证边界；不把冗余防护、装载错误或超时当成目标条件已由断言守住。

Host逐项核对固定artifact、累计patch与范围，独立运行聚焦原件和目标变异；C3不因删除无使用方代码而重建测试它的旧载体。编号以真正加载的TestCase.id为准：原3049项清单留tmp，变化/删除/新增单列；未变以集合相等证明。旧测试读取inquiry.json不是生产使用方，必须迁移；private_dirs与private_migration为旧尝试回收而读取该文件的代码保留。

## 第二部分：DSH原生续接

| 微任务 | 唯一写入范围 | 成果与验证编号 |
| --- | --- | --- |
| R1 无模型原生核对 | 新增tests/probes/dsh_native_resume_preflight.py；docs/acceptance/dsh-native-resume-r1-preflight.md | V-R1：已安装dsh --profile acp，在微任务私有sessions根新建空会话，关闭并确认进程组消失，再由另一进程用同一存储和session/resume恢复。核对能力、session/list/配置读回、传入本次MCP服务、会话标识来源、同一sessions根、错误分类和停止；全程不发送prompt、不调用模型、不读用户凭据内容。 |
| R2 DSH候选原生运行与用量 | src/hey_my_buddy/buddy/harnesses/dsh/native_run.py；必要的新DSH内部模块；tests/python/buddy/harnesses/dsh/test_native_run.py、其DSH原生fixture及新增DSH续接/用量测试；docs/acceptance/dsh-native-resume-r2-native.md | V-R2：初始、新建重构、原生恢复三个已有模式；V-R3：恢复时完成/问询工具、活动和共同实时通道重新绑定本尝试；V-R4：model/effort读回、工具范围启动配置及session-title-llm/session-telemetry-otel关闭保留；V-R5：原会话不存在、拒绝或身份失配都失败，没有新建回退；V-R6：本回合仅新增记录用量，无法证明边界则未知。只在DSH范围实现，不启用能力声明。 |
| R3 共同角色、存储与黑板核对 | Host整合公共文件；路由微任务只新增tests/python/buddy/roles/test_dsh_resume_storage.py、tests/python/blackboard/tasks/test_dsh_resume_selection.py；docs/acceptance/dsh-native-resume-r3-integration.md | V-R7：复用共同resumeMode选择，完整回合/停止、配置、账户与版本的资格和重构原因；工作区/私有会话绑定失配的明确失败留在原生模块，与Codex/ZCode一致；V-R8：同一微任务两尝试共用native根、不同微任务隔离，凭据/启动补丁/日志仍按尝试，原生存储排除备份并按既有规则留存和回收；核对三个其他harness、快速与审阅未变。 |
| Host真实核对与启用 | tests/probes下本次DSH专用冒烟脚本；总体记录、注册表/DSH适配器的必要整合归Host | V-R9：用户另行批准的2个很短DSH回合，第一回合埋下随机内容，第二回合原生续接并证明能读取它；保存第二次真实输入，确认未重放该内容、旧摘要或消息。核对前后同一原生session、签收和工具事实、两层停止及逐回合用量；成功后才声明native-session，并聚焦能力/共同路径测试。 |

R1在第一部分整合后开始；无模型结果若不能证明跨进程恢复或新服务/配置能挂载，先停止说明，不以SDK、安装包静态分析或重放历史来代替。R2随后按实际公开ACP结果实现；R3依赖候选接口，公共缺口由Host先统一修改、提交，再由原run continue核对。两项可在不写公共文件的范围交错进行，不能覆盖对方文件。实际写入范围在每次submit中逐文件冻结，新增模块只在对应DSH范围并经scope-amend登记，不把表中的“必要”视为无限写权限。

存储选择：微任务原生sessions放在既有native_root(<state>,dsh,<taskId>)之下；每次尝试的DSH_HOME、ACP profile、启动配置patch、MCP描述、frame/log证据仍在attempt/invocation私有目录，session-persistence-jsonl的公开root配置指向前者。设置、凭据保留原路径，由DSH自行读取，本项目不读内容。这样共享的只有恢复所需会话状态，不把本次凭据或启动补丁留成跨尝试材料。继续沿用通用私有目录、备份和回收规则，不增加schema。

用量选择：启动恢复prompt之前先读取目标会话的可选私有记录，冻结已有原生记录标识/顺序边界；停止后仅投影可证明为新增的记录，复用protocol.usage的原生字段归一化和现有DSH流式zstandard读取。不能用累计token相减猜测本回合，缺标识、记录重写/缩短、读取缺失或边界不可靠时保留未知/部分事实；不得把旧错误、旧assistant消息或累计usage当作本回合新增事实。初始回合仍采用现有的本会话读取，其他harness的用量口径不改。具体原生记录标识在R1/R2证据里登记，不能为增加可知字段引入未验证假设。

恢复失败选择：已经选择native-session后，会话不存在、原生拒绝、指定会话/工作区/事实失配时如实失败；不悄悄转为session/new。原生之前不满足共同资格时由现有黑板选择reconstructed-new-session并写原因。完成签名、inputSha256、完整执行身份、私有会话归属与两层停止独立核对，未知不等于停止。

## 微任务与Host的操作规则

每次首次submit的adapter/provider/model/effort四项都省略；返修的原run continue同样不指定。确需换buddy用reroute；只有供应方不可重试的限流，按用户许可在原run用完整配置继续，登记被限流与替代的完整配置、路由决定编号、理由。容量等待或慢不据此换buddy。新建一个宏任务，全部微任务挂同一objective，Host负责方案、整合、固定产物审查与验收。

每个任务描述都写明：固定基线、唯一可写范围与公共整合归属；只跑受影响测试、不跑完整检查；不删除任何文件或目录、不写手动清理命令，一次性材料只在已登记短任务根内，报告确切路径；已有生产/测试生命周期自动收尾照旧，不能为保留材料关闭停止与端点回收；受管检出共用用户仓库stash/分支/标签，不使用git stash，不新建、切换、移动或删除分支与标签，改动留工作区由黑板封存；记录/夹具位置用~或占位符；不调用用户安装版harness/模型、不安装升级、不改用户配置/登录/凭据、不读凭据文件内容，R1仅增加本次明确授权的无模型ACP核对。

范围内代码、测试或行为缺陷打回同一run继续，Host不代改；一句话、Host掌握事实且不改变做过/验证过结论的记录更正直接改并登记。验证结论变化与掩盖违规仍打回。每次独立审查保留固定源码与真实检查、单点故障日志，不能仅凭完成工具、RPC或零退出码认定交付。内部验收后由Host核对回收计划并按确切路径回收检出/任务根，其他会话对象不动。

所有测试子进程先清继承的BUDDY_*、ANTHROPIC_*、VIRTUAL_ENV、UV_PROJECT_ENVIRONMENT及源码/venv固定变量，再设置私有状态与运行时根、BUDDY_DEV_SOURCE与本检出绝对PYTHONPATH。Native无模型/付费探针保留native_environment对HOME/locale/代理等的既有处理，DSH_HOME与会话/证据目录私有，不把日常配置或登录搬到空私有HOME里。日常运行时保持0.27.0；路由微任务使用正在运行的黑板，候选0.29.0源码验证在私有环境，二者证据分开。

## 付费与最终验收门槛

2个DSH模型回合没有获得本次授权。在模型前准备完脚本、固定源码、无模型检查和最小输入，停下向用户说明次数/用途并询问；任何因探针自身错误所需重跑也重新询问，不能援引ADR-025旧授权。核对未通过则能力仍不声明，并记录事实、原因与剩余工作。

全部整合、真实核对和能力启用之后，在最终代码提交以默认并行数运行一次 `uv run --frozen python -m hey_my_buddy.cli.checks`，记录提交、完整输出与退出0。若机器负载下出现已登记的检查运行器并行测试或ZCode两个取消测试失败，保留第一次结果，待空闲单独重跑那个文件再判断，不改写、不降低默认并行数。失败引出新修正时只在新代码确实需要时再完整检查；其后只改记录不重跑整套，但运行cli.test_repository_hygiene及实际受影响的读取文档测试。

最终列出真实测试编号差异/未变集合、每项变异的实际失败、无模型与模型次数/版本/会话/输入/工具/用量/停止证据、没有生产使用方的新增字段/类/参数清单、Host整合更正与确切回收结果。对外可见变化交Claude Code Host维护参考与ADR等文件：DSH满足资格后的原生续接、按微任务私有的会话存储、按回合用量与明确恢复失败；契约与schema保持现状。最终提交不加AI署名，不推送，停止等外部验收。

## 当前状态

执行计划7391081已提交，宏任务obj-36c065d5-68b2-4e2d-abd4-e10512124d4c下C1/C2/C3均已内部验收、整合并按确切路径回收。第一部分完成；Host独立无模型核对已通过，R1工具产物已退回原run修异常边界和停止口径；R2候选实现与R3既有共同机制的测试各自路由到隔离检出，尚未验收整合。2回合付费冒烟未开始，产品native-session能力仍未声明。以下保留各次整合的实际经过与当时的验证边界。

Host整合登记：开工核对确认时间窗重复的是protocol/inquiry.py与buddy/harnesses/live.py，c_two_live.py原本已经引用后者。保留两侧接口层protocol/inquiry.py的一份定义，Host仅改live.py的常量来源，取值不变；该文件不在并行微任务写入范围内。原C3任务仍保留，固定产物审查时按当前公共来源核对，不让其扩大写入范围。两项现有窗口边界测试与cli.test_repository_hygiene共7项通过（0.203秒、退出0），原始命令/日志留tmp；不重复完整检查。

Host整合C3固定7b4d8e94、artifact31285f4f-887a-46fb-aaf2-0c59b0123855：8个产物路径逐字整合，公共时窗另由63e6248统一在protocol/inquiry.py，原live/c_two_live的消费符号和100/5000取值不变。C3对旧基线重复定义的剩余说明保留为历史，当前已收尾，不需要改动其数值边界断言。Host固定整合副本165项通过50.689秒；M1恢复遗留文件写入、M2/M3改实际公共上下限、M5关闭旧fallback回收、M6把answered当待答，各自独立产生真正断言失败，日志/patch/SHA留tmp。M4去掉显式bool子句在100下界下没有可观察差异，不称被捕获；其原防护未改。Host首次M6脚本因选择器匹配两处而在写变异前断言退出，另起命名副本只匹配pending_inquiries后实际断言失败，没有覆盖原日志或修改生产。C3旧/新编号171/170（含54项只读参考），变化5旧/4新、未变166集合相等；4个sidecar测试随没有调用方的载体退役，另一个旧编号是问询措辞改名。微任务模型运行不计入DSH原生核对次数；未进行DSH原生核对或付费冒烟。

Host整合C1返修固定7d675ac6：两路径逐字整合，5个新增实际编号；独立固定整合副本5项通过2.670秒，四份单点移除接线均有真实断言失败，所有8个本次captured端点在失败后的夹具收尾后消失，独立组停止已确认。初稿48261f8保留为拒绝历史，既有/未证明归属的共享目录残留不追查、不删除。Host处理C1范围外的既有供体LiveActivityForwardTests.renewal夹具收尾：Worker创建后登记worker.live.stop为cleanup，避免原测试留下注册；只改这一个测试夹具行，不改生产或其他harness行为，属于公共整合。新增测试的原件/变异证明先于此行在固定副本完成，不覆盖其历史日志。

C1整合后的供体收尾改动经受影响模块验证：供体11项、C1新增5项及仓库卫生5项合计21项通过3.395秒，退出0；不重跑C1不变变异。C2固定7e386010在V-C9被拒：大文件两层guard单独删除时旧测试不失败，合删才红；原run在不指定配置的continue里补读取前大小拒绝与fstat后实际文件增长两个独立用例，原红绿证据保留。第一部分尚未完成，DSH无模型/付费核对未开始。

Host整合C2返修固定ab592692：三个新增文件逐字合入、13个新增测试。初稿12项在固定整合副本通过3.658秒，返修只改ready reader类，其他冲突/回执类AST及检查点文件字节相等；返修ready类3项通过0.003秒，两道上限分别单点删除后各恰1项断言失败、另2项保持绿。Host此前独立核对另九处目标变异全部真正断言失败（FIFO、排队两种冲突、回执摘要/签名、检查点四种合取），不以导入错误或多点删除代替。旧M9a/b未红与M9ab合删红保留，不改写。C1内部验收后检出/确切根已回收，52份命名原始材料与逐对象停止/端点日记保留；第一次cleanup-plan遭REVISION_CONFLICT，重新读当前修订并用新命令核对回收，错误原回应保留。没有清理任何未证明归属的共享目录残留。第一部分代码与测试整合完成；完整检查留到DSH接线最终代码，第二部分现可开始无模型核对，2回合付费仍未授权。

第一部分实际全局编号：3066项/192模块、零装载错误，原3049项减少5旧、增加22新，未变3044集合相等；原始完整编号与每个差异留tmp/cleanup-test-ids1.json和cleanup-id-accounting1.json。C1/C2/C3均内部accepted并按确切路径回收检出与短任务根，分别保留52/79/25份命名证据；Host自己的核对根仍保留供后续使用。R1在第一部分之后从aa98c88路由提交（run b8f6ed77-c4b1-46f4-a364-70bbc09aeb26），四个配置字段全部省略，只允许ACP无模型核对；R2/R3与2回合付费冒烟未开始，产品能力仍未声明。

R1首回合因路由所选zcode/zai-api/GLM-5.3-Flash/max的供应方stream EADDRNOTAVAIL失败，quotaFailure为空，两层停止已确认；这不是DSH恢复核对的失败结论，也不按限流许可改配置。Host只读核对受管检出无改动、任务根仅有空t/m，没有可验收的探针或原生核对材料；没有据此推断原生能力已通过。使用原run的continue恢复，adapter/provider/model/effort四项继续全部省略，沿用路由决定dec-814189fa-9cb5-4f94-988b-ea22863895d4与同一受管检出、确切任务根，保留第一回合失败回应。恢复输入收紧到原两文件范围、公开ACP与已有客户端，明确不补prompt、不播种会话、不调用模型；未换buddy、未取消或重开。原始回应及恢复参数留tmp/r1-result1-result.json与r1-recover1-params.json；尚未获得原生核对结论。

Host独立V-R1无模型核对：复用现有AcpClient、materialize_acp_profile、source_binding_rows、private_dirs.native_root和既有MCP测试服务，在<host-root>/m/host-native-preflight2中按微任务放置sessions，两个进程分别使用a1/a2的私有DSH_HOME、profile、patch与frames。第一个进程new返回cdefaada-86de-4b74-bb9b-369fe55cad85，关闭且确认组消失；第二个进程向同一存储发送此标识的resume，配置读回与close都正常，组同样消失、leader退出0。新MCP服务两次都实际收到initialize、notifications/initialized、tools/list，只证明无模型挂载，不称工具执行证明。ACP声明resume/list/close，agentInfo为deepseek-harness-acp 0.0.1（这是ACP应用版本，不当作DSH发行版本）；resume响应只有configOptions，会话身份来自先前new与本次resume请求，未声称回读响应身份；两次list均为空，不靠list给恢复设置资格。共享目录确实生成该会话的session.v3.jsonl.zstd及session.lock，两个始终关闭的行按字面名称写入每次私有启动补丁。整个驱动只允许initialize/new/resume/list/set_config_option/close，没有prompt或authenticate，两个DSH原生进程、零模型调用；日常HOME、设置与凭据只按既有路径传给DSH。

Host独立探针首轮在导入pydantic前失败：Host错误地解析了虚拟环境解释器符号链接，使用基础解释器，没有启动DSH；纠正后经已准备的uv环境在新的命名目录重做，保留首轮错误，不覆盖材料。第二轮驱动退出0后逐项核对公开回应、请求方法、不同私有home、MCP方法记录及真实组停止，不仅凭退出码判断。原始脚本、位置、回应与退出码留tmp/host-native-preflight1-*、host-native-preflight2-*及对应Host专用根；根在创建时登记，不删除、不扫描其他会话。实际原生可行性已经由Host核对，因此R2可依据这些公开事实开始；R1的工具交付仍须固定产物审查，不能拿Host探针替它的源码验收。没有启用产品能力、没有进行付费回合。

R1初稿固定7e9f68e155db、artifact d01a92e1-4d7c-40b6-b28c-c12ec7611432仅改两份授权文件，累计patch范围与SHA及本回合系谱的停止确认均已核对。微任务的原生材料run1/run3各启动两次DSH ACP、未调用模型，另有discovery版本探测；run2在会话启动前因自身UnboundLocalError失败并保留。公开恢复事实与Host独立结果一致，补充确认已关闭会话才出现在list、恢复后effort不会继承前次会话内切换，因此候选实现仍须重新配置并读回。代码审查暂拒：A停止未确认时主流程仍可能启动B；AcpClient.start抛出LaunchOwnershipError时其自带句柄与停止证据没有入库；探针把同一原生进程的wrapper与组观察称作“两层停止”，实际未验证角色控制器外层。这些改变异常行为或验证结论，按原run continue返修，四个配置字段全部省略，不重做真实DSH、不取消重开；原固定产物和历史原生材料保留。

R2从d8fa086路由提交run 9ecbf183-04ba-4b90-98c0-b60fb1bfbe21，决定dec-8777a58e-ae54-4b7b-9394-873a6d030edc选择zcode/zai-api/GLM-5.3/max；R3同基线路由提交run 5295ef5f-5496-4770-a6de-a43312efaec2，决定dec-275642f7-e904-4354-a673-5bccc821cd7b选择zcode/zai-api/GLM-5.3-Flash/max。两份首次参数均完全省略adapter/provider/model/effort。R2只写DSH模块、其fixture/测试与本任务记录，公共文件不碰；R3只加已列共同机制测试和记录，通过测试私有的候选执行器走现有选择与prepare，不运行R2活动代码、不声明生产能力，因此这部分可与R2并行。DSH本次home/profile/patch/frame隔离的实际原生执行由R2核对，R3不把共同prepare的目录边界测试称作原生运行。开工源码核对还细分了V-R7：工作区绑定失配是既有原生模块的明确恢复失败，黑板没有为它另设自动重建，计划按“复用已有判定、不静默回退”的用户要求更正，不增加公共判定或schema。

Host固定核对R1返修da1752381df4：两路径按字节合入，没有Host代改范围内源码。独立受控检查在初稿实际产生两项目标断言失败，返修两项通过0.327秒；单独移除停止gate与启动所有权处理的两个命名副本，各恰有一项实际断言失败、另一项保持绿，源与日志SHA留tmp。另用真实普通Python子进程模拟包装停止未确认，Probe持有原句柄后终止它：alive/未退出/未确认变为gone/退出-15/已确认，PID与PGID仍在记录；零DSH启动、零模型调用。停止finding与入库口径均明确只覆盖原生leader/组，旧“两层”材料保留为更正历史。正常ACP调用段未变，已有四次微任务与两次Host独立ACP运行的事实继续成立，不重复原生核对。源码探针不增加TestCase编号，全局清单仍需与整合最终树核对；完整检查留到最终代码。

R2首回合失败代码duplicate-finish：完成工具后又安排工具，nativeFailure与quotaFailure均为空、系谱停止已确认；固定部分产物50a112299c5c只有八个授权路径，保持未验证、未接受。Host在该固定源码独立跑session_records 13、native_resume 15、native_run 63、session_storage 6，合计97项通过；没有完整检查或真实DSH/模型。原记录把新增模块的装载失败占位算作旧编号，这改变验证结论，连同正确交付末次完成要求在原run继续，四个配置字段全部省略；原duplicate-finish结果、部分产物及失败日志保留，没有限流配置覆盖。R2范围外的旧role-wiring断言和共同目录整合由Host处理并登记，微任务不扩大范围。

公共目录接线的验证：旧 DSH 尝试归属诊断在原源码实际失败（错误选 native-session），接线后实际通过并返回 reconstructed-new-session / private-native-home-required。首次聚焦命令误写卫生模块名 install.test_repository_hygiene，两项请求形状测试已通过，但整次命令因装载错误退出 1；该次不计通过，保留原日志，随后用实际 cli.test_repository_hygiene 及 Codex 续接文件复核。

公共目录接线复核：Codex 既有续接与卫生测试共 14 项通过（host-storage-owner-shared2，退出 0），原 Codex 私有目录与账户规则保持有效；第一条命令的装载错误不并入该通过数。
