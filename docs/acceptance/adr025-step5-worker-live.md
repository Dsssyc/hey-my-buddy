# ADR-025 第五步微任务 5-B2：Worker 持有的 live 端点与绑定

状态：补正 Host 对固定产物 `daad210f4af974342c0a5ad9adf2c2fa7f97227a` 的审查拒绝，新增 best-effort named detach 及竞争验证；修订后的真实 peer 路径等待 Host 验证和固定提交，尚未验收、未生产启用。原 run 为 `a349af9f-afc2-41b7-99b7-a65d016eab7d`，实际检出 HEAD 仍为原基线 `df8b17e4948d17a3649a2518d547a8ed2ffe17b7`，没有切换或新建分支。治理接口此前封存的 `072ec49bdf38c5e51d0fb08bd10a5026b79bf9ad` 和 `daad210f4af974342c0a5ad9adf2c2fa7f97227a` 是固定证据，不是本检出已执行 git commit 的证明。

## 实现与边界

新增 `buddy/runtime/live.py` 导出 `WorkerLiveRuntime(client, worker_id, worker_instance, resolve_channel)`，只持有实际传入 handle 及其已经绑定的 channel。构造配置 C-Two server/client，早于第一次 BoardClient connect；start 显式或首次有效 bind 惰性注册一个 `WorkerRuntimeLive`，地址只从 `cc.server_address()` 回读。描述符复用 `LiveEndpointDescriptor`，不复制 socket 捕获、超时或异常清理，也不声称可选 socket 字段已被捕获。stop 局部 close、注销自己的资源并用 C-Two 原生 shutdown 结束自己的 server。

绑定读取 handle 的完整真实 `role_run_identity`，核对 claim 的 task/attempt/generation、resolver 的 bound 状态及 channel.identity。无身份的 command/unextracted handle 直接 False；pending 可以重试。同绑定缓存同一 channel，附着失败保留同 handle/channel/token 供重试，不造身份。只接受实现 LiveChannel 的有界 `CTwoLiveChannel` 后端，不另造角色通道或角色 harness 名字分支。

每绑定独立生成 service→Worker liveToken，controller channel/token 只留在 Worker 内存。严格 `WorkerLiveActor`/`WorkerLiveAttach` 的 workerId、workerInstance、attemptId、generation、nonce、identity、address、name、instanceId、liveToken 全部参与实际构造、读取或传输。refresh 只用同一 handle 对象、完整 identity 和 actor 再调用 BoardClient.live_attach；相同 PID 的其他 handle 或改变 nonce 都不能刷新。Worker 端点 instanceId 与 workerInstance 用途不同。

三 RPC 复用 Host 公共 `decode_live_wire_frame`、`authenticate_live_frame`、Wire 模型及 64 KiB 限额。找到真实内存绑定后认证进程 instanceId、独立 token 和全部六项身份，再核对 retained handle/channel 当前身份。request 只交 cached channel；observe 只转事实；capabilities 只转声明。旧、未知或已摘除绑定拒绝；异常只回 unavailable，不泄露 token 或异常文本。不增加请求账本、认证/超时实现、原生 stdio、turn、deadline、lease、cancel、SQLite 或框架。

map 锁只保护绑定、resolver reservation 及正在 attach/detach 的状态，resolver、BoardClient、peer 调用和 channel.close 都在锁外。reservation 防止解绑/stop 后迟到 resolver 重新附着。每绑定最多一个同步 attach 调用，重叠 bind/refresh 返回 False，可稍后重试。

unbind 先摘除匹配本地绑定并局部 close，然后用持有的 actor、全部 identity 和 instanceId 构造真实 `WorkerLiveDetach`，通过现有 BoardClient.live_detach 发送 `worker_live_detach`；不发送 controller token、address 或 name，不取消原生、不报告停止。detach 失败不恢复本地权限，保留待摘除绑定，可用同一 claim 再 unbind 或 stop 重试。网络不可达时只保证本地撤销和 best-effort 尝试，不能声称服务登记已消失。

解绑或 stop 发生在 attach 调用期间时，先尝试 detach；attach 返回后即使抛出异常也补偿 detach，因为失败回复可能在提交之后。若第一次 detach 尚未返回，完成它的调用方会在 attach 已返回时再 detach。同一 task/attempt/generation 在旧 attach 已返回且后续 detach 成功前禁止重绑，防止旧摘除擦掉同 Worker 资源的新 token；其他 holder 不受限制。这里复用现有公共字段，没有自行增加 token CAS 字段。stop 将仍持有绑定移入同一摘除流程，先结束自己的 server，再在 map 和生命周期锁外逐个 best-effort detach；重复 stop 只重试未成功摘除的项。

新增字段、参数和类均有实现及测试消费者，没有未使用预留项。生产消费者尚待 Host 接线；本轮没有修改公共值、protocol、角色、registry、worker.py、service.py、inquiry.py 或禁止文档。

## 固定依赖与验证边界

Host 公共认证依赖提交为 `1e734e12fe2fcc830655f2b5bc57c4440625adcc`，其中 `src/hey_my_buddy/buddy/harnesses/c_two_live.py` SHA-256 为 `c2c62ea06dde36143fbd767ea610ae66031642e6d10c5693c284107b09c408a7`。检出公共文件保持基线字节；测试在材料根的新 src/tests 副本仅覆盖这份获授权固定公共源码，最终由 Host 整合。

命令从检出根使用 `uv run --frozen --no-sync --no-cache --offline python -m unittest <selected-methods> -v`。依赖是材料目录已有私有 venv，未安装/升级运行时。PYTHONPATH 为新副本 src 与 tests/python 的实际绝对路径，设置 PYTHONDONTWRITEBYTECODE=1。清除继承的 BUDDY_STATE_DIR、BUDDY_RUNTIME_ROOT、BUDDY_RUNTIME、BUDDY_RUNTIME_IDENTITY、BUDDY_WORKER_STATE、BUDDY_WORKER_ID、BUDDY_AGENT_CREDENTIAL、BUDDY_AGENT_CREDENTIAL_FILE、VIRTUAL_ENV、UV_PROJECT_ENVIRONMENT，再设置私有环境；peer 同样清除这些变量，C2_ENV_FILE/C2_RELAY_ANCHOR_ADDRESS 为空。0 模型、0 安装版 harness，没有完整检查、日常数据、登录、凭据文件内容或配置操作。

[编号表](adr025-step5-worker-live-test-ids.tsv)共有 32 个新增实际 unittest 方法：此前 23 个编号保留，新增 B2-24 至 B2-32 九项 detach 验证。本轮仅运行 B2-01、09、10、11、12、21、22、24 至 32，共 16 项 detach 相关本地测试；未重跑其他十项本地测试或六项真实 peer。fake BoardClient/resolver 使用真实严格身份模型；named detach 帧测试实际调用 BoardClient 的公开方法和注入 call，未连接日常服务。fake registry 匹配 actor/identity/instance 摘除用于竞争测试，不替代真实服务 registry 验收。

真实 peer fixture 仍是 Worker 父进程持有其实际 Popen 子 controller。controller owner consume 后对实际 fake journal write+fsync，再 publish/settle；暂停提交测试检查尚未返回 queued、journal 和 inquiries 仍为空，不能把测试替身 queued DTO 当 native 提交证据。本轮只补 fixture 的严格 detach 和登记表，以及真实 unbind/stop 的登记数归零断言；未运行修订后的真实 peer，仍须 Host 验证。

## 实际输出与故障见证

历史证据保留：首次 `b2-green1-709e5dt_/focused-green1.log` 因 tests/python 缺少 PYTHONPATH 而 import 失败；随后 `b2-green2-i413131c/focused-green2.log` 为 14 项本地通过、六项真实 peer 权限失败，stderr 为 C-Two response pool 的 `shm_open failed: Operation not permitted (os error 1)`。未取得端点描述符，不能作为转达或 socket 消失证据。随后 fixture 补初始化失败收尾、runtime 补 register 失败 shutdown，`b2-final-local-u60yucs1/final-local-green.log` 为 17 项本地通过，0.005 秒，exit 0。这些是上一轮源码的结果。

上一轮 `b2-verification-1zpmlvzy` 的 identity、instance、来源门三组私有变异分别产生 4、2、1 个断言失败，exit 1；恢复后 16 项通过。Host 本次 continue 明确告知：它独立运行固定 `daad210f` 加最新固定公共后端，23 项通过、5.833 秒，含六项真实 peer，日志为 `phase/review-5b2-fixed1.log`。这是 Host 提供的旧固定产物运行结果，本 buddy 未执行或独立读取该日志；Host 随后因缺少 named detach 拒绝产物。23 项绿不等于满足该要求，也不能沿用到本轮修订。

本轮 `b2-detach-verify-dff_1rw8/detach-local-green1.log` 实际 16 项通过、0.012 秒、exit 0；verification.json 固定测试名单、源码摘要和公共依赖。覆盖严格 exact frame、锁外调用、detach 失败本地撤销和重试、stop 多 holder 摘除、attach/refresh/stop 与 late attach 竞争、同身份重绑隔离、故障 holder 不阻塞其他 holder，以及 65 次绑定循环无 fake registry 累积。

本轮变异只在 `b2-detach-faults-9px5nvpi` 的两个新副本：named-detach-omitted-oe99b1bq 去掉真实 BoardClient.live_detach，B2-24/25/26/32 四项均断言失败；late-attach-compensation-omitted-ljiw9e9h 去掉迟到 attach 补偿，B2-27/29 两项均断言失败。actual-red-output.log 均 exit 1、无 import/syntax 错误，fault-results.json 固定摘要与名单。检出从未被变异；最终恢复绿输出和交付摘要见本轮 manifest。

## Host 接线与剩余工作

Host 构造 `WorkerLiveRuntime(client=self.client, worker_id=self.worker_id, worker_instance=self.instance_id, resolve_channel=role_live.handle_live_binding)`，早于第一次 BoardClient connect。Worker.run 在 self.register 前 start，并在进程 run finally stop；直接 execute 的私有测试显式持有 start/stop 生命周期。每 Worker 进程只构造一个 runtime。

Worker._execute_selected 在实际 handle 写入 holder 后 bind，并在 monitor tick 对同一尚未 ready 的 handle 重试；resolver 和活动 monitor 共享 channel 缓存，不另造角色通道。传入该 claim、实际 handle 和 monitor 的 nonce；无 role_run_identity 的 handle 不触发 resolver。

_AttemptMonitor._renew 仅在真实 renew 成功、未 finished/拒绝且无需 reconcile 时 refresh；_AttemptMonitor._recover 仅在真实 reconcile 成功后 refresh。异常分支返回 True 仅表示继续监管，不能作续租证据。holder 释放时 unbind；失败摘除可在后续同 claim unbind 或进程 stop 重试。controller 异常 socket 由 Host 角色公共 helper 在实际 Popen 已收割且组消失后用 5-A cleanup 原语处理，runtime 不重复确认或删除。

Host 需验证修订后的六项 `buddy.runtime.test_live.WorkerLiveRealPeerTests`，使用最终五文件与固定公共后端的新私有副本，保留实际输出、journal、正常 Worker/controller socket 消失和登记数归零证据。不要沿用旧 `daad210f` 的 23 项结论。此验证及生产公共接线归 Host，本 buddy 不声称已经生产启用。

Git 共享元数据权限仍是交付边界；最终五文件摘要、范围证明及补丁由 manifest 固定。若 git add 被拒绝，按 Host 本次授权以 attention 交付系统封存 artifact，请 Host 对这些既有 scope 改动固定提交或确认治理封存 commit；不绕权限、不切分支或重建仓库。

## 材料与未变集合

登记根沿用 `<registered-short-root>`，确切根由任务输入固定；TMPDIR/BUDDY_CHECKS_TMPDIR 为该根 t。一次性材料仅在 m 的新名字目录；原 b2-interface-8j2sZveo、b2-report-KDT2Bfxv、b2-implementation-ydj0ngu5、b2-green1-709e5dt_、b2-green2-i413131c、b2-verification-1zpmlvzy、b2-final-local-u60yucs1、b2-delivery-4olahllz 全部保留。本轮新增 b2-detach-verify-dff_1rw8、b2-detach-faults-9px5nvpi；最终交付目录由 outcome 指定，含最终绿输出、五文件摘要、范围/编号核对和补丁。

旧集合按原基线逐字节核对：389 个既有 src/tests/python 文件、2,802 个旧 AST 测试编号保持不变；原编号排序清单 SHA-256 为 `86cd1e2212370bfbfce15ecdec64eaef7cfc0f782408d9b90867012354035308`。此前 23 个新增编号保留，新增九项无冲突；这是集合证明，不是旧测试执行结果。Worker 没有删除任何材料或扫描日常目录/IPC 残留；Host 验收后按任务输入确切根整体删除。
