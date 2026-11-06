"""Audience-specific CLI help read directly from Pydantic request models."""
from __future__ import annotations

import difflib
import json

from ..protocol import requests
from ..protocol.requests import HOST_REQUESTS, OPERATIONS_REQUESTS, ROLE_REQUESTS, request_model


def nearest_methods(name: str, methods: list[str] | tuple[str, ...]) -> list[str]:
    return difflib.get_close_matches(name, methods, n=3, cutoff=0.5)


def _list(audience: str) -> str:
    registry = {"host": HOST_REQUESTS, "operations": OPERATIONS_REQUESTS, "role": ROLE_REQUESTS}[audience]
    heading = {
        "host": "Host commands (one JSON object per call)",
        "operations": "User operations (top-level commands; also callable by an authorized Host)",
        "role": "Credential-scoped Lead and Worker commands",
    }[audience]
    lines = [heading]
    for name, model in registry.items():
        lines.append(f"  {name}: {(model.__doc__ or '').strip()}")
    if audience == "host":
        lines.extend(("  help: Show a command's live request fields without starting the service.",
                      "Use `hey-my-buddy help operations` or `hey-my-buddy help role` for other audiences."))
    return "\n".join(lines)


def _field_shape(field: dict, *, nullable: bool) -> str:
    field = dict(field)
    if not nullable:
        if 'anyOf' in field:
            alternatives = [item for item in field['anyOf'] if item.get('type') != 'null']
            if len(alternatives) == 1:
                field.pop('anyOf')
                field.update(alternatives[0])
            else:
                field['anyOf'] = alternatives
        if field.get('default') is None:
            field.pop('default', None)
    facts = {key: field[key] for key in ("type", "anyOf", "enum", "const", "minimum", "maximum",
                                         "minLength", "maxLength", "minItems", "maxItems", "pattern", "default")
             if key in field}
    if "x-maxUtf8Bytes" in field:
        facts["maxUtf8Bytes"] = field["x-maxUtf8Bytes"]
    if "$ref" in field:
        facts["type"] = field["$ref"].split("/")[-1]
    if "items" in field:
        item = field["items"]
        facts["items"] = item.get("$ref", item)
    return json.dumps(facts, ensure_ascii=False, separators=(",", ":"))


def _detail(name: str, audience: str) -> str:
    model = request_model(name, audience=audience)
    schema = model.model_json_schema(mode="validation")
    required = set(schema.get("required", []))
    lines = [f"{name}: {(model.__doc__ or '').strip()}", "JSON object fields:"]
    if not schema.get("properties"):
        lines.append("  (none)")
    for field_name, field in schema.get("properties", {}).items():
        necessity = "required" if field_name in required else "optional"
        lines.append(f"  {field_name} ({necessity}): {_field_shape(field, nullable=field_name in model.nullable_fields)}")
        if field.get("description"):
            lines.append(f"    {field['description']}")
    for definition_name, definition in schema.get("$defs", {}).items():
        lines.append(f"Nested {definition_name} fields:")
        nested_required = set(definition.get("required", []))
        nested_model = getattr(requests, definition_name, None)
        nullable_fields = nested_model.nullable_fields if isinstance(nested_model, type) and issubclass(nested_model, requests.Request) else frozenset()
        for field_name, field in definition.get("properties", {}).items():
            necessity = "required" if field_name in nested_required else "optional"
            lines.append(f"  {field_name} ({necessity}): {_field_shape(field, nullable=field_name in nullable_fields)}")
            if field.get("description"):
                lines.append(f"    {field['description']}")
        if isinstance(nested_model, type) and issubclass(nested_model, requests.Request):
            lines.extend(f"  {rule}" for rule in nested_model.rules)
    if model.rules:
        lines.append("Field combinations:")
        lines.extend(f"  {rule}" for rule in model.rules)
    if name != "console" and audience in ("host", "operations"):
        lines.append("CLI-only output: brief (default) or full; omitted before service validation.")
    return "\n".join(lines)


def render(arguments: list[str]) -> str:
    """Render a Pydantic-validated local help request; no service process."""
    request = requests.parse_request("help", {"arguments": arguments}, audience="local")
    return _detail(request.method, request.audience) if request.method is not None else _list(request.audience)
