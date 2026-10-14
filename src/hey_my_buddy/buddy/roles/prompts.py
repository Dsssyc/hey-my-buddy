"""Fixed runtime role text before every part of the Host's input."""
from __future__ import annotations

from ...install.runtime import resource_path
from ...json_codec import canonical_json


def _role_prompt(resource: str, sections: tuple[str, ...]) -> str:
    text = resource_path(resource).read_text(encoding="utf-8")
    return text + "\n" + "\n\n".join(sections)


def worker_prompt(task_text: str, turn_input: dict, *, delivery: tuple[str, ...]) -> str:
    """Shared Worker assembly; delivery describes its native completion channel."""
    return _role_prompt("roles.worker", (*delivery, task_text, canonical_json(turn_input)))


def router_prompt(call_text: str) -> str:
    """All Router calls start with the same role, before the call's instructions."""
    return _role_prompt("roles.router", (call_text,))


def wrap_up_prompt() -> str:
    """One short continuation of the interrupted Worker session."""
    return resource_path("roles.worker").with_name("wrap_up.txt").read_text(encoding="utf-8")
