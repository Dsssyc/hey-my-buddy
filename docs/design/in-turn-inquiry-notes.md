# Codex 与 Claude Code 的回合内问询

2026-10-16，基于 `c605b127` 的源码实现记录，等待 Host 验收；本微任务没有安装产品，也没有调用真实模型。

## 送达、答复与结束

Codex CLI 使用正在执行的根线程和原生回合号调用 `turn/steer`，明确传入 `expectedTurnId`，并核对返回的 `turnId`。Claude Code CLI 向正在执行的 `stream-json` 输入写入一条用户消息。两条路径都只在原生回合活动期间发送，沿用 `InquiryBridge` 的排队、送达、已答、作废和不可用状态以及现有字节、条数上限；回合尚未活动或已经结束时，桥按原规则返回 `not-ready`，由已有服务规则决定问题后续的状态，不开启新回合，也不延长截止时间。根回合结束后，桥将尚未回答的条目记为不可用；子代理的生命周期不改变根回合的问询窗口。

两边的答复都经过现有会话 MCP 服务的 `hey_my_buddy_answer_inquiry`。服务只读取同一个问询日志，返回现有格式的签名回执；原生驱动核对根会话、原生回合或这一次单回合 CLI 运行、工具调用号与工具名称，再由共用的签名校验核对微任务、尝试、代次、业务回合、问题编号、问题散列和答复正文。错误、截断、子代理、外来会话、错误调用号和助手的普通文字不能形成已答状态；同一原生调用的回执重放仍须经过签名和答复冲突校验。工具名称只在本次会话的命名空间内有效。

角色层从经过文件散列验证的 `inquiry-report` 读取 `unansweredDelivered`，把送达后仍未回答的结果转成 `attention`，并在来源记录中留下 `unansweredDeliveredInquiries`。已有 assistance 请求的内容保留，处置改为 attention；原生回合的成功、问询答复和 Host 对工作成果的验收仍是分别记录的事实。

## MCP 挂载与能力声明

Codex CLI 使用 `thread/start` 或 `thread/resume` 的按次 `config.mcp_servers` 挂上会话 MCP 服务，没有采用实验性的客户端自带工具，也没有改写用户配置文件。挂载仅公开 checkpoint 和答复工具，完成结果仍走原生输出 schema。无模型探针见 `tests/probes/codex_inquiry_mcp_mount.py`：在私有 HOME 和 CODEX_HOME 中预置一个 MCP 服务，再按线程增加问询服务，通过 `mcpServerStatus/list` 分别检查两个服务。Codex CLI 0.162.1 实测两个服务均为 `runtimeStatus: connected`，都列出了 `hey_my_buddy_checkpoint` 和 `hey_my_buddy_answer_inquiry`，线程的模型回合数为 0；这同时核对了按次增加服务不会清除已有服务。

Claude Code CLI 通过按次 `--mcp-config` 增加同一个会话服务，并用 `--allowedTools` 放行本次命名空间里的两个工具；`--tools` 中只增加内置的 ToolSearch，不预先加载工具或修改工具搜索设置。保留原有配置来源，没有使用 `--strict-mcp-config`。本机 Claude Code CLI 2.1.299 的 `--help` 和 `--version` 在私有配置目录下核对过，没有开始回合；假流中的答复实际经过挂载配置指定的会话 MCP 进程生成。

Codex CLI 和 Claude Code CLI 的适配器声明新增 `inquiry`，实时通道的能力及问询报告均写明 `realtime`；含义是输入进入正在运行的回合，模型在下一次决策时读取，不能保证立刻打断当前工具。DSH 和 ZCode 的送达声明仍为 `cooperative-checkpoint`，继续使用已有检查点路径。参考文档、共享 skill 和 Host 指南由 Host 更新。

## 验证

所有 Python 检查都使用任务指定的既有解释器执行 `PYTHONPATH=src <指定解释器> -m hey_my_buddy.cli.checks <范围>`，采用默认进程数；没有改动该解释器的环境，也没有运行完整检查。以下检查均通过、退出码为 0，检查运行器确认移除了各自私有测试根目录。

- 共享问询、实时通道、签名、服务恢复与传输：`tests/python/buddy/harnesses/test_in_turn_inquiry.py`、`test_inquiry_owner.py`、`test_live_channel.py`、`test_c_two_live.py`、`test_session_receipts.py`，以及 `tests/python/blackboard/tasks/test_inquiry.py`、`tests/python/blackboard/service/test_execution_recovery.py`、`tests/python/protocol/test_inquiry_transport.py`：8 个文件、203 项测试。
- DSH 原生运行与 ZCode 问询回归：`tests/python/buddy/harnesses/dsh/test_native_run.py`、`tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py`、`test_zcode_checkpoint.py`：3 个文件、123 项测试。
- Claude Code CLI 适配器与角色层：`tests/python/buddy/harnesses/claude/`、`tests/python/buddy/roles/`：23 个文件、395 项测试；最后调整原生答复重放校验后，另跑 `tests/python/buddy/harnesses/claude/test_native_run.py`：79 项测试。
- Codex CLI 适配器：`tests/python/buddy/harnesses/codex/`：254 项测试；最后调整原生答复重放校验及角色 attention 来源核对后，另跑 `tests/python/buddy/harnesses/codex/test_in_turn_inquiry.py`：7 项测试。

假 app-server 和假 Claude Code CLI 流覆盖活动回合内送达、签名答复与问题和根回合的对应、未答时转为 attention、回合结束后的问题、助手文字不算答复，以及外来或子代理工具回执不能代替根回合答复。真实模型下的端到端行为、合并后的完整检查和安装由 Host 另行验收。
