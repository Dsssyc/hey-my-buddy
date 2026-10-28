# Antigravity 的 API key 从哪里读；API key 模式下的冒烟测试

## 问题

续查 [2026-10-27 的记录](2026-10-27-antigravity-harness.md)。开发者选了 API key 这条路之后，要弄清两件事：key 能不能不定死在 shell 的环境变量里，例如交给 CC Switch 管；API key 模式下 `agy` 的无界面运行，是否满足 harness 的要求。服务于是否接入 Antigravity 的决定（待开发者定）与以后的接入计划。

## 看了什么

- **本机安装的 `agy` 1.4.8 CLI**（macOS 27.0.1，arm64）：`--help`，以及程序里内嵌的更新说明与报错文字。只看了字符串，没有读源码。
- **本机安装的 CC Switch 3.20.4**（app）：程序里与各 CLI 配置路径有关的字符串。它存放密钥的文件只看了文件名，没有读内容。
- **自己的实验**（2026-10-28）：
  - 不联网、用假 key：在私有的 HOME 里，把 key 放进四处常见的 `.env` 与 `settings.json` 的三个字段，看 `agy` 读不读；
  - 开发者批准的三次付费调用（API key 模式，私有的 HOME，key 由开发者存进登录钥匙串，每次只交给那一个 `agy` 进程）：列模型；一个读文件的回合；一个回合中途发 SIGTERM。另有一次在 macOS Seatbelt 沙箱里证书校验失败，请求没有发出。
- 没有读 `agy` 与 CC Switch 的源码；没有调用 Gemini API 之外的服务。

## 看到的事实

- **API key 只从进程的环境变量读**（本机实测，假 key，不联网）：
  - `settings.json` 设了 `"modelProvider": "gemini"`、环境里却没有 `GEMINI_API_KEY` 时，它立刻报错退出，不退回 app 的消费者登录；
  - `~/.gemini/.env`、`~/.env`、工作目录的 `.env` 与 `.gemini/.env` 都不读，`settings.json` 里的 `env`、`envOverrides` 与 `apiKey` 也不认；
  - 它的更新说明写的也是这条路：设 `modelProvider`、导出 `GEMINI_API_KEY`（厂商自述）。
- **它认 `HOME`**（本机实测）：设了私有的 HOME，它的设置、日志与会话都落在那里，不碰真实的 `~/.gemini/antigravity-cli`。
- **CC Switch 3.20.4 没有 Antigravity 一项**（本机观察）：它给 Gemini CLI 写的是 `~/.gemini/.env`，对 `agy` 不起作用。
- **冒烟测试**（本机实测，API key 模式）：
  - 模型：列出了 Argon 的 low、medium、high 三档（`gemini-4-argon-*`），另有 3.6 到 3.8 的 Flash 与 3.1 Pro。
  - 一个回合：Argon low 读一个文件、原样回复，用了 9 秒；用量是输入 9,577（其中缓存读取 5,261）、输出 123、思考 0。全新的 HOME 与工作目录里没有出现工作区信任提示，无界面运行没有停下。
  - stream-json：每行一个事件，用 `event` 区分。`init` 带会话 ID、模型、工作目录、工具列表与权限模式（这一次是 `request-review`）；`step_update` 带步骤序号、`ACTIVE` 或 `DONE` 与类型（`user_input`、`agent_response`、`tool`），回复带 `text_delta` 与这一步的用量，工具带名称、完整参数与输出摘要；`result` 带 `SUCCESS` 或 `ERROR`、回复、错误、时长、回合数与总用量（输入、输出、思考、缓存读取）。
  - 原生会话：`~/.gemini/antigravity-cli/brain/<会话 ID>/.system_generated/logs/` 下有 `transcript.jsonl` 与 `transcript_full.jsonl`，会话库在 `~/.gemini/antigravity-cli/conversations/<会话 ID>.db`。
  - API 失败：stderr 打一行 `AGY_ERROR` 的 JSON（`short_error`、`status`、`error_code`、`code_kind`、`retryable`、`error_id`），退出码 3。见到的这一次来自 macOS Seatbelt 沙箱：证书校验失败（`OSStatus -26276`），连接没有建立。
  - SIGTERM：工具在跑 `sleep 120` 时给 `agy` 发 SIGTERM，它 1 秒内结束，退出码 1，最后写出 `result`（`ERROR`、`interrupted`）与用量，子进程也结束了。工具的子进程在它自己的进程组里，不在 `agy` 的进程组里。
  - 更新：每次运行都在自己的目录里检查新版本。
  - 三次调用合计：输入约 1.7 万 token（其中缓存读取 5 千），输出 173。
- **推断**：执行进程按进程组确认停止，管不到 `agy` 放在别的进程组里的工具子进程。同一天 Host 复现了这一类缺口不限于 `agy`，要在停止证据里通用地解决，不为 `agy` 单列。

## 取了什么，没取什么

- **取：key 放在系统钥匙串里，用到时才交给 `agy` 的进程。** 冒烟测试就是这样做的；开发者也可以在 `~/.zprofile` 里从钥匙串取出再导出，换 key 只改钥匙串。
- **不取：CC Switch**，它没有 Antigravity 一项；**不取：把 key 明文写进 shell 的配置文件**，那些文件谁都能读，也常被同步或被智能体读到。
- **待开发者定（接入计划）：** 服务怎样拿到 key，因为 `agy` 只认进程的环境；工具子进程不在 `agy` 的进程组里，由 ADR-031 第 4、5 条停止证据的修订兜住。
- **还没有核对的：** 额度用完时的输出；有新版本时它会不会自己换掉；强杀时工具子进程会不会留下；Windows 上的表现。

## 时效

以下任何一项变化，都会让这份记录过时：
- `agy` 出了新版本，尤其是能从文件或系统钥匙串读 API key，或者改了 stream-json 的事件；
- CC Switch 加上 Antigravity；
- 2026-10-27 那份记录里列的条款或产品形态变了。
