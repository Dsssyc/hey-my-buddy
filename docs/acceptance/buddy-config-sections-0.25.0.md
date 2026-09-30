# Buddy 配置分区导航（0.25.0）

状态：源码验证通过，等待 Host 合并与用户决定安装。

2026-09-30，以 `socu/buddy-core` 的 `58f1cdd250ab9bf8d90ba3b70f680975f050552d` 为基线，在独立分支 `socu/buddy-config-sections` 与 worktree 实现“① 配置页结构”。版本与契约升为 `0.25.0`，schema 仍为 15。修改范围为控制台、相关文档及测试，另同步项目版本、锁文件与契约版本断言，并隔离阻塞全量验证的 ZCode 测试夹具身份。

## 行为

Buddy 配置默认进入“模型”，左侧以图标与标签导航“模型 / Router / Harness”；760 px 及以下切换为顶部横排。三个分区使用固定工作台剩余高度，模型列表与详情各自滚动，Router 与 Harness 在分区内滚动。各分区保持挂载，模型选择、筛选、草稿与未完成操作在切换时保留。

顶部始终显示一条全局状态，含快速／审阅 Router 状态、可用 Harness 数与有效额度观测提醒。审阅 Router 待验证入口直接打开并聚焦 Codex；额度提醒直接打开并聚焦对应 Harness；配置需处理和“查看所在家族”可跨分区选择家族、穿透隐藏该条目的筛选并聚焦档位。分区、Router 条目、模型档位与 Harness 行均支持 URL 深链、刷新、hashchange 与浏览器前进／返回，旧 `#models`、`#settings` 进入模型分区。

审阅能力行的状态、帮助按钮、标签与下拉框、操作按钮统一为 32 px，并纵向居中；“验证配置”与下拉框同一行。模型偏好、并发、Harness 操作等同类行也检查并对齐。窄屏按整组换行。Harness 行在 `HarnessStatus.tsx` 中保留 `harness-account-controls` 插入位置，当前不显示占位文字。新增可见文字使用短状态与处理入口，遵守文案审查开头的 Host 意见。

## 验证

使用本机已有 Node `v24.21.0`。Host 整合后的前端全套 `npm --prefix apps/console test`：52 个文件、616 项通过；TypeScript `npm --prefix apps/console run typecheck` 退出码 0。新增 15 项真实组件回归测试覆盖默认分区、切换、草稿与选择保留、全局状态、提醒跳转、深链、旧书签、返回、穿透筛选及 390 px 下切换。窄屏单元测试核对组件行为，几何由真实浏览器验证。

用 `tests/probes/objective_console_preview.py` 与构建资产启动独立端口的合成预览，在真实 Chrome 中核对三个分区；HTTP API 请求均返回 200，页面无控制台错误或警告。三种尺寸分别捕获模型、Router、Harness 截图，加入图标后的最终矩阵共 9 项。审阅行状态、帮助、标签／下拉框与按钮在各尺寸均为 32 px；桌面同一行中心一致，390 px 换行后同一行中心一致。

| 视口 | 三个分区高度 | document scrollWidth / clientWidth | 导航 |
| --- | --- | --- | --- |
| 1280 × 720 | 588 px | 1280 / 1280 | 左侧竖排 |
| 1440 × 900 | 768 px | 1440 / 1440 | 左侧竖排 |
| 390 × 844 | 624.52 px | 390 / 390 | 顶部横排 |

分区自身也无横向溢出。浏览器额外核对了 Harness／模型深链刷新后的焦点、键盘 Enter、前进／返回、旧书签；390 px 下的模型深链刷新仍为 390 / 390。原始记录在忽略的 `tmp/config-sections/`，截图在忽略的 `output/playwright/config-sections/`；合成预览与本次独立浏览器会话已关闭。

收尾先执行 `npm --prefix apps/console ci`，再执行 `uv run --frozen python -m buddy.checks`。首轮 1,724 项 Python 检查只有旧契约版本断言与共享 skill 4 KB 上限两项失败；契约断言同步至 0.25.0，共享 skill 入口精简为 4,086 字节。相关 6 项 current-core 与 10 项 skill 检查均通过，首轮私有测试根已由检查器清理。第二轮 1,724 项仅 ZCode 的最终回执后询问测试失败。两个独立私有夹具均使用 `attempt-1`，短路径 socket 回退到同一位置；同时启动两个真实 fixture runner 可稳定复现认证拒绝。测试夹具的 attempt 身份改为带各自临时根名称的稳定身份，相关断言随 context 绑定；适配器实现未修改。97 项相关检查与新增双实例回归通过，恢复原固定身份时新增回归稳定失败，隔离后通过。最终 `uv run --frozen python -m buddy.checks` 退出码 0：1,725 项 Python 检查通过（1,266.053 秒），161 项 DSH 检查通过（27.965 秒）；最终私有根目录经检查器清理并核对不存在。打包资产在最终检查后再次构建，构建结果与指纹见下文。

最终 `npm --prefix apps/console run build` 退出码 0；`src/buddy/console_assets` 中的 `index-JiH63Qwo.js`、`index-Djtx77yz.css`、入口 HTML 和图标均与真实浏览器验证资产的 SHA-256 完全一致。Vite 仍提示主 JS chunk 超过 500 kB（当前约 514 kB），属于现有打包体积提示，构建成功。

## Buddy 委派与整合

组件测试委派 run `88a75358-6d89-41a6-8d8a-3a53a10ebb44`，objective `obj-c246ce0e-5162-48ab-9162-e1b332d4b8f1`，decision `dec-753d8736-a513-4504-a5c6-6050f0f983de`。未指定部分配置字段，使用任务级 `routingPreferences` 表达轻量测试偏好；该偏好没有合法匹配，快速 Router 选择 ZCode / `zai-api` / `GLM-5.3-Flash` / `max`。Worker 仅改 4 个测试文件，已确认自身与后代停止。

Host 审查固定产物 `fd6fb964-f149-4147-887c-def7ddf8b03d`，以输入 `763f3d720e8a59ab14260c2e893d39ec34c434a2` 到封存输出 `f78d1598d4c664701596a8f2812236ac6b90c476` 的补丁整合，使用当前最终实现重新跑过 616 项前端测试。整合证明、acknowledge 与 Worker checkout 回收将在最终提交后记录。

已从 `docs/design/backlog.md` 的“控制台”节删除已完成的“Buddy 配置页改为分区导航”与“审阅能力一行的控件高度与位置不齐”两条。其余积压项由对应会话处理。

## 合并交接

建议 Host 将本分支整体合入 `socu/buddy-core`，然后统一②③的版本及重建资产。Harness 列表从 `BuddyConfig.tsx` 提取到 `HarnessStatus.tsx`；后续账户／额度入口接在该组件的行与预留位置。②可能新增的 Router、额度恢复与审阅入口须接到当前分区导航及相应目标；Host 按最终数据与操作合并 `BuddyStatusBar.tsx` 的状态入口。生成的 `console_assets` 由最终合并源重建，避免手工拼接构建文件。

测试与预览使用私有 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`，全量检查另外创建并验证自己的私有根目录；本次没有打开日常控制台、直接读写日常状态文件、安装产品或修改用户配置。日常服务仅用于按授权承接本次 Buddy 委派。未创建 Claude Worker。本记录是源码与合成预览验收；合并与安装等待用户决定。
