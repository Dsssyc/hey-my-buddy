"""Opt-in, model-free Codex CLI app-server session-deletion probe.

Run from the checkout: uv run --frozen python tests/probes/codex_delete_sessions.py
Uses a new empty temporary HOME/CODEX_HOME, no login, no model turns. The normal
check suite never runs this file. Prints the CLI version and native responses,
plus before/after rollout, shell-snapshot and session-index evidence.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid
from unittest.mock import patch

from hey_my_buddy.buddy.harnesses.codex.native_run import _control_connection
from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError
from hey_my_buddy.buddy.harnesses.runtime_selection import bound


def artifacts(home: Path, session_id: str) -> dict:
    index = home / "session_index.jsonl"
    return {
        "rollouts": sorted(str(path.relative_to(home)) for path in (home / "sessions").rglob("*.jsonl")
                           if session_id in path.name),
        "shellSnapshots": sorted(str(path.relative_to(home)) for path in (home / "shell_snapshots").rglob("*")
                                 if path.is_file() and session_id in path.name),
        "sessionIndexRows": [line for line in index.read_text().splitlines() if session_id in line]
                            if index.exists() else [],
    }


def create(connection, root: Path, home: Path) -> str:
    started = connection.call("thread/start", {"cwd": str(root), "ephemeral": False,
                              "approvalPolicy": "never", "sandbox": "read-only"})["thread"]
    if started["turns"]:
        raise RuntimeError("The probe unexpectedly started a model turn")
    session_id = started["id"]
    connection.call("thread/name/set", {"threadId": session_id, "name": "private-deletion-probe"})
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        evidence = artifacts(home, session_id)
        if evidence["rollouts"] and evidence["sessionIndexRows"]:
            return session_id
        time.sleep(0.05)
    raise RuntimeError("The newly named thread did not persist its rollout and session index row")


def delete(connection, session_id: str, expected: str, *, code=None, prefix=None) -> dict:
    try:
        result = connection.call("thread/delete", {"threadId": session_id})
    except CodexProtocolError as error:
        native = error.native_error
        if (expected != "error" or native is None or native["code"] != code
                or not native["message"].startswith(prefix)):
            raise
        return {"error": native}
    if expected != "deleted":
        raise RuntimeError("Expected a native deletion error but received success")
    return {"result": result}


def verify_removed(home, session_id):
    after = artifacts(home, session_id)
    if any(after.values()):
        raise RuntimeError("Deleted session still has native artifacts: " + json.dumps(after))
    return after


def main() -> None:
    executable = shutil.which("codex")
    if executable is None:
        raise RuntimeError("Codex CLI is not installed on PATH")
    with tempfile.TemporaryDirectory(prefix="codex-delete-probe-") as temporary:
        root = Path(temporary)
        home, codex_home = root / "home", root / "codex-home"
        home.mkdir()
        codex_home.mkdir()
        environment = {key: os.environ[key] for key in
                       ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SYSTEMROOT", "WINDIR")
                       if key in os.environ}
        environment.update(HOME=str(home), USERPROFILE=str(home), CODEX_HOME=str(codex_home))
        record = {"harness": "codex", "status": "ready", "command": [executable]}
        report = {"modelTurns": 0}
        def client(label):
            return _control_connection(cwd=str(root), invocation_root=root / label,
                                       native_root=root / label / "native", timeout_seconds=30,
                                       cancelled=lambda: False)
        with patch.dict(os.environ, environment, clear=True), bound([record], environment=environment):
            with client("first-process") as (connection, version, _facts):
                report["codexVersion"] = version
                session_id = create(connection, root, codex_home)
                before = artifacts(codex_home, session_id)
                report["delete"] = {**delete(connection, session_id, "deleted"), "before": before,
                                    "after": verify_removed(codex_home, session_id)}
                missing_prefix = "no rollout found for thread id "
                report["repeatDelete"] = delete(connection, session_id, "error", code=-32600, prefix=missing_prefix)
                report["neverExisted"] = delete(connection, str(uuid.uuid4()), "error", code=-32600, prefix=missing_prefix)
                report["invalidId"] = delete(connection, "invalid-thread-id", "error", code=-32600, prefix="invalid thread id:")
                cross_process_id = create(connection, root, codex_home)
            with client("second-process") as (connection, _version, _facts):
                report["crossProcessDelete"] = {
                    "before": artifacts(codex_home, cross_process_id),
                    **delete(connection, cross_process_id, "deleted"),
                    "after": verify_removed(codex_home, cross_process_id),
                }
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
