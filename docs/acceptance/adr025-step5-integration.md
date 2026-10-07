# ADR-025 第五步整合登记

2026-10-07，实施分支先从 c550d65 快进到 socu/buddy-core 当前的 43d8d63，包含用户指出的 3449897、第三四步验收合并 3fce9ac 及其后的文档提交。第三四步已由 Claude Code Host 验收，依据是 adr025-step3-4-host-review.md；本步尚未完成，日常运行时没有安装或升级。

开始时实际加载 Python 编号 2,785 个、175 个模块，装载错误和重复均为零；原始清单在 <checkout>/tmp/adr025-host/<phase>/step5-before-ids.json。第五步先整合清理批次，再改实时通道；清理无需单独等待外部验收，整步结束才停下。

三项独立清理微任务首次均未指定配置，走现有黑板路由：5-P1 回执核验，run 3b9a2305-b829-4e18-9561-a9f4c803d8f2，路由 dec-dcf974a5-f999-45e4-8388-828c74f62396 选 zcode / zai-api / GLM-5.3-Flash / max；5-P2 运行小段与 ZCode 阶段，run e5e8763a-b5eb-4e83-8952-512603026df1，路由 dec-ef85a626-94ef-4b96-95ec-746e5e8082be 选 zcode / zai-api / GLM-5.3 / max；5-P3 三类防护，run 939b427c-0fc0-4342-bc05-6e424fbf1ae9，路由 dec-6244b519-d4fb-4f4e-9652-b1416ac89a4a 选 zcode / zai-api / GLM-5.3-Flash / max。各自隔离 worktree、固定 43d8d63 基线、唯一可写路径已写入任务描述；公共值、角色与注册表归 Host。三个任务短根由 Host 创建并登记，Worker 不手动删除、不切分支，临时材料不覆盖；验收后按确切根回收。第三个监测子代理因容量被拒，Host 改用原 run 的前台 await，没有重提或更换 buddy。

Host 先确认无生产调用方，再删除 structured_call.start/start_no_tool、旧结果 else/_collect_result 和 read_router_result；保留实际使用的冻结证据留存。控制器移除 before_try，prepare 变为无参数，所有现存启动点保持原位置与 FD 收尾；collect_controller 要求显式 stop，删除可省略 stop 的旧分支及 Node 例外说明。原只为断言拒绝而调用 read_only.start 的测试改用实际角色入口。两项仅覆盖旧结果读取的测试随删除退役；外层停止强制布尔的测试与替换链接拒绝的测试改用真实公共请求、结果和角色收集，保留正向对照。

Host 将角色中的四处厂商判断移到登记参数：冻结账户的读取规则，以及 WorkerReceiptOptions 的未知额度码过滤、只从已验证回合捕获会话、原生活动收据投影；各字段都有生产读取。BUDDY_PYTHON 经源码检索确认只有写入和放行，已删除全部生产引用；启动仍由明确 argv 的解释器、运行时身份与 PYTHONPATH 决定。相关测试不再断言无消费者的环境变量，私有分发回合改报真正的 sys.executable 来核对解释器归属。

Host 清理聚焦 15 个受影响模块，154 项全部通过，27.975 秒，私有检查根正常收尾；没有跑完整检查或调用模型。迁移链接防护的单点变异只去掉新角色读取中的 guard_private_path：原件 1 项通过，变异命中 ok != failed 的断言（0.227/0.346 秒），原始输出留 tmp。第一次探针在原件通过后误读 ChildOutcome.exit_code 属性而退出，不算变异证据；保留该脚本、通过输出与副本，另建新副本和新输出，用真实 returncode 完成核对，没有覆盖旧材料。

用户已明确批准 Claude Code 的两次最小真实冒烟（Worker 回合一次、审阅一次），尚未运行。将沿用本机已有登录，不设置 CLAUDE_CONFIG_DIR 或 CODEX_HOME，不登录、登出、改密钥或读取凭据文件内容；额度或登录阻碍按未验证记录，不反复尝试。

5-P3 第一份提交 30bc23f 的三份测试与两份记录在范围内，但另误写一个未跟踪 MD 到 <managed-checkout>/Users/<user>/…/docs/acceptance/，使黑板拒绝封存。Worker 如实披露并保留，没有手动删除；Host 核对黑板记录的唯一 blockingPath、普通文件类型及字节摘要，以 workspace-resolve restore 恢复该明确新文件，没有扩大写范围或触及受管检出以外。恢复后的固定 artifact 为 6f2f8367-e125-4a26-b4f7-b811ae5a6d85、提交 ed818685；误写文件已消失，黑板恢复登记保留。

Host 随后拒绝该固定交付：所谓原生停止未知的见证只把 continuation.resumable 改为 False/None，实际 groupState 仍 gone，而且测试仍要求整个 shutdownConfirmed=True；这不能证明“原生停止未知不是已停”，记录的相关验证结论也需更正。原 run 已 acknowledge rejected 再 continue，未改配置或重开任务；要求有效回合及 continuation.resumable=True 时，把原生 groupState 改为 unknown/running、外层保持 True，双层停止与会话可续接都必须 False，并对实际原生 gone 判定做单点变异。续接能力的 False/None 可另留，但必须按能力命名；只重跑相关角色测试，DSH 和 Claude 未改的检查不重复。测试代码与验证结论均由原微任务修正，Host 没有代改。

清理并行期间，5-A 从 2e6c2db 首次走路由提交新 C-Two 后端，run b321c8b4-0d8c-42ef-a382-eb01601edc4d、路由 dec-180b6537-20ce-4496-9b16-3d24d530e118；唯一范围是新后端、独立测试/测试 peer 与两份记录，不改现有公共值、角色、注册表或协议。生产接线与版本切换仍归 Host，清理批次完成前不整合实时通道改动；这一微任务不代表通道已经切换。新短根按创建时确切路径登记，Worker 继续不手动删除或切分支。

Claude Code 的已批准两次冒烟可以独立于尚在执行的清理微任务验证角色格式与交付识别，因此先在 2e6c2db 进行：Worker 8.251 秒、审阅 6.547 秒均成功，分别 Read 1 次，身份相等、结构化交付有效、原生与外层两层停止确认；没有重复模型。两次无模型发现中的第一份探针误取目录键名，在任何模型输入前退出，另建修正脚本与材料后才运行两次真实模型，错误留证不覆盖。细节在 adr025-step5-claude-native-smokes.md；这不是切换后的 C-Two 验证。

5-P1 首份固定 artifact 7547dcd3-9bd9-41db-9f1b-860986c3a791、提交 dbd0cbe 的累计补丁摘要与 8 条 scope 路径相符，Host 未整合。审查发现记录明确承认把两边边界判断“并轨”（detail、inquiryId 和非字符串工具名错误消息），不符合仅合并而不改行为的任务要求；通常铸造侧不会产生这些输入不能作为授权。另承认首轮变异日志同名复写，旧输出已不能复核。Host 拒绝并对原 run continue，要求一份公共实现用最小实际差别参数保留基线判断、错误码/消息/顺序，以新目录重做必要变异；违规披露保留、不恢复或追查丢失日志，没有取消另开或配置覆盖。

5-P2 的固定 artifact db7f7c44-a087-4ca9-b4b8-8e2de93e83be、提交 0917942 的 8 条 scope 路径与累计摘要 a40a2a46c3551dcb55b7746f9392bb261733532165f16a37441150ebef8b6b40 已核对；Host 正在新的 2e6c2db 加固定 scope 副本上复核全部受影响消费者与原始变异/顺序证据，尚未整合或签收。

5-P2 固定副本的 36 个受影响模块首次检查 34 个通过（680 项），两份 ZCode 测试因 Host 自建检查根过长而失败，73.722 秒，正常收尾；未写成通过。用检查运行器在系统临时目录创建并登记短私有根后，仅重跑这两份测试：基线和交付各 46 项问询、5 项本机 ZCode 假模型测试全部通过（11.445/22.147/11.232/20.528 秒），各根正常由运行器收尾，确认是 Host 环境问题。累计受影响 731 项有通过证据；独立只读审查逐段比对四个运行模块及 ZCode 阶段，未找到行为/异常/停止/写入顺序回归，16 个新记录字段均有读取方。原始三项变异日志确为断言失败，取消的既有见证也有原件通过。

5-P2 仍因验证结论拒绝并原 run continue：记录称零安装版 harness，但原始 255 项 ZCode 日志没有 skip，包含 5 个 InstalledZcodeTests，实际启动安装版 ZCode/Node、以私有配置和 localhost 假模型运行，不能写成纯模拟 CLI。该过程没有付费模型或真实凭据，测试沙盒拒绝写日常 ZCode 与 agent 目录；没有证据指向日常数据改动。要求只更正并如实披露，不重跑测试、不改已验证代码/测试；根路径的占位写法一并更正，空 .venv 留存不删。这会改变“做过什么、验证过什么”，按用户规则由原微任务更正，Host 不代改。

5-P2 更正轮固定 artifact c088e9cd-97c7-484d-92dd-9572d4681e3a、提交 7e1d4a2 与先前代码相比仅 MD 改动，源码、测试及编号表逐字节不变，不重跑检查。原 run 核对并撤回错误结论：安装版路径实际还包括 2 个 FreeProbePipelineTests，共 7 个测试，进程启动次数未留存，不能把 7 项写成 7 次进程。更正明确违反任务的安装版限制、基线绿日志未持久化的边界，保留旧 checkpoint 的错误历史和空 .venv 披露。Host 已按累计补丁应用全部 8 条路径，并与新固定提交逐字节相等；源代码及迁移防护验证沿用已核对的 731 项与原始故障证据，记录仅更正无需重跑。尚未整合实时通道。

清理批次补充整合：5-P1 同一 run continue 后逐侧保留 detail、空白 inquiryId 与非字符串工具名的原判定，用一份验证核心及实际登记规则束；三项要求的身份/签名变异同时打红 ZCode、DSH 接缝，Host 固定副本的 193 项聚焦测试全部通过（13.462 秒）。5-P3 同一 run continue 以 unknown/alive 原生组事实且外层确认构造负向，不再把续接能力未知当作停止未知；Host 固定副本的 115 项聚焦测试全部通过（13.877 秒）。整合后的源码与测试均逐文件字节匹配固定交付。Host 仅将 5-P3 文末任务根的绝对路径改为占位符，事实、验证结论与测试没有改变；该小更正按用户的相称性规则登记，不打回、不重跑。

5-P2 已内部签收 int-c02d7107-3783-4946-ac4c-0d405fe01732；受管检出回收计划 cln-92935fe9-d086-47b2-8ecd-2961f1f70b1d 已 applied，Host 核实路径消失后，将七份已读取的原始日志/变异摘要保留在本检出 tmp 的 manifest 中并记 SHA-256，再仅整体删除创建时登记的该微任务根。未扫描或清理别的临时对象。

5-A 初版固定审查拒绝：相同 requestId 改 questionId 可重复入队、客户端同步 RPC 超时仅作事后分类、观察分页首条可超过整帧预算。由原 run continue 修正，未整合初版，不新增 buddy、不更换配置。只读固定审查使用 Codex 原生子代理，实施仍走 buddy。

Host 范围外收尾：5-P2 可写范围之外的 codex/protocol.py 仍有相同 UTC 格式函数，唯一生产读取方为额度快照 observed_at。Host 改为直接引用已验收 native_support.utc_now，删除重复定义与 datetime 导入，格式与调用点不变；此项由 Host 修改并纳入聚焦检查。

5-P1 与 5-P3 的两份整合记录均 verified 并内部签收；受管检出各按 cleanup-plan/apply 回收，原始聚焦、对照与变异材料已在本检出 tmp 中按各自 manifest 保留 SHA-256。Host 仅整体删除创建时登记的两个微任务根，核实路径均消失，无通配符删除。UTC 收尾的两模块聚焦检查通过（1.105 秒）；一次性元数据脚本误带 P3 artifact 标识，Host 在新归属记录中更正，原脚本/日志/元数据原样保留，没有更改实际检查结论或重跑。

第五步公共接线选择：CONTRACT_VERSION 与包版本由 0.28.0 提到 0.29.0，新增 worker_live_attach/detach 两个内部具名操作及 HarnessRunLive、WorkerRuntimeLive 的 request/observe/capabilities，不新增 CLI 命令、claim/renew 字段或数据库结构。WorkerLiveAttach/Detach 使用严格内部模型，服务接收的帧只含 Worker 端点、完整运行身份与 attempt actor，绝不含 controller 地址或能力。RunIdentity 六个字段原样移到 protocol/run_identity.py，由现有 run_contract 直接引用同一个类，JSON 规则没有变化；跨边界事实由 protocol 定义，避免新传输模型反向导入 buddy 一侧。该公共接线由 Host 统一拥有，微任务只在其唯一目录中使用；运行结果中的字段本步按实际消费者核对。C-Two 后端尚未内部验收，以上先固定公共格式，尚未启动依赖它的 Worker/服务实施微任务。

Host 聚焦核对发现 5-P1 原可写范围之外的 live_channel 限额测试仍从 ZCode 协议页引用已搬入 session_receipts 的三项公共常量；Host 直接更新导入与引用位置，编号及实际比较值不变，不恢复旧别名。本轮先错误选择不存在的 test_live 模块（31 项 run_contract 已通过），新命令选择实际 test_live_channel 后发现上述旧引用；原失败日志保留，此次仅重跑受修正影响的 44 项。

5-A 第二轮固定审查仍拒绝：真实函数的无模型探针构造 129 个 requestId 别名对应一条问题，全部 queued 且索引无界；owner 消费数为 0 时已有 observed=True/queued 状态；旧接口的空可读 LiveJournal.available=True 变为新后端 None。原 run continue-2 增加有界请求索引、标准库 owner 应答与实际 journal 发布；并通过本 run 问询补充无观察源/原生读失败的 observed/reason/error 来源缺口，公共 LiveSnapshot 字段与三操作均不扩大。首次三项修正保留，未整合此轮未通过源码。

Claude 两次原生冒烟的私有根在核实停止后已由 Host 按各自创建时登记的确切路径整体回收；本检出 tmp 中保留各次实际请求、RunResult、公开回执与核对摘要并记录 SHA-256。仅删除这两个本次创建的根，没有操作原生登录目录、其他会话或日常数据。

5-A 第三轮封存 09176996 的独立聚焦 129 项通过（13.473 秒，1 项原有平台 skip），仍按真实函数的并发/关闭探针拒绝：close 后仍 consume；并发别名成功后未绑定自己的 requestId，改问题可再次入队；settle 覆盖 owner 先发布的 delivery/limitation；consume(0) 不取已排队值。原 run continue-3 修复这四处，Host 没有代改范围内源码，不扩大完整检查。原日志与探针保留。

5-A 第四份封存 8732af92 的固定副本中，53 项后端聚焦测试全部通过（12.893 秒），未重跑未变的其他 80 项。Host 原四处队列探针原样换到该封存源再次核对：close 请求不再消费；并发 r1/r2 均被预算内绑定且 r2 改问题回 request-payload-conflict；settle 保留实际 delivery/limitation 及原 seq；零等待正确取队列。源码五路径逐字节整合，后端保持三个具名 RPC，owner 本地 settle/publish 接口由真实 producer 接线，不直接驱动模型。通过本次内部验收后再开始依赖它的 Worker 实施。

5-B1 首份 helper 的 21 项独立聚焦检查通过（2.908 秒），真实 BoardStore 探针仍发现实例 NULL 放行、已有治理回合省略 turn/hash 放行以及当前合法摘要被拒；拒绝并原 run continue。Host 任务中的“已记录摘要列”假设错误：当前服务先保存规范化 input_json，input_sha256 列等结果才写，修正为复用现有服务输入摘要来源，未新增 schema/写操作或扩大微任务范围。未代改 helper，探针与失败来源完整保留。

两条微任务因 Z.ai 不可重试的限流（rate_limited、code 1308、HTTP 429）失败，按用户第五步许可在原 run 上继续，未取消或另开。5-B1 被限流的 buddy 为 zcode/zai-api/GLM-5.3-Flash/max，原路由决定 dec-024b214f-2b5b-4c75-927c-df5acb3999fd；5-B2 为 zcode/zai-api/GLM-5.3/max，原路由决定 dec-4f931ac6-fef4-4105-9caa-a011e4cd5736。两条均改用已启用且可用的 codex/openai/gpt-6.1-sol/high，保留原检出、任务根与范围；未修改模型启用、登录或配置。

Host 公共接线前置：新增 protocol.activity.ActivityPublisher，复用既有 normalize_activity/is_newer 与相同阶段、时间节流，只将写文件改为调用实际端点发布函数。未成功的发布不前移序列，保留重试；阶段变化即时发送。新增一项行为测试验证合并、发布失败与新阶段，聚焦测试 1 个通过（0.001 秒）；这不是另一个运行通道。暂保留旧 sidecar 实现供尚未整合的调用方，最终接线时删除。

5-A 原 run 已确认验收 accepted，受管检出 cleanup-plan/apply 已成功。Host 先保留任务材料 20 份并记录摘要，再按创建时登记的单个确切任务根整体回收，未扫描或删除其他端点、目录。

5-B2 报告公共接口缺口后，Host 将 5-A 的严格有界解码和完整身份、实例、token 认证原实现提取为 decode_live_wire_frame / authenticate_live_frame，原 endpoint 同源调用，无新规则或账本。后端 53 项聚焦回归通过（原始输出 host-live-auth-focused1.log）；此公共文件由 Host 维护。Worker 的隔离 Codex 检出不能写共享 Git 元数据，后续若只因提交命令失败，由 Host 核对文件来源和范围后代执行 git add/commit，不代改实现并登记。

5-C1 原路由决定 dec-edb4247b-4fc6-4165-b643-8f16e9b4714a 的 zcode/zai-api/GLM-5.3/max 在首次原生回合报不可重试 code 1308/HTTP 429，按用户许可原 run continue 为 codex/openai/gpt-6.1-sol/high；未改供应方启用或登录。

5-B1 Codex 已完成实现、聚焦与变异验证但无法写受管检出外的共享 Git 元数据；Host 按原 run assistance 请求只执行精确四路径暂存和提交（0bfbbaaac34cff3392b7d9ac9cfdf38eeb103ed1），执行前后文件摘要相等，没有源代码或测试代改。之后原 run continue 收尾，不重跑不变的测试。

5-B1 固定交付 6fefde0c18f6659a0fb094c25b443255d9fd1590 已审查：四路径与范围对应，两份 Python 源码与 Host 独立审查的 6fefde0c 副本逐字相同，29 项聚焦测试通过 3.169 秒。真实 claim 的摘要列尚空仍能正常绑定；治理身份缺失、未知 Worker 实例与错实例 view 均被拒绝。7 个原始变异红日志已核对。最终 continue 只收尾固定提交说明，未重跑不变的测试。已将内存 registry 类整合，公共服务/Worker 接线与整步检查仍待整合。

5-B1 受管检出清理计划已应用，路径消失且两层任务停止已确认。Host 保留 30 份原始日志、变异脚本与摘要后按创建时登记的单个任务根回收；其固定提交权限说明作为当时事实保留，Host 代执行提交及封存完成的后续事实在本整合登记记录。

C-Two 点查口径由 Host 选择：按当前所有者已发布的事实查询。成功读取但该 id 没有已发布条目时，observed=true、inquiries=[]；这只表示没有已发布的该条目，不证明原生端没有问题或回答，也不改变黑板 pending 状态。旧 socket 直接点查原生所有者时曾返回 bridge-refused/not-ready；第五步的通道投影差异在此登记，不把两者称为逐字段相同。原生 observation 来源失败的 observed/reason/error 仍原样发布，失联、关停不成为 native stopped 证据。

5-C2 固定产物 351b9f80 已核对十路径范围，并在独立固定副本运行四个 native_run 模块，197 项通过，46.222 秒。三个实际原生事件/callback/计数防护变异的绿/红原始日志已核对。Native producer 使用共同 endpoint 与 ActivityPublisher，Codex 原计数/节流单独保留；零安装版 harness/真实模型检查。公共桥、角色工厂与跨进程运输仍待后续整合，未声称此次聚焦已证明这些边界。

整合缺陷由 Host 修复：5-C1 的真实 journal 发布器首次把带 delivery 的 InquiryState 交给 5-A 后端，重放时 _question_reply 将 FrozenJson 对象直接嵌入 JSON 字典，严格模型拒绝。Host 只把它投影为既有 .value（实际 JSON 值），保留全部 delivery、reason 和状态；新增重放传输回归 1 项通过。未代改 5-C1 范围实现。以 5-C1 未验收部分产物构建固定验证副本，覆盖已固定公共后端及已验收四个 producer，在 Host 可使用共享内存的环境运行 owner 15 项全部通过 1.660 秒，含两个真实跨进程 C-Two peer。仅消除公共整合和测试环境的阻塞，5-C1 仍未验收。

5-C2 验收 accepted 后受管检出清理已应用，确切路径消失。Host 保留 59 份命名原始日志、变异/编号证据及摘要，再整体回收创建时登记的单个任务根（含私有 venv/cache）；未读凭据文件内容，未回收其他会话对象。

5-B2 固定产物 daad210f 在 Host 允许 IPC 的固定副本中 23 项通过 5.833 秒，包含六个真实 peer。但审查发现 unbind 没有实际 named detach，WorkerLiveDetach 没有生产调用，服务登记不能正常回收；明确拒绝该产物并原 run continue。正式 rejected 操作因 awaiting-host 尚非 delivered 被黑板 NOT_READY 拒绝，原始响应保留；拒绝结论和具体缺陷写在同 run continue 与本登记。Host 未代改范围内代码。

5-C3 首次路由 zcode/zai-api/GLM-5.3-Flash/max（决定 dec-ae3c4288-67de-482a-99be-677ef6cca72d）以供应方不可重试 code1308/429 失败；按用户许可原 run continue 为已在本步完成代码/测试迁移的 codex/openai/gpt-6.1-sol/high，未改路由偏好、供应方启用或登录。

5-C4 首次路由 zcode/zai-api/GLM-5.3-Flash/max（决定 dec-ffa2e8ff-33a3-4e33-be3e-cb4ff3a4b6a9）返回不可重试1308/429，原 run continue 为本步完成过同类代码/测试迁移的 codex/openai/gpt-6.1-sol/high；未改用户设置或登录。

5-C1 同配置两轮已完成模型文本后均报安装版 Codex native-shutdown-failed，最终两层任务停止确认；未记作正常模型交付。最新部分产物8e625a2在Host固定副本15项通过1.644秒，含两项实际跨进程，新增瘦身和相关防护已核对。Host按五路径摘要只代执行Git提交（24fc9e9c05963f43f527e46a1e8c22f017fa7408），未代改实现；同run仅收尾最终交付，不重复代码/测试工作。

5-C1 Host代执行 checkout Git提交后，旧黑板 continuation保留的 includeUntracked 选择器因文件已 tracked 而失效（INVALID_WORKSPACE），准备阶段没有调用模型。Host仅用 git reset --mixed 恢复自己该次提交前的 HEAD/index，五文件摘要前后相等、封存8e625a2产物保留，原run再次仅收尾。不删除source、不改分支；此后新文件以系统封存的 outputCommit 为固定提交，避免重复改变检出追踪状态。

5-B2 原run补正固定产物11f28f5a，五路径范围一致；Host固定副本32项全部通过5.921秒，含六项实际IPC peer与named detach、迟到attach补偿，真实登记数归零和正常socket消失断言有效。两项本轮变异原始红输出核对为4与2项断言失败，没有导入错误。重绑在旧attach和后续detach结束前被同key屏障拒绝，复用既有identity/instance契约，没有增加CAS字段。固定产物已整合，公共Worker/服务接线仍待整步验证。

5-C1 最终原run已正常 delivered，固定产物8e625a29445cc137226256413a7d64feb73a8037与Host15项聚焦已验证的8e625a2五文件逐字相同，代码/测试没有再变，不重复检查。只收尾回合没有运行工具或测试，之前两次native-shutdown-failed及准备选择器错误均保留。问询owner唯一实时输入为endpoint有界队列；journal提交后才settle、真实原生收据才delivered/answered；旧socket和失去生产调用的参数/方法已删除。公共producer构造参数适配由Host处理，整步检查仍待。

5b2 已 accepted，受管检出清理计划已应用且路径消失；Host保留47份命名原始日志/变异/编号/摘要后，按创建时登记的单个确切任务根整体回收。未读取凭据文件内容、未改其他会话对象或旧IPC残留。

5c1 已 accepted，受管检出清理计划已应用且路径消失；Host保留45份命名原始日志/变异/编号/摘要后，按创建时登记的单个确切任务根整体回收。未读取凭据文件内容、未改其他会话对象或旧IPC残留。

Host公共接线验证：已收齐5-A/5-B1/5-B2/5-C1/5-C2，角色签名适配与真实endpoint注入完成；公共三模块35项聚焦通过27.000秒（host-public-wiring-focused1.log）。实际私有BoardService/Store的named attach/detach、真实Actor/turn核验和C-Two服务客户端→持有Worker→controller两跳，重启内存映射/实际reconcile后原handle重挂、两次fsynced fake journal、解绑与两socket消失通过1项0.659秒（host-holder-three-hop2.log）；只有登记送达沿测试peer的recording客户端回送service，未称完整后台Worker模型回合。首次nonce夹具不足16字符失败已保留并修正。

独立只读审查发现Host生命周期问题：直接execute/run_once每次关闭不可重启live runtime，下一任务失去连接。Host移除这次过早stop，资源由Worker.run finally或直接驱动的显式owner结束；新增实际runtime连续两次直接attempt test通过0.005秒，使用同一资源且两次真实named detach。生产实现属于Host公共整合，非微任务代码代改；源与环境边界在记录/原始输出中保留。

Host公共防护变异：完整身份比较移除后，13项holder seam测试出现13个子项/方法断言失败；恢复逐项身份门的原件13项通过0.021秒。把live runtime重新改为每次execute后stop时，连续两任务测试出现1个行为断言失败；原件1项通过0.005秒。两个变异都在Host新源码副本中运行，未覆盖实施源码，没有导入/环境ERROR，原始输出与摘要保存 host-public-*-mutation1.log / host-public-mutations1.json。

公共接线由Host统一整合：服务只经内存LiveRegistry向Worker具名端点请求；实际Holder以run request与held identity、ready实例/进程/端点素材核对controller，缓存同一有界客户端。Worker初始化先设置server/client，成功renew/reconcile后仅重挂同一handle；attempt结束摘除，进程结束停止Worker自己的资源。controller每run注册临时endpoint，ready不含token、私有control含独立token，原生结束后finally注销；Holder只有实际Popen收割且进程组确认消失才对同一个文件身份清理。角色/注册表/public文件与跨微任务的ZCode/DSH keyword-only构造适配均由Host完成，尚未在日常运行时启用。

5-C4 部分产物5e44cbf被拒绝：迁移后的测试仍通过JournalOwner.projection实例化整个ExistingLiveChannel，不能在旧backend退休后运行。Host仅记录并原run continue，不代改其测试；提供固定Host源码快照manifest，要求纯journal/严格模型及实际endpoint发布，保留所有原guard意图。上一模型关闭失败真实记录保留，未接受部分产物。

Host退休旧后端：生产消费者已接通C-Two后，删除ExistingLiveChannel、旧raw inquiry_transport以及ActivitySidecar文件读写入口，保留纯journal投影与规范化活动模型。write_json_atomic检索只有退休ActivitySidecar调用，没有usage生产读取方，故同批删除；早先Host要求保留的假设已撤回。第一次固定59项测试因Host误删json import出现9个NameError，恢复equality_key实际依赖后59项通过1.997秒，原始失败保留。

跨交付整合由Host适配ZCode/DSH原生测试：调用keyword-only InquiryBridge，ZCode晚绑定时启动实际owner队列消费者，DSH由native run自己建立唯一owner，SDK连接仅在测试中局部替换；不再用旧raw socket/backend。原guard编号保留，失联/错token断言改查实际C-Two分类。103项聚焦第二轮只剩错token夹具未修改真正_token的1项断言失败（102项通过，26.748秒），修正夹具后该1项通过0.191秒。原先5个旧构造导入错误与2个旧分类断言失败均留原日志，未记为变异证据。

5-C3记录接续continue在workspace准备阶段返回WORKSPACE_GIT_ERROR，没有启动模型，既有五路径固定产物de798ea与测试不变。该continue已失效；Host不改变Worker回合58通过/1失败的历史结论，以自己固定副本59通过及公共holder守卫验证完成整合审查；write_json_atomic用途的小更正由Host直接处理并登记，不重跑不变测试。

5-C3固定产物de798ea的三测试与编号表逐字整合；只将记录中Host指定保留write_json_atomic实际消费者的错误说明更正为后续检索无消费者并删除，其他回合红绿与范围事实不改。Host已有固定退休源码副本59项通过1.997秒，三测试与该已验副本字节相等，不为记录更正重跑。完整检查仍待5-C4收齐。

5-C4原run补正固定交付6dd195e，三路径与封存patch SHA核对一致；Host退休源码固定副本36项全部通过41.206秒。实际Store/Actor/当前turn/registry、共享Wire/Endpoint与C1 reader均执行；Worker网络与原生签名仍由另线证明。四处原始变异日志SHA核对：token与分页各1个断言失败，live source拒绝事实丢失为6个KeyError、terminal source为2个KeyError，均为真实guard反例，没有导入错误，不把后两项称作assertion failure。前轮被拒绝产物与native-shutdown-failed记录保留，Host未代改范围测试。

5-C3/5-C4均已内部accepted，受管检出cleanup-plan/apply完成且路径消失；Host按原创建登记的两个确切任务根整体回收，分别先保留命名原始日志、变异patch/脚本、编号和SHA清单。Worker没有手动删除，未碰其他会话对象。C3的prepare失败保持原记录，Host用已停止固定产物直接完成验收，没有再调用模型。

Host补齐退休入口后发现的范围外测试接缝：Worker invariant的5项改为实际held request/ready验证、strict channel/endpoint及progress，仅替换SDK连接；ZCode旧activity文件3项改为真实fixture native controller的C-Two观察、完整identity拒绝与共同publisher节流；DSH角色线用held controller绑定，不再调用删除的bind_live_channel。初轮20项仅8个夹具调用错误（close缺reason与observe未传limit），DSH12项通过；修正后Worker/ZCode8项通过6.367秒。未修改已验微任务文件；编号改名和同编号迁移纳入整步表。ZCode fixture故障收尾先停止实际持有进程组再按captured descriptor回收自己的socket。

CONTRACT_VERSION的两处旧0.28.0字面断言由Host随本步真实契约变化更新为0.29.0，SQLite schema仍15；CLI字段/帮助生成未改。变更原因及私有版本互验、安装/打包证据列入整步记录，日常运行时未升级。

范围外迁移防护的Host故障注入：4个原件编号通过2.669秒；去掉endpoint完整identity比较后2项断言失败，去掉stored request/held identity比较后1项断言失败，去掉publisher节流后1项断言失败。变异仅在三个新源码副本，不覆盖实施源码；日志/SHA保留。

私有构建与契约核对：0.29.0 wheel离线构建，私有锁定依赖环境安装后四个run/共同live binding、两个内部具名操作可导入；347个wheel文件顶层只有hey_my_buddy与dist-info，没有buddy兼容namespace或退休入口。实际C-Two隔离peer分别加载43d8d63的0.28与本步0.29完整BuddyControl声明，四次ping同代成功、异代双向ERROR_CONTRACT_MISMATCH；运输实现为无业务ping fixture，不称SQLite升级或业务端到端证据。两peer均正常退出且其登记socket文件消失。

无模型生命周期补充：实际WorkerLiveRuntime先设置server/client，真实register并回读地址，登记socket从存在到stop后消失；两个真实ZCode fixture controller经共同通道发布，同阶段刷新、身份拒绝及collect通过2项6.224秒，actual owned进程组停止已确认，两份captured socket文件均消失。0安装版harness、0模型；这些观察不替代真实DSH回合Worker端点缺失的那一条确切路径证据。

字段/类/参数复核覆盖公共运行、实时格式及本步共同机制；逐组生产消费方见adr025-step5-format-consumers.tsv。生产构造、读取与嵌套公共事实投影人工核对，补充AST实参读取检查；具体实现无闲置参数，四个Protocol空声明不是运行实现。formatVersion实际由Pydantic固定版本校验消费，LiveEvent/JournalRejection通过父模型投影消费，没有为了保留字段新增虚假读取方；未发现没有生产使用方的字段、类或参数。最初审计脚本漏列共同ReceiptRules而退出，只补齐审计说明，不涉及生产代码或测试改变。

首轮完整检查默认4并行，183个文件全部调度，17文件失败，其余166文件2638项（原有skip1）通过；原始所有失败与运行器正常私有根收尾保留，未当完整通过。主要是范围外旧夹具仍读旧envelope、activity文件/raw问询，以及C3在tests/python cwd的导入前缀。新增两条相互独立测试迁移微任务5-D1/5-D2首次路由不指定buddy，范围仅fixture/旧测试及记录，不增加生产功能；公共与角色仍Host整合。

Host直接修范围外四处：HarnessStartup将Worker参数改为声明的worker_id,state_dir顺序；registered review不向无参fixture服务工厂注入已删闲置参数；private adapter改核对live.readyFile归属而非退休errorPath；真实Worker回合读取保留RunResult.activity并与公开phase/eventSeq相对，不再断言sidecarWritten。初次27项仅最后1项因Host还引用退休nativeActivity字段KeyError；改为实际activity投影后该1项通过，其他26项原件通过，不重复无变化模块。

5-C3完整发现导入缺陷已准备仅两处前缀的确切patch，按用户规则尝试原run continue；黑板以CONFLICT/An accepted goal cannot be continued拒绝。内部验收漏了tests/python cwd场景，accepted与检出回收已发生；Host未绕过黑板状态或代改，已向用户提出这一次两行导入的例外授权，独立工作继续。

独立只读源码复核与Host初核一致：限定公共运行/实时格式、共同机制及其生产消费者，没有可确认的闲置字段/类/参数或公共整合缺陷。实际Pydantic请求/结果文件解码消费formatVersion，blackboard公共live投影消费嵌套元数据，holder消费descriptor、Worker日志消费CleanupOutcome，ReceiptRules四项均被实际核验函数读取；没有运行厂商harness或静态证明厂商代码。该源码结论不代替完整检查与外部验收。

当前跨边界导入登记56条：黑板→buddy37、buddy→黑板15、protocol→黑板3、protocol→buddy1；第零步54条分别33/16/4/1。同一AST/相对导入/dynamic literal脚本在已验第零步4cf58de重算与原54条逐项完全相等；本步相对增加/删除逐项见cross-import-delta.tsv，完整56条见cross-imports.tsv。只登记，没有顺手改变这些既有边界；本步后续修正仅测试，不会改变该生产登记。

5-D2首次路由dec-cfb245c1-f2b8-441e-95c6-77c5ac35f6c7选zcode/zai-api/GLM-5.3/max，正常固定交付dbdb5e5；没有因耗时更换buddy。七路径封存patch/SHA/停止核对一致，Host固定整合副本六相关模块168项通过224.859秒；8份迁移防护变异均真实断言失败且无导入错误，原始日志保留。仅将MD确切系统临时根改为<task-root>，不改Worker做过/验证过的结论。内部ack/回收暂等最终集成发现检查完成，避免重复C3验收遗漏。

用户补充：提交第五步前合入socu/buddy-core 9b65abbc的ADR-027；解决Codex/DSH等冲突保留两边行为，受影响聚焦后最终代码才再跑完整检查。今后微任务描述新增共享Git规则：受管检出与用户仓库共用stash/分支/标签，Worker禁止git stash，不移动或删除分支/标签；基线对比用新临时副本。Host本次合入不使用stash或自动stash。
