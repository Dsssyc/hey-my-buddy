#!/usr/bin/env python3
"""Explicit DSH CLI ACP session-content probe against a loopback model.

Run with ``uv run --frozen python tests/probes/dsh_session_content.py``. Every
run creates a new directory below ``tmp/session-coverage-c2/dsh``. HOME,
DSH_HOME, settings, session storage, working directory and logs are private.
The sandbox permits only loopback network. No real account is used.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import zstandard

CHECKOUT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CHECKOUT / "src"))

from hey_my_buddy.buddy.harnesses.dsh.acp import AcpClient  # noqa: E402

BASE = CHECKOUT / "tmp/session-coverage-c2/dsh"
DSH = Path(shutil.which("dsh") or "")
NODE = Path(shutil.which("node") or "")
SANDBOX = Path("/usr/bin/sandbox-exec")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record_generation(name: str) -> int | None:
    """The generation a record file's name carries, or None for any other name.

    Generation 0 is ``session``, later ones ``session.vN`` without leading
    zeros, each optionally zstandard-compressed; a migration's temporary file
    carries no generation.
    """
    for suffix in (".jsonl.zstd", ".jsonl"):
        if not name.endswith(suffix):
            continue
        stem = name[: -len(suffix)]
        if stem == "session":
            return 0
        digits = stem[len("session.v"):] if stem.startswith("session.v") else None
        if digits and digits.isascii() and digits.isdigit() and not digits.startswith("0"):
            return int(digits)
        return None
    return None


def latest_session_record(sessions: Path) -> Path | None:
    """The current-generation record under the sessions root: the largest
    generation DSH left on disk, mirroring what the native process itself uses."""
    best: tuple[int, Path] | None = None
    for path in sessions.rglob("session*"):
        if not path.is_file():
            continue
        generation = record_generation(path.name)
        if generation is not None and (best is None or generation > best[0]):
            best = (generation, path)
    return best[1] if best is not None else None


def main() -> int:
    if not DSH.is_file() or not NODE.is_file() or not SANDBOX.is_file():
        raise RuntimeError("installed DSH CLI, Node and macOS sandbox-exec are required")
    run = BASE / datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for part in ("home", "dsh-home", "cwd", "tmp", "agent/logs", "sessions", "storages",
                 "xdg-config", "xdg-data", "xdg-state", "xdg-cache"):
        (run / part).mkdir(parents=True)
    (run / "cwd/probe.txt").write_text("DSH session coverage fixture\n", encoding="utf-8")
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(data)
            write_json(run / "model-requests.json", requests)
            tool_messages = [m for m in data.get("messages", []) if m.get("role") == "tool"]
            stage = min(len(tool_messages), 3)
            tools = [t["function"]["name"] for t in data.get("tools", [])]
            read = next((name for name in tools if name.lower() in {"read", "read_file"}), None)
            glob = next((name for name in tools if name.lower() == "glob"), None)
            plan = next((name for name in tools if name == "todo_write"), None)
            name = (read, glob, plan or read)[stage] if stage < 3 else None
            arguments = ({"todos": [{"content": "Inspect loopback evidence", "status": "completed"}]}
                         if stage == 2 and plan else {"file_path": "probe.txt"} if stage in (0, 2)
                         else {"pattern": "probe.txt"})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            def chunk(delta: dict, finish=None):
                message = {"id": f"chatcmpl-probe-{stage}", "object": "chat.completion.chunk",
                           "created": 1, "model": data["model"],
                           "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(("data: " + json.dumps(message) + "\n\n").encode())
                self.wfile.flush()

            chunk({"role": "assistant", "reasoning_content": f"Reasoning stage {stage}. "})
            chunk({"content": ("Before first tool. ", "Between first and second tools. ",
                               "Between second and third tools. ", "After third tool.")[stage]})
            if name:
                chunk({"tool_calls": [{"index": 0, "id": f"call_probe_{stage}", "type": "function",
                                       "function": {"name": name, "arguments": json.dumps(arguments)}}]})
                chunk({}, "tool_calls")
            else:
                chunk({}, "stop")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    patch = [
        {"id": "llm-pi-ai", "config": {"providers": {"local-probe": {
            "apiKeyEnv": "DSH_PROBE_KEY", "api": "openai-completions",
            "baseURL": f"http://127.0.0.1:{server.server_port}/v1",
            "compat": {"thinkingFormat": "deepseek"},
            "models": [{"id": "probe-model", "name": "Probe model", "contextWindow": 262144}]}}}},
        {"id": "agent-default-model", "config": {"provider": "local-probe", "model": "probe-model"}},
        {"id": "acp", "config": {"provider": "local-probe", "model": "probe-model"}},
        {"id": "session-persistence-jsonl", "config": {"root": str(run / "sessions")}},
        {"id": "storage-json", "config": {"root": str(run / "storages")}},
        {"id": "session-title-llm", "disabled": True},
        {"id": "agent-instructions", "disabled": True},
    ]
    write_json(run / "patch.json", patch)
    private_home = str(run / "home")
    private_dsh = str(run / "dsh-home")
    environment = {"HOME": private_home, "DSH_HOME": private_dsh,
                   "TMPDIR": str(run / "tmp"), "PATH": os.environ["PATH"],
                   "XDG_CONFIG_HOME": str(run / "xdg-config"), "XDG_DATA_HOME": str(run / "xdg-data"),
                   "XDG_STATE_HOME": str(run / "xdg-state"), "XDG_CACHE_HOME": str(run / "xdg-cache"),
                   "LANG": "en_US.UTF-8", "DSH_PROBE_KEY": "local-non-secret"}
    # The child reads only its private run and the installed DSH package under
    # the user's home. Writes are limited to the private run. HTTP is loopback.
    traversed = set(DSH.resolve().parents) | set(run.parents)
    traversed = sorted((path for path in traversed if path.is_relative_to(Path.home())),
                       key=lambda path: len(path.parts))
    profile = ('(version 1) (allow default) '
               '(deny network-outbound (remote ip "*:*")) '
               '(allow network-outbound (remote ip "localhost:*")) '
               f'(deny file-read* (subpath {json.dumps(str(Path.home()))})) '
               + ' '.join(f'(allow file-read-metadata (literal {json.dumps(str(path))}))'
                          for path in traversed) + ' '
               f'(allow file-read-metadata (literal {json.dumps(str(CHECKOUT / ".git"))})) '
               f'(allow file-read* (literal {json.dumps(str(DSH))})) '
               f'(allow file-read* (subpath {json.dumps(str(DSH.resolve().parents[1]))})) '
               f'(allow file-read* (subpath {json.dumps(str(NODE.resolve().parent.parent))})) '
               f'(allow file-read* (subpath {json.dumps(str(run))})) '
               '(deny file-write*) '
               f'(allow file-write* (subpath {json.dumps(str(run))})) '
               '(allow file-write* (subpath "/dev"))')
    argv = [str(SANDBOX), "-p", profile, str(DSH.resolve()), "--profile", "acp", "--patch", str(run / "patch.json")]
    version_argv = [str(SANDBOX), "-p", profile, str(DSH.resolve()), "--version"]
    write_json(run / "launch.json", {"version": subprocess.check_output(
        version_argv, env=environment, cwd=run / "cwd", text=True, stderr=subprocess.PIPE).strip(),
        "argv": argv, "environment": {k: ("<local non-secret>" if k == "DSH_PROBE_KEY" else v)
                                for k, v in environment.items()}})
    raw: list[dict] = []
    result: dict = {}
    client = None
    try:
        # The product launcher inherits native settings. This explicit probe
        # supplies an entirely private parent as well as its launch overrides.
        with mock.patch.dict(os.environ, environment, clear=True):
            client = AcpClient.start(argv, private_root=run / "agent", cwd=run / "cwd",
                                     launch_environment=environment)
        client.connection.observe(raw.append)
        result["initialize"] = client.initialize(timeout=30)
        created = client.new_session(str(run / "cwd"), timeout=45)
        result["newSession"] = created
        session_id = created["sessionId"]
        result["prompt"] = client.prompt(session_id,
            "Read probe.txt, find probe.txt, record the plan with todo_write if offered, then answer. Show steps between tools.", timeout=90)
        result["close"] = client.close_session(session_id, timeout=15)
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        if hasattr(error, "error"):
            result["nativeError"] = error.error
    finally:
        if client is not None:
            result["stderr"] = client.stderr_tail()
            result["connection"] = client.facts()
            result["stop"] = client.shutdown(drain_seconds=5, settle_seconds=5)
        server.shutdown()
        server.server_close()
        write_json(run / "acp-notifications.json", raw)
        write_json(run / "result.json", result)
    updates = [item["params"]["update"] for item in raw
               if item.get("method") == "session/update"]
    bodies = [item["content"]["text"] for item in updates
              if item["sessionUpdate"] == "agent_message_chunk"]
    thoughts = [item["content"]["text"] for item in updates
                if item["sessionUpdate"] == "agent_thought_chunk"]
    calls = [item for item in updates if item["sessionUpdate"] == "tool_call"]
    completed = [item for item in updates if item["sessionUpdate"] == "tool_call_update"
                 and item.get("status") == "completed" and item.get("content")]
    latest = latest_session_record(run / "sessions")
    native_paths = [latest] if latest is not None else []
    native_events = []
    native_messages = []
    if len(native_paths) == 1:
        with native_paths[0].open("rb") as source:
            with zstandard.ZstdDecompressor().stream_reader(source) as decoded:
                native_events = [json.loads(line) for line in decoded.read().decode().splitlines()]
        native_messages = [event for event in native_events if event["type"] == "assistant/message"]
    native_plans = [event["data"]["todos"] for event in native_events if event["type"] == "todo/write"]
    acp_plans = [item["rawInput"]["todos"] for item in calls if item["title"] == "todo_write"]
    evidence = {"acpBodies": bodies, "acpReasoning": thoughts,
                "acpToolCalls": len(calls), "acpCompletedToolOutputs": len(completed),
                "acpPlanUpdates": sum(item["sessionUpdate"] == "plan" for item in updates),
                "nativeTodoWrites": native_plans, "acpTodoWrites": acp_plans,
                "nativeAssistantMessages": len(native_messages),
                "nativeSessionPath": str(native_paths[0]) if len(native_paths) == 1 else None}
    write_json(run / "evidence-summary.json", evidence)
    print(json.dumps({"run": str(run), "result": result, "evidence": evidence,
                      "modelRequests": len(requests), "notifications": len(raw)}, ensure_ascii=False))
    expected_bodies = ["Before first tool. ", "Between first and second tools. ",
                       "Between second and third tools. ", "After third tool."]
    return 0 if ("prompt" in result and len(requests) == 4 and
                 result["stop"]["shutdownConfirmed"] and bodies == expected_bodies and
                 len(thoughts) == 4 and len(calls) == len(completed) == 3 and
                 len(native_messages) == 4 and native_plans == acp_plans) else 1


if __name__ == "__main__":
    raise SystemExit(main())
