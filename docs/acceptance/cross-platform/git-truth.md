# 工作树以 Git 为唯一事实来源（2026-10-26）

微任务“工作树以 Git 为唯一事实来源”（run `cded5d29`，Codex CLI 的 `gpt-6.1-sol / max`），基点 `92a75e2f`，三轮，第一轮产物 `370a5bda`，第二轮产物 `2ef94e1e`，最终产物 `d13c3976`，合入整合分支 `aef27138`。依据开发者 2026-10-26 的决定（[ADR-002](../../decisions/002-host-directed-assistance-and-workspaces.md) 2026-10-26 一段），解决清单里的 W05 与换行（`core.autocrlf`）这一类问题。

## 改了什么

产品不再按磁盘上的原始字节与文件类型读写工作树，一律交给 Git：建检出用 `git reset --hard`，观察在私有的索引副本里用 `git add` 算出条目（钩子、文件系统监视器与未跟踪缓存都关掉），恢复用 `git restore`，回收补回缺失文件用 `checkout-index`；写完再观察一遍，与输入不一致就报出路径拒绝。原先的原始物化、逐级持有目录与 `O_NOFOLLOW`、Windows 的逐级句柄层（整个 `blackboard/tasks/windows_paths.py`）与对应测试删除。CI 里关掉换行转换的那一步删除。

## 核对

- 读完两轮差异。Opus 子代理独立审查第一轮（41 次模型调用，输入约 795 万 tokens，其中缓存读取约 767 万，输出约 11 万，约 21 分钟），Host 核对后打回：阻断两条，`assume-unchanged` 或 `core.ignoreStat` 会让 Worker 的改动在观察里看不见、静默丢失；新检出里经过链接的工作目录会被 `mkdir` 跟进链接，可能写进用户的仓库。应修四条：恢复重写整个真实索引、观察给排除的未跟踪文件写对象、私有索引退回系统临时目录、证据摘要变了含义。另加：随数据增长的 Git 调用不设固定的 60 秒超时。Host 给出的原则：观察一律不用 Git 跳过文件的捷径；绑到树上的内容用 Git 的条目，证据摘要仍取原始字节的 SHA-256。第二轮都改对：每次调用 `core.ignoreStat=false`，私有索引副本先清 `assume-unchanged`；工作目录逐级 `lstat`，遇链接、重解析点或非目录拒绝，再核对检出身份；恢复只更新改到的索引条目；只给准入的路径写对象，排除文件取原始字节摘要；状态目录必传。
- 候选 `1587994d`（整合分支加“子进程只用一种停法”与“宏任务筛选行不随计数越界”，再加本批）上：受影响的 6 个工作区测试文件 107 项通过；完整检查 333 个文件 5,072 项通过（跳过 29 项）。
- 故障注入 5 处，都被测试抓到：不清 `assume-unchanged`、不覆盖仓库的 `core.ignoreStat`（`test_workspace_review.py`）；工作目录改回 `mkdir(parents=True)`；给全部未跟踪文件写对象；检出后多出的未跟踪文件不算不一致（`test_workspace_git_truth.py` 的 smudge 过滤器用例）。
- 像用户那样跑一次（`tmp/storage/gt-probe.sh`）：空 HOME、不设状态目录、经候选与基线（`9246b6f3`，不含本批）各自构建的启动器与真实的私有服务，路由提交在新看板上停在 Host 边界，不调模型。三个源仓库：`core.autocrlf=true` 加 `* text=auto`、`core.symlinks=false` 的已提交链接、已提交的 `sub` 是指向外面的链接而工作区里是真目录。基线的新检出写 LF、写真链接（不理会仓库设置），`existing` 加提交在两个干净仓库上都被 `WORKSPACE_BASE_MISMATCH` 拒绝，经链接的工作目录报 `WORKSPACE_IO_ERROR`。候选的新检出写 CRLF、写占位文件（与 Git 一致），`existing` 加提交被接受，经链接的工作目录报 `WORKSPACE_UNSUPPORTED` 并指出路径，外面的目录没有被写；取消、结论、回收之后源仓库干净，工作树只剩主检出，用户自己设的索引标记不变。
- 持续集成第 16 轮（`c4989d8a`，第二轮产物合进整合分支）：Windows x64 与 ARM64 上 `test_workspace.py` 的 `test_dirty_input_preserves_staging_binary_modes_symlinks_and_source` 失败，交第三轮。
- 第三轮只改测试里的三处链接断言：不再拿 `os.readlink` 读出的路径拼写去比，改为比较 Git 观察到的条目（mode 与对象）与冻结输入或封存树里的条目；`core.symlinks=false` 下占位文件的内容与索引里的 `120000` 仍然断言，脏输入那一项显式打开 `core.symlinks`，让 Windows 默认的占位设置下也走真链接的检出。读完差异；这几处断言用的是产品自己的观察函数，它读工作区的内容由上面的故障注入守着。
- 持续集成第 18 轮（`222ed84a`，整合分支加“Windows 残余（四）”与本批）：Ubuntu、macOS、前端与浏览器全部通过；Windows x64 与 ARM64 上工作区的测试全部通过，第 16 轮失败的那一项也过了。Windows 上剩下的失败都不在本批，交“Windows 残余（五）”：清场时 `test_path_state.py` 与 `test_public_state.py` 的私有服务停止回复未确认（第 15 轮起就有）；ARM64 上 `test_harness_discovery.py` 的记录指纹只看路径、大小与修改时间，同样大小的改写落在同一时间刻度里没被认出；x64 上 `test_rpc_config.py` 的探针服务端非原子地写端点文件，测试读到了写了一半的内容。

## 边界

[2026-10-07 的验收](../console-reads-and-checkouts/console-reads-and-checkouts.md)里 R06 拿检出内容与逐个 `hash-object --no-filters` 对照；本批之后封存的检出内容按仓库的过滤器与换行规则由 Git 算出，那一项对照不再适用，Git 进程数不随文件数增长（8 到 800 个文件）仍由 `test_workspace_batch.py` 守着。排除文件与失败现场的证据仍是原始字节的 SHA-256。已有封存不改写：日常黑板里 4 个仓库 1,096 次封存、6,606 个改动过的内容按 Git 的规则重算全部相同，所以没有升级。`core.symlinks=false` 下 Worker 新建的真链接在部分删除后丢失时，回收补不回原样，按 `WORKSPACE_UNSUPPORTED` 保留检出，不放宽回收证明。Windows 上的保留名与没有建链接权限两种拒绝只有实现方在测试机上的取证，以持续集成为准。
