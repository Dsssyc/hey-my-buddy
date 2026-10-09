# DSH 原生续接 C2：公共事实防护验收记录

2026-10-08，ZCode Worker，宏任务计划[dsh-native-resume](dsh-native-resume.md)第一部分C2。固定基线7391081f0a7b59c1583693f65cbb09d71687f97b，独立受管worktree交付，仅新增三个文件：`tests/python/buddy/harnesses/test_pending_ready_receipt_guards.py`、`tests/python/buddy/roles/test_checkpoint_shutdown_guard.py`与本记录；git工作区显示无任何既有文件被修改，生产代码零改动。任务根`<task-root>`的确切路径经本轮结构化交付报告给Host：`t/`是固定测试临时根，TMPDIR与BUDDY_CHECKS_TMPDIR直接指向t本身（test_c_two_live的AF_UNIX bind在更深的每轮子目录下实测超过104字符sun_path上限报"path too long"，故必须用短根）；`m/`保存启动脚本、原始日志、编号清单、影子源码副本、逐项变异与SHA清单，全部新名创建，未覆盖任何旧材料。

## 防护与验证（V-C5至V-C10）

V-C5、V-C6（排队中同号异内容冲突）：真实调用`CTwoLiveEndpoint.request`入口，复用`buddy.harnesses.test_c_two_live`的既有夹具函数（`endpoint`/`inquiry_request`/`wire_request`/`decode_reply`）与进程内端点，不开C-Two服务器。两处冲突各自独立验证且都发生在提交之前——断言时`_admitted`与`_requests`仍为空：同一请求ID仍在pending映射中时换内容得`request-payload-conflict`（V-C5），同一问询ID在另一请求ID下仍在途时换内容得`question-payload-conflict`（V-C6）；两者都进一步断言被拒请求未加入投递（一个slot、一个队列项、原投递随后照常settle为queued）。已提交后的重放/冲突门（`_requests`/`_admitted`索引）不在本套件内，仍由既有`EndpointAdmissionTests.test_a_concurrent_duplicate_joins_the_in_flight_delivery`覆盖，该测试本次原件通过、编号未变。

V-C7（完成回执摘要绑定，分开验证）：回执由真实会话工具（`buddy.roles.session_mcp.respond`经`worker_services`）铸造，非手拼JSON。正向锚：未篡改回执对其自身configuration通过。失配一：签名对内容有效（同key铸造），仅`inputSha256`与验证configuration不同，在attempt-identity绑定层被拒（消息"failed its attempt-identity binding"）。失配二：执行身份与inputSha256完全一致、仅签名摘要换为异key重算的HMAC，在签名层被拒（消息"failed its signature verification"）。既有套件对`inputSha256`的子测试改内容不重签，实际只到达签名层，从不触及绑定层；本套件把两层分开钉住。

V-C8、V-C9（就绪材料）：直接调用`buddy.roles.live._private_regular_bytes`本尊，使拒绝可归因于它自己的防护而不是被绑定状态吞掉。V-C8用真实无写者FIFO：`O_RDONLY|O_NOFOLLOW|O_NONBLOCK`下该打开立即成功，因此拒绝只能来自函数自身的S_ISREG判定，打开原语无需隔离——这就是本项的隔离边界说明：从路径guard到open/fstat/read/close全部对真实描述符未隔离运行；若某候选非普通文件被底层安全打开方式直接拒绝而需要隔离打开原语，本任务未发生该情形。V-C9用真实普通文件16385字节对上限16384。两者都断言`BoardError`、`INVALID_ARGUMENT`。

V-C10（只有检查点、没有完整回合，任一层unknown/未确认不可续接）：真实`WorkerRunExecutor.collect`，经注册ZCode夹具（`ZcodeFixtureCase`：真实控制器子进程、真实mock app-server、无模型调用）执行一次完整start/wait，读回actual RunResult后仅做两处改写——去掉结果的最终value使角色收集无法完成回合投递（`worker_delivery`被拒、turn-output永不写出、回合记录永不导入），并追加一个真实摘要绑定（size+sha256）的legal检查点证据文件——其余一切（身份、停止事实、continuation、binding文件、nativeExitCode=0）都是该真实运行的既有事实并在改写时断言。正向见证：双层停止确认+resumable检查点+bindingPresent，`nativeSession.resumable`为True且载荷无`turn`、有`turnError`、code为`invalid-role-result`（证明走的是检查点路径而非完整回合）。四个负向族：原生停止层`groupState`为unknown/alive、外层`shutdown_confirmed`假、原生continuation能力None或缺席、检查点legal但不resumable（incomplete）或缺席——各自`resumable`必为False。选ZCode夹具是因为基线上DSH的`session_facts`恒报`bindingPresent: False`（其原生续接未接线），用它无法非空洞地钉住该合取；本防护是共享角色路径，DSH接线后将依赖它。

## 逐项变异（影子副本，交付源零改动）

全部变异只在`<task-root>/m/c2-mut-*/`的src影子副本上做，交付检出未动（git证明）。每个变异恰一处编辑（脚本校验全文恰一次出现），`mutated.diff`与`red.log`（目标测试输出，含M9a/M9b两份行为不变的绿色输出）留在各自目录。原件基线运行在`<task-root>/m/c2-baseline-b/`：12项新增全部通过，受影响既有四文件105项全部通过。

| 变异 | 目标条件（恰一处删除/替换） | 目标测试 | 邻居 | mutated.diff SHA256 |
| --- | --- | --- | --- | --- |
| c2-mut-M5-pending-request-conflict | pending请求ID冲突拒绝两行（c_two_live） | 红（改内容请求不再立即被拒，红因行为AssertionError） | 已提交join测试绿 | 589b0ee18960201dccecd45af1886fd13c9817930d29c1c69d172dfae2fde221 |
| c2-mut-M6-pending-question-conflict | 在途问询ID冲突拒绝两行（c_two_live） | 红（同上形态） | 同上绿 | 757eb8a1682797313eed0a1b3145ae753969a336a63a0354f0b3daed5e3ee8c0 |
| c2-mut-M7a-finish-input-sha | 绑定行`inputSha256`项（session_receipts） | 红（回执被接受） | 签名失配测试绿 | 95b9aca5e2b42b540ee7c751e32dc03fe91c229d2db5e1c3939393ba80b023aa |
| c2-mut-M7b-finish-signature | 签名`compare_digest`项 | 红（伪造摘要被接受） | 绑定失配测试绿 | 4afa6e4af0d54820cfbe70fa7563e51b3a3c1ce271bce0cbf85625318ec2f5df |
| c2-mut-M8-regular-file-term | `S_ISREG`判定项（roles/live） | 红（FIFO读出EOF，BoardError not raised） | 超上限测试绿 | c217b640c6d49aa808e4f18706ea7d22de3cb9fbf3cf64a6c59185fb26113ac4 |
| c2-mut-M9a-fstat-size-term | fstat的`st_size`上限项 | 绿（行为不变，见下） | — | 2eb761f997289501bd5a807c81892abc0fa21229854ab6186f0d581f1d0f0470 |
| c2-mut-M9b-read-bound-guard | 读取后长度guard整块 | 绿（行为不变，见下） | — | f809653f64b8f489873dcd4c491711e3c196ccdead5feb7ea096c69cca8457d7 |
| c2-mut-M9ab-over-limit-both-points | 上限条件全部执行点（恰两处，一次登记运行） | 红（BoardError not raised） | FIFO测试绿（S_ISREG保留） | 029175247f3231697059d6d6efe77ee6ee5a364f96e0a6f6f91d96f0b7d9b089 |
| c2-mut-M10a-native-stop-gone | `_role_stop`的gone合取 | 红（unknown与alive两subTest，"True is not False"） | 正向见证绿 | c36aef037578c57105f26bd7a62cfb5f0f3d1355236d45c08e732550649c4448 |
| c2-mut-M10b-outer-stop-confirm | `_role_stop`的外层确认合取 | 红（同上形态） | 正向见证绿 | 0323f5977a6d55ae8be311a1365920c3817a4738eaa306dcb7ef6e98ea4ac50e |
| c2-mut-M10c-continuation-true | `continuation.resumable is True`合取 | 红（unknown subTest；absent subTest仍绿，`is not None`仍在护） | 正向见证绿 | 09810866717037fc4a7a37b23c53491fdc5d7414a799aeaa055bafbc15271efe |
| c2-mut-M10d-checkpoint-resumable | `checkpoint_resumable`判定项 | 红（not-resumable subTest；absent subTest仍绿） | 正向见证绿 | b345afa555abd83914725bfff0c9c8cffeb7ecc8370ac64782d75df3f3f14e11 |

超上限条件的冗余事实：该条件由两个独立点执行（fstat的`st_size`项与读取后的长度guard），真实普通文件两处同时命中，任一单点删除都被另一层遮蔽——M9a/M9b行为不变，按计划"不把冗余防护……当成目标条件已由断言守住"如实登记为冗余层观察，不声称任一单点被断言单独守住；红证据M9ab在一次登记运行中去掉该条件的全部两个执行点，目标测试红、非普通文件邻居保持绿。此处理与[adr025-step5-host-review](adr025-step5-host-review.md)对"就绪文件路径防护另有一层打开方式防护，去掉这一层时行为不变"的既有结论一致。全部红因均为行为AssertionError（含"BoardError not raised"与"True is not False"），无超时、无装载/import错误充当红。

## 编号、环境与材料

真正加载编号清单（TestLoader实测，非文件名推断）在`<task-root>/m/c2-baseline-b/loaded-ids-*.txt`。新增12项：`buddy.harnesses.test_pending_ready_receipt_guards`下7项（QueuedSameRequestConflictTests 1、QueuedSameQuestionConflictTests 1、FinishReceiptDigestBindingTests 3、ReadinessMaterialBarrierTests 2）、`buddy.roles.test_checkpoint_shutdown_guard.CheckpointOnlyResumableTests`下5项，全名见清单文件。未变集合：既有四文件（test_role_live_seam 13、test_session_receipts 17、test_registered_run_wiring 21、test_c_two_live 54，共105项）内容零修改（git工作区仅两个新增测试文件与本记录），其加载ID清单即未变证据。早期运行目录`c2-smoke-094949`、`c2-baseline-095610`（含深TMPDIR触发AF_UNIX环境错误的首次基线）、`c2-baseline-a`、`c2-abort-M7b-first-attempt`（M7b锚修正前的中止副本）按原样保留，未覆盖、未删除。

运行环境：Host检出`~/.codex/worktrees/dsh-native-resume/hey-my-buddy`的uv锁定环境（`uv run --project <host-checkout> --frozen --offline --no-sync python`），PYTHONPATH显式指向本受管检出`src`与`tests/python`绝对路径，加载输出确认全部模块来自本检出；每个运行子进程先清除继承的BUDDY_*、ANTHROPIC_*、VIRTUAL_ENV、UV_PROJECT_ENVIRONMENT、PYTHONPATH（env从零构造），再设本任务私有状态/运行时根、BUDDY_DEV_SOURCE=1、TMPDIR与BUDDY_CHECKS_TMPDIR。测试只用仓内mock fixture与真实Python控制器进程，无模型调用、未启动用户安装harness、未安装升级任何运行时、未读凭据内容；受管检出未用git stash、未动分支/标签、未commit，改动留在工作区由黑板封存。

全部材料SHA256清单：`<task-root>/m/sha256-manifest.txt`（清单自身SHA256 `670d7fad6e5475b27f477ab0450820477fdc21f2c4061907ff3b1281ef41f409`）。交付文件：`tests/python/buddy/harnesses/test_pending_ready_receipt_guards.py` SHA256 `ee6625a7f7d3dccdbebd410af70bf44471ca4197b5abb725bfde506d754220c4`，`tests/python/buddy/roles/test_checkpoint_shutdown_guard.py` SHA256 `7c07761ee5e10eb4f6e87577500a65edf50e67bb4af106dc2507377316ac7262`；每份变异patch与原始绿/红日志的SHA都在清单内。

## 第二轮：V-C9两层独立职责重做（2026-10-08，固定7e386010审查拒绝后）

Host固定审查7e386010拒绝第一轮V-C9的处理：只断言`BoardError.code`的大文件用例在M9a（删`st_size`项）下仍被读取长度层拒绝、M9b（删读取长度条件）下仍被元数据层拒绝，两处同时删除才红，不满足"每处去掉对应代码测试失败"，两层的独立职责必须分开验证；其余guards与12项第一轮数据保留。Host最新主检出2affb47只退休旁路与common窗口，目标函数未改，本轮重做不受影响。第一轮全部材料（含M9ab历史红与M9a/M9b先前不红记录）原样保留于`<task-root>/m/c2-mut-M9a-*`、`c2-mut-M9b-read-bound-guard`、`c2-mut-M9ab-*`，未改写、未覆盖；本轮全部材料在新目录`<task-root>/m/c2-vc9-rework-baseline/`、`c2-mut-M9a2-fstat-size-term/`、`c2-mut-M9b2-read-bound-guard/`与脚本`mutate2-vc9.py`。生产代码零改动，只有本套件文件更新。

元数据层独立见证（强化既有用例，编号不变）：`test_readiness_material_over_its_frame_bound_is_refused`在code之外明确断言元数据层拒绝文本`Live readiness material is not a bounded regular file`——16385字节真实普通文件在读取开始前被`st_size`上限拒绝。M9a2单点去掉`info.st_size > maximum`后同一字节改由读取层以另一文本拒绝，该用例即红（红因`'Live readiness material exceeds its frame bound' != 'Live readiness material is not a bounded regular file'`，行为断言）；M9b2删除读取层时它保持绿。读取层独立见证（新增用例）：`test_readiness_material_that_grows_after_its_size_check_is_refused_at_the_read_bound`按Host给出的单个低层边界协调模拟fstat之后增长——只包装被测函数内的一次真实`os.fstat`：先取真实小文件（初始16384字节，恰在上限内，元数据层真实通过）的真实stat快照，再经真实第二描述符向同一真实普通文件真实append 1字节，然后返回增长前快照；函数随后`fdopen.read`真实读到16385字节，断言读取层文本`Live readiness material exceeds its frame bound`。隔离边界：被包装的原语仅此一处（os.fstat，快照真实、append真实、无伪造文件对象、不补丁被测函数、单线程无线程时序依赖），其余open/guard/read全真实；M9b2单点删除`len(raw) > maximum`后它单独红（`BoardError not raised`），元数据guard保持完好（M9a2下它绿）。

本轮运行：原件ready reader类3项通过（`<task-root>/m/c2-vc9-rework-baseline/ready-reader-green.log`）；两个独立变异各恰一处编辑、目标红且另一层见证绿（`mutated.diff` SHA256：M9a2 `2eb761f997289501bd5a807c81892abc0fa21229854ab6186f0d581f1d0f0470`、M9b2 `f809653f64b8f489873dcd4c491711e3c196ccdead5feb7ea096c69cca8457d7`，与第一轮同内容的单点diff哈希一致，交叉印证编辑点相同）。编号变化：模块真正加载8项，较第一轮清单恰增1项（新增长见证用例），其余7项集合相等（diff在`<task-root>/m/c2-vc9-rework-baseline/loaded-ids-module.txt`）；强化用例沿用原编号。按Host指示未重跑其余guards、checkpoint验证与无变化既有模块，其第一轮数据与未变集合证明继续有效；本轮新材料的SHA256清单为`<task-root>/m/sha256-manifest-vc9-rework.txt`。

## 当前状态

C2第一轮交付加第二轮V-C9重做已交付：两处排队冲突、回执两层摘要绑定、就绪材料非普通文件与两道上限层各自独立见证、检查点-only可续接合取全部具备逐项单点红证据；第一轮历史材料原样保留。待Host固定审查与独立复核后整合公共值；第一部分其余微任务与第二部分DSH原生续接未开始。
