# 控制台标识与 Lead 预览说明

## 执行计划（2026-10-15）

基点为 `31a2184f`，由整合 Host 授权本 Lead 在独立受管检出中完成四项控制台改动；最终验收由整合 Host 执行。先读过 backlog 四条原文、ADR-029、共享 skill 与 workflow 的 Lead、工作区和固定产物验收规则。

分成两个经默认路由的名下微任务，并行使用独立 worktree：微任务 A 负责标识显示与复制、命令执行隐藏不存在的 buddy、Lead 委派方，写入范围为 `apps/console/src/`；微任务 B 负责 `waiting-owned` Lead、时间轴字段投影与浏览器场景，写入范围为 `tests/probes/macro_task_console_preview.py`、`tests/python/cli/test_host_preview.py`、`apps/console/browser/lead.check.ts`，必要时修改现有 `apps/console/browser/actors.check.ts`。两者不修改同一文件；A 的新组件与测试在 src 内，B 的浏览器检查只依赖已有的 Lead 呈现和补充后的预览数据。

微任务 A 只跑所改组件及数据工具的定向 Vitest 和 i18n 检查，不跑前端全量、Python 或浏览器检查。微任务 B 只跑 `tests/python/cli/test_host_preview.py` 与自己的 Lead、actor 浏览器检查，先安装本检出的 console 开发依赖并将浏览器所需构建写入 ignored `tmp/lead-preview-assets`，在 ignored `tmp/` 中生成继承原配置的临时 Playwright 配置，将 webServer 的 assets 指向该目录、testDir 指回原 browser 目录，避免触碰打包资源。Python 必须经 Host 指定解释器运行检查器，采用默认进程数并清除继承的运行时、Worker 与代理凭据变量。

Lead 对每份交付读取固定 Git 产物的差异与范围，在该微任务检出中重跑受影响测试；范围内缺陷用 `continue` 退回。验证通过后将固定产物的差异合入本 Lead 的检出，以实际整合提交作为 `accept.target` 验证的目标；若产品拒绝该目标或整合流程无法完成，记录命令与输出并向 Host 请求处理，不绕过验收。

全部整合后依次运行 `npm --prefix apps/console ci`、前端全量测试、浏览器检查、重建 `src/hey_my_buddy/console/assets`、指定解释器的 `tests/python/cli/test_host_preview.py`；不跑 Python 完整检查，不安装产品，不改变日常服务。

## Lead 使用记录

角色说明给出了执行凭据文件；每次启动器调用显式设置 `HMB_AGENT_CREDENTIAL_FILE`，由启动器读取，不读取文件内容。`help submit` 成功返回参数规则；Lead 提交省略 `hostId`、owner 与宏任务字段。

当前 shell 的 Node.js 为 25.8.1，不在 console 声明的支持版本范围内；已找到现有 Node.js 24.21.0，前端命令统一将其 bin 放在 PATH 前端，尚未运行验证。

第一次提交命令形式为 `HMB_AGENT_CREDENTIAL_FILE=<本次角色给出的凭据文件> ~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy submit --params-file tmp/ui-submit.json`，第二次改用 `tmp/preview-submit.json`，两次退出码均为 0，没有补 `hostId`。回执的 `leadOwnership.leadRunId` 均为本 Lead 的 `9f8571e9-5983-4afa-9232-864a8d56282c`，状态为 `executing` / `queued`，原因 `awaiting-model-selection`；首次提交的凭据问题本轮未复现。A 的 runId 为 `79771ce9-890b-4cab-867b-4e05c68791da`，B 为 `97e3de5a-d4f5-4d15-ba79-a8362b8b8701`，buddy 尚待路由选出。两份提示均写明写入范围、限定检查、三句交付，以及 Host 的全部环境规则；没有授予继续委派权。

本轮提交两项后返回 `waiting`，等待产品的 `waiting-owned` 叫醒路径；后续回合验明投递与恢复上下文。

## 交付与验收

待名下微任务交回后记录固定产物、buddy、返修次数、审查与检查结果，以及验收和整合的实际结果。
