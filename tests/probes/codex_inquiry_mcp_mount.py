"""Model-free Codex CLI probe: per-thread inquiry MCP plus private user MCP.

Run explicitly from the repository root with the check environment's Python:
PYTHONPATH=src python tests/probes/codex_inquiry_mcp_mount.py
This never starts a model turn. HOME and CODEX_HOME are temporary and private.
"""
from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CODEX = shutil.which("codex")


def main() -> None:
    if CODEX is None:
        raise RuntimeError("Codex CLI is not installed")
    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codex-inquiry-mcp-", dir=ROOT / "tmp") as temporary:
        root = Path(temporary)
        home, codex_home = root / "home", root / "codex-home"
        home.mkdir()
        codex_home.mkdir()
        config = root / "session.json"
        config.write_text(json.dumps({
            "identity": {"taskId": "probe", "attemptId": "probe", "generation": 1, "turnId": "probe"},
            "inputSha256": "0" * 64, "key": "1" * 64,
            "attentionPath": str(root / "attention.json"),
            "inquiryJournalPath": str(root / "inquiries.jsonl"),
            "toolNames": ["hey_my_buddy_checkpoint", "hey_my_buddy_answer_inquiry"],
        }))
        server = {"command": sys.executable,
                  "args": ["-m", "hey_my_buddy.buddy.roles.session_mcp", "--config", str(config)],
                  "env": {"PYTHONPATH": str(ROOT / "src")},
                  "tools": {name: {"approval_mode": "approve"} for name in
                            ("hey_my_buddy_checkpoint", "hey_my_buddy_answer_inquiry")}}
        # This lives solely in the private CODEX_HOME. A per-thread mount must
        # add the inquiry server without erasing the user's configured servers.
        (codex_home / "config.toml").write_text(
            '[mcp_servers.private_existing]\n'
            f'command = {json.dumps(sys.executable)}\n'
            f'args = {json.dumps(server["args"])}\n'
            f'env = {{ PYTHONPATH = {json.dumps(str(ROOT / "src"))} }}\n')
        env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")
               if key in os.environ}
        env.update(HOME=str(home), CODEX_HOME=str(codex_home))
        process = subprocess.Popen([CODEX, "app-server", "--listen", "stdio://"],
                                   cwd=root, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        request_id = 0

        def call(method: str, params: dict) -> dict:
            nonlocal request_id
            request_id += 1
            process.stdin.write((json.dumps({"id": request_id, "method": method,
                                             "params": params}) + "\n").encode())
            process.stdin.flush()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if not select.select([process.stdout], [], [], 0.2)[0]:
                    continue
                line = process.stdout.readline()
                if not line:
                    break
                frame = json.loads(line)
                if frame.get("id") == request_id:
                    if "error" in frame:
                        raise RuntimeError(f"Codex rejected {method}: {frame['error']}")
                    return frame["result"]
            raise RuntimeError(f"Codex did not answer {method}")

        try:
            call("initialize", {"clientInfo": {"name": "private-probe", "title": "Private Probe",
                                               "version": "1"}})
            process.stdin.write(b'{"method":"initialized","params":{}}\n')
            process.stdin.flush()
            started = call("thread/start", {
                "cwd": str(root), "approvalPolicy": "never", "sandbox": "read-only", "ephemeral": True,
                "config": {"mcp_servers": {"private_inquiry": server}},
            })
            thread = started["thread"]
            if thread["turns"]:
                raise RuntimeError("the probe unexpectedly started a model turn")
            invalid = {**server, "tools": {"hey_my_buddy_answer_inquiry": {"approval_mode": "invalid-probe"}}}
            try:
                call("thread/start", {
                    "cwd": str(root), "approvalPolicy": "never", "sandbox": "read-only", "ephemeral": True,
                    "config": {"mcp_servers": {"private_inquiry": invalid}},
                })
            except RuntimeError as error:
                approval_rejection = str(error)
            else:
                raise RuntimeError("Codex did not validate per-tool approval_mode")
            names = {}
            for name in ("private_existing", "private_inquiry"):
                response = call("mcpServerStatus/list", {"threadId": thread["id"], "serverName": name})
                entries = response["data"]
                if len(entries) != 1 or entries[0]["name"] != name:
                    raise RuntimeError(f"the {name} MCP server did not mount")
                status = entries[0].get("runtimeStatus")
                tools = sorted(entries[0]["tools"])
                if status != "connected" or tools != ["hey_my_buddy_answer_inquiry", "hey_my_buddy_checkpoint"]:
                    raise RuntimeError(f"the {name} MCP server is unavailable: {status}, {tools}")
                names[name] = {"runtimeStatus": status, "tools": tools}
            print(json.dumps({"codexVersion": subprocess.check_output([CODEX, "--version"],
                                                                        env=env, text=True).strip(),
                              "modelTurns": len(thread["turns"]), "inquiryToolApproval": server["tools"], "invalidApprovalRejected": approval_rejection, "servers": names}, sort_keys=True))
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


if __name__ == "__main__":
    main()
