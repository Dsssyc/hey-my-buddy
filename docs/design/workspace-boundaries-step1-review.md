# 工作区“只删不换”第 1 步：独立审查

2026-10-14，对象是 `003a0f8c` 相对其第一父提交在 `src` 与 `tests` 下的差异。只读差异、[计划](workspace-boundaries-plan.md)、[裁定](workspace-boundaries-review.md)与代码；没有运行测试、检查器或服务，没有读验收记录。第 1、2 节写完之后才读实现方的[说明](workspace-boundaries-step1-notes.md)。W、S、F 指 `src/hey_my_buddy/blackboard/tasks/` 下的 `workspace.py`、`storage.py`、`workflow.py`，行号是 `003a0f8c` 的。〔读〕读代码得到；〔推〕推断；〔验〕在系统临时目录的新仓库里用 Git 2.54 验证过。

结论：守不住的检查 1 处，另有 2 处从事前挪到了事后；能写出反例的 3 个，其中本步引入 2 个，最重的一个是旧有的；测试守不住的 3 处。没有找到回收删掉未封存内容的序列。

## 1. 逐处清点

| 删掉或挪走的检查 | 守的事实 | 现在由谁守 | 判断 |
| --- | --- | --- | --- |
| `_snapshot` 的 `tracked_tree` 与两个哈希 | 无，只写不读 | 不需要 | 守得住〔读〕 |
| `_prepare_locked` 对新捕获的 existing 检出再看一遍 | 捕获到写清单之间现场没变 | 执行指纹直接取源指纹（W1010）；`_intent` 禁止提交基线带未跟踪选择（W604），两处选择器相同，指纹同义。此后的改动与旧代码里第二遍之后的改动一样，由只读或经路由回合的 `verify`（W862）发现，或算进成果 | 守得住〔读〕 |
| 同上，`input.json` 重放 | 冻结之后现场没变 | W1006–1009，一遍观察比源指纹 | 守得住〔读〕 |
| `_prepare_worktree`（W709）、`verify`（W862）、`_cleanup_proof`（W2344）的第二遍观察 | 读的过程中现场稳定 | 比较对象是固定指纹，一遍已能判等；第二遍只把“读完到动手”的窗口挪后一点，不是另一种守卫 | 守得住〔推〕 |
| `_seal_output` 入口的第二遍观察 | 同上 | 末端那一遍还在（W1247） | 守得住〔读〕 |
| `verify` 查 `inputRef`、`stagedRef` | 引用还指着固定提交；顺带证明两个提交对象在 | 输入提交：W858。暂存提交：`verify` 不再碰它，existing 且暂存树不同于输入树时要到封存的 W1100 才读 | **推迟**，见反例 3〔读〕 |
| `_output_entries` 对暂存树的第二次列举 | 无，两棵树相同时结果相同 | W1100–1101 只在不同时才读 | 守得住〔读〕 |
| `_finish_output` 对刚建的提交问树 | 记录的树与提交一致 | 提交由同一次调用用这棵树建成；从磁盘重放仍查（W1158） | 守得住〔读〕 |
| 首回合 `cumulative_patch` 走 `_artifact_binding` | ① 仓库身份、树、父提交、补丁与对象一致；② 改动路径不超过 512（`_tree_changes`，W1902） | ① 同一进程刚封存，验收（W2009）与回收（W2337）再查。② 首回合没有任何一处在回合内查 | ① 守得住；② **守不住**，见反例 2〔读〕 |
| `precheck_result` 对 effective 清单的 `verify`（F6350） | 检出身份与输入对象还在 | 封存入口刚查过（W1227）；验收与回收各查一次 | 守得住〔读〕 |
| `precheck_result` 对 partial 的 `_artifact_binding`（F6374） | partial 的提交、树、父、补丁与对象一致 | 只剩记录自洽（W1908）。对象一侧由用到它的地方守：验收 W2009、回收 W2337、作续接基线时 W629 | 守得住，时点后移〔读〕 |
| 续接里对上一份清单的 `verify`（原 F6119） | 列未跟踪文件之前检出身份没变 | 受管检出：`_borrowed_submission_allocation`（W944–949）在捕获之前查。用户自己的检出：只剩准备之后的字段比较（F6141），此时对象与引用已经写进那个仓库 | **推迟**〔推〕 |
| 续接里对新清单的 `verify`（F6150） | 新清单对应的现场没变 | 新准备刚观察过；重放仍查（F6147–6150） | 守得住〔读〕 |
| `_artifact_binding` 查三类引用 | 引用还指着固定提交 | 只在回收：W2308–2317、W2325、W2341 | 守得住〔读〕 |
| `_artifact_binding` 重算补丁比 `diffSha256` | 补丁文件等于两个提交的差 | 补丁文件的哈希仍比（W1965）；“等于提交的差”不再有人查，它由封存一次算出并只写一次 | 守得住〔读〕 |
| `_artifact_binding` 重推 `changedPaths` | 记录里的路径清单等于树的差 | 没有人再查；验收用的是从树现算的路径（W1971），不信记录里的清单 | 守得住〔读〕 |
| `integration_verify` 里 `_is_ancestor` 之前的提交检查 | 无 | 对象不在时 `merge-base --is-ancestor` 退出 128，`_is_ancestor` 返回假〔验〕 | 守得住 |
| adjusted 的第二轮 `integration_verify` | 无，同一批固定提交 | 一轮内确认（W2062–2065），理由与区间仍是条件 | 守得住〔读〕 |
| `cleanup_plan` 的 `cleanup_inspect` | 计划时现场可回收 | `cleanup_apply` 的 F5341，在写 `applying`（F5387）之前；被拒写 `blocked` | 守得住〔读〕 |
| `_reclaim_checkout` 的“blocked 且检出已不在”收敛 | 检出消失时算已回收 | 计划不读 Git 所以是 `planned`，执行见路径不在即 `applied`（F5340、F5398） | 守得住〔读〕 |
| `_cleanup_proof` 写对象、建树比树 | 现场与封存的树一致 | 指纹相等即返回（W2345）。指纹盖住 HEAD、index、全部受管文件；封存的树是那次观察的确定函数，所以相等时现场里没有树外的受管内容 | 守得住〔推〕 |
| 存储执行时库存里的 Git 证明（S804） | 确认之后现场没变 | 孤立检出：分配锁内 S384；已验收检出：F5341；两者之前还有文件系统指纹（S817–818） | 守得住〔读〕 |
| `shared_stash_inventory` 解析公共目录 | 传入的是公共目录 | 两个调用点传 `snapshot.repositoryPath`（F5646、F5712）；传错则读取失败，记为 unknown | 守得住〔读〕 |
| `_allocation_refs` 首回合问两遍 | 无 | W2199 去重 | 守得住〔读〕 |

与实现方说明不一致之处：

- 说明写 `verify` 之后“对象不存在仍拒绝”。对输入提交成立，对暂存提交不成立（反例 3）。
- 说明没有提首回合累计补丁原来顺带拦着 512 条路径的上限（反例 2）。
- 说明没有提续接恢复时身份比较落到了写入之后。
- 说明没有提存储计划的一处行为变化：存储计划调 `cleanup_inspect` 不带 `allowed`（S502），旧代码对采纳过范围外路径的现场会报 `unsealed-changes`，现在指纹相等即判可回收，执行时 F5343 带着 `allowed` 也通过。内容全在采纳记录的树里，不丢数据，但“存储回收不动采纳过的现场”这个旧结果变了〔推〕。
- 说明把被拒的续做停在 `applying` 写成守住围栏；它同时是反例 1 的卡死，说明没有写怎么出来。

## 2. 反例

没有找到让回收删掉未封存内容的序列。证明到移除之间没有围栏，有人在这中间改文件就会丢，这个窗口旧代码同样有，本步没有加宽〔读〕。两条命令并发执行同一份计划时，后到的一方若被拒且计划已是 `applying`，不改记录（F5373–5376）；若先写下 `blocked`，另一方在写事务里被 F5442 拦住〔读〕。

**反例 1：续做被拒的回收永远停在 `applying`（旧有，本步保留并用测试钉住）。** 前提：计划已写成 `applying`。步骤：`cleanup_remove` 跑完 `worktree unlock`（W2428）、还没跑 `worktree remove`（W2429）时进程被杀；重放同一条回收。结果：登记里不再有 `locked` 一行〔验〕，W2294 报 `lock-mismatch`，F5373 抛 `NOT_READY` 而不改记录；`cleanup_remove` 自己也会在 W2423 拒绝。此后每次回收都一样，而 `applying` 让 F931 拒绝捕获、F998 拒绝写者、F2771 拒绝续接，`cleanup_plan` 只会把这份计划当重复返回（F5196）。检出完好却被围死，只能由人手工 `git worktree lock --reason buddy:<分配号>` 才能出来，产品没有给出这条出路。`worktree remove` 删到一半被杀、或 `applying` 之后有人改了文件，是同一类。裁定第 2 节正是以“被拒时计划停在 `applying`，检出反而被围住”否掉了设计的后移；续做这一支没有照这个理由处理。测试：`mock.patch.object(workspace_module, "_git")`，让 `worktree remove` 那一次调用抛 `RuntimeError`（W2430 只接 `BoardError`，等于在两步之间被杀），之后再 `reclaim` 两次，断言两次都是 `lock-mismatch`、计划仍是 `applying`、`_cleanup_fence` 仍拒绝。

**反例 2：首回合改动超过 512 条路径的成果作为成功交付（本步引入）。** 前提：第一回合，`goalInputCommit` 等于 `inputCommit`。步骤：Worker 改 513 个文件并正常结束。旧：`cumulative_patch` 经 `_artifact_binding` 的 `_tree_changes` 抛 `WORKSPACE_UNSUPPORTED`，`partial_outputs.enrich` 把回合记为失败。新：W1285–1293 不读 Git，回合成功交付；验收在 W1971 抛 `WORKSPACE_UNSUPPORTED`，回收在 W2337 报 `sealed-output-invalid`，检出留着（安全的方向），但这份成果既验收不了也回收不了，而第二回合起同样的成果仍在回合内失败，两处不一致。测试：照 `test_workspace_lifecycle.py` 358 行把 `OUTPUT_ENTRY_LIMIT` 补丁成 0，封存后调 `workspace.cumulative_patch(manifest, seal, manifest["inputCommit"])`，旧代码抛错、新代码返回。

**反例 3：暂存提交的对象不在了，回合照常开始（本步引入，轻）。** 前提：existing 检出、working-tree 基线，index 里有暂存改动而工作树等于 HEAD，于是输入提交就是基线、暂存提交是只靠 `refs/buddy` 保着的新提交。步骤：清掉 `refs/buddy` 并 `git gc --prune=now`（无引用的提交随之消失〔验〕）；开始回合。旧：`verify` 读 `stagedRef` 失败，回合不开始。新：`verify` 只问输入提交（W858），回合开始，Worker 做完之后封存在 W1100 的 `ls-tree` 上失败。结果是白跑一个回合，内容留在用户自己的检出里，不丢。这正是第 2 条决定的边界之外：对象也不在时，回收之外的一处继续了。测试：按上述前提 `prepare`，删引用、`gc --prune=now`，断言 `workspace.verify(manifest, require_unchanged=False)` 抛错；现在它不抛。前提里那棵暂存树确实会被回收是〔推〕。

## 3. 定下的三件事

1. **两个哈希。** 照做。W661 不再写，`_validate_manifest`（W825–826）本来就不要求它们，没有留旧读法〔读〕。
2. **引用只在回收处拦。** 照做，没有漏拦：有封存记录走 W2341，没有走 W2325，孤立检出另有 S343–352。做过头一处，即反例 3。另外两点不算过头：首回合 `cumulative_patch` 与结果导入完全不读 Git，对象不在也继续，但它们紧跟同一进程的封存，产物到验收与回收时仍被查〔读〕。
3. **回收只证明一次。** 照做：计划零 Git（F5205–5227），证明在 F5341、写 `applying` 之前，被拒写 `blocked` 连同原因、证据与保留清单（F5377–5393）。三点要 Host 知道：
   - 被拒时计划状态变了却不发事件、不动宏任务的修订号，而 `workflow.cleanup_planned` 事件里已经写了 `eligible: true`（F5263）；事件流里这份计划始终是可回收的。源码里没有找到读这个事件的地方〔读〕。
   - 续做被拒不写 `blocked`，是反例 1。
   - 这一步叫只删不换，却新增了一处 Git 诊断 `_changed_workspace_reasons`（S744–754），只为让跳过项保留具体原因；另加了四个只为省调用的内部开关（`_preparation`、`_confirm_adjustments`、`git_proof`、`fresh`）。都不影响删除的判断，是否留着由 Host 定。

## 4. 测试

守得住的：`test_cleanup_is_blocked_by_unsealed_changes` 与两条 attached-branch 测试钉住了“被拒记为 `blocked`、现场原样、改回去能再回收”；`test_storage_apply_keeps_unsealed_change_reasons_after_planning` 的两个子场景分别走文件系统指纹与 `cleanup_apply` 两条拒绝路径（`tree_info` 只看 inode、大小、时间，S53）；`test_replayed_continuation_rechecks_changes_after_its_input_was_prepared` 在重放不再 `verify` 时会失败；adjusted 的一轮判断有真 Git 的验收测试（`test_workspace_lifecycle.py` 436–450）。〔读〕

守不住的三处：

1. **只测了引用不在而对象在。** 两条清引用的测试都不跑 `gc`，原来“缺引用即拒绝”的断言删掉之后，没有一条测试要求对象不在时封存、回合开始或验收拒绝。把 W858 或 W1959–1965 的对象检查删掉，这次新增的测试全过。
2. **回收处的引用检查钉得粗。** 两条测试都是把引用一次清光再断言 `sealed-output-invalid`；`fixed_refs_match` 退成只查其中一个引用，测试仍过。没有封存记录的那一支（W2325）没有测试，孤立检出的测试靠 S343–352 自己的检查通过，遮住了它。
3. **“不写对象”只测了指纹相等。** 指纹不等的一支（W2349 的 `write=False`）退回写对象，两条测试都不失败。计划只要求相等的情形，但两支是一起改的。

另有一条是照着实现写的：`test_refused_started_reclaim_preserves_every_plan_column_and_the_capture_fence` 断言续做被拒后计划各列不变、围栏还在，却没有断言这个状态怎么出来，等于把反例 1 钉成了要求。`MockWorkspace.integration_verify` 的 `_confirm_adjustments` 也是照实现写的，好在有上面那条真 Git 的测试兜着。

## 5. 顺带

- `_reset_site`（W1612–1613）对输入树与暂存树各列一遍，与 W1100 改掉的是同一处重复。
- `_finish_record`（W1650）对刚建成的决议记录再问一次树，与 `_finish_output` 改掉的相同。
- `_cleanup_apply_precheck`（F5440）在读事务与写事务里各跑一遍，每遍都重算 `resolve_allocation` 与 `_cleanup_reasons`；`cleanup_plan` 刚算过第三遍。
- `cleanup_inspect`（W2256）与 `cleanup_remove`（W2399、W2406）背靠背各做一次 `inspect` 与 `worktree list`。
- `_adopted_board_paths`（F4173）对每条采纳记录跑一次完整的 `_artifact_binding`，回收证明里 W2337 又跑一次。
- 孤立检出的 S343–352 对四个引用各问两次，其中两个与 W2325 重复。
- 非首回合的 `cumulative_patch`（W1301）新加的 `base == artifact["inputCommit"]` 分支几乎走不到：传入的是完整提交号时 W1285 已经返回，只有传入别的写法才会到这里。
