"""Controller-owned Codex sandbox challenges, independent of model command text.

The private permission profile and fixed argv are bound before the native call.
Only its correlated native result can supply a challenge result. Model output
cannot name a challenge, and buffered/late/duplicate replies are rejected.
"""
from __future__ import annotations

from pathlib import Path

PROBES = ("internal-read", "outside-read", "inside-write", "outside-write", "network")
PROFILE = "buddy-router"
CAP = 8192
FORMAT = "codex-native-sandbox-probes-v1"


def fixed_commands(frozen: Path, sentinel: Path, url: str) -> dict[str, list[str]]:
    """These test targets are private controller fixtures, never model arguments."""
    return {
        "internal-read": ["/bin/cat", "marker.txt"],
        "outside-read": ["/usr/bin/wc", "-c", str(sentinel)],
        "inside-write": ["/bin/sh", "-c", 'printf PROBE-MODIFIED > "$1"', "buddy-probe", "marker.txt"],
        "outside-write": ["/bin/sh", "-c", 'printf PROBE-MODIFIED > "$1"', "buddy-probe", str(sentinel)],
        "network": ["/usr/bin/curl", "-q", "--noproxy", "*", "--head", "--verbose", "--max-time", "3", url],
    }


def run(connection, *, frozen: Path, sentinel: Path, url: str) -> dict:
    from .adapters.codex_protocol import CodexProtocolError
    if connection.responses:
        raise CodexProtocolError("invalid-protocol", "Sandbox probe started with an uncorrelated native reply")
    prior = connection.strict_responses
    connection.strict_responses = True
    records = []
    try:
        for operation, argv in fixed_commands(frozen, sentinel, url).items():
            request_id = connection.next_id
            response = connection.call("command/exec", {
                "command": argv, "cwd": str(frozen), "permissionProfile": PROFILE,
                "timeoutMs": 5000, "outputBytesCap": CAP,
            })
            code, stdout, stderr = (response.get(key) for key in ("exitCode", "stdout", "stderr"))
            if type(code) is not int or not isinstance(stdout, str) or not isinstance(stderr, str):
                raise CodexProtocolError("invalid-native-result", "Sandbox command has no complete native result")
            # command/exec has no truncation flag. Reaching its per-stream cap
            # cannot establish complete evidence, even when a denial is visible.
            truncated = len(stdout.encode()) >= CAP or len(stderr.encode()) >= CAP
            records.append({"operation": operation, "requestId": request_id,
                "method": "command/exec", "permissionProfile": PROFILE,
                "exitCode": code, "stdout": stdout, "stderr": stderr,
                "truncated": truncated})
            if truncated:
                raise CodexProtocolError("probe-evidence-truncated", "Sandbox command capture reached its bound")
        if connection.responses:
            raise CodexProtocolError("invalid-protocol", "Sandbox probe ended with an uncorrelated native reply")
    finally:
        connection.strict_responses = prior
    return {"format": FORMAT, "permissionProfile": PROFILE, "operations": records}


def results(value) -> tuple[dict, bool]:
    """Validate the fixed controller-to-native transcript, not model declarations."""
    if not isinstance(value, dict) or value.get("format") != FORMAT or value.get("permissionProfile") != PROFILE:
        return {}, False
    records = value.get("operations")
    if not isinstance(records, list) or len(records) != len(PROBES):
        return {}, False
    result, identities = {}, set()
    for record in records:
        if not isinstance(record, dict):
            return {}, False
        operation, identity = record.get("operation"), record.get("requestId")
        if (operation not in PROBES or operation in result or type(identity) is not int or identity <= 0
                or identity in identities or record.get("method") != "command/exec"
                or record.get("permissionProfile") != PROFILE or record.get("truncated") is not False
                or type(record.get("exitCode")) is not int
                or any(not isinstance(record.get(key), str) or len(record[key].encode()) >= CAP
                       for key in ("stdout", "stderr"))):
            return {}, False
        identities.add(identity)
        result[operation] = (record["exitCode"], record["stdout"], record["stderr"])
    return result, set(result) == set(PROBES)
