"""Declarations shared by adapter descriptions and blackboard admission."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

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

#: The fixed sentence every unknown input-limit declaration carries: no numeric
#: value was obtained for the channel, which is never a claim that none exists.
UNKNOWN_INPUT_LIMIT_NOTE = ("no numeric input limit was obtained for this channel; "
                            "unknown does not mean there is no limit")


@dataclass(frozen=True)
class InputLimit:
    """One harness's declared single-input limit, written in code (ADR-023).

    Like ``PERMISSION_LEVELS`` this is a harness capability, not a model
    budget, a database setting or a health observation. A known limit states
    the maximum admitted size (a prompt exactly at it is admitted), the unit
    the harness itself counts in, the native input scope the check covers, the
    form versions the value was verified on, and the public source those
    numbers were read from. An unknown limit states explicitly that no value
    was obtained; it never reads as unlimited. The counting itself lives in
    :mod:`hey_my_buddy.protocol.input_limit`.
    """

    form: str
    scope: str
    source: str
    state: Literal["known", "unknown"]
    value: int | None = None
    unit: str | None = None
    versions: tuple[str, ...] = ()
    note: str = ""

    @classmethod
    def known(cls, *, form: str, scope: str, source: str, value: int, unit: str,
              versions: tuple[str, ...]) -> "InputLimit":
        return cls(form=form, scope=scope, source=source, state="known",
                   value=value, unit=unit, versions=versions)

    @classmethod
    def unknown(cls, *, form: str, scope: str, source: str,
                note: str = UNKNOWN_INPUT_LIMIT_NOTE) -> "InputLimit":
        return cls(form=form, scope=scope, source=source, state="unknown", note=note)

    def to_payload(self) -> dict:
        payload = {"state": self.state, "form": self.form,
                   "scope": self.scope, "source": self.source}
        if self.state == "known":
            return {**payload, "value": self.value, "unit": self.unit,
                    "versions": list(self.versions)}
        return {**payload, "note": self.note}


#: The input-limit declaration of every harness with a native input channel.
#: Codex CLI 0.164.0's app-server ``turn/start`` sums the turn's native text
#: input items in Unicode scalar values and rejects above 1,048,576 (public
#: source of tag rust-v0.164.0, commit 6aaac694); the other three channels have
#: no obtained value yet and say so.
INPUT_LIMITS = {
    "codex": InputLimit.known(
        form="cli-app-server", scope="turn-start-text",
        source="codex-0.164.0-public-source",
        value=1_048_576, unit="unicode-scalars", versions=("0.164.0",)),
    "claude": InputLimit.unknown(
        form="cli-stream-json", scope="prompt-text", source="claude-code-cli-reference"),
    "dsh": InputLimit.unknown(
        form="cli-acp", scope="session-prompt-text", source="dsh-cli-help"),
    "zcode": InputLimit.unknown(
        form="cli-app-server", scope="session-send-text", source="zcode-cli-docs"),
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


