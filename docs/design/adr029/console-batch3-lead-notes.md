# 控制台批次三：Lead 交接

2026-10-18。批次三的十项工作已整合，实际实现与构建资源提交为 `9ed40d303207f79cf986c5416a7477dc96d7ee37`，原任务输入为 `74821e9f33ff94731a7f8179053adc2c6cab2b00`。本 Lead 已在整合检出运行规定检查、查看真实浏览器截图，并以这个实际提交接受全部 8 个名下微任务。这里记录的是 Lead 的验证与名下验收；Host 对整个批次的最终验收、安装和日常环境验证尚未进行。

## 实现与事实契约

原因词条集中在 `i18n/{zh,en}/reasons.ts`，经同一个 formatter 显示。全集检查直接比较协议代码表与中文词条，并格式化当前 149 个代码各自的全部参数，覆盖中文与英文。harness 原有 reasonText、remedyText、账户 guidance 和候选 reasonCode 的旧表已删除或按明确状态推导；不读取旧服务说明作为回落。Host、用户、Router 写下的 note 保持原文。

解析、类型、夹具和显示一并使用当前回复：原因对象；没有 waitReason、capabilities.limitations、存储分类 label 或标记的 label/summary；标记读取 decision、verdict、state、strategy、note；缺标题时 title/fixesTitle 为 null，titleSource 保留；问询的 reason 与执行的 releaseReason 都按原因对象处理。buddy 的 effortRank 为必有值，合成预览按模型内的推理强度位置给出它。

删除入口与确认框按认可稿接上完整预览、确认范围、只读状态、失败和固定请求重试；未知回复不猜作成功。拦截项直接使用自身的标题事实，不再查已加载的时间片行或拿身份编号当标题。长拦截列表采用三行网格：标题与按钮常驻，中间视口滚动；实际浏览器发现的“视口跟着内容撑高、内容被外层裁掉”已在原因处修复。私有 HTTP 检查证明控制台停止后，未确认退出时仍不能删；确认退出且微任务停在 cancelled 后，不要求 Host 另给结论即可删除整个宏任务。

主应用启动先读入口 meta 标记，在创建 API 客户端之前呈现入口失效的标题、原因与 `hey-my-buddy console` 命令，支持中英文。并行上限处放入开发者给定的两段原文，字词、标点保持一致，没有加入厂商、档位、数字或日期。

名字单击打开微任务；时间片、问询与事件保留单击选中、双击或 Enter 打开。宏任务使用一个共用滚动区，名字随纵向滚动、横向保持；两轴滚动条由共用 ScrollArea 统一在悬停或滚动时显示，轨道右侧只有一条竖向条。缩放保留锚点，定时读取和返回详情后保留位置。普通名字行 44 px、执行条 22 px，轴与首行没有额外间隔；Lead 行 60 px，泳道之间保留 24 px。

详情顶部常驻该微任务自己的一行时间片，与宏任务复用 Axis、Track、颜色注册表和缩放控件。首次打开只定位一次，包括没有自身时刻的待验收步；后续选择与读取不跟随。缩放控件仍在右下角，并留独立空间，避免挡住最后一段的点击目标。待验收、历史验收、换手、决定和事件都能切换下面的内容，选择保持唯一；键盘焦点落在实际选中的片上。左栏只切换微任务；窄屏隐藏左栏后由详情本身承接焦点。宽窄按面板实际可用宽度决定，避免窗口还有侧栏时轨道被挤成几像素。

每个明确带 delegationAuthority 的 Lead 自身行作泳道组头，按 rootLeadRunId 的记录把名下行排在一起；不按名字或有没有孩子猜 Lead，不另画 Lead 树。各泳道共用刻度、独立收起，保留自身行和唯一一份汇总。历时、累计执行和 token 按这条泳道的记录计算，包含 Router 执行，按缓存口径避免重复累计，未知和部分记录明确显示。历史条沿用现有六种浅色，按完整 harness/provider/model/effort 四段、首次出现顺序分配，并在读取或筛选后保留。行显示原样 harness 标志、模型与强度，提示给完整身份和被截断的标题；Codex 保留原彩色 SVG。

### 服务新增的两个只读投影

每个 TimelineRow 必有 `delegationAuthority: boolean`，直接来自现有 metadata；普通微任务、helper、尚无名下微任务的 Lead 都返回自身事实，没有额外逐行请求或权限推断。

删除预览增加且必有如下结构，确认请求与原字段保持原义：

```ts
scope: {
  artifacts: { count: number | null; bytes: number | null };
  sessions: { count: number | null; bytes: number | null };
  checkouts: number | null;
  gitRefs: number | null;
}
```

数值为非负整数；0 是已知为空，null 是无法完整确定。artifacts.count 按 workflow_artifacts 条目计数，sessions.count 按不同 (harness, sessionId) 计数；checkouts 只计受管分配的不同检出，gitRefs 按现有删除 helper 的固定绑定去重，绑定缺失或冲突时为 null。非空产物的 Git 对象可能共享，会话回执也没有完整私有字节清单，因此 bytes 为 null，空范围为 0；没有把 patch 长度或整个仓库大小当作总大小，没有读取用户原生库。删除后保留既有 microTaskCount，已不能重建的 scope 各项为 null。不新增统计存储，不改变删除判断、健康检查、共享保护、停止围栏或物理删除步骤。

每个 blocker 必有 `title: string | null` 与 `titleSource: "title" | "task" | "none"`，沿用宏任务投影的作者标题/任务首行事实；none 时为 null，路由计算也有自身的名称事实。状态、原因、attemptIds、排序、50 项上限及 blockerTotal 的原义不变。

合成预览一次性跟上这些契约；包含 Lead 带四个名下微任务及普通微任务、尚无名下微任务的 Lead、可删除的已了结范围。新增两项删除操作只修改当前私有服务器实例的内存数据，不调用真实服务、harness、文件删除或 Git 删除；其他写仍被拒绝。

## 名下微任务与验收

共 8 项、12 个 Worker 回合，全部默认经路由，没有指定 buddy。以下回合与状态来自完成时的 get；两次打回均属于布局任务，其余续接是对请求的答复，不计为打回。续接一直使用 get 返回的实际检出路径。

| 工作与 runId | 路由选中的 buddy | 回合 | 打回与原因 | 最终状态 |
| --- | --- | --- | --- | --- |
| 原因与当前回复 `cda72b0d-e2fe-4416-9664-0a3acc5bc650` | codex / openai / gpt-6.1-sol / max | 1 | 0；预览缺口后来由独立任务补齐 | accepted |
| 删除入口与确认 `481fd25f-34b5-4a68-8bfb-187b0574f9d3` | codex / openai / gpt-6.1-sol / max | 2 | 0；第二回合接服务统计契约，范围外浏览器基线由 Lead 处理 | accepted |
| 滚动与打开手势 `7f3c672c-62f1-4bf9-993f-8ab63e16874c` | zcode / bigmodel-api-2 / GLM-5.3 / max | 1 | 0；接口与后续版式由整合接续 | accepted |
| 并行风险提示 `f193d668-7b5b-42c6-a3b9-92be48329387` | codex / openai / gpt-6.1-sol / high | 2 | 0；首轮请求的 scanner 缺口由 Lead 修正 | accepted |
| 详情顶部行与 Lead 泳道 `da2528ac-0a8f-4e11-a4bc-8569bdc17411` | zcode / bigmodel-api-2 / GLM-5.3 / max | 3 | 2；先修完整身份、键盘、历史步骤、首次定位和返回滚动，再修嵌套按钮、标题提示和焦点轮廓 | accepted |
| Lead 权限事实投影 `3c4fd017-8271-4de0-8910-e20cc5b6878b` | codex / openai / gpt-6.1-sol / max | 1 | 0 | accepted |
| 删除统计与名称 `7423950e-f5e2-4703-9633-5849197d8078` | codex / openai / gpt-6.1-sol / high | 1 | 0 | accepted |
| 当前合成预览 `0ec81fb7-43a1-4ff6-8d88-1fb19e51a463` | codex / openai / gpt-6.1-sol / max | 1 | 0 | accepted |

验收均绑定 `9ed40d303207f79cf986c5416a7477dc96d7ee37`。前三个应用任务、并行提示和布局因后续整合与 Lead 修正出现 blob 差异，服务先以 INTEGRATION_UNVERIFIED 列出差异；逐项核对后用 adjusted:true 明确承担调整，再接受原固定产物。三个服务/预览任务的产物路径直接匹配。八项 integrations 均为 verified，八个受管检出的 cleanup 均为 applied、removed=true；封存补丁、引用与原生会话按黑板规则保留。没有在验收后修改任一已验收产物，也没有需要 fixes 的晚返修。

## 实际检查与图片

所有最终检查在 Lead 的整合检出执行，不以 Worker 的成功消息代替。Node 使用指定的 24.21.0；依赖缺失时仅从本机缓存 ci --offline。测试子进程清除任务列出的运行时、凭据与虚拟环境变量，设置 UV_OFFLINE=1。没有联网、安装浏览器或使用 npm exec/npx。

| 检查 | 实际结果 |
| --- | --- |
| `npm --prefix apps/console run typecheck` | 最终源码通过，退出 0 |
| `npm --prefix apps/console test` | 85 文件、1050 项通过，退出 0 |
| `npm --prefix apps/console run test:browser -- --workers 4` | 构建通过，385 项通过，退出 0；资源随提交交回 |
| 指定 console/CLI 加受影响的黑板 Python 检查 | 18 文件、250 项通过，skipped=0，退出 0，私有根已清理 |
| 协议原因与真实预览解析 | 149 个码的中文全集对照及中英参数格式化；真实前端解析器验证 44 份夹具 |
| 静态稿与改前图片 | 静态稿最终 12 场景通过；真实应用改前 4 个窗口/主题场景通过 |

Python 命令为 `uv run --frozen python -m hey_my_buddy.cli.checks tests/python/console tests/python/cli/test_host_preview.py tests/python/blackboard/tasks/test_macro_tasks.py tests/python/blackboard/tasks/test_macro_task_deletion*.py`。投影的 40 项、删除的 79 项、预览的 7 项还分别在整合时执行过，均通过；它们包含在上述 250 项中，不另累加。250 项之后未再改服务、预览、解析器或 Python 测试，只修了布局、键盘处理和相应浏览器检查；最终三项前端检查随后串行通过。

最终日志在忽略的 `apps/console/test-results/lead-turn9-verified-*.log` 与 `lead-turn9-final-python.log`。私有预览端口已确认关闭。应用源码与设计文件的差异检查通过；生成 JS 里仍有依赖 CSS 模板字符串的 5 处行尾空白，未手改构建输出，不把整个生成资源的 whitespace 检查说成通过。

实际改前/改后图片按同一宏任务、步骤、1280 × 900 与 390 × 900、明暗主题保存：`batch3-before-{macro,detail}-{1280,390}-{light,dark}.png` 与 `batch3-after-{macro,detail}-{1280,390}-{light,dark}.png`。另有四种宽窄/明暗的 after-lanes 图和八张删除图，当时在 `console-frontend/`，Host 验收之后连同稿一起删除，可在 git 历史里找到。已查看宽窄明暗的宏任务、详情、泳道和删除确认/拦截的像素；静态稿的 20 张 PNG 不冒充实际应用截图。

可直接对照改前宽屏与改后宽屏，以及改前窄屏详情与改后窄屏详情。Lead 泳道、窄屏删除和可滚动的拦截列表也是当前构建的实际画面。

## 审查发现与处置

类型与组件测试通过后，浏览器仍发现了真实缺陷：44 px 行的打开图标挤住状态文字、缩放控件遮住时间片、Radix 视口的 intrinsic 宽度撑开详情左栏、窗口宽度与面板宽度混用、隐藏左栏接走焦点、长删除列表的视口无界，以及记号 Enter 的默认动作在焦点转移后再次打开微任务、丢掉事件位置。均在接受之前从原因处修正并检查。另补了稳定行 key 和验收片的键盘焦点；原样 harness 图形没有改动。

旧浏览器检查也有实质不同的假设：服务原因还是字符串、标记有 summary、固定两个宏任务、已删的界面称呼、侧栏步骤文字、名字双击、量到隐藏的字宽测量节点、假定很短片段的中心没有别的记号。检查已按当前契约、可访问名称、真正的刻度/行几何和明确的用户动作改写；保留了单击选择、事件准确打开、缩放锚点、唯一选择、焦点、滚动与布局边界的实际断言，没有为通过检查保留旧读法。

一次全量前端与浏览器并跑造成历史导航用例超时，后续单独运行通过，最终三项已串行验证。Lead 添加键盘焦点断言时曾放进了同名文本的错误用例，最终检查暴露后已移回真正的键盘用例；没有以删掉焦点断言结束。早期旧预览不能启动的浏览器检查、达到失败上限的部分运行和临时 CSS 诊断都不计为通过。Playwright 输出曾清掉同一 test-results 根的日志，现将其输出固定在 test-results/browser，后续原始记录和图片不再被它删除。

## Host 需要更新的参考文档与未验证边界

`docs/reference/console.md` 需要按当前实现更新：原因与 note 的分工；启动 meta 先于 API 的失效页面；删除入口、统计/未知值、拦截名称、固定确认和失败重试；控制台停止后的 cancelled；单击名字与时间片的不同手势；共享滚动区和两轴显示；固定详情行、独立左栏、首次定位与缩放；Lead 泳道、含 Router 的用时/用量和未知记录；完整 buddy 四段的分色与提示；并行上限处开发者认可的双语风险提示。旧 stop 参数 reason 的描述也须随批次二改为 note。

`docs/reference/macro-tasks.md` 需要更新 DTO：每行必有 delegationAuthority；title/fixesTitle 的 null 与 titleSource；事件的 decision/verdict/state/strategy/note；执行 releaseReason；删除 scope 与 blocker title/titleSource 的明确契约及统计边界。参考文档没有在本任务范围内写入。静态稿已经开发者认可，现保留供 Host 对照；按目录 README 的既定规则，Host 最终验收对应实现后再删除稿。

没有遗留的本批次实现阻塞。未进行安装、日常服务重启、日常看板写入、真实账户或原生会话删除探针；测试只用私有状态和编造数据，模型调用仅来自名下经黑板路由的微任务。原生会话/Git 共享内容的字节未知是明确的事实边界，不是假数字或遗漏的扫描；本批次没有扩大物理删除判断。

## Lead 的体会

边界清楚的原因、提示、服务事实和预览可以并行；同一批文件必须顺序交接，尤其是 MacroTasks、类型与布局。续接时先单独导入 Lead 固定补丁、再交回新提交，使第二轮不会把已经整合的差异再贴一次。服务布尔投影只有一行，单独路由的成本也说明并非越细拆越合适。

卡住的主要是预先没有给齐跨层契约：原因形状、无子任务的 Lead、删除统计与名称、预览写操作分别需要对齐。范围外停下、等全部执行确认停止再修订范围，守住了边界，但增加了回合。第一回合的通用“不要再建微任务”与 Lead 授权还发生过冲突，Host 更正后才开始真正的默认路由；两次静态稿审阅是开发者要求的约定停点。

最终浏览器审查比合入提交更费功夫。组件测试不能量出真实字形、重叠点击区、百分比视口和原生键盘默认动作；静态稿认可也不能替代应用像素与交互验证。这里实际完成了 8 项的整合、两次负面审查与逐项验收，但没有可比基线，因此不据此声称 Lead 更省时间或额度。
