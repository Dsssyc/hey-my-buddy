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

WORKER_PERMISSIONS = {
    "codex": {"read": ("read", "enforced"), "write": ("write", "enforced"),
              "full": ("full", "enforced")},
    "claude": {"read": ("read", "enforced"), "write": ("write", "enforced"),
               "full": ("full", "enforced")},
    "dsh": {"read": ("read", "enforced"), "write": ("write", "enforced"),
            "full": ("full", "enforced")},
    "zcode": {"read": ("read", "enforced"), "write": ("write", "convention"),
              "full": ("full", "enforced")},
}


def adapter_capabilities(name: str) -> tuple[str, ...]:
    return (*ADAPTER_CAPABILITIES[name],
            *(f"permission:{level}" for level in WORKER_PERMISSIONS.get(name, {})))


