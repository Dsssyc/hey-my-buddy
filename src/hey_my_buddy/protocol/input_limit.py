"""Pure measurement of one final prompt against a declared input limit.

The counting follows each harness's own declaration (ADR-023 "输入上限"): a
known limit is checked in its declared unit, and an unknown limit never blocks
a run but still measures the same text so a later native rejection can carry
the diagnostic sizes. Everything here is a pure function over strings with the
standard library only — no tokenizer, and no second prompt assembly; the parts
arrive already split at construction time by their source. Wiring this check
into the execution paths is a later step; nothing on an execution path calls
this module yet.
"""
from __future__ import annotations

from dataclasses import dataclass

from .capabilities import InputLimit

#: The units a declaration can be measured in. Unicode scalars count each code
#: point of the text — a non-BMP character is one, a combining mark is one of
#: its own — and UTF-8 bytes are the encoded size. No other unit exists.
UNICODE_SCALARS = "unicode-scalars"
UTF8_BYTES = "utf-8-bytes"
MEASUREMENT_UNITS = (UNICODE_SCALARS, UTF8_BYTES)

#: The six construction-time source partitions of one final prompt, in fixed
#: order. Every piece of the assembled text belongs to exactly one part: the
#: Host input rides in its own part and is never also counted inside context,
#: and separators and JSON key names count in the part that carries them.
PROMPT_PARTS = ("role", "task", "hostInput", "context", "delivery", "format")


@dataclass(frozen=True)
class PromptParts:
    """One final prompt split at construction time into its source partitions.

    A part with no content this turn is an empty string; the six parts together
    are the complete prompt, so the caller that assembles the prompt is the one
    that keeps the partition whole.
    """

    role: str
    task: str
    host_input: str
    context: str
    delivery: str
    format: str

    def items(self) -> tuple[tuple[str, str], ...]:
        """The six parts in the fixed order, as (part name, text) pairs."""
        return tuple(zip(PROMPT_PARTS, (self.role, self.task, self.host_input,
                                        self.context, self.delivery, self.format)))


@dataclass(frozen=True)
class PromptSizes:
    """One text's size in every measurable unit."""

    unicode_scalars: int
    utf8_bytes: int

    def size(self, unit: str) -> int:
        if unit == UNICODE_SCALARS:
            return self.unicode_scalars
        if unit == UTF8_BYTES:
            return self.utf8_bytes
        raise ValueError(f"unsupported measurement unit {unit!r}")


def measure_text(text: str) -> PromptSizes:
    """Count one text in every measurable unit.

    Encodability is the existing input and serialization boundary's job; the
    diagnostic byte count of a valid Unicode string never revalidates it.
    """
    return PromptSizes(unicode_scalars=len(text), utf8_bytes=len(text.encode("utf-8")))


@dataclass(frozen=True)
class PromptMeasurement:
    """The measured sizes of one final prompt and of each of its parts.

    The total is exactly the sum of the parts in every unit; there is no
    second, separately counted whole.
    """

    parts: tuple[tuple[str, PromptSizes], ...]
    total: PromptSizes

    def part_sizes(self, unit: str) -> dict[str, int]:
        return {name: sizes.size(unit) for name, sizes in self.parts}

    def to_payload(self, unit: str) -> dict:
        if unit not in MEASUREMENT_UNITS:
            raise ValueError(f"unsupported measurement unit {unit!r}")
        return {"unit": unit, "total": self.total.size(unit), "parts": self.part_sizes(unit)}


def measure_prompt(parts: PromptParts) -> PromptMeasurement:
    """Measure the six source partitions; the total is their sum."""
    measured = tuple((name, measure_text(text)) for name, text in parts.items())
    return PromptMeasurement(
        parts=measured,
        total=PromptSizes(
            unicode_scalars=sum(sizes.unicode_scalars for _name, sizes in measured),
            utf8_bytes=sum(sizes.utf8_bytes for _name, sizes in measured)))


@dataclass(frozen=True)
class InputLimitCheck:
    """The pure judgment of one measured prompt against one declaration.

    The payload carries the facts a Host-side rejection reason needs: the
    declaration, and the measurement in the declared unit (in Unicode scalars,
    with the UTF-8 byte total beside it, when no unit is declared).
    """

    blocked: bool
    limit: InputLimit
    measurement: PromptMeasurement

    def to_payload(self) -> dict:
        unit = self.limit.unit if self.limit.state == "known" else UNICODE_SCALARS
        measurement = self.measurement.to_payload(unit)
        if self.limit.state != "known":
            measurement["utf8Bytes"] = self.measurement.total.utf8_bytes
        return {"blocked": self.blocked, "limit": self.limit.to_payload(),
                "measurement": measurement}


def check_prompt_input(limit: InputLimit, parts: PromptParts) -> InputLimitCheck:
    """Judge one final prompt against the declaration that applies to this run.

    A known limit blocks only above its maximum — a prompt exactly at the limit
    is admitted, matching how Codex rejects its native text input. An unknown
    limit never blocks; the check still measures the same text so the sizes are
    at hand when a harness rejects the input natively.
    """
    measurement = measure_prompt(parts)
    blocked = (limit.state == "known"
               and measurement.total.size(limit.unit) > limit.value)
    return InputLimitCheck(blocked=blocked, limit=limit, measurement=measurement)


__all__ = [
    "MEASUREMENT_UNITS", "PROMPT_PARTS", "PromptParts", "PromptMeasurement", "PromptSizes",
    "UNICODE_SCALARS", "UTF8_BYTES",
    "InputLimitCheck", "check_prompt_input", "measure_prompt", "measure_text",
]
