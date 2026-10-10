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
