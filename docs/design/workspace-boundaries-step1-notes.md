# 工作区边界削减第 1 步说明

2026-10-14，基线 `6c2207f6`，执行范围以工作区边界执行计划第 1 步为准；本说明记录 Worker 的源码修改和私有检查，不代表 Host 验收。与裁定有一处需要限定：当前 `_allocation_refs` 的 `for-each-ref` 只输出引用名，且截断到 64 条，不能同时证明固定引用的值；本步保留回收专用的逐引用 OID 核对，把输出形状的合问留给第 2 步。

## 删除与保留的事实

`_snapshot` 删除 `tracked_tree`、`stagedSha256`、`unstagedSha256` 及两次只为字段服务的补丁计算。两个字段没有产品读者；输入和暂存的树、提交、源指纹、选中与排除的文件记录继续保留。源捕获的两遍观察继续证明读取稳定。直接修改当前清单形式，没有旧字段读法、换算或样板变更。

`_prepare_locked` 对本次刚捕获的 existing 检出删除完成阶段的重复内容观察，沿用源捕获的指纹；仓库与检出身份核对保留。旧 `input.json` 的重放仍在原完成位置读一次现场、比固定源指纹；中断 worktree 的复核仍比较 HEAD、index、文件和未跟踪内容与固定输入，新 worktree 完成后仍读一次现场。固定指纹或固定 entry map 已经给出比较对象，这些核对里的立即第二遍观察删除。

`verify` 删除 input/staged 引用核对，仍现读检出身份并检查固定 input commit 的树；需要内容核对的只读或重放路径改为一次观察，与已知执行指纹比较。对象不存在或身份、内容变了仍拒绝；固定对象有内容身份，引用只负责可达性，非回收路径不要求引用仍在。

`_output_entries` 在 worktree 输入与暂存基线为同一棵树时复用已读 map，删除第二次列树；existing 的不同暂存基线仍独立读取。封存入口删除稳定包装中立即重复的第二遍观察，保留入口与原末端两时点观察，末端没有后移。写范围、只读内容检查、文件读前后属性检查均保留。

`_finish_output` 删除对本次刚创建的提交再读树；从磁盘重放 `output.json` 或 `pending.json` 时仍核树。记录摘要和顶层绑定、补丁哈希、只写一次与只建不移的引用发布继续守住固定成果。

`cumulative_patch` 在原始固定提交就是本回合 input commit 时删除 Git 绑定重推、基线解析与重复补丁/路径差计算，直接使用封存补丁的字节和路径清单；记录自身、文件哈希及 `_write_once` 保留。原始输入不同的多回合累计补丁继续从固定对象计算。

`precheck_result` 删除 Worker 刚封存后对 effective manifest 的 Git 复问，改为只核清单记录。失败回合的 partial-output 同样只核原有记录摘要、字段和输入绑定，不再调用完整 Git 绑定证明；停止、attempt/turn 身份、结构化结果的验证保留。记录校验从原 `_artifact_binding` 抽出，固定对象与补丁的读取仍由后续使用产物的边界承担。

`prepare_continuation_workspace` 删除对上一份清单的独立 `verify`，新准备结果与原物理检出的字段比较继续守住分配。删除对刚生成新清单的末端 `verify`，磁盘重放或直接沿用旧清单时保留。是否重放由分配锁内原有的清单读取传出，不增加存储字段，也不在锁外猜测；看板 snapshot 前后比较、所有权与预留栅栏保留。

`_artifact_binding` 删除非回收路径的 input/staged/output 引用核对、重算补丁再比摘要，以及从同一成果重新推导自身路径清单的检查。同次操作对相同固定 commit 只读一次树，仍逐项核对所有期望树，包括同 OID 却不同期望的记录；输出对象、树、父提交、本地补丁哈希与整个微任务的真实路径差保留。由固定树导出的真实路径继续用于验收，固定引用在回收专用路径核对。

`integration_verify` 删除 `_is_ancestor` 前重复的 source commit 解析；Git 的祖先查询本来就把缺失对象或非祖先判成假。目标身份、ref、前后树、路径内容及 `beforeCommit` 的祖先关系继续现读。adjusted 验收删除背靠背第二轮 Git 比对，在同一次比较结果上确认精确的 differing/missing 路径；显式理由和提交区间成立仍是验收条件，没有将未成立的区间宣布通过。

`cleanup_plan` 删除整轮 `cleanup_inspect` 和其 Git 证明，仅依据看板资格、清单、`resolve_allocation` 与路径记录 `planned` 或 `blocked`；计划的分配路径和身份来自这些事实。原有计划期限、控制授权、revision 复核、幂等回执与事件名保留。`planned` 表示看板一侧允许尝试回收。

`cleanup_apply` 在原来的位置证明一次，仍在写入 `applying` 之前；该证明把现场证据与固定引用保留清单写进计划。只有当前仍为 `planned` 的计划，拒绝时才在同一复核事务写 `blocked`、原因和证据；当前已为 `applying` 的计划在任何记录写入前报 `NOT_READY`，它的所有列原样保留，围栏继续生效。挂载分支时仍带 `attachedBranch`。检出已经消失时仍收敛为已回收；真实移除前的身份、登记、分配锁标识和 detached 核对，以及失败后的重新加锁证据均保留。

`_cleanup_proof` 删除写对象、构树与重复观察，封存后只读一次现场并比固定封存指纹。相等直接证明内容一致；不等时以只读 OID 和封存 tree map 在内存求未封存路径，保留排除路径与范围诊断，不把诊断变成仓库写入。没有 seal 时仍以固定执行指纹证明内容。固定 input/staged/output 引用的逐一 OID 核对移入这条回收专用路径，没有用截断的展示清单代替证明。

`_allocation_refs` 删除首回合同一 workspace id 的第二次清点，只对身份列表去重，保留原排序、64 条截断与命令形状。`_reclaim_checkout` 删除计划阶段不再产生的 `blocked` 加 `checkout-missing` 收敛分支，以及计划阶段的 attached-branch 提取；分支诊断从执行的 `NOT_READY` 原样传出，消失收敛留给执行。`get` 中回收段的形态未改。

存储执行的库存删除孤立检出与已验收检出的 Git 证明，只保留分配记录、路径、物理身份、树指纹与看板引用检查。孤立检出在原分配锁内证明一次，再由原看板写者栅栏复核历史引用、小记录与物理身份；已验收检出的两处 plan/apply 调用随计划零 Git，只由执行证明一次。存储计划的证明保留，计划与执行之间的用户确认边界没有合并。

`shared_stash_inventory` 删除对已知 common Git dir 再解析一次；两个产品调用点本来就传 `snapshot.repositoryPath`。没有 stash 的 ref 读取及有 stash 的完整 reflog 读取继续保留，读取失败仍是 unknown；直接测试随这个内部输入约定传公共目录，没有在产品里留下旧参数形态的分支。

## 实测计数

计数使用检出内临时 `sitecustomize` 记录真实 `Popen` 源码栈，用 PATH 前置脚本记录真正启动的 Git 后转发原 Git，未读 stdin、改输出或退出码。仪器仅在计数进程的 PATH/PYTHONPATH 中启用，未改产品源码、检查器或解释器环境；检查仍经指定解释器的 `hey_my_buddy.cli.checks` 入口、默认进程数。改前与最终改后各跑同一七文件集合，表中包含测试夹具和直接 Git 故障注入；主例另按调用栈剔除它们。

| 主例产品操作 | 改前 Git | 改后 Git |
| --- | ---: | ---: |
| 准备新 worktree | 59 | 49 |
| 封存 | 38 | 29 |
| 目标验收（带 beforeCommit） | 30 | 18 |
| 回收计划 | 39 | 0 |
| 回收执行（含移除） | 46 | 26 |
| 两次 stash 观察 | 4 | 2 |
| 产品合计 | 216 | 124 |

| 七个文件 | 改前测试 | 改后测试 | 改前 Git | 改后 Git |
| --- | ---: | ---: | ---: | ---: |
| test_workspace_lifecycle.py | 49 | 53 | 13065 | 9042 |
| test_workflow_preparation.py | 30 | 31 | 6116 | 4482 |
| test_storage_orphans.py | 35 | 35 | 5824 | 4113 |
| test_scope_recovery.py | 18 | 18 | 2822 | 2003 |
| test_workspace.py | 26 | 28 | 2883 | 2451 |
| test_workflow_real.py | 11 | 11 | 2857 | 2022 |
| test_workspace_submission_cleanup.py | 20 | 20 | 2488 | 1913 |
| 合计 | 189 | 196 | 36055 | 26026 |

七文件总数减少 10,029 次（27.8%）。Host 复核后的两条新回归测试包含在最终计数里，正常主例的各项调用数没有变化；其余六文件的调用数与首次交付相同。

改前 189 条测试，最终改后 196 条测试；新增场景的 Git 也包含在七文件改后总数里，因此总数不是保持测试数不变的预算。主例的夹具/直接 Git 均为 8 次，产品 216 → 124 次，少 92 次（42.6%）；主例未经过原生 harness 的独立回合开始与累计补丁收集，不代表所有微任务的一生上限。

## 测试变更与验证

删除的旧断言：`test_dirty_input_preserves_staging_binary_modes_symlinks_and_source` 不再检查两个旧哈希非空，改为检查两个字段不存在；`test_missing_or_spoofed_source_objects_cannot_verify` 中缺 inputRef 就拒绝验收的断言删除，剩余对象伪造/输出上限场景保留并改名 `test_spoofed_source_objects_cannot_verify`；stash 测试中直接传 private git-dir 的旧输入形态断言删除，公共目录归属与多 worktree 共享事实保留。没有删除整条安全场景或归并测试。

修改的回收场景：`test_an_attached_branch_blocks_removal_with_the_branch_and_the_detach_retry` 改为计划 `planned`、执行后 `blocked`，保存原分支诊断；`test_apply_reports_the_same_branch_diagnostic_as_the_inspection` 增加拒绝后计划、原因、证据、保留引用、检出文件、分支与 refs 原样的断言；`test_cleanup_is_blocked_by_unsealed_changes` 的既有 blocked 和现场保留断言继续通过。

修改的存储注入：`test_apply_rechecks_database_after_inventory` 透传库存内部关键字；两条 `test_late_reference_after_locked_proof_is_rechecked_in_writer_transaction`、`test_late_allocation_record_change_after_locked_proof_is_retained` 从“恰好第二次 helper”改为在唯一锁内 Git 证明后注入，仍验证写者栅栏与小记录复核拒删；库存 helper 的两次调用仍在，只有锁内那次读 Git。

修改的续接注入：`test_partial_recovery_is_fenced_by_a_new_manual_input` 与 `preparation_race` 的取消、换主、新输入和替换分配场景透传私有准备事实，原有竞态断言保留。`MockWorkspace` 配合记录校验、重放事实、同次 adjusted 判断和无物理分配的事实，不模拟 Git。

修改的 stash 直接测试：`test_reads_commit_ids_and_full_descriptions`、`test_a_repository_without_stashes_is_an_empty_observation`、改名后的 `test_worktrees_share_stash_through_the_common_git_directory`、`test_sha256_repositories_report_full_length_commits`、`test_the_entry_list_is_bounded_and_marks_truncation`、`test_undecodable_description_bytes_stay_canonically_encodable`、`test_adding_the_65th_stash_never_reports_the_unchanged_oldest_entry_lost`、`test_a_truncated_start_side_cannot_claim_new_entries_but_keeps_provable_losses`、`test_the_recorded_recovery_command_restores_a_dropped_entry` 和 `test_claim_and_result_freeze_real_shared_stash_facts_end_to_end` 都改传公共目录，原来的 stash 内容、边界、恢复与端到端断言保留。

首次交付新增五条测试：`test_missing_input_and_output_refs_allow_seal_and_verification_but_block_cleanup` 覆盖初回合清引用后的封存与对象验收；`test_cleared_workspace_refs_allow_turns_and_acceptance_but_block_cleanup` 覆盖清引用后的续接、回合开始、第二次封存、目标验收与回收拒绝；`test_matching_seal_cleanup_proof_does_not_write_git_objects` 检查实际 Git 写命令未被调用；`test_matching_seal_fingerprint_needs_no_git_object_writes_for_cleanup_proof` 检查对象写入与构树入口都没有被调用；`test_replayed_continuation_rechecks_changes_after_its_input_was_prepared` 证明删除 fresh 路径复问后，磁盘重放仍拒绝后来改动。

Host 复核修正后的最终源码检查：`tests/python/blackboard/tasks/` 全目录与 `tests/python/cli/test_repository_hygiene.py` 合计 42 文件、697 条测试、0 跳过，退出 0；重测计数轮的七文件合计 196 条测试、0 跳过，退出 0。两轮均由检查器确认私有根移除。首次交付的任务目录加卫生检查 695 条、七文件计数检查 194 条及改前七文件 189 条检查保留为历史验证；表中改后数已更新为本轮最终源码和新增场景的实测。`git diff --check` 通过；本说明另核对 UTF-8、段落物理行、机器路径、表格合计与改动范围。

## Host 复核后的两处修正

`cleanup_apply` 原先用同一条 UPDATE 把 `planned` 和 `applying` 的拒绝都写成 `blocked`，这会放开一次已经开始、可能只删了一部分的回收围栏。现在在原复核事务中根据当前计划状态判断：拒绝的 `applying` 在写入之前直接返回原 `NOT_READY`，任何一列都不更新；只有 `planned` 的拒绝才保存 `blocked` 和证据。成功路径及原来的证明位置不变，没有增加 Git 调用。

存储执行原先只记录 `NOT_READY` 错误码，现在把它携带的具体 reasons 写进跳过项；没有具体理由的拒绝仍保留错误码。普通文件改动还有更早的路径：无 Git 库存的文件系统指纹已经变化，会直接跳过为 `candidate-changed`。本轮在路径和类别未变、看板仍允许、已有分配的受管检出上增加一次只读诊断，保留其 `unsealed-changes` 等具体理由；这个分支已经决定不删除，诊断通过也仍以 `candidate-changed` 跳过，原计划变更保护没有放开。孤立检出及其他类别不走这条诊断，正常删除仍只由 `cleanup_apply` 证明一次。新诊断改变了拒绝路径调用数，因此重新量了七文件；本轮新测试中诊断分支实发 21 次 Git。

新增 `test_refused_started_reclaim_preserves_every_plan_column_and_the_capture_fence`：在 `applying` 落盘后、真实移除之前中断，删除一个已跟踪文件，再回收，验证 `NOT_READY` 原因、计划所有列逐项相等、`_cleanup_fence` 仍拒绝，以及其余文件和固定引用不变。新增 `test_storage_apply_keeps_unsealed_change_reasons_after_planning`：分别用普通改动和保持原 stat 信息的同长度内容改动覆盖提前跳过与 `cleanup_apply` 拒绝，均验证具体原因 `unsealed-changes`、未删除及文件、引用原样。两个子场景使用独立目标目录。

修正前的私有回归注入复现了计划变为 `blocked` 及跳过原因为 `candidate-changed`；首个测试草稿的第二个子场景还因目标目录复用而失败，已改为独立目标，最终两个子场景均通过。复核要求之外的源码削减没有变动。

## 裁定的限定与本步未做

引用清点一问不能在当前输出形状下承担 OID 证明，故本步保留回收逐引用核对，后续合问再处理；这点已经在交付首段报告。另有两处删除只适用于 fresh：旧 `input.json` 和磁盘封存记录仍有自己的边界，分别保留现场或树核对，避免删掉最后的守卫。裁定只点名 effective manifest 的导入复问，实际 partial-output 导入也调用完整 Git 证明，本步按“导入只验记录自身”一起删除该 Git 复问，记录绑定仍保留。

没有引入新的 Git 命令形状、合问、观察后移、相等图免构树、引用发布合并、恢复前的新观察、双 force 或测试归并；这些属于后续步骤。没有合并回收计划与执行、改变 `get` 中回收段的形态、公开接口、错误码或事件名；没有改版本号、schema 版本、升级基点样板、控制台或其他文档。完整检查、整合、Host 的删除故障注入验收、跨系统与安装验证由 Host 承担，本微任务没有据此作成功声明。临时计数脚本与原始日志不交付。
