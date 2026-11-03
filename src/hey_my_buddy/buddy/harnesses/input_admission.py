"""Bounded facts from a native refusal of an initial text request."""

from __future__ import annotations

from .native_support import truncate_utf8


def admission_error(method: str, error: object) -> dict:
    """Keep native machine fields without interpreting free-form error text."""
    source = error if isinstance(error, dict) else {}
    data = source.get("data") if isinstance(source.get("data"), dict) else {}
    result = {"nativeMethod": method}
    code = source.get("code")
    if isinstance(code, str):
        result["nativeCode"] = truncate_utf8(code, 128)
    elif type(code) is int:
        result["nativeCode"] = code
    message = source.get("message")
    if isinstance(message, str):
        result["message"] = truncate_utf8(message, 512)
    for key in ("input_error_code", "max_chars", "actual_chars"):
        value = data.get(key, source.get(key))
        if key == "input_error_code" and isinstance(value, str):
            result[key] = truncate_utf8(value, 128)
        elif key != "input_error_code" and type(value) is int and value >= 0:
            result[key] = value
    return result
