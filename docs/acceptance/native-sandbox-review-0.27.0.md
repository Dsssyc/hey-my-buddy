# Codex 审阅证明落在原生沙盒（0.27.0）

基线为 buddy-core 的 `5fed0e1`（日常安装 0.26.0 之后），本调整在独立受管 workspace 完成。源码与契约升级为 0.27.0，schema 保持 15。范围只调整 Codex 审阅验证的判定归属；没有调用模型、没有联网、没有安装或修改用户配置，也没有读写日常状态目录。判定逻辑实际位于 `src/buddy/review_probe.py` 与 `src/buddy/review_replay.py`（任务描述把它归到 `review_check.py`/`review_evidence.py`），本次按用户 2026-10-01 同意的方案最小限度修改了这两个文件，其余改动都在授权 writeScope 内。

## 判定调整

模型自己的工具流改为只做结构核对：请求与完成记录按线程、允许回合和调用／条目 ID 及类型一一配对，允许类型限于 exec 函数／自定义工具与原生 `commandExecution` 项；额外、重复或被禁止的工具、无完成记录的调用、截断、预算超限与任何审批请求仍失败。不再解析或解读模型工具的输出内容，也不再要求模型结果与固定探针一致；输出信封解析成功与否仅作为证据里的辅助诊断字段保留。

`boundaryDenials` 与 `internalRead` 完全落在控制器固定的五个原生 `command/exec` 探针上：同一私有 `permissionProfile:"buddy-router"` 下内部 marker 正读、越界读、仓库内写、越界写与联网拒绝（含 Host 在场的网络正对照）即构成证明，外部 sentinel 与受管输入快照不变、请求身份、预算、一次格式纠正、双层停机证明与证据脱敏规则均不变。探针执行、绑定与拒绝识别的代码（`sandbox_probe.py`、`codex_runner.py`）没有改动。

证据格式升为 `buddy-review-evidence-v3`：结构不变，逐项 basis 记录结构流事实与探针推导的边界事实，删除已废止的模型一致性事实；读取端兼容 v1、v2、v3。历史重放按同一新规则重导工具流、策略读回与探针结论，运行期才能观测的事实（正对照、快照、停机、预算、请求身份）沿用保留的 basis 事实，新增 `checksPass` 与 `boundaryDenials` 两个只读结论键，`certifiable` 恒为 false。

## 离线重放结果

`b37e7302` 的 v2 证据（attempt `187f5aef`）复制为 `tests/python/fixtures/review-evidence/luna-v2-b37e7302.json` 后离线重放：五次 exec `custom_tool_call` 输出全部不可解析（`responseReadable:false`）但类型与调用关联完整，两个 `commandExecution` 高层条目闭合，五个固定探针全部拒绝／正读成功；新规则下 `checksPass:true`，但 `REPLAY_IS_NOT_CERTIFICATION`——只有新的一次显式授权真实验证才能签发证书。原目录只读未改。

Sol v1（含同 run 补充摘要）与 Luna v1 两份无固定探针的旧证据重放结论不变：工具流可关联、策略有效、marker 读取与四次拒绝作为辅助观察保留，仍返回 `HISTORICAL_PROBE_BINDING_MISSING`，不按顺序、退出码、长度或模型回答补造类别；Sol v1 不带补充摘要时输出不可读，按新规则同样只判为可关联流，仍不可认证。

模拟用例覆盖：输出不可读但类型与关联完整时九项全过；`commandExecution` 按条目 ID 关联时通过；不可解析输出与 `commandExecution` 条目并存（0.159.0 Luna 真实形态）时九项全过；模型宣称成功或拒绝均不能改变边界判定，只有探针记录变化才能翻转 `boundaryDenials`/`internalRead`；被禁止的工具、缺失完成记录、调用 ID 错位、审批请求、截断、额外工具与任一固定探针失败（探针成功化、去拒绝标记、截断、缺操作）都必须失败。

## 验证结果

聚焦的审阅探针、沙盒探针、历史重放、Codex 控制器与审阅准入测试通过（test_review_probe 21 项、test_sandbox_probe 13 项、test_harness_review 与 test_codex 52 项）。收尾先运行 `npm --prefix apps/console ci`（Node 24.21.0，通过，仅 fsevents install-scripts 提示），再运行完整 `uv run --frozen python -m buddy.checks`，退出码 0；详细数字见下段。原始输出保存在本 workspace 忽略的 `tmp/review-adjust/`，未提交原始日志。

完整检查：1833 项 Python 测试通过（1356.227 秒，1 项跳过），165 项 Node 测试通过（15 个 suite）。后台套件启动早于最后两处小修正（重放 use_raw 镜像修正与 v3 断言），最终代码的聚焦模块复跑 34 项全部通过。测试全部使用测试自建的私有 BUDDY_STATE_DIR 与 BUDDY_RUNTIME_ROOT，并清除继承的运行时、Worker、凭据与 harness-selection 环境。

## 未验证边界

Windows 真机、真实付费调用与本批未发起的新真实验证均未运行；0.159.0 在本批之后仍无已签发证书。按用户方案，下一次真实验证需由 Host 转达用户再次批准后执行，本任务不自行发起。日常安装仍为 0.26.0/schema 15；本源码 0.27.0 的安装、合并与真实验证均待另行授权。
