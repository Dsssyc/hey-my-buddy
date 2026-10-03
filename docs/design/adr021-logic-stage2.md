# ADR-021 逻辑线第二阶段执行计划

本宏任务在独立 worktree 的 socu/adr021-logic-stage2 实现 L4 和 L5，最后按用户 2026-10-03 的方向调整收尾。已合入 socu/buddy-core 的 26f9dd0，遵循 ADR-021 第 2、3、4、20、23 条与 ADR-023；不修改 main、core 或其他会话的检出，不推送、不安装、不改日常配置与数据。最初计划与历次修改保存在本分支提交历史及备查分支 socu/adr021-review-carrier-reference（15be750）。本文件现在描述最终交付；早先的 DSH/ZCode 独立只读通道设计不再执行。

## 最终范围

L4 全部保留：有序 Router 列表、全局模式与预算、默认 600 秒的可设重试间隔、唯一 current_router 解析入口、按既有记录暂时跳过与恢复、同一请求的顺序切换、每个 Router 的健康统计、Host 路由边界四项信息、冻结与认领/发布防护、免费资格检查、付费证书与入口退役、控制台最小适配，以及 accept/conclude 的必填 note 帮助。

L5 保留：共同 toolEvidence 分类、绑定和完整性事实、黑板唯一发布判定、Codex/Claude Code 审阅与各 harness 快速运行的原生事件投影。适配器只记录事实，不自行判定类别是否合法。系统沙盒档允许 read/search/execute，无系统沙盒档允许 read/search，快速模式要求零工具；未知、冲突、foreign/child/MCP、迟到、截断和不完整仍拒绝答案。已验收的四种投影及其相关回归保留。

原 DSH/ZCode 只读 Node 桥接、Python 控制器、ZCode 受限 session 协议、安装包静态证明与专属检查从本阶段移除并保存备查。最新 ADR 第 4 条改为复用 Worker 载体，禁止另造 Router 通道或证明厂商代码；新 L6 将在另一个阶段先设计。本阶段不实现新 L6，不继续静态核对，也不再发起任何原生检查；此前批准与全权测试授权由这次停止指令撤回。L7 及以后仍不开始。

## 保留接口与不变量

RouterSettings 的现行字段继续是 routerProfileIds、mode、budgetPreset、retryIntervalSeconds，列表非空且身份完整，稳定去重、顺序有效，全局模式/预算不降级。convert_legacy_router_settings 只是可调用的纯换算：按旧默认模式决定两个位置的先后、两者都保留并稳定去重；不用于日常读取或保存，实际升级归 L15。current_router 是三个工作面后续复用的唯一解析入口，不新增持久熔断状态；从当前设置、资格与不可变 outcome/receipt 推出跳过、到期和可用项。

请求冻结 Router 列表、共同输入、模式、预算与候选事实；每项使用独立内部 dispatch task/attempt，业务请求身份不变。前项没有答案且实际停止已确认才能推进；有效选择/弃权停止链，取消、停止未知、身份或输入/设置/候选/账户/reader/owner 漂移不切换，容量与 writer gate 排队。认领复核和发布时的冻结、防护、工具证据判定保留；后来缓存资格变差不覆盖已核对的原生策略与事实。失败和超时进入各 Router 的健康统计，停止未知不伪装停止。

Host 边界返回性质与原因、逐项尝试与恢复时间、冻结候选和程序事实、带实际 owner/revision 的继续命令；不以后来目录补造事实，未记录的恢复时间为 null。命令模板是程序准备的信息，不是自动选择新 buddy 的权限。

DshAdapter/ZcodeAdapter 不声明 read_only_structured 或同 attempt 审阅继续能力；local_read_only_check 不读安装包、不启动程序、不调用模型，返回 eligible=false、systemSandbox=false、reasonCode=readonly-worker-carrier-unimplemented，并解释 Worker 载体审阅尚未实现。start_read_only_structured 继承未实现入口；通用 start 和两个 controller 的手工 readOnlyRequest 也在启动前拒绝，防止绕过声明。普通 Worker、原生 continuation 与 no-tool 快速入口及其停止/EOF/零工具回执保持现行实现。测试专用 capability shim 仍可模拟 L4 的审阅防护，不能改变真实适配器资格。

ZcodeToolFacts 是 L5 的事件投影，保留已验收的 canonical/operation/telemetry 关联、同一 call 的冲突与批次事实检查；它不依赖被移除的受限 session 或静态证明。投影的独立测试直接提供可信根回执和原生帧，不再导入只读协议；协议专属序列/checkpoint 检查随原通道移出，现有 fast 协议与零工具回归仍负责它们自己的边界。

Codex/Claude Code 保留原生沙盒、策略核对和工具流。原生检查已停止：Codex 本阶段一次通过；Claude 完成真实读取，但 StructuredOutput 被记录为 other，且模型没有回传文件标记，因此本次未通过。记录保留失败，不为通过而过滤工具、放宽黑板或修改 ADR；后续处理另行安排。现有原生运行没有重做。

## 收尾微任务

| 微任务 | 改什么 | 不改什么 | 验证 | 依赖与顺序 |
| --- | --- | --- | --- | --- |
| S2-R8A 保存与合入决定 | 将 15be750 保存为独立备查分支，合入 core 的方向和术语决定 | 不移 core/其他检出、不推送 | 分支指针、merge、当前 ADR/CONTEXT | 已完成，后续起点 |
| S2-R8B 撤下未实现资格 | DSH/ZCode 声明、免费原因、通用与直接 controller 启动前拒绝 | Worker/fast 路径、字段名、原生账户 | 无进程/无文件检查、明确原因、入口拒绝、registry/health | R8A 后，Host 单写 |
| S2-R8C 移出独立通道 | Node 桥接、两个控制器/受限协议/静态模块及专属 fixture/tests；保留公共投影 | 不删除已有 L4/L5 防护与 fast/Worker 场景 | 无残留 import/resource、打包不含移出文件、公共投影与 fast 回归 | R8B 后，Host 单写 |
| S2-R8D 对齐回归与文档 | 真实不可用断言、投影测试解除旧协议依赖、参考文档/计划/本阶段记录、宏任务/微任务用词 | 接口/存储重命名 L14、SKILL/Host 指南/README L16、界面 U1/U6 | 保留原身份/预算/冻结/停止/证据场景，文档路径和段落约束 | R8C 后 |
| S2-R8E 冻结完整检查与交付 | 完整检查、实际总数、最终提交和时长表 | 不发原生检查、不开始新 L6/L7 | uv run --frozen python -m buddy.checks；检查期间无文件变更 | R8D 后；退出后仅填录数字并做文档检查 |

这些收尾微任务由 Host 实现；范围已确定，不新增写入者。原实现微任务的结果、实际执行时长与回收结论保留在验收记录；失败的成果不写成已验收。所有原微任务的受管检出已回收、子分支已删除，备查分支仅保存代码与历史。

## 检查安排

Console 依赖已用 package.json 支持的 Node 24 执行 npm --prefix apps/console ci，最小适配的 659 项前端检查及 typecheck/build 已通过。收尾不改前端。先跑免费资格/删除防护、四投影、fast、Router claim/发布/切换与原相关回归，再跑打包/资源和完整检查。只删除用户明确移出的独立通道/静态证明检查；公共防护场景保留，投影测试改接纯事实接口，资格测试改验不可用而不是删除。

所有测试使用 checks 框架的私有 state/runtime/temp 根，清除继承的 BUDDY_STATE_DIR、BUDDY_RUNTIME_ROOT、BUDDY_RUNTIME、BUDDY_RUNTIME_IDENTITY、BUDDY_WORKER_STATE、BUDDY_WORKER_ID、BUDDY_AGENT_CREDENTIAL、BUDDY_AGENT_CREDENTIAL_FILE、BUDDY_ACCOUNT_SELECTION、BUDDY_SUPERVISOR_START_ID、VIRTUAL_ENV、UV_PROJECT_ENVIRONMENT；子环境剔除第三方 Claude 网关变量。完整检查不调用模型，运行期间 worktree 冻结。此前 test_harness_discovery 一次因停止观察窗口返回 shutdown-unverified 而失败，同一原测试 17 项单独复查通过；不放宽断言，最终完整检查可用两并发降低时序竞争，并记录真实结果。

每段 Markdown 一行，记录中的位置用 ~、占位符或仓库相对路径，原始日志与原生封存结果留在忽略的 tmp/adr021-stage2/。阶段记录明确区分完成、移出、真实未通过和未做；停止授权以后没有新原生执行。最终报告给出分支/提交、计划与模块记录、最终完整总数、各微任务时长、设计选择与移出原因。
