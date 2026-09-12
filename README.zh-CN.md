# hey-my-buddy

[English](README.md)

一个体积很小的 Codex 技能，外加一个单文件 CLI：把**目标清晰、边界明确**的任务交给本地
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（`dsh`）执行一次，然后只回传一个紧凑的
JSON 结果。定框、选路和最终验收仍由 Codex 负责，dsh 只承担这次有界任务的主体工作。这是一个专注的技能和
CLI，不是框架，也不是 agent 平台。

## 它做什么

- 通过 `dsh --profile headless` 执行一次有界任务，并真正覆盖本次运行的 model/effort。
- 把设置文档复制到私有临时 JSON，只替换 `agent-default-model` 里的路由和 effort，再用临时 `--patch`
  覆盖指向这份副本。原始设置与凭据不会被修改。
- stdout/stderr 写入每次运行独有的私有日志目录，stdout 只输出一个 JSON 对象。
- 不经过 shell：启动器和任务内容都以参数数组传递，含空格路径、前导短横线、换行、shell 元字符都保持原样。
- 除你要求的这一次 dsh 运行外不会自行调用模型，也不会静默重试、降级或替换路由。

## 环境要求

- macOS 或 Linux（POSIX）。Windows 未实现也未测试；CLI 会直接报错退出。
- Node.js 20 或更高版本（使用 `node:test` 和 `node:util.parseArgs`）。
- 已自行安装并配置好凭据的 `dsh`。安装方法见
  <https://github.com/deepseek-ai/deepseek-harness>。本项目不安装 dsh、不登录，也不修改 dsh/Codex 的全局设置。
- 支持技能的 Codex。

## 安装

```sh
git clone https://github.com/Dsssyc/hey-my-buddy.git
cd hey-my-buddy
npm --prefix deepseek-delegate ci
```

技能自带依赖，全部内容都在 `deepseek-delegate/` 里。只复制这个目录并在其中运行 `npm ci` 也够用；仓库根目录
没有 package，也不会发布到 npm，唯一的运行时依赖是持续维护的 `js-yaml` 解析器。

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
`cp -R "$PWD/deepseek-delegate" "$CODEX_SKILLS/deepseek-delegate"`，并在副本里重新运行 `npm ci`。如果已经装了
旧版本，请自己先删除或改名；这里不会覆盖它。

## 快速开始

先写任务包，再运行 CLI：

```sh
SKILL_DIR="$PWD/deepseek-delegate"
DELEGATE_DEMO_DIR="$(mktemp -d)"
printf '%s\n' '{"numbers":[3,7,11]}' > "$DELEGATE_DEMO_DIR/input.json"
cat > "$DELEGATE_DEMO_DIR/task.md" <<'EOF'
Read input.json and write only result.json containing the sum and count of numbers.
Do not modify input.json or use the network. Verify result.json by reading it.
Acceptance: result.json equals {"sum":21,"count":3}.
EOF

node "$SKILL_DIR/scripts/run.mjs" \
  --cwd "$DELEGATE_DEMO_DIR" \
  --task-file "$DELEGATE_DEMO_DIR/task.md" \
  --timeout 1800
cat "$DELEGATE_DEMO_DIR/result.json"
```

`--cwd` 和 `--task-file` 必填。`node "$SKILL_DIR/scripts/run.mjs" --help` 会列出全部选项，且不需要 dsh 或凭据。

## 结果、退出码与日志

stdout 只有一个 JSON 对象，例如：

```json
{"status":"ok","exitCode":0,"signal":null,"error":null,"elapsedSeconds":42.1,"timeoutSeconds":1800,
 "requested":{"provider":"deepseek-official","model":"deepseek-flash","reasoningEffort":"max"},
 "cwd":"/path/to/project","taskFile":"/path/to/task.md","dshBin":"/path/to/dsh",
 "inputDelivery":"inline",
 "logPaths":{"stdout":"/tmp/deepseek-delegate-logs-XXXX/stdout.log","stderr":"/tmp/deepseek-delegate-logs-XXXX/stderr.log"},
 "finalText":"...","finalTextTruncated":false,
 "note":"exit 0 only means the dsh agent finished, not that the task is correct: inspect the real diff/artifacts and run the relevant checks yourself."}
```

- `status`：`ok`、`nonzero`、`timeout`、`cancelled` 或 `spawn-error`。
- 包装器退出码：只有 `ok` 是 `0`；失败或被终止的运行是 `1`；用法和配置错误是 `2`（只写 stderr，不输出
  JSON）。
- `requested` 是实际写入设置副本的路由与 effort。
- `finalText` 最多 6000 个字符，取自 dsh stdout 日志开头，`finalTextTruncated` 表示是否还有更多内容；
  stderr 和推理过程不会进入 JSON。
- 日志写在每次运行独有的私有目录（目录权限 `0700`，文件权限 `0600`）：给了 `--log-dir` 就放在其下，否则放在
  系统临时目录。已存在的文件不会被截断或复用。

## 选项

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| `--cwd <dir>` | 必填 | dsh 运行的工作目录 |
| `--task-file <file>` | 必填 | 存放任务内容的文件 |
| `--model <id>` | 见优先级 | 本次运行的模型 ID |
| `--provider <id>` | 见优先级 | 本次运行的 provider ID |
| `--effort <name>` | `max` | 本次运行的 reasoning effort |
| `--timeout <seconds>` | `1800` | 墙钟超时，10–86400 的整数 |
| `--log-dir <dir>` | 系统临时目录 | 本次运行私有日志目录的父目录 |
| `--dsh-bin <path>` | 见优先级 | 要执行的 dsh 启动器 |
| `--settings-file <path>` | 见优先级 | 要复制并覆盖的设置文档 |
| `-h`、`--help` | | 打印帮助并退出（不需要 dsh） |

## 优先级

| 项目 | 顺序（先匹配者生效） |
| --- | --- |
| dsh 启动器 | `--dsh-bin` → `DSH_BIN` → `PATH` 中的 `dsh` → `~/.local/bin/dsh` |
| 设置文档 | `--settings-file` → `DSH_SETTINGS_FILE` → `$DSH_HOME/settings.yaml` → `~/.dsh/settings.yaml` |
| model | `--model` → `DSH_DELEGATE_MODEL` → 设置中的 `agent-default-model.model` → `deepseek-flash` |
| provider | `--provider` → `DSH_DELEGATE_PROVIDER` → 设置中的 `agent-default-model.provider`→ `deepseek-official` |
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
npm --prefix deepseek-delegate test
```

测试在临时目录里用 mock `dsh` 可执行文件驱动真实 CLI，不调用模型、不读取你的真实设置、不使用你的 `HOME` 或
`CODEX_HOME`。覆盖启动器解析、设置优先级与保留、任务原文传递、32KB 文件引用切换、6000 字符 stdout 上限、
独有私有日志、spawn 失败、超时与取消清理。本仓库不会发布到 npm。

## 许可证

[MIT](LICENSE)。可单独复制的 skill 目录也附带完整许可证。
