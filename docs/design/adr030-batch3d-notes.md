# ADR-030 批次 3d：DSH 启动现状与待 Host 决定的接口

2026-10-12，基点 `71f0a3f6`，0.30.0、schema 17；这是微任务的调查与部分实现记录，不是 Host 验收。接续回合开始时 `git status --short` 为空，本文不存在；文件里的两个用量边界测试已经存在，本回合增强它们并实际做变异验证，没有把上一回合的文字当作已验证产出。

## 停在什么地方

原任务第 2 项要求“DSH 没有只管会话存放处的开关就停下来交给 Host”。本机 DSH 0.1.5-rc.1 的启动器帮助没有会话目录参数，ACP 帮助只有 `--help`；官方完整帮助也没有这种参数。目前可查到的接口是通用 `--patch` 加 `session-persistence-jsonl.config.root`，它是原生支持的会话存储配置，但不是只管会话目录的 CLI 开关。因此没有自行把两者视为同一种授权，没有改启动路径、删私有主目录或设计替代存储。

请 Host 决定是否把这个已有的原生 `root` 配置经 `--patch` 传入视为 ADR-030 第 3 条允许的接口；若允许，后续只保留会话根与暂行权限补丁，删除私有 `DSH_HOME`、设置/凭据路径覆盖与自行写的 ACP profile，由 DSH 自己加载用户主目录及其原生 ACP profile。另请明确两个始终禁用的插件行是否删除：它们不是权限或会话位置，旧验收曾明确接受，ADR-030 又要求用户配置原样使用，本回合归为拿不准而未删除。

如果 Host 不允许用通用补丁设置会话根，另一选择是使用用户主目录中的默认会话库，按 ADR-030 第 3 条没有开关就不管；这会改变存储归属，也不满足本微任务要求会话仍在私有存放处，必须由 Host 重新定范围，不能在本回合擅自落成。

## 查了什么与原生依据

读了 ADR-030、执行计划的贯穿规则与批次 3、ADR-023、AGENTS.md、CONTEXT.md、架构与 ADR-007，以及 DSH 旧运行清单、拒绝清单和原生续接 Host 验收；前两批“打回”提醒的原生名字、路径中的点号、模拟接口的局限、环境隔离和不另造恢复机制都纳入下面的检查边界。旧运行清单记录的是已退休的 Node/direct-LLM 载体，下面以当前 `dsh/native_run.py`、`acp/`、注册表与 `workflow.py` 为准。

仅使用 DSH 的帮助和官方文档，没有读取或移植该项目的源码，也没有读用户的配置或凭据。以下链接指向公开官方文档；master 文档可能比本机版本新，不能拿新帮助里的额外功能当作本机已支持。

- [官方 CLI 完整帮助](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/docs/cli-help.md)：启动器列出 `--profile`、`--patch` 和 dump，ACP 没有会话目录参数。
- [官方 CLI 行为参考](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/apps/cli/reference/README.md)的 Profile boot、Shared deployment behavior：profile 在 `$DSH_HOME/profiles` 下；profile、home 与 argv 的补丁依次叠加；原生会初始化缺失的内置 ACP profile，账户文件由 harness 自己解析。
- [官方 JSONL 会话存储说明](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/packages/session/session-persistence-jsonl/README.md)的 Use this package：`root` 指定会话存储根，`compression` 另有默认值；会话在根下按 cwd 与会话标识分目录，创建可延迟到写入或 flush。
- [官方 base bundle 说明](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/packages/bundle/base/README.md)的 Use this package：原生 ACP 包含 base，默认具备模型、工具、持久会话与工作区写入权限；改变默认工具与权限通过 profile/后续原生补丁表达，不能据此声称另一版本的固定工具清单也已验证。

本回合实际执行本机 CLI 的 `--version`、`--help`、`--profile acp --help`，三条退出码都为 0，版本为 `0.1.5-rc.1`；帮助没有会话目录参数。探查的 cwd 和临时 `DSH_HOME` 均在一次性目录内（含点号、空格与非 ASCII 字符），仅为避免探查接触用户配置；`HOME` 继承，没有 prompt、登录或模型调用。没有执行 dump 或 ACP 握手，也没有把帮助当作配置读回。

## 当前每条启动路径

`C` 表示选定的原生命令数组；`I` 表示本次 invocation 根；`N` 表示传入的 native 根；`T` 表示微任务共享 native 根。所有路径经 `acp.launch.launch` 持有进程组，日志仅列环境键名而不列环境值。

| 路径 | 原生命令与 cwd | 当前主目录、会话与证据位置 | 协议调用 |
| --- | --- | --- | --- |
| CLI 资格发现 | `C --version`，cwd 为临时 probe 根 | probe 根内 `dsh-home/`；`logs/launches.jsonl`；确认停止才清临时根，未知则保留 | 无 ACP，无 prompt |
| 模型目录发现 | `C --profile acp --patch I/dsh-launch-patch.json`，请求 cwd | `N/dsh-home` 与其中 sessions；`N/logs/launches.jsonl`、`N/logs/frames.jsonl`；补丁在 I | initialize → new（空 MCP 数组）→ 读 configOptions → close → 停止；不设模型，不发送 prompt |
| Worker 新回合 | 同一 ACP 命令，请求检出 cwd | 私有 `I/dsh-home`；会话在 `T/sessions`；补丁、bridge 在 I；启动和帧日志在 `I/logs` | initialize → new（本回合 MCP 挂载）→ set_config_option ×2 并读回 → prompt → close → 停止 |
| Worker 原生续接 | 同一 ACP 命令，同一检出 cwd | 与 Worker 新回合相同，会话根和 goal binding 跨尝试共用，其余按本回合私有 | initialize → resume（原会话、本回合挂载）→ 冻结记录 → 重新设并读回两项配置 → prompt → close → 停止；没有 new 回退 |
| 无工具调用及格式纠正 | 同一 ACP 命令，私有请求 cwd | `N/dsh-home/sessions`；补丁在 I；启动与帧日志在 `N/logs` | 每轮 new（空 MCP 数组）→ 两项配置读回 → prompt → close；纠正创建另一根会话，不续接 |
| 审阅调用 | 注册表不声明 DSH 的只读审阅能力，未接入 | 没有另一套审阅启动器 | `DshAdapter.read_only_structured=False`；通用 run 的 read 范围不能算作可用的审阅入口 |

无 ACP 启动以外的隐蔽参数：`_launch_agent` 始终只传选定命令、`--profile acp` 与一个 `--patch` 路径；模型、供应方和推理强度经会话配置接口表达，任务文字经 prompt 表达，不通过环境变量覆盖用户设置。`session/new`/`resume` 的 `cwd` 与 `mcpServers` 是原生协议参数，本回合的签名服务承载结果约定，不是另建供应方配置。

## 环境：实际继承与注入

当前环境白名单在 `discovery.native_environment`；本回合唯一改动是 Host 明确授权增加 `DSH_HOME`，其值是用户自己的主目录设置，原样保留而不解析、规范化或赋新值。依据是 CLI 的 `$DSH_HOME/profiles` 以及官方账户/主目录说明；它不是凭据值。白名单没有设置文件、供应方密钥或 `DSH_PERMISSION_MODE`。

```text
HOME USER LOGNAME USERNAME USERPROFILE HOMEDRIVE HOMEPATH APPDATA LOCALAPPDATA
XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME XDG_STATE_HOME XDG_RUNTIME_DIR
CODEX_HOME CLAUDE_CONFIG_DIR ZCODE_DATA_BASE_DIR DSH_HOME
LANG LANGUAGE LC_ALL LC_CTYPE LC_MESSAGES LC_COLLATE LC_NUMERIC LC_TIME
LC_MONETARY LC_PAPER LC_NAME LC_ADDRESS LC_TELEPHONE LC_MEASUREMENT LC_IDENTIFICATION
TMPDIR TMP TEMP SYSTEMROOT WINDIR COMSPEC PATHEXT TERM COLORTERM PATH
HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy
SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE CURL_CA_BUNDLE NODE_EXTRA_CA_CERTS
```

| 项目 | 四类中的归类 | 现状与处理 |
| --- | --- | --- |
| 继承身份、locale、临时目录、PATH、代理与 CA 路径 | 会话与证据放在哪里（运行基础） | 沿用公共白名单，PATH 继续按既有规则加命令目录；没有额外 DSH shell 初始化 |
| 用户给的 `DSH_HOME` | 替用户另建或改写的配置（当前覆盖它的行为） | 白名单已保留用户值，但 `launch.child_environment` 目前仍赋私有值；这次仅完成前者，删除后者等 Host 的接口决定 |
| `HOME` | 替用户另建或改写的配置（显式私有 HOME 入口） | 生产 `_launch_agent` 和版本 probe 都不传 `home=`，所以实际继承；ACP 包装仍接受显式私有 home，测试会使用，后续应删除该替代入口和它的旧测试 |
| `DSH_PERMISSION_MODE=read-only` | 表达权限 | `tool_scope=read` 时明确注入，沿用暂行办法；write 不注入值，不设计新档位；旧真实只读依据见 `adr025-feasibility-host-review.md` |
| 固定十五个工具行与 `plan-mode` 禁用 | 表达权限 | none 范围经原生补丁表达，按原任务留批次 4；不扩展清单 |
| MCP stdio 描述中的 `PYTHONPATH` | 会话与证据放在哪里（结果载体） | 仅传给本次挂载的签名服务 Python 进程，不是 DSH 环境恢复，也不是用户配置覆盖 |

除 read 范围的权限变量外，生产 `_launch_agent` 没有附加 DSH 环境变量；黑板凭据不会经 `native_environment` 传给 DSH。官方 CLI 文档声明 DeepSeek 使用 `DEEPSEEK_API_KEY` 与搜索地址变量；本回合在检查运行器和 `tests/python/support.py` 的既有过滤中增加 `DSH_`、`DEEPSEEK_` 两个前缀，测试子进程不继承开发者的这些变量，支持夹具只重新设置自己的合成 `DSH_HOME`。这项测试隔离不是产品环境白名单的扩张。

## 补丁与写入文件逐项归类

当前 ACP 启动补丁顺序严格为下表前五行：存在才添加的 settings/credentials，session 根，scope 行，两个 always-off 行；真实 DSH 的 `--patch` 是公开覆盖接口，后层替换目标行 config，而不是改写磁盘上的用户文件。

| 项目 | 四类中的归类 | 依据与下一步 |
| --- | --- | --- |
| `settings` 的 `path=用户主目录/settings.yaml, watch=false` | 替用户另建或改写的配置 | `source_binding_rows` 按文件布局探存在并覆盖原生行；官方 profile/home 叠加本来由 DSH 做，后续删除，不能选哪些文件进来 |
| `credentials` 的 `path=用户主目录/.credentials.yaml, watch=false` | 替用户另建或改写的配置 | 同上；本项目只探文件存在，不读内容，但仍依赖布局，后续删除 |
| `session-persistence-jsonl.config.root` | 拿不准 | 官方存储文档明确 root 只管会话位置，CLI 却只暴露通用补丁；等待 Host 是否认可这就是本任务所说的原生开关 |
| scope 的固定禁用行/只读环境值 | 表达权限 | 原生 CLI 的补丁接口与既有权限验收；沿用，留批次 4 改过严检查 |
| `session-title-llm`、`session-telemetry-otel` 的 `disabled=true` | 拿不准 | 旧验收 `adr025-step4-review-ready.md` 明确接受成本/隐私差异；它们不是会话位置或权限，可能被 ADR-030 原样设置原则取代，未擅自删 |
| 私有 `dsh-home/` | 替用户另建或改写的配置 | 包装强制环境覆盖并检查私有 home；后续删除这一配置主目录与相应校验，保留证据路径防护 |
| `profiles/acp/package.json`、`cordis.yml`、`cordis.patch.yml`、`pnpm-workspace.yaml` | 替用户另建或改写的配置 | `materialize_acp_profile` 自写 base/acp-app bundles、空 patch、包管理设置；官方 CLI 自带 ACP 初始化，后续整段删除，不能复制用户配置代替 |
| `dsh-launch-patch.json` | 会话与证据放在哪里（控制材料） | 文件本身仍按回合私有，允许保留的内容须随 Host 的上面决定收敛；当前没有改内容 |
| `sessions/**/session.v3.jsonl[.zstd]`、原生 `session.lock` | 会话与证据放在哪里 | DSH 自己写持久记录/锁，官方 JSONL 文档说明 root 与目录布局；微任务共享会话根沿用目标不变 |
| 会话 ID 摘要命名的 goal binding JSON | 会话与证据放在哪里 | 本项目的 task/session/cwd/configuration 绑定，仅完整回执且确认停止后保存，不是 DSH 配置；沿用 |
| `logs/launches.jsonl`、`logs/frames.jsonl` | 会话与证据放在哪里 | 本项目启动与 ACP 帧元数据证据，路径经过私有根防护，不是 DSH 官方参数或配置；沿用 |
| `finish-bridge.json`、attention 与 inquiry journal 材料 | 会话与证据放在哪里 | 本项目本回合签名、问询和完成服务的私有控制材料，不是 DSH 用户设置；沿用 |
| `dsh-session-record.json`、`native-provenance.json`、`inquiry-report.json`、`attention-report.json`、`attention-record.json`、`denied-interactions.json`、`permission-decisions.json`、`acp-connection-facts.json`、`native-stderr.log` | 会话与证据放在哪里 | `_evidence_refs` 按实际有无生成的本项目事实投影/日志；不声称这些名字是 DSH 官方接口，沿用 |

本回合没有删除任何现存文件或旧文档段落，因为启动实现仍被上面的决定挡住；不会先把正在工作的路径改成一种未获决定的替代品。历史验收中的私有 home 事实仍是历史事实，当前参考页等真正取代实现时再删相应段落。

## 供应方、名字与续接

核对 `native_run._catalog`、`_declared_model_value`、`_configure`、`check_preparation`、`adapter.py`、`registry.py`、ACP client 的 initialize/session 操作：没有按供应方、账户类型或登录方式的允许清单，没有第一方限定，没有 authenticate/登录流程；选定命令就绪与原生声明选项可用性的检查不是供应方封锁。`authMethods` 和账户状态没有被当成允许执行的门槛；目录的 `accountStatus=not-applicable` 是没有单独读取账户的事实。

四段名字现在来自 `dsh` 加 ACP `configOptions.model` 每个选项 value 内原生 JSON 二元组的 provider/model，以及 `reasoning_effort` 的原生 value；group/display name 不改成供应方 ID，也不制造 unknown 供应方。官方 CLI 的 Shared deployment behavior 使用 `deepseek-official` 这个原生名字；本项目并不替 DSH 选择默认供应方。后续若取消隔离，仍从同一 ACP 二元组和原生 effort value 取名，对未改设置的用户不应变化；这一结论还需要 Host 的真实无模型目录对比，不能用模拟程序保证。

`workflow.py` 继续以 DSH 的 `storageOwner=buddy-goal` 判断共享会话存储，没有改动。如果 Host 同意继续通过原生 root 配置固定 `T/sessions`，会话记录位置、绑定形式与归属均不变，已有微任务共享会话可以沿用一个原生读法；按尝试存放的旧会话仍按既有 `private-native-home-required` 重建。如果 Host 选择用户会话库，旧私有会话停止原生续接、由服务按新会话重建，不能加第二种读法；存储事实与服务资格如何一并调整须先重新定范围。

## 后续给 Host 的无模型验证办法

本回合改变的启动相关行为只有公共白名单保留用户 `DSH_HOME`，生产 DSH 包装仍覆盖它；因此不能声称真实 CLI 已读取用户设置、会话位置已迁移或原生续接已在新布局通过。Host 的决定后，每条改过的启动路径都须按以下办法验证，下面是验收步骤而非已经执行的证据。

1. 用生产 `native_environment` 构造环境，核对 `HOME` 与用户给定的 `DSH_HOME` 原样相同；运行真实 `C --version`，应只得到版本且停止确认，没有新建黑板的配置主目录。
2. 在 `<host-root>/测试.v2 空间/` 准备字面 JSON 补丁（如果 Host 允许）：仅 `[{"id":"session-persistence-jsonl","config":{"root":"<host-root>/测试.v2 空间/会话"}}]`，另按所测范围加权限行；不把路径写成点号键。真实 `C --profile acp --patch <补丁> --dump-config` 不调用模型，需由 Host 私下核对 settings/credentials 行与其未加补丁的原生 dump 相同，用户 home/profile 补丁保留，会话 root 等于指定路径；dump 可能含敏感配置，只保留核对摘要。
3. 对目录发现、Worker 初始/续接、none 调用分别用最终启动 argv/env 创建 ACP 连接，initialize → new → 读 configOptions → set_config_option（只针对回合路径）→ 读回 provider/model/effort → close → 停止；不调用 prompt/authenticate。none 使用原来的固定权限补丁，审阅仍不可用，不另建通道。读回选项应是用户原生目录的选项，设置的值应等于被冻结的请求值。
4. 第二个独立进程沿用同一个私有会话根，resume 第一个进程返回的 sessionId，重新读回配置、close 并确认停止；若无 prompt 的 new/close 未落盘，如实记录而不偷偷加模型调用。比较私有 root 下的记录位置与固定 binding 路径；带 prompt 的两回合真实续接仍由 Host 授权后验收。

当前 `tests/probes/dsh_native_resume_preflight.py` 依赖 `source_binding_rows` 和 `materialize_acp_profile`；本回合没改它，因为实现尚未被取代；后续删除那些生产函数时必须在同一改动内更新该探针，不能留下导入已删函数的旧验证路径。

## 实际验证与交回边界

仅使用指定的只读解释器，`PYTHONPATH=src:tests/python` 优先于安装包；每次测试启动先按前缀清 BUDDY、C2、DSH、DEEPSEEK、Claude/Anthropic 变量并清 `VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`，再设置测试自己的一次性 HOME/DSH_HOME 和临时根。不运行完整检查、服务、Worker、模型或真实配置读回。

- 最终聚焦 69 项通过（6.276 秒，退出 0）：`buddy.harnesses.test_harness_discovery` 全文件、`buddy.harnesses.dsh.test_session_records` 全文件，`cli.test_checks_cleanup` 的 `ChecksEnvironmentTests`、`ResidueObservationTests`、`DeletedOpenFileObservationTests`，以及 `cli.test_support_cleanup.FixtureCleanupTests` 的两个 child_environment 测试；原始日志在忽略的 `tmp/adr030-batch3d/final-focused-tests.log`。
- 首轮上述三个主测试文件共 74 项，5 项失败：新白名单测试漏算公共函数总会返回的空 PATH，已修测试；另 4 项旧清理测试因沙盒 `ps process enumeration failed`，没有改生产检查或绕过沙盒，不能把这次全文件运行报告为通过。
- 两处用量测试保持生产行为不变：非 session 第一行现在刻意带相同 version/id，避免另一判断遮住 type 检查；超限冻结使用大于实际 64 MiB 总上限、每行小于 4 MiB 的真实文件，行内容各异，最终读允许看全文件，避免最终读的截断/重复行检查遮住冻结不可靠的判断。
- 在独立测试进程里仅替换 `_matching_lines` 的函数对象：单独去掉 type 判断，以及单独把 `not stream.truncated` 改成 True，各对应测试实际失败一次，都是 `baseline.reliable` 的断言失败，无导入/环境错误；恢复原函数后记录测试全文件 18 项通过（0.449 秒）。日志在 `tmp/adr030-batch3d/mutation-validation.log`，生产源码没有变异。

请 Host 在能观察进程的环境运行 `cli.test_checks_cleanup.PrivateRootTeardownTests` 整类，特别是 `test_teardown_drains_a_cooperative_supervisor`、`test_teardown_removes_a_clean_private_root`、`test_teardown_reports_a_surviving_process_with_unlinked_lock`、`test_teardown_waits_out_a_live_process_with_unlinked_lock` 这四项失败项；它们不校验新增的 DSH 环境过滤，但全文件验收仍需要通过。`cli.test_support_cleanup` 的其余项由 Host 跑，其中 `test_r06_child_uses_private_state_and_explicit_catalog` 会初始化 C-Two，`test_cleanup_stops_cli_started_replacement_after_restart` 与 `test_private_state_context_stops_its_cli_started_service` 会启动服务/Worker，本任务不运行。

本回合改动为 DSH_HOME 白名单及测试、测试环境的 DSH/DeepSeek 前缀过滤及测试、两个已有用量边界测试的增强与本文；没有改 roles、其他 harness、workflow、schema、安装资源或公共模型目录/计费逻辑；最终 `git diff --check` 通过。核心删除及其模拟原生配置读取/私有会话/续接回归还没做，等待 Host 对接口与两个旧禁用行的明确决定，再继续本微任务。
