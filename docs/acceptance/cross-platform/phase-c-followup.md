# Windows 阶段 C 后续：按句柄换代与读者的删除共享：验收记录

2026-10-22，整合 Host。依据是 [Windows 所有权设计](../../design/cross-platform/windows-ownership.md)第 3 节与第 8 节“阶段 C”，以及[阶段 B 验收记录](phase-b.md)末尾的五件顺带项。宏任务“跨系统适配”。验收通过，合入为 `0dc799a5`；与 0.51.0 一起，没有安装。

## 实现

- **微任务：** “Windows 阶段 C 后续：按句柄换代”（run `aba04da8`，Codex CLI `gpt-6.1-sol / high`，一个回合），基点 `e4411a95`，输出 `8d6dbb5e`。
- **做了什么：**
  - Windows 上的可变发布在临时文件的句柄上写完、fsync，再用 FileRenameInfoEx（POSIX_SEMANTICS | REPLACE_IF_EXISTS）按句柄改名；首次发布与换代走同一条路径。不可变对象照旧 compare-and-create。
  - 产品读发布文件的各处改走 `private_files` 的同一个读者入口，Windows 上允许删除共享。
  - 阶段 B 的五件顺带项：安装发布路径不再走私有边界（`test_publication_paths` 在三个系统上通过）；私有路径只比这一级名字的长名；工作树仓库以上的各级目录按长名、不分大小写比对；`test_transport_attach` 的断言按系统分开；删掉 `ensure_private_dir` 里的死条件。
- **消耗：** 输入 1,552 万（命中缓存 1,535 万），输出 5.9 万。

## Host 的核对

- 差异读过。
- 完整检查（合并候选 `0dc799a5`，含 0.51.0 的问询结清、阶段 B 与历史问询的重新结清）：308 个文件、4,730 项通过，跳过 9 项。
- CI（run 39776731307，`0dc799a5`）：Ubuntu、macOS、前端与浏览器全过。Windows 四片照旧失败，通过的文件由上一轮的 183 个增到 186 个（共 308 个），新通过 `test_windows_workspace`、`test_publication_paths` 与 `test_native_process_journal`，没有新失败。
- 实现方在测试机上自查过读者持有时的换代、错误 32 与 5、强杀窗口和连续发布。实现方自己的 Windows 结果不算验收；这几项的独立复跑放到阶段 B2 之后一起做，B2 会改同一段代码。

## 没有验证的

- 上面那几项 Windows 原生行为还没有独立复跑。
- Windows 上的升级恢复测试卡在 fixture 构造与收尾观察上，实现方没有跑通。
- Defender 实时扫描下的换代未验证。

## 留给下一批的（B2）

- 发布失败时为“恢复可能需要”保留写完的临时文件：改名失败时目标的旧版本完好，也没有代码读这些临时文件，留下只会越积越多。改为一律删掉；设计第 3 节已同步改写。
- PowerShell 启动脚本用 `Get-Content` 读 `.runtime-python`，不允许删除共享，安装恰好在换这个文件时会以错误 32 失败（实现方实测）。
- 为区分私有与公开而加的 `private` 参数，照第 2 节精简后在 Windows 上不再需要，随 B2 删去。
