# ADR-036：用 C-Two 话题唤醒取代独立等待资源

## 状态

已接受。

- 开发者 2026-11-22 决定在 `0.77.0`（契约版本 `0.77.0`，Schema 维持 `43`）升级至 `c-two==0.12.0` 后，用单一 `BuddyControl` 资源上的 `@cc.topic`（`board_changed`）与单次非阻塞读 `task_observe` 取代独立阻塞等待资源 `BuddyWait`，同步移除 `WaitAdmission`、`WAIT_OVERLOAD` 与 `HMB_WAIT_CAPACITY`。
- 实施前经一轮独立只读架构审查并结合 `c-two==0.12.0` 源码与跨进程探针核实，补齐了 `CallCapacityExceeded (717)` 可重试分类、安静守护进程下的本地可中断等待、单连接多路复用、`task_observe` 归属权限边界与升级/回退阶段旧 `HMB_WAIT_CAPACITY` 的平滑交接。

## 背景

早期黑板通过第二套 C-Two 资源 `BuddyWait` 暴露 `task_wait` 与 `events_wait` 两个服务端长阻塞 RPC：每个等待中的宿主或客户端独占一条服务端工作线程，并依赖自建的 `WaitAdmission` 令牌桶、`WAIT_OVERLOAD` 过载重试与 `HMB_WAIT_CAPACITY` 启动配置把等待槽位压在 `max_execution_workers`（64）之下（默认 32、上限 48），避免长阻塞等待饿死控制面。

`c-two==0.12.0` 在 Rust IPC 传输层原生引入了发布/订阅话题（`@cc.topic`、`Publisher` / `Subscription`）：服务端发布变更帧不占用 Python 工作线程，客户端在同一条 `BuddyControl` 连接上即可把低开销的话题流与普通控制面 RPC 多路复用。继续维护第二套资源 `BuddyWait`、服务端阻塞循环、等待准入计数器和 `HMB_WAIT_CAPACITY` 配置已无必要，反而带来额外的连接数、服务端线程占用与 48 路等待硬上限。

## 决定

1. **在 `BuddyControl` 上新增 `@cc.topic` 话题 `board_changed` 与单次观测操作 `task_observe`，彻底退役 `BuddyWait`。**
   - `BuddyControl` 成为黑板服务的唯一对外控制与观测资源，删除 `BuddyWait`、`WAIT_NAME`、`WaitService`、`WaitAdmission`、`WAIT_OVERLOAD`、`MAX_WAIT_CAPACITY`、`HMB_WAIT_CAPACITY`、健康接口中的 `waitCapacity`/`waitAdmission` 字段及能力声明中的 `wait` 操作组。
   - `BoardStore._notify` 在既有的条件变量锁内单调递增变更序号（含宏任务删除这类事件日志 `MAX(seq)` 未增长但任务可见性变化的情形），并在锁内调用 `board_changed` 的 `Publisher.publish` 以保持发布严格有序；提交后发布异常仅记录日志，绝不导致已提交的 SQLite 写入失败。
   - `health` 改为报告 C-Two 注册对象上的实时订阅数 `activeSubscribers`；守护进程停机清理时清空发布回调并校验 `cc.shutdown()` 完成回执。
   - 契约签名校验（`test_contract_version.py`）纳入方法装饰器（含 `@cc.topic`），并将 `CONTRACT_VERSION` 升至 `0.77.0`；数据库表结构与 `SCHEMA_VERSION`（`43`）不变。
2. **用单次非阻塞 `task_observe` 承载消费型观测语义，保持 `task_get` 纯只读且严格收紧权限边界。**
   - `task_observe(runId, reminderWaitId?)` 返回单条微任务的当前权威视图；提供 `reminderWaitId` 时按既有幂等语义认领并重放提醒、异常或暂停回执，不提供时不做任何消费写入。
   - `task_observe` 严格继承原 `task_wait` 的归属边界：宿主可调用，受托 Lead 仅可观测名下微任务，普通 Worker 一律以 `FORBIDDEN` 拒绝。
   - 纯观测与读操作跳过 `_guard` 中冗余的 harness 健康表全量查询，避免每次话题唤醒后重查时产生无谓的 SQLite 读取。
3. **客户端以单一只读多路复用会话 `BoardWaitSession` 驱动阻塞等待与本地可中断控制。**
   - `await`、`conclude` 停机等待以及 `BoardClient.wait_events` / `wait_task` 均在等待窗口内复用同一条只读附着的 `BuddyControl` 连接（绝不冷启动服务、绝不在空状态目录写入文件）：先执行权威读，未达边界再订阅 `board_changed(start="next", on_lag="skip")` 并强制执行一次订阅就绪后的重查以消除竞态窗口；之后每次权威读前先清空本地唤醒标记，再执行读与等待。
   - 为避免跨平台在无流量守护进程上卡住 `Ctrl+C` 或超时边界，客户端以轻量后台接收线程消费 Rust 侧释放 GIL 的订阅迭代器并触发本地 `threading.Event`，主线程以短步长（0.25s）可中断等待并在 `finally` 中显式执行 `sub.cancel()` 与有界 `sub.close()`。
   - 将 C-Two 的 `CallCapacityExceeded (717)` 归类为预分发可重试瞬时 IPC 拒绝（纳入 `is_transient_ipc_error`），保障执行收据投递（`deliver_receipt`）与阻塞等待在逼近 `max_outstanding_calls = 256` 上限时安全退避重试，同时禁止只读附着把 `717` 误判为“服务未运行”。
4. **保持运行期启动配置严格，仅在升级与回退边界平滑消化旧 `HMB_WAIT_CAPACITY`。**
   - 运行期 `LAUNCH_KEYS` 与服务环境变量白名单移除 `HMB_WAIT_CAPACITY`，`launch_defaults` 继续严格拒绝未知持久化键。
   - 仅 `install/upgrade.py` 在跨版本升级读取旧 `launch-settings.json` 与写入升级日志时接受并剥离 `HMB_WAIT_CAPACITY`：目标 `0.77.0+` 运行时不再写入该键，同时把旧配置保留在日志的 `previousEnvironment` 中，确保升级失败回退到 `0.77.0` 之前版本时仍可原样恢复。

## 考虑过的方案

- **保留 `BuddyWait`，仅把 `max_execution_workers` 与 `HMB_WAIT_CAPACITY` 调大。**
  - 最有力的理由：改动极小，无需变动 C-Two 契约、客户端会话模型与测试套件。
  - 不取：本质仍是每个等待客户端独占一条服务端 Python 线程，既保留整套冗余的第二资源与过载准入机制，也无法解除线程数与并发等待数的耦合。
- **去掉 `task_observe`，把提醒/异常消费直接塞进 `task_get`。**
  - 最有力的理由：减少一个 C-Two 操作名，表面上接口更少。
  - 不取：会破坏 `task_get` 纯只读与普通 Worker 可读自身视图的既有语义，混淆“读取视图”与“消费等待回执”的权限与副作用边界。
- **直接同步调高 `max_outstanding_calls`（超过 256）以支持更多订阅者。**
  - 最有力的理由：进一步抬高单守护进程允许的同时订阅上限。
  - 不取：`max_outstanding_calls = 256` 已提供 255 路并发订阅（超过旧 48 路硬上限 5 倍），远大于单机本机协作规模；保持 `rpc_config` 既有预算不变可避免未经实测的内存与通道上限漂移，后续确有更大规模需求时再单独评估。
- **在 `launcher.launch_defaults` 中长期静默忽略已退役的 `HMB_WAIT_CAPACITY`。**
  - 最有力的理由：不用在升级协调器里区分目标与回退两份启动配置。
  - 不取：会把兼容逻辑永久留在运行期配置边界上，违背 0.x 不留废弃兼容的原则；把旧键转换收敛在一次性升级/回退路径中，运行期代码才能保持干净严格。

## 影响

- **得：**
  - 移除整套 `BuddyWait` 资源、`WaitAdmission`、`WAIT_OVERLOAD` 与 `HMB_WAIT_CAPACITY`，消除服务端长阻塞等待线程占用，单守护进程并发等待上限从 48 提升至受 `max_outstanding_calls` 约束的 255 路且不饿死控制面与命令完成；
  - 每次 `await` 窗口内复用单条只读 `BuddyControl` 连接，减少建连与空轮询开销；`Ctrl+C` 与服务停机均可在亚秒级可靠唤醒并回收订阅计数；
  - `717` 预分发可重试分类防止高订阅负载下丢失执行收据。
- **代价：**
  - C-Two 契约版本升至 `0.77.0`，安装升级需按跨契约规则要求黑板处于空闲状态；
  - 每次全局变更会向活跃订阅者广播一次极轻量的序号帧，由客户端发起单次权威重查过滤非目标微任务变更；在极高频全局写入叠加大量订阅者时可能产生温和的重查扇出；
  - 单个 C-Two 远程订阅仍计入 `max_outstanding_calls = 256` 额度，超过 255 路同时等待时需依赖客户端退避或后续按需调大该预算。
