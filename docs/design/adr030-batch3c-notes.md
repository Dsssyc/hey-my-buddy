# ADR-030 批次 3c：ZCode 供应方配置与回合事实

本说明对应输入提交 `71f0a3f6`，仅是实现方的说明与验证记录，等待 Host 验收；不是验收结论。版本与 schema 未变，没有改 Claude Code、Codex、DSH、角色代码、共享发现的环境白名单或共享计费规则，没有启动服务、Worker 或真实 ZCode，没有读用户自己的配置或凭据，没有模型调用。

## 删除与保留

`zcode/config.py` 的 `snapshot_provider_files` 与 `SUPPORTED_ACCESS` 删除。启动不再生成 `builtin-provider.json`、`personal-provider.json`，不再设置 `ZCODE_BUILTIN_PROVIDER_CONFIG_FILE`、`ZCODE_PERSONAL_PROVIDER_CONFIG_FILE`，也不再单独剔除 `ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE`。子进程仍使用 ADR-017/030 的共用环境白名单：没有新增供应方环境变量的透传。ZCode 加载时可能规范化并写回用户的两个供应方配置文件；现在这是 ZCode 自己对用户配置的行为，照原生做法运行。

`ZcodeAdapter.available` 和 `check_preparation` 只检查选定的原生命令，不再读取供应方文件或按登录方式拒绝。命令不存在、缺少解释器或真实启动失败仍然失败。`configure_session` 删除访问类型参数与限制，仍核对所请求的供应方、模型、推理强度在原生清单里，调用原生 setter，并核对实际读回的选择。原生因文件读不到、登录失败或配置不可用而拒绝 RPC 的，继续作为真实原生失败上报；可选计费元数据读不到不会阻止原生启动。

模型目录不再按访问类型筛选，不再声称 OAuth 供应方不可用。原生报告的非密钥供应方照常进入目录，能经同一配置路径选择；是否能运行由真实回合决定。`interaction/requestProviderRuntimeHeaders` 的供应方专属硬错误删除，按现有的未实现宿主请求处理：记录交互事实、发送 JSON-RPC 不支持答复，继续处理原生事件。没有伪造认证头、登录或认证宿主；无工具角色对交互事实的停止规则未改，属于后续批次。

发现路径写出的 `.zcode/config.json`（关闭 plugins、MCP、memory、skill、subagent）删除，因为它替用户挑选配置。独立的 `tests/probes/zcode_request_input.py` 原来直接引用被删的快照函数，也删除复制与访问类型限制及相应测试假设；它仍是需 Host 授权的问询实验，不是本批的无模型验证方法。

### 启动环境逐项

| 项目 | 处理 | 依据与边界 |
| --- | --- | --- |
| `ZCODE_STORAGE_DIR` | 保留，指向微任务私有 native 根下的 `storage` | 原生会话存放处，ADR-030 第 3 条 |
| `ZCODE_SESSION_DB_PATH` | 保留，指向同一根下的 `sessions.sqlite` | 会话续接；绑定与精确身份校验未改 |
| `ZCODE_LOG_DIR` | 保留，指向本次 invocation 的 `native-logs` | 原生证据的位置 |
| `ZCODE_LOG_CONSOLE=0` | 保留 | stdout 是 JSON-RPC 传输，日志走独立证据路径 |
| `ZCODE_MODEL_TELEMETRY_ENABLED=0` | 暂保留，交 Host 定 | 旧理由是避免用户主目录里的遥测状态写入；它同时选择了原生遥测行为，不能仅凭旧理由确认属于证据位置或权限 |
| `ZCODE_DATA_BASE_DIR` | 沿用共用白名单，不改值 | 原生用户配置位置，计费读取使用同一位置 |
| 两个供应方文件变量 | 不设置、不复制，也不为计费读取使用它们 | 原生使用自己的文件，shell 里的覆盖不属于共用白名单 |
| bundled 供应方文件变量 | 删除专门的剔除逻辑 | 不另替原生选择 bundled 文件；共用白名单未变 |
| `BUDDY_ZCODE_TEST_CASE` | 沿用仅开发测试时的透传 | 模拟 CLI 用例选择，不属于用户供应方设置 |
| `BUDDY_ZCODE_CLI` / `BUDDY_NODE` | 沿用开发命令选择，不传给原生 | 安装后的命令仍来自服务选定的运行时 |
| locale、代理、CA 路径与其他共用白名单项 | 未改 | 不在本批改共享发现规则 |

会话请求的 `yolo` 保留为当前 Worker 权限；快速调用的空工具清单、空会话 MCP、off-peak 与 dynamic workflow 关闭保留为无工具范围的表达。`titleGenerationEnabled=False` 保留，避免任务以外的标题模型调用。反向 `session/requestRuntimePreferences` 的现有答复未改：搜索能力随工具范围，自动回答用户问题关闭；其中 `memoryEnabled=False` 与 `modelContextBudgetStrategy=preflight-v1` 的原生含义没有在本批验证，应由 Host 判断是否也属于替用户选择配置，不能由本批宣称用户全部定制都已生效。

### 文件逐项

| 文件 | 处理 | 用途 |
| --- | --- | --- |
| 两个供应方配置副本 | 删除生成代码与旧快照 API | 含凭据的副本不再需要 |
| 发现 cwd 的 `.zcode/config.json` | 删除生成代码 | 原来关闭用户特性 |
| `finish-bridge.json` | 保留 | 本次私有会话工具的身份、输入摘要、签名密钥与契约绑定，不是供应方配置 |
| native 根中的 `<sessionHash>.json` | 保留 | `{taskId,sessionId,cwd,configuration}` 的续接绑定，字段与位置不变 |
| `sessions.sqlite` 与 `storage/` | 保留 | 原生私有会话数据，不是私有配置主目录 |
| `native.stderr.log`、`native-logs/`、`run-end-facts.json` | 保留 | 本次原生证据；失败公开投影仍不包含供应方原始错误正文 |
| 共享角色的输入、控制与结果文件 | 未改 | 任务与结果契约不在本批重新设计 |

存储清理、备份排除与私有迁移中对旧供应方副本文件名的识别未动；那是已有存量凭据材料的清理边界，不是运行时继续生成副本或快照 API 的兼容入口。

## 供应方名字与计费

改前目录的供应方名字来自 `settings.model.available[].ref.providerId`，只是经过访问类型筛选；改后仍来自同一字段，删除筛选，`providerLabel` 仍只作显示名称。模型 ID 仍是 `ref.modelId`，推理强度仍是原生 `reasoning.levels[].value`，harness 仍是 `zcode`。原来已被接受的供应方不会改名；同一份配置内容交给同一原生 CLI，四段身份的来源没有变。安装前 Host 仍须把真实目录的四段名字与原有记录逐个比对，模拟 CLI 不是这个真实接口的证明。

没有增加名为 `unknown` 的供应方或向原生 setter 传入伪造名字。ZCode 这条接口报告的是每个可用模型自己的 `ref.providerId`，没有依赖“缺省供应方”的目录分支；用户的默认选择来自原生 `settings.model.current.providerId`，配置核对使用显式请求与原生读回。缺少这些身份事实时，本批没有猜一个通用内置名字；真实 CLI 若出现这样的形状，交 Host 根据原生证据决定。

仅目录发现读取计费元数据，Worker 与无工具回合不再读取供应方文件。内建位置沿用原来的 CLI 相邻布局查找（`config/provider/zcode-builtin.json` 或 `provider/zcode-builtin.json`）；个人位置是原生数据根下的 `.zcode/v2/provider_config.json`。读取使用传给原生的白名单配置位置，不使用被剔除的 shell 供应方文件覆盖。

这是读凭据旁边的配置：JSON 解码会把整个文件的字节读入内存，只提取 `config.providerConfigRules` 下 `templateRules[].templateId`、`providerRules[].providerId`、`providerRules[].templateId` 与各条 `config.access.type`，按原有模板继承和个人覆盖关系标注。没有访问或返回 `apiKey`、令牌、`requestAuth`、请求头、API 地址、providerName、模型规则或其他配置字段，也不落盘原始 JSON。内部的供应方 ID 只用于把计费标注连接到原生清单，不能生成目录供应方。

只输出已知的 `api-key`（按量）、`zhipu-coding-plan-api-key`（套餐）或未知；文件缺失、拒绝读取、JSON 损坏、不能解析的结构与其他访问方式都标未知，不拒绝目录。这是原生配置的计费标注，不证明一次请求实际扣了资源包还是余额；没有查询厂商账户接口或根据消息文字推断扣费。

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
    params = {"titleGenerationEnabled": False}
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
        print(json.dumps({"mode": mode, "version": spawn.version, "names": names}, ensure_ascii=False))
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

发现应返回正常用户配置中的供应方/模型/强度，不发提示词；Worker 与无工具的两个 setter 应读回 `wanted`，会话 workspacePath 应精确等于带点号、空格与非 ASCII 字符的 cwd，三个原生进程都应正常退出并确认组停止。用 Host 的进程环境观察工具另外核对：两个供应方文件变量不存在，storage、session DB 与 log 变量指向本次私有目录，用户配置根保持原值。不要打印整个环境或整个原生 snapshot。此探针不证明模型调用、MCP 工具执行或登录宿主能力。

审阅调用没有变更过的启动路径：`ZcodeAdapter.read_only_structured=False`，`local_read_only_check()` 应仍返回 `eligible=False`、`readonly-worker-carrier-unimplemented`；审阅在资格边界停下，不启动 CLI。本批没有另造审阅执行载体。续接的模型前握手可在上面 Worker 会话关闭后、同一 storage/DB 环境中新起原生进程，调用 `session/resume` 并核对同一 sessionId、cwd 和 setter 读回；这只验证原生续接接口，完整绑定续接测试仍由下节的 Worker 测试覆盖。

## 实现方验证与交接

2026-10-12，最终源码上可运行集合 119 项通过（30.715 秒，退出码 0），`git diff --check` 通过；文中的 Host 无模型探针仅做了 Python 语法解析，没有执行。使用 Host 指定的只读 Python，`PYTHONPATH=src:tests/python`，清除 AGENTS.md 的运行时、Worker、agent、虚拟环境变量及 `BUDDY_`、`ZCODE_`、`ZAI_`、`ZHIPU_`、`GLM_`、`ANTHROPIC_`、`CLAUDE_CODE_`、`C2_` 前缀，再显式设置 `BUDDY_DEV_SOURCE=1`。`cli/checks.py` 与 `tests/python/support.py` 只按任务要求增加 ZCode 与相关供应方前缀的测试隔离，没有修改产品环境构造；ZCode 的模拟 CLI 测试也清除这些继承变量，并把 HOME、USERPROFILE 与原生数据根设为私有合成目录。

本环境可运行的检查集合是：`buddy.harnesses.zcode.test_native_config`、`test_zcode_protocol`、`test_zcode_request_input_probe` 全文件；`test_native_run.FastSeamTests`、`NoToolReverseRequestTests`、`DiscoveryStopTests`；`test_no_tool_zcode.NoToolPolicyTests`；`test_zcode_tool_evidence.ProjectionTests`、`ProjectionOrderingTests`；`cli.test_checks_cleanup.ChecksEnvironmentTests`；`cli.test_support_cleanup.FixtureCleanupTests.test_child_environment_drops_inherited_ownership_and_credentials`。新测试用私有原生配置路径与带点号、空格、非 ASCII 字符的 CLI/cwd，覆盖非密钥供应方发现与选择、计费未知、没有快照、私有会话位置、失败归因/额度、成功用量与格式纠正的累计用量。

第一轮还尝试了 `test_no_tool_zcode.NoToolFakeProtocolTests` 与 `test_zcode_tool_evidence.NoToolReceiptWiringTests`：所有经共享角色控制器启动的这些用例都未得到有效结果，报告 `invalid-native-result`，stderr 为空，取消用例没有等到模拟 CLI 收到提示词；没有证据把它归因于 ZCode 原生接口，不改共享角色或启动器来绕过。按任务中真实启动器的环境边界交 Host 在验收环境重跑，以上失败不算通过。直接运行模块的新测试不经过这个控制器。

Host 还需跑 `test_zcode.py`、`test_native_run.WorkerSeamTests` 与 `ProducerContractTests`、`test_zcode_native.InstalledZcodeTests`、`test_zcode_checkpoint.py`、`test_zcode_inquiry.py`、`test_zcode_tool_refusals.py`、`buddy.roles.test_registered_run_wiring`、`blackboard.evaluation.test_other_native_observations` 的 ZCode 控制器用例，以及 `cli.test_checks_cleanup`、`cli.test_support_cleanup` 剩余需要私有服务/IPC 的用例；其中已存的精确续接回归要保持通过。完整 Python 检查、真实 Worker/Router 回合与停止故障注入按原任务由 Host 做。实际 CLI 的无模型探针与真实目录名字对照尚未执行；没有前端改动。

交接需 Host 决定遥测开关及上文两项 runtime preferences 的归类，完成剩余测试、无模型握手与原任务规定的真实回合，再记录自己的验收结论；共享环境白名单、模型目录/计费共享部分和角色代码不需要本批改动。
