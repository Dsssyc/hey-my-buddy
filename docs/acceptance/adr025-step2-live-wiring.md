# ADR-025 第二步微任务 2-C2：ZCode 实时通道接入实际消费者

2026-10-06，固定基线 `ace3e1dbb641005b081718347c7e75dbaa1a851d`。先完整读取 Host 固定说明 `~/.codex/worktrees/adr025-run-module/hey-my-buddy/tmp/adr025-host/step2-6npns9su/2c2-detailed-brief.txt`，SHA-256 为 `d136e0896723e3b7900e48465d9e5a75e3292499a47d42ceca0f7a0f2b983ce2`，与指定值一致；已读本检出 AGENTS、CONTEXT、ADR-007/023/025 与执行计划最新 2-C2 小节。隔离 worktree 独占写入，改动未提交，Host 统一整合；未再委派。

## 实际生产接线

共享传输：`blackboard/tasks/inquiry.py` 的 `bridge_request` 机械搬到 `protocol/inquiry_transport.py`（帧大小、id 关联、传输窗口、FileNotFound/ConnectionRefused→bridge-unreachable、Timeout→bridge-timeout、其他 OSError→bridge-write-failed、无效 JSON→bridge-invalid-response、错 id→bridge-mismatched-response、对端拒绝→bridge-refused 加 `BRIDGE_ERRORS` 内具体码，全部原样保留）；ZCode 在 `zcode/live_bridge.py` 的第二份客户端 `bridge_ask` 整体删除，DSH 消费者只改导入共用函数，行为未动。

通道格式（`buddy/harnesses/live.py`）：capabilities/request/observe/close 四方法保留。`observe` 增加字段选择 `fields`（activity/inquiries/observation，省略全取）与单个 `inquiry_id` 原生答案点查，点查只发一次 socket 的 `answer`，不读 sidecar 也不读 journal，与原直连路径的 I/O 量一致；通道公开只读 `identity` 属性，绑定值就是那次真实 `RunRequest` 的完整执行身份。`request` 接受共享传输的结果事实：`{"ok": false}` 的拒绝在 `reason_code`（传输分类）与新增 `error_code`（对端具体码）中并列出现，不再折叠。绑定接缝：`registry.live_binding(name)` 只在注册模块声明可调用 `bind_live_channel` 时返回它（本步只有 zcode/`native_run`），`roles/live.py` 的 `build_live_channel`/`handle_live_channel` 是黑板与 Worker 的唯一入口，泛用消费者未导入具体 harness。

黑板（`blackboard/tasks/inquiry.py`）：活的 ZCode 运行的 ask、observe 与问询等待、journal 投影全部经过 LiveChannel。通道只在注册绑定存在、尝试非终态、凭据在、且 `attempt_root(...)/role-run-request.json` 可解码并核对 task/attempt/generation 与 harness 一致时构建；缺失或不符一律回落原有直连路径，因此已结束旧执行的持久证据仍按既有规则读取（含公开 `journalRejected`），历史文件不被要求伪造新 invocation，原生程序不被唤醒。message_post/message_update、等待结束条件、store 事务与 `_live` 对外字段未重写；答案来源 live-bridge/bridge-journal、未接受/排队/送达/已回答、问询重放与错误对外保持。活运行的 journal 经绑定投影取得，不再绕接口重复读同一文件；该绑定保留 version 与 turnId 绑定，外来记录不会进入投影（`journalRejected` 因此只存在于读文件的直连路径，测试分别钉住两面）。

Worker（`buddy/runtime/worker.py`）：`_Renewal._forward_activity` 对注册 harness 经角色接缝从通道取当前 attempt 的 activity（`fields=("activity",)`，仍只转报更新，认领/租约/nonce/回执/取消逻辑未动）；通道由持有方 control 指向的公共请求文件解码并与所持完整身份核对后构建，成功一次后缓存，请求文件就绪前或未抽取 harness 保留原 sidecar 直读路径；传输失败只是"无更新"，从不判定原生已停。

## 字段增减及理由

新增且均有消费点：`LiveReply.error_code`（对端拒绝的具体码，直接供黑板 `bridge.error`）；`LiveSnapshot.observation/observed/reason/error`（观察值与共享传输事实 observed/reason/error，供黑板 `bridge.observed/reason/error` 无损投影）；`InquiryState.bytes/via/tool_call_id/at/truncated/reason/delivery`（答案来源与实际 reason/delivery，journal 记录有什么带什么，不推导）；`LiveEvent` 与 `LiveObservation`（仅承载现有 `_live` 读取的字段，kind 截 80 字符、toolName 截 120 字符按生产来源保留为字符界；桥快照的 `activity` 最近事件元数据更名 `recentActivity`，快照级 `activity` 仍是规范化 sidecar；桥自身 `limits`/`bridgeStartedAt` 无消费点，投影时丢弃而不藏入 JSON 槽）。`InquiryState` 的序列变更检测同步扩为全部携带字段。RunRequest/RunResult 公共字段、公开 CLI、数据库、其他 harness 行为均未改动。

## 聚焦验证与编号

材料根 `/private/tmp/a252c2-jy5bpl7e`（下称 `<task>`），`t/` 为 TMPDIR 与 BUDDY_CHECKS_TMPDIR，一次性材料在 `<task>/m/` 每次新目录（run1-baseline、run3-tests、run4-final、run5-m*）。测试均以基线 Host 的锁定 `~/.codex/worktrees/adr025-run-module/hey-my-buddy/.venv/bin/python` 运行，清除继承 BUDDY 运行时/Worker/凭据与 VIRTUAL_ENV 等变量，`PYTHONPATH` 指向本检出 src/tests 的逐字节副本（同时复制 packaging/uv.lock/harnesses/skills/pyproject.toml 以满足运行时资产清单），未安装依赖、未装升级运行时。真实 Python 桥（`InquiryBridge` 真实 Unix socket）贯穿黑板 `inquiry_observe`、共享传输、通道与 Worker 转报函数；未 mock 整条 LiveChannel，未跑完整检查/控制台/打包/安装版 harness/真实模型。

受影响模块八份：`protocol.test_inquiry_transport`、`buddy.harnesses.test_live_channel`、`buddy.roles.test_role_live_seam`、`buddy.roles.test_registered_run_wiring`、`buddy.runtime.test_worker_invariants`、`buddy.runtime.test_worker_runtime.WorkerLaunchEnvironmentTests`、`buddy.harnesses.zcode.test_native_run`、`blackboard.tasks.test_inquiry`。基线先行采集（运行前副本 101 项全绿，`<task>/m/baseline-ids.txt`），最终 `run4-final` 同范围 140 项全绿；`StagedWorkerRuntimeTests` 未运行：其断言不触及本次改动且为整步整合时的服务级用例。公开字段前后对照由两侧测试钉住：黑板 `bridge/live/inquiry/journal` 各字段在直连路径（原测试未动）与通道路径（新测试）同形。

编号对账（`<task>/m/after-ids-final.txt`）：删除 1——`buddy.harnesses.zcode.test_native_run.LiveBindingTests.test_a_foreign_reply_id_and_a_silent_socket_are_refused_and_bounded`，其错 id 与静默 socket 两个场景迁入 `protocol.test_inquiry_transport` 的 `test_a_foreign_reply_id_is_mismatched_not_accepted` 与 `test_a_non_json_reply_is_invalid_and_a_silent_socket_times_out`；原位修改断言 1——`test_the_full_answer_set_is_reachable_through_paged_observation` 的页数期望 2 改为 ≥2（2-C2 条目携带答案来源字段后 32×4000 字节答案多占一帧，逐帧上限与完整可达不变），编号未变；新增 40（新模块 transport 8、roles 接缝 5，live 通道 10，zcode 绑定 4，黑板 ZCode 通道 7，Worker 转报 6），其余 99 个编号与基线集合相等。

新增 40 项：transport——roundtrip、缺失/拒连 socket、拒绝带具体码、未知码归 internal、错 id、非 JSON 与静默超时、超大回复、传输窗口边界；roles——仅注册模块有 live 绑定、请求完整身份绑定、异 harness 拒绝与未注册 None、handle 缺失/不可读/外来/改名不绑定、自有请求绑定且令牌不入公共值；live——传输拒绝保留 reason+具体码且可重试、ok 结果解包含 delivery 元数据、不可达在 request 与 snapshot 上的事实、无效观察值报 unavailable 不重塑、点查拒绝的事实、字段选择全取/收窄/空选/非法、缺席来源诚实上报、点查不读 sidecar 与 journal 及非法 id 拒绝、journal 来源字段与 delivery 存活（含 Node 裸文本形状）、LiveEvent 字符界与多字节、新字段帧往返；zcode——通道投影保留答案来源与 delivery、真实桥观察含界外拒绝、点查仅原生答案（journal 指向陈旧文件仍答原生值）、拒绝 ask 带传输事实与具体码（not-ready/unreachable/unauthorized）；黑板——真实桥观察经通道、问询-应答-journal 全链、not-ready 保持 pending 并带具体码、无文本答案不判定 answered、外来请求保直连路径与 journalRejected、通道投影不报外来记录拒绝、外来 invocation 不绑定；Worker——通道转发且同 sidecar 不重复、外来 attempt sidecar 不转发、被篡改请求不绑定、未抽取 harness 原路径、通道不可用不是停止。

## 变异防护

四份隔离副本变异（生产源码未动，副本保留红态，配对绿即 run4-final）：M1 删去 `roles/live.py` handle 绑定的身份核对→`test_a_stored_request_of_another_invocation_never_binds`、`test_a_missing_unreadable_or_foreign_request_never_binds` 抓红；M2 把拒绝折叠回无具体码的 `bridge-refused`（旧重复客户端的缺陷）→transport 事实三项与 zcode 拒绝 ask 抓红；M3 让答案点查额外读 journal→`test_the_single_answer_point_query_reads_neither_activity_nor_journal` 抓红；M4 删去 journal 投影对前记录字段的延续→`test_journal_source_fields_and_delivery_survive_the_projection` 抓红。日志存 `<task>/m/run5-*/`。

## 未验证项与移交

未验证：真实已安装 ZCode app-server 下的端到端（本项只启动 Python fixture，真实运行留给 2-D 的最小私有原生冒烟）；Windows 与非 macOS 平台；`StagedWorkerRuntimeTests` 与完整检查套件（按约定不跑，整合后由 2-D 一次覆盖）。移交事项：本项新增的跨界导入需 Host 登记到 `docs/acceptance/adr025-step-0-cross-imports.tsv`（本项无该文件写权）——`blackboard/tasks/inquiry.py` 对 `hey_my_buddy.buddy.harnesses.live`、`hey_my_buddy.buddy.harnesses.run_contract`、`hey_my_buddy.buddy.roles.live` 的导入（前两者亦为通道帧类型与请求解码的必要引用）；桥快照 `bridgeStartedAt`/`limits` 两个无消费点字段在通道投影处被丢弃，2-D 使用方盘点可据此删减。

## 清理事实

检出内无一次性材料、无 `__pycache__`（测试全部在 `<task>/m/` 副本内以 `PYTHONDONTWRITEBYTECODE=1` 运行）；`git status` 仅含 writeScope 内 10 个修改与 4 个新增文件。未手动删除任何对象、未用通配符；测试内 `TemporaryDirectory` 与桥 `close` 各自正常收尾。等待 Host 验收后按确切根 `/private/tmp/a252c2-jy5bpl7e` 整体回收；其他会话材料未触碰，未读取凭据内容，记录中路径均为 `~` 或占位符。

## 第一次退回的撤回与修正（同日续轮）

上轮固定输出 `fbdf58161432afe1af1c9efe0f835f9b738a6d5b`（artifact `893ac504-c870-4505-9c5d-cba654a5f9aa`）被 Host 拒绝、未整合；Host 核对了全部 15 路径固定字节与范围、共享 `bridge_request` 与基线 AST 相同、140 项通过属实，但四组变异未覆盖下列真实消费者行为，140 项全绿因此不是真实消费者等价的证明——此结论撤回上轮的等价表述。续轮说明 `~/.codex/worktrees/adr025-run-module/hey-my-buddy/tmp/adr025-host/step2-6npns9su/2c2-continue-1-brief.txt`，SHA-256 `4262ee0fabce4f086c1df08067c97fedd0d46c93d06c7276eb5ff4d96c1c60cf`，与指定值一致；Host 探针证据在同目录 `2c2-host-probes-v2.py/.json` 与 `2c2-host-terminal-probes.py/.json`。累计基线仍 `ace3e1d`，本轮从 `fbdf581` 继续，仅修所列缺陷。

R1 唯一通道、完整身份不可旁路：`roles/live.py` 新增 `stored_run_request(attempt_dir)`，读取 C1 实际写出的公共 `role-run-request.json` 与私有 `role-run-control.json` 及其所指的治理 turn 输入，核对 invocation、task、attempt、generation、turnId 与输入摘要的完整身份；黑板 `_live_channel` 除此之外还核对视图的 task/attempt/generation。已抽取 harness 的活路径不再有直连回退：黑板对未绑定通道报告 `bridge.reason`（stored run request 未通过核对）且 journal 报 `channel-unbound`，下一问询可重试绑定；Worker `_Renewal._attempt_activity_binding` 返回三态（unextracted 沿用原 sidecar 路径并缓存 / bound 走通道 / unavailable 本 tick 无更新、不缓存、下 tick 重试），绝不旁读 sidecar。终态 journal 恢复与未抽取 harness 的原路径保留。

R2 journal 事实无损：绑定 `read_journal` 改为返回可用性事实（原直连 reader 的闭集原因 `no-journal-path`/`journal-not-written`/`journal-exceeds-limit` 原样保留；`entries` 是该 reader 自己的去重计数，含被拒记录）；被版本/身份绑定拒绝的记录以新增 `LiveSnapshot.journal`（`LiveJournal`/`InquiryJournalRejection` 最小闭集事实）按问题携带旧公开拒绝字符串（"the journal record belongs to another task/attempt"，无法判定时 "the journal record is not bound to this execution"），黑板经同一通道投影 `journalRejected` 并只导入绑定记录，不另读活文件；共享锁屏障与 version/turn 绑定未动。

R3 分页完整消费：黑板 journal 投影按 `sequence`/`truncated` 消费同一通道的后续页（每帧上限与 limit 不变，文件读取分页无 socket 往返），总条目数、末页答案、来源与状态全部可达。

R4 观察成功与问询状态分立：`LiveReply` 新增 `observed`（与 status 一致性由校验器保证）与 `state`（桥自身提交状态的闭集 `REPLY_STATES`），`discarded` 进入 `LIVE_REPLY_STATUSES`——被撤回问题的重放是携带实际状态与原生原因的观察成功，不再是 `unknown-bridge-state`；`InquiryState` 新增 `limitation` 并入序列变更集，`_record_facts` 携带它，黑板 `_journal_record` 原样回放记录，`_apply_journal` 旧的 limitation 优先选择不变（公开 reason 与直连路径逐字相同，含消息库 200 字符截断）。

本轮聚焦验证：受影响六模块（transport、live_channel、roles 接缝、worker invariants、zcode native_run、board inquiry）改前基线 123 项全绿（`<task>/m/continue1-baseline-ids.txt`，run6 副本），修后 `run9-final` 134 项全绿。编号对账（`<task>/m/continue1-after-ids.txt`）：删除/改名 5——`test_a_channel_projection_never_reports_a_foreign_record_as_rejection` 与 `test_a_foreign_request_keeps_the_direct_path_and_its_journal_rejection` 的预期本身是缺陷（外来记录静默丢失、直连回退当正确行为），由 `test_a_foreign_record_for_the_asked_id_surfaces_as_the_public_rejection`（同一被询问 ID、旧拒绝语义经通道）替代；`test_a_foreign_request_identity_never_binds_this_attempt` 只是给 fixture 起了个 foreign 名字，由 `test_a_tampered_request_never_bridges_around_its_channel`（改真实存储请求的 invocation，断言桥收不到问题、journal 报 channel-unbound）与 `test_a_task_id_mismatch_never_binds_either` 替代；`test_a_stored_request_of_another_invocation_never_binds` 改写为 `..._never_binds_or_bypasses`（断言零转报、修复后下一 tick 重试成功）；observe-only 用例由 TestInquiry 迁入 ZcodeLiveChannelTests（真实通道代替无请求的伪桥，同名 ID 换类）。新增 16，其余 113 项与基线集合相等。

六个隔离副本变异各自被对应新见证抓红（配对绿即 run9-final，日志在 `<task>/m/run8-*/`）：M5 删 stored_run_request 的 turn 输入身份核对；M6 恢复黑板直连回退；M7 恢复 Worker sidecar 旁路；M8 journal 投影只取第一页；M9 绑定丢弃拒绝元数据；M10 把 `discarded` 折叠回未知状态。最直接的等价证据是重跑 Host 自己的两份探针脚本（只读其文件，在 `run9-final` 副本上执行，结果存 `<task>/m/run9-final/host-*-probes-recheck.json`）：外来 task/turn/invocation 的请求一律未绑定、原生桥零收件、Worker 零转报；journal 缺失报 `journal-not-written`、外来同 ID 记录报公开拒绝且计数含被拒记录；32×4000 字节分页全量消费（entries=32、末条 answered）；撤回重放 observed=true、reason 保留、状态 discarded；close 结算 reason 与直连逐字相同（limitation 优先、200 字符截断）。探针中被 mock 成无通道的"直连"一栏如今如实显示未绑定报告，这是 R1 的预期结果而非偏差。

未验证边界不变：真实安装版 ZCode 端到端仍留 2-D；`StagedWorkerRuntimeTests` 与完整检查仍未跑。写入范围未变，`git status` 仍限 scope 内文件加本记录。上轮移交事项（step-0 TSV 登记三处跨界引用）由 Host 在整合时处理，本轮新增未引入第四处。任务根仍为 `/private/tmp/a252c2-jy5bpl7e`，本轮新增材料在 `<task>/m/run6-continue1-baseline`、`run7-continue1`、`run8-m5..m10`、`run9-final`，未触碰旧实验目录；检出内无一次性材料、无 `__pycache__`，未手动删除任何对象。

## 第二次退回的撤回与修正（journal 边界与分页预算）

上轮固定输出 `e6a72aea40210db52616fe05b12672aa1415b035`（artifact `e29f959e-ff28-4d49-adeb-c786b217064a`）再次被拒、未整合；续轮说明 `~/.codex/worktrees/adr025-run-module/hey-my-buddy/tmp/adr025-host/step2-6npns9su/2c2-continue-2-brief.txt`，SHA-256 `8fefd906054d6a63df663ed1154d913142927837055edc27d92b2886dd384cfc`，与指定值一致。R1/R4 与大分页的修正已实现，不重做；本轮只修三个 R2/R3 缺陷，证据为同目录 `2c2-host-correction-review.py/.json` 与基线导出 `2c2-baseline-inquiry.py`（git show ace3e1d 的真实黑板 observe）。

先撤回一段上轮表述：上轮"重跑 Host 探针全部转绿、与直连逐项相同"的说法不成立，予以撤回。旧探针脚本本无断言，exit=0 从来不等于通过；其以 `mock(_live_channel=None)` 模拟的"直连"一栏在新代码中只会得到 channel-unbound，已不能代表直连基线，"逐项一致"的对照方法失效。上轮真实成立的只是新分支的取值本身（外来身份未绑定、原生零收件、Worker 零转报、discarded/关闭原因等，由本轮测试逐值断言），而非与基线的等价证明。本轮对照改用 Host 的新方法：把固定基线的真实 `observe` 函数装为对照（不改动生产代码），由 `2c2-host-correction-review.py` 直接并排。

修正一：journal 状态区分。`bind_live_channel.read_journal` 在共享锁读取旁做最小文件状态核对：`os.stat` 失败→`journal-not-written`，非常规文件→`journal-unreadable`，实际超过字节上限→`journal-exceeds-limit`，状态通过而加锁打开仍失败→`journal-unreadable`（不再把 open 失败当不存在），空文件是真实可读 journal→`available=true/entries=0`。共享锁与提交屏障保留，未绕通道读内容，未改通用 IO。

修正二：拒绝按每问题最终有效记录产生。绑定逐条记录按 inquiryId 记录最新归属，与直连 reader 的最后一条覆盖规则对齐：最新合法记录清除该 id 的旧拒绝并保留其合法记录的字段延续（queued→answered 的 delivery 等不丢），最新外来记录使该 id 恰好贡献一条拒绝（重复拒绝去重，不会堆满 `LiveJournal.rejections` 上限）且不再投影其更早记录；`entries` 是不同问题 id 数。身份检查未绕过，不假定最后写入必合法。

修正三：分页预算计入真实返回的全部字段。`ExistingLiveChannel.observe` 的帧预算候选现在连同 journal 事实与 observation 一起计量，页在超限前断开（16×3500 字节回答+16 条拒绝由超限的 66,713 字节首帧改为 2 页），上限未扩大、拒绝与末尾答案未丢，分页后全部合法回答可达。

验证：三个受影响模块改前基线 82 项全绿（`<task>/m/continue2-baseline-ids.txt`，run10 副本，native_run 仅 LiveBindingTests），修后 `run13-final` 覆盖整个 test_native_run 模块共 113 项全绿。编号：新增 6——LiveBindingTests 的 `test_journal_states_follow_the_direct_reader_semantics`、`test_the_latest_legal_record_clears_an_older_rejection`、`test_the_latest_foreign_record_rejects_once_per_question`、`test_the_page_budget_counts_the_metadata_it_returns`，黑板侧 `test_an_empty_existing_journal_is_available_not_exceeds`、`test_the_latest_legal_record_supersedes_an_older_foreign_rejection`；删除/改名 0；其余各项与既有集合相等（含 native_run 其余 28 项与 continue1 轮集合一一相等，已对账）。

三个隔离副本变异各自被对应新见证抓红（配对绿即 run13-final，日志 `<task>/m/run12-*/`）：M11 重新把空文件报成超限；M12 合法记录不再清除旧拒绝；M13 分页预算撤掉 journal/observation 计量（首帧重超 65,536）。最直接的对照是重跑 Host 的 `2c2-host-correction-review.py`（只读其文件，在 `run11-continue2` 副本上执行，结果 `<task>/m/run11-continue2/host-correction-review-recheck.json`）：空 journal、被覆盖外来记录、帧行界三组的 new 列与 baseline 列完全一致（available=true/entries=0；answered 且无拒绝；`exceeded: false`）。

未验证边界不变：真实安装版 ZCode 端到端仍留 2-D；未跑 134 整批/完整检查/控制台/打包/模型。任务根 `/private/tmp/a252c2-jy5bpl7e`，本轮新增 `m/run10-continue2-baseline`、`run11-continue2`、`run12-m11..m13`、`run13-final` 及 t/ 下少量调试脚本；旧实验目录未触碰，未手动删除任何对象，检出仍限 scope 内文件与本记录。

## Host 整合时的文档更正

以上各轮的实际执行与失败记录保留。此前列为移交事项的“修改 step-0-cross-imports.tsv”予以更正：第零步清单是历史验收事实，保持原样，新增引用在第二步整合登记。当前固定源码从黑板 inquiry 新增的模块关系只有 `buddy.harnesses.live` 与 `buddy.roles.live`；`buddy.harnesses.registry` 本已引用，只增加调用点，第一轮出现的 `buddy.harnesses.run_contract` 直接导入在修正中已移走。这是 Host 已掌握源码事实的小记录更正，不改变本微任务做过的测试或验证结论。

本微任务确实没有进行原生模型检查。Host 另在 `ace3e1d` 完成的新格式真实无工具运行见 `adr025-step2-native-smoke.md`，不得据此宣称实时问询已完成原生端到端验证；两种范围分别记录。Host 对本份固定交付另做三组 journal/帧边界探针和 31 项聚焦检查，全部通过；整步完整检查仍在 2-D 整合之后。
