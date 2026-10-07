# ADR-025 第四步 4-B1：DSH ACP 原生运行主体

本记录是 ADR-025 第四步微任务 4-B1 的交付材料：在基线 `5d31114b` 的受管 worktree 中，基于已验收的 DSH ACP 客户端（`docs/acceptance/adr025-dsh-acp-client.md`）实现统一原生运行主体 `run(request, *, observer, services, cancelled) -> RunResult` 与无输入发现 `run_discovery`，代码在 `src/hey_my_buddy/buddy/harnesses/dsh/native_run.py` 与 `.../dsh/protocol.py`，测试在 `tests/python/buddy/harnesses/dsh/test_native_run.py`（用本 harness 既有的假 ACP 程序 `acp/fake_agent.py`，经同一启动包装启动）。初稿因两小时预算止于 partial-output `cb9dfbc`（未整合、未验收）；本记录覆盖原 run 的同题继续轮：Host 已提交公共决定与依赖（`02d51df` 载体搬家、`ae9f943` 请求字段、`2d844db` 续接检查点、`2c1fd12` zstandard 依赖与两项新批准行为差异），本轮以其为验证基线补齐缺口。本微任务仍是 4-B 线的第一片：初版主体未注册进 `RUN_SEAMS`，旧 Node 入口（`dsh/adapter.py`、`dsh/runner.py`、`harnesses/dsh/`）原样保留到本线后续 4-B2/4-C 切换与删除，第四步整体未完成，本记录不声称任何整步完成。

## 更正与偏差（对初稿记录的修正）

- 初稿"边界与方法"曾声称"全程零已安装 DSH 启动"，该结论不成立，现予更正：初稿后文自己承认旧入口桩测试在不带 `BUDDY_DEV_SOURCE=1` 时回退真实安装版 dsh；Host 独立核对到本任务 `m/repro2/attempt/runner.stdout.log` 中旧入口适配器一次运行的 `dshBin` 为安装版（`~/.local/bin/dsh`）、退出码 1、stderr 为 `MISSING_CREDENTIAL`（无凭据退出，未发生任何模型调用），且 `m/repro_adapter.py` 把该次 DSH_HOME 绑定在任务私有 `m/repro2/home`。
- 按现存记录能确认的只有上述至少一次安装版 dsh 预检启动及其凭据缺失结果；此类启动的准确总次数无法从留存的测试日志重建，如实记为未知，不补写、不追查用户日常数据。初稿的"零模型调用"结论维持不变。

## 边界与方法

- 全程零模型调用、零新增安装版 DSH 启动：原生行为全部由本 harness 的 Python 模拟程序覆盖；真实模型冒烟（含只读预设下"用命令写文件"与真实会话记录用量）按任务书由 Host 在本线整合后执行，模拟测试通过不构成原生强制或记录格式已验证的声明。
- 本轮验证基线是 Host 提交 `2c1fd12`：受管检出 HEAD 仍在 `5d31114`（不切分支），一次性组合副本在任务 `m/continue1/comb` 用 `git archive 2c1fd12`（src/tests/pyproject/uv.lock/packaging/harnesses）展开后仅覆盖本任务 scope 四文件（清单与哈希在 `m/continue1/combined-manifest.txt`）；解释器用 Host 整合检出 `~/.codex/worktrees/adr025-run-module/hey-my-buddy/.venv/bin/python`（不解析符号链接；`pyproject/uv.lock` 在 `2c1fd12..428fcbb` 无变化，依赖一致）。聚焦命令均带 `BUDDY_DEV_SOURCE=1`、私有 `TMPDIR`/`BUDDY_CHECKS_TMPDIR`（任务根 `t/`）与副本 `PYTHONPATH`。
- 一次性材料在任务根 `/private/tmp/a254b1-hat6zxhn/`：本轮新目录 `m/continue1/`（组合副本、基线/改动后完整 unittest 编号清单、新增故障注入摘要与 `mutation-m3..m5` 变异副本）；初稿目录原样保留。Worker 未手动删除任何对象；任务根整体等 Host 验收后按确切根回收。

## 交付内容（按运行阶段）

- 准备与接缝校验：harness 名、服务绑定类型、会话服务描述与挂载一致性、输出 schema 与完成工具契约一致性、native resume 未接线即拒绝，全部在任何进程存在之前判定（`_check_service_descriptions`、`run` 前置分支）。
- 启动与初始化：`tool_scope_launch` 把工具范围翻译为公开启动配置——write 即默认 workspace-write 预设，read 即 `DSH_PERMISSION_MODE=read-only` 子进程键，none 即已核对 15 个工具行加 `plan-mode` 行的 `--patch`。每次启动的统一 patch 顺序：源 home 的 `settings`/`credentials` 指路行（仅当文件存在）、私有会话记录根钉住行（`session-persistence-jsonl` 指向本运行私有 `DSH_HOME/sessions`，防源 settings 覆盖外移）、工具范围行、两项始终关闭行最后。任何启动（含本片全部测试的假程序）都经已验收的 `dsh/acp/launch.py` 私有包装：强制本运行私有 `DSH_HOME`，环境为 `native_environment` 白名单加显式校验的私有目录，`HOME` 默认继承、探针与测试可显式指定私有值；`--version/--help` 形态的调用同样必须走该包装。
- 工具行清单（初稿缺口一关闭）：`NONE_SCOPE_DISABLED_ROWS` 现为 15 项（含平台互斥的 bash/pwsh），来源是 Host 在私有 HOME/DSH_HOME 的免模型 `dump-config` 组合面读取（2026-10-06，原文留存于 Host 任务 `m/dsh-tool-inventory2/dump-config.stdout`，只读引用）：16 个 `tool-` 行中 `tool-result-pruner` 是上下文剪枝不属工具提供行，其余 15 行为工具行；历史"14 行"数字被本次组合面读取取代；`commands` 不能禁；清单外新工具行仍如实报告为保留事实，绝不当作已限制，不对厂商包作静态证明。
- 两项新批准行为差异（第四、五项，用户 2026-10-06 明确同意并要求登记）：hey-my-buddy 自己启动的所有 DSH 运行——Worker、快速调用与无输入发现一律——经本运行的私有 patch 关闭 `session-title-llm` 与 `session-telemetry-otel`；初稿只在 run 主体应用、发现漏掉，本轮已统一到 `_launch_agent`；用户自己交互使用的 DSH 不受影响，未改任何用户配置。
- 来源绑定（Host 问询 `adr025-4b1-native-config-source` 的落实）：来源 home 取父环境 `DSH_HOME`，缺省 `~/.dsh`（旧入口与 catalog 同款约定；launcher 已透传该变量）；`settings.yaml`/`.credentials.yaml` 存在时经公共 patch 行指路（旧快速 runner 的生产行形状），模块只取路径、绝不读内容——测试把凭据文件 chmod 至本用户不可读后运行仍成功；私有 home 物化 Host 探针验证过的 acp profile manifest（base+acp-app bundles、startup patchReload、空 patch 层；本机安装 home 无 `profiles/acp` 可复制，bundle 由 dsh 安装自行解析）。
- 会话与配置：`session/new` 恒带 `mcpServers`（空或本运行挂载），`session/set_config_option` 以声明值字符串选择模型与推理强度，checked 事实只来自响应自身的选项组回读（`declared-option-membership` + `set_config_option-readback`），requested 与 checked 严格分列，会话内无模型身份回报，不伪造 observed。
- 事件循环：单一 `_prompt_and_observe` 承载 prompt 线程、更新队列、取消/截止检查、逐帧折叠与角色反馈；每帧先投影工具事实与未知事件分类，governed 轮再进根回合证据；观察者的 stop 通过发送 `session/cancel` 执行，截止耗尽同样请求中断并有界放弃后交由组停止收尾。工具事实经共享固定表按原生名归类（DSH 恒报 `other`，未知名一律 other），新工具行如实保留为事实。
- 两个载体：final-message（快速路由，无服务，按角色反馈在同期新会话上执行至多角色自定的纠正）与 governed completion-tool（Worker 回合，挂载会话 MCP，完成值只有已验证签收回执里的 outcome）。会话工具回执与拒绝对信封经 `dsh/protocol.py` 独立验证（签名、尝试/输入/工具绑定），复用 `roles.worker_services` 与 `session_receipts`，未复制任何角色业务逻辑；权限回调只拒绝升级（共享 `PermissionPolicy`），每次拒绝写入挂载的 attention 文件供角色完成规则判用。
- 可选私有会话记录读模型（初稿缺口三关闭）：Host 已按 Python 包版本固定 `zstandard>=0.25,<1`（0.25.0）。`session_record_facts` 对本次私有 `DSH_HOME` 的 `sessions` 根做有界确定性扫描（深度/文件数/解压总量/单行界限），`.v3.jsonl.zstd` 走库内流式解压（不用系统 zstd 命令、不整体解压、不手写解码），`.v3.jsonl` 直读；按记录头部的会话 id 归属本运行的会话（外来记录跳过）；投影沿用旧 usage 观察者规则（逐字段非负整数求和、缺字段不计零、缓存按记录自身计数器推导一次、仅"见到已完成回合结束且无缺失用量且未截断"为 complete），错误回合只保留机器码。公共出口：`RunResult.usage`（经 `UsagePackage` 归一）、`native_failure`（quota 分类）、`last_assistant_message`；观察到的模型身份与逐步用量明细随私有证据引用 `dsh-session-record` 留存（公共契约无 observed 配置槽，requested/checked/observed 不混填）。缺失/外来/不可读/越界一律记 unknown 或 partial，绝不影响原生事实留存；正常有记录的 Worker 运行 usage 不为 None。
- 角色绑定（初稿缺口二关闭）：`prepare_services` 的参数与共享角色控制器（`roles/run_execution.worker_request`）完全一致（`invocation_root/identity/input_sha256/attention_path/session_tools/completion_tool/validate_outcome/inquiry/inquiry_tools/activity_dir/native_stderr`），挂载命令直接命名 Host 搬定的 `hey_my_buddy.buddy.roles.session_mcp`（`02d51df`），不复制业务逻辑；`native_stderr` 由 governed 轮消费为 stderr 尾巴镜像路径。
- 收集与停止：`session/close` 未确认即失败；EOF 排空保留停止后到达的每一帧（迟到事实仍达角色，角色 stop 才标记流不完整）；组停止未确认时升级一次组终止并如实报告 `gone/alive/unknown`，`spawn-never-happened` 是无进程时唯一的诚实 gone；`session/cancel` 只有传输事实，无 ack 可报。
- 发现：`run_discovery` 复用同一起动/握手/停止原语与统一 patch（记录根钉住加两项始终关闭行，无工具范围行），建一个裸会话读声明选择器后即关；从不发送 prompt，目录投影如实标注 per-model effort 可用性与上下文窗口在该免提示面不可得。

## 验证证据

- 聚焦测试（组合副本上执行）：`tests/python/buddy/harnesses/dsh` 全树 148 项通过（含保留并运行的旧入口测试 113 项与本模块 35 项），`tests/python/buddy/harnesses/test_run_contract.py` 31 项通过；退出码 0，无模型调用、无安装版 DSH 启动。完整检查按任务书由 Host 整合后以默认并行数统一执行一次。
- 测试编号对账（真实 unittest 加载收集，原始清单 `m/continue1/test-ids-before-2c1fd12.txt` 113 项、`test-ids-after-comb.txt` 148 项）：相对基线 `2c1fd12`（与 `5d31114` 的 dsh 树相同）新增 35 项、删除 0 项、未变 113 项以集合相等证明；相对初稿 partial 的 25 项：21 项原样保留、3 项改名（usage 来源挂载、载体模块断言、空清单拒绝路径）、11 项新增，逐项见 `docs/acceptance/adr025-step4-dsh-native-test-ids.tsv`。
- 迁移防护故障注入：初稿 M1（完成回执签名验证被跳过）、M2（leader 退出读作组消失）针对的防护未改动，失败记录仍见 `m/fault-injection-summary.md`，未重跑；本轮新增 M3（工具行清单被清空 → 清单守卫测试失败）、M4（外来会话记录绑定被去掉 → 外来记录测试失败）、M5（统一 patch 的两项始终关闭行被去掉 → 发现 patch 测试失败），均在 `m/continue1/mutation-m3..m5` 私有副本上"变异 FAILED → 未改动 comb 原件 OK"，摘要见 `m/continue1/fault-injection-summary.md`。

## 行为差异与声明边界

- 已接受行为差异共五项：初稿三项（检查点问询送达、快速路由多出 DSH 系统提示词、原生续接为未接线新能力——本模块对带续接的请求仍拒绝）加本轮登记的第四、五项（所有 hey-my-buddy 启动的 DSH 运行关闭 title-llm 与 otel）。本片不接问询桥、不用原生 resume；问询工具的回执/拒绝对信封验证已就位，桥接与 journal 写侧留给 4-B2 经 `ExistingLiveChannel` 接线。
- 工具范围由公开启动配置满足属于已核对的实现载体选择；read 预设对"用命令写文件"的约束尚未经真实冒烟验证，`effective_policy.filesystem` 如实报 unknown（basis `readonly-command-write-smoke-pending`），不宣称命令写入已被原生强制。
- Host 的免模型 none 组合启动核对（ACP 初始化/新会话/关闭，patch 为 15 工具行加 plan-mode 加两项始终关闭行）先于本轮统一 patch；现 patch 在源文件存在时另含 settings/credentials 指路行与记录根钉住行（均为旧入口生产行形状），该组合变化留给 Host 整合后的最小真实冒烟一并核对；实际有效工具集仍以本线冒烟从本次私有记录核对为准。
- 用量边界：记录读模型的测试使用假程序写出的合成 `session.v3` rollout（夹具，不是原生记录），不构成 DSH 记录布局的原生证明；真实记录上的用量非退化（token 用量不倒退）由 Host 在实际接线后按 4-D 要求冒烟验证。

## 缺口与公共接线清单（由 Host 统一处理后同 run continue 或并入 4-B2）

- 接线缺口：注册 `RUN_SEAMS["dsh"]`、角色控制器组装 `RunRequest`/观察器/收集器、问询桥与 journal 写侧经 `ExistingLiveChannel`、DSH 原生 resume（现为拒绝）、旧入口删除（4-C）。本片为这些接缝提供的过程入口：`prepare_services`（返回 `BoundSessionServices`，参数与共享角色控制器一致）、`run`、`run_discovery`。
- Host 冒烟缺口：真实会话记录上的用量与模型身份读取（合成夹具不能代替）、只读预设下"用命令写文件"、从本次私有记录核对实际生效工具集。
- 无新的公共字段/公共值请求：本轮消费的公共面（`roles.session_mcp`、`session_receipts`、`RunResult` 各包、`worker_services.session_tools()`）均已由 Host 就位。

## 无生产使用方与实现说明

- 本模块过程入口在 4-B2 接线前没有生产使用方（现状即知）；`RunResult.usage` 在快速通道的现行角色投影（`_fast_result`）只读工具调用计数、不读用量包，用量事实对该通道暂无公共读取方（Worker 通道 `_worker_facts` 读取 `tokenUsage`）；记录的观察模型身份与逐步用量明细只进私有证据引用（公共契约无对应槽位）；`run_discovery` 的 `home` 覆盖参数仍只有测试消费方；除此之外本轮未新增无读写方的字段或类。
- 源码行数：`native_run.py` 1796 行（初稿 1359，+437：来源绑定与 profile 物化、记录读模型、prepare_services 重接、统一 patch）；`protocol.py` 649 行（未改）；`test_native_run.py` 817 行（初稿 624，+193）；`fake_agent.py` 736 行（初稿 637，+99：合成会话记录写侧与 stderr 注记，全部增量，既有 ACP 客户端测试原样通过）。`run` 组织函数行数与初稿相当（约 127 行含接缝闭包），新增逻辑按阶段拆分（读模型、启动组装各自成段），未复制 zcode 的长函数。
- 本记录中的路径均为仓库相对、`~` 或占位；任务根 `/private/tmp/a254b1-hat6zxhn/` 及其内容未做任何手动删除，等 Host 按确切根整体回收。

## 拒绝轮整改（固定交付 04f43c5 被拒后的四项修复）

- 本轮范围、buddy 与任务根不变：前轮已验收方向的交付（15 行清单、来源绑定、压缩记录读取、两项关闭行、角色绑定重接）原样保留，只修 Host 探针定位的四处代码/测试缺陷；公共验证基线换成 Host 提交 `73f4aac`（组合副本 `m/continue2/comb`，archive 73f4aac 加仅 scope 四文件覆盖，清单同前轮方式留 `m/continue2/`），解释器仍用 Host 整合检出 `~/.codex/worktrees/adr025-run-module/hey-my-buddy/.venv/bin/python`（不解析符号链接）。
- 修复一（无会话不等于无进程）：`_build_result` 不再以会话列表为空推定从未 spawn——持有中的 client 用它自己的组观察（gone/alive/unknown，`owned-acp-process-group`，started=true），`_RunState.spawn_failure` 现为声明字段并消费 launch 包装在子进程已存在后的 finalize 证据（basis `launch-bookkeeping-finalize`），只有 client 不存在且无 spawn 证据时才是唯一的 `spawn-never-happened` gone；补"初始化前失败、停止未知仍 unknown"与"bookkeeping 失败"两项聚焦见证。
- 修复二（尾部事实交角色、流证据分立）：`_drain_late` 现对每个产生新事实的迟到帧调用角色观察者——无工具角色据此拒绝（run 转 cancelled/observer-interrupt，迟到事实保留，流不完整），继续型角色则只记录；`stream_complete` 改为只按流自身证据（EOF 排空完成加已开启根回合到达原生终点）判定，与业务 status 和组停止分立——"已排空完成但组停止未知"见证中断言 streamComplete 为真而 end 为 error、组为 unknown。
- 修复三（最终文本绑定本轮根）：final-message 载体的 `chunk_fold` 只收本轮真实 `session/new` 根的 `agent_message_chunk`（纠正轮各绑各根）；外来/前一根文本隔离为 `foreign-root-text` 未知事实，绝不当本轮答案；governed 载体的根回合证据本就按会话绑定，未改。
- 修复四（未知种类有界聚合）：`_RunFacts` 在共享契约上限 `MAX_UNKNOWN_EVENT_TYPES`（64）内保留种类，超出折叠进单个 `unclassified-surplus` 桶，total 永不丢失——65 种 future-* 通知的完整 run 见证通过（total=65、种类数≤64、run 正常完成、组停止事实保留），未为厂商事件新增校验层。
- 验证：组合副本上 `test_native_run` 42 项通过（前轮 35 项全部保留，新增 7 项见证，删除与改名均为零，清单 `m/continue2/test-ids-after-73f4aac.txt`；TSV 42 行与真实加载集合逐一相符）；未变 ACP 客户端 71 项与公共契约 31 项按 Host 指示未重跑，完整检查归 Host。
- 本轮迁移防护变异两项（`m/continue2/mutation-a`、`mutation-b`，均为全新副本）：A 折叠"持有进程"分支回 spawn-never-happened → 未观察进程见证 FAILED，原件 OK；B 去掉尾部 notify → 迟到工具拒绝见证 FAILED，原件 OK；摘要 `m/continue2/fault-injection-summary.md`。前轮 M1–M5 针对的防护未改动，未重跑。
- 行数：`native_run.py` 1860（前轮 1796，+64）、`test_native_run.py` 936（+119）、`fake_agent.py` 772（+36，三个增量开关：外来根 chunk、65 种未知、drain 期同根工具帧）；`protocol.py` 649 行仍未改。
- Host 探针定位的四处缺陷均由本 run 修复并以聚焦测试见证；Host 自身免模型探针（`tmp/adr025-host/step34-20261006-105245/4b1-host-fixed-probes.py` 及 JSON）未由本 run 重放，仅按其结论整改。
