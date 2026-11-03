"""Fixed runtime role text before every part of the Host's input."""
from __future__ import annotations

from ...install.runtime import resource_path
from ...json_codec import canonical_json
from ...protocol.input_limit import PROMPT_PARTS, PromptParts


class PromptText(str):
    """The assembled text and its construction-time source partitions.

    Only the string crosses the native seam. The fragments stay in memory until
    the initial request is checked, never in a request file or success receipt.
    """

    def __new__(cls, fragments: tuple[tuple[str, str], ...]):
        prompt = super().__new__(cls, "".join(text for _part, text in fragments))
        prompt.fragments = fragments
        grouped = {part: [] for part in PROMPT_PARTS}
        for source, text in fragments:
            grouped[source].append(text)
        texts = {part: "".join(pieces) for part, pieces in grouped.items()}
        prompt.parts = PromptParts(role=texts["role"], task=texts["task"],
                                   host_input=texts["hostInput"], context=texts["context"],
                                   delivery=texts["delivery"], format=texts["format"])
        return prompt

    def appended(self, part: str, text: str) -> PromptText:
        return PromptText((*self.fragments, (part, text)))


def _role_prompt(resource: str, sections: tuple[tuple[tuple[str, str], ...], ...]) -> PromptText:
    fragments = [("role", resource_path(resource).read_text(encoding="utf-8") + "\n")]
    for index, section in enumerate(sections):
        if index:
            fragments.append((section[0][0], "\n\n"))
        fragments.extend(section)
    return PromptText(tuple(fragments))


def _canonical_member_span(document: dict, path: tuple[str, ...]) -> tuple[int, int] | None:
    """Locate a source member by the canonical codec's ordered construction.

    Offsets come from encoding preceding members and keys, never from searching
    the final text for a value which might also occur in another source part.
    The member owns its leading comma, key, colon and escaped value; enclosing
    braces belong to the context which contains it.
    """
    offset, current = 0, document
    for index, key in enumerate(path):
        if key not in current:
            return None
        preceding = {name: value for name, value in current.items() if name < key}
        start = offset + len(canonical_json(preceding)) - 1
        member = canonical_json({key: current[key]})[1:-1]
        if index == len(path) - 1:
            return start, start + bool(preceding) + len(member)
        offset = start + bool(preceding) + len(canonical_json(key)) + 1
        current = current[key]


def _turn_fragments(turn_input: dict) -> tuple[tuple[str, str], ...]:
    document = _prompt_turn_input(turn_input)
    text = canonical_json(document)
    # These fields are written by the Host's continuation/decision operations.
    paths = (("context", "continuation", "input"), ("context", "continuation", "note"),
             ("context", "hostDecision"))
    spans = sorted(span for path in paths if (span := _canonical_member_span(document, path)) is not None)
    fragments, offset = [], 0
    for start, end in spans:
        fragments.extend((("context", text[offset:start]), ("hostInput", text[start:end])))
        offset = end
    fragments.append(("context", text[offset:]))
    return tuple(fragments)


def _prompt_turn_input(turn_input: dict) -> dict:
    """Project the Worker's operating context without service-side accounts.

    The runtime resolves the frozen ManifestRef before calling this function.
    Workspace paths, access and scope guide work. The snapshot stays in its frozen
    workspace record and the stash inventory in the frozen input; get/audit retain
    their evidence for service comparison and review.
    """
    workspace = {key: value for key, value in turn_input["executionWorkspace"].items()
                 if key != "snapshot"}
    context = {key: value for key, value in turn_input["context"].items() if key != "sharedStash"}
    return {**turn_input, "executionWorkspace": workspace, "context": context}


def worker_prompt(task_text: str, turn_input: dict, *, delivery: tuple[str, ...],
                  credential_file: str | None = None) -> PromptText:
    """Shared Worker assembly; delivery describes its native completion channel."""
    lead = ()
    if turn_input.get("context", {}).get("delegationAuthority") is True:
        lead = ((("role", resource_path("roles.lead").read_text(encoding="utf-8")),),)
        if credential_file is not None:
            lead += ((("delivery", f"Credential file path: {canonical_json(credential_file)}"),),)
    return _role_prompt("roles.worker",
                        (*lead, *((("delivery", text),) for text in delivery),
                         (("task", task_text),), _turn_fragments(turn_input)))


def router_prompt(call_text: str) -> PromptText:
    """All Router calls start with the same role, before the call's instructions."""
    return _role_prompt("roles.router", ((("task", call_text),),))


def wrap_up_prompt() -> str:
    """One short continuation of the interrupted Worker session."""
    return resource_path("roles.wrap_up").read_text(encoding="utf-8")
