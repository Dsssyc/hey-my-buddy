# 备份只为升级回滚保留

开发者于 2026-10-16 同意的范围已实现于 0.41.0 源码，C-Two 契约同步为 0.41.0，看板仍为 schema 23，没有新增存储形式升级。本说明记录受管检出中的实现与检查，尚未经 Host 验收，也不代表安装到了日常服务。

## 备份内容与回滚

契约不同的安装在旧服务退出并取得所有者锁后，仍使用离线备份保存看板原始字节，压缩为 `board.sqlite3.gz`，并记录校验清单。契约相同的安装仍不做备份，失败只切回原运行时；契约不同的安装失败及中断日志恢复仍按原路径还原看板，再启动原运行时。

`blackboard/store/backup.py` 的 `STATE_FILES` 保留四个文件，只有备份时实际存在的文件会复制到 `state/`。它们都由安装或服务写入，具体写入点如下。

- `console-settings.json`：`Console.start()` 保存实际控制台端口，控制台设置操作也会持久化登录要求；回滚还原原控制台设置。
- `runtime-retention.json`：安装成功后的 `retain_runtimes()` 写入当前与上一运行时的保留标识，供运行时回收判断；回滚保留原保留记录。
- `active-runtime.json`：`write_active_runtime()` 在安装、回滚和日志恢复中写入活动运行时指针，日常启动器据此选择稳定运行时；回滚恢复已备份的指针，协调器随后按原语义写回实际恢复的运行时。
- `launch-settings.json`：`write_active_runtime()` 同时持久化并发数与等待容量的启动环境；回滚恢复已备份的设置，协调器随后按原语义写回保留的启动参数。

`console-sessions.json` 不再备份：新控制台启动只加载会话，本地会话由 `_new_local_session()` 在内存中创建；浏览器认证、兑换与会话管理虽然会调用 `ConsoleSessions._save()`，但 HTTP 请求在 `upgrade.json` 存在时先被 `_trusted_request()` 拒绝，安装及恢复的健康检查也不走浏览器认证，所以升级回滚前没有该文件的写入。

升级不改写执行证据、`controls/`、`submissions/`、受管检出的输入、清单、封存产物和会话清理日志，因此这些目录不再复制或还原。验证只接受必有的 `board.sqlite3.gz` 与上述四个可选文件；即使范围外文件的大小和哈希正确，也拒绝其清单。`restore()` 只还原经过验证的备份中实际存在的状态文件，不删除备份时缺失、后来才生成的文件，也不遍历或覆盖证据与受管检出。

复制仍通过普通文件打开边界拒绝链接、Windows reparse point 与特殊文件；备份清单和载荷遍历保留同类检查。还原在覆盖看板、状态文件和删除 SQLite sidecar、临时文件之前检查路径组件，普通文件目标还拒绝目录与 FIFO 等特殊文件。备份发布、回滚和中断恢复的锁、日志与单代保留方式沿用原实现。

## 删除的接口与实现

移除了 CLI 的 `backup` 与 `backup-preflight`、对应帮助和映射、`BuddyControl.backup`、服务的备份处理，以及安装客户端 `command()` 的备份映射；启动器的本地命令名单也不再包含 `backup-preflight`。契约签名摘要更新为 `f4ae9329eb636fd604a14051963b575f42d325c0de408108dcd57639932740a5`。

移除了 `health.backupPreflight`、服务和控制台快照中的预检字段、`/api/backup-preflight`、控制台后端的读取函数和专用条件读取帮助函数；备份模块移除了 `preflight_entries()`、`preflight()`、`preflight_command()`、证据扫描、证据策略及跳过路径的清单字段与校验。删除了 `protocol/attempt_evidence.py`，安装不再调用 `backup_readiness()` 或返回 `BACKUP_PREFLIGHT_FAILED`；移除了仅供手动备份使用的在线复制分支与 `offline` 开关，备份统一要求看板已停止且 WAL 已 checkpoint；路径不安全时由实际备份边界拒绝，并沿原安装失败路径恢复原运行时。

删除了 `test_backup_has_no_upgrade_token_parameter`、手动备份与备份预检的旧测试、证据清单专用断言及其合成预览数据；备份测试改为验证回滚清单、校验与路径边界。模拟升级失败及中断恢复时，在备份后改写看板、控制台设置、执行证据、受管检出文件和启动日志，验证前两者恢复、后三者保留后续写入。

## 控制台

移除了存储页的预检状态、重新读取按钮、`BackupAttention`、`use-backup-preflight`、API 读取与类型、相关测试和样式，设置页不再传递仅用于该预检的可见状态。服务和控制台的 `backup` 类别统一显示“上次升级之前的看板”，仍为受保护的存储类别；确认清理的说明也使用这一名称。合成预览移除了预检响应、路由与异常数据。打包资源由最终前端源码重新构建。

## 检查记录

检查仅使用私有测试环境与合成预览，没有安装产品或操作日常服务。Python 使用 Host 指定的解释器，通过 `PYTHONPATH=src <指定解释器> -m hey_my_buddy.cli.checks <路径…>` 运行，默认进程数；没有运行完整 Python 检查。

- `tests/python/blackboard/store/` 与 `tests/python/blackboard/tasks/test_storage.py`：9 个文件、144 项通过，退出码 0，私有测试目录已删除。
- `tests/python/blackboard/service/`、`tests/python/console/`、`tests/python/cli/`、`tests/python/protocol/`，以及 `test_console_macro_task_fixture.py`、`test_private_adapter_invariants.py`、`test_dsh_resume_storage.py`：54 个文件、715 项通过，退出码 0，私有测试目录已删除。
- `tests/python/install/` 与 `tests/python/blackboard/tasks/test_storage_orphans.py`：18 个文件通过；`test_first_install.py` 中只有约定保留的 README 版本断言失败，其余 11 项通过，该轮退出码 1，私有测试目录已删除。
- 最终四文件清单与统一离线复制落定后，针对 `test_backup.py`、`test_backup_windows.py`、`test_upgrade.py`、`test_upgrade_archive.py`、`test_upgrade_service.py` 和 `test_upgrade_fixture.py` 复核：其中 `test_backup_windows.py`、`test_upgrade_archive.py`、`test_upgrade_service.py` 和 `test_upgrade_fixture.py` 的 19 项通过；失败文件是 `test_backup.py` 与 `test_upgrade.py`，原因是测试夹具在关闭数据库后仍使用同一已关闭连接，修正为停服边界与独立 SQLite 连接后，这两个文件最终复测 32 项通过、退出码 0。两轮私有测试目录均已删除。
- Node 24.21.0 下的 `npm --prefix apps/console test`：81 个文件、1116 项通过，退出码 0；一次与其他长任务并行的运行出现 6 项超时，单独复跑全部通过，没有修改那些测试或放宽超时。
- Node 24.21.0 下的 `PLAYWRIGHT_SKIP_BROWSER_GC=1 npm --prefix apps/console run test:browser`：构建通过，117 项 Chromium 浏览器检查通过，退出码 0。新增的两项使用合成存储响应，在 1440 px 与 390 px 宽度下检查新名称、大小与保护列可读且文字不被裁切；运行于合成预览，不接触日常看板。构建保留了大于 500 kB 的既有分包警告。
- `git diff --check` 通过，打包 `index.html` 引用的 JS、CSS 与图标文件均存在。

两份 README、其他文档、`AGENTS.md` 与共享 skill 由 Host 修改，本微任务只新增本说明；`test_readmes_keep_first_install_parity` 会因 README 安装命令仍为 0.40.0 而失败，按任务要求保留给 Host。真实 Windows、日常服务安装与 Host 合并后的完整检查尚未执行。
