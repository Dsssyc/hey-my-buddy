# Codex 监控子代理与续做修复验收

2026-09-29，从 `socu/buddy-core` 的 `9fcea9a860368d544ab2303c9f5db5e5f3c373bf` 新建分支 `socu/codex-monitor-subagent`，worktree 为 `~/.codex/worktrees/codex-monitor-subagent/hey-my-buddy`。本次保留 contract 0.19.0/schema 14，不做数据迁移；基线中尚未安装的 `USER` 环境修正随本分支保留。日常运行时仍为[已安装的 0.19.0](installed-0.19.0.md)，本次没有安装、升级、重启日常服务，也没有修改用户配置或发布。

## 实现

`skills/buddy/SKILL.md` 与 [usage](../reference/usage.md#waiting-from-codex) 将 Codex Host 的默认等待改为每个运行中委派一个监控子代理，派生时显式设置 `gpt-6-luna / low` 和空历史，覆盖本机 `[agents]` 的 GPT-6 Sol max 默认。监控只执行同一 `runId` 的 `await`，将 brief 结果原样交回，不读仓库、配置或记忆，不执行任何 Host 决策或验收命令。父代理继续独立工作，在需要结果时等待该监控；监控失败、权限不可用或自身超时时回退前台 `await`。文档区分 Buddy 等待窗口、命令工具 yield 和父代理 wait-tool 超时，说明沙盒外权限必须覆盖子代理，Claude Code 后台 Bash 流程不变。

`342b2a1` 修复结构化报告被误拒的问题：结果摘要使用既有的 64 KiB serialized outcome 总预算，其他请求字段仍限 8,000 UTF-8 字节，数组仍限 32 项，引用仍限 4,096 字节。Python 与 DSH 校验器、ZCode 工具 schema 和 Codex schema 说明保持一致；不截断有效报告，不放宽 completed/request 关系、重复 JSON 键或非 JSON 尾部规则。Codex runner 同时保留具体、受限的校验错误原因。

`a23c5e9` 将原生回合完成证据与业务 outcome 校验分开；`cbec057` 补上原生恢复在模型启动前被拒后，再次显式重建时继续携带已保存助手消息的情况。Controller 在已完成的原生回合结束且原生进程退出为 0、停止确认后保存私有绑定，即使 outcome 格式失败也保留可供显式续做的 checkpoint；业务 attempt 仍是失败。服务校验前一 attempt、generation、turn、input digest、配置、harness 版本及停止证据，Controller 再校验目标、checkout、配置、上次 attempt/input/turn 绑定与 `thread/read` 最新原生回合。原生失败、缺少完成事件、变更历史或未确认停止不能取得原生恢复权限。重建输入携带已观测的最后一条根助手消息、原始字节数/摘要和截断标志，并明确它不是新的 Host 指令或已验收成果。文本与其 JSON 字符串各限 64 KiB；没有已观测消息时不捏造。历史收据不追溯改写。

## 原故障复现

run `9cc4223f-4f3f-4205-aeb4-cdc88b6e4939` 的第一轮原生会话为 `01a0eafc-46c0-72a2-b620-df11d2c5ee23`，原生 turn 为 `01a0eafc-4816-7461-98ab-ae00401939bd`。原始最后消息是完整 JSON，共 9,797 UTF-8 字节，`summary` 占 8,848 字节；去掉字符串内部的 `<oai-mem-citation>` 后仍有 8,672 字节。旧解析器返回 `the turn outcome requires a valid disposition and nonblank summary`，runner 将它隐藏为泛化的 strict structured final outcome 错误。真正原因是旧的 8,000 字节摘要限制，记忆标记不是 JSON 语法错误；因此不关闭 memories，也不剥离合法字符串内的引用。JSON 文档外额外附加引用仍拒绝。

原始收据确认原生退出码为 0 且停止已确认；旧代码只有校验成功并生成 turn record 后才写私有 native binding，服务也只从 `concluded` turn 选择 native resume。因此已经完成、已保存研究内容的原生线程未被登记为可恢复，下一轮变成 `reconstructed-new-session`。这属于接入层丢失恢复证据，不能据此认为 Codex 无法恢复这轮。新代码对原始消息做离线 replay，解析结果与原 JSON 完全一致且保留引用；控制器/服务协议夹具验证了失败后显式 continue 恢复同一个线程并产生 native turn 2。没有付费重跑这个历史委派。[原始消息指纹与收据摘要](evidence/codex-outcome-9cc4223f.json)记录了验证范围。

## 经用户逐次批准的监控实测

全部 Buddy 实测使用私有根 `/private/tmp/buddy-monitor-probe-jn8_gplj`，包含独立 state、runtime、skill 安装目录和只读 fixture。监控仍调用规定的绝对入口 `~/.agents/skills/buddy/scripts/buddy`，通过显式私有 state/runtime 附着私有服务。私有测试板只启用了日常已启用的 `dsh:deepseek-official:deepseek-flash:off`；未改日常模型目录或设置。没有 Claude 调用，没有新 Router 调用，没有自动续做或付费重试。

第一次批准包含一个 DeepSeek Flash off Worker 委派和一个 Luna low 监控会话。run `c0342e2e-9210-471d-b540-2442dd72f688` 只读取 `marker.txt` 并用中文原样报告内容；一个本地 `sleep 45` command 先占用唯一执行槽，以便观察排队。Worker 原生执行 4.1 秒，包含排队与服务开销为 50.195 秒，2 次模型请求；固定输出 `2aabdd2e74e08bc4da1b5c7737a5c554fa9ad7d3` 的 marker 与输入一致，changedPaths 与 diff 均为空。第一个监控开始执行 await 时 Worker 已完成，所以 await 的实际等待为 0 秒，只证明模型覆盖、权限与结果交回，不能作为长等待证据。

用户另行批准第二个 Luna low 监控会话，监控本地 `sleep 120` command run `e4d85088-df6a-4543-8cf4-4522a8d97f6e`，不再调用 DeepSeek。监控的 await 实际等待 79.2 秒，最终返回 completed 与停止确认。父代理先调用 10,000 ms 的 `collaboration.wait_agent`，实际 10.017 秒后返回 `timed_out: true`；继续等待同一子代理 60,000 ms，实际 60.007 秒后仍超时。两个超时都没有取消子代理或 Buddy 任务，没有创建替代任务，随后收到同一子代理的完成通知。

| 执行 | 模型请求数 | 原生输入 token | 其中/另计缓存输入 | 输出 token | 总 token | 原生执行/监控会话耗时 | 监控 await 实际等待 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DeepSeek Flash off Worker | 2 | 9,512 | 另计 11,136 | 251 | 20,899 | 4.1 秒 | 不适用 |
| Luna low 监控 1 | 2 | 88,971 | 其中 47,616 | 741 | 89,712 | 21.643 秒 | 0 秒 |
| Luna low 监控 2 | 4 | 178,196 | 其中 152,576 | 427 | 178,623 | 97.364 秒 | 79.2 秒 |

DSH 的 `inputTokens` 不含 `cacheReadTokens`；Codex 的 `input_tokens` 包含 `cached_input_tokens`，表中未重复相加。原生用量来自 DSH 私有压缩 session 记录和两条 Codex 子代理 rollout 的累计 `token_count`。它们是原生报告的用量，不是独立账单核验；没有记录美元费用。空历史子代理仍载入工具与基础指令，每次请求约 4.4 万输入 token，本次不能宣称总 token 或费用必定减少。第二个监控通过命令工具维持同一 await 进程；外层 code-mode 默认 yield 提前返回，额外产生了一次等待调用，因此指引要求外层窗口覆盖内层等待。

两条子代理的 `turn_context` 都记录 `model: gpt-6-luna`、`effort: low`、`approval_policy: never`、`sandbox_policy.type: danger-full-access` 和 `multi_agent_version: v2`；其原生 session 版本是桌面内置的 `0.158.0-alpha.2.1`。本次有效的是 V2，不需要改功能开关。另行运行的 npm Codex CLI 0.157.0 才报告 `multi_agent=true`、`multi_agent_v2=false`，不能用它的配置推断桌面 Host。模型与档位在原生配置中确认，远端实际服务模型没有独立证明。

当前暴露的 V2 `wait_agent` 工具声明默认 30,000 ms、最小 10,000 ms、最大 3,600,000 ms。本次实测 10,000 和 60,000 ms，没有实际等待一小时。父代理原生日志中，两段 call/output 时间区间内的模型助手 response item 与 token_count 事件均为 0；请求等待与处理返回仍产生模型调用及输入/输出开销。发起这两次等待的父模型请求分别报告输入 210,023/210,069 token（其中缓存 209,408/209,792），输出 21/137 token；这说明等待区间不持续采样，并不等于整个协调过程免费。未将其他源码工作回合计入本次监控用量。这是本次观测结论，不扩展为所有 CLI/桌面版本的固定限制或提供商计费保证。[精简实测证据](evidence/codex-monitor-20260929.json)保存 session/run 身份、时间戳、计数和指纹。

Host 检查固定产物后记录 integration not required，并接受了唯一的 Worker 委派；两个本地 command 夹具也已检查并记录验收。两条监控子代理均已完成。私有服务经自己的 launcher stop 与 test teardown 确认无存活进程/持有锁，私有根已删除；只读 fixture 为测试创建的 existing checkout，不是待清理的托管 worktree。精简证据已提交，原始本地结果与实验脚本保存在忽略的 `tmp/codex-monitor/`。

## 验证

摘要回归测试先在旧实现下失败，再在修复后通过；原始 9,797 字节消息离线 replay 成功。摘要修复的 Python 聚焦组 33 项通过，DSH Node 聚焦组 31 项通过。原生恢复回归测试先因缺少 checkpoint 失败，再通过；服务/工作流聚焦组 86 项通过。最终 Codex 控制器、协议与服务续做组 40 项通过，覆盖原生失败、格式失败、断流、错误 attempt 绑定、原生历史变化、harness 版本变化、最后助手消息传递与截断；包含真实 Python controller/mock App Server 跨进程执行到 service continue 的集成路径，不调用模型。补充的恢复拒绝链路在旧 begin_turn 实现下复现消息丢失，修复后的 6 项续做测试全部通过。

首次全量检查通过 1,362 项 Python（1,133.736 秒）与 139 项 Node 测试；它在 `a23c5e9` 上启动，期间补入 `cbec057`，不作为最终冻结候选的全量证明。最终在冻结提交 `9b2d37dbab341ce96cfcdce0d9b3b125f0374043` 重新执行 `uv run --frozen python -m buddy.checks`：1,363 项 Python 测试通过（1,121.021 秒），139 项 Node 测试通过（27.556 秒），退出码 0。两次均使用独立 state/runtime/temp 根，最终根 `/private/tmp/buddy-checks-8zbv8pz3` 已由 harness 确认无持有锁、无存活进程后删除。[检查证据](evidence/codex-monitor-checks-20260929.json)包含冻结提交、计数和日志 SHA-256；收尾提交只更新验收文档与证据，不再改变运行时代码。

两次完整检查的私有首次安装用例各产生一条 subprocess `ResourceWarning`，最终一条涉及 PID 84922；它发生在安装交接启动的子进程对象回收时。检查退出码均为 0，最终私有根清理确认进程已停止且没有残留；没有 SQLite 连接警告。本次未扩展修改该非阻塞警告。

共享 skill 构建通过，生成物包含新的 Codex 监控指引；改动 Markdown 的本地文件链接与 `git diff --check` 通过。未改前端代码，未另跑浏览器或前端专用测试。

## 未验证项与下一步

没有新付费 Codex Worker 原生续做探针；修复依赖原故障原始消息 replay、收据/源码诊断和协议夹具回归。没有 Windows/Linux 真机监控或恢复验证，没有实际测试一小时 wait 上限，没有验证低权限子代理交互审批路径，也没有验证 Host 关闭后的自动唤醒。没有启用/修改任何 Codex feature、默认子代理配置或用户模型偏好；低权限场景依照[官方子代理权限说明](https://learn.chatgpt.com/docs/agent-configuration/subagents#approvals-and-sandbox-controls)与现有精确启动器放行指引处理。

全量检查已通过，建议将本分支合并到 `socu/buddy-core`，在空闲状态下经用户单独授权安装包含 `9fcea9a` 登录用户名修正和本次改动的同版本运行时，再重新载入已安装 skill。源码提交、已验证私有测试和日常安装是独立事实；本记录不授权合并或安装。

## 本地安装候选

`uv build --wheel` 从冻结提交 `9b2d37dbab341ce96cfcdce0d9b3b125f0374043` 构建本地 `tmp/codex-monitor/dist/hey_my_buddy-0.19.0-py3-none-any.whl`（1,608,083 字节）。内嵌 `build-info.json` 保留完整 sourceCommit；已解包核对包含 `USER` 修正、监控 skill 与恢复拒绝后的上下文传递。SHA-256 为 `82557d99ad229a22f085c6c51043f9c138e79d83a85f2e0cc4ab48574bc050e3`，同目录 `SHA256SUMS` 可用于核对。包未安装到日常服务、未发布；安装仍需用户授权，之后应重新载入已安装 skill。
