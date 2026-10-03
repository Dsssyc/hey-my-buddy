# ADR-025 Host 整合登记

本登记记录本 Host 已核对的事实及自己处理的公共文档变更；不替代各微任务的固定交付、步骤完整检查或 Claude Code Host 验收。实施分支为 `socu/adr025-run-module`，证据分支为 `socu/adr025-feasibility`。第零步实施 worktree 始终只有当前搬动微任务写源码；证据分支只承接记录与经用户确认的计划修订，待当前微任务停下后再整合。

## 已核对和提交的输入

| 项目 | 固定事实与整合 |
| --- | --- |
| 上游与计划 | 合入用户指定 `dd8a9ab`，merge `daa8344`；按用户决定修订计划及 635 行文件表，提交 `006b2fb` |
| 0-A | run `cdc575a5-9b86-4989-a871-58ea6a33f5a7`，artifact `8e5c3e77-84f4-42e3-b9d4-74690c413e60`；记录按固定 seal 原样提交 `b22fc63`，整合 `int-70d0b40b-38c8-480d-8fe3-cec1b0db96b4`，已验收本微任务 |
| 0-A 核对 | Host 核对记录字节与 seal 一致、三份编号原始清单的计数及 SHA-256；Python 2,316、skip 1，Node 110，完整命令 exit 0；Vitest 659、tsc exit 0；私有检查根已回收。Console build 留待搬动后的构建定位核对。第零步整体尚未验收 |
| 0-B 拆分 | 当前黑板的整合核对限 512 条变更路径，292 次移动已占 584 个旧/新路径；Host 将原 0-B 细化为 0-B1 源码及引用、0-B2 测试目录两个串行微任务，提交 `053b5c4`，保持总范围和最终验收门槛 |
| F-D1 | run `5033ed4e-e3c3-4e64-a004-259c45a7e7d6`，最终 artifact `af5d570f-aff5-413e-ba2b-9af4cbcdad40`，固定 output `3e1254231a430c1910280e1bfd31767ea9f8faac`；记录字节原样提交 `b02ac13`，整合 `int-5a4f950c-1298-4535-b5bf-c78ba33af913` |
| F-D1 审查 | Host 两次拒绝并 continue 原微任务，修正启动次数、日常目录副作用、持久化检查点与问询的混淆、HTTP 声明与实测的混淆、后来包装器与历史启动环境的混淆；验收的是六问免费部分的有界事实，没有宣称六问全通过 |
| F-C1 | run `25f131ff-d8a6-4945-8ce4-c65164a8e3ee`，最终 artifact `b0cbda76-36fb-4f00-ab2e-ddc90a4efcf7`，固定 output `f444654fca7af937348564dfcf0acb35b0b49fc9`；记录字节原样提交 `108a540`，整合 `int-d96f4f41-10c5-483f-a559-10b75b2b5df4` |
| F-C1 审查 | Host 核对公开 API 驱动及原始结果；拒绝并 continue 一次，收窄 SIGKILL 与正常退出的推论、校准墙钟数值；独立观察 42 个已捕获进程组均不存在、已知套接字无残留。本机核对通过，零 harness/模型调用；此线已停下等待 Claude Code Host，未开始 5-A |

F-D1 实际启动 DSH 9 次，其中 ACP initialize 握手 3 次，模型调用 0 次。第 4 次诊断调用未设私有 DSH_HOME，创建了日常 `~/.dsh/profiles/acp/`；该微任务随后删除了自己创建的目录，仍留下 `~/.dsh/profiles` 的 mtime 变化。第 1–2 次在目录快照之前，写入影响未知。这是已发生的边界违反，后来的修正与私有包装器不能抹去；Host 未恢复日常 mtime、未读取凭据文件内容。

## 0-B1 的前提失败与同一微任务恢复

run `37e8b8db-b4c5-40eb-b685-f170ea64f90e` 的原输入为 `053b5c4`。首轮完整检查有 9 个模块失败，出现旧 `adapters/__init__.py` 与 `worker/__init__.py` 在新分组中引出的包初始化循环；进行中的绕过把类型导入放进 TYPE_CHECKING、把 Worker 的注册表导入移到函数内，超出本步只改路径的边界。Host 另在固定部分交付中发现 `THEME_STORAGE_KEY` 被机械替换错：原值 `hey-my-buddy.console.theme` 不应变为 `hey-my-hey_my_buddy.console.server.theme`。这些都未获验收，Host 没有代改源码。

Host 先发关联问询 `adr025-0b1-circular-boundary` 要求报告并停止，但该问询仍排队未送达。为落实用户的前提失败暂停要求，Host 通过黑板停止当前回合；保留同一 run 和 worktree，没有另开替代任务。黑板确认自身与后代停止，留下 partial artifact `f8e9500f-59c1-4980-805b-8c333615f98e`，output `d1e608951d3b2b37168d0b09bdcd7b2dcadf303c`，仅作未完成材料。第一轮失败日志和第二轮中断日志保留；第二轮私有检查根仍留存，Host 按该根扫描进程命令行未发现引用，这不是独立的全进程清理证明。

Host 将两行整文件搬动建议提交为 `45c39ad`。用户明确同意并要求继续原微任务后，Host 提交 `4e430e5`，只修改计划、文件表和提案状态：注册表改到 `buddy/harnesses/registry.py`，原 Worker 包 API 改到 `buddy/runtime/api.py`，两个新包入口为空；源码/资产原文件数仍为 114。Host 通过同一 run 的 continue 恢复，要求撤销加载时机绕过、恢复稳定主题 key、修复路径失败和测试发现数量差异，再做完整检查。该修订当前保存在证据分支，原微任务按固定 commit 读取；实施分支上的公共文档待固定交付后统一整合。

## F-D2 的首次审查

run `2d37e630-01d8-4249-8550-2eb7f1f61feb` 以 `108a540` 为输入。首次固定 artifact `3b60a171-c346-4255-9008-0d9c1617ce63`、output `27f9793d450de1b00c6adb1c952d6d8aaed340e9` 只修改 DSH 核对记录。报告记载真实模型 prompt 2 次，其中一次被探针缺陷中断；权限请求 0 次，实际写文件、命令和环回 HTTP 均执行。Host 拒绝其“无失败、无需退路、六问均通过”的总判断，要求原微任务继续核对公开的原生工具/权限选择和 SDK 退路；该纠正回合不追加原生模型 prompt。

本次继续还要求收窄 quota 与实时输入的断言、按实际 MCP 请求/挂载区分次数，并保留尝试 4 原始帧/摘要被覆盖的缺口。原生会话记录可以支持模型/工具事实，不能替代进程退出码与组停止证据。Host 曾在覆盖前读到该次摘要中的 exit 0/group gone，但那份摘要已不在交付材料中，不能重新写成原始日志。此线尚未验收，也未开始第四步实现。

## 原始材料留存

黑板当前记录显示 F-D1、F-C1 各自有已经 applied 的 workspace cleanup，分别在 2026-10-03 06:29:34 UTC、06:30:28 UTC 移除了原 worktree；固定 artifact、Git ref、patch 和入库报告仍在。Host 在验收前已经读取相应原始材料，之后复查确认原 worktree 的忽略目录已不存在；本登记不推断是哪一个调用方发起清理，也未改变用户的清理配置。F-D2 因此按已提交的 F-D1 契约重建了私有启动包装和客户端。

F-C1 原先写入短探测根的五组 raw 输出仍在，Host 已另行保留到 `<evidence-worktree>/tmp/adr025-evidence-retained/fc1/`，共 57 个文件；42 组停止观察保留在 `<implementation-worktree>/tmp/adr025-host/fc1-host-stop-observation.json`。原可复用脚本随旧 worktree 清理，未在这份 raw 副本中恢复。F-D2 的后续验收会先保留本次必要脚本和原始证据到 Host 的 worktree，再确认黑板验收，避免依赖随后可能被清理的工作区。

## 0-B1 固定交付验收

原微任务经同一 run 的 continue 修复后，最终 artifact 为 `268d5c0a-38a7-402f-98b6-ababe757fa2e`，output 为 `2cd21d8eb713f41da36d210b40b59c718e1b114c`；Host 确认实施 worktree、暂存树与固定输出树完全相等，提交为 `2fadf958295a5133fe9823d391f3f8eb88e08fb9`，整合 `int-b3e4f146-2f8c-4c4b-b55f-e8d88e9a3b82` 后验收本微任务。114 个源码/资产整文件移动、19 个空包入口及路径适配均由原微任务完成，Host 未代改源码或测试。

固定审查还发现旧布局目标运行时的三处入口被硬编码为新包：升级探测的 client、回滚启动的 daemon、launcher 执行的 CLI。Host 拒绝并 continue 原微任务，以目标自身 `src/<package>` 定位入口；真实旧源码的一次性私有复现验证修复。首次新增回归依赖 Git 历史、外部 tar、额外 Python 和网络安装，Host 再次拒绝并 continue，改为标准库私有旧包夹具及当前 Python 的隔离子进程；保留同 4 条测试编号。Host 独立在清空环境与受限 PATH 下运行这 4 例，全部通过。

Host 核对原 2,316 条 Python 编号全部保留且 Counter 无减少，恰新增 4 条旧布局入口回归，无重复；完整日志为 Python 2,320（skip 1，与基线同项）、Node 110。最终回合仅修测试夹具与记录，生产树与这次完整检查的固定树相等；Console 659、tsc、构建及 wheel/sdist/skill 的通过证据仍适用。Host 直接检查最新 wheel 的 310 个成员，顶层只有 `hey_my_buddy` 与 dist-info，没有顶层 `buddy/`。最终第零步完整检查仍由 0-D 重跑。

Host 将 0-B1 必要脚本、日志、编号清单及最新 wheel/sdist 共 202 个文件另存到 `<evidence-worktree>/tmp/adr025-evidence-retained/0b1/` 并写 SHA-256 清单，再执行黑板验收。两份记录中对曾损坏 key 的引文是历史说明；恢复断言针对实际程序常量和构建内容。后续顺序为整合已批准的公共计划与核对记录、0-B2 测试目录搬动、0-C 私有安装升级、0-D 整步收尾；第零步尚未交给 Claude Code Host 验收。

## F-D2 固定交付验收与暂停

F-D2 最终 artifact 为 `9ea27eb1-db6d-4f42-bf52-4197fd82912d`，output 为 `ad682eeec3227c0e772eb09c753d3ce358d71362`。Host 按固定 blob 原样提交记录 `3ca7e78cbf48d145e6c7d2c0fed081cfd680730a`，整合 `int-f69bf523-6566-4dd5-b9c1-eb43388bcb79` 后验收该微任务；F-D1 的 118 行历史完整保留，未修改生产源码。这条核对线与 F-C1 都已停止，等待用户转达 Claude Code Host 验收；第四步及 5-A 尚未开始，独立的第零步继续。

Host 对原微任务四次拒绝并 continue：收窄“六问全通过”的无依据判断并核对公开控制面及 SDK 退路；补充 none/read 原生开关的实际证据；移除 none 中残留的 `exit_plan_mode`；修正仍停留在旧回合的 MCP 次数与会话范围。全部修正仍由原微任务完成。最终公开 patch 禁用 14 个任务工具提供者与 plan-mode 后，经免模型启动和一个最小真实 prompt，原生 `request/header` 只含本次完成工具；Host 独立读取该私有生成的会话记录予以确认。read 预设的两次升级请求均被 reject，写入未发生，正向读取成功；write、用量、模型配置、真实完成调用及检查点问询分别留证，不扩展为平台或厂商静态证明。

F-D2 合计 DSH 原生启动 20 次（9 次 CLI、11 次 ACP），真实 prompt 5 次、模型步 21 步；最后修记录的回合没有新增原生调用。与 F-D1 合计为 29 次 DSH 启动、5 次真实 prompt，执行微任务本身的 buddy 与 Router 模型运行另计。F-D2 各回合日常目录元数据快照无变化；这不能撤销 F-D1 的已记边界违反。尝试 4 停止摘要被覆盖及早期 5 次 CLI 未留独立停止摘要的缺口继续明示；read 网络、允许路径、真实运行后续接、取消中断、HTTP MCP、cost/quota、其他平台保留未验证。

Host 在验收前将必要 Python 探针、日志和仅本次生成的会话记录共 100 个文件保留在 `<evidence-worktree>/tmp/adr025-evidence-retained/fd2-turn4/`，写逐文件 SHA-256 并核对最后一轮没有改变这些原始材料；未复制其他 home 文件或凭据目录。最终报告与 seal 字节相等，核对分支的变更仅为本报告；后续在实施微任务停止后再合入实施分支。

## 两项核对的外部验收与后续约束

用户随后转达 Claude Code Host 对 F-D1/F-D2、F-C1 均已验收，权威记录及 ADR-025 第 11、13 条更新在 `socu/buddy-core` 的 `fcf294773818c909c4fca6c9c869a1fdb5cbd86c`。Host 已在证据分支合入该提交，更新仅来自上游原文的 ADR、文档索引与 Host 验收记录；本 Host 没有自行改 ADR。实施分支由当前微任务独占，待其停下后整合。此前本登记中的“等待外部验收”是当时状态，现由这次用户转达解除；第零步尚未验收。

Host 只修订执行计划以承接用户的新要求：DSH 工具范围由启动配置满足，权限回调只处理升级请求；4-D 补命令写文件的只读验证，此前不宣称命令受原生只读强制；按原生工具名归类，未知仍为 other；私有会话身份/用量只作可选来源、用 Python 解码、不新增系统 zstd 命令依赖，Worker token 用量退步必须停下；未知工具行不宣称已限制；任何 DSH 启动都须私有主目录。C-Two 以回读地址路由，人名只显示，无重名换名重试；两项进程全局设置在首次连接/注册前完成；Worker 确认控制器组消失后，只删除自己记录且身份仍匹配的套接字。DSH 的独立 ACP 客户端在第零步验收后可开始，不等第三步。

## 0-B2 的越界清理与实施暂停

0-B2 run `633a8b0d-5689-4fcb-aae9-04272c43378f` 已提交测试搬动，但没有通过验收。其第一份 attention 输出为 artifact `abb0f48b-de54-4fdb-9c27-e727b6bd52da`、output `93e394b12dfb91bc6e1b7c9e60e34782b9cac7ff`；实施分支上的对应提交为 `5eaef5a`、`c0e098e`。Host 独立将 0-B1 的 2,320 条编号按 TSV 转换，与搬后编号的 Counter 和集合比较，增减与重复均为零；已读第二轮 Python 2,320（skip 1）日志。Node 的 `native-usage.test.mjs` 仍用旧 fixture 路径，3 条测试被 ENOENT 挡住，完整命令退出 1。未把这份 attention 当成通过，也未开始 0-C、0-D 或第一步。

交付记录披露清理了其他会话遗留的临时根。Host 当即按用户第 15 条暂停第零步实施并告知用户；没有修改 Node 路径或扩大其 scope。黑板对 attention 输出的 `acknowledge rejected` 返回 `NOT_READY`（该 verdict 仅接受 delivered goal），没有发生状态修改；Host 随后对同一 run 使用 continue，明确不予验收，且只授权核实已经发生的动作和纠正记录，不授权恢复实施、运行验证或清理。审计回合已停止，自身与后代 shutdown 均确认，状态仍为 awaiting-host。

审计的固定输出为 artifact `838ea3c5-4e82-4fb3-9ab3-2e914ad47680`、output `abef96ad04d3611dc479895491b931e9f66744ee`，对应记录提交 `cefc7a5c44c977b81152d847a3800ac95667193f`。相对上一固定输出只改本微任务的验收记录；Host 没有代改它的源码、测试或记录。原微任务承认执行了 `rm -rf <system-user-temp>/buddy-checks-*`，没有逐根选择条件，且抑制了 stderr；此前“逐根核对后清理”的表述被撤回。删除前两份截断列表可辨 14 个不同旧名称，其中 5 个有 2026-09-30 的 mtime；实际删除总数、内容类型、备份是否覆盖和恢复能力均未知。不存在证明这些旧根归本次任务所有的证据。没有尝试从未知来源恢复，也没有扫描用户备份或追加清理。

原始删除命令与列表由原微任务依据其会话内工具调用回写到 ignored 的审计材料，未形成删除前的完整清单或独立原始删除日志；Host 已读取这份材料，但不能把回写内容升级成自己在删除现场取得的证据。完整审计与撤回见 [0-B2 记录](adr025-step0-test-move.md) 及 `<implementation-worktree>/tmp/adr025-step0/test-move/temp-root-cleanup-audit.md`。本次测试材料和审计已另存到 `<evidence-worktree>/tmp/adr025-evidence-retained/0b2-paused/`，逐文件 SHA-256 清单只覆盖本次留存材料，不能当作被删旧根的备份。

第零步现在等待用户裁定是否恢复原微任务。若获准，仍使用同一 run：先把唯一缺失的 Node 测试文件加入 scope，再 continue 修一行 fixture 路径、完成检查与固定交付审查，然后进入 0-C 私有升级和 0-D 整步验收。后续只清理明确由本次创建并持有的对象，任何同名、旧日期或无进程引用都不能替代归属证据。

## 用户核对后的 0-B2 恢复

用户明确同意恢复同一 0-B2 run。用户转达 Claude Code Host 已核对影响：被删的是用户临时目录下检查运行器的私有根，可辨认的都是 9 月 30 日的，没有日常数据；`/private/tmp` 下 26 个未动，现在没有进程引用这类目录。用户决定删除对象不恢复、不再追查。原越界清理登记、撤回与当时未知项保留，本 Host 未追加扫描或修改原审计记录。

Host 为原 run 将 scope 从 1 增到 2，仅追加 `harnesses/dsh/tests/native-usage.test.mjs`，并 continue 授权一行 fixture 路径修正和完整检查。每个后续微任务描述都加入用户的新规则：只删除本次创建且在创建时记下确切路径的对象，删除不用通配符或屏蔽报错，不因同名、旧日期或没有进程引用推断归属；一次性材料在本检出的 ignored tmp，确需系统临时目录则先建任务专用目录；其他会话遗留对象只报告不动，交付列实际删除对象。

第一次恢复因 `WORKSPACE_BASE_MISMATCH` 停在准备阶段，没有启动新的实施回合；黑板的继续固定输入是最后封存的 `abef96ad`，此前 Host 合入的六份公共文档使当前文件树不匹配。Host 用普通提交 `9bf26e84f6e1e791d26aecb338c36dc6261bbd43` 暂时延后这六份文档的内容，确认完整暂存树与固定输入相等，所有原提交和批准内容仍保留。随后对同一 run 再次 continue；原 scope2 与恢复授权不变。微任务验收后由 Host 撤销这份延后提交，恢复 `fcf2947`、计划和整合记录；没有重置源码或取消重开微任务。

## 0-B2 固定交付验收与文档恢复

0-B2 最终固定输出为 artifact `0bf83c69-8c00-4389-8387-70df3b4c4bb5`、output `a22b2bbe40d23ada1286dae63e6e217b7656b793`，实施提交 `9dc4b9581d99d81a7b10692cd26855d52bc7d3b5`。Host 独立检查 173 个映射 Python 文件：129 个在导入/路径归一化后相等，其余 44 个逐项审阅为路径与导入变化，没有新增或删除定义；9 份非 Python fixture 字节相等，27 个新测试包入口为空。真实 2,320 条编号的模块映射 Counter 与集合均相等；最后恢复回合只改 Node 的一行 URL 与新增记录小节，旧记录在移除新增小节后逐字相等，包括越界登记、审计与撤回。

Host 核对完整命令退出 0，Python 2,320（原有 skip 1，158 个模块）、Node 110；110 条 Node 编号 Counter 和集合与 0-A 相等，SHA-256 仍为 `90aae667edd078d385bb36a5ec579e933d3414572fc728b27efd291684cf3612`。Console 659/tsc 退出 0 的同代码证据保持，迁移 fixture 防护的故障注入 0→1→0 已读。必要原始材料 409 个文件另存到 `<evidence-worktree>/tmp/adr025-evidence-retained/0b2-complete/` 并逐文件记 SHA-256。整合 `int-63e89157-beaf-4c3e-ac21-4357a2064537` 后，Host 接受该微任务；不是整个第零步的验收。

验收后 Host 用正常 revert 撤销文档延后提交，完整恢复六份批准文档，再合入用户恢复授权与清理规则记录。此过程只修改 Host 管理的公共文档，未代改微任务源码/测试。`fcf2947` 的 ADR 仍是用户上游原文；所有越界登记及撤回保留。后续按计划串行开始 0-C、0-D，整步完成后停下等 Claude Code Host 验收。

## 0-C 固定交付验收

0-C run `73dc5a3a-946b-4c0d-bf88-9638b5264408` 的固定 artifact 为 `1828a4e4-e258-4fbe-934d-c34502b88773`，output 为 `35067dde9193c67d3882297bc83dd0fd348cf080`；Host 将唯一新增的升级记录原样提交为 `a093a05307dc9129175936cc4f9c4c6b8c34d260`，确认整棵提交树与固定输出树相等，整合 `int-26ec13fe-3059-4145-8e1e-fee4d9ebc31a` 后接受该微任务。生产源码、测试与既有记录均未修改，Host 没有代改微任务的交付。

Host 独立核对旧、新、故障 wheel/sdist 的 SHA-256、sourceCommit 和包布局：旧制品确来自 `2bdb497`，新制品确来自 `2b08c945`，新 wheel 无顶层 `buddy/`；故障制品只有私有 daemon 启动注入。实际旧布局安装升级成功，实际启动失败后自动回滚到旧布局；两条链保留的旧回执均逐字节相等，升级后和回滚后的合成 command 微任务均实际完成。六个安装相关测试模块共 90 例通过，无 skip；迁移定位防护的缺包、双包、缺资源、缺清单注入分别被发现，READY 绑定副本路径导致的验证限制已在交付中明示。

私有 stop 回执没有未确认尝试；Host 再核对两链确切目录的进程参数、已登记 daemon PID 与两个私有监听端口，均无存活对象。原生 harness/model 调用为 0，只运行了四项合成 command 微任务；此计数不含路由与执行 buddy。Host 在验收前另存 148 个必要脚本、日志、构建制品与链条身份材料到 `<evidence-worktree>/tmp/adr025-evidence-retained/0c/`，逐文件 SHA-256 相符，未复制整个 home 或凭据目录，未删除材料。实施检出的完整私有链仍保留。

0-C 接受仅解除 0-D 的依赖。0-D 必须运行最终 Python 与 Node 完整检查，核对编号集合、全量文件映射、跨边界导入及受保护文件；相同代码树的 Console、构建与真实升级证据可明确引用。第零步整体验收仍由 Claude Code Host 经用户转达，第一步与独立 ACP 客户端尚未开始。

## 0-D 固定交付验收与第零步停止

0-D run `dc5dfbbf-d961-4b13-b98b-8dabf3643ab8` 的首份固定 artifact 为 `0439ced0-bc64-4fb1-ae81-943712f83b8c`、output `7691e348d54a438d626ffbaa7892c765fef6a60e`。Host 拒绝该交付并对同一 run 使用 continue：清理名单实际 45 项而记录写 44；删除的失败与重试、专用容器的归属依据和 Vitest 临时材料的位置偏离需要准确登记；原生调用 0 必须限定到 0-D，代码树相等证明必须限定路径。原微任务完成全部修订，Host 没有代改它的记录、源码或测试。

更正后的清理登记保留两次 `rmdir` 因非空而失败的事实、随后逐项删除及成功收尾；45 项为 42 个测试日志目录、1 个设置目录与 2 个客户端目录，另行删除的 uv 锁文件不在这份名单内。首次 Vitest 的 JSON 与缓存临时落在 `<repo>/apps/`，偏离一次性材料须在 ignored tmp 的规则；本次对象已按确切路径删除，偏离没有写成遵守规则。独立删除日志未保存的部分明确为会话回写，不作为原始凭证。该更正回合没有追加测试、构建、服务启动或删除，也未恢复或追查 0-B2 已裁定的旧事件。

最终 artifact 为 `1f1e8673-aa97-4beb-b505-1d4fd2a4c59d`，output 为 `751a37a1597d4c01d41d3f16bfea8be085004c9f`。Host 审查完整累积输出的两份文档（本回合的增量只改整步记录，跨边界 TSV 沿用首轮），提交为 `f08397039e1f6942609e3eb19b55a0c057a386b8`，确认整棵提交树与 seal 相等；整合 `int-1014a4a5-153d-4739-b7fd-5ade5614f7f8` 验证后，Host 接受 0-D。整步记录中的 HEAD 与两份文档差异证明指核对时的固定输入 `dfb8159`，后续交付提交增加验收记录，生产/打包/测试/Console 树继续相等。

Host 独立验证原 2,316 条 Python 编号经 171 行模块表转换，加 4 条已登记回归后与当前 2,320 条的 Counter 和集合分别相等，无重复或删除；158 个测试模块与原位两模块的口径一致。Node 110、Vitest 659 的 Counter、集合及排序 SHA-256 均与 0-A 相等，无 skip。最终完整检查退出 0，Python 2,320（原项 skip 1）及 Node 110；当前 Console 659 与 tsc 通过，Vite 资产构建按代码树相等引用 0-B1。跨边界 54 条依赖经独立扫描确认，两处 `from package import module` 在 TSV 中精确登记到子模块；只登记，未修依赖。174 份构建文件/制品的摘要相符，新 wheel 的 310 个成员无顶层 `buddy/`，wheel/sdist 的 sourceCommit 为固定输入 `dfb8159`；0-C 实际私有升级与自动回滚证据保持有效。

Host 在接受前将 404 份必要原始材料与回写整理保留到 `<evidence-worktree>/tmp/adr025-evidence-retained/0d/` 并核对逐文件 SHA-256；没有清理材料或复制私有 home/state。0-D 修订相对首份 seal 只改整步记录，源码、测试、既有验收记录与模块表不变。0-B2 越界登记和撤回保留。第零步实施与记录到此提交，停止等待 Claude Code Host 经用户转达整体验收；第一步、DSH ACP 客户端与 C-Two 后端均未开始。
