"""Identity frozen in a service-written Worker turn input."""
import json
import os

from ..errors import BoardError


NONE_TURN_INPUT_VERSION = 5


def none_execution_workspace(value: object) -> dict:
    """Read the explicit execution context of a version 5 none turn."""
    required = {"kind", "path", "projectId", "projectKind", "access", "integrator"}
    if not isinstance(value, dict) or set(value) not in (required, required | {"accessNote"}):
        raise BoardError("TURN_INVALID", "The none turn has invalid execution workspace fields")
    if (value["kind"] != "none" or not isinstance(value["path"], str)
            or not os.path.isabs(value["path"]) or not value["path"]
            or not isinstance(value["projectId"], str) or not value["projectId"]
            or value["projectKind"] not in ("git", "folder")
            or value["access"] not in ("read", "write", "full")):
        raise BoardError("TURN_INVALID", "The none turn has invalid execution workspace identity")
    integrator = value["integrator"]
    if (not isinstance(integrator, dict)
            or (integrator != {"role": "host"}
                and (set(integrator) != {"role", "id"} or integrator.get("role") != "lead"
                     or not isinstance(integrator.get("id"), str) or not integrator["id"]))):
        raise BoardError("TURN_INVALID", "The none turn has invalid integrator")
    note = value.get("accessNote")
    if (value["access"] == "full") != (isinstance(note, str) and bool(note.strip())
                                        and note.splitlines() == [note]):
        raise BoardError("TURN_INVALID", "The none turn has invalid accessNote")
    if value["access"] != "full" and "accessNote" in value:
        raise BoardError("TURN_INVALID", "The none turn cannot carry accessNote")
    return value


def turn_buddy(input_json: str | None) -> dict | None:
    """Read the turn's own buddy, never the run's later choice or an attempt triple."""
    return json.loads(input_json)["context"]["buddy"] if input_json is not None else None
