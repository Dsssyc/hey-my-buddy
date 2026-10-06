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
