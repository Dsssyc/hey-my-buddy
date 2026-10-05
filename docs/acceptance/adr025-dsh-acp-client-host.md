# ADR-025 DSH 独立 ACP 客户端：Host 整合记录

2026-10-04，本记录覆盖计划 4-A 中提前实施的独立 Python ACP stdio 客户端。本线在第零步验收后的 `4cf58de` 启动，经原 run 的四次 continue 后完成；公共接线、完成与问询服务、工具范围、工具归类、可选会话记录、token 用量核对及 Node 删除仍属第四步后续微任务。客户端已整合，停下等 Claude Code Host 验收；第一步的独立工作继续。

## 固定交付与整合

| 项目 | 身份或结果 |
| --- | --- |
| 微任务 run | `ee2ee1e3-5c5d-4032-8a6f-fcfd20e50b6e` |
| 最终 artifact | `8f08345b-e7d6-4803-92fb-6d789d907097` |
| 最终输出 commit | `f03b94fae114a5c5036829243d1aa06c03a6a683` |
| 累积 patch SHA-256 | `7d0bfe8d30760121a3e946e79c78c41db5da2a109f140f10be1a0a0fdd13b4cd` |
| 源码整合 | `57f6a63`，四回合修正后的 `4cc0d56` 原字节 |
| 文档修正整合 | `3d05ce2`，最终 seal 相对 `4cc0d56` 只有交付记录变化 |
| 整合凭据 | `int-81b83505-27bf-4ef1-b775-5eaaf6ec8a16` |

Host 核对全部 12 条累积变更路径、patch SHA 与最终 blob；各轮源码、测试和记录的范围内缺陷均拒绝并用同一 run 的 continue 打回。Host 没有代改这些产物；最终文档修正没有触发测试重跑。[微任务交付记录](adr025-dsh-acp-client.md) 保留历轮缺陷、夹具挂死、清理登记与撤回。四次拒绝依次处理启动与环境绑定、EOF/帧关联、内存与期限、内容留存、权限标题及测试清理；随后处理响应完成与写入队列的竞态；最后只纠正文档的日志数量与锁内/锁外结算描述。

Host 对写入状态的进程内探针确认：被取消的未开始帧不出线，claim 与期限取消互斥，已完成作业不会逆转成不确定，真实未完成写入的不确定事实在结算后解除。探针无原生进程或模型启动。源码中的唯一写入线程、16 槽队列与已有 `ProcessHandle` 所有权保持；没有新增角色、转报通道或公共操作。资格没有被强化为厂商实现证明。

## 验证与编号

微任务最终三个测试模块共 69 项；Host 实读六份最终聚焦日志，全部是 `Ran 69 tests` 与 `OK`。测试重复的原因和两处夹具故障在交付记录中保留。随后 Host 对已整合的 1-A 与 ACP 客户端这一批执行一次 `uv run --frozen python -m hey_my_buddy.cli.checks`：退出码 0，2,453 项 Python 测试（跳过 1 项）、163 个 Python 模块、110 项 DSH Node 测试全部通过，耗时约 415 秒。被检查源码 commit 为 `57f6a63`；之后 `3d05ce2` 只改 Markdown，源码与测试字节不变，按用户要求不重跑。控制台源码、公共回执与打包配置未变，控制台的独立 659 项检查仍是第零步已验收的证据；本线没有把它记为新运行。

动态收集的原始 unittest 编号及比较结果放在 `<repo>/tmp/adr025-host/step1-baseline-ids-6jjv5tze/`。以 ACP 整合前的 `da8c25d` 为对照，原 2,384 个编号集合相等、重复数为 0，新增 69 个、改名及删除均为 0；[新增编号表](adr025-dsh-acp-client-test-map.tsv) 入库，仅列变化。与第零步 `4cf58de` 对照的另 62 个新增编号属于 1-A，由第一步汇总；原 2,322 个编号仍未变。本线未搬迁或删除旧 DSH 防护，旧 Node 的 110 项仍执行；后续第四步再提供其逐项迁移表。

完整检查的原始输出及私有环境账目在 `<repo>/tmp/adr025-host/parallel-batch1-2wiidn0u/`；首次 Host 摘要解析器只寻找串行 unittest/TAP 行，对并行输出记成了 0，此摘要原样保留，随后从同一原始日志的正式汇总重算为 2,453/163/110，没有重跑检查。Host 的各轮固定交付探针、累积 patch 和最终聚焦日志副本在 `<evidence-worktree>/tmp/adr025-evidence-retained/acp-client-first-7l1eyhmf/`、`acp-client-second-78hz5cna/`、`acp-client-third-1cnv6en0/`、`acp-client-fourth-ombj3jbv/`；复制时记录确切路径与 SHA，未复制私有会话或凭据。

## 原生运行与清理

本线原生 DSH 验证累计 1 次免模型握手、0 次 prompt；四次 continue 均未再启动真实 DSH。initialize、新建空会话、公开配置回读、关闭与组停止已有该次真实证据，list/resume、prompt/cancel、权限应答对真实 DSH 仍未验证。实施 buddy 是 1 个 run、5 个 turn，路由选择与这些原生检查分开登记；Host 没有指定 buddy。完整检查另含已安装 ZCode 配本机回环模型服务的现有测试，网络沙箱只允许 loopback，付费模型调用为 0；这不是 DSH 原生冒烟，也不能补足其尚未验证的操作。

Host 的一次性材料都在本检出 ignored `tmp/` 的本次专用目录，全部保留；完整检查器在创建时报告并登记 `<system-tmp>/buddy-checks-v_1mcsrg/` 及其 `tmp/`，全部子测试的系统临时材料位于该根。检查器完成停止核对后只删除它新建并持有的确切根，Host 验证该根已不存在；没有通配符清理、其他会话清理或日常运行时安装/升级。测试自身创建与删除的专用根按 ACP `support.py` 的 manifest/deletions 账目登记；历史未完整登记及撤回继续保留，未回溯猜测或恢复旧对象。

## 暂停边界

本线已完成并提交，等待用户转达 Claude Code Host 验收。第一步仍在进行；ACP 的公共接线必须同时等待第一步外部验收，本记录不宣称第四步或 ADR-025 完成。新增 DSH 行为差异、核对前提失败或明显超出执行计划时仍按用户决定暂停说明。
