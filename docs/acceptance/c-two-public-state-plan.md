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
