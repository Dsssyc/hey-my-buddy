"""Build a structured reason: a stable code plus the named facts a reader needs."""
from __future__ import annotations

import json
from importlib.resources import files
from types import MappingProxyType
from typing import Any

from ..errors import BoardError


REGISTERED_REASON_PARAMETERS = MappingProxyType({
    code: tuple(parameters) for code, parameters in json.loads(
        files('hey_my_buddy.protocol').joinpath('reason-codes.json').read_text(encoding='utf-8')
    ).items()
})


def reason(code: str, **params: Any) -> dict[str, Any]:
    """Return ``{"code": code, "params": params}``; the reader supplies the words."""
    return {"code": code, "params": params}


def reason_from_json(code: str | None, params_json: str | None) -> dict[str, Any] | None:
    """Project a stored code and its parameter JSON without revalidating them."""
    return {"code": code, "params": json.loads(params_json)} if code is not None else None


def validate_waiting_reason(value: Any, *, activity: bool = False) -> dict[str, Any]:
    """Validate the finite waiting reasons accepted from another process."""
    field = 'activity.waitingReason' if activity else 'data.waitingReason'
    if activity:
        allowed = value in (
            {'code': 'native-authority-required', 'params': {'kind': 'permission'}},
            {'code': 'native-authority-required', 'params': {'kind': 'user-input'}},
        )
    else:
        allowed = value == {'code': 'router-input-unknown', 'params': {}}
    if not allowed:
        raise BoardError('INVALID_ARGUMENT', f'{field} has an unsupported reason', field=field)
    return value
