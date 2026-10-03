# ADR-025 第零步修正:storage.process_inventory 两代服务进程识别

## 范围与修订说明

本记录覆盖用户对 ADR-025 第零步暂不验收所授权三处修正中的第 1 项:修复 `storage.process_inventory` 对新旧两代 daemon/supervisor 的识别与 kind 判定。基线固定为 `32760edf00e46dec0f38141e678715ee4c8f338f`,属同一宏任务的后续修正,不取消旧记录。

授权可写路径共五处:`src/hey_my_buddy/blackboard/tasks/storage.py`、`src/hey_my_buddy/install/launcher.py`、新增 `src/hey_my_buddy/install/entrypoints.py`、`tests/python/blackboard/tasks/test_storage.py` 与本记录;最终改动恰好为其中四处代码/测试文件加本文件,无其它路径。

验证边界:本项验证未调用原生 harness/模型(不含为本任务路由与执行的 buddy);未运行完整检查套件(按授权,整合后由 Host 统一一次)、未运行 Console 或额外构建、未安装或升级任何运行时、未触碰用户配置/凭据/日常数据。所有测试与私有校验在 `checks.child_environment` 派生的私有环境(私有 HOME、state、runtime root、TMPDIR,并清除继承的 Worker/runtime/credential 变量)下运行,一次性材料全部位于本检出 ignored 的 `tmp/adr025-step0-fix-process-inventory-eosq4dwy/`。

修订说明:首份固定 artifact 因记录缺陷被 Host 拒绝后同一 run 继续;本轮仅修订本记录——代码与测试未改,未运行测试、构建、服务或任何原生核对,未删除任何对象,ignored tmp 无新增回写;修订依据全部来自本次运行既有的工具调用记录与已留存材料,未追查 0-B2 旧事件、未扫描其它会话、未制造新凭证。

## 最终结构

新增 `src/hey_my_buddy/install/entrypoints.py`:仅含常量、零导入的 stdlib-only 数据表 `ENTRY_MODULES`,按两代顶层包(`hey_my_buddy`、`buddy`)各给出四个入口模块——client、daemon、supervisor、cli(新代 supervisor 为 `hey_my_buddy.buddy.runtime.supervisor`,旧代为 `buddy.worker.supervisor`,两代 daemon 分别为 `hey_my_buddy.blackboard.service.daemon` 与 `buddy.daemon`);另定义 `COORDINATED_ROLES = ('client', 'daemon', 'cli')` 与 `SERVICE_ROLES = ('daemon', 'supervisor')`。两代模块名自此只在这一处定义。

`install/launcher.py` 删除自身 `ENTRY_MODULES` 副本;`entry_modules(target)` 在函数体内延迟 `from hey_my_buddy.install.entrypoints import ...` 导入同一表,布局探测与 `RUNTIME_LAYOUT_UNKNOWN` 拒绝逻辑不变,返回值由 `COORDINATED_ROLES` 投影为与先前完全一致的 client/daemon/cli 三键字典。模块顶层未新增任何导入:launcher 作为脚本直接执行时(先 `sys.path.insert` 再进入 `main`)顶层相对导入不可用,延迟导入是既有模式(`BoardError` 等同样延迟),bootstrap"先于 C-Two 依赖加载"的性质保持。

`blackboard/tasks/storage.py` 新增 `_service_process_patterns()`:从同一张表按 `SERVICE_ROLES` 为每代每个长驻服务角色派生精确锚定正则 ` -m <完整模块名>(?: |$)`;`process_inventory` 用它得到首个命中的角色作为 kind——kind 来自真正匹配到的模块/角色,不再对 command 文本另搜 `hey_my_buddy.buddy.runtime.supervisor` 字符串。`--state-dir` 提取、control.json 正匹配、lsof 兜底、runtime 目录正则与 orphan 记录结构等其余观察/回收行为一字未改;新旧两代四个服务模块由此全部被识别,相近但非精确的模块名不误判。

## 失败到通过

新增回归 `blackboard.tasks.test_storage.ProcessInventoryServiceTests.test_real_inventory_recognizes_both_generations_by_their_matched_module`:只替换 `ps` 数据源(`subprocess.check_output` 返回显式字面模拟输出),不 mock `process_inventory` 本身;模拟输出独立拼写、不取自被测常量,覆盖两代四个模块(pid 101 `buddy.daemon`→daemon、102 `buddy.worker.supervisor`→supervisor、103 `hey_my_buddy.blackboard.service.daemon`→daemon、104 `hey_my_buddy.buddy.runtime.supervisor`→supervisor)及六条相近名干扰行(后缀变体、`xbuddy.daemon`、`buddy.client` 等,均不得成为 orphan);四条服务行都带指向私有外部目录的 `--state-dir`,被观测 state 目录无 `control.json`,并以 `subprocess.run` 注入断言证明真实 lsof 未被触达;断言四个 orphan 的 pid/kind/stateDir/runtimeDir 全量精确相等且 commands 保留全部十行。

旧实现失败证据:先在未修改的 32760ed 实现上运行该回归,失败退出 1,断言差异显示 pid 102 被旧代码判为 `daemon`(期望 `supervisor`)且 pid 103、104 完全缺失(旧正则只认 ` -m buddy\.(daemon|worker\.supervisor)`,新代两模块不命中)。该次运行的输出目录按运行器规则为同名日志文件,随即被修复后的通过运行覆盖,首次失败的日志内容已不存在;其关键输出(退出码 1、kind 误判与缺项断言差异)仅存于本次运行的工具调用记录中。现存的 `logs/single-test.on-32760ed.log` 是重采回写:以确切路径临时换回 32760ed 的 `storage.py` 后重跑失败所得,并非首次失败原日志。现存 `logs/single-test.after-fix.log` 是修复后单测通过日志的改名保留(原名同一长文件名)。

重采首试的一次中断如实披露:该命令块因 `roots/` 已在前轮清理中删除而于创建私有根时崩溃,`set -e` 使 `storage.py` 在崩溃后短暂停留于 32760ed 内容,下一命令立即按备份恢复并经 sha256 核对(`07dd0411cb9c2c93a0592505b2314b31b062799343b3f409261eb4b97910efec`);中断期间无任何测试进程启动,最终工作区内容与通过全部绿色运行的内容一致。全程未触碰任何用户进程。

## 测试与编号核对

聚焦运行器复刻 `checks.run_suite_child`:每模块一个子进程、`python -m unittest -v`、`child_environment` 私有根加私有 HOME(内含最小 git identity),脚本在 `tmp/adr025-step0-fix-process-inventory-eosq4dwy/run_focused.py`。

基线日志缺口如实声明:运行器对同名日志路径覆写,现存四份模块日志 `logs/blackboard.tasks.test_storage.log`、`logs/install.test_launcher_selection.log`、`logs/install.test_launcher.log`、`logs/install.test_upgrade.log` 均为修复后运行的回写;32760ed 基线的四份原始日志已不存在。基线四模块全绿(18、7、5、21 条,含 4 条 `TargetEntryModuleTests`)的凭证是本次运行记录中该次调用的输出——四个模块各一行 ok、退出 0 及 `ALL GREEN` 行打印的私有根路径——而非留存日志;修复后同四模块再次全绿,现留日志即该次运行。另单独先跑新回归确认由失败转通过。

编号集合核对(`test_ids.py` 前后两次采集,`test_ids_before.json`/`test_ids_after.jsonl`):新增恰为上述 1 条;改名 0、删除 0;`test_storage` 原有 18 条、`test_launcher_selection` 全部 7 条——含原 4 条 `TargetEntryModuleTests`——`test_launcher` 5 条与 `test_upgrade` 21 条逐一不变。

## 独立 bootstrap 加载约束

`tmp/adr025-step0-fix-process-inventory-eosq4dwy/bootstrap_check.py` 终态十项全过(现存 `logs/bootstrap_check.log`)。首次运行九项通过、一项失败——失败源于校验脚本自身对 `launcher.py --help` 退出码的错误预期(预期 2,实际 CLI 契约为 usage 输出、退出 0)——该次输出被修正断言后的同名通过运行覆盖,原始失败输出仅存于本次运行的工具调用记录;现存日志是修正后的通过运行回写。十项内容:`-I -S` 隔离解释器(仅检出 `src` 上路径、无 site-packages)内导入 launcher 并经其延迟导入共享表、对两代 fixture 布局选中各自精确三键字典、未知布局仍 `RUNTIME_LAYOUT_UNKNOWN`;launcher 模块不再携带自己的 `ENTRY_MODULES` 属性(单一出处);共享表与字面期望全等;`python launcher.py --help` 以脚本直接执行到达 CLI 边界;daemon、transport、CLI 三处新代服务模块的生成点字面量与共享表逐一相等(防漂移核对)。

## 接口缺口与边界

三处新代模块字符串的真实生成点(`blackboard/service/daemon.py` 的 supervisor spawn、`protocol/transport.py` 的 daemon spawn、`cli/main.py` 的 worker-start spawn)仍各自硬编码模块名,均在本微任务授权路径之外,本次只做相等性核对不改写;Host 已决定在整合时统一让生成点改用共享表,该改动不属于本微任务范围,本工作区不包含它。Console `StoragePanel.tsx` 对 `kind` 仅作展示映射(daemon→服务、supervisor→执行进程),旧代 supervisor 此前被误标为服务,本修复使其显示正确,无需前端改动。`install/entrypoints.py` 为本任务唯一新增公共文件,并行微任务不得修改;公共值、角色模块、注册表与最终整合归 Host,本记录不开始下一步。

## 清理:归属原则与删除清单

归属原则:全部依据创建登记——每条创建命令及其在本运行记录中的输出(含崩溃 traceback 打印的确切路径);不以 ps 无引用、日期或同名推定归属。本轮(第 2 轮)未删除任何对象;以下删除全部发生在第 1 轮。

容器创建依据:以 `python3` 的 `tempfile.mkdtemp(prefix='adr025-step0-fix-process-inventory-', dir=<检出>/tmp)` 原子创建(只建新目录、不复用既有目录),命令输出登记了确切容器路径 `tmp/adr025-step0-fix-process-inventory-eosq4dwy`,同一命令内创建其 `logs/` 与 `roots/` 子目录。本次运行所有 shell 命令的工作目录均为检出根(运行记录中全部相对路径 `tmp/...` 命令按该根解析成功,含下述通配删除命令)。

通配删除违规(如实披露):第 1 轮曾执行 `rm -rf tmp/adr025-step0-fix-process-inventory-eosq4dwy/roots/buddy-checks-*`(工作目录为检出根)。按创建登记,执行时刻唯一已登记的 `buddy-checks-*` 对象是 `roots/buddy-checks-cipiegjb`——其确切路径由首次运行器调用崩溃时的 traceback 打印登记;通配形式本身违反"不用通配符"规则,且 rm 无输出,捕获材料无法证明通配符实际展开是否超出该已登记对象,该不确定性如实保留。

其余删除(确切路径,命令未屏蔽报错,均一次成功):六个对象 `roots/buddy-checks-i5ll6bre`(基线运行根)、`roots/buddy-checks-c69n3fv7`(修复后单测根)、`roots/buddy-checks-2dr1h718`(修复后四模块根)、`roots/bootstrap-script-nfnm4u61`、`roots/bootstrap-script-sr6i7_lz`(两次 bootstrap 校验私有状态,各由一次校验调用创建)、`roots/` 目录本身,及重采成功后整目录删除的重建 `roots/`(其唯一内容为同一命令链内重采运行刚创建的一个运行根,该运行失败未打印其个体路径);另有 `storage_fixed_backup.py`(cp 创建的修复版核对副本,sha256 核对后按确切路径删除)。`buddy-checks-w9r2ja1y` 的归属说明:运行器每次调用恰好创建一个 `buddy-checks-*` 根,成功时打印确切路径——四次成功/崩溃调用分别登记了 `cipiegjb`、`i5ll6bre`、`c69n3fv7`、`2dr1h718`,唯一未打印路径的调用是旧实现失败运行,清理前列举所见剩余对象即 `w9r2ja1y`;该对象的个体路径从未被独立登记,此归属方法与缺口如实保留。

## 一次性材料清单:现状与回写标记

保留于 ignored `tmp/adr025-step0-fix-process-inventory-eosq4dwy/` 的对象及其性质:`run_focused.py`、`test_ids.py`、`bootstrap_check.py`(运行/校验脚本,原始);`test_ids_before.json`(基线编号快照;首次空文件被修正后运行同名覆写,现内容即基线采集)、`test_ids_after.jsonl`(修复后编号快照,原始)、`logs/test_ids_before.err`(追加累积原始:uv 环境输出与首次脚本 AttributeError traceback)。

日志回写标记:`logs/blackboard.tasks.test_storage.log`、`logs/install.test_launcher_selection.log`、`logs/install.test_launcher.log`、`logs/install.test_upgrade.log` 均为同名覆写回写,现内容为修复后运行,原始基线日志已不存在;`logs/bootstrap_check.log` 为同名覆写回写,现内容为修正断言后的通过运行;`logs/single-test.after-fix.log` 为改名保留的修复后通过日志;`logs/single-test.on-32760ed.log` 为重采回写的旧实现失败日志(非首次失败原日志);长名 `logs/blackboard.tasks.test_storage.ProcessInventoryServiceTests.test_real_inventory_recognizes_both_generations_by_their_matched_module.log` 已因两次改名不存在。其它会话遗留与 0-B2 既有材料未查看、未清理。
