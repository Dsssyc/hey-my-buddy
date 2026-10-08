# ADR-025 第五步微任务 5-C4：黑板 inquiry 测试迁移证据

本次续 run 已移除测试对退休 backend 的依赖。2026-10-07，最终本模块在 Host 固定 `host-final-live-public-snapshot2` 的全新私有源码副本上运行 36 项，全部通过；未变异防护组 5 项全部通过，四处隔离变异均使相同测试变红。未整合基线缺少 C1 reader，模块导入失败，业务测试未执行。本记录只交付授权测试迁移，不是公共整合、原生端到端或宏任务验收结论。

## Scope

比较基线为 `cda461533eca0d9e824fd79c747008ef38d2ae5c`；本次接续系统提供的部分产物 `5e44cbf6b201df2bb7cb86a64f820150f548554f`，没有把它视作已接受成果。唯一写入的 checkout 文件为 `tests/python/blackboard/tasks/test_inquiry.py`、本报告与 [编号对照](adr025-step5-board-inquiry-test-ids.tsv)。未改生产/public/角色/registry/worker.py 或其他微任务文件，未改 ADR/CONTEXT/AGENTS/README/reference/SKILL/Host 指南，未切换或创建分支，未执行 Git commit。由系统封存固定输出。

`<materials2>` 表示 Host 指定私有根中的 `m/c4-codex-20261007-02`；`<materials1>` 为保留的前轮 `m/c4-codex-20261007-01`。所有续 run 实验使用新目录、新脚本、新日志，未删除或覆盖前轮对象或公共 workingtree。原 fixture 正常收尾保留，窄 owner 线程正常 join、Endpoint close，并检查线程存活与消费错误；材料由 Host 验收后按确切根回收。

0 安装版 harness、0 真实模型检查、0 完整检查。未安装或升级日常运行时，未改用户配置、登录、凭据或日常数据，未读取凭据文件内容，未派发其他 Buddy 微任务或使用内部 subagent。依赖缓存从前轮私有缓存复制到新根；以新私有 env 使用 `uv run --frozen --offline`。测试进程清除 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`、`BUDDY_RUNTIME`、`BUDDY_RUNTIME_IDENTITY`、`BUDDY_WORKER_STATE`、`BUDDY_WORKER_ID`、`BUDDY_AGENT_CREDENTIAL`、`BUDDY_AGENT_CREDENTIAL_FILE`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`，再设置 Host 指定的 `TMPDIR`/`BUDDY_CHECKS_TMPDIR` 私有 `t` 和新 uv env/cache；准确 argv、环境键在各 command JSON 中。

公共依赖来自 `<Host snapshot>/host-final-live-public-snapshot2`，Host 提供其公共接线 commit 为 `5776a9b`，这不是整步验收凭据。manifest SHA256 为 `e2481a3b1e03479865e5bcbfd5d2e42eb91683ca6c2ceaea304551152c4294b7`。每个 public/guard/fault 副本从指定基线 archive 取测试与项目材料，排除基线 `src`，再只写入 manifest 声明的 153 个固定 Host 源文件，并逐文件核对 SHA；没有复制旧 backend、sidecar 或 raw transport。`snapshot-sha256-01.json` 和 `test-id-audit-01.json` 保存全部 153 个依赖 SHA 与源码集合完全相等的证明，以下列出直接 seam 依赖。

| 固定文件 | SHA256 |
| --- | --- |
| `src/hey_my_buddy/blackboard/service/service.py` | `a9f69bf327c4a1ca05ca837a93d953fd83a9811d610f7ecedd28d0d5d1a0edd9` |
| `src/hey_my_buddy/blackboard/service/live_registry.py` | `4cbac05bc9b21feedc78449becfc0debe17eb93e8abfdc86301046f028759cb7` |
| `src/hey_my_buddy/blackboard/tasks/inquiry.py` | `6e08591164c07b3596233d9fccdcf9603dce13a5b307155770597200dfc369e3` |
| `src/hey_my_buddy/buddy/harnesses/registry.py` | `f189a268f47fdc0b3bdfae346c2cf3eb9c2dd90784ef0fcdf6873b80e3bf587b` |
| `src/hey_my_buddy/buddy/harnesses/live.py` | `a79ac6f9ef40134f21d2ab5e8cb1e19e87a14fd33109395d57a98c6f6b0a170c` |
| `src/hey_my_buddy/buddy/harnesses/c_two_live.py` | `23658ac0d5c76d9848a9350e4915581a77692ab8056f0fb6d83c2bc625057ec1` |
| `src/hey_my_buddy/buddy/harnesses/inquiry_bridge.py` | `5bb6c11161311edce23e3f25e4c3e71543270d564b897d2401f734722371771b` |
| `src/hey_my_buddy/protocol/inquiry.py` | `727fbdd6527eef8fd8f488ede50d3db65674dde31aab285c07511d4d072950ac` |
| `src/hey_my_buddy/protocol/contracts.py` | `346dc9e16d63adb2dc838639357a7e11bb63af91ad938e24ab809b99d122c497` |
| `src/hey_my_buddy/protocol/run_identity.py` | `3de83697a8801538bf07b24253966200f08cec3b120069c3077801312a67c4be` |

## 聚焦验证

旧 FakeBridge raw socket 和 RealZcodeLive fixtures 已迁为本模块的 `JournalOwner` 与 `OwnerChannel`。真实私有 BoardService/Store 必须先有生产 `store.live_registry` 初始化，随后注入 LiveRegistry 工厂；真实 named `worker_live_attach`/`worker_live_detach`、`attach`/`channel_for` 有实际生产消费者。注册提供明确实例 identity，claim 明确提供 `worker_instance`；真实 ActualActor、worker、nonce、attempt、generation、instance 一致，错误 actor 或身份 attach 不替换 held binding，错误 detach instance 被拒绝。注册 API 无独立 workerInstance 参数，不能把其 display identity 当作 authoritative claim instance。

RunIdentity 的 turnId 与 inputSha256 取自真实 governed claim，核对真实 `workflow_turns.input_json` 和 `input_hash(claim.turn.input)`；运行中的 receipt-time input_sha256 为 NULL 不阻止合法注册，也不用于捏造 turn。invocation 是 fixture 持有者标识。整个 inquiry、actor 验证和公共投影函数没有被 mock；唯一 capability patch 保留原 observe-only 声明测试。

OwnerChannel 仅隔开 `_connect_and_call` 的 Worker/network 边界；实际 CTwoLiveChannel 对 capabilities/request/observe 三项 named RPC 编码共享 Wire，Endpoint 验证 token、instance 和全部六个 RunIdentity 字段。owner 线程真实 `consume_request`，append/flush/fsync 私有 journal 后才 settle；来源读取直接复用 C1 `read_inquiry_journal`，不放宽其 version/task/attempt/generation/turn 守卫，使用纯 `_journal_states`（内部真实 `_record_facts`）与严格 LiveJournal/LiveObservation，经 Endpoint `publish_journal`、`publish_inquiry_state`、`publish_observation` 公开事实。只有 Endpoint 负责序号与分页；没有另造一个旧 live backend 或兼容入口。

恶意或丢失的 observation 经严格 DTO 拒绝，发布 `observed=false, reason=observation-unavailable, error=null` 并清除旧 session 元数据；独立 journal availability 仍按真实 reader 事实发布。目录占据 journal 路径产生真实 read/open 故障，超限文件产生真实 bounded-reader 拒绝；不能由这些故障推导停机或答案。live source reader 保留合法行的答复来源连续性，最终 foreign 行拒绝整个 id 的早先合法来源，合法新行则清除旧 foreign 拒绝；torn 行忽略。C4-036 新增 version/task/attempt/generation/turn 逐项拒绝与公开 observed/reason/error、pending/no-shutdown 断言。

| 实验 | 实际结果 | `<materials2>` 原始材料 |
| --- | --- | --- |
| 固定 snapshot2 本模块 | 36 项全部通过；exit 0 | `public-copy-01-command-01.json`、`public-copy-01-log-01.txt`、`public-copy-01-result-01.json` |
| 未整合基线 + 同一最终模块 | 缺少 `read_inquiry_journal`，导入失败；loader 1 error；exit 1；36 项业务测试未执行 | `baseline-copy-01-command-01.json`、`baseline-copy-01-log-01.txt`、`baseline-copy-01-result-01.json` |
| 未变异防护组 | C4-003/024/015/036/034，5 项全部通过；exit 0 | `guard-green-command-01.json`、`guard-green-log-01.txt`、`guard-green-result-01.json` |

最终测试源码 SHA256 为 `f4f7cea3642a28eaf61bb57b2ad3786b7302d34d1a88ee99b9ffb3974ecebdbb`；与 public、baseline、guard 及全部四个 fault 副本完全一致。public 日志 SHA256 为 `32c87ef2b5dafed2b8d580e46d0cecd8b4f1f631ffe92b6fd45929119c95ecee`，baseline 日志 SHA256 为 `26fc28d4eb312aa0bf4bde871824fa1c56efe23a65f016d65f2ebe3feae05157`。未整合基线的 import error 是依赖缺席，不伪装为 36 项业务断言失败或通过。

前轮 `<materials1>` 的 `public-copy-05` 是 35 项/33 pass、2 fail，`baseline-copy-02` 是 35 项/4 pass、31 fail，三个变异日志与固定 patch 均保留；对应历史结果也保留在 TSV 的 prior 列。Host 告知其 `phase/review-5c4-integrated1.log` 只因 ExistingLiveChannel import 失败，未执行本模块业务断言；本次未修改该 Host 日志，也不将其记为新的业务红测。Host 另告知前轮 native-shutdown-failed 后最终两层停止 true；该失败保持记录，本次没有重新验证，不能称前轮正常交付。

## 变异

故障只写新的隔离副本，原快照和 checkout 生产源码均未修改。准确 patch、前后源码 SHA、测试 SHA、编号、argv、exit、完整日志和日志 SHA 存在 `<materials2>` 各 `*-fault-01.patch`、`*-command-01.json`、`*-result-01.json` 与 `mutation-results-01.json`；未变异同组先通过，未恢复或覆盖故障副本。

| 变异 | 相同编号 | 实际红测与日志 |
| --- | --- | --- |
| Endpoint 移除 token 校验 | C4-003 | 1 项/1 failure；wrong token 的 reason 变为 null；`token-log-01.txt` |
| 公共 importer 仅消费第一页 | C4-024 | 1 项/1 failure；最后一页 id 保持 queued；`paging-log-01.txt` |
| C1 reader 移除全部 source/version 守卫 | C4-015、C4-036 | 2 项/6 errors；应有的 journalRejected 缺失；`live-source-log-01.txt` |
| terminal reader 放行 foreign task/attempt | C4-034 | 1 项/2 errors；两个应有的 journalRejected 缺失；`terminal-source-log-01.txt` |

source 变异的 KeyError 是拒绝事实丢失触发的真实红测，不称为 assertion failure；它与 token/分页的 assertion failure 分开记录。live source 变异覆盖本次实际调用的 C1 reader 全部现有 guard；terminal source 变异独立覆盖公共 terminal durable history，不把其中一层通过推导成另一层通过。

## 编号与语义差异

[TSV](adr025-step5-board-inquiry-test-ids.tsv) 保留原 29 个编号，25 个原名字、4 个重命名、7 个新增，总计 36 项。每行列出原意、语义差异、当前 snapshot2 结果、未整合基线导入边界、前轮 snapshot1/基线结果、测试体 SHA 与 fixture 层次；没有静默移除旧编号。`test-id-audit-01.json` 由真实基线 `git show` 和当前 AST/source 生成集合证明。原测试体逐字未改集合仍为 `C4-007, C4-009, C4-011, C4-012, C4-016, C4-023, C4-027, C4-029`；另 C4-017 只改注释、断言 AST 未改。共享 fixture 变化影响这些编号，测试体未改不表示执行层次未改。

旧 unauthorized 对应真实 C-Two `token-mismatch`，完整身份拒绝为 `identity-mismatch:taskId/attemptId/generation/invocationId/turnId/inputSha256`，实例拒绝为 `instance-mismatch`，失联为 `transport-unreachable`；这些实际公共拒绝保存在 bridge.reason，bridge.error 为 null。owner 明确报告的 agent-gone、journal-unavailable code 另保留，不由失联推断。source reader 的 foreign task/attempt 原因仍为 `the journal record belongs to another task/attempt`，version/generation/turn 拒绝为实际 `the journal record is not bound to this execution`，没有编造更具体的运输拒绝码。

旧直接 socket not-ready 测试退休为 C4-018：point query 成功但 id 尚未发布，`observed=true, inquiries=[]`，只表示未发布，不证明原生无答案、不改变黑板 pending。C4-032 另覆盖 wait 不造答案或新 turn，attempt/turn 各一条；queued 从不当作 delivered。C4-025 不调用退休 discard 入口，以 fixture durable source 的 discarded 事实经 Endpoint 回放，保留 reason；C4-026 保留 limitation 优先。

旧 service 读取 stored request 并拒绝 tamper 的 C4-020/021 改为缺席注册、ActualActor/instance/nonce/generation/当前 turn/digest 不匹配和 held binding 拒绝。服务不读取原生 request/control/inquiry credentials 文件、不直接连接 controller、不对 live run 回退读 journal；合法绑定后磁盘 request 变化不称执行身份改变。holder 原生材料验证属于另线 role-live seam，本次不代称该边界已运行。Host snapshot2 的共同 C2 factory 修正后，C4-008/020 原断言通过，未补兼容入口或改公共源码。

## 边界

本模块验证真实 Store/Actor/当前 governed turn、Registry 绑定、共享 Wire/Endpoint admission、真实 owner 消费/持久写入/settle、C1 source reader、纯投影、公共事实和相称失败。它不是原生端到端验证：实际 Worker holder/controller 转发、C-Two server 注册与网络/跨进程、原生 root checkpoint/签名 receipt、PID/material-link/FIFO/超大原生素材验证被明确隔开。Host 六项真实跨进程验证属另线，本次未运行，不能用本记录代替。

checkpoint/reply-tool 文本、metadata、limitation 是明确 fixture 来源事实，证明公开投影保留来源，不证明原生工具已执行或原生 receipt 已验证。C4-010/031/032/033/035/036 保持无源/失联/拒绝不能形成 shutdown 或答案；C4-034 在 fixture 唯一 execution 线程真正 join 后才提交 worker_result，验证 terminal 只读本 attempt durable history、foreign task/attempt 拒绝、不发 live RPC、不启动新 turn。这是 fixture 的实际停止证据，不是安装版 harness 的停止证据。

## 接口缺口与固定交付

前轮公共缺席注册 gate 与退休 backend import 两个问题在本次指定 snapshot2 路径下不再阻塞本模块；未整合基线仍缺 C1 reader，已如实保存导入失败。授权的 pure DTO/journal/Endpoint fixture 方案没有待 Host 补的 owner 接口缺口。本次没有实例化 InquiryBridge 或验证其 native lifecycle；若以后扩大到原生 owner/receipt/Worker 网络，需要另行固定范围与验证材料，不能由这 36 项推断已完成。

固定交付为授权三路径、本报告/编号/SHA、私有原始命令日志与四个故障 patch。系统可封存固定 outputCommit；本微任务到此停止，不运行完整检查，不执行额外安装、模型、公共修改或 Git commit。
