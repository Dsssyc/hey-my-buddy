# 待处理问题

用户在使用中发现、尚未进入实现的问题。每条写明现象、原因（已查明时）与建议；进入实现时移到对应的 ADR 或规格，完成后从这里删除并在验收记录中注明。

## 控制台

- **"有 N 个工作目标有新活动 · 按最近活动重新排序"横幅频繁出现。** 工作目标列表只要有新活动就弹出整行横幅，委派运行期间几乎一直在跳。原设计是为了不在用户阅读时移动列表项。建议：列表滚动在顶部且鼠标不在列表上时直接静默重排；否则只在列表标题旁显示一个小的"有更新"标记，点击后重排，不再使用整行横幅。（2026-09-29）

## 服务与凭据

- **ZCode 服务商配置快照在 attempt 结束后仍留在磁盘上。** 每个 ZCode attempt 目录里的 `builtin-provider.json`、`personal-provider.json` 可能含 API key，日常看板上有 108 份。0.20.0 起它们不再进入滚动备份，但仍保留在 attempt 目录中。建议在原生进程确认停止后删除，与 [ADR-019](../decisions/019-worker-accounts-and-usage.md) 的凭据处理一起实现。（2026-09-29）
- **Claude 适配器关于 `ANTHROPIC_API_KEY` 的注释与代码不符。** `src/buddy/adapters/claude_config.py` 的注释说保留该变量作为第一方密钥路径，但 `NATIVE_ENVIRONMENT_ALLOWLIST` 并不传递它，因此 Claude Worker 目前只能用订阅登录。建议在 ADR-019 实现时决定是否支持，并同步注释与 `docs/reference/claude.md`。（2026-09-29）
