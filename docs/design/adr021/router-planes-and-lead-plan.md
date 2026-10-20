# Router 三个面与 Lead 执行计划

## Lead 先行的顺序（2026-10-14 修订）

开发者于 2026-10-14 定：Lead 比 Router 的几个面更要紧，第 3 步走主线，工作区提速与两侧互相引用退为副线；Lead 走一条更短的路，但不能因此让排在后面的事要大改或返工。本节是按这个要求、并按[独立审查](router-planes-and-lead-plan-review.md)里整合 Host 核对过的几条修订后的顺序；下面原来的清点与 45 个微任务的表仍是依据，编号沿用。

**Lead 之前必须先做的。**

- 挪升级基点到 0.35.0，删掉用掉的归档升级。0.35.0 装上之后，归档升级已经在日常看板上跑过；在这一步之前不能改任何存储形式。（已交付，在验收。）

**Lead 的主线，按真实的依赖排。** 07 → 11 → 12 → 13 → 14。

- 07 预期时长取代 Worker 的硬时限。Router 调用的预算现在和 Worker 的硬时限共用 `timeoutSeconds`，Router 的切换又认终止原因 `deadline`（整合 Host 核对过：`blackboard/routing/decision.py:686`、`buddy/runtime/worker.py:781`）。所以 07 要把 Router 的预算换成只有 Router 调用才有的字段，到点照旧硬停，`deadline` 只表示 Router 的预算用完；预期时长只给微任务与命令用，并记在黑板的回合记录里，以后的提醒（08）就只是往上加。
- 11 有序收尾与终止原因。控制台的停止今天就有，11 要把它和 Host 的 `cancel` 分开：控制台的停止立即生效，Host 的停止先收尾。终止原因一次定全，发起者单独记一列；以后 Lead 发起的停止只是多一个发起者。收尾写在角色层，不写进各 harness 的运行模块，这样以后各 harness 的档位工作（02–05）不会与它冲突。
- 12 授予委派权并指定 Lead（开发者 2026-10-14 改定 ADR-026 第 2、3 条）：不设启用开关，不设 Lead 列表，也没有控制台的设置入口；用户明确要求时，Host 在委派时授予委派权，并写明用户指定的完整 buddy，不经路由。黑板按显式 buddy 的常规校验它，并把授予记在微任务上。确认与代价说明写进 Host 的指南。
- 13 名下的 Host 权限：只把对名下微任务的 Host 操作加进执行凭据现有的白名单，不另立一组拒绝。Lead 提交微任务用与 Host 同一套字段校验，能动哪些微任务由归属来限。
- 14 Lead 名下的生命周期。名下微任务的归属不复用协助任务的表 `workflow_children`：那张表的批准命令一列不能为空、状态是协助任务的取值（整合 Host 核对过），塞进去会让同一列的意思随行而变。要么另建一张表，要么每行写明种类、批准命令只有协助任务才填。归属记在 Lead 所执行的微任务上；名下微任务带 Lead 的宏任务；在等 Lead 的微任务不计入“等 Host”。停止 Lead 时连带名下的微任务走现有的 `_cancel_children`，同一条收尾路径，不另写一条级联。以后的泳道（16）要能把 Lead 先于泳道执行的工作如实画出来（开发者 2026-10-14 的要求），所以 14 存下的授予、归属、换手与各自的用时用量要足够，泳道不再加存储；14 的测试要证明这些事实能还原出每个 Lead 与它名下的微任务。

**推到 Lead 之后、不会因此返工的。** 01 与 02–05（四个 harness 都已经声明了“放开”一档，Lead 用它就够；各 harness 的档位与续接只是往上加）、06、08–10（提醒挂在被等待的微任务上；急停的范围收窄只改 `_cancel_children` 一处）、15（只在 `workflow_runs` 上加一列可空的“修正的是哪一个”，不建新模块与新表）、16（Lead 的泳道；它的三种状态都能从已有的事实推出，不需要新的存储），以及 Router 三个面的 17–25 等。原生检查一律用 `tests/probes/` 下的探针、有意去跑，不复制用户的配置。

**计划漏掉、另外排的。** ADR-022 第 2、3 条在控制台上的显示与“可能卡住”的阈值入口；数据流向说明的更新；维护面的数据包超过运行合约 2 MiB 上限时改用只读一档、把数据包放成目录；15 排在 17 之前。


291dddbd（0.35.0，schema 19）；完成仅源码，未跑测试、服务或原生检查。

缩写：O=src/hey_my_buddy，B=O/blackboard，W=O/buddy，P=O/protocol，F=apps/console/src；H=W/harnesses，E=B/evaluation，R=W/roles，I=O/install，C=O/cli。别名：d=B/store/db，s=B/store/store，w=B/tasks/workflow，r=B/routing/router，a=B/routing/decision，g=H/registry，p=P/schemas，k=H/run_contract，u=P/usage，x=R/run_execution，z=R/structured_call，b=C/blocking，t=F/console-data.ts；冒号为行号，Python文件省略.py、组件省略.tsx。

续：nc/nl/nd/nz=H/{codex/claude/dsh/zcode}/native_run，q=B/routing/quota_routing，m=E/model_facts，te=P/tool_evidence，iq=B/tasks/inquiry，ww=W/runtime/worker，ft=F/MacroTaskTimeline，fm=F/buddies/ModelLine，fr=F/buddies/RouterList，fd=F/TaskDetails，fs=F/MacroTasks，mg=B/store/migrations，bs=B/service/service；新文件mi=E/model_identity、ld=B/tasks/lead、du=B/tasks/duration。

## 现状（读源码得到的）

|ADR-021模块|状态及依据|
|---|---|
|L1|完成；p:108、a:453。|
|L2|完成；H/dsh/adapter:22、x:299。|
|L3|完成；bs:716、w:3999验收回收。|
|L4|部分；r:320、B/routing/router_boundary:12；缺另两面。|
|L5|部分；a:1269、te:398；缺五档共表。|
|L6|部分；nc:712；H/dsh/adapter:26、H/zcode/adapter:19缺审阅。|
|L7|部分；w:3999、d:309；缺事实、领域统计归纳。|
|L8|完成之后于 2026-10-20 删去：快照没有读者，随 schema 剪枝删掉存储与刷新；路由面要读价格时再做。|
|L9|部分；a:284、r:66；缺领域类型、画像评价、层级计费。|
|L10|未开始；a:465、d:958仅预留plane。|
|L11|未开始；d:423仅事实表，缺画像存储及执行链。|
|L12|未开始；k:71无此档。|
|L13|未开始；r:66无探索答案，d:373无计数。|
|L14|完成；d:22/299、C/main:63。|
|L15|完成；I/upgrade:377、mg:13。|
|L16|部分；skills/hey-my-buddy/SKILL.md:21；新行为待写。|
|L17|部分；w:3277仅跳过accepted/concluded，仍取消交付及Host边界。|
|L18|完成已消费；d:24、mg:13为新升级。|
|U1|部分；fr:56列表排序，缺维护探索及三面状态。|
|U2|未开始；fm:18无画像操作。|
|U3|未开始；fm:18无二维表及评价。|
|U4|部分；F/routing-display.ts:10；缺领域类型、探索、另两面。|
|U5|部分；F/ui:204、fs:331；缺回收、急停位置及数据流向。|
|U6|完成；fs:340、F/MacroTaskList:204；重做属第4步。|
|U7|未开始；F/host-workflow.ts:163仅窗口，u:301未保存状态余额。|

|ADR-022决定|状态、依据与缺口|
|---|---|
|1预期时长|未开始；p:160仍timeoutSeconds/0，ww:781硬停。|
|2重复提醒|未开始；b:461无提醒返回。|
|3可能卡住|部分；s:805；缺阈值提醒。|
|4黑板收尾|部分；w:3351黑板取消立即停，缺总结及宽限。|
|5Router硬预算|部分；r:16；Worker硬时限未删。|
|6保留await|完成；b:461。|
|7终止原因|未开始新分类；d:54仍user-cancel/deadline。|

|ADR-026决定|状态、依据与缺口|
|---|---|
|1双向角色|部分；R/texts/lead.txt:1存在，R/prompts:13未接。|
|2要求确认默认关|未开始；p:104、d:496无授予记录。|
|3有序名单|未开始；d:309无Lead设置。|
|4只有一层|部分；w:44；缺Lead权限。|
|5范围换手|部分；w:884、p:637；缺名下权限，不套helper上限。|
|6不对话用户|部分；R/texts/lead.txt:1已写，未运行接入。|
|7两层验收修正|部分；w:3999有终态，缺Lead验收及缺陷关联。|
|8复用整合收尾|部分；d:588、w:3377；缺Lead生命周期。|
|9并发名额|部分；s:568；缺Lead等待、名额不足上报。|
|10泳道|未开始；ft:1无Lead泳道、三态及汇总。|

|五档|状态、依据与缺口|
|---|---|
|不用工具|部分；k:71、a:1308有快速零工具，缺通用声明及维护使用者。|
|只联网|未开始；k:71无此档。|
|只读|Worker完成，共用部分；g:139四家强制，g:59审阅专参、H/base:154另判资格。|
|写检出|Worker完成；g:139前三家强制、ZCode约定，未贯通Router。|
|放开|Worker完成；g:139四家声明，Lead未选用。|

## 对不上的地方

依据文档，推断如下：

1. ADR-021§4及L6被ADR-023共用档位§2–6取代；g:59、r:29仍禁网及专参。
2. ADR-021§14/L12改共用强制只联网；ADR-030§2/9配置原样带入，不另禁MCP模拟。
3. ADR-021§1/9评价属buddy，backlog“知识归属”改harness＋模型＋强度；d:299备注仍属模型服务。
4. ADR-021§15、L14/L15/L18旧升级被10月13日补充与backlog§2取代；I/upgrade:377归档历史、携设置。
5. ADR-021版本闸门、租约及作废被L14补充的发布时合法候选校验取代；a:1379已实现，不在另两面重建。
6. L9服从ADR-030§4/10与ADR-027；B/catalog/catalog:29未知不等于不可用。
7. L16及一次安装的原顺序被backlog§2–4取代。
8. ADR-021§2三面同Router与ADR-023强制联网资格空缺：缺此档整体换手或画像停下未定。

## 微任务（推断）

45项；S存储、H Host接口、N逐次授权原生检查，—不改。含受影响测试、参考skill更新，F含构建；S动d/mg/夹具，设置动I/upgrade；省重复目录，新文件已标。

|独立微任务|文件|影响；依赖|
|---|---|---|
|01五档请求声明与判定|k、g、H/base、te|S；首项|
|02 Codex共用档位、审阅建线程|nc、H/codex/config|—；01；N|
|03 Claude共用档位删专参|nl、H/claude/config、g|—；01；N|
|04 DSH共用档位|nd、adapter|—；01；N|
|05 ZCode共用档位|nz、adapter|—；01；N|
|06 Router共用审阅载体资格|R/router、z、a、r、g|S；02–05；N|
|07 expectedSeconds取代工作硬时限|p、k、x、ww、command、w、C/main|SH；06|
|08重复超预期提醒|du（新）、s、b|SH；07|
|09无活动提醒及阈值|du、s、b、bs|SH；08|
|10急停限活动执行|w、B/tasks/cancellation|—；06|
|11有序收尾及终止原因|x、R/run_observers、ww、w、nc/nl/nd/nz|SH；07–10；N|
|12 Lead名单授予选用|ld（新）、p、w、R/prompts|SH；11|
|13名下Host权限凭据|ld、w、s、P/contracts、C/main|SH；12；N|
|14 Lead名下生命周期整合|ld、w、B/tasks/workspace、storage、macro_tasks|SH；13；N|
|15验收后缺陷关联|B/tasks/defects（新）、w、p|SH；14|
|16 Lead泳道名单|F/buddies/LeadList（新）、MacroTaskTimeline、t|—；15|
|17结果事实|E/result_facts（新）、w、s|S；06|
|18模型对应共享备注|mi（新）、model_facts、user_policy|S；17|
|19领域归类分层统计|E/domain_evaluation（新）、evaluation|S；18|
|20路由面输入及证据回退|a、r、R/router_input|S；07、19、23、25|
|21维护触发纠正发布|B/routing/maintenance（新）、router_sequence、B/service/daemon|S；19、06|
|22强制联网落法|nc/nl/nd/nz、各家config、g、tests/probes/|—；02–05；N|
|23画像构建|B/routing/portraits（新）、mi、z|S；18、22、06；N|
|24计数探索|a、r、B/routing/router_settings|S；20|
|25原生计费额度归属|u、nc/nl/nd/nz、E/native_observations、q|S；06；N|
|26续接拒绝自动改排|w、x|S；11；N|
|27 suggestedBuddyId必填可空|R/schema_worker、g|—；06；N|
|28 Claude目录失败诊断|H/claude/adapter、native_run、tests/probes/|—；03；N|
|29 Codex账户异常目录诊断|nc、tests/probes/|—；02；N|
|30运行版本|H/version、各家native_run、w|—；06、决定；N|
|31 Codex问询|nc、R/live、iq|—；02；N|
|32 Claude问询|nl、R/live、iq|—；03、31；N|
|33等Host微任务提醒|b、B/tasks/macro_tasks、skills/hey-my-buddy/|H；09|
|34执行租约过期诊断|ww、s|—；11|
|35启动收尾延迟诊断|ww、x|—；11；N|
|36命令回答有界保存|s、d|SH；14|
|37心跳膨胀核对|B/store/、B/service/、F/macro-task-timeline-layout.ts|—；36|
|38维护设置状态|fr、roles.ts、t|—；21|
|39画像对应操作|fm、t|—；23|
|40领域二维表|F/buddies/Evaluations（新）、t|—；21|
|41三面记录证据|fd、routing-display.ts、t|—；20–24|
|42结束回收急停入口|fs、TaskDetails、console-session.ts|—；10、14|
|43 credits展示|F/host-workflow.ts、buddies/HarnessDetails|—；25|
|44退役buddy移除|E/evaluation、B/catalog/catalog、F/buddies/|S；Router完成、第4步、决定|
|45探索设置展示|fr、t|—；24|

接口：01声明资格证据共表；06同列表预算、停止确认再切换，边界一次判工具及副本；22无强制不声明；31/32先证原生路径。依ADR-025§12数据库归B、进程归W、契约归P。

07正数默认1800、拒旧字段、命令不硬停；08用事件序号每次提醒早返；09阈值默认15分钟，只看原生活动、未完成工具。11取消记录来源、宽限截止、结果供恢复及Host；原会话总结、五分钟强停，停止未知不续接，不能续接只强停封存，命令无总结。

12委派权存微任务、名单存设置，首个可用、两层模型不同；13凭执行凭据只开放名下Host操作，禁共享设置、安装、再授予；14复用workflow_children及owner fencing，换手归属不变，Lead放开档同启动器、检出整合、等待占名额、不足上报；15缺陷关联供17及时间轴，终态不重开；授予说明写指南及设置页。

17回合记真实buddy、当时公开条目，基础设施失败单列；18对应表记名称条目、来源依据日期，精确匹配或候选内有据建议，可撤回且不改样本；19按harness＋模型＋强度查询统计，旧代次级证据，不存计数；领域归类供维护二维表，评价存引用归纳，备注画像属模型。

21默认1小时、每批50从最早起、成功后接着跑到读完、失败等下一间隔，12行两层七类，空包不调用不补周期（ADR-021 第 8 条 2026-10-20 改）；沿Router attempt/plane；21/23一次校验格式、引用数字或链接日期，最多两轮、逐项写、失败留旧；23点击确认消耗、跳过已有，画像供路由面；24每10个合格短任务探索1次、少于5样本价格已知，首选标记供校验。

25原生计费余额拒绝事实，登录/提供方改名先保留；26模型未启动且停止确认后服务改排一次；28/29/34/35先交原因证据；36先证窗口，再显式完整回答/摘要供重放；37查s:2143以外的心跳生产者。

并行：01先行；02/04/05并行，03只改Claude可加入、改g顺序整合；06后10与22、17–19与22并行；w/s/d/g/x/t或升级夹具重叠顺序做，31/32与38–41/45先后，42/43并行；文件变动重核。

最短路径：Lead 01–16（16个）；路由面01–07、17–20、22/23/25（14）；维护01–06、17–19、21/38（11）；画像01–06、17/18/22/23/39（11），须解冲突8且至少一家强制只联网。三面并集01–07、17–23、25/38/39（17），共前置01–06；Lead推后路由知识，Router推后08–16；探索展示、诊断瘦身后排，44第4步。闭环须Host验收安装；Lead领域评价待17–21。

路由额度逐条归属：登录/提供方改名25；知识17–19/23；删除44；续接26；旧Codex目录42现有回收；suggestedBuddyId27；Router自相矛盾20证据、17样本，不加语义裁判；异常目录/账户29；其他顺序待办10/15/28/30–37。

## Host验收（推断）

逐项读差异、跑受影响测试；整合后完整Python，F前端全量，布局真实浏览器。S在数据副本演练升级恢复、比空主目录新板及设置；升级消费后先更新基点夹具。

N项逐次授权，在空主目录或授权配置副本跑，不碰真实凭据。11验总结、强停、不能续接、急停竞态及停止未知；13/14验越权换手过期凭据、名额、名下取消回收整合；25/28/29验账户未知不换目录、明确拒绝移出、新观察恢复，不输出凭据。

08/09验不停及提醒不漏重、长工具事实；15验终态缺陷；17–24验基础设施单列、无样本回退、错引用、逐项留旧、停止后切换、撤回换代；26验无重复启动；33不替Host结论；34/35交测量；36/37副本比重放、时间轴体积；42/44删除先计划确认停止。最后空主目录经安装路径验固定运行时、资源、启动器，授权真实Lead/Router回合记录装后边界。

## 要开发者定（推断）

1. Router缺强制只联网时整体换手或画像停下，代价分别是路由面换手、不能画像。
2. 30保留ZCode探测或取检测版本，代价为多进程或旧版本，DSH握手非发行号。
3. 18备注冲突取最新或交用户处理，代价为丢备注或多操作。
4. 44手删或确认缺席后自动收起，代价为操作或隐退规则，均保留历史。
