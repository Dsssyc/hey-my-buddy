"""Build a structured reason: a stable code plus the named facts a reader needs."""
from __future__ import annotations

import json
from typing import Any


def reason(code: str, **params: Any) -> dict[str, Any]:
    """Return ``{"code": code, "params": params}``; the reader supplies the words."""
    return {"code": code, "params": params}


def reason_from_json(code: str | None, params_json: str | None) -> dict[str, Any] | None:
    """Project a stored code and its parameter JSON without revalidating them."""
    return {"code": code, "params": json.loads(params_json)} if code is not None else None
