<p align="right"><a href="terms.md">English</a> · <strong>简体中文</strong></p>

# 账户与条款

hey-my-buddy 在你的电脑上，用你自己的登录运行你安装的编程 harness。它不是你与 harness 或模型厂商之间协议的一方，也不解释这些协议。本页说明 hey-my-buddy 怎样对待你的账户，以及哪些条款仍然适用于你。本页不是法律意见。

## hey-my-buddy 做什么

- 启动你安装的 harness 的命令行程序（Codex 是 Codex CLI 自带的 App Server），不修改它；harness 的登录、设置、提供方和扩展都保持你配置的样子。
- 每个 harness 用它自己保存的信息登录。hey-my-buddy 不保存、不复制、不转交你登录用的凭据。只存在于 shell 环境变量里的提供方密钥不会传给 Worker。
- 为了显示一个 buddy 按订阅还是按量付费，它只从 harness 自己的状态回复或配置文件里读取提供方和接入类型；同一文件里的密钥不复制、不保存。
- 有可以并行的工作时，它会在你于控制台设定的上限之内同时运行几个 Worker。所有用量都经你的账户产生，计入你的套餐和密钥。
- 它不替别人托管 harness，不转售用量，也不替任何人登录。
- 会话记录保存执行时显示的内容，其中可能有命令输出里的敏感文字。它们只存在你电脑上的私有状态里，不进入备份；你删除它们所属的宏任务时一起删除，不属于任何宏任务的微任务则随这个微任务删除。

## 仍然适用于你的条款

有两层条款适用，hey-my-buddy 不改变其中任何一层：

1. **harness 本身。** 每个 harness 有自己的许可与使用条款，不论你怎样为模型付费都适用。
2. **模型服务**，按你的付费方式：
   - **订阅：** 适用厂商对你这个套餐的条款，不论 harness 是用账户登录，还是用套餐发放的密钥。套餐可能以一个人的正常使用为前提；同时运行几个 Worker 是否符合你的套餐，由厂商判断。
   - **按量：** 每次请求按用量计在密钥的所有者名下，适用发放这把密钥的提供方的条款。

在控制台里调高并发之前，先查看你的套餐条款与限额。超出时可能被限流、暂时受限，或者账户被处置。

## 官方条款

以各家自己的页面为准，它们会随时间变化。这些条款不分 harness 的形态，CLI 与 app 同样适用。

| Harness | 页面 |
| --- | --- |
| Claude Code | [Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)；Anthropic 的 [Consumer Terms](https://www.anthropic.com/legal/consumer-terms) 与 [Commercial Terms](https://www.anthropic.com/legal/commercial-terms) |
| Codex | [Authentication](https://learn.chatgpt.com/docs/auth)；OpenAI 的 [Terms of Use](https://openai.com/policies/terms-of-use/) |
| ZCode | Z.ai 的 [GLM Coding Plan Usage Policy](https://docs.z.ai/devpack/usage-policy) |
| DeepSeek Harness | DeepSeek 的 [Open Platform Terms of Service](https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html) |
