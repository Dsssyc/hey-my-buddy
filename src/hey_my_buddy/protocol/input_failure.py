"""Bounded input-rejection facts shared by execution receipts and Host views."""
from __future__ import annotations

from .harness_names import HARNESS_NAMES
from .input_limit import InputLimitCheck, MEASUREMENT_UNITS, PROMPT_PARTS, UNICODE_SCALARS, UTF8_BYTES

INPUT_REJECTED = "HARNESS_INPUT_REJECTED"


def input_failure(check: InputLimitCheck, *, stage: str, harness: str, version: str | None,
                  native_request_sent: bool | None, model_started: bool | None,
                  native_error: dict | None = None) -> dict:
    """Project the check of this exact initial text only when it failed."""
    facts = check.to_payload()
    measurement = facts["measurement"]
    if check.limit.state == "unknown":
        measurement["utf8Parts"] = check.measurement.part_sizes(UTF8_BYTES)
    return {"version": 1, "stage": stage,
            "harness": {"name": harness, "form": check.limit.form, "version": version},
            "limit": facts["limit"], "measurement": measurement,
            "nativeRequestSent": native_request_sent, "modelStarted": model_started,
            **(native_error or {})}


def is_input_too_large(native_error: dict | None) -> bool:
    """Only an explicit machine code identifies the native input refusal."""
    return bool(native_error is not None and (
        native_error.get("input_error_code") == "input_too_large"
        or native_error.get("nativeCode") == "input_too_large"))


def _text(value, maximum: int) -> bool:
    return isinstance(value, str) and 0 < len(value.encode("utf-8")) <= maximum


def _count(value) -> bool:
    return type(value) is int and value >= 0


def _parts(value) -> bool:
    return (isinstance(value, dict) and set(value) == set(PROMPT_PARTS)
            and all(_count(size) for size in value.values()))


def normalize_input_failure(value) -> dict | None:
    """Validate a failure package at the result boundary, without inferring facts."""
    required = {"version", "stage", "harness", "limit", "measurement", "nativeRequestSent", "modelStarted"}
    optional = {"nativeMethod", "nativeCode", "input_error_code", "max_chars", "actual_chars", "message"}
    if (not isinstance(value, dict) or not required <= value.keys()
            or value.keys() - required - optional or type(value["version"]) is not int
            or value["version"] != 1 or value["stage"] not in ("preflight", "native-admission")):
        return None
    harness, limit, measurement = value["harness"], value["limit"], value["measurement"]
    if (not isinstance(harness, dict) or set(harness) != {"name", "form", "version"}
            or harness["name"] not in HARNESS_NAMES or not _text(harness["form"], 80)
            or harness["version"] is not None and (not isinstance(harness["version"], str)
                                                   or len(harness["version"]) > 80)):
        return None
    common_limit = {"state", "form", "scope", "source"}
    if (not isinstance(limit, dict) or not common_limit <= limit.keys()
            or limit["form"] != harness["form"]
            or any(not _text(limit[key], 256) for key in ("form", "scope", "source"))):
        return None
    known = limit["state"] == "known"
    if known:
        if (set(limit) != common_limit | {"value", "unit", "versions"}
                or not _count(limit["value"]) or limit["value"] == 0
                or limit["unit"] not in MEASUREMENT_UNITS
                or not isinstance(limit["versions"], list) or not 1 <= len(limit["versions"]) <= 16
                or any(not _text(version, 320) for version in limit["versions"])):
            return None
    elif (limit["state"] != "unknown" or set(limit) != common_limit | {"note"}
          or not _text(limit["note"], 1024)):
        return None
    expected_measurement = {"unit", "total", "parts"} | (set() if known else {"utf8Bytes", "utf8Parts"})
    if (not isinstance(measurement, dict) or set(measurement) != expected_measurement
            or measurement["unit"] != (limit["unit"] if known else UNICODE_SCALARS)
            or not _count(measurement["total"]) or not _parts(measurement["parts"])
            or sum(measurement["parts"].values()) != measurement["total"]):
        return None
    if not known and (not _count(measurement["utf8Bytes"]) or not _parts(measurement["utf8Parts"])
                      or sum(measurement["utf8Parts"].values()) != measurement["utf8Bytes"]):
        return None
    if any(value[key] is not None and type(value[key]) is not bool
           for key in ("nativeRequestSent", "modelStarted")):
        return None
    if value["stage"] == "preflight" and (not known or measurement["total"] <= limit["value"]
            or value["nativeRequestSent"] is not False or value["modelStarted"] is not False
            or bool(value.keys() & optional)):
        return None
    if any(key in value and not _text(value[key], maximum)
           for key, maximum in (("nativeMethod", 128), ("input_error_code", 128))):
        return None
    if "message" in value and (not isinstance(value["message"], str)
                                or len(value["message"].encode("utf-8")) > 512):
        return None
    if "nativeCode" in value and not (value["nativeCode"] is None
            or type(value["nativeCode"]) is int or _text(value["nativeCode"], 128)):
        return None
    if any(key in value and not _count(value[key]) for key in ("max_chars", "actual_chars")):
        return None
    return value


def input_failure_view(receipt) -> dict | None:
    """Read a raw role result or its stored execution receipt without expansion."""
    if not isinstance(receipt, dict):
        return None
    result = receipt.get("result")
    payload = result if isinstance(result, dict) else receipt
    return payload.get("inputFailure")
