"""Fixed runtime role text before every part of the Host's input."""
from __future__ import annotations

from ...install.runtime import resource_path
from ...json_codec import canonical_json


def _role_prompt(resource: str, sections: tuple[str, ...]) -> str:
    text = resource_path(resource).read_text(encoding="utf-8")
    return text + "\n" + "\n\n".join(sections)


def worker_prompt(task_text: str, turn_input: dict, *, delivery: tuple[str, ...],
                  credential_file: str | None = None) -> str:
    """Shared Worker assembly; delivery describes its native completion channel."""
    lead = ()
    if turn_input.get("context", {}).get("delegationAuthority") is True:
        lead = (resource_path("roles.lead").read_text(encoding="utf-8"),)
        if credential_file is not None:
            lead += (f"Credential file path: {canonical_json(credential_file)}",)
    prompt_input = dict(turn_input)
    if "context" in turn_input:
        prompt_input["context"] = {key: value for key, value in turn_input["context"].items() if key != "task"}
    return _role_prompt("roles.worker", (*lead, *delivery, task_text, canonical_json(prompt_input)))


def router_prompt(call_text: str) -> str:
    """All Router calls start with the same role, before the call's instructions."""
    return _role_prompt("roles.router", (call_text,))


def wrap_up_prompt() -> str:
    """One short continuation of the interrupted Worker session."""
    return resource_path("roles.wrap_up").read_text(encoding="utf-8")
