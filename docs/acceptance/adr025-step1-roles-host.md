# ADR-025 第一步 1-C：Host 固定交付与整合记录

2026-10-04，1-C 原 run `511b3514-bb69-43db-bca3-aafc2a540620` 经两个 continue 完成，三个实施回合中的最后一个只修记录、未重跑检查。最终 artifact 为 `fdbdc3f4-1e76-4478-85f0-108ff33a2d81`，输出 `2e534f8eb53ab05ba14f7738130def43cf97b0cc`，从 `f8b42a5` 起的累积 patch SHA-256 为 `8cead61d3cb3f9bb65094c879bc675bc406fd7fadc66e1d90453cf740f048ae0`。Host 核对全部七条变更路径及字节，按原字节整合为 `a5c3c21`；整合前本分支为 `70ac144`，凭据为 `int-b57ec708-a565-4619-bd26-ccd5a6081d0c`。黑板已内部 acknowledged accepted，第一步整步外部验收仍待进行。

首次交付因原异常重试、Router 重复选择实例、生命周期通知误计工具调用、快速模式未即时停止、登记与调用绑定以及记录事实被拒绝，全部由原微任务 continue 修正。Host 对原版/新版的进程内探针确认：准备失败仍是原 `ADAPTER_UNAVAILABLE`，Router 只选择一次并启动选中实例，不可调用对象不能登记，快速模式工具事实立即停，审阅模式一次调用的 start/end 累计为一次。探针的初版缺少 context 内的私有 state，失败记录原样保留；补齐后的有效结果在 `<repo>/tmp/adr025-host/roles-first-review-z9oxsflj/results-fixed.json` 与 `continue-result.log`。Host 没有代改产物源码。

七个聚焦模块首次共同尝试中 role 模块失败，其余六模块通过；失败汇总未覆写。最终 role 的 34 项绿日志与其他模块通过日志、四组最小变异的红/恢复绿及恢复 SHA 已实读；变异覆盖异常重试、累计计数幂等、唯一登记绑定和方法可调用性。最后文档回合更正实际失败用例名称、绿色证据来源及三个回合/两个 continue 的口径，源码和测试相对 `43f97b97` 字节相等，未重复测试。原 173 个受影响旧编号集合相等，两个新模块最终 40 项；完整编号表由 1-D 汇总。真实 harness 验证和模型检查均为 0 次。

本步放置了角色调用接缝和观察/services 类型，当前四个 harness 仍使用原载体；`RUN_SEAMS` 为空，新 run 入口由合成对象验证。services 的描述生成、公共协议类型标注及同步 run 与外层 handle 的接线仍由 Host 在首个 harness 切片统一处理，不把这些余项记成已联通运行。现有 Worker 的认领、handle 持有、停止、续租、回执及 Router 冻结副本、资格与发布判定保留；[微任务记录](adr025-step1-roles.md)列出实现和未接线边界。

Worker 使用开始时由 Host 创建登记的 `<system-tmp>/a25c-kcjpp32j/`，本轮未手动删除对象。Host 验收前保留本轮原始聚焦/变异/编号/脚本材料共 55 份文件及固定 patch，位置为 `<repo>/tmp/adr025-host/1c-final-fixed-40aayou3/`；没有复制私有凭据文件或测试状态根。验收后只按上述一个确切任务根整体删除，成功且确认不存在，没有通配符或屏蔽报错；回执在 `<repo>/tmp/adr025-host/1c-task-root-cleanup.json`。其他会话材料未动。

1-D 已从 `a5c3c21` 在独立 worktree 通过普通路由开始，run 为 `cc0e12cf-834b-4ec7-bca4-203bd9b4c857`，只核对迁移编号及补齐防护，不跑完整检查。Host 将在该交付整合后集中跑一次完整检查并提交第一步整步记录；独立 ACP 整改仍等待用户转达外部复核，未接线。
