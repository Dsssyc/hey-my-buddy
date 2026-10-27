"""Record refused native interactions for the session's completion tool."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .turn_io import private_json


class AttentionRecorder:
    """Write the live attention fact independently of inquiry delivery."""

    def __init__(self, path: Path | None, *, identity: dict, requests: list[dict] | None = None,
                 on_record: Callable[[], None] | None = None):
        self.path = path
        self.identity = identity
        self.on_record = on_record
        self.requests = requests if requests is not None else []

    def record(self, request: dict) -> None:
        if self.path is not None:
            private_json(self.path, {
                "version": 1,
                **{key: self.identity.get(key) for key in ("taskId", "attemptId", "generation", "turnId")},
                "requests": [*self.requests, request],
            })
        self.requests.append(request)
        if self.on_record is not None:
            self.on_record()
