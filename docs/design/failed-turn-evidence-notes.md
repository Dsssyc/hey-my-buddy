# 没有正常完成的执行保留原生证据

2026-10-15，本批只调整检出回收、私有目录删除和既有存储回收的清单，不改变数据库的存储形式，也不改写执行结果里的 `evidenceRefs`。

回收仍先通过原有的验收或结论、停止确认、工作区归属、保留证据和现场检查，删除同一个受管检出后才处理其私有目录；微任务、借用同一检出的协助微任务及其路由微任务的范围沿用既有规则。在各个 harness 的 `harnesses/<harness>/micro-tasks/<sha256(taskId)>/` 内，只留下需要保留的 `attempts/<attemptId>/` 子树及其祖先目录，目录里的文件和原路径保持不变；`native/`、续接状态、正常完成的执行及其他内容仍按原有删除边界清掉。

判断依据是看板中每次执行已有的 `attempts.result_json.terminationReason`，只有明确为 `completed` 才视为正常完成。`wrap-up`、`grace-expired`、`forced-stop`、`console-stop`、`service-stop`、`unknown-stop`、`deadline`、`harness-error`、`transport-error` 和 `interrupted` 都保留；终止原因缺失或结果尚未记录也不能证明正常完成，因而保留已有证据。不按微任务后来是否成功、是否验收或执行结果的内层 `status` 再作另一套判断：本次回归场景的内层 `status` 是 `invalid-result`，看板记录的终止原因是 `harness-error`。有效地写出 `assistance` 或 `attention` 结论的执行，若终止原因是 `completed`，仍按正常完成处理。

`private_dirs.remove_tree` 接收要留下的确切子树，先检查保留路径属于本次删除根，再沿用原有的根目录、祖先链接和 Windows reparse point 检查；遍历时跳过保留子树，只删除其余内容，嵌套链接仍只删除链接本身。保留清单不会增加删除范围，回收被拒绝时也不会进入私有目录删除。

既有存储回收仍逐个列出 `harnesses/<harness>/micro-tasks/` 下的微任务目录。它从同一组看板终止原因识别仍存在的失败执行证据，保留原有 `runId` 归属，路由微任务继续归属它所属的运行，并在候选项增加 `retentionReason: failed-attempt-evidence`、`description: 失败执行的证据` 和 `failedAttemptIds`。这些字段说明目录为何留下；`reasons` 仍专门表示既有的删除阻塞原因，停止确认、验收、三天保留期、待续做及链接检查都不变。用户通过现有 `storage_plan` 和 `storage_apply` 决定删除，仍使用相同的指纹、计划复核与删除日志，不另建清理流程。

验证通过指定解释器的 `PYTHONPATH=src <指定解释器> -m hey_my_buddy.cli.checks <测试文件…>` 执行，清除继承的运行时、Worker、代理凭据和虚拟环境变量，使用默认进程数，不改解释器环境。最终源码的 14 个测试文件共运行 260 项，0 项跳过，全部通过；运行器使用默认的 4 个进程，退出码为 0，私有测试根已移除，`git diff --check` 通过。最初的 `test_private_directories.py` 定向运行通过 7 项；第一轮存储回收通过 24 项，新增生命周期场景发现测试交接调用 `board.close()` 会删除私有数据库，已改为只关闭连接和控制台后交给私有 daemon，最终运行已覆盖该修正。

最终范围包含 `tests/python/blackboard/tasks/` 下全部 13 个文件名含 `workspace`、`cleanup`、`storage` 或 `reclaim` 的测试文件，以及 `tests/python/test_private_directories.py`；未运行完整检查。新增场景通过私有 `BoardService` 写入失败执行、续做并验收，确认失败证据和原引用仍可读、成功执行和原生会话消失、存储中的失败结果未改写，再由真实私有 daemon 生成存储回收计划、执行计划并确认目录不再列出；原生文件和回合记录均为离线夹具，没有启动真实 harness 或调用模型。本记录是源码验证，Host 验收及合并后的安装验证另行进行。
