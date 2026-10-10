# C-Two 公开默认状态目录返修计划

本批继续使用 `socu/c-two-073` 与 `~/.codex/worktrees/c-two-073/hey-my-buddy`，起点 `8e793196bb7169f283b77f4d12758758cdedfed6`，core 冻结在 `12eb4fcdc2be8d6599c862fae3b9d3a20b40409d`。整合 Host 的 `8337b27f` 复核退回整批，不为取得该记录合并 core；复核材料只读复制到本任务自己的私有根。跨侧引用清理与之后各批只排队，待整合 Host 明确通过并给新基线后才启动。

## 已固定的失败与修复选择

Host 已从原候选归档独立复跑整合方原样的默认入口探针：空 HOME、固定私有模型目录、用户原生 CLI 的不可用 sentinel、自有真实 Python 服务与 C-Two。显式状态 health 退出 0；没有 BUDDY_STATE_DIR 时普通 CLI、源码启动器、BoardClient(autostart=False) 均退出 1、PRIVATE_STATE_REQUIRED；只在公开边界解析目录的对照退出 0；自有服务停止并 wait 退出 0。没有真实模型或日常状态访问。复制材料哈希、原提交、任务根与原始失败保留在 `tmp/c073-host/default-state-red-owned-root.json` 和该台账指向的私有根；不改写整合方材料。

`call_service`、`call_board` 是公开调用边界，选择由它们解析状态目录，并让服务探测、冷启动与实际请求使用同一个结果。BoardClient 保留构造时存原参数、调用时选目录的现有时机：注入 call 的路径原样短路；autostart=True 复用 call_board，autostart=False 在自己调用边界解析后同时传给 attach 与 _request。这样不把默认目录冻结到构造时，不改变显式参数优先于环境、环境优先于默认 HOME 数据路径的现有语义。具体简洁实现由微任务产物核对，不为传输另造解析器。

内部 _request、rpc_config、Worker、控制器的缺根拒绝保留，不增加默认路径回退，不用 shell export 掩盖公开 Python 客户端问题。不改公开参数、schema、契约版本、停止/恢复判定、原生 harness、前端或已有三次付费冒烟；ZCode 按用户决定不调用模型。

## 独立微任务与写入边界

一个窄微任务 3-A 独立交付整个公开目录传递修复及回归，不并行修改同一接缝。唯一可写为 `src/hey_my_buddy/protocol/transport.py`、`src/hey_my_buddy/protocol/client.py`、新 `tests/python/protocol/test_public_state.py`、确有必要的 `tests/python/protocol/test_transport_attach.py` 与新 `docs/acceptance/c-two-public-state-repair.md`。已有 fixture/support、公共值、注册表、角色、Worker、CLI/启动器及其他文件由 Host 统一整合；发现接口缺口在交付中提出，不能自行扩大范围。计划与整合记录由 Host 写。

原 1-A run `a242d4a2-07d5-4b6a-ab20-7eec024a2d10` 已确认 accepted/completed，revision 20，原产物 `5c67e96f-90da-4bff-b201-18515aec15ac` / `552ea251` 保留。按本次整合 Host 明确授权的已签收例外，新建经路由的窄返修 run，登记旧产物与原因，不重复尝试继续已签收 run，不取消旧 run。首次四项配置全部省略；不可重试限流时按已有同 run 完整配置恢复规则并登记。范围内缺陷仍退回当前新 run continue，不由 Host 代改。

| 验证编号 | 微任务验收内容 |
| --- | --- |
| PS-01 | 固定原候选默认 CLI、启动器、默认 BoardClient 三项红灯；显式状态与公开解析对照为绿灯，真实私有服务退出 0 |
| PS-02 | 空 HOME、无 BUDDY_STATE_DIR 的普通 CLI、源码启动器、公开 BoardClient 默认调用经真实 RPC 成功；覆盖 autostart=True 与 False、call_service 与 call_board 接缝 |
| PS-03 | 已有私有服务的默认 stop/restart 分支正常，停止的服务确认为自己持有；没有服务时停止不冷启动 |
| PS-04 | 显式目录优先于环境，环境优先于默认，构造后才决定环境的原调用时语义保留；用服务身份或实际收到的目录确认路由目标 |
| PS-05 | autostart=False 不冷启动；只读 attach 不创建、不 chmod，不通过删除 HOME 或放宽权限解决 |
| PS-06 | 服务探测、冷启动和请求收到同一个公开解析结果；复用既有私有子环境、真实服务及固定模型目录夹具，不能让缺夹具进入原生发现 |
| PS-07 | 内部缺根拒绝仍通过，链接、路径、owner、真实 SDK 访问防护不放宽 |
| PS-08 | 从固定源码只去掉 call_service、call_board、BoardClient(False) 的目录传递，各自既有目标断言失败；真实 RPC 或目标错误原因须保留，任意导入/权限/超时失败不能算证明 |
| PS-09 | 全部旧测试编号保留，新增编号登记、未变化集合相等；固定产物审查、聚焦核对、整合后最终源默认并行数完整检查一次 |

## 核对、环境与收尾

微任务只跑受影响测试，不跑完整检查。建议新 public_state、transport_attach、rpc_config 与真实 service 的相关测试按各自独立解释器运行；实际目录、数目、命令、退出码、用时、失败与故障注入必须可重放。真实服务/冷启动的原生注册若受沙箱阻止，停止重复受限操作，交付固定助手与未验证边界，由 Host 在固定产物上补核，不能用 mock RPC 作为唯一证据。

Worker 开始时建立短的系统临时任务根、创建时记下确切路径，TMPDIR 与 BUDDY_CHECKS_TMPDIR 指入其中，依赖/cache、比较副本、夹具与一次性材料全部放进去；Worker 不删除任何文件或目录，框架自行收尾自建根除外。受管检出共用 stash/分支/标签：不使用 git stash，不新建、切换、移动或删除分支/标签，不自行提交，改动留工作区由黑板封存。交付报告根与所有未停止自建进程，验收后 Host 按确切路径回收受管检出与任务根，绝不手工删除 Buddy 管理检出或用通配符回收。

清除继承 BUDDY_*、ANTHROPIC_*、C2_*、VIRTUAL_ENV、UV_PROJECT_ENVIRONMENT，只用已确认归属的空 HOME、状态与运行时根；默认目录测试仅在私有 HOME 中暂时不设置 BUDDY_STATE_DIR，服务始终用明确的同一私有目录启动。模型目录必须是固定夹具，原生 CLI 用 sentinel 或已存在模拟夹具，不发现真实账户或调用真实模型。依赖只在任务根的 uv 环境使用已核对公开锁定材料，不安装/升级/重启/替换日常服务、Worker 或运行时，不改日常配置、数据或登录，不读凭据内容。Too many open files 或日常委派服务故障出现立即停止报告。

Host 核对固定补丁与风险接缝、实际聚焦测试、目标故障注入和编号后，整合登记并签收微任务；最终代码默认并行数运行一次 `uv run --frozen python -m hey_my_buddy.cli.checks`，保留历史失败。只改记录后只跑卫生，未改前端不跑前端套件。整合方已有 11 文件 218 项及两次 62 秒连接探针、三次已批准真实冒烟保留其原提交绑定，不为记录变化重跑。完成后提交记录并停在整批验收关口；微任务签收不等于整合 Host 验收。

## 路由与同 run 恢复登记

3-A run `ef9677e7-df3e-4f57-b03b-4c7add95b85f`，微任务基线 `9e45e890e0c249250621a12d165001c9c76d7e8e`。首次四项配置全部省略，路由决定 `dec-7705e392-3073-4d96-9576-986a5c4eca38` 选 ZCode/zai-api/GLM-5.3/max；实际 nativeFailure.attribution 为 provider rate_limited、statusCode=429、providerErrorCode=1310、retryable=false，两层停止确认，没有固定修复交付。按本批既有许可，用完整 configuration 与独立 reason 在原 run continue 到 Codex/openai/gpt-6.1-sol/high（曾完成本批 C-Two 实施与夹具返修），revision 5 已排队，范围与规则不变。没有修改用户路由设置或为 ZCode 重试相同限流回合；既有三个付费冒烟和用户暂不调用 ZCode 的决定不改写，微任务使用模型另按本 run 记录。请求、原始失败、路由与恢复响应保存在 `tmp/c073-host/public-state-repair*`。

## 首份固定产物退回与 Host 自建实例收尾

固定候选 `cb33c21792bee0c589dfeaaeefd50595951ac713`、artifact `1148145a-93ed-4e5b-950c-b7d1c6c93886`、补丁 SHA-256 `739ab46e7a541e276ac6b705fe2160f5d06d1d2b2fc2d298856ccc6d7a2326ec` 只有四个范围内路径。生产三个边界改动简洁，内部缺根拒绝不变；Worker assistance 仅声称 50 项沙箱可运行验证，没有真实 RPC green。Host 在固定源码独立跑新 public_state 全 22 项，退出 1、62.348 秒、3 failures；旧 attach 全 28 项退出 0、1.997 秒，rpc_config 全 24 项退出 0、9.253 秒。新文件的默认 CLI、启动器、客户端、覆盖和已有服务 stop/restart 已走真实私有 RPC，但整份测试尚未通过，不签收。

三个失败为新夹具范围内缺陷：cold-service/cold-board 的生产启动环境只传 src，替换为测试助手后没有补 tests/python，报 `ModuleNotFoundError: No module named 'support'`；readonly 把正在运行服务的 SQLite 状态树改成 0500，health 失败后未在 finally 恢复，收尾 stop 也失败并留下自己创建的 daemon。已按原 run continue（不指定配置，revision 9）打回，要求在测试内补齐私有启动环境、用实际 RPC 加现成变更观察器验证只读接入，并在任何失败下恢复夹具权限后 stop/wait，不能修改公共 launcher/support 或弱化断言。原始日志为 `public-state-host-{green,attach,rpc-config}.{json,log}`，首次完整新文件失败保留。

Host 只对自己这一批检查创建且 endpoint PID/serviceId 与台账相等的实例操作：PID 32358、serviceId `01bc5a84-ecdb-42a4-9626-9f00b5c5069d`。将该实例 state 的 0500 恢复 0700，使用既有显式-root stop helper 合作停止；helper 返回 0、服务锁释放，首次随即 PID 仍在，原观察与工具断言失败保留。稍后单独确认该 PID 消失、控制端点已移除；没有发送信号，没有采用其他会话的进程，没有宣称由 Host 对非直属 Popen 完成 wait。该操作是失败夹具的运行收尾，不是代改微任务代码。两个阶段证据为 `public-state-first-residual-stop.json` 与 `public-state-first-residual-stop-confirmed.json`，不把后一个结果覆写到前一个。

## 2026-10-11 第二次 Host 复核补充：3-B 排队

用户要求同一轮再修三处路径/权限/错误回归，复核记录只读取自 `e8f57266:docs/acceptance/c-two-074-host-review.md`，core 仍冻结 `12eb4fcd`。3-B 必须在 3-A 内部验收后开始，不并行修改 transport 接缝；目前仅准备计划与只读探针证据，未委派或修改产品。最终完整检查移到 3-A 与 3-B 全部整合后的源上一次执行，既有付费冒烟不重跑。

用户提供的 path_probe 已复制并核对哈希，分别在候选 `8e793196` 与基线 `12eb4fcd` 的独立归档副本运行，模型调用为 0。仅在探针外层记录并把 tempfile.mkdtemp 创建物约束到各自短私有任务根，原探针逻辑和产品源未改。候选符号链接路径 preflight 报 LAUNCH_ACCESS_DENIED、未解析到真实目录；0755 preflight 同码失败且保持 0755；基线两项均通过，路径变为真实目录、0755 变 0700；两份新 HOME 默认预检都通过。外层两个命令均退出 0，0.279 / 0.144 秒；退出 0 只表示比较完成，不能写成候选被测预检成功。证据与创建路径台账为 `tmp/c073-host/path-preflight-red-owned-root.json` 及该根中原探针/两个 stdout、stderr。两副本使用当前已锁定 0.7.4 解释器；该探针只覆盖文件、本地预检与公开路径选择，不外推旧运行时传输互通。

3-B 的生产选择：公开 get_state_dir 恢复基线的真实路径解析，角色下收到规范后的状态根；状态根之内的 ipc 等私有区继续拒绝链接和 `..`，不能从文件系统根拒绝用户选择的外部链接祖先。服务所有者在 cold_start_preflight 与 Daemon.run 恢复 mkdir/chmod 0700；只读 attach 不创建不 chmod。结构性 BoardError 原代码、消息与路径原样向上报告；系统拒绝写入/本地 IPC 才用 LAUNCH_ACCESS_DENIED，不能把所有 BoardError 当沙箱。移除 preflight 对 BUDDY_CHECKS_TMPDIR 的认识，使用已有通用临时目录机制/正常 TMPDIR，探针仍保留短私有根和合作收尾，不引入新的运行通道。

3-B 唯一写入范围拟为 `src/hey_my_buddy/protocol/{transport,rpc_config}.py`、`src/hey_my_buddy/blackboard/service/daemon.py`、`tests/python/protocol/{test_public_state,test_rpc_config,test_transport_attach}.py`、`tests/python/blackboard/service/test_daemon.py` 及新 `docs/acceptance/c-two-path-state-repair.md`；开始时绑定已验收 3-A 的整合提交。没有额外客户端、角色、注册表、support、launcher 或其他公共文件写权，缺口交 Host 统一整合。新增 PATH-01 链接可达的私有状态 CLI health 真实成功且返回真实根；PATH-02 state/ipc 链接仍拒绝并报告自身路径；PATH-03 0755 冷启动/直接服务启动后为 0700、0500 只读 attach 不 chmod；PATH-04 CLI 保留结构拒绝代码/路径，与模拟系统权限拒绝分开；PATH-05 各删除真实路径解析、所有者修正、错误保留后目标断言失败，并保留既有内部缺根拒绝与旧编号。每项真实服务采用空 HOME、私有根、固定模型目录与 sentinel；范围内缺陷原 run continue。

“已有 0755 状态可用”的旧记载仅在 rpc_config 能选域这一层成立，不是普通冷启动已可用；不抹掉历史证据，在 3-B 验收记录明确修正口径。3-A 当前针对公开状态参数的链接/路径断言随新用户决定逐项迁移到状态内部 guard，列编号与故障注入，不偷偷删除旧防护。整批最后核对编号和默认并行全检；之后只改记录跑卫生，然后停等整合 Host 明确结论。

## 3-A 第二份固定候选的 Host 核对

固定 `5b466de8c9bc61f4b81003ea485005cb62385585`、artifact `063b340b-897a-43f2-afa1-8c40d5fc5aba`、累积补丁 SHA-256 `74e4827cd7bfec3a877b8fe733be78d78df0461461d17e76197e9864495d4bd2` 仍只有四个范围内路径；两个生产文件与首份候选字节相等，旧 attach 文件未改。Host 对新文件全 26 项独立运行，通过、退出 0、27.411 秒（测试 26.704 秒）；默认 CLI、源码启动器、两个客户端、覆盖时机、既有 stop/restart、真实只读与两种真实冷启动均核对。旧 attach 全 28 项与 rpc_config 全 24 项的首份 Host green 因文件和生产配置未变继续适用，不因记录或夹具返修重跑。

新文件证据有 27 个独立子场景，22 个实际自建进程回执均 wait 退出 0，所有场景 RPC shutdown 确认、没有非预期 cleanupFailures，原生 CLI sentinel 调用 0；readonly 的真实 ping 同服务身份、entry snapshot 与权限均保留。收尾故障的三个单元场景是明确的 substitutesOnly，不计入实际 Popen 台账。证据为 `public-state-host-r2-green.{json,log}`、`public-state-host-r2-process-proof.json`；首次 22 项三失败与旧实例两阶段停止证据原样保留。

Host 从本份固定源码重建三份只取消最终目录传递的生产变异，各跑原真实 call_service、call_board、BoardClient(False) 目标：正常 RPC 已通过，取消后均恰好一项 failure、无 errors、退出 1，实际错误为 PRIVATE_STATE_REQUIRED；命令耗时 1.978 / 1.979 / 1.980 秒。另两份只去掉夹具 import 路径或 finally 恢复，分别命中新 import 目标的缺 support 证据与恢复目标的 320!=448 断言，0.644 / 0.301 秒；这两项是夹具防护，不作为生产缺根或真实 RPC 证据。五项没有以沙箱、任意传输或停止失败算红灯，原始计划、日志与结果为 `public-state-host-r2-target-proofs.json` 及对应 red 文件。

实际 unittest loader 从 3,474→3,500，214 个模块，原 3,474 个集合相等、删除 0、新增 26，无重复/装载错误；原始编号及新增差集为 `public-state-{baseline,fixed}-ids.json` 与 `public-state-id-delta.json`。本批当前只整合 3-A，不提前宣称三处新增路径/权限回归已修复，不进行中间全检；3-A 微任务签收后顺序启动 3-B，最终完整检查仍在两份整合后的源码上执行。

3-A 整合提交 `99cd2b0652a1156777472bb307bc6e70f6a16c2e`，黑板整合 `int-02a53b8c-c3a4-4146-b79d-92f2a2f6d26d` verified，原 run accepted/completed（revision 14）。这是内部微任务验收，整合 Host 的整批退回结论不变。3-B 起点绑定这个整合提交；为了把新增路径场景与已固定的公开目录回归分开核对，唯一写入范围在前述八路径之外增加新 `tests/python/protocol/test_path_state.py`，其余不变，共九路径。优先复用 3-A 与现有服务 fixture、MutationRecorder、固定 catalog 和子环境，不另造 RPC 或发现路径。旧编号中确需按新用户决定迁移的逐项说明，没有改变记录而掩盖验证结论。

3-A 清理计划 `cln-05e8a2be-1dd8-4a12-867c-f8a423ba006f` 原样 confirmPath apply，removed=true。两个 Worker 创建并报告的任务根 `/private/tmp/ps3a-34r52t5y`、`/private/tmp/ps3b-21te13_d` 分别留存 4,617 / 2,776 份源码、日志与台账文件后按这两个确切根整体回收；uv/cache/重复公开依赖材料未再留一份。已登记的自有 0500 故障目录按原权限残留台账恢复后回收，没有按名称或日期推断别的对象，不屏蔽删除错误。留存哈希与实际回收结果为 `public-state-retained-evidence.json`，没有手工删除受管检出。

3-B run `9ab4e50b-03dd-49ed-92ca-95d84e22961b`，实际任务基线为仅再增加计划记录的 `b772198d45525f7d2cff12474be4a4103c2e8115`，生产和测试与 3-A 整合相同。首次省略四项配置，路由 `dec-7520b7df-b208-48ad-8d1d-d9a2c7fab67b` 选择 ZCode/zai-api/GLM-5.3/max；保持已授权的供应方不可重试限流同 run 恢复规则，不改变用户路由设置。九路径的独立工作树已经启动，其他公共文件无写权，最终完整检查尚未运行。

该次 ZCode 实际返回 429/1310、nativeFailure.attribution.reason=rate_limited、retryable=false，原生退出 0、两层停止确认，未交付修复。根据原许可与本批同类交付，完整 configuration 与独立 reason 使同 run continue 到 Codex/openai/gpt-6.1-sol/high，revision 5；没有重试已限流 ZCode、修改全局模型或 Router 配置。原路由、失败与继续请求/响应留存为 `tmp/c073-host/path-state-{routing,failed-result,repair*}`，既有三次真实付费冒烟不重跑，ZCode 真实冒烟仍未验证。

## 3-B 固定产物的 Host 补核与整合

固定候选 `765d1c1faaff2aed423b3481c781d8967535c589`、artifact `8fcc5af5-190e-4bc2-aee1-fec313795446`、补丁 SHA-256 `8dc70f6124df2ecf2894e6df43fb319adf2e339431541022d32375ffb55223e3` 共八个路径，均在九路径范围内。Worker 明确记录 73 项沙箱可运行聚焦测试、真实 RPC 成功数 0、一次 console bind 被阻止及其自建进程 wait 退出；Host 不将受限验证当成功签收，固定副本完成以下补核。核心 `12eb4fcd` 未变化，第二次复核的整合分支只读使用，没有合入。

| Host 聚焦文件 | 项数 | exit | 命令墙钟秒 |
| --- | ---: | ---: | ---: |
| `protocol.test_path_state` | 12 | 0 | 6.836 |
| `protocol.test_public_state` | 26 | 0 | 28.293 |
| `protocol.test_rpc_config` | 24 | 0 | 8.819 |
| `protocol.test_transport_attach` | 28 | 0 | 1.381 |
| `blackboard.service.test_daemon` | 13 | 0 | 5.618 |

五个文件各自独立解释器、空 HOME、明确私有 state/runtime、固定 catalog 与原生 sentinel，共 103 项全部通过，日志为 `tmp/c073-host/path-state-host-{path,public,rpc,attach,daemon}.{json,log}`。真实 linked-cli 同时覆盖链接祖先和直接状态链接，两次 CLI health 及随后实际 RPC 的 serviceId、stateDir 与同一服务相符，实际传入路径为真实状态根，nativeRoot 为其 ipc。cold-755 在 preflight 返回瞬间为 0700，再真实启动；daemon-755 不走 preflight，直接启动后 0700。三个场景五个服务/CLI 直属 Popen 均 wait 退出 0、服务与 Worker 锁释放、RPC shutdown 完成、cleanupFailures 为空，native sentinel 没有调用。0500 只读核对用真实 C-Two tiny ping peer，不把 SQLite 访问混成传输结论；实际 RPC 到达、其余目录快照和权限 0500 不变，peer 请求停止并 wait 退出 0，RPC 收尾完成。实际 state/ipc 链接经 CLI 拒绝的 code/message/path 与 preflight 结构错误相等。

Host 从固定源码独立重建 13 份目标变异并运行原目标测试：公开 realpath、两处所有者 chmod/mkdir、BoardError 保留、内部链接/..、结构 path、测试环境耦合，以及 3-A 的三种实际 RPC 目录传递。每份恰好一项 assertion failure、无 errors，命中相应路径、权限、错误对象或 PRIVATE_STATE_REQUIRED 断言，不以沙箱或任意导入/超时算 red；3-A 的三个目标这次仍经过真实私有 RPC。13 份命令墙钟为 0.315–0.705 秒。Host 本地结果分类器最初对五种消息预期过窄，汇总退出 1；逐个读取未改日志确认目标断言后补充准确消息标记，不重跑测试、不改源或掩盖首次汇总。初汇总 `path-state-host-target-proofs.json` 与复核 `path-state-host-target-proofs-reviewed.json` 均保留，各 red 日志不覆写。

用户的原 path_probe 只读复制后在候选及冻结 core 各重放一次：两份均链接路径预检成功、解析到真实目录、0755 修正 0700、新 HOME 默认预检通过；命令各退出 0、0.143/0.144 秒。沿用此前相同外层短私有临时根约束和创建台账，不改探针，产品没有模型调用；该原探针是文件/本地预检对照，真实服务证据以上述新场景为准。台账 `path-preflight-green-owned-root.json` 保留原脚本哈希与两份输出，之前原候选 red 不改写。

实际 loader 3,500→3,512，214→215 模块，旧 3,500 编号集合全保留、删除 0、新增 12、重复和装载错误 0；新编号和集合证明在 `path-state-fixed-ids.json`、`path-state-id-delta.json`。旧 PS-07 及 rpc_config 链接断言按用户决定迁移到状态边界内，公开根链接改由 PATH-01 正向保护；旧 transport 的 owner setup 断言按恢复的 mkdir/chmod 改为实测行为，只读 guard 未削弱。产品 preflight 不再读取 BUDDY_CHECKS_TMPDIR，标准 tempfile 使用 TMPDIR；这项测试设施耦合没有生产必要。0755 只表示 rpc_config 可选 IPC 域，服务所有者冷启动和启动会修正 0700；只读 attach 不创建或改权限。

Host 没有修改微任务代码或测试，只整合固定补丁及在本计划登记独立补核和本地分类器说明。此结论仅为 3-B 内部微任务验收；整批仍等最终完整检查和 Claude Code Host。Worker 原记录的“待 Host 补核”保留原提交绑定，不回写成 Worker 做过真实成功验证。

## 用户补充 3-C：边界一次解析，内部必填

3-B 已整合 `8183fd05e0536f14446b5ca3f621f42fbfd6605e`，黑板整合 `int-cab5da29-2f54-45f8-acfd-86ea7b5a6a39` verified，原 run accepted/completed revision 10。其受管检出清理计划 `cln-f40af380-421e-4051-b21e-3471783333d6` 已 apply，removed=true；Worker 报告的 `/tmp/c073-b-cIzzzx` 尚保留，留存证据后由 Host 按确切根回收。两份修复成果保留，新 3-C 在 3-B 内部验收之后串行启动，没有同接缝的并行写入。

用户新要求抵达时，绑定 `8183fd05` 的默认并行全检已经启动，随后主动中断，命令会话退出 130，不是通过或完整结果。保留原启动/进程/输出及 `public-path-final-check-interrupted.json`；中断时检查器自己的 teardown 也被打断，Host 只对创建时绑定的该检查私有根重新调用现有 teardown_private_root，合作收尾与观测均完成、根已删除，没有信号/扫描清理日常服务。该检查不得当作最终完整检查证据，最终一次移到 3-C 整合后的源码上。

3-C 用一个串行微任务处理同一状态接缝。选择 `transport.get_state_dir` 作为唯一公开可选目录解析函数；CLI、BoardClient（保留既有调用时选择和注入 call 的边界）、call_service、call_board、ensure_service、request_stop 先解析，之后传真实 Path。公开入口相互复用时调用必要的必填内部 helper，避免重复选默认、重复读取环境或重复解析，不增加兼容包装。内部 _request/_healthy/_attach_read_only/preflight、rpc_config 三个 configure、C-Two endpoint/channel、roles/live 和 runtime/live 的参数一律必填，不读环境、不接受 None、不重做已成立的目录不变量。删除 rpc_config.resolve_state_dir 和 PRIVATE_STATE_REQUIRED 运行时规则；private_dirs.context_root 的同名内部兜底也纳入审查范围，不能换名字保留另一份冗余规则。

守护进程、Worker supervisor、控制器在进程入口一次读取父进程传来的真实根，缺失立即失败，不默认解析；同进程 helper 继续显式传 Path。保留 3-B 的私有 ipc 链接/路径防护和所有者 mkdir/chmod700、结构错误原样上报，只读 attach 不创建不改权限。_healthy 只把明确的服务无应答当作不健康；端点目录尚不存在直接判断，结构/配置/响应错误不能吞掉后触发冷启动。LiveWireRequest.deadline_monotonic 为必填有限数，发送方从创建请求时携带原始期限，服务端直接使用；删掉旧缺字段容忍，连接与调用期限仍由 C-Two 现成接口执行，原时间窗防止晚送达。

| 编号 | 3-C 验收 |
| --- | --- |
| SC-01 | 公开可选目录只留列举的入口及唯一解析 helper；显式/环境/默认优先级、调用时选择与空 HOME 默认真实 CLI/启动器/BoardClient 回归保留 |
| SC-02 | 内部状态参数全部必填真实 Path，无环境/客户端回退、无 resolve_state_dir/PRIVATE_STATE_REQUIRED；缺参数自然开发错误，不补重复运行时校验 |
| SC-03 | 自有内部进程无父目录在入口失败，未创建默认状态；有明确根成功，角色/Worker/daemon 接线显式传递 |
| SC-04 | 保留 PATH-01..04 的真实链接服务、0755 两处修正、0500 只读与结构 CLI code/message/path；已验收原探针/历史故障不改写 |
| SC-05 | 缺 deadlineMonotonic 的服务端请求拒绝、不排队不送达；合法/过期期限行为不变，连接/调用仍用原失败码和 transport_phase |
| SC-06 | 默认入口删解析、服务端重新允许缺期限时，目标测试各失败；必要的迁移防护做故障注入，不能以任意导入/沙箱/停止失败替代 |
| SC-07 | _healthy 的真实无应答允许返回不健康，结构及其他错误直接失败；不存在的 ipc 用条件，避免异常控制正常流程 |
| SC-08 | 实际 loader 对照 3,512 基线；新增/删除/改名逐项列出，删规则的测试写明对应规则，未变化集合相等；全部整合后的最终源默认并行数完整检查一次 |

唯一写入范围按已查到的 configure、endpoint/channel、runtime 与 controller 调用方列为下面的精确清单，包含必要生产入口、private_dirs 内部兜底及对应测试、fixture/support、新专属记录；Host 独占计划、整批记录和最终整合。微任务不自行增范围，发现新调用方必须提出并经本轮 continue 修订 scope；不能以动态重导出、环境补值、兼容函数或异常兜底绕过。保护文档仍由 Claude Code Host 维护；本轮取代的生产注释、未使用 helper 和旧规则测试删掉，历史验收记录保留并由新记录说明替代关系。

- `docs/acceptance/c-two-state-boundaries.md`
- `src/hey_my_buddy/blackboard/service/daemon.py`
- `src/hey_my_buddy/blackboard/service/service.py`
- `src/hey_my_buddy/buddy/harnesses/c_two_live.py`
- `src/hey_my_buddy/buddy/roles/live.py`
- `src/hey_my_buddy/buddy/roles/run_controller.py`
- `src/hey_my_buddy/buddy/roles/run_execution.py`
- `src/hey_my_buddy/buddy/roles/structured_call.py`
- `src/hey_my_buddy/buddy/runtime/api.py`
- `src/hey_my_buddy/buddy/runtime/live.py`
- `src/hey_my_buddy/buddy/runtime/supervisor.py`
- `src/hey_my_buddy/buddy/runtime/worker.py`
- `src/hey_my_buddy/cli/main.py`
- `src/hey_my_buddy/private_dirs.py`
- `src/hey_my_buddy/protocol/client.py`
- `src/hey_my_buddy/protocol/rpc_config.py`
- `src/hey_my_buddy/protocol/transport.py`
- `tests/python/blackboard/routing/test_stage2_review_scope.py`
- `tests/python/blackboard/service/test_daemon.py`
- `tests/python/blackboard/tasks/test_inquiry.py`
- `tests/python/buddy/harnesses/claude/test_native_run.py`
- `tests/python/buddy/harnesses/codex/test_native_run.py`
- `tests/python/buddy/harnesses/dsh/test_dsh_role_wiring.py`
- `tests/python/buddy/harnesses/dsh/test_native_run.py`
- `tests/python/buddy/harnesses/dsh/test_no_tool_dsh.py`
- `tests/python/buddy/harnesses/fixtures/c_two_live_peer.py`
- `tests/python/buddy/harnesses/test_c_two_live.py`
- `tests/python/buddy/harnesses/test_inquiry_owner.py`
- `tests/python/buddy/harnesses/test_live_channel.py`
- `tests/python/buddy/harnesses/zcode/test_native_run.py`
- `tests/python/buddy/harnesses/zcode/test_zcode.py`
- `tests/python/buddy/harnesses/zcode/test_zcode_inquiry.py`
- `tests/python/buddy/roles/test_registered_review.py`
- `tests/python/buddy/roles/test_registered_run_wiring.py`
- `tests/python/buddy/roles/test_role_live_seam.py`
- `tests/python/buddy/roles/test_schema_worker.py`
- `tests/python/buddy/runtime/fixtures/live_runtime_peer.py`
- `tests/python/buddy/runtime/test_live.py`
- `tests/python/buddy/runtime/test_live_lifecycle_integration.py`
- `tests/python/buddy/runtime/test_worker_invariants.py`
- `tests/python/cli/test_cli.py`
- `tests/python/cli/test_host_cli.py`
- `tests/python/cli/test_support_cleanup.py`
- `tests/python/protocol/test_activity.py`
- `tests/python/protocol/test_ctwo_integration.py`
- `tests/python/protocol/test_inquiry_transport.py`
- `tests/python/protocol/test_path_state.py`
- `tests/python/protocol/test_public_state.py`
- `tests/python/protocol/test_rpc_config.py`
- `tests/python/protocol/test_state_boundaries.py`
- `tests/python/protocol/test_transport_attach.py`
- `tests/python/support.py`
- `tests/python/test_private_directories.py`

微任务首次四项配置全部省略经路由；供应方实际不可重试限流才按原许可用完整配置/理由同 run 恢复并登记。只跑受影响文件，不跑整批完整检查或付费冒烟。Worker 开始时建立短任务根、记下确切路径，TMPDIR/BUDDY_CHECKS_TMPDIR 指入其中，不手动删除任何文件目录、不用 git stash、不新建切换移动删除分支标签、不自行提交；一次性材料和变异留根交付，由 Host 验收后按确切根回收。清除继承 BUDDY_/ANTHROPIC_/C2_ 与虚拟环境，空 HOME、私有 state/runtime、固定 catalog/sentinel，绝不触达真实原生发现、凭据、日常服务或公共端点。日常委派 Too many open files 立即停止报告，不重启或替换。范围内缺陷原 run continue，Host 不代改；独立固定产物审查、实际聚焦和目标变异之后再整合签收，三步全部整合后最后完整检查并停等整批 Host。

3-C 新 run `df9f35ac-5df8-43b9-89e3-58e0b5ef3bb8` 绑定计划提交 `5f7e3b2a`，首次四项配置全部省略，路由决定 `dec-50783657-382b-46b8-8977-c900b7b3753e`，初响应 revision 1、awaiting-model-selection。有且只有一个仅监控的 await，不以工具等待超时当作交付。3-B 的 Worker 精确任务根 `/tmp/c073-b-cIzzzx` 已留存 20,894 份源码、变异、日志和台账后按该一个根整体回收，removed=true；留存哈希与回收账为 `tmp/c073-host/path-state-retained-evidence.json`。没有手动删除受管检出、其他会话或日常对象。

3-C 路由实际选择 ZCode/zai-api/GLM-5.3-Flash/max，决定 `dec-50783657-382b-46b8-8977-c900b7b3753e`。供应方实际 429/1310 rate_limited、retryable=false，两层停止确认，原生 exit 0、没有固定交付。按既有许可在同 run 用完整 configuration 和独立 reason continue 到 Codex/openai/gpt-6.1-sol/high（完成过本批 3-A/3-B 与 C-Two 同类工作），revision 5、awaiting-worker。原失败、get 和继续请求/响应留存 `tmp/c073-host/state-boundaries*`；不修改用户路由偏好、不重试无额度 ZCode，不把委派模型调用写成既有三次付费冒烟的重跑。

## 3-C 首份固定交付与范围修订

首份候选 `65bd6f1f8d859c7fb0690a4ecefff989d22d7011`、artifact `9acc5797-953d-48ef-a051-f7decae923e5`、补丁 SHA-256 `b04e10f9e6a768cee8718e2d9a5f7602c22a83c99ec46483457f147d1b4604d1` 共 42 个路径、全部在初始 53 路径内。RPC 路径列表的投影显示 changedPathsTruncated，Host 首次封存助手拒绝后从绑定 base/output 的不可变 Git diff 取完整 42 路径、确认原投影是其子集、固定补丁 SHA-256 相等，再建立独立归档，不把截断列表当完整。Worker 记录 567 项可运行聚焦、四处目标变异，以及八删除/十二新增的 loader 对照；真实 RPC 沙箱失败与残留观察未知如实交付，尚未签收。额外只读生产接缝审查未发现已知范围缺口之外的新阻塞，不作为真实测试通过。

Host 在固定副本补核 path_state 全 12 项退出 0、6.801 秒，public_state 全 26 项退出 0、27.858 秒，真实 3-A/3-B 场景通过；C-Two 全文件 70 项退出 1、5.837 秒、18 failures，Worker live 全文件 34 项退出 1、4.416 秒、6 errors。原始 `state-boundaries-host-{path,public,ctwo,live}-r1.{json,log}` 保留：前一缺陷是 c_two_live_peer 使用 Path 却未导入，后一缺陷是 live_runtime_peer 的 controller 建 CTwoLiveEndpoint 未传必填 state_dir，stderr 被现有 DEVNULL 隐去，只见私有 peer pipe 关闭。两处都是原范围 fixture 缺陷，退回原 run，Host 不代改。语法通过不足以证明这些 fixture 实际可用。

按用户明确的范围修订许可，把六个已查到的测试调用方纳入；blocking.await_run 和 console_cli.run 属于 CLI 内部 helper，必须消费 main 已解析 Path，不再有默认或调用公开解析的重复选择。catalog.discover 位于黑板内部、生产由 service/store 明确传目录，不是新公开状态入口，也改必填 Path，删除其目录环境 fallback，保留账户/模型语义，直接调用的离线测试显式提供自有根。没有对外新增入口或改变 CLI schema，console 仅 Python 状态传递，无前端变化。

附加唯一写入路径如下，原 53 路径全部保留，总 66 路径；Host 仍独占计划和整合记录。continue 没有 executionWorkspace 变更字段；Host 起初只用 input 登记范围许可，漏查了已有 scope-amend 操作，没有更新黑板的机械 scope 版本。这是 Host 的流程错误，不能把该 input 当作已完成 scope-amend。原始 scope、修订理由、基线和新 scope 为 `tmp/c073-host/state-boundaries-scope-amendment.json`，Task/Host 验收以该继续输入为依据。

- `tests/python/blackboard/service/test_service_environment.py`
- `tests/python/buddy/runtime/test_worker_runtime.py`
- `tests/python/blackboard/tasks/test_workspace_api.py`
- `tests/python/blackboard/tasks/test_workflow_worker.py`
- `tests/python/cli/test_cli_views.py`
- `tests/python/blackboard/catalog/test_account_operations.py`
- `src/hey_my_buddy/cli/blocking.py`
- `src/hey_my_buddy/cli/console_cli.py`
- `src/hey_my_buddy/blackboard/catalog/catalog.py`
- `tests/python/cli/test_blocking.py`
- `tests/python/console/test_console_cli.py`
- `tests/python/blackboard/catalog/test_catalog.py`
- `tests/python/blackboard/evaluation/test_evaluation.py`

Worker 如实披露第二次误触真实 bind、数字 wait 记录缺口和任务根外 `/tmp/state-calls-readonly-audit.txt` 的写入。Host 只读观测已登记的 `/tmp/c073-c-H6SbNM`：没有与该根绑定的当前活进程、观测问题或持有 daemon/supervisor 锁；这个当下观察不能补造过去缺失的 wait/两层停止回执。根外文件先保留，要求 Worker 用自身原始命令记录说明新建或覆盖的证据，不能按名称/日期推断归属或删除；首次披露与违规口径保留。原 run continue 还须正确接线新增调用方并保留全部旧失败、只跑受影响测试、无真实沙箱重试和无付费模型调用，真实环境补核由 Host 对最终固定候选完成。

补核接口后发现 scope-amend 必须在所有相关回合已确认停止、没有冻结旧 scope 的待执行 continuation 时执行；不能追溯授权活动回合。Host 发现顺序错误时 R2 已 running、scopeVersion 仍 1、self stop 未确认，未调用一个必然被拒绝的修改，也不取消并重建 run或手动改manifest/数据库。等待该回合停下后先核对合法固定交付/机械scope失败现场，有新增路径时按已有workspace-resolve保全与核验，再scope-amend为下一回合记录66路径并在原run continue；当前回合的旧scope与错误顺序保留，任何失败不能改写为通过。

## 3-C 机械范围失败的固定保全与正式修订

R2 在 scopeVersion 1 下封存失败，conflict `wsc-b68ab678-4458-4225-90a6-8e923b18340d`，observedFingerprint `eeb0a6cff67f83d36af2c765736942c3f246a79247cf29765f81a2bed56c6cff`，两层停止均确认。13 个 blockingPaths 与 f22d8450 明确许可的 13 个新增路径集合完全相等，实际文件哈希与失败现场各 observed.sha256 相等。Host 用正式 workspace-resolve/adopt 保全可审查产物 `217e3f07-7f4f-4d65-8f80-82b67adb3573` / `f86215878e746cbb3b5e9c50d0d9374a961467dc`，累积补丁 SHA-256 `fa2e641dbbba4ef176d839934bdd89c0ac7f41bcd6ea6d99bc1cef04e7acea70`，55 路径，没有手动修改 manifest/数据库/工作区。adopt 不是代码签收，原 state=failed 和 scopeVersion 1 的历史不改写。

随后 scope-amend（expectedScopeVersion=1）在确认停止和无待执行 continuation 时记录 scopeVersion 2 的 66 路径；R2 又暴露一个必填 await_run 调用方 `tests/python/blackboard/tasks/test_workflow_stop_surface.py`，四个旧断言只需传自有根，Host 在下一次 continue 之前用 scope-amend（expectedScopeVersion=2）正式追加此路径，当前 scopeVersion 3、67 路径、revision 15。这种当前授权只用于之后的回合，不追溯当前已失败的 R2。Host 封存助手也校验实际黑板 scope，恢复被截断路径列表只使用固定 base/output Git diff 和补丁哈希；不把本地 input 文本替代机械授权。

R2 如实补充：根外审计输出用普通重定向 `>`，没有独占创建或写入前存在检查；是否截断既有内容、写前归属均未知。`/tmp/state-calls-readonly-audit.txt` 原样保留，不按名称/内容/日期推断归属，不纳入 Host 自有根回收。沙箱第二次受限 bind、旧数字 wait 缺口和 R2 service_environment 两处误触 preflight bind 的失败亦保留，不能写成“未绑定”或“全部停止”。当前根只读观察不能补造过去回执。本轮源码尚未整合，最终完整检查未开始。


## 3-C 最终固定交付的 Host 独立核对

R3 固定输出 `dbffa3ab29828250f4d387141bb8b386b5ab6c03`，artifact `69cc7551-cb7d-4e99-a69f-0440c48bbb6b`，累积补丁 SHA-256 `c01aa715f8876a15bed436604ac1e00e6416a6d527af3653a79bebfd743132bf`，相对基线 5f7e3b2a 共 56 路径，均在实际 scopeVersion 3 的 67 路径内。Host 逐项读取生产接缝与六个 R2→R3 差异，核对单一 get_state_dir、公开入口一次解析、内部必填 Path、进程入口一次父目录检查，以及必填 deadline 与两跳原截止时刻；固定源码归档与补丁绑定为 `tmp/c073-host/state-boundaries-fixed.json`。3-A/3-B 成果和历史反例保留，本轮没有代改 Worker 代码或测试。

Host 在空 HOME、明确私有状态与运行时根、清除继承的 BUDDY_/ANTHROPIC_/C2_ 和虚拟环境后运行下面 15 个受影响选择，实际 201 项全部退出 0。原始命令、用时及日志为 `tmp/c073-host/state-boundaries-host-final-<label>.json/.log`；不是 Worker 自报或语法检查。

| label | 项数 | exit | seconds |
| --- | ---: | ---: | ---: |
| `public` | 26 | 0 | 30.092 |
| `path` | 12 | 0 | 8.072 |
| `attach` | 28 | 0 | 1.471 |
| `boundaries` | 9 | 0 | 1.287 |
| `console-canonical` | 36 | 0 | 1.053 |
| `stop` | 8 | 0 | 2.558 |
| `service-env` | 12 | 0 | 0.673 |
| `workspace` | 4 | 0 | 20.227 |
| `worker-runtime` | 4 | 0 | 57.817 |
| `cli-views` | 11 | 0 | 18.055 |
| `blocking` | 26 | 0 | 1.659 |
| `catalog` | 10 | 0 | 1.698 |
| `account-ops` | 8 | 0 | 1.781 |
| `workflow-worker` | 5 | 0 | 22.022 |
| `eval` | 2 | 0 | 0.910 |

首次 console 全 36 项在 Host 助手传入 `/tmp` 别名而非真实私有目录时 3 项失败、exit 1、0.550 秒；公开解析得到 `/private/tmp`，造成目标 Path 不相等。Host 只修自己忽略目录助手 repair_check.py 的任务根 resolve，未改产品或测试；同一固定源码单独重跑 console-canonical 的 36 项退出 0、1.053 秒。第一次日志与结果保留。Host 一次 commentary 把聚焦总数错加为 211，按原始 15 份日志复算为 201，已更正；不改变做过哪些验证的结论。

真实 C-Two 全 71 项退出 0、16.045 秒与 Worker live 全 35 项退出 0、7.195 秒是在 adopt 的 f86215878e74 固定源码上执行。R3 的后端、角色绑定、rpc_config、两份测试及对端 fixture 共八个文件 byte/SHA-256 完全相同，复用 106 项已有真实 IPC 结果；逐文件绑定为 `state-boundaries-host-final-review.json`，原日志为 `state-boundaries-host-ctwo-r2`、`state-boundaries-host-live-r2`。覆盖新连接及已有连接的 pre_dispatch 期限、dispatch_uncertain 后对端仍完成且同连接可用、连接消耗预算后晚请求不送 owner、正常停机与只杀自建控制器后的凭据回收及端点 absent。没有真实模型调用，没有公共端点清扫。

Host 在最终 R3 归档上独立建立六个窄变异副本，正常目标六项退出 0；变异六项均恰好一个目标 assertion failure、没有 unittest errors、导入失败或沙箱拒绝。逐份原/变异 hash、完整目标 ID、命令、退出码与用时为 `tmp/c073-host/state-boundaries-host-final-target-proofs.json`。缺 deadline 变异同时恢复 nullable 字段及服务端容忍，业务窗口到期返回 request-window-expired 与要求 frame-invalid 的断言不符，1.5 秒来自业务窗口，不是外层 watchdog。

| 变异 | green exit / seconds | red exit / seconds | 目标 ID |
| --- | --- | --- | --- |
| `public-resolution` | 0 / 0.674 | 1 / 0.581 | `protocol.test_public_state.PublicStateTests.test_ps08_call_service_directory_parameter` |
| `missing-deadline` | 0 / 0.240 | 1 / 1.724 | `buddy.harnesses.test_c_two_live.WireFrameTests.test_missing_deadline_is_refused_before_queue_or_native_delivery` |
| `worker-cutoff` | 0 / 0.223 | 1 / 0.248 | `buddy.runtime.test_live.WorkerLiveUnitTests.test_worker_forwarding_preserves_original_deadline_and_refuses_missing_or_expired` |
| `invalid-health` | 0 / 0.162 | 1 / 0.187 | `protocol.test_transport_attach.TrustBoundaryTests.test_invalid_health_reply_fails_without_cold_start` |
| `peer-import` | 0 / 0.238 | 1 / 0.256 | `buddy.harnesses.test_c_two_live.ReadyMaterialTests.test_private_peer_reaches_start_with_explicit_state` |
| `controller-root` | 0 / 0.258 | 1 / 0.238 | `buddy.runtime.test_live.WorkerLiveUnitTests.test_private_controller_reaches_start_with_parent_state` |

实际全仓 loader 为 3,519 个唯一 ID、216 模块、重复 0、loaderErrors 0；与 3-B 的 3,512/215 比较：未变集合 3,504 严格相等、减少 8、增加 15。八个减少编号及对应规则、十五新增/改名编号逐项见交付 c-two-state-boundaries.md；Host 集合复算为 `state-boundaries-final-ids.json` 与 `state-boundaries-host-id-delta.json`。没有靠重复 import 维持数量。

可选状态入口最终仅 CLI、BoardClient、call_service、call_board、ensure_service、request_stop；唯一解析 helper 为 transport.get_state_dir。AST 的可选参数函数为 BoardClient.__init__、四个 transport 公开操作及 get_state_dir，CLI 通过既有命令参数与环境接受可选根；cli.checks.create_private_root(directory=None) 选择的是检查器临时目录，既不是服务状态入口也不是第二个状态解析函数。内部 await_run、console_cli.run、catalog.discover 以及本批列举的请求/configure/live 函数都必填 Path，没有环境/客户端回退。完整审计 `state-boundaries-optional-functions.json`。公开依赖路径的行为、链接/0755 owner 修正、0500 只读与结构错误 code/message/path 都由最终 public/path/attach 真实私有回归确认；公开 envelope、schema 和契约版本未改。

Root 整合登记：范围操作顺序错误由 Root 承担，原 scopeVersion 1 封存失败及 adopt 保全不改写，正式版本 2/3 只用于后续回合。R1/R2 fixture 缺陷均原 run continue，readonly 重复流程由原 Worker 合并为一份，不由 Root 代改。Root 只改自己的固定归档/检查助手、数目口径与本计划，不触碰用户 stash、分支标签或保护文档。任务根 `/tmp/c073-c-H6SbNM` 在签收前仍保留，当前无绑定进程/持有锁的观察不能补造旧 daemon 数字 wait 回执。根外 `/tmp/state-calls-readonly-audit.txt` 写前归属与实际覆盖未知，原普通重定向命令留存，文件原样保留、不纳入回收。

这次是微任务内部核对，尚未完整检查、整批 Host 验收或日常安装。已有 Codex/Claude Code/DSH 各一次授权真实冒烟沿用原提交证据；ZCode 无额度未验证，不重跑模型。三步整合后在最终提交上运行一次默认并行数完整检查；完整结果与精确回收随后追加本记录。


## 3-C 整合首轮完整检查退回：剩余夹具调用方

绑定整合提交 `dcc265b0e091b9bf68c3719f3de82d210e54040d`、core `12eb4fcdc2be8d6599c862fae3b9d3a20b40409d` 的完整检查已结束：命令 `uv run --frozen python -m hey_my_buddy.cli.checks`，默认 4 并行，exit 1、706.360 秒，216 文件中 209 通过、7 失败；通过文件共 3,420 项（跳过 1），不能写成 3,519 全通过。3,519 是实际 loader 编号集合。负载开始 21.02/17.52/17.43、结束 16.47/21.71/21.19，中途约 34；下面都有确定的调用方错误，不归因于负载。原日志、提交/根/进程绑定、汇总为 `tmp/c073-host/state-boundaries-final-check*`。3-C 固定源码的既有 201+106 聚焦结果保留其绑定，不覆盖全检失败；原 run 未签收，可直接 continue。

失败文件为 blackboard.service.test_liveness、buddy.harnesses.dsh.test_dsh_role_wiring、buddy.harnesses.test_inquiry_owner、buddy.harnesses.zcode.test_zcode_checkpoint、buddy.harnesses.zcode.test_zcode_inquiry、buddy.harnesses.zcode.test_zcode_tool_refusals、protocol.test_ctwo_integration。liveness 模拟健康 endpoint 却缺其私有 ipc 目录；DSH、ZCode 两个 live helper 仍传 environment 字符串而不是必填 Path，造成已有归属断言失败，checkpoint/tool_refusals 复用 test_zcode 的同一 helper；inquiry owner 子进程把 JSON 字符串未经边界转换传给 endpoint；model-free 整链的 ct_controller fixture 漏传 OneSlowCallEndpoint 必填 state_dir，Worker 轮次提前结束，随后合作 stop 的原断言也失败。原始失败与次级清理错误全部保留。

按用户明确的“范围不够就修订范围”许可，在 3-C 原 run 追加 tests/python/blackboard/service/test_liveness.py 与 tests/python/protocol/fixtures/ctwo_controller.py，两条新增路径使正式范围从 67 到 69；先在停下的边界 scope-amend v3→v4，确认成功后才原 run continue，不再用文本授权替代机械范围。R4 实际唯一修改限于这两份 fixture、既已许可的 test_dsh_role_wiring.py、test_inquiry_owner.py、test_zcode.py、test_zcode_inquiry.py 及交付记录。冻结产品代码；保留所有原断言/编号，不加兼容、不恢复内部解析、不放宽健康/截止时刻规则；Host 不代改。各修补必须经独立聚焦与对应目标故障注入，受限真实 bind 不重复，完整检查由 Host 在最终代码上重跑并保留本轮 exit 1。

Host 汇总助手最初用逐行末尾锚定遗漏一行被并发 stderr 拼接的成功输出，断言拒绝；一次探查脚本有括号语法错误，均未跑测试或改变源码。已按实际逐文件前缀、最终 216/209/7 汇总核对，原日志不改写；检查器私有根是否自行移除以结构化 summary 里的实际存在性为准。


机械范围修订已成功：scopeVersion 4、69 路径、revision 21，新增仅 liveness 与 ct_controller 两份测试 fixture。随后在同 run continue，commandId `c2-state-boundaries-v4-final-fixtures-continue-20261011-v1`、revision 22，四项配置全部省略；本回合实际可写六份夹具和交付记录，产品源码冻结。只有一个仅监控 await，原输出 dbffa3ab、整合 dcc265b0 和 `int-99cf5fe7-a471-44dd-b7d4-1a66f54bec5a` 的 verified 登记保留；完整检查 exit 1 后没有 acknowledge accepted，可在原 run 返修。检查器自建根已由其正常 teardown 移除，外层 Host 根与全部原始日志保留。


## 3-C R4 最终夹具交付的独立补核

固定输出 `70e949439f5ec962407fbe00c92c2efe20284c9d` / artifact `989e8292-6be5-4a54-88d4-788e668df0fd` / 累积补丁 SHA-256 `7f678e7e563d09a5ece2f3c6c0338fe2314d8ba0f26ec0144dc4df14f45a9949`，累计 61 路径，全部在实际 scopeVersion 4 的 69 路径内。R3→R4 恰好为六个许可 fixture 与交付记录七路径；全部 src Python byte/hash 与 dbffa3ab 一致，未改任何产品源码，也没有额外改测试或保护文档。Host 逐行核对实际 Path 接线、健康模拟的 ipc 前提、JSON 子进程边界、控制器 main 的一次 parent-state 检查，以及删除 make_endpoint 的默认参数并接上五个使用方。

Host 用最终固定副本、空 HOME 和各自明确私有根补核七个原失败文件，共 99 项全部通过；额外核对共享 test_zcode 全文件。下面是第一次两进程聚焦批次的实际结果，不能把第八行的失败算进通过。

| 文件 | 项数 | exit | seconds |
| --- | ---: | ---: | ---: |
| `blackboard.service.test_liveness` | 5 | 0 | 0.893 |
| `buddy.harnesses.dsh.test_dsh_role_wiring` | 12 | 0 | 6.409 |
| `buddy.harnesses.test_inquiry_owner` | 15 | 0 | 1.969 |
| `buddy.harnesses.zcode.test_zcode_checkpoint` | 10 | 0 | 13.712 |
| `buddy.harnesses.zcode.test_zcode_inquiry` | 47 | 0 | 7.016 |
| `buddy.harnesses.zcode.test_zcode_tool_refusals` | 8 | 0 | 5.544 |
| `protocol.test_ctwo_integration` | 2 | 0 | 11.273 |
| `buddy.harnesses.zcode.test_zcode` | 25 | 1 | 29.639 |

额外 test_zcode 的取消目标 test_cancel_and_unconfirmed_stop_are_distinct 第一次得到 failed 而非 cancelled，控制器没有完整结果、SIGTERM/-15、shutdownConfirmed=false；原 25 项/exit 1/29.639s 和完整原报告保留。自有并行批次全部结束后，独立解释器在同一固定源码单独重跑整个文件 25 项/exit 0/31.935s，tests自报31.613s。未改代码、断言、等待时间或首次结果，不据此断言初次一定是负载。日志分别为 `state-boundaries-host-r4-buddy-harnesses-zcode-test_zcode` 与 `state-boundaries-r4-zcode-alone`。七份原失败文件和这次单文件最终通过共 124 项，各自固定源码绑定保留。

Host 独立从最终归档新建六个窄变异副本，分别去掉健康 ipc mkdir、DSH/ZCode/活动 Path、owner JSON 转 Path 与控制器 endpoint 状态参数；原集成目标各 green 退出 0，变异各退出 1且有指定目标 assertion，非导入/权限/外层超时错误。controller-state 的原 primary 断言与合作 stop=False 的次级清理 error 均保留，未吞掉来制造单一红灯；owner-json 的真实 peer TypeError 经原 started.ok 断言呈现。准确原/变异 hash、源码节点、目标及结果为 `state-boundaries-host-r4-target-proofs.json`。

| 变异 | green exit / seconds | red exit / seconds | 原集成目标 |
| --- | --- | --- | --- |
| `healthy-ipc` | 0 / 0.321 | 1 / 0.315 | `blackboard.service.test_liveness.LivenessTests.test_each_client_attach_uses_light_ping` |
| `dsh-path` | 0 / 0.878 | 1 / 0.799 | `buddy.harnesses.dsh.test_dsh_role_wiring.WorkerRegisteredRunTests.test_an_answered_host_question_flows_through_the_governed_turn` |
| `zcode-path` | 0 / 0.897 | 1 / 0.792 | `buddy.harnesses.zcode.test_zcode_checkpoint.ZcodeCheckpointFlowTests.test_one_send_delivers_and_answers_a_question_and_finishes_completed` |
| `activity-path` | 0 / 2.877 | 1 / 0.897 | `buddy.harnesses.zcode.test_zcode_inquiry.LiveActivityTests.test_running_same_phase_native_events_refresh_the_published_observation` |
| `owner-json-path` | 0 / 0.621 | 1 / 5.477 | `buddy.harnesses.test_inquiry_owner.InquiryOwnerPeerTests.test_real_ctwo_owner_queue_requires_verified_cooperative_native_delivery_and_answer` |
| `controller-state` | 0 / 5.708 | 1 / 7.303 | `protocol.test_ctwo_integration.CTwoIntegrationTests.test_normal_stop_and_same_connection_after_deadline` |

Host-only 观察助手只记录原持有 Popen 的实际 wait/poll-reap 和原 cc.shutdown 返回，不添加信号、停止 RPC、修改结果或延长时间窗。七文件、首次第八文件及所有点变异的 observed held leaders 都获得退出码、unconfirmedPids=[]，观测到的 SDK shutdown 全 completed=true；深层的 controller/native 两层事实来自原 ct_integration fixture 的实际 native EOF/回执、Worker 停止与 endpoint inspect/reap 断言。未用“进程退出”替代未知原生停止。真实服务→Worker→模拟控制器整链两项通过，包括正常关闭、只杀自建 controller 后确认并凭据回收、外来 peer 保留、同连接期限后可用；协调文件与端点文件分开。原始 stdout/stderr、精确子进程回执、未知字段在同名 JSON/log 与 `state-boundaries-host-r4-focused.json`，没有调用模型或默认公共域。

Host 独立 loader 再核：3,519 唯一 ID、216 模块、重复 0、loaderErrors 0，R3→R4 集合严格相等、增删 0；原 3-B→3-C 的 8 删除/15 新增及逐条规则对应保留。结果为 `state-boundaries-r4-ids.json`；原七失败文件99项加先前通过3420项恰好3519，不把编号清单当新整批检查通过。

Root 整合只应用 dbffa3ab→70e94943 的七路径差异，再追加本计划；不重放已经整合的 R3 累积补丁，不代改 Worker 代码或测试。原 scope1 失败、正式 v2/v3/v4 与同 run continue、全检 dcc265b0 exit1、首次取消失败及当前未知历史回执均保留。最终完整检查仍待在本次整合提交上运行，原 run 尚未签收，以便范围缺陷继续打回；回收与最后全检结果另附。
