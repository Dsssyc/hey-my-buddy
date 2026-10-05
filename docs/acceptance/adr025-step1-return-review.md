# ADR-025 误发会话的交付核对与收尾

2026-10-05，用户要求检查误发至会话 `01a10886-4355-7d72-84a5-31837edd8dc1` 的第一步整改，符合要求后回到本会话 worktree 并清理。该会话显示 idle、实施回合 completed；它虽然属于另一个工作区，但源码操作实际直接在本会话的 `<implementation-worktree>`、分支 `socu/adr025-run-module` 进行。`eeb5116` 已由 `45d6d7e` 合入，所有整改、Host 修正与记录已在本分支，核对时 HEAD=`d46255b` 且工作区干净，因此没有另一份 checkout 或提交要重复合入。原始交付和[瘦身记录](adr025-step1-pydantic.md)保留。

本 Host 对当前源代码、固定交付和已有验证材料复核：运行/实时共 34 个 pydantic 模型都 strict/frozen/extra=forbid，逐类手写 post_init/to_payload/from_payload 已删除，使用一份公共基类；根包仅一份 canonical_json 与一份带重复键/非有限数拒绝的严格解码，解析前整帧大小门与 BoardError 接线保留。公开 CLI schemas、帮助生成和控制台字节未改，legacy 文件/Catalog/未使用角色观察服务已删除。运行格式 1781→665 行（37.3%），包括公共基类在内 3591→1794 行，略高于三分之一的原因已由原记录说明；首次 ZCode 所需尚未使用的格式和调用点仍按用户边界保留。

本次独立重新核实本项目 uv 环境的 pydantic 2.13.5：重复 JSON 键确实最后一个覆盖，公共严格解码拒绝重复键、NaN、Infinity 与 1e999。重跑 run_contract/live_channel/role_controller 三个聚焦模块，61 项绿，检查根由运行器正常收尾；统计脚本在测试完成后把原始列表按对象读导致 TypeError，后续只更正统计，没有重跑测试。一次统计命令准备另有括号语法错误，发生在执行之前，没有产品修改或测试运行。复核原始清单 2322 个全部保留、2532→2497，39 行新增/删除/迁移表完整。先前两次完整检查 stdout/stderr 和退出记录均核对，2497（跳过 1）/166/110 通过；其最终输入与当前源码/测试/依赖字节相等，因此本次没有再跑完整检查，也没有原生 harness/模型检查。证据在 `<repo>/tmp/adr025-host/return-review-fvz7kkl5/`，原完整检查及比较探针继续留在 `<repo>/tmp/adr025-pydantic-host/`。

需要披露两项此前流程偏离。其一，误发会话在已合入用户的 eeb5116 后又编辑了 ADR-025 的状态段和第 10 条；本次恢复该文件至 eeb5116 原字节，把用户的新顺序追加到执行计划，不补写 ADR。其二，格式实现的 countsByType 退化属于原微任务写范围内的缺陷；此前会话先 rejected 后由 Host 在 9382c44 直接代改三条代码/测试路径，再 acknowledged accepted，没有按用户要求用原 run continue 打回。这不是范围外或合并才出现的缺陷，不将它描述成符合打回规则；既有三个调整路径、红/绿与整合回执原样保留。该内部 run 现在 accepted，受管检出已自动回收；不撤销正确修正、重开任务或伪造 continue。本次复核确认修正后的字段形状测试和源代码正确，最终整步是否验收仍由 Claude Code Host 决定，后续严格按用户现行规则处理。

清理只处理本宏任务有开始时创建登记的对象：ACP 整改 run `9ceb132c-e9a8-4cbf-b051-aab67197e8a0` 已按用户转达的 71 项测试及一次已安装 DSH 无模型握手 acknowledged accepted；本次读取确认 ACP 和 pydantic 实现 run `2d76ac80-d9a1-4f0a-a90d-a7cd483377de` 均 accepted、self/descendants 停止确认，服务 cleanup 均 applied/removed=true、检出已不存在，固定引用/patch 已保留，没有再次删除。随后 Host 按原创建台账，只整体删除确切的 `<system-tmp>/a25f-u1fu7fbr/`（285 个后代对象）和未使用的 `<system-tmp>/a25d-lhys8472/`（4 个后代对象），核心 ACP 证据已在此前验收前保留目录中；删除成功，未用通配符或屏蔽错误，回执/对象清单在本次 review 目录的 ACP-task-root-cleanup.json。其他会话的工作区与材料没动。

误发会话还把两份参数文件直接写到 `<system-tmp>/adr025-pydantic-integration.json` 和 `adr025-pydantic-cleanup.json`，未按用户规则放入已登记任务根；命令历史能证明写入路径，但没有创建前不存在的登记，无法确认原对象归属，本次不据名字或日期删除。该清理规则偏离如实登记，没有读取这些参数文件的内容。保留本实施 worktree、待 Host 审阅的源代码与忽略 tmp 验证材料；不归档或调整误发的自动化会话，也不操作其记忆工作区。

本次收尾仅恢复授权文档与补执行/验收记录，没有新代码或测试调整。第一步整改已回归本会话状态，停等 Claude Code Host 复核行数、编号、重复消除与依赖；第二步尚未开始。后续先 ZCode，完成首次使用与删去无使用内容、验收后，再并行其他三个 harness。
