#!/usr/bin/env python3
"""Explicit Codex CLI session-content probe against a loopback Responses model.

Run from the checkout root: uv run --frozen python tests/probes/codex_session_content.py
All writable probe material lives in tmp/session-coverage-c2/codex. The native
CLI gets a private HOME, CODEX_HOME and working directory, a dummy provider key,
and a sandbox-exec profile permitting only loopback network. No real account or
model is used. Native RPC events, model requests and sessions remain in tmp.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hey_my_buddy.buddy.harnesses.codex.protocol import Connection, CodexProtocolError


CHECKOUT = Path(__file__).resolve().parents[2]
ROOT = CHECKOUT / "tmp/session-coverage-c2/codex" / f"run-{uuid.uuid4().hex}"


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def model_items(request: dict, number: int) -> list[dict]:
    if number == 1:
        items = [
            {"id": "rs_probe", "type": "reasoning", "summary": [
                {"type": "summary_text", "text": "Probe reasoning summary."}]},
            {"id": "msg_before", "type": "message", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": "Body before tools.", "annotations": []}]},
        ]
        tools = request.get("tools", [])
        available = [tool.get("name") for tool in tools if isinstance(tool, dict)]
        plan = next((name for name in available if name and "update_plan" in name), None)
        shell = next((name for name in available if name and ("shell" in name or "exec" in name)), None)
        name = plan or shell
        if name:
            arguments = ({"plan": [{"step": "Inspect fixture", "status": "in_progress"}]}
                         if plan else {"cmd": "printf probe-tool-output-1"})
            items.append({"id": "fc_probe_1", "type": "function_call", "status": "completed",
                          "call_id": "call_probe_1", "name": name,
                          "arguments": json.dumps(arguments)})
        return items
    if number == 2:
        available = [tool.get("name") for tool in request.get("tools", []) if isinstance(tool, dict)]
        shell = next((name for name in available if name and ("shell" in name or "exec" in name)), None)
        if not shell:
            raise RuntimeError("Codex did not expose a shell tool for the second call")
        return [
            {"id": "msg_between", "type": "message", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": "Body between tools.", "annotations": []}]},
            {"id": "fc_probe_2", "type": "function_call", "status": "completed",
             "call_id": "call_probe_2", "name": shell,
             "arguments": json.dumps({"cmd": "printf probe-tool-output-2"})},
        ]
    return [{"id": "msg_after", "type": "message", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": "Body after tools.", "annotations": []}]}]


def run() -> None:
    executable, sandbox = shutil.which("codex"), shutil.which("sandbox-exec")
    if not executable or not sandbox:
        raise RuntimeError("Installed Codex CLI and sandbox-exec are required")
    ROOT.mkdir(parents=True, exist_ok=True)
    home, codex_home, cwd = (ROOT / name for name in ("home", "codex-home", "cwd"))
    private_tmp, xdg_config, xdg_data, xdg_cache = (ROOT / name for name in
                                                    ("tmp", "xdg-config", "xdg-data", "xdg-cache"))
    for path in (home, codex_home, cwd, private_tmp, xdg_config, xdg_data, xdg_cache):
        path.mkdir(mode=0o700, exist_ok=True)
    environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE")
                   if key in os.environ}
    environment.update(HOME=str(home), CODEX_HOME=str(codex_home), USERPROFILE=str(home),
                       TMPDIR=str(private_tmp), TMP=str(private_tmp), TEMP=str(private_tmp),
                       XDG_CONFIG_HOME=str(xdg_config), XDG_DATA_HOME=str(xdg_data),
                       XDG_CACHE_HOME=str(xdg_cache), CODEX_PROBE_KEY="local-non-secret", NO_COLOR="1")
    # Even the version invocation uses the private directories and the same
    # network and file boundaries as the App Server run.
    real_harness_dirs = (".codex", ".agents", ".claude", ".zcode")
    real_paths = " ".join(f'(subpath {json.dumps(str(Path.home() / name))})'
                          for name in real_harness_dirs)
    profile = ('(version 1) (allow default) (deny network-outbound (remote ip "*:*")) '
               '(allow network-outbound (remote ip "localhost:*")) '
               f'(deny file-read* {real_paths}) (deny file-write*) '
               f'(allow file-write* (subpath {json.dumps(str(ROOT))}) '
               '(literal "/dev/null") (literal "/dev/tty"))')
    version = subprocess.check_output([sandbox, "-p", profile, executable, "--version"],
                                      cwd=cwd, env=environment, text=True).strip()
    (ROOT / "version.txt").write_text(version + "\n", encoding="utf-8")
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, "body": request})
            items = model_items(request, len(requests))
            response = {"id": f"resp_probe_{len(requests)}", "object": "response", "created_at": 1,
                        "model": request.get("model", "probe-model"), "status": "completed", "output": items,
                        "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20,
                                  "input_tokens_details": {"cached_tokens": 0},
                                  "output_tokens_details": {"reasoning_tokens": 0}}}
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            sequence = 0

            def event(kind: str, **fields):
                nonlocal sequence
                sequence += 1
                value = {"type": kind, "sequence_number": sequence, **fields}
                self.wfile.write(("event: " + kind + "\ndata: " + json.dumps(value) + "\n\n").encode())
                self.wfile.flush()

            event("response.created", response={**response, "status": "in_progress", "output": []})
            for index, item in enumerate(items):
                event("response.output_item.added", output_index=index, item={**item, "status": "in_progress"})
                if item["type"] == "message":
                    event("response.content_part.added", item_id=item["id"], output_index=index,
                          content_index=0, part={"type": "output_text", "text": "", "annotations": []})
                    event("response.output_text.delta", item_id=item["id"], output_index=index,
                          content_index=0, delta=item["content"][0]["text"])
                    event("response.output_text.done", item_id=item["id"], output_index=index,
                          content_index=0, text=item["content"][0]["text"])
                    event("response.content_part.done", item_id=item["id"], output_index=index,
                          content_index=0, part=item["content"][0])
                elif item["type"] == "reasoning":
                    event("response.reasoning_summary_part.added", item_id=item["id"], output_index=index,
                          summary_index=0, part={"type": "summary_text", "text": ""})
                    event("response.reasoning_summary_text.delta", item_id=item["id"], output_index=index,
                          summary_index=0, delta=item["summary"][0]["text"])
                    event("response.reasoning_summary_text.done", item_id=item["id"], output_index=index,
                          summary_index=0, text=item["summary"][0]["text"])
                    event("response.reasoning_summary_part.done", item_id=item["id"], output_index=index,
                          summary_index=0, part=item["summary"][0])
                event("response.output_item.done", output_index=index, item=item)
            event("response.completed", response=response)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = codex_home / "config.toml"
    config.write_text(
        'model_provider = "probe"\nmodel = "probe-model"\n'
        '[model_providers.probe]\nname = "Local probe"\n'
        f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
        'env_key = "CODEX_PROBE_KEY"\nwire_api = "responses"\n'
        'requires_openai_auth = false\nsupports_websockets = false\n', encoding="utf-8")
    command = [sandbox, "-p", profile, executable, "app-server", "--listen", "stdio://"]
    events: list[dict] = []
    report: dict = {"version": version, "command": ["sandbox-exec", "codex app-server"],
                    "provider": "probe", "status": "started"}
    stderr = (ROOT / "native.stderr.log").open("wb")
    process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=stderr, start_new_session=True)
    try:
        connection = Connection(process, time.monotonic() + 60, threading.Event())
        connection.on_notification = events.append
        def reject_interaction(message: dict) -> None:
            events.append(message)
            raise RuntimeError(f"Codex requested interactive input: {message.get('method')}")
        connection.on_request = reject_interaction
        report["initialize"] = connection.call("initialize", {"clientInfo": {
            "name": "session_coverage_probe", "title": "Session Coverage Probe", "version": "1"},
            "capabilities": {"experimentalApi": True}})
        connection.send({"method": "initialized", "params": {}})
        started = connection.call("thread/start", {"cwd": str(cwd), "model": "probe-model",
            "modelProvider": "probe", "approvalPolicy": "never", "sandbox": "danger-full-access",
            "experimentalRawEvents": True})
        report["thread"] = started
        thread_id = started["thread"]["id"]
        report["turn"] = connection.call("turn/start", {"threadId": thread_id, "cwd": str(cwd),
            "model": "probe-model", "effort": "low", "approvalPolicy": "never",
            "sandboxPolicy": {"type": "dangerFullAccess"},
            "input": [{"type": "text", "text": "Call update_plan once if available, run two local print commands, and finish."}]})
        while not any(event.get("method") == "turn/completed" for event in events):
            connection.pump()
        report["completion"] = next(event for event in reversed(events)
                                    if event.get("method") == "turn/completed")
        if report["completion"]["params"]["turn"]["status"] != "completed":
            raise RuntimeError("The native turn did not complete")
        if len(requests) != 3 or any(request["path"] != "/v1/responses" for request in requests):
            raise RuntimeError("The loopback model did not receive three Responses requests")
        raw_items = [event["params"]["item"] for event in events
                     if event.get("method") == "rawResponseItem/completed"]
        if [item["call_id"] for item in raw_items if item.get("type") == "function_call"] != [
                "call_probe_1", "call_probe_2"]:
            raise RuntimeError("Two native tool calls were not observed")
        if [item["call_id"] for item in raw_items if item.get("type") == "function_call_output"] != [
                "call_probe_1", "call_probe_2"]:
            raise RuntimeError("Two native tool results were not observed")
        texts = [event["params"]["item"].get("text") for event in events
                 if event.get("method") == "item/completed"
                 and event.get("params", {}).get("item", {}).get("type") == "agentMessage"]
        if texts != ["Body before tools.", "Body between tools.", "Body after tools."]:
            raise RuntimeError("The native turn did not expose all three body segments")
        if not any(event.get("method") == "item/reasoning/summaryTextDelta"
                   and event.get("params", {}).get("delta") == "Probe reasoning summary."
                   for event in events):
            raise RuntimeError("The native turn did not expose its reasoning summary")
        report["status"] = "completed"
    except CodexProtocolError as error:
        report.update(status="failed", error={"code": error.code, "message": str(error)})
        raise
    finally:
        save(ROOT / "native-events.json", events)
        save(ROOT / "model-requests.json", requests)
        save(ROOT / "report.json", report)
        process.stdin.close()
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        stderr.close()
        server.shutdown()
        server.server_close()
    sessions = [str(path.relative_to(codex_home)) for path in codex_home.rglob("*.jsonl")]
    save(ROOT / "native-sessions.json", sessions)
    if report["status"] != "completed":
        raise RuntimeError(report)
    print(json.dumps({"version": version, "status": report["status"], "runRoot": str(ROOT),
                      "modelRequests": len(requests), "nativeSessions": sessions}, ensure_ascii=False))


if __name__ == "__main__":
    run()
