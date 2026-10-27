# Router 第一步：路由面看到负载与结果统计（Host 验收）

2026-10-27。微任务 R1“路由面看到负载与结果统计”（run `940d9fe7`，Host 指定 Codex CLI 的 `gpt-6.1-sol / high`），依据[执行计划](../../design/adr021/router-step1-plan.md)与 [ADR-021](../../decisions/021-router-buddy-planes-and-routing-evidence.md) 第 1、6、9 条。基点 `31c3a4b5`，封存提交 `7ce76c4c`，合入整合分支 `fb1db282`。契约 0.55.0，没有改存储形式。

## 经过

R1 先读计划与源码，开工前停下来指出一处口径问题：计划写的“回合到达过模型”没有可靠的判据，`modelStarted` 在 Codex 与 ZCode 里发送之前就置上了。Host 把口径改为按回合结束的原因分三类（[计划](../../design/adr021/router-step1-plan.md)“口径”一节，提交 `696fcff2`），R1 照此实现。

## 改了什么

- `blackboard/routing/evidence.py`（新）：在读的时候从现有记录推出两样东西，不存储：
  - 按同型 buddy 的结果统计与按 buddy 的基础设施失败；
  - 按模型服务的负载，等多久按名额依次空出来模拟。
  回合结束的分类只在 `round_classification` 一处判定。
- Router 的输入：每个候选加上计费方式、负载、统计与基础设施失败；请求加上预期时长。
- Router 的说明：加入 ADR-021 第 6 条的原则。
- 答案：依据加上 `statistics` 一种，引用时写候选的 buddyId，执行端与发布端用同一个校验。
- 顺带：
  - 时间轴读回合自己的 buddy（`protocol/turn_input.py`，与统计共用），以前读错位置，看不到推理强度；
  - Router 任务的工作目录改为启动器创建、有人负责删除的 `state/attempts/<taskId>`，待办里那一条随之删去。

## Host 核对的

- 读完差异。统计、舍去、返工与负载的写法逐条对照计划的口径；分类表（12 个终止原因，3 种提供方拒绝，加上 `harness-unavailable`）与计划一致。
- **真实数据**：用候选代码经只读连接在日常看板上推导一次。14 个候选，241 毫秒，没有报错。
  - 抽查：Sol max 成功 250，与 Host 独立写的 SQL 一致。
  - 日常看板 908 个结束的回合里没有一次提供方拒绝或 harness 不可用，所以基础设施失败都是 0，与独立查询一致。
  - GLM-5.3 max 在两个提供方下显示同一份统计，符合“同型 buddy 共享评价”。
- **真实 Router 调用**：一次，经开发者批准。用 DeepSeek Flash 快速模式，输入是由日常看板只读组装的真实候选事实（8 个合法候选）。
  - 4.3 秒，零工具调用，正常关闭，答案通过新的校验。
  - 它选了 `dsh:deepseek-official:deepseek-flash:max`。理由引用了统计与样本量（13/13 成功、一次通过率 1.0、中位 51 秒）、负载（0/10），以及计费方式不知道时“不算负面证据”；比较备选时也写了样本量。
  - 输入包与报告留在 `tmp/storage/router-step1-r1/real-router-call/`。
  - 现成的探针 `tests/probes/router_fast.py` 少调一步适配器的 `prepare`，第一次在调用模型之前就失败了，没有花费；Host 在自己的变体里补上这一步后重跑。
- **测试**：合并后的树上跑了路由、时间轴、Router 角色与契约版本的 63 个测试文件，968 个测试全部通过。

## 记为下一批的顺带项

- 选择的原则在 Router 的角色文本（`router.txt`）与两段调用说明里重复，模型每次读两遍。
- `tests/probes/router_fast.py` 少调 `prepare`，探针本身已经跑不通。

## 没有核对的

- “选一个已满的模型服务要说明理由”还没有用真实模型核对过：私有看板上造不出满载的服务。装上 0.55.0 之后，看日常里 Sol 满着时的路由。
- 完整检查在安装之前与“CI 偶发失败与分片”一起跑。0.55.0 改了契约，安装要等看板空闲。
