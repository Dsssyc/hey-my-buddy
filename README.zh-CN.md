<p align="right"><a href="README.md">English</a> · <strong>简体中文</strong></p>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/banner-dark.svg">
  <img alt="hey-my-buddy" src="docs/assets/banner-light.svg" width="100%">
</picture>

<p align="center"><b>工作委派出去，责任留在自己手里。</b></p>

<p align="center">
  让你正在用的编码 agent（Host）把边界明确的任务委派给本机的其他编码 agent，并在验收之前核对每一份封存的结果。<br>
  微任务、决定与结果都记在本地的黑板上，私有的控制台显示谁在做什么。
</p>

<p align="center">
  <a href="#如何协作">如何协作</a> ·
  <a href="#安装">安装</a> ·
  <a href="#控制台路由与模型">控制台</a> ·
  <a href="#当前状态与限制">当前状态与限制</a> ·
  <a href="#文档与开发">文档</a>
</p>

---

Host buddy 拥有微任务并负责验收，Worker buddy 在本地的编码 harness 里执行其中边界明确的部分。两者都是地位对等的 buddy，只通过共享黑板协作，区别在于角色。Host 可以亲自实现一部分，再把其他部分交给能力、成本或模型更适合的 Worker。目前接入的编码 harness 是 DeepSeek Harness（`dsh`）、ZCode（`zcode`）以及实验性的 Codex App Server（`codex`）；Claude Code harness（`claude`，[参考文档](docs/reference/claude-code.md)）作为 P1 harness 加入，已授权的实机探针验证了原生结果、取消和有界沙箱路径，随后又通过已安装服务完成了单独获准的只读微任务链路验收。

Worker 用你自己的 harness 登录，用量计入你自己的套餐和密钥。[账户与条款](docs/terms.zh-CN.md)说明 hey-my-buddy 怎样对待你的账户，以及哪些条款仍然适用于你。

编码任务有明确的 Git 工作区和固定输入、产物快照。黑板是本地的 SQLite 数据库，私有的 React/Vite 控制台可以查看任务、路由、共享 buddy、用户偏好和模型并发设置。

## 如何协作

适合委派的工作包括范围明确的实现、测试、可复现问题调查、文档整理，以及结果可检查的文件转换。改动很小、答案已知或需求尚未明确时，由 Host 直接处理。

1. Host 确定微任务、buddy 和执行工作区。完整的 harness/provider/model/effort 组合经过校验后直接派发；省略全部四个字段时，Router 按共享路由模式与用户偏好从有界候选中选择。并行写入使用独立 Git worktree，整合后的结果由明确指定的整合者负责。
2. Worker 使用自己的工具和内部 subagent 完成工作。需要协助时，它以结构化结果结束当前回合；Host 可以批准明确的辅助任务、说明理由后拒绝、补充新的接续输入，或在路由边界给出完整 buddy。控制权由私有 control 凭据约束，Host 名称本身不是权限。
3. Host 检查真实 diff、运行相关验证，然后验收固定产物，并给出整合的目标（或明确的无需整合决定）：一次 `accept` 核对整合、记录验收并回收受管检出。Host 不验收的微任务用 `conclude` 结束，之后不能再继续。执行、辅助任务完成、整合与验收是彼此独立的事实。

## 安装

hey-my-buddy 是一个共享 Agent Skill `hey-my-buddy`，自带命令行，并且每个状态目录只有一个本地服务。已核验的日常安装是 0.48.0（contract 0.48.0、schema 26），安装于 2026-10-20（[记录](docs/acceptance/installs/0.48.0.md)）。各角色共用一套权限档位，DSH 与 ZCode 也能担任审阅模式的 Router（[验收记录](docs/acceptance/adr023/shared-levels.md)）；真实看板上还没有跑过审阅模式的路由。0.47.0 为每次执行记下会话，在微任务详情的“会话”标签里查看（[验收记录](docs/acceptance/adr024/session-view.md)）；装上之后已用四个 harness 的真实回合核对过这些记录。它也让控制台更好读：单击就打开一步，buddy 的颜色避开有含义的颜色，时间轴在刻度上用手势缩放（[验收记录](docs/acceptance/adr029/console-frontend.md)）。0.46.0 不再读取额度读数，改为冷却：因额度拒绝工作的提供方暂停参与路由一段时间，模型开始之前就被拒的微任务改派给别的 buddy（[验收记录](docs/acceptance/adr021/quota-cooling.md)）。Host 只经微任务委派，`execution-submit` 这个方法已经删掉。它让 Host 能在 Codex 与 Claude Code 正在跑的回合里提问，Host 等待过微任务之后也一样；它用 C-Two 0.7.6，不再加载 NumPy 与 FastDB（[验收记录](docs/acceptance/live-channel-0.43.0.md)）。控制台各页都已重做，宏任务的时间轴也在内，可以在中文与英文之间切换（[验收记录](docs/acceptance/adr029/console-frontend.md)）。在 Linux 上，状态目录的上层目录组可写而这个组是用户自己的私有组时，它信任这个目录（[验收记录](docs/acceptance/linux-portability/linux-portability.md)）。0.42.0 把看板从 schema 23 升级到 24：每条命令只留下标识这次提交的内容，执行心跳这类周期性状态只留最新的一条。skill 位于 `~/.agents/skills/hey-my-buddy`，Claude Code 通过 `~/.claude/skills/hey-my-buddy` 链接读取同一目录，旧的 Codex 插件已不再使用。源码与日常安装是相互独立的事实。[原生沙盒审阅验收](docs/acceptance/native-sandbox-review/0.27.0.md)记录由固定原生探针拥有、不再解读模型输出的验证。[路由与验证验收](docs/acceptance/adr019/routing-validation-0.25.0.md)记录审阅诊断留存与有界的额度恢复。它实现 ADR-018 的 Host 工作流：文件／标准输入任务包、按方法帮助、同一宏任务内委派、Host 直接收尾、失败结论与清理、部分成果、更换 buddy 续做、累计补丁和原生用量观察。[Host 工作流验收](docs/acceptance/adr018/host-workflow-0.21.0.md)记录验证结果与未验证范围。它尚未发布。改了中间号的安装在切换之前先做校验过的备份（见[运行参考](docs/reference/operations.md#installation)）。

在开发者确定发布渠道与确切版本之后，安装入口是一条固定版本的包命令：

```sh
# 发布渠道与版本确定之后使用；目前不可用
uvx hey-my-buddy@0.48.0 install
```

包名是否可用、是否发布到 PyPI 仍待开发者决定，本候选尚未发布到 PyPI。在正式发布之前，可以从冻结源码构建 wheel，并用绝对路径安装：

```sh
uv build --wheel --out-dir dist
uvx --from /absolute/path/hey_my_buddy-0.48.0-py3-none-any.whl hey-my-buddy install
```

机器上没有 `uv` 时，发布包里的 `install.sh`（macOS、Linux）或 `install.ps1`（Windows）会先准备固定版本的私有 `uv`，再调用同一个安装入口；它们不修改 `PATH`、shell 或 Host 设置，也不会安装全局 `hey-my-buddy` 命令。安装会先列出将写入的路径、物化版本化运行时。只改末位的版本不打断在跑的工作：服务换到新运行时上重启，接回在跑的执行。改了中间号的版本，有排队或在跑的工作、未确认的停止时拒绝并列出阻塞项（`UPGRADE_NOT_IDLE`），空闲后切换，并做校验过的备份。两种都是 skill、启动器、运行时与服务整体切换，失败时回滚。重跑完整同版本只补齐缺失内容、不重启服务；内容损坏时用同一条固定版本命令修复；同版本内容变化会报告 `updated`。sdist 保留来源提交，解包后构建 wheel 仍保留相同 `sourceCommit`。安装或升级日常服务都需要用户的单独授权。代码与脚本可移植到 Windows，但尚未在真机验证。

### 可直接交给安装 agent 的提示

```text
请在本机为我安装 hey-my-buddy <版本>。

1. 使用我指定的发布渠道和确切版本，或我提供的 wheel。只对缺失的发布信息询问，不要假设已经发布到 PyPI。
2. 通过 Host 支持的放行方式，在 agent 或 Host 沙盒之外运行安装命令，不要 sudo：`uvx hey-my-buddy@<版本> install`；未发布的构建用 `uvx --from <wheel 绝对路径> hey-my-buddy install`。没有 uv 时改用发布包中对应的 install.sh 或 install.ps1。
3. 在写入任何内容之前，列出它将创建或替换的全部路径（skill 位于 ~/.agents/skills/hey-my-buddy，Claude Code 链接 ~/.claude/skills/hey-my-buddy，数据与运行时位于 ~/.local/share/hey-my-buddy，bootstrap 脚本可能另建私有 uv；Windows 使用输出中的 %LOCALAPPDATA% 数据目录）。
4. 汇报安装结果：skill 版本与放置结果、Claude Code 链接状态、服务动作，以及是否已生成备份。
5. 用已安装启动器核验，并以它实际报告的路径与版本为准：`~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy health`、`... runtime`、`... harnesses`。新 Host 会话会加载 skill；当前会话可在沙盒外直接使用启动器绝对路径。
6. 如果返回 LAUNCH_ACCESS_DENIED，就把这一条启动器命令放行到沙盒外后重试，不要全局关闭沙盒。如果服务忙碌会返回 UPGRADE_NOT_IDLE 并列出任务：如实汇报并等待，绝不为强行升级而取消工作。
```

先提交一个有界微任务：

```sh
HMB_CLI="$HOME/.agents/skills/hey-my-buddy/scripts/hey-my-buddy"
"$HMB_CLI" submit '{"requestId":"doc-links-1","hostId":"codex","task":"检查本仓库 README 中的相对链接，只新增 buddy-doc-review.md，并列出失效链接和修复建议。","cwd":"/abs/repo","expectedSeconds":3600,"executionWorkspace":{"kind":"worktree","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["buddy-doc-review.md"]}}'
"$HMB_CLI" get '{"runId":"<returned-runId>"}'
"$HMB_CLI" await '{"runId":"<returned-runId>"}'
```

示例把 buddy 选择交给路由；`executionWorkspace` 是工作区所有权和产物校验契约。来源目录与整合者由顶层工作目录和提交的 Host 派生。`submit` 会返回一个私有 `controlFile`，后续 Host 命令必须提供它。确切的安装步骤、稳定错误码、harness 发现命令与恢复流程见[运维文档](docs/reference/operations.md#installation)。

## 控制台、路由与模型

打开私有本地控制台：

```sh
"$HMB_CLI" console
```

控制台默认免登录，通过默认浏览器打开固定回环地址。可在设置中开启“需要登录”，使用有效期 10 分钟的一次性入口；会话不按 30 天过期，可显式退出或撤销。每个获准访问的窗口都可以编辑设置；后保存的内容生效，草稿按窗口保留。使用 `console '{"browser":false}'` 可自行打开链接；使用 `console '{"wait":true}'` 可让 CLI 留在前台，Ctrl-C 只关闭对应控制台。会话有效期、安全边界与独立的安装边界见[控制台入口说明](docs/reference/console.md)。

打开命令返回的 loopback 地址，顶部提供“任务 / Buddy / 设置”三个页签。任务页按项目列出宏任务，独立微任务在最后一组。宏任务打开是它的微任务的时间轴，长的空闲压成固定宽度的断口；单击选中一个微任务，双击在原处打开，按回合组织，它的各步挂在名字下面。Buddy 按 harness 归组模型服务，每种推理强度标识一个独立的 buddy；模型服务一行显示已启用的推理强度和并发占用；每个 buddy 分别编辑启用状态。“角色”分区显示有序 Router 列表与共享模式。页面直接编辑，没有全局编辑开关：改动控件即产生草稿，底部保存栏只提供放弃与保存；结果未确认时保留恢复信息。编辑只涉及你自己的 buddy 偏好与启用状态、模型服务的备注与并发设置和 Router 列表：目录事实始终只读。每个模型服务还有用户拥有的并发上限，在下一次准入时生效，调低上限不会停止正在运行的任务。同一页还显示记录的 harness 状态，提供显式“检测”和自动检测失败时的高级手动路径；检测不调用模型。“设置”页放主题、语言（中文或英文）、提醒、访问、版本，以及本地存储的检查与回收。查看、刷新和编辑草稿都不调用模型。

路由由 Python 冻结合法候选并检查答案边界；不健康的 harness 会从候选中排除。路由分两种模式：快速路由只做一次调用、由 harness 真正关闭全部工具，输入只有任务、候选的事实、偏好与备注；审阅路由是只读模式，另会读取冻结的仓库副本。快速路由已为 DSH、ZCode 与 Codex 实现：DSH 已通过授权实测，ZCode/Codex 已通过原生离线零工具检查；ZCode 的提供方验收被 429 限流阻断，Codex 快速路由尚未做付费实测。0.19.0 源码已在 macOS 的 Codex CLI 0.157.0 上验证 `openai / gpt-6-sol / high` 的只读 Router，推荐 `standard` 预算。Linux/Windows 与其他 harness 的审阅路由尚未验证；指定的 Router 还必须可用且已启用。共享模式适用于有序 Router buddy 列表；路由使用其中第一个可用且已启用的 buddy。没有可用项时，微任务在路由边界交回 Host。请注意：快速 Router 调用会把任务描述发送给该 Router 所在的模型提供方（包括准备交给其他模型执行的任务），审阅路由还会读取冻结的仓库副本。安装后由用户在 Buddy 中选择 buddy，本候选不修改用户设置。见[路由契约](docs/reference/decision.md)、[harness 发现](docs/reference/harnesses.md)和[验收证据](docs/acceptance/adr018/routing-modes-0.20.0.md)。

## 执行与恢复

Codex Host 为每个运行中的微任务派生一个只做监控的原生子代理，显式选择当前 Codex 可用、能执行命令的最便宜模型，并使用最低的推理强度。父代理继续独立工作，并负责全部决策与验收；Claude Code 保持后台 Bash 等待。权限、等待上限与前台回退见 [Codex 等待指引](docs/reference/host-codex.md)。

独立任务默认在一个机器级并发总上限下并行运行（默认 8，可配置 1–32），路由与执行共享该上限；在此之上，每个模型服务（一组确定的 harness/provider/model）还有用户设置的上限（默认 2），各推理强度与使用该模型的路由决策共享同一个计数。服务在这些上限内准入工作，为每次执行起一个执行进程；实际安装可配置不同的总上限，请以 `health.capacity` 为准。工作区重叠和独占资源仍会让冲突任务排队；并行修改代码需要使用不同 worktree。可在[容量配置](docs/reference/operations.md#private-state-and-environment)中调整上限，并用 `health.capacity` 查看总量与各模型占用。

等待超时、关闭终端或连接中断都不会取消任务。可以用同一个 `runId` 通过 `await`、`get` 或 `status` 接回；可用显式 `cancel` 停止微任务，并且只有拿到真实停止证据才报告已关闭。每次接续都会创建新的 attempt。DSH 与 ZCode 仅在微任务、checkout 和 buddy 绑定都匹配时恢复已有证据确认的原生会话，Codex 也只恢复其精确绑定的原生线程；没有已确认的会话或 buddy 发生变化时，harness 会重建新的根会话。每次执行的执行进程挺过服务的重启：它照常做完手头的回合，把结果交给新的服务；已落盘的不变完成收据只重放、不重新执行。结果会记录真实终止原因（正常完成、有序收尾或停止及由谁发起、Router 预算用尽、harness 或传输故障、被中断），不会把一种显示成另一种。断电、重启或执行进程崩溃之后，每次执行记下的身份（这一次开机、进程与它的启动时刻、进程组或 Windows 作业对象）会把它结算为被中断，不需要任何操作：要么证明进程都已不在，要么由服务按核实过的身份停下剩下的。之后微任务等 Host 决定，Host 可以从未经验证的部分产物接着做。有界活动投影展示当前阶段、最后原生活动时间和诚实计数；空日志、缺失 PID 或静态会话列表都不能证明进程已停止。请区分请求的、已解析的和实际观测到的模型身份。

`expectedSeconds`（默认 1800 秒）是 Host 该回头看一眼的时刻，不是期限：到点不停止任何东西，也没有“不设预期”的写法。只有 Router 调用保留硬预算，取自 Router 设置。Host 仍可主动取消，单次 `await` 等待结束也不会停止任务。选择该配置前可查阅[执行期限与等待窗口](docs/reference/cli.md#defaults-and-bounds)。

服务和执行进程从上述稳定私有运行时执行。日常命令直接执行 active 运行时自己的 Python，不经过 `uv`；`uv` 只用于安装与升级。黑板位于 `~/.local/share/hey-my-buddy/state`；升级在校验过的备份之后就地转换它。工作目录和 Git worktree 都不是操作系统沙箱。

## 当前状态与限制

日常安装是 [0.48.0/contract 0.48.0/schema 26](docs/acceptance/installs/0.48.0.md)。它改了契约与存储形式，所以在空闲的看板上、做过校验的备份之后安装，并把看板从 schema 25 升级到 26。

日常运行时支持 macOS 与 Linux、本地单用户 SQLite 状态；Windows 的代码与脚本可移植，但未在真机验证。ZCode 支持活动观察和协作式询问：问题等待根任务的下一个工具检查点或结束尝试，无法打断正在运行的工具，也不会开启新回合。原生权限请求和需要长时间等待的 Host 决策仍通过 attention/assistance 边界处理。Codex 与 Claude Code 把 Host 的问题送进正在跑的回合：模型在下一次决策时读到，用它的问询工具作答；问题不开启也不延长回合，回合结束之后才到的问题会被拒绝。Codex 使用实验性的 App Server。0.19.0 源码在 macOS 的 Codex CLI 0.157.0 上验证了 `openai / gpt-6-sol / high` 的只读 Router，推荐 `standard` 预算；Linux/Windows 与其他 harness 仍未验证。安装不会修改用户的 Router 设置。Claude P1 读取用户原生的提供方、账户与设置，每次接续都重建会话。其[参考文档](docs/reference/claude-code.md)记录已验证的原生路径、日常安装的只读微任务链路、模拟回归覆盖和其余限制。目前不提供货币预算、自动社区评价或原生 App 回合结束后唤醒。后台回访需要用户明确要求。

## 文档与开发

从[文档索引](docs/README.md)进入命令、架构、harness 和设计历史；[运维文档](docs/reference/operations.md)拥有安装、错误码与恢复，[harness 发现](docs/reference/harnesses.md)拥有发现与健康，[控制台](docs/reference/console.md)与[评价](docs/reference/evaluation.md)分别拥有控制台界面和评价表。[AGENTS.md](AGENTS.md)说明仓库开发约束；日常使用不需要 npm 或自行构建前端。问题可提交至 [GitHub Issues](https://github.com/Dsssyc/hey-my-buddy/issues)。

## 许可证

[MIT](LICENSE)。

仓库验证使用 uv 与受支持的 Node 版本（见 `apps/console/package.json`）。先执行 `npm --prefix apps/console ci` 准备控制台测试依赖，再执行 `uv run --frozen python -m hey_my_buddy.cli.checks`；完整 Python 检查包含由真实前端解析器读取合成预览数据的回归测试。前端测试与浏览器检查单独运行，见 [AGENTS.md](AGENTS.md)。

B 批源码加入原生计费标注和额度耗尽的候选过滤。取消显示记录中的发起者与理由，控制台说明已精简。[源码与原生检查记录](docs/acceptance/worker-accounts-phase1-0.23.0.md)与日常安装分别记录。

改了中间号的安装会备份看板，只为它自己的回滚；执行证据、受管检出、凭据与原生私有目录都不进入这份备份。详情见[操作文档](docs/reference/operations.md#backup-upgrade-and-storage-contract)。
