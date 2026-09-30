# 待处理问题

用户在使用中发现、尚未进入实现的问题。每条写明现象、原因（已查明时）与建议；进入实现时移到对应的 ADR 或规格，完成后从这里删除并在验收记录中注明。

## 控制台


## 路由

- **没有重置时间的额度耗尽可能一直排除。** B 批第一阶段按原生观测把额度或余额耗尽的配置暂时移出自动路由，有重置时间的到点恢复；但没有重置时间的耗尽（例如 DeepSeek 余额为零）只能等更新的"可用"观测才恢复。DSH 没有主动的余额查询，被排除的配置也不会再被路由选中产生新观测，用户充值后仍可能一直被排除，界面也没有恢复入口。建议在 B 批第二阶段补上：Buddy 配置中为被排除的配置提供"重新检测 / 恢复"，或让没有重置时间的耗尽在一段时间（例如 1 小时）后允许重试一次。（2026-09-30）

## 服务与凭据

- **ZCode 服务商配置快照在 attempt 结束后仍留在磁盘上。** 每个 ZCode attempt 目录里的 `builtin-provider.json`、`personal-provider.json` 可能含 API key，日常看板上有 108 份。0.20.0 起它们不再进入滚动备份，但仍保留在 attempt 目录中。建议在原生进程确认停止后删除，与 [ADR-019](../decisions/019-worker-accounts-and-usage.md) 的凭据处理一起实现。（2026-09-29）
- **Claude 适配器关于 `ANTHROPIC_API_KEY` 的注释与代码不符。** `src/buddy/adapters/claude_config.py` 的注释说保留该变量作为第一方密钥路径，但 `NATIVE_ENVIRONMENT_ALLOWLIST` 并不传递它，因此 Claude Worker 目前只能用订阅登录。建议在 ADR-019 实现时决定是否支持，并同步注释与 `docs/reference/claude.md`。（2026-09-29）
