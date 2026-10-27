"""Declarations shared by adapter descriptions and blackboard admission."""
from __future__ import annotations

ADAPTER_CAPABILITIES = {
    "command": ("command", "cancel", "artifacts", "deadline", "argv"),
    "decision": ("decision",),
    "dsh": ("dsh", "inquiry", "workspace", "cancel", "artifacts", "deadline", "native-session"),
    "zcode": ("zcode", "observe", "inquiry", "workspace", "cancel", "artifacts", "deadline", "native-session"),
    "codex": ("codex", "inquiry", "workspace", "cancel", "artifacts", "deadline", "native-session"),
    "claude": ("claude", "inquiry", "workspace", "cancel", "artifacts", "deadline"),
}

PERMISSION_LEVELS = {
    "codex": {"none": "convention", "read": "enforced", "write": "enforced", "full": "enforced"},
    "claude": {"read": "enforced", "write": "enforced", "full": "enforced"},
    "dsh": {"none": "enforced", "read": "enforced", "write": "enforced", "full": "enforced"},
    "zcode": {"none": "enforced", "read": "enforced", "write": "convention", "full": "enforced"},
}


def adapter_capabilities(name: str) -> tuple[str, ...]:
    return (*ADAPTER_CAPABILITIES[name],
            *(f"permission:{level}" for level in PERMISSION_LEVELS.get(name, {})))


def buddy_capabilities(harness: str, stored: list[str]) -> list[str]:
    """Add the role capabilities derived from this harness's permission levels."""
    declared = PERMISSION_LEVELS.get(harness, {})
    derived = []
    if "read" in declared:
        derived.append("decision")
    if "none" in declared:
        derived.append("routing:fast")
    derived.extend(f"permission:{level}" for level in declared)
    return [*stored, *derived]


