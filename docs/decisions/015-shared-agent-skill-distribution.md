# ADR-015：以 `.agents` 公共 skill 分发，退役 Codex 插件

## 状态

已接受：用户于 2026-09-28 批准 ADR-012 提案中"单一全局 CLI 加单一 skill 取代按 Host 维护的插件"的方向，并确定了本文的三项选择。0.18.0 源码实现本决定，日常安装需另行授权。本决定取代 [ADR-007](007-neutral-core-and-single-current-contract.md) 中"只通过插件分发 skill、只有插件内的 `bin/buddy` 一个启动器"的规定；ADR-007 的其他内容不变。

后续扩展（实施边界，见文末）：[ADR-017](017-local-installation-and-harness-discovery.md) 于同日接受，扩展了本文的安装入口、启动器、版本切换与 harness 发现。当前源码候选为 0.19.0/contract 0.19.0/schema 14，日常已安装版本为 [0.18.0/contract 0.18.0/schema 13](../acceptance/installed-0.18.0.md)；安装入口、启动器与发现以 ADR-017 和现行参考文档为准，本文以下原文保留 0.18.0 时的决定记录。

## 背景

目前 Host 只能通过 Codex 插件拿到 `$buddy` skill 和 `bin/buddy` 启动器，Claude Code 没有对应入口。`SKILL.md` 链接仓库里的 `docs/reference/...`，并从插件根目录解析启动器，离开插件就无法使用。真正的服务、运行时、状态和备份一直在 `~/.local/share/hey-my-buddy`，与插件无关。

官方文档核实（2026-09-28）：Codex 从用户级 `~/.agents/skills` 读取 skill 并跟随符号链接；Claude Code 从 `~/.claude/skills` 读取，并支持指向其他位置的 skill 目录符号链接。[Agent Skills 规范](https://agentskills.io/specification)允许 skill 目录包含 `scripts/`（可执行代码）、`references/`（按需加载的文档）和 `assets/`，`SKILL.md` 以相对路径引用它们，`name` 必须与目录名一致。

## 决定

1. **唯一正本在 `~/.agents/skills/buddy/`。** 目录包含 `SKILL.md`、`scripts/buddy`（全局 CLI）和 `references/`（从仓库参考文档生成的精简副本）。skill 自包含，不再引用仓库路径或插件根目录。
2. **CLI 放在 skill 目录内。** `scripts/buddy` 是指向稳定运行时的薄启动器，本身不含运行时；`SKILL.md` 用绝对路径 `~/.agents/skills/buddy/scripts/buddy` 调用，不依赖 PATH。需要在终端手动使用时，可另外建一个指向它的 `~/.local/bin/buddy` 链接，但不是必需的。
3. **Claude Code 通过符号链接接入。** `~/.claude/skills/buddy` 指向 `~/.agents/skills/buddy`，不产生第二份副本。不做复制回退：链接建不成、或该位置已被其他目录或其他 skill 占用时，`install` 直接报错并停在升级服务之前，避免第二份副本与正本悄悄不一致（2026-09-28 用户决定）。Windows 上需开启开发者模式；是否改用目录联接留待真机验证。
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
- Windows 适配时，`scripts/` 需要提供 `buddy.cmd` 或 `buddy.ps1`；Claude Code 入口仍只用链接，不复制。
- 真实安装、卸载插件和日常升级仍需用户单独授权。

## 实施边界（ADR-017 扩展）

本节只记录边界，不修改以上历史内容。[ADR-017](017-local-installation-and-harness-discovery.md) 已扩展本文的安装、启动器与发现约定；现行步骤和稳定错误码由 [operations.md](../reference/operations.md) 拥有，harness 发现与健康由 [harnesses.md](../reference/harnesses.md) 拥有：

- 安装入口改为一条固定版本的发布包命令：发布后为 `uvx hey-my-buddy@<版本> install`（包名可用性与是否发布到 PyPI 仍待用户决定，未发布前不可声称可从 PyPI 安装）；未发布时用本地构建的 wheel，例如 `uvx --from /绝对路径/hey_my_buddy-0.19.0-py3-none-any.whl hey-my-buddy install`。`install.sh`/`install.ps1` 只准备固定私有 uv 后调用该入口，不改 PATH、shell 或 Host 设置，也不安装全局 `buddy` 命令。
- `scripts/buddy` 的日常命令直接执行 `active-runtime.json` 指向运行时的 Python，不再经过 uv；uv 只用于安装与升级，以及显式 `BUDDY_DEV_SOURCE=1` 的源码开发。
- `install` 先列出将写入的路径、物化版本化运行时；忙时以 `UPGRADE_NOT_IDLE` 拒绝并列出任务，空闲时整体切换 skill、启动器、运行时与服务，并保留唯一一份校验过的滚动备份用于回滚；完整同版本可重跑且不重启，损坏内容可用同一固定版本命令修复。
- 沙盒不能写状态目录或建立本地通信时，启动器返回 `LAUNCH_ACCESS_DENIED`，而不是拉起继承沙盒限制的残缺服务；Host 入口清除残留的服务内部身份变量与第三方模型端点变量，用户开发变量照常生效。
- harness 发现、健康缓存、手动路径与重新检测改由服务负责，每个 harness 只记录一条健康记录；健康未通过即从可用性与路由候选中排除，新发现的模型默认不启用。每个 adapter 的 `read_only_structured_verified` 仍为 false，验证状态以新的 [local harness discovery 0.19.0 记录](../acceptance/local-harness-discovery-0.19.0.md)为准。
- 第 1–4 条（正本位置、CLI 位置、Claude Code 链接、只安装一次）与第 6–8 条（数据位置、插件退役、不设项目级 skill）继续有效；第 5 条"由 `install` 更新、由 `upgrade` 完成空闲检查、备份、切换与校验"仍然成立，但入口与执行顺序以本节为准。

当前源码候选是 0.19.0/contract 0.19.0/schema 14，日常已安装版本仍是 0.18.0/contract 0.18.0/schema 13；真实安装、Board 迁移、发布到 PyPI 与日常升级都需要用户单独授权。Windows 只有代码与脚本可移植，真机未验证。
