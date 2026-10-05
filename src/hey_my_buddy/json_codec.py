"""The one bounded-JSON codec both sides of the system share.

ADR-025 decision 6 keeps exactly three JSON mechanisms as one common
implementation: the digest-normalized canonical encoding, the strict decode
that refuses duplicate members and non-finite numbers (including the ``1e999``
overflow the plain parser silently widens to ``inf``), and the whole-frame
size limit that is enforced before any parsing starts. Callers keep their own
error mapping: :func:`decode_strict_json` raises ``ValueError`` /
``RecursionError`` exactly like the standard library so each caller maps them
to its own error type, while :func:`decode_bounded_frame` raises the
structured board error the internal formats use.
"""
from __future__ import annotations

import json
import math
from typing import Any, Mapping

from .errors import BoardError


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


def decode_bounded_frame(value: str | bytes | Mapping, *, label: str, maximum: int) -> Any:
    """Size-bound one whole frame before parsing, then strictly decode it.

    A ``Mapping`` form is canonicalized and bounded exactly like the text form,
    so no spelling of the same frame slips past the size limit; undecodable
    bytes and pathological structures come back as structured board errors.
    """
    if isinstance(value, Mapping):
        try:
            value = canonical_json(dict(value))
        except (TypeError, ValueError, RecursionError) as error:
            raise BoardError("INVALID_ARGUMENT", f"the {label} frame is not bounded JSON",
                             reason=str(error)[:200]) from None
    elif isinstance(value, bytes):
        try:
            value = value.decode()
        except UnicodeDecodeError:
            raise BoardError("INVALID_ARGUMENT", f"the {label} frame is not valid UTF-8") from None
    if not isinstance(value, str) or len(value.encode()) > maximum:
        raise BoardError("INVALID_ARGUMENT",
                         f"the {label} frame is missing or exceeds its {maximum}-byte bound", limit=maximum)
    try:
        return decode_strict_json(value)
    except ValueError as error:
        raise BoardError("INVALID_ARGUMENT", f"the {label} frame is not strict JSON",
                         reason=str(error)[:200]) from None
    except RecursionError:
        raise BoardError("INVALID_ARGUMENT", f"the {label} frame nests too deeply") from None


__all__ = ["canonical_json", "decode_bounded_frame", "decode_strict_json"]
