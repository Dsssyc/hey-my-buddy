# hey-my-buddy

Agent buddies share delegated work through one blackboard: a Host buddy delegates a micro task and owns it, a Router buddy may choose the Worker buddy for it, Worker buddies execute its turns, and every fact they exchange is recorded on the blackboard. [ADR-013](docs/decisions/013-buddy-roles-and-blackboard-terminology.md) records why these terms were chosen. [ADR-021](docs/decisions/021-router-buddy-planes-and-routing-evidence.md) adds a buddy's identity, the Router's three planes, the routing knowledge terms and the macro task and micro task terms; its status says how much of that is implemented. [ADR-026](docs/decisions/026-delegation-authority.md) adds the Lead buddy and delegation authority. [ADR-027](docs/decisions/027-model-catalog-trust.md) adds the model catalog and its pending state.

## Language

### The system

**hey-my-buddy**:
The product that provides the blackboard, execution processes, adapters, CLI, console and skill through which buddies work together.
_Avoid_: Buddy

**黑板 / blackboard**:
The single shared record of micro tasks, turns, requests, results and decisions, together with the mechanical rules that admit, queue and fence work on it. It makes no judgment about the work and never directs a buddy.
_Avoid_: Buddy, Buddy service

### Buddies and roles

**buddy**:
A participant, normally an agent, that acts on the blackboard in the Host, Worker or Router role. A buddy is identified by its harness, provider, model and reasoning effort, for example `dsh / deepseek-official / deepseek-flash / off`; anything of that shape is a buddy, and one run of it is an execution of that buddy. Buddies are peers in standing and interact only through the blackboard.
_Avoid_: Buddy (for the product or the blackboard), peer Buddy; configuration, profile, model or template (for a buddy); 伙伴 (Chinese text keeps the word buddy, as it keeps Host, Worker, Router and harness)

**推理强度 / reasoning effort**:
The fourth part of a buddy's identity: how much reasoning the harness asks of the model. A harness offers it in values such as `off`, `high` and `max`, and each value of a model is a different buddy. Each harness names and grades its efforts in its own way, so two harnesses' values are not comparable even when they share a name.
_Avoid_: 思考强度, 档位, 思考档位 (档位 names a permission level), effort level, thinking level

**角色 / role**:
The authority a buddy acts with on the blackboard, Host, Worker or Router, established by its authorized operation rather than by its harness or model. Host and Worker operations use scoped credentials; a Router has no direct blackboard credential and returns only a structured answer collected by its Worker runtime.

**Host buddy**:
A buddy acting with Host authority: it owns the micro tasks it delegates or takes over, defining their authorization, deciding their boundaries and accepting their results. Short form: Host.
_Avoid_: coordinator, orchestrator, manager

**Worker buddy**:
A buddy that executes turns of a micro task with attempt-scoped authority; it may finish, ask for assistance or raise attention, but cannot create micro tasks, authorize helpers or accept results. Its results reach the Host for acceptance. The one exception is a Lead. Short form: Worker.
_Avoid_: a Buddy, coding Buddy

**Lead buddy**:
The buddy executing a micro task that carries delegation authority. Toward the Host that delegated that micro task it is a Worker: it reports only to that Host, which accepts its result, and it never talks to the user. Toward the micro tasks created under that micro task it acts with Host authority: it delegates them, decides their boundaries, sends them back and accepts them. It is the buddy the user names for that work, and exists only when the user asks for it. Short form: Lead.
_Avoid_: sub-Host, coordinator, orchestrator, manager, supervisor

**委派权 / delegation authority**:
The right to act as Host for the micro tasks created under one micro task. A Host grants it together with that micro task, only at the user's request. It covers no other micro task and no shared setting, and a Lead cannot grant it to anyone.

**名下的微任务 / owned micro task**:
A micro task a Lead delegates under the micro task that carries delegation authority. It belongs to that micro task, not to the buddy executing it, so it stays there when that micro task changes hands. The Lead decides its boundaries and accepts it; the Host does neither.
_Avoid_: child task, sub-task; a helper is a different thing

**Router buddy**:
The buddy acting with routing authority. The user lists one or more buddies for the role in order, and the first available one holds it. It works in three planes: it chooses the Worker buddy for a micro task within the routing bounds, it maintains evaluations from recorded outcomes, and it builds model profiles from public sources. When the role passes to the next buddy in the list, all three planes pass with it. Unlike a Worker's result, its output takes effect without Host acceptance; the blackboard checks it for bounds and structure only. Short form: Router.
_Avoid_: decision Buddy, selector (for the buddy)

**路由边界 / routing bounds**:
The legal candidates a Router may choose from: the published, enabled and available buddies that satisfy the micro task's required capabilities and the user's pins and exclusions. The blackboard checks a Router's choice against these bounds only, never its judgment.

**路由计算 / routing calculation**:
One run of the Router buddy that chooses the Worker buddy for one micro task within its routing bounds. The blackboard records it as an execution of its own, with a state, usage and a detail. When the bounds leave a single candidate or the Host names the buddy, no routing calculation runs.
_Avoid_: 内部路由, 决策任务 (a decision is the Host's), 路由 alone (routing is the micro task's step, which may need no Router)

**用户 / user**:
The person on whose behalf buddies work: the source of their authority, the owner of shared settings and the holder of the global view. User authority is not a buddy role.
_Avoid_: 用户 or user for the developer of this project

**开发者 / developer**:
The person who builds this project and sets its direction. Records say the developer whenever they mean that one person: who decided, asked for, reported or authorized something, and whose machine, board or accounts a record describes. A user is anyone who uses the product.
_Avoid_: 用户, user (for the developer)

### Routing knowledge

**模型画像 / model profile**:
The public picture of a model: its positioning and its commonly reported strengths and weaknesses, each with a source, together with published facts such as price and context length. It belongs to the model, is shared by every buddy that uses the model and does not depend on the user's work.
_Avoid_: model card, evaluation (for public information)

**评价 / evaluation**:
What the blackboard's recorded outcomes show about one buddy in this user's work, organised by domain and task type: counted results and observations that cite micro tasks. It belongs to the buddy.
_Avoid_: card, assessment, profile (for local evidence)

**领域清单 / domain list**:
The user's own vertical domains, each the kind of expertise their micro tasks call for, grown from those micro tasks and maintained by the Router. A domain is neither a project nor a task type.

**任务类型 / task type**:
One of seven fixed kinds of work: create (新建), change (改造), debug (排错), verify (验证), review (审查), research (调研) and write (撰写).

**探索 / exploration**:
An occasional routing choice of a rarely used buddy for a short micro task, made to gather evidence about it.

### Where buddies run

**harness**:
A native agent product in which a buddy runs, such as DSH, ZCode, Codex or Claude Code. One harness can run buddies in any role.

**形态 / form**:
The way a harness is run: as a CLI in a terminal, or as an app (a desktop app or an IDE extension), such as the Claude Code CLI or the Codex app. One harness can behave differently in its forms, so a statement about a harness names its form unless it holds for every form.
_Avoid_: 前端, frontend (that is the console's), client

**adapter**:
The connector through which an execution reaches the blackboard: a harness in one of its roles or a command. A micro task always runs a buddy through a harness; a command execution is never a micro task.

**模型 / model**:
One concrete language model, whoever provides it. Providers, communities and users may call the same model by different names.

**模型服务 / model service**:
One provider's service of one model. The blackboard knows a provider only by the name a harness gives it, so it identifies a model service by harness, provider and model, for example `dsh / deepseek-official / deepseek-flash`; a buddy is a model service used through that harness at one reasoning effort.
_Avoid_: family, 模型家族 (a model family is commonly a series of several models); model (for one provider's service of it)

**提供方 / provider**:
Whoever serves models to a harness: a model's own vendor or another party. In a buddy's identity it is the name the harness gives that source.
_Avoid_: 供应方, 服务商

**订阅 / subscription**:
A way of paying for model use: a plan bought from a vendor for a period, whose terms and limits bound how it may be used, whether the harness signs in with an account or with a key the plan issued.
_Avoid_: 订阅登录 / subscription sign-in (the sign-in method does not decide the plan), 第一方登录 / first-party login (a vendor's own API key is first-party too)

**按量 / metered**:
A way of paying for model use: each request billed by usage to the owner of the key, under the terms of the provider that issued it.
_Avoid_: BYOK, API 密钥 / API key (as a way of paying: a key can belong to a subscription)

**冷却 / cooling**:
The time a provider of one harness sits out automatic routing after it refused work for quota. It grows with consecutive refusals, ends by itself or when the user clears it, and a later success ends the sequence. It belongs to the provider in that harness, not to a model or a buddy, and never changes the user's enablement or preferences.
_Avoid_: 额度耗尽 / exhausted (for the provider's state; a refusal that interrupts a turn is still reported as quota used up), quota window

**会话记录 / session record**:
What one execution showed while it ran, as the harness gave it: messages, the reasoning it supplied, tool calls and plans, kept for the console to display. It is not evidence of what happened: settlement, acceptance and stop decisions never read it. It is deleted with its macro task.
_Avoid_: transcript, 会话日志 (for the product's record; a harness's own session store is its native session)

**改派 / reroute**:
A new routing decision for the same micro task, without starting another micro task: at a Host's request, or by the blackboard when the chosen buddy's provider refused the turn for quota before its model started.
_Avoid_: 重派, 换 buddy (for the routing step itself)

**模型目录 / model catalog**:
The blackboard's record of the models and reasoning efforts a harness reported it can provide, and so of which buddies exist in that harness. It is a reading taken at one moment, not a fact about the harness: a buddy whose model is missing from a later reading is pending, still usable, until a further reading confirms the absence.
_Avoid_: model list (for the blackboard's record; the model list is what the harness itself returns)

**执行进程 / execution process**:
The process the service starts for one attempt, carrying it from start to result: it runs the Worker or Router buddy's harness or the plain command, owns the processes started for that run, keeps the attempt's result receipt and delivers it to the blackboard, and outlives a restart of the service. Its liveness and state are not a buddy's.
_Avoid_: run controller, Worker runtime, Worker (for this process)

### Work

**项目 / project**:
The code repository that a macro task belongs to. Every worktree and checkout of one repository belongs to the same project; a worktree or a checkout is not a project.
_Avoid_: 工作区, workspace (an execution workspace is a checkout, not a project)

**宏任务 / macro task**:
A user's overall piece of work, holding one or more micro tasks and used to group them for browsing. It is the unit by which the user deletes records. It does not schedule work or grant control authority.
_Avoid_: 工作目标, objective

**微任务 / micro task**:
One bounded piece of work that a Host buddy delegates, owns and accepts; it may span several Worker turns. Every micro task belongs to at most one macro task.
_Avoid_: 委派 (as a noun), delegation, governed goal

**委派 / delegate**:
What a Host buddy does when it hands a micro task to the blackboard for a Worker buddy to execute. The word names the act, never the piece of work.

**协助任务 / helper**:
A micro task explicitly authorized by the Host to help another micro task.

**修正 / fix**:
A micro task submitted to repair a defect found in another micro task after that one was accepted. It names the micro task it fixes; the fixed micro task stays accepted and counts as a defective delivery.
_Avoid_: correction (a run's in-turn correction of a model's answer), reopening (acceptance is final)

**执行回合 / turn**:
One Worker buddy execution ending in a result, assistance request or attention boundary; a Lead's turn may also end waiting on its owned micro tasks.

**问询 / inquiry**:
A question the micro task's Host or Lead puts to its running turn through the blackboard, together with the answer correlated with it. It changes neither the turn's task nor its authority, and it is not a continuation.
_Avoid_: 询问, 消息 (the stored record is a message; the act is an inquiry)

**封存 / seal**:
The fixed artifact the blackboard makes of a checkout's content when a turn ends; acceptance judges it, never the Worker's own report.
_Avoid_: 第 N 封 (say 第 N 回合的封存), 快照

**预期时长 / expected duration**:
When the Host expects a micro task or a command execution to be done: half an hour unless it says otherwise. Reaching it stops nothing; it is the moment the Host is reminded to look, and the reminder repeats each time the same span passes again.
_Avoid_: timeout, deadline, time limit (for delegated work)

**有序收尾 / wrap-up**:
Stopping a running micro task through the blackboard so that its Worker says what is done and what is not and delivers before it stops; a forced stop follows when the grace period ends, and the partial output is sealed either way.
_Avoid_: graceful shutdown; cancel (for the summary itself)

**权限档位 / permission level**:
How far a buddy may go in one run, in five levels from narrow to wide: 不用工具 / no tools (it only answers), 只联网 / web only (it may search the web and fetch pages and cannot touch local files), 只读 / read-only (read and search; no file may be modified), 写检出 / checkout-write (edit and run commands; files may be modified only inside the micro task's checkout) and 放开 / full access (no limit on where files are modified). The first two say which tools exist; the last three say where files may be modified and nothing about the network. A Worker buddy's level is one of the last three, chosen by the Host when it delegates and changeable between turns; a Router buddy's level follows from what it is doing. A harness carries a level out either 强制 / enforced, when its sandbox or tool limits stop what the level excludes, or 约定 / by convention, when it is only asked to stay inside and the blackboard checks afterwards what it can.
_Avoid_: 权限清单, sandbox mode (a level is not a list of grants, and a sandbox mode is only how one harness carries a level out)

**换档 / level change**:
Short for changing a micro task's permission level between turns: the Host continues the micro task with another level, and the next turn runs at that level.
_Avoid_: 切换模式, mode switch (a native mode is only how one harness carries a level out)

**未知事件 / unknown event**:
A native event or reply shape that a harness's run module does not recognize. It is a fact, never a failure: it is counted by type and returned with the run's result for the Host to see.
_Avoid_: 没读懂的事件, protocol error (an unknown event is not an error)

**未证实项 / unconfirmed item**:
Something a run asked its harness for and could not confirm from what the harness reported back, such as a restriction or the reading of the model catalog. The run continues and its result names the item; it does not mean the harness refused or ignored the request.
_Avoid_: 未验证的限制 (as a reason to refuse a run)

**未知结束 / unknown end**:
A run whose native side ended in a way the run module could not read as success or as failure. It is never treated as success: the turn fails with a reason that names what was unread.
_Avoid_: 结束未确认 (that says a process's stop was not confirmed, a different fact)

**换手 / handoff**:
A micro task passing to a different buddy between turns: the buddy that executed its earlier turns stops, and another executes the next. The micro task, its Host and the micro tasks under it stay the same.
_Avoid_: 接管, takeover (those name a Host taking over the ownership of a micro task)

**验收 / acceptance**:
The Host's evidence-backed judgment on a fixed artifact, separate from Worker completion.

### What records say

**原因 / reason**:
Why something the blackboard did or observed happened, recorded as one fixed code for one fact together with the facts it needs, never as a sentence. Whoever reads it supplies the words, in the reader's language, and any advice on what to do next.
_Avoid_: 说明, remedy (fixed advice is the reader's, not stored with the reason)

**内容 / content**:
Text that a buddy or a native harness wrote, such as a Host's continuation, a Worker's result, a Router's explanation or a harness's own message about a model. It is kept and shown as written; the blackboard never turns content into a reason or a reason into content.
_Avoid_: reason (for words someone wrote)

**备注 / note**:
The content someone attaches to an action of their own: a Host's decision, continuation, cancellation, scope change, acceptance or request for full access, the user's stop of a macro task, a Router's choice of buddy. Some actions require one; where it is left out, the blackboard writes nothing in its place, because the record already says who acted.
_Avoid_: reason (for these words)

**诊断 / diagnostic**:
Error detail meant for the developer, such as a native error code or the text of a failure. It travels with the error it explains and is not a reason.
