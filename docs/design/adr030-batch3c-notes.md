# ADR-030 批次 3c：ZCode 供应方配置与回合事实

本说明对应原输入提交 `71f0a3f6` 与第一回合封存 `e51f0e12` 后 Host 的续接决定，仅是实现方的说明与验证记录，等待 Host 验收；不是验收结论。版本与 schema 未变，没有改 Claude Code、Codex、DSH、角色代码、共享发现的环境白名单或共享计费规则，没有启动服务、Worker 或真实 ZCode，没有读用户自己的配置或凭据，没有模型调用。

## Host 首次续接决定的四项更正

第二实现回合封存为 `b00d4ada` 的四项更正如下；第三回合封存 `ca1cad90` 只检查和验证已有实现、补记本说明，没有再改设计或实现代码。

1. 内置供应方文件改为定位安装中的原文件，绝不复制，且缺失在可用性与准备边界报真实不可用；个人文件由 ZCode 自己定位，计费读取失败只标未知。
2. 删除 `ZCODE_MODEL_TELEMETRY_ENABLED=0`，按 Host 传达的开发者决定让遥测跟 ZCode 自己的设置走。
3. 每次启动只读取用户设置的 `memoryEnabled` 与 `nativeSearchEnhancementsEnabled`，按用户值答复原生请求，在无工具范围收紧搜索，并保留成功发出的偏好答复事实。
4. 修正非密钥供应方准备测试的成功判据，要求调用成功并产生输入文件；模拟 CLI 与版本包装命令补齐原生必需的合成内置文件布局。

收尾检查确认 `check_preparation` 只调用 `cli_command` 与 `provider_paths`；运行模块、协议泵与探针里均没有 `SUPPORTED_ACCESS`、`unsupported-provider`、`restored-api-key-only` 或 `snapshot_provider_files`，临时恢复的密钥类拒绝没有留在源码里。四项更正的依据与边界分别写在下文，Host 先前报告的真实探测结果仍只作为 Host 提供的证据，不冒充本回合实测。

## 本次续接：标题用原生默认，核对真实 CLI 测试隔离

Host 在 `ca1cad90` 后传达开发者的决定：交给 harness 的内容清单是穷尽的，省一次模型调用也不是例外。本次从发现、带会话工具的 Worker 与最终消息载体的两种范围中删除 `titleGenerationEnabled=False`，问询实验与本说明的握手探针同步删除该字段。会话标题跟 ZCode 原生默认走；空工具清单、空会话 MCP、off-peak 与 dynamic workflow 的关闭仍表达无工具权限，不改；没有为可能出现的标题事件增加处理。模拟原生程序在收到建会话参数时明确断言没有标题覆盖，带工具的模拟程序对建会话与续接参数都作同一检查。

Host 在本次续接输入中报告：真实 ZCode 0.16.9 无须另外指定就能以发现、Worker、无工具三种方式启动，目录的 12 个四段名字与原有目录相同，启动前后用户供应方文件与应用包内置文件均未改写；额度用尽时 Worker 与无工具路径均带回原生失败原因和额度事实，回合里的记忆与搜索答复来自用户设置。这些是 Host 对标题覆盖删除前源码的实测，本实现方没有执行真实 CLI 或读取用户文件；删除覆盖后、原生生成标题时的无工具真实调用留给 Host 复核，不能用模拟程序的通过代替。

`InstalledZcodeTests.setUp` 先调用 `test_zcode.ZcodeFixtureCase.setUp`，后者在测试临时根下创建 `native-user-home`，用它覆盖环境里的 HOME、USERPROFILE 和 `ZCODE_DATA_BASE_DIR`；真实 CLI 测试随后把数据根换成同一临时根下的 `account-empty`，其包装脚本只指定临时根下的合成个人供应方文件与内置测试副本。`ExecutionContext.environment` 带入这些值，控制器再经现有原生环境白名单把 HOME、USERPROFILE、数据根带给 CLI，这只能证明传入的配置位置和 HOME 等环境值是私有的，不能据此断言原生运行状态也完全隔离；此前“原有隔离成立”的结论过强，按下文 Host 实测纠正。包装脚本的 bundled 内置路径仍是应用包里的公开内置文件；它不是用户个人配置。该测试需要真实 CLI、loopback HTTP 服务、macOS sandbox-exec 和角色启动器，本回合没有运行它。

另两条真实 CLI 测试是 `test_zcode_hooks_probe.FreeProbePipelineTests.test_free_probe_pipeline_is_isolated_and_selftested` 与 `test_gather_versions_reports_native_identities`；核对发现它们原来继承主目录，版本读取也直接继承整个测试进程环境。本次只在这组测试的 setUp 清除继承的 `BUDDY_`、`ZCODE_`、`ZAI_`、`ZHIPU_`、`GLM_` 配置/供应方变量及虚拟环境固定项，并把 HOME、USERPROFILE、数据根换成该测试临时根下的私有 home。`gather_versions` 的子进程因此继承私有 HOME 与数据根；`build_private_environment` 在 hooks 探针的版本与 plugins list 子进程里只透传这个私有 HOME，不透传数据根，个人供应方文件变量为空；这是出站环境的检查，不证明 ZCode 所有运行状态都跟着这些路径走。新增的非真实 CLI 回归以合成 bundle 初始化同一测试 fixture，证明继承的 HOME、数据根与个人供应方变量被替换或清除；没有修改 hooks 探针的实验行为。其余 ZCode 单元测试使用合成可执行文件或协议重放，启动命令由私有 fixture 显式固定；旧的跨 harness 安装发现测试按 Host 决定由 DSH 微任务处理，本批不改。

## 删除与保留

`zcode/config.py` 的 `snapshot_provider_files` 与 `SUPPORTED_ACCESS` 删除。启动不再生成 `builtin-provider.json`、`personal-provider.json`，不设置 `ZCODE_PERSONAL_PROVIDER_CONFIG_FILE`，也不再单独剔除 `ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE`。按 Host 第二回合的更正，设置 `ZCODE_BUILTIN_PROVIDER_CONFIG_FILE` 指向安装中原有的内置文件：ZCode 0.16.9 脱离桌面应用时自己算不出该位置，这是把 harness 启动起来所需的定位，不是替用户挑配置，绝不做副本。子进程仍使用 ADR-017/030 的共用环境白名单，没有新增 shell 供应方变量透传。ZCode 加载时可能规范化并写回自己的供应方配置文件；现在这是 ZCode 对用户配置的行为，照原生做法运行。

`ZcodeAdapter.available` 的本地检查和 `check_preparation` 检查选定的原生命令及必需内置文件的位置，不读取供应方文件内容或按登录方式拒绝。安装中的内置文件找不到是 `adapter-unavailable`，准备阶段即报错，不等子进程退出；计费文件的内容读不出则写未知，两者分开。命令不存在、缺少解释器或真实启动失败仍然失败。`configure_session` 删除访问类型参数与限制，仍核对所请求的供应方、模型、推理强度在原生清单里，调用原生 setter，并核对实际读回的选择。原生因文件读不到、登录失败或配置不可用而拒绝 RPC 的，继续作为真实原生失败上报；可选计费元数据读不到不会阻止原生启动。

模型目录不再按访问类型筛选，不再声称 OAuth 供应方不可用。原生报告的非密钥供应方照常进入目录，能经同一配置路径选择；是否能运行由真实回合决定。`interaction/requestProviderRuntimeHeaders` 的供应方专属硬错误删除，按现有的未实现宿主请求处理：记录交互事实、发送 JSON-RPC 不支持答复，继续处理原生事件。没有伪造认证头、登录或认证宿主；无工具角色对交互事实的停止规则未改，属于后续批次。

发现路径写出的 `.zcode/config.json`（关闭 plugins、MCP、memory、skill、subagent）删除，因为它替用户挑选配置。独立的 `tests/probes/zcode_request_input.py` 原来直接引用被删的快照函数，也删除复制与访问类型限制及相应测试假设；它仍是需 Host 授权的问询实验，不是本批的无模型验证方法。

### 启动环境逐项

| 项目 | 处理 | 依据与边界 |
| --- | --- | --- |
| `ZCODE_STORAGE_DIR` | 保留，指向微任务私有 native 根下的 `storage` | 原生会话存放处，ADR-030 第 3 条 |
| `ZCODE_SESSION_DB_PATH` | 保留，指向同一根下的 `sessions.sqlite` | 会话续接；绑定与精确身份校验未改 |
| `ZCODE_LOG_DIR` | 保留，指向本次 invocation 的 `native-logs` | 原生证据的位置 |
| `ZCODE_LOG_CONSOLE=0` | 保留 | stdout 是 JSON-RPC 传输，日志走独立证据路径 |
| `ZCODE_MODEL_TELEMETRY_ENABLED=0` | 删除 | Host 传达开发者已同意：遥测跟用户自己的 ZCode 设置走 |
| `ZCODE_DATA_BASE_DIR` | 沿用共用白名单，不改值 | 原生用户配置位置，计费读取使用同一位置 |
| `ZCODE_BUILTIN_PROVIDER_CONFIG_FILE` | 保留定位，等于 `provider_paths` 返回的安装内置原文件路径 | 独立 CLI 启动所需；缺失在准备阶段报不可用，不复制 |
| `ZCODE_PERSONAL_PROVIDER_CONFIG_FILE` | 不设置 | 原生自己找得到个人配置；shell 覆盖不属于共用白名单 |
| bundled 供应方文件变量 | 删除专门的剔除逻辑 | 不另替原生选择 bundled 文件；共用白名单未变 |
| `BUDDY_ZCODE_TEST_CASE` | 沿用仅开发测试时的透传 | 模拟 CLI 用例选择，不属于用户供应方设置 |
| `BUDDY_ZCODE_CLI` / `BUDDY_NODE` | 沿用开发命令选择，不传给原生 | 安装后的命令仍来自服务选定的运行时 |
| locale、代理、CA 路径与其他共用白名单项 | 未改 | 不在本批改共享发现规则 |

会话请求的 `yolo` 保留为当前 Worker 权限；快速调用的空工具清单、空会话 MCP、off-peak 与 dynamic workflow 关闭保留为无工具范围的表达。建会话不传标题开关，标题用原生默认。反向 `session/requestRuntimePreferences` 现在使用下面两项用户设置；无工具范围只收紧搜索。自动回答用户问题仍关闭，`modelContextBudgetStrategy=preflight-v1` 仍为当前宿主常量：Host 已确认应用设置里没有该策略键。

### 文件逐项

| 文件 | 处理 | 用途 |
| --- | --- | --- |
| 两个供应方配置副本 | 删除生成代码与旧快照 API | 含凭据的副本不再需要 |
| 发现 cwd 的 `.zcode/config.json` | 删除生成代码 | 原来关闭用户特性 |
| `finish-bridge.json` | 保留 | 本次私有会话工具的身份、输入摘要、签名密钥与契约绑定，不是供应方配置 |
| native 根中的 `<sessionHash>.json` | 保留 | `{taskId,sessionId,cwd,configuration}` 的续接绑定，字段与位置不变 |
| `sessions.sqlite` 与 `storage/` | 保留 | 原生私有会话数据，不是私有配置主目录 |
| `native.stderr.log`、`native-logs/`、`run-end-facts.json` | 保留 | 本次原生证据；失败公开投影仍不包含供应方原始错误正文 |
| `native-runtime-preferences.json` | 新增本次答复事实 | 只有值、来源及无工具收紧事实，通过已有 evidence_refs 绑定文件大小与摘要，未改共享结果契约 |
| 共享角色的输入、控制与结果文件 | 未改 | 任务与结果契约不在本批重新设计 |

存储清理、备份排除与私有迁移中对旧供应方副本文件名的识别未动；那是已有存量凭据材料的清理边界，不是运行时继续生成副本或快照 API 的兼容入口。

## 供应方名字与计费

改前目录的供应方名字来自 `settings.model.available[].ref.providerId`，只是经过访问类型筛选；改后仍来自同一字段，删除筛选，`providerLabel` 仍只作显示名称。模型 ID 仍是 `ref.modelId`，推理强度仍是原生 `reasoning.levels[].value`，harness 仍是 `zcode`。原来已被接受的供应方不会改名；同一份配置内容交给同一原生 CLI，四段身份的来源没有变。Host 在第二回合输入中报告：用真实 ZCode 0.16.9、只补内置定位的无模型探针读到 12 个四段名字，与日常记录全部一致，配置读回等于请求；这是 Host 的探测证据，本实现方没有读日常看板或执行真实 CLI。Host 在本次续接输入中已报告原文件定位与用户偏好的真实复核结果，见上节；模拟 CLI 不证明真实接口。

没有增加名为 `unknown` 的供应方或向原生 setter 传入伪造名字。ZCode 这条接口报告的是每个可用模型自己的 `ref.providerId`，没有依赖“缺省供应方”的目录分支；用户的默认选择来自原生 `settings.model.current.providerId`，配置核对使用显式请求与原生读回。缺少这些身份事实时，本批没有猜一个通用内置名字；真实 CLI 若出现这样的形状，交 Host 根据原生证据决定。

仅目录发现读取计费元数据，Worker 与无工具回合不再读取供应方文件。内建位置沿用原来的 CLI 相邻布局查找（`config/provider/zcode-builtin.json` 或 `provider/zcode-builtin.json`）；个人位置是原生数据根下的 `.zcode/v2/provider_config.json`。读取使用传给原生的白名单配置位置，不使用被剔除的 shell 供应方文件覆盖。

这是读凭据旁边的配置：JSON 解码会把整个文件的字节读入内存，只提取 `config.providerConfigRules` 下 `templateRules[].templateId`、`providerRules[].providerId`、`providerRules[].templateId` 与各条 `config.access.type`，按原有模板继承和个人覆盖关系标注。没有访问或返回 `apiKey`、令牌、`requestAuth`、请求头、API 地址、providerName、模型规则或其他配置字段，也不落盘原始 JSON。内部的供应方 ID 只用于把计费标注连接到原生清单，不能生成目录供应方。

只输出已知的 `api-key`（按量）、`zhipu-coding-plan-api-key`（套餐）或未知；可选个人文件缺失、计费内容拒绝读取、JSON 损坏、不能解析的结构与其他访问方式都标未知，不拒绝目录。安装内置文件本身缺失属于前述启动能力检查，不属于计费回退。这是原生配置的计费标注，不证明一次请求实际扣了资源包还是余额；没有查询厂商账户接口或根据消息文字推断扣费。

## 两项用户运行偏好

按 Host 实测的 ZCode 应用布局，在传给原生的用户数据根下读取 `.zcode/v2/setting.json`，只投影顶层 `memoryEnabled` 与 `nativeSearchEnhancementsEnabled` 两个键，且只认 JSON 布尔值。解码 JSON 必须将文件字节读入内存，但不访问、复制或返回其他字段，包括代理地址及 `askUserQuestionAutoResolutionEnabled`；不保存原文。每次启动读一次，格式纠正的多个会话使用同一读数，不随反向请求重新读取。

文件缺失、拒绝读取、不是 JSON、不是对象、单个键缺失或不是布尔值时，各键分别回退：记忆关闭，有工具的搜索默认开启，无工具的搜索始终关闭。正常设置中的记忆值用于所有调用；搜索在有工具时用用户值，在无工具时强制为 false，不能放宽范围。原生布局改变、导致这份可选文件或两个键读不到时，表现就是上述回退，没有新拒绝。

原生请求答复成功发送之后才记事实；回合保留 `native-runtime-preferences.json`，并用现有 `RunResult.evidence_refs` 给出文件位置、大小与 SHA-256。每项记录答复 `value` 与 `source=user-settings|fallback`；搜索另外记录 `configuredValue` 和 `restrictedByToolScope`，明确区分用户或回退的值与工具范围最终收紧的值。两个固定答复分别记录 `source=governed-role` 与 `source=harness-controller`。原生没有请求或答复没发成功时，不声称已经答复。没有新增共享契约字段或修改角色代码。

共用目录的 `blackboard/catalog/catalog.py` 仍允许访问类型字面量 `zhipu-account`，现在 ZCode 计费投影不会产生它；按 Host 的明确范围决定本批不改，由 Host 排入后续清理。

## 无工具路径的失败与用量

两种结果载体共用 `native_turn_failure`，它调用已有的 `decode_native_failure`；原有 quota 分类和运行结果收集也共用。无工具路径的 `turn-failed` 生命周期投影不再抢在带结构化归因的 canonical `turn.failed` 之前终止，但仍验证会话、回合与序号；canonical 事件真实失败时立即失败，`state.updated/prompt_failed` 单独到达仍报真实失败，不编造归因。1310、429、不可重试的原生结构会保留失败事实与额度耗尽事实，不公开原始正文。

无工具路径使用已有的 `ZcodeAttemptUsage`，在发送模型输入前读基线，在结算或失败时读最终 `session/messages`，并观察同一处的原生用量 telemetry；没有另写一套消息或 token 解码。格式纠正产生的各个根会话用量相加，任何会话读不到用量时标部分，不把前一次调用漏掉或补成零。带工具的单会话来源、计数与覆盖标签保持原样。续接、重建策略、会话绑定、进程持有与停止证据未改。

## 不调用模型的真实 CLI 核对办法（Host 执行）

下面从改后的公共运行模块调用同一 `_spawn_app_server`，使用它的参数与环境，再停在握手、配置与关闭；没有 `session/send`，不运行任何任务、角色控制器、服务或 Worker。请在私有验证目录中执行，保留用户的正常 HOME 与配置根，用已选定的真实 CLI（开发检出可显式设置 `BUDDY_DEV_SOURCE=1` 与 `BUDDY_ZCODE_CLI`），源码在 `PYTHONPATH` 最前。供应方加载可能触发上面说的原生规范化写回，不是只读文件探针。

```python
import json, tempfile, time, uuid
from pathlib import Path
from hey_my_buddy.buddy.harnesses.zcode import native_run as z
from hey_my_buddy.buddy.harnesses.native_support import CancelFlag
from hey_my_buddy.buddy.roles.worker_services import session_tools

# 完成三次，不给模型发输入；由 Host 记录真实 CLI 版本及退出证据。
root = Path(tempfile.mkdtemp(prefix="zcode.核对 空格-")).resolve()
root.chmod(0o700)
for mode in ("discovery", "worker", "no-tool"):
    private = root / mode
    private.mkdir(mode=0o700)
    cwd = private / "cwd.检出 空格"
    cwd.mkdir(parents=True, mode=0o700)
    invocation = private / "invocation"
    native = private / "native"
    params = {}
    if mode == "worker":
        identity = {"taskId": str(uuid.uuid4()), "attemptId": str(uuid.uuid4()),
                    "generation": 1, "turnId": str(uuid.uuid4())}
        mount = z.prepare_session_service(invocation_root=invocation,
            identity=identity, input_sha256="a" * 64,
            attention_path=invocation / "attention.json", session_tools=session_tools(),
            completion_tool="buddy_finish_turn")
        params.update(mode="yolo", mcpServers=list(mount.mcp_servers))
    else:
        params["toolAllowlist"] = []
        if mode == "no-tool":
            params.update(mcpServers=[], offPeakToolEnabled=False, dynamicWorkflowEnabled=False)
    invocation.mkdir(parents=True, mode=0o700, exist_ok=True)
    deadline = time.monotonic() + 60
    spawn = z._spawn_app_server(cwd=str(cwd), invocation_root=invocation,
        native_root=native, deadline=deadline, cancel=CancelFlag(lambda: False),
        stderr_path=invocation / "native.stderr.log", no_tools=mode != "worker")
    try:
        c = spawn.connection
        c.call("runtime/capabilities", {})
        snap = z._open_root(c, {"workspacePath": str(cwd), "workspaceKey": str(cwd)},
                            create_params=params)
        sid = z._checked_root_session(snap, str(cwd), previous=None, mode="initial",
                                      require_interactive=mode == "worker")
        models = snap["settings"]["model"]["available"]
        # 只记录非秘密的原生目录身份；与安装前目录的四段名字比较。
        names = [("zcode", m["ref"]["providerId"], m["ref"]["modelId"], level["value"])
                 for m in models for level in m.get("reasoning", {}).get("levels", [])]
        print(json.dumps({"mode": mode, "version": spawn.version, "names": names,
                          "runtimePreferences": c.runtime_preferences_facts}, ensure_ascii=False))
        if mode != "discovery":
            # Host 也可以把 wanted 换成目录里原来已启用的那一组名字。
            _, provider, model, effort = names[0]
            wanted = {"provider": provider, "model": model, "effort": effort}
            assert z.configure_session(c, snap, wanted) == wanted
        z._close_root_session(c, sid, None)
    finally:
        stopped, _ = z._stop_native(spawn, deadline)
        spawn.process.stdout.close()
    assert stopped and spawn.process.returncode == 0
    assert not list(invocation.glob("*-provider.json"))
    assert not (cwd / ".zcode/config.json").exists()
print(root)  # 保留本次证据供 Host 看；没有确认停止前不删除。
```

发现应返回正常用户配置中的供应方/模型/强度，不发提示词；Worker 与无工具的两个 setter 应读回 `wanted`，会话 workspacePath 应精确等于带点号、空格与非 ASCII 字符的 cwd，三个原生进程都应正常退出并确认组停止。用 Host 的进程环境观察工具另外核对：内置供应方变量等于安装中的原文件路径，个人供应方变量和遥测覆盖变量不存在，storage、session DB 与 log 变量指向本次私有目录，用户配置根保持原值。原生创建会话后的 `runtimePreferences` 应非空，记忆值等于用户的布尔设置；Worker 搜索值等于用户设置，发现与无工具两种模式的搜索值为 false；缺键或非法类型时，来源为 fallback，值按上节定义；自动回答问题为 false，预算策略为 preflight-v1。不要打印整个环境或整个原生 snapshot。此探针不证明模型调用、MCP 工具执行或登录宿主能力。

审阅调用没有变更过的启动路径：`ZcodeAdapter.read_only_structured=False`，`local_read_only_check()` 应仍返回 `eligible=False`、`readonly-worker-carrier-unimplemented`；审阅在资格边界停下，不启动 CLI。本批没有另造审阅执行载体。续接的模型前握手可在上面 Worker 会话关闭后、同一 storage/DB 环境中新起原生进程，调用 `session/resume` 并核对同一 sessionId、cwd 和 setter 读回；这只验证原生续接接口，完整绑定续接测试仍由下节的 Worker 测试覆盖。

三个建会话请求均不含 `titleGenerationEnabled`，不再要求原生读回标题关闭；握手探针不发送 `session/send`，标题生成期间的事件与无工具真实调用是否能正常结算，由 Host 在最终源码上实测，探针本身不证明这一点。

## 实现方验证与交接

第一回合可运行集合 119 项通过（30.715 秒，退出码 0）；第三回合收尾的可运行集合 156 项通过，具体结果见下文，历史结果不替代本次验证。文中的 Host 无模型探针仅做了 Python 语法解析，本实现方没有执行。使用 Host 指定的只读 Python，`PYTHONPATH=src:tests/python`，清除 AGENTS.md 的运行时、Worker、agent、虚拟环境变量及 `BUDDY_`、`ZCODE_`、`ZAI_`、`ZHIPU_`、`GLM_`、`ANTHROPIC_`、`CLAUDE_CODE_`、`C2_` 前缀，再显式设置 `BUDDY_DEV_SOURCE=1`。`cli/checks.py` 与 `tests/python/support.py` 只按任务要求增加 ZCode 与相关供应方前缀的测试隔离，没有修改产品环境构造；ZCode 的模拟 CLI 测试也清除这些继承变量，并把 HOME、USERPROFILE 与原生数据根设为私有合成目录。

本环境可运行的检查集合是：`buddy.harnesses.zcode.test_native_config`、`test_zcode_protocol`、`test_zcode_request_input_probe` 全文件；`test_native_run.FastSeamTests`、`NoToolReverseRequestTests`、`DiscoveryStopTests`；`test_no_tool_zcode.NoToolPolicyTests`；`test_zcode_tool_evidence.ProjectionTests`、`ProjectionOrderingTests`；`cli.test_checks_cleanup.ChecksEnvironmentTests`；`cli.test_support_cleanup.FixtureCleanupTests.test_child_environment_drops_inherited_ownership_and_credentials`。新测试用私有原生配置路径与带点号、空格、非 ASCII 字符的 CLI/cwd，覆盖非密钥供应方发现与选择、计费未知、没有快照、私有会话位置、失败归因/额度、成功用量与格式纠正的累计用量。

第一回合还尝试了 `test_no_tool_zcode.NoToolFakeProtocolTests` 与 `test_zcode_tool_evidence.NoToolReceiptWiringTests`：所有经共享角色控制器启动的这些用例都未得到有效结果，报告 `invalid-native-result`，stderr 为空，取消用例没有等到模拟 CLI 收到提示词；没有证据把它归因于 ZCode 原生接口，不改共享角色或启动器来绕过。按任务中真实启动器的环境边界交 Host 在验收环境重跑，以上本地失败不算通过；Host 在第二回合输入中报告这些控制器测试均已通过，完整检查 217 个文件中 216 个通过（3,654 项），前端全量通过，唯一失败是下述准备测试断言。直接运行模块的新测试不经过这个控制器。

最终改动后仍由 Host 重跑 `test_zcode.py`、`test_native_run.WorkerSeamTests` 与 `ProducerContractTests`、`test_zcode_native.InstalledZcodeTests`、`test_zcode_checkpoint.py`、`test_zcode_inquiry.py`、`test_zcode_tool_refusals.py`、`buddy.roles.test_registered_run_wiring`、`blackboard.evaluation.test_other_native_observations` 的 ZCode 控制器用例，以及 `cli.test_checks_cleanup`、`cli.test_support_cleanup` 剩余需要私有服务/IPC 的用例；其中已存的精确续接回归要保持通过。完整 Python 检查、真实 Worker/Router 回合与停止故障注入按原任务由 Host 做。第一回合原文件定位缺失由 Host 无模型探针查出；原文件定位与偏好答复的真实 CLI 复核由 Host 在本次续接输入中报告，标题取消后的真实回合仍由 Host 复核，没有前端改动。

第二回合修正 `test_zcode.ZcodeAdapterTests.test_oauth_provider_is_not_rejected_during_preparation`：`prepare` 成功返回 None，不断言返回值，改为要求调用不拒绝并产生本回合的输入文件。正常代码下该测试通过；在仅测试进程中临时恢复按访问类型拒绝的准备逻辑时，该测试因 restored-api-key-only 错误而失败，退出后 mock 自动恢复，没有改源文件。模拟原生 CLI 的安装布局补上必需的合成内置文件；版本测试里的 ZCode 包装命令也使用该合成布局，不增加对其他 harness 的测试配置或产品改动。

最终收尾验证（2026-10-12）分两组：第一组 127 项通过（36.245 秒，退出码 0），完整运行 `test_native_config`、`test_zcode_protocol`、`test_zcode_request_input_probe`，并运行上面列出的直接运行模块、投影与隔离检查，另加 `test_zcode.ZcodeAdapterTests.test_oauth_provider_is_not_rejected_during_preparation` 和 `test_zcode_native.DiscoveryReceiptTests`；第二组 29 项通过（22.748 秒，退出码 0），完整运行 `buddy.harnesses.test_runtime_version` 的 19 项、`test_zcode.ZcodeAdapterTests.test_coding_never_falls_back_to_native_defaults`、`cli.test_checks_cleanup.ResidueObservationTests`、`DeletedOpenFileObservationTests`、`PrivateRootTeardownTests.test_drain_reaches_worker_dirs_with_no_visible_lock`，以及 `cli.test_support_cleanup.FixtureCleanupTests` 中的 `test_unconfirmed_shutdown_preserves_private_state`、`test_child_environment_preserves_explicit_private_paths`、`test_r06_child_uses_private_state_and_explicit_catalog`、`test_r06_catalog_owner_passes_fixture_after_inherited_pins_are_removed`、`test_r05_catalog_daemon_fixture_binds_capabilities_without_native_discovery`。原始日志与精确选择保留在忽略目录 `tmp/adr030-batch3c-final-tests.log`、`tmp/adr030-batch3c-final-tests3.log` 及对应 selection 文件；没有付费调用、真实 harness 启动或前端改动。

第二组首次扩展为 32 项时，`cli.test_checks_cleanup.PrivateRootTeardownTests.test_teardown_removes_a_clean_private_root` 失败，失败证据明确为 `ps process enumeration failed; residue status is unknown` 两次，属于沙盒不能枚举进程，不是 ZCode 断言失败，没有修改检查运行器来绕过。该组另外两项 `test_teardown_preserves_evidence_when_a_supervisor_survives` 与 `test_teardown_preserves_evidence_when_a_daemon_survives` 虽显示通过，也有同样的观察问题，不能把它们当成在本环境完整验证了收尾；三项都交 Host 无沙盒重跑。首次 32 项的输出保留在 `tmp/adr030-batch3c-final-tests2.log`，后续 29 项只排除了这三项，没有更改源码或测试。

Host 仍需完整运行上述启动器/IPC 用例，以及 `cli.test_checks_cleanup.PrivateRootTeardownTests` 全类和 `cli.test_support_cleanup.FixtureCleanupTests.test_cleanup_stops_cli_started_replacement_after_restart`、`test_private_state_context_stops_its_cli_started_service`；本回合没有尝试需要套接字、共享内存或真实角色启动器的用例。请用本说明的最终探针复核取消标题覆盖后的握手，完成原任务规定的真实回合、续接、停止故障注入与整合后的完整检查，再记录验收结论；遥测与运行偏好的归类决定已经落实，共享环境白名单、模型目录/计费共享部分和角色代码不需要本批改动。

本次标题更正后的验证（2026-10-12）：可运行集合 158 项通过，第一组 139 项（36.162 秒，退出码 0），第二组完整的 `buddy.harnesses.test_runtime_version` 19 项（16.968 秒，退出码 0）；原始输出在 `tmp/adr030-batch3c-title-tests.log`、`tmp/adr030-batch3c-title-version-tests.log`，第一组的精确选择在 `tmp/adr030-batch3c-title-test-selection.txt`。第一组覆盖第三回合的 127 项集合，另完整运行 hooks 文件的 `RedactionTests`、`EnvironmentIsolationTests`、`SnapshotTests` 与 `HookAssemblyTests`；其中新增的 fixture 隔离回归没有启动真实 CLI。文中更新后的探针 Python 语法通过，源码和问询探针的出站建会话参数不含标题常量，`git diff --check` 通过。

本次未运行的新增待验项是上文明确命名的两条 `FreeProbePipelineTests` 真实 CLI 用例；`InstalledZcodeTests` 及其他已列明的角色启动器/IPC 用例也仍交 Host，在最终标题默认行为上重跑。没有执行真实 ZCode、没有模型调用，没有新增标题事件处理，也没有触碰共享角色、发现白名单或 DSH 代码；请 Host 在最终源码上检查标题生成时的 Worker/无工具真实回合、更新后握手探针、真实 CLI 测试隔离与完整检查，再作验收。

## 本次 Host 打回：两处断言与标题证据清理

Host 在 `0a1c05d4` 上报告开发者批准的三次真实调用均正常结束：经角色控制器、用户真实主目录的可写回合、同一原生会话的续接和无工具调用；删除标题参数后无工具路径不受影响，启动前后用户供应方文件未改写。Host 的合并树完整检查为 217 个文件中 215 个通过，剩余两处是本批改动后没有更新的测试断言；这些是 Host 提供的实测结果，本实现方没有执行真实调用。

本次给 `WorkerSeamTests.test_a_failed_close_keeps_the_observed_completion_tool_facts` 的允许证据集合加入 `native-runtime-preferences`，保持失败关闭不能发布角色记录的其余断言；给 `FastRegisteredRunTests.test_fast_consumes_public_frames_and_corrects_on_the_registered_process` 删除标题字段期望。仓库检索未找到运行代码单独读取 `titleGenerationEnabled`：`run_execution._fast_result` 与 `router` 只转发整个 `nativeEvidence`，因此删除 ZCode `native_evidence` 中这项旧标题覆盖投影，并同步更新该文件的 `NativeEvidenceProjectionTests`；保留事件数、工具清单、EOF 和原生身份的既有投影，不改共享角色实现。

Host 此次以私有主目录跑完整检查，结束后在那个继承主目录下发现 `.zcode/v2/runtime/provider`，说明真实 ZCode 测试即使使用自己的供应方文件，仍可能把原生运行状态写到继承来的主目录；用用户主目录跑时会写入用户 `~/.zcode/v2/runtime/`。此前静态环境检查不构成“原有隔离成立”的证明；本次已经修正上文结论，不追认那些测试完全隔离，也不猜测是哪条测试或哪项原生路径导致。按 Host 明确分工，测试子进程主目录私有化的通用防线由 DSH 微任务修改检查运行器，本批不另改运行器、安装测试或启动环境。

本回合可执行检查 21 项通过（10.884 秒，退出码 0）：`buddy.roles.test_registered_run_wiring.NativeEvidenceProjectionTests`、`RegisteredDescriptionTests` 与 `buddy.harnesses.zcode.test_native_run.FastSeamTests` 全类；原始输出在忽略目录 `tmp/adr030-batch3c-evidence-tests.log`，使用同一只读解释器和前述清理后的测试环境。Host 指定的 `buddy.harnesses.zcode.test_native_run.WorkerSeamTests.test_a_failed_close_keeps_the_observed_completion_tool_facts` 与 `buddy.roles.test_registered_run_wiring.FastRegisteredRunTests.test_fast_consumes_public_frames_and_corrects_on_the_registered_process` 本回合未运行，按 Host 的真实失败输出更新断言，等待 Host 复跑及整合后的完整检查；没有再次执行真实 CLI、模型调用或服务启动。
