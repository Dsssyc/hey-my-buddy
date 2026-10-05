# ADR-025 第一步 1-B：共用启动与收集（微任务交付记录，continue 修正）

2026-10-04，微任务 1-B 的执行记录，覆盖三轮：第一轮交付（固定 artifact `503a7bdd` / 输出 `544b93c`）经本 Host 零进程探针比对被拒并按 `continue` 在同一 run 内修正；第二轮修正交付（固定 artifact `9388e3e4` / 输出 `7529a298`）经 Host 只读探针指出剩余一处 DSH 异常边界与记录错误后，用户转达影响核对并授权恢复原 run 继续，本文为第三轮修正后的完整交付记录，交付固定累积 artifact 等待 Host 复核。最初输入基线 `da8c25dc344e654e76869aa35755fedcf73f6c80`（1-A 固定交付 `8a84b02` 已整合）；第二轮继续基线为 `544b93c`，第三轮继续基线为未验收的 `7529a2983056aaee506272cc2f4543c8be7dfc54`（Host 主分支新增的 ACP 文件与本微任务无重叠，本微任务未改 `harnesses/dsh/acp/`）。Host 探针材料（`~/.codex/worktrees/adr025-run-module/hey-my-buddy/tmp/adr025-host/controller-first-review-xa0glmu7/` 的 probe.py、baseline.json、candidate.json 与 created-paths.jsonl）本微任务只读未动，未重复执行该探针本体；本文的探针复验脚本只写本检出 `tmp/1b-controller/`。本文不宣告第一步完成，1-C 不在本微任务范围内。

## 交付物与写范围

改动恰好落在授权写范围内：`src/hey_my_buddy/buddy/harnesses/controller.py` 与 `tests/python/buddy/harnesses/test_controller.py`（新增），四个 harness 的 `adapter.py` 与 `buddy/roles/structured_call.py`（修改），两个仅迁移 patch 目标的测试（`test_private_adapter_invariants.py`、`test_account_bindings.py`，断言未改），以及本文。1-A 的 `run_contract.py`、`live.py`、`legacy_facts.py` 及其测试/fixture 未动，共用外层不需要任何公共值调整；`base.py`、`windows_process.py`、四个原生 runner/config/protocol/tool_evidence、注册表、`harnesses/dsh/acp/` 均只读未动；AdapterOutcome、公开回执与角色判定原样保留，公共 `RunResult` 事实字段没有任何增强。

## 第一轮交付的缺陷（Host 探针证实）与本轮修正

探针 baseline/candidate 比对证实两处行为改变，本轮逐条修正并以同语义进程内复验回到 baseline 结果（复验结果存 `tmp/1b-controller/continue-probe-check.json`，与 baseline.json 逐项一致）：其一是启动求值顺序——原各 `start` 先 `open_logs` 再在 spawn `try` 内求值 cwd/environment，第一轮把实参提前到开日志之前求值（探针 trace 从 `['logs','cwd']`/`['logs','cwd','env']` 且两日志存在，退化为 `['cwd']`/`['cwd','env']` 且日志不存在）；其二是停止判定被误并——Worker/发现的规则是整体布尔化（`bool(payload and 回执 is True and handle.shutdown_confirmed())`），Router 的规则额外把外层观察也按 `is True` 判定，第一轮的单一 `stop_confirmed` 丢掉了 Router 的外层强制，使外层 1/'unknown' 变成 ok/真值、None 变成 null。修正方式：`launch_controller` 以 `prepare` 绑定表达"开日志之后的调用方准备步骤"（零参调用、返回 `(command, cwd, environment)`，恰在 `open_logs` 之后、spawn `try` 之内运行，失败时两日志仍在、两个描述符由 `finally` 关闭、owned_popen 不运行）；停止侧拆成三个显式规则函数——`stop_confirmed`（Worker/发现，整体布尔、回执 `is True`、缺回执不启动外层）、`router_stop_confirmed`（Router，外层再 `is True`，报告字段类型不变）、`legacy_node_stop_confirmed`（DSH，真值回执或延迟的 preflight 计入内层，preflight 只在回执为假时求值，外层仍必须确认）。此外修正：DSH 的信号恢复为投影时读 `handle.process.returncode`（仍调用共享 `signal_name`），不再使用提前快照；`ControllerCollection` 删除未使用的 `signal` 字段，不再在收集阶段提前计算。

第一轮还有两条未列入选取范围的交付缺陷也一并修正：收集面只覆盖三个严格路径，DSH/Router 的整段读取与收集完全绕过共用面；本轮所有五类路径都经同一 `collect_controller(handle, *, read, stop=None)` 调用面，读取规则为命名函数——`read_strict_result`（512 KiB 有界、`decode` 参数承接三家各自 `decode_json`）、`read_plain_evidence`（Router 的普通 256 KiB + `guard_private_path` + lstat 普通文件检查 + 普通 `json.loads`，原样抛出，同一读取同时供冻结 request/result 证据核对使用）、`read_router_result`（Router 收集规则：基线异常集折叠为 None，非 object 原样交角色 fallback）、`read_last_line_result`（DSH 的无界 `read_text(errors="replace")` 末行普通 JSON，基线异常集与大小语义不变）。停止规则未在共享面内自动运行时（DSH），`stop_confirmed` 字段为 None，DSH 在自己的分支按基线时机调用 `legacy_node_stop_confirmed`；Router 的"不可读 → invalid-native-result"哨兵是角色自己的投影，留在 `structured_call._collect_result`。没有把三条读取允许集变成同一种更严或更宽的规则。

## 各路径保留的差异

读取：Codex/Claude/ZCode Worker 与三处发现共用 `read_strict_result`（512 KiB 上限、各自 `decode_json`）；Router 经 `read_router_result`（256 KiB、私有路径检查、普通解析，异常折叠在角色哨兵之前）；DSH Worker 经 `read_last_line_result`（末行、errors=replace、无界、基线异常/大小语义）。截止时间：Worker 无期限语义 Codex/Claude/ZCode 为 `math.inf`、DSH 为 `None`（服务侧 schema 把 `timeoutSeconds` 规范为 0 或 ≥10）；审阅预算由黑板验证为正，`+10` 宽限总是生效，外层零值分支不可达；no-tool 请求自身验证 `0 < t ≤ 60`。停止：严格路径回执 `is True` 加外层 handle 确认；Router 外层再 `is True`；DSH 保留真值回执与 preflight（exit 2 且 stdout 为空）两处历史例外，均已注明 legacy basis，第四步删除 Node 运行器时消除。求值顺序逐处按基线：三家 Worker 与三处发现的 `prepare` 内按命令（无副作用路径构造）→ cwd → env 求值；DSH 的 `before_try` 步骤在 open_logs 之后、spawn try/finally 之前求值 selected/native_environment/两组环境回填（正是基线位置，此处失败时两个日志描述符按基线保持打开，未授权顺手修补），`prepare` 在 try 内求值 `arguments()` 与 `workspace_cwd`；`structured_call.start`/`start_no_tool` 的 env 仍在 guard 循环之前构建（基线如此），命令与 cwd 是预计算值；Codex 认证清理仍在双层停止确认后且 `codingHomePrepared is True`；回合导入、native session、usage/quota、attention、artifacts 与 seal 的时机不变；Router 停止后的冻结副本核对与证据留存不提前。日志 FD 在各原 try/finally 边界的异常行为与基线一致（`open_logs` 自身中途失败时首个描述符的遗留也与基线相同，未借机修补）。

## 聚焦检查与测试编号（本轮只跑受修复影响范围，一次通过后停止）

聚焦运行复用 `hey_my_buddy.cli.checks.test_environment` 的净化环境（无继承 authority/runtime pin/厂商登录变量，Codex/Claude 用不存在 CLI 的 sentinel）；本轮起每个子进程额外在专用容器内显式设置 `HOME` 与 `DSH_HOME`，任何 DSH 启动（包括假 CLI）都解析到私有主目录，不改日常环境。运行器每次调用原子创建唯一容器（`tmp/1b-controller/run-<随机>/`）与一个 `/tmp` 下的短套接字根（darwin 用户临时目录深度会超出 104 字节 `sun_path` 预算，只影响本轮自建的聚焦运行器，见"撤回"一节），创建即打印登记，不覆盖、不复用、不删除先前材料。命令为 `uv run --frozen python tmp/1b-controller/focused_run.py` 与 `tmp/1b-controller/inventory.py`；编号是 unittest 真正加载的 `id()`（模块导入 + `loadTestsFromModule`），两侧方法一致。

- 本轮（修正后）聚焦一次通过：41 模块全部通过退出码 0（容器 `tmp/1b-controller/run-twcgexse`，短根 `/tmp/buddy-1b-sock-0ik784c3`）。运行后仅有一处与行为无关的收尾（删除 `dsh/adapter.py` 中已无引用的 `import json`，AST 与导入双验证名字未使用），随即将五个 DSH 模块在最终源码上复跑一次全绿（容器 `run-dsh-final-ig3xunyi`，短根 `/tmp/buddy-1b-sock-fi36eoad`）。
- 旧编号未变集合动态证明：以加载器对第一轮的 40 个旧模块重新收集编号，与第一轮基线清单逐 id 比对——682 个全部在、删除 0、变化 0（`baseline_ids.txt` 对 `continue_ids.txt`）。未再重复 682 个基线的执行运行。
- 新模块编号变化（第一轮 18 → 本轮 33）：保留 16、删除 2（`CollectControllerTests.test_collection_carries_payload_exit_signal_and_stop_together`——collection 不再携带 signal；`LaunchControllerTests.test_spawn_uses_the_owned_child_and_closes_the_log_descriptors`——由 `test_prepare_runs_between_the_logs_and_the_owned_spawn` 以相同断言承接）、新增 17（CodexCallerLaunchOrderTests 三个真实调用方 cwd/env/spawn 失败顺序与日志/FD 收尾测试、RouterCollectCoercionTests 两个真实 `structured_call.collect` 外层强制表测试、RouterStopTests 三个、LegacyNodeStopTests 两个、PlainAndLastLineReadTests 四个 Router/DSH 读取规则测试、CollectControllerTests 两处及 LaunchControllerTests 一处改造后的承接测试）。明细由 `after_ids.txt` 对 `continue_ids.txt` 动态比对得出。
- `test_zcode_native` 等已安装 harness 核对未运行；没有完整检查、Console、打包、真实 harness 或模型启动。

## 故障注入与恢复（本轮重做，最小变异 红 → 恢复 → 绿）

本轮对三个迁移/新增防护各注入一个最小故障，受影响测试真实报错后按字节恢复再重跑；`controller.py` 的本轮交付版本留有 sha256 副本 `<checkout>/tmp/1b-controller/controller.py.round2.pristine`（`3c83f853bbef746af56e44ab71498114100ab684237492e8384c579135c37e7d`），三次恢复后该哈希均复核一致。第一轮的两条变异记录（读取上限、双层停止）保留在 `mutation-read-cap-*.log`、`mutation-stop-*.log`。

- 读取上限：删去 `read_strict_result` 的越界拒绝。红：`StrictReadTests.test_the_512_kib_read_cap_refuses_before_decoding` 失败（超限结果被真正解码，`m2-read-cap-red.log`）；恢复后 `test_controller` 33 个测试全绿（`m2-read-cap-green.log`）。
- Worker/发现双层停止：`stop_confirmed` 坍缩成只看外层。红：`StopConfirmedTests` 7 处失败、`ZcodeAdapterTests.test_missing_controller_receipt_cannot_confirm_the_separate_native_group` 失败、`CodexAdapterTests.test_unconfirmed_stop_retains_goal_auth_and_confirmed_cleanup_handles_refresh` 失败（未确认停止被当成已停止且认证清理随之误触，`m2-two-layer-stop-red.log`）；恢复后三处全绿（`m2-two-layer-stop-green.log`）。
- Router 外层强制：`router_stop_confirmed` 去掉外层 `is True`。红：`RouterCollectCoercionTests`/`RouterStopTests` 5 个测试中 4 处失败（外层 1/'unknown' 再次变成真值成功，`m2-router-coercion-red.log`）；恢复后 `test_controller` 全绿（`m2-router-coercion-green.log`）。第一轮曾以较弱变异（`is not False`）做过一次对照：既有 zcode/codex 钉住测试仍被各自层次挡住，说明两层各有测试钉住、该变异不构成有效故障，故本轮改用上述三种有效变异。

## 第三轮 continue：DSH 异常边界撤销修复与记录更正

Host 只读探针（`~/.codex/worktrees/adr025-run-module/hey-my-buddy/tmp/adr025-host/1b-dsh-fd-order-cyhkerjl/`，位置以同目录 `1b-dsh-fd-probe-root.txt` 为准；材料只读未动）证实：DSH 基线在 open_logs 之后、spawn try/finally 之前求值 selected/native_environment/环境回填，`selected` 异常时两个日志描述符按基线保持打开，而 `7529a298` 把这一块搬进了 `prepare` 回调（spawn try 之内）使异常时描述符被关闭——这是未经授权的顺手修补。本轮撤销：`launch_controller` 增加窄绑定参数 `before_try`（在 open_logs 之后、try 之前运行，返回值交给 `prepare`，只有 DSH Worker 传入），`dsh/adapter.py` 的环境块回到该位置，`prepare` 在 try 内按基线求值 `arguments()` 与 `workspace_cwd`。第一、二轮已确认的修复全部保留：三家 Worker 启动顺序、Router 外层 `is True`、共用收集面与显式读取规则、DSH 末行/legacy 停止与信号取值时机。没有新增通道、所有权或框架。

新测试 `DshCallerFdBoundaryTests`（真实 `DshAdapter.start` 调用方）：`selected` 失败时 trace 为 `['logs','selected']`、两日志存在、两个描述符仍打开（fstat 通过），测试只关闭自己刚创建且仍持有的描述符、不删任何文件；`arguments` 失败（try 内）时 trace 为 `['logs','selected','arguments']`、描述符已关闭。同语义探针复验脚本 `dsh_fd_probe_check.py`（只写本任务根）对修正后候选输出与该探针 `baseline.json` 逐项一致（trace `['logs','selected']`、`[true,true]`、own closed 2）。

本轮聚焦运行按新边界只跑受影响模块：18 个模块（test_controller、四个 DSH 模块、三个 Worker 套件、两个 no-tool 套件、invariants、account_bindings 与四个 router 侧模块）一次全部通过、退出码 0。测试编号由加载器收集（未变化集合只收集不执行）：第一、二轮的 40 个旧模块 682 个编号删除 0、变化 0；`test_controller` 33→35（新增上述两个 DSH 边界测试）。未重跑 682 基线执行、未跑全部 41 模块、没有完整检查/Console/打包/真实 harness 或模型启动（本微任务三次执行自身计 3 次，与上述分开）。

本轮登记：任务临时根为 Host 本次原子创建并登记的 `/private/tmp/a25b-8sebf31t`（子目录 d/h/m/t），每条验证/诊断命令都显式设置 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 指向其 `t/`；本轮创建的对象全部在该根内——`m/affected_run.py`、`m/affected_run.txt`、`m/` 下 18 个模块日志、`m/inventory.py`、`m/continue3_ids.txt`、`m/continue3_inventory.log`、`m/dsh_fd_probe_check.py`、`m/probe-gdpzggm7/`（复验结果）、`d/` 下各模块的 state/runtime/home/dsh-home 根；代码与本文只写原 writeScope。按用户更新的清理规则，Worker 未删除任何对象（未执行 rm/unlink/rmdir/rmtree、未用通配、未按名字/日期/无进程推断归属）；该任务根由 Host 验收后按这一个确切路径整体删除。用户另澄清"私有 DSH 主目录"只指 `DSH_HOME`，不要求运行时一律替换用户 `HOME`（第二轮运行器按模块设 `HOME` 是自加的保守做法，非要求）。第二轮产物与检出 `tmp/1b-controller/` 历史材料原样保留，未复用未删除。

## 用户转达的影响核对与恢复授权（原样追加）

用户已转达 Claude Code Host 影响核对并授权恢复原 1-B run：用户临时目录中 `buddy-1b-*` 均已不在；其他会话 430 个 `buddy-*` 条目仍在，此前记过数量的 12 类与前日一致；没有证据碰到其他会话或日常数据。删除对象不恢复、不再追查。此结论作为用户转达的独立核对追加于本记录；第一、二轮的越界披露、撤回与暂停历史原样保留，不重写旧证据为更强证明。新用户清理指示替代旧清理规则（本条与前节即按新规则执行）。

## 撤回与更正

第一轮记录的"发现"一节结论错误，现撤回：`hey_my_buddy.cli.checks` 在 darwin 的 `main`（当前源码第 795 行）显式 `create_private_root(directory=Path("/tmp"))`，完整检查套件的私有根本来就是短路径，本 Host 也已在不含 1-B 的整合源码上完整检查通过（2453 个 Python 测试 / 163 个 Python 模块 / 110 个 Node 测试）。AF_UNIX 预算问题只存在于第一轮自建聚焦运行器把 TMPDIR 指到检出内超长路径的做法本身；该运行器本轮已改为 `/tmp` 下原子创建的专用短根。`checks.py` 未做任何改动。

第一轮清理登记的更正：第一轮记录声称"11 个专用短根按确切路径删除完毕"，实际只有 2 个（`/tmp/buddy-1b-controller-iv3lb2nc`、`/tmp/buddy-1b-controller-ryr9pjyi`）在创建时登记了全名；其余 9 个中 `buddy-1b-add-IXEv`、`buddy-1b-add-M9l2`、`buddy-1b-add-MLzB`、`buddy-1b-add-dW4u`、`buddy-1b-add-eHAt` 五个的名字是删除前用前缀过滤列表才得到的（创建时未登记，违反登记规则），`buddy-1b-ctl-LEOo`、`buddy-1b-novenv-kH2w`、`buddy-1b-probe-Uxw7`、`buddy-1b-probe-WTIb` 四个有创建输出为证。另有 macOS 用户临时目录下一组第一轮诊断根（`buddy-1b-single/bisect/repro/repeat/diag/trace-` 前缀）在一条命令里用前缀通配删除——对象确为本会话当次 `mkdtemp` 所建，但精确路径未逐一登记，逐对象结果不可证明，按规则标记为"前缀删除、逐路径未知"并作为偏离如实记录，未扫描系统目录归属、未触碰其他会话对象。本轮起全部材料按原子唯一名创建即登记。

## 本轮创建与清理登记（创建时登记的确切对象）

检出内保留（ignored `tmp/`，供核对）：`tmp/1b-controller/` 下第一轮全部材料（`baseline_ids.txt`、`after_ids.txt`、`baseline_run.txt`、`after_run.txt`、四个变异日志、`controller.py.pristine`、`run-roots-baseline/`、`run-roots-after/`、两个脚本）、本轮新增 `continue_ids.txt`、`continue_inventory.log`、`continue_run.txt`、`continue-probe-check.json`、`continue_probe_check.py`、六个 `m2-*.log`、`controller.py.round2.pristine`、容器 `run-twcgexse/` 与 `run-dsh-final-ig3xunyi/`。系统临时目录中本轮创建并逐个删除的确切对象（删除未遇错误、无重试）：`/tmp/buddy-1b-fix-*` 与六个 `m1/m1g/m2/m2g/m3/m3g` 变异短根均为同一条命令内经 shell 变量原子创建并按同一确切路径删除（名字未另行抄录，特此如实说明）；登记全名的有 `/tmp/buddy-1b-probecheck-gb7hwyl1`、`/tmp/buddy-1b-sock-0ik784c3`、`/tmp/buddy-1b-sock-fi36eoad`，均在交付前删除。真实原生 harness/模型运行 0 次（两轮相同）；全部检查只用项目内 fixture 控制器、普通 Python 子进程与进程内 mock，未安装或升级运行时，未改用户配置、凭据或日常数据，未读取凭据文件内容。本微任务自身两次执行（zcode/GLM-5.3-Flash 经受控回合）计 2 次，与上述分开。

## 仍未抽取的旧入口

本步只收拢四个 harness 外层与旧 structured_call 的实际重复，以下保持原样并如实列出：`buddy/runtime/command.py`（command 适配器自己的启动/读取/停止与 `_signal_name`，不在本微任务授权文件内）；四个 runner 内层对原生 CLI 的自有 `owned_popen`（原生进程本体，按计划不动）；`account_native.py` 与 `codex/account_probe.py` 的原生账户进程；DSH 的 Node 运行器及其自带停止判定（第四步整体删除）；Router 的"不可读哨兵" `_collect_result` 与证据保留逻辑（角色所有，读原语已共享）。Adapter 接口未迁移为新的原生 driver，注册表与角色调用点未变，角色投影按计划留待 1-C 与逐 harness 抽取。
