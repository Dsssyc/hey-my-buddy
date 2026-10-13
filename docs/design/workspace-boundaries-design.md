# 工作区按操作边界重排校验

按本设计，一回合、独立 worktree、一次带 `beforeCommit` 的目标比对并成功回收的代表微任务，Git 调用从实测 216 次降到推算 79 次，少 137 次（63.4%）；行为改变共 9 类，列在第 4 节。

2026-10-14，源码基线 `cf2d5efc437326d46c652481d8f3bc4bd84a7244`。这里只交付设计，待 Host 审阅；未改源码、测试、检查器或夹具，未实现削减、做候选验收或安装。依据是 AGENTS.md 的边界校验与 0.x 规则、CONTEXT.md、[上一份方案](workspace-git-calls-plan.md)、[待办](backlog.md)、[仓库目录换行的决定](../research/2026-10-13-line-breaks-in-repository-paths.md)，以及当前 workspace.py、storage.py、workflow.py 和七个重文件。架构与 ADR-007 的停止证据、所有权、固定产物、事务与事件规则继续适用。

## 1. 操作给用户的保证

这里的现场指 HEAD、index、Git 管理的文件、非 ignored untracked，以及显式选中的 ignored 输入；没有选中的环境、缓存和秘密不进入捕获。全程用用户安装的 Git，不运行过滤器或 hook，不刷新源 index，不操作 stash。文件名中的换行、tab、非 UTF-8 字节，以及 executable 和 symlink 模式继续按原始字节处理。

| 操作 | 给用户或开发者的保证 | 必须从现场取得的事实 |
| --- | --- | --- |
| 准备检出 | 输入有固定身份；源检出的 HEAD、index、文件不被改动；新检出属于这一微任务；重放取回原输入；已有或未能证明归属的目录不被覆盖 | 源仓库与检出身份、输入；新检出的物理身份、登记、实际物化内容；已有分配的锁与内容 |
| 捕获输入 | staged、unstaged 与选中 untracked 各自有准确归属；提交基线明确；工作树手交接不能吸收封存之后的无关改动 | 一份 HEAD/index/文件读取；基线对象；捕获末端的第二份读取，用来确认捕获期间的改动 |
| 封存 | 封存内容来自已停止的 Worker 留下的受管内容；越界改动不成为合法结果；固定 ref、patch 与记录相互绑定；中断重放完成原封存 | 入口身份与固定输入、读到的字节；发布引用前的现场；引用自己的现值及 CAS 结果 |
| 续接 | 使用指定封存或已获准的部分输入；原物理检出和预留关系不被换成另一个；未确认停止不启动下一回合；取消、接管和覆盖使旧准备失效 | 新操作入口的身份/固定输入、恢复输入的精确文件；激活前的现场；前后数据库中的所有权、预留和 revision |
| 验收比对 | Host 接受的是指定的当前固定产物；真实目标确有这些路径的内容、模式或删除；调整与 Host 自己的新增路径有明确证据 | 产物/patch/ref/对象的绑定；目标身份、所选 ref 的固定 commit、前后树、逐路径结果和祖先关系 |
| 回收计划 | 明确说出哪个物理检出可删、为何不可删、什么留下；计划本身不删除 | 当前归属、登记与锁、附着分支、未封存改动、固定封存，以及看板上的停止/依赖/验收证据 |
| 回收执行 | 过期、换主、出现依赖或现场变化的计划不授权删除；计划不是现场的替代品 | 当前看板资格；删除前重新取得的完整 Git 证明 |
| 回收移除 | 经完整证明的产品回收只删这个微任务拥有的原物理检出；源检出、借用检出、兄弟检出、输出、ref 与 branch 留下；失败保留原错误并尝试重锁；只有原路径确实消失才算完成 | 紧邻 unlock/remove 的身份、登记、锁、detached 状态与内容证明；失败后的原路径与登记。低级 remove helper 的受信调用前提另见第3节，它自身不补出内容证明 |
| 存储回收 | 已登记、历史记录仍引用、持有状态未知或原分配无法证明的内容留下；孤立分配只删 checkout；回执重放不重删 | 计划时的库存；执行时的新库存；原 allocation 锁内的证明；writer fence 内的历史引用、记录和物理身份 |
| scope 观察与恢复 | 证据说的是读到的失败现场；restore 只恢复选中路径并留下合法改动；adopt 只提升 Host 指定的现场；abandon 先保留失败内容，再恢复获准输入；后来改动不被无声覆盖 | 失败现场的内容与 index；Host 提供的指纹；恢复前的路径 CAS/index；放弃前的完整现场；恢复后的结果 |
| stash 观察 | 只给出开始与封存两个时点的事实；读不到是 unknown，不是空；不推断消失原因、不恢复、不据此判失败 | 各时点的新 common-dir、stash ref 是否存在及完整 reflog（展示另有截断上限） |

保证有具体的时点。读取几万个文件和 Git 的 unlock/remove 都不是原子操作；本设计不能保证外部进程在最后一读之后绝不写入，也不能保证多文件在同一个物理瞬间被冻结。必须保留逐文件打开前后属性检查、不跟随 symlink 的路径操作、Git 自己的锁/CAS，以及看板的 owner/revision/停止证据；把有限的读取放到最有用的时点，并说明余下窗口。

## 2. 真正的边界

用户可以随时改自己的源检出。Worker 在回合进行时可改执行检出；停止证据排除了这个受管子进程继续写，不能排除用户或别的 Git 进程。兄弟 worktree 可改共享 refs、stash 和对象可达性。看板的 reservation、allocation 文件锁和数据库 writer fence 只分别约束配合它们的参与者；它们不冻结外部 Git。

一次操作从接收外部参数/持久记录开始，到返回或发布这次操作的结果结束。操作里已经由 Git 回答的仓库位置、对象格式、固定 OID 的树/父信息、完整 entry map，以普通局部参数传下去；函数调用、进入内部 helper、创建临时 index、由自己刚刚生成的记录进入完成函数，都不是第二个边界。下一次 prepare、verify、seal、continue、accept、plan 或 apply 则重新读取，不增加跨操作缓存、持久状态、配置开关或第二套 Git 实现。

| 时点 | 重新看的内容与原因 | 内部不再重新问的内容 |
| --- | --- | --- |
| 源捕获开始与第一组 retained ref 发布前 | 仓库身份、HEAD/index/文件；源可由用户改，保留两份观察并比较，但第二份移到构树/算 patch 后 | inspect 的另一份 HEAD、每个 blob 批次的对象格式、相同 map 的树 |
| 新检出物化完成、准入前 | 新目录/git-dir 的物理归属与实际内容必须对应固定输入；已有 interrupted checkout 则重新证明其原登记/锁 | 在这同一完成证明里立即再扫一遍相同内容 |
| 封存开始与 retained/output 发布前 | 开始取得内容；构树、commit、patch 后再观察并比较，紧邻发布；输入/引用冲突在各自边界拒绝 | 开始的第二次立即观察、fresh output 进入 `_finish_output` 时再问一次 tree |
| 借用 allocation 加锁前、加锁后；续接激活前 | 先证明有权打开原锁，再确认锁期间没有换分配；下一回合进入另一操作/进程，重新证明输入 | 同一次加锁前或后，verify 与 inspect 对同一身份的叠查 |
| 每次验收比对入口 | 每次重新读 ref/patch/身份；解析成 OID 后只比较固定对象 | 同一次 original/final manifest 对相同 ref/OID/树对逐次再查 |
| 计划入口、执行中的删除前 | 计划给诊断；执行重新决定是否删除；完整内容/身份/登记证明靠近 unlock/remove | apply 的早期完整 Git 证明与 remove 的身份/登记证明分成两层再问 |
| 存储计划、执行库存、原 allocation 锁、数据库 writer fence | 各自解决计划陈旧、正在准入、晚到历史引用和原记录替换；执行库存不成为删除授权 | 同次孤立证明中 cleanup.verify 已读身份/refs 后再次读取，四组逐 ref 的双查询 |
| restore/abandon 写路径和 index 前、恢复结果发布前 | 先取得恢复 blob，再做最后 CAS；abandon 保存证据后复查完整指纹；结果重新观察并在发布前核实 | 生成失败证据时重读已知初始树和文件；读完现场立即再读的稳定包装 |
| remove 失败后的 relock | 错误跨了另一个 Git 进程和可能发生删除的窗口，必须新证明原注册和身份 | 不沿用 remove 前证明来给残留目录重锁 |

验收的 adjusted 第二轮保持独立，重新读取目标 ref、身份、patch 与固定产物；不能把第一次的差异直接宣布已接受。续接中数据库 snapshot 前后比较、Host 控制和 reservation fence 全留，删除与准入之间的 allocation 锁全留。这些是别的进程能够改变授权的边界，Git 计数为零也不能删。

## 3. 当前命令与最少序列

### 3.1 实测边界

临时目录在检出内。沿用上一份方案：PATH 最前的 Bash 脚本以 `%q` 记参数、父进程与私有 TMPDIR，随后 exec 原 Git，不读 stdin、不改输出/退出码；临时 sitecustomize 只记录真实 Popen 的调用栈、函数调用编号，以及 `_tree` entry map 的规范指纹。检查器、包和测试均来自本检出的同一源码，诊断路径随检查器自己的子进程环境传递，不改源码。实际进程数以转发脚本为准，调用栈只作归属；1 次 shell 执行的 detach 没有 Python 栈，仍计入生命周期文件的夹具调用。

第一次诊断把 Mock 的属性误当测试名，使三个故障注入测试的错误分类改变；中断了该轮，数据全部弃用。修正临时诊断后重新运行下面七个文件，默认 4 个进程：189 条测试、0 跳过、退出 0，检查器确认私有根移除。只运行有参数的指定检查，没有完整检查，没有改变指定解释器的环境，也没有真实模型调用。以下 `$PYTHON` 指 Host 指定的现有解释器，不在文档中记录机器路径。

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$PYTHON" -m hey_my_buddy.cli.checks \
  tests/python/blackboard/tasks/test_workspace_lifecycle.py \
  tests/python/blackboard/tasks/test_workflow_preparation.py \
  tests/python/blackboard/tasks/test_storage_orphans.py \
  tests/python/blackboard/tasks/test_scope_recovery.py \
  tests/python/blackboard/tasks/test_workspace.py \
  tests/python/blackboard/tasks/test_workflow_real.py \
  tests/python/blackboard/tasks/test_workspace_submission_cleanup.py
```

当前七文件实发 36,055 次；上一份方案冻结在 `784e8839` 的 36,332 次、190 条测试，不能当作本提交的实测。差额全部是 storage_orphans 少一个测试执行及其 277 次调用，另外六文件的计数复现一致。测试自己与直接 helper 的命令有 746 次归入独立栏；七文件中的未进 workspace.py 的封存后处理、直接 Git 故障注入等归属方式也与旧表不同，不能把这栏与旧方案的“4%”直接相减。

### 3.2 查询单元与归类

下表给出真正发给 Git 的命令形状；`root`、`oid`、`ref` 和 `path` 均为本次已确认的值。共同的环境净化、禁 hook/fsmonitor/untrackedCache/splitIndex、literal pathspec、禁 replacement、二进制 diff 参数保留。标记“边”表示外部或写入/删除边界必需，“内”表示内部重复校验，“重”表示相同问题或可合并的问题；实际命令失败依然通过统一 Git 错误边界报告，不吞成空答案。

| 单元 | 当前实发序列 | 设计序列与归类 |
| --- | --- | --- |
| 定位 L | `rev-parse --show-toplevel`；`rev-parse --absolute-git-dir`；`rev-parse --path-format=absolute --git-common-dir`；`rev-parse --verify --end-of-options HEAD^{commit}`，4 次 | 一次 `rev-parse --path-format=absolute --show-toplevel --absolute-git-dir --git-common-dir --show-object-format`（边）；HEAD 在观察或明确 ref 解析时问（边），内部身份 helper 不问（内）。独立对外 inspect 若需要 headCommit 仍加一次 HEAD，总计 2 次 |
| 一份观察 O | HEAD `rev-parse` 1；`ls-files --stage -z` 与 `ls-files -t -z` 2；`ls-files --others --exclude-standard -z` 1；有 payload 时 `rev-parse --show-object-format` 1，普通非空仓库共 5 次 | HEAD 1、`ls-files --stage -t -z` 1、普通 others 1，共 3 次（边）；格式使用 L 的答案（重）。显式 ignored 选择器仍加原来那一次 `ls-files --others --ignored --exclude-standard -z -- <paths>`，不合并普通与 ignored 的语义 |
| 稳定包装 | `_stable_observation` 内 O→O；封存再 O，共 2 或 3 份 | 捕获源保留入口 O 与发布前 O；封存保留入口 O 与发布前 O；只读 verify、物化验证、计划/删除现场各一次 O 对已知指纹/输入（边）。其余立即第二份 O 删除（内） |
| 存字节 | 每次 `_batch_blobs` 查格式；write=True 再 `cat-file --batch-check`，缺失时 `fast-import` | 同次观察已经读到的 tracked/候选 untracked 字节一起命名、一次 batch-check、必要时一次 fast-import（边）；不重新打开文件再查格式。末端观察只算身份，不写对象；fast-import 的实际 OID 校验保留 |
| 基线/物化读树 | 基线 `_commit`、`ls-tree -rz`、`rev-parse oid^{tree}`；物化再次 `ls-tree -rz inputCommit` | 默认 HEAD 来自入口 O；基线树/模式边界留 2 次。物化入口的独立 ls-tree 暂留 1 次，见保留项；不凭“clean”跳过 mode/submodule 检查 |
| 私有构树 T | `read-tree --empty`；非空 `update-index -z --index-info`；`write-tree`，非空每棵 3 次 | 完整 map 等于已读基线 map，直接使用已证 tree OID；否则只为不同 map 各构一次 T（重）。新 index 的空初始化暂留，不把缺失 index 的跨平台语义计作已证收益 |
| patch D | `diff --binary --full-index --no-renames --no-ext-diff --no-textconv … before after --` | 不同固定树仍问一次（边）；两棵已证树相等，差异就是空，直接计算空字节哈希（重）；不同 spelling 的 commit/tree 必须先有相等的 tree 证据 |
| pin P | 每个 ref：`symbolic-ref --quiet`；`rev-parse --verify`；缺失才 `update-ref --no-deref ref oid <zero>` | 发布前一次 `for-each-ref --format=%(refname)%00%(objectname)%00%(symref) <exact refs…>`；按精确名字确认现值/直接 ref（边），随后每个缺失 ref 仍各一次原 CAS 写入（边）。四个新输入 ref 为 5 次、两个新输出 ref 为 3 次；不合并写事务或改变 retained→记录→public ref 的次序 |
| 固定产物 A | 两个 manifest 各 2 次 ref `rev-parse` 与 2 次 tree `rev-parse`；输出 ref/tree 各 1；patch diff 1；非 noop `rev-list --parents -n 1` 1；两次 `diff-tree --name-only -z` 并逐 path `ls-tree -z`。单输入单路径改动为 16 次 | 一次 ref inventory；一次 `cat-file --batch` 取不同固定 commit 的 tree/parent 并核对所需 tree 对象类型；一次非空 D；每个不同前后树对一次 raw delta。original/final 同输入时共 4 次（边）；另一树对加 1。hash、身份、manifest 的各项预期值逐项比较，不省比较 |
| 固定路径差异 E | `diff-tree -r --no-renames --name-only -z beforeTree afterTree --` 1，加每个新路径的 `ls-tree` | `diff-tree -r --raw --no-abbrev --no-renames -z beforeTree afterTree --` 1，一次返回 path、新 mode/OID 与删除（边/重）；不增加全树递归，不用 Git 不同版本可能缩短的 OID 猜长度 |
| 目标路径 | 对相同固定 after commit 的每个 changed path 分别 `ls-tree -z` | 一批 `ls-tree -z --full-tree after -- <literal paths…>`（重）；目录与其子路径分批、长参数分块，按请求的精确 path 匹配，保留 512 上限和模式/删除语义；before/after/source commit 头可在目标侧一次 cat-file 取得，祖先关系仍由 merge-base 问 |
| 登记与保留 refs | `worktree list --porcelain -z` 1；allocation/current id 各 `for-each-ref`，相同 id 常问两次 | 登记 1（边）；refs 与本次 A 或输入验证的 inventory 合问 1。对相关 workspace 前缀取并集、按 refname 排序截 64；这项截断行为的改变单列 B7，不冒充原样去重 |
| 真正移除 | 身份 4、登记 1、`worktree unlock` 1、`worktree remove --force` 1，共 7 | 产品调用的完整删除前证明包含身份/登记，直接 unlock/remove 2（边）；独立受信 helper 的归属检查为 L+HEAD+登记+unlock/remove，共 5，但调用者还必须有本操作的相邻完整内容证明，5次自身不是删除授权；失败后的新定位/登记/relock 另计，绝不删除 |
| 孤立 refs | `show-ref --verify --hash` 与 `symbolic-ref -q`，四组共 8 次 | 同次 inventory 精确核对 input、staged、retained/input、retained/staged 的存在、直接 ref 和 OID，不另发 8 条（重）；ignored others 1 保留（边） |
| stash | common-dir `rev-parse` 1；quiet refs/stash `rev-parse` 1；有 ref 才 `reflog show --format=%H%x00%gs%x00 refs/stash` 1 | 空 2、有 ref 3，均保留；未证明单次 reflog 能区分缺 ref 与读失败，不按 stderr 猜空 |

合并定位只拒绝仓库根、git-dir、common-dir 及其上级目录的换行；按字节 LF 拆明确的四项，不用 splitlines 扩大到别的字符，没有逐项回退。cwd 只是仓库内子目录、或仓库内文件名带换行，并不因此变成不支持；进入 Git 的输出形状在边界校验，不是“不是四行就当另一个布局”。

### 3.3 每次操作的实测与目标

主例取 `test_cleanup_removes_only_the_registered_worktree_and_is_idempotent` 的唯一微任务：测试内 224 次中夹具/直接 Git 8 次，产品 216 次。它用合成停止/回合证明替代真实 harness，经过真实准备、封存、目标比对、看板验收和回收；没有经过 native executor 的独立 prepare verify 或累计 patch 收集。因此它是明确的代表流程，不是所有 harness、所有回合数的上限。旧待办的“一百八十来次”是折算；同样本轮的 not-required 单轮例实际产品 186 次，目标 67 次。

| 操作/场景 | 当前命令序列与次数（实测） | 设计边界序列与次数（推算） |
| --- | --- | --- |
| 主例准备新 worktree | 源 L4；捕获 25；四 P12；物化读树/add/batch blobs/read-tree 4；新 L4；完成 O×2=10，共 59 | 源 L1；捕获 9；四 P5；物化 4；完成 L1+O3，共 23。完成 O 必须核对 HEAD/index/文件与固定输入，不只保存读到的指纹 |
| 其中捕获 clean working-tree 输入 | base/ref/tree/modes 3；O×2+batch-check=11；三 T9；两 D2，共 25 | 基线 tree/modes 2；O3+batch-check1；构树之后、pin 之前 O3，共 9；三 map 全与基线相同，无 T、无非空 D。脏输入按不同 map 数增加 T、必要的 commit-tree/D/fast-import，不能一律套 9 |
| 主例第一次封存，一个 tracked 改动 | 输入 verify 7；入口 O×2+对象存储=12；初始两树 2；T3；末端 O5；commit-tree/D 2；两 P6；完成 tree 1，共 38 | 入口 L/R/commit 头 3；O3+batch-check+fast-import=5；同 inputTree 的初始 map 1；T3；commit-tree/D 2；发布前 O3；两 P3，共 20 |
| 主例目标比对，一个路径、beforeCommit | A16；目标 L4；before/after commit/tree 4；目标 interval E2；source 存在/祖先 2；目标 path 1；before 祖先 1，共 30 | A4；目标 L1；before/after ref 解析 2；目标 commit 头/存在性 batch 1；interval raw E1；目标路径 batch 1；两 merge-base 2，共 12。没有 before interval 则 25→9；adjusted 仍两次完整独立比对 |
| 主例回收计划，内容与 seal 相同 | L4；登记 1；保留 refs 2；A16；O×2+batch-check=11；初始两树 2；T3，共 39 | L1；登记 1；含保留 refs 的 A4；O3，共 9。指纹与已验证 seal 相等直接证明无未封存改动，不写 blob、不构树；不等时另取封存 entry map 诊断，不授权删除 |
| 主例回收执行的完整证明 | 同上 39，发生在 applying 之前 | 同上 9，移动到 applying 后、原 allocation 锁内、紧邻删除；看板 precheck 和 applying 回执边界保留。路径仍存在时实际删除还要满足原 expiresAt，即使状态已是 applying；该检查不发 Git |
| 主例移除 | 第二次 L4/登记1+unlock/remove2，共 7 | 与上一行合在同一次删除前现场证明里，只再发 unlock/remove 2；不是省掉删除边界 |
| 主例 stash 两个空观察 | 2+2=4 | 2+2=4，两个时点均留 |
| 未封存孤立 worktree 的计划证明 | outer L4；登记1；保留 refs2；verify 的 L/ref/tree7+O×2=10；四 refs8；ignored others1，共 33 | L1；登记1；refs inventory1；固定头1；O3；ignored others1，共 8 |
| 该孤立分配执行，已计划且正常 | 执行库存33；allocation 锁内再证明33；移除7，共 73 | 执行库存8；锁内删除前证明8；移除2，共 18；writer fence 的历史扫描/记录/物理复核仍留 |
| 续接的恢复文件枚举 | `_untracked` 的普通 others 1 | 1，精确路径交给 prepare；后面的 prepare 和激活 verify 是新的操作边界，不能用这一列表免检 |
| 可写 manifest 的独立 verify(False) | L4、两 ref2、tree1，共 7 | L1、ref inventory1、commit/tree batch1，共 3；read 或 require_unchanged=True 再加 O3，普通非空例 17→6 |
| scope 失败观察/证据，现有场景 | 越界 seal 在构输出树前失败，verify7、O/对象存储12、初始树与证据树共4、另一批 untracked 对象3，共 26 | 入口 L/R/头3；一次 O3、合并对象存储2；初始树2、证据沿用这些 map/字节，共 10。读已有 scope-conflicts 记录本来就是 0→0 |
| scope 完整 restore，现有 existing 场景 | 当前 `test_restore_preserves_legal_changes_and_reseals_without_a_model_turn` 同类完整路径 52 | 目标 27；一次入口观察、恢复 blob 读取后一次选中路径/index CAS、恢复后观察、结果 ref 发布前复查；不同 stagedTree、真实 index 写入和重放分支另计 |
| scope adopt，现有场景 | 38 | 目标 20；入口 O 与发布前 O 保留，完整 fingerprint 必须对应 Host 指定的原失败现场 |
| scope abandon，现有场景 | 49 | 目标 28；先保存失败证据，再加一份紧邻恢复的 O3，比对入口指纹；恢复后重新观察。新增这 3 次已计入批次预算 |
| 存储一般目录回收 | 文件树/进程/回执，不发 Git | 0→0；不为削减 Git 改动它的 tomb、runtime 使用与中断恢复协议 |

最低序列的计数前提是普通非空、没有 ignored 选择器、对象存在、一个输出路径、无中断和一次成功删除；变动路径数、空仓库、缺对象、只读、重放、恢复、符号 ref 和 Git 失败各走自己的边界。上表的捕获与 verify 是嵌套项，不重复计入主例。主例合计 `59+38+30+39+39+7+4=216`，目标 `23+20+12+9+9+2+4=79`。

有四项暂不动，也不记收益：物化入口独立读取 inputCommit 的树（它还界定对象缺失/损坏错误，尚未证实只传 map 的同一失败边界）；新私有 index 的 `read-tree --empty`（未验证所有支持的 Git/系统）；普通 others 与显式 ignored others 的分离；stash 的存在性与 reflog 分离。已有 interrupted worktree、borrowed allocation 加锁前后的两次物理证明、失败后重锁、新操作的固定 ref/patch 读取也留。确定收益不能靠删这些待证项凑数。

产品调用点是 workflow.cleanup_apply 和 storage 的原 allocation 回收，它们必须在同一次局部调用中完成完整 proof→破坏前授权复核→unlock/remove；保留低级 helper 只为受信内部调用和其归属/错误/幂等测试，不提供“只有manifest便可免内容证明”的产品入口。来自上一次 plan、另一次 apply 或外部调用者声称已做的 proof 都不满足这个前提；不能把5次的 helper 预算当作另外一种安全回收序列。新建尚未准入分配的失败撤销仍走原 preparation rollback 的专门所有权证明，不改成删除 Worker 输出。

## 4. 行为改变的完整账目

九类是按可独立审阅的产品行为归类，不是把每个少掉的进程当一次行为改变。index 合问、同次相等 map/固定 OID 沿用、raw delta 与批量路径回答不改变所比较的内容；它们的错误/并发采样变化归入下面相应行。没有用“行为保持”掩盖诊断时机、失败后计划状态或观察窗口的变化。

| 编号/改动 | 原来拦什么 | 现在在哪里拦、什么不再发现、后果 |
| --- | --- | --- |
| B1 仓库目录换行拒绝 | 单字段 rev-parse 允许这类仓库位置 | 入口明确拒绝仓库 root/git-dir/common-dir 的 LF，使用该仓库的操作不再准备；仓库内文件与子目录的特殊字节保留支持。这是开发者已经定下的改变 |
| B2 定位、HEAD 和格式按操作给一次 | 每层 inspect 的 HEAD 能拒绝该层时点的坏 HEAD；每批格式问询可能发现同路径仓库替换 | 本操作入口 L+物理身份、观察/目标解析中的 HEAD、破坏/发布前物理身份及 CAS 拦；不再发现只发生在删掉的查询时点又恢复的替换/坏 HEAD。只读操作不承诺发现读取后替换；不能继续往换过的仓库写。原 inode 里非法改对象格式可能到 Git 读写对象时才失败，不再由第二次格式查询先拒绝 |
| B3 立即两读改成实际边界读 | 两次稳定观察之间的 HEAD/index/跨文件变化会报 WORKSPACE_CHANGED | 源捕获与 seal 的第二份读后移到发布前；verify/完成验证/删除证明只需一次新读与固定输入或 seal 比较。只在原两读间出现又复原的变化不再报告；逐文件读取期间的变化仍拒绝。无固定期望的只读 scope 观察不再承诺两轮一致的整体现场；返回的是一次观察，不凭它授权覆盖 |
| B4 fresh 结果不从内部再当外部输入 | `_finish_output/_finish_record` 和重复初始树读取可再次拦对象被移走/损坏、或自己刚生成字段不一致 | 入口的外部记录校验、Git 对象生成/返回解析、发布 CAS 负责；内部生成字段相信不变量，缺陷失败于生成处。发布前人为毁坏对象可能到下一次重放/验收才被查出；所有从 disk 读来的 pending/output/resolve 记录仍完整校验，不能按“这个 id 刚用过”跳过 |
| B5 一次读取文件并复用失败证据 | `_output_entries` 与 `_scope_evidence` 重读能先发现或展示它们各自时点的后来变化；三 map 三树与空 diff 也会单独遇到 Git 故障 | 同份观察的 map、原字节与摘要构成证据，seal/adopt 的发布前 O 负责后来变化；scope seal 已拒绝时保存第一次证据，后来的改动由 restore/adopt 的新入口/CAS 拦。失败证据不再混入晚一点的文件内容；被省略的纯派生命令上的 Git 故障不再出现，固定内容、模式、patch 哈希保持 |
| B6 refs/固定元数据合问 | 串行 symbolic-ref/rev-parse/header/path 查询能分别遇到不同瞬间的 ref 漂移、损坏对象或单条命令失败 | 每操作入口或 pin 前的一次 inventory 与 batch 输出边界校验，逐 ref CAS 写入仍留；缺失/符号/非 commit/坏 parent/坏 patch 仍拒绝。相同 ref 不再读第二次，原多时点冲突可能转为本次读取的答案、CAS 冲突或下一操作发现；不能保证多 ref 原子观察。有损坏 ref 时 for-each-ref 的警告/遗漏也必须拒绝，不能视作不存在并覆盖；具体错误优先级可从旧 Git argv 变成批量查询/固定引用不符，需故障验收 |
| B7 retention refs 的截断定义 | 每 id 一次查询，累计达到64就停，再去重排序；重复 allocation id 会影响停止时点 | 同次读取相关前缀的并集，按 refname 排序取前64；记录展示的64项可能换掉。它只改变保留清单展示，不改变完整 ref 的存活，不拿截断列表授权删除；source/patch/固定产物证明查询不得被这64上限截掉 |
| B8 apply 证明移到删除前，并以指纹判断 clean | applying 之前的完整证明会先 NOT_READY；remove 只再查身份/登记，没有再查内容；proof 总会写对象、造树来诊断；applying 重试原来免除过期检查 | applying 之后、allocation 锁内的一次完整新证明紧接 unlock/remove，期间新脏内容也拒绝。拒绝可留下 applying，未过期时按原 plan/command 重试；只要原路径仍有东西，实际删除前都以原 expiresAt 核对期限，过期则保留路径、把已有计划记为 blocked并要求新计划，不因 applying 给一次未开始的删除延长授权。不添加阶段字段；原路径确实已消失的回执完成仍可跨过期恢复。现有 allocation 锁在常规回收的局部调用中也持有；同路径并发调用可收到 WORKSPACE_BUSY 后重试，不能冒充已删。附着分支/脏内容仍报 NOT_READY。指纹相等且seal已验证时不构树；不等时按已读index/文件与封存树给诊断，head/index-only 的 unsealedPaths 可更具体。低级helper仅有归属证明，完整内容保证来自同操作调用者。最终证明后再发生替换的窄窗口仍存在，不声称融合把它变成原子删除 |
| B9 恢复前的 CAS 与放弃前新读 | restore 当前在取恢复 blobs 之前做 selected/index CAS；abandon 保存证据后 `_reset_site` 只新读 index，不再比完整现场 | 把恢复 blobs 先读好，再贴近写路径做原 CAS，index 写前保留自己的 CAS；abandon 在保存证据后、覆盖前加完整 O/指纹比较。这个阶段发生的后来变化现在拒绝并保留现场；失败证据可能已发布，但不会因此覆盖后来内容。恢复后再读/封存仍独立。每个路径检查到实际写入之间仍有文件系统窗口，不能声称所有路径一笔原子恢复 |

输入 ref 观察的符号性沿现有各操作的规则：pin 与孤立 allocation 要求直接 ref；普通 verify/产物绑定原来通过 commit 解析接受的符号 ref，不在本设计顺便升级成新的拒绝政策。批量 inventory 必须显式携带 symref，不能把“缺失”和“符号引用解析失败/损坏”混为一谈。若实际 Git 无法一次可靠给出这些状态，这一合问停止实施，保留原边界查询并重新计算预算，不增加回退开关。

## 5. 测试清点与归并

### 5.1 哪些测试钉住内部形状

七个文件的现有源码声明 187 个测试方法，workflow_real 的一个父类测试被两个子类继承，实际执行189次；storage_orphans 当前35次，不是旧表的36次。没有发现这七文件断言两次 ls-files、三棵树或逐路径 ls-tree 的确切调用数；减少这些调用本身不需要删除产品断言。下表区分要改的 seam 和必须保留的故障结果，文件均在 `tests/python/blackboard/tasks/`。

| 文件/测试 | 归类与重排后的处理 |
| --- | --- |
| storage_orphans：`test_late_reference_after_physical_proof_is_rechecked_in_writer_transaction`、`test_late_allocation_record_change_after_physical_proof_is_retained` | 注入依赖 `_orphan_workspace` 恰好第2次/总2次；这部分钉内部编排，重写成“完整现场证明后、writer 引用/记录复核前”的注入。STORAGE_CHANGED、原路径留下的断言原样保留，不删安全场景 |
| storage_orphans：`test_planning_streams_rows_and_parses_each_field_once_for_multiple_orphans` | 钉 generator 和单 pass JSON 解析次数；它是已存在的内部性能要求，与 Git 减量无关，原样保留，不拿它当应该删除的校验 |
| storage_orphans：`test_absent_checkouts_and_host_owned_records_need_no_reference_scan`、`test_proven_orphan_removes_only_checkout_and_replays_receipt` | 不扫描不相关历史的形状兼有授权/重放结果；这次保留。若库存重排需要改 hook，仅保留“不相关或完整回执路径不扫历史”的性能性质及所有结果断言 |
| submission_cleanup：`test_two_read_helpers_share_one_original_allocation_lock`、`test_same_request_conflict_recovers_concurrent_winner` | 同一物理分配只取得一次锁、重放不重新 prepare 的次数断言保护竞争边界；原样留，不能为少问 Git 削掉锁或重放入口 |
| workflow_real：`test_adapter_verifies_input_writes_exact_bytes_and_gates_the_seal` | executor verify 调用形状 `[False]` 属进程入口检验，本设计保留这一调用；不是本次内部重复查询 |
| workspace：interrupted manifest/ref 发布、GC 前 retained ref、symbolic 输入、patch 损坏测试；submission_cleanup：read-tree/add/remove 的失败测试 | 真实 Git 或持久发布边界；原样保留行为断言。mock 具体某个 argv 的测试随批量 argv 重写注入，不把实际 Git 失败、保留原错误或中断重放测试删除 |
| lifecycle：proof/remove 竞态、attached branch、已消失/断链 symlink、接管、借用与原分配、两轮最新 seal | 各自钉第1节保证；原样保留结果。删除证明移动后，注入点跟着真实破坏边界移，仍须能再现同一外部改动 |
| scope_recovery：later change CAS、partial restore、adopt、abandon、native provenance、停止 gate | 各自有不同授权/覆盖/交付条件；原样保留结果。B9 应加故障注入来证明 blob 读取后改变文件、保存 abandoned 记录后改变现场都不会被覆盖 |
| workflow_preparation：prepare 前控制拒绝、锁竞争，以及 cancel/takeover/input/allocation replacement 的 snapshot 比较 | 钉操作入口和激活边界；原样通过，不删“尚未有权操作就不进 Git”的断言 |

只读 seal 入口 verify 的指纹检查并入封存的同份入口 O，再保留发布前 O；仍然不能通过 `require_unchanged=False` 让 read 输入免检。验收两轮、Worker 新进程入口和下一操作的 verify 不作这种合并。

### 5.2 为一件事重走整个生命周期的候选

完整生命周期按“准备受管 worktree、封存、验收、到回收”计；不把孤立分配的 prepare→plan→apply、准入失败撤销或 helper reservation 释放冒充整条成功工作流。逐条清点当前189次执行，确定有3条生命周期测试可并进已有场景，少3遍；涉及多个同类断言也逐项搬过去，没有把全文件的所有 prepare 算作可删。

| 文件/执行数 | 来源→已有目标 | 可少走的完整生命周期 | 保留的断言与限制 |
| --- | --- | --- | --- |
| workspace_lifecycle /49 | `test_acceptance_payload_binds_the_recorded_review` → `test_acceptance_requires_a_verified_or_not_required_decision` | 1 | 目标已有接受、相同 payload replay、不同 note 冲突；补已接受后换 target 的 CONFLICT。主验收/回收不再为这条断言重走；不丢固定 review payload |
| workspace_lifecycle /49 | `test_a_blocked_reclaim_needs_a_fresh_removal_for_the_same_exact_path` → `test_cleanup_is_blocked_by_unsealed_changes` | 1 | 保存 blocked planId；恢复后检查新 planId 与原 checkout path。脏 tracked→阻止→恢复→回收分支一致；原路径与 fresh plan 断言均留下 |
| workspace_lifecycle /49 | `test_after_the_recorded_detach_the_reclaim_completes` → `test_an_attached_branch_blocks_removal_with_the_branch_and_the_detach_retry` | 1 | 在已有 attached-branch/诊断/direct-remove 拒绝断言后，执行记录的 detachCommand、核对 HEAD、重试回收及 branch OID 未动。不是仅保留报错、删成功重试 |
| workflow_preparation /30 | 没有确定候选 | 0 | 准备/手交接/取消等各有独立授权时点；没有因此删完整生命周期 |
| storage_orphans /35 | 没有确定候选 | 0 | 相似引用分别来自 integration、request、turn、event、reservation 等；相同来源还分别在 plan后、apply库存后、writer扫描后到达，累积合并会遮蔽哪条保护生效 |
| scope_recovery /18 | 没有确定候选 | 0 | native restore 验收用 existing checkout，虽触发回收尝试，没有成功物理回收；restore/adopt/abandon/CAS 不是同一分支 |
| workspace /26 | 没有确定候选 | 0 | 多数是直接准备/封存；封存重放前由测试手工删 worktree 不等于产品回收生命周期 |
| workflow_real /11 | 没有可用附加一条断言归并的完整回收候选 | 0 | 实际没有 managed 物理回收；有下面的继承重复可独立减少 |
| workspace_submission_cleanup /20 | 没有确定候选 | 0 | 故障大多发生在准入/物化阶段，尚无成功验收→回收；恢复凭据/已有胜者/原错误各不相同 |

另列真正可少走、但不是开发者所问的完整生命周期：workflow_real 的 `TransferredCheckoutContinuationTests.test_parent_continuation_bases_on_the_helpers_sealed_commit` 被 `WorktreeContinuationReuseTests` 和 `ContinuationCheckoutReuseTests` 继承，同一交接测试执行3次；调整测试类继承/共享 helper 可只执行1次，少2次完整交接，保留两子类自己的 reuse 断言。七文件预计因此从189变为184次执行（3项生命周期归并、2项继承重复），这只是一份清点，没有改测试。

还有3项准备/恢复归并候选，不计入上述3遍或总预算：scope_recovery 的 `test_a_scope_failure_without_a_native_turn_is_never_delivered` 可把未交付和 delivery_json 为 NULL 的断言加到 `test_restore_preserves_legal_changes_and_reseals_without_a_model_turn`，少一次 scoped_failure；`test_scope_failure_records_evidence_and_opens_a_host_attention` 的多项断言可在同一目标 resolve 前检查，再少一次 scoped_failure；workflow_preparation 的 `test_unvalidated_seal_in_failed_report_cannot_block_partial_recovery` 可并入 `test_manual_continue_reclaims_released_helper_and_captures_partial_output`，少一次 failed-helper 准备，但改变目标输入组合，须先由 Host 验证。restore replay 与完整恢复归并、apply 后附着分支与 plan 时附着分支归并均有状态交互未证实，列为不动。

这3项确定生命周期归并，按本轮来源测试的产品操作，公共准备/封存成本可少3×（59+38），共同接受/回收或失败计划部分还能少；不直接把来源测试的265次整段都删掉，因为它的 detach/retry/assertion Git 会搬到目标。已有目标是否多发一轮 plan 也要候选重测。测试归并的收益没有再加进第6节的产品 Git 收益，避免两个分母和重叠削减。

## 6. 合计、风险与实施次序

### 6.1 批次预算

下面是机械推算，不是改源码后跑过的结果：按每个真实 helper 调用编号只扣一次，先替换固定产物查询，再合并 cleanup 的同次完整证明，再合并定位/index/格式，删除内部第二份观察，减少捕获的相等 map 与空 diff，合并 pin 查询，最后移除与相邻完整证明重复的 remove 定位。失败后的 relock 单独保留，原计划/执行时点不共享结果。为避免猜测日志没有保存的树相等关系，批次预算仍保留一部分实际可再合并的 verify refs、commit/tree 别名查询与目标头/路径读取，故比主例的最低序列保守；3次 abandon 的新覆盖前 O 各加3次、那1次无栈 detach 原样计入。这个预算维持原测试场景次数和正常/失败分支；批量错误优先级或 B8 拒绝时点改变后，真实候选的失败路径调用数可能不同，必须再测，不能承诺精确达到这个整数。

| 七个重文件 | 本轮测试执行 | 本轮 Git 实发 | 设计预算 Git | 推算少调用 |
| --- | --- | --- | --- | --- |
| test_workspace_lifecycle.py | 49 | 13070 | 5635 | 7435 |
| test_workflow_preparation.py | 30 | 6116 | 2923 | 3193 |
| test_storage_orphans.py | 35 | 5824 | 1981 | 3843 |
| test_scope_recovery.py | 18 | 2822 | 1356 | 1466 |
| test_workspace.py | 26 | 2883 | 1400 | 1483 |
| test_workflow_real.py | 11 | 2852 | 1364 | 1488 |
| test_workspace_submission_cleanup.py | 20 | 2488 | 1008 | 1480 |
| 合计 | 189 | 36055 | 15667 | 20388（56.5%） |

按互斥调用栈归类，准备（含捕获）15335→预算6021；封存5840→3284；续接3384→1654；验收目标比对1402→683；累计 patch281→111；独立固定封存验证128→28；回收完整检查2950→685、移除279→100；独立 verify503→212；scope866→463；存储计划1431→387、执行库存1390→399、原锁内移除证明318→87、独立库存495→135；独立定位61→32；stash646→646；夹具/直接 helper746→740。它们相加为本轮36055与预算15667；捕获/verify 等子项不能再加。存储目录一般路径0→0不在这些有 Git 的数里。代表生命周期216→79（63.4%），not-required 例186→67（64.0%）；不把同机负载下的文件秒或调用比例换成保证的整批秒数。

### 6.2 最大风险与验收次序

最大的风险是把“函数内”错当“外部不会改”：尤其在计划到 apply、apply 到 remove、blob 读取到恢复写入、第一轮 adjusted 到第二轮之间沿用旧现场。其次是批量 ref 对缺失/符号/损坏的区分、raw delta 的路径/模式解析，以及 B8 将拒绝发生在 applying 之后。一次最后复查仍有 TOCTOU；本设计缩短窗口并保留实际边界，没有引入一套能冻结用户仓库的协议。

实施分5个串行微任务，每个冻结前一个集成后的基线；最终审查和验收由 Host 负责。下面“原样通过”指保留的产品行为测试及全部未列入 seam 重写的文件；不能通过删除安全断言让少调用的实现过关。B1/B6/B8/B9 的新行为另补必要故障测试，编排 hook 的改写必须同时给旧安全场景的新注入证据。局部检查仍用 Host 指定解释器与 checks 入口/default jobs，不改检查器、不提高并发。

| 顺序/微任务 | 范围与行为 | 必须原样通过的文件（均在 tasks 目录，seam 例外见第5节） | Host 在私有环境注入的故障 |
| --- | --- | --- | --- |
| 1：入口定位与合并读取 | L、HEAD/格式传递、index stage/tag 合问；B1/B2；不动操作时点 | test_workspace.py、test_workspace_git_errors.py、test_workspace_batch.py、test_scope_recovery.py、test_workflow_preparation.py、test_workspace_submission_cleanup.py、test_workspace_stash.py | 仓库根/上级/git-dir/common-dir 的 LF 拒绝；文件 LF/tab/非 UTF-8 保留；SHA-1/SHA-256；非0 stage、skip-worktree、sparse/submodule 及组合错误优先级；入口后换 repo/git-dir/inode、非法对象格式，写入前必须停 |
| 2：固定对象与引用合问 | A、E、目标 commit头/路径、pin preflight、同次 map/空 diff；B4/B6/B7；CAS 写法/发布次序不变 | test_workspace.py、test_workspace_batch.py、test_workspace_git_errors.py、test_workspace_lifecycle.py、test_storage_orphans.py、test_workspace_submission_cleanup.py、test_workflow_real.py | 批量前后改 ref，缺/符号/悬空/损坏 ref，for-each-ref 的 stderr 警告或截断；同 commit 的错误预期 tree、错误 parent、GC 与 pending/ref 中断；目录转文件、删除、symlink/executable、长路径分块、512上限；adjusted 两轮之间改 ref/patch/checkout 必须独立拒绝 |
| 3：捕获/封存按边界观察 | source 与 seal 的末端 O 后移、物化一次验证、同份字节/证据、fresh 完成函数；B3/B4/B5 | test_workspace.py、test_workflow_real.py、test_workflow_preparation.py、test_scope_recovery.py、test_workspace_lifecycle.py、test_workspace_submission_cleanup.py | 文件读取中变化；构树/commit/patch 后、retained ref 前分别改 HEAD/index/内容；已有 interrupted checkout 有多余文件不覆盖；只读 index-only/untracked 漂移；Worker 未确认停止不封存；fresh 内部坏字段应在生成处暴露，disk pending/output/patch 损坏仍拒绝 |
| 4：回收与 scope 的破坏边界 | 完整删除证明与 remove 合在一处；orphan refs 合问；恢复前 CAS/abandon 再观察；B8/B9 | test_workspace_lifecycle.py、test_storage_orphans.py、test_workspace_submission_cleanup.py、test_scope_recovery.py、test_storage.py、test_workflow_preparation.py | applying 落盘后、最终证明前加脏内容/附着 branch/换锁/换inode/换登记；拒绝后让计划过期再修复，旧plan不得删除，新plan可重新证明；删除已完成后过期仍可完成回执；同路径并发锁竞争；最终证明后模拟目录消失/断链symlink；Git remove 失败与 relock失败；writer扫描后加任意历史引用、改 request/input/manifest；恢复blob读取后改selected/index；abandoned ref发布后改现场；崩溃后同plan/command恢复与 takeover/dependency fence |
| 5：测试场景归并与最后计数 | 仅3项确定生命周期归并及2项继承重复；产品改动集成后才做，预算分开 | 改过的 lifecycle/workflow_real 保留所有搬迁断言；其余五个重文件原样，最终七文件同命令重测；步骤1至4列出的非重文件分别已通过 | 每个来源独有故障/冲突须仍使目标测试失败；原分支和所有独有断言有映射；分别记录产品调用、测试调用、执行数、skip、退出码与私有根清理，不用删测试掩盖产品回归 |

Linux、macOS、Windows 的 Git 原始字节/索引/树差异与路径批量解析须在各支持系统验收；Windows skip 不是这项证据。重排触及 Host 可见的拒绝时点与计划恢复状态，按 0.x 规则属于 Host-facing 行为变更；这份文档本身不改版本或安装。Host 若不接受任一 B 项，重新计该项的原查询及收益，不将它偷偷算进“实现优化”。

本轮确认原源码七文件189条检查与计数通过，另经同一 checks 入口运行 `tests/python/cli/test_repository_hygiene.py` 的5条测试，0跳过、退出0、私有根移除；新文件尚未跟踪，另直接核对其 UTF-8、机器路径、段落物理行、行尾空白、测试名与合计。未证明候选序列、故障注入、跨系统语义、速度或安装。临时计数脚本和日志在交付前删除，交付只增加本文件；实现与测试归并等待 Host 审设计后决定。
