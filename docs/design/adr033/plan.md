# ADR-033 的执行计划：宏任务的暂停、继续与结束

## 依据

- [ADR-033](../../decisions/033-macro-task-pause-resume-end.md)（2026-11-11 开发者定）。
- 版本 0.69.0：契约与存储形式都变（schema 40 → 41）。安装要黑板空闲，并先做备份。

## 现状（2026-11-11 读代码核对）

- **控制台的停止只有 `macro_task_stop`**（`blackboard/tasks/workflow.py` 的 `stop_macro_task`）。
  - 可以指名一个宏任务，也可以用 `run:<id>` 指名一个独立微任务。
  - 只取消进行中的根及其树：在跑的强制停下（`stop_initiator='console'`，不给宽限），排队与路由中的直接取消；等 Host 与已交付的不动。
  - 不写结论，不回收检出。
- **Host 的 `cancel` 会有序收尾，但只在 harness 的 `interrupted_resume` 为真时**（Codex、DSH），宽限是 `WRAP_UP_SECONDS = 300`。之后回合记成已取消，续接在 DSH、ZCode、Claude Code 上都凭记录重建。
- **被记成 `interrupted` 的回合，续接时 Codex 与 DSH 接回原会话**（`test_interrupted_continuation.py`），ZCode 与 Claude Code 重建。`interrupted` 现在只在服务恢复时、执行进程已死的情况下写下。
- **harness 的能力**（`buddy/harnesses/*/adapter.py`）：

  | harness | `native_resume` | `interrupted_resume` |
  |---|---|---|
  | codex | True | True |
  | dsh | True | True |
  | zcode | True | False |
  | claude | False | False |

- **宏任务没有存下来的状态**，显示的状态每次读时推出（`blackboard/tasks/macro_tasks.py`）。
- **服务接纳排队的工作时，按任务的 `queue_reason` 挑选**（`blackboard/service/service.py` 的 `admit_execution`）。
- **黑板自己发起的续接已有先例：** helper 结束后续接父任务（`workflow/auto`）、唤醒 Lead、原生会话接不上时改为重建。
- **读代码推出、还没复现的缺陷：** 微任务排着 Host 已记下的续接时被停下，那次续接被取消，它答的结果却仍标着已答复；之后的 `continue` 要么重放旧回复，要么冲突，只剩 `conclude` 能用（`_cancel_owned_run`）。

## 微任务 1：服务、存储与 Host 面

1. **存储**（新一步 40 → 41，自带定义）：
   - 新表 `work_pauses`：每个暂停的单位一行，写着暂停的时刻。单位是一个宏任务或一个独立微任务，两个外键恰好有一个不为空。
   - 回合的结束原因加 `pause`，任务的排队原因加 `paused`。
   - 没有存量要转换。现在因控制台停止而取消的微任务照旧，结束可以处理它们。
2. **控制台操作：** `macro_task_stop` 换成 `macro_task_pause`、`macro_task_resume` 与 `macro_task_end`。指名方式不变（宏任务或 `run:<id>`），各带自己的 `commandId` 回执。`macro_task_stop` 与它的检查一并删去。
3. **暂停：**
   - 在跑的回合用 harness 自己的中断停下（Codex 的 `turn/interrupt`、DSH 的 ACP cancel），不走总结回合。回合结算之后记成 `interrupted`，带上原生会话能否接回，部分成果照旧封存。
   - ZCode 与 Claude Code 直接强制停下，记成不能接回的中断。
   - 宽限 `WRAP_UP_SECONDS`，到时仍没结算就强制停下。
   - 进行中的 Router 调用也停下，决定作废，继续时重新路由。
   - 每个被中断的微任务，黑板记下一次续接，暂时挡住：输入是 `buddy/roles/texts/` 里的一段固定文字，发起方是控制台。这个微任务仍算执行中，`workflow_runs.state` 不像 `cancel` 那样变成 `cancelled`，也不算等 Host 处理。
   - 单位里排队的任务改为 `paused`；暂停期间新提交的与 Host 记下的续接，同样排在 `paused`，回复里写明挡住了。
   - 等 Host 与已交付的不动。
4. **继续：** 删去暂停那一行，`paused` 的任务回到原来的排队原因。挡住的续接照现有规则开始：能接回原会话的接回，不能的重建。
5. **结束：**
   - 在跑的强制停下，排队与路由中的直接停下。
   - 停止确认之后，给每个没了结的微任务记结论：排队、路由中、在跑、等 Host 处理，以及已取消或失败而没有结论的。发起方是控制台，结论写明是用户在控制台结束的。
   - 已交付的不动。
   - 停止确认不了的，不记结论，回复里如实写出。
   - 检出与会话记录照现有规则回收。
6. **Host 面：**
   - `get` 的宏任务视图加 `paused`（`pausedAt`，没有暂停时为 null）。
   - 微任务因暂停而中断时，`await` 返回一次 `paused`；暂停不算等 Host 处理，顶栏的计数不算它。
   - `help` 与原因码随之补齐，原因的中英文文案要开发者先看过。
7. **事件：** `macro_task.paused`、`macro_task.resumed`、`macro_task.ended`，各带发起方与单位。
8. **缺陷：** 先写测试复现上面那一处，再在根因处修；复现不了就报告，不修。
9. **测试守：**
   - 暂停之后 Codex 与 DSH 的续接选 `native-session`，ZCode 与 Claude Code 选重建（用夹具 harness）；
   - 暂停期间不接纳这个单位的任何执行，别的单位照常；
   - 继续之后，挡住的续接与排队的工作都开始；
   - 结束给每一种没了结的微任务写结论，已交付的不动，停止确认不了的不记；
   - 升级夹具板到 41 之后与新板一致；
   - 服务重启之后，暂停仍然有效。

## 微任务 2：控制台

1. 宏任务页头与独立微任务的“更多”里，“停止宏任务”换成“暂停”“继续”与“结束”。暂停可以撤销，不设确认；结束要确认，对话框照现在停止的写法列出范围。
2. 宏任务列表与页头显示“暂停中”（还有执行没确认停下）与“已暂停”（带暂停的时刻）。时间轴上，被暂停中断的回合照有序收尾的画法标出结束。
3. 文字走 i18n，只加开发者定过的那几处。
4. 被取代的停止对话框、文案与检查一并删去。
5. 服务补两项视图事实（2026-11-11 第二个微任务开工时发现：第一个微任务的视图只有 `pausedAt`，被暂停的微任务仍是 `executing`，计数分不出“暂停中”与“已暂停”；这是计划漏掉的，不是第一个微任务的缺陷）：
   - `paused` 改为 `{pausedAt, shutdownConfirmed}`：单位里的每个执行，连同 helper、Lead 名下的微任务与它们的 Router 调用，都已证明停下时为真。停止证明只用 `unconfirmed_stops` 这一套定义。
   - `counts.unfinished` 是结束会了结的成员数，取代只为旧停止对话框而设的 `stoppableRoots`、`stoppableHelpers` 与 `awaitingHost`。
   - 动作的回复不冒充视图：暂停的回复只带 `pausedAt`，继续的回复不带 `paused`。

## Host 做的

- 参考文档：`console.md`、`workflow.md`、`macro-tasks.md` 与 Host 指南（用户让 Host 暂停时，用 `inquire` 加 `continue`）。
- 版本 0.69.0，`CONTRACT_VERSION` 与签名。
- 演练：在导出的样本上，空 HOME、经启动器、私有服务；注入故障，升级中途失败时黑板留在 40。
- 安装要黑板空闲。

## 验收

- **存储与升级：** 改了存储，按高风险验收：演练与几处故障注入。
  - 暂停时停止证明不了，就不显示已暂停；
  - 暂停之后重启服务，暂停仍然有效；
  - 结束时停止证明不了，就不记结论。
- **真实 harness：** Codex 与 DSH 各跑一个小微任务，暂停之后继续，确认接回了原会话（`resumeMode: native-session`）。这要调用模型，要开发者逐次批准。
- **控制台：** 前端全量、浏览器全量，真实数据回放的像素，深浅两色、中英文都看。

## 不做的

- 一键暂停黑板上全部在跑的宏任务。
- Host 的暂停动词（ADR-033 第 5 条）。
