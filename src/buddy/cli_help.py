"""Per-method CLI help generated from the live request validators.

``buddy help`` lists every public method with one sentence; ``buddy help METHOD``
prints the parameters that method actually accepts, with the type, default and bounds
the service enforces. Those parameter facts are **read from the validation code
itself** by static analysis of this package: every entry names the validator it came
from. There is deliberately no second parameter table to keep in sync, and nothing
here executes a service operation, starts a daemon or calls a model — it only parses
the source files that ship with this package, on every invocation, so a changed
validator constant changes the next answer.

The static reader understands the validation vocabulary of :mod:`buddy.schemas`,
follows the delegation chain from a C-Two operation to the module that validates it,
and keeps nested objects and the documented flat-or-nested ``spec`` spelling. A
condition it cannot reduce to one bound (a hash comparison, a duplicate-key check, a
cross-field conflict) is reported as the readable condition with its source location;
a field the validator accepts but never bounds says exactly that, and a runtime
field-name set is named as runtime-computed. Nothing is silently dropped: a field a
request may carry is never quietly missing from help.

Only the one-sentence method summaries are prose (:data:`SUMMARIES`), and a test
checks that they cover exactly the public method surface. Help is a human summary:
the error envelope from a rejected request remains the authoritative boundary.
"""
from __future__ import annotations

import ast
import copy
import difflib
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

from . import transport

#: The package directory whose modules are read for parameter facts.
PACKAGE_ROOT = Path(__file__).resolve().parent

#: One sentence per public method. Prose only: no defaults, bounds or versions.
SUMMARIES: dict[str, str] = {
    "help": "Show every method, or one method's validated parameters, defaults and bounds.",
    "ping": "Authenticated process liveness; no storage or runtime scan.",
    "health": "Service, runtime, capacity and integrity report; read it before relying on routing.",
    "capabilities": "Adapter report with availability, capabilities, named operations and the honest limitations map.",
    "adapters": "Adapter report; the same operation as capabilities.",
    "harness-set": "Choose the harness executable or restore automatic detection.",
    "harness-verify": "Prepare or explicitly start one native review verification for an enabled configuration.",
    "quota-redetect": "Open the one-shot routing retry window of a provider's no-reset quota exhaustion; no model or balance query.",
    "accounts": "Read sanitized account sources and capabilities without a native refresh.",
    "account-set": "Select native or Worker account source for future attempts using expectedRevision.",
    "account-login": "Private Worker OAuth login or key input through stdin; never changes shared native login.",
    "account-status": "Read one private native login owner; returns no authorization link or code.",
    "account-cancel": "Cancel one owned private login and retain unconfirmed credentials.",
    "account-logout": "Log out only the independent Worker account after confirmed prior stop.",
    "account-remove": "Remove only an unused independent Worker account and its credentials.",
    "runtime": "Installed-runtime identity, description and source-leak report.",
    "backup": "Service-owned verified rolling backup.",
    "backup-preflight": "Read-only evidence backup inventory: copying, skipping and refusal reasons; starts nothing.",
    "upgrade": "Coordinated skill, launcher and runtime upgrade; requires separate installation authorization.",
    "install": "Install the shared skill and its runtime; requires separate user authorization.",
    "paths": "Skill, launcher, data and runtime locations; reads files only and starts nothing.",
    "storage-plan": "Read-only storage reclamation plan.",
    "storage-apply": "Apply one confirmed storage plan.",
    "worker-sessions": "List board-proven Worker native sessions (Codex history, read-only).",
    "submit": "Admit one governed goal, prepare its execution workspace and route its execution configuration.",
    "get": "Compact owner view of one governed goal; includeAudit adds the immutable record.",
    "decide": "Record Host authority on the active request and authorize concrete helpers.",
    "continue": "Record new input for a governed goal and invalidate older unconsumed continuations.",
    "takeover": "Rotate the Host control capability and owner generation without restarting work.",
    "cancel": "Fence a governed goal and complete its owned descendant graph.",
    "acknowledge": "Record Host review of the fixed final artifact, separately from execution.",
    "scope-amend": "Append one authorized write-scope version for the next stage.",
    "workspace-resolve": "Settle one recorded out-of-scope failure site by restore, adopt or abandon.",
    "integration-record": "Bind the sealed artifact to a target checkout/commit relationship, or record not-required.",
    "workspace-cleanup-plan": "Plan removal of this run's own registered managed checkout.",
    "workspace-cleanup-apply": "Remove exactly the planned checkout after rechecking every eligibility rule.",
    "await": "Wait for one existing run without starting, resuming, retrying or cancelling work.",
    "suggest": "Record one bounded suggestion on the caller's own run.",
    "objective-list": "Bounded work-objective and standalone-delegation summaries by latest activity.",
    "objective-timeline": "One work objective's bounded timeline with spans and Host markers.",
    "execution-submit": "Admit one ordinary execution record for the command/external adapters.",
    "execution-cancel": "Cancel one ordinary execution: queued work now, durable intent for an active attempt.",
    "execution-retry": "Requeue an eligible failed, cancelled or reconciliation-needed task as a new generation.",
    "execution-acknowledge": "Record review of a persisted execution result.",
    "status": "Task state, selected attempt, worker, artifacts and workflow summary.",
    "list": "Bounded task history with filters, stable keyset paging and titles.",
    "result": "Task view plus the adapter outcome and commit metadata; NOT_READY before a result exists.",
    "artifacts": "Verified artifact records for a run, task or attempt.",
    "events": "Committed board events after a cursor.",
    "wait": "Bounded wait for one task to change or finish; never cold-starts a service.",
    "watch": "Bounded event wait on the dedicated wait resource.",
    "inquire": "Bounded read-only observation, or one correlated question to a live agent.",
    "message": "Post one correlated inquiry to a run or task.",
    "messages": "List retained messages for one task, or across tasks.",
    "message-get": "Read one inquiry and its recorded answer without resubmitting the question.",
    "message-update": "Record caller-provided delivery or answer evidence for an inquiry.",
    "workers": "Registered workers with state, adapter and current attempt.",
    "worker-register": "Register or refresh one worker; idempotent by commandId.",
    "worker-claim": "Atomically claim one queued task and return its attempt, lease and capability.",
    "worker-reconcile": "Reattach the legitimate worker after daemon downtime.",
    "worker-renew": "Extend the attempt lease and report a durable cancel request.",
    "worker-progress": "Append one bounded progress event to an attempt.",
    "worker-result": "Commit a result, artifacts, state and completion event in one transaction.",
    "worker-release": "Release an attempt that never started.",
    "worker-start": "Start one detached worker supervisor in its own session.",
    "worker-stop": "Write a cooperative durable stop request; never signals a process this CLI did not create.",
    "wait-capacity": "Wait admission counters.",
    "console": "Open, observe or close the private console; browser and wait are CLI-local.",
    "console-snapshot": "Read the bounded evaluation and configuration snapshot without a browser session.",
    "evaluation-write-begin": "Begin one fair bounded evaluation write intent or lease.",
    "evaluation-write-renew": "Renew an owned evaluation write intent or lease.",
    "user-policy-publish": "Authenticated console publication of user policy and configuration.",
    "assessment-publish": "Maintenance Host publication of changed automatic assessment cards.",
    "evaluation-write-abort": "Fence and release this evaluation writer, leaving the last revision unchanged.",
    "evaluation-reader-begin": "Admit one bounded selection reader when no writer intent is present.",
    "evaluation-reader-release": "Release one selection reader and promote a waiting writer when possible.",
    "evaluation-evidence-record": "Append one attributed, deduplicated evidence record.",
    "evaluation-prepare": "Host-only bounded factual preparation for a model-card update; no model call.",
    "evaluation-history": "Read bounded publication metadata pages.",
    "selection-request": "One durable, bounded routing selection request.",
    "selection-get": "Read one recorded selection decision; includeAudit adds the persisted input.",
    "selection-list": "Read-only compact selection history.",
    "model-catalog-refresh": "Explicit native model metadata discovery; one observation per request and no model call.",
    "model-profiles": "Bounded active/unavailable profile page with associated cards.",
    "restart": "Detach the daemon without cancelling owned work; independent workers survive.",
    "stop": "Cancel queued tasks and request durable cancellation of active attempts, then drain.",
}

#: CLI-level methods that never reach a C-Two operation.
LOCAL_ROOTS: dict[str, tuple[str, str]] = {
    "await": ("blocking", "await_run"),
    "install": ("skill_install", "install"),
    "upgrade": ("skill_install", "install"),
    "paths": ("skill_install", "paths"),
    "backup-preflight": ("backup", "preflight_command"),
    "worker-start": ("cli", "_worker_command_unlocked"),
    "worker-stop": ("cli", "_worker_command_unlocked"),
    "console": ("console_cli", "run"),
}

#: Local methods whose validated dict is not the first parameter.
LOCAL_POSITIONS: dict[str, int] = {"worker-start": 1, "worker-stop": 1}

#: Printed in place of a method summary a newly added method has not written yet: the
#: index still lists every method instead of failing, and the test suite fails on it.
MISSING_SUMMARY = "(no one-sentence summary yet; add one to cli_help.SUMMARIES)"

#: Prose notes that describe a CLI-level choice the validators cannot show.
METHOD_NOTES: dict[str, tuple[str, ...]] = {
    "stop": ("the CLI name fixes action to 'stop'; restart and status are separate CLI names",),
    "restart": ("the CLI name fixes action to 'restart'; stop and status are separate CLI names",),
    "adapters": ("the same C-Two operation as capabilities",),
    "upgrade": ("runs the same installer as install with the upgrade coordinator",),
    "quota-redetect": ("reuse requestId after a lost reply, even if the chance was consumed; changed inputs conflict; no model/balance query or recovery claim",),
    "backup-preflight": ("reads directory entries only; no locks, files, database writes, service or runtime startup; samples are bounded to 20 paths per outcome",),
    "wait-capacity": ("takes no parameters",),
}

#: Fields the CLI accepts locally on every method, ahead of (or beside) the RPC.
CLI_LOCAL_LINES: tuple[str, ...] = (
    'output is CLI-local: "brief" (default) or "full"; console always keeps its complete response.',
    "controlFile (governed mutations) names a private 0600 file holding the Host control triple; the CLI removes it before the RPC and never prints the token.",
)

#: The control methods whose request actually requires the Host control triple.
_CONTROL_METHODS = frozenset(
    {
        "decide",
        "continue",
        "takeover",
        "cancel",
        "acknowledge",
        "scope-amend",
        "workspace-resolve",
        "integration-record",
        "workspace-cleanup-plan",
        "workspace-cleanup-apply",
    }
)


@dataclass
class Parameter:
    """One accepted parameter and the facts the validator enforces for it."""

    name: str
    kind: str = "value"
    required: bool = False
    default: str | None = None
    bounds: str | None = None
    conditional: bool = False
    children: list["Parameter"] = dataclass_field(default_factory=list)
    source: str = ""
    #: A readable condition the static reader could not reduce to one bound, kept
    #: with its source so the fact is never silently dropped. Only the request
    #: validator is quoted here; nothing was executed to discover it.
    expression: str | None = None


@dataclass
class MethodHelp:
    """Everything ``buddy help METHOD`` prints for one method."""

    method: str
    operation: str
    parameters: list[Parameter]
    inline: list[str]
    opaque: list[str]
    locations: list[str]
    notes: list[str]


# ---------------------------------------------------------------------------
# Static reader: parse this package and follow the validators
# ---------------------------------------------------------------------------

#: The leaf validation vocabulary of :mod:`buddy.schemas`: helpers whose call-site
#: arguments name the request field and carry its bounds. Composite validators that
#: take the whole request dict are *walked* instead of summarised, so their fields
#: come from the same source as every other fact.
_HELPERS = frozenset(
    {
        "reject_unknown",
        "optional_string",
        "required_string",
        "optional_bool",
        "optional_int",
        "optional_positive_int",
        "optional_sha256",
        "string_list",
        "bounded_text",
    }
)

#: Validation functions that return one nested sub-object of their first argument.
#: Naming the nested key keeps ``helpers[].spec.*`` fields under ``helpers`` instead
#: of leaking them to the top level.
_NESTED_EXTRACTORS = {"_spec_fields": "nested_key"}

#: Prose notes attached when the reader follows one composite validator. The bounds
#: themselves come from the function body; these sentences explain a relationship
#: between fields that is expressed in control flow, not in one comparison.
_HELPER_NOTES: dict[str, tuple[str, ...]] = {
    "normalize_control": (
        "hostId, ownerGeneration and controlToken are one Host control triple: supply all three or none "
        "(schemas.normalize_control)",
    ),
    "execution_timeout": (
        "timeoutSeconds 0 (the explicit sentinel) means no wall-clock deadline; otherwise the 10–86400 second range applies",
    ),
}

#: Prose notes for fields the request validator accepts but does not itself bound.
FIELD_NOTES: dict[str, str] = {
    "consoleAuthority": (
        "a console session identity, not a caller-controlled flag: the service resolves it from the console "
        "registry and rejects it for every non-console caller"
    ),
    "owner": "attribution only: recorded with the run and excluded from the input fingerprint",
}

#: ``self.<container>`` attributes inside the service resources.
_SERVICE_CONTAINERS = {
    ("store",): "BoardStore",
    ("store", "workflow"): "WorkflowCoordinator",
    ("decisions",): "DecisionCoordinator",
    ("evaluation",): "EvaluationStore",
}

_UNRESOLVED = object()


def _attribute_parts(node: ast.AST) -> list[str] | None:
    """``self.store.workflow.get`` -> ``["self", "store", "workflow", "get"]``."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        parts.reverse()
        return parts
    return None


def _expression_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive
        return "<expression>"


class _SourceIndex:
    """Parsed modules, their functions, classes, constants and imports."""

    def __init__(self, root: Path):
        self.root = root
        self.trees: dict[str, ast.Module] = {}
        self.paths: dict[str, Path] = {}
        self.functions: dict[str, dict[str, ast.FunctionDef]] = {}
        self.classes: dict[str, dict[str, dict[str, ast.FunctionDef]]] = {}
        self.constants: dict[str, dict[str, ast.AST]] = {}
        self.imports: dict[str, dict[str, tuple[str, str | None]]] = {}
        self._constants_cache: dict[tuple[str, str], object] = {}
        self._load()

    # -- loading ---------------------------------------------------------
    def _load(self) -> None:
        for path in sorted(self.root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(self.root).with_suffix("")
            name = ".".join(relative.parts)
            if name.endswith(".__init__"):
                name = name[: -len(".__init__")]
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            self.trees[name] = tree
            self.paths[name] = path
            self.functions[name] = {}
            self.classes[name] = {}
            self.constants[name] = {}
            self.imports[name] = {}
        for name, tree in self.trees.items():
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.functions[name][node.name] = node
                elif isinstance(node, ast.ClassDef):
                    self.classes[name][node.name] = {
                        child.name: child
                        for child in node.body
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    }
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            self.constants[name][target.id] = node.value
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                    self.constants[name][node.target.id] = node.value
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        bound = alias.asname or alias.name
                        self.imports[name][bound] = self._import_target(name, node, alias)
        # Function-local imports bind for the whole module in this reader: a name is
        # only ever used inside the function that imported it.
        for name, tree in self.trees.items():
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                if node in tree.body:
                    continue
                for alias in node.names:
                    bound = alias.asname or alias.name
                    self.imports[name][bound] = self._import_target(name, node, alias)

    def _import_target(self, module: str, node: ast.Import | ast.ImportFrom, alias: ast.alias) -> tuple[str, str | None]:
        """Resolve one import to ``(module_name, attribute_or_None)`` inside this package."""
        if isinstance(node, ast.Import):
            parts = alias.name.split(".")
            candidate = ".".join(parts[1:]) if parts[0] == "buddy" else alias.name
            return (candidate if candidate in self.trees else alias.name, None)
        base = node.module or ""
        if node.level:
            package = module.rpartition(".")[0]
            for _ in range(node.level - 1):
                package = package.rpartition(".")[0]
            base = f"{package}.{base}" if package and base else (package or base)
        if base.startswith("buddy."):
            base = base[len("buddy.") :]
        if not base:
            # ``from . import schemas`` in a top-level module: the alias names the module.
            if alias.name in self.trees:
                return (alias.name, None)
            return ("", alias.name)
        if base not in self.trees:
            return (base, alias.name)
        if alias.name == "*":
            return (base, None)
        # ``from . import schemas`` binds a module; ``from .store import BoardStore`` binds an attribute.
        nested = f"{base}.{alias.name}" if base else alias.name
        if nested in self.trees:
            return (nested, None)
        return (base, alias.name)

    # -- constant evaluation --------------------------------------------
    def constant(self, module: str, name: str) -> object:
        key = (module, name)
        if key not in self._constants_cache:
            self._constants_cache[key] = self._eval(self.constants.get(module, {}).get(name), module)
        return self._constants_cache[key]

    def _eval(self, node: ast.AST | None, module: str, locals_: dict[str, object] | None = None) -> object:
        if node is None:
            return _UNRESOLVED
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if locals_ and node.id in locals_:
                return locals_[node.id]
            value = self.constant(module, node.id)
            if value is not _UNRESOLVED:
                return value
            target = self.imports.get(module, {}).get(node.id)
            if target and target[1]:
                return self.constant(target[0], target[1])
            return _UNRESOLVED
        if isinstance(node, ast.Attribute):
            parts = _attribute_parts(node)
            if parts and locals_ and parts[0] in locals_:
                return _UNRESOLVED
            if parts:
                target = self.imports.get(module, {}).get(parts[0])
                if target and target[0] in self.trees and target[1] is None and len(parts) == 2:
                    return self.constant(target[0], parts[1])
            return _UNRESOLVED
        if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
            values: list[object] = []
            for element in node.elts:
                target = element.value if isinstance(element, ast.Starred) else element
                value = self._eval(target, module, locals_)
                if value is _UNRESOLVED:
                    return _UNRESOLVED
                if isinstance(element, ast.Starred):
                    if not isinstance(value, (set, frozenset, list, tuple)):
                        return _UNRESOLVED
                    values.extend(value)
                else:
                    values.append(value)
            if isinstance(node, ast.Set):
                return set(values)
            if isinstance(node, ast.List):
                return list(values)
            return tuple(values)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            left = self._eval(node.left, module, locals_)
            right = self._eval(node.right, module, locals_)
            if isinstance(left, (set, frozenset)) and isinstance(right, (set, frozenset)):
                return set(left) | set(right)
            return _UNRESOLVED
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Pow, ast.FloorDiv)):
            left = self._eval(node.left, module, locals_)
            right = self._eval(node.right, module, locals_)
            if isinstance(left, int) and isinstance(right, int) and not isinstance(left, bool) and not isinstance(right, bool):
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, ast.Pow) and 0 <= right < 1024:
                    return left**right
                if isinstance(node.op, ast.FloorDiv) and right:
                    return left // right
            return _UNRESOLVED
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("set", "frozenset", "list", "tuple"):
            if not node.args:
                return set() if node.func.id in ("set", "frozenset") else []
            return self._eval(node.args[0], module, locals_)
        return _UNRESOLVED

    def _string_set(self, node: ast.AST, module: str, locals_: dict[str, object]) -> tuple[set[str], list[str]]:
        """Resolve one allowed-field expression into literals plus unresolved parts."""
        value = self._eval(node, module, locals_)
        if value is not _UNRESOLVED:
            if isinstance(value, (set, frozenset, list, tuple)) and all(isinstance(item, str) for item in value):
                return set(value), []
            return set(), [_expression_text(node)]
        found: set[str] = set()
        unknown: list[str] = []
        elements = node.elts if isinstance(node, (ast.Set, ast.List, ast.Tuple)) else [node]
        for element in elements:
            target = element.value if isinstance(element, ast.Starred) else element
            value = self._eval(target, module, locals_)
            text = _expression_text(target)
            if isinstance(value, (set, frozenset, list, tuple)) and all(isinstance(item, str) for item in value):
                found.update(value)
            elif isinstance(value, str):
                found.add(value)
            elif text not in unknown:
                unknown.append(text)
        return found, unknown

    # -- lookups ---------------------------------------------------------
    def resolve_function(self, module: str, parts: list[str], container: str | None = None) -> tuple[str, str, str] | None:
        """Resolve a dotted reference to ``(container, module, function)``."""
        if not parts:
            return None
        if parts[0] == "self":
            cleaned = parts[1:]
            if not cleaned:
                return None
            if len(cleaned) == 1 and container not in (None, "module"):
                if cleaned[0] in self.classes.get(module, {}).get(container, {}):
                    return (container, module, cleaned[0])
            return self._resolve_qualified(module, cleaned)
        cleaned = parts
        if parts[0] in self.imports.get(module, {}):
            resolved = self._resolve_qualified(module, cleaned)
            if resolved:
                return resolved
        if len(cleaned) == 1:
            if cleaned[0] in self.functions.get(module, {}):
                return ("module", module, cleaned[0])
            imported = self.imports.get(module, {}).get(cleaned[0])
            if imported:
                target_module, attribute = imported
                if attribute is None and target_module in self.trees:
                    name = target_module.rpartition(".")[2]
                    if name in self.functions.get(target_module, {}):
                        return ("module", target_module, name)
                if attribute and attribute in self.functions.get(target_module, {}):
                    return ("module", target_module, attribute)
                if attribute:
                    for class_name, methods in self.classes.get(target_module, {}).items():
                        if attribute in methods:
                            return (class_name, target_module, attribute)
            return None
        return self._resolve_qualified(module, cleaned) or self._resolve_tail(module, cleaned[-1])

    def _resolve_qualified(self, module: str, cleaned: list[str]) -> tuple[str, str, str] | None:
        for depth in (2, 1):
            container = tuple(cleaned[:depth])
            if container in _SERVICE_CONTAINERS and len(cleaned) == depth + 1:
                class_name = _SERVICE_CONTAINERS[container]
                for owner, classes in self.classes.items():
                    if class_name in classes and cleaned[depth] in classes[class_name]:
                        return (class_name, owner, cleaned[depth])
        head = cleaned[0]
        imported = self.imports.get(module, {}).get(head)
        if imported and len(cleaned) == 2:
            target_module, attribute = imported
            if attribute:
                if cleaned[1] in self.classes.get(target_module, {}).get(attribute, {}):
                    return (attribute, target_module, cleaned[1])
                if cleaned[1] in self.functions.get(target_module, {}):
                    return ("module", target_module, cleaned[1])
            else:
                if cleaned[1] in self.functions.get(target_module, {}):
                    return ("module", target_module, cleaned[1])
                for class_name, methods in self.classes.get(target_module, {}).items():
                    if cleaned[1] in methods:
                        return (class_name, target_module, cleaned[1])
        return None

    def _resolve_tail(self, module: str, name: str) -> tuple[str, str, str] | None:
        """A local variable holding a resource (``service.store_task_wait``)."""
        for class_name, methods in self.classes.get(module, {}).items():
            if name in methods:
                return (class_name, module, name)
        if name in self.functions.get(module, {}):
            return ("module", module, name)
        return None

    def function_node(self, module: str, name: str, container: str | None) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        if container in (None, "module"):
            return self.functions.get(module, {}).get(name)
        return self.classes.get(module, {}).get(container, {}).get(name)

    def location(self, module: str, function: ast.AST) -> str:
        name = getattr(function, "name", "<lambda>")
        path = self.paths.get(module)
        return f"{module}.{name} ({path.name}:{function.lineno})" if path else f"{module}.{name}"


# ---------------------------------------------------------------------------
# Fact extraction
# ---------------------------------------------------------------------------


class _Facts:
    def __init__(self) -> None:
        self.parameters: list[Parameter] = []
        self.children: dict[str, list[Parameter]] = {}
        self.allowed: dict[str | None, set[str]] = {}
        self.reads: dict[str | None, set[str]] = {}
        self.opaque: list[str] = []
        self.locations: list[str] = []
        self.notes: list[str] = []
        self.saw_allowed = False
        #: Field paths whose caller supplied no default and whose validator rejects
        #: the absent value, so the request must carry them.
        self.required_paths: set[str] = set()
        #: Un-decoded rejection conditions, kept as readable expressions with source.
        self.raw: dict[str, tuple[str, str, bool]] = {}
        #: ``spec``-like nested owners and the parent whose allowed set also names
        #: their fields (the documented flat-or-nested spelling of ``_spec_fields``).
        self.mirror_owners: dict[str, str | None] = {}

    def required(self, field: str) -> bool:
        return any(field == path or field.startswith(f"{path}.") for path in self.required_paths)

    def merge(self, parameters: list[Parameter], parameter: Parameter) -> None:
        for existing in parameters:
            if existing.name != parameter.name:
                continue
            existing.required = existing.required or parameter.required
            if parameter.kind != "value" and existing.kind != parameter.kind:
                existing.kind = _merge_kind(existing.kind, parameter.kind)
            if not parameter.conditional:
                if parameter.default is not None:
                    existing.default = parameter.default
                if parameter.bounds:
                    existing.bounds = _merge_bounds(existing.bounds, parameter.bounds)
                existing.conditional = False
            elif parameter.bounds:
                existing.bounds = _merge_bounds(existing.bounds, parameter.bounds)
                existing.conditional = True
            if existing.default is None and parameter.default is not None:
                existing.default = parameter.default
                existing.conditional = True
            if parameter.expression and not existing.expression:
                existing.expression = parameter.expression
            if parameter.source and not existing.source:
                existing.source = parameter.source
            return
        parameters.append(parameter)

    def add_opaque(self, text: str) -> None:
        if text and text not in self.opaque:
            self.opaque.append(text)


#: Kinds whose union the reader can name when two checks describe the same field.
_KIND_RANK = {"boolean": 1, "integer": 2, "list": 3, "object": 3, "string": 4}

#: Calls that return the same value under a normalized path or canonical form.
_PATH_FUNCTIONS = frozenset({"Path", "realpath", "resolve", "absolute", "expanduser", "canonical_json"})


def _merge_kind(existing: str, incoming: str) -> str:
    if existing in ("value", "enum"):
        return incoming
    if incoming in ("value", "enum") or existing == incoming:
        return existing
    if existing in incoming.split(" or "):
        return incoming
    if incoming in existing.split(" or "):
        return existing
    # ``isinstance(x, int) and not isinstance(x, bool)`` still describes one JSON integer.
    if {existing, incoming} == {"integer", "boolean"}:
        return "integer"
    if _KIND_RANK.get(existing, 0) >= _KIND_RANK.get(incoming, 0):
        return f"{existing} or {incoming}"
    return f"{incoming} or {existing}"


def _merge_bounds(existing: str | None, incoming: str | None) -> str | None:
    if not existing:
        return incoming
    if not incoming or existing == incoming or incoming in existing:
        return existing
    if existing in incoming:
        return incoming
    return f"{existing}, {incoming}"


@dataclass
class _TrackedValue:
    """One local name resolved to the request field it holds, or to its length."""

    field: str
    unit: str | None = None
    #: Other fields the same local may hold, e.g. ``x.get("taskId") or x.get("runId")``.
    aliases: tuple[str, ...] = ()
    #: True when a leaf helper already proved the value is a scalar (string, integer,
    #: list, ...); such a local is never an object whose fields a callee validates.
    scalar: bool = False


class _Extractor:
    def __init__(self, index: _SourceIndex):
        self.index = index
        self._container: str | None = None
        self._derived: set[str] = set()
        #: False while reading a value projected out of the request: its fields are
        #: not request fields, so nothing derived from it may be followed further.
        self._trusted = True
        #: Loop/comprehension variables bound to one literal field name, e.g.
        #: ``for name in ("model", "provider", "effort")``.
        self._bindings: dict[str, str] = {}

    def collect(self, module: str, function: str, container: str | None, position: int | None = None) -> tuple[list[Parameter], list[str], list[str], list[str], list[str]]:
        facts = _Facts()
        node = self.index.function_node(module, function, container)
        if node is None:
            facts.notes.append(f"validation function not found: {module}.{function}")
            return (facts.parameters, [], facts.opaque, facts.locations, facts.notes)
        if position is None:
            position = self._params_position(node, container)
        self._walk_function(node, module, container, position, None, False, facts, set())
        return self._absorb(facts)

    # -- walk ------------------------------------------------------------
    def _walk_function(self, node, module, container, position, owner, conditional, facts, visited, required=False, bindings: dict[str, str] | None = None) -> None:
        key = (module, node.name, container or "module", position, owner, tuple(sorted((bindings or {}).items())))
        if key in visited:
            return
        visited.add(key)
        arguments = [argument.arg for argument in node.args.args]
        params_var = arguments[position] if position is not None and 0 <= position < len(arguments) else None
        facts.locations.append(self.index.location(module, node))
        if required and owner:
            # The caller passed ``params.get("owner")`` without a default into a
            # validator whose first checks reject it, so the field is not optional.
            facts.required_paths.add(owner)
        previous = self._container
        previous_derived = self._derived
        previous_trusted = self._trusted
        previous_bindings = self._bindings
        self._container = container
        self._bindings = dict(bindings or {})
        tracked, local_sets, no_default = self._tracked_objects(node, module, params_var, owner, facts)
        self._derived = self._derived_variables(node, params_var) if self._trusted else set()
        try:
            self._walk_body(node.body, module, node, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            self._inline_facts(node, module, tracked, facts, no_default, conditional)
        finally:
            self._container = previous
            self._derived = previous_derived
            self._trusted = previous_trusted
            self._bindings = previous_bindings

    def _derived_variables(self, node, params_var: str | None) -> set[str]:
        """Locals projected from the params dict (``spec_params``, ``request``, ...)."""
        names: set[str] = set()
        if params_var is None:
            return names
        for _ in range(2):
            seeds = names | {params_var}
            for child in ast.walk(node):
                if not isinstance(child, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                if child.value is None or len(targets) != 1 or not isinstance(targets[0], ast.Name):
                    continue
                if any(isinstance(inner, ast.Name) and inner.id in seeds for inner in ast.walk(child.value)):
                    names.add(targets[0].id)
        return names

    def _params_position(self, node, container) -> int | None:
        """Index of the validated-dict parameter (skipping the implicit ``self``)."""
        names = [argument.arg for argument in node.args.args]
        offset = 1 if names and names[0] in ("self", "cls") else 0
        return offset if len(names) > offset else None

    def _tracked_objects(self, node, module, params_var: str | None, owner: str | None = None, facts: _Facts | None = None) -> tuple[dict[str, _TrackedValue], dict[str, object], set[str]]:
        """Objects, lengths and no-default reads reachable from the params dict."""
        tracked: dict[str, _TrackedValue] = {}
        local_sets: dict[str, object] = {}
        no_default: set[str] = set()
        if params_var is None:
            return tracked, local_sets, no_default
        if owner is not None:
            # The validated value itself is the named object of a direct-value
            # validator such as ``_canonical_cwd(params.get("cwd"))``.
            tracked[params_var] = _TrackedValue(owner)
        assignments = [child for child in ast.walk(node) if isinstance(child, (ast.Assign, ast.AnnAssign, ast.For))]
        for _ in range(3):  # alias, length and loop chains resolve over a few passes
            for child in assignments:
                if isinstance(child, ast.For):
                    sources = (
                        list(child.iter.args)
                        if isinstance(child.iter, ast.Call) and isinstance(child.iter.func, ast.Name) and child.iter.func.id == "enumerate"
                        else [child.iter]
                    )
                    field_name = next((self._field_read(source, params_var, tracked, owner) for source in sources if self._field_read(source, params_var, tracked, owner)), None)
                    if field_name is None and owner is not None and any(isinstance(source, ast.Name) and source.id == params_var for source in sources):
                        # ``for entry in params_var``: the elements are the named object itself.
                        field_name = owner
                    if field_name:
                        elements = child.target.elts if isinstance(child.target, ast.Tuple) else [child.target]
                        for element in elements:
                            if isinstance(element, ast.Name):
                                # ``[]`` marks one element of that field's list.
                                tracked[element.id] = _TrackedValue(f"{field_name}[]")
                    continue
                if not isinstance(child, ast.Assign) or len(child.targets) != 1 or not isinstance(child.targets[0], ast.Name):
                    continue
                name = child.targets[0].id
                if name in tracked:
                    continue
                fields = self._fields_read(child.value, params_var, tracked, owner)
                if fields:
                    tracked[name] = _TrackedValue(fields[0][0], None, tuple(field for field, _scalar in fields[1:]), all(scalar for _field, scalar in fields))
                    if self._read_without_default(child.value, params_var):
                        no_default.add(name)
                    continue
                if isinstance(child.value, ast.Call) and isinstance(child.value.func, ast.Name) and child.value.func.id == "require_object" and child.value.args:
                    source = self._field_read(child.value.args[0], params_var, tracked, owner)
                    if source:
                        tracked[name] = _TrackedValue(source)
                        if self._read_without_default(child.value.args[0], params_var):
                            no_default.add(name)
                        continue
                nested = self._nested_extractor(child.value, params_var, tracked, owner)
                if nested is not None:
                    tracked[name] = _TrackedValue(nested[0])
                    if facts is not None:
                        facts.mirror_owners[nested[0]] = nested[1]
                    continue
                length = self._length_reference(child.value, tracked)
                if length is not None:
                    tracked[name] = _TrackedValue(length[0], length[1])
                    continue
                reference = self._projection_reference(child.value, tracked)
                if reference is not None:
                    tracked[name] = _TrackedValue(reference)
                    continue
                value = self.index._eval(child.value, module, local_sets)
                if isinstance(value, (set, frozenset, list, tuple)) and all(isinstance(item, str) for item in value):
                    local_sets[name] = value
        return tracked, local_sets, no_default

    def _nested_extractor(self, node, params_var: str, tracked: dict[str, _TrackedValue], owner: str | None) -> tuple[str, str | None] | None:
        """``_spec_fields(entry, nested_key="spec")`` -> ``("helpers.spec", "helpers")``."""
        if not isinstance(node, ast.Call):
            return None
        parts = _attribute_parts(node.func)
        keyword_name = _NESTED_EXTRACTORS.get(parts[-1]) if parts else None
        if keyword_name is None or not node.args:
            return None
        literal = None
        for keyword in node.keywords:
            if keyword.arg == keyword_name and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                literal = keyword.value.value
        if literal is None:
            return None
        first = node.args[0]
        if isinstance(first, ast.Name) and first.id == params_var:
            base = owner
        elif isinstance(first, ast.Name) and first.id in tracked:
            base = tracked[first.id].field
        else:
            base = None
        return (_nested(base, literal), _owner_field(base)) if base else (literal, None)

    def _length_reference(self, node: ast.AST, tracked: dict[str, _TrackedValue]) -> tuple[str, str] | None:
        """``size = len(text.encode("utf-8"))`` -> ``("input", "bytes")``."""
        subject = _length_subject(node)
        if subject is None:
            return None
        name, unit = subject
        value = tracked.get(name)
        if value is not None and value.unit is None:
            return (value.field, unit)
        return None

    def _projection_reference(self, node: ast.AST, tracked: dict[str, _TrackedValue]) -> str | None:
        """``Path(os.path.realpath(raw))`` keeps the same request field as ``raw``."""
        if not isinstance(node, ast.Call) or not node.args:
            return None
        parts = _attribute_parts(node.func) or []
        if not parts or parts[-1] not in _PATH_FUNCTIONS:
            return None
        reference = self._reference(node.args[0], tracked)
        if reference is not None and reference[1] is None:
            return reference[0]
        return self._projection_reference(node.args[0], tracked)

    @staticmethod
    def _read_without_default(node: ast.AST, params_var: str) -> bool:
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == params_var:
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("get", "pop")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == params_var
        ):
            return len(node.args) == 1 and not node.keywords
        return False

    def _fields_read(self, node: ast.AST, params_var: str, tracked: dict[str, _TrackedValue], scope: str | None = None) -> list[tuple[str, bool]]:
        """Every request field one local can hold, and whether a helper proved it scalar."""
        if isinstance(node, ast.BoolOp):
            fields: list[tuple[str, bool]] = []
            for value in node.values:
                for item in self._fields_read(value, params_var, tracked, scope):
                    if item[0] not in [field for field, _scalar in fields]:
                        fields.append(item)
            return fields
        field = self._field_read(node, params_var, tracked, scope)
        if field:
            return [(field, False)]
        field = self._validated_field(node, params_var, tracked, scope)
        return [(field, True)] if field else []

    def _validated_field(self, node, params_var: str, tracked: dict[str, _TrackedValue], scope: str | None = None) -> str | None:
        """``adapter = optional_string(params, "adapter") or "dsh"`` -> ``adapter``.

        A value validated by one of the schema helpers is still the request field, so
        the inline checks that follow it belong to that field's help entry.
        """
        if isinstance(node, ast.BoolOp):
            for value in node.values:
                field_name = self._validated_field(value, params_var, tracked, scope)
                if field_name:
                    return field_name
            return None
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            return None
        parts = _attribute_parts(node.func)
        if parts is None or parts[-1] not in _HELPERS:
            return None
        source, name = node.args[0], node.args[1]
        if not isinstance(name, ast.Constant) or not isinstance(name.value, str):
            return None
        if isinstance(source, ast.Name) and source.id == params_var:
            return _scoped_field(scope, name.value)
        if isinstance(source, ast.Name) and source.id in tracked:
            return _nested(tracked[source.id].field, name.value)
        return None

    def _literal_name(self, node: ast.AST) -> str | None:
        """A literal string, or a variable whose literal value is known here."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self._bindings.get(node.id)
        return None

    def _field_read(self, node, params_var: str, tracked: dict[str, _TrackedValue], scope: str | None = None) -> str | None:
        if isinstance(node, ast.Name) and node.id in tracked and tracked[node.id].unit is None:
            return tracked[node.id].field
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "pop"):
            base = node.func.value
            if isinstance(base, ast.Name) and node.args:
                first = self._literal_name(node.args[0])
                if first is None:
                    return None
                if base.id == params_var:
                    return _scoped_field(scope, first)
                if base.id in tracked and tracked[base.id].unit is None:
                    return _nested(tracked[base.id].field, first)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            name = self._literal_name(node.slice)
            if name is None:
                return None
            if node.value.id == params_var:
                return _scoped_field(scope, name)
            if node.value.id in tracked and tracked[node.value.id].unit is None:
                return _nested(tracked[node.value.id].field, name)
        return None

    def _walk_body(self, body, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited) -> None:
        for statement in body:
            self._walk_statement(statement, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)

    def _walk_statement(self, node, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            arguments = [argument.arg for argument in node.args.args]
            nested_params = arguments[0] if arguments else params_var
            nested_body = [node.body] if isinstance(node, ast.Lambda) else node.body
            nested_tracked, nested_sets, nested_no_default = self._tracked_objects(node, module, nested_params, owner, facts)
            self._walk_body(nested_body, module, function, nested_params, owner, conditional, facts, nested_tracked, nested_sets, nested_no_default, visited)
            return
        if isinstance(node, ast.If):
            self._walk_body(node.body, module, function, params_var, owner, True, facts, tracked, local_sets, no_default, visited)
            self._walk_body(node.orelse, module, function, params_var, owner, True, facts, tracked, local_sets, no_default, visited)
            return
        if isinstance(node, ast.Try):
            self._walk_body(node.body, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            self._walk_body(node.orelse, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            for handler in node.handlers:
                self._walk_body(handler.body, module, function, params_var, owner, True, facts, tracked, local_sets, no_default, visited)
            self._walk_body(node.finalbody, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            self._walk_loop(node, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            self._walk_body(getattr(node, "orelse", []), module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            return
        if isinstance(node, (ast.While, ast.With, ast.AsyncWith)):
            self._walk_body(node.body, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            self._walk_body(getattr(node, "orelse", []), module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            return
        if isinstance(node, ast.expr):
            self._walk_expression(node, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._walk_expression(child, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            elif isinstance(child, (ast.keyword, ast.Starred)):
                self._walk_expression(child, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                self._walk_statement(child, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)

    def _walk_loop(self, node, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited) -> None:
        """One loop, expanded once per literal element of a field-name tuple."""
        literals = self._literal_names(node.iter, module, local_sets)
        targets = node.target.elts if isinstance(node.target, ast.Tuple) else [node.target]
        names = [element.id for element in targets if isinstance(element, ast.Name)]
        if literals and len(names) == 1:
            for literal in literals:
                self._bindings[names[0]] = literal
                try:
                    self._walk_body(node.body, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
                finally:
                    self._bindings.pop(names[0], None)
            return
        self._walk_body(node.body, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)

    def _literal_names(self, node: ast.AST, module: str, local_sets: dict[str, object]) -> list[str]:
        """``("model", "provider", "effort")`` or a module constant of field names."""
        value = self.index._eval(node, module, local_sets)
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple, set, frozenset)) and all(isinstance(item, str) for item in value):
            return sorted(value)
        return []

    def _walk_expression(self, node, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited) -> None:
        if params_var is None:
            return
        if isinstance(node, ast.IfExp):
            # Both arms of a conditional expression are branch-specific.
            self._walk_expression(node.test, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            self._walk_expression(node.body, module, function, params_var, owner, True, facts, tracked, local_sets, no_default, visited)
            self._walk_expression(node.orelse, module, function, params_var, owner, True, facts, tracked, local_sets, no_default, visited)
            return
        if isinstance(node, (ast.DictComp, ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            self._walk_comprehension(node, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            return
        if isinstance(node, ast.Call):
            self._handle_call(node, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            self._walk_statement(node, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.expr, ast.keyword, ast.Starred)):
                self._walk_expression(child, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                self._walk_statement(child, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)

    def _walk_comprehension(self, node, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited) -> None:
        """``{key: required_string(value, key, ...) for key in FIELDS}``, expanded per field."""
        elements = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        generators = list(node.generators)
        first = generators[0] if generators else None
        targets = first.target.elts if first is not None and isinstance(first.target, ast.Tuple) else [first.target] if first is not None else []
        names = [element.id for element in targets if isinstance(element, ast.Name)]
        literals = self._literal_names(first.iter, module, local_sets) if first is not None else []
        if literals and len(names) == 1:
            for literal in literals:
                self._bindings[names[0]] = literal
                try:
                    for element in elements:
                        self._walk_expression(element, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
                finally:
                    self._bindings.pop(names[0], None)
            for condition in first.ifs if first is not None else []:
                self._walk_expression(condition, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)
            return
        for element in elements:
            self._walk_expression(element, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited)

    # -- one call --------------------------------------------------------
    def _handle_call(self, node, module, function, params_var, owner, conditional, facts, tracked, local_sets, no_default, visited) -> None:
        parts = _attribute_parts(node.func)
        if parts is None:
            return
        helper = parts[-1]
        first = node.args[0] if node.args else None
        if (
            len(parts) <= 2
            and helper in _HELPERS
            and isinstance(first, ast.Name)
            and (first.id == params_var or first.id in tracked)
        ):
            target_owner = owner if first.id == params_var else tracked[first.id].field
            self._record_helper(node, parts, module, function, target_owner, conditional, facts, local_sets)
            return
        self._record_reads(node, params_var, owner, facts)
        if helper in _HELPERS:
            # A leaf helper whose field name was not a literal: never walk its body,
            # which itself reads a dynamically named field.
            return
        if parts[-1] in ("_guard", "_bounded", "_bounded_wait") and node.args:
            handler = node.args[-1]
            handler_parts = _attribute_parts(handler) if isinstance(handler, ast.Attribute) else None
            if handler_parts is not None:
                self._walk_reference(handler_parts, module, conditional, facts, visited)
                return
        self._follow_call(node, parts, module, params_var, owner, conditional, facts, visited, tracked, no_default)

    def _walk_reference(self, parts, module, conditional, facts, visited) -> None:
        """A resource handler passed by reference, e.g. ``self.store.task_get``."""
        resolved = self.index.resolve_function(module, parts, self._container)
        if resolved is None:
            return
        container, target_module, target_name = resolved
        target = self.index.function_node(target_module, target_name, container)
        if target is None:
            return
        position = self._params_position(target, container)
        self._walk_function(target, target_module, container, position, None, conditional, facts, visited)

    def _record_helper(self, node, parts, module, function, owner, conditional, facts, local_sets) -> None:
        source = self.index.location(module, function)
        owner = _owner_field(owner) if owner else None
        parameters = facts.parameters if owner is None else facts.children.setdefault(owner, [])
        helper = parts[-1]
        if helper == "reject_unknown":
            if len(node.args) > 1:
                values, unknown = self.index._string_set(node.args[1], module, local_sets)
                facts.allowed.setdefault(owner, set()).update(values)
                for text in unknown:
                    facts.add_opaque(f"allowed fields are computed at runtime from {text}")
                for value in sorted(values):
                    facts.merge(parameters, Parameter(value, "value", False, None, None, conditional, [], source))
                for value in sorted(values):
                    facts.reads.setdefault(owner, set()).discard(value)
                if owner is None:
                    facts.saw_allowed = True
            return
        name = self._name_argument(node, 1)
        if name is None:
            facts.add_opaque(f"a dynamically named {helper} field at {source}")
            return
        facts.reads.setdefault(owner, set()).discard(name)
        kind, required, default, bounds = "value", helper == "required_string", None, None
        if helper in ("optional_string", "required_string"):
            kind = "string"
            parts_ = []
            maximum = _keyword(node, "max_length")
            if maximum is None:
                declared = self._signature_default(parts, module, "max_length")
                if isinstance(declared, str):
                    parts_.append(f"≤{declared} characters")
                elif isinstance(declared, ast.AST):
                    parts_.append(f"≤{self._render(declared, self._signature_module(parts, module))} characters")
            else:
                parts_.append(f"≤{self._render(maximum, module)} characters")
            pattern = _keyword(node, "pattern")
            literal = _pattern_literal(pattern)
            if literal is not None:
                parts_.append(f"matches {literal}")
            bounds = "; ".join(parts_) or None
        elif helper == "optional_bool":
            kind, default = "boolean", self._render_argument(node, 2, module)
        elif helper == "optional_int":
            kind = "integer"
            default = self._render_argument(node, 2, module)
            minimum, maximum = self._render_argument(node, 3, module), self._render_argument(node, 4, module)
            bounds = f"{minimum}–{maximum}" if minimum is not None and maximum is not None else None
        elif helper == "optional_positive_int":
            kind, bounds = "integer", "≥1"
        elif helper == "optional_sha256":
            kind, bounds = "string", "lowercase sha256 hex digest"
        elif helper == "string_list":
            kind = "list"
            limit = _keyword(node, "limit")
            if limit is None:
                limit = self._signature_default(parts, module, "limit")
            if limit is not None:
                bounds = f"at most {self._render(limit, self._signature_module(parts, module))} entries"
        elif helper == "bounded_text":
            kind = "string"
            maximum = _keyword(node, "max_bytes")
            bounds = f"≤{self._render(maximum, module)} UTF-8 bytes" if maximum is not None else None
            if _keyword_value(node, "allow_empty") is True:
                bounds = f"{bounds}; may be empty" if bounds else "may be empty"
        if required and conditional and owner is None:
            # ``if "title" in params: required_string(params, "title")`` means required
            # when supplied, not that the whole request must carry it.
            required = False
        facts.merge(parameters, Parameter(name, kind, required, default, bounds, conditional, [], source))

    def _name_argument(self, node: ast.Call, index: int) -> str | None:
        """A literal field name, or the loop/comprehension variable bound to one."""
        name = _string_argument(node, index)
        if name is None and len(node.args) > index and isinstance(node.args[index], ast.Name):
            name = self._bindings.get(node.args[index].id)
        return name

    def _signature_module(self, parts: list[str], module: str) -> str:
        resolved = self.index.resolve_function(module, parts, self._container)
        return resolved[1] if resolved else module

    def _signature_default(self, parts: list[str], module: str, parameter: str):
        """The declared default of a leaf helper argument (``max_length=MAX_METADATA``)."""
        resolved = self.index.resolve_function(module, parts, self._container)
        if resolved is None:
            return None
        container, target_module, target_name = resolved
        target = self.index.function_node(target_module, target_name, container)
        if target is None:
            return None
        arguments = [argument.arg for argument in target.args.args]
        defaults = list(target.args.defaults)
        offset = len(arguments) - len(defaults)
        for index, name in enumerate(arguments):
            if name == parameter and index >= offset and defaults:
                node = defaults[index - offset]
                value = self.index._eval(node, target_module)
                return str(value) if value is not _UNRESOLVED else node
        for name, default in zip(target.args.kwonlyargs, target.args.kw_defaults):
            if name.arg == parameter and default is not None:
                value = self.index._eval(default, target_module)
                return str(value) if value is not _UNRESOLVED else default
        return None

    def _inline_facts(self, node, module, tracked, facts, no_default, conditional: bool = False) -> None:
        """Bounds enforced by a rejection ``if`` over a value read from the params."""
        if not tracked:
            return
        source = self.index.location(module, node)
        self._visit_tests(node.body, module, tracked, facts, source, conditional, _none_guards(node), no_default)

    def _visit_tests(self, statements, module, tracked, facts, source, conditional: bool, optional: set[str], no_default: set[str], context: str = "") -> None:
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(statement, (ast.If, ast.While)):
                self._visit_condition(statement, module, tracked, facts, source, conditional, optional, no_default, context)
                self._visit_tests(statement.body, module, tracked, facts, source, True, optional, no_default, context)
                branch = "" if isinstance(statement, ast.While) else _branch_context(context, statement.test)
                self._visit_tests(getattr(statement, "orelse", []), module, tracked, facts, source, True, optional, no_default, branch)
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.With, ast.AsyncWith, ast.Try)):
                self._visit_tests(statement.body, module, tracked, facts, source, conditional, optional, no_default, context)
                self._visit_tests(getattr(statement, "orelse", []), module, tracked, facts, source, conditional, optional, no_default, context)
                self._visit_tests(getattr(statement, "finalbody", []), module, tracked, facts, source, conditional, optional, no_default, context)
                for handler in getattr(statement, "handlers", []):
                    self._visit_tests(handler.body, module, tracked, facts, source, True, optional, no_default, context)

    def _visit_condition(self, statement, module, tracked, facts, source, conditional, optional, no_default, context: str = "") -> None:
        if _raises(statement.body):
            self._rejection(statement, module, tracked, facts, source, conditional, optional, no_default, context)
        elif _accepted_chain(statement):
            # ``if isinstance(value, str): ... elif isinstance(value, dict): ... else: raise``
            # accepts exactly the union of the positive branches.
            accepted = self._accepted_types(statement, module, tracked)
            group = sorted({name for field, _kind, _local in accepted for name in _expand_fields(field, tracked)})
            for field, kind, local in accepted:
                required = local in no_default and local not in optional and len(group) == 1
                for name in _expand_fields(field, tracked):
                    self._add_inline(facts, name, kind, None, source, conditional, required)
            if len(group) > 1:
                for field in group:
                    facts.raw.setdefault(field, (f"one of {', '.join(group)} is required", source, False))
        elif any(isinstance(item, ast.Return) for item in statement.body):
            # ``if value == SENTINEL: return SENTINEL`` accepts one extra value.
            for part, negated in _test_parts(statement.test):
                if negated or not isinstance(part, ast.Compare):
                    continue
                for field, kind, bounds, local in self._compare_facts(part, module, tracked, rejected=False):
                    if kind == "integer" and bounds and bounds.startswith("exactly "):
                        self._add_inline(facts, field, kind, f"can also be {bounds.split(' ', 1)[1]}", source, True, False)

    def _rejection(self, statement, module, tracked, facts, source, conditional, optional, no_default, context: str = "") -> None:
        parts = _test_parts(statement.test)
        optional = set(optional)
        for part, negated in parts:
            # ``elif raw is not None: raise`` accepts absence: this field is optional.
            if not negated and isinstance(part, ast.Compare) and len(part.ops) == 1 and isinstance(part.ops[0], ast.IsNot):
                for side in (part.left, part.comparators[0]):
                    if isinstance(side, ast.Name) and side.id in tracked:
                        optional.add(side.id)
        if len(parts) > 1:
            # Presence required only as part of a larger condition: keep the readable
            # condition next to the derived "required" so the context is never lost.
            for part, negated in parts:
                if negated or not isinstance(part, ast.Compare) or len(part.ops) != 1 or not isinstance(part.ops[0], ast.Is):
                    continue
                side = part.left if _is_none(part.comparators[0]) else (part.comparators[0] if _is_none(part.left) else None)
                if isinstance(side, ast.Name) and side.id in tracked:
                    facts.raw.setdefault(tracked[side.id].field, ("rejected when " + _clip(_expression_text(statement.test)), source, False))
        for part, negated in parts:
            if (
                not negated
                and isinstance(part, ast.Compare)
                and len(part.ops) == 1
                and isinstance(part.ops[0], ast.NotIn)
                and isinstance(part.left, ast.Constant)
                and isinstance(part.left.value, str)
            ):
                # ``if "path" not in params: raise`` requires the field outright.
                field = part.left.value
                facts.required_paths.add(field)
                self._add_inline(facts, field, "value", None, source, conditional, True)
        decoded: set[str] = set()
        for part, negated in parts:
            decoded |= {name.replace("[]", "") for name in self._part_facts(part, module, tracked, facts, source, negated, conditional, optional, no_default, len(parts) == 1)}
        expression = _clip(f"{context} and {_expression_text(statement.test)}" if context else _expression_text(statement.test))
        for part, negated in parts:
            for field in sorted(_subject_fields(part, tracked) - decoded):
                local = _local_name(part, tracked, field)
                required = local in no_default and local not in optional and not conditional
                facts.raw.setdefault(field, ("rejected when " + expression, source, required))

    def _part_facts(self, part, module, tracked, facts, source, negated, conditional, optional, no_default, standalone: bool = True) -> set[str]:
        """The facts one rejection-condition part implies for accepted input."""
        decoded: set[str] = set()
        if isinstance(part, ast.Compare):
            for field, kind, bounds, local in self._compare_facts(part, module, tracked, rejected=not negated):
                required = local in no_default and local not in optional and not conditional
                for name in _expand_fields(field, tracked):
                    if bounds == "required":
                        if standalone:
                            facts.required_paths.add(name)
                        self._add_inline(facts, name, kind, None, source, conditional or not standalone, standalone)
                    else:
                        self._add_inline(facts, name, kind, bounds, source, conditional, required)
                    decoded.add(name)
            return decoded
        if isinstance(part, ast.Call) and isinstance(part.func, ast.Name) and part.func.id == "isinstance" and part.args:
            kind = _ISINSTANCE_KINDS.get(getattr(part.args[1], "id", "") if len(part.args) > 1 else "")
            reference = self._reference(part.args[0], tracked)
            if kind and reference is not None and reference[1] is None and negated:
                field, _unit, local = reference
                required = (local in no_default and local not in optional and not conditional) or (not conditional and facts.required(field))
                for name in _expand_fields(field, tracked):
                    self._add_inline(facts, name, kind, None, source, conditional, required)
                    decoded.add(name)
            return decoded
        if isinstance(part, ast.Call) and isinstance(part.func, ast.Attribute):
            reference = self._reference(part.func.value, tracked)
            if reference is not None and reference[1] is None:
                field, _unit, local = reference
                required = (local in no_default and local not in optional and not conditional) or (not conditional and facts.required(field))
                method = part.func.attr
                if method == "strip":
                    for name in _expand_fields(field, tracked):
                        self._add_inline(facts, name, "string", "nonempty", source, conditional, required)
                        decoded.add(name)
                elif method == "is_dir":
                    for name in _expand_fields(field, tracked):
                        self._add_inline(facts, name, "string", "must be an existing directory", source, conditional, required)
                        decoded.add(name)
                elif method == "startswith" and part.args:
                    literal = part.args[0]
                    if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                        text = f'starts with "{literal.value}"' if negated else f'must not start with "{literal.value}"'
                        for name in _expand_fields(field, tracked):
                            self._add_inline(facts, name, "string", text, source, conditional, required)
                            decoded.add(name)
            return decoded
        reference = self._reference(part, tracked)
        if reference is not None and reference[1] is None and standalone:
            # ``if not raw: raise`` rejects an empty value; a bare truthiness test that
            # is only one half of a compound condition keeps its readable expression.
            field, _unit, local = reference
            required = (local in no_default and local not in optional and not conditional) or (not conditional and facts.required(field))
            text = "must not be empty" if negated else "must be empty"
            for name in _expand_fields(field, tracked):
                self._add_inline(facts, name, "value", text, source, conditional, required)
                decoded.add(name)
        return decoded

    def _accepted_types(self, statement, module, tracked) -> list[tuple[str, str, str]]:
        found: list[tuple[str, str, str]] = []
        node = statement
        while isinstance(node, ast.If):
            for part, negated in _test_parts(node.test):
                if negated or not (isinstance(part, ast.Call) and isinstance(part.func, ast.Name) and part.func.id == "isinstance" and part.args):
                    continue
                kind = _ISINSTANCE_KINDS.get(getattr(part.args[1], "id", "") if len(part.args) > 1 else "")
                reference = self._reference(part.args[0], tracked)
                if kind and reference is not None and reference[1] is None:
                    found.append((reference[0], kind, reference[2]))
            if _raises(node.orelse):
                return found
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                node = node.orelse[0]
                continue
            return []
        return []

    def _reference(self, node: ast.AST, tracked: dict[str, _TrackedValue]) -> tuple[str, str | None, str] | None:
        """``(field, unit, local)`` for a tracked value or its measured length."""
        if isinstance(node, ast.Name) and node.id in tracked:
            value = tracked[node.id]
            return (value.field, value.unit, node.id)
        length = self._length_reference(node, tracked)
        if length is not None:
            return (length[0], length[1], _length_subject(node)[0])
        return None

    def _compare_facts(self, node: ast.Compare, module: str, tracked: dict[str, _TrackedValue], rejected: bool = True) -> list[tuple[str, str, str | None, str]]:
        """Every comparison, with the bound it implies for an accepted request."""
        facts: list[tuple[str, str, str | None, str]] = []
        pairs = [(node.ops[0], node.left, node.comparators[0])]
        for index in range(1, len(node.ops)):
            pairs.append((node.ops[index], node.comparators[index - 1], node.comparators[index]))
        for op, left, right in pairs:
            fact = self._compare_fact(op, left, right, module, tracked, rejected)
            if fact is not None:
                facts.append(fact)
        return facts

    def _compare_fact(self, op, left, right, module: str, tracked: dict[str, _TrackedValue], rejected: bool):
        reference = self._reference(left, tracked)
        if reference is None:
            reference = self._reference(right, tracked)
            if reference is not None:
                inverted = {ast.Lt: ast.Gt, ast.LtE: ast.GtE, ast.Gt: ast.Lt, ast.GtE: ast.LtE}.get(type(op))
                if inverted is None:
                    return None
                op, left, right = inverted(), right, left
            else:
                return None
        field, unit, local = reference
        if isinstance(op, (ast.Is, ast.IsNot)) and _is_none(right):
            # ``if value is None: raise`` requires the field; ``if raw is not None: raise``
            # only scopes a branch, so it is not a bound on the value.
            if isinstance(op, ast.Is) and rejected:
                return (field, "value", "required", local)
            return None
        if isinstance(op, (ast.In, ast.NotIn)):
            values, _unknown = self.index._string_set(right, module, {})
            if values:
                text = ", ".join(sorted(values))
                if isinstance(op, ast.NotIn):
                    return (field, "enum", f"one of: {text}", local)
                return (field, "enum", f"must not be one of: {text}", local)
            return None
        if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(right, ast.Constant) and isinstance(right.value, str):
            if isinstance(op, ast.NotEq):
                return (field, "enum", f'must be "{right.value}"', local)
            return (field, "enum", f'must not be "{right.value}"', local)
        limit = self.index._eval(right, module)
        if isinstance(limit, int) and not isinstance(limit, bool):
            if unit is not None:
                return self._length_bound(field, unit, local, op, limit, rejected)
            if isinstance(op, (ast.Eq, ast.NotEq)):
                text = f"exactly {limit}" if (isinstance(op, ast.Eq) == rejected) else f"must not be exactly {limit}"
                return (field, "integer", text, local)
            if rejected:
                return (field, "integer", {ast.Lt: "≥", ast.LtE: ">", ast.Gt: "≤", ast.GtE: "<"}.get(type(op), "?") + f" {limit}", local)
            return (field, "integer", {ast.Lt: "<", ast.LtE: "≤", ast.Gt: ">", ast.GtE: "≥"}.get(type(op), "?") + f" {limit}", local)
        return None

    @staticmethod
    def _length_bound(field: str, unit: str, local: str, op, limit: int, rejected: bool):
        suffix = " UTF-8 bytes" if unit == "bytes" else " characters"
        if isinstance(op, (ast.Gt, ast.GtE)):
            if rejected:
                accepted = limit if isinstance(op, ast.Gt) else limit - 1
                text = f"at most {accepted}{suffix}"
            else:
                text = f"more than {limit}{suffix}" if isinstance(op, ast.Gt) else f"at least {limit}{suffix}"
        elif isinstance(op, (ast.Lt, ast.LtE)):
            if rejected:
                accepted = limit if isinstance(op, ast.Lt) else limit + 1
                text = f"at least {accepted}{suffix}"
            else:
                text = f"fewer than {limit}{suffix}" if isinstance(op, ast.Lt) else f"at most {limit}{suffix}"
        elif isinstance(op, (ast.Eq, ast.NotEq)):
            if isinstance(op, ast.Eq) == rejected:
                text = "nonempty" if limit == 0 else f"must not be exactly {limit}{suffix}"
            else:
                text = f"exactly {limit}{suffix}" if limit else "nonempty"
        else:
            return None
        return (field, "value", text, local)

    def _render(self, node: ast.AST | None, module: str) -> str:
        value = self.index._eval(node, module)
        if value is not _UNRESOLVED:
            return str(value)
        return _render_value(node)

    def _render_argument(self, node: ast.Call, index: int, module: str) -> str | None:
        return self._render(node.args[index], module) if len(node.args) > index else None

    def _add_inline(self, facts: _Facts, field: str, kind: str, bounds: str | None, source: str, conditional: bool = False, required: bool = False) -> None:
        if field.endswith("[]"):
            # An element-level check (``isinstance(entry, dict)``, ``len(entry)``)
            # describes the list's shape, not one request field inside it.
            return
        if field.endswith("[]"):
            parameters = facts.children.setdefault(_owner_field(field), [])
            field = field.split(".")[-1][:-2]
        elif "." in field:
            parameters = facts.children.setdefault(field.rpartition(".")[0], [])
            field = field.rpartition(".")[2]
        else:
            parameters = facts.parameters
        if conditional:
            # A condition that only holds on one branch never makes a global "required".
            required = False
        else:
            required = required or facts.required(field)
        facts.merge(parameters, Parameter(field, kind, required, None, bounds, conditional, [], source))

    def _record_reads(self, node, params_var, owner, facts) -> None:
        reads = facts.reads.setdefault(owner, set())
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == params_var:
            if node.args:
                name = self._literal_name(node.args[0])
                if name is not None:
                    reads.add(name)
        for child in ast.walk(node):
            if isinstance(child, ast.Subscript) and isinstance(child.value, ast.Name) and child.value.id == params_var:
                if isinstance(child.slice, ast.Constant) and isinstance(child.slice.value, str):
                    reads.add(child.slice.value)
            if (
                isinstance(child, ast.Compare)
                and len(child.ops) == 1
                and isinstance(child.ops[0], ast.In)
                and isinstance(child.left, ast.Constant)
                and isinstance(child.left.value, str)
                and any(isinstance(comparator, ast.Name) and comparator.id == params_var for comparator in child.comparators)
            ):
                reads.add(child.left.value)

    def _follow_call(self, node, parts, module, params_var, owner, conditional, facts, visited, tracked, no_default) -> None:
        resolved = self.index.resolve_function(module, parts, self._container)
        if resolved is None:
            return
        container, target_module, target_name = resolved
        target = self.index.function_node(target_module, target_name, container)
        if target is None:
            return
        found = self._argument_position(target, node, container, params_var, tracked, no_default, owner)
        if found is None:
            return
        position, target_owner, trusted, required = found
        if target_name in _HELPER_NOTES:
            for note in _HELPER_NOTES[target_name]:
                if note not in facts.notes:
                    facts.notes.append(note)
        bindings: dict[str, str] = {}
        arguments = [argument.arg for argument in target.args.args]
        offset = 1 if arguments and arguments[0] in ("self", "cls") else 0
        for index, argument in enumerate(node.args):
            literal = _string_argument(node, index)
            if literal is not None and index + offset < len(arguments):
                bindings[arguments[index + offset]] = literal
        previous = self._trusted
        self._trusted = trusted
        try:
            self._walk_function(target, target_module, container, position, target_owner, conditional, facts, visited, required, bindings)
        finally:
            self._trusted = previous

    def _argument_position(self, target, node, container, params_var, tracked, no_default, scope: str | None = None) -> tuple[int, str | None, bool, bool] | None:
        """``(position, owner, trusted, required)`` for the validated-dict argument."""
        names = [argument.arg for argument in target.args.args]
        offset = 1 if names and names[0] in ("self", "cls") else 0
        for index, argument in enumerate(node.args):
            owner: str | None = None
            trusted = True
            required = False
            if isinstance(argument, ast.Name):
                if argument.id == params_var:
                    # The validated dict itself: a caller scoping it to a sub-object
                    # passes that scope on (``normalize_workflow_spec(spec_params)``).
                    owner = scope
                elif argument.id in tracked and tracked[argument.id].unit is None and not tracked[argument.id].scalar:
                    # An object-valued read (``_spec_fields`` result, ``require_object``
                    # result) scopes a callee to that sub-object; a scalar helper result
                    # (``run_id``) does not.
                    owner = tracked[argument.id].field
                    required = argument.id in no_default
                else:
                    # A local projected out of the params dict is a value, not the
                    # validated dict a callee expects.
                    continue
            elif (
                isinstance(argument, ast.Subscript)
                and isinstance(argument.value, ast.Name)
                and argument.value.id == params_var
                and isinstance(argument.slice, ast.Constant)
                and isinstance(argument.slice.value, str)
            ):
                owner = _scoped_field(scope, argument.slice.value)
                required = True
            elif (
                isinstance(argument, ast.Call)
                and isinstance(argument.func, ast.Attribute)
                and isinstance(argument.func.value, ast.Name)
                and argument.args
                and isinstance(argument.args[0], ast.Constant)
                and isinstance(argument.args[0].value, str)
            ):
                # ``entry.get("match")`` scopes the callee to that sub-object.
                base = argument.func.value
                if base.id == params_var and argument.func.attr in ("get", "pop"):
                    owner = _scoped_field(scope, argument.args[0].value)
                    required = len(argument.args) == 1 and not argument.keywords
                elif base.id in tracked and tracked[base.id].unit is None:
                    owner = _nested(tracked[base.id].field, argument.args[0].value)
            else:
                continue
            candidate = index + offset
            if 0 <= candidate < len(names):
                return (candidate, owner, trusted, required)
        return None

    # -- assembling ------------------------------------------------------
    def _absorb(self, facts: _Facts) -> tuple[list[Parameter], list[str], list[str], list[str], list[str]]:
        nested = [parameter for parameter in facts.parameters if "." in parameter.name]
        if nested:
            facts.parameters[:] = [parameter for parameter in facts.parameters if "." not in parameter.name]
            for parameter in nested:
                self._attach_parameter(facts.parameters, parameter.name, parameter)
        children = {owner: self._finalize(list(items)) for owner, items in facts.children.items()}
        self._mirror_flat_spellings(facts, children)
        for owner in sorted(children):
            self._attach(facts.parameters, owner, children[owner])
        inline: list[str] = []
        for owner in sorted(children):
            allowed = facts.allowed.pop(owner, set())
            known = {item.name for item in children[owner]}
            inline.extend(f"{owner}.{name}" for name in sorted(allowed - known))
        allowed_top = facts.allowed.pop(None, set())
        known_top = {item.name for item in facts.parameters}
        inline = sorted(allowed_top - known_top) + inline
        for name in sorted(facts.reads.pop(None, set()) - known_top - allowed_top):
            if facts.saw_allowed:
                # The operation rejects unknown fields, so this read is not a request field.
                continue
            facts.parameters.append(Parameter(name, "value", False, None, None, False, [], ""))
        self._normalize_units(facts.parameters)
        self._apply_raw(facts)
        if not facts.parameters and not inline and not facts.opaque:
            facts.notes.append("takes no parameters" if facts.saw_allowed else "no top-level parameter validation was found for this method")
        self._sort(facts.parameters)
        locations = list(dict.fromkeys(facts.locations))
        return (facts.parameters, inline, facts.opaque, locations, facts.notes)

    def _mirror_flat_spellings(self, facts: _Facts, children: dict[str, list[Parameter]]) -> None:
        """``_spec_fields`` accepts one field both flat and inside its nested object."""
        for nested, parent in sorted(facts.mirror_owners.items()):
            items = facts.children.get(nested)
            if not items:
                continue
            allowed = facts.allowed.get(parent) or set()
            if parent is None:
                target = facts.parameters
            else:
                target = children.setdefault(parent, [])
            for item in items:
                if item.name in allowed and not item.name.endswith("[]"):
                    facts.merge(target, _copy_parameter(item))
            if parent is None:
                self._sort(target)

    def _apply_raw(self, facts: _Facts, prefix: str = "", parameters: list[Parameter] | None = None) -> None:
        """Attach a readable condition where no concrete bound was extracted."""
        for parameter in parameters if parameters is not None else facts.parameters:
            path = f"{prefix}{parameter.name}"
            entry = facts.raw.get(path)
            if entry is not None and parameter.expression is None:
                expression, source, required = entry
                parameter.expression = expression
                parameter.source = parameter.source or source
                parameter.required = parameter.required or required
            self._apply_raw(facts, f"{path}.", parameter.children)

    def _normalize_units(self, parameters: list[Parameter]) -> None:
        """A list bound measured by ``len()`` counts entries, not characters."""
        for parameter in parameters:
            if parameter.kind == "list" and parameter.bounds:
                parameter.bounds = parameter.bounds.replace(" characters", " entries")
            self._normalize_units(parameter.children)

    def _attach_parameter(self, parameters: list[Parameter], path: str, parameter: Parameter) -> None:
        head, _, rest = path.partition(".")
        for existing in parameters:
            if existing.name == head:
                break
        else:
            existing = Parameter(head, "object", False, None, None, False, [], "")
            parameters.append(existing)
        if rest:
            self._attach_parameter(existing.children, rest, parameter)
            if existing.kind == "value":
                existing.kind = "object"
        else:
            parameter.name = head
            self._finalize(existing.children)
            facts_children = [child for child in existing.children if child.name != head]
            facts_children.append(parameter)
            existing.children = facts_children
            if existing.kind == "value":
                existing.kind = "object"

    def _attach(self, parameters: list[Parameter], path: str, items: list[Parameter]) -> None:
        head, _, rest = path.partition(".")
        for parameter in parameters:
            if parameter.name == head:
                break
        else:
            parameter = Parameter(head, "object", False, None, None, False, [], "")
            parameters.append(parameter)
        if rest:
            self._attach(parameter.children, rest, items)
            if parameter.kind == "value":
                parameter.kind = "object"
        else:
            parameter.children = items
            if parameter.kind == "value":
                parameter.kind = "object"

    def _finalize(self, items: list[Parameter]) -> list[Parameter]:
        self._sort(items)
        return items

    def _sort(self, items: list[Parameter]) -> None:
        items.sort(key=lambda parameter: (not parameter.required, parameter.name))


def _copy_parameter(parameter: Parameter) -> Parameter:
    """An independent copy so a mirrored spelling has its own children."""
    return copy.deepcopy(parameter)


def _scoped_field(owner: str | None, field: str) -> str:
    """One field read out of the validated dict, scoped to the object being walked."""
    if not owner or field == owner or field.startswith(f"{owner}."):
        return field
    return _nested(owner, field)


def _nested(parent: str, child: str) -> str:
    """One field inside an object field; ``helpers[]`` means an element of ``helpers``."""
    return f"{parent[:-2] if parent.endswith('[]') else parent}.{child}"


def _owner_field(field: str) -> str:
    """The object whose fields a tracked value belongs to."""
    return field[:-2] if field.endswith("[]") else field


def _branch_context(context: str, test: ast.AST) -> str:
    """The condition of an ``else`` branch, kept so a nested raw bound stays readable."""
    addition = _clip(f"not ({_expression_text(test)})", 90)
    return f"{context} and {addition}" if context else addition


def _raises(body: list[ast.stmt]) -> bool:
    """Whether a rejection block raises directly, without a further condition."""
    return any(isinstance(statement, ast.Raise) for statement in body)


def _accepted_chain(statement: ast.If) -> bool:
    """Whether an if/elif chain ends in ``else: raise`` (the accepted-value form)."""
    node: ast.stmt = statement
    while isinstance(node, ast.If):
        if _raises(node.orelse):
            return True
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            node = node.orelse[0]
            continue
        return False
    return False


def _test_parts(test: ast.AST, negated: bool = False) -> list[tuple[ast.AST, bool]]:
    """Split ``a or b`` and ``not a`` into conditions and whether each is negated.

    The block itself is the rejection condition (its body raises), so a part that
    is not negated describes invalid values and a negated part describes valid ones.
    """
    if isinstance(test, ast.BoolOp):
        parts: list[tuple[ast.AST, bool]] = []
        for value in test.values:
            parts.extend(_test_parts(value, negated))
        return parts
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _test_parts(test.operand, not negated)
    return [(test, negated)]


def _expand_fields(field: str, tracked: dict[str, _TrackedValue]) -> list[str]:
    """A field plus the other fields the same local may hold."""
    for value in tracked.values():
        if value.unit is None and value.field == field and value.aliases:
            return [field, *value.aliases]
    return [field]


def _subject_names(node: ast.AST, tracked: dict[str, _TrackedValue]) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in tracked:
            value = tracked[child.id]
            names.add(value.field)
            names.update(value.aliases)
    return names


def _subject_fields(node: ast.AST, tracked: dict[str, _TrackedValue]) -> set[str]:
    """Every request field one condition reads, including nested reads."""
    fields = {value.replace("[]", "") for value in _subject_names(node, tracked)}
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr in ("get", "pop")
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id in tracked
            and tracked[child.func.value.id].unit is None
            and child.args
            and isinstance(child.args[0], ast.Constant)
            and isinstance(child.args[0].value, str)
        ):
            fields.add(_nested(tracked[child.func.value.id].field, child.args[0].value).replace("[]", ""))
        if (
            isinstance(child, ast.Subscript)
            and isinstance(child.value, ast.Name)
            and child.value.id in tracked
            and tracked[child.value.id].unit is None
            and isinstance(child.slice, ast.Constant)
            and isinstance(child.slice.value, str)
        ):
            fields.add(_nested(tracked[child.value.id].field, child.slice.value).replace("[]", ""))
    return fields


def _local_name(node: ast.AST, tracked: dict[str, _TrackedValue], field: str) -> str | None:
    for child in ast.walk(node):
        if not (isinstance(child, ast.Name) and child.id in tracked):
            continue
        value = tracked[child.id]
        if value.field.replace("[]", "") == field or field in [alias.replace("[]", "") for alias in value.aliases]:
            return child.id
    return None


def _none_guards(node: ast.AST) -> set[str]:
    """Locals explicitly treated as optional: ``if value is None: return None``."""
    optional: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.If) or _raises(child.body):
            continue
        test = child.test
        if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Is)):
            continue
        name = None
        for side, other in ((test.left, test.comparators[0]), (test.comparators[0], test.left)):
            if isinstance(side, ast.Name) and _is_none(other):
                name = side.id
        if name and any(isinstance(item, (ast.Return, ast.Continue, ast.Break)) for item in child.body):
            optional.add(name)
    return optional


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _clip(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _length_subject(node: ast.AST) -> tuple[str, str] | None:
    """``len(task_text.encode("utf-8"))`` -> ``("task_text", "bytes")``."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len" and node.args):
        return None
    inner = node.args[0]
    if isinstance(inner, ast.Name):
        return (inner.id, "characters")
    if (
        isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Attribute)
        and inner.func.attr == "encode"
        and isinstance(inner.func.value, ast.Name)
    ):
        return (inner.func.value.id, "bytes")
    return None


_ISINSTANCE_KINDS = {"str": "string", "bool": "boolean", "int": "integer", "list": "list", "dict": "object"}


def _string_argument(node: ast.Call, index: int) -> str | None:
    if len(node.args) > index and isinstance(node.args[index], ast.Constant) and isinstance(node.args[index].value, str):
        return node.args[index].value
    return None


def _keyword(node: ast.Call, name: str) -> ast.AST | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _keyword_value(node: ast.Call, name: str):
    value = _keyword(node, name)
    return value.value if isinstance(value, ast.Constant) else None


def _render_value(node: ast.AST | None) -> str:
    if node is None:
        return "?"
    try:
        return str(ast.literal_eval(node))
    except (ValueError, SyntaxError):
        return _expression_text(node)


def _pattern_literal(node: ast.AST | None) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    parts = _attribute_parts(node.func) or []
    if parts and parts[-1] == "compile" and node.args:
        literal = node.args[0]
        if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
            return f"/{literal.value}/"
    return None


_INDEX: _SourceIndex | None = None


def _index() -> _SourceIndex:
    global _INDEX
    if _INDEX is None:
        _INDEX = _SourceIndex(PACKAGE_ROOT)
    return _INDEX


def reset_index() -> None:
    """Drop the parsed-source cache so the next ``help`` re-reads the package.

    Help is derived from source, so a changed validator (or a test pointing
    :data:`PACKAGE_ROOT` at a modified copy) must be reflected without restarting
    the process. Nothing is executed either way: only the files are re-parsed.
    """
    global _INDEX
    _INDEX = None


# ---------------------------------------------------------------------------
# Method resolution and rendering
# ---------------------------------------------------------------------------


def _root(method: str) -> tuple[str, str, str | None, str, int | None] | None:
    """``(module, function, container, operation, position)`` for one CLI method."""
    local = LOCAL_ROOTS.get(method)
    if local is not None:
        return (local[0], local[1], None, local[1], LOCAL_POSITIONS.get(method))
    target = transport.METHOD_MAP.get(method)
    if target is None:
        return None
    resource, operation = target
    container = "WaitService" if resource == "wait" else "BoardService"
    return ("service", operation, container, operation, None)


def method_help(method: str) -> MethodHelp | None:
    """The validated parameter help for one public method, or ``None`` when unknown."""
    if method == "help":
        parameter = Parameter("METHOD", "string", False, None, "optional; omit to list every method", False, [], "cli.py")
        return MethodHelp("help", "cli", [parameter], [], [], ["cli.main"], ["reads only the method summaries and the validators; starts no service or model"])
    root = _root(method)
    if root is None:
        return None
    module, function, container, operation, position = root
    parameters, inline, opaque, locations, notes = _Extractor(_index()).collect(module, function, container, position)
    if not parameters and not inline and not opaque and not notes:
        notes.append("no top-level parameter validation was found for this method")
    notes.extend(METHOD_NOTES.get(method, ()))
    resource = transport.METHOD_MAP.get(method)
    operation_name = f"{resource[0]}.{resource[1]}" if resource else f"cli local ({operation})"
    return MethodHelp(
        method=method,
        operation=operation_name,
        parameters=parameters,
        inline=inline,
        opaque=opaque,
        locations=locations,
        notes=notes,
    )


def nearest_methods(name: str, methods: tuple[str, ...] | list[str]) -> list[str]:
    """Closest method names for a typo, tolerant of ``_`` and ``-`` spelling."""
    normalized = {method: method.lower().replace("_", "-") for method in methods}
    matches = difflib.get_close_matches(name.lower().replace("_", "-"), list(normalized.values()), n=3, cutoff=0.45)
    ranked: list[str] = []
    for match in matches:
        ranked.extend(method for method, value in normalized.items() if value == match and method not in ranked)
    return ranked[:3]


def _render_parameter(parameter: Parameter, depth: int, width: int = 24) -> list[str]:
    indent = "  " * (depth + 1)
    facts: list[str] = []
    if parameter.required:
        facts.append("required")
    if parameter.default is not None:
        facts.append(f"default {parameter.default}")
    if parameter.bounds:
        facts.append(parameter.bounds)
    if parameter.expression:
        origin = f" [{parameter.source}]" if parameter.source else ""
        facts.append(f"{parameter.expression}{origin}")
    if parameter.conditional:
        facts.append("conditional")
    if not facts:
        facts.append("accepted; the request validator sets no bound" if parameter.kind == "value" else "")
    note = FIELD_NOTES.get(parameter.name)
    kind_width = max(9, len(parameter.kind) + 1)
    line = f"{indent}{parameter.name:<{width}}{parameter.kind:<{kind_width}}{'; '.join(facts)}"
    lines = [line.rstrip()]
    if note:
        lines.append(f"{indent}{'':<{width}}{'':<{kind_width}}({note})")
    child_width = max((len(child.name) for child in parameter.children), default=0) + 2
    for child in parameter.children:
        lines.extend(_render_parameter(child, depth + 1, max(18, child_width)))
    return lines


def render_index(methods: tuple[str, ...] | list[str]) -> str:
    """``buddy help``: every public method with its one-sentence description."""
    entries = [(method, SUMMARIES.get(method) or MISSING_SUMMARY) for method in methods]
    entries.append(("help", SUMMARIES["help"]))
    width = max(len(method) for method, _ in entries)
    lines = [
        f"buddy — {len(entries)} methods. Pass one JSON object, --params-file PATH, or - for stdin (mutually exclusive).",
        "`buddy help METHOD` prints the parameters that method actually validates, with their defaults and bounds.",
        "",
    ]
    lines.extend(f"  {method:<{width}}  {summary}" for method, summary in entries)
    lines.extend(
        [
            "",
            'CLI-local on every method: output is "brief" (default) or "full".',
            "A mistyped method name prints the closest candidates.",
        ]
    )
    return "\n".join(lines)


def render_method(help_: MethodHelp) -> str:
    """``buddy help METHOD``: validated parameters, defaults, bounds and conditions."""
    summary = SUMMARIES.get(help_.method, "")
    lines = [f"buddy {help_.method}" + (f" — {summary}" if summary else "")]
    lines.append(f"operation: {help_.operation}")
    if help_.locations:
        shown = help_.locations[:5]
        suffix = f"; +{len(help_.locations) - len(shown)} more" if len(help_.locations) > len(shown) else ""
        lines.append(f"validated in: {'; '.join(shown)}{suffix}")
    lines.append("parameters (read from the live request validators; conditional fields depend on the other inputs):")
    if help_.parameters:
        lines.append(f"  {'name':<24}{'type':<9}defaults, bounds and conditions")
        for parameter in help_.parameters:
            lines.extend(_render_parameter(parameter, 0))
    else:
        lines.append("  (no parameters)")
    if help_.inline:
        lines.append(f"accepted names with no derived bound (declared by the validator): {', '.join(help_.inline)}")
    if help_.opaque:
        lines.append(f"runtime-computed allowed fields (not statically enumerable): {', '.join(help_.opaque)}")
    for note in help_.notes:
        lines.append(f"note: {note}")
    lines.append(f"cli-local: {CLI_LOCAL_LINES[0]}")
    if help_.method in _CONTROL_METHODS:
        lines.append(f"cli-local: {CLI_LOCAL_LINES[1]}")
    return "\n".join(lines)


def render(method: str | None, methods: tuple[str, ...] | list[str]) -> tuple[str | None, list[str]]:
    """Help text for ``method`` (or the index) and the closest candidates for a typo."""
    if method is None:
        return render_index(methods), []
    help_ = method_help(method)
    if help_ is not None:
        return render_method(help_), []
    return None, nearest_methods(method, methods)
