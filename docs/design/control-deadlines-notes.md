# 控制面的时限：范围冲突检查点

本微任务从 `cbb5ffc6` 开始，只完成了初步只读清点与失败传递路径核对。尚未完成全量时限表，未修改源码或测试，也未运行测试。初步搜索的临时结果保存在检出内的忽略目录 `tmp/control-deadlines/initial-search.txt`；该结果含打包的前端资源匹配，不可直接作为完整清点表。第三回合并行只读清点的阶段性结果保存在同目录的 `inventory.md`，明确标注未覆盖全量与尚待复核的映射，不作为验收证据。

第三回合 Host 已授权修改 Router 专用文件 `buddy/roles/router.py`、`router_input.py`、`structured_call.py` 及其他只服务 Router 的文件；`buddy/runtime/` 不受限。当前阻塞点转为仍被明确禁止修改的 `buddy/roles/run_execution.py`：其共用 `discover_models` 入口把一次元数据等待耗尽判为 `HARNESS_UNAVAILABLE`，四个 harness CLI 的模型读取都走它，Claude Code CLI 的可用性还直接依赖它。按“碰到它们再停下说明”的继续指令停止，源码与测试尚未修改。

下表只记录已确认的阻塞点与同一函数内的停止等待，全部位置相对于 `src/hey_my_buddy/`；它不是交付所要求的全量清点表。

| 类别 | 位置 | 时长 | 到点后原来怎样 | 现在怎样 | 为什么 |
| --- | --- | --- | --- | --- | --- |
| 到点就下结论，需要修复 | `buddy/roles/router_input.py:58`，`prepare.git` | 30 秒 | `subprocess.run` 超时，经第 118 行捕获后报 `router-input-unavailable`，最终生成失败回执 | 尚未修改，等待 Host 协调 | Git 未在窗口内返回只能说明输入读取未知，不能证明冻结输入不可用；该读取发生在原生 Router 调用启动前，独立于 ADR-022 的 Router 调用预算 |
| 到点就下结论，需要修复 | `buddy/roles/router_input.py:71–75`，`prepare.blob` | 120 秒 | 下一次读 blob 前发现材料化超过上限，直接报 `router-input-unavailable`，最终生成失败回执 | 尚未修改，等待 Host 协调 | 该条件只有经过时间，没有 Git 退出、错误回复或明确拒绝；材料化慢不能证明冻结输入不可用 |
| 到点转向安全侧，保留 | `buddy/roles/router_input.py:128–131`，`prepare` 的 `finally` | 5 秒 | 关闭输入后等 Git 进程退出，超时就终止自己持有的进程并等待实际退出 | 未修改 | 这里回收自己创建的读取进程并等实际退出，不以等待超时推断进程已停止；它属于执行器回收，按任务要求不改 |
| 到点就下结论，需要修复，当前受限 | `buddy/roles/run_execution.py:780–782`，`discover_models` | 25 秒 | 给四个 harness CLI 的原生模型读取固定执行期限；超时经 controller 结果进入第 804–808 行，判 `HARNESS_UNAVAILABLE` | 尚未修改，等待 Host 协调 | 非模型调用的元数据读取慢不能证明 harness CLI 不可用；该期限不属于 Router 调用预算或收尾回收 |
| 到点就下结论，需要修复，当前受限 | `buddy/roles/run_execution.py:799–808`，`discover_models` | 30 秒 | 外层 controller 未返回就终止；随后缺结果、非零退出或非成功结果统一判 `HARNESS_UNAVAILABLE`，没有区分纯等待耗尽与原生真实失败 | 尚未修改，等待 Host 协调 | 外层等待耗尽不能生成 harness CLI 失效事实，不能用终止动作造成的退出倒推为原生故障；第 800 行的 8 秒回收宽限本身按要求保留 |

失败路径已在源码中核对：`buddy/roles/router.py:59` 调用 `router_input.prepare`；`DecisionAdapter.start` 在第 90 行完成材料化后，才于第 91–93 行记录 Router 启动时间并启动原生调用。材料化异常向外传播到 `buddy/runtime/worker.py` 的 `Worker.execute`，第 581–591 行写入 `status: failed`、`result.status: error` 与 `terminationReason: harness-error`。因此这两个时限不是仅结束一次等待，也不是待保留的 Router 调用硬预算或收尾宽限。

Host 在第二回合允许修改 `buddy/roles/router_input.py`，要求删除独立的 30 秒与 120 秒判失败时限，把冻结输入材料化纳入同一次 Router 调用预算，预算耗尽沿用 `deadline` 与 Router 替换规则，同时继续禁止修改 `buddy/roles/` 里的其他文件。只读核对后确认，仅修改该文件无法完成这个要求：`prepare(manifest, directory)` 没有预算或调用起算时间参数；它的生产调用方 `buddy/roles/router.py:59` 只传冻结输入清单和目录，清单不含 Router 预算。预算位于调用方的 `document['budget']`；冻结输入清单的契约与预算无关，不能把预算伪装成清单字段，也不能从临时路径猜测调用上下文。

外层 Worker 在调用 `worker_start` 前记了 `started`，返回 handle 后才进入预算检查循环（`buddy/runtime/worker.py` 的 `_execute_selected`）。因此该计时包含材料化的经过时间，但同步材料化期间不会运行预算检查。原生 Router 的预算又在材料化完成后经 `ReadOnlyStructuredRequest` 传递，`buddy/roles/run_execution.py:562` 将完整的 `request.budget['timeoutSeconds']` 写给新启动的原生调用。只删除材料化的两处时限，会留下同步读取无预算检查与原生调用重新拿到完整预算的问题，不能满足 Host 要求。

Router 预算的接线权限已经获得：材料化之前建立本次调用的唯一预算起点与截止时间；显式把截止时间交给 `router_input.prepare`；材料化之后把剩余预算交给原生 Router，预算耗尽沿用现有 `deadline` 与替换规则。当前尚未实施，因为随后核查“各 harness 读元数据”这一必做项时发现了受限共用发现入口。

新的失败路径已核对：Codex CLI、ZCode CLI、DSH CLI 的 adapter 的 `discover_models` 均调用 `buddy/roles/run_execution.py` 的同名入口；Claude Code CLI 的 `adapter._probe_native_metadata` 同样调用它，`ClaudeAdapter.available` 捕获其 `BoardError` 后返回 `(False, error.message)`。入口创建的控制文件写死 `timeoutSeconds: 25`；原生 `run_discovery` 将它交给 `execution_deadline`。即使只修改各 harness 的原生读取或 adapter，外层仍在 30 秒终止并报 `HARNESS_UNAVAILABLE`；在调用方吞掉此异常无法区分真正原生错误，会在错误的位置补救，因此必须修改共用入口本身。

需要 Host 协调 `buddy/roles/run_execution.py` 中仅服务元数据发现的 `discover_models` 函数，允许本微任务修改，或由占用该文件的微任务修复并提供源码与验证。修复须分别处理纯等待耗尽、真实原生失败与停止未确认，保留真实停止证据及现有回收宽限，并确保等待耗尽只记未知，后续读取仍能重试。该函数与 Worker 回合运行在同一文件，但它服务四个 harness CLI 的发现，属于本次明确要求覆盖的根因。

测试改动与验证：没有修改测试，没有运行测试。尚未达到可验证的实现；待办点名的偶发测试也尚未逐个核查或进行三次运行。完整 Python 检查、真实 harness CLI 或 app 原生检查、`tests/probes/` 和模型调用均未运行。

与交付要求的差异：当前是按明确范围规则停止的第三回合检查点，清点表、源码修复、行为测试与指定测试结果均未完成。Router 专用文件已获授权，四个 harness CLI 共用的元数据入口仍位于禁止修改的 `run_execution.py`；本回合没有修改源码或测试。除该范围冲突外，尚无已确认的其他偏离；未完成的清点不能作为“没有其他问题”的证据。
