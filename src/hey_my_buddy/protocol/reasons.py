"""Build a structured reason: a stable code plus the named facts a reader needs."""
from __future__ import annotations

from typing import Any


def reason(code: str, **params: Any) -> dict[str, Any]:
    """Return ``{"code": code, "params": params}``; the reader supplies the words."""
    return {"code": code, "params": params}
