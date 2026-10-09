# C-Two 0.7.3 执行计划与验收记录

起点是 `socu/buddy-core` 的 `0fffda8f`，实施分支为 `socu/c-two-073`，检出为 `~/.codex/worktrees/c-two-073/hey-my-buddy`。Host 沿用 `codex-adr025`，本批另建宏任务；不推送，不安装或升级日常运行时。已读 DSH 原生续接、ADR-025 第五步的 Host 验收记录、待办中的 C-Two 0.7.3 实测、ADR-025 第 13 条、ADR-007 和当前架构。

## 选择与边界

保留项目版本与 `CONTRACT_VERSION=0.29.0`、schema 15。ADR-007 要求实际契约改变才提升契约版本；本批不改变命名操作、请求与结果、公开 CLI 参数和失败码。控制器就绪材料属于尝试私有的临时材料，改为承载 C-Two 的端点凭据，不改变数据库 schema。0.29.0 尚未安装，本批与既有未安装的改动作为同一版切换；0.6.0 与 0.7.x 的传输不能互通，安装时必须整批切换客户端、服务与 Worker，不能热接旧服务。若发现必须改 schema，先停止说明。

Unix 端点目录选择为 `<state>/ipc`，复用项目已有的私有目录保护，在第一次本地注册或连接之前调用 `cc.set_local_endpoint(root=...)`。服务、Worker、控制器和 CLI 客户端用同一状态根推导同一目录；不用用户的 `C2_IPC_ROOT` 决定本项目的域。目录必须属于当前用户、为普通目录、无链接、权限 0700；已有不安全目录拒绝使用。Windows 不传根目录，保留 C-Two 的默认 Named Pipe 域。配置在进程中冻结，不能悄悄切换状态根；测试必须在自有进程中使用一致的私有域。

关闭 buddy 缓冲池只设置 `pool_enabled=False`，删除池段大小与池段个数配置及其推导出的容量报告；保留与消息上限、重组及等待/控制并发有关的限制，并按实际 0.7.3 行为核对。关闭池仍可能临时使用共享内存，不能描述成关闭全部共享内存。公共接口与版本以发布的 [0.7.3 包](https://pypi.org/project/c-two/0.7.3/)、其公开接口说明和本批私有探针为依据；代码中的实测事实只写 Host 本次亲自复核过的事实。

控制器注册后用 `cc.inspect_endpoint` 取得凭据，原样通过 `EndpointCredential.to_json()` 保存；Worker 用 `EndpointCredential.from_json()` 读取，仍验证尝试身份、控制器 PID 与实际停止证据。确认持有的进程组消失之后只调用一次 `cc.reap_endpoint`，记录 `reaped`、`already-absent`、`busy`、`stale-target`、`unverified` 等真实结果；后几种结果不重试删除。删除本项目的套接字路径推算、设备号/inode 登记和 unlink。就绪材料仍遵守现有整帧大小、普通文件与私有路径保护。

实时调用改用 `cc.with_call_options(proxy, timeout=...)`，捕获 `c_two.error.CallDeadlineExceeded` 并沿用现有超时失败码。删除为每次调用新建线程及相关等待箱；保留本项目自己的请求时间窗、排队/消费检查与幂等绑定，因为调用期限只停止调用方等待，已经派发的对端调用仍会完成。连接建立和业务调用的期限范围分别在记录中说明，不把业务调用期限写成停止对端执行。

不做 owner-bound 生命周期、`spawn_owned_child`、两侧引用清理、Worker 权限档位和控制台改动；不改 ADR、CONTEXT、AGENTS、README、待办或参考文档。端点由公共目录迁入私有状态目录、rpcProfile 的池容量字段删除以及安装时传输版本必须一致，作为对外可见变化在本记录列出，交 Claude Code Host 维护文档。

## 微任务与顺序

每个微任务首次提交都省略 `adapter/provider/model/effort` 四个字段，经路由选择 buddy。产物缺陷拒绝后在原 run 上 `continue`，不指定配置；确需更换 buddy 用 `reroute`。只有供应方不可重试限流可按用户许可在原 run 上用完整配置继续，并登记旧 buddy、新 buddy 和路由决定编号。

| 微任务 | 输入与唯一可写范围 | 做法与交付 | 聚焦验证 |
| --- | --- | --- | --- |
| 0-A 用量边界防护 | 本计划提交；`tests/python/buddy/harnesses/dsh/test_session_records.py`、`docs/acceptance/c-two-073-0a.md` | 先补非会话头首行与冻结截断两处测试，调用真实冻结与本回合投影，断言未知，生产代码不变 | V-01、V-02；该测试文件 |
| 1-A 配置与私有域 | 0-A 整合提交；`pyproject.toml`、`uv.lock`、`packaging/runtime-assets.json`、`protocol/rpc_config.py`、`protocol/transport.py`、`protocol/client.py`、`blackboard/service/daemon.py`、`buddy/runtime/worker.py`（后三者均在 `src/hey_my_buddy/` 下）、`src/hey_my_buddy/cli/checks.py`、`tests/python/support.py`、`tests/python/protocol/test_rpc_config.py`、`tests/python/protocol/test_ctwo_service.py`、`tests/python/protocol/test_transport_attach.py`、`tests/python/blackboard/tasks/test_workspace_api.py`、`tests/python/cli/test_checks_cleanup.py`、`tests/python/cli/test_checks_parallel.py`、`docs/acceptance/c-two-073-1a.md`；所需新公共模块先报 Host | 升依赖/锁；共享初始化接口关闭池、配置根；更新实际变动的资源清单，清单已覆盖则说明；测试夹具在第一次通信前接入私有域，不清扫公共目录 | V-03～V-06；RPC 配置、服务、检查运行器、打包受影响测试 |
| 1-B 端点凭据 | 1-A 整合提交；`src/hey_my_buddy/buddy/harnesses/c_two_live.py`、`src/hey_my_buddy/buddy/roles/live.py`、`src/hey_my_buddy/buddy/runtime/live.py`、`tests/python/buddy/harnesses/test_c_two_live.py`、`tests/python/buddy/harnesses/fixtures/c_two_live_peer.py`、`tests/python/buddy/roles/test_role_live_seam.py`、`tests/python/buddy/runtime/test_live.py`、`tests/python/buddy/runtime/test_worker_invariants.py`、`tests/python/buddy/runtime/test_worker_live_wiring.py`、`tests/probes/dsh_native_resume_smoke.py`（仅旧端点接口适配）、`docs/acceptance/c-two-073-1b.md` | 把共享私有域初始化接到控制器/Worker 实时入口；就绪材料改存原生凭据；停止证据之后一次原生回收，删除旧路径/文件身份实现，迁移既有防护测试 | V-07、V-08；实时端点、角色和 Worker 防护 |
| 1-C 调用期限 | 1-B 整合提交；`src/hey_my_buddy/buddy/harnesses/c_two_live.py`、`tests/python/buddy/harnesses/test_c_two_live.py`、`tests/python/buddy/harnesses/fixtures/c_two_live_peer.py`、`docs/acceptance/c-two-073-1c.md` | 使用原生调用期限，删除等待线程；保留过期请求不送达与原有失败码，覆盖已派发调用仍会结束 | V-09、V-10；实时通道与请求时间窗 |
| 1-D 整链核对与冒烟准备 | 1-C 整合提交；新 `tests/python/protocol/test_ctwo_073_integration.py`、新 `tests/python/protocol/fixtures/ctwo_073_controller.py`、`docs/acceptance/c-two-073-1d.md`；4 份冒烟脚本放任务根中交付 | 一套真实私有服务、Worker 与模拟控制器，无模型；脚本预备每个 harness 最短一回合，禁止执行模型调用 | V-11～V-13；独立端到端文件 |

表中目录路径均相对检出。前一项固定产物整合并受影响测试通过后冻结下一项基线；1-B 与 1-C 写同一文件，所以串行。Host 负责公共接口决定、冲突与整合，不由微任务越界改注册表、值或其他角色文件。发现需要扩大唯一可写范围先交付具体缺口，Host 评估；明显超出计划则停止说明。

## 验证编号

| 编号 | 需要取得的证据 |
| --- | --- |
| V-01 | 首行不是会话头：真实 freeze 不可靠、本回合 usage 未知；删除首行判断时新增测试失败 |
| V-02 | freeze 达到读取上限：基线不可靠、本回合 usage 未知；删除冻结截断判断时新增测试失败 |
| V-03 | 发布版版本/公开接口、本批独立进程下关闭池与 4 KiB、合法 8 MiB 往返；报告关闭 buddy 池，不把配置容量说成驻留内存 |
| V-04 | 服务、Worker、控制器和 CLI 首次通信前同根；根权限 0700、长路径可用、不同根连接失败；不安全目录拒绝、Windows 无 root 参数 |
| V-05 | 去掉根配置或关闭池配置时对应测试失败；所有原生 C-Two 测试和检查运行器只触及私有域，无公共命名空间清扫 |
| V-06 | 锁文件 c-two 0.7.3、构建资源覆盖、私有安装产物从该锁取得依赖；不更新日常运行时 |
| V-07 | 正常端点退出无残留；持有的控制器被杀后确认两层停止再 reap，回收对应端点；移除停止证据或凭据绑定时防护失败 |
| V-08 | 活端点 busy、旧凭据 stale-target、无法核实均保留真实结果且只调用一次，不误删目标；原生凭据编解码使用库而非自造字段 |
| V-09 | 原生调用期限按时抛出、映射旧超时码、同一连接随后可用；不新增每调用等待线程，去掉期限时测试失败 |
| V-10 | 已派发但超时的调用最终完成；排队请求过时不交给 owner/native loop，去掉本项目时间窗判断时测试失败 |
| V-11 | 无模型整链：真实服务→持有 Worker→模拟控制器，端点均在同一私有状态域，正常关闭与强行杀掉后无自身端点残留 |
| V-12 | 四个 harness 冒烟脚本就绪；用户另行批准后各最短一回合，记录次数、实际 CLI、交付/活动/两层停止与端点；失败重跑重新询问 |
| V-13 | 所有产物整合并合入最新 core 后，默认并行数完整检查一次，记录代码提交、项数、退出码和用时；文档后补只重跑卫生测试 |

测试编号从起点收集到本检出的忽略 `tmp/`，之后用集合差异登记新增、改名和删除，未变的以集合比较证明。Host 从固定输出提交建临时副本，独立运行聚焦测试并逐一移除目标防护确认断言失败；不会在共享检出上做变异，不改写原始失败记录。负载引起的检查运行器/取消测试偶发失败先如实记下，空闲后单文件复跑再判断，不因耗时或偶发失败改测试。

## 执行约束与收尾

每份任务说明写明：只运行受影响测试；Worker 不删除任何文件或目录，开始时创建并记下短的任务专用系统临时目录，`TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 指向其中，一次性材料全部留在里面，交付报告确切路径，由 Host 验收后按这一个路径回收；检查运行器自己创建并收尾的根不在此列。受管检出与用户仓库共用 stash、分支和标签，不使用 `git stash`，不新建、切换、移动或删除分支与标签，改动留在工作区由黑板封存；对比基线用临时副本。记录/夹具用 `~` 或占位符。

测试使用私有状态与运行时根，清除继承的 `BUDDY_*`、`ANTHROPIC_*`、`VIRTUAL_ENV` 和 `UV_PROJECT_ENVIRONMENT`；C-Two 探针也明确设置私有根，不使用默认公共命名空间。所有模型调用限于路由微任务；真实 harness 冒烟先备好脚本并向用户说明四次调用用途，批准之前不运行，探针出错的重跑也重新批准。不设置空的 `CLAUDE_CONFIG_DIR` 或 `CODEX_HOME`，不登录/登出、不改密钥、不读凭据文件内容。日常服务或 Worker 故障导致无法继续委派时立即停止报告，不尝试重启或替换。

交付审查绑定实际 artifactId、输出提交与累积补丁哈希；在黑板登记整合及验收后走 cleanup-plan/apply 回收确切受管检出。整合登记保留拒绝、continue、限流换手、Host 自行更正记录的事实。最后提交本记录、真实检查结果和外部可见变化，保留实施检出供 Claude Code Host 验收；验收前不开始其他工作。

## 整合登记

宏任务 `obj-e2bf6c47-a17b-42ba-9b75-a32581e43716`，0-A run `66028e52-f2f4-4ab8-8930-6c6a4d8bcff0`。首次提交省略四项配置；一次提交前校验拒绝是新记录尚未创建、includeUntracked 未选中实际文件，创建页头后沿用原 requestId 提交成功。初次路由决定 `dec-885d02c5-53c7-4087-958c-83e3882d4f72` 选择 ZCode / zai-api / GLM-5.3-Flash / max，供应方 429、1310、retryable=false 失败，两层停止确认；按用户许可在原 run continue 改用 Codex / openai / gpt-6-luna / high，不改全局路由配置。首次请求 low 被 CONFIGURATION_UNAVAILABLE 拒绝、没有新回合；只读核对 model-profiles 后使用启用的 high。

Host 在本批私有环境装发布的 c-two 0.7.3，用 57 字符且含空格的 0700 端点目录完成独立探针：4 KiB 与 8 MiB 往返成功；100 ms 期限分别在 105.243/105.219 ms 抛出 CallDeadlineExceeded（dispatch_uncertain），同一连接再调用成功，超时调用仍在对端完成。活端点回收返回 busy；正常关闭后 inspect=absent、reap=already-absent；仅杀掉自己的模拟进程组后 reap=reaped、inspect=absent。两次调用之后 outgoing SHM used_bytes=0、peak_bytes=8,392,704，不能解释成 RSS。目录只余 .gate/.gate.marker 协调文件，无端点文件。另核对完整 shutdown 后可在新会话显式配置另一个私有根，故进程中活跃域不能切换与测试停机后重新初始化应分开。原始探针与首次 semver 输入错误留在本检出 tmp/c073-host，未启动模型、未碰默认公共域。项目整链与全部行为验证仍待完成。

0-A 固定输出 `d5e326cd`，artifact `f48a0bb8-8049-4fbe-9b2c-638681d00159`，累积补丁 SHA256 `4be1ca5bc9da4f6b76b40bc965d67ce3ffed8b6ecf638ac97aec98a8d4eaad72`。Host 检查两条声明路径；Worker 因网络/缓存隔离缺依赖，仅以导入桩运行新增测试并如实提出 attention，未报整文件通过。Host 用已锁的真实依赖、固定输出 archive 副本运行 test_session_records：18 项、退出码 0、0.687 秒；各移除首行 session 判断和冻结截断可靠性判断，目标测试都退出 1，失败为 baseline.reliable=True。原补丁整合为 `494a6bda`，生产代码不变，新增 2 项，原有 16 项保留。没有代码或测试代改，也没有为措辞更正发起额外回合。

0-A 已在黑板绑定 int-13de4434-33f0-4067-9fa1-5ea9b0d07b47 后 accepted；cleanup-plan/apply 成功回收原受管检出。Host 保存 Worker 日志后，整体删除它报告的确切任务根 `<0A_TASK_TMP>`，未删其他对象。

1-A 基线 `861f298e`，run `a242d4a2-07d5-4b6a-ab20-7eec024a2d10`；首次路由省略四项配置，决定 `dec-5c74bf7b-49f2-461a-b7b6-ae72457ca669` 选择 ZCode / zai-api / GLM-5.3-Flash / max，同样返回供应方不可重试 429/1310，未交付代码，两层停止确认。按用户授权，原 run continue 改为 Codex / openai / gpt-6.1-sol / high，未改路由与用户配置。

V-09 的连接阶段前提核对未通过，1-C 暂停等待用户选择。Host 用发布0.7.3、独立私有 <HOST_TASK_TMP>/connect-deadline/ipc 启动模拟服务，拿到就绪后只对自己持有的服务进程组 SIGSTOP；独立客户端设置该私有根，记录 before-connect 后调用 cc.connect，3 秒仍未返回，尚未执行100ms with_call_options。外层只终止这个探针自己创建的客户端组；随后 SIGCONT 自己的服务并通过 stdin 正常关闭，探针整体退出0，未调用模型、未触及日常服务与默认域。原始 stdout/stderr 和脚本留在 tmp/c073-host/connect-deadline.*。公开 cc.connect 签名仅 crm_class/name/address，ClientIPCOverrides 没有连接期限字段；现有等待线程同时覆盖连接及调用，故不能把仅业务调用期限描述为完整等价替换。不自行保留部分保护或引入其他实现，已向用户说明并询问；1-A 独立 run 继续收尾。
