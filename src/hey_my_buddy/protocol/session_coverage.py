"""Per-kind native session evidence, shared by every harness and the console."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SESSION_CONTENT_KINDS = ("messages", "reasoning", "tool-input", "tool-output", "plan")
SessionContentKind = Literal["messages", "reasoning", "tool-input", "tool-output", "plan"]


@dataclass(frozen=True)
class Evidenced:
    version: str
    source: str

    def to_payload(self) -> dict:
        return {"status": "evidenced", "version": self.version}


@dataclass(frozen=True)
class Unavailable:
    reason: dict

    def to_payload(self) -> dict:
        return {"status": "unavailable", "reason": self.reason}


@dataclass(frozen=True)
class Unverified:
    def to_payload(self) -> dict:
        return {"status": "unverified"}


SessionCoverage = dict[SessionContentKind, Evidenced | Unavailable | Unverified]
