"""The JSON Schema subset shared by role correction and service validation."""
from __future__ import annotations

_TYPES = {"object": dict, "array": list, "string": str, "null": type(None)}


def schema_errors(value, schema: dict) -> list[str]:
    """Keywords violated by ``value``, for the JSON Schema subset Router answers use.

    Supported: type, enum, required, properties, additionalProperties=false,
    items, minLength, maxLength and maxItems. Any other keyword is refused so an
    unsupported schema can never pass silently.
    """
    supported = {"type", "enum", "required", "properties", "additionalProperties",
                 "items", "minLength", "maxLength", "maxItems"}
    unknown = set(schema) - supported
    if unknown or schema.get("additionalProperties", False) is not False:
        raise ValueError(f"unsupported schema keywords: {sorted(unknown) or ['additionalProperties']}")
    errors = []
    kinds = schema.get("type")
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else list(kinds)
        if not any(isinstance(value, _TYPES[kind]) and not (kind != "null" and isinstance(value, bool)) for kind in kinds):
            return ["type"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append("enum")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append("minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append("maxLength")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append("maxItems")
        for item in value:
            errors.extend(schema_errors(item, schema.get("items", {})))
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        missing = [key for key in schema.get("required", []) if key not in value]
        errors.extend("required" for _ in missing)
        if "additionalProperties" in schema and set(value) - set(properties):
            errors.append("additionalProperties")
        for key, sub in properties.items():
            if key in value:
                errors.extend(schema_errors(value[key], sub))
    return errors

