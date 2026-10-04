# ADR-025 第一步 1-B：恢复与固定交付审查

2026-10-04，用户转达 Claude Code Host 的独立影响核对：用户临时目录中的 `buddy-1b-*` 已全部不在，其他会话的 430 个 `buddy-*` 条目仍在，此前登记的 12 类数量与前日一致，没有证据表明涉及其他会话或日常数据。用户明确不恢复、不再追查，并授权在原 run `ad9da628-9b48-48b9-97a4-f2d8ae1c0b46` continue。此前的[暂停记录](adr025-step1-controller-host-hold.md)及[微任务披露与撤回](adr025-step1-controller.md)保留。

本次是原 run 的第二次 continue、第三个实施回合。固定 artifact 为 `9d39ba84-54c0-4dc5-bacd-b58f2c165b14`，输出 commit 为 `98f1b8c4959ac307b4e6a231c062088afe3c893b`，从 `da8c25d` 起的累积 patch SHA-256 为 `e140d75e128b559bb6c9c334b6fd348af05383f06e42ab76f4c1fa73a47ce038`。Host 核对累积 10 条变更路径、patch SHA、工作区相应文件与 seal 的逐字一致；原 worktree 的 HEAD 仍为输入基线，交付由 seal 固定，不把未提交工作区误写成干净的输出 commit。

本次撤销的异常行为改变由原微任务修正：DSH 的环境准备在打开日志后、spawn 的 try/finally 之前执行，失败时两个日志 FD 按基线保持打开；命令组装和 cwd 求值仍在 try 内，失败时关闭 FD。共享启动函数以仅 DSH 使用的 `before_try` 绑定表达这一顺序，其余调用方只机械接收绑定参数。先前启动顺序、Router 外层 `is True`、读取差别、收集入口、DSH 末行及停止/信号取值时机的修正保留。Host 没有代改产物源码。

Host 实读本轮 18 个聚焦模块的日志，313 项测试全部通过，其中 `test_controller` 为 35 项。新增的两个测试直接调用 `DshAdapter.start`，分别检验 selected 失败和 arguments 失败时的 FD 边界；独立复验输出与基线探针的 trace 和 FD 状态一致。加载器比较保留的 682 个旧编号，删除与变化均为 0；本轮相对上一份交付仅新增两个 FD 测试。真实 harness 验证和模型检查均为 0 次；微任务自身实施回合单独计数。本次没有完整检查，整合后的检查按执行计划集中进行，不把此前不含 1-B 的 2,453/163/110 结果记成本次结果。

固定交付按原字节整合为 `4c49bda`，整合前本分支为 `c1f5101`。整合凭据及 accepted 回执保存在 `<repo>/tmp/adr025-host/1b-integration-3-result.json`、`1b-accept-3-result.json`；这是 1-B 的内部微任务验收，第一步整步的 Claude Code Host 验收仍未开始。

随后 Host 在 `f8b42a5` 仅动态收集编号：164 个模块、2,488 个唯一编号，无导入错误，原 2,453 个编号集合全部保留，新增 35 个属于 controller 测试；原始清单与集合比较位于 `<repo>/tmp/adr025-host/after-1b-ids-xc517sv9/`。这不是测试执行。1-C 已从 `f8b42a5` 在独立 worktree 通过普通路由提交，run 为 `511b3514-bb69-43db-bca3-aafc2a540620`，唯一公共角色写入者与其临时根已在任务开始时指定；ACP 整改不写这些公共文件。1-D 尚未开始。

新规则已落实：Worker 报告 Host 创建并登记的 `<system-tmp>/a25b-8sebf31t/`，本轮未手动删除对象。Host 在验收前把本轮脚本、18 份聚焦日志、编号、FD 探针及累积 patch 共 27 个文件/目录登记项保留于 `<repo>/tmp/adr025-host/1b-final-review-ydr8lamt/`，没有复制私有主目录或凭据文件。验收后 Host 仅对上述登记的任务根执行一次按确切路径的整体删除，成功且确认已不存在，未使用通配符、未屏蔽报错；回执为 `<repo>/tmp/adr025-host/1b-task-root-cleanup.json`。原历史材料及其他会话对象未动。
