# ADR-025 F-D1:DSH ACP 无模型握手可行性核对

本记录是 [ADR-025 执行计划](../design/adr025-execution-plan.md) 第 6 节 F-D1 微任务的验收材料:在基线 `006b2fb` 的隔离 worktree 中,对本机已安装的 `dsh --profile acp` 做不调用模型的 stdio 握手,逐项回答 ADR-025 第 11 条六问的免费部分,并按 actual(握手实测)/ declared(随安装公开文档与公开协议 schema 声明)/ unknown(待 F-D2 真实运行验证)三分记录。握手全程未发送 prompt、未提交用户输入、未产生任何模型调用;控制面(初始化、空会话、会话配置、MCP 挂载、关闭、恢复)在无凭据的私有主目录下全部可用。握手无错误不等于六问通过:每项的 unknown 清单就是 F-D2 的工作输入。

## 方法与材料

- ACP 协议握手共三次,各启动一个带独立进程组的原生进程:run1 首次执行(配置选择取值有误,请求了与当前相同的值)、run1 修正后重跑(真实切换到不同已声明项)、run2(私有 `DSH_HOME` 加 `--patch` 覆盖会话持久化根)。run1 与 run2 的流程相同:initialize 后创建空会话,run1 挂载零依赖 Python stdio MCP stub 并做配置切换与两个负例(不支持的方法、坏 MCP 命令),`session/list` 后 `session/close`,以 stdin EOF 停机;run2 依次 `session/new`、`session/close`、`session/list`、`session/resume`、再次 `session/close`,最后对进程组发 SIGTERM 停机。完整启动账目(含全部诊断调用)见下节。
- 探测材料都在忽略的 `<worktree>/tmp/adr025-dsh/` 下:`acp_probe.py`(零依赖 Python stdio JSON-RPC 客户端驱动)、`mcp_stub.py`(只应答 MCP initialize/tools/list 的空工具服务器,其工具声明为从不被调用)、`patch-sessions-root.yml`(run2 的覆盖文件)、`launch_dsh.py`(强制私有 home 与最小环境的启动包装,含 `--self-test` 故障检查;`acp_probe.py` 现经它启动以约束**后续**调用——既有三次握手是在包装存在之前以继承环境加 `DSH_HOME` 启动的,历史证据未改)、`logs/run1-summary.json` 与 `logs/run2-summary.json` 与 `logs/run1-summary.samevalue.json`(筛选后的运行摘要,run1 文件按执行顺序对应)、`logs/run1-frames.jsonl` 与 `logs/run2-frames.jsonl`(原始帧;run1 帧文件按时间追加,含同值试运行与重跑两次执行,共 2 次 initialize)、`logs/run1-mcp-stub.log`、`logs/launch-wrapper-fault-check.json`(包装故障检查证据)、`dsh-home-before/after*.txt`(日常目录快照)、`acp-schema-extract.txt`(公开 schema 摘录);私有 home(`dsh-home/`、`dsh-home2/`)、patch 会话根(`patch-sessions/`)、空工作目录(`workspace/`)与故障检查目录(`fault-check/`,fake executable 与 marker)保留为一次性材料,可随时丢弃。
- declared 的来源是随安装公开文档(`@deepseek-ai/dsh-acp`、`@deepseek-ai/dsh-acp-app` 的 README 与随安装 cordis patch 配置)和随安装 `@agentclientprotocol/sdk` 的公开 `schema.json`;没有静态阅读厂商实现代码来证明任何能力。
- 环境边界:**本微任务违反过一次日常目录边界**——账目第 4 条 `dsh --profile acp --dump-default-config` 在未设 `DSH_HOME` 的日常 home 下执行,创建了 `~/.dsh/profiles/acp/`;该目录已原样删除,残留是 `~/.dsh/profiles` 目录自身的 mtime 变化,详情与账目见"原生启动与运行次数账目"和"日常目录事实"。按九次账精确划分:第 1–4 次未隔离(日常 home,其中第 4 次造成上述违反),第 5–9 次带私有 `DSH_HOME`;第 1–2 次发生在日常目录基线快照之前,是否有写入 unknown。三次握手的子进程环境是当时的**继承环境加 `DSH_HOME`**(`acp_probe.py` 原实现),不是后来才新增的 `launch_dsh.py` 最小白名单——后者只约束后续调用,尚未用于任何真实握手。不读取凭据文件内容;未安装、未升级任何 DSH 组件,探测脚本零第三方依赖。

## 原生启动与运行次数账目

本微任务全部 dsh 启动器调用按时间顺序如下(依据本回合留存的外壳命令记录与帧日志重建;模型调用为 **0 次**,三份帧日志中没有任何 `session/prompt`):

| # | 调用 | DSH_HOME | 结果 |
| --- | --- | --- | --- |
| 1 | `dsh --version` | 未设(日常) | 打印版本后退出 |
| 2 | `dsh --help` | 未设(日常) | 打印顶层 help 后退出 |
| 3 | `dsh --dump-default-config` | 未设(日常) | 用法错误退出(缺 `--profile`,启动器进程有启动) |
| 4 | `dsh --profile acp --dump-default-config` | 未设(日常) | 打印出厂 acp 模板树;**违反日常目录边界**(具体化 `~/.dsh/profiles/acp/`),已清理 |
| 5 | `dsh --profile acp --help` | 私有 `dsh-home` | 打印 acp help 后退出 |
| 6 | `dsh --profile acp --dump-config` | 私有 `dsh-home` | 打印组合配置树 |
| 7 | ACP 握手 run1 首次执行 | 私有 `dsh-home` | 完整握手(配置选择请求了与当前相同的值);摘要保留为 `logs/run1-summary.samevalue.json` |
| 8 | ACP 握手 run1 重跑 | 私有 `dsh-home` | 完整握手(真实切换);摘要 `logs/run1-summary.json` |
| 9 | ACP 握手 run2 | 私有 `dsh-home2` + `--patch` | 完整握手(覆盖持久化根、resume、SIGTERM);摘要 `logs/run2-summary.json` |

- 因此:**ACP 协议握手 3 次**(帧日志证据:`run1-frames.jsonl` 含 2 次 `initialize`,`run2-frames.jsonl` 含 1 次),**dsh 启动器调用共 9 次**(4 次日常、5 次私有),模型调用 0 次。另有一次纯 node 诊断(`node --version`,非 DSH)和一次未遂启动(首次 run1 的外壳重定向失败,python 未运行、原生进程未启动)。
- unknown:第 1、2 条发生在日常目录基线快照之前,是否对日常目录有写入无法由快照差核对,记 unknown(未观察到可归因于它们的任何产物);其余各条的写入位置均已由快照差或帧日志核实。

## 版本与启动事实

| 项 | actual |
| --- | --- |
| 启动器 | `~/.local/bin/dsh`(symlink 指向 `~/.local/share/dsh/bin/dsh`;shebang `#!/usr/bin/env node`,PATH 上为 nvm 的 node v24.21.0) |
| argv | 三次握手均为 `dsh --profile acp`(run2 另加 `--patch <probe>/patch-sessions-root.yml`),由当时的 `acp_probe.py` 以**继承环境加 `DSH_HOME`** 启动——`launch_dsh.py` 最小白名单包装是其后才新增的,尚未用于任何真实握手(目前仅以假可执行文件核对过拒绝路径);诊断调用见上节账目 |
| 版本 | 启动器与 `@deepseek-ai/dsh` 0.1.5-rc.1,`dsh-acp`/`dsh-acp-app` 0.1.5-rc.2;initialize 实际回报 agentInfo `deepseek-harness-acp` 0.0.1、protocolVersion 1 |
| stdin 方法 | 换行分隔 JSON-RPC;实际只发过 initialize、session/new、session/set_config_option、session/list、session/close、session/resume 和负例 session/set_mode;authMethods 为空,authenticate 未调用(声明"服务器不需要身份验证") |
| stdout 纯净 | 三次握手全部 stdout 帧均为合法 JSON-RPC,无一行混杂(与 declared"组合包不向 stdout 写非协议内容"一致) |
| 启动副作用 | 首次以某 home 调用 `dsh --profile acp`(包括 `--dump-config`/`--dump-default-config`)都会在 `<DSH_HOME>/profiles/acp/` 具体化随安装模板(含 pnpm 工作区),并在 home 下创建 `.anonymous-user-id`(37 字节的匿名遥测标识,非凭据) |

初始化实际能力:`agentCapabilities = {mcpCapabilities: {http: true}, promptCapabilities: {image: false, audio: false, embeddedContext: false}, sessionCapabilities: {close, list, resume}}`。`loadSession`、`delete`、`additionalDirectories`、`fork` 与 SSE/ACP 传输 MCP 均未公布,与 declared 一致:仅标准 ACP v1 自动化界面,`session/load`、删除、fork、mode、命令、计划、终端、客户端文件系统操作与 elicitation 都不支持。

## 六问逐项

### 1 哪些工具发权限请求,能否拒写/命令/联网

- declared:`session/request_permission` 由 agent 发给客户端,选项带 `allow_once`/`allow_always`/`reject_once`/`reject_always` 四种 kind,客户端可自动应答(响应为 `{outcome: selected, optionId}` 或 `{outcome: cancelled}`);随安装 README 写明"带一次性允许/拒绝选项的权限提示;你的客户端可以自动回答"。
- actual:初始化能力没有权限相关位(公开 schema 本就没有该能力位);无 prompt 握手期间未出现任何权限请求,探测客户端的自动拒绝应答路径未被触发。附带:客户端侧 `fs/*` 与终端方法未公布,控制器不需要代答文件读写。
- unknown(待 F-D2):哪些 DSH 工具实际发出权限请求;deny 对写入、命令与联网是否真实阻止。计划第 6 节对此已注明"未出现实际调用的工具覆盖仍标待验证"。

### 2 用量能取得多少

- declared:公开 schema 的 `session/update` 含 `usage_update {used, size, cost?}`(上下文窗口占用与可选费用);随安装 README 声明 update 携带"上下文用量"并按会话串行交付。
- actual:三次握手 `session/update` 通知均为零条,没有 usage_update 样本;配置变化也不产生通知,完整状态只在响应中返回。
- unknown(待 F-D2):真实回合是否实际发出 usage_update 及其时机;used/size/cost 的作用域与累计口径;额度在协议 schema 中不存在,确认协议层不可得。

### 3 会话存储能否指向私有目录

- declared:组合配置中 `session-persistence-jsonl.root = dshHomePath('sessions')`、`storage-json.root = dshHomePath('storages')`;`DSH_HOME` 与启动器 `--patch` overlay 都是公开面。
- actual(私有 home):设 `DSH_HOME=<私有>` 后,profile 具体化、`.anonymous-user-id`、会话记录与投影检查点全部落在私有 home。会话记录布局为 `<sessions 根>/<展平的 cwd 路径>/<sessionId>/`,含 `session.v3.jsonl.zstd`(空会话约 454 字节)与 `session.lock`;投影检查点在 `<home>/storages/session_projcache/sessions/<sessionId>.json`。空会话(无 prompt)`session/close` 后即持久化,`session/list` 能列出(活动中不出现,与"已持久、可恢复的根会话"声明一致),`session/resume` 能恢复并返回完整配置;会话 cwd 零写入。
- actual(patch 覆盖):`--patch` 把 `session-persistence-jsonl.root` 指到第二个私有目录后,会话记录确实落在该处,home 下连 `sessions/` 目录都未创建;但 `storages/session_projcache/` 检查点仍随 `storage-json.root` 留在 `DSH_HOME` 下。
- 结论:能,且有两种公开手段。F-D2 要完全隔离应设私有 `DSH_HOME`(会话与检查点同迁)或同时 patch 两个 root;只 patch 会话根会在 `DSH_HOME` 留下检查点。

### 4 模型与推理强度选择、回报是否可靠

- declared:`session/set_config_option` 串行更新公布的 `model` 或 `reasoning_effort` 并返回完整结果状态;配置项来自 LLM 服务目录,确切模型声明推理选项时才提供 reasoning_effort;提示词在准入时快照选择,并在该轮每个模型步骤固定。
- actual:无凭据的空会话即返回完整配置。model 选择器(类别 `model`)值为 JSON 字符串形如 `["deepseek-official","deepseek-flash"]`,分组目录四项:deepseek-flash、deepseek-v4-flash(默认)、deepseek-v4-pro、deepseek-v4-flash-vision-exp;reasoning_effort 选择器(类别 `thought_level`)为 off/low/high/max,默认 high。`set_config_option` 切换到不同已声明项(model → deepseek-flash,effort → off)后,响应返回整组状态且 currentValue 等于请求值;不产生 `config_option_update` 通知;未持久化任何用户偏好(私有 home 未出现 settings.yaml)。
- 注意点:目录显示名与值 id 不同(显示名 DeepSeek-V41-Flash 对应值 deepseek-flash);acp 桥随装配置的默认路由是 `deepseek-official`/`deepseek-v4-flash`,而 base 的 `agent-default-model` 配置项是 `deepseek-official`/`deepseek-flash`,两者是不同条目,核对时不可混用。
- unknown(待 F-D2):所选配置在实际回合的真实生效——按计划要求 requested/checked/observed 分列,请求值与会话回读值不能冒充实际模型;目录在无凭据时即可用,是随包默认还是匿名在线拉取未区分。

### 5 Python MCP 完成工具是否可行

- declared:`session/new` 校验绝对 cwd 工作区与 stdio/HTTP MCP 声明,stdio 条目授权绝对命令与环境,初始连接或工具同步失败会回滚尚未发布的 agent;协议面只有 MCP 工具,resource/prompt 无消费方。
- actual:零依赖 Python stdio MCP 服务器经 `session/new` 挂载到空会话成功:DSH 拉起 stub,依次发生 MCP initialize、notifications/initialized、tools/list(stub 日志为证);负例(不存在的命令)返回 -32603 "mcp-client(...): initial connection or tool synchronization failed" 并按声明回滚,agent 存活、后续调用正常;`session/close` 时 stub 收到 stdin EOF 退出,后代被释放。全程无模型调用。
- unknown(待 F-D2):完成工具在真实回合被原生调用的完整证据链(native call/结果/结束事件的 ID、顺序与关联,参数经本次 schema 校验,签名回执);工具清单不产生 `available_commands_update` 通知。initialize 公布 `mcpCapabilities.http=true` 属于 declared——本次未挂载任何 HTTP MCP 服务器,HTTP 形态未经实测,不得当作已验证能力。

### 6 问询只能检查点还是实时送达

- declared:客户端→agent 的全部方法只有 initialize、authenticate、session/new、session/list、session/resume、session/close、session/set_config_option、session/prompt、session/cancel(及 `$/cancel_request`);协议没有向进行中回合插入输入的方法;acp-app README 声明投影缓存为 ACP 会话写检查点(会话内容检查点,与问询无关)。
- actual:`session/set_mode` 实测 -32601 Method not found(session/new 的 `modes` 为 null);三次握手除请求响应外没有任何通知;持久化层实际观察到投影检查点文件(`storages/session_projcache/sessions/<sessionId>.json`),它是会话内容的检查点,**不是**问询送达检查点,不能据此宣称问询可送达。cancel 的空闲 no-op 语义只有 declared,未实测(没有进行中的回合可取消)。
- 边界判断:ACP v1 公开方法面没有已确认的实时输入通道——这只是协议边界事实,不等于问询能力已有结论。问询能否经会话内 MCP 工具在合作检查点送达、检查点问询能否工作,是未验证项,归 F-D2;本核对没有证明任何问询可送达。ADR-025"影响"已写明的三项之一(问询可能只能在检查点送达)按计划进行,但其是否成立以 F-D2 的实际运行为准。
- unknown(待 F-D2):经 MCP 工具实现合作检查点问询是否可行及其送达时机与签收形态;cancel 在真实运行中的中断事实与停止语义。

## 停止与组停止证据

- 两次 run1 执行(stdin EOF)与一次 run2(SIGTERM):EOF 后原生进程退出码 0,`killpg(pgid, 0)` 报 ProcessLookupError(进程组消失),stderr 为空;SIGTERM 后退出码 0,组消失,stderr 为空;各次墙钟约 2.4 秒(含启动与 profile 具体化)。首次 run1(同值试运行)的停机证据与重跑一致,其摘要保留在 `logs/run1-summary.samevalue.json`。
- declared:stdin EOF 绑定启动器的有界成功关闭;ACP 连接关闭、SIGINT、SIGTERM 都在退出前排空 bridge 自有 agent 与根 profile 树;`session/close` 做停稳式取消、更新 drain、后代释放、持久化 flush。
- 说明:本核对只提供"可观察时"的积极证据;无法观察时按还活着处理的保守口径是运行模块的实现责任(ADR-025 第 8 条),不属于本次核对范围。

## 日常目录事实(边界违反、恢复与残留)

- 核对方法:探测前对 `~/.dsh` 做全树路径+mtime 只读快照,探测后 diff。
- **边界违反**:账目第 4 条 `dsh --profile acp --dump-default-config` 在未设 `DSH_HOME` 的日常 home 下执行,创建了 `~/.dsh/profiles/acp/`(随安装模板的四个文本文件,共 16KB,无 node_modules)。这是本微任务对"不改日常数据"边界的一次实际违反,不因已清理而消失。
- 恢复范围与残留:该目录确认仅为本次命令产物后已原样删除;清理后 diff 显示与基线的唯一差异是 `~/.dsh/profiles` 目录自身的 mtime(内容零变化),settings.yaml、sessions、storages 与凭据均未读写。mtime 残留如实保留,**不再触碰日常目录、不尝试恢复 mtime**;快照留存在 `dsh-home-before.txt`、`dsh-home-after.txt`、`dsh-home-after-cleanup.txt`。
- 给 F-D2 的教训:任何 `dsh --profile <x>` 调用(包括 `--dump-*`)都会向 `<DSH_HOME>/profiles/` 写入,必须始终经 `launch_dsh.py` 包装带私有 `DSH_HOME` 执行;包装会在 spawn 前拒绝日常路径(故障检查见下节)。

## 结论与给 F-D2 的输入

- 六问的免费部分已完成,没有任何一项达到"ACP 不可行"的门槛,因此未动用 `--profile sdk` 退路核对(按计划仅在 ACP 项失败时只读核对同一安装的 SDK profile)。**本记录带有一处日常目录边界违反(账目第 4 条,已清理,mtime 残留),不能表述为完全未改日常数据**;六问中属于实时输入与问询送达的结论以 F-D2 为准,本核对只确立协议边界。
- **后续启动契约(F-D2 起)**:任何 dsh 启动(含 `--help`/`--dump-*`)必须经 `tmp/adr025-dsh/launch_dsh.py`——它要求 DSH home 是探测私有根下**已存在的非链接目录**(缺失、symlink、越界、日常路径都在 spawn 前拒绝),子进程环境为最小白名单(PATH + DSH_HOME),不透传上层 Worker/agent/runtime 变量;日志只记 argv、私有 home 与环境键名,不含环境值。`acp_probe.py` 已改为经同一包装启动(仅约束后续调用;既有三次握手以继承环境加 `DSH_HOME` 启动的历史事实保留在各摘要与帧日志中)。故障检查用一次私有 fake executable 的 marker 证明:四种非法 home 全部在 spawn 前被拒绝且命令未执行,合法 home 对照组正常执行(证据 `logs/launch-wrapper-fault-check.json`);包装至今未启动过真实 DSH,其真实启动路径将在 F-D2 首次使用时观察。
- 凭据边界(给 F-D2):沿用用户 native 登录,只提交公开配置的路径引用(`DSH_HOME`、`--patch` 文件),不复制、不读取密钥;`launch_dsh.py` 不读取凭据文件内容。
- F-D2 可直接复用:启动 argv 与 `DSH_HOME`/`--patch` 双层隔离(建议私有 `DSH_HOME`,或同时 patch 会话与 storage 两个 root);`session/set_config_option` 线格式(`sessionId` + `configId` + `value`,响应携带整组状态且无通知,须读响应而非等通知);model/reasoning_effort 的已声明值集合;会话记录与投影检查点的落盘布局;`session/request_permission` 的应答线格式;EOF 与 SIGTERM 两条停止路径均验证可用。
- F-D2 必须补的真实运行证据:权限请求/拒绝的实际行为、usage_update 实发与口径、所选模型的实际生效(checked/observed 分列)、完成工具调用的原生证据链与签名回执、问询能否经 MCP 工具在合作检查点送达及其时机、cancel 中断事实;一次运行覆盖不了就按计划记部分/未知。
- 未验证事项如实保留:额度与登录未构成本次障碍(控制面免密钥可用);Linux/Windows 未测,本记录仅为 macOS 本机事实;stdio 传输,无 Unix socket 路径限制问题;日常 `--version`/`--help`(快照前)的写入行为 unknown。

## 第 2 回合修正(2026-10-03,Host 拒绝固定交付后 continue)

1. **运行次数账目**:初稿"原生进程只启动两次"不实——`run1-frames.jsonl` 实际含两次握手(同值试运行 + 修正重跑,摘要 `run1-summary.samevalue.json` 曾被留在原处),加 run2 共 **3 次 ACP 握手**;新增"原生启动与运行次数账目"一节,逐条列出全部 9 次 dsh 启动器调用(含 `--version`/`--help`/`--dump-*` 诊断)、1 次 node 诊断、1 次未遂启动,并分别统计原生启动与模型调用(0 次);快照前的日常调用写入行为明确记 unknown。
2. **日常目录边界**:开头与结论不再宣称"不改日常数据";明确账目第 4 条为边界违反、恢复范围(删除其创建的 `~/.dsh/profiles/acp/`)与残留(`~/.dsh/profiles` 目录 mtime);本次未再触碰日常目录,也未尝试恢复 mtime。
3. **投影检查点 ≠ 问询检查点**:第 6 问不再把 `session_projcache` 持久化检查点当作问询的合作检查点,删除"检查点是唯一形状"的表述;公开方法面没有已确认的实时输入通道只作为协议边界,经 MCP 工具的合作检查点问询仍待 F-D2 验证。
4. **HTTP MCP**:initialize 公布的 `mcpCapabilities.http=true` 改标 declared/未测,不再写成实测可用。
5. **启动包装**:新增 `tmp/adr025-dsh/launch_dsh.py`(私有根内非链接目录校验、最小环境白名单、spawn 前拒绝、日志不含环境值),`acp_probe.py` 的启动改为经它(只约束后续调用;既有握手以继承环境加 `DSH_HOME` 启动,历史证据原样保留);以一次私有 fake executable 的 marker 故障检查证明非法 home 不执行命令(合法 home 对照组执行),证据 `logs/launch-wrapper-fault-check.json`;本回合未启动原生 DSH、未重跑握手,既有证据原样保留。

## 第 3 回合修正(2026-10-03,Host 复核后 continue)

1. **握手启动环境的历史事实**:版本/argv 表、材料清单与环境边界不再把既有三次握手写成经 `launch_dsh.py` 最小环境启动——包装在第 2 回合才新增,尚未用于任何真实握手(目前仅以假可执行文件核对过拒绝路径);三次握手的历史实际是 `acp_probe.py` 以继承环境加 `DSH_HOME` 启动,摘要与帧日志原样保留。
2. **环境边界表述与九次账一致**:开头不再说"其余原生调用均带私有 `DSH_HOME`",改为第 1–4 次未隔离(日常 home,其中第 4 次造成已记录的边界违反)、第 5–9 次带私有 `DSH_HOME`、第 1–2 次在基线快照之前写入影响 unknown。本回合零 DSH 进程、零模型调用、未触碰日常目录,仅修改本记录文字。
