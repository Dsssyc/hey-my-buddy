# ADR-006 研究记录：ZCode 以 dsh 适配器方式接入黑板

## 状态

状态：已在 0.6 实现并接入。本文件最初是 2026-09-22 的技术可行性研究记录；下面的第一至第九节作为历史依据与实测证据保留，不再描述当前行为。当前行为以源码和 [architecture.md](../reference/architecture.md)、[workers.md](../reference/workers.md)、[workflow.md](../reference/workflow.md) 为准。

## 当前实现路线（0.6）

`zcode` 是一个一等编码适配器（`src/buddy/adapters/zcode.py`），与 `dsh` 并列复用黑板既有的任务、attempt、worker、容量与回执机制。

- 执行形态：ZCode 原生 **app-server** 协议（`zcode app-server --cwd <cwd>`，stdio 上的原生 NDJSON `{id, method, params}` 消息）创建或恢复根会话；该通道不带 JSON-RPC 版本字段。`src/buddy/adapters/zcode_runner.py` 是持有该原生进程组的控制器，外层 Python 适配器再持有控制器进程组，只有控制器回执与组级停机观测同时成立才算确认停止。
- 结项工具：每个 attempt 注入一个只属于该原生会话的 **MCP 终端工具**（`mcp__buddy_<hash>__buddy_finish_turn`，由 `python -m buddy.adapters.zcode_mcp` 提供，`isolation: "session"`）。结构化结果只能来自该根会话对该工具的一次成功调用；`provenance` 记录原生 tool result、turn/end 与 session-close 证据（`settlement: "session-closed"`），不伪造 DSH 的 tool/turn/flush 凭据。
- 提供方：**仅支持 API-key 提供方**（`api-key`、`zhipu-coding-plan-api-key`）。前置检查只返回 provider 的 access 分类；执行前会把已安装 ZCode 的内置和个人 provider 配置文件复制到 attempt 私有目录，以 `0600` 权限保存，这些快照可能包含 API key。原生进程读取和规范化这些私有快照，源配置不被修改；OAuth store 和其余 `~/.zcode` 状态不会复制，密钥不会作为公开结果返回。`available()` 与 `prepare()` 会明确报告 OAuth account provider 不可用。
- 身份读回：结果信封分别记录 `requested`（提交的 provider/model/effort）、`resolved`（原生 `session/setModel` 与 `session/setThoughtLevel` 之后的实际设置）与 `observed: null`；`observed` 不会把 resolved 伪装成实际服务的模型版本。
- 模型与 effort 发现：`model-catalog-refresh` 通过原生 app-server 的 `session/create` 快照按模型返回原生 reasoning level 列表；没有可配置 effort 的模型被省略并给出 warning。显式请求的 provider/model/effort 必须同时出现在原生目录与 effort 列表中，否则在派发前拒绝。
- 续跑：首个回合为 `resumeMode: "initial"`。已有证据确认前一回合结项、且冻结的执行配置不变时，后续回合使用 `native-session`；控制器在调用 `session/resume` 前校验私有绑定 `{taskId, sessionId, cwd, configuration}`，其中 configuration 为 provider/model/effort，必须与当前 Goal、checkout 和配置一致，并要求返回同一根会话。原生恢复请求的绑定缺失或不匹配会明确失败；没有已确认的前一会话或配置发生变化时，使用 `reconstructed-new-session`，根据固定接续输入创建新的根会话。
- 工具边界：编码工具与内部 subagent 正常工作、不受限制；适配器注入的 MCP 只负责记录结构化结项结果。
- 路由边界：无选择器或没有合法候选时产生持久的路由 attention，由 Host 在同一个 Goal 上用完整 `configuration` 接续或 `reroute:true` 重试；工具无关的决策选择器只支持 DSH，本项目没有 ZCode 决策助手。
- 诚实边界：目前没有 ZCode 侧的 inquiry 桥（`inquire` 对 `zcode` 如实报告没有该能力），也还没有安装态真实提供方的 0.6 验收记录。

本文回答一个问题：另一个编码代理 ZCode 能否像 dsh 那样接入黑板，由黑板调度、作为一个受适配器管理的执行器运行。分析基于对当时实现源码的通读和对本机 ZCode CLI 的实测；下面引用的文件路径、行号与实测环境是 2026-09-22 的 `socu/python-blackboard` 分支快照，指向源码的链接已改指当前 `src/buddy/` 布局，仅用于核对相同职责的模块。文中第五节起的接口名、环境变量名与代码骨架是当时的研究性示意，不是当前代码。

以下第一至第九节保留原始测量与当时的判断作为历史依据；其中“OAuth 免登录可直接复用”“没有 per-run 模型覆盖”的结论已被 0.6 的 API-key 接入与原生 app-server 实现取代，见上节。ZCode 侧的 inquiry 桥仍未实现。

结论先行：可以接入，且有两条独立的路。路径 A 不改任何代码，当天即可用 `command` 适配器或 `external` 适配器把 ZCode 任务跑起来，但映射粗、能力受限。路径 B 照 `DshAdapter` 的样子新增一个一等的 `zcode` 适配器，改动面小且每一处都有现成先例；真正需要新做的工作只有 inquiry 桥和 per-run model/effort 覆盖两块，首版可以诚实放弃。ZCode 已具备硬前提：一个可无头调用、单 JSON 输出、以 cwd 为界的 CLI（已实测跑通，见第三节）。

## 问题定义："如 dsh 般接入"指什么

dsh 在当前体系里是一个被适配器管理的执行器：任务由黑板经正常提交/认领流程调度，`dsh` 适配器在本机 spawn 无头 CLI 进程、持有其进程组句柄、强制 deadline、收集结构化结果并回报。这个方向是"黑板调度 ZCode"——ZCode 是 Worker 管理的子进程，黑板是权威调度者。

它与反向的"ZCode 使用黑板"不同：后者是 ZCode 会话作为调用方，通过 `buddy` CLI 或 `BoardClient` 提交任务、认领 `external` 任务并自己回报（[workers.md](../reference/workers.md) 有完整的可运行示例）。两个方向不冲突，都可以成立；本文第四节 A2 会顺带覆盖反向路径，主体讨论正向路径。

## 一、当前黑板的执行组织

### 进程拓扑与分层

```text
调用方(skill / CLI / dashboard / 外部 agent)
   │  命名 C-Two RPC(私有 token)
Python 黑板 daemon ── 唯一权威状态写入者
   │  SQLite WAL(board.sqlite3, schema 5)
   │  tasks/attempts/workers/messages/artifacts/events/commands/resource_claims
独立 supervisor 进程 ── C-Two ──> daemon
   └─ Worker 循环(独立进程,持有子进程句柄、deadline、本地回执)
        ├─ dsh 适配器    → node scripts/run.mjs → dsh --profile headless
        ├─ command 适配器 → 单个显式 argv 进程
        └─ external 适配器 → 无本地执行者,由调用方自己的 agent 经 BoardClient 认领
```

daemon 与 supervisor 是分离的 OS 进程。Worker 对象跑在 supervisor 进程里，持有适配器子进程句柄并在 daemon 不可用时继续强制 deadline；`external` 任务没有本地执行者，由调用方自己的 agent 直接经 RPC 参与。存储的 PID 只是诊断值，永远不是发信号的权限。

### 适配器契约

[adapters/base.py](../../src/buddy/adapters/base.py) 定义了执行器与黑板之间的全部界面，适配器拿不到数据库句柄、不写权威状态：

- `ExecutionContext`：一次 attempt 能知道的一切——taskId、attemptId、generation、规范化 spec（含 `cwd`、`task`、`timeoutSeconds`）、attempt 目录、运行时信息、环境变量。`task_file()` 约定任务文本写到 attempt 目录的 `task.txt`。
- `Adapter.prepare(context)`：spawn 前校验可用性，不满足抛 `ADAPTER_UNAVAILABLE`。
- `Adapter.start(context) -> ProcessHandle`：实际拉起子进程，必须 `start_new_session=True`（独立进程组）。
- `Adapter.collect(handle, context) -> AdapterOutcome`：子进程结束后产出结构化结果——`status`（ok/failed/cancelled）、`result`、`exit_code`、`signal`、`shutdown_confirmed`、`artifacts`。
- `ProcessHandle`：封装"只有创建者可发信号"。`terminate()` 先 SIGTERM 后在宽限期内升级 SIGKILL，作用于整个进程组；`shutdown_confirmed()` 只在观察到组内全部进程（含后代）消失时为真，绝不从 PID 缺失或租约过期推断死亡。

### Worker 循环与适配器无关性

[worker/worker.py](../../src/buddy/worker/worker.py) 的执行循环通过注册表 `adapter(name)` 取实现，本身完全不知道执行器是什么。它统一承担：claim 幂等（nonce 与 `claimRequestId` 在调用前先 fsync 落盘，`ReceiptSpool`，worker.py:47 起）；租约续期与每 2 秒的只读 cancel 意图观测（worker.py:617 起）；deadline 到点终止进程组（worker.py:511-522）；结果事务被服务确认前保留不可变完成回执，未确认回执在下次 supervisor 启动时重放。这意味着新执行器不需要重新实现任何调度、恢复或终止语义。

### 契约与校验面

[schemas.py](../../src/buddy/schemas.py) 是提交请求的唯一校验点：`ADAPTERS = ("dsh", "command", "external")`（schemas.py:38）是适配器白名单，`SUBMIT_FIELDS` 冻结了全部可提交字段（未知字段 `INVALID_ARGUMENT`），任务文本上限 `MAX_TASK_BYTES = 1 MiB`（schemas.py:22），超时 10–86400 秒、默认 1800。按适配器的条件校验有先例：`argv` 只对 `command` 合法（`_argv`，schemas.py:153）。`model`/`provider`/`effort` 是通用 spec 字段，由各适配器自行决定如何消费。

注意一个容易混淆的数值：runner.md 里"超过 32,000 字节改为文件投递"是 `run.mjs` 自己的内联/文件分界，不是黑板的任务大小上限；黑板接受到 1 MiB。

### 传输边界

C-Two 的字符串参数用 Python pickle 序列化，architecture.md 明说这是同用户 Python-only 的 API，非 Python 客户端不得依赖这些载荷、必须定义自己的契约。事实上 Node 侧也从不直连 C-Two：dsh 插件体系（workspace 桥、inquiry 桥）与服务的通信方向永远是 Python 服务作为 Node 桥的客户端，经文件与私有 socket 交换数据。ZCode 插件若将来参与，应遵循同一方向，不发明 Node 直连 C-Two 的路径。

## 二、dsh 的接入解剖

### 完整执行链

一次 `dsh` 任务从提交到回执经过这些环节（右侧是负责的组件）：

```text
buddy submit(requestId, adapter="dsh", cwd, task, timeoutSeconds, [model/provider/effort])
  → daemon 校验+规范化+指纹,任务入队 queued                    [schemas.py / store.py]
  → Worker 空闲,claim 成功,attempt 生成(generation/nonce)        [worker.py]
  → DshAdapter.prepare: 校验 node 与 runner 入口,写 task.txt(0600) [adapters/dsh.py]
  → DshAdapter.start: 生成 inquiry socket 凭据,
     spawn `node scripts/run.mjs --cwd --task-file --timeout
     --log-dir --inquiry-* [--model=… --provider=… --effort=…]`   [adapters/dsh.py]
  → run.mjs: 复制并 patch settings,
     spawn `dsh --profile headless --patch <副本> -- -- <prompt>`,
     收集 stdout/capture,打印恰好一个 JSON 对象                    [scripts/run.mjs]
  → 子进程结束,Worker 在 daemon 停机时也持续强制 deadline          [worker.py]
  → DshAdapter.collect: 解析 stdout 最后一个 JSON,
     按退出码∧status∧shutdownConfirmed 映射,发现 artifacts         [adapters/dsh.py]
  → worker_result: 结果+artifacts+状态+事件单事务提交               [store.py / service.py]
```

### 适配器实际承担的职责

`DshAdapter`（[adapters/dsh.py](../../src/buddy/adapters/dsh.py)）本体只做六件事：可用性探测（node 二进制与 runner 入口，支持 `BUDDY_NODE`/`BUDDY_RUNNER_PATH` 覆盖）；写任务文件；生成 inquiry 凭据（socket 路径、hex token、0600 落盘，绝不进公共任务视图）；组装 argv 并以新会话 spawn；从 stdout 解析最后一个 JSON 并做诚实映射（`exit 0 ∧ status=="ok" ∧ shutdownConfirmed` 三者同时成立才是 ok，cancel 未确认停机算 failed，解析不出算 `invalid-result`）；从 runner 自报的路径（capture 与两份 runner 日志）计算 SHA-256 后发现 artifacts。

### run.mjs 承担的职责

runner（[scripts/run.mjs](../../harnesses/dsh/scripts/run.mjs)，`DSH_PROFILE = 'headless'`，run.mjs:38；最终 argv 组装在 run.mjs:634）在适配器与 dsh 之间补齐了：settings 副本与 per-run model/provider/effort patch（原设置永不修改）；进程组所有权与 `shutdownConfirmed` 判定；stdout/capture 日志（0700/0600）；超过 32,000 字节任务的文件投递；`finalText`（dsh stdout 头部至多 6000 字符）与截断标志；workspace 分组与 inquiry 桥挂载；以及那句贯穿全仓的诚实提醒——退出 0 只表示 agent 跑完，不表示任务正确。

### 导出：对执行器的真实要求

把上面两层职责里不属于 Python 侧的部分剥掉，"如 dsh 般接入"对执行器的硬性要求只有四条：提供无头非交互的 CLI 调用方式；接受以 cwd 为工作边界、以文件或参数投递任务文本、有外部强制手段约束时长；结束时在 stdout 产出恰好一个可解析的 JSON 对象，含可映射的完成状态与最终文本；退出码与 JSON 字段的语义足够诚实，允许适配器组合判定而不单凭退出码。进程组所有权、终止升级、shutdown 证据、幂等回执、cancel 观测、lease 恢复全部由 Python 侧既有机制承担。可选增强有三项：inquiry 桥（观察/提问在跑的 agent）、workspace 分组、per-run 模型覆盖——dsh 三项都有，缺了不影响成为合法执行器，只影响能力宣称。

## 三、ZCode CLI 实证调查（2026-09-22）

### 发现与定位

本机没有 `zcode` PATH 命令。ZCode 桌面版（`/Applications/ZCode.app`，3.14.0）的 bundle 内带一个 Node CLI：`/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs`，`--version` 报 `zcode 0.16.9`，需要系统 Node 运行（实测用 `~/.nvm/versions/node/v24.21.0/bin/node`）。CLI 与桌面版共享 `~/.zcode` 状态目录与登录凭据——实测在已登录桌面版的机器上无头调用直接可用，无需再次认证。没有 PATH shim 这一点是接入时要处理的现实问题（环境覆盖变量或安装包装脚本）。

### 无头模式能力

`zcode.cjs --help` 确认的相关旗标：`-p/--prompt <text>` 单次运行不开 TUI；`--cwd <path>` 指定工作目录；`--mode build|edit|plan|yolo` 权限模式，`--prompt` 默认即 `yolo`（全自动，适合无头）；`--json` 机器可读输出；`--attach <path>` 附加本地文件给 prompt（可重复，是任务文本文件投递的现成通道）；`--resume <sessionId>` 按 sessionId 续跑持久化会话；`--continue` 续跑当前目录最新会话；`--target` 会话目标；`--disallowed-tools` 按次移除工具；`--surface terminal|desktop` 无头呈现面。注意两点：CLI 没有 `--model` 旗标（模型选择是 TUI 内 `/model` 斜杠命令与持久设置），也没有 `--timeout` 旗标（时长约束完全依赖外部的进程组终止）。

### 实测运行与输出信封

在空临时目录实测 `node zcode.cjs --cwd <dir> --mode yolo --json -p "Reply with exactly the single word: pong."`：退出码 0，stdout **恰好一个 JSON 文档**（对整体 stdout 直接 `json.load` 成功，不是 JSONL 流），信封结构：

```json
{
  "sessionId": "sess_…", "traceId": "…", "turnId": "turn_…",
  "response": "pong",
  "usage": {"source": "provider", "modelRequestCount": 1, "inputTokens": 35452,
             "outputTokens": 31, "cacheReadTokens": 31808, "totalTokens": 35483,
             "reasoningTokens": 0, "webFetchRequests": 0, "webSearchRequests": 0},
  "eventCount": 40,
  "projection": {"status": "idle", "turnCount": 1, "totalTokenCount": 35483,
                 "contextUsed": 35483, "contextWindow": 200000}
}
```

对适配器而言：`response` 即 finalText；`sessionId` 可进回执支撑后续 `--resume` 续跑（这是 dsh 路径没有的原生可恢复性）；`projection.status` 是回合级状态信号；`usage` 提供真实 token 计量。实测两次运行各消耗约 35k input tokens（其中约 31.8k 命中缓存读），说明无头模式的固定开销不小，短任务批量调度时要计入成本。失败路径的退出码行为未实测，适配器设计不得假设"退出 0 即成功"（见第七节）。

### 环境敏感性与凭据

用 `env -i` 清空环境后 CLI 启动失败，报"无法定位 CLI ZCode Built-in Provider Config"并依次探测 bundle 内路径与 `/config/provider/zcode-builtin.json`（相对根变量解析为空串所致）；实际配置文件在 `/Applications/ZCode.app/Contents/Resources/config/provider/zcode-builtin.json`。结论：适配器 spawn 时必须把 Worker 传入的 `context.environment` 原样带上，不得构造"干净"环境。凭据方面，无头调用复用桌面版 OAuth 登录态，实测免登录可用；但接入文档必须写明此前提（`zcode login`），availability 探测应把"未登录"作为可报告的不可用原因。

### 与 dsh 需求的逐项对照

| dsh 接入需要 | ZCode 现状 | 实测/判断 |
| --- | --- | --- |
| 无头单次执行 | `-p` + `--mode yolo`（headless 默认 yolo） | ✅ 实测跑通 |
| 以 cwd 为界 | `--cwd <path>` | ✅ |
| 单 JSON 输出 | `--json`，stdout 恰好一个 JSON 对象 | ✅ 实测验证 |
| 最终文本 | `response` 字段；另有 `sessionId`、`projection.status`、`usage` | ✅ |
| 任务文件投递 | `--attach <path>`（可重复）；`-p` 内联受 exec argv 总量限制 | ✅ 契约存在 |
| 超时强制 | CLI 无 `--timeout`；由 Worker deadline + 进程组终止兜底 | ✅ 可行（worker.py:511-522） |
| 停机证据 | 复用 `ProcessHandle.shutdown_confirmed()`（组级观测） | ✅ Python 侧既有 |
| per-run model/effort 覆盖 | 无 `--model` 旗标；靠持久设置/TUI | ❌ 首版放弃或另找机制 |
| inquiry 桥 | 无对应物；dsh 的桥是 Node 插件 + `--inquiry-socket` + JSONL 协议 | ❌ 需新做 ZCode 侧插件 |
| workspace 分组 | 无对应桥；ZCode 有自己的 session 体系（`--resume`） | ❌ 语义不同，首版不宣称 |

## 四、接入路径 A：零代码改动

### A1：command 适配器直接跑 zcode CLI

`command` 适配器（[adapters/command.py](../../src/buddy/adapters/command.py)）以 `shell=False` 运行恰好一个显式 argv 进程，同样获得进程组所有权、deadline、cancel、shutdown 证据、日志与 artifacts（stdout/stderr/task.txt 三个工件）。今天就可以提交：

```sh
"$BUDDY" submit '{"requestId":"zcode-smoke-1","adapter":"command",
  "cwd":"/abs/workdir","task":"Read task.txt in the cwd and do what it says.",
  "timeoutSeconds":1800,
  "argv":["node","/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs",
          "--cwd","/abs/workdir","--mode","yolo","--json","--attach","/abs/workdir/task.txt",
          "-p","Complete the attached task. Reply with a short summary."]}'
```

适配器会把 `task.txt` 写进 attempt 目录并通过 `BUDDY_TASK_FILE` 环境变量告知路径，但 zcode CLI 不会自动读它，所以示例里任务文本经 `--attach` 投递、`task` 字段承担双通道（黑板记录 + 附件）。限制要诚实认识：结果映射只有"退出 0 = 命令成功"（command 适配器的 note 明说这不等于任务正确）；zcode 的 JSON 信封落在 runner.stdout.log 里，需人工或后续脚本解析；任务文本经 argv 内联受 exec 参数总量约束（黑板允许 1 MiB 任务文本，argv 不是通用投递通道）；无 per-run 模型覆盖；无 inquiry。这适合冒烟、试用和低频手动调度。

### A2：external 适配器，ZCode 会话作为 caller-owned agent

反向方向同样零改动：ZCode 会话（或任何能跑 Python 的宿主）用 `BoardClient` 认领 `external` 任务、自己做、自己回报。[workers.md](../reference/workers.md) 给出完整可运行示例（注册 worker → 先 fsync 落盘 nonce/claimRequestId → claim → progress → submit_result 带 artifacts 与 shutdownConfirmed），仓库内 [buddy/examples/external_worker.py](../../src/buddy/examples/external_worker.py) 是同流程的打包版本，端到端行为有 `test_blackboard.py::TestExtensibility::test_an_external_agent_completes_a_real_task_through_the_public_api` 持续验证。调用方式是 `uv run --project deepseek-delegate python worker.py`，不触碰 SQLite、不读 daemon token。适合"ZCode 用黑板"：把长任务挂到黑板、跨会话恢复、让别的 worker 代跑。示例本身不含 supervisor 循环（租约续期、cancel 观测、deadline、回执重放），长驻 agent 要按 workers.md 的契约补齐。

### A 路径的定位

A1 证明链路通，A2 证明契约通；两者合起来覆盖了"试运行 ZCode 委派"的全部即时需求。它们的共同上限是：A1 的结果映射太粗（诚实但信息少），A2 依赖调用方自己实现完整的 worker 义务。把 ZCode 变成与 dsh 同等的一等执行器，是路径 B。

## 五、接入路径 B：一等 zcode 适配器（提案）

以下全部是提案性设计，未实现；名称与结构以现有先例为准，实现时可调整。

### 设计原则

完全复用既有执行语义，适配器只做"dsh 适配器对 run.mjs 做的事"。不需要为 ZCode 写一层 Node runner 包装：run.mjs 存在是因为 dsh 侧需要 settings 副本/patch、capture、workspace 分组与 inquiry 挂载等额外服务，而 zcode CLI 自己就吐最终 JSON，Python 适配器直接 spawn 即可。不宣称没有的能力：capabilities 不含 `inquiry` 与 `workspace`，`inquire` 对该适配器如实回答"本适配器无 inquiry 能力"（这是 workers.md 规定的既有行为，不是缺陷）。

### 文件级改动清单

| 位置 | 改动 | 先例 |
| --- | --- | --- |
| `python/buddy/adapters/zcode.py`（新建） | `ZcodeAdapter(Adapter)`：`available/prepare/start/collect/cancel` | `adapters/dsh.py` 逐段对应 |
| `python/buddy/adapters/__init__.py:12` | `BUILT_IN` 元组加 `ZcodeAdapter` | dsh/command 注册方式 |
| `python/buddy/schemas.py:38` | `ADAPTERS` 加 `"zcode"`；决定 `model/provider/effort` 对该适配器是拒绝还是忽略 | `_argv` 的按适配器条件校验（schemas.py:153） |
| `python/tests/test_blackboard.py` | 端到端用例：私有 `BUDDY_STATE_DIR`/`BUDDY_RUNTIME_ROOT`，`BUDDY_DEV_SOURCE=1`，伪 CLI 或受控真 CLI | 既有 ext-external 端到端测试 |
| `deepseek-delegate/references/workers.md` 等 owning 文档 + 两份 SKILL.md + 双 README | 适配器表、能力、示例同步（AGENTS.md 路由表规则） | 既有更新惯例 |

### 适配器骨架示意

```python
class ZcodeAdapter(Adapter):
    name = "zcode"
    capabilities = ("zcode", "cancel", "artifacts", "deadline", "task-text")

    def available(self):
        # 探测顺序:BUDDY_ZCODE_CLI 显式路径 → 已知 bundle 布局 → PATH 上的 zcode shim
        # 不可用原因要可报告: 无 node / 无 CLI 入口 / 未登录
    def prepare(self, context):
        # 写 task.txt(0600);长任务经 --attach 投递,-p 只放短指令
    def start(self, context):
        # spawn [node, cli, "--cwd", spec["cwd"], "--mode", "yolo", "--json",
        #        "--attach", task_file, "-p", 短指令]
        # start_new_session=True,环境=context.environment 原样传递,stdin=DEVNULL
        # handle.deadline = monotonic() + timeout_seconds
    def collect(self, handle, context):
        # 整体解析 stdout 为一个 JSON(实测形态);解析失败 → invalid-result(同 dsh)
        # ok 判定 = exit 0 ∧ JSON 可解析 ∧ handle.shutdown_confirmed() ∧ projection.status=="idle"
        # response → finalText;sessionId/usage 原样进 result;artifacts = runner 两日志 + task.txt
    def cancel(self, handle, *, grace_seconds=3.0):
        handle.terminate(grace_seconds=grace_seconds)   # 进程组 SIGTERM→SIGKILL
```

`available()` 建议支持 `BUDDY_ZCODE_CLI` 环境覆盖（模式同 `BUDDY_NODE`/`BUDDY_RUNNER_PATH`），因为 zcode CLI 目前无 PATH 命令且位置随桌面版 bundle 走。CLI 位置与输出信封都随 app 版本漂移，适配器必须防御式解析（未知字段忽略、必需字段缺失算 `invalid-result`），并把实测的 CLI 版本记进 result 供追溯。

### 结果映射（诚实边界）

成功路径实测退出 0，但退出码在失败/中断下的行为未验证，且"回合完成"不等于"任务正确"（run.mjs 的 note 对 dsh 也反复强调这一点）。因此映射规则与 dsh 相同的三重合取：`exit 0 ∧ JSON 可解析 ∧ shutdownConfirmed` 才是 ok，并额外用 `projection.status` 作回合级信号；cancel 被确认停机才是 cancelled，否则 failed；stdout 解析不出 JSON 一律 `invalid-result`。`response` 只是最终文本，不是验收——验收仍走黑板既有的"核查真实工件与任务结果后才 acknowledge"流程（AGENTS.md 不变量）。

### capabilities 与契约面决定

建议 capabilities 为 `("zcode", "cancel", "artifacts", "deadline", "task-text")`。`model/provider/effort` 三个通用 spec 字段对 zcode 首版建议**显式拒绝**（`INVALID_ARGUMENT`，报"zcode 适配器不支持 per-run 模型覆盖"）而不是静默忽略——静默忽略会让指纹不同的提交看起来等价，与仓库"未知即拒绝"的校验哲学一致。`argv` 维持仅 `command` 合法。`workspace` 布尔字段对 zcode 无意义，建议提交时带 `workspace: false` 或在 canonical 化时同样拒绝 true 值（实现时二选一，写进 cli.md）。

### 测试与文档要求

按 AGENTS.md：从完整 checkout 跑 `uv run --project deepseek-delegate --frozen python -m buddy.checks`；聚焦测试用私有 `BUDDY_STATE_DIR`/`BUDDY_RUNTIME_ROOT` 与 `BUDDY_DEV_SOURCE=1`。端到端测试建议两层：伪 CLI（一个打印固定 JSON 的小脚本，验证映射/超时/取消/解析失败各分支，不需要凭据）与一条可选的受控真调用冒烟（复用 A1 的 pong 任务，凭据可用时才跑）。公共行为变化后按路由表同步 workers.md（适配器表加行）、cli.md（错误码与字段）、usage.md（示例）与两个 SKILL.md、双 README。

## 六、无对应物的能力

### inquiry 桥

现有 inquiry 链路是：`DshAdapter` 为每次 attempt 生成 socket/token/results 三件套凭据，run.mjs 把桥挂进 dsh 插件体系，Python 服务作为桥的客户端导入有界 JSONL 日志，`buddy inquire` 由此观察在跑的 run 或向其活体 agent 提相关问题（回答只能经该 run 自己的关联回复工具，assistant 散文永远不算答案）。ZCode 没有这层。若要补齐，需要写一个 ZCode 侧插件/MCP 或 hook 对接同一 JSONL 结果协议（token 认证、socket 私有），服务端导入逻辑可基本复用。这是路径 B 中最大的一块新工作，首版不做时 `inquire` 如实报无能力，黑板的其他一切不受影响。

### workspace 分组

dsh 的 workspace 桥把 run 归组进宿主插件的会话视图。ZCode 的对应概念是自己的 session 体系（`sessionId`、`--resume`），与"宿主 workspace 分组"语义不同。首版适配器不宣称 `workspace` 能力，结果里保留 `sessionId` 即可——需要续跑的人拿它直接 `zcode --resume`。这反而是比 dsh 更强的原语：dsh 的 attach 模式只能归组已完成的会话，ZCode 的 resume 是真续跑。

### per-run model/effort 覆盖

run.mjs 通过"复制 settings → 只写 `agent-default-model` 三字段 → 临时 `--patch`"实现 per-run 覆盖且永不改共享设置。ZCode CLI 无对应旗标；可能的机制（专用设置文件、环境变量、`--resume` 进已配置会话）都未验证。首版按第五节建议显式拒绝这三个字段；将来若 ZCode 提供等价机制，再按 run.mjs 的"副本+patch、不动原件"原则接入。

### C-Two 直连禁止

再强调一次边界：ZCode 插件将来参与黑板时不得直连 C-Two RPC（pickle、同用户 Python-only）。正确方向与 dsh 插件相同——Python 服务做客户端，ZCode 侧只暴露文件/socket 级桥；或反向经 `BoardClient`/`buddy` CLI 走公共契约。

## 七、陷阱与边界

- **退出码语义**：实测只覆盖成功路径。适配器不得单凭退出 0 判 ok；zcode"回合完成"与"任务正确"是两件事，验收永远走真实工件核查。
- **环境继承**：`env -i` 会让 CLI 找不到 provider 配置（相对根变量解析为空）。spawn 必须原样传递 `context.environment`。
- **任务投递**：黑板允许 1 MiB 任务文本；`-p` 内联受 exec argv 总量限制，不是通用通道。`--attach task.txt` 是通用投递，`-p` 只放一句短指令。
- **无 CLI 超时**：zcode 无 `--timeout`，完全依赖 Worker deadline + 进程组 SIGTERM/SIGKILL。这已被 worker.py 兜底，但要在 workers.md 写明"zcode 任务的时限由 worker 强制"。
- **无 PATH 命令**：CLI 在桌面版 bundle 内，随 app 更新移动。`BUDDY_ZCODE_CLI` 覆盖 + `available()` 探测 + 防御式解析，三者都要。
- **登录前提**：无头调用复用 `~/.zcode` OAuth 登录态。`available()` 应把未登录报告为不可用原因，文档写明 `zcode login` 前提。
- **固定开销**：无头模式实测每次约 35k input tokens（约 31.8k 缓存读）。短任务高频调度要把这笔开销计入，评估时不能按 output tokens 估算成本。
- **并发**：一个 worker 一次只跑一个 attempt（`WORKER_BUSY`），`BUDDY_MAX_CONCURRENT` 默认 1。多 ZCode 并发需要多 supervisor（`worker-start`），不是适配器的职责。
- **POSIX 边界**：整个体系 POSIX-only，zcode CLI 的 macOS 实测不自动推广到 Linux。

## 八、验收要求草案（若未来实现）

| 验收场景 | 要求 |
| --- | --- |
| 伪 CLI 成功/失败/超时/取消/输出不可解析 | 五种映射各就各位：ok/failed/超时failed/cancelled(需确认停机)/invalid-result |
| cancel 意图到达 | 进程组在宽限期内终止，shutdown 证据真实，未确认停机不得报 cancelled |
| daemon 停机期间 | worker 继续强制 deadline 并保留回执，恢复后 `reconcile` 重挂同一 attempt |
| 环境传递 | 清净环境构造（如 env -i）不得出现在 spawn 路径；context.environment 原样传递 |
| CLI 缺失/未登录 | 提交或认领阶段得到可读的 `ADAPTER_UNAVAILABLE` 原因，不是晦涩 spawn 失败 |
| 长任务文本（>argv 限量） | 经 `--attach` 文件投递成功，黑板内 task 全文保留 |
| 重复提交 | 相同 requestId+指纹恢复既有任务，不产生第二个 attempt |
| 结果信封漂移（CLI 升级） | 未知字段忽略、必需字段缺失判 invalid-result，不崩溃、不误判 ok |
| capabilities 诚实 | `buddy capabilities` 如实报告 zcode 可用性与原因；inquire 报无能力 |
| 验收闭环 | pong 型端到端任务 + 真实工件任务各一条，acknowledge 前核查实际产物 |

## 九、实测证据记录（2026-09-22）

环境：macOS（darwin 25.6.0 arm64），ZCode 桌面版 3.14.0（`/Applications/ZCode.app`），CLI `zcode 0.16.9`（`/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs`），Node v24.21.0（`~/.nvm/versions/node/v24.21.0/bin/node`），黑板源码为 `socu/python-blackboard` 分支（HEAD 6442456）。

| 编号 | 操作 | 结果 |
| --- | --- | --- |
| E1 | `zcode.cjs --help`（正常环境） | 输出完整帮助；确认 `-p/--cwd/--mode/--json/--attach/--resume/--target/--surface` 等旗标；无 `--model`、无 `--timeout` |
| E2 | `zcode.cjs --cwd <tmp> --mode yolo --json -p "…pong…"`（`env -i` 清空环境） | 失败："无法定位 CLI ZCode Built-in Provider Config"，探测路径含空根 `/config/provider/zcode-builtin.json` |
| E3 | 同 E2 命令、正常环境 | 成功；退出 0；stdout 恰好一个 JSON 对象（整体 `json.load` 通过）；`response=="pong"`；`usage.inputTokens≈35k`（cacheRead≈31.8k）；`projection.status=="idle"`；`contextWindow==200000` |
| E4 | E3 重复一次并全量落盘 | 同构信封（sessionId/traceId/turnId/response/usage/eventCount/projection），证实输出形态稳定 |

E2–E4 的原始命令与完整输出保存在本地 `tmp/` 之外的分析会话中（样本 JSON 曾存 `/tmp/zcode-headless-sample.json`）；按仓库惯例，若本分析进入实现阶段，原始证据应归档到 `docs/acceptance/` 并把原始文件留在被忽略的 `tmp/` 目录。本文引用的源码事实（适配器契约、Worker 循环、schema 白名单、run.mjs 行为）均直接读自上述分支源码，关键行号：adapters/base.py 全文、adapters/dsh.py 全文、adapters/__init__.py:12、schemas.py:19-46/153-159/185-213、worker/worker.py:47-108/400/511-522/596-660、run.mjs:38/634。
