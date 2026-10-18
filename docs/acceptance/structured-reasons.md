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
- **返修（2026-10-18）：** 参考文档对照源码核对时发现，`await` 在 Host 边界给出的命令没跟上：decline 仍带 `reason`，指定 buddy 的 continue 缺 `note`，Host 照发会被拒。Host 用看板自己的校验复现后，提交返修微任务“await 边界命令的 note”（run `64144256`，`fixes` 本项，Codex CLI `gpt-6.1-sol / high`，一个回合；输入 142 万，其中命中缓存 132 万，输出 0.7 万）。它改了这两条模板，并加了一条测试：在私有看板上让 6 类 Host 边界各自生成命令，填上占位后经看板真实的校验与事务执行。合在 `socu/batch2` 上（`bc5ae487`）；受影响的 5 个测试文件 142 项通过；把模板换回旧样子，新测试正好失败 5 处。看板上验收 `int-5aa541eb`。
- **补漏（2026-10-18）：** 同一次核对还发现，用户给 buddy 设偏好时写的说明仍叫 `reason`，照术语表是备注；第 2b 项的清点没有列到它。微任务“用户偏好的备注”（run `644a0783`，Codex CLI `gpt-6.1-sol / high`，一个回合；输入 666 万，其中命中缓存 647 万，输出 2.6 万）把存储、发布、读取、冻结进 Router 输入的排除名单与控制台都改成 `note`，在 24 → 25 升级里转换（空串转 null），顺带让路由健康汇总给出原因对象、浏览器检查的截图不再写进受管文件。Host 在日常副本上与改动前的批次二对照升级：只差在偏好表（4 条空串转 null，2 条原文保留）与两张路由快照表里各 240 项排除名单（空串的 `reason` 转为 null 的 `note`）；0.42.0 起这三种快照的 `reason` 都是用户写的说明。去掉 `programSelection` 那段转换，升级测试失败。Python 60 个文件 1003 项、前端 1051 项与浏览器 385 项通过。看板上验收 `int-769037cc`。
- **补漏的返修（2026-10-18）：** 完整检查里原因的静态检查失败：路由健康汇总用 `make_reason(resolution.problem["code"])` 拼出原因，动态的代码不来自字面量的映射。返修微任务“路由健康的原因不用动态代码”（run `f6fac9be`，`fixes` 上一项，经路由分到 DSH CLI `deepseek-flash / max`；输入 339 万，其中命中缓存 330 万，输出 2.5 万）让 Router 解析的 `problem` 在三处源头直接写成原因对象，各处原样传出；顺带删掉的 `configuration()` 外的 `try/except` 走不到（它只读两张表，不抛 `BoardError`）。原因、路由、评估、控制台与主机预览 49 个文件 790 项通过，看板上验收 `int-3a176598`。

## 微任务 3a：决策与路由关注的存储

- **拆分：** 2026-10-18 Host 给第 3 项写说明前清点了读写点，并在日常看板的私有副本与样板库上数了存量，按存储与投影拆成 3a 与 3b（[计划](../design/structured-reasons-plan.md)第 6 节）。Router 回答改名 note 之后，按回答的键读已存回答的三处会成两种读法，所以 Router 的文字在采用时存进决策自己的 note 列，原始输出与回执不改。
- **微任务：** “原因3a：决策与路由关注的存储”（run `8f242c12`，Codex CLI `gpt-6.1-sol / high`，四个回合）。基点 `17e4f81b`，最终输出 `cad25242`，合并在 `socu/batch2` 上（`08329cb7`、`71e799fe`）。第一回合停在范围边界：回答改名后探针 `tests/probes/router_readonly_tools.py` 仍按旧键读，Host 把它补进范围。第二回合进行中，Host 用问询告诉它两件事：在日常副本上试跑第一回合的封存时，`attempt.uncertain` 的旧措辞让升级失败，作者应按事件种类认；Host 说明里“原因只带它的错误代码”有歧义，它把 9 个大写错误代码登记成了原因代码，应作参数。两条都在该回合改了。第二回合交付后，Host 的核对与 Opus 独立审查找出的问题打回（见下），第三回合改完；合并后的完整检查又发现 Claude harness 的测试夹具仍是旧回答形状，第四回合改完。
- **做了什么：** `evaluation_decisions` 的 reason、error 两个混合列换成 `reason_code`、`reason_params_json` 与 `note`。黑板写的原因各用一个代码：程序直接选定 `single-candidate`、`no-legal-candidate`、Router 不可用的六个细代码（带健康或目录的具体原因作事实）、`router-input-too-large` 带字节或条数、`router-out-of-bounds` 带 bound 等。Router 的文字只在采用或弃选时进 note，弃选另记 `router-abstained`。新派发的回答是 `{buddyId, note, evidence}`（协议 13），已存的原始输出、回执与 dispatch 文档不改，也不再按键读。`router_history` 删掉运行时按旧形状分类决策事件的那条路，健康摘要只读结果事实与决策的原因，样本只取到达过 Router 派发的决策，`unattributed` 删除；批次二的源码读日常副本时，那条路曾把 46 个已有结果事实的决策全部报成无法归属。`workflow_routes.reason` 删除；路由关注经共用的系统请求写入函数写，请求表加 `decision_id` 列与第三种形式（只引用决策）。派发前校验失败用 `dispatch.buddy-rejected`，错误代码作参数；接管后的关注用 `routing-taken-over`。取消与释放的原因转代码，`attempts.error` 只放诊断，发布失败的诊断进 `execution_issues`。没有读者的 `workflow.attempt_released` 事件与 `events_router_outcome_idx` 索引删除。
- **独立审查：** Opus 5.5（Claude Code CLI，看板上的审查微任务 `b94f2c29`，16 分钟；输入 2150 万，几乎全部命中缓存，输出 8.7 万）。阻塞的有：旧写入方能写出的三种记录让升级失败（领取时 Router 用尽、发布时抛 BoardError、排队取消事件的操作者存在列里）；先取消后释放的决策被认成释放原因；六处新写法与升级给出不同的代码；健康统计把从没调用 Router 的决策算成 Router 失败。另有一个没有读者的索引、一个只有一个使用者的开关、没有写入方的转换分支。排队取消事件那条 Host 已先独立复现；其余 Host 在日常副本里注入旧写入方会存下的值逐条复现后，连同自己发现的两条（改道后没有候选时新旧代码不一，接管后的关注引用了错的决策，后者是 Host 说明定错了）一起打回第三回合。
- **核对：** 差异读过。日常看板同一时刻的两份私有副本，分别用基线 `17e4f81b` 与候选源码升级，逐表逐行比对，只差预期的转换：46 个决策的 Router 文字逐字移进 note（46 对 46 相等），决策事件里的副本与 `queued for a Router worker`、两种措辞的 `attempt.uncertain` 说明删除，快照里取值为空的 inspection reason 删除，路由表删掉的 reason 都是决策原因的副本（46 对 46）；其余各表逐字节相同；schema 25，完整性通过，外键为空。样板库里程序直接选定的决策转成 `single-candidate`。故障注入：作者不明的三种情形升级拒绝，看板逐字节不变；排队取消、领取时用尽、发布时抛错、提交时全不可用、先取消后释放，在最终输出上都按新写法转换。合并后的完整检查 278 个文件 4380 项，失败两处：已知的 README 版本行，以及上面那组旧回答夹具；夹具改好后 `buddy/roles` 与 `buddy/harnesses` 77 个文件 1600 项通过。看板上已验收（`int-da1a812d`），检出已回收。
- **接受的小处：** 发布时因输入过大而失败的旧决策，过大的那份文档当时没有存下，升级转 `history.reason-unavailable`；决策已完成却选出了不能用的组合时，若旧路由的原因是 Router 的文字，升级会失败，这条路只在不变量被破坏时才走得到。
- **顺带项：** 3b——`router_boundary_data.router_trials` 仍按 `output["decision"]["reason"]` 读已存回答，并把执行进程的诊断抄进条目；从记录拼原因对象的函数挂在 `DecisionCoordinator` 上，应放进 `protocol/reasons.py`。第 5 项——释放的原因只在 `attempt.released` 事件里，执行视图看不到。
- **消耗：** 四个回合输入 4430 万，其中命中缓存 4342 万，输出 10 万；审查另计，见上。

## 微任务 3b：路由边界自己的文字

- **微任务：** “原因3b：路由边界自己的文字”（run `a592c335`，经路由，Router 选了 Codex CLI `gpt-6.1-sol / max`，一个回合）。基点 `08329cb7`，输出 `42e364ae`，合并在 `socu/batch2` 上（`8e628caa`）。没有存储改动。
- **做了什么：** 路由边界的条目、候选问题、续接问题与命令阻塞不再写句子，只带代码与事实：候选问题复用 `router-out-of-bounds` 加 bound，续接问题用代码（新登记 `micro-task-not-found`、`micro-task-not-owned`、`workspace-cleanup-started`、`workspace-ownership-lost`，已关闭的祖先沿用 `ANCESTOR_TERMINAL`），能由 blocked、停止证据、revision 与候选推出的不另给代码。条目不再按回答的键读已存回答，也不再抄执行进程的诊断，旧尝试不借用当前决策的原因。续接模板删掉写死的 input，新模板的 commandId 随之变化，已存命令与重放不动。从记录拼原因对象只剩 `protocol/reasons.py:reason_from_json` 一处，路由请求的原因由 `tasks/request_reasons.py` 一处解析。
- **核对：** 差异读过。交付时它没能跑 `cli/test_host_preview.py` 的前端解析项（检出里没有控制台依赖），Host 在合并后的树上跑通。受影响的 routing、tasks、evaluation、catalog、store、cli、protocol、console 共 158 个文件 2437 项通过。看板上已验收（`int-e513decd`），检出已回收。
- **小处（接受）：** 两种检出归属问题（不再拥有冻结的分配、被别的所有者占用）合成了一个代码；`router-out-of-bounds` 的 bound 在发布与候选两处粒度不同（发布处合为 constraints-or-quota，候选处分开）。
- **消耗：** 输入 880 万，其中命中缓存 859 万，输出 4.1 万。

## 微任务 4：问询与等待原因

- **清点：** 2026-10-18 Host 写说明前清点了读写点，并在日常看板的私有副本上数了存量，更正了计划里第 4 项的事实：进度里的等待原因只有执行进程的代码 `router-input-unknown`，ZCode 的英文写在活动里，DSH 从不写这个字段。同时找到一个缺陷：执行结束时的清理把仍在排队的问题标成“没送到”，问询日志随后才导入回答，于是一条已回答的消息（日常看板上的 mt3a-q2）带着这句不实的原因。
- **微任务：** “原因4：问询与等待原因”（run `68cd1a06`，Codex CLI `gpt-6.1-sol / high`，三个回合）。基点 `01e71407`，最终输出 `7eeb0535`，合并在 `socu/batch2` 上（`e84d6f03`、`42cbc084`）。第一回合进行中，开发者定了删掉公开的 `message` 与 `message-update`（产品里没有调用者，操作者与作者由调用方自填）；Host 用问询告诉它本回合不加 note 列、认不出的文字让升级失败。删除要改契约与传输两个文件，不在第一回合的写入范围里，Host 修订范围后由第二回合做完。第三回合改 Host 核对与 Opus 独立审查找出的问题（见下）。
- **做了什么：** `messages.reason` 换成 `reason_code` 与 `reason_params_json`，CHECK 写明只有 delivered 与 unavailable 带原因，unavailable 一定带原因；记下回答时清掉原因。登记 `inquiry-no-live-agent`、`inquiry-queued-at-attempt-end`（只说执行结束时它在看板上还在排队，不说没送到）、`inquiry-observe-only`、`inquiry-answer-empty`、`inquiry-root-turn-ended`、带 errorCode 的 `bridge-refused`，日志记录被拒的 `journal-task-mismatch`、`journal-attempt-mismatch`、`journal-attempt-unbound`、`journal-question-unknown`、`journal-record-invalid`，以及等待原因 `router-input-unknown` 与 `native-authority-required`（kind 为 permission 或 user-input）。问询桥不再在日志与帧里写原因句子和 limitation 说明，服务凭状态写原因。`inquire` 的回复删掉 `note`、能推出的句子与 `live.reason`，投递方式照帧给出（含 realtime），桥的诊断放 `live.error`，不合格的日志记录进 `journal.rejections`。两种等待原因在服务收到处与固定的几个值比较一次；值不变时不写列与事件，原来每次 worker 启动都追加一个空的 `attempt.waiting`，日常副本上有 239 个。日志里的回答只剩嵌套对象一种读法；原来靠吞掉 `BoardError` 实现的“看板上没有的问题不建消息”改成明确判断。公开的 `message`、`message-update` 与 `message_wait` 删除，`inquire`、`messages`、`message-get` 保留。
- **独立审查：** Opus 5.5（Claude Code CLI，看板上的审查微任务 `8a21ec6b`，16 分钟；输入 1655 万，全部命中缓存，输出 7.5 万）。阻塞的一条：升级把已回答消息上调用方写的文字无声丢掉，Host 注入复现；起因是 Host 说明第 7 条的“已回答的消息清空”与同一条的“认不出就失败”自相矛盾。一并打回的：句子只要求有某个程序写入者，没有绑到写它的那一个；截断前缀没有真实的写入；实时问询的投递方式被投成 null；`live.reason` 里放的是诊断；一条不合格的日志记录会让这次执行之后的每次 `inquire` 都失败；到不了的检查与不用的参数；与计划第 2 节相违的通用原因校验器。审查另指出控制台任务详情仍按字符串读消息的原因，写进批次三；两侧的日志读取器规则不同、新消息的作者几列成了常量，写进待办。
- **核对：** 差异读过。日常看板同一时刻的两份私有副本，分别用基线 `01e71407` 与最终源码升级，逐表逐行比对，只有 messages 不同：列的变化，3 条早于 0.43.0 的问题在已结束的执行上从 queued 改成 unavailable 并带 `inquiry-queued-at-attempt-end`，mt3a-q2 那句不实的原因清掉；其余各表逐字节相同，schema 25，完整性通过，外键为空。故障注入按 0.42.0 到 0.45.3 的写入方会存下的形状造数据，共 24 种：程序句子、0.42.0 存成原因的 DSH 与 ZCode 说明、Claude Code CLI 的关闭变体、等待回答时留下的无原因 unavailable、两种等待原因，都按新写法转换；调用方写的文字（含已回答消息上的）、写入方不对的句子、认不出的文字与等待原因都让升级拒绝，看板逐字节不变。第二回合与第三回合合并后各跑一次完整检查，最后一次 279 个文件 4425 项，只有已知的 README 版本行没过。看板上已验收（`int-4ff930b4`），检出已回收；审查微任务按只留证据结束（`int-501faf3a`）。
- **小处（接受）：** DSH 的 Node 桥（2026-10-07 删除）留下的旧日志之后读不进来，看板上已经导入的不受影响。
- **消耗：** 三个回合输入 2188 万，其中命中缓存 2155 万，输出 8.3 万；审查另计，见上。

## 微任务 5：剩下的固定说明与顺带项

- **微任务：** “原因5：剩下的固定说明与顺带项”（run `eb0b31be`，Codex CLI `gpt-6.1-sol / high`，三个回合）。基点 `b445aac6`，最终输出 `cf1ad303`，合并在 `socu/batch2` 上（`c69c8759`、`c6e0fc7c`）。第一回合停下请 Host 跑前端解析的检查（它的检出里没有控制台依赖），Host 在合并后的树上跑过，3 项通过。第二回合改 Host 核对与 Opus 独立审查找出的问题，第三回合补上最后一处升级的认法（见下）。
- **做了什么：** 回复里删掉 `waitReason`、`capabilities.limitations`、CLI 等待结果与配置报告里写死的说明、存储分类与时间轴标记的中文名称和拼起来的标记摘要（标记改给 decision、verdict、state、strategy 与 note 几项事实），没有标题时为 null。控制台入口失效时按原状态码返回带标记 `console.entry-unavailable` 的主页面，可信 loopback 的请求在认证之前可以取构建资源（主应用一侧随批次三）。执行详情的 `selectedAttempt` 给出释放的原因。第 1 项留下的顺带项：目录刷新失败记 `catalog.discovery-failed`（带错误码），删掉几处编出来的缺省原因，`native-metadata-wait-expired` 登记成代码；目录发现的回执从子进程进服务时在接收处校验一次，服务自己记录与读取时只做规整。实现方还发现两处写入仍会写出没有根原因的健康记录，改了写入，并扩展了升级。
- **独立审查：** Opus 5.5（Claude Code CLI，看板上的审查微任务 `b7dd2815`，13 分钟；输入 986 万，全部命中缓存，输出 6.6 万）。最严重的一条：目录的校验放在了服务自己也用的记录处，拒收服务写的 `catalog.account-status-unknown` 等原因，控制台的“刷新目录”会因此失败。另有：升级认不出复制进服务载荷的超时原因、Codex 账户读取恢复之后的 ready 行；观察快照里的健康副本与另四种 harness 事件留下两种形式；服务载荷里复制来的看板代码被转成原生内容；两处没有真实输入的防御；`releaseReason` 的 null 在不同回复里含义不同，查询扫整张事件表。Host 逐条复现后打回第二回合，连同 Host 自己发现的一处：存储分类的 `reasons` 汇总被删了，起因是 Host 的说明把“删掉 label”写成了“只留 id 与数字”，控制台的存储页还在读它。第二回合之后 Host 又按 0.42.0 的 `read_health` 造了快照项：没探测过的 harness 在快照里是带视图字段的形状，升级仍会失败，打回第三回合。
- **核对：** 差异读过。故障注入按 0.42.0 的健康写入方造数据：初始化、扫描占位、占位之后的未知探测补 `harness.not-checked`；有 `harness.invalidated` 证据的目录刷新失败转成 `catalog.discovery-failed`，健康记录与 buddy 一致；没有证据、或不健康却没有代码的拒绝，看板逐字节不变；快照里三种没探测过的视图补上原因并保留视图字段。同样的注入在第 5 项之前的源码上，会拒绝目录刷新失败过的正常看板、让没有根原因的行留空，这两处由本项修好。日常看板同一时刻的两份私有副本分别用 `b445aac6` 与最终源码升级，只差 4 个 `harness.checked` 事件的 `reasonCode` 转成了原因。合并后的完整检查 279 个文件 4447 项，只有已知的 README 版本行没过。看板上已验收（`int-ef3d5ff2`），审查微任务按只留证据结束（`int-2ab6d1e2`）。
- **小处（接受）：** 构建产物缺失时，过期入口得到的是 JSON 的 500，不是页面（只在没构建的开发检出里出现）。
- **消耗：** 三个回合输入 3230 万，其中命中缓存 3201 万，输出 9.1 万；审查另计，见上。
