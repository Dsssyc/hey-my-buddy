# ADR-034：退役协助任务

## 状态

已接受。

- 开发者 2026-11-18 评估后决定退役协助任务（helper），并在尚未安装的 `0.71.0` / Schema 42 批次内一次清理干净，不再另起一次升级。
- 同日经两轮独立只读设计审核补齐了历史会话归属、工作区冲突解决三类存量与共享续接唯一归属、Lead 唤醒失败请求保留、拆树与未消费授权拒升边界，以及 `attempts.waiting_hint_seq` 单调去重约定。

## 背景

早期的 [ADR-002](002-host-directed-assistance-and-workspaces.md) 在只有单一微任务、还没有 Lead buddy 时引入了协助任务（helper）：Worker 回合可以以 `assistance` 停在边界上请求协助，Host 通过 `continue` 的 `decision: approve | decline` 批准最多 8 个协助任务、选择是否移交检出与自动续接父任务，并经 `targetRunId`、`helperPolicy`、多层代理请求（`helper-attention` / `helper-report`）与 `workflow_children` 表维持父子生命周期。

长期真实使用表明这套机制已经失去存在必要，反而成为最高维护成本的来源：

- 日常黑板累计已有一千多个微任务，协助任务的使用次数为零，检出移交为零，而 Lead 与名下的微任务（[ADR-026](026-delegation-authority.md)）已稳定承担多子任务协作；需要另一份独立工作时，Host 直接在同一宏任务下并行提交普通微任务，或由用户指定 Lead 管理名下的微任务，两者都不需要代理边界链与检出移交。
- 协助任务的多层代理请求、影子子任务状态与保留策略（`helperPolicy: keep`）在边界同步、取消和恢复路径上反复引发状态漂移。
- 随着协助任务退出，只为它服务的两处区分也失去意义：`ContinueRequest` 的 `decision: approve | decline` 在真实使用中从未带过协助任务，只被借作普通 `input` 续接；Worker 回合结果中的 `assistance` 与 `attention` 在黑板与控制台没有任何行为区别，`suggestedBuddyId` 也仅用于向 Host 建议协助任务 buddy。

## 决定

1. **彻底退役协助任务机制。**
   - 黑板不再支持创建协助任务、移交检出、代理边界请求、自动收集协助结果或按协助任务保留策略续接。
   - Host 面向边界的十个动词保持不变（[ADR-032](032-host-interface-delegation-loop.md)），但在 `continue`、`accept`、`conclude`、`cancel` 上移除 `targetRunId`（并清理内部回收与回包中的对应字段），在 `continue` 上移除 `decision`、`helpers`、`autoContinue`、`helperPolicy` 与 `HelperRequest`。
   - 需要额外工作时，Host 在同一宏任务下直接提交独立的微任务；需要受托统筹一组微任务时，按 [ADR-026](026-delegation-authority.md) 由用户明确授予 Lead 委派权。Lead 与名下的微任务不受本决定影响；Lead 唤醒重排失败改记为 Lead 自身的系统边界请求，不再借用 `helper-report`，同时保留原触发事件、失败原因与一次有效待唤醒约束。
2. **统一 Host 边界回答路径。**
   - 回答打开的边界请求（`requestId`）只保留两条路径：给出 `input` 续接微任务（可同时指定换手、换档或新写入范围），或针对工作区越界冲突给出 `resolution` 与 `observedFingerprint`（也可同时附带 `input` 在解决后直接续接）；不再需要继续时直接 `conclude`，需要停下以备换手时用 `cancel`。
   - 本条取代 [ADR-002](002-host-directed-assistance-and-workspaces.md) 的协助审批路径与 [ADR-032](032-host-interface-delegation-loop.md) 中 `continue` 的决策字段约定；ADR-002 的工作区封存、越界冲突解决、Host 验收与真实停止证据继续有效。
3. **Worker 回合边界收敛为单一 `attention`。**
   - 新回合的 Worker 结构化输出升级为 `version: 2`，`disposition` 只有 `completed` 与 `attention`（Lead 另有 `waiting`），在 harness 侧、新收据接收入口与恢复入口（含封存失败但收据仍带 `turn` 的路径）对每个新 Worker `turn` 均严格拒绝 `assistance` 与 `suggestedBuddyId`。
   - Worker 需要帮助、缺权限、遇阻或达到约定审阅条件时，一律以 `attention` 结束回合，由 Host 判断自行处理、另派独立微任务或续接原微任务。
   - 历史已封存的 `version: 1` 回合输出、回合输入、固定结果、收据、检查点与事件日志原字节不动；历史读侧（验收、工作区冲突解决后的边界重建以及控制台）才按原版本校验身份与证明并继续识别历史 `assistance`，重建当前边界时仅投影现行字段。所有保留的 `workflow_requests.payload_json` 均清理已退役的 `suggestedBuddyId`，同时保留 `source='routing'`、`inputFailure`、`preparationError` 等现行读者字段。
4. **在 Schema 41 → 42 中清理对应存储、固定历史会话归属、持久化等待提示序号并拒绝未了结的旧协助授权。**
   - 删去 `workflow_children` 表与局部索引 `workflow_continuations_auto_unique`。
   - `workflow_runs.state` 去掉 `waiting-helpers`。
   - `attempts` 增加可空冻结列 `session_group_id` 与单调列 `waiting_hint_seq INTEGER NOT NULL DEFAULT 0 CHECK (waiting_hint_seq >= 0)`：`session_group_id` 在认领事务（`admit_execution`）内为有分组的非 `command` 尝试一次性冻结目录归属（无分组尝试为 `NULL`，有分组但尚未落盘文件者仍冻结归属），41 → 42 删边前按 Schema 41 归属一次性回填；控制台读取、样本导出（Schema 30–41 按冻结旧分组查询、Schema 42 按 `session_group_id` 读写）、存储扫描与删除任务均按尝试自身 `session_group_id` 定位和逐个清理 `<attemptId>.jsonl`（沿用目录锁与防符号链接检查，允许文件已缺失或目录仍非空，目录空时移除空目录），判断 `SESSION_DELETED` 仍依据该尝试当前所属分组的删除标记；`waiting_hint_seq` 用于服务端 `execution_progress` 校验等待提示与尝试内序号成对并单调写入（`None → None` 同样推进，旧序号不回写，重复确认仍唤醒预算观察）。
   - `workflow_requests` 删去 `child_task_id` 与常量列 `kind`；保留既有的请求来源约束（Worker 提出 `summary`、路由 `decision_id`、以及允许附带 `decision_id` 的本地系统 `reason_code`）；`decision_json` 收口并更名为 `resolution_json`，只保存工作区冲突解决记录；`state` 收口为 `open`、`resolved`、`superseded`、`cancelled`；`response_kind` 收口为 `continue`、`resolution`。保留 `(response_kind IS NULL) = (response_id IS NULL)`，并以显式 `CASE ... ELSE 0 END` 约束校验合法组合（使用 `IS` 校验必需 JSON 字段类型，避免 `NULL` 绕过约束）：`resolved` 表示该请求记录了一次工作区处置，允许部分 `restore` 或 `abandon` 后工作区现场仍为 `open`（`artifactId` 可为 `null` 或非空字符串），附带 `input` 而现场未清完时落入 `resolution` 槽并在重放中保持 `continuationBlocked`；`resolution` 槽要求完整 `{parameters, result}`，`continue` 槽允许普通空值或 `{result}`，空槽只对显式标注的早期旧解决形式允许非空 `resolution_json`。新 writer 一次写齐直接请求的状态、槽与 `resolution_json`，不再按共享 `response_id` 宽泛回填其他被取代请求。
   - `workflow_continuations` 删去 `controlling_run_id`、`helper_policy`、`helper_outcomes_json`，`kind` 收口为 `manual` 与 `lead-wakeup`。
   - `workspace_reservations` 删去 `holder_kind`、`parent_run_id`，`state` 收口为 `held` 与 `released`。
   - 区分单步离线迁移能力与跨契约安装前置条件（安装器仍要求日常黑板在跨契约升级时无任何进行中或排队续接）；单步迁移 41 → 42 在删改前若发现以下任一未了结或改权状态，直接报错拒绝并列出具体标识，要求先在旧版本处理完毕后再升：
     - 存在 `workflow_runs.state = 'waiting-helpers'`；
     - 存在未结束的 `workflow_children`（`state IN ('active','attention')`，或其对应子 run 处于活动、待宿主处理或待验收状态）；
     - 存在 `workspace_reservations.state = 'transferred'`，或任意 `workflow_children` 的父或子仍有未释放的预约（`state='held'`）或未完成回收的托管检出；
     - 存在挂在 `workflow_lead_tasks` 树下且未终结（`state NOT IN ('accepted','concluded')`）的 `workflow_children`，或挂在终态祖先（`accepted`/`concluded`）下且处于 `failed`/`cancelled` 的 `workflow_children`，或涉及旧协助树的 `work_pauses` 暂停记录；
     - 存在仍打开的纯协助任务 `helper-attention` / `helper-report`（属于 Lead 唤醒失败的 `helper-report` 除外，见下条）；
     - 存在仍可能被调度或重放入队的续接（`state IN ('recorded','queued')`，或 `state='cancelled' AND attempt_id IS NULL`）且带有任意协助依赖：`helper_policy IS NOT NULL`、跨 run `controlling_run_id != run_id`、非空 `helper_outcomes_json`、指向 `helper-attention` / `helper-report` 代理来源，或所属 `run_id` 在 `workflow_children` 中存在任意历史子行；
     - 存在涉及旧协助关系的未完成 `micro-task-tree` 删除标记（`macro_task_deletions`）。
   - 对已结束及允许迁移的历史存量按以下规则转换（并在删代理前完成引用归位与唯一归属核对）：
     - 历史上经预检放行拆出的原协助任务作为普通历史微任务留在 `tasks` 与 `workflow_runs` 中，不替 Host 补写验收或结论；对于无 Lead、非终态父任务下已取消且残留旧 `open` 冲突与零打开请求的存量，不新开历史专用处置入口，允许的操作限定为只读回看与符合条件的整组删除；
     - 删去已关闭的纯协助 `helper-attention` / `helper-report` 前，先识别所有打开及已回答的 Lead 唤醒失败 `helper-report`（核对 `delegation_authority=1`、空 `helperOutcomes`、自动续接失败 `reason_code` 与对应 `lead-wakeup` 触发记录），保留其 `request_id`、回答槽、`reason_code` 与对象型 `reason_params_json` 并清除退役载荷，保留待唤醒唯一索引；对其余代理请求，先沿 `payload.origin` 或逐级 `proxy.requestId` 把 `workflow_continuations.request_id` 归位至 `continuation.run_id` 上的唯一真实请求（无下层请求的已消费纯 `helper-report` 清空该退役来源），再校验或按既有顺序重选 `workflow_runs.active_request_id`；
     - 历史 `workflow_requests` 按实际写入形态转换（共享续接带 `{result}` 时，先从 `workflow_continuations.request_id` 沿已验证的 `origin` / `proxy` 定位 `continuation.run_id` 上唯一的真实请求并核对 `response_id` 与 `result.conflictId`，仅该行转 `resolved + continue + {result}`，其余同次被取代的真实请求写 `superseded + continue + NULL`，无唯一匹配或遇未知组合均携带标识报错并整事务回滚）：

       | Schema 41 存量形态 | Schema 42 存储表示 | 重放与保留边界 |
       | --- | --- | --- |
       | 早期 `approved`，响应槽均为 `NULL`，`{"resolution":{...}}` | `state='resolved'`，响应槽仍为 `NULL`，`resolution_json` 显式标记为早期解决形式 | 保留 `conflictId`、`action`、`state`、`actor`，不补造参数、产物或指纹；再次操作按已关闭请求拒绝 |
       | 单独解决：`approved` + `resolution` + `conflictId`，`{parameters,result}` | `state='resolved'` + `resolution` + 原 `conflictId`，保留 `{parameters,result}` | 同参重放返回原结果（含 `abandon` 后 `state='open'`、非空 `artifactId` 及 `continuationBlocked`），异参拒绝；原代理参数经校验归位真实 run 后折算 |
       | 解决并续接：`superseded` + `continue` + `continuationId`，`{result}` | 经唯一归属定位的真实处置请求转为 `state='resolved'` + `continue` + 原 `continuationId` 与 `{result}` | 重放结合续接行与 `{result}`；同次被取代的其他真实请求维持 `superseded` + `continue` 且 `resolution_json=NULL` |
       | 普通 `continue`，`decision_json` 为 `NULL` | `state='superseded'` + `continue` + 原 `continuationId`，`resolution_json=NULL` | 保留同一续接；由固定产物或失败回合发起时保留原来源槽 |
       | 旧批复：顶层 `decision` 为 `approve` 或 `decline`（`response_kind` 为 `decision` 或 `NULL`） | `state='superseded'`，成对清空响应槽及 `resolution_json` | 按顶层 `decision` 识别，不按旧 `state` 误判；绝不重新开放已关闭请求 |
       | `open` / `cancelled` / `superseded`，响应槽与决策均为空 | 保留原状态与空槽 | 保留时间戳与自身有效请求；所有保留行清理 `payload_json.suggestedBuddyId` |

     - 历史 `workflow_continuations` 中已 `consumed` / `invalidated` 的 `kind='helper'` 行（含非空旧协助成果）保留原 `continuation_id` 与工作区绑定转为 `kind='manual'`；无任何协助依赖的未消费无子 `kind='helper'` 行（含旧版无子任务的 `decline + autoContinue`）保留原 `continuation_id` 转为 `kind='manual'`，若原无自定义 `note` 且原请求含旧 `decision_json`，先将原 `approve`/`decline` 结论写入 `input_text` 以保留等价指令；
     - 清理残留于 `tasks.queue_reason` 中的旧协助排队原因（`helper-attention` / `awaiting-helpers`），按当前真实边界核实或置空。
5. **控制台同步清理。**
   - 控制台移除协助任务子行、代理边界展示、协助结果列表与旧批复展示，保留微任务自身的边界回复展示、工作区冲突解决记录展示以及 Lead 与名下的微任务视图，并继续正确展示历史封存回合中的 `assistance` 边界。

## 考虑过的方案

- **只把协助任务从提示词与文档里隐藏，代码与表先留着。**
  - 最有力的理由：改动面小，不需要动存储表、测试和控制台前端，以后想用还能立刻打开。
  - 不取：只要代码路径和表约束还在，每次改动状态机、边界同步、清理或恢复都必须继续为它兜底和修回归，维护成本一分不减，且违背 0.x 不留废弃机制的原则。
- **保留 `decision: approve | decline` 仅用于文字批复，只删 `helpers` 与 `workflow_children`。**
  - 最有力的理由：减少对既有 Host 习惯和测试的改动，保留“批准/拒绝”的语义区分。
  - 不取：不创建协助任务时，“批复一段文字让 Worker 继续”与 `continue` 的 `input` 完全重复；保留两套回答边界的路径只会让接口更复杂、让模型更容易选错字段。
- **升级时在磁盘上搬迁独立旧协助任务的会话文件，而不是在 `attempts` 上固定归属。**
  - 最有力的理由：不必在 `attempts` 上新增 `session_group_id` 列。
  - 不取：磁盘文件搬迁难以与 SQLite 升级事务原子回滚，一旦安装回退，旧版本会找不到已搬走的会话文件；按尝试冻结写入时的目录归属零搬移、可原子回滚，也能从根本上消除运行时重算目录的风险。
- **放宽拆树后的历史权限校验或在服务端保留历史专用处置入口。**
  - 最有力的理由：即使旧黑板存在未终结的旧协助树、未回收借用检出或 Lead 树下的旧子任务，也能强行升到 Schema 42。
  - 不取：会把已退役的协助任务权限规则永久拖进新版本服务端；把所有可能改变权限或借用语义的未了结存量挡在 41 → 42 预检门外，新代码才能保持彻底干净。
- **先按原样安装 `0.71.0`，再另起 `0.72.0` / Schema 43 做退役。**
  - 最有力的理由：单次提交更小，把尝试等待列与协助任务退役分在两次安装里。
  - 不取：`0.71.0` 尚未安装，也尚未进入 `main`；连续跳两次契约版本与升级步骤，要白白多跑一轮全量验收、安装演练与跨平台检查，且在 `0.71.0` 里还得先给即将删除的协助任务代理路径继续打补丁。

## 影响

- **得：**
  - 移除整个协助任务子系统，显著简化 `workflow.py`、Host 请求契约、存储表结构与控制台前端，消除多层代理与影子状态这一类顽固缺陷；
  - Host 回答边界收口为 `input` 与 `resolution` 两条清晰路径，Worker 边界收口为单一 `attention`，减少 skill 上下文与分支推导负担；
  - `attempts.session_group_id` 固定了会话文件的真实目录归属并改为按尝试精确清理，消除删父误删子会话的风险；`attempts.waiting_hint_seq` 为等待提示提供持久化单调屏障；
  - 与 `0.71.0` 合并在同一次 Schema 41 → 42 升级中完成，少一次契约跳跃与重复验证。
- **代价：**
  - 若将来确实出现“单个 Worker 需要临时借用独立子任务并把检出移交过去再交还”的场景，无法直接复用旧机制，需要借助普通并行微任务或 Lead 名下的微任务处理；
  - 若有旧版黑板在升级时仍有未结束的协助任务、未消费的协助授权、未释放/未回收的协助检出、Lead 树或终态父任务下的未终结旧子任务、或涉及旧协助树的暂停/删除标记，必须先在旧版处理完毕后再升级；
  - 历史 `workflow_requests` 中旧的 `approve` / `decline` 决策清除后，未写入续接行的旧批复备注与协助任务授权包字段不再保留（事件日志仍保留请求 ID、决定、actor、协助任务 ID 与 `autoContinue`），已删代理请求对应的旧等待区间亦随之一并移除；
  - 本次 `0.71.0` 批次改动面扩大至后端、控制台前端、升级底座 Schema 41 补包、测试与参考文档，需要完整重跑全量验收、实机升级演练与无头浏览器视觉核验。
