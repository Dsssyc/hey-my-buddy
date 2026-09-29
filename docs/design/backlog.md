# 待处理问题

用户在使用中发现、尚未进入实现的问题。每条写明现象、原因（已查明时）与建议；进入实现时移到对应的 ADR 或规格，完成后从这里删除并在验收记录中注明。

## 控制台

- **"有 N 个工作目标有新活动 · 按最近活动重新排序"横幅频繁出现。** 工作目标列表只要有新活动就弹出整行横幅，委派运行期间几乎一直在跳。原设计是为了不在用户阅读时移动列表项。建议：列表滚动在顶部且鼠标不在列表上时直接静默重排；否则只在列表标题旁显示一个小的"有更新"标记，点击后重排，不再使用整行横幅。（2026-09-29）
- **控制台登录对本机使用过严，在 Codex sidechat 中打不开。** 现状：`buddy console` 给出只能用一次、60 秒有效的入口链接，登录状态 30 天未使用即过期。agent 生成链接到用户点开常常超过 60 秒；内嵌面板如果不保存 cookie 或以嵌入方式打开页面，SameSite=Strict 的 cookie 可能根本不会被发送（sidechat 的具体现象未确认）。在本机场景下，登录几乎不提供保护：以用户身份运行的程序本来就能直接读写状态目录，网页发起的攻击由 Host、Origin 与 Fetch Metadata 检查挡住，DSH 自己的本机 web UI 也不要求登录。建议：本机回环默认不再需要登录，`buddy console` 直接打开固定地址 `http://127.0.0.1:<port>/`；保留只绑定 127.0.0.1、精确的 Host 检查、写请求的 Origin 与 Fetch Metadata 检查、不开放 CORS、内容安全策略，以及多窗口编辑的修订冲突检查；多人共用的机器可在设置中开启登录，默认关闭。ADR-019 的密钥表单只写入、从不回显，不受影响。实现时同步修改 `docs/reference/console.md` 与 skill 中的入口说明。（2026-09-29）
- **取消原因一律显示为"用户取消"。** 2026-09-30 A 批中 Codex Host（`codex-console-routing-fixes`）为自行完成余下工作取消了 ZCode GLM-5.3 的委派（run `b6ad0a8f`），控制台却显示"终止原因：用户取消"，用户以为是自己的操作。原因：终止代码 `user-cancel` 只表示收到取消命令，与发起者无关，而 `apps/console/src/task-activity.tsx:128` 一律译为"用户取消"；实际发起者与理由记录在 `workflow.cancelled` 事件的 `actor` 与 `reason` 中。建议：Host 发起时显示"Host 取消"，并给出 Host 标识与理由；控制台整目标停止时显示"在控制台停止"；查不到发起者时显示"取消（发起者未知）"；CLI 的 get 与 result 视图同样给出发起者与理由。随 B 批与[控制台文案精简](console-copy-audit.md)一起处理。（2026-09-30）

## 路由

- **冻结候选只有一个时仍调用 Router。** 2026-09-29 Codex Host 提交 4 个 ADR-018 第二批任务时带了硬约束 `adapter: dsh`，而 DSH 下的 Flash off 被用户设为"排除"，冻结候选只剩 `dsh:deepseek-official:deepseek-flash:max`。快速 Router 仍做了一次模型调用，写了一长段理由，最后说"选择该唯一可用 profile"（例如 decision `dec-262240a7`）。只有一个合法候选时 Router 没有可比较的对象，这次调用只增加费用和等待；理由里也没说明候选为何只剩一个，用户看了以为路由出了问题。建议：候选恰好一个时由程序直接选定，路由记录写明"唯一合法候选，未调用 Router"；路由依据同时显示提交时的硬约束与被排除的配置，让人看出候选为何变少；没有候选时仍按现有规则停在 Host 边界。（2026-09-29）

## 服务与凭据

- **ZCode 服务商配置快照在 attempt 结束后仍留在磁盘上。** 每个 ZCode attempt 目录里的 `builtin-provider.json`、`personal-provider.json` 可能含 API key，日常看板上有 108 份。0.20.0 起它们不再进入滚动备份，但仍保留在 attempt 目录中。建议在原生进程确认停止后删除，与 [ADR-019](../decisions/019-worker-accounts-and-usage.md) 的凭据处理一起实现。（2026-09-29）
- **Claude 适配器关于 `ANTHROPIC_API_KEY` 的注释与代码不符。** `src/buddy/adapters/claude_config.py` 的注释说保留该变量作为第一方密钥路径，但 `NATIVE_ENVIRONMENT_ALLOWLIST` 并不传递它，因此 Claude Worker 目前只能用订阅登录。建议在 ADR-019 实现时决定是否支持，并同步注释与 `docs/reference/claude.md`。（2026-09-29）
