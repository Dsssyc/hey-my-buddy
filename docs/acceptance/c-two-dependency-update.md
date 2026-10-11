# 1-A2 发布依赖更新与版本证据

固定目标基线仍为 `111f885cafa9b0803145e67b5e3b675f4bb79ac6`，前次固定输出为 `5a8459d6a1a68bdf5357c4fd17f199cac5d2c98c` / artifact `30cad445-b2b7-43fd-9041-13416de8989a`。本次原 run continue 已完成授权依赖/锁和版本说明更新、可执行的验证及私有构建安装；真实 socket/ps 检查受沙箱限制，交付 attention 待 Host 从固定产物独立验收。下方“前回合原始记录”保持原文，是历史证据，不能当作本次当前状态。

## 本次产物与锁来源

累计实际改动恰为五条授权路径：`pyproject.toml`、`uv.lock`、`src/hey_my_buddy/protocol/rpc_config.py`、`tests/python/protocol/test_rpc_config.py` 和本记录。前三个源/测试文件相对目标基线仍仅为 0.7.3→0.7.4 的四处替换，保留 20 个 RPC 测试编号、机制、限制、期限和断言分量。公共/live/角色/注册表及 ADR、CONTEXT、AGENTS、README、待办、参考文档未改；无新增资源，runtime-assets 原有目录及 pyproject/lock 条目继续覆盖。所有 0.7.3 历史、原 48 项失败和两次返修保持原文件和版本归属。

Host assistance 提供的 `uv.lock`、wheelhouse、requirements 与 provenance 来自前次固定输出的隔离副本公开 PyPI 解析；其 exit0 和下载事实是 Host 提供的来源说明。本 run 复制材料至新 `<task-tmp>/materials/`，逐一计算 17 份材料的 SHA256/大小并比对 provenance，核对 16 个 wheel 中当前平台 10 个运行依赖的哈希均在新锁；真实 installed metadata 也逐项读回。没有借用 Host 或日常依赖环境。

本 run 将核对后的参考字节写入授权 `uv.lock`，锁 SHA256 为 `5ae675829eb4c9aca5e2c933b86799abd3ab9a979e80f8d00c074adf5e27cdf8`。新旧锁均 12 包；仅 C-Two 的 version/sdist/wheels 和项目 metadata requires-dist 的 `==0.7.4` 改变，另外 10 个包完整记录、锁顶层/平台 markers、registry 和依赖关系逐项相同。版本及契约仍 0.29.0，schema 未改。

## 本次环境与真实验证

本回合创建新的短 `<task-tmp>`，确切路径在 outcome 提供，旧任务根及原失败材料保留。新根中包含私有 state/runtime/native fallback、uv cache、两个 CPython 3.13.3 环境、Host 材料副本、基线副本、源码快照、变异副本、构建产物及全部 `.log`/`.json`。命令包装先清除继承的 `BUDDY_*`、`ANTHROPIC_*`、`C2_*`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`，再仅设置任务自有配置；TMPDIR/BUDDY_CHECKS_TMPDIR 指入新根，uv offline/no-index 与本地 wheelhouse 固定，模型事实离线且 harness CLI 为不可用哨兵。没有读取凭据内容、操作默认公共域、修改日常 runtime/配置/登录/凭据或管理日常服务/Worker；没有 Too many open files。Worker 没有删除文件/目录，原测试夹具的收尾按原逻辑执行。

最初冻结离线 sync 因注册表 URL 缓存缺失退出1；`uv pip install --require-hashes` 成功安装 10 个锁定依赖后，editable sync 因 Host wheelhouse 未含 `editables` 再退出1。两次失败保留，不变更项目或构建依赖声明。采用 uv 原生 `--no-editable` 路径后，`uv sync --frozen --no-editable --offline --no-index --find-links <wheelhouse>` 退出0，实际从交付锁完成非 editable 项目安装；测试通过 `uv run --no-sync` 使用该任务环境和检出源码。未声称默认 editable sync 已通过。

以下每条命令均实际经 `python3 <task-tmp>/run.py <label> <command>` 执行，同名 `.json` 包含精确 command/exitCode/seconds，完整日志同名 `.log`。包装秒数与 unittest 自报测试秒数分别记录。

| label / 完整实际命令 | 退出码 / 包装秒数 | 实际结果与边界 |
| --- | --- | --- |
| `materials`：`python3 <task-tmp>/verify_materials.py` | 0 / 0.247 | 17 份材料哈希/大小匹配；12 包锁只有两条目标记录变化 |
| `sync`：`uv sync --frozen --offline --no-index --find-links <task-tmp>/materials/wheelhouse` | 1 / 0.206 | 锁中 URL 未在新缓存；保留首次失败 |
| `install-locked`：`uv pip install --python <task-tmp>/venv/bin/python --offline --no-index --find-links <task-tmp>/materials/wheelhouse --require-hashes -r <task-tmp>/materials/locked-dependencies.txt` | 0 / 0.438 | 按 Host requirements 的 hashes 安装当前平台 10 包 |
| `sync-seeded`：`uv sync --frozen --offline --no-index --find-links <task-tmp>/materials/wheelhouse` | 1 / 0.218 | 依赖已安装；editable 构建缺 editables，保留失败 |
| `sync-wheel`：`uv sync --frozen --no-editable --offline --no-index --find-links <task-tmp>/materials/wheelhouse` | 0 / 0.918 | 非 editable 冻结同步成功；未改构建声明 |
| `api`：`uv run --no-sync python <task-tmp>/api_verify.py` | 1 / 0.165 | 任务探针错误地比较 /tmp 与 /private/tmp 别名；保留失败 |
| `api-canonical`：`uv run --no-sync python <task-tmp>/api_verify.py` | 0 / 0.122 | 修正探针的预期根为规范路径；版本、公开签名/名字/异常与配置键核对成功 |
| `profile-fixture`：`uv run --no-sync python -m unittest -v protocol.test_rpc_config.ProfileTests protocol.test_rpc_config.DaemonFixtureTests` | 0 / 4.351 | 14 项，0.152 秒，退出 0 |
| `native-root`：`uv run --no-sync python -m unittest -v protocol.test_rpc_config.IsolatedTransportTests.test_private_root_overrides_inherited_namespace` | 1 / 1.547 | 1 项，注册被沙箱拒绝，未到达目标 root 断言 |
| `transport`：`uv run --no-sync python -m unittest -v protocol.test_transport_attach` | 0 / 1.301 | 27 项，0.911 秒，退出 0 |
| `parallel`：`uv run --no-sync python -m unittest -v cli.test_checks_parallel` | 0 / 6.668 | 14 项，6.500 秒，退出 0；仅加载全套编号，没有运行完整套件 |
| `cleanup-observation`：`uv run --no-sync python -m unittest -v cli.test_checks_cleanup.PrivateRootTeardownTests.test_teardown_removes_a_clean_private_root` | 1 / 0.119 | 1 项，ps 不可观察；原 evidence 根保留，不代称 clean |
| `cleanup-unit`：`uv run --no-sync python -m unittest -v cli.test_checks_cleanup.ChecksEnvironmentTests cli.test_checks_cleanup.ResidueObservationTests cli.test_checks_cleanup.DeletedOpenFileObservationTests` | 0 / 0.458 | 6 项，0.336 秒，退出 0；不含实际 ps 观察 |
| `workspace-unit`：`uv run --no-sync python -m unittest -v blackboard.tasks.test_workspace_api.WorkspaceApiTests.test_five_named_surfaces_and_worker_authority blackboard.tasks.test_workspace_api.WorkspaceApiTests.test_cli_integration_acceptance_and_cleanup_preserve_private_control` | 0 / 8.72 | 2 项，7.704 秒，退出 0；进程内实际 SQLite/Git/CLI 路径 |
| `packaging-runtime`：`uv run --no-sync python -m unittest -v install.test_packaging.SingleEntrypointTests install.test_packaging.CheckHarnessTests install.test_packaging.SkillBuildTests install.test_runtime` | 0 / 6.354 | 42 项，6.220 秒，退出 0；不含两个真实 launcher 用例 |
| `build`：`uv build --offline --no-index --find-links <task-tmp>/materials/wheelhouse --out-dir <task-tmp>/dist` | 0 / 1.774 | 真实 wheel/sdist 构建成功 |
| `export`：`uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file <task-tmp>/locked-dependencies.txt` | 0 / 0.017 | 由本 run 从交付锁导出 requirements/hashes |
| `wheel-venv`：`uv venv --python 3.13 <task-tmp>/wheel-env` | 0 / 0.077 | 创建全新任务私有 CPython 3.13 环境 |
| `wheel-deps`：`uv pip install --python <task-tmp>/wheel-env/bin/python --offline --no-index --find-links <task-tmp>/materials/wheelhouse --require-hashes -r <task-tmp>/locked-dependencies.txt` | 0 / 0.107 | 本 run export 的 hashes 安装 10 包 |
| `wheel-install`：`uv pip install --python <task-tmp>/wheel-env/bin/python --offline --no-index --no-deps <task-tmp>/dist/hey_my_buddy-0.29.0-py3-none-any.whl` | 0 / 0.066 | 安装本 run 构建的 wheel；不含日常 runtime |
| `wheel-verify`：`<task-tmp>/wheel-env/bin/python -I <task-tmp>/wheel_verify.py` | 0 / 0.805 | -I 隔离读回安装路径、11 包版本、metadata 与内置锁一致 |
| `snapshot`：`python3 <task-tmp>/snapshot.py` | 0 / 0.728 | 基线 archive 加授权五文件建立副本；未操作共享 refs |
| `mutation-daemon`：`uv run --no-sync python -m unittest -v protocol.test_rpc_config.DaemonFixtureTests.test_daemon_environment_health_and_cleanup_select_the_same_state` | 1 / 0.867 | 目标断言失败 exit1：实际 Popen env 根为 work，期望 work/state；不是库异常 |

本回合正常通过的测试合计 105 项：1-A 范围的配置/夹具、transport、parallel、cleanup 单元和两个 workspace API 共 63 项，打包/runtime 42 项；不是原 82+44 项整批通过。真实 root 基线及 cleanup 观察各1项失败，其他已知同类 socket/ps 用例未重复执行。C-Two service 整文件4项、RPC 其余真实负载/并发/daemon 等5项、workspace 实际 RPC/HTTP2项、cleanup 其余6项与打包两个真实 launcher 用例保留给 Host 重取。已有测试中的 fake_uv、mock Popen/连接保持原样，本 run 没有造导入桩或把单元替代描述为真实通信。

公开接口核对实际来自任务内发布 wheel 的导入、`inspect.signature`、异常 MRO 和配置接受：读回 C-Two 0.7.4、`cc.connect(..., timeout: float | None = None)`，11 个所需 public 名字存在；`CallDeadlineExceeded` 继承 `CCError`、`CCBaseError`、Exception；server/client override 键分别9/5个，配置和 public root 规范路径读回成功，shutdown completed。首次自建 API 脚本把 macOS `/tmp` 和 `/private/tmp` 别名作字面比较而失败，修正探针预期路径为项目既有规范路径后 exit0，原失败日志保留，项目源码未改。本回合未安装0.7.3作双版本比较，不声称除 timeout 外所有内部行为相同，不对 vendor 做静态证明。

真实 `test_private_root_overrides_inherited_namespace` 的服务注册在 c_two 原生层报 `Operation not permitted (os error 1)`，尚未到达 public root 断言；不算 V-03/V-04/V-05 证据，未再启动同类服务、daemon/Worker、RPC/HTTP 或两条原生变异。`test_teardown_removes_a_clean_private_root` 因 `ps process enumeration failed; residue status is unknown` 失败，运行器保留自己的 evidence 根，确切子路径见 outcome；未知不说已 clean，未重试实际 ps 观察或清扫。失败的端点子进程已以错误退出；没有本回合成功启动的真实服务/Worker。

## 变异、打包安装与 Host 边界

任务根中的 `snapshot/` 来自固定目标基线 archive 加授权五文件，`mutation-daemon/` 是其独立副本。只将 daemon 夹具 `_start_daemon` 的 `private_environment(self.state, ...)` 改回 `self.work`，目标 `DaemonFixtureTests.test_daemon_environment_health_and_cleanup_select_the_same_state` exit1，失败在实际传给 mocked Popen 的 env 根断言：work != work/state；生产源码/共享检出/refs 未变。正常目标包含在此前14项 exit0 中；此变异用真实依赖与真实 helper，未启动 child，不能冒称实际 daemon 运行。root setter、pool_enabled 两条变异因正常原生基线被沙箱拒绝未执行，不能用 CoreError 代替目标 assert。

本 run `uv build` 从当前源码构建 wheel 和 sdist，exit0；wheel SHA256 为 `2154fef578cb69d6aee04903c4a8fdc535e29aa7bdd8af870870790754a1cf8b`，sdist 为 `cec64f66fda745195032a13fed43f59126a9c1c2c63d4251a780cc24dbe35dff`。本 run 自行从交付锁 `uv export --frozen`，在另一全新任务私有 wheel-env 中按 hashes 安装10个依赖，再安装本次 wheel。`-I` 隔离读回 package 在该环境的 site-packages、C-Two0.7.4/项目0.29.0及其他锁版本一致；wheel metadata requires-dist 为 c-two==0.7.4，wheel 内 `hey_my_buddy/_distribution/package/uv.lock` 和 sdist 根 uv.lock 均逐字节等于交付锁。这是本 run 新取得的 V-06 私有构建/安装证据，没有安装或升级日常 runtime；Windows 包和硬件未验证。

Host 应从最终固定产物独立运行1-A六模块完整82项、打包/runtime完整44项，重取 V-03/V-04 的真实4 KiB/8 MiB、outgoing SHM used=0、长路径/权限/隔离/首次同根及停止观察，并完成三处指定目标断言变异。默认 editable 安装需要正常构建依赖 `editables`，由 Host 私有验收环境提供即可，不要求项目扩大写范围或依赖声明。本次没有新增空闲60秒泄漏测试、完整检查、模型调用或真实四 harness 冒烟；Host 后续私有验收边界保持。最终输出补丁及逐文件哈希保存在新任务根的 `output.patch`、`output-manifest.json`，尚未由 Host 验收，不自称整项已通过。

## 前回合原始记录（历史，原文保留）

目标为已发布的 C-Two 0.7.4，固定基线为 `111f885cafa9b0803145e67b5e3b675f4bb79ac6`。本回合交付部分改动与依赖准备缺口，尚未完成依赖升级或验收；应在原 run continue，不取消另开。已读 `c-two-073.md` 当前计划和 `c-two-073-1a.md` 历史；0.7.3 原 48 项失败、两次返修及最终 Host 验收均保持原文件、原版本和原归属。

## 固定范围与当前产物

唯一可写范围为 `pyproject.toml`、`uv.lock`、`src/hey_my_buddy/protocol/rpc_config.py`、`tests/python/protocol/test_rpc_config.py` 和本记录。当前实际改动为其中四个文件：pyproject pin 改为 `c-two==0.7.4`，RPC 配置和测试模块说明改为 0.7.4，真实合法负载往返的版本断言改为 0.7.4；其余源/测试字节与固定基线相同。RPC 文件的 20 个测试编号全部保留，机制、限制、内存断言、等待期限和清理逻辑未改。`uv.lock` 因解析受阻仍逐字节等于基线，锁内 C-Two 仍为 0.7.3，与新 pin 不一致，不能按冻结锁交付为升级完成。锁 SHA256 为 `e412457722ef7e050204f30e897568bc3697a1935806438676f17ddf8a8baa3c`。

现有 `packaging/runtime-assets.json` 已覆盖整个 `src/hey_my_buddy`、`pyproject.toml` 和 `uv.lock`，静态覆盖断言成功；没有新增资源，未改清单。未改 live、角色、注册表、ADR、CONTEXT、AGENTS、README、待办或参考文档；未提交、操作 stash/分支/标签、调用模型、执行真实 harness 冒烟或完整检查。

## 本回合真实命令与边界

本回合开始创建 `<task-tmp>`，确切路径在结构化 outcome 提供，所有包装脚本、日志、uv cache、项目环境和测试临时材料均在此根下，保留给 Host 按确切路径回收。`run.py` 在每条子命令前清除继承的 `BUDDY_*`、`ANTHROPIC_*`、`C2_*`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`，再设置任务自有 state/runtime/native fallback、TMPDIR、BUDDY_CHECKS_TMPDIR、uv cache/project environment、离线模型事实夹具和不可用 harness 哨兵。没有读取凭据内容、清扫默认公共域、安装日常运行时或管理日常服务/Worker。Worker 未删除文件或目录；测试夹具自身收尾按原测试执行。

命令均从检出根经 `python3 <task-tmp>/run.py <label> <command>` 调用；每条有同名 `.log` 和记录 command、exitCode、seconds 的 `.json`。由于检出无 `.venv`、系统解释器无项目依赖，下面的系统 Python 批次仅记录加载/依赖失败，不能当作发布版实测。

| label / 实际命令 | 退出码、用时 | 实际观察 |
| --- | --- | --- |
| `lock`：`uv lock --upgrade-package c-two` | 2；0.196 秒 | CPython 3.13.3；PyPI zstandard 索引 DNS 解析失败；未生成新锁；HTTP retries=0 |
| `dependency-import`：`python3 -c 'import sys, c_two; print(sys.executable); print(c_two.__version__)'` | 1；0.052 秒 | `ModuleNotFoundError: c_two`；不是 0.7.4 接口核对 |
| `focused-unavailable`：下列八模块批次 | 1；0.536 秒 | unittest 报 Ran 15、failures=2、errors=14；七个模块加载失败，打包类 setup/cleanup 亦失败；两个静态入口检查成功，不构成受影响整文件通过 |
| `static`：`python3 <task-tmp>/static_verify.py` | 0；0.443 秒 | 固定 HEAD、三文件仅版本替换、语法/20 编号保留、锁未变和资源覆盖断言成功；详情在 `static-verification.json` |
| `final-static`：同一静态脚本在本记录写入后运行 | 0；0.582 秒 | 四条实际改动路径均在 writeScope 内；其余静态断言保持 |
| `diff-check`：`git diff --check` | 0；0.133 秒 | 当时补丁无 whitespace 错误；不代替依赖验证 |
| `lock-check-unavailable`：`uv lock --check --offline` | 1；0.045 秒 | 自有缓存无 C-Two 0.7.4；uv 的跨平台解析无法完成，不能声称锁一致 |

受影响批次的完整实际命令为：

```sh
python3 -m unittest -v \
  protocol.test_rpc_config \
  protocol.test_ctwo_service \
  protocol.test_transport_attach \
  blackboard.tasks.test_workspace_api \
  cli.test_checks_cleanup \
  cli.test_checks_parallel \
  install.test_packaging \
  install.test_runtime
```

模块加载失败涉及真实缺失的 `c_two` 与 `portalocker`。两个 launcher 用例各按既有流程尝试创建自己的私有环境，分别因 `annotated-types`/`fastdb4py` 下载的 DNS 错误失败，其 cleanup 因 portalocker 缺失也报错；锁仍旧版，不能把这些 launcher 尝试算为 0.7.4 安装。失败日志完整保留，不重复同类网络或测试尝试，不造导入桩、不把环境失败当防护证据。没有启动成功的真实 RPC 服务/Worker；本回合未运行 socket/ps 行为核对，不能据此判断这些权限是否可用。日志未出现 Too many open files。

本回合通过浏览工具读取 [PyPI 0.7.4 发布页](https://pypi.org/project/c-two/0.7.4/)，核对该页列出版本化发布物；PyPI JSON 端点返回 Cache miss。页面说明正文仍含 0.7.3 的安装示例，未据此作 0.7.4 行为等价证明，未手写发布物 URL/hash 或假造解析锁。Host 提供的私有安装及 `inspect.signature(cc.connect)` timeout 观察仍归 Host；本回合没有导入 0.7.4，公开名字、异常类型、配置键及 timeout 签名均未亲测，不审阅 vendor 内部作证明。

## 原 run 继续所需材料与剩余验收

请 Host 在任务专用根的 `host-materials/` 内提供基于当前 pin 与基线其余依赖生成的真实 `uv.lock` 参考，以及包含新锁当前平台依赖和构建依赖的离线 wheelhouse 或可复制的任务私有 CPython 3.13 依赖环境，并附生成命令、退出码、版本/哈希来源。参考锁不直接覆盖实施分支，由本 run 核对并写入授权的 `uv.lock`。不能从日常 pinned runtime 或凭据目录借用材料；若需任何范围外读取，应由 Host 先提供明确的任务私有材料位置。

材料就绪后继续核对只更新 C-Two 与项目 requires-dist 的锁差异、真实 0.7.4 导入和公开接口/异常/配置键，重跑上述受影响文件及对应打包/runtime 检查，取得 wheel/sdist 与按锁的私有安装证据。V-03 的 4 KiB/8 MiB 往返和关闭池 outgoing SHM、V-04 的首次同根/权限/长路径/隔离及 Windows mock、V-06 的新版锁/资源/私有安装均未由本回合取得；Host 的独立复核门槛保持，不借用 0.7.3 数字。

三处变异未执行，须先取得新版本目标基线通过，再在任务根各自副本执行：移除 `configure_local_endpoint` 的 root setter，要求 `IsolatedTransportTests.test_private_root_overrides_inherited_namespace` 的实际 public root 回读断言失败；移除 `pool_enabled=False`，要求 `IsolatedTransportTests.test_disabled_pool_releases_outgoing_shm_after_real_rpc` 的 outgoing SHM used_bytes==0 断言失败；把 daemon 夹具 `_start_daemon` 的 `private_environment(self.state, ...)` 改回 `self.work`，要求 `DaemonFixtureTests.test_daemon_environment_health_and_cleanup_select_the_same_state` 的实际 Popen env 状态根断言失败。缺失依赖、权限或库异常均不计作变异成功；Host 应从最终固定产物独立复核。未添加空闲60秒泄漏测试，未执行真实四 harness 冒烟。
