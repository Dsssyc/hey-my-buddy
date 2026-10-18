# 有结构的原因：验收记录

第 4 步批次二的一部分，见[第 4 步的执行计划](../design/step4-remaining-plan.md)与[有结构的原因的执行计划](../design/structured-reasons-plan.md)。各微任务都在宏任务“第 4 步：控制台余下的事”里，实现都在 `socu/batch2` 上，随批次二的 0.46.0 安装。

## 执行计划（2026-10-17）

- **第 1 版：** 微任务“有结构的原因的执行计划”（run `e2dcfd09`，Codex CLI `gpt-6.1-sol / high`），只写计划。
- **两份审查：** Host 与一个独立审查者分别审，互不看对方的结论。独立审查是微任务“有结构的原因的计划：独立审查”（run `99401c99`，Claude Code CLI `claude-opus-5-5 / high`），审查全文留作 Host 的证据，没有进仓库。两份的方向一致：第 1 版铺得太大。独立审查者另外指出存储形式与升级被拆在不同的微任务里，违反 `AGENTS.md`；同一个事实两套代码；等待原因可以由状态推出；黑板写的请求照 Worker 的样子填四个字段；同一个路由原因存了好几份。入口失效页采用独立审查者的办法（由主应用按 meta 标记画），比 Host 原想的双语静态页少一份文案。
- **第 2 版（`654dd475`）：** 接受，入库 `2410631a`。它用源码纠正了独立审查的一处说法：`router-unavailable` 没有被逻辑当作分类使用，可以直接换成细的代码。Host 改了一句（`04fe1805`）：升级里必须匹配的旧中文句子照原文写，服务源码的守卫明确豁免 `upgrade.py`，不用 Unicode 转义绕过；这一次升级被消耗之后，转换器连同这些句子一起删除。
- **消耗：** 独立审查输入 674 万，全部命中缓存，输出 4.3 万。

## 微任务 0：最小的原因接缝

- **微任务：** “原因0：最小的原因接缝”（run `05528030`，DSH CLI `deepseek-flash / max`，两个回合）。
- **基点与合并：** 基点 `cb88a28c`。第一回合 `f65239e1` 停在一个边界上：写入范围之外的一个冷启动测试写死了运行时资源的集合，实现方报告而没有越界去改；Host 把那个文件加进写入范围，第二回合 `7b3feceb` 只补了那一项。合并在 `socu/batch2` 上（`629ad512`）。
- **做了什么：** `protocol/reasons.py` 的 `reason(code, **params)`；空的代码表 `protocol/reason-codes.json`，作为运行时资源打包；一条静态测试按 AST 检查源码里对 `reason()` 的每次调用：代码必须是已登记的字面量，或来自值全是字面量的映射表，参数名与登记的一致；反例写在临时源码上。
- **核对：** 差异读过；合并后 protocol、打包、分发、运行时与冷启动的 22 个文件 357 项通过。
- **消耗：** 两个回合输入 408 万，其中命中缓存 384 万，输出 3.6 万。

## 微任务 1：健康与目录

- **微任务：** “原因1：健康与目录”（run `9de38568`，Codex CLI `gpt-6.1-sol / high`，三个回合）。
- **基点与合并：** 基点 `629ad512`。第一回合停在边界上：读这些字段的地方有四处在写入范围之外（`evaluation/evaluation.py`、`routing/router.py`、`service/service.py`、`buddy/runtime/execution.py`），Host 补进范围。第二回合 `9ba19878` 在日常看板的副本上升级失败：“Buddy unavailable reason lacks catalog provenance”。日常看板有 16 个 buddy 存的是目录确认缺失的那句原文，而当初写下它的发现记录已被后来的读取替换，转换器找不到出处就拒绝。Host 把日常数据的分布交给实现方后打回；第三回合 `e0cd47b5` 让与常量逐字相同的句子不凭出处直接转换，并补了回归测试。合并在 `socu/batch2` 上（`4c573258`）；`codex/native_run.py` 与运行控制器的发现测试同时带着会话删除的改动，验收时声明为合并的结果。
- **做了什么：** `buddies.unavailable_reason` 拆成 `unavailable_code`、`unavailable_params_json`、`unavailable_content` 三列，两条 CHECK 约束：代码与参数同有同无，内容只跟着原生声明的不可用。健康记录、候选、最近一次探测与账户读取失败里的 `reasonCode`、`remedy` 与旧说明合成一个 `reason`；账户视图的 `reasonCode`、`guidance` 同样合成 `reason`。目录的警告与发现原因改成有结构的原因，原生文字是内容；`catalog_current.reason` 存原因的 JSON；刷新成功的 note、可由状态推出的固定警告、额度警告的 message 删除。登记 48 个代码。
- **核对：** 差异读过。升级在日常看板的只读备份副本上跑过：0.035 秒，schema 25，完整性检查通过，外键检查为空；buddy 122 个没有原因，16 个是 `catalog.model-absent`；52 条目录读取记录删去 note、补上空的不可用内容，39 条固定警告删去（三种各 13 条）；4 条健康记录的 `reasonCode`、`remedy` 换成空的 `reason`。副本里含 `history.reason-unavailable` 字样的 10 个单元格都是 Host 的任务书与 Worker 的结果里引用这个代码的原文，属于内容，没有一条是转换出来的。合并后的 184 个文件 3101 项里只有 `blackboard/routing/test_stage2_review_scope.py` 失败，原因是会话删除那个微任务改了运行控制器，见[删除宏任务的验收记录](macro-task-deletion.md)，与本微任务无关。
- **顺带项（下一个原因的微任务）：** 目录刷新失败时健康记为 `harness.launch-failed`，应记 `catalog.discovery-failed` 并带上错误码；`read_health` 读出时、探测合并时各补一次空的 `reason`，所有写入都已带着它，两处默认值删除；`run_execution.discover_models` 等待到期时仍写字符串原因 `native-metadata-wait-expired`，登记代码之后，`canonical_payload` 接受字符串原因与字符串警告的分支一并删除；`capability_report` 对从未探测过的 harness 编出 `harness.probe-failed`，应取健康记录的原因或 `harness.not-checked`。
- **消耗：** 三个回合输入 1706 万，其中命中缓存 1680 万，输出 3.3 万。

## 微任务 2a：系统请求

- **拆分：** 2026-10-18，Host 给计划的第 2 项写说明之前清点了读写点，把它拆成 2a（系统请求）与 2b（备注与黑板的缺省句子），路由请求移到第 3 项（[计划](../design/structured-reasons-plan.md)第 6 节）。控制台的任务详情要求请求的 summary 与结论的 note 是字符串，所以 0.46.0 与批次三一起装。
- **微任务：** “原因2a：系统请求”（run `c058d176`，Codex CLI `gpt-6.1-sol / high`，一个回合，17 分钟）。基点 `3efde4d3`，输出 `b6ad94e3`，合并在 `socu/batch2` 上（`92540af9`）。
- **做了什么：** `workflow_requests.summary` 改为可空，加 `reason_code` 与 `reason_params_json`，CHECK 写明内容与原因两种形式互斥。工作区越界、继续准备失败、Lead 拒收与 helper 汇报经一个共用的写入函数写一个原因及其事实，不再写 summary、attempted、neededWork、expectedArtifacts、acceptance 这些程序句子；Lead 不再拼假的回合。自动续接被拒时，错误代码经一张六项的有限映射成为原因，映射之外的代码直接失败。代理照抄来源的形式。完整视图总有 summary 与 reason 两个键，恰好一个非空；时间线的等待片段给内容或原因；`workflow.lead_result_refused` 事件去掉理由句子。升级按现有标记转换存量，代理随来源转，Worker 的内容逐字不动，helper 汇报的代码认不出时升级失败。登记 10 个代码。路由请求照旧，由第 3 项改。
- **核对：** 差异读过。两处行为变化接受：系统请求经 `_sync_boundary` 选当前请求，run 已有打开的当前请求时它不变（原来新请求顶替它）；这几种请求打开时多记一条 `workflow.boundary_updated`。升级在日常看板的只读备份副本上跑过：0.04 秒，schema 25，完整性检查通过，外键检查为空；44 条请求里 1 条工作区越界转成原因，43 条是 Worker 的内容，逐条对得上各自回合的结果，转换前后逐字不变，没有转成 `history.reason-unavailable` 的。合并后的完整检查 276 个文件 4325 项，只有 `install/test_first_install.py` 的 README 安装命令一项失败：批次二的版本已是 0.46.0，README 还写着 0.45.2，与本微任务无关，批次二合入整合分支时改。看板上已验收（`int-8933d008`），检出已回收。
- **消耗：** 输入 781 万，其中命中缓存 760 万，输出 4.4 万。

## 微任务 2b：备注与黑板的缺省句子

- **决定：** 2026-10-18 开发者定，Host、用户与 Router 自己写的文字叫备注（note），不叫 reason（术语表“备注 / note”）。2b 因此从“结论与停止说明”改为把 Host 与用户写的 reason 全部改名，并删掉黑板替缺失的备注写的句子（[计划](../design/structured-reasons-plan.md)第 6 节）。
- **微任务：** “原因2b：备注与黑板的缺省句子”（run `a457ab98`，Codex CLI `gpt-6.1-sol / high`，五个回合）。基点 `92540af9`，最终输出 `f853f623`，合并在 `socu/batch2` 上（`bbadc215`、`d560e1f2`）。第一回合停在范围边界上：`protocol/client.py` 的 retry 仍发旧参数；Host 顺着查到安装器在切换前给正在运行的旧服务发 `reason: "coordinated installation"`，装 0.46.0 时对面是只认 reason 的 0.45.3，发 note 会被拒且失败被吞掉，于是把这两个文件补进范围，安装器改为只发动作与等待秒数。第二回合报出旧的完全权限清单的冲突，Host 定为新清单升到第 3 版、不再带说明，第 1、2 版按版本校验，不改写；同时删掉路由边界模板里黑板替 Host 写的 note，补上 `signal`。第三回合合并后，Opus 独立审查找出两处会丢或改错数据的问题（见下），Host 核实后打回。第四回合 Codex 中途报错（harness-error，日志为空，额度正常），第五回合续做完成。
- **做了什么：** decide、continue、cancel、宏任务停止、scope-amend、workspace-resolve、服务控制、execution-cancel、execution-retry 的 reason 参数改为 note，`executionWorkspace.accessReason` 改为 `accessNote`，旧名一律被拒，备注上限只留 `MAX_NOTE_BYTES`。记录里的同名列与键、事件、回复、Worker 回合输入一起改名；没写备注时黑板不写句子，谁做的由操作者、停止发起方与新补的事实（`leadRunId`、`continuationId`）说明。名下自动结论改为原因 `lead.owned-concluded`。`_requeue` 的闲置参数、`CANCELLATION_NOTE`、`CATALOG_REMEDY`、附着分支与 stash 的固定说明删除，BUDDY_UNAVAILABLE 的详情改为原因对象。工作区清单升到第 3 版、不带说明；回合输入升到第 2 版，工作区处置记录升到第 3 版，旧记录不改写，读的一方按版本认。升级按真实的写入者转换存量：取消事件按 `events.actor_role` 认，缺省句只在真正写过它的那一处删，空串一律为 NULL 或 JSON 的 null。
- **独立审查：** Opus 5.5（Claude Code CLI，看板上的审查微任务 `b0c53190`，13 分钟；输入 1338 万，几乎全部命中缓存，输出 6.4 万）。两条高：存下的事件载荷里没有操作者（`_append_event` 把它移进 `events.actor_role` 列），升级按载荷判断的分支从不成立，黑板写的取消原因会被当成备注；一个全局的缺省句集合在每一列都删，会删掉 Host 恰好写成同样字句的内容，最坏时完全权限微任务的说明被删、再也跑不起来。另有空串与 null 并存、`review_rejection_note` 含义变化、改了键的封存记录没升版本号、自动结论的认定层层叠加、一个没有使用者的重建分支。Host 逐条核实后全部转给第四、五回合。Host 先前在日常副本上的核对没有发现前两条，因为核对脚本照搬了升级自己的规则。
- **核对：** 差异读过。日常看板的只读副本上，用从旧写入者独立推出的期望值逐条比对：schema 25，完整性通过，外键为空；Host 写的字逐字保留（续接 2、范围 10、工作区冲突 1、验收 23、回合的权限说明 164、事件 6），缺省句只在真正写过的地方删（Lead 唤醒续接 6、范围第 1 版 131、Lead 结论引起的停止 1，后者补上 `leadRunId`），96 条已核对验收的空串转为 NULL，问题 0。库里与磁盘上的第 1、2 版工作区清单用新校验全部通过（第三回合核对）。合并后的完整检查 278 个文件 4358 项，只有已知的 README 版本一项失败。看板上已验收（`int-bbae59b1`），检出已回收。
- **顺带项（第 3 项）：** 没有操作者、`reason` 为 null 的旧 `task.cancelled` 事件留着 `reason` 键；服务写的取消原因与决策取消原因转原因代码。
- **消耗：** 五个回合输入 5063 万，其中命中缓存 5003 万，输出 9.3 万；审查另计，见上。

## 微任务 3a：决策与路由关注的存储

- **拆分：** 2026-10-18 Host 给第 3 项写说明前清点了读写点，并在日常看板的私有副本与样板库上数了存量，按存储与投影拆成 3a 与 3b（[计划](../design/structured-reasons-plan.md)第 6 节）。Router 回答改名 note 之后，按回答的键读已存回答的三处会成两种读法，所以 Router 的文字在采用时存进决策自己的 note 列，原始输出与回执不改。
- **微任务：** “原因3a：决策与路由关注的存储”（run `8f242c12`，Codex CLI `gpt-6.1-sol / high`，四个回合）。基点 `17e4f81b`，最终输出 `cad25242`，合并在 `socu/batch2` 上（`08329cb7`、`71e799fe`）。第一回合停在范围边界：回答改名后探针 `tests/probes/router_readonly_tools.py` 仍按旧键读，Host 把它补进范围。第二回合进行中，Host 用问询告诉它两件事：在日常副本上试跑第一回合的封存时，`attempt.uncertain` 的旧措辞让升级失败，作者应按事件种类认；Host 说明里“原因只带它的错误代码”有歧义，它把 9 个大写错误代码登记成了原因代码，应作参数。两条都在该回合改了。第二回合交付后，Host 的核对与 Opus 独立审查找出的问题打回（见下），第三回合改完；合并后的完整检查又发现 Claude harness 的测试夹具仍是旧回答形状，第四回合改完。
- **做了什么：** `evaluation_decisions` 的 reason、error 两个混合列换成 `reason_code`、`reason_params_json` 与 `note`。黑板写的原因各用一个代码：程序直接选定 `single-candidate`、`no-legal-candidate`、Router 不可用的六个细代码（带健康或目录的具体原因作事实）、`router-input-too-large` 带字节或条数、`router-out-of-bounds` 带 bound 等。Router 的文字只在采用或弃选时进 note，弃选另记 `router-abstained`。新派发的回答是 `{buddyId, note, evidence}`（协议 13），已存的原始输出、回执与 dispatch 文档不改，也不再按键读。`router_history` 删掉运行时按旧形状分类决策事件的那条路，健康摘要只读结果事实与决策的原因，样本只取到达过 Router 派发的决策，`unattributed` 删除；批次二的源码读日常副本时，那条路曾把 46 个已有结果事实的决策全部报成无法归属。`workflow_routes.reason` 删除；路由关注经共用的系统请求写入函数写，请求表加 `decision_id` 列与第三种形式（只引用决策）。派发前校验失败用 `dispatch.buddy-rejected`，错误代码作参数；接管后的关注用 `routing-taken-over`。取消与释放的原因转代码，`attempts.error` 只放诊断，发布失败的诊断进 `execution_issues`。没有读者的 `workflow.attempt_released` 事件与 `events_router_outcome_idx` 索引删除。
- **独立审查：** Opus 5.5（Claude Code CLI，看板上的审查微任务 `b94f2c29`，16 分钟；输入 2150 万，几乎全部命中缓存，输出 8.7 万）。阻塞的有：旧写入方能写出的三种记录让升级失败（领取时 Router 用尽、发布时抛 BoardError、排队取消事件的操作者存在列里）；先取消后释放的决策被认成释放原因；六处新写法与升级给出不同的代码；健康统计把从没调用 Router 的决策算成 Router 失败。另有一个没有读者的索引、一个只有一个使用者的开关、没有写入方的转换分支。排队取消事件那条 Host 已先独立复现；其余 Host 在日常副本里注入旧写入方会存下的值逐条复现后，连同自己发现的两条（改道后没有候选时新旧代码不一，接管后的关注引用了错的决策，后者是 Host 说明定错了）一起打回第三回合。
- **核对：** 差异读过。日常看板同一时刻的两份私有副本，分别用基线 `17e4f81b` 与候选源码升级，逐表逐行比对，只差预期的转换：46 个决策的 Router 文字逐字移进 note（46 对 46 相等），决策事件里的副本与 `queued for a Router worker`、两种措辞的 `attempt.uncertain` 说明删除，快照里取值为空的 inspection reason 删除，路由表删掉的 reason 都是决策原因的副本（46 对 46）；其余各表逐字节相同；schema 25，完整性通过，外键为空。样板库里程序直接选定的决策转成 `single-candidate`。故障注入：作者不明的三种情形升级拒绝，看板逐字节不变；排队取消、领取时用尽、发布时抛错、提交时全不可用、先取消后释放，在最终输出上都按新写法转换。合并后的完整检查 278 个文件 4380 项，失败两处：已知的 README 版本行，以及上面那组旧回答夹具；夹具改好后 `buddy/roles` 与 `buddy/harnesses` 77 个文件 1600 项通过。看板上已验收（`int-da1a812d`），检出已回收。
- **接受的小处：** 发布时因输入过大而失败的旧决策，过大的那份文档当时没有存下，升级转 `history.reason-unavailable`；决策已完成却选出了不能用的组合时，若旧路由的原因是 Router 的文字，升级会失败，这条路只在不变量被破坏时才走得到。
- **顺带项：** 3b——`router_boundary_data.router_trials` 仍按 `output["decision"]["reason"]` 读已存回答，并把执行进程的诊断抄进条目；从记录拼原因对象的函数挂在 `DecisionCoordinator` 上，应放进 `protocol/reasons.py`。第 5 项——释放的原因只在 `attempt.released` 事件里，执行视图看不到。
- **消耗：** 四个回合输入 4430 万，其中命中缓存 4342 万，输出 10 万；审查另计，见上。
