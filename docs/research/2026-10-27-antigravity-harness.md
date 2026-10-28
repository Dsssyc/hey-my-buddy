# Antigravity 能不能作为 harness 接进来

## 问题

Gemini 4 Argon 发布之后，开发者问能不能把 Google Antigravity 接成一个 harness，让 Worker 用上这个模型。这份记录要回答三件事：Antigravity 有没有我们需要的无界面形态；用户自己的登录能不能这样用；Gemini CLI 能不能代替。还没有对应的决定，结论待开发者定。

## 看了什么

- **本机安装的软件**（本机观察，只读）：
  - Antigravity app 2.26.0：看了包的结构，运行了包里 `language_server` 的 `--help`，以及 `~/.gemini/antigravity/bin/agentapi` 的帮助。
  - `~/.gemini` 与 app 的数据目录只看了文件名，没有读任何可能存着登录的文件。
  - 2026-10-27 当时没有安装 `agy`，没有发起对话，也没有调用模型。
- **本机核对 `agy` 1.4.8 CLI**（2026-10-28，开发者同意安装与付费调用）：在私有的 HOME 里用 API key 模式列了模型、跑了一个读文件的回合与一次中途 SIGTERM；另有几次不联网、用假 key 的运行，查 key 从哪里读。
- **厂商的公开文档**（2026-10-27 读取，Antigravity 的文档页都没有标日期）：
  - Antigravity 的 CLI 文档：headless、参考、权限与沙箱、对话、模型与推理强度、hooks；
  - CLI 的 changelog（1.0.15 到 1.4.0，最后一条 2026-10-16），以及介绍 CLI（2026-05-19）与 Argon（2026-10-13）的博客；
  - SDK 概览、Sidecars、在 Zed 里使用的说明；
  - FAQ 与附加条款：这两页 Host 亲自读了原文，其余由 Host 委派的调研子代理阅读；
  - Gemini CLI 的公开文档站：headless、会话、沙箱、ACP、条款与认证；
  - Google 开发者博客“Gemini CLI 转向 Antigravity CLI”（2026-05-19）；
  - Gemini API 的 Argon 模型页（2026-10-14）与托管的 Antigravity Agent 页；
  - ACP 的公开注册表。
- **二手材料**：Google 开发者论坛上几条非员工的回复，一篇关于 Argon 每周额度的第三方报道。只用来补空白，下文标为二手。
- 没有读任何项目的源码。

## 看到的事实

**Antigravity 的几种形态**

- **app 本身不行**（本机观察）。2.26.0 是桌面 app：Electron 外壳加一个 Go 写的 `language_server`，PATH 上没有命令行。
  - 唯一的命令行入口 `agentapi` 只有三个命令：新建对话（模型只能选 flash_lite、flash、pro）、发消息、查对话元数据，没有工作目录、权限、输出格式与用量。
  - 厂商文档只在 Sidecars 里提到它，说它是 app 托管的 sidecar 用的。所以它多半要 app 在运行，这一点是推断。
- **Antigravity CLI（`agy`）够用**（厂商自述）：
  - 形态：单独安装的 CLI，用官方脚本装到 `~/.local/bin/agy`，有 macOS、Linux 与 Windows 版。它与 app 共用 agent 与权限设置，会话默认不共享。
  - 跑一个回合：`agy -p` 在当前目录里跑；`--output-format json` 结束时给一个结果，`stream-json` 是 NDJSON 事件流，带每一步的工具、文字增量与用量。
  - 续接：用 `--conversation <id>` 续接原来的对话。
  - 模型：`agy models` 列出模型，`--model` 遇到未知值直接失败，`--effort` 选推理强度。
  - 用量与错误：用量分输入、输出、思考、缓存读取。API 失败时 stderr 打一行 `AGY_ERROR` 的 JSON，退出码 3；日额度与计费上限会让它立即停下。
  - 原生会话存成 `transcript.jsonl`，字段格式没有文档。
  - 权限：headless 不弹审批，工作区里的读写自动允许，shell 命令默认被拒；`--mode`、`--sandbox`（三个系统都有 OS 级隔离）与一个全放开的开关可以调整。没有逐次调用的只读档位。
  - 文档没写：SIGTERM 之后怎样结束子进程树。
- **API key 只从进程的环境变量读**（本机核对，agy 1.4.8 CLI，2026-10-28，不联网、用假 key）：
  - `settings.json` 设了 `"modelProvider": "gemini"`、环境里却没有 `GEMINI_API_KEY` 时，它立刻报错退出，不退回 app 的消费者登录；
  - `~/.gemini/.env`、`~/.env`、工作目录的 `.env` 与 `.gemini/.env` 都不读，`settings.json` 里的 `env`、`envOverrides` 与 `apiKey` 也不认；
  - 它认 `HOME`：设了私有的 HOME，就不碰真实的 `~/.gemini/antigravity-cli`；
  - CC Switch 3.20.4 没有 Antigravity 一项，它给 Gemini CLI 写的 `~/.gemini/.env` 对 `agy` 不起作用。
- **冒烟测试**（本机核对，agy 1.4.8 CLI，2026-10-28，API key 模式，私有的 HOME）：
  - 模型：列出了 Argon 的 low、medium、high 三档（`gemini-4-argon-*`），另有 3.6 到 3.8 的 Flash 与 3.1 Pro。
  - 一个回合：Argon low 读一个文件、原样回复，用了 9 秒；用量是输入 9,577（其中缓存读取 5,261）、输出 123、思考 0。全新的 HOME 与工作目录里没有出现工作区信任提示，无界面运行没有停下。
  - stream-json：每行一个事件，用 `event` 区分。`init` 带会话 ID、模型、工作目录、工具列表与权限模式（这一次是 `request-review`）；`step_update` 带步骤序号、`ACTIVE` 或 `DONE` 与类型（`user_input`、`agent_response`、`tool`），回复带 `text_delta` 与这一步的用量，工具带名称、完整参数与输出摘要；`result` 带 `SUCCESS` 或 `ERROR`、回复、错误、时长、回合数与总用量（输入、输出、思考、缓存读取）。
  - 原生会话：`~/.gemini/antigravity-cli/brain/<会话 ID>/.system_generated/logs/` 下有 `transcript.jsonl` 与 `transcript_full.jsonl`，会话库在 `~/.gemini/antigravity-cli/conversations/<会话 ID>.db`。
  - API 失败：stderr 打一行 `AGY_ERROR` 的 JSON（`short_error`、`status`、`error_code`、`code_kind`、`retryable`、`error_id`），退出码 3。见到的这一次来自一个 macOS Seatbelt 沙箱：证书校验失败（`OSStatus -26276`），连接没有建立。
  - SIGTERM：工具在跑 `sleep 120` 时给 `agy` 发 SIGTERM，它 1 秒内结束，退出码 1，最后写出 `result`（`ERROR`、`interrupted`）与用量，子进程也结束了。工具的子进程在它自己的进程组里，不在 `agy` 的进程组里。
  - 更新：每次运行都在自己的目录里检查新版本；有新版本时会不会自己换掉，没有核对。
  - 三次调用合计：输入约 1.7 万 token（其中缓存读取 5 千），输出 173。
- **SDK**（厂商自述）：Python 包，Alpha 阶段；认证只写了 API key 与 Vertex，没有 Google 账户登录。
- **托管的 Antigravity Agent**（厂商自述）：在 Gemini API 里，可以选 Argon，但跑在 Google 托管的沙箱里，不在用户的机器上。
- **ACP**：ACP 的公开注册表里有 Google 登记的 Antigravity 条目，Antigravity 在 Zed 里就靠它接入；Antigravity 自己的文档里没有提到 ACP。MCP 只作为客户端。

**条款（Host 读了原文）**

- **FAQ**：
  - `agy -p` 是给直接写脚本与 CI 用的；在消费者计划下，把 `agy` 包进第三方 coding agent 或应用、以及自动轮换账户，“不受支持”。
  - 另一条写道，用第三方软件、工具或服务访问 Antigravity 违反服务条款，可能导致账户被停用或终止；要让第三方 coding agent 用 Gemini，建议用 Gemini Enterprise 或 Google AI Studio 的 API key。
- **附加条款第 6 条**：把服务与非 Google 的产品一起使用、用第三方软件访问服务，都算违约，可能导致 Antigravity 和/或 Gemini CLI 账户被停用或终止。页面没有日期。
- **二手**：论坛上有人说在本地用自己的账户运行原版 `agy` 没问题，但回复者都没有员工标识，而且与 FAQ 相反。

**Gemini CLI**（厂商自述）

- 仍在发版，有 headless、JSON 输出、ACP 模式、审批档位与沙箱。
- 自 2026-06-18 起不再为 Google AI Pro、Ultra 与免费个人用户服务，只留 Code Assist Standard 和 Enterprise 与付费 API key。它的文档写着没有针对 Argon 优化，建议改用 Antigravity。

**Gemini 4 Argon**（厂商自述）

- Gemini API 的模型 ID 是 `gemini-4-argon`，只在付费层，上下文 1,048,576 token，thinking 分低、中、高三档。
- Antigravity 的 app 与 CLI 都有，要付费的 Pro、Ultra 或 Enterprise。
- 二手：Pro 用户另有每周额度。

## 取了什么，没取什么

- **不取：在用户的消费者订阅下由黑板驱动 `agy`。** 厂商明说这不受支持，条款写明可以停用账户。代价落在用户的 Google 账户上，而且不是黑板这边能挽回的。
- **待开发者定：只在 API key 或 Enterprise 下接 `agy`。** `agy` 的 headless 形态技术上几乎满足 harness 的要求，按 token 计费的 API key 是厂商自己指的路。本机核对过的见上面的“冒烟测试”。还没有核对的：
  - 额度用完时的输出；
  - 有新版本时它会不会自己换掉；
  - 发 SIGKILL 时，在自己进程组里的工具子进程会不会留下：执行进程按进程组停止时管不到它们；
  - Windows 上的表现。

  另外，黑板不检查用户的 harness 用的是哪种登录（ADR-030），所以接进来以后，在说明里写明厂商的条款，是开发者要定的发布边界，与 2026-10-13 那份条款记录同一类。
- **不取：Gemini CLI。** 它不再接受消费者订阅；用 API key 的话，不如上一条。
- **推断，待核对：让 Antigravity 当 Host。** 用户直接用 Antigravity，由它的 agent 调用 hey-my-buddy 的命令把工作委派出去。这属于用户自己使用 Antigravity，不是第三方包装它，所以不落在上面那几条限制里。要核对的是：Antigravity 读不读 `~/.agents/skills` 下的 skill；它的 agent 能不能在后台等一个长时间的 `await`。
- **用 API key 用 Argon 的另一条路**（推断，未核对）：Gemini API 有与 OpenAI 兼容的接口。用户若在 DSH 里加一个指向它的提供方，就能用上 Argon，不必新接 harness。

## 时效

以下任何一项变化，都会让这份记录过时：
- Antigravity 的 FAQ 或附加条款改写，尤其是放开消费者计划下的包装；
- `agy` 出了新的大版本，或者官方给出逐次调用的只读档位与 SIGTERM 的说明；
- `agy` 能从文件或系统钥匙串读 API key，或者 CC Switch 加上 Antigravity；
- Gemini CLI 恢复接受消费者订阅；
- Argon 的提供范围变了。
