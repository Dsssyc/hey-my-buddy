# 备份只为升级的回滚：验收记录

2026-10-16，整合 Host。做法见[待办](../design/backlog.md)“安装与存储”一节的同名一条，开发者同日同意。微任务“备份：只为升级的回滚”（Codex CLI，`gpt-6.1-sol / high`，约 15 分钟），基点 `72f1b55b`，产物 `e727f095`，合并为 `5196c72b`。同日随 0.41.0 装上（[安装记录](installs/0.41.0.md)）。

## 做成了什么

- 去掉了手动备份：CLI 的 `backup` 与服务、C-Two 的 `backup` 操作。
- 安装的备份只装回滚需要的东西：停服之后逐字节复制的看板，以及安装或它起的新服务会改动的四个状态文件（`console-settings.json`、`runtime-retention.json`、`active-runtime.json`、`launch-settings.json`）；校验拒绝这之外的任何文件；回滚只还原备份里有的。`console-sessions.json` 不在其中：新服务在安装的围栏下不会写它。
- 删掉了只为证据清单存在的东西：`backup-preflight`、`health.backupPreflight`、控制台快照里的字段与 `/api/backup-preflight`、证据清单与它的校验、`protocol/attempt_evidence.py`、安装的预检与 `BACKUP_PREFLIGHT_FAILED`；控制台存储页的备份预检与提醒一并删掉。
- 存储页把这一行写明是“上次升级之前的看板”。
- 删了面向 Host 的命令与一个 C-Two 方法，版本与契约改成 0.41.0，签名摘要随之更新。

## Host 的核对

- 读完源码的差异：删得干净；复制与还原时对链接与特殊文件的守卫留着，还原之前对所有目标多了一道“必须是普通文件”的检查。
- 这一批改了回滚时的还原，按开发者事先的同意请 Opus 盲审一次（看板上的微任务“备份：删掉的守卫与还原路径的审查”，Claude Code CLI，`claude-opus-5-5 / high`，约 7 分钟，[审查记录](../design/backup-for-rollback-review.md)）。没有阻塞的发现；最重的一条是 skill 与参考文档还在讲已删的预检，由 Host 随合并改掉。代码上的几件小事与两条以后要守的规矩记进[待办](../design/backlog.md)：升级只改看板、改到状态目录里的文件要等新服务核对通过之后（已写进运维参考）；下一次装中间号变了的版本之前先看一眼 `backups/`。
- 在产物上跑：前端全量 81 个文件、1,116 项，浏览器检查 117 项，都通过，构建之后打包产物不变；受影响的 Python 测试 81 个文件里，除了 README 里的版本号（由 Host 随合并改）与一个机器很忙时偶发超时的旧测试（`test_ctwo_integration.py`，单独重跑三次都通过），其余都通过。
- 在合成预览里亲眼看了：存储页不再有备份预检；临时让预览答一份存储清点（看完就撤掉），那一行写着“上次升级之前的看板”。
- 空 HOME 上经启动器演练，只跑命令执行、不调用模型：先装 0.40.0（`72f1b55b`）并留下一次做完的命令执行；再装一个新服务起不来的 0.41.0，安装以 `UPGRADE_ROLLED_BACK`（原因 `UPGRADE_START_FAILED`）换回 0.40.0，这次备份只有看板与两个状态文件，看板还原到安装之前（事件号仍是 6），执行证据一字未动，原来的执行照常可读；然后装真正的 0.41.0，成功，备份只有看板与三个状态文件；手动的 `backup` 与 `backup-preflight` 都回 `UNKNOWN_METHOD`。

## 没有验证的

- Windows 上的发布与恢复只经检查套件的模拟。
