# 中间号不变的安装（ADR-031 M5）：删掉的守卫与删除路径的独立审查

审查对象是提交 `1ef144c1` 相对基点 `1b5b4be6` 在 `src` 与 `tests` 下的差异，以及它所走到的已有代码（服务的安装围栏、执行进程的回执与提示、存储清理、启动器）。结论全部来自读代码；没有运行检查套件、服务或任何复现，文中给出的复现做法都还没有跑过。实现方的说明在清点做完之后才读，只用于最后一节的对照。

## A. 删掉或绕开的守卫

**A1（严重，会让装上的产品出错）：安装日志在的时候，服务对执行的一切调用都按“空闲升级”的围栏拒绝，契约相同的路上这会杀掉在做的回合、丢掉交出的结果。** 原来的空闲要求（`idle_snapshot`）除了保证备份、转换与指纹比对有意义之外，还隐含地保证了安装期间没有执行在跟服务说话；契约相同的路去掉了空闲要求，这件事就会发生，而围栏没有跟着改。`upgrade.json` 从 `src/hey_my_buddy/install/upgrade.py:546` 写下到 `:599` 删除，这段时间覆盖旧服务的收尾（最长约 37 秒）、等所有权锁（最长 60 秒）、新服务启动（最长 45 秒）、核对与 skill 发布。期间旧服务和新服务的 `_guard`（`src/hey_my_buddy/blackboard/service/service.py:144-153`）只放行 ping、health、runtime.info、capabilities、backup 与 restart/status，其余一律回 `UPGRADE_IN_PROGRESS`。执行进程用 `BoardClient(autostart=False)`（`src/hey_my_buddy/buddy/runtime/execution.py:232`），走只读连接，正好连到这个健康但设了围栏的服务。后果有三种：（一）交结果：`deliver_receipt` 只把 `RETRYABLE`（`src/hey_my_buddy/buddy/runtime/receipts.py:12-13`）里的码当暂时失败，`UPGRADE_IN_PROGRESS` 不在其中，于是被当成明确拒绝，写下 `accepted: false` 并删掉回执（`receipts.py:38-40, 53-56`）；执行退出后服务确认它已不在，读不到回执，按“Execution was interrupted”记为失败（`src/hey_my_buddy/blackboard/tasks/interruption.py:226-252`）。一个在安装窗口里正常做完的回合，结果丢失，Host 看到的是失败。（二）交提示：执行的控制线程每 0.25 秒调一次 `flush_hints`（`execution.py:783`），非 `RETRYABLE` 的错误直接抛出（`execution.py:279`），控制线程把它记为 `fatal` 后退出（`execution.py:752-756`）；回合的等待循环随即 `raise_fatal`（`execution.py:592`），`execute` 的兜底取消原生运行并写一张失败回执（`execution.py:338-379`）。启动后的 `waitingReason` 和 harness 的实时活动都会产生提示，接了实时端点的 harness 运行（`src/hey_my_buddy/buddy/roles/inline_run.py:37`）在活跃回合里几乎一定会在窗口内有新活动，所以“执行在跑时装”这一主要情形，在真实的 harness 上大概率是回合被取消、记为失败，与 ADR-031 第 10 条正好相反。（三）刚准入、还在确认身份（`execution.py:300-305`）或做 harness 预检（`execution.py:428`，它另有一份只含 `SERVICE_UNAVAILABLE`、`SERVICE_START_TIMEOUT` 的重试码）的执行，同样失败。测试没有守住：`tests/python/install/test_install_without_upgrade.py` 用的命令适配器没有实时活动，每个用例都在 `upgrade()` 返回、日志删除之后才放行回合；回滚用例里“安装之后的事件保留”是直接写库注入的，绕开了围栏。复现：在 `test_two_patch_installs_preserve_execution_receipt_await_and_runtime` 里给 `upgrade.verify_started` 包一层，先 `release.touch()`，等该执行目录下出现 `result-delivery.json` 再调原函数；按代码，安装成功之后这个运行的结果是 failed、`result-delivery.json` 里 `accepted` 为 false。提示那一条在单元层面即可复现：私有服务在跑时写下 `upgrade.json`，对一个 `Execution` 先 `hint(activity=...)` 再 `flush_hints()`，会抛 `UPGRADE_IN_PROGRESS`。根因在两处的含义：围栏只认日志文件在不在，仍是“空闲升级”的意思；执行一侧把围栏当成身份拒绝。我的建议是在执行一侧修根因：围栏是暂时的不可用，执行交回执、交提示与 harness 预检共用同一份重试码并把 `UPGRADE_IN_PROGRESS` 放进去，这也一并关掉了契约不同那条路上已有的竞态（围栏之前刚准入的执行，在“未切换”回滚之前交结果，同样会丢回执）；契约相同的路是否还需要服务一侧的整套围栏，请 Host 另行判断。改动落在 `buddy/runtime/`，超出本微任务的 `install/` 范围，需要修订范围，不能在安装里绕过去。

**A2（中）：契约字符串相同而方法签名或跨边界的事实格式变了时，安装照样报告成功，执行卡在“不知道”而不是明确失败。** 版本规矩被违反、签名又变了时，新服务连旧执行的 `ExecutionLive` 被 C-Two 拒绝，记为“不知道”；旧执行连新服务同样被拒，得到 `SERVICE_UNAVAILABLE`，交回执的循环没有上限（`receipts.py:25-50`），永远不退出，回合做完了也结不了账，直到有人要求停止、服务按身份结束它，结果丢失。安装的核对（`upgrade.py:356-375`）只看运行时身份、稳定、schema、容量与源码泄漏，不看接回是否成功，这是任务定下的范围；但守着这条规矩的只有 `tests/python/protocol/test_contract_version.py`，它只核对字符串是不是“中间号版本、末位为 0”，没有任何检查在签名变了而 `CONTRACT_VERSION` 没变时失败。回执、`execution-start.json`、`execution-live.json` 这些由新服务读、由旧执行写的文件格式（例如 `src/hey_my_buddy/protocol/execution.py:155` 要求回执的键集合完全相同）也只靠这条规矩。复现：在一个只改末位的私有运行时里给 `BuddyControl` 的某个方法改签名，执行在跑时装，安装返回 upgraded，执行的尝试一直是 unknown。建议 Host 定：要么检查套件把三个契约的方法签名做成摘要，钉在 `CONTRACT_VERSION` 旁边，变了而版本没动就失败；要么安装在核对之后报告仍为“不知道”的执行数。前者只是一个测试，代价更小。

**站得住的部分。** 下面这些原来的守卫在契约相同的路上可以去掉，或者仍由别处守着：
- 空闲要求与 `retire_workers`：它们守的是转换、离线备份与 0.39.0 Worker 池的退役。契约相同时不转换、不备份；0.39.0 的看板是 schema 22，而契约相同的路要求看板 schema 等于新包的（`upgrade.py:96-99`），所以在版本号还没改成 0.40 之前把本源码装到 0.39.0 上，会以 `UNSUPPORTED_SCHEMA` 拒绝，不会走到没有退役 Worker 的切换。`legacyWorkerCleanup` 也只在契约不同时置为真（`upgrade.py:592, 666`）。
- 备份预检、离线备份与失败时还原看板：契约相同时看板不被安装改动，回滚只换回运行时（`upgrade.py:624` 与 `:704-714` 的还原都在 `contract_changed` 之下），安装之后写下的东西保留，这是对的；不做备份也就不需要备份清单的预检。
- 数据指纹与完整性检查：执行在跑时看板本来就在变，指纹没法比；`PRAGMA integrity_check` 与外键检查守的是要被转换和备份的看板，契约相同的安装不碰看板，不需要。新服务启动时是否改写数据，只能靠版本规矩，这是第 10 条的取舍。
- 安装途中准入新的工作：日志写下之前准入的执行由旧服务起、新服务接回；日志在的时候准入被 `src/hey_my_buddy/blackboard/store/store.py:687-688` 拦住，所以回滚时不会留下用目标运行时起的执行。它本身没有问题，问题在 A1：拦准入的同一把围栏也拦了执行。
- 契约相同而 schema 不同：在运行时准备之前拒绝（`upgrade.py:502`），由 `install()` 在动 skill 路径之前也拒绝（`src/hey_my_buddy/install/skill_install.py:166-168`），都有测试。
- 新服务起来之后核对失败：先 `restart` 新服务（执行保留，`src/hey_my_buddy/blackboard/service/daemon.py:203-213`），实际取得两把所有权锁之后再起旧运行时并核对（`upgrade.py:621-631`），不还原看板，不把超时或进程号当成已停。
- 日志中断后恢复：日志带 `contractChanged`，恢复按写下日志时选定的那条路走，契约相同时不查空闲、不还原，取锁之后起旧运行时并核对（`upgrade.py:697-716`）；预检阶段中断时确认或重新起旧服务（`:680-696`）。读不到 `contractChanged` 的旧日志会以 `KeyError` 失败，这符合 0.x 不留兼容的规矩。
- 接不回来的执行：没有另做，沿用第 4 条的“不知道”与按身份停止，冻结执行的用例覆盖了。

## B. 删除与覆盖的路径

**B1（低）：日志在而服务健康时，已经在跑的客户端要先做一次冷启动预检，才能排到启动锁后面等。** `_ensure_service`（`src/hey_my_buddy/protocol/transport.py:386-397`）看见日志就不走快速连接，而是先跑 `_cold_start_preflight`：在状态目录里写一个探测文件、绑一个本地套接字。这个客户端注定不会冷启动，却因此在拒绝这些动作的 Host 沙箱里得到 `LAUNCH_ACCESS_DENIED`（“把启动器放到沙箱外”），而不是等安装做完；`wait` 这一资源则直接回“No board service is running”，可服务明明在跑。`await` 靠重连那一次读状态仍然能接上，但在这样的沙箱里重连本身会以误导的错误结束。建议日志在时只排启动锁，取得锁之后仍连不上才拒绝；冷启动预检只留给真要冷启动的那条路。

**站得住的部分。**
- 旧运行时的清理：成功安装与 verified 日志的恢复都记下 current 与 previous 之后才清理（`upgrade.py:466-474`）；两代之外的运行时由 `src/hey_my_buddy/blackboard/tasks/storage.py:116-131` 按命令行与打开的文件判断有没有人在用，`ps` 或 `lsof` 看不全就保留，Windows 上一律保留，路径里有链接就整类不删（`storage.py:559-560`）。执行进程用服务的解释器起（`src/hey_my_buddy/protocol/execution.py:81`），解释器在运行时目录里，所以还活着的执行、包括“不知道”的那种（进程身份还在），都钉住自己的运行时；连装两次的用例覆盖了。执行退出之后才删，那时结账由新服务读回执完成，不需要旧运行时。
- 回滚与日志恢复：契约相同时从不调 `restore`，也不还原备份；安装之后写下的事件与结果保留。
- skill 的发布与还原：逻辑没有变。恢复在碰任何服务之前先用 `_skill_paths` 核对日志里的路径（`upgrade.py:654`），发布与还原只在核对过的路径上做 rename 与删除。
- 冷启动被拒绝之后的恢复：启动器在日志在时只放行 install、upgrade 与 backup-preflight（`src/hey_my_buddy/install/launcher.py:343-346`），再跑一次安装就进入 `recover`；服务一侧的客户端在取得启动锁之后才拒绝（`transport.py:400`），不退役端点、不起旧的那一代，这是对的。拒绝的措辞见 C5。

## C. 过度防御、重复与遗留

**C1：没有人用的备份令牌。** 契约不同时仍生成 `backup_token`、只把它的哈希写进日志（`upgrade.py:537-539`），明文随即丢掉；服务的 `backup` 里比对 `upgradeToken` 的分支（`service.py:424-434`）因此永远不会放行。这次改动把它包进了条件里，而不是删掉。应当连同 `secrets`、`hashlib` 的引入和服务一侧的令牌分支一起删掉，日志在时的在线备份直接拒绝即可。

**C2：契约相同时同一个 schema 读了最多七次。** `install()` 在 `skill_install.py:166-168`、`:181-183`、`:210-212` 各调一次 `install_preflight`，`upgrade()` 又在 `upgrade.py:502`、`:519`、`:553`、`:579` 各调一次 `preflight`。只有安装自己会转换 schema，而它在 `upgrade.lock` 之下做，这几次之间看板的 schema 不会变。空闲那条路上重复的 `idle_snapshot` 有它的理由（检查之间可能准入新的工作），照抄到契约相同的路上就只是在边界之内重复检查。保留 `install()` 在动路径之前的一次和 `upgrade()` 在准备运行时之前的一次就够了。同一段里契约来源也有三种写法：`contract_changed` 取自 `health_before`（`:501`），`:519` 用 `attached`，`:553`、`:579` 又回到 `health_before`；因为前面核对过 `serviceId` 相同，值一样，但读起来像三个来源。

**C3：没有服务时用正则读旧运行时的源码来知道它的契约。** `install_preflight`（`upgrade.py:109-113`）在没有 `control.json` 时打开旧运行时的 `contracts.py`，用正则取 `CONTRACT_VERSION`；匹配不到会以 `AttributeError` 而不是 `BoardError` 失败。这是第二种得知一个已经有记录的事实的办法：已安装 skill 的标记本来就记着 `contract`，而且安装核对过它与运行时同一代（`upgrade.py:440-450`）。建议改读记录下来的事实，不读源码。

**C4：等锁的期限按契约分成 30 秒与 60 秒两档。** `detach`（`upgrade.py:201`）与 `recover`（`:653`）里的理由是观察者收尾加上 C-Two 的 30 秒关闭预算，这取决于正在停的服务，与契约无关；契约不同时服务是空闲的，用 60 秒也不多花时间。一个值就够，不必为它设开关。

**C5：措辞还是空闲升级的意思。** 服务围栏的消息是“fenced for an idle upgrade”（`service.py:153`）；契约相同的路上恢复不了时仍说“journal and backup retained”（`upgrade.py:644`），其实没有备份；`UPGRADE_IN_PROGRESS` 让人“等它做完再试”（`launcher.py:346`、`transport.py:462-463`），但日志成了孤儿时没有人在做，应当指向重跑安装。

**站得住的部分。** `preflight` 与 `verify_started` 按 `contract_changed` 分两支是这个设计本身要的，不算重复；把运行时保留抽成 `retain_runtimes` 消除了成功与恢复两处的重复，它的宽 `except` 把错误码带进返回值、不阻塞已核对的安装，不算吞掉；`detach` 对 restart 回复的 `pass` 有注释说明，停止证据来自随后实际取得的锁；`_ensure_service` 先后两次看日志（快速连接之前与取得启动锁之后）都需要，后一次关掉了预检与上锁之间出现日志的窗口，有测试。

## 对照实现方的说明

说明与代码不符的有三处。第一，“新服务启动或核对失败时……安装开始之后的新事件和执行回执继续保留”：直接写库的事件确实保留，但安装窗口里执行交来的回执会被围栏拒绝并删掉（A1），用例里的事件是绕过围栏注入的。第二，“覆盖命令和离线 harness CLI 的结果交付、连续安装……跨安装 await”：每个用例都在安装返回之后才让回合结束，交付只在日志删除之后验证过，安装窗口之内的交付和提示都没有覆盖。第三，说明写“已经运行的 await 便通过现有的同一运行重读逻辑连接新服务”，这对 Host 的 await 成立，却没有说执行进程的客户端在同一窗口里会撞上服务的围栏；“执行进程的客户端继续使用 `autostart=False`”恰恰是它们绕过启动锁、直接撞上围栏的原因。其余的描述（选路、schema 拒绝、60 秒等锁、运行时保留、版本核对）与代码一致。另外，`docs/reference/operations.md` 仍写着“跨升级的 await 以这个码结束”，与改过的 `test_upgrade_host_cutover.py` 不符；那份文档由 Host 维护，这里只是指出来。
