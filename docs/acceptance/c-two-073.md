# C-Two 执行计划与验收记录（当前目标 0.7.4）

2026-10-11 当前关口：Host退回后的3-A/3-B/3-C已串行修复并整合，最终代码 `256023c56eeebc1f57da2889980c165166eb380d` 在指定core `12eb4fcd` 上的完整检查退出0，默认4并行，3,519项（跳过1）、216/216文件，用时645.517秒。所有范围内缺陷原run continue，正式scope版本、原706.360秒七文件失败、额外ZCode取消失败与单文件复跑、目标故障注入、编号差异和精确回收均见 [公开状态返修及最终记录](c-two-public-state-plan.md) 与 [3-C交付](c-two-state-boundaries.md)。3-C微任务已内部签收，受管检出及其精确任务根已回收，实施检出保留。下列2026-10-10关口是历史结果；本轮尚待Claude Code Host整批验收。既有Codex/Claude Code/DSH各一次授权真实冒烟保留原提交绑定；ZCode真实回合因额度仍未验证。不推送、不安装或升级日常运行时，验收前不开始下一条线。

2026-10-10 最新关口：补充夹具修复已固定审查、整合并内部验收，最终代码 `e0ce3d2be333b1655397920d59a8014162730849` 的完整检查退出 0，默认并行数，3,474 项（跳过 1）、213 个文件，用时 690.917 秒。指定 core `1f7e8b60` 及随后纯文档更新都已合入；实际 core 绑定为 `40d82d937caa03a058b484ff0f660657cd595945`。三次完整失败、Host 临时根过长的可复现诊断及短根最终通过均见 `c-two-repair-host.md`；历史各关口原样保留。用户分别单次批准的 Codex、Claude Code、DSH 真实 Worker 冒烟已通过（各一个回合、32.542 / 27.764 / 9.616 秒，各自两层停止与三个端点 absent）；ZCode 因额度不足由用户要求暂不调用模型，记为真实回合未验证。提交记录后停止等 Claude Code Host 整步验收，不推送、不安装或升级日常运行时。

起点是 `socu/buddy-core` 的 `0fffda8f`，实施分支为 `socu/c-two-073`，检出为 `~/.codex/worktrees/c-two-073/hey-my-buddy`。Host 沿用 `codex-adr025`，本批另建宏任务；不推送，不安装或升级日常运行时。已读 DSH 原生续接、ADR-025 第五步的 Host 验收记录、待办中的 C-Two 0.7.3 实测、ADR-025 第 13 条、ADR-007 和当前架构。

## 选择与边界

保留项目版本与 `CONTRACT_VERSION=0.29.0`、schema 15。ADR-007 要求实际契约改变才提升契约版本；本批不改变命名操作、请求与结果、公开 CLI 参数和失败码。控制器就绪材料属于尝试私有的临时材料，改为承载 C-Two 的端点凭据，不改变数据库 schema。0.29.0 尚未安装，本批与既有未安装的改动作为同一版切换；0.6.0 与 0.7.x 的传输不能互通，安装时必须整批切换客户端、服务与 Worker，不能热接旧服务。若发现必须改 schema，先停止说明。

Unix 端点目录选择为 `<state>/ipc`，在第一次本地注册或连接之前调用 `cc.set_local_endpoint(root=...)`。服务、Worker、控制器和 CLI 客户端用同一状态根推导同一目录；不用用户的 `C2_IPC_ROOT` 决定本项目的域。端点目录由项目以 0700 创建，已有目录的所有权与组、其他人不可写规则交给 C-Two 校验；保留链接与 `..` 防护，状态目录本批不新增权限要求。内部角色缺显式状态根时报错，默认解析仅限面向 Host 的 CLI。Windows 不传根目录，保留 C-Two 的默认 Named Pipe 域。配置在进程中冻结，不能悄悄切换状态根；测试必须在自有进程中使用一致的私有域。

关闭 buddy 缓冲池只设置 `pool_enabled=False`，删除池段大小与池段个数配置及其推导出的容量报告；保留与消息上限、重组及等待/控制并发有关的限制，并按发布的 0.7.4 行为重新核对。关闭池仍可能临时使用共享内存，不能描述成关闭全部共享内存。公共接口与版本以发布的 [0.7.4 包](https://pypi.org/project/c-two/0.7.4/)、其公开接口说明和本批私有探针为依据；代码中的实测事实只写 Host 本次亲自复核过的事实。

控制器注册后用 `cc.inspect_endpoint` 取得凭据，原样通过 `EndpointCredential.to_json()` 保存；Worker 用 `EndpointCredential.from_json()` 读取，仍验证尝试身份、控制器 PID 与实际停止证据。确认持有的进程组消失之后只调用一次 `cc.reap_endpoint`，记录 `reaped`、`already-absent`、`busy`、`stale-target`、`unverified` 等真实结果；后几种结果不重试删除。删除本项目的套接字路径推算、设备号/inode 登记和 unlink。就绪材料仍遵守现有整帧大小、普通文件与私有路径保护。

每次实时连接使用 `cc.connect(..., timeout=剩余秒数)`，业务调用使用 `cc.with_call_options(proxy, timeout=剩余秒数)`，捕获 `c_two.error.CallDeadlineExceeded` 并沿用现有超时失败码。删除为每次调用新建线程及相关等待箱；保留本项目自己的请求时间窗、排队/消费检查与幂等绑定，因为调用期限只停止调用方等待，已经派发的对端调用仍会完成。连接与调用采用同一个整体时间窗的剩余预算，整个等待线程删除。两处 CallDeadlineExceeded 映射既有超时码；需要区分未发出与结果未知时只读 transport_phase 的 pre_dispatch / dispatch_uncertain，不解析错误文字。

不做 owner-bound 生命周期、`spawn_owned_child`、两侧引用清理、Worker 权限档位和控制台改动；不改 ADR、CONTEXT、AGENTS、README、待办或参考文档。端点由公共目录迁入私有状态目录、rpcProfile 的池容量字段删除以及安装时传输版本必须一致，作为对外可见变化在本记录列出，交 Claude Code Host 维护文档。

## 微任务与顺序

每个微任务首次提交都省略 `adapter/provider/model/effort` 四个字段，经路由选择 buddy。产物缺陷拒绝后在原 run 上 `continue`，不指定配置；确需更换 buddy 用 `reroute`。只有供应方不可重试限流可按用户许可在原 run 上用完整配置继续，并登记旧 buddy、新 buddy 和路由决定编号。

| 微任务 | 输入与唯一可写范围 | 做法与交付 | 聚焦验证 |
| --- | --- | --- | --- |
| 0-A 用量边界防护 | 本计划提交；`tests/python/buddy/harnesses/dsh/test_session_records.py`、`docs/acceptance/c-two-073-0a.md` | 先补非会话头首行与冻结截断两处测试，调用真实冻结与本回合投影，断言未知，生产代码不变 | V-01、V-02；该测试文件 |
| 1-A 配置与私有域 | 0-A 整合提交；`pyproject.toml`、`uv.lock`、`packaging/runtime-assets.json`、`protocol/rpc_config.py`、`protocol/transport.py`、`protocol/client.py`、`blackboard/service/daemon.py`、`buddy/runtime/worker.py`（后三者均在 `src/hey_my_buddy/` 下）、`src/hey_my_buddy/cli/checks.py`、`tests/python/support.py`、`tests/python/protocol/test_rpc_config.py`、`tests/python/protocol/test_ctwo_service.py`、`tests/python/protocol/test_transport_attach.py`、`tests/python/blackboard/tasks/test_workspace_api.py`、`tests/python/cli/test_checks_cleanup.py`、`tests/python/cli/test_checks_parallel.py`、`docs/acceptance/c-two-073-1a.md`；所需新公共模块先报 Host | 升依赖/锁；共享初始化接口关闭池、配置根；更新实际变动的资源清单，清单已覆盖则说明；测试夹具在第一次通信前接入私有域，不清扫公共目录 | V-03～V-06；RPC 配置、服务、检查运行器、打包受影响测试 |
| 1-A2 更新发布依赖 | 本次计划提交；`pyproject.toml`、`uv.lock`、`src/hey_my_buddy/protocol/rpc_config.py`、`tests/python/protocol/test_rpc_config.py`、新 `docs/acceptance/c-two-dependency-update.md` | 锁定0.7.4，修正当前绑定版本的说明与断言；重新取得V-03/V-04/V-06、1-A聚焦与三处变异，旧结果保留为历史 | 1-A相关聚焦文件、三处变异、发布版与私有安装 |
| 1-B 端点凭据 | 1-A2 整合提交；`src/hey_my_buddy/buddy/harnesses/c_two_live.py`、`src/hey_my_buddy/buddy/roles/live.py`、`src/hey_my_buddy/buddy/runtime/live.py`、`tests/python/buddy/harnesses/test_c_two_live.py`、`tests/python/buddy/harnesses/fixtures/c_two_live_peer.py`、`tests/python/buddy/roles/test_role_live_seam.py`、`tests/python/buddy/runtime/test_live.py`、`tests/python/buddy/runtime/test_worker_invariants.py`、`tests/python/buddy/runtime/test_worker_live_wiring.py`、`tests/probes/dsh_native_resume_smoke.py`（仅旧端点接口适配）、`docs/acceptance/c-two-endpoints.md` | 把共享私有域初始化接到控制器/Worker 实时入口；就绪材料改存原生凭据；停止证据之后一次原生回收，删除旧路径/文件身份实现，迁移既有防护测试 | V-07、V-08；实时端点、角色和 Worker 防护 |
| 1-C 调用期限 | 1-B 整合提交；`src/hey_my_buddy/buddy/harnesses/c_two_live.py`、`tests/python/buddy/harnesses/test_c_two_live.py`、`tests/python/buddy/harnesses/fixtures/c_two_live_peer.py`、`tests/python/protocol/test_inquiry_transport.py`、`tests/python/buddy/harnesses/test_live_channel.py`、`tests/python/protocol/test_activity.py`、`tests/python/buddy/harnesses/zcode/test_native_run.py`、`tests/python/buddy/runtime/test_worker_invariants.py`、`tests/python/blackboard/tasks/test_inquiry.py`、`docs/acceptance/c-two-deadlines.md` | 使用原生调用期限，删除等待线程；保留过期请求不送达与原有失败码，覆盖已派发调用仍会结束 | V-09、V-10；实时通道与请求时间窗 |
| 1-D 整链核对与冒烟准备 | 1-C 整合提交；新 `tests/python/protocol/test_ctwo_integration.py`、新 `tests/python/protocol/fixtures/ctwo_controller.py`、`docs/acceptance/c-two-integration.md`；4 份冒烟脚本放任务根中交付 | 一套真实私有服务、Worker 与模拟控制器，无模型；脚本预备每个 harness 最短一回合，禁止执行模型调用 | V-11～V-13；独立端到端文件 |

表中目录路径均相对检出。前一项固定产物整合并受影响测试通过后冻结下一项基线；1-B 与 1-C 写同一文件，所以串行。Host 负责公共接口决定、冲突与整合，不由微任务越界改注册表、值或其他角色文件。发现需要扩大唯一可写范围先交付具体缺口，Host 评估；明显超出计划则停止说明。

## 验证编号

| 编号 | 需要取得的证据 |
| --- | --- |
| V-01 | 首行不是会话头：真实 freeze 不可靠、本回合 usage 未知；删除首行判断时新增测试失败 |
| V-02 | freeze 达到读取上限：基线不可靠、本回合 usage 未知；删除冻结截断判断时新增测试失败 |
| V-03 | 发布版版本/公开接口、本批独立进程下关闭池与 4 KiB、合法 8 MiB 往返；报告关闭 buddy 池，不把配置容量说成驻留内存 |
| V-04 | 服务、Worker、控制器和 CLI 首次通信前同根；根权限 0700、长路径可用、不同根连接失败；不安全目录拒绝、Windows 无 root 参数 |
| V-05 | 去掉根配置或关闭池配置时对应测试失败；所有原生 C-Two 测试和检查运行器只触及私有域，无公共命名空间清扫 |
| V-06 | 锁文件 c-two 0.7.4、构建资源覆盖、私有安装产物从该锁取得依赖；不更新日常运行时 |
| V-07 | 正常端点退出无残留；持有的控制器被杀后确认两层停止再 reap，回收对应端点；移除停止证据或凭据绑定时防护失败 |
| V-08 | 活端点 busy、旧凭据 stale-target、无法核实均保留真实结果且只调用一次，不误删目标；原生凭据编解码使用库而非自造字段 |
| V-09 | 自建对端暂停时，首次连接与已连接各一例按连接期限抛出pre_dispatch；去掉连接期限目标测试失败，仅暂停自建进程。业务调用期限按时抛出、映射旧超时码、同一连接随后可用；不新增每调用等待线程，去掉期限时测试失败 |
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

1-A 第一份固定输出 `73d326d0`（artifact `261370ab-5755-4655-ac4a-6364ec26e5fc`，SHA256 `f2d15856bac7f0739f20c2448bd0f02bc034fd897134ba8519e97c0149b64120`）未验收。14 个改动路径均在范围内；Worker 如实报告联网/IPC/ps 沙箱限制，锁仍0.6.0。Host 在固定源码的私有副本生成验证用锁（仅 c-two 0.6→0.7.3）并完成 uv sync --frozen，参考锁作为联网解析材料交给原 Worker，实施分支未代改其锁或代码。Host 实测库支持 state0500/ipc0700 完成注册/调用，而交付把state也强制0700、将旧只读成功测试改成拒绝，属于范围内行为回归。尝试登记 rejected 被当前0.27服务以 NOT_READY 拒绝（attention边界不能直接reject）；已用原run continue 明确打回，要求保留state既有owner-private语义、端点仍0700、恢复成功用例、交付锁和确切任务根。continue省略四项配置，无新run；独立的1-A返修继续，1-C仍暂停。

1-A 首份输出的真实聚焦批次（固定生产代码，测试依赖使用Host生成的新锁）运行48项，47通过、1失败，退出1、87.039秒；不改写失败。失败为 RealDaemonControlTests 健康等待超时。Host独立复核 private_environment(work, BUDDY_STATE_DIR=work/state) 实际返回 BUDDY_STATE_DIR=work，原因是改用 support._child_environment 后它强制采用第一参数作状态根：服务端点和两个supervisor落在work，测试仍观察/清理work/state。该用例原收尾删除了自己的work后留下两个仅本测试创建的supervisor；Host只对 <HOST_TASK_TMP>/1a-native-first/<exact-fixture>/workers/local 与 local-2 写 stop.request，确认两进程不再存在，无信号/删除公共目录或日常Worker。确切路径和PID仅存tmp/c073-host/1a-first-survivor-stop.json；此夹具接线/收尾缺陷须原1-A继续修，不由Host代改。其他检查清理测试按预期制造的未完成证据也保留，不能把它们解释为本批生产残留。

1-A 第二份固定输出 `6853febb`（artifact `5a1a2b86-ee53-481b-92eb-7d00f4a501f8`，SHA256 `82f8ba67baa86f59fbf00094edd7e2a03bf6e151bc7f66e6eb41ba9a8d3f7c8a`）已恢复只读 state 原语义并由 Worker 交付新锁；仍未验收，原run continue修上述daemon夹具缺陷，不指定四项配置。Host 在固定副本独立跑两个真实目标与 transport 整文件29项，退出0、2.015秒；删除根setter后真实原生回读为私有fallback而非state/ipc，目标assertEqual失败exit1；移除pool_enabled=False后真实outgoing SHM used_bytes=268,455,936，目标assertEqual(used,0)失败exit1。两变异均在各自私有域，不碰公共命名空间，不用CoreError当防护证据。

V-06 的私有准备：第二份固定产物执行 uv build 成功生成 wheel/sdist；install.test_packaging 与 install.test_runtime 共44项退出0、42.818秒。另在本批自建的私有wheel环境，从该锁的 uv export 用 --require-hashes 安装10个依赖，读回 c-two0.7.3、pydantic2.13.5，其余原锁版本保持；没有安装日常运行时。此时1-A第三回合仍修夹具，部分产物未整合，不能把这些验证称为整批完成。

1-A 最终固定输出 `552ea251`（artifact `5c67e96f-90da-4bff-b201-18515aec15ac`，SHA256 `78dd556eb338a9380a528144998ef4fe861b1b4354d99da560979842075b461b`）15个路径均在范围内，已核对源补丁、代码与库公开接口。Host独立跑 RPC、C-Two服务、transport、workspace API、checks cleanup/parallel 共82项，退出0、54.630秒；原48项失败保留。真实daemon满等待槽时 health/renew/cancel/result分别0.115/0.009/0.015/0.018秒，waits3.559秒；自身daemon/supervisor合作停止，无该夹具进程残留。检查清理三个用例故意构造的未完成状态及证据是预期行为。生产源码与上一固定输出一致，既有44项打包/运行时和wheel/sdist/按锁安装证据继续适用；已核对私有wheel的metadata与其内置uv.lock依赖为c-two0.7.3，安装位置只在Host任务根。

Host从最终固定输出重新建立三份变异副本：去掉私有根setter，原生回读目录assertEqual失败；去掉关闭池配置，used_bytes=268,455,936而非0，assertEqual失败；把daemon夹具重新改成work覆盖state，实际Popen env根assertEqual失败。三项各exit1，正常82项exit0，不用库异常代替断言。第一次变异工具把源码副本与运行目录同名导致FileExistsError，尚未执行测试；保留该工具失败后换独立运行目录，未删文件绕过，也未算作防护证据。

编号集合：起点3230→当前3248，202模块、加载错误0、无重复编号；未变3223，3个模块内改名、4个因旧池假设退出的用例、22个实际新增（包含0-A两项）。三改名及四删除理由见1-A记录，原始清单/集合差保存在tmp/c073-host与Host私有根；不把加载清单当作3248项实际运行结果。全部其他源码与文档的原测试编号保留。

1-A 原补丁整合为 `7064e32b`；黑板整合 int-77bd89c5-540f-4487-8cc5-8ab991997484 已 verified，原run accepted。cleanup-plan/apply 成功回收登记的受管检出；Host保留交付日志后整体删除结构化summary报告的确切 <1A_TURN4_TASK_TMP>，只按报告路径回收，不按相同前缀推断别的目录。实施检出自己的 uv sync --frozen 已切到 c-two0.7.3；此环境及上述私有wheel环境都不是日常运行时。Host任务根的探针、固定副本、原始失败与检查证据仍保留以供后续核对。

当前状态：0-A 与1-A已内部验收；1-B、1-C、1-D未开始。1-C因连接阶段期限前提未通过暂停，用户选择尚未返回。未做整批完整检查、四harness冒烟，也未准备或执行付费冒烟；恢复后继续剩余微任务、合入最新core，再在最终代码上按默认并行数跑一次完整检查，四个最短真实CLI回合须另行批准。日常服务/Worker、配置和登录未改。实施分支不推送，保留供恢复与Claude Code Host后续验收。

## 2026-10-10 用户决定与恢复

目标由0.7.3改为已发布0.7.4，分支与已提交记录文件名保留。1-A2作为小微任务先于1-B，更新依赖/锁并重新取得版本绑定证据。当前计划段落已更新；此前0.7.3实测、失败、验收与暂停记载均是历史，不改写成0.7.4通过。1-C恢复，所有实时连接带连接期限、业务调用带调用期限，整个等待线程删除；两种期限同码，通过transport_phase区分。连接空闲60秒后泄漏由Claude Code Host用私有验收探针核对，本批测试集不加长等待用例。

日常0.6.0服务的FD接近上限。遇到Too many open files立即停止报告，不重启或替换日常服务/Worker，也不以私有服务替代委派。四个harness的真实回合及其重跑仍须用户逐次批准，旧批准不覆盖本批；其他原约束保持。

1-A2 基线111f885c，run9afaad94-0410-4bfc-826b-cba26a5a0b81，仍归原宏任务；首次提交省略四项配置。Host专用0.7.4环境复核connect公开参数、暂停自建对端：fresh与already-connected连接的150ms预算分别152.3/152.4ms抛CallDeadlineExceeded，transport_phase=pre_dispatch；对端恢复后可用。timeout0为0.1ms的pre_dispatch，负数/正负无穷/NaN/字符串均拒绝；100ms业务调用约101.8ms为dispatch_uncertain，同一连接再调用成功，原超时调用仍在对端完成。不带期限的独立客户端在一秒外层观察窗仍停在before-connect，仅终止自己创建的探针客户端，随后恢复并正常关闭自己创建的服务，inspect=absent。无模型、无默认公共域、无日常进程操作，原始证据tmp/c073-host/connect-timeout-probe.*。

Host公开表面比较0.7.3与0.7.4：__all__、异常类名字、ClientIPCOverrides/ServerIPCOverrides键及类型、所用公开函数签名均一致，唯一签名差为connect新增timeout；只核公开能力，不对vendor静态证明。0.7.4第二份私有探针重取4KiB/8MiB往返、调用期限、池计数和端点维护：两次8MiB约11.392/11.808ms，调用期限101.128/105.616ms、dispatch_uncertain，同连接随后可用，远端仍完成；outgoing SHM used=0、peak=8,392,704；alive reap=busy，正常exit0后already-absent/inspect absent，杀自己的模拟组exit-9后reaped/inspect absent；目录只余.gate/.gate.marker，无端点。脚本复制时输出文件名最初沿用了旧probe名，Host把新结果另存public-probe.result.json，并从未改的旧stdout恢复旧0.7.3结果JSON，随后修正新脚本输出名；两版历史数据均保留，不混写为本次结果。

1-A2 首次决定dec-83d0edbf-3bf8-43aa-8725-b031c8e233cd选择ZCode/zai-api/GLM-5.3-Flash/max，供应方不可重试429/1310失败，两层停止确认；原run continue改为Codex/openai/gpt-6.1-sol/high（已完成1-A同类配置与锁），按用户许可登记，未改用户路由/模型配置。尚无文件描述符耗尽错误；如遇到即停。

1-A2 第二回合停在依赖准备的Host assistance边界：原沙箱DNS与缺依赖失败保留，未声称升级完成。Host在固定输出5a8459d6的独立副本从公开PyPI执行uv lock --upgrade-package c-two，退出0，只更新c-two0.7.3→0.7.4；用导出的哈希锁下载当前平台依赖及构建依赖，两次退出0，共16个wheel和参考锁，命令与SHA256在原任务根host-materials/provenance.json。材料交原run continue自行核对写入，Host未代改实施分支uv.lock；本回合不指定配置四字段。真实无模型0.7.4探针证据继续保留，尚未复核最终固定产物或开展1-B/1-C。

Host在1-C开工前复核到protocol.test_inquiry_transport直接调用旧等待机制，包含桩SDK阻塞的用例，因此把该受影响文件加入1-C唯一可写范围。只适配SDK边界与保留原关联/失败覆盖，原生期限由私有真实对端核对；不是新通道或新增产品行为，其他归属不变。

1-A2最终固定输出dd0304dc（artifact5471e7a0-5227-48b6-9fe5-5430b3b08510，补丁SHA46279948a25a031377ef88e55073ac707c1ddf1d88d5d5c6cb5d5a9e4c0a728b）严格5路径，已核对12包锁仅c-two与项目metadata变动；442份运行源码、测试、资源及新锁与Host受检副本逐字节相同。Host在0.7.4重新取得82项RPC/服务/transport/workspace/checks聚焦exit0（测试57.170秒，命令60.163秒）、44项打包/runtime exit0（37.288秒）、三处目标变异均assert失败exit1。检查清理用例故意保留的未确认证据按预期，未当作生产残留。V-03/V-04/V-06在0.7.4重取：outgoing SHM used0/peak8392704、实际根与权限/隔离/长路径/Windows mock、wheel/sdist及require-hashes私有安装读回0.7.4、wheel内置锁与metadata一致；16个wheel素材只在任务根。Worker另有105项正常通过和沙箱socket/ps缺口，Host上述原生补核覆盖缺口，原失败保留。完整检查、真实harness回合未执行。

Host核对工具的两处失败已纠正：第三变异首次误定位support.py，0处匹配时停住未执行，随后在test_rpc_config.py正确夹具定位后目标assert失败；固定输出列表首次误取较旧输出，锁比较失败即停，随后按createdAt选最新输出并核对SHA/442文件，不使用旧产物做验收。只是Host验证工具问题，未代改Worker代码。原始日志与比较数据保留tmp/c073-host。

1-A2补丁整合fdff0c2a，integration int-81363d55-478d-4dff-ae38-41607dcda753 已 verified，原run accepted/completed；cleanup-plan/apply 已 applied。Host在保留最新原补丁、两回合顶层验证日志和固定副本后整体删除结构化交付登记的两个确切任务根 <1A2_FIRST_TASK_TMP>/<1A2_SECOND_TASK_TMP>，没有按前缀查找或删除其他对象；公共依赖材料另拷到Host自建根供后续微任务使用，增补editables的公开下载退出0、哈希已登记。实施检出uv sync --frozen退出0，现为0.7.4，只更新实施环境；记录补充后仓库卫生5项退出0。

1-B从9a9a66c3路由提交，run6f1205d5-c82d-45ea-87d3-241c627d0cc2，四项配置全部省略。决定dec-4a97c9c4-1d3c-4446-9f67-74628c5f84de选ZCode/zai-api/GLM-5.3-Flash/max，供应方不可重试429/1310失败，两层停止确认；按用户许可在原run continue改用已完成同类1-A2的Codex/openai/gpt-6.1-sol/high，未改变全局配置。Host首份continue错误地把四字段写在顶层，INVALID_ARGUMENT拒绝且无状态改动，随后按现有CLI契约改用configuration对象成功；原错误保留，未另开run。微任务仍在实施，未验收。

1-B继续期间，Host把最新socu/buddy-core的1a9decd9合入，合并提交8013f9cc58b5d78f8bc7fe63e5acf4ab7673a90d，仅docs/design/backlog.md变化，没有手动改Host维护文档；本项目端点仍按计划0700，未把Host的其他权限实测写成自主证据。1-C任务另明确V-10必须核对连接消耗部分预算之后的排队截止时间，复用现有本机时间窗，防止连接后服务端重新获得整窗；涉及公开schema或范围外接口则停止交付缺口。B的固定基线与写入范围保持。监测包装器未取得继续回合结果后，Host按指南前台持有原run唯一await，不另开run。

1-B首份固定84110805已拒绝：Host133项exit1，4失败1错误（夹具/tmp别名域不一致、跨身份漏state根、直接SDK连接选错根、逻辑server_id不能固定原生地址）；原run continue无四字段完成五项返修，未由Host代改范围内源/测试。最终固定e9206d5f37d8d25628b7d1951c0898cd9484cf13，artifact b6cff186-a980-4fda-b065-7e4ba5d5eb06，SHA b38dc39c34f154eab93b7f74821f761a8a2002ce8774d2e3c0370331d4645392，11路径均核对；Host134项exit0/23.103秒，12份独立最终源码副本变异均目标assert失败exit1（包括真实Worker退出stop与execute回收）。正常shutdown后already-absent，强杀自己的控制器后reaped且inspect absent，busy及跨端点旧凭据stale-target保留，两端点仍可用；同址实例替换未构造，不以此对厂商做静态证明，Windows仅mock。

Host整合时独立补了三处范围外接缝：service.py实时client factory显式传store.directory；黑板整链测试的旧socket_path导入/断言改为原生inspect事实，并使Peer使用board的状态根；ZCode旧fixture清理/ready字段断言改用EndpointCredential/inspect，在绑定前配置其私有client根且清继承ANTHROPIC/C2环境。第一轮26项exit1有两处夹具/字段遗漏，修正后26项exit0/29.887秒；又在与board不同的环境根下创建service channel，1项exit0，删显式store.directory参数则该目标assert失败exit1。这些由Host处理公共接线与范围外测试，所有第一次失败保留；不改harness行为，不调用模型。

Host编号核对工具首次用完整loader发现范围外黑板旧socket_path导致加载失败，已作为上述整合接缝修正，不隐去模块。编号3248→3259，未变3240个以集合相等证明，无加载错误/重复；8旧编号退出对应19新编号（8迁移、11新增），原始清单及集合差只存tmp/c073-host。

| 退出的编号 | 对应迁移编号或原因 |
| --- | --- |
| `buddy.harnesses.test_c_two_live.CleanupPrimitiveTests.test_a_replaced_file_refuses_and_a_missing_file_is_already_absent` | `CleanupPrimitiveTests.test_public_reap_facts_are_preserved_once（本项目文件身份实现删除，状态交SDK；补独立stale-target映射）` |
| `buddy.harnesses.test_c_two_live.EndpointLifecycleTests.test_the_socket_identity_is_captured_from_the_registered_address` | `EndpointLifecycleTests.test_native_credential_json_is_preserved_without_reencoding` |
| `buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_a_replaced_socket_file_is_refused_by_the_cleanup` | `SubprocessLifecycleTests.test_a_credential_for_another_endpoint_is_stale_target（真实同址替换构造未验证，边界已说明）` |
| `buddy.roles.test_role_live_seam.HandleBindingTests.test_ready_cleanup_cannot_name_an_unrelated_socket` | `HandleBindingTests.test_credential_address_and_domain_mismatch_never_reap` |
| `buddy.roles.test_role_live_seam.HandleBindingTests.test_unknown_controller_group_never_deletes_endpoint` | `HandleBindingTests.test_unknown_controller_group_never_reaps_endpoint` |
| `buddy.runtime.test_live.WorkerLiveRealPeerTests.test_b2_17_real_same_name_different_addresses_and_normal_socket_disappearance` | `WorkerLiveRealPeerTests.test_b2_17_real_same_name_different_addresses_and_normal_endpoint_disappearance` |
| `buddy.runtime.test_worker_live_wiring.WorkerEndpointClosureTests.test_controller_end_through_execute_clears_only_the_captured_socket` | `WorkerEndpointClosureTests.test_controller_end_through_execute_reaps_only_the_owned_endpoint` |
| `buddy.runtime.test_worker_live_wiring.WorkerEndpointClosureTests.test_worker_exit_closes_its_own_c_two_socket` | `WorkerEndpointClosureTests.test_worker_exit_closes_its_own_c_two_endpoint` |

新增或迁移的全部编号：

- `buddy.harnesses.test_c_two_live.CleanupPrimitiveTests.test_address_and_foreign_domain_refuse_before_reap`
- `buddy.harnesses.test_c_two_live.CleanupPrimitiveTests.test_public_reap_facts_are_preserved_once`
- `buddy.harnesses.test_c_two_live.CleanupPrimitiveTests.test_stale_target_from_public_reap_is_preserved_without_retry`
- `buddy.harnesses.test_c_two_live.CleanupPrimitiveTests.test_windows_no_credential_is_not_applicable`
- `buddy.harnesses.test_c_two_live.EndpointLifecycleTests.test_native_codec_rejects_malformed_material`
- `buddy.harnesses.test_c_two_live.EndpointLifecycleTests.test_native_credential_json_is_preserved_without_reencoding`
- `buddy.harnesses.test_c_two_live.ReadyMaterialTests.test_escaped_opaque_json_cannot_exceed_ready_frame`
- `buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_a_credential_for_another_endpoint_is_stale_target`
- `buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_a_live_endpoint_is_busy`
- `buddy.roles.test_role_live_seam.HandleBindingTests.test_a_native_refusal_is_reported_and_never_retried`
- `buddy.roles.test_role_live_seam.HandleBindingTests.test_credential_address_and_domain_mismatch_never_reap`
- `buddy.roles.test_role_live_seam.HandleBindingTests.test_exceptional_reap_is_not_retried`
- `buddy.roles.test_role_live_seam.HandleBindingTests.test_release_revalidates_every_identity_instance_pid_and_cached_address`
- `buddy.roles.test_role_live_seam.HandleBindingTests.test_unknown_controller_group_never_reaps_endpoint`
- `buddy.roles.test_role_live_seam.HandleBindingTests.test_unreaped_or_wrong_popen_pid_never_reaps_endpoint`
- `buddy.runtime.test_live.WorkerLiveRealPeerTests.test_b2_17_real_same_name_different_addresses_and_normal_endpoint_disappearance`
- `buddy.runtime.test_live.WorkerLiveUnitTests.test_explicit_client_root_wins_over_environment`
- `buddy.runtime.test_worker_live_wiring.WorkerEndpointClosureTests.test_controller_end_through_execute_reaps_only_the_owned_endpoint`
- `buddy.runtime.test_worker_live_wiring.WorkerEndpointClosureTests.test_worker_exit_closes_its_own_c_two_endpoint`

1-B整合53b0130a，integration int-7920ec42-6a5a-47a6-9c66-41bf67e4ab73已verified，原run accepted/completed；cleanup-plan/apply applied。Host保留两回合的顶层交付日志、原补丁和最终固定副本后，按结构化交付登记的两个确切根 <1B_FIRST_TASK_TMP>/<1B_REPAIR_TASK_TMP> 整体回收，未扫描前缀或公共命名空间。Host显示清理进度的小脚本因非清理响应中的cleanup为None而报AttributeError，显示失败未改变状态，随后从对应记录正确读回accepted/applied，未重复回收操作。

1-C固定基线ebf8dfb6，run3fa17c8b-0820-481b-8151-c3f476030cc9首次提交省略四字段；决定dec-df9f6bd8-e8a8-4a39-9415-1f9e2e85c1e6选ZCode/zai-api/GLM-5.3-Flash/max，供应方不可重试429/1310失败，两层停止确认。用户许可原run continue采用已完成本批同类1-A2与1-B的Codex/openai/gpt-6.1-sol/high，未动全局配置。1-C仍实施，范围5文件（含受影响protocol.test_inquiry_transport），无完整检查、付费harness冒烟或日常服务/Worker变动。

1-C第一份固定a0c87c85只新增边界记录，未实施源码；它指出with_call_options不能接受原本仅cc.connect桩的EndpointConnection，以及4份通过旧_connect_and_call替换/覆盖的夹具。Host核对是本次既有SDK/删除旧入口的必要测试适配，通过scope-amend（scopeVersion2）把五个受影响测试纳入，原生产范围仍仅c_two_live.py，无公开CLI/schema/角色/registry扩张；同run continue不指定配置四字段。两个DSH测试消费者只验证，没发现独立编辑需求。范围外代码不由Worker改，原缺口记录保留；没有把未实施V09/V10当作通过。

1-C最终固定bbdef61c5b8aad1b5f97067b81aaffed99588a66，artifact ce7b9ea5-9892-4413-ba38-2c1fa6b42f51，补丁SHA 03e233e2fa12667908fda845e81635b03b0410f519b6451fae9386e2a09039c8，scopeVersion2十路径均核对。Host11个受影响整模块（含DSH共享消费者、Worker/黑板真实链）303项exit0，测试94.912秒/命令95.750秒；六个新增真实NativeDeadline案例与原12个真实peer案例通过。fresh/warm暂停只针对自建Popen，150ms预算约151.83/152.29ms得到pre_dispatch，恢复后可用；100ms业务调用105.31ms dispatch_uncertain，原调用完成且同一连接可用。连接300ms耗时后原500ms整窗仍约503.75ms结束，消费为空。全部等待线程/事件箱/积累permit/旧_connect_and_call删除，SDK负责connect与call等待，原失败码保持。

LiveWireRequest新内部deadlineMonotonic是有限非负数，生产channel始终写原始monotonic截止时间、endpoint/待消费slot实际读取，按本机IPC同一时钟使用；与原timeoutMs取较早值，保留100–5000ms边界，阻止连接后重获整窗。没有公共CLI字段、SQL schema或CONTRACT_VERSION改变。SDK既有transport_phase用于探针分类，生产返回同一旧失败码，无需增设无业务读取方的阶段字段；不解析异常文字。

Host七份独立最终源码变异取得目标assert失败exit1：移除connect预算的fresh/warm各一例、移除call预算、移除过期队列消费门、删除原始owner预算、删除消费前截止检查、删除负预算归零。第一轮Host脚本把第六目标类名写错导致加载ERROR，不计为防护证据；保持五项已取得证据后改正EndpointAdmissionTests，在已有副本用新运行目录得到目标assert失败，第七也确认assert，原工具失败保留。没有長等待连接泄漏测试，日常服务/Worker与模型未动，未完整检查。

编号3259→3268，未变3254集合相等，无加载错误或重复；五个旧线程/字段假设案例由十四个新原生边界/截止案例替代，净增9，完整raw名单只在tmp/。

| 退出编号 | 新覆盖与理由 |
| --- | --- |
| `buddy.harnesses.test_c_two_live.ChannelUnitTests.test_bounded_call_slots_report_busy_and_drain_back` | ChannelUnitTests.test_repeated_deadlines_need_no_call_workers_or_busy_slots（旧累积线程permit机制删除） |
| `buddy.harnesses.test_c_two_live.ChannelUnitTests.test_the_whole_connect_and_call_is_bounded_by_the_window` | ChannelUnitTests.test_native_connection_and_call_deadlines_keep_the_existing_taxonomy + NativeDeadlineTests（由真实SDK期限替代旧线程定时） |
| `buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_a_stalling_endpoint_returns_within_the_window_and_stays_bounded` | SubprocessLifecycleTests.test_a_stalling_endpoint_returns_within_the_native_window（保留2s stall/300ms窗/2.2s完成观察） |
| `buddy.harnesses.test_c_two_live.WireFrameTests.test_the_request_frame_is_the_request_plus_exactly_three_private_fields` | WireFrameTests.test_the_request_frame_carries_the_private_deadline_and_envelope（增加内部截止字段） |
| `protocol.test_inquiry_transport.TransportTests.test_stalled_sdk_calls_expire_without_reporting_a_stopped_owner` | TransportTests.test_sdk_deadline_facts_expire_without_reporting_a_stopped_owner（桩注入原生异常，不自行计时） |

新增或迁移的编号：

- `buddy.harnesses.test_c_two_live.ChannelUnitTests.test_budgets_refresh_and_clamp_at_both_native_boundaries`
- `buddy.harnesses.test_c_two_live.ChannelUnitTests.test_native_connection_and_call_deadlines_keep_the_existing_taxonomy`
- `buddy.harnesses.test_c_two_live.ChannelUnitTests.test_repeated_deadlines_need_no_call_workers_or_busy_slots`
- `buddy.harnesses.test_c_two_live.EndpointAdmissionTests.test_consume_checks_deadline_even_before_rpc_waiter_marks_expiry`
- `buddy.harnesses.test_c_two_live.NativeDeadlineTests.test_already_connected_peer_expires_pre_dispatch_and_recovers`
- `buddy.harnesses.test_c_two_live.NativeDeadlineTests.test_connection_time_does_not_renew_the_owner_queue_window`
- `buddy.harnesses.test_c_two_live.NativeDeadlineTests.test_delayed_owner_never_consumes_an_expired_queue_entry`
- `buddy.harnesses.test_c_two_live.NativeDeadlineTests.test_dispatched_call_expires_but_same_connection_and_remote_call_survive`
- `buddy.harnesses.test_c_two_live.NativeDeadlineTests.test_first_connection_expires_pre_dispatch_and_recovers`
- `buddy.harnesses.test_c_two_live.NativeDeadlineTests.test_zero_connection_and_call_budgets_are_native_pre_dispatch`
- `buddy.harnesses.test_c_two_live.SubprocessLifecycleTests.test_a_stalling_endpoint_returns_within_the_native_window`
- `buddy.harnesses.test_c_two_live.WireFrameTests.test_private_deadline_is_finite_and_keeps_the_public_window_bound`
- `buddy.harnesses.test_c_two_live.WireFrameTests.test_the_request_frame_carries_the_private_deadline_and_envelope`
- `protocol.test_inquiry_transport.TransportTests.test_sdk_deadline_facts_expire_without_reporting_a_stopped_owner`

1-C补丁整合6d072f37，integration int-ee84b6fb-d68d-4191-953c-cde71213aa18 verified，原run accepted/completed；cleanup-plan/apply applied。Host保留范围缺口、实施回合的顶层脚本/manifest/所有runs原始日志、最终原补丁与固定副本后，按结构化交付唯一根 <1C_TASK_TMP> 整体回收，未按前缀扫描或清扫默认公共域。未运行60秒连接空闲泄漏探针，该项留给Claude Code Host验收。

1-D基线dcc11031，run8a6200bc-324e-4d64-b232-b4794eb1d198首次省略四字段，经决定dec-d9c89127-f9b9-4750-a119-bb10ae0805a0选ZCode/zai-api/GLM-5.3-Flash/max；供应方不可重试429/1310，两层停止确认后用户许可原run continue改用完成本批同类工作的Codex/openai/gpt-6.1-sol/high。范围仍三个新测试/fixture/记录，模型冒烟只准备，不执行。所有原清理/私有根/共享refs/日常服务约束保持，源分支不推送。

## 1-D 整合核对与公共接线修正

1-D 首份固定 d2e6f202 未验收：真实两用例检查 exit 1、7.532 秒、2 failures/2 errors。范围内的异常收尾与无限管道等待退回原 run continue，省略四项配置；最终固定 9d873dfc8fc091fe7c53f9e3dfaf0656b0899e3f、artifact bc691eec-d61f-4dcb-947d-df6ab09ab6d8、SHA 0c3b114ff35ddbc45e271802d71c7265834d018c824830b6d61b2c172c97857a，三个路径已独立核对。Host 最终私有合成源码的两个真实无模型整链用例 exit 0，测试 11.156 秒、命令 11.923 秒；三处原定变异及五处整合防护变异都由目标断言抓住。首次失败、沙箱 EPERM 和所有原始日志保留，未改写为通过。

公共整合由 Host 修改 buddy/runtime/worker.py：原先 _execute_selected 与异常路径在 release_live_binding 前删除 role-run-request.json，令回收退成 endpoint-binding-unverified。私有诊断只禁用这次提前删除，强杀用例 1/1 通过；正式修正复用既有 release/reap 与凭据清理实现，把顺序合到 _release_attempt_resources，先核验回收材料，再按原来的 confirmed_stopped 门槛清理凭据。原生启动前失败的内部重试在丢弃旧 handle 前执行同一收尾。未知停止继续保留凭据，不推断已停止；无 schema 或公开 CLI 改动。恢复旧顺序的变异由新整链用例抓住。

范围外夹具适配也由 Host 处理：Codex、Claude 与 DSH 快速调用的角色夹具提前 mkdir(state,0700)，DSH Worker 角色夹具用已有 rpc_config.configure_client 建私有域，SDK shutdown 在自己的收尾里执行。这修正了直接驱动角色时中间 state 被 parents mkdir 建成0755、以及父进程没选择控制器私有域的问题。Codex41、Claude42、DSH快速6项分别 exit0；DSH角色初次12项中问询1项失败，接上父进程私有域后12项 exit0；另一个新增 import 漏项导致12 errors的原始结果保留，修正后通过。去掉各处适配的四个变异都触发实际目标断言。未修改角色判定、权限策略或另一个运行通道。

Host 的公共接线聚焦按每文件独立解释器运行：worker_runtime4、run7、run_execution10项 exit0，worker_sessions3项在Codex夹具补齐私有state后exit0。先前误把四文件合在一个解释器导致的1 failure/1 error保留：域冻结只在合跑出现，单文件中未再出现；session失败独立复现并由夹具修正。误列不存在的test_worker模块的18项运行 exit1，其中17个实际用例通过，不算整组通过。完整检查仍等所有产物整合、合入最新core之后只跑一次，默认并行数。

无模型整链保留四个端点同根/权限、问询真实提交、正常already-absent、强杀reaped、外来peer保留、两层停止、板上shutdown事实、最终inspect全absent与自身目录无socket；SDK收尾completed。第一次失败遗留的自建服务只经其私有service_control协作停止，端点present→absent，没有信号发现的PID，没有清扫默认公共域。细节及原始证据见c-two-integration.md与本检出tmp/c073-host。四个真实harness回合仍未批准、未执行。

1-D 编号集合由实际 unittest loader 核对：3268→3270，3268个既有编号集合相等，改名/删除均0，仅新增 protocol.test_ctwo_integration.CTwoIntegrationTests.test_holder_reaps_killed_controller_and_preserves_foreign_peer 与 protocol.test_ctwo_integration.CTwoIntegrationTests.test_normal_stop_and_same_connection_after_deadline；模块203，loader错误/重复0。原始清单与差集在tmp/c073-host/integration-final-ids.json、integration-id-delta.json；四处夹具及公共Worker接线不改变编号。仓库卫生5项exit0。

1-D 首次 integration-record 返回 INTEGRATION_UNVERIFIED：Host 把补证追加进 Worker 范围内记录，目标 blob 不再等于固定交付。已把 Host 实际验证与整合证据保留在 c-two-integration-host.md，Worker 的 c-two-integration.md 从固定 9d873dfc 逐字节恢复，代码与测试不变；用新 commandId 重试登记，不把失败登记称为成功。

1-D 已以 int-b93d8ac7-0349-4438-a810-1e3ae62c7689 verified 登记并 accepted/completed。首次 acknowledge 误用 status 参数，被0.27 CLI以INVALID_ARGUMENT拒绝，没有形成签收；改用既有verdict参数的新commandId后成功。黑板已自动回收受管检出，后续cleanup-plan回读state=applied/result.removed=true，不重复删除。保留两回合顶层交付日志、固定副本与六份冒烟材料后，Host只整体删除两个结构化交付报告的确切 <1D_FIRST_TASK_TMP>/<1D_FINAL_TASK_TMP>；无通配符，无其他会话对象。

四个最短回合的入口与共享脚本已经固定SHA复制至Host自建 <HOST_TASK_TMP>/native-smoke-bundle，逐个静态prepare退出0；prepare未发现CLI、未启动模型。候选配置复用现有已验证选择：ZCode/zai-api/GLM-5.3-Flash/max，Codex/openai/gpt-6.1-sol/low，Claude/anthropic/claude-haiku-4-5-20251001/default，DSH/deepseek-official/deepseek-v4-flash/off。各回合独立私有根，execute必须绑定用户的逐次批准，当前0个真实回合、没有批准文件。脚本不覆写CODEX_HOME或CLAUDE_CONFIG_DIR；登录/额度若阻碍，只如实记录，重跑另请批准。

## 完整初跑与范围核对

最新 core 1a9decd 已在实施分支，最终候选 0dc618ed61412c97179c5b2a7c936f4c2669046d 的完整初跑命令是 uv run --frozen python -m hey_my_buddy.cli.checks，不带 --jobs。退出1、909.486秒；203份Python文件中177份通过，26份失败，通过文件的汇总2816 tests/skipped1，不能写成3270全通过。3270是此前独立loader清单的集合计数。完整日志、命令/私有根/提交绑定、逐文件错误与清单保留在tmp/c073-host/final-check.{log,result.json}、final-first-failure-inventory.json。前端源码没有改，也没有另跑前端测试。没有真实付费harness回合。

Host自己的顺序修正引入了凭据清理失败后的第二次机会丢失，已修正：端点release单独复用原实现，在原来collect后的凭据清理之前调用；凭据清理第一次仍可抛入原execute异常分支，异常分支保留停止证据并再清理一次，finally只作端点收尾，不增加第三次凭据删除。每个新handle清除live_release_attempted，原生reap只调用一次；未知native停止仍不删除凭据。14个CrashWindows故障测试exit0/31.961秒，恢复旧清理顺序与去掉异常路径第二次机会的两个变异分别触发真实目标assert失败。新增整链2项仍exit0/10.929秒。该修正由Host负责，记录先前错误，不改写首次全检。

另一个确认的范围外夹具遗漏已修正：blackboard/store/test_blackboard.py重启场景的wait_capacity直接_request补state_dir=self.directory，该实际重启用例exit0/15.171秒。全检中ZCode与Claude两个取消用例失败分别保留；空闲时独立重跑完整相关文件，ZCode25项exit0/30.499秒，Claude42项exit0/41.636秒，没有改取消测试或停止判定，也不把这些单文件成功改写成初跑成功。

余下失败涉及原唯一写入范围之外的大批夹具和调用方。本计划要求“明显超出计划则停止说明”；本次先登记可审查的补充范围，暂停扩大实施：RPC私有state传递及Mock/stub接口适配；其余原生角色/工具/用量夹具的0700状态域初始化；同一测试进程同时使用多个不同state的并发场景隔离；安装版ZCode离线app-server关闭与Host原生回合停止的独立定位；console测试catalog显式环境与host-preview开发依赖前提。每一份修改仍需独立固定交付/聚焦/去掉目标行为红灯，默认经路由，公共文件由Host整合。未证明的controller失败不能统称为状态根或机器负载，不能弱化shutdown与工具/用量断言。

用户答复“先交 Claude Code Host 复核失败记录”。固定的 26 模块失败清单、原始证据摘要、后续聚焦范围与未定位项已整理在 [c-two-failure-review.md](c-two-failure-review.md)；补充实施继续暂停，等待用户转达复核意见。本次只整理记录，未提交新的修复微任务、未重跑完整检查、未运行付费冒烟。

## Claude Code Host 复核后的补充实施

Host 在 `3cca4505` 归档副本、0.7.4 环境、私有检查根和空临时 HOME 中单独重跑 25 个模块（未跑 `test_host_workflow_worker`），22 个稳定复现。以下是用户转达的 Host 证据，尚不是本轮 Host 自行重跑所得：七份测试缺 `state_dir` 或 Mock/桩不接受参数；十余份原生角色的 0755 状态目录被本项目新增权限要求拒绝；问询对端与 console 夹具的隔离被破坏。先前未定位项目据此有了具体原因，不再用机器负载解释这些失败。ZCode、Claude 取消文件及安装版 ZCode 单独通过，初跑仍保留；`test_blackboard` 整文件 57 项由 Host 单独通过。

偏离登记：`9b73fe42` 的 `worker.py` 清理顺序修正是 Host 在整合中自己引入回归后的代码修复，超出一句话记录更正；Claude Code Host 已接受。原两处点变异及首次失败保留。隔离越界登记：初跑的 `test_inquiry_owner` 在日常 `~/.local/share/hey-my-buddy/state/ipc` 创建了 `.gate` 与 `.gate.marker`；console 子进程丢失模型目录夹具，实际执行本机 Claude 与 Codex，Host 空 HOME 复现出现 `.claude.json`、`.claude/backups` 与 `.codex/tmp/arg0`。这些是此前未声明的测试行为；日常 `ipc` 不回收、不改动，修复只验证新自建私有材料。

当前规则按用户决定收紧归属并放宽误加的父目录权限：内部角色、Worker 运行时及测试对端显式取得状态根，缺失则失败，不能解析默认状态目录；只有面向 Host 的 CLI 可以解析默认状态目录。状态目录本批不新增权限要求。`<state>/ipc` 仍以 0700 创建，已有端点目录的所有权与写入权限交给 C-Two 校验，其拒绝映射为现有 BoardError；项目保留链接与 `..` 防护。库接受 0755 父目录及符合规则的端点目录，不把“必须 0700”写成库的要求。

| 微任务 | 唯一可写范围 | 顺序与验收 |
| --- | --- | --- |
| 2-A 内部域与端点规则 | `src/hey_my_buddy/protocol/rpc_config.py`、`src/hey_my_buddy/buddy/harnesses/c_two_live.py`、`tests/python/protocol/test_rpc_config.py`、`tests/python/buddy/harnesses/test_c_two_live.py`、`tests/python/buddy/harnesses/fixtures/c_two_live_peer.py`、`tests/python/buddy/harnesses/test_inquiry_owner.py`、新 `docs/acceptance/c-two-domain-repair.md` | 最先实施；复用原生 set_local_endpoint 校验，禁止内部缺根时回退；问询 peer 显式私有根。R-01～R-04。角色与 Worker 公共接线缺口由 Host 统一整合，不改注册表或 schema。 |
| 2-B 测试子进程隔离 | `tests/python/support.py`、`tests/python/console/test_console.py`、`tests/python/cli/test_support_cleanup.py`、`tests/python/blackboard/service/fixtures/daemon_with_catalog.py`、新 `docs/acceptance/c-two-fixture-isolation.md` | 与 2-A 独立同时实施；显式传递本测试的模型目录夹具，继续清除继承的权限、运行时与凭据变量；真实程序替换为自建拒绝/记录探针。R-05、R-06。 |
| 2-C RPC 夹具适配 | 七份 `tests/python/` 文件：`cli/test_cli.py`、`cli/test_cli_views.py`、`cli/test_host_cli.py`、`blackboard/service/test_liveness.py`、`blackboard/service/test_harness_startup.py`、`buddy/runtime/test_live_lifecycle_integration.py`、`blackboard/service/test_daemon.py`；以及 `tests/python/support.py`、新 `docs/acceptance/c-two-rpc-fixtures.md` | 2-A/2-B 整合后开始；只补参数与 Mock 私有状态根，不改产品，不减少断言或既有编号。R-07。 |
| 2-D 并发域夹具 | `tests/python/buddy/harnesses/zcode/test_zcode_checkpoint.py`、新 `tests/python/buddy/harnesses/zcode/fixtures/checkpoint_domain.py`、新 `docs/acceptance/c-two-checkpoint-isolation.md` | 2-A/2-B 整合后，与 2-C 独立实施；两个状态域各有自建进程，复用现有原生模拟夹具，保留两份并发用例的断言与编号。R-08。 |

R-01：缺状态根并清除相关环境时，真实内部入口失败，空私有 HOME 的默认路径未出现；去掉拒绝或恢复默认回退应使目标断言失败。R-02：真实 SDK 接受 0755 状态目录下项目新建的 0700 ipc，不修父目录；恢复旧父目录权限拒绝应失败。R-03：端点目录权限与所有权拒绝由 SDK 实际触发并映射；链接与 `..` 仍拒绝，去掉对应保护应失败。R-04：问询 peer 的真实注册与连接同在显式私有域，空 HOME 未改；移除其根传递应失败。R-05：console 的真实私有服务取得显式模型目录夹具，不执行真实原生 CLI；去掉夹具传递应由探针或目标断言抓住。R-06：检查环境与子进程清除继承身份/凭据/运行时，但保留显式私有夹具，检查进程不解析默认状态目录；分别去掉保护确认失败。R-07：七份测试逐文件聚焦通过，既有编号集合不减少。R-08：两个进程各自使用不同私有域且问询不串线，恢复单解释器跨域应使目标断言失败。

微任务都由路由首次选择，不填四项配置；各自隔离检出，公共值、角色与注册表仍归 Host。Worker 不删除任何对象，不使用 stash，不新建、切换、移动或删除分支/标签，不提交，改动由黑板封存；短任务根在开始时记录，TMPDIR 与 BUDDY_CHECKS_TMPDIR 指入其中，材料与变异副本在根内，交付后 Host 按确切路径回收。只跑受影响测试，固定交付后 Host 独立检查与目标行为变异；范围内缺陷回原 run continue，小的记录更正由 Host 登记。日常服务失败或 Too many open files 即停止报告，不重启替换。

Host 在本检出执行 `npm --prefix apps/console ci` 准备 AGENTS 要求的开发依赖，不改前端源码与锁。2-A/2-B 收齐整合后才开始 2-C/2-D；最终合入最新 core、核对全部编号变化、保留原失败后，避开其他会话重测试高峰，以默认并行数重跑一次完整检查。疑似负载失败保留初次结果，空闲后单文件重跑再判断；四个付费冒烟仍须用户逐次批准，未批准不运行。

本轮基线 `b2741294`：2-A run `ad73f506-d3f9-49f5-85fc-7b72fcbb643c` 首次决定 `dec-09af40e4-65c8-4585-ac03-c0cbf561cf9f` 选 ZCode/zai-api/GLM-5.3/max；2-B run `7bba6511-75fe-4aa3-b042-2fd5df372bc3` 首次决定 `dec-8e478dff-b249-458e-9116-dcac7f2d6264` 选 ZCode/zai-api/GLM-5.3-Flash/max。两者供应方均不可重试 429/1310 限流，两层停止确认；按已有授权各自在原 run 上用 Codex/openai/gpt-6.1-sol/high continue，该配置已完成本批同类实现与核对。原路由、原失败、原根与请求保留，没有改用户路由设置。

Host 已执行开发依赖准备，退出 0、4.590 秒，`apps/console` 源码与锁未变；npm 输出已有 audit 警告，只保留原日志，不另做升级。原始材料 `tmp/c073-host/console-dev-ci.{json,log}`。Host 在新自建根亲自核对发布版 0.7.4：0755 状态父目录下，0700 与 0755 端点目录真实注册均成功，0770 端点目录在原生初始化/注册时被 `CoreError` 拒绝；仅 `set_local_endpoint` 配置成功不证明实际域初始化通过。所有探针根均属本任务，未使用默认公共域，材料 `sdk-directory-rule-native.{json,log}`。此次观察的机器负载约 31～37，最终完整检查等待重测试低峰，已确认的产品与隔离失败不归因于负载。

用户授权补充微任务 2-E：仅修改 `tests/python/console/test_console.py` 中新增 R-05 测试及必要局部 helper、新增 `docs/acceptance/c-two-r05-guard.md`，基线 `27b91e0d`。旧 2-B 已签收，黑板实际拒绝原 run 的 continue；新任务是用户明确批准的例外，不取消旧 run 或改写旧产物。CLI/HTTP 错误也必须核对记录拒绝诱饵，原 HTTP 500 本身不再计为有效目标变异。2-E 与 2-C 的 support、2-D 的检查点夹具范围互不重叠，其余约束沿用本计划。

| 微任务 / run | 首次路由决定 | 不可重试限流后的同 run 配置 |
| --- | --- | --- |
| 2-C / `9a357572-1b91-4e5a-81d2-095593f6fcd0` | `dec-adc545f2-4ae0-467c-87a4-eaba1b3f2222`；ZCode/zai-api/GLM-5.3-Flash/max | Codex/openai/gpt-6.1-sol/high |
| 2-D / `a4e106fd-82f7-44ca-8312-9b3a11bd320d` | `dec-3c1f508b-71c1-469d-b2f6-91a83d1ead8a`；ZCode/zai-api/GLM-5.3-Flash/max | Codex/openai/gpt-6.1-sol/high |
| 2-E / `22e5e432-44c3-439c-b885-2b3f7f23c470` | `dec-783275aa-5531-40d1-a1ba-2fc4240aeb19`；ZCode/zai-api/GLM-5.3-Flash/max | Codex/openai/gpt-6.1-sol/high |

三项首次提交均省略全部四项配置。供应方均返回不可重试 429/1310，失败回合两层停止确认后，按用户已有许可在各自原 run 上 continue，使用已完成本批同类工作的完整配置；没有改变用户路由设置。原失败与请求材料分别保留在 `<实施检出>/tmp/c073-host/repair-rpc*`、`repair-checkpoint*`、`repair-r05*` 的精确材料清单中；此处星号只说明文件名前缀，未用于清理。

公共夹具整合归属补充：2-D 提示并由 Host 实测确认，`tests/python/buddy/harnesses/zcode/test_zcode.py` 的共享 `live_channel` 需要显式传 fixture 自身状态根，不能借用检查进程的另一个根。Host 统一修改这处共享调用并补根归属断言，记录原失败、修正后的通过与去掉传递时的目标红灯；不把该文件加入并发 Worker 的写入范围。2-D 范围内的 canonical 根缺陷仍退回原 run。最终完整检查依然只在所有返修整合并合入最新 core 后运行，既有失败日志保留。

用户随后指定最终 core 为 `1f7e8b60577cc0b8883aa5dca9e9a1bc392f6ad0`，Host 已核对本地 `socu/buddy-core` 与此提交一致。它包含已由 Claude Code Host 验收的控制台与黑板修复；本线会在最终完整检查之前合入实施分支并绑定合并后的实际提交，不使用此前 `1a9decd9` 的基线替代。本线没有改前端源码，不重复已验收的前端套件。
