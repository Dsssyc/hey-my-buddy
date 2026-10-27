# Windows 残余（五）独立审查

审查对象：`git diff 90617623 06fefad1 -- src tests`（15 个文件，+187/−51）。审查方式：只读代码，对照 AGENTS.md 的 Invariants 与 ADR-031 决定 4、5、14；没有运行任何测试，没有在 Windows 上做实验。下文凡涉及 Windows 内核行为的判断，都是推论，标明了需要的实验。

## 结论

没有找到能把“还没停”判成“已停”、进而在停之前释放或删除东西的新路径：新代码在 Job 上只是比原来多要了一个条件（终止信号），所有失败都落在“未确认”一侧。但这次修复的根据——`TerminateJobObject` 之后 Job 对象被置为有信号，且在成员资源释放之后才置——在仓库里没有任何实验或文档佐证，单次通过的 Windows 测试也证明不了一个时序上的保证（发现 1）。同一个“Job 已空”的事实现在有两种判法：`ProcessHandle` 要信号，ACP 的 `stop_evidence` 仍只看计数（发现 3）。另有两条只会把“已停”判成“未知”的活性退化（发现 2、5）。建议：发现 1 的实验与发现 3 的统一判法在合入前做；其余可作为顺带项。

## 被删掉或改弱的检查

| 位置 | 原来守着的事实 | 现在由谁守 |
| --- | --- | --- |
| `process_identity._stop_windows_job` 的轮询循环（在 `settle_seconds` 内反复看 `active()==0` 且根的启动时刻已不在） | 强停后 Job 已空、根身份已消失，并容忍两者先后到达 | `OwnedJob.terminate` → `wait`：等 Job 信号并读一次计数；随后只读一次根的启动时刻，不再重试。Job 一侧更严，根一侧失去了时序容忍（发现 5） |
| `owned_popen` 失败路径 `stopped = job.active() == 0` | 挂起子进程被强停后 Job 已空 | `job.wait(剩余时间)`：信号且计数为零，更严 |
| `ProcessHandle.group_alive` 的 `job.active() != 0` | 组是否还活着 | `not job.wait(0)`：请求过终止时要信号，否则同前 |
| `ProcessHandle.terminate` 强停后不阻塞、由循环轮询 | 强停与观察共用 `settle_seconds` | 截止时刻提前到强停之前，`job.terminate` 在同一预算里阻塞等待，总上限不变 |
| `test_process_identity` 中 `api.active.side_effect=[1, 0]` | 钉住“先读到非空、再读到空”的轮询 | 改为单次 `wait_job(77, 2.0)` 加一次 `active`。轮询随代码一起删掉，合理；但没有测试钉住“有信号而计数不为零 → unknown”与“有信号而根的启动时刻仍在 → unknown” |
| `test_harness_discovery` 撤掉停止证据时打补丁的对象由 `OwnedJob.active` 改为 `OwnedJob.wait` | 观察失败不算停 | 等价：`group_alive` 现在只经 `wait` |

## 发现

### 1. 修复依据的内核语义没有被证实（中；合入前补实验）

`OwnedJob.wait` 的文档串断言：自然退出不给 Job 置信号；`TerminateJobObject` 置信号，且在成员资源释放之后。仓库里关于 Job 的实验记录（`docs/design/cross-platform/windows-experiments-*.md`、`windows-ownership.md`）只观察过 `ActiveProcesses=0` 与按名打开失败，没有一处观察过 Job 的等待语义。`TerminateJobObject` 的终止与 `TerminateProcess` 一样是发起即返回；信号是在发起终止时、计数归零时，还是在每个成员的句柄表关闭之后才置，从代码里读不出来。

可能的事件顺序：`TerminateJobObject` → 内核在成员还在退出（句柄表尚未关闭、当前目录句柄仍在）时就置了 Job 信号 → `wait_job` 返回真、计数为零 → `terminate` 判已停 → 调用方删除工作目录 → WinError 32。后果落在安全一侧（删除失败，不会删错东西），但这样修复就只是把时序窗口挪小，CI 的通过是概率性的。

复现做法（委派 Worker 在测试机上做）：一个 Job，根进程已退出，一个后代以目录 D 为当前目录在睡；`TerminateJobObject` → `WaitForSingleObject(job, 2000)` → 立即 `rmdir D`，循环 500 次计 WinError 32；对照组把等待换成轮询 `ActiveProcesses==0`。或者把新增的 `test_exited_leader_and_forced_descendant_release_their_working_directory` 在 Windows 上连跑 200 次。两种都为零才算证实；同时记录 Windows 版本。

### 2. “请求过终止”一旦置上，就再也不能由计数证明已停（中；活性，安全一侧）

`termination_requested` 在调用 `api.terminate` 之前就置为真，且之后永不复位；`wait` 从此只认 Job 信号。两种事件顺序会让一个真实已停的组永远显示为活着：

- 竞争：`ProcessHandle.terminate` 里 `_wait_for_group(0)` 看到组还活着 → 最后一个成员自然退出 → `job.terminate` 对一个已空的 Job 调 `TerminateJobObject`。若对空 Job 的终止不置信号（文档串自己说自然退出不置信号；未验证），`wait_job` 等满 `settle_seconds` 返回假，`terminate` 返回 `(False, True)`，此后同一句柄上的 `shutdown_confirmed` 也恒为假。
- `api.terminate` 抛出 `OSError`（例如拒绝访问）：标志已置、信号永远不会来，之后成员即使全部自然退出，`group_alive` 仍为真。

后果：执行报 `shutdownConfirmed=false`，尝试进入未知，直到执行进程退出、Job 名字消失后由孤儿路径按 `identity-absent` 结清。不违反“未知不等于已停”，但把一次真实的停止变成了一轮恢复。

复现做法：单元测试用 `tests/python/buddy/runtime/test_windows_process.py` 的 `FakeAPI`，令 `count=0` 后再调 `handle.terminate(grace_seconds=0, settle_seconds=0.1)`、`job_signalled=False`，断言今天返回 `(False, True)`；第二个用例令 `FakeAPI.terminate` 抛 `OSError`，再置 `count=0`，断言 `group_alive()` 恒为真。Windows 上：对一个成员已全部退出的 Job 调 `TerminateJobObject`，再 `WaitForSingleObject(job, 0)`，看返回 0 还是 `0x102`。建议至少把标志移到 `api.terminate` 成功返回之后；空 Job 的规则等实验结果再定。

### 3. ACP 的停止证据仍只看计数，与 `ProcessHandle` 成了两种判法（中；合入前统一）

`dsh/acp/connection.py:632` 的 `group_observation` 仍是 `job.active() == 0`；`AcpClient.shutdown`（`dsh/acp/client.py:128`）丢掉 `handle.terminate` 返回的 `(confirmed, escalated)`，改由 `stop_evidence` 重新判断，`dsh/native_run.py:1375` 与 `dsh/acp/launch.py:53` 也用它。ADR-031 决定 14 说停的顺序与停止证据只有一种；现在 ProcessHandle 要信号，ACP 不要。

事件顺序：`client.shutdown` → `handle.terminate` 强停 → 在 `settle_seconds` 内没等到 Job 信号，但计数已为零 → `terminate` 返回未确认 → `stop_evidence` 判 `groupObserved="gone"`、`shutdownConfirmed=true` → dsh 向服务报已停 → 服务可回收检出与原生目录。按这次改动自己的理论，这正是“计数先归零、句柄后释放”的窗口；失败的那条 ACP 测试现在能过，只是因为 `terminate` 先阻塞等了信号。反过来，在发现 2 的空 Job 情形里，两者给出相反的结论。

复现做法：用 `FakeAPI`（`job_signalled=False`、终止后 `count=0`）构造 ACP 客户端的 `ProcessHandle`，调用 `client.shutdown(drain_seconds=0, settle_seconds=0.1)`，断言今天 `handle.terminate` 返回假而证据里 `shutdownConfirmed` 为真。修法：`group_observation` 在有 Job 时改用 `handle.group_alive()`（即 `job.wait(0)`），让两处只有一个判断。

### 4. 同一窗口在自然退出与“Job 名字消失”两条路上依旧存在（低；待 Host 定）

- 自然退出：没有请求过终止时，`wait` 仍只看 `active()==0`。按文档串的说法，计数归零可能早于句柄释放，于是“正常结束 → 判已停 → 回收目录”仍可能撞上 WinError 32。
- 转交后的执行根 Job：根进程退出时句柄表关闭，最后一个 Job 句柄随之关闭，kill-on-close 发起对其余成员的终止，名字随即消失；`wait` 与 `terminate` 在按名打开得到 `None` 时直接判已停。终止此时只是发起了。这一点是 AGENTS.md 已写下的不变量（“Job 不在或已空即证明进程已结束”），不是本次引入的。

需要 Host 定的是“已停”的含义：若指不再执行代码，那么本次的等待只关乎资源释放，应放在删除的边界上（回收遇到共享冲突报未完成、下次再试，`storage_apply` 已有 `STORAGE_INCOMPLETE` 这条路）；若指资源也已释放，自然退出与名字消失两条路都需要另一种证据（例如等进程对象本身的信号），而不只是强停这一条。现在只修了强停一条，与它自己的理由不一致。

复现做法：Windows 上根与后代都以 D 为当前目录、在 0.2 秒内各自自然退出；轮询 `ActiveProcesses==0` 后立即 `rmdir D`，循环 500 次；另一组让根持有唯一的 Job 句柄后退出，轮询按名打开失败后立即 `rmdir D`。

### 5. 孤儿强停不再容忍根的退出晚于 Job 信号（低；活性，安全一侧）

`_stop_windows_job` 原来在 `settle_seconds` 内轮询“计数为零且根的启动时刻已不在”；现在 `terminate` 返回真之后只读一次 `started_at`（`include_exited=False`，靠等进程对象的信号判断退出）。若 Job 信号先于根进程对象的信号到达，结果是 `unknown`，原来的循环会等到它。不会误判已停。

复现做法：`tests/python/protocol/test_process_identity.py` 的 `Source` 让 `started_at` 第一次返回记录的启动时刻、第二次返回 `None`，旧代码得 `orphan-stopped`，新代码得 `unknown`；Windows 上对真实孤儿连做 200 次 `stop_orphan_process`，统计 `unknown` 次数。

### 6. Claude 回合内的停止：宽限期从 1 秒变为至多 3 秒，差异里没有测试（低；记录）

`native_run.py:561` 改用 `halt_owned_group` 符合决定 14 的“一种停法”。代价是宽限期从固定 1 秒变为 `min(3, 剩余时间)`：这一处的注释要求在第一个根结果处同步停下，免得排队的输入开出下一次查询；宽限期变长，原生 CLI 开出排队查询的窗口多了至多 2 秒（`followup_init_observed` 会记下它，但那次查询可能已经发给了模型）。返回值前后都被丢弃，回合末尾的 `_halt_owned_group`（第 773 行）仍给出停止证据，这一点没变。差异里没有测试覆盖这一行。

复现做法：用现有的 Claude 替身让 CLI 在收到中断后 1.5 秒才退出，并在其间输出第二个 `system/init`；比较改动前后 `followup_init_observed` 与是否升级为强停。

### 7. 发现模块：哈希与解析读的是两次不同的读取（低；顺带项）

`file_fingerprint(record=True)` 读一次字节求哈希，`_record_text` 再读一次求版本。两次之间文件改写，会把旧内容的哈希与新内容推出的根缓存在一起；A→B→A 的改写之后，缓存会以 A 的指纹命中 B 的结果。读到的长度与 `stat` 不符时返回 `None`，该记录在这一轮被当作不存在，下一轮因指纹变化重扫，可以接受。4096 的上限与 `_record_text` 的上限一致（超过上限的记录本来就不被解析，所以不会漏判），但两处各写一个字面量。建议读一次字节，同时用于哈希与解析，并共用一个常量。`harness_health` 里存的 `locationFingerprint` 是不透明的摘要，安装后每个 harness 会多一次重扫，没有存储形式上的歧义。

复现做法：在 `file_fingerprint` 与 `_record_text` 之间打补丁改写记录文件，断言快照的指纹与解析出的版本来自同一内容。

## 多余的防御与冗余

- `owned_popen` 失败路径：`job.terminate(...)` 已经等过信号并返回结果，返回值被丢掉，随后又 `job.wait(...)` 观察一次。用 `terminate` 的返回值即可（`process.wait` 仍需要，用来取退出码）。
- `OwnedJob.terminate` 的转交分支把 `wait` 里的 `observed` 表达式重写了一遍；两处的 `try/except OSError: return False` 只在 `_stop_windows_job` 里有人读返回值，而那里外层已有 `except OSError: return "unknown"`。同时 `api.terminate` 自己的 `OSError` 不被吞，向上抛出：同一个方法里两种失败走两种出口。
- `OwnedJob.terminate(timeout=2.0)` 的默认值没有调用方使用，三处调用都显式传入；应改为必填。
- `open_job` 给所有按名打开都加了 `SYNCHRONIZE`，包括只做查询的 `verify_job_member` 等路径。同一账户下无害，记一笔。

## 核对过、没有问题的点

- 所有新的等待都有上限：`wait_job` 把秒数换成毫秒并截在 `0xfffffffe`（不会变成 `INFINITE`）；`ProcessHandle.terminate`、`owned_popen`、`_stop_windows_job` 传入的都是由既有预算算出的剩余时间。`WAIT_FAILED` 经 `_error` 抛出，`_error` 总会抛出，不会静默返回 `None`。
- 没有新的句柄泄漏：转交分支的按名打开都在 `_reopened` 里关闭；`_SystemSource.open_job` 在 `finally` 里关闭；`owned_popen` 失败路径照旧关闭创建句柄。
- `CreateJobObject` 返回的句柄带全部访问权，含 `SYNCHRONIZE`，`wait_job` 在创建句柄上可用；按名打开的句柄已补上 `SYNCHRONIZE`。
- 测试改动：`request_private_service_stop` 让清理认出本测试已确认过的停止，修掉了 Windows 上“观察到运行却没有请求过停止”的误报；探针服务改用原子发布端点文件、启动失败时回收子进程；长路径改走 `native_path`；就绪等待 25 秒改为 180 秒属于测试夹具的等待，不在规模上限的规则之内。`cleanup_owned_case` 新增的删除状态根这一步没有像其他夹具那样先调用 `assert_private_processes_gone`，这里没有执行进程，风险很小，但与既有的删除顺序不一致。
