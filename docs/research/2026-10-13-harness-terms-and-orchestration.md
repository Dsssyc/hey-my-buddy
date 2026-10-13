# 四家 harness 的官方条款对“由别的工具来调度”说了什么

2026-10-13，整合 Host。用户要求查清：本项目调度 buddy 的做法合不合各家的规定，会不会给项目或它的用户带来风险。

## 问题

本项目在用户的机器上启动用户自己安装的 harness，用用户自己的登录跑 Worker 与 Router 的回合，常常同时跑几个。各家的官方条款允许不允许这样用；哪些做法有风险；发布之前文档里要写明什么，产品里要不要加提示或限制。

## 看了什么

都是 2026-10-13 读的公开文档，经网页摘录工具取得，逐字的引文以原页为准。

- **Anthropic。** Claude Code 文档的 [Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance) 全文；[Consumer Terms of Service](https://www.anthropic.com/legal/consumer-terms)（2025-10-08 生效）只取了第 2、3 节里与账户共享、自动化访问有关的两条；支持文章 [Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)（页面标注本周更新）。Max 与 Team 每月 API 额度的那篇只看了搜索结果里的摘要，没有打开。Commercial Terms、Usage Policy 与商标指引没有读。
- **OpenAI。** Codex 文档的 [Authentication](https://learn.chatgpt.com/docs/auth) 与 [Rules](https://learn.chatgpt.com/docs/agent-configuration/rules)。帮助中心讲 ChatGPT 套餐里的 Codex 的那一篇打不开；Terms of Use 没有读。
- **智谱。** 国际站 GLM Coding Plan 的 [Usage Policy](https://docs.z.ai/devpack/usage-policy) 与[概览](https://docs.z.ai/devpack/overview)。国内站对应的条款没有找到，没有读。
- **DeepSeek。** [Open Platform Terms of Service](https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html)（2026-04-22 发布，04-29 生效）。DSH 自己的许可与说明只看了搜索结果里的二手描述，没有读它的仓库。
- **自己的实验。** Codex 的规则与沙盒的关系，见下面 OpenAI 一段。
- 各家的隐私政策与企业协议都没有读。

## 看到的事实

**Anthropic（Claude Code）**

- 文档自述：Claude Code 的使用，Free、Pro、Max 受消费者条款约束，Team、Enterprise 与 API 受商业条款约束。
- 文档自述：在自己的产品或服务里预装或运行 Claude Code（举的例子是托管的沙盒与别的代理基础设施），要同意商业条款并满足两条：二进制不得修改，也不得去掉、停用或限制它自带的任何登录方式；不得替最终用户付费、转售或居间 Claude 的用量，每个最终用户用自己的凭据登录，用量直接记在他自己名下。
- 文档自述：可以用纯文字如实说自己的产品运行 Claude Code；不得把 Claude Code 或 Anthropic 的名称与标志用进自己的产品名、功能名、公司名或标志，也不得让人以为得到了官方的背书；名称与标志的其余用法要书面许可。
- 文档自述：订阅的 OAuth 登录是给订阅用户正常使用 Claude Code 与别的原生应用的；做产品或服务的开发者应当用 API key；不允许第三方开发者在自己的应用里提供 Claude.ai 登录，或者代用户经 Free、Pro、Max 的凭据转发请求，也不得收集、保存或居间这些凭据与会话令牌。同一页明说：这不妨碍最终用户用自己的订阅登录未经修改的 Claude Code。这些限制可以不经事先通知就执行。
- 文档自述：Pro 与 Max 宣传的用量是按普通的个人使用估的。
- 消费者条款自述：不得以自动化或非人工的方式访问服务，经 API key 访问或官方另行明确许可的除外；不得与他人共享账户凭据。
- 支持文章自述：`claude -p`、Agent SDK 与第三方应用仍然可以用订阅的额度；2026 年 6 月宣布的把这类用量移到单独额度的改动已暂停。摘要里说 Max 与 Team 另有每月 API 额度，只在用关联的 Console 组织的 API key 运行时才用得上。
- 推断：本项目不改二进制、不碰凭据、由用户自己登录、用量记在用户名下、不托管，落在同一页明说不受妨碍的那一类里；`claude -p` 是官方的无界面入口，支持文章把它列为可以用订阅额度的用法。剩下三个风险点。一，同时跑多个 Worker 可能超出“普通的个人使用”。二，“不得限制自带的登录方式”：本项目的服务按白名单传环境变量，只靠 shell 里的环境变量给 Claude Code 提供 API key 的用户，这个变量到不了 Worker 的进程；这算不算限制，不由我们判断。三，控制台里用了 Claude 的标志，按这一页属于要书面许可的用法。

**OpenAI（Codex）**

- 文档自述：Codex 有 ChatGPT 登录（用订阅的额度）与 API key（按量计费）两种；程序化的工作流用 API key，API key 是自动化的推荐默认；企业另有给脚本与调度器用的访问令牌；不要在不受信任或公开的环境里暴露 Codex 的执行；`auth.json` 要当作密码对待。
- 文档自述：规则控制哪些命令可以在沙盒之外运行，`allow` 的含义是不经询问在沙盒之外运行；规则是实验性的；项目里的规则只在这个项目被信任时加载；`zsh -lc` 这类外壳会被拆成里面的各条命令再套规则。
- 实测（2026-10-13，Codex 0.162.1）：一个写检出档位的真实回合里，`cp` 把文件写到了检出之外，而同一个回合里用重定向写同一个目录被沙盒拒绝；不调用模型、直接在 Codex 的沙盒里跑同一条 `cp` 是被拒绝的；用 Codex 自己的规则检查命令去问，这台机器上用户的规则对 `cp` 这个前缀给的是放行。三项与文档的说法一致。
- 推断：读到的页面没有禁止别的工具驱动本机的 Codex，对用订阅登录跑自动化的态度是不推荐，不是禁止；Terms of Use 没有读，所以这一条不下结论。

**智谱（GLM Coding Plan 与 ZCode）**

- 文档自述：套餐只能在官方支持的工具与产品里使用，在不支持的工具里使用可能被限制权益；概览页列出的工具里有 ZCode、Claude Code、Codex、OpenCode、Cline 等。套餐权益只属于订阅者本人，禁止共享账号。并发随档位而定，平台动态调整：Lite 建议同时一个项目，Pro 一到两个，Max 两个以上。违反使用规则可能触发风控，包括限流、冻结；违规超过三次可能封禁。不退款。
- 推断：本项目经 ZCode 使用套餐，在支持的工具之内。风险在并发：同时派出几个 ZCode 的 Worker，等于同时开几个项目，超过档位建议的并发可能触发风控。有第三方文章说套餐明确禁止自动化脚本，官方页面里没有读到这句话，不采信；国内站的条款可能不同。

**DeepSeek（DSH）**

- 条款自述：可以把模型的能力集成进下游的系统与应用，下游由开发者自己负责；API key 不得共享、公开，不得放进客户端代码；平台可以不经通知暂停或终止服务。没有读到关于并发或自动化的限制。
- 二手：DSH 以宽松的许可开源，自带无界面的运行方式。
- 推断：本项目让 DSH 用用户自己配置的 key，不经手 key，四家里风险最低。

## 取了什么，没取什么

- **取：把已有的做法写成对外的边界。** 不修改、不打包任何 harness 的二进制；不读、不存、不转交任何登录凭据；每个用户用自己的登录，用量记在自己名下；不托管别人的 harness。这些已经是 [ADR-030](../decisions/030-harness-layer-scope.md) 的决定，发布之前在 README 与运维参考里写成一段，措辞待用户确认。
- **取：把风险如实告诉用户，不替用户判断。** 订阅登录之下同时跑多个 Worker 的风险、两家把 API key 列为自动化的推荐方式、智谱按档位的并发，写进各 harness 的参考文档，只陈述官方的说法与出处。要不要在控制台里提示、默认的并发要不要按供应方收紧，待用户定。
- **取：Codex 的规则放行的命令不经沙盒这一事实**，用在权限档位的强度说明里（[ADR-023](../decisions/023-harness-integration-principles.md) 第 9 条）。
- **待用户定：** 控制台里 Claude 的标志。2026-10-11 的决定是按通行的做法显示、收到要求就撤；这次读到官方页面写明这类用法要书面许可。
- **待核对：** 环境变量白名单与“不得限制自带的登录方式”的关系，随 ADR-030 批次 5 一起看。
- **没取：** 不因此自己实现登录或改用任何一家的凭据接口；不依据第三方文章加限制；不对用户的订阅条款下结论。

## 时效

任何一家改了上面读到的页面；Anthropic 的支持文章本周刚更新，6 月的改动只是暂停；读到智谱国内站的条款或 OpenAI 的 Terms of Use 之后；本项目开始对外分发，或者出现替别人托管 harness 的形态时，要重读 Anthropic 讲“在产品里提供 Claude Code”的那一节。
