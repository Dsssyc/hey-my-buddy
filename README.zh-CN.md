# hey-my-buddy

[English](README.md)

Buddy 让 Codex 把边界明确的工作交给本地编码 agent，同时保留对整体任务的责任。Host 可以亲自实现一部分，再把其他部分交给能力或成本更适合的 Worker。目前接入的编码工具是 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（`dsh`）。

任务、决定与结果保存在本地黑板中。仓库任务有明确的工作区和固定输入、产物快照；React/Vite 控制台可以查看执行情况，以及跨项目共享的模型配置、用户偏好和评价卡片。

## 如何协作

适合委派的工作包括范围明确的实现、测试、可复现问题调查、文档整理，以及结果可检查的文件转换。改动很小、答案已知或需求尚未明确时，由 Host 直接处理。是否委派，要同时考虑执行收益、交接、验收和可能的返工。

1. Host 确定任务、模型参数和工作区。并行写入使用独立 Git worktree；顺序执行且只有一个写入者时，可以使用现有 checkout。整合后的结果由明确指定的整合者负责。
2. Worker 使用自己的工具和内部 subagent 完成工作。需要协助时，它结束当前回合并记录请求；Host 可以批准辅助任务和一次自动接续，随后同一逻辑目标在新会话中根据固定产物继续执行。
3. Host 检查实际 diff、运行相关验证后，验收最终产物。辅助任务完成、整合与最终验收分别记录。

[工作流说明](deepseek-delegate/references/workflow.md)提供协助、接续和控制权的具体操作。非 Git 工作仍可使用普通一次性任务命令。

## 安装并试用

需要 macOS 或 Linux、[uv](https://docs.astral.sh/uv/) 与 Python 3.12–3.14、供 Buddy runner 使用的 Node.js 20+、已配置服务商凭据的本地 dsh，以及支持 skill 的 Codex。Buddy 使用 harness 已有凭据，不修改全局模型设置。

在仓库中，把完整的 `deepseek-delegate/` 目录安装为独立 skill：

```sh
git clone https://github.com/Dsssyc/hey-my-buddy.git
cd hey-my-buddy
uv sync --frozen --project deepseek-delegate --python 3.12

BUDDY_SKILLS_DIR="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$BUDDY_SKILLS_DIR"
if [ -e "$BUDDY_SKILLS_DIR/deepseek-delegate" ] || [ -L "$BUDDY_SKILLS_DIR/deepseek-delegate" ]; then
  printf '%s\n' '此位置已有 skill，请按升级说明处理。'
else
  ln -s "$PWD/deepseek-delegate" "$BUDDY_SKILLS_DIR/deepseek-delegate"
fi
```

使用这个链接时，请保留对应仓库目录。若采用复制安装，需复制整个目录，包括脚本、Python 包、插件、参考文档和锁文件。[使用说明](deepseek-delegate/references/usage.md)也提供 Codex 插件的安装方式：插件入口是 `$buddy`，独立入口是 `$deepseek-delegate`。

已有安装请先按[升级说明](deepseek-delegate/references/operations.md#database-upgrade)切换到 0.5.0。Schema 5/6 需要显式离线迁移，并生成经过验证的备份。如果同时安装了两个 skill 入口，应让它们使用同一发布版本。

在新的 Codex 任务中试用：

> 用 $deepseek-delegate 检查当前仓库 README 和 docs 中的相对链接。在独立 worktree 中只新增 buddy-doc-review.md，列出失效链接和修复建议。本次设置 workspace: false，关闭 DSH 会话分组。完成后请复核报告，并给出固定产物的路径。

会话分组与执行工作区隔离是独立设置。分组默认开启，需要 [DSH 工作区桥接](deepseek-delegate/references/operations.md#workspace-bridge)；上面的示例显式关闭了分组。

## 控制台与模型选择

在仓库中打开私有本地控制台：

```sh
BUDDY="$PWD/deepseek-delegate/scripts/launch-buddy.sh"
"$BUDDY" console
```

打开命令返回的地址，即可查看任务、处理协助请求和核对固定产物。控制台也支持发现已安装的模型配置、启用配置、设置用户偏好和维护跨项目共享的评价卡片。查看或刷新页面不会调用模型。

初始决策配置由你明确指定。需要比较模型时，决策 Buddy 可以读取有界的当前评价表，推荐可用的模型与 effort 组合，再由 Host 授权实际任务。评价维护需要显式发起；自动采纳有效卡片更新需要另行启用。配置、证据与编辑规则见[评价表与控制台说明](deepseek-delegate/references/evaluation.md)。

## 执行与恢复

等待结束或关闭等待终端不会取消任务，可以使用保存的运行 ID 接回。执行仍受自身期限约束：每次 attempt 默认 30 分钟，最多 24 小时。接续会创建新的 attempt 和会话。需要超出 Host 当前回合继续工作时，应使用[后台跟进流程](deepseek-delegate/references/usage.md#background-work-that-outlives-the-turn)；目前没有原生即时 App 唤醒。

服务和 Worker 从稳定的私有运行时执行，替换插件缓存不会中断它们；首次启动会准备这个运行时。工作目录和 Git worktree 都不是操作系统沙箱，任务仍拥有本地用户的访问能力，共享服务和仓库元数据仍需协调。

目前只有 DSH 编码 Buddy 已接入。其他 harness、自动收集社区评价、货币预算和定期评价维护尚未实现。任务协调保存在本地，模型请求发送至已配置的服务商。

## 文档与开发

[文档索引](docs/README.md)提供命令、架构、适配器和设计历史的入口。[0.5.0 验收记录](docs/acceptance/productivity-workflow-0.5.0.md)区分了真实 DSH、浏览器验证与实现声明。参与开发请遵循 [AGENTS.md](AGENTS.md)；日常使用不需要 npm 或自行构建前端。问题可提交至 [GitHub Issues](https://github.com/Dsssyc/hey-my-buddy/issues)。

## 许可证

[MIT](LICENSE)。独立的 `deepseek-delegate/` 目录内也附有许可证。
