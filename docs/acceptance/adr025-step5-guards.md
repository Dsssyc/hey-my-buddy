# ADR-025 第五步开头：三项清理防护（微任务交付记录）

2026-10-07，第五步开头的清理微任务交付记录，不开始 C-Two 切换，等待本 Host 固定审查。输入基线 `43d8d6326d3af3cc6aca1db879febccfaeea41eb`（第五步开始时的分支头），在黑板分配的受管 worktree 完成，首次提交走路由、无指定 buddy。本微任务只补三项实际防护的测试，生产源码零改动，除已批准的 DSH 行为差异、停止口径与之后实时通道切换外不触碰任何行为；本微任务自身的执行（zcode / GLM-5.3-Flash 经受控回合）计 2 次（首轮交付与固定审查不通过后的同一 run continue 轮），与下文检查计数分开；下文以更正后的第二轮状态为准，首轮被更正处随文注明。

## 交付物与写范围

改动恰好落在授权写范围内：修改 `tests/python/buddy/harnesses/dsh/test_native_run.py`、`tests/python/buddy/harnesses/claude/test_claude_tool_evidence.py`、`tests/python/buddy/roles/test_registered_run_wiring.py` 三个既有测试文件，新增本文与 `adr025-step5-guards-test-ids.tsv`；首轮交付提交恰为三改二新、合计五个路径，第二轮 continue 只再改其中一份测试文件与两份文档，累计交付仍是这五个路径。被防护的生产源码点全部只读未动：`buddy/harnesses/dsh/native_run.py` 的 `_ALWAYS_DISABLED_ROWS` 常量与 `_launch_agent` 启动构造、`buddy/roles/run_execution.py` `_role_stop` 的原生 gone 合取与 `collect` 内 `nativeSession.resumable` 的停止合取、`buddy/harnesses/claude/tool_evidence.py` 的 `native_schema_delivery` 构造参数（真实消费点在 `claude/native_run.py` 的 `"--json-schema" in preparation.args`）。未发现接口缺口：三个防护都绑定到现存的真实开关与构造路径，无需 Host 删开关或接真实参数，没有升级事项。另一处如实登记并已闭环：本文首版曾被一次缺前导斜杠的写入误放进检出内以本机主目录命名的嵌套路径，内容随即按字节复制到本正确路径；固定审查时 Host 已通过黑板 workspace-resolve restore 将该误写路径从本受管检出移除并经指纹核对，检出内已不存在它。它从未进入任何提交，不属于交付内容；本 Worker 全程未删除任何对象。

## 三个防护的绑定与断言

DSH 常关两行：新增 `AlwaysOffPatchRowsTests`（三个编号），经真实的 `run`（write scope）与 `run_discovery` 无提示启动构造驱动公认启动包装器，从包装器记录的 argv 取 `--patch` 文件按字节读取，对 `session-title-llm` 与 `session-telemetry-otel` 两个字面行名分别断言恰出现一次且 `disabled` 恒为 True；断言与期望完全不从 `_ALWAYS_DISABLED_ROWS` 构造，也不引用该常量——由同一常量造出的期望在行被删时依旧自洽，这正是既有若干编号的形态，本防护补上会被单点变异打红的那个见证。

停止口径：`WorkerRegisteredRunTests` 新增四个编号。正向对照确认双层确认加有效回合导入时 `nativeSession.resumable` 恒 True 且 `bindingPresent` True；外层未确认（`handle.shutdown_confirmed` 打为假）重新 `collect` 后 resumable 必 False；原生层未确认是真实停止事实的负向见证——保持有效回合、binding 与结果帧 `continuation.resumable` True，把 `stopEvidence.native.groupState` 分别改为 unknown 与 alive，外层保持确认时 `collect` 必须 `shutdown_confirmed` False、原生停止事实 `processState.shutdownConfirmed` False、resumable False——未知与仍在运行绝不算已停。`GroupState` 是 `gone/alive/unknown` 的闭词汇表，没有 running；alive 是原生层真实产出的“仍在运行”事实（DSH 的 `native_run.py` 即此产出方），线帧写入 running 在解码即被拒，故仍在运行的见证用 alive。首轮曾把改写 `continuation.resumable` 当作“原生层未确认”，固定审查指出那只是续接能力；该部分已分离为改名编号（`test_a_denied_or_unknown_continuation_capability_is_not_resumable`）：能力 False 与 None 两个 subTest 下 resumable 必 False、双层停止确认保持 True，是能力见证而非停止证据。`is True`/`is False` 的严格比较是被钉住的事实。

Claude 交付识别：`StructuredDeliveryProjectionTests` 新增一个负向见证，按 `native_schema_delivery=False` 的真实分支构造与已启用正向见证完全相同的帧序列（init、根级 StructuredOutput 携带交付值、tool_result 收尾）：`settle_delivery` 恒返回空组、`tool_calls` 计 1、把 settlement 结果经共享包 `exclude_calls` 接缝传入 `finish` 后包仍计 1 且 StructuredOutput 的 start/end 事件保留、`judge_tool_evidence` 判 `TOOLS_FORBIDDEN`——未启用时内建调用是普通工具，绝不从工具计数排除；与 `test_the_root_delivery_is_verified_and_excluded_from_the_package` 成对，两个编号合起来覆盖开关两侧的实际行为而非开关常量本身。

## 聚焦检查

只运行受改动影响的三份测试文件，逐份在检出版执行 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 指向任务根 `t/`、`PYTHONPATH=<受管检出的绝对路径>/src:<同一检出的tests/python>`、`uv run --frozen python -m unittest -v <模块>`：`buddy.harnesses.dsh.test_native_run` 58 项 OK（12.9 秒）、`buddy.roles.test_registered_run_wiring` 20 项 OK（30.8 秒）、`buddy.harnesses.claude.test_claude_tool_evidence` 36 项 OK（2.8 秒，含经 mock CLI 的回执级 RunnerReceiptTests，无跳过）；三条命令真实输出在任务根 `m/baseline-20261007a/` 三个日志。第二轮 continue 按固定审查只重跑受影响的 `buddy.roles.test_registered_run_wiring`：未变异基线 21 项 OK（26.5 秒），日志在 `m/baseline-20261007b/wiring-baseline.log`；DSH 与 Claude 的代码与测试本轮未动，不重跑，首轮 58、20、36 项 OK 的三条基线日志原样保留在 `m/baseline-20261007a/`。未运行完整检查套件，未调用真实模型或安装版 harness，未安装或升级任何运行时。

## 变异防护（单点、隔离、留证）

变异机制不改检出内的生产源码：每个变异把整棵 `src` 拷入任务根 `m/` 的一个新子目录，用一次性脚本对副本做恰好一处的字符串替换（`assert count == 1` 后替换，`mutated.diff` 留差），再以 `PYTHONPATH=<变异副本>:<检出src>:<检出tests>` 运行受影响测试——三份测试的被变异模块都在测试进程内消费，红绿可完全归因于该单点。对照运行 `m/control-shadow-unmutated-20261007a/`：未变异影子副本先经 `diff -r` 与检出示意全同，三组防护全绿，证明影子机制本身不引入行为差；第二轮第五个变异复用同一机制。

五个变异的实际结果（目录都在任务根 `m/` 下，各含 `mutated.diff` 与 `red.log`）：`mutation-dsh-drop-title-row-20261007a` 删 title 行——title 行 run 测试与 discovery 测试红（断言 `0 != 1 : the launch patch must carry the session-title-llm row exactly once`），telemetry 行测试绿；`mutation-dsh-drop-telemetry-row-20261007a` 删 telemetry 行——镜像地红绿对调；`mutation-resumable-forced-true-20261007a` 把 `run_execution.collect` 的 resumable 合取整体替换为 `True`——正向对照保持绿、外层未确认与当时的“原生层未确认”编号（两个 subTest）共三处红（`True is not False`）；固定审查指出它没有覆盖“原生层必须 gone”的判定本身，该判定由本轮第五个变异钉住；`mutation-claude-armed-while-disabled-20261007a` 把 `_delivery_armed` 赋值改为恒真——新负向见证红（settle 返回验证键而非空组），全部已启用正向用例与未变异的其余用例保持绿；`mutation-native-gone-requirement-dropped-20261007b`（第二轮）在影子副本里把 `_role_stop` 的 gone 合取单点去掉——新原生停止见证的 unknown 与 alive 两个 subTest 均 red（`True is not False`），同一次运行里 gone 正向对照、外层负向与续接能力改名编号全部保持绿（其余 19 项 ok）。五个 `red.log` 的失败全部是断言失败（退出码 1、`FAILED (failures=N)`），无任何 ImportError 或环境错误冒充验证；其下的探针目录 `probe-shadow-1791338512` 只验证过 PYTHONPATH 影子优先级，未承载任何结论。

## 编号对照

三份文件合计 107 项既有编号全部保留：删除 0，累计新增 8 项（三个 DSH、四个 resumable、一个 Claude 负向见证），其中一轮改名：首轮新增的原生停止编号按固定审查一分为二——原编号承载重写后的真实停止见证，续接能力部分以 `test_a_denied_or_unknown_continuation_capability_is_not_resumable` 分离改名，逐项去向、guard 与故障核对见同目录 `adr025-step5-guards-test-ids.tsv`。未改编号以集合相等核对：本轮类/方法级提取差集在任务根 `m/test-id-diff-20261007b/test-id-diff.txt`（before=107、after=115、removed=0、added=8），首轮差集（before=107、after=114、removed=0）原样保留在 `m/baseline-20261007a/test-id-diff.txt`。

## 任务根与一次性材料

任务根为 Host 建立并登记的 `<任务根>`（`t/`、`m/` 已存在）；本微任务每条诊断与测试命令都显式把 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 指向其 `t/`，系统临时目录需求只落在该根内。`m/` 下本微任务新建七个子目录并全部原样保留：`probe-shadow-1791338512`（机制探针：src 副本加 PYTHONPATH 优先级验证）、`baseline-20261007a`（三条基线日志、test-id-diff.txt）、四个 `mutation-*-20261007a`（各含 src 影子副本、mutated.diff、red.log）、`control-shadow-unmutated-20261007a`（src 影子副本、shadow-identical.txt、三条绿日志）。第二轮新建三个子目录并同样原样保留：`baseline-20261007b`（未变异基线日志）、`mutation-native-gone-requirement-dropped-20261007b`（src 影子副本、mutate.py、mutated.diff、red.log）、`test-id-diff-20261007b`（extract_ids.py、test-id-diff.txt）；首轮七个子目录一个未动。`t/` 下测试夹具按各自私有根正常收尾，本微任务未自建容器，未执行任何 rm/unlink/rmdir/rmtree 或清理脚本，未覆盖或重建任何既有实验副本，检出与系统临时目录中的其他会话物品一律未动；该任务根连同上述全部一次性材料由 Host 验收后按这一个确切路径整体回收。
