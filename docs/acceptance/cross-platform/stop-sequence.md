# 子进程只用一种停法（2026-10-26）

微任务“子进程只用一种停法”（run `28eb8ed7`，Codex CLI 的 `gpt-6.1-sol / max`），基点 `51194753`，两轮（第一轮回合 1 与第二轮回合 3 各因 Codex 的 `serverOverloaded` 中断一次，原样续上），第一轮产物 `bd42ce50`，最终产物 `477c7d20`，合入整合分支 `e777a4e1`。依据 [ADR-031](../../decisions/031-one-process-per-execution.md) 第 14 条（开发者 2026-10-26 定）与[调研记录](../../research/2026-10-26-windowless-child-soft-stop.md)。

## 改了什么

每个子进程只有一条请停的通道，停的顺序都一样（`protocol/process_handle.py` 的 `terminate`）：讲协议的 harness 由调用方先发协议中断，这里关它的输入；run controller 由父进程关掉它的标准输入，它的读线程读到 EOF 就停；没有通道的子进程在 POSIX 上以 SIGTERM 为通道。然后等宽限期，宽限期只等被请停的那个进程；它退出后组里还有成员，POSIX 立即补 SIGTERM 再等一秒，最后强停（POSIX 反复 SIGKILL，Windows 终止 Job），观察到整组或 Job 已空才算确认。Windows 不再发送不到的 CTRL_BREAK，没有通道的子进程直接终止 Job。各处自己的第二套停法删除：DSH 版本探针 finally 里的再停一次、ACP 关闭输入的退路与客户端改写句柄、Windows 上执行根创建失败时自写的 Job 终止。共同的 `halt_owned_group` 去掉了不用的 `process` 参数。

## 核对

- 读完两轮差异。第一轮之后持续集成第 14 轮在三个系统上都挂了 `protocol/test_text_encoding.py`（它给 run controller 的 `signal` 打补丁，第一轮删了这个导入）：Host 本机核对时用了实现方给的测试清单，没有自己找全使用方，这是 Host 的疏漏；随第二轮修好。
- Opus 子代理独立审查第一轮（73 次模型调用，输入约 1,532 万 tokens，其中缓存读取约 1,500 万，输出约 11 万，约 25 分钟），Host 核对后打回三条：Claude 的中断把连接的截止时间永久改成两秒后并换掉取消事件，成功但确认慢的回合会被报成超时（阻断）；宽限期等的是整组而不是被请停的根，一个不理会输入的工具让 DSH 的停止从 0.06 秒变成 15.03 秒；几处残留的第二套停法。第二轮都改对，ADR-031 第 14 条补明宽限期只等被请停的进程。
- 组合候选 `9246b6f3`（整合分支加本批与“宏任务筛选行不随计数越界”）上完整检查 330 个文件 5,081 项通过（跳过 29 项）。
- 故障注入 4 处，都被测试抓到：把读不到的进程组当成已停（`buddy/harnesses/test_controller.py`）；宽限期改回等整组（`protocol/test_process_handle.py` 两项）；Claude 中断不恢复截止时间（`claude/test_native_run.py` 三项）；DSH 版本探针再加一次停（`test_harness_discovery.py`）。
- 持续集成第 15 轮（`9246b6f3`）：Ubuntu、macOS、前端与浏览器全部通过。Windows 上剩下的失败文件都在“Windows 残余（四）”的范围里（私有服务冷启动、Lead 启动器、执行恢复与冻结、调度器替身、版本读取、DSH 共享审查与假代理日志）。

## 边界

`buddy/harnesses/dsh/acp/test_acp_session.py` 的 `test_leader_exits_while_its_group_survives` 在 Windows 上时有时无地失败：停止已经确认、整组观察为已空之后，测试清理临时目录时 `native-root` 报 WinError 32（被另一个进程占用）；第 14 轮在 x64、第 15 轮在 ARM。是测试自己的句柄、运行环境，还是 Job 报空之后进程的句柄还没放完，尚未查明，交给下一批 Windows 修缮在测试机上复现。Windows 上的停法以持续集成为准，测试机上的复跑由实现方做，不算验收。
