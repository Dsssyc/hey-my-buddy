# 待处理问题

用户在使用中发现、尚未进入实现的问题。每条写明现象、原因（已查明时）与建议；进入实现时移到对应的 ADR 或规格，完成后从这里删除并在验收记录中注明。

## 控制台

- **取消原因一律显示为"用户取消"。** 2026-09-30 A 批中 Codex Host（`codex-console-routing-fixes`）为自行完成余下工作取消了 ZCode GLM-5.3 的委派（run `b6ad0a8f`），控制台却显示"终止原因：用户取消"，用户以为是自己的操作。原因：终止代码 `user-cancel` 只表示收到取消命令，与发起者无关，而 `apps/console/src/task-activity.tsx:128` 一律译为"用户取消"；实际发起者与理由记录在 `workflow.cancelled` 事件的 `actor` 与 `reason` 中。建议：Host 发起时显示"Host 取消"，并给出 Host 标识与理由；控制台整目标停止时显示"在控制台停止"；查不到发起者时显示"取消（发起者未知）"；CLI 的 get 与 result 视图同样给出发起者与理由。随 B 批与[控制台文案精简](console-copy-audit.md)一起处理。（2026-09-30）

## 服务与凭据

- **ZCode 服务商配置快照在 attempt 结束后仍留在磁盘上。** 每个 ZCode attempt 目录里的 `builtin-provider.json`、`personal-provider.json` 可能含 API key，日常看板上有 108 份。0.20.0 起它们不再进入滚动备份，但仍保留在 attempt 目录中。建议在原生进程确认停止后删除，与 [ADR-019](../decisions/019-worker-accounts-and-usage.md) 的凭据处理一起实现。（2026-09-29）
- **Claude 适配器关于 `ANTHROPIC_API_KEY` 的注释与代码不符。** `src/buddy/adapters/claude_config.py` 的注释说保留该变量作为第一方密钥路径，但 `NATIVE_ENVIRONMENT_ALLOWLIST` 并不传递它，因此 Claude Worker 目前只能用订阅登录。建议在 ADR-019 实现时决定是否支持，并同步注释与 `docs/reference/claude.md`。（2026-09-29）
