# 控制面的时限：范围冲突检查点

本微任务从 `cbb5ffc6` 开始，只完成了初步只读清点与失败传递路径核对。尚未完成全量时限表，未修改源码或测试，也未运行测试。初步搜索的临时结果保存在检出内的忽略目录 `tmp/control-deadlines/initial-search.txt`；该结果含打包的前端资源匹配，不可直接作为完整清点表。

开发者要求“到点只记未知，不改状态、不判失败”，同时要求“要改 `blackboard/tasks/workflow.py`、`blackboard/store/` 与 `buddy/roles/` 就先停下告诉 Host”。已发现 `buddy/roles/router_input.py` 中两个仅因等待耗尽就判执行失败的根因，位于并行微任务占用的目录，因此在修改之前停止，等待 Host 协调该文件的写入归属或提供已修好的实现。

下表只记录已确认的阻塞点与同一函数内的停止等待，全部位置相对于 `src/hey_my_buddy/`；它不是交付所要求的全量清点表。

| 类别 | 位置 | 时长 | 到点后原来怎样 | 现在怎样 | 为什么 |
| --- | --- | --- | --- | --- | --- |
| 到点就下结论，需要修复 | `buddy/roles/router_input.py:58`，`prepare.git` | 30 秒 | `subprocess.run` 超时，经第 118 行捕获后报 `router-input-unavailable`，最终生成失败回执 | 尚未修改，等待 Host 协调 | Git 未在窗口内返回只能说明输入读取未知，不能证明冻结输入不可用；该读取发生在原生 Router 调用启动前，独立于 ADR-022 的 Router 调用预算 |
| 到点就下结论，需要修复 | `buddy/roles/router_input.py:71–75`，`prepare.blob` | 120 秒 | 下一次读 blob 前发现材料化超过上限，直接报 `router-input-unavailable`，最终生成失败回执 | 尚未修改，等待 Host 协调 | 该条件只有经过时间，没有 Git 退出、错误回复或明确拒绝；材料化慢不能证明冻结输入不可用 |
| 到点转向安全侧，保留 | `buddy/roles/router_input.py:128–131`，`prepare` 的 `finally` | 5 秒 | 关闭输入后等 Git 进程退出，超时就终止自己持有的进程并等待实际退出 | 未修改 | 这里回收自己创建的读取进程并等实际退出，不以等待超时推断进程已停止；它属于执行器回收，按任务要求不改 |

失败路径已在源码中核对：`buddy/roles/router.py:59` 调用 `router_input.prepare`；`DecisionAdapter.start` 在第 90 行完成材料化后，才于第 91–93 行记录 Router 启动时间并启动原生调用。材料化异常向外传播到 `buddy/runtime/worker.py` 的 `Worker.execute`，第 581–591 行写入 `status: failed`、`result.status: error` 与 `terminationReason: harness-error`。因此这两个时限不是仅结束一次等待，也不是待保留的 Router 调用硬预算或收尾宽限。

Host 在第二回合允许修改 `buddy/roles/router_input.py`，要求删除独立的 30 秒与 120 秒判失败时限，把冻结输入材料化纳入同一次 Router 调用预算，预算耗尽沿用 `deadline` 与 Router 替换规则，同时继续禁止修改 `buddy/roles/` 里的其他文件。只读核对后确认，仅修改该文件无法完成这个要求：`prepare(manifest, directory)` 没有预算或调用起算时间参数；它的生产调用方 `buddy/roles/router.py:59` 只传冻结输入清单和目录，清单不含 Router 预算。预算位于调用方的 `document['budget']`；冻结输入清单的契约与预算无关，不能把预算伪装成清单字段，也不能从临时路径猜测调用上下文。

外层 Worker 在调用 `worker_start` 前记了 `started`，返回 handle 后才进入预算检查循环（`buddy/runtime/worker.py` 的 `_execute_selected`）。因此该计时包含材料化的经过时间，但同步材料化期间不会运行预算检查。原生 Router 的预算又在材料化完成后经 `ReadOnlyStructuredRequest` 传递，`buddy/roles/run_execution.py:562` 将完整的 `request.budget['timeoutSeconds']` 写给新启动的原生调用。只删除材料化的两处时限，会留下同步读取无预算检查与原生调用重新拿到完整预算的问题，不能满足 Host 要求。

需要 Host 再协调 `buddy/roles/router.py` 的写入归属，允许修改 `prepare_router_review` 与 `DecisionAdapter.start` 的预算接线，或由占用该目录的微任务提供这段接线。必要的接线是：材料化之前建立本次调用的唯一预算起点与截止时间；显式把截止时间交给 `router_input.prepare`；材料化之后把剩余预算交给原生 Router，预算耗尽沿用现有 `deadline` 与替换规则。`router_input.py` 才能据此把 Git 等待绑定在调用预算上，删除独立的 30 秒与 120 秒时限，并保留真实 Git 错误与冻结输入校验。当前不修改接口或调用方，按“做不到就停下说明原因”的继续指令停止。

测试改动与验证：没有修改测试，没有运行测试。尚未达到可验证的实现；待办点名的偶发测试也尚未逐个核查或进行三次运行。完整 Python 检查、真实 harness CLI 或 app 原生检查、`tests/probes/` 和模型调用均未运行。

与交付要求的差异：当前是按明确范围规则停止的第二回合检查点，清点表、源码修复、行为测试与指定测试结果均未完成。`router_input.py` 已获得写入授权，但唯一预算的传递还需要尚未授权的 `router.py` 接线；本回合没有修改源码或测试。除该范围冲突外，尚无已确认的其他偏离；未完成的清点不能作为“没有其他问题”的证据。
