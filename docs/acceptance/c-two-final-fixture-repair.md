# C-Two 五份最终夹具修复：微任务 2-F

本记录绑定基线 `bd89119a0c5b961016f1d6d269267fcd869c8ac2`，实际 HEAD 相同，`git merge-base --is-ancestor 1f7e8b60577cc0b8883aa5dca9e9a1bc392f6ad0 HEAD` 退出 0。Host 的完整检查 `0648162d`（213 模块、6 失败、665.333 秒）和安装版 ZCode 六项复跑属于已有证据，未由本任务重跑或改写。首次路由、供应方不可重试限流及同 run 继续采用 Host 给定记录，本任务没有创建 goal、派发 Buddy 任务、提交、操作 stash/refs 或切换分支。

交付状态为 assistance：五份修复留在工作区；本地完整备份文件及另外四文件的非原生子集共 92 项通过，两份单点变异命中目标断言。真实 C-Two 0.7.4 注册被沙箱阻断，余下 17 项和四份原生目标变异未验收，不称为五文件全通过。

## 唯一修改路径与行为

| 路径 | 修复与保留的边界 |
| --- | --- |
| `tests/python/buddy/runtime/test_repair_recovery.py` | `private_environment` 仅返回明确 overrides；`ExecutionContext` 与 supervisor 的完整子环境由已有 `BoardTestCase.child_environment` 构造。daemon/CLI 接收 overrides，不再覆盖私有 HOME。保留原 PYTHONPATH、显式额外变量、Worker 身份、nonce、命令、恢复重连、owner fencing、停止与重复执行防护。新增同一测试中的 HOME/PYTHONPATH 前置断言。 |
| `tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py` | 问询 Host peer 给现有 `handle_live_binding` 传 fixture 的 `BUDDY_STATE_DIR`，观察前核对实际 `channel._state_dir == (self.root / "state").resolve()`。真实签收、原角色判定和两层停止断言保留。 |
| `tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py` | 原 `activity_channel` 复用 shared fixture 所用的 `handle_live_binding` 接缝与根断言，显式传本 fixture 状态根。保留原 8 秒就绪循环、8 秒活动观察窗口及 2 秒发布节流；没有另建 collector，也没有修改公共 `test_zcode.py`。公共 `live_channel` 的就绪循环是 20 秒，因此这里保留已有活动 helper 的 8 秒循环并复用同一绑定接缝。 |
| `tests/python/install/test_backup_preflight.py` | 先构造 `_child_environment`，再取业务 `snapshot()`。HOME 保留；全部快照、只读、无 service/runtime 断言保留。 |
| `tests/python/protocol/test_transport_attach.py` | 0750 进入真实 SDK/受控 ping RPC；0770/0777 在 SDK 首次 I/O 被拒绝、peer 无收到的 RPC、模式不变。复用 `protocol.test_rpc_config.ProbeServer`、其原生 server 脚本和 `shutdown_private_rpc`，只扩展 probe 的 ping/接收记录，替换合约类型和路由名以驱动生产 `_request`；未 mock `cc.connect`。生产错误映射仍要求 `SERVICE_UNAVAILABLE`，并核对原始 SDK 原因指向组/其他人可写权限，排除任意连接失败。注册等待之前登记同一 Popen 的清理，成功 peer 以原 server 的 `cc.shutdown()["completed"]` 断言和 wait 退出 0 确认停止。 |
| `docs/acceptance/c-two-final-fixture-repair.md` | 本次事实、验证边界、编号与 Host 补核入口。 |

没有修改产品源码、support、角色、注册表、schema、ADR、README、指南或 shared skill；原无链接、`..`、owner 和只读防护仍保留。新增 native helper 的脚本已作语法解析；实际 native 通过和权限错误具体文本仍需 Host 运行核对，不能以解析或依赖版本代替原生验收。

## 私有根、依赖和保留材料

开始时建立 `/tmp/c2ff-0B4h55`，其 canonical 路径为 `/private/tmp/c2ff-0B4h55`；两者指向同一个任务根。全部测试的 `TMPDIR`、`BUDDY_CHECKS_TMPDIR` 都指向该根。`env/` 是任务私有 uv 环境，`cache/` 是私有 uv cache；每个解释器另建 `<label>-<index>/{home,state,runtime,fallback}`。`baseline/`、`fixed/`、六份 `mutant-*/`、助手、材料与日志都在该根。未手工删除任何文件或目录；测试框架正常清理其自建夹具，整个任务根留给 Host 验收后按确切根回收。

依赖仅从授权公开材料 `/tmp/c073-h-x6lhuldm/delegate-materials` 读取，先逐项核对 `provenance.json` 中全部 18 项哈希，再执行 `uv venv --python /opt/homebrew/bin/python3.13 /tmp/c2ff-0B4h55/env` 和 `uv pip install --python /tmp/c2ff-0B4h55/env/bin/python --no-index --find-links /tmp/c073-h-x6lhuldm/delegate-materials/wheelhouse --require-hashes -r /tmp/c073-h-x6lhuldm/delegate-materials/locked-dependencies.txt`，两命令退出 0。解释器为 CPython 3.13.11；安装十个锁定依赖，其中 C-Two 固定为 0.7.4，wheel SHA-256 为 `bb625650f186c6066992cac0948c0f44b95a22af46778c8e63bba30d80ee88b8`。材料随后复制到任务根 `materials/`；`dependency-hashes.json`、`dependency-{0,1}.log` 保留核对和安装结果，没有安装、升级或替换日常 runtime。

测试启动时清除继承的 `BUDDY_*`、`ANTHROPIC_*`、`C2_*`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`，使用显式私有 HOME/state/runtime；最终助手只继承 PATH、locale、时区等基础进程变量，再填私有路径、离线 model-facts 与未安装 harness sentinel。实际 fixture 子环境仍复用 support helper，没有在五份夹具里另造环境过滤。未读取真实凭据、发现真实账号、调用真实模型、接触日常 state/default IPC 或重启日常服务/Worker。

## 原始失败与原生阻断

下表日志均在任务根；完整 argv、实际 PID、wait、退出码和墙钟耗时保存在对应 `<label>-results.json`。所有五文件都独立加载；实际执行也按文件各用独立解释器。ZCode 原生场景在首次真实注册阻断后没有重复受限运行，其原始全文件失败引用 Host 已保留的 `final-repair-failure-inventory.json` 与 `docs/acceptance/c-two-repair-host.md` 末节；本地没有冒充重新复现该原生失败。

| 原始运行 label / 文件选择器 | 项数 | 退出；测试秒 / 命令秒 | 原始事实 |
| --- | --- | --- | --- |
| `original-0` / `install.test_backup_preflight` | 12 | 1；1.966 / 5.464 | 1 failure，`test_cli_preflight_writes_nothing_and_starts_no_service_or_runtime` 的全快照不相等；HOME 在基线快照之后建立。 |
| `original-1` / `buddy.runtime.test_repair_recovery` | 10 | 1；18.443 / 19.265 | 1 error，daemon 启动前 `ValueError: Test HOME must be inside its private state directory`。 |
| `original-role-0` / `buddy.harnesses.dsh.test_dsh_role_wiring` | 12 | 1；33.912 / 34.426 | 8 failures、2 errors（包含 subTest），没有完整 run result，Host peer 不就绪；本日志没有底层 stderr，不将这些失败自行归因为根绑定或 socket。 |
| `original-attach-0` / `protocol.test_transport_attach` | 27 | 1；1.039 / 1.569 | 1 failure，旧 0750 测试的 `connect.assert_not_called()` 失败，mock 实际已被调用一次。该 mock 结果不算原生权限验证。 |
| `original-zcode-bounded-0` / `buddy.harnesses.zcode.test_zcode_inquiry` 非原生子集 | 41 / 47 | 0；1.454 / 1.847 | 仅非原生子集通过；未运行剩余六项，不称为原始文件完整通过。 |

原始命令为 `python3 /tmp/c2ff-0B4h55/run.py original <checkout> install.test_backup_preflight buddy.runtime.test_repair_recovery`、`run.py original-role <checkout> buddy.harnesses.dsh.test_dsh_role_wiring`、`run.py original-attach <checkout> protocol.test_transport_attach`；三个 wrapper 都为每个文件派生 `/private/tmp/c2ff-0B4h55/env/bin/python -m unittest -v <module>`。ZCode 子集命令为 `run.py original-zcode-bounded /tmp/c2ff-0B4h55/baseline bounded:buddy.harnesses.zcode.test_zcode_inquiry`，底层执行私有解释器的 `bounded.py <module>`。

仅作一次真实原生核对：`python3 /tmp/c2ff-0B4h55/native_probe.py` 调用已有 `ProbeServer`，底层私有解释器退出 1，0.834 秒；`native-probe/probe-server.log`、`native-probe.log`、`native-probe.json` 保留真实 `cc.register` 失败，原因为 `c_two._native.CoreError: server error: config error: server failed to start: IO error: Operation not permitted (os error 1)`。它没有注册就绪，不是 SDK 0770/0777 拒绝的目标证据。确认这个阻断后没有再尝试 socket 注册、最终原生场景或对应原生变异。

## 最终聚焦验证

命令入口是 `python3 /tmp/c2ff-0B4h55/run.py <label> <checkout> <selector...>`。完整文件使用私有解释器 `-m unittest -v <module>`；`bounded:<module>` 使用同一私有解释器运行 `bounded.py <module>`，其确定排除列表只存在于任务根助手，未 skip、删减或改变仓库测试。表中覆盖的是实际执行数量，未执行项明确列出。

| label / 文件 | 项数 | 退出；测试秒 / 命令秒 | 覆盖与未执行 |
| --- | --- | --- | --- |
| `green-backup-0` / `install.test_backup_preflight` | 12 | 0；1.325 / 1.948 | 完整文件通过，所有快照/只读/无服务断言均实际运行。 |
| `green-bounded-0` / `buddy.runtime.test_repair_recovery` | 9 / 10 | 0；18.641 / 19.233 | 原命令子进程、恢复、owner fencing、终止和 receipt 防护通过；`RealDaemonRestart` 一项待原生补核。 |
| `green-bounded-1` / `buddy.harnesses.dsh.test_dsh_role_wiring` | 4 / 12 | 0；0.411 / 0.750 | description/discovery 四项通过；`WorkerRegisteredRunTests` 八项待原生补核。 |
| `green-bounded-2` / `buddy.harnesses.zcode.test_zcode_inquiry` | 41 / 47 | 0；1.452 / 1.797 | 队列、journal barrier、工具与窗口等非原生项通过；两项实时活动、三项 inquiry integration、一项 native catalog 待原生补核。 |
| `green-bounded-3` / `protocol.test_transport_attach` | 26 / 28 | 0；0.767 / 1.011 | 原只读、路径、owner、冷启动、根传递等非原生项通过；0750 和可写 IPC 两项待原生补核。 |
| `green-attach-final-0` / `protocol.test_transport_attach` | 26 / 28 | 0；0.797 / 1.142 | 给新 native helper 在等待 ready 前登记进程 ownership 后，再对最终文件核对同一非原生子集。 |
| `green-home-precondition-0` / `helper:home_precondition` | 1 个前置片段 | 0；0.003 / 0.555 | 从现有真实恢复测试 AST 只执行 daemon 之前的 HOME/PYTHONPATH 原断言；没有启动 daemon，不替代完整恢复测试通过。 |

最终入口分别为 `run.py green-backup <checkout> install.test_backup_preflight`、`run.py green-bounded <checkout> bounded:buddy.runtime.test_repair_recovery bounded:buddy.harnesses.dsh.test_dsh_role_wiring bounded:buddy.harnesses.zcode.test_zcode_inquiry bounded:protocol.test_transport_attach`、`run.py green-attach-final <checkout> bounded:protocol.test_transport_attach`、`run.py green-home-precondition <checkout> helper:home_precondition`。没有运行完整检查、frontend 或安装版 ZCode 文件。

## 编号与语义迁移

实际 unittest loader 与 AST 清单交叉核对：基线五文件共 108 个编号，最终 109 个；删除差集为空，无重复、无加载错误。各文件数量为 recovery 10→10、DSH 12→12、ZCode inquiry 47→47、backup 12→12、transport attach 27→28。唯一新增为 `protocol.test_transport_attach.RpcReadOnlySetupTests.test_group_readable_ipc_reaches_native_rpc_without_chmod`；没有改名或退出的旧编号。

原 `RpcReadOnlySetupTests.test_unsafe_ipc_is_not_chmodded_or_connected` 保留编号，名称中的 unsafe 现在明确为组/其他人可写（0770/0777），connected 表示 SDK 拒绝实际 I/O 且受控 peer 未收到 RPC。旧“0750 unsafe、项目在 Python 包装 connect 被调用前自行拒绝”断言与用户已接受规则冲突，改为“真实库拒绝可写 IPC，peer 未收到，不 chmod”；此语义变更单独登记，不把 Python connect 是否调用作为原生连接证据。0750 允许性由新增真实受控 RPC 编号接替；其余原防护保留。

loader 命令为 `run.py loader-original /tmp/c2ff-0B4h55/baseline helper:load_ids`（退出 0，0.984 秒）和 `run.py loader-final <checkout> helper:load_ids`（退出 0，0.506 秒），运行测试/模型次数均为 0。完整编号在 `loader-{original,final}-0/ids.json`，差集在 `test-id-delta-{loader,ast}.json`。

## 单点变异与有效证据

全部变异仅在任务根的固定比较副本中；五份交付测试不带变异。`mutants-plan.json` 保存每处唯一文本替换、目标文件、选择器与应命中的既有断言；`local-target-proofs.json` 只登记已经实际命中的两份。没有用导入、任意 HTTP 或 sandbox 错误作为红灯。

| 单点 | 实际证据或补核边界 |
| --- | --- |
| HOME：在明确 overrides 字典插回 `**os.environ` | `run.py red-home-final /tmp/c2ff-0B4h55/mutant-home buddy.runtime.test_repair_recovery.RealDaemonRestart.test_a_real_supervisor_keeps_its_child_and_reattaches_after_a_restart`：1 项、退出 1、failure=1、errors=0，0.003 / 0.581 秒。命中既有测试新增的 `daemon overrides must leave HOME to the private child environment`，在任何 daemon/socket 之前失败。对应正常前置片段 1 项通过，不声称完整真实恢复 green。 |
| 快照：仅把 `_child_environment` 移回快照之后 | `run.py red-snapshot /tmp/c2ff-0B4h55/mutant-snapshot-order install.test_backup_preflight.BackupPreflightTests.test_cli_preflight_writes_nothing_and_starts_no_service_or_runtime`：1 项、退出 1、failure=1、errors=0，0.758 / 1.345 秒。CLI 已退出 0，原 `self.assertEqual(snapshot(), before)` 失败。对应最终完整备份文件 12 项通过。 |
| DSH：仅去掉 `handle_live_binding` 的 `state_dir` | 固定 `mutant-dsh-state/` 已准备；保留 `the Host inquiry channel must use the fixture's private state root` 断言。未在阻断环境重跑，等待 Host 原生问询路径。 |
| ZCode：仅去掉 `activity_channel` 的 `state_dir` | 固定 `mutant-zcode-state/` 已准备；保留 `the role channel must use the fixture's private state root` 断言。未重跑，等待 Host 活动路径。 |
| SDK 旧期待：仅把 unsafe 输入元组改回 `(0o750,)` | 固定 `mutant-sdk-old-expectation/`；要求实际 SDK 接受后原 `assertRaises(ServiceError)` 失败，目标为 `ServiceError not raised`。这是权限边界输入的负控，不能写成删除了 vendor 权限实现。尚未运行。 |
| SDK 允许边界：仅把允许 fixture 的 0750 改为 0770 | 固定 `mutant-sdk-allowed-boundary/`；保留 `0750 must reach the actual SDK peer` 断言，要求真实已就绪 server 下 SDK 拒绝导致该断言失败。尚未运行；它同样是输入边界负控，不是 vendor 实现变异。 |

首次 HOME 变异 `red-home-0` 也保留（1 项、退出 1、failure=1、errors=0，0.003 / 0.670 秒）。随后把 HOME 断言改为同义布尔形式以避免失败时打印整个环境，最终证明使用 `red-home-final-0`；没有删掉首次日志。两份最终红灯原始输出与 argv 都在各自目录和 results JSON。

## Host 补核与进程收尾

固定助手为 `/tmp/c2ff-0B4h55/host_verify.py`，入口 `python3 /tmp/c2ff-0B4h55/host_verify.py <固定交付源码根>`；默认固定源码根为任务根 `fixed/`。助手先核对 `fixed-fixtures-sha256.json` 中五份测试逐字节哈希，再为五文件各开私有解释器，执行完整聚焦 green（预期 10、12、47、12、28 项，共 109 项），随后逐个执行六份单点副本。每份红灯必须退出 1、恰好 failure=1、errors=0、命中指定具体断言，权限阻断和 Too many open files 均拒绝作为目标证明。它没有删除、安装、改 refs、运行全套检查或模型的逻辑；未由 Worker 执行这个原生补核入口，也未声称 Host/reviewer 已运行。

期待 Host 产物为 `host-final-green-results.json` 及五份 `host-final-green-*/raw.log`、六份 `host-red-*-results.json` / 原始日志、`host-target-proofs.json`；实际 SDK 错误文本或范围内夹具仍有问题时，用原 run continue 返回具体原始证据，不能跳过或弱化断言。本任务还缺原生通过与四份原生负控实证，Host 应确认这两份 SDK 输入边界负控是否满足其“取消目标行为”的最终验收解释；它们不能替代 vendor 权限实现被删除的证明。本任务没有自行修改产品或 vendor 来制造这类变异。

`direct-processes.json` 记录 17 个直接持有的测试/loader 解释器，全部 wait 已返回，无未 wait 的直接测试解释器。native probe 的已有 helper 通过 `poll()` 确认其 register 子进程提前退出；DSH 初跑报告里的 `shutdownConfirmed=false` 仍是原始未确认两层停止事实，未因父解释器结束而改写为 stopped。没有仍由本助手持有的运行进程，没有发现/操作其他会话的进程；初跑未输出的子层 PID 不编造为已确认停止清单。任务根、uv 环境/cache、材料、原始日志、源码/变异副本都未手工清理，Host 验收后按 `/tmp/c2ff-0B4h55` 整体回收。

`git diff --check` 退出 0；五份测试和所有任务根助手已语法解析。验收边界仍为上述 92 项、两个目标红灯、加载与路径核对，原生部分待 Host 补核。
