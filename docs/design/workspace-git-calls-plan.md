# 工作区路径的 Git 调用清点与削减方案

最大的三类可削减调用是 index 的重复读取、私有 index 的空初始化、相同快照树的重复构造；按互不重叠的预算，7 个重文件合计可少 4,536 次，占本轮 36,332 次的 12.5%。

2026-10-14；冻结源码 `784e88398dbbabc7e47825155f8988bf31a8e7ff`。本微任务只交付本方案，源码、测试、检查器与夹具未改，没有提交、安装或日常服务操作。下面是待 Host 审阅的产品改动方案；两轮通过的是原源码，削减次数和秒数都是推算，未取得候选实现或三个系统上的验收。另一个微任务删除的工作区身份别名与迁移报告不作为本方案的前提或改动点。

## 1. 实测方法与逐文件计数

按指定顺序读了 AGENTS.md、CONTEXT.md、检查提速方案的开头及第 2、4、5 节、Host 二审记录，以及 workspace.py、workspace_identity.py、storage.py 和 workflow.py 的准备、结果预查、续接、验收与回收路径。使用 Host 指定的既有解释器；下文 `$PYTHON` 代表它，不创建或修改解释器环境。两轮都由检查器按默认 4 个进程运行以下同一组 9 个文件，231 条测试、0 跳过、退出 0，私有根均由检查器确认清理。只跑这些文件，没有完整检查。

```sh
PATH="$MEASURE/bin:$PATH" WG_LOG_ROOT="$MEASURE/logs" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON" \
  -m hey_my_buddy.cli.checks \
  tests/python/blackboard/tasks/test_workspace_lifecycle.py \
  tests/python/blackboard/tasks/test_workflow_preparation.py \
  tests/python/blackboard/tasks/test_storage_orphans.py \
  tests/python/blackboard/tasks/test_scope_recovery.py \
  tests/python/blackboard/tasks/test_workspace.py \
  tests/python/blackboard/tasks/test_workflow_real.py \
  tests/python/blackboard/tasks/test_workspace_submission_cleanup.py \
  tests/python/blackboard/tasks/test_workspace_stash.py \
  tests/python/blackboard/tasks/test_storage.py
```

`$MEASURE` 是检出内本微任务创建的独占临时目录。先解析真实 git，再将一个 Bash 转发脚本放在 PATH 最前面。脚本仅记录参数、诊断关联编号与检查器的私有 TMPDIR，随后 `exec "$REAL_GIT" "$@"`；不读 stdin、不改变 stdout/stderr、工作目录、环境中的 Git 选项或退出码。每个父进程写独立日志，记录参数用 Bash `%q`，原始调用栈另用 JSON 保存，避免把换行或非 UTF-8 参数重新按 shell 解释。

```sh
#!/bin/bash
printf -v wg_args '%q ' "$@"
printf '%s\t%s\t%s\n' "${WG_TRACE_ID:-unattributed}" "$TMPDIR" "$wg_args" \
  >> "$WG_LOG_ROOT/args-$PPID.log"
exec "$REAL_GIT" "$@"
```

第一轮同时通过临时 sitecustomize 记录实际 Git Popen 的源码栈，并给同步 subprocess.run 记墙钟；第二轮另记 `_tree` 输入 entry map 的规范 JSON 指纹，仅用来判断三个快照树中哪些输入相同。诊断从临时启动目录加载，其临时 src 入口指向同一源码；检查器仍通过解析自身文件位置锁定真实检出，仍给 unittest 子进程设置原有的私有根并净化身份与凭据变量，额外的临时诊断路径只负责日志。不修改被调用函数的实参、返回值、异常或测试断言，不替代 Git，也没有模型调用。命令计数以转发脚本实际收到的调用为准，调用栈只负责归类；第一轮 37,337 条栈记录与 3 条无栈的测试夹具命令组成 37,340 次实发调用，3 条是一次私有 worktree detach 和两次私有 stash store，未归入产品削减。stash 写操作都由原测试在私有仓库内执行，没有对源检出手工使用 stash。

两轮逐文件计数完全一致。计时受诊断开销和同机负载影响：有额外指纹诊断的第二轮反而更快，不能将两轮时间之差叫作产品提速。原提速方案的 36,102 次来自 `398a275e` 的同步 Git 剖析；本轮冻结提交和“所有实际 Git 进程，含 fast-import”的计数边界不同，不混用两个分母。提交失败清理文件的 2,488 次与 Host 的转发计数复现一致。

| 文件（均在 `tests/python/blackboard/tasks/`） | 测试数 | Git 次数（两轮相同） | `rev-parse` | `ls-files` | 第一轮／第二轮文件秒 |
| --- | --- | --- | --- | --- | --- |
| `test_workspace_lifecycle.py` | 49 | 13070 | 6100 | 2091 | 567.3／432.4 |
| `test_workflow_preparation.py` | 30 | 6116 | 2740 | 1290 | 312.7／208.1 |
| `test_storage_orphans.py` | 36 | 6101 | 2600 | 1240 | 295.5／202.0 |
| `test_scope_recovery.py` | 18 | 2822 | 1211 | 618 | 150.7／99.3 |
| `test_workspace.py` | 26 | 2883 | 1244 | 677 | 142.0／96.3 |
| `test_workflow_real.py` | 11 | 2852 | 1253 | 648 | 144.4／96.6 |
| `test_workspace_submission_cleanup.py` | 20 | 2488 | 1065 | 464 | 118.2／79.2 |
| `test_workspace_stash.py` | 17 | 1008 | 357 | 60 | 62.0／38.2 |
| `test_storage.py` | 24 | 0 | 0 | 0 | 4.5／2.7 |
| 7 个重文件合计 | 190 | 36332 | 16213 | 7028 | 1730.8／1213.9 |
| 9 个文件合计 | 231 | 37340 | 16570 | 7088 | 1797.3／1254.8 |

文件秒是文件进程的墙钟之和，不是默认 4 个进程的整批墙钟。存储一般路径的 24 条测试没有发出 Git；需要 Git 的孤立工作区证明在 test_storage_orphans.py 和生命周期文件里。

## 2. 产品操作与调用点清单

同一产品操作的命令数随新建、重放、只读、脏输入、封存失败、续接与删除竞态分支改变。下表给 9 个文件整个批次的实发总量，包含正面与负面路径；没有把静态调用数冒充每次操作的实测均值。按调用栈优先归入外层 storage.plan/apply、stash 观察、续接、提交回滚，其余归入 workspace 的入口；各行互斥，可相加。验收的比对与随后的回收分列，封存包括 Worker seal 与 Host seal。

| 操作 | 合计 | `rev-parse` | `ls-files` | 其余命令与次数 |
| --- | --- | --- | --- | --- |
| 准备检出（含捕获输入） | 15673 | 6179 | 3210 | `ls-tree` 427；`cat-file` 420；`read-tree` 948；`update-index` 795；`write-tree` 795；`diff` 532；`symbolic-ref` 1079；`update-ref` 1062；`fast-import` 7；`commit-tree` 23；`worktree` 196 |
| 封存 | 5912 | 2556 | 1439 | `cat-file` 202；`ls-tree` 359；`read-tree` 138；`update-index` 138；`write-tree` 138；`diff` 138；`symbolic-ref` 280；`update-ref` 276；`fast-import` 143；`commit-tree` 105 |
| 续接准备与验证 | 3384 | 1684 | 777 | `ls-tree` 41；`cat-file` 41；`read-tree` 123；`update-index` 123；`write-tree` 123；`commit-tree` 35；`diff` 80；`symbolic-ref` 160；`update-ref` 160；`worktree` 32；`fast-import` 5 |
| 固定产物、累计补丁与验收比对 | 1811 | 1110 | 0 | `diff` 81；`rev-list` 65；`diff-tree` 189；`ls-tree` 279；`merge-base` 87 |
| 回收检查与移除 | 3182 | 1507 | 450 | `worktree` 173；`for-each-ref` 152；`diff` 72；`rev-list` 70；`diff-tree` 149；`ls-tree` 303；`cat-file` 89；`fast-import` 4；`read-tree` 71；`update-index` 71；`write-tree` 71 |
| 独立输入验证 | 554 | 362 | 192 | 无 |
| scope 现场观察与恢复 | 866 | 355 | 273 | `cat-file` 27；`ls-tree` 67；`read-tree` 16；`update-index` 16；`write-tree` 16；`commit-tree` 16；`diff` 16；`symbolic-ref` 32；`update-ref` 32 |
| 独立仓库／工作区身份解析 | 81 | 81 | 0 | 无 |
| 存储回收计划 | 1464 | 666 | 306 | `worktree` 44；`for-each-ref` 88；`diff` 2；`rev-list` 2；`diff-tree` 4；`ls-tree` 8；`cat-file` 2；`read-tree` 2；`update-index` 2；`write-tree` 2；`show-ref` 168；`symbolic-ref` 168 |
| 存储回收执行 | 1708 | 791 | 328 | `worktree` 60；`for-each-ref` 86；`diff` 13；`rev-list` 8；`diff-tree` 16；`ls-tree` 35；`cat-file` 10；`read-tree` 14；`update-index` 14；`write-tree` 14；`show-ref` 148；`symbolic-ref` 158；`update-ref` 10；`fast-import` 1；`commit-tree` 1；`merge-base` 1 |
| stash 观察 | 697 | 682 | 0 | `reflog` 15 |
| 提交失败后的回收 | 311 | 148 | 56 | `worktree` 27；`for-each-ref` 16；`show-ref` 32；`symbolic-ref` 32 |
| 独立孤立分配证明 | 231 | 105 | 49 | `worktree` 7；`for-each-ref` 14；`show-ref` 28；`symbolic-ref` 28 |
| 夹具及直接 helper 调用 | 1466 | 344 | 8 | `init` 54；`config` 106；`add` 321；`commit` 107；`status` 9；`diff` 8；`stash` 230；`for-each-ref` 13；`worktree` 14；`clone` 41；`apply` 44；`update-index` 127；`checkout` 1；`switch` 4；`ls-tree` 12；`update-ref` 3；`cat-file` 3；`show` 6；`gc` 3；`rm` 1；`symbolic-ref` 2；`show-ref` 5 |

下面展开被上表包含的 helper；这些数字有嵌套，不能再加到操作总量上。身份解析只指 workspace.inspect，workspace_identity.stable_identity 用路径解析、inode 与 SHA-256，本身发出 0 次 Git；别名与迁移报告相关的 SQL 不计作 Git，也不在本方案优化范围。

| helper／源码位置 | Git 合计 | 实发命令 |
| --- | --- | --- |
| `inspect`（workspace.py） | 5562 | `rev-parse` 5562 |
| `_snapshot`（workspace.py） | 7858 | `rev-parse` 1872；`ls-tree` 312；`ls-files` 1908；`cat-file` 308；`read-tree` 924；`update-index` 924；`write-tree` 924；`diff` 616；`commit-tree` 58；`fast-import` 12 |
| `_stable_observation`（workspace.py） | 11432 | `rev-parse` 4261；`ls-files` 6492；`cat-file` 560；`fast-import` 119 |
| `_artifact_binding`（workspace.py） | 2446 | `rev-parse` 1509；`diff` 150；`rev-list` 145；`diff-tree` 299；`ls-tree` 343 |
| `_orphan_workspace`（storage.py:315） | 3079 | `rev-parse` 1396；`worktree` 93；`for-each-ref` 186；`ls-files` 652；`show-ref` 376；`symbolic-ref` 376 |

身份解析正常一次是 4 条 rev-parse：169 行 show-toplevel、170 行 absolute-git-dir、171 行 absolute common-dir、178 行经 `_commit` 验证 HEAD。本轮三条定位查询各为 1,392／1,390／1,390 次，HEAD 查询 1,390 次；两次在首条失败。捕获一次 `_observe`（434–467）重新问 HEAD，经 `_entries` 读取 stage 和 skip-worktree，再枚举非 ignored untracked，必要时单列显式选中的 ignored 文件，最后为已读字节查询对象格式；write=True 还查询已有对象，缺失时运行一个 fast-import。`_stable_observation`（470–473）完整执行两次；封存（1245）还在建树后执行第三次 `_observe`，这三次文件、index 与 HEAD 的读取都保留。

准备路径为 workflow.submit（1640）→ submission_preparation／_prepare_locked（898／975）→ inspect、_snapshot（629）、四个固定／retained ref 的 `_pin`，再 _prepare_worktree（708）的 worktree add、一次批量对象读取与检出 read-tree，最后重新 inspect 和稳定观察；重放只验证原固定输入。封存走 `_seal_output`（1197）：输入 verify、稳定观察、scope 比对、私有树构造、建树后复查、commit-tree／diff、retained ref 的 CAS 与 `_finish_output`（1143）的固定产物校验。workflow.precheck_result（6420）只导入符合 attempt、manifest、shutdown 绑定的结果／产物证明，不能因为少跑 Git 而省掉它。

续接在 workflow.prepare_continuation_workspace（6126）于读事务中捕获任务、run、reservation 与停止证据，事务外 verify／recovery_inputs／prepare，再在原有读、写边界比对完整 snapshot。验收为 workflow.accept（3698）→ _accept_verify（3907）→ integration_verify（1974）：先 `_artifact_binding` 验证固定输入和最终产物，再 inspect 真实目标、解析 before/after commit 与树、逐路径比较 mode/OID、核实祖先区间；adjusted 的第二次比对保持独立现场读取。验收写入后 `_reclaim_checkout`（4527）另走 cleanup_plan/apply，不能把比对目标的信息当作受管源 worktree 的删除证明。

回收为 cleanup_inspect（2204）→ 新的 inspect、worktree list、_allocation_refs、固定产物与现场稳定观察；真正删除为 cleanup_remove（2339）→ 再次 inspect 和 worktree list，确认原物理分配、原 buddy 锁与 detached 状态，然后 unlock/remove；失败仍按原路径身份复查并尝试重新 lock，路径仍存在则保留错误。storage.plan（744）调用 inspect（420）；storage.apply（753）重新 inspect，并在孤立分配锁与数据库 writer fence 下经 `_remove_orphan_workspace`（381）再证明记录、refs、历史引用、inode 与指纹，最后调用 cleanup_remove。计划与执行之间这些重复读取属于新的授权时点。

孤立分配的 `_orphan_workspace` 还逐个对 input、staged、retained/input、retained/staged 发出 show-ref 与 symbolic-ref，共 376 + 376 次，并用 ls-files --others --ignored 检查遗留 ignored 内容。stash 观察 shared_stash_inventory（1362）先解析 common-dir，再以 --git-dir 验证 refs/stash，存在时读 reflog show；它可返回 unknown，不能把读取失败改为空列表。collect_claim_stash_facts（5688）已有同一收集 pass 的仓库去重；collect_result_stash_facts（5777）是另一个时点，原始 start 与 seal 的两个观察都保留。

## 3. 削减候选、相同答案的依据与不跨越的边界

只考虑单次调用内已读的不可变对象与可合并的一次边界读取。A、B、C 是先实施的三类；D、E、F 是后续小收益候选。下表的次数来自 9 文件日志；C 的相等条件额外由第二轮输入指纹实测。B 已覆盖所有树的初始化，C 因而只扣其余 update-index/write-tree，D/E/F 也按相同调用的身份去重，不能把同一次删除算两次。固定产物计数按一次 `_artifact_binding` 的入口与返回分段，连续的两个调用也分开，不把跨调用重复算作局部收益。

| 项 | 候选／调用点 | 本轮可省次数 | 占 9 文件调用 | 计数依据 |
| --- | --- | --- | --- | --- |
| A | 合并 index stage／标记查询；workspace.py:395–415 `_entries` | 2297 | 6.15% | 每次 `ls-files --stage -z` + `ls-files -t -z` → `ls-files --stage -t -z` |
| B | 移除新私有 index 的空初始化；workspace.py:475–485 `_tree` | 1159 | 3.10% | 每次独占的新 index 路径省去 `read-tree --empty` |
| C | 复用相同 entry map 构造的树；workspace.py:642–644 `_snapshot` | 1116 | 2.99% | 308 次三树捕获、924 次构造中，558 次 entry map 与同次捕获前一个构造相同 |
| D | 复用固定 commit 的 tree OID 答案；workspace.py:1918–1919、1942 `_artifact_binding` | 433 | 1.16% | 只对相同已验证十六进制 commit OID 的 `^{tree}` 答案复用 |
| E | 复用相同固定树差异／路径答案；workspace.py:1887–1898、1955–1958 | 310 | 0.83% | 同一次 `_artifact_binding` 激活内相同 tree pair 和 after-tree/path 不重复问 |
| F | 按固定树批量查询路径；workspace.py:1897、2038 `_tree_entry` 循环 | 41 | 0.11% | 原单路径 ls-tree 按同一固定树、非重叠路径分块；只计 D/E 后仍多出的调用 |

A 的两条命令分别回答“index 中每个路径的 mode/OID/stage”和“是否有 skip-worktree”。观察指纹、scope 比对、恢复的 CAS 与源 index 保持依赖它们。Git 官方 `ls-files` 文档的 stage 输出允许前置 tag；本机探针也确认 `--stage -t -z` 返回 `H mode oid stage<TAB>path<NUL>`，因此同一次 index 读取可给出两个答案。[Git ls-files](https://git-scm.com/docs/git-ls-files) 解析必须按 NUL 和第一个 tab 分割，保留原始路径字节，并保留原错误优先级：遍历完 stage 记录的 mode、不支持项与非 0 stage 检查后，再报告任意 S 标记；不能见到 S 立即短路而盖掉 WORKSPACE_UNMERGED。每个原 `_entries` 调用仍执行新的 Git 查询，不把一轮 index 沿用到下一轮；合并使 stage 与标签来自同一次扫描。

B 当前 read-tree 只回答“这个新临时 index 从空开始”，不读取源 index；_tree 每次创建新的 TemporaryDirectory，GIT_INDEX_FILE 明确指向其中原本不存在的绝对路径。保留这条隔离条件与 `_git_command` 对继承 GIT_* 的净化后，非空 entries 的 update-index --index-info 会创建该私有 index，空 entries 则直接 write-tree；后者仍由 Git 检查引用对象存在性。[Git update-index](https://git-scm.com/docs/git-update-index)、[Git write-tree](https://git-scm.com/docs/git-write-tree) 本机 Git 2.54.0 的私有探针对空／非空输入得到与旧流程完全相同的 tree OID，也覆盖含换行的文件名；这是局部依据，文档未显式保证所有版本的“缺失 index 等于空 index”。实施微任务须先在 Linux、macOS、Windows 的受支持 Git 上通过同样的私有探针，未通过就保留此调用。检出初始化的 read-tree（727）和恢复的真实 index 写入不受这项削减影响。

C 当前 `_snapshot` 分别给已读 staged、tracked、tracked+included entry map 构造树，依赖方为 stagedCommit、inputCommit、两段补丁哈希和后续 worktree materialization。改为同一次 `_snapshot` 中仅对不同 entry map 调用 `_tree`；比较完整 path→[mode,OID] 字典，不能用“clean”“没有 changedPaths”或空选择器猜相等。所有首次 `_tree` 的排序、原始 index-info、对象存在性检查与返回 OID 保留；只复用刚刚构造出的同内容树，仍分别组装 staged/input 元数据和原来的 diff。308 次捕获的 924 张 map 中 558 张是重复输入，当前每张消耗 read-tree/update-index/write-tree 共 3 次：单做 C 可少 1,674 次，先做 B 后 C 只再省 1,116 次。可变文件、HEAD、index 与所选 untracked 的两次稳定观察仍完整执行；不跨另一捕获、另一个 repository 或一次重启复用。

D 当前逐次从固定 commit OID 解析 tree OID，并将答案分别与原始、最终 manifest 和产物的各自 tree 字段比对。可以把本次 `_artifact_binding` 已验证的相同 commit OID 的解析答案沿用，但每个 manifest 的 schema/hash/身份与每个预期 tree 的比较都仍执行；不能因为两个 manifest 相等就跳过其整段检查。E 同理只沿用本次函数内由固定 before/after tree OID 导出的差异或 after-tree/path 的 mode/OID/缺失答案，保留两段 changedPaths 的独立比较和上限检查。内容寻址对象的答案不会因为另一个进程改 HEAD/index/ref 而改变；可变 ref 的解析一条不减，manifest／artifact 的 expected 字段也不成为答案来源。可复用值不越过 `_artifact_binding` 返回、第二轮 adjusted 比对、计划／执行或新的身份解析；当前对象缺失、损坏、diff hash 不符仍在原有 Git 与产物边界失败，不通过已有值给新操作兜底。

F 当前 `_tree_changes` 先用 diff-tree 得到最多 512 个路径，再每个路径 ls-tree；integration_verify 的目标循环再逐个问同一个已经解析到固定 OID 的 after commit。可分别一次或按参数字节预算分块执行 `ls-tree -z --full-tree <oid> -- <literal paths…>`，结果按原请求的完整路径取 mode/OID，未出现的路径保持 None；同一批路径必须互不为祖先，出现 dir 与 dir/file 就拆批，否则 Git 会省掉其中一个被请求的目录项。本机私有探针已经验证该边界与非重叠路径的输出，计数也按拆批后重算。不能直接加 -r：它会把“原文件现在是目录”误读为不存在；即使再加 -t，会额外遍历原单路径查询不访问的子树，扩大输出和失败边界，因此本方案保持不递归。[Git ls-tree](https://git-scm.com/docs/git-ls-tree) 不要用全树 `_entries` 替代，否则会额外拒绝未变的 submodule。必须保留 path normalization、重复／512 上限、删除项、文件与目录变换、symbolic link/executable 模式、特殊字节与原始名字匹配，并处理 Windows 的命令行长度；若 chunk 包含目录，只接收原请求的精确路径，不能把其子路径当该目录的答案。先做 E，再对仍需要的路径读取批量化，本轮才额外省 41 次。这里沿用的是一次已解析的 after OID，不沿用下一次验收中的 ref/HEAD。

这些微任务不改变 workspace.py 对停止证据的前置条件。Worker 停止、Host revision／reservation／dependency fence、固定 ref 的 compare-and-create、输出 manifest 与 patch 的不可变绑定、allocation 的路径/inode/buddy 锁、删除前现场证明和失败后 relock 都原样保留。用户自己的 Git 配置仍由真实 Git 读取，现有禁止 hooks/fsmonitor/untrackedCache/splitIndex、literal pathspec、no replacement object、原始字节与不运行过滤器的命令设置继续沿原路径执行。

## 4. 推算的调用与秒数收益

调用预算依照 A→B→C→D/E→F 的顺序扣除，计数互不重叠。秒数使用第一轮对应调用点的同步 Git 墙钟：A/B/D/E/F 按被删调用的原耗时求和；C 用该文件 `_tree` 的 update-index/write-tree 均值乘实测重复 map 数。它们包括转发与栈诊断开销，假定新批量命令增加的输出／解析成本不大，因此是预算，不是准确的下限或候选提速实测。第二轮时间明显变化说明负载未固定，不给置信区间，也不承诺默认 4 个进程的整轮时间。

| 7 个重文件 | A | B | C（B 后） | D/E/F | A/B/C 合计 | 全部候选合计／占比 | 推算少文件秒 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `test_workspace_lifecycle.py` | 698 | 364 | 268 | 702 | 1330 | 2032／15.5% | 81.7 |
| `test_workflow_preparation.py` | 418 | 247 | 234 | 0 | 899 | 899／14.7% | 43.0 |
| `test_storage_orphans.py` | 387 | 168 | 220 | 15 | 775 | 790／12.9% | 36.3 |
| `test_scope_recovery.py` | 208 | 80 | 78 | 62 | 366 | 428／15.2% | 21.3 |
| `test_workspace.py` | 216 | 96 | 90 | 0 | 402 | 402／13.9% | 19.0 |
| `test_workflow_real.py` | 200 | 107 | 104 | 0 | 411 | 411／14.4% | 19.9 |
| `test_workspace_submission_cleanup.py` | 150 | 89 | 114 | 5 | 353 | 358／14.4% | 15.6 |
| 合计 | 2277 | 1151 | 1108 | 784 | 4536 | 5320／14.6% | 236.9 |

下表按第二节的同一操作边界给本批次的收益，包含测试中的分支和失败路径，不能解读成“一次准备省这些秒”。未有削减的身份解析、stash 观察与无 Git 的存储一般路径仍列为零。捕获输入是准备／续接的子路径，另在表后给其局部预算。

| 操作 | 原 Git 次数 | 推算少调用 | 推算少文件秒 |
| --- | --- | --- | --- |
| 准备检出（含捕获输入） | 15673 | 2877 | 130.6 |
| 封存 | 5912 | 612 | 27.3 |
| 续接准备与验证 | 3384 | 449 | 21.1 |
| 固定产物、累计补丁与验收比对 | 1811 | 370 | 15.6 |
| 回收检查与移除 | 3182 | 585 | 24.7 |
| 独立输入验证 | 554 | 60 | 2.6 |
| scope 现场观察与恢复 | 866 | 112 | 5.1 |
| 独立仓库／工作区身份解析 | 81 | 0 | 0.0 |
| 存储回收计划 | 1464 | 100 | 4.1 |
| 存储回收执行 | 1708 | 159 | 6.0 |
| stash 观察 | 697 | 0 | 0.0 |
| 提交失败后的回收 | 311 | 16 | 0.7 |
| 独立孤立分配证明 | 231 | 14 | 0.6 |
| 夹具及直接 helper 调用 | 1466 | 2 | 0.1 |

捕获输入 `_snapshot` 子路径本轮 7,858 次调用中，A 实测可合并 624 次、B 可少 924 次、B 后 C 再少 1,116 次，共 2,664 次；按相同耗时方法推算约 122.1 个文件秒，已包含在准备／续接的表内，不能重复加。

每次完整三树捕获当前为 9 次构树 plumbing；令 d 为三个 entry map 中不同 map 的数量，B 后为 6 次，B+C 后为 2d 次，因此构树局部省 9−2d 次，d=1/2/3 分别省 7/5/3 次。还可每个原 index 读取省 1 次 A，首次与第二次 `_observe` 都保留。封存的每个独立树只省 1 次 B；验收的 live target inspect 与 ref 解析不省；回收每个原观察省 A 与其构树的 B/D/E/F，但仍重新执行 plan、apply、remove 各个原边界。本轮捕获的不同 map 数已实测，不能用“所有输入都 clean”把 d 一律写成 1。

7 文件 A/B/C 的预算为 4,536 次、约 204.6 个文件秒；再加 D/E/F 为 5,320 次、约 236.9 个文件秒。9 文件的对应总预算为 4,572 次和 5,356 次；stash 测试只因其准备／封存部分省 36 次，stash 观察自身省 0。将文件秒除以 4 只能表示理想吞吐的量级，不能代替调度或整轮测量；本轮只支持约 12.5%／14.6% 的 7 文件调用削减，不能据此承诺从十二三分钟降到七分钟。

## 5. 实施微任务顺序与 Host 验收

建议串行修改同一个 workspace.py，每个微任务都冻结集成后的基线。源码候选可独立交付；测试文件、断言、循环、参数、异常与状态转换不改，原测试原样通过是行为保持的主要证据。Host 在私有副本／私有根中运行故障注入，不把变异放进交付源码，也不通过改测试使候选通过；注入失败必须命中预期产品拒绝或断言，导入失败、超时与环境错误不算。新增实现授权、候选验收与最后安装属于 Host，本文只给范围和停止条件。

| 次序／微任务 | 修改范围与依赖 | 必须原样通过（文件均在同一 tasks 测试目录） | Host 的故障／边界注入 |
| --- | --- | --- | --- |
| 1：一次读取 index | 仅 `_entries` 与它的 NUL/tag 解析；A | test_workspace.py、test_workspace_git_errors.py、test_workspace_batch.py、test_scope_recovery.py、test_workspace_submission_cleanup.py、test_workflow_preparation.py | 在第一轮观察与第二轮之间改 index/stage／skip-worktree；分别放非 0 stage、稀疏目录项、submodule，再组合以检查错误优先级；换行/tab/非 UTF-8 文件名，Windows alias 仍拒绝 |
| 2：私有 index 从缺失开始 | 仅 `_tree` 空初始化；B，接步骤 1 | test_workspace.py、test_workspace_batch.py、test_workspace_git_errors.py、test_workspace_submission_cleanup.py、test_workspace_lifecycle.py、test_storage_orphans.py | 三个系统各用空／非空 entries 私有探针比旧新 tree 与源 index 字节；注入 update-index/write-tree 非零、超时、缺 blob；检出 read-tree 失败仍撤销新分配，已有源不被动 |
| 3：同次快照相同 map 复用 | 仅 `_snapshot` 的三次树构造；C，依赖 2 的预算 | test_workspace.py、test_workspace_batch.py、test_workflow_preparation.py、test_workflow_real.py、test_scope_recovery.py、test_workspace_submission_cleanup.py、test_workspace_lifecycle.py、test_storage_orphans.py | 分别 staged≠tracked、tracked≠tracked+included、三者全不同；二进制／模式／symlink、显式 ignored 输入；捕获中和物化后改变源内容／HEAD；源 HEAD/index/files 均保持 |
| 4：本次固定对象答案复用 | 仅 `_artifact_binding` 和 `_tree_changes` 内固定 OID 答案；D/E，不动可变 refs | test_workspace.py、test_scope_recovery.py、test_workspace_lifecycle.py、test_storage_orphans.py、test_workspace_submission_cleanup.py、test_workspace_git_errors.py | 两个 manifest 同 commit 却给不同 tree；变更 ref 为符号引用／不同 commit、patch／snapshot hash 损坏、父绑定错误、对象缺失；两轮 adjusted 比对之间改目标 ref；未验集成不可接受 |
| 5：固定树的多路径读取 | 仅 `_tree_entry` 两类循环和批量解析；F，依赖 4，收益很小可最后做 | test_workspace.py、test_workspace_batch.py、test_workspace_lifecycle.py、test_scope_recovery.py、test_workspace_git_errors.py；最终九文件合跑 | 删除／文件转目录／symlink 与 executable；多个及 512 路径、长参数强制分块、特殊字节、未变 submodule；恶意或截断批量输出必须失败；调整只允许原 changed paths |

每步的受影响文件用同一 checks 入口和默认进程数验证；最后在最终源码上运行本轮同一 9 文件命令，并补上上表列出的 batch/Git-errors/Windows 文件（Windows 文件应在实际 Windows 跑，不能把平台 skip 当作验收）。不要求改检查器、不增加并行度、不降低等待预算、不改测试 fixtures。前后转发计数应对上 A/B/C/D/E/F 实际减少量；计时用同负载窗口交替基线／候选，记录失败、退出码、跳过与私有根清理，候选仍失败则先查原因，不能以较少调用数接收。

删除安全另做共同验收：在 cleanup 计划后、apply 证明后与真正 remove 前分别引入 unsealed tracked/untracked 内容、换 checkout/git-dir 的 inode、移动或复制 `.git` 指针、撤掉登记、改 buddy 锁或附着 branch；在 storage 的历史引用扫描或 writer fence 边界添加任意状态的看板引用、改变 request/input/manifest；在 review 与 reclaim 之间 takeover，或令另一个原 owner 已完成同一路径删除。必须保持现有拒绝／保留／精确路径幂等收敛及 dangling symlink 判断，未知停止状态不授权封存或删除，remove 失败且路径仍在时保留原错误与 relock 证据。现有对应回归主要在 test_workspace_lifecycle.py、test_storage_orphans.py 和 test_workspace_submission_cleanup.py；本微任务只提出这些注入，没有声称执行过。

## 6. 未采纳的做法与保留的查询

三条身份定位 rev-parse 合并是大候选：本轮 1,390 组完整定位理论上可少 2,780 次，但不计入上述收益。本机实际验证 `rev-parse --sq --path-format=absolute --show-toplevel --absolute-git-dir --git-common-dir` 仍把这些路径选项逐行输出，`--sq` 没有为它们加可逆引号；含换行的合法仓库目录会把一个字段拆成多行。当前单字段 `_line` 只去掉最后一个换行，支持这种路径。没有验证出同时保留原路径字节、strict resolve、错误分类的分隔格式，因而不采用按 splitlines 拆三项、拒绝新路径形状或回退重问；本轮保留三条查询。[Git rev-parse](https://git-scm.com/docs/git-rev-parse)

不复用 inspect 的 HEAD、_observe 的 index/文件/untracked，也不删 `_stable_observation` 的第二次或封存建树后的第三次观察；它们检出用户或 Worker 在边界内的变化。不把一次 object-format 查询跨两次观察沿用：同路径仓库替换与对象格式变化仍交给原读取及身份检查处理，本轮 2,347 次 format 查询全留。源 inspect 与准备完成后的执行 inspect 不同，borrowed checkout 加锁前后两次物理证明不同，失败后 relock 的 inspect 也不同。

不将 `_artifact_binding` 的 original/final manifest 整段去重，不合并 `_pin` 的 symbolic-ref、现有 ref 与 compare-and-create；这些 refs 可由别的 Git 进程移动。即使原始与最终 manifest 是同一个字典，可变 ref 的每个现有时点仍验证。不用 update-ref --stdin 将多个固定 ref 的读写改为一笔新事务，因为会改变中断时已保留哪一组 ref、重放与冲突分支。storage 的 4 个 show-ref + 4 个 symbolic-ref 有一次 for-each-ref 全问的候选，但缺 ref、符号 ref、悬空／损坏 ref 的诊断与错误优先级尚未证明等价，本轮 752 次都留。

`_allocation_refs`（2196）有按 identifier 循环的 for-each-ref，并且初始 allocationId 与 manifestWorkspaceId 相同时会重复前缀；本轮孤立证明就产生 186 次 listing。一次批量多前缀会改变逐组达到 64 才停、最后排序去重截 64 的结果范围，也有并发 ref 变化与命令行长度边界；本轮不采纳，只列为后续可重新测量的诊断优化。不能把整个 storage inventory 的 worktree list 跨 candidate/apply/remove 共用，也不能把 plan 的 Git 结果作为 apply 的删除证明。

不合并 `_untracked` 的普通 others 与显式选择的 ignored 查询：普通列表覆盖全部非 ignored 文件，ignored 查询仅覆盖选中路径，改变 pathspec 或 ignored 标记会遗漏 scope 变化。也不因 working-tree 分支看似没用 base_entries 就删除初始 ls-tree，因为它仍会拒绝提交树中的 submodule／不支持 mode。相同 diff 的省略、refs/stash 存在性与 reflog 读取合并都尚未证明保持 Git 失败和 unknown/empty 的区别，故不计收益；stash start/seal 两个时点继续分别观察。

换成广泛使用的进程内 Git 库可消除一部分启动成本，但当前不采用。以 libgit2/pygit2 为代表，它提供 repository、index、对象、refs、worktree、config 等 API；pygit2 的安装说明列出 Python/cffi/libgit2 的版本耦合，适配 wheel 的平台可直接带 libgit2，没有匹配 wheel 则需要头文件与原生编译链，macOS、Linux、Windows 与各架构都要扩充私有安装／打包验证。[libgit2 官方 API](https://libgit2.org/docs/reference/main/)、[pygit2 官方安装说明](https://www.pygit2.org/install.html) 这引入第二套 Git 实现和升级周期，不能仅因 API 有同名功能就推定与用户安装的 Git 完全一致。

库路线需逐项重新证明用户的 system/global/local/worktree 配置及 include、core.worktree、index 格式扩展和 sparse/split index、worktree 的 git-dir/common-dir、锁与 refs/reflog、SHA-1/SHA-256、路径编码与 Windows 行为、mode/symlink、attributes/filters、原始字节和对象替换禁用的语义；已有 CLI 对环境污染、失败 argv/输出尾部、timeout 与实际子进程收束的证据也要改成库边界。纯读取切到库但写入保留 Git，会形成两套配置和现场读取的一致性问题；全部切换则扩大到固定 ref CAS、materialization 与回收。它有调查价值，当前缺乏等价性证据，也超出一次只削减调用的微任务范围，不能作为捷径。以上风险是本仓库契约带来的验证要求，未声称官方库必然不支持这些功能。

跨操作缓存不做：用户 checkout 的 HEAD/index/ignored 规则、Worker 写入、ref/stash 的共用修改、GC、目录与 inode 替换、服务重启及看板 revision/ownership 都能在下一次调用前改变；路径相同不等于同一 repository/checkout。缓存的失效事件会把本来由 Git 与现有 fence 负责的事实重新变成一套状态协议。本文只沿用当前函数中已经由真实 Git 回答过的固定 OID 或相同 entry map，不新增持久缓存、全局 LRU、mtime 快捷判断、WATCH 失效机制或跨宏任务／微任务共享状态。

临时计数脚本、探针与日志在交付前删除；交付树仅新增本文件。两轮原检查已通过并清理私有根；额外通过 checks 运行 test_repository_hygiene.py 的 5 条测试（0 跳过、退出 0、私有根已移除），并直接核对本文件的 UTF-8、个人路径、段落物理行、行尾空白与 Markdown code 标记，私有 Git 探针只验证了本机局部语义；未改实现、未测候选速度、未做 Host 故障注入、未运行完整检查或安装，也未作 Host 验收。
