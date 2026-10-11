# C-Two 0.7.3 微任务 1-A：夹具同根整改与验证边界

固定目标基线为 `861f298e6f7b585a96800534014e5ba1eb914491`。首个产物 `261370ab-5755-4655-ac4a-6364ec26e5fc` / `73d326d0` 因错误收紧 state 权限及缺少锁被 Host 拒绝；上一整改产物 `5a1a2b86-ee53-481b-92eb-7d00f4a501f8` / `6853febb` 的只读 state 与锁经 Host 核对，但其真实 48 项聚焦运行因 daemon 夹具状态根接错失败，再次退回。本回合沿用同 run，仅修该夹具及本记录；不把历史通过结果或本回合单元测试视为整项验收。本回合不提交、不操作 stash/分支/标签、不安装日常 runtime、不调用模型、不重复已知受沙箱限制的原生操作。临时材料位于本回合开始创建的 `<task-tmp>`，确切路径在结构化 summary 提供，留给 Host 回收；旧任务目录也保留。

## 固定修改范围与行为

累积修改 `pyproject.toml`、`uv.lock`、`src/hey_my_buddy/protocol/{rpc_config,transport,client}.py`、`src/hey_my_buddy/blackboard/service/daemon.py`、`src/hey_my_buddy/buddy/runtime/worker.py`、`src/hey_my_buddy/cli/checks.py`、`tests/python/support.py`、`tests/python/protocol/{test_rpc_config,test_ctwo_service,test_transport_attach}.py`、`tests/python/blackboard/tasks/test_workspace_api.py`、`tests/python/cli/test_checks_cleanup.py` 和本记录，共 15 份文件，均在 writeScope 内。本回合相对 `6853febb` 仅修改 `tests/python/protocol/test_rpc_config.py` 和本记录，生产代码及锁均未再改。上一整改相对 `73d326d0` 的 6 文件变动保留。`packaging/runtime-assets.json` 已包含整个 `src/hey_my_buddy`、`pyproject.toml` 和 `uv.lock`，没有新增公共模块或漏列资源，清单无需修改；`test_checks_parallel.py` 未改。

版本与契约仍为 0.29.0，schema 仍为 15。未改公共 contracts、live 模块、注册表、参考文档、ADR、AGENTS、README、待办或 console。控制器、runtime live、roles live 及相应 live 测试的显式根和端点凭据接线仍由 1-B 完成，未接线行为不描述为已验证；1-C 的暂停不扩大本步权限或修改范围。

`rpc_config.py` 在同文件提供 `configure_local_endpoint(state_dir=None, *, create=True)`、`configure_server(state_dir=None, *, create=True)` 和 `configure_client(state_dir=None, *, create=True)`。默认沿用 `BUDDY_STATE_DIR` / 项目状态根选择。Unix 首次原生 I/O 前显式选择 `<state>/ipc`，忽略继承 `C2_IPC_ROOT`；Windows 不传 root。复用 `private_dirs` 的路径规范化和链接组件保护，检查祖先所有权和可写性，拒绝链接、非目录、其他所有者及不安全权限，创建后仍重新检查，绝不 chmod 修复已有条目。

上一整改恢复 state 原有 owner-private 规则：属于当前用户且无 group/other 权限，允许合法只读的 0500 state。严格的 exact 0700 只适用于 `<state>/ipc`。`transport._private_directory` 也恢复原权限判定，保留新加的链接祖先防护。`create=False` 要求安全 state 和 IPC 都已存在；缺失、0750 或 0500 的 IPC 拒绝使用，不 mkdir、不 chmod、不发起连接。完整原生 shutdown 后的新根由 C-Two 自身支持，未增加生产测试专用生命周期。

RPC 入口、只读健康探测和 `BoardClient(autostart=False)` 显式传递状态根；daemon 在状态写入前配置其显式根，Worker 也传入构造参数的根。关闭 buddy 池只使用 `pool_enabled=False`，删除池段大小、段数和池容量报告，保留消息、重组、等待及控制并发限制，`sharedMemoryDisabled=False`。关闭 buddy 池仍可临时使用 SHM，配置容量与 C-Two 内存计数都不代表 RSS。

共享 support 为直接 `cc.connect` 提供私有连接夹具，真实 RPC 子进程在首次通信前配置同根；夹具结束自己的通信并确认原生 shutdown completed 后再回收或选择下一根。运行器停止 RPC 显式传根，并在处理下一根前结束自身会话。support 和运行器清除继承的 `BUDDY_*`、`ANTHROPIC_*`、`C2_*`、虚拟环境及 uv 项目环境，再设置私有测试配置。原生变异探针的 C2 冲突对照也是夹具自己的 0700 目录，删除代码根覆盖时不会落入默认公共域。

运行器当前源码没有公共 C-Two 目录清扫或 `sweep_endpoints`/旧 IPC 残留删除逻辑，无需删除，也未新增。它只保留与自己的私有根绑定的进程和生命周期锁观察；指定 `BUDDY_CHECKS_TMPDIR` 时运行器根与冷启动探针都留在其中。

## 本回合夹具整改与聚焦验证

Host 继续指令报告：在上一固定输出的真实 48 项聚焦运行中，`RealDaemonControlTests` 的健康等待失败；独立读取实际子进程环境证明 requested 为 `<work>/state` 而 actual 为 `<work>`。原因是 `_start_daemon` 把 `work` 传给 `_child_environment`，试图用 overrides 指向 `state`，但共享 helper 最后会把 `BUDDY_STATE_DIR` 固定回其目录参数。Host 还报告已只在其失败夹具的确切私有路径写入 cooperative stop 请求并确认两个遗留 supervisor 消失。原始 48 项失败保留，不改写成通过；本 run 没有重跑该真实运行或独立确认 Host 的停止观察。

修正 `_start_daemon` 调用为 `private_environment(self.state, ...)`，去掉无效状态根 override；共享 helper 的防越界行为完全不变。新增测试读取真实传给 mocked `Popen` 的 env，断言 daemon 的 state、runtime、native fallback 和健康检查的根，并用该实际 env 根核对服务与 Worker 清理参数。另一个断言确认任意状态根 override 仍被忽略，跨 state 的 runtime 根仍拒绝。未调整等待期限、并发窗口、原控制响应或成功期望。

夹具清理先提交仅本测试成功 claim 的 external 模拟尝试结果：这些 probe 从未启动 harness 或子进程，等待线程退出后才结束 claim；已在控制窗口内提交的 renewable claim 从集合移除，不重复交付，不结束其他尝试。这使 daemon 的 cooperative stop 能对本测试的模拟工作正常 drain。随后复用 `stop_private_service(state)`、`stop_private_workers(state)` 的私有生命周期锁确认，等待自己持有的 daemon 子进程退出，并确认自己的 RPC shutdown 完成，最后由夹具 `shutil.rmtree(work)` 回收。移除 terminate/kill 以及 `ignore_errors=True`；任何 receipt、服务、Worker、子进程或 RPC 确认失败均阻止目录回收，删除错误向上传播。未扫描或清理其他会话及默认 C-Two 域。

本回合只复制上一任务自有测试依赖环境和 sanitized runner 至新任务根；复制环境仍不等于冻结同步。所有测试经 `python3 <task-tmp>/run.py <probe-python> -m unittest -v protocol.test_rpc_config.DaemonFixtureTests` 执行，清除继承的 `BUDDY_*`、`ANTHROPIC_*`、`C2_*`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`，再设私有 state/runtime 和任务内 TMPDIR/BUDDY_CHECKS_TMPDIR。Popen、RPC 和停止操作在这 7 个单元用例中均被 mock；没有真实 IPC、服务或 Worker 被启动。临时夹具的自建自清理是测试收尾，任务根本身留给 Host 回收。

| 聚焦命令 / 材料 | 退出码与实际结果 | 证据边界 |
| --- | --- | --- |
| 修正前运行新增的环境根与 helper 边界两个用例 | 1；2 项中 1 项断言失败 | `fixture-red.log`：实际 env 根为 work，期望为 work/state；不是库错误或等待超时 |
| 修正后运行 `DaemonFixtureTests` | 0；7 项，0.007 秒 | `fixture-green.log`；单元验证 |
| 最终同一聚焦命令 | 0；7 项，0.008 秒 | `fixture-final.log`、`fixture-result.json`；补上实际 env 根与清理参数一致的断言后重跑 |
| AST 编号、语法、固定 HEAD 与原交付锁 hash 核对 | 0 | `fixture-test-ids.json`；当前 RPC 文件 20 项，新增 7 项，原 13 个编号全部保留，无删除或改名 |

7 个新增编号均在 `DaemonFixtureTests`：`test_daemon_environment_health_and_cleanup_select_the_same_state`、`test_environment_helper_keeps_its_state_and_runtime_boundary`、`test_cleanup_confirms_only_its_service_workers_and_child_before_removal`、`test_unconfirmed_cleanup_preserves_the_fixture`、`test_cleanup_does_not_hide_directory_removal_errors`、`test_cleanup_releases_only_its_synthetic_claims_before_service_stop`、`test_uncommitted_probe_receipt_preserves_claim_and_state`。清理单元断言覆盖同根、顺序、服务/Worker/子进程/RPC 未确认时保留目录、删除错误传播和仅本夹具 claim 的结果归属。真实 daemon 控制用例编号、等待时长和并发成功断言不变，仍需 Host 按本次固定输出复跑真实聚焦门槛及两条真实变异，确认 cooperative shutdown 后没有自己的残留。

以下章节保留上一整改回合及更早证据的来源和边界；其中的命令、材料与计数属于对应旧任务根，没有在本回合重新运行。原生 V-03～V-06、冻结同步、打包及私有安装门槛保持；1-C 继续暂停，本步不接 live。

## 上一整改回合的锁来源与实际核对

Host 的当前继续指令提供公共解析参考 `<host-tmp>/1a-lock-reference/uv.lock`，说明它来自固定产物临时副本中的真实 `uv lock`，尚未应用到实施分支。本回合先复制参考到自建任务根，解析其 TOML 并与固定基线锁及当前 `pyproject.toml` 比较，再由本 run 将参考逐字节整合到检出的 `uv.lock`；没有要求 Host 代改交付文件，也未手写 URL 或 hash。

参考及交付锁 SHA256 均为 `e412457722ef7e050204f30e897568bc3697a1935806438676f17ddf8a8baa3c`。实际断言验证：C-Two 从 0.6.0 改为 0.7.3；项目 requires-dist 中 C-Two 改为 `==0.7.3`，与当前 pyproject 的完整依赖声明一致；其余 10 个包的完整记录保持不变，锁顶层字段、C-Two 依赖关系及 registry 来源不变；C-Two 的 sdist/wheel URL 均命名 0.7.3 且 hash 字段格式有效。没有下载发布物复核这些 artifact hash。`<task-tmp>/lock-verification.json` 保存核对结果，`uv lock --check --offline` 在本检出退出 0，解析 12 个包。

本回合测试环境是复制前回合自有缓存发布依赖环境至新任务根的 CPython 3.13.3 环境。本回合重新核对 sys.prefix 位于 `<task-tmp>/probe-venv`，并逐项读回当前平台 10 个依赖的已安装版本，与本次交付锁一致，包括 C-Two 0.7.3；Windows 专用 pywin32 没有安装或验证。`environment-verification.json` 保存结果。这证明本次单元测试使用的版本与锁一致，不等于 `uv sync --frozen`：沙箱内没有完成冻结同步或私有安装，仍需 Host 补该证据，不触及日常 runtime。

## 上一整改回合聚焦验证

所有命令经 `python3 <task-tmp>/run.py <command>` 执行；包装清除上述继承变量并设置私有 state/runtime、`TMPDIR`、`BUDDY_CHECKS_TMPDIR`、uv cache 和项目环境，禁写 bytecode。下表 `<probe-python>` 为本回合任务根内的解释器。

| 命令 / 材料 | 退出码与实际结果 | 证据边界 |
| --- | --- | --- |
| 锁结构、参考字节一致性与 pyproject 核对脚本 | 0 | `lock-verification.json`；未下载验证 wheel/sdist hash |
| `uv lock --check --offline` | 0；12 个包 | `lock-check.log`；锁一致性，不是冻结安装 |
| 当前平台环境版本与 prefix 核对脚本 | 0；10 个依赖与锁相符 | `environment-verification.json`；复制环境，没有运行 sync |
| 修生产代码前运行恢复的成功用例和新增实际配置入口用例 | 1；2 项报错 | `readonly-red.log`；失败分别是项目代码 `PRIVATE_PATH_UNSAFE`/`LAUNCH_ACCESS_DENIED` 与 `SERVICE_UNAVAILABLE`，证明原有只读成功行为被错误拒绝；没有原生 C-Two 通信或库错误，不能替代 V-05 的真实变异 |
| 修正后 `<probe-python> -m unittest -v protocol.test_transport_attach` | 0；27 项，0.933 秒 | `readonly-green.log`；完整 transport 单元文件 |
| 下列最终聚焦命令 | 0；33 项，0.556 秒 | `unit-final.log`；仅只读/配置单元验证 |

最终聚焦命令：

```sh
<probe-python> -m unittest -v \
  protocol.test_transport_attach \
  protocol.test_rpc_config.ProfileTests.test_profile_disables_pool_and_preserves_non_pool_limits \
  protocol.test_rpc_config.ProfileTests.test_configure_is_idempotent_and_writes_no_environment \
  protocol.test_rpc_config.ProfileTests.test_windows_never_passes_root_override \
  protocol.test_rpc_config.ProfileTests.test_bad_state_or_ipc_is_refused_without_repair \
  protocol.test_rpc_config.ProfileTests.test_read_only_setup_does_not_create_or_chmod_missing_directories \
  protocol.test_rpc_config.ProfileTests.test_wait_admission_cannot_consume_every_native_callback
```

恢复的 `ReadOnlyAttachTests.test_attach_succeeds_when_the_directory_cannot_be_written` 保留原来的不可写断言、成功返回和一次请求断言，并增加 mkdir/chmod/spawn/写入检查、内容快照和 state 保持 0500 的断言。新增 `RpcReadOnlySetupTests.test_read_only_state_with_private_ipc_reaches_rpc_without_mutation` 使用真实 state/endpoint 安全检查、真实 `configure_client(create=False)` 与 BoardClient 入口，只替换 `cc.connect`，断言健康探测及请求两次到达 RPC、state0500/ipc0700 保持不变且没有文件系统修改。RPC 配置的坏目录测试增加 IPC0500 拒绝且不改权限的断言；缺失 IPC、IPC0750、坏 state、其他所有者、链接和 Windows 无 root 的既有断言都保留。

本回合没有运行需要实际 connect 的活跃域测试、真实服务、RPC 探针、变异、ps 清理观察、wheel/sdist 构建、私有安装、完整检查或模型。Host 当前指令报告其已用发布 0.7.3 在 state0500/ipc0700 注册并完成一次真实调用；这属于 Host 提供的事实，本回合没有冒称亲自复测。

## 更早证据与待验收边界

前回合原始日志已复制至 `<task-tmp>/previous/`：`rpc-first.log` 为真实发布包 RPC 配置/探针 12 项、退出 1，5 个 register 因沙箱 `Operation not permitted (os error 1)` 失败；`focused-first.log` 为首次 69 项、退出 1，其中 4 项 socket mock 设置问题后续已修，另 4 项 ps 观察因沙箱拒绝失败；`focused-final2.log` 为前产物 63 项、退出 0；`workspace-inprocess.log` 为两个进程内 workspace API 用例、退出 0。前产物被拒绝，这些历史结果不构成当前修改后的通过声明。本回合按 Host 指令只跑上述受影响单元测试，未重跑已知权限失败的原生操作。

V-03 的合法 4 KiB/8 MiB 往返、库超限失败、大消息并发以及真实 daemon wait 槽满时 renew/result/cancel/health 不饥饿，仍由 Host 在真实依赖与私有 IPC 环境核验。V-04 的本回合证据为目录/只读/显式客户端根和 Windows mock；硬件 Windows、真实同根通信、长路径、活跃根及 shutdown 换域本回合未运行。Host 计划中已记录的 outgoing SHM used=0、peak=8392704 是发布版探针事实，不作为本回合的亲测数字。

V-05 的两条真实变异目标仍为 `protocol.test_rpc_config.IsolatedTransportTests.test_private_root_overrides_inherited_namespace`（只删除代码 root override）和 `protocol.test_rpc_config.IsolatedTransportTests.test_disabled_pool_releases_outgoing_shm_after_real_rpc`（只删除关闭池条目）。前回合两份副本保留在 `<previous-task-tmp>/mutation-no-root`、`mutation-pool-default`，但它们包含被拒绝的旧 state 判定；Host 应从本次固定输出另建副本，不把旧副本当作最新来源。本 run 没有运行真实变异，无变异退出码；需要先取得真实基线退出 0，再取得目标断言失败、退出 1 和完整日志，不能用同一权限/库错误代替。

V-06 的锁已由本 run 交付并通过一致性检查，资源清单覆盖已核对；按锁的实际冻结同步、私有构建/安装证据仍待 Host 补齐。其余服务、workspace API 的真实 RPC/HTTP 和受限清理观察聚焦门槛保持，不因本回合单元通过而移除。

## 上一整改回合测试编号变动

`<task-tmp>/baseline-test-ids.json`、`current-test-ids.json` 和 `test-id-changes.json` 保存范围内六份测试文件的 AST 编号集合，同时比较固定目标基线及被拒绝产物。相对被拒绝产物，本回合删除错误的 `ReadOnlyAttachTests.test_private_non_0700_directory_is_refused_without_mutation`，恢复基线已有的 `test_attach_succeeds_when_the_directory_cannot_be_written`，并新增 `RpcReadOnlySetupTests.test_read_only_state_with_private_ipc_reaches_rpc_without_mutation`；没有改名规避原成功断言。RPC 坏目录测试仅增加断言，不改编号。

相对固定基线，transport 原 22 个编号全部保留，新增 `RpcReadOnlySetupTests` 的 5 个编号，共 27 项；RPC 共 13 项。RPC 原编号保留合法/超限、wait admission 与真实 daemon 控制并发；原 profile/off-switch、一次配置、两池段并发的三个用例分别改名为 `test_profile_disables_pool_and_preserves_non_pool_limits`、`test_configure_is_idempotent_and_writes_no_environment`、`test_concurrent_large_transfers_complete_without_pool`。删除依赖旧池映射/RSS数字、错误的关闭池结论、池设置混配和池段 chunk 阈值的四个用例：`test_configured_capacity_mapping_and_rss_are_separate_measurements`、`test_pool_enabled_false_still_maps_shared_memory`、`test_a_client_with_other_overrides_can_still_reach_the_profile_server`、`test_chunked_fallback_uses_the_production_reassembly_bounds`，新增 public memory_stats、私有根覆盖/隔离以及目录/Windows/只读/原生根冻结保护；逐项集合见 JSON。

服务与 workspace API 原各 4 个编号全部保留，只改私有连接接线。checks cleanup 原 12 个编号保留，新增显式停止根和结束 RPC 会话的编号，共 13 项；原环境测试扩展前缀清理断言。checks parallel 原 14 个编号及文件不变。没有将未运行的整文件写为验收通过。
