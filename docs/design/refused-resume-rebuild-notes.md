# 原生续接被拒后的服务改排

2026-10-15，受管检出中的源码与夹具验证记录，等待 Host 审查；没有安装，也没有调用真实模型。

## 判据与改排

服务在 `WorkflowCoordinator.settle_turn` 导入一个失败的 `native-session` 回合时，只使用运行结果的 `code == "native-resume-unavailable"`、`modelStarted is False` 和外层停止事实 `shutdownConfirmed is True`，不分析错误文案，不区分 Codex CLI、DSH CLI 和 ZCode CLI。已取消的微任务、已关闭的祖先和待处理的工作区范围冲突继续服从原有边界；模型已启动、启动事实缺失或停止未确认时不自动改排。

同一个结果事务先把被拒回合记为 `failed`、撤销它的执行凭据，再通过 `_requeue` 将微任务排队，并将它原来消费的 `workflow_continuations` 记录重新置为 `queued`。复用记录保留原续接文本、原因、协助结果快照、工作区清单和原有 `manual`、`helper` 或 `lead-wakeup` 类型，不新增存储形式，不修改 schema 21 到 22 的待做升级。后继 claim 按原有流程消费这份记录，并把新的执行和回合身份写入记录；被拒回合的身份、冻结输入及自动改排事件继续保留。

`begin_turn` 从上一回合的相同归一化事实确定新回合为 `reconstructed-new-session`，在输入的 `context.resumeReason` 写入 `native-resume-refused`，在 `context.resumeDetail` 带上运行模块的 `error` 原文。原有助手消息保留路径继续工作。Worker 的启动前协议重试列表没有改变，运行模块也没有增加重建决定。

## 一次改排与执行排他

改排只接受 `native-session` 回合；后继回合已经是 `reconstructed-new-session`，即使再次返回同样错误码也会停在原有失败边界。服务沿用结果不可变、结果重放幂等、事务内 `_requeue`、停止确认检查、claim 收据和每个微任务最多一个有效执行的约束，不添加重试计数或第二条执行路径。自动改排的结果回复同步报告 `taskState: queued`；这次失败不结算协助微任务，也不作为工作流失败边界唤醒 Lead，重建回合的最终失败仍按原有规则通知上级。

## Host 可读事实

服务写入 `workflow.native_resume_refused` 与 `workflow.auto_continued` 事件，二者绑定被拒的执行、回合与原续接记录，并携带原因、运行模块说明和后继重建方式。`get` 的 `resumeRecovery` 与 `status` 的 `workflow.resumeRecovery` 从最近一次此类自动续接事件投影同一份事实，包含 `autoContinued`、`reason`、`detail`、`turnId`、`attemptId`、`continuationId` 和 `eventSeq`；这些标识明确其所指的历史回合。新回合的 `resumeReason` 与 `resumeDetail` 也出现在回合视图中，CLI 的默认简短输出保留这些字段。

## 验证

所有 Python 测试均通过 Host 指定解释器执行 `PYTHONPATH=src <指定解释器> -m hey_my_buddy.cli.checks <文件或目录…>`，使用默认进程数；调用检查运行器前清除继承的 `HMB_STATE_DIR`、`HMB_RUNTIME_ROOT`、`HMB_RUNTIME`、`HMB_RUNTIME_IDENTITY`、`HMB_WORKER_STATE`、`HMB_WORKER_ID`、`HMB_AGENT_CREDENTIAL_FILE`、`HMB_AGENT_CREDENTIAL_FD`、`VIRTUAL_ENV` 和 `UV_PROJECT_ENVIRONMENT`。测试只使用运行器的私有根与夹具。

首次定向检查的服务、Codex CLI 助手消息续接与 DSH CLI 角色连接共 3 个文件、26 项测试通过；协助微任务结算与 Lead 唤醒边界补充后，服务与 Lead 生命周期共 2 个文件、31 项测试通过。要求范围的批量检查共 62 个文件、1041 项测试通过；最后补充的 Lead 原生续接拒绝与唤醒记录复用测试，连同 `test_store.py`、`test_blackboard.py`、`test_current_core.py` 共 4 个文件、105 项测试通过。各次检查均退出 0，私有根均已清理；这些运行包含重叠测试，不将项数相加。

批量检查选择了 `tests/python/blackboard/tasks/test_*workflow*.py`、`test_*continu*.py`、`test_*resume*.py`、`test_lead_*.py` 和新增的 `test_refused_resume.py`；Codex CLI 的 `test_codex.py`、`test_native_run.py`、`test_codex_continuation.py`；DSH CLI 的 `test_native_resume.py`、`test_native_run.py`、`test_dsh_role_wiring.py`、`test_session_records.py`；ZCode CLI 的 `test_zcode.py`、`test_native_run.py`；以及整个 `tests/python/buddy/roles/`、`tests/python/buddy/runtime/` 与 `tests/python/cli/test_cli_views.py`。`git diff --check` 也已通过。

服务夹具覆盖三个 CLI harness 的自动改排与原因、续接输入和工作区保留、初始回合与重建回合不改排、模型已启动和未知启动事实不改排、未确认停止不改排、重复结果与重复 claim、竞争 Worker 和唯一活动执行、协助微任务保留工作区，以及 Lead 等待与实际失败的唤醒边界。Codex CLI 和 ZCode CLI 的既有运行模块夹具补上严格的 `modelStarted is False` 断言；DSH CLI 的角色连接夹具补上同一归一化结果断言。

没有运行完整检查、真实 CLI 模型调用或日常服务验证；完整检查和安装由 Host 在集成之后处理，`docs/reference` 也由 Host 更新。
