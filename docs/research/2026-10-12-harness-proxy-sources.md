# 调研：四个 harness 各自从哪里读代理，以及现在子进程的环境里有没有代理变量

日期：2026-10-12。为 [ADR-030 执行计划](../design/adr030-execution-plan.md)的批次 5"代理的设置"而查（"动手之前先量两件事"）；现状是[待处理问题](../design/backlog.md)里"代理靠继承，没有设置"一条。只读调查，没有改任何源码。

## 问题

两件：其一，现有源码里代理变量怎样从服务进程到达每一个 harness 子进程，哪些变量被保留，四个 harness 的启动各有没有另外的处理；其二，四个 harness（Codex、Claude Code、DSH、ZCode）各自从哪里读代理。由此列出对那个设置的约束，不做设计。

## 看了什么

- 自己的源码：提交 `8bdb7358`（版本 0.30.0）的状态。读了启动器、守护进程与 Worker 监督者的启动、执行时的环境装配、harness 层共用的环境过滤与四个运行模块，直到每一处环境构造与子进程生成点；只读未运行。文件与函数在"看到的事实"里逐处给出。
- 各 harness 的官方文档（2026-10-12 当天读）：Claude Code 的[企业代理](https://code.claude.com/docs/en/corporate-proxy)一页；Codex 现官方文档站的[配置参考](https://learn.chatgpt.com/codex/config-file/config-reference)、[环境变量](https://learn.chatgpt.com/codex/config-file/environment-variables)、[高级配置](https://learn.chatgpt.com/codex/config-file/config-advanced)与[接入网关](https://learn.chatgpt.com/codex/enterprise/connect-to-a-gateway)四页；智谱官方文档的 [ZCode 页](https://docs.bigmodel.cn/cn/coding-plan/tool/zcode.md)、[编码套餐 FAQ](https://docs.bigmodel.cn/cn/coding-plan/faq)、[快速开始](https://docs.bigmodel.cn/cn/coding-plan/quick-start.md)与[全站索引](https://docs.bigmodel.cn/llms.txt)。
- 本机安装的软件，静态查看（没有启动任何 harness，没有读用户侧的配置与凭据文件）：DSH 0.1.5-rc.1 随包发布的包参考——它自带的代理包（0.1.5-rc.2）的说明，全文读完；它链接的用户指南页不在安装包里，没有读。ZCode 应用包内的 CLI 脚本（文件日期 2026-09-30），只做字符串的静态查看。
- 没有读的：Codex 与 ZCode 的源码；DSH 的其余文档；没有实测任何 harness 的代理行为，也没有读取任何一台机器上各 harness 的用户配置。

## 看到的事实

### 代理变量在现有源码里的流向（我们查自己的源码）

- 服务进程的环境不是启动它的那个会话的原样：客户端冷启动守护进程时，环境只由 `install/launcher.py` 的 `service_environment` 按 `SERVICE_ENVIRONMENT_KEYS` 白名单构成；守护进程拉起的 Worker 监督者（`blackboard/service/daemon.py` 的 `start`）、显式命令（`cli/main.py` 的 `_worker_command_unlocked`）与升级协调器代启的服务（`install/upgrade.py` 的 `_environment`）同用这一函数。代理一类保留了 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 与对应四个小写名，另有五个 CA 证书路径变量；其余是进程基本信息、locale、临时与系统路径、XDG、原生账户配置目录、本程序私有根与启动设置；开发夹具变量只在 `BUDDY_DEV_SOURCE=1` 下进入。所以现在有没有代理变量、值是什么，取决于当时在什么环境里启动了服务。
- Worker 监督者是服务进程，并在自己的进程里运行 Worker（`buddy/runtime/supervisor.py`），所以 Worker 看到的 `os.environ` 就是上面那张白名单。执行时 Worker 以它为底，加上状态目录与 harness 记录文件路径，构成执行上下文的环境（`buddy/runtime/worker.py` 的 `_execute_selected`；认领里的 `harnessEnvironment` 只携带记录文件路径）。
- 四个运行模块的准备阶段都取 `dict(os.environ)`，经共用的 `native_environment`（`buddy/harnesses/discovery.py`）过滤后生成子进程：这张第二张白名单同样保留八个代理名与五个 CA 证书名，并把所用 harness 可执行文件所在目录放到 `PATH` 最前。Codex（`buddy/harnesses/codex/native_run.py` 的 `_prepare`，生成于 `_spawn_app_server`）、Claude Code（`buddy/harnesses/claude/native_run.py` 的 `_prepare` 与 `_spawn_native`）、ZCode（`buddy/harnesses/zcode/native_run.py`，生成 `app-server`）与 DSH（`buddy/harnesses/dsh/acp/launch.py` 的 `child_environment`）都在它返回的环境上生成进程；`codex/config.py` 与 `claude/config.py` 的同名函数是共用函数加开发夹具变量，ZCode 在共用函数之上加自己的存储、会话库与日志路径，DSH 只加权限控制变量——整条链上没有任何一处增删或改写代理变量。
- 模型发现的探测与 Codex 账户探针用同一个共用函数（`discovery.py` 的两处探测调用点、`codex/account_probe.py`）；受信的 Python 控制器先过同一个过滤、再加指名变量（`buddy/harnesses/runtime_selection.py` 的 `controller_environment`）。服务进程自己的出站 HTTP 用的也是第一段那张白名单环境。
- 由此（推断，上列各处可逐一核对）：代理变量原样传给每一个 harness 子进程与模型发现探测；有没有、走哪个，全由启动服务的那个会话当时的环境决定；没有按 harness 的任何代理处理，控制台也没有窗口看到它。这正是待处理问题里记的现状。

### 各 harness 的代理读取面

- Claude Code（对方官方文档的自述）。认 `HTTPS_PROXY`、`HTTP_PROXY`、`NO_PROXY`，小写名也生效，且按 `https_proxy`、`HTTPS_PROXY`、`http_proxy`、`HTTP_PROXY` 的固定次序取第一个；文档未提到 `ALL_PROXY`。默认遵守这些变量，没有专用的代理配置项；各变量可放进设置文件的 `env` 段。`NO_PROXY` 接受空格或逗号分隔的列表与 `*`；本机回环地址从不走代理。支持把用户名与密码写进代理地址；不支持 SOCKS。桌面应用会话里只从受管设置与用户级设置文件读这些变量，仓库级设置被忽略。
- DSH（对方随包文档的自述，对应安装版 0.1.5-rc.1）。启动器在第一个插件载入前按 profile 解析一次代理策略，使用 Node 内建 `fetch` 的出站流量（LLM、网页搜索、HTTP MCP）默认都走代理，无需另配；没有专用的代理配置项。认 `http_proxy`、`https_proxy`、`no_proxy`、`all_proxy`：小写优先、大写兜底，空白视为未设；`ALL_PROXY` 对两种协议都生效，HTTPS 最后退回 HTTP 代理。值先取导出的环境变量、再取它数据根下 `.env`，随项目带来的 `.env` 不许携带这些名字，带了就拒绝启动。本机回环一律直连（localhost、整段 127.0.0.0/8、::1、0.0.0.0 及 IPv4 映射写法）。`NO_PROXY` 的条目匹配主机与其子域，接受前导点与 `*.`，可带端口，`*` 表示全部直连；CIDR 段不匹配。不支持 SOCKS、PAC 与操作系统代理：用不了的值记为诊断并让该协议直连。不自配 CA 证书，TLS 拦截代理要在启动前设 `NODE_EXTRA_CA_CERTS`。文档明说的例外：OTLP 遥测始终直连；执行模型所写代码的两类 worker 拿不到代理；自建传输的 SDK 不经全局策略。用户导出的名字（两种大小写都算）按原样传给子进程，未提名的协议以解析值补齐，并为子进程开启 Node 的 `NODE_USE_ENV_PROXY`（22.21+/24+；按它的说明 Node 该开关不读 `ALL_PROXY`）；值是它拒收的那种时不开该开关，让子进程直连而不是起不来。
- Codex（对方官方文档的自述）。配置参考与环境变量两页都没有记载 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY`，也没有 Codex 自身出站流量的代理设置——没有查到；它的 HTTP 客户端默认遵守什么，文档没有依据。文档写到的代理相关配置只有沙盒网络代理（实验性，`features.network_proxy` 一组键）：为沙盒内命令开本机 HTTP 与 SOCKS5 监听，其中 `allow_upstream_proxy` 默认 true，即默认经"环境里的上游代理"链出，但上游代理是哪个变量，文档没有说；各权限档另有自己的 `network.proxy_url` 与 `socks_url`。证书方面记载了 `CODEX_CA_CERTIFICATE` 与 `SSL_CERT_FILE`。
- ZCode（静态查看，无官方文档）。智谱官方文档读过的几页没有代理相关内容——没有查到。对安装的 CLI 脚本做字符串静态查看（未启动、未实测行为）：脚本里有 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 及对应小写名，有一个次序与 Claude Code 文档一致（小写在先）的代理解析清单和读 `NO_PROXY`、判直连、规范化代理地址之名的函数；也有它自己的 `ZCODE_HTTP_PROXY`、`ZCODE_NO_PROXY` 变量名与 SOCKS 客户端代码。这些覆盖哪些流量、服务所启动的子命令是否同样处理，文档给不出依据，本调研没有确认。

### 由事实得出的约束（不设计）

- 一个 HTTP(S) 代理地址够主场景用：Claude Code 与 DSH 都自述默认遵守标准变量，同一地址经环境变量对两家都到得了；DSH 还接受 `ALL_PROXY` 兼管两种协议，Claude Code 文档未提它，四个标准名对两家已够。
- Codex 与 ZCode 没有"写了环境变量就走代理"的文档依据：前者官方文档不记自身流量的代理变量，后者没有查到任何官方文档；目前能给这两家的只有它们各自的配置面（Codex 的 config.toml 键）与静态查看的结果。是否生效、覆盖什么，只能由实测记成事实。
- `NO_PROXY` 要能随地址一起设：有文档的两家都遵守它且语法各有出入（Claude Code 逗号/空格/`*`；DSH 子域/端口/`*` 且不认 CIDR）；两家文档都说回环直连，Codex 与 ZCode 没有这样的文档，回环条目是唯一有文档依据的共同保护（待处理问题已定本机回环一律直连）。
- 地址可能带用户名与密码，有文档的两家都支持；DSH 文档还说明了为什么不给执行模型所写代码的 worker 代理。保存时应按私有数据处理（待处理问题已有此条）。
- 现有管道大小写都保留，"给哪个 harness 写哪种大小写"不受管道阻碍；每个运行模块本就各自构造子进程环境，按 harness 分别开关（待处理问题第 2 条）有现成的落点。
- 用户自己的配置与服务写的变量并存：只有 DSH 的文档给出次序（导出变量在先、`.env` 在后）；Claude Code 的设置 `env` 段是用户自己的配置，它与继承环境谁优先，文档没有说明；产品不改写这些文件（ADR-030）。

## 取了什么，没取什么

- 取：管道现状（八个代理名、两种大小写、每一处构造点）作为批次 5 写环境的落点；四家读取面的现状作为设置设计的依据，其中两家有文档、两家没有，照实记；上面六条约束。批次 5 的验收（"在有代理的环境里跑一次真实的回合"）对四家都要覆盖，特别是没有文档依据的两家。
- 没取：不据静态字符串推断 Codex 与 ZCode 的行为，只记名字与未确认状态；不替任何一家实测代理行为（那是批次 5 验收的事）；没有把任何一家的实现细节当作我们要复用的机制——做法仍是待处理问题里定的"只设标准环境变量，各 harness 自己识别"。

## 时效

- 源码事实对应提交 `8bdb7358`；任何环境构造或白名单的改动使第一节过时。
- 官方文档是 2026-10-12 当天的状态：Codex 文档站已迁移（旧地址 308 重定向到 learn.chatgpt.com，页面还会再动）；Claude Code 代理页、智谱文档页与 DSH 的随包文档都可能随版本变化，harness 升级后相应行要重新核对。
- ZCode 的静态查看对应本机安装的应用包（文件日期 2026-09-30），DSH 对应 0.1.5-rc.1 及其自带代理包 0.1.5-rc.2；升级后要看新版本。
- 批次 5 实现设置之后，本文记录的"代理靠继承"现状即过时，由那一批的验收记录接替。
