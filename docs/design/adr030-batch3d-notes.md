# ADR-030 批次 3d：DSH 启动现状与待 Host 决定的接口

2026-10-12，基点 `71f0a3f6`，0.30.0、schema 17；这是微任务的调查与部分实现记录，不是 Host 验收。上一调查回合开始时 `git status --short` 为空，本文不存在；文件里的两个用量边界测试已经存在，本回合增强它们并实际做变异验证，没有把上一回合的文字当作已验证产出。

## Host 决定与最终实现

Host 已确认：`session-persistence-jsonl.config.root` 经 DSH 的公开 `--patch` 传入算它自己的会话存放开关，符合 ADR-030 第 3 条与 ADR-023。启动仍是选定命令加 `--profile acp --patch <本回合补丁>`，不增加另一套运行路径。

本回合删除强制私有 `DSH_HOME`（包括版本探测）、显式私有 `HOME` 的参数入口、settings/credentials 的文件路径覆盖、`source_home`/`source_binding_rows` 与自行写 ACP profile 的全部代码及旧测试；`HOME`、`DSH_HOME` 原样经过白名单，无 DSH_HOME 时保持未设置，由 DSH 自己选默认主目录。额外环境仍不许覆盖两个继承的主目录键，这是调用方参数边界，不限制用户主目录。

`session-telemetry-otel` 的禁用已删除：遥测开不开是用户自己的设置；`session-title-llm` 的禁用保留：私有会话标题没有人看，多发一次模型调用只会浪费用户额度，这是 Host 明确保留的例外。无工具固定禁用行和 read 范围的 `DSH_PERMISSION_MODE=read-only` 不变，留给批次 4。

Worker 会话仍在微任务共享 `T/sessions`，goal binding、用量边界与续接规则不变，`workflow.py` 未改；发现/无工具的原 `N/dsh-home/sessions` 位置也不变，其中 `dsh-home` 只是会话目录的父目录，没有 profile、设置、凭据，不作为环境主目录传给 DSH。这保持了所有记录的位置，不是第二种读法。补丁、签名服务控制材料、启动日志与帧日志继续按本次 invocation 私有。

DSH 原生加载或初始化 ACP profile：首次使用内置 ACP 而用户目录没有该 profile 时，DSH 会按其原生模板在用户的 `$DSH_HOME/profiles/acp/` 下创建 profile 文件，未设置 DSH_HOME 时是在 `~/.dsh/profiles/acp/`；这是 DSH 自己的正常行为，黑板不物化、不复制、不按布局选择文件，Host 需要告知用户。原生初始化在真实环境的结果仍待 Host 验证。

## 查了什么与原生依据

读了 ADR-030、执行计划的贯穿规则与批次 3、ADR-023、AGENTS.md、CONTEXT.md、架构与 ADR-007，以及 DSH 旧运行清单、拒绝清单和原生续接 Host 验收；前两批“打回”提醒的原生名字、路径中的点号、模拟接口的局限、环境隔离和不另造恢复机制都纳入下面的检查边界。旧运行清单记录的是已退休的 Node/direct-LLM 载体，下面以当前 `dsh/native_run.py`、`acp/`、注册表与 `workflow.py` 为准。

仅使用 DSH 的帮助和官方文档，没有读取或移植该项目的源码，也没有读用户的配置或凭据。依据是公开官方文档的名称与小节；master 文档可能比本机版本新，不能拿新帮助里的额外功能当作本机已支持。

- 官方 CLI 完整帮助：启动器列出 `--profile`、`--patch` 和 dump，ACP 没有会话目录参数。
- 官方 CLI 行为参考的 Profile boot、Shared deployment behavior：profile 在 `$DSH_HOME/profiles` 下；profile、home 与 argv 的补丁依次叠加；原生会初始化缺失的内置 ACP profile，账户文件由 harness 自己解析。
- 官方 JSONL 会话存储说明的 Use this package：`root` 指定会话存储根，`compression` 另有默认值；会话在根下按 cwd 与会话标识分目录，创建可延迟到写入或 flush。
- 官方 base bundle 说明的 Use this package：原生 ACP 包含 base，默认具备模型、工具、持久会话与工作区写入权限；改变默认工具与权限通过 profile/后续原生补丁表达，不能据此声称另一版本的固定工具清单也已验证。

本回合实际执行本机 CLI 的 `--version`、`--help`、`--profile acp --help`，三条退出码都为 0，版本为 `0.1.5-rc.1`；帮助没有会话目录参数。探查的 cwd 和临时 `DSH_HOME` 均在一次性目录内（含点号、空格与非 ASCII 字符），仅为避免探查接触用户配置；`HOME` 继承，没有 prompt、登录或模型调用。没有执行 dump 或 ACP 握手，也没有把帮助当作配置读回。

## 改前每条启动路径（调查时的事实）

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

## 环境：白名单与改前注入

当前环境白名单在 `discovery.native_environment`；本回合唯一改动是 Host 明确授权增加 `DSH_HOME`，其值是用户自己的主目录设置，原样保留而不解析、规范化或赋新值。核心删除后 DSH 的包装也不再覆盖它。依据是 CLI 的 `$DSH_HOME/profiles` 以及官方账户/主目录说明；它不是凭据值。白名单没有设置文件、供应方密钥或 `DSH_PERMISSION_MODE`。

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
| 用户给的 `DSH_HOME` | 替用户另建或改写的配置（改前覆盖它的行为） | 白名单与 launch 都继承用户值，强制私有赋值已经删除 |
| `HOME` | 替用户另建或改写的配置（显式私有 HOME 入口） | 生产 `_launch_agent` 和版本 probe 都不传 `home=`，所以实际继承；ACP 包装的显式私有 home 入口和它的旧测试已删除，测试夹具自己设置模拟用户环境 |
| `DSH_PERMISSION_MODE=read-only` | 表达权限 | `tool_scope=read` 时明确注入，沿用暂行办法；write 不注入值，不设计新档位；旧真实只读依据见 `adr025-feasibility-host-review.md` |
| 固定十五个工具行与 `plan-mode` 禁用 | 表达权限 | none 范围经原生补丁表达，按原任务留批次 4；不扩展清单 |
| MCP stdio 描述中的 `PYTHONPATH` | 会话与证据放在哪里（结果载体） | 仅传给本次挂载的签名服务 Python 进程，不是 DSH 环境恢复，也不是用户配置覆盖 |

除 read 范围的权限变量外，生产 `_launch_agent` 没有附加 DSH 环境变量；黑板凭据不会经 `native_environment` 传给 DSH。官方 CLI 文档声明 DeepSeek 使用 `DEEPSEEK_API_KEY` 与搜索地址变量；本回合在检查运行器和 `tests/python/support.py` 的既有过滤中增加 `DSH_`、`DEEPSEEK_` 两个前缀，测试子进程不继承开发者的这些变量，支持夹具只重新设置自己的合成 `DSH_HOME`。这项测试隔离不是产品环境白名单的扩张。

## 改前补丁与写入文件逐项归类

改前 ACP 启动补丁顺序为：存在才添加的 settings/credentials，session 根，scope 行，两个 always-off 行；真实 DSH 的 `--patch` 是公开覆盖接口，后层替换目标行 config，而不是改写磁盘上的用户文件。

| 项目 | 四类中的归类 | 依据与下一步 |
| --- | --- | --- |
| `settings` 的 `path=用户主目录/settings.yaml, watch=false` | 替用户另建或改写的配置 | `source_binding_rows` 按文件布局探存在并覆盖原生行；官方 profile/home 叠加本来由 DSH 做，已经删除，不能选哪些文件进来 |
| `credentials` 的 `path=用户主目录/.credentials.yaml, watch=false` | 替用户另建或改写的配置 | 同上；改前本项目只探文件存在，不读内容，但仍依赖布局，已删除 |
| `session-persistence-jsonl.config.root` | 会话与证据放在哪里 | 官方存储文档明确 root 只管会话位置，Host 已认定通用补丁接口符合任务，沿用 |
| scope 的固定禁用行/只读环境值 | 表达权限 | 原生 CLI 的补丁接口与既有权限验收；沿用，留批次 4 改过严检查 |
| `session-title-llm` 的 `disabled=true` | 拿不准（Host 已定） | 旧验收接受，Host 本次明确保留，以免私有会话标题浪费模型额度 |
| `session-telemetry-otel` 的 `disabled=true` | 替用户另建或改写的配置 | Host 本次明确删除，遥测由用户设置决定 |
| 私有 `dsh-home/` | 替用户另建或改写的配置 | 包装强制环境覆盖并检查私有 home；已删除配置主目录与相应校验，保留证据路径防护 |
| `profiles/acp/package.json`、`cordis.yml`、`cordis.patch.yml`、`pnpm-workspace.yaml` | 替用户另建或改写的配置 | `materialize_acp_profile` 自写 base/acp-app bundles、空 patch、包管理设置；官方 CLI 自带 ACP 初始化，已整段删除，不能复制用户配置代替 |
| `dsh-launch-patch.json` | 会话与证据放在哪里（控制材料） | 文件本身仍按回合私有，内容已收敛为会话根、暂行权限行与保留的标题禁用 |
| `sessions/**/session.v3.jsonl[.zstd]`、原生 `session.lock` | 会话与证据放在哪里 | DSH 自己写持久记录/锁，官方 JSONL 文档说明 root 与目录布局；微任务共享会话根沿用目标不变 |
| 会话 ID 摘要命名的 goal binding JSON | 会话与证据放在哪里 | 本项目的 task/session/cwd/configuration 绑定，仅完整回执且确认停止后保存，不是 DSH 配置；沿用 |
| `logs/launches.jsonl`、`logs/frames.jsonl` | 会话与证据放在哪里 | 本项目启动与 ACP 帧元数据证据，路径经过私有根防护，不是 DSH 官方参数或配置；沿用 |
| `finish-bridge.json`、attention 与 inquiry journal 材料 | 会话与证据放在哪里 | 本项目本回合签名、问询和完成服务的私有控制材料，不是 DSH 用户设置；沿用 |
| `dsh-session-record.json`、`native-provenance.json`、`inquiry-report.json`、`attention-report.json`、`attention-record.json`、`denied-interactions.json`、`permission-decisions.json`、`acp-connection-facts.json`、`native-stderr.log` | 会话与证据放在哪里 | `_evidence_refs` 按实际有无生成的本项目事实投影/日志；不声称这些名字是 DSH 官方接口，沿用 |

旧的配置代码、对应测试和当前参考页描述已在本回合一起删除或替换；历史验收保持其当时的事实。日志私有根、路径防护和进程停止证据没有弱化，用户主目录不作为私有证据根接受。

## 供应方、名字与续接

核对 `native_run._catalog`、`_declared_model_value`、`_configure`、`check_preparation`、`adapter.py`、`registry.py`、ACP client 的 initialize/session 操作：没有按供应方、账户类型或登录方式的允许清单，没有第一方限定，没有 authenticate/登录流程；选定命令就绪与原生声明选项可用性的检查不是供应方封锁。`authMethods` 和账户状态没有被当成允许执行的门槛；目录的 `accountStatus=not-applicable` 是没有单独读取账户的事实。

四段名字现在来自 `dsh` 加 ACP `configOptions.model` 每个选项 value 内原生 JSON 二元组的 provider/model，以及 `reasoning_effort` 的原生 value；group/display name 不改成供应方 ID，也不制造 unknown 供应方。官方 CLI 的 Shared deployment behavior 使用 `deepseek-official` 这个原生名字；本项目并不替 DSH 选择默认供应方。取消隔离后仍从同一 ACP 二元组和原生 effort value 取名，对未改设置的用户不应变化；这一结论还需要 Host 的真实无模型目录对比，不能用模拟程序保证。

`workflow.py` 继续以 DSH 的 `storageOwner=buddy-goal` 判断共享会话存储，没有改动。会话 root、绑定形式与归属均不变，已有微任务共享会话沿用一个原生读法；按尝试存放的更旧会话仍按既有 `private-native-home-required` 重建，没有兼容读取或同回合恢复失败后新建的机制。

## 给 Host 的最终无模型验证办法

以下步骤使用最终启动参数与环境，但未在本回合执行真实握手、dump 或用户配置读回；模拟原生程序验证不等于真实接口验证。审阅仍未接入，不新增验证通道。

1. 用生产 `native_environment` 构造环境，核对 `HOME` 与用户给定的 `DSH_HOME` 原样相同；运行真实 `C --version`，应只得到版本且停止确认，没有新建黑板的配置主目录。
2. 在 `<host-root>/测试.v2 空间/` 准备字面 JSON 补丁：`[{"id":"session-persistence-jsonl","config":{"root":"<host-root>/测试.v2 空间/会话"}},{"id":"session-title-llm","disabled":true}]`，另按所测范围加权限行；不把路径写成点号键。真实 `C --profile acp --patch <补丁> --dump-config` 不调用模型，需由 Host 私下核对 settings/credentials 行与其未加补丁的原生 dump 相同，用户 home/profile 补丁保留，遥测行与原生 dump 相同，标题行 disabled=true，会话 root 等于指定路径；dump 可能含敏感配置，只保留核对摘要。
3. 对目录发现、Worker 初始/续接、none 调用分别用最终启动 argv/env 创建 ACP 连接，initialize → new → 读 configOptions → set_config_option（只针对回合路径）→ 读回 provider/model/effort → close → 停止；不调用 prompt/authenticate。none 使用原来的固定权限补丁，审阅仍不可用，不另建通道。读回选项应是用户原生目录的选项，设置的值应等于被冻结的请求值。
4. 第二个独立进程沿用同一个私有会话根，resume 第一个进程返回的 sessionId，重新读回配置、close 并确认停止；若无 prompt 的 new/close 未落盘，如实记录而不偷偷加模型调用。比较私有 root 下的记录位置与固定 binding 路径；带 prompt 的两回合真实续接仍由 Host 授权后验收。

可用 `PYTHONPATH=src:tests/python <只读解释器> tests/probes/dsh_native_resume_preflight.py --task-root "<Host 已分配的验证根>" --run-label "batch3d.v2 空间 测试"` 验证两进程恢复；先按探针要求清除继承的运行时/Worker/agent 环境钉住的变量，保留用户自己的 HOME/DSH_HOME；不传 prompt，不运行任何服务。`tests/probes/dsh_native_resume_preflight.py` 已同步删除旧函数导入、profile 物化、私有 DSH_HOME 与 settings/credentials 覆盖，补丁只固定共享会话根并禁用标题；它的两进程无模型协议顺序保持不变，供 Host 按其中的命令说明执行。

## 实际验证与交回边界

仅使用指定的只读解释器，`PYTHONPATH=src:tests/python` 优先于安装包；每次测试启动先按前缀清 BUDDY、C2、DSH、DEEPSEEK、Claude/Anthropic 变量并清 `VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`，再设置测试自己的一次性 HOME/DSH_HOME 和临时根。不运行完整检查、服务、Worker、模型或真实配置读回。

- 上一调查回合聚焦 69 项通过（6.276 秒，退出 0）：`buddy.harnesses.test_harness_discovery` 全文件、`buddy.harnesses.dsh.test_session_records` 全文件，`cli.test_checks_cleanup` 的 `ChecksEnvironmentTests`、`ResidueObservationTests`、`DeletedOpenFileObservationTests`，以及 `cli.test_support_cleanup.FixtureCleanupTests` 的两个 child_environment 测试；原始日志在忽略的 `tmp/adr030-batch3d/final-focused-tests.log`。
- 上一调查回合首轮上述三个主测试文件共 74 项，5 项失败：新白名单测试漏算公共函数总会返回的空 PATH，已修测试；另 4 项旧清理测试因沙盒 `ps process enumeration failed`，没有改生产检查或绕过沙盒，不能把这次全文件运行报告为通过。
- 两处用量测试保持生产行为不变：非 session 第一行现在刻意带相同 version/id，避免另一判断遮住 type 检查；超限冻结使用大于实际 64 MiB 总上限、每行小于 4 MiB 的真实文件，行内容各异，最终读允许看全文件，避免最终读的截断/重复行检查遮住冻结不可靠的判断。
- 在独立测试进程里仅替换 `_matching_lines` 的函数对象：单独去掉 type 判断，以及单独把 `not stream.truncated` 改成 True，各对应测试实际失败一次，都是 `baseline.reliable` 的断言失败，无导入/环境错误；恢复原函数后记录测试全文件 18 项通过（0.449 秒）。日志在 `tmp/adr030-batch3d/mutation-validation.log`，生产源码没有变异。

请 Host 在能观察进程的环境运行 `cli.test_checks_cleanup.PrivateRootTeardownTests` 整类，特别是 `test_teardown_drains_a_cooperative_supervisor`、`test_teardown_removes_a_clean_private_root`、`test_teardown_reports_a_surviving_process_with_unlinked_lock`、`test_teardown_waits_out_a_live_process_with_unlinked_lock` 这四项失败项；它们不校验新增的 DSH 环境过滤，但全文件验收仍需要通过。`cli.test_support_cleanup` 的其余项由 Host 跑，其中 `test_r06_child_uses_private_state_and_explicit_catalog` 会初始化 C-Two，`test_cleanup_stops_cli_started_replacement_after_restart` 与 `test_private_state_context_stops_its_cli_started_service` 会启动服务/Worker，本任务不运行。

本回合最终聚焦 210 项通过（33.978 秒，退出 0），日志为忽略的 `tmp/adr030-batch3d/core-final.log`：上述发现与记录读取文件全量，ACP launch/session/connection 三个文件全量、DSH 会话存储文件全量、native_run 与 native_resume 除下列九项 C-Two 用例外全量，以及检查运行器的环境/锁/模拟 lsof 测试和 support 的两个环境测试。核心首轮 200 项有 1 处旧错误文案断言失败与 3 处测试错误（旧主目录字段断言、新测试漏导入常量），已经修正，没有以沙盒限制掩盖范围内错误。最终模拟程序在发现、Worker 和无工具三条路径分别读到用户目录中的 settings、home patch、ACP profile patch，文件原字节未变，补丁没有 settings、credentials 或 telemetry 行；默认 HOME 下未设置 DSH_HOME 也读到模拟原生配置，read 权限值照常注入，真实假进程的双回合原生续接和本回合新增用量通过。

最终又独立重做两处函数对象变异，各对应测试断言失败一次；恢复后记录读取全文件 18 项通过（0.435 秒），日志在 `tmp/adr030-batch3d/final-mutations.log`。探针 `--help` 退出 0，静态检查确认不再引用已删的函数；未执行真实 CLI 握手。没有改 roles、其他 harness、workflow、schema、安装资源或公共模型目录/计费逻辑。

本回合九项 C-Two 用例未运行，按 Host 要求交回（没有通过改模拟 transport 绕过沙盒）：

- `buddy.harnesses.dsh.test_native_resume.ResumeInquiryTests.test_a_resumed_turn_delivers_this_attempts_question_over_its_own_channel`。
- `buddy.harnesses.dsh.test_native_run.InquirySeamTests.test_a_delivered_but_unanswered_question_turns_completion_into_attention`。
- `buddy.harnesses.dsh.test_native_run.InquirySeamTests.test_a_foreign_journal_record_never_binds_this_attempt`。
- `buddy.harnesses.dsh.test_native_run.InquirySeamTests.test_a_forged_checkpoint_receipt_fails_the_turn`。
- `buddy.harnesses.dsh.test_native_run.InquirySeamTests.test_an_asked_question_is_delivered_at_the_checkpoint_and_answered`。
- `buddy.harnesses.dsh.test_native_run.InquirySeamTests.test_the_shared_endpoint_declares_the_registered_capability_and_binds_identity`。
- `buddy.harnesses.dsh.test_native_run.WorkerSeamTests.test_a_refused_activity_callback_keeps_transport_unknown_and_native_facts`。
- `buddy.harnesses.dsh.test_native_run.WorkerSeamTests.test_a_tampered_finish_receipt_is_fatal`。
- `buddy.harnesses.dsh.test_native_run.WorkerSeamTests.test_the_governed_activity_callback_publishes_bounded_phases`。

另外请 Host 运行 `buddy.harnesses.test_private_adapter_invariants` 全文件（DSH 的旧私有 home 断言已更新，此处夹具要配置 C-Two 并启动角色控制器），`buddy.harnesses.dsh.test_dsh_role_wiring`、`buddy.harnesses.dsh.test_no_tool_dsh`、`buddy.roles.test_dsh_resume_storage` 和 `blackboard.tasks.test_dsh_resume_selection`，以及前述四项清理测试与 service/support 测试；它们属于控制器/服务/监听的验收边界，没有在本回合启动。Host 再做完整检查、最终参数下的无模型验证、已授权的真实回合与验收。没有修改或重启日常服务，没有读用户设置、登录或付费调用。

最终补丁范围还包括仅描述 DSH 旧行为的 `docs/reference/workers.md` 与 `architecture.md` 句子，以及 DSH 的模拟原生程序/旧 workflow 夹具和原生续接探针；没有更动其他 harness 的实现或测试。保留的 `default_dsh_home` 只用于防止证据根触及用户目录，不决定原生配置位置。所有记录与本文使用仓库相对路径、`~` 或占位符，没有本机主目录地址或其他项目仓库/文件地址。

最终 `git diff --check` 退出 0；19 个改过的 Python 文件均用指定只读解释器编译通过，没有写字节码。最终源文件没有再修改，完整检查及真实接口验收仍留给 Host。
