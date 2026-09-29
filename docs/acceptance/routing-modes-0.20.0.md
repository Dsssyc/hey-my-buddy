# ADR-018 第一部分：路由双模式，0.20.0 源码验收

日期：2026-09-29。范围是 [ADR-018](../decisions/018-routing-modes-and-host-workflow.md) 第 1–10 条，基线为 `socu/buddy-core` 的 `157e7e15cc8de10b012eb172de90efa119c15cdc`，集成分支为 `socu/routing-modes`。契约与包版本为 0.20.0，schema 保持 14；配置迁移只使用 `meta`，没有 schema 变更。第二部分的 Host 工作流、第 11–23 条及其列出的独立缺陷不属于本次实现。日常安装仍为 0.19.0/schema 14，未合并、未安装、未发布，未修改用户配置。

## 已实现的行为

快速路由由各 harness 关闭工具，并由程序审计原生事件流；工具事件、子代理事件、未绑定或不完整事件、晚到的工具调用都不能生成有效答案。所有原生调用结束并确认退出后，才能写入 `zeroToolVerified: true` 与精确整数 `toolCalls: 0`，服务在发布时再次验证。DSH 通过原生 LLM 接口传入空工具列表；ZCode 用空 `toolAllowlist` 并订阅原生会话事件；Codex 使用私有配置、受限模型目录和空原生环境。快速路径的工作目录为空且位于仓库之外；不传仓库或历史执行证据，只允许卡片、偏好、备注引用。一次格式纠正共用 60 秒截止时间，越界候选不纠正。

审阅路由保留已验证的只读沙盒、冻结输入副本及调用后校验。预算接口为 `brief`、`standard`、`deep`，显示为“简要 / 标准 / 深入”；旧 `quick` 在升级时映射为 `brief`。读取字节上限继续保存在预算中，但当前原生 adapter 无可用读取字节计数，不能声称该上限已执行。

配置使用快速、审阅两个 Router 位置、默认模式及审阅预算。新安装默认 `fast`，两个位置均等待用户选择。升级按原 Router 的资格优先保留在审阅位置，否则放入快速位置，并保留相应默认模式；另一个位置留空，控制台提示选择。升级在已有备份和空闲检查内写入 `meta`，测试验证其他表及无关 `meta` 不变。提交支持 `routingMode` 与默认开启的 `allowRoutingFallback`；审阅不可用时记录请求模式、实际模式、降级代码与原因，关闭降级则进入 Host 边界。认领和启动前再次检查资格；只在模型尚未开始且进程确认停止时，将审阅尝试重新排队为快速尝试，保留冻结候选和实际模型家族配额。

控制台分别显示两个 Router、默认模式与审阅预算；档位菜单按实际能力开放快速或审阅位置。时间轴以竖线和点纹区分快速与审阅路由，并显示降级标记；路由依据展示请求模式、实际模式及降级原因。设置明确告知任务描述会发送给快速 Router 的提供方。旧路由按历史审阅记录显示。CLI、decision、evaluation、architecture、共享 SKILL 与两份 README 已同步。

## 自动与浏览器验证

最终在私有 `BUDDY_STATE_DIR` / `BUDDY_RUNTIME_ROOT` 下运行 `uv run --frozen python -m buddy.checks`，退出码为 0：1,404 个 Python 测试全部通过（1,351.372 秒），142 个 Node 测试全部通过。运行前清除继承的 runtime、Worker、agent、harness record 与虚拟环境变量。原始日志为忽略的 `tmp/acceptance/buddy-checks-frozen.log`，SHA-256 为 `0d79f57ce4accadd8c71958f3a5671187f9d559a6089370ad0663e837cfac5fc`。早期全量检查发现 capability 读路径没有绑定当前黑板的 harness 健康缓存，导致私有测试观察到机器上的实际 harness；该问题已修复，最终检查通过。

最终控制台执行 `npm test`：44 个文件、548 个测试全部通过；`npm run build` 的 TypeScript 检查及生产构建通过，生成资源已同步到 `src/buddy/console_assets/`。合成预览 `python3 tests/probes/objective_console_preview.py --check` 通过。新增与调整的测试覆盖各原生事件族的零工具拒绝、缺失证明和布尔伪零、一次格式纠正与禁止越界重试、截断/超时/取消/关闭后工具事件、配置迁移、默认与指定模式、降级关闭、认领后的版本变化、正确模型家族配额、服务发布与控制台。

浏览器只访问本次私有合成预览，不连接日常黑板。在 320、768、1440 像素宽度检查布局，未发现横向溢出；核对双 Router 状态、默认模式、预算名称和任务描述发送提示。实际双击快速降级块可打开对应的路由依据，展示“请求审阅 / 实际快速 / 降级原因”；短块的最小点击区域已提到后续执行条之上，避免打开错误详情。浏览器没有 error/warn 日志。测试后恢复视口并停止本次预览进程。

## 逐次授权的真实提供方检查

每一行都单独获得用户授权，输入仅为“修正 README 一处标点”的任务和两个候选配置，只选择配置，不执行文件修改。所有会话、state、runtime 和输出均在本次私有目录；没有 Claude 调用。真实结果的可提交摘要见 [原生检查数据](evidence/adr018-fast-routing-native.json)，原始日志保留在被忽略的 `tmp/acceptance-fast-*/`。

| Harness / 次数 | 耗时 | modelStarted | 结果 |
| --- | --- | --- | --- |
| DSH / 1 | 0.294 秒 | false | 未通过私有配置解析；原生 YAML 字段顺序处理已修复 |
| DSH / 2 | 1.377 秒 | true | 原生调用失败；离线重现凭据初始化竞态后修复 |
| DSH / 3 | 2.28 秒 | true | 通过；0 次工具调用、0 次格式纠正 |
| ZCODE / 1 | 3.368 秒 | false | 拒绝未识别的启动通知；模型回合未开始 |
| ZCODE / 2 | 3.836 秒 | true | 拒绝未识别的进程遥测通知 |
| ZCODE / 3 | 3.582 秒 | true | 拒绝未识别的 computer-use 生命周期通知 |
| ZCODE / 4 | 5.727 秒 | true | 提供方 429 / rate_limited（原生 code 1310）；未取得有效路由答案 |

DSH 第 3 次成功选择 `dsh / deepseek-official / deepseek-flash / off`，引用 `routingPreferences[0]`，未触发格式纠正；原生报告输入 592 token、输出 104 token、合计 696 token，108 个流块、工具 schema 数为 0、普通 agent runner 已禁用。所有已结束的真实检查均确认原生进程退出。失败记录中的 `modelStarted` 表示控制器是否已进入模型回合阶段，不等于付费账单证据；本次未核验金额，不将未知费用记为零。

## 原生离线验证与修复

使用真实安装的 DSH 与本机假 DeepSeek SSE，伪造 API key，并通过 macOS sandbox 限定唯一 loopback 端口；实际验证外网和另一端口均返回 `EPERM`。原生请求不含工具，生产调用成功，`zeroToolVerified=true`、工具调用数为 0。该探测重现了凭据加载竞态，并验证插件同时依赖 `llm` 与 `credentials` 的修复；没有读取或使用真实凭据。

使用真实 ZCode CLI 0.16.9、本机假 OpenAI SSE 和相同网络隔离，原生请求 `tools=[]`。生产 `zcode_runner.run` 全路径得到合法答案、零工具证明、8 个 canonical 会话事件、已确认关闭和流 EOF。独立原始事件重放也通过。修复包含遗漏的 `session/subscribe`、普通生命周期与遥测事件的精确处理，以及重复 `session/close`；工具、权限、子代理和工作流事件仍一律拒绝，未知事件不能用于零工具证明。投影事件先于 canonical 事件，序号空间独立，不能代替绑定 inputId 的完成事件。

使用真实 Codex CLI 0.157.0、公开模型元数据和本机假 Responses 后端，使用伪造 API key，原生请求未携带顶层 `tools`，`input.additional_tools.tools=[]`。完整回合完成，真实原生事件经生产观察器重放无错误；无技能指令进入模型输入。耗时 8.527 秒，只有 1 个包含 JSON 的本机 Responses 请求；其他 7 个为未携带 JSON 的 WebSocket 升级请求，全部使用伪造 Bearer，拒绝代理没有收到外部请求。该检查验证了原生工具构造和事件处理，不代表真实 ChatGPT 账户路径的付费验收。

离线脚本和原始报告按仓库规则留在忽略的 `tmp/dsh-native-offline/`、`tmp/zcode-native-offline/`、`tmp/codex-native-offline/`；提交的摘要不包含凭据、提供方配置快照、原生请求头或推理内容。

## 未验证项与后续边界

ZCode 的真实提供方验收被第 4 次检查的 429 限流阻断；恢复后若需再次实测，仍须逐次授权。其原生离线零工具能力已验证，不能将此写成真实提供方成功。Codex 快速路由未执行真实 ChatGPT 账户模型调用；macOS 原生离线工具检查与既有审阅 Router 认证是两项独立证据。Claude 快速路由未接入，也没有任何 Claude 付费调用。Linux/Windows 的原生快速路径未在真机验证。审阅路由沿用此前 macOS Codex CLI 0.157.0 的认证，未重新运行付费审阅探针；预算数值仍属待校准值。本次未做日常迁移、安装后服务冒烟或实际费用核对。

实现使用 Buddy 工作目标 `obj-78a6585a-e208-44b8-86d9-dec1f127ce98`，四个子任务的固定最终产物均已登记 `verified` 整合并 `accepted`。Host 在整合记录中列出实际调整的产物路径，并完成原生协议、迁移、配额、界面与文档复核；分组提交用于记录各补丁的落点，完整检查针对最终组合源码。产物之外的 Host 补充修正在 `b51eaf1`，源码与参考文档在 `f8701fc` 汇合，之后只补充本验收记录与议程状态。四个受管检出均经 `workspace-cleanup-plan` / `workspace-cleanup-apply` 精确匹配路径后清理，原始补丁、清单与固定 Git 引用保留。本次开发 worktree 保留供用户审阅。

| 子任务 | runId | 已验证整合记录 | 分组提交 |
| --- | --- | --- | --- |
| 通用、DSH、ZCode adapter | `8fb06832-5df1-4870-a863-8fb5819ec508` | `int-64a7f618-15f8-4712-984f-5f38a56ab3ae` | `72a2671` |
| Codex adapter | `b0b0a7f0-f90d-41cf-b00f-e2d445c68503` | `int-cd69faa2-9745-46c1-9173-3703263bf115` | `4b5ef4d` |
| 控制台 | `d32f2331-c59e-4d2a-91cb-5865688a780f` | `int-2749e76f-097b-4779-905a-e187c3032270` | `c1143e6` |
| 文档 | `3f6f1810-9fc7-40db-b1eb-1ce4e9eeff87` | `int-f74b0e42-0937-4bdc-9671-40b10ad547d3` | `f8701fc` |

开发期间 `socu/buddy-core` 前进到 `7e9257e`，新增时间轴重排修正和控制台问题清单。对该提交与本候选执行 `git merge-tree --write-tree --name-only` 的模拟合并无冲突，未修改任何分支；模拟结果尚未作为合并后的运行代码测试。建议获授权后将本候选合入 `socu/buddy-core`，复核并测试合并结果，再在日常服务空闲时从固定提交用 `uv build --wheel` 直接构建候选 wheel，经单独授权后通过该 wheel 的安装入口升级。升级后核对版本、备份、迁移后的两个 Router 位置及控制台提示；用户另行选择空的 Router 位置。当前没有执行合并、日常安装或用户配置变更。
