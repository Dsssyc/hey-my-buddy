# Windows 阶段 F3：执行计划

2026-10-25。依据 [F 计划](windows-f-plan.md)的“F2 的结果”、[Windows 所有权设计](windows-ownership.md)第 6 节与 [D2、E、F1 的验收](../../acceptance/cross-platform/windows-d2-e-f1.md)。初稿由只读微任务 `2b1e13bb` 写出，并在测试机上做了一次长路径实验；Host 与一次 Opus 独立审查各自审过、互不看对方的结论，Opus 的发现经 Host 在源码里核对后才采用。实验与审查的原文留在 Host 检出被忽略的 `tmp/storage/f3-plan-evidence/` 与审查记录里。

## 实验得出的边界

测试机 Windows 11 x64，未开系统的长路径设置（`LongPathsEnabled=0`），CPython 3.12.13：

- 文件操作（pywin32 的 CreateFile，`os.open`、`os.replace`、`os.unlink`、`Path.mkdir`/`stat`、`shutil.rmtree`）用普通路径在约 260 个字符处失败（CreateFile 原始错误 3，`mkdir` 从 259 起错误 206），改用扩展长度路径（`\\?\` 前缀）全部成功；产品的 `private_files.publish_bytes` 收到扩展路径时整个原子发布都成功。
- 进程的工作目录不论带不带前缀，从 259 个字符起创建进程前就报错误 267；Git 在 280 个字符的检出里不论哪种拼法都进不去。所以前缀不能用在 cwd 与 Git 根上，这两者只能保持短。

## 要修的产品问题

- **默认安装就会撞上长路径。** 工具证据写在 `<状态目录>\harnesses\<harness>\micro-tasks\<64 位摘要>\attempts\<执行编号>\tool-evidence\<64 位摘要>.jsonl`，用户名 5 个字符时整条路径 272 个字符（Host 按 `protocol/tool_evidence.py` 与 `private_dirs.attempt_root` 的拼法算过），任何用户都超过上限；执行的启动记录、回执也在同一层下面。这是发版的阻塞项，缩短布局替代不了文件边界的修复。
- **测试支持在 Windows 上把“观察不完整”一律当成收尾失败。** `tests/python/support.py` 的 `assert_private_processes_gone` 只要 `observed_processes` 报了问题就失败，而 Windows 上它总是报“只观察得到 cwd”，所以凡是起过服务的测试在 Windows 上都失败。不先修它，后面的复跑看不出哪些已经修好。
- **Router 镜像的只读文件挡住删除，并且与已定的设计冲突。** 所有权设计第 3 节已定“产品也不给自己的文件设 READONLY”；`buddy/roles/router_input.py` 却把镜像设成 0o444/0o555，Windows 上即 READONLY。保留下来的镜像（核对不过、结果未知、停止未确认、进程崩溃）会让存储回收、宏任务删除与检出清理永远卡在错误 5。

## 拆批

七个实现微任务，最后一次另派 Worker 的独立复跑。T 与 A 都是发版阻塞项，互不改同一个文件，可以并行；其余在它们之后。

- **T：Windows 上的收尾判定（先做；删除差异请独立审查）。** 检查运行器与测试支持只认自己创建并持有的东西：运行器持有的子进程句柄、精确创建时间与它自建的 Job，在持有的句柄上读到成员数为 0 才算停；“Job 不存在”对没设 kill-on-close 的测试 Job 不是停止证据。私有服务用已有的生命周期锁与服务的停止回复确认，不新增服务身份的登记，也不新增持久记录。看不全时照旧保留根、退出 3；不向扫描得到的陌生 PID 发信号。开工前先在测试机上量一次：经过 venv 转发器的孙进程会不会脱离运行器的 Job。测试根里只读叶子的删除改为按句柄、忽略只读属性删除（`FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE`），不再清属性，免得改到根外硬链接的同一个文件（例如 uv 缓存）；先在测试机上核实这条路径。文件：`cli/checks.py`、`tests/python/support.py`，复用 `protocol/owned_process.py`、`protocol/process_identity.py`。
- **A：文件边界用扩展长度路径（与 T 并行；删除差异请独立审查）。** 在 `windows_files.py`、`private_dirs.py`、`private_files.py` 的本机调用处，把已核对过的绝对路径转成扩展长度表示；只在调用操作系统时转换，记下的路径、比较、摘要与对外 JSON 照旧用普通写法。删除树时“要保留的子树”与遍历必须用同一种写法比较，否则会删掉本该保留的。覆盖工具证据、执行启动记录、回执、Router 镜像（现在全用普通 pathlib）与存储回收对深层目录的遍历；`Path.resolve()` 在长路径上的行为一并核实。安装与运行时的路径本来就短，并且绝对路径存在 READY 文件里，不在本批。Router 镜像在 Windows 上不再设 READONLY，POSIX 照旧 chmod；若镜像的核对用到文件模式，按平台给出同一个业务结论，删除路径一行不改。
- **B：Git 的长文件。** 产品自己的 Git 命令统一带单次 `-c core.longpaths=true`（只改本次调用，不碰用户与全局配置），检出根照旧保持短；同时把工作区操作失败时 Git 的 stderr 带进诊断。文件：工作区的 Git 命令构造处与 Router 读 blob 的 helper。
- **C：样板字节、默认路径、仓库卫生与前端的 Python 入口（测试为主）。** 一条通用的 `.gitattributes` 规则固定被逐字节摘要的文本的换行；HOME/USERPROFILE/LOCALAPPDATA、Scripts/bin 等按平台构造预期；前端预览测试改用 `playwright.config.ts` 已有的 `uv run --no-project python` 约定，检查运行器给它当前解释器，不依次试 `python3`/`python`/`py`。
- **D：harness 的测试替身在 Windows 上（测试）。** DSH 协议替身的私有 home 判断，靠 shebang 或 `/bin/sh` 的替身改成显式的 Python argv，匿名管道测试改用读线程或 `communicate` 的有界握手。
- **E：进程、信号、端点与权限的测试（测试）。** 只在 POSIX 上有的调用（`os.killpg`、boot_id、FIFO、SIGSTOP、uid、0700）标明平台，Windows 用各自业务目标的对应验证，不只跳过、不降低断言。权限与属主的测试只用当前账户真实的与合成的描述符，不建别的账户，不做冒充、低完整性或其他攻击性质的实验。
- **F：残余（T、A 之后的一次 Windows 复跑定范围）。** 包括 zh-CN 系统上子进程按 cp936 输出、读的一方按 UTF-8 解码失败的可能（推断，先量），Router 镜像遇到仓库里的 Git 符号链接（推断，先量），以及复跑后仍失败、未归因的用例。
- **复跑：** 各批合入之后，另派 Worker 在测试机上在最终源码上跑完整检查（含前端），并在 macOS 上跑一次；Windows 上的结果只认这次复跑，实现方自己跑的只作开发证据。

## 定下的收窄

- 不改系统设置，不截短身份摘要，不改持久的目录布局；默认布局若仍让 cwd 或 Git 根超出上限，再单列持久布局批（改存储形式，扩展待升级的 28 → 29）。
- 不做显式指定可执行文件的扩展路径（产品与测试的解释器路径都远低于上限，没有使用者），不做路径预算的边界实验（Win32 文档已给出上限），不为网络盘路径写规则（不在首发范围）。
- 不加预防性重试，不做全机句柄枚举，不向陌生 PID 发信号；`open_files` 为空不当作没有占用的证明。
- 取证与修好之前不在 Windows 上跑 `--sweep`：它会用自己的证据覆盖保留根里原有的 `teardown-evidence.json`。

## 待开发者定

- D 与 E 只改测试。它们必须在首次发版之前做完（Windows 上的完整检查全过、持续集成的 Windows 任务能变绿），还是可以放到发版之后、在 README 里照实写明 Windows 测试套件的状态。
