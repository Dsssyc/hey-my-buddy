# hey-my-buddy


[English](README.md)

[架构设计](docs/decisions/001-python-transactional-blackboard.md) ·
[0.4.0 验收记录](docs/acceptance/python-blackboard-0.4.0.md)

通过 Codex 插件把边界明确的任务交给本地 dsh。**基于 C-Two 的 Python 事务型黑板服务**拥有全部任务记录；独立的 Python worker 领取排队任务，并通过适配器执行（默认 `dsh`，另有 `command` 和由调用方 agent 执行的 `external`）。Codex 负责明确任务和最终验收。同一服务和持久任务也可直接从 CLI 使用；原来的独立 Node runner 仍可单独运行，同时也是 `dsh` 适配器每次执行所启动的 runner。

## Codex app 插件

```sh
uv sync --project deepseek-delegate --python 3.12
```

仓库包含 `hey-my-buddy` 插件：一个精简 skill 加上由 uv 管理的 Buddy CLI。**这里没有 MCP 服务器，也不需要任何 MCP 注册**：插件只有 skill + CLI，所有操作都通过 `buddy` 命令访问同一个基于 C-Two 的 Python 事务型黑板服务。

```sh
BUDDY=deepseek-delegate/scripts/launch-buddy.sh     # 或：node deepseek-delegate/scripts/buddy.mjs
"$BUDDY" start '{"requestId":"fix-123","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
"$BUDDY" await '{"runId":"<runId>","waitSeconds":28800}'
```

默认工作流是"启动一次、记下 `runId`、在本回合内 await 同一个任务"：`buddy start` 立即返回，`buddy await` 保持连接到任务结束并打印最终信封（runId、执行状态、进程关闭确认、runner 结果与日志路径）。等待前就知道 runId，正是 `buddy inquire` 能在任务仍在执行时观察它或向它提问的原因。`buddy run` 仍是可选的一次性便捷命令：它启动（或恢复）持久任务并保持连接，`waitSeconds` 默认为 `timeoutSeconds + 60 秒`关闭宽限、上限 24 小时。等待期间本回合保持活动，不使用 heartbeat、cron 或模型轮询。带相同 `requestId` 重跑同一条 `start`（或 `run`）命令会恢复同一个任务，绝不会再次启动 dsh。`buddy result` 可再次读取既有任务，`buddy dashboard` 返回私有的只读本地任务页，页面刷新不调用模型。完整子命令：`run`、`await`、`start`、`submit`、`status`、`wait`、`watch`、`events`、`result`、`list`、`cancel`、`retry`、`acknowledge`、`inquire`、`message`、`messages`、`message-get`、`message-update`、`artifacts`、`workers`、`worker-register`、`worker-claim`、`worker-reconcile`、`worker-renew`、`worker-progress`、`worker-result`、`worker-release`、`worker-start`、`worker-stop`、`wait-capacity`、`capabilities`、`adapters`、`runtime`、`dashboard`、`legacy-import`、`restart`、`health`、`stop`。

提交的任务先以 `queued` 排队，等 worker 领取后才开始执行；`queueReason` 说明等待原因（`awaiting-worker`、`capacity`、`cwd-overlap`、`exclusive-resource`）。这是有意为之：容量与资源冲突会让任务排队，而不是返回 BUSY；任务会保留位置直到有匹配的 worker 空闲。服务默认只运行一个并发 attempt（`BUDDY_MAX_CONCURRENT`，1–8），并且只控制自己启动的 worker 进程；现有 dsh 宿主/会话的生命周期和原始设置仍各自独立。分组默认开启（`workspace: true`），沿用下方的工作区宿主桥接，传 `workspace: false` 可显式退出分组。`cwd` 只是工作目录，不是沙箱；独立的用户 dsh 进程不受 Buddy 协调。

等待窗口现在由 CLI 自己决定：默认是 `timeoutSeconds + 60 秒`关闭宽限，上限为 CLI 既有的 24 小时最大值（86400 秒），因此一次调用通常就能覆盖整个任务；需要更短、可恢复的等待时显式传 `waitSeconds`。不再有宿主工具超时，也不再有 `BUDDY_TOOL_WAIT_BUDGET_SECONDS`。执行期限独立且未变：`timeoutSeconds` 是 Buddy 对整个 dsh 进程组的**执行**超时（`timeoutSeconds`，默认 1800 秒、整数 10–86400）：从进程启动开始计时，覆盖所有模型与工具步骤，不是模型回合或会话限制；长任务请显式传入，例如 `"timeoutSeconds": 28800` 表示 8 小时。runner 期限长于一次等待窗口是允许的：任务继续运行，信封给出 `waitCoversRunnerDeadline: false`，用 `buddy await` 继续等待同一个 runId 即可。

杀掉正在等待的 CLI 进程（Ctrl-C、终端关闭、`waitSeconds` 到期）只会取消等待：只要仍有 worker 拥有该任务，持久任务就会继续执行并可凭 requestId/runId 恢复。任务也可能因自身的执行期限到期而结束；`buddy cancel`（或 `buddy stop`）通过持久取消意图通知拥有子进程的 worker，由它结束自己的进程组。守护进程重启后，执行中的 attempt 会被标记为 `uncertain` 并保留其资源声明：独立 worker 继续持有子进程与期限，凭 attempt 身份和能力重新挂接；`buddy restart` 保留正在执行的任务。worker 会自动补交已持久保存的完成回执，直到服务确认，期间不会再次执行任务。重新执行须显式调用 `buddy retry`，创建新一代 attempt。

runner 结束不等于验收通过。`buddy acknowledge` 在结果已持久化且进程关闭已确认后，记录你真实复核过的结果（包括已复核的失败）；它不会把失败执行变成成功，用不同的 note 或 `verdict` 重复确认会返回 `CONFLICT`。退出码 0 也不代表任务成功：要读取 `status`、`outcome`、`resultDelivered`、`shutdownConfirmed`，并检查真实产物。

`buddy start` 立即返回 `runId`，它既是默认前台工作流的第一步，也是显式后台场景的入口：需要结束回合后自动继续时，Buddy skill 会创建官方 App heartbeat，其提示词调用同一个 CLI 读取既有任务、验收结果并删除 heartbeat。该 heartbeat 是周期性后台跟进，不是完成即推送；Buddy 并未解决原生的回合结束后 App 唤醒问题。详见[插件服务说明](deepseek-delegate/references/plugin-service.md)。

### 从已移除的 MCP 路径迁移

不再需要为 MCP 安装任何东西。如果你以前注册过本插件的 MCP 服务器，请自行清理残留条目（Buddy 不会自动改你的配置）：

- `~/.codex/config.toml` 中的 `[mcp_servers.buddy_ctwo]`（以及任何 `plugins.*.mcp_servers.*` 里的 Buddy 条目，包括其 `tool_timeout_sec` / `approval_mode` 字段）；
- App 记住的 Buddy 插件级工具授权；
- 旧插件缓存副本里已经无用的 `mcp.json`。

请保留所有其它 Codex/App 工具与设置，尤其是官方的 App heartbeat 自动化——它不属于 Buddy。

### 安全升级插件

在替换插件缓存版本之前，先让服务静默并**停掉它**——纯文档的 cachebuster 更新也一样：

```sh
deepseek-delegate/scripts/launch-buddy.sh stop    # 或用旧版本的启动脚本
# 然后再安装/刷新插件版本
```

`buddy stop` 会取消排队任务、为每个活动 attempt 写入持久取消请求、在有界时间内 drain 并报告未解决的 attempt，因此是升级前的受支持操作。`buddy restart` 只让守护进程脱离而不取消任何工作，独立 worker 会继续运行；但运行中的守护进程仍在使用启动时加载的代码路径，在其脚下替换缓存目录可能让服务继续运行在已被删除的路径上（下一次 `dsh` 任务会因 runner 不可用而失败）。若要让安装不受缓存替换影响，请在插件缓存之外物化内容寻址的稳定运行时（见[运行时打包](deepseek-delegate/references/plugin-service.md#packaging-migration-and-unrelated-app-tools)）；存在 READY 运行时时，冷启动会自动从它启动服务。失败模式与细节见[插件服务说明](deepseek-delegate/references/plugin-service.md#cli-wait-window-and-upgrade-ordering)。

## 服务 CLI

同一个服务和同一批持久任务也可以直接从 CLI 使用：

```sh
uv run --frozen --project deepseek-delegate buddy health
# 默认：先 start 一次并记下 runId，再 await 同一个持久任务；await 有自己的有界窗口
# （waitSeconds 1..86400，默认 86400），且绝不启动任务。
uv run --frozen --project deepseek-delegate buddy start '{"requestId":"x","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
uv run --frozen --project deepseek-delegate buddy await '{"runId":"<runId>","waitSeconds":28800}'
# 一次性便捷命令：阻塞式运行的等待窗口 = timeoutSeconds + 60 秒宽限，上限 24 小时。
uv run --frozen --project deepseek-delegate buddy run '{"requestId":"x","task":"...","cwd":"/abs/path","timeoutSeconds":28800}'
uv run --frozen --project deepseek-delegate buddy status '{"runId":"<runId>"}'
uv run --frozen --project deepseek-delegate buddy result '{"runId":"<runId>"}'
uv run --frozen --project deepseek-delegate buddy inquire '{"runId":"<runId>"}'
uv run --frozen --project deepseek-delegate buddy events '{"after":0}'
uv run --frozen --project deepseek-delegate buddy workers
uv run --frozen --project deepseek-delegate buddy cancel '{"runId":"<runId>"}'
uv run --frozen --project deepseek-delegate buddy acknowledge '{"runId":"<runId>","note":"reviewed the diff and ran the tests","verdict":"accepted"}'
uv run --frozen --project deepseek-delegate buddy restart
uv run --frozen --project deepseek-delegate buddy stop
# 使用其他适配器的显式板块提交（一个 argv 进程，绝不经过隐式 shell）：
uv run --frozen --project deepseek-delegate buddy submit '{"requestId":"y","task":"...","cwd":"/abs/path","adapter":"command","argv":["/bin/echo","hi"]}'
# 离线、单事务导入已移除 Node 实现的记录（默认 dry-run）：
uv run --frozen --project deepseek-delegate buddy legacy-import '{"sourceDir":"/old/state","dryRun":true}'
```

只有 `run` 和 `await` 属于长等待契约：`run` 在自己的等待窗口内阻塞，`await` 等待到自己的 `waitSeconds`。其他子命令只有有限等待或立即返回：`wait` 和 `watch` 最多等 30 秒；`inquire` 除非用 `waitMs` 请求一个有界答复窗口（上限 30 秒），否则立即返回；`stop` 等服务 drain 完成后返回。`wait` 和 `watch` 绝不冷启动服务。`await` 只等待既有的 `requestId` 或 `runId`，绝不启动任务。CLI 打印一个 JSON 对象（带缩进），退出码 0 只说明调用返回：要读取 `status`/`outcome`/`resultDelivered`/`shutdownConfirmed` 并检查真实产物。底层错误会打印 `error` 对象并以 1 退出。所有恢复信封都会给出真实命令（`buddy status|await|result|cancel`）和既有 run ID。完整的等待、worker、适配器、问询与恢复契约见[插件服务说明](deepseek-delegate/references/plugin-service.md)。

`buddy inquire` 不带问题时是只读的：它报告观测到的执行状态、明确标注为估算的期限和最近的工具活动，不承诺百分比或准确 ETA，并列出所有无法观测的字段。要向该任务自己的在线 agent 提一个可关联的问题，必须同时传 `inquiryId` 和 `question`；要再次读取同一个问题，必须同时重复相同文本和相同 ID（只给 ID 不是有效查询）。相同 ID 配不同文本会返回 `CONFLICT`，运行中和终态任务都是如此；`waitMs`（上限 30000）只限制这次调用等待答复的时间，绝不延长或取消任务。每个问题与答复上限 4000 UTF-8 字节，每个任务最多保留 32 条问询；没有问询能力的适配器会给出明确原因，而不是伪造进度。没有自动定时器或模型触发的问询功能；任务允许执行 shell 命令时，agent 仍可在需要时显式调用该 CLI。

## 它做什么

插件服务通过适配器执行任务。`dsh` 适配器（默认）为每个任务启动既有的 Node runner，它会：

- 通过 `dsh --profile headless` 执行一次有界任务，并真正覆盖本次运行的 model/effort。
- 把设置文档复制到私有临时 JSON，只替换 `agent-default-model` 里的路由和 effort，再用临时 `--patch`
  覆盖指向这份副本。原始设置与凭据不会被修改。
- stdout/stderr 写入每次运行独有的私有日志目录，stdout 只输出一个 JSON 对象。
- 不经过 shell：启动器和任务内容都以参数数组传递，含空格路径、前导短横线、换行、shell 元字符都保持原样。
- 除你要求的这一次 dsh 运行外不会自行调用模型，也不会静默重试、降级或替换路由。

`command` 适配器只运行一个显式 `argv` 进程，绝不经过隐式 shell。`external` 适配器完全不启动本地进程：由调用方自己的 agent 通过公开的 C-Two 契约（`deepseek-delegate/python/buddy/client.py`）领取任务并自行上报结果。`buddy capabilities` 会报告每个适配器的 `executedBy`，以及服务如实声明的能力限制（不支持 steer/resume、不支持原生 App 唤醒、不支持 PostgreSQL/HA、不支持远程多租户）。

## 环境要求

- uv 和 Python 3.12 以上、3.15 以下（`requires-python = ">=3.12,<3.15"`；启动器通过 uv 选择 Python 3.12）。

- macOS 或 Linux（POSIX）。Windows 未实现也未测试；CLI 会直接报错退出。
- Node.js 20 或更高版本，`dsh` 适配器和它所驱动的 dsh 需要它；`command` 与 `external` 适配器不需要 Node。本项目只使用 Node 内置模块，不需要 npm 安装依赖。
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
没有 package，也不会发布到 npm。全部第三方库由 uv 管理并锁定：PyPI `c-two==0.5.1` 与 PyYAML。Node 文件仅使用内置模块，`mcp` Python SDK 已不再是任何组件的依赖。若要让安装独立于 Codex 插件缓存，请在缓存之外物化内容寻址的稳定运行时：

```sh
uv run --frozen --project deepseek-delegate python -c "from buddy import runtime; runtime.materialize()"
uv run --frozen --project deepseek-delegate buddy runtime
```

`materialize()` 先把完整运行时资产复制进最终的内容寻址目录，在那里运行 `uv sync --frozen`，最后写入 `READY.json`；凭据、用户数据、测试和已有虚拟环境永远不会被复制。存在 READY 运行时时，冷启动会用该运行时自带的解释器启动服务及其 worker supervisor；`buddy runtime` / `buddy health` 会如实报告当前进程是否真的运行在其中。详见[运行时打包](deepseek-delegate/references/plugin-service.md#packaging-migration-and-unrelated-app-tools)。

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

`dsh` 适配器的 runner 在任务前检查桥接插件是否可用。观察插件记录本次根会话；headless 进程组完全停止后，宿主检查已持久化的
根会话 header 与规范 cwd，通过官方 API 绑定并回读成员关系。分组不会创建或激活 Agent。
任务结果与分组结果分别保留，失败时可只重试绑定。

宿主与 headless 必须使用相同的会话存储，workspace 存储只由宿主写入，避免 JSON 后端的跨进程写入冲突。
这是本项目基于[官方 workspace API](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/workspace.md)
提供的适配层，不是上游自带的 CLI 命令。可用 `--profile` 指定其他已加载 workspace/persistence 的长期运行 profile。
不要启动第二个 workspace 写入进程去共享正在使用的存储。

独立执行可明确加 `--no-workspace`；分组运行需要宿主插件在线，不会静默降级。服务运行默认 `workspace: true`，同样需要该桥接；`workspace: false` 等同于 runner 的 `--no-workspace`。
迁移后删除旧 `~/.config/deepseek-delegate/web-url` 文件即可；程序已不再读取它。
旧 `--dsh-web-url*` 和 `--web-timeout` 参数会被拒绝。模型 API 凭据不受影响。

### 只修复分组，不重跑任务

使用绑定失败结果里的 `workspace.sessionId`，或已知的已完成普通会话 ID：

```sh
node "$SKILL_DIR/scripts/run.mjs" --cwd /path/to/project --attach-session SESSION_ID
```

此命令先确认会话存在，再请求绑定；不会运行模型，也不会改全局默认模型，不会扫描或批量重分配历史会话。
会话中记录的 cwd 必须与工作区的规范路径相等；历史目录失效或路径不一致时需要单独处理。

## 独立 runner 快速开始

`scripts/run.mjs` 是 `dsh` 适配器执行每个 dsh 任务时启动的底层 runner。它仍然随仓库提供并经过测试，也可以在没有持久黑板记录和问询通道时单独使用（独立调用会报告 `inquiry.enabled: false`），直接得到原始 runner 契约。下面的临时示例明确跳过侧边栏分组。真实项目任务先安装上面的宿主插件，再省略 `--no-workspace`。

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

## 独立 runner 结果、退出码与日志

stdout 只有一个 JSON 对象，例如：

```json
{"status":"ok","mode":"run","exitCode":0,"signal":null,"error":null,"elapsedSeconds":42.1,"timeoutSeconds":1800,
 "requested":{"provider":"deepseek-official","model":"deepseek-flash","reasoningEffort":"max"},
 "cwd":"/path/to/project","taskFile":"/path/to/task.md","dshBin":"/path/to/dsh",
 "inputDelivery":"inline",
 "logPaths":{"stdout":"/tmp/deepseek-delegate-logs-XXXX/stdout.log","stderr":"/tmp/deepseek-delegate-logs-XXXX/stderr.log","capture":"/tmp/deepseek-delegate-logs-XXXX/capture.json"},
 "inquiry":{"enabled":false,"socketPath":null,"resultsPath":null,"errorPath":null,"error":null},
 "finalText":"...","finalTextTruncated":false,
 "workspace":{"enabled":true,"bound":true,"id":"workspace-example","path":"/path/to/project","sessionId":"session-example"},
 "processState":{"shutdownConfirmed":true},
 "note":"exit 0 only means the dsh agent finished, not that the task is correct: inspect the real diff/artifacts and run the relevant checks yourself."}
```

- 运行模式的 `status`：`ok`、`nonzero`、`timeout`、`cancelled` 或 `spawn-error`；恢复绑定失败为 `attach-error`。
- 包装器退出码：只有任务 `ok` 且所需分组已验证才是 `0`；任务、终止或分组失败是 `1`；用法和配置错误是 `2`（只写 stderr，不输出
  JSON）。
- `requested` 是实际写入设置副本的路由与 effort。
- `finalText` 最多 6000 个字符，取自 dsh stdout 日志开头，`finalTextTruncated` 表示是否还有更多内容；
  stderr 和推理过程不会进入 JSON。
- `inquiry` 表示所属 Buddy 服务是否为本任务挂载了私有问询桥接（`enabled`、路径及启动 `error`）。桥接启动失败
  不会让任务失败；不带服务的独立 `run.mjs` 调用报告 `enabled: false`。
- `processState.shutdownConfirmed` 只有在确认所属进程组已停止时才为 true；分组和 `buddy acknowledge` 都需要它。
- 日志写在每次运行独有的私有目录（目录权限 `0700`，文件权限 `0600`）：给了 `--log-dir` 就放在其下，否则放在
  系统临时目录。已存在的文件不会被截断或复用。

分组结果位于 `workspace`：`enabled`、`bound`、`id`、`path`、`sessionId`，以及失败时的 `error`。
任务的 `status`、`exitCode` 和日志在绑定失败时仍会保留；请同时检查 CLI 进程退出码和 `workspace.bound`。
只有任务成功且请求的分组已验证，CLI 才返回 0；绑定失败返回非零。`mode` 区分 `run` 与 `attach`。

## 独立 runner 选项

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| `--cwd <dir>` | 必填 | dsh 运行的工作目录 |
| `--task-file <file>` | 普通运行必填 | 存放任务内容的文件 |
| `--model <id>` | 见优先级 | 本次运行的模型 ID |
| `--provider <id>` | 见优先级 | 本次运行的 provider ID |
| `--effort <name>` | `max` | 本次运行的 reasoning effort |
| `--timeout <seconds>` | `1800` | 整个进程组的执行超时，整数 10–86400；workspace socket 请求另用 `--workspace-timeout` |
| `--log-dir <dir>` | 系统临时目录 | 本次运行私有日志目录的父目录 |
| `--dsh-bin <path>` | 见优先级 | 要执行的 dsh 启动器 |
| `--settings-file <path>` | 见优先级 | 要复制并覆盖的设置文档 |
| `--no-workspace` | 默认关闭 | 明确跳过工作区分组 |
| `--attach-session <id>` | | 绑定已有已完成会话，不传任务文件、不运行模型 |
| `--workspace-socket <path>` | 见优先级 | 宿主插件的私有 socket 路径 |
| `--workspace-timeout <seconds>` | `15` | 每个请求的超时，整数 1–120 |
| `--inquiry-socket <path>`、`--inquiry-token <token>`、`--inquiry-results <path>` | | 每次运行的私有问询通道，由所属 Buddy 服务成组提供；不要手工传入 |
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
- 服务运行默认 `workspace: true`，要求工作区宿主桥接已安装且在线；不会静默降级为不分组。用 `workspace: false`
  （或独立 runner 的 `--no-workspace`）显式退出分组。
- 只控制 Buddy 自己的任务；独立的用户 dsh 进程和会话是分开的，不受 Buddy 协调、分组或取消。
- 所有权威状态都是 `BUDDY_STATE_DIR`（默认 `~/.local/share/hey-my-buddy`）下的本地 SQLite。
  PostgreSQL/HA、远程多租户和原生 App 唤醒都没有实现，也不在声明范围内。
- 容量以及 cwd/独占资源冲突会让任务带 `queueReason` 排队，而不是拒绝提交；`BUDDY_MAX_CONCURRENT`
  （1–8，默认 1）限制活动 attempt 数量，一个 worker 同时只运行一个 attempt。
- CLI 命令使用当前 Codex 任务 shell 的权限；Buddy 不会授予额外权限，其服务只是同用户的本地进程。已移除的 MCP 服务器其插件级 `approval_mode` 也随之消失，shell 自身的沙箱是唯一边界，不会
  扩大另一个任务的沙箱。
- 设置覆盖假设启动的 profile 以 `settings` 为 entry id 挂载 dsh settings provider（官方自带 profile 就是
  这样）。自定义插件或 profile 若用其他方式读取设置，或把配置放在 `settings.yaml` 之外的 profile patch
  文件里，不在复制范围内，会继续生效。
- model/provider ID 不会与你的 provider 目录做校验；包装器不会降级、替换或重试。请自行选择 provider 支持
  的 ID。
- 超时或取消时，只终止 Buddy 自己启动的进程组，并如实报告 `timeout`/`cancelled`/`failed`；即使子进程
  随后以 0 退出，也绝不报告 `ok`。
- 本项目不会自行发布、推送或对外发消息；这类范围由你授权，技能继承你本次会话已授予的权限。

## 测试

```sh
uv run --frozen --project deepseek-delegate python -m buddy.checks
```

测试在临时目录里用 mock `dsh` 可执行文件驱动真实 CLI，不调用模型、不读取你的真实设置、不使用你的 `HOME` 或
`CODEX_HOME`。覆盖启动器解析、设置优先级与保留、任务原文传递、32KB 文件引用切换、6000 字符 stdout 上限、
独有私有日志、spawn 失败、超时与取消清理。其他测试在私有状态目录中驱动真实守护进程，覆盖事务型黑板
（回滚与 SQLite 完整性、重复提交与 claim 重放、容量排队、cwd 与独占资源声明、三处崩溃窗口、真实守护进程
重启后保留同一 attempt/worker/期限/结果、过期 generation 与错误能力拒绝、租约不确定性、并发等待者下的
`WAIT_OVERLOAD`、取消竞态）、阻塞委派路径（等待超时与断线恢复）、问询状态机与其边界、`dsh`/`command`/
`external` 三个适配器（含通过公开客户端接入的外部 worker）、不会复制凭据或环境的运行时物化，以及离线
legacy 导入（dry-run、幂等、回滚与指纹校验）；不会访问真实宿主。
本仓库不会发布到 npm。

可选的真实官方包验证（无模型调用，使用隔离临时存储）：

```sh
node deepseek-delegate/tests/manual/real-workspace.mjs --dsh-lib /path/to/node_modules/@deepseek-ai
```

验证绑定、未知会话拒绝、Cordis 插件卸载与同路径重启重连，不修改实际会话或工作区。

## 许可证

[MIT](LICENSE)。可单独复制的 skill 目录也附带完整许可证。
