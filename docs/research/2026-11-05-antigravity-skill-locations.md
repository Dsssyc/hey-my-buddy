# Antigravity 与 Gemini CLI 从哪里读用户级的 skill

## 问题

开发者 2026-11-05 发现 Antigravity 不读 `~/.agents/skills` 下的 skill。安装时要不要像给 Claude Code 那样，在 Antigravity 读 skill 的目录里放一个指向共享 skill 的链接；放在哪里，以什么为条件。服务于 [ADR-015](../decisions/015-shared-agent-skill-distribution.md)（共享 skill 是唯一的分发方式）。

## 看了什么

- Antigravity 的公开文档 [Agent skills](https://antigravity.google/docs/skills)：页面没有版本与日期，2026-11-05 读了全页。
- Gemini CLI 的公开文档 [Managing Agent Skills](https://geminicli.com/docs/cli/using-agent-skills/)：页面写着 2026-04-30 更新，2026-11-05 读了全页。
- 第三方的安装指南与 Google 的 codelab 只看了搜索结果的摘要。
- 没有读任何源码，没有在 Antigravity 上做实验，也没有看开发者机器上的 `~/.gemini`。

## 看到的事实

- **Antigravity 文档自述：**
  - App、CLI 与 IDE 都从 `~/.gemini/config/skills/<skill 目录>/` 读全局的 skill。
  - IDE 另外仍支持旧的 `~/.gemini/antigravity/skills/`。
  - Antigravity CLI 还读 `~/.gemini/antigravity-cli/skills/` 与 `~/.gemini/skills/`。
  - 工作区的 skill 在 `<工作区根>/.agents/skills/`，App 与 CLI 还逐级往上找到仓库根；旧的 `.agent/skills` 为向后兼容仍然可用。
- **Antigravity 文档没有说的：** 全页没有提到 `~/.agents/skills`，与开发者的观察一致（开发者在 Antigravity App 2.29.0，build 1013060670 上观察到的）。页面也没有说是否跟随符号链接或 Windows 的目录联接。
- **Gemini CLI 文档自述：** 用户级的 skill 在 `~/.gemini/skills/`，`~/.agents/skills/` 是同一层级的别名，所以 Gemini CLI 不需要另放链接。页面的横幅说，2026-06-18 起，免费档与 Google One 用户的 Gemini CLI 已由 Antigravity CLI 取代。
- **二手材料：** 第三方指南大多写 `~/.gemini/antigravity/skills/`，也有别的写法，与官方文档现在的路径不一致，说明这个位置随版本变过。
- **我们的推断：** `~/.gemini` 由 Gemini CLI 与 Antigravity 共用，有这个目录不等于装了 Antigravity。

## 取了什么，没取什么

- **取：** 链接放在官方文档现在的路径 `~/.gemini/config/skills/hey-my-buddy`，Antigravity 的三种形态都读它。旧路径与 CLI 专用的路径不放，免得同一个 skill 出现两次。
- **不取：** 不给 Gemini CLI 放链接，它已经读 `~/.agents/skills`。
- **之后核实的：** 2026-11-05 开发者在 macOS 上手动放了 `~/.gemini/config/skills/hey-my-buddy` 这个符号链接，Antigravity App 2.29.0（build 1013060670）随即能调用这个 skill。Windows 的目录联接还没有验证。
- **定下的条件：** `~/.gemini/antigravity` 或 `~/.gemini/antigravity-cli` 存在时才放链接（见待办里的那一条）。

## 时效

这份记录在以下情形下过时：

- Antigravity 改了全局 skill 的位置，或者开始读 `~/.agents/skills`；
- Gemini CLI 去掉了 `~/.agents/skills` 这个别名。
