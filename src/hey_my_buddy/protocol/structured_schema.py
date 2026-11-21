"""The JSON Schema subset shared by role correction and service validation."""
from __future__ import annotations

import json
from collections.abc import Iterator

_TYPES = {"object": dict, "array": list, "string": str, "null": type(None), "boolean": bool}


def schema_errors(value, schema: dict) -> list[str]:
    """Keywords violated by ``value``, for the JSON Schema subset Router answers use.

    Supported: type, enum, required, properties, additionalProperties=false,
    items, minLength, maxLength and maxItems. Any other keyword is refused so an
    unsupported schema can never pass silently.
    """
    return [keyword for _, keyword in schema_issues(value, schema, all_extras=False)]


def schema_issues(value, schema: dict, path: str = "$", *, all_extras: bool = True) -> Iterator[tuple[str, str]]:
    """Locate the violated keywords by JSON path."""
    supported = {"type", "enum", "required", "properties", "additionalProperties",
                 "items", "minLength", "maxLength", "maxItems"}
    unknown = set(schema) - supported
    if unknown or schema.get("additionalProperties", False) is not False:
        raise ValueError(f"unsupported schema keywords: {sorted(unknown) or ['additionalProperties']}")
    kinds = schema.get("type")
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else list(kinds)
        if not any(isinstance(value, _TYPES[kind]) and (kind == "boolean" or not isinstance(value, bool))
                   for kind in kinds):
            yield path, "type"
            return
    if "enum" in schema and value not in schema["enum"]:
        yield path, "enum"
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            yield path, "minLength"
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            yield path, "maxLength"
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            yield path, "maxItems"
        for index, item in enumerate(value):
            yield from schema_issues(item, schema.get("items", {}), f"{path}[{index}]", all_extras=all_extras)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        yield from ((f"{path}.{key}", "required") for key in schema.get("required", []) if key not in value)
        if "additionalProperties" in schema:
            for extra in (key for key in value if key not in properties):
                if isinstance(extra, str) and len(extra) <= 64:
                    path_to_extra = f"{path}[{json.dumps(extra)}]"
                else:
                    path_to_extra = path
                yield path_to_extra, "additionalProperties"
                if not all_extras:
                    break
        for key, sub in properties.items():
            if key in value:
                yield from schema_issues(value[key], sub, f"{path}.{key}", all_extras=all_extras)

