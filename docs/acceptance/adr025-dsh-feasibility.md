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

# ADR-025 F-D2:DSH ACP 已授权真实运行核对

本节是 [执行计划](../design/adr025-execution-plan.md) 第 6 节 F-D2 微任务的验收材料:在基线 `108a540` 的隔离 worktree 中,用真实模型运行回答六问需要实际运行的部分。真实运行与同目的必要重跑按用户 2026-10-03 的明确授权执行,未做计划外付费运行。F-D1 一节的全部文字(含边界违反与九次账)是历史事实,原样保留于上方;本节不重复、不改写。

## 方法、材料与边界

- 全部材料在本 worktree 忽略的 `<worktree>/tmp/adr025-dsh-real/` 下:`launch_dsh.py`(启动包装,按 F-D1 记录载明的契约重新实现:home 必须是探测私有根内**已存在的真实非 symlink 目录**,缺失/symlink/越界/日常路径都在 spawn 前拒绝,子进程环境只含 `PATH`+`DSH_HOME`,日志只记 argv、私有 home 与环境键名)、`acp_probe.py`(ACP stdio 客户端:持有 Popen、每请求超时、原始帧日志、权限应答策略、EOF 排空与组停止证据)、`mcp_stub.py`(零依赖 stdio MCP:deliver_outcome 完成工具按本次 nonce+identity 校验,check_inbox/answer_inquiry 承载控制器队列问询)、`oneshot.py`、`snapshot_home.py`(日常目录元数据快照)、`read_session_record.py`(zstd 解压本次私有会话记录)、`prompt-main.txt`(最小任务提示词;`prompt-second.txt` 备用未用)、`patch-main.json`(私有 patch)、`fixture/`(哨兵与 cwd)、`httpdocs/`(本机 HTTP fixture)、`homes/main`(私有 `DSH_HOME`)、`logs/`(帧/摘要/快照/证据)。
- Host 指定的 F-D1 只读输入目录(`~/.local/share/hey-my-buddy/...` 下 ws-84127... 工作区的 `checkout/tmp/adr025-dsh/`)现已不存在;Host 已读黑板 cleanup 记录,确认 F-D1 与 F-C1 的原 worktree 由 applied cleanup 移除(F-D1 在 06:29:34 UTC),归因已由 Host 提供,本记录不再推断。一次性 `tmp/` 材料无法复用,本微任务按 F-D1 **已提交记录**中载明的契约(argv、包装验证规则、`session/set_config_option` 线格式、`session/request_permission` 应答格式、私有 home 布局、声明值集合)重新实现探针,未读取任何凭据或日常会话。本微任务全部原始材料保留至 Host 自行备份,Host 验收前不做任何清理。
- 边界证明:探测前后对日常 `~/.dsh` 全树做 path+type+size+mtime 只读快照并 diff,**29,717 项,零新增、零删除、零变化**(`logs/dsh-home-before.json`/`dsh-home-after.json`);日常 `settings.yaml` 与 `.credentials.yaml` 仅 `is_file`/`stat`,内容未打开、未复制、未打印;真实认证只经 DSH 自己读取——patch 用与旧 `dsh_runner.py` 相同的公开插件行 `{"id": "settings"/"credentials", "config": {"path": <日常路径>, "watch": false}}` 引用,`--dump-config` 已核实两行生效;登录未改。
- 成本控制:私有 patch 禁用 `session-title-llm` 与 `session-telemetry-otel`,`--dump-config` 核实两行均 `disabled: true`;实际会话记录中 `session/title` 的 `source.kind = "fallback"`(本地截断,非模型调用),遥测 exporter 未启用。版本与启动器与 F-D1 一致:`dsh --version` 0.1.5-rc.1(私有 home 下经包装执行),initialize 回报 agentInfo `deepseek-harness-acp` 0.0.1、protocolVersion 1、sessionCapabilities close/list/resume、authMethods 空。
- 会话/存储/临时 profile 全在私有 `homes/main`(会话记录、投影检查点、具体化的 acp profile、`.anonymous-user-id` 均在其下);`read_session_record.py` 用系统 zstd CLI 只解压**本次运行产生的**私有会话记录(见"六问"),未读取用户历史会话。第 2 回合补核新增材料(同样只在 `<worktree>/tmp/adr025-dsh-real/`):`patch-readonly-probe.json`(工具行/沙箱行的组合面核实 patch)、`homes/sdk`(sdk profile 的私有 home)、`logs/readonly-dump-stdout.txt`、`logs/sdk-help-stdout.txt`、`logs/sdk-dump-config-stdout.txt`、`logs/dsh-home-after-turn2.json`(第 2 回合后的日常目录快照,与首轮 after 快照 diff 仍为零)。第 3 回合新增:`fake_agent.py` 与 `dry_run_check.py`(合成 transport 干跑及断言)、`patch-none-scope.json`/`patch-none-scope-r2.json`/`patch-read-scope.json`、`prompt-none.txt`/`prompt-read.txt`、`runA-workspace/`、`runB-workspace/`、`runB-sibling/`(均在探测根内;runB 两目录为 workspace 与 workspace 外 sibling)、`homes/none`/`homes/read`/`homes/dryrun`(私有 home)、`logs/dryrun*` 与 `logs/fake-agent-*`(干跑证据)、`logs/none-scope*`/`logs/read-scope*`/`logs/none-dump*`/`logs/ro-env-dump*`(各次真实运行的帧/摘要/stop 摘要)、`logs/launch-wrapper-fault-check-turn3.json`(修改后包装的重新自检)、`logs/dsh-home-before-turn3.json`/`dsh-home-after-turn3.json`(前后快照,diff 为零)。第 4 回合新增:`patch-none-scope-r3.json`(加 plan-mode 禁用)、`prompt-none.txt` 复用、`logs/none-dump-r3*`、`logs/none-r3-bootcheck-summary.json`(免模型 boot check)、`logs/none-scope-r3-summary.json` 与 `logs/stub-none-scope-r3.log`(真实 prompt 与交付回执)、`logs/dryrun-noprompt-*`(新标志干跑)、`logs/dsh-home-after-turn4.json`(与本回合前快照 diff 为零)。

## 原生启动与真实 prompt 账目

| # | 调用 | 结果与原因 |
| --- | --- | --- |
| 1 | 包装故障检查(假可执行文件,非 dsh) | 4 种非法 home 全部 spawn 前拒绝且命令未执行,合法对照执行;`logs/launch-wrapper-fault-check.json` |
| 2 | `dsh --version`(私有 home,经包装) | exit 0,组消失 |
| 3 | `dsh --profile acp --patch … --dump-config`(私有 home,经包装) | exit 0,组消失;核实 patch 四行与持久化根 |
| 4 | ACP 运行尝试 1 | 探针缺陷(spawn 未用 PIPE stdin,进程继承关闭的 stdin 即退出);**零协议流量、零模型调用** |
| 5 | ACP 运行尝试 2 | 探针缺陷(帧日志以文本模式写 bytes,收发双方日志崩);仅 initialize;**零模型调用** |
| 6 | ACP 运行尝试 3 | 探针缺陷(响应提取返回 waiter 而非消息 + model 值用了数组而非声明 JSON 字符串);initialize+session/new 成功,两次 `set_config_option` 与 `session/prompt` 均 -32602 被协议层拒绝;**零模型调用**(`logs/main-attempt3-defect-frames.jsonl`) |
| 7 | ACP 运行尝试 4 | 真实 prompt 已入 turn(写文件与 cat 哨兵完成、`session/steer` 实测 -32601);探针 steer 提取缺陷使回合中崩溃、以 stdin EOF 收尾;**消耗 1 次真实模型调用**(私有会话记录 `05db67e2`:inputTokens 9778、outputTokens 159,模型 deepseek-flash/effort low);**该次停止证据有缺口**:其探针摘要与帧在重跑前未另存而被覆盖(我的留证缺陷),留存的原生会话记录不含退出码/进程组事实,不能充当该次停止证据;Host 曾在覆盖前读到该摘要为 exit 0/组消失,但该原始日志已不在交付中,按缺口如实登记,不补写 |
| 8 | ACP 运行尝试 5(最终完成) | 全流程完成:**1 次真实模型 prompt**,stopReason `end_turn`,耗时 11.8 秒;完整停止证据在留(`logs/main-summary.json`) |
| 9 | `dsh --profile acp --patch …readonly-probe --dump-config`(第 2 回合补核) | exit 0,组消失;核实工具行/沙箱行的 patch 形状被组合面接受 |
| 10 | `dsh --profile sdk --help`(第 2 回合补核,私有 home `homes/sdk`) | exit 0,组消失;help 仅一行用途说明 |
| 11 | `dsh --profile sdk --dump-config`(第 2 回合补核,私有 home `homes/sdk`) | exit 0,组消失;与 acp 共享同一 dsh-base 控制面 |
| 12 | `dsh --profile acp --patch …none-scope --dump-config`(第 3 回合) | exit 0,组消失;组合面含 15 个 disabled 行(当时含 commands) |
| 13 | ACP 运行尝试 6(none-scope) | **原生启动失败**:`commands` 行被禁用后 3 个依赖插件(command-feedback/goal/compact)pending,plugin tree 加载失败,进程 exit 1;**零模型调用**;证据 `logs/none-scope-summary.json`(含 stderrTail)——**dump 组合面接受 ≠ 可启动**的实测发现 |
| 14 | `dsh --profile acp --patch …none-scope-r2 --dump-config`(第 3 回合) | exit 0,组消失;去掉 commands 行后组合面正常 |
| 15 | ACP 运行尝试 7(none-scope-r2) | **真实 prompt #3**,turn 完成(2 个模型步),工具集实测见第 1 问 (iii);完整停止证据在留(`logs/none-scope-r2-summary.json`) |
| 16 | `dsh --profile acp --patch …read-scope --dump-config`(第 3 回合,env `DSH_PERMISSION_MODE=read-only`) | exit 0,组消失;dump 中 `!!js` 表达式不求值,preset 实际生效由会话记录证实 |
| 17 | ACP 运行尝试 8(read-scope) | 探针缺陷:session/new 未带 `mcpServers` 键被 DSH -32602 拒绝(schema 标注 optional、DSH 实测必填);**零模型调用**;证据 `logs/read-scope-summary.json`/`read-scope-frames.jsonl` |
| 18 | ACP 运行尝试 9(read-scope-r2) | **真实 prompt #4**,turn 完成(7 个模型步),read 预设实测见第 1 问 (ii)/(iii);完整停止证据在留(`logs/read-scope-r2-summary.json`) |
| 19 | `dsh --profile acp --patch …none-scope-r3 --dump-config`(第 4 回合) | exit 0,组消失;组合面含 plan-mode 行 `disabled: true` + 14 个 tool 行 disabled |
| 20 | ACP 运行尝试 10(none-r3-bootcheck,`--no-prompt`) | initialize/session/new/set_config/close 全部成功,**零模型调用**,exit 0 组消失——plan-mode 禁用后的组合**可启动**(`logs/none-r3-bootcheck-summary.json`) |
| 21 | ACP 运行尝试 11(none-scope-r3) | **真实 prompt #5**,turn 完成(2 个模型步),有效工具集实测恰为交付机制一项(见第 1 问 (iii));完整停止证据在留(`logs/none-scope-r3-summary.json`) |

- 合计(至第 4 回合):**dsh 原生启动 20 次**(CLI 9 次 + ACP 11 次),**真实模型 prompt 共 5 次**——尝试 4(被中止于 turn 前段)、尝试 5(完整,10 个模型步)、尝试 7(none-scope-r2,完整,2 个模型步)、尝试 9(read-scope-r2,完整,7 个模型步)、尝试 11(none-scope-r3,完整,2 个模型步);prompt 次数与模型步数分开记(模型步共 21 步)。修正:第 3 回合的免模型 dump 为 **3 次**(none-dump、none-dump-r2、ro-env-dump),前稿重跑理由处"两次"系笔误。留存证据分层:尝试 1/2/3/5/6/7/8/9/10/11 的 ACP 运行各有独立留存摘要(含 pid/pgid/exit/组观察);第 3/4 回合的 4 次 CLI 各有独立 stop 摘要;**第 1/2 回合的 5 次 CLI(version、dump-config、readonly-dump、sdk-help、sdk-dump-config)只有 stdout 文件与 launches.jsonl 旧行留痕(行内 pid 为 None、无退出/组字段),exit 0/组消失属执行时外壳观察,不构成可复核留存证据**;尝试 4 的缺口保留。
- MCP stdio 挂载按层分账(不从 ACP 进程数推定;截至第 4 回合累计):session/new 携带 MCP 声明 **6 次**(尝试 3/4/5/7/10/11;尝试 3/4 因 `env` 形状缺陷声明无效);stub 实际被 DSH 拉起并完成 MCP 握手 **4 次**(尝试 5/7/10/11;证据为各自 stub 日志 `stub-main.log`、`stub-none-scope-r2.log`、`stub-none-r3-bootcheck.log`、`stub-none-scope-r3.log` 的 startup 与 notifications/initialized 行);实际工具发现(模型可见 MCP 工具名,以模型按名调用为准)**3 次**(尝试 5/7/11;尝试 10 为免模型 boot check,无发现观测);实际模型 tools/call **5 次**(尝试 5 三次、尝试 7 一次、尝试 11 一次,均 valid 回执)。
- 重跑理由逐次登记:第 1 回合四次重跑对应四个确定性客户端脚本缺陷;第 3 回合尝试 6 为原生 boot 失败(留证后改 patch 重试)、尝试 8 为客户端 session/new 形状缺陷(留证后修复重试);第 4 回合尝试 11 前先做免模型 boot check(尝试 10)。模型相关的额度或登录失败未发生。第 2 回合补核为免模型核对;第 3 回合的三次免模型 dump 与第 4 回合的一次免模型 dump 各有独立 stop 摘要。

## 六问逐项(actual / declared / unknown)

### 1 哪些工具发权限请求,能否拒写/命令/联网(第 2 回合拆三项,第 3 回合实测闭合)

- **(i) native 已有 workspace-write 行为(observed)**:最终一次运行与尝试 4 中,`session/request_permission` 出现 **0 次**(尝试 4 以留存的私有会话记录 `05db67e2` 为证:仅 `permission/preset`/`sandbox/mode`/`approval/policy` 三行策略状态,`tool/call` 与 `tool/result` 各 2,无任何权限请求事件)——write(被"先读后改"工具策略挡过一次后重写成功)、read、bash(`cat sentinel.txt`)、web_fetch(见下)及三个 MCP 工具全部在无客户端许可往返下由原生侧自行执行;最终会话记录同样载明 `permission/preset: workspace-write`、`sandbox/mode: workspace-write`、`approval/policy: "ask"`。结果关联:文件 `fixture/out.txt` 实际写入且内容精确匹配,哨兵 `sentinel.txt` 字节未变,本机 HTTP server 收到 **1 次** GET `/probe-hit.txt`(经 bash `curl`);原生 web_fetch 工具自身以 `URL hostname "127.0.0.1" resolves to a non-public IP address` 拒绝环回地址。该观察与随装公开文档一致:dsh-base README 明文默认策略"confines file writes to your workspace and asks before risky actions. Web fetch runs without per-call approval; its provider rejects non-public destinations"——即工作区内动作不询问是**声明内的默认行为**,不是异常。
- **(ii) client 回调 deny 能力(第 3 回合已实测:请求出现、reject 被遵守)**:第 1 回合范围内没有动作触发 `session/request_permission`(当时记 unknown);第 3 回合 read 预设运行中,两次真实 `session/request_permission` 到达客户端——read-only 沙箱原生拒绝写入后,模型以 `sandbox_permissions: workspace-write` 参数尝试升级,对 cwd 内与 workspace 外(sibling)两个写入各触发一次,选项为 allow_once/reject_once,客户端按既定策略答 `reject_once`,原生记录同时载明 `approval/asked: 2` 与 `approval/decided: 2`(与两次客户端回答对应),两次写入均以 `failed` 结束,`out.txt` 与 sibling `out.txt` 均未产生(`logs/read-scope-r2-summary.json`、私有会话记录 d54f7b16)——**deny 真实阻止得到可复核证据**。仍 unknown 的部分:"risky actions" 的完整触发面(公网联网、更多工具类别)未枚举;read-only 沙箱对首次写入的自动拒绝(无请求直接 `failed`)说明部分拒绝不经过客户端,控制器的应答回调只覆盖会问询的子集。允许路径(accept)未测:本微任务不需要,不为它花模型调用。
- **(iii) none/read 范围能否原生限制(第 3/4 回合已实测:none 完全闭合,read 已实测)**:ACP 通道内没有会话级的客户端工具范围选择——`session/new` 响应 `modes: null`(ACP 标准的 SessionModeState/availableModes 会话模式机制未被 DSH 使用),会话 configOptions 仅 `model`/`reasoning_effort` 两项。生效的是**启动面**公共机制:**none**——公开 patch 禁用 14 个 tool 行**再加 `plan-mode` 行禁用**(第 4 回合:先免模型 dump 核实组合面接受、再以免模型 `--no-prompt` ACP spawn 核实可启动,然后以 Host 授权的 1 个真实 prompt 实测),私有会话记录 `request/header` 显示模型有效工具集**恰为本次交付机制一项** `['mcp__buddy-fd2-probe__deliver_outcome']`——`exit_plan_mode` 已不在集合中,任务工具为零;模型对写入/命令的尝试无 tool_call 事件、无文件产生、哨兵未变、deliver_outcome 有效回执(`logs/none-r3-bootcheck-summary.json`、`logs/none-scope-r3-summary.json`、私有记录 d6bee7d6)。此前第 3 回合只禁工具行时 `exit_plan_mode` 残留(记录 67ddbab1),第 4 回合以同一公开面闭合。**read**——`DSH_PERMISSION_MODE=read-only`(经包装 `--env` 显式注入的唯一新增子进程键,启动行 envKeys 有记录,不继承日常/Worker 环境)下,会话记录回读 `permission/preset: read-only`、`sandbox/mode: read-only`、`approval/policy: ask`,工具清单完整保留(25 项);cwd 内写入被只读沙箱原生拒绝(首次尝试无请求直接 `failed`),workspace 外(sibling)写入同样被拒,`cat sentinel.txt` 读哨兵正对照成功输出 `probe-ro-sentinel-ok`。两个必须记录的边界事实:**`commands` 行不能单独禁用**(尝试 6 原生 boot 失败:command-feedback/goal/compact 三个依赖插件 pending,进程 exit 1)——dump 组合面接受不等于可启动;`session/new` 的 `mcpServers` 键实测必填(ACP schema 标 optional,DSH -32602,尝试 8 留证)。问询工具本次未挂载(可选会话服务,本回合不需要),交付工具未改名、未伪装成模式控制工具。
- **对既有行为的影响(第 4 回合实测后的口径)**:三种范围现在都有实测的原生满足方式——write 即第 1 回合观察的 workspace-write 默认;none 即启动面 patch(14 个 tool 行 + plan-mode 行禁用,有效工具集经原生记录核实恰为本次交付机制);read 即 `DSH_PERMISSION_MODE=read-only` 公开预设(回读+沙箱拒绝+升级问询可拒)。**若第四步采用这些公开启动配置,工具范围本身被维持,属实现载体从 `tools=[]` 到公开配置的选择,不因此构成行为放宽**;是否采纳仍由 Host 在第四步验收中审定,本报告不自行写入实施。残留的实现注意事项:none 集合下 DSH 仍按 workspace-write 预设记录策略行(策略与实际工具集解耦,投影时以有效工具集为准);问询为可选会话服务,按需挂载,交付工具不改名;审阅载体维持现状不可用,read 机制核对不开放 DSH 审阅资格。
- **`--profile sdk` 退路核对(第 11 条,第 2 回合免费完成)**:`dsh --profile sdk --help`(私有 home)仅一行用途说明;`--profile sdk --dump-config` 与 acp **共享同一 dsh-base 控制面**(同样的 sandbox-policy/approval/permission 预设与 tool 行,无新增控制项);随装 dsh-sdk-protocol README 载明方法面仅 `initialize`/`session/prompt`/`shutdown` 三请求加四个单向通知,并明文 "**Server→client requests are a dead capability**——server 从不发送,Python SDK 的应答面留作未来 approval flows"、 "**No cancel or session-close methods**"。因此 **SDK 无法补齐 (ii)**(连权限回调都不存在,弱于 ACP 的实测回调),对 (iii) 也只提供与 acp 相同的启动面配置,无新增能力;其相对 ACP 的增量仅是 init 时钉 provider/model/effort/maxTokens。结论:**退路不成立,无需为此改用 SDK;ACP 是两者中较强的权限面**。(iii) 的效果缺口已在第 3/4 回合以最小真实运行闭合,仍不需要"最少进程内插件"。

### 2 用量能取得多少

- actual:ACP 流内 `session/update usage_update` 共 **10 条**,`{used: 10773→11761, size: 1000000}` 单调递增,每步一条,属**会话上下文窗口的累计快照**(非增量);未观察到 `cost` 字段。本次私有会话记录(642dbda6,64 行)另有**每模型步**的 provider 用量字段:`inputTokens`、`outputTokens`、`totalTokens`、`cacheReadTokens`、`reasoningTokens`(最终步 6452/176/10212/3584/116;观察上 `totalTokens` 覆盖含 cache read 的整次请求上下文,跨步不累加,逐字段语义未做厂商证明);每条 assistant message 的 `source` 均带 provider/model。被中止的尝试 4 turn:inputTokens 9778、outputTokens 159、cacheRead 0、reasoning 50。
- declared:schema 的 `usage_update {used, size, cost?}`;cost 可选,本次未出现。
- unknown:本次运行**未观察到**任何 quota 字段(ACP update 流与私有记录均无;F-D1 已录协议面未声明 quota——但"协议层不可能出现"超出核对所能证明的范围,只记未观察到);cost 未观察到;`used` 的确切口径(含 system+历史)未做厂商证明,按上下文快照记录。

### 3 会话存储能否指向私有目录

- actual(真实模型运行下复核):**截至第 1 回合**的 3 个会话(含 2 个早期缺陷尝试的空/中断会话与最终会话)记录只落在私有 `homes/main/sessions/<展平 cwd>/<sessionId>/session.v3.jsonl.zstd`(+`session.lock`),投影检查点在 `homes/main/storages/session_projcache/sessions/<sessionId>.json`;第 3/4 回合的会话同样全部落在各自私有 home(`homes/none`:尝试 7/10/11 共 3 个记录、`homes/read`:尝试 9 共 1 个记录;尝试 6 在会话创建前因 boot 失败退出、尝试 8 的 session/new 被 -32602 拒绝,均无会话记录),日常 home 快照 diff 为零变化贯穿各回合(见"方法、材料与边界")。zstd 记录可用公开 CLI 解压核对(各回合靠它取得 Q2/Q4 与工具集的 observed 事实)。
- declared/结论:与 F-D1 一致——私有 `DSH_HOME` 一层即同时迁移会话与检查点,无需复制用户目录;任何写入日常存储即失败的判据,本次以零 diff 满足。

### 4 模型与推理强度选择、回报是否可靠

- requested:model 选择器声明值字符串 `["deepseek-official","deepseek-flash"]`(选项列表中的精确 JSON 字符串,非数组)、`reasoning_effort: low`;两者均为已声明项,无需取近似值。
- checked:`session/set_config_option` 响应整组状态显示 model currentValue 由默认 `["deepseek-official","deepseek-v4-flash"]` 真实切换为请求值、thought_level 由默认 `high` 切换为 `low`(依据:原生会话配置回读;与 F-D1"acp 桥默认路由与 base 默认是不同条目"的告诫一致)。
- observed:本次私有会话记录的 session 头 config(`provider deepseek-official, model deepseek-flash, reasoningEffort low, maxTokens 256000`)、每 turn 的 `request/header` 与每条 assistant message 的 `message.source` 三处一致指向 deepseek-flash/low。**注意:本次 ACP update 流内未观察到任何 model/effort 字段**(探测客户端对全部通知做了字段扫描,0 命中),因此"实际生效"的 in-band 回报本次缺失,运行模块要么读私有会话记录、要么该值记 null,不能把会话配置回读冒充 turn 回报。

### 5 Python MCP 完成工具是否可行

- actual:全链成立且证据齐备——`session/new`(stdio 条目 `env: []` 时)拉起 stub(stub 日志 startup,MCP initialize、notifications/initialized、tools/list);模型根在**同一个 turn 内**真实调用 `mcp__buddy-fd2-probe__deliver_outcome`,ACP 事件 ID `toolCallId call_00_KvAZNL0M0NfyNxGtYH6R2631`,顺序为 `tool_call(in_progress)`→`tool_call_update(completed)`,结果文本 "outcome received and validated";stub 侧收到的参数 nonce=`fd2-521e…`、identity=`34426c63-…` 与本次运行配置精确匹配,校验行 `nonceMatch/identityMatch/valid` 全 true(`logs/stub-main.log`);完成证据是这条原生调用链+时间戳回执,**不是**末条文本(末条文本同时存在但仅作旁证)。注意点(给第四步):ACP 会把 MCP 工具名呈现为 `mcp__<server>__<tool>`,tool_call 的 `kind` 一律报 `other`,ACP toolCallId 与私有记录中的 provider `call_00_…` ID 相同可直接关联;stdio MCP 条目的 `env` 必须是 `[{name,value}]` **数组**(传对象时 DSH 不报错但不挂载该 server——尝试 3 的直接教训);工具清单不产生 `available_commands_update` 通知(与 F-D1 一致)。
- declared/unknown:权限拒绝未阻止 MCP——原因不是"拒绝被绕过",而是范围内根本未出现任何权限请求(见第 1 问 (ii)),备用的"第二小会话仅放行 MCP 工具"因此未启用(`prompt-second.txt` 未用);若第四步实施中 MCP 工具被权限请求拦住,该备用路径仍待验证。HTTP 形态 MCP 仍未实测(declared only)。挂载次数按层分账见"原生启动与真实 prompt 账目"。

### 6 问询只能检查点还是实时送达

- actual:**本次未发现已确认的实时输入通道**——turn 进行中客户端发 `session/steer` 实测 -32601 "Method not found"(0.001 秒返回);这与 F-D1 枚举的公开方法面一致(客户端→agent 仅 initialize/authenticate/session/new/list/resume/close/set_config_option/prompt/cancel 与 `$/cancel_request`),但结论收窄为"在已枚举的公开方法面与本次实测内未发现",**不是对所有可能形态(如 `_meta` 私有扩展、其他通知承载)的绝对否定**。**合作检查点问询成立**:控制器在 turn 进行中(07:08:00.388)写入 inbox 文件产生 inquiryId `inq-fd2-main-01`;模型在原 turn 第 8 步调 `check_inbox`(08.571,`firstDelivery: true`,即排队后约 8.2 秒、由模型自己的工具检查点决定时机)取得问题,随即以 `answer_inquiry` 回答,inquiryId 匹配(expected/actual 一致),回答文本入 stub 回执;**原任务在同一 turn 内继续并完成**(私有记录 turns=[1]、turn/start 与 turn/end 各一次,10 步),未为问询新开 prompt 或新 turn;探测控制器的全局超时预算全程未变(原 deadline 未重置,代码上预算常量不可变,时间线上 turn 在原预算内结束)。这是控制器队列+标准 MCP 的合作机制,不是原生问询通道;投影检查点(`session_projcache`)仍只是会话内容检查点,与问询无关。
- declared/unknown:F-D1 已枚举的公开方法面没有已确认的实时输入通道(协议边界事实,本次 `session/steer` 实测亦 -32601);cancel 在真实运行中的中断事实仍未测(不在六问授权实验内,记 unknown);问询延迟上界=模型到下一个工具检查点的间隔,本次观察约 8 秒。

## 停止与组停止证据

- 最终运行:stdin EOF 停机路径——`session/close` 无错误后关闭 stdin,stdout 排空至 EOF,读取线程结束,退出码 **0**,stderr 空,`killpg(pgid, 0)` 报 ProcessLookupError(进程组消失);MCP stub 同步收到 stdin EOF 退出(其日志 `stdin-eof`);spawn(07:07:58)至组消失(07:08:10 后)全程约 12–14 秒。
- **可复核停止证据的留存范围(第 3 回合修正口径,区分执行时观察与留存证据)**:尝试 1、2、3 各有留存摘要(`logs/main-attempt{1,2,3}-defect-summary.json`)记录 exitCode 与组状态;尝试 5 即最终运行,证据在留(`logs/main-summary.json`、`logs/main-frames.jsonl`);尝试 6/7/8/9(第 3 回合)各有独立留存的 `logs/none-scope-summary.json`、`none-scope-r2-summary.json`、`read-scope-summary.json`、`read-scope-r2-summary.json`(含真实 pid/pgid、exitCode、EOF 排空与 killpg 组观察)。**CLI 各次(版本/dump)的 exit 0 与组消失是执行时的外壳观察,当时的留存物不构成可复核证据**——`launches.jsonl` 旧行只记 spawn 前 argv/envKeys/home(pid 为 None),stdout 文件也不含退出/组状态;第 3 回合起 oneshot 为每次 CLI 写独立 stop 摘要(`logs/none-dump-stop.json`、`none-dump-r2-stop.json`、`ro-env-dump-stop.json`,含真实 pid/pgid/exitCode/EOF/组状态),旧行不回填。**尝试 4 仍是缺口**:其探针摘要与帧在重跑前未另存而被覆盖,留存的原生会话记录 `05db67e2` 不含退出码/进程组字段;Host 曾在覆盖前读到该摘要为 exit 0/组消失,但该原始日志已不在交付中——此缺口如实保留,不补写。
- 已核对的故障证据:包装 4 非法 home 拒绝 + 合法对照(假可执行文件;第 1 回合 `logs/launch-wrapper-fault-check.json`,包装修改后第 3 回合以 `launch-wrapper-fault-check-turn3.json` 重新自检通过);尝试 1–3 的协议层负例(-32602 无效参数、-32601 未知方法)原生侧行为正确;尝试 6 的原生 boot 失败(stderr 与 exit 1 留证)与尝试 8 的 -32602 留证;干跑的超时升级路径(SIGTERM 后组消失,`logs/dryrun-timeout-summary.json`)。保守口径(不可观察按仍活着处理)仍是运行模块的实现责任,不在本次核对范围。

## 行为差异逐项(三项已获准差异的核对状态 + 送 Host/用户决定的影响清单)

已获准三项的核对状态:

1. **问询可能只能检查点送达——成立并已证实**:已枚举公开方法面与实测内未发现实时输入通道,合作检查点经 Python MCP 可用,时机由模型工具步决定(本次约 8 秒)。
2. **快速路由多出 DSH 系统提示——已观察到**:真实 agent 会话的 system message 为 DSH 自己的提示词("You are an AI agent powered by DeepSeek Harness…",含工具使用指引),并声明 "coding agent powered by the deepseek-flash model";属已批准差异,本次仅记录,不扩大。
3. **原生续接可能成为新能力——维持 F-D1 结论**:initialize 公布 `resume`、`session/resume` 在 F-D1 已通;真实 turn 后的续接本次未测(六问未要求),记"协议与控制面已核,真实续接未测"。

**实测后的范围与差异状态(第 4 回合)**:

- **工具范围**:三种范围均有实测的原生满足方式——write(workspace-write 默认,第 1 回合)、none(启动面 patch 禁 14 个 tool 行 + plan-mode 行,有效工具集经原生记录核实恰为本次交付机制,第 3/4 回合)、read(`DSH_PERMISSION_MODE=read-only` 预设,回读+沙箱拒绝+升级问询可拒,第 3 回合)。据此,**工具范围由公开启动配置维持属于实现选择,不构成行为放宽,也无需"最少进程内插件"或 SDK**;此前 none 集合残留的 `exit_plan_mode` 已由同一公开面(plan-mode 行禁用)移除并实测闭合,不再是差异候选。第四步若采纳,以第 1 问 (iii) 的实测边界为准(`commands` 不可禁用、`mcpServers` 必填、策略行与工具集解耦等)。工具范围不再是送决定项;是否采纳由 Host 在第四步验收审定。
- **权限回调**:deny 路径已实测(两次请求、两次 reject、原生 `approval/asked`/`decided` 对应、写入均被阻止);允许路径与完整触发面未测,控制器实现以"reject 优先、allow 仅显式列举"为既定策略即可,不构成本微任务的送决定项。
- **已获准三项**(检查点问询/DSH 系统提示/原生续接)维持原状:前两项已观察,第三项维持"协议与控制面已核,真实续接未测"。
- **实现输入级事实**(记录供建议,不单独定性为行为差异):stdio MCP 的 env 必须为数组形状;ACP 将 MCP 工具名呈现为 `mcp__<server>__<tool>`,tool kind 恒为 `other`;update 流内无 model 身份字段;usage_update 仅上下文口径;web_fetch 拒绝非公网地址;写工具先读后改;plan-mode 等内部模式未接入 ACP modes;`session/request_permission` 应答必须用嵌套 `RequestPermissionOutcome` 形状;`session/new` 的 `mcpServers` 实测必填;`commands` 行禁用会令 boot 失败。

## 结论与给第四步的输入(第 4 回合口径)

- 六问状态:Q1 三项——(i) workspace-write 行为 observed 且与随装声明一致;(ii) client deny 已实测(请求/reject/原生 decided 对应/写入被阻止),允许路径与完整触发面未测;(iii) none 与 read 均由公开启动配置实测满足,none 的有效工具集经第 4 回合实测恰为本次交付机制一项(`exit_plan_mode` 已由 plan-mode 行禁用移除并实测)。Q2–Q6 维持此前 actual 证据(quota 与实时输入两处按收窄口径)。**三种工具范围都能由原生开关维持,第四步不需要"报 unrestricted"的降级,也不需要进程内插件或 SDK**;这是实现载体的选择,工具范围契约本身不变。
- 第四步的实现输入(全部有实测依据):隔离用私有 `DSH_HOME`;none 用 14 个 tool 行 + plan-mode 行的 disabled patch(不能禁 `commands`;有效集合以私有会话记录 `request/header` 为准);read 用 `DSH_PERMISSION_MODE=read-only` 子进程键(经包装显式注入);write 即默认预设+控制器的 reject 策略应答(嵌套 `RequestPermissionOutcome` 形状);`session/new` 必须带 `mcpServers`(可为空数组)且 stdio MCP 条目 `env: []`;模型配置用 `session/set_config_option` 声明值字符串;完成/问询工具经 Python MCP(按需挂载,交付工具不改名);observed 模型身份读私有会话记录;用量=ACP 上下文快照+记录内 per-step provider 字段;问询=合作检查点;停机 EOF/SIGTERM 双路径均实测。
- SDK 退路(第 11 条)结论不变:不成立,也不需要。
- 未验证事项如实保留:allow 路径与"risky actions"完整触发面、真实 turn 后 native resume、cancel 中断事实、HTTP MCP、cost/quota(本次未观察到)、Linux/Windows、read 预设下的环回 HTTP(实测一次因本报告模板缺陷 URL 不完整,未取得干净事实,不再花 prompt 补测);尝试 4 停止证据缺口保留。本节结论限于 macOS 本机、`dsh` 0.1.5-rc.1/acp 0.1.5-rc.2;F-D2 的核对不等于第四步实现已验证(4-D 另有整步验收)。全部原始材料保留至 Host 自行备份,验收前不清理;Host 已将第 3 回合必要材料备份至其证据 worktree(不含凭据目录),本工作区材料同时保留。

## 第 4 回合修正(2026-10-03,Host 复核后 continue,授权至多 1 个新真实 prompt)

1. **结论自洽性修正**:第 3 回合稿同时写"范围已维持、不再送决定"与"exit_plan_mode 需角色层定性",不自洽;本回合以公开面闭合该残留——`plan-mode` 行 `disabled: true` 先经组合面 dump 核实,再以免模型 boot check(尝试 10:`--no-prompt` 完成 initialize/session/new/set_config/close,exit 0 组消失,零模型调用)证实可启动,最后以授权的 1 个真实 prompt 实测(尝试 11):有效工具集恰为 `['mcp__buddy-fd2-probe__deliver_outcome']`,none 完全满足;模式控制工具未改名、未伪装成完成工具,计划第 4 节的完成机制单列边界未被触碰。
2. **MCP 服务收窄**:stub 新增 onlyTools 声明过滤,本回合只声明 deliver_outcome;问询工具(inbox/answer)按可选会话服务未挂载。
3. **计数修正**:第 3 回合免模型 dump 为 3 次而非"两次";总账更新为 dsh 原生启动 20 次(CLI 9+ACP 11)、真实 prompt 5 次、模型步 21 步;本回合新增 CLI 1 次(带 stop 摘要)、ACP 2 次(no-prompt boot check + 1 个真实 prompt,均有独立摘要)。
4. **过程与边界**:脚本先干跑(`--no-prompt` 与 onlyTools 均经合成 transport 验证,期间修复两处本回合作用域缺陷,干跑标签为本回合自测夹具);全程唯一 label、新日志不覆盖旧日志;未读厂商实现、未发明私有字段、未写 JS/TS、未做进程内插件;read/network/allow/平台等未验证项原样保留,不因本回合扩大;原生控制工具按可观察事实登记,是否计为任务工具/如何投影由角色与黑板负责,本报告不作豁免定性。

## 第 5 回合修正(2026-10-03,Host 复核后 continue,仅修记录)

1. **MCP 分层计数更新到第 4 回合**:账目原停留在第 3 回合值(声明 4/握手 2/发现 2/调用 4),现按现有帧与 stub 日志列明截至第 4 回合累计——声明 **6 次**(尝试 3/4/5/7/10/11)、实际握手 **4 次**(尝试 5/7/10/11,证据为四个 stub 日志的 startup 与 notifications/initialized 行)、实际工具发现 **3 次**(尝试 5/7/11;尝试 10 为免模型 boot check 无发现观测)、实际模型 tools/call **5 次**(尝试 5 三次、7 一次、11 一次,均 valid 回执);不从启动次数推断。
2. **Q3 会话范围修正**:"全部 3 个会话"改标"截至第 1 回合",补记第 3/4 回合会话分布(homes/none 3 个:尝试 7/10/11;homes/read 1 个:尝试 9;尝试 6 会话创建前 boot 失败、尝试 8 session/new 被 -32602 拒绝,均无会话记录),全部在私有 home、日常目录零 diff 贯穿各回合。
3. **SDK 小节回合归属**:"(iii) 效果缺口已闭合"由"第 3 回合"更正为"第 3/4 回合"(none 的完全闭合含第 4 回合的 plan-mode 禁用与实测)。
4. **本回合边界**:零原生 CLI、零模型调用,仅核对现有日志与摘要后修改本记录;Host 已独立核对尝试 11 会话 d6bee7d6 的原生 request/header(仅含 deliver_outcome)、两次新 ACP stop 摘要与进程组消失、日常 metadata 相等,并将第 4 回合材料备份至其证据 worktree `tmp/adr025-evidence-retained/fd2-turn4`(不含凭据目录);本工作区全部原始材料继续保留至最终备份。

## 第 3 回合修正(2026-10-03,Host 复核后 continue,授权两次最小真实运行)

1. **留证表述修正**:`launches.jsonl` 旧行只记 spawn 前 argv/envKeys/home(pid 为 None)、stdout 文件不含退出/组状态——CLI 各次的 exit 0/组消失改称"执行时观察",留存证据从第 3 回合起由 oneshot 的独立 stop 摘要承担;尝试 4 帧摘要缺口在"两次 prompt 原始材料留存"表述中显式排除;F-D1/F-C1 worktree 的移除归因采用 Host 提供的黑板 cleanup 记录(06:29:34 UTC applied cleanup),不再推断。
2. **脚本先经合成 transport 干跑**(`fake_agent.py` + `dry_run_check.py`,零 DSH 零模型):全协议形状、权限应答(拒绝/取消)、fs 请求拒绝、MCP 挂载握手、超时升级(SIGTERM 后组消失)全部通过;干跑捕获并修复一处真实缺陷(权限应答误用扁平形状+多余键,实机必被拒),避免了模型调用的浪费。
3. **none 实测(尝试 6/7,1 个新 prompt)**:`commands` 行禁用致原生 boot 失败(3 依赖插件 pending,exit 1,留证)→ 去 `commands` 重试成功;私有会话记录 `request/header` 实测模型工具集恰为 MCP 三工具+`exit_plan_mode`,任务工具全部缺席,写入/命令尝试无 tool_call 事件、无文件产生,MCP deliver_outcome 收到有效回执。
4. **read 实测(尝试 8/9,1 个新 prompt)**:尝试 8 留证 `mcpServers` 必填缺陷(零模型调用)后修复;`DSH_PERMISSION_MODE=read-only`(包装显式注入的唯一新增键)下原生回读 read-only 预设,首次写入被沙箱直接拒绝,两次升级尝试触发真实权限请求并被客户端 reject_once 拒绝(原生 `approval/asked: 2`/`decided: 2` 对应),cwd 内与 workspace 外写入均未产生文件,读哨兵正对照成功;环回 HTTP 步骤因本报告模板缺陷({PORT} 占位符被花括号包裹)未取得干净事实,如实记未验证,不再补测。
5. **范围结论**:三种工具范围均可由公开原生开关维持(write=默认预设、none=工具行 patch、read=权限预设),属实现选择而非行为放宽,无需 unrestricted 降级、进程内插件或 SDK;prompt 总账更新为 4 次(含第 1 回合 2 次),dsh 原生启动总账 17 次(CLI 8+ACP 9),每次新 spawn 均有真实 pid/pgid 与独立 stop 摘要。

## 第 2 回合修正(2026-10-03,Host 复核固定交付后 continue)

1. **总体结论收窄**:删除"六问全部取得 actual 证据、无 ACP 失败项、无需 SDK 退路、第四步可实现性成立"式的总结;第 1 问拆为 (i) native workspace-write 行为(observed)/(ii) client deny 未测(unknown)/(iii) none/read 无 ACP 会话级选择、启动面机制存在但未验证三项,工具范围影响列为送 Host/用户决定的清单,不再由本报告豁免。
2. **SDK 退路补核(免模型)**:新增 `--profile sdk --help` 与 `--dump-config`(均私有 home)+ 随装 dsh-sdk-app/dsh-sdk-protocol/dsh-base README 的公开面核对:SDK 方法面仅 initialize/session/prompt/shutdown + 4 通知,权限回调明文为 dead capability、无 cancel/close,控制面与 acp 同源,退路不成立。
3. **启动面机制补核(免模型)**:一次 `--patch` 后的 `--dump-config` 核实 14 个 tool 行 + `commands` 的 `disabled: true` 与 `sandbox-policy.mode: read-only` 被组合面接受;同时记录 session configOptions 仅 model/effort、modes null、plan-mode 自述工具目录跨模式不变等公开面事实。本回合新增 3 次 dsh CLI(账目第 9–11 行),零模型调用,日常 `~/.dsh` 快照 diff 仍为零。
4. **证据表述精确化**:MCP 挂载按"声明 3 次/实际握手 1 次/实际工具发现 1 次/实际模型 tools/call 3 次"分账;真实 prompt 总数 2 与最终 turn 的 10 个模型步分开;尝试 4 的停止证据登记为缺口(原始摘要被覆盖,留存会话记录不含退出码/进程组事实,Host 覆盖前读到的 exit 0/组消失不在交付内,不补写);F-D1 只读目录仅记"现不存在、无法复用、按记录重新实现",不作清理归因;quota 收窄为"本次未观察到";实时输入收窄为"已枚举面与实测内未发现"。既有两次 prompt 的原始材料与本回合私有会话记录全部留存,未覆盖任何旧摘要/帧。
