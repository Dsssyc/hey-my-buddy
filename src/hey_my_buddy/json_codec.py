"""Canonical JSON encoding and strict decoding shared by both sides.

Callers enforce their frame bounds before parsing and map decoder errors at
those boundaries. Strict decoding rejects duplicate keys and non-finite values.
"""
from __future__ import annotations

import json
import math
from typing import Any


def canonical_json(value: object) -> str:
    """The project's deterministic bounded JSON text for one value."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _no_duplicates(items: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _finite_float(raw: str) -> float:
    number = float(raw)
    if not math.isfinite(number):
        raise ValueError(f"non-finite JSON number {raw}")
    return number


def _reject_constant(name: str) -> None:
    raise ValueError(f"non-finite JSON number {name}")


def decode_strict_json(raw: str | bytes) -> Any:
    """Strict JSON decode: duplicate members and non-finite numbers are refused.

    The installed pydantic (2.13.5, pydantic-core 2.46.5) was probed and does
    not reject either: ``model_validate_json`` resolves a duplicated key by
    letting the last one win, and accepts ``NaN`` and the ``1e999`` overflow as
    ``nan``/``inf`` float values. Untrusted frames are therefore decoded here
    first and model-validated only afterwards.
    """
    return json.loads(raw, object_pairs_hook=_no_duplicates, parse_float=_finite_float,
                      parse_constant=_reject_constant)



__all__ = ["canonical_json", "decode_strict_json"]
