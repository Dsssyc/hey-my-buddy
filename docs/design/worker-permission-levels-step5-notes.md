# Worker 权限档位第 5 步：范围阻塞说明

2026-10-13，在输入提交 `2aded1c5` 的受管检出核对 DSH 的三档预设、Worker 载体、续接绑定和拒绝操作的结果路径。本回合停在第 5 项的写入范围边界，尚未修改实现或测试；本文是阻塞调查，不是实现完成或 Host 验收记录。

## 已核对的当前实现

`dsh/native_run.py::tool_scope_launch` 按工具范围选启动配置：read 设置 `DSH_PERMISSION_MODE=read-only`，write 不设置权限变量，none 禁用已确认的工具行与 plan-mode。`run` 仍拒绝 full，按是否提供 `SessionServices` 选择受管 Worker 载体；`_effective_policy` 仅给 read 记录 permissionMode，write 的请求策略为空。目标是 Worker 三档分别显式设置 read-only、workspace-write、danger-full-access；带 Worker permission 的回合应当选择同一个受管 Worker 载体，Router 快速调用保持原有配置。

`_goal_binding` 只含任务、会话、工作目录与供应方、模型、推理强度，不含权限档位。目标实现可以保持这份绑定，初次、重建与续接每次启动都从本回合的档位取得原生预设，无须预先增加换档新开会话逻辑。实际续接是否采纳新进程的预设仍需 Host 量。

## 第 5 项需要范围外修改

`dsh/native_run.py::_AttentionPolicy.decide` 当前只在注意记录里保存时间、选择的拒绝 outcome 与 basis，basis 是 reject_once 等拒绝依据，没有工具名或命令、路径摘要。`dsh/acp/connection.py` 的 permissionDecisions 保存 toolCallId、标题元数据、选项和拒绝依据，也没有输入摘要；这些记录都没有填入 Worker 结果的 request.attempted。

结束结果由 `buddy/roles/worker_services.py::call_tool` 接收 Worker 的参数，`_finish_receipt` 将原参数作为 outcome 签发 receipt。completed 遇到注意记录时只返回通用的 ATTENTION_REFUSAL，要求 Worker 描述请求；attention 或 assistance 的参数随即原样签发。`dsh/protocol.py::RootTurnEvidence` 验签这份 receipt，`dsh/native_run.py::_value_and_completion` 直接把 receipt.outcome 投影为返回结果。离线假 DSH 的当前注意结果也只写 attempted 为 “the bounded fixture work”。因此现有实现没有满足第 5 项。

需要 Host 将 `src/hey_my_buddy/buddy/roles/worker_services.py` 纳入本微任务的写入范围，或由 Host 在该文件完成签发前的拒绝操作事实补充：DSH 在当前回合的注意记录中保存工具与命令或路径的有界摘要，共用结束工具在签名之前将其写入现有 request.attempted。摘要按 UTF-8 字节限制到 512 字节，不新增结果字段；结束 receipt 与返回 outcome 保持一致。实现还需覆盖带与不带 inquiry 的注意记录路径。

本回合没有通过修改已验签结果、另造会话工具载体或只改假 DSH 的输出来补这项要求，也没有新增拒绝条件。尚未发生实现替换，所以没有删除代码、测试或已有文档段落；registry 的 DSH 行和相邻 harness 行均未改。

## 验证与 Host 后续核对

本回合只读源码与任务指定的决定、计划和第 2 步说明，未运行测试，未启动真实 harness、启动器、服务或 Worker，未访问用户配置、凭据或日常看板。指定的 DSH、harness 顶层、roles、routing 与两个 workflow_worker 测试文件均尚未运行；获得范围扩展并完成实现后，按 Host 指定的只读解释器逐文件独立进程执行，并对需要共享内存、进程枚举或真实启动器的用例逐项记录，由 Host 补跑。

Host 在真实 DSH 上仍需逐档核对文件在检出内、检出外与临时目录的写入，以及网络、本机端口、Unix 套接字、共享内存和进程枚举；同时核对 read→write、write→full 的原生续接是否使用新预设、只读 Worker 的结束工具与问询是否正常、被回绝的提权操作是否带着工具和至多 512 字节的输入摘要进入 request.attempted。假 DSH 的环境记录只能证明参数交付，不能证明原生权限效果。
