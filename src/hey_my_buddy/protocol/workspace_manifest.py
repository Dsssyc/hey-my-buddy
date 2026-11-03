"""Identity and selection forms for immutable workspace manifests."""

from typing import Literal, TypedDict


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


class SealSelection(TypedDict):
    kind: Literal["seal"]


class RecoverySelection(TypedDict):
    kind: Literal["recovery"]


# The plan fixes these discriminants but leaves the selectors key and the
# placement of SealRef within executionSelection for its writer to settle.
ExecutionSelection = SelectorsSelection | SealSelection | RecoverySelection


def project_id(manifest: dict) -> str:
    return manifest["projectId"] if manifest["version"] == 4 else manifest["repositoryId"]


def project_id_sql(expression: str) -> str:
    return (f"CASE WHEN json_extract({expression}, '$.version')=4 "
            f"THEN json_extract({expression}, '$.projectId') "
            f"ELSE json_extract({expression}, '$.repositoryId') END")
