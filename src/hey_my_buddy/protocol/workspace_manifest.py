"""Project identity projected from immutable workspace manifests."""


def project_id(manifest: dict) -> str:
    return manifest["projectId"] if manifest["version"] == 4 else manifest["repositoryId"]


def project_id_sql(expression: str) -> str:
    return (f"CASE WHEN json_extract({expression}, '$.version')=4 "
            f"THEN json_extract({expression}, '$.projectId') "
            f"ELSE json_extract({expression}, '$.repositoryId') END")
