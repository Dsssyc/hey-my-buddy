# hey-my-buddy

Agent buddies share delegated work through one blackboard: a Host buddy delegates a micro task and owns it, a Router buddy may choose the Worker buddy for it, Worker buddies execute its turns, and every fact they exchange is recorded on the blackboard. [ADR-013](docs/decisions/013-buddy-roles-and-blackboard-terminology.md) records why these terms were chosen. [ADR-021](docs/decisions/021-router-buddy-planes-and-routing-evidence.md) adds a buddy's identity, the Router's three planes, the routing knowledge terms and the macro task and micro task terms; its status says how much of that is implemented.

## Language

### The system

**hey-my-buddy**:
The product that provides the blackboard, Worker runtimes, adapters, CLI, console and skill through which buddies work together.
_Avoid_: Buddy

**黑板 / blackboard**:
The single shared record of micro tasks, turns, requests, results and decisions, together with the mechanical rules that admit, queue and fence work on it. It makes no judgment about the work and never directs a buddy.
_Avoid_: Buddy, Buddy service

### Buddies and roles

**伙伴 / buddy**:
A participant, normally an agent, that acts on the blackboard in the Host, Worker or Router role. A buddy is identified by its harness, provider, model and reasoning effort, for example `dsh / deepseek-official / deepseek-flash / off`; anything of that shape is a buddy, and one run of it is an execution of that buddy. Buddies are peers in standing and interact only through the blackboard.
_Avoid_: Buddy (for the product or the blackboard), peer Buddy; configuration, profile, model or template (for a buddy)

**角色 / role**:
The authority a buddy acts with on the blackboard, Host, Worker or Router, established by its authorized operation rather than by its harness or model. Host and Worker operations use scoped credentials; a Router has no direct blackboard credential and returns only a structured answer collected by its Worker runtime.

**Host 伙伴 / Host buddy**:
A buddy acting with Host authority: it owns the micro tasks it delegates or takes over, defining their authorization, deciding their boundaries and accepting their results. Short form: Host.
_Avoid_: coordinator, orchestrator, manager

**Worker 伙伴 / Worker buddy**:
A buddy that executes turns of a micro task with attempt-scoped authority; it may finish, ask for assistance or raise attention, but cannot create micro tasks, authorize helpers or accept results. Its results reach the Host for acceptance. Short form: Worker.
_Avoid_: a Buddy, coding Buddy

**Router 伙伴 / Router buddy**:
The buddy acting with routing authority. The user lists one or more buddies for the role in order, and the first available one holds it. It works in three planes: it chooses the Worker buddy for a micro task within the routing bounds, it maintains evaluations from recorded outcomes, and it builds model profiles from public sources. When the role passes to the next buddy in the list, all three planes pass with it. Unlike a Worker's result, its output takes effect without Host acceptance; the blackboard checks it for bounds and structure only. Short form: Router.
_Avoid_: decision Buddy, selector (for the buddy)

**路由边界 / routing bounds**:
The legal candidates a Router may choose from: the published, enabled and available buddies that satisfy the micro task's required capabilities and the user's pins and exclusions. The blackboard checks a Router's choice against these bounds only, never its judgment.

**用户 / user**:
The person on whose behalf buddies work: the source of their authority, the owner of shared settings and the holder of the global view. User authority is not a buddy role.

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

**adapter**:
The connector through which one harness takes part in the blackboard in one role.

**Worker 运行时 / Worker runtime**:
The hey-my-buddy-managed execution host that claims attempts, owns the processes executing them (a Worker or Router buddy's harness, or a plain command) and reports their stop evidence. Its liveness and state are not a buddy's.
_Avoid_: Worker (for the process)

### Work

**宏任务 / macro task**:
A user's overall piece of work, holding one or more micro tasks and used to group them for browsing and archival. It does not schedule work or grant control authority.
_Avoid_: 工作目标, objective

**微任务 / micro task**:
One bounded piece of work that a Host buddy delegates, owns and accepts; it may span several Worker turns. Every micro task belongs to at most one macro task.
_Avoid_: 委派 (as a noun), delegation, governed goal

**委派 / delegate**:
What a Host buddy does when it hands a micro task to the blackboard for a Worker buddy to execute. The word names the act, never the piece of work.

**协助任务 / helper**:
A micro task explicitly authorized by the Host to help another micro task.

**执行回合 / turn**:
One Worker buddy execution ending in a result, assistance request or attention boundary.

**验收 / acceptance**:
The Host's evidence-backed judgment on a fixed artifact, separate from Worker completion.
