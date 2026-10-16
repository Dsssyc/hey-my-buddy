# 备份只为升级的回滚：删掉的守卫与还原路径的独立审查

审查对象是微任务“备份：只为升级的回滚”在基点 `72f1b55b` 上交回的 `e727f095`，范围是 `git diff 72f1b55b HEAD -- src tests apps/console/src` 以及它们触到的现有代码。只读代码与文档，没有运行检查套件、没有启动服务或 harness；下文的“复现”是给验收用的做法，我自己没有跑。行号都指 `e727f095`。

## A. 删掉的守卫

A1（中低）不安全的状态文件现在在停服之后才被拒绝，返回的消息说备份已还原，实际没有。原来 `backup_readiness()` 在三处（准备阶段、取得升级围栏之后、停服持锁之后）跑预检，链接或特殊的状态文件在停服之前就以 `BACKUP_PREFLIGHT_FAILED` 拒绝，服务不动。这件事在新结构里仍然存在（四个状态文件与看板仍要复制），新代码在复制边界守着：`blackboard/store/backup.py:408-409` 拒绝状态目录的链接组件，`backup.py:450-453` 对存在或是链接的状态文件调用 `_private_copy`，`backup.py:149-152` 拒绝链接与非普通文件，看板本身也走 `backup.py:441` 的同一个复制。守卫本身没有丢；变化是时机：拒绝发生在 `detach()` 之后的 `backup.create()` 里，于是旧服务被停下再由回滚重新启动（契约变化的安装要求看板空闲，所以没有工作被取消）。缺陷在消息：`install/upgrade.py:568` 在 `backed_up is None` 且契约变化时给出 `UPGRADE_ROLLED_BACK` 与 “the previous runtime and backup were restored”，而这时没有备份、也没有还原。这句话原来也会出现在别的“备份之前失败”的路径上（例如 WAL 未 checkpoint），但不安全路径的拒绝过去是原样透传的，现在成了这条消息最常见的来源。复现：在 `tests/python/install/test_upgrade.py` 的 `test_fenced_upgrade_rejects_new_linked_state_file_and_rolls_back` 里打印 `result['error']['message']`。建议按 `backed_up` 是否为空区分措辞，属下一批的顺带项。

A2（低）`test_private_adapter_invariants.py` 删掉了 `attempt_evidence.is_evidence` 断言之后，`assert_partition` 只剩“文件不是链接”。原断言守着两件事：备份只收已声明的证据（这件事已不存在），以及 harness 只往执行目录写已知的文件、原生状态落在 `harnesses/` 下的私有根。后一件仍然成立并且仍有意义：`attempts/` 在存储页属于看板与记录，永不回收，原生状态若写进执行目录就再也收不回来。凭据一侧仍有正向检查（同文件 `agent-credential.json` 的清理、`personal-provider.json` 等的缺席、`native_root` 下有文件），所以不是安全缺口；只是“执行目录里不出现原生主目录”这一反向事实没人守了。测试名也已改成 `use_private_attempt_directories`，与它现在实际检查的不符。由 Host 决定是补一条针对原生主目录的反向断言，还是接受。

A3（无缺陷）清单里不安全路径的拒绝（原 `_verify` 中 `is_absolute()` 与 `'..'` 的检查）。它原来守着清单把文件指到备份目录之外。新代码用固定的允许集合代替：`backup.py:240-242` 只接受 `board.sqlite3.gz` 与 `state/<四个文件名>`，`backup.py:243-245` 要求磁盘上的实际文件集合与清单相等；这比原来的检查更严。还原时目标由 `_regular_files(current / 'state')` 的实际文件推出（`install/upgrade.py:248-252`），且在 `backup.verify()` 之后，路径只能是那四个名字。

A4（无缺陷）证据策略的校验（原 `_verify` 对 `state/attempts/` 与 `state/worker-session-cleanup/` 的名字检查与 `attemptEvidencePolicy`）。它守着备份里只有声明过的证据、还原时不把别的东西写回状态目录。这件事已不存在：任何 `state/attempts/...` 条目都会被 A3 的允许集合拒绝，`tests/python/install/test_backup.py` 的 `test_verify_rejects_out_of_scope_manifest_entry_with_valid_hash` 正是这一情形。

A5（无缺陷）预检里其余的结构检查：状态根是链接或不是目录、`board.sqlite3` 是目录或链接、`workspaces/`、`worker-session-cleanup/` 里的名字与链接。前两项由 `backup.py:408-409` 与 `_private_copy`（`backup.py:149-152`，配合 `private_dirs.open_regular_fd`）守着，原先 `create()` 里单独的 `_guard(state / 'board.sqlite3')` 删掉后由同一处覆盖；后几项守的是证据目录，那些目录不再被遍历，守的事情随之不存在。

A6（无缺陷）在线备份与它的安装围栏检查（`test_installation_fence_refuses_online_backup_in_both_modes`）。在线复制分支与 `offline` 开关一起删掉，`create()` 只剩一个调用者，在 `install/upgrade.py:498-503` 持有 `control-daemon.lock` 与 `board-owner.lock`；WAL 检查（`backup.py:412-413`）改为无条件，与“只做停服后的逐字节复制”一致。错误映射里的 `BACKUP_PREFLIGHT_FAILED` 在 `upgrade.py:549` 与 `upgrade.py:568` 两处一并删掉，没有留下不可达的分支。

## B. 还原与覆盖的路径

B1（中，面向以后）回滚现在依赖一条没写下来的不变量：从备份到回滚之间，除了看板与四个状态文件，安装和新服务不改状态目录里的任何文件。今天它成立，我逐处看过：事务准入在 `upgrade.json` 存在时被拒绝（`test_transaction_admission_rechecks_a_fence_after_rpc_entry` 守着），已验收检出的清理在 `blackboard/service/daemon.py:98-100` 被同一标记挡住，控制台 HTTP 在 `console/server.py:690` 被挡住，执行恢复面对空闲看板无事可做，`blackboard/store/upgrade.py` 目前没有待做的升级。但 AGENTS.md 允许存储形式的升级“转换或清掉存量”，而它跑在新服务启动的 `store.initialize()`（`daemon.py:129`），早于验证；以后若有一次升级在那里转换或删除 `attempts/`、`workspaces/`、`controls/` 里的文件，回滚只会把看板还原，文件留在新形式，看板与文件对不上。原先的整份备份恰好兜住了这种情形。建议把这条写进 ADR-031 或 architecture 的升级一节：存储形式的升级只改看板，文件的转换或删除放在验证通过之后（像 Worker 退役那样），否则同一改动必须扩展回滚清单。不是本批的缺陷。

B2（低）旧格式的备份代际过不了新的 `_verify`。0.40.0 留在日常看板的 `backups/current` 带着 `state/attempts/...` 等条目。正常发布路径不校验旧代际（POSIX 上 `backup.py:482-490` 交换后删除，Windows 上先挪到 `.previous`），所以 0.40.0 到 0.41.0 的安装能走通；但若日常看板留有 `backups/.incoming`（或 Windows 的 `.publish.json`、`.previous`），`_recover_publication` 会在 `backup.py:316-319` 先校验旧的 `current`，以 “files outside the rollback inventory” 失败，安装随之回滚且每次都会如此；遗留的 0.40.0 升级日志走 `recover()` 时也会在 `restore()` 的 `backup.verify()` 失败。0.x 不留兼容，代码不必改；建议 Host 在安装前确认日常看板的 `backups/` 只有 `current` 与 `.lock`，状态目录里没有 `upgrade.json`。

B3（低）`restore()` 不删除备份之后才出现的状态文件。例如备份时没有 `console-settings.json`，新服务启动时由 `console/server.py:312` 写下端口，回滚后它留着；旧服务读它没有问题。这与基点行为一致，也符合“只还原备份里有的”。

B4（无缺陷）`console-sessions.json` 排除在外站得住。新服务启动时 `ConsoleSessions.__init__` 只读（`console/console_sessions.py:46-59`），本地会话只在内存（`server.py:395-396`）；写这个文件的 `create`、`authenticate`、`revoke` 都经 HTTP 处理器（`server.py:438-439`、`server.py:460`、`server.py:733`、`server.py:749`），而 `_trusted_request()` 在 `upgrade.json` 存在时先拒绝（`server.py:690`）；CLI 一侧的启动器同样在标记存在时只放行 `upgrade`、`install` 与帮助（`install/launcher.py:342`）。所以回滚之前这个文件不会被改，回滚之后浏览器登录照旧有效。

B5（无缺陷）四个留下的状态文件逐个看：`console-settings.json` 是唯一会在回滚之前被新服务改写的（`server.py:312` 保存端口），还原它是必要的；`active-runtime.json` 与 `launch-settings.json` 由安装写，但回滚在还原之后无条件以 `write_active_runtime(previous, ...)` 重写（`upgrade.py:564`、`upgrade.py:648`）；`runtime-retention.json` 只在验证通过之后由 `retain_runtimes()` 写（`upgrade.py:534`、`upgrade.py:411`），之后不再有回滚。所以后三者的还原不会漏东西，也不会覆盖回滚之后仍要的内容；它们其实用不着，见 C3。

B6（无缺陷）经链接写到别处。还原在动看板之前检查全部路径：状态目录与备份目录的组件（`upgrade.py:242-243`），看板、两个 sidecar、`.restore.sqlite3` 与压缩件必须不存在或是普通文件（`upgrade.py:244-245`），每个目标同样（`upgrade.py:250-252`）；看板经 `O_EXCL` 的私有打开写临时文件再 `os.replace`，状态文件经 `.restore-<名字>` 临时文件再替换，替换前再查一次。悬空链接会被 `linked_component` 拒绝。新增的 FIFO 测试（`test_restore_refuses_special_existing_targets_before_changing_board`）覆盖了特殊文件，并检查看板在失败时未被改动。

B7（无缺陷）中断后的日志恢复一致。`recover()` 在 `backed-up`、`migrated` 等阶段核对备份路径后调用 `restore()`（`upgrade.py:636-643`）；`restore()` 是幂等的：看板整份替换，状态文件逐个替换，遗留的 `.restore.sqlite3` 与 `.restore-<名字>` 在下一次进入时清掉（`upgrade.py:257-263`、`upgrade.py:284-286`）。在看板替换与状态文件替换之间中断，重跑会再写一遍同样的字节。`preflight` 阶段的中断不还原，`create()` 的残留 `.incoming` 在下一次备份时清掉，与基点相同。`test_upgrade_archive.py` 新增的中断用例覆盖了 `backed-up`/`migrated` 两个阶段。

## C. 过度防御、重复与遗留

C1（高，属 Host 的文档）面向用户与 Host 的文档仍在讲已删的东西。`skills/hey-my-buddy/SKILL.md:28` 仍要求中间号变化的安装先有干净的 `backup-preflight '{}'`，装上 0.41.0 之后照做的 Host 会碰到未知命令；`README.md:107` 与 `README.zh-CN.md:107`、`docs/reference/cli.md:56`（`health` 里的 `backupPreflight`）与 `cli.md:58`、`docs/reference/operations.md` 的 24、78（`BACKUP_PREFLIGHT_FAILED` 一行）、141-152 行、`docs/reference/architecture.md:213-215`、`docs/reference/console.md:13`、`console.md:23` 也一样。按分工这些由 Host 改，实现方的说明第 46 段也这样写了；列在这里是因为 skill 那一句必须在安装之前改掉。

C2（低）只为已删功能留着的“缺席”测试：`tests/python/console/test_console_reads_http.py:131` 与 `:137-139`（`/api/backup-preflight` 返回 404），`tests/python/blackboard/service/test_service.py:169`，`tests/python/protocol/test_ctwo_service.py:78`。它们把一个已删的名字钉在测试里；未知路由返回 404 是通用行为，CLI 方法集合已由 `tests/python/cli/test_cli.py:97` 的等式钉住。按“被取代的测试一并删掉”，这几条可以删。

C3（低）回滚清单比需要的宽。B5 已说明 `active-runtime.json`、`launch-settings.json` 与 `runtime-retention.json` 的还原要么随即被 `write_active_runtime()` 覆盖，要么根本没有可还原的变化，等于同一件事有两套机制。实现忠实于需求原文（“安装会改动的状态文件”），所以不算实现方的缺陷；是否把 `STATE_FILES` 收窄到 `console-settings.json` 由 Host 定。

C4（低）`console/reads.py:13-14` 的模块说明在删掉一句之后断成了半句：“...route, and” 后面接着大写的 “Each projection...”。

C5（低）`backup.py` 的遗留：`_regular_files()` 的 `scope` 参数没有任何调用者传入（基点就如此）；`_SHOWN_PATH_LIMIT` 的注释仍说“错误或清单里的路径”，清单里已没有可变路径；`create()` 的 `runtime_identity`、`plugin_commit`、`contract_version` 默认值只剩测试在用；`pluginCommitStatus` 的值 `unavailable-in-source-metadata` 是为已删的 `source_commit()` 起的名字，现在提交号来自 `runtime.read_ready(previous)`。都不影响行为。

C6（低，基点就有，在本批触到的函数里）边界之内的重复检查：`upgrade.py:503-507` 在 `create()` 已经 `verify(incoming)` 之后再 `verify` 一次，随后 `if stored.get('databaseSnapshot') is not None` 恒为真；`_verify` 先在 `backup.py:238` 要求 `schema == SCHEMA_VERSION`，又在 `backup.py:269` 判断同一条件；`restore()` 先 `backup.recover(state)` 再 `backup.verify(current)`，后者对 `backups/current` 又调一次 `recover`（`backup.py:229-230`）；`upgrade.py:245` 对压缩件的 `regular=True` 检查与随后的 `verify` 与 `open_regular_fd` 重复。可以在以后碰到这些函数时顺带收掉，本批不必。

站得住的：`_verify` 的允许集合与实际文件等式（备份目录是从磁盘读回的外来输入，属于边界）；无条件的 WAL 检查；`_private_copy` 对源与目标的链接、特殊文件拒绝（复制边界）；`_restore_guard(..., regular=True)` 在替换看板之前对所有目标的一次预检（覆盖与删除的边界，失败时看板不动）；替换前对同一路径的再次检查是缩小竞态窗口，基点就是这种写法。

## 对照实现方的说明

说明与代码没有相互矛盾的地方。两处说明没有提到、需要 Host 知道：一是第 26 段说“路径不安全时由实际备份边界拒绝，并沿原安装失败路径恢复原运行时”，属实，但没有说这时返回的消息称备份已还原（A1）；二是第 18 段“升级不改写执行证据……受管检出的输入、清单、封存产物”在今天成立，但它是回滚正确的前提，说明把它当作事实而没有指出以后的存储形式升级必须守住它（B1）。第 12-14 段对 `runtime-retention.json`、`active-runtime.json`、`launch-settings.json` 的描述准确，正好说明这三者的还原没有作用（C3）。第 46 段把 README、参考文档与 skill 留给 Host，与 C1 一致。
