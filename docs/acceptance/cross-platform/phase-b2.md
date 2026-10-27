# Windows 阶段 B2：私有边界精简：验收记录

2026-10-22，整合 Host。依据是 [Windows 所有权设计](../../design/cross-platform/windows-ownership.md)第 2 节（同日重写）、第 3 节与第 8 节“阶段 B2”。宏任务“跨系统适配”。验收通过，合入为 `42cf38b9`，与同一候选里的实时通道删除批一起快进到 `5b43ab12`；与 0.51.0 一起，没有安装。本记录同时收下[阶段 C 后续](phase-c-followup.md)留到 B2 之后的 Windows 独立复跑。

## 实现

- **微任务：** “Windows 阶段 B2：私有边界精简”（run `ab60182d`，Codex CLI `gpt-6.1-sol / high`，一个回合），基点 `07fd634f`，输出 `4b20ab4a`。
- **做了什么：** 照第 2 节删去私有路径逐次固定祖先、逐次 ACL 校验、私有路径的名称比对、提权进程改默认属主（连同 `PRIVATE_OWNER_SETUP_FAILED`）与 Windows 上的 0o700；状态根只在安装与冷启动时查一次（路径上没有链接，属主是本账户或 Administrators，ACE 都是普通 allow，受托者只有本账户、SYSTEM、Administrators 与 OWNER RIGHTS）；去掉为区分私有与公开而加的 `private` 参数；发布失败时临时文件一律删掉；PowerShell 启动脚本以允许删除共享的方式读运行时提示；Python 下限回到 `>=3.12,<3.13`。句柄层只留给工作树与第 3 节的发布。
- **消耗：** 输入 1,143 万（命中缓存 1,124 万），输出 5.5 万。

## Host 的核对

- 差异读过。
- 删守卫的独立审查（Claude Code CLI 上的 `claude-opus-5-5`，微任务 `b05608f2`，输入 906 万，输出 4.2 万）：没有被删的守卫守着停止证据或数据；第 2、3 节要求的都做到了。它的八条线索（B2-1 至 B2-8）记为下一批的顺带项，见文末。
- 完整检查（候选 `5b43ab12`）：308 个文件、4,662 项通过，跳过 11 项。
- CI（run 39784771387，`5b43ab12`）：Ubuntu、macOS、前端与浏览器全过。macOS 一片第一次失败在 `test_codex` 读会话记录时读到空文件，重跑通过，本地连跑三次通过，记为偶发。Windows 四片照旧失败，通过的文件由 186 个增到 188 个；新失败两份，`codex.test_session_content` 与 `console.test_console_session_content`，它们钉着状态目录里逐次拒绝链接，正是第 2 节删去的行为：代码是对的，测试没跟上，记为顺带项。
- Windows 独立复跑（微任务 `86831881`，经路由由 Codex CLI `gpt-6.1-sol / max` 执行，两个回合，输入 1,057 万，输出 6.9 万），同时覆盖阶段 C 后续。Host 读了它的报告与原始输出（桌面日志、逐对象 ACL 对照、基线对照）：
  - 指定的 11 个测试文件共 169 条：117 通过，31 条由测试自己的系统条件跳过，2 条 FAIL、19 条 ERROR 都在 `test_launcher` 与 `test_runtime`，在 `07fd634f` 上逐条相同（Windows 上没有 `/bin/sh`，虚拟环境路径按 POSIX 断言），不是这两批带来的。
  - 同一个私有状态目录上，先 SSH 提权、再由开发者在远程桌面的普通 PowerShell（`TokenElevation=0`）、再 SSH 提权，各冷启动一次私有服务、读 `health`、停止，退出码都是 0，状态根检查没有拒绝。三轮共 60 次对象观察（含 SQLite 的 WAL 与 SHM）：每个对象只有三条继承来的普通 allow ACE，受托者是 SYSTEM、Administrators 与本账户，三轮逐项相同；提权建出的对象属主是 Administrators，普通令牌新建或换代的对象属主是本账户，交替打开都成功。
  - 状态根检查的两个反例：加了 Everyone 读 ACE 的根、路径上有一级目录联接的根，冷启动都以 `PRIVATE_PATH_UNSAFE` 拒绝，联接目标里没有建出状态。
  - 经产品的 `private_files.publish_bytes`：首次发布成功；允许删除共享的读者持有时换代成功，旧读者读完旧版、新读者读到新版；禁止删除共享的读者持有时得到错误 32，目标保持旧版、临时文件没有留下，读者释放后再发布成功；写者在 fsync 之后、改名之前、改名之后被强杀，新进程依次读到完整的旧版、旧版、新版。
  - 照 PowerShell 启动脚本的读法读 `.runtime-python` 1,993 次，同时经产品的发布换代 1,000 次，没有错误 32。
  - 测试机上的工作目录已删除，另一次 SSH 核对它已不在。第一个回合在 SSH 一侧做完之后以 harness 错误结束，续接的回合完成了其余部分。

## 没有验证的

- Windows 上的升级恢复测试（与阶段 C 后续相同，卡在 fixture 构造与清场观察上）。
- Defender 实时扫描下的换代。
- 状态目录在 Windows 上取 `absolute()`、在 POSIX 上取 `resolve()`，8.3 短名与长名混用的地方没有专门核对（B2-8）；CI 的 runner 临时目录是短名，没有因此出现新失败。

## 留给下一批的

2026-10-23 第八轮删除的根模块一批（微任务 `08b6c00d`，合入 `ed630097`，随 0.52.0 安装）做掉了这里留下的各条，Host 在源码里核对过：

- B2-1：`0o777 if os.name == 'nt' else 0o700` 只剩 `private_dirs` 里的一个入口；
- B2-2：产品代码里没有 `tempfile.TemporaryDirectory` 了；
- B2-3：两个系统的私有打开在共同入口上用同一条规则，不带 `O_EXCL` 的 `O_TRUNC` 一律拒绝；
- B2-4：两个系统的私有打开对 `..` 用同一条规则；
- B2-5：一次安装只查一次状态根；
- B2-6：安装锁与准备运行时用的存储锁都经 `private_dirs.open_lock_fd` 打开；
- B2-7：`workflow.py` 与 `codex/native_run.py` 读可变发布文件改走 `private_files.open_read`；
- `codex.test_session_content` 与 `console.test_console_session_content` 照第 2 节改写。

B2-8（8.3 短名与长名混用）仍是上一节所说的没有核对的一项。
