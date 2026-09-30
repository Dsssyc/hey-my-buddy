# hey-my-buddy

Agent buddies share delegated work through one blackboard: a Host buddy owns a goal, a Router buddy may choose its configuration, Worker buddies execute its turns, and every fact they exchange is recorded on the blackboard. [ADR-013](docs/decisions/013-buddy-roles-and-blackboard-terminology.md) records why these terms were chosen.

## Language

### The system

**hey-my-buddy**:
The product that provides the blackboard, Worker runtimes, adapters, CLI, console and skill through which buddies work together.
_Avoid_: Buddy

**黑板 / blackboard**:
The single shared record of goals, turns, requests, results and decisions, together with the mechanical rules that admit, queue and fence work on it. It makes no judgment about the work and never directs a buddy.
_Avoid_: Buddy, Buddy service

### Buddies and roles

**伙伴 / buddy**:
A participant, normally an agent, that acts on the blackboard in the Host, Worker or Router role. Buddies are peers in standing and interact only through the blackboard.
_Avoid_: Buddy (for the product or the blackboard), peer Buddy

**角色 / role**:
The authority a buddy acts with on the blackboard, Host, Worker or Router, established by its authorized operation rather than by its harness or model. Host and Worker operations use scoped credentials; a Router has no direct blackboard credential and returns only a structured answer collected by its Worker runtime.

**Host 伙伴 / Host buddy**:
A buddy acting with Host authority: it owns the governed goals it submits or takes over, defining their authorization, deciding their boundaries and accepting their results, and may perform Host-only operations such as evaluation maintenance. Short form: Host.
_Avoid_: coordinator, orchestrator, manager

**Worker 伙伴 / Worker buddy**:
A buddy that executes turns of a governed goal with attempt-scoped authority; it may finish, ask for assistance or raise attention, but cannot create goals, authorize helpers or accept results. Its results reach the Host for acceptance. Short form: Worker.
_Avoid_: a Buddy, coding Buddy

**Router 伙伴 / Router buddy**:
A buddy acting with routing authority: it examines one governed goal read-only and submits one configuration choice within the routing bounds. Unlike a Worker's result, its choice takes effect without Host acceptance. Short form: Router.
_Avoid_: decision Buddy, selector (for the buddy)

**路由边界 / routing bounds**:
The legal candidates a Router may choose from: the published, enabled and available configurations that satisfy the goal's fixed fields and required capabilities and the user's pins and exclusions. The blackboard checks a Router's choice against these bounds only, never its judgment.

**用户 / user**:
The person on whose behalf buddies work: the source of their authority, the owner of shared settings and the holder of the global view. User authority is not a buddy role.

### Where buddies run

**harness**:
A native agent product in which a buddy runs, such as DSH, ZCode, Codex or Claude Code. One harness can run buddies in any role.

**adapter**:
The connector through which one harness takes part in the blackboard in one role.

**Worker 运行时 / Worker runtime**:
The hey-my-buddy-managed execution host that claims attempts, owns the processes executing them (a Worker or Router buddy's harness, or a plain command) and reports their stop evidence. Its liveness and state are not a buddy's.
_Avoid_: Worker (for the process)

### Work

**工作目标 / objective**:
A user's overall objective used to group related delegations for browsing and archival. It does not schedule work or grant control authority.

**委派 / governed goal**:
One bounded piece of work owned and accepted by a Host buddy; it may span several Worker turns.

**协助任务 / helper**:
A delegation explicitly authorized by the Host to help another delegation.

**执行回合 / turn**:
One Worker buddy execution ending in a result, assistance request or attention boundary.

**验收 / acceptance**:
The Host's evidence-backed judgment on a fixed artifact, separate from Worker completion.
