# 回收中断后的续做说明

本回合在开发者机器断电后继续原微任务，看到的是 `reconstructed-new-session`（重建的会话），不是原生会话接回；先核对了基点 `8eb31e5c` 之后留下的三个文件，部分产物的测试与完成状态没有被当作既有证明。

只有计划已经是 `applying` 时，`workflow.py` 的 `cleanup_apply` 才把续做事实传给 `workspace.py`；从 `planned` 开始的证明仍走原来的身份与内容检查，少了封存文件仍以 `unsealed-changes` 拒绝，计划阶段的比对与正常 Git 调用未改写。

身份先由 `resolve_allocation` 用看板保留的输入、分配自己的不可变清单、仓库与检出标识、受管分配的精确路径共同证明；随后 `_cleanup_resume_registration` 要求仓库仍登记该路径，且原检出标识对应的登记目录用 `gitdir` 指回该路径的 `.git`。锁不在时，用物理分配自己的 `hey-my-buddy:<workspaceId>` 原因重新锁上，再检查内容；锁属于别人时，保留原锁并以 `lock-mismatch` 拒绝。续接使用最初物理分配的标识，不借用后续逻辑工作区的标识。

检出自己的 `.git` 文件不在时，身份由仓库登记及分配清单证明；若它还在，则必须确实指向原登记目录。Git 的删除命令要求这个双向指针存在，因此仅在内容证明通过、进入 `cleanup_remove` 后补回缺失的 `.git` 指针，再解锁并删除；内容被拒绝时不补指针、不删除内容。仓库已无该路径的登记而目录还在时，以 `unregistered-checkout` 拒绝，保留目录；不通过猜路径或重新登记取得删除权。Git 删除失败而目录仍在时，同样用登记证明恢复本分配的锁，支持删除失败时 `.git` 也已经不在的现场。

`_cleanup_remaining_changes` 枚举仍在目录中的文件，包括 Git 忽略的文件，不跟随目录链接，并把文件内容的 Git 对象标识与模式同最新封存的完整树逐一比较；没有输出封存时，比较已固定引用保留的输入树。已经批准的越界路径由经过原有绑定验证的封存树携带。只少了文件，指每个仍在的文件都在该树中且模式与原始字节一致，树里有而目录里没有的文件允许缺失；任何新增文件、修改、模式变化或链接变化都拒绝，不把 Git 忽略规则当作删除未封存内容的授权。目录本身不作为 Git 文件，目录链接按文件比较。登记目录中的索引与 HEAD 也必须同封存已有的 `indexSha256`、`headCommit` 相符，没有输出时则同已固定的输入 HEAD 与索引树相符；即使文件字节已经恢复，也不删除后来只写入索引的内容或移动后的 HEAD，差异以 `.git/index`、`.git/HEAD` 命名报告，缺少检出的 `.git` 文件不会跳过这项证明。

内容拒绝返回 `unsealed-changes`、检出路径与有界的 `unsealedPaths`；`applying` 计划的每一列、既有证据与保留清单保持原值，捕获、写者与续接共用的回收围栏继续生效。处理掉新增或修改的内容后，同一个计划可再续做。完成时沿用现有事务将计划记为 `applied`，只写一次 `workflow.cleanup_applied`，`alreadyRemoved` 表示这次执行是否发现检出已经消失；后续重放直接返回原计划结果与保留清单，不因如今路径已消失而改写这次执行的事实。

存储形式没有变化，未修改 schema 20、任何数据库表或已在日常看板消耗的 19 到 20 升级。修改限于回收源码、相应测试及本说明，没有安装或处理日常看板的受阻检出。

测试均使用 Host 指定的解释器运行 `PYTHONPATH=src <指定解释器> -m hey_my_buddy.cli.checks <测试文件或目录>`，保留默认进程数与运行器建立的私有根；没有直接启动测试进程、运行完整检查、运行 `tests/probes/` 或调用真实 harness。

最初单独运行 `tests/python/blackboard/tasks/test_workspace_lifecycle.py`，56 项中有 3 个错误（断电前部分改动的两处测试断言错误，以及缺少 `.git` 时 Git 拒绝删除），退出 1；修正断言并补齐要求场景后的单文件重跑执行 61 项，有 2 个错误，均为尚未补回 `.git` 指针导致的同一删除失败，退出 1。为定位这一点，单独运行 `tests/python/blackboard/tasks/test_workspace_boundaries.py`，11 项中有 1 个同因错误，退出 1；补回经过证明的指针后单文件重跑，11 项全部通过，退出 0。这些是实现与测试中的确定性缺陷，没有归作机器忙导致的时序失败；各次私有根均由运行器清理。

第一轮受影响检查覆盖 `tests/python/blackboard/tasks/`、`tests/python/blackboard/store/`，以及 `tests/python/cli/test_cli.py`、`tests/python/console/test_console.py`、`tests/python/install/test_upgrade_waiting_host.py`、`tests/python/blackboard/routing/test_router_boundary_integration.py`、`tests/python/test_private_directories.py`，55 个文件共 954 项全部通过，退出 0，私有根已清理。随后为审查发现的仅索引变化场景增加反例，单独运行 `test_workspace_boundaries.py`，14 项中该反例失败，证明旧的续做只比剩余文件会错误放行，退出 1，私有根已清理；补上索引与 HEAD 比对后，重跑相同受影响范围以验证最终源码，55 个文件共 956 项全部通过、无跳过，退出 0，私有根已清理，其中生命周期文件用时 193.1 秒、边界文件用时 25.3 秒。`git diff --check` 通过；没有运行完整检查，Host 在整合之后统一运行。

与要求不一致的情形：没有；要求未覆盖而影响本次交付的情形：没有。
