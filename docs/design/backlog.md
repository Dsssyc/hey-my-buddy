# 待处理问题

用户在使用中发现、尚未进入实现的问题。每条写明现象、原因（已查明时）与建议；进入实现时移到对应的 ADR 或规格，完成后从这里删除并在验收记录中注明。

## 控制台

- **"有 N 个工作目标有新活动 · 按最近活动重新排序"横幅频繁出现。** 工作目标列表只要有新活动就弹出整行横幅，委派运行期间几乎一直在跳。原设计是为了不在用户阅读时移动列表项。建议：列表滚动在顶部且鼠标不在列表上时直接静默重排；否则只在列表标题旁显示一个小的"有更新"标记，点击后重排，不再使用整行横幅。（2026-09-29）

## 路由

- **冻结候选只有一个时仍调用 Router。** 2026-09-29 Codex Host 提交 4 个 ADR-018 第二批任务时带了硬约束 `adapter: dsh`，而 DSH 下的 Flash off 被用户设为"排除"，冻结候选只剩 `dsh:deepseek-official:deepseek-flash:max`。快速 Router 仍做了一次模型调用，写了一长段理由，最后说"选择该唯一可用 profile"（例如 decision `dec-262240a7`）。只有一个合法候选时 Router 没有可比较的对象，这次调用只增加费用和等待；理由里也没说明候选为何只剩一个，用户看了以为路由出了问题。建议：候选恰好一个时由程序直接选定，路由记录写明"唯一合法候选，未调用 Router"；路由依据同时显示提交时的硬约束与被排除的配置，让人看出候选为何变少；没有候选时仍按现有规则停在 Host 边界。（2026-09-29）

## 服务与凭据

- **ZCode 服务商配置快照在 attempt 结束后仍留在磁盘上。** 每个 ZCode attempt 目录里的 `builtin-provider.json`、`personal-provider.json` 可能含 API key，日常看板上有 108 份。0.20.0 起它们不再进入滚动备份，但仍保留在 attempt 目录中。建议在原生进程确认停止后删除，与 [ADR-019](../decisions/019-worker-accounts-and-usage.md) 的凭据处理一起实现。（2026-09-29）
- **Claude 适配器关于 `ANTHROPIC_API_KEY` 的注释与代码不符。** `src/buddy/adapters/claude_config.py` 的注释说保留该变量作为第一方密钥路径，但 `NATIVE_ENVIRONMENT_ALLOWLIST` 并不传递它，因此 Claude Worker 目前只能用订阅登录。建议在 ADR-019 实现时决定是否支持，并同步注释与 `docs/reference/claude.md`。（2026-09-29）
