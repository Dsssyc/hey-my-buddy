# 回收中断后的续做说明

第二回合在开发者机器断电后继续原微任务，看到的是 `reconstructed-new-session`（重建的会话），不是原生会话接回；先核对了基点 `8eb31e5c` 之后留下的三个文件，部分产物的测试与完成状态没有被当作既有证明。第三回合按 Host 的复审结果继续，收到的是 `native-session`（原生会话接回），基点仍是 `8eb31e5c`，没有合并其他提交。

只有计划已经是 `applying` 时，`workflow.py` 的 `cleanup_apply` 才把续做事实传给 `workspace.py`；从 `planned` 开始的证明仍走原来的身份与内容检查，少了封存文件仍以 `unsealed-changes` 拒绝，计划阶段的比对与正常 Git 调用未改写。

身份先由 `resolve_allocation` 用看板保留的输入、分配自己的不可变清单、仓库与检出标识、受管分配的精确路径共同证明；随后 `_cleanup_resume_registration` 要求仓库仍登记该路径，且原检出标识对应的登记目录用 `gitdir` 指回该路径的 `.git`，相对回指按登记目录解析后比对。锁不在时，用物理分配自己的 `hey-my-buddy:<workspaceId>` 原因重新锁上，再检查内容；锁属于别人时，保留原锁并以 `lock-mismatch` 拒绝。续接使用最初物理分配的标识，不借用后续逻辑工作区的标识。

检出自己的 `.git` 文件不在时，身份由仓库登记及分配清单证明；若它还在，则必须确实指向原登记目录。证明身份并补回锁之后，`_cleanup_restore` 先补回缺失的 `.git` 指针，再恢复缺失的已封存文件，使内容拒绝后的检出仍是可用 Git 查看、保留本分配锁的正常 worktree。仓库已无该路径的登记而目录还有内容时，以 `unregistered-checkout` 拒绝，不重新登记；若只剩根空目录，则允许续做，由 `rmdir` 原子地确认空目录并删除，新出现的内容会使删除失败。Git 删除失败而目录仍在时，同样用登记证明恢复本分配的锁，支持删除失败时 `.git` 也已经不在的现场。

续做不另写内容比对：`_cleanup_restore` 从最新封存树（没有封存时用输入树）只选择不存在的文件，沿不跟随链接的父目录逐层操作，复用 `_materialize` 独占创建普通文件或链接并恢复模式，已有路径不覆盖；恢复期间新出现的文件会使独占创建失败并被保留。随后原样执行计划时的 `inspect`、`_cleanup_proof` 与正常删除，已经批准的越界路径仍由看板传给原证明；`_cleanup_remaining_changes` 和 `_cleanup_proof` 内的续做分支已删除。只少了文件时，补回已保留的内容后能满足原证明；没被忽略的新增文件、已有文件的修改及原证明能观察到的其他变化仍拒绝。Git 忽略文件与计划时遵守同一规则，缓存不会单独卡住续做，已进入封存树的被忽略文件仍按原证明处理。

内容拒绝沿用原证明的 `unsealed-changes`、检出路径与有界的 `unsealedPaths`；恢复缺失的封存内容不删除或覆盖任何仍在的内容，`applying` 计划的每一列、既有证据与保留清单保持原值，捕获、写者与续接共用的回收围栏继续生效。处理掉新增或修改的内容后，同一个计划可再续做。成功的续做也不执行开始删除时的 UPDATE，第一次的证据、保留清单与 `command_id` 保持原样，只有完成事务将计划记为 `applied`、写入结果并推进一次修订，只写一次 `workflow.cleanup_applied`。`alreadyRemoved` 表示这次执行是否发现检出已经消失，后续重放返回原计划结果与保留清单。

存储形式没有变化，未修改 schema 20、任何数据库表或已在日常看板消耗的 19 到 20 升级。修改限于回收源码、相应测试及本说明，没有安装或处理日常看板的受阻检出。

测试均使用 Host 指定的解释器运行 `PYTHONPATH=src <指定解释器> -m hey_my_buddy.cli.checks <测试文件或目录>`，保留默认进程数与运行器建立的私有根；没有直接启动测试进程、运行完整检查、运行 `tests/probes/` 或调用真实 harness。

第二回合最初单独运行 `tests/python/blackboard/tasks/test_workspace_lifecycle.py`，56 项中有 3 个错误（断电前部分改动的两处测试断言错误，以及缺少 `.git` 时 Git 拒绝删除），退出 1；修正断言并补齐要求场景后的单文件重跑执行 61 项，有 2 个错误，均为尚未补回 `.git` 指针导致的同一删除失败，退出 1。为定位这一点，单独运行 `tests/python/blackboard/tasks/test_workspace_boundaries.py`，11 项中有 1 个同因错误，退出 1；补回经过证明的指针后单文件重跑，11 项全部通过，退出 0。这些是实现与测试中的确定性缺陷，没有归作机器忙导致的时序失败；各次私有根均由运行器清理。

第二回合第一轮受影响检查覆盖 `tests/python/blackboard/tasks/`、`tests/python/blackboard/store/`，以及 `tests/python/cli/test_cli.py`、`tests/python/console/test_console.py`、`tests/python/install/test_upgrade_waiting_host.py`、`tests/python/blackboard/routing/test_router_boundary_integration.py`、`tests/python/test_private_directories.py`，55 个文件共 954 项全部通过，退出 0，私有根已清理。随后为仅索引变化场景增加反例，单独运行 `test_workspace_boundaries.py`，14 项中该反例失败，退出 1，私有根已清理；当时补上索引与 HEAD 比对后，重跑相同受影响范围，55 个文件共 956 项全部通过、无跳过，退出 0，私有根已清理，其中生命周期文件用时 193.1 秒、边界文件用时 25.3 秒。这是复审前源码的测试记录，第三回合已用与计划相同的证明替换该比对；没有把该次通过当作本回合完成的证据。

## 第三回合：按复审结果修正

本回合修正了忽略缓存使续做受阻、接管换代使续做受阻、成功续做改写首次记录、相对 `gitdir` 回指被拒绝、无登记的根空目录不能删除五处问题。`_cleanup_apply_precheck` 只对 `applying` 免去旧 `ownerGeneration` 的相等要求，仍验证当前控制三元组、任务状态、分配、依赖与退出证据；`planned` 遇到接管仍以 `review-changed` 拒绝。相对 worktree 回指的测试在测试仓库启用 `worktree.useRelativePaths`，检查实际生成的相对回指后再续做。

被替换的“新增忽略文件必须拒绝”测试已删除，改为回收前已有忽略缓存、打断后续做删完；未忽略的新文件及被修改的已跟踪文件仍验证拒绝、不删除原有字节、恢复 `.git` 与缺失封存文件、保留计划每一列及围栏，处理后可再删完。另核对成功续做只推进一次修订、不改首次证据和 `command_id`，接管后旧控制三元组仍失效，以及缺失的二进制、模式、链接和父目录能恢复、恢复期间新出现的文件不被覆盖。

本回合只运行指定的 `test_workspace_lifecycle.py`、`test_workspace_boundaries.py`、`test_workspace.py`、`test_workspace_identity.py`、`test_workspace_submission_cleanup.py`、`test_storage.py`、`test_storage_orphans.py`（均在 `tests/python/blackboard/tasks/`），使用前述指定解释器与检查运行器、默认进程数；7 个文件共 191 项全部通过，无跳过，退出 0，私有根由运行器清理，其中生命周期文件用时 173.1 秒、边界文件用时 31.4 秒。本回合没有失败文件，没有触发失败文件的单独重跑；`git diff --check` 通过，没有运行完整检查。worktree 自己的引用与日志，以及证明到删除之间的空档，按 Host 划定的范围不在本回合处理；存储形式没有变化，也没有安装或处理日常看板。

与要求不一致的情形：没有；要求未覆盖而影响本次交付的情形：没有。
