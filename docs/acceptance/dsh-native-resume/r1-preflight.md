# DSH 原生续接 R1：无模型原生恢复核对（preflight）

2026-10-08，微任务R1（宏任务obj-36c065d5下第二部分首项），Codex Buddy Worker执行，固定基线aa98c880。本记录只做事实核对：已安装`dsh --profile acp`在微任务私有sessions根新建空会话、关闭并确认进程组消失，再由另一独立进程用同一存储和公开`session/resume`恢复；核对能力、session/list、配置读回、传入本次MCP服务、会话标识来源、同一sessions根、错误分类与原生leader/进程组停止。全程0次prompt、0次模型调用、0次`authenticate`，未读取任何凭据文件内容，未安装升级任何运行时，未改动用户配置或登录。跨进程原生恢复在两次独立原生运行中均得到确认；这不构成产品native-session能力声明，能力启用仍按计划在付费核对之后。本记录经一次返修：修正停止核对口径（原"两层停止"名称不成立，见"返修与口径更正"节），并给探针补上A停止未确认即禁止B启动、启动所有权错误证据登记两道防护。

## 运行方式与环境

探针为本检出的新文件`tests/probes/dsh_native_resume_preflight.py`，只加不删；启动行、补丁顺序、profile物化与启动包装全部复用既有公共机制：`runtime_selection.selected/discover`（与`check_preparation`同路径）、`native_run.source_home/source_binding_rows`、`materialize_acp_profile`、`acp.launch`经`AcpClient.start`、`FrameMetaLog`。补丁行序与`native_run._launch_agent`一致： owning home的`credentials`行（本机`~/.dsh`只有`.credentials.yaml`存在，按路径指回，`watch`关；无settings行）、共享sessions根的`session-persistence-jsonl`行、最后两个literal关闭行`session-title-llm`/`session-telemetry-otel`；探针启动时断言这两个literal与生产常量相等。

两个尝试的编排：假微任务ID经`private_dirs.native_root(<private-state>,dsh,<fake-task>)/sessions`建立共享会话根；每个尝试各自`attempt_root`内私有DSH_HOME（含`profiles/acp`物化）、`dsh-launch-patch.json`、`logs/launches.jsonl`、`logs/frames.jsonl`与MCP stub证据文件；两份补丁的session-persistence-jsonl.root都指向同一个共享sessions根；两次会话共用一个私有cwd目录。进程A：initialize→空root session/new（mcpServers挂本尝试stub）→session/list→`reasoning_effort`切换并读回→session/list→session/close→session/list→停机；进程B：另一次独立attempt启动→initialize→session/list→对A的会话id发`session/resume`（mcpServers挂B自己的新stub配置）→session/list→对不存在id的resume（负例）→session/close→session/list→停机。没有session/new回退路径：resume被拒或无响应时原样记录。

运行器契约（可按占位符复跑）：先清继承`BUDDY_*`、`ANTHROPIC_*`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`、`PYTHONPATH`，再设`TMPDIR`与`BUDDY_CHECKS_TMPDIR`＝`<task-root>/t`、`BUDDY_STATE_DIR`/`BUDDY_RUNTIME_ROOT`＝`<task-root>/m/<label>/`下、`BUDDY_DEV_SOURCE=1`、绝对`PYTHONPATH`＝`<checkout>/src:<checkout>/tests`，解释器借Host检出`~/.codex/worktrees/dsh-native-resume/hey-my-buddy`的uv frozen python；命令`python <checkout>/tests/probes/dsh_native_resume_preflight.py --task-root <task-root> --run-label <新label>`。探针自身有环境卫生闸（禁继承Worker/凭据/ANTHROPIC/venv变量，临时根必须在任务根内，`hey_my_buddy`必须解析到本检出）。本微任务的短根由Host开工登记并确认为空（t、m为登记子目录），确切路径只在本微任务交付报告给Host；入库记录一律用`<task-root>`。

## 执行历史

run1（`<task-root>/m/r1-20261008T0332-run1`）：20项发现、0完整性失败，首次确认跨进程恢复。run2（`<task-root>/m/r1-20261008T0345-run2`）：探针自身缺陷（evidence键赋值早于字典定义，UnboundLocalError），发生在选择已就绪之后、任何会话启动之前，故无ACP会话启动、无stub、无会话创建（discovery选择探测按其自身生命周期已发生至少一次），证据文件原样保留未覆盖；修复后未复用该标签。run3（`<task-root>/m/r1-20261008T0348-run3`）：增强证据（完整initialize结果、session/new后的sessions根列表、B关闭后的session/list、所选版本）后21项发现、0完整性失败，为最终引用运行。原生账目：ACP会话启动共4次（run1与run3各A/B一次，组全部确认停止），每次尝试前另有与`check_preparation`同路径的discovery无输入版本探测启动（次数以discovery自身为准），全部0模型调用。run1与run3是两次独立原生复制，结论一致。

## 核对事实（run3为准，run1一致）

启动与能力：discovery两次都选中`dsh 0.1.5-rc.1`，argv为`[node, ~/.local/bin/dsh, --profile, acp, --patch, <attempt>/dsh-launch-patch.json]`，`HOME`沿用、`DSH_HOME`为尝试私有目录。两进程initialize结果相同：`protocolVersion 1`、`agentInfo {name: deepseek-harness-acp, version: 0.0.1}`、`authMethods []`、`agentCapabilities.sessionCapabilities {close, list, resume}`、`mcpCapabilities.http true`、`promptCapabilities`的audio/image/embeddedContext均false。三个声明能力（close/list/resume）本次都在真实agent上实际行使过。未发送`authenticate`。

会话创建与持久化：`session/new`响应携带`sessionId`（创建标识的唯一公开来源，run3为`54e2c998-1954-4538-abb5-1eb7315d68b2`）和完整`configOptions`（model当前值`["deepseek-official","deepseek-v4-flash"]`、reasoning_effort `high`）。共享sessions根在`session/new`之后立即就有`<按cwd净化的目录>/<sessionId>/session.v3.jsonl.zstd`（327字节）与`session.lock`——rollout在创建时即落盘，不在关闭时。空会话全程无prompt，rollout内容未读取（只取存在、大小、mtime）。

session/list与会话标识来源：打开中的会话不在`session/list`里（A创建后、切换后两次列表均为空）；`session/close`后该id出现在列表中；B在resume前列表中恰为该id；resume应答期间该id从列表消失；B再close后该id重新出现在列表。跨进程恢复所需的会话标识只有两个公开来源：A的`session/new`响应，或B侧的`session/list`；`session/resume`响应不携带id（本次实际响应仅`configOptions`一键），与公开schema事实一致，探针断言了这一点。

跨进程resume：B以同一sessions根、同一cwd、自己的新MCP服务配置对A关闭过的会话id发`session/resume`，agent正常应答（两次运行都如此）。resume使agent重新加锁（`session.lock` mtime更新）并追加同一rollout文件（327→407字节），同一会话id目录、无新id。resume响应的`configOptions`里`reasoning_effort`报`high`——A中切换成`off`并读回成功的值没有跨resume恢复；该切换是否写入过rollout不可观察（未读内容），公开事实是：恢复不还原会话内配置切换，R2实现必须在恢复后重新声明model/effort。

MCP挂载：两个尝试各自的stub（同名模式`r1_<attempt>`，env为数组形状、含`PYTHONPATH`与本探针标记变量）都在挂载时完成initialize+tools/list握手（session/new时A的stub、resume时B的stub，各自毫秒级完成）；stub只回目录（一个`r1_probe_catalog_tool`），未被调用，因此这是0模型下可观察的挂载证据，不是工具执行证明。A停止后其stub证据文件无任何新事件；DSH是否会尝试从持久化状态重挂上一尝试的旧服务，本探针无法观察，记未知。

错误分类：对不存在id的`session/resume`被原生拒绝，JSON-RPC错误码`-32602`，消息`Invalid params: session is not resumable: <id>`；该消息不区分"不存在"与"不可恢复"，错误分类粒度以码+该消息形状为界。本次无其他拒绝、无超时、无连接中断。

停止（原生leader/进程组层）：两次尝试均为`shutdownConfirmed true`、leader退出码0、`groupObserved gone`、直接组观察`gone`，未触发终止升级；启动包装的排水证据与owned group观察都留存于证据文件。这一核对只覆盖原生进程层——包装证据与组观察看的是同一个owned进程，本免费探针不核对roles/controller外层停止，真正两层在后续Worker冒烟核对。stub进程随DSH组退出（stub-eof先于各自组消失）。

无模型证明：两进程的操作日志与帧元数据逐一吻合（A：7个请求=7个出站帧；B：7=7），方法封闭集核对了`session/prompt`/`authenticate`/`session/cancel`零出现；`notificationCounts {}`（无任何session/update流）、`permissionDecisions []`、`deniedInteractions []`、`protocolFaultCount 0`、`unmatchedResponseCount 0`、写失败0。帧元数据日志只含方向、字节数、摘要，无内容。

## 边界与未证实项

本记录不证明：工具真正可执行（真实completion/inquiry在批准后的2回合冒烟）、rollout内容语义（未读）、配置切换是否持久化到rollout（行为上已知不随resume恢复）、旧MCP服务是否会被重挂（未知）、resume对"打开中"会话的行为（本次只对已关闭会话resume）。`sessionCapabilities`在本轮完整initialize结果里位于`agentCapabilities`嵌套下，早前adr025-dsh-acp-client记录按顶层描述，形状投影差异不影响本轮三个能力均实际行使的事实。产品续接（R2/R3）与能力启用不在本微任务范围；付费核对未开始，2个模型回合仍未授权。

## 返修与口径更正

固定产物7e9f68e1（artifact d01a92e1）经Host独立两进程无模型核对后暂不验收、原run返修；正常路径公开ACP结果维持通过，不重做真实DSH核对，本节只修三处原范围缺陷并做无原生DSH启动、无模型的聚焦故障验证，材料在新标签`<task-root>/m/r1-20261008T0407-rework/`。

其三先说口径：原记录的"两层停止"名称不成立——`client.shutdown`的包装排水证据与`group_observation`直接组观察看的是同一个原生进程层，本免费探针从未核对roles/controller的外层停止。探针finding由`two-layer-stop-*`更名为`native-leader-group-stop-*`并在detail声明scope仅原生进程层，`stop`与模块docstring同口径；真正两层停止属于后续Worker冒烟核对。run1/run2/run3的全部材料与其旧finding按原样保留为历史，不重写为新结论，本节即更正登记；跨进程恢复等正常事实不因此作废。

其一：main在A返回后未检查其原生组停止是否确认即运行B。现gate为恢复结论前置条件——A的`stopConfirmed`不成立时登记`attempt-b-not-started`（error，含A的停止事实与原因），抛错结束且绝不启动B、绝不声明跨进程恢复结论；unknown停止事实与保留的所有权原样留存。受控夹具（纯假client走真实`main`流程，A停止固定为unknown）断言：退出1、start仅调用1次、attempts仅含a、findings含`attempt-b-not-started`与状态unknown的`native-leader-group-stop-a`、无任何resume结论；独立命名副本整体去掉该gate后同一断言实际失败（start被调用2次、B进入流程）。

其二：`Probe.attempt`内`AcpClient.start`可抛项目既有`LaunchOwnershipError`（自带process/handle/evidence），原main只存异常字符串、丢失本次启动的停止/所有权证据。现attempt内捕获：登记真实pid/pgid与包装evidence，包装finalize未确认停止时由探针补一次owned组终止并重新`stop_evidence`观察，登记最终`stopEvidence`与`stopConfirmed`；确认不了就明确保留unknown且不再开始另一尝试（以ProbeError结束本run）。受控夹具以`launch()`真子进程（普通python，非DSH非模型）模拟post-spawn所有权错误、包装evidence固定未确认，断言：记录含句柄pid/pgid、经探针终止后组观察gone、`stopConfirmed` true、`launch-ownership-a` finding存在；变异副本把处理改为裸re-raise后同一断言实际失败（无spawnFailure登记），夹具自清理该子进程。未新写通用生命周期系统，只复用既有`LaunchOwnershipError`/`stop_evidence`/`terminate`。

聚焦验证结果（全部无原生DSH启动、无模型，脚本与材料都在返修标签目录）：`fix3-wording-check`通过（探针无旧finding名、含单层scope声明；记录含更正节且旧声明句已除）。`fix1-stop-gate-fixture`通过：假client走真实`main`，退出1、start恰1次、attempts仅a、findings含`attempt-b-not-started`、`native-leader-group-stop-a`为unknown、无任何resume结论、conclusion未生成。`fix1-stop-gate-mutation`对gate-less独立副本实际断言失败：变异运行`start`被调用2次，其证据含attempts a与b且b的operations含`session/resume`——gate被证明承重。`fix2-ownership-fixture`通过：真子进程pid/pgid登记、包装evidence未确认→探针自行终止后组观察gone、`stopConfirmed` true、`launch-ownership-a`在。`fix2-ownership-mutation`对裸re-raise副本实际断言失败：`spawnFailure`为None，所有权登记被证明承重。夹具脚本自身的四次早期失败（FakeProcess缺pid、FakeHandle缺process、变异副本任务根内CHECKOUT解析错误、夹具误把模块当实例）按原标签原样保留，属夹具缺陷非探针缺陷；每次运行都用新标签，未覆盖任何材料。

## 材料

全部一次性材料在`<task-root>/m/`下新标签目录：三个原生运行标签（run1、run2失败留档、run3，每个含`probe-evidence.json`、`state/`假微任务私有根、`cwd/`）、返修标签`r1-20261008T0407-rework/`（五份聚焦脚本、两份去guard独立命名变异副本、`__pycache__`）、聚焦验证的各次运行目录（`f1-stop-gate-*`三个夹具运行含两次早期失败留档、`f1-stop-gate-mutated*`两次、`f2-ownership*`三次，各含`probe-evidence.json`与夹具私有的dummy子进程根/状态目录）；`<task-root>/t`为原生运行的临时根，残留stub自检两文件与DSH/node的临时目录各一。四个owned ACP组（两次运行×A/B，leader与组）全部确认停止，四个stub进程随各自组退出（stub-eof留档）；discovery版本探测由discovery自身生命周期收尾；返修验证无任何原生DSH启动，fix2夹具的普通python子进程由被测处理与夹具清理共同确认停止（运行后无残留进程观察）。未删除任何对象；受管检出的两个新文件留在工作区待Host整合，未动git分支、标签或stash。
