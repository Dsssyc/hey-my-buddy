"""Model-free Codex CLI client-tool schema and admission probe.

Run explicitly from the repository root:
PYTHONPATH=src python tests/probes/codex_inquiry_client_tool.py
HOME and CODEX_HOME are private. No user settings, login or model turns are used.
"""
from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from hey_my_buddy.buddy.harnesses.codex.native_run import prepare_inquiry_services
from hey_my_buddy.buddy.roles.worker_services import ANSWER_SCHEMA

ROOT = Path(__file__).resolve().parents[2]
CODEX = shutil.which("codex")


def main() -> None:
    if CODEX is None:
        raise RuntimeError("Codex CLI is not installed")
    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codex-inquiry-client-", dir=ROOT / "tmp") as temporary:
        root = Path(temporary)
        home, codex_home, schema_root = root / "home", root / "codex-home", root / "schema"
        home.mkdir()
        codex_home.mkdir()
        env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")
               if key in os.environ}
        env.update(HOME=str(home), CODEX_HOME=str(codex_home))
        version = subprocess.check_output([CODEX, "--version"], env=env, text=True).strip()
        subprocess.run([CODEX, "app-server", "generate-json-schema", "--experimental", "--out", str(schema_root)],
                       env=env, cwd=root, check=True, capture_output=True, text=True)
        schema = lambda name: json.loads((schema_root / name).read_text())
        start_schema = schema("v2/ThreadStartParams.json")
        resume_schema = schema("v2/ThreadResumeParams.json")
        call_schema = schema("DynamicToolCallParams.json")
        response_schema = schema("DynamicToolCallResponse.json")
        if "dynamicTools" not in start_schema["properties"]:
            raise RuntimeError("the installed CLI does not declare dynamicTools")
        if not {"arguments", "callId", "threadId", "tool", "turnId"} <= set(call_schema["required"]):
            raise RuntimeError("the installed CLI has a different client-tool call shape")
        text_schema = next(item for item in response_schema["definitions"]["DynamicToolCallOutputContentItem"]["oneOf"]
                           if item["properties"]["type"]["enum"] == ["inputText"])
        if set(text_schema["required"]) != {"type", "text"} or set(response_schema["required"]) != {"contentItems", "success"}:
            raise RuntimeError("the installed CLI has a different client-tool response shape")
        inquiry = prepare_inquiry_services(
            invocation_root=root, identity={"taskId": "probe", "attemptId": "probe", "generation": 1, "turnId": "probe"},
            input_sha256="0" * 64, attention_path=root / "attention.json",
            inquiry={"resultsPath": str(root / "inquiries.jsonl")}, answer_schema=ANSWER_SCHEMA)
        stderr = (root / "stderr.log").open("wb")
        process = subprocess.Popen([CODEX, "app-server", "--listen", "stdio://"], cwd=root, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, start_new_session=True)
        request_id = 0

        def call(method: str, params: dict) -> dict:
            nonlocal request_id
            request_id += 1
            process.stdin.write((json.dumps({"id": request_id, "method": method, "params": params}) + "\n").encode())
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
            call("initialize", {"clientInfo": {"name": "private-probe", "title": "Private Probe", "version": "1"},
                                "capabilities": {"experimentalApi": True}})
            process.stdin.write(b'{"method":"initialized","params":{}}\n')
            process.stdin.flush()
            started = call("thread/start", {"cwd": str(root), "approvalPolicy": "never", "sandbox": "read-only",
                                             "ephemeral": True, "dynamicTools": inquiry.dynamic_tools})
            thread = started["thread"]
            if thread["turns"]:
                raise RuntimeError("the probe unexpectedly started a model turn")
            read = call("thread/read", {"threadId": thread["id"], "includeTurns": False})["thread"]
            descriptor = read.get("dynamicTools")
            if descriptor is not None and descriptor != inquiry.dynamic_tools:
                raise RuntimeError("thread/read changed the declared client tool")
            # thread/resume has no dynamicTools override in 0.162.1. The original
            # declaration belongs to the thread; neither the protocol schema nor
            # its thread/read response exposes a descriptor readback in this version.
            print(json.dumps({"codexVersion": version, "modelTurns": len(thread["turns"]),
                              "experimentalApi": True, "threadStartAccepted": True,
                              "declaredTool": inquiry.answer_tool, "responseTextType": "inputText",
                              "resumeDeclaresDynamicTools": "dynamicTools" in resume_schema["properties"],
                              "threadReadExposesDynamicTools": descriptor is not None,
                              "descriptorReadback": descriptor}, sort_keys=True))
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
            stderr.close()


if __name__ == "__main__":
    main()
