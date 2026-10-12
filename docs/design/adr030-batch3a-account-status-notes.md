# ADR-030 批次 3a：Claude Code 的账户事实

根因是目录读取按固定账户字段筛选，遗漏原生正常形状。现在 `_catalog` 只检查 `account` 是对象且至少一个值非空，供应方也计入；否则上报 `unknown`，不按供应方或登录方式分支。

下表形状由 Host 于 2026-10-12 使用产品的 initialize-only 发现对真实 Claude Code 2.1.284 量到；隔离实验使用空的私有配置目录与占位值，没有模型调用，本回合未自行运行真实 CLI。

| 登录方式 | 原生账户形状 | 当前上报 |
| --- | --- | --- |
| 订阅 | `apiProvider: "firstParty"`、`email`、`organization`、`subscriptionType`，无 `tokenSource` | `confirmed` |
| 环境变量密钥 | `apiProvider: "firstParty"`、`tokenSource: "none"`、`apiKeySource: "ANTHROPIC_API_KEY"` | `confirmed` |
| Bedrock | 只有 `apiProvider: "bedrock"` | `confirmed` |
| Vertex | 只有 `apiProvider: "vertex"` | `confirmed` |
| 未登录 | 启动器检测报告 `login-required`，到不了 initialize | 不经过 `_catalog` |

[Claude Code 认证文档](https://code.claude.com/docs/en/authentication)说明云供应方认证在外部完成；[Agent SDK AccountInfo](https://code.claude.com/docs/en/agent-sdk/typescript#accountinfo)的 `email`、`organization`、`subscriptionType`、`tokenSource`、`apiKeySource` 都是可选字段。结合 Host 实测，任意非空账户值均作为事实报告，包括供应方和文档未列明的未来字段。

非空沿用 JSON 值的 Python 真值语义：`null`、`""`、`{}`、`[]`、`false`、`0` 不计入；缺失、非对象、空对象或所有值都为空时为 `unknown`。`confirmed` 不证明凭据有效或模型可调用，采信仍由黑板按 ADR-027 决定。未添加登录状态回读，启动器检测保持原样。

第三方与密钥夹具分别改成表中的真实形状；默认订阅不带 `tokenSource`。直接测试覆盖仅第一方、云供应方、未知供应方与空账户边界；未知账户测试改用空对象或缺失账户。仅供应方的 Bedrock 仍通过选择与执行测试。

本回合沙盒内可执行的受影响测试 98 项通过，退出码 0；`test_native_run`、`test_catalog_observations` 全文件及其他受影响文件的发现、目录规则、投影测试均通过。`git diff --check` 通过。Host 仍需在最终树上运行受本机通信限制的以下测试选择：

- `buddy.harnesses.claude.test_claude.ClaudeAdapterTests`：两项独立发现已过，其余包括 `test_governed_turn_leaves_unknown_login_to_the_native_cli`。
- `buddy.harnesses.claude.test_claude_worker.ClaudeWorkerTests`：`test_governed_worker_seals_only_a_proven_claude_result`、`test_quota_failure_is_persisted_as_harness_error_without_a_capability_sample`。
- `buddy.harnesses.claude.test_claude_tool_evidence.RunnerReceiptTests`。

Host 无模型复核：在最终检出根目录清掉 AGENTS.md 列出的继承运行时与凭据变量，以任务指定只读解释器设置 `BUDDY_VERIFY_PYTHON`，将占位符换成真实 CLI，运行下方产品发现；只到 initialize，确认原生停止后返回，检查 `accountStatus: confirmed`。不读写日常看板。

```sh
PYTHONPATH=src BUDDY_DEV_SOURCE=1 BUDDY_CLAUDE_CLI="<real-Claude-Code>" "$BUDDY_VERIFY_PYTHON" -c 'from hey_my_buddy.buddy.harnesses.claude.adapter import ClaudeAdapter; print(ClaudeAdapter().discover_models()["discoveries"])'
```

完整检查、真实发现、故障注入、安装与日常 buddy 恢复由 Host 验收。
