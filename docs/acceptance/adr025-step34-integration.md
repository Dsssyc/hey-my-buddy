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
