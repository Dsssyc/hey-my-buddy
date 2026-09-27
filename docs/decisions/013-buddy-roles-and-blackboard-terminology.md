# ADR-013：buddy 角色与黑板术语

## 状态

已接受：用户于 2026-09-28 确认。本决定只统一领域语言和文档用词，不改变运行时行为、C-Two 契约、CLI、schema 或代码标识符。术语的定义以 [CONTEXT.md](../../CONTEXT.md) 为准。本决定延续 [ADR-002](002-host-directed-assistance-and-workspaces.md) 的原则：Host 与 Worker 的区分表达任务控制关系，不构成能力等级。

## 背景

"Buddy"在当前文档中同时指四种东西：整个产品（"Buddy lets a Host agent delegate…"）、黑板服务（ADR-002："Buddy 的 Python 黑板……Buddy 负责持久化决定、调度执行"）、被委派的执行者（skill："Use a Buddy when…"）和路由决策者（"decision Buddy"）。"Worker"也同时指执行任务的 agent 和承载它的运行时进程。读者因此难以判断一句话里行动的是哪个组件；讨论 Host 侧契约时，"黑板"和"Buddy"也被混为一谈。

## 决定

按经典黑板架构的分工命名。经典黑板系统（如 Hearsay-II）由黑板、知识源和控制组件组成：知识源之间不直接调用，也没有哪个知识源统管全局。

- **黑板**只是黑板：唯一的共享记录，加上准入、排队、容量、lease 和 fencing 等机械规则。它不对工作作判断，也不指挥任何参与者。经典架构中的控制组件对应这些机械规则，而不是某个参与者。
- **buddy** 是以 Host 或 Worker 角色读写黑板的参与者，对应经典架构中的知识源。buddy 之间地位对等，只通过黑板交互。
- **Host buddy**（简称 Host）以 Host 权限行事：拥有自己提交或接管的委派，定义授权、处理边界、验收结果，并可执行评价维护等 Host 专属操作。它也可以亲自完成部分工作。
- **Worker buddy**（简称 Worker）以 attempt 级权限执行回合：可以完成、请求协助或提出 attention，不能创建委派、批准 helper 或验收。路由决策同样由 Worker buddy 完成。
- **角色由凭据决定**，而不是由 harness 或模型决定。同一 harness（例如 Claude Code、Codex）可以运行任一角色的 buddy。
- **用户**是 buddy 为之工作的人：权限来源、共享设置的所有者和全局视图（控制台）的持有者。用户权限不是 buddy 角色，也没有任何 buddy 统管全局。
- **Worker 运行时**是 hey-my-buddy 管理的执行宿主，现为 supervisor 进程及其 adapter 和 controller。它认领 attempt、持有执行进程并报告停止证据；它的存活和状态不等于 buddy 的存活和状态。
- **hey-my-buddy** 是产品名。正文不再用 "Buddy" 指产品、黑板或服务。代码标识符（`buddy` 包与 CLI、`BuddyControl`/`BuddyWait`、`X-Buddy-CSRF`、`BUDDY_*` 环境变量）属于产品命名空间，保持不变。

地位对等不等于权限对等。Host 与 Worker 的权限不对称仍是不变量：Worker buddy 不能创建委派，也不能冒充 Host（见 [architecture](../reference/architecture.md#host-directed-work)）。本决定只改名，不放开这一限制；将来若允许 Worker buddy 成为子委派的 Host buddy，需要单独决定。

## 考虑过的方案

- **保留 "Buddy" 作为产品或服务名，Host 和 Worker 不加限定。** 现有的四种含义仍然并存，读者需要逐句猜测行动者。
- **只把 Worker 称为 buddy。** Host 被排除在知识源之外，暗示存在一个统管全局的协调者，与 ADR-002 的原则矛盾，也无法自然地表达同一 harness 可以担任任一角色。
- **把 Host 定义为协调者（coordinator 或 orchestrator）。** Host 的权限只及于它拥有的委派；全局调度是黑板的机械规则，全局视图属于用户。

## 影响

- 当前参考文档、两份 README、skill 与插件描述按这些术语改写；正文可以继续使用 Host、Worker 简称。历史 ADR 与验收记录保持原文。
- 代码中的 Worker 状态（`starting`、`idle`、`busy`、`stopping`、`lost`）和 `workers` 表描述的是 Worker 运行时，而不是 Worker buddy。
- 控制台界面中"请从 Buddy 重新打开控制台"等文字仍使用旧称；修改它们需要重新构建前端资源，另行处理。
- 目前只有 inquiry 由黑板一侧主动连接 buddy：黑板服务把 Host 的问题转发到 Worker 的 bridge。本决定不改变这条通道。
- 今后讨论 Host 侧契约时，Host 与 Worker 视为同一套 buddy 协议的两个角色切片；具体契约另行决定。
