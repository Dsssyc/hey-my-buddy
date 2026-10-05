# ADR-025 DSH 独立 ACP 客户端：Host 复核整改（子进程环境与测试夹具）

2026-10-04，Claude Code Host 对独立 ACP 客户端线暂不验收后，同一 ADR-025 宏任务下追加本记录所述整改。原微任务 run `ee2ee1e3-5c5d-4032-8a6f-fcfd20e50b6e` 已内部 ack accepted，服务拒绝 continue（CONFLICT: An accepted goal cannot be continued），原 run 未取消；[Host 整合记录](adr025-dsh-acp-client-host.md)与[微任务交付记录](adr025-dsh-acp-client.md)连同历轮记录只读保留，原交付文档不改。本 run `9ceb132c` 的实施回合依次为：submit 起始的初始整改回合、第一次 continue（第二个实施回合）、本记录当前所处的第二次 continue（第三个实施回合）；旧 run 的 5 个实施回合与 4 次 continue 计数独立保留，不混计。固定源码基线为 `a5389f7f3dfa48163be44a395801017122d7718b`，其 ACP 代码与测试和原固定 `f03b94f` 逐字相同；本轮按普通路由执行，未指定 buddy 或配置。本记录描述初始整改回合的两处修改与其实际验证，以及其后 continue 的追加整改，不重写历史已验证语义。

## 本轮授权的澄清

本轮授权中的"私有主目录"指 `DSH_HOME`：每次启动仍强制 `DSH_HOME` 落在调用方为本次运行创建、已通过校验的私有根内，用户默认 DSH 目录、缺失、非目录、符号链接、越界、日志路径拒绝副作用、`extra_env` 不能绕过 `DSH_HOME`、spawn 后所有权等全部既有校验逐字保留。`HOME` 默认不强制：子进程环境经项目既有 `harnesses.discovery.native_environment` 传递，`HOME` 保留其继承值（默认真实或继承均可，不要求在私有根内，任何路径都不读取其内容）；调用方仍可为探针或测试显式提供私有 `HOME`，显式私有 home 的既有路径校验（缺失、非目录、符号链接、越界、真实主目录、默认 DSH 目录）全部保留。历史记录中"强制私有 HOME 与 DSH_HOME"的已验证事实按其当时语义保留。

## 改动一：子进程环境改用 native_environment

`launch.py` 的 `child_environment` 不再手写窄白名单：父环境先过 `discovery.native_environment`（保留其既有全部允许键——身份、locale、临时目录、终端、代理、CA 信任根），再并入显式 `extra_env`，最后强制 `DSH_HOME`；仅当调用方显式提供 `home` 时才覆盖 `HOME`。除 `native_environment` 允许键外的父环境变量与凭据变量不带入子进程。`HOME`/`DSH_HOME` 仍为保留键，`extra_env` 携带其一即拒绝，校验值始终最后写入，因此任何实参都不能绕过显式入口、`DSH_HOME` 验证或静默改写已冻结目录。

签名调整仅两处并在此写明：`child_environment(dsh_home, home=None, extra_env=None, *, source=None)`，其中 `home` 变为可选、`source` 只用于测试注入合成环境；`launch(...)` 的签名不变，`home=None` 时不再默认 `<私有根>/home`，仅显式提供时校验并覆盖；`record_launch` 的 `home` 字段随之可空。`AcpClient.start` 与 client 传递不变，未扩其他 API。启动日志仍只记 argv、home 路径与环境键名，从不记环境值；本模块不读凭据文件。包与模块 docstring 同步改写；`client.py`、`connection.py`、注册表、角色、公共文件与 DSH 旧入口未动，未接线。

## 改动二：测试临时目录与失败呈现

`support.py` 重写：每测试一个普通 `TemporaryDirectory`，位于 `BUDDY_CHECKS_TMPDIR` 指向的检查运行器私有根（直接运行聚焦测试时由本回合任务根 `t/` 提供；回退 `/tmp` 与其他套件的既有写法一致），teardown 正常清理。常驻 run 容器、`manifest.jsonl`/`deletions.jsonl` 台账与 `preserve` 过度保留机制全部移除；检出 `tmp/adr025-dsh-acp-tests` 不再被本测试创建、扫描或清理，历史遗留一律不触碰。

未确认停止直接让测试失败，路径分三种：`shutdown` 抛出时对 owned handle 保守 terminate 一次（所有权不丢）后重新抛出原异常；`shutdown` 正常返回但 `shutdownConfirmed` 为假时同样保守 terminate 后重新观察，组确认 gone 则正常通过；仅当最终观察仍未确认 gone 时才以 `AssertionError` 携带最终观察证据失败。任何路径都不写常驻账本。close/cancel/EOF 不等于 group gone 与 owned handle 保守停止证据的本体断言全部不变。fixture 只清理自己在 `BUDDY_CHECKS_TMPDIR` 中新建的测试对象——这是用户授权的"Worker 不删除"测试例外；fake agent 与测试资料只写在每测试容器内。

## 验证

每条命令都显式设置 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 为本回合任务根的 `t/`，并按检查套件同样的卫生条件清除继承的运行时、Worker 与凭据变量（`BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`、`BUDDY_RUNTIME`、`BUDDY_RUNTIME_IDENTITY`、`BUDDY_WORKER_STATE`、`BUDDY_WORKER_ID`、`BUDDY_AGENT_CREDENTIAL`、`BUDDY_AGENT_CREDENTIAL_FILE`、`BUDDY_ACCOUNT_SELECTION`、`BUDDY_SUPERVISOR_START_ID`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`），统一用 `uv run --frozen`。一次性脚本与原始日志都在任务根 `m/`。按授权只跑三处实际影响的聚焦模块与必要最小故障，一次通过即停，未跑完整检查、Console 或打包。

| 步骤 | 内容 | 结果 |
| --- | --- | --- |
| 变异红 | 停用 `_violations` 的默认 DSH 目录检查，单跑 `test_user_default_dsh_home_is_refused` | FAILED：拒绝消息退化为 "dsh-home is outside the private root: <用户默认 .dsh>"，断言要求的 "default DSH home" 不再出现；退出码 1（`m/mutation-red.log`） |
| 恢复绿 | 逐字恢复保护后重跑同测 | OK，退出码 0（`m/mutation-restore-green.log`） |
| 三聚焦模块 | `python -m unittest -v` 跑 `test_acp_launch`、`test_acp_connection`、`test_acp_session` | `Ran 71 tests in 12.636s`、`OK`，退出码 0（`m/focused-three-modules.log`） |

测试编号由加载器对基线 `a5389f7`（以 `git archive` 落在任务根的只读副本）与工作树动态收集比对（`m/ids-baseline.txt`、`m/ids-current.txt`、`m/ids-diff.txt`）：66 个编号不变；改名 2（`test_legal_launch_runs_and_logs_the_whitelist` 改为 `test_legal_launch_runs_and_logs_the_native_environment`，`test_child_environment_is_the_whitelist_plus_enumerated_extras` 改为 `test_child_environment_is_native_environment_plus_the_forced_homes`）；新增 3（`test_parent_home_is_kept_without_an_explicit_private_home`、`test_spawned_child_receives_the_native_environment_with_forced_homes`、`test_unconfirmed_stop_fails_the_test_with_the_observed_evidence`）；删除 1（`test_cleanup_guard_preserves_instead_of_deleting`，其被删除的 preserve 机制不复存在）。总数 69 改为 71（launch 20、connection 39、session 12）。旧五轮[编号表](adr025-dsh-acp-client-test-map.tsv)只读保留，未变集合以上述动态比对为证。

新增与调整测试的证明面：合成环境下子环境与 `native_environment` 逐键相等；无显式私有 home 时父 `HOME` 保留；显式私有 `HOME` 可用且就是子进程实际运行的环境；用户名、locale、`TMPDIR`、代理与 CA 信任根键保留；父环境非允许键与凭据键（含 `BUDDY_AGENT_CREDENTIAL` 与注入的测试垃圾键）不带入；默认 DSH 目录与各私有 `DSH_HOME` 路径保护（缺失、非目录、符号链接、越界、真实主目录、保留键拒绝）仍被原有测试覆盖并通过；启动记录从不携带环境值。全程不读真实配置文件。

## 原生运行与边界

本轮原生 DSH 运行 0 次、模型验证 0 次；历史累计原生 DSH 1 次免模型握手、0 次 prompt 保持不变。不调用模型的真实握手由 Host 用修正后的客户端另行执行，本 Worker 未抢先执行。其他真实 harness 与模型均未运行。

## 任务根与写范围

本回合唯一任务临时根为 `/private/tmp/a25f-u1fu7fbr`（Host 本次原子创建并登记）。运行结束后 `t/` 无测试残留，仅有 uv 工具自身创建的 `uv-*.lock`（工具对象，保留不删）；`m/` 有 `mutation-red.log`、`mutation-restore-green.log`、`focused-three-modules.log`、`ids-baseline.txt`、`ids-current.txt`、`ids-diff.txt`、`collect_ids.py` 与 `baseline-ids/`（基线测试树只读副本）。Worker 未删除任何对象，未执行 rm/unlink/rmdir/rmtree，未用 glob 删除，未按名字或日期推断归属；验收后由 Host 按这一个确切根整体删除。代码与入库记录只写三个唯一写范围：初始整改回合修改 5 个既有文件（`src/hey_my_buddy/buddy/harnesses/dsh/acp/` 下 2 个、`tests/python/buddy/harnesses/dsh/acp/` 下 3 个，合计 +203/−179 行），新建本记录 1 个文档，无其他新增或删除文件；检出 `tmp/` 未被创建或写入。

## 第一次 continue（第二个实施回合）：守卫测试平台无关化与口径修正

Host 审查固定 artifact `bcf9fa97`（输出 `92b60a4d`）后在本 run 的第一次 continue 内追加两处范围整改，本节记录其完成情况；上文初始整改回合（submit 起始的首个实施回合）的验证与计数（71 项、变异红绿等）为历史事实，本回合未重跑也未改写。整改基线为该固定输出，除本节两个文件外其余字节不变。

其一，新增守卫测试 `test_unconfirmed_stop_fails_the_test_with_the_observed_evidence` 原以 `os.getpgrp()` 绑定本进程组，该函数 POSIX 才有；现改为完全内存化的合成观察——Job 型句柄（`job.active()` 返回 1）驱动生产 `group_observation` 的 job 分支得到 `alive`、`shutdownConfirmed` 为假，任何平台行为一致，不触碰本次运行的真实进程组，不跳过测试；同时移除该测试引入且不再使用的 `import os`。生产 connection/stop 逻辑、环境与临时目录支架零改动。

其二，本记录"改动二"段的失败路径口径已修正为代码实际行为：`shutdown` 抛出时保守 terminate 后重新抛出原异常；正常返回但未确认时保守 terminate 后重查，确认 gone 则通过；仅最终仍未确认才抛 `AssertionError`。

验证只用该回合显式 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR`（任务根 `t/`）及同样清洗的环境跑了两条：单跑该守卫测试（`m/continue-2/single-guard-test.log`，OK，退出码 0）；平台缺失函数模拟——删除 `os.getpgrp`/`os.killpg`/`os.setpgrp` 后运行同一测试仍通过（`m/continue-2/posix-absence-sim.py` 与 `posix-absence-sim.log`，退出码 0），证明测试不依赖任何 POSIX 进程组函数。71 项测试、默认 DSH 变异、完整检查、Console、打包、原生 DSH 与模型均未重复；测试编号集合与 71 项完全一致（同名同方法，无增删改名）。该回合新增任务根对象都在 `m/continue-2/` 新子目录（目录名沿用创建当时的命名），旧材料只读未覆写；Worker 未删除任何对象。

## 第二次 continue（第三个实施回合）：回合命名口径修正

Host 复核固定 artifact `998213a3`（累积输出 `fcf133f2`，其生产与支架字节相对 `92b60a4d` 完全不变；Job 合成观察测试与 POSIX 缺失模拟日志均通过）后指出：run `9ceb132c` 在此之前只有两个实施回合——submit 起始的初始整改回合（71 项测试等验证）与第一次 continue（单守卫测试加平台缺失模拟，第二个实施回合），服务 continuationCount 为 1；本记录此前却把初始整改回合称作"第一次 continue"、把实际的第一次 continue 称作"第二次 continue"。本节即第二次 continue、第三个实施回合，纯文档整改：仅修正本记录的回合标题、历史事实关联与状态口径；不重跑任何测试、编号、原生或模型检查，不改任何源码；已通过证据不撤回，清理披露与历史材料原样保留，旧 run 的回合与 continue 计数不混入。本回合未产生新的任务根一次性材料。

## 状态

初始整改回合的两处修改、第一次 continue 的两处范围整改与第二次 continue 的回合命名口径修正均已完成并提交，停下等用户转达 Host 复核。ACP 公共接线继续等待第一步外部验收；本记录不宣称第四步或 ADR-025 完成。
