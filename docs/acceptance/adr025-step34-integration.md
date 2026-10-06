# ADR-025 第三、四步整合登记

本记录区分微任务固定交付、Host 公共接线、整合验证与 Claude Code Host 的整步验收；某个微任务交付或内部签收不表示第三、四步已经通过外部验收。第三、四步依据 `docs/design/adr025-execution-plan.md` 的第二步后续小节推进。

## 准备

Host 将包含第二步验收的 `socu/buddy-core` 合入实施分支：用户指定的 `886836e` 与随后只说明受管检出被拒回收原因的 `4a8dfee` 均已包含，合入是从 `5859475` 到 `4a8dfee` 的快进。执行计划更新提交 `90900d7`，增加四项准备、三条独立 harness 线、默认并行完整检查、未消费字段期限、限流恢复授权与受管检出不得切换分支的任务规则。

按用户明确授权，Host 在旧准备微任务 `7d967147-ce32-405c-a5a0-62859e84abb1` 的受管检出确认干净、HEAD 为 `c919197` 后执行 `git checkout --detach`；然后用新读取的 revision 完成 `workspace-cleanup-plan` 与 `workspace-cleanup-apply`，结果为 `removed=true`，实际目录消失。随后强制删除已补丁整合的分支 `socu/adr025-step2-preparation`。只处理这一受管检出及指定分支；旧第二步记录中的历史拒绝保留。原始计划、应用和路径核对输出留在本检出 `tmp/adr025-host/step34-20261006-105245/`。

3-P1 首次提交走路由，run 为 `4aab6133-0ba4-4690-b61b-402c9b0939be`，固定基线 `90900d7`；唯一写入范围为三个指定测试模块及本项新记录。路由决定 `dec-77f37d16-ef38-49dc-af0b-bf49f8d2aee9` 选择 `zcode / zai-api / GLM-5.3 / max`。Host 已即时登记短任务根，Worker 不删除；检查和变异证据等待固定交付审查。

3-P2 由 Host 完成一次计划内真实 ZCode Worker 角色冒烟，结果、完成签收、普通工具事实与两层停止均通过，详见 [冒烟记录](adr025-step34-worker-smoke.md)。原生冒烟配置为 `zcode / zai-api / GLM-5.3-Flash / max`，运行 1 次（用量报告含 4 条 nativeRecords）；本项探针及记录属于 Host 独立验收工作，没有代改微任务范围内代码。

3-P1 固定交付为 `8444d0a`、artifact `b0774106-f648-4c75-ba1a-f9601de0862b`，累计补丁 SHA-256 `028b2eb9c76dc3337dbeef12af05f02c8a3ef501bd79cdb3ada33d20a5b32cd5`。Host 独立核对封存改动只有获准四个文件，补丁摘要正确，整合后文件与固定提交逐字节相同；受管检出保持 detached HEAD。三处改动直接命中要求的真实入口，原生停止测试没有只改结果或 mock 整体；身份测试只改一个组成部分，实际 turn input 及其摘要保持不变。两个隔离变异的源码分别只有强制 gone、删除 attempt 比较这一项变化，摘要与记录一致，原始失败日志的断言和测试路径也对应固定交付。停止测试里的 interrupt 标志证明经过了尝试终止的分支，不另外声称操作系统已向一个仍存活进程送达信号；本次要补的证据是未获确认时如实返回 unknown。

Host 单独运行 inquiry transport 模块，8 项通过且专用临时目录零条目。首次 Host 探针经 uv 启动，测试通过，但探针把 uv 自建锁文件误计为 fixture 残留；随后用同一个 uv 管理的锁定解释器直接执行，隔离了启动器锁文件，确认模块本身收尾完整。该修正仅在 Host 一次性探针，没有改微任务代码，也没有重跑变异矩阵。

整合后的全量测试编号为 2,631，原有 2,628 个全部保留，新增 3 个，模块仍为 171 个，加载错误为零；逐个变化见 [编号表](adr025-step34-preparation-test-ids.tsv)，原始集合与计数在 `tmp/`。默认并行完整检查 `uv run --frozen python -m hey_my_buddy.cli.checks` 退出码 0：Python 2,631 项（跳过 1 项）／171 个模块全跑，Node 110 项，用时 441.197 秒，没有传 --jobs。准备阶段真实模型冒烟只有 3-P2 的 ZCode 1 次；微任务本身不另启动模型探针。该次完整检查同时作为准备批次整合检查，不再重复。

3-P1 整合提交 `5d31114`，黑板核对的整合编号 `int-8c06d5a2-a946-4c51-a808-76bff5da95af` 为 verified（四条产物路径 matching，differing/missing/unrecorded 均为空），随后 accepted。回收计划请求曾返回 REVISION_CONFLICT，重新读取已存在的有效计划后按该 planId 应用，结果 removed=true，检出实际消失；没有绕过黑板手工删检出。Host 留存聚焦、变异与编号证据后，整体删除创建时登记的 3-P1 任务根，以及本次冒烟的私有目录和唯一短 socket 目录；删除命令没有通配符，没有屏蔽报错。第一次 Host 清理脚本在删除前因台账首条使用 purpose、其余使用 owner 而报 KeyError，未删除任何对象；修正只核对已登记的确切路径集合后执行完成，原始操作输出保留。Host 总任务根仍供后续整合使用。

## 公共接线与并行原生主体

3-A1 Codex run `19e61256-94a4-418d-975d-bf08695b7d04`、3-B1 Claude Code run `c7af488f-bc57-41e2-a7ec-71c62f34b524`、4-B1 DSH run `eebeeba8-d479-4cb3-a4e2-e4c531845e2c` 同以 `5d31114` 为基线，各自隔离检出、首次提交不指定 buddy。任务书逐项限定本 harness 包／测试及新记录，明确公共值、角色、注册表、共享测试与依赖归 Host，Worker 不切换分支且不删除。三项只是各线第一个原生主体微任务，尚不表示整条线通过。

Host 为 DSH 共用既有完成服务，将 `buddy/harnesses/zcode/mcp.py` 用 git mv 搬到 `buddy/roles/session_mcp.py`；只改相对导入、模块说明、ZCode 的启动模块字符串和两份既有测试的导入／命令。执行体 AST 去掉导入与模块说明后完全相等，没有复制 MCP 载体或完成业务规则。没有新增库或另写协议实现，复用已存在的承载；旧路径不留兼容入口。聚焦 `test_zcode_protocol` 与 `test_zcode_inquiry` 共 88 项通过（10.882 秒），含真实 Python stdio MCP 进程；测试编号无变化，没有原生模型调用。此处属于 Host 公共文件整合，随首批原生主体整合后的完整检查一起覆盖，不单独再跑整步检查。

3-B1 首轮固定交付 `106bb6e` 尚未整合。Host 核对累计补丁 SHA 与五个改动文件均在范围内，并用该固定代码上的模拟原生进程重现了两处事实错误：invalid-json 时原生根身份列表为空，却上报本地预分配的 nativeIdentity；init-wrong-session 时 nativeIdentity 与原生根列表相冲突；显式失败结果已经经过 EOF drain，却因业务成功与停止条件混入而把 streamComplete 报为 false。源码比对还确认 Worker-read 的网络域名从原有七项变为空、附加禁用项增多，不能称为等价抽取。这些是原微任务范围内问题，回原 run 修正，Host 不代改该主体。

Host 按已批准的请求字段可调整规则补 `network_allowed_domains` 与 `additional_denied_tools`，并在公共 run 调用点拒绝模块没有声明支持的非默认控制，选择与理由见执行计划补充。它们分别表达原生网络域名和附加禁用工具，不引入角色标签；默认值保留已有路径的行为。字段与共同入口的聚焦检查共 44 项通过（0.051 秒），新增两项测试覆盖 None／空清单区别、JSON 与严格类型、未支持控制不会静默运行。公开 CLI 与 C-Two 契约版本不变。这是 Host 公共接口工作；其 Claude 消费和策略对照由 3-B1 原 run 修正，实际角色接线归后续整合。跨基线验证可在 Worker 的 m/ 下用该 Host 提交与本任务自身文件组成一次性验证副本，不改受管检出的公共文件，不切换分支。

公共控制值提交为 `ae9f943`。Host 另一个窄注入确认：原生回合已停止后，600 KiB 的结构化值在 RunValue 构造处抛出 BoardError，实际停止结果为 `(True, 0)` 却没有 RunResult 返回。连同无依据的 20 ms 消息边界等待和未使用的私有字段，一并列入 3-B1 的固定交付拒绝，原 run 已用 continue 继续（revision 6）；没有取消或新开微任务。Host 自己的冒烟记录也把次数口径明确为“原生 Worker 运行 1 次”，同时保留 4 条原生用量记录，避免把运行次数写成厂商请求次数；这是已知事实的表述更正，无代码或测试变化。

3-A1 报告的续接缺口由 Host 补公共 ResumeCheckpoint 对象，角色只投影黑板已有的 nativeTurnId、attemptId、inputSha256 三项；共同入口拒绝未声明支持的非默认检查点。三个键须同时存在，没有新增角色标签或传递整个输入。公共格式、控制与已接入 ZCode 的聚焦检查共 59 项通过（17.702 秒），新增一项编号，原有一项控制测试增加检查点子用例；Codex 的绑定比较仍由原微任务消费，不能据此声明 Codex 已接入。

3-A1 首轮因根回合在 finish 后再次调工具而失败（duplicate-finish），原生与外层停止均已确认；这不是供应方限流，不更换 buddy。封存的 partial-output 为 07eef2d、artifact 1fcbcb46-9bdd-4626-95e8-c3f332bd20c7，累计补丁摘要与六条范围路径核对一致。Host 对该固定代码的模拟原生进程探针确认：Worker 工具完成后 toolEvidence 为空，观察计数先为零、终结才变成一；65 种未知事件令结果构造抛 BoardError，已确认的停止事实没有返回；准备失败且未启动原生进程时却报告 groupState=unknown、started=null，同时 basis=spawn-never-happened。以上要求回原 run 修正，不整合该产物。

该 partial-output 的记录同时声称没有手动删除，又承认重建变异副本使用 shutil.rmtree；实际脚本在本任务已登记根的 m/fault-* 具体副本内删除再复制。已见对象均位于本任务根，按用户规则拒绝交付并要求如实披露、撤回矛盾口径后 continue，不暂停等用户，也不恢复或追查别的会话。Host 未代改这份掩盖违规的记录。上述探针只用假原生 CLI，不调用模型；原始输出保留在本检出 tmp/。

3-B1 同 run 第二轮固定交付 834f825（artifact 5b3c7f94-0571-4c73-8a88-aeb6058b1ba0）累计补丁 SHA-256 为 1b12f95beaff40299484f7d4faf8c82b8f5faf48c2f7a714c48de88961269949，七条改动路径均在原 scope。Host 核对五项整改及未改路径，整合七文件与固定提交逐字节相同；两份新变异副本各只有对应身份／EOF 条件的一行变化，指定测试在变异下失败、对照通过。Host 当前公共基线上的新原生主体及配置聚焦检查 47 项通过（7.338 秒），没有模型调用。接受原生主体微任务，等待共享角色接线、3-C、旧入口删除和整条线的验证，不宣称 Claude 线已完成；本项没有单独跑完整检查，随接线批次整合后运行一次默认并行检查。

Host 补共享审阅角色接线：ReviewPreparation 冻结登记的运行模块，沿用同一个外层控制器、RunRequest／RunResult、停止收集和真实值校验；ReviewObserver 保留 N 次允许、N+1 次停止、原 Codex 一次格式纠正及原文后缀，Claude 无纠正。注册处只绑定既有原生禁用项／网络参数，角色观察规则不含厂商名字。该接线尚未把 Claude/Codex 注册进生产路径，原生主体的本地 helper 和旧入口删除由各线下一微任务完成。

共享审阅新增 3 项测试，以真实 Python 假 CLI 经角色控制器执行、实际请求和结果帧核对、工具预算停止、纠正与已有行为对照。第一轮 68 项中 67 项通过；Host 新测试用了不存在的read夹具名而没有触发工具，改为已有budget夹具后发现停止期间真实收到第二条已排队工具事实，遂按该夹具设预算1并断言第二次触发，3项通过（0.172秒）；其余源码未因此改动。之后角色控制器的聚焦模块再通过。预算>临时变为>=的一行变异被测试抓住；首次Host变异探针错误地解析了虚拟环境解释器的符号链接，因缺依赖而导入失败，不算变异证据；改用同一锁定虚拟环境的入口后得到指定断言失败。没有模型调用；这些公共文件由Host负责，完整检查留给接线批次。

3-B1 的整合编号 int-6abe0a6b-037a-4808-83e5-dc9dec6c0dfe 已verified并accepted。回收读取到现有已applied计划 cln-f4f2dc80-cec9-4d6e-a8e6-408dc8520074，removed=true，实际检出消失，不重复手工删检出。Host 留存九项具名证据与摘要后，按创建时登记的确切任务根整体删除，没有通配符或忽略错误。3-C 从02b1f76首次走路由，run 29e6d180-8d6e-41ea-b51e-1c5dc35c29d2，只修改Claude范围；其任务书再次明确受管检出不切分支、Worker不删除、一次性材料用新目录和公共文件归Host。

4-B1 在约两小时执行预算后结束，终态cancelled、cancellation为空，原生与外层停止都已确认；没有供应方限流，不能按限流例外更换buddy。封存partial-output为cb9dfbc、artifact 97e40606-dadc-4b96-9f9e-9f8e50b2cbcd，累计摘要1dfc416c46a043c2407104887e8c9cf8102695918bc2945ff0bb974b1f8556cf及六条scope路径核对一致，未整合、未验收。初稿none工具清单为空，usage恒为None，并建议Host补解压依赖；同一记录先称零安装版启动，随后承认旧入口测试回退安装版DSH。Host已核对至少一个复现的DSH_HOME明确在本任务私有根、以MISSING_CREDENTIAL退出，尚不声称准确启动总数，后续须由原run纠正验证结论。

DSH初稿另把F-D2探针关闭标题模型和遥测的patch应用于全部运行。当前旧Worker入口没有这项强制关闭，旧快速路由有；这超出已接受的三项行为差异。按用户的暂停条件，Host停止DSH线并请求选择：恢复Worker原profile行为（推荐），或明确接受全部关闭并登记。Codex与Claude的独立工作继续。Python解压库属于项目依赖的待处理缺口，未因此更改日常运行时；初稿建议的zstandard>=1.5,<2混用了libzstd和Python包版本，官方PyPI显示Python包为0.25.0，后续依赖选择须按Python包版本核定（https://pypi.org/project/zstandard/）。

用户已明确同意所有hey-my-buddy启动的DSH运行关闭session-title-llm和session-telemetry-otel，分别登记为第四、五项已接受行为差异；私有启动patch不影响用户自己交互使用的DSH。DSH暂停条件解除，按原run继续，不更换buddy。Host按官方Python包版本增加项目依赖zstandard>=0.25,<1，uv.lock固定0.25.0；uv sync --frozen只更新实施检出的项目虚拟环境，锁定解压库的压缩/解压往返通过，未安装或升级日常运行时。

F-D2记录所指原始none patch在当前留存路径及其旧受管检出已不可读，不能声称逐字节复用了旧清单。Host用私有HOME/DSH_HOME执行两次免模型dump-config，重新读取当前公开组合面：16个tool-前缀行中，tool-result-pruner是上下文剪枝行，其余15个是工具提供行（包含两种平台shell）；不是照历史数字盲填14。第一次脚本用safe_load处理!!js数据标签失败，发生在子进程communicate和finalize之后，原始stdout保留，但其停止返回没有单独保存；第二次先保存停止证据，再用BaseLoader把表达式仅作为字符串读取，exit0且groupObserved=gone。随后对明确15项工具行、plan-mode及两项已批准关闭行做一次免模型ACP初始化/新会话/关闭核对，结果见tmp下dsh-none-boot-summary.json。仅证明当前组合能启动；没有prompt，实际有效工具集仍要在本线最小真实冒烟从本次私有记录核对，未来清单外新行不得被当作已限制。以上Host免模型原生启动共3次、模型prompt0次，均经已验收的私有启动包装。

Host补原生schema Worker的共享角色部分：原Codex/Claude提示词前缀从语法树取值搬入注册参数，共用schema构造器的结果与两份旧schema规范化摘要逐一相同；保留Codex只读检出仍请求可写原生工具、Claude随检出read/write的旧映射。角色接收native-turn-facts并复用六字段validate_outcome，按原条件把被拒请求转为attention；用量/额度/报告逐包保留，原生成功但角色值无效的控制器退出码为1，角色证据不可读仍返回已确认的原生结果和停止。原生服务绑定仍由本harness下一个接线微任务实现，本提交未注册Codex/Claude。

这份公共接线的聚焦检查36项通过（20.132秒，含已有ZCode接线、角色调用点、新审阅与5项schema Worker用例）；新用例通过真实Python假CLI检查本次请求/结果帧、原有schema/prompt摘要、权限attention、原生成功与角色失败分离、证据读取失败不丢停止事实。关闭attention决定的一行变异令指定真实回合见证以completed!=attention失败，对照已通过。只读侧继续投影原有token/quota/末条消息事实，不把有效包随别的报告错误一同丢掉。属于Host公共文件整合，未代改Worker范围的代码，无模型调用。

3-A1第二轮固定封存ed7c9ef与其摘要cca303b的tree相等，3-C首轮固定封存0594ab0与其摘要5754ead同样相等，摘要与封存使用不同提交名本身不是缺陷。固定补丁摘要/范围已核对；两项尚未整合。Host的窄探针发现Codex已completed的review流会被随后unknown停止改报不完整，要求保留两项独立事实；其记录用日志块数和创建时间推断实际删除零次也不成立，要求只写无法确认次数，不继续追查。3-C探针显示未验证StructuredOutput对零预算observer报settled toolCalls=0、最终包却为1并成功结束；同callId两份冲突input仍被豁免。连同测试编号口径不一致，分别拒绝固定交付并用原run继续，未取消重开，未改变buddy。

3-A1第三轮固定封存d5de06f（摘要提交a5e4bb6，文件树相同）、artifact 2db64999-9df9-47bb-bebf-42e6faa5c514，累计补丁e2976b440ba9ae416133f5b3e81fd2ec223df56eef30ff200ce81d833f100768及六条scope路径核对一致。修正后Worker看本回合原生完成事实、review看所有回合完成、fast看真实EOF；都不再用组停止或业务status覆盖流事实，unknown停止仍不能保存可续接绑定。清理记录明确撤回零删除推断，实际次数无法由现存证据确认，没有继续追查。Host整合六文件与封存字节一致，新主体和协议聚焦49项通过（13.819秒）；原run的一行status门控变异使三个相关见证失败，证据已核对。接受此原生主体微任务，注册、公共角色接线、旧入口删除及整条线冒烟尚未完成。

Host为3-A2补共享收集接缝：将原checkpoint的身份/输入摘要核对与可续接判定机械搬入roles.turn_io（原harness副本由3-A2删除），仍先要求双层停止，再允许一个已完成原生检查点支撑续接，即使本回合业务值无效。正常回合仍须有效record；ZCode原有bindingPresent条件保留。收集只在双层停止后调用本harness可选的cleanup_after_run窄接口，保留原私有auth清理时机与错误事实。原native-observations/quota/checkpoint引用按摘要逐包核对。

把既有NativeCheckpointTests临时绑定到迁移后的函数，与现有ZCode接线/schema Worker聚焦共21项执行，20项通过；一次源标签统一保留过了头，令ZCode既有unknown内部标签未被投成原来的公开来源。Host按原有四种收据来源在注册处绑定固定标签，相关10项再通过（15.426秒）。这属于Host公共接线缺陷，已自行修正并登记，没有打回Worker；未改原测试意图。3-A2仍需实现路径/账户服务绑定、来源事实与来源校验、共用实时接口和私有auth清理事实，迁移本harness全部旧调用点测试并删除旧入口；公共注册与跨目录测试归Host，受管检出禁止改公共文件。

3-C第二轮已修普通结算计数/双输入冲突和134→157编号口径，但Host在8份输入保留界之后复现了冲突信息遗失：c1旧input逐出后换成不同input，最终仍被豁免，9个不同调用只报8个。另delivery_exclusion只有生产定义、没有调用；记录还披露本轮重建两个临时镜像，违反Worker不删除。按用户规则拒绝后原run继续，要求保留输入指纹或丢证后拒绝豁免、删除未使用接口，并明确删除路径/次数；都在任务根范围内，不另停等用户。

Host新增已登记运行模块的共用只读资格检查，供删除继承执行入口后的描述类调用：只确认本项目run接缝存在，以及既有平台/工具类别条件，不读厂商源码。免原生探针覆盖未登记拒绝、已登记可用与不支持平台拒绝；实际执行/沙盒仍由本线原生检查验证。这是3-A2/3-B2删除旧入口所需的公共整合辅助，旧未抽取Adapter检查暂不改动。

3-C第三轮固定封存29d4d4c（摘要8ee10fe，tree相同）、artifact f53a796a-13ac-4800-b5ad-65646b175e14，七条scope路径与补丁摘要已核对，整合文件逐字节相同。候选表保留首份完整input的有界sha256指纹，8份全文逐出后仍拒绝冲突；结算前冻结验证，恢复未验证调用的计数，实时末帧与终包一致。删除无人调用的方法，编号按统一加载134→161，27个新增、旧134集合相等。Host整合后的工具投影/原生主体/真实共享审阅及Worker角色聚焦86项通过（6.205秒，无skip）。违规记录已明确：第二轮rm -rf删除并重建任务根内baseline/current两个镜像，一次；原记录保留并撤回含糊口径，本轮无删除。按用户既定规则，同run整改后内部接受此修正；不构成Claude整条线完成。

Host复核接线时补齐原生schema路径服务的共同入口：prepare_run_services新增tool_scope参数，并同时用于Worker、fast和review；fast控制文件保留冻结account，三者均只传运行目录、活动目录、账户选择和工具范围，不传黑板凭据或整个控制文件。此前fast/review返回services=None会使Codex活动目录和明确账户绑定缺失，这属于公共角色接线缺口，由Host统一修正；本次不改任何微任务范围内文件。相关角色与已登记ZCode聚焦23项通过（6.296秒），新增一项实进程审阅用例核对窄服务绑定和JSON请求不携带账户。新增接口随3-A2/3-B2实际消费与各线完整检查验证，无模型调用。

3-A1与3-C内部验收后，Host分别留存13份、14份指定聚焦/变异/测试编号文件并记录字节及摘要。黑板回收计划已自动应用：cln-e16950a4-727c-41ba-944f-039521611ed6、cln-bb36ce07-c5a2-49ec-8213-89562ba0e3c4均removed=true，受管检出实际不存在。随后只整体删除创建时登记的两个确切任务根（3-A1根、3-C根），无通配符、不屏蔽报错；实际路径与清单留tmp台账，其他活动任务根保留。

3-A2与3-B2已首次走路由提交独立接线微任务，run分别为d006212d-c355-4824-b62a-6aabd3382939和77dac0ab-0e68-4770-bf09-f53ec74d73c7；路由决定为dec-a2198ffa-c091-4bf2-8742-4d139cab60e3和dec-307bf613-99f1-47d4-87a4-0c9e67f9e24f。只写各自harness包、对应测试和新记录，公共改动仍归Host；3-B2基线73f4aac包含已内部验收的StructuredOutput修正。Host提供只含import/register的私有验证补丁，交付不会自行修改注册表。

4-B1 继续轮固定 04f43c5、artifact f7c787fa-7a79-4013-821e-b2fe73c2f700、累计补丁 21143f7e6c9ad70dcd9057964b7ffeb5c68fcccda59c609e5e9cd303ff834bb0 的六条 scope 路径一致。Host 在 73f4aac 加固定交付的私有验证副本上运行 35 项新主体测试，全部通过（5.060 秒）；进一步用真实假 ACP 进程复现四项缺陷：初始化失败但已启动的进程被报成 spawn-never-happened/gone；drain 的尾部工具最终计数 1、角色观察计数仍 0 且成功；foreign-root 的文本可成为本根最终值；65 种未知通知令构造结果抛错，已停止事实未返回。四次探针最终实际进程组均消失，没有安装版或模型调用。固定交付已拒绝，原 run 同配置 continue（revision 11）修正，未整合这份代码。

Host 将跨目录用量测试的执行入口改到 worker_executor，将配额有界读取测试改到已抽取的 Codex 原生函数，并把 Router 的禁止启动断言移到公共角色接缝。原 Codex FD 边界测试改为公共 _launch 的真实调用，用同一日志/工作目录/环境/启动异常顺序验证日志保留和 FD 关闭，不再依赖将删除的 Adapter.start；相关 63 项通过（1.411 秒），编号与断言意图不变。两份用量测试的实际新运行路径须在注册后再验证，当前未声称已接线。

Host 另修公共收集的 Claude 旧配额事实形状：角色参数 native_quota_failure 只在 Claude 启用，成功时省略 quotaFailure，拒绝时使用已校验 native-observations 中的原生两字段，不再把数字 resetsAt 转换为时间字符串或添加 null。原生两字段由 3-B2 提供，已通知其原 run；共享角色与既有 ZCode 的 23 项检查通过，本次还不能代替接线后的既有 Claude 用量断言。这是跨交付的公共输出保真修正，没有 Host 代改 harness 范围代码。

4-B1 修正后固定 c77112f、artifact 749b6ab3-856e-4c65-8cbc-767f1c71d58d、累计补丁 b30a43f2961deeda7c7ecc6bcaa78d11eef6c6b2bd83e1fbd1afbc8995efbec6，六条 scope 路径与摘要已核对，整合文件逐字节一致。Host 聚焦 42 项通过（5.819 秒），并重放四个前轮探针：无会话但持有进程保持 unknown；迟到工具到达观察者并拒绝；外来根文本被隔离；65 种未知事件有界聚合且返回真实停止事实。四个探针的真实组最终均消失，均为假 ACP，无安装版或模型。Worker 两份新变异的源码各只有一处替换；未发现独立原始失败日志，Host 独立重跑对应两个见证，分别以 gone!=unknown 和 ok!=cancelled 失败，未变原件已在 42 项中通过。接收原生主体，注册、公共角色接线、原生续接取舍、旧入口删除和真实冒烟留在本线后续微任务，整条 DSH 线尚未验收。

这份固定代码的 git diff --check 指出 native_run.py 一处空白行尾空格；它不影响 Python 语义，未为此增加往返或由 Host 修改范围内代码。完整检查尚待整批接线收齐。本轮 DSH 真实模型运行 0 次，免模型安装版启动 0 次；前轮已登记的至少一次意外安装版启动及准确次数未知的记录保留。

3-A2 固定 2fb8f27（摘要 3f04956，tree 相同）、artifact 2d79698a-0497-41ee-b5c4-48ea4e32c49d、累计补丁 d9044238539c119f776793f1d4b982bda0dc88b207ace4037bc2ac9a0ff1aeed 的 15 条 scope 路径已核对。Host 验证副本完全不含已删除的 runner.py/no_tool.py，加当前公共文件、Codex 注册与两处 workflow 导入适配后，Codex 129 项通过（53.771 秒）。Host 对已验收基线和新代码各跑五个模拟 Worker 场景，状态、错误、停止、续接与用量相符；发现公共投影遗漏 nativeIdentity 和 outcomeValidationError，并额外增加 Codex 原没有的 attentionRequired，均由 Host 修正。未知时间值与私有路径未作相等断言，原始结果留 tmp。

Host 保留各原有结构化收据的字段位置与缺省口径：Codex 快速调用的 nativePolicy 在顶层，审阅 nativePolicy/nativeConfigPolicy 不受可选 capture 开关影响；Codex/DSH 的快速失败仍有 zeroToolVerified=false，ZCode 的既有缺键口径不变。NativeSchemaWorker 通过实际使用的收据参数保留 Codex 原生身份与有界业务校验原因，Claude 的 attentionRequired 缺计数时仍为 false。两处黑板 checkpoint 导入切到已机械搬定的 roles.turn_io。新增两个公共投影回归，与既有角色/ZCode 聚焦共 25 项通过（5.875 秒）。Codex 驱动对失败 thread/start 回读的保留、原中断收据事实及被削弱的旧断言，仍由 3-A2 原 run 修正；未由 Host 代改其代码或测试。

Host 将 ZCode 的合作检查点桥用 git mv 搬到 harnesses/inquiry_bridge.py，供 DSH 共用。队列、journal 持久化/重放、私有 socket、分页读取与 ExistingLiveChannel 保持一份；驱动只传原有错误构造、限制说明和原生事件到元数据的映射。ZCode 的事件映射原样移到 native_run，新增的 make_inquiry_bridge 是实际构造入口；旧 zcode.live_bridge 路径已删除，没有兼容模块。公共能力表中的 DSH 模式留到 DSH 注册时再改，当前旧路径不提前换行为。

桥搬动及实时角色的 93 项聚焦通过（23.886 秒，日志有一条未关闭文件的 ResourceWarning，未据此声称警告为零）；受影响黑板问询 29 项通过（28.170 秒）。把搬后公共桥的 _bound_record 临时改成恒真，原 journal 重放见证按预期因 q-unbound/q-foreign/q-legacy 混入而失败；未变原件已在 93 项中通过。测试函数编号没有改名、增删，完整集合对账随本批整合检查完成。无模型或安装版原生调用。这是两条 harness 共用设施的 Host 整合，没有复制 DSH 专用运行通道。

4-B1 内部验收整合编号 int-7b218b64-344c-4c29-98e5-d1bcb9304c3f 为 verified，随后 accepted。回收计划 cln-32bd1c86-9abc-4490-a0c0-5ee50b1c8663 已应用且检出实际消失；Host 留存 12 份明确命名的编号/组合/变异材料后，只删除创建时登记的 4-B1 确切任务根，无通配符、不屏蔽错误。4-B2 已从 ee0e2f4 首次走路由提交为 ec5ea096-91cc-4b2e-b795-be9b1b053688，拥有独立检出和新短任务根；公共文件与 Node 树不在其写入范围。

3-B2 固定 7b455ea、artifact 71714cad-2de1-4150-ba9b-8665b6b2ead7、累计补丁 02549ce1610c71e74eb8fec1170569e0a097a2ca73108181fce968f0b24602ee 的九条范围路径已核对。Host 首次聚焦错误地传了相对 PYTHONPATH，子进程从可编辑安装回到主检出，引发 28 处失败和 13 处错误；该环境失误不归给 Worker。改为固定验证副本的绝对 PYTHONPATH 后，165 项全部通过（53.003 秒）。五个新旧模拟 Worker 对照的状态、错误、停止相符，旧配额两字段保留；原中断确认事实仍缺失。固定交付尚未整合。

Host 恢复 Claude 旧的非法 initial/previousSessionId 预拒绝，将角色输入校验留在共享角色，在已存下公共请求后、调用原生 run 前返回 invalid-resume-mode 和确实未启动的停止事实，避免已知非法回合进入模型。无输入发现的失败也经同一外层返回有界原因；只采信驱动在异常上明确附带的 discovery_shutdown_confirmed=True，缺失、false 或字符串均不能作原生停止确认。原生包负责给出实际停止事实，Host 不按错误码猜测。共享层另保留 native-observations 中严格布尔的原生中断请求/确认字段。角色、既有 ZCode 和两个新增回归共 37 项通过（5.535 秒）。Claude 的发现接线、原生错误停止事实、中断回读与无调用方的旧 schema/校验器由原 3-B2 run 修正；这些公共修改不代改其范围内代码。

3-A2 新固定 0ae358c、artifact 036ab5f7-7299-44db-a353-8d40d1cdf326、累计补丁 3dd4c517ed6c5e7e9434fd0f3d0b03c22a552eae782078dc5366081c708b39be 已恢复上轮四项：失败策略回读、真实中断投影、旧断言/身份反例及冗余账户携带。Host 在 1d28a16 加固定交付的无旧入口副本运行 130 项通过（55.474 秒）。公共发现异常停止接缝在这份交付结束后才到达，Host 直接用原 run continue 补齐该新依赖，没有再次把已修的前轮问题记为拒绝；仍未整合本接线微任务。

Host 继续迁移共享测试：实时登记测试逐个要求已登记 harness 有真实绑定，并显式移除一项登记验证缺席路径；平台模拟改到共用资格入口；账户测试读取实际续接绑定中的 credentialSource，保留服务冻结账户优先和原有账户 revision 断言。无工具证据防护改从共享 start_fast 起步，使用真实请求/结果帧和持有方冻结控制绑定，七个原有路径/链接/reparse/I/O 反例原样保留。三组聚焦分别为 18 项、5 项、7 项通过；临时允许修改 _NoToolEvidence 的冻结字段后，原控制重绑定见证在两个子例中均以应有异常未出现而失败，未变对照已通过。测试编号未增删改名；这批是 Host 公共接线测试，未代改各微任务的 harness 文件。

3-A2 最终固定 f07ab2e、artifact 0b74ace2-94d8-4b1c-b002-483fe59332fa、累计补丁 a8eabae5a924d03bf470d461683d1af4847c91019385d7c72e6a83e5990cc620，共 16 条路径（含 runner.py/no_tool.py 删除），整合逐字节一致。最后一轮只在已持有进程后的发现异常上附实际停止布尔；原错误和零提示发现不变。Host 的两项发现测试通过（0.801 秒），先前当前公共基线 130 项已通过，未因这一局部补充重跑整包。三份相关变异的独立重跑均命中目标：原生策略回读丢失、请求冒充中断确认、发现错误缺停止属性，分别以两个断言失败和一个目标属性缺失错误被发现；未变原件均已有通过证据。

Host 激活 Codex 注册，公共身份/账户/实时资格/证据留存聚焦首轮 62 项中 60 项通过。剩余两项均是共享测试仍假定 Codex 未抽取：一项还 mock 旧 start_read_only_structured，另一项用真实 Codex 名模拟未登记。改为 mock 公共 start_review 和显式临时移除登记后，相关九项通过（0.010 秒）；原断言意图不变。旧 mock 失效曾额外启动一个共享控制器（测试日志 PID 51806，伴随 ResourceWarning），之后该 PID 已不在，killpg(51806,0) 返回组不存在；未发送终止信号。该测试当时的临时日志已被 fixture 自动收尾，没有留存足以核定内部原生启动次数的材料，不以此声称该意外控制器做过已安装原生验证。

Worker 记录中的“主检出解释器缺 pydantic”不能用来判断本实施检出的环境：Host 已用本实施检出的 uv 项目 .venv 完成上述独立检查；Worker 实际使用另一个按锁文件离线创建的验证环境，此项验证事实保留。本次内部签收仅覆盖 Codex 接线微任务；全量编号、默认并行完整检查、最小安装版冒烟、未使用字段/类清单和外部 Host 验收尚待，不把 131 个本包编号当作整步结果。

Codex 接线后的第一次默认并行完整检查在两个模块失败：共享控制器的 review 分支未在读取参数前拒绝尚无审阅能力的 ZCode；原 ZCode 取消见证固定等 0.2 秒，可能取消还在导入的外层，取不到原生证据。Host 在公共入口恢复能力检查，在既有假进程夹具加本次 inputId 的提示接收标记，取消后给原停止流程 8 秒收尾，保留原不完整证据断言。修正测试时另发现 no-tool 不允许零超时、空 cwd 不能放标记、2 秒强停会打断约 3 秒的原生收尾；这些失败原始日志全部保留，最终标记放 fixture 根，取消用 10 秒预算，未改生产停止行为。两个失败模块和共用该夹具的 no-tool 模块共 39 项通过（16.294 秒），编号未变。它们属于公共整合与既有测试同步问题，Host 自行处理，没有代改活动微任务范围。完整检查需在本批整合后重跑。

3-A2 已按固定产物内部签收；回收计划 cln-99d4b8a1-346a-417c-974c-4972bf858bee 为 applied/removed，受管检出实际消失。Host 留存 13 份明确命名材料与摘要后，只整体删除创建时登记的 3-A2 任务根，无通配符、不屏蔽错误。

3-B2 修正后固定封存 4930dab（Worker 摘要提交 522e229）、artifact aa007546-aa9e-4999-b5d4-47691d146405、累计补丁 acd8b80166a5ac5b5c0392aa7660fd6b836b65f1d9bfc20b7b8ad99c96df9396，14 条范围路径及删除均已核对。Host 在 ef856ef 加固定范围和 Claude 注册的无旧入口副本独立运行 165 项通过（58.148 秒）。中断确认恒真、发现停止恒真、不写中断事实三份变异逐字核对到固定源码的单处替换，再独立重跑见证，分别因 True≠False、True≠False、None≠True 失败；原件同见证已在 165 项通过。

本轮恢复真实原生中断回复、共享发现外层与保守停止事实、非法 initial 的模型前拒绝；删除无生产调用方的 Claude OUTCOME_SCHEMA、parse_structured_output 与 config 默认回退。Host 只激活公共注册，范围文件逐字节合入。记录所称“Host 实施检出缺 pydantic”实际指另一个 Desktop 检出；本任务实施检出的 uv 环境已独立跑通上述 165 项，Worker 使用同锁冻结环境的验证事实保留。此处以整合登记澄清检出归属，不改变其做过的检查。共享完整检查、真实冒烟与整线外部验收仍待。

3-B2 内部签收编号 int-b806afb4-84f4-4da2-a4a4-41de2cee8a5d 为 verified，回收计划 cln-02d86000-8309-41c0-9fbe-87cd339717a3 最终 applied/removed，实际检出消失。一次显式 apply 因缺 confirmPath 被校验拒绝，未执行删除；随后的只读回查确认黑板已自动应用。Host 留存 14 份指定材料与摘要后，仅整体删除创建时登记的 3-B2 根，不用通配符、不屏蔽报错。

第二次默认并行完整检查基线 5a83d06，466.878 秒，退出 1；176 个 Python 模块中只失败 cli.test_repository_hygiene，原因是 3-B2 记录含本机项目目录层级。其余 175 个模块运行 2,785 项（skip 1），Node 110 项通过；不是整步通过。Host 把该一处路径替换为 <other-desktop-checkout>，只改记录路径表示，不改代码、测试或验证结论，按用户相称性规则直接更正，不为此 continue；此项是内部签收后的记录调整。

本批全量编号按同一 python_test_modules 加 unittest 加载器收集为 2,790 个、176 个模块，加载错误 0、重复 0。相对第二步 2,628 个：2,619 个编号不变，7 个改类/改名有对应，2 个重复 Claude schema/解析器测试随唯一旧使用方删除，由共享 schema-worker 的 schema 哈希与严格业务值校验覆盖；净新增编号 164 个。已列变化两侧从原始集合减去后集合完全相等。[编号表](adr025-step3-test-ids.tsv) 只列变化，原始清单留 tmp；其中包含并行已合入的 DSH 原生主体 42 项，不能把这一共存计数冒充 DSH 接线验收。

[格式使用表](adr025-step3-format-usage.tsv) 复核第二步的 18 组 B 项：InterruptEvidence.requested/basis 已有 Codex native_evidence 的实际读取；3 组部分投影/部分读取；14 组仍没有业务读取。整帧存下与测试断言不算业务使用方。ModelStartEvidence、DeniedInteraction、UnknownEvents 三个格式类当前只有构造/序列化，连同表中其余未消费字段在第四步结束前删除或明确真实读取；不为保留它们造消费者。本步新增的角色参数、网络/拒绝工具请求字段、路径服务绑定均有实际调用。真实检查见[原生冒烟记录](adr025-step3-native-smokes.md)：Codex Worker 1 次通过，Claude 1 次无模型发现遇登录条件，Worker 0 次、未验证、不重试。

第三步最终整合检查基线 bd4324e：uv run --frozen python -m hey_my_buddy.cli.checks，默认并行数、不传 --jobs、清除 BUDDY_CHECKS_JOBS，退出 0；Python 2,790 项（skip 1）／176 个模块全部完成，Node 110 项，用时 459.388 秒。两处代码/同步问题与一处记录路径失败均已闭环，本次同时作为本批整合检查和第三步两条线的整步完整检查，不再重复。代码与测试在检查期间保持该固定版本；之后只补结果记录。Codex、Claude 两条线在此停止等待用户转达 Claude Code Host 验收；DSH 线独立继续，未开始第五步。Claude 私有探针根在所选 CLI 启动前资格拒绝、无 Worker 启动后按登记确切路径回收；所有原始发现摘要保留。

4-B2 首轮在约 7,200 秒窗口后返回 cancelled，黑板 cancellation 为 null、quotaFailure 为 null，原生收据 code=cancelled、nativeExitCode=0，两层停止均已确认；不能把停止确认当作交付。仅有 partial-output 7236ba66-972a-4c73-8bc1-8c44a2f18302，固定 2ded7516，累计补丁 5996ebc63f904ed82cb7607efe55c4d6967e9c377526c4aa2b8794bfb7f7fe01。Host 核对 12 条范围路径和摘要，未整合或签收。初审发现 available/check_preparation 把已有选择记录变成新门槛，丢失原未绑定时的发现路径；正式记录也未提交。原 run 已 continue（revision 5），配置仍为路由决定 dec-1fbe15bd-3790-4df2-af7f-185bf8799b12 选出的 zcode / zai-api / GLM-5.3-Flash / max，没有配置覆盖、没有另开任务。要求局部修复、复用已有聚焦与变异证据、完成编号/偏差记录，公共邻接缺口列给 Host，不反复扩大验证。任务根保留，4-C 尚未开始。
