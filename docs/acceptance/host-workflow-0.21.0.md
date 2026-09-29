# ADR-018 第二部分：Host 工作流 0.21.0

日期：2026-09-29。范围为 [ADR-018](../decisions/018-routing-modes-and-host-workflow.md) 第 11–23 条与四个同时修复的缺陷。基线为第一批合入后的 `socu/buddy-core`，提交 `cf4d70a4faa3c9884b4cccb21c1ceb105f46f579`，包含路由双模式合并 `f97dea8`；实施分支为 `socu/host-workflow`，位于独立 worktree。用户先审阅并同意 [schema 15 方案](../design/adr018-host-workflow-schema15.md)，再实施迁移。源码与契约均为 `0.21.0`，schema 为 15。

状态：第 11–23 条、四个缺陷、离线集成、浏览器验证与最终完整检查均已完成，四个委派均已验收并清理受管检出。本轮没有安装日常运行时、迁移日常板、修改用户配置、合并或发布。

## 实现与逐项验证

| ADR 条目 | 实现与证据 | 验证边界 |
| --- | --- | --- |
| 11，精简 SKILL | 源文件 4,084 字节，wheel 中重写参考链接后 4,004 字节；保留 health、路由、同一工作目标、等待、固定成果审查和 Host 权限，细节移入 references。`test_skill_workflow`、`test_host_cli` 检查体积、入口与参考链接。 | 不再携带版本、发布或安装状态；实际安装须单独授权。 |
| 12，文件与标准输入 | `--params-file PATH`、`-` 与 `--params-file -` 和原有单 JSON 参数等价；互斥输入、UTF-8、大小界限、非法 JSON、控制文件和执行凭据均经同一处理。`test_host_cli` 覆盖文件内容不经 shell 再解释。 | 命令参数格式没有启动模型或改变服务权限。 |
| 13，方法帮助 | `help` 列出全部方法；`help METHOD` 从现有校验函数的 AST、签名和常量提取参数、默认值、条件及边界，无法简化的条件附实际拒绝表达式与来源。拼写错误返回最近候选。`test_host_cli` 覆盖全部方法、嵌套配置、输入文本界限和校验常量变化。 | 帮助不执行校验函数、不启动服务或模型；复杂条件保留源码表达式，不假称为无条件必填。 |
| 14，objectiveOf | 服务在提交事务内解析被引用 run 的工作目标，校验原始 Host 与项目归属；不能跨 Host 或借用未归组 run，参与请求幂等。`test_host_workflow` 覆盖归属、拒绝与重复提交。 | 只是归组，不授予其他目标的控制权。 |
| 15，等待 Host 时直接收尾 | `acknowledge` 绑定当前轮封存成果、已核实整合、当前控制权及全部停止证据；写 Host 收尾事件，保留 Worker 原来的 attention/assistance 结果。拒绝后可用新命令和当前 revision 再审；helper 收尾同时结算父子图。`test_host_workflow` 验证无需额外 Worker 回合。 | 活跃后代、未停止执行、待续做或工作区冲突仍阻止收尾。 |
| 16，失败/取消结论与清理 | `verdict:"recorded"` 追加独立 Host 结论，绑定被审查执行、owner generation、成果集合和可选整合记录；不改 failed/cancelled，不计为成功样本。清理仍验证固定成果与当前检出；续做及所有权变化使旧结论失效。`test_host_workflow` 与 `test_host_workflow_worker` 覆盖取消前未执行、失败成果保留、未封存改动拒绝清理。 | 清理只删除已审查的受管检出，保留补丁、清单与 Git 引用。 |
| 17，被打断工作的部分成果 | 拥有子进程的 Worker 在确认停止后封存允许范围内的改动，由服务验证并随失败回执发布 `partial-output`。标明 partial、verified=false、final=false；额度原因和最后助手消息保留。`test_partial_output_safety` 拒绝未停止、明确未开始模型、超范围结果；`test_host_workflow_worker` 驱动真实 CLI/daemon/Worker 和模拟额度失败。 | 不把部分成果当成功；原生进程存活未知时不清理或伪造停止。 |
| 18，更换配置与锁定 | 默认显式配置可由 Host 在 continue 中附理由换为启用且可用的配置，写 `configuration_overridden`；`configurationLocked:true` 保持硬约束。同一检出以固定部分提交续做；Codex 模拟额度失败后换至 DSH，重建输入含上一轮助手文本和未验收/非新指令标记。`test_host_workflow`、`test_host_workflow_worker` 验证禁用配置、锁定拒绝、跨 harness 恢复与历史成果保留。 | 全部额度恢复为离线夹具，无付费复跑；原生 resume 仍取决于各 adapter 已有绑定证据。 |
| 19，hostPaths | `integration-record` 单独核验 Host 补充路径：相对路径、与整个目标成果路径分离，且真实出现在 target 的 before/after 差异中；写入绑定与 verification。`test_host_workflow` 验证真实补充、重叠路径与无实际变更的拒绝。 | 不借 hostPaths 掩盖 Worker 成果的未整合路径。 |
| 20，累计补丁 | 每轮保留增量补丁，同时固定从目标最初 inputCommit 到该输出提交的累计 patch、hash、base/output commits 与 changedPaths。`test_host_workflow_worker` 将最终累计补丁独立应用到原始输入，验证第一轮部分修改和第二轮新增文件同时存在；清理后仍保留累计补丁；`test_scope_recovery` 验证 Host 恢复/采纳后直接交付的 resolved-output 也附累计补丁。 | 老版本既有成果没有补造历史累计补丁。 |
| 21，服务环境白名单 | 冷启动、升级拉起、daemon 派生 supervisor 和显式 worker start 统一使用白名单；剔除 Host 会话标识、凭据和非运行所需变量，保留合法执行路径；单次 CLI 的 scoped credential 保持有效，scoped Worker 不能启动新 Host supervisor。`test_service_environment` 覆盖各入口及开发夹具的精确白名单。 | 不修改用户 shell 或配置；原生 Windows 环境继承尚未真机验证。 |
| 22，逐次 token 用量 | 归一化输入总数（已含缓存）、缓存子集、输出、来源、范围与完整性；与回执同事务保存，重复回执不累计。DSH 原生 session 记录与 Codex 原生 token/usage 通知夹具验证缓存口径、去重、跨轮 delta 和未知字段；Claude、ZCode 有离线协议/控制器夹具。`test_usage`、`test_adapter_usage`、`test_dsh_usage_plugin`、`test_codex_protocol`、`test_other_native_observations`、`test_native_observations` 覆盖采集至存储/读模型。 | 原生主会话/线程以外的独立子会话和计费总额不推算；无证据或不完整记录显示未知/部分。 |
| 23，额度状态与提醒 | harness 独立保存最新原生 quota，健康刷新不覆盖它；按来源、provider、时间和窗口 reset 判断有效性。匹配 provider 的新鲜窗口使用率 ≥90% 或原生明确额度限制会提醒；无百分比的限制记录不造 100%。CLI、控制台分别展示 quotaFailure 和 quota。原生 DSH/Codex 夹具及真实服务回执测试覆盖导入、重放、过期、旧观测拒绝、提交回执提醒。 | 一小时以外、未来或已重置窗口为 stale；ZCode 未确认的额度窗口保持未知。提醒不自动重试、改路由、改启用状态或发起探针。 |

最后助手文本仅保留普通助手文字，排除 reasoning 和工具负载。UTF-8 内容和 JSON 字符串均限 64 KiB，并保留完整原文的 hash、字节长度、来源和截断标记。元数据读取有独立的两秒界限，不能占用无限执行期限。

## 四个缺陷

| 缺陷 | 修复与回归 |
| --- | --- |
| 同版本重装误报 already-current | 对比实际 skill 文件内容后决定 placement；内容改变返回 updated，完整相同才是 already-current。`test_skill_install` 同时覆盖相同版本/契约/sourceCommit 下的内容变化。 |
| sdist → wheel 丢失 sourceCommit | 构建钩子把来源提交固化到 sdist 的 build-info，后续构建优先使用归档身份而非外层无关 Git 仓库。`test_distribution` 实际构建直接 wheel、sdist 和解包后的 wheel；`test_source_identity` 验证嵌套 Git 与 unknown。两份 README 和 operations 使用 `uv build --wheel` 构建候选。 |
| 合成预览落后于契约 | `test_host_preview` 导出 normal/readonly/truncated/error 四类数据，调用实际前端 Vitest API/workflow 解析器；损坏形状会失败。前端 `preview-contract.test.ts` 覆盖 Host 结论、累计补丁、额度与用量。完整仓库检查要求先准备控制台 npm 开发依赖，不静默跳过。 |
| 聚焦测试依赖外围环境 | `test_claude_config` 自带私有 state/runtime、隔离的原生配置与环境，不依赖 `buddy.checks` 清除 Host 注入变量。单独运行和完整套件均纳入验证；没有调用真实 Claude 模型。 |

## 验证记录

Host 的定点验证包含 schema 14 → 15、环境/安装/分发、CLI 帮助、Host 直接收尾和结论、部分成果、跨 harness 续做、用量/额度回执以及前端解析器。所有服务测试使用私有 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`；额度失败来自 `mock_quota_turn_runner.mjs` 与 `mock_quota_codex.py`。前端最终测试为 48 个文件、579 项通过；TypeScript 和 Vite 构建通过。Vite 保留单 bundle 超过 500 kB 的体积提示，不影响构建。

完整检查在冻结的 `18baa33a4f0dfd788e25b5718424f42318b32e61` 上运行 `uv run --frozen python -m buddy.checks`，PATH 前置受支持的 Node 24.21.0；退出码 0。Python 1,581 项全部通过（1,217.091 秒），Node 161 项、14 个 suite 全部通过（28.215 秒），无失败、取消、跳过或 todo。私有根 `/private/tmp/buddy-checks-8gcudqrr` 已由检查器的停止/存活核对流程移除。原始日志为忽略目录中的 `tmp/host-workflow/final-checks.log`。首次完整扫描发现两个旧断言需要同步：未完成原生 turn 的用量应标为 partial；安全测试应检查秘密字段/真实秘密值，不能把 tokenUsage 计数字段当作凭据。另同步了两处启动夹具（补全 runtime stable 字段、区分 Host 启动与 scoped Worker 拒绝），并修复新保护误拦已取消根目标明确续做的回归；相应定点验证通过，最终完整复测已覆盖这些修正。

Host 在隔离合成预览的真实浏览器中核对了逐次用量、缓存子集、Host 补充路径、可展开的累计补丁、部分成果标签、失败结果与独立结论并列，以及额度来源、重置时间、stale 和未知窗口。1280 宽桌面与 390 宽窄屏没有页面横向溢出，浏览器 error 日志为空。此证据针对合成数据和已打包的本次前端；实际服务字段另由跨组件/HTTP/解析器测试验证。没有访问日常控制台或更改其设置。

最终运行时代码冻结于 `18baa33a4f0dfd788e25b5718424f42318b32e61`。使用 `uv build --wheel` 构建 `tmp/host-workflow/dist-final/hey_my_buddy-0.21.0-py3-none-any.whl`（1,788,745 字节），SHA-256 为 `3fdff9fc11cd9fde485613f9743853528f9bdb2ac8f9a974ae10e790691451f9`。已打开 wheel 检查 skill marker、内嵌 package 的 build-info、版本/契约和 Skill 字节数，sourceCommit 均与该冻结提交一致。本轮不安装此 wheel。验收记录及状态索引随后以仅文档提交收尾。

## 试用观察逐行对照

| ADR 原始观察 | 本轮结论与证据 |
| --- | --- |
| Claude Code 没有 skill，启动器在 Codex 插件缓存里 | ADR-015 既有共享 skill 方案保留；本轮检查打包与安装回归，没有新装 Claude skill 或修改用户入口。 |
| SKILL.md 带着过时的发布状态，体积大且混有版本说明 | 第 11 条完成，源码 4,084 字节；发布事实位于 acceptance。 |
| 没有已验证的 Router，默认路由总停在 Host 边界 | 第一批路由双模式作为基线保留；第 11 条要求先读 health。无新付费路由探针，既有原生资格和限制见第一批验收。 |
| 单个 JSON 参数不便传长任务 | 第 12 条完成，文件/stdin 等价与输入边界测试通过。 |
| 需要手动清除 BUDDY_*、ANTHROPIC_BASE_URL；每次调用都经过 uv | ADR-017 启动器路径保留；第 21 条补全常驻子进程环境过滤，聚焦 Claude 配置测试自带环境。没有更改用户变量。 |
| 没有按方法的参数帮助 | 第 13 条完成，全部方法由校验源生成帮助，错误方法给候选。 |
| 后续委派要手动复制 objectiveId | 第 14 条完成，objectiveOf 归属与幂等有服务测试。 |
| 被额度打断的 worker 没有成果，改动只能手工提取 | 第 17 条完成，离线跨进程失败自动封存部分成果；真实付费额度事故未重跑。 |
| 显式配置的目标失败后不能换模型续做，只能另开目标并手工提交半成品 | 第 17、18 条完成，模拟 Codex → DSH 在同一 checkout 续做并绑定部分输出；用户锁定与禁用配置拒绝有测试。 |
| acknowledge 不接受失败的目标；未验收目标的检出无法清理 | 第 16 条完成，recorded 结论不改失败/取消结果；受管检出按停止和封存证据清理。 |
| integration-record 不能记录 Host 的补充改动 | 第 19 条完成，hostPaths 独立核验、持久化并显示。 |
| 已整合的成果只能靠取消或空跑一轮来收尾 | 第 15 条完成，直接收尾及 helper 结算不增加 Worker 回合。 |
| 多轮成果只有增量补丁，整合时要按顺序叠加 | 第 20 条完成，累计 patch 可从最初输入独立应用，并经清理保留。 |
| 等待 Host 的时间轴缺少结束时间、标签被遮挡 | 保留既有 `0ad0e72`、`753981a` 修复；本轮前端套件和合成长标题浏览器验证覆盖当前呈现。 |
| Codex 有效结构化结果被误判、续做丢失上下文 | 保留 0.19.0 修复；本轮 Codex 协议与模拟跨 harness 恢复覆盖 lastAssistantMessage 和原生身份边界，未做新的付费原生复验。 |
| Codex Host 需要轮询等待 | 本轮 Buddy 委派按既有轻量监控子代理流程等待，没有新建 scheduler。 |
| Claude Code 会话恢复后换了 worktree | 属于 Claude Code 桌面应用，依 ADR 不在本轮范围；未验证。 |
| 常驻服务继承了 Host 会话的 CLAUDE_CODE_* 等变量 | 第 21 条完成，服务/升级/supervisor 白名单入口测试覆盖。 |
| 各模型消耗只能事后手工统计 | 第 22 条完成，原生记录归一化并在每次执行和 CLI 展示；缺失字段和未覆盖的独立子会话不估算。 |
| 额度耗尽只能在失败后才知道 | 第 23 条完成已观测额度的提交提醒及控制台展示；无新鲜原生观测时仍为未知，不承诺事前预测所有额度失败。 |
| 同版本重装被报告为 already-current；wheel 丢失来源提交 | 两项分发缺陷均完成，实际多路径构建与安装夹具验证；未安装日常运行时。 |
| 合成预览数据落后于 schema，新控制台无法加载 | Python 预览输出进入真实前端解析器回归；四场景和故意损坏输入覆盖。 |
| 聚焦测试单独运行时失败 | test_claude_config 使用自身私有环境，单独运行覆盖；测试不依赖日常服务。 |

## 委派、未验证项与后续

本轮 CLI/skill、环境/分发、原生观测、控制台四项实现通过已安装的 Buddy 委派，分别由轻量监控子代理等待，均未派 Claude Worker。四项最初提交都由 Host 指定 `adapter:dsh` 并使用 fast 路由；这只是本轮实际执行记录，不表示其他 harness 不可用或新功能只支持 DSH。用户指出该限制无充分依据，后续普通委派不再默认限制为 DSH。Host 负责 schema、工作流、代码整合、纠正与验收。四项均已对固定成果登记整合并验收，受管检出已移除，补丁与固定 Git 引用保留。CLI 与原生观测任务各两轮，整合验证使用整个目标输入至最终输出的差异。

| 委派 | runId | 最终 artifactId | integrationId | 已应用的清理计划 |
| --- | --- | --- | --- | --- |
| CLI / Skill | `cd4b4745-467d-41e4-850f-80548e203012` | `122e6157-e83a-47a7-bdea-a7bc031c415d` | `int-621a7af9-05dd-43ba-8602-e22b9eb54609` | `cln-64c78577-13e4-4731-89c8-cfd1aa2c0a4d` |
| 环境 / 分发 | `de74fe1b-2501-483b-b26d-d9a018eb6692` | `1fe17800-3f02-4bff-afb4-bf41470a4f63` | `int-5b9947a4-533a-4b6a-9b75-bcbf943c99da` | `cln-9ec3f031-f1c7-4e7d-83f8-1623dffc647b` |
| 原生观测 | `5ab7b756-faf7-4bae-9377-70f64bd638d5` | `ab76b419-d65a-432f-a4b5-6f5e3410864e` | `int-4aef5617-4a28-4462-8475-932ac266345a` | `cln-94629a5f-bd5a-40b8-bb45-4a3862b2ce07` |
| 控制台 / 预览 | `ed9c615c-ff43-44bd-b010-98d59104fba7` | `5411a31f-6f4c-42a5-b067-63278db09389` | `int-7be44d3a-1d62-408c-8b7a-2e1dcb0e0292` | `cln-9029e751-0bd6-4f5b-a677-dce785714b61` |

环境/分发任务在 `1b716df` 已完成独立整合验收，其余三项整合到最终运行时代码 `18baa33`。控制台任务清理调用返回一次 `REVISION_CONFLICT`，随后只读 `get` 确认同一计划为 applied、removed=true；现场检出不存在，输出 patch 和 6 个固定引用仍可读。CLI 与原生观测各保留 2 个输出 patch、12 个固定引用。没有把错误回执当成成功，也没有盲目重复删除；冲突产生原因未另行推断。

未验证的是新版本在用户日常板的迁移/安装、本次变更的原生 Linux/Windows、实际付费额度失败后的恢复，以及各 provider 后续版本的记录格式。已有原生记录和模拟 harness 证明本次实现的处理路径，不扩大此前 Router 的原生资格。合并到 `socu/buddy-core`、空闲时安装候选与 schema 15 日常迁移均等待用户另行授权。

## Host 合并验收（2026-09-30）

Claude Code Host 在 `socu/integration-0.21` 上把 `socu/host-workflow`（`ce09950`）合入当时的 `socu/buddy-core`（`3f0a91c`），合并提交为 `137dab1`。唯一冲突在 AGENTS.md：保留已改为只含仓库事实的版本，本分支新增的议程段落不收，只吸收了"完整检查前用 `npm --prefix apps/console ci` 准备控制台依赖"这一步骤说明。随后 `02b9479` 删掉了 skill 与参考文档中属于某一位用户的设定：SKILL.md 的"this repository: Chinese"，usage.md 中"本仓库使用中文"及过时的版本标题，以及 claude.md 中关于 Claude Worker 数量与分工的整段个人要求。`02b9479` 上的完整检查通过（Python 1,581 项，Node 161 项）。

Host 自己审查了 schema 14 → 15 迁移：迁移在单个 `BEGIN IMMEDIATE` 事务中只增加列和 Host 结论表，前后比对原有列指纹，完成后做外键与完整性检查，失败整体回滚；历史目标默认不锁定配置，与已同意的方案一致。独立只读审查（run `b90b02a8`，路由按软偏好选中 Codex GPT-6 Sol high，未加硬约束）报告三项，Host 逐条对照代码确认成立：Codex 与 ZCode 在发出请求前就把 `modelStarted` 置为真，模型被拒绝且没有改动时仍会发布空补丁的部分成果；额度中的标识类字段只检查长度，可能存下原生错误原文；新增的服务环境白名单漏掉 CA 证书路径变量。修正任务（run `2483361f`，路由选中 ZCode GLM-5.3 max）使部分成果只在确有改动时发布（防重试的 `modelStarted` 语义不变），额度标识字段只接受 `^[A-Za-z0-9._:/-]{1,64}$`，并把 `SSL_CERT_FILE`、`SSL_CERT_DIR`、`REQUESTS_CA_BUNDLE`、`CURL_CA_BUNDLE`、`NODE_EXTRA_CA_CERTS` 加入服务、原生子进程与 Claude 三处白名单，均有回归测试。Host 读完每处改动后原样整合为 `2f930a6`，其上的完整检查通过（Python 1,590 项，Node 161 项，运行时 PATH 前置 Node 24.21.0）。两项委派都已记录整合并验收。

0.21.0 尚未安装；日常板从 schema 14 迁移到 15 需要用户另行授权。
