# ADR-025 第一步：公共格式、外层与角色接缝验收记录

2026-10-05，第一步从用户已验收的 `socu/buddy-core` 基线 `4cf58de` 开始，在 `socu/adr025-run-module` 实施检出整合 1-A/1-B/1-C/1-D；本次最终完整检查输入为 `0719e65919a7f80c4ff0cb4ed50857d0ce18700b`。源代码与测试冻结在该提交，后续只补验收记录。本文提交后停等用户转达 Claude Code Host 的整步验收；独立 DSH ACP 客户端整改另有外部复核边界。

1-A 放置内部冻结 `RunRequest`/`RunResult`、当前结果到事实格式的转换和 `ExistingLiveChannel`；请求、配置、工具、原生身份、用量、来源、双层停止事实只报告已知/未知，角色判定保留。格式是草案，新增 `nativeError` 用于保留已有非额度错误、`InquiryState.seq` 用于更新后的无缺口分页，warnings 是否存在和各帧/载荷边界的选择均在[公共值记录](adr025-step1-values-live.md)说明。32 个问询、传输超时、重复/冲突、显式 unsupported/unavailable 的现行约束保留，ADR-024 的 events 位仍关闭。

1-B 合并日志打开、owned spawn、截止/取消和结果收集的重复外层，保持严格 512 KiB 与普通 256 KiB 读取、DSH 末行与原始无界读取、原生宽限和凭据保留差别。DSH 环境准备失败时日志 FD 的旧行为已由原微任务恢复，Node 运行器及其历史停止例外保留到第四步；详细修正、越界清理披露与撤回在[原记录](adr025-step1-controller.md)、[暂停](adr025-step1-controller-host-hold.md)与[恢复验收](adr025-step1-controller-host-resume.md)，历史未改写。

1-C 放置 Worker/Router 角色控制器、事实观察/services 类型和唯一运行调用点，保留 Worker 认领、监督、续租、回执及 Router 冻结输入、资格与发布判定。角色按原生投影已去重的累计 `toolCalls`、纠正与未知计数观察，重复 start/end 不重复花预算；快速模式工具事实立即请求停止。准备保存实际选中的执行体；资格只确认 run/discover 可调用，登记与请求 harness 显式绑定，不静态证明厂商代码。异常重试原对象/code、唯一选择与调用绑定、累计计数、即时停止和记录问题均由原 run continue 修复，见[角色记录](adr025-step1-roles.md)和[Host 复核](adr025-step1-roles-host.md)。

1-D 核对原有与新增编号、补两项防护及三组故障见证，见[变化表](adr025-step1-test-map.tsv)和[验证记录](adr025-step1-validation.md)。本步没有为角色增加运行通道，也没有新增 C-Two 操作或改变其 CONTRACT_VERSION；worker_live_attach/detach 及按 ADR-007 的版本影响评估留第五步。

## 固定交付与整合

| 微任务 | run / 最后 attempt | 最后 artifact / 输出提交 | 输入 → Host 整合提交 / 凭据 |
| --- | --- | --- | --- |
| 1-A | `c4d264e5-7d8b-465b-a130-521d9e79257c` / `13e66db2-de1d-4110-9f61-d46aeb8a8777` | `97758209-36ba-4634-9f88-8dbec870af7e` / `8a84b02f97710c9469a8b62c5f77ee50486e3906` | `4cf58dee` → `da8c25dc` / `int-292f5fd7-82d8-47af-ae2c-677d3ad866e7` |
| 1-B | `ad9da628-9b48-48b9-97a4-f2d8ae1c0b46` / `a66371bd-ad52-4311-a52e-3e90c897361d` | `9d39ba84-54c0-4dc5-bacd-b58f2c165b14` / `98f1b8c4959ac307b4e6a231c062088afe3c893b` | `da8c25dc` → `4c49bdac` / `int-b0872dbf-bf2f-4325-98d7-abbead36083e` |
| 1-C | `511b3514-bb69-43db-bca3-aafc2a540620` / `e66ec1d6-5ea6-4b1e-83f7-674c7420cbba` | `fdbdc3f4-1e76-4478-85f0-108ff33a2d81` / `2e534f8eb53ab05ba14f7738130def43cf97b0cc` | `f8b42a56` → `a5c3c215` / `int-b57ec708-a565-4619-bd26-ccd5a6081d0c` |
| 1-D | `cc0e12cf-834b-4ec7-bca4-203bd9b4c857` / `56a88349-f478-4dd2-ab90-d2c608bbb9fe` | `6f061d7f-05e9-4d39-8090-75c1553c016a` / `fd0ce3edbfd33ec8a225eb8a983a8ebbe9c34151` | `a5c3c21` → `0492dd8`（测试源码先入 `1de0168`）/ `int-822a8508-853e-4d5d-a5c3-40c73de6b2d8` |

四个微任务的实现按普通路由交给 buddy，未指定 buddy 或配置。1-A、1-B、1-C 分别三个实施回合、两次 continue；1-D 两个实施回合、一次 continue，最后回合只修记录、没有重跑。1-D 的文档 continue 在用户补相称性规则之前已发出；之后 Host 直接更正已掌握的小事实，并在 `int-822a8508-853e-4d5d-a5c3-40c73de6b2d8` 的 reason/verification/adjustedPaths 及[整合登记](adr025-step1-integration.md)写明原因，没有再为同类小更正打回。上述属于微任务内部验收，整步外部验收仍待进行。

Host 未代改这些固定交付的生产源码；由 Host 修改的两份实施测试文件先后登记于整合记录；首次修改的是写范围之外的 `tests/python/blackboard/store/test_blackboard.py`。首次完整检查揭示其中八处旧 mock 注入点（12 个 error 含五个子项）尚未跟随新角色接缝，Host 在 `44bf4cc` 把注入点改到真实消费的 worker_executor，并补模拟体 name，原断言、错误注入和编号完整保留。正规化 AST 只差这八个目标与八个字段；57 项聚焦绿，再把“外层退出可确认所有 harness 已停止”注入隔离 Worker 副本，既有停止防护五个子项全红，按字节恢复后绿。生产源码未变；第二轮出现的既有时序前提问题另由 Host 在 `0719e65` 修测试夹具：存活子进程由测试清理结束、发现的未确认分支严格保持不可用和输出私有，均有受控红/绿和恢复证据，详见整合登记。没有范围内代码缺陷由 Host 代改，也没有取消后另开这些微任务。

独立 ACP 线的旧 run 已 internally accepted 而不能 continue；本次用户新增整改因此另以关联微任务 `9ceb132c-e9a8-4cbf-b051-aab67197e8a0` 交付，没有取消原 run。整改固定 artifact `67ec3ae9-9d7a-45dd-8483-38570f1e89d2`、输出 `70c95a9a7de2c66f9432ca77db5a5a6791e71093`，整合为 `ccc8bee`，凭据 `int-f42076f2-de72-4700-9f27-4d27cbb4d521`。它仍是 delivered、未 acknowledge，方便在外部复核有缺陷时继续原整改 run。环境沿用 native_environment、只强制私有 DSH_HOME，HOME 默认继承；测试用 BUDDY_CHECKS_TMPDIR 自收尾，不留检出 run 目录或台账。71 项聚焦证据、用户默认 DSH 路径防护的红/恢复绿和改名/新增/删除表在[整改 Host 记录](adr025-dsh-acp-host-fixes-host.md)与[6 行变化表](adr025-dsh-acp-host-fixes-test-map.tsv)，不把该线写作第四步抽取或已获外部验收。

## 检查与证据

最终完整检查在 `0719e65` 执行 `uv run --frozen python -m hey_my_buddy.cli.checks --jobs 2`，退出 0：Python 2,532 项（跳过 1）/166 个文件全部执行，Node 110 项，耗时 729.38 秒，计数与独立加载器一致。原始日志、正确解析并行汇总的 summary、创建路径及脚本在 `<repo>/tmp/adr025-host/step1-full-gate-gbjq1lt4/`；运行器自建的 `<system-tmp>/buddy-checks-b3gcp1r0/` 正常收尾，确认不存在。三次完整检查的前两次失败保留，失败原因、聚焦修正与为什么重跑见整合登记；没有把失败文件的计数混入成功汇总，没有将早前 2,453/163/110 的成绩当作本轮结果。

控制台在 `a5c3c21` 使用本机已有受支持 Node v24.21.0 与既有锁定依赖：54 个文件 /659 项测试通过，typecheck 与 build 均退出 0；构建输出私有忽略目录 `<repo>/tmp/adr025-host/step1-console-cd167oot/dist/`，同目录保留 test/typecheck/build 日志及 summary。`apps/` 从该提交到本次输入字节相等，后续测试修正及记录不影响它，所以没有无理由重复；DSH Node 测试树与基线字节相同，由最终完整检查实跑 110 项。没有安装或升级日常 Node 或其他 runtime。

Host 在 `0719e65` 用真实 unittest loader 独立收集：166 模块、2,532 个唯一编号、重复/加载错误 0；原 2,322 个编号完整保留，删除和改名 0，未变化部分集合相等。新增 210 = 1-A 62（实际模块拆分 33+29，原微任务 34+28 的记录保留并明确更正）+1-B 35+1-C 40+1-D 2+独立 ACP 71。变化表列 139 个核心新增编号及三个 ACP 模块链接；ACP 的旧 69 行与整改 6 行由各自记录拥有，不复制整表。Host 整合修正前后编号集合相等，证据在 `<repo>/tmp/adr025-host/step1-full-gate-gbjq1lt4/host-final-ids.json` 与 `host-id-set-proof.json`；重建的基线同时与最初 Host 原始清单集合相等，原始完整清单留 tmp。

迁移防护的故障见证分别保留：1-B 的严格读取上限、双层停止和 Router 外层布尔；1-C 的 marker 失败持有句柄、未知事件差别、冻结镜像/预算/重试及累计计数/唯一调用绑定/可调用登记；1-D 的 POSIX 组观察错误仍不确认、严格读取拒绝非对象、证据路径被替换后真调用方仍拒绝；本轮整合修正的原黑板停止防护。已有有效红→恢复→绿的族不重复，新增三组原始红/绿及恢复 SHA 在 `<repo>/tmp/adr025-host/1d-final-review-cn_zlc9u/`，Host 整合修正的见证在 `<repo>/tmp/adr025-host/step1-integration-repair-t0dp7hoa/`。未只靠测试总数、AST 方法数或 buddy 完成状态作为验收。

本次 Host 三次完整检查各包含已安装 ZCode 的 localhost fixture 模块一次、5 个用例；每次用例包含 5 个使用合成模型响应的 Worker 回合及 2 次不调用模型的发现，真实付费模型检查 0 次。Codex/Claude/DSH 均用既有 stub fixture，未另起原生冒烟。1-A 最初两轮探针确实曾启动已安装 ZCode 并失败，底层启动总数未知，按原记录保留；不把历史启动写成 0。ACP 整改真实 DSH/模型检查 0 次，新的无模型握手待用户所述 Claude Code Host 执行并复核，本 Host 没有代跑。实施 buddy 的模型回合按各微任务独立计数，不混作原生验证。

1-D 最后 fixed artifact 经 Host 审查后已内部 acknowledged accepted（revision 11），其 self/descendants 停止都确认；固定补丁、三份编号 JSON、脚本、三组原始红/恢复绿及 SHA 共 16 份核心材料，连同首轮/最终 artifact metadata 与累积 patch 保留在 `<repo>/tmp/adr025-host/1d-final-review-cn_zlc9u/`。Host 随后只按开始时登记的一个确切任务根 `<system-tmp>/a25v-w6zbxl88/` 整体删除，含 3,738 个后代对象，成功且根已不存在；没有通配符或屏蔽错误，没有推断别的会话对象的归属，详细回执与删除清单在 `<repo>/tmp/adr025-host/1d-task-root-cleanup.json`。1-D 受管检出暂留供外部复核，没有清理其他会话的检出或材料。

1-B/1-C 已验收任务根的确切删除和保留证据见其 Host 记录；本轮三个完整检查根以及聚焦/诊断/变异的自建检查根由检查函数各自创建登记并正常收尾。Host 的原始清单、所有失败材料、整合修正实验以及 Console 构建产物留在自己创建的忽略 tmp 目录。ACP 仍待外部复核，确切根 `<system-tmp>/a25f-u1fu7fbr/` 与原已分配但未使用的 `<system-tmp>/a25d-lhys8472/` 保留，不提前删除。Worker 不做手动清理，普通测试 fixture 与检查运行器自建自收尾按用户例外执行；1-B 早先清理违规与撤回记录仍保留。

## 外部验收边界

截至本次记录，四个 harness 仍由原载体运行，RUN_SEAMS 为空；本步建立内部格式与调用接缝，未宣称已接通所有原生路径。首个 harness 切片仍由 Host 统一处理 HarnessRun.services 的 Any 标注、SessionService 描述生成、同步 run 与 Worker 外层 handle/截止/取消的监督接线；发现接口缺口时记录公共调整及原因，再让原微任务继续。DSH ACP 客户端也没有被公共调用方引用。第二至四步的抽取和第五步通道切换均等其相应前置验收，当前不开始。

第一步源码和检查记录已准备送 Claude Code Host 审阅。Linux/Windows 没有实际运行，仅现有平台 mock 参加本机测试；没有安装或升级日常 runtime，没有修改用户配置、凭据或日常数据，没有读取凭据文件内容。ADR、SKILL、Host 指南、AGENTS、README 和参考文档自 `4cf58de` 起字节不变；文档改动限于执行计划和本步验收记录。需要用户转达第一步验收结果，以及独立 ACP 两处修正/无模型握手的复核结果；通过前保持这些门槛。
