<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg"><img src="docs/assets/logo-light.svg" alt="hey-my-buddy logo" width="88"></picture></p>

# hey-my-buddy

[English](README.md)

hey-my-buddy 让 Host buddy（拥有目标的 agent）把边界明确的工作交给运行在本地编码 harness 中的 Worker buddy，同时保留对整体目标的责任。两者都是地位对等的 buddy，只通过共享黑板协作，区别在于角色。Host 可以亲自实现一部分，再把其他部分交给能力、成本或模型更适合的 Worker。目前接入的编码 harness 是 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（`dsh`）、ZCode（`zcode`）以及实验性的 Codex App Server（`codex`）；Claude Code 适配器（`claude`，[参考文档](docs/reference/claude.md)）作为 P1 适配器加入，默认采用用户确认的隔离设置；已授权的实机探针验证了原生结果、取消和有界沙箱路径，随后又通过已安装服务完成了单独获准的只读委派链路验收。

目标、决定与结果保存在本地 SQLite 黑板中。编码任务有明确的 Git 工作区和固定输入、产物快照；私有 React/Vite 控制台可以查看任务、路由、共享模型配置、用户偏好、模型并发设置和评价卡片。

## 如何协作

适合委派的工作包括范围明确的实现、测试、可复现问题调查、文档整理，以及结果可检查的文件转换。改动很小、答案已知或需求尚未明确时，由 Host 直接处理。

1. Host 确定目标、模型参数和执行工作区。完整的 adapter/provider/model/effort 组合经过校验后直接派发；信息不完整时通过生效模式的路由模型（快速或审阅）和有界评价表完成路由，最多八条任务级软路由偏好只影响该目标，不改变共享设置。并行写入使用独立 Git worktree，整合后的结果由明确指定的整合者负责。
2. Worker 使用自己的工具和内部 subagent 完成工作。需要协助时，它以结构化结果结束当前回合；Host 可以批准明确的辅助任务、说明理由后拒绝、补充新的接续输入，或在路由边界给出完整配置。控制权由私有 control 凭据约束，Host 名称本身不是权限。
3. Host 检查真实 diff、运行相关验证，把已验证的整合记录（或明确的无需整合决定）绑定到固定产物后完成验收，再通过记录在案的两步回收释放受管 checkout。执行、辅助任务完成、整合与验收是彼此独立的事实。

## 安装

hey-my-buddy 是一个共享 Agent Skill `buddy`，自带命令行，并且每个状态目录只有一个本地服务。已核验的日常安装是 0.24.0（contract 0.24.0、schema 15）：skill 位于 `~/.agents/skills/buddy`，Claude Code 通过 `~/.claude/skills/buddy` 链接读取同一目录，旧的 Codex 插件已不再使用。源码与日常安装是相互独立的事实。当前源码候选是 0.21.0（contract 0.21.0、schema 15），实现 ADR-018 的 Host 工作流：文件／标准输入任务包、按方法帮助、同目标委派、Host 直接收尾、失败结论与清理、部分成果、改配续做、累计补丁和原生用量／额度观察。[Host 工作流验收](docs/acceptance/host-workflow-0.21.0.md)记录验证结果与未验证范围。它尚未日常安装或发布；schema 14 → 15 仅通过显式空闲升级迁移。

在用户确定发布渠道与确切版本之后，安装入口是一条固定版本的包命令：

```sh
# 发布渠道与版本确定之后使用；目前不可用
uvx hey-my-buddy@0.21.0 install
```

包名是否可用、是否发布到 PyPI 仍待用户决定，本候选尚未发布到 PyPI。在正式发布之前，可以从冻结源码构建 wheel，并用绝对路径安装：

```sh
uv build --wheel --out-dir dist
uvx --from /absolute/path/hey_my_buddy-0.21.0-py3-none-any.whl hey-my-buddy install
```

机器上没有 `uv` 时，发布包里的 `install.sh`（macOS、Linux）或 `install.ps1`（Windows）会先准备固定版本的私有 `uv`，再调用同一个安装入口；它们不修改 `PATH`、shell 或 Host 设置，也不会安装全局 `buddy` 命令。安装会先列出将写入的路径、物化版本化运行时；有任务在运行时拒绝并列出这些任务（`UPGRADE_NOT_IDLE`）；空闲后整体切换 skill、启动器、运行时与服务，并保留唯一一份校验过的滚动备份用于回滚。重跑完整同版本只补齐缺失内容、不重启服务；内容损坏时用同一条固定版本命令修复；同版本内容变化会报告 `updated`。sdist 保留来源提交，解包后构建 wheel 仍保留相同 `sourceCommit`。安装或升级日常服务都需要用户的单独授权。代码与脚本可移植到 Windows，但尚未在真机验证。

### 可直接交给安装 agent 的提示

```text
请在本机为我安装 hey-my-buddy <版本>。

1. 使用我指定的发布渠道和确切版本，或我提供的 wheel。只对缺失的发布信息询问，不要假设已经发布到 PyPI。
2. 通过 Host 支持的放行方式，在 agent 或 Host 沙盒之外运行安装命令，不要 sudo：`uvx hey-my-buddy@<版本> install`；未发布的构建用 `uvx --from <wheel 绝对路径> hey-my-buddy install`。没有 uv 时改用发布包中对应的 install.sh 或 install.ps1。
3. 在写入任何内容之前，列出它将创建或替换的全部路径（skill 位于 ~/.agents/skills/buddy，Claude Code 链接 ~/.claude/skills/buddy，数据与运行时位于 ~/.local/share/hey-my-buddy，bootstrap 脚本可能另建私有 uv；Windows 使用输出中的 %LOCALAPPDATA% 数据目录）。
4. 汇报安装结果：skill 版本与放置结果、Claude Code 链接状态、服务动作，以及是否已生成备份。
5. 用已安装启动器核验，并以它实际报告的路径与版本为准：`~/.agents/skills/buddy/scripts/buddy health`、`... runtime`、`... adapters`。新 Host 会话会加载 skill；当前会话可在沙盒外直接使用启动器绝对路径。
6. 如果返回 LAUNCH_ACCESS_DENIED，就把这一条启动器命令放行到沙盒外后重试，不要全局关闭沙盒。如果服务忙碌会返回 UPGRADE_NOT_IDLE 并列出任务：如实汇报并等待，绝不为强行升级而取消工作。
```

先提交一个有界目标：

```sh
BUDDY="$HOME/.agents/skills/buddy/scripts/buddy"
"$BUDDY" submit '{"requestId":"doc-links-1","hostId":"codex","task":"检查本仓库 README 中的相对链接，只新增 buddy-doc-review.md，并列出失效链接和修复建议。","cwd":"/abs/repo","timeoutSeconds":3600,"workspace":false,"executionWorkspace":{"kind":"worktree","cwd":"/abs/repo","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["buddy-doc-review.md"],"integrator":"codex"}}'
"$BUDDY" get '{"runId":"<returned-runId>"}'
"$BUDDY" await '{"runId":"<returned-runId>"}'
```

示例把模型选择交给路由，并用 `workspace:false` 关闭 DSH 会话分组。分组是独立于 Git 隔离的 DSH 选项；`executionWorkspace` 才是工作区所有权和产物校验契约。`submit` 会返回一个私有 `controlFile`，后续 Host 命令必须提供它。确切的安装步骤、稳定错误码、harness 发现命令与恢复流程见[运维文档](docs/reference/operations.md#installation)。

## 控制台、路由与模型

打开私有本地控制台：

```sh
"$BUDDY" console
```

控制台默认免登录，通过默认浏览器打开固定回环地址。可在设置中开启“需要登录”，使用有效期 10 分钟的一次性入口；会话不按 30 天过期，可显式退出或撤销。每个获准访问的窗口都可以在版本检查下编辑设置；草稿与冲突按窗口保留。使用 `console '{"browser":false}'` 可自行打开链接；使用 `console '{"wait":true}'` 可让 CLI 留在前台，Ctrl-C 只关闭对应控制台。会话有效期、安全边界与独立的安装边界见[控制台入口说明](docs/reference/console.md)。

打开命令返回的 loopback 地址，顶部提供“委派记录 / Buddy 配置 / 设置”三个页签。委派按来源项目分组，显示原始委派方、当前 Host、执行回合和固定的路由依据；工作目标以概览卡与委派时间轴呈现，独立委派折叠在“未归档委派”中。Buddy 配置按 harness 归组模型家族，各思考档位保留独立评价；家族行显示已启用档位数与 Router 标记，已启用档位有勾选标记，路由状态行分别显示两个 Router 位置与默认模式。页面直接编辑，没有全局编辑开关：改动控件即产生草稿，底部保存栏只提供放弃与保存，保存时才申请短时发布资格；版本冲突和结果未确认时保留恢复信息。编辑只涉及你自己的家族偏好与备注、档位覆盖、启用状态、模型并发设置和 Router 位置：自动评价、证据和目录事实始终只读。每个模型家族还有用户拥有的并发上限，在下一次认领时生效，调低上限不会停止正在运行的任务。同一页还显示记录的 harness 状态，提供显式“重新检测”和自动检测失败时的高级手动路径；重新检测不调用模型。“更新记录”显示已发布版本，“设置”页放浅色/深色/跟随系统主题和本地存储检查与回收面板。查看、刷新和编辑草稿都不调用模型。

可以直接让具备 skill 的 Harness“更新黑板中的模型评价”，或在你明确需要定期更新时，通过该 Harness 自身的定时功能安排更新。[维护流程](docs/reference/evaluation-maintenance.md)增量采集跨 Host、跨项目的已验收事实，保留有证据的失败与重试结果，并以有界的卡片补丁发布，不改写用户偏好和人工备注。任务验收不触发模型调用；没有新材料时可以跳过归纳，不宣称产生了新评价。

路由由 Python 冻结合法候选并检查答案边界；不健康的 harness 会从候选中排除。路由分两种模式：快速路由只做一次调用、由 harness 真正关闭全部工具，输入只有任务、偏好与评价卡片；审阅路由是只读模式，另会读取冻结的仓库副本。快速路由已为 DSH、ZCode 与 Codex 实现：DSH 已通过授权实测，ZCode/Codex 已通过原生离线零工具检查；ZCode 的提供方验收被 429 限流阻断，Codex 快速路由尚未做付费实测。0.19.0 源码已在 macOS 的 Codex CLI 0.157.0 上验证 `openai / gpt-6-sol / high` 的只读 Router，推荐 `standard` 预算。Linux/Windows 与其他 harness 的审阅路由尚未验证；指定的 Router 还必须可用且已启用。提交时可指定 `routingMode`；审阅 Router 不可用时默认降级到快速路由，除非 Host 传入 `allowRoutingFallback:false`。请注意：快速路由会把每个任务的描述发送给快速 Router 所在的模型提供方（包括准备交给其他模型执行的任务），审阅路由还会读取冻结的仓库副本。安装后由用户在 Buddy 配置中选择配置，本候选不修改用户设置。见[路由契约](docs/reference/decision.md)、[harness 发现](docs/reference/harnesses.md)和[验收证据](docs/acceptance/routing-modes-0.20.0.md)。

## 执行与恢复

Codex Host 为每个运行中的委派派生一个只做监控的原生子代理，显式选择当前 Codex 可用、能执行命令的最便宜模型，并使用最低推理档位。父代理继续独立工作，并负责全部决策与验收；Claude Code 保持后台 Bash 等待。权限、等待上限与前台回退见 [Codex 等待指引](docs/reference/usage.md#waiting-from-codex)。

独立任务默认在一个机器级并发总上限下并行运行（默认 8，可配置 1–32），路由与执行共享该上限；在此之上，每个精确的 adapter/provider/model 家族还有用户设置的上限（每个家族默认 2），各思考档位与使用该模型的路由决策共享同一家族计数。服务会自动启动相应的 worker 池；实际安装可配置不同的总上限，请以 `health.capacity` 为准。工作区重叠和独占资源仍会让冲突任务排队；并行修改代码需要使用不同 worktree。可在[容量配置](docs/reference/operations.md#private-state-and-environment)中调整上限，并用 `health.capacity` 查看总量与各模型占用。

等待超时、关闭终端或连接中断都不会取消任务。可以用同一个 `runId` 通过 `await`、`get` 或 `status` 接回；只有显式 `cancel` 才会停止目标，并且只有拿到真实停止证据才报告已关闭。每次接续都会创建新的 attempt。DSH 重建新会话；ZCode 仅在目标、checkout 和配置绑定都匹配时恢复已有证据确认的原生会话，Codex 也只恢复其精确绑定的原生线程；没有已确认的会话或配置发生变化时，harness 会重建新的根会话。守护服务重启后，仍持有子进程的同一 Worker 会按身份重连不确定的 attempt 并清除重启等待原因，其他进程无权接管；已落盘的不变完成收据只重放、不重新执行。结果会记录真实终止原因（正常完成、用户取消、执行到期、harness 错误或传输故障），到期不会被显示成用户取消。有界活动投影展示当前阶段、最后原生活动时间和诚实计数；空日志、缺失 PID 或静态会话列表都不能证明进程已停止。请区分请求的、已解析的和实际观测到的模型身份。

用户授权的长任务可显式设置 `"timeoutSeconds": 0`，使执行没有总时限；省略该字段仍默认 1800 秒。Host 仍可主动取消，单次 `await` 等待结束也不会停止任务。选择该配置前可查阅[执行期限与等待窗口](docs/reference/cli.md#defaults-and-bounds)。

服务和 Worker 从上述稳定私有运行时执行。日常命令直接执行 active 运行时自己的 Python，不经过 `uv`；`uv` 只用于安装与升级。全新黑板位于 `~/.local/share/hey-my-buddy/state`；位于旧默认位置的旧黑板目录保留为归档，不会被读取、转换或导入。工作目录和 Git worktree 都不是操作系统沙箱。

## 当前状态与限制

日常安装是 [0.24.0/contract 0.24.0/schema 15](docs/acceptance/installed-0.24.0.md)。它在 ADR-018 两部分之上加入：本机控制台可选登录（默认关闭）、唯一合法候选由程序直接选定、精简后的控制台文案、如实显示取消的发起者、计费方式标注以及额度确认耗尽时暂时移出自动路由、按 harness 版本记录的审阅验证，以及 ADR-019 第 11 条：attempt 目录只保存证据，harness 私有内容与临时凭据放在备份之外的私有区，只读的 `backup-preflight` 能在升级前说明能否安装。[0.24.0 安装记录](docs/acceptance/installed-0.24.0.md)区分已验证的行为与仍需用户批准的检查。

日常运行时支持 macOS 与 Linux、本地单用户 SQLite 状态；Windows 的代码与脚本可移植，但未在真机验证。ZCode 支持 API-key 提供方、活动观察和协作式询问：问题等待根任务的下一个工具检查点或结束尝试，无法打断正在运行的工具，也不会开启新回合。原生权限请求和需要长时间等待的 Host 决策仍通过 attention/assistance 边界处理。Codex 使用实验性的 App Server，未声明 inquiry。0.19.0 源码在 macOS 的 Codex CLI 0.157.0 上验证了 `openai / gpt-6-sol / high` 的只读 Router，推荐 `standard` 预算；Linux/Windows 与其他 harness 仍未验证。安装不会修改用户的 Router 设置。Claude P1 需要 Anthropic 第一方认证，默认使用隔离设置，每次接续都重建会话，未声明 inquiry。其[参考文档](docs/reference/claude.md)记录已验证的原生路径、日常安装的只读委派链路、模拟回归覆盖和其余限制。目前不提供货币预算、自动社区评价、内置定期维护或原生 App 回合结束后唤醒。后台回访需要用户明确要求。

## 文档与开发

从[文档索引](docs/README.md)进入命令、架构、harness 适配器和设计历史；[运维文档](docs/reference/operations.md)拥有安装、错误码与恢复，[harness 发现](docs/reference/harnesses.md)拥有发现与健康，[控制台](docs/reference/console.md)与[评价](docs/reference/evaluation.md)分别拥有控制台界面和评价表。[AGENTS.md](AGENTS.md)说明仓库开发约束；日常使用不需要 npm 或自行构建前端。问题可提交至 [GitHub Issues](https://github.com/Dsssyc/hey-my-buddy/issues)。

## 许可证

[MIT](LICENSE)。

仓库验证使用 uv 与受支持的 Node 版本（见 `apps/console/package.json`）。先执行 `npm --prefix apps/console ci` 准备控制台测试依赖，再执行 `uv run --frozen python -m buddy.checks`；完整检查包含由真实前端解析器读取合成预览数据的回归测试。

B 批源码加入原生计费标注、额度耗尽的候选过滤，以及 Buddy 配置和 `harness-verify` 的当前版本审阅验证；新版本验证前沿用快速路由降级。取消显示记录中的发起者与理由，控制台说明已精简。Worker 独立账户和登录须在私有目录前提合入后继续。[源码与原生检查记录](docs/acceptance/worker-accounts-phase1-0.23.0.md)与日常安装分别记录。

0.24.0 源码将 attempt 证据与 harness 私有状态分区。请求安装授权前，用新包的 `buddy backup-preflight '{}'` 查看将复制、跳过和拒绝的路径；它不启动服务、不写数据。安装器在停机前检查该清单，已识别且停止已证明的旧布局只在已验证备份后整理；凭据与原生私有目录不进入备份。详情见[操作文档](docs/reference/operations.md#backup-upgrade-and-storage-contract)。
