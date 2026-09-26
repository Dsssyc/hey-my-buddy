# hey-my-buddy

[English](README.md)

Buddy 让 Host agent 把边界明确的工作交给本地编码 harness，同时保留对整体目标的责任。Host 可以亲自实现一部分，再把其他部分交给能力、成本或模型更适合的 Worker。目前接入的编码 harness 是 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（`dsh`）、ZCode（`zcode`）以及实验性的 Codex App Server（`codex`）；Claude Code 适配器（`claude`，[参考文档](docs/reference/claude.md)）作为 P1 适配器加入，默认采用用户确认的隔离设置；已授权的实机探针验证了原生结果、取消和有界沙箱路径，随后又通过已安装服务完成了单独获准的只读委派链路验收。

目标、决定与结果保存在本地 SQLite 黑板中。编码任务有明确的 Git 工作区和固定输入、产物快照；私有 React/Vite 控制台可以查看任务、路由、共享模型配置、用户偏好、模型并发设置和评价卡片。

## 如何协作

适合委派的工作包括范围明确的实现、测试、可复现问题调查、文档整理，以及结果可检查的文件转换。改动很小、答案已知或需求尚未明确时，由 Host 直接处理。

1. Host 确定目标、模型参数和执行工作区。完整的 adapter/provider/model/effort 组合经过校验后直接派发；信息不完整时通过已配置的决策配置和有界评价表完成路由，最多八条任务级软路由偏好只影响该目标，不改变共享设置。并行写入使用独立 Git worktree，整合后的结果由明确指定的整合者负责。
2. Worker 使用自己的工具和内部 subagent 完成工作。需要协助时，它以结构化结果结束当前回合；Host 可以批准明确的辅助任务、说明理由后拒绝、补充新的接续输入，或在路由边界给出完整配置。控制权由私有 control 凭据约束，Host 名称本身不是权限。
3. Host 检查真实 diff、运行相关验证，把已验证的整合记录（或明确的无需整合决定）绑定到固定产物后完成验收，再通过记录在案的两步回收释放受管 checkout。执行、辅助任务完成、整合与验收是彼此独立的事实。

## 安装并试用

需要 macOS 或 Linux、[uv](https://docs.astral.sh/uv/) 与 Python 3.12–3.14、DSH 所需的 Node.js 20+ 及所安装 ZCode CLI 要求的运行环境，以及已配置服务商凭据的本地 `dsh` 和/或 ZCode。Codex 使用已安装的 App Server 和既有原生账户登录，不需要 API key。Buddy 使用 harness 已配置的凭据，不修改全局模型设置。

从本仓库自带的插件市场安装 `hey-my-buddy`，不需要个人市场，也不依赖任何公共目录上架。从本地 checkout 安装：

```sh
codex plugin marketplace add /abs/path/to/hey-my-buddy
codex plugin add hey-my-buddy@hey-my-buddy
```

Git 克隆使用同一个市场：`codex plugin marketplace add Dsssyc/hey-my-buddy --ref main` 会注册仓库内的清单；`packaging/stage-plugin.py` 打包出的目录本身也可作为市场根。分阶段本地安装、已有市场条目和安装后的核验步骤见[安装说明](docs/reference/operations.md#installation)。新建任务以加载 `$buddy` skill。skill 会从插件自身位置解析 `bin/buddy` 启动器；没有需要单独安装的 skill，也没有旧目录回退。首次需要服务的命令会在 `~/.local/share/hey-my-buddy/runtime` 安装稳定运行时，之后替换插件不会中断正在运行的工作。

先提交一个有界目标：

```sh
BUDDY="<absolute-plugin-root>/bin/buddy"
"$BUDDY" submit '{"requestId":"doc-links-1","hostId":"codex","task":"检查本仓库 README 中的相对链接，只新增 buddy-doc-review.md，并列出失效链接和修复建议。","cwd":"/abs/repo","timeoutSeconds":3600,"workspace":false,"executionWorkspace":{"kind":"worktree","cwd":"/abs/repo","access":"write","base":{"kind":"working-tree"},"includeUntracked":[],"writeScope":["buddy-doc-review.md"],"integrator":"codex"}}'
"$BUDDY" get '{"runId":"<returned-runId>"}'
"$BUDDY" await '{"runId":"<returned-runId>"}'
```

示例把模型选择交给路由，并用 `workspace:false` 关闭 DSH 会话分组。分组是独立于 Git 隔离的 DSH 选项；`executionWorkspace` 才是工作区所有权和产物校验契约。`submit` 会返回一个私有 `controlFile`，后续 Host 命令必须提供它。

## 控制台、路由与模型

打开私有本地控制台：

```sh
"$BUDDY" console
```

0.13.0 源码候选通过有效期 60 秒的一次性入口打开默认浏览器。新打开的会话接管写权限，旧会话保持只读并保留本地草稿。使用 `console '{"browser":false}'` 可自行打开链接；使用 `console '{"wait":true}'` 可让 CLI 留在前台，Ctrl-C 只关闭对应控制台。[控制台入口说明](docs/reference/console.md)记录会话过期行为与独立的安装边界。

打开命令返回的 loopback 地址，顶部提供“委派记录 / 模型卡片 / 路由配置”三个页签。委派按来源项目分组，显示原始委派方、当前 Host、执行回合和固定的路由依据。模型按家族聚合，各思考档位保留独立评价；已启用档位有勾选标记，与当前查看的档位分开显示。右上角“编辑模式”创建本地草稿，保存时才申请短时发布资格；版本冲突和结果未确认时保留恢复信息。编辑模式只修改你自己的意见、偏好、启用状态、模型并发设置和路由配置：自动评价、证据和目录事实始终只读。每个模型家族在模型卡片上还有一个用户拥有的并发上限，走同样的本地草稿与短时发布流程；它在下一次认领时生效，调低上限不会停止正在运行的任务。同一入口可以显示不可用配置并分页查看保留历史；失效的 pin 或决策配置只给出提示，不阻止保存其他修改。证据区始终只读，并提示通过具备 skill 的 Harness 更新；“更新记录”显示已发布版本。深浅主题开关只记住显示偏好。查看、刷新和编辑草稿都不调用模型。

可以直接让具备 skill 的 Harness“更新 Buddy 黑板的模型评价”，或在你明确需要定期更新时，通过该 Harness 自身的定时功能安排更新。[维护流程](docs/reference/evaluation-maintenance.md)增量采集跨 Host、跨项目的已验收事实，保留有证据的失败与重试结果，并以有界的卡片补丁发布，不改写用户偏好和人工意见。任务验收不触发模型调用；没有新材料时可以跳过归纳，不宣称产生了新评价。

## 执行与恢复

独立任务默认在一个机器级并发总上限下并行运行（默认 8，可配置 1–32），路由与执行共享该上限；在此之上，每个精确的 adapter/provider/model 家族还有用户设置的上限（每个家族默认 2），各思考档位与使用该模型的路由决策共享同一家族计数。服务会自动启动相应的 worker 池；实际安装可配置不同的总上限，请以 `health.capacity` 为准。工作区重叠和独占资源仍会让冲突任务排队；并行修改代码需要使用不同 worktree。可在[容量配置](docs/reference/operations.md#private-state-and-environment)中调整上限，并用 `health.capacity` 查看总量与各模型占用。

等待超时、关闭终端或连接中断都不会取消任务。可以用同一个 `runId` 通过 `await`、`get` 或 `status` 接回；只有显式 `cancel` 才会停止目标，并且只有拿到真实停止证据才报告已关闭。每次接续都会创建新的 attempt。DSH 重建新会话；ZCode 仅在目标、checkout 和配置绑定都匹配时恢复已有证据确认的原生会话，Codex 也只恢复其精确绑定的原生线程；没有已确认的会话或配置发生变化时，harness 会重建新的根会话。守护服务重启后，仍持有子进程的同一 Worker 会按身份重连不确定的 attempt 并清除重启等待原因，其他进程无权接管；已落盘的不变完成收据只重放、不重新执行。结果会记录真实终止原因（正常完成、用户取消、执行到期、harness 错误或传输故障），到期不会被显示成用户取消。有界活动投影展示当前阶段、最后原生活动时间和诚实计数；空日志、缺失 PID 或静态会话列表都不能证明进程已停止。请区分请求的、已解析的和实际观测到的模型身份。

用户授权的长任务可显式设置 `"timeoutSeconds": 0`，使执行没有总时限；省略该字段仍默认 1800 秒。Host 仍可主动取消，单次 `await` 等待结束也不会停止任务。选择该配置前可查阅[执行期限与等待窗口](docs/reference/cli.md#defaults-and-bounds)。

服务和 Worker 从上述稳定私有运行时执行。全新黑板位于 `~/.local/share/hey-my-buddy/state`；位于旧默认位置的旧黑板目录保留为归档，不会被读取、转换或导入。工作目录和 Git worktree 都不是操作系统沙箱。

当前限制：仅支持 POSIX；本地单用户 SQLite 状态。ZCode 支持 API-key 提供方、活动观察和协作式询问：问题等待根任务的下一个工具检查点或结束尝试，无法打断正在运行的工具，也不会开启新回合。原生权限请求和需要长时间等待的 Host 决策仍通过 attention/assistance 边界处理。Codex 使用实验性的 App Server，未声明 inquiry 或无工具路由能力。Claude P1 需要 Anthropic 第一方认证，默认使用隔离设置，每次接续都重建会话，未声明 inquiry。[参考文档](docs/reference/claude.md) 记录已验证的原生路径、日常安装的只读委派链路、模拟回归覆盖和其余限制。目前不提供货币预算、自动社区评价、内置定期维护或原生 App 回合结束后唤醒。后台回访需要用户明确要求。

当前源码候选使用 contract 0.14.0、schema 12，新增[工作目标与委派时间轴](docs/reference/objectives.md)，并包含[控制台入口与单写会话](docs/reference/console.md)与已安装的[任务标题回退](docs/acceptance/title-fallback-0.12.0.md)。已有的 schema 11 看板需先按该文档执行显式离线准备，再进行切换。源码改动与日常运行时的实际安装分别记录。日常连接使用轻量 `ping`；显式 `health` 保留完整存储诊断。用 `health` 和 `runtime` 核对实际运行的安装；新契约需要协调空闲切换。

## 文档与开发

[文档索引](docs/README.md)提供命令、架构、harness 适配器和设计历史的入口。[AGENTS.md](AGENTS.md)说明仓库开发约束；日常使用不需要 npm 或自行构建前端。问题可提交至 [GitHub Issues](https://github.com/Dsssyc/hey-my-buddy/issues)。

## 许可证

[MIT](LICENSE)。
