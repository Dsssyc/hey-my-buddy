# DSH 原生续接：执行计划与验收记录

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
| C3 退休旁路与重复定义 | protocol/usage.py、buddy/roles/turn_io.py、buddy/roles/worker_services.py、buddy/harnesses/c_two_live.py（以上均在src/hey_my_buddy下）；tests/python/protocol/test_usage.py、tests/python/buddy/roles/test_worker_services.py、tests/python/buddy/harnesses/zcode/test_zcode.py、tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py；docs/acceptance/dsh-native-resume-c3-cleanup.md | V-C11：删除无生产调用方read_sidecar及独有常量/测试，保留仍用的用量归一化；V-C12：inquiry_paths不再写inquiry.json，测试改用真实返回控制材料，旧尝试文件仍能由现有回收函数处理；V-C13：回合提示去掉撤回说法；V-C14：传输时间窗沿用live.py的100至5000毫秒定义，不再重复。聚焦验证受影响模块、旧文件回收及边界值。 |

三项从计划提交的同一固定基线在隔离受管worktree路由，可并行；C1/C2只加测试，不碰生产公共文件；C3只做列出的退休和等价适配，不削弱原保护。公共文件、角色、注册表与总体记录的整合归Host，接口缺口由交付提出。非普通文件条件若同时被底层安全打开方式拒绝，测试可以隔离底层打开原语并使用真实文件描述符，保留目标函数逻辑，明确这一验证边界；不把冗余防护、装载错误或超时当成目标条件已由断言守住。

Host逐项核对固定artifact、累计patch与范围，独立运行聚焦原件和目标变异；C3不因删除无使用方代码而重建测试它的旧载体。编号以真正加载的TestCase.id为准：原3049项清单留tmp，变化/删除/新增单列；未变以集合相等证明。旧测试读取inquiry.json不是生产使用方，必须迁移；private_dirs与private_migration为旧尝试回收而读取该文件的代码保留。

## 第二部分：DSH原生续接

| 微任务 | 唯一写入范围 | 成果与验证编号 |
| --- | --- | --- |
| R1 无模型原生核对 | 新增tests/probes/dsh_native_resume_preflight.py；docs/acceptance/dsh-native-resume-r1-preflight.md | V-R1：已安装dsh --profile acp，在微任务私有sessions根新建空会话，关闭并确认进程组消失，再由另一进程用同一存储和session/resume恢复。核对能力、session/list/配置读回、传入本次MCP服务、会话标识来源、同一sessions根、错误分类和停止；全程不发送prompt、不调用模型、不读用户凭据内容。 |
| R2 DSH候选原生运行与用量 | src/hey_my_buddy/buddy/harnesses/dsh/native_run.py；必要的新DSH内部模块；tests/python/buddy/harnesses/dsh/test_native_run.py、其DSH原生fixture及新增DSH续接/用量测试；docs/acceptance/dsh-native-resume-r2-native.md | V-R2：初始、新建重构、原生恢复三个已有模式；V-R3：恢复时完成/问询工具、活动和共同实时通道重新绑定本尝试；V-R4：model/effort读回、工具范围启动配置及session-title-llm/session-telemetry-otel关闭保留；V-R5：原会话不存在、拒绝或身份失配都失败，没有新建回退；V-R6：本回合仅新增记录用量，无法证明边界则未知。只在DSH范围实现，不启用能力声明。 |
| R3 共同角色、存储与黑板核对 | Host整合公共文件；路由微任务只新增tests/python/buddy/roles/test_dsh_resume_storage.py、tests/python/blackboard/tasks/test_dsh_resume_selection.py；docs/acceptance/dsh-native-resume-r3-integration.md | V-R7：复用共同resumeMode选择，完整回合/停止、配置、账户、版本与工作区失配的重构原因；V-R8：同一微任务两尝试共用native根、不同微任务隔离，凭据/启动补丁/日志仍按尝试，原生存储排除备份并按既有规则留存和回收；核对三个其他harness、快速与审阅未变。 |
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

执行计划已写入；第一部分尚未提交微任务，DSH无模型核对、实现与2回合付费冒烟均未开始。本节在实际证据形成后更新，不预写通过结论。
