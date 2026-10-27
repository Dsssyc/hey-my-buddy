# Windows 阶段 F（忙文件、运行时保留与测试清场）：执行计划

2026-10-25。依据 [Windows 所有权设计](windows-ownership.md) 第 6、8 节。初稿由只读微任务 `9272fe6c` 在 D2 与 E 的最终源码 `b3692247` 上写出，Host 审过；初稿明确不加预防性重试、不另写扫描器，没有层层设防的迹象，所以计划不另请独立审查，改到删除路径的差异再请。

## 已经做了、不再排的

设计第 6 节点名的几项源码里已有：`test_harness_health.py` 手建的 store 都登记了关闭；`tests/python/support.py` 的 `InProcessBoard` 与 `BoardTestCase` 显式清场，不递归搜未知对象；测试根删除器只在 Windows 的 READONLY 叶子上清一次属性重试，其余错误抛出，有原生用例；`test_native_process_journal.py` 已用 Python 替身，没有 `/bin/sleep`；检查运行器在 Windows 上已经不调 `ps` 或 `lsof`（`_ps_argv_table` 只是旧名字）。产品的 `Database.close` 在服务关闭时调用。

## 拆批

现在就能在 macOS 上做的，在 `b3692247` 上开工，依次两个微任务：

- **F1a 测试清场与一处安装清理（经路由）。** `tests/python/install/test_backup.py` 两处 SQLite 连接改用 `closing` 管连接、原来的连接上下文管事务；`test_native_process_journal.py` 的身份断言按平台（Windows 三键、POSIX 五键），结束时用 `shutdown_confirmed` 确认整棵树停止并关闭 Job，不靠析构；`test_backup_publication.py` 照 `test_private_files.py` 的做法起真正的写者（基础解释器加锁定环境），结束的是写者本身，字节握手与有界 `communicate`；真跑 POSIX 命令的测试替身（`buddy/runtime/test_run.py` 的 `sleep` 与 `sh`、DSH `fake_agent.py` 的同组后代）换成保持原意的 Python 替身。产品只改 `install/upgrade.py` 的 `retain_runtimes`：临时 `BoardStore` 用 `finally` 关闭，“升级成功、延后清理失败”的语义不变。
- **F1b 检查运行器的可移植性（GPT-6.1 Sol，差异请独立审查，因为改到删除路径）。** `cli/checks.py` 的 `sweep` 不再无条件依赖 `os.getuid`；运行器删除自己的私有根时，用与测试根删除器同一条策略（只清 READONLY 叶子、只重试一次、其余错误保留根），这条策略放在产品代码里，测试支持复用它，不让发行的源码依赖 `tests/`。

等 Windows 测试机开机之后：

- **F2 最少的 Windows 取证，与 D2、E 的复跑一起做。** 真写者（venv 与基础解释器）、运行器所有权（套件后代、私有服务与 command 执行）、SQLite 产品恢复（外部进程持读写与只读连接时，恢复保留现场，释放后从同一份日志续做）、删除续做（持有禁止删除共享的叶子时只记未完成，释放后同一计划只结算一次）、runtime 被旧执行使用时完整保留。Defender 开启条件下的实验要开发者安排开启，单列。
- **F3 以后按 F2 的结果落地。** 运行器在 Windows 上按实际拥有的进程、Job、私有服务与执行停止证据清场，清场未知仍退出 3 并保留根；忙文件的恢复与清理只补诊断与证据，部分删除不记完成；Windows 的 runtime 回收：能映射到执行的保留对应的，映射不了就保留所有可能用到的旧候选，不新增启动事实。

F1b 的独立审查（2026-10-25）留给 Windows 阶段的三条：

- 取证之前不在 Windows 上跑 `--sweep`：它能走到写证据那一步，会用自己的证据覆盖候选根里原有的 `teardown-evidence.json`。
- F2 加一项：Router 的镜像文件被 `buddy/roles/router_input.py` 设成 0o444/0o555，在 Windows 上即 READONLY，正常流程里 `discard` 经 `private_dirs.remove_tree` 删除时可能以错误 5 失败（推断，未在 Windows 复现）。复现了就在产品里修根因。
- F3 让 Windows 清场真正删根时，核对 READONLY 叶子是不是指向根外的硬链接（例如 uv 在 Windows 上的硬链接缓存）：清属性会改到根外的同一个文件。

## F2 的结果（2026-10-25）

取证微任务 `a3abc85e` 在 `f17267f2` 上做，Host 读过报告与关键原始输出，证据在 Host 检出被忽略的 `tmp/storage/win-f2-evidence/`。

- 真写者：16 个中断窗口都起基础解释器加锁定环境，结束的是写者本身。
- SQLite 恢复：外部读写或只读连接持有看板时，恢复停在删除 WAL（WinError 32），看板、旁文件、日志与备份都在；释放后同一份日志续做完成。符合设计。
- 删除续做：改名前持有叶子得 WinError 5，改名后持有得 WinError 32，都只记未完成（`STORAGE_INCOMPLETE`，已删的那份不计）；释放后同一计划只结算一次。符合设计。
- 运行时保留：执行用着旧运行时时，回收因 `process-inspection-unavailable` 把它完整保留（1,381 个文件不变）。Windows 上现在只能这样保守保留，还不能把运行时映射到执行。
- Router 镜像：正常流程里 `discard` 在 READONLY 的镜像文件上以 WinError 5 失败，镜像留下。推断已复现，F3 在产品里修根因。
- 运行器：Windows 上的清场只观察得到 cwd，三次都退出 3 保留根；套件后代与私有服务按身份核对都已结束。
- **新发现：长路径。** 产品发布 `execution-start.json` 时，完整路径约 262 个字符，`windows_files.py` 的 CreateFile 返回原始错误 3，command 执行起不来（launch issue，`processCreated=false`）。0.52.1 就有，不是 D2、E 引入的。默认状态目录在 `%LOCALAPPDATA%\hey-my-buddy` 下，用户名短时最深的这类路径约 245 个字符，离上限不远；检查运行器的私有根更深，所以 Windows 上起 command 执行的用例都会因此失败。不改系统设置（开启长路径是用户的系统设置）。
- 测试的可移植性：`test_execution_process.py` 有三项调用只在 POSIX 上有的 boot_id，一项替身缺 Windows 需要的进程与 Job。

F3 因此加两项：产品自己的文件路径在 Windows 上超过 260 个字符时的做法（先在测试机上实验扩展长度路径能走到哪些操作，再在计划里比较），以及上面这几处测试的可移植性。

## 定下的收窄

- 不加预防性重试：持续的读者、SQLite 连接、已加载的解释器都不会自己释放；只有实验捕获到会自行释放的短暂占用，才在唯一的文件边界讨论一次有界重试。
- READONLY 只在合成的测试根上清，不推广到产品删除器、源仓库或正式 runtime，不把所有错误 5 当成属性或 ACL 问题。
- 不做全系统句柄枚举或第二套扫描器；用已有的身份、Job、锁与快照，`open_files` 为空不当作 Windows 上没有占用的证明。
- 只在 POSIX 上有意义的用例（FIFO、SIGSTOP）标明平台，Windows 用各自业务目标的对应验证，不只跳过。
- 错误诊断走已有的消息与日志；新的结构化持久字段先由 Host 判断是否改了事实形式。

## 待定

- Defender 实验：测试机上开启实时保护是系统设置，由开发者安排。
