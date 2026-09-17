# hey-my-buddy


[English](README.md)

通过 Codex 插件把边界明确的任务交给本地 dsh。插件内的本地服务负责启动、状态与结果持久化，Codex 负责明确任务和最终验收。原来的独立 skill / CLI 仍可单独使用。

## Codex app 插件

```sh
uv sync --project deepseek-delegate --python 3.12
```

在 Codex 中安装本仓库的 `hey-my-buddy` 插件后，新任务可以直接调用 `buddy_start`、`buddy_wait`、`buddy_result` 等工具，无需手写任务文件、维护 PID 或创建 hourly。`buddy_dashboard` 返回可在浏览器面板打开的本地任务页，页面刷新不调用模型。

服务默认只运行一个委派；多个 Codex 连接共享同一个服务。它只取消自己启动的进程，不停止现有 dsh 宿主或交互会话。分组沿用下方的工作区桥接安装；模型设置仍使用每次运行的私有副本。

`notify` 默认关闭。当前回合用 `buddy_wait` 等待；需要结束回合后自动继续时，Buddy skill 创建绑定原任务的官方 App heartbeat，定期通过 C-Two 读取既有任务、验收结果并删除 heartbeat。正常约每分钟检查一次，App 可用性、调度和模型处理会影响实际延迟。直接原生通知仍是受进程身份校验阻止的实验路径。持久工具授权和恢复说明见 [插件服务说明](deepseek-delegate/references/plugin-service.md)。

## 它做什么

- 通过 `dsh --profile headless` 执行一次有界任务，并真正覆盖本次运行的 model/effort。
- 把设置文档复制到私有临时 JSON，只替换 `agent-default-model` 里的路由和 effort，再用临时 `--patch`
  覆盖指向这份副本。原始设置与凭据不会被修改。
- stdout/stderr 写入每次运行独有的私有日志目录，stdout 只输出一个 JSON 对象。
- 不经过 shell：启动器和任务内容都以参数数组传递，含空格路径、前导短横线、换行、shell 元字符都保持原样。
- 除你要求的这一次 dsh 运行外不会自行调用模型，也不会静默重试、降级或替换路由。

## 环境要求

- uv 和 Python 3.12（启动器可通过 uv 选择该解释器）。

- macOS 或 Linux（POSIX）。Windows 未实现也未测试；CLI 会直接报错退出。
- Node.js 20 或更高版本（使用 `node:test` 和 `node:util.parseArgs`）。
- 已自行安装并配置好凭据的 `dsh`。安装方法见
  <https://github.com/deepseek-ai/deepseek-harness>。本项目不安装 dsh、不配置模型凭据，也不修改全局模型设置。显式运行桥接安装器会备份并追加目标 profile 的一个插件项。
- 支持技能的 Codex。

## 安装

```sh
git clone https://github.com/Dsssyc/hey-my-buddy.git
cd hey-my-buddy
uv sync --project deepseek-delegate --python 3.12
```

技能自带依赖，全部内容都在 `deepseek-delegate/` 里。只复制这个目录并在其中运行 `uv sync` 也够用；仓库根目录
没有 package，也不会发布到 npm。全部第三方库由 uv 管理并锁定，包括 PyPI `c-two==0.5.1`、Python MCP SDK 和 PyYAML。Node 文件仅使用内置模块。

### 安装到 Codex，且不覆盖已有内容

如果设置了 `CODEX_HOME` 就读取它，否则使用 `~/.codex`。下面的命令只读取 `CODEX_HOME`，不会设置或导出它，
也不会改动已存在的技能目录。

```sh
CODEX_SKILLS="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$CODEX_SKILLS"
if [ -e "$CODEX_SKILLS/deepseek-delegate" ] || [ -L "$CODEX_SKILLS/deepseek-delegate" ]; then
  echo "已存在，未做任何修改：$CODEX_SKILLS/deepseek-delegate" >&2
else
  ln -s "$PWD/deepseek-delegate" "$CODEX_SKILLS/deepseek-delegate"
fi
```

想用复制而不是符号链接，就把 `ln -s` 那行换成
`cp -R "$PWD/deepseek-delegate" "$CODEX_SKILLS/deepseek-delegate"`，并在副本里重新运行 `uv sync`。如果已经装了
旧版本，请自己先删除或改名；这里不会覆盖它。

## 配置工作区分组

默认开启分组。先把本项目提供的宿主插件安装到已有 `web` profile：

```sh
node deepseek-delegate/scripts/install-workspace-bridge.mjs
```

安装器备份并追加该 profile 的 `cordis.patch.yml`，保留既有配置。长期运行的 profile 会热加载用户 patch；
否则正常启动 `dsh web`。插件使用官方进程内 `ctx.workspaceRegistry.create(cwd)` 和
`workspace.attachSession(sessionId)` 完成分组，通过私有 Unix socket 接收 CLI 请求。
默认地址为 `$DSH_HOME/deepseek-delegate/workspace.sock`，home 默认 `~/.dsh`。
不再需要 Web URL、浏览器 token、cookie 或 HTTP；正常重启后沿用相同 socket 路径。
异常退出若留下旧 socket，先确认原宿主已经停止，再只移除该 socket；插件不会自动删除被占用的端点。

CLI 在任务前检查桥接插件是否可用。观察插件记录本次根会话；headless 进程组完全停止后，宿主检查已持久化的
根会话 header 与规范 cwd，通过官方 API 绑定并回读成员关系。分组不会创建或激活 Agent。
任务结果与分组结果分别保留，失败时可只重试绑定。

宿主与 headless 必须使用相同的会话存储，workspace 存储只由宿主写入，避免 JSON 后端的跨进程写入冲突。
这是本项目基于[官方 workspace API](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/workspace.md)
提供的适配层，不是上游自带的 CLI 命令。可用 `--profile` 指定其他已加载 workspace/persistence 的长期运行 profile。
不要启动第二个 workspace 写入进程去共享正在使用的存储。

独立执行可明确加 `--no-workspace`；分组运行需要宿主插件在线，不会静默降级。
迁移后删除旧 `~/.config/deepseek-delegate/web-url` 文件即可；程序已不再读取它。
旧 `--dsh-web-url*` 和 `--web-timeout` 参数会被拒绝。模型 API 凭据不受影响。

### 只修复分组，不重跑任务

使用绑定失败结果里的 `workspace.sessionId`，或已知的已完成普通会话 ID：

```sh
node "$SKILL_DIR/scripts/run.mjs" --cwd /path/to/project --attach-session SESSION_ID
```

此命令先确认会话存在，再请求绑定；不会运行模型，也不会改全局默认模型，不会扫描或批量重分配历史会话。
会话中记录的 cwd 必须与工作区的规范路径相等；历史目录失效或路径不一致时需要单独处理。

## 快速开始

下面的临时示例明确跳过侧边栏分组。真实项目任务先安装上面的宿主插件，再省略 `--no-workspace`。

```sh
SKILL_DIR="$PWD/deepseek-delegate"
DELEGATE_DEMO_DIR="$(mktemp -d)"
printf '%s\n' '{"numbers":[3,7,11]}' > "$DELEGATE_DEMO_DIR/input.json"
cat > "$DELEGATE_DEMO_DIR/task.md" <<'EOF'
Read input.json and write only result.json containing the sum and count of numbers.
Do not modify input.json or use the network. Verify result.json by reading it.
Acceptance: result.json equals {"sum":21,"count":3}.
EOF

node "$SKILL_DIR/scripts/run.mjs" --no-workspace \
  --cwd "$DELEGATE_DEMO_DIR" \
  --task-file "$DELEGATE_DEMO_DIR/task.md" \
  --timeout 1800
cat "$DELEGATE_DEMO_DIR/result.json"
```

`--cwd` 必填；普通运行需要 `--task-file`，恢复绑定模式不传任务文件。`node "$SKILL_DIR/scripts/run.mjs" --help` 会列出全部选项，且不需要 dsh 或凭据。

## 结果、退出码与日志

stdout 只有一个 JSON 对象，例如：

```json
{"status":"ok","mode":"run","exitCode":0,"signal":null,"error":null,"elapsedSeconds":42.1,"timeoutSeconds":1800,
 "requested":{"provider":"deepseek-official","model":"deepseek-flash","reasoningEffort":"max"},
 "cwd":"/path/to/project","taskFile":"/path/to/task.md","dshBin":"/path/to/dsh",
 "inputDelivery":"inline",
 "logPaths":{"stdout":"/tmp/deepseek-delegate-logs-XXXX/stdout.log","stderr":"/tmp/deepseek-delegate-logs-XXXX/stderr.log"},
 "workspace":{"enabled":true,"bound":true,"id":"workspace-example","path":"/path/to/project","sessionId":"session-example"},
 "finalText":"...","finalTextTruncated":false,
 "note":"exit 0 only means the dsh agent finished, not that the task is correct: inspect the real diff/artifacts and run the relevant checks yourself."}
```

- 运行模式的 `status`：`ok`、`nonzero`、`timeout`、`cancelled` 或 `spawn-error`；恢复绑定失败为 `attach-error`。
- 包装器退出码：只有任务 `ok` 且所需分组已验证才是 `0`；任务、终止或分组失败是 `1`；用法和配置错误是 `2`（只写 stderr，不输出
  JSON）。
- `requested` 是实际写入设置副本的路由与 effort。
- `finalText` 最多 6000 个字符，取自 dsh stdout 日志开头，`finalTextTruncated` 表示是否还有更多内容；
  stderr 和推理过程不会进入 JSON。
- 日志写在每次运行独有的私有目录（目录权限 `0700`，文件权限 `0600`）：给了 `--log-dir` 就放在其下，否则放在
  系统临时目录。已存在的文件不会被截断或复用。

分组结果位于 `workspace`：`enabled`、`bound`、`id`、`path`、`sessionId`，以及失败时的 `error`。
任务的 `status`、`exitCode` 和日志在绑定失败时仍会保留；请同时检查 CLI 进程退出码和 `workspace.bound`。
只有任务成功且请求的分组已验证，CLI 才返回 0；绑定失败返回非零。`mode` 区分 `run` 与 `attach`。

## 选项

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| `--cwd <dir>` | 必填 | dsh 运行的工作目录 |
| `--task-file <file>` | 普通运行必填 | 存放任务内容的文件 |
| `--model <id>` | 见优先级 | 本次运行的模型 ID |
| `--provider <id>` | 见优先级 | 本次运行的 provider ID |
| `--effort <name>` | `max` | 本次运行的 reasoning effort |
| `--timeout <seconds>` | `1800` | headless 执行超时，整数 10–86400；网络请求单独计时 |
| `--log-dir <dir>` | 系统临时目录 | 本次运行私有日志目录的父目录 |
| `--dsh-bin <path>` | 见优先级 | 要执行的 dsh 启动器 |
| `--settings-file <path>` | 见优先级 | 要复制并覆盖的设置文档 |
| `--no-workspace` | 默认关闭 | 明确跳过 Web 分组 |
| `--attach-session <id>` | | 绑定已有已完成会话，不传任务文件、不运行模型 |
| `--workspace-socket <path>` | 见优先级 | 宿主插件的私有 socket 路径 |
| `--workspace-timeout <seconds>` | `15` | 每个请求的超时，整数 1–120 |
| `-h`、`--help` | | 打印帮助并退出（不需要 dsh） |

## 优先级

| 项目 | 顺序（先匹配者生效） |
| --- | --- |
| dsh 启动器 | `--dsh-bin` → `DSH_BIN` → `PATH` 中的 `dsh` → `~/.local/bin/dsh` |
| 设置文档 | `--settings-file` → `DSH_SETTINGS_FILE` → `$DSH_HOME/settings.yaml` → `~/.dsh/settings.yaml` |
| model | `--model` → `DSH_DELEGATE_MODEL` → 设置中的 `agent-default-model.model` → `deepseek-flash` |
| provider | `--provider` → `DSH_DELEGATE_PROVIDER` → 设置中的 `agent-default-model.provider`→ `deepseek-official` |
| 分组 socket | `--workspace-socket` → `DSH_WORKSPACE_SOCKET` → `$DSH_HOME/deepseek-delegate/workspace.sock`（home 默认 `~/.dsh`） |
| effort | `--effort` → `DSH_DELEGATE_EFFORT` → `max`（不会从设置里继承） |

说明：

- 显式指定的设置文件（`--settings-file` 或 `DSH_SETTINGS_FILE`）必须存在；默认位置的文件不存在时按空映射
  处理。
- model/provider/effort 传空值会直接报错，而不是悄悄清掉默认值；effort 永远不会被隐式降低。
- 运行用的设置副本保持其他所有 section 结构不变，包括 `agent-presets` 等。`agent-default-model` 内只写入
  `provider`、`model`、`reasoningEffort`，该 section 中的其他键（例如扩展字段）会原样保留。
- context window 和输出上限由你的 dsh 模型目录与 preset 决定。本包装器不会设置它们，也不会把上下文压到某个
  固定值。1M token 上下文是特定模型配置的示例，不是跨 provider 的承诺。

## 最小任务包

好的任务包能让 dsh 不必猜测：

- 目标与原因；预期输入和输出。
- 工作目录，以及验证所需的命令。
- 允许改动的文件/范围，以及明确的非目标。
- 验收标准：可观察的结果和要运行的检查。
- 关键参考：文件路径、函数名、已知事实、坑。
- 任务需要的背景尽量给全。上下文容量按模型/provider 配置，不要人为压缩任务包。超过 32,000 字节的任务内容
  会以文件引用方式交给 dsh，该文件在运行结束前必须保持可用。

## 运行结束后必须自己复核

dsh 进程结束**不等于**任务正确。Codex 必须查看真实 diff、新增/删除的文件和命令输出，自己运行相关检查
（测试、构建、lint 或针对性复现），并逐条对照验收标准。JSON 里的 `note` 字段就是在强调这一点。

## 平台与支持边界

- **仅支持 POSIX。** 实际支持 macOS 和 Linux；没有 Windows 代码路径。
- `--cwd` 只是工作目录，不是沙箱。请在任务包里写清允许范围；需要更强隔离时给 dsh 独立工作区（例如 git
  worktree）。
- 设置覆盖假设启动的 profile 以 `settings` 为 entry id 挂载 dsh settings provider（官方自带 profile 就是
  这样）。自定义插件或 profile 若用其他方式读取设置，或把配置放在 `settings.yaml` 之外的 profile patch
  文件里，不在复制范围内，会继续生效。
- model/provider ID 不会与你的 provider 目录做校验；包装器不会降级、替换或重试。请自行选择 provider 支持
  的 ID。
- 超时或取消时，包装器只终止自己启动的 dsh 进程组，然后以非零退出码报告 `timeout`/`cancelled`；即使子进程
  随后以 0 退出，也绝不报告 `ok`。
- 本项目不会自行发布、推送或对外发消息；这类范围由你授权，技能继承你本次会话已授予的权限。

## 测试

```sh
uv run --frozen --project deepseek-delegate python -m buddy.checks
```

测试在临时目录里用 mock `dsh` 可执行文件驱动真实 CLI，不调用模型、不读取你的真实设置、不使用你的 `HOME` 或
`CODEX_HOME`。覆盖启动器解析、设置优先级与保留、任务原文传递、32KB 文件引用切换、6000 字符 stdout 上限、
独有私有日志、spawn 失败、超时与取消清理。本仓库不会发布到 npm。

新增测试使用本地 socket 模拟服务，覆盖根会话捕获、私有端点权限、存在性检查、分组回读和恢复；不会访问真实宿主。

可选的真实官方包验证（无模型调用，使用隔离临时存储）：

```sh
node deepseek-delegate/tests/manual/real-workspace.mjs --dsh-lib /path/to/node_modules/@deepseek-ai
```

验证绑定、未知会话拒绝、Cordis 插件卸载与同路径重启重连，不修改实际会话或工作区。

## 许可证

[MIT](LICENSE)。可单独复制的 skill 目录也附带完整许可证。
