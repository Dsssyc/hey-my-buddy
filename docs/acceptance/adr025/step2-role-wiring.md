# ADR-025 第二步微任务 2-C1：角色运行接线

2026-10-06，固定基线 `ff1034e7cf82a14e7d045791a2684983204b1909`。先完整读取 Host 固定说明 `~/.codex/worktrees/adr025-run-module/hey-my-buddy/tmp/adr025-host/step2-6npns9su/2c1-detailed-brief.txt`，SHA-256 为 `93c72220c8b98bb27e7782cc28b2177992d58f0d4bd325cba699430dacf38e09`，与指定值一致；已读本检出的 AGENTS、CONTEXT、ADR-007/023/025、执行计划最新补充及 2-B 记录。受限的 ZCode/zai-api 执行路径未重试；本轮按 Host 续接配置由 Codex 执行，公共文件仅本项串行修改，未再委派。改动未提交，Host 审查整合与 socket 补验仍待完成。

## 入口与格式流

`roles/controller.worker_executor` 对已注册的 ZCode 返回通用 `WorkerRunExecutor`；快速 Router 在 `FastPreparation` 冻结注册的 run，启动时核对注册未变。两者均经 `launch_controller` 启动唯一的 `hey_my_buddy.buddy.roles.run_controller --control <private-file>`，复用既有 `ProcessHandle` / `collect_controller`。控制器调用注册模块的服务准备接口，角色组装提示词与 `RunRequest`，实际 encode 到私有 `role-run-request.json`、decode 该文件后经 `run_harness` 调用注册的 `native_run.run`；stdout 是 encode 后的完整 `RunResult`，持有方有界 decode 并核对 harness 和完整 invocation 身份，再作业务投影。角色的纠正原因与耗时放在私有 `role-run-verdict.json`，没有把旧回执字典藏进公共格式。

最小注册 surface：`check_preparation(spec, environment)` 只检查本机原生准备；`prepare_services(*, invocation_root, identity, input_sha256, attention_path, session_tools, completion_tool, validate_outcome, inquiry, inquiry_tools, activity_dir, native_stderr)` 不启动进程，返回 `BoundSessionServices(description, completion_tool, checkpoint_tool, answer_tool, services)`，五个字段均由角色实际消费；`services` 在控制器进程内保持不透明，不进入帧。原生模块提供 `validate_turn_provenance(record)`、`session_facts(native_root, session_id)` 和独立 `run_discovery(...)`。资格注册仅确认 run/discovery 可调用，不分析厂商代码。没有新增或改动 RunRequest/RunResult 字段，也未出现额外格式缺口。

角色拥有提示词、六字段 outcome 校验、快速结构判断与一次纠正、attention 对 completed 的拒绝、turn record 发布及检出封存；原生模块保留真实工具限定名、协议顺序与签收验证、绑定存在性、错误归因、会话存储和发现。公共角色没有导入 ZCode 实现或新增厂商分支。只有原生 group_state=gone 且持有的控制器组实际停止才确认 shutdown；身份不匹配的帧不借用持有方的停止事实。来源证据件按长度与 SHA-256 核对，失败时拒绝 turn，同时保留已观察的用量、末条消息与真实停止事实。

ZCode 描述对象不再继承 Adapter，旧 prepare/start/collect/cancel、两类 structured 入口和 `zcode/runner.py` 模式 CLI 均删除，无转发层。只读资格仍为 `readonly-worker-carrier-unimplemented`，经真实资格入口证明不 spawn。无输入发现复用原生 spawn/握手，只读元数据，不构造 RunRequest、不挂服务、不 send prompt；返回必须已有原生停止证据，外层再确认控制器停止，否则保留目录。三个角色私有文件名已登记精确白名单，旧 `zcode-control.json` 仅保留在历史数据回收规则。参考文档只替换入口路径和私有文件名。

## 实跑验证与等价边界

材料根为 `/private/tmp/a252c1-oqd9h9pj/m/work-xw_h56zp`（下称 `<m>`）。测试均用 `uv run --frozen --offline --no-sync python`，清除继承的 BUDDY 运行时、Worker、agent 环境及 VIRTUAL_ENV，显式 PYTHONPATH 指向本检出的逐文件 SHA 相等副本，私有 state/runtime；只读复用 `~/.codex/worktrees/adr025-run-module/hey-my-buddy/.venv` 中的锁定依赖，未安装依赖。每个受影响文件独立子进程、串行运行；命令、导入来源、逐 ID 结果、退出码和源哈希均留存。未跑完整检查、控制台、打包、安装版 harness、真实模型或付费调用；测试使用 Python FAKE/mock app-server 贯穿实际控制器、注册 run 和编解码。

`<m>/focus-k8edyw0q/summary.json`：20 个聚焦组共 328 项通过、0 skip，覆盖角色、控制器、格式、只读资格与路由偏好、私有目录、ZCode Worker/fast/协议/工具事实/turn、用量观察与非 socket 的问询拒绝。最后的事实保留修正后，`verify-final-c233yyk1` 的另外四组 48 项通过，角色组初次因新增断言未计入原有公开来源名归一而失败；修正预期后 `audit-final-kejftnnm/green-roles` 的 8 项通过，合计覆盖最后修正的 56 项。描述对象最后调整的 11 项在 `mutations-69570zlz/final-description` 通过；最终代码清理未用 import 后，`delivery-fljafa6w/final-role-tests` 的 8 项再次通过。未将较早副本的 328 项冒充最终同批全绿。

原 Worker 的 initial/resume/reconstruct、失败与取消、nativeSession、turn/封存、产物、用量/配额失败/末条消息，及 fast 的 rawAnswer/correctionCount/toolEvidence/nativeEventCount 等原断言保留。真实接线新增见证检查完整身份、请求帧、服务限定名、秘密不进入请求或公开回执、纠正在同一原生进程、无输入发现、双层停止和来源引用篡改。`native-definition-proof.json` 证明原生 34 个顶层函数/类 AST 与基线相等（含 run、结算、drain、停止和协议类）；只有发现移动原有禁止项目输入配置，另加四个窄事实/准备接口。`source-scope-proof.json` 证明其他 harness 47 个文件、runtime 7 个、公共 CLI/protocol 21 个、依赖 4 个及受保护文档 35 个文件 SHA 集合相等；共享入口的其他 harness/command 路径由既有聚焦断言覆盖。

## 编号与小变异

全仓声明编号只做 AST 对账，未执行全仓测试：基线 2,500、当前 2,509，删除 0、改名 0，原集合完整保留；159 个未改模块的 2,325 个编号集合相等且源 SHA 相等。同一聚焦范围只收集 unittest 编号：427→436，删除/改名 0、新增 9，原集合相等；其中 15 个未改模块的 255 个运行编号集合相等。移除旧 runner import 的 review-scope 三个编号由全仓对账确认保留并实际通过，未计入这份 427 的基线。详情在 `audit-final-kejftnnm/{declared-id-proof.json,collection/id-proof.json}`，不把收集计为执行。

新增 9 个编号：`buddy.roles.test_registered_run_wiring` 中 `RegisteredDescriptionTests` 1、`FastRegisteredRunTests` 5、`WorkerRegisteredRunTests` 2，以及 `test_private_directories.PrivateDirectoryTests.test_role_control_cleanup_matches_exact_private_filenames`。完整新增名仅存编号差异材料；原 fixture 与原编号均保留，入口迁移没有削弱 socket 断言。

四份独立副本变异都由相应断言抓红（exit 1）并有正式代码配对绿（exit 0）：M1 去掉结果身份/harness 关联；M2 把原生停止恒置 True；M3 把未实现只读资格改为 eligible；M4 在来源证据错误时丢弃已观察用量/末条消息。前三组见 `mutations-69570zlz/summary.json`；第四组见 `audit-final-kejftnnm/M4` 与 `green-roles`。每轮验证导入来自自己的副本，生产源码未作变异，副本保留红态，没有重跑旧大矩阵。

## 未消费字段与后续范围

`audit-final-kejftnnm/field-class-inventory.json` 区分业务读取与 codec 校验/私有帧留存，列出读写位置。`run_execution.worker_request/fast_request` 写 network、frozen_account、budget.max_output_bytes；native run 只读 budget.timeout_seconds，不读上述值；budget.tool_calls/bytes_read 及 continuation 的四个额外关联字段仍未填。`native_run.prepare_services` 写 SessionService 元数据，原生仅消费 tool_names 并用实际挂载契约核对请求 schema，其余元数据尚无业务读取方。

`native_run._build_result` 写 model_start_evidence/root_identities/denied_interactions/unknown_events/effective_policy、配置检查来源、值/完成证据元数据、续接引用和 interrupt；当前角色只读相应主事实、值、完成身份、continuation.resumable 与 native.group_state，其余仅保留和校验。StopEvidence.controller 留 unknown，真实外层停止从持有的 ProcessHandle 读取；EvidenceRef.retained 未参与判定，位置/长度/哈希已实际读取；RunResult.quota 无本原生生产写入，公开 quota 保持原来的 None。ModelStartEvidence/DeniedInteraction/UnknownEvents/PolicyFact/EffectivePolicy/InterruptEvidence 有生产构造与 codec 使用，尚无角色判断读取方；不能据此称它们已无任何使用方。本项新增 BoundSessionServices 与 WorkerRunExecutor 均有生产实例和消费，没有新增未使用的类；删除了本项孤儿 import 与描述对象旧字段。统一格式删减留 2-D，Live 生产接线、黑板问询和活动消费留 2-C2。

## 未验证、清理与固定交付

本沙箱实际失败的原 ID 为 `buddy.harnesses.zcode.test_zcode.ZcodeAdapterTests.test_success_uses_native_root_receipt_and_keeps_secrets_private`：原断言 `assertTrue(outcome.result["inquiry"]["mounted"])` 得到 False，原生报告 `the inquiry bridge socket could not be mounted: PermissionError`；在两个开发批次均遇同一限制。断言未改成 skip 或放宽。Host 需在允许本机 Unix socket bind 的环境补验该项及同样依赖桥的 16 个关联项；完整 17 个原 ID 固定在 `audit-final-kejftnnm/host-socket-tests.json`，其余 16 项本轮未执行，不预报通过。LiveBinding、安装版原生套件和 Windows 未验证；历史验收不能代替本轮证据。

确切任务根 `/private/tmp/a252c1-oqd9h9pj`，TMPDIR/BUDDY_CHECKS_TMPDIR 始终指其 `t/`；实验、日志、源码副本与变异都在 `m/work-xw_h56zp/`，每次新目录，未覆盖旧实验。清理偏差如实登记：首次 uv 默认缓存访问 `~/.cache/uv/sdists-v9/.git` 被沙箱拒绝，随后缓存改指 m；未核实或清理外部缓存。早期控制器过滤 PYTHONDONTWRITEBYTECODE，自动在 `<checkout>/src` 生成 15 个 __pycache__ 目录，违反材料只放 m 的要求；未删除，确切相对路径在 `audit-final-kejftnnm/checkout-bytecode-final.json`，之后改用字节相等副本运行，目录集合未再增加。Worker 没有手动删除清理对象、使用通配符清理或触碰其他会话材料；常规 fixture 与产品发现收尾照旧。未安装升级日常运行时、修改用户配置/凭据/日常数据或读取用户凭据内容。Host 验收后按登记路径整体回收，并处理上述 checkout 字节码遗留。

固定交付目录为 `<m>/delivery-fljafa6w`，累计补丁 `cumulative.patch`、逐文件内容 `files/`、基线/文件/补丁 SHA 清单 `manifest.json` 和补丁验证 `patch-check.json` 供 Host 审查；本检出的唯一验收记录是本文件。以 Host 差分审查及 17 项 socket 补验为剩余边界，本项交付后停，不开展 2-C2/2-D。

## 第一次续轮：原生事实投影与独立报告保留

Host 拒绝固定 artifact `62f6e5c8-7c47-40c8-9517-f2258ab649d5`（输出 `1488c001cf3611231fdea3a355bfc2a98541eab4`，累计补丁 SHA-256 `0988ced39c0cb084f5b6430d9cdec3205f1190eade50e8f24225ff2d7434e00a`）。本轮完整读取 `~/.codex/worktrees/adr025-run-module/hey-my-buddy/tmp/adr025-host/step2-6npns9su/2c1-continue-1-brief.txt`，SHA-256 `fe89610f34bfdbf7f1b5676aee05518afb0717905a52389f3f1202aa760db47c` 一致；757 个输入文件逐字节与该固定 Git 输出相等，累计基线仍为 ff1034e。原交付、失败与材料保持原状；本轮不把格式验证或前轮测试视为 Host 已验收。Host 明确安装版 acknowledge(rejected) 为 NOT_READY，拒绝通过本次 continue 登记；本 Worker 未调用该接口、未取消任务或更改执行配置。

Host 提供的只读证据确认：原 17 项 socket 补验全部通过，0 fail/error/skip；原完整 2,562 个 unittest ID 全部保留，固定输出为 2,571 个 ID / 169 个模块；Worker 正常结束与 fast 一次纠正两个 Python fixture 的公开键/选定值相等，差异仅是独立运行的原生身份。执行方是 Host，证据为上述说明同目录的 `2c1-host-socket-results.json`、`2c1-fixed-test-ids.json`、`2c1-public-projection-diff.json`。该补验解决前轮权限边界，不是本 Worker 重跑或完整检查，也不代表原固定差分获验收。

A 修正：注册的 `zcode/native_run.native_evidence(result)` 是无副作用机械投影，从 `EffectivePolicy.tools.requested` 取实际记录的工具/标题设置，从 `CompletionEvidence.stream_end` 取 EOF，缺失保持 None，不由 scope 或 end.status 推断。通用角色按 capture_evidence 调用这份注册投影，不含厂商开关；nativeIdentity 使用公共 NativeIdentity 的完整非空序列化字段，threadId/inputId/callId 不再被裁掉。真实 fast 纠正 fixture 保持原来的四个 nativeEvidence 键及 [] / False / True 值；纯值见证同时检查 Read / True / False、五种原生标识、未知事实和未请求时不调用投影。没有新增 RunRequest/RunResult 字段；capture_evidence 本来就在基线，不列为新增字段。

B 修正：collect 先保留 typed 用量/消息等事实，再分别核对 inquiry-report 与 attention-report 的位置、长度、SHA-256 和对象形状。每份报告独立留存，来源或另一份报告失败只把业务交付置失败，不重置已验证报告；外层停止未知时仍读取事实，但不发布 turn 或调用封存。来源检查、两层停止与固定绑定没有放宽；缺失/损坏的 attention 报告不再推成 0 或 False，而是 attentionRequired=None。真实 attention 场景的外层停止未知与来源篡改两例均保留 requests=3、完好 inquiry、attentionRequired=True，并分别保持外层 False / True 与原生 True 的停止事实；另覆盖 inquiry/attention 各自损坏时保留另一份报告，原用量/末条消息保留见证一起通过。

本轮材料独立放在 `/private/tmp/a252c1-oqd9h9pj/m/continue1-hh8o2e85`（下称 `<c1>`）。净化环境、锁定依赖只读复用、t/ 临时目录和逐文件 SHA 相等源码副本的运行方式与前轮相同。两组修正影响的聚焦测试共 17 项通过（角色接线 14、原 fast 回执 3），见 `focused-x_h8zscc/summary.json`；增加显式缺键断言后，最后六个修正/保留见证再次通过，见 `paired-_m6m_n6x/corrected/result.json`。没有重跑 328、socket 旧矩阵、全套、控制台、打包、安装版 harness 或真实模型。

配对故障见证只做两组：在两份新的固定拒绝源码副本中置入当前见证，A 的不同/未知原生事实两例和 B 的外层未知/来源篡改两例均失败（exit 1，各两项 assertion failure，0 error）；配对的修正源码六项全部通过（exit 0），包含原用量/消息保留与单份报告损坏。生产源码未变异，固定输入及两个红态副本保留，来源哈希、命令与导入路径见 `paired-_m6m_n6x/summary.json`，未重复旧矩阵。

只收集编号、不执行全仓：完整 169 模块的 2,571 个输入 ID 全部保留，当前 2,577，新增 6、删除/改名 0；168 个未改模块的 ID 集合相等。新增均在 `buddy.roles.test_registered_run_wiring`：NativeEvidenceProjectionTests 三项，WorkerRegisteredRunTests 三项；原描述对象与 fast 纠正两个 ID 扩展断言，名字保留。AST 声明编号 2,509→2,515，原集合也完整保留。`ids-o1ct5ox4/id-set-proof.json` / `declared-set-proof.json` 留完整差异；初次辅助列表比较遇两个既有动态 suite 的顺序差异，集合无差异，原文件保留，没有重跑测试掩盖它。

本轮生产新读取了 EffectivePolicy.tools.requested 与 CompletionEvidence.stream_end，前文对应的未消费字段清单仅描述首次交付，不能再沿用作本轮删除依据。新增 native_evidence 与 _worker_reports 都有生产消费；其余格式统一删减仍留 2-D，Live 生产接线仍留 2-C2。相对拒绝输出只改 native_run.py、run_execution.py、test_registered_run_wiring.py 与本记录，其他入口、格式、运行时和公共文件字节保持。

确切任务根仍为 `/private/tmp/a252c1-oqd9h9pj`；TMPDIR/BUDDY_CHECKS_TMPDIR 指其 t/，新实验各建目录且材料都在 `<c1>`。前轮 15 个 checkout 字节码目录及 uv 外部缓存访问拒绝披露保留；目录集合没有新增或删除，清单为 `<c1>/checkout-bytecode-continue1.json`。没有手动删除、通配符清理、覆盖旧实验或触碰其他会话材料；常规 fixture 自己收尾照常。未安装升级运行时、修改用户配置/凭据/日常数据或读取用户凭据。本轮修正仍待 Host 审查固定差分后整合，交付后停止。

本轮固定交付为 `<c1>/delivery-x3692ua1`：相对拒绝输出的 `delta-1488c001.patch`、相对 ff1034e 的 `cumulative.patch`、四个修正文件 `files/`、逐文件/补丁 SHA 清单 `manifest.json` 及基线应用检查 `patch-check.json`。旧固定交付没有覆盖或删除；Host 后续审查本轮固定内容，前轮 socket 补验不再列为待办，2-C2/2-D 不开始。
