# hey-my-buddy

一个个人用的 Codex 技能：把**目标清晰、边界明确**的实现、调查、批量文件改写、补测试或写文档任务尽早交给本地 DeepSeek Harness（dsh）执行，只把紧凑结果带回 GPT，降低 GPT 上下文消耗。

## 架构与责任划分

Codex 负责判断是否委派、写任务包与验收标准、按任务难度选择模型，effort 默认 max（除非用户另行指定），并在事后独立复核真实产物；dsh 只负责执行这一次有界任务。默认值取自实际脚本与目录：`provider=deepseek-official`、`model=deepseek-flash`（本机目录标注为 DeepSeek-V41-Flash）、`effort=max`、默认 `contextWindow=1,000,000`；不按 Pro/Flash 名字推断强弱，模型目录变化时按实际 dsh 目录核对 ID，出错如实上报、不静默降级。脚本只回传紧凑 JSON：status、退出码、耗时、请求的 model/effort、日志路径，以及最多 6000 字符的 `finalText` 和截断标志；冗长输出留在本地日志中。

## 目录结构

```
hey-my-buddy/
├── README.md
├── .gitignore
└── deepseek-delegate/          # 技能本体，安装到 ~/.codex/skills/
    ├── SKILL.md                # 委派时机、任务包、模型/effort、边界与复核要求
    ├── agents/openai.yaml      # Codex 技能接口元数据
    └── scripts/run.mjs         # 单次委派包装：覆盖 model/effort、隔离日志、超时
```

`.dsh-skill-build/` 是本机临时目录（任务规格、stdout/stderr、机器路径），不入库。

## 前置条件

- Node.js 需支持 `node:util.parseArgs` 及选项默认值。
- 本机已安装并配置好 dsh，凭据可用；本仓库不安装依赖、不登录、不改全局 dsh 或 Codex 设置，`js-yaml` 从 dsh 安装目录解析。
- 已有 `~/.codex/skills/` 技能目录。

## 安装（符号链接，不覆盖）

在克隆根目录执行：

```sh
mkdir -p "$HOME/.codex/skills"
if [ -e "$HOME/.codex/skills/deepseek-delegate" ] || [ -L "$HOME/.codex/skills/deepseek-delegate" ]; then
  echo "已存在同名技能，未做任何修改：$HOME/.codex/skills/deepseek-delegate" >&2
else
  ln -s "$PWD/deepseek-delegate" "$HOME/.codex/skills/deepseek-delegate"
fi
```

**不要覆盖已存在的 `~/.codex/skills/deepseek-delegate`**：先确认它是否为旧版本或别的技能，需要替换时自己手动删除或改名。链接指向克隆目录，更新代码后无需重新链接。

## 使用

在 Codex 中直接委派：

```
$deepseek-delegate 重构 src/parser 的错误处理并补齐单元测试；完成后我会自己核对 diff 和测试结果。
```

也可以手动调用脚本（路径为基于 `$PWD` 的绝对路径）：

```sh
node "$PWD/deepseek-delegate/scripts/run.mjs" \
  --cwd "$PWD" \
  --task-file "$PWD/.dsh-skill-build/task.md" \
  --model deepseek-flash --effort max --timeout 1800
```

`--cwd`、`--task-file` 必填；`--model`、`--effort`、`--timeout`（秒，10–86400，默认 1800）可选。

## 边界与注意事项

- `--cwd` 只是 dsh 的工作目录，**不是安全沙箱**：允许改动的范围必须写进任务包；需要更强隔离时给 dsh 独立工作区（如 git worktree）。
- 每次运行会复制当前 settings 文档、只替换 `agent-default-model`（model/effort），再通过临时 `--patch` 覆盖传入；其余设置原样保留，全局设置不被修改。
- 超过 32KB 的任务文件不随命令行传入，而是把文件路径交给 dsh 自行读取；该文件在任务结束前必须保持可用。
- stdout/stderr 只写入本地私有日志，日志不上传，需要时再自行查看。
- 委派失败最多带新信息重试一到两次；不自动发布、发消息或提权。
- 真实验收仍是 Codex 的责任：自己查看 diff、新增/删除的文件与命令输出，并运行测试、构建或 lint；退出码 0 不代表任务正确。

## 当前机器的安装假设（非通用保证）

这是个人集成，`scripts/run.mjs` 按固定位置解析，不保证在其它机器上可用：

- 启动器：`~/.local/bin/dsh`
- 依赖解析：`~/.local/share/dsh/lib/node_modules/@deepseek-ai/dsh/package.json`（`createRequire` 从这里加载 `js-yaml`）
- 设置文档：`$DSH_HOME/settings.yaml`；未设置 `DSH_HOME` 时为 `~/.dsh/settings.yaml`

位置不同就需要修改 `scripts/run.mjs`。仓库内不包含凭据，路径均以 `~` 或 `$HOME`、`$PWD` 表示。
