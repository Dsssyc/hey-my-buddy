# Codex CLI Worker 回合的最终回答

2026-10-15，本次微任务把 Codex CLI Worker 回合的结果选择改为取本会话、本回合在 `turn/completed` 之前最后完成的 `final_answer`。后续最终回答会取代前一条，不再因多条最终回答中止回合；选中的回答继续经过现有的结构化结果校验，最后一条不合法时仍返回 `invalid-result`。只有一条最终回答时沿用原有行为。没有收到逐条完成通知时，原有的完成事件 `items` 读取路径统一放到回合观察器中，并按列表顺序选最后一条；已观察到的完成通知不会因完成事件重复列出消息而被重复计数。

被取代的回答摘要放在已有的 `native-checkpoint.json` 证据内，字段为 `supersededFinalAnswers`：`count` 是被取代条数，`records` 按被取代的顺序记录每条的 `itemId`、`sourceBytes`、`sha256` 和 `jsonParsable`。字节数与 SHA-256 均取原始回答文字的 UTF-8 编码，包含全部尾随空白；`jsonParsable` 使用现有严格 JSON 解析器，拒绝重复成员和非有限数值，只表示可解析，不表示符合结果约定。缺少字符串文字时，字节数和摘要为 `null`，可解析标记为 `false`。只在发生取代时增加这个字段，不保留被取代回答的原文。

现有 Worker 结果收集将整个检查点读入 `nativeCheckpoint`，Worker 的持久回执随后提交给服务，服务把它写入 `attempts.result_json`。持久结果中的位置为 `result.nativeCheckpoint.supersededFinalAnswers`，成功回合和最后一条不合法的失败回合都保留它；微任务验收及检出回收后可以从 attempt 结果查询，无需依赖检出内文件或用户的 Codex CLI 会话库。本次没有增加数据库表、修改已有字段的含义或增加事件种类。

顺带修正 `tests/probes/zcode_hooks.py` 的隔离清单：移除已不存在的 `HMB_AGENT_CREDENTIAL`，加入 `HMB_AGENT_CREDENTIAL_FD`。

验证全部使用 Host 指定的现有解释器，通过 `PYTHONPATH=src <测试解释器> -m hey_my_buddy.cli.checks <文件或目录…>` 运行，使用默认进程数，没有修改解释器环境。首轮指定 `tests/python/buddy/harnesses/codex/test_codex.py`、`tests/python/buddy/harnesses/codex/test_codex_protocol.py` 和 `tests/python/buddy/harnesses/zcode/test_zcode_hooks_probe.py`，72 个测试通过。补齐边界测试后，最终指定 `tests/python/buddy/harnesses/codex/` 和 `tests/python/buddy/harnesses/zcode/test_zcode_hooks_probe.py`，8 个文件、260 个测试全部通过，0 个跳过，退出码 0，检查运行器确认私有根目录已移除。

离线原生程序夹具覆盖先不合法后合法、两条都合法、只有一条、最后一条不合法四种回合，并通过真实 Worker 角色收集路径核对结果、最终消息身份和回执中的替代摘要。第一种夹具包含未闭合 JSON、中文和 680,000 个尾随空白，验证字节数与摘要不被截断。观察器测试另行覆盖多次取代、完成事件列出回答而没有逐条通知、其他会话或回合的消息、非最终消息以及完成之后的最终消息。没有运行完整检查、真实模型调用、日常服务或安装验证；Host 的整合与验收尚未发生。
