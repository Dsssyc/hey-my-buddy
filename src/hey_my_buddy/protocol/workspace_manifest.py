"""Read immutable workspace and output facts by their explicit stored form."""

import hashlib
import json
from pathlib import Path
from typing import Literal, TypedDict

from ..errors import BoardError
from ..json_codec import decode_strict_json
from ..private_dirs import linked
from ..private_files import read_bytes


class ManifestRef(TypedDict):
    """Identity of the manifest at workspaces/<workspaceId>/manifest.json."""

    workspaceId: str
    manifestSha256: str


class SealRef(TypedDict):
    """Identity of a previous sealed workspace snapshot."""

    artifactId: str
    snapshotSha256: str


class SelectorsSelection(TypedDict):
    kind: Literal["selectors"]
    selectors: list[str]


class SealSelection(TypedDict):
    kind: Literal["seal"]
    seal: SealRef


class RecoverySelection(TypedDict):
    kind: Literal["recovery"]


ExecutionSelection = SelectorsSelection | SealSelection | RecoverySelection


def project_id(manifest: dict) -> str:
    return manifest["projectId"] if manifest["version"] in (4, 5, 6) else manifest["repositoryId"]


def manifest_ref(manifest: dict) -> ManifestRef:
    return {"workspaceId": manifest["workspaceId"], "manifestSha256": manifest["manifestSha256"]}


def _workspace_id(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 35 and value.startswith("ws-")
            and all(character in "0123456789abcdef" for character in value[3:]))


def _hex_digest(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def _manifest_bytes(manifest: dict) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _private_path(state_dir: Path, relative: Path) -> Path:
    root = Path(state_dir)
    current = root
    for part in relative.parts[:-1]:
        current /= part
        if linked(current):
            raise BoardError("PRIVATE_PATH_UNSAFE", "A referenced private directory is linked", path=str(current))
    return root / relative


def resolve_manifest(state_dir: Path, ref: ManifestRef) -> dict:
    """Read the one existing manifest file and prove its frozen identity."""
    if (not isinstance(ref, dict) or set(ref) != {"workspaceId", "manifestSha256"}
            or not _workspace_id(ref["workspaceId"]) or not _hex_digest(ref["manifestSha256"])):
        raise BoardError("INVALID_WORKSPACE", "Invalid workspace manifest reference")
    path = _private_path(state_dir, Path("workspaces") / ref["workspaceId"] / "manifest.json")
    try:
        raw = read_bytes(path)
        manifest = decode_strict_json(raw)
    except FileNotFoundError as error:
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The referenced workspace manifest is missing",
                         workspaceId=ref["workspaceId"]) from error
    except (ValueError, UnicodeError) as error:
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The referenced workspace manifest is malformed") from error
    if (not isinstance(manifest, dict) or manifest.get("workspaceId") != ref["workspaceId"]
            or type(manifest.get("version")) is not int or manifest["version"] not in (1, 2, 3, 4, 5, 6)):
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The workspace manifest identity changed")
    digest = hashlib.sha256(_manifest_bytes({key: value for key, value in manifest.items()
                                            if key != "manifestSha256"})).hexdigest()
    if (digest != ref["manifestSha256"] or manifest.get("manifestSha256") != digest
            or raw != _manifest_bytes(manifest)):
        raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The workspace manifest hash does not match its contents")
    return manifest


def turn_manifest(state_dir: Path, turn_input: dict) -> dict:
    """A new turn has a reference; archived turn versions froze the body."""
    version = turn_input["version"]
    if type(version) is not int:
        raise BoardError("TURN_INVALID", "Unsupported turn input version")
    if version == 4:
        return resolve_manifest(state_dir, turn_input["executionWorkspace"])
    if version in (1, 2, 3):
        return turn_input["executionWorkspace"]
    raise BoardError("TURN_INVALID", "Unsupported turn input version")


def _field(row, key):
    return row[key]


def read_record_reference(state_dir: Path, reference: dict) -> dict:
    """Resolve a file bound by its raw bytes and its internal snapshot digest."""
    if (not isinstance(reference, dict) or set(reference) != {"recordFile", "snapshotSha256"}
            or not isinstance(reference["recordFile"], dict)
            or set(reference["recordFile"]) != {"path", "sha256"}
            or not isinstance(reference["recordFile"]["path"], str)):
        raise BoardError("ARTIFACT_MISMATCH", "Invalid output record reference")
    file = reference["recordFile"]
    relative = Path(file["path"])
    if (relative.is_absolute() or ".." in relative.parts or not relative.parts
            or relative.parts[0] != "workspaces" or "\0" in file["path"]
            or not _hex_digest(file["sha256"]) or not _hex_digest(reference["snapshotSha256"])):
        raise BoardError("ARTIFACT_MISMATCH", "Invalid output record reference")
    path = _private_path(state_dir, relative)
    try:
        raw = read_bytes(path)
    except FileNotFoundError as error:
        raise BoardError("ARTIFACT_MISSING", "The referenced output record is missing", path=str(path)) from error
    if hashlib.sha256(raw).hexdigest() != file["sha256"]:
        raise BoardError("ARTIFACT_MISMATCH", "The output record bytes changed", path=str(path))
    try:
        record = decode_strict_json(raw)
    except (ValueError, UnicodeError) as error:
        raise BoardError("ARTIFACT_MISMATCH", "The output record is malformed", path=str(path)) from error
    if not isinstance(record, dict) or record.get("snapshotSha256") != reference["snapshotSha256"]:
        raise BoardError("ARTIFACT_MISMATCH", "The output record snapshot identity changed", path=str(path))
    try:
        fixed_path = _record_relative(record).as_posix()
    except (KeyError, TypeError, ValueError) as error:
        raise BoardError("ARTIFACT_MISMATCH", "The output record has an invalid binding", path=str(path)) from error
    if fixed_path != file["path"]:
        raise BoardError("ARTIFACT_MISMATCH", "The output record is outside its fixed binding", path=str(path))
    return record


resolve_record = read_record_reference


def artifact_manifest(state_dir: Path, row) -> dict:
    """Decode a stored artifact according to its declared content form."""
    form = _field(row, "content_form")
    if form == "sealed-inline-v1":
        return decode_strict_json(_field(row, "manifest_json"))
    if form == "input-reference":
        return resolve_manifest(state_dir, {"workspaceId": _field(row, "workspace_id"),
                                            "manifestSha256": _field(row, "input_manifest_sha256")})
    if form == "record-reference":
        return read_record_reference(state_dir, {"recordFile": {
            "path": _field(row, "record_path"), "sha256": _field(row, "record_sha256")},
            "snapshotSha256": _field(row, "output_snapshot_sha256")})
    raise BoardError("ARTIFACT_MISMATCH", "Unknown artifact content form")


def _record_relative(record: dict) -> Path:
    """The fixed location follows only from the sealed record's identity."""
    if (not isinstance(record, dict)
            or not isinstance(record.get("taskId"), str) or not record["taskId"]
            or not isinstance(record.get("attemptId"), str) or not record["attemptId"]
            or not _hex_digest(record.get("snapshotSha256"))):
        raise BoardError("ARTIFACT_MISMATCH", "Invalid sealed output record identity")
    workspace_id = record.get("workspaceId")
    if not _workspace_id(workspace_id):
        raise BoardError("ARTIFACT_MISMATCH", "Invalid output workspace identity")
    output_id = hashlib.sha256(_manifest_bytes([record["taskId"], record["attemptId"]])).hexdigest()
    relative = Path("workspaces") / workspace_id / "outputs" / output_id
    kind = record.get("kind")
    if kind is None:
        name = "output.json"
    elif kind == "host-output":
        relative = relative.with_name(output_id + "-host")
        name = "output.json"
    elif kind == "resolution" and record.get("action") in ("restore", "adopt"):
        name = "resolve-" + record["action"] + ".json"
    elif kind == "abandoned-site":
        name = "abandoned.json"
    else:
        raise BoardError("ARTIFACT_MISMATCH", "Unknown output record kind")
    return relative / name


def record_reference(state_dir: Path, record: dict) -> dict:
    """Identify the existing sealed output or resolution file without copying it."""
    relative = _record_relative(record)
    path = _private_path(state_dir, relative)
    try:
        raw = read_bytes(path)
    except FileNotFoundError as error:
        raise BoardError("ARTIFACT_MISSING", "The sealed output record is missing", path=str(path)) from error
    persisted = {key: value for key, value in record.items() if key != "cumulativePatch"}
    try:
        frozen = decode_strict_json(raw)
    except (ValueError, UnicodeError) as error:
        raise BoardError("ARTIFACT_MISMATCH", "The sealed output record is malformed", path=str(path)) from error
    if frozen != persisted:
        raise BoardError("ARTIFACT_MISMATCH", "The sealed output record differs from the supplied record")
    return {"recordFile": {"path": relative.as_posix(), "sha256": hashlib.sha256(raw).hexdigest()},
            "snapshotSha256": record["snapshotSha256"]}


def cumulative_patch_error(artifact, attempt):
    """The bound failed worker report may explicitly have no cumulative file."""
    if (artifact["kind"] != "partial-output" or artifact["output_origin"] != "worker"
            or attempt is None or attempt["result_form"] != "current-report-v2"):
        return None
    report = decode_strict_json(attempt["result_json"])
    payload = report["result"]
    partial = payload.get("partialWorkspaceSeal")
    if (report["status"] != "ok" and isinstance(partial, dict)
            and partial["snapshotSha256"] == artifact["output_snapshot_sha256"]):
        return payload.get("cumulativePatchError")
    return None
