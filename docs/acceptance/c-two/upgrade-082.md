# C-Two 0.8.2：验收记录

2026-10-23，整合 Host。随 0.52.0 在 2026-10-23 装上（[安装记录](../installs/0.52.0.md)）。

## 为什么升

开发者当天定：0.52.0 等 C-Two 补上两处缺口再装。一是客户端只能逐条连接、同步地等关闭，服务对每条物理连接都要起一个线程等它关闭；二是结构化的错误类型不在公开接口里，本项目只能从内部模块导入。C-Two 0.8.2 当晚发布，两处都补上了。

## 新能力的核实

先在隔离实验里核对发布说明，只用一个私有的端点目录，不碰黑板。macOS 由 Host 做，Windows 11 x64 由微任务 `1f834732` 在测试机上用 Named Pipe 复跑，Host 读过原始输出；两边结果一致。实验脚本与原始输出在 Host 的 `tmp/ctwo-082/`。

- **公开的错误类型：** 连接失败以 `c_two.error.CoreError` 捕获，它是 `CCBaseError` 的子类，带 `category`、`io_kind`、`raw_os_error`、`io_operation`、`transport_kind`、`transport_phase` 等字段。
- **连接事件：** `cc.connection_events()` 同时报客户端发出的与服务端接受的物理连接（字段 `side`）；对端被强杀后几毫秒内收到关闭事件，带关闭原因。连接观察只留 `connection_id` 与 `snapshot()`：开着时为空，关闭后给出关闭的事实；`wait_closed` 删去。
- **落后：** 事件队列容量 1024，消费者落后时得到 `ConnectionEventsLagged`，订阅随之结束；Windows 上 1,500 次连接与关闭、macOS 上的同类实验都与说明一致。
- **线协议不变：** IPC 握手 12、SHM 3、C/FFI ABI 5。

## 本项目改用它

- **微任务：** “改用 C-Two 0.8.2”（run `785cdbcf`，Codex 上的 `gpt-6.1-sol / max`，一个回合），输出 `6bbb1ccc`，与进程盘点改用 psutil 一批一起合入 `f59a653f`。
- **改了什么：** 锁到 0.8.2；整个调度器只订阅一次连接事件，由一个消费者线程观察执行连接的关闭，代替每条物理连接一个等关闭的线程；登记之前就已关闭的连接由快照发现，落后时重新订阅并逐一复查所持连接的快照；`CoreError` 改从公开的 `c_two.error` 导入；`local_rpc_error` 公开。
- **Host 的核对：** 差异读完；可重入锁的嵌套、快照的两种结果在 0.8.2 上核对过。合并树上完整检查 315 个文件中 314 个通过，`cli.test_host_preview` 因临时树没装前端依赖失败，`npm ci` 后在同一棵树上重跑通过。Windows 11 x64 由另派的 Worker 复跑（`abd4134f`），调度器的测试文件候选 44 项、基线 39 项，新加的都通过，两边各有同样的 2 项既有失败，候选失败而基线通过的为 0。

## 没有验证的

- Windows ARM64：PyPI 上有 0.8.2 的 win_arm64 wheel，本项目还没有在 ARM64 上跑过。
- 留给下一批的：`protocol/rpc_config.py` 的文档字符串仍写着 0.8.1。
