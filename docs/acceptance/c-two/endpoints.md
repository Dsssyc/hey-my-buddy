# C-Two 0.7.4 端点凭据：1-B 交付与验证边界

本步固定基线为 `9a9a66c35bc3eac2ed700e36da19f2aa478ce47c`，仅修改任务列出的三个 live 模块、七个测试/探针文件及本记录。工作区未提交，等待 Host 固定产物独立审查；本记录不是 Host 验收结论。完整检查、原生模型、日常安装、服务/Worker、登录和配置均未操作。

控制器注册后通过 `inspect_endpoint` 取得原生 `EndpointCredential`，将 `to_json()` 返回的字符串原样存入私有、独占创建的 ready descriptor；整个 descriptor 写入与读取均保持 16 KiB 边界。持有方通过 `from_json()` 复原，核对原生 address/context 与当前 Worker 已配置的私有域，同时核对完整 RunIdentity、instanceId、实际持有的 PID；释放时也重新验证，缓存不能绕过绑定。Windows 无凭据时回收返回 `not-applicable`，没有伪造 Unix 端点材料。

`EndpointSocketFact`、`C_TWO_IPC_DIRECTORY`、`_capture_socket_fact` 和 `cleanup_abandoned_socket` 已删除，没有旧入口兼容别名；不再推算 socket 路径、保存 dev/inode 或自行 unlink。`cleanup_owned_endpoint` 仅在 leader 已 poll/reap、PID 一致且持有组明确消失后调用一次原生 `reap_endpoint`。原生 `reaped`、`already-absent`、`busy`、`stale-target`、`unverified`、`io-error`、`not-applicable` 与 reason 保持原义，既有 Worker 日志消费这些结果；持有方保存该次结果，异常调用也留下未核实标记，不自动重试或 sweep。

端点、连接和 Worker runtime 均复用 `protocol.rpc_config`。Worker runtime 使用生产 BoardClient.state_dir；注入无 state_dir 的 client 时只使用 Worker 构造前已显式配置的原生私有域，不重新从环境选择根。角色接线核对当前原生受信域，传给 channel 的状态根来自该域；channel 固定状态根后再连接，不被后续 BUDDY_STATE_DIR 切换。既有 trusted controller 环境已经保留 BUDDY_STATE_DIR，无须修改范围外的启动器。未改等待线程或 `_call` 期限实现。

## 被拒绝产物与历史失败

首轮固定产物 `84110805821c0eec59d5d50742fc7d7bb8ba7e9d` 已被 Host 拒绝，不能作为验收通过的证据。Host 在实际 C-Two 0.7.4、私有根中执行六个受影响整模块，共 133 项，退出 1，4 项失败、1 项错误；原始日志 `endpoints-host-focused.log` 原样保留为返修任务材料的 `host-rejected-133.log`。正常/强杀回收得到 `unverified/endpoint-domain-mismatch`，跨身份 channel 得到 `transport-unreachable`，原帧测试连接得到 No such file or directory，替换夹具的新旧实际地址不同。这些是真实失败，没有归因于机器负载或偶发问题。

首轮本地 91 项、SDK 生命周期模拟接线 8 项及十处保护/接线变异的日志仍保留在 `<FIRST_TASK_TMP>`，仅说明当时的本地覆盖。首轮唯一真实 IPC 尝试注册失败为 `CoreError ... Operation not permitted (os error 1)`，退出 1；之后及本次返修均未再次尝试真实 IPC。历史接线模拟首次 5 项通过、3 项错误由夹具先写 spool 导致非私有 state，后改为预建 0700 state 得到 8 项通过，失败日志未覆盖。上述历史通过均未代替 Host 对最终源码的原生验证。

## 本次返修与实际检查

生命周期夹具现在读取 `rpc_config.configure_client(case_state)` 后的原生 `local_endpoint_context()`，复用实际规范化的受信域，生产凭据域检查未削弱。跨身份 channel 显式使用同一 case_state，仍要求 `identity-mismatch:attemptId`；直接 `cc.connect` 的帧测试先配置同一根，保留原来的无效帧和超长帧断言。已删除 WorkerLiveRuntime 无调用方的可选 state_dir 参数和分支，继续通过 client 或已经配置的 Worker 域选择根，保留 client 胜过环境的测试。夹具 reap 也关闭自己的 stdin/stdout/stderr。

Host 的真实结果否定了 `set_server(server_id=旧地址后缀)` 能构造同址新实例的夹具假设，因此删除 rebind 分支和参数。新增聚焦测试验证本项目将公开 SDK 的 `stale-target/target-changed` 原样返回且只调用一次；另外准备了原生公开接口场景，用一个端点的凭据向另一个端点请求 reap，要求 `stale-target` 且两端仍可用。0.7.4 的公开 `reap_endpoint` 文档声明该跨端点凭据场景，但本次沙箱未实测。此场景不是同址新实例替换验证；没有拼路径、修改 opaque 凭据、访问厂商内部实现或对其替换保护作静态证明。

离线公共材料复制到自有 `<TASK_TMP>/materials`，18 个哈希条目核对通过，含 17 个 wheel；提供的 uv.lock 与仓库一致。自有环境先执行 require-hashes/no-index 安装，再执行 `uv sync --frozen --offline --no-index --find-links <TASK_TMP>/materials/wheelhouse`，退出 0；实际读回 c-two 为 0.7.4，没有借用 Host 环境或旧 0.7.3 环境。

| 检查 | 实际结果 | 边界与材料 |
| --- | --- | --- |
| EndpointLifecycleTests、CleanupPrimitiveTests、ChannelUnitTests、HandleBindingTests、WorkerLiveUnitTests | 52 项，退出 0 | `focused.log/json`；生命周期、凭据成功复原和传输由单元模拟覆盖 |
| 修订的正常、强杀、跨身份、帧、跨端点凭据五个夹具方法 | 5 项，退出 0 | `fixture-simulation.log/json`；真实原生 context/config、生产本地帧和 channel/回收包装，SDK I/O、进程及成功凭据 codec 使用模拟，不能作为原生验证 |
| 活动转发与 Worker tick/renew/reconcile 接线 | 8 项，退出 0 | `wiring-local.log/json`；临时脚本仅替代 SDK 生命周期，保留既有 Worker 调用和本地 wire 准入 |
| 八处独立副本保护变异 | 每处退出 1，均为 AssertionError | `mutation-results.json`；unknown 组、完整身份、credential 地址/域、PID、instance、client 根、stale-target 事实映射 |
| 恢复三处旧夹具写法的独立副本 | 每处退出 1 | `regression-results.json`；未规范化 context 与省略 channel 根触发断言失败，raw connect 默认根触发 FileNotFoundError；SDK/进程模拟边界同上 |
| 指定 Python 文件语法、补丁空白及写入范围 | 通过 | `final-checks.json`；DSH 探针未执行，完整检查和模型未运行 |

测试子进程清理继承的 BUDDY_*、ANTHROPIC_*、C2_*、VIRTUAL_ENV 和 UV_PROJECT_ENVIRONMENT，再指定自有私有 state/runtime/ipc、TMPDIR 与 BUDDY_CHECKS_TMPDIR。临时材料全部位于 `<TASK_TMP>`；Worker 未删除任何文件/目录，只有测试自身的 TemporaryDirectory/fixture 收尾执行了删除。没有出现 Too many open files；未触及默认公共 IPC 域或日常进程。

## Host 待补核

请 Host 独立审查修订后的固定源码和补丁，再在允许私有 socket 与进程组核验的环境、实际 0.7.4 上重跑之前六个受影响整模块，重点包括 `SubprocessLifecycleTests`、`WorkerLiveRealPeerTests`、`WorkerEndpointClosureTests` 和未模拟 SDK 的 Worker 防护/接线测试。需核对正常 shutdown 后 already-absent、强杀自己的模拟控制器并确认两层停止后 reaped、活端点 busy、跨端点凭据 stale-target、同一私有 state 下 owned/foreign 隔离，以及完整主循环退出接线。主循环 stop 与 execute release 两处既有接线的独立副本变异仍须原生补核；本地接线模拟不能代替它们。保留历史真实失败，不调用模型或 sweep 默认公共域，不跑完整检查。

同址新实例替换的真实保护仍未验证，公开 SDK 场景的构造能力边界如上；本项目的原生状态映射已聚焦覆盖，不能据此宣称厂商替换保护通过。Windows 原生 not-applicable 尚未实测，仅有模拟覆盖。V-07/V-08 的最终源码原生行为仍未验收。

固定材料包括本 checkout 指定文件、`<TASK_TMP>/final.patch`、`<TASK_TMP>/repair.patch`、`<TASK_TMP>/final-manifest.json`、`<TASK_TMP>/fixed-source/`、实际检查与失败日志、八份保护变异副本及三份夹具回归副本。确切 TASK_TMP 和 FIRST_TASK_TMP 只在结构化 outcome 中列出，不将本机目录写入仓库。工作区保留给黑板封存，没有自行提交或变动分支标签。
