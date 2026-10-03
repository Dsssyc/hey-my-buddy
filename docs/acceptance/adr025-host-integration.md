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
