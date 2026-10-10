# C-Two 0.7.4 接入：Host 复核

## 结论

最新状态见文末的[第三次独立复核](#第三次独立复核)：返修后的候选 `9522e73e` 行为核对全部通过，四处范围之内的整洁性缺陷退回，尚未合入。以下两节是对首个候选的复核，保留原样。

2026-10-10 复核 `socu/c-two-073@8e793196`，整合基线为 `12eb4fcd`。本次退回返修，尚未合入、安装或回收实施检出。发现一个阻塞回归：未设置 `BUDDY_STATE_DIR` 的正常 Host CLI 和公开 `BoardClient` 用法失败。内部缺状态根即拒绝的规则应保留；公开入口必须先解析默认目录，再把同一目录传到服务探测和实际 RPC。

## 已核对的证据

实施方完整检查绑定 `e0ce3d2b`：默认并行数，213 个文件全部通过，执行 3,474 项，其中跳过 1 项，退出 0，690.917 秒。Host 核对原始 `final-short-check.log` 与结果，并确认该提交至候选的 `src`、`tests`、依赖、锁文件、打包、skill 与前端差集为空；后续提交是文档更新。因此未重复完整检查或前端检查。前三次失败仍保留在实施记录中。

Host 从候选提交另建源码副本，用已锁定的 0.7.4 解释器、空 HOME、私有状态和运行时目录分别执行下列测试。各文件独立进程，均退出 0，共 218 项；外层空 HOME 没有新增内容，检查运行器报告收尾成功。未运行新的付费模型回合。

| 文件 | 项数 |
| --- | ---: |
| `protocol.test_rpc_config` | 24 |
| `protocol.test_transport_attach` | 28 |
| `buddy.harnesses.test_inquiry_owner` | 15 |
| `console.test_console` | 20 |
| `cli.test_support_cleanup` | 8 |
| `buddy.harnesses.test_c_two_live` | 70 |
| `buddy.roles.test_role_live_seam` | 18 |
| `buddy.runtime.test_worker_live_wiring` | 5 |
| `protocol.test_ctwo_integration` | 2 |
| `buddy.harnesses.zcode.test_zcode_checkpoint` | 10 |
| `buddy.harnesses.dsh.test_session_records` | 18 |

Host 另用本项目最终 `rpc_config` 在两个自建进程之间运行真实连接探针：30 次短调用、闲置 62 秒后调用、再次闲置 62 秒后调用、20 次 3 MiB 往返。服务端 Unix 文件描述符基线为 4，各阶段均为 5；两次闲置后连接的内核身份变化而数量不增，客户端退出后回到 4。探针退出 0。这支持原 0.6.0 闲置驱逐泄漏在发布版 0.7.4 与本项目配置下没有复现，不外推为日常环境已经完成升级。

实施方已获逐次批准的 Codex、Claude Code、DSH 各一次真实冒烟，其结果文件均记录交付后签收、控制器退出与原生进程组停止；本次只核对已有结果，没有重跑。ZCode 按用户决定未调用真实模型，保留未验证。真实冒烟也显式指定私有状态根，不能覆盖下面的默认入口缺口。

## 阻塞问题：默认目录没有传入实际 RPC

`protocol/transport.py` 的 `call_service` 与 `call_board` 先经 `get_state_dir` 间接解析默认目录、探测服务，随后却把原参数 `state_dir=None` 传给 `_request`。新增的 `rpc_config.configure_client(state_dir, create=False)` 只接受显式参数或 `BUDDY_STATE_DIR`，因此抛出 `PRIVATE_STATE_REQUIRED`。`protocol/client.py` 的 `BoardClient(autostart=False)` 同样先用解析后的目录 attach，再把原 `self.state_dir=None` 传下去。源码启动器虽然计算过默认目录，也没有把该值交给 CLI 的实际调用。

Host 在独立空 HOME 的默认数据路径下启动真实私有服务，禁用自建服务的 Worker 池并用仓库的静态模型目录夹具，避免模型发现与委派。只读 health/ping 与该服务的停止均经过真实 C-Two，没有替换连接或响应：

| 调用 | 退出码与结果 |
| --- | --- |
| 显式 `BUDDY_STATE_DIR` 的 CLI `health` | 0，`status=ok` |
| 删除该变量后的同一 CLI `health` | 1，`PRIVATE_STATE_REQUIRED` |
| 删除该变量后的源码启动器 `health` | 1，`PRIVATE_STATE_REQUIRED` |
| 删除该变量后的 `BoardClient(autostart=False).ping()` | 1，`PRIVATE_STATE_REQUIRED` |
| 只在公开 CLI 调用边界把 `get_state_dir(state_dir)` 传入的对照 | 0，`status=ok` |
| 显式指定私有目录，停止持有的测试服务 | 0，服务进程退出 0 |

最后一个正向对照仅在探针进程内替换入口函数的参数传递，未改产品源码。它说明正常默认路径已有且可达，失败来自 Host 边界没有把解析结果向下传递。内部缺状态根的防护无需放宽。现有全检与真实冒烟都给了显式私有根，故没有检出这一普通使用路径。

原始材料保留在整合检出的忽略目录 `tmp/c074-acceptance/`：`binding.json`、`focused-results.json`、各模块日志、`idle-probe.log`、`idle-probe-result.json`、`default_cli_probe.py`、`default-cli-results.json` 与私有根台账。探针的汇总进程正常退出不代表其中所有被测命令成功；默认入口三项的实际退出码均为 1，见上表。

## 返修范围与复验

在公开 CLI 与公开 Python 客户端边界解析一次状态目录，并将同一个值传给服务探测、冷启动和最终请求；兼顾 `autostart=False`、已有服务的 `stop`/`restart` 分支、显式参数及环境覆盖，保留只读 attach 不创建、不 chmod 的约束。不得给内部 `_request`、`rpc_config`、Worker 或控制器增加默认目录回退，不要求用户新增配置，不改日常数据与服务。

补真实私有服务回归：空 HOME 下不设置 `BUDDY_STATE_DIR`，普通 CLI、启动器和公开 `BoardClient` 的默认路径可用；显式目录仍优先、禁止 autostart 的客户端仍不会冷启动。对新增测试做删除目录传递的故障注入，明确命中相应断言；保留所有既有测试编号和内部缺根拒绝测试。聚焦通过后，在最终代码上按本批规则执行一次完整检查，保留本次红灯；已有三次付费冒烟保留原提交绑定，不因记录变化重跑。

本次只记录退回结论；C-Two 实施源码未合入。`socu/buddy-core` 保持 `12eb4fcd`，避免另一条实施线追随纯记录变化。验收通过前不进行源码安装或日常服务切换。

## 第二次独立复核

2026-10-11，Claude Code Host。前文由另一个承担整合 Host 角色的会话写成；本节是对同一候选 `8e793196` 的再一次独立核对，只记录本 Host 亲自做的事。结论相同：退回。阻塞问题成立，另有三处同一区域的问题应当在这一轮一并修正。

### 阻塞问题的复现与影响

在空 HOME、清空环境变量的进程里用候选源码与 C-Two 0.7.4 调用 `rpc_config.configure_client(None, create=False)`，得到 `PRIVATE_STATE_REQUIRED`；同一进程里公开的 `get_state_dir()` 能给出默认目录。`call_board`、`call_service` 与 `BoardClient` 把调用方的参数原样交给 `_request`，没有交解析后的目录。影响比"默认用法失败"更具体：安装后的启动器在选定运行时之后以 `runtime_environment` 给出的环境执行 CLI，这个环境不设置 `BUDDY_STATE_DIR`，所以装上这一版之后，Host 的每一条命令都会失败。

这个回归的来源有本 Host 的一份：2026-10-10 审阅首次失败的完整检查时，本 Host 提出"内部角色必须显式拿到状态根，不回退到默认目录"，没有把公开入口与内部角色分开说。

### 另外三处问题

用同一个探针分别在候选与基线 `12eb4fcd` 上运行，只用临时目录：

| 情形 | 基线 | 候选 |
| --- | --- | --- |
| 状态目录的路径里有用户自己建的符号链接 | 解析成真实目录后正常启动 | 拒绝，报 `LAUNCH_ACCESS_DENIED` |
| 已经存在、权限为 0755 的状态目录 | 改成 0700 后正常启动 | 拒绝，报 `LAUNCH_ACCESS_DENIED`，权限仍是 0755 |
| 空 HOME 下的默认目录 | 正常 | 正常 |

1. **路径里有链接的状态目录被拒绝。** `get_state_dir` 不再解析链接，`configure_local_endpoint` 又从根目录起逐级检查链接，直接调用时报 `PRIVATE_PATH_UNSAFE`。`private_dirs` 的链接检查是为状态目录之内的私有区写的，状态目录本身在哪里由用户决定：`/home` 是链接的发行版、把 `~/.local/share` 链到别的磁盘的用户都会被挡住。日常机器的路径里没有链接，不受影响。
2. **权限较宽的状态目录不再被修正。** 冷启动预检与 `Daemon.run` 里把状态目录建成并改成 0700 的两处被删掉了，`rpc_config` 接受 0755，冷启动却仍然要求只有本人能访问。实现方的记录写的是"已有的 0755 状态目录可用"，这只在 `rpc_config` 之内成立。只读的 attach 不创建、不改权限是对的；服务的所有者启动时修正自己的状态目录是原有行为。
3. **结构性的拒绝被说成沙箱问题。** 冷启动预检现在把所有 `BoardError` 都换成 `LAUNCH_ACCESS_DENIED`，提示用户"让启动器在 Host 沙箱之外运行"。上面两种情形与沙箱无关，照这个提示做解决不了问题。

另有一处不必阻塞：`transport._cold_start_preflight` 读取检查运行器的变量 `BUDDY_CHECKS_TMPDIR`，产品代码因此认识了测试设施，请说明理由或去掉。

### 测试编号

本 Host 用自己的脚本列出编号：基线 3,421，候选 3,474，新增 73，消失 20，没有装载错误与重复。消失的 20 个在实现方的记录里都有对应的新编号与说明（被删除的等待线程、套接字文件身份，以及改名）。本 Host 核对了名单，没有逐条核对断言是否等价，留到返修后的验收。

### 没有做的

没有对候选做变异，没有重跑完整检查，没有重跑付费的原生冒烟，都留到返修后的候选。从已安装的 0.27.0（C-Two 0.6.0，套接字在共用的临时目录）升级到这一版，旧服务要由旧运行时自己的模块停止，这条路从来没有跨 C-Two 版本走过；安装之前由本 Host 在私有副本上演练。本节的探针与编号清单在整合检出的忽略目录 `tmp/c074-claude/`。

## 第三次独立复核

2026-10-11，Claude Code Host。对象是返修后的候选 `socu/c-two-073@9522e73e`：在 `12eb4fcd` 之上 74 个提交，源码 19 个文件（+404，−373），测试 65 个文件（+4,228，−952），依赖从 `c-two==0.6.0` 改为 `0.7.4`。本节只记录本 Host 亲自做的事。候选在一个临时分支上与整合分支 `b7285470` 合并（`010e3135`，无冲突），下面各项除注明的以外都在合并后的这棵树上进行，用它自己的环境（Python 3.13.3，C-Two 0.7.4）。

结论：行为核对全部通过，前两次复核提出的问题都已修正；四处范围之内的整洁性缺陷退回原微任务，修完再合入。尚未合入、安装或回收实施检出。

### 像用户那样运行

空 HOME，不设 `BUDDY_STATE_DIR`，清掉继承的运行时、Worker 与凭据变量，启动真实的私有服务：

| 情形 | 结果 |
| --- | --- |
| CLI 冷启动 `health` | 成功；状态目录与其中的 `ipc` 都是 0700 |
| 经启动器 `health` | 成功 |
| `BoardClient()` 不给目录 | 成功 |
| 状态目录的路径里有用户自己建的链接 | 成功，状态落在链接指向的真实目录 |
| 已经存在、权限为 0755 的状态目录 | 成功，目录被改成 0700 |
| `ipc` 本身是一个链接 | 拒绝，`PRIVATE_PATH_UNSAFE` |

读源码核对了状态目录的归属：只有 `get_state_dir`、`call_board`、`call_service`、`ensure_service`、`request_stop` 与 `BoardClient` 接受可选的目录并解析一次（展开、取真实路径）；内部的调用都要求一个必填的路径；`rpc_config.resolve_state_dir` 与 `PRIVATE_STATE_REQUIRED` 已经删除；`LiveWireRequest.deadline_monotonic` 是必填字段；`_healthy` 只把 `SERVICE_UNAVAILABLE` 当作"没有服务"，其余错误照原样抛出；`BUDDY_CHECKS_TMPDIR` 只剩检查运行器读取。

### 空闲连接与文件描述符

日常服务耗尽描述符的机制（一个客户端进程两次调用之间沉默超过 60 秒，C-Two 0.6.0 就在两端各留下一条连接）在私有服务上直接验证：同一个探针在基线与合并后的树上各起一个服务，一个客户端进程先连续调用 21 次，再三次"沉默 65 秒后调用一次"，每次调用之后数服务进程与客户端进程的 unix 套接字。

| | 基线（C-Two 0.6.0） | 合并后的树（0.7.4） |
| --- | --- | --- |
| 服务进程：连续调用之后 | 14 | 14 |
| 服务进程：三轮沉默之后 | 15、16、17 | 14、14、14 |
| 客户端进程：三轮沉默之后 | 5、6、7 | 5、5、5 |

基线每轮多留一条，合并后的树不变。另一个探针量稳定状态：服务进程的描述符在预热后、500 次调用后、再启动 40 个客户端进程之后，基线是 33、33、33，合并后的树是 34、34、34；每次调用 10.62 毫秒对 10.88 毫秒；`stop` 之后两边都在 1.7 秒内没有剩下进程，`ipc` 里只剩 `.gate` 与 `.gate.marker`。

### 测试编号

本 Host 用自己的脚本列出编号：基线 3,421，合并后 3,519，新增 120，消失 22，与实现方的记录一致。消失的 22 个逐个对到了替代它的测试或删除的理由：被删除的等待线程与套接字文件身份的测试随机制一起删除；依赖 `PRIVATE_STATE_REQUIRED` 才成立的断言随这条规则删除（例如 `start_review` 那一条）；`test_unresolvable_health_reply_is_not_attached` 由 `test_invalid_health_reply_fails_without_cold_start` 取代。

### 变异

在提交内容的副本上逐个施加变异，用检查运行器自己的子进程环境运行相关的测试模块。22 个变异中 21 个被测试发现，对照（只改一句文档字符串）如预期存活。

| 变异 | 结果 |
| --- | --- |
| `get_state_dir` 不取真实路径 | 发现 |
| `call_service`、`call_board`、`BoardClient` 各自跳过默认目录（3 个） | 发现 |
| `_healthy` 把任何错误都当作"没有服务" | 发现 |
| 冷启动预检、`Daemon.run` 各自不再把目录改成 0700（2 个） | 发现 |
| 只读的 `_request` 创建目录 | 发现 |
| 私有区之内的链接不再拒绝 | 发现 |
| `ipc` 不存在时仍去连接 | 发现 |
| daemon 入口在没有 `BUDDY_STATE_DIR` 时回退到默认目录 | 发现 |
| `deadline_monotonic` 改回可选 | 发现 |
| Worker 转发已过期的请求；所有者消费已过期的条目；连接耗时重新计算窗口（3 个） | 发现 |
| 回收端点：未确认进程消失、跨域、地址不符、进程号不符、重复回收（5 个） | 发现 |
| 就绪握手接受另一个域的凭据 | 发现 |
| **状态根目录本身是链接时不再拒绝** | **存活** |

存活的那一个先在 6 个协议测试模块上跑，之后又在另外 35 个涉及链接、daemon 与端点配置的测试模块（726 项）上跑，都没有被发现。见退回的第 4 项。变异用的副本只含 `src`、`tests`、`packaging` 等目录，`install.test_first_install` 在这样的副本上本来就有 1 项出错，没有计入，原因没有查；它在完整检查里是通过的。

### 完整检查

合并后的树上运行一次：退出 0，3,519 项（跳过 1 项），216 个文件全部通过，4 个并行，678.68 秒。完整检查的时长已经过了十分钟（待办里有这一条）。

### 从已安装的版本升级

在私有的空 HOME 里按用户的做法走了一遍：从 `e4fbc916` 构建并安装 0.27.0（C-Two 0.6.0），启动它的服务，再用新的包覆盖安装。

- 旧服务每一次都被新的安装程序停下，失败后又由旧运行时重新启动，备份校验通过，回滚后的服务可用。跨 C-Two 版本停止旧服务这一步是通的。
- 升级本身没有成功，原因不在这一批：以整合分支 `b7285470` 为新版本同样失败。第一处是 `router-settings-upgrade-required`：当前源码只读新的 Router 列表设置，旧的两个 Router 位置的换算排在 ADR-021 的 L15，还没有写。在演练的看板上把设置的版本标成当前值以越过这一处之后，两棵树都在验证处以 `UPGRADE_VERIFY_FAILED` 回滚，原因是"表的清单变了"：当前源码比 0.27.0 多一张 `model_facts` 表，由启动时的 `CREATE TABLE IF NOT EXISTS` 建出来（[第一阶段的记录](adr021-logic-stage1.md)已经写明它要收进显式升级）。验证失败的原因没有写进 `upgrade-last.json`，是在一份临时改了一行的副本上读出来的。
- 所以当前源码装不到已安装 0.27.0 的看板上，原定在这一批验收之后进行的那次源码安装要等显式升级写出来；这件事记在[待办](../design/backlog.md)里。升级成功之后新服务在 0.7.4 上的表现，本次没有验证到。

### 退回的四项

都在这一批自己写的代码里，按仓库的 0.x 规则算缺陷：

1. **`cleanup_owned_endpoint` 的 `context` 是可选参数，缺省时在函数里面自己去取。** `buddy/harnesses/c_two_live.py` 第 355 至 377 行：`context: cc.LocalEndpointContext | None = None`，为 `None` 时用 `cc.local_endpoint_context()`。唯一的生产调用方 `buddy/roles/live.py:131` 总是传入，走缺省值的只有测试里的 8 处调用；文档字符串写的也是"由持有者给出"。改为必填，删掉里面的取值，测试显式传入。
2. **supervisor 的状态目录有两个来源。** `buddy/runtime/supervisor.py` 第 134 至 146 行接受 `--state-dir`，没有时读 `BUDDY_STATE_DIR`；两个启动方（`cli/main.py:316`、`blackboard/service/daemon.py:149`）都传参数，`blackboard/tasks/storage.py:84` 清点进程时也靠命令行里的这个参数认出它属于哪个状态目录。读环境变量的那一支没有使用者：参数改为必填，去掉回退。
3. **一句过时的注释。** `protocol/transport.py:231` 说 `configure_client` "第一次之后是空操作"；现在它每次都重新检查路径并设置端点域。
4. **状态根目录本身是链接时的拒绝没有测试。** `protocol/rpc_config.py` 的 `_validate_path` 里 `if private_dirs.linked(first)` 这一支删掉之后没有任何测试失败。`Daemon.run` 在这之后会对这个目录 `chmod`，所以它是改动之前的一道边界检查，应当保留并补上测试：状态根是链接时 `Daemon.run` 与 `configure_local_endpoint` 以 `PRIVATE_PATH_UNSAFE` 拒绝，链接指向的目录的权限不变。

### 留给后面的

不在这一轮返修里，记在待办"两侧之间互相引用的地方没有消除"之下，随那一批处理：`transport._call_service`、`_call_board`、`_call_board_read_only`、`rpc_config._validate_path` 与 `channel._request` 以下划线命名却被别的模块使用；这一批之前就有的几处仍然自己读 `BUDDY_STATE_DIR`（`buddy/roles/turn_io.py` 的 `seal_workspace`、`buddy/harnesses/codex/native_run.py`、`blackboard/tasks/workspace_identity.py` 的 `active_aliases`），`blackboard/store/backup.py` 的 `backup-preflight` 与 `install/launcher.py` 各有一份自己的解析，其中前者不取真实路径。

参考文档（`architecture`、`operations`、`workers`）仍写着 C-Two 0.6.0、共用临时目录里的套接字与逐次调用的等待线程，按约定由本 Host 在合入时改写。

### 没有做的

没有运行付费的原生回合：换到 0.7.4 之后各 harness 的真实运行只有实现方记录里的那几次（Codex、Claude Code、DSH 各一次，都在更早的提交上；ZCode 没有）。没有在 Windows 上运行。升级成功之后的服务没有验证，见上。本节的探针、编号清单、变异脚本与演练脚本在整合检出的忽略目录 `tmp/c074-final/`。
