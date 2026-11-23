"""Declarations shared by adapter descriptions and blackboard admission.

The four coded harnesses' facts derive from the single static declarations in
:mod:`hey_my_buddy.protocol.harness_declarations`; the per-topic tables below
stay the working surfaces their readers and the check suite patch and read.
"""
from __future__ import annotations

import re
from typing import Literal

from ..errors import BoardError
from .harness_declarations import HARNESS_DECLARATIONS, InputLimit, ModelStartUnseen

#: The adapter capability surfaces, keyed by adapter name. The four coded
#: harnesses read their single static declarations; ``command`` and
#: ``decision`` are the execution layer's own model-free carriers.
ADAPTER_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "command": ("command", "cancel", "artifacts", "deadline", "argv"),
    "decision": ("decision",),
    **{name: declaration.capabilities for name, declaration in HARNESS_DECLARATIONS.items()},
}

#: The declared permission levels and their native enforcement strength, as a
#: working copy the check suite can patch per case.
PERMISSION_LEVELS: dict[str, dict[str, Literal["enforced", "convention"]]] = {
    name: dict(declaration.permission_levels) for name, declaration in HARNESS_DECLARATIONS.items()
}

#: The harnesses that can mount the read-only frozen snapshot query service for
#: the Router and maintenance planes (ADR-035 decision 2). The declaration is
#: per harness, recorded only after the native probes confirmed the mount and
#: its isolation: Codex through `dynamicTools`, Claude Code, DSH and ZCode
#: through a fixed-name stdio MCP server. A harness without this capability
#: loses only its `routing:fast` / `decision` / maintenance eligibility; its
#: Worker `permission:read` (and every other level) stays exactly as declared.
ROUTER_SNAPSHOT_QUERY_HARNESSES = frozenset(
    name for name, declaration in HARNESS_DECLARATIONS.items() if declaration.router_snapshot_query)

#: Versions on which the ADR-023 clause 10 permission behavior was verified.
#: An unknown version records missing verification, not unsupported permissions.
PERMISSIONS_VERIFIED_VERSIONS: dict[str, str | None] = {
    name: declaration.permissions_verified_version for name, declaration in HARNESS_DECLARATIONS.items()
}

#: The input-limit declaration of every harness with a native input channel.
INPUT_LIMITS: dict[str, InputLimit] = {
    name: declaration.input_limit for name, declaration in HARNESS_DECLARATIONS.items()
}

#: The harnesses whose native protocol cannot show the model's start in real
#: time.
MODEL_START_UNSEEN: dict[str, ModelStartUnseen] = {
    name: declaration.model_start_unseen for name, declaration in HARNESS_DECLARATIONS.items()
    if declaration.model_start_unseen is not None
}


#: One version-shaped token of a public ``--version`` line, with any
#: pre-release or build suffix kept attached so it never equals the bare
#: release, and a number glued to a word is not a token.
_VERSION_TOKEN = re.compile(r"(?<![\w.])v?[0-9]+(?:\.[0-9]+)+(?:[-+][0-9A-Za-z.-]+)?(?![\w.])")


def _observed_version(observed: str) -> str | None:
    """The harness's own version inside a public version line, or ``None``.

    The production probe keeps the line as printed (stripped, at most 80
    characters); the harness's version is the first version-shaped token of
    that line, with an optional ``v`` prefix dropped.
    """
    found = _VERSION_TOKEN.search(observed)
    if found is None:
        return None
    token = found.group(0)
    return token[1:] if token.startswith("v") else token


def version_matches(observed: str, versions: tuple[str, ...]) -> bool:
    """Whether a public version fact identifies exactly one verified version."""
    token = _observed_version(observed)
    return token is not None and token in versions


def declared_input_limit(harness: str) -> InputLimit | None:
    """The declared input limit, or ``None`` where no native input channel exists."""
    return INPUT_LIMITS.get(harness)


def model_start_unseen(harness: str) -> ModelStartUnseen | None:
    """The declaration that this harness's protocol cannot show the model's start."""
    return MODEL_START_UNSEEN.get(harness)


def select_input_limit(harness: str, observed_version: str | None) -> InputLimit | None:
    """The declaration that applies to one observed public version fact.

    A known value stays tied to the form versions it was verified on: any other
    observed version, or none observed, selects an unknown declaration that
    says why — the neighboring known value is never applied to a version
    outside its verified range.
    """
    declared = INPUT_LIMITS.get(harness)
    if declared is None or declared.state != "known":
        return declared
    if observed_version is not None and version_matches(observed_version, declared.versions):
        return declared
    verified = ", ".join(declared.versions)
    note = (f"no harness version was observed; a numeric limit is verified only at {verified}"
            if observed_version is None else
            f"the observed version does not match the verified versions ({verified})")
    return InputLimit.unknown(form=declared.form, scope=declared.scope,
                              source=declared.source, note=note)


def adapter_capabilities(name: str) -> tuple[str, ...]:
    return (*ADAPTER_CAPABILITIES[name],
            *(f"permission:{level}" for level in PERMISSION_LEVELS.get(name, {})))


def require_permission(buddy: dict | None, access: str) -> None:
    """Reject an explicit Worker route that cannot run at the requested access.

    An unresolved route is checked after selection.
    """
    if buddy is None:
        return
    name = buddy["harness"]
    capability = f"permission:{access}"
    if name in ADAPTER_CAPABILITIES and capability in adapter_capabilities(name):
        return
    raise BoardError("UNSUPPORTED_CAPABILITY", "This harness does not support the requested Worker permission",
                     harness=name, access=access, requiredCapability=capability,
                     missingCapabilities=[capability])


def buddy_capabilities(harness: str, stored: list[str]) -> list[str]:
    """Add the role capabilities derived from this harness's declared facts."""
    declared = PERMISSION_LEVELS.get(harness, {})
    # Router-plane eligibility needs the frozen snapshot mount (ADR-035
    # decision 2): without it the harness loses `routing:fast` / `decision`
    # while keeping every Worker permission level it declares.
    snapshots = harness in ROUTER_SNAPSHOT_QUERY_HARNESSES
    derived = []
    if snapshots and "read" in declared:
        derived.append("decision")
    if snapshots and "none" in declared:
        derived.append("routing:fast")
    derived.extend(f"permission:{level}" for level in declared)
    return [*stored, *derived]
