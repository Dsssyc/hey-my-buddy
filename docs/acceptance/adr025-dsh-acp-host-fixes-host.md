# ADR-025 ACP 环境与测试夹具整改：Host 整合记录

2026-10-04，按用户转达的 Claude Code Host 复核意见，修正 ACP 客户端的环境继承和测试临时目录。`DSH_HOME` 仍强制私有并保留全部路径校验；环境使用既有 `native_environment`，`HOME` 默认继承，调用方仍可显式指定并校验私有 `HOME`。测试使用 `BUDDY_CHECKS_TMPDIR` 中的普通夹具，正常收尾，无常驻 run 容器或清理台账；最终未确认停止直接失败。客户端仍没有公共接线。[微任务记录](adr025-dsh-acp-host-fixes.md)给出两个实际改动及历史边界。

原 run `ee2ee1e3-5c5d-4032-8a6f-fcfd20e50b6e` 已内部 acknowledged accepted，服务拒绝其 continue，错误为 `CONFLICT: An accepted goal cannot be continued`。Host 保留原 run、产物和五个回合历史，没有取消它或改变黑板状态；本次按用户修订后的环境与清理要求，在同一宏任务下路由追加微任务 `9ceb132c-e9a8-4cbf-b051-aab67197e8a0`。本 run 有三个实施回合、两次 continue：初始整改；修正新停止守卫测试和异常记录；最后只更正回合名称。所有范围内修正均由原微任务完成，Host 没有代改源码。

最终 artifact 为 `67ec3ae9-9d7a-45dd-8483-38570f1e89d2`，输出 commit 为 `70c95a9a7de2c66f9432ca77db5a5a6791e71093`；累积基线 `a5389f7`，patch SHA-256 为 `fc3a20b62beb323ffe22f25c9c3d53e090cf99e66048ed84acf1a965c7169bfe`。Host 核对全部六条变更路径及对应工作区字节，将固定产物原字节整合为 `ccc8bee`，整合前本分支为 `83ef7a2`；整合凭据为 `int-f42076f2-de72-4700-9f27-4d27cbb4d521`。源码变更仅在 ACP 包与对应测试，其他角色、注册表、公共值、旧 DSH 入口和历史记录未随本线改动。

初始三个 ACP 聚焦模块 71 项通过，默认 DSH 目录检查的最小变异失败、恢复后单项通过，原始日志已实读。首次审查发现新增守卫测试使用 POSIX 专属函数，拒绝并 continue 后改为内存中的 Job 观察；单项及模拟缺少 POSIX 函数的两次验证均通过。失败路径说明也按实现修正：shutdown 抛错时终止后重抛原异常；最终仍未确认时才抛 AssertionError；终止后确认则通过。最后纯文档 continue 没有重跑任何测试或编号收集，且源码、支架与测试字节相对前一份交付相等。本次没有新的完整检查；整合批的完整检查待第一步其他交付收齐后集中进行，此前 2,453/163/110 不作为本次结果。

整合后 Host 仅动态收集编号，在 `ccc8bee` 得到 2,490 个唯一编号、164 个模块，无导入错误。相对整合前 2,488 个编号，2,485 个保持相等；ACP 的两个编号改名，一个 preserve 机制测试按用户要求删除，新增三个环境或停止防护测试，ACP 从 69 项变为 71 项。[变化表](adr025-dsh-acp-host-fixes-test-map.tsv)只列六行变化，原始列表及集合证明在 `<repo>/tmp/adr025-host/after-acp-host-fixes-ids-zv9q4ztk/`；这次收集不是测试执行。

本次原生 DSH 验证 0 次、模型检查 0 次；原客户端历史为 DSH 无模型握手 1 次、prompt 0 次，计数保持独立。修正后的已安装 DSH 无模型握手由用户的 Claude Code Host 执行，本 Host 与 Worker 均未抢先运行。代理、CA、locale 等继承由合成环境和普通 Python 子进程证明，实际 DSH Worker 行为尚未据此宣称验证。

Worker 使用开始时由 Host 创建并登记的 `<system-tmp>/a25f-u1fu7fbr/`，本次未手动删除对象；聚焦测试的自有夹具正常收尾，`t/` 仅余 uv 自建锁文件，未在检出创建测试 run 目录或常驻台账。Host 已把固定 patch、测试和变异日志、原始编号及模拟材料按确切路径与 SHA 保留于 `<repo>/tmp/adr025-host/acp-host-fixes-first-5tqfus__/`、`acp-host-fixes-second-szsq67uf/`、`acp-host-fixes-final-2_n573dk/`。没有复制私有主目录、凭据或基线归档树。原任务根仍保留，待验收后 Host 按该一个确切路径整体删除；旧 run continue 被拒绝前分配而未使用的 `<system-tmp>/a25d-lhys8472/` 同样有创建账目，尚未删除。

本线已提交，停下等用户转达两处复核及无模型握手结果。黑板保持 delivered，最终 acknowledge 暂留至外部复核通过，以便需要修正时仍能对同一个 run continue；整合已登记不等于外部验收。第一步独立进行到 1-C，ACP 公共接线仍等待第一步外部验收。
