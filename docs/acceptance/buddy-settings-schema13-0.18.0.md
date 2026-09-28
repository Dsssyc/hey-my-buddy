# 0.18.0 源码：共享 skill、Buddy 配置与 schema 13

本记录覆盖 2026-09-28 在 `socu/shared-skill` 与其后续分支上完成、并快进合并到 `socu/buddy-core` 的源码工作，以及同日以 Claude Code 作为 Host 试用 buddy 的观察。安装另见 [installed-0.18.0.md](installed-0.18.0.md)。

## 范围

- ADR-015 共享 skill 分发：`8631f85`（决定）至 `0c571b9`、`08f2ce8`（本地临时目录改为 `tmp/`）。
- Buddy 配置设计与数据契约：`625bb5e` 至 `6e10cd7`，决定记录在 [buddy-settings.md](../design/buddy-settings.md)。
- schema 13 后端：`ed5650b`、`d1b8522`、`109ce5a`、`7d7bb2d`。偏好改为家族默认加档位覆盖（含 `none`），备注改为家族级，所有读取方通过 `effective_preferences` 视图取得生效偏好；`upgrade` 在校验过的备份之后、独占锁下单事务把 schema 12 迁移到 13，并校验迁移范围之外每张表的指纹；备份校验会在试验副本上验证迁移；`board_prepare` 接着执行同一迁移。启动时仍不做任何迁移。
- 控制台：`6230316`、`0673229`、`58e9666`、`a00b806`，导航改为 委派记录 / Buddy 配置 / 设置。
- 时间轴修复：`0ad0e72`。等待 Host 的运行（任务状态仍为 queued）不再同时画出未结束的排队段，排队段的斜纹曾盖住"等待 Host"标签；被取消请求的等待段以记录的取消时间结束；取消纹理只用于执行段。
- 文档同步：`b8029c6`、`a00b806`；[ADR-017](../decisions/017-local-installation-and-harness-discovery.md) 为提议，未实现（`e61fa0b`）。

## 委派记录

全部委派都通过日常 0.17.0 服务，属于工作目标 `obj-aa246f16-a324-4e22-b630-38e0d5fb9d46`。

| run | 配置 | 内容 | 结果与 Host 处理 |
| --- | --- | --- | --- |
| `a461cf03` | Claude Opus 5.5 high | 控制台重构 | 27 分钟、241 轮后因 Claude 五小时额度耗尽失败，未封存；工作区未提交的改动由 Host 提取为 WIP `6230316` |
| `a2a230da` | DeepSeek Flash max | 测试迁移到 schema 13 | 189 项中 188 项通过后请求 Host 修复 `migrations.py` 的一行；Host 以 `109ce5a` 修复，补丁整合为 `7d7bb2d` 并登记整合，随后取消该 run（本应回复请求、让它交付后再验收） |
| `5679034b` | Claude Sonnet 5 medium | 续做控制台 | 369 轮后以 `background-work-unsettled` 失败，五小时额度 98%；输出由 Host 验证后整合为 `0673229`，无法登记整合 |
| `ab917e53` | DeepSeek Flash max | 按需求审查并补齐控制台 | 已验收，`58e9666` |
| `15e52c96` | DeepSeek Flash max | 文档同步 | 已验收，`b8029c6`；Host 只检查了旧名称残留 |
| `4d7377ef` | DeepSeek Flash max | 已确认决定与文档修正 | 已验收，`a00b806` |
| `dee9e4ad` | DeepSeek Flash max | ADR-017 草稿 | 用户暂停讨论后由 Host 取消，已确认停止，未产生改动；ADR 最终由 Host 起草 |

三次向 Codex GPT-6 Sol 提交都在受理时被拒绝（`ADAPTER_UNAVAILABLE`：Codex closed before the request settled）。服务与 Worker 的 PATH 不含 nvm 目录，Claude Code 会话 PATH 优先的 node 22 下 codex 缺少原生依赖包；用户终端的 node 24 下 codex 0.157.0 可以正常握手。这是 ADR-017 的背景之一。已验收 run 的受管检出已通过 `workspace-cleanup-apply` 删除；`a461cf03`、`a2a230da`、`5679034b`、`dee9e4ad` 未验收，官方清理与存储回收都拒绝删除它们的检出（`not-accepted`），保留在 `~/.local/share/hey-my-buddy/state/workspaces/`。

## 验证

- 迁移与升级：`test_migrations`（7 项，含迁移后形状与全新 schema 13 一致）、`test_upgrade_migration`（5 项：备份记录 schema 12 并验证可迁移、迁移范围外指纹不变、越界修改被拒、恢复回 schema 12、按被验证运行时的 schema 校验）。
- 完整 Python 测试：在 `6230316` 上 1252 项通过（之后没有后端改动）。
- 控制台（`a00b806`）：`tsc --noEmit` 通过，vitest 41 个文件 505 项通过，`vite build` 重新构建的产物与提交内容逐字节一致。
- 完整检查 `uv run --frozen python -m buddy.checks`（`e61fa0b`，私有状态与运行时根目录）：退出码 0；Python 1252 项通过，Node 139 项通过、0 失败。

## 以 Claude Code 为 Host 的试用观察

- 0.17 只有 Codex 插件，Claude Code 要到 Codex 插件缓存里找 SKILL.md 和启动器；0.18 共享 skill 解决此问题。SKILL.md 约 12.9 KB，混有版本说明，其中"未安装"的描述在安装后已经过时。
- 没有经过验证的 Router，默认路由的委派都会停在 Host 边界，skill 没有告诉 Host 如何提前判断。
- 启动器每次调用都经过 `uv run`，Claude Code 沙盒不允许写 uv 缓存，每条 buddy 命令都要在沙盒外执行；会话还带入 `BUDDY_*` 与 `ANTHROPIC_BASE_URL`，每次都要手动清除。
- 单个 JSON 参数不便传递长任务文本；没有按方法的参数帮助；后续委派加入同一工作目标需要手动复制 `objectiveId`。
- 后台 `await` 与 Claude Code 的完成通知配合良好，多个委派可以并行，Host 同时推进后端。
- Claude worker 消耗额度很快（见委派记录），用户随后要求本议程不再使用 Claude worker。
- 被额度中断的 worker 以 failed 结束且没有封存成果，未提交的改动只留在受管检出里；显式配置是硬约束，`continue` 不能改用其他模型（路由得出的配置可以，run `6e072cc5` 即如此），只能另开任务；`acknowledge` 不接受 failed 的任务；`integration-record` 的 `adjustedPaths` 只能是成果内的路径。
- 恢复后的 Claude Code 会话被分到新的 worktree，钩子禁止写入旧 worktree，分支只能改指向后继续。

## 未完成与限制

- 所有 adapter 的 `read_only_structured_verified` 仍为 false，没有可用的 Router；[0.17.0 源码记录](router-read-only-routing-0.17.0.md)中的原生探针仍未执行，每次执行都需要用户单独授权。Claude 探针因用户暂停 Claude 使用而搁置，Codex 探针先要解决服务侧找不到可用 codex 的问题。
- 上一会话的 worktree `.claude/worktrees/skill-windows-adaptation-1d9d2f` 留有两处未提交改动，与 `0ad0e72` 中的文件逐字节相同，可以丢弃。
- `b8029c6` 的文档内容只做了旧名称残留检查，没有逐段审阅。
