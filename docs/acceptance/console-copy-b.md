# B 批第一阶段：控制台说明文案收敛

基线 `62fd32b`；逐条对应 [控制台文案审计清单](../design/console-copy-audit.md) 的表格行，按原顺序编号。清单实际为 227 行。处理遵循 Host 的更严标准：仅保留可处理错误、破坏性确认、短的如实状态；路由数据流向放入同区域 `?` 帮助。动态值在下表以 `{…}` 表示。

| 编号 | 原清单来源 | 实际处理与现行文案 |
| --- | --- | --- |
| 001 | `TaskDetails.tsx:16` | 删除只读提示 |
| 002 | `TaskDetails.tsx:84` | 精简为“用量：{…}” |
| 003 | `TaskDetails.tsx:90` | 删除验收解释 |
| 004 | `TaskDetails.tsx:92` | 精简为“决策 ID 未记录” |
| 005 | `Tasks.tsx:99` | 精简为“已加载 {…} / {…} 条” |
| 006 | `Tasks.tsx:127` | 精简为“试试其他筛选或关键词。” |
| 007 | `Tasks.tsx:134` | 删除未选详情说明 |
| 008 | `WorkflowPanel.tsx:88` | 删除“由 Host 处理” |
| 009 | `WorkflowPanel.tsx:105` | 精简为“Host 结论” |
| 010 | `WorkflowPanel.tsx:107` | 精简为“{结果} · 不计入验收” |
| 011 | `WorkflowPanel.tsx:139` | 删除接续次数解释 |
| 012 | `WorkflowPanel.tsx:140` | 精简为“暂无回合摘要” |
| 013 | `WorkflowPanel.tsx:142` | 精简为“摘要已截断” |
| 014 | `WorkflowPanel.tsx:162` | 精简为“另有 {…} 项待决定” |
| 015 | `WorkflowPanel.tsx:186` | 精简为“累计补丁 · 引用不完整”（按引用完整性显示） |
| 016 | `WorkflowPanel.tsx:194` | 精简为“Host 补充改动” |
| 017 | `WorkflowPanel.tsx:199` | 删除产物列表下方验收说明 |
| 018 | `WorkflowPanel.tsx:199` | 精简为“暂无固定产物” |
| 019 | `WorkflowPanel.tsx:222` | 精简为“整合证明未记录；请由 Host 完成整合与验收。” |
| 020 | `WorkflowPanel.tsx:231` | 精简为“暂无执行记录” |
| 021 | `WorkflowPanel.tsx:243` | 删除重复用量口径说明；各行保留未知状态 |
| 022 | `RoutingDetails.tsx:52` | 精简为“决策 ID 未记录；可查看当前配置或路由历史。” |
| 023 | `RoutingDetails.tsx:60` | 精简为“Host 指定”或“路由决策未记录” |
| 024 | `RoutingDetails.tsx:64` | 保留当时硬约束字段 |
| 025 | `RoutingDetails.tsx:74` | 删除路由历史说明 |
| 026 | `RoutingDetails.tsx:98` | 精简为“Host 指定”或“回合路由未记录” |
| 027 | `RoutingDetails.tsx:101` | 精简为“已截断 · 另有 {…} 回合” |
| 028 | `RoutingPanel.tsx:29` | 精简为“路由需要 Host 补充配置；处理后请刷新。” |
| 029 | `DecisionDetails.tsx:96` | 保留“未记录”；精简为“无引用证据” |
| 030 | `DecisionDetails.tsx:103` | 保留当时硬约束字段及未记录状态 |
| 031 | `DecisionDetails.tsx:109` | 精简为“候选快照无用户偏好”或“输入快照未记录” |
| 032 | `DecisionDetails.tsx:110` | 删除候选范围的重复解释 |
| 033 | `DecisionDetails.tsx:113` | 精简为“已发布”或“未发布” |
| 034 | `DecisionDetails.tsx:115` | 精简为“无新版本” |
| 035 | `ObjectiveOverview.tsx:15` | 删除层级术语帮助及其入口 |
| 036 | `ObjectiveOverview.tsx:34` | 保留“Worker 自述”归属标签 |
| 037 | `ObjectiveOverview.tsx:71` | 精简为“结果未知” |
| 038 | `ObjectiveOverview.tsx:243` | 精简为“回合累计：{…}” |
| 039 | `ObjectiveOverview.tsx:244` | 精简为“运行中 {…} 回合 · 截至 {…}” |
| 040 | `ObjectiveOverview.tsx:245` | 精简为“结束未确认 {…} 回合 · 未计入” |
| 041 | `ObjectiveOverview.tsx:247` | 精简为“统计范围不完整：{…}” |
| 042 | `ObjectiveOverview.tsx:248` | 精简为“数据截至 {…}” |
| 043 | `ObjectiveOverview.tsx:250` | 精简为“来源 Host {…} · 当前 Host {…}” |
| 044 | `objective-metrics.ts:29` | 精简为“读取范围不完整” |
| 045 | `objective-metrics.ts:30` | 保留“委派行已截断” |
| 046 | `objective-metrics.ts:31` | 保留“执行片段已截断” |
| 047 | `objective-metrics.ts:32` | 保留“Host 事件已截断” |
| 048 | `objective-metrics.ts:33` | 精简为“片段缺少时间” |
| 049 | `objective-metrics.ts:34` | 精简为“片段时间异常” |
| 050 | `objective-metrics.ts:35` | 精简为“片段存在时钟偏差” |
| 051 | `objective-metrics.ts:36` | 精简为“结束未确认 · 未计入” |
| 052 | `objective-metrics.ts:37` | 精简为“运行中 · 计至读取时刻” |
| 053 | `ObjectiveList.tsx:215` | 精简悬浮提示为“未指定工作目标 · 已加载 {…} 个” |
| 054 | `ObjectiveList.tsx:224` | 删除更早记录说明；保留“加载更早工作目标”操作 |
| 055 | `ObjectiveList.tsx:227` | 删除初始空状态说明 |
| 056 | `ObjectiveList.tsx:228` | 精简为“试试其他筛选或关键词。” |
| 057 | `Objectives.tsx:113` | 确认框保留停止范围和已验收数量，删除状态预告 |
| 058 | `Objectives.tsx:144` | 删除工作目标视图悬浮说明 |
| 059 | `Objectives.tsx:146` | 删除全部记录视图悬浮说明 |
| 060 | `Objectives.tsx:365` | 精简为“{停止状态}：{详情}” |
| 061 | `Objectives.tsx:441` | 删除未选详情说明 |
| 062 | `ObjectiveTimeline.tsx:820` | 精简为“空闲 {…} · {时间段}” |
| 063 | `ObjectiveTimeline.tsx:931` | 精简为“片段时间缺失或异常，未定位” |
| 064 | `ObjectiveTimeline.tsx:930` | 删除副标题中的“取自任务首行” |
| 065 | `ObjectiveTimeline.tsx:1019` | 精简为“时间轴记录不完整：{…}。可调整筛选或打开委派详情。” |
| 066 | `ObjectiveTimeline.tsx:1030` | 精简标题为“检查器” |
| 067 | `ObjectiveChronology.tsx:23` | 精简为“记录缺少可用时间，无法排序。” |
| 068 | `ObjectiveChronology.tsx:29` | 精简为空闲时长与时间段 |
| 069 | `TimelineInspector.tsx:7` | 精简为“记录在当前范围外，无法打开” |
| 070 | `TimelineInspector.tsx:73` | 精简为“记录在当前范围外” |
| 071 | `TimelineInspector.tsx:86` | 精简为“Host 事件已截断” |
| 072 | `TimelineInspector.tsx:124` | 删除交互提示 |
| 073 | `inspector-card.ts:114` | 保留已记录范围内无片段与未记录片段的区别 |
| 074 | `inspector-card.ts:129` | 保留“执行情况未知”与“未执行”的区别 |
| 075 | `MarkerPopover.tsx:105` | 删除交互提示 |
| 076 | `RunDetailPane.tsx:99` | 精简为“可重试读取或返回时间轴。” |
| 077 | `RunDetailPane.tsx:104` | 删除加载占位的导航说明 |
| 078 | `objective-display.ts:225` | 精简为“任务首行” |
| 079 | `objective-display.ts:253` | 删除标题悬浮提示中的详情指引 |
| 080 | `objective-display.ts:682` | 保留路由模式、降级原因、配置与结果的悬浮事实 |
| 081 | `objective-stop.ts:61` | 精简为“停止结果未知，可能已生效；可重试同一请求或刷新核对。” |
| 082 | `objective-stop.ts:65` | 精简为“停止中 · 等待证据” |
| 083 | `objective-stop.ts:69` | 精简为“时间轴读取不完整” |
| 084 | `objective-stop.ts:102` | 精简为“仍有 {…} 项停止未确认” |
| 085 | `objective-stop.ts:105` | 精简为“{…} 项停止未确认或超出读取范围” |
| 086 | `objective-stop.ts:111` | 精简为“时间轴读取不完整 · 停止未确认” |
| 087 | `objective-stop.ts:115` | 精简为“{…} 个委派及协助任务已停止” |
| 088 | `objective-stop.ts:164` | 精简为“停止结果未知；可重试同一请求。” |
| 089 | `task-activity.tsx:203` | 精简为“仅有监管心跳 · 进展未知”“工具执行中”“已收到原生活动”“活动未知 · 停止未确认” |
| 090 | `task-activity.tsx:209` | 删除活动观察机制说明 |
| 091 | `task-activity.tsx:213` | 精简为“暂无活动记录 · 停止未确认” |
| 092 | `native-session.tsx:123` | 删除原生会话的查看说明 |
| 093 | `host-workflow.ts:93` | 精简为“用量未记录” |
| 094 | `host-workflow.ts:190` | 精简为“额度观测已过期”或“最近额度观测 · 非实时” |
| 095 | `task-state.ts:29` | 删除任务标题悬浮提示中的详情指引 |
| 096 | `task-state.ts:109` | 精简为“交付结果未记录” |
| 097 | `BuddyConfig.tsx:37` | 精简为“点击‘重新检测’查看状态。” |
| 098 | `BuddyConfig.tsx:38` | 精简为“安装 CLI，或填写手动路径。” |
| 099 | `BuddyConfig.tsx:39` | 精简为“在 Harness 中登录后重新检测。” |
| 100 | `BuddyConfig.tsx:40` | 精简为“检查 CLI，或更换手动路径。” |
| 101 | `BuddyConfig.tsx:227` | 精简为“最近额度观测：{提醒}”悬浮提示 |
| 102 | `BuddyConfig.tsx:233` | 精简为“重新检测全部 Harness”悬浮提示 |
| 103 | `BuddyConfig.tsx:264` | 精简为“重新检测 {Harness}”悬浮提示 |
| 104 | `BuddyConfig.tsx:292` | 保留原生额度备注；由观测来源提供 |
| 105 | `BuddyConfig.tsx:296` | 精简为“{修复办法}” |
| 106 | `BuddyConfig.tsx:302` | 精简为“{来源}” |
| 107 | `BuddyConfig.tsx:327` | 精简为“请输入可执行文件的绝对路径。” |
| 108 | `BuddyConfig.tsx:335` | 精简为“保存后立即检测；恢复自动检测将清除手动路径。” |
| 109 | `BuddyConfig.tsx:463` | 删除发现模型按钮的重复悬浮解释 |
| 110 | `BuddyConfig.tsx:479` | 精简为“已达显示上限（{…}）；请搜索更早配置。” |
| 111 | `BuddyConfig.tsx:495` | 精简为“{不可用原因}” |
| 112 | `BuddyConfig.tsx:527` | 精简为“试试其他搜索或筛选。”或“点击‘发现模型’查找本机模型。” |
| 113 | `BuddyConfig.tsx:543` | 删除未选模型家族说明 |
| 114 | `FamilyDetail.tsx:45` | 精简为“评价证据未记录” |
| 115 | `FamilyDetail.tsx:126` | 档位偏好说明保留在原有“?”，缩成档位覆盖与家族偏好关系 |
| 116 | `FamilyDetail.tsx:127` | 删除当前设置重复说明 |
| 117 | `FamilyDetail.tsx:156` | 保留不可用原因 |
| 118 | `FamilyDetail.tsx:185` | 保留占用及非法并发值的提示 |
| 119 | `FamilyDetail.tsx:282` | 档位帮助保留在“?”，缩为符号与菜单位置 |
| 120 | `FamilyDetail.tsx:294` | 家族偏好帮助保留在“?”，缩成适用范围 |
| 121 | `FamilyDetail.tsx:314` | 并发帮助保留在“?”，缩成共享范围、1–32 与生效边界 |
| 122 | `FamilyDetail.tsx:319` | 备注帮助保留在“?”，缩成“家族备注；留空清除。” |
| 123 | `FamilyDetail.tsx:328` | 评价帮助保留在“?”，缩成“评价由维护 Harness 发布。” |
| 124 | `FamilyDetail.tsx:339` | 精简为“{评价来源} · {更新时间}” |
| 125 | `FamilyDetail.tsx:345` | 删除重复的证据用途说明 |
| 126 | `FamilyDetail.tsx:351` | 精简为“未验证”或“目录中不可用” |
| 127 | `FamilyDetail.tsx:362` | 确认框保留将替换的 Router，删除后续任务解释 |
| 128 | `buddy-display.ts:50` | 偏好帮助保留在原有“?”，缩成优先、固定、排除定义 |
| 129 | `EvaluationHistory.tsx:134` | 删除更新记录页的只读解释 |
| 130 | `EvaluationHistory.tsx:140` | 精简为“暂无已发布评价” |
| 131 | `RoutingStatusBar.tsx:10` | 预算帮助保留在“?”，缩成审阅预算与快速路由时限 |
| 132 | `RoutingStatusBar.tsx:42` | 健康帮助保留在“?”，缩成失败统计口径 |
| 133 | `RoutingStatusBar.tsx:44` | 精简为“路由摘要未知” |
| 134 | `RoutingStatusBar.tsx:46` | 精简为“最近 {…} 次内暂无样本” |
| 135 | `RoutingStatusBar.tsx:24` | 精简为“路由连续失败 {…} 次；请查看详情，必要时更换 Router。” |
| 136 | `RoutingStatusBar.tsx:55` | 仅有非零结果时显示弃权、取消与过期计数 |
| 137 | `RoutingStatusBar.tsx:123` | Router 资格帮助保留在“?”，缩成资格和设置入口 |
| 138 | `RoutingStatusBar.tsx:97` | 精简为“未指定{模式} Router；请在档位菜单中选择。” |
| 139 | `RoutingStatusBar.tsx:137` | 数据流向移入默认模式旁的“路由数据流向 ?”：需要 Router 判断时，快速模式发送任务描述；审阅模式还读取冻结仓库副本 |
| 140 | `RoutingStatusBar.tsx:150` | 当前预算上限移入“路由预算说明 ?” |
| 141 | `policy.ts:138` | 精简为“{家族} 并发上限须为 1–32 的整数；请修改。” |
| 142 | `policy.ts:148` | 精简为“{档位} {原因}；无法启用，请撤销修改。” |
| 143 | `policy.ts:169` | 精简为“{家族} 没有可用档位；请先启用档位再固定。” |
| 144 | `policy.ts:184` | 精简为“{档位} {原因}；无法固定，请选择可用档位。” |
| 145 | `policy.ts:198` | 精简为“{档位} {原因}；无法担任 Router，请换档位。” |
| 146 | `policy.ts:221` | 保留当前 Router 失效原因和具体修复动作 |
| 147 | `policy.ts:255` | 精简为“固定选择指向 {档位}：{原因}；请启用或改回‘跟随家族’。” |
| 148 | `policy.ts:270` | 精简为“家族 {…} 的固定档位不可用；请启用档位或更改家族偏好。” |
| 149 | `draft.ts:610` | 两种冲突分别精简为“尚待读取最新版本；草稿已保留”与“已在 V{…} 更改；请重新加载核对” |
| 150 | `Settings.tsx:23` | 删除主题选择说明 |
| 151 | `StoragePanel.tsx:207` | 删除存储标题下说明 |
| 152 | `StoragePanel.tsx:245` | 精简悬浮提示为“看板、记录及当前备份受保护” |
| 153 | `StoragePanel.tsx:222` | 精简为“已过期 · 请重新检查”“计划已过期 · 请重新检查”或检查时间 |
| 154 | `StoragePanel.tsx:231` | 精简为“清理结果未知；请先重试同一请求” |
| 155 | `StoragePanel.tsx:270` | 删除“为何保留”内重复标题 |
| 156 | `StoragePanel.tsx:275` | 精简为“清理不会停止这些进程。” |
| 157 | `StoragePanel.tsx:298` | 精简为“跳过 {…} 项 · 数据已变化” |
| 158 | `StoragePanel.tsx:311` | 精简为“清理结果未知，可能已执行”或“清理未完成 · 已开始删除” |
| 159 | `StoragePanel.tsx:316` | 精简为“重试同一请求可继续或核对清理” |
| 160 | `StoragePanel.tsx:401` | 确认框保留复核、跳过与受保护范围，缩成一句 |
| 161 | `StoragePanel.tsx:402` | 精简为“计划已过期；请关闭并重新检查” |
| 162 | `App.tsx:43` | 加载页精简为“正在连接…” |
| 163 | `App.tsx:64` | 精简为“{…} 项未保存”或“保存结果待核对” |
| 164 | `App.tsx:93` | 精简为“连接中断；请刷新重试，草稿已保留。” |
| 165 | `App.tsx:95` | 精简为“登录已失效；请运行 buddy console 重新登录，草稿已保留。” |
| 166 | `App.tsx:97` | 精简为“无写入资格；保存不可用。” |
| 167 | `App.tsx:115` | 精简为“连接中断：{错误} 请检查服务或刷新重试。” |
| 168 | `App.tsx:116` | 精简为“评价表 V{…}” |
| 169 | `App.tsx:130` | 刷新悬浮提示精简为“数据截至 {…}” |
| 170 | `App.tsx:150` | 刷新悬浮提示仅显示刷新状态或数据截至时间 |
| 171 | `App.tsx:157` | 精简为“设置已更新；请核对草稿。” |
| 172 | `api.ts:17` | 精简为“记录已更新；请刷新核对后重试。” |
| 173 | `api.ts:18` | 精简为“记录冲突；请刷新核对后重试。” |
| 174 | `api.ts:19` | 精简为“无操作权限；请重新打开控制台。” |
| 175 | `api.ts:20` | 精简为“编辑权限已过期；请重新取得权限，草稿已保留。” |
| 176 | `api.ts:21` | 精简为“编辑权限已失效；请重新取得权限，草稿已保留。” |
| 177 | `api.ts:22` | 精简为“操作资格已失效；请刷新核对负责人。” |
| 178 | `api.ts:23` | 精简为“停止未确认；请稍后核对再重试。” |
| 179 | `api.ts:24` | 精简为“清理未完成；请重试同一请求。” |
| 180 | `api.ts:25` | 精简为“计划已过期；请重新检查。” |
| 181 | `api.ts:26` | 精简为“登录已失效；请运行 buddy console 重新登录。” |
| 182 | `api.ts:27` | 精简为“入口已过期；请重新打开控制台。” |
| 183 | `api.ts:59` | 精简为“会话信息无法识别；请检查服务版本或重新登录。” |
| 184 | `api.ts:256` | 精简为“无法连接本地黑板；请刷新重试。” |
| 185 | `api.ts:296` | 精简为“控制台数据不完整；请检查服务版本。” |
| 186 | `api.ts:332` | 精简为“委派历史不完整；请检查服务版本。” |
| 187 | `api.ts:346` | 精简为“工作目标列表不完整；请检查服务版本。” |
| 188 | `api.ts:374` | 精简为“工作目标时间轴不完整；请检查服务版本。” |
| 189 | `api.ts:317` | 精简为“提交结果未知；请核对后重试。” |
| 190 | `api.ts:397` | 精简为“清理结果未知；请重试同一请求。” |
| 191 | `api.ts:416` | 精简为“重新检测结果不完整；请检查服务版本。” |
| 192 | `api.ts:438` | 精简为“路径保存结果未知；请检查服务版本。” |
| 193 | `console-session.ts:53` | 精简为“登录已失效，操作未提交；请重新登录。” |
| 194 | `console-session.ts:57` | 精简为“登录已失效；请重新登录后保存，草稿已保留。” |
| 195 | `console-session.ts:65` | 精简为“保存结果未知，可能已生效；请核对后重试。” |
| 196 | `console-session.ts:73` | 精简为“连接中断，无法提交；请刷新重试，草稿已保留。” |
| 197 | `use-editor.ts:786` | 精简为“已发布新版本” |
| 198 | `use-editor.ts:324` | 精简为“保存资格已过期，结果未知；请重试同一保存。” |
| 199 | `use-editor.ts:327` | 精简为“保存资格已过期；请重试保存。” |
| 200 | `use-editor.ts:331` | 精简为“编辑资格未知；请重试保存。” |
| 201 | `use-editor.ts:341` | 精简为“{错误} 释放结果未知；请重试保存。” |
| 202 | `use-editor.ts:382` | 精简为“正在保存或读取目录；请稍后修改。” |
| 203 | `use-editor.ts:386` | 精简为“保存结果未知；请核对或重试同一保存。” |
| 204 | `use-editor.ts:470` | 精简为“编辑资格未知；请重试保存。” |
| 205 | `use-editor.ts:567` | 精简为“登录已失效；请重新登录后核对保存结果。”或“请重新登录后保存。” |
| 206 | `use-editor.ts:591` | 精简为“编辑资格释放未确认；请稍后重试。” |
| 207 | `use-editor.ts:606` | 精简为“编辑资格释放未确认；请稍后重试。” |
| 208 | `use-editor.ts:654` | 精简为“等待保存 · 前面 {…} 位”或“等待保存…” |
| 209 | `use-editor.ts:673` | 精简为“等待超时；请重试保存，草稿已保留。”或“等待超时，释放未确认；请重试保存。” |
| 210 | `use-editor.ts:708` | 精简为“编辑资格未知；请重试保存。” |
| 211 | `use-editor.ts:719` | 精简为“{错误} 释放结果未知；请重试保存。” |
| 212 | `use-editor.ts:746` | 精简为“已取消等待；草稿已保留。” |
| 213 | `use-editor.ts:751` | 精简为“编辑资格释放未知；请稍后重试，草稿已保留。” |
| 214 | `use-editor.ts:828` | 精简为“保存结果未知，可能已生效；请重试同一保存。” |
| 215 | `use-editor.ts:847` | 精简为“设置已更新；请重新加载核对或放弃草稿。” |
| 216 | `use-editor.ts:858` | 精简为“正在读取目录或保存；请稍后重试。” |
| 217 | `use-editor.ts:880` | 精简为“暂无法保存；请稍后重试，草稿已保留。” |
| 218 | `use-editor.ts:892` | 精简为“{…} 项无法保存：{原因}” |
| 219 | `use-editor.ts:905` | 删除无修改时的通知 |
| 220 | `use-editor.ts:909` | 精简为“设置已更新；请重新加载核对或放弃草稿。” |
| 221 | `use-editor.ts:928` | 精简为“正在排队；请先取消等待。”或“正在保存；请等待结果。” |
| 222 | `use-editor.ts:932` | 精简为“保存结果未知；请核对或重试同一保存。” |
| 223 | `use-editor.ts:942` | 精简为“草稿已放弃” |
| 224 | `use-editor.ts:945` | 精简为“草稿已放弃；编辑资格释放未知。” |
| 225 | `use-editor.ts:956` | 精简为“保存未确认；请核对后重新加载。” |
| 226 | `use-editor.ts:969` | 精简为“编辑资格释放未知；请稍后重新加载。” |
| 227 | `use-editor.ts:989` | 精简为“已加载 V{…}；请核对后保存。” |

## 验证边界

在 Node 24.21.0 下，`npm --prefix apps/console ci`、`npm --prefix apps/console run typecheck`、`npm --prefix apps/console run build` 均退出 0。使用私有 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT` 并清除继承的运行时、Worker、Agent 和 Python 环境变量后，前端全量测试为 589/589 通过；最后删除两处交互提示后，受影响的 `objective-timeline.test.tsx` 为 39/39 通过，构建再次退出 0。Vite 提示单个打包块超过 500 kB，这是非阻塞的体积提示。

真实应用浏览器访问 `tests/probes/objective_console_preview.py` 的私有合成服务；`/api/console` 返回 `X-Buddy-Preview: synthetic-fixture-data`。在工作目标、Buddy 配置和设置页核对短文案；展开路由详情后，默认模式旁的“路由数据流向 ?”显示发送任务描述及读取冻结仓库副本的边界，预算帮助显示当前 300 秒、24 次工具调用。浏览器警告与错误为零。预览不连接日常看板、不启动 Harness；检查后已关闭浏览器标签与预览服务。

本提交只包含 `apps/console` 源码、受影响断言和本记录。构建出的 `src/buddy/console_assets` 已还原；合并后须从最终组合树重新构建资源。完整 `uv run --frozen python -m buddy.checks` 由 Host 在整合树执行。本记录不声称日常服务已安装或实际账户能力已验证。
