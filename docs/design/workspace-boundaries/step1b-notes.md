# 工作区“只删不换”之后的四处：实现说明

2026-10-14，基于 `68e7d2e2`。下文 W 指 `src/hey_my_buddy/blackboard/tasks/workspace.py`，B 指新增的 `tests/python/blackboard/tasks/test_workspace_boundaries.py`，行号均为本次最终源文件的行号；修改前读了执行计划、独立审查及 Host 验收记录的最后一节。

第一处：W1290–1292 在首回合 `cumulative_patch` 验过封存记录之后，直接用 `snapshot.changedEntriesTruncated` 拒绝超限产物，错误为 `WORKSPACE_UNSUPPORTED`；不重读 Git，不改变封存、验收或回收的路径上限。B34 用上限 0 封存一次真实文件改动，禁止累计补丁读取 Git，断言拒绝且没有累计补丁文件；B63 通过 Worker 的 `partial_outputs.enrich` 检查状态变成 `failed`，记录 `cumulativePatchError`，保留为部分产物而没有成功的 `workspaceSeal`。

第二处已复现：B45 建立自有检出，先把文件的暂存内容改成另一份，再把工作树恢复为 HEAD 的内容，使用 working-tree 基线准备；输入提交仍是 HEAD，暂存提交和暂存树不同。清掉工作区引用并运行 `git gc --prune=now` 后，`cat-file --batch-check` 确认暂存提交为 `missing`，输入提交仍为 `commit`。原代码的 `verify(require_unchanged=False)` 没有报错；修复后报 `WORKSPACE_GIT_ERROR`。W860–862 只在 `kind == existing` 且暂存树不同于输入树时，按暂存提交号读取并核对树；封存也会在入口的 W1230 调用该检查。

第二处的省调用边界由 B80 守着：自有检出的暂存树等于输入树，以及 worktree 不使用源暂存区这两种情形，实际删除指向暂存提交的全部工作区引用、运行 GC、确认暂存提交消失之后，`verify` 仍通过，且没有对该暂存提交增加 Git 读取。初写这一测试时只删了 `stagedRef`，`retained/staged` 仍保护对象，两个子场景因对象没有消失而失败；测试已改成按提交号清除全部相关引用，再重跑通过，没有因此修改产品代码。

第三处：原有锁原因文字共替换 11 处，分布于 3 个文件。W 的 7 处运行代码是 W954（借用校验）、W997（准备）、W1029（准备回滚）、W2300（回收证明）、W2413（挂分支校验）、W2429（删除前校验）、W2447（删除失败后重新加锁），另有 W2171、W2229 两处说明字符串；`tests/python/blackboard/tasks/test_storage_orphans.py:62` 和 `tests/python/blackboard/tasks/test_workspace_lifecycle.py:1821` 各替换一处。写入和比对统一为 `hey-my-buddy:` 加分配标识。对 `src` 和 `tests/python` 的 Python 文件做了 `rg` 检查，旧前缀只留在 B197 的拒绝测试里；B190 同时检查新锁原因和旧锁原因被拒绝，没有换算或兼容读法。

第四处的对象缺失测试：B108 清引用并 GC，确认输入、暂存提交均消失，分别要求回合开始和封存拒绝；B45 另守输入仍在而暂存提交消失的情形。两条测试在 GC 后又写入尚未存入对象库的新内容，禁止调用 `_write_objects`，从而检查封存在写对象前就拒绝无效输入。临时把 W858 的输入对象条件改成 `False`，B108 的回合开始和封存子场景均失败；把 W860 的暂存对象条件改成 `False`，B45 失败；跳过 W1230 的封存入口 `verify`，B108 的封存子场景及 B45 均失败，随后逐次恢复源文件。

封存入口的变异验证曾暴露测试不足：最初只断言封存抛出 `BoardError`，跳过 W1230 后仍然通过，因为后面的读树也会拒绝缺失对象；加入上述新内容与禁止写对象的断言之后，恢复代码时通过，重新跳过 W1230 时失败。这个过程没有修改封存或回收的产品路径。

验收对象缺失由 B125 守住 W2015 的 `_artifact_binding` 边界，以及它调用的 W1970 输出提交对象读取：自有检出封存后，把相同内容提交到目标，使目标的树与封存树完全一致，再清工作区引用并 GC，确认封存提交消失而目标树还在，`integration_verify` 仍拒绝。临时把 W2015 改为只按已存在的树计算路径、跳过对象绑定时，B125 因不再报错而失败；恢复后通过。

逐个固定引用由 B136 的公共场景及 B166、B169 两条测试守住：有封存记录时分别只删除 input、staged、output 中的一条，没有封存记录时分别只删除 input 或 staged；其他引用仍指向各自的固定提交，拒绝时检出和文件原样，恢复该引用后重新变为可回收。临时从 W2316–2317 的预期集合中分别漏掉 input 和 staged，两个测试均失败；临时漏掉 W2319 的 output，B166 失败；分别绕过 W2331 的无封存引用判断、W2347 的有封存引用判断，B169、B166 各自失败，随后逐次恢复。

指纹不同的“不写对象”由 B172 守住 W2356 的 `write=False`：封存之后增加一个未封存且对象库没有的新文件，要求证明报 `unsealed-changes`、指出该路径、保留现场，同时 `_write_objects` 和 `_tree` 都不被调用，对象库清单前后相同。临时将 W2356 改成 `write=True`，测试在调用对象写入器时失败；恢复后通过。原有指纹相等分支的测试保留。

另外两组变异：把 W1290 的截断判断改成 `False`，B34 和 B63 都失败；把 W860 改成无条件读取暂存提交，B80 的 existing、worktree 两个子场景都失败。这次完整变异轮共 12 组，每组均用规定检查运行器跑整个 B 文件，都以退出码 1 和相应测试失败结束；每一组结束即恢复源文件，最终源文件不含变异。

没有量一个微任务一生的 Git 调用总数，不能给出新的实测总数。按代码路径，首回合截断判断和锁原因改名不增加调用；符合 W860 条件的每次 `verify` 增加一次 `rev-parse`，暂存树相同或执行检出为 worktree 时没有这次读取。主例的完整生命周期没有重新计数。

测试过程：新写的两条回归测试先在原实现上运行，均因预期错误没有抛出而失败；扩充到 10 条后，B80 的两个子场景因未清掉保留引用而失败，修正测试后单独重跑 B，10 条通过（25.4 秒）；加强封存入口断言后再单独运行，10 条通过（30.4 秒）。这些失败分别是预期的缺陷复现和已修正的测试前提，不是机器忙导致的时序失败。变异过程的原始结果和最终检查日志留在忽略的 `tmp/workspace-step1b/`。

所有 Python 测试都使用 Host 指定的解释器、`PYTHONPATH=src` 和 `hey_my_buddy.cli.checks`，进程数保持默认 4，没有传 `--jobs` 或设置 `HMB_CHECKS_JOBS`；私有根由检查运行器建立。没有运行完整检查，没有安装产品、调用模型或操作日常服务。回收中途被杀后的续做、计划和执行状态、围栏及其原有测试均未修改；版本、schema、升级 fixture 和其他文档未修改。

最终一轮受影响检查覆盖 `tests/python/blackboard/tasks/` 下的 `test_workspace.py`、`test_workspace_api.py`、`test_workspace_batch.py`、`test_workspace_boundaries.py`、`test_workspace_git_errors.py`、`test_workspace_identity.py`、`test_workspace_lifecycle.py`、`test_workspace_stash.py`、`test_workspace_submission_cleanup.py`、`test_windows_workspace.py`、`test_storage.py`、`test_storage_orphans.py`，12 个文件逐文件通过，共 223 项测试、跳过 0 项，检查运行器退出码为 0，并确认私有根已删除；没有最终失败需要单独重跑。最终 `git diff --check` 通过。这是源文件和私有真实 Git 场景的验证，没有完整检查、安装后验证或真实 Windows 机器验证。
