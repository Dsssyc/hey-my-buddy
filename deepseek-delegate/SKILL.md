---
name: deepseek-delegate
description: Delegate clear, bounded implementation, investigation, file transformation, testing, or documentation tasks to DeepSeek Harness (dsh) early to save GPT tokens. Select a model for the task, default to max reasoning, and independently verify its work. Skip trivial one-step tasks.
---

# deepseek-delegate：尽早把有界任务交给本地 dsh

## 何时委派（尽早路由）
- 在 GPT 动手前判断：目标清晰、边界明确、工作量明显超过一次简单编辑的任务（实现、重构、有界调查、批量转换、补测试、写文档），先委派给 dsh，不要等自己做完再"转交"。
- 留在本地：一两行的改动、已知答案的查询、需要反复追问才能定框的工作，以及需求定框、模型选择、最终验收这类只能由 Codex 负责的判断。
- 委派不是放弃责任：Codex 仍负责定框、给出验收标准，并在事后独立审查真实产物。

## 任务包（先写进 task 文件）
- 目标与背景：要解决什么、为什么、输入输出是什么。
- 工作目录：dsh 的 cwd，以及可用的运行/检查命令。
- 允许范围：可修改的文件或目录；明确列出非目标（不要做什么）。
- 验收标准：完成的可观察判据，以及需要运行的检查。
- 相关引用：关键文件路径、函数名、已知事实与坑。
- 给足上下文：不要塞无关对话历史或任何凭据；预期 1M 输入窗口，任务相关背景尽量给全，并允许 dsh 自行读取工作区文件。不要人为压缩输入；maxTokens 限制的是输出，别拿它省输入。

## 模型与 effort
- 按任务难度、所需能力选择模型，不从 Pro/Flash 名字推断强弱；用户指定当前最强为 v4.1 Flash，而非 v4 Pro，需要最强能力时优先选择它。
- 当前默认 provider=deepseek-official、model=deepseek-flash、reasoningEffort=max。本机 2026-09-12 目录将 deepseek-flash 标为 DeepSeek-V41-Flash，默认 contextWindow=1,000,000。版本变化时核对实际目录，不臆造模型 ID。
- 每次委派默认 max effort，除非用户另行指定；出错时如实上报，禁止静默降级模型或 effort。

## 调用方式
```
cd <本技能目录>   # 安装后为 ~/.codex/skills/deepseek-delegate
node scripts/run.mjs --cwd <工作目录> --task-file <任务文件> \
  [--model <id>] [--provider <id>] [--effort <name>] [--timeout <秒>] [--log-dir <目录>]
```
- 小任务文件原文通过 argv 传入；超过 32KB 时传文件路径让 dsh 读取，避开系统命令行长度限制，任务结束前保留文件。大量背景也通过文件引用提供。
- 脚本不经 shell，stdout/stderr 写私有日志，只回传紧凑 JSON：status、exitCode、耗时、请求的 model/effort、日志路径、最多 6000 字符的 finalText 与截断标志；status 不是 ok 时脚本也返回非零退出码。配置覆盖不修改全局设置；自定义 settings 路径的部署须先适配脚本读取路径。
- 冗长输出留在日志里，不要把 stderr 或推理过程整段带回 GPT；需要时再读日志文件。

## 边界、重试与隔离
- 工作目录不是沙箱：必须在任务包里写清允许范围；需要更强隔离时给 dsh 独立工作区（如 git worktree）。
- Codex 与 dsh 不要同时编辑同一批文件；委派进行中本地只读或改其他部分。
- 失败时最多再委派一到两次，且必须带上新信息（错误原文、缺失上下文、更窄范围）；不要原样重复提交。
- 不自动对外发消息、不发布、不提权、不改全局 dsh 或 Codex 设置。

## Codex 必须做的独立审查（不可跳过）
- dsh 退出码 0 只说明代理跑完了，不代表任务正确。
- 查看真实产物：git diff、新增/删除的文件、命令输出；自己运行相关检查（测试、构建、lint 或针对性复现），不要只信它的自述。
- 逐条核对验收标准，说明支撑证据与实际限制；证据不足时不得宣称完成。
