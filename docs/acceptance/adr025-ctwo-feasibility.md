# ADR-025 F-C1:C-Two 0.6.0 临时端点生命周期无模型本机核对

Status:待 Host 验收。2026-10-03 由 ADR-025 宏任务的 F-C1 微任务在独立 worktree 完成的一次有界无模型本机核对,基线 commit `006b2fb9bf9266f3f8e85fd7975d999bbef285b2`。本记录只报告实测事实与由此得出的生命周期约束;它不是第五步(C-Two 实时通道)的实施,也不是任何生产能力的验收。用户作为 C-Two 作者确认的"一个进程可以 `cc.register` 自己为服务并 `cc.connect` 别的服务"按执行计划作为输入事实,未重复证明。

## 核对问题(来自执行计划第七节)

1. 每次运行一个临时端点:真实跨 Python 子进程的注册、连接、调用、关闭、再次创建。
2. 同一地址/命名空间的重名注册的实际语义,以及注册失败后换一个随机人名重试是否可用。
3. 异常退出后的真实连接、残留资源与重建语义。
4. `set_server`/`set_client` 设置是否进程全局、设置时机,以及对后续端点/客户端的影响。

## 环境与材料(实测身份)

| 项 | 值 |
| --- | --- |
| 基线 | worktree HEAD = `006b2fb9bf9266f3f8e85fd7975d999bbef285b2`(与输入一致,工作区干净) |
| 依赖 | `c-two==0.6.0`(项目 `uv.lock` 锁定值;wheel `c_two-0.6.0-cp312-macosx_11_0_arm64`),仅装入私有 venv `<worktree>/tmp/adr025-ctwo/.venv`(`uv venv --python 3.12` + `uv pip install`),未动日常 runtime |
| 解释器 | CPython 3.12.10(该私有 venv) |
| 平台 | macOS arm64(Darwin 25.6.0);Windows 未验证,Linux 未在本机验证 |
| 隔离 | 每个被测子进程用最小环境启动:`PATH`、`HOME`/`TMPDIR` 指到本次 run 的私有目录、`C2_RELAY_ANCHOR_ADDRESS=""`、`C2_ENV_FILE=""`(与 `buddy.daemon` 对服务进程的做法一致);未触碰运行中的黑板、用户设置与凭据 |
| 无模型 | 全部用合成 JSON 字符串;未启动 native harness,未调用模型,未读取任何凭据文件内容 |

材料与位置对应关系:可复用探针脚本在 `<worktree>/tmp/adr025-ctwo/`(`probe_common.py` 共用契约/服务端/客户端,`driver_lib.py` 持句柄驱动,`run_smoke.py` 加 `run_e1.py`–`run_e4.py` 四个实验驱动);原始输出先写在短系统临时私有根 `<probe-root>`(本机为 `/tmp/adr025-fc1/<run>/raw/`,run 名 `smoke/e1/e2/e3/e4`),已原样拷贝到 `<worktree>/tmp/adr025-ctwo/raw/<run>/`。调用方式:`tmp/adr025-ctwo/.venv/bin/python tmp/adr025-ctwo/run_eN.py <probe-root>/<run>`,五个入口(冒烟与 E1–E4)退出码全部为 0。驱动对每个子进程持有 `Popen` 句柄、有界等待、超时对进程组 SIGKILL 并记录;每次退出都记为退出码或负数信号值,失联从不当作 stopped。探针自身的一处缺陷(recall 模式在连接关闭后调用)在记录前已修正并重跑 E3;入库的原始输出为修正后的运行。

## 测得语义:寻址与命名

`cc.register(Contract, impl, name=..., concurrency=...)` 的返回值是资源名字符串本身;真实地址要用 `cc.server_address()` 回读,形如 `ipc://<server_id>`(`server_id` 为 40 位十六进制、`cc` 前缀),实际端点是固定机器目录 `/tmp/c_two_ipc/<server_id>.sock`。该路径与 `TMPDIR`/`HOME` 无关(实测重定向后不变),总长约 87 字符,与 checkout 深度无关;目录为同机共享,文件名只含 `server_id`。契约用 `@cc.crm(namespace, version)` 声明;`connect` 在连接时核对 namespace/类/版本,不匹配立即报 `ContractMismatch`(含 abi/signature hash 细节),跨命名空间与同命名空间不同版本两种错配都实测到。生产调用形状(每次调用新开一条 `cc.connect` 上下文)实测每次连接约 1 ms。

## E1:临时端点生命周期(一次运行一个端点)

| 步骤 | 实测 |
| --- | --- |
| 注册 | 服务子进程 `register` 后写 ready 材料,进程存活,`/tmp/c_two_ipc/<server_id>.sock` 存在且可裸 connect |
| 连接+调用 | 两个先后客户端子进程各自 `connect(name, address)` + `echo`,均退出码 0,回包携带服务端 pid |
| 干净关闭 | `unregister(name)` + `shutdown()` 后退出码 0;套接字文件被卸载(消失),`server_address()` 回读变 `null` |
| 对已关闭描述符 | 新客户端 `connect` 立即失败(~0.0 s):`CoreError … IPC I/O error: No such file or directory`,客户端退出码 3 |
| 同名重建 | 新进程注册同一名字得到**新的** `server_id`/地址,新地址可服务;旧地址保持失效并快速报错 |
| 同 `server_id` 重建 | `set_server(server_id=<旧值>)` + `register` 精确复用旧地址 `ipc://<旧 server_id>`,客户端调用成功 |

## E2:重名注册与改名重试

| 场景 | 实测 |
| --- | --- |
| 跨进程同名注册(同 namespace、同契约,无 relay 锚) | **不冲突**:两个进程同时以同名注册成功,各自持有独立地址;带地址的连接分别正确路由到两个服务端(按地址而非名字) |
| 改一个随机人名再注册 | 可用:换名后注册并调用成功 |
| 同进程重复注册同名 | `ValueError: Name already registered: '<name>'`(普通 Python 异常,可捕获、进程可继续) |
| 只按名字解析(不给地址) | `LookupError: Name '<name>' is not registered locally and no address was provided`——清空 anchor 后名字没有跨进程可达性 |
| 契约错配 | 见"寻址与命名":connect 时 `ContractMismatch`,快速失败 |

公开错误表里有 `ResourceAlreadyRegistered`("relay 拒绝重名注册"),它属于 relay/anchor 路径;生产与本次探测一致地清空 anchor,该路径未验证(公开表面未见启动 relay 的 API),已列为未验证项。

## E3:异常退出(SIGKILL 服务端/客户端)

| 场景 | 实测 |
| --- | --- |
| 服务端进程组被 SIGKILL | 驱动记录到组长真实退出 `-9`(按信号死亡);无"假定已停"的推断 |
| 存活连接上的下一次调用 | 立即失败(~0.0 s):`ClientCallResource … DispatchUncertain: IPC I/O error: Broken pipe (os error 32)`——错误明示这次调用的结果**不确定**,与"失联不等于停止"一致 |
| 对陈旧地址重连 | 立即失败(~0.0 s):`CoreError … Connection refused (os error 61)`,无挂起 |
| 残留 | 陈旧的 `/tmp/c_two_ipc/<server_id>.sock` 文件**保留**,裸 connect 被拒;文件存在不表示端点活着 |
| 同 `server_id` 重建 | 在陈旧文件仍存在时成功:同地址复活并由新进程服务,客户端调用成功;该新进程随后干净关闭时把文件正常卸载 |
| 客户端调用中途被 SIGKILL | 服务端存活,后续新客户端调用正常;无服务端崩溃或额外残留 |
| 收尾 | 本次全部 run 结束后,本探测创建过的每个套接字文件均为已卸载状态;`/tmp/c_two_ipc` 中他人条目未触碰,本探测零残留,无遗留进程 |

## E4:`set_server`/`set_client` 的作用域与时机

可观测判据:注册前 `set_server(ipc_overrides={"max_execution_workers": 1})` 后,3 个并发 `slow`(各 0.6 s)墙钟 1.821 s(串行);无 override 的对照组墙钟 0.612 s(并行)。以下墙钟均取自归档的最终原始文件。

| 场景 | 实测 |
| --- | --- |
| `register` 之前设置 | 生效(如上 1.821 s vs 0.612 s) |
| `register` 之后设置 | **静默忽略**,仅 `UserWarning: Server already started, set_server() ignored. Call set_server() before register().`;并发仍并行(0.612 s) |
| 注册前连续两次不同设置(1 再 8) | 后者生效(并行,墙钟 0.610 s),无报错 |
| 超上限值 `max_execution_workers=999999999` | `register` 抛 `ValueError: max_execution_workers (999999999) must be <= 64`(0.6.0 上限 64,与 `rpc_config` 记载一致) |
| 未知键 `not_a_real_key` | `set_server` 抛 `ValueError: unknown IPC override option: not_a_real_key`;捕获后注册仍可正常进行 |
| `connect` 之后 `set_client`(含无效键) | **静默忽略**,仅 `UserWarning: Client connections already exist, set_client() ignored.` |
| 任何 `connect` 之前 `set_client` | 被真正求值:无效键 `ValueError`,合法覆盖被接受,随后连接与调用正常 |
| 作用域 | 进程内全局、不跨进程:全新进程的公开回读 `cc.config.settings` 为默认值(relay `null`、shm 4096、chunk 1 MiB);IPC override 值没有公开回读面 |
| 一进程多资源 | 同一进程注册两个资源共享**同一个**服务端地址(一次 server,两个名字),各自可被调用 |

## 对执行计划第七节 / 5-A 的生命周期约束

1. 每运行端点的地址必须取 `cc.server_address()` 回读(`register` 返回值只是名字);私有 ready 材料发布 `{address, name, instanceId}` 时以 `address`(`ipc://<server_id>`)为路由键。名字在同 namespace 无 anchor 时跨进程不冲突、也不提供跨进程解析,只能作显示/本地标识——与 ADR-025"名字不用于核对身份"一致;计划里"重名就换一个随机人名"的重试可用且无害,但不能作为唯一身份防线(防线是地址+凭据)。
2. 本次实测的退出只有两种场景:序列 `unregister(name)` + `shutdown()` 之后套接字文件消失、`server_address()` 回读变 `null`;进程组被 SIGKILL 后该文件保留、裸 connect 被拒。正常解释器退出而不调 `shutdown`、只调 `shutdown` 而不调 `unregister` 等其他退出路径本次未核对,此处不做推论。文件存在与否不能当 liveness;liveness 只能用成功调用/连接证明。同 `server_id` 可在 SIGKILL 留下的文件上原地址重建,因此这类文件的清理只需卫生目的、不阻塞重建;只能删除自己持有的端点文件。
3. 端点失联时,在途调用立即失败且错误标明 `DispatchUncertain`(结果未知);重连陈旧地址立即 `Connection refused`。失败类型与速度都支持把"live 不可达"显示为不可用而不是把对端进程判定为 stopped,保留原有 deadline 与事实。
4. `rpc_config.configure_server()`/`configure_client()` 的"每进程首次 `register`/`connect` 之前应用、幂等"纪律在 0.6.0 上仍是**必需**的:迟到调用被静默忽略(仅 UserWarning),不会报错提醒。重复设置后者生效;override 键与取值校验严格(未知键、超 64 的执行线程数都 `ValueError`)。设置不跨进程、不落环境变量;一个进程一个 server,多资源共享同一地址,与"Worker 运行时每持有进程一个端点"的形状一致。
5. 未验证项(明确边界):Windows 全部未验证;Linux 未在本机验证;relay/anchor 模式(含 `ResourceAlreadyRegistered` 的重名拒绝)未验证;退出语义只覆盖 `unregister`+`shutdown` 序列与 SIGKILL 两种场景,正常解释器退出不经 `shutdown`、单独 `shutdown` 不带 `unregister` 未核对;`hold`/`bridge`/`ResourceBridge`/`InputLifetime` 等其余公开 API 未核对;大帧/分块阈值与吞吐未测(第五步按计划沿用既有帧限额,不依赖新测值)。
6. 结论:四项核对全部通过实测,未出现需要修改 ADR-025 的不可行项;第五步可按第七节形状实施(本微任务不实施)。

## 清理证据

五个驱动入口退出码全 0;无被驱动 SIGKILL 后遗留的存活进程(`pgrep` 无 `probe_common`);本探测创建的全部 `/tmp/c_two_ipc/*.sock` 已随干净关闭卸载,收尾扫描为零残留;`/tmp/c_two_ipc` 中其他进程的条目原样未动(其间出现过一条不属于本探测的临时端点,由其属主自行消失,本探测未触碰)。运行期子进程的 `HOME`/`TMPDIR` 都指到 `<probe-root>` 内,日常主目录与黑板状态无写入。
