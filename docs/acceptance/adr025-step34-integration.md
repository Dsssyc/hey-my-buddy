# ADR-025 第三、四步整合登记

本记录区分微任务固定交付、Host 公共接线、整合验证与 Claude Code Host 的整步验收；某个微任务交付或内部签收不表示第三、四步已经通过外部验收。第三、四步依据 `docs/design/adr025-execution-plan.md` 的第二步后续小节推进。

## 准备

Host 将包含第二步验收的 `socu/buddy-core` 合入实施分支：用户指定的 `886836e` 与随后只说明受管检出被拒回收原因的 `4a8dfee` 均已包含，合入是从 `5859475` 到 `4a8dfee` 的快进。执行计划更新提交 `90900d7`，增加四项准备、三条独立 harness 线、默认并行完整检查、未消费字段期限、限流恢复授权与受管检出不得切换分支的任务规则。

按用户明确授权，Host 在旧准备微任务 `7d967147-ce32-405c-a5a0-62859e84abb1` 的受管检出确认干净、HEAD 为 `c919197` 后执行 `git checkout --detach`；然后用新读取的 revision 完成 `workspace-cleanup-plan` 与 `workspace-cleanup-apply`，结果为 `removed=true`，实际目录消失。随后强制删除已补丁整合的分支 `socu/adr025-step2-preparation`。只处理这一受管检出及指定分支；旧第二步记录中的历史拒绝保留。原始计划、应用和路径核对输出留在本检出 `tmp/adr025-host/step34-20261006-105245/`。

3-P1 首次提交走路由，run 为 `4aab6133-0ba4-4690-b61b-402c9b0939be`，固定基线 `90900d7`；唯一写入范围为三个指定测试模块及本项新记录。路由决定 `dec-77f37d16-ef38-49dc-af0b-bf49f8d2aee9` 选择 `zcode / zai-api / GLM-5.3 / max`。Host 已即时登记短任务根，Worker 不删除；检查和变异证据等待固定交付审查。

3-P2 由 Host 完成一次计划内真实 ZCode Worker 角色冒烟，结果、完成签收、普通工具事实与两层停止均通过，详见 [冒烟记录](adr025-step34-worker-smoke.md)。模型调用为 `zcode / zai-api / GLM-5.3-Flash / max`，1 次；本项探针及记录属于 Host 独立验收工作，没有代改微任务范围内代码。

3-P1 固定交付为 `8444d0a`、artifact `b0774106-f648-4c75-ba1a-f9601de0862b`，累计补丁 SHA-256 `028b2eb9c76dc3337dbeef12af05f02c8a3ef501bd79cdb3ada33d20a5b32cd5`。Host 独立核对封存改动只有获准四个文件，补丁摘要正确，整合后文件与固定提交逐字节相同；受管检出保持 detached HEAD。三处改动直接命中要求的真实入口，原生停止测试没有只改结果或 mock 整体；身份测试只改一个组成部分，实际 turn input 及其摘要保持不变。两个隔离变异的源码分别只有强制 gone、删除 attempt 比较这一项变化，摘要与记录一致，原始失败日志的断言和测试路径也对应固定交付。停止测试里的 interrupt 标志证明经过了尝试终止的分支，不另外声称操作系统已向一个仍存活进程送达信号；本次要补的证据是未获确认时如实返回 unknown。

Host 单独运行 inquiry transport 模块，8 项通过且专用临时目录零条目。首次 Host 探针经 uv 启动，测试通过，但探针把 uv 自建锁文件误计为 fixture 残留；随后用同一个 uv 管理的锁定解释器直接执行，隔离了启动器锁文件，确认模块本身收尾完整。该修正仅在 Host 一次性探针，没有改微任务代码，也没有重跑变异矩阵。

整合后的全量测试编号为 2,631，原有 2,628 个全部保留，新增 3 个，模块仍为 171 个，加载错误为零；逐个变化见 [编号表](adr025-step34-preparation-test-ids.tsv)，原始集合与计数在 `tmp/`。默认并行完整检查 `uv run --frozen python -m hey_my_buddy.cli.checks` 退出码 0：Python 2,631 项（跳过 1 项）／171 个模块全跑，Node 110 项，用时 441.197 秒，没有传 --jobs。准备阶段真实模型冒烟只有 3-P2 的 ZCode 1 次；微任务本身不另启动模型探针。该次完整检查同时作为准备批次整合检查，不再重复。

3-P1 整合提交 `5d31114`，黑板核对的整合编号 `int-8c06d5a2-a946-4c51-a808-76bff5da95af` 为 verified（四条产物路径 matching，differing/missing/unrecorded 均为空），随后 accepted。回收计划请求曾返回 REVISION_CONFLICT，重新读取已存在的有效计划后按该 planId 应用，结果 removed=true，检出实际消失；没有绕过黑板手工删检出。Host 留存聚焦、变异与编号证据后，整体删除创建时登记的 3-P1 任务根，以及本次冒烟的私有目录和唯一短 socket 目录；删除命令没有通配符，没有屏蔽报错。第一次 Host 清理脚本在删除前因台账首条使用 purpose、其余使用 owner 而报 KeyError，未删除任何对象；修正只核对已登记的确切路径集合后执行完成，原始操作输出保留。Host 总任务根仍供后续整合使用。

## 公共接线与并行原生主体

3-A1 Codex run `19e61256-94a4-418d-975d-bf08695b7d04`、3-B1 Claude Code run `c7af488f-bc57-41e2-a7ec-71c62f34b524`、4-B1 DSH run `eebeeba8-d479-4cb3-a4e2-e4c531845e2c` 同以 `5d31114` 为基线，各自隔离检出、首次提交不指定 buddy。任务书逐项限定本 harness 包／测试及新记录，明确公共值、角色、注册表、共享测试与依赖归 Host，Worker 不切换分支且不删除。三项只是各线第一个原生主体微任务，尚不表示整条线通过。

Host 为 DSH 共用既有完成服务，将 `buddy/harnesses/zcode/mcp.py` 用 git mv 搬到 `buddy/roles/session_mcp.py`；只改相对导入、模块说明、ZCode 的启动模块字符串和两份既有测试的导入／命令。执行体 AST 去掉导入与模块说明后完全相等，没有复制 MCP 载体或完成业务规则。没有新增库或另写协议实现，复用已存在的承载；旧路径不留兼容入口。聚焦 `test_zcode_protocol` 与 `test_zcode_inquiry` 共 88 项通过（10.882 秒），含真实 Python stdio MCP 进程；测试编号无变化，没有原生模型调用。此处属于 Host 公共文件整合，随首批原生主体整合后的完整检查一起覆盖，不单独再跑整步检查。

3-B1 首轮固定交付 `106bb6e` 尚未整合。Host 核对累计补丁 SHA 与五个改动文件均在范围内，并用该固定代码上的模拟原生进程重现了两处事实错误：invalid-json 时原生根身份列表为空，却上报本地预分配的 nativeIdentity；init-wrong-session 时 nativeIdentity 与原生根列表相冲突；显式失败结果已经经过 EOF drain，却因业务成功与停止条件混入而把 streamComplete 报为 false。源码比对还确认 Worker-read 的网络域名从原有七项变为空、附加禁用项增多，不能称为等价抽取。这些是原微任务范围内问题，回原 run 修正，Host 不代改该主体。

Host 按已批准的请求字段可调整规则补 `network_allowed_domains` 与 `additional_denied_tools`，并在公共 run 调用点拒绝模块没有声明支持的非默认控制，选择与理由见执行计划补充。它们分别表达原生网络域名和附加禁用工具，不引入角色标签；默认值保留已有路径的行为。字段与共同入口的聚焦检查共 44 项通过（0.051 秒），新增两项测试覆盖 None／空清单区别、JSON 与严格类型、未支持控制不会静默运行。公开 CLI 与 C-Two 契约版本不变。这是 Host 公共接口工作；其 Claude 消费和策略对照由 3-B1 原 run 修正，实际角色接线归后续整合。跨基线验证可在 Worker 的 m/ 下用该 Host 提交与本任务自身文件组成一次性验证副本，不改受管检出的公共文件，不切换分支。
