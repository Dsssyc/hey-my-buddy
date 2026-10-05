# ADR-025 第一步 1-B：暂停与未验收记录

2026-10-04，1-B 的原微任务 run `ad9da628-9b48-48b9-97a4-f2d8ae1c0b46` 在同一个 run 的第一次 continue 后交付 artifact `9388e3e4-2ec9-497b-b2d4-af2cdcac9887`，输出 commit `7529a2983056aaee506272cc2f4543c8be7dfc54`，累积 patch SHA-256 `bbd24ae9a45a805c4e7c2a34813981b9e21657c8df52bb713171f66b19b36fec`。Host 已拒绝，微任务处于等待 Host 输入状态；没有取消或另开 run，没有整合此交付，也没有开始 1-C。本次提交只登记暂停事实。

## 清理越界披露

第二份交付记录在“撤回与更正”中承认第一回合曾在 macOS 用户临时目录按 `buddy-1b-single/bisect/repro/repeat/diag/trace-` 前缀通配删除一组诊断根，精确路径、数量和逐对象结果未知。另有 5 个 `<system-tmp>/buddy-1b-add-IXEv`、`buddy-1b-add-M9l2`、`buddy-1b-add-MLzB`、`buddy-1b-add-dW4u`、`buddy-1b-add-eHAt` 的名称来自删除前的前缀过滤列表，创建时没有逐项登记全名。这些是本次 1-B 的新披露，不是第零步已经由用户关闭的事件。Host 不据前缀、日期或无进程引用推断归属；执行者关于“都是本会话对象、未触碰其他会话”的断言尚无独立证明，影响范围目前未知。

本披露违背用户关于“只删除自己创建且创建时登记确切路径”“自己目录内也不用通配符”的规则。第一步在此暂停，等待用户转达 Claude Code Host 的影响核对及是否恢复原 run 的决定；Host 没有扩大扫描范围，没有读取凭据内容，没有删除、恢复或追查系统临时目录中的对象。执行者第一回合已经覆盖的原失败日志没有被重建成原始证据。已有材料和披露继续保留。

## 固定代码复核

10 条累积变更路径及 patch SHA 与固定输出逐字相符。上轮反馈中的启动参数顺序、Router 外层 `is True` 与共享读取入口已经落实。尚有 DSH 异常边界未等价：基线在 `open_logs` 后、spawn 的 `try/finally` 之前准备环境，新版把该准备移入 `try`；对 `selected()` 抛错的进程内探针，两版 trace 都是 `logs → selected`，但基线的 2 个日志 FD 仍开，新版均已关闭。这是本轮未获授权的异常行为修复，违反“只重构”及上轮明确保留原 FD 异常边界的要求，恢复后须由原微任务 continue 处理；Host 不代改。

探针材料在 `<repo>/tmp/adr025-host/1b-dsh-fd-order-cyhkerjl/`，只用 mock、无原生 harness 或模型启动。所有目录、日志文件和 FD 在本次探针创建时记下；Host 只关闭自己探针开启且仍在持有的 2 个 FD，未删除文件。固定 patch 与这 5 份探针材料已按确切路径及 SHA 保留于 `<evidence-worktree>/tmp/adr025-evidence-retained/step1b-second-hold-u7v7xv__/`。交付记录另把已通过的 `2453/163/110` 错标为 Python/Node/控制台，正确口径是 Python 测试/Python 模块/Node 测试，恢复时改记录即可，不重跑测试。

## 当前分支与验收边界

`1-A` 已在 `da8c25d` 通过内部微任务验收并整合；独立 ACP 客户端线已在 `ec12d7e` 提交整合记录并停等外部验收，见 [Host 整合记录](adr025-dsh-acp-client-host.md)。此前仅对这批已整合源码执行一次完整检查，`57f6a63` 的 2,453 项 Python（跳过 1 项）/163 个模块/110 项 Node 全部通过，随后仅文档改变，没有重跑；该检查不含未验收的 1-B。第一步整步尚未完成，1-C、1-D 及依赖第一步的 harness 抽取未开始。本次没有修改 1-B 产物、ADR、SKILL、Host 指南或用户文档。
