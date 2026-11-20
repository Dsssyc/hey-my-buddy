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
    "codex": {"none": "convention", "web": "enforced", "read": "enforced", "write": "enforced", "full": "enforced"},
    # Claude Code CLI 2.1.329 with `--tools "" --strict-mcp-config --mcp-config
    # <snapshot only> --allowedTools mcp__hey_my_buddy_snapshot__*` exposes exactly
    # StructuredOutput and the mounted snapshot tools — no ToolSearch, no built-in
    # tools, no user MCP — so `none` is enforced (ADR-035 decision 3, probes note
    # 2026-11-19).
    "claude": {"none": "enforced", "web": "enforced", "read": "enforced", "write": "enforced", "full": "enforced"},
    "dsh": {"none": "enforced", "web": "enforced", "read": "enforced", "write": "enforced", "full": "enforced"},
    "zcode": {"none": "enforced", "read": "convention", "write": "convention", "full": "enforced"},
}

#: The harnesses that can mount the read-only frozen snapshot query service for
#: the Router and maintenance planes (ADR-035 decision 2). The declaration is
#: per harness, recorded only after the native probes confirmed the mount and
#: its isolation: Codex through `dynamicTools`, Claude Code, DSH and ZCode
#: through a fixed-name stdio MCP server. A harness without this capability
#: loses only its `routing:fast` / `decision` / maintenance eligibility; its
#: Worker `permission:read` (and every other level) stays exactly as declared.
ROUTER_SNAPSHOT_QUERY_HARNESSES = frozenset({"codex", "claude", "dsh", "zcode"})

#: Versions on which the ADR-023 clause 10 permission behavior was verified.
#: An unknown version records missing verification, not unsupported permissions.
PERMISSIONS_VERIFIED_VERSIONS: dict[str, str | None] = {
    "codex": None,
    "claude": None,
    "dsh": "0.2.2-rc.1",
    "zcode": None,
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

@dataclass(frozen=True)
class ModelStartUnseen:
    """One harness's declaration that its native protocol cannot show the
    model's start in real time, written in code (ADR-023).

    Like ``INPUT_LIMITS`` this is a harness capability, not a setting or a
    health observation. The Router start wait judges silence within the wait
    as a model that never began; on a protocol that cannot show the start as
    it happens that judgment is false, so the execution process must not apply
    the wait to a harness declared here and the harness's own stream idle
    timeout bounds the call instead. A harness absent from the table is not
    declared the opposite: it is simply not verified either way and keeps the
    configured wait.
    """

    form: str
    versions: tuple[str, ...]
    basis: str


#: The harnesses whose native protocol cannot show the model's start in real
#: time. DSH CLI 0.2.2-rc.1 (CLI form, ACP channel) forwards only events
#: already written to the session: one whole model step — reasoning, message
#: and tool calls — streams into the session first and only then goes out
#: block by block, with no handshake capability, settings entry or environment
#: switch that changes it and no earlier notification at the turn's start; the
#: harness's own stream idle timeout (no data at all, 300 seconds by default)
#: is what bounds a silent call.
MODEL_START_UNSEEN = {
    "dsh": ModelStartUnseen(
        form="cli-acp", versions=("0.2.2-rc.1",),
        basis="the ACP server emits each block only after the model's whole step "
              "is written to the session"),
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


