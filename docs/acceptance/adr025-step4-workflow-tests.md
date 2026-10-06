# ADR-025 第四步 4-D1：真实黑板 Worker 流程的离线 DSH 夹具迁移

本记录是 ADR-025 第四步微任务 4-D1 的交付材料，覆盖同一微任务的三轮 Worker 回合：第一轮把仍引用 `BUDDY_RUNNER_PATH` 与两个 Node mock（`mock_turn_runner.mjs`、`mock_quota_turn_runner.mjs`）的 `tests/python/blackboard/tasks/test_workflow_worker.py` 与 `test_host_workflow_worker.py` 迁到注册运行本体（固定提交 `5cb0f9`，基线 `3c042df`）；第二轮在 Host 整合修正（基线 `bf34678`）之上按其指示恢复回执断言；第三轮按 Host `0603706` 的跨平台直选接口改选择缝并更正两处记录。任何环节不启动 Node，零模型调用、零安装版 DSH。两个 Node 夹具的"等 Host 迁移那批测试时一并退役"预留（见 [4-C 记录](adr025-step4-node-retirement.md)整合清单第 6 条）已兑现。

## 第三轮整改（跨平台直选与记录更正）

- Host 提交 `0603706` 修正夹具 daemon 的选择接口：`BUDDY_<X>_CLI` 指向 `.py` 文件时命令为 `[sys.executable, 可执行文件]`，其余可执行文件仍 `[可执行文件]`，无 Node 特例——上一轮"测试自写 shebang 包装脚本"的做法只适用 Unix，属接口要求不充分，本轮废除。本轮唯一代码修改：`harness_environment` 保留私有 `HOME`，`BUDDY_DSH_CLI` 直接设为夹具文件路径，删除自建的包装脚本创建与 chmod，不造 shell/cmd 包装。
- 记录更正一（第一、二轮验证史的表述）：`bf34678` 的 Git 树中仍存在两个旧 Node 夹具（`mock_turn_runner.mjs`、`mock_quota_turn_runner.mjs`，本检出先于 Host 历史的侧提交未入其树）；验证镜像 archive 出的基线因此携带它们，删除发生在应用本任务授权的累计补丁时，而非"基线中已不存在"。前两轮"archive 阶段无需排除"的写法据此更正。
- 记录更正二：真实配额拒绝未验证，此前"属 Host 排期的原生冒烟"的措辞删去，不写成已安排随后主动制造额度拒绝；现状仅是离线夹具形状经真实流程验证。
- 本轮验证：以 `0603706` archive 构造全新组合副本（其树仍含两旧夹具，应用相对原基线的累计补丁后按授权删除），仅运行两个受影响模块的 9 项一次，全绿；前轮两项变异证据复用不重跑（目标防护在 `3c042df..0603706` 的生产变更外未动）；完整检查归 Host。

## 第二轮整改（上一轮报告的 Host 修正落位）

- Host 提交 `bf34678`（及其间的 `b22a5ff` 原生停止原因保留、`dc8d77d` 未消费事实清理等）解决了上一轮报告的公共缺口：目录夹具 daemon 对四个 harness 一律 `command = [BUDDY_<X>_CLI]`（dsh 不再有 Node 分支，退役的 `BUDDY_RUNNER_PATH` 组合删除，开发环境白名单加入 `BUDDY_DSH_CLI`）；公共 Worker collect 恢复 DSH 已发布的回执语义——`sessionId`/`captured` 只取经验证导入的回合记录，`nativeSession.sessionIdSource` 报 `validated-turn`/`none`，`nativeActivity.sidecarWritten` 如实报告该 attempt 的 `activity.json` 是否存在。
- 上一轮记录的更正：当时把"`sessionIdSource`/`sidecarWritten` 无生产写入方"当成可用差异申报，Host 指出这不算允许差异——它们是 DSH 公共回执的既有语义，恢复后本轮断言一并恢复；`sessionIdConflict` 则从来不曾是生产字段，旧断言 `assertFalse(native.get(...))` 的本意就是确认无冲突，恢复原样、不造常量。被拒回合不捕获原生根会话的一侧由 Host 新增见证 `buddy.roles.test_registered_run_wiring.DshPublishedReceiptTests.test_an_unvalidated_dsh_turn_does_not_publish_a_captured_session` 钉住。
- 本轮唯一 scope 修改：`harness_environment` 改用直接可执行命令——测试在自己的私有根写一个可执行启动脚本（shebang 为当前解释器，进入 `workflow_agent.py`），经 `BUDDY_DSH_CLI` 选择；不依赖系统 Python，不给生产代码添兼容变量。恢复的三条断言加入 completed 测试，磁盘侧车的 task/attempt/generation 绑定读取保留；其余九项签收/封存/配额/冻结输入/跨 harness 断言不减弱。

## 迁移方式（第一轮建立，第二轮更新选择缝）

- 新夹具 `fixtures/workflow_agent.py` 是共享假 ACP 程序（`../acp/fake_agent.py`，本轮未改）的薄扩展：线缆行为、治理载体与强制私有 HOME 绊线都是共享程序自己的；它只补两类测试特有事实——工作区修改（配额回合的真实编辑、续接回合的上下文捕获与输出文件）与一次离线配额死亡（不发 finish、`refusal` 停止、私有会话记录携带机器 `QUOTA` 回合末，即新集成中原生失败码的唯一所在）。最终值仍来自真实角色规则：finish 回执由 `worker_services.call_session_tool` 对本运行自己的 bridge 铸造。会话服务绑定（服务器名与 bridge 路径）按安装版 DSH 的所见机械推导自 `session/new` 的挂载描述，不读控制器控制文件、不硬编码工具名。
- 执行选择缝（第三轮更新）：daemon 链运行的是服务侧选择的健康记录，目录测试夹具 daemon 对四个 harness 一律从 `BUDDY_<X>_CLI` 组合命令，`.py` 条目以 daemon 自身解释器启动（Host `0603706`）；两个测试模块把 `BUDDY_DSH_CLI` 直接设为 `workflow_agent.py`，无私有包装脚本、无 shell/cmd 包装。第一轮曾临时经 `BUDDY_NODE`+`BUDDY_RUNNER_PATH` 旧组合钉解释器，第二轮曾自写 shebang 启动脚本（仅 Unix 可用），两者均随 Host 修正废弃。`HOME` 钉进测试私有根，使共享程序的强制私有 HOME 绊线对整条 daemon 链成立。生产代码与共享测试支持均未改动。
- 配额场景的选取：夹具对"turnIndex 1 且目标文本为本场景固定目标"的回合执行配额死亡，其余回合一律治理完成（turn≥2 捕获续接上下文）；`--workflow-mode` 可为直接启动钉死行为。死亡形状与新集成的事实面一致：回合以原生停止原因失败（`bf34678` 起 Worker collect 保留原因，失败码为 `native-refusal`，由 Host 见证 `WorkerRegisteredRunTests.test_a_noncompleted_native_turn_keeps_its_reason_without_a_finish_receipt` 钉住；第一轮基线上同形状表现为 `missing-finish`），机器失败码只从私有会话记录的回合末进入回执（`quotaFailure.nativeCode=QUOTA`、`source=dsh/session-turn-end`、规范化 `code=quota-exceeded`），部分用量与末条助手消息同样来自记录；旧 mock 曾把 `rateLimitType`、`status:error` 与配额事实混在 runner 输出里，该形状随载体退役。

## 断言处置

- 九项测试编号逐项保留（见[编号表](adr025-step4-workflow-tests-test-ids.tsv)）：任务/尝试/世代、冻结输入、签收/封存、partial patch、同 run 更换配置、用户锁定配置拒绝、跨 harness 重建上下文、活动与停止边界等意图的断言未改；`mock-session-` 前缀改为 `fake-session-`（ACP `session/new` 的真实根会话，不再是夹具自造标签）。
- 存储事实改为新 DSH 的真实事实：`attempt-private-sessions`/`buddy-attempt`/`not-listed-in-native-app`，另加 `credentialsStore=harness-user-store` 与 `bindingPresent=false`；旧断言 `harness-user-store`×2/`user-store` 是 Node mock 硬编码的分组假象，不当真实原生证据。正常新会话落私有存储是既定改造口径，不是本轮发明的差异。
- 回执身份与活动事实（第二轮恢复，第一轮曾误报为无后继）：`nativeSession.sessionIdSource="validated-turn"`、无冲突的旧 `assertFalse` 原样恢复（生产从无 `sessionIdConflict` 字段），`nativeActivity.sidecarWritten` 恢复为实际文件存在性；其"未验证回合不捕获原生根会话"的一侧由 Host 见证 `DshPublishedReceiptTests.test_an_unvalidated_dsh_turn_does_not_publish_a_captured_session` 承载，磁盘侧车的 task/attempt/generation 绑定读取保留为本侧活动见证，相位/计数断言不变（`finishing`、`{modelTurns: 1, toolCalls: 1}`，由运行模块自身观察器产出）。
- 删除 `DshNativeStorageArgumentsTests` 两项 argv 见证（载体 `BUDDY_RUNNER_PATH`+`DshAdapter.arguments` 已随 4-B2 删除）：会话根/属主 home 意图由现有见证承载（编号见表），"治理回合在 attempt 目录发布活动侧车"的意图则保留在本轮 completed 端到端断言与现有单元见证 `WorkerSeamTests.test_the_governed_activity_sidecar_publishes_bounded_phases`。
- 新增一条忠实迁移的机器码见证（`quota_failure` 助手内，三项宿主测试共享）：`quotaFailure.code=quota-exceeded`、`nativeCode=QUOTA`、`source=dsh/session-turn-end` 且 `currentTurn.tokenUsage` 非空——旧测试只在 codex 段断言过分类，DSH 段的事实由新断言钉住；这正是不改断言求绿的反面：为新夹具的真实事实补上回执 witness。

## 缺口与差异报告

- 上一轮报告的目录夹具 daemon 选择缝已由 Host `bf34678` 解决（四 harness 统一从 `BUDDY_<X>_CLI` 组合命令、`BUDDY_DSH_CLI` 入开发白名单、`BUDDY_RUNNER_PATH` 组合退役）；`harness_environment` 第二轮起改用该缝（第三轮起按 `0603706` 直接指向夹具 `.py` 文件），不再经 Node 命名变量。上一轮描述的"退役回执字段无后继"按 Host 更正处理：`sessionIdSource`/`sidecarWritten` 属 DSH 既有回执语义，已恢复并恢复断言，不作为允许差异留档；生产从无 `sessionIdConflict` 字段，旧断言即"无冲突"确认。
- 仍归 Host 的文档滞后：`docs/reference/workers.md` 第 25 行附近仍描述已删除的 Node runner 与 `BUDDY_RUNNER_PATH`；`docs/reference/operations.md` 的环境变量表仍列 `BUDDY_NODE`/`BUDDY_RUNNER_PATH` 为 DSH 覆盖项（`bf34678` 树上仍在，反例为上述两行原文）。历史验收/设计文档中的旧引用按惯例不改写。
- 真实配额拒绝未验证：假程序按新集成的事实面构造私有会话记录的机器 `QUOTA` 回合末，真实安装版 DSH 在配额拒绝时的确切组合（原生回合末与 ACP 停止原因的对应）未验证，本轮不制造也不承诺制造真实配额拒绝。

## 验证证据

- 任务根以下记作 `<task-root>`（确切路径只在交付摘要与任务 tmp 台账）；全部命令 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 指 `<task-root>/t/`。第一轮材料在新子目录 `<task-root>/m/d1-migration/`，第二轮在 `m/d1-turn2/`，第三轮在 `m/d1-turn3/`；未触碰任何旧实验或他人会话对象；普通测试夹具与检查运行器自有收尾照常。
- 第一轮（基线 `3c042df`，改动前）：`python -m unittest blackboard.tasks.test_workflow_worker.DshNativeStorageArgumentsTests blackboard.tasks.test_host_workflow_worker.HostQuotaRecoveryTests.test_quota_failure_cannot_override_a_user_locked_configuration` → 3 项全红（两项 `AttributeError: … no attribute 'arguments'`，一项配额流程无 partial-output）。迁移后第一轮检出内 `python -m unittest blackboard.tasks.test_host_workflow_worker blackboard.tasks.test_workflow_worker` → 9 项全绿（约 59 秒）。
- 第二轮：以 Host 新基线 `bf34678` 的 archive 构造全新验证镜像 `m/d1-turn2/verify/`，其上应用唯一范围相对原基线 `3c042df` 的累计补丁（7 文件，`m/d1-turn2/scope.patch`），未在受管检出切换分支、未改公共文件；镜像内 `git apply` 干净。更正：`bf34678` 树仍含两个旧 Node 夹具，archive 出的基线携带它们，删除发生在应用累计补丁时（补丁即本任务授权的删除），并非"基线中已不存在"。以检出虚拟环境的解释器直接运行（`PYTHONPATH` 指镜像 `src` 与 `tests/python`，先以 `hey_my_buddy.__file__` 核验解析落点在镜像内）：两模块 9 项全绿（约 60 秒）。
- 第三轮：以 Host `0603706` 的 archive 构造全新组合副本 `m/d1-turn3/verify/`（树仍含两旧夹具，应用相对原基线的累计补丁 `m/d1-turn3/scope.patch` 后按授权删除），仅运行一次 `python -m unittest blackboard.tasks.test_host_workflow_worker blackboard.tasks.test_workflow_worker` → 9 项全绿（约 58 秒）；`BUDDY_DSH_CLI` 直接指向夹具 `.py` 文件（无包装脚本），解析落点核验同前。测试计数三轮一致（无增删）。
- 第二轮探针（保留目录的私有运行）：配额回合在新基线的失败码为保留的原生停止原因 `native-refusal`（error "the native turn stopped with reason 'refusal'"），`quotaFailure` 为 `{code: quota-exceeded, nativeCode: QUOTA, source: dsh/session-turn-end}`，失败回合 `sessionIdSource=none`、`captured=false`——与 Host 的恢复语义一致。
- 故障注入（第一轮执行，恰好两项，均命中目标断言，变异 FAILED→原件 OK；第二、三轮两项目标防护未被 Host 改动——侧车绑定读取与配额分类路径在生产变更外未动，按指示复用不再重跑；第二、三轮镜像运行另行复证了机器码断言的健康形态）：
  - F1 侧车绑定：注入脚本在测试读取侧车瞬间把 `attemptId` 改为外来值 → `read_sidecar` 按绑定拒绝返回 None → 目标断言 `assertIsNotNone(sidecar, "the governed dsh run did not leave a bound activity sidecar")` FAILED；原件通过。
  - F2 非配额机器码：夹具副本把会话记录回合末码改为 `SUSPENDED` → 回执 `quotaFailure` 为 None → 目标断言 `assertIsNotNone(failure, "the offline quota death lost its machine code")` FAILED，且失败前 state=failed、shutdown 自证、partial 封存断言全部照常通过（证明该 witness 恰好只钉配额分类）；原件通过。
- 编号账目：基线 11 项 → 9 项，逐项对应见表；+0/−2（两项 argv 见证删除，意图由现有见证与本轮端到端断言承载），未变集合即其余 9 项断言意图不变（第二轮恢复 3 条断言、第三轮仅改选择缝与记录，9 项计数始终不变）。

## 未验证边界

- 真实安装版 DSH 上的配额形状（原生记录的确切回合末与 ACP 停止原因组合）未验证：假程序按新集成的事实面构造（记录携带机器码、prompt 以 refusal 停止），真实组合可能不同；本轮不制造、不承诺制造，也不代 Host 排期。
- 完整检查套件、控制台前端解析、打包与安装面未运行，归 Host 统一；本轮两模块在私有任务根内的运行不等于全套件结论。
