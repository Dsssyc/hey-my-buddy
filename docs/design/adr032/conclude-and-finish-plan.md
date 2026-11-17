# 0.66.0 执行计划：结束直接停下运行中的工作，结束之后的工具调用记成事实

2026-11-07，Host 起草。装为 0.66.0：Host 面的 `conclude` 改了含义，契约到 0.66.0；不改存储。2026-11-08 装上（[安装记录](../../acceptance/installs/0.66.0.md)）。

## 依据

- [ADR-032](../../decisions/032-host-interface-delegation-loop.md) 第 1 条 2026-11-07 的修改：`conclude` 结束不再续做的工作，还在运行的先停下，等停止确认之后在同一次调用里记下结论；`cancel` 只用于停下之后交给别的 buddy 接手；用户不在控制台替 Host 结束微任务。
- [ADR-030](../../decisions/030-harness-layer-scope.md) 只保留三类拒绝：结果确实属于这个回合、进程确实停了、凭据不外泄。
- [待办](../backlog.md)：“委派与 Worker”一节的“Host 取消之后不再回来，微任务就一直‘等 Host’”与“ZCode 的 Worker 调用结束工具之后再调一次工具，整个回合就判失败”。

## 现状（Host 读代码得到的，2026-11-07）

- **结束**：`blackboard/tasks/workflow.py` 的 `conclude` 只接受状态为失败、取消或已交付的微任务（`_conclude_precheck`，否则 `NOT_READY`）。Lead 是例外：`_stop_lead_for_conclusion` 在同一次调用里取消 Lead 与它名下的整棵树，停止还没确认就返回 `SHUTDOWN_UNCONFIRMED`，要 Host 等停下来再调一次。
- **取消**：Host 取消时给 Worker 一段有序收尾的宽限期（`tests/python/blackboard/tasks/test_wrap_up.py` 里是 5 分钟）。取消之后、没有结论的根微任务算作“等 Host”（`blackboard/tasks/macro_tasks.py` 的 `_state`，`micro_task.host_filter_sql`）。
- **等待**：`await` 的 `waitSeconds` 是 1–86400 的整数，默认 86400。
- **结束之后的工具调用**：ZCode 在根会话调用结束工具之后，见到回答工具以外的任何工具调用就判 `duplicate-finish`（`buddy/harnesses/zcode/protocol.py` 约 926 行）；DSH 对第二次结束调用也判 `duplicate-finish`（`buddy/harnesses/dsh/protocol.py` 约 360 行）。日常黑板上 164 次 ZCode 执行里有 4 次这样失败，两次结束之后调的是 TodoWrite，两次是又调了一次结束工具。Codex 一个回合出了两条最终回答时，产品取回合结束前的最后一条，被取代的记成事实（`codex/protocol.py` 的 `superseded_final_answers`，2026-10-15）；ZCode 与 DSH 的答案是结束工具一经接受就固定的回执，所以这里不取最后一次，而以已经接受的回执为准。

## 口径（Host 定，开发者可改）

- **结束运行中的工作**：`conclude` 对任何还没有结论的微任务都可用。还在运行、停在边界或正在停的，先走与 `cancel` 相同的取消（同样的宽限期与停止证据），然后在这次调用里等停止确认，再照现在的做法封存、记下结论、回收检出。等多久用与 `await` 相同的 `waitSeconds`（同样的取值与默认值）；到时停止仍未确认，回复写明停止未确认，取消已经记下、结论没有记，Host 再调一次 `conclude` 即可。Lead 的整棵树走同一条路，原来的 `SHUTDOWN_UNCONFIRMED` 立即返回改成同样的等待。不另存“停下之后结束”的意图：那要改存储；等待写在调用里，一次调用就够。
- **结束之后的工具调用**：已经接受的结束回执就是这个回合的答案。之后的工具调用，包括再一次调用结束工具，记成事实写进执行结果，不让回合失败；结束工具对第二次调用照实回答模型这个回合已经结束。封存的输出照常以检出为准，Host 从事实里看得到结束之后还做了什么。

## 微任务

1. **结束直接停下运行中的工作**：`blackboard/tasks/workflow.py` 的 `conclude` 与它的预检、Lead 的停止，请求模型（`protocol/requests.py`）、`help` 的说明，钉着 C-Two 方法签名的 `tests/python/protocol/contract_signatures.json` 与 `CONTRACT_VERSION`，以及受影响的测试。动到停止证据与结论的路径，验收时请一次独立审查。
2. **结束之后的工具调用记成事实**：`buddy/harnesses/zcode/protocol.py`、`buddy/harnesses/dsh/protocol.py` 与它们把事实带进执行结果的地方，以及受影响的测试。

两个的写入范围不重叠，同时开工。文档（skill 第 4、5 步、`docs/reference` 里写到 `cancel` 与 `conclude` 的几页、Host 指南）由 Host 在两个都合进来之后改。

## 验收与安装

- 每个微任务读完差异、跑受影响与下游的测试；第 1 个请一次独立审查（删除与停止的路径）。
- 第 1 个在私有服务上像 Host 那样跑一次：提交一个微任务，在它运行时 `conclude`，看取消、等待、停止确认、封存与结论；再做一处故障注入，停止一直确认不了时不记结论。微任务要调用真实的模型，这一次付费调用先请开发者批准。
- 安装前完整检查一次；契约改变，从装着的 0.65.1 升级演练一次，看安装发给旧服务的请求与 Host 的视图。
- 契约改变，安装要等黑板空闲，开始前告诉开发者。
