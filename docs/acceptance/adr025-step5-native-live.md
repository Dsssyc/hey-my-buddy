# ADR-025 第五步微任务 5-C2：native activity producer 迁移

本记录对应基线 `93a764807822b01fb7511119568b83a51b423db3` 上的固定交付。仅修改四个 harness 的 `native_run.py`、各自 `test_native_run.py` 和本记录/编号表，公共值、角色、注册表、protocol 与服务接线未改。代码固定 diff 的 SHA-256 为 `7776629ddfb9afa3ea064e0eac4d362a67d84177c8340c9d6a70f8da864f7cca`，原始 patch 与逐文件哈希在 `<task-root>/m/delivery-1lrcp69s/`。未创建或切换分支，未写 Git 管理目录；固定源码由受管 checkout 交付，Git 提交、公共整合及验收交 Host。

## 实际改动与行为边界

- Claude 从只有 activity 路径的 `BoundRunPaths` 改为 `RunServices(live=None)`；Codex 的 `RunServices`、ZCode/DSH 的 `SessionServices` 都增加实际消费的 `live` 可选字段。四者复用 controller 提供的同一个 `c_two_live.CTwoLiveEndpoint`，未实现第二套生产 transport 或角色分支。角色用 `dataclasses.replace` 注入 endpoint 属于 Host 尚未整合的部分；聚焦 fixture 已以真实 endpoint 注入验证字段消费。
- 四个 native producer 删除 `ActivitySidecar`、activity 文件写入、`activity_dir` 服务字段及各自的 `bind_live_channel` 入口/说明/导出。Claude/ZCode/DSH 使用 `ActivityPublisher(live.publish_activity if live else None)`，保留公共真实规范化、单调检查和节流；`RunResult.activity` 来自原生 collector/projection，而非传输成功回执。无 endpoint、callback 返回 False 时仍保留原生终态，拒绝发布不会成为传输成功证据。
- Codex 保留自己的原生计数、工具元数据与 2 秒本地节流，最终送出改为真实 `publish_activity`。共享 Publisher 设 `min_interval_seconds=0`，只复用规范化和单调检查，避免第二次节流改变行为。实际 `readonly-repair` 两轮 fake native 流证明：默认共享节流会使 endpoint 的 `modelTurns` 停在 1，而最终事实为 2；保留单层原有节流后两者都是 2。未改变原生每轮事件序号、修正轮计数基数或阶段结构。
- DSH 复用已有 `DshActivity` 投影补充本次要求的 `RunResult.activity`；只有真实 prompt 计数或原生事件已推进时才提供，pre-spawn 失败没有伪造的 starting 活动。原异常、完成工具签名与停止口径保留。
- ZCode/DSH 的 `make_inquiry_bridge` 将 `services.live` 作为 `InquiryBridge(live=...)` 实参传递；native 输入只需要原有 journal/签名材料，不读取 socket/token 运输字段。DSH native inquiry fixture 实际只传 `resultsPath`/`errorPath`；签名仍由已有 MCP mount/receipt 契约提供。基线公共 bridge 暂不接受 `live`，构造传参用 focused mock 验证，真实集成未验证。
- ZCode 的已拆 `_run` owner loop AST 与基线完全相等（`m/delivery-1lrcp69s/stage-proof.json`）；活动和问询改动留在既有 governed 阶段，没有重新膨胀 owner loop。其他三个 harness 沿用原阶段结构。

生产机制复用现有 `ActivityPublisher`、原生事实投影、共享 C-Two endpoint、严格公共模型和 session receipt/journal。保留 Codex 自有小段是因为它的计数与节流语义不同，不能照搬通用节流。手写新增支持仅在测试：基线公共 bridge 尚未迁移且沙盒禁止 socket bind，fixture 将 socket 挂载 mock 为实际 journal 读取，把传输边界 mock 到实际 owner handler，并用标准库线程/Event 驱动共享 endpoint 的 consume/settle；这不是新增生产 transport。

## 剩余工厂签名

| harness | 工厂 | 实际剩余参数 |
| --- | --- | --- |
| Claude | `prepare_run_services()` | 无参数；返回默认 live=None |
| Codex | `prepare_run_services(*, account, tool_scope="write")` | frozen account 与原有凭据来源选择实际消费 |
| ZCode/DSH | `prepare_services(*, invocation_root, identity, input_sha256, attention_path, session_tools, completion_tool, validate_outcome, inquiry, inquiry_tools, native_stderr)` | mount/校验/问询材料及 stderr mirror 均有消费 |

Codex 删除无作用的 `invocation_root`、`native_root`、`activity_dir` 工厂参数；Claude 删除原来所有路径/account/scope 参数；ZCode/DSH 删除 `activity_dir`。Host 应用成熟 `inspect.signature` 筛选自己的工厂投影，再对实际服务 dataclass 注入 controller endpoint；本微任务未进行厂商 SDK 静态证明。

## 实际验证

只运行受影响的四个 `test_native_run` 模块及其中的针对性用例，没有运行完整检查或其他测试树。最终输出逐文件确认：Codex 42、Claude 52、ZCode 42、DSH 61，合计 **197 项 OK**，无跳过。命令均为 `uv run --frozen --offline --no-sync python -m unittest -v buddy.harnesses.<harness>.test_native_run`；每文件独立私有 subprocess。

| 最终模块 | 原始日志（相对任务根） | 结果 |
| --- | --- | --- |
| Codex | `m/verification-9bijp276/codex-final/codex.log` | 42 OK |
| Claude | `m/verification-9bijp276/claude-final/claude.log` | 52 OK |
| ZCode | `m/verification-9bijp276/zcode-dsh-final/zcode.log` | 42 OK |
| DSH | `m/verification-9bijp276/zcode-dsh-final/dsh.log` | 61 OK |

测试覆盖原生 fake event 到实际 endpoint callback 的阶段、计数、顺序及正常最终值；无 endpoint 与发布拒绝保持原生最终事实；Claude/Codex 的未知事件/工具、ZCode post-configure 失败、DSH forged/tampered receipt 及原 native 停止未知、取消、异常测试仍执行。新 callback 测试调用生产 `publish_activity` 并验证严格规范化/排序；旧 ZCode 11 项 live 编号保留 journal、receipt、metadata、预算、refusal 和 point-query 断言，经真实共享 endpoint/channel 合同送出，只有公共 bridge 运输边界明确 mock。没有启动 C-Two server，未声称本轮验证跨进程运输、公共 5-C1 集成或安装版生产使用。

环境固定：`TMPDIR` 和 `BUDDY_CHECKS_TMPDIR` 均为 `<task-root>/t`；各测试 subprocess 清除继承的 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`、`BUDDY_RUNTIME`、`BUDDY_RUNTIME_IDENTITY`、`BUDDY_WORKER_STATE`、`BUDDY_WORKER_ID`、`BUDDY_AGENT_CREDENTIAL`、`BUDDY_AGENT_CREDENTIAL_FILE`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`，再设置本轮私有 state/runtime 与私有 venv。DSH 启动测试具有私有 `DSH_HOME`；普通 HOME 仅由原来明确使用私有 HOME 的 fixture 修改。`m/verification-9bijp276/focused_v3.py` 与各 `boundary.json` 保存实际命令和环境边界。全程零真实模型调用，四个模块只启动原有 Python fake native 程序；没有安装版付费冒烟，没有安装/升级日常运行时、修改日常用户配置/凭据/数据/登录或读取真实凭据内容。

离线环境准备有两次已保留的失败：初次 uv 缓存路径不可写，改用任务根私有缓存；`uv sync --frozen --offline` 未解析到已有 numpy wheel，随后用标准库 zipfile 从已有解包缓存恢复本地安装包，再用 `uv pip install --offline --no-deps` 装入全新私有 venv。初选 C-Two 缓存是 editable 包，首次新版测试导入因 dylib 缺失失败；最终改用包含真实 `c_two` 包的 wheel 缓存，在另一个全新私有 venv 安装，未修改旧环境或 SDK。最终核对版本为 c-two 0.6.0、pydantic 2.13.5、portalocker 3.2.0、zstandard 0.25.0、numpy 2.5.3、fastdb4py 0.2.1；恢复安装包不声称其压缩字节哈希等于远端 wheel。初次导入失败日志在 `m/verification-9bijp276/codex-claude-green/`（目录名不代表结果，实际两文件 ERROR）。

基线四模块也实际运行并留原始日志 `m/verification-9bijp276/baseline-green/`（目录名不代表全绿）：Codex 37 OK、Claude 49 OK、ZCode 37 中 5 FAIL、DSH 58 中 4 FAIL。失败均为旧 socket inquiry 路径无法 bind；单独私有 AF_UNIX bind 探测返回 EPERM，未申请越权。迁移后在明确的公共 bridge mock 边界运行这些原断言，不把基线红日志伪称全绿。初版新 Claude 两项阶段断言漏掉真实 system/init 的 starting，红日志在 `focused-pristine/claude.log`；修正测试断言后 52 OK，未改原生行为。

## 编号迁移与故障防护

`adr025-step5-native-live-test-ids.tsv` 只列变更/新增，删除 0。真实 unittest 加载收集与 AST 方法集合相符：基线 181 → 最终 197；15 项编号迁移、6 项同编号方法修改、16 项新增。剩余 160 项两侧集合相等，且方法 AST 相等（Codex 35、Claude 46、ZCode 25、DSH 54）。证明在 `m/final-numbering-q9049hsu/{before-ids,after-ids,unchanged-before,unchanged-after}.txt`、`summary.json` 和实际收集日志；未变集合文本 SHA-256 两侧均为 `03ad73669bf2df1e5d0590aa09b126e468f27f03344820d66b97296730083552`。Inquiry fixture 支持层改变单独披露，不将方法 body 相等描述为支持层没有变化。

少量真实隔离单点变异均原件 exit 0、变异 exit 1，红色原因是行为 AssertionError（非导入/环境错误）；只在任务根材料副本变异，未修改交付源：M1 断开 Codex activity callback，真实 endpoint callback 用例失败；M2 丢弃 Codex `model_turns_base`，实际 native event 计数断言从 2 变 0；M3 断开 DSH callback，真实 governed event 阶段断言失败。最终 Codex 的 M1/M2 原始绿/红日志和变异源码在 `m/final-mutations-8t_fp_ik/`；DSH M3 在 `m/mutations-qkp1p2lu/M3-dsh-disconnect/`（对应 DSH 文件此后未变）。早期三项副本及证据同样保留，不覆盖。

R1 是额外的真实修复前/后回归证据：`m/codex-correction-102lcf0f/red.log` 断言 endpoint 与第二轮最终事实相等失败（计数 1 对 2），`green.log` 修复后 OK；最后重新运行 Codex 全文件 42 OK。公共规范化与单调检查保留，节流仍来自原 Codex writer，没有改变原生序号契约。

## 无生产使用项与接口缺口（交 Host）

1. 四个 `live` 字段都有本次实际 native callback 消费，但基线角色没有提供 controller endpoint；本交付不称已生产启用。controller endpoint 生命周期/凭据/公共合同由 Host 接线，并用 `dataclasses.replace` 注入实际 RunServices/SessionServices。
2. `roles/run_execution.py` 仍给四个工厂传旧参数，ZCode/DSH 的 `prepare_services` 调用仍带 `activity_dir`；需要按上表用 `inspect.signature` 筛选，不能在本交付外保留静默吞参 facade。
3. `registry.live_binding` 与 `roles/live.py` 仍寻找已删除的 native `bind_live_channel`，并有旧 activity 文件来源；Host 改为公共 endpoint。`roles/run_execution.py` 的 `sidecarWritten` 事实仍在授权范围外，应由 Host 删除或改为本次实际 live 证据，不增加无人消费的字段。
4. 公共 `InquiryBridge.__init__` 基线不接受 `live`，直接真实 inquiry 集成会 TypeError；5-C1 需接受 endpoint、从 owner loop consume/settle，并发布实际 inquiry/journal/observation 事实。native 构造传参已验证，公共集成待 Host。`inquiry_paths` 中 socket/token 运输材料同样待 Host 移除，MCP journal/签名材料保留。
5. 旧 producer 对不存在的问题 point query 返回 `observed=False, reason=bridge-refused, error=not-ready`；当前共享 endpoint 没有该已发布 entry 时返回 `observed=True` 与空 inquiries。迁移测试分别保留这两层的真实断言，未宣称全字段等价；Host 需决定统一公共失败/已知缺席的投影口径。
6. DSH/ZCode 原生 protocol 投影的历史 sidecar 说明仍在范围外；本任务未改公共 protocol。旧公共 `ActivitySidecar`/bridge binding 也在范围外，删除/整理由 Host 负责。

工厂已有字段除删除的死路径外均实际消费；新 live 字段、DSH activity 投影包与测试支持均有使用方。没有新增生产 transport、角色名称分支或无人消费的事实字段；没有宣称 reviewer/helper 已验收。以上范围外接线与公共语义决定均在固定交付后交 Host，Worker 到此停下。

## 任务根与收尾

任务根为 `<task-root>`，确切根通过本轮结构化交付摘要给 Host：`t/` 是固定测试临时根，`m/` 各新目录保存私有 venv/cache、基线副本、命令脚本、原始绿/红日志、编号证明、隔离变异与固定 diff/hash。Worker 未执行删除或回收，未覆盖原始证据；原有 fixture 的正常私有收尾保持，其他会话物品未动。Host 验收后仅按登记的确切任务根回收。
