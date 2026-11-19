# ADR-034：退役协助任务

## 状态

已接受。

- 开发者 2026-11-18 评估后决定退役协助任务（helper），并在尚未安装的 `0.71.0` / Schema 42 批次内一次清理干净，不再另起一次升级。

## 背景

早期的 [ADR-002](002-host-directed-assistance-and-workspaces.md) 在只有单一微任务、还没有 Lead buddy 时引入了协助任务（helper）：Worker 回合可以以 `assistance` 停在边界上请求协助，Host 通过 `continue` 的 `decision: approve | decline` 批准最多 8 个协助任务、选择是否移交检出与自动续接父任务，并经 `targetRunId`、`helperPolicy`、多层代理请求（`helper-attention` / `helper-report`）与 `workflow_children` 表维持父子生命周期。

长期真实使用表明这套机制已经失去存在必要，反而成为最高维护成本的来源：

- 日常黑板累计已有一千多个微任务，协助任务的使用次数为零，检出移交为零，而 Lead 与名下的微任务（[ADR-026](026-delegation-authority.md)）已稳定承担多子任务协作；需要另一份独立工作时，Host 直接在同一宏任务下并行提交普通微任务，或由用户指定 Lead 管理名下的微任务，两者都不需要代理边界链与检出移交。
- 协助任务的多层代理请求、影子子任务状态与保留策略（`helperPolicy: keep`）在边界同步、取消和恢复路径上反复引发状态漂移。
- 随着协助任务退出，只为它服务的两处区分也失去意义：`ContinueRequest` 的 `decision: approve | decline` 在真实使用中从未带过协助任务，只被借作普通 `input` 续接；Worker 回合结果中的 `assistance` 与 `attention` 在黑板与控制台没有任何行为区别，`suggestedBuddyId` 也仅用于向 Host 建议协助任务 buddy。

## 决定

1. **彻底退役协助任务机制。**
   - 黑板不再支持创建协助任务、移交检出、代理边界请求、自动收集协助结果或按协助任务保留策略续接。
   - Host 面向边界的十个动词保持不变（[ADR-032](032-host-interface-delegation-loop.md)），但在 `continue`、`accept`、`conclude`、`cancel`、`cleanup` 上移除 `targetRunId`，在 `continue` 上移除 `decision`、`helpers`、`autoContinue`、`helperPolicy` 与 `HelperRequest`。
   - 需要额外工作时，Host 在同一宏任务下直接提交独立的微任务；需要受托统筹一组微任务时，按 [ADR-026](026-delegation-authority.md) 由用户明确授予 Lead 委派权。Lead 与名下的微任务不受本决定影响。
2. **统一 Host 边界回答路径。**
   - 回答打开的边界请求（`requestId`）只保留两条路径：给出 `input` 续接微任务（可同时指定换手、换档或新写入范围），或针对工作区越界冲突给出 `resolution` 与 `observedFingerprint`（也可同时附带 `input` 在解决后直接续接）；不再需要继续时直接 `conclude` 或 `cancel`。
   - 本条取代 [ADR-002](002-host-directed-assistance-and-workspaces.md) 的协助审批路径与 [ADR-032](032-host-interface-delegation-loop.md) 中 `continue` 的决策字段约定；ADR-002 的工作区封存、越界冲突解决、Host 验收与真实停止证据继续有效。
3. **Worker 回合边界收敛为单一 `attention`。**
   - 新回合的 Worker 结果 `disposition` 只有 `completed` 与 `attention`（Lead 另有 `waiting`），不再输出 `assistance` 或 `suggestedBuddyId`。
   - Worker 需要帮助、缺权限、遇阻或达到约定审阅条件时，一律以 `attention` 结束回合，由 Host 判断自行处理、另派独立微任务或续接原微任务。
4. **在 Schema 41 → 42 中清理对应存储并拒绝未了结的旧协助任务。**
   - 删去 `workflow_children` 表与局部索引 `workflow_continuations_auto_unique`。
   - `workflow_runs.state` 去掉 `waiting-helpers`。
   - `workflow_requests` 删去 `child_task_id` 与常量列 `kind`；`decision_json` 收口并更名为 `resolution_json`，只保存工作区冲突解决结果；`state` 收口为 `open`、`resolved`、`superseded`、`cancelled`；`response_kind` 收口为 `continue`、`resolution`。请求来源由既有的三选一约束区分（`summary` 为 Worker 提出、`decision_id` 为路由边界、`reason_code` 为系统边界）。
   - `workflow_continuations` 删去 `controlling_run_id`、`helper_policy`、`helper_outcomes_json`，`kind` 收口为 `manual` 与 `lead-wakeup`。
   - `workspace_reservations` 删去 `holder_kind`、`parent_run_id`，`state` 收口为 `held` 与 `released`。
   - 升级时若发现待升级黑板仍有未结束的协助任务状态（`waiting-helpers`、未结束的 `workflow_children`、`transferred` 占锁，或仍打开的 `helper-attention` / `helper-report`），直接报错拒绝升级，要求先在旧版本结束后再升。
   - 对已结束的历史存量：历史上作为协助任务跑过的微任务留在 `tasks` 与 `workflow_runs` 中作为普通历史微任务保留，所有封存快照、固定结果、回合输入与事件日志原样不动；历史 `helper-attention` / `helper-report` 请求行删去；历史工作区解决行转为 `state='resolved'` 并迁入 `resolution_json`，其余历史 `approve` / `decline` 请求转为 `state='superseded'` 并清除旧决策字段与 `response_kind='decision'`；历史 `kind='helper'` 续接转为 `kind='manual'`。读侧回看历史封存结果中的 `disposition: "assistance"` 时继续按边界展示。
5. **控制台同步清理。**
   - 控制台移除协助任务子行、代理边界展示、协助结果列表与批/拒决策交互，只保留微任务自身的边界回复、工作区冲突解决以及 Lead 与名下的微任务展示。

## 考虑过的方案

- **只把协助任务从提示词与文档里隐藏，代码与表先留着。**
  - 最有力的理由：改动面小，不需要动存储表、测试和控制台前端，以后想用还能立刻打开。
  - 不取：只要代码路径和表约束还在，每次改动状态机、边界同步、清理或恢复都必须继续为它兜底和修回归，维护成本一分不减，且违背 0.x 不留废弃机制的原则。
- **保留 `decision: approve | decline` 仅用于文字批复，只删 `helpers` 与 `workflow_children`。**
  - 最有力的理由：减少对既有 Host 习惯和测试的改动，保留“批准/拒绝”的语义区分。
  - 不取：不创建协助任务时，“批复一段文字让 Worker 继续”与 `continue` 的 `input` 完全重复；保留两套回答边界的路径只会让接口更复杂、让模型更容易选错字段。
- **先按原样安装 `0.71.0`，再另起 `0.72.0` / Schema 43 做退役。**
  - 最有力的理由：单次提交更小，把尝试等待列与协助任务退役分在两次安装里。
  - 不取：`0.71.0` 尚未安装，也尚未进入 `main`；连续跳两次契约版本与升级步骤，要白白多跑一轮全量验收、安装演练与跨平台检查，且在 `0.71.0` 里还得先给即将删除的协助任务代理路径继续打补丁。

## 影响

- **得：**
  - 移除整个协助任务子系统，显著简化 `workflow.py`、Host 请求契约、存储表结构与控制台前端，消除多层代理与影子状态这一类顽固缺陷；
  - Host 回答边界收口为 `input` 与 `resolution` 两条清晰路径，Worker 边界收口为单一 `attention`，接口更易于人与模型正确使用；
  - 与 `0.71.0` 合并在同一次 Schema 41 → 42 升级中完成，少一次契约跳跃与重复验证。
- **代价：**
  - 若将来确实出现“单个 Worker 需要临时借用独立子任务并把检出移交过去再交还”的场景，无法直接复用旧机制，需要借助普通并行微任务或 Lead 名下的微任务处理；
  - 若有旧版黑板在升级时仍有未结束的协助任务，必须先在旧版结束后再升级；
  - 历史 `workflow_requests` 中旧的 `approve` / `decline` 决策记录不再单独保留在 `workflow_requests` 表中（完整记录仍保留在未改动的事件日志里）；
  - 本次 `0.71.0` 批次改动面扩大至后端、控制台前端、33 组 Python 测试与文档，需要完整重跑全量验收与实机升级演练。
