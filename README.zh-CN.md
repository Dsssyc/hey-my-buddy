# hey-my-buddy

[English](README.md)

Buddy 让 Codex 把一个**边界明确的任务**交给本地
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（`dsh`）运行，并拿回
可以独立核查的真实成果。Codex 仍然负责界定任务、选择路径和检查结果，dsh 负责主要执行。

## 它有什么用

- **成果可以直接检查。** 给定工作目录和带验收标准的任务，它会修改文件、运行命令，
  把成果留在磁盘上。
- **等待结束后仍可接回。** 关闭等待终端或等待超时，任务仍会继续：Codex 可以再次接上同一个任务，报告
  它的进度或结果。
- **控制权仍在你手里。** 任务使用你自己的凭据和权限执行，Codex 会先检查真实文件和检查项，
  再判断任务是否完成。

## 适合委派的工作

适合：调查失败的测试或可复现的 bug；实现范围明确的功能或重构；批量文件转换；为指定文件更新
文档；产出结果可验证的计算或报告。

留在 Codex：一行两行的修改、已知答案、需求仍在变化，或需要你亲自界定和最终判断的工作。

## 环境要求

- macOS 或 Linux（POSIX）。
- [uv](https://docs.astral.sh/uv/) 与 Python 3.12–3.14；默认 dsh 路径需要 Node.js 20 或更高版本。
- 已自行安装并配置好凭据的本地 `dsh`（<https://github.com/deepseek-ai/deepseek-harness>）。
- 支持技能的 Codex。

Buddy 不安装 dsh、不配置模型凭据，也不修改全局模型设置。

## 开始使用（0.4.0 分支）

这份 0.4.0 代码位于 `socu/python-blackboard` 分支，尚未合并到 `main`，请显式克隆该分支：

```sh
git clone --branch socu/python-blackboard https://github.com/Dsssyc/hey-my-buddy.git
cd hey-my-buddy
uv sync --frozen --project deepseek-delegate --python 3.12
```

把整个 `deepseek-delegate/` 目录安装为独立的 Codex 技能（复制整个目录，不能只复制
`SKILL.md`）：

```sh
BUDDY_SKILLS_DIR="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$BUDDY_SKILLS_DIR"
if [ -e "$BUDDY_SKILLS_DIR/deepseek-delegate" ] || [ -L "$BUDDY_SKILLS_DIR/deepseek-delegate" ]; then
  printf '%s\n' '此位置已有技能，请参阅升级说明。'
else
  ln -s "$PWD/deepseek-delegate" "$BUDDY_SKILLS_DIR/deepseek-delegate"
fi
```

已有安装请参阅[升级说明](deepseek-delegate/references/operations.md#runtime-lifecycle-and-upgrade)。
[使用说明](deepseek-delegate/references/usage.md)提供复制安装、插件安装和工作区分组配置。

在新的 Codex 任务中，用自然语言发起委派：

> 用 `$deepseek-delegate` 检查当前仓库 README 和 docs 中的相对链接，只新增
> `buddy-doc-review.md`，列出失效链接和修复建议。这次关闭 dsh 工作区分组
> （`workspace: false`）。任务完成后请复核报告，并把结果给我看。

`$deepseek-delegate` 是独立技能；当本仓库作为 Codex 插件安装时，同一个技能叫 `$buddy`
（安装细节见使用说明）。

## 会发生什么

Codex 只启动一次任务，并在同一回合里等待它运行。你可以询问任务正在做什么；任务返回时会得到结果
以及日志和产物的位置。随后 Codex 会检查真实文件，执行相关验证，并报告核实到的内容。

如果等待先结束，任务仍按自己的执行期限继续运行，Codex 可以凭保存的运行 ID 接回。
需要跨回合运行时，[后台流程](deepseek-delegate/references/usage.md#background-work-that-outlives-the-turn)
使用 App 周期性跟进；目前不支持回合结束后的即时自动接回。

## 需要知道

- 工作区分组默认开启，需要一次性安装一个宿主桥接；上面的第一个示例用 `workspace: false`，
  因此无需该配置即可运行。不要假设默认值被改动过，详见
  [运维说明](deepseek-delegate/references/operations.md)。
- 执行期限默认 30 分钟。长任务请在请求中明确更长的时限，最多 24 小时。
- 请写清允许修改的文件。工作目录不会限制文件访问权限；独立 worktree 可避免并行修改相互干扰。
- Buddy 只管理自己启动的任务；独立的 dsh 会话不受影响。
- 任务协调和结果存储在本机，模型请求使用 dsh 配置的服务商；Buddy 无需注册 MCP。
- 第一次运行可能稍慢，因为服务会准备一个私有运行时。

## 文档与支持

- [docs/README.md](docs/README.md) —— 全部文档及其职责。
- 详细主题：[使用说明](deepseek-delegate/references/usage.md)、
  [CLI](deepseek-delegate/references/cli.md)、
  [运维](deepseek-delegate/references/operations.md)、
  [worker](deepseek-delegate/references/workers.md)、
  [runner](deepseek-delegate/references/runner.md) 与
  [架构](deepseek-delegate/references/architecture.md)。
- 问题与支持：[GitHub Issues](https://github.com/Dsssyc/hey-my-buddy/issues)。
- 欢迎贡献；提交前请先看 [AGENTS.md](AGENTS.md) 列出的检查命令。
- 设计历史：[ADR-001](docs/decisions/001-python-transactional-blackboard.md) 与
  [0.4.0 验收记录](docs/acceptance/python-blackboard-0.4.0.md)。

## 许可证

[MIT](LICENSE)。可单独复制的 `deepseek-delegate/` 目录附带自己的许可证副本。
