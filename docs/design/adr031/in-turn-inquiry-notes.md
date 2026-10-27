# Codex 与 Claude Code 的回合内问询

2026-10-16，基于 `c605b127` 的源码实现记录，等待 Host 验收；本微任务没有安装产品，也没有调用真实模型。首轮输出 `a176b992` 经开发者批准的 Host 真实回合核对未通过：Codex CLI 的 MCP 答复调用被审批策略拒绝，Claude Code CLI 在晚到输入的结束窗口出现第二个根初始化。开发者随后决定 Codex CLI 改用 app-server 客户端工具，本记录描述调整后的源码。

## 送达、答复与结束

Codex CLI 使用正在执行的根线程和原生回合号调用 `turn/steer`，明确传入 `expectedTurnId`，并核对返回的 `turnId`。Claude Code CLI 向正在执行的 `stream-json` 输入写入一条用户消息。两条路径都沿用 `InquiryBridge` 的状态与现有字节、条数上限，不延长截止时间。启动前暂不可用仍返回 `not-ready`；已观察到根回合结束、或桥已关闭时返回已有的 `agent-not-running`，服务将这个窗口里尚未写入日志的问题也记为不可用，避免永久保持 queued。根回合结束后，桥将日志中尚未回答的条目记为不可用；子代理的生命周期不改变根回合的问询窗口。

Codex CLI 只声明客户端工具 `hey_my_buddy_answer_inquiry`，没有 checkpoint 工具。`item/tool/call` 从驱动已有连接传回，驱动在边界上核对当前根线程、根回合、调用号、工具名，以及仅含 `inquiryId` 和 `answer` 的有界参数。桥在同一把锁下确认问题已经送达且尚未答复，持久写入答案后才返回 `{success:true, contentItems:[{type:"inputText", text:...}]}`，不使用签名回执。子线程、旧回合、无效参数、未知问题、未送达或已答的问题返回 `success:false` 和具体原因，回合继续；其他工具及其他服务端请求仍按原路径以 `-32601` 拒绝。

Claude Code CLI 的答复继续经过会话 MCP 服务的 `hey_my_buddy_answer_inquiry`。服务只读取同一个问询日志，返回现有格式的签名回执；驱动核对根会话与工具调用，再校验回执的微任务、尝试、代次、回合、问题编号、问题散列和答复正文。普通工具错误不记录答案，也不结束微任务；声称是签名拒绝的内容仍须验证，伪造内容会失败。失败原文通过经过散列验证的 `inquiry-tool-failures` 证据投影到结果的 `inquiryToolFailures`，最多保留 8 条，每条最多 4000 UTF-8 字节，另记丢弃条数。助手普通文字不能替代任一答复工具。

角色层从经过文件散列验证的 `inquiry-report` 读取 `unansweredDelivered`，把送达后仍未回答的结果转成 `attention`，并在来源记录中留下 `unansweredDeliveredInquiries`。已有 assistance 请求的内容保留，处置改为 attention；原生回合的成功、问询答复和 Host 对工作成果的验收仍是分别记录的事实。

Claude Code CLI 发送过问询输入后，驱动在观察到第一个根结果时同步关闭输入并停止所拥有的原生进程组，最终仍须取得进程组已消失的证据。若该结果时仍有未答的已送达输入，随后缓冲区出现同会话的第二个根初始化，则记录为未获准的后续查询，保留第一个结果、忽略其后续模型帧、不应答其工具权限回调，并将问题的原因记为根回合在读取或回答前已结束、排队的后续查询已停止。没有这项晚到输入证据的重复初始化仍是协议错误。假流验证的是驱动观察根结果后停止进程组，不能据此声称真实 CLI 在结果被驱动读到之前从未开始后续查询。

服务投影优先保存具体 `reason`，没有它时才用 `limitation`，400 字符上限只在消息更新边界处理。选中的执行已经 finished 时，即使微任务停在 awaiting-host，也读取该执行的日志；仅按 runId 观察会导入实时通道或日志中的全部分页状态。活跃执行没有绑定通道时仍不退回文件；外来、被拒绝以及孤立的日志记录不能创建或覆盖问题。

## 工具接入与能力声明

Codex CLI 在 `initialize` 声明 `experimentalApi`，通过 `thread/start.dynamicTools` 声明一个答复工具，完成结果仍走原生输出 schema。Codex 一侧的会话 MCP 挂载、审批覆盖、签名校验和 `mcpToolCall` 答复解析已经移除；用户自己的配置不受改写。无模型探针已替换为 `tests/probes/codex_inquiry_client_tool.py`：在私有 HOME 和 CODEX_HOME 中生成 Codex CLI 0.162.1 的实验协议 schema，核对客户端调用与 `inputText` 响应形状，并确认 app-server 接受工具声明，模型回合数为 0。该版本的 `thread/resume` 没有 `dynamicTools` 参数，`thread/read` 也不暴露工具声明；实现续接时使用线程原有声明，其真实调用可用性尚未验证。

Claude Code CLI 通过按次 `--mcp-config` 增加同一个会话服务，并用 `--allowedTools` 放行本次命名空间里的两个工具；`--tools` 中只增加内置的 ToolSearch，不预先加载工具或修改工具搜索设置。保留原有配置来源，没有使用 `--strict-mcp-config`。本机 Claude Code CLI 2.1.299 的 `--help` 和 `--version` 在私有配置目录下核对过，没有开始回合；假流中的答复实际经过挂载配置指定的会话 MCP 进程生成。

Codex CLI 和 Claude Code CLI 的适配器声明新增 `inquiry`，实时通道的能力及问询报告均写明 `realtime`；含义是输入进入正在运行的回合，模型在下一次决策时读取，不能保证立刻打断当前工具。DSH 和 ZCode 的送达声明仍为 `cooperative-checkpoint`，继续使用已有检查点路径。参考文档、共享 skill 和 Host 指南由 Host 更新。

## 验证

所有 Python 检查都使用任务指定的既有解释器执行 `PYTHONPATH=src <指定解释器> -m hey_my_buddy.cli.checks <范围>`，采用默认进程数；没有改动该解释器的环境，也没有运行完整检查。以下是修复与改换客户端工具后的检查，均退出 0，检查运行器确认移除了私有根目录；各组有重复覆盖，不作累计。

- 共享桥、实时通道、服务状态投影及存储：`tests/python/buddy/harnesses/test_in_turn_inquiry.py`、`test_inquiry_owner.py`、`test_live_channel.py`、`test_c_two_live.py`，以及 `tests/python/blackboard/tasks/test_inquiry.py`、`tests/python/blackboard/store/test_blackboard.py`：6 个文件、218 项测试。
- 最后补上结束窗口的拒绝处理后，运行共享问询与 DSH、ZCode 回归，以及服务问询：`test_in_turn_inquiry.py`、`test_inquiry_owner.py`、`dsh/test_native_run.py`、`zcode/test_zcode_inquiry.py`、`zcode/test_zcode_checkpoint.py`、`tests/python/blackboard/tasks/test_inquiry.py`：6 个文件、185 项测试。
- Claude Code CLI 适配器与角色层：`tests/python/buddy/harnesses/claude/`、`tests/python/buddy/roles/`：23 个文件、401 项测试。
- Codex CLI 适配器：`tests/python/buddy/harnesses/codex/`：8 个文件、254 项测试；最后调整无效 Unicode 参数和日志失败重试边界后，另跑 `tests/python/buddy/harnesses/codex/test_in_turn_inquiry.py`：7 项测试。
- 服务恢复与问询传输：`tests/python/blackboard/service/test_execution_recovery.py`、`tests/python/protocol/test_inquiry_transport.py`：2 个文件、20 项测试。

假 app-server 覆盖客户端工具声明、根回合答复、子线程和旧回合、参数错误、问题未送达或已答、其他请求的原有拒绝；假 Claude Code CLI 流覆盖普通失败及重试、签名拒绝与伪造、首个根结果后紧跟第二个初始化和工具回调、阻止随后的测试写操作。服务测试覆盖具体原因、Host 边界已结束执行的日志，以及只按 runId 的分页刷新与拒绝证据。调整后的真实 Codex CLI 客户端工具调用、真实 Claude Code CLI 结束时序、原生续接中的客户端工具可用性，以及合并后的完整检查和安装，均等待 Host 另行验收。
