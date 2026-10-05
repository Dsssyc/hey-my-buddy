# ADR-025 4-A:DSH 独立 Python ACP stdio 客户端(不碰公共文件的部分)

本文是 [ADR-025 执行计划](../design/adr025-execution-plan.md) 微任务 4-A 中"不碰公共文件的 ACP 客户端"部分的交付记录:在基线 `4cf58de` 的独立 worktree 中,新建 `src/hey_my_buddy/buddy/harnesses/dsh/acp/` 包与镜像测试包 `tests/python/buddy/harnesses/dsh/acp/`,用本机已安装 `dsh --profile acp` 的公开 stdio 接口实现一个独立 Python 客户端。公共接线(RunRequest/RunResult、角色模块、注册表)按计划等第一步验收后由 Host 统一整合,本包未接入任何运行路径;这不构成第四步(4-B/4-C/4-D)的完成。语义依据是已验收的 [F-D1/F-D2 核对记录](adr025-dsh-feasibility.md) 与 [Host 验收](adr025-feasibility-host-review.md)。

## 实现范围

- `launch.py`:强制私有主目录的启动包装。每次启动(含任何 test/help/version 用途)都要求调用方显式给出本次持有的私有根,`dsh-home` 与 `home` 两个目录必须已存在于其中;缺失、符号链接、越界、触及用户默认 `~/.dsh` 或用户真实 home 的路径一律在 spawn 前拒绝并记入启动日志。子进程环境是白名单:继承的 `PATH`(为 `#!/usr/bin/env node` 启发式所需)、私有的 `HOME` 与 `DSH_HOME`,外加调用方逐键显式枚举的附加项(如 `DSH_PERMISSION_MODE`);日志只记 argv、私有目录与环境键名,从不记环境值。进程经现有 `owned_popen` + `ProcessHandle` 持有,两者公共实现未改;本模块不读取任何凭据文件。
- `connection.py`:有界 NDJSON JSON-RPC 2.0 stdio 连接。读取线程按行分发:响应按 id 关联(先到先得);迟到、重复与未知 id 的响应记为有界事实,不致命、不丢失;agent 发来的请求中,`session/request_permission` 交由应答策略以嵌套 `RequestPermissionOutcome` 形状回答,其余一律 `-32601` 拒绝并记为 denied interaction;无效 JSON、超长行与无形状消息记为协议故障事实(计数有界留存)。保留通道全部有界:原始帧进字节上限的私有日志、只落在本运行的私有目录里(截断本身也是事实,完整帧只存在于这一处),内存事实只留删减摘要,stderr 只留尾部;凭据值不入任何日志(启动日志只记环境键名),进入交付的事实记录一律是删减摘要。
- `client.py`:一条原生会话路径 `initialize / session/new / session/set_config_option / session/list / session/resume / session/close`,加 `prompt` 与 `cancel` 两个原生操作接口供后续驱动复用(本客户端不驱动 prompt)。`session/new` 与 `session/resume` 的 `mcpServers` 键总是随帧发送(空数组也发;已安装 agent 实测缺该键即 `-32602`);stdio MCP 条目的 `env` 必须是 `{name, value}` 对象数组,映射形状在客户端侧拒绝(已安装 agent 对映射形状不报错但静默不挂载,故提前拒绝)。权限应答策略 `PermissionPolicy` 是保守默认:先 `reject_once` 再 `reject_always` 再 `cancelled`,未知或缺失 kind 的选项绝不选中,`allow` 仅对显式列举的工具调用标题生效;它只回答协议的升级权限请求,不用来模拟工具范围。
- 停止语义:leader 与组分开观察。`shutdownConfirmed` 仅在 leader 已退出且所属组被观察为 gone 时为真;组观察对 `PermissionError` 记 alive、对其他 `OSError` 记 unknown,unknown 按仍存活处理。EOF、会话关闭、cancel 通知(协议无应答可等)或端点失联都只是传输事实,永不充当组停止证据;确认收尾仍靠持有进程对象的 `ProcessHandle`。

## 聚焦测试

命令:`PYTHONPATH=src:tests/python uv run --frozen python -m unittest buddy.harnesses.dsh.acp.test_acp_launch buddy.harnesses.dsh.acp.test_acp_connection buddy.harnesses.dsh.acp.test_acp_session`,32 项测试约 4.5 秒,连续三次全绿。测试子目录含 `__init__.py`,检查套件的发现器已确认会调度这三个模块。

- `test_acp_launch.py`(9 项):缺私有根、未备 home 目录、用户默认 DSH home、符号链接、越界目录、用户真实 home 六种拒绝各验"spawn 前拒绝且假可执行文件未运行",拒绝入账;合法对照组执行成功,启动日志含真实 pid、envKeys 恰为 `["DSH_HOME","HOME","PATH"]` 且无任何环境值;子进程环境实测为白名单加显式附加键(子进程 Python 可能因 PEP 538 locale 矫正重执行而自增 `LC_CTYPE`/`__CF_USER_TEXT_ENCODING`,这是子解释器自身行为,非父环境泄漏)。
- `test_acp_connection.py`(13 项):initialize 与私有 home 契约(假 agent 自检 `home-check`);乱序响应各自正确关联;超时释放 id、迟到响应记为 late;重复响应记为 duplicate(以"最近已应答 id"的有界集合判定,不是竞态巧合);垃圾帧(invalid JSON、无形状对象、非对象)计为 3 项协议故障且连接不死;超长行单元测试(一条故障、后续行正常);默认策略答 `reject_once` 与 `cancelled`、fs 请求被拒并记录;显式允许标题选中 `allow_once`;未知 kind 选项永不选中的策略单元测试;EOF 打断未决请求、正常 EOF 收尾;组观察错误映射(ProcessLookupError→gone、PermissionError→alive、OSError→unknown)。
- `test_acp_session.py`(10 项):`session/new` 恒带 `mcpServers`;`env` 数组形状的客户端拒绝与线上挂载(经 `mcp_stub.py` 实际完成 MCP 握手并发现工具);配置回显(整组返回、默认值与切换值);未知会话的 `-32602` 请求错误;list/resume/close 全路径(resume 响应无 sessionId,与公开 schema 一致);cancel 通知送达且绝不是停止证据(组仍活、shutdown_confirmed 为假);leader 退出而组内孤儿子进程存活时组仍 alive、终止后准确收尾;慢 EOF 排空;无会话干净收尾。

假 agent(`fake_agent.py`,零第三方依赖)按公开 schema 与 F-D 记录的实测形状说话,自身在启动时强制校验私有的 `HOME`/`DSH_HOME`,越界即退出;故障开关覆盖延迟、重复、垃圾帧、不答即退、同组孤儿、慢 EOF。`mcp_stub.py` 是它挂载的最小 stdio MCP 服务器。

## 原生核对(授权范围内一次)

按本微任务授权做了一次最小免模型原生握手,经本客户端与启动包装执行:argv `~/.local/bin/dsh --profile acp`,私有根 `<worktree>/tmp/adr025-dsh-acp-client/probe-root/`(内含 `dsh-home/`、`home/`、`logs/`)。流程严格为 initialize → 空 `session/new`(`mcpServers: []`,cwd 为私有根)→ 从 session/new 响应回读公开配置 → `session/close` → EOF 停机;未发送 prompt,未改任何配置项,模型调用 0 次。

实测:initialize 回报 protocolVersion 1、agentInfo `deepseek-harness-acp` 0.0.1、sessionCapabilities close/list/resume、authMethods 空,与 F-D1 一致;空会话返回完整配置(model 选择器当前值 `["deepseek-official","deepseek-v4-flash"]`,reasoning_effort `high`),与 F-D1 的默认值一致;`session/close` 无错误;停止证据 leader 退出码 0、组观察 gone、shutdownConfirmed true;连接事实零故障、零未匹配响应;全程约 1.2 秒。启动账目:本次微任务原生 DSH 启动共 1 次(即该握手),模型 prompt 0 次;本执行 harness 自身未启动其他原生 harness。

材料(创建时逐项登记,全部保留,未删除):`<worktree>/tmp/adr025-dsh-acp-client/handshake.py`(驱动脚本)、`handshake-summary.json`(摘要)、`probe-root/dsh-home/`(私有 DSH home:具体化 acp profile、`.anonymous-user-id`、被关闭空会话的记录与投影检查点)、`probe-root/home/`(私有 HOME)、`probe-root/logs/launches.jsonl`(启动账目,含 pid 与环境键名)、`probe-root/logs/frames.jsonl`(原始帧,2,961 字节)。按清理规则未执行任何删除,也不为整洁做额外清理;本微任务未访问用户日常 `~/.dsh`(包装在 spawn 前按路径拒绝,构造上不可能写入),因此未做日常目录快照。

## 边界与未验证事实

- 本客户端未接入公共接口:无 RunRequest/RunResult 投影、无角色模块调用点、未进注册表、无 Worker/Router 分支;归类(未知 tool kind 保持 other)、完成工具、问询、角色预算、结果与封存、黑板判定都不在本包,也不在本文宣称范围。
- `session/list` 与 `session/resume` 只有假 agent 证据:授权的握手流程不含它们,对真实 DSH 未验证;实现按公开 schema 的形状(含 `session/list` 的 cwd/cursor 过滤与 resume 响应无 sessionId)。`prompt`/`cancel` 对真实 DSH 未验证(本微任务无 prompt 授权);权限请求的客户端应答路径只有假 agent 证据,真实 DSH 侧的权限行为以 F-D2 的核对为准。
- 工具范围(none/read/write 的启动配置)、`DSH_PERMISSION_MODE` 注入、DSH 系统提示词、检查点问询、可选私有会话记录的模型身份与逐步用量、HTTP 形态 MCP、额度与费用、token 用量口径,全部属于 4-B 及之后;当前不存在"Worker token 用量等同"的事实,也未做此类宣称。
- 平台边界:全部验证限于 macOS 本机、本机已安装的 `dsh` 0.1.5-rc.1(与 F-D 记录同一安装);Linux/Windows 未验证。`HOME` 强制覆盖在 Windows 上的等价物(`USERPROFILE`)未处理,属后续步骤的平台事项。
- 实现层面两处实测更正:子进程 Python 在白名单环境下的 PEP 538 重执行会自增 locale 键(测试按此口径断言);`FrameLog` 截断与协议故障一样是"原生流被截"的事实而非错误。

## 第 2 回合修正(2026-10-03,Host 拒绝固定交付 33cba18 后 continue;本回合零原生 DSH 启动、零模型调用)

Host 以固定字节核对拒绝第一回合交付并列出七组缺陷(A 至 G)。以下逐项记录本回合的修复;第一回合的原文作为历史保留,其中被推翻的表述在本节逐条作废。本回合只重跑受影响的聚焦测试与新增合成复现,未启动任何真实 DSH 或模型;第一回合那次免模型原生握手的原始四份材料已由 Host 保留至 `<evidence-worktree>/tmp/adr025-evidence-retained/acp-client-first-7l1eyhmf/native-handshake/`,本回合未读取其中的私有 home、会话或凭据内容。

- **A(启动契约)。** `extra_env` 现在显式拒绝保留键 `HOME`/`DSH_HOME`(拒绝发生在 spawn 前、假可执行文件不运行),且经过验证的私有目录值在任何合并之后最后写入,双保险;新增两项拒绝测试与子环境对照。`launch_log` 与 frame log 路径经 `ensure_private_log_path` 绑定本次私有根:父目录按项目 `private_dirs.ensure_private_dir`(0700)创建、全路径拒绝链接组件(`private_dirs.linked_component`)、realpath 必须落在私有根内,参数无法把日志引到外部;拒绝发生在 spawn 之前并有测试覆盖(launch 与 frame 两侧)。spawn 之后的簿记故障(`record_launch` 抛错)与连接启动故障(frame log 打开、线程启动)不再丢失所有权:子进程被就地收尾(关 stdin、等 leader、保守观察组、必要时一次 terminate),并以 `LaunchOwnershipError` 携带 process、handle 与停止证据抛出;合成故障测试(monkeypatch `record_launch` 抛错、坏 frame log)验证证据齐备且组确认消失。
- **B(EOF 覆盖已接纳响应)。** 读取线程的 EOF 收尾不再无条件改写 waiter:只有未 resolved 的条目才被置为 EOF 态,已接纳的响应原子保留(两处均在 `_pending_lock` 内判定);新增"应答后立即退出"与"不答即退"两个方向测试。
- **C(JSON-RPC 形状验证)。** 每帧先验 envelopes:`jsonrpc` 必须 `"2.0"`、method 必须非空字符串、id 必须是 int 或非空字符串(bool 明确拒绝,`True == 1` 不能冒充本端 id)、响应 result/error 恰好其一且 error 必须是对象、params 只能是数组或对象;不合格式记为有界协议故障并丢弃,不击穿读取线程、不关联任何 waiter。未知 id 不再一律记 late:本端发出且已超时的 id 记 `late`,已应答的记 `duplicate`,其余记 `unknown`(以有界的最近已答/已超时 id 集合判定)。新增敌意帧测试(7 种畸形帧 + 真实响应穿插)验证真实结果存活、6 项故障、999 号响应记 unknown、后续请求正常。
- **D(有界性与期限)。** 超长入站行改为固定内存丢弃:超限后不再继续积累,逐块喂给增量哈希,换行时记录总字节数与整行摘要(8 MiB 无换行输入的合成测试验证账目精确、后续行恢复);出站帧超过 1 MiB 上限在写任何字节前拒绝(`ACP_OUTBOUND_FRAME_TOO_LARGE`),失败 send 释放其 pending 条目;request 的一次预算现在同时覆盖写入与等答(写线程 + `sent` 事件),agent 停止读取 stdin 时请求在预算内以"无法写入"超时,不再无限卡住("冻结不读"假 agent 合成测试验证 2 秒预算约 2 秒返回且 pending 归零);distinct 通知类型上限 64,溢出计入 `<overflow>` 并如实置 truncated 标志;权限决定中的 options 每条上限 16 并带 `optionsTruncated`;id 类字段定长截断。
- **E(留存改脱敏)。** 第一回合"任何留存不包含完整工具参数或完整工具输出"的表述**作废**:原始帧日志当时确实整帧落盘,facts 的 `titleBrief`/`paramsBrief`/`preview`/`resultBrief` 也携带内容截断。现改为:客户端不再持久化任何帧内容——`FrameMetaLog`(原 FrameLog 更名)每帧只记方向、字节数与 12 位摘要;全部内存事实只含闭集元数据(方法名、选项 kind/optionId、计数)、长度与摘要,原文只经 `observe` 回调与请求返回值即时交给可信调用方;观察者异常不再静默,记为 observation failure 事实(计数 + 有界记录)。合成哨兵测试验证标记字符串不出现在任何 facts 或日志行,元数据日志行只含 bytes/digest。
- **F(权限标题)。** `allowed_titles` 改为精确匹配,前缀相似的标题(`listed-title-extra` 对 `listed-title`)不再被授权,配置了允许清单时未列出的标题一律 `cancelled`(即使存在 reject 选项);参数形状不合法(params 非对象、options 非数组、toolCall 非对象、title 非字符串)由策略保守应答 `cancelled` 并在 basis 写明,不抛入读取线程。单元与线上测试均覆盖。
- **G(测试清理契约)。** `support.py` 重写:测试材料只放本检出 ignored `tmp/` 的专用容器 `tmp/adr025-dsh-acp-tests/<run>/`(mkdtemp 原子创建,创建即登记);每个创建路径(容器、逐测试目录、native 根、外部兄弟目录、symlink 目标)在创建时逐项写入 `manifest.jsonl`(确切路径、用途、时间),每次成功删除追加 `deletions.jsonl`;停止失败或组未确认消失时保留该测试目录并在 cleanup 中显式失败,绝不在未确认停止 behind 场景删除状态;删除错误不让测试静默通过。两轮共 17 个 run 容器、882 条创建登记、407 条删除记录全部在留;唯一例外是挂死回合(run-c2_h0xh8)的逐测试目录:其 harness 被按 PID 终止(见下)未能执行自身清理,该目录按规则保留并在此列明。第一回合"未执行任何删除"的表述**作废并更正**:第一回合的测试 teardown 在每轮运行中删除过自己创建的 mkdtemp 临时根(系统临时树),这些路径创建时未逐项登记,确切清单按规则记为未知,不做回溯扫描猜归属;除此之外第一回合未删除任何对象。
- **本回合事故如实记录。** 一次测试运行因真实死锁挂起:`shutdown()` 在读取线程仍阻塞在 `read1` 内时关闭带缓冲的 stdout 管道,`BufferedIO.close` 无限期等待缓冲锁;按 PID 终止了本次运行自己的三个进程(unittest 主进程、parked 假 agent、uv 前进程),随后修复 `shutdown()`(各读取线程在 EOF 时自关自己的流;shutdown 只在对应线程已结束后才补关)。挂起回合的逐测试目录保留如上。修复后全套 48 项测试连续三次全绿(每次约 20 秒,含冻结写入、超长行、敌意帧等全部合成故障);检查套件的发现器确认仍调度这三个测试模块。
- **本回合账目。** 原生 DSH 启动 0 次、模型 prompt 0 次;第一回合的累计账目(原生启动 1 次、prompt 0 次)不变。修复后包对外形状变化:`FrameLog` 更名 `FrameMetaLog` 且只写元数据;新增导出 `LaunchOwnershipError`;`AcpConnection.facts()` 新增观察失败、pending 计数与截断标志字段。公共文件仍零改动,git 状态仍只有三个授权路径加 ignored `tmp/`。

## 第 3 回合修正(2026-10-03,Host 拒绝固定交付 f93078a2/2485fa3 后 continue;本回合零原生 DSH 启动、零模型调用)

Host 以固定字节核对与零进程最小探针复核第二回合交付,列出六项仍在范围内的缺陷。以下逐项记录修复;前两回合原文保留,历史结论被推翻处在本节作废。本回合只跑新增与受修复影响的聚焦测试,全套 65 项一次通过(约 12 秒),另对新增的并发写入与原子性测试做了一次确认重跑(6 项,全绿),未做完整检查、Console、打包、真实 DSH 或模型运行;累计原生 DSH 启动仍 1 次、prompt 0 次。

- **日志路径拒绝的副作用。** 探针证实被拒的外部日志路径已先建目录(`outside_created_before_rejection=True`)。现改为任何创建/chmod 之前完成全部核对:私有根本身的存在性、真实目录、链接与默认目录拒绝;日志路径的链接组件;realpath 包含关系。只有核对全部通过后才在已验证的私有根内创建日志父目录(0700),拒绝不触及外部对象。`launch()` 也在一切之前显式验证私有根,根不可信时不写任何日志。新增无副作用测试:被拒的缺失父链不创建、既有目录 mode 前后对照不变、默认根与链接根直接调用拒绝且目标零变化、根缺失/链接的启动拒绝不产生日志。
- **响应完成的原子性。** 探针证实在锁内置 `resolved=True` 而消息与唤醒在锁外,期限可在两者之间观察到"已解决、无消息"而误抛连接已闭。现把 `resolved`、`message` 的写入放在同一段临界区内,唤醒在锁外仅作信号;期限路径在锁内看到 resolved 即可取到完整消息。确定性竞态测试在派发线程上轮询这一转移:任何一次观察到 resolved 的瞬间消息必须已在,无 sleep 巧合。
- **断帧与协议形状。** 三处补齐:超长行若直到 EOF 都无换行,现在在 EOF 处记故障(精确字节数与整行增量摘要;此前 8 MiB 无换行输入故障数为 0,测试与记录描述一并更正);无效 UTF-8 是显式故障(字节摘要),不再是静默替换内容;超过 JSON 解析深度的帧记 `too-deep` 故障(此前 RecursionError 会击穿读取线程)。带 method 的帧同时携带 result/error 现在按互斥形状拒绝(`bad-request-shape`),不会到达处理器。响应 error 的基本字段(code 为非 bool 整数、message 为字符串)不合格式记 `bad-error-shape`。`_handle_agent_request` 的记录对坏字段与应答同样保守:`toolCall` 为列表、`options` 为整数不再抛 AttributeError/TypeError 击穿线程,而是以 `toolCallShape`/`optionsShape` 元数据如实记录;线上畸形 permission 帧测试验证应答照发、回合照常完成。
- **写入资源与期限。** 每次 request 新建 daemon writer 的做法废除:所有出站帧(request、notify/cancel、读取线程的 agent 应答)改走唯一的有界写入线程与有界队列(16),每帧一个覆盖排队与写入的绝对期限。排队中到期的帧被原地取消、永不出线("排队超时不再送出");已开始而未能在期限内完成的写入记为不确定送达(`writeUncertain` 事实),在管道恢复或断裂时结算为 delivered/failed,阻止盲目重用/重发;写入失败、队列满、reply 无法投递均是有界事实。恢复/终止后写入线程收尾:shutdown 停止写入器、对其从未写出的帧给出失败结果并 join。合成测试:同一冻结 agent 上连续 5 次超时后写入线程仍恰为 1,`pendingWrites` 为 1 活动加 4 已取消,元数据日志出站帧计数冻结不动,终止后不确定态结算、队列按事实排空;cancel 在大帧占住管道后于自身期限内超时;队列填满后 reply 立即记 `reply-not-delivered`,读取线程不受阻塞。
- **启动故障与测试清理。** `finalize_after_spawn_failure` 现在永不抛出:关 stdin、等退出、组观察、terminate 每步都受守卫,二次故障进入证据的 `finalizeErrors`,unknown 不写 stopped,process/handle 始终随 `LaunchOwnershipError` 交付(合成观察故障测试验证 OSError 不再穿透)。`support._shutdown_client` 对 terminate、wait 与最终观察的异常同样先 preserve 再显式失败,未确认停止绝不删除状态(此前只覆盖 shutdown 本身的异常)。
- **留存更正。** `AcpRequestError` 的字符串形式只含元数据(方法与错误码),此前 `brief(error, 400)` 会把 data 中的标记带进异常文字(探针已复现);原始 error 对象仍作为 `.error` 即时交给可信调用者,不进入事实/日志。三种截断事实自本回合集分开:源流故障(协议故障)、观察失败、可选元数据日志的留存截断(facts 字段 `metaLogTruncated`);第二回合"FrameLog 截断是原生流被截"的表述**作废**——它是可选日志的留存事实,与本次工具事实完整性互不影响。
- **本回合账目。** 原生 DSH 启动 0 次、模型 prompt 0 次;Host 已将本次复核材料保留至 `<evidence-worktree>/tmp/adr025-evidence-retained/acp-client-second-78hz5cna/`(只读,未动)。本回合测试材料继续走 `tmp/adr025-dsh-acp-tests/<run>/` 专用容器,创建/删除逐项登记;无新增保留目录。git 状态仍只有三个授权路径加 ignored `tmp/`;公共接线仍等第一步外部验收。

## 第 4 回合修正(2026-10-03,Host 拒绝固定交付 ff1f13a7/9cf9f84 后 continue;本回合零原生 DSH 启动、零模型调用)

Host 的零进程确定性探针证明写入状态机还剩两处竞态,本回合把排队/取消/开始、完成的落锁与超时观察收敛到同一把写队列锁保护的原子转移;唯一写入线程对已开始作业的收尾(不确定计数递减与标志撤销)在锁外按序执行;修复边界与验证如下,前三回合记录原样保留。本回合只跑新增与受影响的聚焦测试;全套 69 项连续六次全绿,累计原生 DSH 启动仍 1 次、prompt 0 次。

- **取消与开始互斥(探针竞态一)。** 原实现里 writer 在检查 cancelled/期限之后、`job.started` 赋值之前可被暂停,超时方此刻置 `cancelled=True`,writer 恢复后仍会写出被取消的帧(探针:释放后 sent=1)。现在关键转移只经三个函数,其中 claim 与期限观察整体在锁内、结算只落锁一半:`_claim_job` 在锁内一并检查取消与期限并置 started(被取消的作业就地结算为 `("cancelled",)` 并记 `cancelled-unsent` 事实,永不写线);请求方超时观察 `_observe_write_after_deadline` 在同一把锁内判定——已结算的作业返回 settled(完成抢先到,绝不逆转为不确定),未开始的置 cancelled,确已开始而完成未确认的才置 `job.uncertain` 与不确定事实;`_settle_job` 在锁内写入 outcome 并置 done,不确定的结算(计数递减、`write_uncertain` 撤销、结算事实)由唯一写入线程在锁外按序执行。判定测试按两种确定顺序钉住这条边界:取消先于 claim(释放 writer 后作业以 cancelled 结束、`started` 保持假、线上一律无该帧)与 claim 先于取消(取消成为无操作、帧照常写线),外加 40 轮两序轮替:被 claim 的帧全部到达假 agent,未 claim 的帧一律不可见。
- **完成与超时观察相邻(探针竞态二)。** 原 `_await_write` 在 `done.wait` 超时后先读 started,完成侧恰好插进来的作业会被永久标成不确定。修复后观察原子进行,已结算作业按结果继续处理;不确定事实带 `job.uncertain` 标记与连接级计数,结算时对账递减、计数归零即撤销不确定——完成与超时相邻时最终事实必然结算。判定测试直接构造三种边界:先写完再观察得 settled 且无不确定;claim 后挂起写线再观察得 uncertain 且事实置位,结算后撤销并留下 `uncertain-settled` 事实。
- **两处测试夹具缺陷(新测试暴露,如实记录)。** 其一:假 agent 的并发 handler 线程无锁直写 stdout,行交错会产出损坏帧,客户端侧表现为偶发 15 秒超时;已加发送锁。其二:第三回合为冻结功能改写的 serve 循环在 fd 上 select、却用带缓冲的 `readline` 取帧——同一次读取合并进缓冲的后续帧对 select 不可见,会饿死到新数据到来(25 次复现中 13 次失败的根源);已改为直接 `os.read` 并自行按行切分,冻结语义(置位后不再读、驻留存活)不变。修复后 25 次定向复现零失败,全套 69 项连续六次全绿。
- **本回合账目。** 原生 DSH 启动 0 次、模型 prompt 0 次;Host 复核材料保留于 `<evidence-worktree>/tmp/adr025-evidence-retained/acp-client-third-1cnv6en0/`(只读,未动)。本回合一次性材料:检出 ignored `tmp/adr025-dsh-acp-client/` 下的九份日志,其中三份失败/基线日志 `suite-run-1.log`、`suite-run-3.log`、`suite-fix-run-6.log`,六份最终全绿日志 `suite-final-1.log` 至 `suite-final-6.log`(唯一名,保留不删);同目录其余运行日志一并原样保留,不逐项追记。测试私有根照旧走 `tmp/adr025-dsh-acp-tests/<run>/` 登记。git 状态仍只有三个授权路径加 ignored `tmp/`;公共接线仍等第一步外部验收。

## 第 5 回合修正(2026-10-03,Host 复验源码通过后仅修记录;本回合零测试、零原生、零模型)

Host 复验确认 4cc0d56 的源码修正通过固定字节、原子边界与两项旧问题复验,69 项六份最终日志均为通过,本轮无新增源码阻塞;拒绝仅针对第四回合记录的两处与实际不符的表述。本回合只修改本文,未改任何源码或测试,未重跑任何测试/探针/原生/模型,未删除任何对象。两处更正:其一,第四回合"四份失败/基线日志"的计数与列举不符,实为九份(三份失败/基线加六份最终全绿),已按确切日志名改齐,同目录其余运行日志保留但不逐项追记;其二,"全部转移在同一把锁上"与"`_settle_job` 在锁内结算不确定计数"的表述过强——锁内保护的是 claim、完成的 outcome/done 落锁与期限观察,`job.uncertain` 的结算、连接级计数递减与标志撤销在锁外由唯一写入线程按序执行,第四回合段落已按此改写。前两回合的拒绝、撤回与夹具故障/重复次数,以及六次 69 项验证的口径,均原样保留。
