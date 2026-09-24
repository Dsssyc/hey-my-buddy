# hey-my-buddy

[English](README.md)

Buddy 让 Host agent 把边界明确的工作交给本地编码 harness，同时保留对整体目标的责任。Host 可以亲自实现一部分，再把其他部分交给能力、成本或模型更适合的 Worker。目前接入的编码 harness 是 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（`dsh`）和 ZCode（`zcode`）。

目标、决定与结果保存在本地 SQLite 黑板中。编码任务有明确的 Git 工作区和固定输入、产物快照；私有 React/Vite 控制台可以查看任务、路由、共享模型配置、用户偏好和评价卡片。

## 如何协作

适合委派的工作包括范围明确的实现、测试、可复现问题调查、文档整理，以及结果可检查的文件转换。改动很小、答案已知或需求尚未明确时，由 Host 直接处理。

1. Host 确定目标、模型参数和执行工作区。完整的 adapter/provider/model/effort 组合经过校验后直接派发；信息不完整时通过已配置的决策配置和有界评价表完成路由。并行写入使用独立 Git worktree，整合后的结果由明确指定的整合者负责。
2. Worker 使用自己的工具和内部 subagent 完成工作。需要协助时，它以结构化结果结束当前回合；Host 可以批准明确的辅助任务、说明理由后拒绝、补充新的接续输入，或在路由边界给出完整配置。控制权由私有 control 凭据约束，Host 名称本身不是权限。
3. Host 检查真实 diff、运行相关验证，然后验收固定产物。执行、辅助任务完成、整合与验收是彼此独立的事实。

## 安装并试用

需要 macOS 或 Linux、[uv](https://docs.astral.sh/uv/) 与 Python 3.12–3.14、DSH 所需的 Node.js 20+ 及所安装 ZCode CLI 要求的运行环境，以及已配置服务商凭据的本地 `dsh` 和/或 ZCode。Buddy 使用 harness 已配置的凭据，不修改全局模型设置。

从带有 `hey-my-buddy` 的插件市场安装。下面的命令适用于已经配置好市场、且市场条目指向该打包目录的情况：

```sh
uv run --frozen python packaging/stage-plugin.py --destination /path/to/marketplace/plugins/hey-my-buddy
codex plugin add hey-my-buddy@your-marketplace
```

首次本地安装请先按[市场配置说明](docs/reference/operations.md#installation)设置。新建任务以加载 `$buddy` skill。skill 会从插件自身位置解析 `bin/buddy` 启动器；没有需要单独安装的 skill，也没有旧目录回退。首次需要服务的命令会在 `~/.local/share/hey-my-buddy/runtime` 安装稳定运行时，之后替换插件不会中断正在运行的工作。

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

打开命令返回的 loopback 地址，顶部提供“委派记录 / 模型卡片 / 路由配置”三个页签。委派按来源项目分组，显示原始委派方、当前 Host 和执行配置，并按需加载更早历史；点击执行配置可查看该委派的路由依据和此前决定，Host 指定与缺失记录会明确标注。模型按 harness、提供方和模型聚合，思考档位在详情内切换，各自的评价保持独立；模型卡片页提供评价维护与整理建议入口，路由配置页仅保留配置项。横屏布局让列表与分区详情分别滚动，切换视图时保留内存中的草稿。初始固定决策配置由你明确指定；模型发现只提出默认禁用的配置及其原生 effort，用户偏好用于排序或约束合法候选，查看或刷新页面不会调用模型。

## 执行与恢复

等待超时、关闭终端或连接中断都不会取消任务。可以用同一个 `runId` 通过 `await`、`get` 或 `status` 接回；只有显式 `cancel` 才会停止目标，并且只有拿到真实停止证据才报告已关闭。每次接续都会创建新的 attempt。DSH 重建新会话；ZCode 仅在目标、checkout 和配置绑定都匹配时恢复已有证据确认的原生会话。没有已确认的前一会话或配置发生变化时，ZCode 会重建新的根会话。请区分请求的、已解析的和实际观测到的模型身份。

服务和 Worker 从上述稳定私有运行时执行。全新黑板位于 `~/.local/share/hey-my-buddy/state`；位于旧默认位置的旧黑板目录保留为归档，不会被读取、转换或导入。工作目录和 Git worktree 都不是操作系统沙箱。

当前限制：仅支持 POSIX；本地单用户 SQLite 状态；ZCode 仅支持 API-key 提供方且没有 inquiry 桥；没有货币预算、自动社区评价或原生 App 回合结束后唤醒。[0.6 验收记录](docs/acceptance/neutral-core-0.6.0.md) 收录了回归检查、真实 DSH/ZCode 协作及安装态运行时的路由产物任务。每次升级后，用 `health` 和 `runtime` 核对实际运行的安装。

## 文档与开发

[文档索引](docs/README.md)提供命令、架构、harness 适配器和设计历史的入口。[AGENTS.md](AGENTS.md)说明仓库开发约束；日常使用不需要 npm 或自行构建前端。问题可提交至 [GitHub Issues](https://github.com/Dsssyc/hey-my-buddy/issues)。

## 许可证

[MIT](LICENSE)。
