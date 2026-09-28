# ADR-015：以 `.agents` 公共 skill 分发，退役 Codex 插件

## 状态

已接受：用户于 2026-09-28 批准 ADR-012 提案中"单一全局 CLI 加单一 skill 取代按 Host 维护的插件"的方向，并确定了本文的三项选择。0.18.0 源码实现本决定，日常安装需另行授权。本决定取代 [ADR-007](007-neutral-core-and-single-current-contract.md) 中"只通过插件分发 skill、只有插件内的 `bin/buddy` 一个启动器"的规定；ADR-007 的其他内容不变。

## 背景

目前 Host 只能通过 Codex 插件拿到 `$buddy` skill 和 `bin/buddy` 启动器，Claude Code 没有对应入口。`SKILL.md` 链接仓库里的 `docs/reference/...`，并从插件根目录解析启动器，离开插件就无法使用。真正的服务、运行时、状态和备份一直在 `~/.local/share/hey-my-buddy`，与插件无关。

官方文档核实（2026-09-28）：Codex 从用户级 `~/.agents/skills` 读取 skill 并跟随符号链接；Claude Code 从 `~/.claude/skills` 读取，并支持指向其他位置的 skill 目录符号链接。[Agent Skills 规范](https://agentskills.io/specification)允许 skill 目录包含 `scripts/`（可执行代码）、`references/`（按需加载的文档）和 `assets/`，`SKILL.md` 以相对路径引用它们，`name` 必须与目录名一致。

## 决定

1. **唯一正本在 `~/.agents/skills/buddy/`。** 目录包含 `SKILL.md`、`scripts/buddy`（全局 CLI）和 `references/`（从仓库参考文档生成的精简副本）。skill 自包含，不再引用仓库路径或插件根目录。
2. **CLI 放在 skill 目录内。** `scripts/buddy` 是指向稳定运行时的薄启动器，本身不含运行时；`SKILL.md` 用绝对路径 `~/.agents/skills/buddy/scripts/buddy` 调用，不依赖 PATH。需要在终端手动使用时，可另外建一个指向它的 `~/.local/bin/buddy` 链接，但不是必需的。
3. **Claude Code 通过符号链接接入。** `~/.claude/skills/buddy` 指向 `~/.agents/skills/buddy`，不产生第二份副本。Windows 无法创建符号链接时改为复制，并由版本号保证两份一致。
4. **只安装一次。** 首次安装由 `buddy install` 完成：安装稳定运行时、写入公共 skill、建立 Claude Code 链接。安装器先检查 `~/.agents/skills/buddy` 与其中的版本标记，版本相同则只补齐缺失的链接，不重复安装；多个 Host 并发执行时用锁文件串行化。
5. **新版本也用 `install` 安装。** 新包的 `scripts/buddy install` 把新版 skill 写入临时目录后按重命名替换，刷新链接，再在服务运行时调用已安装 skill 的 `upgrade`，由它完成现有的空闲检查、滚动备份、服务切换和校验；正在读取旧 skill 的 agent 不会读到写了一半的文件。`install` 的输出报告 skill 版本、contract 和放置结果。`upgrade` 本身仍只切换服务，中断后可单独重跑恢复。
6. **运行时与数据位置不变。** 运行时、状态、备份继续放在 `~/.local/share/hey-my-buddy`，它们是数据而非用户直接使用的入口。
7. **插件直接退役。** 新方案安装并验证后卸载 `hey-my-buddy@personal`，不保留过渡版本，避免 Codex 同时出现两个 `buddy` skill。仓库不再发布插件清单、插件市场目录和 `packaging/stage-plugin.py` 的插件产物，打包改为产出可安装的 skill 目录。
8. **不设项目级 skill。** 各仓库不放 `.agents/skills/buddy`，只使用用户级这一份。

## 考虑过的方案

- **CLI 放在 `~/.local/bin`，skill 另放。** 需要两处安装，桌面版 agent 的精简 PATH 里也不一定有 `~/.local/bin`；放进 skill 目录后一处即可判断是否已安装。
- **skill 放在 `~/.local/share`。** 位置过于隐蔽，两个 agent 都不会从那里发现 skill。
- **保留插件一个版本作为后备。** 两个同名 skill 会同时出现在 Codex 中，用户选择退役。
- **项目级 skill。** 与用户级重复，版本容易不一致。

## 影响

- `SKILL.md`、`references/` 与 CLI 改为从 skill 目录相对定位；AGENTS.md、两份 README、operations.md 的安装与升级说明按新方案改写，ADR-007 相关条款以本文为准。
- 需要新增 `buddy install`，并扩展 `buddy upgrade` 以更新 skill；安装与升级都要在私有根目录下测试，包括并发安装、已安装跳过、链接缺失补齐和升级中断恢复。
- Windows 适配时，`scripts/` 需要提供 `buddy.cmd` 或 `buddy.ps1`，链接改为复制。
- 真实安装、卸载插件和日常升级仍需用户单独授权。
