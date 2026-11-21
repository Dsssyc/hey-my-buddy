# 四个 harness 在不用工具（`none`）下挂载只读快照查询服务的原生探针实测

## 问题

[ADR-035](../decisions/035-router-snapshot-queries-and-correction.md) 将 `none`（不用工具）权限明确为允许执行自身挂载的只读冻结快照查询服务，同时禁止任何原生本地工具、网页工具与外部用户 MCP 工具，并要求按需分页查询、HMAC 签名回执、上下文感知纠错与 O(1) 静态提示前缀。动手改驱动前，必须在本机四个已安装 harness 的隔离实验里实测五个问题：1. `none` 下挂载私有查询服务时的真实隔离强度（`enforced` 还是 `convention`）；2. 服务端返回 `isError` / 失败页时的真实线协议封装，以及签名回执能否无损验签；3. Codex `dynamicToolCall` 在事件流里的根 `callId` 归因字段；4. 同线程纠错（Codex）与新会话纠错（DSH / ZCode / Claude Code）的行为；5. 静态 O(1) 前缀加按需查询相对整包内联 prompt 的缓存命中与耗时差异。

## 看了什么

本机已安装的四个 harness CLI 与隔离实验（运行于仓库忽略目录 `tmp/track0-probes/`，使用独立临时工作目录与私有会话存储，不触碰日常黑板与用户配置），实验日期 2026-11-19（均经开发者逐次批准原生调用）：

- **Claude Code CLI `2.1.329`**（`-p --input-format stream-json --output-format stream-json --verbose --no-session-persistence`，使用本机配置的兼容接口端点与模型别名 `haiku`）：实测 4 轮（`--tools ""` 挂载私有 MCP 含故意错游标、`--tools ""` 新会话纠错、热查询 Task B、整包无工具基线 Task B）。
- **DSH CLI `0.2.2-rc.1`**（ACP 模式 `--profile acp --patch ...`，模型 `deepseek-official / deepseek-v4-flash / off`）：实测 4 轮（全量禁用 `tool-*` 保留 `tool-executor-native` 并通过 `session/new` 挂私有 MCP 含错游标、新会话纠错、热查询 Task B、整包基线 Task B），外加一次无模型调用的 `--dump-config` 核对 `mcp-resources` 行禁用效果。
- **ZCode CLI `0.17.1`**（`app-server`，模型 `bigmodel-api-2 / GLM-5.3-Flash / low`）：实测 5 轮（`toolAllowlist: []` 挂私有 MCP、显式白名单 5 个私有 MCP 工具含错游标、新会话纠错、热查询 Task B、整包基线 Task B）。
- **Codex CLI `0.169.1`**（`app-server --listen stdio://`，模型 `openai / gpt-6.2-sol / high`）：实测 3 线程 4 回合（带全量 feature 禁用覆盖和禁用用户 `mcp_servers` 的 `thread/start` + `dynamicTools` 含错游标与同线程第 2 回合 `turn/start` 纠错、热查询 Task B、整包无工具基线 Task B）。

未读部分：未读任何 harness 的内部实现源码；未测 Windows 平台上的四个 CLI；未单独注入冲突同名外部 MCP 服务做对抗实测（以签名回执验签与隔离实测结果结合 `session_receipts.py` 既有机制覆盖）。

## 看到的事实

以下均为隔离实测结果，区分线协议地面事实（如 `system/init`、事件流、rollout/SQLite 账本）与模型自报的 `visibleTools`：

- **Claude Code CLI `2.1.329`（`permission:none` 可达 `enforced`）。** 传入 `--tools "" --strict-mcp-config --mcp-config <仅含 hey_my_buddy_snapshot> --allowedTools mcp__hey_my_buddy_snapshot__* --permission-mode default --json-schema ...` 后，CLI 无需 `ToolSearch` 即直接暴露 5 个私有查询工具；线协议地面事实 `system/init.tools` 严格只包含 `StructuredOutput` 与 5 个 `mcp__hey_my_buddy_snapshot__*`，零原生工具、零用户 MCP。`isError: true` 页在 `user` `tool_result` 中带 `"is_error": true` 且文本原样保留，HMAC 验签全部通过。冷跑（含错游标共 3 次查询）23.5 s，`cache_read_input_tokens = 36,544`；热跑 Task B（2 次查询）15.6 s，`cache_read_input_tokens = 24,064`；新会话纠错 11.8 s 且 0 次多余查询；整包基线 10.2 s、`cache_read_input_tokens = 0`（单次 14,553 未缓存输入 token）。
- **DSH CLI `0.2.2-rc.1`（`permission:none` 保持 `enforced`，需一并禁用 `mcp-resources` 行）。** 仅禁用全部 `tool-*`（保留 `tool-executor-native`）并通过 `session/new` 的 `mcpServers` 挂载私有服务时，无任何原生 shell/文件/网页/子代理工具，也无用户 MCP，但模型仍看到了 `@deepseek-ai/dsh-mcp-resources` 提供的 3 个只读资源工具（`list_mcp_resources`、`list_mcp_resource_templates`、`read_mcp_resource`），因为其行 id 为 `mcp-resources` 而非 `tool-*`。无模型调用的 `--dump-config` 实测证实，在启动补丁里加入 `{"id": "mcp-resources", "disabled": true}` 可直接将其禁用。`isError: true` 页经 `tool_call_update` 返回 `"status": "failed"`，并在 `rawOutput` 前加 `"Error: "` 前缀；容忍有界前缀定位首个 `{` 后，HMAC 验签 100% 通过。DSH 在工具调用之间会以 `agent_message_chunk` 输出简短过渡句，最终答案需取最后一次工具调用之后的终段内容。从 `.jsonl.zstd` rollout 读到的真实缓存：冷跑步间 `cacheReadTokens` 达 7,040–7,808（98% 命中），新会话纠错首步即命中 `cacheReadTokens = 1,536`（8.2 s、0 次查询），热跑 Task B 首步跨会话命中 `1,280` 且步间达 `8,192`，而整包基线 `cacheReadTokens = 0`（9,179 未缓存输入）。
- **ZCode CLI `0.17.1`（`permission:none` 保持 `enforced`，挂载查询服务时必须显式写入 `toolAllowlist`）。** 实测发现关键约束：`session/create` 同时传 `"toolAllowlist": []` 与 `"mcpServers": [hey_my_buddy_snapshot]` 时，空允许表会连同私有 MCP 工具一起屏蔽（查询账本 0 条，模型无法调用）。将 `toolAllowlist` 设为精确的 5 个 `mcp__hey_my_buddy_snapshot__*` 后，模型自报 `visibleTools` 严格仅含这 5 个工具，零原生工具与用户 MCP。（2026-11-20 补正：前次探针脚本未绑定 `conn.attention` 回调，漏记了 ZCode `0.17.1` 在 `session/create` 启动自带官方插件 `zcode-site@zcode-plugins-official` 时固定发出的 `interaction/requestOfficialMcpAuthHeaders` 反向请求与 `session/lifecycle/changed` 通知；零模型调用复测证实即使 `toolAllowlist: []` 与 `mcpServers: []` 亦会发出，故在 `no_tools=True` 下亦应沿用工具档做法以 `-32601` 拒绝并仅记为 `unsupportedNativeRequests` 证据。）`isError: true` 页在线协议 `tool.updated` 的 `result` 中包成 `{"success": true, "content": "MCP tool returned an error:\n{...}"}`，与 `session_receipts.py` 已有封装规则一致，解出 `result["content"]` 并跳过有界前缀后即可完成 HMAC 验签。从私有 `sessions.sqlite` 的 `step-finish` 读到的真实缓存：冷跑与热跑步间 `cache.read` 达 7,616–8,576（93% 以上命中），整包基线 `cache.read = 0`（10,318 未缓存输入）。
- **Codex CLI `0.169.1`（`permission:none` 保持 `convention`，同线程纠错与前缀缓存收益显著）。** 在 `thread/start` 中传入全量 feature 禁用覆盖（`web_search: "disabled"`、`features.shell_tool: False`、`features.view_image: False`、`agents.enabled: False`、`features.multi_agent: False`、`features.multi_agent_v2: False`、`features.apps: False`、`features.plugins: False`、`features.image_generation: False`）并通过 `config/read` 禁用全部已配置的用户 `mcp_servers` 后，用户 MCP 已被彻底屏蔽，但 Codex CLI 仍向模型暴露了部分内置工具（`functions.exec`、`functions.wait`、`apply_patch`、`clock.sleep`、`create_goal`、`list_mcp_resources` 等），因此 `permission:none` 必须如实维持 `convention`，由 `ToolEventEvidence` v4 事后严格拒收任何非快照工具调用（2026-11-20 补正：GPT-6 `code_mode_only` 模型调用 `dynamicTools` 时经由受限 V8 JS 分发器 `exec` 的 `custom_tool_call` / `custom_tool_call_output` 派发内嵌 `dynamicToolCall`，探针日志里的 `exec-*` 调用标识已反映该路径，但探针脚本未运行 `FastCorrection` 与 `judge_tool_evidence`；挂载快照服务的 `none` 档须沿用 `web` 档规则放行完整且一致的 `exec` 根调用，同时对其内嵌调用继续按 `none` 档逐条严审）。线协议关键发现：`item/tool/call` 服务端请求里携带 `params["callId"]`，但 `item/started` 与 `item/completed` 的 `dynamicToolCall` 条目将该值放在 **`item["id"]`**（而 `item["callId"]` 缺失），且 `success=False` 时 `status="failed"`、`contentItems[0]["text"]` 原样承载签名错误页（HMAC 验签通过）。同线程第 2 回合 `turn/start` 纠错仅 256 字节输入、耗时 7.67 s、0 次多余查询、缓存命中 `28,672 / 29,155`（98.3%）；跨线程热跑 Task B 耗时 16.0 s、总缓存命中 `74,752 / 83,584`（89.4%）；整包基线 `cachedInputTokens = 0`。

## 取了什么，没取什么

- **取：Claude Code CLI `2.1.329` 启用 `permission:none`（`enforced`）及 Router 快照查询。** 依据是 `--tools ""` 加 `--strict-mcp-config` 与 `--mcp-config` 无需 `ToolSearch` 即严格只暴露 `StructuredOutput` 与 5 个私有查询工具，落到 ADR-035 第 1、3、7 条。
- **取：DSH `none` 启动补丁增加禁用 `mcp-resources` 行，并容忍 `"Error: "` 有界前缀验签。** 落到 ADR-035 第 1、3 条与 DSH 驱动解耦实现。
- **取：ZCode `none` 挂载快照查询服务时将 `toolAllowlist` 设为该次挂载的精确 `mcp__hey_my_buddy_snapshot__*` 工具名列表（不挂载时仍为 `[]`），并在 `tool.updated` 中从 `result["content"]` 解包验签。** 落到 ADR-035 第 1、3 条与 ZCode 驱动实现。
- **取：Codex `none` 保持 `convention`，但复用 `config/read` 禁用用户 `mcp_servers` 与多余 feature 以收紧默认暴露面；`dynamicToolCall` 根 `callId` 归因对齐 `item["id"]` 与 `item/tool/call.params["callId"]`；保持同线程 `turn/start` 纠错。** 落到 ADR-035 第 1、3、7 条与 `ToolEventEvidence` v4 实现。
- **没取：不把 Codex `none` 标成 `enforced`。** 依据是实测 `thread/start` 的配置覆盖仍无法从模型可见工具列表中移除内置 `exec` / `apply_patch` 等工具，遵守 ADR-023 第 2 条如实声明能力的原则。
- **没取：不为四个 harness 造统一的新纠错通道。** 依据是 Codex 同线程 `turn/start` 与 DSH / ZCode / Claude Code 新会话纠错均已在实测中跑通且开销可控，符合 ADR-035 第 7 条上下文感知纠错方案。

## 时效

以下任一变化都会让本记录过时，需要重跑隔离探针：四个 harness 任一升级后改动 `none` 权限过滤规则（例如 Codex 新增隐藏内置工具的原生参数、Claude Code 要求 MCP 工具经过 `ToolSearch`、DSH 重命名 `mcp-resources` 行或改变 `isError` 包装格式、ZCode 改变 `toolAllowlist` 与 `mcpServers` 的交集语义），或在 Windows 平台上观察到与 macOS 不同的线协议或进程隔离行为。
