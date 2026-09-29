# ADR-018 第二部分：schema 15 实施前确认

状态：用户于 2026-09-29 明确同意，作为源码和私有测试板的实施依据。2026-09-29 从 `socu/buddy-core` 的 `cf4d70a4faa3c9884b4cccb21c1ceb105f46f579` 创建 `socu/host-workflow`；基线包含第一部分合并提交 `f97dea8`。日常安装与数据迁移仍需另行授权。

依据为 [ADR-018](../decisions/018-routing-modes-and-host-workflow.md) 第 11–23 条与同时修复的四个缺陷。用户要求“需要 schema 变更时先停下向用户说明并取得同意”。本次在持久化预检阶段触发此确认点；完整实现与验收继续以 ADR 的“试用观察的去向”表为准。

## 已核对的持久化边界

实施预检时，`src/buddy/db.py` 的基线 schema 为 14。`workflow_runs` 保存委派目标及当前执行配置；`tasks.accepted_at`、`acceptance_note`、`acceptance_verdict` 表示现行验收。`workflow.acknowledge` 只接受 delivered/accepted，检出清理同时要求 accepted、最终成果与整合记录。失败或取消目标需要一条独立的 Host 结论，且该结论必须绑定被审查的执行代次，防止续做后旧结论继续授权清理。

`attempts.result_json` 已保存各 adapter 的原始结构化结果。第 22 条新增跨 harness 的原生用量投影，需明确所属 attempt、原生来源与未知字段；该投影不能随当前选中 attempt 的改变而覆盖过去的执行。`harness_health.record_json` 当前由健康刷新整体替换，第 23 条的额度观察需要自己的持久化位置与观察时间，不能随一次路径发现或握手被清空。

当前 `configuration_constraints` 将原始提交中的配置字段全部当作硬约束。第 18 条要求把 Host 的选择与用户要求的锁定分开保存；后续改配仍受 Host 控制令牌、owner generation、revision、已启用配置和停止证据约束。

成果清单、整合核验和回执已有结构化存储。部分成果的性质、累计补丁的固定引用与 `hostPaths` 分别扩展其所属 manifest / verification JSON；这些本身不要求新增关系表。

## 拟议 schema 14 → 15

| 位置 | 变更 | 语义与旧数据初值 |
| --- | --- | --- |
| `workflow_runs` | `configuration_locked INTEGER NOT NULL DEFAULT 0 CHECK (configuration_locked IN (0,1))` | 新提交的 `configurationLocked` 默认 false；true 保持原始显式配置约束。旧记录置 false，允许拥有控制权的 Host 在显式 continue 中附理由改配；原始 goal、请求指纹、历史配置与历史回执保持原样，迁移不发起执行。 |
| `workflow_host_conclusions` | 新增追加式记录表 | 保存 conclusion ID、run ID、所评审 attempt（未开始执行时可空）、run revision、owner generation、failed/cancelled 执行结果、note、evidence、可选 artifact/integration 引用、actor、command ID、时间。旧目标不补造结论。 |
| `attempts` | `token_usage_json TEXT`，允许 NULL | 保存有来源的输入、缓存输入、输出用量以及范围与完整性；历史执行为 NULL，不从缺失记录猜测或写入零。与执行回执在同一事务中持久化，重复回执不能重复累计。 |
| `harness_health` | `quota_json TEXT`，允许 NULL | 保存最近原生额度观察的来源、时间、适用范围、窗口使用率及重置时间；旧数据为 NULL。健康刷新保留额度观察，额度更新不替代健康状态；过期或范围不明时明确显示未知或过期。 |
| `meta` | `schema_version = 15` | 与上述结构变更在同一迁移事务中提交。 |

拟同步源码版本和 C-Two 契约版本至 `0.21.0`；协议运输版本维持现状。增加 CLI/工作流/控制台字段时同步类型、校验、brief 投影和文档。此确认只授权源码和私有测试板的 schema 实现；日常板的迁移仍随后续单独授权的安装执行。

## 与工作流的连接

第 15 条沿用 `integration-record` 和 `acknowledge`：等待 Host 的目标必须有当前轮封存成果、整合证据、本人及所有后代的确认停止证据，且无未处理的工作区冲突，才能由 Host 直接选定并验收成果。保存 Host 收尾事件，关闭对应等待边界；不会制造一次 Worker completed 回合。

第 16 条拟为 `acknowledge` 增加 `verdict: "recorded"`，用于 failed/cancelled 目标的结论。执行状态维持 failed/cancelled，结论从独立记录返回；它不进入成功验收样本。清理计划绑定当前结论、当前执行代次、固定成果、检出身份和停止证据；continue、新的成果或相关所有权变化使旧计划失效。没有执行过的取消目标可记录结论，清理仍需证明检出内容已保留；未封存的改动不能仅凭结论删除。

第 17 条在确认停止、核实模型确实开始工作并验证写入范围之后，封存 `partial-output` 成果，标记 partial、unverified、non-final，保留实际额度、超时或进程故障原因。缺少停止证据时保留待核实状态；超出范围时走现有工作区冲突流程。失败与部分成果发布需要可重放的同一回执／事务关系。

第 18 条更换配置写 `configuration_overridden`；在同一受管检出继续。原生恢复还必须通过该 harness 的会话、attempt、工作区和配置绑定核验；无法证明可恢复时重建，并以证据形式携带最后助手消息和部分成果引用。重建材料显式标明未验收且不构成新的 Host 指令。

第 19、20 条分别把 Host 补充路径与成果路径单独核验、记录，以及从目标最初固定 inputCommit 生成累计补丁。每轮增量补丁继续保留；累计补丁、hash、base/end commit 与对应成果一起固定并在检出清理后可读。

第 22 条用 DSH 与 Codex 原生记录夹具核实输入计数口径：DSH 的输入与缓存读取分列，Codex 的输入包含缓存输入，归一化时不能重复相加。恢复同一原生会话时按本次 attempt 的范围取值，不能把会话历史用量再次计入；只有累计记录且无法证明区间时显示未知。第 23 条只采用原生额度事实，提醒不自动换模型、改偏好或发起重试。

## 迁移和验证

迁移由显式 upgrade 或离线 board preparation 路径执行；普通启动只接受当前 schema。沿用空闲检查、独占锁、已验证的单份滚动备份、单事务迁移和失败恢复。迁移允许变更的表限定为 `meta`、`workflow_runs`、`workflow_host_conclusions`、`attempts`、`harness_health`；其余表逐表校验指纹，扩展表同时核对全部旧列的内容未变。

迁移测试覆盖全新 schema 15 与 14 → 15 形状一致、旧目标解锁后的显式授权边界、原始请求与回执不变、未知用量与额度、外键和完整性、事务中断回滚、备份恢复、启动拒绝直接迁移。本页保留批准时的设计边界；实际测试结果与未验证项由本次验收记录维护。

实现后的逐项验证包括：第 11 条约 4 KB 的 skill 与参考链接；第 12 条三种输入方式等价；第 13 条帮助与实际校验同源及拼写候选；第 14 条 objectiveOf 的归属、Host 权限与幂等；第 15、16 条直接收尾、结论和清理的权限／停止／旧计划拒绝；第 17、18 条模拟 harness 额度失败、部分成果、同检出换配置续做的跨进程集成；第 19 条 hostPaths 核验；第 20 条多轮累计补丁可独立应用；第 21 条服务环境白名单与 Worker 身份保留；第 22、23 条 DSH/Codex 原生记录夹具和 CLI/控制台投影；四个独立缺陷各自回归。测试均使用私有 state/runtime 根，不进行付费复跑。

收尾在冻结候选上运行 `uv run --frozen python -m buddy.checks`，把实际结果和未验证项逐行对应 ADR 的观察表写入 `docs/acceptance/`。候选 wheel 用 `uv build --wheel` 构建，同时验证 sdist → wheel 的 sourceCommit 保留。委派走 Buddy 并由显式轻量监控子代理等待，不派 Claude Worker。合并、安装和用户配置修改均保留给用户后续授权。
