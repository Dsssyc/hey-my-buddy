#!/usr/bin/env python3
"""Installed Claude Code CLI, product arguments, loopback Messages fixture.

Run explicitly from this checkout: uv run --frozen python tests/probes/claude_session_content.py
All homes, settings, native logs and captured events stay in
tmp/session-coverage-c2/claude. No real provider or user configuration is used.
The literal API key is not a secret. sandbox-exec permits only loopback network.
Claude's product invocation includes --no-session-persistence; its stream-json
frames are therefore the harness's own record of this turn.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from hey_my_buddy.buddy.harnesses.claude.config import execution_args, permission_sandbox_settings
from hey_my_buddy.buddy.harnesses.claude.session_content import ClaudeSessionProjector
from hey_my_buddy.buddy.harnesses.session_record import SessionRecord


def main() -> None:
    root = REPOSITORY / "tmp/session-coverage-c2/claude" / str(uuid4())
    for name in ("home", "config", "cwd", "temp"):
        (root / name).mkdir(parents=True)
    (root / "cwd/probe.txt").write_text("loopback fixture tool output\n")
    executable = shutil.which("claude")
    assert executable and shutil.which("sandbox-exec"), "installed Claude and sandbox-exec are required"
    shared = " ".join(f"(subpath {json.dumps(str(Path.home() / name))})"
                      for name in (".claude", ".claude.json", ".agents", ".codex", ".dsh", ".zcode"))
    profile = ('(version 1) (allow default) (deny network-outbound (remote ip "*:*")) '
               '(allow network-outbound (remote ip "localhost:*")) '
               f'(deny file-read* {shared}) '
               '(deny file-write*) '
               f'(allow file-write* (subpath {json.dumps(str(root))}) '
               '(literal "/dev/null") (literal "/dev/tty"))')
    command = ["sandbox-exec", "-p", profile, executable]
    environment = {
        "PATH": os.environ["PATH"], "HOME": str(root / "home"),
        "CLAUDE_CONFIG_DIR": str(root / "config"), "TMPDIR": str(root / "temp"),
        "XDG_CONFIG_HOME": str(root / "home/.config"), "XDG_DATA_HOME": str(root / "home/.local/share"),
        "XDG_CACHE_HOME": str(root / "home/.cache"), "LANG": "en_US.UTF-8",
        "ANTHROPIC_API_KEY": "local-fixture-not-a-secret",
    }
    version = subprocess.run([*command, "--version"], env=environment, cwd=root / "cwd",
                             capture_output=True, text=True, timeout=20, check=True).stdout.strip()
    (root / "version.txt").write_text(version + "\n")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, "body": data})
            (root / "requests.json").write_text(json.dumps(requests, indent=2))
            if not self.path.startswith("/v1/messages"):
                self.send_error(404)
                return
            tools = {tool["name"]: tool for tool in data.get("tools", [])}
            results = [block for message in data.get("messages", []) if isinstance(message.get("content"), list)
                       for block in message["content"] if block.get("type") == "tool_result"]
            if len(results) == 0:
                blocks = [{"type": "thinking", "thinking": "probe reasoning before tools", "signature": "fixture-signature-not-secret"},
                          {"type": "text", "text": "probe body before tools"},
                          {"type": "tool_use", "id": "toolu_probe_read", "name": "Read",
                           "input": {"file_path": str(root / "cwd/probe.txt")}}]
            elif len(results) == 1:
                plan = "TodoWrite" in tools
                blocks = [{"type": "text", "text": "probe body between tools"},
                          {"type": "tool_use", "id": "toolu_probe_second", "name": "TodoWrite" if plan else "Read",
                           "input": {"todos": [{"content": "Check loopback evidence", "activeForm": "Checking loopback evidence", "status": "completed"}]} if plan
                           else {"file_path": str(root / "cwd/probe.txt")}}]
            elif len(results) == 2 and "StructuredOutput" in tools:
                blocks = [{"type": "text", "text": "probe body after tools"},
                          {"type": "tool_use", "id": "toolu_probe_result", "name": "StructuredOutput",
                           "input": {"summary": "loopback fixture completed"}}]
            else:
                assert len(results) <= 4, "unexpected repeated model requests"
                blocks = [{"type": "text", "text": "probe final body"}]
            message_id = f"msg_probe_{len(requests)}"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            def emit(kind, **fields):
                self.wfile.write(("event: " + kind + "\ndata: " + json.dumps({"type": kind, **fields}) + "\n\n").encode())
                self.wfile.flush()

            emit("message_start", message={"id": message_id, "type": "message", "role": "assistant", "content": [],
                 "model": data["model"], "stop_reason": None, "stop_sequence": None,
                 "usage": {"input_tokens": 10, "output_tokens": 0}})
            for index, block in enumerate(blocks):
                if block["type"] == "text":
                    emit("content_block_start", index=index, content_block={"type": "text", "text": ""})
                    emit("content_block_delta", index=index, delta={"type": "text_delta", "text": block["text"]})
                elif block["type"] == "thinking":
                    emit("content_block_start", index=index, content_block={"type": "thinking", "thinking": "", "signature": ""})
                    emit("content_block_delta", index=index, delta={"type": "thinking_delta", "thinking": block["thinking"]})
                    emit("content_block_delta", index=index, delta={"type": "signature_delta", "signature": block["signature"]})
                else:
                    emit("content_block_start", index=index, content_block={**block, "input": {}})
                    emit("content_block_delta", index=index, delta={"type": "input_json_delta", "partial_json": json.dumps(block["input"])})
                emit("content_block_stop", index=index)
            emit("message_delta", delta={"stop_reason": "tool_use" if blocks[-1]["type"] == "tool_use" else "end_turn", "stop_sequence": None},
                 usage={"output_tokens": 20})
            emit("message_stop")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"
    settings = root / "settings.json"
    settings.write_text(json.dumps(permission_sandbox_settings("full")))
    session_id = str(uuid4())
    prompt = "Read probe.txt, use a plan tool if offered, and return the fixture result."
    args = execution_args(session_id=session_id, model="claude-sonnet-4-6", effort="high",
                          settings_path=str(settings), read_only=False, full_access=True,
                          output_schema={"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"], "additionalProperties": False})
    (root / "invocation.json").write_text(json.dumps({"version": version, "arguments": args}, indent=2))
    frame = {"type": "user", "session_id": session_id,
             "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}}
    try:
        result = subprocess.run([*command, *args], input=json.dumps(frame) + "\n", env=environment,
                                cwd=root / "cwd", capture_output=True, text=True, timeout=90)
    finally:
        server.shutdown()
        server.server_close()
    (root / "events.jsonl").write_text(result.stdout)
    (root / "stderr.txt").write_text(result.stderr)
    print(json.dumps({"root": str(root.relative_to(REPOSITORY)), "version": version,
                      "exitCode": result.returncode, "requests": len(requests)}))
    assert result.returncode == 0, result.stderr + result.stdout
    frames = [json.loads(line) for line in result.stdout.splitlines()]
    assert any(frame.get("type") == "result" and not frame.get("is_error") for frame in frames), result.stdout
    record = SessionRecord(root / "projected.jsonl", "probe-attempt")
    projector = ClaudeSessionProjector(record)
    projector.start_prompt(session_id, prompt)
    for frame in frames:
        projector.observe_frame(frame)
    projector.close()
    assert record.failure is None, record.failure
    projected = (root / "projected.jsonl").read_text()
    segments = {}
    for line in projected.splitlines():
        update = json.loads(line).get("update", {})
        if update.get("sessionUpdate") in ("agent_message_chunk", "agent_thought_chunk"):
            meta = update["_meta"]
            key = (meta["nativeItemId"], meta["contentSegment"])
            segments[key] = ("" if meta.get("replace") else segments.get(key, "")) + update["content"]["text"]
    for text in ("probe reasoning before tools", "probe body before tools", "probe body between tools",
                 "loopback fixture tool output"):
        assert text in projected, f"native content lost: {text}"
    for text in ("probe reasoning before tools", "probe body before tools", "probe body between tools"):
        assert text in segments.values(), f"native content overwritten: {text}"
    print("Native thinking, body and tool input/output retained; plan only when natively offered.")


if __name__ == "__main__":
    main()
