"""Build a structured reason: a stable code plus the named facts a reader needs."""
from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from ..errors import BoardError


_CODES: dict[str, list[str]] = json.loads(
    files('hey_my_buddy.protocol').joinpath('reason-codes.json').read_text(encoding='utf-8')
)


def reason(code: str, **params: Any) -> dict[str, Any]:
    """Return ``{"code": code, "params": params}``; the reader supplies the words."""
    return {"code": code, "params": params}


def reason_from_json(code: str | None, params_json: str | None) -> dict[str, Any] | None:
    """Project a stored code and its parameter JSON without revalidating them."""
    return {"code": code, "params": json.loads(params_json)} if code is not None else None


def validate_reason(value: Any, field: str) -> dict[str, Any]:
    """Validate a reason received across a process boundary against the code table."""
    if not isinstance(value, dict) or set(value) != {'code', 'params'}:
        raise BoardError('INVALID_ARGUMENT', f'{field} must be a reason object', field=field)
    code, params = value['code'], value['params']
    if not isinstance(code, str) or code not in _CODES:
        raise BoardError('INVALID_ARGUMENT', f'{field}.code must be registered', field=f'{field}.code')
    if not isinstance(params, dict) or any(not isinstance(key, str) for key in params):
        raise BoardError('INVALID_ARGUMENT', f'{field}.params must be an object', field=f'{field}.params')
    if set(params) != set(_CODES[code]):
        raise BoardError('INVALID_ARGUMENT', f'{field}.params must match the registered names', field=f'{field}.params')
    return value
