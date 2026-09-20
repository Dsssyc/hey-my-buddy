# Python 黑板 0.4.0 验收

日期：2026-09-19。设计依据：[ADR-001](../decisions/001-python-transactional-blackboard.md)。

实现先由现有 0.3.0 Buddy skill 委派给 dsh，Codex 负责设计、独立审查和验收。 随后通过新 Python 黑板派发真实 dsh 修复任务，并用新实现完成自身验收。

## 验证结果

| 范围 | 实际证据 |
| --- | --- |
| 完整检查 | 干净的 uv 环境导入当前源码；Python 138 项、Node 116 项通过。 |
| 事务与恢复 | 独立 store 探针验证产物提交、跨 attempt 回执冲突、重启后的未启动执行、终态不可变、数据库版本拒绝及活动 attempt 唯一约束。 |
| 真实 dsh | 派发、运行中问询、重启 daemon、重新等待同一任务、核对产物、acknowledge、停止私有服务的完整流程通过。 |
| 执行身份 | 重启前后 task、attempt、generation、worker instance、Worker 与子进程保持一致；重复 start 返回原任务。 |
| 产物 | 对 519 个整数独立验证输入文件 SHA-256、数量、求和、平方和及排序；执行日志只有一次开始与结束，前台运行至少 90 秒。 |
| 问询 | 任务终态后通过新 inquire 读取答案，与 message-get 及真实 bridge journal 的文本、toolCallId、reply-tool 来源一致。 |
| 事件 | 提交与完成事件各出现一次；重启前的事件保留，按原游标补读一致。 |
| 缓存独立性 | 真实任务运行时移走可丢弃的 staged 插件副本，任务继续完成；解释器环境、Python 模块及 dsh 适配资源来自固定 runtime。 |
| 命令适配器 | 真实进程计算 `sum(range(100))`，结果为 4950，取得结果并验收。 |
| 外部 Worker | 独立 Python 进程仅使用公共客户端，领取无 argv 的 external 任务，计算并提交实际文件；调用方独立核对 SHA-256、数量和平方和后验收。 |
| C-Two 等待隔离 | 实际 RPC 占满两个等待名额，第三次等待返回 WAIT_OVERLOAD；两个等待仍挂起时，续租、取消、结果提交合计约 21 毫秒。 |
| 历史迁移 | 真实 28 条旧记录先在私有目标预演，再导入默认服务；逐条比较 ID、请求 ID、结果、状态及验收记录，重复导入不新增，原文件字节不变。 |
| 安装后检查 | 从实际安装的 launcher 派发命令任务，检查精确 stdout、完成状态和退出确认后验收；28 条历史任务仍可读取。 |

## 验收发现并修复的问题

首次真实验收在较长的状态目录下暴露了旧兼容 socket 的路径长度限制。 修复保留两个服务所有权锁，只在真实 bind 错误证明地址不可表示、且路径上没有旧条目时省略旧监听器。 真实短路径监听器、陈旧 socket、通过短别名访问的长路径监听器均继续阻止冲突启动。 新增 10 项针对性测试，长路径真实任务随后通过验收。

真实历史记录还覆盖了早于问询功能的格式：这些记录没有 inquiries 字段。 导入器补充兼容缺省值并拒绝错误类型；迁移回归测试和真实 28 条记录比较均通过。

## 提交前复查（2026-09-20）

修复后的完整检查通过：Python 138 项、Node 117 项。测试使用私有状态及运行时目录；本次未重复执行前述真实模型与默认安装迁移实验。

提交前复查修复了等待隔离测试的时序依赖：后台 worker 注册可能发生在测试读取事件游标之后，正常唤醒等待者，使固定延迟后的过载断言失效。测试现改为等待自己创建的 external 任务，并观察实际占用的等待名额；显式插入无关 worker 注册事件后仍须返回 WAIT_OVERLOAD，随后通过控制通道取消目标任务，验证控制请求可完成且等待者收到对应取消事件。

Node 检查同时暴露了系统时钟回拨对耗时统计的影响：执行超过 10 秒的超时任务可能报告 9.9 秒。新增回归测试主动回拨 Date.now，旧实现可产生负耗时；runner 的耗时统计和退出等待上限现使用单调时钟，该回归测试已通过。

## 复现入口

```sh
uv sync --project deepseek-delegate --frozen
uv run --project deepseek-delegate --frozen python -m buddy.checks
```

公共 CLI 与外部 Worker 的调用方式见 [服务参考](../../deepseek-delegate/references/plugin-service.md)。 脚本 `deepseek-delegate/scripts/acceptance-probe.sh` 分发本次实际通过的隔离验收驱动。 它要求显式提供四个互不包含的私有目录，默认服务目录会被拒绝：

```sh
sh deepseek-delegate/scripts/acceptance-probe.sh \
  --state-dir /tmp/buddy-acceptance-case/state \
  --runtime-root /tmp/buddy-acceptance-case/runtime \
  --work-dir /tmp/buddy-acceptance-case/work \
  --evidence-dir /tmp/buddy-acceptance-case/evidence
```

前提是本机 dsh 已安装并配置模型服务。该探针会执行一次真实模型任务，并保留唯一请求与任务 ID； 已通过或失败的 case 不会自动重跑。运行中的中断可按已保存的身份接回，新的实验使用新的私有目录。 命令及外部 Worker 接入由另外的公共 API 探针验证，上述脚本不把这些检查冒充为自身覆盖范围。

原始日志、输入、哈希、进程身份及迁移快照保存在本地忽略目录 `.dsh-skill-build/python-blackboard-20260919/`，不随仓库分发。

## 保证范围

ACID 覆盖黑板内的任务状态、执行记录、结果引用和事件提交。外部文件修改、模型调用及操作系统 进程启动不属于数据库事务，因此不承诺任意外部副作用恰好发生一次。

daemon 重启时，原 Worker 可以继续执行并补交回执。Worker 本身死亡后，系统保留不确定状态 与资源占用，不根据 PID 消失或租约到期伪造退出确认，也不会自动接管遗留进程。

此版本面向本机同一用户的可信客户端，采用 SQLite。跨用户权限隔离、PostgreSQL/高可用、 模型内部状态迁移，以及已经结束回合的 Codex App 任务即时唤醒，均不属于本次通过的验收范围。
